# -*- coding: utf-8 -*-
"""Gestión ampliada de X: encuestas, comunidades, listas, búsqueda de cuentas y tendencias por
lugar. Mixin de XService.

Todo va por twikit (twifork 2.4.0). Las LECTURAS se comprobaron con una sesión real (2026-09);
las ESCRITURAS usan la API de twikit tal cual (votar, crear/editar/borrar listas, añadir o quitar
miembros, entrar o salir de una comunidad) y siguen la regla de reintentos de escritura.
"""
from app.services.x.x_common import Callable, Optional, db, task_manager, _g

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s


def _comunidad_a_dict(c) -> dict:
    return {
        "id": str(_g(c, "id", default="") or ""),
        "name": _g(c, "name", default="") or "",
        "description": (_g(c, "description", default="") or "").replace("\n", " "),
        "member_count": int(_g(c, "member_count", default=0) or 0),
        "is_member": bool(_g(c, "is_member", default=False)),
    }


class _GestionMixin:
    # ------------------------------------------------------------------ encuestas
    def votar_encuesta_async(self, account_id: int, tweet_id: str, opcion: str,
                             callback: Optional[Callable] = None, poll_json: str = ""):
        """Vota en la encuesta de un tuit. `opcion`: el número de la opción ("1", "2"...) o su
        texto. Con `poll_json` (guardado en la caché) no hace falta volver a pedir el tuit; sin
        él, se pide para sacar la tarjeta de la encuesta. Antes se llamaba a t.vote(), que el
        Tweet de twikit no tiene: no votaba nunca y no avisaba."""
        import json

        def _bg(ctx):
            datos = {}
            if poll_json:
                try:
                    datos = json.loads(poll_json) or {}
                except ValueError:
                    datos = {}

            async def _coro():
                client = await self._cliente(account_id)
                if datos.get("id") and datos.get("name"):
                    numero = str(opcion)
                    for c in datos.get("choices", []):
                        if c.get("label") == opcion:
                            numero = str(c.get("number"))
                            break
                    return await client.vote(numero, datos["id"], str(tweet_id), datos["name"])
                t = (await client.get_tweets_by_ids([str(tweet_id)]) or [None])[0]
                poll = _g(t, "poll", default=None)
                if poll is None:
                    from app.services.x.x_common import XServiceError
                    raise XServiceError(_("Esta publicación no tiene encuesta."))
                return await poll.vote(str(opcion))
            return self._run_con_reauth(account_id, _coro, timeout=45, escritura=True)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------ comunidades
    def buscar_comunidades_async(self, account_id: int, query: str,
                                 callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.search_community(query))
            return [_comunidad_a_dict(c) for c in self._run_con_reauth(account_id, _coro, timeout=45)]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def comunidad_info_async(self, account_id: int, community_id: str,
                             callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.get_community(str(community_id))
            return _comunidad_a_dict(self._run_con_reauth(account_id, _coro, timeout=45))
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------ listas
    def crear_lista_async(self, account_id: int, nombre: str, descripcion: str = "",
                          privada: bool = False, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.create_list(nombre, descripcion, privada)
            lst = self._run_con_reauth(account_id, _coro, timeout=45, escritura=True)
            return {"list_id": str(_g(lst, "id", default="") or ""), "name": nombre}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def editar_lista_async(self, account_id: int, list_id: str, nombre=None, descripcion=None,
                           privada=None, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.edit_list(str(list_id), nombre, descripcion, privada)
            self._run_con_reauth(account_id, _coro, timeout=45, escritura=True)
            if nombre:
                db.execute("UPDATE x_lists SET name = ? WHERE account_id = ? AND list_id = ?",
                           (nombre, account_id, str(list_id)))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def borrar_lista_async(self, account_id: int, list_id: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.delete_list(str(list_id))
            self._run_con_reauth(account_id, _coro, timeout=45, escritura=True)
            db.execute("DELETE FROM x_lists WHERE account_id = ? AND list_id = ?",
                       (account_id, str(list_id)))
            db.execute("DELETE FROM x_timeline_tweets WHERE account_id = ? AND timeline = ?",
                       (account_id, "list:" + str(list_id)))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def miembros_lista_async(self, account_id: int, list_id: str, count: int = 100,
                             callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.get_list_members(str(list_id), count=count))
            return [self._user_a_dict(u) for u in self._run_con_reauth(account_id, _coro, timeout=45)]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------ tendencias por lugar
    def lugares_tendencias_async(self, account_id: int, callback: Optional[Callable] = None):
        """Países y ciudades con tendencias propias: [{woeid, name, country}]."""
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.get_available_locations())
            lugares = self._run_con_reauth(account_id, _coro, timeout=45)
            salida = [{"woeid": int(_g(l, "woeid", default=0) or 0),
                       "name": _g(l, "name", default="") or "",
                       "country": _g(l, "country", default="") or ""} for l in lugares]
            return sorted(salida, key=lambda x: (x["country"] != x["name"], x["country"], x["name"]))
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def tendencias_lugar_async(self, account_id: int, woeid: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return await client.get_place_trends(int(woeid))
            res = self._run_con_reauth(account_id, _coro, timeout=45)
            trends = _g(res, "trends", default=None) or (res.get("trends") if isinstance(res, dict) else []) or []
            return [{"name": _g(t, "name", default="") or "",
                     "count": _g(t, "tweet_volume", "tweets_count", default=0) or 0} for t in trends]
        task_manager.start_task(_bg, callback=callback, pass_context=True)
