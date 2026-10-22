# -*- coding: utf-8 -*-
"""
Diálogo de prueba de sonidos identificativos.

Permite elegir un paquete de sonidos (si hay más de uno) y, dentro de él, recorrer la
lista de sonidos con su descripción y nombre de archivo. Al pulsar Intro (o el botón
Reproducir) sobre un sonido, este suena, para poder identificarlo con facilidad.
"""

import wx

from app.core.config_manager import config
from app.core.speech import speak
from app.services.audio.sound_manager import (
    sound_manager, listar_paquetes_sonido, sonidos_del_paquete,
)


class DialogoPruebaSonidos(wx.Dialog):
    """Prueba de sonidos identificativos: elige paquete y escucha cada sonido."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Prueba de sonidos identificativos"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._paquetes = listar_paquetes_sonido()
        self._sonidos = []  # lista de (nombre_archivo, descripcion, ruta)

        panel = wx.Panel(self)
        v = wx.BoxSizer(wx.VERTICAL)

        # Selector de paquete: solo si hay más de uno.
        self.choice_pack = None
        if len(self._paquetes) > 1:
            v.Add(wx.StaticText(panel, label=_("&Paquete de sonidos:")), 0, wx.LEFT | wx.TOP, 8)
            self.choice_pack = wx.Choice(panel, choices=self._paquetes)
            actual = config.get("audio", "sound_pack", "default")
            if actual in self._paquetes:
                self.choice_pack.SetSelection(self._paquetes.index(actual))
            else:
                self.choice_pack.SetSelection(0)
            self.choice_pack.Bind(wx.EVT_CHOICE, lambda e: self._cargar_lista())
            v.Add(self.choice_pack, 0, wx.EXPAND | wx.ALL, 8)

        v.Add(wx.StaticText(panel, label=_("&Sonidos (Intro para escuchar):")), 0, wx.LEFT, 8)
        self.lst = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.lst.InsertColumn(0, _("Descripción"), width=380)
        self.lst.InsertColumn(1, _("Archivo"), width=160)
        v.Add(self.lst, 1, wx.EXPAND | wx.ALL, 8)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_reproducir = wx.Button(panel, label=_("&Reproducir"))
        btn_cerrar = wx.Button(panel, wx.ID_CANCEL, _("Cerrar"))
        botones.Add(self.btn_reproducir, 0, wx.RIGHT, 8)
        botones.Add(btn_cerrar, 0)
        v.Add(botones, 0, wx.ALL | wx.ALIGN_RIGHT, 8)

        panel.SetSizer(v)
        self.SetSize((620, 460))

        self.btn_reproducir.Bind(wx.EVT_BUTTON, lambda e: self._reproducir_seleccion())
        self.lst.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._reproducir_seleccion())
        self.lst.Bind(wx.EVT_KEY_DOWN, self._on_key)

        self._cargar_lista()
        self.lst.SetFocus()

    def _paquete_actual(self):
        if self.choice_pack is not None:
            idx = self.choice_pack.GetSelection()
            if 0 <= idx < len(self._paquetes):
                return self._paquetes[idx]
        return self._paquetes[0] if self._paquetes else "default"

    def _cargar_lista(self):
        self.lst.DeleteAllItems()
        self._sonidos = sonidos_del_paquete(self._paquete_actual())
        for i, (fname, desc, _ruta) in enumerate(self._sonidos):
            self.lst.InsertItem(i, desc)
            self.lst.SetItem(i, 1, fname)
        if self._sonidos:
            self.lst.Select(0)
            self.lst.Focus(0)
        else:
            speak(_("Este paquete no tiene sonidos."), interrupt=True)

    def _on_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._reproducir_seleccion()
        else:
            event.Skip()

    def _reproducir_seleccion(self):
        i = self.lst.GetFirstSelected()
        if i < 0 or i >= len(self._sonidos):
            return
        fname, desc, ruta = self._sonidos[i]
        if not sound_manager._initialized:
            sound_manager.inicializar()
        sound_manager.play_file(ruta)
