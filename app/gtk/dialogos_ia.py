# -*- coding: utf-8 -*-
"""Diálogos de IA y traducción: prompts, historiales y visor de resultados."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import notificar

from .dialogos import confirmar_async
from .dialogos_mastodon import DialogoLista
from .dialogos_mastodon_extra import DialogoTextoLargo

log = logging.getLogger(__name__)


def _leer(tv: Gtk.TextView) -> str:
    b = tv.get_buffer()
    return b.get_text(b.get_start_iter(), b.get_end_iter(), False)


class DialogoPrompts(Gtk.Window):
    """Los cuatro textos que se envían a la IA junto con lo que analiza."""

    CLAVES = [("prompt_image_description", "Descripción de imágenes"), ("prompt_alt_text", "Texto alternativo"),
              ("prompt_text_summary", "Resúmenes"), ("prompt_hashtags", "Hashtags")]

    def __init__(self, padre):
        super().__init__(title=_("Prompts de IA"), transient_for=padre, modal=True)
        self.set_default_size(640, 520)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        self.set_child(caja)
        cuaderno = Gtk.Notebook(vexpand=True)
        cuaderno.update_property([Gtk.AccessibleProperty.LABEL], [_("Prompts de IA")])
        caja.append(cuaderno)
        self.campos = {}
        for clave, titulo in self.CLAVES:
            tv = Gtk.TextView(accepts_tab=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
            tv.get_buffer().set_text(config.get("ai", clave, ""))
            tv.update_property([Gtk.AccessibleProperty.LABEL], [_("Prompt de {t}").format(t=_(titulo).lower())])
            cuaderno.append_page(Gtk.ScrolledWindow(child=tv, has_frame=True), Gtk.Label(label=_(titulo)))
            self.campos[clave] = tv
        botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        c = Gtk.Button(label=_("_Cancelar"), use_underline=True)
        c.connect("clicked", lambda *_a: self.close())
        g = Gtk.Button(label=_("_Guardar"), use_underline=True)
        g.connect("clicked", lambda *_a: self._guardar())
        botones.append(c)
        botones.append(g)
        caja.append(botones)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)

    def _guardar(self):
        for clave, tv in self.campos.items():
            config.set("ai", clave, _leer(tv))
        notificar(_("Prompts guardados"))
        self.close()


class DialogoHistorialIA(DialogoLista):
    def __init__(self, padre):
        super().__init__(padre, _("Historial de IA"), _("Lista de operaciones de IA"), al_activar=lambda pos: self._ver())
        self.filas = db.query("SELECT funcion, entrada, respuesta, created_at FROM ai_history ORDER BY id DESC LIMIT 200")
        self.lista.poner([f"{(f[3] or '')[:16]} — {f[0]}: {' '.join((f[1] or '').split())[:80]}" for f in self.filas])
        self.mostrar_estado(_("{n} operaciones").format(n=len(self.filas)) if self.filas else _("El historial está vacío"))
        self.boton(_("_Ver"), self._ver)
        self.boton(_("_Vaciar el historial"), self._vaciar)
        self.cerrar_boton()

    def _ver(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.filas):
            f = self.filas[p]
            DialogoTextoLargo(self, f[0], _("Entrada:") + "\n" + (f[1] or "") + "\n\n" + _("Respuesta:") + "\n" + (f[2] or "")).present()

    def _vaciar(self):
        def _hacer():
            db.execute("DELETE FROM ai_history")
            self.filas = []
            self.lista.poner([])
            self.mostrar_estado(_("El historial está vacío"))
        confirmar_async(self, _("¿Vaciar el historial de IA?"), _hacer, etiqueta_si=_("Vaciar"))


class DialogoHistorialTraducciones(DialogoLista):
    def __init__(self, padre):
        from app.services.translation.translation_service import translation_service
        super().__init__(padre, _("Historial de traducciones"), _("Lista de traducciones"), al_activar=lambda pos: self._ver())
        self.servicio = translation_service
        self.filas = translation_service.obtener_historial(200)
        self.lista.poner([f"{(f['created_at'] or '')[:16]} — {f['source_lang']} a {f['target_lang']}: "
                          f"{' '.join((f['original_text'] or '').split())[:80]}" for f in self.filas])
        self.mostrar_estado(_("{n} traducciones").format(n=len(self.filas)) if self.filas else _("El historial está vacío"))
        self.boton(_("_Ver"), self._ver)
        self.boton(_("_Copiar la traducción"), self._copiar)
        self.boton(_("_Vaciar el historial"), self._vaciar)
        self.cerrar_boton()

    def _actual(self):
        p = self.lista.posicion()
        return self.filas[p] if 0 <= p < len(self.filas) else None

    def _ver(self):
        f = self._actual()
        if f:
            DialogoTextoLargo(self, _("Traducción"), f["original_text"] + "\n\n" + f["translated_text"]).present()

    def _copiar(self):
        f = self._actual()
        if f:
            from app.core.speech import copiar_al_portapapeles
            copiar_al_portapapeles(f["translated_text"], _("Traducción copiada"))

    def _vaciar(self):
        def _hacer():
            self.servicio.borrar_historial()
            self.filas = []
            self.lista.poner([])
            self.mostrar_estado(_("El historial está vacío"))
        confirmar_async(self, _("¿Vaciar el historial de traducciones?"), _hacer, etiqueta_si=_("Vaciar"))
