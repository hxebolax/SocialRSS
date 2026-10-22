# -*- coding: utf-8 -*-
"""Proveedor de IA para Anthropic (Claude).

Usa la librería oficial `anthropic`. Admite VARIAS claves API para rotación circular cuando una agota
su cuota, y obtener la lista de modelos del servicio. Guarda las claves en `ai.anthropic_api_keys`.
"""
import logging
import importlib.util
import base64
import mimetypes
from pathlib import Path

from .base_provider import BaseAIProvider
from app.core.config_manager import config
from app.core.errors import AIError

# Carga DIFERIDA del SDK 'anthropic' (importarlo al arrancar costaba ~0,5 s y solo hace falta si se
# usa Claude). Aquí solo se comprueba (barato) si está instalado.
HAS_ANTHROPIC = importlib.util.find_spec("anthropic") is not None
anthropic = None


def _asegurar_sdk():
    """Importa el SDK 'anthropic' la primera vez que se usa (carga diferida)."""
    global anthropic
    if anthropic is None:
        import anthropic as _anthropic
        anthropic = _anthropic

log = logging.getLogger(__name__)

_MODELO_DEF = "claude-3-5-haiku-latest"


class AnthropicProvider(BaseAIProvider):
    """Implementación del proveedor Anthropic (Claude)."""

    nombre = "Anthropic (Claude)"

    def __init__(self):
        self._client = None
        self._key_idx = 0
        self._key_actual = None

    def _get_keys(self) -> list:
        claves = config.get("ai", "anthropic_api_keys", [])
        if isinstance(claves, str):
            claves = [k.strip() for k in claves.replace(",", "\n").splitlines() if k.strip()]
        return [k for k in (claves or []) if k]

    def _get_client(self):
        if not HAS_ANTHROPIC:
            raise AIError("La librería 'anthropic' no está instalada. Instálala para usar Claude.")
        claves = self._get_keys()
        if not claves:
            raise AIError("No has configurado ninguna clave API de Anthropic en Preferencias.")
        if self._key_idx >= len(claves):
            self._key_idx = 0
        api_key = claves[self._key_idx]
        if not self._client or self._key_actual != api_key:
            _asegurar_sdk()
            self._client = anthropic.Anthropic(api_key=api_key)
            self._key_actual = api_key
        return self._client

    def _rotar_clave(self) -> bool:
        claves = self._get_keys()
        if len(claves) <= 1:
            return False
        self._key_idx = (self._key_idx + 1) % len(claves)
        self._client = None
        log.info(f"Rotando a la clave API de Anthropic número {self._key_idx + 1} de {len(claves)}.")
        return True

    @staticmethod
    def _es_error_de_cuota(e: Exception) -> bool:
        t = str(e).lower()
        return any(x in t for x in ("quota", "rate limit", "rate_limit", "429", "overloaded", "credit"))

    def _modelo(self):
        return config.get("ai", "model", _MODELO_DEF) or _MODELO_DEF

    def _generar(self, content) -> str:
        intentos = max(1, len(self._get_keys()))
        ultimo = None
        for _i in range(intentos):
            try:
                client = self._get_client()
                resp = client.messages.create(
                    model=self._modelo(), max_tokens=1500,
                    messages=[{"role": "user", "content": content}])
                partes = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
                return ("".join(partes) or "Sin respuesta del modelo.").strip()
            except Exception as e:
                ultimo = e
                if self._es_error_de_cuota(e) and self._rotar_clave():
                    continue
                raise AIError(f"Error en Anthropic: {e}")
        raise AIError(f"Todas las claves de Anthropic agotaron su cuota. Último error: {ultimo}")

    def listar_modelos(self) -> list:
        client = self._get_client()
        try:
            return sorted({getattr(m, "id", "") for m in client.models.list() if getattr(m, "id", "")})
        except Exception as e:
            log.error(f"Error al listar modelos de Anthropic: {e}")
            raise AIError(f"No se pudieron obtener los modelos: {e}")

    def describir_imagen(self, ruta_imagen: str, prompt: str) -> str:
        path = Path(ruta_imagen)
        if not path.exists():
            raise FileNotFoundError(f"Imagen no encontrada: {ruta_imagen}")
        mime, _ = mimetypes.guess_type(ruta_imagen)
        mime = mime or "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        contenido = [
            {"type": "text", "text": prompt},
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
        ]
        return self._generar(contenido)

    def generar_texto_alternativo(self, ruta_imagen: str, prompt: str) -> str:
        return self.describir_imagen(ruta_imagen, prompt)

    def resumir_texto(self, texto: str, prompt: str) -> str:
        return self._generar(f"{prompt}\n\n{texto}")

    def sugerir_hashtags(self, texto: str, prompt: str) -> str:
        return self._generar(f"{prompt}\n\n{texto}")

    def mejorar_redaccion(self, texto: str) -> str:
        prompt = ("Corrige la ortografía, gramática y mejora el estilo del siguiente texto en español. "
                  "Devuelve ÚNICAMENTE el texto corregido final, sin introducciones ni comillas.")
        return self._generar(f"{prompt}\n\n{texto}")

    def probar_conexion(self) -> bool:
        try:
            return bool(self._generar("test"))
        except Exception as e:
            log.error(f"Fallo de conexión en Anthropic: {e}")
            return False
