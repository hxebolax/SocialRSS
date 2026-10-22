# -*- coding: utf-8 -*-
"""Pruebas del catálogo de teclas de los REPRODUCTORES.

Las teclas de los reproductores se capturan con un filtro de eventos de toda la aplicación, así
que una tecla "escribible" (una letra o un número sueltos) se comería la pulsación en cualquier
campo de texto. Estas pruebas fijan esa regla y el resto de invariantes del catálogo.
"""
import pytest

wx = pytest.importorskip("wx")

from app.ui.shortcut_manager import (ACCIONES_REPRODUCTOR, CATALOGO, DEFAULT_SHORTCUTS,
                                     SEC_REPRODUCTOR, ShortcutManager, TECLAS_RESERVADAS)


def test_las_ocho_acciones_del_reproductor_estan_en_el_catalogo():
    assert len(ACCIONES_REPRODUCTOR) == 8
    esperadas = {"media_retroceder", "media_play_pausa", "media_avanzar", "media_detener",
                 "media_bajar_volumen", "media_subir_volumen",
                 "media_bajar_velocidad", "media_subir_velocidad"}
    assert set(ACCIONES_REPRODUCTOR) == esperadas


def test_las_teclas_por_defecto_siguen_siendo_f1_a_f8():
    """Personalizar no cambia lo que ya conocen los usuarios: por defecto, F1..F8 en orden."""
    assert [DEFAULT_SHORTCUTS[a] for a in ACCIONES_REPRODUCTOR] == \
        ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"]


def test_f1_a_f8_ya_no_son_reservadas():
    """Estaban bloqueadas porque no se podían editar; ahora son acciones del catálogo."""
    for f in ("f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8"):
        assert f not in TECLAS_RESERVADAS


def test_la_seccion_del_reproductor_se_ofrece_en_el_editor():
    sm = ShortcutManager()
    assert SEC_REPRODUCTOR in sm.secciones()
    assert sm.acciones_de(SEC_REPRODUCTOR) == ACCIONES_REPRODUCTOR


@pytest.mark.parametrize("combo,valido", [
    ("F1", True), ("F12", True), ("Ctrl+P", True), ("Ctrl+Shift+M", True), ("Alt+K", True),
    ("A", False),          # una letra suelta se comería lo que escribes
    ("5", False),
    ("F13", False),        # fuera del rango de teclas de función
    ("", False),
])
def test_solo_valen_teclas_de_funcion_o_con_modificador(combo, valido):
    assert ShortcutManager.combo_valido_reproductor(combo) is valido


def test_no_se_asigna_una_tecla_escribible_a_un_reproductor():
    sm = ShortcutManager()
    assert sm.establecer_atajo("media_play_pausa", "J") is False
    assert sm.obtener_atajo("media_play_pausa") == "F2"     # sigue como estaba


def test_una_tecla_del_reproductor_choca_con_el_resto_del_catalogo():
    """Al salir de TECLAS_RESERVADAS, lo que impide pisarlas es la detección de conflictos."""
    sm = ShortcutManager()
    assert sm.conflicto("actualizar_buffer", "F5") == "media_bajar_volumen"


def test_ninguna_tecla_por_defecto_esta_repetida():
    # Las "Más acciones" vienen sin tecla de fábrica (""): eso no es una repetición.
    combos = [tecla.lower() for _acc, _et, _sec, tecla in CATALOGO if tecla]
    assert len(combos) == len(set(combos))


def test_las_mas_acciones_vienen_sin_tecla_y_no_chocan():
    from app.ui.shortcut_manager import SEC_MAS
    sm = ShortcutManager()
    mas = sm.acciones_de(SEC_MAS)
    assert len(mas) >= 6 and SEC_MAS in sm.secciones()
    for acc in mas:
        assert sm.obtener_atajo(acc) == ""
    # Asignar una tecla libre no choca con nada; una ocupada sí se detecta.
    assert sm.conflicto("pub_impulsar", "Ctrl+Alt+Shift+I") is None
    assert sm.conflicto("pub_impulsar", "Ctrl+R") == "pub_responder"


def test_el_mapa_de_teclas_del_filtro_se_rehace_al_cambiar_una():
    """El filtro de eventos consulta un mapa cacheado en cada pulsación: debe quedar invalidado
    cuando el usuario cambia una tecla, o el reproductor seguiría respondiendo a la vieja."""
    sm = ShortcutManager()
    antes = dict(sm._mapa_reproductor())
    assert antes[(wx.MOD_NONE, wx.WXK_F2)] == "media_play_pausa"
    assert sm.establecer_atajo("media_play_pausa", "Ctrl+Alt+F2") is True
    try:
        despues = sm._mapa_reproductor()
        assert (wx.MOD_NONE, wx.WXK_F2) not in despues
        assert despues[(wx.MOD_CONTROL | wx.MOD_ALT, wx.WXK_F2)] == "media_play_pausa"
    finally:
        sm.restaurar("media_play_pausa")
