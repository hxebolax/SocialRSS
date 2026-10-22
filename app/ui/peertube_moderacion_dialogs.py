# -*- coding: utf-8 -*-
"""Diálogos de moderación de PeerTube (Fase 8): cola de denuncias y vídeos bloqueados.

Las acciones destructivas (borrar una denuncia, resolver) piden confirmación y explican su efecto.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.peertube import peertube_service
from .accessible_controls import (AccessibleListCtrl, CustomTextEntryDialog, mostrar_info,
                                   confirmar, habilitar_escape_cierre)


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


class PeerTubeAbusosDialog(wx.Dialog):
    """Cola de denuncias (abusos) de la instancia. `solo_mias`=True muestra las del propio usuario."""

    def __init__(self, parent, account_id, *, solo_mias=False):
        titulo = _("Mis denuncias") if solo_mias else _("Cola de denuncias")
        super().__init__(parent, title=titulo, size=(720, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        self.account_id = account_id
        self.solo_mias = solo_mias
        self._items = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Denuncias:")), 0, wx.LEFT | wx.TOP, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Denuncias"))
        for i, (enc, an) in enumerate([(_("Motivo"), 260), (_("Estado"), 100),
                                       (_("Denunciado"), 160), (_("Vídeo"), 160)]):
            self.lista.InsertColumn(i, enc, width=an)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        botones = [(_("A&ctualizar"), self._cargar)]
        if not self.solo_mias:
            botones = [(_("&Aceptar"), lambda: self._resolver(True)),
                       (_("&Rechazar"), lambda: self._resolver(False)),
                       (_("&Mensajes..."), self._mensajes),
                       (_("&Borrar"), self._borrar)] + botones
        for etiqueta, cb in botones:
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, lambda e, f=cb: f())
            bs.Add(b, 0, wx.RIGHT, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _cargar(self):
        speak(_("Cargando denuncias..."), interrupt=True)

        def _bg(ctx):
            if self.solo_mias:
                return peertube_service.mis_denuncias(self.account_id)
            return peertube_service.listar_abusos(self.account_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las denuncias: {0}").format(err))
                return
            self._items = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, a in enumerate(self._items):
                objeto = a.get("video_titulo") or (_("Comentario") if a.get("comentario_denunciado")
                                                   else a.get("denunciado", ""))
                pos = self.lista.InsertItem(i, a.get("motivo", "")[:120])
                self.lista.SetItem(pos, 1, a.get("estado", ""))
                self.lista.SetItem(pos, 2, a.get("denunciado", ""))
                self.lista.SetItem(pos, 3, objeto)
                try:
                    self.lista.establecer_lectura(pos, _("{0}. Estado: {1}. Contra {2}. {3}").format(
                        a.get("motivo", ""), a.get("estado", ""), a.get("denunciado", ""), objeto))
                except Exception:
                    pass
            if self._items:
                self.lista.Focus(0); self.lista.Select(0)
            speak(_("{0} denuncias.").format(len(self._items)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _sel(self):
        i = self.lista.GetFirstSelected()
        return self._items[i] if 0 <= i < len(self._items) else None

    def _resolver(self, aceptar):
        a = self._sel()
        if not a:
            speak(_("Selecciona una denuncia."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self, _("Comentario de moderación (opcional):"),
                                    _("Aceptar denuncia") if aceptar else _("Rechazar denuncia"),
                                    multiline=True)
        comentario = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        fn = (peertube_service.aceptar_abuso if aceptar else peertube_service.rechazar_abuso)
        _ejecutar(self, lambda: fn(self.account_id, a["id"], comentario=comentario),
                  _("Denuncia aceptada.") if aceptar else _("Denuncia rechazada."),
                  tras=lambda r: self._cargar())

    def _borrar(self):
        a = self._sel()
        if not a:
            return
        if confirmar(self, _("¿Borrar esta denuncia? Se elimina del sistema de moderación."),
                     _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: peertube_service.borrar_abuso(self.account_id, a["id"]),
                  _("Denuncia borrada."), tras=lambda r: self._cargar())

    def _mensajes(self):
        a = self._sel()
        if not a:
            return

        def _bg(ctx):
            return peertube_service.mensajes_abuso(self.account_id, a["id"])

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar los mensajes: {0}").format(err))
                return
            texto = "\n\n".join(_("{0}: {1}").format(m.get("autor", ""), m.get("mensaje", ""))
                                for m in (res or [])) or _("(sin mensajes)")
            mostrar_info(self, texto, _("Mensajes de la denuncia"))
            dlg = CustomTextEntryDialog(self, _("Añadir un mensaje (opcional):"),
                                        _("Mensaje de moderación"), multiline=True)
            if dlg.ShowModal() == wx.ID_OK:
                m = dlg.GetValue().strip()
                if m:
                    _ejecutar(self, lambda: peertube_service.añadir_mensaje_abuso(
                        self.account_id, a["id"], m), _("Mensaje añadido."))
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class PeerTubeBloqueadosDialog(wx.Dialog):
    """Vídeos bloqueados en la instancia."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Vídeos bloqueados"), size=(640, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Vídeos bloqueados"))
        self.account_id = account_id
        self._items = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Vídeos bloqueados"))
        self.lista.InsertColumn(0, _("Título"), width=320)
        self.lista.InsertColumn(1, _("Motivo"), width=220)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_des = wx.Button(self, label=_("&Desbloquear"))
        b_des.Bind(wx.EVT_BUTTON, lambda e: self._desbloquear())
        b_act = wx.Button(self, label=_("A&ctualizar"))
        b_act.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        bs.Add(b_des, 0, wx.RIGHT, 4)
        bs.Add(b_act, 0, wx.RIGHT, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _cargar(self):
        def _bg(ctx):
            return peertube_service.listar_videos_bloqueados(self.account_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los vídeos bloqueados: {0}").format(err))
                return
            self._items = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, b in enumerate(self._items):
                pos = self.lista.InsertItem(i, b.get("titulo", ""))
                self.lista.SetItem(pos, 1, b.get("razon", ""))
                try:
                    self.lista.establecer_lectura(pos, _("{0}. Motivo: {1}").format(
                        b.get("titulo", ""), b.get("razon", "") or _("(sin motivo)")))
                except Exception:
                    pass
            if self._items:
                self.lista.Focus(0); self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _desbloquear(self):
        i = self.lista.GetFirstSelected()
        if i < 0 or i >= len(self._items):
            speak(_("Selecciona un vídeo."), interrupt=True)
            return
        b = self._items[i]
        if confirmar(self, _("¿Desbloquear «{0}»? Volverá a estar disponible.").format(
                b.get("titulo", "")), _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: peertube_service.desbloquear_video(self.account_id, b["video_id"]),
                  _("Vídeo desbloqueado."), tras=lambda r: self._cargar())
