# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio Telegram: imports, Kurigram, TelegramError y log.
"""
import asyncio
import logging
import sqlite3
import threading
import time
import importlib.util
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Callable

from app.core.database import db
from app.core.security import security
from app.core.event_bus import event_bus
from app.core.paths import get_data_path
from app.core.errors import TelegramServiceError

log = logging.getLogger(__name__)

# Carga DIFERIDA de Kurigram (se importa como 'pyrogram'): importarlo al arrancar costaba ~1,4 s
# (pyrogram.raw genera miles de tipos) y solo hace falta al USAR Telegram. Aquí solo comprobamos
# (barato) si está instalado; el módulo se importa de verdad al crear el primer Client o acceder a
# los enums. Las funciones de login importan SessionPasswordNeeded localmente (pyrogram ya está
# cargado en ese punto, porque para llegar al login se ha creado un Client).
HAS_KURIGRAM = importlib.util.find_spec("pyrogram") is not None
if not HAS_KURIGRAM:  # pragma: no cover - depende del entorno
    log.info("Kurigram (pyrogram) no disponible. Telegram quedará deshabilitado hasta instalar dependencias.")


class _LazyEnums:
    """Proxy de `pyrogram.enums` con carga diferida (ver HAS_KURIGRAM). Mantiene el uso
    `tg_enums.ChatType`, etc., por todo el código."""
    _real = None

    def __getattr__(self, nombre):
        if _LazyEnums._real is None:
            from pyrogram import enums as _enums
            _LazyEnums._real = _enums
        return getattr(_LazyEnums._real, nombre)


tg_enums = _LazyEnums() if HAS_KURIGRAM else None  # type: ignore


_parche_bad_msg_aplicado = False


def _parchear_bad_msg_notification():
    """Hace que MTProto reintente cuando el servidor responde BadMsgNotification.

    Kurigram solo reintenta ante BadServerSalt; ante un BadMsgNotification (típicamente
    error 16/17/20: el msg_id, calculado con la hora LOCAL del PC, está desincronizado con
    el servidor) se limita a registrar un warning y DEVUELVE el objeto crudo. Ese objeto sube
    hasta get_chat_history, que espera un messages.Messages con .users, y revienta con
    "'BadMsgNotification' object has no attribute 'users'".

    Al recibir el BadMsgNotification, la sesión ya llamó a _set_server_time() y corrigió el
    offset de reloj, así que basta con reintentar: el nuevo msg_id sí será válido. Esto hace
    la app inmune a un reloj de Windows desfasado (causa habitual del fallo)."""
    global _parche_bad_msg_aplicado
    if _parche_bad_msg_aplicado:
        return
    try:
        from pyrogram import raw
        from pyrogram.session.session import Session
    except Exception as e:  # pragma: no cover - depende del entorno
        log.debug("No se pudo aplicar el parche de BadMsgNotification: %s", e)
        return

    _orig_send = Session.send

    async def _send_reintentando(self, *args, **kwargs):
        # Hasta 3 reintentos: el offset de reloj ya se corrige al recibir cada BadMsgNotification.
        for intento in range(3):
            result = await _orig_send(self, *args, **kwargs)
            if isinstance(result, raw.types.BadMsgNotification):
                log.warning(
                    "BadMsgNotification(error_code=%s); reintentando con el reloj resincronizado (intento %s/3).",
                    getattr(result, "error_code", "?"), intento + 1,
                )
                await asyncio.sleep(0.2)
                continue
            return result
        return result

    Session.send = _send_reintentando
    _parche_bad_msg_aplicado = True
    log.info("Parche MTProto aplicado: reintento automático ante BadMsgNotification.")


def Client(*args, **kwargs):  # type: ignore
    """Fábrica que crea un `pyrogram.Client` importándolo de forma diferida (ver HAS_KURIGRAM)."""
    from pyrogram import Client as _Client
    _parchear_bad_msg_notification()
    return _Client(*args, **kwargs)


class TelegramError(TelegramServiceError):
    """Error específico del servicio de Telegram."""
    pass
