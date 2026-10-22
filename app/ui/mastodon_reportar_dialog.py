# -*- coding: utf-8 -*-
"""Denunciar una publicación de Mastodon con TODO lo que admite la API: categoría (spam, contenido
ilegal, incumple las normas del servidor u otra), qué normas concretas incumple, comentario y
reenvío al servidor de origen del autor. Antes solo se enviaba un comentario, y la moderación
recibía la denuncia sin clasificar."""

import wx

from app.core.speech import speak
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import AccessibleCheckListBox, AccessibleTextCtrl, habilitar_escape_cierre


class ReportarTootDialog(wx.Dialog):
    CATEGORIAS = [("other", _("Otro motivo")), ("spam", _("Spam o publicidad no deseada")),
                  ("legal", _("Contenido ilegal")), ("violation", _("Incumple las normas del servidor"))]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Denunciar publicación"), size=(560, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Denunciar publicación"))
        self._reglas = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Motivo de la denuncia:")), 0, wx.ALL, 5)
        self.choice_cat = wx.Choice(self, choices=[n for _c, n in self.CATEGORIAS], name=_("Motivo de la denuncia"))
        self.choice_cat.SetSelection(0)
        self.choice_cat.Bind(wx.EVT_CHOICE, lambda e: self._actualizar_reglas())
        s.Add(self.choice_cat, 0, wx.EXPAND | wx.ALL, 5)
        self.lbl_reglas = wx.StaticText(self, label=_("Normas del servidor que incumple:"))
        s.Add(self.lbl_reglas, 0, wx.ALL, 5)
        self.lista_reglas = AccessibleCheckListBox(self, choices=[], name=_("Normas que incumple"))
        s.Add(self.lista_reglas, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(wx.StaticText(self, label=_("Comentario para la moderación (opcional):")), 0, wx.ALL, 5)
        self.txt_com = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 80),
                                          name=_("Comentario para la moderación"))
        s.Add(self.txt_com, 0, wx.EXPAND | wx.ALL, 5)
        self.cb_reenviar = wx.CheckBox(self, label=_("Reenviar también al servidor del autor (si es de otro servidor)"),
                                       name=_("Reenviar la denuncia"))
        s.Add(self.cb_reenviar, 0, wx.ALL, 5)
        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, _("&Denunciar"))
        ok.SetDefault()
        btns.AddButton(ok)
        btns.AddButton(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")))
        btns.Realize()
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self._actualizar_reglas()
        self.CenterOnParent()
        self.choice_cat.SetFocus()

        def _con_reglas(res, err):
            if not self or err:
                return
            self._reglas = res or []
            self.lista_reglas.Set([r["text"] for r in self._reglas])
            self._actualizar_reglas()
        mastodon_service.reglas_instancia_async(account_id, callback=_con_reglas)

    def _actualizar_reglas(self):
        """Las normas solo tienen sentido con el motivo "incumple las normas del servidor"."""
        activas = self.CATEGORIAS[max(0, self.choice_cat.GetSelection())][0] == "violation" and bool(self._reglas)
        self.lista_reglas.Enable(activas)
        self.lbl_reglas.Enable(activas)

    def get_datos(self):
        categoria = self.CATEGORIAS[max(0, self.choice_cat.GetSelection())][0]
        reglas = []
        if categoria == "violation":
            reglas = [self._reglas[i]["id"] for i in self.lista_reglas.GetCheckedItems()
                      if i < len(self._reglas)]
            if not reglas:
                # La API exige al menos una norma con esa categoría: sin ninguna, "otro".
                speak(_("No marcaste ninguna norma: se enviará como otro motivo."), interrupt=False)
                categoria = "other"
        return {"categoria": categoria, "reglas": reglas, "comentario": self.txt_com.GetValue().strip(),
                "reenviar": self.cb_reenviar.GetValue()}
