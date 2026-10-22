# -*- coding: utf-8 -*-
"""Borrado DEFINITIVO de artículos RSS que no vuelven a aparecer, y qué hace Suprimir en
cada feed.

Antes, vaciar la papelera borraba los artículos de la base de datos pero, si el feed los
seguía sirviendo, el siguiente sondeo los volvía a meter como nuevos y sin leer (y el índice de
búsqueda se quedaba con filas huérfanas). Ahora el borrado definitivo anota el identificador en
rss_deleted_ledger, que el sondeo consulta para saltárselos, y limpia también el índice.

Suprimir sobre un artículo: por defecto a la papelera; se puede elegir por feed "borrar
definitivamente" (Editar feed). La tabla se crea sola (sin migración).
"""
import logging
from datetime import datetime
from typing import Iterable, List, Set

from app.core.config_manager import config
from app.core.database import db

log = logging.getLogger(__name__)
MODOS = ("papelera", "definitivo")
_MAX_REGISTRO = 200_000
_creada = False


def asegurar_tabla():
    global _creada
    if not _creada:
        db.execute("CREATE TABLE IF NOT EXISTS rss_deleted_ledger (article_id TEXT PRIMARY KEY, ts TEXT)")
        _creada = True


def borrar_definitivo(ids: Iterable[str]) -> int:
    """Borra los artículos para siempre (con su índice, adjuntos y etiquetas) y los anota
    para que el sondeo no los vuelva a traer. Devuelve cuántos se borraron."""
    ids = [i for i in ids if i]
    if not ids:
        return 0
    asegurar_tabla()
    ahora = datetime.now().isoformat(timespec="seconds")
    borrados = 0
    with db.transaccion():
        for i in range(0, len(ids), 400):
            lote = ids[i:i + 400]
            ph = ",".join("?" for _ in lote)
            for aid in lote:
                db.execute("INSERT OR REPLACE INTO rss_deleted_ledger (article_id, ts) VALUES (?, ?)", (aid, ahora))
            for tabla, col in (("rss_articles_fts", "article_db_id"), ("rss_enclosures", "article_id"),
                               ("rss_article_tags", "article_id")):
                try:
                    db.execute(f"DELETE FROM {tabla} WHERE {col} IN ({ph})", tuple(lote))
                except Exception as e:
                    log.debug(f"Borrado definitivo: no se pudo limpiar {tabla}: {e}")
            antes = db.query_one(f"SELECT COUNT(*) FROM rss_articles WHERE id IN ({ph})", tuple(lote))
            db.execute(f"DELETE FROM rss_articles WHERE id IN ({ph})", tuple(lote))
            borrados += antes[0] if antes else 0
    # Tope del registro: los más antiguos ya no los sirve ningún feed.
    try:
        total = db.query_one("SELECT COUNT(*) FROM rss_deleted_ledger")[0]
        if total > _MAX_REGISTRO:
            db.execute("DELETE FROM rss_deleted_ledger WHERE article_id IN (SELECT article_id FROM "
                       "rss_deleted_ledger ORDER BY ts LIMIT ?)", (total - _MAX_REGISTRO,))
    except Exception:
        pass
    return borrados


def borrados_entre(ids: List[str]) -> Set[str]:
    """Cuáles de estos ids se borraron definitivamente (el sondeo se los salta)."""
    asegurar_tabla()
    salida = set()
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        ph = ",".join("?" for _ in lote)
        salida.update(r[0] for r in db.query(
            f"SELECT article_id FROM rss_deleted_ledger WHERE article_id IN ({ph})", tuple(lote)))
    return salida


def modo_de_feed(feed_id) -> str:
    """Qué hace Suprimir en los artículos de ese feed: 'papelera' o 'definitivo'."""
    por_feed = config.get("rss", "delete_mode_feeds", {}) or {}
    modo = por_feed.get(str(feed_id)) or config.get("rss", "delete_mode", "papelera")
    return modo if modo in MODOS else "papelera"


def fijar_modo_de_feed(feed_id, modo: str):
    por_feed = dict(config.get("rss", "delete_mode_feeds", {}) or {})
    if modo in MODOS and modo != "papelera":
        por_feed[str(feed_id)] = modo
    else:
        por_feed.pop(str(feed_id), None)
    config.set("rss", "delete_mode_feeds", por_feed)
