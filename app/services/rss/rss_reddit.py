# -*- coding: utf-8 -*-
"""Hilos de Reddit como artículo (publicación + TODOS los comentarios), como BlindRSS.

Desde 2024 Reddit no deja leer hilos sin iniciar sesión: el JSON responde 403 y el RSS de
comentarios, 429, aunque se imite a un navegador. BlindRSS lo resuelve con la sesión de Reddit
que el usuario ya tiene iniciada en Firefox, y aquí se hace igual:

1. Con la sesión del navegador (Preferencias, RSS; Firefox por defecto): el JSON del hilo y,
   después, todos los "cargar más comentarios" (/api/morechildren), en lotes de 100.
2. Si no hay sesión: el RSS de comentarios del hilo (a veces responde) como respaldo.
(La cuenta de Reddit de la aplicación, si la hay, se usa antes: rss_foros._reddit.)

Las cookies se leen solo de reddit.com, se guardan unos minutos en memoria y solo viajan a
reddit.com, en una sesión HTTP aparte (rss_suscripciones.cookies_sesion).

Idea tomada de BlindRSS (core/article_extractor.py: _download_reddit_thread_html,
_reddit_json_to_thread_html, _reddit_rss_thread_html; Copyright (c) serrebidev and
contributors, licencia MIT), reescrita.
"""
import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
_TIEMPO = 20
MAX_PETICIONES_MAS = 20
LOTE_MAS = 100


def _sesion():
    """Sesión HTTP propia con las cookies de Reddit del navegador (o None si no hay)."""
    from app.core.config_manager import config
    if not config.get("rss", "reddit_browser_session", True):
        return None
    from .rss_suscripciones import cookies_sesion
    tarro = cookies_sesion("reddit.com")
    if not tarro:
        return None
    import requests
    s = requests.Session()
    s.cookies = tarro
    s.headers.update({"User-Agent": _UA, "Accept": "application/json, text/json;q=0.9, */*;q=0.5"})
    return s


def _json(sesion, url: str, params: dict):
    r = sesion.get(url, params=params, timeout=_TIEMPO, allow_redirects=True)
    if r.status_code != 200 or "json" not in (r.headers.get("Content-Type") or "").lower():
        raise ValueError(f"Reddit respondió {r.status_code}")
    return r.json()


def _ordenar(post_id: str, nodos: Dict[str, dict], orden: List[str]) -> List[dict]:
    """Recorre el árbol (padre -> hijos en el orden de Reddit) y da la profundidad real."""
    hijos: Dict[str, List[str]] = {}
    for nombre in orden:
        hijos.setdefault(nodos[nombre].get("parent_id") or f"t3_{post_id}", []).append(nombre)
    salida = []

    def _bajar(padre, prof):
        for nombre in hijos.get(padre, []):
            c = dict(nodos[nombre], depth=prof)
            salida.append(c)
            _bajar(nombre, prof + 1)
    _bajar(f"t3_{post_id}", 0)
    return salida


def hilo_json(post_id: str, sesion, maximo: int = 500) -> Optional[dict]:
    """{'post': {...}, 'comments': [{author, body_html, score, depth, parent_author}]}."""
    datos = _json(sesion, f"https://www.reddit.com/comments/{post_id}.json",
                  {"raw_json": "1", "limit": "500", "depth": "10", "sort": "confidence"})
    if not isinstance(datos, list) or len(datos) < 2:
        return None
    post = ((datos[0].get("data") or {}).get("children") or [{}])[0].get("data") or {}
    nodos: Dict[str, dict] = {}
    orden: List[str] = []
    pendientes: List[str] = []

    def _recoger(listado):
        for hijo in ((listado or {}).get("data") or {}).get("children") or []:
            kind, d = hijo.get("kind"), hijo.get("data") or {}
            if kind == "t1" and d.get("name") and d["name"] not in nodos:
                nodos[d["name"]] = {"author": d.get("author") or "[deleted]",
                                    "body_html": d.get("body_html") or "", "score": d.get("score"),
                                    "parent_id": d.get("parent_id")}
                orden.append(d["name"])
                if isinstance(d.get("replies"), dict):
                    _recoger(d["replies"])
            elif kind == "more":
                pendientes.extend(x for x in d.get("children") or [] if x and x != "_")
    _recoger(datos[1])
    peticiones = 0
    while pendientes and peticiones < MAX_PETICIONES_MAS and len(orden) < maximo:
        lote, pendientes = pendientes[:LOTE_MAS], pendientes[LOTE_MAS:]
        peticiones += 1
        try:
            mas = _json(sesion, "https://www.reddit.com/api/morechildren.json",
                        {"api_type": "json", "link_id": f"t3_{post_id}", "children": ",".join(lote),
                         "raw_json": "1", "sort": "confidence"})
        except Exception as e:
            log.debug(f"Reddit: no se pudieron traer más comentarios: {e}")
            break
        for cosa in ((mas.get("json") or {}).get("data") or {}).get("things") or []:
            kind, d = cosa.get("kind"), cosa.get("data") or {}
            if kind == "t1" and d.get("name") and d["name"] not in nodos:
                nodos[d["name"]] = {"author": d.get("author") or "[deleted]",
                                    "body_html": d.get("body_html") or "", "score": d.get("score"),
                                    "parent_id": d.get("parent_id")}
                orden.append(d["name"])
            elif kind == "more":
                pendientes.extend(x for x in d.get("children") or [] if x and x != "_")
    comentarios = _ordenar(post_id, nodos, orden)
    for c in comentarios:
        padre = nodos.get(c.get("parent_id") or "")
        c["parent_author"] = padre["author"] if padre else ""
    return {"post": {"title": post.get("title") or "", "author": post.get("author") or "",
                     "subreddit": post.get("subreddit") or "", "score": post.get("score"),
                     "selftext_html": post.get("selftext_html") or "", "url": post.get("url") or "",
                     "is_self": bool(post.get("is_self")),
                     "num_comments": int(post.get("num_comments") or 0)},
            "comments": comentarios[:maximo]}


def hilo_rss(post_id: str, sesion=None) -> Optional[dict]:
    """Respaldo: el RSS de comentarios del hilo (sin árbol: todos al mismo nivel)."""
    import feedparser
    import requests
    cliente = sesion or requests
    r = cliente.get(f"https://www.reddit.com/comments/{post_id}/.rss", params={"limit": "500"},
                    headers={"User-Agent": _UA, "Accept": "application/atom+xml, */*;q=0.5"},
                    timeout=_TIEMPO, allow_redirects=True)
    if r.status_code != 200:
        return None
    entradas = feedparser.parse(r.content).entries or []
    if not entradas:
        return None

    def _contenido(e):
        valores = e.get("content") or []
        return (valores[0].get("value") if valores else "") or e.get("summary") or ""
    primero, resto = entradas[0], entradas[1:]
    return {"post": {"title": primero.get("title") or "", "author": (primero.get("author") or "").replace("/u/", ""),
                     "subreddit": "", "score": None, "selftext_html": _contenido(primero), "url": "",
                     "is_self": True, "num_comments": len(resto)},
            "comments": [{"author": (e.get("author") or "").replace("/u/", ""), "body_html": _contenido(e),
                          "score": None, "depth": 0, "parent_author": ""} for e in resto]}


def hilo_con_sesion(post_id: str) -> Optional[dict]:
    """Hilo con la sesión del navegador y, si no hay o falla, con el RSS de comentarios."""
    sesion = _sesion()
    try:
        if sesion is not None:
            try:
                return hilo_json(post_id, sesion)
            except Exception as e:
                log.debug(f"Reddit con la sesión del navegador falló ({e}); se prueba el RSS.")
        try:
            return hilo_rss(post_id, sesion)
        except Exception as e:
            log.debug(f"Reddit: el RSS de comentarios tampoco respondió: {e}")
            return None
    finally:
        if sesion is not None:
            sesion.close()

