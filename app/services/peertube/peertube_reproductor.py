# -*- coding: utf-8 -*-
"""Selección de fuente de reproducción, resolución de variantes HLS, subtítulos y registro de vista
(Fase 3 de PeerTube).

Mixin de `PeerTubeService`. La reproducción real la hace BassKit (por player2), que es un motor de
AUDIO: de un vídeo suena solo la pista de audio (adecuado para un cliente de personas ciegas). Aquí
se decide QUÉ URL reproducir a partir de las fuentes del vídeo, se resuelve una variante concreta de
un máster HLS y se ofrecen los subtítulos como texto accesible.

Estrategia (ver el informe de BassKit): preferir el fichero progresivo MP4; si solo hay HLS, resolver
una variante; el WebM necesita FFmpeg (se marca para que la UI avise). Para un cliente de audio, a
igualdad de audio conviene la MENOR resolución (menos ancho de banda) o una rendición de solo audio.
"""
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from app.core.config_manager import config

log = logging.getLogger(__name__)


class _ReproductorMixin:
    # ------------------------------------------------------------------
    # Selección de la fuente a reproducir
    # ------------------------------------------------------------------
    def seleccionar_fuente(self, fuentes: List[Dict[str, Any]], *, es_directo: bool = False,
                           preferencia: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Elige la mejor fuente de la lista `fuentes` (de `files_json`). Devuelve un dict
        {tipo, url, resolucion, es_hls, necesita_ffmpeg} o None si no hay ninguna reproducible.

        Orden de preferencia: fichero web MP4 (progresivo, el más fiable) → máster HLS → fichero web
        WebM (requiere FFmpeg) → fichero HLS suelto (fragmentado, último recurso)."""
        if preferencia is None:
            preferencia = str(config.get("peertube", "preferred_resolution", "auto"))
        web = [f for f in (fuentes or []) if f.get("tipo") == "web" and f.get("url")]
        hls_master = [f for f in (fuentes or []) if f.get("tipo") == "hls" and f.get("url")]
        hls_file = [f for f in (fuentes or []) if f.get("tipo") == "hls-file" and f.get("url")]

        web_mp4 = [f for f in web if self._es_mp4(f["url"])]
        web_webm = [f for f in web if not self._es_mp4(f["url"])]

        if web_mp4:
            elegido = self._elegir_por_resolucion(web_mp4, preferencia)
            return {"tipo": "web", "url": elegido["url"], "resolucion": elegido.get("resolucion"),
                    "es_hls": False, "necesita_ffmpeg": False}
        if hls_master:
            return {"tipo": "hls", "url": hls_master[0]["url"], "resolucion": None,
                    "es_hls": True, "necesita_ffmpeg": False}
        if web_webm:
            elegido = self._elegir_por_resolucion(web_webm, preferencia)
            return {"tipo": "web", "url": elegido["url"], "resolucion": elegido.get("resolucion"),
                    "es_hls": False, "necesita_ffmpeg": True}
        if hls_file:
            elegido = self._elegir_por_resolucion(hls_file, preferencia)
            return {"tipo": "hls-file", "url": elegido["url"], "resolucion": elegido.get("resolucion"),
                    "es_hls": False, "necesita_ffmpeg": True}
        return None

    @staticmethod
    def _es_mp4(url: str) -> bool:
        ruta = urlsplit(url or "").path.lower()
        return ruta.endswith(".mp4") or ruta.endswith(".m4a") or ruta.endswith(".m4v")

    @staticmethod
    def _elegir_por_resolucion(lista: List[Dict[str, Any]], preferencia: str) -> Dict[str, Any]:
        """Elige un fichero por preferencia de resolución. Una rendición de solo audio (resolución 0)
        se prefiere SIEMPRE (es lo ideal para un cliente de audio). 'auto' → la menor resolución
        (mismo audio, menos ancho de banda). Un número → la mayor que no lo supere; si ninguna, la
        menor."""
        def _res(f):
            r = f.get("resolucion")
            try:
                return int(r) if r is not None else 99999
            except (TypeError, ValueError):
                return 99999
        # Rendición de solo audio (resolución 0).
        solo_audio = [f for f in lista if _res(f) == 0]
        if solo_audio:
            return solo_audio[0]
        ordenadas = sorted(lista, key=_res)
        pref = (preferencia or "auto").strip().lower()
        if pref in ("", "auto"):
            return ordenadas[0]
        try:
            objetivo = int(pref)
        except ValueError:
            return ordenadas[0]
        candidatas = [f for f in ordenadas if _res(f) <= objetivo]
        return candidatas[-1] if candidatas else ordenadas[0]

    # ------------------------------------------------------------------
    # Resolución de una variante de un máster HLS
    # ------------------------------------------------------------------
    def resolver_variante_hls(self, account_id: int, url_master: str, *,
                              preferencia: Optional[str] = None) -> str:
        """Descarga el máster m3u8 y devuelve la URL absoluta de una variante concreta (para dársela
        a BASS/basshls). Si ya es una media playlist (no un máster), devuelve la propia URL. Ante
        cualquier problema, devuelve la URL del máster (mejor intentar que fallar)."""
        if preferencia is None:
            preferencia = str(config.get("peertube", "preferred_resolution", "auto"))

        def _fn(cliente):
            resp = cliente.solicitar("GET", url_master, auth=True, espera_json=False)
            return getattr(resp, "text", "") or ""

        try:
            texto = self._ejecutar(account_id, _fn)
        except Exception:
            return url_master
        try:
            import m3u8
            pl = m3u8.loads(texto, uri=url_master)
        except Exception:
            return url_master
        variantes = getattr(pl, "playlists", None) or []
        if not variantes:
            return url_master  # ya es una media playlist

        def _res_de(v):
            try:
                info = v.stream_info
                if info and info.resolution:
                    return int(info.resolution[1])  # alto en píxeles
                return int(getattr(info, "bandwidth", 0) or 0)
            except Exception:
                return 0
        ordenadas = sorted(variantes, key=_res_de)
        elegida = ordenadas[0]  # por defecto, la menor (audio igual, menos ancho de banda)
        pref = (preferencia or "auto").strip().lower()
        if pref not in ("", "auto"):
            try:
                objetivo = int(pref)
                candidatas = [v for v in ordenadas if _res_de(v) <= objetivo]
                if candidatas:
                    elegida = candidatas[-1]
            except ValueError:
                pass
        try:
            return elegida.absolute_uri or url_master
        except Exception:
            return url_master

    # ------------------------------------------------------------------
    # Subtítulos (captions)
    # ------------------------------------------------------------------
    def listar_subtitulos(self, account_id: int, id_or_uuid: str) -> List[Dict[str, Any]]:
        """Lista los subtítulos de un vídeo. Devuelve [{idioma, etiqueta, path, automatico}]."""
        base_url = self.instancia_de(account_id) or ""

        def _fn(cliente):
            datos = cliente.get(f"videos/{id_or_uuid}/captions", auth=True)
            res = []
            filas = datos.get("data") if isinstance(datos, dict) else datos
            for c in (filas or []):
                idioma = (c.get("language") or {}) if isinstance(c, dict) else {}
                path = c.get("captionPath") or c.get("fileUrl") or ""
                res.append({"idioma": idioma.get("id") or "", "etiqueta": idioma.get("label") or "",
                            "path": self._url_absoluta(base_url, path),
                            "automatico": bool(c.get("automaticallyGenerated"))})
            return res
        return self._ejecutar(account_id, _fn)

    def descargar_subtitulo(self, account_id: int, url_o_path: str) -> str:
        """Descarga un subtítulo (WebVTT) y devuelve su TEXTO plano (sin marcas de tiempo), listo
        para leerlo con el lector de pantalla."""
        def _fn(cliente):
            resp = cliente.solicitar("GET", url_o_path, auth=True, espera_json=False)
            return getattr(resp, "text", "") or ""

        vtt = self._ejecutar(account_id, _fn)
        return self._vtt_a_texto(vtt)

    @staticmethod
    def _vtt_a_texto(vtt: str) -> str:
        """Convierte WebVTT/SRT a texto plano: descarta cabeceras, números de cue y marcas de tiempo,
        y colapsa el texto de los subtítulos evitando líneas repetidas consecutivas."""
        lineas = []
        anterior = None
        for cruda in (vtt or "").splitlines():
            l = cruda.strip()
            if not l or l.upper().startswith("WEBVTT") or l.isdigit():
                continue
            if "-->" in l:
                continue
            if l == anterior:
                continue
            lineas.append(l)
            anterior = l
        return "\n".join(lineas)

    # ------------------------------------------------------------------
    # Registro de vista / posición remota
    # ------------------------------------------------------------------
    def registrar_vista(self, account_id: int, id_or_uuid: str, current_time: float = 0) -> bool:
        """Registra una vista/posición del vídeo (`POST /videos/{id}/views`), que alimenta el
        historial remoto y el 'continuar viendo'. Best-effort."""
        def _fn(cliente):
            cliente.post(f"videos/{id_or_uuid}/views",
                         json_body={"currentTime": max(0, int(current_time or 0))}, auth=True)
            return True
        try:
            return bool(self._ejecutar(account_id, _fn))
        except Exception:
            return False
