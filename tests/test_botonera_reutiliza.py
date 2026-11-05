# -*- coding: utf-8 -*-
"""La botonera de acciones REUTILIZA sus botones al navegar (lag con NVDA, 2026-11-03).

Antes se destruían y creaban todos los botones en cada flecha; cada ventana nativa nueva
inundaba a NVDA de eventos de accesibilidad. Sin mostrar ventanas ni mover el foco."""
import wx
import pytest

from app.ui.mf_acciones_toot import _Mf_acciones_toot


class _Frame(_Mf_acciones_toot):
    def __init__(self, padre):
        self.panel_der = wx.Panel(padre)
        self.panel_botonera = wx.Panel(self.panel_der)
        self.sizer_botonera = wx.WrapSizer(wx.HORIZONTAL)
        self.panel_botonera.SetSizer(self.sizer_botonera)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(self.panel_botonera)
        self.panel_der.SetSizer(s)
        self.panel_botonera.Hide()
        self._bandeja_activa = False
        self.botones = []
        self.layouts = 0
        original = self.panel_der.Layout

        def _layout():
            self.layouts += 1
            return original()
        self.panel_der.Layout = _layout

    def _botonera_activa(self):
        return True

    def _botones_botonera(self):
        return list(self.botones)


@pytest.fixture
def frame():
    app = wx.App.Get() or wx.App(False)
    padre = wx.Frame(None)
    f = _Frame(padre)
    yield f
    padre.Destroy()
    del app


def _ventanas(f):
    return list(f.panel_botonera.GetChildren())


def test_misma_botonera_no_crea_ni_destruye_ventanas(frame):
    pulsado = []
    frame.botones = [("Responder", lambda: pulsado.append("r1")), ("Favorito", lambda: None)]
    frame._actualizar_botonera()
    antes = _ventanas(frame)
    layouts = frame.layouts
    assert [b.GetLabel() for b in antes] == ["Responder", "Favorito"]
    # Siguiente elemento: mismas etiquetas, acciones nuevas.
    frame.botones = [("Responder", lambda: pulsado.append("r2")), ("Favorito", lambda: None)]
    frame._actualizar_botonera()
    assert _ventanas(frame) == antes            # las mismas ventanas, ninguna nueva
    assert frame.layouts == layouts             # y sin recolocar el panel
    frame._pulsar_botonera(0)
    assert pulsado == ["r2"]                    # el clic usa la acción del elemento ACTUAL


def test_cambia_etiqueta_y_numero_de_botones(frame):
    frame.botones = [("Responder", lambda: None), ("Favorito", lambda: None), ("Impulsar", lambda: None)]
    frame._actualizar_botonera()
    primero = _ventanas(frame)[0]
    frame.botones = [("Responder", lambda: None), ("Quitar favorito", lambda: None)]
    frame._actualizar_botonera()
    v = _ventanas(frame)
    assert v[0] is primero
    assert [b.GetLabel() for b in v] == ["Responder", "Quitar favorito"]
    frame.botones = []
    frame._actualizar_botonera()
    assert not frame.panel_botonera.IsShown()
