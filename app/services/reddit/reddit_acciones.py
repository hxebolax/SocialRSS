# -*- coding: utf-8 -*-
"""Acciones sobre publicaciones y comentarios: votar, guardar, ocultar, responder, editar,
borrar, NSFW/spoiler y flair.

Mixin de RedditService. Cada acción hace la RED (PRAW) y luego refleja el nuevo estado en la caché
local (fuera de transacción de red). El fullname lleva el prefijo del tipo: t3_ (publicación) o
t1_ (comentario).
"""
from app.services.reddit.reddit_common import (
    db, config, log, RedditServiceError, _g)


class _AccionesServMixin:
    def _objeto_por_fullname(self, client, fullname):
        """Devuelve el objeto PRAW (Submission o Comment) a partir de su fullname."""
        base = fullname.split("_", 1)[1] if "_" in fullname else fullname
        if fullname.startswith("t1_"):
            return client.comment(id=base)
        return client.submission(id=base)

    def _tabla_de(self, fullname):
        return "reddit_comments" if fullname.startswith("t1_") else "reddit_posts"

    def _ejecutar(self, account_id, fn):
        """Envuelve una operación PRAW clasificando errores y límites."""
        try:
            return fn(self.obtener_cliente(account_id))
        except RedditServiceError:
            raise
        except Exception as e:
            self._marcar_revocada_si_token(account_id, e)
            ra = self._es_limite_429(e)
            if ra is not None:
                self.registrar_limite_429(account_id, ra)
            raise RedditServiceError(self._mensaje_error(e))

    # ------------------------------------------------------------------
    # Votar
    # ------------------------------------------------------------------
    def votar(self, account_id: int, fullname: str, direccion: str) -> bool:
        """direccion: 'up', 'down' o 'clear'. Actualiza el voto local."""
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            if direccion == "up":
                obj.upvote()
            elif direccion == "down":
                obj.downvote()
            else:
                obj.clear_vote()
            return True
        self._ejecutar(account_id, _fn)
        likes = 1 if direccion == "up" else (0 if direccion == "down" else None)
        db.execute(f"UPDATE {self._tabla_de(fullname)} SET likes = ? WHERE id = ?",
                   (likes, f"{account_id}:{fullname}"))
        return True

    # ------------------------------------------------------------------
    # Guardar / ocultar
    # ------------------------------------------------------------------
    def guardar(self, account_id: int, fullname: str, guardar: bool) -> bool:
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            obj.save() if guardar else obj.unsave()
            return True
        self._ejecutar(account_id, _fn)
        db.execute(f"UPDATE {self._tabla_de(fullname)} SET saved = ? WHERE id = ?",
                   (1 if guardar else 0, f"{account_id}:{fullname}"))
        return True

    def ocultar(self, account_id: int, fullname: str, ocultar: bool) -> bool:
        """Ocultar/mostrar (solo publicaciones)."""
        if not fullname.startswith("t3_"):
            raise RedditServiceError("Solo las publicaciones se pueden ocultar.")

        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            obj.hide() if ocultar else obj.unhide()
            return True
        self._ejecutar(account_id, _fn)
        db.execute("UPDATE reddit_posts SET hidden = ? WHERE id = ?",
                   (1 if ocultar else 0, f"{account_id}:{fullname}"))
        return True

    # ------------------------------------------------------------------
    # Responder / editar / borrar
    # ------------------------------------------------------------------
    def responder(self, account_id: int, fullname: str, texto: str) -> str:
        """Responde a una publicación o comentario. Devuelve el fullname del nuevo comentario."""
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            nuevo = obj.reply(texto)
            return _g(nuevo, "fullname") or ""
        return self._ejecutar(account_id, _fn)

    def editar(self, account_id: int, fullname: str, texto: str) -> bool:
        """Edita el cuerpo de una publicación (self) o comentario propio."""
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            obj.edit(texto)
            return True
        self._ejecutar(account_id, _fn)
        campo = "body" if fullname.startswith("t1_") else "selftext"
        db.execute(f"UPDATE {self._tabla_de(fullname)} SET {campo} = ?, edited = 1 WHERE id = ?",
                   (texto, f"{account_id}:{fullname}"))
        return True

    def borrar(self, account_id: int, fullname: str) -> bool:
        """Borra una publicación o comentario propio. Elimina también la copia local (política de
        Reddit: no conservar contenido borrado)."""
        def _fn(client):
            self._objeto_por_fullname(client, fullname).delete()
            return True
        self._ejecutar(account_id, _fn)
        db.execute(f"DELETE FROM {self._tabla_de(fullname)} WHERE id = ?",
                   (f"{account_id}:{fullname}",))
        return True

    # ------------------------------------------------------------------
    # NSFW / spoiler (publicaciones)
    # ------------------------------------------------------------------
    def marcar_nsfw(self, account_id: int, fullname: str, nsfw: bool) -> bool:
        if not fullname.startswith("t3_"):
            raise RedditServiceError("Solo las publicaciones admiten la marca +18.")

        def _fn(client):
            sub = self._objeto_por_fullname(client, fullname)
            sub.mod.nsfw() if nsfw else sub.mod.sfw()
            return True
        self._ejecutar(account_id, _fn)
        db.execute("UPDATE reddit_posts SET over_18 = ? WHERE id = ?",
                   (1 if nsfw else 0, f"{account_id}:{fullname}"))
        return True

    def marcar_spoiler(self, account_id: int, fullname: str, spoiler: bool) -> bool:
        if not fullname.startswith("t3_"):
            raise RedditServiceError("Solo las publicaciones admiten la marca de spoiler.")

        def _fn(client):
            sub = self._objeto_por_fullname(client, fullname)
            sub.spoiler() if spoiler else sub.unspoiler()
            return True
        self._ejecutar(account_id, _fn)
        db.execute("UPDATE reddit_posts SET spoiler = ? WHERE id = ?",
                   (1 if spoiler else 0, f"{account_id}:{fullname}"))
        return True

    # ------------------------------------------------------------------
    # Flair de la publicación (como autor)
    # ------------------------------------------------------------------
    def opciones_flair(self, account_id: int, fullname: str) -> list:
        """Devuelve las opciones de flair disponibles para la publicación: [{id, text}]."""
        def _fn(client):
            sub = self._objeto_por_fullname(client, fullname)
            opciones = []
            for ch in sub.flair.choices():
                opciones.append({"id": ch.get("flair_template_id", ""),
                                 "text": ch.get("flair_text", "") or ch.get("flair_css_class", "")})
            return opciones
        return self._ejecutar(account_id, _fn)

    def aplicar_flair(self, account_id: int, fullname: str, template_id: str, texto: str = None) -> bool:
        def _fn(client):
            sub = self._objeto_por_fullname(client, fullname)
            sub.flair.select(template_id, text=texto)
            return True
        self._ejecutar(account_id, _fn)
        if texto is not None:
            db.execute("UPDATE reddit_posts SET link_flair_text = ? WHERE id = ?",
                       (texto, f"{account_id}:{fullname}"))
        return True
