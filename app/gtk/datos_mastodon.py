# -*- coding: utf-8 -*-
"""Datos de Mastodon para la interfaz GTK: árbol de cuentas y buffers, listados y detalle.

El servicio descarga y cachea en SQLite; aquí solo se LEE esa caché y se le da forma de fila."""

import json
import logging
from typing import Dict, List, Optional

from app.core.database import db
from . import buffers_guardados
from .datos import Nodo, Fila, buffer_oculto
from .filtros_contenido import sin_ocultas
from .lectura import con_plantilla, notificacion

log = logging.getLogger(__name__)

# (clave del servicio, título). Las claves son las de mastodon_service.cargar_timeline_async.
BUFFERS = [
    ("home", "Inicio"),
    ("notifications", "Notificaciones"),
    ("mentions", "Menciones"),
    ("local", "Local"),
    ("public", "Federada"),
    ("direct", "Mensajes directos"),
    ("bookmarks", "Marcadores"),
    ("favourites", "Favoritos"),
    ("sent", "Mis publicaciones"),
]

# Buffers abiertos por el usuario durante la sesión (hilos, cronologías de usuario).
EXTRA: Dict[int, List[Nodo]] = {}

TIPOS_NOTIFICACION = {
    "mention": "te ha mencionado",
    "status": "ha publicado",
    "reblog": "ha impulsado tu publicación",
    "follow": "te ha seguido",
    "follow_request": "quiere seguirte",
    "favourite": "ha marcado como favorita tu publicación",
    "poll": "ha terminado una encuesta",
    "update": "ha editado una publicación",
    "admin.sign_up": "se ha registrado",
    "admin.report": "ha enviado un informe",
    "quote": "ha citado tu publicación",
    "quoted_update": "ha editado una publicación que citaste",
    "moderation_warning": "aviso de moderación",
    "severed_relationships": "relaciones cortadas",
}

VISIBILIDAD = {"public": "público", "unlisted": "no listado", "private": "solo seguidores", "direct": "mensaje directo"}


def titulo_buffer(clave: str) -> str:
    return dict(BUFFERS).get(clave, clave)


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'mastodon' AND active = 1 ORDER BY position, id")]


def construir_nodos() -> Nodo:
    """Raíz 'Mastodon' con una cuenta por hijo y, dentro de cada una, sus buffers."""
    raiz = Nodo(_("Mastodon"), "servicio", servicio="mastodon")
    for acc_id, nombre in cuentas():
        cuenta = Nodo(nombre, "cuenta", servicio="mastodon", account_id=acc_id)
        for clave, titulo in BUFFERS:
            if buffer_oculto("mastodon", clave):
                continue
            cuenta.hijos.append(Nodo(_(titulo), "timeline", servicio="mastodon", account_id=acc_id, timeline=clave))
        cuenta.hijos.extend(buffers_guardados.nodos("mastodon", acc_id))
        cuenta.hijos.extend(EXTRA.get(acc_id, []))
        raiz.hijos.append(cuenta)
    return raiz


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    """Crea (o recupera) un buffer abierto por el usuario; conversaciones, cronologías y búsquedas se guardan."""
    if buffers_guardados.guarda("mastodon", timeline):
        titulo = buffers_guardados.guardar("mastodon", account_id, timeline, titulo)
        return next(n for n in buffers_guardados.nodos("mastodon", account_id) if n.timeline == timeline)
    """Crea (o recupera) un buffer abierto por el usuario."""
    for n in EXTRA.setdefault(account_id, []):
        if n.timeline == timeline:
            return n
    n = Nodo(titulo, "timeline", servicio="mastodon", account_id=account_id, timeline=timeline, cerrable=True)
    EXTRA[account_id].append(n)
    return n


def cerrar_extra(nodo: Nodo):
    if buffers_guardados.guarda("mastodon", nodo.timeline):
        return buffers_guardados.quitar("mastodon", nodo.account_id, nodo.timeline)
    lista = EXTRA.get(nodo.account_id, [])
    if nodo in lista:
        lista.remove(nodo)


def _usuario_propio(account_id: int) -> str:
    fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
    return (fila[0] if fila else "") or ""


def _es_propio(author_username: str, nombre_cuenta: str) -> bool:
    """El nombre de cuenta es '@usuario@instancia'; el autor local llega como '@usuario'."""
    a = (author_username or "").lower()
    n = (nombre_cuenta or "").lower()
    return bool(a) and (a == n or n.startswith(a + "@"))


def _condensar(texto: str, limite: int = 220) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


_COLS = ("s.id, s.status_id, COALESCE(s.reblog_of_id, s.status_id), s.author_name, s.author_username, "
         "COALESCE(s.content, ''), COALESCE(s.created_at, ''), COALESCE(s.visibility, 'public'), "
         "COALESCE(s.spoiler_text, ''), s.favourited, s.reblogged, s.bookmarked, COALESCE(s.media_json, '[]'), "
         "COALESCE(s.reblog_by_name, ''), COALESCE(s.has_poll, 0), COALESCE(s.replies_count, 0), "
         "COALESCE(s.reblogs_count, 0), COALESCE(s.favourites_count, 0), COALESCE(s.uri, ''), "
         "COALESCE(s.edited_at, ''), COALESCE(s.in_reply_to_id, ''), COALESCE(s.pinned, 0), "
         "COALESCE(s.application, ''), COALESCE(s.language, '')")


def _fila_estado(r, nombre_cuenta: str, account_id: int) -> Fila:
    (dbid, sid, orig, autor, usuario, contenido, creado, visib, cw, fav, imp, marc, media,
     impulsado_por, poll, resp, nimp, nfav, uri, editado, responde_a, fijado, aplicacion, idioma) = r
    try:
        adjuntos = len(json.loads(media or "[]"))
    except Exception:
        adjuntos = 0
    resumen = f"{autor} ({usuario}): " + (f"{_('Aviso de contenido')}: {_condensar(cw, 120)}" if cw
                                           else _condensar(contenido))
    fila = Fila(id=dbid, feed_id=0, feed_titulo=usuario or "", titulo=resumen, autor=autor or "",
                fecha=creado, leido=True, favorito=bool(fav), mas_tarde=False, audio=False, enlace=uri,
                servicio="mastodon",
                extra={"tipo": "estado", "account_id": account_id, "status_id": orig, "entrada_id": sid,
                       "impulsado": bool(imp), "marcador": bool(marc), "impulsado_por": impulsado_por,
                       "adjuntos": adjuntos, "encuesta": bool(poll), "respuestas": resp,
                       "n_impulsos": nimp, "n_favoritos": nfav, "visibilidad": visib,
                       "propio": _es_propio(usuario, nombre_cuenta), "usuario": usuario})
    from html import unescape
    from app.core.html_utils import html_a_texto
    fijos = [_(t) for t, hay in (("Encuesta", poll), ("Editado", editado), ("Respuesta", responde_a),
                                  ("Fijado", fijado)) if hay]
    fila.extra["indicadores_fijos"] = fijos
    return con_plantilla(fila, "mastodon_template", {
        "author": impulsado_por or autor or "", "username": usuario or "",
        "content": _condensar(html_a_texto(contenido) if "<" in (contenido or "") else unescape(contenido or "")),
        "visibility": _(VISIBILIDAD.get(visib, visib)).capitalize(),
        "media": (_("1 archivo adjunto") if adjuntos == 1 else _("{0} archivos adjuntos").format(adjuntos)) if adjuntos else "",
        "application": aplicacion or "", "cw": (cw or "").strip(), "language": idioma or "",
        "counts": _("{f} favoritos, {r} impulsos, {p} respuestas").format(f=nfav, r=nimp, p=resp)})


def listar(account_id: int, timeline: str, limite: int = 200) -> List[Fila]:
    """Filas de un buffer de Mastodon desde la caché local."""
    nombre = _usuario_propio(account_id)
    if timeline in ("notifications", "mentions"):
        return _listar_notificaciones(account_id, nombre, solo_menciones=(timeline == "mentions"), limite=limite)
    clave = "direct" if timeline == "direct" else timeline
    sql = (f"SELECT {_COLS} FROM mastodon_timeline_statuses t JOIN mastodon_statuses s ON s.id = t.status_db_id "
           "WHERE t.account_id = ? AND t.timeline = ? ORDER BY CAST(s.status_id AS INTEGER) DESC LIMIT ?")
    if timeline.startswith("thread:"):
        # La conversación va en orden cronológico, no del más nuevo al más viejo.
        sql = sql.replace("DESC LIMIT", "ASC LIMIT")
    try:
        filas = db.query(sql, (account_id, clave, limite))
        if not timeline.startswith("thread:"):
            filas = sin_ocultas("mastodon", filas, 5, 4, html=True)
        return [_fila_estado(r, nombre, account_id) for r in filas]
    except Exception as e:
        log.error(f"Error al listar el buffer '{timeline}': {e}")
        return []


def _listar_notificaciones(account_id: int, nombre: str, solo_menciones: bool, limite: int) -> List[Fila]:
    sql = (f"SELECT n.id, n.notification_id, n.type, COALESCE(n.created_at, ''), COALESCE(n.from_name, ''), "
           f"COALESCE(n.from_username, ''), n.status_id, COALESCE(n.extra_json, ''), {_COLS} "
           "FROM mastodon_notifications n LEFT JOIN mastodon_statuses s "
           "ON s.id = n.account_id || ':' || n.status_id WHERE n.account_id = ? "
           + ("AND n.type = 'mention' " if solo_menciones else "")
           + "ORDER BY CAST(n.notification_id AS INTEGER) DESC LIMIT ?")
    filas = []
    try:
        rows = db.query(sql, (account_id, limite))
    except Exception as e:
        log.error(f"Error al listar notificaciones: {e}")
        return []
    for r in rows:
        nid, notif_num, tipo, creado, de_nombre, de_usuario, status_id, extra = r[:8]
        estado = r[8:]
        accion = _(TIPOS_NOTIFICACION.get(tipo, tipo))
        texto = f"{de_nombre} ({de_usuario}) {accion}"
        fila_estado = None
        if status_id and estado and estado[0]:
            fila_estado = _fila_estado(estado, nombre, account_id)
            texto += ": " + (fila_estado.titulo.split("): ", 1)[-1])
        f = Fila(id=nid, feed_id=0, feed_titulo=de_usuario, titulo=texto, autor=de_nombre, fecha=creado,
                 leido=True, favorito=bool(fila_estado and fila_estado.favorito), mas_tarde=False, audio=False,
                 enlace=fila_estado.enlace if fila_estado else "", servicio="mastodon",
                 extra=dict(fila_estado.extra) if fila_estado else {})
        f.extra.update({"tipo": "notificacion", "notificacion": tipo, "notificacion_id": notif_num,
                        "de_usuario": de_usuario, "account_id": account_id, "estado_db_id": estado[0] if estado else None})
        if fila_estado:
            f.extra["entrada_id"] = fila_estado.extra.get("entrada_id")
        f.extra.pop("plantilla", None)          # se copió de la publicación: la notificación usa la suya
        f.extra.pop("campos", None)
        contenido = fila_estado.extra.get("campos", {}).get("content", "") if fila_estado else ""
        notificacion(f, tipo, de_nombre, de_usuario, contenido,
                     "mention_template" if (solo_menciones and tipo == "mention") else "")
        filas.append(f)
    return filas


def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de Mastodon para el panel de lectura."""
    from .datos import fecha_legible
    x = fila.extra
    if x.get("tipo") == "notificacion" and not x.get("estado_db_id"):
        return (fila.titulo, fecha_legible(fila.fecha), "", _extra_notificacion(fila))
    dbid = x.get("estado_db_id") or fila.id
    r = db.query_one(f"SELECT {_COLS}, COALESCE(s.urls_json, '[]'), COALESCE(s.quote_json, ''), "
                     "COALESCE(s.application, ''), COALESCE(s.edited_at, ''), COALESCE(s.language, '') "
                     "FROM mastodon_statuses s WHERE s.id = ?", (dbid,))
    if not r:
        return (fila.titulo, "", fila.enlace, "")
    base, (urls, cita, _app, editado, idioma) = r[:24], r[24:]  # noqa: F841
    (_i, _sid, _orig, autor, usuario, contenido, creado, visib, cw, fav, imp, marc, media, impulsado_por,
     poll, resp, nimp, nfav, uri) = base[:19]
    meta = [f"{autor} ({usuario})", fecha_legible(creado), _(VISIBILIDAD.get(visib, visib))]
    if editado:
        meta.append(_("editado"))
    if idioma:
        meta.append(idioma)
    partes = []
    if x.get("tipo") == "notificacion":
        partes.append(f"{fila.autor} ({x.get('de_usuario', '')}) {_(TIPOS_NOTIFICACION.get(x.get('notificacion'), ''))}\n")
    if impulsado_por:
        partes.append(_("Impulsado por {n}").format(n=impulsado_por) + "\n")
    if cw:
        partes.append(_("Aviso de contenido: {t}").format(t=cw) + "\n")
    partes.append(contenido)
    try:
        adj = json.loads(media or "[]")
    except Exception:
        adj = []
    for i, a in enumerate(adj, 1):
        desc = a.get("description") or _("sin descripción")
        partes.append("\n" + _("Adjunto {i} ({t}): {d}").format(i=i, t=a.get("type", ""), d=desc))
    if poll:
        partes.append("\n" + _("Esta publicación contiene una encuesta."))
    if cita:
        try:
            c = json.loads(cita)
            if c.get("content"):
                partes.append("\n" + _("Citando a {n} (@{a}): {t}").format(
                    n=c.get("name", ""), a=c.get("acct", ""), t=c["content"]))
        except Exception:
            pass
    try:
        enlaces = [u for u in json.loads(urls or "[]") if u != uri]
    except Exception:
        enlaces = []
    if enlaces:
        partes.append("\n" + _("Enlaces:") + "\n" + "\n".join(enlaces))
    partes.append("\n" + _("{r} respuestas · {i} impulsos · {f} favoritos").format(r=resp, i=nimp, f=nfav))
    return (cw and _("Aviso de contenido: {t}").format(t=cw) or autor, " · ".join(p for p in meta if p), uri,
            "\n".join(partes))


def _extra_notificacion(fila: Fila) -> str:
    return fila.titulo


def urls_de(fila: Fila) -> List[str]:
    dbid = fila.extra.get("estado_db_id") or fila.id
    r = db.query_one("SELECT COALESCE(urls_json, '[]') FROM mastodon_statuses WHERE id = ?", (dbid,))
    try:
        return json.loads(r[0]) if r else []
    except Exception:
        return []


def estado_por_id(db_id: str) -> Optional[Fila]:
    r = db.query_one(f"SELECT {_COLS}, t.account_id FROM mastodon_statuses s "
                     "JOIN accounts t ON t.id = s.account_id WHERE s.id = ?", (db_id,))
    return None if not r else _fila_estado(r[:-1], _usuario_propio(r[-1]), r[-1])


def menciones_de_respuesta(fila: Fila) -> str:
    """Texto inicial de una respuesta: el autor y los mencionados (sin repetir ni incluirte)."""
    dbid = fila.extra.get("estado_db_id") or fila.id
    r = db.query_one("SELECT author_username, COALESCE(mentions_json, '[]'), account_id FROM mastodon_statuses WHERE id = ?",
                     (dbid,))
    if not r:
        return ""
    propio = _usuario_propio(r[2])
    accts = [r[0]]
    try:
        accts += ["@" + m for m in json.loads(r[1])]
    except Exception:
        pass
    vistos, salida = set(), []
    for a in accts:
        k = (a or "").lower()
        if not k or k in vistos or _es_propio(a, propio):
            continue
        vistos.add(k)
        salida.append(a)
    return " ".join(salida) + (" " if salida else "")
