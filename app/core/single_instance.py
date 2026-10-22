# -*- coding: utf-8 -*-
"""
Ayudas para la instancia única: cuando se lanza una SEGUNDA copia de la aplicación, además
de avisar de que ya está abierta, se trae al FRENTE la ventana de la instancia que ya estaba
en ejecución (útil para llamarla desde un atajo de teclado).

Como esta versión de wxPython no trae el IPC clásico (wx.Server/wx.Client), se usa un fichero
con el HWND (identificador de ventana de Windows) que escribe la primera instancia, y la
segunda lo lee y usa la API de Windows (user32) para restaurar y enfocar esa ventana.
"""

import logging
import time
from .paths import get_data_path

log = logging.getLogger(__name__)

# Edad máxima de una orden de mostrar pendiente: más vieja es basura de un cierre
# brusco y no debe abrir la ventana "de la nada" mucho después.
MAX_EDAD_ORDEN_SEGUNDOS = 60


def _ruta_hwnd():
    return get_data_path("config/.instance_hwnd")


def _ruta_orden_mostrar():
    return get_data_path("config/.instance_mostrar")


def dejar_orden_mostrar() -> bool:
    """La SEGUNDA instancia deja la orden de que la instancia viva muestre su ventana.

    Complementa a :func:`traer_instancia_existente_al_frente`: el forzado por user32 pone la
    ventana en pantalla, pero desde fuera no puede arreglar el estado interno de wx (destruir el
    icono de la bandeja, reactivar el streaming, anunciarlo por voz). La instancia viva revisa
    esta orden con un temporizador y se restaura con su PROPIO código, que sí hace todo eso.
    """
    try:
        ruta = _ruta_orden_mostrar()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(str(time.time()), encoding="utf-8")
        return True
    except Exception as e:
        log.debug(f"No se pudo dejar la orden de mostrar la ventana: {e}")
        return False


def recoger_orden_mostrar() -> bool:
    """La instancia VIVA comprueba (y consume) la orden de mostrar su ventana.

    Devuelve True solo si había una orden reciente. Una orden caducada o ilegible se borra
    igualmente para no acumular basura.
    """
    try:
        ruta = _ruta_orden_mostrar()
        if not ruta.exists():
            return False
        try:
            creado = float((ruta.read_text(encoding="utf-8") or "0").strip() or "0")
        except ValueError:
            creado = 0.0
        ruta.unlink()
        return creado > 0 and (time.time() - creado) <= MAX_EDAD_ORDEN_SEGUNDOS
    except Exception as e:
        log.debug(f"No se pudo recoger la orden de mostrar la ventana: {e}")
        return False


def guardar_hwnd(hwnd) -> None:
    """La PRIMERA instancia guarda el HWND de su ventana principal para que una segunda
    instancia pueda traerla al frente."""
    try:
        ruta = _ruta_hwnd()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(str(int(hwnd)), encoding="utf-8")
    except Exception as e:
        log.debug(f"No se pudo guardar el HWND de la instancia: {e}")


def borrar_hwnd() -> None:
    """Borra el fichero de HWND al cerrar (evita dejar un identificador obsoleto)."""
    try:
        ruta = _ruta_hwnd()
        if ruta.exists():
            ruta.unlink()
    except Exception:
        pass


def forzar_al_frente(hwnd) -> bool:
    """Fuerza una ventana (por su HWND) a PRIMER PLANO en Windows, sorteando el bloqueo de
    'foreground' del sistema (truco de la tecla ALT + topmost momentáneo). Devuelve True solo si la
    ventana queda REALMENTE en primer plano. Se usa tanto al ARRANCAR (que la app no se quede detrás
    de otras ventanas) como cuando una SEGUNDA instancia trae al frente la primera."""
    import sys
    if not sys.platform.startswith("win"):
        return False
    try:
        hwnd = int(hwnd or 0)
        if not hwnd:
            return False
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        # Fijar tipos: en 64 bits los HWND son punteros; sin esto ctypes los trataría como
        # enteros de 32 bits y podrían corromperse.
        u.IsWindow.argtypes = [wintypes.HWND]; u.IsWindow.restype = wintypes.BOOL
        u.IsIconic.argtypes = [wintypes.HWND]; u.IsIconic.restype = wintypes.BOOL
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]; u.ShowWindow.restype = wintypes.BOOL
        u.SetForegroundWindow.argtypes = [wintypes.HWND]; u.SetForegroundWindow.restype = wintypes.BOOL
        u.BringWindowToTop.argtypes = [wintypes.HWND]; u.BringWindowToTop.restype = wintypes.BOOL
        u.GetForegroundWindow.restype = wintypes.HWND
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SetWindowPos.restype = wintypes.BOOL

        if not u.IsWindow(hwnd):
            log.debug("El HWND no es una ventana válida.")
            return False

        SW_SHOW, SW_RESTORE = 5, 9
        u.ShowWindow(hwnd, SW_SHOW)
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)

        # Truco de la tecla ALT: Windows solo permite cambiar el primer plano a procesos que
        # "han recibido una pulsación reciente"; simular ALT desbloquea SetForegroundWindow
        # (si no, Windows solo parpadea la barra de tareas y la ventana no se enfoca).
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        try:
            u.keybd_event(VK_MENU, 0, 0, 0)
            u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)

        # Refuerzo: poner la ventana arriba del todo un instante y quitar el "siempre encima".
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)

        en_primer_plano = bool(u.GetForegroundWindow() == hwnd)
        log.debug(f"Forzar ventana al frente: hwnd={hwnd}, en_primer_plano={en_primer_plano}")
        return en_primer_plano
    except Exception as e:
        log.debug(f"No se pudo forzar la ventana al frente: {e}")
        return False


def traer_instancia_existente_al_frente() -> bool:
    """La SEGUNDA instancia trae al frente la ventana de la primera. Devuelve True solo si la
    ventana queda REALMENTE en primer plano. Solo aplica en Windows."""
    import sys
    if not sys.platform.startswith("win"):
        return False
    try:
        ruta = _ruta_hwnd()
        if not ruta.exists():
            log.debug("No hay fichero de HWND de la instancia.")
            return False
        hwnd = int((ruta.read_text(encoding="utf-8") or "0").strip() or "0")
        if not hwnd:
            return False
        return forzar_al_frente(hwnd)
    except Exception as e:
        log.debug(f"No se pudo traer la instancia existente al frente: {e}")
        return False
