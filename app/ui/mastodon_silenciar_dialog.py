# -*- coding: utf-8 -*-
"""Diálogo para silenciar una cuenta de Mastodon: durante cuánto tiempo y si también se ocultan
sus notificaciones (account_mute con duration y notifications). Antes solo había un "¿Seguro?"
y el silencio era siempre indefinido y con notificaciones incluidas."""

import wx

from .accessible_controls import habilitar_escape_cierre


class SilenciarCuentaDialog(wx.Dialog):
    DURACIONES = [(_("Para siempre"), 0), (_("30 minutos"), 1800), (_("1 hora"), 3600),
                  (_("6 horas"), 21600), (_("1 día"), 86400), (_("3 días"), 259200),
                  (_("1 semana"), 604800)]

    def __init__(self, parent, acct: str):
        super().__init__(parent, title=_("Silenciar a {0}").format(acct), style=wx.DEFAULT_DIALOG_STYLE,
                         name=_("Silenciar cuenta"))
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "No verás sus publicaciones en tus cronologías. No se entera de que la has silenciado.")),
            0, wx.ALL, 8)
        s.Add(wx.StaticText(self, label=_("Duración del silencio:")), 0, wx.LEFT | wx.TOP, 8)
        self.choice_dur = wx.Choice(self, choices=[n for n, _s in self.DURACIONES], name=_("Duración del silencio"))
        self.choice_dur.SetSelection(0)
        s.Add(self.choice_dur, 0, wx.EXPAND | wx.ALL, 8)
        self.cb_notif = wx.CheckBox(self, label=_("Ocultar también sus notificaciones"),
                                    name=_("Ocultar sus notificaciones"))
        self.cb_notif.SetValue(True)
        s.Add(self.cb_notif, 0, wx.ALL, 8)
        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        ok.SetDefault()
        btns.AddButton(ok)
        btns.AddButton(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")))
        btns.Realize()
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(s)
        habilitar_escape_cierre(self)
        self.CenterOnParent()
        self.choice_dur.SetFocus()

    def get_opciones(self):
        """(duración en segundos o None si es para siempre, ocultar notificaciones)."""
        seg = self.DURACIONES[max(0, self.choice_dur.GetSelection())][1]
        return (seg or None), self.cb_notif.GetValue()
