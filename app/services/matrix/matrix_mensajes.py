# -*- coding: utf-8 -*-
"""Mensajes y salas de Matrix: envío, crear sala/DM, unirse, typing y marcas de lectura.

Mixin de MatrixService (tanda M2). Los mensajes largos no necesitan troceo (Matrix admite
cuerpos grandes); el envío devuelve el event_id, que se guarda YA como mensaje propio (el
eco del sync no duplica gracias al UNIQUE por event_id).
"""
import json

from app.services.matrix.matrix_common import (
    Any, Dict, Optional, List, db, log, MatrixError, ahora_iso, ts_a_iso, db_id_sala)


class _MensajesMixin:
    def _ignorar_no_verificados(self, account_id: int) -> bool:
        """Política de envío ante dispositivos NO verificados en salas cifradas (MX-016).

        Por defecto True (comportamiento histórico: se comparte la sesión con todos, como hace
        Element cuando no está en modo estricto). Si se activa el ENVÍO ESTRICTO (config
        matrix.envio_estricto), devuelve False: nio se NEGARÁ a enviar mientras haya un dispositivo
        sin verificar, y la app mostrará un error claro pidiendo verificarlo o bloquearlo. Así la
        verificación de dispositivos SÍ influye en la política de envío para quien lo desee."""
        try:
            from app.core.config_manager import config
            return not bool(config.get("matrix", "envio_estricto", False))
        except Exception:
            return True

    def _cliente_conectado(self, account_id: int):
        with self._lock:
            cliente = self._clients.get(account_id)
        if cliente is None or not self.esta_conectada(account_id):
            raise MatrixError("La cuenta no está conectada. Conéctala desde el menú Matrix.")
        return cliente

    # ---------------- Envío ----------------
    @staticmethod
    def _menciones_de(texto: str) -> list:
        """user_ids completos (@usuario:servidor) citados en el texto, para las menciones
        intencionales (m.mentions) de la spec: el mencionado recibe su aviso de mención."""
        import re
        return sorted(set(re.findall(r"@[A-Za-z0-9._=\-/+]+:[A-Za-z0-9.\-]+", texto or "")))

    def enviar_mensaje(self, account_id: int, room_id: str, texto: str, emote: bool = False,
                       reply_to: str = "", editar_event: str = "", thread_root: str = ""):
        """Envía un m.text (o m.emote con /me). `reply_to` crea una respuesta enlazada
        (m.in_reply_to); `editar_event` edita un mensaje propio (m.replace); `thread_root`
        responde EN EL HILO cuya raíz es ese event_id (m.thread). Bloqueante."""
        texto = (texto or "").strip()
        if not texto:
            return
        cliente = self._cliente_conectado(account_id)
        from nio import RoomSendResponse
        contenido = {"msgtype": "m.emote" if emote else "m.text", "body": texto}
        if editar_event:
            # Edición según la spec (módulo event replacements): el body lleva el fallback "* ".
            contenido = {"msgtype": "m.text", "body": f"* {texto}",
                         "m.new_content": {"msgtype": "m.text", "body": texto},
                         "m.relates_to": {"rel_type": "m.replace", "event_id": editar_event}}
        elif thread_root:
            # Hilo (spec: threading): is_falling_back + in_reply_to para clientes sin hilos.
            ultimo = db.query_one(
                "SELECT event_id FROM matrix_mensajes WHERE account_id = ? AND room_id = ? "
                "AND (thread_root = ? OR event_id = ?) ORDER BY id DESC LIMIT 1",
                (account_id, room_id, thread_root, thread_root))
            contenido["m.relates_to"] = {
                "rel_type": "m.thread", "event_id": thread_root,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": (ultimo[0] if ultimo else thread_root)}}
        elif reply_to:
            contenido["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
        menciones = self._menciones_de(texto)
        if menciones and not editar_event:
            contenido["m.mentions"] = {"user_ids": menciones}
        try:
            resp = self._run(cliente.room_send(room_id, "m.room.message", contenido,
                             ignore_unverified_devices=self._ignorar_no_verificados(account_id)),
                             timeout=30.0)
            ok = isinstance(resp, RoomSendResponse)
            err_msg = None if ok else (getattr(resp, "message", None) or str(resp))
        except MatrixError as e:
            ok, err_msg = False, str(e)
        if not ok:
            # ESTADO DE ENVÍO (P2): un mensaje NUEVO que no salió se guarda como "fallido" con un
            # event_id local, para que quede visible en la conversación con opción de reintentar. Una
            # edición fallida no se materializa (el original sigue como estaba).
            if not editar_event:
                import uuid
                self._guardar_mensaje(
                    account_id, room_id, "accion" if emote else "msg",
                    sender=self._user_id(account_id), sender_nombre=_("Yo"),
                    texto=texto, event_id="local:" + uuid.uuid4().hex, fecha=ahora_iso(),
                    propio=True, leido=True, contar=False, reply_to=reply_to,
                    thread_root=thread_root, send_state="fallido")
            raise MatrixError(err_msg or _("No se pudo enviar el mensaje."))
        if editar_event:
            db.execute("UPDATE matrix_mensajes SET texto = ?, editado = 1 "
                       "WHERE account_id = ? AND event_id = ?",
                       (texto, account_id, editar_event))
        else:
            self._guardar_mensaje(
                account_id, room_id, "accion" if emote else "msg",
                sender=self._user_id(account_id), sender_nombre=_("Yo"),
                texto=texto, event_id=resp.event_id, fecha=ahora_iso(),
                propio=True, leido=True, contar=False, reply_to=reply_to,
                thread_root=thread_root)
        return resp.event_id

    def descartar_mensaje_local(self, account_id: int, event_id: str):
        """Borra SOLO una fila local no enviada (event_id 'local:...'): nunca un mensaje real del
        servidor. Lo usan 'descartar' y 'reintentar' (tras reenviar) el mensaje fallido."""
        if not (event_id or "").startswith("local:"):
            return
        try:
            fila = db.query_one("SELECT id FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
                                (account_id, event_id))
            db.execute("DELETE FROM matrix_mensajes WHERE account_id = ? AND event_id = ? "
                       "AND send_state = 'fallido'", (account_id, event_id))
            if fila:
                db.execute("DELETE FROM matrix_mensajes_fts WHERE msg_db_id = ?", (fila[0],))
        except Exception as e:
            log.debug(f"Matrix: no se pudo descartar el mensaje local: {e}")

    def reintentar_envio(self, account_id: int, room_id: str, local_event_id: str, texto: str,
                         emote: bool = False, reply_to: str = "", thread_root: str = "") -> str:
        """Reintenta enviar un mensaje que había fallado; si ahora sale, borra la fila local fallida."""
        eid = self.enviar_mensaje(account_id, room_id, texto, emote=emote, reply_to=reply_to,
                                  thread_root=thread_root)
        self.descartar_mensaje_local(account_id, local_event_id)
        return eid

    # ---------------- Hilos ----------------
    def mensajes_de_hilo(self, account_id: int, room_id: str, thread_root: str) -> List[Dict[str, Any]]:
        """Mensajes de un hilo: la raíz + las respuestas del SERVIDOR (relations m.thread),
        completadas con lo local. Devuelve [{quien, texto, fecha, event_id}] en orden."""
        cliente = self._cliente_conectado(account_id)
        from nio import RoomMessageText, RoomMessageNotice, RoomMessageEmote

        async def _leer():
            # rel_type debe ser el ENUM de nio (con un string revienta al construir la ruta).
            from nio.api import RelationshipType
            eventos = []
            async for ev in cliente.room_get_event_relations(
                    room_id, thread_root, rel_type=RelationshipType.thread, limit=200):
                eventos.append(ev)
            return eventos
        salida = []
        raiz = db.query_one(
            "SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto, date "
            "FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
            (account_id, thread_root))
        if raiz:
            salida.append({"quien": raiz[0], "texto": raiz[1], "fecha": raiz[2] or "",
                           "event_id": thread_root})
        try:
            eventos = self._run(_leer(), timeout=60.0)
        except Exception as e:
            log.debug(f"Matrix: relations del hilo no disponibles: {e}")
            eventos = []
        vistos = {thread_root}
        for ev in sorted(eventos, key=lambda e: getattr(e, "server_timestamp", 0)):
            eid = getattr(ev, "event_id", "") or ""
            if eid in vistos or not isinstance(ev, (RoomMessageText, RoomMessageNotice,
                                                    RoomMessageEmote)):
                continue
            vistos.add(eid)
            sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)
            quien = (sala.user_name(getattr(ev, "sender", "")) if sala else "") \
                or getattr(ev, "sender", "")
            salida.append({"quien": quien, "texto": getattr(ev, "body", "") or "",
                           "fecha": ts_a_iso(getattr(ev, "server_timestamp", 0)),
                           "event_id": eid})
        # Completar con lo local que el servidor no devolviera (p. ej. lo propio recién enviado).
        for quien, texto, fecha, eid in db.query(
                "SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto, date, event_id "
                "FROM matrix_mensajes WHERE account_id = ? AND thread_root = ? ORDER BY id",
                (account_id, thread_root)):
            if eid not in vistos:
                vistos.add(eid)
                salida.append({"quien": quien, "texto": texto, "fecha": fecha or "",
                               "event_id": eid})
        return salida

    # ---------------- Encuestas (MSC3381, las de Element) ----------------
    _POLL_START = "org.matrix.msc3381.poll.start"
    _POLL_RESPONSE = "org.matrix.msc3381.poll.response"
    _POLL_END = "org.matrix.msc3381.poll.end"

    def crear_encuesta(self, account_id: int, room_id: str, pregunta: str,
                       opciones: List[str], oculta: bool = False) -> str:
        """Crea una encuesta (formato de Element). `oculta`: los votos no se ven hasta cerrar."""
        pregunta = (pregunta or "").strip()
        opciones = [o.strip() for o in (opciones or []) if o.strip()]
        if not pregunta or len(opciones) < 2:
            raise MatrixError(_("La encuesta necesita una pregunta y al menos dos opciones."))
        cliente = self._cliente_conectado(account_id)
        from nio import RoomSendResponse
        import uuid
        respuestas = [{"id": f"op{i}_{uuid.uuid4().hex[:6]}", "org.matrix.msc1767.text": op}
                      for i, op in enumerate(opciones)]
        kind = ("org.matrix.msc3381.poll.undisclosed" if oculta
                else "org.matrix.msc3381.poll.disclosed")
        contenido = {
            "org.matrix.msc1767.text": pregunta + "\n" + "\n".join(
                f"{i+1}. {o}" for i, o in enumerate(opciones)),
            self._POLL_START: {
                "question": {"org.matrix.msc1767.text": pregunta},
                "kind": kind, "max_selections": 1, "answers": respuestas},
        }
        resp = self._run(cliente.room_send(room_id, self._POLL_START, contenido,
                                           ignore_unverified_devices=self._ignorar_no_verificados(account_id)), timeout=30.0)
        if not isinstance(resp, RoomSendResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        poll = {"pregunta": pregunta, "opciones": {r["id"]: r["org.matrix.msc1767.text"]
                                                   for r in respuestas},
                "votos": {}, "cerrada": False, "oculta": bool(oculta)}
        self._guardar_mensaje(
            account_id, room_id, "encuesta", sender=self._user_id(account_id),
            sender_nombre=_("Yo"), texto=self._texto_encuesta(poll),
            event_id=resp.event_id, fecha=ahora_iso(), propio=True, leido=True,
            contar=False, poll_json=json.dumps(poll))
        return resp.event_id

    def votar_encuesta(self, account_id: int, room_id: str, poll_event_id: str, answer_id: str):
        cliente = self._cliente_conectado(account_id)
        from nio import RoomSendResponse
        contenido = {"m.relates_to": {"rel_type": "m.reference", "event_id": poll_event_id},
                     self._POLL_RESPONSE: {"answers": [answer_id]}}
        resp = self._run(cliente.room_send(room_id, self._POLL_RESPONSE, contenido,
                                           ignore_unverified_devices=self._ignorar_no_verificados(account_id)), timeout=30.0)
        if not isinstance(resp, RoomSendResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        # El voto propio se registra como relación (dedupe con su eco del sync) y se recalcula.
        self._registrar_relacion(account_id, resp.event_id, poll_event_id, "m.reference",
                                 json.dumps([answer_id]), self._user_id(account_id))
        self._recalcular_votos(account_id, poll_event_id)

    def cerrar_encuesta(self, account_id: int, room_id: str, poll_event_id: str):
        cliente = self._cliente_conectado(account_id)
        from nio import RoomSendResponse
        contenido = {"m.relates_to": {"rel_type": "m.reference", "event_id": poll_event_id},
                     self._POLL_END: {}, "org.matrix.msc1767.text": _("Encuesta cerrada")}
        resp = self._run(cliente.room_send(room_id, self._POLL_END, contenido,
                                           ignore_unverified_devices=self._ignorar_no_verificados(account_id)), timeout=30.0)
        if not isinstance(resp, RoomSendResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        self._cerrar_encuesta_local(account_id, poll_event_id)

    @staticmethod
    def _texto_encuesta(poll: Dict[str, Any]) -> str:
        """Texto legible de la encuesta para la fila: pregunta + opciones con votos. En una encuesta
        OCULTA (undisclosed) los conteos NO se muestran hasta que se cierra (MX-012: privacidad)."""
        oculta_sin_cerrar = bool(poll.get("oculta")) and not poll.get("cerrada")
        conteo = {}
        if not oculta_sin_cerrar:
            for _u, ids in (poll.get("votos") or {}).items():
                for i in ids:
                    conteo[i] = conteo.get(i, 0) + 1
        partes = []
        for aid, texto in (poll.get("opciones") or {}).items():
            n = conteo.get(aid, 0)
            partes.append(f"{texto} ({n})" if n else texto)
        if poll.get("cerrada"):
            estado = _("[Encuesta CERRADA]")
        elif poll.get("oculta"):
            estado = _("[Encuesta oculta: los votos se ven al cerrar]")
        else:
            estado = _("[Encuesta]")
        return f"{estado} {poll.get('pregunta','')} — " + " · ".join(partes)

    def _cerrar_encuesta_local(self, account_id: int, poll_event_id: str):
        try:
            fila = db.query_one("SELECT poll_json FROM matrix_mensajes "
                                "WHERE account_id = ? AND event_id = ?",
                                (account_id, poll_event_id))
            if not fila or not fila[0]:
                return
            poll = json.loads(fila[0])
            poll["cerrada"] = True
            db.execute("UPDATE matrix_mensajes SET poll_json = ?, texto = ? "
                       "WHERE account_id = ? AND event_id = ?",
                       (json.dumps(poll), self._texto_encuesta(poll),
                        account_id, poll_event_id))
        except Exception as e:
            log.debug(f"Matrix: cierre de encuesta no aplicado: {e}")

    # ---------------- Ubicación ----------------
    def enviar_ubicacion(self, account_id: int, room_id: str, latitud: float,
                         longitud: float, descripcion: str = "") -> str:
        """Comparte una ubicación (m.location con geo URI)."""
        cliente = self._cliente_conectado(account_id)
        from nio import RoomSendResponse
        geo = f"geo:{latitud},{longitud}"
        cuerpo = descripcion or _("Ubicación")
        contenido = {"msgtype": "m.location", "body": cuerpo, "geo_uri": geo}
        resp = self._run(cliente.room_send(room_id, "m.room.message", contenido,
                                           ignore_unverified_devices=self._ignorar_no_verificados(account_id)), timeout=30.0)
        if not isinstance(resp, RoomSendResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        self._guardar_mensaje(
            account_id, room_id, "msg", sender=self._user_id(account_id),
            sender_nombre=_("Yo"), texto=_("[Ubicación: {0}] {1}").format(cuerpo, geo),
            event_id=resp.event_id, fecha=ahora_iso(), propio=True, leido=True, contar=False)
        return resp.event_id

    def reaccionar(self, account_id: int, room_id: str, event_id: str, emoji: str):
        """Envía una reacción (m.reaction con anotación) al mensaje indicado."""
        emoji = (emoji or "").strip()
        if not emoji:
            return
        cliente = self._cliente_conectado(account_id)
        from nio import RoomSendResponse
        contenido = {"m.relates_to": {"rel_type": "m.annotation",
                                      "event_id": event_id, "key": emoji}}
        resp = self._run(cliente.room_send(room_id, "m.reaction", contenido,
                                   ignore_unverified_devices=self._ignorar_no_verificados(account_id)), timeout=30.0)
        if not isinstance(resp, RoomSendResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        # Registrar la reacción como RELACIÓN deduplicable (MX-012): el eco del sync se deduplica por
        # event_id y el agregado se recalcula, no se incrementa a ciegas.
        self._reacciones_propias[(account_id, event_id, emoji)] = resp.event_id
        self._registrar_relacion(account_id, resp.event_id, event_id, "m.annotation", emoji,
                                 self._user_id(account_id))
        self._recalcular_reacciones(account_id, event_id)

    def quitar_reaccion(self, account_id: int, room_id: str, event_id: str, emoji: str):
        """Quita una reacción propia (redacta el evento m.reaction). Ahora se localiza el evento de
        reacción también en la BD de relaciones, así que funciona aunque se pusiera en otra sesión."""
        rid = self._reacciones_propias.pop((account_id, event_id, emoji), "")
        if not rid:
            fila = db.query_one(
                "SELECT event_id FROM matrix_relations WHERE account_id = ? AND target_event_id = ? "
                "AND rel_type = 'm.annotation' AND rel_key = ? AND sender = ? AND redacted = 0 "
                "ORDER BY id DESC LIMIT 1",
                (account_id, event_id, emoji, self._user_id(account_id)))
            rid = fila[0] if fila else ""
        if not rid:
            raise MatrixError(_("No encuentro tu reacción para quitarla."))
        cliente = self._cliente_conectado(account_id)
        from nio import RoomRedactResponse
        resp = self._run(cliente.room_redact(room_id, rid), timeout=30.0)
        if not isinstance(resp, RoomRedactResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        # Marcar la relación redactada y recalcular el agregado (idempotente).
        self._redactar_relacion(account_id, rid)

    # ---------------- Relaciones idempotentes (MX-012) ----------------
    def _registrar_relacion(self, account_id: int, event_id: str, target: str, rel_type: str,
                            rel_key: str, sender: str, ts: int = 0) -> bool:
        """Registra una relación (reacción m.annotation / voto m.reference) como evento deduplicable
        por event_id. Devuelve True si era nueva (no un duplicado del eco/replay)."""
        if not event_id or not target:
            return False
        try:
            filas = db.execute(
                "INSERT OR IGNORE INTO matrix_relations "
                "(account_id, event_id, target_event_id, rel_type, rel_key, sender, redacted, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (account_id, event_id, target, rel_type, rel_key or "", sender or "", int(ts or 0)))
            return bool(filas)
        except Exception as e:
            log.debug(f"Matrix: relación no registrada: {e}")
            return False

    def _recalcular_reacciones(self, account_id: int, target_event_id: str):
        """Recalcula el texto legible de reacciones ("👍 2, ❤ 1") del mensaje objetivo contando las
        relaciones NO redactadas. Idempotente: reprocesar el mismo sync deja el mismo resultado."""
        try:
            filas = db.query(
                "SELECT rel_key, COUNT(*) FROM matrix_relations WHERE account_id = ? "
                "AND target_event_id = ? AND rel_type = 'm.annotation' AND redacted = 0 "
                "GROUP BY rel_key", (account_id, target_event_id))
            texto = ", ".join(f"{k} {n}" for k, n in filas if k)
            db.execute("UPDATE matrix_mensajes SET reacciones = ? WHERE account_id = ? AND event_id = ?",
                       (texto, account_id, target_event_id))
        except Exception as e:
            log.debug(f"Matrix: recuento de reacciones no recalculado: {e}")

    def _recalcular_votos(self, account_id: int, poll_event_id: str):
        """Recalcula los votos de una encuesta desde las relaciones (último voto por votante, no
        redactado) y actualiza poll_json y el texto legible. Idempotente."""
        try:
            fila = db.query_one("SELECT poll_json FROM matrix_mensajes "
                                "WHERE account_id = ? AND event_id = ?", (account_id, poll_event_id))
            if not fila or not fila[0]:
                return
            poll = json.loads(fila[0])
            opciones = poll.get("opciones") or {}
            # Último voto por votante: la relación de mayor id (orden de llegada) de cada sender.
            filas = db.query(
                "SELECT sender, rel_key FROM matrix_relations WHERE account_id = ? "
                "AND target_event_id = ? AND rel_type = 'm.reference' AND redacted = 0 "
                "AND id IN (SELECT MAX(id) FROM matrix_relations WHERE account_id = ? "
                "AND target_event_id = ? AND rel_type = 'm.reference' AND redacted = 0 GROUP BY sender)",
                (account_id, poll_event_id, account_id, poll_event_id))
            votos = {}
            for sender, key in filas:
                try:
                    ans = json.loads(key) if key else []
                except Exception:
                    ans = []
                validas = [a for a in ans if a in opciones]
                if validas:
                    votos[sender] = validas
            poll["votos"] = votos
            db.execute("UPDATE matrix_mensajes SET poll_json = ?, texto = ? "
                       "WHERE account_id = ? AND event_id = ?",
                       (json.dumps(poll), self._texto_encuesta(poll), account_id, poll_event_id))
        except Exception as e:
            log.debug(f"Matrix: votos no recalculados: {e}")

    def _redactar_relacion(self, account_id: int, event_id: str) -> bool:
        """Si event_id es una relación conocida (reacción/voto), la marca redactada y recalcula su
        objetivo. Devuelve True si era una relación (y por tanto NO debe tratarse como la redacción
        de un mensaje normal). Así una redacción ajena de una reacción SÍ decrementa el agregado."""
        try:
            fila = db.query_one(
                "SELECT target_event_id, rel_type FROM matrix_relations "
                "WHERE account_id = ? AND event_id = ?", (account_id, event_id))
            if not fila:
                return False
            target, rel_type = fila[0], fila[1]
            db.execute("UPDATE matrix_relations SET redacted = 1 "
                       "WHERE account_id = ? AND event_id = ?", (account_id, event_id))
            if rel_type == "m.annotation":
                self._recalcular_reacciones(account_id, target)
            elif rel_type == "m.reference":
                self._recalcular_votos(account_id, target)
            return True
        except Exception as e:
            log.debug(f"Matrix: redacción de relación no aplicada: {e}")
            return False

    def borrar_mensaje(self, account_id: int, room_id: str, event_id: str):
        """Borra (redacción) un mensaje. El servidor decide si puedes (los tuyos, o con permisos)."""
        cliente = self._cliente_conectado(account_id)
        from nio import RoomRedactResponse
        resp = self._run(cliente.room_redact(room_id, event_id), timeout=30.0)
        if not isinstance(resp, RoomRedactResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        self._marcar_redactado(account_id, event_id)

    # ---------------- Apoyo de eventos ricos (también usados por el núcleo) ----------------
    def _marcar_redactado(self, account_id: int, event_id: str):
        # Si el evento redactado es una RELACIÓN (reacción/voto), decrementar su agregado en vez de
        # convertir un mensaje inexistente en "[Mensaje eliminado]" (MX-012).
        if self._redactar_relacion(account_id, event_id):
            return
        db.execute("UPDATE matrix_mensajes SET texto = ?, tipo = 'msg', media_mxc = '' "
                   "WHERE account_id = ? AND event_id = ?",
                   (_("[Mensaje eliminado]"), account_id, event_id))
        # Quitar el texto redactado del índice de búsqueda (ya no debe encontrarse).
        try:
            fila = db.query_one("SELECT id FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
                                (account_id, event_id))
            if fila:
                db.execute("DELETE FROM matrix_mensajes_fts WHERE msg_db_id = ?", (fila[0],))
        except Exception:
            pass

    def _aplicar_edicion(self, account_id: int, event_id_original: str, texto: str):
        db.execute("UPDATE matrix_mensajes SET texto = ?, editado = 1 "
                   "WHERE account_id = ? AND event_id = ?",
                   (texto, account_id, event_id_original))
        # Reindexar en la búsqueda global el texto EDITADO (antes la edición no tocaba el FTS y las
        # búsquedas seguían encontrando el texto viejo). Solo si esa fila estaba indexada.
        try:
            fila = db.query_one(
                "SELECT id, sender, sender_nombre, tipo FROM matrix_mensajes "
                "WHERE account_id = ? AND event_id = ?", (account_id, event_id_original))
            if fila and fila[3] in ("msg", "accion", "aviso"):
                db.execute("DELETE FROM matrix_mensajes_fts WHERE msg_db_id = ?", (fila[0],))
                if (texto or "").strip():
                    db.execute("INSERT INTO matrix_mensajes_fts (msg_db_id, sender, texto) "
                               "VALUES (?, ?, ?)", (fila[0], fila[2] or fila[1] or "", texto))
        except Exception:
            pass

    # ---------------- Crear / unirse ----------------
    def crear_dm(self, account_id: int, user_id: str) -> str:
        """Crea (o abre) un mensaje directo con un usuario. Devuelve el room_id."""
        user_id = (user_id or "").strip()
        if not user_id.startswith("@"):
            raise MatrixError("Escribe el usuario completo, por ejemplo @nombre:matrix.org.")
        # Si ya hay un DM con esa persona, reutilizarlo (mapa m.direct del sync).
        cliente = self._cliente_conectado(account_id)
        try:
            for salas in [(cliente.direct_rooms or {}).get(user_id)] :
                if salas:
                    return list(salas)[0]
        except Exception:
            pass
        from nio import RoomCreateResponse, RoomPreset
        resp = self._run(cliente.room_create(
            is_direct=True, invite=[user_id],
            preset=RoomPreset.trusted_private_chat), timeout=30.0)
        if not isinstance(resp, RoomCreateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        room_id = resp.room_id
        with self._estado_lock:
            self._dms.setdefault(account_id, set()).add(room_id)
        db.execute("""
            INSERT INTO matrix_salas (id, account_id, room_id, nombre, es_dm)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET es_dm = 1
        """, (db_id_sala(account_id, room_id), account_id, room_id, user_id))
        # m.direct en el servidor: sin esto, Element y otros dispositivos muestran el DM como sala
        # normal y el mapa se pierde al reiniciar. Best-effort (no romper la creación si falla).
        try:
            self._marcar_dm_en_servidor(account_id, user_id, room_id)
        except Exception as e:
            log.debug(f"Matrix: no se pudo actualizar m.direct: {e}")
        return room_id

    def _marcar_dm_en_servidor(self, account_id: int, user_id: str, room_id: str):
        """Añade (user_id -> room_id) al mapa m.direct del account data GLOBAL del servidor con
        read-modify-write, PRESERVANDO las entradas de otros usuarios y las salas ya existentes de
        este (la spec, módulo dm.md, exige m.direct para que otros clientes traten la sala como
        conversación directa). Bloqueante: se llama dentro de crear_dm (task_manager)."""
        import urllib.parse
        yo = self._user_id(account_id)
        if not yo:
            return
        # quote(safe='') OBLIGATORIO: el user_id lleva '@' y ':' (y podría llevar '/'); sin safe=''
        # el ':' quedaría sin codificar y la ruta sería ambigua.
        ruta = ("/_matrix/client/v3/user/" + urllib.parse.quote(yo, safe="")
                + "/account_data/m.direct")
        try:
            actual = self._api_http(account_id, "GET", ruta) or {}
        except Exception:
            actual = {}  # 404 si la cuenta aún no tiene m.direct: se crea de cero
        if not isinstance(actual, dict):
            actual = {}
        salas = list(actual.get(user_id) or [])
        if room_id in salas:
            return
        salas.append(room_id)
        actual[user_id] = salas
        self._api_http(account_id, "PUT", ruta, actual)

    def crear_sala(self, account_id: int, nombre: str, publica: bool = False,
                   tema: str = "") -> str:
        """Crea una sala (privada por defecto). Devuelve el room_id."""
        nombre = (nombre or "").strip()
        if not nombre:
            raise MatrixError("Escribe un nombre para la sala.")
        cliente = self._cliente_conectado(account_id)
        from nio import RoomCreateResponse, RoomVisibility, RoomPreset
        resp = self._run(cliente.room_create(
            visibility=RoomVisibility.public if publica else RoomVisibility.private,
            preset=RoomPreset.public_chat if publica else RoomPreset.private_chat,
            name=nombre, topic=tema or None), timeout=30.0)
        if not isinstance(resp, RoomCreateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        db.execute("""
            INSERT INTO matrix_salas (id, account_id, room_id, nombre, tema)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET nombre = excluded.nombre
        """, (db_id_sala(account_id, resp.room_id), account_id, resp.room_id, nombre, tema or ""))
        return resp.room_id

    def unirse_sala(self, account_id: int, sala: str) -> str:
        """Se une a una sala por id (!...) o alias (#sala:servidor). Devuelve el room_id."""
        sala = (sala or "").strip()
        if not sala:
            raise MatrixError("Escribe el alias o el identificador de la sala.")
        cliente = self._cliente_conectado(account_id)
        from nio import JoinResponse
        resp = self._run(cliente.join(sala), timeout=45.0)
        if not isinstance(resp, JoinResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        return resp.room_id

    # ---------------- Typing y lectura ----------------
    def enviar_typing(self, account_id: int, room_id: str, escribiendo: bool = True):
        """'Estoy escribiendo' hacia la sala (con timeout corto; fallar aquí no es grave)."""
        try:
            cliente = self._cliente_conectado(account_id)
            self._run(cliente.room_typing(room_id, typing_state=escribiendo, timeout=6000),
                      timeout=10.0)
        except Exception as e:
            log.debug(f"Matrix: typing no enviado: {e}")

    def marcar_leida_servidor(self, account_id: int, room_id: str):
        """Marca la sala como leída TAMBIÉN en el servidor (read marker en el último evento),
        para que otros clientes (Element) vean el mismo estado. Silencioso si falla."""
        try:
            fila = db.query_one(
                "SELECT event_id FROM matrix_mensajes WHERE account_id = ? AND room_id = ? "
                "ORDER BY id DESC LIMIT 1", (account_id, room_id))
            if not fila or not fila[0]:
                return
            cliente = self._cliente_conectado(account_id)
            self._run(cliente.room_read_markers(room_id, fully_read_event=fila[0],
                                                read_event=fila[0]), timeout=15.0)
        except Exception as e:
            log.debug(f"Matrix: read marker no enviado: {e}")

    # ---------------- Miembros ----------------
    def _miembros_unidos(self, account_id: int, room_id: str) -> List[tuple]:
        """Lista AUTORITATIVA de miembros unidos vía /joined_members. Con lazy-loading de miembros
        (y tras un sync incremental al reconectar), room.users está vacío hasta que el servidor los
        reenvía; por eso 'Miembros' salía 0 hasta escribir. La API /joined_members los trae al
        momento, sin depender del sync. Cae a room.users si la petición falla. NO usar en el hilo de
        la interfaz: hace una petición de red (llamar desde task_manager)."""
        try:
            cliente = self._cliente_conectado(account_id)
        except MatrixError:
            return []
        from nio.responses import JoinedMembersResponse
        try:
            resp = self._run(cliente.joined_members(room_id), timeout=30.0)
            if isinstance(resp, JoinedMembersResponse):
                return [(m.user_id, (getattr(m, "display_name", "") or m.user_id))
                        for m in (resp.members or [])]
        except Exception as e:
            log.debug(f"Matrix: /joined_members falló para {room_id}: {e}")
        sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)
        if sala is None:
            return []
        return [(uid, getattr(u, "display_name", "") or uid)
                for uid, u in (getattr(sala, "users", {}) or {}).items()]

    def miembros_de_sala(self, account_id: int, room_id: str) -> List[tuple]:
        """Miembros de la sala: [(user_id, nombre_mostrado)]. Vía /joined_members (fiable aunque el
        sync aún no los haya traído)."""
        salida = self._miembros_unidos(account_id, room_id)
        salida.sort(key=lambda t: t[1].lower())
        return salida
