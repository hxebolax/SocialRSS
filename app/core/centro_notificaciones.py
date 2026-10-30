# -*- coding: utf-8 -*-
"""Recopila las notificaciones de TODAS las redes para el centro de notificaciones unificado (menciones, respuestas, me
gusta, seguidores, chats con mensajes sin leer, invitaciones...). Solo lee la caché local: no hace peticiones de red.
Lo usan las interfaces de Windows (wx) y de Linux (GTK)."""

from app.core.database import db


def reunir() -> list:
    """Devuelve una lista de dicts {red, tipo, de, texto, fecha, abrir}, de más reciente a más antigua. `abrir` es una
    tupla cuyo primer elemento indica la red y el resto lo que hace falta para abrir el elemento."""
    from app.core.html_utils import html_a_texto
    items = []
    # Mastodon: todas las notificaciones cacheadas.
    _tipos_mast = {"mention": _("Mención"), "reblog": _("Impulso"), "favourite": _("Favorito"),
                   "follow": _("Seguidor"), "follow_request": _("Solicitud"), "poll": _("Encuesta"),
                   "status": _("Nueva publicación"), "update": _("Editada"), "quote": _("Cita")}
    try:
        for r in db.query(
                "SELECT n.account_id, n.type, n.created_at, n.from_name, n.from_username, "
                "n.status_id, s.content FROM mastodon_notifications n "
                "LEFT JOIN mastodon_statuses s ON s.status_id = n.status_id "
                "ORDER BY n.created_at DESC LIMIT 100"):
            acc, tipo, fecha, nombre, usuario, status_id, contenido = r
            items.append({
                "red": _("Mastodon"), "tipo": _tipos_mast.get(tipo, tipo or ""),
                "de": nombre or usuario or "", "texto": html_a_texto(contenido or "")[:200],
                "fecha": fecha or "", "abrir": ("mastodon", acc, status_id)})
    except Exception:
        pass
    # Bluesky: notificaciones cacheadas.
    _tipos_bsky = {"mention": _("Mención"), "reply": _("Respuesta"), "like": _("Me gusta"),
                   "repost": _("Repost"), "follow": _("Seguidor"), "quote": _("Cita")}
    try:
        from app.services.bluesky.bluesky_common import es_uri_de_post, uri_post_de_notificacion
        for r in db.query(
                "SELECT account_id, reason, indexed_at, author_name, author_handle, "
                "subject_uri, reason_subject, uri, content "
                "FROM bluesky_notifications ORDER BY indexed_at DESC LIMIT 100"):
            acc, reason, fecha, nombre, handle, subj, motivo, uri, texto = r
            # Solo se abre si es de verdad una PUBLICACIÓN: en filas antiguas (sin
            # subject_uri) el reasonSubject de un "me gusta vía repost" es el repost, y
            # el de un starter pack, el pack; pedir su hilo daba 400 NotFound.
            if not es_uri_de_post(subj):
                subj = uri_post_de_notificacion(reason or "", uri or "", motivo or "")
            items.append({
                "red": _("Bluesky"), "tipo": _tipos_bsky.get(reason, reason or ""),
                "de": nombre or handle or "", "texto": (texto or "")[:200],
                "fecha": fecha or "", "abrir": ("bluesky", acc, subj)})
    except Exception:
        pass
    # X: menciones cacheadas (buffer de menciones -> x_timeline_tweets con timeline
    # 'mentions_tl'; se une con x_tweets para el texto y el autor).
    try:
        for r in db.query(
                "SELECT t.account_id, t.created_at, t.author_name, t.author_screen_name, "
                "t.text, t.tweet_id FROM x_tweets t "
                "JOIN x_timeline_tweets tt ON tt.tweet_db_id = t.id "
                "WHERE tt.timeline IN ('mentions', 'mentions_tl') "
                "ORDER BY t.created_at DESC LIMIT 50"):
            acc, fecha, nombre, screen, texto, tid = r
            items.append({
                "red": _("X"), "tipo": _("Mención"), "de": nombre or screen or "",
                "texto": (texto or "")[:200], "fecha": fecha or "",
                "abrir": ("x", acc, tid)})
    except Exception:
        pass
    # Telegram: chats con mensajes sin leer.
    try:
        for r in db.query(
                "SELECT account_id, chat_id, title, unread_count, last_message, last_message_date "
                "FROM telegram_chats WHERE COALESCE(unread_count,0) > 0 "
                "ORDER BY last_message_date DESC LIMIT 100"):
            acc, chat_id, titulo, unread, ultimo, fecha = r
            items.append({
                "red": _("Telegram"), "tipo": _("{0} sin leer").format(unread),
                "de": titulo or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                "abrir": ("telegram", acc, chat_id, titulo)})
    except Exception:
        pass
    # W Social: notificaciones sin leer y conversaciones con mensajes pendientes.
    try:
        for r in db.query(
                "SELECT account_id, reason, author_name, author_handle, texto, indexed_at "
                "FROM wsocial_notifications WHERE COALESCE(is_read,0) = 0 "
                "ORDER BY indexed_at DESC LIMIT 100"):
            acc, motivo, autor, handle, texto, fecha = r
            from app.services.wsocial.wsocial_common import MOTIVOS_NOTIFICACION
            items.append({
                "red": _("W Social"),
                "tipo": MOTIVOS_NOTIFICACION.get(motivo, motivo),
                "de": autor or handle or "", "texto": (texto or "")[:200],
                "fecha": fecha or "", "abrir": ("wsocial", acc, "notifications", "")})
    except Exception:
        pass
    try:
        for r in db.query(
                "SELECT account_id, convo_id, title, unread_count, last_message, "
                "last_message_date FROM wsocial_convos WHERE COALESCE(unread_count,0) > 0 "
                "ORDER BY last_message_date DESC LIMIT 50"):
            acc, convo_id, titulo, unread, ultimo, fecha = r
            items.append({
                "red": _("W Social"), "tipo": _("{0} sin leer").format(unread),
                "de": titulo or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                "abrir": ("wsocial_convo", acc, convo_id, titulo)})
    except Exception:
        pass
    # Matrix: directos con no leídos e invitaciones pendientes.
    try:
        for r in db.query(
                "SELECT account_id, room_id, nombre, unread_count, last_message, last_message_date "
                "FROM matrix_salas WHERE es_dm = 1 AND COALESCE(unread_count,0) > 0 "
                "ORDER BY last_message_date DESC LIMIT 50"):
            acc, room_id, nombre, unread, ultimo, fecha = r
            items.append({
                "red": _("Matrix"), "tipo": _("{0} sin leer").format(unread),
                "de": nombre or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                "abrir": ("matrix", acc, room_id, nombre)})
        for r in db.query(
                "SELECT account_id, room_id, nombre FROM matrix_salas "
                "WHERE invitacion = 1 ORDER BY nombre LIMIT 50"):
            acc, room_id, nombre = r
            items.append({
                "red": _("Matrix"), "tipo": _("Invitación"),
                "de": nombre or "", "texto": _("Tienes una invitación pendiente."),
                "fecha": "", "abrir": ("matrix", acc, room_id, nombre)})
    except Exception:
        pass
    # IRC: menciones a tu apodo sin leer y privados pendientes.
    try:
        for r in db.query(
                "SELECT account_id, channel, nick, text, date FROM irc_messages "
                "WHERE highlight = 1 AND leido = 0 ORDER BY id DESC LIMIT 50"):
            acc, canal, nick, texto, fecha = r
            items.append({
                "red": _("IRC"), "tipo": _("Mención en {0}").format(canal),
                "de": nick or "", "texto": (texto or "")[:200], "fecha": fecha or "",
                "abrir": ("irc", acc, canal)})
        for r in db.query(
                "SELECT account_id, name, unread_count, last_message, last_message_date "
                "FROM irc_channels WHERE es_privado = 1 AND COALESCE(unread_count,0) > 0 "
                "ORDER BY last_message_date DESC LIMIT 50"):
            acc, nombre, unread, ultimo, fecha = r
            items.append({
                "red": _("IRC"), "tipo": _("{0} privados sin leer").format(unread),
                "de": nombre or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                "abrir": ("irc", acc, nombre)})
    except Exception:
        pass
    # XMPP (Jabber): contactos con mensajes sin leer, menciones en salas e invitaciones.
    try:
        for r in db.query(
                "SELECT account_id, jid, nombre, unread_count, last_message, last_message_date, tipo "
                "FROM xmpp_chats WHERE COALESCE(unread_count,0) > 0 AND COALESCE(solicitud,0) = 0 "
                "ORDER BY last_message_date DESC LIMIT 100"):
            acc, jid, nombre, unread, ultimo, fecha, tipo = r
            es_muc = (tipo == "muc")
            items.append({
                "red": _("XMPP"),
                "tipo": (_("{0} sin leer en la sala").format(unread) if es_muc
                         else _("{0} sin leer").format(unread)),
                "de": nombre or jid or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                "abrir": ("xmpp", acc, jid, nombre or jid, es_muc)})
        for r in db.query(
                "SELECT account_id, jid, nombre, tipo FROM xmpp_chats "
                "WHERE COALESCE(solicitud,0) = 1 ORDER BY nombre LIMIT 50"):
            acc, jid, nombre, tipo = r
            es_muc = (tipo == "muc")
            items.append({
                "red": _("XMPP"),
                "tipo": _("Invitación a sala") if es_muc else _("Solicitud de contacto"),
                "de": nombre or jid or "",
                "texto": (_("Tienes una invitación a una sala.") if es_muc
                          else _("Alguien quiere agregarte.")),
                "fecha": "", "abrir": ("xmpp", acc, jid, nombre or jid, es_muc)})
    except Exception:
        pass
    # Ordenar todo por fecha descendente (cadenas ISO comparables; las no ISO al final).
    items.sort(key=lambda x: str(x.get("fecha") or ""), reverse=True)
    return items
