# -*- coding: utf-8 -*-
"""Proveedores de traducción GRATUITOS (sin clave API) basados en los endpoints web de Google.

Reimplementados en el estilo de la app a partir de los módulos verificados de TranslateAdvanced
(GPL, de Héctor J. Benítez / xebolax). Dos variantes distintas y probadas que funcionan sin clave:
- GoogleWeb (método web 1): translate.googleapis.com / mirrors, client=gtx.
- GoogleWebAlt (método web 2): translate.google.com, client=at (cliente Android).
"""
import logging
import urllib.parse
import random
import requests
from app.core.net import http_session
from .base_translation_provider import BaseTranslationProvider
from app.core.errors import TranslationError

log = logging.getLogger(__name__)

_MIRRORS = [
    "https://translate.googleapis.com/translate_a/single",
    "https://translate.google.com/translate_a/single",
    "https://translate.google.es/translate_a/single",
    "https://translate.google.co.uk/translate_a/single",
    "https://translate.google.de/translate_a/single",
    "https://translate.google.fr/translate_a/single",
]
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


class _GoogleSingleBase(BaseTranslationProvider):
    """Base para los endpoints translate_a/single (devuelven listas con fragmentos)."""

    _client = "gtx"
    _user_agent = None  # None = rotar entre varios
    _mirror_fijo = None  # None = rotar entre mirrors

    def _peticion(self, sl: str, tl: str, texto: str) -> list:
        base = self._mirror_fijo or random.choice(_MIRRORS)
        ua = self._user_agent or random.choice(_USER_AGENTS)
        url = (f"{base}?client={self._client}&sl={sl}&tl={tl}&dt=t"
               f"&q={urllib.parse.quote(texto)}")
        res = http_session().get(url, headers={"User-Agent": ua}, timeout=15.0)
        if res.status_code != 200:
            raise TranslationError(f"HTTP {res.status_code} del traductor de Google.")
        return res.json()

    def traducir(self, texto: str, idioma_origen: str, idioma_destino: str) -> str:
        if not texto or not texto.strip():
            return ""
        sl = idioma_origen or "auto"
        tl = idioma_destino or "es"
        try:
            datos = self._peticion(sl, tl, texto)
            if datos and datos[0]:
                return "".join(p[0] for p in datos[0] if p and p[0])
            raise TranslationError("Respuesta vacía del traductor.")
        except TranslationError:
            raise
        except Exception as e:
            log.error(f"Error al traducir (Google web): {e}")
            raise TranslationError(f"Fallo en la traducción: {e}")

    def detectar_idioma(self, texto: str) -> str:
        if not texto or not texto.strip():
            return "auto"
        try:
            datos = self._peticion("auto", "en", texto[:200])
            if isinstance(datos, list) and len(datos) > 2 and isinstance(datos[2], str):
                return datos[2]
        except Exception as e:
            log.debug(f"No se pudo detectar idioma (Google web): {e}")
        return "auto"


class GoogleWebTranslationProvider(_GoogleSingleBase):
    """Google Translate web (método 1): endpoint single con client=gtx y rotación de mirrors/UA."""
    _client = "gtx"


class GoogleWebAltTranslationProvider(_GoogleSingleBase):
    """Google Translate web (método 2): cliente Android (client=at)."""
    _client = "at"
    _mirror_fijo = "https://translate.google.com/translate_a/single"
    _user_agent = "AndroidTranslate/5.3.0.RC02.130803278-53000263 5.1 phone TRANSLATE_OPM5_TEST_1"
