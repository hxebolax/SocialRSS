# -*- coding: utf-8 -*-
"""Bucle asyncio, login por cookies, reautenticación y cuenta.

Mixin de XService (parte de la refactorización modular).
"""
from app.services.x.x_common import (
    json, asyncio, threading, Any, Callable, Dict,
    Optional, db, security, config, log,
    HAS_TWIKIT, Client, XServiceError)


class _SesionMixin:
    # ------------------------------------------------------------------
    # Bucle asyncio
    # ------------------------------------------------------------------
    def _ensure_loop(self):
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, name="XLoop", daemon=True)
            self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout: float = 60.0):
        if not HAS_TWIKIT:
            raise XServiceError("La librería de X (twikit) no está instalada.")
        self._ensure_loop()
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # Cancelar la corrutina al agotarse el plazo: si siguiera viva podría
            # completarse después (p. ej. publicar un tuit "fallido" dos veces).
            future.cancel()
            err = XServiceError("La operación tardó demasiado y se canceló. Comprueba si "
                                "llegó a completarse antes de repetirla.")
            err.auth = False
            raise err
        except XServiceError:
            raise
        except Exception as e:
            err = XServiceError(self._mensaje_error(e))
            # Marcar si es un problema de sesión/cookies (para reimportar y reintentar) o un
            # rechazo PASAJERO (para repetir tal cual antes de nada).
            err.auth = self._es_error_auth(e)
            err.transitorio = self._es_transitorio(e)
            err.es_404 = type(e).__name__ == "NotFound" or "status: 404" in str(e)
            raise err

    def _run_con_reauth(self, account_id: int, coro_factory: Callable, timeout: float = 60.0,
                        escritura: bool = False):
        """Ejecuta una operación y, si falla por sesión/cookies caducadas, reimporta las
        cookies del navegador EN SILENCIO una vez y reintenta. Centraliza la reconexión:
        todas las operaciones de X (timelines, notificaciones, acciones, DMs, listas...) la
        heredan sin tener que repetir el patrón en cada callback de la UI.

        `coro_factory` debe ser una función SIN argumentos que devuelva una corrutina NUEVA
        cada vez (no se puede reutilizar una corrutina ya consumida), de modo que el reintento
        vuelva a pedir `self._cliente(account_id)` —que tras la reimport es el cliente nuevo."""
        import time as _time
        ultimo = None
        # Rechazos PASAJEROS de X (un 404 sin cuerpo cuando no acepta la cabecera
        # x-client-transaction-id de esa petición, un 5xx, un corte de red): se repite con una
        # pausa corta y cabecera nueva. Comprobado con una sesión real: la misma llamada que da
        # 404 vacío funciona al repetirla. Sin esto se reimportaban cookies y se decía al
        # usuario que su sesión había caducado.
        for pausa in (0, 0.8, 2.0):
            if pausa:
                _time.sleep(pausa)
            try:
                return self._run(coro_factory(), timeout)
            except XServiceError as e:
                ultimo = e
                if not getattr(e, "transitorio", False):
                    break
                # En una ESCRITURA (publicar, enviar un mensaje, programar) solo se repite el
                # 404 vacío, que X rechaza ANTES de procesarla. Un 5xx o un corte de red
                # pueden llegar después de publicarse: repetir podría duplicarla.
                if escritura and not getattr(e, "es_404", False):
                    break
                log.debug(f"X: rechazo pasajero, se repite la petición: {e}")
        e = ultimo
        if not getattr(e, "auth", False):
            raise e
        if not account_id or not self._reimport_silencioso(account_id):
            raise e
        # Reintento único con la sesión ya renovada.
        return self._run(coro_factory(), timeout)

    @staticmethod
    def _es_transitorio(e: Exception) -> bool:
        """True si el fallo suele ser pasajero y merece repetir la petición tal cual: 404 SIN
        mensaje (X rechaza la cabecera de transacción de esa petición concreta), errores 5xx del
        servidor y cortes o esperas de red. Un 404 con mensaje ("does not exist", code 34) es un
        recurso que no existe: no se repite."""
        nombre = type(e).__name__
        txt = str(e)
        if nombre == "NotFound" or "status: 404" in txt:
            cuerpo = txt.split("message:", 1)[1].strip() if "message:" in txt else ""
            return cuerpo in ("", '""', "''")
        if nombre in ("ServerError", "InternalServerError", "BadGateway", "ServiceUnavailable"):
            return True
        if any(c in txt for c in ("status: 500", "status: 502", "status: 503", "status: 504")):
            return True
        return nombre in ("ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError",
                          "ReadError", "WriteError", "PoolTimeout")

    def _reimport_silencioso(self, account_id: int) -> bool:
        """Reimporta las cookies del navegador para la cuenta, en silencio y con
        enfriamiento global. Devuelve True si se renovó la sesión. No interrumpe al usuario:
        si no hay cookies en el navegador, devuelve False y el error original sigue su curso."""
        import time
        with self._reauth_lock:
            ahora = time.monotonic()
            if ahora - self._ultimo_reauth < self._REAUTH_COOLDOWN:
                return False
            self._ultimo_reauth = ahora
        # Probar primero el navegador que funcionó la última vez (si se recuerda) y luego auto.
        navegadores = []
        ultimo = config.get("x", "last_browser", "")
        if ultimo and ultimo != "auto":
            navegadores.append(ultimo)
        navegadores.append("auto")
        for nav in navegadores:
            try:
                cookies = self.cookies_desde_navegador(nav)
                self.reimportar_cookies(account_id, cookies)
                config.set("x", "last_browser", nav)
                log.info(f"Reconexión de X: cookies recargadas en silencio (navegador={nav}).")
                return True
            except XServiceError as e:
                log.debug(f"Reconexión de X con navegador={nav} falló: {e}")
                continue
        return False

    @staticmethod
    def _es_error_auth(e: Exception) -> bool:
        """True si el error parece de sesión/cookies (caducada/incorrecta), no otro fallo."""
        txt = str(e).lower()
        return any(k in txt for k in (
            "401", "unauthorized", "could not authenticate", "code\":37", "code\":64",
            "code\":34", "does not exist", "suspend", "404"))

    @staticmethod
    def _mensaje_error(e: Exception) -> str:
        txt = str(e)
        low = txt.lower()
        if "401" in txt or "unauthorized" in low or "could not authenticate" in low:
            return ("La sesión de X no es válida o ha caducado. Vuelve a iniciar sesión "
                    "(importa de nuevo las cookies del navegador).")
        if "transaction" in low or "ondemand" in low or "webpack" in low:
            return ("X ha cambiado algo en su web y el componente de transacción ha fallado. "
                    "Puede que haya que actualizar el cliente de X.")
        if "rate limit" in low or "429" in txt:
            return "X ha limitado las peticiones por ahora. Espera unos minutos."
        if "suspend" in low or "code\":37" in txt or "code\":64" in txt:
            return ("X ha rechazado la sesión (suele ser por cookies incorrectas o caducadas, "
                    "no porque tu cuenta esté suspendida). Vuelve a importar las cookies de x.com "
                    "del navegador donde tienes la sesión abierta, o pégalas manualmente "
                    "(auth_token y ct0).")
        if "locked" in low:
            return ("Tu cuenta de X está bloqueada por X; entra en x.com/account/access para "
                    "desbloquearla y vuelve a intentarlo.")
        if "404" in txt or "code\":34" in txt or "does not exist" in low:
            return ("X no ha reconocido la petición (cookies incorrectas/caducadas o un cambio "
                    "en X). Vuelve a importar las cookies de tu sesión de x.com, o pégalas "
                    "manualmente (auth_token y ct0).")
        return f"Error de X: {txt}"

    # ------------------------------------------------------------------
    # Login / sesión (cookies cifradas)
    # ------------------------------------------------------------------
    _COOKIES_X = ("auth_token", "ct0", "twid", "kdt", "guest_id", "att", "d_prefs")

    @classmethod
    def cookies_desde_navegador(cls, navegador: str = "auto") -> Dict[str, str]:
        """Extrae las cookies de sesión de X del navegador.

        navegador: 'auto', 'chrome', 'edge', 'firefox', 'brave', 'opera'. Devuelve un dict
        con al menos auth_token y ct0, o lanza XServiceError con un mensaje claro.

        IMPORTANTE: se priorizan las cookies de x.com sobre las de twitter.com. Antes, las
        de twitter.com (a veces caducadas) sobreescribían las buenas de x.com y la sesión
        salía inválida (X respondía 'suspendida'/404)."""
        try:
            import browser_cookie3 as bc
        except Exception:
            raise XServiceError("Falta el componente para leer cookies del navegador (browser-cookie3).")
        funcs = {
            "chrome": getattr(bc, "chrome", None), "edge": getattr(bc, "edge", None),
            "firefox": getattr(bc, "firefox", None), "brave": getattr(bc, "brave", None),
            "opera": getattr(bc, "opera", None),
        }
        # En 'auto' se prueba Firefox PRIMERO a propósito: los navegadores basados en Chromium
        # (Chrome/Edge/Brave/Opera) cifran las cookies con App-Bound Encryption desde Chrome 127
        # (2024) y browser_cookie3 normalmente NO puede descifrarlas; Firefox las guarda sin ese
        # cifrado, así que es el que funciona de forma fiable.
        candidatos = ([(navegador, funcs[navegador])] if navegador in funcs and funcs[navegador]
                      else [(n, funcs[n]) for n in ("firefox", "chrome", "edge", "brave", "opera")
                            if funcs.get(n)])
        chromium = {"chrome", "edge", "brave", "opera"}
        ultimo_error = None
        fallo_chromium = False   # algún navegador Chromium falló al descifrar (App-Bound Encryption)
        for nombre, fn in candidatos:
            # Recoger por dominio SIN mezclar: cada dominio en su propio dict, y nos
            # quedamos con el primero (x.com) que tenga auth_token + ct0.
            for dominio in ("x.com", "twitter.com"):
                cookies: Dict[str, str] = {}
                try:
                    jar = fn(domain_name=dominio)
                    for c in jar:
                        if c.name in cls._COOKIES_X and c.name not in cookies:
                            cookies[c.name] = c.value
                except Exception as e:
                    ultimo_error = e
                    if nombre in chromium:
                        fallo_chromium = True
                    continue
                if cookies.get("auth_token") and cookies.get("ct0"):
                    return cookies

        # Mensaje específico: si pidió (o se intentó) un navegador Chromium y no se pudieron leer
        # sus cookies, explicar el cifrado de Chrome y orientar a Firefox o al pegado manual.
        pidio_chromium = navegador in chromium
        if pidio_chromium or fallo_chromium:
            msg = ("No se han podido leer las cookies de X de un navegador basado en Chrome "
                   "(Chrome, Edge, Brave u Opera). Desde Chrome 127, estos navegadores cifran "
                   "sus cookies (App-Bound Encryption) y no se pueden extraer automáticamente. "
                   "Soluciones: 1) inicia sesión en x.com con FIREFOX y vuelve a importar (Firefox "
                   "sí funciona); o 2) pega las cookies manualmente (auth_token y ct0), que es el "
                   "método más robusto.")
        else:
            msg = ("No se han encontrado las cookies de sesión de X en el navegador. Comprueba "
                   "que has iniciado sesión en x.com en ese navegador (y en su perfil principal). "
                   "Si usas Firefox con varios perfiles/contenedores, puede que no se lean; en ese "
                   "caso usa la opción de pegar las cookies manualmente (auth_token y ct0).")
        if ultimo_error:
            msg += f" (Detalle técnico: {ultimo_error})"
        raise XServiceError(msg)

    @staticmethod
    def _uid_de_twid(cookies: Dict[str, str]) -> str:
        """Extrae el id de usuario del cookie 'twid' (formato u=<id> o u%3D<id>)."""
        import re
        import urllib.parse
        twid = urllib.parse.unquote(cookies.get("twid", "") or "")
        m = re.search(r"(\d{4,})", twid)
        return m.group(1) if m else ""

    def login_con_cookies(self, cookies: Dict[str, str], etiqueta: Optional[str] = None) -> Dict[str, Any]:
        """Crea una cuenta de X a partir de cookies (auth_token + ct0). Síncrono (úsese en un worker)."""
        if not HAS_TWIKIT:
            raise XServiceError("La librería de X (twikit) no está instalada.")
        if not cookies.get("auth_token") or not cookies.get("ct0"):
            raise XServiceError("Faltan las cookies necesarias (auth_token y ct0).")

        async def _coro():
            client = Client("es-ES")
            client.set_cookies(dict(cookies))
            # Validación con el endpoint v1.1 'account/settings' (estable), en vez del
            # GraphQL get_user_by_screen_name (que cambia de ID a menudo y da 404/code 34).
            resp, _r = await client.v11.settings()
            screen = ""
            if isinstance(resp, dict):
                screen = resp.get("screen_name", "") or ""
            return client, screen

        client, screen = self._run(_coro(), timeout=45)
        if not screen:
            raise XServiceError(
                "La sesión no es válida (no se pudo leer tu cuenta). Revisa que las cookies "
                "(auth_token y ct0) sean de tu sesión actual de x.com.")
        uid = self._uid_de_twid(cookies)
        row_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) "
            "VALUES (?, 'x', 1, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))", (f"@{screen}",))
        security.guardar_credencial(f"{self.NS}:{row_id}", "cookies", json.dumps(cookies))
        security.guardar_credencial(f"{self.NS}:{row_id}", "screen_name", screen)
        security.guardar_credencial(f"{self.NS}:{row_id}", "user_id", uid)
        self._clients[row_id] = client
        self._me[row_id] = {"id": uid, "screen_name": screen}
        log.info(f"Cuenta de X registrada: @{screen} (ID {row_id})")
        return {"account_id": row_id, "screen_name": screen, "user_id": uid}

    def login_programatico(self, usuario: str, password: str,
                           totp_secret: Optional[str] = None) -> Dict[str, Any]:
        """Login con usuario/contraseña/2FA (el más frágil). No guarda la contraseña."""
        if not HAS_TWIKIT:
            raise XServiceError("La librería de X (twikit) no está instalada.")

        async def _coro():
            client = Client("es-ES")
            await client.login(auth_info_1=usuario, password=password,
                               totp_secret=(totp_secret or None))
            resp, _r = await client.v11.settings()
            screen = resp.get("screen_name", "") if isinstance(resp, dict) else ""
            return client, screen, dict(client.get_cookies())

        client, screen, cookies = self._run(_coro(), timeout=120)
        screen = screen or usuario
        uid = self._uid_de_twid(cookies)
        row_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) "
            "VALUES (?, 'x', 1, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))", (f"@{screen}",))
        security.guardar_credencial(f"{self.NS}:{row_id}", "cookies", json.dumps(dict(cookies)))
        security.guardar_credencial(f"{self.NS}:{row_id}", "screen_name", screen)
        security.guardar_credencial(f"{self.NS}:{row_id}", "user_id", uid)
        self._clients[row_id] = client
        self._me[row_id] = {"id": uid, "screen_name": screen}
        log.info(f"Cuenta de X registrada (login programático): @{screen} (ID {row_id})")
        return {"account_id": row_id, "screen_name": screen, "user_id": uid}

    async def _cliente(self, account_id: int):
        """Devuelve un Client de twikit con la sesión cargada (con caché)."""
        if account_id in self._clients:
            return self._clients[account_id]
        raw = security.obtener_credencial(f"{self.NS}:{account_id}", "cookies")
        if not raw:
            raise XServiceError(f"No hay sesión guardada para la cuenta de X {account_id}.")
        client = Client("es-ES")
        client.set_cookies(json.loads(raw))
        self._clients[account_id] = client
        return client

    def obtener_cookies_login(self, usuario: str, password: str,
                              totp_secret: Optional[str] = None) -> Dict[str, str]:
        """Inicia sesión por usuario/contraseña SOLO para obtener cookies (no crea cuenta).
        Útil para reimportar las cookies de una cuenta de X ya existente."""
        if not HAS_TWIKIT:
            raise XServiceError("La librería de X (twikit) no está instalada.")

        async def _coro():
            client = Client("es-ES")
            await client.login(auth_info_1=usuario, password=password,
                               totp_secret=(totp_secret or None))
            return dict(client.get_cookies())
        return self._run(_coro(), timeout=120)

    def reimportar_cookies(self, account_id: int, cookies: Dict[str, str]) -> Dict[str, Any]:
        """Actualiza las cookies de una cuenta de X YA existente (sin borrarla ni volver a
        crearla). Valida la sesión, guarda las cookies cifradas y refresca el cliente."""
        if not HAS_TWIKIT:
            raise XServiceError("La librería de X (twikit) no está instalada.")
        if not cookies.get("auth_token") or not cookies.get("ct0"):
            raise XServiceError("Faltan las cookies necesarias (auth_token y ct0).")

        async def _coro():
            client = Client("es-ES")
            client.set_cookies(dict(cookies))
            resp, _r = await client.v11.settings()
            screen = resp.get("screen_name", "") if isinstance(resp, dict) else ""
            return client, screen

        client, screen = self._run(_coro(), timeout=45)
        if not screen:
            raise XServiceError(
                "La sesión sigue sin ser válida. Revisa que las cookies sean de tu sesión "
                "actual de x.com (auth_token y ct0).")
        security.guardar_credencial(f"{self.NS}:{account_id}", "cookies", json.dumps(cookies))
        security.guardar_credencial(f"{self.NS}:{account_id}", "screen_name", screen)
        uid = self._uid_de_twid(cookies) or self._yo(account_id).get("id", "")
        if uid:
            security.guardar_credencial(f"{self.NS}:{account_id}", "user_id", uid)
        db.execute("UPDATE accounts SET name = ? WHERE id = ?", (f"@{screen}", account_id))
        self._clients[account_id] = client
        self._me[account_id] = {"id": uid, "screen_name": screen}
        # Re-sembrar el pool de twscrape con las cookies nuevas (motor de lectura complementario).
        try:
            self._tws_resembrar(account_id)
        except Exception as e:
            log.debug(f"twscrape: no se pudo re-sembrar la cuenta {account_id}: {e}")
        log.info(f"Cookies de X reimportadas para la cuenta {account_id} (@{screen}).")
        return {"account_id": account_id, "screen_name": screen}

    def reimportar_desde_navegador(self, account_id: int, navegador: str = "auto") -> Dict[str, Any]:
        """Reimporta las cookies de la cuenta desde el navegador (sin pedir nada al usuario)."""
        cookies = self.cookies_desde_navegador(navegador)
        return self.reimportar_cookies(account_id, cookies)

    def persistir_sesiones(self):
        """Guarda cifradas las cookies ACTUALES de cada sesión activa. X rota `ct0` durante el
        uso y twikit lo actualiza solo en su jar en memoria; sin volcarlo, al reiniciar se
        partiría de una `ct0` vieja y la sesión podría fallar. Se llama al CERRAR la app, para
        que el login siga siendo permanente entre arranques. No interrumpe el cierre si falla."""
        for account_id, client in list(self._clients.items()):
            try:
                cookies = dict(client.get_cookies())
                if cookies.get("auth_token") and cookies.get("ct0"):
                    security.guardar_credencial(
                        f"{self.NS}:{account_id}", "cookies", json.dumps(cookies))
            except Exception as e:
                log.debug(f"No se pudieron persistir las cookies de X (cuenta {account_id}): {e}")

    def vaciar_cache_sesion(self, account_id: int):
        """'No conservar entre sesiones': vacía la caché VOLÁTIL de una cuenta de X (tweets de
        los timelines, notificaciones y listas de personas), para que cada arranque empiece
        limpio. NO toca la sesión (cookies), ni las listas/búsquedas guardadas (estructura), ni
        los programados (viven en el servidor). Todo lo borrado se vuelve a descargar al usar."""
        for tabla in ("x_tweets", "x_timeline_tweets", "x_notifications", "x_users"):
            try:
                db.execute(f"DELETE FROM {tabla} WHERE account_id = ?", (account_id,))
            except Exception as e:
                log.debug(f"No se pudo vaciar {tabla} de X (cuenta {account_id}): {e}")

    def _yo(self, account_id: int) -> Dict[str, str]:
        if account_id not in self._me:
            self._me[account_id] = {
                "id": security.obtener_credencial(f"{self.NS}:{account_id}", "user_id") or "",
                "screen_name": security.obtener_credencial(f"{self.NS}:{account_id}", "screen_name") or "",
            }
        return self._me[account_id]

    def eliminar_cuenta(self, account_id: int):
        """Cierra sesión y borra la cuenta de X y sus datos locales."""
        self._clients.pop(account_id, None)
        self._me.pop(account_id, None)
        # Borrar el pool de twscrape (cookies sembradas) de esta cuenta.
        try:
            self._tws_limpiar(account_id)
        except Exception:
            pass
        for k in ("cookies", "screen_name", "user_id"):
            security.borrar_credencial(f"{self.NS}:{account_id}", k)
        for tabla in ("x_tweets", "x_timeline_tweets", "x_users", "x_notifications",
                      "x_lists", "x_convos", "x_messages", "x_searches", "x_user_timelines"):
            try:
                db.execute(f"DELETE FROM {tabla} WHERE account_id = ?", (account_id,))
            except Exception:
                pass
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    # ------------------------------------------------------------------
    # Parseo y caché
    # ------------------------------------------------------------------
    _MESES = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
