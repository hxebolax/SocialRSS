# -*- coding: utf-8 -*-
"""Motores de reproducción. `crear_backend()` elige el del sistema: BASS (basskit) en Windows y
GStreamer en Linux; los módulos de cada motor solo se importan cuando se usan."""

from app.core.plataforma import ES_WINDOWS
from .base_player_backend import BasePlayerBackend, BANDAS_EQ, VELOCIDAD_MIN, VELOCIDAD_MAX


def crear_backend() -> BasePlayerBackend:
    if ES_WINDOWS:
        from .basskit_player_backend import BasskitPlayerBackend
        return BasskitPlayerBackend()
    from .gstreamer_player_backend import GStreamerPlayerBackend
    return GStreamerPlayerBackend()
