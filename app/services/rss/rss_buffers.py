# -*- coding: utf-8 -*-
"""Filtro SQL de cada buffer RSS del árbol, en UN solo sitio.

Lo usan la carga completa de la lista (_cargar_listado_artículos) y la inserción incremental
de novedades (_filas_rss_actual). Antes cada una tenía su copia y la incremental no conocía las
carpetas inteligentes ni las etiquetas: al refrescar, en esos buffers entraban artículos que no
les correspondían.

Tipos: all, favorites, unread, read_later, today, category, feed, smart:<id>, tag:<id> y
search:<id> (búsqueda guardada como buffer, que se actualiza sola con cada sondeo). Los ids se
validan a entero; nunca se mete texto del usuario en el SQL (la búsqueda va como parámetro).
"""
import json
import logging
from typing import List, Tuple

from app.core.config_manager import config
from app.core.database import db

log = logging.getLogger(__name__)


def _id(tipo: str) -> int:
    try:
        return int(str(tipo).split(":", 1)[1])
    except (ValueError, IndexError):
        return 0


def consulta_fts(texto: str) -> str:
    """Consulta FTS5 segura: cada palabra entre comillas (Y implícito)."""
    return " ".join('"{0}"'.format(w.replace('"', '""')) for w in (texto or "").split() if w)


def where_buffer(tipo: str, category_id=None, feed_id=None) -> Tuple[str, List]:
    """(" AND ...", parámetros) del buffer, sobre los alias a (artículo) y f (feed)."""
    sql, params = "", []
    tipo = str(tipo or "all")
    if tipo == "favorites":
        sql += " AND a.favorite = 1"
    elif tipo == "unread":
        sql += " AND a.read = 0"
    elif tipo == "read_later":
        sql += " AND COALESCE(a.read_later, 0) = 1"
    elif tipo == "today":
        sql += " AND date(a.published_at) = date('now', 'localtime')"
    elif tipo == "category" and category_id:
        sql += " AND f.category_id = ?"
        params.append(category_id)
    elif tipo == "feed" and feed_id:
        sql += " AND a.feed_id = ?"
        params.append(feed_id)
    elif tipo.startswith("smart:"):
        # Carpeta inteligente: criterios ESTRUCTURADOS validados por el servicio.
        try:
            fila = db.query_one("SELECT criteria_json FROM rss_smart_folders WHERE id = ?", (_id(tipo),))
            criterios = json.loads(fila[0]) if fila else {}
        except Exception:
            criterios = {}
        from .rss_service import rss_service
        extra, extra_params = rss_service.where_carpeta_inteligente(criterios)
        sql += extra
        params.extend(extra_params)
    elif tipo.startswith("tag:"):
        tag = _id(tipo)
        sql += (" AND (a.id IN (SELECT article_id FROM rss_article_tags WHERE tag_id = ?) "
                "OR a.feed_id IN (SELECT feed_id FROM rss_feed_tags WHERE tag_id = ?))")
        params.extend([tag, tag])
    elif tipo.startswith("search:"):
        fila = db.query_one("SELECT query FROM rss_saved_searches WHERE id = ?", (_id(tipo),))
        consulta = consulta_fts(fila[0] if fila else "")
        if consulta:
            sql += " AND a.id IN (SELECT article_db_id FROM rss_articles_fts WHERE rss_articles_fts MATCH ?)"
            params.append(consulta)
        else:
            sql += " AND 0"
    if config.get("rss", "show_only_unread", False) and tipo in ("all", "category", "feed", "today"):
        sql += " AND a.read = 0"
    # Artículos ANCLADOS con fecha futura: ocultarlos si el usuario lo activó.
    if config.get("rss", "hide_future_articles", False):
        sql += " AND datetime(a.published_at) <= datetime('now', 'localtime')"
    return sql, params


def busquedas_en_arbol(account_id: int) -> List[Tuple[int, str]]:
    """Búsquedas guardadas de la cuenta que se muestran como buffer del árbol."""
    try:
        return [(r[0], r[1]) for r in db.query(
            "SELECT id, name FROM rss_saved_searches WHERE account_id = ? ORDER BY position, name",
            (account_id,))]
    except Exception as e:
        log.debug(f"Búsquedas guardadas no disponibles: {e}")
        return []
