# -*- coding: utf-8 -*-
"""Pruebas del repintado DIFERIDO de la lista principal mientras hay un diálogo modal.

Reporte de usuario: con el compositor abierto (y el contador en vivo desactivado) la aplicación
se atascaba al escribir y salían mayúsculas por minúsculas. El compositor no hacía trabajo por
tecla, pero detrás, cada toot que llegaba por el streaming y cada tick del temporizador repintaban
la lista principal (consulta del buffer entero + filtros + "hace X") en el hilo de la interfaz.
Ahora, con un modal delante, la lista no se toca y se pone al día UNA vez al cerrarlo.
"""
import pytest

from app.ui.mf_arranque_streaming import _Mf_arranque_streaming
from app.ui.mf_diferido_modal import _Mf_diferido_modal


class _Frame(_Mf_arranque_streaming, _Mf_diferido_modal):
    def __init__(self):
        import threading
        self.modal = False
        self.active_mastodon_account_id = 1
        self.active_timeline_type = "home"
        self._cargando_buffers = False
        self._bandeja_activa = False
        self._lock_streaming = threading.Lock()
        self._refrescos_streaming_pendientes = set()
        self._estado_evento_streaming = {}
        self._ultimo_refresco_streaming = {}
        self._updating = False
        self.insertados = 0
        self.recargas = 0
        self.sincronizados = []

    def _modal_delante(self):
        return self.modal

    def _filas_mastodon_actual(self):
        return ["fila"]

    def _insertar_items_incremental(self, filas):
        self.insertados += 1
        return len(filas)

    def _cargar_listado_toots(self, cache_only=False):
        self.recargas += 1

    def _sincronizar_timeline_streaming(self, acc, timeline_key, callback):
        self.sincronizados.append((timeline_key, callback))

    def _timelines_para_streaming(self, acc=None):
        return ["home"]


@pytest.fixture
def frame(monkeypatch):
    from app.ui import mf_arranque_streaming as mod
    monkeypatch.setattr(mod.config, "get",
                        lambda s, k, d=None: True if (s, k) == ("mastodon", "realtime") else d)
    return _Frame()


def _evento(frame, borrado=False, rest=False):
    """Simula que el streaming coalescido de 'home' llega al hilo de la interfaz."""
    frame._estado_evento_streaming[(1, "home")] = {"rest": rest, "borrado": borrado}
    frame._refrescar_por_streaming(1, "home")


def test_sin_modal_el_streaming_repinta_al_momento(frame):
    _evento(frame)
    assert frame.insertados == 1


def test_con_modal_el_streaming_no_toca_la_lista(frame):
    frame.modal = True
    _evento(frame)
    _evento(frame)
    assert frame.insertados == 0
    assert frame._pendiente_tras_modal


def test_al_cerrar_el_modal_se_repinta_una_sola_vez(frame):
    frame.modal = True
    _evento(frame)
    _evento(frame)
    frame.modal = False
    frame._ponerse_al_dia_tras_modal()
    frame._ponerse_al_dia_tras_modal()   # sin nada pendiente ya no hace nada
    assert frame.insertados == 1


def test_un_borrado_pendiente_recarga_desde_cache(frame):
    frame.modal = True
    _evento(frame, borrado=True)
    frame.modal = False
    frame._ponerse_al_dia_tras_modal()
    assert frame.recargas == 1 and frame.insertados == 0


def test_con_el_modal_aun_abierto_la_puesta_al_dia_espera(frame):
    frame.modal = True
    _evento(frame)
    frame._ponerse_al_dia_tras_modal()
    assert frame.insertados == 0 and frame._pendiente_tras_modal


def test_evento_sin_payload_se_sincroniza_pero_no_repinta_con_modal(frame):
    frame.modal = True
    _evento(frame, rest=True)
    assert [k for k, _cb in frame.sincronizados] == ["home"]
    _k, cb = frame.sincronizados[0]
    cb(["toot"], None)          # llega la respuesta con el compositor aún abierto
    assert frame.insertados == 0 and frame._pendiente_tras_modal


def test_el_tick_del_temporizador_no_hace_nada_con_modal(frame):
    frame.modal = True
    llamado = []
    frame.lista_items = type("L", (), {"refrescar_todas_las_fechas": lambda s: llamado.append(1)})()
    frame._on_auto_timer(None)
    assert llamado == [] and frame._pendiente_tras_modal


def test_recuperacion_tras_reconectar_solo_sincroniza_con_modal(frame):
    frame.modal = True
    frame._catchup_streaming_mastodon(1)
    assert [k for k, _cb in frame.sincronizados] == ["home"]
    _k, cb = frame.sincronizados[0]
    frame.modal = False         # el usuario publicó y cerró antes de que llegara la respuesta
    cb(["toot"], None)
    assert frame.insertados == 1
