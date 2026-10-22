# -*- coding: utf-8 -*-
"""Presentación de REPOSTS y RESPUESTAS en las redes de AT Protocol (Bluesky y W Social).

Funciones puras (sin wx ni base de datos) para poder probarlas aisladas:

- Datos: quién reposteó (a partir del motivo `reason` que da el servidor) y a quién responde una
  publicación (a partir del `reply` hidratado del FeedViewPost, lexicon app.bsky.feed.defs#replyRef,
  cuyo `parent` puede ser postView, notFoundPost o blockedPost).
- Lectura: cómo se cuenta un repost ("Mensaje de Y reposteado por X", el formato histórico, o
  "X reposteó el mensaje de Y") y si se antepone "En respuesta a @x" al texto. Ambas cosas son
  preferencias por red; los valores por defecto reproducen exactamente lo de siempre.
"""
import json

# Formatos del repost en la lista.
FORMATO_REPOST_ORIGINAL = "original"    # De Y. [Reposteado por @X] texto   (el de siempre)
FORMATO_REPOST_QUIEN = "repostea"       # De X. Reposteó el mensaje de Y: texto
FORMATOS_REPOST = (FORMATO_REPOST_ORIGINAL, FORMATO_REPOST_QUIEN)


def _g(obj, *nombres, default=None):
    """Lee un atributo o clave probando varios nombres (modelos atproto o dicts)."""
    if obj is None:
        return default
    for n in nombres:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v is not None:
            return v
    return default


def did_de_uri(uri) -> str:
    """DID del autor de una AT-URI (at://did:plc:xxx/app.bsky.feed.post/rkey). Ojo: los DID llevan
    dos puntos, así que se corta por barras, nunca por ':'."""
    s = str(uri or "")
    if not s.startswith("at://"):
        return ""
    return s[5:].split("/", 1)[0]


def autor_del_padre(feed_item, parent_uri, author_did="", author_handle="", author_name=""):
    """(handle, nombre) del autor de la publicación a la que responde `feed_item`.

    1. Si el servidor hidrató la respuesta (FeedViewPost.reply.parent es un postView), su autor.
    2. Si el padre es del MISMO autor (un hilo propio), el propio autor: eso se sabe por el DID de
       la URI del padre sin pedir nada a la red.
    3. Si no hay forma de saberlo (búsquedas, menciones, padre borrado o bloqueado), ("", "").
       La interfaz intenta además resolverlo con la caché local al pintar.
    """
    reply = _g(feed_item, "reply") if feed_item is not None and _g(feed_item, "post") is not None else None
    padre = _g(reply, "parent")
    if padre is not None:
        autor = _g(padre, "author")
        # blockedPost también trae author, pero solo con el DID (blockedAuthor): sin handle no sirve.
        handle = _g(autor, "handle", default="") or ""
        if handle:
            nombre = _g(autor, "display_name", "displayName", default="") or handle
            return handle, nombre
    if parent_uri and author_did and did_de_uri(parent_uri) == author_did:
        return author_handle or "", author_name or author_handle or ""
    return "", ""


def repost_de_reason(reason):
    """(handle, nombre) de quien reposteó, a partir del `reason` de un FeedViewPost."""
    if reason is None:
        return "", ""
    tipo = str(_g(reason, "py_type", "$type", default="") or "").lower()
    if "repost" not in tipo:
        return "", ""
    by = _g(reason, "by")
    handle = _g(by, "handle", default="") or ""
    nombre = _g(by, "display_name", "displayName", default="") or handle
    return handle, nombre


def repost_de_reason_json(reason_json):
    """(handle, nombre) de quien reposteó, a partir del motivo guardado en JSON por buffer
    (bluesky_timeline_posts.reason_json, volcado con by_alias: claves $type, by.displayName...)."""
    if not reason_json:
        return "", ""
    try:
        datos = json.loads(reason_json)
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(datos, dict):
        return "", ""
    return repost_de_reason(datos)


def _nombre_visible(nombre, handle, con_usuario):
    """Cómo se nombra a una cuenta en la lectura, respetando "mostrar nombres de usuario"."""
    nombre = (nombre or "").strip()
    handle = (handle or "").strip()
    if con_usuario and handle:
        return f"{nombre} (@{handle})" if nombre and nombre != handle else f"@{handle}"
    return nombre or (f"@{handle}" if handle else "")


def mi_handle(servicio, account_id) -> str:
    """Handle de la cuenta propia SIN red: el que el servicio ya tiene en memoria o, si aún no,
    el nombre de la cuenta ("@handle", como se da de alta). "" si no se sabe."""
    try:
        yo = (getattr(servicio, "_me", None) or {}).get(account_id) or {}
        handle = yo.get("handle", "") if isinstance(yo, dict) else ""
        if handle:
            return handle
        from app.core.database import db
        fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
        nombre = str(fila[0] or "") if fila else ""
        return nombre[1:] if nombre.startswith("@") else ""
    except Exception:
        return ""


# Cuántos niveles del hilo reciben aviso de una respuesta. En el AppView oficial de AT Protocol
# (packages/bsky/.../indexing/plugins/post.ts, idéntico en el fork de W Social)
# REPLY_NOTIF_DEPTH = 5 contando la propia respuesta (altura 0): el padre y hasta 3 más arriba.
AVISO_RESPUESTA_NIVELES = 4


def contexto_desde_hilo(thread, mi_did="", mi_handle=""):
    """Recorre hacia arriba el hilo de getPostThread (pedido desde la publicación a la que se
    responde) y devuelve {"padre": {handle, nombre, texto} o None, "avisados": [{handle, nombre}],
    "yo": handle}. "avisados" = autores que recibirán aviso de la respuesta según la regla
    oficial, sin repetidos y sin la cuenta propia. Una publicación borrada o bloqueada corta la
    cadena (el servidor tampoco sigue por ahí)."""
    padre, avisados, vistos = None, [], {mi_did} if mi_did else set()
    nodo, nivel = thread, 0
    while nodo is not None and nivel < AVISO_RESPUESTA_NIVELES:
        post = _g(nodo, "post")
        if post is None:
            break
        autor = _g(post, "author")
        handle = _g(autor, "handle", default="") or ""
        datos = {"handle": handle,
                 "nombre": _g(autor, "display_name", "displayName", default="") or handle}
        if padre is None:
            padre = dict(datos, texto=_g(_g(post, "record"), "text", default="") or "")
        did = _g(autor, "did", default="") or handle
        if did not in vistos:
            vistos.add(did)
            avisados.append(datos)
        nodo = _g(nodo, "parent")
        nivel += 1
    return {"padre": padre, "avisados": avisados, "yo": mi_handle or ""}


def menciones_al_responder(autor_handle, texto, mi_handle="") -> str:
    """"@autor @mencionado ... " para empezar una respuesta como en Mastodon (y como FastSM o
    TWBlue): el autor de la publicación y quien ella menciona, sin la cuenta propia ni
    repetidos. Es OPCIONAL en Bluesky (Preferencias): el servidor ya avisa a los de encima en el
    hilo sin menciones, y cada mención ocupa caracteres del límite de 300."""
    from app.services.bluesky.bluesky_facets import detectar_facets
    yo = str(mi_handle or "").strip().lstrip("@").lower()
    salida, vistos = [], {yo} if yo else set()
    candidatos = [autor_handle] + [f["valor"] for f in detectar_facets(texto or "")
                                   if f.get("tipo") == "mention"]
    for h in candidatos:
        h = str(h or "").strip().lstrip("@")
        if h and h.lower() not in vistos:
            vistos.add(h.lower())
            salida.append("@" + h)
    return (" ".join(salida) + " ") if salida else ""


def _lista_legible(elementos):
    if not elementos:
        return ""
    if len(elementos) == 1:
        return elementos[0]
    return _("{0} y {1}").format(", ".join(elementos[:-1]), elementos[-1])


def texto_contexto_respuesta(ctx) -> str:
    """Texto del campo "Respondiendo a" del editor, a partir de contexto_respuesta_async:
    la publicación a la que respondes y quién recibirá el aviso de tu respuesta.

    Así se sabe, como en Mastodon, a quién va dirigida, pero sin menciones: en Bluesky el
    servidor avisa solo a los autores de encima en el hilo (regla oficial, hasta 4 niveles)."""
    ctx = ctx or {}
    padre = ctx.get("padre") or {}
    lineas = []
    if padre:
        quien = padre.get("nombre") or padre.get("handle") or ""
        if padre.get("handle"):
            quien = _("{0} (@{1})").format(quien, padre["handle"])
        lineas.append(_("{0}: {1}").format(quien, (padre.get("texto") or "").replace("\n", " ").strip()))
    else:
        lineas.append(_("La publicación a la que respondes no está disponible."))
    avisados = [(_("{0} (@{1})").format(a.get("nombre") or a["handle"], a["handle"])
                 if a.get("handle") else a.get("nombre") or "")
                for a in (ctx.get("avisados") or [])]
    avisados = [a for a in avisados if a]
    if avisados:
        if len(avisados) == 1:
            lineas.append(_("Recibirá aviso de tu respuesta: {0}. No hace falta "
                            "mencionarlo.").format(avisados[0]))
        else:
            lineas.append(_("Recibirán aviso de tu respuesta: {0}. No hace falta "
                            "mencionarlos.").format(_lista_legible(avisados)))
    elif padre:
        lineas.append(_("Es una publicación tuya: solo recibirá aviso quien menciones."))
    return "\n".join(lineas)


def texto_respuesta_a(handle, nombre="", mi_handle="") -> str:
    """Valor del campo {reply_to}: "En respuesta a @x", "En respuesta a ti" si la publicación
    original es tuya (como el "Replied to you" de la app oficial), o "" si no se sabe a quién."""
    handle = (handle or "").strip()
    if handle and mi_handle and handle.lower() == str(mi_handle).strip().lstrip("@").lower():
        return _("En respuesta a ti")
    if handle:
        return _("En respuesta a @{0}").format(handle)
    if (nombre or "").strip():
        return _("En respuesta a {0}").format(nombre.strip())
    return ""


def aplicar_repost_y_respuesta(valores, *, red, autor, handle, repost_handle, repost_name,
                               reply_handle, reply_name, formato_repost, prefijo_respuesta,
                               plantilla, con_usuario, mi_handle=""):
    """Completa `valores` (los campos de la plantilla de una publicación) con el repost y la
    respuesta según las preferencias. Modifica y devuelve el mismo dict.

    - Formato "original" (por defecto): el de siempre, "[Reposteado por @X] " delante del texto
      (W Social: "[Republicado por @X] ").
    - Formato "repostea": el autor de la fila pasa a ser quien reposteó y el texto empieza por
      "Reposteó el mensaje de Y: ", como se lee en Mastodon un impulso.
    - {reply_to} siempre lleva "En respuesta a @x" cuando se sabe; si la plantilla no tiene ese
      campo y el usuario lo pidió, se antepone al texto.
    """
    respuesta = texto_respuesta_a(reply_handle, reply_name, mi_handle)
    valores["reply_to"] = respuesta
    cuerpo = valores.get("content", "") or ""
    if respuesta and prefijo_respuesta and "{reply_to}" not in (plantilla or ""):
        cuerpo = _("{0}: ").format(respuesta) + cuerpo

    if repost_handle or repost_name:
        es_wsocial = red == "wsocial"
        if formato_repost == FORMATO_REPOST_QUIEN:
            original = _nombre_visible(autor, handle, con_usuario) or _("otra cuenta")
            verbo = (_("Republicó el mensaje de {0}: ") if es_wsocial
                     else _("Reposteó el mensaje de {0}: "))
            cuerpo = verbo.format(original) + cuerpo
            valores["author"] = _nombre_visible(repost_name, repost_handle, con_usuario)
            if repost_handle:
                valores["username"] = f"@{repost_handle}"
        else:
            etiqueta = (_("[Republicado por @{0}] ") if es_wsocial
                        else _("[Reposteado por @{0}] "))
            cuerpo = etiqueta.format(repost_handle or repost_name) + cuerpo
    valores["content"] = cuerpo
    return valores
