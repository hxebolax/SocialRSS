# -*- coding: utf-8 -*-
"""Versiones anteriores de un artículo (menú del artículo → "Versiones anteriores..."): la lista
de versiones guardadas cuando el medio corrigió la noticia y, para la elegida, qué frases se
quitaron y cuáles se añadieron respecto a la versión actual."""
import wx

from app.services.rss import rss_versiones
from .accessible_controls import AccessibleListCtrl, habilitar_escape_cierre


class VersionesArticuloDialog(wx.Dialog):
    def __init__(self, parent, article_id: str):
        super().__init__(parent, title=_("Versiones anteriores del artículo"), size=(720, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Versiones anteriores del artículo"))
        self.article_id = article_id
        self.versiones = rss_versiones.listar(article_id)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Versiones (la más reciente primero):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, name=_("Versiones"))
        self.lista.InsertColumn(0, _("Guardada"), width=170)
        self.lista.InsertColumn(1, _("Título de entonces"), width=500)
        for i, (_vid, fecha, titulo) in enumerate(self.versiones):
            self.lista.InsertItem(i, fecha.replace("T", " "))
            self.lista.SetItem(i, 1, titulo)
        self.lista.Bind(wx.EVT_LIST_ITEM_FOCUSED, lambda e: self._mostrar())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("&Cambios respecto a la versión actual:")), 0, wx.ALL, 6)
        self.txt = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(680, 220),
                               name=_("Cambios respecto a la versión actual"))
        self.txt.SetMinSize((640, 200))
        s.Add(self.txt, 2, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self.Layout()
        self.CenterOnParent()
        if self.versiones:
            self.lista.Select(0)
            self.lista.Focus(0)
            self._mostrar()
        self.lista.SetFocus()

    def _mostrar(self):
        i = self.lista.GetFocusedItem()
        if 0 <= i < len(self.versiones):
            self.txt.ChangeValue(rss_versiones.diferencias(self.article_id, self.versiones[i][0]))


def mostrar_versiones(parent, article_id: str):
    dlg = VersionesArticuloDialog(parent, article_id)
    dlg.ShowModal()
    dlg.Destroy()
