# -*- coding: utf-8 -*-
"""Mixin del MainFrame: submenú "Administración" del menú XMPP (tanda X7).

El submenú solo se construye si la preferencia xmpp.admin_tools está activa (Preferencias → XMPP).
Reúne la administración de servidor (comandos ad-hoc 0133/0050 como formularios) y el registro en
banda (0077): cambiar tu contraseña, crear una cuenta nueva y cancelar (borrar) tu cuenta.

OJO (regla de la casa): estos handlers NUNCA asignan self.active_xmpp_account_id; usan
_xmpp_cuenta_o_primera() y pasan la cuenta como parámetro.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager


class _Mf_xmpp_admin:
    def _xmpp_admin_comandos_servidor(self):
        acc = self._xmpp_cuenta_o_primera()
        if not self._xmpp_admin_listo(acc):
            return
        from .xmpp_admin_dialogs import XMPPComandosDialog
        dlg = XMPPComandosDialog(self, acc, jid=None, titulo=_("Comandos del servidor"))
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_admin_comando_adhoc(self):
        acc = self._xmpp_cuenta_o_primera()
        if not self._xmpp_admin_listo(acc):
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("JID cuyos comandos quieres ver (déjalo vacío para tu "
                                          "servidor; puede ser un componente o un contacto):"),
                                  _("Ejecutar comando ad-hoc"))
        jid = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if jid is None:
            return
        from .xmpp_admin_dialogs import XMPPComandosDialog
        dlg = XMPPComandosDialog(self, acc, jid=(jid or None),
                                 titulo=_("Comandos de {0}").format(jid) if jid else _("Comandos del servidor"))
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_admin_cambiar_password(self):
        acc = self._xmpp_cuenta_o_primera()
        if not self._xmpp_admin_listo(acc):
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nueva contraseña para tu cuenta:"),
                                  _("Cambiar contraseña"), password=True)
        nueva = d.GetValue() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if not nueva:
            return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.cambiar_password(acc, nueva)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Contraseña cambiada.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)

    def _xmpp_admin_crear_cuenta(self):
        """Crear una cuenta NUEVA en un servidor que permita registro en banda (0077)."""
        from app.core.feature_flags import xmpp_libreria_instalada
        if not xmpp_libreria_instalada():
            notificar(_("La librería de XMPP (slixmpp) no está instalada."))
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("JID de la cuenta nueva (usuario@servidor):"),
                                  _("Crear cuenta nueva"))
        jid = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not jid:
            return
        d = CustomTextEntryDialog(self, _("Contraseña para la cuenta nueva:"),
                                  _("Crear cuenta nueva"), password=True)
        pw = d.GetValue() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not pw:
            return
        d = CustomTextEntryDialog(self, _("Correo (opcional, algunos servidores lo piden):"),
                                  _("Crear cuenta nueva"))
        email = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        speak(_("Creando la cuenta..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.registrar_cuenta_nueva(jid, pw, email)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo crear la cuenta: {0}").format(err))
                return
            notificar(_("Cuenta creada y añadida. Ya puedes conectarla desde el menú XMPP."))
            try:
                self._crear_menus()
            except Exception:
                pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _xmpp_admin_cancelar_cuenta(self):
        """Cancelar (BORRAR) tu cuenta en el servidor (0077 remove). Irreversible."""
        acc = self._xmpp_cuenta_o_primera()
        if not self._xmpp_admin_listo(acc):
            return
        from .accessible_controls import confirmar
        if confirmar(self, _("Vas a BORRAR tu cuenta del servidor de forma permanente. Se perderá "
                             "en el servidor y se quitará de la aplicación. ¿Seguro?"),
                     _("Cancelar cuenta")) != wx.ID_YES:
            return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.cancelar_cuenta_servidor(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo cancelar la cuenta: {0}").format(err))
                return
            notificar(_("Cuenta cancelada y eliminada."))
            try:
                self._crear_menus()
                if getattr(self, "xmpp_ctrl", None):
                    self.xmpp_ctrl.construir_servicio()
            except Exception:
                pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- utilidades ----------------
    def _xmpp_admin_listo(self, acc):
        """Comprueba cuenta + conexión; avisa si falta algo. Devuelve True si se puede seguir."""
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return False
        from app.services.xmpp.xmpp_service import xmpp_service
        if not xmpp_service.esta_conectada(acc):
            speak(_("Conecta la cuenta de XMPP primero."), interrupt=True)
            return False
        return True
