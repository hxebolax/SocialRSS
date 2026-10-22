# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio Bluesky: imports, atproto, constantes y helpers de módulo.
"""
import json
import logging
import re
import importlib.util
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.database import db
from app.core.security import security
from app.core.errors import BlueskyServiceError
from app.core.task_manager import task_manager
from app.core.models import BlueskyPost
from app.core.config_manager import config

log = logging.getLogger(__name__)

# Carga DIFERIDA de atproto: importarlo al arrancar costaba ~4 s (atproto_client.models define
# miles de modelos) y solo hace falta al USAR Bluesky. Aquí solo comprobamos (barato) si está
# instalado; el módulo se importa de verdad la primera vez que se accede a `models.X` o se crea un
# `Client(...)`. Así la ventana principal abre mucho antes y atproto se carga en segundo plano
# cuando la primera tarea de Bluesky lo necesita.
HAS_ATPROTO = importlib.util.find_spec("atproto") is not None
if not HAS_ATPROTO:  # pragma: no cover - entorno sin atproto
    log.warning("atproto no está disponible. El servicio Bluesky quedará inactivo.")


class _LazyModels:
    """Proxy de `atproto.models` que importa el módulo real al primer acceso a un atributo
    (carga diferida). Mantiene intacto el uso `models.AppBskyFeedPost`, etc., por todo el código."""
    _real = None

    def __getattr__(self, nombre):
        if _LazyModels._real is None:
            from atproto import models as _m
            _LazyModels._real = _m
        return getattr(_LazyModels._real, nombre)


models = _LazyModels() if HAS_ATPROTO else None  # type: ignore


def Client(*args, **kwargs):  # type: ignore
    """Fábrica que crea un `atproto.Client` importándolo de forma diferida (ver _LazyModels)."""
    from atproto import Client as _Client
    return _Client(*args, **kwargs)

# Valores válidos de self-label (la 'advertencia de contenido' de Bluesky).
ETIQUETAS_CONTENIDO = ("porn", "sexual", "nudity", "graphic-media")

_URL_RE = re.compile(r"https?://[^\s]+")
_MENCION_RE = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")


def _g(obj: Any, *nombres: str, default: Any = None) -> Any:
    """Lee el primer atributo presente (tolerante a snake_case/camelCase)."""
    for n in nombres:
        if obj is None:
            return default
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
        elif hasattr(obj, n):
            return getattr(obj, n)
    return default


def _ahora_iso() -> str:
    """Fecha-hora actual en ISO-8601 UTC con sufijo Z (formato AT Protocol)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Colección (NSID) de una publicación en AT Protocol. Un at:// que no la lleve NO es un post
# y pedir su hilo devuelve 400 NotFound.
COLECCION_POST = "/app.bsky.feed.post/"


def identidad_tras_login(client: Any, profile: Any, handle: str = "") -> tuple:
    """(did, handle) de la cuenta recién autenticada. Desde atproto 0.0.70, login() puede
    devolver None (el PDS no sirve app.bsky y no se pudo pedir el perfil): entonces se sacan de
    la propia sesión, que siempre los trae. Sin esto, el DID quedaba vacío y se saltaban las
    comprobaciones de cuenta duplicada y de "has entrado con otra cuenta"."""
    did = getattr(profile, "did", "") or ""
    handle_real = getattr(profile, "handle", "") or ""
    if not did or not handle_real:
        try:
            from atproto_client.client.session import Session
            sesion = Session.decode(client.export_session_string())
            did = did or (sesion.did or "")
            handle_real = handle_real or (sesion.handle or "")
        except Exception:
            pass
    return did, handle_real or handle


def es_uri_de_post(uri: Any) -> bool:
    """True si el at:// apunta a una publicación (y no a un like, repost, follow o lista)."""
    return bool(uri) and COLECCION_POST in str(uri)


def uri_post_de_notificacion(reason: str, uri: str, reason_subject: str = "", record: Any = None) -> str:
    """Publicación a la que se refiere una notificación de listNotifications, o "" si no hay.

    `uri` es el REGISTRO que provoca el aviso, no la publicación: en un "me gusta" es
    at://<did>/app.bsky.feed.like/<rkey>, y pedir la conversación de eso da 400 NotFound
    ("Post not found"), que es justo el error que veían los usuarios. Según el indexador
    oficial (packages/bsky/.../indexing/plugins):
      - mención, respuesta, cita y publicación de cuenta suscrita: `uri` YA es el post.
      - me gusta / repost: `reasonSubject` es tu publicación afectada.
      - me gusta o repost VÍA repost: `reasonSubject` es tu REPOST (tampoco un post), así que
        la publicación real sale del registro (record.subject.uri).
      - seguidores, verificaciones y "contact-match": no hay ninguna publicación implicada.
    """
    if es_uri_de_post(uri):
        return uri
    if es_uri_de_post(reason_subject):
        return reason_subject
    subject_uri = _g(_g(record, "subject"), "uri", default="") or ""
    return subject_uri if es_uri_de_post(subject_uri) else ""
