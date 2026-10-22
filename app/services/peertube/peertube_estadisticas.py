# -*- coding: utf-8 -*-
"""Estadísticas (Fase 7 de PeerTube).

Mixin de `PeerTubeService`. Estadísticas generales de un vídeo (para su creador o un moderador) y de
la instancia (públicas). La UI las presenta como TEXTO accesible; los gráficos, si se añaden, serían
complementarios y nunca la única vía (regla de accesibilidad).
"""
import logging
from typing import Any, Dict, List

from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)


class _EstadisticasMixin:
    def stats_video(self, account_id: int, video_id: Any) -> Dict[str, Any]:
        """Estadísticas generales de un vídeo (`stats/overall`): visualizaciones, tiempo de
        reproducción, pico de espectadores y países principales."""
        def _fn(cliente):
            datos = cliente.get(f"videos/{video_id}/stats/overall", auth=True)
            paises: List[Dict[str, Any]] = []
            for c in (_g(datos, "countries") or []):
                paises.append({"pais": _g(c, "isoCode") or "", "espectadores": _g(c, "viewers") or 0})
            return {"total_espectadores": int(_g(datos, "totalViewers", default=0) or 0),
                    "pico_espectadores": int(_g(datos, "viewersPeak", default=0) or 0),
                    "tiempo_total": float(_g(datos, "totalWatchTime", default=0) or 0),
                    "tiempo_medio": float(_g(datos, "averageWatchTime", default=0) or 0),
                    "paises": paises[:10]}
        return self._ejecutar(account_id, _fn)

    def stats_instancia(self, account_id: int) -> Dict[str, Any]:
        """Estadísticas públicas de la instancia (`server/stats`). Se devuelven en crudo las claves
        numéricas más útiles (la UI las presenta como texto)."""
        def _fn(cliente):
            datos = cliente.get("server/stats", auth=True)
            if not isinstance(datos, dict):
                return {}
            interesantes = ("totalUsers", "totalVideos", "totalLocalVideos", "totalVideoComments",
                            "totalInstanceFollowers", "totalInstanceFollowing",
                            "totalLocalVideoViews", "totalLocalVideoChannels", "totalLocalPlaylists")
            return {k: datos[k] for k in interesantes if k in datos and isinstance(datos[k], (int, float))}
        return self._ejecutar(account_id, _fn)
