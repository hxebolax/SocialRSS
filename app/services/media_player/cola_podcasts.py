# -*- coding: utf-8 -*-
"""Cola de reproducción del reproductor de podcasts (player2): episodios de RSS que sonarán
seguidos, en orden, y que se conserva al cerrar la aplicación.

Es una cola "a continuación": al terminar un episodio (o con "Siguiente de la cola") se saca el
primero y se reproduce. La tabla se crea sola la primera vez (no hace falta migración).
"""
import logging
from typing import List, Optional, Tuple

from app.core.database import db

log = logging.getLogger(__name__)
_creada = False


def _tabla():
    global _creada
    if not _creada:
        db.execute("CREATE TABLE IF NOT EXISTS player2_cola (article_id TEXT PRIMARY KEY, "
                   "titulo TEXT, orden INTEGER NOT NULL)")
        _creada = True


def listar() -> List[Tuple[str, str]]:
    _tabla()
    return [(r[0], r[1] or "") for r in db.query("SELECT article_id, titulo FROM player2_cola ORDER BY orden")]


def encolar(article_id: str, titulo: str) -> bool:
    """Añade al final. False si ya estaba en la cola."""
    _tabla()
    if db.query_one("SELECT 1 FROM player2_cola WHERE article_id = ?", (article_id,)):
        return False
    orden = (db.query_one("SELECT COALESCE(MAX(orden), 0) FROM player2_cola") or (0,))[0] + 1
    db.execute("INSERT INTO player2_cola (article_id, titulo, orden) VALUES (?, ?, ?)",
               (article_id, titulo or "", orden))
    return True


def quitar(article_id: str):
    _tabla()
    db.execute("DELETE FROM player2_cola WHERE article_id = ?", (article_id,))


def vaciar():
    _tabla()
    db.execute("DELETE FROM player2_cola")


def mover(article_id: str, delta: int) -> bool:
    """Sube (delta -1) o baja (+1) un episodio intercambiándolo con su vecino."""
    items = [a for a, _t in listar()]
    if article_id not in items:
        return False
    i = items.index(article_id)
    j = i + delta
    if not 0 <= j < len(items):
        return False
    items[i], items[j] = items[j], items[i]
    with db.transaccion():
        for n, aid in enumerate(items, start=1):
            db.execute("UPDATE player2_cola SET orden = ? WHERE article_id = ?", (n, aid))
    return True


def sacar_siguiente() -> Optional[Tuple[str, str]]:
    """Quita y devuelve el primero de la cola (o None si está vacía)."""
    items = listar()
    if not items:
        return None
    quitar(items[0][0])
    return items[0]
