# -*- coding: utf-8 -*-
"""Notificaciones del usuario (Fase 7 de PeerTube).

Mixin de `PeerTubeService`. Lista las notificaciones convirtiendo cada una a una descripción legible
según su tipo (nuevo vídeo de una suscripción, comentario en tu vídeo, nuevo seguidor, mención,
importación terminada...), y permite marcarlas como leídas y ajustar las preferencias. No hay push:
se sondea por intervalo como el resto.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)

# Tipos de notificación de PeerTube (UserNotificationType).
(NUEVO_VIDEO_SUB, COMENTARIO_EN_MI_VIDEO, ABUSO_MODERADOR, BLACKLIST_MI_VIDEO, UNBLACKLIST_MI_VIDEO,
 MI_VIDEO_PUBLICADO, MI_IMPORT_OK, MI_IMPORT_ERROR, NUEVO_REGISTRO, NUEVO_SEGUIDOR, MENCION,
 AUTO_BLACKLIST_MOD, NUEVO_SEGUIDOR_INSTANCIA, AUTO_SIGUIENDO_INSTANCIA, ABUSO_CAMBIO_ESTADO,
 ABUSO_NUEVO_MENSAJE, NUEVO_PLUGIN, NUEVA_VERSION_PEERTUBE, ESTUDIO_TERMINADO,
 SOLICITUD_REGISTRO, NUEVO_DIRECTO_SUB) = range(1, 22)


class _NotificacionesMixin:
    def listar_notificaciones(self, account_id: int, *, solo_no_leidas: bool = False,
                              limite: int = 25) -> List[Dict[str, Any]]:
        """Lista las notificaciones (con descripción legible). Cada una: {id, tipo, leida,
        descripcion, video_uuid, fecha}."""
        params = {"sort": "-createdAt"}
        if solo_no_leidas:
            params["unread"] = "true"

        def _fn(cliente):
            data, _total = cliente.pagina("users/me/notifications", params=params, start=0,
                                          count=limite, auth=True)
            res = []
            for n in (data or []):
                res.append({"id": _g(n, "id"), "tipo": _g(n, "type"),
                            "leida": bool(_g(n, "read", default=False)),
                            "descripcion": self._descripcion_notif(n),
                            "video_uuid": self._video_uuid_notif(n),
                            "fecha": _g(n, "createdAt") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def contador_no_leidas(self, account_id: int) -> int:
        """Número de notificaciones sin leer."""
        def _fn(cliente):
            datos = cliente.get("users/me/notifications", params={"unread": "true", "count": 0},
                                auth=True)
            return int(_g(datos, "total", default=0) or 0) if isinstance(datos, dict) else 0
        try:
            return self._ejecutar(account_id, _fn)
        except Exception:
            return 0

    def marcar_leidas(self, account_id: int, ids: List[Any]) -> bool:
        def _fn(cliente):
            cliente.post("users/me/notifications/read", json_body={"ids": list(ids)}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def marcar_todas_leidas(self, account_id: int) -> bool:
        def _fn(cliente):
            cliente.post("users/me/notifications/read-all", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def preferencias_notificaciones(self, account_id: int) -> Dict[str, Any]:
        """Devuelve el diccionario de preferencias de notificación (clave -> valor entero)."""
        def _fn(cliente):
            datos = cliente.get("users/me/notification-settings", auth=True)
            return datos if isinstance(datos, dict) else {}
        return self._ejecutar(account_id, _fn)

    def guardar_preferencias_notificaciones(self, account_id: int, ajustes: Dict[str, int]) -> bool:
        """Guarda las preferencias (cada clave -> 0 nada, 1 web, 2 email, 3 ambos)."""
        def _fn(cliente):
            cliente.put("users/me/notification-settings", json_body=ajustes, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Descripción legible por tipo
    # ------------------------------------------------------------------
    def _descripcion_notif(self, n: dict) -> str:
        tipo = _g(n, "type")
        video = _g(n, "video") or {}
        comment = _g(n, "comment") or {}
        follow = _g(n, "actorFollow") or {}
        imp = _g(n, "videoImport") or {}
        canal = self._nombre_actor(_g(video, "channel") or {})
        autor_com = self._nombre_actor(_g(comment, "account") or {})
        seguidor = self._nombre_actor(_g(follow, "follower") or {})
        video_com = _g(_g(comment, "video") or {}, "name") or ""
        if tipo == NUEVO_VIDEO_SUB:
            return _("Nuevo vídeo de {0}: {1}").format(canal, _g(video, "name") or "")
        if tipo == NUEVO_DIRECTO_SUB:
            return _("Directo en marcha de {0}: {1}").format(canal, _g(video, "name") or "")
        if tipo == COMENTARIO_EN_MI_VIDEO:
            return _("{0} comentó en tu vídeo «{1}»").format(autor_com, video_com)
        if tipo == MENCION:
            return _("{0} te mencionó en un comentario").format(autor_com)
        if tipo == NUEVO_SEGUIDOR:
            return _("{0} se ha suscrito a tu canal").format(seguidor)
        if tipo == MI_VIDEO_PUBLICADO:
            return _("Tu vídeo «{0}» se ha publicado").format(_g(video, "name") or "")
        if tipo == MI_IMPORT_OK:
            return _("Importación completada: {0}").format(
                _g(_g(imp, "video") or {}, "name") or _g(imp, "targetUrl") or "")
        if tipo == MI_IMPORT_ERROR:
            return _("Falló una importación de vídeo")
        if tipo == ESTUDIO_TERMINADO:
            return _("La edición de tu vídeo «{0}» ha terminado").format(_g(video, "name") or "")
        if tipo in (BLACKLIST_MI_VIDEO, UNBLACKLIST_MI_VIDEO):
            estado = _("bloqueado") if tipo == BLACKLIST_MI_VIDEO else _("desbloqueado")
            return _("Tu vídeo «{0}» ha sido {1} por un moderador").format(
                _g(video, "name") or "", estado)
        if tipo in (ABUSO_MODERADOR, AUTO_BLACKLIST_MOD, ABUSO_CAMBIO_ESTADO, ABUSO_NUEVO_MENSAJE):
            return _("Novedad de moderación (denuncias)")
        if tipo in (NUEVO_REGISTRO, SOLICITUD_REGISTRO):
            return _("Registro de usuario en la instancia")
        if tipo in (NUEVO_SEGUIDOR_INSTANCIA, AUTO_SIGUIENDO_INSTANCIA):
            return _("Novedad de federación entre instancias")
        if tipo == NUEVA_VERSION_PEERTUBE:
            return _("Hay una nueva versión de PeerTube disponible")
        if tipo == NUEVO_PLUGIN:
            return _("Hay una nueva versión de un plugin")
        return _("Notificación (tipo {0})").format(tipo)

    @staticmethod
    def _nombre_actor(actor: dict) -> str:
        nombre = _g(actor, "displayName") or _g(actor, "name") or ""
        host = _g(actor, "host") or ""
        if _g(actor, "name") and host and not _g(actor, "displayName"):
            return f"{_g(actor, 'name')}@{host}"
        return nombre or _("alguien")

    @staticmethod
    def _video_uuid_notif(n: dict) -> Optional[str]:
        """UUID del vídeo relacionado con la notificación (para poder abrirlo), si lo hay."""
        for clave in ("video",):
            v = _g(n, clave) or {}
            if _g(v, "uuid"):
                return _g(v, "uuid")
        comment = _g(n, "comment") or {}
        v = _g(comment, "video") or {}
        if _g(v, "uuid"):
            return _g(v, "uuid")
        imp = _g(n, "videoImport") or {}
        v = _g(imp, "video") or {}
        return _g(v, "uuid")
