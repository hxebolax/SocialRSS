# -*- coding: utf-8 -*-
"""Árbol de comentarios de una publicación y comentarios del usuario.

Mixin de RedditService. Descarga el hilo completo de una submission (resolviendo los "cargar más
comentarios" hasta el límite configurado) y también el buffer "Mis comentarios". La red va fuera de
transacción; los comentarios del buffer se persisten en un único bloque.
"""
import json

from app.services.reddit.reddit_common import (
    db, task_manager, config, log, RedditServiceError,
    _g, _nombre_autor, _ahora_iso, es_cuerpo_borrado)


class _ComentariosMixin:
    # ------------------------------------------------------------------
    # Hilo de comentarios de una publicación (para el visor)
    # ------------------------------------------------------------------
    def cargar_hilo_async(self, account_id: int, post_id: str, callback=None):
        """Descarga el post y su árbol de comentarios en orden de lectura. Devuelve
        {'post': {...}, 'comments': [{..., 'depth': n}]}. `post_id` es el id base36 (sin t3_)."""
        def _bg(ctx):
            return self._cargar_hilo_sync(account_id, post_id)

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _cargar_hilo_sync(self, account_id: int, post_id: str) -> dict:
        client = self.obtener_cliente(account_id)
        try:
            sub = client.submission(id=post_id)
            sub.comment_sort = config.get("reddit", "comment_sort", "confidence")
            # Cargar el post (accede a un atributo para forzar el fetch perezoso).
            _ = sub.title
            limite_more = int(config.get("reddit", "replace_more_limit", 32))
            sub.comments.replace_more(limit=limite_more)
            comentarios = []
            for c in sub.comments.list():
                if type(c).__name__ != "Comment":
                    continue
                comentarios.append(self._comment_a_valores(c, account_id))
        except Exception as e:
            self._marcar_revocada_si_token(account_id, e)
            ra = self._es_limite_429(e)
            if ra is not None:
                self.registrar_limite_429(account_id, ra)
            raise RedditServiceError(self._mensaje_error(e))
        return {"post": self._post_a_valores(sub, account_id), "comments": comentarios}

    # ------------------------------------------------------------------
    # Buffer "Mis comentarios"
    # ------------------------------------------------------------------
    def cargar_mis_comentarios_async(self, account_id: int, limit: int = None,
                                     cargar_mas: bool = False, callback=None):
        """Descarga los comentarios del usuario y los cachea (buffer 'comments')."""
        lim = int(limit or config.get("reddit", "limit_per_load", 25))

        def _bg(ctx):
            return self._cargar_mis_comentarios_sync(account_id, lim, cargar_mas)

        task_manager.start_task(
            _bg, callback=callback, pass_context=True,
            unique_key=f"reddit:{account_id}:comments:{'older' if cargar_mas else 'fresh'}")

    def _cargar_mis_comentarios_sync(self, account_id, limit, cargar_mas) -> int:
        client = self.obtener_cliente(account_id)
        after = None
        if cargar_mas:
            after = getattr(self, "_after", {}).get(f"{account_id}:comments")
            if after is None:
                return 0
        params = {"after": after} if after else None
        try:
            data = []
            ultimo = None
            for c in client.user.me().comments.new(limit=limit, params=params):
                if type(c).__name__ != "Comment":
                    continue
                v = self._comment_a_valores(c, account_id)
                data.append(v)
                ultimo = v["fullname"]
        except Exception as e:
            self._marcar_revocada_si_token(account_id, e)
            ra = self._es_limite_429(e)
            if ra is not None:
                self.registrar_limite_429(account_id, ra)
            raise RedditServiceError(self._mensaje_error(e))
        if not hasattr(self, "_after"):
            self._after = {}
        if ultimo:
            self._after[f"{account_id}:comments"] = ultimo
        return self._persistir_comentarios(account_id, "comments", data, reemplazar=not cargar_mas)

    def _persistir_comentarios(self, account_id, timeline, comentarios, reemplazar):
        if not comentarios and not reemplazar:
            return 0
        nuevos = 0
        with db.transaccion():
            if reemplazar:
                db.execute("DELETE FROM reddit_timeline_comments WHERE account_id = ? AND timeline = ?",
                           (account_id, timeline))
            base_pos = 0
            if not reemplazar:
                r = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 FROM reddit_timeline_comments "
                                 "WHERE account_id = ? AND timeline = ?", (account_id, timeline))
                base_pos = r[0] if r else 0
            for i, v in enumerate(comentarios):
                # Política de Reddit: no conservar comentarios borrados.
                if es_cuerpo_borrado(v.get("body")):
                    db.execute("DELETE FROM reddit_comments WHERE id = ?", (v["id"],))
                    continue
                db.execute("""
                    INSERT INTO reddit_comments (
                        id, account_id, fullname, comment_id, link_id, parent_id, subreddit, author,
                        body, created_utc, score, depth, permalink, saved, likes, edited, stickied,
                        is_submitter, distinguished, fetched_at)
                    VALUES (:id,:account_id,:fullname,:comment_id,:link_id,:parent_id,:subreddit,
                        :author,:body,:created_utc,:score,:depth,:permalink,:saved,:likes,:edited,
                        :stickied,:is_submitter,:distinguished,:fetched_at)
                    ON CONFLICT(id) DO UPDATE SET
                        body=excluded.body, score=excluded.score, saved=excluded.saved,
                        likes=excluded.likes, edited=excluded.edited, fetched_at=excluded.fetched_at
                """, v)
                if db.execute("""
                    INSERT OR IGNORE INTO reddit_timeline_comments (account_id, timeline, comment_db_id, position)
                    VALUES (?, ?, ?, ?)
                """, (account_id, timeline, v["id"], base_pos + i)):
                    nuevos += 1
        return nuevos

    # ------------------------------------------------------------------
    # Extracción
    # ------------------------------------------------------------------
    def _comment_a_valores(self, c, account_id) -> dict:
        fullname = _g(c, "fullname") or f"t1_{_g(c, 'id') or ''}"
        edited = _g(c, "edited")
        likes = _g(c, "likes")
        return {
            "id": f"{account_id}:{fullname}",
            "account_id": account_id,
            "fullname": fullname,
            "comment_id": _g(c, "id") or "",
            "link_id": _g(c, "link_id") or "",
            "parent_id": _g(c, "parent_id") or "",
            "subreddit": str(_g(c, "subreddit") or ""),
            "author": _nombre_autor(c),
            "body": _g(c, "body") or "",
            "created_utc": float(_g(c, "created_utc") or 0),
            "score": int(_g(c, "score") or 0),
            "depth": int(_g(c, "depth") or 0),
            "permalink": _g(c, "permalink") or "",
            "saved": 1 if _g(c, "saved") else 0,
            "likes": (1 if likes is True else (0 if likes is False else None)),
            "edited": 1 if edited else 0,
            "stickied": 1 if _g(c, "stickied") else 0,
            "is_submitter": 1 if _g(c, "is_submitter") else 0,
            "distinguished": _g(c, "distinguished"),
            "fetched_at": _ahora_iso(),
        }
