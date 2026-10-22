# -*- coding: utf-8 -*-
"""
Ventana de control del SEGUNDO reproductor (radio, podcast y YouTube).

Independiente del reproductor de redes sociales. Permite reproducir/pausar, detener,
avanzar/retroceder (en podcast y YouTube), ajustar el volumen y silenciar. La radio es
en directo, así que avanzar/retroceder no aplican.
"""

import logging
import wx

from app.core.speech import speak, notificar
from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.services.media_player.player2_service import player2_service, KIND_RADIO
from app.ui.accessible_controls import AccessibleSlider
from app.ui.player.player2_extras import _Player2ExtrasMixin

log = logging.getLogger(__name__)


class Player2Dialog(_Player2ExtrasMixin, wx.Dialog):
    """Diálogo accesible del reproductor de radio, podcast y YouTube."""

    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Reproductor de radio, podcast y YouTube"),
            size=(660, 380),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Reproductor de radio, podcast y YouTube"))
        event_bus.subscribe("player2_state_changed", self._on_state_changed)
        event_bus.subscribe("player2_error", self._on_player_error)
        self._init_ui()
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self._actualizar_info(), self.timer)
        self.timer.Start(500)
        self.CenterOnParent()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self._actualizar_info()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)

        self.lbl_pista = wx.StaticText(self, label=_("Nada en reproducción."),
                                       name=_("Contenido actual"))
        s.Add(self.lbl_pista, 0, wx.ALL | wx.EXPAND, 10)

        self.lbl_progreso = wx.StaticText(self, label=_("Progreso: 00:00 / 00:00"),
                                          name=_("Progreso"))
        s.Add(self.lbl_progreso, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        tr = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_play = wx.Button(self, label=_("&Reproducir / Pausar"))
        self.btn_play.Bind(wx.EVT_BUTTON, lambda e: self._play_pause())
        tr.Add(self.btn_play, 0, wx.ALL, 3)

        self.btn_stop = wx.Button(self, label=_("&Detener"))
        self.btn_stop.Bind(wx.EVT_BUTTON, lambda e: self._detener())
        tr.Add(self.btn_stop, 0, wx.ALL, 3)

        paso_b = int(self._paso_atras())
        self.btn_back = wx.Button(self, label=_("&Atrasar {0}s").format(paso_b))
        self.btn_back.Bind(wx.EVT_BUTTON, lambda e: player2_service.retroceder_segundos(self._paso_atras()))
        tr.Add(self.btn_back, 0, wx.ALL, 3)

        paso_f = int(self._paso_adelante())
        self.btn_fwd = wx.Button(self, label=_("A&vanzar {0}s").format(paso_f))
        self.btn_fwd.Bind(wx.EVT_BUTTON, lambda e: player2_service.avanzar_segundos(self._paso_adelante()))
        tr.Add(self.btn_fwd, 0, wx.ALL, 3)
        s.Add(tr, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        # Volumen
        sv = wx.BoxSizer(wx.HORIZONTAL)
        sv.Add(wx.StaticText(self, label=_("Volumen:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        vol_act = int(player2_service.obtener_informacion()["volume"] * 100)
        self.slider_vol = AccessibleSlider(self, value=vol_act, minValue=0, maxValue=100,
                                           name=_("Volumen"), unidad="%")
        self.slider_vol.Bind(wx.EVT_SLIDER,
                             lambda e: player2_service.establecer_volumen(self.slider_vol.GetValue() / 100.0))
        sv.Add(self.slider_vol, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(sv, 0, wx.EXPAND | wx.ALL, 5)

        # Velocidad (solo para podcast y YouTube; en radio no aplica).
        ss = wx.BoxSizer(wx.HORIZONTAL)
        # Mnemónico en la "i": la "v" ya es de "Avanzar".
        ss.Add(wx.StaticText(self, label=_("Veloc&idad:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        # De 0,5x a 3x en pasos de 0,1 (también con F7 y F8).
        self._velocidades = [round(0.5 + 0.1 * i, 1) for i in range(26)]
        self.choice_speed = wx.Choice(self, choices=[f"{v:g}x".replace(".", ",") for v in self._velocidades],
                                      name=_("Velocidad de reproducción"))
        self.choice_speed.Bind(wx.EVT_CHOICE, self._on_speed)
        ss.Add(self.choice_speed, 0, wx.ALL, 5)
        s.Add(ss, 0, wx.EXPAND | wx.ALL, 5)
        self._sincronizar_velocidad()
        # Capítulos, cola, ecualizador y saltar silencios (player2_extras.py).
        self._crear_fila_podcast(s)

        bottom = wx.BoxSizer(wx.HORIZONTAL)
        bottom.AddStretchSpacer()
        bottom.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(bottom, 0, wx.EXPAND | wx.ALL, 5)

        self.SetSizer(s)

    def _paso_adelante(self) -> float:
        """Segundos para avanzar (configurable en Preferencias > Reproductores)."""
        try:
            return float(config.get("media_player", "default_step_forward2", 15.0))
        except (TypeError, ValueError):
            return 15.0

    def _paso_atras(self) -> float:
        """Segundos para retroceder (configurable en Preferencias > Reproductores)."""
        try:
            return float(config.get("media_player", "default_step_backward2", 15.0))
        except (TypeError, ValueError):
            return 15.0

    def _play_pause(self):
        if not player2_service.esta_activo():
            speak(_("No hay nada cargado."), interrupt=True)
            return
        player2_service.alternar_pausa()

    def _detener(self):
        player2_service.detener()
        speak(_("Detenido."), interrupt=True)

    def _on_speed(self, event):
        # El lector ya anuncia la opción elegida en el selector; no duplicar con speak().
        idx = self.choice_speed.GetSelection()
        if 0 <= idx < len(self._velocidades):
            player2_service.establecer_velocidad(self._velocidades[idx])

    def _sincronizar_velocidad(self):
        """Pone el choice en la velocidad guardada y lo activa solo para podcast/YouTube."""
        info = player2_service.obtener_informacion()
        v = info.get("speed", 1.0)
        # Elegir la opción más cercana.
        try:
            idx = min(range(len(self._velocidades)), key=lambda i: abs(self._velocidades[i] - v))
            self.choice_speed.SetSelection(idx)
        except ValueError:
            pass
        es_podcast_yt = info.get("kind") in ("podcast", "youtube")
        self.choice_speed.Enable(es_podcast_yt)

    def _actualizar_info(self):
        info = player2_service.obtener_informacion()
        kind = info.get("kind", "")
        etiquetas = {KIND_RADIO: _("Radio"), "podcast": _("Podcast"), "youtube": _("YouTube")}
        prefijo = etiquetas.get(kind, "")
        titulo = info["title"]
        self.lbl_pista.SetLabel(
            (f"{prefijo}: {titulo}" if prefijo and titulo != _("Ninguno") else _("Nada en reproducción.")))
        # En directo (radio) no hay progreso útil.
        es_radio = kind == KIND_RADIO
        self.btn_back.Enable(not es_radio)
        self.btn_fwd.Enable(not es_radio)
        self.choice_speed.Enable(kind in ("podcast", "youtube"))
        self.btn_capitulos.Enable(bool(player2_service.capitulos))
        if es_radio or info["duration"] <= 0:
            estado = {"reproduciendo": _("En directo"), "pausado": _("Pausado"),
                      "detenido": _("Detenido")}.get(info["state"], "")
            extra = _(" (silenciado por prioridad de redes)") if info.get("ducked") else ""
            self.lbl_progreso.SetLabel(f"{estado}{extra}")
        else:
            pos, dur = info["position"], info["duration"]
            pos_s = f"{int(pos // 60):02d}:{int(pos % 60):02d}"
            dur_s = f"{int(dur // 60):02d}:{int(dur % 60):02d}"
            extra = _(" (pausado por prioridad de redes)") if info.get("ducked") else ""
            self.lbl_progreso.SetLabel(_("Progreso: {0} / {1}{2}").format(pos_s, dur_s, extra)
                                       + self._texto_capitulo())

    def _on_state_changed(self, estado):
        wx.CallAfter(self._actualizar_info)

    def _on_player_error(self, err):
        notificar(_("Error en el reproductor: {0}").format(err))

    def _on_close(self, event):
        if self.timer.IsRunning():
            self.timer.Stop()
        event.Skip()

    def Destroy(self):
        if self.timer.IsRunning():
            self.timer.Stop()
        event_bus.unsubscribe("player2_state_changed", self._on_state_changed)
        event_bus.unsubscribe("player2_error", self._on_player_error)
        return super().Destroy()
