# -*- coding: utf-8 -*-
"""Autenticación (OAuth2 usuario/contraseña) y gestión de cuentas de PeerTube (Fase 1).

Mixin de `PeerTubeService`. El flujo, verificado contra el OpenAPI, NO exige registrar ninguna app:
1. `GET /oauth-clients/local` → client_id/client_secret de la instancia (anónimo).
2. `POST /users/token` (grant `password`) con usuario+contraseña → access token (1 día) + refresh
   token (2 semanas). Si la cuenta tiene 2FA, el login responde 401 `missing_two_factor` y se
   reintenta con la cabecera `x-peertube-otp`.
3. El access token se renueva con grant `refresh_token` cuando está a punto de caducar; el logout es
   `POST /users/revoke-token`.

Los tokens se guardan CIFRADOS (security/DPAPI) y nunca se registran en logs. Los datos no secretos
(instancia, usuario, rol) van en `peertube_cuenta_meta` para consultarlos sin descifrar.
"""
import time
import logging
import threading
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from app.core.database import db
from app.core.security import security
from app.core.errors import PeerTubeServiceError
from app.services.peertube.peertube_common import _ahora_iso, normalizar_base_url
from app.services.peertube.peertube_modelos import OAuthClient, OAuthToken, parsear

log = logging.getLogger(__name__)

# Cabecera del segundo factor y margen (segundos) para renovar el token ANTES de que caduque.
CABECERA_OTP = "x-peertube-otp"
MARGEN_RENOVACION = 120.0
# Duraciones por defecto si la instancia no las informa (1 día / 2 semanas).
EXPIRA_DEFECTO = 86400
REFRESH_EXPIRA_DEFECTO = 1209600

# Roles de PeerTube (role.id): 0 admin, 1 moderador, 2 usuario.
_ROLES = {0: "administrator", 1: "moderator", 2: "user"}


class DosFactoresRequerido(PeerTubeServiceError):
    """El login necesita el código de doble factor (2FA). El llamante debe pedir el OTP y reintentar."""


class _AuthMixin:
    # ------------------------------------------------------------------
    # Cliente OAuth local de la instancia (client_id/client_secret)
    # ------------------------------------------------------------------
    def _oauth_client(self, cliente, host: str) -> OAuthClient:
        """Obtiene (y cachea por instancia) el client_id/client_secret local de la instancia."""
        cache = getattr(self, "_oauth_cache", None)
        if cache is None:
            cache = {}
            self._oauth_cache = cache
        if host in cache:
            return cache[host]
        datos = cliente.get("oauth-clients/local", auth=False)
        oc = parsear(OAuthClient, datos)
        cache[host] = oc
        return oc

    # ------------------------------------------------------------------
    # Login (password grant, con 2FA)
    # ------------------------------------------------------------------
    def _login(self, base_url: str, username: str, password: str,
               otp: Optional[str] = None) -> Dict[str, Any]:
        """Inicia sesión contra la instancia. Devuelve {client_id, client_secret, token: OAuthToken}.
        Lanza `DosFactoresRequerido` si falta el 2FA, o `PeerTubeServiceError` con mensaje claro."""
        host = urlsplit(base_url).netloc
        cliente = self.cliente_para(base_url)
        try:
            oc = self._oauth_client(cliente, host)
            data = {"client_id": oc.client_id, "client_secret": oc.client_secret,
                    "grant_type": "password", "username": username, "password": password}
            headers = {CABECERA_OTP: otp} if otp else None
            resp = cliente.solicitar_cruda("POST", "users/token", data=data, headers=headers,
                                           auth=False)
            if 200 <= resp.status_code < 300:
                return {"client_id": oc.client_id, "client_secret": oc.client_secret,
                        "token": parsear(OAuthToken, resp.json())}
            self._lanzar_error_login(resp)
        finally:
            cliente.cerrar()

    @staticmethod
    def _lanzar_error_login(resp) -> None:
        """Traduce un error del endpoint de token a una excepción clara (por `code`)."""
        codigo = ""
        detalle = ""
        try:
            cuerpo = resp.json()
            if isinstance(cuerpo, dict):
                codigo = str(cuerpo.get("code") or "").strip()
                detalle = str(cuerpo.get("error") or cuerpo.get("detail")
                              or cuerpo.get("error_description") or "").strip()
        except Exception:
            pass
        if codigo == "missing_two_factor" or (resp.status_code == 401 and "two_factor" in detalle):
            raise DosFactoresRequerido("Esta cuenta tiene activada la verificación en dos pasos. "
                                       "Introduce el código de tu aplicación de autenticación.")
        if codigo == "invalid_grant" or resp.status_code in (400, 401):
            raise PeerTubeServiceError("Usuario o contraseña incorrectos (o el código de doble factor "
                                       "no es válido).")
        if codigo == "invalid_client":
            raise PeerTubeServiceError("La instancia no aceptó al cliente OAuth. Inténtalo de nuevo.")
        raise PeerTubeServiceError(
            f"No se pudo iniciar sesión (HTTP {resp.status_code}). {detalle}".strip())

    # ------------------------------------------------------------------
    # Alta de cuenta
    # ------------------------------------------------------------------
    def iniciar_alta(self, instancia: str, username: str, password: str, *,
                     otp: Optional[str] = None, progreso: Optional[Callable[[str], None]] = None) -> dict:
        """Da de alta una cuenta: detecta la instancia, inicia sesión y guarda la sesión cifrada.
        Devuelve {account_id, username, instancia}. Bloqueante: llamar desde task_manager."""
        def _p(msg):
            if progreso:
                try:
                    progreso(msg)
                except Exception:
                    pass

        username = (username or "").strip()
        if not username or not password:
            raise PeerTubeServiceError("El usuario y la contraseña son obligatorios.")

        _p("Comprobando la instancia...")
        info = self.detectar_capacidades(instancia)
        base_url = info["base_url"]
        host = info["host"]

        _p("Iniciando sesión...")
        sesion = self._login(base_url, username, password, otp=otp)
        token = sesion["token"]

        _p("Comprobando la cuenta...")
        me = self._obtener_me(base_url, token.access_token)
        username_real = str((me or {}).get("username") or username)
        rol = self._rol_de_me(me)

        # Evitar duplicados: misma instancia + mismo usuario.
        existente = self._cuenta_existente(host, username_real)
        if existente:
            raise PeerTubeServiceError(
                f"Esa cuenta ya está añadida ({username_real}@{host}). Si su sesión caducó, usa "
                "'Volver a iniciar sesión' en el gestor de cuentas.")

        nombre = f"{username_real}@{host}"
        row_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) "
            "VALUES (?, 'peertube', 1, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))",
            (nombre,))

        ok = self._persistir_sesion(row_id, base_url, host, username_real, rol,
                                    sesion["client_id"], sesion["client_secret"], token)
        if ok is False:
            db.execute("DELETE FROM accounts WHERE id = ?", (row_id,))
            raise PeerTubeServiceError(
                "No se pudieron guardar las credenciales en el almacén seguro; el alta se ha "
                "deshecho. Revisa el espacio en disco y vuelve a intentarlo.")

        log.info(f"Cuenta de PeerTube registrada: {nombre} (ID {row_id}, rol {rol}).")
        return {"account_id": row_id, "username": username_real, "instancia": host, "rol": rol}

    def reautenticar(self, account_id: int, password: str, *, otp: Optional[str] = None) -> dict:
        """Vuelve a iniciar sesión en una cuenta EXISTENTE (misma instancia y usuario), renovando sus
        tokens. Bloqueante."""
        base_url = self.instancia_de(account_id)
        username = self.nombre_usuario(account_id)
        if not base_url or not username:
            raise PeerTubeServiceError("La cuenta no tiene instancia o usuario guardados. Elimínala y "
                                       "vuelve a añadirla.")
        host = urlsplit(base_url).netloc
        sesion = self._login(base_url, username, password, otp=otp)
        token = sesion["token"]
        me = self._obtener_me(base_url, token.access_token)
        rol = self._rol_de_me(me)
        ok = self._persistir_sesion(account_id, base_url, host, username, rol,
                                    sesion["client_id"], sesion["client_secret"], token)
        if ok is False:
            raise PeerTubeServiceError("No se pudieron guardar las credenciales renovadas.")
        self.invalidar_cliente(account_id)
        log.info(f"Cuenta de PeerTube {account_id} vuelta a autenticar ({username}@{host}).")
        return {"account_id": account_id, "username": username, "instancia": host, "rol": rol}

    # ------------------------------------------------------------------
    # Renovación / revocación / sesión vigente
    # ------------------------------------------------------------------
    def refrescar_token(self, account_id: int) -> bool:
        """Renueva el access token con el refresh token. Devuelve True si se renovó. Marca la sesión
        como caducada (y lanza) si el refresh también expiró o la instancia lo rechaza."""
        sesion = self._sesiones.get(account_id) or self._cargar_sesion_cuenta(account_id)
        if not sesion:
            raise PeerTubeServiceError("No hay sesión guardada para esta cuenta. Vuelve a iniciar sesión.")
        base_url = sesion.get("instancia")
        refresh = sesion.get("refresh_token")
        cid = sesion.get("client_id")
        secret = sesion.get("client_secret")
        if not (base_url and refresh and cid and secret):
            self._sesiones_revocadas.add(account_id)
            raise PeerTubeServiceError("Faltan datos de sesión. Vuelve a iniciar sesión en esta cuenta.")
        host = urlsplit(base_url).netloc
        cliente = self.cliente_para(base_url)
        try:
            data = {"client_id": cid, "client_secret": secret, "grant_type": "refresh_token",
                    "refresh_token": refresh}
            resp = cliente.solicitar_cruda("POST", "users/token", data=data, auth=False)
            if not (200 <= resp.status_code < 300):
                self._sesiones_revocadas.add(account_id)
                raise PeerTubeServiceError(
                    "La sesión de esta cuenta caducó. Vuelve a iniciar sesión en el gestor de cuentas.")
            token = parsear(OAuthToken, resp.json())
        finally:
            cliente.cerrar()
        self._persistir_sesion(account_id, base_url, host, sesion.get("username"),
                               sesion.get("rol"), cid, secret, token)
        self._sesiones_revocadas.discard(account_id)
        return True

    def _asegurar_sesion(self, account_id: int) -> None:
        """Garantiza un access token vigente: lo carga si hace falta y lo renueva si está caducado o a
        punto de caducar. Lanza si la cuenta necesita volver a iniciar sesión."""
        if account_id in self._sesiones_revocadas:
            raise PeerTubeServiceError(
                "La sesión de esta cuenta de PeerTube caducó. Vuelve a iniciar sesión.")
        sesion = self._sesiones.get(account_id)
        if not sesion:
            sesion = self._cargar_sesion_cuenta(account_id)
        if not sesion or not sesion.get("access_token"):
            raise PeerTubeServiceError("No hay sesión para esta cuenta. Vuelve a iniciar sesión.")
        expira = float(sesion.get("token_expira") or 0)
        if expira and time.time() >= (expira - MARGEN_RENOVACION):
            self.refrescar_token(account_id)

    def revocar_token(self, account_id: int) -> None:
        """Cierra la sesión en el servidor (best-effort; no lanza si falla)."""
        sesion = self._sesiones.get(account_id) or self._cargar_sesion_cuenta(account_id)
        if not sesion or not sesion.get("access_token") or not sesion.get("instancia"):
            return
        try:
            cliente = self.obtener_cliente(account_id)
            cliente.post("users/revoke-token", auth=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Ejecutar una operación autenticada (renueva y reintenta ante 401)
    # ------------------------------------------------------------------
    def _ejecutar(self, account_id: int, fn: Callable[[Any], Any]) -> Any:
        """Ejecuta `fn(cliente_http)` garantizando la sesión. Si la instancia responde 401 (token
        recién invalidado), renueva UNA vez y reintenta. Clasifica el resto de errores."""
        self._asegurar_sesion(account_id)
        cliente = self.obtener_cliente(account_id)
        try:
            return fn(cliente)
        except PeerTubeServiceError as e:
            if "caduc" in str(e).lower() or "401" in str(e) or "no válida" in str(e).lower():
                try:
                    self.refrescar_token(account_id)
                except PeerTubeServiceError:
                    raise
                return fn(self.obtener_cliente(account_id))
            raise

    # ------------------------------------------------------------------
    # Consultas de cuenta
    # ------------------------------------------------------------------
    def nombre_usuario(self, account_id: int) -> str:
        sesion = self._sesiones.get(account_id)
        if sesion and sesion.get("username"):
            return sesion["username"]
        fila = db.query_one("SELECT username FROM peertube_cuenta_meta WHERE account_id = ?",
                            (account_id,))
        return (fila[0] if fila and fila[0] else
                security.obtener_credencial(f"peertube:{account_id}", "username") or "")

    def rol(self, account_id: int) -> str:
        sesion = self._sesiones.get(account_id)
        if sesion and sesion.get("rol"):
            return sesion["rol"]
        fila = db.query_one("SELECT rol FROM peertube_cuenta_meta WHERE account_id = ?", (account_id,))
        return (fila[0] if fila and fila[0] else "user")

    def es_administrador(self, account_id: int) -> bool:
        return self.rol(account_id) == "administrator"

    def es_moderador(self, account_id: int) -> bool:
        return self.rol(account_id) in ("administrator", "moderator")

    # ------------------------------------------------------------------
    # Carga de sesiones al arrancar y eliminación de cuenta
    # ------------------------------------------------------------------
    def cargar_sesiones(self) -> int:
        """Carga en memoria las sesiones de todas las cuentas de PeerTube (desde el almacén cifrado).
        Se llama una vez al arrancar. Devuelve cuántas cuentas se cargaron."""
        n = 0
        try:
            filas = db.query("SELECT id FROM accounts WHERE service_id = 'peertube'")
        except Exception:
            filas = []
        for (acc_id,) in filas:
            if self._cargar_sesion_cuenta(acc_id):
                n += 1
        log.info(f"PeerTube: {n} sesiones cargadas al arrancar.")
        return n

    def eliminar_cuenta(self, account_id: int) -> bool:
        """Elimina la cuenta: revoca la sesión (best-effort), borra sus filas (CASCADE limpia el
        contenido y la meta), sus credenciales y toda la caché en memoria."""
        try:
            self.revocar_token(account_id)
        except Exception:
            pass
        try:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        except Exception as e:
            log.error(f"Error al borrar la cuenta de PeerTube {account_id}: {e}")
            return False
        for clave in ("access_token", "refresh_token", "client_id", "client_secret",
                      "instancia", "username", "token_expira", "refresh_expira"):
            try:
                security.borrar_credencial(f"peertube:{account_id}", clave)
            except Exception:
                pass
        self.invalidar_cliente(account_id)
        self._sesiones.pop(account_id, None)
        self._sesiones_revocadas.discard(account_id)
        log.info(f"Cuenta de PeerTube {account_id} eliminada.")
        return True

    # ------------------------------------------------------------------
    # Persistencia de la sesión
    # ------------------------------------------------------------------
    def _persistir_sesion(self, account_id: int, base_url: str, host: str, username: str,
                          rol: str, client_id: str, client_secret: str, token: OAuthToken) -> bool:
        """Guarda los tokens cifrados, actualiza `peertube_cuenta_meta` y la sesión en memoria."""
        ahora = time.time()
        token_expira = ahora + float(token.expires_in or EXPIRA_DEFECTO)
        refresh_expira = ahora + float(token.refresh_token_expires_in or REFRESH_EXPIRA_DEFECTO)
        ok = security.guardar_credenciales({
            (f"peertube:{account_id}", "access_token"): token.access_token,
            (f"peertube:{account_id}", "refresh_token"): token.refresh_token or "",
            (f"peertube:{account_id}", "client_id"): client_id,
            (f"peertube:{account_id}", "client_secret"): client_secret,
            (f"peertube:{account_id}", "instancia"): base_url,
            (f"peertube:{account_id}", "username"): username,
            (f"peertube:{account_id}", "token_expira"): str(token_expira),
            (f"peertube:{account_id}", "refresh_expira"): str(refresh_expira),
        })
        if ok is False:
            return False
        try:
            db.execute(
                "INSERT OR REPLACE INTO peertube_cuenta_meta "
                "(account_id, instancia, username, rol, ultima_sync) VALUES (?,?,?,?,?)",
                (account_id, base_url, username, rol, _ahora_iso()))
        except Exception as e:
            log.debug(f"No se pudo guardar la meta de la cuenta {account_id}: {e}")
        self._instancias[account_id] = normalizar_base_url(base_url)
        self._sesiones[account_id] = {
            "access_token": token.access_token, "refresh_token": token.refresh_token or "",
            "client_id": client_id, "client_secret": client_secret, "instancia": base_url,
            "username": username, "rol": rol, "token_expira": token_expira,
            "refresh_expira": refresh_expira}
        return True

    def _cargar_sesion_cuenta(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Carga la sesión de una cuenta desde el almacén cifrado a `self._sesiones`. None si no hay
        credenciales guardadas."""
        obtener = lambda k: security.obtener_credencial(f"peertube:{account_id}", k)
        access = obtener("access_token")
        instancia = obtener("instancia")
        if not (access and instancia):
            return None
        try:
            token_expira = float(obtener("token_expira") or 0)
        except (TypeError, ValueError):
            token_expira = 0.0
        try:
            refresh_expira = float(obtener("refresh_expira") or 0)
        except (TypeError, ValueError):
            refresh_expira = 0.0
        sesion = {
            "access_token": access, "refresh_token": obtener("refresh_token") or "",
            "client_id": obtener("client_id") or "", "client_secret": obtener("client_secret") or "",
            "instancia": instancia, "username": obtener("username") or "",
            "rol": self.rol(account_id), "token_expira": token_expira,
            "refresh_expira": refresh_expira}
        self._sesiones[account_id] = sesion
        return sesion

    # ------------------------------------------------------------------
    # /users/me y rol
    # ------------------------------------------------------------------
    def _obtener_me(self, base_url: str, access_token: str) -> dict:
        """Lee `GET /users/me` con un access token concreto (sin depender de la caché de sesión)."""
        cliente = self.cliente_para(base_url, token_provider=lambda: access_token)
        try:
            datos = cliente.get("users/me", auth=True)
            return datos if isinstance(datos, dict) else {}
        finally:
            cliente.cerrar()

    @staticmethod
    def _rol_de_me(me: dict) -> str:
        """Extrae el rol ('user'/'moderator'/'administrator') de la respuesta de /users/me."""
        try:
            rid = ((me or {}).get("role") or {}).get("id")
            if rid is not None:
                return _ROLES.get(int(rid), "user")
        except Exception:
            pass
        return "user"

    def _cuenta_existente(self, host: str, username: str) -> Optional[int]:
        """account_id de una cuenta ya registrada con ese usuario en esa instancia, o None."""
        objetivo = f"{username}@{host}".lower()
        try:
            filas = db.query(
                "SELECT account_id, instancia, username FROM peertube_cuenta_meta")
        except Exception:
            filas = []
        for acc_id, inst, user in filas:
            h = urlsplit(inst or "").netloc
            if f"{(user or '').lower()}@{h.lower()}" == objetivo:
                return acc_id
        return None
