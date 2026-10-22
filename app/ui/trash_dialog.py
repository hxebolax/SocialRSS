# -*- coding: utf-8 -*-
"""
Papelera de artículos RSS eliminados: permite restaurarlos o vaciarla.
"""

import wx
from app.core.speech import notificar
from app.services.rss.rss_service import rss_service
from .accessible_controls import AccessibleListCtrl


class TrashDialog(wx.Dialog):
    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Papelera de artículos"), size=(640, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Diálogo de la papelera de artículos"))
        self.account_id = account_id
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Artículos eliminados (se pueden restaurar):")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de artículos en la papelera")
        )
        self.lista.InsertColumn(0, _("Título"), width=420)
        self.lista.InsertColumn(1, _("Autor"), width=180)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_restaurar = wx.Button(self, label=_("&Restaurar seleccionado"))
        self.btn_restaurar.Bind(wx.EVT_BUTTON, self._on_restaurar)
        btns.Add(self.btn_restaurar, 0, wx.ALL, 5)

        self.btn_vaciar = wx.Button(self, label=_("&Vaciar papelera"))
        self.btn_vaciar.Bind(wx.EVT_BUTTON, self._on_vaciar)
        btns.Add(self.btn_vaciar, 0, wx.ALL, 5)

        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _cargar(self):
        self.lista.DeleteAllItems()
        self.articulos = rss_service.listar_papelera(self.account_id) if self.account_id else []
        for i, a in enumerate(self.articulos):
            pos = self.lista.InsertItem(i, a.title or "")
            self.lista.SetItem(pos, 1, a.author or "")
        if not self.articulos:
            notificar(_("La papelera está vacía."))

    def _seleccionado(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.articulos):
            return None
        return self.articulos[idx]

    def _on_restaurar(self, event):
        a = self._seleccionado()
        if not a:
            return
        rss_service.restaurar_de_papelera(a.id)
        self._cargar()
        notificar(_("Artículo restaurado."))

    def _on_vaciar(self, event):
        if not self.articulos:
            notificar(_("La papelera ya está vacía."))
            return
        dlg = wx.MessageDialog(self, _("¿Borrar definitivamente todos los artículos de la papelera?"),
                               _("Vaciar papelera"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if dlg.ShowModal() == wx.ID_YES:
            n = rss_service.vaciar_papelera(self.account_id)
            self._cargar()
            notificar(_("Papelera vaciada. {0} artículos borrados.").format(n))
        dlg.Destroy()
