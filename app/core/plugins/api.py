# -*- coding: utf-8 -*-
"""API pública del sistema de complementos.

Un complemento es una clase que hereda de `PluginBase`, declara sus datos con `get_info()` y, al
activarse (`on_enable`), se suscribe a los puntos de extensión (hooks) y/o añade entradas de menú a
través del objeto `PluginAPI` que recibe. Todo en el estilo de la app (sin dependencias externas).
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict


class PluginHook(Enum):
    """Puntos de extensión a los que un complemento puede engancharse."""
    APP_STARTED = "app_started"        # La app está lista. cb()
    APP_CLOSING = "app_closing"        # La app se cierra. cb()
    FEED_UPDATED = "feed_updated"      # Un feed terminó de actualizarse. cb(feed_id, nuevos)
    ARTICLE_NEW = "article_new"        # Llega un artículo NUEVO de RSS (interceptable). cb(article)->dict|None
    ARTICLE_OPENED = "article_opened"  # El usuario abre un artículo. cb(article)


@dataclass
class PluginInfo:
    """Metadatos de un complemento."""
    id: str
    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    website: str = ""
    has_settings: bool = False


class PluginAPI:
    """Objeto que recibe cada complemento para interactuar con la app de forma controlada.

    Lo crea el PluginManager por cada complemento (sabe su id, para aislar config/datos)."""

    def __init__(self, manager, plugin_id: str):
        self._manager = manager
        self._id = plugin_id

    # --- Hooks y menús ---
    def register_hook(self, hook: PluginHook, callback: Callable):
        """Suscribe un callback a un punto de extensión."""
        self._manager.register_hook(self._id, hook, callback)

    def add_menu_item(self, label: str, callback: Callable):
        """Añade una entrada al menú 'Complementos' de la app."""
        self._manager.add_menu_item(self._id, label, callback)

    # --- Voz y avisos (estilo de la app) ---
    def speak(self, texto: str):
        from app.core.speech import speak
        speak(texto, interrupt=True)

    def notify(self, texto: str):
        from app.core.speech import notificar
        notificar(texto)

    # --- Configuración y datos propios del complemento ---
    def get_config(self) -> Dict[str, Any]:
        """Configuración persistente del complemento (un dict JSON)."""
        from app.core.config_manager import config
        return dict(config.get("plugins", f"cfg_{self._id}", {}) or {})

    def save_config(self, datos: Dict[str, Any]):
        from app.core.config_manager import config
        config.set("plugins", f"cfg_{self._id}", dict(datos or {}))

    def get_data_path(self, sub: str = "") -> str:
        """Carpeta de datos privada del complemento (data/plugins_data/<id>/...)."""
        from app.core.paths import get_data_path
        p = get_data_path(f"plugins_data/{self._id}")
        p.mkdir(parents=True, exist_ok=True)
        return str(p / sub) if sub else str(p)

    # --- Acceso de SOLO LECTURA a la base de datos ---
    def db_query(self, sql: str, params: tuple = ()) -> list:
        from app.core.database import db
        return db.query(sql, params)

    # --- Ventana principal (para diálogos) ---
    def get_main_window(self):
        return self._manager.main_window


class PluginBase:
    """Clase base de la que hereda todo complemento."""

    def __init__(self, api: PluginAPI):
        self.api = api

    @staticmethod
    def get_info() -> PluginInfo:
        raise NotImplementedError("El complemento debe implementar get_info().")

    def on_enable(self) -> bool:
        """Se llama al activar el complemento. Aquí se registran hooks y menús. Devuelve True si OK."""
        return True

    def on_disable(self):
        """Se llama al desactivar el complemento (limpieza). Opcional."""
        pass

    def get_settings_panel(self, parent):
        """Devuelve un wx.Panel de configuración (o None). Solo si get_info().has_settings."""
        return None
