# -*- coding: utf-8 -*-
"""Bandeja de entrada: las publicaciones SIN TEXTO no pueden quedarse mudas.

Un toot de Mastodon que solo lleva una imagen, un vídeo, un audio o una encuesta tiene el
contenido vacío. La bandeja no tiene columnas {media} ni {indicators} donde caiga esa
información, así que la fila se leía como "Fulanito. Hace 7 horas." y nada más: lo que los
usuarios reportaron como "publicaciones fantasma". Aquí se comprueba que el contenido de la
bandeja describe esos adjuntos (como ya hacía con Bluesky) y que nunca queda en blanco.
"""
import json

from app.ui.mf_bandeja import _Mf_bandeja


def _enriquecer(**kwargs):
    datos = {"cuerpo": "", "quote_json": "", "card_json": "",
             "media_json": "", "has_poll": 0, "spoiler_text": ""}
    datos.update(kwargs)
    return _Mf_bandeja._enriquecer_content_mastodon(None, **datos)


def _media(tipo, descripcion=None):
    return json.dumps([{"type": tipo, "description": descripcion}])


def test_toot_solo_imagen_sin_descripcion_no_queda_mudo():
    texto = _enriquecer(media_json=_media("image"))
    assert texto.strip()
    assert "Imagen" in texto


def test_toot_solo_imagen_usa_el_texto_alternativo():
    texto = _enriquecer(media_json=_media("image", "Un pájaro sobre una rama"))
    assert "Un pájaro sobre una rama" in texto


def test_video_y_audio_se_identifican_por_su_tipo():
    assert "Vídeo" in _enriquecer(media_json=_media("video"))
    assert "Audio" in _enriquecer(media_json=_media("audio"))
    assert "GIF" in _enriquecer(media_json=_media("gifv"))


def test_encuesta_sin_texto_se_anuncia():
    assert "Encuesta" in _enriquecer(has_poll=1)


def test_advertencia_de_contenido_va_delante_del_cuerpo():
    texto = _enriquecer(cuerpo="el spoiler", spoiler_text="Final de temporada")
    assert texto.index("Final de temporada") < texto.index("el spoiler")


def test_el_cuerpo_se_conserva_junto_a_los_adjuntos():
    texto = _enriquecer(cuerpo="mira esto", media_json=_media("image", "gráfica"))
    assert "mira esto" in texto and "gráfica" in texto


def test_media_json_corrupto_no_rompe_la_fila():
    assert _enriquecer(cuerpo="hola", media_json="{no es json") == "hola"
