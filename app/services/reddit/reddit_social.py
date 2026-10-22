# -*- coding: utf-8 -*-
"""Suscripciones a subreddits, perfiles de usuario, búsqueda y multireddits.

Mixin de RedditService. La búsqueda de PUBLICACIONES se integra en el flujo de timelines (buffer
"search:<texto>", resuelto en reddit_timeline); aquí están la búsqueda de subreddits y usuarios,
las suscripciones, los perfiles y los multireddits.
"""
from app.services.reddit.reddit_common import (
    db, task_manager, config, log, RedditServiceError, _g, _ahora_iso)


class _SocialMixin:
    # ------------------------------------------------------------------
    # Suscripciones
    # ------------------------------------------------------------------
    def cargar_suscripciones_async(self, account_id, callback=None):
        def _bg(ctx):
            return self._cargar_suscripciones_sync(account_id)
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"reddit:{account_id}:subreddits")

    def _cargar_suscripciones_sync(self, account_id) -> int:
        client = self.obtener_cliente(account_id)
        try:
            subs = []
            for s in client.user.subreddits(limit=None):
                subs.append(self._subreddit_a_valores(s, account_id, es_suscriptor=True))
        except Exception as e:
            self._marcar_revocada_si_token(account_id, e)
            ra = self._es_limite_429(e)
            if ra is not None:
                self.registrar_limite_429(account_id, ra)
            raise RedditServiceError(self._mensaje_error(e))
        with db.transaccion():
            db.execute("DELETE FROM reddit_subreddits WHERE account_id = ? AND user_is_subscriber = 1",
                       (account_id,))
            for v in subs:
                db.execute("""
                    INSERT INTO reddit_subreddits (
                        id, account_id, name, title, public_description, subscribers, over_18,
                        user_is_subscriber, user_is_moderator, subreddit_type, icon_url, fetched_at)
                    VALUES (:id,:account_id,:name,:title,:public_description,:subscribers,:over_18,
                        :user_is_subscriber,:user_is_moderator,:subreddit_type,:icon_url,:fetched_at)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title, public_description=excluded.public_description,
                        subscribers=excluded.subscribers, user_is_subscriber=excluded.user_is_subscriber,
                        user_is_moderator=excluded.user_is_moderator, fetched_at=excluded.fetched_at
                """, v)
        return len(subs)

    def _subreddit_a_valores(self, s, account_id, es_suscriptor=False) -> dict:
        nombre = str(_g(s, "display_name") or _g(s, "name") or "")
        return {
            "id": f"{account_id}:{nombre.lower()}",
            "account_id": account_id,
            "name": nombre,
            "title": _g(s, "title") or "",
            "public_description": _g(s, "public_description") or "",
            "subscribers": int(_g(s, "subscribers") or 0),
            "over_18": 1 if _g(s, "over18", "over_18") else 0,
            "user_is_subscriber": 1 if (es_suscriptor or _g(s, "user_is_subscriber")) else 0,
            "user_is_moderator": 1 if _g(s, "user_is_moderator") else 0,
            "subreddit_type": _g(s, "subreddit_type") or "",
            "icon_url": _g(s, "icon_img") or _g(s, "community_icon") or "",
            "fetched_at": _ahora_iso(),
        }

    def suscribirse(self, account_id, subreddit, suscribir=True) -> bool:
        nombre = (subreddit or "").lstrip("r/").lstrip("/").strip()

        def _fn(client):
            sr = client.subreddit(nombre)
            sr.subscribe() if suscribir else sr.unsubscribe()
            return True
        self._ejecutar(account_id, _fn)
        db.execute("UPDATE reddit_subreddits SET user_is_subscriber = ? WHERE id = ?",
                   (1 if suscribir else 0, f"{account_id}:{nombre.lower()}"))
        return True

    # ------------------------------------------------------------------
    # Perfil de usuario
    # ------------------------------------------------------------------
    def cargar_perfil_async(self, account_id, username, callback=None):
        def _bg(ctx):
            return self._cargar_perfil_sync(account_id, username)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _cargar_perfil_sync(self, account_id, username) -> dict:
        nombre = (username or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            r = client.redditor(nombre)
            _ = r.link_karma  # fuerza el fetch
            bio = ""
            try:
                bio = getattr(r.subreddit, "public_description", "") or ""
            except Exception:
                pass
            return {
                "name": nombre,
                "post_karma": int(_g(r, "link_karma") or 0),
                "comment_karma": int(_g(r, "comment_karma") or 0),
                "total_karma": int(_g(r, "total_karma") or 0),
                "created_utc": float(_g(r, "created_utc") or 0),
                "is_employee": bool(_g(r, "is_employee")),
                "is_mod": bool(_g(r, "is_mod")),
                "is_friend": bool(_g(r, "is_friend")),
                "bio": bio,
            }
        return self._ejecutar(account_id, _fn)

    def seguir_usuario(self, account_id, username, seguir=True) -> bool:
        nombre = (username or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            r = client.redditor(nombre)
            r.friend() if seguir else r.unfriend()
            return True
        return self._ejecutar(account_id, _fn)

    def bloquear_usuario(self, account_id, username) -> bool:
        nombre = (username or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            client.redditor(nombre).block()
            return True
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Búsqueda de subreddits y usuarios (los posts van por timeline "search:")
    # ------------------------------------------------------------------
    def buscar_subreddits(self, account_id, query) -> list:
        def _fn(client):
            res = []
            for s in client.subreddits.search(query):
                res.append({"name": str(_g(s, "display_name") or ""),
                            "title": _g(s, "title") or "",
                            "subscribers": int(_g(s, "subscribers") or 0),
                            "over_18": bool(_g(s, "over18", "over_18")),
                            "description": _g(s, "public_description") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def buscar_usuarios(self, account_id, query) -> list:
        def _fn(client):
            res = []
            for r in client.redditors.search(query):
                res.append({"name": str(_g(r, "name") or "")})
            return res
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Multireddits
    # ------------------------------------------------------------------
    def cargar_multireddits_async(self, account_id, callback=None):
        def _bg(ctx):
            return self._cargar_multireddits_sync(account_id)
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"reddit:{account_id}:multireddits")

    def _cargar_multireddits_sync(self, account_id) -> list:
        def _fn(client):
            res = []
            for m in client.user.multireddits():
                # 'name' es el nombre interno (para la API); 'display' el visible; 'owner' el dueño.
                # Con owner+name se construye el buffer navegable "multi:<owner>:<name>".
                owner = _g(m, "owner")
                res.append({"name": _g(m, "name") or "",
                            "display": _g(m, "display_name") or _g(m, "name") or "",
                            "owner": str(_g(owner, "name") or owner or ""),
                            "path": _g(m, "path") or ""})
            return res
        multis = self._ejecutar(account_id, _fn)
        try:
            config.set("reddit", "multireddits_cache", multis)
        except Exception:
            pass
        return multis
