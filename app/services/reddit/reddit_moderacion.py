# -*- coding: utf-8 -*-
"""Moderación de subreddits: colas, acciones por elemento, relaciones de usuario (baneados,
silenciados, contribuidores, moderadores), motivos de eliminación y configuración del subreddit.

Mixin de RedditService. Todas las operaciones hacen la RED (PRAW) a través de self._ejecutar, que
clasifica errores y límites. Los listados devuelven listas de dicts para la interfaz.
"""
from app.services.reddit.reddit_common import (
    log, RedditServiceError, _g, _nombre_autor)


class _ModeracionMixin:
    _COLAS = ("modqueue", "spam", "reports", "unmoderated", "edited")

    def subreddits_que_modero(self, account_id) -> list:
        """Lista de nombres de subreddits que el usuario modera."""
        def _fn(client):
            return sorted(str(_g(s, "display_name") or "") for s in client.user.moderator_subreddits(limit=None))
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Colas de moderación
    # ------------------------------------------------------------------
    def cargar_cola(self, account_id, subreddit, cola="modqueue", limit=50) -> list:
        if cola not in self._COLAS:
            raise RedditServiceError(f"Cola de moderación no reconocida: {cola}")

        def _fn(client):
            sub = client.subreddit(subreddit)
            gen = getattr(sub.mod, cola)(limit=limit)
            return [self._item_mod_a_dict(it) for it in gen]
        return self._ejecutar(account_id, _fn)

    def modlog(self, account_id, subreddit, limit=100) -> list:
        """Registro de acciones de moderación (ModAction)."""
        def _fn(client):
            res = []
            for a in client.subreddit(subreddit).mod.log(limit=limit):
                res.append({
                    "mod": str(_g(a, "mod") or ""),
                    "action": _g(a, "action") or "",
                    "target": _g(a, "target_fullname") or "",
                    "details": _g(a, "details") or "",
                    "description": _g(a, "description") or "",
                    "created_utc": float(_g(a, "created_utc") or 0),
                })
            return res
        return self._ejecutar(account_id, _fn)

    @staticmethod
    def _item_mod_a_dict(it) -> dict:
        es_comment = (type(it).__name__ == "Comment")
        return {
            "fullname": _g(it, "fullname") or "",
            "tipo": "comment" if es_comment else "post",
            "author": _nombre_autor(it),
            "subreddit": str(_g(it, "subreddit") or ""),
            "titulo": "" if es_comment else (_g(it, "title") or ""),
            "cuerpo": (_g(it, "body") or "") if es_comment else (_g(it, "selftext") or ""),
            "permalink": _g(it, "permalink") or "",
            "num_reports": int(_g(it, "num_reports") or 0),
            "created_utc": float(_g(it, "created_utc") or 0),
        }

    # ------------------------------------------------------------------
    # Acciones por elemento (fullname t1_/t3_)
    # ------------------------------------------------------------------
    def mod_aprobar(self, account_id, fullname):
        return self._ejecutar(account_id, lambda c: (self._objeto_por_fullname(c, fullname).mod.approve(), True)[1])

    def mod_eliminar(self, account_id, fullname, spam=False, reason_id=None):
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            obj.mod.remove(spam=spam, reason_id=reason_id)
            return True
        return self._ejecutar(account_id, _fn)

    def mod_bloquear(self, account_id, fullname, bloquear=True):
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            obj.mod.lock() if bloquear else obj.mod.unlock()
            return True
        return self._ejecutar(account_id, _fn)

    def mod_distinguir(self, account_id, fullname, how="yes", sticky=False):
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            if how == "no":
                obj.mod.undistinguish()
            else:
                obj.mod.distinguish(how=how, sticky=sticky)
            return True
        return self._ejecutar(account_id, _fn)

    def mod_ignorar_reportes(self, account_id, fullname, ignorar=True):
        def _fn(client):
            obj = self._objeto_por_fullname(client, fullname)
            obj.mod.ignore_reports() if ignorar else obj.mod.unignore_reports()
            return True
        return self._ejecutar(account_id, _fn)

    def mod_mensaje_eliminacion(self, account_id, fullname, mensaje, tipo="private"):
        def _fn(client):
            self._objeto_por_fullname(client, fullname).mod.send_removal_message(
                message=mensaje, type=tipo)
            return True
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Relaciones de usuario en el subreddit
    # ------------------------------------------------------------------
    def _listar_relacion(self, account_id, subreddit, relacion):
        def _fn(client):
            sub = client.subreddit(subreddit)
            rel = getattr(sub, relacion)
            res = []
            for r in rel(limit=None):
                res.append({"name": str(_g(r, "name") or ""),
                            "note": _g(r, "note") or "",
                            "date": float(_g(r, "date") or 0)})
            return res
        return self._ejecutar(account_id, _fn)

    def listar_baneados(self, account_id, subreddit):
        return self._listar_relacion(account_id, subreddit, "banned")

    def listar_silenciados(self, account_id, subreddit):
        return self._listar_relacion(account_id, subreddit, "muted")

    def listar_contribuidores(self, account_id, subreddit):
        return self._listar_relacion(account_id, subreddit, "contributor")

    def listar_moderadores(self, account_id, subreddit):
        def _fn(client):
            res = []
            for m in client.subreddit(subreddit).moderator():
                res.append({"name": str(_g(m, "name") or ""),
                            "permissions": list(_g(m, "mod_permissions") or [])})
            return res
        return self._ejecutar(account_id, _fn)

    def banear(self, account_id, subreddit, usuario, *, motivo="", mensaje="", duracion=None, nota=""):
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            kwargs = {}
            if motivo:
                kwargs["ban_reason"] = motivo
            if mensaje:
                kwargs["ban_message"] = mensaje
            if duracion:
                kwargs["duration"] = int(duracion)
            if nota:
                kwargs["note"] = nota
            client.subreddit(subreddit).banned.add(usuario, **kwargs)
            return True
        return self._ejecutar(account_id, _fn)

    def quitar_ban(self, account_id, subreddit, usuario):
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()
        return self._ejecutar(account_id, lambda c: (c.subreddit(subreddit).banned.remove(usuario), True)[1])

    def silenciar(self, account_id, subreddit, usuario, quitar=False):
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            rel = client.subreddit(subreddit).muted
            rel.remove(usuario) if quitar else rel.add(usuario)
            return True
        return self._ejecutar(account_id, _fn)

    def contribuidor(self, account_id, subreddit, usuario, quitar=False):
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            rel = client.subreddit(subreddit).contributor
            rel.remove(usuario) if quitar else rel.add(usuario)
            return True
        return self._ejecutar(account_id, _fn)

    def invitar_moderador(self, account_id, subreddit, usuario, permisos=None):
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()

        def _fn(client):
            client.subreddit(subreddit).moderator.invite(usuario, permissions=permisos)
            return True
        return self._ejecutar(account_id, _fn)

    def quitar_moderador(self, account_id, subreddit, usuario):
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()
        return self._ejecutar(account_id, lambda c: (c.subreddit(subreddit).moderator.remove(usuario), True)[1])

    def actualizar_permisos_moderador(self, account_id, subreddit, usuario, permisos):
        usuario = (usuario or "").lstrip("u/").lstrip("/").strip()
        return self._ejecutar(account_id,
                              lambda c: (c.subreddit(subreddit).moderator.update(usuario, permissions=permisos), True)[1])

    # ------------------------------------------------------------------
    # Motivos de eliminación (removal reasons)
    # ------------------------------------------------------------------
    def listar_motivos(self, account_id, subreddit) -> list:
        def _fn(client):
            res = []
            for r in client.subreddit(subreddit).mod.removal_reasons:
                res.append({"id": _g(r, "id") or "", "title": _g(r, "title") or "",
                            "message": _g(r, "message") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def añadir_motivo(self, account_id, subreddit, titulo, mensaje):
        return self._ejecutar(account_id,
                              lambda c: (c.subreddit(subreddit).mod.removal_reasons.add(
                                  title=titulo, message=mensaje), True)[1])

    def borrar_motivo(self, account_id, subreddit, reason_id):
        def _fn(client):
            for r in client.subreddit(subreddit).mod.removal_reasons:
                if (_g(r, "id") or "") == reason_id:
                    r.delete()
                    return True
            return False
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Configuración del subreddit
    # ------------------------------------------------------------------
    def obtener_config_subreddit(self, account_id, subreddit) -> dict:
        def _fn(client):
            s = client.subreddit(subreddit).mod.settings()
            return dict(s) if s else {}
        return self._ejecutar(account_id, _fn)

    def actualizar_config_subreddit(self, account_id, subreddit, **settings):
        def _fn(client):
            client.subreddit(subreddit).mod.update(**settings)
            return True
        return self._ejecutar(account_id, _fn)
