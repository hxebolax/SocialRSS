# -*- coding: utf-8 -*-
"""Qué artículos nuevos de RSS merecen aviso (sonido y notificación).

- Feeds silenciados: "No avisar de sus artículos nuevos" en Editar feed (siguen llegando, pero
  sin sonido ni notificación).
- Reglas con la acción "No avisar de su llegada" (rss_rules.SIN_AVISO).
"""
from typing import List

from app.core.config_manager import config
from app.core.database import db


def feeds_silenciados() -> set:
    try:
        return {int(x) for x in (config.get("rss", "mute_feeds", []) or [])}
    except (TypeError, ValueError):
        return set()


def feed_silenciado(feed_id) -> bool:
    try:
        return int(feed_id) in feeds_silenciados()
    except (TypeError, ValueError):
        return False


def fijar_feed_silenciado(feed_id, silenciado: bool):
    actuales = feeds_silenciados()
    if silenciado:
        actuales.add(int(feed_id))
    else:
        actuales.discard(int(feed_id))
    config.set("rss", "mute_feeds", sorted(actuales))


def avisables(ids: List[str]) -> List[str]:
    """De los artículos nuevos, los que SÍ se avisan (en el mismo orden)."""
    if not ids:
        return []
    from .rss_rules import SIN_AVISO
    silenciados = feeds_silenciados()
    feed_de = {}
    if silenciados:
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            feed_de.update({r[0]: r[1] for r in db.query(
                f"SELECT id, feed_id FROM rss_articles WHERE id IN ({ph})", tuple(lote))})
    return [i for i in ids if i not in SIN_AVISO and feed_de.get(i) not in silenciados]
