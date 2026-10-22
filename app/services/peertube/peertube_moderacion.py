# -*- coding: utf-8 -*-
"""Moderación (Fase 8 de PeerTube).

Mixin de `PeerTubeService`. Herramientas para moderadores y administradores: la cola de denuncias
(abusos), su resolución (aceptar/rechazar) y mensajes internos, y el bloqueo de vídeos. También la
consulta del estado de las denuncias que ha puesto el propio usuario. La UI pide confirmación en las
acciones destructivas.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)

# Estados de una denuncia (AbuseState): 1 pendiente, 2 rechazada, 3 aceptada.
ABUSO_PENDIENTE = 1
ABUSO_RECHAZADO = 2
ABUSO_ACEPTADO = 3
_ESTADO_ES = {1: "pendiente", 2: "rechazada", 3: "aceptada"}


class _ModeracionMixin:
    def _abuso_a_dict(self, a: dict) -> Dict[str, Any]:
        video = _g(a, "video") or {}
        comment = _g(a, "comment") or {}
        reportero = _g(a, "reporterAccount") or {}
        denunciado = _g(a, "flaggedAccount") or {}
        estado_id = _g(_g(a, "state") or {}, "id")
        return {"id": _g(a, "id"), "motivo": _g(a, "reason") or "",
                "estado": _ESTADO_ES.get(estado_id, ""), "estado_id": estado_id,
                "video_uuid": _g(video, "uuid") or "", "video_titulo": _g(video, "name") or "",
                "comentario_denunciado": bool(comment),
                "denunciante": self._handle_actor(reportero),
                "denunciado": self._handle_actor(denunciado),
                "comentario_moderacion": _g(a, "moderationComment") or "",
                "num_mensajes": int(_g(a, "countMessages", default=0) or 0),
                "fecha": _g(a, "createdAt") or ""}

    @staticmethod
    def _handle_actor(actor: dict) -> str:
        nombre = _g(actor, "name") or ""
        host = _g(actor, "host") or ""
        return f"{nombre}@{host}" if nombre and host else (nombre or "")

    # ------------------------------------------------------------------
    # Cola de denuncias (moderador/administrador)
    # ------------------------------------------------------------------
    def listar_abusos(self, account_id: int, *, estado: Optional[int] = None,
                      limite: int = 25) -> List[Dict[str, Any]]:
        """Lista las denuncias de la instancia (requiere ser moderador/administrador)."""
        params = {"sort": "-createdAt"}
        if estado:
            params["state"] = estado

        def _fn(cliente):
            data, _total = cliente.pagina("abuses", params=params, start=0, count=limite, auth=True)
            return [self._abuso_a_dict(a) for a in (data or [])]
        return self._ejecutar(account_id, _fn)

    def mis_denuncias(self, account_id: int, *, limite: int = 25) -> List[Dict[str, Any]]:
        """Denuncias que ha puesto el propio usuario, con su estado."""
        def _fn(cliente):
            data, _total = cliente.pagina("users/me/abuses", params={"sort": "-createdAt"},
                                          start=0, count=limite, auth=True)
            return [self._abuso_a_dict(a) for a in (data or [])]
        return self._ejecutar(account_id, _fn)

    def actualizar_abuso(self, account_id: int, abuse_id: Any, *, estado: Optional[int] = None,
                         comentario: str = "") -> bool:
        """Actualiza una denuncia: su estado (aceptada/rechazada) y/o el comentario de moderación."""
        cuerpo: Dict[str, Any] = {}
        if estado:
            cuerpo["state"] = estado
        if comentario:
            cuerpo["moderationComment"] = comentario

        def _fn(cliente):
            cliente.put(f"abuses/{abuse_id}", json_body=cuerpo, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def aceptar_abuso(self, account_id: int, abuse_id: Any, *, comentario: str = "") -> bool:
        return self.actualizar_abuso(account_id, abuse_id, estado=ABUSO_ACEPTADO, comentario=comentario)

    def rechazar_abuso(self, account_id: int, abuse_id: Any, *, comentario: str = "") -> bool:
        return self.actualizar_abuso(account_id, abuse_id, estado=ABUSO_RECHAZADO, comentario=comentario)

    def borrar_abuso(self, account_id: int, abuse_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"abuses/{abuse_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def mensajes_abuso(self, account_id: int, abuse_id: Any) -> List[Dict[str, Any]]:
        """Mensajes internos de una denuncia."""
        def _fn(cliente):
            datos = cliente.get(f"abuses/{abuse_id}/messages", auth=True)
            filas = datos.get("data") if isinstance(datos, dict) else datos
            res = []
            for m in (filas or []):
                autor = _g(m, "account") or {}
                res.append({"mensaje": _g(m, "message") or "",
                            "autor": self._handle_actor(autor) or _g(autor, "displayName") or "",
                            "fecha": _g(m, "createdAt") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def añadir_mensaje_abuso(self, account_id: int, abuse_id: Any, mensaje: str) -> bool:
        def _fn(cliente):
            cliente.post(f"abuses/{abuse_id}/messages", json_body={"message": mensaje}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Bloqueo de vídeos (blacklist)
    # ------------------------------------------------------------------
    def listar_videos_bloqueados(self, account_id: int, *, limite: int = 25) -> List[Dict[str, Any]]:
        """Vídeos bloqueados en la instancia (moderador/administrador)."""
        def _fn(cliente):
            data, _total = cliente.pagina("videos/blacklist", params={"sort": "-createdAt"},
                                          start=0, count=limite, auth=True)
            res = []
            for b in (data or []):
                video = _g(b, "video") or {}
                res.append({"video_id": _g(video, "id"), "video_uuid": _g(video, "uuid") or "",
                            "titulo": _g(video, "name") or "", "razon": _g(b, "reason") or "",
                            "sin_federar": bool(_g(b, "unfederated", default=False))})
            return res
        return self._ejecutar(account_id, _fn)

    def bloquear_video(self, account_id: int, video_id: Any, *, razon: str = "",
                       unfederate: bool = False) -> bool:
        """Bloquea (retira) un vídeo de la instancia. `unfederate` lo retira también de las
        instancias remotas (solo para vídeos locales)."""
        def _fn(cliente):
            cuerpo: Dict[str, Any] = {}
            if razon:
                cuerpo["reason"] = razon
            if unfederate:
                cuerpo["unfederate"] = True
            cliente.post(f"videos/{video_id}/blacklist", json_body=cuerpo or None, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def desbloquear_video(self, account_id: int, video_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"videos/{video_id}/blacklist", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))
