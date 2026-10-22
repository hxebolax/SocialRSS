# -*- coding: utf-8 -*-
"""Hilos, vídeo, bookmarks, reportes, muted words y DMs.

Mixin de BlueskyService (parte de la refactorización modular).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
from app.services.bluesky.bluesky_common import (
    json, Callable, List, Optional, db, BlueskyServiceError, task_manager, BlueskyPost, log,
    models, ETIQUETAS_CONTENIDO, _g, _ahora_iso)


class _AvanzadoMixin:
    def publicar_hilo_async(self, account_id: int, textos: List[str],
                           langs: Optional[List[str]] = None,
                           labels: Optional[List[str]] = None,
                           reply_control: Optional[str] = None,
                           callback: Optional[Callable] = None):
        """
        Publica una secuencia de posts encadenados (cada uno responde al anterior).
        Devuelve la lista de URIs publicadas.
        """
        textos = [t.strip() for t in textos if t and t.strip()]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            root_uri = root_cid = None
            prev_uri = prev_cid = None
            uris = []
            labels_obj = None
            vals = [v for v in (labels or []) if v in ETIQUETAS_CONTENIDO]
            if vals:
                labels_obj = models.ComAtprotoLabelDefs.SelfLabels(
                    values=[models.ComAtprotoLabelDefs.SelfLabel(val=v) for v in vals])

            for i, texto in enumerate(textos):
                if ctx.is_cancelled():
                    raise Exception("Hilo cancelado.")
                ctx.report_progress(int((i / len(textos)) * 100),
                                    f"Publicando {i + 1} de {len(textos)}...")
                facets = self._construir_facets(client, texto)
                reply = None
                if prev_uri:
                    reply = models.AppBskyFeedPost.ReplyRef(
                        root=models.ComAtprotoRepoStrongRef.Main(uri=root_uri, cid=root_cid),
                        parent=models.ComAtprotoRepoStrongRef.Main(uri=prev_uri, cid=prev_cid))
                record = models.AppBskyFeedPost.Record(
                    created_at=_ahora_iso(), text=texto, facets=facets, reply=reply,
                    langs=langs or None, labels=(labels_obj if i == 0 else None))
                resp = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"], collection="app.bsky.feed.post", record=record))
                uri = _g(resp, "uri")
                cid = _g(resp, "cid")
                uris.append(uri)
                if i == 0:
                    root_uri, root_cid = uri, cid
                    # Threadgate solo en la raíz del hilo.
                    if reply_control and reply_control != "everybody":
                        try:
                            if reply_control == "nobody":
                                allow = []
                            elif reply_control == "following":
                                allow = [models.AppBskyFeedThreadgate.FollowingRule()]
                            elif reply_control == "mentioned":
                                allow = [models.AppBskyFeedThreadgate.MentionRule()]
                            else:
                                allow = None
                            if allow is not None:
                                tg = models.AppBskyFeedThreadgate.Record(
                                    post=uri, allow=allow, created_at=_ahora_iso())
                                client.com.atproto.repo.create_record(
                                    models.ComAtprotoRepoCreateRecord.Data(
                                        repo=yo["did"], collection="app.bsky.feed.threadgate",
                                        rkey=uri.rsplit("/", 1)[-1], record=tg))
                        except Exception as e:
                            log.debug(f"No se pudo aplicar threadgate al hilo: {e}")
                prev_uri, prev_cid = uri, cid
            return uris

        def _on_finish(result, error):
            if error:
                if callback:
                    callback(False, self._mensaje_error(error))
            else:
                if callback:
                    callback(True, result)

        task_manager.start_task(_bg, callback=_on_finish, success_sound="ready",
                               error_sound="error", pass_context=True)

    def enviar_hilo_dm_async(self, account_id: int, actor: str, textos: List[str],
                             callback: Optional[Callable] = None):
        """Envía los trozos de un hilo como MENSAJES seguidos de una conversación privada.

        En Bluesky no existe la publicación privada: el lexicon app.bsky.feed.post no tiene
        ningún campo de audiencia (text, facets, reply, embed, langs, labels, tags, createdAt),
        así que todo post es público y el threadgate solo limita QUIÉN PUEDE RESPONDER, no quién
        lo ve. Lo único privado de la red son los mensajes directos (chat.bsky.convo), así que un
        "hilo en privado" se manda como una serie de mensajes al destinatario, en orden.

        `actor` puede ser un handle o un DID. Devuelve {'convo_id', 'title', 'enviados'}.
        """
        textos = [t.strip() for t in textos if t and t.strip()]

        def _bg(ctx):
            if not textos:
                raise ValueError("No hay nada que enviar.")
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            if not did:
                raise ValueError(f"No se ha encontrado la cuenta {actor}.")
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            resp = chat.chat.bsky.convo.get_convo_for_members({"members": [did]})
            convo = _g(resp, "convo")
            convo_id = _g(convo, "id")
            _m, titulo, _g2, _mapa = self._miembros_convo(convo, mi_did)
            enviados = 0
            for i, texto in enumerate(textos):
                if ctx.is_cancelled():
                    raise Exception("Envío cancelado.")
                ctx.report_progress(int((i / len(textos)) * 100),
                                    f"Enviando {i + 1} de {len(textos)}...")
                facets = self._construir_facets(chat, texto)
                msg = models.ChatBskyConvoDefs.MessageInput(text=texto, facets=facets)
                chat.chat.bsky.convo.send_message(
                    models.ChatBskyConvoSendMessage.Data(convo_id=convo_id, message=msg))
                enviados += 1
            return {"convo_id": convo_id, "title": titulo, "enviados": enviados}

        def _on_finish(result, error):
            if error:
                if callback:
                    callback(False, self._mensaje_error(error))
            else:
                if callback:
                    callback(True, result)

        task_manager.start_task(_bg, callback=_on_finish, success_sound="ready",
                                error_sound="error", pass_context=True)

    def publicar_video_async(self, account_id: int, texto: str, video_path: str,
                            alt: str = "", reply_to_uri: Optional[str] = None,
                            langs: Optional[List[str]] = None, callback: Optional[Callable] = None):
        """Publica un post con vídeo. DELEGA en publicar_post_async(es_video=True), la vía
        completa: la ruta antigua (send_video) perdía el aviso de contenido, el control de
        respuestas y la cita, y no ponía relación de aspecto (auditoría Bluesky BSKY-002).
        Se conserva esta firma por compatibilidad con llamadores externos/plugins."""
        self.publicar_post_async(
            account_id, texto, reply_to_uri=reply_to_uri,
            media_paths=[video_path], alt_texts=[alt or ""], es_video=True,
            langs=langs, callback=callback)

    def limites_video_async(self, account_id: int, callback: Optional[Callable] = None):
        """Devuelve los límites de subida de vídeo (cuántos vídeos quedan, tamaño máx.)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.video.get_upload_limits()
            return {
                "can_upload": bool(_g(resp, "can_upload", "canUpload", default=True)),
                "remaining_daily_videos": _g(resp, "remaining_daily_videos", "remainingDailyVideos", default=None),
                "message": _g(resp, "message", default="") or "",
            }
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def enviar_interaccion_async(self, account_id: int, item_uri: str, feed_uri: str,
                                event: str = "app.bsky.feed.defs#interactionSeen",
                                callback: Optional[Callable] = None):
        """Envía una señal de interacción de feed (analítica del algoritmo). Reutiliza el
        feed_context guardado con la asociación de timeline (auditoría BSKY-004): sin él,
        la señal no representaba la impresión real que el generador entregó."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            fila = db.query_one(
                "SELECT t.feed_context FROM bluesky_timeline_posts t "
                "JOIN bluesky_posts p ON p.id = t.post_db_id "
                "WHERE t.account_id = ? AND p.uri = ? AND t.feed_context IS NOT NULL LIMIT 1",
                (account_id, item_uri))
            feed_context = fila[0] if fila else None
            inter = models.AppBskyFeedDefs.Interaction(
                item=item_uri, event=event, feed_context=feed_context)
            client.app.bsky.feed.send_interactions({"interactions": [inter]})
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # TANDA 4 — MARCADORES, MODERACIÓN Y PREFERENCIAS
    # ------------------------------------------------------------------
    def crear_bookmark_async(self, account_id: int, post_db_id: str, callback: Optional[Callable] = None):
        """Añade un post a marcadores."""
        fila = db.query_one("SELECT uri, cid FROM bluesky_posts WHERE id = ?", (post_db_id,))
        if not fila:
            if callback:
                callback(None, BlueskyServiceError("Post no encontrado."))
            return
        uri, cid = fila

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.bookmark.create_bookmark({"uri": uri, "cid": cid})
            db.execute("UPDATE bluesky_posts SET bookmarked = 1 WHERE id = ?", (post_db_id,))
            db.execute(
                "INSERT OR IGNORE INTO bluesky_timeline_posts (account_id, timeline, post_db_id) VALUES (?, 'bookmarks', ?)",
                (account_id, post_db_id))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def borrar_bookmark_async(self, account_id: int, post_db_id: str, callback: Optional[Callable] = None):
        """Quita un post de marcadores."""
        fila = db.query_one("SELECT uri FROM bluesky_posts WHERE id = ?", (post_db_id,))
        if not fila:
            if callback:
                callback(None, BlueskyServiceError("Post no encontrado."))
            return
        uri = fila[0]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.bookmark.delete_bookmark({"uri": uri})
            db.execute("UPDATE bluesky_posts SET bookmarked = 0 WHERE id = ?", (post_db_id,))
            db.execute(
                "DELETE FROM bluesky_timeline_posts WHERE account_id = ? AND timeline = 'bookmarks' AND post_db_id = ?",
                (account_id, post_db_id))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cargar_bookmarks_async(self, account_id: int, cargar_mas: bool = False,
                              callback: Optional[Callable] = None):
        """Descarga la lista de marcadores y la cachea en el buffer 'bookmarks'."""
        clave = f"{account_id}:bookmarks"

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cursor = self._cursors.get(clave) if cargar_mas else None
            resp = client.app.bsky.bookmark.get_bookmarks(
                {"cursor": cursor, "limit": 50})
            self._cursors[clave] = _g(resp, "cursor")
            # Toda la página en UNA transacción (auditoría Bluesky 7.4): antes cada
            # marcador hacía sus escrituras sueltas (varios commits por elemento).
            posts = []
            with db.transaccion() as _conn:
                for bm in _g(resp, "bookmarks", default=[]):
                    item = _g(bm, "item", default=None)
                    if not item:
                        continue
                    po = self._procesar_y_guardar_post(account_id, item, conn=_conn)
                    if po:
                        _conn.execute("UPDATE bluesky_posts SET bookmarked = 1 WHERE id = ?", (po.id,))
                        _conn.execute(
                            "INSERT OR IGNORE INTO bluesky_timeline_posts (account_id, timeline, post_db_id) VALUES (?, 'bookmarks', ?)",
                            (account_id, po.id))
                        posts.append(po)
            return posts
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"bsky:{account_id}:bookmarks:"
                                           f"{'older' if cargar_mas else 'fresh'}")

    _REASON_MAP = {
        "spam": "com.atproto.moderation.defs#reasonSpam",
        "violation": "com.atproto.moderation.defs#reasonViolation",
        "misleading": "com.atproto.moderation.defs#reasonMisleading",
        "sexual": "com.atproto.moderation.defs#reasonSexual",
        "rude": "com.atproto.moderation.defs#reasonRude",
        "other": "com.atproto.moderation.defs#reasonOther",
    }

    def reportar_async(self, account_id: int, reason_type: str, comentario: str = "",
                      post_db_id: Optional[str] = None, did: Optional[str] = None,
                      callback: Optional[Callable] = None):
        """Reporta una publicación (post_db_id) o una cuenta (did) al servicio de moderación."""
        subject_info = {}
        if post_db_id:
            fila = db.query_one("SELECT uri, cid FROM bluesky_posts WHERE id = ?", (post_db_id,))
            if not fila:
                if callback:
                    callback(None, BlueskyServiceError("Post no encontrado."))
                return
            subject_info = {"uri": fila[0], "cid": fila[1]}
        elif did:
            subject_info = {"did": did}

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if "uri" in subject_info:
                subject = models.ComAtprotoRepoStrongRef.Main(
                    uri=subject_info["uri"], cid=subject_info["cid"])
            else:
                subject = models.ComAtprotoAdminDefs.RepoRef(did=subject_info["did"])
            client.com.atproto.moderation.create_report(
                models.ComAtprotoModerationCreateReport.Data(
                    reason_type=self._REASON_MAP.get(reason_type, self._REASON_MAP["other"]),
                    subject=subject,
                    reason=comentario or None))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def silenciar_hilo_async(self, account_id: int, root_uri: str, silenciar: bool = True,
                            callback: Optional[Callable] = None):
        """Silencia (o reactiva) las notificaciones de un hilo."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if silenciar:
                client.app.bsky.graph.mute_thread({"root": root_uri})
            else:
                client.app.bsky.graph.unmute_thread({"root": root_uri})
            # No hay API para listar los hilos silenciados: se recuerdan en local para ofrecer
            # "Quitar silencio del hilo" en el menú (B14).
            try:
                self._recordar_hilo_silenciado(account_id, root_uri, silenciar)
            except Exception as e:
                log.debug(f"No se pudo recordar el hilo silenciado: {e}")
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # Palabras silenciadas, servicios de etiquetado y demás preferencias de moderación:
    # ver bluesky_preferencias._PreferenciasMixin.

    # ------------------------------------------------------------------
    # STARTER PACKS PROPIOS (crear / borrar)
    # ------------------------------------------------------------------
    def crear_starter_pack_async(self, account_id: int, nombre: str, descripcion: str,
                                 lista_uri: str, feeds_uris: Optional[list] = None,
                                 callback: Optional[Callable] = None):
        """Crea un starter pack propio (paquete de bienvenida) a partir de una LISTA tuya ya
        existente y, opcionalmente, unos feeds. El starter pack referencia la lista; para
        crearlo desde cero, primero se crea una lista con crear_lista_async."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            record = models.AppBskyGraphStarterpack.Record(
                name=nombre, description=descripcion or None, list=lista_uri,
                feeds=[models.AppBskyGraphStarterpack.FeedItem(uri=u) for u in (feeds_uris or [])] or None,
                created_at=_ahora_iso())
            resp = client.com.atproto.repo.create_record(models.ComAtprotoRepoCreateRecord.Data(
                repo=yo["did"], collection="app.bsky.graph.starterpack", record=record))
            return {"uri": _g(resp, "uri", default="")}
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def borrar_starter_pack_async(self, account_id: int, pack_uri: str,
                                  callback: Optional[Callable] = None):
        """Borra un starter pack propio por su URI."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            rkey = pack_uri.rsplit("/", 1)[-1]
            client.com.atproto.repo.delete_record(models.ComAtprotoRepoDeleteRecord.Data(
                repo=yo["did"], collection="app.bsky.graph.starterpack", rkey=rkey))
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    # ------------------------------------------------------------------
    # TANDA 3 — MENSAJES DIRECTOS (chat.bsky.convo)
    # ------------------------------------------------------------------
    def _miembros_convo(self, convo, mi_did: str):
        """Devuelve (lista miembros [{did,handle,name}], título, es_grupo, mapa did->nombre)."""
        miembros = []
        mapa = {}
        for m in _g(convo, "members", default=[]) or []:
            did = _g(m, "did", default="")
            handle = _g(m, "handle", default="")
            nombre = _g(m, "display_name", "displayName", default="") or handle
            mapa[did] = nombre
            if did != mi_did:
                miembros.append({"did": did, "handle": handle, "name": nombre})
        es_grupo = len(miembros) > 1
        titulo = ", ".join(m["name"] or m["handle"] for m in miembros) or "(conversación)"
        return miembros, titulo, es_grupo, mapa

    def _chat_capaz(self, account_id: int):
        """Cliente de chat, comprobando la CAPACIDAD de la credencial la primera vez
        (auditoría BSKY-003): el chat exige una contraseña de aplicación PRIVILEGIADA;
        con una normal el PDS responde "Bad token method" y antes el usuario solo veía
        un error genérico en cada intento. La sonda se hace una vez por cuenta y sesión;
        sin capacidad, todas las operaciones de chat explican cómo arreglarlo."""
        if not hasattr(self, "_chat_cap"):
            self._chat_cap = {}
        cap = self._chat_cap.get(account_id)
        mensaje = _(
            "Esta cuenta no puede usar los mensajes directos: su contraseña de aplicación "
            "se creó sin el permiso de mensajes directos. Crea una nueva en bsky.app → "
            "Configuración → Contraseñas de aplicación marcando \"Permitir acceso a los "
            "mensajes directos\" y vuelve a autorizar la cuenta con ella.")
        if cap is False:
            raise BlueskyServiceError(mensaje)
        chat = self._chat(account_id)
        if cap is None:
            try:
                chat.chat.bsky.convo.list_convos({"limit": 1})
                self._chat_cap[account_id] = True
            except Exception as e:
                if "bad token" in str(e).lower():
                    self._chat_cap[account_id] = False
                    raise BlueskyServiceError(mensaje)
                raise
        return chat

    def chat_disponible(self, account_id: int):
        """True/False si ya se sondeó la capacidad de chat; None si aún no se sabe."""
        return getattr(self, "_chat_cap", {}).get(account_id)

    # ------------------------------------------------------------------
    # SINCRONIZACIÓN INCREMENTAL DEL CHAT (chat.bsky.convo.getLog, auditoría §11)
    # ------------------------------------------------------------------
    def _nombre_remitente_cacheado(self, account_id: int, convo_id: str, sender_did: str,
                                   mi_did: str) -> str:
        """Nombre del remitente desde la caché local (mensajes ya guardados), sin pedir
        getConvo por cada evento (auditoría §11: los miembros ya están cacheados)."""
        if sender_did == mi_did:
            return "Yo"
        row = db.query_one(
            "SELECT sender_name FROM bluesky_messages WHERE account_id = ? AND convo_id = ? "
            "AND sender_did = ? AND sender_name != '' LIMIT 1",
            (account_id, convo_id, sender_did))
        return (row[0] if row else "") or sender_did

    def _aplicar_evento_chat(self, account_id: int, mi_did: str, ev, resumen: dict):
        """Aplica UN evento del log del chat a la caché local (mensajes y conversaciones)."""
        tipo = str(_g(ev, "py_type", "$type", default="")).lower()
        convo_id = _g(ev, "convo_id", "convoId", default="") or ""
        if not convo_id:
            return
        if "logcreatemessage" in tipo or "logaddreaction" in tipo or "logremovereaction" in tipo:
            m = _g(ev, "message", default=None)
            mid = _g(m, "id") if m is not None else None
            if not mid:
                return
            sender_did = _g(_g(m, "sender"), "did", default="") or ""
            texto = _g(m, "text", default="") or ""
            tipo_msg = str(_g(m, "py_type", "$type", default="")).lower()
            if not texto:
                if "deleted" in tipo_msg:
                    texto = _("[Mensaje eliminado]")
                elif _g(m, "embed", default=None) is not None:
                    texto = _("[Adjunto]")
            sent_at = _g(m, "sent_at", "sentAt", default="") or ""
            outgoing = 1 if sender_did == mi_did else 0
            reacc = _g(m, "reactions", default=None)
            reacc_json = ""
            if reacc:
                try:
                    reacc_json = json.dumps([{"emoji": _g(r, "value", default=""),
                                              "did": _g(_g(r, "sender"), "did", default="")}
                                             for r in reacc], ensure_ascii=False)
                except Exception:
                    reacc_json = ""
            nombre = self._nombre_remitente_cacheado(account_id, convo_id, sender_did, mi_did)
            db.execute("""
                INSERT OR REPLACE INTO bluesky_messages
                (id, account_id, convo_id, message_id, sender_did, sender_name, text,
                 embed_json, reactions_json, sent_at, outgoing)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (f"{account_id}:{convo_id}:{mid}", account_id, convo_id, mid, sender_did,
                  nombre, texto, "", reacc_json, sent_at, outgoing))
            if "logcreatemessage" in tipo:
                resumen["nuevos"] += 1
                resumen["convos"].add(convo_id)
                # Reflejar el último mensaje y el contador de no leídos de la conversación.
                db.execute(
                    "UPDATE bluesky_convos SET last_message = ?, last_message_date = ?, "
                    "unread_count = unread_count + ? WHERE account_id = ? AND convo_id = ?",
                    (texto, sent_at, 0 if outgoing else 1, account_id, convo_id))
        elif "logdeletemessage" in tipo:
            mid = _g(_g(ev, "message", default=None), "id", default=None)
            if mid:
                db.execute("DELETE FROM bluesky_messages WHERE account_id = ? AND convo_id = ? "
                           "AND message_id = ?", (account_id, convo_id, mid))
                resumen["borrados"] += 1
                resumen["convos"].add(convo_id)
        elif "logreadmessage" in tipo:
            # Leído (también desde OTRO dispositivo): poner a cero el contador local.
            db.execute("UPDATE bluesky_convos SET unread_count = 0 WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            resumen["leidos"] += 1
            resumen["convos"].add(convo_id)
        elif "logleaveconvo" in tipo:
            # Abandonaste (o te sacaron de) la conversación: reconciliar la caché.
            db.execute("DELETE FROM bluesky_messages WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            db.execute("DELETE FROM bluesky_convos WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            resumen["convos"].add(convo_id)
        elif "logmuteconvo" in tipo or "logunmuteconvo" in tipo:
            db.execute("UPDATE bluesky_convos SET muted = ? WHERE account_id = ? AND convo_id = ?",
                       (1 if "logmuteconvo" in tipo else 0, account_id, convo_id))

    def sincronizar_chat_async(self, account_id: int, callback: Optional[Callable] = None):
        """Sincronización INCREMENTAL de los mensajes directos con chat.bsky.convo.getLog
        (auditoría §11): altas, borrados, lecturas (también desde otros dispositivos),
        reacciones, silencios y salidas de grupo, sin re-descargar conversaciones enteras.
        El cursor se persiste por cuenta. La PRIMERA vez no procesa el histórico (ya lo
        trae la carga normal): solo avanza el cursor hasta el presente para escuchar desde
        ahora. Devuelve {"nuevos", "borrados", "leidos", "convos": [ids afectados]}."""
        # Sesión caducada: no insistir con los directos hasta que se vuelva a iniciar sesión.
        if self.abortar_si_sesion_caducada(account_id, callback):
            return

        def _bg(ctx):
            from app.core.config_manager import config as _cfg
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            cursores = dict(_cfg.get("bluesky", "chat_log_cursor", {}) or {})
            cursor = cursores.get(str(account_id)) or ""
            resumen = {"nuevos": 0, "borrados": 0, "leidos": 0, "convos": set()}
            primera_vez = not cursor
            for _vuelta in range(20 if not primera_vez else 50):  # tope de páginas por ciclo
                if ctx.is_cancelled():
                    break
                params = {"cursor": cursor} if cursor else {}
                resp = chat.chat.bsky.convo.get_log(params)
                logs = _g(resp, "logs", default=[])
                nuevo = _g(resp, "cursor") or ""
                if not primera_vez and logs:
                    with db.transaccion():
                        for ev in logs:
                            self._aplicar_evento_chat(account_id, mi_did, ev, resumen)
                if not nuevo or nuevo == cursor:
                    cursor = nuevo or cursor
                    break
                cursor = nuevo
                if not logs:
                    break
            if cursor:
                cursores[str(account_id)] = cursor
                _cfg.set("bluesky", "chat_log_cursor", cursores)
            resumen["convos"] = sorted(resumen["convos"])
            return resumen
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"bsky:chatlog:{account_id}")

    def cargar_convos_async(self, account_id: int, cargar_mas: bool = False,
                           callback: Optional[Callable] = None):
        """Lista las conversaciones (DMs) y las cachea."""
        clave = f"{account_id}:convos"

        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            cursor = self._cursors.get(clave) if cargar_mas else None
            resp = chat.chat.bsky.convo.list_convos({"cursor": cursor, "limit": 50})
            self._cursors[clave] = _g(resp, "cursor")
            salida = []
            with db.transaccion():
                for convo in _g(resp, "convos", default=[]):
                    convo_id = _g(convo, "id")
                    miembros, titulo, es_grupo, _mapa = self._miembros_convo(convo, mi_did)
                    last = _g(convo, "last_message", default=None)
                    last_text = _g(last, "text", default="") or ""
                    last_date = _g(last, "sent_at", "sentAt", default="") or ""
                    unread = _g(convo, "unread_count", "unreadCount", default=0) or 0
                    muted = 1 if _g(convo, "muted", default=False) else 0
                    db_id = f"{account_id}:{convo_id}"
                    db.execute("""
                        INSERT OR REPLACE INTO bluesky_convos
                        (id, account_id, convo_id, members_json, title, unread_count, muted,
                         last_message, last_message_date, is_group)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (db_id, account_id, convo_id, json.dumps(miembros, ensure_ascii=False),
                          titulo, unread, muted, last_text, last_date, 1 if es_grupo else 0))
                    salida.append({"convo_id": convo_id, "title": titulo, "unread": unread,
                                   "last": last_text, "date": last_date, "muted": bool(muted)})
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cargar_mensajes_async(self, account_id: int, convo_id: str, cargar_mas: bool = False,
                             callback: Optional[Callable] = None):
        """Carga los mensajes de una conversación y los cachea (orden cronológico)."""
        clave = f"{account_id}:msg:{convo_id}"

        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            # Mapa did->nombre desde los miembros de la conversación.
            mapa = {}
            try:
                cv = chat.chat.bsky.convo.get_convo({"convo_id": convo_id})
                _m, _t, _g2, mapa = self._miembros_convo(_g(cv, "convo"), mi_did)
            except Exception as e:
                log.debug(f"No se pudieron leer miembros del convo: {e}")
            cursor = self._cursors.get(clave) if cargar_mas else None
            resp = chat.chat.bsky.convo.get_messages(
                {"convo_id": convo_id, "cursor": cursor, "limit": 50})
            self._cursors[clave] = _g(resp, "cursor")
            mensajes = []
            with db.transaccion():
                for m in _g(resp, "messages", default=[]):
                    mid = _g(m, "id")
                    if not mid:  # mensajes borrados
                        continue
                    sender_did = _g(_g(m, "sender"), "did", default="")
                    texto = _g(m, "text", default="") or ""
                    # Mensajes de SISTEMA y tipos sin texto (auditoría Bluesky §11): un
                    # deletedMessageView u otro union sin 'text' aparecía EN BLANCO en la
                    # conversación. Se les da una representación legible.
                    tipo_msg = str(_g(m, "py_type", "$type", default="")).lower()
                    if not texto:
                        if "deleted" in tipo_msg:
                            texto = _("[Mensaje eliminado]")
                        elif tipo_msg and "messageview" not in tipo_msg:
                            texto = _("[Evento de la conversación]")
                        elif _g(m, "embed", default=None) is not None:
                            texto = _("[Adjunto]")
                    sent_at = _g(m, "sent_at", "sentAt", default="") or ""
                    outgoing = 1 if sender_did == mi_did else 0
                    sender_name = mapa.get(sender_did, "") or ("Yo" if outgoing else sender_did)
                    reacc = _g(m, "reactions", default=None)
                    reacc_json = ""
                    if reacc:
                        try:
                            reacc_json = json.dumps([{"emoji": _g(r, "value", default=""),
                                                      "did": _g(_g(r, "sender"), "did", default="")}
                                                     for r in reacc], ensure_ascii=False)
                        except Exception:
                            reacc_json = ""
                    db_id = f"{account_id}:{convo_id}:{mid}"
                    db.execute("""
                        INSERT OR REPLACE INTO bluesky_messages
                        (id, account_id, convo_id, message_id, sender_did, sender_name, text,
                         embed_json, reactions_json, sent_at, outgoing)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (db_id, account_id, convo_id, mid, sender_did, sender_name, texto,
                          "", reacc_json, sent_at, outgoing))
                    mensajes.append({"id": mid, "sender": sender_name, "text": texto,
                                     "sent_at": sent_at, "outgoing": bool(outgoing)})
            return mensajes
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def enviar_mensaje_async(self, account_id: int, convo_id: str, texto: str,
                            callback: Optional[Callable] = None):
        """Envía un mensaje de texto (con facets) a una conversación."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            facets = self._construir_facets(chat, texto)
            msg = models.ChatBskyConvoDefs.MessageInput(text=texto, facets=facets)
            chat.chat.bsky.convo.send_message(
                models.ChatBskyConvoSendMessage.Data(convo_id=convo_id, message=msg))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def borrar_mensaje_async(self, account_id: int, convo_id: str, message_id: str,
                            callback: Optional[Callable] = None):
        """Borra un mensaje para uno mismo."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.convo.delete_message_for_self(
                {"convo_id": convo_id, "message_id": message_id})
            db.execute("DELETE FROM bluesky_messages WHERE account_id = ? AND convo_id = ? AND message_id = ?",
                       (account_id, convo_id, message_id))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def marcar_convo_leido_async(self, account_id: int, convo_id: str,
                                callback: Optional[Callable] = None):
        """Marca una conversación como leída."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.convo.update_read({"convo_id": convo_id})
            db.execute("UPDATE bluesky_convos SET unread_count = 0 WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def silenciar_convo_async(self, account_id: int, convo_id: str, silenciar: bool = True,
                             callback: Optional[Callable] = None):
        """Silencia o quita el silencio de una conversación."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            if silenciar:
                chat.chat.bsky.convo.mute_convo({"convo_id": convo_id})
            else:
                chat.chat.bsky.convo.unmute_convo({"convo_id": convo_id})
            db.execute("UPDATE bluesky_convos SET muted = ? WHERE account_id = ? AND convo_id = ?",
                       (1 if silenciar else 0, account_id, convo_id))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def salir_convo_async(self, account_id: int, convo_id: str, callback: Optional[Callable] = None):
        """Abandona una conversación."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.convo.leave_convo({"convo_id": convo_id})
            db.execute("DELETE FROM bluesky_convos WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            db.execute("DELETE FROM bluesky_messages WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def reaccionar_mensaje_async(self, account_id: int, convo_id: str, message_id: str,
                                emoji: str, quitar: bool = False, callback: Optional[Callable] = None):
        """Añade o quita una reacción (emoji) a un mensaje."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            data = {"convo_id": convo_id, "message_id": message_id, "value": emoji}
            if quitar:
                chat.chat.bsky.convo.remove_reaction(data)
            else:
                chat.chat.bsky.convo.add_reaction(data)
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def abrir_convo_con_async(self, account_id: int, did: str, callback: Optional[Callable] = None):
        """Obtiene (o crea) la conversación con un usuario y devuelve su convo_id+título."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            resp = chat.chat.bsky.convo.get_convo_for_members({"members": [did]})
            convo = _g(resp, "convo")
            convo_id = _g(convo, "id")
            _m, titulo, _g2, _mapa = self._miembros_convo(convo, mi_did)
            return {"convo_id": convo_id, "title": titulo}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # GRUPOS DE CHAT (chat.bsky.group)
    # ------------------------------------------------------------------
    # Bluesky admite conversaciones de GRUPO además de los mensajes de dos: crear el grupo,
    # administrar quién está, repartir un enlace de invitación y aprobar a quien lo pide. Son los
    # mismos lexicons que usa W Social, así que ambas redes se manejan igual desde la interfaz.
    def crear_grupo_async(self, account_id: int, nombre: str, actores: List[str],
                          callback: Optional[Callable] = None):
        """Crea un grupo de chat con las cuentas indicadas (handles o DIDs)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            chat = self._chat_capaz(account_id)
            dids = [d for d in (self._resolver_did(client, a) for a in actores) if d]
            if not dids:
                raise ValueError("Añade al menos una cuenta al grupo.")
            resp = chat.chat.bsky.group.create_group({"name": nombre, "members": dids})
            convo = _g(resp, "convo", default=None) or resp
            return {"convo_id": _g(convo, "id", default=""), "name": nombre}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def editar_grupo_async(self, account_id: int, convo_id: str, nombre: str,
                           callback: Optional[Callable] = None):
        """Cambia el nombre de un grupo."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.group.edit_group({"convo_id": convo_id, "name": nombre})
            db.execute("UPDATE bluesky_convos SET title = ? WHERE account_id = ? AND convo_id = ?",
                       (nombre, account_id, convo_id))
            return nombre
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def gestionar_miembros_grupo_async(self, account_id: int, convo_id: str, actores: List[str],
                                       quitar: bool = False, callback: Optional[Callable] = None):
        """Añade o expulsa cuentas de un grupo."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            chat = self._chat_capaz(account_id)
            dids = [d for d in (self._resolver_did(client, a) for a in actores) if d]
            if not dids:
                raise ValueError("No se ha reconocido ninguna de esas cuentas.")
            if quitar:
                chat.chat.bsky.group.remove_members({"convo_id": convo_id, "members": dids})
            else:
                chat.chat.bsky.group.add_members({"convo_id": convo_id, "members": dids})
            return len(dids)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def enlace_grupo_async(self, account_id: int, convo_id: str, accion: str = "crear",
                           callback: Optional[Callable] = None):
        """Enlace de invitación del grupo: crear, activar, desactivar o regenerar."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            datos = {"convo_id": convo_id}
            if accion == "crear":
                resp = chat.chat.bsky.group.create_join_link(datos)
            elif accion == "activar":
                resp = chat.chat.bsky.group.enable_join_link(datos)
            elif accion == "desactivar":
                resp = chat.chat.bsky.group.disable_join_link(datos)
            elif accion == "editar":
                resp = chat.chat.bsky.group.edit_join_link(datos)
            else:
                raise ValueError(f"Acción de enlace desconocida: {accion}")
            return {"enlace": _g(resp, "link", "join_link", "joinLink", default=""),
                    "activo": bool(_g(resp, "enabled", default=True))}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def solicitudes_grupo_async(self, account_id: int, convo_id: str,
                                callback: Optional[Callable] = None):
        """Quién ha pedido entrar en el grupo."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.group.list_join_requests({"convo_id": convo_id, "limit": 100})
            salida = []
            for r in (_g(resp, "requests", default=[]) or []):
                actor = _g(r, "actor", default=None) or r
                salida.append({"did": _g(actor, "did", default=""),
                               "handle": _g(actor, "handle", default=""),
                               "nombre": _g(actor, "display_name", "displayName", default=""),
                               "fecha": _g(r, "created_at", "createdAt", default="")})
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

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
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def pedir_entrada_grupo_async(self, account_id: int, enlace: str,
                                  callback: Optional[Callable] = None):
        """Pide entrar en un grupo con su enlace de invitación."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.group.request_join({"link": enlace})
            return {"convo_id": _g(_g(resp, "convo", default=None), "id", default=""),
                    "pendiente": bool(_g(resp, "pending", default=False))}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def previsualizar_enlace_grupo_async(self, account_id: int, enlaces: List[str],
                                         callback: Optional[Callable] = None):
        """Qué hay detrás de un enlace de grupo (nombre y nº de participantes) antes de entrar."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.group.get_join_link_previews({"links": enlaces})
            return [{"enlace": _g(p, "link", default=""), "nombre": _g(p, "name", default=""),
                     "miembros": _g(p, "member_count", "memberCount", default=0)}
                    for p in (_g(resp, "previews", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

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
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def salir_grupo_async(self, account_id: int, convo_id: str, callback: Optional[Callable] = None):
        """Sale de un grupo (deja la conversación y la quita de la caché local)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.convo.leave_convo({"convo_id": convo_id})
            db.execute("DELETE FROM bluesky_messages WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            db.execute("DELETE FROM bluesky_convos WHERE account_id = ? AND convo_id = ?",
                       (account_id, convo_id))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def solicitudes_mensajes_async(self, account_id: int, callback: Optional[Callable] = None):
        """Solicitudes de mensaje pendientes (DMs de gente que no sigues). list_convo_requests."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            mi_did = self._yo(account_id)["did"]
            resp = chat.chat.bsky.convo.list_convo_requests({"limit": 50})
            out = []
            for convo in (_g(resp, "convos", default=[]) or []):
                _m, titulo, _g2, _mapa = self._miembros_convo(convo, mi_did)
                out.append({"convo_id": _g(convo, "id", default=""), "title": titulo})
            return out
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def aceptar_solicitud_convo_async(self, account_id: int, convo_id: str, callback: Optional[Callable] = None):
        """Acepta una solicitud de mensaje para poder responder (accept_convo)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            chat.chat.bsky.convo.accept_convo({"convo_id": convo_id})
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def miembros_convo_async(self, account_id: int, convo_id: str, callback: Optional[Callable] = None):
        """Miembros de una conversación (get_convo_members)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            resp = chat.chat.bsky.convo.get_convo({"convo_id": convo_id})
            convo = _g(resp, "convo")
            return [{"did": _g(m, "did", default=""), "handle": _g(m, "handle", default=""),
                     "name": _g(m, "display_name", default="")}
                    for m in (_g(convo, "members", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def estado_subida_video_async(self, account_id: int, job_id: str, callback: Optional[Callable] = None):
        """Estado de un trabajo de subida de vídeo (video.get_job_status): progreso/estado/blob."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.video.get_job_status({"job_id": job_id})
            js = _g(resp, "job_status")
            return {"state": _g(js, "state", default=""), "progress": _g(js, "progress", default=0),
                    "error": _g(js, "error", default=""), "blob": _g(js, "blob", default=None)}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # BÚSQUEDA LOCAL (FTS)
    # ------------------------------------------------------------------
    def buscar_posts_fts(self, account_id: int, query_text: str, limit: int = 50) -> List[BlueskyPost]:
        """Busca posts cacheados con FTS5 (para la búsqueda global).

        BÚSQUEDA LITERAL por defecto (auditoría Bluesky 13.1): antes el texto del usuario
        iba crudo a MATCH y una comilla sin cerrar, un guion ("social-rss") o un operador
        incompleto ("foo OR") producían un error de sintaxis FTS y CERO resultados,
        indistinguible de "no hay". Cada término se entrecomilla (doblando comillas
        internas). Además el filtro de cuenta se aplica dentro de la consulta y, si FTS
        fallara igualmente, se cae a LIKE en vez de devolver vacío."""
        posts: List[BlueskyPost] = []
        palabras = [w for w in (query_text or "").split() if w]
        if not palabras:
            return posts
        consulta = " ".join('"' + w.replace('"', '""') + '"' for w in palabras)
        try:
            filas = db.query("""
                SELECT p.*, COALESCE(p.mod_json,'') FROM bluesky_posts p
                JOIN bluesky_posts_fts f ON p.id = f.post_db_id
                WHERE bluesky_posts_fts MATCH ? AND p.account_id = ?
                LIMIT ?
            """, (consulta, account_id, limit))
            for f in filas:
                posts.append(self._fila_a_post(f))
            return self._moderar_resultados(account_id, posts)
        except Exception as e:
            log.debug(f"FTS de Bluesky no disponible para esta consulta; se usa LIKE: {e}")
        # Respaldo LIKE (más lento pero nunca falla por sintaxis).
        try:
            patron = "%" + (query_text or "").strip() + "%"
            filas = db.query("""
                SELECT p.*, COALESCE(p.mod_json,'') FROM bluesky_posts p
                WHERE p.account_id = ? AND (p.content LIKE ? OR p.author_name LIKE ?
                      OR p.author_handle LIKE ?)
                LIMIT ?
            """, (account_id, patron, patron, patron, limit))
            for f in filas:
                posts.append(self._fila_a_post(f))
        except Exception as e:
            log.error(f"Error en búsqueda de Bluesky: {e}")
        return self._moderar_resultados(account_id, posts)

    def _moderar_resultados(self, account_id: int, posts: List[BlueskyPost]) -> List[BlueskyPost]:
        """Moderación de los resultados de la búsqueda local (B2/B5): lo filtrado no sale y lo
        difuminado (palabra silenciada, etiqueta con aviso, publicación ocultada) se muestra
        con su aviso en lugar del texto."""
        from .bluesky_moderacion import moderar_post, texto_aviso
        from .bluesky_moddatos import de_json
        try:
            prefs = self.prefs_moderacion(account_id)
        except Exception:
            return posts
        salida = []
        for po in posts:
            try:
                res = moderar_post(de_json(po.mod_json), prefs, contexto="lista", uri=po.uri,
                                   etiquetas=po.labels, author_did=po.author_did)
            except Exception as e:
                log.debug(f"Moderación de resultado de búsqueda fallida: {e}")
                salida.append(po)
                continue
            if res["filtrar"]:
                continue
            if res["difuminar"]:
                po.content = texto_aviso(res)
            salida.append(po)
        return salida

    def _fila_a_post(self, fila) -> BlueskyPost:
        """Convierte una fila de bluesky_posts en un BlueskyPost."""
        cols = ["id", "account_id", "uri", "cid", "author_did", "author_handle", "author_name",
                "content", "created_at", "indexed_at", "langs", "labels", "reply_root_uri",
                "reply_root_cid", "reply_parent_uri", "reply_parent_cid", "quote_uri",
                "like_count", "repost_count", "reply_count", "quote_count", "liked_uri",
                "reposted_uri", "bookmarked", "media_json", "external_json", "urls_json",
                "repost_by_name", "repost_by_handle"]
        d = dict(zip(cols, fila))
        return BlueskyPost(
            id=d["id"], account_id=d["account_id"], uri=d["uri"], cid=d["cid"],
            author_did=d["author_did"], author_handle=d["author_handle"], author_name=d["author_name"],
            content=d["content"], created_at=d["created_at"], indexed_at=d["indexed_at"],
            langs=d["langs"], labels=d["labels"], reply_root_uri=d["reply_root_uri"],
            reply_root_cid=d["reply_root_cid"], reply_parent_uri=d["reply_parent_uri"],
            reply_parent_cid=d["reply_parent_cid"], quote_uri=d["quote_uri"],
            like_count=d["like_count"] or 0, repost_count=d["repost_count"] or 0,
            reply_count=d["reply_count"] or 0, quote_count=d["quote_count"] or 0,
            liked_uri=d["liked_uri"], reposted_uri=d["reposted_uri"],
            bookmarked=bool(d["bookmarked"]),
            media_attachments=json.loads(d["media_json"]) if d["media_json"] else [],
            external_card=json.loads(d["external_json"]) if d["external_json"] else None,
            urls=json.loads(d["urls_json"]) if d["urls_json"] else [],
            repost_by_name=d["repost_by_name"], repost_by_handle=d["repost_by_handle"],
            # Las consultas de búsqueda añaden COALESCE(mod_json,'') al final de la fila.
            mod_json=(fila[-1] if isinstance(fila[-1], str) and fila[-1].startswith("{")
                      else None),
        )
