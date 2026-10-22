# -*- coding: utf-8 -*-
"""Chats, carpetas, temas, búsqueda, miembros e info.

Mixin de TelegramService (parte de la refactorización modular).
"""
from app.services.telegram.telegram_common import (
    Optional, db, log)


class _ChatsMixin:
    def editar_titulo_chat(self, account_id: int, chat_id: int, titulo: str):
        """Cambia el título de un grupo o canal."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_title(chat_id, titulo)
        return self._run(_coro())

    def editar_descripcion_chat(self, account_id: int, chat_id: int, descripcion: str):
        """Cambia la descripción de un grupo o canal."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_description(chat_id, descripcion)
        return self._run(_coro())

    # --- CARPETAS ---
    def obtener_carpetas(self, account_id: int) -> list:
        """Devuelve las carpetas de chats del usuario."""
        async def _coro():
            client = await self._cliente(account_id)
            carpetas = []
            for f in await client.get_folders():
                carpetas.append({
                    "id": getattr(f, "id", 0),
                    "title": getattr(f, "name", "") or getattr(f, "title", "") or "",
                })
            return carpetas
        return self._run(_coro(), timeout=60)

    def crear_carpeta(self, account_id: int, nombre: str, chats_incluidos: Optional[list] = None):
        """Crea una carpeta de chats con los chats indicados (create_folder)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.create_folder(nombre, included_chats=chats_incluidos or None)
        res = self._run(_coro())
        # Refrescar la tabla de carpetas que alimenta los buffers.
        try:
            self._guardar_carpetas(account_id)
        except Exception as e:
            log.debug(f"No se pudieron refrescar carpetas: {e}")
        return res

    def editar_carpeta(self, account_id: int, folder_id: int, nombre: Optional[str] = None,
                       chats_incluidos: Optional[list] = None):
        """Edita el nombre y/o los chats incluidos de una carpeta (edit_folder)."""
        async def _coro():
            client = await self._cliente(account_id)
            kw = {}
            if nombre is not None:
                kw["name"] = nombre
            if chats_incluidos is not None:
                kw["included_chats"] = chats_incluidos
            return await client.edit_folder(folder_id, **kw)
        res = self._run(_coro())
        try:
            self._guardar_carpetas(account_id)
        except Exception as e:
            log.debug(f"No se pudieron refrescar carpetas: {e}")
        return res

    def borrar_carpeta(self, account_id: int, folder_id: int):
        """Elimina una carpeta de chats (delete_folder)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_folder(folder_id)
        res = self._run(_coro())
        db.execute(
            "DELETE FROM telegram_folders WHERE account_id = ? AND folder_id = ?",
            (account_id, folder_id)
        )
        return res

    def reordenar_carpetas(self, account_id: int, folder_ids: list, pos_lista_principal: int = 0):
        """Cambia el orden de las carpetas (reorder_folders). folder_ids en el orden deseado."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.reorder_folders(list(folder_ids), main_chat_list_position=pos_lista_principal)
        res = self._run(_coro())
        try:
            self._guardar_carpetas(account_id)
        except Exception as e:
            log.debug(f"No se pudieron refrescar carpetas: {e}")
        return res

    # --- CARPETAS COMPARTIBLES (enlaces de invitación de carpeta) ---
    def chats_para_enlace_carpeta(self, account_id: int, folder_id: int) -> list:
        """Chats de una carpeta que se pueden compartir por enlace (get_chats_for_folder_invite_link).
        Devuelve [(id, nombre), ...]."""
        async def _coro():
            client = await self._cliente(account_id)
            chats = await client.get_chats_for_folder_invite_link(folder_id)
            res = []
            for c in (chats or []):
                nombre = getattr(c, "title", None) or getattr(c, "first_name", None) or str(c.id)
                res.append((c.id, nombre))
            return res
        return self._run(_coro())

    def crear_enlace_carpeta(self, account_id: int, folder_id: int, chat_ids: list,
                             nombre: Optional[str] = None) -> Optional[str]:
        """Crea un enlace de invitación para compartir una carpeta (create_folder_invite_link)."""
        async def _coro():
            client = await self._cliente(account_id)
            res = await client.create_folder_invite_link(folder_id, list(chat_ids), name=nombre)
            return getattr(res, "invite_link", None) or getattr(res, "link", None) or str(res)
        return self._run(_coro())

    def obtener_enlaces_carpeta(self, account_id: int, folder_id: int) -> list:
        """Enlaces de invitación creados para una carpeta compartible (get_folder_invite_links).
        Devuelve [(link, nombre), ...]."""
        async def _coro():
            client = await self._cliente(account_id)
            enlaces = await client.get_folder_invite_links(folder_id)
            res = []
            for e in (enlaces or []):
                link = getattr(e, "invite_link", None) or getattr(e, "link", None) or str(e)
                res.append((link, getattr(e, "name", "") or ""))
            return res
        return self._run(_coro())

    def borrar_enlace_carpeta(self, account_id: int, folder_id: int, enlace: str):
        """Borra un enlace de invitación de carpeta (delete_folder_invite_link)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_folder_invite_link(folder_id, enlace)
        return self._run(_coro())

    def unirse_a_carpeta(self, account_id: int, enlace: str):
        """Se une a una carpeta compartida por su enlace de invitación (join_folder)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.join_folder(enlace)
        res = self._run(_coro())
        try:
            self._guardar_carpetas(account_id)
        except Exception as e:
            log.debug(f"No se pudieron refrescar carpetas: {e}")
        return res

    # --- CHATS FRECUENTES Y CANALES SIMILARES ---
    def chats_frecuentes(self, account_id: int, categoria: str = "USERS", limit: int = 0) -> list:
        """Chats usados con más frecuencia, por categoría (get_top_chats).
        categoria: USERS, BOTS, GROUPS, CHANNELS, INLINE_BOTS, FORWARD_CHATS.
        Devuelve [(id, nombre, tipo), ...]."""
        from app.services.telegram.telegram_common import tg_enums
        async def _coro():
            client = await self._cliente(account_id)
            try:
                cat = tg_enums.TopChatCategory[categoria.upper()]
            except (KeyError, AttributeError):
                cat = tg_enums.TopChatCategory.USERS
            res = []
            async for c in client.get_top_chats(cat, limit=limit or 0):
                nombre = getattr(c, "title", None) or getattr(c, "first_name", None) or str(c.id)
                tipo = str(getattr(c, "type", "") or "")
                res.append((c.id, nombre, tipo))
            return res
        return self._run(_coro())

    def canales_similares(self, account_id: int, chat_id: int) -> list:
        """Canales parecidos a uno dado (get_similar_channels). Devuelve [(id, nombre, miembros), ...]."""
        async def _coro():
            client = await self._cliente(account_id)
            chats = await client.get_similar_channels(chat_id)
            res = []
            for c in (chats or []):
                nombre = getattr(c, "title", None) or str(c.id)
                miembros = getattr(c, "members_count", None) or 0
                res.append((c.id, nombre, miembros))
            return res
        return self._run(_coro())

    async def _ids_carpeta(self, client, folder_id: int):
        """Devuelve la lista de ids de chat incluidos actualmente en una carpeta."""
        for f in await client.get_folders():
            if getattr(f, "id", None) == folder_id:
                ids = []
                for c in (getattr(f, "included_chats", None) or []):
                    cid = getattr(c, "id", None)
                    if cid is not None:
                        ids.append(cid)
                return ids
        return []

    def anadir_chat_a_carpeta(self, account_id: int, folder_id: int, chat_id: int):
        """Añade un chat a una carpeta existente (conservando los que ya tenía)."""
        async def _coro():
            client = await self._cliente(account_id)
            ids = await self._ids_carpeta(client, folder_id)
            if chat_id in ids:
                return False  # ya estaba
            ids.append(chat_id)
            await client.edit_folder(folder_id, included_chats=ids)
            return True
        res = self._run(_coro())
        try:
            self._guardar_carpetas(account_id)
        except Exception as e:
            log.debug(f"No se pudieron refrescar carpetas: {e}")
        return res

    def quitar_chat_de_carpeta(self, account_id: int, folder_id: int, chat_id: int):
        """Quita un chat de una carpeta (conservando el resto)."""
        async def _coro():
            client = await self._cliente(account_id)
            ids = await self._ids_carpeta(client, folder_id)
            if chat_id not in ids:
                return False
            ids = [c for c in ids if c != chat_id]
            await client.edit_folder(folder_id, included_chats=ids)
            return True
        res = self._run(_coro())
        try:
            self._guardar_carpetas(account_id)
        except Exception as e:
            log.debug(f"No se pudieron refrescar carpetas: {e}")
        return res

    # --- TEMAS (FOROS) ---
    def obtener_temas(self, account_id: int, chat_id: int, limit: int = 100) -> list:
        """Devuelve los temas (topics) de un supergrupo tipo foro."""
        async def _coro():
            client = await self._cliente(account_id)
            temas = []
            async for t in client.get_forum_topics(chat_id, limit=limit):
                temas.append({
                    "id": getattr(t, "id", 0),
                    "title": getattr(t, "title", "") or "",
                })
            return temas
        return self._run(_coro(), timeout=90)

    def crear_tema(self, account_id: int, chat_id: int, titulo: str):
        """Crea un nuevo tema en un supergrupo tipo foro."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.create_forum_topic(chat_id, titulo)
        return self._run(_coro())

    def editar_tema(self, account_id: int, chat_id: int, topic_id: int, titulo: str):
        """Renombra un tema de foro (edit_forum_topic)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.edit_forum_topic(chat_id, topic_id, title=titulo)
        return self._run(_coro())

    def cerrar_tema(self, account_id: int, chat_id: int, topic_id: int, cerrar: bool = True):
        """Cierra o reabre un tema de foro (edit_forum_topic closed=)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.edit_forum_topic(chat_id, topic_id, closed=cerrar)
        return self._run(_coro())

    def borrar_tema(self, account_id: int, chat_id: int, topic_id: int):
        """Elimina un tema de foro junto a sus mensajes (delete_forum_topic)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_forum_topic(chat_id, topic_id)
        return self._run(_coro())

    def fijar_tema(self, account_id: int, chat_id: int, topic_id: int, fijar: bool = True):
        """Fija o desfija un tema de foro (pin/unpin_forum_topic)."""
        async def _coro():
            client = await self._cliente(account_id)
            if fijar:
                return await client.pin_forum_topic(chat_id, topic_id)
            return await client.unpin_forum_topic(chat_id, topic_id)
        return self._run(_coro())

    # --- ACCIONES SOBRE CHATS ---
    def marcar_chat_leido(self, account_id: int, chat_id: int):
        """Marca todo el historial del chat como leído."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.read_chat_history(chat_id)
        res = self._run(_coro())
        db.execute("UPDATE telegram_chats SET unread_count = 0 WHERE id = ?", (f"{account_id}:{chat_id}",))
        return res

    def marcar_todos_leidos(self, account_id: int) -> int:
        """Marca como leídos todos los chats con mensajes sin leer. Devuelve cuántos."""
        chats = db.query(
            "SELECT chat_id FROM telegram_chats WHERE account_id = ? AND unread_count > 0",
            (account_id,)
        )

        async def _coro():
            client = await self._cliente(account_id)
            n = 0
            for (cid,) in chats:
                try:
                    await client.read_chat_history(cid)
                    n += 1
                except Exception as e:
                    log.debug(f"No se pudo marcar leído el chat {cid}: {e}")
            return n

        n = self._run(_coro(), timeout=120)
        db.execute("UPDATE telegram_chats SET unread_count = 0 WHERE account_id = ?", (account_id,))
        return n

    def marcar_chat_no_leido(self, account_id: int, chat_id: int):
        """Marca un chat como NO leído (mark_chat_unread)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.mark_chat_unread(chat_id)
        res = self._run(_coro())
        # Si el chat no tenía no leídos, dejamos al menos 1 para que se note el estado.
        db.execute(
            "UPDATE telegram_chats SET unread_count = MAX(unread_count, 1) WHERE id = ?",
            (f"{account_id}:{chat_id}",)
        )
        return res

    def marcar_leido_hasta(self, account_id: int, chat_id: int, max_id: int, restantes: int = 0):
        """Marca como leídos los mensajes hasta `max_id` (incluido), dejando `restantes` sin leer.
        Sirve para 'leído hasta donde me quedé': se persiste en el servidor (read_chat_history) el
        punto al que llegaste y los que faltan siguen sin leer. La BD se actualiza YA (optimista)
        para que la lista de chats lo refleje al instante."""
        # 1) BD al instante (optimista): la lista de chats muestra ya el nuevo contador.
        db.execute("UPDATE telegram_chats SET unread_count = ? WHERE id = ?",
                   (max(0, int(restantes)), f"{account_id}:{chat_id}"))
        if not max_id:
            return None

        # 2) Persistir en el SERVIDOR: marcar leído hasta ese mensaje. Si falla (red), la próxima
        # sincronización de la lista corregiría el contador; no rompemos por ello.
        async def _coro():
            client = await self._cliente(account_id)
            return await client.read_chat_history(chat_id, max_id=int(max_id))
        try:
            return self._run(_coro())
        except Exception as e:
            log.debug(f"No se pudo marcar leído hasta {max_id} en {chat_id}: {e}")
            return None

    def puedo_publicar(self, account_id: int, chat_id: int) -> bool:
        """Indica si la cuenta PUEDE escribir/publicar en el chat. Para chats privados y bots
        siempre sí; en canales solo si eres administrador con permiso de publicar; en grupos/
        supergrupos según tu estado (no baneado/restringido sin permiso) y los permisos por defecto
        del grupo. Sirve para ocultar la barra de publicar/grabar en sitios de solo lectura."""
        from pyrogram import enums

        async def _coro():
            client = await self._cliente(account_id)
            chat = await client.get_chat(chat_id)
            tipo = chat.type.value if hasattr(chat.type, "value") else str(chat.type)
            if tipo in ("private", "bot"):
                return True
            try:
                yo = await client.get_chat_member(chat_id, "me")
                estado = getattr(yo, "status", None)
            except Exception:
                yo, estado = None, None
            # Dueño o administrador: en canales, además, con permiso de publicar.
            if estado == enums.ChatMemberStatus.OWNER:
                return True
            if estado == enums.ChatMemberStatus.ADMINISTRATOR:
                priv = getattr(yo, "privileges", None)
                if tipo == "channel":
                    return bool(priv and getattr(priv, "can_post_messages", False))
                return True
            # Baneado o se ha ido: no.
            if estado in (enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.LEFT):
                return False
            # Canal de difusión y no admin: no se puede publicar.
            if tipo == "channel":
                return False
            # Restringido: según tus propios permisos.
            if estado == enums.ChatMemberStatus.RESTRICTED:
                perm = getattr(yo, "permissions", None)
                return bool(perm and getattr(perm, "can_send_messages", False))
            # Miembro normal de grupo/supergrupo: según los permisos por defecto del grupo.
            perm = getattr(chat, "permissions", None)
            return bool(perm is None or getattr(perm, "can_send_messages", True))

        try:
            return bool(self._run(_coro(), timeout=30))
        except Exception as e:
            log.debug(f"No se pudo determinar permiso de publicar en {chat_id}: {e}")
            return True   # ante la duda, no ocultar la barra

    def archivar_chat(self, account_id: int, chat_id: int, archivar: bool = True):
        """Archiva o desarchiva un chat."""
        async def _coro():
            client = await self._cliente(account_id)
            if archivar:
                return await client.archive_chats(chat_id)
            return await client.unarchive_chats(chat_id)
        return self._run(_coro())

    def obtener_enviar_como(self, account_id: int, chat_id: int) -> list:
        """Lista de perfiles/canales con los que puedes publicar en este chat ("Enviar como").
        Devuelve [(id, nombre), ...]."""
        async def _coro():
            client = await self._cliente(account_id)
            chats = await client.get_send_as_chats(chat_id)
            res = []
            for c in (chats or []):
                nombre = getattr(c, "title", None) or getattr(c, "first_name", None) or str(c.id)
                res.append((c.id, nombre))
            return res
        return self._run(_coro())

    def establecer_enviar_como(self, account_id: int, chat_id: int, send_as_id: int):
        """Fija con qué perfil/canal se publicará por defecto en este chat (set_send_as_chat)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_send_as_chat(chat_id, send_as_id)
        return self._run(_coro())

    def vaciar_chat(self, account_id: int, chat_id: int, para_todos: bool = False,
                    tipo: str = None):
        """Vacía la conversación borrando el historial (delete_chat_history).

        - PRIVADOS y GRUPOS BÁSICOS: borra TU historial. para_todos=True lo borra también para la
          otra persona (revoke).
        - CANALES y SUPERGRUPOS: Telegram EXIGE revoke=True (borra los mensajes PARA TODOS) y ser
          ADMINISTRADOR; no existe "vaciar solo para mí" en un canal. Antes se llamaba con
          revoke=False y el servidor lo rechazaba, de ahí el error al vaciar un canal."""
        es_canal = tipo in ("channel", "supergroup")
        async def _coro():
            client = await self._cliente(account_id)
            if es_canal:
                return await client.delete_chat_history(chat_id, revoke=True)
            return await client.delete_chat_history(chat_id, revoke=para_todos)
        res = self._run(_coro())
        # Quitar los mensajes cacheados localmente de ese chat.
        db.execute("DELETE FROM telegram_messages WHERE account_id = ? AND chat_id = ?",
                   (account_id, chat_id))
        db.execute("UPDATE telegram_chats SET unread_count = 0 WHERE id = ?",
                   (f"{account_id}:{chat_id}",))
        return res

    def silenciar_chat(self, account_id: int, chat_id: int, silenciar: bool = True,
                       minutos: int = 0):
        """Silencia o reactiva las notificaciones de un chat (update_chat_notifications).

        - silenciar=False: reactiva.
        - silenciar=True y minutos>0: silencia SOLO ese tiempo (mute_until).
        - silenciar=True y minutos<=0: silencia PARA SIEMPRE.
        Como otras apps de Telegram, permite elegir cuánto tiempo o para siempre."""
        async def _coro():
            client = await self._cliente(account_id)
            if not silenciar:
                return await client.update_chat_notifications(chat_id, mute=False)
            if minutos and minutos > 0:
                from datetime import datetime, timedelta
                return await client.update_chat_notifications(
                    chat_id, mute_until=datetime.now() + timedelta(minutes=minutos))
            return await client.update_chat_notifications(chat_id, mute=True)
        res = self._run(_coro())
        db.execute("UPDATE telegram_chats SET muted = ? WHERE id = ?",
                   (1 if silenciar else 0, f"{account_id}:{chat_id}"))
        return res

    def fijar_chat(self, account_id: int, chat_id: int, fijar: bool = True):
        """Fija o desfija un chat en la lista (raw messages.ToggleDialogPin)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import raw
            peer = await client.resolve_peer(chat_id)
            return await client.invoke(raw.functions.messages.ToggleDialogPin(
                peer=raw.types.InputDialogPeer(peer=peer),
                pinned=fijar
            ))
        res = self._run(_coro())
        db.execute("UPDATE telegram_chats SET pinned = ? WHERE id = ?", (1 if fijar else 0, f"{account_id}:{chat_id}"))
        return res

    # --- BÚSQUEDA ---
    def buscar_mensajes(self, account_id: int, chat_id: int, query: str, limit: int = 50) -> int:
        """Busca mensajes dentro de un chat y los cachea. Devuelve el número encontrado."""
        async def _coro():
            client = await self._cliente(account_id)
            encontrados = []
            async for msg in client.search_messages(chat_id, query=query, limit=limit):
                encontrados.append(msg)
            return encontrados
        mensajes = self._run(_coro(), timeout=90)
        with db.transaccion():
            for m in mensajes:
                self._guardar_mensaje(account_id, m)
        return len(mensajes)

    def buscar_mensajes_resultados(self, account_id: int, chat_id: int, query: str = "",
                                   desde=None, hasta=None, limit: int = 200) -> list:
        """Busca mensajes en un chat y DEVUELVE los resultados (sin mezclarlos en la conversación),
        para mostrarlos en una ventana de búsqueda aparte. `desde`/`hasta` (datetime) acotan por
        fecha. Cada resultado: {message_id, autor, texto, fecha, media}."""
        async def _coro():
            client = await self._cliente(account_id)
            kw = {"limit": int(limit)}
            if query:
                kw["query"] = query
            if desde is not None:
                kw["min_date"] = desde
            if hasta is not None:
                kw["max_date"] = hasta
            res = []
            async for m in client.search_messages(chat_id, **kw):
                autor = ""
                try:
                    u = getattr(m, "from_user", None)
                    if u:
                        autor = (getattr(u, "first_name", "") or "") + (
                            (" " + u.last_name) if getattr(u, "last_name", None) else "")
                    elif getattr(m, "sender_chat", None):
                        autor = getattr(m.sender_chat, "title", "") or ""
                except Exception:
                    autor = ""
                # Tipo de adjunto, NOMBRE del archivo y duración, como en la conversación: antes
                # solo se guardaba el tipo en inglés y un documento sin pie salía como
                # "[document]", sin decir qué archivo era (y Telegram también busca por nombre
                # de archivo, así que es un resultado frecuente).
                media, duracion, nombre_archivo = "", 0, ""
                try:
                    if getattr(m, "media", None):
                        media = m.media.value if hasattr(m.media, "value") else str(m.media)
                except Exception:
                    media = ""
                if not media:
                    for a in ("photo", "video", "audio", "voice", "document", "animation",
                              "sticker", "video_note"):
                        if getattr(m, a, None):
                            media = a
                            break
                if getattr(m, "checklist", None) is not None:
                    media = "checklist"
                for a in ("voice", "audio", "video", "video_note", "document", "animation"):
                    obj = getattr(m, a, None)
                    if obj is not None:
                        duracion = duracion or (getattr(obj, "duration", 0) or 0)
                        nombre_archivo = nombre_archivo or (getattr(obj, "file_name", "") or "")
                texto = str(getattr(m, "text", None) or getattr(m, "caption", None) or "")
                poll = getattr(m, "poll", None)
                if poll is not None and not texto:
                    preg = getattr(poll, "question", "") or ""
                    texto = str(getattr(preg, "text", preg) or "")
                chk = getattr(m, "checklist", None)
                if chk is not None and not texto:
                    texto = str(getattr(chk, "title", "") or "")
                res.append({
                    "message_id": m.id,
                    "autor": autor.strip(),
                    "texto": texto.replace("\n", " ")[:300],
                    "fecha": self._fecha_iso(getattr(m, "date", None)) if hasattr(self, "_fecha_iso")
                             else str(getattr(m, "date", "")),
                    "media": media,
                    "file_name": str(nombre_archivo or ""),
                    "duration": int(duracion or 0),
                })
            return res
        return self._run(_coro(), timeout=120)

    # Filtros de contenido para "multimedia compartida" y mensajes fijados.
    FILTROS_MEDIA = {
        "fotos": "PHOTO", "videos": "VIDEO", "musica": "AUDIO", "voz": "VOICE_NOTE",
        "archivos": "DOCUMENT", "gif": "ANIMATION", "enlaces": "URL", "fijados": "PINNED",
        "videonotas": "VIDEO_NOTE",
    }

    def medios_del_chat(self, account_id: int, chat_id: int, filtro: str, limit: int = 60) -> list:
        """Lista el contenido de un chat por tipo (fotos, vídeos, música, voz, archivos, GIF, enlaces,
        fijados). Devuelve [{message_id, tipo, texto, fecha, file_name}], sin cachear."""
        from app.services.telegram.telegram_common import tg_enums
        async def _coro():
            client = await self._cliente(account_id)
            f = getattr(tg_enums.MessagesFilter, self.FILTROS_MEDIA.get(filtro, "EMPTY"),
                        tg_enums.MessagesFilter.EMPTY)
            res = []
            async for m in client.search_messages(chat_id, filter=f, limit=limit):
                media = (getattr(m, "photo", None) or getattr(m, "video", None) or
                         getattr(m, "audio", None) or getattr(m, "voice", None) or
                         getattr(m, "document", None) or getattr(m, "animation", None))
                nombre = getattr(media, "file_name", None) or ""
                texto = (m.text or m.caption or "") if hasattr(m, "text") else ""
                res.append({
                    "message_id": m.id,
                    "tipo": filtro,
                    "texto": (texto or "").replace("\n", " ")[:200],
                    "fecha": self._fecha_iso(getattr(m, "date", None)) if hasattr(self, "_fecha_iso") else str(getattr(m, "date", "")),
                    "file_name": nombre,
                })
            return res
        return self._run(_coro(), timeout=90)

    def contar_mensajes_chat(self, account_id: int, chat_id: int) -> int:
        """Número total de mensajes de un chat (get_chat_history_count)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.get_chat_history_count(chat_id)
        return self._run(_coro())

    def contar_filtro(self, account_id: int, chat_id: int, filtro: str) -> int:
        """Número de mensajes de un chat de un tipo dado (search_messages_count)."""
        from app.services.telegram.telegram_common import tg_enums
        async def _coro():
            client = await self._cliente(account_id)
            f = getattr(tg_enums.MessagesFilter, self.FILTROS_MEDIA.get(filtro, "EMPTY"),
                        tg_enums.MessagesFilter.EMPTY)
            return await client.search_messages_count(chat_id, filter=f)
        return self._run(_coro())

    def contar_dialogos(self, account_id: int) -> int:
        """Número total de chats (get_dialogs_count)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.get_dialogs_count()
        return self._run(_coro())

    def contar_contactos(self, account_id: int) -> int:
        """Número total de contactos (get_contacts_count)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.get_contacts_count()
        return self._run(_coro())

    def cargar_mensajes_desde_fecha(self, account_id: int, chat_id: int, fecha, limit: int = 40) -> int:
        """Trae al caché los mensajes de un chat alrededor de una fecha (get_chat_history offset_date)
        para poder 'saltar a fecha'. Devuelve cuántos se han traído."""
        async def _coro():
            client = await self._cliente(account_id)
            traidos = []
            async for m in client.get_chat_history(chat_id, offset_date=fecha, limit=limit):
                traidos.append(m)
            return traidos
        mensajes = self._run(_coro(), timeout=90)
        with db.transaccion():
            for m in mensajes:
                self._guardar_mensaje(account_id, m)
        return len(mensajes)

    def cargar_mensajes_hasta_id(self, account_id: int, chat_id: int, message_id: int,
                                 limit: int = 40) -> int:
        """Trae al caché un mensaje concreto Y los anteriores a él (get_chat_history con max_id,
        que es inclusivo). Para "Ir al mensaje" desde la búsqueda: por fecha (offset_date) se
        traían solo los ESTRICTAMENTE anteriores y el propio mensaje podía quedarse fuera.
        Devuelve cuántos se han traído."""
        async def _coro():
            client = await self._cliente(account_id)
            traidos = []
            async for m in client.get_chat_history(chat_id, max_id=int(message_id), limit=limit):
                traidos.append(m)
            return traidos
        mensajes = self._run(_coro(), timeout=90)
        with db.transaccion():
            for m in mensajes:
                self._guardar_mensaje(account_id, m)
        return len(mensajes)

    def obtener_miembros(self, account_id: int, chat_id: int, limit: int = 200) -> list:
        """Devuelve la lista de miembros de un grupo/canal con datos completos para administrar:
        nombre mostrado (nombre y apellido, o el contacto renombrado), @usuario, ROL (owner,
        administrator, member, restricted, banned, left), presencia (en línea / última conexión…) y
        el título de administrador si lo tiene."""
        async def _coro():
            client = await self._cliente(account_id)
            # Tu PROPIO id: Telegram no reporta tu estado en línea en la lista de miembros (apareces
            # con tu última desconexión), así que para ti mostramos "En línea (tú)".
            try:
                yo = (await client.get_me()).id
            except Exception:
                yo = None
            miembros = []
            async for m in client.get_chat_members(chat_id, limit=limit):
                u = m.user
                if u is None:
                    continue
                nombre = " ".join(p for p in (getattr(u, "first_name", None),
                                              getattr(u, "last_name", None)) if p).strip()
                if not nombre:
                    nombre = (u.username and f"@{u.username}") or str(u.id)
                rol = m.status.value if hasattr(m.status, "value") else str(m.status)
                presencia = "En línea (tú)" if (yo and u.id == yo) else self._descripcion_estado(u)
                miembros.append({
                    "id": u.id,
                    "nombre": nombre,
                    "username": (f"@{u.username}" if getattr(u, "username", None) else ""),
                    "rol": rol,
                    "estado": rol,   # compatibilidad con código anterior
                    "presencia": presencia,
                    "titulo": getattr(m, "custom_title", "") or "",
                    "bot": bool(getattr(u, "is_bot", False)),
                })
            return miembros
        return self._run(_coro(), timeout=90)

    # --- BÚSQUEDA GLOBAL ---
    def buscar_global(self, account_id: int, query: str, limit: int = 50) -> list:
        """Busca mensajes en todos los chats. Devuelve lista de dicts resumidos."""
        async def _coro():
            client = await self._cliente(account_id)
            resultados = []
            async for msg in client.search_global(query=query, limit=limit):
                resultados.append({
                    "chat_id": getattr(msg.chat, "id", None),
                    "chat_title": getattr(msg.chat, "title", None) or getattr(msg.chat, "first_name", "") or "",
                    "message_id": msg.id,
                    "text": str(msg.text or msg.caption or "")[:200],
                    "date": self._fecha_iso(msg.date),
                })
            return resultados
        return self._run(_coro(), timeout=90)

    def buscar_publicaciones_canales(self, account_id: int, hashtag: str, limit: int = 50) -> list:
        """Busca publicaciones PÚBLICAS de canales de todo Telegram por hashtag (search_posts),
        aunque no estés suscrito al canal. Complementa a buscar_global (que solo busca en TUS
        chats). Devuelve lista de dicts resumidos."""
        tag = (hashtag or "").strip().lstrip("#")
        async def _coro():
            client = await self._cliente(account_id)
            resultados = []
            async for msg in client.search_posts(tag, limit=limit):
                resultados.append({
                    "chat_id": getattr(msg.chat, "id", None),
                    "chat_title": getattr(msg.chat, "title", None) or "",
                    "chat_username": getattr(msg.chat, "username", None) or "",
                    "message_id": msg.id,
                    "text": str(msg.text or msg.caption or "")[:300],
                    "date": self._fecha_iso(msg.date),
                })
            return resultados
        return self._run(_coro(), timeout=90)

    # --- INFORMACIÓN / PERFIL DE CHATS ---
    @staticmethod
    def _descripcion_estado(user) -> str:
        """Devuelve una descripción del estado de un usuario (en línea, visto, etc.)."""
        try:
            estado = user.status
            nombre = getattr(estado, "name", str(estado)).lower()
        except Exception:
            return ""
        if "online" in nombre:
            return "En línea"
        if "offline" in nombre:
            ultima = getattr(user, "last_online_date", None)
            if ultima:
                from app.ui.main_frame import formatear_tiempo_relativo
                return "Última conexión " + formatear_tiempo_relativo(
                    ultima.isoformat() if hasattr(ultima, "isoformat") else str(ultima)
                )
            return "Desconectado"
        if "recently" in nombre:
            return "Visto recientemente"
        if "last_week" in nombre:
            return "Visto la semana pasada"
        if "last_month" in nombre:
            return "Visto el mes pasado"
        if "long_ago" in nombre:
            return "Visto hace mucho"
        return ""

    def obtener_estado_usuario(self, account_id: int, chat_id: int) -> str:
        """Devuelve el estado de presencia de un usuario (En línea, Última conexión…)
        listo para mostrar. Cadena vacía si no aplica o no se puede consultar. Pensado
        para llamarse en segundo plano al abrir una conversación privada."""
        async def _coro():
            client = await self._cliente(account_id)
            u = await client.get_users(chat_id)
            return self._descripcion_estado(u)
        try:
            return self._run(_coro(), timeout=30) or ""
        except Exception as e:
            log.debug(f"No se pudo obtener el estado del usuario {chat_id}: {e}")
            return ""

    def obtener_info_chat(self, account_id: int, chat_id: int) -> dict:
        """Devuelve información detallada de un chat/usuario/grupo/canal."""
        async def _coro():
            client = await self._cliente(account_id)
            chat = await client.get_chat(chat_id)
            info = {
                "id": chat.id,
                "title": chat.title or " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)])) or "",
                "type": self._tipo_chat(chat),
                "username": (f"@{chat.username}" if getattr(chat, "username", None) else ""),
                "bio": getattr(chat, "bio", "") or getattr(chat, "description", "") or "",
                "members_count": getattr(chat, "members_count", 0) or 0,
                "estado": "",
                "telefono": getattr(chat, "phone_number", "") or "",
                "verificado": self._chat_verificado(chat),
            }
            # Estado en línea para usuarios
            if info["type"] in ("private", "bot"):
                try:
                    u = await client.get_users(chat_id)
                    info["estado"] = self._descripcion_estado(u)
                except Exception:
                    pass
            # Para grupos/supergrupos/canales: recuento real de miembros y de gente en línea (el
            # get_chat a veces no trae members_count en supergrupos), y si el contenido está
            # protegido (no se puede reenviar) y si es un foro (tiene temas).
            if info["type"] in ("group", "supergroup", "channel"):
                if not info["members_count"]:
                    try:
                        info["members_count"] = await client.get_chat_members_count(chat_id)
                    except Exception:
                        pass
                try:
                    info["online_count"] = await client.get_chat_online_count(chat_id)
                except Exception:
                    info["online_count"] = 0
                info["protegido"] = bool(getattr(chat, "has_protected_content", False))
                info["es_foro"] = bool(getattr(chat, "is_forum", False))
                # Grupo de comentarios vinculado (en canales) o canal asociado (en grupos).
                vinc = getattr(chat, "linked_chat", None)
                if vinc is not None:
                    info["chat_vinculado"] = {
                        "id": getattr(vinc, "id", None),
                        "titulo": getattr(vinc, "title", "") or "",
                    }
            return info
        return self._run(_coro(), timeout=60)

    def obtener_media_compartida(self, account_id: int, chat_id: int, categoria: str, limit: int = 100) -> list:
        """
        Devuelve la lista de elementos compartidos de un tipo en el chat.
        categoria: photo, video, audio, voice, document, url.
        """
        from pyrogram import enums
        mapa = {
            "photo": enums.MessagesFilter.PHOTO,
            "video": enums.MessagesFilter.VIDEO,
            "audio": enums.MessagesFilter.AUDIO,
            "voice": enums.MessagesFilter.VOICE_NOTE,
            "document": enums.MessagesFilter.DOCUMENT,
            "url": enums.MessagesFilter.URL,
        }
        filtro = mapa.get(categoria, enums.MessagesFilter.EMPTY)

        async def _coro():
            client = await self._cliente(account_id)
            items = []
            async for msg in client.search_messages(chat_id, filter=filtro, limit=limit):
                # str(...) plano: recortar directamente el Str de pyrogram dispara su slicing por
                # surrogates UTF-16 y, si el corte cae en un emoji, lanza un UnicodeDecodeError
                # ('utf-16-le ... unexpected end of data'). Con str() se recorta como texto normal.
                texto = str(msg.text or msg.caption or "")
                # Comentario (pie) del adjunto POR SEPARADO del nombre del archivo, para poder
                # mostrarlo aunque el elemento tenga file_name (p. ej. un documento con comentario).
                comentario = str(msg.caption or "")
                nombre = ""
                dur = 0
                for attr in ("voice", "audio", "video", "document"):
                    obj = getattr(msg, attr, None)
                    if obj is not None:
                        nombre = getattr(obj, "file_name", "") or ""
                        dur = getattr(obj, "duration", 0) or 0
                        break
                # Guardar en caché ANTES de leer la ruta (conserva media_path si ya se descargó).
                self._guardar_mensaje(account_id, msg)
                # Ruta local SOLO si el archivo sigue en disco (detecta descargas ya borradas).
                ruta = self.ruta_descarga_valida(account_id, chat_id, msg.id)
                items.append({
                    "message_id": msg.id,
                    "texto": (texto or nombre or "")[:200],
                    "caption": comentario[:300],
                    "fecha": self._fecha_iso(msg.date),
                    "file_name": nombre,
                    "duration": dur,
                    "outgoing": bool(getattr(msg, "outgoing", False)),
                    "categoria": categoria,
                    "media_path": ruta or "",
                    "descargado": bool(ruta),
                })
            return items
        return self._run(_coro(), timeout=120)

    # --- CONTACTOS ---
