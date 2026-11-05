# -*- coding: utf-8 -*-
"""Longitud de un toot tal y como la cuenta Mastodon (la que valida el servidor), sin interfaz: la usan
el compositor de Windows (wx), el de Linux (GTK) y el servicio al dividir hilos."""
import re
import unicodedata

MASTODON_MAX_CHARS = 500
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
# Mención: @usuario con un @dominio opcional (se descarta el dominio al contar).
_MENTION_RE = re.compile(r'@(\w+)(?:@[\w.-]+)?')


def contar_grafemas(s: str) -> int:
    """Cuenta GRAFEMAS (no puntos de código), como hace Mastodon (each_grapheme_cluster).
    Aproximación de UAX #29 que cubre los casos reales: marcas combinantes, selectores de
    variación, ZWJ (emojis unidos como familias), parejas de banderas y tonos de piel cuentan
    como UN grafema junto a su base. Así un emoji compuesto cuenta 1 y no varios."""
    count = 0
    i = 0
    n = len(s)
    while i < n:
        cp = ord(s[i])
        count += 1  # este carácter inicia un grafema nuevo
        i += 1
        # Bandera: un indicador regional se une con el siguiente indicador regional.
        if 0x1F1E6 <= cp <= 0x1F1FF and i < n and 0x1F1E6 <= ord(s[i]) <= 0x1F1FF:
            i += 1
        # Absorber lo que "extiende" el grafema actual (no cuenta aparte).
        while i < n:
            ncp = ord(s[i])
            cat = unicodedata.category(s[i])
            if (cat in ("Mn", "Mc", "Me") or ncp == 0x200D
                    or 0xFE00 <= ncp <= 0xFE0F          # selectores de variación
                    or 0x1F3FB <= ncp <= 0x1F3FF):       # modificadores de tono de piel
                i += 1
                # Tras un ZWJ, el siguiente emoji se une (tampoco cuenta).
                if ncp == 0x200D and i < n:
                    i += 1
                continue
            break
    return count


def longitud_contable_mastodon(texto: str, cw: str = "") -> int:
    """Devuelve la longitud de un toot tal y como la cuenta Mastodon."""
    t = texto or ""
    # 1) URLs -> 23 caracteres (un marcador sin '@' para no afectar a las menciones).
    t = _URL_RE.sub("x" * 23, t)
    # 2) Menciones -> solo la parte local '@usuario' (se quita '@dominio').
    t = _MENTION_RE.sub(lambda m: "@" + m.group(1), t)
    # El CW cuenta íntegro y se suma al texto ya normalizado; ambos por GRAFEMAS.
    return contar_grafemas(cw or "") + contar_grafemas(t)
