# -*- coding: utf-8 -*-
"""Abstracción del almacenamiento remoto de Telegram Drive.

El servicio de alto nivel no habla con Telegram directamente: lo hace a través de un
BACKEND con una interfaz mínima. Así el servicio se puede probar con un doble en memoria
(sin subir archivos reales ni borrar mensajes reales) y la dependencia de Telegram queda
aislada en `BackendTelegram`, que reenvía a los métodos `drive_*` del TelegramService.
"""
from typing import Optional, Callable, List, Dict, Any


class BackendAlmacen:
    """Interfaz que el servicio de Telegram Drive necesita del almacenamiento remoto. Los
    dobles de prueba implementan estos mismos métodos."""

    def crear_canal(self, account_id: int, titulo: str) -> int:
        raise NotImplementedError

    def limite_tamano(self, account_id: int) -> int:
        raise NotImplementedError

    def subir(self, account_id: int, channel_id: int, ruta: str, caption: str = "",
              progreso: Optional[Callable[[int, int], None]] = None,
              cancelado: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def descargar(self, account_id: int, channel_id: int, message_id: int, destino: str,
                  file_id: Optional[str] = None,
                  progreso: Optional[Callable[[int, int], None]] = None,
                  cancelado: Optional[Callable[[], bool]] = None) -> Optional[str]:
        raise NotImplementedError

    def borrar_mensajes(self, account_id: int, channel_id: int, message_ids: List[int]):
        raise NotImplementedError

    def canal_existe(self, account_id: int, channel_id: int) -> bool:
        raise NotImplementedError

    def borrar_canal(self, account_id: int, channel_id: int) -> bool:
        raise NotImplementedError

    def buscar_canal(self, account_id: int, titulo: str) -> Optional[int]:
        raise NotImplementedError

    def buscar_manifiesto(self, account_id: int, channel_id: int,
                          nombre: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def listar_documentos_canal(self, account_id: int, channel_id: int) -> List[Dict[str, Any]]:
        raise NotImplementedError


class BackendTelegram(BackendAlmacen):
    """Backend real: reenvía a los métodos drive_* del TelegramService (mismo cliente y
    sesión que el resto de Telegram; no crea otro cliente)."""

    def __init__(self, telegram_service):
        self.tg = telegram_service

    def crear_canal(self, account_id, titulo):
        return self.tg.drive_crear_canal(account_id, titulo)

    def limite_tamano(self, account_id):
        return self.tg.drive_limite_tamano(account_id)

    def subir(self, account_id, channel_id, ruta, caption="", progreso=None, cancelado=None):
        return self.tg.drive_subir_documento(account_id, channel_id, ruta, caption, progreso,
                                             cancelado=cancelado)

    def descargar(self, account_id, channel_id, message_id, destino, file_id=None, progreso=None,
                  cancelado=None):
        return self.tg.drive_descargar_documento(account_id, channel_id, message_id, destino,
                                                 file_id=file_id, progreso=progreso,
                                                 cancelado=cancelado)

    def borrar_mensajes(self, account_id, channel_id, message_ids):
        self.tg.drive_borrar_mensajes(account_id, channel_id, message_ids)

    def canal_existe(self, account_id, channel_id):
        return self.tg.drive_canal_existe(account_id, channel_id)

    def borrar_canal(self, account_id, channel_id):
        return self.tg.drive_borrar_canal(account_id, channel_id)

    def buscar_canal(self, account_id, titulo):
        return self.tg.drive_buscar_canal(account_id, titulo)

    def buscar_manifiesto(self, account_id, channel_id, nombre):
        return self.tg.drive_buscar_manifiesto(account_id, channel_id, nombre)

    def listar_documentos_canal(self, account_id, channel_id):
        return self.tg.drive_listar_documentos_canal(account_id, channel_id)
