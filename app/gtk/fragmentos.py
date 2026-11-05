# -*- coding: utf-8 -*-
"""Ayudas de los campos de redacción: Ctrl+Mayús+J inserta un fragmento de texto guardado (en `general/snippets` de la
configuración, los mismos que usa la versión de Windows) y F7 revisa la ortografía."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar


def obtener_fragmentos() -> list:
    datos = config.get("general", "snippets", [])
    return [d for d in datos if isinstance(d, dict) and d.get("texto")]


def instalar_fragmentos(vista: Gtk.TextView):
    """Activa Ctrl+Mayús+J en un campo de redacción. Llamar una vez por campo."""
    if vista is None or getattr(vista, "_fragmentos_instalado", False):
        return
    vista._fragmentos_instalado = True

    def _insertar(*_a):
        if not obtener_fragmentos():
            notificar(_("No tienes fragmentos guardados. Créalos desde Herramientas → «Fragmentos de texto»."))
            return True
        from .dialogos_herramientas import DialogoElegirFragmento

        def _poner(texto):
            buf = vista.get_buffer()
            buf.delete_selection(True, True)
            buf.insert_at_cursor(texto)
            vista.grab_focus()
            notificar(_("Fragmento insertado."))
        DialogoElegirFragmento(vista.get_root(), _poner).present()
        return True
    ctl = Gtk.ShortcutController()
    ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl><Shift>j"), Gtk.CallbackAction.new(_insertar)))
    vista.add_controller(ctl)


def instalar_ortografia(vista: Gtk.TextView):
    """Activa F7 en un campo de redacción: revisa la ortografía palabra por palabra. Llamar una vez por campo."""
    if vista is None or getattr(vista, "_ortografia_instalada", False):
        return
    vista._ortografia_instalada = True

    def _revisar(*_a):
        from app.services import spellcheck
        if not spellcheck.disponible():
            notificar(_("La corrección ortográfica no está disponible."))
            return True
        buf = vista.get_buffer()
        texto = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if not texto.strip():
            notificar(_("No hay texto que revisar."))
            return True
        from .dialogos_herramientas import DialogoOrtografia

        def _corregido(nuevo):
            if nuevo != texto:
                buf.set_text(nuevo)
            vista.grab_focus()
        DialogoOrtografia(vista.get_root(), texto, _corregido).present()
        return True
    ctl = Gtk.ShortcutController()
    ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("F7"), Gtk.CallbackAction.new(_revisar)))
    vista.add_controller(ctl)


def instalar_ayudas(vista: Gtk.TextView):
    """Todas las ayudas de un campo de redacción (fragmentos y ortografía)."""
    instalar_fragmentos(vista)
    instalar_ortografia(vista)
