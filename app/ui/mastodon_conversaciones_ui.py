# -*- coding: utf-8 -*-
"""Conversaciones de mensajes directos de Mastodon (auditoría N6).

Lista REAL de conversaciones del servidor (no inferida de los estados del buffer de
directos): participantes, último mensaje, no leída y fecha. Desde aquí se abre el contexto
completo de la conversación (el visor de conversación de la app) y se marca como leída.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import wx

from app.core.speech import speak, notificar
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import habilitar_escape_cierre

log = logging.getLogger(__name__)


class ConversacionesDialog(wx.Dialog):
    """Gestor modal de conversaciones directas. Si el usuario abre una, al cerrarse deja en
    `conversacion_abrir` el db_id local del último mensaje (el llamador abre el contexto)."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Conversaciones de mensajes directos"), size=(640, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Conversaciones directas de Mastodon"))
        self.account_id = account_id
        self.conversacion_abrir = None
        self._convs = []

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Cargando conversaciones..."))
        vbox.Add(self.lbl, 0, wx.ALL, 5)
        self.lista = wx.ListBox(self, style=wx.LB_SINGLE, name=_("Lista de conversaciones"))
        self.lista.Bind(wx.EVT_LISTBOX_DCLICK, self._on_abrir)
        self.lista.Bind(wx.EVT_KEY_DOWN, self._on_key)
        vbox.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_abrir = wx.Button(self, label=_("&Abrir conversación"))
        self.btn_abrir.Bind(wx.EVT_BUTTON, self._on_abrir)
        self.btn_abrir.SetDefault()
        botones.Add(self.btn_abrir, 0, wx.ALL, 5)
        self.btn_leida = wx.Button(self, label=_("Marcar como &leída"))
        self.btn_leida.Bind(wx.EVT_BUTTON, self._on_leida)
        botones.Add(self.btn_leida, 0, wx.ALL, 5)
        self.btn_borrar = wx.Button(self, label=_("&Quitar de mi lista"))
        self.btn_borrar.Bind(wx.EVT_BUTTON, self._on_borrar)
        botones.Add(self.btn_borrar, 0, wx.ALL, 5)
        btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"))
        botones.Add(btn_cerrar, 0, wx.ALL, 5)
        vbox.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(vbox)
        habilitar_escape_cierre(self)
        self.btn_abrir.Disable()
        self.btn_leida.Disable()
        self.btn_borrar.Disable()
        self.CenterOnParent()
        self.lista.SetFocus()
        self._cargar()

    def _cargar(self):
        def _cb(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron obtener las conversaciones: {0}").format(err))
                return
            self._convs = res or []
            self._pintar()
        mastodon_service.obtener_conversaciones_async(self.account_id, callback=_cb)

    def _pintar(self):
        self.lista.Clear()
        for c in self._convs:
            partes = []
            if c.get("unread"):
                partes.append(_("[No leída]"))
            partes.append(c.get("participantes") or _("(sin participantes)"))
            if c.get("extracto"):
                partes.append("«{0}»".format(c["extracto"]))
            if c.get("fecha"):
                partes.append(str(c["fecha"])[:16].replace("T", " "))
            self.lista.Append(" — ".join(partes))
        hay = bool(self._convs)
        if hay:
            self.lista.SetSelection(0)
        self.btn_abrir.Enable(hay)
        self.btn_leida.Enable(hay)
        self.btn_borrar.Enable(hay)
        no_leidas = sum(1 for c in self._convs if c.get("unread"))
        if hay:
            self.lbl.SetLabel(_("Conversaciones ({0}, {1} sin leer):").format(len(self._convs), no_leidas))
        else:
            self.lbl.SetLabel(_("No tienes conversaciones de mensajes directos."))

    def _conv(self):
        idx = self.lista.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._convs):
            return None
        return self._convs[idx]

    def _on_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._on_abrir(None)
        else:
            event.Skip()

    def _on_abrir(self, event):
        c = self._conv()
        if not c:
            return
        if not c.get("last_db_id"):
            notificar(_("Esta conversación no tiene un último mensaje que abrir."))
            return
        self.conversacion_abrir = c["last_db_id"]
        # Abrirla también la marca como leída en el servidor (comportamiento esperable).
        if c.get("unread"):
            mastodon_service.marcar_conversacion_leida_async(
                self.account_id, c["conversation_id"], callback=lambda r, e: None)
        self.EndModal(wx.ID_OK)

    def _on_leida(self, event):
        c = self._conv()
        if not c:
            return
        if not c.get("unread"):
            speak(_("Esta conversación ya está leída."), interrupt=True)
            return

        def _cb(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo marcar como leída: {0}").format(err))
                return
            c["unread"] = 0
            idx = self.lista.GetSelection()
            self._pintar()
            if idx != wx.NOT_FOUND and idx < self.lista.GetCount():
                self.lista.SetSelection(idx)
            notificar(_("Conversación marcada como leída."))
        mastodon_service.marcar_conversacion_leida_async(self.account_id, c["conversation_id"], callback=_cb)


    def _on_borrar(self, event):
        c = self._conv()
        if not c:
            return
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Quitar esta conversación de tu lista? Los mensajes no se borran para "
                             "nadie; volverá a aparecer si llega un mensaje nuevo."),
                     _("Quitar conversación")) != wx.ID_YES:
            return
        idx = self.lista.GetSelection()

        def _cb(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo quitar la conversación: {0}").format(err))
                return
            self._convs = [x for x in self._convs if x is not c]
            self._pintar()
            if self._convs:
                self.lista.SetSelection(min(max(idx, 0), len(self._convs) - 1))
            notificar(_("Conversación quitada de tu lista."))
        mastodon_service.borrar_conversacion_async(self.account_id, c["conversation_id"], callback=_cb)


def abrir_conversaciones(frame, account_id: int):
    """Abre el gestor y, si el usuario elige una conversación, su contexto completo con el
    visor de conversación de la ventana principal (frame)."""
    dlg = ConversacionesDialog(frame, account_id)
    abierto = dlg.ShowModal() == wx.ID_OK
    db_id = dlg.conversacion_abrir
    dlg.Destroy()
    if abierto and db_id:
        try:
            frame._on_ver_detalles_toot(db_id)
        except Exception as e:
            log.error("No se pudo abrir el contexto de la conversación: %s", e)
            notificar(_("No se pudo abrir la conversación: {0}").format(e))
