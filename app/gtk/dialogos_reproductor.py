# -*- coding: utf-8 -*-
"""Ventana de control del segundo reproductor (radio, podcast y YouTube) en la interfaz GTK: reproducir y pausar, detener,
saltar, volumen, velocidad, capítulos, cola de podcasts, ecualizador y saltar silencios. Es una ventana normal (no modal)
para controlar el audio mientras se sigue usando la aplicación."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar
from app.services.media_player import cola_podcasts
from app.services.media_player.backends import BANDAS_EQ
from app.services.media_player.player2_service import KIND_RADIO, player2_service

from .dialogos import DialogoBase, confirmar_async
from .dialogos_mastodon import DialogoLista

log = logging.getLogger(__name__)

# Preajustes del ecualizador (dB por banda: 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k).
PREAJUSTES = [
    (_("Plano"), [0] * 10),
    (_("Voz (podcasts y charlas)"), [-6, -4, -2, 0, 2, 4, 4, 3, 1, 0]),
    (_("Más graves"), [6, 5, 4, 2, 0, 0, 0, 0, 0, 0]),
    (_("Más agudos"), [0, 0, 0, 0, 0, 1, 3, 5, 6, 6]),
    (_("Menos graves (voces retumbantes)"), [-8, -6, -4, -2, 0, 0, 0, 0, 0, 0]),
    (_("Música"), [4, 3, 1, 0, -1, 0, 1, 3, 4, 4]),
]
VELOCIDADES = [round(0.5 + 0.1 * i, 1) for i in range(26)]      # de 0,5x a 3x en pasos de 0,1


def hms(segundos: float) -> str:
    s = int(segundos or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _paso(clave: str) -> float:
    try:
        return float(config.get("media_player", clave, 15.0))
    except (TypeError, ValueError):
        return 15.0


class DialogoReproductor2(Gtk.Window):
    _abierta = None

    @classmethod
    def mostrar(cls, padre):
        """Abre la ventana o, si ya está abierta, la trae al frente."""
        if cls._abierta is not None:
            cls._abierta.present()
            return cls._abierta
        cls._abierta = cls(padre)
        cls._abierta.present()
        return cls._abierta

    def __init__(self, padre):
        super().__init__(title=_("Reproductor de radio, podcast y YouTube"), transient_for=padre)
        self.set_default_size(660, 380)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)
        self.pista = Gtk.Label(xalign=0, wrap=True, label=_("Nada en reproducción."))
        self.pista.update_property([Gtk.AccessibleProperty.LABEL], [_("Contenido actual")])
        self.progreso = Gtk.Label(xalign=0, wrap=True, label=_("Progreso: 00:00 / 00:00"))
        self.progreso.update_property([Gtk.AccessibleProperty.LABEL], [_("Progreso")])
        caja.append(self.pista)
        caja.append(self.progreso)

        def boton(etiqueta, fn):
            b = Gtk.Button(label=etiqueta, use_underline=True)
            b.connect("clicked", lambda *_a: fn())
            return b
        fila = Gtk.Box(spacing=8)
        self.b_play = boton(_("_Reproducir / pausar"), self._play_pausa)
        self.b_stop = boton(_("_Detener"), self._detener)
        self.b_atras = boton(_("_Atrasar {0}s").format(int(_paso("default_step_backward2"))),
                             lambda: player2_service.retroceder_segundos(_paso("default_step_backward2")))
        self.b_adelante = boton(_("A_vanzar {0}s").format(int(_paso("default_step_forward2"))),
                                lambda: player2_service.avanzar_segundos(_paso("default_step_forward2")))
        for b in (self.b_play, self.b_stop, self.b_atras, self.b_adelante):
            fila.append(b)
        caja.append(fila)

        fila = Gtk.Box(spacing=8)
        lbl = Gtk.Label(label=_("Vo_lumen:"), use_underline=True)
        self.volumen = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.volumen.set_hexpand(True)
        self.volumen.set_value(int(player2_service.obtener_informacion()["volume"] * 100))
        lbl.set_mnemonic_widget(self.volumen)
        self.volumen.connect("value-changed", lambda s: player2_service.establecer_volumen(s.get_value() / 100.0))
        fila.append(lbl)
        fila.append(self.volumen)
        caja.append(fila)

        fila = Gtk.Box(spacing=8)
        lbl = Gtk.Label(label=_("Veloc_idad:"), use_underline=True)
        self.velocidad = Gtk.DropDown.new_from_strings([f"{v:g}x".replace(".", ",") for v in VELOCIDADES])
        lbl.set_mnemonic_widget(self.velocidad)
        self._cambiando_velocidad = False
        self.velocidad.connect("notify::selected", self._on_velocidad)
        fila.append(lbl)
        fila.append(self.velocidad)
        caja.append(fila)
        self._sincronizar_velocidad()

        fila = Gtk.Box(spacing=8)
        self.b_capitulos = boton(_("Ca_pítulos…"), self._capitulos)
        self.b_cola = boton(_("C_ola…"), lambda: DialogoColaPodcasts(self).present())
        self.b_eq = boton(_("_Ecualizador…"), lambda: DialogoEcualizador(self).present())
        self.silencios = Gtk.CheckButton(label=_("_Saltar silencios"), use_underline=True,
                                         active=player2_service.saltar_silencios_activo())
        self.silencios.connect("toggled", lambda c: player2_service.activar_saltar_silencios(c.get_active()))
        for w in (self.b_capitulos, self.b_cola, self.b_eq, self.silencios):
            fila.append(w)
        caja.append(fila)
        caja.append(boton(_("_Cerrar"), self.close))

        ctl = Gtk.ShortcutController()
        ctl.set_scope(Gtk.ShortcutScope.LOCAL)
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        tec = Gtk.EventControllerKey()
        tec.connect("key-pressed", self._tecla)
        self.add_controller(tec)
        self.connect("close-request", self._al_cerrar)
        self._temporizador = GLib.timeout_add(500, self._actualizar)
        self._actualizar()

    def present(self):
        super().present()
        self.b_play.grab_focus()

    def _tecla(self, ctl, keyval, keycode, estado):
        """Av Pág y Re Pág cambian de capítulo; Ctrl+Av Pág, de episodio de la cola."""
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up) and not estado & Gdk.ModifierType.ALT_MASK:
            if estado & Gdk.ModifierType.CONTROL_MASK:
                if keyval == Gdk.KEY_Page_Down:
                    player2_service.reproducir_siguiente_de_la_cola()
                    return True
                return False
            self._saltar_capitulo(1 if keyval == Gdk.KEY_Page_Down else -1)
            return True
        return False

    def _al_cerrar(self, *_a):
        if self._temporizador:
            GLib.source_remove(self._temporizador)
            self._temporizador = 0
        type(self)._abierta = None
        return False

    def _play_pausa(self):
        if not player2_service.esta_activo():
            notificar(_("No hay nada cargado."))
            return
        player2_service.alternar_pausa()

    def _detener(self):
        player2_service.detener()
        notificar(_("Detenido."))

    def _on_velocidad(self, *_a):
        if self._cambiando_velocidad:
            return
        i = self.velocidad.get_selected()
        if 0 <= i < len(VELOCIDADES):
            player2_service.establecer_velocidad(VELOCIDADES[i])

    def _sincronizar_velocidad(self):
        info = player2_service.obtener_informacion()
        v = info.get("speed", 1.0)
        self._cambiando_velocidad = True
        try:
            self.velocidad.set_selected(min(range(len(VELOCIDADES)), key=lambda i: abs(VELOCIDADES[i] - v)))
        finally:
            self._cambiando_velocidad = False
        self.velocidad.set_sensitive(info.get("kind") in ("podcast", "youtube"))

    def _saltar_capitulo(self, delta: int):
        if not player2_service.capitulos:
            notificar(_("Este episodio no tiene capítulos."))
            return
        titulo = player2_service.capitulo_relativo(delta)
        n = player2_service.capitulo_actual()
        if titulo is not None:
            notificar(_("Capítulo {0} de {1}: {2}").format(n + 1, len(player2_service.capitulos), titulo))

    def _capitulos(self):
        if not player2_service.capitulos:
            notificar(_("Este episodio no tiene capítulos (o aún se están buscando)."))
            return
        DialogoCapitulos(self).present()

    def _texto_capitulo(self) -> str:
        n = player2_service.capitulo_actual()
        if n < 0 or not player2_service.capitulos:
            return ""
        return _(", capítulo {0} de {1}: {2}").format(n + 1, len(player2_service.capitulos), player2_service.capitulos[n][1])

    def _actualizar(self):
        info = player2_service.obtener_informacion()
        kind = info.get("kind", "")
        prefijo = {KIND_RADIO: _("Radio"), "podcast": _("Podcast"), "youtube": _("YouTube")}.get(kind, "")
        titulo = info["title"]
        texto = f"{prefijo}: {titulo}" if prefijo and titulo != _("Ninguno") else _("Nada en reproducción.")
        if self.pista.get_text() != texto:
            self.pista.set_text(texto)
        es_radio = kind == KIND_RADIO
        self.b_atras.set_sensitive(not es_radio)
        self.b_adelante.set_sensitive(not es_radio)
        self.velocidad.set_sensitive(kind in ("podcast", "youtube"))
        self.b_capitulos.set_sensitive(bool(player2_service.capitulos))
        if es_radio or info["duration"] <= 0:
            estado = {"reproduciendo": _("En directo"), "pausado": _("Pausado"), "detenido": _("Detenido")}.get(info["state"], "")
            extra = _(" (silenciado por prioridad de redes)") if info.get("ducked") else ""
            nuevo = f"{estado}{extra}"
        else:
            extra = _(" (pausado por prioridad de redes)") if info.get("ducked") else ""
            nuevo = _("Progreso: {0} / {1}{2}").format(hms(info["position"]), hms(info["duration"]), extra) + self._texto_capitulo()
        if self.progreso.get_text() != nuevo:
            self.progreso.set_text(nuevo)
        return True


class DialogoCapitulos(DialogoLista):
    def __init__(self, padre):
        super().__init__(padre, _("Capítulos del episodio"), _("Capítulos"), al_activar=lambda pos: self._ir())
        self.lista.poner([f"{titulo}, {hms(inicio)}" for inicio, titulo in player2_service.capitulos])
        actual = max(0, player2_service.capitulo_actual())
        if player2_service.capitulos:
            self.lista.seleccion.set_selected(min(actual, len(player2_service.capitulos) - 1))
        self.boton(_("_Ir al capítulo"), self._ir)
        self.cerrar_boton()

    def _ir(self):
        i = self.lista.posicion()
        titulo = player2_service.ir_a_capitulo(i)
        if titulo is not None:
            notificar(_("Capítulo {0}: {1}").format(i + 1, titulo))
            self.close()


class DialogoColaPodcasts(DialogoLista):
    def __init__(self, padre):
        super().__init__(padre, _("Cola de podcasts"), _("Cola"), al_activar=lambda pos: self._reproducir())
        self.items = []
        self.mostrar_estado(_("Episodios que sonarán a continuación, en este orden. Se añaden desde el menú de un "
                              "artículo de podcast."))
        self.boton(_("_Reproducir ahora"), self._reproducir)
        self.boton(_("_Subir"), lambda: self._mover(-1))
        self.boton(_("_Bajar"), lambda: self._mover(1))
        self.boton(_("_Quitar"), self._quitar)
        self.boton(_("_Vaciar la cola"), self._vaciar)
        self.cerrar_boton()
        self._cargar(0)

    def _cargar(self, foco: int = 0):
        self.items = cola_podcasts.listar()
        self.lista.poner([t for _aid, t in self.items] or [_("La cola está vacía.")])
        if self.items:
            self.lista.seleccion.set_selected(max(0, min(foco, len(self.items) - 1)))
        self.lista.enfocar()

    def _actual(self):
        p = self.lista.posicion()
        return (p, self.items[p]) if self.items and 0 <= p < len(self.items) else (p, None)

    def _reproducir(self):
        _p, item = self._actual()
        if not item:
            return
        cola_podcasts.quitar(item[0])
        from app.services.rss.rss_service import rss_service
        rss_service.reproducir_articulo_audio(item[0], callback=lambda ok, err=None: (
            None if ok else notificar(_("No se pudo reproducir: {0}").format(err or ""))))
        notificar(_("Cargando: {0}").format(item[1]))
        self.close()

    def _mover(self, delta):
        p, item = self._actual()
        if item and cola_podcasts.mover(item[0], delta):
            self._cargar(p + delta)
            notificar(_("Posición {0}.").format(p + delta + 1))

    def _quitar(self):
        p, item = self._actual()
        if item:
            cola_podcasts.quitar(item[0])
            self._cargar(p)
            notificar(_("Quitado de la cola."))

    def _vaciar(self):
        if not self.items:
            return

        def _hacer():
            cola_podcasts.vaciar()
            self._cargar()
            notificar(_("Cola vaciada."))
        confirmar_async(self, _("¿Vaciar la cola de podcasts?"), _hacer, etiqueta_si=_("Vaciar"))


class DialogoEcualizador(DialogoBase):
    """Diez bandas y preamplificador, en dB (de -15 a +15). Se oye al momento; Cancelar devuelve lo que había."""

    def __init__(self, padre):
        super().__init__(padre, _("Ecualizador del reproductor de podcasts"), _("_Aceptar"))
        self._antes = player2_service.backend.obtener_ecualizador()
        self._aceptado = False
        ganancias, preamp = self._antes
        self.preajuste = self.campo(_("_Preajuste:"), Gtk.DropDown.new_from_strings([n for n, _g in PREAJUSTES]))
        self.preajuste.connect("notify::selected", lambda *_a: self._preajuste())
        self.spins = []
        for freq, g in [("preamp", preamp)] + list(zip(BANDAS_EQ, ganancias)):
            nombre = _("_Preamplificador (dB):") if freq == "preamp" else (
                _("{0} _kHz (dB):").format(freq // 1000) if freq >= 1000 else _("{0} _Hz (dB):").format(freq))
            sp = self.campo(nombre, Gtk.SpinButton.new_with_range(-15, 15, 1))
            sp.set_value(int(round(g)))
            sp.connect("value-changed", lambda *_a: self._aplicar())
            self.spins.append(sp)
        b = Gtk.Button(label=_("_Restablecer (plano)"), use_underline=True)
        b.connect("clicked", lambda *_a: self._poner([0] * 10, 0))
        self.rejilla.attach(b, 1, self._fila, 1, 1)
        self._fila += 1
        self.connect("close-request", self._al_cerrar)
        self.set_focus(self.preajuste)

    def _valores(self):
        vals = [sp.get_value() for sp in self.spins]
        return vals[1:], vals[0]

    def _aplicar(self):
        ganancias, preamp = self._valores()
        player2_service.backend.establecer_ecualizador(ganancias, preamp)

    def _poner(self, ganancias, preamp):
        self.spins[0].set_value(int(preamp))
        for sp, g in zip(self.spins[1:], ganancias):
            sp.set_value(int(g))
        self._aplicar()

    def _preajuste(self):
        i = self.preajuste.get_selected()
        if 0 <= i < len(PREAJUSTES):
            self._poner(PREAJUSTES[i][1], 0)

    def aceptar(self):
        ganancias, preamp = self._valores()
        player2_service.guardar_ecualizador(ganancias, preamp)
        self._aceptado = True
        self.close()

    def _al_cerrar(self, *_a):
        if not self._aceptado:
            player2_service.backend.establecer_ecualizador(*self._antes)
        return False
