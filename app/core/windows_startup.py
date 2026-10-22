# -*- coding: utf-8 -*-
"""
Arranque automático de SocialRSS con Windows (como hacía TWBlue).

Se hace con el método de USUARIO, no con un servicio de Windows: un servicio corre en la
sesión 0, sin escritorio, y ni la ventana ni NVDA existirían ahí, así que sería inservible
para un cliente accesible. Lo que se usa es la clave de registro

    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

que es la misma que usan la mayoría de programas de usuario: no necesita permisos de
administrador, se aplica solo a esta cuenta de Windows y el usuario puede desactivarla
también desde el Administrador de tareas (pestaña Inicio).

Combinado con "Iniciar minimizado en la bandeja del sistema" (general.start_minimized), el
resultado es el que se pide: SocialRSS arranca con Windows y se queda funcionando por detrás
(streaming, sondeos, sonidos de notificación) sin ventana en primer plano.
"""

import os
import sys
import logging

from app.core.paths import IS_FROZEN

log = logging.getLogger(__name__)

# Nombre del valor dentro de la clave Run. No se traduce: es un identificador del registro.
NOMBRE_ENTRADA = "SocialRSS"
_CLAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _es_windows() -> bool:
    return sys.platform.startswith("win")


def comando_arranque() -> str:
    """Línea de comandos que Windows ejecutará al iniciar sesión.

    Compilado: el propio .exe. Desde código fuente: el intérprete + main.py (se usa pythonw.exe
    si está disponible, para que no aparezca una consola negra al arrancar). Las rutas van entre
    comillas porque la instalación puede estar en una carpeta con espacios.
    """
    if IS_FROZEN:
        return f'"{sys.executable}"'
    interprete = sys.executable
    pythonw = os.path.join(os.path.dirname(interprete), "pythonw.exe")
    if os.path.isfile(pythonw):
        interprete = pythonw
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if not script or not os.path.isfile(script):
        from app.core.paths import ROOT_DIR
        script = str(ROOT_DIR / "main.py")
    return f'"{interprete}" "{script}"'


def esta_activado() -> bool:
    """True si SocialRSS está registrado para arrancar con Windows."""
    if not _es_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN, 0, winreg.KEY_READ) as k:
            valor, _tipo = winreg.QueryValueEx(k, NOMBRE_ENTRADA)
            return bool(valor)
    except FileNotFoundError:
        return False
    except OSError as e:
        log.debug(f"No se pudo leer el arranque con Windows: {e}")
        return False


def activar() -> bool:
    """Registra la app para arrancar con Windows. Devuelve True si quedó registrada.

    Se reescribe siempre el valor (aunque ya existiera) porque la ruta de la instalación puede
    haber cambiado: si el usuario mueve la carpeta, la entrada antigua apuntaría a la nada.
    """
    if not _es_windows():
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _CLAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, NOMBRE_ENTRADA, 0, winreg.REG_SZ, comando_arranque())
        log.info("Arranque automático con Windows activado.")
        return True
    except OSError as e:
        log.error(f"No se pudo activar el arranque con Windows: {e}")
        return False


def desactivar() -> bool:
    """Quita el registro de arranque automático. Devuelve True si ya no está registrada."""
    if not _es_windows():
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, NOMBRE_ENTRADA)
        log.info("Arranque automático con Windows desactivado.")
        return True
    except FileNotFoundError:
        return True          # no estaba: el resultado deseado ya se cumple
    except OSError as e:
        log.error(f"No se pudo desactivar el arranque con Windows: {e}")
        return False


def aplicar(activado: bool) -> bool:
    """Deja el arranque automático como pide `activado`. Devuelve True si se pudo aplicar."""
    return activar() if activado else desactivar()


def sincronizar_ruta():
    """Si el arranque está activado, reescribe el comando con la ruta ACTUAL.

    Se llama al arrancar la aplicación: así, si el usuario ha movido la carpeta de SocialRSS o
    ha pasado de código fuente a versión compilada, la entrada del registro se corrige sola y no
    se queda apuntando a una ruta que ya no existe.
    """
    if not _es_windows() or not esta_activado():
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLAVE_RUN, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            actual, _tipo = winreg.QueryValueEx(k, NOMBRE_ENTRADA)
            nuevo = comando_arranque()
            if actual != nuevo:
                winreg.SetValueEx(k, NOMBRE_ENTRADA, 0, winreg.REG_SZ, nuevo)
                log.info("Arranque con Windows: ruta actualizada.")
    except OSError as e:
        log.debug(f"No se pudo sincronizar la ruta de arranque: {e}")
