# -*- coding: utf-8 -*-
"""Emisiones en directo (Fase 6 de PeerTube).

Mixin de `PeerTubeService`. Crea y gestiona directos y expone sus datos de emisión (URL RTMP y clave
de emisión) para configurarlos en un codificador externo tipo OBS. La reproducción de un directo (y
de sus repeticiones) usa el reproductor normal (un directo es un vídeo con `isLive`, que suena como
la radio, sin barra de tiempo).

SEGURIDAD: la clave de emisión (`streamKey`) es SECRETA. Nunca se registra en logs; la UI la muestra
oculta y solo la revela/copia con una acción explícita y advertida.
"""
import logging
from typing import Any, Dict, List

from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)

# Modos de latencia de PeerTube.
LATENCIA_DEFECTO = 1
LATENCIA_ALTA = 2
LATENCIA_BAJA = 3


class _DirectosMixin:
    def crear_directo(self, account_id: int, channel_id: Any, name: str, *, privacy: int = 3,
                      permanent_live: bool = False, latency_mode: int = LATENCIA_DEFECTO,
                      save_replay: bool = False, replay_privacy: int = 1, description: str = "",
                      nsfw: bool = False, tags: List[str] = None) -> Dict[str, Any]:
        """Crea una emisión en directo. Devuelve {id, uuid}. `permanent_live` permite emitir varias
        veces; `save_replay` guarda la repetición (incompatible con `permanent_live` en algunas
        instancias). `latency_mode`: 1 normal, 2 alta, 3 baja."""
        def _fn(cliente):
            cuerpo: Dict[str, Any] = {
                "channelId": channel_id, "name": name, "privacy": privacy,
                "permanentLive": self._bool(permanent_live),
                "latencyMode": latency_mode, "saveReplay": self._bool(save_replay)}
            if save_replay:
                cuerpo["replaySettings[privacy]"] = replay_privacy
            if description:
                cuerpo["description"] = description
            if nsfw:
                cuerpo["nsfw"] = self._bool(True)
            for i, t in enumerate(tags or []):
                cuerpo[f"tags[{i}]"] = t
            datos = cliente.post("videos/live", data=cuerpo, auth=True)
            v = (_g(datos, "video") or {}) if isinstance(datos, dict) else {}
            return {"id": _g(v, "id"), "uuid": _g(v, "uuid"), "shortUUID": _g(v, "shortUUID")}
        return self._ejecutar(account_id, _fn)

    def info_directo(self, account_id: int, video_id: Any) -> Dict[str, Any]:
        """Datos de emisión de un directo (incluida la clave secreta). No registrar en logs."""
        def _fn(cliente):
            datos = cliente.get(f"videos/live/{video_id}", auth=True)
            return {"rtmp_url": _g(datos, "rtmpUrl") or "",
                    "rtmps_url": _g(datos, "rtmpsUrl") or "",
                    "stream_key": _g(datos, "streamKey") or "",
                    "permanent_live": bool(_g(datos, "permanentLive", default=False)),
                    "latency_mode": _g(datos, "latencyMode"),
                    "save_replay": bool(_g(datos, "saveReplay", default=False))}
        return self._ejecutar(account_id, _fn)

    def actualizar_directo(self, account_id: int, video_id: Any, **campos) -> bool:
        """Actualiza la configuración de un directo (saveReplay, permanentLive, latencyMode,
        replaySettings). Solo antes de que empiece la emisión."""
        cuerpo = {k: v for k, v in campos.items() if v is not None}

        def _fn(cliente):
            cliente.put(f"videos/live/{video_id}", json_body=cuerpo, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def sesiones_directo(self, account_id: int, video_id: Any) -> List[Dict[str, Any]]:
        """Lista las sesiones (emisiones/repeticiones) de un directo."""
        def _fn(cliente):
            data, _total = cliente.pagina(f"videos/live/{video_id}/sessions", start=0, count=50,
                                          auth=True)
            res = []
            for s in (data or []):
                replay = _g(s, "replayVideo") or {}
                res.append({"inicio": _g(s, "startDate") or "", "fin": _g(s, "endDate") or "",
                            "error": _g(s, "error"),
                            "repeticion_uuid": _g(replay, "uuid") or "",
                            "repeticion_titulo": _g(replay, "name") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    @staticmethod
    def _bool(valor: bool) -> str:
        """PeerTube espera 'true'/'false' en los formularios."""
        return "true" if valor else "false"
