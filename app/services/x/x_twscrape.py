# -*- coding: utf-8 -*-
"""Motor de LECTURA alternativo de X con twscrape.

twscrape (https://github.com/vladkens/twscrape) es un scraper de la API GraphQL de X, de SOLO
LECTURA pero muy mantenido (sus IDs de GraphQL se actualizan al día) y con MÁS endpoints de lectura
que los que cablea twikit. En este cliente las dos librerías se COMPLEMENTAN: twikit escribe y lee,
twscrape aporta lecturas frescas y endpoints extra (tweets+respuestas de un usuario, solo
multimedia, seguidores verificados, hilo completo, comunidades...). El enrutado lo decide
`x_router`; aquí solo vive el motor twscrape.

Claves del diseño:
- Se importa de forma DIFERIDA (no al arrancar); si twscrape no está instalado, `disponible()`
  devuelve False y el router usa solo twikit.
- Reutiliza las MISMAS cookies cifradas que ya guardamos para twikit: se siembra una cuenta en el
  pool de twscrape con esas cookies (no hay segundo login).
- Sus métodos `_tws_*` devuelven los MISMOS dicts que los adaptadores de twikit
  (`_tweet_a_dict`/`_user_a_dict`), de modo que el guardado en la BD y la UI no cambian.
"""
import os
import json

from app.services.x.x_common import (
    asyncio, Any, Dict, List, Optional, db, security, config, log, _g, XServiceError)

# Import DIFERIDO: se resuelve en _cargar_twscrape() al primer uso, no al importar el módulo.
_TWS = None  # cache del módulo (API, AccountsPool, gather) o False si no está disponible.


def _cargar_twscrape():
    """Importa twscrape una sola vez (diferido). Devuelve un dict con sus símbolos o None."""
    global _TWS
    if _TWS is not None:
        return _TWS or None
    try:
        # Desactivar la telemetría anónima de twscrape (privacidad, como el resto de la app).
        os.environ.setdefault("TWS_TELEMETRY", "0")
        # Solo avisos de twscrape (su INFO/WARNING por petición no aporta nada al usuario).
        os.environ.setdefault("TWS_LOG_LEVEL", "ERROR")
        # En la versión COMPILADA (sin consola) sys.stderr es None y el logger de twscrape
        # (loguru: logger.add(sys.stderr) al importarse) revienta con "Cannot log to objects of
        # type 'NoneType'": twscrape nunca llegaba a cargarse fuera del entorno de desarrollo.
        import sys as _sys
        if _sys.stderr is None:
            _sys.stderr = open(os.devnull, "w", encoding="utf-8")
        from twscrape import API, AccountsPool, gather
        _TWS = {"API": API, "AccountsPool": AccountsPool, "gather": gather}
        log.info("twscrape disponible: motor de lectura complementario de X activado.")
    except Exception as e:  # pragma: no cover - entorno sin twscrape
        _TWS = False
        log.info(f"twscrape no disponible ({e}). X usará solo twikit para leer.")
    return _TWS or None


class _TwscrapeMixin:
    """Motor de lectura twscrape para XService. Añade `_tws_*` que devuelven dicts uniformes."""

    @staticmethod
    def twscrape_disponible() -> bool:
        return _cargar_twscrape() is not None

    # ------------------------------------------------------------------
    # Bucle asyncio (reutiliza el de _SesionMixin) sin exigir twikit
    # ------------------------------------------------------------------
    def _run_tws(self, coro, timeout: float = 60.0):
        """Ejecuta una corrutina de twscrape en el mismo bucle asyncio del servicio."""
        self._ensure_loop()
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # Cancelar la corrutina al agotarse el plazo (no dejarla viva en el bucle).
            future.cancel()
            raise XServiceError("twscrape: la operación tardó demasiado y se canceló.")
        except XServiceError:
            raise
        except Exception as e:
            raise XServiceError(f"twscrape: {e}")

    # ------------------------------------------------------------------
    # Pool/API por cuenta (sembrado con las cookies que ya tenemos)
    # ------------------------------------------------------------------
    def _tws_db_path(self, account_id: int) -> str:
        from app.core.paths import get_data_path
        p = get_data_path(f"database/x_tws_{account_id}.db")
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    def _tws_api(self, account_id: int):
        """Devuelve (con caché) el objeto API de twscrape para una cuenta, sembrado con sus
        cookies. Lanza XServiceError si twscrape no está o no hay cookies válidas."""
        tws = _cargar_twscrape()
        if not tws:
            raise XServiceError("twscrape no está instalado.")
        cache = getattr(self, "_tws_apis", None)
        if cache is None:
            cache = self._tws_apis = {}
        api = cache.get(account_id)
        if api is not None:
            return api
        raw = security.obtener_credencial(f"{self.NS}:{account_id}", "cookies")
        if not raw:
            raise XServiceError(f"No hay sesión guardada para la cuenta de X {account_id}.")
        cookies = json.loads(raw)
        if not cookies.get("ct0") or not cookies.get("auth_token"):
            raise XServiceError("Faltan las cookies necesarias (auth_token y ct0) para twscrape.")
        # Se pasan como JSON (parse_cookies de twscrape lo acepta y es más robusto que "k=v; k=v").
        cookie_str = json.dumps({k: v for k, v in cookies.items() if v})
        screen = (self._yo(account_id).get("screen_name") or f"acc{account_id}").lstrip("@")
        # raise_when_no_account=True: si la cuenta no queda activa (cookies caducadas), las lecturas
        # lanzan y el router cae a twikit en vez de devolver vacío en silencio.
        api = tws["API"](self._tws_db_path(account_id), raise_when_no_account=True)
        self._run_tws(self._tws_sembrar(api, screen, cookie_str), timeout=30)
        cache[account_id] = api
        return api

    async def _tws_sembrar(self, api, username: str, cookie_str: str):
        """Siembra la cuenta en el pool de twscrape si no estaba (con las cookies dadas)."""
        existente = await api.pool.get_account(username)
        if existente is None:
            await api.pool.add_account_cookies(username, cookie_str)

    def _tws_resembrar(self, account_id: int):
        """Tras reimportar cookies: borra la cuenta del pool y la caché para que se re-siembre con
        las cookies nuevas en el próximo uso."""
        cache = getattr(self, "_tws_apis", None)
        api = cache.pop(account_id, None) if cache else None
        screen = (self._yo(account_id).get("screen_name") or f"acc{account_id}").lstrip("@")
        if api is not None:
            try:
                self._run_tws(api.pool.delete_accounts([screen]), timeout=20)
            except Exception as e:
                log.debug(f"twscrape: no se pudo limpiar la cuenta del pool: {e}")

    def _tws_limpiar(self, account_id: int):
        """Elimina la caché de API y el archivo de pool de una cuenta (al borrarla)."""
        cache = getattr(self, "_tws_apis", None)
        if cache:
            cache.pop(account_id, None)
        try:
            p = self._tws_db_path(account_id)
            for suf in ("", "-wal", "-shm"):
                ruta = p + suf
                if os.path.exists(ruta):
                    os.remove(ruta)
        except Exception as e:
            log.debug(f"twscrape: no se pudo borrar el pool de la cuenta {account_id}: {e}")

    # ------------------------------------------------------------------
    # Adaptadores → MISMA forma que _tweet_a_dict / _user_a_dict (twikit)
    # ------------------------------------------------------------------
    def _tweet_tws_a_dict(self, account_id: int, t: Any) -> Dict[str, Any]:
        """Convierte un Tweet de twscrape al dict que espera `_guardar_tweet`."""
        rt = _g(t, "retweetedTweet", default=None)
        base = rt or t
        autor = _g(base, "user")
        autor_rt = _g(t, "user")
        media = []
        m = _g(base, "media", default=None)
        if m is not None:
            for p in (_g(m, "photos", default=[]) or []):
                media.append({"type": "photo", "url": _g(p, "url", default="") or "",
                              "video": False, "alt": ""})
            for v in (_g(m, "videos", default=[]) or []):
                url = ""
                variants = _g(v, "variants", default=[]) or []
                if variants:
                    url = _g(variants[-1], "url", default="") or ""
                media.append({"type": "video", "url": url or (_g(v, "thumbnailUrl", default="") or ""),
                              "video": True, "alt": ""})
            for a in (_g(m, "animated", default=[]) or []):
                media.append({"type": "animated_gif", "url": _g(a, "thumbnailUrl", default="") or "",
                              "video": True, "alt": ""})
        fecha = _g(base, "date", default=None)
        created = fecha.isoformat() if hasattr(fecha, "isoformat") else (str(fecha) if fecha else "")
        quoted = _g(base, "quotedTweet", default=None)
        return {
            "tweet_id": str(_g(base, "id", default="") or ""),
            "author_id": str(_g(autor, "id", default="") or ""),
            "author_name": _g(autor, "displayname", default="") or "",
            "author_screen_name": _g(autor, "username", default="") or "",
            "text": _g(base, "rawContent", default="") or "",
            "created_at": created,
            "lang": _g(base, "lang", default="") or "",
            # twscrape no expone tu estado por tweet; se rellena al interactuar (vía twikit).
            "liked": 0, "retweeted": 0, "bookmarked": 0,
            "favorite_count": int(_g(base, "likeCount", default=0) or 0),
            "retweet_count": int(_g(base, "retweetCount", default=0) or 0),
            "reply_count": int(_g(base, "replyCount", default=0) or 0),
            "quote_count": int(_g(base, "quoteCount", default=0) or 0),
            "view_count": int(_g(base, "viewCount", default=0) or 0),
            "in_reply_to": str(_g(base, "inReplyToTweetId", default="") or ""),
            "conversation_id": str(_g(base, "conversationId", default="") or ""),
            "quoted_id": str(_g(quoted, "id", default="") or ""),
            "retweeted_by": (_g(autor_rt, "username", default="") if rt else ""),
            "media_json": json.dumps(media),
            "in_reply_to_screen_name": str(_g(_g(base, "inReplyToUser", default=None),
                                              "username", default="") or ""),
            "quoted_author": (f"{_g(_g(quoted, 'user'), 'displayname', default='') or ''} "
                              f"(@{_g(_g(quoted, 'user'), 'username', default='') or ''})"
                              if quoted is not None else ""),
            "quoted_text": str(_g(quoted, "rawContent", default="") or "") if quoted is not None else "",
            "poll_json": "",
        }

    @staticmethod
    def _user_tws_a_dict(u: Any) -> Dict[str, Any]:
        """Convierte un User de twscrape al dict que espera `_guardar_usuario`."""
        return {
            "user_id": str(_g(u, "id", default="") or ""),
            "screen_name": _g(u, "username", default="") or "",
            "name": _g(u, "displayname", default="") or "",
            "description": (_g(u, "rawDescription", default="") or "").replace("\n", " "),
            "followers": int(_g(u, "followersCount", default=0) or 0),
            "following": int(_g(u, "friendsCount", default=0) or 0),
            "statuses": int(_g(u, "statusesCount", default=0) or 0),
            "is_following": 0, "is_blocked": 0, "is_muted": 0,
            "verified": 1 if _g(u, "verified", default=False) else 0,
        }

    # ------------------------------------------------------------------
    # Lecturas (devuelven listas de dicts uniformes; las consume el router)
    # ------------------------------------------------------------------
    def _tws_timeline(self, account_id: int, tipo: str, count: int) -> List[Dict[str, Any]]:
        """Timelines que twscrape sabe servir: search:, list:, bookmarks, mentions. (Las
        cronologías de usuario van por _tws_user_tweets, que admite el modo.)"""
        api = self._tws_api(account_id)
        gather = _cargar_twscrape()["gather"]

        async def _c():
            if tipo.startswith("search:"):
                gen = api.search(tipo.split(":", 1)[1], limit=count)
            elif tipo.startswith("list:"):
                gen = api.list_timeline(int(tipo.split(":", 1)[1]), limit=count)
            elif tipo == "bookmarks":
                gen = api.bookmarks(limit=count)
            elif tipo == "mentions":
                screen = self._yo(account_id).get("screen_name", "") or ""
                gen = api.search(f"@{screen}", limit=count)
            else:
                raise XServiceError(f"twscrape no soporta el timeline '{tipo}'.")
            return await gather(gen)

        return [self._tweet_tws_a_dict(account_id, t) for t in self._run_tws(_c(), timeout=70)]

    def _tws_user_tweets(self, account_id: int, uid: str, modo: str, count: int) -> List[Dict[str, Any]]:
        """Cronología de un usuario. modo: 'tweets' | 'replies' | 'media'."""
        api = self._tws_api(account_id)
        gather = _cargar_twscrape()["gather"]

        async def _c():
            if modo == "replies":
                gen = api.user_tweets_and_replies(int(uid), limit=count)
            elif modo == "media":
                gen = api.user_media(int(uid), limit=count)
            else:
                gen = api.user_tweets(int(uid), limit=count)
            return await gather(gen)

        return [self._tweet_tws_a_dict(account_id, t) for t in self._run_tws(_c(), timeout=70)]

    def _tws_hilo(self, account_id: int, tweet_id: str) -> List[Dict[str, Any]]:
        """Hilo completo de un tweet (foco + toda la conversación), con tweet_thread."""
        api = self._tws_api(account_id)
        gather = _cargar_twscrape()["gather"]

        async def _c():
            focal = await api.tweet_details(int(tweet_id))
            # tweet_thread filtra por conversationId == el id que se le pasa, y conversationId
            # es el de la RAÍZ: con una respuesta devolvía solo el propio tuit. Se le pasa la
            # raíz de la conversación y se ordena por fecha (la raíz primero).
            raiz = int(_g(focal, "conversationId", default=0) or tweet_id)
            hilo = await gather(api.tweet_thread(raiz, limit=200))
            vistos, salida = set(), []
            for t in ([focal] if focal is not None else []) + list(hilo):
                tid = str(_g(t, "id", default=""))
                if tid and tid not in vistos:
                    vistos.add(tid)
                    salida.append(t)
            salida.sort(key=lambda t: str(_g(t, "date", default="") or ""))
            return salida

        return [self._tweet_tws_a_dict(account_id, t) for t in self._run_tws(_c(), timeout=70)]

    def _tws_actores(self, account_id: int, uid: str, bucket: str, count: int) -> List[Dict[str, Any]]:
        """Personas. bucket: 'followers' | 'friends' (a quienes sigo) | 'verified'."""
        api = self._tws_api(account_id)
        gather = _cargar_twscrape()["gather"]

        async def _c():
            if bucket == "followers":
                gen = api.followers(int(uid), limit=count)
            elif bucket in ("verified", "verified_followers"):
                gen = api.verified_followers(int(uid), limit=count)
            else:
                gen = api.following(int(uid), limit=count)
            return await gather(gen)

        return [self._user_tws_a_dict(u) for u in self._run_tws(_c(), timeout=70)]

    def _tws_buscar_usuarios(self, account_id: int, query: str, count: int = 40) -> List[Dict[str, Any]]:
        api = self._tws_api(account_id)
        gather = _cargar_twscrape()["gather"]

        async def _c():
            return await gather(api.search_user(query, limit=count))

        return [self._user_tws_a_dict(u) for u in self._run_tws(_c(), timeout=60)]

    def _tws_perfil(self, account_id: int, screen_name: str) -> Optional[Dict[str, Any]]:
        api = self._tws_api(account_id)

        async def _c():
            return await api.user_by_login(screen_name.lstrip("@"))

        u = self._run_tws(_c(), timeout=45)
        if not u:
            return None
        return {
            "user_id": str(_g(u, "id", default="") or ""),
            "screen_name": _g(u, "username", default="") or "",
            "name": _g(u, "displayname", default="") or "",
            "description": _g(u, "rawDescription", default="") or "",
            "followers": int(_g(u, "followersCount", default=0) or 0),
            "following": int(_g(u, "friendsCount", default=0) or 0),
            "statuses": int(_g(u, "statusesCount", default=0) or 0),
            "verified": bool(_g(u, "verified", default=False)),
        }

    def _tws_resolver_uid(self, account_id: int, screen_name: str) -> Optional[Dict[str, str]]:
        """Resuelve un @usuario a su id numérico y nombre (para abrir su cronología)."""
        p = self._tws_perfil(account_id, screen_name)
        if not p or not p.get("user_id"):
            return None
        return {"user_id": p["user_id"], "screen_name": p["screen_name"], "name": p["name"]}

    def _tws_tendencias(self, account_id: int) -> List[Dict[str, Any]]:
        api = self._tws_api(account_id)
        gather = _cargar_twscrape()["gather"]

        async def _c():
            return await gather(api.trends("trending", limit=40))

        trends = self._run_tws(_c(), timeout=45)
        salida = []
        for t in trends:
            nombre = _g(t, "name", default="") or ""
            if nombre:
                salida.append({"name": nombre,
                               "count": int(_g(t, "tweet_count", "tweetCount", default=0) or 0)})
        return salida
