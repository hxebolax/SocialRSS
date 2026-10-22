# -*- coding: utf-8 -*-
"""
Proveedor de Inteligencia Artificial para Google Gemini.
Utiliza la librería oficial google-genai.
"""

import logging
import importlib.util
from pathlib import Path
import mimetypes
from .base_provider import BaseAIProvider
from app.core.config_manager import config
from app.core.errors import AIError

# Carga DIFERIDA de google-genai: importar el SDK al arrancar costaba ~0,6 s y solo hace falta si
# se usa Gemini. Aquí solo comprobamos (barato) si está instalado; el módulo se importa de verdad
# la primera vez que se necesita (ver _asegurar_sdk).
HAS_GEMINI = importlib.util.find_spec("google.genai") is not None
genai = None
types = None


def _asegurar_sdk():
    """Importa google-genai la primera vez que se usa (carga diferida)."""
    global genai, types
    if genai is None:
        from google import genai as _genai
        from google.genai import types as _types
        genai = _genai
        types = _types

log = logging.getLogger(__name__)

class GeminiProvider(BaseAIProvider):
    """Implementación del proveedor Google Gemini."""
    
    def __init__(self):
        self._client = None
        self._key_idx = 0
        self._key_actual = None

    def _get_keys(self) -> list:
        """Devuelve la lista de claves API de Gemini (varias permiten rotación circular)."""
        claves = config.get("ai", "gemini_api_keys", [])
        if isinstance(claves, str):
            claves = [k.strip() for k in claves.replace(",", "\n").splitlines() if k.strip()]
        claves = [k for k in (claves or []) if k]
        # Compatibilidad con la clave única antigua
        unica = config.get("ai", "gemini_api_key", "")
        if unica and unica not in claves:
            claves.insert(0, unica)
        return claves

    def _get_client(self):
        """Devuelve el cliente de Gemini con la clave activa actual."""
        if not HAS_GEMINI:
            raise AIError("La librería google-genai no está instalada.")
        _asegurar_sdk()
        claves = self._get_keys()
        if not claves:
            raise AIError("No se ha configurado ninguna clave API de Gemini en Preferencias.")
        if self._key_idx >= len(claves):
            self._key_idx = 0
        api_key = claves[self._key_idx]
        if not self._client or self._key_actual != api_key:
            try:
                self._client = genai.Client(api_key=api_key)
                self._key_actual = api_key
            except Exception as e:
                log.error(f"Error al inicializar el cliente Gemini: {e}")
                raise AIError(f"Error de inicialización de Gemini: {e}")
        return self._client

    def _rotar_clave(self) -> bool:
        """Pasa a la siguiente clave API (rotación circular). Devuelve True si hay otra."""
        claves = self._get_keys()
        if len(claves) <= 1:
            return False
        self._key_idx = (self._key_idx + 1) % len(claves)
        self._client = None
        log.info(f"Rotando a la clave API de Gemini número {self._key_idx + 1} de {len(claves)}.")
        return True

    @staticmethod
    def _es_error_de_cuota(e: Exception) -> bool:
        texto = str(e).lower()
        return ("quota" in texto or "rate" in texto or "429" in texto or
                "resource_exhausted" in texto or "exhausted" in texto)

    def _generar(self, contents, model: str = None) -> str:
        """Ejecuta generate_content rotando de clave si una agota su cuota."""
        if model is None:
            model = config.get("ai", "model", "gemini-2.5-flash")
        intentos = max(1, len(self._get_keys()))
        ultimo_error = None
        for _ in range(intentos):
            try:
                client = self._get_client()
                response = client.models.generate_content(model=model, contents=contents)
                return response.text or "Sin respuesta del modelo."
            except Exception as e:
                ultimo_error = e
                if self._es_error_de_cuota(e) and self._rotar_clave():
                    continue
                raise AIError(f"Error en Gemini: {e}")
        raise AIError(f"Todas las claves API de Gemini agotaron su cuota. Último error: {ultimo_error}")

    def listar_modelos(self) -> list:
        """Devuelve la lista de modelos de Gemini que soportan generación de contenido."""
        client = self._get_client()
        modelos = []
        try:
            for m in client.models.list():
                nombre = getattr(m, "name", "") or ""
                acciones = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or []
                if (not acciones) or any("generateContent" in str(a) for a in acciones):
                    modelos.append(nombre.replace("models/", ""))
        except Exception as e:
            log.error(f"Error al listar modelos de Gemini: {e}")
            raise AIError(f"No se pudieron obtener los modelos: {e}")
        return sorted(set(modelos))

    def describir_imagen(self, ruta_imagen: str, prompt: str) -> str:
        if not HAS_GEMINI:
            raise AIError("La librería google-genai no está instalada.")
        _asegurar_sdk()
        path = Path(ruta_imagen)
        if not path.exists():
            raise FileNotFoundError(f"Imagen no encontrada: {ruta_imagen}")
        mime_type, _ = mimetypes.guess_type(ruta_imagen)
        if not mime_type:
            mime_type = "image/jpeg"
        with open(path, "rb") as f:
            img_bytes = f.read()
        return self._generar([types.Part.from_bytes(data=img_bytes, mime_type=mime_type), prompt])

    def generar_texto_alternativo(self, ruta_imagen: str, prompt: str) -> str:
        return self.describir_imagen(ruta_imagen, prompt)

    def resumir_texto(self, texto: str, prompt: str) -> str:
        return self._generar([prompt, texto])

    def sugerir_hashtags(self, texto: str, prompt: str) -> str:
        return self._generar([prompt, texto])

    def mejorar_redaccion(self, texto: str) -> str:
        prompt = (
            "Corrige la ortografía, gramática y mejora el estilo del siguiente texto en español. "
            "Devuelve ÚNICAMENTE el texto corregido final, sin introducciones, explicaciones, "
            "comentarios ni comillas adicionales."
        )
        return self._generar([prompt, texto])

    def probar_conexion(self) -> bool:
        try:
            return bool(self._generar("test"))
        except Exception as e:
            log.error(f"Fallo de conexión en GeminiProvider: {e}")
            return False
