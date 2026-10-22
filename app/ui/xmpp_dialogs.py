# -*- coding: utf-8 -*-
"""Diálogos del cliente XMPP: alta de cuenta, agregar contacto (X1), unirse a sala, ocupantes y
directorio de salas (X4)."""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import (AccessibleTextCtrl, AccessibleListCtrl, CustomTextEntryDialog,
                                  mostrar_info, confirmar)


class XMPPLoginDialog(wx.Dialog):
    """Alta de una cuenta de XMPP: JID (usuario@servidor) + contraseña. El servidor y el puerto se
    resuelven solos por SRV; TLS es obligatorio. El recurso por defecto es 'SocialRSS'."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Añadir cuenta de XMPP (Jabber)"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Añadir cuenta de XMPP"))
        self.account_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&JID (usuario@servidor, ej: nombre@jabber.org):")), 0, wx.ALL, 6)
        self.txt_jid = AccessibleTextCtrl(self, name=_("JID de XMPP"),
                                          ayuda_texto=_("Tu dirección de Jabber completa: usuario@servidor. "
                                                        "El servidor real y el puerto se descubren solos."))
        s.Add(self.txt_jid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        s.Add(wx.StaticText(self, label=_("&Contraseña:")), 0, wx.ALL, 6)
        self.txt_pass = AccessibleTextCtrl(self, style=wx.TE_PASSWORD, name=_("Contraseña de XMPP"),
                                           ayuda_texto=_("Se guarda cifrada en tu equipo; XMPP la usa en "
                                                         "cada conexión (no hay token de sesión)."))
        s.Add(self.txt_pass, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        s.Add(wx.StaticText(self, label=_("&Recurso (opcional, identifica este dispositivo):")), 0, wx.ALL, 6)
        self.txt_recurso = AccessibleTextCtrl(self, value="SocialRSS", name=_("Recurso de XMPP"))
        s.Add(self.txt_recurso, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        s.Add(wx.StaticText(self, label=_(
            "La conexión SIEMPRE va cifrada (TLS). Si tu cuenta o servidor no existe, se avisa con\n"
            "claridad. Para crear una cuenta nueva en un servidor que lo permita, usa el menú XMPP.")),
              0, wx.ALL, 8)

        # Servidores cuyo certificado TLS ha CADUCADO o es autofirmado (se reportó, p. ej.,
        # xmpp.eco.br): la conexión falla en la verificación del certificado. Con esta casilla se
        # acepta ese certificado (la conexión sigue CIFRADA, pero no se comprueba su validez). Es
        # menos seguro: solo para tu propio servidor o uno de confianza.
        self.cb_no_cert = wx.CheckBox(self, label=_(
            "No verificar el certificado del servidor (para servidores con certificado caducado o "
            "autofirmado; la conexión sigue cifrada, pero es menos seguro)"),
            name=_("No verificar el certificado TLS del servidor XMPP"))
        s.Add(self.cb_no_cert, 0, wx.ALL, 8)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        self.btn_ok = wx.Button(self, wx.ID_OK, _("Iniciar &sesión"))
        self.btn_ok.Bind(wx.EVT_BUTTON, self._on_login)
        fila.Add(self.btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_jid.SetFocus()

    def _on_login(self, event):
        jid = self.txt_jid.GetValue().strip()
        password = self.txt_pass.GetValue()
        recurso = self.txt_recurso.GetValue().strip() or "SocialRSS"
        if not jid or "@" not in jid or not password:
            speak(_("Escribe el JID completo (usuario@servidor) y la contraseña."), interrupt=True)
            return
        no_cert = self.cb_no_cert.GetValue()
        self.btn_ok.Disable()
        speak(_("Verificando la cuenta de XMPP..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            acc = xmpp_service.crear_cuenta(jid, password, recurso, no_verificar_cert=no_cert)
            xmpp_service.guardar_password(acc, password)
            return acc

        def _fin(res, err):
            try:
                self.btn_ok.Enable()
            except Exception:
                pass
            if err:
                notificar(_("No se pudo añadir la cuenta: {0}").format(err))
                return
            self.account_id = res
            notificar(_("Cuenta de XMPP añadida."))
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class XMPPAgregarContactoDialog(wx.Dialog):
    """Agregar un contacto al roster (le pide suscripción de presencia)."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Agregar contacto de XMPP"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Agregar contacto"))
        self.account_id = account_id
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&JID del contacto (usuario@servidor):")), 0, wx.ALL, 6)
        self.txt_jid = AccessibleTextCtrl(self, name=_("JID del contacto"))
        s.Add(self.txt_jid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Nombre (opcional):")), 0, wx.ALL, 6)
        self.txt_nombre = AccessibleTextCtrl(self, name=_("Nombre del contacto"))
        s.Add(self.txt_nombre, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        btn_ok = wx.Button(self, wx.ID_OK, _("&Agregar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        fila.Add(btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_jid.SetFocus()

    def _on_ok(self, event):
        jid = self.txt_jid.GetValue().strip()
        nombre = self.txt_nombre.GetValue().strip()
        if not jid or "@" not in jid:
            speak(_("Escribe un JID válido (usuario@servidor)."), interrupt=True)
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.agregar_contacto(acc, jid, nombre)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Contacto agregado; espera a que acepte tu solicitud.") if not e
            else _("No se pudo: {0}").format(e)), pass_context=True)
        self.EndModal(wx.ID_OK)


class XMPPUnirseSalaDialog(wx.Dialog):
    """Unirse a una sala MUC: JID de la sala, apodo y contrasena opcional."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Unirse a una sala de XMPP"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Unirse a una sala"))
        self.account_id = account_id
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Sala (sala@conference.servidor):")), 0, wx.ALL, 6)
        self.txt_sala = AccessibleTextCtrl(self, name=_("JID de la sala"))
        s.Add(self.txt_sala, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Apodo (con el que te veran; vacio = tu usuario):")), 0, wx.ALL, 6)
        self.txt_nick = AccessibleTextCtrl(self, name=_("Apodo en la sala"))
        s.Add(self.txt_nick, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Contrasena de la sala (si la pide):")), 0, wx.ALL, 6)
        self.txt_pass = AccessibleTextCtrl(self, style=wx.TE_PASSWORD, name=_("Contrasena de la sala"))
        s.Add(self.txt_pass, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        self.cb_guardar = wx.CheckBox(self, label=_("Guardar la sala y entrar automaticamente al conectar"),
                                      name=_("Guardar la sala"))
        self.cb_guardar.SetValue(True)
        s.Add(self.cb_guardar, 0, wx.ALL, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        btn_ok = wx.Button(self, wx.ID_OK, _("&Entrar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        fila.Add(btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_sala.SetFocus()

    def _on_ok(self, event):
        sala = self.txt_sala.GetValue().strip()
        nick = self.txt_nick.GetValue().strip()
        password = self.txt_pass.GetValue()
        guardar = self.cb_guardar.GetValue()
        if not sala or "@" not in sala:
            speak(_("Escribe el JID de la sala (sala@conference.servidor)."), interrupt=True)
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.unirse_sala(acc, sala, nick, password, guardar=guardar)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Dentro de la sala.") if not e else _("No se pudo entrar: {0}").format(e)),
            pass_context=True)
        self.EndModal(wx.ID_OK)


class XMPPDirectorioSalasDialog(wx.Dialog):
    """Directorio de salas publicas del servicio MUC; Intro para unirse."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Directorio de salas de XMPP"),
                         size=(640, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Directorio de salas"))
        self.account_id = account_id
        self.salas = []
        s = wx.BoxSizer(wx.VERTICAL)
        fila_b = wx.BoxSizer(wx.HORIZONTAL)
        fila_b.Add(wx.StaticText(self, label=_("&Servicio (vacio = detectar):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.txt_svc = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Servicio MUC"),
                                          ayuda_texto=_("Vacio para detectarlo; o escribe conference.servidor."))
        self.txt_svc.Bind(wx.EVT_TEXT_ENTER, lambda e: self._buscar())
        fila_b.Add(self.txt_svc, 1, wx.EXPAND | wx.ALL, 4)
        fila_b.Add(wx.StaticText(self, label=_("&Filtro:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        self.txt_filtro = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Filtro de salas"))
        self.txt_filtro.Bind(wx.EVT_TEXT_ENTER, lambda e: self._buscar())
        fila_b.Add(self.txt_filtro, 1, wx.EXPAND | wx.ALL, 4)
        btn = wx.Button(self, label=_("Bu&scar"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self._buscar())
        fila_b.Add(btn, 0, wx.ALL, 4)
        s.Add(fila_b, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
        self.lbl = wx.StaticText(self, label=_("Salas (Intro para unirte):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de salas publicas"))
        self.lista.InsertColumn(0, _("Sala"), width=260)
        self.lista.InsertColumn(1, _("Direccion"), width=320)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._unirse())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(s)
        self.CenterOnParent()
        self._buscar()

    def _buscar(self):
        svc = self.txt_svc.GetValue().strip()
        filtro = self.txt_filtro.GetValue().strip()
        speak(_("Buscando salas..."), interrupt=True)
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.directorio_salas(acc, svc, filtro)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            self.salas = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, sala in enumerate(self.salas):
                self.lista.InsertItem(i, sala["nombre"])
                self.lista.SetItem(i, 1, sala["jid"])
                self.lista.establecer_lectura(i, _("{0} ({1})").format(sala["nombre"], sala["jid"]))
            self.lbl.SetLabel(_("{0} salas (Intro para unirte):").format(len(self.salas)))
            notificar(_("{0} salas.").format(len(self.salas)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _unirse(self):
        i = self.lista.GetFocusedItem()
        if i < 0 or i >= len(self.salas):
            return
        sala = self.salas[i]
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.unirse_sala(acc, sala["jid"])
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Dentro de {0}.").format(sala["nombre"]) if not e else _("No se pudo entrar: {0}").format(e)),
            pass_context=True)


class XMPPOcupantesDialog(wx.Dialog):
    """Ocupantes de una sala MUC con rol y afiliacion EN PALABRAS y acciones de moderacion."""

    def __init__(self, parent, account_id, room_jid, nombre_sala):
        super().__init__(parent, title=_("Ocupantes de {0}").format(nombre_sala),
                         size=(560, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Ocupantes de la sala"))
        self.account_id = account_id
        self.room_jid = room_jid
        self.ocupantes = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Ocupantes (Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de ocupantes"))
        self.lista.InsertColumn(0, _("Apodo"), width=200)
        self.lista.InsertColumn(1, _("Rol"), width=140)
        self.lista.InsertColumn(2, _("Afiliacion"), width=160)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_inv = wx.Button(self, label=_("&Invitar a alguien..."))
        btn_inv.Bind(wx.EVT_BUTTON, self._on_invitar)
        fila.Add(btn_inv, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        from app.services.xmpp.xmpp_service import xmpp_service
        self.ocupantes = xmpp_service.ocupantes(self.account_id, self.room_jid)
        self._mi_rol = xmpp_service.mi_rol_en(self.account_id, self.room_jid)
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, o in enumerate(self.ocupantes):
            self.lista.InsertItem(i, o["nick"])
            self.lista.SetItem(i, 1, o["rol"])
            self.lista.SetItem(i, 2, o["afiliacion"])
            self.lista.establecer_lectura(i, _("{0}, {1}, {2}").format(o["nick"], o["rol"], o["afiliacion"]))
        self.lbl.SetLabel(_("{0} ocupantes (Aplicaciones para acciones):").format(len(self.ocupantes)))
        if self.ocupantes:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.ocupantes[i] if 0 <= i < len(self.ocupantes) else None

    def _on_menu(self, event):
        o = self._actual()
        if not o:
            return
        menu = wx.Menu()
        if o.get("jid"):
            it = menu.Append(wx.ID_ANY, _("Mensaje directo con {0}").format(o["nick"]))
            self.Bind(wx.EVT_MENU, lambda e, j=o["jid"], n=o["nick"]: self._dm(j, n), it)
        if self._mi_rol == "moderator":
            menu.AppendSeparator()
            acciones = [(_("Dar voz"), "dar_voz"), (_("Quitar voz"), "quitar_voz"),
                        (_("Hacer moderador/a"), "dar_moderador"), (_("Quitar moderador/a"), "quitar_moderador"),
                        (_("Expulsar de la sala..."), "expulsar"), (_("Banear..."), "banear")]
            for etiqueta, accion in acciones:
                it = menu.Append(wx.ID_ANY, etiqueta)
                self.Bind(wx.EVT_MENU, lambda e, a=accion, n=o["nick"]: self._accion(a, n), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _dm(self, jid, nombre):
        from .xmpp_conversation import XMPPConversationDialog
        dlg = XMPPConversationDialog(self.GetParent(), self.account_id, jid, nombre)
        dlg.ShowModal()
        dlg.Destroy()

    def _accion(self, accion, nick):
        motivo = ""
        if accion in ("expulsar", "banear"):
            if confirmar(self, _("Seguro que quieres {0} a {1}?").format(
                    _("expulsar") if accion == "expulsar" else _("banear"), nick),
                    _("Confirmar")) != wx.ID_YES:
                return
            d = CustomTextEntryDialog(self, _("Motivo (opcional):"), _("Motivo"))
            motivo = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
            d.Destroy()
        acc, room = self.account_id, self.room_jid

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.accion_ocupante(acc, room, nick, accion, motivo)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Hecho."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_invitar(self, event):
        d = CustomTextEntryDialog(self, _("JID de quien invitar (usuario@servidor):"),
                                  _("Invitar a la sala"))
        jid = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not jid:
            return
        acc, room = self.account_id, self.room_jid

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.invitar_sala(acc, room, jid)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Invitacion enviada.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)


class XMPPHuellasDialog(wx.Dialog):
    """Huellas OMEMO (X6): mi propia huella (para que otros la verifiquen) y las de los dispositivos
    del contacto/sala, con su nivel de confianza. Desde aquí se marca un dispositivo como VERIFICADO
    (confianza total) o DESCONFIADO. Con BTBV activo, los dispositivos nuevos ya se confían a ciegas;
    verificar una huella a mano (comparándola en persona o por otro canal) sube el listón de seguridad."""

    def __init__(self, parent, account_id, jid, nombre, es_muc=False, propio=False):
        # propio=True: son TUS PROPIOS dispositivos (para confirmar uno nuevo desde otro). Cambia las
        # etiquetas para que quede claro que verificas tus sesiones, no las de un contacto.
        self.propio = bool(propio)
        titulo = (_("Mis dispositivos de cifrado OMEMO") if self.propio
                  else _("Huellas de cifrado OMEMO — {0}").format(nombre))
        super().__init__(parent, title=titulo,
                         size=(640, 500), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Huellas de cifrado OMEMO"))
        self.account_id = account_id
        self.jid = jid
        self.es_muc = es_muc
        self.huellas = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl_mia = wx.StaticText(self, label=_("Tu huella: (cargando...)"))
        s.Add(self.lbl_mia, 0, wx.ALL, 6)
        _lbl_lista = (_("Tus OTROS dispositivos (verifícalos comparando la huella; Aplicaciones para actuar):")
                      if self.propio
                      else _("Dispositivos del contacto (Aplicaciones para verificar):"))
        s.Add(wx.StaticText(self, label=_lbl_lista), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de dispositivos y huellas"))
        self.lista.InsertColumn(0, _("Dispositivo"), width=90)
        self.lista.InsertColumn(1, _("Confianza"), width=130)
        self.lista.InsertColumn(2, _("Huella"), width=380)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_ver = wx.Button(self, label=_("Marcar como &verificado"))
        btn_ver.Bind(wx.EVT_BUTTON, lambda e: self._confiar(True))
        fila.Add(btn_ver, 0, wx.ALL, 5)
        btn_des = wx.Button(self, label=_("&Desconfiar"))
        btn_des.Bind(wx.EVT_BUTTON, lambda e: self._confiar(False))
        fila.Add(btn_des, 0, wx.ALL, 5)
        btn_ref = wx.Button(self, label=_("&Refrescar"))
        btn_ref.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        fila.Add(btn_ref, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            mia = xmpp_service.mi_huella(acc)
            huellas = xmpp_service.huellas_de(acc, jid) if not es_muc else []
            return mia, huellas

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudieron cargar las huellas: {0}").format(err or ""))
                return
            mia, huellas = res
            if mia:
                self.lbl_mia.SetLabel(_("Tu huella (dispositivo {0}): {1}").format(
                    mia["device_id"], mia["huella"]))
            self.huellas = huellas or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, h in enumerate(self.huellas):
                conf = self._confianza_legible(h["confianza"])
                self.lista.InsertItem(i, str(h["device_id"]))
                self.lista.SetItem(i, 1, conf)
                self.lista.SetItem(i, 2, h["huella"])
                self.lista.establecer_lectura(i, _("Dispositivo {0}, {1}. Huella: {2}").format(
                    h["device_id"], conf, h["huella"]))
            if self.huellas:
                self.lista.Focus(0)
                self.lista.Select(0)
            elif not es_muc:
                notificar(_("No tienes otros dispositivos OMEMO publicados.") if self.propio
                          else _("El contacto no tiene dispositivos OMEMO publicados todavía."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    @staticmethod
    def _confianza_legible(nombre):
        return {
            "TRUSTED": _("Verificado"),
            "BLINDLY_TRUSTED": _("Confianza ciega"),
            "UNDECIDED": _("Sin decidir"),
            "DISTRUSTED": _("Desconfiado"),
        }.get(nombre, nombre or _("Desconocido"))

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.huellas[i] if 0 <= i < len(self.huellas) else None

    def _on_menu(self, event):
        h = self._actual()
        if not h:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Marcar como verificado (confianza total)"))
        self.Bind(wx.EVT_MENU, lambda e: self._confiar(True), it)
        it = menu.Append(wx.ID_ANY, _("Marcar como desconfiado (no cifrar para él)"))
        self.Bind(wx.EVT_MENU, lambda e: self._confiar(False), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _confiar(self, confiar):
        h = self._actual()
        if not h:
            speak(_("Selecciona un dispositivo."), interrupt=True)
            return
        acc, jid = self.account_id, self.jid
        ik, dev = h.get("_ik"), h.get("device_id")
        # Marcar VERIFICADO es una decisión criptográfica: no debe ser un clic a ciegas (XMPP-010).
        # Se muestra la huella COMPLETA y se exige confirmar que se ha comparado por un canal seguro
        # (llamada, en persona, QR): así "verificado" significa de verdad verificado fuera de banda.
        if confiar:
            from .accessible_controls import confirmar
            aviso = _("Vas a marcar como VERIFICADO el dispositivo {0}.\n\n"
                      "Huella:\n{1}\n\n"
                      "Confírmalo SOLO si has comparado esta huella con la otra persona por un canal "
                      "seguro (en persona, llamada de voz o QR). Si no coincide exactamente, podría "
                      "ser un intento de suplantación. ¿La has comparado y coincide?").format(
                          dev, h.get("huella") or "")
            if confirmar(self, aviso, _("Verificar dispositivo")) != wx.ID_YES:
                return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.confiar_dispositivo(acc, jid, ik, confiar)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo cambiar la confianza: {0}").format(err or ""))
            else:
                notificar(_("Dispositivo {0} marcado como {1}.").format(
                    dev, _("verificado") if confiar else _("desconfiado")))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class XMPPBuscarContactoDialog(wx.Dialog):
    """Búsqueda rápida de un contacto o grupo por nombre/JID. Escribe y la lista se filtra; Intro
    (o el botón) abre la conversación. Accesible: el foco puede saltar del campo a la lista con Tab."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Buscar contacto o grupo"),
                         size=(520, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Buscar contacto de XMPP"))
        self.account_id = account_id
        self.resultados = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Escribe para buscar (nombre o JID):")), 0, wx.ALL, 6)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Texto de búsqueda"),
                                      ayuda_texto=_("Escribe parte del nombre. Baja a la lista con Tab "
                                                    "o flecha abajo; Intro abre la conversación."))
        self.txt.Bind(wx.EVT_TEXT, lambda e: self._filtrar())
        self.txt.Bind(wx.EVT_TEXT_ENTER, lambda e: self._abrir_primero())
        self.txt.Bind(wx.EVT_KEY_DOWN, self._on_txt_key)
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Resultados"))
        self.lista.InsertColumn(0, _("Nombre"), width=260)
        self.lista.InsertColumn(1, _("Tipo"), width=90)
        self.lista.InsertColumn(2, _("Presencia / no leídos"), width=150)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._abrir_sel())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, wx.ID_OK, _("&Abrir conversación"))
        b.Bind(wx.EVT_BUTTON, lambda e: self._abrir_sel())
        fila.Add(b, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Ce&rrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._filtrar()
        self.txt.SetFocus()

    def _filtrar(self):
        from app.core.database import db
        patron = "%" + (self.txt.GetValue() or "").strip() + "%"
        self.resultados = db.query(
            "SELECT jid, nombre, tipo, COALESCE(presencia,''), COALESCE(unread_count,0) "
            "FROM xmpp_chats WHERE account_id = ? AND COALESCE(solicitud,0)=0 "
            "AND (nombre LIKE ? OR jid LIKE ?) ORDER BY unread_count DESC, tipo, nombre LIMIT 200",
            (self.account_id, patron, patron)) or []
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, (jid, nombre, tipo, pres, unread) in enumerate(self.resultados):
            tipo_txt = _("grupo") if tipo == "muc" else _("contacto")
            estado = (_("{0} sin leer").format(unread) if unread else (pres or ""))
            self.lista.InsertItem(i, nombre or jid)
            self.lista.SetItem(i, 1, tipo_txt)
            self.lista.SetItem(i, 2, estado)
            self.lista.establecer_lectura(i, f"{nombre or jid}, {tipo_txt}"
                                          + (f", {estado}" if estado else ""))
        if self.resultados:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _on_txt_key(self, event):
        if event.GetKeyCode() == wx.WXK_DOWN and self.resultados:
            self.lista.SetFocus()
            self.lista.Focus(0)
            self.lista.Select(0)
            return
        event.Skip()

    def _abrir_primero(self):
        if self.resultados:
            self._abrir(0)

    def _abrir_sel(self):
        i = self.lista.GetFocusedItem()
        if 0 <= i < len(self.resultados):
            self._abrir(i)

    def _abrir(self, i):
        jid, nombre, tipo = self.resultados[i][0], self.resultados[i][1], self.resultados[i][2]
        self.EndModal(wx.ID_OK)
        from .xmpp_conversation import XMPPConversationDialog
        dlg = XMPPConversationDialog(self.GetParent(), self.account_id, jid, nombre or jid,
                                     es_muc=(tipo == "muc"))
        dlg.ShowModal()
        dlg.Destroy()
