# -*- coding: utf-8 -*-
"""Interacción accesible con BOTS de Telegram (mixin de TelegramService).

Cubre todo lo que la librería (Kurigram/Pyrogram) permite hacer a un cliente de USUARIO frente a
un bot, alineado con la API oficial de MTProto:
- Teclados de los mensajes: inline (callback, url, copiar texto, inline query, login, web app…) y
  de respuesta (botones que envían texto, piden contacto/ubicación/encuesta).
- Pulsar botones y leer la RESPUESTA del bot (notificación/alerta/enlace).
- Comandos del bot (los que publica el propio bot) y ejecutarlos; iniciar el bot (/start) con o sin
  parámetro de enlace profundo.
- Modo inline: consultar a un bot (@bot consulta) y enviar uno de sus resultados.
"""
from app.services.telegram.telegram_common import (Optional, db, log, TelegramError)


class _BotsMixin:
    # ------------------------------------------------------------------
    # SERIALIZACIÓN DE TECLADOS (para guardar y mostrar los botones)
    # ------------------------------------------------------------------
    @staticmethod
    def _boton_inline_a_dict(b) -> dict:
        """Convierte un InlineKeyboardButton en un dict legible {text, kind, valor}."""
        d = {"text": str(getattr(b, "text", "") or ""), "kind": "otro", "valor": ""}
        if getattr(b, "callback_data", None) is not None:
            d["kind"] = "callback"
        elif getattr(b, "url", None):
            d["kind"] = "url"; d["valor"] = b.url
        elif getattr(b, "login_url", None):
            d["kind"] = "login"; d["valor"] = getattr(b.login_url, "url", "") or ""
        elif getattr(b, "web_app", None):
            d["kind"] = "webapp"; d["valor"] = getattr(b.web_app, "url", "") or ""
        elif getattr(b, "copy_text", None):
            d["kind"] = "copiar"; d["valor"] = str(b.copy_text)
        elif getattr(b, "switch_inline_query_current_chat", None) is not None:
            d["kind"] = "inline_aqui"; d["valor"] = str(b.switch_inline_query_current_chat)
        elif getattr(b, "switch_inline_query", None) is not None:
            d["kind"] = "inline_otro"; d["valor"] = str(b.switch_inline_query)
        elif getattr(b, "user_id", None):
            d["kind"] = "usuario"; d["valor"] = str(b.user_id)
        elif getattr(b, "callback_game", None):
            d["kind"] = "juego"
        elif getattr(b, "pay", None):
            d["kind"] = "pago"
        return d

    @staticmethod
    def _boton_reply_a_dict(b) -> dict:
        """Convierte un KeyboardButton (teclado de respuesta) en un dict legible."""
        d = {"text": str(getattr(b, "text", "") or ""), "kind": "texto", "valor": ""}
        if getattr(b, "request_contact", None):
            d["kind"] = "pide_contacto"
        elif getattr(b, "request_location", None):
            d["kind"] = "pide_ubicacion"
        elif getattr(b, "request_poll", None):
            d["kind"] = "pide_encuesta"
        elif getattr(b, "request_users", None):
            d["kind"] = "pide_usuarios"
        elif getattr(b, "request_chat", None):
            d["kind"] = "pide_chat"
        elif getattr(b, "web_app", None):
            d["kind"] = "webapp"; d["valor"] = getattr(b.web_app, "url", "") or ""
        return d

    def _serializar_reply_markup(self, msg) -> Optional[str]:
        """Serializa a JSON el teclado de un mensaje (inline o de respuesta), o None si no tiene.
        Se guarda en telegram_messages.reply_markup para mostrar los botones sin volver a la red."""
        markup = getattr(msg, "reply_markup", None)
        if markup is None:
            return None
        try:
            import json
            from pyrogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
            filas = []
            if isinstance(markup, InlineKeyboardMarkup):
                tipo = "inline"
                for row in (markup.inline_keyboard or []):
                    filas.append([self._boton_inline_a_dict(b) for b in row])
            elif isinstance(markup, ReplyKeyboardMarkup):
                tipo = "reply"
                for row in (markup.keyboard or []):
                    filas.append([self._boton_reply_a_dict(b) for b in row])
            else:
                return None
            if not any(filas):
                return None
            return json.dumps({"tipo": tipo, "filas": filas}, ensure_ascii=False)
        except Exception as e:
            log.debug(f"No se pudo serializar el teclado del mensaje: {e}")
            return None

    # ------------------------------------------------------------------
    # LEER LOS BOTONES DE UN MENSAJE
    # ------------------------------------------------------------------
    def obtener_botones(self, account_id: int, chat_id: int, message_id: int) -> Optional[dict]:
        """Devuelve el teclado del mensaje {tipo: 'inline'|'reply', botones: [ {text, kind, valor}... ]}
        en orden de lectura (aplanado), o None si no tiene botones. Lo lee EN VIVO (el bot puede
        haber editado el teclado) y, si falla, recurre a lo guardado en la caché."""
        async def _coro():
            client = await self._cliente(account_id)
            msg = await client.get_messages(chat_id, message_id)
            return self._serializar_reply_markup(msg) if msg else None

        cruda = None
        try:
            cruda = self._run(_coro(), timeout=30)
        except Exception as e:
            log.debug(f"obtener_botones en vivo falló: {e}")
        if not cruda:
            fila = db.query_one(
                "SELECT reply_markup FROM telegram_messages WHERE id = ?",
                (f"{account_id}:{chat_id}:{message_id}",))
            cruda = fila[0] if fila and fila[0] else None
        if not cruda:
            return None
        try:
            import json
            datos = json.loads(cruda)
        except Exception:
            return None
        botones = [b for fila in datos.get("filas", []) for b in fila]
        if not botones:
            return None
        return {"tipo": datos.get("tipo", "inline"), "botones": botones}

    # ------------------------------------------------------------------
    # PULSAR UN BOTÓN
    # ------------------------------------------------------------------
    def pulsar_boton(self, account_id: int, chat_id: int, message_id: int, indice: int,
                     password: Optional[str] = None) -> dict:
        """Pulsa el botón número `indice` (en orden de lectura) del teclado del mensaje y devuelve
        un dict con el RESULTADO para que la interfaz actúe:
        - callback: {tipo:'callback', texto, alerta(bool), url}
        - url/login/webapp: {tipo:'url', url, login(bool)}
        - copiar: {tipo:'copiar', texto}
        - inline_aqui/inline_otro: {tipo:'inline_aqui'|'inline_otro', consulta}
        - usuario: {tipo:'usuario', user_id}
        - teclado de respuesta de texto: {tipo:'enviado', texto}
        - pedir contacto/ubicación/encuesta: {tipo:'pide_contacto'|'pide_ubicacion'|'pide_encuesta', texto}
        """
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
            msg = await client.get_messages(chat_id, message_id)
            markup = getattr(msg, "reply_markup", None) if msg else None
            if isinstance(markup, InlineKeyboardMarkup):
                botones = [b for row in markup.inline_keyboard for b in row]
                if not (0 <= indice < len(botones)):
                    raise TelegramError("Ese botón ya no existe.")
                b = botones[indice]
                if getattr(b, "callback_data", None) is not None:
                    kw = {"timeout": 15}
                    if password is not None:
                        kw["password"] = password
                    ans = await client.request_callback_answer(
                        chat_id, message_id, b.callback_data, **kw)
                    return {"tipo": "callback",
                            "texto": getattr(ans, "message", "") or "",
                            "alerta": bool(getattr(ans, "alert", False)),
                            "url": getattr(ans, "url", "") or ""}
                if getattr(b, "url", None):
                    return {"tipo": "url", "url": b.url, "login": False}
                if getattr(b, "login_url", None):
                    return {"tipo": "url", "url": getattr(b.login_url, "url", "") or "", "login": True}
                if getattr(b, "web_app", None):
                    return {"tipo": "url", "url": getattr(b.web_app, "url", "") or "", "login": False}
                if getattr(b, "copy_text", None):
                    return {"tipo": "copiar", "texto": str(b.copy_text)}
                if getattr(b, "switch_inline_query_current_chat", None) is not None:
                    return {"tipo": "inline_aqui", "consulta": str(b.switch_inline_query_current_chat)}
                if getattr(b, "switch_inline_query", None) is not None:
                    return {"tipo": "inline_otro", "consulta": str(b.switch_inline_query)}
                if getattr(b, "user_id", None):
                    return {"tipo": "usuario", "user_id": b.user_id}
                if getattr(b, "requires_password", None):
                    return {"tipo": "necesita_password"}
                return {"tipo": "no_soportado"}
            elif isinstance(markup, ReplyKeyboardMarkup):
                botones = [b for row in markup.keyboard for b in row]
                if not (0 <= indice < len(botones)):
                    raise TelegramError("Ese botón ya no existe.")
                b = botones[indice]
                if getattr(b, "request_contact", None):
                    # CONSENTIMIENTO (TG-011): compartir TU número de teléfono es revelar un dato
                    # personal. NO se envía aquí; se devuelve una intención para que la UI confirme
                    # mostrando el número exacto, y solo tras el "sí" se llama a compartir_mi_contacto.
                    me = await client.get_me()
                    tel = getattr(me, "phone_number", None)
                    if tel:
                        return {"tipo": "requiere_consentimiento_contacto",
                                "telefono": str(tel), "texto": b.text}
                    return {"tipo": "pide_contacto", "texto": b.text}
                if getattr(b, "request_location", None):
                    return {"tipo": "pide_ubicacion", "texto": b.text}
                if getattr(b, "request_poll", None):
                    return {"tipo": "pide_encuesta", "texto": b.text}
                # Botón de texto normal: enviar su texto al chat (es lo que hace el botón).
                await client.send_message(chat_id, b.text)
                return {"tipo": "enviado", "texto": b.text}
            else:
                raise TelegramError("Este mensaje ya no tiene botones.")

        return self._run(_coro(), timeout=30)

    def compartir_mi_contacto(self, account_id: int, chat_id: int):
        """Envía TU contacto (teléfono y nombre) al chat. Se llama SOLO tras la confirmación
        explícita del usuario (TG-011): nunca de forma automática al pulsar el botón."""
        async def _coro():
            client = await self._cliente(account_id)
            me = await client.get_me()
            tel = getattr(me, "phone_number", None)
            if not tel:
                raise TelegramError("No hay un número de teléfono asociado a esta cuenta.")
            await client.send_contact(chat_id, str(tel), me.first_name or "",
                                      last_name=me.last_name or None)
            return True

        return self._run(_coro(), timeout=30)

    # ------------------------------------------------------------------
    # COMANDOS DEL BOT
    # ------------------------------------------------------------------
    def obtener_comandos_bot(self, account_id: int, chat_id: int) -> list:
        """Devuelve la lista de comandos que publica un bot [{comando, descripcion}], leída de la
        información del bot (users.getFullUser → bot_info.commands). Vacía si no es un bot o no
        publica comandos."""
        async def _coro():
            client = await self._cliente(account_id)
            from pyrogram import raw
            peer = await client.resolve_peer(chat_id)
            full = await client.invoke(raw.functions.users.GetFullUser(id=peer))
            fu = getattr(full, "full_user", None)
            bot_info = getattr(fu, "bot_info", None)
            cmds = getattr(bot_info, "commands", None) or []
            return [{"comando": getattr(c, "command", "") or "",
                     "descripcion": getattr(c, "description", "") or ""} for c in cmds]
        return self._run(_coro(), timeout=30)

    def enviar_comando(self, account_id: int, chat_id: int, comando: str):
        """Envía un comando al bot (p. ej. 'help' → '/help')."""
        cmd = (comando or "").strip()
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        return self.enviar_mensaje(account_id, chat_id, cmd)

    def iniciar_bot(self, account_id: int, chat_id: int, parametro: str = ""):
        """Inicia el bot con /start, opcionalmente con un parámetro de enlace profundo (deep link)."""
        texto = "/start" + (f" {parametro.strip()}" if parametro and parametro.strip() else "")
        return self.enviar_mensaje(account_id, chat_id, texto)

    # ------------------------------------------------------------------
    # MODO INLINE (@bot consulta)
    # ------------------------------------------------------------------
    def consultar_inline(self, account_id: int, bot: str, consulta: str, offset: str = "") -> dict:
        """Consulta a un bot en modo inline (@bot consulta). Devuelve
        {query_id, resultados: [{id, titulo, descripcion, tipo}], siguiente_offset}."""
        async def _coro():
            client = await self._cliente(account_id)
            res = await client.get_inline_bot_results(bot, query=consulta or "", offset=offset or "")
            salida = []
            for r in (getattr(res, "results", None) or []):
                salida.append({
                    "id": str(getattr(r, "id", "") or ""),
                    "titulo": getattr(r, "title", None) or "",
                    "descripcion": getattr(r, "description", None) or "",
                    "tipo": getattr(r, "type", None) or "",
                })
            return {"query_id": getattr(res, "query_id", None),
                    "resultados": salida,
                    "siguiente_offset": getattr(res, "next_offset", "") or ""}
        return self._run(_coro(), timeout=30)

    def enviar_resultado_inline(self, account_id: int, chat_id: int, query_id, result_id: str,
                                reply_to: Optional[int] = None):
        """Envía al chat uno de los resultados de una consulta inline (send_inline_bot_result)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.send_inline_bot_result(
                chat_id, query_id, result_id, reply_to_message_id=reply_to)
        self._run(_coro(), timeout=60)
        return True

    # ------------------------------------------------------------------
    # GESTIÓN DE BOTS PROPIOS (los que tú posees)
    # ------------------------------------------------------------------
    BOTFATHER_ID = 93372553  # id de @BotFather, necesario para crear bots

    def mis_bots(self, account_id: int) -> list:
        """Lista los bots que posees (get_owned_bots). Devuelve [(id, nombre, @usuario), ...]."""
        async def _coro():
            client = await self._cliente(account_id)
            bots = await client.get_owned_bots()
            res = []
            for b in (bots or []):
                usuario = getattr(b, "username", None)
                nombre = getattr(b, "first_name", None) or (usuario and f"@{usuario}") or str(b.id)
                res.append((b.id, nombre, f"@{usuario}" if usuario else ""))
            return res
        return self._run(_coro())

    def crear_bot(self, account_id: int, nombre: str, username: str):
        """Crea un bot nuevo a través de BotFather (create_bot). username debe acabar en 'bot'."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.create_bot(self.BOTFATHER_ID, nombre, username)
        return self._run(_coro(), timeout=120)

    def obtener_nombre_bot(self, account_id: int, bot_id) -> str:
        async def _coro():
            client = await self._cliente(account_id)
            return await client.get_bot_name(for_my_bot=bot_id)
        return self._run(_coro())

    def fijar_nombre_bot(self, account_id: int, bot_id, nombre: str):
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_bot_name(nombre, for_my_bot=bot_id)
        return self._run(_coro())

    def fijar_descripcion_bot(self, account_id: int, bot_id, descripcion: str):
        """Descripción larga del bot (lo que se ve antes de iniciarlo)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_bot_info_description(descripcion, for_my_bot=bot_id)
        return self._run(_coro())

    def fijar_descripcion_corta_bot(self, account_id: int, bot_id, descripcion: str):
        """Descripción corta del bot (la que aparece en su perfil)."""
        async def _coro():
            client = await self._cliente(account_id)
            return await client.set_bot_info_short_description(descripcion, for_my_bot=bot_id)
        return self._run(_coro())
