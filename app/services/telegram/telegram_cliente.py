# -*- coding: utf-8 -*-
"""Cliente, handlers, carga de chats, historial y mensajes.

Mixin de TelegramService (parte de la refactorización modular).
"""
from app.services.telegram.telegram_common import (
    datetime, db, event_bus, log, tg_enums)


class _ClienteMixin:
    async def _cliente(self, account_id: int):
        """
        Versión ASÍNCRONA: devuelve (o crea+arranca) el Client. DEBE usarse dentro
        de las corrutinas (con await), porque ya se ejecuta en el bucle de eventos.
        Llamar aquí a self._run() provocaría un interbloqueo del bucle.
        """
        cli = self._clients.get(account_id)
        if cli is not None:
            return cli
        # Serializar la creación por cuenta: si dos corrutinas llegan a la vez, solo una crea y
        # arranca el cliente (la otra espera y reutiliza el ya creado). Evita clientes duplicados
        # con handlers en tiempo real repetidos. _aio_locks solo se toca en el bucle de Telegram.
        import asyncio as _asyncio
        lock = self._aio_locks.get(account_id)
        if lock is None:
            lock = _asyncio.Lock()
            self._aio_locks[account_id] = lock
        async with lock:
            cli = self._clients.get(account_id)
            if cli is not None:
                return cli
            client = self._nuevo_cliente(account_id)
            await client.start()
            self._registrar_handlers(client, account_id)
            with self._lock:
                self._clients[account_id] = client
            return client

    def obtener_cliente(self, account_id: int):
        """
        Versión SÍNCRONA para llamar desde un hilo worker (no desde el bucle).
        Internamente programa la creación del cliente en el bucle de Telegram.
        """
        with self._lock:
            if account_id in self._clients:
                return self._clients[account_id]
        return self._run(self._cliente(account_id))

    def _registrar_handlers(self, client, account_id: int):
        """Registra manejadores en tiempo real: mensajes nuevos, editados y borrados,
        estado en línea y 'escribiendo…'."""
        from pyrogram.handlers import (
            MessageHandler, EditedMessageHandler, DeletedMessagesHandler,
            UserStatusHandler, RawUpdateHandler)

        async def _on_message(_, message):
            try:
                self._guardar_mensaje(account_id, message)
                # TG-006: además del mensaje, ACTUALIZAR la lista de chats (vista previa, fecha y no
                # leídos) y crear el chat si es desconocido; antes un mensaje nuevo no movía el diálogo.
                self._actualizar_chat_por_mensaje(account_id, message)
                # El sonido solo para mensajes ENTRANTES (no los propios enviados desde otro dispositivo).
                if not getattr(message, "outgoing", False):
                    event_bus.emit("sound_trigger", "telegram_new_message")
                event_bus.emit("telegram_new_message", account_id, getattr(message.chat, "id", None))
            except Exception as e:
                log.debug(f"Error al procesar mensaje entrante de Telegram: {e}")

        async def _on_edited(_, message):
            try:
                self._guardar_mensaje(account_id, message)
                event_bus.emit("telegram_message_edited", account_id, getattr(message.chat, "id", None), message.id)
            except Exception as e:
                log.debug(f"Error al procesar mensaje editado de Telegram: {e}")

        async def _on_deleted(_, messages):
            try:
                por_chat = {}
                for m in messages:
                    cid = getattr(getattr(m, "chat", None), "id", None)
                    self._borrar_mensaje_cache(account_id, cid, m.id)
                    por_chat.setdefault(cid, []).append(m.id)
                for cid, ids in por_chat.items():
                    event_bus.emit("telegram_messages_deleted", account_id, cid, ids)
            except Exception as e:
                log.debug(f"Error al procesar mensajes borrados de Telegram: {e}")

        async def _on_user_status(_, user):
            try:
                estado = self._descripcion_estado(user)
                if estado:
                    event_bus.emit("telegram_user_status", account_id, user.id, estado)
            except Exception as e:
                log.debug(f"Error al procesar estado de usuario: {e}")

        async def _on_raw(_, update, users, chats):
            # Detectar acción "escribiendo…" / "grabando audio…" (UpdateUserTyping)
            try:
                tipo = update.__class__.__name__
                if tipo in ("UpdateUserTyping", "UpdateChatUserTyping", "UpdateChannelUserTyping"):
                    # TG-016: convertir el peer RAW al chat_id de Pyrogram para que coincida con la
                    # conversación abierta. En grupos básicos es -chat_id y en canales/supergrupos es
                    # -100…; usar el id crudo hacía que el "escribiendo…" no llegara a la vista.
                    from pyrogram import utils as _pyutils
                    if tipo == "UpdateChannelUserTyping":
                        chat_id = _pyutils.get_channel_id(getattr(update, "channel_id", 0))
                    elif tipo == "UpdateChatUserTyping":
                        chat_id = -abs(int(getattr(update, "chat_id", 0) or 0))
                    else:
                        chat_id = getattr(update, "user_id", None)
                    act = getattr(update, "action", None)
                    descripcion = self._descripcion_accion(act)
                    event_bus.emit("telegram_typing", account_id, chat_id, descripcion)
            except Exception as e:
                log.debug(f"Error al procesar acción de chat: {e}")

        client.add_handler(MessageHandler(_on_message))
        client.add_handler(EditedMessageHandler(_on_edited))
        client.add_handler(DeletedMessagesHandler(_on_deleted))
        client.add_handler(UserStatusHandler(_on_user_status))
        client.add_handler(RawUpdateHandler(_on_raw))

    def _borrar_mensaje_cache(self, account_id: int, chat_id, message_id: int):
        """Quita un mensaje de la caché al recibir un borrado en tiempo real. En chats
        privados/grupos el chat puede venir vacío (el id de mensaje es único por cuenta);
        en canales/supergrupos sí llega el chat y se acota el borrado a ese chat."""
        try:
            if chat_id is not None:
                db.execute(
                    "DELETE FROM telegram_messages WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                    (account_id, chat_id, message_id))
            else:
                db.execute(
                    "DELETE FROM telegram_messages WHERE account_id = ? AND message_id = ?",
                    (account_id, message_id))
        except Exception as e:
            log.debug(f"No se pudo borrar mensaje {message_id} de la caché: {e}")

    @staticmethod
    def _descripcion_accion(accion) -> str:
        """Traduce una acción de chat (typing) a texto."""
        nombre = accion.__class__.__name__ if accion is not None else ""
        mapa = {
            "SendMessageTypingAction": "escribiendo",
            "SendMessageRecordAudioAction": "grabando un audio",
            "SendMessageRecordRoundAction": "grabando un videomensaje",
            "SendMessageUploadAudioAction": "enviando un audio",
            "SendMessageUploadPhotoAction": "enviando una foto",
            "SendMessageUploadVideoAction": "enviando un vídeo",
            "SendMessageUploadDocumentAction": "enviando un archivo",
            "SendMessageGeoLocationAction": "compartiendo ubicación",
            "SendMessageChooseStickerAction": "eligiendo un sticker",
        }
        return mapa.get(nombre, "escribiendo")

    def podar_mensajes_antiguos(self, account_id: int, max_por_chat: int = 0):
        """Mantenimiento (Fase 6): recorta la caché de mensajes a los 'max_por_chat'
        más recientes por chat, para que telegram_messages no crezca sin fin. Con 0
        (por defecto) NO se poda nada (se mantienen todos). Los mensajes borrados se
        vuelven a pedir al servidor si abres una conversación antigua; no se borra
        ningún archivo descargado."""
        if not max_por_chat or max_por_chat <= 0:
            return
        try:
            db.execute("""
                DELETE FROM telegram_messages WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY chat_id ORDER BY date DESC) AS rn
                        FROM telegram_messages WHERE account_id = ?
                    ) WHERE rn > ?
                )
            """, (account_id, max_por_chat))
        except Exception as e:
            log.debug(f"No se pudo podar la caché de mensajes de Telegram (cuenta {account_id}): {e}")

    # --- CHATS / DIÁLOGOS ---
    def cargar_chats(self, account_id: int, limit: int = 200) -> int:
        """Descarga la lista de chats (diálogos) y los cachea. Devuelve el número guardado."""
        async def _coro():
            client = await self._cliente(account_id)
            chats = []
            async for dialog in client.get_dialogs(limit=limit):
                chats.append(dialog)
            return chats

        dialogs = self._run(_coro(), timeout=90)
        ids_vivos = set()
        # Tombstones: chats que el usuario borró o de los que salió. El servidor a veces sigue
        # devolviendo su diálogo (típico de canales/supergrupos), lo que hacía que "reaparecieran" al
        # actualizar. No se reinsertan mientras no llegue un mensaje MÁS NUEVO que el borrado (señal de
        # que el usuario volvió a entrar), en cuyo caso se limpia el tombstone y el chat vuelve.
        try:
            tombstones = {row[0]: (row[1] or "") for row in db.query(
                "SELECT chat_id, deleted_at FROM telegram_deleted_chats WHERE account_id = ?", (account_id,))}
        except Exception:
            tombstones = {}
        # Toda la lista de chats en UNA transacción (un commit en vez de uno por chat). Con el
        # auto-join por hilo, los db.execute de _guardar_chat se unen solos a esta transacción.
        with db.transaccion():
            for dialog in dialogs:
                cid = getattr(getattr(dialog, "chat", None), "id", None)
                if cid is None:
                    continue
                if cid in tombstones:
                    last_date = ""
                    try:
                        if dialog.top_message and dialog.top_message.date:
                            last_date = self._fecha_iso(dialog.top_message.date)
                    except Exception:
                        last_date = ""
                    if last_date and last_date > tombstones[cid]:
                        # Reactivado (mensaje nuevo tras el borrado): quitar el tombstone y guardar.
                        db.execute("DELETE FROM telegram_deleted_chats WHERE id = ?",
                                   (f"{account_id}:{cid}",))
                    else:
                        # Sigue borrado: NO reinsertar (así no reaparece al actualizar).
                        continue
                self._guardar_chat(account_id, dialog)
                ids_vivos.add(cid)
        # PODA: quitar de la caché los chats que ya NO están en tu lista de diálogos (los que has
        # ABANDONADO o CERRADO/ELIMINADO siendo administrador, o que han desaparecido). Sin esto,
        # como guardar solo INSERTA/actualiza, esos chats seguían apareciendo. Solo se poda si la
        # descarga NO se truncó (devolvió menos que el límite => son TODOS tus chats); si se truncó,
        # no se poda para no borrar chats que sí existen más allá del límite.
        if ids_vivos and len(dialogs) < limit:
            try:
                marcadores = ",".join("?" * len(ids_vivos))
                db.execute(
                    f"DELETE FROM telegram_chats WHERE account_id = ? AND chat_id NOT IN ({marcadores})",
                    (account_id, *ids_vivos))
            except Exception as e:
                log.debug(f"No se pudo podar la caché de chats de Telegram: {e}")
        # Actualizar también las carpetas (no crítico si falla)
        try:
            self._guardar_carpetas(account_id)
        except Exception as e:
            log.debug(f"No se pudieron actualizar las carpetas de Telegram: {e}")
        # Marcar quién está bloqueado, para que el menú alterne Bloquear/Desbloquear (no crítico).
        try:
            self.refrescar_bloqueados(account_id)
        except Exception as e:
            log.debug(f"No se pudo refrescar la lista de bloqueados de Telegram: {e}")
        return len(dialogs)

    def _guardar_carpetas(self, account_id: int):
        """Descarga las carpetas y las guarda con los ids de sus chats."""
        import json

        async def _coro():
            client = await self._cliente(account_id)
            carpetas = []
            for f in await client.get_folders():
                chat_ids = []
                for c in (getattr(f, "included_chats", None) or []):
                    cid = getattr(c, "id", None)
                    if cid is not None:
                        chat_ids.append(cid)
                carpetas.append({
                    "id": getattr(f, "id", 0),
                    "title": getattr(f, "name", "") or getattr(f, "title", "") or "",
                    "chat_ids": chat_ids,
                })
            return carpetas

        carpetas = self._run(_coro(), timeout=60)
        db.execute("DELETE FROM telegram_folders WHERE account_id = ?", (account_id,))
        for c in carpetas:
            db.execute(
                "INSERT INTO telegram_folders (id, account_id, folder_id, title, chat_ids_json) VALUES (?, ?, ?, ?, ?)",
                (f"{account_id}:{c['id']}", account_id, c["id"], c["title"], json.dumps(c["chat_ids"]))
            )

    @staticmethod
    def _chat_verificado(chat) -> bool:
        """Indica si el chat está verificado, usando la API nueva sin avisos."""
        vs = getattr(chat, "verification_status", None)
        if vs is not None:
            return bool(getattr(vs, "is_verified", False))
        try:
            return bool(chat.is_verified)
        except Exception:
            return False

    def _tipo_chat(self, chat) -> str:
        try:
            t = chat.type
            mapa = {
                tg_enums.ChatType.PRIVATE: "private",
                tg_enums.ChatType.BOT: "bot",
                tg_enums.ChatType.GROUP: "group",
                tg_enums.ChatType.SUPERGROUP: "supergroup",
                tg_enums.ChatType.CHANNEL: "channel",
            }
            return mapa.get(t, "private")
        except Exception:
            return "private"

    def _guardar_chat(self, account_id: int, dialog):
        chat = dialog.chat
        chat_id = chat.id
        title = chat.title or " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)])) or (chat.username or str(chat_id))
        last_msg = ""
        last_date = ""
        last_media = ""
        if dialog.top_message:
            last_msg = dialog.top_message.text or dialog.top_message.caption or ""
            if dialog.top_message.date:
                last_date = self._fecha_iso(dialog.top_message.date)
            media = getattr(dialog.top_message, "media", None)
            if media:
                last_media = getattr(media, "value", str(media))

        protegido = 1 if getattr(chat, "has_protected_content", False) else 0
        db.execute("""
            INSERT INTO telegram_chats (id, account_id, chat_id, title, chat_type, username, unread_count, last_message, last_message_date, pinned, muted, verified, last_media_type, protected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, chat_type=excluded.chat_type, username=excluded.username,
                unread_count=excluded.unread_count, last_message=excluded.last_message,
                last_message_date=excluded.last_message_date, pinned=excluded.pinned, muted=excluded.muted,
                last_media_type=excluded.last_media_type, protected=excluded.protected
        """, (
            f"{account_id}:{chat_id}", account_id, chat_id, title, self._tipo_chat(chat),
            getattr(chat, "username", None), dialog.unread_messages_count or 0,
            (last_msg or "").replace("\n", " ")[:200], last_date,
            1 if dialog.is_pinned else 0, 1 if getattr(dialog, "is_muted", False) else 0,
            1 if self._chat_verificado(chat) else 0, last_media, protegido
        ))

    # --- MENSAJES ---
    def cargar_historial(self, account_id: int, chat_id: int, limit: int = 50) -> int:
        """Descarga los últimos mensajes de un chat y los cachea."""
        async def _coro():
            client = await self._cliente(account_id)
            mensajes = []
            async for msg in client.get_chat_history(chat_id, limit=limit):
                mensajes.append(msg)
            return mensajes

        mensajes = self._run(_coro(), timeout=90)
        # Guardado por LOTES: con "sincronizar todo" un chat grande trae miles de
        # mensajes; una única transacción gigante retenía el lock de escritura (y el
        # WAL crecía de golpe). En lotes de 500 el resto de la app sigue fluida.
        for i in range(0, len(mensajes), 500):
            with db.transaccion():
                for msg in mensajes[i:i + 500]:
                    self._guardar_mensaje(account_id, msg)
        return len(mensajes)

    def cargar_mas_antiguos(self, account_id: int, chat_id: int, before_message_id: int,
                            limit: int = 100) -> int:
        """Trae al caché mensajes MÁS ANTIGUOS que 'before_message_id' (paginación hacia atrás con
        offset_id), para poder recuperar TODO el historial de un chat/grupo/canal por tandas.
        limit=0 = todo lo que quede hasta el principio. Devuelve cuántos se trajeron."""
        async def _coro():
            client = await self._cliente(account_id)
            mensajes = []
            async for msg in client.get_chat_history(chat_id, offset_id=int(before_message_id or 0),
                                                      limit=int(limit) or 0):
                mensajes.append(msg)
            return mensajes

        mensajes = self._run(_coro(), timeout=600)
        # Lotes de 500 (ver cargar_historial): no retener el lock de escritura minutos.
        for i in range(0, len(mensajes), 500):
            with db.transaccion():
                for msg in mensajes[i:i + 500]:
                    self._guardar_mensaje(account_id, msg)
        return len(mensajes)

    @staticmethod
    def _fecha_iso(valor) -> str:
        """
        Normaliza una fecha de Telegram a ISO.

        Kurigram convierte las marcas de tiempo con datetime.fromtimestamp(), que
        devuelve la hora LOCAL (naive). Por tanto NO hay que forzar UTC: se guarda
        tal cual (hora local naive) y formatear_tiempo_relativo la interpreta como
        local. Forzar UTC sumaba el desfase horario y mostraba "en el futuro".
        """
        if isinstance(valor, datetime):
            # Normalizar a hora local naive de forma robusta usando el epoch:
            # .timestamp() da el instante correcto sea naive-local o aware, y
            # fromtimestamp lo devuelve en hora local.
            try:
                return datetime.fromtimestamp(valor.timestamp()).isoformat()
            except (OverflowError, OSError, ValueError):
                if valor.tzinfo is not None:
                    valor = valor.astimezone().replace(tzinfo=None)
                return valor.isoformat()
        return str(valor or "")

    def _guardar_mensaje(self, account_id: int, msg):
        chat_id = getattr(msg.chat, "id", None)
        if chat_id is None:
            return
        sender_name = ""
        sender_username = ""
        sender_id = None
        if msg.from_user:
            sender_id = msg.from_user.id
            # Nombre MOSTRADO: nombre + apellido. Para los contactos que tengas guardados, Telegram
            # devuelve aquí el nombre con el que TÚ los renombraste. Así el lector dice "Pedro Pérez"
            # (o "Pedro el buyanguero") en vez de "@pedroperez24". El @usuario se guarda aparte para
            # quien prefiera mostrarlo con el campo {username} de la plantilla.
            nombre = " ".join(p for p in (msg.from_user.first_name, msg.from_user.last_name) if p).strip()
            if msg.from_user.username:
                sender_username = f"@{msg.from_user.username}"
            sender_name = nombre or sender_username or str(sender_id)
        elif msg.sender_chat:
            sender_id = msg.sender_chat.id
            sender_name = msg.sender_chat.title or ""
            if getattr(msg.sender_chat, "username", None):
                sender_username = f"@{msg.sender_chat.username}"

        media_type = ""
        duration = 0
        file_name = ""
        if msg.media:
            try:
                media_type = msg.media.value if hasattr(msg.media, "value") else str(msg.media)
            except Exception:
                media_type = "media"
            # Extraer duración (voz/audio/vídeo) y nombre de archivo (documento/audio)
            for attr in ("voice", "audio", "video", "video_note"):
                obj = getattr(msg, attr, None)
                if obj is not None:
                    duration = getattr(obj, "duration", 0) or 0
                    file_name = getattr(obj, "file_name", "") or ""
                    break
            if not file_name and getattr(msg, "document", None) is not None:
                file_name = getattr(msg.document, "file_name", "") or ""

        # Una LISTA DE TAREAS (checklist) no llega como media normal: marcarla como tal para que la
        # lista la etiquete bien y su menú ofrezca "Lista de tareas" (y no acciones de descarga).
        if getattr(msg, "checklist", None) is not None:
            media_type = "checklist"

        # Texto del mensaje. Para una ENCUESTA, msg.text/caption vienen vacíos: usar la PREGUNTA como
        # texto para que la lista no muestre una fila vacía con solo "[Encuesta]".
        texto_msg = str(msg.text or msg.caption or "")
        poll = getattr(msg, "poll", None)
        if poll is not None and not texto_msg:
            preg = getattr(poll, "question", "") or ""
            texto_msg = str(getattr(preg, "text", preg) or "")  # question puede ser objeto con .text
        chk = getattr(msg, "checklist", None)
        if chk is not None and not texto_msg:
            texto_msg = str(getattr(chk, "title", "") or "")

        # Reenvío: nombre del autor original (usuario, canal o remitente oculto). Vistas: solo en
        # publicaciones de canal. Sirven para los campos {reenviado} y {vistas} de la plantilla.
        forward_from = ""
        ff = getattr(msg, "forward_from", None)
        if ff is not None:
            forward_from = " ".join(p for p in (getattr(ff, "first_name", None),
                                                getattr(ff, "last_name", None)) if p).strip() \
                or (ff.username and f"@{ff.username}") or str(ff.id)
        elif getattr(msg, "forward_from_chat", None) is not None:
            forward_from = getattr(msg.forward_from_chat, "title", "") or ""
        elif getattr(msg, "forward_sender_name", None):
            forward_from = msg.forward_sender_name
        views = getattr(msg, "views", 0) or 0
        # Identificador de álbum: agrupa los mensajes que se enviaron juntos como un solo envío
        # con varios adjuntos (fotos/vídeos). Permite reconstruir todos los adjuntos de ese envío.
        media_group_id = getattr(msg, "media_group_id", None)
        # Teclado del mensaje (botones de bot): se serializa a JSON para mostrarlo sin volver a la red.
        reply_markup = self._serializar_reply_markup(msg)
        # Reacciones del mensaje: se guarda un texto ya legible ("👍 3 · ❤ 1") para mostrarlo en la
        # fila sin volver a la red. Cada Reaction tiene emoji y count (los premium, custom, salen ⭐).
        reactions = self._reacciones_texto(msg)
        # Tema (topic) del foro al que pertenece el mensaje, para poder filtrar la vista por tema.
        thread_id = getattr(msg, "message_thread_id", None)
        # Enlaces del mensaje: se extraen de las entidades (hipervínculos con texto visible distinto de
        # la URL, p. ej. "Descargar aquí"), de las URLs sueltas del texto y de la vista previa web
        # (web_page). Se guardan como JSON para poder abrirlos sin volver a la red y sin depender de
        # que la URL aparezca literalmente en el texto (que es lo que fallaba con los canales).
        urls = self._extraer_urls(msg)

        fecha = self._fecha_iso(msg.date)

        db.execute("""
            INSERT INTO telegram_messages (id, account_id, chat_id, message_id, sender_name, sender_username, sender_id, text, media_type, date, outgoing, reply_to, edited, duration, file_name, forward_from, views, media_group_id, reply_markup, reactions, thread_id, urls)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                text=excluded.text, media_type=excluded.media_type, edited=excluded.edited,
                duration=excluded.duration, file_name=excluded.file_name, date=excluded.date,
                sender_name=excluded.sender_name, sender_username=excluded.sender_username,
                forward_from=excluded.forward_from, views=excluded.views,
                media_group_id=excluded.media_group_id, reply_markup=excluded.reply_markup,
                reactions=excluded.reactions, thread_id=excluded.thread_id, urls=excluded.urls
        """, (
            f"{account_id}:{chat_id}:{msg.id}", account_id, chat_id, msg.id,
            sender_name, sender_username, sender_id, texto_msg, media_type, fecha,
            1 if msg.outgoing else 0,
            msg.reply_to_message_id if getattr(msg, "reply_to_message_id", None) else None,
            1 if getattr(msg, "edit_date", None) else 0,
            duration, file_name, forward_from, views, media_group_id, reply_markup, reactions, thread_id, urls
        ))

    def _actualizar_chat_por_mensaje(self, account_id: int, msg):
        """(TG-006) Mantiene la lista de chats al día con un mensaje NUEVO: actualiza la vista previa,
        la fecha y el contador de no leídos; si el chat no existe todavía (desconocido), lo crea. Solo
        para mensajes en vivo (no para el historial): incrementar no leídos aquí sería incorrecto en
        una carga masiva. Un mensaje propio (outgoing) actualiza la vista previa pero no suma no leídos."""
        try:
            chat = getattr(msg, "chat", None)
            chat_id = getattr(chat, "id", None)
            if chat is None or chat_id is None:
                return
            inc = 0 if getattr(msg, "outgoing", False) else 1
            texto = str(getattr(msg, "text", "") or getattr(msg, "caption", "") or "")
            last_media = ""
            media = getattr(msg, "media", None)
            if media:
                last_media = getattr(media, "value", str(media))
                if not texto:
                    texto = f"[{last_media}]"
            fecha = self._fecha_iso(msg.date) if getattr(msg, "date", None) else ""
            title = (getattr(chat, "title", None)
                     or " ".join(filter(None, [getattr(chat, "first_name", None),
                                               getattr(chat, "last_name", None)]))
                     or getattr(chat, "username", None) or str(chat_id))
            db.execute("""
                INSERT INTO telegram_chats (id, account_id, chat_id, title, chat_type, username,
                                            unread_count, last_message, last_message_date, last_media_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_message = excluded.last_message,
                    last_message_date = excluded.last_message_date,
                    last_media_type = excluded.last_media_type,
                    title = excluded.title,
                    unread_count = unread_count + ?
            """, (f"{account_id}:{chat_id}", account_id, chat_id, title, self._tipo_chat(chat),
                  getattr(chat, "username", None), inc, texto.replace("\n", " ")[:200], fecha,
                  last_media, inc))
        except Exception as e:
            log.debug(f"TG: no se pudo actualizar el chat por mensaje nuevo: {e}")

    @staticmethod
    def _extraer_urls_list(msg) -> list:
        """Lista (ordenada y sin repetidos) de TODAS las URLs de un mensaje. Fuentes:
        1. Entidades TEXT_LINK: hipervínculos cuyo texto visible NO es la URL (ent.url).
        2. Entidades URL: URLs escritas en el texto (el fragmento del texto, con offset/longitud en
           UTF-16, como los da Telegram).
        3. Vista previa web (web_page): la página enlazada que genera la tarjeta.
        4. Respaldo: URLs http(s) sueltas encontradas por expresión regular en el texto/pie.
        Así se recuperan los enlaces aunque no aparezcan literalmente escritos (el caso típico de los
        canales, donde los enlaces van como "Descargar aquí")."""
        import re
        urls = []
        def add(u):
            u = (u or "").strip()
            if u and u not in urls:
                urls.append(u)

        def de_entidades(base_str, entidades):
            if not base_str or not entidades:
                return
            u16 = base_str.encode("utf-16-le")
            for ent in entidades:
                url_ent = getattr(ent, "url", None)
                if url_ent:                      # TEXT_LINK: la URL viene aparte
                    add(url_ent)
                    continue
                tipo = getattr(ent, "type", None)
                nombre = str(getattr(tipo, "name", "") or getattr(tipo, "value", "") or tipo).lower()
                if nombre.endswith("url"):        # URL: la URL es el propio fragmento de texto
                    try:
                        off = int(getattr(ent, "offset", 0)); length = int(getattr(ent, "length", 0))
                        frag = u16[off * 2:(off + length) * 2].decode("utf-16-le", "ignore")
                        add(frag)
                    except Exception:
                        pass

        for campo_txt, campo_ent in (("text", "entities"), ("caption", "caption_entities")):
            de_entidades(str(getattr(msg, campo_txt, None) or ""), getattr(msg, campo_ent, None) or [])
        wp = getattr(msg, "web_page", None)
        if wp is not None:
            add(getattr(wp, "url", None) or getattr(wp, "display_url", None))
        base = str(getattr(msg, "text", None) or getattr(msg, "caption", None) or "")
        for u in re.findall(r'https?://[^\s\)\]\}<>"]+', base):
            add(u)
        return urls

    @staticmethod
    def _extraer_urls(msg) -> str:
        """Igual que _extraer_urls_list pero serializado a JSON para guardarlo (o "" si no hay)."""
        import json
        urls = _ClienteMixin._extraer_urls_list(msg)
        return json.dumps(urls, ensure_ascii=False) if urls else ""

    @staticmethod
    def _reacciones_texto(msg) -> str:
        """Texto legible de las reacciones de un mensaje: "👍 3 · ❤ 1" (o "" si no tiene)."""
        mr = getattr(msg, "reactions", None)
        if not mr:
            return ""
        partes = []
        for r in (getattr(mr, "reactions", None) or []):
            emoji = getattr(r, "emoji", None) or ("⭐" if getattr(r, "custom_emoji_id", None) else "")
            cnt = getattr(r, "count", 0) or 0
            if emoji and cnt:
                partes.append(f"{emoji} {cnt}")
        return " · ".join(partes)
