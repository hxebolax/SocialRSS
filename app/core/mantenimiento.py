# -*- coding: utf-8 -*-
"""Poda del histórico para que las tablas no crezcan sin fin y las consultas se mantengan rápidas con el tiempo. Recorta los
buffers de alto volumen y las notificaciones a un máximo (si el usuario lo fijó), borra contenido huérfano no guardado y
recorta la caché de Telegram. Conserva lo guardado (favoritos, marcadores) y los buffers curados. Lo usan las interfaces de
Windows (wx) y de Linux (GTK): se lanza en segundo plano al arrancar y cada 20 minutos."""

import logging

from app.core.config_manager import config
from app.core.database import db

log = logging.getLogger(__name__)


def _int_cfg(seccion: str, clave: str) -> int:
    try:
        return int(config.get(seccion, clave, 0))
    except Exception:
        return 0


def podar_historico() -> bool:
    """Aplica los límites de cada red (0 = sin límite: ningún buffer se recorta a menos que el usuario fije un máximo)."""
    from app.services.mastodon.mastodon_service import mastodon_service
    from app.services.rss.rss_service import rss_service
    mst_buf, mst_notif = _int_cfg("mastodon", "max_items_buffer"), _int_cfg("mastodon", "max_notifications")
    bsky_buf, bsky_notif = _int_cfg("bluesky", "max_items_buffer"), _int_cfg("bluesky", "max_notifications")
    max_tg = _int_cfg("telegram", "max_messages_per_chat")
    leer_prefs_srv = bool(config.get("mastodon", "read_preferences_from_instance", False))
    if mst_buf > 0 or mst_notif > 0:
        for (mid,) in db.query("SELECT id FROM accounts WHERE service_id = 'mastodon' AND active = 1"):
            mastodon_service.podar_antiguos(mid, max_buffer=mst_buf, max_notif=mst_notif)
    # Precargar en segundo plano las preferencias del servidor (visibilidad por defecto) para que el compositor las tenga listas.
    if leer_prefs_srv:
        for (mid,) in db.query("SELECT id FROM accounts WHERE service_id = 'mastodon' AND active = 1"):
            try:
                mastodon_service.obtener_preferencias_servidor(mid)
            except Exception:
                pass
    if bsky_buf > 0 or bsky_notif > 0:
        from app.services.bluesky import bluesky_service as _bsky
        for (bid,) in db.query("SELECT id FROM accounts WHERE service_id = 'bluesky' AND active = 1"):
            _bsky.podar_antiguos(bid, max_buffer=bsky_buf, max_notif=bsky_notif)
    if max_tg > 0:
        from app.services.telegram.telegram_service import telegram_service
        for (tid,) in db.query("SELECT id FROM accounts WHERE service_id = 'telegram' AND active = 1"):
            telegram_service.podar_mensajes_antiguos(tid, max_por_chat=max_tg)
    for (rid,) in db.query("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1"):
        try:
            rss_service.limpieza_mantenimiento(rid)
        except Exception:
            pass
    # Limpiar filas huérfanas de las tablas FTS (índices de búsqueda sin su registro).
    try:
        from app.core.data_cleanup import limpiar_huerfanos_fts
        limpiar_huerfanos_fts()
    except Exception as e:
        log.debug(f"No se pudieron limpiar huérfanos FTS en mantenimiento: {e}")
    return True
