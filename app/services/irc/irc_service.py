# -*- coding: utf-8 -*-
"""
Servicio del cliente de IRC (pydle).

Ensamblador: la clase `IRCService` hereda de los mixins por dominio (patrón
telegram_service). Los símbolos comunes viven en `irc_common`.
"""
from app.services.irc.irc_common import (
    asyncio, threading, Any, Dict, Optional, HAS_PYDLE, IRCError, log)

from app.services.irc.irc_nucleo import _NucleoMixin
from app.services.irc.irc_redes import _RedesMixin
from app.services.irc.irc_mensajes import _MensajesMixin
from app.services.irc.irc_canales import _CanalesMixin
from app.services.irc.irc_historial import _HistorialMixin
from app.services.irc.irc_avanzado import _AvanzadoMixin


class IRCService(_NucleoMixin, _RedesMixin, _MensajesMixin, _CanalesMixin, _HistorialMixin,
                 _AvanzadoMixin):
    """Administrador del cliente de IRC (una conexión pydle por red)."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: Dict[int, Any] = {}      # account_id -> cliente pydle
        self._lock = threading.Lock()
        self._conectadas: Dict[int, bool] = {}  # account_id -> registrada en la red
        self._estado_lock = threading.Lock()


# Instancia global única del servicio de IRC
irc_service = IRCService()
