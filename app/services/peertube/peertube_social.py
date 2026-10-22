# -*- coding: utf-8 -*-
"""Interacción social: valoraciones, suscripciones, bloqueos/silencios y reportes (Fase 4 de PeerTube).

Mixin de `PeerTubeService`. La red va por `self._ejecutar`. Las valoraciones y suscripciones NO
devuelven contadores actualizados: la UI da retroalimentación inmediata (optimista) y revierte con un
aviso si la API falla.
"""
import logging
from typing import Any, Dict, List
from urllib.parse import urlsplit

from app.core.database import db

log = logging.getLogger(__name__)

# Valores válidos de valoración de PeerTube.
_RATINGS = ("like", "dislike", "none")


class _SocialMixin:
    # ------------------------------------------------------------------
    # Valoraciones (me gusta / no me gusta)
    # ------------------------------------------------------------------
    def votar(self, account_id: int, id_or_uuid: str, valoracion: str) -> bool:
        """Valora un vídeo: 'like', 'dislike' o 'none' (quitar). Actualiza el contador cacheado de
        forma optimista (la API no lo devuelve)."""
        if valoracion not in _RATINGS:
            valoracion = "none"

        def _fn(cliente):
            cliente.put(f"videos/{id_or_uuid}/rate", json_body={"rating": valoracion}, auth=True)
            return True
        ok = self._ejecutar(account_id, _fn)
        return bool(ok)

    def mi_valoracion(self, account_id: int, id_or_uuid: str) -> str:
        """Devuelve la valoración del usuario para un vídeo ('like'/'dislike'/'none')."""
        def _fn(cliente):
            datos = cliente.get(f"users/me/videos/{id_or_uuid}/rating", auth=True)
            return (datos.get("rating") if isinstance(datos, dict) else "none") or "none"
        try:
            return self._ejecutar(account_id, _fn)
        except Exception:
            return "none"

    # ------------------------------------------------------------------
    # Suscripciones a canales
    # ------------------------------------------------------------------
    def suscribirse(self, account_id: int, handle: str) -> bool:
        """Se suscribe a un canal por su identificador (nombre@instancia)."""
        def _fn(cliente):
            cliente.post("users/me/subscriptions", json_body={"uri": handle}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def cancelar_suscripcion(self, account_id: int, handle: str) -> bool:
        """Cancela la suscripción a un canal."""
        def _fn(cliente):
            cliente.delete(f"users/me/subscriptions/{handle}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def existe_suscripcion(self, account_id: int, handle: str) -> bool:
        """True si el usuario está suscrito a ese canal."""
        def _fn(cliente):
            datos = cliente.get("users/me/subscriptions/exist", params={"uris[]": handle}, auth=True)
            if isinstance(datos, dict):
                val = datos.get(handle)
                return bool(val)
            return False
        try:
            return self._ejecutar(account_id, _fn)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Bloqueos / silencios (blocklist personal)
    # ------------------------------------------------------------------
    def silenciar_cuenta(self, account_id: int, handle: str, silenciar: bool = True) -> bool:
        """Silencia (bloquea en la lista personal) una cuenta, o le quita el silencio."""
        handle = (handle or "").lstrip("@").strip()

        def _fn(cliente):
            if silenciar:
                cliente.post("users/me/blocklist/accounts", json_body={"accountName": handle},
                             auth=True)
            else:
                cliente.delete(f"users/me/blocklist/accounts/{handle}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def silenciar_servidor(self, account_id: int, host: str, silenciar: bool = True) -> bool:
        """Silencia una instancia entera (todas sus cuentas), o le quita el silencio."""
        host = (host or "").strip()

        def _fn(cliente):
            if silenciar:
                cliente.post("users/me/blocklist/servers", json_body={"host": host}, auth=True)
            else:
                cliente.delete(f"users/me/blocklist/servers/{host}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def listar_cuentas_silenciadas(self, account_id: int, *, limite: int = 50) -> List[Dict[str, Any]]:
        """Cuentas silenciadas por el usuario."""
        def _fn(cliente):
            data, _total = cliente.pagina("users/me/blocklist/accounts", start=0, count=limite,
                                          auth=True)
            res = []
            for it in (data or []):
                bloqueada = it.get("blockedAccount") if isinstance(it, dict) else None
                if bloqueada:
                    nombre = bloqueada.get("name") or ""
                    host = bloqueada.get("host") or ""
                    res.append({"handle": f"{nombre}@{host}" if host else nombre,
                                "display": bloqueada.get("displayName") or nombre})
            return res
        return self._ejecutar(account_id, _fn)

    def listar_servidores_silenciados(self, account_id: int, *, limite: int = 50) -> List[Dict[str, Any]]:
        """Instancias silenciadas por el usuario."""
        def _fn(cliente):
            data, _total = cliente.pagina("users/me/blocklist/servers", start=0, count=limite,
                                          auth=True)
            res = []
            for it in (data or []):
                bloqueado = it.get("blockedServer") if isinstance(it, dict) else None
                if bloqueado and bloqueado.get("host"):
                    res.append({"host": bloqueado["host"]})
            return res
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Reportes (abusos)
    # ------------------------------------------------------------------
    def reportar_video(self, account_id: int, video_id: Any, motivo: str) -> bool:
        """Reporta un vídeo a los moderadores de la instancia."""
        def _fn(cliente):
            cliente.post("abuses", json_body={"video": {"id": video_id}, "reason": motivo},
                         auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def reportar_comentario(self, account_id: int, comment_id: Any, motivo: str) -> bool:
        """Reporta un comentario a los moderadores."""
        def _fn(cliente):
            cliente.post("abuses", json_body={"comment": {"id": comment_id}, "reason": motivo},
                         auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def reportar_cuenta(self, account_id: int, cuenta_id: Any, motivo: str) -> bool:
        """Reporta una cuenta a los moderadores."""
        def _fn(cliente):
            cliente.post("abuses", json_body={"account": {"id": cuenta_id}, "reason": motivo},
                         auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Historial de reproducción
    # ------------------------------------------------------------------
    def vaciar_historial(self, account_id: int) -> bool:
        """Borra todo el historial de vídeos vistos del usuario."""
        def _fn(cliente):
            cliente.delete("users/me/history/videos", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def quitar_del_historial(self, account_id: int, id_or_uuid: str) -> bool:
        """Quita un vídeo concreto del historial."""
        def _fn(cliente):
            cliente.delete(f"users/me/history/videos/{id_or_uuid}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))
