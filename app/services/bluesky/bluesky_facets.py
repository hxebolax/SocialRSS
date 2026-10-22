# -*- coding: utf-8 -*-
"""Detección de facets de texto enriquecido de Bluesky (auditoría 6.4).

Parser PURO (sin red ni modelos de atproto), calcado del comportamiento del cliente
oficial, en una sola fase: detectar (enlaces, dominios desnudos, menciones y hashtags),
normalizar la puntuación final, validar, ordenar por bytes y descartar solapes. Al ser
puro es directamente testeable con los casos de la auditoría (emoji antes del enlace,
paréntesis, coma y punto final, correos, '@' dentro de una ruta, Unicode, facets
adyacentes...). Los offsets son SIEMPRE en bytes UTF-8, como exige el lexicón.
"""
import re

# Enlaces absolutos. La puntuación final se recorta después (una URL al final de una
# frase no debe llevarse el punto o la coma).
_RE_URL = re.compile(r"https?://[^\s]+")

# Dominios desnudos (bsky.app/algo, ejemplo.com...). Para no convertir en enlace
# abreviaturas tipo "p.ej" o "etc.", el sufijo debe estar en una lista de TLD comunes
# (el cliente oficial usa la Public Suffix List; esta lista corta cubre lo cotidiano).
_TLDS = (
    "com|org|net|edu|gov|info|io|me|app|dev|social|blue|sky|es|eu|fr|de|it|pt|br|mx|ar|"
    "cl|co|uk|us|ca|au|nz|jp|gg|tv|fm|am|xyz|online|site|tech|news|wiki|blog|chat|club|"
    "live|one|top|lol|art|zone|cloud|host|network|digital|media|world|life|space|gay|foo"
)
_RE_DOMINIO = re.compile(
    r"(?:^|(?<=[\s(\[]))((?:[a-zA-Z0-9][a-zA-Z0-9-]*\.)+(?:" + _TLDS + r")(?:/[^\s]*)?)(?![\w@])",
    re.IGNORECASE)

# Menciones: exigen un LÍMITE antes de la arroba (inicio, espacio o puntuación de
# apertura). Así "correo@dominio.com" o "https://web.com/@usuario" no cuentan como
# mención (además, los solapes con un enlace se descartan al final).
_RE_MENCION = re.compile(
    r"(?:^|(?<=[\s(\[{'\"]))@([a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,})")

# Hashtags: '#' al inicio o tras espacio (no captura el fragmento de una URL).
_RE_TAG = re.compile(r"(?:^|(?<=\s))#([^\s#]+)")

# Puntuación que una URL/dominio no debe llevarse si va al final de la frase.
_PUNTUACION_FINAL = ".,;:!?'\"…»”’)]}>"


def _recortar_puntuacion(url: str) -> str:
    """Quita la puntuación FINAL de una URL, con balance de paréntesis: en
    'wikipedia.org/Perl_(lenguaje)' el paréntesis de cierre forma parte del enlace;
    en '(mira bsky.app)' no."""
    while url and url[-1] in _PUNTUACION_FINAL:
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        if url[-1] == "]" and url.count("[") >= url.count("]"):
            break
        url = url[:-1]
    return url


def detectar_facets(texto: str) -> list:
    """Devuelve los facets detectados en el texto, ya validados, sin solapes y ordenados
    por posición. Cada elemento: {"tipo": "link"|"mention"|"tag", "byte_start",
    "byte_end", "valor"} — valor es la URL completa (con https:// si era un dominio
    desnudo), el handle SIN la arroba, o el tag sin '#'."""
    if not texto:
        return []
    candidatos = []  # (prioridad, ini_char, fin_char, tipo, valor)

    # 1) Enlaces absolutos (prioridad máxima: ganan a menciones/tags que pisen dentro).
    for m in _RE_URL.finditer(texto):
        url = _recortar_puntuacion(m.group(0))
        if len(url) <= len("https://x"):
            continue
        candidatos.append((0, m.start(), m.start() + len(url), "link", url))

    # 2) Menciones (@handle con límite anterior; el handle no termina en '.' ni '-').
    for m in _RE_MENCION.finditer(texto):
        handle = m.group(1).rstrip(".-")
        if not handle or "." not in handle:
            continue
        fin = m.start() + 1 + len(handle)  # 1 por la arroba
        candidatos.append((1, m.start(), fin, "mention", handle))

    # 3) Hashtags (sin puntuación final, no solo números, máximo 64 caracteres).
    for m in _RE_TAG.finditer(texto):
        tag = m.group(1).rstrip(_PUNTUACION_FINAL + "…").strip()
        if not tag or tag.isdigit() or len(tag) > 64:
            continue
        candidatos.append((2, m.start(), m.start() + 1 + len(tag), "tag", tag))

    # 4) Dominios desnudos (menor prioridad: un enlace absoluto ya los cubre).
    for m in _RE_DOMINIO.finditer(texto):
        crudo = _recortar_puntuacion(m.group(1))
        if not crudo or "." not in crudo:
            continue
        candidatos.append((3, m.start(1), m.start(1) + len(crudo), "link", "https://" + crudo))

    # Resolución de solapes: por prioridad y, a igualdad, el más largo. Un facet solo
    # entra si no pisa a ninguno ya aceptado (el lexicón prohíbe solapes).
    candidatos.sort(key=lambda c: (c[0], -(c[2] - c[1]), c[1]))
    aceptados = []
    for prio, ini, fin, tipo, valor in candidatos:
        if any(not (fin <= a[0] or ini >= a[1]) for a in aceptados):
            continue
        aceptados.append((ini, fin, tipo, valor))

    # A bytes UTF-8 y orden por posición (imprescindible con emoji y multibyte).
    aceptados.sort(key=lambda a: a[0])
    resultado = []
    for ini, fin, tipo, valor in aceptados:
        resultado.append({
            "tipo": tipo,
            "byte_start": len(texto[:ini].encode("utf-8")),
            "byte_end": len(texto[:fin].encode("utf-8")),
            "valor": valor,
        })
    return resultado


def primera_url(texto: str):
    """La primera URL del texto (ya sin puntuación final), para la tarjeta de vista
    previa; None si no hay."""
    for f in detectar_facets(texto or ""):
        if f["tipo"] == "link":
            return f["valor"]
    return None
