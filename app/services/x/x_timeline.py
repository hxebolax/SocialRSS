# -*- coding: utf-8 -*-
"""Timelines, parseo de tweets, publicación y acciones sociales.

Mixin de XService (parte de la refactorización modular).
"""
from app.services.x.x_common import (
    json, Any, Callable, Dict, List, Optional,
    db, task_manager, XServiceError, _g)

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext (la app instala _ al arrancar)


def _cursor_de(contenido) -> str:
    """Valor de un cursor de X, esté donde esté: en el formato actual va en content.value; en el
    antiguo, en content.itemContent.value (y dentro de los módulos, en item.itemContent.value)."""
    if not isinstance(contenido, dict):
        return ""
    for c in (contenido, contenido.get("itemContent") or {},
              (contenido.get("item") or {}).get("itemContent") or {}):
        if isinstance(c, dict) and c.get("value"):
            return str(c["value"])
    return ""


def datos_encuesta(poll) -> str:
    """Encuesta de un tuit (twikit.tweet.Poll) como JSON para guardar: identificador de la
    tarjeta (card_uri) y su nombre, que son lo que pide X para votar, las opciones con sus votos,
    el cierre y la opción que votaste. "" si no hay encuesta."""
    if poll is None:
        return ""
    try:
        opciones = []
        for c in (_g(poll, "choices", default=None) or []):
            opciones.append({"number": str(c.get("number", "")), "label": c.get("label", "") or "",
                             "count": int(c.get("count", 0) or 0)})
        return json.dumps({
            "id": _g(poll, "id", default="") or "",
            "name": _g(poll, "name", default="") or "",
            "choices": opciones,
            "end": _g(poll, "end_datetime_utc", default="") or "",
            "final": bool(_g(poll, "counts_are_final", default=False)),
            "selected": _g(poll, "selected_choice", default="") or "",
        }, ensure_ascii=False)
    except Exception:
        return ""


def datos_extra_tweet(legacy, cita, poll) -> dict:
    """Campos que no vienen como propiedad en twikit: a quién responde (del tuit crudo), el tuit
    citado (autor y texto) y la encuesta."""
    autor_cita = _g(cita, "user", default=None) if cita is not None else None
    return {
        "in_reply_to_screen_name": str((legacy or {}).get("in_reply_to_screen_name") or ""),
        "quoted_author": (f"{_g(autor_cita, 'name', default='') or ''} "
                          f"(@{_g(autor_cita, 'screen_name', default='') or ''})"
                          if autor_cita is not None else ""),
        "quoted_text": str(_g(cita, "full_text", "text", default="") or "") if cita is not None else "",
        "poll_json": datos_encuesta(poll),
    }


def entradas_tweet_detail(resp, tweet_id: str):
    """Lee una respuesta GraphQL de TweetDetail y la separa en (arriba, foco, abajo, cursor):

    - arriba: entradas de los tuits a los que responde el foco (raíz → padre), en orden.
    - foco: la entrada del propio tuit (o None si no ha venido: borrado, protegido...).
    - abajo: entradas de las respuestas, en orden de lectura; cada "conversationthread" es un
      módulo con la respuesta y las respuestas anidadas de esa rama (y del propio autor, si
      sigue su hilo).
    - cursor: el de la página siguiente de respuestas ("" si no hay más).

    Tolerante al formato: salta cursores, el cuadro de "responder" (tweetcomposer), los tuits
    "relacionados" y lo que no sea un tuit. Las entradas se pasan tal cual a
    twikit.tweet.tweet_from_data, que busca el 'result' dentro de cada una."""
    arriba, foco, abajo, cursor = [], None, [], ""
    instrucciones = []
    try:
        instrucciones = (resp.get("data", {}).get("threaded_conversation_with_injections_v2", {})
                         .get("instructions", [])) or []
    except AttributeError:
        instrucciones = []
    entradas = []
    for ins in instrucciones:
        if isinstance(ins, dict):
            entradas.extend(ins.get("entries") or [])
            if ins.get("entry"):            # TimelineReplaceEntry / PinEntry
                entradas.append(ins["entry"])
    objetivo = f"tweet-{tweet_id}"
    for e in entradas:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("entryId", ""))
        contenido = e.get("content") or {}
        if eid.startswith("cursor-bottom") or contenido.get("cursorType") == "Bottom":
            cursor = _cursor_de(contenido) or cursor
            continue
        if eid.startswith(("cursor", "tweetdetailrelatedtweets", "tweetcomposer", "label")):
            continue
        if eid == objetivo:
            foco = e
        elif eid.startswith("tweet-"):
            (arriba if foco is None else abajo).append(e)
        elif isinstance(contenido.get("items"), list):
            for item in contenido["items"]:
                ieid = str((item or {}).get("entryId", ""))
                if "cursor" in ieid or "tweetcomposer" in ieid:
                    continue
                if ieid.endswith(f"-tweet-{tweet_id}") and foco is None:
                    foco = item
                else:
                    abajo.append(item)
    return arriba, foco, abajo, cursor


class _TimelineMixin:
    @classmethod
    def _fecha_iso(cls, base: Any) -> str:
        """Convierte la fecha de X a ISO-8601 para que el tiempo relativo funcione igual
        que en las demás redes.

        X la da como 'Tue Jun 16 13:30:00 +0000 2026' (nombres en INGLÉS). NO se usa
        strptime con %a/%b ni la propiedad created_at_datetime de twikit, porque ambos
        dependen del locale y fallan con el locale en español. Se parsea a mano."""
        raw = _g(base, "created_at", default="") or ""
        if not raw:
            return ""
        try:
            from datetime import datetime, timezone, timedelta
            p = raw.split()  # [Tue, Jun, 16, 13:30:00, +0000, 2026]
            if len(p) >= 6:
                mes = cls._MESES.get(p[1])
                dia = int(p[2])
                hh, mm, ss = (int(x) for x in p[3].split(":"))
                tz = p[4]
                anio = int(p[5])
                off = int(tz[1:3]) * 60 + int(tz[3:5])
                if tz[0] == "-":
                    off = -off
                if mes:
                    return datetime(anio, mes, dia, hh, mm, ss,
                                    tzinfo=timezone(timedelta(minutes=off))).isoformat()
        except Exception:
            pass
        # Último recurso: intentar el parseo RFC 2822 estándar. NUNCA devolver la cadena
        # cruda ("Tue Jun 16..."): el orden de los buffers es lexicográfico sobre esta
        # columna y una sola fila con formato crudo quedaba ordenada arbitrariamente.
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(raw).isoformat()
        except Exception:
            return ""

    def _tweet_a_dict(self, account_id: int, t: Any) -> Dict[str, Any]:
        autor = _g(t, "user")
        rt = _g(t, "retweeted_tweet", default=None)
        base = rt or t
        media = []
        for m in (_g(base, "media", default=[]) or []):
            media.append({
                "type": _g(m, "type", default=""),
                "url": _g(m, "media_url", "media_url_https", "url", default=""),
                "video": _g(m, "streams", default=None) and True or False,
                "alt": _g(m, "alt_text", "ext_alt_text", default="") or "",
            })
        legacy = getattr(base, "_legacy", None) or {}
        cita = _g(base, "quote", default=None)
        return {
            **datos_extra_tweet(legacy, cita, _g(base, "poll", default=None)),
            "tweet_id": str(_g(base, "id", default="")),
            "author_id": str(_g(_g(base, "user"), "id", default="")),
            "author_name": _g(_g(base, "user"), "name", default="") or "",
            "author_screen_name": _g(_g(base, "user"), "screen_name", default="") or "",
            "text": _g(base, "full_text", "text", default="") or "",
            "created_at": self._fecha_iso(base),
            "lang": _g(base, "lang", default="") or "",
            "liked": 1 if _g(base, "favorited", default=False) else 0,
            "retweeted": 1 if _g(base, "retweeted", default=False) else 0,
            "bookmarked": 1 if _g(base, "bookmarked", default=False) else 0,
            "favorite_count": int(_g(base, "favorite_count", default=0) or 0),
            "retweet_count": int(_g(base, "retweet_count", default=0) or 0),
            "reply_count": int(_g(base, "reply_count", default=0) or 0),
            "quote_count": int(_g(base, "quote_count", default=0) or 0),
            "view_count": int(_g(base, "view_count", default=0) or 0),
            "in_reply_to": str(_g(base, "in_reply_to", default="") or ""),
            # twikit no expone conversation_id como propiedad: viene en los datos crudos.
            "conversation_id": str(_g(base, "conversation_id", default="")
                                   or legacy.get("conversation_id_str") or ""),
            "quoted_id": str(_g(_g(base, "quote", default=None), "id", default="") or ""),
            "retweeted_by": (_g(autor, "screen_name", default="") if rt else ""),
            "media_json": json.dumps(media),
        }

    def _guardar_tweet(self, account_id: int, d: Dict[str, Any], conn=None) -> str:
        db_id = f"{account_id}:{d['tweet_id']}"
        yo = self._yo(account_id)
        es_propio = 1 if d["author_id"] and d["author_id"] == yo.get("id") else 0
        _ejec = (conn.execute if conn is not None else db.execute)
        _ejec("""
            INSERT OR REPLACE INTO x_tweets
            (id, account_id, tweet_id, author_id, author_name, author_screen_name, text,
             created_at, lang, liked, retweeted, bookmarked, favorite_count, retweet_count,
             reply_count, quote_count, view_count, in_reply_to, conversation_id, quoted_id,
             retweeted_by, media_json, urls_json, is_own, in_reply_to_screen_name,
             quoted_author, quoted_text, poll_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (db_id, account_id, d["tweet_id"], d["author_id"], d["author_name"],
              d["author_screen_name"], d["text"], d["created_at"], d["lang"], d["liked"],
              d["retweeted"], d["bookmarked"], d["favorite_count"], d["retweet_count"],
              d["reply_count"], d["quote_count"], d["view_count"], d["in_reply_to"],
              d["conversation_id"], d["quoted_id"], d["retweeted_by"], d["media_json"],
              "[]", es_propio, d.get("in_reply_to_screen_name", "") or "",
              d.get("quoted_author", "") or "", d.get("quoted_text", "") or "",
              d.get("poll_json", "") or ""))
        return db_id

    def _mapear_buffer(self, account_id: int, timeline: str, db_ids: List[str], conn=None):
        _ejec = (conn.execute if conn is not None else db.execute)
        _ejec("DELETE FROM x_timeline_tweets WHERE account_id = ? AND timeline = ?",
              (account_id, timeline))
        for pos, db_id in enumerate(db_ids):
            _ejec("""INSERT OR REPLACE INTO x_timeline_tweets
                     (account_id, timeline, tweet_db_id, position) VALUES (?,?,?,?)""",
                  (account_id, timeline, db_id, pos))

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    def _tw_timeline(self, account_id: int, tipo: str, count: int):
        """Implementación TWIKIT de un timeline (devuelve dicts uniformes para el router).
        Soporta también user:<id> (cronología de un usuario) por si twscrape no está disponible."""
        async def _coro():
            client = await self._cliente(account_id)
            if tipo == "following":
                res = await client.get_latest_timeline(count=count)
            elif tipo in ("home", "for_you"):
                res = await client.get_timeline(count=count)
            elif tipo == "bookmarks":
                res = await client.get_bookmarks(count=count)
            elif tipo == "mentions":
                res = await client.search_tweet(
                    f"@{self._yo(account_id).get('screen_name','')}", "Latest")
            elif tipo == "communities":
                res = await client.get_communities_timeline(count=count)
            elif tipo.startswith("community:"):
                res = await client.get_community_tweets(tipo.split(":", 1)[1], "Latest", count=count)
            elif tipo.startswith("list:"):
                res = await client.get_list_tweets(tipo.split(":", 1)[1], count=count)
            elif tipo.startswith("search:"):
                res = await client.search_tweet(tipo.split(":", 1)[1], "Latest")
            else:
                res = await client.get_timeline(count=count)
            return list(res)
        tweets = self._run_con_reauth(account_id, _coro, timeout=60)
        return [self._tweet_a_dict(account_id, t) for t in tweets]

    def obtener_timeline_async(self, account_id: int, tipo: str = "home", count: int = None,
                               callback: Optional[Callable] = None):
        """Carga un timeline/buffer y lo cachea. tipo: home, following, mentions, notifications,
        bookmarks, communities, list:<id>, search:<query>, user:<id>. La lectura la enruta el
        router (twikit/twscrape complementarios); el guardado es uniforme."""
        if not count:
            # Preferencias → X → "publicaciones por carga" (antes no se usaba: siempre 40).
            from app.services.x.x_common import config as _cfg
            try:
                count = max(10, min(100, int(_cfg.get("x", "limit_per_load", 40) or 40)))
            except (TypeError, ValueError):
                count = 40

        def _bg(ctx):
            dicts = self.r_timeline(account_id, tipo, count)
            db_ids = []
            # Toda la página (tweets + mapeo del buffer) en UNA transacción.
            with db.transaccion() as _conn:
                for d in dicts:
                    if d and d.get("tweet_id"):
                        db_ids.append(self._guardar_tweet(account_id, d, conn=_conn))
                self._mapear_buffer(account_id, tipo, db_ids, conn=_conn)
            return len(db_ids)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _cursores_tl(self) -> dict:
        """Dict perezoso {account:tipo -> cursor 'bottom' de twikit} para paginar hacia ATRÁS."""
        c = getattr(self, "_x_cursores_tl", None)
        if c is None:
            c = {}
            self._x_cursores_tl = c
        return c

    def _tw_timeline_cursor(self, account_id: int, tipo: str, count: int, cursor):
        """TWIKIT con cursor: devuelve (lista_tweets, next_cursor) para paginar hacia ATRÁS.

        Para "cargar anteriores" se usa twikit directamente (soporta cursor) en vez del router, que
        descarta el cursor. Cubre los buffers cronológicos; otros tipos devuelven (vacío, None)."""
        async def _coro():
            client = await self._cliente(account_id)
            if tipo == "following":
                res = await client.get_latest_timeline(count=count, cursor=cursor)
            elif tipo in ("home", "for_you"):
                res = await client.get_timeline(count=count, cursor=cursor)
            elif tipo == "mentions":
                res = await client.search_tweet(
                    f"@{self._yo(account_id).get('screen_name', '')}", "Latest",
                    count=count, cursor=cursor)
            elif tipo == "bookmarks":
                res = await client.get_bookmarks(count=count, cursor=cursor)
            elif tipo == "communities":
                res = await client.get_communities_timeline(count=count, cursor=cursor)
            elif tipo.startswith("community:"):
                res = await client.get_community_tweets(tipo.split(":", 1)[1], "Latest",
                                                        count=count, cursor=cursor)
            elif tipo.startswith("list:"):
                res = await client.get_list_tweets(tipo.split(":", 1)[1], count=count, cursor=cursor)
            elif tipo.startswith("search:"):
                res = await client.search_tweet(tipo.split(":", 1)[1], "Latest", count=count, cursor=cursor)
            elif tipo.startswith("user:"):
                # 'user:<id>' o 'user:<id>:<modo>': antes se pasaba "<id>:<modo>" entero como id.
                uid, _s, modo = tipo.split(":", 1)[1].partition(":")
                pestana = {"replies": "Replies", "media": "Media", "likes": "Likes"}.get(modo, "Tweets")
                res = await client.get_user_tweets(uid, pestana, count=count, cursor=cursor)
            else:
                # Tipo sin paginación hacia atrás (una conversación, por ejemplo). Antes caía en
                # get_timeline y metía tuits de INICIO en ese buffer.
                return [], None
            return list(res), getattr(res, "next_cursor", None)
        return self._run_con_reauth(account_id, _coro, timeout=60)

    def _mapear_buffer_append(self, account_id: int, timeline: str, db_ids: List[str], conn=None) -> int:
        """Añade tweets al FINAL del buffer (posiciones tras la última), SIN borrar lo que ya hay.
        Salta los que ya estaban mapeados. Devuelve cuántos nuevos se añadieron."""
        _ejec = (conn.execute if conn is not None else db.execute)
        fila = _ejec("SELECT COALESCE(MAX(position), -1) FROM x_timeline_tweets "
                     "WHERE account_id = ? AND timeline = ?", (account_id, timeline)).fetchone()
        pos = (fila[0] if fila else -1) + 1
        existentes = {r[0] for r in _ejec(
            "SELECT tweet_db_id FROM x_timeline_tweets WHERE account_id = ? AND timeline = ?",
            (account_id, timeline)).fetchall()}
        nuevos = 0
        for db_id in db_ids:
            if db_id in existentes:
                continue
            _ejec("INSERT OR REPLACE INTO x_timeline_tweets (account_id, timeline, tweet_db_id, position) "
                  "VALUES (?,?,?,?)", (account_id, timeline, db_id, pos))
            pos += 1
            nuevos += 1
        return nuevos

    def cargar_anteriores_async(self, account_id: int, tipo: str = "home", count: int = 40,
                                callback: Optional[Callable] = None):
        """Descarga una página de tweets MÁS ANTIGUOS del buffer y los añade al FINAL (paginación
        hacia atrás con el cursor de twikit). Devuelve cuántos tweets nuevos se añadieron."""
        def _bg(ctx):
            cursores = self._cursores_tl()
            clave = f"{account_id}:{tipo}"
            cursor = cursores.get(clave)
            if not cursor:
                # Primera vez: traer la página actual (más nuevos) solo para obtener el cursor 'bottom'.
                _tw, cursor = self._tw_timeline_cursor(account_id, tipo, count, None)
            if not cursor:
                return 0
            tweets, nuevo_cursor = self._tw_timeline_cursor(account_id, tipo, count, cursor)
            cursores[clave] = nuevo_cursor
            dicts = [self._tweet_a_dict(account_id, t) for t in tweets]
            with db.transaccion() as _conn:
                db_ids = []
                for d in dicts:
                    if d and d.get("tweet_id"):
                        db_ids.append(self._guardar_tweet(account_id, d, conn=_conn))
                return self._mapear_buffer_append(account_id, tipo, db_ids, conn=_conn)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_notificaciones_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                return list(await client.get_notifications("All"))
            notifs = self._run_con_reauth(account_id, _coro, timeout=60)
            with db.transaccion() as _conn:
                _conn.execute("DELETE FROM x_notifications WHERE account_id = ?", (account_id,))
                for n in notifs:
                    actor = _g(n, "from_user", "user", default=None)
                    nid = str(_g(n, "id", default="")) or str(id(n))
                    ts = _g(n, "timestamp", "created_at", default="")
                    # Pasar la fecha a ISO si twikit la da como datetime.
                    fecha = ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else "")
                    _conn.execute("""INSERT OR REPLACE INTO x_notifications
                        (id, account_id, tipo, actor_name, actor_screen_name, text, tweet_id, created_at)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (f"{account_id}:{nid}", account_id, _g(n, "type", default="") or "",
                         _g(actor, "name", default="") or "", _g(actor, "screen_name", default="") or "",
                         _g(n, "message", "text", default="") or "",
                         str(_g(_g(n, "tweet", default=None), "id", default="") or ""), fecha))
            return len(notifs)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _tw_user_tweets(self, account_id: int, uid: str, modo: str, count: int):
        """Implementación TWIKIT de la cronología de un usuario (tweets/replies/media)."""
        tab = {"replies": "Replies", "media": "Media", "likes": "Likes"}.get(modo, "Tweets")
        async def _coro():
            client = await self._cliente(account_id)
            return list(await client.get_user_tweets(str(uid), tab, count=count))
        tweets = self._run_con_reauth(account_id, _coro, timeout=60)
        return [self._tweet_a_dict(account_id, t) for t in tweets]

    # Páginas de respuestas que se piden como mucho al abrir una conversación (unas 20-40
    # respuestas por página). Suficiente para leer un hilo sin disparar el límite de X.
    HILO_MAX_PAGINAS = 5

    def _tw_hilo(self, account_id: int, tweet_id: str):
        """Conversación COMPLETA de un tuit en orden de lectura: los tuits de ENCIMA (a qué
        responde, hasta la raíz), el tuit, y sus respuestas con las anidadas de cada rama.

        No usa client.get_tweet_by_id: su lector de TweetDetail rompe con el formato actual de
        X (el cursor de "más respuestas" ya no va en itemContent → KeyError 'itemContent'), y
        además solo devolvía el tuit y las respuestas de primer nivel, sin los de encima. Aquí se
        pide TweetDetail directamente y se lee con `entradas_tweet_detail`, tolerante al formato."""
        async def _coro():
            from twikit.tweet import tweet_from_data
            client = await self._cliente(account_id)
            ancestros, focal, respuestas = [], None, []
            cursor, vistos = None, set()
            for _pagina in range(self.HILO_MAX_PAGINAS):
                resp, _ = await client.gql.tweet_detail(str(tweet_id), cursor)
                if isinstance(resp, dict) and resp.get("errors") and not resp.get("data"):
                    raise XServiceError(_("X no ha devuelto la conversación: {0}").format(
                        resp["errors"][0].get("message", "")))
                arriba, foco, abajo, siguiente = entradas_tweet_detail(resp, str(tweet_id))
                for grupo, destino in ((arriba, ancestros), (abajo, respuestas)):
                    for e in grupo:
                        t = tweet_from_data(client, e)
                        if t is not None and str(t.id) not in vistos:
                            vistos.add(str(t.id))
                            destino.append(t)
                if foco is not None and focal is None:
                    focal = tweet_from_data(client, foco)
                    if focal is not None:
                        vistos.add(str(focal.id))
                if not siguiente or siguiente == cursor:
                    break
                cursor = siguiente
            if focal is None:
                raise XServiceError(_("El tuit no está disponible (borrado, protegido o de una "
                                      "cuenta que te bloquea)."))
            return ancestros + [focal] + respuestas
        tweets = self._run_con_reauth(account_id, _coro, timeout=90)
        return [self._tweet_a_dict(account_id, t) for t in tweets]

    def _tw_perfil(self, account_id: int, screen_name: str):
        async def _coro():
            client = await self._cliente(account_id)
            return await client.get_user_by_screen_name(screen_name.lstrip("@"))
        u = self._run_con_reauth(account_id, _coro, timeout=45)
        if not u:
            return None
        return {
            "user_id": str(_g(u, "id", default="")),
            "screen_name": _g(u, "screen_name", default=""),
            "name": _g(u, "name", default=""),
            "description": _g(u, "description", default="") or "",
            "followers": int(_g(u, "followers_count", default=0) or 0),
            "following": int(_g(u, "following_count", default=0) or 0),
            "statuses": int(_g(u, "statuses_count", default=0) or 0),
            "verified": bool(_g(u, "verified", "is_blue_verified", default=False)),
        }

    def _tw_buscar_usuarios(self, account_id: int, query: str, count: int = 40):
        async def _coro():
            client = await self._cliente(account_id)
            return list(await client.search_user(query))
        users = self._run_con_reauth(account_id, _coro, timeout=45)
        return [self._user_a_dict(u) for u in users]

    def obtener_hilo_async(self, account_id: int, tweet_id: str, callback: Optional[Callable] = None):
        """Conversación de un tuit en orden de lectura (encima, el tuit, respuestas). Los tuits
        se GUARDAN en la caché y cada dict lleva su "db_id", para que el visor pueda responder,
        dar me gusta, etc. sobre cualquiera de ellos."""
        def _bg(ctx):
            dicts = self.r_hilo(account_id, tweet_id)
            with db.transaccion() as _conn:
                for d in dicts:
                    if d and d.get("tweet_id"):
                        d["db_id"] = self._guardar_tweet(account_id, d, conn=_conn)
            return dicts
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_perfil_async(self, account_id: int, screen_name: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            return self.r_perfil(account_id, screen_name)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def buscar_usuarios_async(self, account_id: int, query: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            return self.r_buscar_usuarios(account_id, query)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def resolver_usuario_async(self, account_id: int, screen_name: str,
                               callback: Optional[Callable] = None):
        """Resuelve un @usuario a {user_id, screen_name, name} para abrir su cronología."""
        def _bg(ctx):
            return self.r_resolver_uid(account_id, screen_name)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # Publicación e interacciones
    # ------------------------------------------------------------------
    def publicar_async(self, account_id: int, texto: str, media_paths: Optional[List[str]] = None,
                       reply_to: Optional[str] = None, quote_id: Optional[str] = None,
                       alt_texts: Optional[List[str]] = None,
                       poll_opciones: Optional[List[str]] = None, poll_minutos: int = 0,
                       callback: Optional[Callable] = None):
        """Publica un tweet. Admite multimedia con TEXTO ALTERNATIVO (alt_texts, en paralelo a
        media_paths) y ENCUESTA (poll_opciones + poll_minutos)."""
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                media_ids = []
                rutas = (media_paths or [])[:4]
                alts = list(alt_texts or [])
                for i, ruta in enumerate(rutas):
                    mid = await client.upload_media(ruta)
                    media_ids.append(mid)
                    # Texto alternativo de la imagen (accesibilidad), si se proporcionó.
                    alt = alts[i].strip() if i < len(alts) and alts[i] else ""
                    if alt:
                        try:
                            await client.create_media_metadata(mid, alt_text=alt)
                        except Exception:
                            pass
                kw = {}
                if media_ids:
                    kw["media_ids"] = media_ids
                if reply_to:
                    kw["reply_to"] = reply_to
                if quote_id:
                    kw["attachment_url"] = f"https://x.com/i/status/{quote_id}"
                # Encuesta: crear la papeleta y adjuntarla por poll_uri (no combinable con media).
                if poll_opciones and len([o for o in poll_opciones if o.strip()]) >= 2 and poll_minutos > 0:
                    opciones = [o.strip() for o in poll_opciones if o.strip()][:4]
                    kw["poll_uri"] = await client.create_poll(opciones, poll_minutos)
                return await client.create_tweet(text=texto, **kw)
            t = self._run_con_reauth(account_id, _coro, timeout=120, escritura=True)
            return str(_g(t, "id", default=""))
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def accion_async(self, account_id: int, tweet_id: str, accion: str, callback: Optional[Callable] = None):
        """accion: like, unlike, retweet, unretweet, bookmark, unbookmark, delete."""
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                if accion == "like":
                    await client.favorite_tweet(tweet_id)
                elif accion == "unlike":
                    await client.unfavorite_tweet(tweet_id)
                elif accion == "retweet":
                    await client.retweet(tweet_id)
                elif accion == "unretweet":
                    await client.delete_retweet(tweet_id)
                elif accion == "bookmark":
                    await client.bookmark_tweet(tweet_id)
                elif accion == "unbookmark":
                    await client.delete_bookmark(tweet_id)
                elif accion == "delete":
                    await client.delete_tweet(tweet_id)
                return True
            ok = self._run_con_reauth(account_id, _coro, timeout=45)
            # Reflejar el cambio en la caché local.
            db_id = f"{account_id}:{tweet_id}"
            campos = {"like": ("liked", 1), "unlike": ("liked", 0),
                      "retweet": ("retweeted", 1), "unretweet": ("retweeted", 0),
                      "bookmark": ("bookmarked", 1), "unbookmark": ("bookmarked", 0)}
            if accion in campos:
                col, val = campos[accion]
                db.execute(f"UPDATE x_tweets SET {col} = ? WHERE id = ?", (val, db_id))
            elif accion == "delete":
                db.execute("DELETE FROM x_tweets WHERE id = ?", (db_id,))
            return ok
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def accion_cuenta_async(self, account_id: int, user_id: str, accion: str,
                            callback: Optional[Callable] = None):
        """accion: follow, unfollow, block, unblock, mute, unmute."""
        def _bg(ctx):
            async def _coro():
                client = await self._cliente(account_id)
                fn = {"follow": client.follow_user, "unfollow": client.unfollow_user,
                      "block": client.block_user, "unblock": client.unblock_user,
                      "mute": client.mute_user, "unmute": client.unmute_user}.get(accion)
                if not fn:
                    raise XServiceError("Acción de cuenta no válida.")
                await fn(user_id)
                return True
            return self._run_con_reauth(account_id, _coro, timeout=45)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # Buffers de personas (seguidores / a quienes sigo)
    # ------------------------------------------------------------------
    def _user_a_dict(self, u: Any) -> Dict[str, Any]:
        """Adaptador TWIKIT de una cuenta al dict uniforme (igual que `_user_tws_a_dict`)."""
        return {
            "user_id": str(_g(u, "id", default="") or ""),
            "screen_name": _g(u, "screen_name", default="") or "",
            "name": _g(u, "name", default="") or "",
            "description": (_g(u, "description", default="") or "").replace("\n", " "),
            "followers": int(_g(u, "followers_count", default=0) or 0),
            "following": int(_g(u, "following_count", default=0) or 0),
            "statuses": int(_g(u, "statuses_count", default=0) or 0),
            "is_following": 1 if _g(u, "following", default=False) else 0,
            "is_blocked": 1 if _g(u, "blocking", default=False) else 0,
            "is_muted": 1 if _g(u, "muting", default=False) else 0,
            "verified": 1 if _g(u, "verified", "is_blue_verified", default=False) else 0,
        }

    def _tw_actores(self, account_id: int, uid: str, bucket: str, count: int):
        """Implementación TWIKIT de seguidores ('followers') o seguidos ('friends')."""
        async def _coro():
            client = await self._cliente(account_id)
            u = uid or self._yo(account_id).get("id") or await client.user_id()
            # GraphQL (Followers/Following), no los antiguos get_latest_* de la API v1.1: esos
            # devuelven vacío o 404 hoy (comprobado con una sesión real en 2026-09).
            if bucket == "followers":
                res = await client.get_user_followers(str(u), count=count)
            elif bucket in ("verified", "verified_followers"):
                res = await client.get_user_verified_followers(str(u), count=count)
            else:
                res = await client.get_user_following(str(u), count=count)
            return list(res)
        users = self._run_con_reauth(account_id, _coro, timeout=60)
        return [self._user_a_dict(x) for x in users]

    def _guardar_usuario(self, account_id: int, d: Dict[str, Any], bucket: str, pos: int, conn=None):
        """Guarda una cuenta (dict uniforme, venga de twikit o twscrape) en x_users."""
        uid = str(d.get("user_id") or "")
        if not uid:
            return
        _ejec = (conn.execute if conn is not None else db.execute)
        _ejec("""
            INSERT OR REPLACE INTO x_users
            (id, account_id, user_id, screen_name, name, description, followers, following,
             statuses, is_following, is_blocked, is_muted, bucket, position)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (f"{account_id}:{uid}:{bucket}", account_id, uid,
              d.get("screen_name", "") or "", d.get("name", "") or "",
              d.get("description", "") or "",
              int(d.get("followers", 0) or 0), int(d.get("following", 0) or 0),
              int(d.get("statuses", 0) or 0),
              int(d.get("is_following", 0) or 0), int(d.get("is_blocked", 0) or 0),
              int(d.get("is_muted", 0) or 0), bucket, pos))

    def obtener_actores_async(self, account_id: int, bucket: str, callback: Optional[Callable] = None):
        """bucket: 'followers' (quién me sigue) | 'friends' (a quienes sigo) | 'verified'
        (seguidores verificados; solo lo da twscrape). La lectura la enruta el router."""
        def _bg(ctx):
            users = self.r_actores(account_id, None, bucket, count=100)
            with db.transaccion() as _conn:
                _conn.execute("DELETE FROM x_users WHERE account_id = ? AND bucket = ?", (account_id, bucket))
                for pos, d in enumerate(users):
                    self._guardar_usuario(account_id, d, bucket, pos, conn=_conn)
            return len(users)
        task_manager.start_task(_bg, callback=callback, pass_context=True)
