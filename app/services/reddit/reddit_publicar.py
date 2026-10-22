# -*- coding: utf-8 -*-
"""Publicación: envío de todos los tipos de post (texto, enlace, imagen, vídeo, galería, encuesta),
crosspost y flairs del subreddit.

Mixin de RedditService. Cada envío hace la RED (PRAW) y devuelve {fullname, permalink, url} del
nuevo post. En PRAW 7.x los envíos con multimedia son métodos separados (submit_image/submit_video/
submit_gallery/submit_poll), equivalentes al submit() unificado de la 8.x.
"""
from app.services.reddit.reddit_common import (
    log, RedditServiceError, _g)


class _PublicarMixin:
    @staticmethod
    def _resultado(post) -> dict:
        return {"fullname": _g(post, "fullname") or "", "permalink": _g(post, "permalink") or "",
                "url": _g(post, "url") or "", "id": _g(post, "id") or ""}

    def publicar_texto(self, account_id, subreddit, title, selftext="", *, flair_id=None,
                       flair_text=None, nsfw=False, spoiler=False, send_replies=True):
        def _fn(client):
            sub = client.subreddit(subreddit)
            return self._resultado(sub.submit(
                title, selftext=selftext or "", flair_id=flair_id, flair_text=flair_text,
                nsfw=nsfw, spoiler=spoiler, send_replies=send_replies))
        return self._ejecutar(account_id, _fn)

    def publicar_enlace(self, account_id, subreddit, title, url, *, flair_id=None, flair_text=None,
                        nsfw=False, spoiler=False, send_replies=True, resubmit=True):
        if not url:
            raise RedditServiceError("Falta la dirección (URL) del enlace.")

        def _fn(client):
            sub = client.subreddit(subreddit)
            return self._resultado(sub.submit(
                title, url=url, flair_id=flair_id, flair_text=flair_text, nsfw=nsfw,
                spoiler=spoiler, send_replies=send_replies, resubmit=resubmit))
        return self._ejecutar(account_id, _fn)

    def publicar_imagen(self, account_id, subreddit, title, image_path, *, flair_id=None,
                        flair_text=None, nsfw=False, spoiler=False, send_replies=True):
        def _fn(client):
            sub = client.subreddit(subreddit)
            return self._resultado(sub.submit_image(
                title, image_path, flair_id=flair_id, flair_text=flair_text, nsfw=nsfw,
                spoiler=spoiler, send_replies=send_replies, without_websockets=True))
        return self._ejecutar(account_id, _fn)

    def publicar_video(self, account_id, subreddit, title, video_path, *, thumbnail_path="",
                       flair_id=None, flair_text=None, nsfw=False, spoiler=False, send_replies=True):
        def _fn(client):
            sub = client.subreddit(subreddit)
            kwargs = dict(flair_id=flair_id, flair_text=flair_text, nsfw=nsfw, spoiler=spoiler,
                          send_replies=send_replies, without_websockets=True)
            if thumbnail_path:
                kwargs["thumbnail_path"] = thumbnail_path
            return self._resultado(sub.submit_video(title, video_path, **kwargs))
        return self._ejecutar(account_id, _fn)

    def publicar_galeria(self, account_id, subreddit, title, imagenes, *, flair_id=None,
                         flair_text=None, nsfw=False, spoiler=False, send_replies=True):
        """`imagenes`: lista de rutas o de dicts {image_path, caption, outbound_url}."""
        images = []
        for it in (imagenes or []):
            if isinstance(it, dict):
                images.append(it)
            else:
                images.append({"image_path": it})
        if len(images) < 2:
            raise RedditServiceError("Una galería necesita al menos 2 imágenes.")

        def _fn(client):
            sub = client.subreddit(subreddit)
            return self._resultado(sub.submit_gallery(
                title, images, flair_id=flair_id, flair_text=flair_text, nsfw=nsfw,
                spoiler=spoiler, send_replies=send_replies))
        return self._ejecutar(account_id, _fn)

    def publicar_encuesta(self, account_id, subreddit, title, opciones, duracion_dias, *,
                          selftext="", flair_id=None, flair_text=None, nsfw=False, spoiler=False,
                          send_replies=True):
        opciones = [o for o in (opciones or []) if o and o.strip()]
        if not (2 <= len(opciones) <= 6):
            raise RedditServiceError("Una encuesta necesita entre 2 y 6 opciones.")
        if not (1 <= int(duracion_dias) <= 7):
            raise RedditServiceError("La duración de la encuesta debe ser de 1 a 7 días.")

        def _fn(client):
            sub = client.subreddit(subreddit)
            return self._resultado(sub.submit_poll(
                title, selftext=selftext or "", options=opciones, duration=int(duracion_dias),
                flair_id=flair_id, flair_text=flair_text, nsfw=nsfw, spoiler=spoiler,
                send_replies=send_replies))
        return self._ejecutar(account_id, _fn)

    def crosspost(self, account_id, subreddit, submission_fullname, title, *, nsfw=False,
                  spoiler=False, send_replies=True, flair_id=None, flair_text=None):
        base = (submission_fullname.split("_", 1)[1] if "_" in submission_fullname
                else submission_fullname)

        def _fn(client):
            original = client.submission(id=base)
            return self._resultado(original.crosspost(
                subreddit, title=title, nsfw=nsfw, spoiler=spoiler, send_replies=send_replies,
                flair_id=flair_id, flair_text=flair_text))
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Flairs disponibles en el subreddit (para elegir al publicar)
    # ------------------------------------------------------------------
    def flairs_de_subreddit(self, account_id, subreddit) -> list:
        """Devuelve las plantillas de flair para publicaciones del subreddit: [{id, text}]."""
        def _fn(client):
            sub = client.subreddit(subreddit)
            opciones = []
            try:
                for t in sub.flair.link_templates.user_selectable():
                    opciones.append({"id": t.get("flair_template_id", ""),
                                     "text": t.get("flair_text", "")})
            except Exception as e:
                log.debug(f"No se pudieron obtener los flairs de r/{subreddit}: {e}")
            return opciones
        return self._ejecutar(account_id, _fn)
