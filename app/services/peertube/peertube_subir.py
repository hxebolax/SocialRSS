# -*- coding: utf-8 -*-
"""Subida de vídeos (Fase 5 de PeerTube).

Mixin de `PeerTubeService`. Dos vías: subida simple (multipart, un POST) para archivos pequeños, y
subida REANUDABLE (protocolo uploadx: init + chunks con `Content-Range`) para archivos grandes, con
progreso, cancelación y sin cargar el vídeo entero en memoria (se lee por trozos del disco).
"""
import os
import logging
import mimetypes
import threading
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit, parse_qs

from app.core.errors import PeerTubeServiceError

log = logging.getLogger(__name__)

# Tamaño de cada trozo de la subida reanudable (5 MiB; múltiplo recomendado por el protocolo).
TAM_CHUNK_SUBIDA = 5 * 1024 * 1024


class _SubirMixin:
    def subir_video(self, account_id: int, ruta_archivo: str, *, channel_id: Any, name: str,
                    privacy: int = 3, cancelar: Optional[threading.Event] = None,
                    **metadata) -> Dict[str, Any]:
        """Sube un vídeo en un solo POST multipart (para archivos no muy grandes). El archivo se pasa
        como manejador y httpx lo transmite en streaming (no se carga entero en memoria)."""
        nombre = os.path.basename(ruta_archivo)

        def _fn(cliente):
            campos = self._campos_subida(channel_id, name, privacy, metadata)
            with open(ruta_archivo, "rb") as f:
                files = {"videofile": (nombre, f, "application/octet-stream")}
                datos = cliente.solicitar("POST", "videos/upload", data=campos, files=files,
                                          auth=True, reintentos=0, cancelar=cancelar)
            v = (datos.get("video") if isinstance(datos, dict) else None) or {}
            return {"id": v.get("id"), "uuid": v.get("uuid"), "shortUUID": v.get("shortUUID")}
        return self._ejecutar(account_id, _fn)

    def subir_video_reanudable(self, account_id: int, ruta_archivo: str, *, channel_id: Any,
                               name: str, privacy: int = 3,
                               progreso: Optional[Callable[[int, int], None]] = None,
                               cancelar: Optional[threading.Event] = None,
                               **metadata) -> Dict[str, Any]:
        """Sube un vídeo por el protocolo REANUDABLE (init + chunks). Llama a `progreso(subido, total)`
        tras cada trozo. Si se cancela, aborta la subida en el servidor. Devuelve {id, uuid}."""
        total = os.path.getsize(ruta_archivo)
        mime = mimetypes.guess_type(ruta_archivo)[0] or "video/mp4"
        nombre = os.path.basename(ruta_archivo)
        upload_id = self._iniciar_subida_reanudable(account_id, nombre, total, mime, channel_id,
                                                     name, privacy, metadata, cancelar)
        subido = 0
        with open(ruta_archivo, "rb") as f:
            while subido < total:
                if cancelar is not None and cancelar.is_set():
                    self._cancelar_subida_reanudable(account_id, upload_id)
                    from app.services.peertube.peertube_http import PeerTubeCancelado
                    raise PeerTubeCancelado("Subida cancelada.")
                trozo = f.read(TAM_CHUNK_SUBIDA)
                if not trozo:
                    break
                ini = subido
                fin = subido + len(trozo) - 1

                def _put(cliente):
                    cab = {"Content-Range": f"bytes {ini}-{fin}/{total}",
                           "Content-Length": str(len(trozo)),
                           "Content-Type": "application/octet-stream"}
                    return cliente.solicitar_cruda("PUT", "videos/upload-resumable",
                                                   params={"upload_id": upload_id}, content=trozo,
                                                   headers=cab, auth=True, reintentos=0,
                                                   cancelar=cancelar)
                resp = self._ejecutar(account_id, _put)
                subido += len(trozo)
                if progreso:
                    try:
                        progreso(subido, total)
                    except Exception:
                        pass
                if resp.status_code == 200:
                    v = (resp.json().get("video") if resp.content else {}) or {}
                    return {"id": v.get("id"), "uuid": v.get("uuid"), "shortUUID": v.get("shortUUID")}
                if resp.status_code != 308:
                    raise PeerTubeServiceError(
                        f"La subida falló al enviar un fragmento (HTTP {resp.status_code}).")
        raise PeerTubeServiceError("La subida terminó sin que el servidor confirmara el vídeo.")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    @staticmethod
    def _campos_subida(channel_id: Any, name: str, privacy: int, metadata: dict) -> dict:
        """Construye los campos de formulario de la subida (metadatos)."""
        campos: Dict[str, Any] = {"channelId": channel_id, "name": name, "privacy": privacy}
        for k, v in (metadata or {}).items():
            if v is not None:
                campos[k] = v
        return campos

    def _iniciar_subida_reanudable(self, account_id: int, nombre: str, total: int, mime: str,
                                   channel_id: Any, name: str, privacy: int, metadata: dict,
                                   cancelar) -> str:
        """POST de inicialización: devuelve el upload_id extraído de la cabecera Location."""
        def _fn(cliente):
            cuerpo = self._campos_subida(channel_id, name, privacy, metadata)
            cuerpo["filename"] = nombre
            cab = {"X-Upload-Content-Length": str(total), "X-Upload-Content-Type": mime}
            resp = cliente.solicitar_cruda("POST", "videos/upload-resumable", json_body=cuerpo,
                                           headers=cab, auth=True, reintentos=0, cancelar=cancelar)
            if resp.status_code not in (200, 201):
                raise PeerTubeServiceError(
                    self._mensaje_cuota(resp) if resp.status_code == 413
                    else f"No se pudo iniciar la subida (HTTP {resp.status_code}).")
            location = resp.headers.get("Location") or resp.headers.get("location") or ""
            uid = parse_qs(urlsplit(location).query).get("upload_id", [None])[0]
            if not uid:
                raise PeerTubeServiceError("El servidor no devolvió el identificador de subida.")
            return uid
        return self._ejecutar(account_id, _fn)

    @staticmethod
    def _mensaje_cuota(resp) -> str:
        codigo = ""
        try:
            codigo = (resp.json() or {}).get("code", "") if resp.content else ""
        except Exception:
            codigo = ""
        if codigo == "quota_reached":
            return "Has alcanzado tu cuota de almacenamiento en esta instancia."
        if codigo == "max_file_size_reached":
            return "El archivo supera el tamaño máximo permitido por la instancia."
        return "El archivo es demasiado grande para esta instancia."

    def _cancelar_subida_reanudable(self, account_id: int, upload_id: str) -> None:
        """DELETE de la subida reanudable (best-effort)."""
        def _fn(cliente):
            cliente.solicitar_cruda("DELETE", "videos/upload-resumable",
                                    params={"upload_id": upload_id},
                                    headers={"Content-Length": "0"}, auth=True, reintentos=0)
            return True
        try:
            self._ejecutar(account_id, _fn)
        except Exception:
            pass
