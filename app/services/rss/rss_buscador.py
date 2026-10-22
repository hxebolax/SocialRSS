# -*- coding: utf-8 -*-
"""Buscador de feeds y podcasts en directorios públicos (mixin de RSSService).

Consulta a la vez varios directorios con API pública y junta los resultados sin duplicados:
- Podcasts: Apple Podcasts (iTunes Search), fyyd y gPodder.
- Feeds: Feedly y NewsBlur.
- Comunidades: Lemmy (lemmy.world) y la etiqueta en Mastodon (mastodon.social).
Idea tomada de BlindRSS (serrebidev, MIT, gui/dialogs.py FeedSearchDialog), reescrita aquí.
Comprobado en vivo (2026-09): Reddit ya no deja buscar sin iniciar sesión (403), así que no se
usa; sus direcciones sí se convierten a feed al pegarlas (rss_descubrir).
"""
import concurrent.futures
import logging
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

log = logging.getLogger(__name__)

_CABECERAS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 "
                            "Firefox/143.0", "Accept": "application/json"}
_TIEMPO = 15

# Tipos de búsqueda: qué fuentes consulta cada uno.
TIPOS = {"todo": ("itunes", "fyyd", "gpodder", "feedly", "newsblur", "lemmy", "mastodon"),
         "podcasts": ("itunes", "fyyd", "gpodder"),
         "feeds": ("feedly", "newsblur", "lemmy", "mastodon")}

NOMBRES_FUENTE = {"itunes": "Apple Podcasts", "fyyd": "fyyd", "gpodder": "gPodder",
                  "feedly": "Feedly", "newsblur": "NewsBlur", "lemmy": "Lemmy",
                  "mastodon": "Mastodon"}


def _get_json(url: str):
    from app.core.net import http_session
    r = http_session().get(url, headers=_CABECERAS, timeout=_TIEMPO)
    r.raise_for_status()
    return r.json()


def _r(titulo, url, fuente, tipo, descripcion="", suscriptores=0, autor=""):
    return {"titulo": (titulo or "").strip(), "url": (url or "").strip(), "fuente": fuente,
            "tipo": tipo, "descripcion": re.sub(r"\s+", " ", descripcion or "").strip()[:300],
            "suscriptores": int(suscriptores or 0), "autor": (autor or "").strip()}


def _itunes(q):
    d = _get_json(f"https://itunes.apple.com/search?media=podcast&limit=25&term={quote(q)}")
    return [_r(x.get("collectionName"), x.get("feedUrl"), "itunes", "podcast",
               x.get("primaryGenreName", ""), 0, x.get("artistName"))
            for x in d.get("results", []) if x.get("feedUrl")]


def _fyyd(q):
    d = _get_json(f"https://api.fyyd.de/0.2/search/podcast?count=25&title={quote(q)}")
    return [_r(x.get("title"), x.get("xmlURL"), "fyyd", "podcast", x.get("description", ""),
               0, x.get("author", "")) for x in (d.get("data") or []) if x.get("xmlURL")]


def _gpodder(q):
    d = _get_json(f"https://gpodder.net/search.json?q={quote(q)}")
    return [_r(x.get("title"), x.get("url"), "gpodder", "podcast", x.get("description", ""),
               x.get("subscribers", 0), x.get("author", "")) for x in (d or []) if x.get("url")]


def _feedly(q):
    d = _get_json(f"https://cloud.feedly.com/v3/search/feeds?count=25&query={quote(q)}")
    salida = []
    for x in d.get("results", []):
        fid = x.get("feedId") or ""
        url = fid[5:] if fid.startswith("feed/") else ""
        if url:
            salida.append(_r(x.get("title"), url, "feedly", "feed", x.get("description", ""),
                             x.get("subscribers", 0)))
    return salida


def _newsblur(q):
    d = _get_json(f"https://www.newsblur.com/rss_feeds/feed_autocomplete?format=full&v=2&term={quote(q)}")
    return [_r(x.get("feed_title"), x.get("feed_address"), "newsblur", "feed",
               x.get("feed_link", ""), x.get("num_subscribers", 0))
            for x in (d.get("feeds") or []) if x.get("feed_address")]


def _lemmy(q):
    d = _get_json(f"https://lemmy.world/api/v3/search?type_=Communities&limit=15&q={quote(q)}")
    salida = []
    for x in d.get("communities", []):
        c = x.get("community") or {}
        actor = c.get("actor_id") or ""
        m = re.match(r"(https?://[^/]+)/c/([^/]+)", actor)
        if m:
            salida.append(_r(c.get("title") or c.get("name"), f"{m.group(1)}/feeds/c/{m.group(2)}.xml",
                             "lemmy", "comunidad", c.get("description", ""),
                             (x.get("counts") or {}).get("subscribers", 0)))
    return salida


def _mastodon(q):
    """La etiqueta en mastodon.social, si la búsqueda es una sola palabra (sin red: se valida
    al suscribirse)."""
    palabra = q.strip().lstrip("#")
    if not palabra or " " in palabra:
        return []
    return [_r(_("Etiqueta #{0} en Mastodon").format(palabra),
               f"https://mastodon.social/tags/{quote(palabra)}.rss", "mastodon", "comunidad",
               _("Publicaciones públicas con la etiqueta, desde mastodon.social."))]


_FUENTES = {"itunes": _itunes, "fyyd": _fyyd, "gpodder": _gpodder, "feedly": _feedly,
            "newsblur": _newsblur, "lemmy": _lemmy, "mastodon": _mastodon}


def buscar_en_directorios(q: str, tipo: str = "todo",
                          al_llegar: Optional[Callable[[List[Dict[str, Any]]], None]] = None
                          ) -> Dict[str, Any]:
    """Busca en las fuentes del tipo pedido EN PARALELO. Devuelve {resultados, errores} con los
    resultados sin duplicados (por dirección). `al_llegar(lote)` se llama cada vez que una
    fuente responde (para ir llenando la lista), desde el hilo de la búsqueda."""
    q = (q or "").strip()
    resultados, vistos, errores = [], set(), {}
    if not q:
        return {"resultados": resultados, "errores": errores}
    fuentes = TIPOS.get(tipo, TIPOS["todo"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(fuentes)) as ex:
        futuros = {ex.submit(_FUENTES[f], q): f for f in fuentes}
        for fut in concurrent.futures.as_completed(futuros, timeout=_TIEMPO * 3):
            fuente = futuros[fut]
            try:
                lote = []
                for r in fut.result() or []:
                    clave = r["url"].lower().rstrip("/")
                    if r["url"] and clave not in vistos:
                        vistos.add(clave)
                        lote.append(r)
                resultados.extend(lote)
                if lote and al_llegar:
                    al_llegar(lote)
            except Exception as e:
                errores[fuente] = str(e)
                log.debug(f"Buscador de feeds: {fuente} falló: {e}")
    return {"resultados": resultados, "errores": errores}


class _BuscadorMixin:
    def buscar_feeds_async(self, q: str, tipo: str, al_llegar=None, callback=None):
        from app.core.task_manager import task_manager

        def _bg(ctx):
            return buscar_en_directorios(q, tipo, al_llegar)
        task_manager.start_task(_bg, callback=callback, pass_context=True)
