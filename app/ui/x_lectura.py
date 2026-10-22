# -*- coding: utf-8 -*-
"""Textos accesibles de una publicación de X (sin wx): adjuntos, tuit citado, encuesta e
indicadores. Los usan la lista, el visor de conversación y la bandeja, para leerse igual."""
import json

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s


def describir_media_x(media_json) -> str:
    """"Imagen: descripción. Vídeo (sin descripción)" a partir de x_tweets.media_json."""
    if not media_json:
        return ""
    try:
        adjuntos = json.loads(media_json) or []
    except (TypeError, ValueError):
        return ""
    partes = []
    for a in adjuntos:
        if not isinstance(a, dict):
            continue
        tipo = (a.get("type") or "").lower()
        if "gif" in tipo:
            etq = _("GIF")
        elif a.get("video") or "video" in tipo:
            etq = _("Vídeo")
        else:
            etq = _("Imagen")
        alt = (a.get("alt") or "").strip()
        partes.append(etq + ((": " + alt) if alt else (" " + _("(sin descripción)"))))
    return ". ".join(partes)


def texto_cita_x(autor, texto) -> str:
    """"Cita de Ana (@ana): texto" del tuit citado, o "" si no cita a nadie."""
    autor = (autor or "").strip()
    if not autor:
        return ""
    texto = (texto or "").replace("\n", " ").strip()
    return _("Cita de {0}: {1}").format(autor, texto) if texto else _("Cita de {0}").format(autor)


def datos_encuesta_x(poll_json):
    """La encuesta guardada como dict, o None."""
    if not poll_json:
        return None
    try:
        d = json.loads(poll_json)
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("choices") else None


def texto_encuesta_x(poll_json) -> str:
    """"Encuesta: Sí, 12 votos; No, 3 votos. Cerrada." (o "" si no hay encuesta)."""
    d = datos_encuesta_x(poll_json)
    if not d:
        return ""
    opciones = []
    for c in d.get("choices", []):
        n = int(c.get("count", 0) or 0)
        votos = _("{0} voto").format(n) if n == 1 else _("{0} votos").format(n)
        marca = _(" (tu voto)") if d.get("selected") and str(d["selected"]) in (
            str(c.get("number")), str(c.get("label"))) else ""
        opciones.append(f"{c.get('label', '')}, {votos}{marca}")
    estado = _("Cerrada") if d.get("final") else _("Abierta")
    return _("Encuesta: {0}. {1}").format("; ".join(opciones), estado)


def indicadores_x(rt_by, liked, retuit, guardado, in_reply_to, cita_autor) -> str:
    """Estado de la publicación para el campo {indicators} (como en Mastodon y Bluesky)."""
    ind = []
    if rt_by:
        ind.append(_("RT de @{0}").format(rt_by))
    if in_reply_to:
        ind.append(_("Respuesta"))
    if cita_autor:
        ind.append(_("Cita"))
    if liked:
        ind.append(_("Me gusta"))
    if retuit:
        ind.append(_("Retuiteado"))
    if guardado:
        ind.append(_("Guardado"))
    return ", ".join(ind)
