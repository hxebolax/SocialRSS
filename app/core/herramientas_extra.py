# -*- coding: utf-8 -*-
"""Cálculo de las herramientas transversales que comparten las interfaces de Windows (wx) y de Linux (GTK): estadísticas
sociales locales y el texto que se manda a la IA en «Ponte al día». Solo leen la caché local de la base de datos."""

from app.core.database import db


def calcular_estadisticas() -> dict:
    """Publicaciones por red, horas con más actividad y cuentas con más presencia, con los datos ya cacheados."""
    # (etiqueta, tabla, columna de fecha, columna de autor)
    fuentes = [
        (_("Mastodon"), "mastodon_statuses", "created_at", "author_username"),
        (_("Bluesky"), "bluesky_posts", "created_at", "author_handle"),
        (_("W Social"), "wsocial_posts", "created_at", "author_handle"),
        (_("RSS"), "rss_articles", "published_at", "author"),
        (_("X"), "x_tweets", "created_at", "author_screen_name"),
        (_("Telegram"), "telegram_messages", "date", "author_username"),
        (_("IRC"), "irc_messages", "date", "nick"),
        (_("Matrix"), "matrix_mensajes", "date", "sender_nombre"),
        (_("XMPP"), "xmpp_mensajes", "date", "sender_nombre"),
    ]
    totales = []
    por_hora = {h: 0 for h in range(24)}
    autores = {}
    for etiqueta, tabla, col_fecha, col_autor in fuentes:
        try:
            n = (db.query_one(f"SELECT COUNT(*) FROM {tabla}") or [0])[0]
        except Exception:
            continue  # la tabla puede no existir si la red nunca se usó
        totales.append((etiqueta, n))
        if not n:
            continue
        # Actividad por hora (substr del ISO: posiciones 12-13 = hora). Barato y sin parsear.
        try:
            for h, c in db.query(
                    f"SELECT substr({col_fecha},12,2) AS h, COUNT(*) FROM {tabla} "
                    f"WHERE length({col_fecha})>=13 GROUP BY h"):
                try:
                    por_hora[int(h)] = por_hora.get(int(h), 0) + c
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass
        # Autores más presentes (top por tabla, luego se combinan).
        try:
            for autor, c in db.query(
                    f"SELECT {col_autor}, COUNT(*) c FROM {tabla} "
                    f"WHERE {col_autor} IS NOT NULL AND {col_autor} != '' "
                    f"GROUP BY {col_autor} ORDER BY c DESC LIMIT 10"):
                autores[f"{autor} ({etiqueta})"] = c
        except Exception:
            pass
    top_autores = sorted(autores.items(), key=lambda x: x[1], reverse=True)[:10]
    top_horas = sorted(por_hora.items(), key=lambda x: x[1], reverse=True)[:5]
    return {"totales": totales, "top_autores": top_autores, "top_horas": top_horas}


def texto_estadisticas(d: dict) -> str:
    """Las estadísticas de `calcular_estadisticas` como texto para leer."""
    partes = [_("Contenido guardado por red:")]
    total_global = 0
    for etiqueta, n in d["totales"]:
        partes.append(f"  {etiqueta}: {n}")
        total_global += n
    partes.append(_("  Total: {0}").format(total_global))
    if d["top_horas"] and any(c for _h, c in d["top_horas"]):
        partes.append("")
        partes.append(_("Horas del día con más actividad:"))
        for h, c in d["top_horas"]:
            if c:
                partes.append(_("  {0}:00 — {1} elementos").format("%02d" % h, c))
    if d["top_autores"]:
        partes.append("")
        partes.append(_("Cuentas/fuentes con más presencia:"))
        for autor, c in d["top_autores"]:
            partes.append(f"  {autor}: {c}")
    return "\n".join(partes)


def texto_pendiente() -> str:
    """Lo que el usuario no ha leído (RSS y menciones) con la instrucción para que la IA lo resuma; None si no hay nada."""
    trozos = []
    # RSS no leído (título + feed).
    try:
        filas = db.query(
            "SELECT f.title, a.title FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
            "WHERE COALESCE(a.read,0)=0 AND COALESCE(a.deleted,0)=0 "
            "ORDER BY a.published_at DESC LIMIT 60")
        if filas:
            trozos.append(_("Artículos RSS sin leer ({0}):").format(len(filas)))
            for feed, titulo in filas:
                trozos.append(f"- [{feed}] {titulo}")
    except Exception:
        pass
    # Menciones de Mastodon recientes (últimas 24 h por fecha de la notificación).
    try:
        filas = db.query(
            "SELECT from_username, s.content FROM mastodon_notifications n "
            "LEFT JOIN mastodon_statuses s ON s.status_id = n.status_id "
            "WHERE n.type = 'mention' ORDER BY n.created_at DESC LIMIT 30")
        if filas:
            trozos.append("")
            trozos.append(_("Menciones recientes en Mastodon ({0}):").format(len(filas)))
            from app.core.html_utils import html_a_texto
            for user, content in filas:
                trozos.append(f"- {user}: {html_a_texto(content or '')[:200]}")
    except Exception:
        pass
    if not trozos:
        return None
    texto = "\n".join(trozos)
    instruccion = _("Haz un resumen breve, claro y por temas de lo siguiente, que es lo que "
                    "el usuario no ha leído en sus redes. Agrupa por asuntos y destaca lo más "
                    "importante. Responde en español:\n\n")
    return instruccion + texto
