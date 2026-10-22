# -*- coding: utf-8 -*-
"""Bandeja de SALIDA de Bluesky (auditoría 6.6).

Muestra las publicaciones que no llegaron a confirmarse (fallidas, desconocidas tras un
corte de red, o aún en camino) y permite REINTENTARLAS con su mismo rkey idempotente (el
servidor rechaza duplicados, así que reintentar nunca publica dos veces) o borrarlas.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import wx

from app.core.speech import speak, notificar
from app.services.bluesky import bluesky_service
from .accessible_controls import confirmar, habilitar_escape_cierre

log = logging.getLogger(__name__)

_ESTADOS = {
    "pending": lambda: _("En camino"),
    "failed": lambda: _("Fallida (se puede reintentar)"),
    "unknown": lambda: _("Desconocida (corte de red: puede haber llegado)"),
}


class BlueskyOutboxDialog(wx.Dialog):
    """Gestor modal de la bandeja de salida de una cuenta de Bluesky."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Salida pendiente de Bluesky"), size=(640, 400),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Bandeja de salida de Bluesky"))
        self.account_id = account_id
        self._apuntes = []

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Publicaciones sin confirmar:"))
        vbox.Add(self.lbl, 0, wx.ALL, 5)
        self.lista = wx.ListBox(self, style=wx.LB_SINGLE, name=_("Publicaciones sin confirmar"))
        vbox.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_reintentar = wx.Button(self, label=_("&Reintentar (sin duplicados)"))
        self.btn_reintentar.Bind(wx.EVT_BUTTON, self._on_reintentar)
        botones.Add(self.btn_reintentar, 0, wx.ALL, 5)
        self.btn_borrar = wx.Button(self, label=_("&Borrar apunte"))
        self.btn_borrar.Bind(wx.EVT_BUTTON, self._on_borrar)
        botones.Add(self.btn_borrar, 0, wx.ALL, 5)
        btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"))
        botones.Add(btn_cerrar, 0, wx.ALL, 5)
        vbox.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(vbox)
        habilitar_escape_cierre(self)
        self.CenterOnParent()
        self.lista.SetFocus()
        self._recargar()

    def _recargar(self):
        self._apuntes = bluesky_service.listar_outbox(self.account_id)
        self.lista.Clear()
        for a in self._apuntes:
            texto = " ".join(((a.get("payload") or {}).get("texto") or "").split())
            if len(texto) > 70:
                texto = texto[:70] + "…"
            partes = [_ESTADOS.get(a["estado"], lambda e=a["estado"]: e)(),
                      texto or _("(sin texto)")]
            if a.get("error"):
                partes.append(a["error"][:80])
            partes.append(str(a.get("fecha") or "")[:16].replace("T", " "))
            self.lista.Append(" — ".join(p for p in partes if p))
        hay = bool(self._apuntes)
        if hay:
            self.lista.SetSelection(0)
        self.btn_reintentar.Enable(hay)
        self.btn_borrar.Enable(hay)
        self.lbl.SetLabel(_("Publicaciones sin confirmar ({0}):").format(len(self._apuntes))
                          if hay else _("No hay publicaciones pendientes: todo confirmado."))

    def _apunte(self):
        idx = self.lista.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._apuntes):
            return None
        return self._apuntes[idx]

    def _on_reintentar(self, event):
        a = self._apunte()
        if not a:
            return
        speak(_("Reintentando con el mismo identificador (no puede duplicarse)..."), interrupt=True)

        def _cb(ok, msg):
            if not self:
                return
            if ok:
                notificar(_("Publicación confirmada."))
            else:
                notificar(_("Sigue sin poder publicarse: {0}").format(msg))
            self._recargar()
        bluesky_service.reintentar_outbox_async(self.account_id, a["id"], callback=_cb)

    def _on_borrar(self, event):
        a = self._apunte()
        if not a:
            return
        if confirmar(self, _("¿Borrar este apunte de la bandeja de salida? Su texto se perderá."),
                     _("Borrar apunte")) != wx.ID_YES:
            return
        bluesky_service.borrar_outbox(a["id"])
        notificar(_("Apunte borrado."))
        self._recargar()
