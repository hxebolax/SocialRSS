# -*- coding: utf-8 -*-
"""Capa de plataforma: el núcleo habla con la interfaz solo a través de app.core.plataforma."""
import threading

import pytest

from app.core import plataforma


@pytest.fixture
def interfaz_falsa(monkeypatch):
    """Sustituye la interfaz registrada por una falsa que ejecuta en el acto (hilo 'de la UI')."""
    nueva = plataforma._Interfaz()
    monkeypatch.setattr(plataforma, "_interfaz", nueva)
    return nueva


def test_sin_interfaz_no_ejecuta_ni_falla(interfaz_falsa):
    llamadas = []
    assert plataforma.llamar_en_ui(llamadas.append, 1) is False
    assert llamadas == []
    assert plataforma.hay_interfaz() is False


def test_llamar_en_ui_usa_la_interfaz_registrada(interfaz_falsa):
    llamadas = []
    plataforma.registrar_interfaz("falsa", llamar_en_ui=lambda f, *a, **k: f(*a, **k))
    assert plataforma.llamar_en_ui(lambda x, y=0: llamadas.append((x, y)), 1, y=2) is True
    assert llamadas == [(1, 2)]
    assert plataforma.nombre_interfaz() == "falsa"


def test_registrar_capacidad_desconocida_se_ignora(interfaz_falsa):
    plataforma.registrar_interfaz("falsa", no_existe=lambda: None)
    assert not hasattr(plataforma._interfaz, "no_existe")


def test_confirmar_ui_desde_otro_hilo_espera_la_respuesta(interfaz_falsa):
    cola = []
    principal = threading.current_thread()

    def _llamar(fn, *a, **k):          # simula el bucle de la UI: lo ejecuta el hilo "principal"
        cola.append(lambda: fn(*a, **k))
    plataforma.registrar_interfaz(
        "falsa", llamar_en_ui=_llamar, hay_interfaz=lambda: True,
        es_hilo_ui=lambda: threading.current_thread() is principal,
        confirmar=lambda texto, titulo: texto == "¿seguro?")
    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(ok=plataforma.confirmar_ui("¿seguro?", "t")))
    hilo.start()
    while hilo.is_alive():
        while cola:
            cola.pop(0)()
        hilo.join(0.01)
    assert resultado["ok"] is True


def test_confirmar_ui_sin_interfaz_devuelve_el_valor_por_defecto(interfaz_falsa):
    assert plataforma.confirmar_ui("x", por_defecto=False) is False
    assert plataforma.confirmar_ui("x", por_defecto=True) is True


def test_anunciar_sin_capacidad_devuelve_false(interfaz_falsa):
    assert plataforma.anunciar("hola") is False
    plataforma.registrar_interfaz("falsa", anunciar=lambda t, i: True)
    assert plataforma.anunciar("hola") is True


def test_copiar_texto_usa_la_interfaz(interfaz_falsa):
    copiado = []
    plataforma.registrar_interfaz("falsa", copiar=lambda t: copiado.append(t) or True)
    assert plataforma.copiar_texto("abc") is True
    assert copiado == ["abc"]


def test_config_acepta_una_seccion_nueva_para_la_interfaz_gtk():
    from app.core.config_manager import config
    assert config.get("gtk", "mastodon_realtime", True) is True
    config.set("gtk", "mastodon_realtime", False)
    assert config.get("gtk", "mastodon_realtime", True) is False
    config.set("gtk", "mastodon_realtime", True)
