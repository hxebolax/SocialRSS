# -*- coding: utf-8 -*-
"""Búsqueda y consulta de canales/cuentas (Fase 2 de PeerTube).

Mixin de `PeerTubeService`. La búsqueda de VÍDEOS se realiza como una sección normal
("search:<texto>", en `_VideosMixin`); aquí van la búsqueda de canales y la información de una
cuenta/canal concretos. La red va por `self._ejecutar`.
"""
import logging
from typing import Any, Dict, List
from urllib.parse import urlsplit

from app.core.config_manager import config
from app.services.peertube.peertube_modelos import Account, VideoChannel, parsear

log = logging.getLogger(__name__)


class _BusquedaMixin:
    def buscar_canales(self, account_id: int, texto: str, *, limite: int = 25) -> List[Dict[str, Any]]:
        """Busca canales por texto. Devuelve una lista de dicts {handle, display, host, followers,
        description}."""
        def _fn(cliente):
            data, _total = cliente.pagina("search/video-channels", params={"search": texto},
                                          start=0, count=limite, auth=True)
            res = []
            for item in (data or []):
                c = parsear(VideoChannel, item)
                res.append({"handle": c.handle, "display": c.display_name or c.name or "",
                            "host": c.host or "", "followers": int(c.followers_count or 0),
                            "description": c.description or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def info_canal(self, account_id: int, handle: str) -> Dict[str, Any]:
        """Información de un canal (`GET /video-channels/{handle}`)."""
        def _fn(cliente):
            datos = cliente.get(f"video-channels/{handle}", auth=True)
            c = parsear(VideoChannel, datos)
            return {"handle": c.handle, "display": c.display_name or c.name or "",
                    "host": c.host or "", "followers": int(c.followers_count or 0),
                    "description": c.description or "", "support": c.support or "",
                    "owner": (c.owner_account.handle if c.owner_account else "")}
        return self._ejecutar(account_id, _fn)

    def info_cuenta(self, account_id: int, handle: str) -> Dict[str, Any]:
        """Información de una cuenta (`GET /accounts/{handle}`)."""
        def _fn(cliente):
            datos = cliente.get(f"accounts/{handle}", auth=True)
            a = parsear(Account, datos)
            return {"handle": a.handle, "display": a.display_name or a.name or "",
                    "host": a.host or "", "followers": int(a.followers_count or 0),
                    "description": a.description or ""}
        return self._ejecutar(account_id, _fn)
