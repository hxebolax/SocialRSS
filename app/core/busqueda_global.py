# -*- coding: utf-8 -*-
"""Búsqueda global en texto completo sobre todo lo guardado en local (Mastodon, Bluesky, W Social, RSS, Telegram, X, IRC,
Matrix y XMPP). La usan las interfaces de Windows (wx) y de Linux (GTK); nunca toca la red."""

from app.core.database import db
from app.core.html_utils import html_a_texto
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service

# Posición de cada ámbito en el desplegable: 0 = todos.
AMBITOS = ("todos", "mastodon", "bluesky", "rss", "telegram", "x", "irc", "matrix", "xmpp", "wsocial")


def _cuentas(service):
    try:
        return [r[0] for r in db.query("SELECT id FROM accounts WHERE service_id = ?", (service,))]
    except Exception:
        return []


def buscar(query: str, ambito: int = 0) -> list:
    """Resultados {service, db_id, origen, texto, fecha, ...} de buscar `query` en el ámbito (índice de AMBITOS)."""
    from app.core.feature_flags import servicio_habilitado
    res = []
    # Un servicio DESACTIVADO no se busca, ni siquiera con el ámbito "Todos".
    if ambito in (0, 1) and servicio_habilitado("mastodon"):
        for acc in _cuentas("mastodon"):
            for st in mastodon_service.buscar_statuses_fts(acc, query, limit=100):
                res.append({
                    "service": "mastodon", "db_id": st.id, "account_id": acc,
                    "status_id": getattr(st, "status_id", "") or "", "uri": getattr(st, "uri", "") or "",
                    "origen": getattr(st, "author_name", "") or "",
                    "texto": html_a_texto(getattr(st, "content", "") or ""),
                    "fecha": str(getattr(st, "created_at", "") or ""),
                })
    if ambito in (0, 2) and servicio_habilitado("bluesky"):
        from app.services.bluesky import bluesky_service
        for acc in _cuentas("bluesky"):
            for po in bluesky_service.buscar_posts_fts(acc, query, limit=100):
                res.append({
                    "service": "bluesky", "db_id": po.id, "account_id": acc, "uri": getattr(po, "uri", "") or "",
                    "origen": getattr(po, "author_name", "") or "",
                    "texto": (getattr(po, "content", "") or ""),
                    "fecha": str(getattr(po, "indexed_at", "") or ""),
                })
    # W Social: se busca en lo YA descargado (índice de texto completo local), como en
    # el resto de redes; la búsqueda global nunca depende de la red.
    if ambito in (0, 9) and servicio_habilitado("wsocial"):
        from app.services.wsocial import wsocial_service
        for fila in wsocial_service.buscar_local(query, limite=100):
            res.append({
                "service": "wsocial", "db_id": fila[0], "account_id": fila[5] if len(fila) > 5 else None,
                "origen": fila[1] or fila[2] or "",
                "texto": fila[3] or "",
                "fecha": str(fila[4] or ""),
            })
    if ambito in (0, 3) and servicio_habilitado("rss"):
        for acc in _cuentas("rss"):
            for art in rss_service.buscar_articulos_fts(acc, query, limit=100):
                res.append({
                    "service": "rss", "db_id": art.id, "account_id": acc, "link": getattr(art, "link", "") or "",
                    "origen": getattr(art, "author", "") or "",
                    "texto": (getattr(art, "title", "") or ""),
                    "fecha": str(getattr(art, "published_at", "") or ""),
                })
    # Telegram y X no tienen FTS; se buscan con LIKE sobre el texto cacheado.
    like = f"%{query}%"
    if ambito in (0, 4) and servicio_habilitado("telegram"):
        for acc in _cuentas("telegram"):
            for row in db.query(
                    "SELECT id, sender_name, text, date, chat_id FROM telegram_messages "
                    "WHERE account_id = ? AND text LIKE ? ORDER BY date DESC LIMIT 100",
                    (acc, like)):
                res.append({
                    "service": "telegram", "db_id": row[0], "chat_id": row[4], "account_id": acc,
                    "origen": row[1] or "", "texto": row[2] or "", "fecha": str(row[3] or ""),
                })
    if ambito in (0, 5) and servicio_habilitado("x"):
        for acc in _cuentas("x"):
            for row in db.query(
                    "SELECT id, author_name, author_screen_name, text, created_at, tweet_id "
                    "FROM x_tweets WHERE account_id = ? AND text LIKE ? ORDER BY created_at DESC LIMIT 100",
                    (acc, like)):
                res.append({
                    "service": "x", "db_id": row[0], "account_id": acc, "screen": row[2] or "", "tweet_id": row[5],
                    "origen": row[1] or row[2] or "", "texto": row[3] or "", "fecha": str(row[4] or ""),
                })
    if ambito in (0, 7) and servicio_habilitado("matrix"):
        from app.services.matrix import matrix_service
        for acc in _cuentas("matrix"):
            for m in matrix_service.buscar_mensajes_fts(acc, query, limit=100):
                res.append({
                    "service": "matrix", "db_id": m["db_id"], "account_id": acc,
                    "room_id": m["room_id"], "origen": m["quien"] or "",
                    "texto": m["texto"] or "", "fecha": str(m["date"] or ""),
                })
    if ambito in (0, 6) and servicio_habilitado("irc"):
        from app.services.irc import irc_service
        for acc in _cuentas("irc"):
            for m in irc_service.buscar_mensajes_fts(acc, query, limit=100):
                res.append({
                    "service": "irc", "db_id": m["db_id"], "account_id": acc,
                    "channel": m["channel"],
                    "origen": f"{m['nick']} ({m['channel']})" if m.get("nick") else m["channel"],
                    "texto": m["text"] or "", "fecha": str(m["date"] or ""),
                })
    if ambito in (0, 8) and servicio_habilitado("xmpp"):
        from app.services.xmpp.xmpp_service import xmpp_service
        for acc in _cuentas("xmpp"):
            for m in xmpp_service.buscar_mensajes_fts(acc, query, limit=100):
                res.append({
                    "service": "xmpp", "db_id": m["db_id"], "account_id": acc,
                    "chat_jid": m["chat_jid"], "origen": m["quien"] or m["chat_jid"],
                    "texto": m["texto"] or "", "fecha": str(m["date"] or ""),
                })
    return res
