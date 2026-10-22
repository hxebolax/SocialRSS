# -*- coding: utf-8 -*-
"""Bandeja de entrada: los sondeos por red NO pueden insertar sus filas encima de la bandeja.

Mientras un menú contextual o "Ver publicación" de la bandeja están abiertos sobre una fila
RSS, _fijar_contexto_bandeja cambia temporalmente _modo_lista y la cuenta activa a los de esa
fila. Si el temporizador disparaba el sondeo de RSS en esa ventana, su callback insertaba los
artículos en la LISTA DE LA BANDEJA con las columnas de la bandeja: como el diccionario de
valores de RSS solo aporta {author} y {date}, las filas quedaban como "Fulanito; hace 7
horas" y nada más (segunda tanda de "publicaciones fantasma" reportada), además de
desalinear la lista paralela de orígenes. Aquí se comprueba que los callbacks tardíos de
RSS, Mastodon y Telegram no tocan la lista cuando la bandeja está activa.
"""
import types

from app.ui.mf_acciones_toot import _Mf_acciones_toot, rss_service


class _FrameFalso(_Mf_acciones_toot):
    """Doble mínimo: registra si el callback intentó tocar la lista."""

    def __init__(self):
        self._updating = False
        self.active_rss_account_id = "cuenta-rss"
        self._bandeja_activa = True
        self._modo_lista = "rss"  # contexto temporal de la bandeja fijado sobre una fila RSS
        self.toco_la_lista = False

    def _actualizar_contadores_rss(self):
        pass

    def _filas_rss_actual(self):
        self.toco_la_lista = True
        return [], {}

    def _insertar_items_incremental(self, filas):
        self.toco_la_lista = True
        return 0

    def _actualizar_lista_chats(self, filas):
        self.toco_la_lista = True
        return 0

    def _filas_telegram_actual(self):
        self.toco_la_lista = True
        return []


def _capturar_callback_rss(monkeypatch, frame):
    """Lanza _actualizar_rss_incremental capturando el callback que registra en rss_service."""
    capturado = {}

    def _falso_async(cuenta, callback=None, **kwargs):
        capturado["cb"] = callback

    monkeypatch.setattr(rss_service, "actualizar_todos_async", _falso_async)
    frame._actualizar_rss_incremental()
    return capturado["cb"]


def test_callback_rss_no_inserta_con_la_bandeja_activa(monkeypatch):
    frame = _FrameFalso()
    cb = _capturar_callback_rss(monkeypatch, frame)
    cb(5, None)  # llegaron 5 artículos nuevos con la bandeja delante
    assert not frame.toco_la_lista


def test_callback_rss_no_inserta_si_ya_no_estas_en_rss(monkeypatch):
    frame = _FrameFalso()
    frame._bandeja_activa = False
    frame._modo_lista = "toots"  # el usuario navegó a otra red antes de llegar el callback
    cb = _capturar_callback_rss(monkeypatch, frame)
    cb(5, None)
    assert not frame.toco_la_lista


def test_callback_rss_si_inserta_en_un_buffer_rss(monkeypatch):
    frame = _FrameFalso()
    frame._bandeja_activa = False
    frame._modo_lista = "rss"
    cb = _capturar_callback_rss(monkeypatch, frame)
    cb(5, None)
    assert frame.toco_la_lista


def test_callback_telegram_no_toca_la_lista_con_la_bandeja_activa(monkeypatch):
    frame = _FrameFalso()
    frame.active_telegram_account_id = "cuenta-tg"
    capturado = {}

    from app.ui.mf_acciones_toot import task_manager

    def _falso_start_task(fn, callback=None, **kwargs):
        capturado["cb"] = callback

    monkeypatch.setattr(task_manager, "start_task", _falso_start_task)
    frame._actualizar_telegram_incremental()
    capturado["cb"](3, None)  # llegaron chats nuevos con la bandeja delante
    assert not frame.toco_la_lista
