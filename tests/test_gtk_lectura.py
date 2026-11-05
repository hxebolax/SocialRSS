# -*- coding: utf-8 -*-
"""Lectura de las filas de la interfaz GTK con las plantillas del usuario (app/gtk/lectura.py)."""
from types import SimpleNamespace

from app.core.config_manager import config
from app.gtk import lectura
from app.gtk.datos import Fila


def _obj(servicio, extra, titulo="t", **estado):
    fila = Fila(id="1", feed_id=0, feed_titulo="", titulo=titulo, autor="Aurora", fecha="2020-01-01T10:00:00",
                leido=True, favorito=False, mas_tarde=False, audio=False, enlace="", servicio=servicio, extra=extra)
    base = dict(fila=fila, favorito=False, impulsado=False, marcador=False, leido=True, mas_tarde=False)
    base.update(estado)
    return SimpleNamespace(**base)


def _restaurar(clave):
    config.set("accessibility", clave, "")


def test_fila_sin_plantilla_no_se_lee_aqui():
    assert lectura.leer(_obj("reddit", {})) is None


def test_post_usa_la_plantilla_y_los_indicadores_cambian_con_el_estado():
    o = _obj("mastodon", {})
    lectura.con_plantilla(o.fila, "mastodon_template", {
        "author": "Aurora", "content": "Hola", "visibility": "Público", "media": ""})
    config.set("accessibility", "mastodon_template", "{author}: {content}. {indicators}.")
    try:
        assert lectura.leer(o)[0].startswith("Aurora: Hola.")
        assert "Favorito" not in lectura.leer(o)[0]
        o.favorito = True
        assert "Favorito" in lectura.leer(o)[0]
    finally:
        _restaurar("mastodon_template")


def test_plantilla_rota_cae_a_la_lectura_de_siempre():
    o = _obj("x", {})
    lectura.con_plantilla(o.fila, "x_post", {"author": "A"})
    config.set("accessibility", "x_post", "{author")
    try:
        assert lectura.leer(o) is None
    finally:
        _restaurar("x_post")


def test_rss_marca_leido_y_destacado():
    o = _obj("rss", {}, titulo="Titular", leido=False, favorito=True)
    lectura.con_plantilla(o.fila, "rss_template", {"feed": "Diario", "title": "Titular", "author": ""})
    texto = lectura.leer(o)[0]
    assert texto.startswith("Diario: Titular.") and "No leído" in texto and "Destacado" in texto


def test_en_la_bandeja_manda_la_plantilla_de_la_bandeja():
    o = _obj("mastodon", {"bandeja": {"red": "mastodon"}, "origen": "Mastodon · Inicio"})
    lectura.con_plantilla(o.fila, "mastodon_template", {"author": "Aurora", "content": "Hola"})
    o.fila.extra["bandeja"] = {"red": "mastodon"}
    texto, con_origen = lectura.leer(o)
    assert con_origen and texto.startswith("Mastodon · Inicio.") and "Aurora" in texto and "Hola" in texto


def test_mensaje_nuevo_lleva_el_aviso():
    o = _obj("irc", {"nuevo": True})
    lectura.con_plantilla(o.fila, "irc_message", {"nick": "ana", "content": "hola", "hora": "12:45"})
    assert lectura.leer(o)[0].startswith("Nuevo.") and "ana: hola" in lectura.leer(o)[0]


def test_notificacion_con_nombre_de_tipo():
    o = _obj("bluesky", {})
    lectura.notificacion(o.fila, "like", "Aurora", "aurora.bsky.social", "")
    assert lectura.leer(o)[0].startswith("Me gusta: Aurora.")
