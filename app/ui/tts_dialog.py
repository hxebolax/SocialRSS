# -*- coding: utf-8 -*-
"""
Diálogo lector de texto en voz alta (TTS) dedicado.

Lee un texto largo (p. ej. el contenido de un artículo de RSS) con la voz SAPI5
de Windows, con controles accesibles de reproducir, pausar/reanudar y detener,
además de velocidad y selección de voz. Es independiente del lector de pantalla.
"""

import wx

from app.core.speech import speak
from app.core.config_manager import config
from app.services.audio.tts_reader import tts_reader
from .accessible_controls import AccessibleTextCtrl

try:
    _
except NameError:
    _ = lambda s: s


class TTSReaderDialog(wx.Dialog):
    """Lector TTS con controles de transporte, velocidad y voz."""

    def __init__(self, parent, titulo: str, texto: str, posicion_inicial: int = 0):
        super().__init__(parent, title=_("Lector en voz alta: {0}").format(titulo),
                         size=(640, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Lector de texto en voz alta"))
        self.texto = texto or ""
        # Lectura por FRASES para poder recordar la posición: cada frase con su offset de carácter.
        self._frases = self._dividir_en_frases(self.texto)   # lista de (offset, frase)
        self._idx_frase = self._frase_en_offset(posicion_inicial)
        self.posicion_actual = self._frases[self._idx_frase][0] if self._frases else 0
        self._reproduciendo = False
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tick, self._timer)
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.CenterOnParent()

    @staticmethod
    def _dividir_en_frases(texto):
        """Parte el texto en frases conservando el offset de inicio de cada una."""
        import re
        frases = []
        i = 0
        for trozo in re.split(r'(?<=[.!?\n])\s+', texto):
            if trozo:
                frases.append((i, trozo))
            i += len(trozo) + 1
        return frases or [(0, texto)]

    def _frase_en_offset(self, offset):
        """Índice de la frase que contiene un offset de carácter."""
        if offset <= 0:
            return 0
        for i, (ini, _f) in enumerate(self._frases):
            if ini >= offset:
                return max(0, i - 1) if self._frases[i][0] > offset else i
        return max(0, len(self._frases) - 1)

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("Texto a leer:")), 0, wx.ALL, 5)
        self.txt = AccessibleTextCtrl(self, value=self.texto,
                                      style=wx.TE_MULTILINE | wx.TE_READONLY,
                                      name=_("Texto del artículo"))
        sizer.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)

        # Voz y velocidad
        cfg = wx.BoxSizer(wx.HORIZONTAL)
        cfg.Add(wx.StaticText(self, label=_("Voz:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        self._voces = tts_reader.listar_voces()
        self.choice_voz = wx.Choice(self, choices=self._voces or [_("(sin voces)")], name=_("Voz de lectura"))
        # Restaurar la última voz usada (por nombre, robusto entre sesiones).
        voz_guardada = config.get("accessibility", "tts_voice_name", "")
        idx_voz = self._voces.index(voz_guardada) if voz_guardada in self._voces else 0
        self.choice_voz.SetSelection(idx_voz)
        if self._voces:
            tts_reader.establecer_voz(idx_voz)
        self.choice_voz.Bind(wx.EVT_CHOICE, self._on_voz)
        cfg.Add(self.choice_voz, 0, wx.ALL, 4)

        cfg.Add(wx.StaticText(self, label=_("Velocidad:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        # Restaurar la última velocidad usada.
        vel_guardada = int(config.get("accessibility", "tts_speed", 0))
        vel_guardada = max(-10, min(10, vel_guardada))
        self.slider_vel = wx.Slider(self, value=vel_guardada, minValue=-10, maxValue=10,
                                    style=wx.SL_HORIZONTAL | wx.SL_LABELS, name=_("Velocidad de lectura"))
        tts_reader.establecer_velocidad(vel_guardada)
        self.slider_vel.Bind(wx.EVT_SLIDER, self._on_velocidad)
        cfg.Add(self.slider_vel, 1, wx.EXPAND | wx.ALL, 4)
        sizer.Add(cfg, 0, wx.EXPAND | wx.ALL, 4)

        # Transporte
        btn = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_play = wx.Button(self, label=_("&Reproducir"), name=_("Reproducir desde el principio"))
        self.btn_play.Bind(wx.EVT_BUTTON, self._on_play)
        btn.Add(self.btn_play, 0, wx.ALL, 5)
        self.btn_pausa = wx.Button(self, label=_("&Pausar"), name=_("Pausar o reanudar"))
        self.btn_pausa.Bind(wx.EVT_BUTTON, self._on_pausa)
        btn.Add(self.btn_pausa, 0, wx.ALL, 5)
        self.btn_stop = wx.Button(self, label=_("&Detener"), name=_("Detener la lectura"))
        self.btn_stop.Bind(wx.EVT_BUTTON, self._on_stop)
        btn.Add(self.btn_stop, 0, wx.ALL, 5)
        btn.AddStretchSpacer()
        btn.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(sizer)

        if not tts_reader.disponible():
            self.btn_play.Disable()
            self.btn_pausa.Disable()
            self.btn_stop.Disable()
            speak(_("No hay voz de Windows (SAPI5) disponible para la lectura."), interrupt=True)

    def _on_voz(self, event):
        idx = self.choice_voz.GetSelection()
        tts_reader.establecer_voz(idx)
        # Recordar la voz elegida (por nombre) para la próxima vez.
        if 0 <= idx < len(self._voces):
            config.set("accessibility", "tts_voice_name", self._voces[idx])

    def _on_velocidad(self, event):
        val = self.slider_vel.GetValue()
        tts_reader.establecer_velocidad(val)
        config.set("accessibility", "tts_speed", val)

    def _on_play(self, event):
        tts_reader.establecer_velocidad(self.slider_vel.GetValue())
        # Si ya se había leído todo, empezar de nuevo desde el principio.
        if self._idx_frase >= len(self._frases):
            self._idx_frase = 0
        self._reproduciendo = True
        self._hablar_frase_actual()
        self._timer.Start(250)
        self.btn_pausa.SetLabel(_("&Pausar"))
        speak(_("Leyendo."), interrupt=True)

    def _hablar_frase_actual(self):
        """Pronuncia la frase del índice actual y actualiza la posición recordada."""
        if not (0 <= self._idx_frase < len(self._frases)):
            return
        offset, frase = self._frases[self._idx_frase]
        self.posicion_actual = offset
        tts_reader.leer(frase)

    def _on_tick(self, event):
        """Cuando SAPI termina la frase actual, pasa a la siguiente. Así se sabe por dónde va."""
        if not self._reproduciendo or tts_reader.esta_pausado():
            return
        if tts_reader.esta_hablando():
            return  # sigue con la frase actual
        # Frase terminada: avanzar.
        self._idx_frase += 1
        if self._idx_frase >= len(self._frases):
            self._reproduciendo = False
            self._timer.Stop()
            self.posicion_actual = len(self.texto)  # terminó: se olvidará la posición guardada
            self.btn_pausa.SetLabel(_("&Pausar"))
            speak(_("Lectura terminada."), interrupt=True)
            return
        self._hablar_frase_actual()

    def _on_pausa(self, event):
        pausado = tts_reader.alternar_pausa()
        self.btn_pausa.SetLabel(_("&Reanudar") if pausado else _("&Pausar"))
        speak(_("En pausa.") if pausado else _("Reanudado."), interrupt=True)

    def _on_stop(self, event):
        self._reproduciendo = False
        self._timer.Stop()
        tts_reader.detener()
        self.btn_pausa.SetLabel(_("&Pausar"))
        speak(_("Lectura detenida."), interrupt=True)

    def _on_close(self, event):
        self._reproduciendo = False
        try:
            self._timer.Stop()
        except Exception:
            pass
        tts_reader.detener()
        self.EndModal(wx.ID_CANCEL)
