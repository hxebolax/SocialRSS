# -*- coding: utf-8 -*-
"""Envío/edición/borrado, media, encuestas y grabación de voz.

Mixin de TelegramService (parte de la refactorización modular).
"""
from app.services.telegram.telegram_common import (
    time, Optional, Callable, db, get_data_path, log, TelegramError)

# Tipos de multimedia que SON un archivo descargable. Los demás (web_page/vista previa de enlace,
# encuesta, ubicación, contacto, dado, juego, lista de tareas…) NO tienen archivo, así que no deben
# aparecer en la lista de adjuntos ni como "--- Adjunto ---" en Ver mensaje.
_MEDIA_ARCHIVO = {"photo", "video", "audio", "voice", "video_note", "document", "animation", "sticker"}


class _MensajesMixin:
    def enviar_mensaje(self, account_id: int, chat_id: int, texto: str, reply_to: Optional[int] = None,
                       schedule_date=None):
        """Envía un mensaje de texto a un chat, opcionalmente como respuesta.
        schedule_date (datetime) lo programa para esa fecha en lugar de enviarlo ya."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_message(
                chat_id, texto, reply_to_message_id=reply_to, schedule_date=schedule_date)

        msg = self._run(_coro())
        # Los mensajes programados no aparecen aún en el historial: no los cacheamos.
        if not schedule_date:
            self._guardar_mensaje(account_id, msg)
        return msg

    # --- ACCIONES SOBRE MENSAJES ---
    def editar_mensaje(self, account_id: int, chat_id: int, message_id: int, texto: str):
        """Edita el texto de un mensaje propio."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.edit_message_text(chat_id, message_id, texto)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def editar_pie_mensaje(self, account_id: int, chat_id: int, message_id: int, caption: str):
        """Edita el pie (caption) de un mensaje multimedia propio (foto, vídeo, audio, documento)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.edit_message_caption(chat_id, message_id, caption)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def editar_media_mensaje(self, account_id: int, chat_id: int, message_id: int,
                             ruta: str, caption: str = ""):
        """Reemplaza el archivo multimedia de un mensaje propio por otro (según extensión)."""
        import os
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram.types import (InputMediaPhoto, InputMediaVideo,
                                        InputMediaAudio, InputMediaDocument)
            ext = os.path.splitext(ruta)[1].lower()
            if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
                media = InputMediaVideo(ruta, caption=caption)
            elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
                media = InputMediaPhoto(ruta, caption=caption)
            elif ext in (".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".wav", ".aac"):
                media = InputMediaAudio(ruta, caption=caption)
            else:
                media = InputMediaDocument(ruta, caption=caption)
            return await client.edit_message_media(chat_id, message_id, media)
        msg = self._run(_coro(), timeout=900)
        self._guardar_mensaje(account_id, msg)
        return msg

    def borrar_mensaje(self, account_id: int, chat_id: int, message_ids, revoke: bool = True):
        """Borra uno o varios mensajes. revoke=True borra para todos cuando es posible."""
        if isinstance(message_ids, int):
            message_ids = [message_ids]

        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_messages(chat_id, message_ids, revoke=revoke)
        res = self._run(_coro())
        for mid in message_ids:
            db.execute("DELETE FROM telegram_messages WHERE id = ?", (f"{account_id}:{chat_id}:{mid}",))
        return res

    def reenviar_mensaje(self, account_id: int, from_chat_id: int, message_ids, to_chat_id: int):
        """Reenvía mensajes de un chat a otro."""
        if isinstance(message_ids, int):
            message_ids = [message_ids]

        async def _coro():
            client = await self._cliente(account_id)
            return await client.forward_messages(to_chat_id, from_chat_id, message_ids)
        return self._run(_coro())

    def reenviar_album(self, account_id: int, from_chat_id: int, message_id: int, to_chat_id: int):
        """Reenvía un álbum completo (todos los adjuntos del mismo envío) como álbum
        (forward_media_group). message_id: cualquiera de los mensajes del álbum."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.forward_media_group(to_chat_id, from_chat_id, message_id)
        return self._run(_coro())

    def enviar_reaccion(self, account_id: int, chat_id: int, message_id: int, emoji: str):
        """Añade (o cambia) una reacción a un mensaje. emoji vacío la quita."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_reaction(chat_id, message_id, emoji)
        return self._run(_coro())

    def quitar_reaccion_usuario(self, account_id: int, chat_id: int, message_id: int,
                                user_id: Optional[int] = None):
        """(Admin) Quita la reacción de un usuario a un mensaje del grupo. Sin user_id, la propia."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_message_reaction(chat_id, message_id, user_id=user_id)
        return self._run(_coro())

    def quitar_todas_reacciones(self, account_id: int, chat_id: int, message_id: int,
                                user_id: Optional[int] = None):
        """(Admin) Quita todas las reacciones recientes de un mensaje (o las de un usuario)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_all_message_reactions(chat_id, message_id, user_id=user_id)
        return self._run(_coro())

    def aviso_captura_pantalla(self, account_id: int, chat_id: int):
        """Avisa al otro en un chat privado de que has hecho una captura de pantalla."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_screenshot_notification(chat_id)
        return self._run(_coro())

    def fijar_mensaje(self, account_id: int, chat_id: int, message_id: int,
                      notificar: bool = True, ambos_lados: bool = False):
        """Fija un mensaje en el chat (pin_chat_message)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.pin_chat_message(
                chat_id, message_id,
                disable_notification=not notificar,
                both_sides=ambos_lados,
            )
        return self._run(_coro())

    def desfijar_mensaje(self, account_id: int, chat_id: int, message_id: int = 0):
        """Desfija un mensaje (o el último si message_id=0)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.unpin_chat_message(chat_id, message_id)
        return self._run(_coro())

    def desfijar_todos_mensajes(self, account_id: int, chat_id: int):
        """Desfija todos los mensajes fijados del chat."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.unpin_all_chat_messages(chat_id)
        return self._run(_coro())

    def copiar_mensaje(self, account_id: int, from_chat_id: int, message_id: int,
                       to_chat_id: int, caption: Optional[str] = None):
        """Copia un mensaje a otro chat SIN la etiqueta 'reenviado de' (copy_message)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.copy_message(to_chat_id, from_chat_id, message_id, caption=caption)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def marcar_menciones_leidas(self, account_id: int, chat_id: int):
        """Marca como leídas las menciones pendientes del chat (read_mentions)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.read_mentions(chat_id)
        return self._run(_coro())

    def marcar_reacciones_leidas(self, account_id: int, chat_id: int):
        """Marca como leídas las reacciones nuevas del chat (read_reactions)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.read_reactions(chat_id)
        return self._run(_coro())

    def enviar_accion_chat(self, account_id: int, chat_id: int, accion: str = "typing"):
        """Envía el indicador de actividad ('escribiendo…', 'grabando…') a un chat.
        accion: typing, upload_photo, record_video, upload_video, record_voice,
        upload_voice, upload_document, choose_sticker, cancel… (send_chat_action)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import enums
            try:
                act = enums.ChatAction[accion.upper()]
            except (KeyError, AttributeError):
                act = enums.ChatAction.TYPING
            return await client.send_chat_action(chat_id, act)
        return self._run(_coro())

    # --- MULTIMEDIA ---
    @staticmethod
    def _sanear_nombre(nombre: str) -> str:
        """Convierte un texto en un nombre de carpeta válido en Windows."""
        import re
        nombre = (nombre or "").strip()
        if not nombre:
            return "sin_nombre"
        # Caracteres prohibidos en rutas de Windows
        nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nombre)
        nombre = nombre.rstrip(". ")  # Windows no admite punto/espacio final
        return (nombre[:60] or "sin_nombre")

    @staticmethod
    def _tipo_media(mensaje) -> str:
        """Devuelve una carpeta de categoría según el tipo de adjunto."""
        for attr, carpeta in (
            ("photo", "imagenes"), ("video", "videos"), ("animation", "gifs"),
            ("voice", "notas_de_voz"), ("audio", "audios"),
            ("video_note", "videomensajes"), ("sticker", "stickers"),
            ("document", "documentos"),
        ):
            if getattr(mensaje, attr, None):
                return carpeta
        return "otros"

    def _download_dir(self, subcarpetas=None):
        """Directorio base de descargas. subcarpetas: lista opcional para anidar
        (p. ej. [nombre_chat, tipo_media]).

        Con la carpeta por defecto (data/downloads) las descargas se organizan POR RED:
        downloads/telegram/<chat>/<tipo> (igual que downloads/matrix/<chat>). Si el usuario
        configuró su propia ruta en Preferencias, se respeta esa ruta tal cual como base."""
        from app.core.config_manager import config
        from pathlib import Path
        ruta = config.get("telegram", "download_dir", "")
        p = Path(ruta) if ruta else (get_data_path("downloads") / "telegram")
        if config.get("telegram", "download_subdirs", True) and subcarpetas:
            for parte in subcarpetas:
                if parte:
                    p = p / self._sanear_nombre(parte)
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def descargar_media(self, account_id: int, chat_id: int, message_id: int,
                        progreso: Optional[Callable[[int, int], None]] = None) -> Optional[str]:
        """Descarga el archivo multimedia de un mensaje. Devuelve la ruta local."""
        async def _coro():
            client = await self._cliente(account_id)
            mensaje = await client.get_messages(chat_id, message_id)
            if not mensaje or not mensaje.media:
                return None

            async def _prog(current, total):
                if progreso:
                    import wx
                    wx.CallAfter(progreso, current, total)

            # Organiza en subcarpetas: <chat>/<tipo>
            nombre_chat = ""
            try:
                ch = getattr(mensaje, "chat", None)
                nombre_chat = (getattr(ch, "title", None)
                               or getattr(ch, "first_name", None)
                               or (f"chat_{chat_id}"))
            except Exception:
                nombre_chat = f"chat_{chat_id}"
            destino = self._download_dir([nombre_chat, self._tipo_media(mensaje)])
            ruta = await client.download_media(mensaje, file_name=destino + "/", progress=_prog)
            return ruta

        ruta = self._run(_coro(), timeout=600)
        if ruta:
            db.execute(
                "UPDATE telegram_messages SET media_path = ? WHERE id = ?",
                (str(ruta), f"{account_id}:{chat_id}:{message_id}")
            )
        return str(ruta) if ruta else None

    # --- STREAMING (reproducir sin descargar el archivo entero) ---
    _STREAM_CHUNK = 1024 * 1024  # pyrogram/kurigram entrega el media en trozos de 1 MB

    def preparar_stream(self, account_id: int, chat_id: int, message_id: int):
        """Devuelve (tamaño_en_bytes, mime) del media de un mensaje, para el servidor de streaming.
        Devuelve (0, '') si el mensaje no tiene un adjunto descargable."""
        async def _c():
            client = await self._cliente(account_id)
            msg = await client.get_messages(chat_id, message_id)
            media = None
            for attr in ("voice", "audio", "video", "video_note", "document", "animation"):
                media = getattr(msg, attr, None)
                if media is not None:
                    break
            if media is None:
                return 0, ""
            return int(getattr(media, "file_size", 0) or 0), (getattr(media, "mime_type", "") or "")
        return self._run(_c(), timeout=30)

    def leer_chunks_stream(self, account_id: int, chat_id: int, message_id: int, offset_chunk: int = 0):
        """Generador SÍNCRONO de bytes del media (para el hilo del servidor HTTP). Puentea el
        `stream_media` asíncrono de kurigram: cada trozo se pide al bucle de Telegram. offset_chunk =
        índice del trozo de 1 MB por el que empezar (para saltar al hacer seek)."""
        import asyncio
        self._ensure_loop()

        async def _prep():
            client = await self._cliente(account_id)
            msg = await client.get_messages(chat_id, message_id)
            return client, msg

        client, msg = asyncio.run_coroutine_threadsafe(_prep(), self._loop).result(30)
        agen = client.stream_media(msg, offset=int(offset_chunk or 0))
        try:
            while True:
                try:
                    chunk = asyncio.run_coroutine_threadsafe(agen.__anext__(), self._loop).result(60)
                except StopAsyncIteration:
                    break
                if chunk:
                    yield chunk
        finally:
            # Cerrar el generador asíncrono en su bucle para no dejar la descarga colgando.
            try:
                asyncio.run_coroutine_threadsafe(agen.aclose(), self._loop).result(5)
            except Exception:
                pass

    def url_streaming(self, account_id: int, chat_id: int, message_id: int) -> str:
        """URL local (http://127.0.0.1:...) para reproducir el adjunto EN STREAMING, sin descargarlo
        entero. El reproductor la abre como cualquier URL de radio/podcast."""
        from app.services.telegram.telegram_stream_server import stream_server
        return stream_server.url_para(account_id, chat_id, message_id)

    def ruta_descarga_valida(self, account_id: int, chat_id: int, message_id: int):
        """Devuelve la ruta local del adjunto SOLO si sigue descargado (existe en disco). Si en la
        caché consta una ruta pero el archivo ya NO está (lo borró el usuario), limpia esa ruta en
        la base de datos y devuelve None. Así el estado de descarga es siempre real y dinámico."""
        import os
        fila = db.query_one(
            "SELECT media_path FROM telegram_messages WHERE id = ?",
            (f"{account_id}:{chat_id}:{message_id}",))
        ruta = fila[0] if fila and fila[0] else None
        if ruta and os.path.exists(ruta):
            return ruta
        if ruta:
            # Constaba descargado pero el archivo ya no existe: limpiar para reflejar la realidad.
            db.execute("UPDATE telegram_messages SET media_path = NULL WHERE id = ?",
                       (f"{account_id}:{chat_id}:{message_id}",))
        return None

    def borrar_descarga_local(self, account_id: int, chat_id: int, message_id: int) -> bool:
        """Borra el archivo descargado del disco (si existe) y limpia su ruta en la caché, para que
        el adjunto vuelva a constar como NO descargado. No toca el mensaje en Telegram."""
        import os
        fila = db.query_one(
            "SELECT media_path FROM telegram_messages WHERE id = ?",
            (f"{account_id}:{chat_id}:{message_id}",))
        ruta = fila[0] if fila and fila[0] else None
        borrado = False
        if ruta and os.path.exists(ruta):
            try:
                os.remove(ruta)
                borrado = True
            except OSError as e:
                log.debug(f"No se pudo borrar la descarga {ruta}: {e}")
                raise TelegramError(f"No se pudo borrar el archivo: {e}")
        db.execute("UPDATE telegram_messages SET media_path = NULL WHERE id = ?",
                   (f"{account_id}:{chat_id}:{message_id}",))
        return borrado

    # Categorías de adjunto para la descarga masiva (atributo del mensaje -> nada,
    # solo se usa el nombre del atributo como clave de filtro).
    _CATEGORIAS_MEDIA = ("photo", "video", "animation", "voice", "audio", "video_note", "document")

    def descargar_chat_masivo(self, account_id: int, chat_id: int, tipos=None,
                              limite: int = 0,
                              progreso: Optional[Callable[[int, int], None]] = None) -> dict:
        """Descarga en lote el multimedia de un chat.

        tipos: conjunto/lista de categorías a incluir (de _CATEGORIAS_MEDIA) o None para todas.
        limite: número máximo de mensajes a recorrer (0 = todo el historial).
        progreso(descargados, total): se llama en el hilo de la interfaz (wx.CallAfter).
        Devuelve {descargados, total, carpeta}.
        """
        filtro = set(tipos) if tipos else None

        async def _coro():
            client = await self._cliente(account_id)
            try:
                ch = await client.get_chat(chat_id)
                nombre_chat = (getattr(ch, "title", None) or getattr(ch, "first_name", None)
                               or f"chat_{chat_id}")
            except Exception:
                nombre_chat = f"chat_{chat_id}"

            # 1ª pasada: localizar los mensajes con adjunto que cumplen el filtro.
            candidatos = []
            async for m in client.get_chat_history(chat_id, limit=int(limite) or 0):
                attrs = [a for a in self._CATEGORIAS_MEDIA if getattr(m, a, None)]
                if not attrs:
                    continue
                if filtro and not (set(attrs) & filtro):
                    continue
                candidatos.append(m)

            total = len(candidatos)
            if progreso:
                import wx
                wx.CallAfter(progreso, 0, total)
            descargados = 0
            for i, m in enumerate(candidatos, 1):
                destino = self._download_dir([nombre_chat, self._tipo_media(m)])
                try:
                    ruta = await client.download_media(m, file_name=destino + "/")
                    if ruta:
                        descargados += 1
                        db.execute(
                            "UPDATE telegram_messages SET media_path = ? WHERE id = ?",
                            (str(ruta), f"{account_id}:{chat_id}:{getattr(m, 'id', 0)}"))
                except Exception as e:
                    log.debug(f"Descarga masiva: falló el mensaje {getattr(m, 'id', None)}: {e}")
                if progreso:
                    import wx
                    wx.CallAfter(progreso, i, total)
            return {"descargados": descargados, "total": total,
                    "carpeta": self._download_dir([nombre_chat])}

        return self._run(_coro(), timeout=3600)

    @staticmethod
    def tipo_por_extension(ruta: str) -> str:
        """Deduce el tipo de envío (photo/video/voice/audio/document) por la extensión."""
        import os
        ext = os.path.splitext(ruta)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            return "photo"
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            return "video"
        if ext in (".ogg", ".oga"):
            return "voice"
        if ext in (".mp3", ".m4a", ".flac", ".wav"):
            return "audio"
        return "document"

    def enviar_archivo(self, account_id: int, chat_id: int, ruta: str, caption: str = "",
                       tipo: str = "auto", reply_to: Optional[int] = None, schedule_date=None,
                       progress_cb=None):
        """
        Envía un archivo. tipo: auto, photo, video, audio, voice, document.
        En 'auto' se decide por la extensión. schedule_date (datetime) lo programa.
        progress_cb(current, total): callback opcional de progreso de subida (se llama desde el
        bucle de Telegram; debe ser rápido y no bloquear —típicamente hace wx.CallAfter—).
        """
        if tipo == "auto":
            tipo = self.tipo_por_extension(ruta)

        async def _coro():
            client = await self._cliente(account_id)
            kw = {"caption": caption, "reply_to_message_id": reply_to, "schedule_date": schedule_date}
            if progress_cb is not None:
                kw["progress"] = progress_cb
            if tipo == "photo":
                return await client.send_photo(chat_id, ruta, **kw)
            if tipo == "video":
                return await client.send_video(chat_id, ruta, **kw)
            if tipo == "voice":
                return await client.send_voice(chat_id, ruta, **kw)
            if tipo == "audio":
                return await client.send_audio(chat_id, ruta, **kw)
            return await client.send_document(chat_id, ruta, **kw)

        msg = self._run(_coro(), timeout=600)
        if not schedule_date:
            self._guardar_mensaje(account_id, msg)
        return msg

    def enviar_album(self, account_id: int, chat_id: int, rutas: list, caption: str = ""):
        """Envía varias imágenes/vídeos como un álbum (grupo de medios)."""
        import os
        from pyrogram.types import InputMediaPhoto, InputMediaVideo
        media = []
        for i, ruta in enumerate(rutas):
            ext = os.path.splitext(ruta)[1].lower()
            cap = caption if i == 0 else ""
            if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
                media.append(InputMediaVideo(ruta, caption=cap))
            else:
                media.append(InputMediaPhoto(ruta, caption=cap))

        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_media_group(chat_id, media)

        msgs = self._run(_coro(), timeout=900)
        with db.transaccion():
            for m in (msgs or []):
                self._guardar_mensaje(account_id, m)
        return msgs

    def enviar_sticker(self, account_id: int, chat_id: int, sticker: str):
        """Envía un sticker (por file_id o ruta a un .webp/.tgs)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_sticker(chat_id, sticker)
        msg = self._run(_coro(), timeout=120)
        self._guardar_mensaje(account_id, msg)
        return msg

    # --- LISTAS DE TAREAS (CHECKLISTS) ---
    def enviar_checklist(self, account_id: int, chat_id: int, titulo: str, tareas: list,
                         otros_pueden_anadir: bool = False, otros_pueden_marcar: bool = True):
        """Envía una lista de tareas (checklist). tareas: lista de textos."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram.types import InputChecklist, InputChecklistTask
            items = [InputChecklistTask(id=i + 1, text=t) for i, t in enumerate(tareas) if (t or "").strip()]
            checklist = InputChecklist(
                title=titulo, tasks=items,
                others_can_add_tasks=otros_pueden_anadir,
                others_can_mark_tasks_as_done=otros_pueden_marcar,
            )
            return await client.send_checklist(chat_id, checklist)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def marcar_tareas_checklist(self, account_id: int, chat_id: int, message_id: int,
                                hechas: Optional[list] = None, no_hechas: Optional[list] = None):
        """Marca tareas de una lista como hechas/no hechas (mark_checklist_tasks_as_done). Ids de tarea."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.mark_checklist_tasks_as_done(
                chat_id, message_id,
                marked_as_done_task_ids=hechas or None,
                marked_as_not_done_task_ids=no_hechas or None)
        return self._run(_coro())

    def anadir_tareas_checklist(self, account_id: int, chat_id: int, message_id: int, textos: list,
                                desde_id: int = 1000):
        """Añade tareas nuevas a una lista existente (add_checklist_tasks)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram.types import InputChecklistTask
            tareas = [InputChecklistTask(id=desde_id + i, text=t) for i, t in enumerate(textos) if (t or "").strip()]
            return await client.add_checklist_tasks(chat_id, message_id, tareas)
        return self._run(_coro())

    def leer_checklist(self, account_id: int, chat_id: int, message_id: int) -> Optional[dict]:
        """Lee la lista de tareas de un mensaje. Devuelve {titulo, tareas:[(id, texto, hecha)]} o None."""
        async def _coro():
            client = await self._cliente(account_id)
            msg = await client.get_messages(chat_id, message_id)
            chk = getattr(msg, "checklist", None)
            if not chk:
                return None
            tareas = []
            for t in (getattr(chk, "tasks", None) or []):
                tareas.append((getattr(t, "id", 0), getattr(t, "text", "") or "",
                               bool(getattr(t, "completed_by_user", None) or getattr(t, "completed", None))))
            return {"titulo": getattr(chk, "title", "") or "", "tareas": tareas}
        return self._run(_coro())

    # --- GIF GUARDADOS Y MEDIA CACHEADA ---
    def file_id_de_mensaje(self, account_id: int, chat_id: int, message_id: int) -> Optional[str]:
        """Devuelve el file_id del adjunto de un mensaje (para guardarlo como GIF o reenviarlo)."""
        async def _coro():
            client = await self._cliente(account_id)
            msg = await client.get_messages(chat_id, message_id)
            for attr in ("animation", "video", "photo", "document", "audio", "voice", "sticker"):
                media = getattr(msg, attr, None)
                if media is not None:
                    return getattr(media, "file_id", None)
            return None
        return self._run(_coro())

    def guardar_gif(self, account_id: int, file_id: str, quitar: bool = False):
        """Guarda (o quita) un GIF en tus 'GIF guardados' (add_to_gifs)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.add_to_gifs(file_id, unsave=quitar)
        return self._run(_coro())

    def enviar_media_cacheada(self, account_id: int, chat_id: int, file_id: str, caption: str = ""):
        """Envía un archivo ya conocido por su file_id, sin volver a subirlo (send_cached_media)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_cached_media(chat_id, file_id, caption=caption)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    # --- ENCUESTAS ---
    def crear_encuesta(self, account_id: int, chat_id: int, pregunta: str, opciones: list,
                       anonima: bool = True, multiple: bool = False, quiz: bool = False,
                       correcta: Optional[int] = None, explicacion: Optional[str] = None):
        """Crea una encuesta. quiz=True la hace tipo concurso (con respuesta correcta)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import enums
            kw = {
                "is_anonymous": anonima,
                "type": enums.PollType.QUIZ if quiz else enums.PollType.REGULAR,
            }
            if multiple and not quiz:
                kw["allows_multiple_answers"] = True
            if quiz and correcta is not None:
                kw["correct_option_ids"] = [correcta]
                if explicacion:
                    kw["explanation"] = explicacion
            return await client.send_poll(chat_id, pregunta, list(opciones), **kw)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def votar_encuesta(self, account_id: int, chat_id: int, message_id: int, opciones):
        """Vota en una encuesta. opciones: índice (int) o lista de índices."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.vote_poll(chat_id, message_id, opciones)
        return self._run(_coro())

    def cerrar_encuesta(self, account_id: int, chat_id: int, message_id: int):
        """Cierra una encuesta propia para que no admita más votos (stop_poll)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.stop_poll(chat_id, message_id)
        return self._run(_coro())

    def retirar_voto_encuesta(self, account_id: int, chat_id: int, message_id: int):
        """Retira tu voto de una encuesta (retract_vote)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.retract_vote(chat_id, message_id)
        return self._run(_coro())

    def anadir_opcion_encuesta(self, account_id: int, chat_id: int, message_id: int, opcion: str):
        """Añade una opción a una encuesta propia (add_poll_option)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.add_poll_option(chat_id, message_id, opcion)
        return self._run(_coro())

    def quitar_opcion_encuesta(self, account_id: int, chat_id: int, message_id: int, opcion: str):
        """Quita una opción de una encuesta propia (delete_poll_option)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_poll_option(chat_id, message_id, opcion)
        return self._run(_coro())

    def obtener_encuesta(self, account_id: int, chat_id: int, message_id: int) -> Optional[dict]:
        """Devuelve los datos de la encuesta de un mensaje, o None si no la tiene."""
        async def _coro():
            client = await self._cliente(account_id)
            msg = await client.get_messages(chat_id, message_id)
            poll = getattr(msg, "poll", None) if msg else None
            if not poll:
                return None
            def _texto(v):
                # En kurigram reciente, la pregunta y las opciones pueden ser objetos con .text
                # (para admitir emoji/entidades) en vez de cadenas. Normalizar SIEMPRE a str.
                return str(getattr(v, "text", v) or "")
            opciones = []
            for i, o in enumerate(poll.options or []):
                opciones.append({
                    "indice": i,
                    "texto": _texto(getattr(o, "text", "")),
                    "votos": getattr(o, "voter_count", 0) or 0,
                    "porcentaje": getattr(o, "vote_percentage", 0) or 0,
                })
            return {
                "pregunta": _texto(getattr(poll, "question", "")),
                "opciones": opciones,
                "total": getattr(poll, "total_voter_count", 0) or 0,
                "cerrada": bool(getattr(poll, "is_closed", False)),
                "multiple": bool(getattr(poll, "allows_multiple_answers", False)),
                "elegidas": list(getattr(poll, "chosen_option_ids", None) or []),
            }
        return self._run(_coro())

    def quien_reacciono(self, account_id: int, chat_id: int, message_id: int, limit: int = 100) -> list:
        """Devuelve quién ha reaccionado a un mensaje y con qué emoji: [{nombre, emoji}].
        Usa la API cruda GetMessageReactionsList (kurigram no tiene envoltorio de alto nivel). Es una
        consulta BAJO DEMANDA (al abrir el diálogo), no afecta al rendimiento general."""
        from pyrogram.raw.functions.messages import GetMessageReactionsList

        async def _coro():
            client = await self._cliente(account_id)
            peer = await client.resolve_peer(chat_id)
            r = await client.invoke(GetMessageReactionsList(peer=peer, id=message_id, limit=limit))
            # Mapa id -> nombre legible, resolviendo usuarios y canales del resultado.
            nombres = {}
            for u in getattr(r, "users", None) or []:
                nom = " ".join(p for p in (getattr(u, "first_name", None), getattr(u, "last_name", None)) if p).strip()
                nombres[u.id] = nom or (getattr(u, "username", None) and f"@{u.username}") or str(u.id)
            for c in getattr(r, "chats", None) or []:
                nombres[c.id] = getattr(c, "title", None) or str(c.id)
            salida = []
            for pr in getattr(r, "reactions", None) or []:
                pid = getattr(pr, "peer_id", None)
                uid = (getattr(pid, "user_id", None) or getattr(pid, "channel_id", None)
                       or getattr(pid, "chat_id", None))
                reac = getattr(pr, "reaction", None)
                emoji = getattr(reac, "emoticon", None) or ("⭐" if getattr(reac, "document_id", None) else "?")
                salida.append({"nombre": nombres.get(uid, str(uid)), "emoji": emoji})
            return salida
        return self._run(_coro(), timeout=30)

    # --- DETALLES DE UN MENSAJE (diálogo "Ver mensaje") ---
    @staticmethod
    def _tamano_legible(num) -> str:
        """Convierte un número de bytes en un texto legible (KB, MB, GB)."""
        try:
            n = float(num or 0)
        except (TypeError, ValueError):
            return ""
        if n <= 0:
            return ""
        for unidad in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unidad == "TB":
                return (f"{int(n)} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}")
            n /= 1024
        return ""

    def _info_media(self, mensaje) -> dict:
        """Extrae los datos del adjunto de un mensaje: tipo, nombre, tamaño, duración,
        dimensiones y mime. Devuelve {} si el mensaje no tiene multimedia."""
        if not getattr(mensaje, "media", None):
            return {}
        try:
            media_type = mensaje.media.value if hasattr(mensaje.media, "value") else str(mensaje.media)
        except Exception:
            media_type = "media"
        # web_page (vista previa de enlace), encuesta, ubicación, contacto… NO son archivos: no cuentan
        # como adjunto (sus enlaces se ofrecen aparte con "Abrir enlace" y su naturaleza se indica en
        # los detalles del mensaje).
        if media_type not in _MEDIA_ARCHIVO:
            return {}
        info = {"media_type": media_type, "file_name": "", "size": 0,
                "duration": 0, "width": 0, "height": 0, "mime": ""}
        for attr in ("photo", "video", "animation", "voice", "audio", "video_note",
                     "sticker", "document"):
            obj = getattr(mensaje, attr, None)
            if obj is None:
                continue
            info["file_name"] = getattr(obj, "file_name", "") or ""
            info["size"] = getattr(obj, "file_size", 0) or 0
            info["duration"] = getattr(obj, "duration", 0) or 0
            info["width"] = getattr(obj, "width", 0) or 0
            info["height"] = getattr(obj, "height", 0) or 0
            info["mime"] = getattr(obj, "mime_type", "") or ""
            break
        return info

    def obtener_detalles_mensaje(self, account_id: int, chat_id: int, message_id: int) -> dict:
        """Devuelve TODOS los datos obtenibles de un mensaje (para el diálogo Ver mensaje):
        autor, fecha, texto, reenvío, vistas, reacciones, respuesta-a, edición, adjunto, etc."""
        async def _coro():
            client = await self._cliente(account_id)
            m = await client.get_messages(chat_id, message_id)
            if not m:
                return None

            # Autor (usuario o canal que firma)
            autor = ""
            autor_usuario = ""
            autor_id = None
            if getattr(m, "from_user", None):
                u = m.from_user
                autor_id = u.id
                autor = " ".join(p for p in (u.first_name, u.last_name) if p).strip()
                if u.username:
                    autor_usuario = f"@{u.username}"
                autor = autor or autor_usuario or str(u.id)
            elif getattr(m, "sender_chat", None):
                sc = m.sender_chat
                autor_id = sc.id
                autor = getattr(sc, "title", "") or ""
                if getattr(sc, "username", None):
                    autor_usuario = f"@{sc.username}"

            # Reenviado de…
            reenviado = ""
            ff = getattr(m, "forward_from", None)
            if ff is not None:
                reenviado = " ".join(p for p in (getattr(ff, "first_name", None),
                                                 getattr(ff, "last_name", None)) if p).strip() \
                    or (ff.username and f"@{ff.username}") or str(ff.id)
            elif getattr(m, "forward_from_chat", None) is not None:
                reenviado = getattr(m.forward_from_chat, "title", "") or ""
            elif getattr(m, "forward_sender_name", None):
                reenviado = m.forward_sender_name

            # Reacciones
            reacciones = []
            reacts = getattr(m, "reactions", None)
            if reacts is not None:
                for r in (getattr(reacts, "reactions", None) or []):
                    emoji = getattr(r, "emoji", None) or getattr(r, "custom_emoji_id", "") or "?"
                    reacciones.append({"emoji": str(emoji), "count": getattr(r, "count", 0) or 0,
                                       "chosen": bool(getattr(r, "chosen", False) or getattr(r, "is_chosen", False))})

            tipo_chat = ""
            ch = getattr(m, "chat", None)
            if ch is not None and getattr(ch, "type", None) is not None:
                tipo_chat = ch.type.value if hasattr(ch.type, "value") else str(ch.type)

            return {
                "message_id": m.id,
                "fecha": self._fecha_iso(getattr(m, "date", None)),
                "autor": autor, "autor_usuario": autor_usuario, "autor_id": autor_id,
                "outgoing": bool(getattr(m, "outgoing", False)),
                "tipo_chat": tipo_chat,
                "texto": str(m.text or m.caption or ""),
                "reenviado": reenviado,
                "fecha_reenvio": self._fecha_iso(getattr(m, "forward_date", None)),
                "vistas": getattr(m, "views", 0) or 0,
                "reenvios": getattr(m, "forwards", 0) or 0,
                "reacciones": reacciones,
                "reply_to": getattr(m, "reply_to_message_id", None),
                "editado": self._fecha_iso(getattr(m, "edit_date", None)),
                "via_bot": (getattr(getattr(m, "via_bot", None), "username", "") and
                            f"@{m.via_bot.username}") or "",
                "firma": getattr(m, "author_signature", "") or "",
                "fijado": bool(getattr(m, "pinned", False)),
                "media": self._info_media(m),
                "media_group_id": getattr(m, "media_group_id", None),
                "es_encuesta": getattr(m, "poll", None) is not None,
                "es_ubicacion": getattr(m, "location", None) is not None or getattr(m, "venue", None) is not None,
                "es_contacto": getattr(m, "contact", None) is not None,
                "es_web_page": getattr(m, "web_page", None) is not None,
                "urls": self._extraer_urls_list(m),
            }
        try:
            res = self._run(_coro(), timeout=60)
            if res:
                return res
        except Exception as e:
            log.debug(f"obtener_detalles_mensaje: la consulta en vivo falló: {e}")
        # Respaldo: construir los detalles a partir de la fila cacheada para que el diálogo
        # sea útil aunque la consulta en vivo falle (sin conexión, flood, etc.).
        return self._detalles_desde_cache(account_id, chat_id, message_id)

    def _detalles_desde_cache(self, account_id: int, chat_id: int, message_id: int) -> dict:
        """Detalles mínimos de un mensaje a partir de la caché (respaldo de obtener_detalles_mensaje)."""
        r = db.query_one("""
            SELECT message_id, sender_name, sender_username, sender_id, text, date, outgoing,
                   media_type, file_name, duration, COALESCE(edited, 0), reply_to,
                   COALESCE(forward_from, ''), COALESCE(views, 0), media_group_id, COALESCE(urls, '')
            FROM telegram_messages WHERE id = ?
        """, (f"{account_id}:{chat_id}:{message_id}",))
        if not r:
            return {}
        # Solo es "adjunto" si es un archivo real (no una vista previa web/encuesta/ubicación…).
        media = {}
        if r[7] and r[7] in _MEDIA_ARCHIVO:
            media = {"media_type": r[7], "file_name": r[8] or "", "size": 0,
                     "duration": r[9] or 0, "width": 0, "height": 0, "mime": ""}
        urls = []
        if r[15]:
            try:
                import json
                urls = [str(u) for u in (json.loads(r[15]) or []) if u]
            except Exception:
                urls = []
        return {
            "message_id": r[0], "autor": r[1] or "", "autor_usuario": r[2] or "",
            "autor_id": r[3], "texto": r[4] or "", "fecha": r[5] or "",
            "outgoing": bool(r[6]), "tipo_chat": "", "reenviado": r[12] or "",
            "fecha_reenvio": "", "vistas": r[13] or 0, "reenvios": 0, "reacciones": [],
            "reply_to": r[11], "editado": "", "via_bot": "", "firma": "", "fijado": False,
            "media": media, "media_group_id": r[14],
            "es_encuesta": False, "es_ubicacion": False, "es_contacto": False,
            "es_web_page": (r[7] == "web_page"), "urls": urls,
            "_editado_flag": bool(r[10]),
        }

    @staticmethod
    def _item_media_db(r) -> dict:
        """Construye el dict de un adjunto a partir de una fila (message_id, media_type,
        file_name, duration) de la caché."""
        return {"message_id": r[0], "media_type": r[1] or "", "file_name": r[2] or "",
                "size": 0, "duration": r[3] or 0, "width": 0, "height": 0, "mime": ""}

    def _grupo_desde_cache(self, account_id: int, chat_id: int, message_id: int) -> list:
        """Reconstruye los adjuntos del envío SOLO desde la caché. Si el mensaje pertenece a un
        álbum (media_group_id), devuelve TODOS sus adjuntos; si no, su único adjunto. Incluye
        CUALQUIER tipo de multimedia que Telegram guarde (foto, vídeo, audio, voz, documento,
        GIF, sticker, videomensaje…)."""
        clave = f"{account_id}:{chat_id}:{message_id}"
        fila = db.query_one("SELECT media_group_id FROM telegram_messages WHERE id = ?", (clave,))
        gid = fila[0] if fila and fila[0] is not None else None
        if gid is not None:
            filas = db.query(
                "SELECT message_id, media_type, file_name, duration FROM telegram_messages "
                "WHERE account_id = ? AND chat_id = ? AND media_group_id = ? ORDER BY message_id",
                (account_id, chat_id, gid))
        else:
            filas = db.query(
                "SELECT message_id, media_type, file_name, duration FROM telegram_messages WHERE id = ?",
                (clave,))
        return [self._item_media_db(r) for r in (filas or []) if r[1] in _MEDIA_ARCHIVO]

    def obtener_grupo_medios(self, account_id: int, chat_id: int, message_id: int) -> list:
        """Devuelve la lista de adjuntos del envío al que pertenece el mensaje. Si el mensaje
        forma parte de un álbum (grupo de medios), devuelve TODOS sus adjuntos; si no, una lista
        con su único adjunto. Vale para CUALQUIER tipo que soporte Telegram (foto, vídeo, audio,
        voz, documento, GIF, sticker, videomensaje). Cada elemento:
        {message_id, media_type, file_name, size, duration, width, height, mime}.

        La CACHÉ garantiza que los adjuntos siempre aparezcan (no depende de la red). La consulta
        en vivo solo se usa para ENRIQUECER (tamaños, dimensiones) o COMPLETAR un álbum que aún no
        estuviera entero en la caché; si falla, se devuelve lo de la caché."""
        grupo_db = self._grupo_desde_cache(account_id, chat_id, message_id)

        async def _coro():
            client = await self._cliente(account_id)
            try:
                # Lanza ValueError si el mensaje no forma parte de un álbum.
                grupo = await client.get_media_group(chat_id, message_id)
            except Exception:
                grupo = None
            if not grupo:
                m = await client.get_messages(chat_id, message_id)
                grupo = [m] if m else []
            salida = []
            for m in grupo:
                if not getattr(m, "media", None):
                    continue
                info = self._info_media(m)
                # _info_media devuelve {} cuando NO es un archivo real (vista previa web, encuesta,
                # ubicación, contacto…): esos no son adjuntos descargables, así que se omiten (si no,
                # aparecía una fila de adjunto vacía y sin nada que descargar).
                if not info:
                    continue
                info["message_id"] = m.id
                salida.append(info)
            return salida

        grupo_live = None
        try:
            grupo_live = self._run(_coro(), timeout=120)
        except Exception as e:
            log.debug(f"obtener_grupo_medios: la consulta en vivo falló: {e}")

        # Se queda con la fuente que tenga MÁS adjuntos; en empate prefiere la viva (trae tamaños
        # y dimensiones). Así nunca se pierde ningún archivo ni queda vacío.
        if grupo_live and len(grupo_live) >= len(grupo_db):
            return grupo_live
        return grupo_db

    def obtener_respuestas(self, account_id: int, chat_id: int, message_id: int) -> list:
        """Devuelve las respuestas en caché a un mensaje (mensajes cuyo reply_to apunta a él)."""
        filas = db.query("""
            SELECT message_id, sender_name, sender_username, text, date, outgoing, media_type,
                   media_path, duration, file_name, COALESCE(edited, 0), reply_to,
                   COALESCE(forward_from, ''), COALESCE(views, 0), COALESCE(reply_markup, ''),
                   media_group_id, COALESCE(reactions, ''), COALESCE(urls, '')
            FROM telegram_messages
            WHERE account_id = ? AND chat_id = ? AND reply_to = ?
            ORDER BY message_id ASC
        """, (account_id, chat_id, message_id))
        return filas or []

    # --- TIPOS DE MENSAJE ADICIONALES ---
    def enviar_ubicacion(self, account_id: int, chat_id: int, latitud: float, longitud: float):
        """Envía una ubicación (punto en el mapa)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_location(chat_id, latitud, longitud)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def enviar_lugar(self, account_id: int, chat_id: int, latitud: float, longitud: float,
                     titulo: str, direccion: str = ""):
        """Envía un lugar con nombre y dirección (send_venue)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_venue(chat_id, latitud, longitud, titulo, direccion or titulo)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def enviar_contacto(self, account_id: int, chat_id: int, telefono: str,
                        nombre: str, apellido: str = ""):
        """Comparte un contacto telefónico (send_contact)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_contact(chat_id, telefono, nombre, last_name=apellido or None)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    def enviar_videonota(self, account_id: int, chat_id: int, ruta: str):
        """Envía un videomensaje redondo (send_video_note)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_video_note(chat_id, ruta)
        msg = self._run(_coro(), timeout=600)
        self._guardar_mensaje(account_id, msg)
        return msg

    def enviar_gif(self, account_id: int, chat_id: int, ruta: str, caption: str = ""):
        """Envía un GIF animado (send_animation)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_animation(chat_id, ruta, caption=caption)
        msg = self._run(_coro(), timeout=600)
        self._guardar_mensaje(account_id, msg)
        return msg

    def enviar_dado(self, account_id: int, chat_id: int, emoji: str = "🎲"):
        """Envía un emoji animado con valor aleatorio (🎲🎯🏀⚽🎰🎳)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_dice(chat_id, emoji)
        msg = self._run(_coro())
        self._guardar_mensaje(account_id, msg)
        return msg

    # --- MENSAJES PROGRAMADOS ---
    def obtener_programados(self, account_id: int, chat_id: int) -> list:
        """Devuelve la lista de mensajes programados de un chat (resúmenes)."""
        async def _coro():
            client = await self._cliente(account_id)
            msgs = await client.get_scheduled_messages(chat_id)
            salida = []
            for m in (msgs or []):
                salida.append({
                    "message_id": m.id,
                    "text": str(m.text or m.caption or "")[:200],
                    "date": self._fecha_iso(getattr(m, "date", None)),
                })
            return salida
        return self._run(_coro(), timeout=60)

    def cancelar_programado(self, account_id: int, chat_id: int, message_id: int):
        """Cancela (borra) un mensaje programado."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import raw
            peer = await client.resolve_peer(chat_id)
            return await client.invoke(raw.functions.messages.DeleteScheduledMessages(
                peer=peer, id=[message_id]))
        return self._run(_coro(), timeout=60)

    def enviar_programado_ahora(self, account_id: int, chat_id: int, message_id: int):
        """Envía inmediatamente un mensaje que estaba programado."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import raw
            peer = await client.resolve_peer(chat_id)
            return await client.invoke(raw.functions.messages.SendScheduledMessages(
                peer=peer, id=[message_id]))
        return self._run(_coro(), timeout=60)

    # --- MENSAJES GUARDADOS Y BORRADORES ---
    def obtener_id_guardados(self, account_id: int) -> int:
        """Devuelve el id del chat «Mensajes guardados» (tu propio chat)."""
        async def _coro():
            client = await self._cliente(account_id)
            ch = await client.get_chat("me")
            return ch.id
        return self._run(_coro(), timeout=30)

    def reenviar_a_guardados(self, account_id: int, from_chat_id: int, message_ids):
        """Reenvía uno o varios mensajes a tu chat «Mensajes guardados»."""
        if isinstance(message_ids, int):
            message_ids = [message_ids]
        async def _coro():
            client = await self._cliente(account_id)
            return await client.forward_messages("me", from_chat_id, message_ids)
        return self._run(_coro(), timeout=60)

    def guardar_borrador_servidor(self, account_id: int, chat_id: int, texto: str):
        """Guarda (o borra, si texto está vacío) el borrador del chat en el servidor de
        Telegram, para que se sincronice con los demás dispositivos."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import raw
            peer = await client.resolve_peer(chat_id)
            return await client.invoke(raw.functions.messages.SaveDraft(
                peer=peer, message=texto or "", no_webpage=False))
        return self._run(_coro(), timeout=60)

    # --- GRABACIÓN DE NOTAS DE VOZ (ffmpeg) ---
    def iniciar_grabacion_voz(self) -> str:
        """
        Inicia la grabación del micrófono (con BASS) usando el dispositivo y
        volumen configurados en Preferencias → Sonidos → Micrófono. Devuelve la ruta del WAV.
        """
        from app.services.audio import microphone
        ruta = str(get_data_path("temp") / f"voz_{int(time.time())}.wav")
        dispositivo, volumen = microphone.config_microfono()
        grabador = microphone.GrabadorBass()
        try:
            grabador.iniciar(dispositivo or None, volumen)
        except RuntimeError as e:
            raise TelegramError(str(e))
        self._grabacion = {"grabador": grabador, "ruta": ruta}
        return ruta

    def detener_grabacion_y_enviar(self, account_id: int, chat_id: int):
        """Detiene la grabación, la convierte a Opus y la envía como nota de voz.
        Si no se puede convertir a Opus (ffmpeg sin libopus), la envía como audio."""
        from app.services.audio import microphone
        grab = getattr(self, "_grabacion", None)
        if not grab:
            raise TelegramError("No hay ninguna grabación en curso.")
        ruta = grab["ruta"]
        self._grabacion = None
        try:
            grab["grabador"].detener_a_wav(ruta)
        except RuntimeError as e:
            raise TelegramError(str(e))

        # Intentar convertir a Opus para que sea una nota de voz real
        opus = microphone.convertir_a_opus(ruta)
        if opus:
            return self.enviar_archivo(account_id, chat_id, opus, tipo="voice")
        # Sin Opus disponible: enviar como audio para que al menos funcione
        log.warning("ffmpeg no pudo crear Opus; se envía la grabación como audio.")
        return self.enviar_archivo(account_id, chat_id, ruta, tipo="audio")

    def cancelar_grabacion_voz(self):
        """Cancela la grabación en curso sin enviarla."""
        grab = getattr(self, "_grabacion", None)
        if not grab:
            return
        self._grabacion = None
        try:
            grab["grabador"].cancelar()
        except Exception:
            pass

    # --- EDICIÓN DE GRUPOS/CANALES ---
