# -*- coding: utf-8 -*-
"""Administración de grupos/canales, enlaces, llamadas de voz.

Mixin de TelegramService (parte de la refactorización modular).
"""
from app.services.telegram.telegram_common import (
    asyncio, Optional, db, get_data_path, log, TelegramError)


class _AdminMixin:
    def unirse_por_enlace(self, account_id: int, enlace: str):
        """Se une a un grupo/canal por enlace de invitación o @usuario público."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.join_chat(enlace)
        res = self._run(_coro())
        # Si te re-unes a un chat que habías borrado, quitar su tombstone para que vuelva a aparecer
        # (aunque no tenga mensajes nuevos respecto a cuando lo borraste).
        try:
            cid = getattr(res, "id", None)
            if cid is not None:
                db.execute("DELETE FROM telegram_deleted_chats WHERE id = ?", (f"{account_id}:{cid}",))
        except Exception:
            pass
        # Refrescar la lista de chats tras unirse
        try:
            self.cargar_chats(account_id, limit=100)
        except Exception:
            pass
        return res

    @staticmethod
    def _es_error_no_miembro(e) -> bool:
        """True si el error de Telegram indica que YA no estás en ese chat / no tienes acceso a él
        (porque ya saliste, lo cerraste desde otro sitio, o el canal es privado para ti). En esos
        casos no es un fallo real: hay que quitarlo de tu lista igualmente y sin avisar."""
        msg = str(e).upper()
        # Errores que significan "ya no estás dentro / el chat ya no existe para ti": en ese caso
        # quitarlo de la lista local es lo correcto (ya estabas fuera). NO se incluye
        # USER_ALREADY_PARTICIPANT (significa lo contrario: que SIGUES dentro), para no fingir un
        # borrado que en realidad no ocurrió.
        return any(clave in msg for clave in (
            "CHANNEL_PRIVATE", "USER_NOT_PARTICIPANT", "PEER_ID_INVALID", "CHANNEL_INVALID",
            "CHAT_ID_INVALID", "CHAT_FORBIDDEN", "USER_DEACTIVATED"))

    def _quitar_chat_local(self, account_id: int, chat_id: int):
        """Quita un chat de la caché local (lista y mensajes) y deja un TOMBSTONE para que no
        reaparezca al actualizar si el servidor sigue devolviendo su diálogo (típico de canales y
        supergrupos). El tombstone se limpia solo si más adelante llega un mensaje más nuevo que el
        borrado (el usuario volvió a entrar); ver cargar_chats."""
        from datetime import datetime
        db.execute("DELETE FROM telegram_chats WHERE id = ?", (f"{account_id}:{chat_id}",))
        try:
            db.execute("DELETE FROM telegram_messages WHERE account_id = ? AND chat_id = ?",
                       (account_id, chat_id))
        except Exception:
            pass
        try:
            db.execute(
                "INSERT OR REPLACE INTO telegram_deleted_chats (id, account_id, chat_id, deleted_at) "
                "VALUES (?, ?, ?, ?)",
                (f"{account_id}:{chat_id}", account_id, chat_id, datetime.now().isoformat()))
        except Exception as e:
            log.debug(f"No se pudo registrar el tombstone del chat {chat_id}: {e}")

    def salir_chat(self, account_id: int, chat_id: int, eliminar: bool = False, tipo: str = None):
        """Abandona/borra un chat de TU lista, según su tipo. La clave es que el chat DESAPAREZCA de
        la lista de diálogos (get_dialogs) y NO reaparezca al actualizar, lo que exige BORRAR EL
        DIÁLOGO, no solo salir:

        - PRIVADOS y bots: no se puede "salir" (no eres miembro de nada); delete_chat_history
          (just_clear=False) borra tu historial Y quita el diálogo de la lista. revoke=False: no lo
          borra para la otra persona.
        - GRUPOS BÁSICOS (chat): leave_chat(delete=True) -> messages.DeleteChatUser (salir) +
          messages.DeleteHistory (quita el diálogo). Antes, al "salir" (delete=False) solo se hacía
          DeleteChatUser y el diálogo SE QUEDABA, así que reaparecía al actualizar. Por eso siempre
          se borra el diálogo: salir de un grupo debe quitarlo de tu lista.
        - SUPERGRUPOS y CANALES (channel): channels.LeaveChannel -> te saca y quita el diálogo (el
          parámetro 'delete' de leave_chat se ignora para canales).

        Solo se quita de la lista LOCAL si el servidor CONFIRMA, o si el error indica que ya no eras
        miembro/no existe para ti. Si hay un error REAL (p. ej. eres el único propietario de un canal
        y Telegram no te deja salir sin borrarlo), NO se quita en local y se propaga el error."""
        es_privado = tipo in ("private", "bot")
        es_grupo_basico = tipo == "group"
        async def _coro():
            client = await self._cliente(account_id)
            if es_privado:
                return await client.delete_chat_history(chat_id, just_clear=False, revoke=False)
            if es_grupo_basico:
                # delete=True SIEMPRE: salir + borrar el diálogo, para que no reaparezca en la lista.
                return await client.leave_chat(chat_id, delete=True)
            # Canal o supergrupo (o tipo desconocido): leave_chat -> channels.LeaveChannel.
            return await client.leave_chat(chat_id)
        error = None
        try:
            self._run(_coro())
        except Exception as e:
            if not self._es_error_no_miembro(e):
                error = e
        if error is not None:
            raise error
        self._quitar_chat_local(account_id, chat_id)
        return True

    def crear_enlace_invitacion(self, account_id: int, chat_id: int, nombre: str = None,
                                caduca_en_segundos: int = None, limite_miembros: int = None,
                                con_aprobacion: bool = None) -> str:
        """Crea un enlace de invitación al chat, opcionalmente con: nombre, caducidad (en segundos
        desde ahora), límite de usos (miembros) y si requiere aprobación del administrador."""
        from datetime import datetime, timedelta
        expira = None
        if caduca_en_segundos and caduca_en_segundos > 0:
            expira = datetime.now() + timedelta(seconds=caduca_en_segundos)
        async def _coro():
            client = await self._cliente(account_id)
            link = await client.create_chat_invite_link(
                chat_id, name=nombre or None, expire_date=expira,
                member_limit=limite_miembros or None,
                creates_join_request=con_aprobacion)
            return link.invite_link
        return self._run(_coro())

    def editar_enlace_invitacion(self, account_id: int, chat_id: int, enlace: str,
                                 nombre: str = None, caduca_en_segundos: int = None,
                                 limite_miembros: int = None, con_aprobacion: bool = None) -> str:
        """Edita un enlace de invitación existente (nombre, caducidad, límite, aprobación)."""
        from datetime import datetime, timedelta
        expira = None
        if caduca_en_segundos and caduca_en_segundos > 0:
            expira = datetime.now() + timedelta(seconds=caduca_en_segundos)
        async def _coro():
            client = await self._cliente(account_id)
            link = await client.edit_chat_invite_link(
                chat_id, enlace, name=nombre, expire_date=expira,
                member_limit=limite_miembros, creates_join_request=con_aprobacion)
            return getattr(link, "invite_link", enlace)
        return self._run(_coro())

    def banear_miembro(self, account_id: int, chat_id: int, user_id: int):
        """Banea (expulsa) a un miembro de un grupo/canal."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.ban_chat_member(chat_id, user_id)
        return self._run(_coro())

    def desbanear_miembro(self, account_id: int, chat_id: int, user_id: int):
        """Quita el baneo a un miembro."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.unban_chat_member(chat_id, user_id)
        return self._run(_coro())

    def borrar_mensajes_de_usuario(self, account_id: int, chat_id: int, user_id: int):
        """(Admin) Borra todos los mensajes de un usuario en un supergrupo (limpieza de spam)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_user_history(chat_id, user_id)
        res = self._run(_coro())
        db.execute("DELETE FROM telegram_messages WHERE account_id = ? AND chat_id = ? AND sender_id = ?",
                   (account_id, chat_id, user_id))
        return res

    def promover_admin(self, account_id: int, chat_id: int, user_id: int, privilegios: dict = None):
        """Promociona a un miembro a administrador. Si se pasa 'privilegios' (dict de booleanos de
        ChatPrivileges), se usan esos permisos exactos; si no, un conjunto básico razonable."""
        from pyrogram.types import ChatPrivileges
        if privilegios is None:
            privilegios = dict(
                can_manage_chat=True, can_delete_messages=True,
                can_restrict_members=True, can_invite_users=True,
                can_pin_messages=True,
            )
        async def _coro():
            client = await self._cliente(account_id)
            return await client.promote_chat_member(
                chat_id, user_id, privileges=ChatPrivileges(**privilegios))
        return self._run(_coro())

    # Campos de permisos de administrador (ChatPrivileges) que se ofrecen en la interfaz, con su
    # etiqueta en español. Orden = orden de aparición en el diálogo de permisos.
    PRIVILEGIOS_ADMIN = [
        ("can_manage_chat", "Gestionar el chat"),
        ("can_change_info", "Cambiar la información (nombre, foto)"),
        ("can_delete_messages", "Borrar mensajes"),
        ("can_restrict_members", "Banear y restringir usuarios"),
        ("can_promote_members", "Añadir nuevos administradores"),
        ("can_invite_users", "Invitar usuarios mediante enlace"),
        ("can_pin_messages", "Fijar mensajes"),
        ("can_manage_video_chats", "Gestionar chats de voz/vídeo"),
        ("can_manage_topics", "Gestionar temas (foros)"),
        ("can_post_messages", "Publicar mensajes (canales)"),
        ("can_edit_messages", "Editar mensajes de otros (canales)"),
        ("is_anonymous", "Anónimo (ocultar quién administra)"),
    ]

    def degradar_admin(self, account_id: int, chat_id: int, user_id: int):
        """Quita los privilegios de administrador a un usuario (promover con todo a falso)."""
        from pyrogram.types import ChatPrivileges
        async def _coro():
            client = await self._cliente(account_id)
            return await client.promote_chat_member(
                chat_id, user_id,
                privileges=ChatPrivileges(can_manage_chat=False))
        return self._run(_coro())

    def obtener_privilegios_admin(self, account_id: int, chat_id: int, user_id: int) -> dict:
        """Devuelve {campo: bool} con los privilegios actuales del usuario (vacío si no es admin),
        para precargar las casillas del diálogo de permisos."""
        async def _coro():
            client = await self._cliente(account_id)
            miembro = await client.get_chat_member(chat_id, user_id)
            priv = getattr(miembro, "privileges", None)
            if not priv:
                return {}
            return {campo: bool(getattr(priv, campo, False)) for campo, _e in self.PRIVILEGIOS_ADMIN}
        return self._run(_coro())

    def soy_administrador(self, account_id: int, chat_id: int) -> bool:
        """True si la cuenta es DUEÑO o ADMINISTRADOR del chat. Sirve para mostrar el botón
        'Administrar' solo cuando procede. Ante cualquier error (p. ej. el chat no permite
        consultar miembros) devuelve False, que es el lado seguro (no ofrecer administración)."""
        from pyrogram import enums

        async def _coro():
            client = await self._cliente(account_id)
            try:
                miembro = await client.get_chat_member(chat_id, "me")
            except Exception:
                return False
            estado = getattr(miembro, "status", None)
            return estado in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)
        return self._run(_coro())

    # --- ADMINISTRACIÓN AVANZADA DE GRUPOS/CANALES (§J) ---
    def establecer_foto_chat(self, account_id: int, chat_id: int, ruta: str):
        """Cambia la foto de un grupo/canal (set_chat_photo)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_photo(chat_id, photo=ruta)
        return self._run(_coro(), timeout=300)

    def borrar_foto_chat(self, account_id: int, chat_id: int):
        """Elimina la foto de un grupo/canal (delete_chat_photo)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_chat_photo(chat_id)
        return self._run(_coro())

    def establecer_username_chat(self, account_id: int, chat_id: int, username: str):
        """Establece (o quita, con cadena vacía) el username público del chat."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_username(chat_id, username or None)
        return self._run(_coro())

    def establecer_modo_lento(self, account_id: int, chat_id: int, segundos: int):
        """Configura el modo lento de un supergrupo (0 lo desactiva)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_slow_mode(chat_id, segundos)
        return self._run(_coro())

    def establecer_autodestruccion_chat(self, account_id: int, chat_id: int, segundos: int):
        """Configura el autoborrado de mensajes del chat (0 lo desactiva)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_ttl(chat_id, segundos)
        return self._run(_coro())

    def restringir_reenvios(self, account_id: int, chat_id: int, restringir: bool):
        """Activa/desactiva el "contenido protegido": si está activo, los miembros NO pueden
        reenviar ni guardar el contenido del grupo/canal (set_chat_protected_content)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_protected_content(chat_id, restringir)
        return self._run(_coro())

    def activar_temas_foro(self, account_id: int, chat_id: int, activar: bool):
        """Activa o desactiva los TEMAS (modo foro) de un supergrupo (toggle_forum_topics)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.toggle_forum_topics(chat_id, is_forum=activar)
        return self._run(_coro())

    # --- GRUPO DE COMENTARIOS (DISCUSIÓN) DE UN CANAL ---
    def chats_aptos_discusion(self, account_id: int) -> list:
        """Lista de grupos que pueden vincularse como grupo de comentarios de un canal
        (get_suitable_discussion_chats). Devuelve [{id, titulo}]."""
        async def _coro():
            client = await self._cliente(account_id)
            res = []
            for c in await client.get_suitable_discussion_chats():
                res.append({"id": c.id, "titulo": getattr(c, "title", "") or str(c.id)})
            return res
        return self._run(_coro(), timeout=60)

    def vincular_grupo_discusion(self, account_id: int, canal_id: int, grupo_id: int):
        """Vincula un grupo como grupo de COMENTARIOS de un canal (set_chat_discussion_group)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_discussion_group(
                chat_id=canal_id, discussion_chat_id=grupo_id)
        return self._run(_coro())

    def desvincular_grupo_discusion(self, account_id: int, canal_id: int):
        """Quita el grupo de comentarios de un canal (discussion_chat_id=None)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_chat_discussion_group(
                chat_id=canal_id, discussion_chat_id=None)
        return self._run(_coro())

    def establecer_permisos_chat(self, account_id: int, chat_id: int, permisos: dict):
        """Permisos por defecto del grupo. permisos: dict de booleanos (ver ChatPermissions)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram.types import ChatPermissions
            return await client.set_chat_permissions(chat_id, ChatPermissions(**permisos))
        return self._run(_coro())

    def restringir_miembro(self, account_id: int, chat_id: int, user_id: int, permisos: dict):
        """Restringe a un miembro con los permisos indicados (restrict_chat_member)."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram.types import ChatPermissions
            return await client.restrict_chat_member(chat_id, user_id, ChatPermissions(**permisos))
        return self._run(_coro())

    def silenciar_miembro(self, account_id: int, chat_id: int, user_id: int):
        """Silencia a un miembro: no podrá enviar nada (restricción total de envío)."""
        return self.restringir_miembro(account_id, chat_id, user_id, {"can_send_messages": False})

    def quitar_restriccion_miembro(self, account_id: int, chat_id: int, user_id: int):
        """Devuelve a un miembro todos los permisos de envío."""
        permisos = {
            "can_send_messages": True, "can_send_media_messages": True,
            "can_send_other_messages": True, "can_add_web_page_previews": True,
            "can_send_polls": True, "can_invite_users": True,
        }
        return self.restringir_miembro(account_id, chat_id, user_id, permisos)

    def titulo_administrador(self, account_id: int, chat_id: int, user_id: int, titulo: str):
        """Establece el título personalizado de un administrador (set_administrator_title)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_administrator_title(chat_id, user_id, titulo)
        return self._run(_coro())

    def añadir_miembros(self, account_id: int, chat_id: int, user_ids):
        """Añade uno o varios usuarios a un grupo/canal (add_chat_members)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.add_chat_members(chat_id, user_ids)
        return self._run(_coro(), timeout=120)

    def transferir_propiedad(self, account_id: int, chat_id: int, user_id: int, password: str):
        """Transfiere la propiedad del chat a otro usuario (requiere contraseña 2FA)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.transfer_chat_ownership(chat_id, user_id, password)
        return self._run(_coro())

    def eliminar_chat(self, account_id: int, chat_id: int, es_canal: bool = False, tipo: str = None):
        """Elimina/cierra por completo un canal, supergrupo o grupo, siendo administrador/creador.
        'tipo' ('channel'/'supergroup'/'group') tiene preferencia; si no se pasa, se usa es_canal
        (compatibilidad). Los CANALES y SUPERGRUPOS se borran del todo; los grupos BÁSICOS (legacy)
        no se pueden borrar por la API, así que se abandonan y se quitan de tu lista."""
        if tipo is None:
            tipo = "channel" if es_canal else "supergroup"
        async def _coro():
            client = await self._cliente(account_id)
            if tipo == "channel":
                return await client.delete_channel(chat_id)
            if tipo == "supergroup":
                return await client.delete_supergroup(chat_id)
            # Grupo básico: la API no permite borrarlo; lo abandonamos eliminándolo de tu lista.
            return await client.leave_chat(chat_id, delete=True)
        error = None
        try:
            self._run(_coro())
        except Exception as e:
            # Si ya no eres miembro (CHANNEL_PRIVATE, etc.) es porque ya saliste/lo cerraste: hay
            # que quitarlo de TU lista igualmente y SIN avisar. Otros errores se relanzan, pero el
            # chat se quita de la lista local de todos modos (es lo que pides al "cerrar y eliminar").
            if not self._es_error_no_miembro(e):
                error = e
        self._quitar_chat_local(account_id, chat_id)
        if error is not None:
            raise error
        return True

    # --- SOLICITUDES DE UNIÓN Y ENLACES (§K) ---
    def listar_solicitudes_union(self, account_id: int, chat_id: int, limit: int = 100) -> list:
        """Lista las solicitudes de unión pendientes (get_chat_join_requests)."""
        async def _coro():
            client = await self._cliente(account_id)
            solicitudes = []
            async for j in client.get_chat_join_requests(chat_id, limit=limit):
                u = getattr(j, "user", None)
                nombre = ""
                uid = None
                if u is not None:
                    uid = u.id
                    nombre = (u.username and f"@{u.username}") or (u.first_name or str(u.id))
                solicitudes.append({
                    "user_id": uid,
                    "nombre": nombre,
                    "bio": getattr(j, "bio", "") or "",
                    "fecha": self._fecha_iso(getattr(j, "date", None)),
                })
            return solicitudes
        return self._run(_coro(), timeout=90)

    def aprobar_solicitud(self, account_id: int, chat_id: int, user_id: int):
        """Aprueba una solicitud de unión (approve_chat_join_request)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.approve_chat_join_request(chat_id, user_id)
        return self._run(_coro())

    def rechazar_solicitud(self, account_id: int, chat_id: int, user_id: int):
        """Rechaza una solicitud de unión (decline_chat_join_request)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.decline_chat_join_request(chat_id, user_id)
        return self._run(_coro())

    def aprobar_todas_solicitudes(self, account_id: int, chat_id: int):
        """Aprueba todas las solicitudes de unión pendientes."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.approve_all_chat_join_requests(chat_id)
        return self._run(_coro())

    def rechazar_todas_solicitudes(self, account_id: int, chat_id: int):
        """Rechaza todas las solicitudes de unión pendientes."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.decline_all_chat_join_requests(chat_id)
        return self._run(_coro())

    def listar_enlaces_invitacion(self, account_id: int, chat_id: int, limit: int = 100) -> list:
        """Lista los enlaces de invitación creados por la cuenta (get_chat_admin_invite_links)."""
        async def _coro():
            client = await self._cliente(account_id)
            me = await client.get_me()
            enlaces = []
            async for l in client.get_chat_admin_invite_links(chat_id, me.id, limit=limit):
                enlaces.append({
                    "enlace": getattr(l, "invite_link", "") or "",
                    "nombre": getattr(l, "name", "") or "",
                    "revocado": bool(getattr(l, "is_revoked", False)),
                    "miembros": getattr(l, "member_count", 0) or 0,
                    "solicitudes": getattr(l, "pending_join_request_count", 0) or 0,
                })
            return enlaces
        return self._run(_coro(), timeout=90)

    def quien_se_unio_por_enlace(self, account_id: int, chat_id: int, enlace: str, limit: int = 100) -> list:
        """Lista las personas que se unieron por un enlace de invitación concreto
        (get_chat_invite_link_joiners). Devuelve [(nombre, @usuario, fecha_iso)]."""
        async def _coro():
            client = await self._cliente(account_id)
            res = []
            async for j in client.get_chat_invite_link_joiners(chat_id, enlace, limit=limit):
                u = getattr(j, "user", None)
                nombre = (getattr(u, "first_name", None) or str(getattr(u, "id", ""))) if u else ""
                usuario = f"@{u.username}" if (u and getattr(u, "username", None)) else ""
                fecha = self._fecha_iso(getattr(j, "date", None)) if hasattr(self, "_fecha_iso") else str(getattr(j, "date", ""))
                res.append((nombre, usuario, fecha))
            return res
        return self._run(_coro(), timeout=90)

    def revocar_enlace_invitacion(self, account_id: int, chat_id: int, enlace: str):
        """Revoca un enlace de invitación (revoke_chat_invite_link)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.revoke_chat_invite_link(chat_id, enlace)
        return self._run(_coro())

    def borrar_enlace_invitacion(self, account_id: int, chat_id: int, enlace: str):
        """Borra definitivamente un enlace de invitación ya revocado."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.delete_chat_invite_link(chat_id, enlace)
        return self._run(_coro())

    # --- COMENTARIOS DE CANAL (§L) ---
    def obtener_comentarios(self, account_id: int, chat_id: int, message_id: int,
                            limit: int = 50) -> list:
        """Devuelve los comentarios (hilo de discusión) de una publicación de canal."""
        async def _coro():
            client = await self._cliente(account_id)
            comentarios = []
            async for m in client.get_discussion_replies(chat_id, message_id, limit=limit):
                autor = ""
                if getattr(m, "from_user", None):
                    u = m.from_user
                    autor = (u.username and f"@{u.username}") or (u.first_name or str(u.id))
                elif getattr(m, "sender_chat", None):
                    autor = getattr(m.sender_chat, "title", "") or ""
                comentarios.append({
                    "message_id": m.id,
                    "autor": autor,
                    "texto": str(m.text or m.caption or "")[:400],
                    "fecha": self._fecha_iso(getattr(m, "date", None)),
                })
            return comentarios
        return self._run(_coro(), timeout=90)

    def obtener_chat_comentarios(self, account_id: int, chat_id: int, message_id: int):
        """Devuelve (grupo_discusion_id, message_id) para responder a los comentarios."""
        async def _coro():
            client = await self._cliente(account_id)
            msg = await client.get_discussion_message(chat_id, message_id)
            if not msg:
                return None
            return {"chat_id": getattr(msg.chat, "id", None), "message_id": msg.id}
        return self._run(_coro())

    def crear_grupo(self, account_id: int, titulo: str, miembros: list) -> int:
        """Crea un grupo con los miembros indicados (lista de @usuarios o ids)."""
        async def _coro():
            client = await self._cliente(account_id)
            chat = await client.create_group(titulo, miembros)
            return chat.id
        cid = self._run(_coro())
        try:
            self.cargar_chats(account_id, limit=100)
        except Exception:
            pass
        return cid

    def crear_canal(self, account_id: int, titulo: str, descripcion: str = "") -> int:
        """Crea un canal."""
        async def _coro():
            client = await self._cliente(account_id)
            chat = await client.create_channel(titulo, description=descripcion)
            return chat.id
        cid = self._run(_coro())
        try:
            self.cargar_chats(account_id, limit=100)
        except Exception:
            pass
        return cid

    def crear_supergrupo(self, account_id: int, titulo: str, descripcion: str = "",
                         es_foro: bool = False) -> int:
        """Crea un supergrupo (grupo grande con administración avanzada). Si es_foro=True, se crea
        ya con los temas (foros) activados."""
        async def _coro():
            client = await self._cliente(account_id)
            chat = await client.create_supergroup(titulo, description=descripcion or "",
                                                  is_forum=es_foro or None)
            return chat.id
        cid = self._run(_coro())
        try:
            self.cargar_chats(account_id, limit=100)
        except Exception:
            pass
        return cid

    # --- EXPORTACIÓN ---
    def exportar_chat(self, account_id: int, chat_id: int, ruta: str, formato: str = "txt") -> str:
        """Exporta los mensajes cacheados de un chat a TXT, HTML o JSON."""
        rows = db.query("""
            SELECT message_id, sender_name, text, date, outgoing, media_type
            FROM telegram_messages WHERE account_id = ? AND chat_id = ?
            ORDER BY message_id ASC
        """, (account_id, chat_id))

        if formato == "json":
            import json
            datos = [
                {"message_id": r[0], "sender": r[1], "text": r[2], "date": r[3],
                 "outgoing": bool(r[4]), "media_type": r[5]}
                for r in rows
            ]
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=2)
        elif formato == "html":
            import html as _html
            lineas = ["<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'><title>Chat</title></head><body>"]
            for r in rows:
                quien = "Tú" if r[4] else (r[1] or "Desconocido")
                texto = _html.escape(r[2] or (f"[{r[5]}]" if r[5] else ""))
                lineas.append(f"<p><strong>{_html.escape(quien)}</strong> ({_html.escape(str(r[3]))}): {texto}</p>")
            lineas.append("</body></html>")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write("\n".join(lineas))
        else:  # txt
            with open(ruta, "w", encoding="utf-8") as f:
                for r in rows:
                    quien = "Tú" if r[4] else (r[1] or "Desconocido")
                    texto = r[2] or (f"[{r[5]}]" if r[5] else "")
                    f.write(f"[{r[3]}] {quien}: {texto}\n")
        return ruta

    # --- CIERRE DE SESIÓN ---
    def cerrar_sesion(self, account_id: int) -> bool:
        """Cierra la sesión en Telegram (revoca la autorización), borra el archivo de sesión y la
        cuenta local. Devuelve True si la sesión se REVOCÓ en el servidor; False si no se pudo (por
        ejemplo sin red): en ese caso la cuenta se quita igualmente de este equipo, pero la
        autorización SIGUE activa en Telegram y hay que revocarla desde Ajustes → Dispositivos
        (TG-022: no presentar como revocado algo que quizá no lo está)."""
        async def _coro():
            client = self._clients.get(account_id)
            if client is None:
                client = self._nuevo_cliente(account_id)
                await client.start()
            await client.log_out()
            return True

        revocada = False
        try:
            revocada = bool(self._run(_coro(), timeout=30))
        except Exception as e:
            log.warning("Telegram: no se pudo revocar la sesión en el servidor (se quitará igualmente "
                        "del equipo; sigue activa hasta revocarla en Ajustes → Dispositivos): %s", e)

        with self._lock:
            self._clients.pop(account_id, None)
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        db.execute("DELETE FROM telegram_chats WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM telegram_messages WHERE account_id = ?", (account_id,))

        self._borrar_sesion_local(account_id)
        return revocada


    # --- CHATS DE VOZ (PyTgCalls) [EXPERIMENTAL] ---
    def _obtener_calls(self, account_id: int):
        """Devuelve (creando si hace falta) la instancia PyTgCalls para la cuenta."""
        with self._lock:
            if account_id in self._calls:
                return self._calls[account_id]

        async def _coro():
            from pytgcalls import PyTgCalls
            client = await self._cliente(account_id)
            calls = PyTgCalls(client)
            await calls.start()
            return calls

        calls = self._run(_coro())
        with self._lock:
            self._calls[account_id] = calls
        return calls

    def unirse_llamada_voz(self, account_id: int, chat_id: int, ruta_audio: Optional[str] = None):
        """
        Se une al chat de voz de un grupo/canal. Si se indica ruta_audio,
        reproduce ese archivo; si no, entra en silencio listo para hablar.
        EXPERIMENTAL: requiere PyTgCalls y ffmpeg instalados.
        """
        calls = self._obtener_calls(account_id)

        async def _coro():
            from pytgcalls.types import MediaStream
            try:
                if ruta_audio:
                    await calls.play(chat_id, MediaStream(ruta_audio))
                else:
                    # Entrar al chat de voz sin emitir audio aún (reproduce un WAV de silencio).
                    from pytgcalls.types import MediaStream as MS
                    await calls.play(chat_id, MS(self._silencio_wav()))
            except NotImplementedError:
                raise TelegramError(_("Este sistema no soporta la librería de llamadas de voz "
                                      "(ntgcalls). No se puede entrar al chat de voz aquí."))
            return True

        return self._run(_coro(), timeout=120)

    def hablar_microfono(self, account_id: int, chat_id: int, dispositivo: Optional[str] = None):
        """
        Transmite el micrófono al chat de voz mediante ffmpeg (entrada dshow en
        Windows). EXPERIMENTAL: requiere ffmpeg y el nombre correcto del
        dispositivo de audio. Si no se indica, usa 'default'.
        """
        calls = self._obtener_calls(account_id)

        async def _coro():
            from pytgcalls.types import MediaStream, AudioQuality
            entrada = f"audio={dispositivo}" if dispositivo else "audio=default"
            try:
                # ffmpeg dshow para capturar el micrófono en Windows
                await calls.play(
                    chat_id,
                    MediaStream(entrada, audio_parameters=AudioQuality.HIGH,
                                ffmpeg_parameters="-f dshow")
                )
            except NotImplementedError:
                # ntgcalls no soporta capturar el micrófono en vivo en este equipo: no es un fallo
                # nuestro, así que damos un mensaje claro en vez del críptico "NotImplementedError".
                raise TelegramError(_("La emisión del micrófono en tiempo real no está disponible en "
                                      "este sistema. Sigues en el chat de voz (solo escucha)."))
            return True

        return self._run(_coro(), timeout=120)

    def silenciar_llamada(self, account_id: int, chat_id: int, silenciar: bool = True):
        """Silencia o reactiva el micrófono en el chat de voz."""
        calls = self._calls.get(account_id)
        if not calls:
            raise TelegramError("No estás en ningún chat de voz.")

        async def _coro():
            if silenciar:
                return await calls.mute(chat_id)
            return await calls.unmute(chat_id)

        return self._run(_coro())

    def salir_llamada_voz(self, account_id: int, chat_id: int):
        """Abandona el chat de voz."""
        calls = self._calls.get(account_id)
        if not calls:
            return

        async def _coro():
            return await calls.leave_call(chat_id)

        return self._run(_coro())

    def _silencio_wav(self) -> str:
        """Genera (una sola vez) un WAV de silencio para entrar al chat de voz sin hablar."""
        ruta = get_data_path("temp") / "tg_silencio.wav"
        if not ruta.exists():
            import wave
            import struct
            with wave.open(str(ruta), "w") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(48000)
                for _ in range(48000):  # 1 segundo de silencio
                    w.writeframes(struct.pack("<h", 0))
        return str(ruta)


    def apagar(self):
        """
        Cierra ordenadamente Telegram al salir: detiene chats de voz, desconecta
        clientes y para el bucle asyncio. Necesario porque PyTgCalls/ntgcalls
        levantan hilos nativos que impedían que la aplicación terminara sola.
        """
        # Cerrar el servidor de streaming local si se llegó a levantar.
        try:
            from app.services.telegram.telegram_stream_server import stream_server
            stream_server.cerrar()
        except Exception:
            pass

        if self._loop is None or not self._loop.is_running():
            return

        async def _cerrar():
            for calls in list(self._calls.values()):
                try:
                    await calls.stop()
                except Exception:
                    pass
            for client in list(self._clients.values()):
                try:
                    if client.is_connected:
                        await client.stop()
                except Exception:
                    pass

        try:
            fut = asyncio.run_coroutine_threadsafe(_cerrar(), self._loop)
            fut.result(timeout=10)
        except Exception as e:
            log.debug(f"Error al cerrar Telegram: {e}")
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=5)
        except Exception as e:
            log.debug(f"Error al detener el bucle de Telegram: {e}")
        with self._lock:
            self._clients.clear()
            self._calls.clear()
