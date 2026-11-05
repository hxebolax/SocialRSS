# -*- coding: utf-8 -*-
"""Editor de atajos de teclado de la interfaz GTK: cada acción de la ventana puede tener la combinación que prefiera el
usuario (o ninguna). Los cambios se aplican al momento y se guardan en la configuración (`gtk/atajos`)."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar

from .dialogos import confirmar_async
from .dialogos_mastodon import DialogoLista

log = logging.getLogger(__name__)

MODIFICADORES = (Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
                 Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R, Gdk.KEY_ISO_Level3_Shift,
                 Gdk.KEY_Caps_Lock, Gdk.KEY_Num_Lock)


def etiquetas_de_menu(modelo: Gio.MenuModel, acumulado: dict = None) -> dict:
    """{acción: etiqueta sin el subrayado del acelerador} de todos los elementos de un menú."""
    acumulado = {} if acumulado is None else acumulado
    for i in range(modelo.get_n_items()):
        accion = modelo.get_item_attribute_value(i, "action", None)
        etiqueta = modelo.get_item_attribute_value(i, "label", None)
        if accion is not None and etiqueta is not None:
            acumulado.setdefault(accion.get_string(), etiqueta.get_string().replace("_", "").rstrip("…").strip())
        for enlace in ("section", "submenu"):
            sub = modelo.get_item_link(i, enlace)
            if sub is not None:
                etiquetas_de_menu(sub, acumulado)
    return acumulado


def texto_atajo(acelerador: str) -> str:
    """'<Ctrl><Shift>r' → 'Ctrl+Mayús+R' (en el idioma de la interfaz)."""
    ok, tecla, mods = Gtk.accelerator_parse(acelerador)
    return Gtk.accelerator_get_label(tecla, mods) if ok else acelerador


def humanizar(accion: str) -> str:
    return accion.split(".", 1)[-1].replace("-", " ").capitalize()


class DialogoCapturaTecla(Gtk.Window):
    """Espera a que el usuario pulse la combinación que quiere. Escape cancela."""

    def __init__(self, padre, accion: str, al_elegir):
        super().__init__(title=_("Nueva tecla"), transient_for=padre, modal=True, resizable=False)
        self.al_elegir = al_elegir
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(16)
        self.set_child(caja)
        self.mensaje = Gtk.Label(wrap=True, width_chars=44, max_width_chars=44, xalign=0, label=_(
            "Pulsa ahora la combinación de teclas para «{a}». Escape cancela.").format(a=accion))
        caja.append(self.mensaje)
        ctl = Gtk.EventControllerKey()
        ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._tecla)
        self.add_controller(ctl)

    def _tecla(self, ctl, keyval, keycode, estado):
        if keyval in MODIFICADORES:
            return True
        mods = estado & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Escape and not mods:
            self.close()
            return True
        acelerador = Gtk.accelerator_name(keyval, mods)
        self.close()
        self.al_elegir(acelerador)
        return True


class DialogoAtajos(DialogoLista):
    """Lista de acciones con su tecla. Intro cambia la tecla de la acción elegida."""

    def __init__(self, padre):
        super().__init__(padre, _("Editor de atajos de teclado"), _("Lista de acciones y sus teclas"),
                         al_activar=lambda pos: self._cambiar())
        self.app = padre.get_application()
        self.defecto = dict(padre._atajos_defecto)
        from .aplicacion import _menu
        etiquetas = etiquetas_de_menu(_menu())
        self.acciones = sorted(self.defecto, key=lambda a: (etiquetas.get(f"win.{a}") or humanizar(a)).lower())
        self.etiquetas = {a: etiquetas.get(f"win.{a}") or humanizar(a) for a in self.acciones}
        self.boton(_("_Cambiar la tecla…"), self._cambiar)
        self.boton(_("_Quitar la tecla"), self._quitar)
        self.boton(_("R_establecer esta"), self._restablecer)
        self.boton(_("Restablecer _todas"), self._restablecer_todas)
        self.cerrar_boton()
        self._pintar()

    # ---- estado
    def _personalizados(self) -> dict:
        return dict(config.get("gtk", "atajos", {}) or {})

    def _teclas(self, accion: str) -> list:
        p = self._personalizados()
        return list(p[accion]) if accion in p else list(self.defecto.get(accion, []))

    def _pintar(self, pos: int = None):
        pos = self.lista.posicion() if pos is None else pos
        lineas = []
        for a in self.acciones:
            t = self._teclas(a)
            lineas.append(f"{self.etiquetas[a]}: " + (", ".join(texto_atajo(x) for x in t) if t else _("sin tecla")))
        self.lista.poner(lineas)
        if lineas:
            self.lista.seleccion.set_selected(max(0, min(pos, len(lineas) - 1)))
        self.mostrar_estado(_("{0} acciones. Intro cambia la tecla de la acción elegida.").format(len(lineas)))
        self.lista.enfocar()

    def _actual(self):
        p = self.lista.posicion()
        return self.acciones[p] if 0 <= p < len(self.acciones) else None

    def _aplicar(self, accion: str, teclas: list):
        p = self._personalizados()
        if teclas == list(self.defecto.get(accion, [])):
            p.pop(accion, None)
        else:
            p[accion] = teclas
        config.set("gtk", "atajos", p)
        self.app.set_accels_for_action(f"win.{accion}", teclas)

    def _usada_por(self, acelerador: str, salvo: str):
        for a in self.acciones:
            if a != salvo and acelerador in self._teclas(a):
                return a
        return None

    # ---- acciones
    def _cambiar(self):
        accion = self._actual()
        if accion is None:
            return

        def _elegida(acel):
            otra = self._usada_por(acel, accion)

            def _poner():
                if otra:
                    self._aplicar(otra, [x for x in self._teclas(otra) if x != acel])
                self._aplicar(accion, [acel])
                notificar(_("{a}: {t}").format(a=self.etiquetas[accion], t=texto_atajo(acel)))
                self._pintar()
            if otra:
                confirmar_async(self, _("Esa combinación ya la usa «{o}». ¿Quitársela y dársela a «{a}»?").format(
                    o=self.etiquetas[otra], a=self.etiquetas[accion]), _poner, etiqueta_si=_("Reasignar"))
            else:
                _poner()
        DialogoCapturaTecla(self, self.etiquetas[accion], _elegida).present()

    def _quitar(self):
        accion = self._actual()
        if accion is not None:
            self._aplicar(accion, [])
            notificar(_("{a}: sin tecla").format(a=self.etiquetas[accion]))
            self._pintar()

    def _restablecer(self):
        accion = self._actual()
        if accion is None:
            return
        p = self._personalizados()
        p.pop(accion, None)
        config.set("gtk", "atajos", p)
        self.app.set_accels_for_action(f"win.{accion}", list(self.defecto.get(accion, [])))
        notificar(_("{a}: tecla restablecida").format(a=self.etiquetas[accion]))
        self._pintar()

    def _restablecer_todas(self):
        def _hacer():
            config.set("gtk", "atajos", {})
            for a, t in self.defecto.items():
                self.app.set_accels_for_action(f"win.{a}", list(t))
            notificar(_("Todas las teclas restablecidas."))
            self._pintar()
        confirmar_async(self, _("¿Restablecer todas las teclas a sus valores por defecto?"), _hacer,
                        etiqueta_si=_("Restablecer"))
