# -*- coding: utf-8 -*-
"""Gestor de BORRADORES persistentes de Mastodon (auditoría N3).

Lista los borradores guardados de la cuenta activa (menú Mastodon → Borradores) y permite
abrirlos en el compositor tal cual se dejaron (texto, CW, privacidad, idioma, adjuntos,
encuesta y programación) o borrarlos. Al publicar un borrador abierto, se elimina solo.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import wx

from app.core.speech import notificar
from app.services.mastodon import mastodon_borradores
from .accessible_controls import confirmar, habilitar_escape_cierre

log = logging.getLogger(__name__)


class MastodonDraftsDialog(wx.Dialog):
    """Gestor modal de borradores. Si el usuario elige abrir uno, al cerrar el diálogo
    deja su id en `draft_elegido` (el llamador abre entonces el compositor)."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Borradores de Mastodon"), size=(560, 400),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Gestor de borradores de Mastodon"))
        self.account_id = account_id
        self.draft_elegido = None
        self._borradores = []

        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(wx.StaticText(self, label=_("Borradores guardados (del más reciente al más antiguo):")), 0, wx.ALL, 5)
        self.lista = wx.ListBox(self, style=wx.LB_SINGLE, name=_("Lista de borradores"))
        self.lista.Bind(wx.EVT_LISTBOX_DCLICK, self._on_abrir)
        self.lista.Bind(wx.EVT_KEY_DOWN, self._on_key)
        vbox.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_abrir = wx.Button(self, label=_("&Abrir en el compositor"))
        self.btn_abrir.Bind(wx.EVT_BUTTON, self._on_abrir)
        self.btn_abrir.SetDefault()
        botones.Add(self.btn_abrir, 0, wx.ALL, 5)
        self.btn_borrar = wx.Button(self, label=_("&Borrar borrador"))
        self.btn_borrar.Bind(wx.EVT_BUTTON, self._on_borrar)
        botones.Add(self.btn_borrar, 0, wx.ALL, 5)
        btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"))
        botones.Add(btn_cerrar, 0, wx.ALL, 5)
        vbox.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(vbox)
        habilitar_escape_cierre(self)
        self._recargar()
        self.CenterOnParent()
        self.lista.SetFocus()

    def _recargar(self):
        self._borradores = mastodon_borradores.listar_borradores(self.account_id)
        self.lista.Clear()
        for b in self._borradores:
            texto = " ".join((b["texto"] or "").split())
            if len(texto) > 90:
                texto = texto[:90] + "…"
            if not texto:
                texto = _("(sin texto)")
            fecha = (b["actualizado"] or "").replace("T", " ")[:16]
            ctx = b.get("contexto") or {}
            if ctx.get("in_reply_to_id"):
                texto = _("[respuesta] {0}").format(texto)
            elif ctx.get("quote_status_id"):
                texto = _("[cita] {0}").format(texto)
            self.lista.Append("{0} — {1}".format(fecha, texto))
        hay = bool(self._borradores)
        self.btn_abrir.Enable(hay)
        self.btn_borrar.Enable(hay)
        if hay:
            self.lista.SetSelection(0)

    def _seleccionado(self):
        idx = self.lista.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._borradores):
            return None
        return self._borradores[idx]

    def _on_key(self, event):
        # Intro abre el borrador; Suprimir lo borra (además de los botones).
        code = event.GetKeyCode()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._on_abrir(None)
        elif code == wx.WXK_DELETE:
            self._on_borrar(None)
        else:
            event.Skip()

    def _on_abrir(self, event):
        b = self._seleccionado()
        if not b:
            return
        self.draft_elegido = b["id"]
        self.EndModal(wx.ID_OK)

    def _on_borrar(self, event):
        b = self._seleccionado()
        if not b:
            return
        if confirmar(self, _("¿Borrar este borrador? Esta acción no se puede deshacer."),
                     _("Borrar borrador")) != wx.ID_YES:
            return
        mastodon_borradores.borrar_borrador(b["id"])
        notificar(_("Borrador borrado."))
        self._recargar()
        if not self._borradores:
            self.lista.SetFocus()


def abrir_gestor_borradores(parent, account_id: int):
    """Abre el gestor y, si el usuario elige un borrador, el compositor con él cargado.
    `parent` es la ventana principal (padre del compositor)."""
    dlg = MastodonDraftsDialog(parent, account_id)
    abierto = dlg.ShowModal() == wx.ID_OK
    draft_id = dlg.draft_elegido
    dlg.Destroy()
    if not (abierto and draft_id):
        return
    datos = mastodon_borradores.obtener_borrador(draft_id)
    if not datos:
        notificar(_("El borrador ya no existe."))
        return
    from .mastodon_compose import ComposeTootDialog
    ctx = datos.get("contexto") or {}
    try:
        comp = ComposeTootDialog(
            parent, datos["account_id"],
            in_reply_to_id=ctx.get("in_reply_to_id"),
            quote_status_id=ctx.get("quote_status_id"),
            borrador=datos.get("borrador") or {},
            draft_db_id=draft_id)
        comp.ShowModal()
        comp.Destroy()
    except Exception as e:
        log.error("No se pudo abrir el borrador %s: %s", draft_id, e)
        notificar(_("No se pudo abrir el borrador: {0}").format(e))
