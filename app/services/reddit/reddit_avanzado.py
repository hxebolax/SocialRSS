# -*- coding: utf-8 -*-
"""Funciones avanzadas (Fase 4 de Reddit): hilos en directo (live threads), colecciones
(collections) y borradores (drafts).

Mixin de RedditService. La red va por self._ejecutar. Los hilos en directo se acceden por su id; las
colecciones son por subreddit (crear/añadir exige moderar); los borradores son personales.
"""
from app.services.reddit.reddit_common import _g, _nombre_autor


class _AvanzadoMixin:
    # ------------------------------------------------------------------ hilos en directo
    def crear_live_thread(self, account_id, titulo, descripcion="") -> str:
        def _fn(client):
            lt = client.live.create(title=titulo, description=descripcion or None)
            return _g(lt, "id") or ""
        return self._ejecutar(account_id, _fn)

    def info_live_thread(self, account_id, thread_id) -> dict:
        def _fn(client):
            lt = client.live(thread_id)
            return {"id": thread_id, "title": _g(lt, "title") or "",
                    "description": _g(lt, "description") or "",
                    "state": _g(lt, "state") or "", "nsfw": bool(_g(lt, "nsfw", default=False))}
        return self._ejecutar(account_id, _fn)

    def leer_updates_live(self, account_id, thread_id, limit=25) -> list:
        def _fn(client):
            res = []
            for u in client.live(thread_id).updates(limit=limit):
                res.append({"body": _g(u, "body") or "",
                            "author": _nombre_autor(u),
                            "stricken": bool(_g(u, "stricken", default=False))})
            return res
        return self._ejecutar(account_id, _fn)

    def publicar_update_live(self, account_id, thread_id, body) -> bool:
        def _fn(client):
            client.live(thread_id).contrib.add(body)
            return True
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------ colecciones
    def listar_collections(self, account_id, subreddit) -> list:
        def _fn(client):
            res = []
            for c in client.subreddit(subreddit).collections:
                enlaces = _g(c, "link_ids") or _g(c, "sorted_links") or []
                res.append({"id": _g(c, "collection_id") or _g(c, "id") or "",
                            "title": _g(c, "title") or "",
                            "description": _g(c, "description") or "",
                            "num_links": len(list(enlaces))})
            return res
        return self._ejecutar(account_id, _fn)

    def leer_collection(self, account_id, subreddit, collection_id) -> dict:
        def _fn(client):
            c = client.subreddit(subreddit).collections(collection_id)
            posts = []
            for s in (_g(c, "sorted_links") or []):
                posts.append({"fullname": _g(s, "fullname") or "",
                              "title": _g(s, "title") or "",
                              "author": _nombre_autor(s)})
            return {"id": collection_id, "title": _g(c, "title") or "",
                    "description": _g(c, "description") or "", "posts": posts}
        return self._ejecutar(account_id, _fn)

    def crear_collection(self, account_id, subreddit, title, description="") -> str:
        def _fn(client):
            col = client.subreddit(subreddit).collections.mod.create(
                title=title, description=description or "")
            return _g(col, "collection_id") or _g(col, "id") or ""
        return self._ejecutar(account_id, _fn)

    def añadir_post_collection(self, account_id, subreddit, collection_id, fullname) -> bool:
        def _fn(client):
            col = client.subreddit(subreddit).collections(collection_id)
            col.mod.add_post(self._objeto_por_fullname(client, fullname))
            return True
        return self._ejecutar(account_id, _fn)

    def borrar_collection(self, account_id, subreddit, collection_id) -> bool:
        def _fn(client):
            client.subreddit(subreddit).collections(collection_id).mod.delete()
            return True
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------ borradores
    def listar_drafts(self, account_id) -> list:
        def _fn(client):
            res = []
            for d in client.drafts:
                sub = _g(d, "subreddit")
                url = _g(d, "url") or ""
                res.append({"id": _g(d, "id") or "",
                            "title": _g(d, "title") or "",
                            "subreddit": str(_g(sub, "display_name") or sub or ""),
                            "selftext": _g(d, "selftext") or "",
                            "url": url, "is_link": bool(url)})
            return res
        return self._ejecutar(account_id, _fn)

    def crear_draft(self, account_id, subreddit, title, selftext="", url="") -> str:
        def _fn(client):
            kwargs = {"title": title, "subreddit": subreddit or None}
            if url:
                kwargs["url"] = url
            else:
                kwargs["selftext"] = selftext
            d = client.drafts.create(**kwargs)
            return _g(d, "id") or ""
        return self._ejecutar(account_id, _fn)

    def enviar_draft(self, account_id, draft_id) -> str:
        def _fn(client):
            sub = client.drafts(draft_id).submit()
            return _g(sub, "fullname") or ""
        return self._ejecutar(account_id, _fn)

    def borrar_draft(self, account_id, draft_id) -> bool:
        def _fn(client):
            client.drafts(draft_id).delete()
            return True
        return self._ejecutar(account_id, _fn)
