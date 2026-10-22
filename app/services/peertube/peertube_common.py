# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio PeerTube: imports, cliente httpx (carga diferida), constantes y
helpers.

PeerTube se integra por su **API REST** (JSON sobre HTTPS) con `httpx`. Se usa el cliente SÍNCRONO
de httpx porque encaja de forma natural con `task_manager` (hilos de fondo con prioridad baja), igual
que el resto de servicios de la app, sin mantener un bucle asyncio por cuenta.
"""
import re
import json  # noqa: F401  (reexportado para los mixins)
import logging
import importlib.util
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional  # noqa: F401  (reexportados para los mixins)

log = logging.getLogger(__name__)

# Carga DIFERIDA de httpx: comprobar si está instalado es barato (find_spec, no lo importa). El
# módulo se importa de verdad la primera vez que se crea un cliente. httpx es dependencia de base,
# así que normalmente siempre está.
HAS_HTTPX = importlib.util.find_spec("httpx") is not None
if not HAS_HTTPX:  # pragma: no cover - entorno sin httpx
    log.warning("httpx no está disponible. El servicio PeerTube quedará inactivo.")

# Prefijo común de la API REST de PeerTube.
API_BASE = "/api/v1"

# Tiempos de espera por defecto (segundos): conexión / lectura / escritura / pool.
TIMEOUT_CONEXION = 10.0
TIMEOUT_LECTURA = 30.0
TIMEOUT_ESCRITURA = 30.0
TIMEOUT_POOL = 10.0

# Reintentos por defecto para errores temporales (5xx / red) y backoff exponencial base (segundos).
REINTENTOS_DEFECTO = 3
BACKOFF_BASE = 0.5
# Cota superior de espera al respetar Retry-After o el backoff (evita bloqueos larguísimos).
ESPERA_MAXIMA = 30.0


def crear_cliente_httpx(**kwargs):
    """Fábrica que crea un `httpx.Client` importándolo de forma diferida.

    Valores por defecto seguros: verificación TLS activada, redirecciones seguidas y HTTP/2 si está
    disponible. Los `kwargs` sobreescriben lo que haga falta (base_url, headers, timeout...)."""
    import httpx
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("verify", True)  # NUNCA desactivar la validación de certificados
    try:
        return httpx.Client(http2=True, **kwargs)
    except Exception:
        # http2 requiere el paquete opcional 'h2'; si no está, se usa HTTP/1.1 sin romper.
        return httpx.Client(**kwargs)


def httpx_excepciones():
    """Devuelve el módulo `httpx` (import diferido) para clasificar errores por tipo. None si falta."""
    try:
        import httpx
        return httpx
    except Exception:  # pragma: no cover - entorno sin httpx
        return None


def user_agent() -> str:
    """User-Agent descriptivo de SocialRSS (identifica el cliente ante la instancia)."""
    try:
        from app.core.version import APP_VERSION
        ver = APP_VERSION
    except Exception:
        ver = "1.0"
    return f"SocialRSS/{ver} (+https://github.com/xebolax; PeerTube client; httpx)"


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"https?://[^\s)]+")


def _g(obj: Any, *nombres: str, default: Any = None) -> Any:
    """Lee el primer campo/atributo presente de un dict o de un objeto (tolerante a ausencias)."""
    for n in nombres:
        if obj is None:
            return default
        try:
            if isinstance(obj, dict):
                if n in obj:
                    return obj[n]
            elif hasattr(obj, n):
                return getattr(obj, n)
        except Exception:
            continue
    return default


def _ahora_iso() -> str:
    """Fecha-hora actual en ISO-8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


def normalizar_base_url(url: str) -> str:
    """Normaliza la URL de una instancia a su forma base `https://host` (sin barra ni ruta final).

    - Añade `https://` si falta el esquema.
    - Fuerza HTTPS (PeerTube exige TLS; nunca se habla en claro).
    - Quita espacios, la barra final y cualquier ruta/query.
    Lanza ValueError si no hay host válido. La detección real de que es PeerTube y su versión se hace
    aparte (fase de instancias); aquí solo se sanea la URL para construir el cliente HTTP."""
    if not url or not url.strip():
        raise ValueError("URL de instancia vacía")
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    try:
        from urllib.parse import urlsplit
        partes = urlsplit(u)
        host = partes.hostname
        if not host:
            raise ValueError(f"URL de instancia sin host: {url}")
        # Se conserva el puerto si lo hubiera (instancias de desarrollo).
        puerto = f":{partes.port}" if partes.port else ""
        return f"https://{host}{puerto}"
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"URL de instancia no válida: {url} ({e})")
