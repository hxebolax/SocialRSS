# -*- coding: utf-8 -*-
"""Servicio central de integración con Reddit usando la biblioteca `praw` (síncrona).

Ensamblador: la clase `RedditService` hereda de los mixins por dominio. Los símbolos comunes
(PRAW, constantes, helpers) viven en `reddit_common`. En la Fase 0 solo está el mixin de sesión;
los mixins de timeline/publicar/social/inbox se añaden en fases posteriores.
"""
import threading

from app.services.reddit.reddit_common import (  # noqa: F401  (re-exportados para los mixins)
    Dict, Any, Optional, HAS_PRAW, Reddit, RedditServiceError,
    db, security, task_manager, config, log)

from app.services.reddit.reddit_sesion import _SesionMixin
from app.services.reddit.reddit_timeline import _TimelineMixin
from app.services.reddit.reddit_comentarios import _ComentariosMixin
from app.services.reddit.reddit_acciones import _AccionesServMixin
from app.services.reddit.reddit_publicar import _PublicarMixin
from app.services.reddit.reddit_inbox import _InboxMixin
from app.services.reddit.reddit_social import _SocialMixin
from app.services.reddit.reddit_moderacion import _ModeracionMixin
from app.services.reddit.reddit_wiki import _WikiMixin
from app.services.reddit.reddit_personalizacion import _PersonalizacionMixin
from app.services.reddit.reddit_modmail import _ModmailMixin
from app.services.reddit.reddit_avanzado import _AvanzadoMixin


class RedditService(_SesionMixin, _TimelineMixin, _ComentariosMixin, _AccionesServMixin,
                    _PublicarMixin, _InboxMixin, _SocialMixin, _ModeracionMixin,
                    _WikiMixin, _PersonalizacionMixin, _ModmailMixin, _AvanzadoMixin):
    """Administrador de cuentas y operaciones de la API de Reddit (PRAW)."""

    def __init__(self):
        # Un cliente `praw.Reddit` por cuenta (cacheado). PRAW no es thread-safe al compartir una
        # instancia entre hilos que rotan el token; con un cliente por cuenta y un lock por cuenta
        # los refrescos de token se serializan correctamente.
        self._clients: Dict[int, Any] = {}
        self._me: Dict[int, Dict[str, str]] = {}       # account_id -> {username}
        # Un lock reentrante por cuenta para crear/usar el cliente sin carreras entre tareas de fondo.
        self._client_locks: Dict[int, Any] = {}
        self._client_locks_guard = threading.Lock()
        # Cuentas con la sesión revocada/caducada (token muerto): obtener_cliente falla rápido sin
        # volver a golpear la API, para no martillear el login desde muchos sondeos de fondo.
        self._sesiones_revocadas: set = set()
        # Política central de límites (429): cuenta -> marca de tiempo hasta la que no reintentar.
        self._cooldown_429: Dict[int, float] = {}


# Instancia global única del servicio de Reddit
reddit_service = RedditService()
