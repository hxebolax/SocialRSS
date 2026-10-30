# -*- coding: utf-8 -*-
"""Registro de la interfaz GTK 4 en la capa de plataforma (app.core.plataforma)."""

import logging
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib, Gio, Gtk, Gdk  # noqa: E402

from app.core.plataforma import registrar_interfaz

log = logging.getLogger(__name__)

_app = None


def _ventana_activa():
    if _app is None:
        return None
    return _app.get_active_window() or (_app.get_windows() or [None])[0]


def llamar_en_ui(fn, *args, **kwargs):
    def _correr():
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("Error en una llamada diferida al hilo de la interfaz")
        return GLib.SOURCE_REMOVE
    GLib.idle_add(_correr)


def es_hilo_ui() -> bool:
    return GLib.MainContext.default().is_owner() or threading.current_thread() is threading.main_thread()


def anunciar(texto: str, interrumpir: bool = True) -> bool:
    """Anuncia un texto por AT-SPI (región viva): Orca lo locuta y lo saca por braille."""
    ventana = _ventana_activa()
    if ventana is None or not hasattr(ventana, "announce"):
        return False
    prioridad = (Gtk.AccessibleAnnouncementPriority.HIGH if interrumpir
                 else Gtk.AccessibleAnnouncementPriority.MEDIUM)

    def _anunciar():
        try:
            ventana.announce(texto, prioridad)
        except Exception as e:
            log.debug(f"announce falló: {e}")
        return GLib.SOURCE_REMOVE
    GLib.idle_add(_anunciar)
    return True


def _esperar(montar):
    """Ejecuta un diálogo asíncrono de GTK 4 como si fuera modal síncrono (bucle anidado)."""
    bucle = GLib.MainLoop()
    resultado = {}

    def _listo(valor):
        resultado["valor"] = valor
        bucle.quit()
    montar(_listo)
    bucle.run()
    return resultado.get("valor")


def mostrar_aviso(texto: str, titulo: str = None):
    dlg = Gtk.AlertDialog()
    dlg.set_message(titulo or "SocialRSS Accessible Suite")
    dlg.set_detail(texto)
    dlg.set_buttons([_("Aceptar")])
    dlg.set_default_button(0)
    dlg.set_cancel_button(0)
    dlg.choose(_ventana_activa(), None, lambda *_a: None)


def confirmar(texto: str, titulo: str = "") -> bool:
    def _montar(listo):
        dlg = Gtk.AlertDialog()
        dlg.set_message(titulo or "SocialRSS")
        dlg.set_detail(texto)
        dlg.set_buttons([_("No"), _("Sí")])
        dlg.set_default_button(0)
        dlg.set_cancel_button(0)

        def _fin(origen, res):
            try:
                listo(origen.choose_finish(res) == 1)
            except GLib.Error:
                listo(False)
        dlg.choose(_ventana_activa(), None, _fin)
    return bool(_esperar(_montar))


def mostrar_toast(texto: str, titulo: str = None):
    if _app is None:
        return
    n = Gio.Notification.new(titulo or "SocialRSS")
    n.set_body(texto)
    _app.send_notification(None, n)


def copiar(texto: str) -> bool:
    display = Gdk.Display.get_default()
    if display is None:
        return False
    display.get_clipboard().set(texto)
    return True


def registrar(app):
    """Registra GTK como interfaz activa. `app` es la Gtk.Application."""
    global _app
    _app = app
    registrar_interfaz(
        "gtk",
        llamar_en_ui=llamar_en_ui,
        es_hilo_ui=es_hilo_ui,
        hay_interfaz=lambda: _app is not None,
        anunciar=anunciar,
        mostrar_aviso=mostrar_aviso,
        mostrar_toast=mostrar_toast,
        confirmar=confirmar,
        copiar=copiar,
    )
