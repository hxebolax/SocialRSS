# -*- coding: utf-8 -*-
"""
Servicio central de traducción integrada de la aplicación.
Soporta detección de idioma, traducción de texto e historial persistente en SQLite.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.core.config_manager import config
from app.core.database import db
from app.core.errors import TranslationError
from .providers.google_translation_provider import GoogleTranslationProvider
from .providers.google_web_provider import (GoogleWebTranslationProvider,
                                            GoogleWebAltTranslationProvider)

log = logging.getLogger(__name__)

# Motores GRATUITOS disponibles (clave de config -> (etiqueta, clase)). Todos verificados sin clave.
PROVEEDORES_GRATIS = {
    "google_api_free": (_("Google Translate (API gratuita)"), GoogleTranslationProvider),
    "google_web": (_("Google Translate (web 1)"), GoogleWebTranslationProvider),
    "google_web_alt": (_("Google Translate (web 2)"), GoogleWebAltTranslationProvider),
}


class TranslationService:
    """Coordinador de traducción."""

    def __init__(self):
        # Una instancia por motor (se crean al vuelo y se cachean).
        self._instancias = {}

    def proveedores_disponibles(self) -> list:
        """Lista [(clave, etiqueta)] de los motores de traducción gratuitos para Preferencias."""
        return [(k, v[0]) for k, v in PROVEEDORES_GRATIS.items()]

    def _instancia(self, clave: str):
        if clave not in self._instancias:
            cls = PROVEEDORES_GRATIS.get(clave, (None, None))[1]
            self._instancias[clave] = cls() if cls else None
        return self._instancias[clave]

    def obtener_proveedor_activo(self):
        """Devuelve el motor de traducción activo según la preferencia."""
        prov = config.get("translation", "provider", "google_api_free")
        return self._instancia(prov) or self._instancia("google_api_free")

    def _calcular_hash(self, text: str) -> str:
        """Calcula el hash MD5 del texto original para búsquedas rápidas."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def traducir(self, texto: str, idioma_origen: Optional[str] = None, idioma_destino: Optional[str] = None) -> str:
        """Traduce texto de origen a destino e inserta en el historial."""
        if not texto or not texto.strip():
            return ""
            
        prov = self.obtener_proveedor_activo()
        if not prov:
            raise TranslationError("No hay ningún motor de traducción activo en la configuración.")
            
        # Determinar idiomas por defecto si no se especifican
        sl = idioma_origen if idioma_origen else config.get("translation", "default_source", "auto")
        tl = idioma_destino if idioma_destino else config.get("translation", "default_target", "es")
        
        # 1. Comprobar si ya existe la traducción en el historial de SQLite
        text_hash = self._calcular_hash(texto)
        if config.get("translation", "save_history", True):
            sql = "SELECT translated_text, source_lang FROM translation_history WHERE text_hash = ? AND target_lang = ?"
            try:
                row = db.query_one(sql, (text_hash, tl))
                if row:
                    log.debug("Traducción recuperada del historial de SQLite.")
                    return row[0]
            except Exception as e:
                log.error(f"Error al buscar en historial de traducciones: {e}")
                
        # 2. Si no está en historial, traducir usando el motor
        translated = prov.traducir(texto, sl, tl)
        
        # 3. Detectar idioma original si se configuró auto
        if sl == "auto":
            try:
                sl = prov.detectar_idioma(texto)
            except Exception:
                sl = "unknown"
                
        # 4. Guardar en historial de base de datos
        if config.get("translation", "save_history", True):
            sql_insert = """
            INSERT INTO translation_history (
                text_hash, original_text, translated_text, source_lang, target_lang, provider, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            now_str = datetime.now().isoformat()
            try:
                last_id = db.execute_insert(sql_insert, (
                    text_hash,
                    texto,
                    translated,
                    sl,
                    tl,
                    config.get("translation", "provider", "google_api_free"),
                    now_str
                ))

                # Sincronizar con FTS5 si está disponible
                try:
                    db.execute(
                        "INSERT INTO translation_history_fts (history_id, original_text, translated_text) VALUES (?, ?, ?)",
                        (last_id, texto, translated)
                    )
                except Exception as fts_err:
                    log.debug(f"No se pudo indexar en FTS de traducción: {fts_err}")
                    
            except Exception as e:
                log.error(f"Error al guardar traducción en historial: {e}")
                
        return translated

    def detectar_idioma(self, texto: str) -> str:
        """Detecta el idioma del texto."""
        prov = self.obtener_proveedor_activo()
        if not prov:
            return "auto"
        return prov.detectar_idioma(texto)

    def obtener_historial(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Recupera el historial de traducciones realizadas."""
        sql = """
        SELECT id, original_text, translated_text, source_lang, target_lang, provider, created_at
        FROM translation_history
        ORDER BY created_at DESC
        LIMIT ?
        """
        historial = []
        try:
            rows = db.query(sql, (limit,))
            for r in rows:
                historial.append({
                    "id": r[0],
                    "original_text": r[1],
                    "translated_text": r[2],
                    "source_lang": r[3],
                    "target_lang": r[4],
                    "provider": r[5],
                    "created_at": r[6]
                })
        except Exception as e:
            log.error(f"Error al obtener historial de traducciones: {e}")
        return historial

    def borrar_historial(self):
        """Limpia el historial de traducciones persistido."""
        try:
            db.execute("DELETE FROM translation_history")
            # Limpiar FTS
            try:
                db.execute("DELETE FROM translation_history_fts")
            except Exception:
                pass
            log.info("Historial de traducciones vaciado de la base de datos.")
        except Exception as e:
            log.error(f"Error al borrar el historial de traducciones: {e}")

# Instancia global única del servicio de traducción
translation_service = TranslationService()
