# -*- coding: utf-8 -*-
"""
Servicio del cliente de usuario de Telegram (Kurigram / MTProto).

Ensamblador: la clase `TelegramService` hereda de los mixins por dominio.
Los símbolos comunes (Kurigram, TelegramError, imports) viven en `telegram_common`.
"""
from app.services.telegram.telegram_common import (
    asyncio, threading, Any, Dict, Optional,
    HAS_KURIGRAM, Client, tg_enums, TelegramError,
    TelegramServiceError, log)

from app.services.telegram.telegram_nucleo import _NucleoMixin
from app.services.telegram.telegram_cliente import _ClienteMixin
from app.services.telegram.telegram_mensajes import _MensajesMixin
from app.services.telegram.telegram_chats import _ChatsMixin
from app.services.telegram.telegram_perfil import _PerfilMixin
from app.services.telegram.telegram_admin import _AdminMixin
from app.services.telegram.telegram_bots import _BotsMixin
# Telegram Drive: almacenamiento de la nube personal sobre el MISMO cliente/sesión.
from app.services.tgdrive.tgdrive_storage import _DriveStorageMixin

class TelegramService(_NucleoMixin, _ClienteMixin, _MensajesMixin, _ChatsMixin, _PerfilMixin, _AdminMixin, _BotsMixin, _DriveStorageMixin):
    """Administrador del cliente de usuario de Telegram (Kurigram)."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: Dict[int, Any] = {}          # account_id -> Client conectado
        self._pending: Dict[int, Dict[str, Any]] = {}  # account_id -> datos de login en curso
        self._calls: Dict[int, Any] = {}            # account_id -> instancia PyTgCalls
        self._lock = threading.Lock()
        # Locks asyncio por cuenta para crear+arrancar el Client UNA sola vez. Sin esto, dos
        # corrutinas podían colarse entre el chequeo de caché y el `await client.start()` y crear
        # un cliente DUPLICADO con handlers en tiempo real repetidos (mensajes/sonidos dobles) y
        # una conexión fugada. Solo se tocan desde el bucle de Telegram, así que no necesitan
        # protección de hilos adicional.
        self._aio_locks: Dict[int, Any] = {}        # account_id -> asyncio.Lock


# Instancia global única del servicio de Telegram
telegram_service = TelegramService()
