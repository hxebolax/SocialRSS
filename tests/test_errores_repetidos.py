# -*- coding: utf-8 -*-
"""Fallos que se repiten en cada sondeo: ni traza infinita en el registro ni silencio al usuario.

Una cuenta de Bluesky con la sesión caducada y una instancia de Mastodon que responde con una
página web en lugar de con la API dejaron 3.200 fallos en un solo registro de 20 horas, con su
traza completa cada uno, y el usuario no se enteró de ninguno de los dos. Aquí se comprueban las
tres piezas del arreglo: clasificación correcta del error, apunte deduplicado y freno de los
sondeos cuando la sesión ya se sabe caducada.
"""
import pytest

from app.core import task_manager as tm
from app.core.errors import BlueskyServiceError, SocialRSSError


@pytest.fixture(autouse=True)
def _limpiar_dedupe():
    tm._ultimos_conocidos.clear()
    yield
    tm._ultimos_conocidos.clear()


# --- Clasificación de errores ------------------------------------------------

def test_codigo_http_dentro_de_otro_numero_no_cuenta_como_transitorio():
    """El value="5000" de una página HTML contenía "500" y colaba el error como transitorio."""
    e = Exception('bad json content was b\'<input id="http" value="5000">\'')
    assert not tm._es_error_transitorio(e)


def test_codigos_http_reales_si_son_transitorios():
    for codigo in ("429", "500", "502", "503", "504"):
        assert tm._es_error_transitorio(Exception(f"HTTP {codigo} desde el servidor")), codigo


def test_servidor_que_devuelve_html_se_reconoce():
    e = Exception("Could not parse response as JSON, response code was 200")
    assert tm._es_servidor_sin_api(e)
    assert not tm._es_servidor_sin_api(Exception("timeout"))


def test_sin_traza_por_defecto_es_falso():
    assert SocialRSSError("x").sin_traza is False
    err = BlueskyServiceError("caducada")
    err.sin_traza = True
    assert err.sin_traza is True


# --- Apunte deduplicado ------------------------------------------------------

def test_el_mismo_error_solo_se_anota_una_vez(caplog):
    with caplog.at_level("WARNING", logger="app.core.task_manager"):
        for _ in range(50):
            tm._registrar_error_conocido("La sesión de Bluesky ha caducado.")
    assert len(caplog.records) == 1


def test_errores_distintos_se_anotan_por_separado(caplog):
    with caplog.at_level("WARNING", logger="app.core.task_manager"):
        tm._registrar_error_conocido("sesión caducada")
        tm._registrar_error_conocido("el servidor no habla la API")
    assert len(caplog.records) == 2


def test_pasado_el_plazo_se_reanota_con_el_recuento(caplog):
    with caplog.at_level("WARNING", logger="app.core.task_manager"):
        for _ in range(10):
            tm._registrar_error_conocido("sesión caducada")
        assert len(caplog.records) == 1        # las 9 repeticiones no dijeron nada
        # Simular que ha pasado el plazo de silencio empujando hacia atrás el último apunte.
        tm._ultimos_conocidos["sesión caducada"][0] -= tm._DEDUPE_SEGUNDOS + 1
        tm._registrar_error_conocido("sesión caducada")
    assert len(caplog.records) == 2
    assert "10 veces" in caplog.records[-1].message


# --- Freno de los sondeos de Bluesky ----------------------------------------

def _servicio_bluesky():
    from app.services.bluesky.bluesky_service import BlueskyService
    return BlueskyService()


def test_sesion_caducada_es_falso_mientras_la_cuenta_va_bien():
    assert _servicio_bluesky().sesion_caducada(9) is False


def test_al_marcar_revocada_se_avisa_una_sola_vez(monkeypatch):
    svc = _servicio_bluesky()
    avisos = []
    monkeypatch.setattr("app.core.speech.notificar", lambda *a, **k: avisos.append(a))
    svc._marcar_sesion_revocada(9)
    svc._marcar_sesion_revocada(9)
    svc._marcar_sesion_revocada(9)
    assert svc.sesion_caducada(9) is True
    assert len(avisos) == 1


def test_el_error_de_sesion_caducada_no_pide_traza():
    svc = _servicio_bluesky()
    svc._sesiones_revocadas.add(9)
    err = svc._error_sesion_caducada(9)
    assert isinstance(err, BlueskyServiceError)
    assert err.sin_traza is True


def test_abortar_avisa_al_callback_y_corta_la_tarea():
    svc = _servicio_bluesky()
    svc._sesiones_revocadas.add(9)
    recibido = []
    # Sin bucle de wx, wx.CallAfter no entrega: lo que se comprueba es que ABORTA.
    assert svc.abortar_si_sesion_caducada(9, lambda r, e: recibido.append(e)) is True
    assert svc.abortar_si_sesion_caducada(1, lambda r, e: recibido.append(e)) is False


def test_reautenticar_vuelve_a_habilitar_los_sondeos():
    svc = _servicio_bluesky()
    svc._sesiones_revocadas.add(9)
    assert svc.sesion_caducada(9) is True
    svc._sesiones_revocadas.discard(9)   # lo que hace reautenticar()
    assert svc.sesion_caducada(9) is False
