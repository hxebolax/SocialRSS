# -*- coding: utf-8 -*-
"""Palabras silenciadas de AT Protocol: casos sacados de los tests oficiales
(packages/api/tests/moderation-mutewords.test.ts) más los de publicación completa."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.bluesky.bluesky_mutewords import (
    coincidencias, palabras_de_post, tags_de_facets, caducada, normalizar_palabra)


def _w(valor, targets=("content",), actor="all", expira=""):
    return {"value": valor, "targets": list(targets), "actorTarget": actor, "expiresAt": expira}


def _casa(valor, texto, **kw):
    return bool(coincidencias([_w(valor, kw.pop("targets", ("content",)))], texto, **kw))


# ------------------------------------------------------------------ etiquetas
def test_tags_outline_e_inline():
    w = _w("outlineTag", ["tag"])
    m = coincidencias([w], "This is a post #inlineTag", tags=["inlineTag", "outlineTag"])
    assert m and m[0]["predicate"] == "outlineTag"
    assert _casa("inlineTag", "This is a post #inlineTag", targets=["tag"],
                 tags=["inlineTag", "outlineTag"])


def test_content_tambien_mira_etiquetas_y_tag_solo_etiquetas():
    assert _casa("inlineTag", "This is a post #inlineTag", tags=["inlineTag"])
    assert not _casa("inlineTag", "This is a post", targets=["tag"], tags=[])


def test_tags_de_facets():
    facets = [{"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "bad"},
                            {"$type": "app.bsky.richtext.facet#tag", "tag": "tags"}]},
              {"features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://x"}]}]
    assert tags_de_facets(facets) == ["bad", "tags"]
    assert _casa("bad", "tags", tags=tags_de_facets(facets))


# ------------------------------------------------------------------ salidas tempranas
@pytest.mark.parametrize("valor,texto,esperado", [
    ("希", "改善希望です", True),                 # un carácter: subcadena
    ("☠︎", "Idk why ☠︎ but maybe", True),        # carácter con selector de variación
    ("politics", "hey", False),                   # palabra más larga que el texto
    ("javascript", "javascript", True),           # texto exacto
    ("javascript", "This is a post about javascript", True),
    ("ai", "Use your brain, Eric", False),        # parte de palabra: no
    ("brain", "Use your\n\tbrain, Eric", True),   # multilínea
    (":)", "So happy :)", True),
])
def test_contenido_general(valor, texto, esperado):
    assert _casa(valor, texto) is esperado


# ------------------------------------------------------------------ puntuación
@pytest.mark.parametrize("valor,texto,esperado", [
    ("yay!", "We're federating, yay!", True),
    ("yay", "We're federating, yay!", True),
    ("y!ppee", "We're federating, y!ppee!!", True),
    ("y!ppee!", "We're federating, y!ppee!!", True),
    ("Bluesky's", "Yay, Bluesky's mutewords work", True),
    ("Bluesky", "Yay, Bluesky's mutewords work", True),
    ("bluesky", "Yay, Bluesky's mutewords work", True),
    ("blueskys", "Yay, Bluesky's mutewords work", True),
    ("S@assy", "Why so S@assy?", True),
    ("s@assy", "Why so S@assy?", True),
    ("new york times", "New York Times", True),
    ("!command", "Idk maybe a bot !command", True),
    ("command", "Idk maybe a bot !command", True),
    ("!command", "Idk maybe a bot command", False),
    ("and/or", "Tomatoes are fruits and/or vegetables", True),
    ("Andor", "Tomatoes are fruits and/or vegetables", False),
    ("super-bad", "I'm super-bad", True),
    ("super", "I'm super-bad", True),
    ("bad", "I'm super-bad", True),
    ("super bad", "I'm super-bad", True),
    ("superbad", "I'm super-bad", True),
    ("idk what this would be", "Weird post with idk_what_this_would_be", True),
    ("idk what this would be for", "Weird post with idk_what_this_would_be", False),
    ("idk", "Weird post with idk_what_this_would_be", True),
    ("idkwhatthiswouldbe", "Weird post with idk_what_this_would_be", True),
    ("context(iykyk)", "Post with context(iykyk)", True),
    ("context", "Post with context(iykyk)", True),
    ("iykyk", "Post with context(iykyk)", True),
    ("(iykyk)", "Post with context(iykyk)", True),
    ("🦋", "Post with 🦋", True),
])
def test_puntuacion_semi_difusa(valor, texto, esperado):
    assert _casa(valor, texto) is esperado


def test_frases():
    texto = "I like turtles, or how I learned to stop worrying and love the internet."
    assert _casa("stop worrying", texto)
    assert _casa("turtles, or how", texto)


def test_idiomas_sin_espacios():
    texto = "私はカメが好きです、またはどのようにして心配するのをやめてインターネットを愛するようになったのか"
    assert _casa("インターネット", texto, idiomas=["ja"])
    # Sin el idioma declarado no hay excepción: la palabra no está separada por espacios.
    assert not _casa("インターネット", texto, idiomas=["es"])


# ------------------------------------------------------------------ caducidad y cuentas
def test_caducidad():
    futuro = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    pasado = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    assert coincidencias([_w("words", expira=futuro)], "Mute words!")
    assert not coincidencias([_w("words", expira=pasado)], "Mute words!")
    assert caducada(_w("x", expira=pasado)) and not caducada(_w("x"))


def test_excepto_cuentas_que_sigo():
    w = _w("words", actor="exclude-following")
    assert not coincidencias([w], "Mute words!", sigue_al_autor=True)
    assert coincidencias([w], "Mute words!", sigue_al_autor=False)


def test_devuelve_todas_las_coincidencias():
    m = coincidencias([_w("post"), _w("javascript")], "This is a post about javascript")
    assert [x["word"]["value"] for x in m] == ["post", "javascript"]


def test_normalizar_palabra_snake_y_camel():
    class Modelo:
        value = "hola"
        targets = ["tag"]
        actor_target = None
        expires_at = None
        id = "abc"
    w = normalizar_palabra(Modelo())
    assert w == {"value": "hola", "targets": ["tag"], "actorTarget": "all",
                 "expiresAt": "", "id": "abc"}


# ------------------------------------------------------------------ publicación completa
def test_publicacion_alt_tarjeta_y_cita():
    palabras = [_w("badword")]
    assert palabras_de_post({"tx": "innocent text", "alt": ["contains badword here"]}, palabras)
    assert not palabras_de_post({"tx": "innocent text", "alt": ["clean"]}, palabras)
    assert palabras_de_post({"tx": "hola", "ext": "Título con badword descripción"}, palabras)
    # Cita: su texto, su alt y su galería dentro de recordWithMedia.
    assert palabras_de_post({"tx": "outer post", "q": {"tx": "badword inside"}}, palabras)
    assert palabras_de_post({"tx": "outer post", "q": {"tx": "x", "alt": ["badword"]}}, palabras)
    assert palabras_de_post({"tx": "outer post", "q": {"tx": "x", "ralt": ["badword"]}}, palabras)
    # Multimedia del recordWithMedia propio.
    assert palabras_de_post({"tx": "outer", "q": {"tx": "inner"}, "malt": ["badword"]}, palabras)
    # Cita no visible (bloqueo): no se mira.
    assert not palabras_de_post({"tx": "outer", "q": {"blk": True, "tx": "badword"}}, palabras)


def test_publicacion_cita_usa_el_seguimiento_del_autor_citado():
    w = [_w("words", actor="exclude-following")]
    datos = {"tx": "outer", "f": 0, "q": {"tx": "Mute words!", "f": 1}}
    assert not palabras_de_post(datos, w)
    datos["q"]["f"] = 0
    assert palabras_de_post(datos, w)
