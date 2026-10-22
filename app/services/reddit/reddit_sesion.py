# -*- coding: utf-8 -*-
"""Alta de cuenta (OAuth por navegador), sesión, clientes y ciclo de vida.

Mixin de RedditService. El flujo de alta es el de "código de autorización" de OAuth2 para
aplicaciones instaladas (installed app, sin secreto): el usuario autoriza en SU navegador con su
propia sesión de Reddit y la app recibe un refresh token permanente que se guarda cifrado. La
contraseña del usuario NUNCA pasa por la app.
"""
import time
import secrets
import threading
import urllib.parse

from app.services.reddit.reddit_common import (
    HAS_PRAW, Reddit, RedditServiceError, db, security, config, log,
    user_agent, REDDIT_REDIRECT_PORT, REDDIT_REDIRECT_URI,
    SCOPES, praw_exceptions, praw_reddit_exceptions)


class _SesionMixin:
    # ------------------------------------------------------------------
    # ALTA POR NAVEGADOR (OAuth 2.0, código de autorización)
    # ------------------------------------------------------------------
    def _client_id_efectivo(self, client_id: str = None) -> str:
        """Devuelve el client_id a usar: el explícito o el configurado en Preferencias → Reddit
        (uno por usuario, como el api_id/api_hash de Telegram). Lanza si no hay ninguno."""
        cid = (client_id or "").strip()
        if not cid:
            cid = (config.get("reddit", "client_id", "") or "").strip()
        if not cid:
            raise RedditServiceError(
                "Falta tu identificador de aplicación (client id). Créalo en reddit.com/prefs/apps "
                "como 'installed app' con la URL de redirección http://localhost:8080 e introdúcelo "
                "en Preferencias → Reddit.")
        return cid

    def client_id_configurado(self) -> bool:
        """True si hay un client id configurado en Preferencias → Reddit (patrón Telegram)."""
        return bool((config.get("reddit", "client_id", "") or "").strip())

    def iniciar_alta_oauth(self, client_id: str = None, progreso=None, timeout: int = 300) -> dict:
        """Da de alta una cuenta autenticándose por NAVEGADOR (OAuth). Devuelve
        {account_id, username}. `client_id` opcional (si no, usa el de Preferencias → Reddit).
        `progreso` es un callback opcional (str). Bloqueante: llamar desde task_manager."""
        if not HAS_PRAW:
            raise RedditServiceError("La biblioteca praw no está instalada.")
        cid = self._client_id_efectivo(client_id)

        def _p(msg):
            if progreso:
                try:
                    progreso(msg)
                except Exception:
                    pass

        # Servidor local que recogerá el redirect con el código. Reddit valida el redirect_uri por
        # igualdad EXACTA, así que se usa el puerto fijo REDDIT_REDIRECT_PORT (el mismo que hay que
        # registrar en la app de reddit.com/prefs/apps).
        resultado = {}
        evento = threading.Event()
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
                resultado.update(params)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                cuerpo = ("<html><head><meta charset='utf-8'><title>SocialRSS</title></head>"
                          "<body style='font-family:sans-serif'>"
                          "<h1>Autenticacion completada</h1>"
                          "<p>Ya puedes cerrar esta pestana y volver a SocialRSS.</p>"
                          "</body></html>")
                try:
                    self.wfile.write(cuerpo.encode("utf-8"))
                except Exception:
                    pass
                evento.set()

        try:
            servidor = HTTPServer(("127.0.0.1", REDDIT_REDIRECT_PORT), _Handler)
        except OSError as e:
            raise RedditServiceError(
                f"No se pudo abrir el puerto local {REDDIT_REDIRECT_PORT} para completar el inicio de "
                f"sesión (¿lo está usando otro programa?). Ciérralo y vuelve a intentarlo. Detalle: {e}")

        hilo = threading.Thread(target=servidor.serve_forever, name="RedditOAuthCallback", daemon=True)
        hilo.start()
        try:
            reddit = Reddit(client_id=cid, client_secret=None, redirect_uri=REDDIT_REDIRECT_URI,
                            user_agent=user_agent())
            state = secrets.token_urlsafe(24)
            url = reddit.auth.url(scopes=SCOPES, state=state, duration="permanent")

            from app.core.privacy import abrir_url
            abrir_url(url)
            _p("Se ha abierto el navegador. Inicia sesión en Reddit y aprueba el acceso; luego vuelve aquí.")

            if not evento.wait(timeout=timeout):
                raise RedditServiceError("Se agotó el tiempo esperando a que autorices el acceso en el navegador.")
            if resultado.get("error"):
                raise RedditServiceError(f"Reddit rechazó el acceso: {resultado.get('error')}")
            if resultado.get("state") != state:
                raise RedditServiceError(
                    "La respuesta del navegador no coincide con la petición; se ha cancelado por seguridad.")
            code = resultado.get("code")
            if not code:
                raise RedditServiceError("El navegador no devolvió el código de autorización.")

            _p("Obteniendo el token de acceso...")
            refresh_token = reddit.auth.authorize(code)
            if not refresh_token:
                raise RedditServiceError(
                    "Reddit no devolvió un token permanente. Asegúrate de que la app es de tipo "
                    "'installed app' y vuelve a intentarlo.")

            _p("Comprobando la cuenta...")
            yo = reddit.user.me()
            username = getattr(yo, "name", None) or ""
            if not username:
                raise RedditServiceError("No se pudo obtener tu nombre de usuario tras iniciar sesión.")
        except RedditServiceError:
            raise
        except Exception as e:
            raise RedditServiceError(self._mensaje_error(e))
        finally:
            try:
                servidor.shutdown()
            except Exception:
                pass

        # Impedir cuentas DUPLICADAS (misma identidad): dos veces la misma cuenta rota el mismo
        # refresh token y se revocan mutuamente.
        existente = self._cuenta_con_username(username)
        if existente:
            raise RedditServiceError(
                f"Esa cuenta de Reddit ya está añadida (u/{username}). Si su sesión caducó, usa "
                "'Volver a autorizar' en el gestor de cuentas en vez de añadirla otra vez.")

        # Posición al FINAL (max+1): una cuenta nueva no salta al principio del gestor.
        row_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) "
            "VALUES (?, 'reddit', 1, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))",
            (f"u/{username}",),
        )

        # Guardado EN BLOQUE y COMPENSADO: las credenciales van en una única actualización atómica;
        # si el almacén falla, se deshace el alta entera (no queda cuenta huérfana).
        ok = security.guardar_credenciales({
            (f"reddit:{row_id}", "refresh_token"): refresh_token,
            (f"reddit:{row_id}", "client_id"): cid,
            (f"reddit:{row_id}", "username"): username,
        })
        if ok is False:
            db.execute("DELETE FROM accounts WHERE id = ?", (row_id,))
            raise RedditServiceError(
                "No se pudieron guardar las credenciales en el almacén seguro; el alta se ha "
                "deshecho. Revisa el espacio en disco y vuelve a intentarlo.")

        self._clients[row_id] = reddit
        self._me[row_id] = {"username": username}
        log.info(f"Cuenta de Reddit registrada: u/{username} (ID {row_id})")
        return {"account_id": row_id, "username": username}

    def _cuenta_con_username(self, username: str):
        """Devuelve el account_id de la cuenta de Reddit ya registrada con ese usuario, o None."""
        if not username:
            return None
        objetivo = username.lower()
        for (acc_id,) in db.query("SELECT id FROM accounts WHERE service_id = 'reddit'"):
            actual = (security.obtener_credencial(f"reddit:{acc_id}", "username") or "").lower()
            if actual == objetivo:
                return acc_id
        return None

    def reautenticar(self, account_id: int, client_id: str = None, progreso=None,
                     timeout: int = 300) -> dict:
        """'Volver a autorizar' una cuenta EXISTENTE conservando su account_id y su caché local.
        Verifica que la nueva autorización es de la MISMA cuenta (mismo usuario). Bloqueante."""
        if not HAS_PRAW:
            raise RedditServiceError("La biblioteca praw no está instalada.")
        usuario_previo = (security.obtener_credencial(f"reddit:{account_id}", "username") or "").lower()
        cid = self._client_id_efectivo(
            client_id or security.obtener_credencial(f"reddit:{account_id}", "client_id"))

        def _p(msg):
            if progreso:
                try:
                    progreso(msg)
                except Exception:
                    pass

        resultado = {}
        evento = threading.Event()
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                resultado.update(dict(urllib.parse.parse_qsl(
                    urllib.parse.urlparse(self.path).query)))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                try:
                    self.wfile.write(b"<html><body><h1>Autenticacion completada</h1>"
                                     b"<p>Vuelve a SocialRSS.</p></body></html>")
                except Exception:
                    pass
                evento.set()

        try:
            servidor = HTTPServer(("127.0.0.1", REDDIT_REDIRECT_PORT), _Handler)
        except OSError as e:
            raise RedditServiceError(
                f"No se pudo abrir el puerto local {REDDIT_REDIRECT_PORT}. Detalle: {e}")
        hilo = threading.Thread(target=servidor.serve_forever, name="RedditOAuthReauth", daemon=True)
        hilo.start()
        try:
            reddit = Reddit(client_id=cid, client_secret=None, redirect_uri=REDDIT_REDIRECT_URI,
                            user_agent=user_agent())
            state = secrets.token_urlsafe(24)
            url = reddit.auth.url(scopes=SCOPES, state=state, duration="permanent")
            from app.core.privacy import abrir_url
            abrir_url(url)
            _p("Se ha abierto el navegador. Inicia sesión y aprueba el acceso.")
            if not evento.wait(timeout=timeout):
                raise RedditServiceError("Se agotó el tiempo esperando a que autorices el acceso.")
            if resultado.get("state") != state or not resultado.get("code"):
                raise RedditServiceError("La respuesta del navegador no es válida; cancelado por seguridad.")
            refresh_token = reddit.auth.authorize(resultado["code"])
            username = getattr(reddit.user.me(), "name", None) or ""
        except RedditServiceError:
            raise
        except Exception as e:
            raise RedditServiceError(self._mensaje_error(e))
        finally:
            try:
                servidor.shutdown()
            except Exception:
                pass

        if usuario_previo and username.lower() != usuario_previo:
            raise RedditServiceError(
                f"Has iniciado sesión como u/{username}, pero esta cuenta es u/{usuario_previo}. "
                "Se ha cancelado para no mezclar datos de dos cuentas distintas.")

        ok = security.guardar_credenciales({
            (f"reddit:{account_id}", "refresh_token"): refresh_token,
            (f"reddit:{account_id}", "client_id"): cid,
            (f"reddit:{account_id}", "username"): username,
        })
        if ok is False:
            raise RedditServiceError("No se pudieron guardar las credenciales renovadas.")

        # Limpiar estado de "muerta" y refrescar caché de cliente.
        self._sesiones_revocadas.discard(account_id)
        self._cooldown_429.pop(account_id, None)
        self._clients[account_id] = reddit
        self._me[account_id] = {"username": username}
        log.info(f"Cuenta de Reddit {account_id} vuelta a autorizar (u/{username}).")
        return {"account_id": account_id, "username": username}

    # ------------------------------------------------------------------
    # CLIENTES POR CUENTA
    # ------------------------------------------------------------------
    def _lock_de_cuenta(self, account_id: int):
        """Devuelve (creando si hace falta) el RLock reentrante de esta cuenta."""
        with self._client_locks_guard:
            lock = self._client_locks.get(account_id)
            if lock is None:
                lock = threading.RLock()
                self._client_locks[account_id] = lock
            return lock

    def obtener_cliente(self, account_id: int):
        """Devuelve el cliente `praw.Reddit` de la cuenta (creándolo la primera vez). No hace red:
        PRAW es perezoso y solo autentica en la primera operación real. Falla rápido si la sesión
        está revocada o en cooldown por límite de tasa."""
        cli = self._clients.get(account_id)
        if cli is not None:
            return cli
        if not HAS_PRAW:
            raise RedditServiceError("La biblioteca praw no está instalada.")
        if account_id in self._sesiones_revocadas:
            raise RedditServiceError(
                "La sesión de esta cuenta de Reddit caducó o fue revocada. Usa 'Volver a autorizar'.")
        self._comprobar_cooldown(account_id)
        with self._lock_de_cuenta(account_id):
            cli = self._clients.get(account_id)
            if cli is not None:
                return cli
            refresh = security.obtener_credencial(f"reddit:{account_id}", "refresh_token")
            cid = (security.obtener_credencial(f"reddit:{account_id}", "client_id")
                   or config.get("reddit", "client_id", "") or "").strip()
            if not (refresh and cid):
                self._sesiones_revocadas.add(account_id)
                raise RedditServiceError(
                    "No hay credenciales guardadas para esta cuenta de Reddit. Vuelve a autorizarla.")
            reddit = Reddit(client_id=cid, client_secret=None, refresh_token=refresh,
                            user_agent=user_agent())
            self._clients[account_id] = reddit
            usuario = security.obtener_credencial(f"reddit:{account_id}", "username") or ""
            if usuario:
                self._me[account_id] = {"username": usuario}
            return reddit

    def nombre_usuario(self, account_id: int) -> str:
        """Nombre de usuario (sin u/) de la cuenta, desde la caché o el almacén seguro."""
        cache = self._me.get(account_id)
        if cache and cache.get("username"):
            return cache["username"]
        return security.obtener_credencial(f"reddit:{account_id}", "username") or ""

    def eliminar_cuenta(self, account_id: int) -> bool:
        """Elimina la cuenta: borra sus filas (CASCADE limpia el contenido), sus credenciales y
        toda la caché en memoria."""
        try:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        except Exception as e:
            log.error(f"Error al borrar la cuenta de Reddit {account_id}: {e}")
            return False
        for clave in ("refresh_token", "client_id", "username"):
            try:
                security.borrar_credencial(f"reddit:{account_id}", clave)
            except Exception:
                pass
        cli = self._clients.pop(account_id, None)
        if cli is not None:
            self._cerrar_cliente(cli)
        self._me.pop(account_id, None)
        self._sesiones_revocadas.discard(account_id)
        self._cooldown_429.pop(account_id, None)
        with self._client_locks_guard:
            self._client_locks.pop(account_id, None)
        log.info(f"Cuenta de Reddit {account_id} eliminada.")
        return True

    @staticmethod
    def _cerrar_cliente(cli):
        """Cierra la sesión HTTP subyacente de un cliente PRAW (requests.Session)."""
        try:
            cli._core._requestor._http.close()
        except Exception:
            pass

    def cerrar(self):
        """Cierra todos los clientes (al salir de la app)."""
        for cli in list(self._clients.values()):
            self._cerrar_cliente(cli)
        self._clients.clear()

    # ------------------------------------------------------------------
    # LÍMITES DE TASA (429) Y ERRORES POR CÓDIGO
    # ------------------------------------------------------------------
    def _comprobar_cooldown(self, account_id: int):
        """Lanza si la cuenta está en cooldown tras un 429, indicando cuándo reintentar."""
        hasta = self._cooldown_429.get(account_id)
        if hasta and time.monotonic() < hasta:
            restante = int(hasta - time.monotonic())
            raise RedditServiceError(
                f"Reddit ha limitado temporalmente esta cuenta por exceso de peticiones. "
                f"Reintenta en unos {restante} segundos.")
        if hasta:
            self._cooldown_429.pop(account_id, None)

    def registrar_limite_429(self, account_id: int, retry_after: float = None):
        """Marca la cuenta en cooldown tras un 429 (usa retry_after si viene, o 60 s por defecto)."""
        espera = 60.0
        try:
            if retry_after:
                espera = max(5.0, float(retry_after))
        except Exception:
            pass
        self._cooldown_429[account_id] = time.monotonic() + espera
        log.warning(f"Reddit: límite de tasa en la cuenta {account_id}; cooldown {int(espera)} s.")

    def _marcar_revocada_si_token(self, account_id: int, e: Exception) -> None:
        """Si el error es de token inválido/OAuth, marca la cuenta como revocada (fail-fast)."""
        exc = praw_exceptions()
        if exc and isinstance(e, (getattr(exc, "InvalidToken", ()), getattr(exc, "OAuthException", ()))):
            self._sesiones_revocadas.add(account_id)

    def _mensaje_error(self, e: Exception) -> str:
        """Traduce una excepción de PRAW/prawcore a un mensaje claro en español, clasificando por
        CÓDIGO (no por str(), que arrastra cabeceras y da falsos positivos)."""
        exc = praw_exceptions()
        rexc = praw_reddit_exceptions()
        # Errores lógicos de Reddit por ítem (RedditAPIException): mensaje del primer error.
        if rexc is not None:
            api_exc = getattr(rexc, "RedditAPIException", None)
            if api_exc and isinstance(e, api_exc):
                try:
                    items = getattr(e, "items", None) or []
                    if items:
                        it = items[0]
                        return (getattr(it, "message", None) or getattr(it, "error_type", None)
                                or str(e))
                except Exception:
                    pass
                return str(e)
        if exc is None:
            return str(e) or "Error desconocido de Reddit."
        if isinstance(e, getattr(exc, "TooManyRequests", ())):
            return "Reddit ha limitado las peticiones (429). Espera un poco antes de reintentar."
        if isinstance(e, (getattr(exc, "InvalidToken", ()), getattr(exc, "OAuthException", ()))):
            return "La sesión de Reddit caducó o fue revocada. Usa 'Volver a autorizar'."
        if isinstance(e, getattr(exc, "InsufficientScope", ())):
            return "La autorización actual no tiene permisos para esta acción. Vuelve a autorizar la cuenta."
        if isinstance(e, getattr(exc, "Forbidden", ())):
            return "Reddit ha denegado la acción (403): puede que no tengas permiso o el contenido esté restringido."
        if isinstance(e, getattr(exc, "NotFound", ())):
            return "No se encontró el contenido solicitado en Reddit (404)."
        if isinstance(e, getattr(exc, "Redirect", ())):
            return "Reddit redirigió la petición (puede que el subreddit no exista o sea privado)."
        if isinstance(e, getattr(exc, "ServerError", ())):
            return "Reddit está teniendo problemas en sus servidores (5xx). Inténtalo más tarde."
        if isinstance(e, getattr(exc, "RequestException", ())):
            return "No se pudo conectar con Reddit. Revisa tu conexión a internet."
        return str(e) or "Error desconocido de Reddit."
