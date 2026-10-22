# -*- coding: utf-8 -*-
"""Diálogos de gestión de PeerTube (Fase 5): listas de reproducción, canales e importaciones."""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.peertube import peertube_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                   mostrar_info, confirmar, habilitar_escape_cierre)


def _ejecutar(parent, fn, ok_msg="", tras=None):
    def _bg(ctx):
        return fn()

    def _fin(res, err):
        vivo = bool(parent) if parent is not None else False
        if err:
            notificar(_("No se pudo completar la operación: {0}").format(err))
            return
        if tras and vivo:
            tras(res)
        if ok_msg:
            speak(ok_msg, interrupt=True)
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


class PeerTubeListasDialog(wx.Dialog):
    """Gestión de las listas de reproducción del usuario."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mis listas de reproducción"), size=(560, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Mis listas"))
        self.account_id = account_id
        self._listas = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Listas:")), 0, wx.LEFT | wx.TOP, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Listas de reproducción"))
        self.lista.InsertColumn(0, _("Nombre"), width=300)
        self.lista.InsertColumn(1, _("Vídeos"), width=90)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_new = wx.Button(self, label=_("&Nueva..."))
        b_new.Bind(wx.EVT_BUTTON, lambda e: self._nueva())
        b_del = wx.Button(self, label=_("&Borrar"))
        b_del.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        bs.Add(b_new, 0, wx.RIGHT, 4)
        bs.Add(b_del, 0, wx.RIGHT, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _cargar(self):
        speak(_("Cargando listas..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.mis_listas(self.account_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las listas: {0}").format(err))
                return
            self._listas = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, p in enumerate(self._listas):
                nombre = p["display"] + (_(" (ver más tarde)") if p.get("es_ver_mas_tarde") else "")
                pos = self.lista.InsertItem(i, nombre)
                self.lista.SetItem(pos, 1, str(p.get("num_videos", 0)))
                try:
                    self.lista.establecer_lectura(pos, _("{0}, {1} vídeos").format(
                        nombre, p.get("num_videos", 0)))
                except Exception:
                    pass
            if self._listas:
                self.lista.Focus(0); self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _nueva(self):
        dlg = CustomTextEntryDialog(self, _("Nombre de la nueva lista:"), _("Nueva lista"))
        if dlg.ShowModal() == wx.ID_OK:
            nombre = dlg.GetValue().strip()
            if nombre:
                _ejecutar(self, lambda: peertube_service.crear_lista(self.account_id, nombre),
                          _("Lista creada."), tras=lambda r: self._cargar())
        dlg.Destroy()

    def _borrar(self):
        i = self.lista.GetFirstSelected()
        if i < 0 or i >= len(self._listas):
            speak(_("Selecciona una lista."), interrupt=True)
            return
        p = self._listas[i]
        if p.get("es_ver_mas_tarde"):
            speak(_("La lista 'ver más tarde' no se puede borrar."), interrupt=True)
            return
        if confirmar(self, _("¿Borrar la lista «{0}»?").format(p["display"]),
                     _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: peertube_service.borrar_lista(self.account_id, p["id"]),
                  _("Lista borrada."), tras=lambda r: self._cargar())


class PeerTubeCanalesDialog(wx.Dialog):
    """Gestión de los canales del usuario."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mis canales"), size=(560, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Mis canales"))
        self.account_id = account_id
        self._canales = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Canales:")), 0, wx.LEFT | wx.TOP, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Canales"))
        self.lista.InsertColumn(0, _("Nombre"), width=260)
        self.lista.InsertColumn(1, _("Seguidores"), width=110)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, cb in ((_("&Nuevo..."), self._nuevo), (_("&Editar..."), self._editar),
                             (_("&Borrar"), self._borrar)):
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, lambda e, f=cb: f())
            bs.Add(b, 0, wx.RIGHT, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _cargar(self):
        speak(_("Cargando canales..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.mis_canales(self.account_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los canales: {0}").format(err))
                return
            self._canales = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, c in enumerate(self._canales):
                pos = self.lista.InsertItem(i, c["display"])
                self.lista.SetItem(pos, 1, str(c.get("followers", 0)))
                try:
                    self.lista.establecer_lectura(pos, _("{0}, {1} seguidores").format(
                        c["display"], c.get("followers", 0)))
                except Exception:
                    pass
            if self._canales:
                self.lista.Focus(0); self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _sel(self):
        i = self.lista.GetFirstSelected()
        return self._canales[i] if 0 <= i < len(self._canales) else None

    def _nuevo(self):
        d1 = CustomTextEntryDialog(self, _("Identificador del canal (sin espacios):"), _("Nuevo canal"))
        if d1.ShowModal() != wx.ID_OK:
            d1.Destroy(); return
        name = d1.GetValue().strip().replace(" ", "")
        d1.Destroy()
        d2 = CustomTextEntryDialog(self, _("Nombre visible del canal:"), _("Nuevo canal"))
        display = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if name and display:
            _ejecutar(self, lambda: peertube_service.crear_canal(self.account_id, name, display),
                      _("Canal creado."), tras=lambda r: self._cargar())

    def _editar(self):
        c = self._sel()
        if not c:
            return
        d = CustomTextEntryDialog(self, _("Nuevo nombre visible:"), _("Editar canal"),
                                  value=c.get("display", ""))
        if d.ShowModal() == wx.ID_OK:
            nuevo = d.GetValue().strip()
            if nuevo:
                _ejecutar(self, lambda: peertube_service.editar_canal(
                    self.account_id, c["handle"], displayName=nuevo), _("Canal actualizado."),
                    tras=lambda r: self._cargar())
        d.Destroy()

    def _borrar(self):
        c = self._sel()
        if not c:
            return
        if confirmar(self, _("¿Borrar el canal «{0}»? Se borrarán también sus vídeos.").format(
                c["display"]), _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: peertube_service.borrar_canal(self.account_id, c["handle"]),
                  _("Canal borrado."), tras=lambda r: self._cargar())


class PeerTubeImportacionesDialog(wx.Dialog):
    """Estado de las importaciones del usuario."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mis importaciones"), size=(600, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Mis importaciones"))
        self.account_id = account_id
        self._items = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Importaciones"))
        self.lista.InsertColumn(0, _("Título"), width=320)
        self.lista.InsertColumn(1, _("Estado"), width=140)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, cb in ((_("&Cancelar"), lambda: self._accion("cancelar")),
                             (_("&Reintentar"), lambda: self._accion("reintentar")),
                             (_("&Borrar"), lambda: self._accion("borrar")),
                             (_("&Actualizar"), self._cargar)):
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, lambda e, f=cb: f())
            bs.Add(b, 0, wx.RIGHT, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _cargar(self):
        def _bg(ctx):
            return peertube_service.listar_importaciones(self.account_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las importaciones: {0}").format(err))
                return
            self._items = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, it in enumerate(self._items):
                pos = self.lista.InsertItem(i, it.get("titulo") or _("(sin título)"))
                self.lista.SetItem(pos, 1, it.get("estado", ""))
                try:
                    self.lista.establecer_lectura(pos, _("{0}, estado {1}").format(
                        it.get("titulo") or "", it.get("estado", "")))
                except Exception:
                    pass
            if self._items:
                self.lista.Focus(0); self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _accion(self, cual):
        i = self.lista.GetFirstSelected()
        if i < 0 or i >= len(self._items):
            speak(_("Selecciona una importación."), interrupt=True)
            return
        iid = self._items[i]["id"]
        fn = {"cancelar": peertube_service.cancelar_importacion,
              "reintentar": peertube_service.reintentar_importacion,
              "borrar": peertube_service.borrar_importacion}[cual]
        _ejecutar(self, lambda: fn(self.account_id, iid), _("Hecho."), tras=lambda r: self._cargar())
