# -*- coding: utf-8 -*-
"""Datos de W Social para la interfaz GTK: árbol de cuentas y buffers, listados y detalle.

W Social habla AT Protocol (los mismos lexicons que Bluesky) pero tiene sus propias tablas, cuentas y servicio.

Igual que con Mastodon: el servicio descarga y cachea en SQLite; aquí solo se LEE esa caché."""

import json
import logging
from typing import Dict, List, Optional

from app.core.database import db
from . import buffers_guardados
from .datos import Nodo, Fila, buffer_oculto
from .filtros_contenido import sin_ocultas
from .lectura import campos_post_at, con_plantilla, describir_adjuntos_at, notificacion

log = logging.getLogger(__name__)

# (clave del servicio, título). Las claves son las de wsocial_service.cargar_timeline_async.
BUFFERS = [
    ("home", "Inicio"),
    ("notifications", "Notificaciones"),
    ("mentions", "Menciones"),
    ("sent", "Mis publicaciones"),
    ("likes", "Me gusta"),
    ("bookmarks", "Marcadores"),
    ("dms", "Mensajes directos"),
]

EXTRA: Dict[int, List[Nodo]] = {}

MOTIVOS = {
    "like": "ha marcado como me gusta tu publicación",
    "repost": "ha republicado tu publicación",
    "follow": "te ha seguido",
    "mention": "te ha mencionado",
    "reply": "ha respondido a tu publicación",
    "quote": "ha citado tu publicación",
    "starterpack-joined": "se ha unido con tu paquete de inicio",
    "verified": "te ha verificado",
    "unverified": "ha retirado tu verificación",
    "like-via-repost": "ha marcado como me gusta una republicación tuya",
    "repost-via-repost": "ha republicado una republicación tuya",
    "subscribed-post": "ha publicado",
    "subscribed-reply": "ha respondido",
}


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'wsocial' AND active = 1 ORDER BY position, id")]


def construir_nodos() -> Nodo:
    raiz = Nodo(_("W Social"), "servicio", servicio="wsocial")
    for acc_id, nombre in cuentas():
        cuenta = Nodo(nombre, "cuenta", servicio="wsocial", account_id=acc_id)
        for clave, titulo in BUFFERS:
            if buffer_oculto("wsocial", clave):
                continue
            cuenta.hijos.append(Nodo(_(titulo), "timeline", servicio="wsocial", account_id=acc_id, timeline=clave))
        cuenta.hijos.extend(buffers_guardados.nodos("wsocial", acc_id))
        cuenta.hijos.extend(EXTRA.get(acc_id, []))
        raiz.hijos.append(cuenta)
    return raiz


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    """Crea (o recupera) un buffer abierto por el usuario; conversaciones, cronologías y búsquedas se guardan."""
    if buffers_guardados.guarda("wsocial", timeline):
        titulo = buffers_guardados.guardar("wsocial", account_id, timeline, titulo)
        return next(n for n in buffers_guardados.nodos("wsocial", account_id) if n.timeline == timeline)
    for n in EXTRA.setdefault(account_id, []):
        if n.timeline == timeline:
            return n
    n = Nodo(titulo, "timeline", servicio="wsocial", account_id=account_id, timeline=timeline, cerrable=True)
    EXTRA[account_id].append(n)
    return n


def cerrar_extra(nodo: Nodo):
    if buffers_guardados.guarda("wsocial", nodo.timeline):
        return buffers_guardados.quitar("wsocial", nodo.account_id, nodo.timeline)
    lista = EXTRA.get(nodo.account_id, [])
    if nodo in lista:
        lista.remove(nodo)


def _condensar(texto: str, limite: int = 240) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


def url_web(uri: str, handle: str) -> str:
    """W Social no tiene un formato de enlace web estable que se pueda dar por bueno: sin enlace."""
    return ""


def _yo(account_id: int) -> str:
    """Handle propio (de la cuenta) para reconocer tus publicaciones."""
    fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
    return ((fila[0] if fila else "") or "").lstrip("@").lower()


_COLS = ("p.id, p.uri, p.cid, p.author_did, COALESCE(p.author_handle, ''), COALESCE(p.author_name, ''), "
         "COALESCE(p.content, ''), COALESCE(p.created_at, ''), COALESCE(p.indexed_at, ''), COALESCE(p.labels, ''), "
         "COALESCE(p.like_count, 0), COALESCE(p.repost_count, 0), COALESCE(p.reply_count, 0), "
         "COALESCE(p.quote_count, 0), COALESCE(p.liked_uri, ''), COALESCE(p.reposted_uri, ''), "
         "COALESCE(p.bookmarked, 0), COALESCE(p.media_json, '[]'), COALESCE(p.repost_by_name, ''), "
         "COALESCE(p.reply_parent_uri, ''), COALESCE(p.quote_uri, '')")


def _fila_post(r, yo: str, account_id: int) -> Fila:
    (dbid, uri, cid, did, handle, nombre, contenido, creado, indexado, etiquetas, nlikes, nreposts, nresp,
     ncitas, liked, reposted, marcador, media, repost_por, padre, cita) = r
    try:
        adjuntos = len(json.loads(media or "[]"))
    except Exception:
        adjuntos = 0
    autor = nombre or handle
    resumen = f"{autor} (@{handle}): " + _condensar(contenido)
    fila = Fila(id=dbid, feed_id=0, feed_titulo=f"@{handle}", titulo=resumen, autor=autor,
                fecha=creado or indexado, leido=True, favorito=bool(liked), mas_tarde=False, audio=False,
                enlace=url_web(uri, handle), servicio="wsocial",
                extra={"tipo": "estado", "account_id": account_id, "uri": uri, "cid": cid, "did": did,
                       "usuario": handle, "impulsado": bool(reposted), "marcador": bool(marcador),
                       "impulsado_por": repost_por, "adjuntos": adjuntos, "respuestas": nresp,
                       "n_impulsos": nreposts + ncitas, "n_favoritos": nlikes, "etiquetas": etiquetas,
                       "es_respuesta": bool(padre), "propio": handle.lower() == yo, "cita": cita,
                       "indicadores_fijos": ([_("Respuesta")] if padre else []) + ([_("Cita")] if cita else [])})
    return con_plantilla(fila, "wsocial_post", campos_post_at(
        autor, handle, contenido, repost_por, describir_adjuntos_at(media, bool(cita)), etiquetas, nlikes, nreposts,
        nresp, ncitas, True))


def listar(account_id: int, timeline: str, limite: int = 200) -> List[Fila]:
    if timeline == "dms":
        return _listar_convos(account_id, limite)
    if timeline.startswith("dm:"):
        return _listar_mensajes(account_id, timeline[3:], limite)
    yo = _yo(account_id)
    if timeline == "notifications":
        return _listar_notificaciones(account_id, yo, solo_menciones=False, limite=limite)
    # position conserva el orden del servidor (0 = lo más nuevo en todos los buffers); en una conversación
    # el orden de lectura ya viene dado por position.
    sql = (f"SELECT {_COLS} FROM wsocial_timeline_posts t JOIN wsocial_posts p ON p.id = t.post_db_id "
           "WHERE t.account_id = ? AND t.timeline = ? "
           "ORDER BY t.position ASC LIMIT ?")
    params = (account_id, timeline, limite)
    try:
        filas = db.query(sql, params)
        if not timeline.startswith("thread:"):
            filas = sin_ocultas("wsocial", filas, 6, 4)
        return [_fila_post(r, yo, account_id) for r in filas]
    except Exception as e:
        log.error(f"Error al listar el buffer '{timeline}' de W Social: {e}")
        return []


def _listar_notificaciones(account_id: int, yo: str, solo_menciones: bool, limite: int) -> List[Fila]:
    where = "AND n.reason IN ('mention', 'reply', 'quote') " if solo_menciones else ""
    sql = ("SELECT n.id, n.reason, COALESCE(n.subject_uri, ''), COALESCE(n.texto, ''), "
           "COALESCE(n.author_handle, ''), COALESCE(n.author_name, ''), COALESCE(n.indexed_at, ''), n.is_read "
           f"FROM wsocial_notifications n WHERE n.account_id = ? {where}"
           "ORDER BY n.indexed_at DESC LIMIT ?")
    filas = []
    try:
        rows = db.query(sql, (account_id, limite))
    except Exception as e:
        log.error(f"Error al listar notificaciones de W Social: {e}")
        return []
    for nid, motivo, sujeto, contenido, handle, nombre, indexado, leida in rows:
        quien = nombre or handle
        accion = _(MOTIVOS.get(motivo, motivo))
        texto = f"{quien} (@{handle}) {accion}"
        if contenido:
            texto += ": " + _condensar(contenido, 200)
        f = Fila(id=nid, feed_id=0, feed_titulo=f"@{handle}", titulo=texto, autor=quien, fecha=indexado,
                 leido=bool(leida), favorito=False, mas_tarde=False, audio=False,
                 enlace=url_web(sujeto, "") if sujeto else "", servicio="wsocial",
                 extra={"tipo": "notificacion", "motivo": motivo, "account_id": account_id, "usuario": handle,
                        "uri": sujeto, "propio": False})
        filas.append(notificacion(f, motivo, quien, handle, _condensar(contenido)))
    return filas


def _listar_convos(account_id: int, limite: int) -> List[Fila]:
    filas = []
    try:
        rows = db.query("SELECT convo_id, COALESCE(title, ''), COALESCE(unread_count, 0), COALESCE(muted, 0), "
                        "COALESCE(last_message, ''), COALESCE(last_message_date, ''), COALESCE(is_group, 0) "
                        "FROM wsocial_convos WHERE account_id = ? ORDER BY last_message_date DESC LIMIT ?",
                        (account_id, limite))
    except Exception as e:
        log.error(f"Error al listar las conversaciones de W Social: {e}")
        return []
    for convo_id, titulo, no_leidos, silenciada, ultimo, fecha, grupo in rows:
        texto = (_("Grupo {t}") if grupo else _("Conversación con {t}")).format(t=titulo)
        if no_leidos:
            texto += " · " + _("{n} sin leer").format(n=no_leidos)
        if silenciada:
            texto += " · " + _("silenciada")
        if ultimo:
            texto += ": " + _condensar(ultimo, 160)
        filas.append(Fila(id=convo_id, feed_id=0, feed_titulo=titulo, titulo=texto, autor=titulo, fecha=fecha,
                          leido=not no_leidos, favorito=False, mas_tarde=False, audio=False, enlace="",
                          servicio="wsocial",
                          extra={"tipo": "convo", "account_id": account_id, "convo_id": convo_id, "titulo": titulo,
                                 "silenciada": bool(silenciada), "grupo": bool(grupo), "propio": False}))
    return filas


def _listar_mensajes(account_id: int, convo_id: str, limite: int) -> List[Fila]:
    filas = []
    try:
        rows = db.query("SELECT message_id, COALESCE(sender_name, ''), COALESCE(text, ''), COALESCE(sent_at, ''), "
                        "COALESCE(outgoing, 0), COALESCE(reactions_json, '') FROM wsocial_messages "
                        "WHERE account_id = ? AND convo_id = ? ORDER BY sent_at ASC LIMIT ?",
                        (account_id, convo_id, limite))
    except Exception as e:
        log.error(f"Error al listar los mensajes de W Social: {e}")
        return []
    for mid, quien, texto, fecha, saliente, reacciones in rows:
        quien = _("Yo") if saliente else quien
        linea = f"{quien}: {_condensar(texto, 400)}"
        try:
            rs = json.loads(reacciones) if reacciones else []
        except Exception:
            rs = []
        if rs:
            linea += " · " + _("reacciones: {r}").format(r=" ".join(r.get("emoji", "") for r in rs))
        filas.append(Fila(id=mid, feed_id=0, feed_titulo=quien, titulo=linea, autor=quien, fecha=fecha,
                          leido=True, favorito=False, mas_tarde=False, audio=False, enlace="", servicio="wsocial",
                          extra={"tipo": "mensaje", "account_id": account_id, "convo_id": convo_id,
                                 "message_id": mid, "usuario": "", "propio": bool(saliente), "texto": texto}))
    return filas


def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de W Social para el panel de lectura."""
    from .datos import fecha_legible
    x = fila.extra
    if x.get("tipo") == "convo":
        return (fila.autor, fecha_legible(fila.fecha), "",
                fila.titulo + "\n\n" + _("Pulsa Intro para abrir la conversación."))
    if x.get("tipo") == "mensaje":
        return (fila.autor, fecha_legible(fila.fecha), "", x.get("texto") or fila.titulo)
    if x.get("tipo") == "notificacion":
        cuerpo = fila.titulo
        if x.get("uri"):
            r = db.query_one(f"SELECT {_COLS} FROM wsocial_posts p WHERE p.account_id = ? AND p.uri = ?",
                             (x["account_id"], x["uri"]))
            if r:
                return detalle(_fila_post(r, _yo(x["account_id"]), x["account_id"]))
        return (fila.titulo, fecha_legible(fila.fecha), fila.enlace, cuerpo)
    r = db.query_one(f"SELECT {_COLS}, COALESCE(p.external_json, ''), COALESCE(p.urls_json, '[]') "
                     "FROM wsocial_posts p WHERE p.id = ?", (fila.id,))
    if not r:
        return (fila.titulo, "", fila.enlace, "")
    base, (externo, urls) = r[:21], r[21:]
    (dbid, uri, cid, did, handle, nombre, contenido, creado, indexado, etiquetas, nlikes, nreposts, nresp,
     ncitas, liked, reposted, marcador, media, repost_por, padre, cita) = base
    meta = [f"{nombre or handle} (@{handle})", fecha_legible(creado or indexado)]
    partes = []
    if repost_por:
        partes.append(_("Republicado por {n}").format(n=repost_por))
    if etiquetas:
        partes.append(_("Advertencia de contenido: {e}").format(e=etiquetas.replace(",", ", ")))
    if padre:
        partes.append(_("Es una respuesta."))
    partes.append(contenido)
    try:
        adj = json.loads(media or "[]")
    except Exception:
        adj = []
    for i, a in enumerate(adj, 1):
        partes.append("\n" + _("Adjunto {i} ({t}): {d}").format(
            i=i, t=a.get("type", ""), d=(a.get("alt") or _("sin descripción"))))
    if externo:
        try:
            e = json.loads(externo)
            partes.append("\n" + _("Enlace: {t}. {d}").format(t=e.get("title", ""), d=e.get("description", "")))
        except Exception:
            pass
    if cita:
        q = db.query_one("SELECT COALESCE(author_name, author_handle), COALESCE(author_handle, ''), COALESCE(content, '') "
                         "FROM wsocial_posts WHERE account_id = ? AND uri = ?", (x["account_id"], cita))
        if q:
            partes.append("\n" + _("Citando a {n} (@{h}): {t}").format(n=q[0], h=q[1], t=q[2]))
        else:
            partes.append("\n" + _("Cita otra publicación (ábrela desde el menú para leerla)."))
    try:
        enlaces = json.loads(urls or "[]")
    except Exception:
        enlaces = []
    if enlaces:
        partes.append("\n" + _("Enlaces:") + "\n" + "\n".join(enlaces))
    partes.append("\n" + _("{r} respuestas · {i} republicaciones y citas · {f} me gusta").format(
        r=nresp, i=nreposts + ncitas, f=nlikes))
    return (nombre or handle, " · ".join(p for p in meta if p), fila.enlace, "\n".join(partes))


def menciones_de_respuesta(fila: Fila) -> str:
    """Respuesta: W Social enlaza al padre, no hace falta escribir la mención."""
    return ""


def estado_por_uri(account_id: int, uri: str) -> Optional[Fila]:
    r = db.query_one(f"SELECT {_COLS} FROM wsocial_posts p WHERE p.account_id = ? AND p.uri = ?", (account_id, uri))
    return None if not r else _fila_post(r, _yo(account_id), account_id)


def adaptar(funcion):
    """El servicio de W Social entrega (ok, resultado_o_error); la interfaz GTK trabaja con (resultado, error)."""
    def _cb(ok, valor):
        if ok:
            return funcion(valor, None)
        return funcion(None, valor or _("Error desconocido"))
    return _cb
