# -*- coding: utf-8 -*-
"""Recuperar episodios antiguos de un podcast desde el archivo de Internet (Wayback Machine).

Muchos podcasts solo dejan en su feed los últimos episodios. El archivo de Internet (servicio
público) guarda copias del feed de distintas fechas: se pide su índice (CDX), se descargan unas
cuantas copias repartidas en el tiempo, se juntan todos los episodios que aparecieron alguna vez
y se añaden los que no tenemos. Entran como LEÍDOS (son el catálogo antiguo, no novedades) y sin
avisos. Se identifican igual que en el sondeo normal (mismo article_id por GUID o enlace), así
que un episodio que ya estaba no se duplica.

Idea tomada de BlindRSS (core/podcast_archive.py; Copyright (c) serrebidev and contributors,
licencia MIT), reescrita.
"""
import calendar
import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlencode

log = logging.getLogger(__name__)

CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK = "https://web.archive.org/web"
MAX_CAPTURAS = 40
_TIEMPO = 45
_MAX_BYTES = 15 * 1024 * 1024


def _pedir(url: str, ctx=None):
    """GET al archivo con reintentos ante 429/5xx (va lento a menudo)."""
    from app.core.net import http_session
    ultimo = None
    for intento in range(3):
        if ctx is not None and ctx.is_cancelled():
            raise RuntimeError("Cancelado")
        try:
            r = http_session().get(url, timeout=(15, _TIEMPO),
                                   headers={"User-Agent": "SocialRSS (recuperar episodios archivados)"})
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
            ultimo = RuntimeError(f"El archivo de Internet respondió {r.status_code}")
        except Exception as e:
            ultimo = e
        time.sleep(2 ** intento)
    raise ultimo


def elegir_capturas(filas: List[List[str]], maximo: int = MAX_CAPTURAS) -> List[tuple]:
    """De las filas del CDX ([timestamp, original], la primera es la cabecera) elige hasta
    `maximo` capturas REPARTIDAS en el tiempo (siempre la más antigua y la más reciente)."""
    datos = sorted({(f[0], f[1]) for f in filas[1:] if len(f) >= 2 and f[0].isdigit()})
    if len(datos) <= maximo:
        return datos
    paso = (len(datos) - 1) / (maximo - 1)
    return sorted({datos[round(i * paso)] for i in range(maximo)})


def listar_capturas(feed_url: str, maximo: int = MAX_CAPTURAS, ctx=None) -> List[tuple]:
    params = urlencode([("url", feed_url), ("output", "json"), ("fl", "timestamp,original"),
                        ("filter", "statuscode:200"), ("collapse", "digest"), ("limit", "5000")])
    r = _pedir(f"{CDX}?{params}", ctx)
    r.raise_for_status()
    try:
        filas = r.json()
    except ValueError:
        return []
    return elegir_capturas(filas if isinstance(filas, list) else [], maximo)


class _ArchivoMixin:
    def recuperar_episodios_archivados(self, feed_id: int, maximo: int = MAX_CAPTURAS,
                                       ctx=None) -> Dict[str, int]:
        """SÍNCRONO (hilo de fondo). Devuelve {'capturas', 'leidas', 'episodios', 'nuevos'}."""
        import feedparser
        from app.core.database import db
        from .rss_parseo import _construir_podcast_json, _duracion_a_segundos, _normalizar_entrada
        fila = db.query_one("SELECT url, account_id FROM rss_feeds WHERE id = ?", (feed_id,))
        if not fila:
            raise ValueError(_("El feed ya no existe."))
        url, account_id = fila
        capturas = listar_capturas(url, maximo, ctx)
        encontrados: Dict[str, dict] = {}
        leidas = 0
        for n, (ts, original) in enumerate(capturas):
            if ctx is not None:
                if ctx.is_cancelled():
                    break
                ctx.report_progress(int(n * 100 / max(len(capturas), 1)),
                                    _("Leyendo la copia {0} de {1} del archivo...").format(n + 1, len(capturas)))
            try:
                r = _pedir(f"{WAYBACK}/{ts}id_/{original}", ctx)
                if r.status_code != 200 or len(r.content) > _MAX_BYTES:
                    continue
                parsed = feedparser.parse(r.content)
            except Exception as e:
                log.debug(f"Copia archivada ilegible {ts} de {original}: {e}")
                continue
            leidas += 1
            for entrada in parsed.entries or []:
                e = _normalizar_entrada(entrada)
                media = next(((x.get("href") or x.get("url")) for x in e.get("enclosures") or []
                              if (x.get("href") or x.get("url"))), "")
                if not media:
                    continue          # solo episodios con audio o vídeo
                link = e.get("link", "")
                titulo = e.get("title") or _("Sin título")
                aid = self._generar_article_id(feed_id, e.get("id") or link, link, titulo)
                encontrados[aid] = {"e": e, "media": media, "ts": ts}
        episodios = len(encontrados)
        # Quitar los que ya tenemos: por id y, por si el editor cambió el GUID, por el audio.
        existentes = {r[0] for r in db.query("SELECT id FROM rss_articles WHERE feed_id = ?", (feed_id,))}
        audios = {(r[0] or "").split("?")[0] for r in db.query(
            "SELECT enclosure_url FROM rss_articles WHERE feed_id = ?", (feed_id,))}
        from .rss_borrados import borrados_entre
        borrados = borrados_entre(list(encontrados))
        nuevos = [(aid, d) for aid, d in encontrados.items()
                  if aid not in existentes and aid not in borrados
                  and d["media"].split("?")[0] not in audios]
        ahora = datetime.now().isoformat()
        insertados = 0
        with db.transaccion():
            for aid, d in nuevos:
                e = d["e"]
                publicado = None
                for clave in ("published_parsed", "updated_parsed"):
                    if e.get(clave):
                        publicado = datetime.fromtimestamp(calendar.timegm(e[clave])).isoformat()
                        break
                if not publicado:
                    publicado = datetime.strptime(d["ts"][:8], "%Y%m%d").isoformat()
                enc = next((x for x in e.get("enclosures") or [] if (x.get("href") or x.get("url"))), {})
                tipo = enc.get("type", "") or ""
                try:
                    largo = int(enc.get("length") or 0)
                except (TypeError, ValueError):
                    largo = 0
                es_audio = "audio" in tipo or d["media"].lower().split("?")[0].endswith(
                    (".mp3", ".m4a", ".ogg", ".opus", ".wav"))
                titulo = e.get("title") or _("Sin título")
                contenido = e.get("contenido_resuelto") or ""
                link = e.get("link", "")
                hash_c = hashlib.sha1("\x1f".join((titulo, contenido, e.get("author", ""), link,
                                                   d["media"])).encode("utf-8", "replace")).hexdigest()
                try:
                    db.execute(
                        "INSERT INTO rss_articles (id, feed_id, account_id, guid, link, title, content, "
                        "author, published_at, read, favorite, has_media, media_url, media_type, "
                        "urls_json, read_later, enclosure_url, enclosure_type, enclosure_length, "
                        "is_podcast, first_seen_at, last_seen_at, source_updated_at, content_hash, "
                        "duration_seconds, podcast_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 1, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (aid, feed_id, account_id, e.get("id") or link, link, titulo, contenido,
                         e.get("author", ""), publicado, d["media"], tipo,
                         json.dumps([u for u in (link, d["media"]) if u]),
                         d["media"] if es_audio else None, tipo if es_audio else None, largo,
                         1 if es_audio else 0, ahora, ahora, publicado, hash_c,
                         _duracion_a_segundos(e.get("itunes_duration")),
                         (_construir_podcast_json(e) if es_audio else "") or None))
                    db.execute("INSERT INTO rss_articles_fts (article_db_id, title, content, author) "
                               "VALUES (?, ?, ?, ?)", (aid, titulo, contenido, e.get("author", "")))
                    insertados += 1
                except Exception as err:
                    log.debug(f"No se pudo añadir el episodio archivado {aid}: {err}")
        return {"capturas": len(capturas), "leidas": leidas, "episodios": episodios,
                "nuevos": insertados}
