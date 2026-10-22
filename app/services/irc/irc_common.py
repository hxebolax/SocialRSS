# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio IRC: imports, carga diferida de pydle, IRCError y helpers.

Regla de arranque: pydle NO se importa a nivel de módulo (carga diferida, como Kurigram o
atproto). Aquí solo se comprueba (barato) si está instalado; el módulo se importa de verdad
al crear el primer cliente.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext (los eventos llevan textos para la UI)

import asyncio
import logging
import re
import threading
import time
import importlib.util
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Callable, List

from app.core.database import db
from app.core.security import security
from app.core.event_bus import event_bus
from app.core.errors import IRCServiceError

log = logging.getLogger(__name__)

# ¿Está instalada la librería? (find_spec no la importa: no cuesta arranque)
HAS_PYDLE = importlib.util.find_spec("pydle") is not None
if not HAS_PYDLE:  # pragma: no cover - depende del entorno
    log.info("pydle no disponible. IRC quedará deshabilitado hasta instalar dependencias.")


class IRCError(IRCServiceError):
    """Error específico del servicio de IRC."""
    pass


# --- Formato mIRC ---------------------------------------------------------------------
# Códigos de control de mIRC: \x02 negrita, \x1d cursiva, \x1f subrayado, \x1e tachado,
# \x11 monoespaciado, \x16 inverso, \x0f reset, \x03 color (con dígitos opcionales
# "fg[,bg]"), \x04 color hexadecimal. Un lector de pantalla JAMÁS debe oír estos códigos.
_RE_COLOR_MIRC = re.compile(r"\x03(\d{1,2}(,\d{1,2})?)?|\x04([0-9A-Fa-f]{6}(,[0-9A-Fa-f]{6})?)?")
_RE_CONTROL_MIRC = re.compile(r"[\x02\x1d\x1f\x1e\x11\x16\x0f]")


def quitar_formato_mirc(texto: str) -> str:
    """Elimina TODOS los códigos de formato/color de mIRC de un texto (para mostrarlo
    limpio y que el lector no lea basura)."""
    if not texto:
        return ""
    texto = _RE_COLOR_MIRC.sub("", texto)
    return _RE_CONTROL_MIRC.sub("", texto)


def ahora_iso() -> str:
    """Fecha-hora actual en ISO 8601 UTC (formato común de la BD)."""
    return datetime.now(timezone.utc).isoformat()


def db_id_canal(account_id: int, nombre: str) -> str:
    """Id de fila de irc_channels: 'cuenta|nombre'. Separador '|' (los nombres de canal
    IRC no pueden llevar '|'; igual que se hizo con los DID de Bluesky)."""
    return f"{account_id}|{(nombre or '').lower()}"
