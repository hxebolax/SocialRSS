# -*- coding: utf-8 -*-
"""Wiki de subreddits (Fase 3 de Reddit).

Lectura y edición de páginas wiki, creación de páginas nuevas y consulta del historial de revisiones.
Mixin de RedditService: la red va por self._ejecutar (clasifica errores y límites). Leer una wiki no
exige ser moderador (depende de los permisos de la comunidad); crear/editar sí suele exigirlo, y el
servicio traduce el error de permisos.
"""
from app.services.reddit.reddit_common import _g


def _nombre_redditor(r) -> str:
    """Nombre de un Redditor de PRAW (revision_by, author...); '' si no hay."""
    if not r:
        return ""
    return str(_g(r, "name") or r)


class _WikiMixin:
    def listar_paginas_wiki(self, account_id, subreddit) -> list:
        """Nombres de las páginas wiki de la comunidad (ordenados)."""
        def _fn(client):
            return sorted(str(_g(p, "name") or p) for p in client.subreddit(subreddit).wiki)
        return self._ejecutar(account_id, _fn)

    def leer_pagina_wiki(self, account_id, subreddit, page) -> dict:
        """Contenido y metadatos de una página wiki."""
        def _fn(client):
            wp = client.subreddit(subreddit).wiki[page]
            return {
                "name": _g(wp, "name") or page,
                "content_md": _g(wp, "content_md") or "",
                "revision_by": _nombre_redditor(_g(wp, "revision_by")),
                "revision_date": float(_g(wp, "revision_date") or 0),
                "may_revise": bool(_g(wp, "may_revise", default=False)),
            }
        return self._ejecutar(account_id, _fn)

    def crear_pagina_wiki(self, account_id, subreddit, page, content, reason="") -> bool:
        def _fn(client):
            client.subreddit(subreddit).wiki.create(name=page, content=content, reason=reason or None)
            return True
        return self._ejecutar(account_id, _fn)

    def editar_pagina_wiki(self, account_id, subreddit, page, content, reason="") -> bool:
        def _fn(client):
            client.subreddit(subreddit).wiki[page].edit(content=content, reason=reason or None)
            return True
        return self._ejecutar(account_id, _fn)

    def revisiones_wiki(self, account_id, subreddit, page, limit=20) -> list:
        """Historial de revisiones de una página: autor, motivo y fecha (epoch)."""
        def _fn(client):
            res = []
            for rev in client.subreddit(subreddit).wiki[page].revisions(limit=limit):
                autor = rev.get("author") if isinstance(rev, dict) else _g(rev, "author")
                res.append({
                    "author": str(_g(autor, "name") or autor or ""),
                    "reason": (rev.get("reason") if isinstance(rev, dict) else _g(rev, "reason")) or "",
                    "timestamp": float((rev.get("timestamp") if isinstance(rev, dict)
                                        else _g(rev, "timestamp")) or 0),
                })
            return res
        return self._ejecutar(account_id, _fn)
