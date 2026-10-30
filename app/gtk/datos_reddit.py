# -*- coding: utf-8 -*-
"""Datos de Reddit para la interfaz GTK: árbol de cuentas y buffers, listados y detalle.

Igual que con las demás redes: el servicio descarga y cachea en SQLite; aquí solo se LEE esa caché. Los buffers fijos, su
orden y los ocultos, los subreddits fijados y las búsquedas guardadas usan las mismas claves de configuración que la versión
de escritorio de Windows, así que las dos interfaces comparten el árbol. La lectura de cada elemento sale de las mismas
plantillas de lectura (Herramientas, plantillas) que ya tiene configuradas el usuario."""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import List

from app.core.config_manager import config, limite_render
from app.core.database import db
from app.core.plantillas import definicion, pulir_lectura
from .datos import Nodo, Fila, fecha_legible

log = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"

# (clave, título). El orden por defecto es el de la versión de escritorio.
BUFFERS = [
    ("home", "Página principal"), ("popular", "Populares"), ("all", "Todo (r/all)"),
    ("inbox", "Bandeja de entrada"), ("unread", "Mensajes sin leer"), ("mentions", "Menciones"),
    ("sent", "Mensajes enviados"), ("submitted", "Mis publicaciones"), ("comments", "Mis comentarios"),
    ("saved", "Guardados"), ("upvoted", "Votados positivos"), ("downvoted", "Votados negativos"),
    ("hidden", "Ocultos"), ("subreddits", "Mis subreddits"), ("multireddits", "Multireddits"),
    ("searches", "Búsquedas guardadas"),
]
TITULOS = dict(BUFFERS)
BUFFERS_BANDEJA = ("inbox", "unread", "mentions", "sent")
# Buffers "de lista" que no se pueden ordenar ni se descargan como publicaciones.
ESPECIALES = ("comments", "subreddits", "multireddits", "searches") + BUFFERS_BANDEJA
_POSTS_FIJOS = ("home", "popular", "all", "submitted", "saved", "upvoted", "downvoted", "hidden")
ORDENES = [("hot", "Populares"), ("new", "Nuevos"), ("top", "Los mejores"), ("rising", "En auge"),
           ("controversial", "Controvertidos")]
_PREFIJOS_POSTS = ("sub:", "user:", "home:", "popular:", "all:", "search:", "multi:")
# Tipos de mensaje de la bandeja.
TIPOS_MENSAJE = {"message": "Mensaje privado", "comment_reply": "Respuesta a comentario",
                 "post_reply": "Respuesta a publicación", "username_mention": "Mención", "sent": "Enviado"}
_DONDE_BANDEJA = {"inbox": "msg_type != 'sent'", "unread": "new = 1 AND msg_type != 'sent'",
                  "mentions": "msg_type = 'username_mention'", "sent": "msg_type = 'sent'"}

_COLS_POST = ("p.id, p.subreddit, p.author, p.title, p.selftext, p.url, p.permalink, p.created_utc, p.score, "
              "p.num_comments, p.over_18, p.spoiler, p.stickied, p.locked, p.saved, p.likes, p.link_flair_text, "
              "p.is_self, p.is_gallery, p.media_json, p.distinguished, p.domain, p.hidden")


def es_posts(timeline: str) -> bool:
    return timeline in _POSTS_FIJOS or str(timeline).startswith(_PREFIJOS_POSTS)


def es_bandeja(timeline: str) -> bool:
    return timeline in BUFFERS_BANDEJA


def es_cronologico(timeline: str) -> bool:
    """Admite "cargar anteriores": publicaciones (por el cursor `after`) y Mis comentarios."""
    return es_posts(timeline) or timeline == "comments"


def disponible() -> bool:
    """Punto único de decisión: interruptor del servicio en Preferencias y librería (praw) instalada."""
    try:
        from app.core.feature_flags import servicio_habilitado
        return bool(servicio_habilitado("reddit"))
    except Exception:
        return False


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'reddit' AND active = 1 ORDER BY position, id")]


def url_permalink(permalink: str) -> str:
    return (REDDIT_BASE + permalink) if permalink else ""


def _condensar(texto: str, limite: int = 280) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


def fecha_iso(created_utc) -> str:
    """El epoch UTC de Reddit como ISO-8601 (lo que entiende fecha_legible)."""
    try:
        return datetime.fromtimestamp(float(created_utc or 0), timezone.utc).isoformat() if created_utc else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------------------------- árbol
def _nodo(account_id: int, timeline: str, titulo: str, cerrable: bool = False) -> Nodo:
    return Nodo(titulo, "timeline", servicio="reddit", account_id=account_id, timeline=timeline, cerrable=cerrable)


def buffers_extra(account_id: int) -> List[dict]:
    """Buffers que abre la interfaz GTK y la de Windows no guarda (multireddits y perfiles de usuario)."""
    return [c for c in (config.get("reddit", "gtk_buffers", []) or [])
            if isinstance(c, dict) and c.get("account_id") in (None, account_id)]


def construir_nodos() -> Nodo:
    raiz = Nodo(_("Reddit"), "servicio", servicio="reddit")
    orden = list(config.get("reddit", "buffer_order", [k for k, _t in BUFFERS]) or [])
    orden += [k for k, _t in BUFFERS if k not in orden]
    ocultos = set(config.get("reddit", "hidden_buffers", []) or [])
    for acc_id, nombre in cuentas():
        cuenta = Nodo(nombre, "cuenta", servicio="reddit", account_id=acc_id)
        for clave in orden:
            if clave in TITULOS and clave not in ocultos:
                cuenta.hijos.append(_nodo(acc_id, clave, _(TITULOS[clave])))
        for sub in config.get("reddit", "saved_subreddits", []) or []:
            nombre_sub = sub.get("name", "") if isinstance(sub, dict) else str(sub)
            if nombre_sub:
                cuenta.hijos.append(_nodo(acc_id, "sub:" + nombre_sub, _("r/{0}").format(nombre_sub), True))
        if "searches" not in ocultos:
            for termino in config.get("reddit", "saved_searches", []) or []:
                cuenta.hijos.append(_nodo(acc_id, "search:" + termino, _("Búsqueda: {0}").format(termino), True))
        for c in buffers_extra(acc_id):
            cuenta.hijos.append(_nodo(acc_id, c["timeline"], c.get("titulo") or c["timeline"], True))
        raiz.hijos.append(cuenta)
    return raiz


def titulo_buffer(timeline: str, extra: str = "") -> str:
    if timeline.startswith("sub:"):
        return _("r/{0}").format(timeline.split(":")[1])
    if timeline.startswith("search:"):
        return _("Búsqueda: {0}").format(timeline.split(":", 1)[1])
    if timeline.startswith("user:"):
        return _("Publicaciones de u/{0}").format(timeline.split(":", 1)[1])
    if timeline.startswith("multi:"):
        return _("Multireddit: {0}").format(extra or timeline.split(":", 2)[-1])
    return _(TITULOS.get(timeline, timeline))


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    """Persiste el buffer que se abre (si hace falta) y devuelve su nodo; el árbol se reconstruye desde lo guardado."""
    if timeline.startswith("sub:") and timeline.count(":") == 1:
        nombre = timeline.split(":")[1]
        subs = list(config.get("reddit", "saved_subreddits", []) or [])
        if not any((s.get("name") if isinstance(s, dict) else s) == nombre for s in subs):
            subs.append({"name": nombre})
            config.set("reddit", "saved_subreddits", subs)
        titulo = _("r/{0}").format(nombre)
    elif timeline.startswith("search:"):
        termino = timeline.split(":", 1)[1]
        busquedas = list(config.get("reddit", "saved_searches", []) or [])
        if termino not in busquedas:
            busquedas.append(termino)
            config.set("reddit", "saved_searches", busquedas)
        titulo = _("Búsqueda: {0}").format(termino)
    else:
        guardados = list(config.get("reddit", "gtk_buffers", []) or [])
        if not any(isinstance(c, dict) and c.get("timeline") == timeline and c.get("account_id") in (None, account_id)
                   for c in guardados):
            guardados.append({"account_id": account_id, "timeline": timeline, "titulo": titulo})
            config.set("reddit", "gtk_buffers", guardados)
    return _nodo(account_id, timeline, titulo, True)


def cerrar_extra(nodo: Nodo):
    """Quita de lo guardado el buffer que el usuario cierra."""
    tl, acc = nodo.timeline, nodo.account_id
    if tl.startswith("sub:") and tl.count(":") == 1:
        nombre = tl.split(":")[1]
        config.set("reddit", "saved_subreddits", [s for s in (config.get("reddit", "saved_subreddits", []) or [])
                                                  if (s.get("name") if isinstance(s, dict) else s) != nombre])
    elif tl.startswith("search:"):
        termino = tl.split(":", 1)[1]
        config.set("reddit", "saved_searches", [s for s in (config.get("reddit", "saved_searches", []) or [])
                                                if s != termino])
    else:
        config.set("reddit", "gtk_buffers", [c for c in (config.get("reddit", "gtk_buffers", []) or [])
                                             if not (isinstance(c, dict) and c.get("timeline") == tl
                                                     and c.get("account_id") in (None, acc))])
    if es_posts(tl):
        db.execute("DELETE FROM reddit_timeline_posts WHERE account_id = ? AND timeline = ?", (acc, tl))


# ---------------------------------------------------------------------------------------------- listados
def _indicadores(saved, likes, over_18, spoiler, stickied, locked, distinguished) -> str:
    ind = []
    if likes == 1:
        ind.append(_("Voto positivo"))
    elif likes == 0:
        ind.append(_("Voto negativo"))
    if saved:
        ind.append(_("Guardado"))
    if over_18:
        ind.append(_("+18"))
    if spoiler:
        ind.append(_("Spoiler"))
    if stickied:
        ind.append(_("Fijado"))
    if locked:
        ind.append(_("Bloqueado"))
    if distinguished:
        ind.append(_("Moderador") if distinguished == "moderator" else _("Destacado"))
    return " ".join(ind)


def describir_media(media_json: str) -> str:
    partes = []
    try:
        for a in (json.loads(media_json) if media_json else []):
            tp = (a.get("type") or "").lower()
            if tp == "gallery":
                partes.append(_("Galería de {0} imágenes").format(a.get("count", 0)))
            elif tp == "video":
                partes.append(_("Vídeo"))
            elif tp == "image":
                partes.append(_("Imagen"))
    except Exception:
        pass
    return ". ".join(partes)


def _plantilla(clave: str) -> str:
    return config.get("accessibility", clave, "") or definicion(clave)["default"]


def _yo(account_id: int) -> str:
    fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
    return ((fila[0] if fila else "") or "").lower().removeprefix("u/")


def _fila_post(r, plantilla: str, yo: str, account_id: int) -> Fila:
    (db_id, subreddit, autor, titulo, selftext, url, permalink, creado, score, ncom, over_18, spoiler, stickied,
     locked, saved, likes, flair, es_self, es_galeria, media, distinguished, domain, oculto) = r
    cuerpo = (selftext or "").replace("\n", " ").strip() if es_self else (
        _("Enlace a {0}").format(domain) if domain else (url or ""))
    fecha = fecha_iso(creado)
    indicadores = _indicadores(saved, likes, over_18, spoiler, stickied, locked, distinguished)
    valores = {"subreddit": _("r/{0}").format(subreddit) if subreddit else "",
               "author": _("u/{0}").format(autor) if autor else "", "title": titulo or "", "content": cuerpo,
               "date": fecha_legible(fecha), "score": str(score or 0), "comments": str(ncom or 0),
               "indicators": indicadores, "media": describir_media(media), "flair": flair or "", "language": ""}
    lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
    return Fila(id=db_id, feed_id=0, feed_titulo=valores["subreddit"], titulo=lectura, autor=autor or "", fecha=fecha,
                leido=True, favorito=likes == 1, mas_tarde=False, audio=False, enlace=url_permalink(permalink),
                servicio="reddit",
                extra={"tipo": "post", "account_id": account_id, "fullname": db_id.split(":", 1)[1],
                       "usuario": autor or "", "subreddit": subreddit or "", "voto": likes, "marcador": bool(saved),
                       "guardado": bool(saved), "oculto": bool(oculto), "nsfw": bool(over_18), "spoiler": bool(spoiler),
                       "es_texto": bool(es_self), "url": url or "", "n_comentarios": ncom or 0,
                       "propio": bool(autor) and autor.lower() == yo, "titulo_post": titulo or ""})


def _filtrar(filas_rows, funcion, servicio: str = "reddit"):
    from app.core.content_filters import ContentFilterEngine
    filtros = ContentFilterEngine.cargar(servicio)
    for r in filas_rows:
        f, contenido, texto = funcion(r)
        if filtros and ContentFilterEngine.evaluar(servicio, contenido, texto, filtros)["hide"]:
            continue
        yield f


def listar(account_id: int, timeline: str, limite: int = None) -> List[Fila]:
    if timeline == "comments":
        return _listar_comentarios(account_id, limite)
    if timeline in BUFFERS_BANDEJA:
        return _listar_mensajes(account_id, timeline, limite)
    if timeline == "subreddits":
        return _listar_subreddits(account_id)
    if timeline == "multireddits":
        return _listar_multis(account_id)
    if timeline == "searches":
        return _listar_busquedas(account_id)
    limite = limite or limite_render("reddit", "max_items_buffer")
    plantilla, yo = _plantilla("reddit_post"), _yo(account_id)
    try:
        rows = db.query(f"SELECT {_COLS_POST} FROM reddit_posts p JOIN reddit_timeline_posts t ON p.id = t.post_db_id "
                        "WHERE t.account_id = ? AND t.timeline = ? ORDER BY t.position ASC LIMIT ?",
                        (account_id, timeline, limite))
    except Exception as e:
        log.error(f"Error al listar el buffer '{timeline}' de Reddit: {e}")
        return []

    def _una(r):
        return _fila_post(r, plantilla, yo, account_id), f"{r[3] or ''}. {r[4] or ''}", f"{r[1] or ''} {r[2] or ''} {r[3] or ''}"
    return list(_filtrar(rows, _una))


_COLS_COMENTARIO = "c.id, c.subreddit, c.author, c.body, c.created_utc, c.score, c.saved, c.likes, c.is_submitter, " \
                   "c.stickied, c.distinguished, c.permalink, c.link_id"


def _fila_comentario(r, plantilla: str, yo: str, account_id: int) -> Fila:
    (db_id, subreddit, autor, cuerpo, creado, score, saved, likes, es_autor, stickied, distinguished, permalink,
     link_id) = r
    ind = []
    if likes == 1:
        ind.append(_("Voto positivo"))
    elif likes == 0:
        ind.append(_("Voto negativo"))
    if saved:
        ind.append(_("Guardado"))
    if es_autor:
        ind.append(_("Autor original"))
    if stickied:
        ind.append(_("Fijado"))
    if distinguished:
        ind.append(_("Moderador") if distinguished == "moderator" else _("Destacado"))
    fecha = fecha_iso(creado)
    valores = {"author": _("u/{0}").format(autor) if autor else "", "body": (cuerpo or "").replace("\n", " ").strip(),
               "date": fecha_legible(fecha), "score": str(score or 0), "indicators": " ".join(ind),
               "subreddit": _("r/{0}").format(subreddit) if subreddit else ""}
    lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
    return Fila(id=db_id, feed_id=0, feed_titulo=valores["subreddit"], titulo=lectura, autor=autor or "", fecha=fecha,
                leido=True, favorito=likes == 1, mas_tarde=False, audio=False, enlace=url_permalink(permalink),
                servicio="reddit",
                extra={"tipo": "comentario", "account_id": account_id, "fullname": db_id.split(":", 1)[1],
                       "usuario": autor or "", "subreddit": subreddit or "", "voto": likes, "marcador": bool(saved),
                       "guardado": bool(saved), "link_id": link_id or "",
                       "propio": bool(autor) and autor.lower() == yo})


def _listar_comentarios(account_id: int, limite) -> List[Fila]:
    plantilla, yo = _plantilla("reddit_comment"), _yo(account_id)
    try:
        rows = db.query(f"SELECT {_COLS_COMENTARIO} FROM reddit_comments c JOIN reddit_timeline_comments t "
                        "ON c.id = t.comment_db_id WHERE t.account_id = ? AND t.timeline = 'comments' "
                        "ORDER BY t.position ASC LIMIT ?",
                        (account_id, limite or limite_render("reddit", "max_items_buffer")))
    except Exception as e:
        log.error(f"Error al listar los comentarios de Reddit: {e}")
        return []
    return [_fila_comentario(r, plantilla, yo, account_id) for r in rows]


def _listar_mensajes(account_id: int, timeline: str, limite) -> List[Fila]:
    plantilla = _plantilla("reddit_message")
    try:
        rows = db.query("SELECT id, msg_type, author, subject, body, created_utc, new, fullname, context "
                        f"FROM reddit_messages WHERE account_id = ? AND {_DONDE_BANDEJA[timeline]} "
                        "ORDER BY created_utc DESC LIMIT ?",
                        (account_id, limite or limite_render("reddit", "max_notifications")))
    except Exception as e:
        log.error(f"Error al listar la bandeja de Reddit: {e}")
        return []
    filas = []
    for db_id, tipo, autor, asunto, cuerpo, creado, nuevo, fullname, contexto in rows:
        fecha = fecha_iso(creado)
        etiqueta = _(TIPOS_MENSAJE[tipo]) if tipo in TIPOS_MENSAJE else (tipo or "")
        valores = {"type": (_("(nuevo) ") if nuevo else "") + etiqueta,
                   "author": _("u/{0}").format(autor) if autor else "", "subject": asunto or "",
                   "content": (cuerpo or "").replace("\n", " ").strip(), "date": fecha_legible(fecha)}
        lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
        filas.append(Fila(id=db_id, feed_id=0, feed_titulo=valores["author"], titulo=lectura, autor=autor or "",
                          fecha=fecha, leido=not nuevo, favorito=False, mas_tarde=False, audio=False,
                          enlace=url_permalink(contexto or ""), servicio="reddit",
                          extra={"tipo": "mensaje", "account_id": account_id, "fullname": fullname or "",
                                 "usuario": autor or "", "tipo_mensaje": tipo or "", "nuevo": bool(nuevo),
                                 "contexto": contexto or "", "asunto": asunto or "", "propio": tipo == "sent"}))
    return filas


def _listar_subreddits(account_id: int) -> List[Fila]:
    plantilla = _plantilla("reddit_subreddit")
    try:
        rows = db.query("SELECT id, name, title, subscribers, public_description FROM reddit_subreddits "
                        "WHERE account_id = ? AND user_is_subscriber = 1 ORDER BY name COLLATE NOCASE", (account_id,))
    except Exception as e:
        log.error(f"Error al listar los subreddits de Reddit: {e}")
        return []
    filas = []
    for db_id, nombre, titulo, suscriptores, descripcion in rows:
        valores = {"subreddit": _("r/{0}").format(nombre), "title": titulo or "", "subscribers": str(suscriptores or 0),
                   "description": (descripcion or "").replace("\n", " ").strip()}
        filas.append(Fila(id=db_id, feed_id=0, feed_titulo=valores["subreddit"],
                          titulo=pulir_lectura(plantilla.format_map(defaultdict(str, valores))), autor="", fecha="",
                          leido=True, favorito=False, mas_tarde=False, audio=False,
                          enlace=f"{REDDIT_BASE}/r/{nombre}", servicio="reddit",
                          extra={"tipo": "subreddit", "account_id": account_id, "subreddit": nombre,
                                 "descripcion": descripcion or "", "titulo_sub": titulo or "",
                                 "suscriptores": suscriptores or 0, "propio": False}))
    return filas


def _listar_multis(account_id: int) -> List[Fila]:
    filas = []
    for m in config.get("reddit", "multireddits_cache", []) or []:
        if isinstance(m, dict):
            visible = m.get("display") or m.get("name", "")
            owner, interno = m.get("owner", ""), m.get("name", "")
            clave = f"multi:{owner}:{interno}" if owner and interno else ""
            enlace = REDDIT_BASE + (m.get("path") or "")
        else:
            visible, clave, enlace = str(m), "", ""
        filas.append(Fila(id=clave or visible, feed_id=0, feed_titulo=visible,
                          titulo=_("Multireddit: {0}").format(visible), autor="", fecha="", leido=True, favorito=False,
                          mas_tarde=False, audio=False, enlace=enlace, servicio="reddit",
                          extra={"tipo": "multi", "account_id": account_id, "clave": clave, "nombre": visible,
                                 "propio": False}))
    return filas


def _listar_busquedas(account_id: int) -> List[Fila]:
    return [Fila(id=f"busqueda:{t}", feed_id=0, feed_titulo=t, titulo=_("Búsqueda: {0}").format(t), autor="", fecha="",
                 leido=True, favorito=False, mas_tarde=False, audio=False, enlace="", servicio="reddit",
                 extra={"tipo": "busqueda", "account_id": account_id, "termino": t, "propio": False})
            for t in config.get("reddit", "saved_searches", []) or []]


# ---------------------------------------------------------------------------------------------- detalle
def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de Reddit para el panel de lectura."""
    x = fila.extra
    tipo = x.get("tipo")
    if tipo == "mensaje":
        cabecera = [_("De u/{0}").format(fila.autor)] if fila.autor else []
        cuerpo = db.query_one("SELECT COALESCE(body, '') FROM reddit_messages WHERE id = ?", (fila.id,))
        return (x.get("asunto") or _(TIPOS_MENSAJE.get(x.get("tipo_mensaje"), "Mensaje")),
                " · ".join(cabecera + [fecha_legible(fila.fecha)]), fila.enlace, cuerpo[0] if cuerpo else "")
    if tipo == "subreddit":
        return (_("r/{0}").format(x["subreddit"]), _("{0} suscriptores").format(x.get("suscriptores", 0)), fila.enlace,
                "\n".join(p for p in (x.get("titulo_sub"), x.get("descripcion")) if p))
    if tipo in ("multi", "busqueda"):
        return (fila.titulo, "", fila.enlace, fila.titulo)
    if tipo == "comentario":
        r = db.query_one("SELECT subreddit, author, body, score FROM reddit_comments WHERE id = ?", (fila.id,))
        if not r:
            return (fila.titulo, "", fila.enlace, "")
        sub, autor, cuerpo, score = r
        meta = " · ".join(p for p in (_("r/{0}").format(sub) if sub else "", _("Por u/{0}").format(autor) if autor else "",
                                      fecha_legible(fila.fecha)) if p)
        return (_("Comentario de u/{0}").format(autor or "?"), meta, fila.enlace,
                (cuerpo or "") + "\n\n" + _("{0} puntos").format(score or 0))
    r = db.query_one("SELECT subreddit, author, title, selftext, url, score, num_comments, is_self, link_flair_text, "
                     "media_json, upvote_ratio, created_utc FROM reddit_posts WHERE id = ?", (fila.id,))
    if not r:
        return (fila.titulo, "", fila.enlace, "")
    sub, autor, titulo, selftext, url, score, ncom, es_self, flair, media, ratio, creado = r
    cab = _("r/{0}").format(sub) if sub else ""
    if flair:
        cab += f" [{flair}]"
    meta = " · ".join(p for p in (cab, _("Por u/{0}").format(autor) if autor else "", fecha_legible(fecha_iso(creado))) if p)
    partes = []
    if es_self and selftext:
        partes.append(selftext)
    elif url and not es_self:
        partes.append(_("Enlace: {0}").format(url))
    descripcion = describir_media(media)
    if descripcion:
        partes.append("\n" + descripcion)
    enlaces = [e for e in re.findall(r"https?://[^\s\)\]]+", selftext or "") if e != url]
    if enlaces:
        partes.append("\n" + _("Enlaces:") + "\n" + "\n".join(enlaces))
    estado = [t for t, v in ((_("voto positivo"), x_voto(fila) == 1), (_("voto negativo"), x_voto(fila) == 0),
                             (_("guardada"), fila.extra.get("guardado")), (_("oculta"), fila.extra.get("oculto")),
                             (_("+18"), fila.extra.get("nsfw")), (_("spoiler"), fila.extra.get("spoiler"))) if v]
    resumen = _("{0} puntos · {1} comentarios").format(score or 0, ncom or 0)
    if ratio:
        resumen += " · " + _("{0}% de votos positivos").format(int(round(ratio * 100)))
    partes.append("\n" + resumen + (" · " + ", ".join(estado) if estado else ""))
    return (titulo or fila.titulo, meta, fila.enlace, "\n".join(partes))


def x_voto(fila: Fila):
    return fila.extra.get("voto")


def texto_plano(fila: Fila) -> str:
    """El texto de la fila tal cual, sin plantilla de lectura (para traducir o resumir)."""
    titulo, _meta, _enlace, cuerpo = detalle(fila)
    return (titulo + "\n\n" + cuerpo).strip()


def fullname_de(db_id: str) -> str:
    return db_id.split(":", 1)[1] if db_id and ":" in db_id else db_id
