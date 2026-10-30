# -*- coding: utf-8 -*-
"""Diálogos de Telegram para la interfaz GTK 4: inicio de sesión, credenciales de API y mensajes."""

import logging
import webbrowser

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.telegram.telegram_service import telegram_service

from .dialogos import DialogoBase, ejecutar  # noqa: F401  (ejecutar se reexporta a ventana_telegram)
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

LIMITE_MENSAJE = 4096
LIMITE_PIE = 1024
REACCIONES = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👎", "🔥", "🎉", "🤔"]


class DialogoCredencialesTelegram(DialogoBase):
    """api_id y api_hash de la aplicación: se obtienen gratis en my.telegram.org, en «API development tools»."""

    def __init__(self, padre, al_guardar=None):
        super().__init__(padre, _("Credenciales de la API de Telegram"), _("_Guardar"))
        self.al_guardar = al_guardar
        api_id, api_hash = telegram_service.get_api_credentials()
        self.api_id = self.campo(_("_api_id:"), Gtk.Entry(text=str(api_id or ""), activates_default=True, width_chars=40),
                                 _("Número que muestra my.telegram.org al crear una aplicación."))
        self.api_hash = self.campo(_("api_ha_sh:"), Gtk.PasswordEntry(text=str(api_hash or ""), activates_default=True,
                                                                      show_peek_icon=True),
                                   _("Cadena de 32 caracteres hexadecimales."))
        ayuda = Gtk.Button(label=_("_Abrir my.telegram.org"), use_underline=True)
        ayuda.connect("clicked", lambda *_a: webbrowser.open("https://my.telegram.org/apps"))
        self.rejilla.attach(ayuda, 1, self._fila, 1, 1)
        self.set_focus(self.api_id)

    def aceptar(self):
        api_id, api_hash = self.api_id.get_text().strip(), self.api_hash.get_text().strip()
        if not api_id.isdigit():
            notificar(_("El api_id debe ser un número"))
            return
        if len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
            notificar(_("El api_hash debe tener 32 caracteres hexadecimales"))
            return
        telegram_service.set_api_credentials(api_id, api_hash)
        notificar(_("Credenciales guardadas"))
        self.close()
        if self.al_guardar:
            self.al_guardar()


class DialogoCuentaTelegram(DialogoBase):
    """Alta de cuenta en tres pasos: teléfono, código y, si la cuenta lo tiene, contraseña de dos pasos."""

    def __init__(self, padre, al_terminar):
        super().__init__(padre, _("Añadir cuenta de Telegram"), _("_Continuar"))
        self.al_terminar = al_terminar
        self.account_id = None
        self._paso = "telefono"
        self.telefono = self.campo(_("_Teléfono (con prefijo, por ejemplo +34600112233):"),
                                   Gtk.Entry(activates_default=True, width_chars=30, input_purpose=Gtk.InputPurpose.PHONE))
        self.codigo = self.campo(_("C_ódigo recibido:"), Gtk.Entry(activates_default=True, sensitive=False, width_chars=12,
                                                                   input_purpose=Gtk.InputPurpose.DIGITS),
                                 _("Llega a la aplicación de Telegram de otro dispositivo o por SMS."))
        self.clave = self.campo(_("Contraseña de _verificación en dos pasos:"),
                                Gtk.PasswordEntry(activates_default=True, show_peek_icon=True, sensitive=False))
        self.reenviar = Gtk.Button(label=_("_Reenviar el código"), use_underline=True, sensitive=False)
        self.reenviar.connect("clicked", lambda *_a: self._reenviar())
        self.rejilla.attach(self.reenviar, 1, self._fila, 1, 1)
        self._fila += 1
        self.connect("close-request", self._al_cerrar)
        self._terminado = False
        self.set_focus(self.telefono)

    def _al_cerrar(self, *_a):
        if not self._terminado and self.account_id is not None:
            ident = self.account_id
            self.account_id = None
            ejecutar(lambda: telegram_service.cancelar_login(ident), lambda r, e: None)
        return False

    def aceptar(self):
        if self._paso == "telefono":
            self._enviar_codigo()
        elif self._paso == "codigo":
            self._verificar_codigo()
        else:
            self._verificar_clave()

    def _bloquear(self, si: bool):
        self.boton_aceptar.set_sensitive(not si)

    def _enviar_codigo(self):
        telefono = self.telefono.get_text().strip()
        if not telefono:
            notificar(_("Escribe el número de teléfono"))
            return
        if not telegram_service.credenciales_configuradas():
            notificar(_("Primero hay que indicar las credenciales de la API de Telegram"))
            DialogoCredencialesTelegram(self, al_guardar=self._enviar_codigo).present()
            return
        self._bloquear(True)
        notificar(_("Pidiendo el código a Telegram…"))

        def _fin(res, err):
            self._bloquear(False)
            if err:
                notificar(_("No se pudo enviar el código: {e}").format(e=err))
                return
            self.account_id = res["account_id"]
            self._paso = "codigo"
            self.telefono.set_sensitive(False)
            self.codigo.set_sensitive(True)
            self.codigo.grab_focus()
            self.boton_aceptar.set_label(_("_Iniciar sesión"))
            self._rearmar_reenvio(res)
            notificar(_("Código enviado a {d}. Escríbelo aquí.").format(d=res.get("destino") or _("Telegram")))
        ejecutar(lambda: telegram_service.iniciar_login(telefono), _fin)

    def _rearmar_reenvio(self, info):
        self.reenviar.set_sensitive(bool(info.get("puede_reenviar")))
        if info.get("siguiente_destino"):
            self.reenviar.set_label(_("_Reenviar el código por {d}").format(d=info["siguiente_destino"]))

    def _reenviar(self):
        ident = self.account_id
        self.reenviar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.reenviar.set_sensitive(True)
                notificar(_("No se pudo reenviar: {e}").format(e=err))
                return
            self._rearmar_reenvio(res)
            notificar(_("Código reenviado a {d}").format(d=res.get("destino") or _("Telegram")))
        ejecutar(lambda: telegram_service.reenviar_codigo(ident), _fin)

    def _verificar_codigo(self):
        codigo = self.codigo.get_text().strip().replace(" ", "")
        if not codigo:
            return
        ident = self.account_id
        self._bloquear(True)

        def _fin(res, err):
            self._bloquear(False)
            if err:
                notificar(_("No se pudo iniciar sesión: {e}").format(e=err))
                return
            if res == "password":
                self._paso = "clave"
                self.codigo.set_sensitive(False)
                self.reenviar.set_sensitive(False)
                self.clave.set_sensitive(True)
                self.clave.grab_focus()
                notificar(_("Esta cuenta tiene verificación en dos pasos. Escribe tu contraseña."))
                return
            self._listo()
        ejecutar(lambda: telegram_service.completar_codigo(ident, codigo), _fin)

    def _verificar_clave(self):
        clave = self.clave.get_text()
        if not clave:
            return
        ident = self.account_id
        self._bloquear(True)

        def _fin(res, err):
            self._bloquear(False)
            if err:
                notificar(_("Contraseña incorrecta: {e}").format(e=err))
                return
            self._listo()
        ejecutar(lambda: telegram_service.completar_password(ident, clave), _fin)

    def _listo(self):
        self._terminado = True
        ident = self.account_id
        notificar(_("Cuenta de Telegram añadida"))
        self.close()
        self.al_terminar(ident)


class DialogoMensajeTelegram(DialogoBase):
    """Escribir un mensaje: nuevo, respuesta a otro o edición de uno propio. Ctrl+Intro lo envía."""

    def __init__(self, padre, account_id: int, chat_id: int, titulo_chat: str, al_enviar=None,
                 responde_a: int = None, cita: str = "", editar: int = None, texto_inicial: str = ""):
        titulo = (_("Editar mensaje en {t}") if editar else _("Responder en {t}") if responde_a
                  else _("Mensaje a {t}")).format(t=titulo_chat)
        super().__init__(padre, titulo, _("_Guardar") if editar else _("_Enviar"))
        self.account_id, self.chat_id, self.al_enviar = account_id, chat_id, al_enviar
        self.responde_a, self.editar = responde_a, editar
        self.set_resizable(True)
        self.set_default_size(580, -1)
        if cita:
            quien = Gtk.Label(label=_("Respondiendo a: {c}").format(c=cita), xalign=0, wrap=True, selectable=True)
            self.rejilla.attach(quien, 0, self._fila, 2, 1)
            self._fila += 1
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        instalar_ayudas(self.texto)
        self.texto.get_buffer().set_text(texto_inicial)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=110, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Mensaje:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.contador = Gtk.Label(xalign=0)
        self.rejilla.attach(self.contador, 1, self._fila, 1, 1)
        self.texto.get_buffer().connect("changed", lambda *_a: self._contar())
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl>Return"),
                                          Gtk.CallbackAction.new(lambda *_a: self.aceptar() or True)))
        self.add_controller(ctl)
        self._contar()
        self.set_focus(self.texto)

    def _leer(self) -> str:
        b = self.texto.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def _contar(self):
        self.contador.set_text(_("Caracteres restantes: {n}").format(n=LIMITE_MENSAJE - len(self._leer())))

    def aceptar(self):
        texto = self._leer().strip()
        if not texto:
            notificar(_("Escribe el mensaje"))
            return
        if len(texto) > LIMITE_MENSAJE:
            notificar(_("El mensaje supera los {n} caracteres").format(n=LIMITE_MENSAJE))
            return
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo enviar: {e}").format(e=err))
                return
            notificar(_("Mensaje editado") if self.editar else _("Mensaje enviado"))
            self.close()
            if self.al_enviar:
                self.al_enviar()
        if self.editar:
            ejecutar(lambda: telegram_service.editar_mensaje(self.account_id, self.chat_id, self.editar, texto), _fin)
        else:
            ejecutar(lambda: telegram_service.enviar_mensaje(self.account_id, self.chat_id, texto,
                                                            reply_to=self.responde_a), _fin)
