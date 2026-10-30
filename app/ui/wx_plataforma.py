# -*- coding: utf-8 -*-
"""Registro de la interfaz wxPython en la capa de plataforma (app.core.plataforma)."""

import logging

import wx

from app.core.plataforma import registrar_interfaz

log = logging.getLogger(__name__)


def _aviso(texto: str, titulo: str = None):
    app = wx.GetApp()
    if app is None:
        return
    dlg = wx.MessageDialog(app.GetTopWindow(), texto, titulo or "SocialRSS Accessible Suite",
                           wx.OK | wx.ICON_INFORMATION)
    dlg.ShowModal()
    dlg.Destroy()


def _toast(texto: str, titulo: str = None):
    import wx.adv
    if wx.GetApp() is None:
        return
    n = wx.adv.NotificationMessage(titulo or "SocialRSS", texto)
    try:
        n.SetFlags(wx.ICON_INFORMATION)
    except Exception:
        pass
    n.Show(timeout=wx.adv.NotificationMessage.Timeout_Auto)


def _confirmar(texto: str, titulo: str = "") -> bool:
    dlg = wx.MessageDialog(None, texto, titulo or "SocialRSS",
                           wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
    try:
        return dlg.ShowModal() == wx.ID_YES
    finally:
        dlg.Destroy()


def _copiar(texto: str) -> bool:
    if not wx.TheClipboard.Open():
        return False
    try:
        wx.TheClipboard.SetData(wx.TextDataObject(texto))
        wx.TheClipboard.Flush()
        return True
    finally:
        wx.TheClipboard.Close()


def registrar():
    registrar_interfaz(
        "wx",
        llamar_en_ui=wx.CallAfter,
        es_hilo_ui=wx.IsMainThread,
        hay_interfaz=lambda: wx.GetApp() is not None,
        mostrar_aviso=_aviso,
        mostrar_toast=_toast,
        confirmar=_confirmar,
        # En Windows se conserva win32clipboard (comportamiento original); en otros sistemas, wx.
        copiar=None if __import__("sys").platform == "win32" else _copiar,
    )
