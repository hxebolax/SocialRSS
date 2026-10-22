# -*- coding: utf-8 -*-
"""Carga de feeds/listados de publicaciones y su persistencia.

Mixin de RedditService. Descarga los buffers de posts (portada, populares, r/all, subreddits,
perfiles y las listas del usuario: enviados, guardados, votados, ocultos) y los cachea en la base
de datos. Toda la RED ocurre FUERA de la transacción de escritura (regla del proyecto): primero se
descarga a memoria y luego se persiste en un único bloque.
"""
import json

from app.services.reddit.reddit_common import (
    db, task_manager, config, log, RedditServiceError,
    _g, _nombre_autor, _ahora_iso, _URL_RE, es_cuerpo_borrado)

# Órdenes de feed admitidos como sufijo del buffer ("sub:python:top").
_SORTS = ("hot", "new", "top", "rising", "controversial")
# Buffers que son listas del propio usuario (mezclan posts y comentarios; aquí solo posts).
_LISTAS_USUARIO = ("submitted", "saved", "upvoted", "downvoted", "hidden", "gilded")


class _TimelineMixin:
    def cargar_timeline_async(self, account_id: int, timeline_type: str = "home",
                              limit: int = None, cargar_mas: bool = False,
                              callback=None):
        """Descarga un buffer de posts y lo cachea. `timeline_type`: home[:sort], popular[:sort],
        all[:sort], sub:<nombre>[:sort], user:<nombre>, submitted, saved, upvoted, downvoted,
        hidden. `cargar_mas` pagina con el 'after' guardado."""
        lim = int(limit or config.get("reddit", "limit_per_load", 25))

        def _bg(ctx):
            return self._cargar_timeline_sync(account_id, timeline_type, lim, cargar_mas)

        task_manager.start_task(
            _bg, callback=callback, pass_context=True,
            unique_key=f"reddit:{account_id}:{timeline_type}:{'older' if cargar_mas else 'fresh'}")

    # ------------------------------------------------------------------
    # Descarga + persistencia
    # ------------------------------------------------------------------
    def _cargar_timeline_sync(self, account_id: int, timeline_type: str, limit: int,
                              cargar_mas: bool) -> int:
        """Descarga (RED) y persiste. Devuelve el número de posts nuevos añadidos a la relación."""
        client = self.obtener_cliente(account_id)
        after = None
        if cargar_mas:
            after = self._after.get(f"{account_id}:{timeline_type}") if hasattr(self, "_after") else None
            if after is None:
                return 0  # sin página previa cargada: nada que continuar
        try:
            listing = self._resolver_listing(client, account_id, timeline_type, limit, after)
            posts = []
            ultimo_fullname = None
            for sub in listing:
                # Las listas del usuario (saved/upvoted...) mezclan posts y comentarios: aquí solo
                # se procesan las submissions; los comentarios se cachean en su propio mixin.
                if type(sub).__name__ != "Submission":
                    continue
                valores = self._post_a_valores(sub, account_id)
                posts.append(valores)
                ultimo_fullname = valores["fullname"]
        except Exception as e:
            self._marcar_revocada_si_token(account_id, e)
            exc = self._es_limite_429(e)
            if exc is not None:
                self.registrar_limite_429(account_id, exc)
            raise RedditServiceError(self._mensaje_error(e))

        # Guardar el cursor de paginación (el fullname del último elemento de la página).
        if not hasattr(self, "_after"):
            self._after = {}
        if ultimo_fullname:
            self._after[f"{account_id}:{timeline_type}"] = ultimo_fullname
        elif not cargar_mas:
            self._after.pop(f"{account_id}:{timeline_type}", None)

        return self._persistir_posts(account_id, timeline_type, posts, reemplazar=not cargar_mas)

    def _resolver_listing(self, client, account_id, timeline_type, limit, after):
        """Traduce el timeline_type a la llamada PRAW correspondiente (devuelve un iterable)."""
        params = {"after": after} if after else None

        def _feed(obj, sort):
            tf = config.get("reddit", "time_filter", "day")
            if sort == "new":
                return obj.new(limit=limit, params=params)
            if sort == "top":
                return obj.top(time_filter=tf, limit=limit, params=params)
            if sort == "rising":
                return obj.rising(limit=limit, params=params)
            if sort == "controversial":
                return obj.controversial(time_filter=tf, limit=limit, params=params)
            return obj.hot(limit=limit, params=params)

        partes = timeline_type.split(":")
        base = partes[0]

        if base == "home":
            sort = partes[1] if len(partes) > 1 and partes[1] in _SORTS else "hot"
            return _feed(client.front, sort)
        if base in ("popular", "all"):
            sort = partes[1] if len(partes) > 1 and partes[1] in _SORTS else "hot"
            return _feed(client.subreddit(base), sort)
        if base == "sub":
            nombre = partes[1] if len(partes) > 1 else ""
            sort = partes[2] if len(partes) > 2 and partes[2] in _SORTS else "hot"
            return _feed(client.subreddit(nombre), sort)
        if base == "user":
            nombre = partes[1] if len(partes) > 1 else ""
            return client.redditor(nombre).submissions.new(limit=limit, params=params)
        if base == "multi":
            # "multi:<dueño>:<nombre>[:sort]" navega un multireddit como un feed más. Los nombres de
            # usuario y de multireddit no contienen ':', así que el troceo por ':' es seguro.
            owner = partes[1] if len(partes) > 1 else ""
            nombre = partes[2] if len(partes) > 2 else ""
            sort = partes[3] if len(partes) > 3 and partes[3] in _SORTS else "hot"
            return _feed(client.multireddit(redditor=owner, name=nombre), sort)
        if base == "search":
            # "search:<texto>" busca en todo Reddit; el texto puede contener ':'.
            texto = timeline_type.split(":", 1)[1] if ":" in timeline_type else ""
            return client.subreddit("all").search(texto, sort="relevance", limit=limit, params=params)
        if base in _LISTAS_USUARIO:
            yo = client.user.me()
            if base == "submitted":
                return yo.submissions.new(limit=limit, params=params)
            return getattr(yo, base)(limit=limit, params=params)
        raise RedditServiceError(f"Buffer de Reddit no reconocido: {timeline_type}")

    def _persistir_posts(self, account_id, timeline, posts, reemplazar):
        """Guarda los posts (UPSERT que NO borra, para no disparar el ON DELETE CASCADE de la
        relación) y la relación buffer↔post. Devuelve cuántas relaciones nuevas se crearon."""
        if not posts and not reemplazar:
            return 0
        nuevos = 0
        with db.transaccion():
            if reemplazar:
                db.execute("DELETE FROM reddit_timeline_posts WHERE account_id = ? AND timeline = ?",
                           (account_id, timeline))
            # Posición base: si se está paginando (no reemplazar), continuar tras la última.
            base_pos = 0
            if not reemplazar:
                r = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 FROM reddit_timeline_posts "
                                 "WHERE account_id = ? AND timeline = ?", (account_id, timeline))
                base_pos = r[0] if r else 0
            for i, v in enumerate(posts):
                # Política de Reddit: no conservar contenido borrado. Si el cuerpo está borrado,
                # se purga la copia local (si la hubiera) y no se añade a la relación.
                if v.get("is_self") and es_cuerpo_borrado(v.get("selftext")):
                    db.execute("DELETE FROM reddit_posts WHERE id = ?", (v["id"],))
                    continue
                db.execute("""
                    INSERT INTO reddit_posts (
                        id, account_id, fullname, post_id, subreddit, author, title, selftext, url,
                        permalink, created_utc, score, upvote_ratio, num_comments, over_18, spoiler,
                        stickied, locked, saved, hidden, clicked, likes, link_flair_text, is_self,
                        is_gallery, post_hint, domain, edited, distinguished, media_json, urls,
                        crosspost_parent, fetched_at)
                    VALUES (:id,:account_id,:fullname,:post_id,:subreddit,:author,:title,:selftext,
                        :url,:permalink,:created_utc,:score,:upvote_ratio,:num_comments,:over_18,
                        :spoiler,:stickied,:locked,:saved,:hidden,:clicked,:likes,:link_flair_text,
                        :is_self,:is_gallery,:post_hint,:domain,:edited,:distinguished,:media_json,
                        :urls,:crosspost_parent,:fetched_at)
                    ON CONFLICT(id) DO UPDATE SET
                        score=excluded.score, upvote_ratio=excluded.upvote_ratio,
                        num_comments=excluded.num_comments, saved=excluded.saved,
                        hidden=excluded.hidden, likes=excluded.likes, edited=excluded.edited,
                        selftext=excluded.selftext, link_flair_text=excluded.link_flair_text,
                        over_18=excluded.over_18, spoiler=excluded.spoiler, locked=excluded.locked,
                        stickied=excluded.stickied, fetched_at=excluded.fetched_at
                """, v)
                # db.execute devuelve rowcount (int): 1 si se insertó la relación, 0 si ya existía.
                if db.execute("""
                    INSERT OR IGNORE INTO reddit_timeline_posts (account_id, timeline, post_db_id, position)
                    VALUES (?, ?, ?, ?)
                """, (account_id, timeline, v["id"], base_pos + i)):
                    nuevos += 1
        return nuevos

    # ------------------------------------------------------------------
    # Extracción de datos de una Submission de PRAW
    # ------------------------------------------------------------------
    def _post_a_valores(self, sub, account_id) -> dict:
        fullname = _g(sub, "fullname") or f"t3_{_g(sub, 'id') or ''}"
        edited = _g(sub, "edited")
        likes = _g(sub, "likes")  # True/False/None
        flair = _g(sub, "link_flair_text")
        return {
            "id": f"{account_id}:{fullname}",
            "account_id": account_id,
            "fullname": fullname,
            "post_id": _g(sub, "id") or "",
            "subreddit": str(_g(sub, "subreddit") or ""),
            "author": _nombre_autor(sub),
            "title": _g(sub, "title") or "",
            "selftext": _g(sub, "selftext") or "",
            "url": _g(sub, "url") or "",
            "permalink": _g(sub, "permalink") or "",
            "created_utc": float(_g(sub, "created_utc") or 0),
            "score": int(_g(sub, "score") or 0),
            "upvote_ratio": float(_g(sub, "upvote_ratio") or 0),
            "num_comments": int(_g(sub, "num_comments") or 0),
            "over_18": 1 if _g(sub, "over_18") else 0,
            "spoiler": 1 if _g(sub, "spoiler") else 0,
            "stickied": 1 if _g(sub, "stickied") else 0,
            "locked": 1 if _g(sub, "locked") else 0,
            "saved": 1 if _g(sub, "saved") else 0,
            "hidden": 1 if _g(sub, "hidden") else 0,
            "clicked": 1 if _g(sub, "clicked") else 0,
            "likes": (1 if likes is True else (0 if likes is False else None)),
            "link_flair_text": flair,
            "is_self": 1 if _g(sub, "is_self") else 0,
            "is_gallery": 1 if _g(sub, "is_gallery") else 0,
            "post_hint": _g(sub, "post_hint"),
            "domain": _g(sub, "domain"),
            "edited": 1 if edited else 0,
            "distinguished": _g(sub, "distinguished"),
            "media_json": json.dumps(self._extraer_media(sub), ensure_ascii=False),
            "urls": json.dumps(self._extraer_urls(sub), ensure_ascii=False),
            "crosspost_parent": _g(sub, "crosspost_parent"),
            "fetched_at": _ahora_iso(),
        }

    @staticmethod
    def _extraer_media(sub) -> list:
        """Descripción de los adjuntos del post para la lectura accesible."""
        media = []
        try:
            hint = (_g(sub, "post_hint") or "").lower()
            if _g(sub, "is_gallery"):
                meta = _g(sub, "media_metadata") or {}
                n = len(meta) if isinstance(meta, dict) else 0
                media.append({"type": "gallery", "count": n,
                              "url": _g(sub, "url") or "", "alt": ""})
            elif _g(sub, "is_video") or "video" in hint:
                url = ""
                m = _g(sub, "media") or {}
                if isinstance(m, dict):
                    url = (m.get("reddit_video") or {}).get("fallback_url", "") or ""
                media.append({"type": "video", "url": url or (_g(sub, "url") or ""), "alt": ""})
            elif hint == "image" or (str(_g(sub, "url") or "").lower().endswith(
                    (".jpg", ".jpeg", ".png", ".gif", ".webp"))):
                media.append({"type": "image", "url": _g(sub, "url") or "", "alt": ""})
        except Exception:
            pass
        return media

    @staticmethod
    def _extraer_urls(sub) -> list:
        """Enlaces asociados al post: el enlace externo (si no es self) y los del cuerpo."""
        urls = []
        try:
            if not _g(sub, "is_self"):
                u = _g(sub, "url")
                if u:
                    urls.append(u)
            cuerpo = _g(sub, "selftext") or ""
            for m in _URL_RE.findall(cuerpo):
                if m not in urls:
                    urls.append(m)
        except Exception:
            pass
        return urls

    # ------------------------------------------------------------------
    # Helper de clasificación de 429 (retry_after)
    # ------------------------------------------------------------------
    def _es_limite_429(self, e):
        """Devuelve el retry_after (segundos) si e es un 429 de Reddit, o None."""
        from app.services.reddit.reddit_common import praw_exceptions
        exc = praw_exceptions()
        if exc and isinstance(e, getattr(exc, "TooManyRequests", ())):
            return getattr(e, "retry_after", None) or 60
        return None
