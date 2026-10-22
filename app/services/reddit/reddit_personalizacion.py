# -*- coding: utf-8 -*-
"""Personalización de comunidades (Fase 3 de Reddit): plantillas de etiqueta (flair), etiqueta de
usuario, emojis personalizados y lectura de los widgets de la barra lateral.

Mixin de RedditService. La red va por self._ejecutar. Gestionar plantillas/emojis exige ser
moderador (el servicio traduce el error de permisos); elegir la propia etiqueta o leer los widgets no.
"""
from app.services.reddit.reddit_common import _g


def _plantilla_flair_a_dict(t) -> dict:
    """Normaliza una plantilla de flair (dict de la API o atributos) a un dict estable."""
    get = (lambda k: t.get(k)) if isinstance(t, dict) else (lambda k: _g(t, k))
    return {
        "id": get("id") or "",
        "text": get("text") or "",
        "css_class": get("css_class") or "",
        "mod_only": bool(get("mod_only") or False),
        "text_editable": bool(get("text_editable") or False),
        "background_color": get("background_color") or "",
        "text_color": get("text_color") or "",
    }


class _PersonalizacionMixin:
    # ------------------------------------------------------------------ plantillas de flair
    def plantillas_flair_usuario(self, account_id, subreddit) -> list:
        """Plantillas de etiqueta de usuario del subreddit."""
        def _fn(client):
            return [_plantilla_flair_a_dict(t) for t in client.subreddit(subreddit).flair.templates]
        return self._ejecutar(account_id, _fn)

    def plantillas_flair_enlace(self, account_id, subreddit) -> list:
        """Plantillas de etiqueta de publicación (post flair) del subreddit."""
        def _fn(client):
            return [_plantilla_flair_a_dict(t) for t in client.subreddit(subreddit).flair.link_templates]
        return self._ejecutar(account_id, _fn)

    def crear_plantilla_flair(self, account_id, subreddit, text, *, tipo="user", mod_only=False,
                              text_editable=False, background_color=None, text_color=None) -> bool:
        def _fn(client):
            flair = client.subreddit(subreddit).flair
            destino = flair.link_templates if tipo == "link" else flair.templates
            destino.add(text, mod_only=mod_only, text_editable=text_editable,
                        background_color=background_color, text_color=text_color)
            return True
        return self._ejecutar(account_id, _fn)

    def borrar_plantilla_flair(self, account_id, subreddit, template_id) -> bool:
        def _fn(client):
            client.subreddit(subreddit).flair.templates.delete(template_id)
            return True
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------ etiqueta de usuario
    def opciones_flair_usuario(self, account_id, subreddit) -> list:
        """Opciones de etiqueta que el usuario actual puede elegir para sí mismo."""
        def _fn(client):
            res = []
            for c in client.subreddit(subreddit).flair.choices():
                res.append({"id": c.get("flair_template_id", ""),
                            "text": c.get("flair_text", ""),
                            "editable": bool(c.get("flair_text_editable", False))})
            return res
        return self._ejecutar(account_id, _fn)

    def establecer_mi_flair(self, account_id, subreddit, *, text=None, template_id=None) -> bool:
        def _fn(client):
            client.subreddit(subreddit).flair.set(client.user.me(), text=text or "",
                                                  flair_template_id=template_id)
            return True
        return self._ejecutar(account_id, _fn)

    def asignar_flair_usuario(self, account_id, subreddit, usuario, *, text=None,
                              template_id=None) -> bool:
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            client.subreddit(subreddit).flair.set(usuario, text=text or "",
                                                  flair_template_id=template_id)
            return True
        return self._ejecutar(account_id, _fn)

    def quitar_flair_usuario(self, account_id, subreddit, usuario) -> bool:
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            client.subreddit(subreddit).flair.delete(usuario)
            return True
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------ emojis
    def listar_emojis(self, account_id, subreddit) -> list:
        def _fn(client):
            res = []
            for e in client.subreddit(subreddit).emoji:
                res.append({"name": str(_g(e, "name") or e),
                            "url": _g(e, "url") or "",
                            "mod_flair_only": bool(_g(e, "mod_flair_only", default=False)),
                            "post_flair_allowed": bool(_g(e, "post_flair_allowed", default=True)),
                            "user_flair_allowed": bool(_g(e, "user_flair_allowed", default=True))})
            return res
        return self._ejecutar(account_id, _fn)

    def añadir_emoji(self, account_id, subreddit, name, image_path) -> bool:
        def _fn(client):
            client.subreddit(subreddit).emoji.add(name, image_path)
            return True
        return self._ejecutar(account_id, _fn)

    def borrar_emoji(self, account_id, subreddit, name) -> bool:
        def _fn(client):
            client.subreddit(subreddit).emoji[name].delete()
            return True
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------ widgets (lectura)
    def listar_widgets_sidebar(self, account_id, subreddit) -> list:
        """Widgets de la barra lateral (solo lectura): tipo, nombre corto y texto si lo tiene."""
        def _fn(client):
            res = []
            for w in client.subreddit(subreddit).widgets.sidebar:
                res.append({"kind": str(_g(w, "kind") or ""),
                            "shortName": _g(w, "shortName", "short_name") or "",
                            "text": _g(w, "text") or _g(w, "description") or ""})
            return res
        return self._ejecutar(account_id, _fn)
