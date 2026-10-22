# -*- coding: utf-8 -*-
"""Pruebas del PUNTO DE LECTURA de las conversaciones de Telegram.

Cubren la lógica pura de _frontera_inicial: dónde queda la marca de "leído hasta aquí" al abrir
un chat. El fallo que arreglan estas pruebas era que la frontera se deducía restando unread_count
al total de filas cargadas, sin tener en cuenta que unread_count NO cuenta tus mensajes salientes:
con mensajes tuyos en el tramo final, la marca retrocedía y el chat te dejaba varios mensajes por
detrás del último que habías leído de verdad.
"""
from app.ui.telegram_conv_mensajes import _ConvMensajesMixin


def _fila(message_id, outgoing=0):
    """Fila mínima con la forma que consulta _cargar_mensajes_cache: el id va en [0] y
    'outgoing' en [5]."""
    return (message_id, "Alguien", "alguien", "texto", "2026-07-31", outgoing, None, None,
            0, "", 0, None, "", 0, "")


frontera = _ConvMensajesMixin._frontera_inicial


def test_manda_el_punto_guardado():
    """Si hay last_read_message_id guardado, es el que vale (aunque el contador diga otra cosa)."""
    rows = [_fila(i) for i in (10, 11, 12, 13, 14)]
    assert frontera(rows, 3, 12) == 12


def test_sin_no_leidos_la_frontera_es_el_ultimo():
    rows = [_fila(i) for i in (10, 11, 12)]
    assert frontera(rows, 0, 0) == 12


def test_deduccion_sin_mensajes_propios():
    """Sin salientes de por medio, la deducción clásica sigue dando el mismo resultado."""
    rows = [_fila(i) for i in (10, 11, 12, 13, 14)]
    assert frontera(rows, 2, 0) == 12          # 13 y 14 sin leer


def test_los_mensajes_propios_no_desplazan_la_marca():
    """El caso del fallo: dos entrantes sin leer, pero con mensajes TUYOS intercalados al final.

    unread_count = 2 cuenta solo los entrantes (13 y 15). Restando a ciegas, la frontera caía en
    el 12 y el chat te devolvía dos mensajes atrás; contando solo entrantes, cae donde debe."""
    rows = [_fila(10), _fila(11), _fila(12), _fila(13), _fila(14, outgoing=1), _fila(15)]
    assert frontera(rows, 2, 0) == 12


def test_todo_lo_cargado_sin_leer():
    """Si hay más no leídos que mensajes cargados, no hay nada leído."""
    rows = [_fila(i) for i in (10, 11)]
    assert frontera(rows, 5, 0) == 0


def test_chat_vacio():
    assert frontera([], 0, 0) == 0
    assert frontera([], 3, 77) == 77
