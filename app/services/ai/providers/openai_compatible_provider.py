# -*- coding: utf-8 -*-
"""Proveedor de IA genérico COMPATIBLE CON LA API DE OPENAI.

Una sola implementación sirve para varios servicios que hablan el protocolo de OpenAI, cambiando solo
la base_url: OpenAI, DeepSeek, xAI (Grok), OpenRouter, Groq y Mistral. Cada servicio guarda sus claves
en `ai.<id>_api_keys` y admite VARIAS para rotación circular cuando una agota su cuota. Usa la
librería oficial `openai`.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import importlib.util
import base64
import mimetypes
from pathlib import Path

from .base_provider import BaseAIProvider
from app.core.config_manager import config
from app.core.errors import AIError

# Carga DIFERIDA del SDK 'openai' (importarlo al arrancar costaba ~0,5 s y solo hace falta al usar
# OpenAI/DeepSeek/xAI/OpenRouter/Groq/Mistral). Aquí solo se comprueba (barato) si está instalado.
HAS_OPENAI = importlib.util.find_spec("openai") is not None
OpenAI = None


def _asegurar_sdk():
    """Importa el SDK 'openai' la primera vez que se usa (carga diferida)."""
    global OpenAI
    if OpenAI is None:
        from openai import OpenAI as _OpenAI
        OpenAI = _OpenAI

log = logging.getLogger(__name__)

# Servicios compatibles con OpenAI: id -> (nombre visible, base_url, modelo por defecto).
SERVICIOS_OPENAI = {
    "openai":     (_("OpenAI (ChatGPT)"),  "https://api.openai.com/v1",        "gpt-4o-mini"),
    "deepseek":   (_("DeepSeek"),          "https://api.deepseek.com/v1",      "deepseek-chat"),
    "xai":        (_("xAI (Grok)"),        "https://api.x.ai/v1",              "grok-2-latest"),
    "openrouter": (_("OpenRouter"),        "https://openrouter.ai/api/v1",     "openai/gpt-4o-mini"),
    "groq":       (_("Groq"),              "https://api.groq.com/openai/v1",   "llama-3.3-70b-versatile"),
    "mistral":    (_("Mistral"),           "https://api.mistral.ai/v1",        "mistral-small-latest"),
}


class OpenAICompatibleProvider(BaseAIProvider):
    """Proveedor para cualquier servicio que use la API de OpenAI (chat completions)."""

    def __init__(self, service_id: str, nombre: str, base_url: str, modelo_def: str):
        self.service_id = service_id
        self.nombre = nombre
        self.base_url = base_url
        self.modelo_def = modelo_def
        self._client = None
        self._key_idx = 0
        self._key_actual = None

    def _get_keys(self) -> list:
        claves = config.get("ai", f"{self.service_id}_api_keys", [])
        if isinstance(claves, str):
            claves = [k.strip() for k in claves.replace(",", "\n").splitlines() if k.strip()]
        return [k for k in (claves or []) if k]

    def _get_client(self):
        if not HAS_OPENAI:
            raise AIError("La librería 'openai' no está instalada. Instálala para usar este servicio.")
        claves = self._get_keys()
        if not claves:
            raise AIError(f"No has configurado ninguna clave API de {self.nombre} en Preferencias.")
        if self._key_idx >= len(claves):
            self._key_idx = 0
        api_key = claves[self._key_idx]
        if not self._client or self._key_actual != api_key:
            _asegurar_sdk()
            self._client = OpenAI(api_key=api_key, base_url=self.base_url)
            self._key_actual = api_key
        return self._client

    def _rotar_clave(self) -> bool:
        claves = self._get_keys()
        if len(claves) <= 1:
            return False
        self._key_idx = (self._key_idx + 1) % len(claves)
        self._client = None
        log.info(f"Rotando a la clave API de {self.nombre} número {self._key_idx + 1} de {len(claves)}.")
        return True

    @staticmethod
    def _es_error_de_cuota(e: Exception) -> bool:
        t = str(e).lower()
        return any(x in t for x in ("quota", "rate limit", "rate_limit", "429", "insufficient", "exceeded"))

    def _modelo(self):
        return config.get("ai", "model", self.modelo_def) or self.modelo_def

    def _generar(self, messages) -> str:
        intentos = max(1, len(self._get_keys()))
        ultimo = None
        for _i in range(intentos):
            try:
                client = self._get_client()
                resp = client.chat.completions.create(model=self._modelo(), messages=messages)
                return (resp.choices[0].message.content or "Sin respuesta del modelo.").strip()
            except Exception as e:
                ultimo = e
                if self._es_error_de_cuota(e) and self._rotar_clave():
                    continue
                raise AIError(f"Error en {self.nombre}: {e}")
        raise AIError(f"Todas las claves de {self.nombre} agotaron su cuota. Último error: {ultimo}")

    def listar_modelos(self) -> list:
        client = self._get_client()
        try:
            return sorted({getattr(m, "id", "") for m in client.models.list() if getattr(m, "id", "")})
        except Exception as e:
            log.error(f"Error al listar modelos de {self.nombre}: {e}")
            raise AIError(f"No se pudieron obtener los modelos: {e}")

    def _img_data_url(self, ruta_imagen: str) -> str:
        path = Path(ruta_imagen)
        if not path.exists():
            raise FileNotFoundError(f"Imagen no encontrada: {ruta_imagen}")
        mime, _ = mimetypes.guess_type(ruta_imagen)
        mime = mime or "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def describir_imagen(self, ruta_imagen: str, prompt: str) -> str:
        contenido = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": self._img_data_url(ruta_imagen)}},
        ]
        return self._generar([{"role": "user", "content": contenido}])

    def generar_texto_alternativo(self, ruta_imagen: str, prompt: str) -> str:
        return self.describir_imagen(ruta_imagen, prompt)

    def resumir_texto(self, texto: str, prompt: str) -> str:
        return self._generar([{"role": "user", "content": f"{prompt}\n\n{texto}"}])

    def sugerir_hashtags(self, texto: str, prompt: str) -> str:
        return self._generar([{"role": "user", "content": f"{prompt}\n\n{texto}"}])

    def mejorar_redaccion(self, texto: str) -> str:
        prompt = ("Corrige la ortografía, gramática y mejora el estilo del siguiente texto en español. "
                  "Devuelve ÚNICAMENTE el texto corregido final, sin introducciones ni comillas.")
        return self._generar([{"role": "user", "content": f"{prompt}\n\n{texto}"}])

    def probar_conexion(self) -> bool:
        try:
            return bool(self._generar([{"role": "user", "content": "test"}]))
        except Exception as e:
            log.error(f"Fallo de conexión en {self.nombre}: {e}")
            return False
