# -*- coding: utf-8 -*-
"""
Proveedor de traducción gratuito utilizando la API de Google Translate Web (dict-chrome-ex).
"""

import logging
import urllib.parse
import requests
from app.core.net import http_session
from .base_translation_provider import BaseTranslationProvider
from app.core.errors import TranslationError

log = logging.getLogger(__name__)

class GoogleTranslationProvider(BaseTranslationProvider):
    """Implementación de traducción gratuita con Google Translate API."""
    
    def traducir(self, texto: str, idioma_origen: str, idioma_destino: str) -> str:
        if not texto or not texto.strip():
            return ""
            
        # Google Translate espera 'auto' si no se conoce el origen
        sl = idioma_origen if idioma_origen else "auto"
        tl = idioma_destino if idioma_destino else "es"
        
        try:
            texto_codificado = urllib.parse.quote(texto)
            url = (
                f"https://clients5.google.com/translate_a/t"
                f"?client=dict-chrome-ex&sl={sl}&tl={tl}&q={texto_codificado}"
            )
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            res = http_session().get(url, headers=headers, timeout=10.0)
            if res.status_code != 200:
                raise TranslationError(f"HTTP error {res.status_code} de Google Translate.")
                
            datos = res.json()
            
            # Procesar el formato JSON retornado por clients5
            if isinstance(datos, list):
                if len(datos) > 0:
                    if isinstance(datos[0], list):
                        # Juntar fragmentos si Google dividió el texto
                        resultado = ''.join(parte[0] for parte in datos if isinstance(parte, list) and parte)
                        return resultado
                    elif isinstance(datos[0], str):
                        return datos[0]
            elif isinstance(datos, dict) and "sentences" in datos:
                resultado = ''.join(s.get("trans", "") for s in datos["sentences"])
                return resultado
                
            raise TranslationError("Formato de respuesta desconocido de Google Translate.")
            
        except Exception as e:
            log.error(f"Error al traducir texto con Google: {e}")
            raise TranslationError(f"Fallo en la traducción de Google: {e}")

    def detectar_idioma(self, texto: str) -> str:
        if not texto or not texto.strip():
            return "en"
            
        try:
            # Usar la API single con client=gtx para obtener la detección
            texto_codificado = urllib.parse.quote(texto[:200]) # Solo los primeros 200 caracteres para rapidez
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={texto_codificado}"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            }
            
            res = http_session().get(url, headers=headers, timeout=5.0)
            if res.status_code == 200:
                datos = res.json()
                # El idioma detectado suele estar al final de la respuesta de single (índice 2 o similar)
                # Ejemplo de retorno: [[["translation","original",null,null,1]],null,"es"]
                if len(datos) > 2 and isinstance(datos[2], str):
                    return datos[2]
            return "auto"
        except Exception as e:
            log.debug(f"Error al detectar idioma con Google: {e}")
            return "auto"
