# -*- coding: utf-8 -*-
"""Versiones anteriores de un artículo RSS.

Cuando el editor corrige una noticia (mismo GUID, otro título o texto), el sondeo ya actualizaba
el artículo guardado (auditoría RSS-14). Ahora, antes de sobrescribirlo, se guarda la versión
anterior, para poder ver qué ha cambiado (útil en noticias que se corrigen o se amplían). Se
conservan las 10 últimas versiones de cada artículo. La tabla se crea sola (sin migración).
"""
import difflib
import logging
import re
from datetime import datetime
from typing import List, Tuple

from app.core.database import db

log = logging.getLogger(__name__)
MAX_VERSIONES = 10
_creada = False


def asegurar_tabla():
    global _creada
    if not _creada:
        db.execute("CREATE TABLE IF NOT EXISTS rss_article_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "article_id TEXT NOT NULL, title TEXT, content TEXT, author TEXT, link TEXT, "
                   "saved_at TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rss_article_versions ON rss_article_versions(article_id)")
        _creada = True


def guardar_version_anterior(article_id: str):
    """Copia la versión ACTUAL del artículo antes de que se sobrescriba. Se llama dentro de la
    transacción del sondeo (asegurar_tabla() ya se llamó fuera)."""
    db.execute("INSERT INTO rss_article_versions (article_id, title, content, author, link, saved_at) "
               "SELECT id, title, content, author, link, ? FROM rss_articles WHERE id = ?",
               (datetime.now().isoformat(timespec="seconds"), article_id))
    db.execute("DELETE FROM rss_article_versions WHERE article_id = ? AND id NOT IN ("
               "SELECT id FROM rss_article_versions WHERE article_id = ? ORDER BY id DESC LIMIT ?)",
               (article_id, article_id, MAX_VERSIONES))


def listar(article_id: str) -> List[Tuple[int, str, str]]:
    """[(id, fecha en que se guardó, título)] de la más reciente a la más antigua."""
    asegurar_tabla()
    return [(r[0], r[1], r[2] or "") for r in db.query(
        "SELECT id, saved_at, title FROM rss_article_versions WHERE article_id = ? ORDER BY id DESC",
        (article_id,))]


def _texto(titulo: str, contenido: str) -> List[str]:
    from app.core.html_utils import html_a_texto
    cuerpo = html_a_texto(contenido or "")
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+|\n+", cuerpo) if f.strip()]
    return [titulo or ""] + frases


def diferencias(article_id: str, version_id: int) -> str:
    """Qué cambió entre esa versión y la actual, frase a frase: "Quitado:" y "Añadido:"."""
    asegurar_tabla()
    antes = db.query_one("SELECT title, content FROM rss_article_versions WHERE id = ?", (version_id,))
    ahora = db.query_one("SELECT title, content FROM rss_articles WHERE id = ?", (article_id,))
    if not antes or not ahora:
        return ""
    a, b = _texto(*antes), _texto(*ahora)
    lineas = []
    if (antes[0] or "") != (ahora[0] or ""):
        lineas += [_("Título anterior: {0}").format(antes[0] or ""),
                   _("Título actual: {0}").format(ahora[0] or ""), ""]
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a[1:], b[1:], autojunk=False).get_opcodes():
        if op in ("delete", "replace"):
            lineas += [_("Quitado: {0}").format(t) for t in a[1 + i1:1 + i2]]
        if op in ("insert", "replace"):
            lineas += [_("Añadido: {0}").format(t) for t in b[1 + j1:1 + j2]]
    return "\n".join(lineas) if lineas else _("El texto es igual; solo cambiaron detalles de formato.")
