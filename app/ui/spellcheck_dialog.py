# -*- coding: utf-8 -*-
"""
Diálogo de corrección ortográfica: recorre las palabras desconocidas y permite
corregirlas, ignorarlas o editarlas a mano. Devuelve el texto corregido.
"""

import wx
from app.core.speech import speak, notificar
from app.services import spellcheck
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl


class SpellCheckDialog(wx.Dialog):
    """Revisa el texto y aplica correcciones palabra por palabra."""

    def __init__(self, parent, texto):
        super().__init__(parent, title=_("Corrección ortográfica"), size=(520, 420),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Diálogo de corrección ortográfica"))
        self.texto = texto
        self.errores = spellcheck.revisar(texto)
        self.indice = 0
        self._init_ui()
        self.CenterOnParent()
        if not self.errores:
            notificar(_("No se encontraron errores ortográficos."))
        else:
            self._mostrar_actual()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl_palabra = wx.StaticText(self, label=_("Sin errores."))
        s.Add(self.lbl_palabra, 0, wx.ALL, 8)

        s.Add(wx.StaticText(self, label=_("Sugerencias:")), 0, wx.LEFT | wx.TOP, 8)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Sugerencias de corrección"))
        self.lista.InsertColumn(0, _("Sugerencia"), width=440)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

        s.Add(wx.StaticText(self, label=_("O escribir la corrección a mano:")), 0, wx.LEFT, 8)
        self.txt_manual = AccessibleTextCtrl(self, name=_("Corrección manual"))
        s.Add(self.txt_manual, 0, wx.EXPAND | wx.ALL, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_reemplazar = wx.Button(self, label=_("&Reemplazar"))
        self.btn_reemplazar.Bind(wx.EVT_BUTTON, self._on_reemplazar)
        btns.Add(self.btn_reemplazar, 0, wx.ALL, 5)
        self.btn_ignorar = wx.Button(self, label=_("&Ignorar"))
        self.btn_ignorar.Bind(wx.EVT_BUTTON, self._on_ignorar)
        btns.Add(self.btn_ignorar, 0, wx.ALL, 5)
        self.btn_fin = wx.Button(self, wx.ID_OK, _("&Finalizar"))
        btns.Add(self.btn_fin, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)

    def _mostrar_actual(self):
        if self.indice >= len(self.errores):
            speak(_("Revisión terminada."), interrupt=True)
            self.EndModal(wx.ID_OK)
            return
        palabra, sugerencias = self.errores[self.indice]
        self.lbl_palabra.SetLabel(_("Palabra desconocida: {0}").format(palabra))
        self.lista.DeleteAllItems()
        for i, sug in enumerate(sugerencias):
            self.lista.InsertItem(i, sug)
        if sugerencias:
            self.lista.Focus(0)
            self.lista.Select(0)
        self.txt_manual.SetValue(sugerencias[0] if sugerencias else palabra)
        speak(_("Palabra desconocida: {0}. {1} sugerencias.").format(palabra, len(sugerencias)), interrupt=True)

    def _on_reemplazar(self, event):
        if self.indice >= len(self.errores):
            return
        palabra, _sug = self.errores[self.indice]
        idx = self.lista.GetFocusedItem()
        nueva = ""
        if idx is not None and 0 <= idx < self.lista.GetItemCount():
            nueva = self.lista.GetItemText(idx)
        manual = self.txt_manual.GetValue().strip()
        if manual:
            nueva = manual
        if nueva:
            self.texto = spellcheck.reemplazar_palabra(self.texto, palabra, nueva)
            speak(_("Reemplazado por {0}.").format(nueva), interrupt=True)
        self.indice += 1
        self._mostrar_actual()

    def _on_ignorar(self, event):
        self.indice += 1
        self._mostrar_actual()

    def get_texto(self):
        return self.texto
