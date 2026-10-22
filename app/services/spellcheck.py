# -*- coding: utf-8 -*-
"""
Servicio de corrección ortográfica (pyspellchecker, sin dependencias nativas).

Detecta palabras desconocidas y propone correcciones. El idioma se toma de la
configuración (`general.spell_language`, por defecto español). Es tolerante a
fallos: si la librería no está disponible, se desactiva sin romper la app.
"""

import re
import logging
from typing import List, Tuple

from app.core.config_manager import config

log = logging.getLogger(__name__)

# Palabra: secuencia de letras unicode (incluye acentos y ñ), sin números.
_RE_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)

_corrector = None
_idioma_actual = None
_disponible = None


def disponible() -> bool:
    """Indica si la corrección ortográfica está disponible."""
    global _disponible
    if _disponible is None:
        try:
            import spellchecker  # noqa: F401
            _disponible = True
        except Exception as e:
            log.info(f"Corrección ortográfica no disponible: {e}")
            _disponible = False
    return _disponible


def _obtener_corrector():
    """Devuelve (y cachea) el corrector para el idioma configurado."""
    global _corrector, _idioma_actual
    idioma = config.get("general", "spell_language", "es")
    if _corrector is not None and _idioma_actual == idioma:
        return _corrector
    if not disponible():
        return None
    try:
        from spellchecker import SpellChecker
        _corrector = SpellChecker(language=idioma)
        _idioma_actual = idioma
    except Exception as e:
        log.error(f"No se pudo inicializar el corrector para '{idioma}': {e}")
        _corrector = None
    return _corrector


def revisar(texto: str) -> List[Tuple[str, List[str]]]:
    """
    Revisa un texto y devuelve una lista de (palabra_mal, [sugerencias]) para
    cada palabra desconocida (sin repetir), en orden de aparición.
    Ignora menciones (@usuario), hashtags (#etiqueta) y URLs.
    """
    sp = _obtener_corrector()
    if not sp or not texto:
        return []

    # Quitar URLs, menciones y hashtags para no marcarlos como errores.
    limpio = re.sub(r"https?://\S+", " ", texto)
    limpio = re.sub(r"[@#]\w+", " ", limpio)

    vistas = set()
    palabras_orden = []
    for m in _RE_PALABRA.finditer(limpio):
        w = m.group(0)
        wl = w.lower()
        if len(wl) < 3 or wl in vistas:
            continue
        vistas.add(wl)
        palabras_orden.append(w)

    resultado = []
    try:
        desconocidas = sp.unknown([p.lower() for p in palabras_orden])
    except Exception as e:
        log.debug(f"Error al revisar ortografía: {e}")
        return []

    for w in palabras_orden:
        if w.lower() in desconocidas:
            try:
                sugerencias = list(sp.candidates(w.lower()) or [])[:6]
            except Exception:
                sugerencias = []
            resultado.append((w, sugerencias))
    return resultado


def reemplazar_palabra(texto: str, palabra: str, nueva: str) -> str:
    """Reemplaza todas las apariciones (palabra completa, sin distinguir mayúsculas)
    de `palabra` por `nueva` en el texto."""
    patron = re.compile(r"(?<!\w)" + re.escape(palabra) + r"(?!\w)", re.IGNORECASE)
    return patron.sub(nueva, texto)
