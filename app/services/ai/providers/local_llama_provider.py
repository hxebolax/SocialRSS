# -*- coding: utf-8 -*-
"""
Proveedor de Inteligencia Artificial para modelos locales (llama.cpp, Ollama, LM Studio).
Utiliza peticiones HTTP directas compatibles con la API de OpenAI.
"""

import base64
import logging
import mimetypes
from pathlib import Path
import requests
from .base_provider import BaseAIProvider
from app.core.config_manager import config
from app.core.errors import AIError

log = logging.getLogger(__name__)

class LocalLlamaProvider(BaseAIProvider):
    """Implementación de proveedor local compatible con OpenAI API."""
    
    def _get_url(self) -> str:
        url = config.get("ai", "local_llama_url", "http://localhost:8080/v1")
        if not url.endswith("/chat/completions"):
            url = f"{url.rstrip('/')}/chat/completions"
        return url

    def probar_conexion(self) -> bool:
        url = self._get_url()
        # Intentar una llamada rápida de prueba con timeout corto
        payload = {
            "model": "local",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5
        }
        try:
            res = requests.post(url, json=payload, timeout=3.0)
            return res.status_code == 200
        except Exception as e:
            log.debug(f"Local Llama no responde en {url}: {e}")
            return False

    def describir_imagen(self, ruta_imagen: str, prompt: str) -> str:
        url = self._get_url()
        path = Path(ruta_imagen)
        if not path.exists():
            raise FileNotFoundError(f"Imagen no encontrada: {ruta_imagen}")
            
        mime_type, _ = mimetypes.guess_type(ruta_imagen)
        if not mime_type:
            mime_type = "image/jpeg"
            
        try:
            # Leer imagen y codificar en base64
            with open(path, "rb") as f:
                encoded_image = base64.b64encode(f.read()).decode("utf-8")
                
            payload = {
                "model": "local",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{encoded_image}"
                                }
                            }
                        ]
                    }
                ]
            }
            
            res = requests.post(url, json=payload, timeout=60.0)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            else:
                raise AIError(f"Código de error HTTP local: {res.status_code} - {res.text}")
        except Exception as e:
            log.error(f"Error al describir imagen localmente: {e}")
            raise AIError(f"Error en modelo local al describir imagen: {e}")

    def generar_texto_alternativo(self, ruta_imagen: str, prompt: str) -> str:
        return self.describir_imagen(ruta_imagen, prompt)

    def _completar_texto(self, prompt: str, texto: str) -> str:
        url = self._get_url()
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": "Eres un asistente útil y preciso en español."},
                {"role": "user", "content": f"{prompt}\n\nTexto:\n{texto}"}
            ]
        }
        try:
            res = requests.post(url, json=payload, timeout=45.0)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            else:
                raise AIError(f"Código de error HTTP local: {res.status_code}")
        except Exception as e:
            log.error(f"Error al completar texto localmente: {e}")
            raise AIError(f"Error en modelo local: {e}")

    def resumir_texto(self, texto: str, prompt: str) -> str:
        return self._completar_texto(prompt, texto)

    def sugerir_hashtags(self, texto: str, prompt: str) -> str:
        return self._completar_texto(prompt, texto)

    def mejorar_redaccion(self, texto: str) -> str:
        prompt = (
            "Corrige la ortografía y mejora el estilo del siguiente texto en español. "
            "Devuelve ÚNICAMENTE el texto corregido final, sin explicaciones ni comillas."
        )
        return self._completar_texto(prompt, texto)
