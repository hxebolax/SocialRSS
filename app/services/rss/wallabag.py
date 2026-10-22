# -*- coding: utf-8 -*-
"""Integración con Wallabag (servicio libre y autoalojado de "leer más tarde").

Envía la URL de un artículo a la cuenta de Wallabag del usuario para leerlo en cualquier
dispositivo. Solo usa 'requests' (sin dependencias nuevas). Las credenciales se guardan cifradas
con el gestor de secretos de la app; la URL del servidor va en la configuración normal.

Wallabag usa OAuth2 (client_credentials + password): se pide un token con client_id/secret +
usuario/contraseña y luego se crean entradas con ese token. El token se cachea en memoria.
"""
import time
import logging

import requests

from app.core.config_manager import config
from app.core.security import security

log = logging.getLogger(__name__)

_token_cache = {"access_token": "", "expira": 0.0}


def esta_configurado() -> bool:
    """True si hay al menos servidor y credenciales guardadas."""
    servidor = (config.get("rss", "wallabag_url", "") or "").strip()
    cid = security.obtener_credencial("wallabag", "client_id")
    return bool(servidor and cid)


def guardar_config(servidor: str, client_id: str, client_secret: str, usuario: str, password: str):
    """Guarda la configuración de Wallabag (servidor en config; secretos cifrados)."""
    config.set("rss", "wallabag_url", (servidor or "").rstrip("/"))
    security.guardar_credencial("wallabag", "client_id", client_id or "")
    security.guardar_credencial("wallabag", "client_secret", client_secret or "")
    security.guardar_credencial("wallabag", "username", usuario or "")
    security.guardar_credencial("wallabag", "password", password or "")
    _token_cache["access_token"] = ""
    _token_cache["expira"] = 0.0


def _obtener_token() -> str:
    """Devuelve un token de acceso válido (lo pide/renueva si hace falta)."""
    ahora = time.time()
    if _token_cache["access_token"] and _token_cache["expira"] > ahora + 30:
        return _token_cache["access_token"]
    servidor = (config.get("rss", "wallabag_url", "") or "").rstrip("/")
    datos = {
        "grant_type": "password",
        "client_id": security.obtener_credencial("wallabag", "client_id") or "",
        "client_secret": security.obtener_credencial("wallabag", "client_secret") or "",
        "username": security.obtener_credencial("wallabag", "username") or "",
        "password": security.obtener_credencial("wallabag", "password") or "",
    }
    resp = requests.post(f"{servidor}/oauth/v2/token", data=datos, timeout=20)
    resp.raise_for_status()
    j = resp.json()
    _token_cache["access_token"] = j.get("access_token", "")
    _token_cache["expira"] = ahora + int(j.get("expires_in", 3600))
    if not _token_cache["access_token"]:
        raise ValueError("Wallabag no devolvió un token de acceso.")
    return _token_cache["access_token"]


def enviar_articulo(url: str, titulo: str = "") -> bool:
    """Envía una URL a Wallabag (crea una entrada nueva). Lanza excepción si falla."""
    if not url:
        raise ValueError("No hay URL que enviar.")
    servidor = (config.get("rss", "wallabag_url", "") or "").rstrip("/")
    token = _obtener_token()
    payload = {"url": url}
    if titulo:
        payload["title"] = titulo
    resp = requests.post(f"{servidor}/api/entries.json", data=payload,
                         headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return True
