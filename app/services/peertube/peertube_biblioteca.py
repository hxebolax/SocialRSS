# -*- coding: utf-8 -*-
"""Biblioteca y creación: canales, listas de reproducción, "ver más tarde" y edición (Fase 5).

Mixin de `PeerTubeService`. Cubre lo que el usuario POSEE o gestiona: sus canales, sus listas de
reproducción (crear/editar/borrar y añadir/quitar vídeos), la lista especial "ver más tarde", y la
edición/borrado de sus propios vídeos. La subida, la importación y las descargas van en sus propios
mixins.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)

# Tipo de lista de reproducción de PeerTube: 1 = normal, 2 = "ver más tarde".
_TIPO_WATCH_LATER = 2


class _BibliotecaMixin:
    # ------------------------------------------------------------------
    # Mis canales
    # ------------------------------------------------------------------
    def mis_canales(self, account_id: int) -> List[Dict[str, Any]]:
        """Canales del usuario (de `GET /users/me`)."""
        def _fn(cliente):
            me = cliente.get("users/me", auth=True)
            res = []
            for c in (_g(me, "videoChannels") or []):
                res.append({"id": _g(c, "id"), "handle": self._handle_de(c),
                            "display": _g(c, "displayName") or _g(c, "name") or "",
                            "followers": int(_g(c, "followersCount", default=0) or 0),
                            "description": _g(c, "description") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    @staticmethod
    def _handle_de(actor: dict) -> str:
        nombre = _g(actor, "name") or ""
        host = _g(actor, "host") or ""
        return f"{nombre}@{host}" if host else nombre

    def crear_canal(self, account_id: int, name: str, display_name: str, *, description: str = "",
                    support: str = "") -> Any:
        """Crea un canal. `name` es el identificador (sin espacios); `display_name` el visible."""
        def _fn(cliente):
            cuerpo = {"name": name, "displayName": display_name}
            if description:
                cuerpo["description"] = description
            if support:
                cuerpo["support"] = support
            datos = cliente.post("video-channels", json_body=cuerpo, auth=True)
            return _g(_g(datos, "videoChannel") or {}, "id")
        return self._ejecutar(account_id, _fn)

    def editar_canal(self, account_id: int, handle: str, **campos) -> bool:
        """Edita un canal (displayName, description, support...)."""
        cuerpo = {k: v for k, v in campos.items() if v is not None}

        def _fn(cliente):
            cliente.put(f"video-channels/{handle}", json_body=cuerpo, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def borrar_canal(self, account_id: int, handle: str) -> bool:
        def _fn(cliente):
            cliente.delete(f"video-channels/{handle}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Mis listas de reproducción
    # ------------------------------------------------------------------
    def mis_listas(self, account_id: int, *, limite: int = 50) -> List[Dict[str, Any]]:
        """Listas de reproducción del usuario.

        PeerTube no tiene `users/me/video-playlists` (esa ruta solo existe con el sufijo
        `/videos-exist`, y sin él la API responde 400 "Solicitud incorrecta"). Las listas propias
        se piden por la cuenta: `accounts/{nombre}/video-playlists`."""
        nombre = self.nombre_usuario(account_id)
        if not nombre:
            return []

        def _fn(cliente):
            data, _total = cliente.pagina(f"accounts/{nombre}/video-playlists", start=0,
                                          count=limite, auth=True)
            res = []
            for p in (data or []):
                tipo = _g(_g(p, "type") or {}, "id")
                res.append({"id": _g(p, "id"), "uuid": _g(p, "uuid") or "",
                            "display": _g(p, "displayName") or "",
                            "num_videos": int(_g(p, "videosLength", default=0) or 0),
                            "es_ver_mas_tarde": (tipo == _TIPO_WATCH_LATER),
                            "descripcion": _g(p, "description") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def crear_lista(self, account_id: int, display_name: str, *, privacy: int = 3,
                    channel_id: Optional[int] = None, description: str = "") -> Any:
        """Crea una lista de reproducción. `privacy`: 1 público, 2 no listado, 3 privado."""
        def _fn(cliente):
            cuerpo = {"displayName": display_name, "privacy": privacy}
            if channel_id:
                cuerpo["videoChannelId"] = channel_id
            if description:
                cuerpo["description"] = description
            datos = cliente.post("video-playlists", json_body=cuerpo, auth=True)
            return _g(_g(datos, "videoPlaylist") or {}, "id")
        return self._ejecutar(account_id, _fn)

    def editar_lista(self, account_id: int, playlist_id: Any, **campos) -> bool:
        cuerpo = {k: v for k, v in campos.items() if v is not None}

        def _fn(cliente):
            cliente.put(f"video-playlists/{playlist_id}", json_body=cuerpo, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def borrar_lista(self, account_id: int, playlist_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"video-playlists/{playlist_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def añadir_a_lista(self, account_id: int, playlist_id: Any, video_id: Any) -> Any:
        """Añade un vídeo a una lista. Devuelve el id del elemento creado."""
        def _fn(cliente):
            datos = cliente.post(f"video-playlists/{playlist_id}/videos",
                                 json_body={"videoId": video_id}, auth=True)
            return _g(_g(datos, "videoPlaylistElement") or {}, "id")
        return self._ejecutar(account_id, _fn)

    def quitar_de_lista(self, account_id: int, playlist_id: Any, element_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"video-playlists/{playlist_id}/videos/{element_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Ver más tarde (lista especial)
    # ------------------------------------------------------------------
    def id_ver_mas_tarde(self, account_id: int) -> Optional[Any]:
        """Devuelve (y cachea) el id de la lista especial 'ver más tarde' del usuario, o None."""
        cache = getattr(self, "_watch_later", None)
        if cache is None:
            cache = {}
            self._watch_later = cache
        if account_id in cache:
            return cache[account_id]
        for lista in self.mis_listas(account_id):
            if lista.get("es_ver_mas_tarde"):
                cache[account_id] = lista["id"]
                return lista["id"]
        return None

    def añadir_ver_mas_tarde(self, account_id: int, video_id: Any) -> bool:
        """Añade un vídeo a 'ver más tarde'."""
        pid = self.id_ver_mas_tarde(account_id)
        if not pid:
            return False
        self.añadir_a_lista(account_id, pid, video_id)
        return True

    # ------------------------------------------------------------------
    # Edición / borrado de mis vídeos
    # ------------------------------------------------------------------
    def editar_video(self, account_id: int, video_id: Any, **campos) -> bool:
        """Edita los metadatos de un vídeo propio (name, description, category, licence, language,
        privacy, nsfw, tags, commentsEnabled, downloadEnabled...). Solo se envían los campos dados."""
        cuerpo = {k: v for k, v in campos.items() if v is not None}

        def _fn(cliente):
            cliente.put(f"videos/{video_id}", data=cuerpo, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def borrar_video(self, account_id: int, video_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"videos/{video_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))
