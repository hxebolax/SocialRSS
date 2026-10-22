# -*- coding: utf-8 -*-
"""Controlador de la interfaz de Reddit (fachada).

Ensambla los mixins por dominio (carga/render y acciones), como el controlador de Bluesky. Las
etiquetas de los buffers y el orden viven aquí; la lógica, en los mixins.
"""
import logging

from app.ui.reddit_controller_carga import _CargaMixin
from app.ui.reddit_controller_acciones import _AccionesMixin
from app.ui.reddit_controller_social import _SocialUIMixin
from app.ui.reddit_controller_moderacion import _ModeracionUIMixin
from app.ui.reddit_controller_fase3 import _Fase3UIMixin
from app.ui.reddit_controller_fase4 import _Fase4UIMixin

log = logging.getLogger(__name__)

# Etiquetas visibles de los buffers fijos (clave -> texto). El orden lo decide
# config("reddit", "buffer_order"); los buffers ocultos se omiten.
BUFFER_LABELS = {
    "home": _("Página principal"),
    "popular": _("Populares"),
    "all": _("Todo (r/all)"),
    "inbox": _("Bandeja de entrada"),
    "unread": _("Mensajes sin leer"),
    "mentions": _("Menciones"),
    "sent": _("Mensajes enviados"),
    "submitted": _("Mis publicaciones"),
    "comments": _("Mis comentarios"),
    "saved": _("Guardados"),
    "upvoted": _("Votados positivos"),
    "downvoted": _("Votados negativos"),
    "hidden": _("Ocultos"),
    "subreddits": _("Mis subreddits"),
    "multireddits": _("Multireddits"),
    "searches": _("Búsquedas guardadas"),
}

_TODOS = tuple(BUFFER_LABELS.keys())


class RedditController(_CargaMixin, _AccionesMixin, _SocialUIMixin, _ModeracionUIMixin,
                       _Fase3UIMixin, _Fase4UIMixin):
    """Controlador de Reddit acoplado al MainFrame (self.f)."""

    BUFFER_LABELS = BUFFER_LABELS
    _TODOS = _TODOS

    def __init__(self, frame):
        self.f = frame
