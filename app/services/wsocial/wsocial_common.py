# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio W Social: imports, atproto, constantes y helpers de módulo.

W Social (wsocial.eu) es una red europea de micromensajes construida sobre AT PROTOCOL, la misma
tecnología abierta de Bluesky. Su fork del repositorio de referencia no añade ningún lexicon
propio: usa los `app.bsky.*`, `com.atproto.*`, `chat.bsky.*` y `tools.ozone.*` estándar. Por eso
este cliente se apoya en la MISMA biblioteca `atproto` que el de Bluesky.

La diferencia importante está en el SERVIDOR: no se puede dar por sentado `bsky.social`. Cada
cuenta vive en su propio PDS, que se descubre resolviendo la identidad del usuario
(handle -> DID -> documento DID -> punto de servicio `#atproto_pds`). Eso lo hace
`wsocial_identidad`, y es lo que permite que el mismo cliente valga para cualquier servidor de W.
"""
import json
import logging
import importlib.util
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.database import db
from app.core.security import security
from app.core.errors import WSocialServiceError
from app.core.task_manager import task_manager
from app.core.config_manager import config

log = logging.getLogger(__name__)

# Carga DIFERIDA de atproto (misma razón que en Bluesky: importarlo al arrancar cuesta segundos
# porque define miles de modelos, y solo hace falta al USAR la red). Aquí solo se comprueba si
# está instalado; el módulo real se importa al primer acceso.
HAS_ATPROTO = importlib.util.find_spec("atproto") is not None
if not HAS_ATPROTO:  # pragma: no cover - entorno sin atproto
    log.warning("atproto no está disponible. El servicio W Social quedará inactivo.")


class _LazyModels:
    """Proxy de `atproto.models` que importa el módulo real al primer acceso (carga diferida)."""
    _real = None

    def __getattr__(self, nombre):
        if _LazyModels._real is None:
            from atproto import models as _m
            _LazyModels._real = _m
        return getattr(_LazyModels._real, nombre)


models = _LazyModels() if HAS_ATPROTO else None  # type: ignore


def Client(*args, **kwargs):  # type: ignore
    """Crea un `atproto.Client` importándolo de forma diferida (ver _LazyModels)."""
    from atproto import Client as _Client
    return _Client(*args, **kwargs)


# Servidor por defecto que se OFRECE al añadir una cuenta. No se impone: si el handle del usuario
# vive en otro PDS, la resolución de identidad lo descubre y manda ese (ver wsocial_identidad).
PDS_POR_DEFECTO = "https://wsocial.eu"

# Valores válidos de self-label (el "aviso de contenido" del propio autor, app.bsky.actor).
ETIQUETAS_CONTENIDO = ("porn", "sexual", "nudity", "graphic-media")

# Límite de caracteres de una publicación en AT Protocol (graphemes; el lexicon marca 300).
LIMITE_CARACTERES = 300

# Motivos de notificación que reconoce el lexicon app.bsky.notification.listNotifications.
MOTIVOS_NOTIFICACION = {
    "like": "Me gusta",
    "repost": "Republicación",
    "follow": "Nuevo seguidor",
    "mention": "Mención",
    "reply": "Respuesta",
    "quote": "Cita",
    "starterpack-joined": "Se unió con tu paquete de inicio",
    "verified": "Cuenta verificada",
    "unverified": "Verificación retirada",
    "like-via-repost": "Me gusta a través de una republicación",
    "repost-via-repost": "Republicación de una republicación tuya",
    "subscribed-post": "Publicación de una cuenta a la que sigues de cerca",
}


def _g(obj: Any, *nombres: str, default: Any = None) -> Any:
    """Lee el primer atributo presente (tolerante a snake_case/camelCase y a dicts)."""
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


def rkey_de_uri(uri: str) -> str:
    """Clave de registro (rkey) de un at:// URI: la última parte de la ruta."""
    return (uri or "").rsplit("/", 1)[-1]


def did_de_uri(uri: str) -> str:
    """DID del repositorio al que pertenece un at:// URI ('at://did:plc:xxx/col/rkey')."""
    resto = (uri or "").replace("at://", "")
    return resto.split("/", 1)[0] if resto else ""


# Colección (NSID) de una publicación. Un at:// de otra colección (like, repost, follow) NO es
# una publicación y pedir su hilo devuelve 400 NotFound.
COLECCION_POST = "/app.bsky.feed.post/"


def es_uri_de_post(uri: Any) -> bool:
    """True si el at:// apunta a una publicación."""
    return bool(uri) and COLECCION_POST in str(uri)


def uri_post_de_notificacion(reason: str, uri: str, reason_subject: str = "", record: Any = None) -> str:
    """Publicación a la que se refiere una notificación de listNotifications, o "" si no hay.

    El `uri` de la notificación es el REGISTRO que la provoca: en un "me gusta" es
    at://<did>/app.bsky.feed.like/<rkey>, y pedir su conversación da 400 NotFound. Según el
    indexador oficial de AT Protocol: en mención, respuesta, cita y publicación de cuenta
    suscrita el `uri` ya es el post; en me gusta y repost la publicación está en
    `reasonSubject`; en las variantes "vía repost" reasonSubject es un REPOST, así que la
    publicación sale del registro; y en seguidores o verificaciones no hay ninguna."""
    if es_uri_de_post(uri):
        return uri
    if es_uri_de_post(reason_subject):
        return reason_subject
    subject_uri = _g(_g(record, "subject"), "uri", default="") or ""
    return subject_uri if es_uri_de_post(subject_uri) else ""
