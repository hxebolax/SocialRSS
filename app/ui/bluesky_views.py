# -*- coding: utf-8 -*-
"""Fachada del servicio Bluesky (UI): diálogos + BlueskyController (ensamblado de mixins).
"""
import builtins
if not hasattr(builtins, "_"):
    # Salvaguarda: BUFFER_LABELS usa _() en tiempo de import. En la app real, i18n ya
    # instaló el _ traducido antes de cargar la UI; esto solo cubre imports aislados (tests).
    builtins._ = lambda s: s
from .bluesky_dialogs import (
    _fila_botones, BlueskyLoginDialog, BlueskyComposeDialog, BlueskyThreadDialog,
    BlueskyConversationDialog, BlueskyMutedWordsDialog, BlueskyProfileDialog,
    BlueskyAppPasswordsDialog, BlueskyThreadEditorDialog)
from .bluesky_controller_carga import _CargaMixin
from .bluesky_controller_acciones import _AccionesMixin
from .bluesky_controller_social import _SocialMixin
from .bluesky_controller_moderacion import _ModeracionMixin


class BlueskyController(_ModeracionMixin, _CargaMixin, _AccionesMixin, _SocialMixin):
    """Lógica de Bluesky enganchada al MainFrame con cambios mínimos en éste."""

    BUFFER_LABELS = {
        "home": _("Inicio"),
        "notifications": _("Notificaciones"),
        "mentions": _("Menciones"),
        "sent": _("Enviados"),
        "likes": _("Me gusta"),
        "bookmarks": _("Marcadores"),
        "feeds": _("Feeds personalizados"),
        "lists": _("Listas"),
        "starterpacks": _("Starter packs"),
        "trending": _("Tendencias"),
        "followers": _("Seguidores"),
        "follows": _("Siguiendo"),
        "blocks": _("Bloqueados"),
        "mutes": _("Silenciados"),
        "convos": _("Mensajes directos"),
    }
    _TODOS = ["home", "notifications", "mentions", "sent", "likes", "bookmarks",
              "feeds", "lists", "starterpacks", "trending", "followers", "follows",
              "blocks", "mutes", "convos"]

    def __init__(self, frame):
        self.f = frame
