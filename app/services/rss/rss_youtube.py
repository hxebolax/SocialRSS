# -*- coding: utf-8 -*-
"""Suscripciones de YouTube vía yt-dlp (mixin de RSSService).

- Lee TUS suscripciones de YouTube usando las cookies del navegador (sin pedir usuario/contraseña).
- Suscribe un canal como feed de YouTube y descarga TODO su catálogo de vídeos (sin límite), que se
  guardan como artículos; las novedades posteriores entran solas por el feed RSS del canal.
"""
import logging
from datetime import datetime, timedelta

from app.core.database import db

log = logging.getLogger(__name__)

# Navegadores admitidos por yt-dlp para extraer cookies.
NAVEGADORES_COOKIES = ["chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"]


class _YoutubeMixin:
    def youtube_listar_suscripciones(self, cookies_browser: str) -> list:
        """Devuelve la lista de canales a los que estás suscrito en YouTube, leyendo las cookies del
        navegador indicado. Cada elemento: {channel_id, url, title}. Requiere sesión iniciada en
        YouTube en ese navegador."""
        import yt_dlp
        opciones = {
            "quiet": True, "no_warnings": True, "extract_flat": True,
            "skip_download": True, "cookiesfrombrowser": (cookies_browser,),
        }
        canales = []
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/channels", download=False)
        for e in (info.get("entries") or []):
            cid = e.get("channel_id") or e.get("id") or ""
            url = e.get("url") or (f"https://www.youtube.com/channel/{cid}" if cid.startswith("UC") else "")
            titulo = e.get("title") or e.get("channel") or cid
            if cid or url:
                canales.append({"channel_id": cid, "url": url, "title": titulo})
        # Quitar duplicados por channel_id/url conservando el orden.
        vistos, salida = set(), []
        for c in canales:
            clave = c["channel_id"] or c["url"]
            if clave and clave not in vistos:
                vistos.add(clave)
                salida.append(c)
        return salida

    def _resolver_channel_id(self, canal: dict, cookies_browser: str) -> str:
        """Obtiene el channel_id (UC...) de un canal, resolviéndolo del HTML si solo hay URL."""
        cid = (canal.get("channel_id") or "").strip()
        if cid.startswith("UC"):
            return cid
        # Reutiliza el resolutor de feed de YouTube ya existente (_resolver_feed_youtube),
        # que extrae el channel_id del HTML del canal.
        try:
            feed_url = self._resolver_feed_youtube(canal.get("url") or cid)
            import re
            m = re.search(r"channel_id=(UC[\w-]+)", feed_url)
            if m:
                return m.group(1)
        except Exception as e:
            log.debug(f"No se pudo resolver channel_id de {canal}: {e}")
        return ""

    def youtube_suscribir_canal_completo(self, account_id: int, canal: dict, category_id=None,
                                         cookies_browser: str = "chrome", progreso=None) -> dict:
        """Suscribe un canal de YouTube como feed y descarga TODO su catálogo de vídeos (sin límite).
        progreso(actual, total) opcional (hilo de UI). Devuelve {feed_id, titulo, videos}."""
        import yt_dlp
        channel_id = self._resolver_channel_id(canal, cookies_browser)
        if not channel_id:
            raise ValueError("No se pudo identificar el canal de YouTube.")
        titulo = canal.get("title") or channel_id
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

        # 1) Crear (o reutilizar) el feed de tipo youtube, para que las NOVEDADES entren solas.
        existe = self.buscar_feed_por_url(account_id, feed_url)
        if existe:
            feed_id = existe[0]
            if category_id is not None:
                db.execute("UPDATE rss_feeds SET category_id = ? WHERE id = ?", (category_id, feed_id))
        else:
            feed_id = self.agregar_feed(account_id, feed_url, titulo, category_id, feed_kind="youtube")

        # 2) Enumerar TODOS los vídeos del canal (sin límite) con yt-dlp y guardarlos como artículos.
        url_videos = f"https://www.youtube.com/channel/{channel_id}/videos"
        opciones = {
            "quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "skip_download": True, "cookiesfrombrowser": (cookies_browser,),
        }
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url_videos, download=False)
        entradas = info.get("entries") or []
        total = len(entradas)
        base = datetime.now()
        guardados = 0
        # Candidatos primero (CPU fuera de la transacción) y duplicados en UN chequeo por
        # lote; después las escrituras van POR LOTES y en transacción (auditoría RSS-04:
        # antes se insertaba vídeo a vídeo, con un commit por cada uno, y SIN account_id,
        # con lo que el catálogo quedaba invisible para los buffers, que filtran por cuenta).
        candidatos = []
        for i, e in enumerate(entradas):
            vid = e.get("id") or ""
            if not vid:
                continue
            link = e.get("url") or f"https://www.youtube.com/watch?v={vid}"
            vtitulo = e.get("title") or vid
            # Orden: los más nuevos primero (yt-dlp los da así); se reparte la fecha por índice
            # para conservar ese orden si el extractor no trae la fecha real.
            published = (base - timedelta(minutes=i)).isoformat()
            article_id = self._generar_article_id(feed_id, vid, link, vtitulo)
            candidatos.append((article_id, vid, link, vtitulo, published, i))
        existentes = set()
        ids = [c[0] for c in candidatos]
        for j in range(0, len(ids), 500):
            lote = ids[j:j + 500]
            ph = ",".join("?" for _ in lote)
            existentes.update(r[0] for r in db.query(
                f"SELECT id FROM rss_articles WHERE id IN ({ph})", tuple(lote)))
        ahora = datetime.now().isoformat()
        LOTE = 500
        pendientes = [c for c in candidatos if c[0] not in existentes]
        for j in range(0, len(pendientes), LOTE):
            with db.transaccion():
                for article_id, vid, link, vtitulo, published, i in pendientes[j:j + LOTE]:
                    try:
                        db.execute(
                            "INSERT INTO rss_articles (id, feed_id, account_id, guid, link, title, "
                            "content, author, published_at, read, favorite, has_media, media_url, "
                            "media_type, read_later, first_seen_at, last_seen_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 0, 0, 1, ?, 'video/youtube', 0, ?, ?)",
                            (article_id, feed_id, account_id, vid, link, vtitulo, titulo,
                             published, link, ahora, ahora))
                        try:
                            db.execute(
                                "INSERT INTO rss_articles_fts (article_db_id, title, content, author) "
                                "VALUES (?, ?, '', ?)", (article_id, vtitulo, titulo))
                        except Exception:
                            pass
                        guardados += 1
                    except Exception as ex:
                        log.debug(f"No se pudo guardar el vídeo {vid}: {ex}")
            if progreso and total:
                import wx
                wx.CallAfter(progreso, min(j + LOTE, len(pendientes)), max(len(pendientes), 1))
        log.info(f"YouTube: canal '{titulo}' suscrito con {guardados}/{total} vídeos.")
        return {"feed_id": feed_id, "titulo": titulo, "videos": guardados}
