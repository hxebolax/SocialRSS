# -*- coding: utf-8 -*-
"""
Módulo para la internacionalización y localización (i18n) de la aplicación.
Usa gettext y expone la función global _() para traducir cadenas.
"""

import gettext
import builtins
import logging
from .config_manager import config
from .paths import get_locales_path

log = logging.getLogger(__name__)

# Nombres legibles de los códigos de idioma (ISO 639-1). Para códigos no listados se usa el propio
# código. El español es el idioma BASE del código fuente (no necesita catálogo .mo).
NOMBRES_IDIOMA = {
    "es": "Español", "en": "Inglés", "fr": "Francés", "de": "Alemán", "pt": "Portugués",
    "it": "Italiano", "ca": "Catalán", "gl": "Gallego", "eu": "Euskera", "nl": "Neerlandés",
    "ru": "Ruso", "pl": "Polaco", "tr": "Turco", "ar": "Árabe", "zh": "Chino", "ja": "Japonés",
    "ko": "Coreano",
}


def nombre_idioma(codigo: str) -> str:
    """Nombre legible de un código de idioma (p. ej. 'es' -> 'Español'). Si no se conoce, el código."""
    return NOMBRES_IDIOMA.get((codigo or "").lower(), codigo)


def idioma_sistema() -> str:
    """Código ISO de 2 letras del idioma del SISTEMA OPERATIVO ('es', 'en', 'de'...), o '' si no se
    puede determinar. En Windows usa el idioma de la interfaz del usuario."""
    import locale
    cod = ""
    try:
        import ctypes
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        cod = locale.windows_locale.get(lcid, "")
    except Exception:
        cod = ""
    if not cod:
        try:
            cod = (locale.getdefaultlocale()[0] or "")
        except Exception:
            cod = ""
    return cod.replace("-", "_").split("_")[0].lower()


def _resolver_idioma_inicial() -> str:
    """Idioma con el que arrancar. En el PRIMER arranque (instalación nueva) detecta el idioma del
    sistema y, si está disponible en la app, lo usa (y lo guarda); si no, español (idioma base).
    En arranques posteriores respeta lo que haya configurado el usuario."""
    try:
        if getattr(config, "instalacion_nueva", False):
            sistema = idioma_sistema()
            disponibles = idiomas_disponibles()
            elegido = sistema if sistema in disponibles else "es"
            config.set("general", "language", elegido)
            try:
                config.save()
            except Exception:
                pass
            log.info(f"Primer arranque: idioma del sistema='{sistema}', se usa '{elegido}'.")
            return elegido
    except Exception as e:
        log.debug(f"No se pudo autodetectar el idioma del sistema: {e}")
    return config.get("general", "language", "es")


def idiomas_disponibles() -> list:
    """Códigos de idioma DISPONIBLES: siempre 'es' (idioma base del código fuente) más los que tengan
    un catálogo compilado en data/locales/<código>/LC_MESSAGES/*.mo. Así el selector de idioma se
    rellena solo con lo que de verdad hay en 'locales'. Devuelve 'es' primero y el resto por nombre."""
    codigos = {"es"}
    try:
        base = get_locales_path()
        for sub in base.iterdir():
            try:
                lc = sub / "LC_MESSAGES"
                if sub.is_dir() and lc.is_dir() and any(f.suffix == ".mo" for f in lc.iterdir()):
                    codigos.add(sub.name)
            except Exception:
                continue
    except Exception:
        pass
    resto = sorted(codigos - {"es"}, key=lambda c: nombre_idioma(c).lower())
    return ["es"] + resto

def inicializar_traducciones():
    """Configura gettext y carga el idioma: el configurado por el usuario o, en el primer arranque,
    el del sistema operativo si está disponible (si no, español)."""
    lang = _resolver_idioma_inicial()
    locale_dir = get_locales_path()
    
    log.info(f"Cargando traducciones para el idioma: {lang} desde {locale_dir}")
    
    try:
        # Intentar inicializar el catálogo de traducciones
        traduccion = gettext.translation(
            domain="socialrss",
            localedir=str(locale_dir),
            languages=[lang],
            fallback=True
        )
        
        # Instalar la función _() de forma global en Python
        traduccion.install()
        
        # Registrar explícitamente en builtins por si algún módulo no lo detecta
        builtins._ = traduccion.gettext
        log.info(f"Idioma '{lang}' cargado con éxito.")
    except Exception as e:
        log.warning(
            f"No se pudieron cargar las traducciones para '{lang}': {e}. "
            "Se usará el español nativo del código fuente."
        )
        # Respaldo a traducción nula (devuelve la cadena original tal cual)
        fallback_trans = gettext.NullTranslations()
        fallback_trans.install()
        builtins._ = fallback_trans.gettext

# Ejecutar inicialmente una traducción nula por si se importa antes de inicializar la app
if not hasattr(builtins, "_"):
    fallback_trans = gettext.NullTranslations()
    fallback_trans.install()
    builtins._ = fallback_trans.gettext
