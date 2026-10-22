# -*- coding: utf-8 -*-
"""
Configuración de red de la aplicación: aplica el proxy elegido en Preferencias
(como TWBlue) mediante variables de entorno estándar, que respetan requests
(Mastodon.py), httpx (atproto/Bluesky) y urllib (feedparser/RSS).
"""

import os
import builtins
import logging
from urllib.parse import quote
from .config_manager import config

log = logging.getLogger(__name__)


def humanizar_error(err) -> str:
    """Convierte una excepción/mensaje técnico (sobre todo de red) en un texto claro y breve en
    español, para no mostrar al usuario cosas como 'HTTPSConnectionPool... Read timed out'.
    Si no parece un error de red conocido, devuelve el texto tal cual."""
    if not err:
        return ""
    texto = str(err).strip()
    low = texto.lower()
    indicadores_red = (
        "timed out", "timeout", "could not complete request", "connectionpool",
        "max retries", "failed to establish", "connection aborted", "connection reset",
        "connection refused", "name or service not known", "getaddrinfo", "temporarily",
        "remotedisconnected", "newconnectionerror", "ssl", "handshake",
    )
    _t = getattr(builtins, "_", lambda s: s)
    if any(s in low for s in indicadores_red):
        return _t("No se pudo conectar con el servidor: puede ir lento o haber un problema de "
                  "conexión. Inténtalo de nuevo en un momento.")
    return texto

# ---------------------------------------------------------------------------
# Sesión HTTP compartida POR HILO (pooling de conexiones TLS/TCP).
#
# Las utilidades que hacían requests.get/post sueltos abrían y cerraban una
# conexión TLS por petición (sin keep-alive). Esta fábrica da una
# requests.Session por hilo con: pool de conexiones, cabecera User-Agent propia,
# y REINTENTOS solo para métodos idempotentes (GET/HEAD) ante 429/5xx respetando
# Retry-After. Las publicaciones (POST/PUT...) NO se reintentan nunca de forma
# automática: repetir una acción mutable puede duplicarla.
#
# Uso:  from app.core.net import http_session
#       r = http_session().get(url, timeout=TIMEOUT_HTTP)
# ---------------------------------------------------------------------------
import threading as _threading

_sesion_local = _threading.local()

# Timeout (conexión, lectura) recomendado para peticiones sueltas.
TIMEOUT_HTTP = (10, 30)

USER_AGENT = "SocialRSS/AccessibleSuite (+https://xebolax.synology.me/SocialRSS)"


def http_session():
    """Devuelve la requests.Session compartida de ESTE hilo (la crea la primera vez).
    No compartir una sesión entre hilos: requests no lo garantiza; por eso es thread-local."""
    ses = getattr(_sesion_local, "ses", None)
    if ses is not None:
        return ses
    import requests
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
        reintentos = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),  # solo idempotentes
            respect_retry_after_header=True,
        )
    except Exception:
        reintentos = 0
    ses = requests.Session()
    ses.headers["User-Agent"] = USER_AGENT
    adaptador = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=reintentos)
    ses.mount("https://", adaptador)
    ses.mount("http://", adaptador)
    # El proxy de Preferencias ya llega por variables de entorno (trust_env=True por defecto).
    _sesion_local.ses = ses
    return ses


# ---------------------------------------------------------------------------
# DESCARGA SEGURA de URLs NO CONFIABLES (auditoría Bluesky 6.5): para las tarjetas
# de vista previa y cualquier descubrimiento sobre URLs que llegan en contenido
# remoto. Un fetch directo permitía SSRF (la URL podía apuntar a loopback, red
# privada o metadata local), cuerpos sin límite en memoria y esperas largas.
# ---------------------------------------------------------------------------

def _ip_privada(host: str) -> bool:
    """True si el host resuelve (algún registro) a una IP privada, loopback,
    link-local, multicast o reservada. Ante un fallo de resolución: True (rechazar)."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return True
    return False


def descargar_url_segura(url: str, max_bytes: int = 2 * 1024 * 1024,
                         timeout=(5, 8), max_redirecciones: int = 3):
    """Descarga una URL EXTERNA con defensas: solo http/https, rechazo de destinos
    privados/loopback (revalidado en CADA redirección), timeouts separados de
    conexión y lectura, y cuerpo leído EN STREAMING con tope de bytes.

    Devuelve (bytes, content_type) o lanza ValueError con el motivo."""
    from urllib.parse import urlparse, urljoin
    actual = url
    for _ in range(max_redirecciones + 1):
        p = urlparse(actual)
        if p.scheme not in ("http", "https"):
            raise ValueError(f"Esquema no permitido: {p.scheme}")
        if not p.hostname or _ip_privada(p.hostname):
            raise ValueError("La URL apunta a una dirección privada o no resoluble.")
        ses = http_session()
        resp = ses.get(actual, timeout=timeout, stream=True, allow_redirects=False)
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                destino = resp.headers.get("Location")
                if not destino:
                    raise ValueError("Redirección sin destino.")
                actual = urljoin(actual, destino)
                continue
            resp.raise_for_status()
            declarado = int(resp.headers.get("Content-Length") or 0)
            if declarado > max_bytes:
                raise ValueError("El contenido declarado supera el tamaño permitido.")
            total = 0
            trozos = []
            for trozo in resp.iter_content(chunk_size=65536):
                total += len(trozo)
                if total > max_bytes:
                    raise ValueError("El contenido supera el tamaño permitido.")
                trozos.append(trozo)
            return b"".join(trozos), (resp.headers.get("Content-Type") or "").lower()
        finally:
            resp.close()
    raise ValueError("Demasiadas redirecciones.")


# Variables de entorno que controlan el proxy en las librerías HTTP usadas.
_VARS = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
         "http_proxy", "https_proxy", "all_proxy"]


def _limpiar_proxy_env():
    for v in _VARS:
        os.environ.pop(v, None)


def aplicar_configuracion_proxy():
    """Aplica (o quita) el proxy según la configuración. Devuelve la URL del proxy
    aplicada, o cadena vacía si no hay proxy activo. Es idempotente: puede llamarse
    al arrancar y de nuevo tras guardar las Preferencias."""
    if not config.get("proxy", "enabled", False):
        _limpiar_proxy_env()
        return ""

    tipo = (config.get("proxy", "type", "http") or "http").strip().lower()
    servidor = (config.get("proxy", "server", "") or "").strip()
    if not servidor:
        _limpiar_proxy_env()
        return ""
    try:
        puerto = int(config.get("proxy", "port", 8080) or 8080)
    except (TypeError, ValueError):
        puerto = 8080
    usuario = (config.get("proxy", "user", "") or "").strip()
    clave = config.get("proxy", "password", "") or ""

    # socks5 requiere el esquema socks5h para resolver el DNS a través del proxy.
    esquema = {"http": "http", "https": "https", "socks5": "socks5h"}.get(tipo, "http")
    auth = ""
    if usuario:
        auth = "{0}:{1}@".format(quote(usuario, safe=""), quote(str(clave), safe=""))
    url = "{0}://{1}{2}:{3}".format(esquema, auth, servidor, puerto)

    for v in _VARS:
        os.environ[v] = url
    log.info("Proxy aplicado: %s://%s:%s", esquema, servidor, puerto)
    return url
