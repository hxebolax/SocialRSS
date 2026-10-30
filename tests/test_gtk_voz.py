# -*- coding: utf-8 -*-
"""Notas de voz de la interfaz GTK: alternar grabar y enviar, cancelar y no mandarla al chat equivocado (sin audio ni pantalla)."""
import pytest

pytest.importorskip("gi")
from app.gtk import ventana_voz  # noqa: E402


class _Servicio:
    def __init__(self):
        self.llamadas, self.fallo = [], None

    def iniciar_grabacion_voz(self):
        if self.fallo:
            raise RuntimeError(self.fallo)
        self.llamadas.append("iniciar")
        return "/tmp/voz.wav"

    def cancelar_grabacion_voz(self):
        self.llamadas.append("cancelar")


class _Ventana(ventana_voz._VozMixin):
    def __init__(self):
        self._voz = None
        self.dicho, self.enviadas, self.refrescos = [], [], 0

    def avisar(self, texto, interrumpir=True):
        self.dicho.append(texto)


@pytest.fixture
def v(monkeypatch):
    # `ejecutar` lanza un hilo y devuelve el resultado en el de la interfaz; aquí se resuelve en el acto.
    def _sync(fn, cb):
        try:
            cb(fn(), None)
        except Exception as e:      # noqa: BLE001
            cb(None, e)
    monkeypatch.setattr(ventana_voz, "ejecutar", _sync)
    monkeypatch.setattr(ventana_voz.GLib, "timeout_add_seconds", lambda *a: 7)
    monkeypatch.setattr(ventana_voz.GLib, "source_remove", lambda i: True)
    monkeypatch.setattr(ventana_voz.event_bus, "emit", lambda *a, **k: None)
    return _Ventana()


def _alternar(v, s, clave=("matrix", 1, "!a"), titulo="Sala"):
    v._voz_alternar(s, clave, titulo, lambda: v.enviadas.append(clave), lambda: setattr(v, "refrescos", v.refrescos + 1))


def test_primera_pulsacion_graba_y_la_segunda_envia(v):
    s = _Servicio()
    _alternar(v, s)
    assert s.llamadas == ["iniciar"] and v._voz is not None and "Grabando para Sala" in v.dicho[-1]
    _alternar(v, s)
    assert v.enviadas == [("matrix", 1, "!a")] and v._voz is None and v.refrescos == 1
    assert v.dicho[-1] == "Nota de voz enviada"


def test_no_se_envia_a_otro_chat(v):
    s = _Servicio()
    _alternar(v, s)
    _alternar(v, s, clave=("matrix", 1, "!b"), titulo="Otra")
    assert v.enviadas == [] and v._voz is not None and "Estás grabando una nota de voz para Sala" in v.dicho[-1]


def test_cancelar(v):
    s = _Servicio()
    v.voz_cancelar()
    assert v.dicho[-1] == "No hay ninguna grabación en curso"
    _alternar(v, s)
    v.voz_cancelar()
    assert s.llamadas == ["iniciar", "cancelar"] and v._voz is None and v.enviadas == []


def test_fallo_al_abrir_el_micro_no_deja_grabacion_a_medias(v):
    s = _Servicio()
    s.fallo = "No se pudo abrir el micrófono"
    _alternar(v, s)
    assert v._voz is None and "No se pudo empezar a grabar" in v.dicho[-1]


def test_caduca_a_los_diez_minutos(v):
    s = _Servicio()
    _alternar(v, s)
    v._voz_caducada()
    assert v._voz is None and s.llamadas[-1] == "cancelar" and "10 minutos" in v.dicho[-1]
