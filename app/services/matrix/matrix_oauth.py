# -*- coding: utf-8 -*-
"""Alta de cuenta de Matrix por NAVEGADOR (OAuth 2.0 / OIDC) para servidores con Matrix
Authentication Service (MAS) — la "autenticación de nueva generación" de Matrix (MSC2964/2965/2966/
2967, estable desde Matrix 1.15).

En esos servidores el login por contraseña (`m.login.password`) está DESHABILITADO: hay que
autenticarse en el navegador. Este mixin hace el flujo de código de autorización con PKCE:

  1. Descubre los endpoints de autenticación del servidor (auth_metadata).
  2. Registra la app dinámicamente (Dynamic Client Registration, RFC 7591) → client_id.
  3. Abre el navegador en el endpoint de autorización (con PKCE S256 y un redirect a un servidor
     local efímero en 127.0.0.1). El usuario inicia sesión y aprueba.
  4. Recoge el `code` en el redirect y lo intercambia por un access_token (+ refresh_token).
  5. whoami → user_id y device_id, y persiste la cuenta (mismo almacenamiento que el login normal:
     el token va CIFRADO, la contraseña NUNCA se guarda porque nunca se pide).

La referencia del flujo es el cliente oficial Element Admin (auth.ts / login), adaptada a Python.
Se pide, si el usuario lo marca, el scope de administración (urn:synapse:admin:*) para que un
administrador tenga un cliente COMPLETO con las acciones de administración habilitadas por el token.
"""
import json as _json
import base64
import hashlib
import secrets
import threading
import urllib.parse
import urllib.request
import urllib.error

from app.services.matrix.matrix_common import (
    security, log, MatrixError, normalizar_homeserver, validar_endpoint_descubierto)

# Scopes de la API de cliente (MSC2967). El de dispositivo ata el token a un device_id concreto.
_SCOPE_API = "urn:matrix:org.matrix.msc2967.client:api:*"
_SCOPE_DEVICE = "urn:matrix:org.matrix.msc2967.client:device:{0}"
# Scopes de administración (solo si el usuario los pide y el servidor se los concede).
_SCOPE_ADMIN = "urn:mas:admin urn:synapse:admin:*"


class _OAuthMixin:
    # ---------------- Utilidades HTTP (pre-login, sin cliente nio) ----------------
    @staticmethod
    def _http_json(metodo: str, url: str, cuerpo=None, headers=None, form: bool = False,
                   timeout: int = 30):
        """Petición HTTP que devuelve JSON. `form=True` envía el cuerpo como
        application/x-www-form-urlencoded (endpoints OAuth de token/registro). Lanza MatrixError
        con el mensaje del servidor ante error."""
        datos = None
        hdr = dict(headers or {})
        if cuerpo is not None:
            if form:
                datos = urllib.parse.urlencode(cuerpo).encode("utf-8")
                hdr.setdefault("Content-Type", "application/x-www-form-urlencoded")
            else:
                datos = _json.dumps(cuerpo).encode("utf-8")
                hdr.setdefault("Content-Type", "application/json")
        hdr.setdefault("Accept", "application/json")
        req = urllib.request.Request(url, data=datos, method=metodo, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = _json.loads(e.read() or b"{}")
            except Exception:
                err = {}
            msg = (err.get("error_description") or err.get("error")
                   or err.get("errcode") or f"HTTP {e.code}")
            raise MatrixError(str(msg))
        except Exception as e:
            raise MatrixError(str(e))

    def _resolver_homeserver_real(self, base: str) -> str:
        """Resuelve la delegación .well-known/matrix/client (un dominio propio puede delegar en otro
        homeserver). Si no hay .well-known, devuelve la URL tal cual."""
        try:
            wk = self._http_json("GET", base.rstrip("/") + "/.well-known/matrix/client", timeout=15)
            real = normalizar_homeserver(((wk.get("m.homeserver") or {}).get("base_url") or ""))
            if real and real != base:
                # El .well-known puede DELEGAR en otro homeserver: se valida (https + anti-SSRF) para
                # que un dominio no redirija la sesión a una dirección interna del equipo.
                return validar_endpoint_descubierto(real, "servidor delegado")
            if real:
                return real
        except MatrixError:
            raise
        except Exception:
            pass
        return base

    def descubrir_auth_navegador(self, homeserver: str):
        """Devuelve los endpoints de autenticación por navegador del servidor (dict con
        authorization_endpoint, token_endpoint, registration_endpoint...) o None si el servidor NO
        usa autenticación de nueva generación (entonces se usa el login clásico por contraseña).
        Bloqueante: llamar desde task_manager."""
        base = self._resolver_homeserver_real(normalizar_homeserver(homeserver))
        if not base:
            return None
        for ruta in ("/_matrix/client/v1/auth_metadata",
                     "/_matrix/client/unstable/org.matrix.msc2965/auth_metadata"):
            try:
                meta = self._http_json("GET", base.rstrip("/") + ruta, timeout=15)
                if isinstance(meta, dict) and meta.get("authorization_endpoint"):
                    meta["_homeserver"] = base
                    return meta
            except Exception:
                continue
        return None

    # ---------------- Alta por navegador ----------------
    def crear_cuenta_oauth(self, homeserver: str, pedir_admin: bool = False, progreso=None) -> int:
        """Da de alta una cuenta autenticándose por NAVEGADOR (MAS/OAuth). Devuelve el account_id.
        `pedir_admin=True` pide además los permisos de administración del servidor (el servidor solo
        los concede si de verdad eres administrador). `progreso` es un callback opcional (str) para
        ir informando. Bloqueante: llamar desde task_manager."""
        def _p(msg):
            if progreso:
                try:
                    progreso(msg)
                except Exception:
                    pass

        _p(_("Buscando cómo iniciar sesión en el servidor..."))
        meta = self.descubrir_auth_navegador(homeserver)
        if not meta:
            raise MatrixError(_("Este servidor no usa inicio de sesión por navegador. Usa el alta "
                                "normal con usuario y contraseña."))
        base = meta.get("_homeserver") or normalizar_homeserver(homeserver)
        auth_ep = meta.get("authorization_endpoint") or ""
        token_ep = meta.get("token_endpoint") or ""
        reg_ep = meta.get("registration_endpoint") or ""
        if not (auth_ep and token_ep and reg_ep):
            raise MatrixError(_("El servidor no ofrece el registro de aplicaciones necesario para "
                                "iniciar sesión por navegador."))
        # Anti-SSRF / anti-downgrade: los tres endpoints los ANUNCIA el servidor; se exige que sean
        # https y que no apunten a direcciones internas antes de mandarles nada (registro, código,
        # token). Un homeserver hostil no puede así desviar el token a una dirección local.
        auth_ep = validar_endpoint_descubierto(auth_ep, "endpoint de autorización")
        token_ep = validar_endpoint_descubierto(token_ep, "endpoint de token")
        reg_ep = validar_endpoint_descubierto(reg_ep, "endpoint de registro")

        # Servidor local efímero que recogerá el redirect con el código de autorización.
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

        servidor = HTTPServer(("127.0.0.1", 0), _Handler)
        puerto = servidor.server_address[1]
        redirect_uri = f"http://localhost:{puerto}/"
        hilo = threading.Thread(target=servidor.serve_forever, name="MatrixOAuthCallback",
                                daemon=True)
        hilo.start()
        try:
            # 1) Registro dinámico de la aplicación.
            _p(_("Registrando la aplicación en el servidor..."))
            reg = self._http_json("POST", reg_ep, {
                "application_type": "native",
                "client_name": "SocialRSS Suite",
                "client_uri": "https://github.com/xebolax",
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "redirect_uris": [redirect_uri],
            })
            client_id = reg.get("client_id")
            if not client_id:
                raise MatrixError(_("El servidor no devolvió el identificador de aplicación."))

            # 2) PKCE (S256), device_id de cliente y state anti-suplantación.
            device_id = secrets.token_hex(5).upper()  # 10 caracteres
            verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
            state = secrets.token_urlsafe(24)
            scope = _SCOPE_API + " " + _SCOPE_DEVICE.format(device_id)
            if pedir_admin:
                scope += " " + _SCOPE_ADMIN

            # 3) Abrir el navegador en el endpoint de autorización.
            params = urllib.parse.urlencode({
                "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
                "scope": scope, "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256"})
            url = auth_ep + ("&" if "?" in auth_ep else "?") + params
            from app.core.privacy import abrir_url
            abrir_url(url)
            _p(_("Se ha abierto el navegador. Inicia sesión y aprueba el acceso; luego vuelve aquí."))

            # 4) Esperar el redirect con el código (hasta 5 minutos).
            if not evento.wait(timeout=300):
                raise MatrixError(_("Se agotó el tiempo esperando a que inicies sesión en el navegador."))
            if resultado.get("error"):
                raise MatrixError(_("El servidor rechazó el acceso: {0}").format(
                    resultado.get("error_description") or resultado.get("error")))
            if resultado.get("state") != state:
                raise MatrixError(_("La respuesta del navegador no coincide con la petición; se ha "
                                    "cancelado por seguridad."))
            code = resultado.get("code")
            if not code:
                raise MatrixError(_("El navegador no devolvió el código de autorización."))

            # 5) Intercambiar el código por el token.
            _p(_("Obteniendo el token de acceso..."))
            tok = self._http_json("POST", token_ep, {
                "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "client_id": client_id, "redirect_uri": redirect_uri}, form=True)
            access_token = tok.get("access_token")
            refresh_token = tok.get("refresh_token") or ""
            if not access_token:
                raise MatrixError(_("El servidor no devolvió un token de acceso."))
        finally:
            try:
                servidor.shutdown()
            except Exception:
                pass

        # 6) whoami → user_id y device_id definitivos.
        _p(_("Comprobando la cuenta..."))
        who = self._http_json("GET", base.rstrip("/") + "/_matrix/client/v3/account/whoami",
                              headers={"Authorization": f"Bearer {access_token}"})
        user_id = who.get("user_id") or ""
        dev = who.get("device_id") or device_id
        if not user_id:
            raise MatrixError(_("No se pudo obtener tu identificador de usuario tras iniciar sesión."))

        # 7) Persistir (mismo almacenamiento que el login normal; el token va cifrado). Además se
        #    guardan los datos para RENOVAR el token cuando caduque (los de MAS caducan): el
        #    refresh_token, el token_endpoint y el client_id.
        # Los datos para RENOVAR el token cuando caduque (refresh_token, token_endpoint, client_id)
        # se guardan en el MISMO bloque atómico que el access_token: así el alta OAuth es todo o nada
        # y no quedan cuentas a medias si falla el almacenamiento (MX-019).
        extra = {}
        if refresh_token:
            extra = {"refresh_token": refresh_token, "oauth_token_endpoint": token_ep,
                     "oauth_client_id": client_id}
        account_id = self._persistir_cuenta(base, user_id, dev, access_token, secretos_extra=extra)
        log.info(f"Matrix: cuenta {user_id} dada de alta por navegador (OAuth/MAS), cuenta {account_id}.")
        return account_id

    # ---------------- Renovación del token (MAS) ----------------
    def refrescar_token_oauth(self, account_id: int):
        """Renueva el access_token con el refresh_token en el token_endpoint del servidor (OAuth/MAS).
        Devuelve el nuevo access_token, o None si esta cuenta NO es de navegador o no se pudo. MAS
        suele ROTAR el refresh_token (de un solo uso), así que se guarda el nuevo. Bloqueante
        (urllib): NO llamar desde el bucle asyncio; sí desde task_manager o desde conectar()."""
        refresh = security.obtener_credencial("matrix", f"{account_id}:refresh_token")
        token_ep = security.obtener_credencial("matrix", f"{account_id}:oauth_token_endpoint")
        client_id = security.obtener_credencial("matrix", f"{account_id}:oauth_client_id")
        if not (refresh and token_ep and client_id):
            return None  # cuenta clásica (contraseña): no hay nada que refrescar
        try:
            tok = self._http_json("POST", token_ep, {
                "grant_type": "refresh_token", "refresh_token": refresh,
                "client_id": client_id}, form=True)
        except Exception as e:
            log.warning(f"Matrix: no se pudo renovar el token OAuth (cuenta {account_id}): {e}")
            return None
        nuevo = tok.get("access_token")
        if not nuevo:
            return None
        security.guardar_credencial("matrix", f"{account_id}:access_token", nuevo)
        nuevo_refresh = tok.get("refresh_token")
        if nuevo_refresh:
            security.guardar_credencial("matrix", f"{account_id}:refresh_token", nuevo_refresh)
        log.info(f"Matrix: token OAuth renovado para la cuenta {account_id}.")
        return nuevo
