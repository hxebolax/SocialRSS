# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio X: imports, twikit, XServiceError y helper _g.
"""
import json
import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.database import db
from app.core.security import security
from app.core.task_manager import task_manager
from app.core.config_manager import config

log = logging.getLogger(__name__)

HAS_TWIKIT = False
try:
    from twikit import Client
    HAS_TWIKIT = True
except Exception:  # pragma: no cover - entorno sin twikit
    Client = None  # type: ignore
    log.warning("twikit no está disponible. El cliente de X quedará inactivo.")


class XServiceError(Exception):
    """Error del servicio de X con mensaje claro para el usuario."""


def _g(obj: Any, *nombres: str, default: Any = None) -> Any:
    """Lee el primer atributo/clave presente (tolerante a esquemas cambiantes de X).

    Importante: NO se usa hasattr(), porque algunas propiedades de twikit (p. ej.
    created_at_datetime) lanzan ValueError al evaluarse (parseo de fecha con el locale
    en español), y hasattr propagaría esa excepción. Aquí cualquier fallo al leer un
    atributo se trata como 'no presente' y se prueba el siguiente nombre."""
    for n in nombres:
        if obj is None:
            return default
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
            continue
        try:
            return getattr(obj, n)
        except AttributeError:
            continue
        except Exception:
            continue
    return default
