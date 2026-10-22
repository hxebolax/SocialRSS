# -*- coding: utf-8 -*-
"""Controlador de la interfaz de PeerTube (fachada) — esqueleto de la Fase 0.

Ensambla, en fases posteriores, los mixins por dominio (carga/catálogos, reproducción, social,
biblioteca, subida/importación, descargas, directos, moderación, administración), igual que el
controlador de Reddit. En la Fase 0 solo existe la fachada vacía para poder cablearla más adelante en
el `MainFrame` sin romper nada.
"""
import logging

from app.ui.peertube_controller_carga import _CargaMixin
from app.ui.peertube_controller_acciones import _AccionesMixin
from app.ui.peertube_controller_repro import _ReproMixin
from app.ui.peertube_controller_social import _SocialUIMixin
from app.ui.peertube_controller_biblioteca import _BibliotecaUIMixin
from app.ui.peertube_controller_directos import _DirectosUIMixin
from app.ui.peertube_controller_notif import _NotifUIMixin
from app.ui.peertube_controller_moderacion import _ModeracionUIMixin
from app.ui.peertube_controller_admin import _AdminUIMixin

log = logging.getLogger(__name__)

# Etiquetas visibles de las secciones fijas del árbol (clave -> texto). El orden lo decide
# config("peertube", "section_order"); las secciones ocultas se omiten. La lógica de construcción
# del árbol y de carga se añade en la Fase 2.
SECTION_LABELS = {
    "recientes": _("Vídeos recientes"),
    "populares": _("Populares"),
    "tendencias": _("Tendencias"),
    "locales": _("Vídeos locales"),
    "federados": _("Vídeos federados"),
    "suscripciones": _("Suscripciones"),
    "biblioteca": _("Mi biblioteca"),
    "historial": _("Historial"),
    "ver_mas_tarde": _("Ver más tarde"),
    "me_gusta": _("Me gusta"),
    "listas": _("Listas de reproducción"),
    "mis_videos": _("Mis vídeos"),
    "mis_canales": _("Mis canales"),
    "directos": _("Directos"),
    "notificaciones": _("Notificaciones"),
    "busquedas": _("Búsquedas guardadas"),
}


class PeerTubeController(_CargaMixin, _AccionesMixin, _ReproMixin, _SocialUIMixin,
                         _BibliotecaUIMixin, _DirectosUIMixin, _NotifUIMixin, _ModeracionUIMixin,
                         _AdminUIMixin):
    """Controlador de PeerTube acoplado al MainFrame (self.f).

    Ensambla los mixins por dominio: `_CargaMixin` (árbol, carga y render), `_AccionesMixin`
    (selección, detalle y menú contextual), `_ReproMixin` (reproducción y subtítulos), `_SocialUIMixin`
    (valoraciones, suscripciones, comentarios, silencios, reportes e historial) y `_BibliotecaUIMixin`
    (subir, importar, descargar, listas, canales y edición). Fases posteriores: directos, moderación
    y administración."""

    SECTION_LABELS = SECTION_LABELS

    def __init__(self, frame):
        self.f = frame
