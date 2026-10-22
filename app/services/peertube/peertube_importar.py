# -*- coding: utf-8 -*-
"""Importación de vídeos desde una URL, un magnet o un torrent (Fase 5 de PeerTube).

Mixin de `PeerTubeService`. La importación crea un vídeo a partir de un recurso externo (si la
instancia lo tiene habilitado). Se puede consultar el estado, cancelar, reintentar y borrar la tarea.
La UI avisa sobre derechos de autor antes de importar contenido ajeno.
"""
import logging
from typing import Any, Dict, List

from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)


class _ImportarMixin:
    def importar_url(self, account_id: int, target_url: str, *, channel_id: Any, name: str = "",
                     privacy: int = 3, **metadata) -> Dict[str, Any]:
        """Importa un vídeo desde una URL HTTP (por ejemplo, de otra plataforma)."""
        return self._importar(account_id, {"targetUrl": target_url}, channel_id, name, privacy,
                              metadata)

    def importar_magnet(self, account_id: int, magnet_uri: str, *, channel_id: Any, name: str = "",
                        privacy: int = 3, **metadata) -> Dict[str, Any]:
        """Importa un vídeo desde un enlace magnet."""
        return self._importar(account_id, {"magnetUri": magnet_uri}, channel_id, name, privacy,
                              metadata)

    def _importar(self, account_id: int, fuente: dict, channel_id: Any, name: str, privacy: int,
                  metadata: dict) -> Dict[str, Any]:
        def _fn(cliente):
            cuerpo = dict(fuente)
            cuerpo["channelId"] = channel_id
            cuerpo["privacy"] = privacy
            if name:
                cuerpo["name"] = name
            for k, v in (metadata or {}).items():
                if v is not None:
                    cuerpo[k] = v
            datos = cliente.post("videos/imports", data=cuerpo, auth=True)
            return {"id": _g(datos, "id"), "video": _g(datos, "video") or {},
                    "state": _g(_g(datos, "state") or {}, "label")}
        return self._ejecutar(account_id, _fn)

    def listar_importaciones(self, account_id: int, *, limite: int = 25) -> List[Dict[str, Any]]:
        """Lista las importaciones del usuario con su estado."""
        def _fn(cliente):
            data, _total = cliente.pagina("users/me/videos/imports", start=0, count=limite,
                                          auth=True)
            res = []
            for it in (data or []):
                video = _g(it, "video") or {}
                res.append({"id": _g(it, "id"),
                            "titulo": _g(video, "name") or _g(it, "targetUrl") or _g(it, "magnetUri") or "",
                            "estado": _g(_g(it, "state") or {}, "label") or "",
                            "error": _g(it, "error") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def cancelar_importacion(self, account_id: int, import_id: Any) -> bool:
        def _fn(cliente):
            cliente.post(f"videos/imports/{import_id}/cancel", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def reintentar_importacion(self, account_id: int, import_id: Any) -> bool:
        def _fn(cliente):
            cliente.post(f"videos/imports/{import_id}/retry", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def borrar_importacion(self, account_id: int, import_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"videos/imports/{import_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))
