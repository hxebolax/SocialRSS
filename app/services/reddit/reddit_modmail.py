# -*- coding: utf-8 -*-
"""Modmail: la mensajería de moderación de los subreddits (Fase 4 de Reddit).

Mixin de RedditService. Lista conversaciones (por subreddit o de todos los que moderas con "all"),
lee una conversación con sus mensajes, responde (respuesta normal o nota interna), archiva y crea
conversaciones nuevas con un usuario. La red va por self._ejecutar.
"""
from datetime import datetime

from app.services.reddit.reddit_common import _g

# Estados de la bandeja de modmail admitidos por la API.
ESTADOS_MODMAIL = ("all", "new", "inprogress", "archived", "appeals", "mod", "notifications",
                   "join_requests")


def _iso_a_epoch(valor) -> float:
    """Convierte la fecha ISO-8601 del modmail a epoch (0 si no se puede)."""
    if not valor:
        return 0.0
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


class _ModmailMixin:
    def listar_conversaciones_modmail(self, account_id, subreddit="all", estado="all",
                                      limit=25) -> list:
        if estado not in ESTADOS_MODMAIL:
            estado = "all"

        def _fn(client):
            res = []
            for c in client.subreddit(subreddit).modmail.conversations(state=estado, limit=limit):
                part = _g(c, "participant")
                res.append({
                    "id": _g(c, "id") or "",
                    "subject": _g(c, "subject") or "",
                    "participant": str(_g(part, "name") or part or ""),
                    "subreddit": str(_g(c, "owner") or subreddit),
                    "num_messages": int(_g(c, "num_messages", default=0) or 0),
                    "state": _g(c, "state") or "",
                    "is_highlighted": bool(_g(c, "is_highlighted", default=False)),
                    "last_updated": _iso_a_epoch(_g(c, "last_updated")),
                })
            return res
        return self._ejecutar(account_id, _fn)

    def leer_conversacion_modmail(self, account_id, subreddit, conv_id) -> dict:
        def _fn(client):
            c = client.subreddit(subreddit).modmail(conv_id)
            mensajes = []
            for m in (_g(c, "messages") or []):
                autor = _g(m, "author")
                mensajes.append({
                    "author": str(_g(autor, "name") or autor or ""),
                    "body": _g(m, "body_markdown") or _g(m, "body") or "",
                    "date": _iso_a_epoch(_g(m, "date")),
                    "es_interno": bool(_g(m, "is_internal", default=False)),
                })
            part = _g(c, "participant")
            return {"id": _g(c, "id") or conv_id,
                    "subject": _g(c, "subject") or "",
                    "participant": str(_g(part, "name") or part or ""),
                    "messages": mensajes}
        return self._ejecutar(account_id, _fn)

    def responder_modmail(self, account_id, subreddit, conv_id, body, interno=False) -> bool:
        def _fn(client):
            client.subreddit(subreddit).modmail(conv_id).reply(body=body, internal=interno)
            return True
        return self._ejecutar(account_id, _fn)

    def archivar_modmail(self, account_id, subreddit, conv_id, archivar=True) -> bool:
        def _fn(client):
            c = client.subreddit(subreddit).modmail(conv_id)
            c.archive() if archivar else c.unarchive()
            return True
        return self._ejecutar(account_id, _fn)

    def crear_modmail(self, account_id, subreddit, asunto, cuerpo, destinatario) -> bool:
        destinatario = (destinatario or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            client.subreddit(subreddit).modmail.create(
                subject=asunto, body=cuerpo, recipient=destinatario)
            return True
        return self._ejecutar(account_id, _fn)
