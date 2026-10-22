# -*- coding: utf-8 -*-
"""
Módulo para la gestión y resolución de rutas de la aplicación.
Soporta ejecución desde código fuente y empaquetado (cx_Freeze).
Crea la estructura de carpetas necesaria en el primer inicio.
"""

import sys
import os
from pathlib import Path
from typing import Optional

# Determinar si estamos ejecutando desde el ejecutable compilado
IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    # Si está congelado, el ejecutable está en el directorio raíz del build
    ROOT_DIR = Path(sys.executable).parent.resolve()
else:
    # Si corre desde fuente, el directorio raíz es el padre de 'app'
    ROOT_DIR = Path(__file__).parent.parent.parent.resolve()

# Directorio de datos de usuario (lectura y escritura)
# El ejecutable compilado buscará 'data' junto al .exe.
# SOCIALRSS_DATA_DIR permite redirigir 'data' a otra carpeta: lo usan las PRUEBAS para trabajar
# sobre un data/ temporal y NO tocar la configuración real del usuario (settings.json, secrets...).
_DATA_OVERRIDE = os.environ.get("SOCIALRSS_DATA_DIR", "").strip()
if _DATA_OVERRIDE:
    DATA_DIR = Path(_DATA_OVERRIDE).expanduser().resolve()
else:
    DATA_DIR = ROOT_DIR / "data"

# Los recursos (sonidos por defecto, documentación/manual) viven dentro de 'data'
# en sus subcarpetas correspondientes (data/sounds, data/docs). Antes había una
# carpeta 'resources' aparte; se consolidó todo en 'data' para tener una única
# estructura junto al ejecutable.
RESOURCES_DIR = DATA_DIR

# Las traducciones (gettext) viven en 'data/locales', con la estructura estándar
# data/locales/<idioma>/LC_MESSAGES/socialrss.mo.
LOCALES_DIR = DATA_DIR / "locales"

def get_resource_path(relative_path: str) -> Path:
    """Devuelve la ruta absoluta a un recurso (dentro de data: sounds/docs)."""
    return RESOURCES_DIR / relative_path

def get_locales_path(relative_path: str = "") -> Path:
    """Devuelve la ruta a la carpeta de traducciones 'locales' (o un subelemento)."""
    return LOCALES_DIR / relative_path if relative_path else LOCALES_DIR

def get_data_path(relative_path: str) -> Path:
    """Devuelve la ruta absoluta a un archivo o carpeta en data."""
    return DATA_DIR / relative_path

def get_bin_path() -> Path:
    """Devuelve la carpeta data/bin, donde residen binarios como ffmpeg."""
    return DATA_DIR / "bin"

def asegurar_bin_en_path():
    """
    Añade data/bin al PATH del proceso para que ffmpeg (usado por PyTgCalls y
    el procesamiento multimedia) se encuentre tanto en código fuente como en el
    ejecutable compilado, sin depender de instalaciones del sistema.
    """
    bin_dir = get_bin_path()
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
        ruta = str(bin_dir)
        actual = os.environ.get("PATH", "")
        if ruta not in actual.split(os.pathsep):
            os.environ["PATH"] = ruta + os.pathsep + actual
    except Exception:
        pass

def get_ffmpeg_path() -> Optional[str]:
    """Devuelve la ruta a ffmpeg.exe en data/bin si existe, o None."""
    exe = get_bin_path() / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    return str(exe) if exe.exists() else None

def limpiar_temp():
    """Vacía la carpeta data/temp (archivos sueltos y subcarpetas).

    Se llama al cerrar la aplicación para que los temporales (imágenes
    descargadas para describir/OCR, conversiones, etc.) no se acumulen. Los
    archivos en uso que no se puedan borrar se ignoran sin interrumpir el cierre.
    """
    import shutil
    temp_dir = get_data_path("temp")
    if not temp_dir.exists():
        return
    for elemento in temp_dir.iterdir():
        try:
            if elemento.is_dir():
                shutil.rmtree(elemento, ignore_errors=True)
            else:
                elemento.unlink()
        except Exception:
            pass

def inicializar_directorios():
    """Crea la estructura mínima de carpetas necesarias si no existe.

    Solo se crean las carpetas que la aplicación usa realmente, para no llenar
    'data' de carpetas vacías. Las que se usan bajo demanda (p. ej. exports al
    exportar un chat) las crea su propio código justo antes de escribir.
    """
    carpetas_data = [
        "config",      # settings.json + secrets.enc
        "logs",        # app.log / app.jsonl
        "database",    # social_rss.db
        "sounds",      # paquetes de sonido (default se autogenera aquí)
        "docs",        # manual y documentación
        "bin",         # binarios (ffmpeg)
        "temp",        # archivos temporales
        "sessions",    # sesiones de Telegram
        "downloads",   # descargas, organizadas por red: telegram/<chat>/<tipo>, matrix/<chat>...
        "backups",     # copias de seguridad
    ]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for c in carpetas_data:
        get_data_path(c).mkdir(parents=True, exist_ok=True)
