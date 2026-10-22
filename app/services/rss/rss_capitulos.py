# -*- coding: utf-8 -*-
"""Capítulos de un episodio para el reproductor de podcasts: [(segundo de inicio, título)].

Fuentes, por orden:
  1. Podcast 2.0: el JSON de capítulos que anuncia el feed (<podcast:chapters>).
  2. Los capítulos ID3 (CHAP) del propio MP3: del archivo descargado si lo hay o, si no, del
     principio del archivo remoto (petición de rango).
  3. YouTube: los capítulos del vídeo (yt-dlp, sin descargar nada).
Todo SÍNCRONO: se llama desde un hilo de fondo.
"""
import logging
import re
from typing import List, Tuple

log = logging.getLogger(__name__)
Capitulos = List[Tuple[float, str]]


def _de_json_podcast20(url: str) -> Capitulos:
    from app.core.net import http_session
    r = http_session().get(url, timeout=20, headers={"User-Agent": "SocialRSS"})
    r.raise_for_status()
    datos = r.json()
    salida = []
    for c in (datos.get("chapters") or []) if isinstance(datos, dict) else []:
        if c.get("toc") is False:          # capítulos "ocultos" (solo imagen o enlace)
            continue
        try:
            inicio = float(c.get("startTime") or 0)
        except (TypeError, ValueError):
            continue
        salida.append((inicio, (c.get("title") or "").strip() or _("Capítulo")))
    return sorted(salida)


def _de_archivo_local(ruta: str) -> Capitulos:
    from .rss_id3 import MAX_ETIQUETA, analizar_id3
    with open(ruta, "rb") as f:
        datos = f.read(MAX_ETIQUETA)
    return analizar_id3(datos).get("capitulos") or []


def capitulos_youtube(video_id: str) -> Capitulos:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False,
                                process=False) or {}
    return [(float(c.get("start_time") or 0), c.get("title") or _("Capítulo"))
            for c in info.get("chapters") or []]


def capitulos_de(article_id: str = "", ruta_o_url: str = "") -> Capitulos:
    """Capítulos del episodio (por su artículo de RSS) o, sin artículo, de un MP3 local."""
    fuentes = []
    enclosure, link, local = "", "", ""
    if article_id:
        try:
            from app.core.database import db
            fila = db.query_one("SELECT COALESCE(enclosure_url,''), COALESCE(link,''), "
                                "COALESCE(download_path,'') FROM rss_articles WHERE id = ?", (article_id,))
            if fila:
                enclosure, link, local = fila
            from .rss_service import rss_service
            meta = rss_service.obtener_metadatos_podcast(article_id) or {}
            url_json = ((meta.get("chapters") or {}).get("url") if isinstance(meta.get("chapters"), dict)
                        else "")
            if url_json:
                fuentes.append(lambda: _de_json_podcast20(url_json))
        except Exception as e:
            log.debug(f"Capítulos: no se pudo leer el artículo {article_id}: {e}")
    import os
    if local and os.path.isfile(local):
        fuentes.append(lambda: _de_archivo_local(local))
    elif ruta_o_url and os.path.isfile(ruta_o_url):
        fuentes.append(lambda: _de_archivo_local(ruta_o_url))
    if enclosure or (ruta_o_url or "").startswith("http"):
        from .rss_id3 import leer_id3_remoto
        remoto = enclosure or ruta_o_url
        if re.search(r"\.mp3(\?|$)", remoto, re.I) or enclosure:
            fuentes.append(lambda: leer_id3_remoto(remoto).get("capitulos") or [])
    from .rss_foros import tipo_de_enlace
    tipo = tipo_de_enlace(link)
    if tipo and tipo[0] == "youtube":
        fuentes.append(lambda: capitulos_youtube(tipo[1]))
    for fuente in fuentes:
        try:
            caps = fuente()
        except Exception as e:
            log.debug(f"Capítulos: una fuente falló: {e}")
            continue
        if caps:
            return caps
    return []
