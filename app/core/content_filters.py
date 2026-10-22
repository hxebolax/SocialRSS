# -*- coding: utf-8 -*-
"""
Motor de filtros de contenido UNIFICADO para Mastodon y RSS.

Un mismo conjunto de reglas permite, según su acción:
  - hide:        ocultar el elemento (no se muestra en la lista).
  - mark_read:   marcarlo automáticamente como leído.
  - star:        destacarlo/marcarlo como favorito automáticamente.
  - read_later:  guardarlo automáticamente para leer más tarde (solo RSS).

Cada filtro se aplica a un servicio ('mastodon', 'rss' o 'all') y a un campo
('text', 'author' o 'all'), con coincidencia por subcadena (insensible a
mayúsculas) o por expresión regular.

El motor es independiente de la UI; los cargadores de listas consultan
`evaluar()` para decidir qué hacer con cada elemento.
"""

import re
import logging
from typing import List, Dict, Any
from app.core.database import db

log = logging.getLogger(__name__)

ACCIONES_VALIDAS = ("hide", "mark_read", "star", "read_later")
CAMPOS_VALIDOS = ("all", "text", "author")
# Servicios a los que se puede asignar un filtro de contenido (publicaciones/artículos). Bluesky y
# X tienen publicaciones igual que Mastodon; 'all' aplica a todos. (Telegram es mensajería y no usa
# estos filtros.)
SERVICIOS_VALIDOS = ("all", "mastodon", "rss", "bluesky", "x", "wsocial")


class ContentFilterEngine:
    """Carga y evalúa los filtros de contenido unificados."""

    @staticmethod
    def cargar(service: str) -> List[Dict[str, Any]]:
        """Devuelve los filtros activos aplicables a un servicio (incluye los 'all')."""
        try:
            rows = db.query(
                """SELECT id, service, name, pattern, is_regex, field, action, active
                   FROM content_filters
                   WHERE active = 1 AND (service = ? OR service = 'all')""",
                (service,)
            )
        except Exception as e:
            log.debug(f"No se pudieron cargar los filtros de contenido: {e}")
            return []
        filtros = []
        for r in rows:
            filtros.append({
                "id": r[0], "service": r[1], "name": r[2], "pattern": r[3],
                "is_regex": bool(r[4]), "field": r[5] or "all",
                "action": r[6] or "hide", "active": bool(r[7]),
            })
        return filtros

    @staticmethod
    def _coincide(filtro: Dict[str, Any], texto: str, autor: str) -> bool:
        """Indica si un filtro coincide con el texto/autor dados."""
        campo = filtro.get("field", "all")
        if campo == "text":
            objetivo = texto or ""
        elif campo == "author":
            objetivo = autor or ""
        else:
            objetivo = f"{texto or ''} {autor or ''}"
        if not objetivo:
            return False
        patron = filtro.get("pattern", "")
        if not patron:
            return False
        if filtro.get("is_regex"):
            try:
                return re.search(patron, objetivo, re.IGNORECASE) is not None
            except re.error:
                # Regex inválida: degradar a subcadena para no romper el filtrado.
                return patron.lower() in objetivo.lower()
        return patron.lower() in objetivo.lower()

    @classmethod
    def evaluar(cls, service: str, texto: str, autor: str = "",
                filtros: List[Dict[str, Any]] = None) -> Dict[str, bool]:
        """
        Evalúa texto/autor contra los filtros del servicio. Devuelve un dict de
        acciones a aplicar: {hide, mark_read, star, read_later} (booleanos).
        Si se pasa `filtros` (precargados) se reutilizan para no consultar la BD
        en cada elemento de la lista.
        """
        resultado = {"hide": False, "mark_read": False, "star": False, "read_later": False}
        if filtros is None:
            filtros = cls.cargar(service)
        if not filtros:
            return resultado
        for f in filtros:
            if cls._coincide(f, texto, autor):
                accion = f.get("action", "hide")
                if accion in resultado:
                    resultado[accion] = True
        return resultado

    # --- CRUD para el diálogo de gestión ---
    @staticmethod
    def listar_todos() -> List[Dict[str, Any]]:
        """Todos los filtros (activos e inactivos) para el gestor."""
        try:
            rows = db.query(
                """SELECT id, service, name, pattern, is_regex, field, action, active
                   FROM content_filters ORDER BY service, id"""
            )
        except Exception as e:
            log.error(f"Error al listar filtros: {e}")
            return []
        return [{
            "id": r[0], "service": r[1], "name": r[2], "pattern": r[3],
            "is_regex": bool(r[4]), "field": r[5] or "all",
            "action": r[6] or "hide", "active": bool(r[7]),
        } for r in rows]

    @staticmethod
    def crear(service: str, name: str, pattern: str, is_regex: bool,
              field: str, action: str) -> int:
        if service not in SERVICIOS_VALIDOS:
            service = "all"
        if field not in CAMPOS_VALIDOS:
            field = "all"
        if action not in ACCIONES_VALIDAS:
            action = "hide"
        return db.execute_insert(
            """INSERT INTO content_filters (service, name, pattern, is_regex, field, action, active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (service, name or pattern, pattern, 1 if is_regex else 0, field, action)
        )

    @staticmethod
    def eliminar(filtro_id: int):
        db.execute("DELETE FROM content_filters WHERE id = ?", (filtro_id,))

    @staticmethod
    def set_activo(filtro_id: int, activo: bool):
        db.execute("UPDATE content_filters SET active = ? WHERE id = ?",
                   (1 if activo else 0, filtro_id))
