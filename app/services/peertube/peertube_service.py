# -*- coding: utf-8 -*-
"""Servicio central de integración con PeerTube (fachada).

`PeerTubeService` es la fachada de alto nivel: la interfaz habla con ella, nunca con httpx ni con los
detalles de los endpoints. Gestiona **un cliente HTTP por cuenta** (cacheado, con lock por cuenta) y
el estado de sesión (tokens) por cuenta. En fases posteriores se ensamblan aquí los mixins por
dominio (auth, instancias, vídeos, búsqueda, social, biblioteca, subida, directos, moderación,
administración...). En la Fase 0 solo está la infraestructura de clientes.
"""
import logging
import threading
from typing import Any, Dict, Optional

from app.core.database import db
from app.core.errors import PeerTubeServiceError
from app.services.peertube.peertube_common import HAS_HTTPX, normalizar_base_url
from app.services.peertube.peertube_http import ClienteHTTP
from app.services.peertube.peertube_instancia import _InstanciaMixin
from app.services.peertube.peertube_auth import _AuthMixin
from app.services.peertube.peertube_videos import _VideosMixin
from app.services.peertube.peertube_busqueda import _BusquedaMixin
from app.services.peertube.peertube_reproductor import _ReproductorMixin
from app.services.peertube.peertube_social import _SocialMixin
from app.services.peertube.peertube_comentarios import _ComentariosMixin
from app.services.peertube.peertube_biblioteca import _BibliotecaMixin
from app.services.peertube.peertube_subir import _SubirMixin
from app.services.peertube.peertube_importar import _ImportarMixin
from app.services.peertube.peertube_descargas import _DescargasMixin
from app.services.peertube.peertube_directos import _DirectosMixin
from app.services.peertube.peertube_notificaciones import _NotificacionesMixin
from app.services.peertube.peertube_estadisticas import _EstadisticasMixin
from app.services.peertube.peertube_moderacion import _ModeracionMixin
from app.services.peertube.peertube_admin import _AdminMixin

log = logging.getLogger(__name__)


class PeerTubeService(_InstanciaMixin, _AuthMixin, _VideosMixin, _BusquedaMixin, _ReproductorMixin,
                      _SocialMixin, _ComentariosMixin, _BibliotecaMixin, _SubirMixin, _ImportarMixin,
                      _DescargasMixin, _DirectosMixin, _NotificacionesMixin, _EstadisticasMixin,
                      _ModeracionMixin, _AdminMixin):
    """Administrador de cuentas y operaciones de la API de PeerTube (REST/httpx).

    Ensambla los mixins por dominio. En fases posteriores se añaden aquí los de vídeos, búsqueda,
    social, biblioteca, subida, directos, moderación y administración."""

    def __init__(self) -> None:
        # Un `ClienteHTTP` por cuenta (cacheado). Cada cuenta habla con SU instancia.
        self._clients: Dict[int, ClienteHTTP] = {}
        # Un lock por cuenta para crear/usar el cliente sin carreras entre tareas de fondo.
        self._client_locks: Dict[int, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        # Estado de sesión en memoria por cuenta: {account_id: {"access_token","refresh_token",
        # "instancia", "username", "expira", "refresh_expira"}}. Lo rellena el mixin de auth (Fase 1)
        # a partir de las credenciales cifradas; el token NUNCA se registra en logs.
        self._sesiones: Dict[int, Dict[str, Any]] = {}
        # Base URL (host) de la instancia por cuenta, cacheada para no consultarla en cada llamada.
        self._instancias: Dict[int, str] = {}
        # Cuentas con la sesión caducada/revocada (token muerto): fallan rápido sin martillear la API.
        self._sesiones_revocadas: set = set()
        # Caché del client_id/client_secret local por instancia (host -> OAuthClient).
        self._oauth_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Estado / disponibilidad
    # ------------------------------------------------------------------
    @staticmethod
    def disponible() -> bool:
        """True si la librería HTTP (httpx) está presente y el servicio puede operar."""
        return HAS_HTTPX

    # ------------------------------------------------------------------
    # Locks por cuenta
    # ------------------------------------------------------------------
    def _lock_cuenta(self, account_id: int) -> threading.RLock:
        with self._locks_guard:
            lock = self._client_locks.get(account_id)
            if lock is None:
                lock = threading.RLock()
                self._client_locks[account_id] = lock
            return lock

    # ------------------------------------------------------------------
    # Instancia (host) de una cuenta
    # ------------------------------------------------------------------
    def instancia_de(self, account_id: int) -> Optional[str]:
        """Base URL (https://host) de la instancia de una cuenta. La lee de `peertube_cuenta_meta`
        (la cachea). None si la cuenta no tiene instancia registrada todavía."""
        if account_id in self._instancias:
            return self._instancias[account_id]
        try:
            fila = db.query_one("SELECT instancia FROM peertube_cuenta_meta WHERE account_id = ?",
                                (account_id,))
        except Exception:
            fila = None
        if fila and fila[0]:
            try:
                base = normalizar_base_url(fila[0])
            except ValueError:
                base = None
            if base:
                self._instancias[account_id] = base
                return base
        return None

    def _token_provider(self, account_id: int):
        """Devuelve una función SIN argumentos que da el access token vigente de la cuenta (o None).
        Se pasa al `ClienteHTTP` para que la renovación de token (Fase 1) surta efecto al vuelo."""
        def _proveer() -> Optional[str]:
            sesion = self._sesiones.get(account_id) or {}
            return sesion.get("access_token")
        return _proveer

    # ------------------------------------------------------------------
    # Clientes HTTP
    # ------------------------------------------------------------------
    def cliente_para(self, base_url: str, *, token_provider=None, verify: bool = True) -> ClienteHTTP:
        """Crea un `ClienteHTTP` NUEVO (no cacheado) contra una URL de instancia. Útil para la
        detección de instancia anónima y para pruebas. La URL se normaliza a `https://host`."""
        if not HAS_HTTPX:  # pragma: no cover - entorno sin httpx
            raise PeerTubeServiceError("httpx no está disponible: PeerTube no puede funcionar.")
        base = normalizar_base_url(base_url)
        return ClienteHTTP(base, token_provider=token_provider, verify=verify)

    def obtener_cliente(self, account_id: int) -> ClienteHTTP:
        """Devuelve el `ClienteHTTP` cacheado de una cuenta (lo crea la primera vez con su instancia
        y su proveedor de token). Lanza `PeerTubeServiceError` si la cuenta no tiene instancia."""
        cliente = self._clients.get(account_id)
        if cliente is not None:
            return cliente
        with self._lock_cuenta(account_id):
            cliente = self._clients.get(account_id)
            if cliente is not None:
                return cliente
            base = self.instancia_de(account_id)
            if not base:
                raise PeerTubeServiceError(
                    "La cuenta de PeerTube no tiene una instancia asociada. Vuelve a añadirla.")
            cliente = ClienteHTTP(base, token_provider=self._token_provider(account_id))
            self._clients[account_id] = cliente
            return cliente

    def invalidar_cliente(self, account_id: int) -> None:
        """Cierra y descarta el cliente cacheado de una cuenta (p. ej. al cambiar de instancia)."""
        with self._lock_cuenta(account_id):
            cliente = self._clients.pop(account_id, None)
            self._instancias.pop(account_id, None)
            if cliente is not None:
                cliente.cerrar()

    def cerrar_todo(self) -> None:
        """Cierra todos los clientes HTTP (al salir de la app o desactivar el servicio)."""
        with self._locks_guard:
            ids = list(self._clients.keys())
        for account_id in ids:
            self.invalidar_cliente(account_id)


# Instancia global única del servicio de PeerTube.
peertube_service = PeerTubeService()
