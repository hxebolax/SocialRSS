# -*- coding: utf-8 -*-
"""Aviso de riesgos y diálogos de login/compositor de X (Twitter).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext para textos en import-time
import wx

from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.services.x import x_service
from .accessible_controls import (AccessibleTextCtrl, AccessibleListCtrl, mostrar_info,
                                  confirmar, habilitar_escape_cierre, CustomTextEntryDialog)


AVISO_RIESGOS = _(
    "AVISO IMPORTANTE sobre el cliente de X (Twitter)\n\n"
    "Este cliente NO es oficial: usa la API interna de X mediante las cookies de tu "
    "sesión. Debes saber que:\n\n"
    "• Incumple las condiciones de uso de X y existe RIESGO de que X limite, bloquee o "
    "suspenda tu cuenta.\n"
    "• X cambia su web a menudo, así que el cliente puede dejar de funcionar hasta que se "
    "publique una corrección.\n"
    "• Se recomienda usar una cuenta secundaria de pruebas.\n"
    "• Tus cookies se guardan CIFRADAS en este equipo; nunca se piden ni guardan tu "
    "contraseña salvo que elijas el método de usuario y contraseña.\n\n"
    "¿Entiendes los riesgos y quieres continuar?")


class XLoginDialog(wx.Dialog):
    """Diálogo de inicio de sesión en X con aviso de riesgos y varios métodos."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Iniciar sesión en X (Twitter)"), size=(560, 460),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Inicio de sesión en X"))
        # Resultado: ("navegador", navegador) | ("cookies", {auth_token,ct0}) |
        #            ("password", (usuario, password, totp)) | None
        self.resultado = None
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)

        aviso = wx.TextCtrl(self, value=AVISO_RIESGOS,
                            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_BESTWRAP,
                            name=_("Aviso de riesgos"))
        aviso.SetMinSize((-1, 170))
        s.Add(aviso, 0, wx.EXPAND | wx.ALL, 8)

        s.Add(wx.StaticText(self, label=_("Método de inicio de sesión:")), 0, wx.LEFT | wx.TOP, 8)
        self.radio = wx.RadioBox(
            self, choices=[
                _("Importar cookies de mi navegador (recomendado, sin contraseña)"),
                _("Pegar cookies manualmente (auth_token y ct0)"),
                _("Usuario y contraseña (menos robusto)"),
            ], style=wx.RA_SPECIFY_ROWS, name=_("Método de login"))
        self.radio.Bind(wx.EVT_RADIOBOX, self._on_metodo)
        s.Add(self.radio, 0, wx.EXPAND | wx.ALL, 8)

        # Panel cambiante según el método.
        self.panel = wx.Panel(self)
        self.panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel.SetSizer(self.panel_sizer)
        s.Add(self.panel, 1, wx.EXPAND | wx.ALL, 8)

        btns = wx.StdDialogButtonSizer()
        self.btn_ok = wx.Button(self, wx.ID_OK, _("Continuar"))
        self.btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        btns.AddButton(self.btn_ok)
        btns.AddButton(wx.Button(self, wx.ID_CANCEL, _("Cancelar")))
        btns.Realize()
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self._construir_panel(0)

    def _on_metodo(self, event):
        self._construir_panel(self.radio.GetSelection())

    def _construir_panel(self, metodo):
        self.panel_sizer.Clear(True)
        if metodo == 0:
            self.panel_sizer.Add(wx.StaticText(
                self.panel, label=_("Navegador donde tienes la sesión de X iniciada:")),
                0, wx.ALL, 4)
            self.cmb_nav = wx.Choice(self.panel, choices=[
                _("Detectar automáticamente"), "Chrome", "Edge", "Firefox", "Brave", "Opera"],
                name=_("Navegador"))
            self.cmb_nav.SetSelection(0)
            self.panel_sizer.Add(self.cmb_nav, 0, wx.ALL, 4)
        elif metodo == 1:
            self.panel_sizer.Add(wx.StaticText(self.panel, label=_("auth_token:")), 0, wx.ALL, 4)
            self.txt_auth = AccessibleTextCtrl(self.panel, name="auth_token")
            self.panel_sizer.Add(self.txt_auth, 0, wx.EXPAND | wx.ALL, 4)
            self.panel_sizer.Add(wx.StaticText(self.panel, label="ct0:"), 0, wx.ALL, 4)
            self.txt_ct0 = AccessibleTextCtrl(self.panel, name="ct0")
            self.panel_sizer.Add(self.txt_ct0, 0, wx.EXPAND | wx.ALL, 4)
            self.panel_sizer.Add(wx.StaticText(
                self.panel, label=_("(En el navegador: DevTools → Aplicación → Cookies de x.com)")),
                0, wx.ALL, 4)
        else:
            self.panel_sizer.Add(wx.StaticText(
                self.panel, label=_("Usuario, correo o teléfono:")), 0, wx.ALL, 4)
            self.txt_user = AccessibleTextCtrl(self.panel, name=_("Usuario"))
            self.panel_sizer.Add(self.txt_user, 0, wx.EXPAND | wx.ALL, 4)
            self.panel_sizer.Add(wx.StaticText(self.panel, label=_("Contraseña:")), 0, wx.ALL, 4)
            self.txt_pwd = wx.TextCtrl(self.panel, style=wx.TE_PASSWORD, name=_("Contraseña"))
            self.panel_sizer.Add(self.txt_pwd, 0, wx.EXPAND | wx.ALL, 4)
            self.panel_sizer.Add(wx.StaticText(
                self.panel, label=_("Clave TOTP de 2FA (opcional):")), 0, wx.ALL, 4)
            self.txt_totp = AccessibleTextCtrl(self.panel, name=_("Clave TOTP"))
            self.panel_sizer.Add(self.txt_totp, 0, wx.EXPAND | wx.ALL, 4)
        self.panel.Layout()

    def _on_ok(self, event):
        metodo = self.radio.GetSelection()
        if metodo == 0:
            mapa = {0: "auto", 1: "chrome", 2: "edge", 3: "firefox", 4: "brave", 5: "opera"}
            self.resultado = ("navegador", mapa.get(self.cmb_nav.GetSelection(), "auto"))
        elif metodo == 1:
            auth = self.txt_auth.GetValue().strip()
            ct0 = self.txt_ct0.GetValue().strip()
            if not auth or not ct0:
                mostrar_info(self, _("Debes pegar auth_token y ct0."), _("Datos incompletos"))
                return
            self.resultado = ("cookies", {"auth_token": auth, "ct0": ct0})
        else:
            usuario = self.txt_user.GetValue().strip()
            pwd = self.txt_pwd.GetValue()
            if not usuario or not pwd:
                mostrar_info(self, _("Debes indicar usuario y contraseña."), _("Datos incompletos"))
                return
            self.resultado = ("password", (usuario, pwd, self.txt_totp.GetValue().strip() or None))
        self.EndModal(wx.ID_OK)


class XComposeDialog(wx.Dialog):
    """Compositor de tweets (publicar, responder, citar)."""

    def __init__(self, parent, account_id, reply_to=None, quote_id=None, titulo=None):
        super().__init__(parent, title=titulo or _("Nuevo post en X"), size=(560, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Compositor de X"))
        self.account_id = account_id
        self.reply_to = reply_to
        self.quote_id = quote_id
        self.media_paths = []
        self.media_alts = []      # texto alternativo en paralelo a media_paths
        self.poll_opciones = []   # opciones de encuesta (2-4) o vacío
        self.poll_minutos = 0     # duración de la encuesta en minutos (0 = sin encuesta)
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Texto (máx. 280; las cuentas Premium admiten más):")),
              0, wx.ALL, 6)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE,
                                      name=_("Texto del post"),
                                      ayuda_texto=_("Escribe tu post y pulsa Publicar."))
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt)
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_media = wx.Button(self, label=_("&Adjuntar imagen/vídeo..."))
        btn_media.Bind(wx.EVT_BUTTON, self._on_media)
        fila.Add(btn_media, 0, wx.ALL, 4)
        btn_poll = wx.Button(self, label=_("Crear &encuesta..."))
        btn_poll.Bind(wx.EVT_BUTTON, self._on_encuesta)
        fila.Add(btn_poll, 0, wx.ALL, 4)
        self.lbl_media = wx.StaticText(self, label=_("Sin adjuntos."))
        fila.Add(self.lbl_media, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)

        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, _("Publicar"))
        ok.Bind(wx.EVT_BUTTON, self._on_publicar)
        btns.AddButton(ok)
        btns.AddButton(wx.Button(self, wx.ID_CANCEL, _("Cancelar")))
        btns.Realize()
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(s)

    def _on_media(self, event):
        if len(self.media_paths) >= 4:
            mostrar_info(self, _("Máximo 4 adjuntos."), _("Límite"))
            return
        dlg = wx.FileDialog(self, _("Elige imagen o vídeo"),
                            wildcard="Imagen/Vídeo (*.jpg;*.jpeg;*.png;*.gif;*.mp4)|*.jpg;*.jpeg;*.png;*.gif;*.mp4",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            # Pedir el texto alternativo (accesibilidad): descripción de la imagen para lectores.
            from .accessible_controls import CustomTextEntryDialog
            d2 = CustomTextEntryDialog(
                self, _("Descripción (texto alternativo) de este adjunto, opcional:"),
                _("Texto alternativo"), multiline=True)
            alt = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
            d2.Destroy()
            self.media_paths.append(ruta)
            self.media_alts.append(alt)
            self.lbl_media.SetLabel(_("{0} adjunto(s).").format(len(self.media_paths)))
        dlg.Destroy()

    def _on_encuesta(self, event):
        """Pide las opciones (una por línea, 2 a 4) y la duración en minutos."""
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(
            self, _("Opciones de la encuesta, una por línea (de 2 a 4):"),
            _("Crear encuesta"), multiline=True)
        opciones = []
        if d.ShowModal() == wx.ID_OK:
            opciones = [l.strip() for l in d.GetValue().splitlines() if l.strip()][:4]
        d.Destroy()
        if len(opciones) < 2:
            if opciones:
                mostrar_info(self, _("La encuesta necesita al menos 2 opciones."), _("Encuesta"))
            return
        d2 = CustomTextEntryDialog(
            self, _("Duración en minutos (por ejemplo 1440 para 1 día):"), _("Crear encuesta"))
        valor = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        try:
            minutos = int(valor)
        except ValueError:
            minutos = 0
        if minutos <= 0:
            mostrar_info(self, _("Duración no válida."), _("Encuesta"))
            return
        self.poll_opciones = opciones
        self.poll_minutos = minutos
        self.lbl_media.SetLabel(_("Encuesta con {0} opciones.").format(len(opciones)))

    def _on_publicar(self, event):
        texto = self.txt.GetValue().strip()
        if not texto and not self.media_paths and not self.poll_opciones:
            mostrar_info(self, _("Escribe algo, adjunta un archivo o crea una encuesta."), _("Vacío"))
            return
        # Recordatorio de texto alternativo (accesibilidad): avisar si hay imágenes sin describir.
        from app.core.config_manager import config
        if config.get("x", "alt_text_reminder", True) and self.media_paths:
            faltan = [i for i, a in enumerate(self.media_alts) if not (a or "").strip()]
            if faltan:
                from .accessible_controls import confirmar
                if not confirmar(self, _("Hay {0} adjunto(s) sin descripción (texto alternativo). "
                                         "¿Publicar de todos modos?").format(len(faltan)),
                                 _("Texto alternativo")):
                    return
        speak(_("Publicando..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("No se pudo publicar: {0}").format(err))
            else:
                notificar(_("Publicado en X."))
        x_service.publicar_async(self.account_id, texto, media_paths=self.media_paths,
                                 reply_to=self.reply_to, quote_id=self.quote_id,
                                 alt_texts=self.media_alts,
                                 poll_opciones=self.poll_opciones, poll_minutos=self.poll_minutos,
                                 callback=_cb)
        self.EndModal(wx.ID_OK)


class XConversationDialog(wx.Dialog):
    """Ventana de conversación de un mensaje directo de X (tipo Telegram): lista de mensajes,
    envío y TIEMPO REAL (mensajes nuevos y 'escribiendo…' por streaming)."""

    def __init__(self, parent, account_id, user_id, screen):
        super().__init__(parent, title=_("Mensajes con @{0}").format(screen),
                         size=(720, 560), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Conversación de X"))
        self.account_id = account_id
        self.user_id = str(user_id)
        self.screen = screen
        self.convo_id = None
        self.msg_ids = []            # message_id por índice de fila (para insertar solo los nuevos)
        self._titulo_base = self.GetTitle()
        self._typing_timer = None

        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("Mensajes (los más recientes al final):")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(
            panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Mensajes de la conversación"))
        self.lista.InsertColumn(0, _("Remitente"), width=150)
        self.lista.InsertColumn(1, _("Mensaje"), width=420)
        self.lista.InsertColumn(2, _("Fecha"), width=130)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu_mensaje)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(panel, label=_("Mensaje:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_msg = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER, name=_("Escribe tu mensaje"))
        self.txt_msg.Bind(wx.EVT_TEXT_ENTER, self._on_enviar)
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt_msg)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt_msg)
        fila.Add(self.txt_msg, 1, wx.EXPAND | wx.ALL, 5)
        btn_env = wx.Button(panel, label=_("&Enviar"))
        btn_env.Bind(wx.EVT_BUTTON, self._on_enviar)
        fila.Add(btn_env, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)

        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        fila2.AddStretchSpacer()
        btn_cerrar = wx.Button(panel, wx.ID_CLOSE, _("&Cerrar"))
        btn_cerrar.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        fila2.Add(btn_cerrar, 0, wx.ALL, 5)
        s.Add(fila2, 0, wx.EXPAND)

        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_CLOSE, self._on_cerrar)
        habilitar_escape_cierre(self, wx.ID_CLOSE)

        # TIEMPO REAL: el streaming del servicio avisa por el bus de eventos (otro hilo) → UI.
        event_bus.subscribe("x_dm_update", self._on_dm_update)
        event_bus.subscribe("x_dm_typing", self._on_dm_typing)
        self.CenterOnParent()
        self.lista.SetFocus()
        self._cargar(inicial=True)

    # ---------------- carga e inserción ----------------
    def _cargar(self, inicial=False):
        def _cb(res, err):
            if err:
                if inicial:
                    notificar(_("Error al cargar mensajes: {0}").format(err))
                return
            self.convo_id = (res or {}).get("convo_id") or self.convo_id
            self._insertar_nuevos((res or {}).get("mensajes") or [])
            if inicial and self.convo_id:
                # Arrancar la escucha en vivo de ESTA conversación.
                x_service.iniciar_streaming_dm(self.account_id, self.convo_id)
        x_service.cargar_dms_async(self.account_id, self.user_id, self.screen, callback=_cb)

    def _insertar_nuevos(self, mensajes):
        """Añade al final SOLO los mensajes que aún no están (sin reconstruir ni mover el foco,
        salvo que ya estuvieras en el último, para leer el recién llegado)."""
        from .main_frame import formatear_tiempo_relativo
        nuevos = [m for m in mensajes if m.get("message_id") not in self.msg_ids]
        if not nuevos:
            return
        n = self.lista.GetItemCount()
        foco_al_final = (n == 0) or (self.lista.GetFocusedItem() == n - 1)
        self.lista.Freeze()
        try:
            for m in nuevos:
                quien = _("Tú") if m.get("outgoing") else (f"@{self.screen}" if self.screen else _("Él/Ella"))
                idx = self.lista.GetItemCount()
                pos = self.lista.InsertItem(idx, quien)
                self.lista.SetItem(pos, 1, (m.get("text") or "").replace("\n", " "))
                self.lista.SetItem(pos, 2, formatear_tiempo_relativo(m.get("date")) if m.get("date") else "")
                self.msg_ids.append(m.get("message_id"))
        finally:
            self.lista.Thaw()
        if foco_al_final:
            ult = self.lista.GetItemCount() - 1
            self.lista.Focus(ult)
            self.lista.EnsureVisible(ult)

    # ---------------- tiempo real ----------------
    def _on_dm_update(self, account_id, conversation_id):
        if account_id == self.account_id and conversation_id == self.convo_id:
            wx.CallAfter(self._cargar)

    def _on_dm_typing(self, account_id, conversation_id):
        if account_id == self.account_id and conversation_id == self.convo_id:
            wx.CallAfter(self._mostrar_escribiendo)

    def _mostrar_escribiendo(self):
        self.SetTitle(_("{0} — escribiendo…").format(self._titulo_base))
        if self._typing_timer:
            self._typing_timer.Stop()
        self._typing_timer = wx.CallLater(4000, lambda: self.SetTitle(self._titulo_base))

    # ---------------- acciones ----------------
    def _on_enviar(self, event):
        texto = self.txt_msg.GetValue().strip()
        if not texto:
            return
        # Limpiar YA el campo para que no se reenvíe por error (anti-doble).
        self.txt_msg.SetValue("")

        def _cb(res, err):
            if err:
                notificar(_("Error al enviar: {0}").format(err))
            else:
                self._cargar()
        x_service.enviar_dm_async(self.account_id, self.user_id, texto, callback=_cb)
        self.txt_msg.SetFocus()

    def _on_menu_mensaje(self, event):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self.msg_ids):
            return
        mid = self.msg_ids[idx]
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Reaccionar…"))
        self.Bind(wx.EVT_MENU, lambda e, m=mid: self._reaccionar(m), it)
        it = menu.Append(wx.ID_ANY, _("Borrar mensaje"))
        self.Bind(wx.EVT_MENU, lambda e, m=mid: self._borrar(m), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _reaccionar(self, mid):
        dlg = CustomTextEntryDialog(self, _("Emoji de reacción (por ejemplo 👍):"),
                                    _("Reaccionar"), value="👍")
        if dlg.ShowModal() == wx.ID_OK:
            emoji = dlg.GetValue().strip()
            if emoji and self.convo_id:
                def _cb(res, err):
                    notificar(_("Error: {0}").format(err) if err else _("Reacción enviada."))
                x_service.reaccionar_dm_async(self.account_id, mid, self.convo_id, emoji, callback=_cb)
        dlg.Destroy()

    def _borrar(self, mid):
        if confirmar(self, _("¿Borrar este mensaje?"), _("Borrar")) != wx.ID_YES:
            return

        def _cb(res, err):
            if err:
                notificar(_("Error al borrar: {0}").format(err))
            else:
                notificar(_("Mensaje borrado."))
                self.msg_ids = []
                self.lista.DeleteAllItems()
                self._cargar()
        x_service.borrar_dm_async(self.account_id, mid, callback=_cb)

    def _on_cerrar(self, event):
        try:
            event_bus.unsubscribe("x_dm_update", self._on_dm_update)
            event_bus.unsubscribe("x_dm_typing", self._on_dm_typing)
            if self.convo_id:
                x_service.detener_streaming_dm(self.convo_id)
            if self._typing_timer:
                self._typing_timer.Stop()
        except Exception:
            pass
        self.EndModal(wx.ID_CLOSE)
