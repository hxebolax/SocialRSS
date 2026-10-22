# -*- coding: utf-8 -*-
"""Bandeja de entrada: mensajes privados, respuestas y menciones.

Mixin de RedditService. Descarga los distintos buffers de la bandeja y los cachea en
reddit_messages (el buffer se deriva de la columna msg_type). Permite enviar y responder mensajes
privados y marcar leído.
"""
from app.services.reddit.reddit_common import (
    db, task_manager, config, log, RedditServiceError, _g, _nombre_autor, _ahora_iso)


class _InboxMixin:
    def cargar_inbox_async(self, account_id: int, tipo: str = "inbox", limit: int = None,
                           callback=None):
        """tipo: inbox, unread, mentions, sent. Descarga el listing y lo cachea."""
        lim = int(limit or config.get("reddit", "limit_per_load", 25))

        def _bg(ctx):
            return self._cargar_inbox_sync(account_id, tipo, lim)

        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"reddit:{account_id}:inbox:{tipo}")

    def _cargar_inbox_sync(self, account_id, tipo, limit) -> int:
        client = self.obtener_cliente(account_id)
        try:
            if tipo == "mentions":
                listing = client.inbox.mentions(limit=limit)
            elif tipo == "sent":
                listing = client.inbox.sent(limit=limit)
            else:  # inbox, unread -> all() sincroniza el estado de leído/no leído
                listing = client.inbox.all(limit=limit)
            mensajes = [self._mensaje_a_valores(it, account_id, tipo) for it in listing]
        except Exception as e:
            self._marcar_revocada_si_token(account_id, e)
            ra = self._es_limite_429(e)
            if ra is not None:
                self.registrar_limite_429(account_id, ra)
            raise RedditServiceError(self._mensaje_error(e))
        return self._persistir_mensajes(account_id, mensajes)

    def _persistir_mensajes(self, account_id, mensajes) -> int:
        if not mensajes:
            return 0
        nuevos = 0
        with db.transaccion():
            for v in mensajes:
                existe = db.query_one("SELECT 1 FROM reddit_messages WHERE id = ?", (v["id"],))
                db.execute("""
                    INSERT INTO reddit_messages (
                        id, account_id, fullname, kind, msg_type, author, dest, subject, body,
                        context, subreddit, created_utc, new, parent_id, fetched_at)
                    VALUES (:id,:account_id,:fullname,:kind,:msg_type,:author,:dest,:subject,:body,
                        :context,:subreddit,:created_utc,:new,:parent_id,:fetched_at)
                    ON CONFLICT(id) DO UPDATE SET
                        new=excluded.new, body=excluded.body, fetched_at=excluded.fetched_at
                """, v)
                if not existe:
                    nuevos += 1
        return nuevos

    def _mensaje_a_valores(self, item, account_id, tipo_buffer) -> dict:
        fullname = _g(item, "fullname") or ""
        es_message = (type(item).__name__ == "Message")
        kind = "t4" if es_message else "t1"
        if tipo_buffer == "sent":
            msg_type = "sent"
        elif es_message:
            msg_type = "message"
        else:
            msg_type = _g(item, "type") or "comment_reply"
        dest = ""
        d = _g(item, "dest")
        if d is not None:
            dest = getattr(d, "name", None) or str(d)
        subject = _g(item, "subject") or _g(item, "link_title") or ""
        return {
            "id": f"{account_id}:{fullname}",
            "account_id": account_id,
            "fullname": fullname,
            "kind": kind,
            "msg_type": msg_type,
            "author": _nombre_autor(item),
            "dest": dest,
            "subject": subject,
            "body": _g(item, "body") or "",
            "context": _g(item, "context") or "",
            "subreddit": str(_g(item, "subreddit") or ""),
            "created_utc": float(_g(item, "created_utc") or 0),
            "new": 1 if _g(item, "new") else 0,
            "parent_id": _g(item, "parent_id") or "",
            "fetched_at": _ahora_iso(),
        }

    # ------------------------------------------------------------------
    # Acciones de mensajería
    # ------------------------------------------------------------------
    def enviar_pm(self, account_id, destinatario, asunto, cuerpo) -> bool:
        destinatario = (destinatario or "").lstrip("u/").lstrip("/").strip()
        if not destinatario or not cuerpo:
            raise RedditServiceError("Faltan el destinatario o el cuerpo del mensaje.")

        def _fn(client):
            client.redditor(destinatario).message(subject=asunto or "", message=cuerpo)
            return True
        return self._ejecutar(account_id, _fn)

    def responder_mensaje(self, account_id, fullname, cuerpo) -> bool:
        base = fullname.split("_", 1)[1] if "_" in fullname else fullname

        def _fn(client):
            item = client.inbox.message(base) if fullname.startswith("t4_") else client.comment(id=base)
            item.reply(cuerpo)
            return True
        return self._ejecutar(account_id, _fn)

    def marcar_leido(self, account_id, fullname, leido=True) -> bool:
        base = fullname.split("_", 1)[1] if "_" in fullname else fullname

        def _fn(client):
            item = client.inbox.message(base) if fullname.startswith("t4_") else client.comment(id=base)
            item.mark_read() if leido else item.mark_unread()
            return True
        self._ejecutar(account_id, _fn)
        db.execute("UPDATE reddit_messages SET new = ? WHERE id = ?",
                   (0 if leido else 1, f"{account_id}:{fullname}"))
        return True

    def marcar_todo_leido(self, account_id) -> bool:
        def _fn(client):
            client.inbox.mark_all_read()
            return True
        self._ejecutar(account_id, _fn)
        db.execute("UPDATE reddit_messages SET new = 0 WHERE account_id = ?", (account_id,))
        return True
