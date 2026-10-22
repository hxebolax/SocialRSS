# -*- coding: utf-8 -*-
"""Norma de saltos de línea del proyecto: LF en todo y CRLF solo en .bat/.cmd (.editorconfig).

El proyecto llegó a tener 74 archivos .py en CRLF, uno mezclado y un .bat en LF, fruto de
herramientas distintas. Este test avisa si vuelve a entrar algo fuera de la norma (por ejemplo, al
restaurar una copia antigua). Para arreglarlo de una vez:  python tools/normalizar_saltos.py"""
import importlib.util
import os

_RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools", "normalizar_saltos.py")
_spec = importlib.util.spec_from_file_location("normalizar_saltos", _RUTA)
normalizar_saltos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(normalizar_saltos)


def test_todo_el_proyecto_cumple_la_norma_de_saltos():
    malos = normalizar_saltos.revisar(corregir=False)
    assert not malos, ("Archivos con saltos de línea fuera de la norma (arréglalo con "
                       "'python tools/normalizar_saltos.py'): " + ", ".join(malos))


def test_normalizado_solo_toca_los_saltos():
    n = normalizar_saltos.normalizado
    assert n(b"a\r\nb\nc\rd", b"\n") == b"a\nb\nc\nd"
    assert n(b"a\nb\r\n", b"\r\n") == b"a\r\nb\r\n"
    assert normalizar_saltos.fin_esperado("x/build.bat") == b"\r\n"
    assert normalizar_saltos.fin_esperado("x/modulo.py") == b"\n"
    assert normalizar_saltos.fin_esperado("x/sonido.wav") is None
