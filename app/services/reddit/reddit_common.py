# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio Reddit: imports, PRAW (carga diferida), constantes y helpers.

Reddit se integra con la biblioteca oficial PRAW (síncrona). La API de PRAW y de Async PRAW es
idéntica; se usa la síncrona porque encaja de forma natural con `task_manager` (hilos de fondo con
prioridad baja), sin necesidad de mantener un bucle asyncio propio por cuenta.
"""
import json
import re
import logging
import importlib.util
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.database import db
from app.core.security import security
from app.core.errors import RedditServiceError
from app.core.task_manager import task_manager
from app.core.config_manager import config
from app.core.models import RedditPost, RedditComment

log = logging.getLogger(__name__)

# Carga DIFERIDA de PRAW: comprobar si está instalado es barato (find_spec, no importa). El módulo
# se importa de verdad la primera vez que se crea un `Reddit(...)`. Así el arranque no paga el coste
# de importar PRAW si el usuario nunca usa Reddit.
HAS_PRAW = importlib.util.find_spec("praw") is not None
if not HAS_PRAW:  # pragma: no cover - entorno sin praw
    log.warning("praw no está disponible. El servicio Reddit quedará inactivo.")


def Reddit(*args, **kwargs):  # type: ignore
    """Fábrica que crea una instancia `praw.Reddit` importándola de forma diferida.

    Desactiva la comprobación de actualizaciones de PRAW (evita una petición de red extra y el
    aviso de 'versión desatendida' en cada arranque)."""
    import praw
    kwargs.setdefault("check_for_updates", False)
    return praw.Reddit(*args, **kwargs)


def praw_exceptions():
    """Devuelve el módulo `prawcore.exceptions` (import diferido) para clasificar errores por CÓDIGO
    y no por str(). Devuelve None si prawcore no está disponible."""
    try:
        import prawcore.exceptions as _exc
        return _exc
    except Exception:
        return None


def praw_reddit_exceptions():
    """Devuelve el módulo `praw.exceptions` (RedditAPIException, etc.) o None."""
    try:
        import praw.exceptions as _exc
        return _exc
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Identidad OAuth de la aplicación
# ---------------------------------------------------------------------------
# User-Agent OBLIGATORIO y descriptivo (política de Reddit). Formato recomendado:
#   <plataforma>:<app ID>:<versión> (by /u/<usuario>)
# Un user-agent genérico (python-requests…) recibe límites drásticos. Incluir la versión permite a
# Reddit bloquear versiones concretas si hiciera falta.
def user_agent() -> str:
    try:
        from app.core.version import APP_VERSION
        ver = APP_VERSION
    except Exception:
        ver = "1.0"
    return f"windows:SocialRSS:v{ver} (by /u/xebolax)"


# Redirect del flujo de código de autorización. DEBE coincidir EXACTAMENTE con el "redirect uri"
# registrado en la app de reddit.com/prefs/apps. Se usa un puerto FIJO (Reddit valida el redirect
# uri por igualdad) sobre el que se levanta un servidor local efímero durante el alta.
REDDIT_REDIRECT_PORT = 8080
REDDIT_REDIRECT_URI = f"http://localhost:{REDDIT_REDIRECT_PORT}"

# Scopes solicitados en el alta. Se pide el conjunto COMPLETO (con duration="permanent") para que el
# mismo refresh token sirva en todas las fases (lectura, publicación, mensajería y moderación) sin
# tener que volver a autorizar cuando se añadan funciones de moderación/wiki/live.
SCOPES = [
    "identity", "edit", "flair", "history", "modconfig", "modflair", "modlog", "modposts",
    "modwiki", "mysubreddits", "privatemessages", "read", "report", "save", "submit", "subscribe",
    "vote", "wikiedit", "wikiread", "account", "creddits", "livemanage", "modcontributors",
    "modmail", "modothers", "modself", "modtraffic", "structuredstyles", "modnote",
]


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"https?://[^\s)]+")


def _g(obj: Any, *nombres: str, default: Any = None) -> Any:
    """Lee el primer atributo presente de un objeto PRAW (tolerante a atributos ausentes)."""
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


# Marcadores de contenido borrado/eliminado en Reddit. Por la Data API Terms, el contenido
# borrado NO debe conservarse en la caché local: se purga en cuanto se detecta.
_MARCAS_BORRADO = ("[deleted]", "[removed]")


def es_cuerpo_borrado(texto: str) -> bool:
    """True si el cuerpo (selftext/body) corresponde a contenido borrado o eliminado."""
    return (texto or "").strip().lower() in _MARCAS_BORRADO


def _nombre_autor(obj: Any) -> str:
    """Nombre del autor de un post/comentario PRAW; "[deleted]" si la cuenta ya no existe."""
    autor = _g(obj, "author")
    if autor is None:
        return "[deleted]"
    try:
        return getattr(autor, "name", None) or str(autor)
    except Exception:
        return "[deleted]"
