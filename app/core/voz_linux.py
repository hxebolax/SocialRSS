# -*- coding: utf-8 -*-
"""
Salida de voz para Linux, con la misma forma que accessible_output2 (`speak` y `braille`).

Orden de preferencia:
  1. Anuncio por la propia interfaz (en GTK 4, `Gtk.Accessible.announce`): lo recibe Orca por
     AT-SPI como una región viva, con su voz, velocidad y braille. Es el camino normal.
  2. Orca por D-Bus (`org.gnome.Orca.Service`), si Orca está en marcha y no hay interfaz.
  3. speech-dispatcher (`spd-say`), para quien no usa lector de pantalla pero quiere avisos.
"""

import logging
import shutil
import subprocess

from . import plataforma

log = logging.getLogger(__name__)


class SalidaLinux:
    def __init__(self):
        self._spd = shutil.which("spd-say")
        self._proc = None

    def speak(self, text: str, interrupt: bool = True) -> bool:
        if plataforma.anunciar(text, interrupt):
            return True
        if self._orca_dbus(text):
            return True
        return self._spd_say(text, interrupt)

    def braille(self, text: str) -> bool:
        # Orca ya envía a braille lo que anuncia por AT-SPI.
        return False

    def _orca_dbus(self, text: str) -> bool:
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = bus.call_sync("org.gnome.Orca.Service", "/org/gnome/Orca/Service",
                                "org.gnome.Orca.Service", "PresentMessage",
                                GLib.Variant("(s)", (text,)), None,
                                Gio.DBusCallFlags.NO_AUTO_START, 500, None)
            return res is not None
        except Exception:
            return False

    def _spd_say(self, text: str, interrupt: bool) -> bool:
        if not self._spd:
            return False
        try:
            cmd = [self._spd, "-e"]
            if interrupt:
                cmd.append("-C")
            subprocess.Popen(cmd + ["--", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            log.debug(f"spd-say falló: {e}")
            return False
