# -*- coding: utf-8 -*-
"""Contactos, perfil propio, 2FA, historias, sesiones, bloqueos.

Mixin de TelegramService (parte de la refactorización modular).
"""
from app.services.telegram.telegram_common import (
    Optional, db, log)


class _PerfilMixin:
    def obtener_contactos(self, account_id: int) -> list:
        """Devuelve la lista de contactos del usuario."""
        async def _coro():
            client = await self._cliente(account_id)
            contactos = []
            for u in await client.get_contacts():
                nombre = " ".join(filter(None, [getattr(u, "first_name", None), getattr(u, "last_name", None)])) or (u.username or str(u.id))
                contactos.append({
                    "id": u.id,
                    "nombre": nombre,
                    "username": (f"@{u.username}" if getattr(u, "username", None) else ""),
                    "telefono": getattr(u, "phone_number", "") or "",
                })
            contactos.sort(key=lambda c: c["nombre"].lower())
            return contactos
        contactos = self._run(_coro(), timeout=90)
        # Guardar en caché para no volver a pedirlos en cada navegación (evita FLOOD_WAIT)
        db.execute("DELETE FROM telegram_contacts WHERE account_id = ?", (account_id,))
        for c in contactos:
            db.execute(
                "INSERT OR REPLACE INTO telegram_contacts (id, account_id, user_id, nombre, username, telefono) VALUES (?, ?, ?, ?, ?, ?)",
                (f"{account_id}:{c['id']}", account_id, c["id"], c["nombre"], c["username"], c["telefono"])
            )
        return contactos

    def contactos_cacheados(self, account_id: int) -> list:
        """Devuelve los contactos guardados en caché (sin pedir red)."""
        filas = db.query(
            "SELECT user_id, nombre, username, telefono FROM telegram_contacts WHERE account_id = ? ORDER BY nombre COLLATE NOCASE",
            (account_id,)
        )
        return [{"id": r[0], "nombre": r[1], "username": r[2], "telefono": r[3]} for r in filas]

    def añadir_contacto(self, account_id: int, identificador, first_name: str,
                        last_name: str = "", telefono: str = ""):
        """Añade un usuario a la agenda. identificador: id de usuario, @usuario o teléfono."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.add_contact(
                identificador, first_name, last_name=last_name or "",
                phone_number=telefono or "",
            )
        u = self._run(_coro())
        # Refrescar la caché de contactos tras el cambio.
        try:
            self.obtener_contactos(account_id)
        except Exception as e:
            log.debug(f"No se pudo refrescar contactos tras añadir: {e}")
        return u

    def borrar_contacto(self, account_id: int, user_ids):
        """Elimina uno o varios contactos de la agenda (delete_contacts)."""
        if isinstance(user_ids, int):
            user_ids = [user_ids]

        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_contacts(user_ids)
        res = self._run(_coro())
        for uid in user_ids:
            db.execute(
                "DELETE FROM telegram_contacts WHERE account_id = ? AND user_id = ?",
                (account_id, uid)
            )
        return res

    def importar_contactos(self, account_id: int, contactos: list) -> int:
        """Importa contactos en lote desde una lista de (telefono, nombre, apellido). Devuelve cuántos
        se han importado de verdad (import_contacts)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram.types import InputPhoneContact
            entradas = []
            for tel, nombre, apellido in contactos:
                tel = (tel or "").strip()
                nombre = (nombre or "").strip()
                if not tel or not nombre:
                    continue
                entradas.append(InputPhoneContact(phone=tel, first_name=nombre, last_name=(apellido or "").strip()))
            if not entradas:
                return 0
            res = await client.import_contacts(entradas)
            imp = getattr(res, "imported", None)
            return len(imp) if imp is not None else 0
        n = self._run(_coro())
        try:
            self.obtener_contactos(account_id)
        except Exception as e:
            log.debug(f"No se pudo refrescar contactos tras importar: {e}")
        return n

    def nota_contacto(self, account_id: int, user_id, nota: Optional[str] = None):
        """Cambia (o borra, con nota vacía) la nota privada de un contacto (set_contact_note)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_contact_note(user_id, nota or None)
        return self._run(_coro())

    def buscar_contactos(self, account_id: int, query: str, limit: int = 50) -> list:
        """Busca USUARIOS, GRUPOS, SUPERGRUPOS y CANALES públicos por nombre o @usuario, como la
        lupa de Telegram (search_contacts → contacts.Search). Devuelve dicts con id, nombre,
        username y tipo. Combina los resultados personales y los globales sin repetir."""
        async def _coro():
            client = await self._cliente(account_id)
            res = await client.search_contacts(query, limit=limit)
            chats = (list(getattr(res, "my_results", []) or [])
                     + list(getattr(res, "global_results", []) or []))
            salida, vistos = [], set()
            for c in chats:
                cid = getattr(c, "id", None)
                if cid is None or cid in vistos:
                    continue
                vistos.add(cid)
                nombre = (getattr(c, "title", None)
                          or " ".join(filter(None, [getattr(c, "first_name", None),
                                                    getattr(c, "last_name", None)]))
                          or (getattr(c, "username", None) or str(cid)))
                salida.append({
                    "id": cid,
                    "nombre": nombre,
                    "username": (f"@{c.username}" if getattr(c, "username", None) else ""),
                    "tipo": self._tipo_chat(c),
                })
            return salida
        return self._run(_coro(), timeout=60)

    # --- PERFIL ---
    def obtener_mi_perfil(self, account_id: int) -> dict:
        """Devuelve datos del perfil propio."""
        async def _coro():
            client = await self._cliente(account_id)
            me = await client.get_me()
            return {
                "id": me.id,
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "username": me.username or "",
                "phone": me.phone_number or "",
                "is_premium": bool(getattr(me, "is_premium", False)),
            }
        return self._run(_coro())

    def editar_perfil(self, account_id: int, first_name: Optional[str] = None,
                      last_name: Optional[str] = None, bio: Optional[str] = None):
        """Edita nombre, apellido y biografía del perfil propio."""
        async def _coro():
            client = await self._cliente(account_id)
            kwargs = {}
            if first_name is not None:
                kwargs["first_name"] = first_name
            if last_name is not None:
                kwargs["last_name"] = last_name
            if bio is not None:
                kwargs["bio"] = bio
            return await client.update_profile(**kwargs)
        return self._run(_coro())

    def cambiar_username(self, account_id: int, username: str):
        """Establece (o quita, con cadena vacía) el nombre de usuario público."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_username(username or None)
        return self._run(_coro())

    # --- PERFIL PROPIO AVANZADO (§M) ---
    def establecer_foto_perfil(self, account_id: int, ruta: str):
        """Cambia la foto de perfil propia (set_profile_photo)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import types
            return await client.set_profile_photo(photo=types.InputChatPhotoStatic(ruta))
        return self._run(_coro(), timeout=300)

    def borrar_foto_perfil(self, account_id: int):
        """Elimina la foto de perfil actual (delete_profile_photos)."""
        async def _coro():
            client = await self._cliente(account_id)
            fotos = []
            async for p in client.get_chat_photos("me", limit=1):
                fotos.append(p.file_id)
            if not fotos:
                return False
            return await client.delete_profile_photos(fotos)
        return self._run(_coro())

    def quitar_estado_emoji(self, account_id: int):
        """Quita el estado emoji del perfil (set_emoji_status sin emoji)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_emoji_status(emoji_status=None)
        return self._run(_coro())

    def actualizar_cumpleanos(self, account_id: int, dia: int, mes: int, anio: Optional[int] = None):
        """Establece la fecha de cumpleaños del perfil (update_birthday)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.update_birthday(day=dia, month=mes, year=anio)
        return self._run(_coro())

    def chats_en_comun(self, account_id: int, user_id) -> list:
        """Devuelve los chats en común con un usuario (get_common_chats)."""
        async def _coro():
            client = await self._cliente(account_id)
            comunes = []
            for c in await client.get_common_chats(user_id):
                comunes.append({
                    "id": getattr(c, "id", None),
                    "titulo": getattr(c, "title", None) or getattr(c, "first_name", "") or "",
                })
            return comunes
        return self._run(_coro(), timeout=60)

    # --- VERIFICACIÓN EN DOS PASOS (§N) ---
    def activar_2fa(self, account_id: int, password: str, pista: str = "", email: Optional[str] = None):
        """Activa la verificación en dos pasos (enable_cloud_password)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.enable_cloud_password(password, hint=pista, email=email)
        return self._run(_coro())

    def cambiar_2fa(self, account_id: int, password_actual: str, password_nueva: str, pista: str = ""):
        """Cambia la contraseña de verificación en dos pasos (change_cloud_password)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.change_cloud_password(password_actual, password_nueva, new_hint=pista)
        return self._run(_coro())

    def quitar_2fa(self, account_id: int, password: str):
        """Desactiva la verificación en dos pasos (remove_cloud_password)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.remove_cloud_password(password)
        return self._run(_coro())

    # --- HISTORIAS (STORIES, §O) ---
    def obtener_historias(self, account_id: int) -> list:
        """Lista las historias activas de los contactos (get_all_stories)."""
        async def _coro():
            client = await self._cliente(account_id)
            historias = []
            async for s in client.get_all_stories():
                autor = ""
                if getattr(s, "from_user", None):
                    u = s.from_user
                    autor = (u.username and f"@{u.username}") or (u.first_name or str(u.id))
                elif getattr(s, "sender_chat", None):
                    autor = getattr(s.sender_chat, "title", "") or ""
                chat = getattr(s.chat, "id", None) if getattr(s, "chat", None) else None
                tipo = "foto" if getattr(s, "photo", None) else ("vídeo" if getattr(s, "video", None) else "")
                historias.append({
                    "story_id": s.id,
                    "chat_id": chat,
                    "autor": autor,
                    "caption": getattr(s, "caption", "") or "",
                    "tipo": tipo,
                    "fecha": self._fecha_iso(getattr(s, "date", None)),
                })
            return historias
        return self._run(_coro(), timeout=90)

    def marcar_historia_vista(self, account_id: int, chat_id, story_id):
        """Marca una historia como vista (view_stories)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.view_stories(chat_id, story_id)
        return self._run(_coro())

    def publicar_historia(self, account_id: int, ruta: str, caption: str = ""):
        """Publica una historia propia desde un archivo (send_story)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_story("me", ruta, caption=caption or None)
        return self._run(_coro(), timeout=600)

    def descargar_historia(self, account_id: int, chat_id, story_id) -> Optional[str]:
        """Descarga el archivo de una historia. Devuelve la ruta local."""
        async def _coro():
            client = await self._cliente(account_id)
            historia = await client.get_stories(chat_id, story_id)
            if not historia or not getattr(historia, "media", None):
                return None
            destino = self._download_dir(["historias"])
            return await client.download_media(historia, file_name=destino + "/")
        return self._run(_coro(), timeout=600)

    # --- MIS HISTORIAS (gestión de las propias) ---
    def mis_historias(self, account_id: int) -> list:
        """Tus historias publicadas (fijadas en tu perfil). Devuelve [{story_id, caption, tipo, fecha}]."""
        async def _coro():
            client = await self._cliente(account_id)
            historias = []
            async for s in client.get_pinned_stories("me"):
                tipo = "foto" if getattr(s, "photo", None) else ("vídeo" if getattr(s, "video", None) else "")
                historias.append({
                    "story_id": s.id,
                    "chat_id": "me",
                    "caption": getattr(s, "caption", "") or "",
                    "tipo": tipo,
                    "fecha": self._fecha_iso(getattr(s, "date", None)),
                })
            return historias
        return self._run(_coro(), timeout=90)

    def quien_vio_historia(self, account_id: int, story_id: int) -> list:
        """Nombres de quienes han visto tu historia (get_story_views). Devuelve [nombre, ...]."""
        async def _coro():
            client = await self._cliente(account_id)
            res = await client.get_story_views("me", story_id, limit=100)
            vistas = getattr(res, "views", None)
            if vistas is None:
                vistas = res if isinstance(res, (list, tuple)) else []
            nombres = []
            for v in vistas:
                u = getattr(v, "user", None) or getattr(v, "from_user", None) or v
                nombre = (getattr(u, "username", None) and f"@{u.username}") or \
                         getattr(u, "first_name", None) or str(getattr(u, "id", v))
                nombres.append(nombre)
            return nombres
        return self._run(_coro())

    def borrar_historia(self, account_id: int, story_id: int):
        """Borra una de tus historias (delete_stories)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_stories("me", story_id)
        return self._run(_coro())

    def editar_pie_historia(self, account_id: int, story_id: int, caption: str):
        """Edita el pie de una de tus historias (edit_story_caption)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.edit_story_caption("me", story_id, caption)
        return self._run(_coro())

    def fijar_historia(self, account_id: int, story_id: int, fijar: bool = True):
        """Fija o quita de tu perfil una de tus historias (pin/unpin_chat_stories)."""
        async def _coro():
            client = await self._cliente(account_id)
            if fijar:
                return await client.pin_chat_stories("me", story_id)
            return await client.unpin_chat_stories("me", story_id)
        return self._run(_coro())

    def repostear_historia(self, account_id: int, from_chat_id, story_id: int, destino):
        """Reenvía (repostea) una historia a un chat (forward_story)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.forward_story(destino, from_chat_id, story_id)
        return self._run(_coro())

    def mis_historias_archivadas(self, account_id: int) -> list:
        """Tus historias archivadas (get_archived_stories). Devuelve [{story_id, caption, tipo, fecha}]."""
        async def _coro():
            client = await self._cliente(account_id)
            historias = []
            async for s in client.get_archived_stories("me"):
                tipo = "foto" if getattr(s, "photo", None) else ("vídeo" if getattr(s, "video", None) else "")
                historias.append({
                    "story_id": s.id, "chat_id": "me",
                    "caption": getattr(s, "caption", "") or "", "tipo": tipo,
                    "fecha": self._fecha_iso(getattr(s, "date", None)),
                })
            return historias
        return self._run(_coro(), timeout=90)

    def historias_de_chat(self, account_id: int, chat_id) -> list:
        """Historias activas de un canal/chat concreto (get_chat_stories).
        Devuelve [{story_id, chat_id, caption, tipo, fecha}]."""
        async def _coro():
            client = await self._cliente(account_id)
            historias = []
            async for s in client.get_chat_stories(chat_id):
                tipo = "foto" if getattr(s, "photo", None) else ("vídeo" if getattr(s, "video", None) else "")
                historias.append({
                    "story_id": s.id, "chat_id": chat_id,
                    "caption": getattr(s, "caption", "") or "", "tipo": tipo,
                    "fecha": self._fecha_iso(getattr(s, "date", None)),
                })
            return historias
        return self._run(_coro(), timeout=90)

    def editar_privacidad_historia(self, account_id: int, story_id: int, privacidad: str = "PUBLIC"):
        """Cambia la privacidad de una de tus historias (edit_story_privacy).
        privacidad: PUBLIC, CONTACTS, CLOSE_FRIENDS, SELECTED_USERS."""
        from app.services.telegram.telegram_common import tg_enums
        async def _coro():
            client = await self._cliente(account_id)
            regla = getattr(tg_enums.StoriesPrivacyRules, privacidad.upper(),
                            tg_enums.StoriesPrivacyRules.PUBLIC)
            return await client.edit_story_privacy("me", story_id, privacy=regla)
        return self._run(_coro())

    def editar_media_historia(self, account_id: int, story_id: int, ruta: str):
        """Reemplaza el archivo (foto/vídeo) de una de tus historias (edit_story_media)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.edit_story_media("me", story_id, ruta)
        return self._run(_coro(), timeout=600)

    # --- POTENCIAR CANAL (BOOSTS, §P) ---
    def potenciar_canal(self, account_id: int, chat_id):
        """Aplica un boost a un canal (apply_boost)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.apply_boost(chat_id)
        return self._run(_coro())

    def estado_boosts(self, account_id: int, chat_id) -> dict:
        """Devuelve el estado de boosts de un canal (get_boosts_status)."""
        async def _coro():
            client = await self._cliente(account_id)
            st = await client.get_boosts_status(chat_id)
            return {
                "nivel": getattr(st, "level", 0) or 0,
                "boosts": getattr(st, "boosts", 0) or 0,
                "mi_boost": bool(getattr(st, "my_boost", False)),
                "siguiente_nivel": getattr(st, "next_level_boosts", 0) or 0,
            }
        return self._run(_coro())

    # --- REGISTRO DE EVENTOS DE ADMIN (§Q) ---
    def obtener_registro_admin(self, account_id: int, chat_id, limit: int = 50) -> list:
        """Devuelve eventos recientes del registro de administración (get_chat_event_log)."""
        async def _coro():
            client = await self._cliente(account_id)
            eventos = []
            async for ev in client.get_chat_event_log(chat_id, limit=limit):
                eventos.append({
                    "fecha": self._fecha_iso(getattr(ev, "date", None)),
                    "accion": str(getattr(ev, "action", "")),
                })
            return eventos
        return self._run(_coro(), timeout=90)

    # --- TRADUCCIÓN NATIVA (§R) ---
    def traducir_mensaje_nativo(self, account_id: int, chat_id, message_id: int, idioma: str = "es") -> str:
        """Traduce un mensaje con el traductor de Telegram (translate_message_text)."""
        async def _coro():
            client = await self._cliente(account_id)
            res = await client.translate_message_text(chat_id, message_id, idioma)
            if isinstance(res, str):
                return res
            return getattr(res, "text", "") or str(res)
        return self._run(_coro(), timeout=60)

    # --- STICKERS (selector, resto de §F) ---
    def obtener_stickers(self, account_id: int, nombre_set: str) -> list:
        """Devuelve los stickers de un paquete por su nombre corto (get_stickers)."""
        async def _coro():
            client = await self._cliente(account_id)
            stickers = []
            for s in await client.get_stickers(nombre_set):
                stickers.append({
                    "file_id": s.file_id,
                    "emoji": getattr(s, "emoji", "") or "",
                })
            return stickers
        return self._run(_coro(), timeout=60)

    # --- SESIONES ACTIVAS ---
    def listar_sesiones(self, account_id: int) -> list:
        """Lista las sesiones (dispositivos) activas de la cuenta."""
        async def _coro():
            client = await self._cliente(account_id)
            res = await client.get_active_sessions()
            sesiones = []
            for s in res.active_sessions:
                sesiones.append({
                    "id": getattr(s, "id", 0),
                    "device": getattr(s, "device_model", ""),
                    "platform": getattr(s, "platform", ""),
                    "app": getattr(s, "application_name", ""),
                    "current": bool(getattr(s, "is_current", False)),
                    "ip": getattr(s, "ip_address", "") or getattr(s, "ip", ""),
                })
            return sesiones
        return self._run(_coro())

    def cerrar_otras_sesiones(self, account_id: int):
        """Cierra todas las demás sesiones, dejando solo la actual (reset_sessions)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.reset_sessions()
        return self._run(_coro())

    def cerrar_sesion_concreta(self, account_id: int, session_id: int):
        """Cierra una sesión (dispositivo) concreta por su id (reset_session)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.reset_session(session_id)
        return self._run(_coro())

    # --- BLOQUEO Y PRIVACIDAD ---
    def bloquear_usuario(self, account_id: int, user_id):
        """Bloquea a un usuario (block_user). Refleja el estado en la caché de chats."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.block_user(user_id)
        res = self._run(_coro())
        db.execute("UPDATE telegram_chats SET blocked = 1 WHERE account_id = ? AND chat_id = ?",
                   (account_id, user_id))
        return res

    def desbloquear_usuario(self, account_id: int, user_id):
        """Desbloquea a un usuario (unblock_user). Refleja el estado en la caché de chats."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.unblock_user(user_id)
        res = self._run(_coro())
        db.execute("UPDATE telegram_chats SET blocked = 0 WHERE account_id = ? AND chat_id = ?",
                   (account_id, user_id))
        return res

    def refrescar_bloqueados(self, account_id: int):
        """Marca en la caché de chats (columna blocked) quién está bloqueado, según la lista real
        del servidor (get_blocked_message_senders). Así el menú alterna Bloquear/Desbloquear con
        el estado correcto aunque el bloqueo se hiciera en otra app. No crítico si falla."""
        try:
            bloqueados = self.listar_bloqueados(account_id)
        except Exception as e:
            log.debug(f"No se pudo refrescar la lista de bloqueados de Telegram: {e}")
            return
        ids = [b["id"] for b in bloqueados if b.get("id") is not None]
        db.execute("UPDATE telegram_chats SET blocked = 0 WHERE account_id = ?", (account_id,))
        for uid in ids:
            db.execute("UPDATE telegram_chats SET blocked = 1 WHERE account_id = ? AND chat_id = ?",
                       (account_id, uid))

    def listar_bloqueados(self, account_id: int, limit: int = 200) -> list:
        """Lista los usuarios/chats bloqueados (get_blocked_message_senders)."""
        async def _coro():
            client = await self._cliente(account_id)
            bloqueados = []
            async for chat in client.get_blocked_message_senders(limit=limit):
                nombre = (getattr(chat, "title", None)
                          or " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)]))
                          or (getattr(chat, "username", None) or str(getattr(chat, "id", ""))))
                bloqueados.append({
                    "id": getattr(chat, "id", None),
                    "nombre": nombre,
                    "username": (f"@{chat.username}" if getattr(chat, "username", None) else ""),
                })
            return bloqueados
        return self._run(_coro(), timeout=90)

    # Claves de privacidad ofrecidas en la UI (nombre legible -> enum de Kurigram)
    CLAVES_PRIVACIDAD = {
        "last_seen": "STATUS",
        "profile_photo": "PROFILE_PHOTO",
        "phone_number": "PHONE_NUMBER",
        "forwards": "FORWARDS",
        "calls": "PHONE_CALL",
        "voice_messages": "VOICE_MESSAGES",
        "chat_invite": "CHAT_INVITE",
        "bio": "ABOUT",
        "birthday": "BIRTHDAY",
    }

    def fijar_privacidad(self, account_id: int, clave: str, modo: str):
        """Ajusta una regla de privacidad. clave: una de CLAVES_PRIVACIDAD.
        modo: 'todos', 'contactos' o 'nadie'."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import enums, types
            key = enums.PrivacyKey[self.CLAVES_PRIVACIDAD.get(clave, "STATUS")]
            if modo == "todos":
                reglas = [types.InputPrivacyRuleAllowAll()]
            elif modo == "nadie":
                reglas = [types.InputPrivacyRuleDisallowAll()]
            else:  # contactos
                reglas = [types.InputPrivacyRuleAllowContacts()]
            return await client.set_privacy(key, reglas)
        return self._run(_coro())

    # --- CUENTA Y PRIVACIDAD GLOBAL ---
    def obtener_canales_personales(self, account_id: int) -> list:
        """Tus canales públicos, para poder fijar uno como canal personal del perfil.
        Devuelve [(id, nombre), ...]."""
        async def _coro():
            client = await self._cliente(account_id)
            chats = await client.get_personal_channels()
            res = []
            for c in (chats or []):
                res.append((c.id, getattr(c, "title", None) or str(c.id)))
            return res
        return self._run(_coro())

    def establecer_canal_personal(self, account_id: int, chat_id=None):
        """Fija (o quita, con chat_id=None) el canal personal mostrado en tu biografía."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_personal_channel(chat_id)
        return self._run(_coro())

    def obtener_privacidad_global(self, account_id: int) -> dict:
        """Ajustes de privacidad global de la cuenta (get_global_privacy_settings)."""
        async def _coro():
            client = await self._cliente(account_id)
            s = await client.get_global_privacy_settings()
            return {
                "archive_and_mute_new_chats": bool(getattr(s, "archive_and_mute_new_chats", False)),
                "keep_unmuted_chats_archived": bool(getattr(s, "keep_unmuted_chats_archived", False)),
                "keep_chats_from_folders_archived": bool(getattr(s, "keep_chats_from_folders_archived", False)),
                "show_read_date": bool(getattr(s, "show_read_date", False)),
                "allow_new_chats_from_unknown_users": bool(getattr(s, "allow_new_chats_from_unknown_users", False)),
            }
        return self._run(_coro())

    def establecer_privacidad_global(self, account_id: int, **campos):
        """Cambia ajustes de privacidad global (set_global_privacy_settings). Pasa solo los que quieras."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_global_privacy_settings(**campos)
        return self._run(_coro())

    def obtener_ttl_cuenta(self, account_id: int) -> int:
        """Días de inactividad tras los que se autodestruiría la cuenta (get_account_ttl)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.get_account_ttl()
        return self._run(_coro())

    def establecer_ttl_cuenta(self, account_id: int, dias: int):
        """Fija los días de inactividad antes de autodestruir la cuenta (set_account_ttl)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_account_ttl(dias)
        return self._run(_coro())

    def establecer_ttl_sesiones(self, account_id: int, dias: int):
        """Fija tras cuántos días de inactividad se cierran solas las sesiones (set_inactive_session_ttl)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_inactive_session_ttl(dias)
        return self._run(_coro())

    def actualizar_estado(self, account_id: int, offline: bool):
        """Marca tu estado como en línea (offline=False) o desconectado (offline=True)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.update_status(offline)
        return self._run(_coro())

    # --- CAMBIO DE NÚMERO DE TELÉFONO (flujo en dos pasos) ---
    def enviar_codigo_cambio_numero(self, account_id: int, numero: str) -> Optional[str]:
        """Paso 1: envía el código de confirmación al nuevo número. Devuelve el phone_code_hash."""
        async def _coro():
            client = await self._cliente(account_id)
            sent = await client.send_phone_number_code(numero)
            return getattr(sent, "phone_code_hash", None)
        return self._run(_coro())

    def confirmar_cambio_numero(self, account_id: int, numero: str, code_hash: str, codigo: str):
        """Paso 2: confirma el cambio de número con el código recibido (change_phone_number)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.change_phone_number(numero, code_hash, codigo)
        return self._run(_coro())

    # --- GRUPOS Y CANALES ---
