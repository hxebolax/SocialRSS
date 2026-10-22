# -*- coding: utf-8 -*-
"""
Diálogo de borrado de datos controlado y personalizado.
"""

import wx
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.core import data_cleanup


class CleanupDialog(wx.Dialog):
    """Permite elegir qué datos borrar (por servicio y antigüedad) y optimizar la BD."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Borrar datos / liberar espacio"), size=(560, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Diálogo de borrado de datos"))
        self._init_ui()
        self._actualizar_resumen()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)

        self.lbl_resumen = wx.StaticText(self, label="")
        s.Add(self.lbl_resumen, 0, wx.ALL, 8)

        s.Add(wx.StaticText(self, label=_("Antigüedad: borrar elementos de más de estos días (0 = todos):")), 0, wx.LEFT | wx.TOP, 8)
        self.spin_dias = wx.SpinCtrl(self, min=0, max=3650, initial=30, name=_("Días de antigüedad"))
        s.Add(self.spin_dias, 0, wx.LEFT | wx.BOTTOM, 8)

        self.cb_mastodon = wx.CheckBox(self, label=_("Publicaciones de Mastodon"), name=_("Borrar publicaciones de Mastodon"))
        s.Add(self.cb_mastodon, 0, wx.LEFT | wx.TOP, 8)
        self.cb_bluesky = wx.CheckBox(self, label=_("Publicaciones de Bluesky"), name=_("Borrar publicaciones de Bluesky"))
        s.Add(self.cb_bluesky, 0, wx.LEFT, 8)
        self.cb_wsocial = wx.CheckBox(self, label=_("Publicaciones de W Social"),
                                      name=_("Borrar publicaciones de W Social"))
        s.Add(self.cb_wsocial, 0, wx.LEFT, 8)
        self.cb_rss = wx.CheckBox(self, label=_("Artículos de RSS"), name=_("Borrar artículos de RSS"))
        s.Add(self.cb_rss, 0, wx.LEFT, 8)
        self.cb_telegram = wx.CheckBox(self, label=_("Mensajes cacheados de Telegram"), name=_("Borrar mensajes de Telegram"))
        s.Add(self.cb_telegram, 0, wx.LEFT, 8)
        self.cb_x = wx.CheckBox(self, label=_("Publicaciones de X (Twitter)"), name=_("Borrar publicaciones de X"))
        s.Add(self.cb_x, 0, wx.LEFT, 8)
        self.cb_irc = wx.CheckBox(self, label=_("Mensajes de IRC"), name=_("Borrar mensajes de IRC"))
        s.Add(self.cb_irc, 0, wx.LEFT, 8)
        self.cb_matrix = wx.CheckBox(self, label=_("Mensajes de Matrix"), name=_("Borrar mensajes de Matrix"))
        s.Add(self.cb_matrix, 0, wx.LEFT, 8)
        self.cb_xmpp = wx.CheckBox(
            self, label=_("Mensajes de XMPP (con 0 días borra también la lista de contactos y grupos)"),
            name=_("Borrar datos de XMPP"))
        self.cb_xmpp.SetToolTip(_(
            "Borra los mensajes de XMPP. Si dejas 0 días (borrar TODOS), limpia además la lista local "
            "de contactos, grupos y solicitudes: es solo una caché del servidor y se vuelve a "
            "sincronizar sola al reconectar la cuenta. Útil para empezar de cero tras re-vincular."))
        s.Add(self.cb_xmpp, 0, wx.LEFT, 8)

        self.cb_conservar = wx.CheckBox(self, label=_("Conservar elementos marcados (favoritos, destacados, leer más tarde)"), name=_("Conservar marcados"))
        self.cb_conservar.SetValue(True)
        s.Add(self.cb_conservar, 0, wx.ALL, 8)

        self.cb_vacuum = wx.CheckBox(self, label=_("Optimizar la base de datos al terminar (recomendado)"), name=_("Optimizar al terminar"))
        self.cb_vacuum.SetValue(True)
        s.Add(self.cb_vacuum, 0, wx.LEFT | wx.BOTTOM, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_borrar = wx.Button(self, label=_("&Borrar lo seleccionado"))
        self.btn_borrar.Bind(wx.EVT_BUTTON, self._on_borrar)
        btns.Add(self.btn_borrar, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _actualizar_resumen(self):
        d = data_cleanup.contar()
        self.lbl_resumen.SetLabel(_(
            "Datos actuales: {0} publicaciones de Mastodon, {1} publicaciones de Bluesky, "
            "{2} artículos de RSS, {3} mensajes de Telegram, {4} publicaciones de X, "
            "{5} mensajes de IRC, {6} de Matrix, {8} de XMPP, {9} publicaciones de W Social. "
            "Tamaño de la base de datos: {7} MB."
        ).format(d["mastodon"], d["bluesky"], d["rss"], d["telegram"], d.get("x", 0),
                 d.get("irc", 0), d.get("matrix", 0), d["tamano_mb"], d.get("xmpp", 0),
                 d.get("wsocial", 0)))

    def _on_borrar(self, event):
        if not (self.cb_mastodon.GetValue() or self.cb_bluesky.GetValue() or self.cb_rss.GetValue()
                or self.cb_telegram.GetValue() or self.cb_x.GetValue() or self.cb_irc.GetValue()
                or self.cb_matrix.GetValue() or self.cb_xmpp.GetValue()
                or self.cb_wsocial.GetValue()):
            speak(_("Seleccione al menos un tipo de datos a borrar."), interrupt=True)
            return
        dias = self.spin_dias.GetValue()
        ambito = _("de más de {0} días").format(dias) if dias > 0 else _("TODOS")
        dlg = wx.MessageDialog(
            self, _("Se borrarán los datos seleccionados {0}. Esta acción no se puede deshacer.\n\n¿Continuar?").format(ambito),
            _("Confirmar borrado"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return

        do_m = self.cb_mastodon.GetValue()
        do_b = self.cb_bluesky.GetValue()
        do_r = self.cb_rss.GetValue()
        do_t = self.cb_telegram.GetValue()
        do_x = self.cb_x.GetValue()
        do_i = self.cb_irc.GetValue()
        do_mx = self.cb_matrix.GetValue()
        do_xmpp = self.cb_xmpp.GetValue()
        do_ws = self.cb_wsocial.GetValue()
        conservar = self.cb_conservar.GetValue()
        vacuum = self.cb_vacuum.GetValue()
        self.btn_borrar.Disable()
        speak(_("Borrando datos..."), interrupt=True)

        def _bg(ctx):
            total = 0
            if do_m:
                total += data_cleanup.borrar_mastodon(dias, conservar)
            if do_b:
                total += data_cleanup.borrar_bluesky(dias, conservar)
            if do_r:
                total += data_cleanup.borrar_rss(dias, conservar)
            if do_t:
                total += data_cleanup.borrar_telegram(dias)
            if do_x:
                total += data_cleanup.borrar_x(dias, conservar)
            if do_i:
                total += data_cleanup.borrar_irc(dias)
            if do_mx:
                total += data_cleanup.borrar_matrix(dias)
            if do_xmpp:
                total += data_cleanup.borrar_xmpp(dias)
            if do_ws:
                total += data_cleanup.borrar_wsocial(dias, conservar)
            if vacuum:
                data_cleanup.optimizar()
            return total

        def _fin(res, err):
            self.btn_borrar.Enable()
            if err:
                notificar(_("Error al borrar: {0}").format(err))
            else:
                self._actualizar_resumen()
                notificar(_("Borrado completado. {0} elementos eliminados.").format(res or 0))

        task_manager.start_task(_bg, callback=_fin, pass_context=True)
