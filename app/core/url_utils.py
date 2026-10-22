# -*- coding: utf-8 -*-
"""Normalización de URLs (auditoría RSS 2026-07-11, RSS-15/RSS-21).

Vive en core (sin dependencias pesadas) porque la usan tanto las migraciones al
arranque como el servicio RSS. La normalización es CONSERVADORA: solo cambia lo
que nunca altera el recurso (esquema/host en minúsculas, puerto por defecto,
fragmento, barra final, parámetros de seguimiento) para poder detectar feeds
duplicados sin falsos positivos.
"""
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# Parámetros de seguimiento que no cambian el recurso (Miniflux hace lo mismo).
_PARAMS_SEGUIMIENTO = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src")


def normalizar_url(url: str) -> str:
    """Devuelve una forma canónica de la URL para comparar duplicados.

    - esquema y host en minúsculas; puerto por defecto (80/443) fuera
    - sin fragmento (#...)
    - sin barra final en la ruta ("/feed/" == "/feed"; "/" == "")
    - sin parámetros de seguimiento (utm_*, fbclid...), resto del query ORDENADO
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        p = urlsplit(url)
        esquema = (p.scheme or "").lower()
        host = (p.hostname or "").lower()
        puerto = p.port
        if puerto and not ((esquema == "http" and puerto == 80)
                           or (esquema == "https" and puerto == 443)):
            host = f"{host}:{puerto}"
        ruta = p.path or ""
        if ruta.endswith("/"):
            ruta = ruta.rstrip("/")
        pares = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not any(k.lower().startswith(s) or k.lower() == s.rstrip("_")
                            for s in _PARAMS_SEGUIMIENTO)]
        pares.sort()
        return urlunsplit((esquema, host, ruta, urlencode(pares), ""))
    except Exception:
        return url
