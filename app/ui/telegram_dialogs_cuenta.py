# -*- coding: utf-8 -*-
"""Diálogos de login y QR de Telegram.
"""
import logging
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service
from .accessible_controls import AccessibleTextCtrl


log = logging.getLogger(__name__)

class TelegramLoginDialog(wx.Dialog):
    """Asistente de inicio de sesión en Telegram por teléfono, código y 2FA."""

    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Asistente de Inicio de Sesión en Telegram"),
            size=(620, 430),
            style=wx.DEFAULT_DIALOG_STYLE,
            name=_("Diálogo de login de Telegram")
        )
        self.account_id = None
        self._reenviar_call_later = None
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, self._on_cancel)
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("1. Número de teléfono (con prefijo internacional, ej: +34600112233):")), 0, wx.ALL, 5)
        self.txt_phone = AccessibleTextCtrl(
            self, name=_("Número de teléfono"),
            ayuda_texto=_("Introduzca su número de teléfono asociado a Telegram con el prefijo del país.")
        )
        sizer.Add(self.txt_phone, 0, wx.EXPAND | wx.ALL, 5)

        self.btn_enviar_codigo = wx.Button(self, label=_("Enviar código"), name=_("Enviar código de verificación"))
        self.btn_enviar_codigo.Bind(wx.EVT_BUTTON, self._on_enviar_codigo)
        sizer.Add(self.btn_enviar_codigo, 0, wx.ALL, 5)

        self.lbl_estado_codigo = wx.StaticText(self, label=_("Estado del código: sin enviar."))
        self.lbl_estado_codigo.Wrap(580)
        sizer.Add(self.lbl_estado_codigo, 0, wx.EXPAND | wx.ALL, 5)

        self.btn_reenviar_codigo = wx.Button(self, label=_("Reenviar código"), name=_("Reenviar código por el siguiente método disponible"))
        self.btn_reenviar_codigo.Bind(wx.EVT_BUTTON, self._on_reenviar_codigo)
        self.btn_reenviar_codigo.Disable()
        sizer.Add(self.btn_reenviar_codigo, 0, wx.ALL, 5)

        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.ALL, 8)

        sizer.Add(wx.StaticText(self, label=_("2. Código recibido en Telegram:")), 0, wx.ALL, 5)
        self.txt_code = AccessibleTextCtrl(
            self, name=_("Código de verificación"),
            ayuda_texto=_("Introduzca el código que Telegram le ha enviado a otra sesión o por SMS.")
        )
        self.txt_code.Disable()
        sizer.Add(self.txt_code, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("3. Contraseña de verificación en dos pasos (solo si la tiene activada):")), 0, wx.ALL, 5)
        self.txt_password = AccessibleTextCtrl(
            self, style=wx.TE_PASSWORD, name=_("Contraseña de dos factores"),
            ayuda_texto=_("Si tiene activada la verificación en dos pasos, introduzca aquí su contraseña.")
        )
        self.txt_password.Disable()
        sizer.Add(self.txt_password, 0, wx.EXPAND | wx.ALL, 5)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_verificar = wx.Button(self, wx.ID_OK, _("Verificar y acceder"), name=_("Verificar código y acceder"))
        self.btn_verificar.Bind(wx.EVT_BUTTON, self._on_verificar)
        self.btn_verificar.Disable()
        btn_sizer.Add(self.btn_verificar, 0, wx.ALL, 5)

        self.btn_qr = wx.Button(self, label=_("Iniciar con código QR..."), name=_("Iniciar sesión escaneando un código QR"))
        self.btn_qr.Bind(wx.EVT_BUTTON, self._on_qr)
        btn_sizer.Add(self.btn_qr, 0, wx.ALL, 5)

        self.btn_cancel = wx.Button(self, wx.ID_CANCEL, _("Cancelar"))
        self.btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)
        btn_sizer.Add(self.btn_cancel, 0, wx.ALL, 5)

        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)

    def _on_qr(self, event):
        """Abre el diálogo de inicio de sesión por QR; si tiene éxito, cierra este."""
        dlg = TelegramQRDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self.account_id = dlg.account_id
            dlg.Destroy()
            self.EndModal(wx.ID_OK)
        else:
            dlg.Destroy()

    def _detener_temporizador_reenvio(self):
        if self._reenviar_call_later:
            try:
                self._reenviar_call_later.Stop()
            except Exception:
                pass
            self._reenviar_call_later = None

    def _habilitar_reenvio(self):
        self._reenviar_call_later = None
        if self.account_id is not None:
            self.btn_reenviar_codigo.Enable()

    def _texto_estado_codigo(self, res, reenviado=False):
        destino = res.get("destino", "Telegram")
        accion = _("Código reenviado a {0}.") if reenviado else _("Código enviado a {0}.")
        partes = [accion.format(destino)]

        siguiente = res.get("siguiente_destino", "")
        timeout = int(res.get("timeout") or 0)
        if siguiente:
            if timeout > 0:
                partes.append(
                    _("Si no llega, espere {0} segundos y pulse Reenviar código para probar con {1}.").format(
                        timeout, siguiente
                    )
                )
            else:
                partes.append(_("Si no llega, pulse Reenviar código para probar con {0}.").format(siguiente))
        else:
            partes.append(_("Telegram no ha ofrecido SMS ni llamada para este intento; por eso Reenviar código queda desactivado."))
        return " ".join(partes)

    def _aplicar_resultado_codigo(self, res, reenviado=False):
        self.account_id = res["account_id"]
        self.txt_code.Enable()
        self.btn_verificar.Enable()
        self.txt_code.SetFocus()

        mensaje = self._texto_estado_codigo(res, reenviado=reenviado)
        self.lbl_estado_codigo.SetLabel(mensaje)
        self.lbl_estado_codigo.Wrap(max(320, self.GetClientSize().GetWidth() - 20))
        self.Layout()
        speak(_("{0} Introdúzcalo en el paso 2 y pulse Verificar.").format(mensaje), interrupt=True)

        self._detener_temporizador_reenvio()
        self.btn_reenviar_codigo.Disable()
        if res.get("puede_reenviar"):
            timeout = int(res.get("timeout") or 0)
            if timeout > 0:
                self._reenviar_call_later = wx.CallLater(timeout * 1000, self._habilitar_reenvio)
            else:
                self._habilitar_reenvio()

    def _on_enviar_codigo(self, event):
        if not telegram_service.credenciales_configuradas():
            notificar(_("Configure primero api_id y api_hash en Preferencias, pestaña Telegram."))
            return
        phone = self.txt_phone.GetValue().strip()
        if not phone:
            speak(_("Introduzca un número de teléfono."), interrupt=True)
            return

        speak(_("Enviando código a Telegram..."), interrupt=True)
        self.btn_enviar_codigo.Disable()
        self.btn_reenviar_codigo.Disable()
        self.lbl_estado_codigo.SetLabel(_("Estado del código: enviando..."))
        self.Layout()

        def _bg(ctx):
            return telegram_service.iniciar_login(phone)

        def _finish(res, err):
            if err:
                notificar(_("Error al enviar el código: {0}").format(err))
                self.lbl_estado_codigo.SetLabel(_("Estado del código: error al enviar."))
                self.btn_enviar_codigo.Enable()
            else:
                self._aplicar_resultado_codigo(res, reenviado=False)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_reenviar_codigo(self, event):
        if self.account_id is None:
            speak(_("Primero envíe el código (paso 1)."), interrupt=True)
            return

        speak(_("Reenviando código a Telegram..."), interrupt=True)
        self.btn_reenviar_codigo.Disable()
        self.lbl_estado_codigo.SetLabel(_("Estado del código: reenviando..."))
        self.Layout()

        def _bg(ctx):
            return telegram_service.reenviar_codigo(self.account_id)

        def _finish(res, err):
            if err:
                notificar(_("Error al reenviar el código: {0}").format(err))
                self.lbl_estado_codigo.SetLabel(_("Estado del código: error al reenviar."))
                self.btn_reenviar_codigo.Enable()
            else:
                self.txt_code.Clear()
                self._aplicar_resultado_codigo(res, reenviado=True)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_verificar(self, event):
        if self.account_id is None:
            speak(_("Primero envíe el código (paso 1)."), interrupt=True)
            return
        code = self.txt_code.GetValue().strip()
        password = self.txt_password.GetValue()

        if not code:
            speak(_("Introduzca el código recibido."), interrupt=True)
            return

        speak(_("Verificando..."), interrupt=True)

        def _bg(ctx):
            resultado = telegram_service.completar_codigo(self.account_id, code)
            if resultado == "password":
                if not password:
                    return "need_password"
                telegram_service.completar_password(self.account_id, password)
            return "ok"

        def _finish(res, err):
            if err:
                notificar(_("Error de verificación: {0}").format(err))
            elif res == "need_password":
                self._detener_temporizador_reenvio()
                self.btn_reenviar_codigo.Disable()
                self.txt_password.Enable()
                self.txt_password.SetFocus()
                speak(_("Esta cuenta tiene verificación en dos pasos. Introduzca su contraseña en el paso 3 y pulse Verificar de nuevo."), interrupt=True)
            else:
                self._detener_temporizador_reenvio()
                speak(_("Sesión de Telegram iniciada correctamente."), interrupt=True)
                self.EndModal(wx.ID_OK)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_cancel(self, event):
        self._detener_temporizador_reenvio()
        # Si había un login a medias, revertir la cuenta provisional
        if self.account_id is not None:
            def _bg(ctx):
                telegram_service.cancelar_login(self.account_id)
                return True
            task_manager.start_task(_bg, pass_context=True)
        self.EndModal(wx.ID_CANCEL)


class TelegramQRDialog(wx.Dialog):
    """Inicio de sesión por código QR: muestra el QR y sondea hasta autorizar."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Iniciar sesión con código QR"), size=(420, 520),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Diálogo de QR de Telegram"))
        self.account_id = None
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tick, self._timer)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self._init_ui()
        self.CenterOnParent()
        self._generar_qr()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_(
            "Abre Telegram en tu móvil → Ajustes → Dispositivos → Vincular dispositivo, "
            "y escanea este código QR. También puedes copiar el enlace de abajo."
        )), 0, wx.ALL, 8)

        self.bmp = wx.StaticBitmap(self, name=_("Imagen del código QR"))
        sizer.Add(self.bmp, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        sizer.Add(wx.StaticText(self, label=_("Enlace de inicio de sesión:")), 0, wx.ALL, 5)
        self.txt_url = AccessibleTextCtrl(self, style=wx.TE_READONLY, name=_("Enlace de login de Telegram"))
        sizer.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 5)

        self.lbl_estado = wx.StaticText(self, label=_("Generando código QR..."))
        sizer.Add(self.lbl_estado, 0, wx.ALL, 5)

        sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cancelar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)

    def _generar_qr(self):
        speak(_("Generando código QR..."), interrupt=True)

        def _bg(ctx):
            return telegram_service.iniciar_login_qr()

        def _finish(res, err):
            if err:
                notificar(_("Error al generar el QR: {0}").format(err))
                self.lbl_estado.SetLabel(_("Error: {0}").format(err))
                return
            self.account_id = res["account_id"]
            self._mostrar_qr(res["url"])
            self.lbl_estado.SetLabel(_("Esperando a que escanees el código..."))
            speak(_("Código QR listo. Escanéalo con tu app de Telegram."), interrupt=True)
            self._timer.Start(3000)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _mostrar_qr(self, url):
        self.txt_url.SetValue(url)
        try:
            import qrcode
            from io import BytesIO
            img = qrcode.make(url)
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            wx_img = wx.Image(buf)
            if wx_img.IsOk():
                self.bmp.SetBitmap(wx.Bitmap(wx_img))
                self.Layout()
        except Exception as e:
            log.debug(f"No se pudo renderizar el QR (¿falta la librería qrcode?): {e}")
            self.lbl_estado.SetLabel(_("No se pudo dibujar el QR. Usa el enlace de arriba o el método por código."))

    def _on_tick(self, event):
        if self.account_id is None:
            return
        # Evitar sondeos solapados (causaban una tormenta de reintentos de ExportLoginToken)
        if getattr(self, "_comprobando", False):
            return
        self._comprobando = True

        def _bg(ctx):
            return telegram_service.comprobar_qr(self.account_id)

        def _finish(res, err):
            self._comprobando = False
            if err:
                self.lbl_estado.SetLabel(_("Esperando... ({0})").format(err))
                return
            estado = res.get("estado")
            if estado == "ok":
                self._timer.Stop()
                speak(_("Sesión iniciada con éxito mediante QR."), interrupt=True)
                self.EndModal(wx.ID_OK)
            elif estado == "password":
                self._timer.Stop()
                self._pedir_password()
            elif estado == "pending" and res.get("url"):
                self._mostrar_qr(res["url"])

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _pedir_password(self):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Esta cuenta tiene verificación en dos pasos. Introduce tu contraseña:"), _("Contraseña 2FA"))
        if dlg.ShowModal() == wx.ID_OK:
            pwd = dlg.GetValue()
            acc = self.account_id
            def _bg(ctx):
                telegram_service.completar_password(acc, pwd)
                return True
            def _finish(res, err):
                if err:
                    notificar(_("Contraseña incorrecta: {0}").format(err))
                    self._timer.Start(3000)
                else:
                    speak(_("Sesión iniciada con éxito."), interrupt=True)
                    self.EndModal(wx.ID_OK)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        else:
            self._timer.Start(3000)
        dlg.Destroy()

    def _on_close(self, event):
        self._timer.Stop()
        if self.account_id is not None and self.GetReturnCode() != wx.ID_OK:
            acc = self.account_id
            def _bg(ctx):
                telegram_service.cancelar_login(acc)
                return True
            task_manager.start_task(_bg, pass_context=True)
        self.EndModal(wx.ID_CANCEL)
