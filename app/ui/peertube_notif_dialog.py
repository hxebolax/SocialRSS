# -*- coding: utf-8 -*-
"""Diálogo de preferencias de notificaciones de PeerTube (Fase 7).

Muestra las notificaciones más habituales como casillas (activada = recibir aviso web). Parte de las
preferencias actuales y solo cambia las mostradas, conservando el resto tal cual.
"""
import wx

from .accessible_controls import AccessibleCheckListBox, habilitar_escape_cierre

# Notificaciones que se ofrecen en el diálogo (clave de la API -> etiqueta). Valor: bitmask
# (1 = aviso web). Se muestran las de un usuario normal; las de moderación solo aplican si eres mod.
_OPCIONES = [
    ("newVideoFromSubscription", lambda: _("Nuevo vídeo de una suscripción")),
    ("newLiveFromSubscription", lambda: _("Directo de una suscripción")),
    ("newCommentOnMyVideo", lambda: _("Comentario en mis vídeos")),
    ("commentMention", lambda: _("Menciones en comentarios")),
    ("newFollow", lambda: _("Nuevo seguidor de mi canal")),
    ("myVideoPublished", lambda: _("Mis vídeos publicados")),
    ("myVideoImportFinished", lambda: _("Importaciones terminadas")),
    ("newPeerTubeVersion", lambda: _("Nuevas versiones de PeerTube")),
]
_AVISO_WEB = 1


class PeerTubeNotifPrefsDialog(wx.Dialog):
    """Preferencias de notificaciones. `self.resultado` = dict de ajustes completo, o None."""

    def __init__(self, parent, prefs_actuales):
        super().__init__(parent, title=_("Preferencias de notificaciones"), size=(420, 400),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Preferencias de notificaciones"))
        self.resultado = None
        self._prefs = dict(prefs_actuales or {})
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Recibir aviso de:")), 0, wx.ALL, 8)
        self.chk = AccessibleCheckListBox(self, choices=[o[1]() for o in _OPCIONES],
                                          name=_("Notificaciones"))
        for i, (clave, _e) in enumerate(_OPCIONES):
            try:
                valor = int(self._prefs.get(clave, 0) or 0)
            except (TypeError, ValueError):
                valor = 0
            if valor & _AVISO_WEB:
                self.chk.Check(i, True)
        s.Add(self.chk, 1, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)

    def _on_ok(self, event):
        # Parte de las preferencias actuales y actualiza solo las mostradas (activa/desactiva el bit web).
        ajustes = dict(self._prefs)
        for i, (clave, _e) in enumerate(_OPCIONES):
            try:
                valor = int(ajustes.get(clave, 0) or 0)
            except (TypeError, ValueError):
                valor = 0
            if self.chk.IsChecked(i):
                valor |= _AVISO_WEB
            else:
                valor &= ~_AVISO_WEB
            ajustes[clave] = valor
        self.resultado = ajustes
        self.EndModal(wx.ID_OK)
