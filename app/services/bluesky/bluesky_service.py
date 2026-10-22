# -*- coding: utf-8 -*-
"""
Servicio central de integración con Bluesky / AT Protocol usando la biblioteca `atproto`.

Ensamblador: la clase `BlueskyService` hereda de los mixins por dominio. Los símbolos
comunes (atproto, constantes, helpers) viven en `bluesky_common`.
"""
import threading

from app.services.bluesky.bluesky_common import (
    Dict, Any, Optional, HAS_ATPROTO, Client, models, BlueskyServiceError,
    db, security, task_manager, config, log, json)

from app.services.bluesky.bluesky_sesion import _SesionMixin
from app.services.bluesky.bluesky_timeline import _TimelineMixin
from app.services.bluesky.bluesky_publicar import _PublicarMixin
from app.services.bluesky.bluesky_social import _SocialMixin
from app.services.bluesky.bluesky_avanzado import _AvanzadoMixin
from app.services.bluesky.bluesky_preferencias import _PreferenciasMixin

class BlueskyService(_SesionMixin, _TimelineMixin, _PublicarMixin, _SocialMixin, _AvanzadoMixin,
                     _PreferenciasMixin):
    """Administrador de cuentas y operaciones de la API de Bluesky / AT Protocol."""

    def __init__(self):
        self._clients: Dict[int, Any] = {}
        self._chats: Dict[int, Any] = {}              # cliente con proxy de chat (DMs)
        self._me: Dict[int, Dict[str, str]] = {}      # account_id -> {did, handle}
        # Cursor de paginación por (account_id, timeline) para "Cargar más".
        self._cursors: Dict[str, Optional[str]] = {}
        # Un lock por cuenta para crear el Client UNA sola vez. Sin esto, varias tareas en
        # segundo plano creaban cada una su propio Client y reanudaban la sesión con el MISMO
        # refresh token a la vez; atproto lo ROTA en el primer refresh e invalida el resto
        # ("Token has been revoked"), tirando el login. Con un único Client por cuenta, el
        # _refresh_lock interno de atproto serializa correctamente los refrescos siguientes.
        self._client_locks: Dict[int, Any] = {}   # account_id -> RLock (reentrante)
        self._client_locks_guard = threading.Lock()
        # Cuentas cuya sesión está REVOCADA/CADUCADA (token muerto). Mientras una cuenta esté aquí,
        # obtener_cliente falla rápido SIN volver a golpear la API de login: así muchas tareas en
        # segundo plano (sondeos por columna, watchdog…) no martillean el servidor ni inundan el log
        # con el mismo error una y otra vez. Se limpia al reautenticar (nuevo login).
        self._sesiones_revocadas: set = set()
        # Política central de límites (auditoría 9.5): cuentas en "cooldown" tras un 429,
        # con la marca de tiempo hasta la que no se debe volver a pedir. obtener_cliente
        # falla rápido con la hora de reintento y la interfaz sigue usando la caché.
        self._cooldown_429: Dict[int, float] = {}
        # Caché handle -> (did, timestamp) para no resolver la misma mención en cada post.
        self._did_cache: Dict[str, Any] = {}


# Instancia global única del servicio de Bluesky
bluesky_service = BlueskyService()
