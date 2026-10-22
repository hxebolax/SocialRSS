# -*- coding: utf-8 -*-
"""Descargas autorizadas de vídeos (Fase 5 de PeerTube).

Mixin de `PeerTubeService`. Descarga el fichero progresivo de un vídeo (cuando el propietario y la
instancia lo permiten) a una carpeta local, con progreso, cancelación y nombre de archivo seguro. No
carga el vídeo en memoria: se escribe en disco por trozos según llega. Nunca se registra la URL
firmada completa en los logs.
"""
import os
import re
import logging
import threading
from typing import Any, Callable, Dict, Optional

from app.core.errors import PeerTubeServiceError
from app.services.peertube.peertube_common import crear_cliente_httpx
from app.services.peertube.peertube_stream import UA_NAVEGADOR

log = logging.getLogger(__name__)

_INSEGUROS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def nombre_seguro(nombre: str, *, extension: str = ".mp4") -> str:
    """Convierte un título en un nombre de archivo seguro (sin caracteres inválidos ni traversal)."""
    base = _INSEGUROS.sub("_", (nombre or "video").strip()) or "video"
    base = base.strip(". ")[:150] or "video"
    if not base.lower().endswith(extension.lower()):
        base += extension
    return base


class _DescargasMixin:
    def descargar_video(self, account_id: int, video_uuid: str, carpeta_destino: str, *,
                        preferencia: Optional[str] = None,
                        progreso: Optional[Callable[[int, int], None]] = None,
                        cancelar: Optional[threading.Event] = None) -> str:
        """Descarga el mejor fichero progresivo del vídeo a `carpeta_destino`. Devuelve la ruta del
        archivo. Lanza `PeerTubeServiceError` si el vídeo no permite descarga o no hay fichero
        progresivo. Bloqueante: llamar desde task_manager."""
        det = self.detalle_video(account_id, video_uuid)
        import json
        fuentes = json.loads(det.get("files_json") or "[]")
        # Para DESCARGAR se prefiere la MAYOR resolución (no la de audio); se busca un fichero web.
        web = [f for f in fuentes if f.get("tipo") == "web" and f.get("url")]
        if not web:
            raise PeerTubeServiceError(
                "Este vídeo no ofrece un fichero descargable (solo emisión HLS o descarga "
                "deshabilitada por el autor).")
        elegido = self._mejor_para_descarga(web, preferencia)
        url = elegido["url"]
        ext = os.path.splitext(url.split("?", 1)[0])[1] or ".mp4"
        destino = os.path.join(carpeta_destino, nombre_seguro(det.get("name") or video_uuid,
                                                              extension=ext))
        # Evitar duplicados: si ya existe, añadir un sufijo.
        destino = self._destino_unico(destino)
        self._descargar_stream(url, destino, progreso=progreso, cancelar=cancelar)
        return destino

    @staticmethod
    def _mejor_para_descarga(web: list, preferencia: Optional[str]) -> dict:
        def _res(f):
            try:
                return int(f.get("resolucion") or 0)
            except (TypeError, ValueError):
                return 0
        ordenadas = sorted(web, key=_res, reverse=True)   # mayor resolución primero
        pref = (preferencia or "").strip().lower()
        if pref and pref not in ("auto", ""):
            try:
                objetivo = int(pref)
                exactas = [f for f in web if _res(f) == objetivo]
                if exactas:
                    return exactas[0]
                menores = [f for f in ordenadas if _res(f) <= objetivo]
                if menores:
                    return menores[0]
            except ValueError:
                pass
        return ordenadas[0]

    @staticmethod
    def _destino_unico(ruta: str) -> str:
        if not os.path.exists(ruta):
            return ruta
        raiz, ext = os.path.splitext(ruta)
        i = 1
        while os.path.exists(f"{raiz} ({i}){ext}"):
            i += 1
        return f"{raiz} ({i}){ext}"

    def _descargar_stream(self, url: str, destino: str, *, progreso, cancelar) -> None:
        """Descarga por streaming a un archivo temporal y lo renombra al terminar (para no dejar un
        archivo a medias si se cancela o falla)."""
        from app.services.peertube.peertube_http import PeerTubeCancelado
        tmp = destino + ".part"
        cliente = crear_cliente_httpx(timeout=60.0)
        try:
            with cliente.stream("GET", url, headers={"User-Agent": UA_NAVEGADOR}) as r:
                if r.status_code >= 400:
                    raise PeerTubeServiceError(
                        f"El servidor rechazó la descarga (HTTP {r.status_code}).")
                total = int(r.headers.get("Content-Length") or 0)
                descargado = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1024 * 256):
                        if cancelar is not None and cancelar.is_set():
                            raise PeerTubeCancelado("Descarga cancelada.")
                        f.write(chunk)
                        descargado += len(chunk)
                        if progreso:
                            try:
                                progreso(descargado, total)
                            except Exception:
                                pass
            os.replace(tmp, destino)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise
        finally:
            try:
                cliente.close()
            except Exception:
                pass
