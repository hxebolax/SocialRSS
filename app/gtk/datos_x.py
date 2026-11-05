# -*- coding: utf-8 -*-
"""Datos de X (Twitter) para la interfaz GTK: árbol de cuentas y buffers, listados y detalle.

Igual que con Mastodon y Bluesky: el servicio descarga y cachea en SQLite; aquí solo se LEE esa caché. Las búsquedas, las
cronologías de usuario, las listas, las conversaciones y las comunidades que se abren como buffer se guardan en las mismas
tablas y claves de configuración que usa la versión de escritorio de Windows, así que las dos interfaces comparten el árbol.

Ojo con el nombre: `x_` ya es el prefijo de XMPP en esta interfaz, por eso aquí todo es `tw_`/`datos_x`."""

import json
import logging
import re
from typing import List

from app.core.config_manager import config
from app.core.database import db
from app.core.at_lectura import texto_respuesta_a
# Textos accesibles de una publicación de X (módulo sin wx, compartido con la interfaz de Windows).
from app.ui.x_lectura import (datos_encuesta_x, describir_media_x, texto_cita_x, texto_encuesta_x)
from .datos import Nodo, Fila
from .filtros_contenido import sin_ocultas
from .lectura import con_plantilla, notificacion

log = logging.getLogger(__name__)

# (clave del servicio, título). Las claves son las de x_service.obtener_timeline_async.
BUFFERS = [
    ("home", "Inicio (Para ti)"),
    ("following", "Siguiendo"),
    ("mentions", "Menciones"),
    ("notifications", "Notificaciones"),
    ("bookmarks", "Marcadores"),
    ("followers", "Seguidores"),
    ("verified_followers", "Seguidores verificados"),
    ("friends", "Cuentas que sigo"),
    ("communities", "Comunidades"),
]
ACTORES = ("followers", "verified_followers", "friends")
# Buffers de publicaciones que se descargan con obtener_timeline_async y admiten "cargar anteriores".
CRONOLOGICOS = ("home", "following", "mentions", "communities")
PREFIJOS_CRONOLOGICOS = ("search:", "list:", "user:", "thread:", "community:")

TIPOS_NOTIFICACION = {
    "like": "ha dado me gusta a tu publicación", "favorite": "ha dado me gusta a tu publicación",
    "retweet": "ha hecho retweet de tu publicación", "follow": "te ha seguido", "mention": "te ha mencionado",
    "reply": "ha respondido a tu publicación", "quote": "ha citado tu publicación", "recommendation": "te recomienda algo",
}
MODOS_CRONOLOGIA = [("tweets", "Publicaciones"), ("replies", "Publicaciones y respuestas"),
                    ("media", "Solo multimedia"), ("likes", "Lo que le ha gustado")]


def es_cronologico(timeline: str) -> bool:
    return timeline in CRONOLOGICOS or str(timeline).startswith(PREFIJOS_CRONOLOGICOS)


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'x' AND active = 1 ORDER BY position, id")]


def disponible() -> bool:
    """Punto único de decisión de la app: interruptor de código, preferencia del usuario y librería instalada."""
    try:
        from app.core.feature_flags import x_cliente_disponible
        return bool(x_cliente_disponible())
    except Exception:
        return False


def _condensar(texto: str, limite: int = 280) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


def url_tweet(screen: str, tweet_id: str) -> str:
    return f"https://x.com/{screen or 'i'}/status/{tweet_id}" if tweet_id else ""


# ---------------------------------------------------------------------------------------------- árbol
def _guardados(clave: str, account_id: int) -> List[dict]:
    return [c for c in (config.get("x", clave, []) or [])
            if isinstance(c, dict) and c.get("account_id") in (None, account_id)]


def _nodo(account_id: int, timeline: str, titulo: str, cerrable: bool = False) -> Nodo:
    return Nodo(titulo, "timeline", servicio="x", account_id=account_id, timeline=timeline, cerrable=cerrable)


def construir_nodos() -> Nodo:
    raiz = Nodo(_("X (Twitter)"), "servicio", servicio="x")
    ocultos = set(config.get("x", "hidden_buffers", []) or [])
    for acc_id, nombre in cuentas():
        cuenta = Nodo(nombre, "cuenta", servicio="x", account_id=acc_id)
        for clave, titulo in BUFFERS:
            if clave not in ocultos:
                cuenta.hijos.append(_nodo(acc_id, clave, _(titulo)))
        if "lists" not in ocultos:
            for lid, lnombre in db.query("SELECT list_id, name FROM x_lists WHERE account_id = ? ORDER BY position",
                                         (acc_id,)):
                cuenta.hijos.append(_nodo(acc_id, f"list:{lid}", _("Lista: {n}").format(n=lnombre or lid)))
        if "searches" not in ocultos:
            for nombre_b, q in db.query("SELECT name, query FROM x_searches WHERE account_id = ? ORDER BY position, id",
                                        (acc_id,)):
                cuenta.hijos.append(_nodo(acc_id, f"search:{q}", _("Búsqueda: {n}").format(n=nombre_b or q), True))
        if "user_timelines" not in ocultos:
            for uid, screen, nombre_u, modo in db.query(
                    "SELECT user_id, screen_name, name, COALESCE(modo, 'tweets') FROM x_user_timelines "
                    "WHERE account_id = ? ORDER BY position, id", (acc_id,)):
                cuenta.hijos.append(_nodo(acc_id, _clave_cronologia(uid, modo),
                                          _titulo_cronologia(screen or nombre_u or uid, modo), True))
        for clave, prefijo, plantilla in (("thread_buffers", "thread:", _("Conversación: {n}")),
                                          ("community_buffers", "community:", _("Comunidad: {n}")),
                                          ("dm_buffers", "dm:", _("Mensajes con {n}"))):
            if clave in ocultos:
                continue
            for c in _guardados(clave, acc_id):
                cuenta.hijos.append(_nodo(acc_id, prefijo + str(c.get("id")),
                                          plantilla.format(n=c.get("display") or c.get("id")), True))
        raiz.hijos.append(cuenta)
    return raiz


def _clave_cronologia(uid: str, modo: str) -> str:
    return f"user:{uid}" + ("" if modo in ("", "tweets") else f":{modo}")


def _titulo_cronologia(screen: str, modo: str) -> str:
    sufijo = {"replies": _(" (con respuestas)"), "media": _(" (solo multimedia)"), "likes": _(" (sus me gusta)")}.get(modo, "")
    return _("Cronología: @{u}{s}").format(u=str(screen).lstrip("@"), s=sufijo)


def guardar_cronologia(account_id: int, uid: str, screen: str, nombre: str, modo: str = "tweets") -> str:
    """Guarda (si falta) la cronología de un usuario y devuelve su clave de buffer."""
    modo = modo or "tweets"
    if not db.query_one("SELECT 1 FROM x_user_timelines WHERE account_id = ? AND user_id = ? "
                        "AND COALESCE(modo, 'tweets') = ?", (account_id, str(uid), modo)):
        db.execute_insert("INSERT INTO x_user_timelines (account_id, user_id, screen_name, name, modo, position) "
                          "VALUES (?, ?, ?, ?, ?, 0)", (account_id, str(uid), screen, nombre, modo))
    return _clave_cronologia(uid, modo)


def titulo_cronologia(account_id: int, uid: str, modo: str) -> str:
    fila = db.query_one("SELECT screen_name, name FROM x_user_timelines WHERE account_id = ? AND user_id = ? "
                        "AND COALESCE(modo, 'tweets') = ?", (account_id, str(uid), modo or "tweets"))
    return _titulo_cronologia((fila[0] or fila[1]) if fila else uid, modo)


def dm_guardados(account_id: int) -> List[dict]:
    return _guardados("dm_buffers", account_id)


def _guardar_buffer_config(clave: str, account_id: int, ident: str, display: str):
    guardadas = list(config.get("x", clave, []) or [])
    if not any(isinstance(c, dict) and str(c.get("id")) == ident and c.get("account_id") in (None, account_id)
               for c in guardadas):
        guardadas.append({"id": ident, "account_id": account_id, "display": (display or ident).replace("\n", " ")[:50]})
        config.set("x", clave, guardadas)


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    """Persiste el buffer que se abre (si hace falta) y devuelve su nodo; el árbol se reconstruye desde lo guardado."""
    if timeline.startswith("search:"):
        q = timeline.split(":", 1)[1]
        if not db.query_one("SELECT 1 FROM x_searches WHERE account_id = ? AND query = ?", (account_id, q)):
            db.execute_insert("INSERT INTO x_searches (account_id, name, query, position) VALUES (?, ?, ?, 0)",
                              (account_id, q[:40], q))
        titulo = _("Búsqueda: {n}").format(n=q[:40])
    elif timeline.startswith("thread:"):
        _guardar_buffer_config("thread_buffers", account_id, timeline.split(":", 1)[1], titulo)
        titulo = _("Conversación: {n}").format(n=titulo[:50])
    elif timeline.startswith("community:"):
        _guardar_buffer_config("community_buffers", account_id, timeline.split(":", 1)[1], titulo)
        titulo = _("Comunidad: {n}").format(n=titulo)
    elif timeline.startswith("dm:"):
        _guardar_buffer_config("dm_buffers", account_id, timeline.split(":", 1)[1], titulo)
        titulo = _("Mensajes con {n}").format(n=titulo)
    return _nodo(account_id, timeline, titulo, not timeline.startswith("list:"))


def cerrar_extra(nodo: Nodo):
    """Quita de lo guardado el buffer que el usuario cierra."""
    acc, tl = nodo.account_id, nodo.timeline
    if tl.startswith("search:"):
        db.execute("DELETE FROM x_searches WHERE account_id = ? AND query = ?", (acc, tl.split(":", 1)[1]))
    elif tl.startswith("user:"):
        uid, _s, modo = tl.split(":", 1)[1].partition(":")
        db.execute("DELETE FROM x_user_timelines WHERE account_id = ? AND user_id = ? AND COALESCE(modo, 'tweets') = ?",
                   (acc, uid, modo or "tweets"))
    elif tl.startswith(("thread:", "community:", "dm:")):
        prefijo, _s, ident = tl.partition(":")
        clave = {"thread": "thread_buffers", "community": "community_buffers", "dm": "dm_buffers"}[prefijo]
        config.set("x", clave, [c for c in (config.get("x", clave, []) or [])
                                if not (isinstance(c, dict) and str(c.get("id")) == ident
                                        and c.get("account_id") in (None, acc))])
        if prefijo == "thread":
            db.execute("DELETE FROM x_timeline_tweets WHERE account_id = ? AND timeline = ?", (acc, tl))


# ---------------------------------------------------------------------------------------------- listados
_COLS = ("p.id, p.tweet_id, COALESCE(p.author_id, ''), COALESCE(p.author_name, ''), COALESCE(p.author_screen_name, ''), "
         "COALESCE(p.text, ''), COALESCE(p.created_at, ''), COALESCE(p.favorite_count, 0), "
         "COALESCE(p.retweet_count, 0), COALESCE(p.reply_count, 0), COALESCE(p.quote_count, 0), "
         "COALESCE(p.retweeted_by, ''), COALESCE(p.in_reply_to_screen_name, ''), COALESCE(p.in_reply_to, ''), "
         "COALESCE(p.quoted_author, ''), COALESCE(p.quoted_text, ''), COALESCE(p.poll_json, ''), "
         "COALESCE(p.media_json, ''), COALESCE(p.lang, ''), COALESCE(p.liked, 0), COALESCE(p.retweeted, 0), "
         "COALESCE(p.bookmarked, 0), COALESCE(p.is_own, 0), COALESCE(p.view_count, 0)")


def _yo(account_id: int) -> str:
    fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
    return ((fila[0] if fila else "") or "").lstrip("@").lower()


def _fila_post(r, yo: str, account_id: int) -> Fila:
    (dbid, tid, autor_id, nombre, screen, texto, creado, nlikes, nrts, nresp, ncitas, rt_por, resp_a, resp_id,
     cita_autor, cita_texto, encuesta, media, idioma, liked, retuiteado, guardado, propio, vistas) = r
    cuerpo = " ".join(texto.split())
    extras = [x for x in (texto_cita_x(cita_autor, cita_texto), texto_encuesta_x(encuesta)) if x]
    if extras:
        cuerpo = ". ".join(([cuerpo] if cuerpo else []) + extras)
    descripcion_media = describir_media_x(media)
    if not cuerpo.strip() and descripcion_media:
        cuerpo = descripcion_media
    quien = nombre or screen
    resumen = f"{quien} (@{screen}): {_condensar(cuerpo)}"
    try:
        adjuntos = len(json.loads(media)) if media else 0
    except Exception:
        adjuntos = 0
    fila = Fila(id=dbid, feed_id=0, feed_titulo=f"@{screen}", titulo=resumen, autor=quien, fecha=creado, leido=True,
                favorito=bool(liked), mas_tarde=False, audio=False, enlace=url_tweet(screen, tid), servicio="x",
                extra={"tipo": "estado", "account_id": account_id, "tweet_id": tid, "author_id": autor_id,
                       "usuario": screen, "impulsado": bool(retuiteado), "marcador": bool(guardado),
                       "impulsado_por": rt_por, "adjuntos": adjuntos, "encuesta": bool(texto_encuesta_x(encuesta)),
                       "respuestas": nresp, "n_impulsos": nrts + ncitas, "n_favoritos": nlikes,
                       "es_respuesta": bool(resp_id), "respuesta_a": texto_respuesta_a(resp_a, "", yo),
                       "propio": bool(propio) or screen.lower() == yo,
                       "indicadores_fijos": ([_("RT de @{0}").format(rt_por)] if rt_por else [])
                       + ([_("Respuesta")] if resp_id else []) + ([_("Cita")] if cita_autor else [])})
    return con_plantilla(fila, "x_post", {
        "author": quien, "username": f"@{screen}" if screen else "", "content": _condensar(cuerpo),
        "reply_to": texto_respuesta_a(resp_a, "", yo), "media": descripcion_media, "cw": "", "language": idioma or "",
        "counts": _("{0} me gusta, {1} RT, {2} resp.").format(nlikes or 0, nrts or 0, nresp or 0)})


def listar(account_id: int, timeline: str, limite: int = 200) -> List[Fila]:
    if timeline.startswith("dm:"):
        return _listar_mensajes(account_id, timeline[3:], limite)
    if timeline == "notifications":
        return _listar_notificaciones(account_id, limite)
    if timeline in ACTORES:
        return _listar_actores(account_id, timeline, limite)
    yo = _yo(account_id)
    # position conserva el orden del servidor (y, en una conversación, el orden de lectura).
    sql = (f"SELECT {_COLS} FROM x_tweets p JOIN x_timeline_tweets t ON p.id = t.tweet_db_id "
           "WHERE t.account_id = ? AND t.timeline = ? ORDER BY t.position LIMIT ?")
    try:
        filas = db.query(sql, (account_id, timeline, limite))
        if not timeline.startswith("thread:"):
            filas = sin_ocultas("x", filas, 5, 4)
        return [_fila_post(r, yo, account_id) for r in filas]
    except Exception as e:
        log.error(f"Error al listar el buffer '{timeline}' de X: {e}")
        return []


def _listar_notificaciones(account_id: int, limite: int) -> List[Fila]:
    try:
        rows = db.query("SELECT id, COALESCE(tipo, ''), COALESCE(actor_name, ''), COALESCE(actor_screen_name, ''), "
                        "COALESCE(created_at, ''), COALESCE(text, ''), COALESCE(tweet_id, '') FROM x_notifications "
                        "WHERE account_id = ? ORDER BY created_at DESC LIMIT ?", (account_id, limite))
    except Exception as e:
        log.error(f"Error al listar las notificaciones de X: {e}")
        return []
    filas = []
    for nid, tipo, nombre, screen, fecha, texto, tid in rows:
        quien = nombre or screen
        accion = _(TIPOS_NOTIFICACION[tipo]) if tipo in TIPOS_NOTIFICACION else (tipo or _("ha enviado una notificación"))
        linea = f"{quien} (@{screen}): {accion}"
        if texto:
            linea += ". " + _condensar(texto, 200)
        filas.append(notificacion(
            Fila(id=nid, feed_id=0, feed_titulo=f"@{screen}", titulo=linea, autor=quien, fecha=fecha,
                 leido=True, favorito=False, mas_tarde=False, audio=False,
                 enlace=url_tweet(screen, tid) if tid else f"https://x.com/{screen}", servicio="x",
                 extra={"tipo": "notificacion", "motivo": tipo, "account_id": account_id, "usuario": screen,
                        "tweet_id": tid, "propio": False}), tipo, quien, screen, _condensar(texto)))
    return filas


def _listar_actores(account_id: int, bucket: str, limite: int) -> List[Fila]:
    try:
        rows = db.query("SELECT user_id, COALESCE(screen_name, ''), COALESCE(name, ''), COALESCE(description, ''), "
                        "COALESCE(followers, 0), COALESCE(following, 0), COALESCE(is_following, 0) "
                        "FROM x_users WHERE account_id = ? AND bucket = ? ORDER BY position LIMIT ?",
                        (account_id, bucket, limite))
    except Exception as e:
        log.error(f"Error al listar cuentas de X: {e}")
        return []
    filas = []
    for uid, screen, nombre, bio, seguidores, siguiendo, sigo in rows:
        quien = nombre or screen
        linea = f"{quien} (@{screen})"
        if bio:
            linea += ": " + _condensar(bio, 200)
        filas.append(Fila(id=f"{account_id}:{uid}:{bucket}", feed_id=0, feed_titulo=f"@{screen}", titulo=linea,
                          autor=quien, fecha="", leido=True, favorito=False, mas_tarde=False, audio=False,
                          enlace=f"https://x.com/{screen}", servicio="x",
                          extra={"tipo": "usuario", "account_id": account_id, "author_id": uid, "usuario": screen,
                                 "bio": bio, "seguidores": seguidores, "siguiendo": siguiendo, "sigo": bool(sigo),
                                 "propio": False}))
    return filas


def _listar_mensajes(account_id: int, uid: str, limite: int) -> List[Fila]:
    from app.services.x import x_service
    mi_id = str(x_service._yo(account_id).get("id", "") or "")
    convo = x_service._convo_id_de(mi_id, uid)
    try:
        rows = db.query("SELECT message_id, COALESCE(sender_name, ''), COALESCE(text, ''), COALESCE(date, ''), "
                        "COALESCE(outgoing, 0) FROM x_messages WHERE account_id = ? AND convo_id = ? "
                        "ORDER BY date ASC LIMIT ?", (account_id, convo, limite))
    except Exception as e:
        log.error(f"Error al listar los mensajes directos de X: {e}")
        return []
    otro = next((str(c.get("display") or "") for c in dm_guardados(account_id) if str(c.get("id")) == str(uid)), "")
    filas = []
    for mid, quien, texto, fecha, saliente in rows:
        nombre = _("Yo") if saliente else (quien or otro)
        filas.append(Fila(id=mid, feed_id=0, feed_titulo=nombre, titulo=f"{nombre}: {_condensar(texto, 500)}",
                          autor=nombre, fecha=fecha, leido=True, favorito=False, mas_tarde=False, audio=False,
                          enlace="", servicio="x",
                          extra={"tipo": "mensaje", "account_id": account_id, "user_id": str(uid), "convo_id": convo,
                                 "message_id": mid, "usuario": "", "titulo": otro, "propio": bool(saliente),
                                 "texto": texto}))
    return filas


# ---------------------------------------------------------------------------------------------- detalle
def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de X para el panel de lectura."""
    from .datos import fecha_legible
    x = fila.extra
    if x.get("tipo") == "mensaje":
        return (fila.autor, fecha_legible(fila.fecha), "", x.get("texto") or fila.titulo)
    if x.get("tipo") == "usuario":
        cuerpo = [x.get("bio") or _("Sin biografía")]
        cuerpo.append(_("{s} seguidores · {g} siguiendo").format(s=x.get("seguidores", 0), g=x.get("siguiendo", 0)))
        return (fila.autor, f"@{x.get('usuario')}", fila.enlace, "\n".join(cuerpo))
    if x.get("tipo") == "notificacion":
        if x.get("tweet_id"):
            r = db.query_one(f"SELECT {_COLS} FROM x_tweets p WHERE p.account_id = ? AND p.tweet_id = ?",
                             (x["account_id"], x["tweet_id"]))
            if r:
                return detalle(_fila_post(r, _yo(x["account_id"]), x["account_id"]))
        return (fila.titulo, fecha_legible(fila.fecha), fila.enlace, fila.titulo)
    r = db.query_one(f"SELECT {_COLS} FROM x_tweets p WHERE p.id = ?", (fila.id,))
    if not r:
        return (fila.titulo, "", fila.enlace, "")
    (dbid, tid, autor_id, nombre, screen, texto, creado, nlikes, nrts, nresp, ncitas, rt_por, resp_a, resp_id,
     cita_autor, cita_texto, encuesta, media, idioma, liked, retuiteado, guardado, propio, vistas) = r
    meta = [f"{nombre or screen} (@{screen})", fecha_legible(creado)]
    partes = []
    if rt_por:
        partes.append(_("Retuiteado por @{u}").format(u=rt_por))
    respuesta = texto_respuesta_a(resp_a, "", _yo(x.get("account_id", 0)))
    if respuesta:
        partes.append(respuesta)
    partes.append(texto)
    for i, linea in enumerate((s for s in describir_media_x(media).split(". ") if s), 1):
        partes.append("\n" + _("Adjunto {i}: {d}").format(i=i, d=linea))
    cita = texto_cita_x(cita_autor, cita_texto)
    if cita:
        partes.append("\n" + cita)
    sondeo = texto_encuesta_x(encuesta)
    if sondeo:
        partes.append("\n" + sondeo)
    enlaces = re.findall(r"https?://[^\s\)\]]+", texto)
    if enlaces:
        partes.append("\n" + _("Enlaces:") + "\n" + "\n".join(enlaces))
    estado = [t for t, v in ((_("te gusta"), liked), (_("retuiteada"), retuiteado), (_("en marcadores"), guardado)) if v]
    partes.append("\n" + _("{r} respuestas · {t} retweets · {c} citas · {f} me gusta").format(
        r=nresp, t=nrts, c=ncitas, f=nlikes) + (f" · {int(vistas)} " + _("vistas") if vistas else "")
        + (" · " + ", ".join(estado) if estado else ""))
    return (nombre or screen, " · ".join(p for p in meta if p), fila.enlace, "\n".join(partes))


def menciones_de_respuesta(fila: Fila) -> str:
    """Texto inicial al responder: la mención del autor (X no enlaza al padre como Bluesky)."""
    u = fila.extra.get("usuario")
    return f"@{u} " if u else ""


def datos_encuesta(account_id: int, db_id: str):
    """La encuesta guardada del tuit como dict (con 'choices'), o None."""
    fila = db.query_one("SELECT COALESCE(poll_json, '') FROM x_tweets WHERE id = ?", (db_id,))
    return datos_encuesta_x(fila[0] if fila else "")
