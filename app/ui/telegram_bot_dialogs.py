# -*- coding: utf-8 -*-
"""Diálogos accesibles para interactuar con BOTS de Telegram:
- BotonesMensajeDialog: muestra los botones (teclado) de un mensaje y permite pulsarlos.
- InlineQueryDialog: consulta a un bot en modo inline y envía uno de sus resultados.
- ComandosBotDialog: lista los comandos publicados por un bot y ejecuta el elegido.
"""
import wx

from app.core.privacy import abrir_url
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl


# Etiquetas legibles del tipo de cada botón (para que el lector diga qué hace).
_TIPOS_BOTON = {
    "callback": lambda: _("acción"),
    "url": lambda: _("enlace"),
    "login": lambda: _("iniciar sesión"),
    "webapp": lambda: _("aplicación web"),
    "copiar": lambda: _("copiar texto"),
    "inline_aqui": lambda: _("consulta inline aquí"),
    "inline_otro": lambda: _("consulta inline en otro chat"),
    "usuario": lambda: _("perfil de usuario"),
    "juego": lambda: _("juego"),
    "pago": lambda: _("pago"),
    "texto": lambda: _("enviar texto"),
    "pide_contacto": lambda: _("compartir tu contacto"),
    "pide_ubicacion": lambda: _("compartir ubicación"),
    "pide_encuesta": lambda: _("crear encuesta"),
    "pide_usuarios": lambda: _("elegir usuarios"),
    "pide_chat": lambda: _("elegir chat"),
    "otro": lambda: _("botón"),
}


def _nombre_tipo_boton(kind):
    f = _TIPOS_BOTON.get(kind)
    return f() if f else kind


class BotonesMensajeDialog(wx.Dialog):
    """Lista los botones de un mensaje (teclado de bot) y permite pulsarlos de forma accesible."""

    def __init__(self, conv, account_id, chat_id, message_id):
        super().__init__(conv, title=_("Botones del mensaje"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Botones del mensaje de Telegram"))
        self.conv = conv
        self.account_id = account_id
        self.chat_id = chat_id
        self.message_id = message_id
        self._botones = []
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Botones (Intro para pulsar):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de botones"))
        self.lista.InsertColumn(0, _("Botón"), width=330)
        self.lista.InsertColumn(1, _("Tipo"), width=180)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_activar)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_pulsar = wx.Button(self, label=_("&Pulsar"), name=_("Pulsar el botón seleccionado"))
        self.btn_pulsar.Bind(wx.EVT_BUTTON, self._on_activar)
        fila.Add(self.btn_pulsar, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)

    def _cargar(self):
        def _bg(ctx):
            return telegram_service.obtener_botones(self.account_id, self.chat_id, self.message_id)

        def _finish(res, err):
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            self._botones = (res or {}).get("botones", []) if res else []
            if not self._botones:
                notificar(_("Este mensaje ya no tiene botones."))
                self._cerrar()
                return
            for i, b in enumerate(self._botones):
                tipo = _nombre_tipo_boton(b.get("kind", "otro"))
                pos = self.lista.InsertItem(i, b.get("text", "") or _("(sin texto)"))
                self.lista.SetItem(pos, 1, tipo)
                self.lista.establecer_lectura(pos, "{0}. {1}".format(b.get("text", ""), tipo))
            self.lista.Focus(0)
            self.lista.Select(0)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_activar(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._botones)):
            return
        b = self._botones[idx]
        kind = b.get("kind", "otro")

        # Algunos botones se resuelven SIN llamar al servidor (enlaces, copiar): respuesta inmediata.
        # Tras actuar se CIERRA el diálogo y se vuelve a la conversación para seguir con el bot.
        if kind in ("url", "login", "webapp") and b.get("valor"):
            self._abrir_enlace(b["valor"], login=(kind == "login"))
            self._cerrar()
            return
        if kind == "copiar" and b.get("valor"):
            self._copiar(b["valor"])
            self._cerrar()
            return
        if kind in ("inline_aqui", "inline_otro"):
            self._abrir_inline(b.get("valor", ""))  # ya cierra este diálogo
            return

        speak(_("Pulsando..."), interrupt=True)

        def _bg(ctx):
            return telegram_service.pulsar_boton(self.account_id, self.chat_id, self.message_id, idx)

        def _finish(res, err):
            if err:
                # En caso de error NO se cierra, por si quieres reintentar con otro botón.
                notificar(_("No se pudo pulsar el botón: {0}").format(err))
                return
            res = res or {}
            # Compartir contacto exige CONFIRMACIÓN explícita (TG-011): se pide antes de cerrar y solo
            # tras el "sí" se envía de verdad el número.
            if res.get("tipo") == "requiere_consentimiento_contacto":
                self._consentir_contacto(res.get("telefono") or "")
                self._cerrar()
                return
            self._procesar_resultado(res)
            self._cerrar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _consentir_contacto(self, telefono):
        """Pide confirmación explícita antes de revelar tu número de teléfono a un bot/chat."""
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Compartir tu número de teléfono ({0}) con este chat? Quien lo reciba "
                             "(incluido un bot) podrá verlo y guardarlo.").format(telefono),
                     _("Compartir tu contacto")) != wx.ID_YES:
            notificar(_("No se ha compartido tu contacto."))
            return
        acc, chat = self.account_id, self.chat_id

        def _bg(ctx):
            return telegram_service.compartir_mi_contacto(acc, chat)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Has compartido tu contacto.") if not e
            else _("No se pudo compartir el contacto: {0}").format(e)), pass_context=True)

    def _cerrar(self):
        """Cierra el diálogo y devuelve el foco a la lista de mensajes de la conversación."""
        try:
            self.EndModal(wx.ID_OK)
        except Exception:
            pass

    def _procesar_resultado(self, res):
        tipo = res.get("tipo")
        if tipo == "callback":
            texto = (res.get("texto") or "").strip()
            url = res.get("url") or ""
            if url:
                self._abrir_enlace(url)
            if texto:
                if res.get("alerta"):
                    # Alerta: mostrarla en un diálogo (el bot quiere que la leas).
                    self._mostrar(_("Respuesta del bot"), texto)
                else:
                    speak(texto, interrupt=True)
                    notificar(texto)
            elif not url:
                speak(_("Hecho."), interrupt=True)
        elif tipo == "url":
            self._abrir_enlace(res.get("url", ""), login=res.get("login", False))
        elif tipo == "copiar":
            self._copiar(res.get("texto", ""))
        elif tipo in ("inline_aqui", "inline_otro"):
            self._abrir_inline(res.get("consulta", ""))
        elif tipo == "enviado":
            notificar(_("Enviado: {0}").format(res.get("texto", "")))
        elif tipo == "enviado_contacto":
            notificar(_("Has compartido tu contacto."))
        elif tipo == "pide_contacto":
            notificar(_("Este botón comparte tu contacto, pero tu cuenta no tiene teléfono visible."))
        elif tipo == "pide_ubicacion":
            notificar(_("Este botón pide tu ubicación; compártela desde 'Más acciones', 'Enviar ubicación'."))
        elif tipo == "pide_encuesta":
            notificar(_("Este botón crea una encuesta; usa 'Más acciones', 'Crear encuesta'."))
        elif tipo == "usuario":
            notificar(_("Este botón abre el perfil de un usuario (id {0}).").format(res.get("user_id", "")))
        elif tipo == "necesita_password":
            notificar(_("Este botón requiere tu contraseña de verificación en dos pasos."))
        else:
            notificar(_("Este tipo de botón no se puede activar desde aquí."))

    def _abrir_enlace(self, url, login=False):
        if not url:
            return
        if login:
            from .accessible_controls import confirmar
            if confirmar(self, _("Este enlace inicia sesión en un servicio externo con tu cuenta de "
                                 "Telegram. ¿Abrirlo en el navegador?"), _("Abrir enlace de inicio de sesión")) != wx.ID_YES:
                return
        abrir_url(url)
        speak(_("Abriendo enlace."), interrupt=True)

    def _copiar(self, texto):
        if texto and wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(texto))
            wx.TheClipboard.Close()
            notificar(_("Texto copiado: {0}").format(texto))

    def _abrir_inline(self, consulta):
        # El bot de este chat es el destinatario de la consulta inline.
        self.EndModal(wx.ID_OK)
        dlg = InlineQueryDialog(self.conv, self.account_id, self.chat_id,
                                bot_predef=str(self.chat_id), consulta_predef=consulta)
        dlg.ShowModal()
        dlg.Destroy()

    def _mostrar(self, titulo, texto):
        dlg = wx.Dialog(self, title=titulo, size=(520, 320),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sz = wx.BoxSizer(wx.VERTICAL)
        txt = AccessibleTextCtrl(dlg, style=wx.TE_MULTILINE | wx.TE_READONLY, name=titulo)
        txt.SetValue(texto)
        sz.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        sz.Add(wx.Button(dlg, wx.ID_OK, _("Aceptar")), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizer(sz)
        dlg.Bind(wx.EVT_CLOSE, lambda e: dlg.EndModal(wx.ID_OK))
        txt.SetFocus()
        dlg.ShowModal()
        dlg.Destroy()


class InlineQueryDialog(wx.Dialog):
    """Consulta a un bot en modo inline (@bot consulta) y envía uno de sus resultados al chat."""

    def __init__(self, conv, account_id, chat_id, bot_predef="", consulta_predef=""):
        super().__init__(conv, title=_("Consulta inline a un bot"), size=(600, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Consulta inline a un bot de Telegram"))
        self.conv = conv
        self.account_id = account_id
        self.chat_id = chat_id
        self._query_id = None
        self._resultados = []
        self._init_ui(bot_predef, consulta_predef)
        self.CenterOnParent()
        if bot_predef and consulta_predef is not None:
            self._buscar(None)

    def _init_ui(self, bot_predef, consulta_predef):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Bot (sin @, p. ej. gif):")), 0, wx.LEFT | wx.TOP, 6)
        self.txt_bot = AccessibleTextCtrl(self, value=str(bot_predef or ""), name=_("Nombre del bot"))
        s.Add(self.txt_bot, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Consulta:")), 0, wx.LEFT, 6)
        self.txt_q = AccessibleTextCtrl(self, value=str(consulta_predef or ""), name=_("Texto de la consulta"))
        s.Add(self.txt_q, 0, wx.EXPAND | wx.ALL, 6)

        self.btn_buscar = wx.Button(self, label=_("&Buscar"), name=_("Buscar resultados inline"))
        self.btn_buscar.Bind(wx.EVT_BUTTON, self._buscar)
        s.Add(self.btn_buscar, 0, wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Resultados (Intro para enviar):")), 0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de resultados inline"))
        self.lista.InsertColumn(0, _("Título"), width=320)
        self.lista.InsertColumn(1, _("Descripción"), width=240)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._enviar)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_enviar = wx.Button(self, label=_("&Enviar"), name=_("Enviar el resultado seleccionado"))
        self.btn_enviar.Bind(wx.EVT_BUTTON, self._enviar)
        fila.Add(self.btn_enviar, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)

    def _buscar(self, event):
        bot = self.txt_bot.GetValue().strip().lstrip("@")
        consulta = self.txt_q.GetValue()
        if not bot:
            speak(_("Escribe el nombre del bot."), interrupt=True)
            return
        speak(_("Buscando..."), interrupt=True)

        def _bg(ctx):
            return telegram_service.consultar_inline(self.account_id, bot, consulta)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo consultar: {0}").format(err))
                return
            self._query_id = (res or {}).get("query_id")
            self._resultados = (res or {}).get("resultados", [])
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            if not self._resultados:
                speak(_("Sin resultados."), interrupt=True)
                return
            for i, r in enumerate(self._resultados):
                titulo = r.get("titulo") or r.get("tipo") or _("(resultado)")
                desc = r.get("descripcion") or ""
                pos = self.lista.InsertItem(i, titulo)
                self.lista.SetItem(pos, 1, desc)
                self.lista.establecer_lectura(pos, "{0}. {1}".format(titulo, desc))
            self.lista.Focus(0)
            self.lista.Select(0)
            speak(_("{0} resultados.").format(len(self._resultados)), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _enviar(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._resultados)) or self._query_id is None:
            return
        result_id = self._resultados[idx].get("id")
        speak(_("Enviando..."), interrupt=True)

        def _bg(ctx):
            return telegram_service.enviar_resultado_inline(
                self.account_id, self.chat_id, self._query_id, result_id)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo enviar: {0}").format(err))
            else:
                notificar(_("Resultado enviado."))
                try:
                    self.conv._cargar_mensajes_cache()
                except Exception:
                    pass
                self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class ComandosBotDialog(wx.Dialog):
    """Lista los comandos publicados por un bot y ejecuta el elegido."""

    def __init__(self, conv, account_id, chat_id):
        super().__init__(conv, title=_("Comandos del bot"), size=(560, 400),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Comandos del bot de Telegram"))
        self.conv = conv
        self.account_id = account_id
        self.chat_id = chat_id
        self._comandos = []
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Comandos (Intro para ejecutar):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de comandos del bot"))
        self.lista.InsertColumn(0, _("Comando"), width=180)
        self.lista.InsertColumn(1, _("Descripción"), width=330)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._ejecutar)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_ejec = wx.Button(self, label=_("&Ejecutar"), name=_("Ejecutar el comando seleccionado"))
        self.btn_ejec.Bind(wx.EVT_BUTTON, self._ejecutar)
        fila.Add(self.btn_ejec, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)

    def _cargar(self):
        def _bg(ctx):
            return telegram_service.obtener_comandos_bot(self.account_id, self.chat_id)

        def _finish(res, err):
            if err:
                notificar(_("No se pudieron obtener los comandos: {0}").format(err))
                return
            self._comandos = res or []
            if not self._comandos:
                notificar(_("Este bot no publica una lista de comandos. Prueba '/start' o '/help'."))
                return
            for i, c in enumerate(self._comandos):
                cmd = "/" + (c.get("comando", "") or "")
                desc = c.get("descripcion", "") or ""
                pos = self.lista.InsertItem(i, cmd)
                self.lista.SetItem(pos, 1, desc)
                self.lista.establecer_lectura(pos, "{0}. {1}".format(cmd, desc))
            self.lista.Focus(0)
            self.lista.Select(0)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _ejecutar(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._comandos)):
            return
        comando = self._comandos[idx].get("comando", "")

        def _bg(ctx):
            return telegram_service.enviar_comando(self.account_id, self.chat_id, comando)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo ejecutar: {0}").format(err))
            else:
                notificar(_("Comando enviado: /{0}").format(comando))
                try:
                    self.conv._cargar_mensajes_cache()
                except Exception:
                    pass
                self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)
