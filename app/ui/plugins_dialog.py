# -*- coding: utf-8 -*-
"""Diálogo de gestión de complementos (plugins): activar, desactivar y configurar."""
import wx

from app.core.speech import speak, notificar
from app.core.plugins import plugin_manager
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl


class PluginsDialog(wx.Dialog):
    """Lista los complementos descubiertos; permite activar/desactivar y abrir su configuración."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Complementos"), size=(640, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Gestión de complementos"))
        self._plugins = []
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Complementos instalados (en data/plugins):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de complementos"))
        self.lista.InsertColumn(0, _("Complemento"), width=240)
        self.lista.InsertColumn(1, _("Estado"), width=110)
        self.lista.InsertColumn(2, _("Versión"), width=90)
        self.lista.InsertColumn(3, _("Autor"), width=150)
        self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_sel)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        self.txt_desc = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 80),
            name=_("Descripción del complemento"))
        s.Add(self.txt_desc, 0, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_toggle = wx.Button(self, label=_("&Activar / Desactivar"), name=_("Activar o desactivar el complemento"))
        self.btn_toggle.Bind(wx.EVT_BUTTON, self._on_toggle)
        fila.Add(self.btn_toggle, 0, wx.ALL, 4)
        self.btn_config = wx.Button(self, label=_("&Configurar..."), name=_("Configurar el complemento"))
        self.btn_config.Bind(wx.EVT_BUTTON, self._on_config)
        fila.Add(self.btn_config, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)

    def _cargar(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self._plugins = plugin_manager.listar()
        if not self._plugins:
            self.txt_desc.SetValue(_("No hay complementos instalados. Coloca complementos en la carpeta data/plugins."))
            return
        for i, p in enumerate(self._plugins):
            estado = _("Activado") if p.activo else (_("Error") if p.error else _("Desactivado"))
            pos = self.lista.InsertItem(i, p.info.name)
            self.lista.SetItem(pos, 1, estado)
            self.lista.SetItem(pos, 2, p.info.version)
            self.lista.SetItem(pos, 3, p.info.author or "")
            self.lista.establecer_lectura(pos, "{0}. {1}. v{2}. {3}".format(
                p.info.name, estado, p.info.version, p.info.author or ""))
        self.lista.Focus(0); self.lista.Select(0)
        self._mostrar_desc(0)

    def _on_sel(self, event):
        self._mostrar_desc(event.GetIndex())

    def _mostrar_desc(self, idx):
        if not (0 <= idx < len(self._plugins)):
            return
        p = self._plugins[idx]
        partes = [p.info.description or ""]
        if p.error:
            partes.append(_("Error: {0}").format(p.error))
        if p.info.website:
            partes.append(p.info.website)
        self.txt_desc.SetValue("\n".join(x for x in partes if x))
        self.btn_config.Enable(bool(p.activo and p.info.has_settings))

    def _on_toggle(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._plugins)):
            return
        p = self._plugins[idx]
        if p.activo:
            plugin_manager.desactivar(p.info.id)
            notificar(_("Complemento desactivado: {0}").format(p.info.name))
        else:
            if plugin_manager.activar(p.info.id):
                notificar(_("Complemento activado: {0}").format(p.info.name))
            else:
                notificar(_("No se pudo activar: {0}").format(p.error or p.info.name))
        # Reconstruir EN CALIENTE el menú de complementos para que sus entradas aparezcan/desaparezcan
        # sin reiniciar la app.
        try:
            self.GetParent()._reconstruir_menu_complementos()
        except Exception:
            pass
        self._cargar()
        self.lista.Focus(idx); self.lista.Select(idx)
        self._mostrar_desc(idx)

    def _on_config(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._plugins)):
            return
        p = self._plugins[idx]
        if not (p.activo and p.instancia and p.info.has_settings):
            speak(_("Este complemento no tiene configuración o no está activado."), interrupt=True)
            return
        try:
            dlg = wx.Dialog(self, title=_("Configurar: {0}").format(p.info.name),
                            size=(520, 400), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
            s = wx.BoxSizer(wx.VERTICAL)
            panel = p.instancia.get_settings_panel(dlg)
            if panel is None:
                dlg.Destroy()
                speak(_("Este complemento no ofrece panel de configuración."), interrupt=True)
                return
            s.Add(panel, 1, wx.EXPAND | wx.ALL, 8)
            s.Add(wx.Button(dlg, wx.ID_OK, _("Aceptar")), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
            dlg.SetSizer(s)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            notificar(_("Error al abrir la configuración: {0}").format(e))
