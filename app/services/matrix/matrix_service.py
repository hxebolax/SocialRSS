# -*- coding: utf-8 -*-
"""
Servicio del cliente de Matrix (matrix-nio).

Ensamblador: la clase `MatrixService` hereda de los mixins por dominio (patrón
telegram_service/irc_service). Los símbolos comunes viven en `matrix_common`.
"""
from app.services.matrix.matrix_common import (
    asyncio, threading, Any, Dict, Optional, HAS_NIO, MatrixError, log)

from app.services.matrix.matrix_nucleo import _NucleoMixin
from app.services.matrix.matrix_cuentas import _CuentasMixin
from app.services.matrix.matrix_mensajes import _MensajesMixin
from app.services.matrix.matrix_medios import _MediosMixin
from app.services.matrix.matrix_e2ee import _E2EEMixin
from app.services.matrix.matrix_producto import _ProductoMixin
from app.services.matrix.matrix_avanzado import _AvanzadoMixin
from app.services.matrix.matrix_crosssigning import _CrossSigningMixin
from app.services.matrix.matrix_admin import _AdminMixin
from app.services.matrix.matrix_admin_ext import _AdminExtMixin
from app.services.matrix.matrix_oauth import _OAuthMixin


class MatrixService(_NucleoMixin, _CuentasMixin, _MensajesMixin, _MediosMixin, _E2EEMixin,
                    _ProductoMixin, _AvanzadoMixin, _CrossSigningMixin, _AdminMixin,
                    _AdminExtMixin, _OAuthMixin):
    """Administrador del cliente de Matrix (un AsyncClient + tarea de sync por cuenta)."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: Dict[int, Any] = {}       # account_id -> AsyncClient
        self._sync_tasks: Dict[int, Any] = {}    # account_id -> asyncio.Task del sync
        self._lock = threading.Lock()
        self._estado_lock = threading.Lock()
        self._conectadas: Dict[int, bool] = {}   # primer sync recibido
        self._sync_inicial: Dict[int, bool] = {} # True durante el relleno inicial (sin sonidos)
        self._hubo_caida: Dict[int, bool] = {}   # para anunciar la reconexión una vez
        self._dms: Dict[int, set] = {}           # account_id -> room_ids que son DM (m.direct)
        self._user_ids: Dict[int, str] = {}      # caché de user_id por cuenta
        self._page_tokens: Dict[str, str] = {}   # "acc:room" -> token de paginación hacia atrás
        self._ignorados: Dict[int, set] = {}     # account_id -> user_ids ignorados (M7)
        self._limites_subida: Dict[int, int] = {}  # account_id -> bytes máx. de subida (M7)
        self._reacciones_propias: Dict[tuple, str] = {}  # (acc, event, emoji) -> event_id reacción
        self._xsign_pendiente: Dict[int, Any] = {}  # cross-signing en espera de aprobación (M9)
        self._recibos: Dict[tuple, Dict[str, Any]] = {}  # (acc,room) -> {user_id: (event_id, ts_ms)} recibos de lectura
        self._fallo_persistencia: Dict[int, bool] = {}  # MX-001: fallo al guardar en el lote de sync en curso
        self._prev_batch: Dict[str, str] = {}   # MX-010: "acc|room" -> prev_batch de la timeline (paginar hacia atrás)


# Instancia global única del servicio de Matrix
matrix_service = MatrixService()
