# -*- coding: utf-8 -*-
"""Fachada de la UI de X (Twitter): re-exporta diálogos y controlador.
"""
from .x_dialogs import AVISO_RIESGOS, XLoginDialog, XComposeDialog
from .x_controller import XController

__all__ = ['AVISO_RIESGOS', 'XLoginDialog', 'XComposeDialog', 'XController']
