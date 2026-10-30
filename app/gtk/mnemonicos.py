# -*- coding: utf-8 -*-
"""Que ninguna combinación Alt+letra se repita dentro de una ventana.

Los textos de botones y etiquetas llevan el guion bajo antes de su letra de acceso. En ventanas con muchos botones (o con campos y
botones a la vez) dos de ellos pueden coincidir y Alt+letra solo activa uno. Al mostrarse la ventana se revisa y, si hay
repetidos, el segundo y los siguientes pasan a otra letra libre de su propio texto."""

import re

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

_ACCESO = re.compile(r"_([^\W_])")


def _recorrer(w):
    c = w.get_first_child()
    while c is not None:
        yield c
        if not isinstance(c, Gtk.Button):       # lo de dentro de un botón (su etiqueta) es del propio botón
            yield from _recorrer(c)
        c = c.get_next_sibling()


def _con_texto(w):
    """(obtener, poner) del texto con guion bajo de un botón o una etiqueta, o None."""
    if isinstance(w, Gtk.Button) and w.get_use_underline() and not isinstance(w, (Gtk.CheckButton, Gtk.ToggleButton)):
        return w.get_label, w.set_label
    if isinstance(w, Gtk.CheckButton) and w.get_use_underline():
        return w.get_label, w.set_label
    if isinstance(w, Gtk.Label) and w.get_use_underline() and w.get_mnemonic_widget() is not None:
        return w.get_label, w.set_label
    return None


def asegurar_unicos(ventana: Gtk.Window):
    """Reasigna las letras de acceso repetidas de la ventana."""
    usadas = set()
    for w in _recorrer(ventana):
        acc = _con_texto(w)
        if acc is None:
            continue
        leer, poner = acc
        texto = leer() or ""
        m = _ACCESO.search(texto)
        if not m:
            continue
        letra = m.group(1).lower()
        if letra not in usadas:
            usadas.add(letra)
            continue
        limpio = _ACCESO.sub(lambda x: x.group(1), texto, count=1)
        nueva = next((i for i, ch in enumerate(limpio) if ch.isalnum() and ch.lower() not in usadas), None)
        if nueva is None:
            poner(limpio)       # sin letra libre: queda sin tecla de acceso antes que repetida
            continue
        usadas.add(limpio[nueva].lower())
        poner(limpio[:nueva] + "_" + limpio[nueva:])


def vigilar(ventana: Gtk.Window):
    """Revisa las letras de acceso la primera vez que se muestra la ventana."""
    def _mostrada(*_a):
        ventana.disconnect(ident)
        asegurar_unicos(ventana)
    ident = ventana.connect("map", _mostrada)
