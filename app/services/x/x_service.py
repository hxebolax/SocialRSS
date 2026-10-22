# -*- coding: utf-8 -*-
"""
Servicio del cliente no oficial de X (Twitter) mediante twikit.

Ensamblador: `XService` hereda de los mixins por dominio. Los símbolos comunes
(twikit, XServiceError, helper `_g`) viven en `x_common`.
"""
from app.services.x.x_common import (
    asyncio, threading, Any, Dict, Optional, HAS_TWIKIT, Client, XServiceError, log)

from app.services.x.x_sesion import _SesionMixin
from app.services.x.x_timeline import _TimelineMixin
from app.services.x.x_extras import _ExtrasMixin
from app.services.x.x_dms import _DmsMixin
from app.services.x.x_twscrape import _TwscrapeMixin
from app.services.x.x_router import _RouterMixin
from app.services.x.x_gestion import _GestionMixin

class XService(_SesionMixin, _TimelineMixin, _GestionMixin, _ExtrasMixin, _DmsMixin,
               _TwscrapeMixin, _RouterMixin):
    """Administrador de cuentas y operaciones de X (Twitter).

    Combina DOS motores complementarios: twikit (lee y ESCRIBE) y twscrape (solo lectura, scraper
    muy mantenido con endpoints extra). El router (`_RouterMixin`) decide por operación cuál usar,
    con respaldo cruzado; la escritura siempre va por twikit."""

    NS = "x"  # namespace para credenciales cifradas: f"x:{account_id}"

    # Enfriamiento global entre reimportaciones silenciosas de cookies (segundos): evita
    # tormentas de reimport cuando varios buffers/acciones fallan por sesión caducada a la vez.
    _REAUTH_COOLDOWN = 45.0

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: Dict[int, Any] = {}
        self._me: Dict[int, Dict[str, str]] = {}
        # Caché de objetos API de twscrape por cuenta (motor de lectura complementario).
        self._tws_apis: Dict[int, Any] = {}
        self._cursors: Dict[str, Optional[str]] = {}
        # Sesiones de streaming de DMs en curso, por conversation_id (tiempo real).
        self._dm_streams: Dict[str, Any] = {}
        self._lock = threading.Lock()
        # Estado de la reautenticación automática (reconexión por cookies).
        self._reauth_lock = threading.Lock()
        self._ultimo_reauth = 0.0


# Instancia global única del servicio de X.
x_service = XService()
