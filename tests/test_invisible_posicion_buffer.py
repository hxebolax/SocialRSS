# -*- coding: utf-8 -*-
"""Interfaz invisible: al cambiar de buffer se anuncia también "X de N", como TWBlue
("Inicio, 3 de 120"), si la opción de Preferencias > Accesibilidad está activada."""
import app.ui.mf_servicios_feeds as mod_feeds
from app.ui.mf_servicios_feeds import _Mf_servicios_feeds
from app.ui.mf_interfaz_invisible import _Mf_interfaz_invisible


class _Nodo:
    def IsOk(self):
        return True


class _Lista:
    def __init__(self, n, foco):
        self.n, self.foco = n, foco

    def GetItemCount(self):
        return self.n

    def GetFocusedItem(self):
        return self.foco

    def SetFocus(self):
        pass


class _Arbol:
    def EnsureVisible(self, nodo):
        pass

    def SelectItem(self, nodo):
        pass


class _Frame(_Mf_servicios_feeds, _Mf_interfaz_invisible):
    def __init__(self, n, foco):
        self.lista_items = _Lista(n, foco)
        self.tree_nav = _Arbol()
        self._ultimo_panel_servicio = {}

    def _guardar_foco_buffer(self):
        pass

    def _servicio_activo(self):
        return "mastodon"


def _anuncio(monkeypatch, frame, **kw):
    dicho = []
    monkeypatch.setattr(mod_feeds, "speak", lambda t, **k: dicho.append(t))
    frame._ir_a_buffer_nodo(_Nodo(), "Inicio", **kw)
    return dicho[-1]


def test_con_posicion_dice_elemento_y_total(monkeypatch):
    assert _anuncio(monkeypatch, _Frame(120, 2), con_posicion=True) == "Inicio, 3 de 120"


def test_sin_posicion_solo_el_nombre(monkeypatch):
    assert _anuncio(monkeypatch, _Frame(120, 2)) == "Inicio"


def test_buffer_vacio_o_cargando_solo_el_nombre(monkeypatch):
    assert _anuncio(monkeypatch, _Frame(0, -1), con_posicion=True) == "Inicio"


def test_sin_foco_cuenta_desde_el_primero(monkeypatch):
    assert _anuncio(monkeypatch, _Frame(5, -1), con_posicion=True) == "Inicio, 1 de 5"


def test_opcion_de_preferencias(monkeypatch):
    frame = _Frame(1, 0)
    monkeypatch.setattr("app.ui.mf_interfaz_invisible.config.get",
                        lambda sec, clave, defecto=None: False
                        if clave == "invisible_anunciar_posicion" else defecto)
    assert frame._iv_con_posicion() is False
