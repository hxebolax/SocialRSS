# -*- coding: utf-8 -*-
"""
Servicio del cliente de XMPP (slixmpp).

Ensamblador: la clase `XMPPService` hereda de los mixins por dominio (patrón
matrix_service/irc_service, cuarta iteración del molde). Los símbolos comunes viven en
`xmpp_common`. Un ClientXMPP por cuenta, con el stream abierto en el hilo XMPPLoop.
"""
from app.services.xmpp.xmpp_common import (
    asyncio, threading, Any, Dict, Optional, HAS_SLIXMPP, XMPPError, log)

from app.services.xmpp.xmpp_nucleo import _NucleoMixin
from app.services.xmpp.xmpp_cuentas import _CuentasMixin
from app.services.xmpp.xmpp_roster import _RosterMixin
from app.services.xmpp.xmpp_mensajes import _MensajesMixin
from app.services.xmpp.xmpp_historial import _HistorialMixin
from app.services.xmpp.xmpp_muc import _MucMixin
from app.services.xmpp.xmpp_medios import _MediosMixin
from app.services.xmpp.xmpp_omemo import _OmemoMixin
from app.services.xmpp.xmpp_admin import _AdminMixin
from app.services.xmpp.xmpp_pasarelas import _PasarelasMixin
from app.services.xmpp.xmpp_perfil import _PerfilMixin


class XMPPService(_NucleoMixin, _CuentasMixin, _RosterMixin, _MensajesMixin, _HistorialMixin,
                  _MucMixin, _MediosMixin, _OmemoMixin, _AdminMixin, _PasarelasMixin, _PerfilMixin):
    """Administrador del cliente de XMPP (un ClientXMPP + stream por cuenta)."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: Dict[int, Any] = {}          # account_id -> ClientXMPP
        self._lock = threading.Lock()
        self._estado_lock = threading.Lock()
        self._conectadas: Dict[int, bool] = {}       # sesión iniciada (session_start)
        self._fallo_auth: Dict[int, bool] = {}       # último intento falló por credenciales
        self._jids: Dict[int, str] = {}              # caché de jid por cuenta
        # Presencia coalescida: la presencia es RUIDOSA (WhatsApp la emite sin parar). En vez de un
        # UPDATE+commit por stanza, se acumulan en este buffer y se vuelcan cada ~1,5 s en UNA sola
        # transacción, con un único evento de refresco por cuenta (la lista ya coalesce en la UI).
        self._presencia_buffer: Dict[Any, Any] = {}  # fila_id -> (etiqueta, estado_texto, account_id)
        self._presencia_lock = threading.Lock()
        self._presencia_flush_armado = False

    def libreria_disponible(self) -> bool:
        return HAS_SLIXMPP


# Instancia global única del servicio de XMPP
xmpp_service = XMPPService()
