# -*- coding: utf-8 -*-
"""Extrae de una publicación de AT Protocol (PostView / FeedViewPost) los DATOS DE MODERACIÓN
que necesita el motor oficial y los guarda compactos (columna `mod_json`).

Qué se guarda (claves cortas para no inflar la base de datos):
- d:   DID del autor (para distinguir autoetiquetas y "es mío").
- l:   etiquetas del CONTENIDO [[valor, origen], ...] ya con las negaciones aplicadas; las
       autoetiquetas del registro van con origen = DID del autor.
- al:  etiquetas de la CUENTA/PERFIL del autor [[valor, origen, "a"|"p"], ...]: "p" si la
       etiqueta está puesta sobre el registro de perfil (app.bsky.actor.profile/self) y "a"
       si es sobre la cuenta. El oficial las trata distinto (decideAccount / decideProfile).
- tx, t, lg: texto, etiquetas (#tag de facets + record.tags) e idiomas (palabras silenciadas).
- alt: textos alternativos de sus imágenes/galería; ext: "título descripción" de su tarjeta.
- malt / mext: multimedia y tarjeta del recordWithMedia propio.
- f, mu, bl, bb: sigues al autor / lo has silenciado / lo bloqueas / te bloquea.
- q:   la publicación CITADA con lo mismo (u, d, tx, t, lg, f, l, al, alt, ext, ralt, rext,
       mu, bl, bb) o {"blk": True, ...} si no se puede ver (bloqueo).
- rp:  en un FeedViewPost que es respuesta: autores del padre y de la raíz y si los sigues
       ({"pd", "pf", "rd", "rf"}), para las preferencias de Inicio (feedViewPref).

Módulo PURO (sin wx, sin red, sin atproto importado): reutilizable por W Social.
"""
import json

from app.services.bluesky.bluesky_mutewords import tags_de_facets

_SUFIJO_PERFIL = "/app.bsky.actor.profile/self"


def _g(obj, *nombres, default=None):
    for n in nombres:
        if obj is None:
            return default
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
        elif hasattr(obj, n):
            return getattr(obj, n)
    return default


def _tipo(obj) -> str:
    return str(_g(obj, "py_type", "$type", default="") or "").lower()


def etiquetas_de_vista(labels) -> list:
    """[[valor, origen], ...] de una lista de com.atproto.label.defs#label, aplicando las
    negaciones (neg=True retira la etiqueta del mismo origen)."""
    salida = []
    for lab in (labels or []):
        val = str(_g(lab, "val", default="") or "")
        src = str(_g(lab, "src", default="") or "")
        if not val:
            continue
        if _g(lab, "neg", default=False):
            salida = [x for x in salida if not (x[0] == val and x[1] == src)]
        elif [val, src] not in salida:
            salida.append([val, src])
    return salida


def autoetiquetas(record, author_did: str) -> list:
    """Autoetiquetas (record.labels: com.atproto.label.defs#selfLabels) como [[valor, did]]."""
    salida = []
    for v in (_g(_g(record, "labels", default=None), "values", default=None) or []):
        val = str(_g(v, "val", default="") or "")
        if val and [val, author_did] not in salida:
            salida.append([val, author_did])
    return salida


def etiquetas_de_autor(author) -> list:
    """Etiquetas del autor separadas por objetivo: "p" (perfil) o "a" (cuenta)."""
    salida = []
    for lab in (_g(author, "labels", default=None) or []):
        val = str(_g(lab, "val", default="") or "")
        src = str(_g(lab, "src", default="") or "")
        uri = str(_g(lab, "uri", default="") or "")
        if not val:
            continue
        objetivo = "p" if uri.endswith(_SUFIJO_PERFIL) else "a"
        if _g(lab, "neg", default=False):
            salida = [x for x in salida if not (x[0] == val and x[1] == src)]
        elif [val, src, objetivo] not in salida:
            salida.append([val, src, objetivo])
    return salida


def _viewer_flags(author) -> dict:
    viewer = _g(author, "viewer", default=None)
    d = {}
    if _g(viewer, "following", default=None):
        d["f"] = 1
    if _g(viewer, "muted", default=False):
        d["mu"] = 1
    if _g(viewer, "blocking", default=None):
        d["bl"] = 1
    if _g(viewer, "blocked_by", "blockedBy", default=False):
        d["bb"] = 1
    return d


def _alts_de_embed_registro(embed) -> list:
    """alt de las imágenes o de la galería de un embed de REGISTRO (images / gallery)."""
    tipo = _tipo(embed)
    if "images" in tipo:
        return [str(_g(i, "alt", default="") or "") for i in (_g(embed, "images", default=None) or [])]
    if "gallery" in tipo:
        return [str(_g(i, "alt", default="") or "") for i in (_g(embed, "items", default=None) or [])
                if "image" in (_tipo(i) or "image")]
    return []


def _tarjeta(external) -> str:
    if not external:
        return ""
    return f"{_g(external, 'title', default='') or ''} {_g(external, 'description', default='') or ''}"


def _texto_tags_idiomas(record) -> tuple:
    texto = str(_g(record, "text", default="") or "")
    tags = tags_de_facets(_g(record, "facets", default=None))
    tags += [str(t) for t in (_g(record, "tags", default=None) or [])]
    langs = [str(x) for x in (_g(record, "langs", default=None) or [])]
    return texto, tags, langs


def _datos_cita(vr, completa: bool) -> dict:
    """Datos de un app.bsky.embed.record#viewRecord (o viewBlocked/notFound)."""
    tipo = _tipo(vr)
    author = _g(vr, "author", default=None)
    did = str(_g(author, "did", default="") or "")
    uri = str(_g(vr, "uri", default="") or "")
    if "viewblocked" in tipo or "viewnotfound" in tipo or "viewdetached" in tipo:
        d = {"u": uri, "d": did, "blk": True}
        d.update(_viewer_flags(author))
        return d
    if "viewrecord" not in tipo and _g(vr, "value", default=None) is None:
        return {}   # otro tipo de registro citado (feed, lista, starter pack...)
    valor = _g(vr, "value", default=None)
    texto, tags, langs = _texto_tags_idiomas(valor)
    q = {"u": uri, "d": did, "tx": texto}
    if tags:
        q["t"] = tags
    if langs:
        q["lg"] = langs
    q.update(_viewer_flags(author))
    l = etiquetas_de_vista(_g(vr, "labels", default=None)) + autoetiquetas(valor, did)
    if l:
        q["l"] = l
    al = etiquetas_de_autor(author)
    if al:
        q["al"] = al
    if completa:
        emb = _g(valor, "embed", default=None)
        alts = _alts_de_embed_registro(emb)
        if alts:
            q["alt"] = alts
        tipo_e = _tipo(emb)
        if "external" in tipo_e and "recordwithmedia" not in tipo_e:
            q["ext"] = _tarjeta(_g(emb, "external", default=None))
        if "recordwithmedia" in tipo_e:
            media = _g(emb, "media", default=None)
            if "external" in _tipo(media):
                q["rext"] = _tarjeta(_g(media, "external", default=None))
            ralts = _alts_de_embed_registro(media)
            if ralts:
                q["ralt"] = ralts
    return q


def extraer_datos_post(post, feed_item=None) -> dict:
    """Datos de moderación de un PostView (y, si se da, del FeedViewPost que lo envuelve)."""
    author = _g(post, "author", default=None)
    did = str(_g(author, "did", default="") or "")
    record = _g(post, "record", default=None)
    texto, tags, langs = _texto_tags_idiomas(record)
    d = {"d": did, "tx": texto}
    l = etiquetas_de_vista(_g(post, "labels", default=None))
    for x in autoetiquetas(record, did):
        if x not in l:
            l.append(x)
    if l:
        d["l"] = l
    al = etiquetas_de_autor(author)
    if al:
        d["al"] = al
    if tags:
        d["t"] = tags
    if langs:
        d["lg"] = langs
    d.update(_viewer_flags(author))
    # Imágenes propias (del REGISTRO, como el oficial).
    alts = _alts_de_embed_registro(_g(record, "embed", default=None))
    if alts:
        d["alt"] = alts
    # Embed hidratado: tarjeta, cita y recordWithMedia.
    embed = _g(post, "embed", default=None)
    tipo = _tipo(embed)
    if "recordwithmedia" in tipo:
        rec = _g(_g(embed, "record", default=None), "record", default=None)
        q = _datos_cita(rec, completa=False) if rec is not None else {}
        if q:
            d["q"] = q
        media = _g(embed, "media", default=None)
        tipo_m = _tipo(media)
        if "images" in tipo_m:
            malt = [str(_g(i, "alt", default="") or "") for i in (_g(media, "images", default=None) or [])]
        elif "gallery" in tipo_m:
            malt = [str(_g(i, "alt", default="") or "") for i in (_g(media, "items", default=None) or [])]
        else:
            malt = []
        if malt:
            d["malt"] = malt
        if "external" in tipo_m:
            d["mext"] = _tarjeta(_g(media, "external", default=None))
    elif "embed.record" in tipo:
        rec = _g(embed, "record", default=None)
        q = _datos_cita(rec, completa=True) if rec is not None else {}
        if q:
            d["q"] = q
    elif "external" in tipo:
        d["ext"] = _tarjeta(_g(embed, "external", default=None))
    # Contexto de respuesta del FeedViewPost (autores del padre y de la raíz).
    reply = _g(feed_item, "reply", default=None) if feed_item is not None else None
    if reply is not None:
        rp = {}
        for clave, nodo in (("p", _g(reply, "parent", default=None)),
                            ("r", _g(reply, "root", default=None))):
            a = _g(nodo, "author", default=None)
            if a is not None:
                rp[clave + "d"] = str(_g(a, "did", default="") or "")
                rp[clave + "f"] = 1 if _g(_g(a, "viewer", default=None), "following", default=None) else 0
        if rp:
            d["rp"] = rp
    return d


def extraer_datos_notificacion(n) -> dict:
    """Datos de moderación de una notificación (listNotifications): sus etiquetas, las de la
    cuenta que la provoca y, si el registro es una publicación (mención, respuesta, cita), su
    texto para las palabras silenciadas."""
    author = _g(n, "author", default=None)
    did = str(_g(author, "did", default="") or "")
    record = _g(n, "record", default=None)
    d = {"d": did}
    l = etiquetas_de_vista(_g(n, "labels", default=None))
    if l:
        d["l"] = l
    else:
        d["l"] = []
    al = etiquetas_de_autor(author)
    if al:
        d["al"] = al
    d.update(_viewer_flags(author))
    if "feed.post" in _tipo(record) or (_g(record, "text", default=None) is not None
                                        and "/app.bsky.feed.post/" in str(_g(n, "uri", default=""))):
        texto, tags, langs = _texto_tags_idiomas(record)
        d["tx"] = texto
        if tags:
            d["t"] = tags
        if langs:
            d["lg"] = langs
        alts = _alts_de_embed_registro(_g(record, "embed", default=None))
        if alts:
            d["alt"] = alts
    return d


def a_json(datos: dict) -> str:
    try:
        return json.dumps(datos or {}, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return ""


def de_json(texto) -> dict:
    if not texto:
        return {}
    if isinstance(texto, dict):
        return texto
    try:
        d = json.loads(texto)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def fusionar_rp(nuevo: dict, previo: dict) -> dict:
    """Conserva el contexto de respuesta (rp) ya conocido si esta vez el post llega sin él
    (búsqueda, menciones, hilos no traen reply hidratado)."""
    if "rp" not in nuevo and previo and previo.get("rp"):
        nuevo = dict(nuevo)
        nuevo["rp"] = previo["rp"]
    return nuevo
