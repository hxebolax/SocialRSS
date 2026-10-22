# -*- coding: utf-8 -*-
"""Interacciones, tendencias, encuestas, programados, DMs y listas.

Mixin de XService (parte de la refactorización modular).
"""
from app.services.x.x_common import (
    Callable, Optional, db, task_manager, _g)


class _ExtrasMixin:
    def obtener_interacciones_async(self, account_id: int, tweet_id: str, tipo: str,
                                    callback: Optional[Callable] = None):
        """Cuentas que interactuaron con un tweet. tipo: 'likes' | 'retweets'."""
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                if tipo == "retweets":
                    return list(await client.get_retweeters(tweet_id))
                return list(await client.get_favoriters(tweet_id))
            users = self._run_con_reauth(account_id, _coro, timeout=45)
            return [{"user_id": str(_g(u, "id", default="")),
                     "screen_name": _g(u, "screen_name", default=""),
                     "name": _g(u, "name", default="") or ""} for u in users]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _tw_tendencias(self, account_id: int):
        """Implementación TWIKIT de las tendencias (dicts uniformes para el router)."""
        async def _coro():
            client = await self._cliente(account_id)
            return list(await client.get_trends("trending"))
        trends = self._run_con_reauth(account_id, _coro, timeout=45)
        return [{"name": _g(t, "name", default=""),
                 "count": _g(t, "tweets_count", "tweet_volume", default=0) or 0} for t in trends]

    def obtener_tendencias_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            return self.r_tendencias(account_id)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # Más endpoints (cliente "super completo"): encuestas, programados, DMs, listas,
    # comunidades, perfil de un usuario. Capa de servicio lista para la UI.
    # ------------------------------------------------------------------
    # votar_encuesta_async: ahora en x_gestion.py (esta versión llamaba a Tweet.vote, que no
    # existe: no votaba nunca).

    def programar_tweet_async(self, account_id: int, texto: str, fecha,
                              callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.create_scheduled_tweet(fecha, text=texto)
            return self._run_con_reauth(account_id, _coro, timeout=60, escritura=True)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_programados_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.get_scheduled_tweets())
            tws = self._run_con_reauth(account_id, _coro, timeout=45)
            return [{"id": str(_g(t, "id", default="")),
                     "text": _g(t, "text", "full_text", default="") or "",
                     "date": _g(t, "execute_at", "scheduled_at", default="") or ""} for t in tws]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cancelar_programado_async(self, account_id: int, tweet_id: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.delete_scheduled_tweet(tweet_id)
            return self._run_con_reauth(account_id, _coro, timeout=45)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_dms_async(self, account_id: int, user_id: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.get_dm_history(user_id))
            msgs = self._run_con_reauth(account_id, _coro, timeout=45)
            return [{"id": str(_g(m, "id", default="")), "text": _g(m, "text", default="") or "",
                     "sender_id": str(_g(m, "sender_id", default="") or "")} for m in msgs]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def enviar_dm_async(self, account_id: int, user_id: str, texto: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.send_dm(user_id, texto)
            return self._run_con_reauth(account_id, _coro, timeout=45, escritura=True)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_listas_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.get_lists())
            lists = self._run_con_reauth(account_id, _coro, timeout=45)
            salida = []
            with db.transaccion():
                db.execute("DELETE FROM x_lists WHERE account_id = ?", (account_id,))
                for pos, l in enumerate(lists):
                    lid = str(_g(l, "id", default=""))
                    nombre = _g(l, "name", default="") or ""
                    db.execute("""INSERT OR REPLACE INTO x_lists
                        (id, account_id, list_id, name, description, member_count, position)
                        VALUES (?,?,?,?,?,?,?)""",
                        (f"{account_id}:{lid}", account_id, lid, nombre,
                         _g(l, "description", default="") or "", int(_g(l, "member_count", default=0) or 0), pos))
                    salida.append({"list_id": lid, "name": nombre})
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def gestionar_miembro_lista_async(self, account_id: int, list_id: str, user_id: str,
                                      anadir: bool, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                if anadir:
                    return await client.add_list_member(list_id, user_id)
                return await client.remove_list_member(list_id, user_id)
            return self._run_con_reauth(account_id, _coro, timeout=45)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def comunidad_async(self, account_id: int, community_id: str, accion: str,
                        callback: Optional[Callable] = None):
        """accion: 'join' | 'leave'."""
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                if accion == "join":
                    return await client.join_community(community_id)
                return await client.leave_community(community_id)
            return self._run_con_reauth(account_id, _coro, timeout=45)
        task_manager.start_task(_bg, callback=callback, pass_context=True)
