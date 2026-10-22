# -*- coding: utf-8 -*-
"""Gestor de complementos: descubre, carga, activa/desactiva y despacha hooks.

Descubre complementos en `data/plugins/`, admitiendo:
- archivo único: `data/plugins/mi_plugin.py`
- paquete: `data/plugins/mi_plugin/__init__.py`

Cada módulo debe definir UNA subclase de `PluginBase`. El estado activado/desactivado se guarda en
`config` (sección "plugins", clave "enabled"). Es robusto: el fallo de un complemento se aísla y se
registra, nunca tumba la app.
"""
import importlib.util
import logging
import os

from .api import PluginBase, PluginHook, PluginAPI

log = logging.getLogger(__name__)


class _PluginCargado:
    """Estado de un complemento ya descubierto."""
    def __init__(self, info, clase, ruta):
        self.info = info
        self.clase = clase
        self.ruta = ruta
        self.instancia = None
        self.activo = False
        self.error = ""


class PluginManager:
    """Administra el ciclo de vida de los complementos y el despacho de hooks."""

    def __init__(self):
        self.main_window = None
        self._plugins = {}                  # id -> _PluginCargado
        self._hooks = {h: [] for h in PluginHook}   # hook -> [(plugin_id, callback)]
        self._menu_items = []               # [(plugin_id, label, callback)]
        self._descubierto = False
        self._excesos = {}                  # plugin_id -> nº de hooks por encima del presupuesto

    # ------------------------------------------------------------------
    # DESCUBRIMIENTO Y CARGA
    # ------------------------------------------------------------------
    def _carpeta_plugins(self):
        from app.core.paths import get_data_path
        p = get_data_path("plugins")
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def descubrir(self):
        """Recorre data/plugins/ y carga los módulos (sin activarlos todavía)."""
        if self._descubierto:
            return
        self._descubierto = True
        carpeta = self._carpeta_plugins()
        for nombre in sorted(os.listdir(carpeta)):
            ruta = os.path.join(carpeta, nombre)
            modulo_path = None
            if nombre.startswith("_") or nombre.startswith("."):
                continue
            if os.path.isfile(ruta) and nombre.endswith(".py"):
                modulo_path = ruta
            elif os.path.isdir(ruta):
                cand = os.path.join(ruta, "__init__.py")
                if os.path.isfile(cand):
                    modulo_path = cand
            if not modulo_path:
                continue
            try:
                self._cargar_modulo(nombre.replace(".py", ""), modulo_path)
            except Exception as e:
                log.error(f"No se pudo cargar el complemento '{nombre}': {e}")

    def _cargar_modulo(self, nombre, modulo_path):
        spec = importlib.util.spec_from_file_location(f"socialrss_plugin_{nombre}", modulo_path)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        # Buscar la subclase de PluginBase definida en el módulo.
        clase = None
        for attr in vars(modulo).values():
            if isinstance(attr, type) and issubclass(attr, PluginBase) and attr is not PluginBase:
                clase = attr
                break
        if clase is None:
            log.debug(f"El complemento '{nombre}' no define una clase PluginBase; se ignora.")
            return
        info = clase.get_info()
        self._plugins[info.id] = _PluginCargado(info, clase, modulo_path)
        log.info(f"Complemento descubierto: {info.name} ({info.id}) v{info.version}")

    # ------------------------------------------------------------------
    # ACTIVAR / DESACTIVAR
    # ------------------------------------------------------------------
    def _habilitados_guardados(self) -> set:
        from app.core.config_manager import config
        return set(config.get("plugins", "enabled", []) or [])

    def _guardar_habilitados(self, conjunto):
        from app.core.config_manager import config
        config.set("plugins", "enabled", sorted(conjunto))

    def activar_guardados(self):
        """Activa los complementos que el usuario tenía habilitados (al arrancar)."""
        for pid in self._habilitados_guardados():
            if pid in self._plugins:
                self.activar(pid, persistir=False)

    def activar(self, plugin_id: str, persistir: bool = True) -> bool:
        p = self._plugins.get(plugin_id)
        if not p or p.activo:
            return bool(p and p.activo)
        try:
            api = PluginAPI(self, plugin_id)
            p.instancia = p.clase(api)
            if p.instancia.on_enable() is False:
                raise RuntimeError("on_enable devolvió False")
            p.activo = True
            p.error = ""
            if persistir:
                hs = self._habilitados_guardados(); hs.add(plugin_id); self._guardar_habilitados(hs)
            log.info(f"Complemento activado: {plugin_id}")
            return True
        except Exception as e:
            p.error = str(e)
            p.activo = False
            self._quitar_registros(plugin_id)
            log.error(f"Error al activar el complemento '{plugin_id}': {e}")
            return False

    def desactivar(self, plugin_id: str, persistir: bool = True):
        p = self._plugins.get(plugin_id)
        if not p or not p.activo:
            return
        try:
            if p.instancia:
                p.instancia.on_disable()
        except Exception as e:
            log.debug(f"on_disable de '{plugin_id}' lanzó: {e}")
        self._quitar_registros(plugin_id)
        p.activo = False
        p.instancia = None
        if persistir:
            hs = self._habilitados_guardados(); hs.discard(plugin_id); self._guardar_habilitados(hs)
        log.info(f"Complemento desactivado: {plugin_id}")

    def _quitar_registros(self, plugin_id):
        for h in self._hooks:
            self._hooks[h] = [(pid, cb) for pid, cb in self._hooks[h] if pid != plugin_id]
        self._menu_items = [m for m in self._menu_items if m[0] != plugin_id]

    # ------------------------------------------------------------------
    # API que usan los complementos (vía PluginAPI)
    # ------------------------------------------------------------------
    def register_hook(self, plugin_id, hook: PluginHook, callback):
        self._hooks.setdefault(hook, []).append((plugin_id, callback))

    def add_menu_item(self, plugin_id, label, callback):
        self._menu_items.append((plugin_id, label, callback))

    # ------------------------------------------------------------------
    # DESPACHO DE HOOKS
    # ------------------------------------------------------------------

    # Presupuesto de ejecución por hook: un complemento lento bloquea al llamador
    # (a veces el hilo de la interfaz). Superarlo repetidas veces desactiva el
    # complemento para esta sesión, con aviso en el log (no impide el arranque).
    _PRESUPUESTO_HOOK_S = 0.5
    _MAX_EXCESOS = 5

    def _cronometrar(self, pid, hook_name, cb, kwargs):
        """Ejecuta el callback midiendo su coste. Devuelve (resultado, error)."""
        import time
        inicio = time.perf_counter()
        try:
            return cb(**kwargs), None
        except Exception as e:
            return None, e
        finally:
            coste = time.perf_counter() - inicio
            if coste > self._PRESUPUESTO_HOOK_S:
                self._excesos[pid] = self._excesos.get(pid, 0) + 1
                log.warning(f"El complemento '{pid}' tardó {coste * 1000:.0f} ms en el hook "
                            f"{hook_name} (presupuesto {self._PRESUPUESTO_HOOK_S * 1000:.0f} ms; "
                            f"exceso {self._excesos[pid]}/{self._MAX_EXCESOS}).")
                if self._excesos[pid] >= self._MAX_EXCESOS:
                    log.error(f"El complemento '{pid}' superó el presupuesto {self._MAX_EXCESOS} "
                              f"veces: se desactiva durante esta sesión (no se toca tu "
                              f"configuración; volverá a cargarse al reiniciar).")
                    try:
                        self.desactivar(pid, persistir=False)
                    except Exception:
                        pass

    def dispatch(self, hook: PluginHook, **kwargs):
        """Notifica un hook a todos los complementos suscritos (sin esperar retorno).
        Cada callback se cronometra; el que exceda su presupuesto repetidamente se
        desactiva para la sesión (ver _cronometrar)."""
        for pid, cb in list(self._hooks.get(hook, [])):
            _res, err = self._cronometrar(pid, hook.name, cb, kwargs)
            if err is not None:
                log.error(f"Hook {hook.name} del complemento '{pid}' falló: {err}")

    def dispatch_articulo(self, article: dict) -> dict:
        """Despacho INTERCEPTABLE para ARTICLE_NEW: cada complemento puede devolver un dict de
        cambios ({hide, mark_read, star, read_later, title}) que se acumulan. Devuelve los cambios
        combinados (vacío si nadie interviene)."""
        cambios = {}
        for pid, cb in list(self._hooks.get(PluginHook.ARTICLE_NEW, [])):
            res, err = self._cronometrar(pid, "ARTICLE_NEW", cb, {"article": article})
            if err is not None:
                log.error(f"Hook ARTICLE_NEW del complemento '{pid}' falló: {err}")
            elif isinstance(res, dict):
                cambios.update(res)
        return cambios

    # ------------------------------------------------------------------
    # CONSULTA (para el diálogo de gestión y el menú)
    # ------------------------------------------------------------------
    def listar(self) -> list:
        """Devuelve la lista de complementos descubiertos con su estado."""
        return list(self._plugins.values())

    def obtener(self, plugin_id):
        return self._plugins.get(plugin_id)

    def menu_items(self):
        return list(self._menu_items)

    def hay_hooks(self, hook: PluginHook) -> bool:
        return bool(self._hooks.get(hook))


# Instancia única
plugin_manager = PluginManager()
