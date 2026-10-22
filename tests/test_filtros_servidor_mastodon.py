# -*- coding: utf-8 -*-
"""Filtros del servidor de Mastodon aplicados como la web oficial: 'hide' no se muestra,
'warn' avisa por título, 'blur' solo afecta a la multimedia, por contexto y caducidad."""
import builtins
import json

import pytest

builtins.__dict__.setdefault("_", lambda s: s)

import app.services.mastodon.filtros_servidor as fs  # noqa: E402


def _filtered(fid, titulo, accion, contextos, expira=None):
    return {"filter": {"id": fid, "title": titulo, "filter_action": accion,
                       "context": contextos, "expires_at": expira},
            "keyword_matches": ["x"], "status_matches": None}


@pytest.fixture(autouse=True)
def _limpio():
    fs._vigentes.clear()
    fs._pedidos.clear()
    yield
    fs._vigentes.clear()
    fs._pedidos.clear()


def test_hide_oculta_en_su_contexto_y_no_en_otro():
    fb = fs.serializar_filtrados([_filtered(1, "politica", "hide", ["home", "notifications"])])
    assert fs.evaluar(fb, 7, "home")["accion"] == "hide"
    assert fs.evaluar(fb, 7, "list:3")["accion"] == "hide"
    assert fs.evaluar(fb, 7, "notifications")["accion"] == "hide"
    assert fs.evaluar(fb, 7, "public")["accion"] is None        # contexto no marcado


def test_warn_avisa_con_titulo():
    fb = fs.serializar_filtrados([_filtered(2, "spoilers", "warn", ["public"])])
    ev = fs.evaluar(fb, 7, "local")
    assert ev["accion"] == "warn" and ev["titulos"] == ["spoilers"]
    assert fs.segmento_aviso(ev) == ["[Filtro: spoilers]"]


def test_hide_gana_a_warn_y_blur_solo_multimedia():
    fb = fs.serializar_filtrados([_filtered(1, "a", "warn", ["home"]),
                                  _filtered(2, "b", "hide", ["home"]),
                                  _filtered(3, "c", "blur", ["home"])])
    ev = fs.evaluar(fb, 7, "home")
    assert ev["accion"] == "hide" and ev["titulos_media"] == ["c"]
    solo_blur = fs.serializar_filtrados([_filtered(3, "c", "blur", ["home"])])
    ev = fs.evaluar(solo_blur, 7, "home")
    assert ev["accion"] is None
    assert fs.segmento_aviso(ev, tiene_media=True) == ["[Multimedia filtrada: c]"]
    assert fs.segmento_aviso(ev, tiene_media=False) == []


def test_favoritos_busqueda_y_foco_del_hilo_avisan_en_vez_de_ocultar():
    fb = fs.serializar_filtrados([_filtered(1, "p", "hide", ["home", "public", "thread"])])
    assert fs.evaluar(fb, 7, "favourites")["accion"] == "warn"
    assert fs.evaluar(fb, 7, "bookmarks")["accion"] == "warn"
    assert fs.evaluar(fb, 7, "search:algo")["accion"] == "warn"
    assert fs.evaluar(fb, 7, "thread:99")["accion"] == "hide"
    assert fs.evaluar(fb, 7, "thread:99", foco_detalle=True)["accion"] == "warn"


def test_caducado_no_cuenta():
    fb = fs.serializar_filtrados([_filtered(1, "p", "hide", ["home"], "2000-01-01T00:00:00Z")])
    assert fs.evaluar(fb, 7, "home")["accion"] is None


def test_vigentes_mandan_filtro_borrado_o_cambiado():
    fb = fs.serializar_filtrados([_filtered(1, "p", "hide", ["home"])])
    fs.actualizar_vigentes(7, [])                                    # lo borraste
    assert fs.evaluar(fb, 7, "home")["accion"] is None
    fs.actualizar_vigentes(7, [{"id": "1", "title": "p", "filter_action": "warn",
                                "context": ["home"], "expires_at": "", "v2": True}])
    assert fs.evaluar(fb, 7, "home")["accion"] == "warn"            # lo pasaste a avisar


def test_respuesta_v1_no_pisa_vigentes():
    fs.actualizar_vigentes(7, [{"id": "1", "phrase": "p", "context": ["home"], "v2": False}])
    assert not fs.tiene_vigentes(7)


def test_formato_antiguo_solo_titulo_se_cruza_por_titulo():
    # Lo guardado antes del cambio: solo el título. Sin vigentes, se avisa.
    assert fs.evaluar("politica", 7, "home")["accion"] == "warn"
    fs.actualizar_vigentes(7, [{"id": "5", "title": "politica", "filter_action": "hide",
                                "context": ["home", "public"], "expires_at": "", "v2": True}])
    assert fs.evaluar("politica", 7, "home")["accion"] == "hide"
    assert fs.evaluar("otro", 7, "home")["accion"] is None


def test_contextos_de_timeline():
    assert fs.contexto_de_timeline("user:@a")["contexto"] == "account"
    assert fs.contexto_de_timeline("trending")["contexto"] == "public"
    assert fs.contexto_de_timeline("mentions")["contexto"] == "notifications"


def test_serializar_deduplica_y_vacio():
    assert fs.serializar_filtrados([]) is None
    fb = fs.serializar_filtrados([_filtered(1, "a", "hide", ["home"]), _filtered(1, "a", "hide", ["home"])])
    assert len(json.loads(fb)) == 1
