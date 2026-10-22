# -*- coding: utf-8 -*-
"""Visor de comentarios de un vídeo de PeerTube (Fase 4).

Muestra los hilos de primer nivel y, al abrir uno, su árbol de respuestas APLANADO con el nivel de
profundidad anunciado (accesible). Permite comentar, responder y borrar.
"""
from datetime import datetime, timezone

import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.peertube import peertube_service
from .accessible_controls import (AccessibleListCtrl, CustomTextEntryDialog, confirmar,
                                   habilitar_escape_cierre)


def _fmt_fecha(iso):
    try:
        if not iso:
            return ""
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


class PeerTubeComentariosDialog(wx.Dialog):
    """Comentarios de un vídeo: hilos y árbol de respuestas."""

    def __init__(self, parent, account_id, video_uuid, titulo_video):
        super().__init__(parent, title=_("Comentarios"), size=(720, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Comentarios"))
        self.account_id = account_id
        self.video_uuid = video_uuid
        self._filas = []          # comentarios mostrados actualmente
        self._thread_id = None    # None = vista de hilos; si no, dentro de un hilo
        self._init_ui(titulo_video)
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar_hilos()

    def _init_ui(self, titulo_video):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Vídeo: {0}").format(titulo_video)), 0, wx.ALL, 6)
        self.lbl_vista = wx.StaticText(self, label=_("Hilos (Intro para ver las respuestas):"))
        s.Add(self.lbl_vista, 0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Comentarios"))
        self.lista.InsertColumn(0, _("Autor"), width=180)
        self.lista.InsertColumn(1, _("Comentario"), width=360)
        self.lista.InsertColumn(2, _("Respuestas"), width=90)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._abrir_o_volver(e.GetIndex()))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_nuevo = wx.Button(self, label=_("&Nuevo comentario..."))
        b_nuevo.Bind(wx.EVT_BUTTON, lambda e: self._nuevo())
        self.btn_responder = wx.Button(self, label=_("&Responder..."))
        self.btn_responder.Bind(wx.EVT_BUTTON, lambda e: self._responder())
        self.btn_borrar = wx.Button(self, label=_("&Borrar"))
        self.btn_borrar.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        self.btn_volver = wx.Button(self, label=_("&Volver a los hilos"))
        self.btn_volver.Bind(wx.EVT_BUTTON, lambda e: self._cargar_hilos())
        self.btn_volver.Enable(False)
        for b in (b_nuevo, self.btn_responder, self.btn_borrar, self.btn_volver):
            botones.Add(b, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    # ------------------------------------------------------------------
    def _cargar_hilos(self):
        self._thread_id = None
        self.btn_volver.Enable(False)
        self.lbl_vista.SetLabel(_("Hilos (Intro para ver las respuestas):"))
        speak(_("Cargando comentarios..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.listar_hilos(self.account_id, self.video_uuid)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los comentarios: {0}").format(err))
                return
            self._filas = res or []
            self._pintar()
            speak(_("{0} hilos.").format(len(self._filas)) if self._filas
                  else _("No hay comentarios."), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _abrir_o_volver(self, idx):
        if self._thread_id is not None:
            return  # ya dentro de un hilo; Intro no hace nada más
        c = self._sel(idx)
        if c:
            self._cargar_hilo(c["id"])

    def _cargar_hilo(self, thread_id):
        self._thread_id = thread_id
        self.btn_volver.Enable(True)
        self.lbl_vista.SetLabel(_("Respuestas del hilo:"))
        speak(_("Cargando respuestas..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.cargar_hilo(self.account_id, self.video_uuid, thread_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar el hilo: {0}").format(err))
                return
            self._filas = res or []
            self._pintar()
            speak(_("{0} comentarios en el hilo.").format(len(self._filas)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _pintar(self):
        self.lista.DeleteAllItems()
        try:
            self.lista.limpiar_lecturas()
        except Exception:
            pass
        for i, c in enumerate(self._filas):
            nivel = c.get("nivel", 0)
            prefijo = "    " * nivel
            autor = c.get("autor_display") or c.get("autor") or _("Desconocido")
            texto = (c.get("texto") or "").replace("\n", " ")
            if c.get("borrado"):
                texto = _("(comentario borrado)")
            pos = self.lista.InsertItem(i, prefijo + autor)
            self.lista.SetItem(pos, 1, texto)
            self.lista.SetItem(pos, 2, str(c.get("total_replies", 0)) if self._thread_id is None else "")
            lectura = _("Nivel {0}. {1}: {2}. {3}").format(
                nivel + 1, autor, texto, _fmt_fecha(c.get("fecha")))
            try:
                self.lista.establecer_lectura(pos, lectura)
            except Exception:
                pass
        if self._filas:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _sel(self, idx=None):
        i = self.lista.GetFirstSelected() if idx is None else idx
        return self._filas[i] if 0 <= i < len(self._filas) else None

    # ------------------------------------------------------------------ acciones
    def _nuevo(self):
        dlg = CustomTextEntryDialog(self, _("Escribe tu comentario:"), _("Nuevo comentario"),
                                    multiline=True)
        if dlg.ShowModal() == wx.ID_OK:
            texto = dlg.GetValue().strip()
            if texto:
                self._enviar(lambda: peertube_service.publicar_comentario(
                    self.account_id, self.video_uuid, texto), _("Comentario publicado."))
        dlg.Destroy()

    def _responder(self):
        c = self._sel()
        if not c or not c.get("id"):
            speak(_("Selecciona un comentario."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self, _("Tu respuesta a {0}:").format(
            c.get("autor_display") or c.get("autor") or ""), _("Responder"), multiline=True)
        if dlg.ShowModal() == wx.ID_OK:
            texto = dlg.GetValue().strip()
            if texto:
                self._enviar(lambda: peertube_service.responder_comentario(
                    self.account_id, self.video_uuid, c["id"], texto), _("Respuesta publicada."))
        dlg.Destroy()

    def _borrar(self):
        c = self._sel()
        if not c or not c.get("id"):
            speak(_("Selecciona un comentario."), interrupt=True)
            return
        if confirmar(self, _("¿Borrar este comentario?"), _("Confirmar")) != wx.ID_YES:
            return
        self._enviar(lambda: peertube_service.borrar_comentario(
            self.account_id, self.video_uuid, c["id"]), _("Comentario borrado."))

    def _enviar(self, fn, msg_ok):
        speak(_("Enviando..."), interrupt=True)

        def _bg(ctx):
            return fn()

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo completar la acción: {0}").format(err))
                return
            speak(msg_ok, interrupt=True)
            # Recargar la vista actual (hilos o el hilo abierto).
            if self._thread_id is not None:
                self._cargar_hilo(self._thread_id)
            else:
                self._cargar_hilos()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
