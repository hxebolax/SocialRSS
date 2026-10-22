# -*- coding: utf-8 -*-
"""Capa de almacenamiento de Telegram Drive sobre el cliente de Telegram EXISTENTE.

Es un mixin que se añade a `TelegramService` para tener acceso natural a su bucle asyncio
(`self._run`), a su cliente conectado (`self._cliente`) y a su caché (`self._clients`), sin
crear otro cliente ni otra sesión. Ofrece las operaciones de bajo nivel que necesita la
nube: crear el canal privado, subir un documento, descargarlo, borrar mensajes y consultar
el límite de tamaño de la cuenta. Añade gestión de FloodWait y reintentos con espera
progresiva, que el resto del servicio de Telegram no hace.
"""
import asyncio
import os
from typing import Optional, Callable, List, Dict, Any

from .tgdrive_models import TransferenciaCancelada

import logging
log = logging.getLogger(__name__)


def _stop_transmission_exc():
    """Excepción con la que kurigram/Pyrogram ABORTA una subida/descarga en curso desde el
    callback de progreso (cancelación inmediata). Se localiza de forma tolerante a la versión."""
    try:
        from pyrogram.errors import StopTransmission
        return StopTransmission
    except Exception:
        try:
            from pyrogram import StopTransmission
            return StopTransmission
        except Exception:
            return None

# Límites de tamaño por documento (bytes). Telegram: 2 GiB gratis, 4 GiB con Premium.
LIMITE_GRATIS = 2 * 1000 * 1000 * 1000
LIMITE_PREMIUM = 4 * 1000 * 1000 * 1000

# Reintentos ante errores transitorios (además de la espera exacta de FloodWait).
_MAX_REINTENTOS = 4


class _DriveStorageMixin:
    """Operaciones de Telegram para la nube. Se ejecutan en el bucle de Telegram vía _run."""

    async def _drive_llamar(self, factory: Callable, intentos: int = _MAX_REINTENTOS):
        """Ejecuta una corrutina (creada por `factory()`) reintentando ante FloodWait
        (esperando EXACTAMENTE lo que pide Telegram) y ante errores de red transitorios
        (espera creciente). `factory` se vuelve a llamar en cada intento por si la corrutina
        ya se consumió."""
        ultimo = None
        for intento in range(intentos):
            try:
                return await factory()
            except Exception as e:
                nombre = e.__class__.__name__
                ultimo = e
                if nombre == "FloodWait":
                    segundos = getattr(e, "value", None) or getattr(e, "x", None) or 5
                    log.info("Telegram Drive: FloodWait, esperando %s s.", segundos)
                    await asyncio.sleep(min(int(segundos) + 1, 120))
                    continue
                # Errores de red/servidor transitorios: espera creciente.
                if nombre in ("TimeoutError", "ConnectionError", "OSError",
                              "InternalServerError", "ServiceUnavailable"):
                    if intento < intentos - 1:
                        await asyncio.sleep(1.5 * (2 ** intento))
                        continue
                raise
        if ultimo:
            raise ultimo
        raise RuntimeError("Telegram Drive: operación fallida sin excepción.")

    async def _asegurar_peer_canal(self, client, account_id: int, channel_id: int):
        """Pre-resuelve el canal en la sesión para poder operar por su id numérico. Sin esto,
        tras REINICIAR la app kurigram no conoce el access_hash del canal y da 'Peer id invalid'
        (p. ej. al BORRAR mensajes de la papelera). Se cachea: solo hace la llamada la 1.ª vez
        por sesión y canal."""
        cache = getattr(self, "_drive_peers_ok", None)
        if cache is None:
            cache = self._drive_peers_ok = set()
        clave = (account_id, channel_id)
        if clave in cache:
            return
        try:
            await client.get_chat(channel_id)
            cache.add(clave)
        except Exception as e:
            log.debug("Drive: no se pudo pre-resolver el canal %s: %s", channel_id, e)

    def drive_crear_canal(self, account_id: int, titulo: str,
                          descripcion: str = "") -> int:
        """Crea un canal PRIVADO (sin nombre público, invisible en búsquedas) para la nube y
        devuelve su id. Solo el creador tiene acceso."""
        async def _coro():
            client = await self._cliente(account_id)
            chat = await self._drive_llamar(
                lambda: client.create_channel(titulo, description=descripcion or "SocialRSS Telegram Drive"))
            return chat.id
        return self._run(_coro(), timeout=120)

    def drive_borrar_canal(self, account_id: int, channel_id: int) -> bool:
        """Borra por completo el canal privado de la nube (elimina TODOS sus archivos). Solo lo
        puede hacer el propietario. Devuelve True si se borró (o ya no existía)."""
        async def _coro():
            client = await self._cliente(account_id)
            await self._asegurar_peer_canal(client, account_id, channel_id)
            try:
                await self._drive_llamar(lambda: client.delete_channel(channel_id))
            except Exception as e:
                # Si el canal ya no existe, damos el borrado por bueno.
                nombre = type(e).__name__
                if "ChannelInvalid" in nombre or "PeerIdInvalid" in nombre or "ChannelPrivate" in nombre:
                    return True
                raise
            return True
        return self._run(_coro(), timeout=120)

    def drive_buscar_canal(self, account_id: int, titulo: str) -> Optional[int]:
        """Localiza en la cuenta un canal de DIFUSIÓN con ese título (el de la nube), recorriendo
        los chats. Sirve para reconstruir cuando la app perdió el id del canal (reinstalación,
        base borrada, otro equipo). Devuelve su id o None si no lo encuentra."""
        async def _coro():
            client = await self._cliente(account_id)
            try:
                from pyrogram.enums import ChatType
                es_canal = lambda c: getattr(c, "type", None) == ChatType.CHANNEL
            except Exception:
                es_canal = lambda c: True
            encontrado = None
            async for dialog in client.get_dialogs():
                chat = getattr(dialog, "chat", None)
                if chat is None:
                    continue
                if (getattr(chat, "title", "") or "") == titulo and es_canal(chat):
                    encontrado = chat.id
                    break
            return encontrado
        try:
            return self._run(_coro(), timeout=90)
        except Exception as e:
            log.warning("Drive: fallo al buscar el canal por título: %s", e)
            return None

    def drive_limite_tamano(self, account_id: int) -> int:
        """Límite de tamaño por documento según si la cuenta es Premium (4 GiB) o no (2 GiB)."""
        async def _coro():
            client = await self._cliente(account_id)
            me = await self._drive_llamar(lambda: client.get_me())
            return LIMITE_PREMIUM if getattr(me, "is_premium", False) else LIMITE_GRATIS
        try:
            return self._run(_coro(), timeout=30)
        except Exception:
            return LIMITE_GRATIS

    def drive_subir_documento(self, account_id: int, channel_id: int, ruta: str,
                              caption: str = "",
                              progreso: Optional[Callable[[int, int], None]] = None,
                              cancelado: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        """Sube un archivo como documento al canal y devuelve {message_id, file_id,
        unique_id, size}. kurigram trocea internamente por bloques (no carga todo en
        memoria). `progreso(current, total)` lo llama el bucle de Telegram: debe ser rápido.
        Si `cancelado()` pasa a True DURANTE la subida, se aborta AL INSTANTE (no espera a que
        termine) lanzando StopTransmission, que el servicio traduce a TransferenciaCancelada."""
        async def _coro():
            client = await self._cliente(account_id)
            await self._asegurar_peer_canal(client, account_id, channel_id)
            stop = _stop_transmission_exc()

            def _prog(current, total):
                if cancelado and cancelado() and stop is not None:
                    raise stop()   # kurigram aborta la subida en curso
                if progreso:
                    progreso(current, total)

            try:
                msg = await self._drive_llamar(
                    lambda: client.send_document(channel_id, ruta, caption=caption or "",
                                                 file_name=os.path.basename(ruta),
                                                 progress=_prog, disable_notification=True))
            except Exception as e:
                if stop is not None and isinstance(e, stop):
                    raise TransferenciaCancelada()
                raise
            doc = getattr(msg, "document", None)
            return {
                "message_id": getattr(msg, "id", None),
                "file_id": getattr(doc, "file_id", None) if doc else None,
                "unique_id": getattr(doc, "file_unique_id", None) if doc else None,
                "size": getattr(doc, "file_size", 0) if doc else 0,
            }
        return self._run(_coro(), timeout=7200)

    def drive_descargar_documento(self, account_id: int, channel_id: int, message_id: int,
                                  destino: str, file_id: Optional[str] = None,
                                  progreso: Optional[Callable[[int, int], None]] = None,
                                  cancelado: Optional[Callable[[], bool]] = None) -> Optional[str]:
        """Descarga un documento del canal a la ruta `destino`. Localiza el mensaje por su
        id; si ya no existe, cae a descargar por `file_id` (si se dio). Devuelve la ruta o
        None. `progreso` se envuelve en wx.CallAfter internamente cuando wx está disponible.
        Si `cancelado()` pasa a True durante la descarga, se aborta al instante."""
        async def _coro():
            client = await self._cliente(account_id)
            await self._asegurar_peer_canal(client, account_id, channel_id)
            stop = _stop_transmission_exc()

            async def _prog(current, total):
                if cancelado and cancelado() and stop is not None:
                    raise stop()   # aborta la descarga en curso
                if progreso is None:
                    return
                try:
                    import wx
                    wx.CallAfter(progreso, current, total)
                except Exception:
                    progreso(current, total)

            objetivo = None
            try:
                objetivo = await self._drive_llamar(
                    lambda: client.get_messages(channel_id, message_id))
            except Exception as e:
                log.debug("Drive: no se pudo obtener el mensaje %s: %s", message_id, e)
            if objetivo is None and file_id:
                objetivo = file_id  # descargar por file_id directamente
            if objetivo is None:
                raise FileNotFoundError("El mensaje del archivo ya no existe en Telegram.")
            try:
                ruta = await self._drive_llamar(
                    lambda: client.download_media(objetivo, file_name=destino, progress=_prog))
            except Exception as e:
                if stop is not None and isinstance(e, stop):
                    raise TransferenciaCancelada()
                raise
            return ruta
        return self._run(_coro(), timeout=7200)

    def drive_borrar_mensajes(self, account_id: int, channel_id: int,
                              message_ids: List[int]):
        """Borra mensajes del canal (por lotes de 100). Lanza excepción si falla, para que el
        llamador registre la eliminación como pendiente y la reintente."""
        if not message_ids:
            return
        async def _coro():
            client = await self._cliente(account_id)
            await self._asegurar_peer_canal(client, account_id, channel_id)
            for i in range(0, len(message_ids), 100):
                lote = message_ids[i:i + 100]
                await self._drive_llamar(lambda l=lote: client.delete_messages(channel_id, l))
            return True
        self._run(_coro(), timeout=300)

    def drive_canal_existe(self, account_id: int, channel_id: int) -> bool:
        """Comprueba que el canal sigue existiendo y es accesible por la cuenta."""
        async def _coro():
            client = await self._cliente(account_id)
            try:
                await self._drive_llamar(lambda: client.get_chat(channel_id))
                return True
            except Exception:
                return False
        try:
            return self._run(_coro(), timeout=30)
        except Exception:
            return False

    def drive_buscar_manifiesto(self, account_id: int, channel_id: int,
                                nombre: str) -> Optional[Dict[str, Any]]:
        """Busca en el canal el ÚLTIMO documento del manifiesto (por nombre de archivo) y
        devuelve {message_id, file_id} o None. Se usa para reconstruir el índice."""
        async def _coro():
            client = await self._cliente(account_id)
            await self._asegurar_peer_canal(client, account_id, channel_id)
            encontrado = None
            async for msg in client.get_chat_history(channel_id, limit=200):
                doc = getattr(msg, "document", None)
                if doc and getattr(doc, "file_name", "") == nombre:
                    encontrado = {"message_id": msg.id, "file_id": doc.file_id}
                    break  # el historial viene de más nuevo a más antiguo
            return encontrado
        return self._run(_coro(), timeout=120)

    def drive_listar_documentos_canal(self, account_id: int, channel_id: int) -> List[Dict[str, Any]]:
        """Recorre TODO el historial del canal y devuelve los mensajes que llevan un documento:
        [{message_id, file_name, caption}]. Sirve para revisar/reparar (cruzar con el índice
        local y detectar mensajes huérfanos que no le corresponden a ningún archivo)."""
        async def _coro():
            client = await self._cliente(account_id)
            await self._asegurar_peer_canal(client, account_id, channel_id)
            salida = []
            async for msg in client.get_chat_history(channel_id):
                doc = getattr(msg, "document", None)
                if doc is not None:
                    salida.append({
                        "message_id": msg.id,
                        "file_name": getattr(doc, "file_name", "") or "",
                        "caption": getattr(msg, "caption", "") or "",
                    })
            return salida
        return self._run(_coro(), timeout=600)
