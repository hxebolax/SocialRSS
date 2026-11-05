# -*- coding: utf-8 -*-
"""Lectura de las filas con las plantillas del usuario (Herramientas, Editor de plantillas de lectura).

Las mismas plantillas y la misma configuración (`accessibility.<clave>`) que usa la versión de Windows. Al armar una fila, cada
red guarda en `extra["plantilla"]` la clave de su plantilla y en `extra["campos"]` lo que no cambia (autor, texto, adjuntos,
contadores...). Lo que sí cambia con la acción del usuario (indicadores de «Me gusta» o «Guardado», leído o no) y la fecha
relativa se calculan al leer, así que un «Me gusta» se oye de inmediato y los «hace 5 minutos» no se quedan viejos."""

import logging
from collections import defaultdict

from app.core.config_manager import config
from app.core.plantillas import definicion, pulir_lectura

log = logging.getLogger(__name__)

# Etiquetas de «me gusta» y «impulso» por red: Mastodon habla de favoritos e impulsos; X, de me gusta y retuits.
_ETIQUETAS_POST = {"mastodon": ("Favorito", "Boosteado"), "x": ("Me gusta", "Retuiteado")}
_PREDETERMINADAS = ("Me gusta", "Reposteado")


def con_plantilla(fila, clave: str, campos: dict):
    """Anota en la fila qué plantilla la lee y los campos fijos de esa plantilla. Devuelve la propia fila."""
    fila.extra["plantilla"] = clave
    fila.extra["campos"] = campos
    return fila


def _tipos_notificacion(servicio: str) -> dict:
    if servicio == "mastodon":
        return {"mention": _("Mención"), "reblog": _("Impulso"), "favourite": _("Favorito"), "follow": _("Seguidor"),
                "follow_request": _("Solicitud"), "poll": _("Encuesta"), "status": _("Nueva publicación"),
                "update": _("Editada"), "quote": _("Cita")}
    if servicio == "x":
        return {"like": _("Me gusta"), "favorite": _("Me gusta"), "retweet": _("Retweet"), "follow": _("Nuevo seguidor"),
                "mention": _("Mención"), "reply": _("Respuesta"), "quote": _("Cita"), "recommendation": _("Recomendación")}
    return {"like": _("Me gusta"), "repost": _("Repost"), "follow": _("Nuevo seguidor"), "mention": _("Mención"),
            "reply": _("Respuesta"), "quote": _("Cita"), "starterpack-joined": _("Starter pack"),
            "verified": _("Cuenta verificada"), "unverified": _("Verificación retirada"),
            "like-via-repost": _("Me gusta a tu repost"), "repost-via-repost": _("Repost de tu repost"),
            "subscribed-post": _("Publicación de una cuenta con aviso"), "contact-match": _("Contacto encontrado")}


_PLANTILLA_NOTIFICACION = {"mastodon": "notification_template", "bluesky": "bluesky_notification",
                           "wsocial": "wsocial_notification", "x": "x_notification"}


def notificacion(fila, tipo: str, autor: str, usuario: str, contenido: str, clave: str = ""):
    """Lee una notificación con la plantilla de notificaciones de su red (o la que se indique, como la de menciones)."""
    nombre = _tipos_notificacion(fila.servicio).get(tipo, tipo or "")
    arroba = usuario if not usuario or usuario.startswith("@") else "@" + usuario
    return con_plantilla(fila, clave or _PLANTILLA_NOTIFICACION[fila.servicio],
                         {"type": nombre, "author": autor or "", "username": arroba or "", "content": contenido or ""})


def describir_adjuntos_at(media_json: str, cita: bool = False) -> str:
    """Adjuntos de una publicación de AT Protocol (Bluesky, W Social): tipo y texto alternativo de cada uno, y «Cita»."""
    import json
    partes = []
    try:
        adjuntos = json.loads(media_json or "[]") or []
    except Exception:
        adjuntos = []
    for a in adjuntos:
        tipo = (a.get("type") or "").lower()
        alt = (a.get("alt") or "").strip()
        if a.get("video") or "video" in tipo:
            etq = _("Vídeo")
        elif "gif" in tipo or "animated" in tipo:
            etq = _("GIF")
        else:
            etq = _("Imagen")
        partes.append(etq + ((": " + alt) if alt else " " + _("(sin descripción)")))
    if cita:
        partes.append(_("Cita"))
    return ". ".join(partes)


def campos_post_at(autor: str, handle: str, contenido: str, repost_por: str, media: str, etiquetas: str,
                   nlikes: int, nreposts: int, nresp: int, ncitas: int, republicaciones: bool) -> dict:
    """Campos fijos de una publicación de Bluesky o W Social."""
    cuerpo = " ".join((contenido or "").split())
    if repost_por:
        cuerpo = _("[Reposteado por {0}] ").format(repost_por) + cuerpo
    cuentas = (_("{0} me gusta, {1} republicaciones, {2} respuestas, {3} citas") if republicaciones
               else _("{0} me gusta, {1} reposts, {2} respuestas, {3} citas")).format(nlikes, nreposts, nresp, ncitas)
    return {"author": autor or handle, "username": "@" + handle if handle else "", "content": cuerpo, "media": media,
            "cw": ", ".join(e for e in (etiquetas or "").split(",") if e), "counts": cuentas, "language": "",
            "reply_to": ""}


def hora(iso: str) -> str:
    """HH:MM de una fecha ISO ('' si no se entiende)."""
    t = (iso or "").replace("T", " ")
    return t[11:16] if len(t) >= 16 else ""


def plantilla(clave: str) -> str:
    return config.get("accessibility", clave, "") or definicion(clave)["default"]


def _indicadores_post(obj) -> str:
    f, x = obj.fila, obj.fila.extra
    fav, imp = _ETIQUETAS_POST.get(f.servicio, _PREDETERMINADAS)
    ind = []
    if obj.favorito:
        ind.append(_(fav))
    if obj.impulsado:
        ind.append(_(imp))
    if obj.marcador:
        ind.append(_("Guardado"))
    ind.extend(x.get("indicadores_fijos", []))
    return ", ".join(ind) if f.servicio == "x" else " ".join(ind)


def _marcas_rss(obj) -> str:
    estado = _("Leído") if obj.leido else _("No leído")
    if obj.favorito:
        estado += ", " + _("Destacado")
    if obj.mas_tarde:
        estado += ", " + _("Leer más tarde")
    return estado


def valores(obj) -> dict:
    """Campos de la plantilla de la fila: los fijos y los que dependen del estado actual."""
    from .datos import fecha_legible
    f, x = obj.fila, obj.fila.extra
    v = dict(x.get("campos") or {})
    v.setdefault("date", fecha_legible(f.fecha))
    clave = x.get("plantilla", "")
    if clave in ("mastodon_template", "bluesky_post", "wsocial_post", "x_post"):
        v["indicators"] = _indicadores_post(obj)
    elif clave == "rss_template":
        v["flags"] = _marcas_rss(obj)
    return v


def leer(obj):
    """(texto, con_origen) de la fila según su plantilla, o None si la fila no usa una (o la plantilla no se puede aplicar).
    `con_origen` es True si el texto ya dice de qué buffer viene (plantilla de la bandeja)."""
    x = obj.fila.extra
    clave = x.get("plantilla")
    if not clave:
        return None
    try:
        v = valores(obj)
        con_origen = False
        if x.get("bandeja"):
            # La bandeja tiene su propia plantilla, con el origen de cada fila.
            v = {"origen": x.get("origen", ""), "author": v.get("author") or obj.fila.autor,
                 "content": v.get("content") or obj.fila.titulo, "date": v.get("date", "")}
            clave, con_origen = "inbox_template", True
        texto = pulir_lectura(plantilla(clave).format_map(defaultdict(str, v)))
    except Exception:
        log.debug("Plantilla de lectura no aplicable (%s)", clave, exc_info=True)
        return None
    if not texto:
        return None
    if x.get("nuevo"):
        texto = _("Nuevo. ") + texto
    return texto, con_origen
