# -*- coding: utf-8 -*-
"""Diálogos del cliente IRC: asistente de alta/edición de una red.

Una RED IRC (Libera.Chat, OFTC, un servidor propio...) es una "cuenta" de la app. El
asistente pide lo mínimo con valores seguros por defecto (TLS 6697 con verificación) y
guarda los secretos (contraseñas de servidor/SASL/NickServ) cifrados en security.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import (AccessibleTextCtrl, AccessibleListCtrl, mostrar_info,
                                  confirmar, CustomTextEntryDialog)

# Centinela para "contraseña guardada, sin cambios" en modo edición: si el usuario deja el
# campo tal cual, el secreto NO se toca; si lo vacía, se borra; si escribe, se sustituye.
_SECRETO_SIN_CAMBIOS = "********"


class IRCNetworkDialog(wx.Dialog):
    """Asistente accesible de alta/edición de una red IRC."""

    def __init__(self, parent, account_id=None):
        titulo = _("Editar red IRC") if account_id else _("Añadir red IRC")
        super().__init__(parent, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=titulo)
        self.account_id = account_id
        self._init_ui()
        if account_id:
            self._cargar(account_id)
        self.CenterOnParent()
        self.txt_nombre.SetFocus()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        panel = wx.ScrolledWindow(self, style=wx.VSCROLL)
        panel.SetScrollRate(0, 12)
        ps = wx.BoxSizer(wx.VERTICAL)

        def campo(etiqueta, valor="", ayuda="", password=False):
            ps.Add(wx.StaticText(panel, label=etiqueta), 0, wx.LEFT | wx.TOP, 6)
            estilo = wx.TE_PASSWORD if password else 0
            ctrl = AccessibleTextCtrl(panel, value=valor, style=estilo,
                                      name=etiqueta.replace("&", ""), ayuda_texto=ayuda)
            ps.Add(ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
            return ctrl

        # --- Identificación de la red ---
        self.txt_nombre = campo(_("&Nombre de la red (ej: Libera.Chat):"),
                                ayuda=_("Nombre con el que verás esta red en el árbol."))
        self.txt_host = campo(_("&Servidor (ej: irc.libera.chat):"),
                              ayuda=_("Dirección del servidor IRC."))
        self.txt_puerto = campo(_("&Puerto:"), valor="6697",
                                ayuda=_("6697 es el puerto seguro (TLS) habitual; 6667 el no cifrado."))
        self.chk_tls = wx.CheckBox(panel, label=_("Usar conexión segura (&TLS)"))
        self.chk_tls.SetValue(True)
        ps.Add(self.chk_tls, 0, wx.ALL, 6)
        self.chk_verify = wx.CheckBox(panel, label=_("&Verificar el certificado del servidor (recomendado)"))
        self.chk_verify.SetValue(True)
        ps.Add(self.chk_verify, 0, wx.LEFT | wx.BOTTOM, 6)

        # --- Identidad ---
        self.txt_nick = campo(_("&Apodo (nick):"),
                              ayuda=_("Tu apodo en esta red."))
        self.txt_alt = campo(_("Apodos a&lternativos (separados por comas):"),
                             ayuda=_("Se usan en orden si tu apodo está ocupado."))
        self.txt_user = campo(_("&Usuario (ident, opcional):"),
                              ayuda=_("Si se deja vacío se usa el apodo."))
        self.txt_real = campo(_("Nombre &real (opcional):"),
                              ayuda=_("Texto libre visible en el whois."))

        # --- Autenticación ---
        self.txt_server_pass = campo(_("Contraseña del servidor (opcional):"), password=True,
                                     ayuda=_("Solo para servidores o bouncers que piden PASS."))
        self.chk_sasl = wx.CheckBox(panel, label=_("Iniciar sesión con SASL (recomendado si la red lo soporta)"))
        ps.Add(self.chk_sasl, 0, wx.ALL, 6)
        self.txt_sasl_user = campo(_("Usuario SASL (si se deja vacío se usa el apodo):"))
        self.txt_sasl_pass = campo(_("Contraseña SASL:"), password=True)
        self.txt_ns_pass = campo(_("Contraseña de NickServ (opcional, para redes sin SASL):"), password=True,
                                 ayuda=_("Se envía IDENTIFY a NickServ al conectar."))

        # --- Canales y arranque ---
        self.txt_autojoin = campo(_("&Canales al conectar (separados por comas, ej: #nvda, #accesibilidad):"))
        self.chk_auto = wx.CheckBox(panel, label=_("C&onectar automáticamente al abrir la aplicación"))
        self.chk_auto.SetValue(True)
        ps.Add(self.chk_auto, 0, wx.ALL, 6)

        panel.SetSizer(ps)
        s.Add(panel, 1, wx.EXPAND)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        btn_ok = wx.Button(self, wx.ID_OK, _("Acep&tar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_aceptar)
        fila.Add(btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)

        self.SetSizer(s)
        self.SetSize((560, 560))

    def _cargar(self, account_id):
        from app.services.irc import irc_service
        cfg = irc_service.obtener_red(account_id) or {}
        self.txt_nombre.SetValue(cfg.get("name") or "")
        self.txt_host.SetValue(cfg.get("host") or "")
        self.txt_puerto.SetValue(str(cfg.get("port") or 6697))
        self.chk_tls.SetValue(bool(cfg.get("tls", True)))
        self.chk_verify.SetValue(bool(cfg.get("verify_cert", True)))
        self.txt_nick.SetValue(cfg.get("nick") or "")
        self.txt_alt.SetValue(cfg.get("alt_nicks") or "")
        self.txt_user.SetValue(cfg.get("username") or "")
        self.txt_real.SetValue(cfg.get("realname") or "")
        self.chk_sasl.SetValue(bool(cfg.get("use_sasl")))
        self.txt_sasl_user.SetValue(cfg.get("sasl_username") or "")
        self.txt_autojoin.SetValue(cfg.get("autojoin") or "")
        self.chk_auto.SetValue(bool(cfg.get("auto_connect", True)))
        # Secretos: nunca se muestran; el centinela indica "hay una guardada".
        if cfg.get("server_password_definida"):
            self.txt_server_pass.SetValue(_SECRETO_SIN_CAMBIOS)
        if cfg.get("sasl_password_definida"):
            self.txt_sasl_pass.SetValue(_SECRETO_SIN_CAMBIOS)
        if cfg.get("nickserv_password_definida"):
            self.txt_ns_pass.SetValue(_SECRETO_SIN_CAMBIOS)

    def _datos(self):
        """Recoge y valida los campos. Devuelve None si falta algo obligatorio."""
        nombre = self.txt_nombre.GetValue().strip()
        host = self.txt_host.GetValue().strip()
        nick = self.txt_nick.GetValue().strip()
        if not host:
            speak(_("Escribe la dirección del servidor."), interrupt=True)
            self.txt_host.SetFocus()
            return None
        if not nick:
            speak(_("Escribe tu apodo."), interrupt=True)
            self.txt_nick.SetFocus()
            return None
        try:
            puerto = int(self.txt_puerto.GetValue().strip() or "6697")
        except ValueError:
            speak(_("El puerto debe ser un número."), interrupt=True)
            self.txt_puerto.SetFocus()
            return None
        datos = {
            "name": nombre or host,
            "host": host,
            "port": puerto,
            "tls": self.chk_tls.GetValue(),
            "verify_cert": self.chk_verify.GetValue(),
            "nick": nick,
            "alt_nicks": self.txt_alt.GetValue().strip(),
            "username": self.txt_user.GetValue().strip(),
            "realname": self.txt_real.GetValue().strip(),
            "use_sasl": self.chk_sasl.GetValue(),
            "sasl_username": self.txt_sasl_user.GetValue().strip(),
            "autojoin": self.txt_autojoin.GetValue().strip(),
            "auto_connect": self.chk_auto.GetValue(),
        }
        # Secretos: solo se incluyen si el usuario los cambió (el centinela = sin cambios).
        for ctrl, clave in ((self.txt_server_pass, "server_password"),
                            (self.txt_sasl_pass, "sasl_password"),
                            (self.txt_ns_pass, "nickserv_password")):
            valor = ctrl.GetValue()
            if valor != _SECRETO_SIN_CAMBIOS:
                datos[clave] = valor.strip()
        return datos

    def _on_aceptar(self, event):
        datos = self._datos()
        if datos is None:
            return
        from app.services.irc import irc_service
        try:
            if self.account_id:
                irc_service.actualizar_red(self.account_id, datos)
                notificar(_("Red IRC actualizada."))
            else:
                self.account_id = irc_service.crear_red(datos)
                notificar(_("Red IRC '{0}' añadida.").format(datos["name"]))
        except Exception as e:
            notificar(_("No se pudo guardar la red: {0}").format(e))
            return
        self.EndModal(wx.ID_OK)


def mostrar_whois(parent_frame, account_id, nick):
    """WHOIS legible en segundo plano y visor de texto (reutilizable desde varios sitios)."""
    speak(_("Consultando a {0}...").format(nick), interrupt=True)

    def _bg(ctx):
        from app.services.irc import irc_service
        return irc_service.whois_legible(account_id, nick)

    def _fin(res, err):
        if err:
            notificar(_("No se pudo consultar: {0}").format(err))
        elif res:
            # El visor de texto largo vive en el frame principal.
            frame = parent_frame
            while frame is not None and not hasattr(frame, "_mostrar_texto_largo"):
                frame = frame.GetParent() if hasattr(frame, "GetParent") else None
            if frame is not None:
                frame._mostrar_texto_largo(_("Datos de {0}").format(nick), res)
            else:
                mostrar_info(parent_frame, res, _("Datos de {0}").format(nick))
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


class IRCUsersDialog(wx.Dialog):
    """Usuarios de un canal (nicklist accesible): rangos en palabras y menú de acciones por
    usuario (whois, privado, ignorar y, si eres operador, op/voz/expulsar/banear)."""

    def __init__(self, parent, account_id, canal):
        super().__init__(parent, title=_("Usuarios de {0}").format(canal),
                         size=(520, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Usuarios del canal"))
        self.account_id = account_id
        self.canal = canal
        self.usuarios = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Usuarios (Aplicaciones o Mayús+F10 para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de usuarios del canal"))
        self.lista.InsertColumn(0, _("Apodo"), width=220)
        self.lista.InsertColumn(1, _("Rango"), width=160)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._abrir_privado())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_act = wx.Button(self, label=_("Actuali&zar"))
        btn_act.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        fila.Add(btn_act, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        from app.services.irc import irc_service
        self.usuarios = irc_service.usuarios_de_canal(self.account_id, self.canal)
        self._soy_op = irc_service.soy_operador(self.account_id, self.canal)
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, (nick, rango) in enumerate(self.usuarios):
            self.lista.InsertItem(i, nick)
            self.lista.SetItem(i, 1, rango or "")
            self.lista.establecer_lectura(i, f"{nick}, {rango}" if rango else nick)
        self.lbl.SetLabel(_("{0} usuarios (Aplicaciones o Mayús+F10 para acciones):").format(len(self.usuarios)))
        if self.usuarios:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _nick_actual(self):
        i = self.lista.GetFocusedItem()
        return self.usuarios[i][0] if 0 <= i < len(self.usuarios) else None

    def _abrir_privado(self):
        nick = self._nick_actual()
        if not nick:
            return
        from app.services.irc import irc_service
        irc_service.abrir_privado(self.account_id, nick)
        from .irc_conversation import IRCConversationDialog
        dlg = IRCConversationDialog(self.GetParent(), self.account_id, nick, es_privado=True)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_menu(self, event):
        nick = self._nick_actual()
        if not nick:
            return
        from app.services.irc import irc_service
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Ver datos (whois)"))
        self.Bind(wx.EVT_MENU, lambda e, n=nick: mostrar_whois(self, self.account_id, n), it)
        it = menu.Append(wx.ID_ANY, _("Privado con {0}").format(nick))
        self.Bind(wx.EVT_MENU, lambda e: self._abrir_privado(), it)
        if irc_service.esta_ignorado(self.account_id, nick):
            it = menu.Append(wx.ID_ANY, _("Dejar de ignorar"))
            self.Bind(wx.EVT_MENU, lambda e, n=nick: self._ignorar(n, False), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Ignorar (no ver sus mensajes)"))
            self.Bind(wx.EVT_MENU, lambda e, n=nick: self._ignorar(n, True), it)
        if self._soy_op:
            menu.AppendSeparator()
            for accion, etiqueta in (("op", _("Dar operador")), ("deop", _("Quitar operador")),
                                     ("voice", _("Dar voz")), ("devoice", _("Quitar voz"))):
                it = menu.Append(wx.ID_ANY, etiqueta)
                self.Bind(wx.EVT_MENU, lambda e, a=accion, n=nick: self._accion_op(a, n), it)
            it = menu.Append(wx.ID_ANY, _("Expulsar del canal..."))
            self.Bind(wx.EVT_MENU, lambda e, n=nick: self._accion_op("kick", n, confirmar_motivo=True), it)
            it = menu.Append(wx.ID_ANY, _("Banear y expulsar..."))
            self.Bind(wx.EVT_MENU, lambda e, n=nick: self._accion_op("bankick", n, confirmar_motivo=True), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _ignorar(self, nick, valor):
        from app.services.irc import irc_service
        irc_service.ignorar(self.account_id, nick, valor)
        notificar(_("{0} ignorado. No verás sus mensajes.").format(nick) if valor
                  else _("{0} ya no está ignorado.").format(nick))

    def _accion_op(self, accion, nick, confirmar_motivo=False):
        """Acción de operador; las destructivas piden confirmación (y motivo opcional)."""
        motivo = ""
        if confirmar_motivo:
            etiqueta = _("expulsar") if accion == "kick" else _("banear y expulsar")
            if confirmar(self, _("¿Seguro que quieres {0} a {1} de {2}?").format(etiqueta, nick, self.canal),
                         _("Confirmar")) != wx.ID_YES:
                return
            dlg = CustomTextEntryDialog(self, _("Motivo (opcional):"), _("Motivo"))
            motivo = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
            dlg.Destroy()
        acc, canal = self.account_id, self.canal

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.accion_usuario_canal(acc, canal, nick, accion, motivo)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Hecho."))
                try:
                    self._cargar()
                except Exception:
                    pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class IRCChannelListDialog(wx.Dialog):
    """Lista de canales del servidor (/LIST) con filtro; Intro para unirse."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Canales del servidor"),
                         size=(640, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Lista de canales del servidor"))
        self.account_id = account_id
        self.canales = []      # todos
        self.visibles = []     # filtrados
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Filtrar por nombre o tema:")), 0, wx.ALL, 6)
        self.txt_filtro = AccessibleTextCtrl(self, name=_("Filtro de canales"))
        self.txt_filtro.Bind(wx.EVT_TEXT, lambda e: self._aplicar_filtro())
        s.Add(self.txt_filtro, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        self.lbl = wx.StaticText(self, label=_("Canales (Intro para unirte):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        # Lista VIRTUAL (LC_VIRTUAL): en redes grandes /LIST devuelve decenas de miles de canales; en
        # modo virtual el control solo pinta las filas visibles, así que se cargan TODOS sin ralentizar.
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de canales"), virtual=True)
        self.lista.InsertColumn(0, _("Canal"), width=180)
        self.lista.InsertColumn(1, _("Usuarios"), width=80)
        self.lista.InsertColumn(2, _("Tema"), width=340)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_unirse)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_unirse = wx.Button(self, label=_("&Unirse al canal"))
        self.btn_unirse.Bind(wx.EVT_BUTTON, self._on_unirse)
        fila.Add(self.btn_unirse, 0, wx.ALL, 5)
        # Ayudas de navegación para listas largas: en qué posición estoy e ir a una posición.
        self.btn_pos = wx.Button(self, label=_("Mi &posición"))
        self.btn_pos.Bind(wx.EVT_BUTTON, self._mi_posicion)
        fila.Add(self.btn_pos, 0, wx.ALL, 5)
        self.btn_ir = wx.Button(self, label=_("&Ir a una posición..."))
        self.btn_ir.Bind(wx.EVT_BUTTON, self._ir_a_posicion)
        fila.Add(self.btn_ir, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._descargar()

    def _descargar(self):
        speak(_("Pidiendo la lista de canales (puede tardar en redes grandes)..."), interrupt=True)
        acc = self.account_id

        def _bg(ctx):
            from app.services.irc import irc_service
            return irc_service.canales_del_servidor(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo obtener la lista: {0}").format(err))
                return
            self.canales = res or []
            self._aplicar_filtro()
            notificar(_("{0} canales.").format(len(self.canales)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _aplicar_filtro(self):
        filtro = self.txt_filtro.GetValue().strip().lower()
        self.visibles = [c for c in self.canales
                         if not filtro or filtro in (c.get("name") or "").lower()
                         or filtro in (c.get("topic") or "").lower()]
        # Lista VIRTUAL: se cargan TODOS los canales filtrados (sin tope). La carga diferida
        # (congelar_diferido) hace que pintar miles de filas sea instantáneo: solo toca el modelo y
        # fija el conteo una vez al terminar.
        self.lista.congelar_diferido()
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, c in enumerate(self.visibles):
            self.lista.InsertItem(i, c.get("name") or "")
            self.lista.SetItem(i, 1, str(c.get("users") or 0))
            self.lista.SetItem(i, 2, (c.get("topic") or "").replace("\n", " "))
            self.lista.establecer_lectura(i, _("{0}, {1} usuarios. {2}").format(
                c.get("name") or "", c.get("users") or 0, c.get("topic") or ""))
        self.lbl.SetLabel(_("Canales: {0}. Intro para unirte:").format(len(self.visibles)))

    def _on_unirse(self, event):
        i = self.lista.GetFocusedItem()
        if i < 0 or i >= len(self.visibles):
            return
        canal = self.visibles[i].get("name")
        acc = self.account_id

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service._run(irc_service._clients[acc].join(canal), timeout=20.0)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Dentro de {0}.").format(canal) if not e else _("No se pudo entrar: {0}").format(e)),
            pass_context=True)

    def _mi_posicion(self, event):
        """Dice en voz en qué posición de la lista está el foco (canal N de M)."""
        total = len(self.visibles)
        if total == 0:
            speak(_("La lista está vacía."), interrupt=True)
            return
        i = self.lista.GetFocusedItem()
        if i < 0:
            speak(_("No hay ningún canal seleccionado. Hay {0} canales.").format(total),
                  interrupt=True)
            return
        nombre = self.visibles[i].get("name") or ""
        speak(_("Canal {0} de {1}: {2}").format(i + 1, total, nombre), interrupt=True)

    def _ir_a_posicion(self, event):
        """Pide un número y salta a ese canal de la lista (mueve el foco a esa posición)."""
        total = len(self.visibles)
        if total == 0:
            speak(_("La lista está vacía."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(
            self, _("¿A qué posición quieres ir? (1 a {0}):").format(total),
            _("Ir a una posición"))
        valor = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not valor:
            return
        try:
            n = int(valor)
        except ValueError:
            speak(_("Escribe un número entre 1 y {0}.").format(total), interrupt=True)
            return
        if not (1 <= n <= total):
            speak(_("Fuera de rango. Escribe un número entre 1 y {0}.").format(total), interrupt=True)
            return
        idx = n - 1
        self.lista.Focus(idx)
        self.lista.Select(idx)
        self.lista.EnsureVisible(idx)
        self.lista.SetFocus()
        nombre = self.visibles[idx].get("name") or ""
        speak(_("Canal {0} de {1}: {2}").format(n, total, nombre), interrupt=True)


class IRCModosCanalDialog(wx.Dialog):
    """Modos de un canal (I7): casillas para moderado / solo invitación / tema bloqueado /
    sin mensajes externos / secreto / privado, más clave y límite de usuarios. El servidor
    valida que seas operador (si no, lo dice)."""

    def __init__(self, parent, account_id, canal):
        super().__init__(parent, title=_("Modos del canal {0}").format(canal),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Modos del canal"))
        self.account_id = account_id
        self.canal = canal
        from app.services.irc.irc_avanzado import MODOS_CANAL
        self._defs = MODOS_CANAL
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Activa o desactiva los modos y pulsa Guardar:")),
              0, wx.ALL, 6)
        self._checks = {}
        for letra, etiqueta in self._defs:
            chk = wx.CheckBox(self, label=etiqueta, name=etiqueta)
            self._checks[letra] = chk
            s.Add(chk, 0, wx.ALL, 4)
        s.Add(wx.StaticText(self, label=_("&Clave del canal (vacío = sin clave):")), 0, wx.ALL, 6)
        self.txt_clave = AccessibleTextCtrl(self, name=_("Clave del canal"))
        s.Add(self.txt_clave, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Límite de usuarios (0 = sin límite):")), 0, wx.ALL, 6)
        self.txt_limite = AccessibleTextCtrl(self, name=_("Límite de usuarios"))
        s.Add(self.txt_limite, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        self.btn_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        self.btn_ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        fila.Add(self.btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        from app.services.irc import irc_service
        estado = irc_service.modos_actuales_canal(self.account_id, self.canal)
        self._estado = estado
        letras = estado.get("letras") or ""
        for letra, chk in self._checks.items():
            chk.SetValue(letra in letras)
        self.txt_clave.SetValue(estado.get("clave") or "")
        self.txt_limite.SetValue(str(estado.get("limite") or 0))

    def _on_guardar(self, event):
        acc, canal = self.account_id, self.canal
        # Calcular los cambios respecto al estado leído.
        letras_antes = set(self._estado.get("letras") or "")
        cambios = []  # (modo, arg)
        for letra, chk in self._checks.items():
            marcado = chk.GetValue()
            if marcado and letra not in letras_antes:
                cambios.append((f"+{letra}", ""))
            elif not marcado and letra in letras_antes:
                cambios.append((f"-{letra}", ""))
        clave_nueva = self.txt_clave.GetValue().strip()
        clave_antes = self._estado.get("clave") or ""
        try:
            limite_nuevo = int(self.txt_limite.GetValue().strip() or 0)
        except ValueError:
            limite_nuevo = 0
        limite_antes = int(self._estado.get("limite") or 0)
        self.btn_ok.Disable()

        def _bg(ctx):
            from app.services.irc import irc_service
            for modo, arg in cambios:
                irc_service.fijar_modo_canal(acc, canal, modo, arg)
            if clave_nueva != clave_antes:
                irc_service.fijar_clave_canal(acc, canal, clave_nueva)
            if limite_nuevo != limite_antes:
                irc_service.fijar_limite_canal(acc, canal, limite_nuevo)
            return True

        def _fin(res, err):
            try:
                self.btn_ok.Enable()
            except Exception:
                pass
            if err:
                notificar(_("No se pudo (¿eres operador del canal?): {0}").format(err))
                return
            notificar(_("Modos aplicados."))
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class IRCBaneosDialog(wx.Dialog):
    """Lista de baneos de un canal (I7): ver las máscaras baneadas (con quién y cuándo),
    quitar baneos y añadir uno por máscara. El servidor valida los permisos."""

    def __init__(self, parent, account_id, canal):
        super().__init__(parent, title=_("Baneos de {0}").format(canal), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Baneos del canal"))
        self.account_id = account_id
        self.canal = canal
        self.baneos = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Baneos:"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de baneos"))
        self.lista.InsertColumn(0, _("Máscara"), width=280)
        self.lista.InsertColumn(1, _("Puesto por"), width=160)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_quitar = wx.Button(self, label=_("&Quitar el baneo"))
        btn_quitar.Bind(wx.EVT_BUTTON, self._on_quitar)
        fila.Add(btn_quitar, 0, wx.ALL, 5)
        btn_add = wx.Button(self, label=_("&Añadir baneo por máscara..."))
        btn_add.Bind(wx.EVT_BUTTON, self._on_add)
        fila.Add(btn_add, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        speak(_("Pidiendo la lista de baneos..."), interrupt=True)
        acc, canal = self.account_id, self.canal

        def _bg(ctx):
            from app.services.irc import irc_service
            return irc_service.lista_baneos(acc, canal)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo obtener la lista: {0}").format(err))
                return
            self.baneos = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, b in enumerate(self.baneos):
                self.lista.InsertItem(i, b.get("mascara") or "")
                self.lista.SetItem(i, 1, b.get("por") or "")
                self.lista.establecer_lectura(i, _("{0}, puesto por {1}").format(
                    b.get("mascara") or "", b.get("por") or _("desconocido")))
            self.lbl.SetLabel(_("{0} baneos:").format(len(self.baneos)))
            if self.baneos:
                self.lista.Focus(0)
                self.lista.Select(0)
            else:
                notificar(_("No hay baneos en este canal."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_quitar(self, event):
        i = self.lista.GetFocusedItem()
        if i < 0 or i >= len(self.baneos):
            return
        mascara = self.baneos[i].get("mascara") or ""
        acc, canal = self.account_id, self.canal

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.quitar_baneo(acc, canal, mascara)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿eres operador?): {0}").format(err))
            else:
                notificar(_("Baneo quitado."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_add(self, event):
        dlg = CustomTextEntryDialog(self, _("Máscara a banear (ej: *!*@host.com):"),
                                    _("Añadir baneo"))
        mascara = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not mascara:
            return
        acc, canal = self.account_id, self.canal

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.banear_mascara(acc, canal, mascara)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿eres operador?): {0}").format(err))
            else:
                notificar(_("Baneo añadido."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
