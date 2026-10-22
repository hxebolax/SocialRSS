# -*- coding: utf-8 -*-
"""Artículo completo "por sitio": hilos de foros y vídeos de YouTube.

Cuando el enlace de un artículo es un hilo de Lemmy, Discourse o Reddit, el artículo completo es
la publicación CON todos sus comentarios (autor, puntos y a quién responde cada uno), no solo el
primer mensaje. Cuando es un vídeo de YouTube, es la descripción, los capítulos con su minuto y
la transcripción (subtítulos del autor o automáticos).

Cada comentario va como un ENCABEZADO (h3 los de primer nivel, h4 sus respuestas...), así que
en el lector se salta de uno a otro con H o con el número del nivel.

- Lemmy: API pública v3 (post + comment/list paginado).
- Discourse: API pública (/t/<id>.json y /t/<id>/posts.json para el resto).
- Reddit: desde 2024 no deja leer hilos sin sesión. Se usa la cuenta de Reddit de la
  aplicación si hay una y, si no, como BlindRSS, la sesión de Reddit iniciada en el navegador
  (rss_reddit.py); el RSS de comentarios queda de último recurso.
- YouTube: yt-dlp (ya es dependencia), sin descargar el vídeo.

Idea tomada de BlindRSS (core/article_extractor.py, core/forum_sources.py y
core/youtube_fulltext.py; Copyright (c) serrebidev and contributors, licencia MIT), reescrita.
Todo es SÍNCRONO: se llama desde el hilo de fondo del artículo completo.
"""
import html
import logging
import re
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

MAX_COMENTARIOS = 500
_TIEMPO = 20
_CABECERAS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 "
                            "Firefox/143.0", "Accept": "application/json"}
_RE_YT_ID = re.compile(r"^[\w-]{11}$")


# ------------------------------------------------------------------ utilidades
def _get_json(url: str, params=None):
    from app.core.net import http_session
    r = http_session().get(url, params=params, headers=_CABECERAS, timeout=_TIEMPO)
    r.raise_for_status()
    if "json" not in (r.headers.get("Content-Type") or "").lower():
        raise ValueError("La respuesta no es JSON")
    return r.json()


def _md(texto: str) -> str:
    """Markdown (Reddit, Lemmy) a HTML; si falla, texto escapado por párrafos."""
    texto = texto or ""
    try:
        import markdown
        return markdown.markdown(texto, extensions=["tables", "sane_lists"])
    except Exception:
        return "".join(f"<p>{html.escape(p)}</p>" for p in re.split(r"\n\s*\n", texto) if p.strip())


def _nivel_encabezado(profundidad: int) -> int:
    """h3 para los comentarios de primer nivel, h4 sus respuestas... hasta h6."""
    return min(3 + max(0, profundidad), 6)


def _puntos(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return _("1 punto") if n == 1 else _("{0} puntos").format(n)


def _comentario_html(autor: str, cuerpo_html: str, profundidad: int, puntos=None,
                     responde_a: str = "", fecha: str = "") -> str:
    partes = [html.escape(autor or _("anónimo"))]
    if puntos is not None and _puntos(puntos):
        partes.append(_puntos(puntos))
    if responde_a:
        partes.append(_("responde a {0}").format(html.escape(responde_a)))
    if fecha:
        partes.append(html.escape(fecha))
    h = _nivel_encabezado(profundidad)
    return f"<h{h}>{', '.join(partes)}</h{h}>\n{cuerpo_html}\n"


def _bloque_comentarios(total: int, cuerpo: List[str], cortado: bool) -> str:
    if not cuerpo:
        return "<h2>" + _("Sin comentarios") + "</h2>"
    cab = _("Comentarios ({0})").format(total)
    pie = ("<p>" + _("(Se muestran los primeros {0} comentarios.)").format(len(cuerpo)) + "</p>"
           if cortado else "")
    return f"<h2>{cab}</h2>\n" + "".join(cuerpo) + pie


# ------------------------------------------------------------------ detección
def tipo_de_enlace(url: str):
    """('youtube', id) | ('lemmy', base, id) | ('discourse', base, id) | ('reddit', id) | None.
    Lemmy y Discourse se CONFIRMAN al pedir su API (una web cualquiera con /post/12 no lo es)."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    partes = [x for x in (p.path or "").split("/") if x]
    base = f"{p.scheme or 'https'}://{p.netloc}"
    if host in ("youtu.be",) and partes and _RE_YT_ID.match(partes[0]):
        return ("youtube", partes[0])
    if host.endswith("youtube.com"):
        v = (parse_qs(p.query or "").get("v") or [""])[0]
        if _RE_YT_ID.match(v):
            return ("youtube", v)
        if len(partes) >= 2 and partes[0] in ("shorts", "live", "embed") and _RE_YT_ID.match(partes[1]):
            return ("youtube", partes[1])
        return None
    if host.endswith("reddit.com") or host == "redd.it":
        if host == "redd.it" and partes:
            return ("reddit", partes[0])
        if "comments" in partes:
            i = partes.index("comments")
            if i + 1 < len(partes):
                return ("reddit", partes[i + 1])
        return None
    if len(partes) >= 2 and partes[0] == "post" and partes[1].isdigit():
        return ("lemmy", base, int(partes[1]))
    if len(partes) >= 2 and partes[0] == "t":
        # /t/<slug>/<id>[/<n>] o /t/<id>
        num = next((x for x in partes[1:3] if x.isdigit()), "")
        if num:
            return ("discourse", base, int(num))
    return None


# ------------------------------------------------------------------ Lemmy
def _lemmy(base: str, post_id: int) -> Optional[str]:
    d = _get_json(f"{base}/api/v3/post", {"id": post_id})
    pv = d.get("post_view") or {}
    post = pv.get("post") or {}
    if not post:
        return None
    autor = (pv.get("creator") or {}).get("name", "")
    comunidad = (pv.get("community") or {}).get("name", "")
    total = int((pv.get("counts") or {}).get("comments") or 0)
    cab = [f"<p>{html.escape(_('Publicado por {0} en {1}').format(autor, comunidad))}, "
           f"{_puntos((pv.get('counts') or {}).get('score'))}.</p>"]
    if post.get("url"):
        u = html.escape(post["url"])
        cab.append(f'<p>{_("Enlace")}: <a href="{u}">{u}</a></p>')
    cab.append(_md(post.get("body") or ""))
    # Comentarios: se piden por páginas y se ordenan en árbol (el orden de la API es por
    # puntuación dentro de cada nivel; la ruta "0.12.34" dice de quién cuelga cada uno).
    todos, vistos = [], set()
    for pagina in range(1, 11):
        lote = _get_json(f"{base}/api/v3/comment/list",
                         {"post_id": post_id, "max_depth": 8, "limit": 50, "sort": "Top",
                          "type_": "All", "page": pagina}).get("comments") or []
        # En modo árbol (max_depth) algunas versiones ignoran la página y repiten lo mismo.
        nuevos = [cv for cv in lote if (cv.get("comment") or {}).get("id") not in vistos]
        vistos.update((cv.get("comment") or {}).get("id") for cv in nuevos)
        todos += nuevos
        if not nuevos or len(lote) < 50 or len(todos) >= MAX_COMENTARIOS:
            break
    hijos, autores = {}, {}
    for cv in todos:
        c = cv.get("comment") or {}
        ruta = [x for x in (c.get("path") or "0").split(".") if x]
        padre = int(ruta[-2]) if len(ruta) >= 3 else 0
        autores[c.get("id")] = (cv.get("creator") or {}).get("name", "")
        hijos.setdefault(padre, []).append(cv)
    cuerpo = []

    def _recorrer(padre, prof):
        for cv in hijos.get(padre, []):
            if len(cuerpo) >= MAX_COMENTARIOS:
                return
            c = cv.get("comment") or {}
            texto = _("[comentario borrado]") if c.get("deleted") or c.get("removed") else _md(c.get("content"))
            cuerpo.append(_comentario_html(autores.get(c.get("id"), ""), texto, prof,
                                           (cv.get("counts") or {}).get("score"),
                                           autores.get(padre, "") if padre else ""))
            _recorrer(c.get("id"), prof + 1)
    _recorrer(0, 0)
    return "\n".join(cab) + _bloque_comentarios(max(total, len(cuerpo)), cuerpo,
                                               total > len(cuerpo))


# ------------------------------------------------------------------ Discourse
def _discourse(base: str, topic_id: int) -> Optional[str]:
    d = _get_json(f"{base}/t/{topic_id}.json")
    ps = d.get("post_stream") or {}
    posts = list(ps.get("posts") or [])
    if not posts:
        return None
    ids_cargados = {p.get("id") for p in posts}
    pendientes = [i for i in (ps.get("stream") or []) if i not in ids_cargados][:MAX_COMENTARIOS]
    for i in range(0, len(pendientes), 20):
        try:
            mas = _get_json(f"{base}/t/{topic_id}/posts.json",
                            [("post_ids[]", x) for x in pendientes[i:i + 20]])
            posts += (mas.get("post_stream") or {}).get("posts") or []
        except Exception as e:
            log.debug(f"Discourse: no se pudieron traer más mensajes de {base}/t/{topic_id}: {e}")
            break
    posts.sort(key=lambda p: p.get("post_number") or 0)
    primero, resto = posts[0], posts[1:]
    cab = [f"<p>{html.escape(_('Publicado por {0}').format(primero.get('username', '')))}.</p>",
           primero.get("cooked") or ""]
    cuerpo = []
    for p in resto[:MAX_COMENTARIOS]:
        a_quien = ((p.get("reply_to_user") or {}).get("username") or "") if p.get("reply_to_post_number") else ""
        cuerpo.append(_comentario_html(p.get("username", ""), p.get("cooked") or "", 0,
                                       None, a_quien))
    total = int(d.get("posts_count") or len(posts)) - 1
    return "\n".join(cab) + _bloque_comentarios(total, cuerpo, total > len(cuerpo))


# ------------------------------------------------------------------ Reddit
def _reddit_con_cuenta(post_id: str) -> Optional[dict]:
    """Con la cuenta de Reddit de la aplicación (API oficial), si hay alguna activa."""
    try:
        from app.core.database import db
        fila = db.query_one("SELECT id FROM accounts WHERE service_id = 'reddit' AND active = 1 "
                            "ORDER BY position LIMIT 1")
    except Exception:
        fila = None
    if not fila:
        return None
    from app.services.reddit.reddit_service import reddit_service
    datos = reddit_service._cargar_hilo_sync(fila[0], post_id)
    post = datos.get("post") or {}
    comentarios = datos.get("comments") or []
    autores = {c.get("fullname"): c.get("author", "") for c in comentarios}
    return {"post": {"title": post.get("title") or "", "author": post.get("author") or "",
                     "subreddit": post.get("subreddit") or "", "score": post.get("score"),
                     "selftext_html": _md(post.get("selftext") or ""), "url": post.get("url") or "",
                     "is_self": bool(post.get("is_self")),
                     "num_comments": int(post.get("num_comments") or 0)},
            "comments": [{"author": c.get("author", ""), "body_html": _md(c.get("body")),
                          "score": c.get("score"), "depth": int(c.get("depth") or 0),
                          "parent_author": autores.get(c.get("parent_id"), "")} for c in comentarios]}


def _reddit(post_id: str) -> Optional[str]:
    """Hilo de Reddit: con la cuenta de la aplicación o, como BlindRSS, con la sesión de
    Reddit del navegador (rss_reddit.py). Sin ninguna de las dos, el RSS de comentarios."""
    datos = None
    try:
        datos = _reddit_con_cuenta(post_id)
    except Exception as e:
        log.debug(f"Reddit con la cuenta de la aplicación falló: {e}")
    if not datos:
        from .rss_reddit import hilo_con_sesion
        datos = hilo_con_sesion(post_id)
    if not datos:
        return None
    post = datos["post"]
    donde = f" en r/{post['subreddit']}" if post.get("subreddit") else ""
    puntos = f", {_puntos(post['score'])}" if post.get("score") is not None else ""
    cab = [f"<p>{html.escape(_('Publicado por {0}').format(post.get('author', '')) + donde)}{puntos}.</p>"]
    if post.get("url") and not post.get("is_self"):
        u = html.escape(post["url"])
        cab.append(f'<p>{_("Enlace")}: <a href="{u}">{u}</a></p>')
    cab.append(post.get("selftext_html") or "")
    comentarios = datos.get("comments") or []
    cuerpo = [_comentario_html(c.get("author", ""), c.get("body_html") or "", int(c.get("depth") or 0),
                               c.get("score"), c.get("parent_author", ""))
              for c in comentarios[:MAX_COMENTARIOS]]
    total = max(int(post.get("num_comments") or 0), len(cuerpo))
    return "\n".join(cab) + _bloque_comentarios(total, cuerpo, total > len(cuerpo))


# ------------------------------------------------------------------ YouTube
def _hms(segundos) -> str:
    s = int(float(segundos or 0))
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _idioma_preferido() -> str:
    try:
        from app.core.config_manager import config
        return (config.get("translation", "default_target", "es") or "es").split("-")[0]
    except Exception:
        return "es"


def _elegir_subtitulos(info: dict):
    """(idioma, formatos, automáticos) con prioridad: los del autor en tu idioma, en el idioma
    del vídeo o en inglés; luego los automáticos en el idioma original del vídeo."""
    preferido = _idioma_preferido()
    original = (info.get("language") or "").split("-")[0]
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    orden = [preferido, original, "en"]
    for fuente, automaticos in ((subs, False), (autos, True)):
        claves = list(fuente)
        for idioma in orden + ([f"{original}-orig"] if automaticos and original else []):
            if not idioma:
                continue
            for k in claves:
                if k == idioma or (not automaticos and k.split("-")[0] == idioma):
                    return k, fuente[k], automaticos
        if not automaticos and claves:
            k = next((c for c in claves if c != "live_chat"), "")
            if k:
                return k, fuente[k], False
    return "", [], False


def _descargar_transcripcion(formatos) -> list:
    """[(segundo, texto)] a partir del formato json3 (o vtt si no hay json3)."""
    from app.core.net import http_session
    por_ext = {f.get("ext"): f.get("url") for f in formatos or [] if f.get("url")}
    if por_ext.get("json3"):
        r = http_session().get(por_ext["json3"], headers=_CABECERAS, timeout=_TIEMPO)
        r.raise_for_status()
        lineas = []
        for ev in (r.json().get("events") or []):
            texto = "".join(s.get("utf8", "") for s in ev.get("segs") or []).strip()
            if texto:
                lineas.append((ev.get("tStartMs", 0) / 1000.0, re.sub(r"\s+", " ", texto)))
        return lineas
    if por_ext.get("vtt"):
        r = http_session().get(por_ext["vtt"], headers=_CABECERAS, timeout=_TIEMPO)
        r.raise_for_status()
        lineas, inicio, previo = [], 0.0, ""
        for linea in r.text.splitlines():
            m = re.match(r"(\d+):(\d+):(\d+)[.,]\d+\s+-->", linea) or re.match(r"(\d+):(\d+)[.,]\d+\s+-->", linea)
            if m:
                g = [int(x) for x in m.groups()]
                inicio = g[0] * 3600 + g[1] * 60 + g[2] if len(g) == 3 else g[0] * 60 + g[1]
                continue
            texto = re.sub(r"<[^>]+>", "", linea).strip()
            if texto and "-->" not in texto and texto != "WEBVTT" and texto != previo:
                lineas.append((inicio, texto))
                previo = texto
        return lineas
    return []


def _parrafos_transcripcion(lineas, cada: float = 30.0) -> List[str]:
    """Agrupa las líneas en párrafos de unos 30 segundos, con el minuto delante."""
    parrafos, actual, inicio = [], [], None
    for seg, texto in lineas:
        if inicio is None:
            inicio = seg
        if actual and seg - inicio >= cada:
            parrafos.append((inicio, " ".join(actual)))
            actual, inicio = [], seg
        if not actual or actual[-1] != texto:
            actual.append(texto)
    if actual:
        parrafos.append((inicio or 0, " ".join(actual)))
    return [f"<p>[{_hms(s)}] {html.escape(t)}</p>" for s, t in parrafos]


def _youtube(video_id: str) -> Optional[str]:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False,
                                process=False)
    if not info:
        return None
    partes = []
    datos = [info.get("uploader") or info.get("channel") or ""]
    if info.get("duration"):
        datos.append(_("duración {0}").format(_hms(info["duration"])))
    if info.get("view_count"):
        datos.append(_("{0} visualizaciones").format(info["view_count"]))
    partes.append(f"<p>{html.escape(', '.join(d for d in datos if d))}</p>")
    desc = (info.get("description") or "").strip()
    if desc:
        partes.append("<h2>" + _("Descripción") + "</h2>")
        partes += [f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>"
                   for p in re.split(r"\n\s*\n", desc) if p.strip()]
    capitulos = info.get("chapters") or []
    if capitulos:
        partes.append("<h2>" + _("Capítulos") + "</h2><ol>")
        partes += [f"<li>{_hms(c.get('start_time'))} {html.escape(c.get('title') or '')}</li>"
                   for c in capitulos]
        partes.append("</ol>")
    idioma, formatos, automaticos = _elegir_subtitulos(info)
    lineas = []
    if formatos:
        try:
            lineas = _descargar_transcripcion(formatos)
        except Exception as e:
            log.debug(f"YouTube {video_id}: no se pudo descargar la transcripción: {e}")
    if lineas:
        tipo = _("automática") if automaticos else _("del autor")
        partes.append("<h2>" + _("Transcripción ({0}, idioma {1})").format(tipo, idioma) + "</h2>")
        if capitulos:
            # Cada capítulo con su parte de la transcripción, para saltar de uno a otro.
            limites = [float(c.get("start_time") or 0) for c in capitulos] + [float("inf")]
            for i, c in enumerate(capitulos):
                trozo = [(s, t) for s, t in lineas if limites[i] <= s < limites[i + 1]]
                if trozo:
                    partes.append(f"<h3>{_hms(limites[i])} {html.escape(c.get('title') or '')}</h3>")
                    partes += _parrafos_transcripcion(trozo)
        else:
            partes += _parrafos_transcripcion(lineas)
    return "\n".join(partes)


# ------------------------------------------------------------------ API
def extraer_por_sitio(url: str, titulo: str = ""):
    """ArticuloExtraido para hilos de foros y vídeos de YouTube, o None si el enlace no es de
    esos sitios (o el sitio no respondió): entonces se usa el extractor normal."""
    tipo = tipo_de_enlace(url)
    if not tipo:
        return None
    try:
        if tipo[0] == "youtube":
            cuerpo, metodo = _youtube(tipo[1]), "youtube"
        elif tipo[0] == "lemmy":
            cuerpo, metodo = _lemmy(tipo[1], tipo[2]), "lemmy"
        elif tipo[0] == "discourse":
            cuerpo, metodo = _discourse(tipo[1], tipo[2]), "discourse"
        else:
            cuerpo, metodo = _reddit(tipo[1]), "reddit"
    except Exception as e:
        log.debug(f"Extractor por sitio ({tipo[0]}) sin resultado para {url}: {e}")
        return None
    if not cuerpo:
        return None
    from app.core.html_utils import html_a_texto, sanear_html
    from .rss_fulltext import ArticuloExtraido
    cuerpo = sanear_html(cuerpo)
    return ArticuloExtraido(html=cuerpo, texto=html_a_texto(cuerpo), metodo=metodo, url_final=url)
