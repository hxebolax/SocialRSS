# -*- coding: utf-8 -*-
"""RSS, fase 2 del informe de BlindRSS (2026-09-26): leer entero.

- Hilos de foros (Lemmy, Discourse) con sus comentarios en árbol, y vídeos de YouTube con
  descripción, capítulos y transcripción (sin red: API simuladas).
- Artículos partidos en varias páginas.
- Notas y capítulos dentro del MP3 (ID3)."""
import io

import pytest

from app.services.rss import rss_foros
from app.services.rss import rss_paginas
from app.services.rss.rss_id3 import analizar_id3, html_de_id3, notas_para_articulo


# ------------------------------------------------------------------ detección de enlaces
@pytest.mark.parametrize("url, esperado", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=3", ("youtube", "jNQXAC9IVRw")),
    ("https://youtu.be/jNQXAC9IVRw", ("youtube", "jNQXAC9IVRw")),
    ("https://www.youtube.com/shorts/jNQXAC9IVRw", ("youtube", "jNQXAC9IVRw")),
    ("https://www.youtube.com/@canal", None),
    ("https://lemmy.world/post/123", ("lemmy", "https://lemmy.world", 123)),
    ("https://meta.discourse.org/t/un-tema/96331", ("discourse", "https://meta.discourse.org", 96331)),
    ("https://meta.discourse.org/t/un-tema/96331/4", ("discourse", "https://meta.discourse.org", 96331)),
    ("https://www.reddit.com/r/python/comments/abc123/titulo/", ("reddit", "abc123")),
    ("https://www.elpais.com/noticia/2026/", None),
])
def test_tipo_de_enlace(url, esperado):
    assert rss_foros.tipo_de_enlace(url) == esperado


# ------------------------------------------------------------------ Lemmy
def test_lemmy_arbol_de_comentarios(monkeypatch):
    def _c(cid, ruta, autor, texto, puntos):
        return {"comment": {"id": cid, "path": ruta, "content": texto},
                "creator": {"name": autor}, "counts": {"score": puntos}}

    def falso(url, params=None):
        if url.endswith("/api/v3/post"):
            return {"post_view": {"post": {"name": "T", "body": "Cuerpo **fuerte**"},
                                  "creator": {"name": "ana"}, "community": {"name": "linux"},
                                  "counts": {"score": 10, "comments": 3}}}
        if params.get("page") == 1:
            return {"comments": [_c(1, "0.1", "luis", "Primero", 5), _c(3, "0.2", "eva", "Otro", 2),
                                 _c(2, "0.1.2", "ana", "Respuesta", 1)]}
        return {"comments": [_c(1, "0.1", "luis", "Primero", 5)]}   # página repetida
    monkeypatch.setattr(rss_foros, "_get_json", falso)
    html = rss_foros._lemmy("https://l.test", 7)
    assert "<strong>fuerte</strong>" in html and "Comentarios (3)" in html
    # Orden de lectura en árbol: la respuesta va justo debajo de su comentario, un nivel más.
    assert html.index("<h3>luis, 5 puntos</h3>") < html.index("<h4>ana, 1 punto, responde a luis</h4>") \
        < html.index("<h3>eva, 2 puntos</h3>")
    assert html.count("luis, 5 puntos") == 1


def test_discourse_pide_el_resto_de_mensajes(monkeypatch):
    pedidos = []

    def falso(url, params=None):
        pedidos.append(url)
        if url.endswith("/t/9.json"):
            return {"posts_count": 3, "post_stream": {
                "stream": [1, 2, 3],
                "posts": [{"id": 1, "post_number": 1, "username": "ana", "cooked": "<p>Tema</p>"}]}}
        return {"post_stream": {"posts": [
            {"id": 3, "post_number": 3, "username": "eva", "cooked": "<p>Tres</p>",
             "reply_to_post_number": 2, "reply_to_user": {"username": "luis"}},
            {"id": 2, "post_number": 2, "username": "luis", "cooked": "<p>Dos</p>"}]}}
    monkeypatch.setattr(rss_foros, "_get_json", falso)
    html = rss_foros._discourse("https://d.test", 9)
    assert pedidos == ["https://d.test/t/9.json", "https://d.test/t/9/posts.json"]
    assert html.index("<h3>luis</h3>") < html.index("<h3>eva, responde a luis</h3>")
    assert "Comentarios (2)" in html


def test_reddit_sin_cuenta_ni_sesion_no_hace_nada(monkeypatch):
    from app.core.database import db
    monkeypatch.setattr(db, "query_one", lambda *a, **k: None)
    from app.services.rss import rss_reddit
    monkeypatch.setattr(rss_reddit, "hilo_con_sesion", lambda pid: None)   # ni navegador ni red
    assert rss_foros._reddit("abc") is None


# ------------------------------------------------------------------ YouTube
def test_youtube_capitulos_y_transcripcion(monkeypatch):
    info = {"uploader": "Canal", "duration": 125, "description": "Hola.\n\nSegundo párrafo.",
            "chapters": [{"start_time": 0, "title": "Intro"}, {"start_time": 60, "title": "Tema"}],
            "subtitles": {"es": [{"ext": "json3", "url": "https://s.test/es"}]}}

    class _YDL:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, *a, **k):
            return info
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _YDL)
    monkeypatch.setattr(rss_foros, "_descargar_transcripcion",
                        lambda f: [(1, "buenas"), (2, "a todos"), (61, "ahora el tema")])
    html = rss_foros._youtube("jNQXAC9IVRw")
    assert "duración 2:05" in html and "<p>Segundo párrafo.</p>" in html
    assert "Transcripción (del autor, idioma es)" in html
    assert html.index("<h3>0:00 Intro</h3>") < html.index("[0:01] buenas a todos") \
        < html.index("<h3>1:00 Tema</h3>") < html.index("[1:01] ahora el tema")


def test_youtube_elige_subtitulos(monkeypatch):
    monkeypatch.setattr(rss_foros, "_idioma_preferido", lambda: "es")
    info = {"language": "en", "subtitles": {"de": [1]},
            "automatic_captions": {"en": [2], "es": [3], "fr": [4]}}
    # Los del autor mandan aunque no estén en tu idioma ni en el del vídeo.
    assert rss_foros._elegir_subtitulos(info) == ("de", [1], False)
    info["subtitles"] = {}
    assert rss_foros._elegir_subtitulos(info) == ("es", [3], True)


# ------------------------------------------------------------------ varias páginas
def test_siguiente_pagina_de_la_misma_noticia():
    base = "https://p.test/noticias/la-gran-historia-19"
    html = ('<a href="/noticias/otra-historia">Siguiente</a>'
            '<a href="/noticias/la-gran-historia-19/2">Siguiente</a>')
    assert rss_paginas.siguiente_pagina(html, base) == base + "/2"
    html2 = '<link rel="next" href="?id=4&page=3"><a href="?id=4&page=2">2</a>'
    assert rss_paginas.siguiente_pagina(html2, "https://p.test/n?id=4&page=2",
                                        pagina=2) == "https://p.test/n?id=4&page=3"
    # Una noticia distinta nunca cuenta como página siguiente.
    assert rss_paginas.siguiente_pagina('<a href="/noticias/otra/2">Siguiente</a>', base) is None


def test_unir_paginas_quita_repetidos():
    unido = rss_paginas.unir_paginas("<p>Entradilla</p><p>Uno</p>",
                                     ["<p>Entradilla</p><p>Dos</p>", "<p>Entradilla</p>"])
    assert unido.count("Entradilla") == 1 and "Dos" in unido


# ------------------------------------------------------------------ ID3
def _mp3_con_etiqueta():
    from mutagen.id3 import ID3, CHAP, COMM, CTOC, TIT2, CTOCFlags
    tag = ID3()
    tag.add(TIT2(encoding=3, text=["Episodio 7"]))
    tag.add(COMM(encoding=3, lang="spa", desc="", text=["Notas del programa con enlaces y más."]))
    tag.add(CTOC(element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                 child_element_ids=["c1", "c2"], sub_frames=[]))
    tag.add(CHAP(element_id="c2", start_time=90500, end_time=200000,
                 sub_frames=[TIT2(encoding=3, text=["Segunda parte"])]))
    tag.add(CHAP(element_id="c1", start_time=0, end_time=90500,
                 sub_frames=[TIT2(encoding=3, text=["Presentación"])]))
    f = io.BytesIO()
    tag.save(f)
    return f.getvalue() + b"\xff\xfb" + b"\x00" * 400


def test_id3_notas_y_capitulos():
    datos = analizar_id3(_mp3_con_etiqueta())
    assert datos["titulo"] == "Episodio 7"
    assert datos["notas"] == "Notas del programa con enlaces y más."
    assert datos["capitulos"] == [(0.0, "Presentación"), (90.5, "Segunda parte")]
    html = html_de_id3(datos)
    assert "<li>1:30 Segunda parte</li>" in html and "Notas del episodio" in html
    assert analizar_id3(b"\xff\xfb" + b"\x00" * 50) == {}


def test_id3_solo_para_audio_mp3(monkeypatch):
    import app.services.rss.rss_id3 as m
    monkeypatch.setattr(m, "leer_id3_remoto", lambda u: {"notas": "Algo que contar"})
    assert notas_para_articulo("https://a.test/v.mp4", "video/mp4") is None
    assert "Algo que contar" in notas_para_articulo("https://a.test/e.mp3", "audio/mpeg")
    assert "Algo que contar" in notas_para_articulo("https://a.test/e.mp3?x=1", "")


# ------------------------------------------------------------------ Reddit con la sesión (como BlindRSS)
def test_reddit_json_con_sesion_y_cargar_mas():
    from app.services.rss import rss_reddit

    def c(nombre, autor, padre, respuestas=None):
        return {"kind": "t1", "data": {"name": nombre, "author": autor, "body_html": f"<p>{autor}</p>",
                                       "score": 2, "parent_id": padre,
                                       "replies": {"data": {"children": respuestas or []}} if respuestas else ""}}
    hilo = [{"data": {"children": [{"data": {"title": "T", "author": "op", "subreddit": "py", "score": 9,
                                             "selftext_html": "<p>cuerpo</p>", "is_self": True,
                                             "num_comments": 4}}]}},
            {"data": {"children": [c("t1_a", "ana", "t3_x", [c("t1_b", "luis", "t1_a")]),
                                   {"kind": "more", "data": {"children": ["c", "d"]}}]}}]
    mas = {"json": {"data": {"things": [c("t1_c", "eva", "t3_x"), c("t1_d", "pio", "t1_c")]}}}

    class _R:
        def __init__(self, d):
            self.status_code, self._d, self.headers = 200, d, {"Content-Type": "application/json"}

        def json(self):
            return self._d

    class _S:
        def get(self, url, params=None, **k):
            return _R(mas if "morechildren" in url else hilo)
    datos = rss_reddit.hilo_json("x", _S())
    assert [(x["author"], x["depth"], x["parent_author"]) for x in datos["comments"]] == [
        ("ana", 0, ""), ("luis", 1, "ana"), ("eva", 0, ""), ("pio", 1, "eva")]


def test_reddit_prefiere_cuenta_y_luego_sesion(monkeypatch):
    from app.services.rss import rss_reddit
    monkeypatch.setattr(rss_foros, "_reddit_con_cuenta", lambda pid: None)
    monkeypatch.setattr(rss_reddit, "hilo_con_sesion", lambda pid: {
        "post": {"title": "T", "author": "op", "subreddit": "py", "score": 3, "selftext_html": "<p>Hola</p>",
                 "url": "", "is_self": True, "num_comments": 1},
        "comments": [{"author": "ana", "body_html": "<p>Sí</p>", "score": 1, "depth": 0, "parent_author": ""}]})
    html = rss_foros._reddit("x")
    assert "Publicado por op en r/py, 3 puntos." in html and "<h3>ana, 1 punto</h3>" in html
