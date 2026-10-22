# -*- coding: utf-8 -*-
"""
Servicio central de Inteligencia Artificial.
Permite delegar tareas al proveedor activo (Gemini, Llama Local, etc.)
e implementa una caché persistente en SQLite para ahorrar tokens y tiempo.
"""

import hashlib
import logging
from datetime import datetime
from typing import Optional

from app.core.config_manager import config
from app.core.database import db
from app.core.errors import AIError
from .providers.gemini_provider import GeminiProvider
from .providers.local_llama_provider import LocalLlamaProvider
from .providers.anthropic_provider import AnthropicProvider
from .providers.openai_compatible_provider import OpenAICompatibleProvider, SERVICIOS_OPENAI

log = logging.getLogger(__name__)

class AIService:
    """Coordinador central de peticiones de IA."""

    def __init__(self):
        self._gemini = GeminiProvider()
        self._local_llama = LocalLlamaProvider()
        self._anthropic = AnthropicProvider()
        # Un proveedor por cada servicio compatible con OpenAI (OpenAI, DeepSeek, xAI, OpenRouter...).
        self._openai_compat = {
            sid: OpenAICompatibleProvider(sid, nombre, base, modelo)
            for sid, (nombre, base, modelo) in SERVICIOS_OPENAI.items()
        }

    def proveedores_disponibles(self) -> list:
        """Lista [(clave, etiqueta, necesita_claves)] de proveedores de IA para Preferencias."""
        provs = [("gemini", _("Google Gemini"), True)]
        for sid, (nombre, _b, _m) in SERVICIOS_OPENAI.items():
            provs.append((sid, nombre, True))
        provs.append(("anthropic", _("Anthropic (Claude)"), True))
        provs.append(("local_llama", _("Llama local (servidor propio)"), False))
        provs.append(("off", _("Desactivada"), False))
        return provs

    def _proveedor(self, prov: str):
        if prov == "gemini":
            return self._gemini
        if prov == "anthropic":
            return self._anthropic
        if prov == "local_llama":
            return self._local_llama
        if prov in self._openai_compat:
            return self._openai_compat[prov]
        return None

    def obtener_proveedor_activo(self):
        """Devuelve el proveedor de IA configurado."""
        return self._proveedor(config.get("ai", "provider", "gemini"))

    def listar_modelos(self, prov: str = None) -> list:
        """Modelos disponibles del proveedor indicado (o del activo)."""
        p = self._proveedor(prov) if prov else self.obtener_proveedor_activo()
        if not p or not hasattr(p, "listar_modelos"):
            raise AIError("Este proveedor no permite obtener modelos.")
        return p.listar_modelos()

    def _calcular_hash(self, prompt: str, datos_adicionales: str = "") -> str:
        """Calcula el hash SHA-256 para servir como clave de caché."""
        sha = hashlib.sha256()
        sha.update(prompt.encode("utf-8"))
        if datos_adicionales:
            sha.update(datos_adicionales.encode("utf-8"))
        return sha.hexdigest()

    def _buscar_en_cache(self, key_hash: str) -> Optional[str]:
        """Busca una respuesta guardada previamente en SQLite."""
        if not config.get("ai", "cache_responses", True):
            return None
            
        sql = "SELECT response FROM ai_cache WHERE prompt_hash = ?"
        try:
            row = db.query_one(sql, (key_hash,))
            if row:
                log.debug("Respuesta recuperada de la caché de IA.")
                return row[0]
        except Exception as e:
            log.error(f"Error al buscar en caché de IA: {e}")
        return None

    def _guardar_en_cache(self, key_hash: str, response: str):
        """Persiste una respuesta de IA en la caché SQLite."""
        if not config.get("ai", "cache_responses", True):
            return

        sql = "INSERT OR REPLACE INTO ai_cache (prompt_hash, response, created_at) VALUES (?, ?, ?)"
        now_str = datetime.now().isoformat()
        try:
            db.execute(sql, (key_hash, response, now_str))
        except Exception as e:
            log.error(f"Error al guardar en caché de IA: {e}")

    def _registrar_historial(self, funcion: str, entrada: str, respuesta: str):
        """Guarda la operación de IA en el historial."""
        try:
            db.execute(
                "INSERT INTO ai_history (funcion, entrada, respuesta, created_at) VALUES (?, ?, ?, ?)",
                (funcion, (entrada or "")[:2000], (respuesta or "")[:5000], datetime.now().isoformat())
            )
        except Exception as e:
            log.debug(f"No se pudo registrar el historial de IA: {e}")

    def listar_modelos_gemini(self) -> list:
        """Devuelve los modelos disponibles de Gemini para que el usuario elija."""
        return self._gemini.listar_modelos()

    def describir_imagen(self, ruta_imagen: str) -> str:
        """Describe una imagen local usando el prompt configurado."""
        prov = self.obtener_proveedor_activo()
        if not prov:
            raise AIError("No hay ningún proveedor de IA activo en la configuración.")
            
        prompt = config.get("ai", "prompt_image_description", "")
        # El hash incluye el prompt, el nombre del archivo y su tamaño para detectar cambios
        # (Opcional, también podríamos leer los bytes del archivo para el hash, pero el tamaño + ruta es rápido)
        import os
        try:
            size = os.path.getsize(ruta_imagen)
            hash_key = self._calcular_hash(prompt, f"{ruta_imagen}:{size}")
        except Exception:
            hash_key = self._calcular_hash(prompt, ruta_imagen)
            
        cached = self._buscar_en_cache(hash_key)
        if cached:
            return cached
            
        response = prov.describir_imagen(ruta_imagen, prompt)
        self._guardar_en_cache(hash_key, response)
        self._registrar_historial("Descripción de imagen", ruta_imagen, response)
        return response

    def ocr_imagen(self, ruta_imagen: str) -> str:
        """Extrae (transcribe) el texto literal que aparece en una imagen (OCR)."""
        prov = self.obtener_proveedor_activo()
        if not prov:
            raise AIError("No hay ningún proveedor de IA activo.")
        prompt = ("Transcribe literalmente TODO el texto que aparezca en esta imagen, "
                  "respetando saltos de línea. Si no hay texto, responde exactamente: "
                  "(La imagen no contiene texto).")
        import os
        try:
            size = os.path.getsize(ruta_imagen)
            hash_key = self._calcular_hash(prompt, f"{ruta_imagen}:{size}:ocr")
        except Exception:
            hash_key = self._calcular_hash(prompt, f"{ruta_imagen}:ocr")
        cached = self._buscar_en_cache(hash_key)
        if cached:
            return cached
        response = prov.describir_imagen(ruta_imagen, prompt)
        self._guardar_en_cache(hash_key, response)
        self._registrar_historial("OCR de imagen", ruta_imagen, response)
        return response

    def generar_texto_alternativo(self, ruta_imagen: str) -> str:
        """Genera un texto alternativo conciso para publicaciones (alt text)."""
        prov = self.obtener_proveedor_activo()
        if not prov:
            raise AIError("No hay ningún proveedor de IA activo.")
            
        prompt = config.get("ai", "prompt_alt_text", "")
        import os
        try:
            size = os.path.getsize(ruta_imagen)
            hash_key = self._calcular_hash(prompt, f"{ruta_imagen}:{size}:alt")
        except Exception:
            hash_key = self._calcular_hash(prompt, f"{ruta_imagen}:alt")
            
        cached = self._buscar_en_cache(hash_key)
        if cached:
            return cached
            
        response = prov.generar_texto_alternativo(ruta_imagen, prompt)
        # Limpiar posibles saltos de línea molestos en alt text
        response = response.replace("\n", " ").strip()
        self._guardar_en_cache(hash_key, response)
        self._registrar_historial("Texto alternativo", ruta_imagen, response)
        return response

    def resumir_texto(self, texto: str) -> str:
        """Resume un texto largo."""
        prov = self.obtener_proveedor_activo()
        if not prov:
            raise AIError("No hay ningún proveedor de IA activo.")
            
        prompt = config.get("ai", "prompt_text_summary", "")
        hash_key = self._calcular_hash(prompt, texto)
        
        cached = self._buscar_en_cache(hash_key)
        if cached:
            return cached
            
        response = prov.resumir_texto(texto, prompt)
        self._guardar_en_cache(hash_key, response)
        self._registrar_historial("Resumen", texto, response)
        return response

    def sugerir_hashtags(self, texto: str) -> str:
        """Sugiere hashtags para un post."""
        prov = self.obtener_proveedor_activo()
        if not prov:
            raise AIError("No hay ningún proveedor de IA activo.")
            
        prompt = config.get("ai", "prompt_hashtags", "")
        hash_key = self._calcular_hash(prompt, texto)
        
        cached = self._buscar_en_cache(hash_key)
        if cached:
            return cached
            
        response = prov.sugerir_hashtags(texto, prompt)
        self._guardar_en_cache(hash_key, response)
        self._registrar_historial("Hashtags", texto, response)
        return response

    def mejorar_redaccion(self, texto: str) -> str:
        """Corrige la redacción y ortografía del texto."""
        prov = self.obtener_proveedor_activo()
        if not prov:
            raise AIError("No hay ningún proveedor de IA activo.")
            
        # Para mejorar redacción no usamos caché porque suele ser sobre borradores en edición dinámica
        respuesta = prov.mejorar_redaccion(texto)
        self._registrar_historial("Mejorar redacción", texto, respuesta)
        return respuesta

# Instancia global del servicio de IA
ai_service = AIService()
