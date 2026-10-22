# -*- coding: utf-8 -*-
"""Mensajes directos (DMs) de X con TIEMPO REAL por streaming.

Mixin de XService. twikit no expone un "inbox" para listar conversaciones, pero sí el historial
1 a 1 (get_dm_history), el envío (send_dm), reacciones/borrado y el STREAMING por conversación
(get_streaming_session con Topic.dm_update / Topic.dm_typing). Con eso se construye una ventana de
conversación tipo Telegram: carga el historial, envía y RECIBE en vivo (mensajes y "escribiendo…").
"""
from app.services.x.x_common import (
    Callable, Optional, db, task_manager, asyncio, datetime, _g, log)


class _DmsMixin:
    @staticmethod
    def _convo_id_de(my_id: str, their_id: str) -> str:
        """Id de conversación 1 a 1 de X: los dos ids de usuario ordenados y unidos por guion."""
        try:
            return "-".join(sorted([str(my_id), str(their_id)], key=lambda x: int(x)))
        except Exception:
            return f"{my_id}-{their_id}"

    @staticmethod
    def _fecha_dm(valor) -> str:
        """El 'time' de un DM de X es epoch en MILISEGUNDOS (string). A ISO en hora local."""
        try:
            return datetime.fromtimestamp(int(valor) / 1000).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return str(valor or "")

    def cargar_dms_async(self, account_id: int, user_id: str, screen: str = "",
                         callback: Optional[Callable] = None):
        """Descarga el historial de DM con un usuario, lo cachea en x_messages y devuelve
        {'convo_id', 'mensajes': [...]}. El convo_id sirve para abrir el streaming en vivo."""
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.get_dm_history(user_id))
            msgs = self._run_con_reauth(account_id, _coro, timeout=45)
            my_id = str(self._yo(account_id).get("id", "") or "")
            conv = self._convo_id_de(my_id, user_id)
            salida = []
            ultimo_txt, ultima_fecha = "", ""
            # TODO el historial en UNA transacción (un solo commit/fsync): antes cada mensaje
            # hacía su propio commit y un historial largo eran cientos de mini-transacciones.
            with db.transaccion():
                db.execute(
                    "INSERT OR REPLACE INTO x_convos (id, account_id, convo_id, title, last_message, last_message_date) "
                    "VALUES (?,?,?,?,?,?)",
                    (f"{account_id}:{conv}", account_id, conv, (f"@{screen}" if screen else user_id),
                     "", ""))
                for m in msgs:
                    mid = str(_g(m, "id", default=""))
                    if not mid:
                        continue
                    sender = str(_g(m, "sender_id", default="") or "")
                    text = _g(m, "text", default="") or ""
                    fecha = self._fecha_dm(_g(m, "time", default=""))
                    outgoing = 1 if sender and sender == my_id else 0
                    db.execute(
                        "INSERT OR REPLACE INTO x_messages "
                        "(id, account_id, convo_id, message_id, sender_id, sender_name, text, date, outgoing) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"{account_id}:{conv}:{mid}", account_id, conv, mid, sender,
                         "" if outgoing else (f"@{screen}" if screen else ""), text, fecha, outgoing))
                    salida.append({"message_id": mid, "sender_id": sender, "text": text,
                                   "date": fecha, "outgoing": bool(outgoing)})
                    ultimo_txt, ultima_fecha = text, fecha
                if salida:
                    db.execute(
                        "UPDATE x_convos SET last_message = ?, last_message_date = ? WHERE id = ?",
                        (ultimo_txt[:200], ultima_fecha, f"{account_id}:{conv}"))
            return {"convo_id": conv, "mensajes": salida}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def reaccionar_dm_async(self, account_id: int, message_id: str, conversation_id: str,
                            emoji: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.add_reaction_to_message(message_id, conversation_id, emoji)
            return self._run_con_reauth(account_id, _coro, timeout=45)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def borrar_dm_async(self, account_id: int, message_id: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.delete_dm(message_id)
            return self._run_con_reauth(account_id, _coro, timeout=45)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # TIEMPO REAL (streaming de una conversación abierta)
    # ------------------------------------------------------------------
    def iniciar_streaming_dm(self, account_id: int, conversation_id: str):
        """Arranca, en el bucle de X, la escucha en vivo de una conversación: emite por el
        event_bus 'x_dm_update'(account_id, conversation_id) cuando llega/cambia un mensaje y
        'x_dm_typing'(account_id, conversation_id) cuando la otra persona escribe. Idempotente."""
        self._ensure_loop()
        with self._lock:
            if conversation_id in self._dm_streams:
                return

        async def _stream():
            from twikit.streaming import Topic
            from app.core.event_bus import event_bus
            try:
                client = await self._cliente(account_id)
                topics = {Topic.dm_update(conversation_id), Topic.dm_typing(conversation_id)}
                session = await client.get_streaming_session(topics)
                async for _topic, payload in session:
                    try:
                        if _g(payload, "dm_update"):
                            event_bus.emit("x_dm_update", account_id, conversation_id)
                        if _g(payload, "dm_typing"):
                            event_bus.emit("x_dm_typing", account_id, conversation_id)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug(f"Streaming de DM de X detenido (conv {conversation_id}): {e}")

        fut = asyncio.run_coroutine_threadsafe(_stream(), self._loop)
        with self._lock:
            self._dm_streams[conversation_id] = fut

    def detener_streaming_dm(self, conversation_id: str):
        """Detiene la escucha en vivo de una conversación (al cerrar su ventana)."""
        with self._lock:
            fut = self._dm_streams.pop(conversation_id, None)
        if fut:
            try:
                fut.cancel()
            except Exception:
                pass

    def detener_todos_streaming_dm(self):
        """Detiene todas las escuchas de DM en curso (al cerrar la aplicación)."""
        with self._lock:
            futs = list(self._dm_streams.values())
            self._dm_streams.clear()
        for fut in futs:
            try:
                fut.cancel()
            except Exception:
                pass
