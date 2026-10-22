# -*- coding: utf-8 -*-
"""Diálogos de PeerTube.

Fase 1: alta de cuenta (URL de instancia + usuario + contraseña). El segundo factor (2FA), si la
cuenta lo tiene, se pide aparte solo cuando la instancia lo exige (no se muestra si no hace falta).
"""
import wx

from .accessible_controls import AccessibleTextCtrl, mostrar_info


class PeerTubeAltaDialog(wx.Dialog):
    """Datos para añadir una cuenta de PeerTube. `self.resultado` = (instancia, usuario, contraseña)."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Añadir cuenta de PeerTube"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Añadir cuenta de PeerTube"))
        self.resultado = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Dirección de la instancia (por ejemplo, framatube.org):")),
              0, wx.LEFT | wx.TOP, 8)
        self.txt_instancia = AccessibleTextCtrl(self, name=_("Dirección de la instancia"))
        s.Add(self.txt_instancia, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Usuario o correo:")), 0, wx.LEFT, 8)
        self.txt_usuario = AccessibleTextCtrl(self, name=_("Usuario o correo"))
        s.Add(self.txt_usuario, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Contraseña:")), 0, wx.LEFT, 8)
        self.txt_password = AccessibleTextCtrl(self, style=wx.TE_PASSWORD, name=_("Contraseña"))
        s.Add(self.txt_password, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_(
            "Tu contraseña se envía solo a tu instancia para obtener un identificador de sesión, que "
            "se guarda cifrado. Si tu cuenta tiene verificación en dos pasos, se te pedirá el código "
            "después.")), 0, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(self, wx.ID_OK, _("&Iniciar sesión"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        botones.Add(btn_ok, 0, wx.ALL, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_instancia.SetFocus()

    def _on_ok(self, event):
        instancia = self.txt_instancia.GetValue().strip()
        usuario = self.txt_usuario.GetValue().strip()
        password = self.txt_password.GetValue()
        if not instancia or not usuario or not password:
            mostrar_info(self, _("La instancia, el usuario y la contraseña son obligatorios."),
                         _("Faltan datos"))
            return
        self.resultado = (instancia, usuario, password)
        self.EndModal(wx.ID_OK)
