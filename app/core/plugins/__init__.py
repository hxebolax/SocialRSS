# -*- coding: utf-8 -*-
"""Sistema de complementos (plugins) de SocialRSS.

Permite extender la app con módulos cargados desde `data/plugins/` sin tocar el núcleo. Diseño
propio, alineado con la arquitectura de la app (config, db, event_bus, speak/notificar).
"""
from .api import PluginBase, PluginInfo, PluginHook, PluginAPI
from .manager import PluginManager, plugin_manager

__all__ = ["PluginBase", "PluginInfo", "PluginHook", "PluginAPI", "PluginManager", "plugin_manager"]
