# -*- coding: utf-8 -*-
"""
Capa de plataforma e interfaz: lo único que el núcleo y los servicios necesitan saber de la
interfaz gráfica que los está usando (wxPython en Windows, GTK 4 en Linux).

El núcleo y los servicios NO deben importar wx ni Gtk. Cuando necesitan algo de la interfaz
(ejecutar una función en el hilo de la UI, mostrar un aviso, copiar al portapapeles) lo piden
aquí, y la interfaz que arranca registra su implementación con `registrar_interfaz()`.
"""

import logging
import sys
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

ES_WINDOWS = sys.platform == "win32"
ES_LINUX = sys.platform.startswith("linux")
ES_MAC = sys.platform == "darwin"


class _Interfaz:
    """Implementación de interfaz registrada. Todo es opcional: lo que falte se ignora."""

    def __init__(self):
        self.nombre = ""
        self.llamar_en_ui: Optional[Callable] = None        # (fn, *args, **kw) -> None
        self.es_hilo_ui: Optional[Callable[[], bool]] = None
        self.mostrar_aviso: Optional[Callable] = None        # (texto, titulo) -> None (en hilo UI)
        self.mostrar_toast: Optional[Callable] = None        # (texto, titulo) -> None (en hilo UI)
        self.confirmar: Optional[Callable] = None            # (texto, titulo) -> bool (en hilo UI)
        self.copiar: Optional[Callable] = None               # (texto) -> bool
        self.hay_interfaz: Optional[Callable[[], bool]] = None
        self.anunciar: Optional[Callable] = None             # (texto, interrumpir) -> bool


_interfaz = _Interfaz()


def registrar_interfaz(nombre: str, **funciones):
    """Registra la interfaz activa. `funciones` son los atributos de `_Interfaz` que ofrezca."""
    _interfaz.nombre = nombre
    for clave, fn in funciones.items():
        if hasattr(_interfaz, clave):
            setattr(_interfaz, clave, fn)
        else:
            log.debug(f"registrar_interfaz: capacidad desconocida '{clave}'")


def nombre_interfaz() -> str:
    """'wx', 'gtk' o '' si todavía no hay ninguna registrada."""
    return _interfaz.nombre


def _wx_activo():
    """El módulo wx SOLO si ya estaba importado y hay una aplicación (nunca lo importa)."""
    wx = sys.modules.get("wx")
    try:
        if wx is not None and wx.GetApp() is not None:
            return wx
    except Exception:
        pass
    return None


def hay_interfaz() -> bool:
    """True si hay una interfaz gráfica lista para recibir llamadas."""
    if _interfaz.hay_interfaz:
        try:
            return bool(_interfaz.hay_interfaz())
        except Exception:
            return False
    return _wx_activo() is not None


def es_hilo_ui() -> bool:
    """True si el hilo actual es el de la interfaz."""
    if _interfaz.es_hilo_ui:
        try:
            return bool(_interfaz.es_hilo_ui())
        except Exception:
            return False
    wx = _wx_activo()
    if wx is not None:
        try:
            return bool(wx.IsMainThread())
        except Exception:
            return False
    return threading.current_thread() is threading.main_thread()


def llamar_en_ui(fn: Callable, *args, **kwargs) -> bool:
    """Ejecuta `fn(*args, **kwargs)` en el hilo de la interfaz (equivale a wx.CallAfter).

    Es seguro llamarla desde cualquier hilo. Devuelve False si no hay interfaz que lo atienda
    (por ejemplo, durante el cierre); entonces la función NO se ejecuta."""
    if _interfaz.llamar_en_ui:
        try:
            _interfaz.llamar_en_ui(fn, *args, **kwargs)
            return True
        except Exception as e:
            log.debug(f"llamar_en_ui falló: {e}")
            return False
    wx = _wx_activo()
    if wx is not None:
        try:
            wx.CallAfter(fn, *args, **kwargs)
            return True
        except Exception as e:
            log.debug(f"wx.CallAfter falló: {e}")
            return False
    return False


def en_ui_o_ahora(fn: Callable, *args, **kwargs):
    """Ejecuta `fn` ya si estamos en el hilo de la UI y, si no, la difiere a ese hilo."""
    if es_hilo_ui():
        fn(*args, **kwargs)
    else:
        llamar_en_ui(fn, *args, **kwargs)


def mostrar_aviso_dialogo(texto: str, titulo: str = None):
    """Muestra un mensaje informativo en un cuadro de diálogo (si la interfaz sabe hacerlo)."""
    if _interfaz.mostrar_aviso:
        llamar_en_ui(_interfaz.mostrar_aviso, texto, titulo)


def mostrar_toast(texto: str, titulo: str = None):
    """Notificación del sistema (centro de notificaciones) si la interfaz la ofrece."""
    if _interfaz.mostrar_toast:
        llamar_en_ui(_interfaz.mostrar_toast, texto, titulo)


def copiar_texto(texto: str) -> bool:
    """Copia texto al portapapeles del sistema. False si no se pudo."""
    if _interfaz.copiar:
        try:
            return bool(_interfaz.copiar(texto))
        except Exception as e:
            log.error(f"Error al copiar al portapapeles: {e}")
            return False
    if ES_WINDOWS:
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(texto, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            log.error(f"Error al copiar al portapapeles: {e}")
    return False


def confirmar_ui(texto: str, titulo: str = "", por_defecto: bool = False, espera: float = 120.0) -> bool:
    """Pregunta Sí/No al usuario y devuelve su respuesta. Segura desde cualquier hilo.
    Sin interfaz (o si no responde a tiempo) devuelve `por_defecto`."""
    if not hay_interfaz():
        return por_defecto
    fn = _interfaz.confirmar or _confirmar_wx
    if es_hilo_ui():
        try:
            return bool(fn(texto, titulo))
        except Exception as e:
            log.debug(f"confirmar_ui falló: {e}")
            return por_defecto
    resultado = {}
    hecho = threading.Event()

    def _en_ui():
        try:
            resultado["ok"] = bool(fn(texto, titulo))
        except Exception as e:
            log.debug(f"confirmar_ui falló: {e}")
        finally:
            hecho.set()
    if not llamar_en_ui(_en_ui):
        return por_defecto
    hecho.wait(timeout=espera)
    return bool(resultado.get("ok", por_defecto))


def _confirmar_wx(texto: str, titulo: str = "") -> bool:
    import wx
    dlg = wx.MessageDialog(None, texto, titulo or "SocialRSS",
                           wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
    try:
        return dlg.ShowModal() == wx.ID_YES
    finally:
        dlg.Destroy()


def anunciar(texto: str, interrumpir: bool = True) -> bool:
    """Pide a la interfaz que anuncie `texto` al lector de pantalla por accesibilidad (AT-SPI).
    False si la interfaz no lo ofrece o no hay ventana donde anunciarlo."""
    if not _interfaz.anunciar:
        return False
    try:
        return bool(_interfaz.anunciar(texto, interrumpir))
    except Exception as e:
        log.debug(f"anunciar falló: {e}")
        return False
