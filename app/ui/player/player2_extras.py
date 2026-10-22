# -*- coding: utf-8 -*-
"""Podcasts en la ventana del reproductor 2: capítulos, cola, ecualizador y saltar silencios.

Teclas en la ventana del reproductor:
    Av Pág / Re Pág      capítulo siguiente / anterior (si el episodio tiene capítulos)
    Ctrl+Av Pág          siguiente episodio de la cola
"""
import wx

from app.core.speech import speak, notificar
from app.services.media_player import cola_podcasts
from app.services.media_player.backends.basskit_player_backend import BANDAS_EQ
from app.services.media_player.player2_service import player2_service
from app.ui.accessible_controls import AccessibleListCtrl, habilitar_escape_cierre, confirmar


def hms(segundos: float) -> str:
    s = int(segundos or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# Preajustes del ecualizador (dB por banda: 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k).
PREAJUSTES = [
    (_("Plano"), [0] * 10),
    (_("Voz (podcasts y charlas)"), [-6, -4, -2, 0, 2, 4, 4, 3, 1, 0]),
    (_("Más graves"), [6, 5, 4, 2, 0, 0, 0, 0, 0, 0]),
    (_("Más agudos"), [0, 0, 0, 0, 0, 1, 3, 5, 6, 6]),
    (_("Menos graves (voces retumbantes)"), [-8, -6, -4, -2, 0, 0, 0, 0, 0, 0]),
    (_("Música"), [4, 3, 1, 0, -1, 0, 1, 3, 4, 4]),
]


class _Player2ExtrasMixin:
    """Añade a Player2Dialog la fila de podcasts y sus teclas."""

    def _crear_fila_podcast(self, sizer):
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_capitulos = wx.Button(self, label=_("Ca&pítulos..."), name=_("Capítulos del episodio"))
        self.btn_capitulos.Bind(wx.EVT_BUTTON, lambda e: self._ver_capitulos())
        fila.Add(self.btn_capitulos, 0, wx.ALL, 3)
        self.btn_cola = wx.Button(self, label=_("C&ola..."), name=_("Cola de podcasts"))
        self.btn_cola.Bind(wx.EVT_BUTTON, lambda e: self._ver_cola())
        fila.Add(self.btn_cola, 0, wx.ALL, 3)
        self.btn_eq = wx.Button(self, label=_("&Ecualizador..."), name=_("Ecualizador"))
        self.btn_eq.Bind(wx.EVT_BUTTON, lambda e: self._ver_ecualizador())
        fila.Add(self.btn_eq, 0, wx.ALL, 3)
        self.cb_silencios = wx.CheckBox(self, label=_("&Saltar silencios"),
                                        name=_("Saltar los silencios de los podcasts"))
        self.cb_silencios.SetValue(player2_service.saltar_silencios_activo())
        self.cb_silencios.Bind(wx.EVT_CHECKBOX, lambda e: player2_service.activar_saltar_silencios(
            self.cb_silencios.GetValue()))
        fila.Add(self.cb_silencios, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 3)
        sizer.Add(fila, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        self.Bind(wx.EVT_CHAR_HOOK, self._teclas_podcast)

    def _teclas_podcast(self, event):
        key = event.GetKeyCode()
        ctrl, alt = event.ControlDown(), event.AltDown()
        if key in (wx.WXK_PAGEDOWN, wx.WXK_PAGEUP) and not alt:
            if ctrl and key == wx.WXK_PAGEDOWN:
                player2_service.reproducir_siguiente_de_la_cola()
                return
            if not ctrl:
                self._saltar_capitulo(1 if key == wx.WXK_PAGEDOWN else -1)
                return
        event.Skip()

    def _saltar_capitulo(self, delta: int):
        if not player2_service.capitulos:
            speak(_("Este episodio no tiene capítulos."), interrupt=True)
            return
        titulo = player2_service.capitulo_relativo(delta)
        n = player2_service.capitulo_actual()
        if titulo is not None:
            speak(_("Capítulo {0} de {1}: {2}").format(n + 1, len(player2_service.capitulos), titulo),
                  interrupt=True)

    def _texto_capitulo(self) -> str:
        """Para la línea de progreso: "capítulo 3 de 18: título" (vacío sin capítulos)."""
        n = player2_service.capitulo_actual()
        if n < 0 or not player2_service.capitulos:
            return ""
        return _(", capítulo {0} de {1}: {2}").format(n + 1, len(player2_service.capitulos),
                                                       player2_service.capitulos[n][1])

    def _ver_capitulos(self):
        if not player2_service.capitulos:
            speak(_("Este episodio no tiene capítulos (o aún se están buscando)."), interrupt=True)
            return
        dlg = CapitulosDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _ver_cola(self):
        dlg = ColaPodcastsDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _ver_ecualizador(self):
        dlg = EcualizadorDialog(self)
        dlg.ShowModal()
        dlg.Destroy()


class CapitulosDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Capítulos del episodio"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Capítulos del episodio"))
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, name=_("Capítulos"))
        self.lista.InsertColumn(0, _("Empieza"), width=90)
        self.lista.InsertColumn(1, _("Capítulo"), width=420)
        for i, (inicio, titulo) in enumerate(player2_service.capitulos):
            self.lista.InsertItem(i, hms(inicio))
            self.lista.SetItem(i, 1, titulo)
            self.lista.establecer_lectura(i, f"{titulo}, {hms(inicio)}")
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._ir())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, label=_("&Ir al capítulo"))
        b.Bind(wx.EVT_BUTTON, lambda e: self._ir())
        fila.Add(b, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        actual = max(0, player2_service.capitulo_actual())
        if self.lista.GetItemCount():
            self.lista.Select(actual)
            self.lista.Focus(actual)
        self.lista.SetFocus()
        self.CenterOnParent()

    def _ir(self):
        i = self.lista.GetFocusedItem()
        titulo = player2_service.ir_a_capitulo(i)
        if titulo is not None:
            speak(_("Capítulo {0}: {1}").format(i + 1, titulo), interrupt=True)
            self.EndModal(wx.ID_OK)


class ColaPodcastsDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Cola de podcasts"), size=(600, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Cola de podcasts"))
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "Episodios que sonarán a continuación, en este orden. Se añaden desde el menú de un "
            "artículo de podcast (Añadir a la cola de podcasts).")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, name=_("Cola"))
        self.lista.InsertColumn(0, _("Episodio"), width=540)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._reproducir())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, accion in ((_("&Reproducir ahora"), self._reproducir), (_("&Subir"), lambda: self._mover(-1)),
                                 (_("&Bajar"), lambda: self._mover(1)), (_("&Quitar"), self._quitar),
                                 (_("&Vaciar la cola"), self._vaciar)):
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, lambda e, a=accion: a())
            fila.Add(b, 0, wx.ALL, 3)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 3)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self._cargar(0)
        self.lista.SetFocus()
        self.CenterOnParent()

    def _cargar(self, foco: int = 0):
        self.items = cola_podcasts.listar()
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, (_aid, titulo) in enumerate(self.items):
            self.lista.InsertItem(i, titulo)
        if self.items:
            foco = max(0, min(foco, len(self.items) - 1))
            self.lista.Select(foco)
            self.lista.Focus(foco)
        else:
            self.lista.InsertItem(0, _("La cola está vacía."))

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return (i, self.items[i]) if self.items and 0 <= i < len(self.items) else (i, None)

    def _reproducir(self):
        i, item = self._actual()
        if not item:
            return
        cola_podcasts.quitar(item[0])
        from app.services.rss.rss_service import rss_service
        rss_service.reproducir_articulo_audio(item[0], callback=lambda ok, err=None: (
            None if ok else notificar(_("No se pudo reproducir: {0}").format(err or ""))))
        speak(_("Cargando: {0}").format(item[1]), interrupt=True)
        self.EndModal(wx.ID_OK)

    def _mover(self, delta):
        i, item = self._actual()
        if item and cola_podcasts.mover(item[0], delta):
            self._cargar(i + delta)
            speak(_("Posición {0}.").format(i + delta + 1), interrupt=True)

    def _quitar(self):
        i, item = self._actual()
        if item:
            cola_podcasts.quitar(item[0])
            self._cargar(i)
            speak(_("Quitado de la cola."), interrupt=True)

    def _vaciar(self):
        if self.items and confirmar(self, _("¿Vaciar la cola de podcasts?"), _("Cola de podcasts")):
            cola_podcasts.vaciar()
            self._cargar()
            speak(_("Cola vaciada."), interrupt=True)


class EcualizadorDialog(wx.Dialog):
    """Diez bandas y preamplificador, en dB (de -15 a +15). Se oye al momento; Cancelar
    devuelve lo que había."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Ecualizador del reproductor de podcasts"), size=(460, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Ecualizador"))
        self._antes = player2_service.backend.obtener_ecualizador()
        ganancias, preamp = self._antes
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Preajuste:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        self.choice = wx.Choice(self, choices=[n for n, _g in PREAJUSTES], name=_("Preajuste"))
        self.choice.Bind(wx.EVT_CHOICE, self._preajuste)
        fila.Add(self.choice, 1, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        rejilla = wx.FlexGridSizer(cols=2, vgap=4, hgap=8)
        self.spins = []
        for freq, g in [("preamp", preamp)] + list(zip(BANDAS_EQ, ganancias)):
            nombre = _("Preamplificador (dB)") if freq == "preamp" else (
                _("{0} kHz (dB)").format(freq // 1000) if freq >= 1000 else _("{0} Hz (dB)").format(freq))
            rejilla.Add(wx.StaticText(self, label=nombre), 0, wx.ALIGN_CENTER_VERTICAL)
            sp = wx.SpinCtrl(self, min=-15, max=15, initial=int(round(g)), name=nombre)
            sp.Bind(wx.EVT_SPINCTRL, lambda e: self._aplicar())
            sp.Bind(wx.EVT_TEXT, lambda e: self._aplicar())
            rejilla.Add(sp, 0)
            self.spins.append(sp)
        s.Add(rejilla, 1, wx.ALL, 8)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_cero = wx.Button(self, label=_("&Restablecer (plano)"))
        b_cero.Bind(wx.EVT_BUTTON, lambda e: self._poner([0] * 10, 0))
        botones.Add(b_cero, 0, wx.ALL, 4)
        botones.AddStretchSpacer()
        ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        ok.Bind(wx.EVT_BUTTON, self._aceptar)
        botones.Add(ok, 0, wx.ALL, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._al_cerrar)
        self._aceptado = False
        self.CenterOnParent()
        self.choice.SetFocus()

    def _valores(self):
        vals = [sp.GetValue() for sp in self.spins]
        return vals[1:], vals[0]

    def _aplicar(self):
        ganancias, preamp = self._valores()
        player2_service.backend.establecer_ecualizador(ganancias, preamp)

    def _poner(self, ganancias, preamp):
        self.spins[0].SetValue(int(preamp))
        for sp, g in zip(self.spins[1:], ganancias):
            sp.SetValue(int(g))
        self._aplicar()

    def _preajuste(self, event):
        i = self.choice.GetSelection()
        if 0 <= i < len(PREAJUSTES):
            self._poner(PREAJUSTES[i][1], 0)

    def _aceptar(self, event):
        ganancias, preamp = self._valores()
        player2_service.guardar_ecualizador(ganancias, preamp)
        self._aceptado = True
        self.EndModal(wx.ID_OK)

    def _al_cerrar(self, event):
        if event.GetEventObject() is self and not self._aceptado:
            player2_service.backend.establecer_ecualizador(*self._antes)
        event.Skip()
