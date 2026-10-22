# -*- coding: utf-8 -*-
"""Mensajes directos y GRUPOS de chat de W Social (chat.bsky.convo y chat.bsky.group).

Mixin de WSocialService.

El chat de AT Protocol no vive en el repositorio público: va por un servicio aparte al que se
habla con un cliente "con proxy de chat". Por eso la contraseña de aplicación tiene que estar
habilitada para mensajes directos; si no lo está, el servidor responde que no hay permiso.

Además de las conversaciones de dos, W soporta GRUPOS con administración propia: crear, editar,
invitar por enlace, aprobar solicitudes, expulsar y cerrar el grupo.
"""
from app.services.wsocial.wsocial_common import (
    Any, Callable, Dict, List, Optional, db, json, log, models, WSocialServiceError,
    _g, _ahora_iso)


class _ChatMixin:
    # ------------------------------------------------------------------
    # CONVERSACIONES
    # ------------------------------------------------------------------
    def _miembros_convo(self, convo, mi_did: str):
        """Devuelve (miembros, título, es_grupo, mapa_did_nombre) de una conversación.

        El título de un chat de dos es la otra persona; el de un grupo, su nombre (y si no tiene,
        la lista de participantes)."""
        miembros, mapa = [], {}
        for m in (_g(convo, "members", default=[]) or []):
            did = _g(m, "did", default="")
            nombre = _g(m, "display_name", "displayName", default="") or _g(m, "handle", default="")
            mapa[did] = nombre
            if did != mi_did:
                miembros.append({"did": did, "handle": _g(m, "handle", default=""), "nombre": nombre})
        nombre_grupo = _g(convo, "name", default="") or ""
        es_grupo = bool(nombre_grupo) or len(miembros) > 1
        if nombre_grupo:
            titulo = nombre_grupo
        elif miembros:
            titulo = ", ".join(m["nombre"] for m in miembros[:4]) + ("…" if len(miembros) > 4 else "")
        else:
            titulo = "Conversación"
        return miembros, titulo, es_grupo, mapa

    def cargar_convos_async(self, account_id: int, cargar_mas: bool = False,
                            solicitudes: bool = False, callback: Optional[Callable] = None):
        """Lista de conversaciones (o de solicitudes de mensaje sin aceptar) y las cachea."""
        clave = f"{account_id}:convos:{'req' if solicitudes else 'all'}"

        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            cursor = self._cursors.get(clave) if cargar_mas else None
            if solicitudes:
                resp = chat.chat.bsky.convo.list_convo_requests({"limit": 50, "cursor": cursor})
            else:
                resp = chat.chat.bsky.convo.list_convos({"limit": 50, "cursor": cursor})
            self._cursors[clave] = _g(resp, "cursor", default=None)
            salida = []
            with db.transaccion() as conn:
                for convo in (_g(resp, "convos", default=[]) or []):
                    convo_id = _g(convo, "id", default="")
                    if not convo_id:
                        continue
                    miembros, titulo, es_grupo, _mapa = self._miembros_convo(convo, mi_did)
                    ultimo = _g(convo, "last_message", "lastMessage", default=None)
                    datos = {
                        "convo_id": convo_id,
                        "title": titulo,
                        "miembros": miembros,
                        "unread": _g(convo, "unread_count", "unreadCount", default=0) or 0,
                        "muted": 1 if _g(convo, "muted", default=False) else 0,
                        "es_grupo": 1 if es_grupo else 0,
                        "papel": str(_g(convo, "role", default="") or ""),
                        "bloqueado": 1 if _g(convo, "locked", default=False) else 0,
                        "ultimo": (_g(ultimo, "text", default="") or "").replace("\n", " "),
                        "fecha": _g(ultimo, "sent_at", "sentAt", default=""),
                    }
                    conn.execute(
                        "INSERT INTO wsocial_convos (id, account_id, convo_id, members_json, title, "
                        "unread_count, muted, last_message, last_message_date, is_group, group_role, "
                        "is_request, locked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET members_json=excluded.members_json, "
                        "title=excluded.title, unread_count=excluded.unread_count, "
                        "muted=excluded.muted, last_message=excluded.last_message, "
                        "last_message_date=excluded.last_message_date, is_group=excluded.is_group, "
                        "group_role=excluded.group_role, is_request=excluded.is_request, "
                        "locked=excluded.locked",
                        (f"{account_id}:{convo_id}", account_id, convo_id,
                         json.dumps(miembros, ensure_ascii=False), titulo, datos["unread"],
                         datos["muted"], datos["ultimo"], datos["fecha"], datos["es_grupo"],
                         datos["papel"], 1 if solicitudes else 0, datos["bloqueado"]))
                    salida.append(datos)
            return salida
        self._tarea(_bg, callback)

    def cargar_mensajes_async(self, account_id: int, convo_id: str, cargar_mas: bool = False,
                              callback: Optional[Callable] = None):
        """Mensajes de una conversación (los más recientes primero en el servidor; aquí se
        guardan y se devuelven en orden cronológico, que es como se leen)."""
        clave = f"{account_id}:msgs:{convo_id}"

        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            cursor = self._cursors.get(clave) if cargar_mas else None
            resp = chat.chat.bsky.convo.get_messages(
                {"convo_id": convo_id, "limit": 100, "cursor": cursor})
            self._cursors[clave] = _g(resp, "cursor", default=None)
            mensajes = []
            with db.transaccion() as conn:
                for m in (_g(resp, "messages", default=[]) or []):
                    mid = _g(m, "id", default="")
                    if not mid:
                        continue
                    remitente = _g(_g(m, "sender"), "did", default="")
                    saliente = 1 if remitente == mi_did else 0
                    texto = _g(m, "text", default="") or ""
                    # Un mensaje borrado llega sin texto: se marca para que se entienda.
                    if not texto and _g(m, "py_type", "$type", default="").endswith("deletedMessageView"):
                        texto = "(mensaje eliminado)"
                    reacciones = []
                    for r in (_g(m, "reactions", default=[]) or []):
                        reacciones.append({"emoji": _g(r, "value", default=""),
                                           "did": _g(_g(r, "sender"), "did", default="")})
                    conn.execute(
                        "INSERT OR REPLACE INTO wsocial_messages (id, account_id, convo_id, "
                        "message_id, sender_did, sender_name, text, embed_json, reactions_json, "
                        "sent_at, outgoing) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (f"{account_id}:{convo_id}:{mid}", account_id, convo_id, mid, remitente,
                         self._nombre_de_did(account_id, convo_id, remitente), texto, "",
                         json.dumps(reacciones, ensure_ascii=False),
                         _g(m, "sent_at", "sentAt", default=""), saliente))
                    mensajes.append({"id": mid, "text": texto, "outgoing": bool(saliente),
                                     "sent_at": _g(m, "sent_at", "sentAt", default=""),
                                     "reacciones": reacciones})
            mensajes.reverse()
            return mensajes
        self._tarea(_bg, callback)

    def _nombre_de_did(self, account_id: int, convo_id: str, did: str) -> str:
        """Nombre legible de un participante, sacado de los miembros cacheados de la conversación."""
        if not did:
            return ""
        fila = db.query_one("SELECT members_json FROM wsocial_convos WHERE id = ?",
                            (f"{account_id}:{convo_id}",))
        if fila and fila[0]:
            try:
                for m in json.loads(fila[0]):
                    if m.get("did") == did:
                        return m.get("nombre") or m.get("handle") or did
            except Exception:
                pass
        yo = self._me.get(account_id) or {}
        return "Tú" if did == yo.get("did") else did

    def enviar_mensaje_async(self, account_id: int, convo_id: str, texto: str,
                             callback: Optional[Callable] = None):
        """Envía un mensaje de texto (con sus enlaces y menciones marcados)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            facets = self._construir_facets(chat, texto)
            msg = models.ChatBskyConvoDefs.MessageInput(text=texto, facets=facets)
            resp = chat.chat.bsky.convo.send_message(
                models.ChatBskyConvoSendMessage.Data(convo_id=convo_id, message=msg))
            return {"id": _g(resp, "id", default=""), "text": texto}
        self._tarea(_bg, callback)

    def enviar_mensajes_lote_async(self, account_id: int, envios: List[Dict[str, str]],
                                   callback: Optional[Callable] = None):
        """Envía varios mensajes de una vez (`envios` = [{convo_id, texto}, ...]). Es lo que
        permite mandar un hilo entero por privado sin una petición por mensaje."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            items = []
            for e in envios:
                items.append(models.ChatBskyConvoSendMessageBatch.BatchItem(
                    convo_id=e["convo_id"],
                    message=models.ChatBskyConvoDefs.MessageInput(
                        text=e["texto"], facets=self._construir_facets(chat, e["texto"]))))
            resp = chat.chat.bsky.convo.send_message_batch({"items": items})
            return len(_g(resp, "items", default=[]) or [])
        self._tarea(_bg, callback)

    def enviar_hilo_dm_async(self, account_id: int, actor: str, textos: List[str],
                             callback: Optional[Callable] = None):
        """Envía los trozos de un texto largo como mensajes seguidos de una conversación."""
        textos = [t.strip() for t in textos if t and t.strip()]

        def _bg(ctx):
            if not textos:
                raise WSocialServiceError("No hay nada que enviar.")
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            if not did:
                raise WSocialServiceError(f"No se ha encontrado la cuenta {actor}.")
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            resp = chat.chat.bsky.convo.get_convo_for_members({"members": [did]})
            convo = _g(resp, "convo")
            convo_id = _g(convo, "id")
            _m, titulo, _e, _mapa = self._miembros_convo(convo, mi_did)
            for i, texto in enumerate(textos):
                if ctx.is_cancelled():
                    raise WSocialServiceError("Envío cancelado.")
                ctx.report_progress(int((i / len(textos)) * 100),
                                    f"Enviando {i + 1} de {len(textos)}...")
                chat.chat.bsky.convo.send_message(
                    models.ChatBskyConvoSendMessage.Data(
                        convo_id=convo_id,
                        message=models.ChatBskyConvoDefs.MessageInput(
                            text=texto, facets=self._construir_facets(chat, texto))))
            return {"convo_id": convo_id, "title": titulo, "enviados": len(textos)}
        self._tarea(_bg, callback, sonidos=True)

    def abrir_convo_con_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Obtiene (o crea) la conversación con una cuenta y devuelve su identificador y título."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            if not did:
                raise WSocialServiceError(f"No se ha encontrado la cuenta {actor}.")
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            resp = chat.chat.bsky.convo.get_convo_for_members({"members": [did]})
            convo = _g(resp, "convo")
            _m, titulo, _e, _mapa = self._miembros_convo(convo, mi_did)
            return {"convo_id": _g(convo, "id"), "title": titulo}
        self._tarea(_bg, callback)

    def disponibilidad_convo_async(self, account_id: int, actores: List[str],
                                   callback: Optional[Callable] = None):
        """Comprueba si se puede escribir a esas cuentas (sus ajustes pueden impedirlo)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            chat = self._chat_capaz(account_id)
            dids = [d for d in (self._resolver_did(client, a) for a in actores) if d]
            resp = chat.chat.bsky.convo.get_convo_availability({"members": dids})
            return {"puede": bool(_g(resp, "can_chat", "canChat", default=False)),
                    "convo_id": _g(_g(resp, "convo", default=None), "id", default="")}
        self._tarea(_bg, callback)

    def accion_convo_async(self, account_id: int, convo_id: str, accion: str,
                           callback: Optional[Callable] = None):
        """Acciones sobre una conversación: leer, silenciar, dejar de silenciar, aceptar, salir,
        cerrar (lock) y reabrir."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            db_id = f"{account_id}:{convo_id}"
            if accion == "leer":
                chat.chat.bsky.convo.update_read({"convo_id": convo_id})
                db.execute("UPDATE wsocial_convos SET unread_count = 0 WHERE id = ?", (db_id,))
            elif accion == "silenciar":
                chat.chat.bsky.convo.mute_convo({"convo_id": convo_id})
                db.execute("UPDATE wsocial_convos SET muted = 1 WHERE id = ?", (db_id,))
            elif accion == "no_silenciar":
                chat.chat.bsky.convo.unmute_convo({"convo_id": convo_id})
                db.execute("UPDATE wsocial_convos SET muted = 0 WHERE id = ?", (db_id,))
            elif accion == "aceptar":
                chat.chat.bsky.convo.accept_convo({"convo_id": convo_id})
                db.execute("UPDATE wsocial_convos SET is_request = 0 WHERE id = ?", (db_id,))
            elif accion == "salir":
                chat.chat.bsky.convo.leave_convo({"convo_id": convo_id})
                db.execute("DELETE FROM wsocial_messages WHERE account_id = ? AND convo_id = ?",
                           (account_id, convo_id))
                db.execute("DELETE FROM wsocial_convos WHERE id = ?", (db_id,))
            elif accion == "cerrar":
                chat.chat.bsky.convo.lock_convo({"convo_id": convo_id})
                db.execute("UPDATE wsocial_convos SET locked = 1 WHERE id = ?", (db_id,))
            elif accion == "reabrir":
                chat.chat.bsky.convo.unlock_convo({"convo_id": convo_id})
                db.execute("UPDATE wsocial_convos SET locked = 0 WHERE id = ?", (db_id,))
            elif accion == "leer_todo":
                chat.chat.bsky.convo.update_all_read({})
                db.execute("UPDATE wsocial_convos SET unread_count = 0 WHERE account_id = ?",
                           (account_id,))
            else:
                raise WSocialServiceError(f"Acción de conversación desconocida: {accion}")
            return accion
        self._tarea(_bg, callback)

    def borrar_mensaje_async(self, account_id: int, convo_id: str, message_id: str,
                             callback: Optional[Callable] = None):
        """Borra un mensaje solo para ti (el otro lo sigue viendo: así funciona el protocolo)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.convo.delete_message_for_self(
                {"convo_id": convo_id, "message_id": message_id})
            db.execute("DELETE FROM wsocial_messages WHERE account_id = ? AND convo_id = ? "
                       "AND message_id = ?", (account_id, convo_id, message_id))
            return True
        self._tarea(_bg, callback)

    def reaccionar_mensaje_async(self, account_id: int, convo_id: str, message_id: str,
                                 emoji: str, quitar: bool = False,
                                 callback: Optional[Callable] = None):
        """Añade o quita una reacción a un mensaje."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            datos = {"convo_id": convo_id, "message_id": message_id, "value": emoji}
            if quitar:
                chat.chat.bsky.convo.remove_reaction(datos)
            else:
                chat.chat.bsky.convo.add_reaction(datos)
            return emoji
        self._tarea(_bg, callback)

    def no_leidos_chat_async(self, account_id: int, callback: Optional[Callable] = None):
        """Cuántos mensajes y solicitudes sin leer hay (para el contador del árbol)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.convo.get_unread_counts()
            return {"conversaciones": _g(resp, "convos", default=0) or 0,
                    "solicitudes": _g(resp, "requests", default=0) or 0}
        self._tarea(_bg, callback)

    def registro_chat_async(self, account_id: int, cursor: str = "",
                            callback: Optional[Callable] = None):
        """Novedades del chat desde el último cursor (getLog). Es lo que permite enterarse de
        mensajes nuevos sin recargar toda la lista de conversaciones."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.convo.get_log({"cursor": cursor or None})
            eventos = []
            for e in (_g(resp, "logs", default=[]) or []):
                tipo = str(_g(e, "py_type", "$type", default="")).rsplit("#", 1)[-1]
                eventos.append({
                    "tipo": tipo,
                    "convo_id": _g(e, "convo_id", "convoId", default=""),
                    "mensaje": _g(_g(e, "message", default=None), "text", default=""),
                    "remitente": _g(_g(_g(e, "message", default=None), "sender", default=None),
                                    "did", default=""),
                })
            return {"cursor": _g(resp, "cursor", default=""), "eventos": eventos}
        self._tarea(_bg, callback)

    def preferencias_chat_async(self, account_id: int, quien_puede_escribirme: str = "",
                                callback: Optional[Callable] = None):
        """Consulta o cambia quién puede escribirte por privado.

        Es un registro del propio repositorio (chat.bsky.actor.declaration, clave 'self') con
        `allow_incoming`: all (cualquiera), following (solo a quien sigues) o none (nadie).
        Sin argumento, devuelve el valor actual."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            if quien_puede_escribirme:
                if quien_puede_escribirme not in ("all", "following", "none"):
                    raise WSocialServiceError(
                        "Valor no válido: usa 'all', 'following' o 'none'.")
                client.com.atproto.repo.put_record(
                    models.ComAtprotoRepoPutRecord.Data(
                        repo=yo["did"], collection="chat.bsky.actor.declaration", rkey="self",
                        record=models.ChatBskyActorDeclaration.Record(
                            allow_incoming=quien_puede_escribirme)))
                return quien_puede_escribirme
            try:
                actual = client.com.atproto.repo.get_record(
                    {"repo": yo["did"], "collection": "chat.bsky.actor.declaration", "rkey": "self"})
                return _g(_g(actual, "value", default=None), "allow_incoming", "allowIncoming",
                          default="following")
            except Exception:
                return "following"      # el valor por defecto del servicio
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # GRUPOS
    # ------------------------------------------------------------------
    def crear_grupo_async(self, account_id: int, nombre: str, actores: List[str],
                          callback: Optional[Callable] = None):
        """Crea un grupo de chat con las cuentas indicadas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            chat = self._chat_capaz(account_id)
            dids = [d for d in (self._resolver_did(client, a) for a in actores) if d]
            if not dids:
                raise WSocialServiceError("Añade al menos una cuenta al grupo.")
            resp = chat.chat.bsky.group.create_group({"name": nombre, "members": dids})
            convo = _g(resp, "convo", default=None) or resp
            return {"convo_id": _g(convo, "id", default=""), "name": nombre}
        self._tarea(_bg, callback)

    def editar_grupo_async(self, account_id: int, convo_id: str, nombre: str,
                           callback: Optional[Callable] = None):
        """Cambia el nombre de un grupo."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.group.edit_group({"convo_id": convo_id, "name": nombre})
            db.execute("UPDATE wsocial_convos SET title = ? WHERE id = ?",
                       (nombre, f"{account_id}:{convo_id}"))
            return nombre
        self._tarea(_bg, callback)

    def miembros_grupo_async(self, account_id: int, convo_id: str,
                             callback: Optional[Callable] = None):
        """Participantes de un grupo con su papel (dueño, administrador o miembro)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.convo.get_convo_members({"convo_id": convo_id, "limit": 100})
            salida = []
            for m in (_g(resp, "members", default=[]) or []):
                salida.append({
                    "did": _g(m, "did", default=""),
                    "handle": _g(m, "handle", default=""),
                    "nombre": _g(m, "display_name", "displayName", default=""),
                    "papel": str(_g(m, "role", default="") or "member"),
                })
            return salida
        self._tarea(_bg, callback)

    def gestionar_miembros_grupo_async(self, account_id: int, convo_id: str, actores: List[str],
                                       quitar: bool = False, callback: Optional[Callable] = None):
        """Añade o expulsa cuentas de un grupo."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            chat = self._chat_capaz(account_id)
            dids = [d for d in (self._resolver_did(client, a) for a in actores) if d]
            if not dids:
                raise WSocialServiceError("No se ha reconocido ninguna de esas cuentas.")
            if quitar:
                chat.chat.bsky.group.remove_members({"convo_id": convo_id, "members": dids})
            else:
                chat.chat.bsky.group.add_members({"convo_id": convo_id, "members": dids})
            return len(dids)
        self._tarea(_bg, callback)

    def enlace_grupo_async(self, account_id: int, convo_id: str, accion: str = "crear",
                           callback: Optional[Callable] = None):
        """Enlace de invitación de un grupo: crear, activar, desactivar o consultar."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            if accion == "crear":
                resp = chat.chat.bsky.group.create_join_link({"convo_id": convo_id})
            elif accion == "activar":
                resp = chat.chat.bsky.group.enable_join_link({"convo_id": convo_id})
            elif accion == "desactivar":
                resp = chat.chat.bsky.group.disable_join_link({"convo_id": convo_id})
            elif accion == "editar":
                resp = chat.chat.bsky.group.edit_join_link({"convo_id": convo_id})
            else:
                raise WSocialServiceError(f"Acción de enlace desconocida: {accion}")
            return {"enlace": _g(resp, "link", "join_link", "joinLink", default=""),
                    "activo": bool(_g(resp, "enabled", default=True))}
        self._tarea(_bg, callback)

    def solicitudes_grupo_async(self, account_id: int, convo_id: str,
                                callback: Optional[Callable] = None):
        """Quién ha pedido entrar en el grupo."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.group.list_join_requests({"convo_id": convo_id, "limit": 100})
            salida = []
            for r in (_g(resp, "requests", default=[]) or []):
                actor = _g(r, "actor", default=None) or r
                salida.append({
                    "did": _g(actor, "did", default=""),
                    "handle": _g(actor, "handle", default=""),
                    "nombre": _g(actor, "display_name", "displayName", default=""),
                    "fecha": _g(r, "created_at", "createdAt", default=""),
                })
            return salida
        self._tarea(_bg, callback)

    def resolver_solicitud_grupo_async(self, account_id: int, convo_id: str, did: str,
                                       aceptar: bool = True, callback: Optional[Callable] = None):
        """Acepta o rechaza una solicitud de entrada al grupo."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            datos = {"convo_id": convo_id, "actor": did}
            if aceptar:
                chat.chat.bsky.group.approve_join_request(datos)
            else:
                chat.chat.bsky.group.reject_join_request(datos)
            return aceptar
        self._tarea(_bg, callback)

    def pedir_entrada_grupo_async(self, account_id: int, enlace: str,
                                  callback: Optional[Callable] = None):
        """Pide entrar en un grupo a partir de su enlace de invitación."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.group.request_join({"link": enlace})
            return {"convo_id": _g(_g(resp, "convo", default=None), "id", default=""),
                    "pendiente": bool(_g(resp, "pending", default=False))}
        self._tarea(_bg, callback)

    def previsualizar_enlace_grupo_async(self, account_id: int, enlaces: List[str],
                                         callback: Optional[Callable] = None):
        """Qué hay detrás de un enlace de grupo (nombre y participantes) antes de entrar."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.group.get_join_link_previews({"links": enlaces})
            salida = []
            for p in (_g(resp, "previews", default=[]) or []):
                salida.append({"enlace": _g(p, "link", default=""),
                               "nombre": _g(p, "name", default=""),
                               "miembros": _g(p, "member_count", "memberCount", default=0)})
            return salida
        self._tarea(_bg, callback)

    def grupos_en_comun_async(self, account_id: int, actor: str,
                              callback: Optional[Callable] = None):
        """Grupos que compartes con otra cuenta."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            chat = self._chat_capaz(account_id)
            did = self._resolver_did(client, actor)
            resp = chat.chat.bsky.group.list_mutual_groups({"actor": did, "limit": 50})
            return [{"convo_id": _g(c, "id", default=""), "nombre": _g(c, "name", default="")}
                    for c in (_g(resp, "convos", default=[]) or [])]
        self._tarea(_bg, callback)
