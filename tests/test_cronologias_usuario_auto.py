# -*- coding: utf-8 -*-
"""Pruebas de la actualización AUTOMÁTICA de las cronologías de usuarios de Mastodon.

Las cronologías que se añaden como buffer ('user:<acct>') no se streamean: Mastodon no ofrece
tiempo real por cuenta. Antes tampoco entraban en el sondeo, así que no se actualizaban nunca
solas —ni al entrar en ellas— y solo traían novedades con Ctrl+Mayús+R (reporte de usuarios).
Estas pruebas fijan las dos mitades del arreglo:

  1. el sondeo del buffer ACTIVO ya no descarta las cronologías;
  2. las que NO estás mirando se refrescan en segundo plano POR TURNOS (una por repaso, y como
     mucho una vez cada ~2 min cada una), para no disparar una petición por cronología por tick.
"""
import pytest

from app.ui.mf_acciones_toot import _Mf_acciones_toot
from app.ui.mf_arranque_streaming import _Mf_arranque_streaming


def _config_falsa(monkeypatch, modulo, valores):
    """Sustituye config.get en el módulo indicado por un diccionario {(seccion, clave): valor}."""
    def _get(seccion, clave, defecto=None):
        return valores.get((seccion, clave), defecto)
    monkeypatch.setattr(modulo.config, "get", _get)


# --------------------------------------------------------------------------- sondeo del activo

class _FrameActivo(_Mf_acciones_toot):
    def __init__(self):
        self._updating = False
        self.active_mastodon_account_id = 1


@pytest.fixture
def frame_activo(monkeypatch):
    from app.ui import mf_acciones_toot as mod
    f = _FrameActivo()
    pedidos = []
    monkeypatch.setattr(mod.db, "query_one", lambda *a, **k: None)
    monkeypatch.setattr(
        mod.mastodon_service, "cargar_timeline_async",
        lambda acc, timeline_type=None, min_id=None, callback=None: pedidos.append(timeline_type))
    return f, pedidos


def test_la_cronologia_de_un_usuario_que_estas_mirando_se_sondea(frame_activo):
    f, pedidos = frame_activo
    f.active_timeline_type = "user:HXeBoLaX@comunidad.nvda.es"
    f._actualizar_mastodon_incremental()
    assert pedidos == ["user:HXeBoLaX@comunidad.nvda.es"]


def test_una_conversacion_abierta_como_buffer_tambien_se_sondea(frame_activo):
    f, pedidos = frame_activo
    f.active_timeline_type = "thread:12345"
    f._actualizar_mastodon_incremental()
    assert pedidos == ["thread:12345"]


def test_la_conversacion_no_se_pide_mas_de_una_vez_por_minuto(frame_activo):
    """Un hilo cuesta varias peticiones (contexto + mensaje): no debe repetirse en cada tick."""
    f, pedidos = frame_activo
    f.active_timeline_type = "thread:12345"
    f._actualizar_mastodon_incremental()
    f._updating = False              # el callback de la primera lo habría liberado
    f._actualizar_mastodon_incremental()
    assert pedidos == ["thread:12345"]


def test_los_buffers_que_solo_van_a_mano_siguen_sin_sondearse(frame_activo):
    """Cuentas, tendencias y demás buffers no cronológicos se refrescan con Ctrl+Mayús+R."""
    f, pedidos = frame_activo
    f.active_timeline_type = "followers"
    f._actualizar_mastodon_incremental()
    assert pedidos == []


# ------------------------------------------------------------------- sondeo de fondo por turnos

class _FrameFondo(_Mf_arranque_streaming):
    def __init__(self):
        self.active_mastodon_account_id = 1
        self.active_timeline_type = "home"
        self.sondeados = []

    def _sincronizar_timeline_streaming(self, acc, timeline_key, callback):
        self.sondeados.append(timeline_key)


@pytest.fixture
def frame_fondo(monkeypatch):
    from app.ui import mf_arranque_streaming as mod
    _config_falsa(monkeypatch, mod, {
        ("mastodon", "hidden_buffers"): [],
        ("mastodon", "buffer_order"): [],          # nada de bajo tráfico ni firehose que sondear
        ("mastodon", "user_timelines"): [
            {"account_id": 1, "acct": "ana@servidor"},
            {"account_id": 1, "acct": "luis@otro"},
            {"account_id": 2, "acct": "otra_cuenta@ahi"},   # de OTRA cuenta: no toca
        ],
    })
    return _FrameFondo()


def test_solo_se_sondea_una_cronologia_por_repaso(frame_fondo):
    """Con muchas cronologías, sondearlas todas en cada tick sería una ráfaga de peticiones."""
    frame_fondo._sondear_streamables_no_activos()
    assert frame_fondo.sondeados == ["user:ana@servidor"]


def test_el_turno_pasa_a_la_siguiente_y_no_se_repite_la_recien_sondeada(frame_fondo):
    frame_fondo._sondear_streamables_no_activos()
    frame_fondo._sondear_streamables_no_activos()
    assert frame_fondo.sondeados == ["user:ana@servidor", "user:luis@otro"]
    # Ya están todas al día: el siguiente repaso no vuelve a pedir nada.
    frame_fondo._sondear_streamables_no_activos()
    assert len(frame_fondo.sondeados) == 2


def test_no_se_sondean_las_cronologias_de_otras_cuentas(frame_fondo):
    for _ in range(5):
        frame_fondo._sondear_streamables_no_activos()
    assert all(not k.startswith("user:otra_cuenta") for k in frame_fondo.sondeados)


def test_la_cronologia_que_estas_mirando_no_se_sondea_por_duplicado(frame_fondo):
    """El buffer activo ya lo cubre _actualizar_mastodon_incremental."""
    frame_fondo.active_timeline_type = "user:ana@servidor"
    frame_fondo._sondear_streamables_no_activos()
    assert frame_fondo.sondeados == ["user:luis@otro"]
