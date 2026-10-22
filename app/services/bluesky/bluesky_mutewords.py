# -*- coding: utf-8 -*-
"""Motor PURO de PALABRAS SILENCIADAS de AT Protocol (Bluesky y W Social).

Réplica fiel de `packages/api/src/moderation/mutewords.ts` (matchMuteWords) y de la parte de
`subjects/post.ts` que decide QUÉ textos de una publicación se comparan (matchAllMuteWords):

- texto de la publicación, con sus etiquetas (#tag de los facets y `record.tags`);
- texto alternativo de sus imágenes o de su galería;
- título y descripción de la tarjeta de enlace;
- de la publicación CITADA: su texto, etiquetas, textos alternativos y tarjeta.

Reglas del oficial que se respetan:
- `targets`: "content" se aplica al texto Y a las etiquetas; "tag" solo a las etiquetas.
- `actorTarget = "exclude-following"`: no se aplica a cuentas que sigues.
- `expiresAt`: una palabra caducada ya no se aplica.
- Idiomas sin espacios (ja, zh, ko, th, vi) y palabras de UN carácter: búsqueda por subcadena.
- Frases (con espacio o puntuación): subcadena. Palabras sueltas: por "palabras" separadas por
  espacios, ignorando la puntuación del principio y del final y admitiendo la interna
  ("s@ssy", "super-bad" casa con "super", "bad", "super bad" y "superbad"; "and/or" NO casa
  con "andor").

Sin wx ni red: lo usan el servicio de Bluesky al pintar las listas y, más adelante, W Social
(los lexicons son los mismos). Los datos de la publicación llegan como el diccionario compacto
que guarda `bluesky_moddatos.extraer_datos_post` (columna mod_json), o como parámetros sueltos.
"""
import re
import unicodedata
from datetime import datetime, timezone

# Códigos de idioma que no separan palabras con espacios (o no de forma útil para filtrar):
# con ellos se busca por subcadena, como hace el oficial.
LANGUAGE_EXCEPTIONS = ("ja", "zh", "ko", "th", "vi")

_ESPACIO_RE = re.compile(r"\s", re.UNICODE)


# ---------------------------------------------------------------------------
# Utilidades de texto equivalentes a las expresiones regulares Unicode del oficial
# (\p{P} = cualquier signo de puntuación; Python no lo soporta en `re`).
# ---------------------------------------------------------------------------
def _es_puntuacion(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def _tiene_puntuacion(s: str) -> bool:
    return any(_es_puntuacion(c) for c in s)


def _tiene_espacio_o_puntuacion(s: str) -> bool:
    return any(c.isspace() or _es_puntuacion(c) for c in s)


def _quitar_puntuacion_extremos(s: str) -> str:
    """Equivale a word.replace(/(?:^\\p{P}+|\\p{P}+$)/gu, '')."""
    ini, fin = 0, len(s)
    while ini < fin and _es_puntuacion(s[ini]):
        ini += 1
    while fin > ini and _es_puntuacion(s[fin - 1]):
        fin -= 1
    return s[ini:fin]


def _puntuacion_a_espacios(s: str) -> str:
    """Equivale a replace(/\\p{P}+/gu, ' '): cada tramo de puntuación pasa a UN espacio."""
    salida, en_punt = [], False
    for c in s:
        if _es_puntuacion(c):
            if not en_punt:
                salida.append(" ")
            en_punt = True
        else:
            salida.append(c)
            en_punt = False
    return "".join(salida)


def _partir_por_puntuacion(s: str):
    """Equivale a split(/\\p{P}+/u)."""
    partes, actual, en_punt = [], [], False
    for c in s:
        if _es_puntuacion(c):
            if not en_punt:
                partes.append("".join(actual))
                actual = []
            en_punt = True
        else:
            actual.append(c)
            en_punt = False
    partes.append("".join(actual))
    return partes


def _len16(s: str) -> int:
    """Longitud como en JavaScript (unidades UTF-16): el oficial compara longitudes así."""
    return len(s.encode("utf-16-le")) // 2


def _g(obj, *nombres, default=None):
    """Lee el primer atributo/clave presente (modelos de atproto o diccionarios)."""
    for n in nombres:
        if obj is None:
            return default
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
        elif hasattr(obj, n):
            return getattr(obj, n)
    return default


# ---------------------------------------------------------------------------
# Palabras silenciadas (app.bsky.actor.defs#mutedWord)
# ---------------------------------------------------------------------------
def normalizar_palabra(w) -> dict:
    """Una palabra silenciada (modelo de atproto o dict, en camelCase o snake_case) como dict
    uniforme {value, targets, actorTarget, expiresAt, id}. Sin actorTarget = "all" (igual que
    el oficial al leer las preferencias)."""
    if isinstance(w, str):
        return {"value": w, "targets": ["content", "tag"], "actorTarget": "all",
                "expiresAt": "", "id": ""}
    targets = _g(w, "targets", default=None) or []
    return {
        "value": str(_g(w, "value", default="") or ""),
        "targets": [str(t) for t in targets],
        "actorTarget": str(_g(w, "actorTarget", "actor_target", default="") or "all"),
        "expiresAt": str(_g(w, "expiresAt", "expires_at", default="") or ""),
        "id": str(_g(w, "id", default="") or ""),
    }


def _a_datetime(valor):
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def caducada(palabra, ahora=None) -> bool:
    """True si la palabra tiene fecha de caducidad y ya ha pasado."""
    exp = _a_datetime(normalizar_palabra(palabra).get("expiresAt"))
    if exp is None:
        return False
    ahora = _a_datetime(ahora) or datetime.now(timezone.utc)
    return exp < ahora


def tags_de_facets(facets) -> list:
    """Etiquetas (#tag) de los facets de un registro (modelos de atproto o dicts)."""
    salida = []
    for facet in (facets or []):
        for feat in (_g(facet, "features", default=None) or []):
            tipo = str(_g(feat, "py_type", "$type", default="") or "")
            tag = _g(feat, "tag", default=None)
            if tag and (tipo.endswith("#tag") or not tipo):
                salida.append(str(tag))
    return salida


def coincidencias(palabras, texto, tags=None, idiomas=None, sigue_al_autor=False,
                  ahora=None) -> list:
    """matchMuteWords del oficial. Devuelve la lista de coincidencias
    [{"word": palabra_normalizada, "predicate": texto_que_casó}] (vacía si no hay).

    - palabras: palabras silenciadas (modelos, dicts o cadenas).
    - texto: el texto a comprobar.
    - tags: etiquetas de la publicación (de los facets y de record.tags), sin '#'.
    - idiomas: record.langs (solo cuenta el primero, como en el oficial).
    - sigue_al_autor: si sigues a la cuenta (author.viewer.following)."""
    idiomas = list(idiomas or [])
    excepcion = (idiomas[0] if idiomas else "") in LANGUAGE_EXCEPTIONS
    tags_min = [str(t).lower() for t in (tags or [])]
    texto_post = (texto or "").lower()
    salida = []

    for cruda in (palabras or []):
        w = normalizar_palabra(cruda)
        muted = w["value"].lower()
        if not muted:
            continue
        if caducada(w, ahora):
            continue
        if w["actorTarget"] == "exclude-following" and sigue_al_autor:
            continue
        # "content" se aplica también a las etiquetas (y "tag" solo a ellas).
        if muted in tags_min:
            salida.append({"word": w, "predicate": w["value"]})
            continue
        if "content" not in w["targets"]:
            continue
        # Un solo carácter o idioma sin espacios: subcadena.
        if (_len16(muted) == 1 or excepcion) and muted in texto_post:
            salida.append({"word": w, "predicate": w["value"]})
            continue
        if _len16(muted) > _len16(texto_post):
            continue
        if muted == texto_post:
            salida.append({"word": w, "predicate": w["value"]})
            continue
        # Frase con espacio o puntuación: subcadena.
        if _tiene_espacio_o_puntuacion(muted) and muted in texto_post:
            salida.append({"word": w, "predicate": w["value"]})
            continue
        # Palabra a palabra.
        for palabra in _ESPACIO_RE.split(texto_post):
            if palabra == muted:
                salida.append({"word": w, "predicate": palabra})
                break
            recortada = _quitar_puntuacion_extremos(palabra)
            if muted == recortada:
                salida.append({"word": w, "predicate": palabra})
                break
            if _len16(muted) > _len16(recortada):
                continue
            if _tiene_puntuacion(recortada):
                # "and/or" no debe casar con "Andor": la barra corta la comparación.
                if "/" in recortada:
                    break
                espaciada = _puntuacion_a_espacios(recortada)
                if espaciada == muted:
                    salida.append({"word": w, "predicate": palabra})
                    break
                contigua = _ESPACIO_RE.sub("", espaciada)
                if contigua == muted:
                    salida.append({"word": w, "predicate": palabra})
                    break
                if any(parte == muted for parte in _partir_por_puntuacion(recortada)):
                    salida.append({"word": w, "predicate": palabra})
                    break
    return salida


def hay_palabra(palabras, texto, **kw) -> bool:
    return bool(coincidencias(palabras, texto, **kw))


# ---------------------------------------------------------------------------
# Publicación completa (matchAllMuteWords de subjects/post.ts)
# ---------------------------------------------------------------------------
def palabras_de_post(datos: dict, palabras, ahora=None) -> list:
    """Coincidencias de las palabras silenciadas en una publicación completa, en el MISMO orden
    de comprobación que el oficial (se devuelve la primera fuente que casa).

    `datos` es el diccionario compacto de `bluesky_moddatos` (columna mod_json):
      tx (texto), t (etiquetas), lg (idiomas), f (sigues al autor),
      alt (alt de sus imágenes/galería), ext ("título descripción" de su tarjeta),
      malt / mext (imágenes y tarjeta del recordWithMedia propio: el oficial las compara con
      el autor CITADO), q (la cita: tx, t, lg, f, alt, ext, ralt, rext).
    Devuelve [] si no hay coincidencias."""
    if not palabras or not datos:
        return []
    lg = datos.get("lg") or []
    sigue = bool(datos.get("f"))
    m = coincidencias(palabras, datos.get("tx") or "", tags=datos.get("t") or [],
                      idiomas=lg, sigue_al_autor=sigue, ahora=ahora)
    if m:
        return m
    for alt in (datos.get("alt") or []):
        m = coincidencias(palabras, alt or "", idiomas=lg, sigue_al_autor=sigue, ahora=ahora)
        if m:
            return m
    q = datos.get("q") or None
    if q and not q.get("blk"):
        sigue_q = bool(q.get("f"))
        lg_q = q.get("lg") or []
        m = coincidencias(palabras, q.get("tx") or "", tags=q.get("t") or [], idiomas=lg_q,
                          sigue_al_autor=sigue_q, ahora=ahora)
        if m:
            return m
        for alt in (q.get("alt") or []):
            m = coincidencias(palabras, alt or "", idiomas=lg_q, sigue_al_autor=sigue_q,
                              ahora=ahora)
            if m:
                return m
        for ext in ([q.get("ext")] if q.get("ext") else []) + \
                   ([q.get("rext")] if q.get("rext") else []):
            m = coincidencias(palabras, ext, idiomas=[], sigue_al_autor=sigue_q, ahora=ahora)
            if m:
                return m
        for alt in (q.get("ralt") or []):
            m = coincidencias(palabras, alt or "", idiomas=lg_q, sigue_al_autor=sigue_q,
                              ahora=ahora)
            if m:
                return m
        # Multimedia del recordWithMedia PROPIO: el oficial la compara con el autor citado y
        # los idiomas de la publicación.
        for alt in (datos.get("malt") or []):
            m = coincidencias(palabras, alt or "", idiomas=lg, sigue_al_autor=sigue_q,
                              ahora=ahora)
            if m:
                return m
        if datos.get("mext"):
            m = coincidencias(palabras, datos["mext"], idiomas=[], sigue_al_autor=sigue_q,
                              ahora=ahora)
            if m:
                return m
    elif datos.get("ext"):
        m = coincidencias(palabras, datos["ext"], idiomas=[], sigue_al_autor=sigue, ahora=ahora)
        if m:
            return m
    return []


def valores_coincidentes(coinc) -> list:
    """Las palabras silenciadas (su valor, sin repetir) de una lista de coincidencias."""
    salida = []
    for c in (coinc or []):
        v = (c.get("word") or {}).get("value", "")
        if v and v not in salida:
            salida.append(v)
    return salida
