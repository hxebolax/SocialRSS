# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio Matrix: carga diferida de matrix-nio, MatrixError y helpers.

Regla de arranque: nio NO se importa a nivel de módulo (carga diferida, como pydle/Kurigram).
La referencia normativa de protocolo está en local:
Consulta_SocialRSS/MATRIX/repos_matrix/matrix-spec (client-server-api + OpenAPI por endpoint).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext (los eventos llevan textos para la UI)

import asyncio
import logging
import threading
import importlib.util
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Callable, List

from app.core.database import db
from app.core.security import security
from app.core.event_bus import event_bus
from app.core.errors import ServiceError

log = logging.getLogger(__name__)

# ¿Está instalada la librería? (find_spec no la importa: no cuesta arranque)
HAS_NIO = importlib.util.find_spec("nio") is not None
if not HAS_NIO:  # pragma: no cover - depende del entorno
    log.info("matrix-nio no disponible. Matrix quedará deshabilitado hasta instalar dependencias.")


class MatrixError(ServiceError):
    """Error específico del servicio de Matrix."""
    pass


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ts_a_iso(origin_server_ts) -> str:
    """Convierte el timestamp de Matrix (milisegundos epoch) a ISO 8601 UTC."""
    try:
        return datetime.fromtimestamp(int(origin_server_ts) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return ahora_iso()


def hora_local(iso_str, con_fecha: bool = False) -> str:
    """Convierte un ISO 8601 (guardado en UTC) a la hora LOCAL del equipo para MOSTRARLA.
    Antes se cortaba el ISO en crudo ('[11:16]'), lo que enseñaba la hora UTC (p. ej. 2 h menos
    en la España peninsular en verano). Aquí se pasa a la zona horaria del sistema."""
    try:
        dt = datetime.fromisoformat((iso_str or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()  # zona horaria local del sistema operativo
        return local.strftime("%d/%m/%Y %H:%M" if con_fecha else "%H:%M")
    except Exception:
        return (iso_str or "")[11:16] if not con_fecha else (iso_str or "")[:16].replace("T", " ")


def q_seg(s) -> str:
    """Codifica UN segmento de ruta con quote(safe='') (MX-020): a diferencia del quote() por
    defecto (safe='/'), NO deja pasar la barra '/', así un identificador con caracteres reservados
    (user_id, room_id, alias, event_id, media_id...) nunca puede alterar la ruta ni añadir segmentos."""
    import urllib.parse
    return urllib.parse.quote(str(s if s is not None else ""), safe="")


def db_id_sala(account_id: int, room_id: str) -> str:
    """Id de fila de matrix_salas: 'cuenta|room_id' (separador '|': los room_id llevan ':' y '!',
    mismo criterio que los DID de Bluesky y los canales de IRC)."""
    return f"{account_id}|{room_id or ''}"


def _host_de_url(url: str) -> str:
    """Devuelve el host (sin puerto ni corchetes IPv6) de una URL, o '' si no se puede."""
    try:
        import urllib.parse
        h = urllib.parse.urlsplit(url).hostname or ""
        return h.strip("[]")
    except Exception:
        return ""


def _es_loopback_host(host: str) -> bool:
    """True si el host es la máquina local (localhost / 127.0.0.0/8 / ::1). El loopback NO sufre
    downgrade de transporte por la red, así que http:// sobre loopback se admite siempre."""
    import ipaddress
    h = (host or "").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def permitir_http_inseguro() -> bool:
    """Opción de DESARROLLO explícita (config matrix.permitir_http_inseguro). Fuera de ella, Matrix
    exige HTTPS para el homeserver y los endpoints, para no filtrar contraseña/tokens en claro ni
    permitir un downgrade de transporte por un intermediario."""
    try:
        from app.core.config_manager import config
        return bool(config.get("matrix", "permitir_http_inseguro", False))
    except Exception:
        return False


def normalizar_homeserver(texto: str) -> str:
    """Normaliza lo que escribe el usuario ('matrix.org', 'https://matrix.org/') a una URL base.
    Exige HTTPS: un http:// remoto se rechaza (robo de credenciales / downgrade). Se admite http
    solo contra la máquina local (loopback) o si se ha activado el modo de desarrollo inseguro."""
    t = (texto or "").strip().rstrip("/")
    if not t:
        return ""
    if t.startswith("http://"):
        host = _host_de_url(t)
        if _es_loopback_host(host) or permitir_http_inseguro():
            return t
        raise MatrixError(
            "Por seguridad, el servidor de Matrix debe usar HTTPS (https://). Escribe la dirección "
            "sin 'http://' o con 'https://'.")
    if not t.startswith("https://"):
        t = "https://" + t
    return t


def validar_endpoint_descubierto(url: str, etiqueta: str = "endpoint") -> str:
    """Valida una URL DESCUBIERTA por el servidor (.well-known, auth_metadata, endpoints OAuth): debe
    ser una URL https absoluta y NO apuntar a direcciones internas de metadatos/enlace-local
    (anti-SSRF: un servidor malicioso podría delegar sus endpoints a 169.254.169.254 u otra dirección
    interna del equipo). Devuelve la URL si es válida; lanza MatrixError si no.

    Se admite loopback solo en modo de desarrollo inseguro (homeserver local con MAS de pruebas)."""
    import ipaddress
    import urllib.parse
    u = (url or "").strip()
    if not u:
        raise MatrixError(f"El servidor no indicó un {etiqueta} válido.")
    partes = urllib.parse.urlsplit(u)
    dev = permitir_http_inseguro()
    if partes.scheme != "https" and not (dev and partes.scheme == "http"):
        raise MatrixError(f"El {etiqueta} del servidor no usa HTTPS; se cancela por seguridad.")
    host = (partes.hostname or "").strip("[]")
    if not host:
        raise MatrixError(f"El {etiqueta} del servidor no tiene un host válido.")
    if _es_loopback_host(host):
        if dev:
            return u
        raise MatrixError(f"El {etiqueta} del servidor apunta a la máquina local; se cancela por "
                          "seguridad.")
    # Si el host es una IP literal, bloquear rangos peligrosos directamente. Si es un nombre, se
    # intenta resolver (best-effort) para detectar un DNS que apunte a una dirección interna.
    candidatos = []
    try:
        candidatos.append(ipaddress.ip_address(host))
    except ValueError:
        try:
            import socket
            for info in socket.getaddrinfo(host, partes.port or 443):
                try:
                    candidatos.append(ipaddress.ip_address(info[4][0]))
                except ValueError:
                    pass
        except Exception:
            candidatos = []  # sin resolución: se admite el nombre (nio validará TLS al conectar)
    for ip in candidatos:
        if (ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                or ip.is_loopback):
            raise MatrixError(f"El {etiqueta} del servidor apunta a una dirección interna "
                              f"({ip}); se cancela por seguridad.")
    return u
