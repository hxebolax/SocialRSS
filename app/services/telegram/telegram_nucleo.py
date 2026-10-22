# -*- coding: utf-8 -*-
"""Bucle asyncio, credenciales API, login (código/QR).

Mixin de TelegramService (parte de la refactorización modular).
"""
from app.services.telegram.telegram_common import (
    asyncio, sqlite3, threading, time, Any, Dict, db,
    security, get_data_path, log, HAS_KURIGRAM,
    Client, TelegramError)


class _NucleoMixin:
    # --- GESTIÓN DEL BUCLE ASYNCIO ---
    def _ensure_loop(self):
        """Arranca el bucle de eventos de Telegram en un hilo dedicado si aún no existe."""
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="TelegramLoop", daemon=True
            )
            self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout: float = 60.0):
        """Ejecuta una corrutina en el bucle de Telegram y espera su resultado (bloqueante)."""
        if not HAS_KURIGRAM:
            raise TelegramError(
                "Las dependencias de Telegram (kurigram) no están instaladas. "
                "Ejecute la instalación de requisitos desde build.bat."
            )
        self._ensure_loop()
        # pyrogram ya está cargado aquí (ejecutar una corrutina requiere un Client). Import local
        # de la excepción de 2FA (la carga de pyrogram es diferida; ver telegram_common).
        from pyrogram.errors import SessionPasswordNeeded
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # Al agotarse el plazo hay que CANCELAR la corrutina: si se deja viva puede
            # terminar después en silencio (un mensaje "fallido" que en realidad se envió,
            # y que el usuario repite pensando que no salió).
            future.cancel()
            raise TelegramError(
                "La operación tardó demasiado y se canceló. Comprueba en el chat si "
                "llegó a completarse antes de repetirla.")
        except SessionPasswordNeeded:
            # Necesario propagarla tal cual para el flujo de 2FA
            raise
        except TelegramError:
            raise
        except Exception as e:
            # Traducir cualquier error de Telegram a un mensaje claro para el usuario
            raise TelegramError(self._mensaje_error_telegram(e))

    # --- CREDENCIALES DE API ---
    @staticmethod
    def get_api_credentials() -> tuple:
        """Devuelve (api_id, api_hash) almacenados de forma segura, o (None, None)."""
        api_id = security.obtener_credencial("telegram", "api_id")
        api_hash = security.obtener_credencial("telegram", "api_hash")
        return api_id, api_hash

    @staticmethod
    def set_api_credentials(api_id: str, api_hash: str):
        """Guarda las credenciales de API de Telegram de forma segura."""
        security.guardar_credencial("telegram", "api_id", str(api_id).strip())
        security.guardar_credencial("telegram", "api_hash", str(api_hash).strip())

    @staticmethod
    def credenciales_configuradas() -> bool:
        api_id, api_hash = _NucleoMixin.get_api_credentials()
        return bool(api_id) and bool(api_hash)

    @staticmethod
    def _normalizar_telefono(phone: str) -> str:
        """Normaliza un telefono a formato internacional +<digitos>."""
        texto = str(phone or "").strip()
        if texto.startswith("00"):
            texto = "+" + texto[2:]
        digitos = "".join(ch for ch in texto if ch.isdigit())
        if not digitos:
            raise TelegramError("Introduzca el numero de telefono con prefijo internacional, por ejemplo +34600112233.")
        if len(digitos) < 8 or len(digitos) > 15:
            raise TelegramError("El numero de telefono no parece valido. Use formato internacional, por ejemplo +34600112233.")
        return f"+{digitos}"

    @staticmethod
    def _telefono_enmascarado(phone: str) -> str:
        digitos = "".join(ch for ch in str(phone or "") if ch.isdigit())
        if not digitos:
            return "<vacio>"
        if len(digitos) <= 4:
            return "+" + ("*" * len(digitos))
        prefijo = digitos[:2]
        sufijo = digitos[-2:]
        return "+" + prefijo + ("*" * max(1, len(digitos) - 4)) + sufijo

    @staticmethod
    def _credenciales_api_validadas() -> tuple[int, str]:
        api_id, api_hash = _NucleoMixin.get_api_credentials()
        api_id = str(api_id or "").strip()
        api_hash = str(api_hash or "").strip()
        if not api_id or not api_hash:
            raise TelegramError("Configure primero api_id y api_hash en Preferencias -> Telegram.")
        if not api_id.isdigit():
            raise TelegramError("El api_id de Telegram debe ser numerico. Reviselo en Preferencias -> Telegram.")
        hash_hex32 = len(api_hash) == 32 and all(c in "0123456789abcdefABCDEF" for c in api_hash)
        if not hash_hex32:
            raise TelegramError("El api_hash de Telegram no tiene el formato esperado. Debe ser una cadena hexadecimal de 32 caracteres.")
        log.info(
            "Telegram credentials loaded api_id_digits=%s api_hash_len=%s api_hash_hex32=%s",
            len(api_id),
            len(api_hash),
            hash_hex32,
        )
        return int(api_id), api_hash

    def _sessions_dir(self) -> str:
        ruta = get_data_path("sessions")
        ruta.mkdir(parents=True, exist_ok=True)
        return str(ruta)

    def _nuevo_cliente(self, account_id: int):
        """Crea (sin conectar) una instancia de Client para la cuenta indicada."""
        api_id, api_hash = self._credenciales_api_validadas()
        return Client(
            name=f"tg_{account_id}",
            api_id=api_id,
            api_hash=api_hash,
            workdir=self._sessions_dir(),
            app_version="SocialRSS Suite",
            device_model="PC",
            system_version="Windows",
            # kurigram serializa subidas/descargas con un semáforo cuyo tamaño es este valor
            # (por defecto 1). Lo subimos a 10 para permitir transferencias SIMULTÁNEAS (p. ej.
            # varias subidas de Telegram Drive a la vez). El número real de simultáneas lo limita
            # la cola del Drive según Preferencias (1..10); esto solo eleva el techo del cliente.
            max_concurrent_transmissions=10,
        )

    @staticmethod
    def _nombre_enum(valor) -> str:
        """Devuelve el nombre estable de un enum de Pyrogram/Kurigram."""
        if valor is None:
            return ""
        nombre = getattr(valor, "name", "")
        if nombre:
            return str(nombre).lower()
        texto = str(valor).split(".")[-1]
        return texto.lower()

    @classmethod
    def _descripcion_tipo_codigo(cls, tipo) -> str:
        """Convierte SentCodeType/NextCodeType a una descripcion para el usuario."""
        nombre = cls._nombre_enum(tipo)
        if nombre == "app":
            return (
                "la aplicacion de Telegram. Revisa el chat oficial "
                "\"Telegram\" en otro movil, PC o web donde ya tengas sesion"
            )
        if nombre in {"sms", "sms_phrase", "sms_word", "firebase_sms", "fragment_sms"}:
            return "un SMS"
        if nombre == "call":
            return "una llamada telefonica"
        if nombre == "flash_call":
            return "una llamada flash"
        if nombre == "missed_call":
            return "una llamada perdida"
        if nombre == "email_code":
            return "el correo electronico asociado a Telegram"
        if nombre == "setup_email_required":
            return "Telegram, pero esta cuenta requiere configurar un correo de verificacion"
        return "Telegram"

    @classmethod
    def _info_codigo(cls, sent) -> Dict[str, Any]:
        """Extrae los datos importantes de un SentCode de Kurigram."""
        timeout = getattr(sent, "timeout", None) or 0
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = 0
        siguiente = getattr(sent, "next_type", None)
        return {
            "phone_code_hash": getattr(sent, "phone_code_hash", ""),
            "destino": cls._descripcion_tipo_codigo(getattr(sent, "type", None)),
            "tipo_codigo": cls._nombre_enum(getattr(sent, "type", None)),
            "siguiente_destino": cls._descripcion_tipo_codigo(siguiente) if siguiente else "",
            "siguiente_tipo": cls._nombre_enum(siguiente),
            "timeout": timeout,
            "puede_reenviar": bool(siguiente),
        }

    @staticmethod
    def _mensaje_error_telegram(error: Exception) -> str:
        """Traduce errores comunes de Telegram a mensajes accionables."""
        nombre = error.__class__.__name__
        if nombre in {"PhoneCodeInvalid", "PhoneCodeEmpty"}:
            return "El codigo introducido no es valido. Revise que sea el ultimo codigo recibido y escribalo sin espacios."
        if nombre in {"PhoneCodeExpired", "PhoneHashExpired", "PhoneCodeHashEmpty"}:
            return "El codigo ha caducado. Pulse Reenviar codigo para pedir uno nuevo."
        if nombre == "PhoneNumberInvalid":
            return "El numero de telefono no es valido. Escribalo con prefijo internacional, por ejemplo +34600112233."
        if nombre == "PhoneNumberBanned":
            return "Telegram ha bloqueado ese numero para iniciar sesion."
        if nombre == "PhoneNumberFlood":
            return "Se han solicitado demasiados codigos para este numero. Espere antes de volver a intentarlo."
        if nombre == "FloodWait":
            segundos = getattr(error, "value", None)
            if segundos:
                return f"Telegram pide esperar {segundos} segundos antes de repetir esta acción. Inténtelo de nuevo pasado ese tiempo."
            return "Telegram pide esperar un momento antes de repetir esta acción."
        if nombre in {"ApiIdInvalid", "ApiIdPublishedFlood"}:
            return "El api_id o api_hash no son validos. Revise las credenciales en Preferencias, pestana Telegram."
        if nombre in {"PremiumAccountRequired", "UserPremiumRequired"} or "PREMIUM" in str(error).upper():
            return "Esta función requiere una cuenta de Telegram Premium. Esa reacción o acción solo está disponible para usuarios Premium."
        if nombre in {"ReactionInvalid", "ReactionEmpty"}:
            return "Esa reacción no está permitida en este chat."
        if nombre == "ChatAdminRequired":
            return "Necesita ser administrador del grupo o canal para hacer esto."
        if nombre == "MessageDeleteForbidden":
            return "No tiene permiso para borrar este mensaje."
        if nombre == "TimeoutError":
            return "Telegram tardo demasiado en responder. Compruebe la conexion e intentelo de nuevo."
        texto = str(error).strip()
        return texto or nombre

    def _borrar_sesion_local(self, account_id: int):
        """Borra archivos de sesion provisionales asociados a una cuenta local."""
        base = get_data_path("sessions")
        for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
            ruta = base / f"tg_{account_id}{suffix}"
            try:
                if ruta.exists():
                    ruta.unlink()
            except Exception as e:
                log.debug(f"No se pudo borrar sesion provisional {ruta}: {e}")

    def _sesion_local_autorizada(self, account_id: int) -> bool:
        """Indica si un archivo de sesion local tiene usuario autenticado."""
        ruta = get_data_path("sessions") / f"tg_{account_id}.session"
        if not ruta.exists():
            return False
        try:
            con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
            try:
                row = con.execute("SELECT user_id FROM sessions LIMIT 1").fetchone()
            finally:
                con.close()
            return bool(row and row[0])
        except Exception as e:
            log.debug(f"No se pudo inspeccionar sesion local {ruta}: {e}")
            return True

    def _limpiar_sesiones_huerfanas(self):
        """Elimina sesiones tg_N no autorizadas que ya no tienen cuenta local."""
        base = get_data_path("sessions")
        if not base.exists():
            return
        ids_cuenta = {
            int(row[0])
            for row in db.query("SELECT id FROM accounts WHERE service_id = 'telegram'")
        }
        with self._lock:
            ids_pendientes = set(self._pending)
        for ruta in base.glob("tg_*.session"):
            try:
                account_id = int(ruta.stem.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            if account_id in ids_cuenta or account_id in ids_pendientes:
                continue
            if self._sesion_local_autorizada(account_id):
                log.warning(
                    "Sesion local tg_%s parece autorizada pero no tiene cuenta local; no se borra automaticamente.",
                    account_id,
                )
                continue
            self._borrar_sesion_local(account_id)
            log.info("Sesion provisional huerfana de Telegram eliminada: tg_%s", account_id)

    def _limpiar_logins_provisionales(self, phone: str):
        """Elimina intentos incompletos anteriores para el mismo telefono."""
        rows = db.query(
            "SELECT id FROM accounts WHERE service_id = 'telegram' AND name = ?",
            (phone,)
        )
        for (account_id,) in rows:
            with self._lock:
                pend = self._pending.pop(account_id, None)
                cliente_activo = account_id in self._clients
            if cliente_activo:
                continue
            if pend:
                try:
                    self._run(pend["client"].disconnect(), timeout=15)
                except Exception:
                    pass
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._borrar_sesion_local(account_id)

    # --- LOGIN POR FASES (teléfono -> código -> 2FA) ---
    def iniciar_login(self, phone: str) -> Dict[str, Any]:
        """
        Crea una cuenta provisional, conecta y envía el código al teléfono.
        Devuelve {account_id, phone_code_hash}.
        """
        phone = self._normalizar_telefono(phone)
        log.info("Telegram login start phone=%s", self._telefono_enmascarado(phone))
        self._limpiar_sesiones_huerfanas()
        self._limpiar_logins_provisionales(phone)
        self._limpiar_cuentas_provisionales_inactivas()

        # 1. Crear fila de cuenta provisional para obtener un account_id estable. Posición al
        #    final (max+1) para no alterar el orden del gestor al añadir una cuenta nueva.
        account_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) "
            "VALUES (?, 'telegram', 0, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))",
            (phone,)
        )

        async def _coro():
            client = self._nuevo_cliente(account_id)
            try:
                await client.connect()
                # IMPORTANTE: no solicitar entrega por llamada (flash/missed call).
                # Con los ajustes por defecto, Telegram envía el código a la propia
                # aplicación de Telegram (chat oficial) o por SMS, que es lo que el
                # usuario espera recibir. Pedir flash/missed call hacía que no
                # llegara nada visible a las apps de Telegram.
                sent = await client.send_code(phone)
                return client, sent
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise

        try:
            client, sent = self._run(_coro())
        except Exception as e:
            # Revertir la cuenta provisional si falla el envío del código
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._borrar_sesion_local(account_id)
            raise TelegramError(f"No se pudo enviar el código: {self._mensaje_error_telegram(e)}")

        info = self._info_codigo(sent)
        log.info(
            "Telegram send_code account_id=%s tipo=%s destino=%s siguiente=%s timeout=%s",
            account_id,
            info.get("tipo_codigo"),
            info.get("destino"),
            info.get("siguiente_tipo") or "",
            info.get("timeout"),
        )

        with self._lock:
            self._pending[account_id] = {
                "client": client,
                "phone": phone,
                "hash": info["phone_code_hash"],
                "last_sent": info,
                "resend_after": time.monotonic() + info["timeout"] if info["timeout"] else 0,
            }
        info["account_id"] = account_id
        return info

    def reenviar_codigo(self, account_id: int) -> Dict[str, Any]:
        """Solicita a Telegram reenviar el código usando el siguiente método disponible."""
        pend = self._pending.get(account_id)
        if not pend:
            raise TelegramError("No hay un proceso de login en curso para esta cuenta.")

        last_sent = pend.get("last_sent") or {}
        if not last_sent.get("puede_reenviar"):
            raise TelegramError("Telegram no ha ofrecido otro método de envío para este intento.")

        restante = int(max(0, pend.get("resend_after", 0) - time.monotonic()))
        if restante > 0:
            raise TelegramError(f"Espere {restante} segundos antes de pedir otro código.")

        client = pend["client"]

        async def _coro():
            return await client.resend_code(pend["phone"], pend["hash"])

        try:
            sent = self._run(_coro())
        except Exception as e:
            raise TelegramError(f"No se pudo reenviar el código: {self._mensaje_error_telegram(e)}")

        info = self._info_codigo(sent)
        log.info(
            "Telegram resend_code account_id=%s tipo=%s destino=%s siguiente=%s timeout=%s",
            account_id,
            info.get("tipo_codigo"),
            info.get("destino"),
            info.get("siguiente_tipo") or "",
            info.get("timeout"),
        )
        with self._lock:
            pend["hash"] = info["phone_code_hash"]
            pend["last_sent"] = info
            pend["resend_after"] = time.monotonic() + info["timeout"] if info["timeout"] else 0
        info["account_id"] = account_id
        return info

    def completar_codigo(self, account_id: int, code: str) -> str:
        """
        Verifica el código recibido. Devuelve 'ok' si inició sesión o
        'password' si se requiere la contraseña de verificación en dos pasos.
        """
        pend = self._pending.get(account_id)
        if not pend:
            raise TelegramError("No hay un proceso de login en curso para esta cuenta.")
        client = pend["client"]
        from pyrogram.errors import SessionPasswordNeeded  # pyrogram ya cargado (hay Client de login)

        async def _coro():
            try:
                await client.sign_in(pend["phone"], pend["hash"], code)
                return "ok"
            except SessionPasswordNeeded:
                return "password"

        try:
            resultado = self._run(_coro())
        except Exception as e:
            raise TelegramError(self._mensaje_error_telegram(e))
        if resultado == "ok":
            self._finalizar_login(account_id)
        return resultado

    def completar_password(self, account_id: int, password: str):
        """Verifica la contraseña de 2FA y finaliza el login."""
        pend = self._pending.get(account_id)
        if not pend:
            raise TelegramError("No hay un proceso de login en curso para esta cuenta.")
        client = pend["client"]

        async def _coro():
            await client.check_password(password)
            return True

        try:
            self._run(_coro())
        except Exception as e:
            raise TelegramError(self._mensaje_error_telegram(e))
        self._finalizar_login(account_id)

    # --- LOGIN POR CÓDIGO QR ---
    @staticmethod
    def _qr_url(token: bytes) -> str:
        """Construye la URL tg://login a partir del token de inicio de sesión."""
        import base64
        b64 = base64.urlsafe_b64encode(token).decode("ascii").rstrip("=")
        return f"tg://login?token={b64}"

    def _limpiar_cuentas_provisionales_inactivas(self):
        """Elimina cuentas de Telegram inactivas (logins incompletos) que no estén en curso."""
        for (aid,) in db.query("SELECT id FROM accounts WHERE service_id = 'telegram' AND active = 0"):
            if aid not in self._pending:
                db.execute("DELETE FROM accounts WHERE id = ?", (aid,))
                self._borrar_sesion_local(aid)

    def iniciar_login_qr(self) -> Dict[str, Any]:
        """
        Crea una cuenta provisional, conecta y exporta un token de inicio de
        sesión para mostrarlo como código QR. Devuelve {account_id, url}.
        """
        self._limpiar_sesiones_huerfanas()
        self._limpiar_cuentas_provisionales_inactivas()
        api_id, api_hash = self._credenciales_api_validadas()

        account_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) "
            "VALUES (?, 'telegram', 0, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))",
            (_("QR pendiente"),)
        )

        async def _coro():
            from pyrogram.raw.functions.auth import ExportLoginToken
            client = self._nuevo_cliente(account_id)
            await client.connect()
            r = await client.invoke(ExportLoginToken(api_id=int(api_id), api_hash=api_hash, except_ids=[]))
            return client, r

        try:
            client, r = self._run(_coro())
        except Exception as e:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._borrar_sesion_local(account_id)
            raise TelegramError(f"No se pudo generar el código QR: {self._mensaje_error_telegram(e)}")

        with self._lock:
            self._pending[account_id] = {"client": client, "phone": None, "qr": True}
        return {"account_id": account_id, "url": self._qr_url(r.token)}

    def comprobar_qr(self, account_id: int) -> Dict[str, Any]:
        """
        Comprueba el estado del login por QR. Devuelve dict con 'estado':
        'pending' (con 'url' quizá renovada), 'ok' o 'password'.
        """
        pend = self._pending.get(account_id)
        if not pend:
            raise TelegramError("No hay un proceso de QR en curso.")
        client = pend["client"]
        from pyrogram.errors import SessionPasswordNeeded  # pyrogram ya cargado (hay Client de login)

        async def _coro():
            from pyrogram.raw.functions.auth import ExportLoginToken, ImportLoginToken
            from pyrogram.raw.types.auth import LoginTokenSuccess, LoginTokenMigrateTo
            api_id, api_hash = self.get_api_credentials()
            try:
                r = await client.invoke(ExportLoginToken(api_id=int(api_id), api_hash=api_hash, except_ids=[]))
            except SessionPasswordNeeded:
                return ("password", None)

            if isinstance(r, LoginTokenMigrateTo):
                # El usuario está en otro centro de datos: migrar e importar el token.
                # Kurigram (pyrogram 2.2.x) exige server_address y port en Auth y
                # Session; la API antigua (solo dc_id) provocaba un TypeError que
                # dejaba el QR colgado en "Esperando..." aunque el móvil ya hubiera
                # autorizado. Resolvemos la dirección del DC destino con get_dc_option.
                from pyrogram.session import Session, Auth
                log.info(f"QR Telegram: migrando al centro de datos {r.dc_id}...")
                dc_option = await client.get_dc_option(r.dc_id, ipv6=client.ipv6)
                server_address = dc_option.ip_address
                port = dc_option.port
                test_mode = await client.storage.test_mode()
                await client.session.stop()
                await client.storage.dc_id(r.dc_id)
                await client.storage.server_address(server_address)
                await client.storage.port(port)
                auth_key = await Auth(client, r.dc_id, server_address, port, test_mode).create()
                await client.storage.auth_key(auth_key)
                client.session = Session(
                    client, r.dc_id, server_address, port, auth_key, test_mode
                )
                await client.session.start()
                r = await client.invoke(ImportLoginToken(token=r.token))

            if isinstance(r, LoginTokenSuccess):
                log.info("QR Telegram: autorización aceptada, finalizando sesión.")
                return ("ok", None)
            # Sigue pendiente: devolver una URL (posiblemente renovada)
            token = getattr(r, "token", None)
            return ("pending", self._qr_url(token) if token else None)

        try:
            estado, url = self._run(_coro())
        except SessionPasswordNeeded:
            estado, url = "password", None
        except Exception as e:
            raise TelegramError(self._mensaje_error_telegram(e))

        if estado == "ok":
            self._finalizar_login(account_id)
        return {"estado": estado, "url": url}

    def _finalizar_login(self, account_id: int):
        """Tras autenticarse, obtiene el perfil, guarda el cliente y actualiza el nombre."""
        pend = self._pending.get(account_id)
        client = pend["client"]

        async def _coro():
            try:
                from pyrogram import raw
                if not getattr(client, "is_initialized", False):
                    await client.invoke(raw.functions.updates.GetState())
            except Exception as e:
                log.debug(f"No se pudo sincronizar estado inicial de Telegram: {e}")
            me = await client.get_me()
            if not getattr(client, "is_initialized", False):
                try:
                    await client.initialize()
                except Exception as e:
                    log.debug(f"No se pudo inicializar dispatcher de Telegram: {e}")
            return me

        me = self._run(_coro())
        nombre = me.username and f"@{me.username}" or (me.first_name or str(me.id))
        db.execute("UPDATE accounts SET name = ?, active = 1 WHERE id = ?", (nombre, account_id))

        with self._lock:
            self._clients[account_id] = client
            self._pending.pop(account_id, None)
        self._registrar_handlers(client, account_id)
        log.info(f"Cuenta de Telegram autenticada: {nombre} (ID {account_id})")

    def cancelar_login(self, account_id: int):
        """Cancela un login en curso y elimina la cuenta provisional."""
        pend = self._pending.pop(account_id, None)
        if pend:
            client = pend["client"]
            try:
                self._run(client.disconnect(), timeout=15)
            except Exception:
                pass
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self._borrar_sesion_local(account_id)

    # --- CLIENTE EN EJECUCIÓN ---
