# -*- coding: utf-8 -*-
"""Diálogos de pasarelas / gateways / relays de XMPP (tanda X8).

- XMPPPasarelasDialog: lista las pasarelas (transportes) del servidor y, por cada una, permite
  registrarse, ver/ejecutar sus comandos (emparejamiento, re-login, listar grupos…), abrir el chat
  con la pasarela (donde llega el código/QR como texto) y cancelar el registro.
- XMPPRegistroGatewayDialog: el formulario de registro de una pasarela (0077), reutilizando el
  pintado de campos accesibles del diálogo genérico de X7.

El emparejamiento (p. ej. el QR de WhatsApp) siempre se presenta como TEXTO accesible: llega como
mensaje de la pasarela (chat) o como un campo de texto de un comando ad-hoc, nunca solo como imagen.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, mostrar_info, confirmar
from .xmpp_admin_dialogs import pintar_campo, recoger_controles, XMPPComandosDialog


class XMPPRegistroGatewayDialog(wx.Dialog):
    """Formulario de registro en una pasarela (0077 / 0100)."""

    def __init__(self, parent, account_id, gateway_jid, nombre, datos):
        self.account_id = account_id
        self.gateway_jid = gateway_jid
        self.nombre = nombre
        self.datos = datos  # {registrado, instrucciones, campos, es_form}
        titulo = (_("Editar registro en {0}") if datos.get("registrado")
                  else _("Registrarse en {0}")).format(nombre)
        super().__init__(parent, title=titulo, size=(560, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Registro en una pasarela de XMPP"))
        self._controles = []
        s = wx.BoxSizer(wx.VERTICAL)
        panel = wx.ScrolledWindow(self, style=wx.VSCROLL)
        panel.SetScrollRate(0, 10)
        ps = wx.BoxSizer(wx.VERTICAL)
        self.txt_instr = None
        # ¿Es la pasarela de WhatsApp? Entonces añadimos una explicación PROPIA (en español) del
        # emparejamiento por QR, porque el mensaje del servidor viene en inglés y es escueto.
        self._es_whatsapp = "whatsapp" in (str(gateway_jid) + " " + str(nombre)).lower()
        self._preambulo = self._preambulo_whatsapp() if self._es_whatsapp else ""
        instr_servidor = datos.get("instrucciones") or ""
        if self._preambulo or instr_servidor:
            ps.Add(wx.StaticText(panel, label=_("Instrucciones:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
            self.txt_instr = AccessibleTextCtrl(
                panel, value=self._componer_instrucciones(instr_servidor),
                style=wx.TE_MULTILINE | wx.TE_READONLY, name=_("Instrucciones del registro"),
                ayuda_texto=_("Instrucciones del registro. Solo lectura."))
            self.txt_instr.SetMinSize((-1, 140 if self._es_whatsapp else 90))
            ps.Add(self.txt_instr, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
            if instr_servidor:
                self._traducir_instrucciones(instr_servidor)
        for c in datos.get("campos", []):
            entrada = pintar_campo(panel, ps, c)
            if entrada is not None:
                self._controles.append(entrada)
        if not datos.get("campos"):
            ps.Add(wx.StaticText(panel, label=_("Esta pasarela no pide datos: pulsa Registrarse.")),
                   0, wx.ALL, 6)
        panel.SetSizer(ps)
        s.Add(panel, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn = wx.Button(self, wx.ID_OK, _("&Registrarse"))
        btn.Bind(wx.EVT_BUTTON, self._on_registrar)
        fila.Add(btn, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()

    def _preambulo_whatsapp(self):
        """Explicación PROPIA (en español) de cómo emparejar WhatsApp por QR, para poner ENCIMA de
        las instrucciones (en inglés) que manda la pasarela."""
        return _(
            "Para vincular tu cuenta de WhatsApp:\n"
            "1. Pulsa «Registrarse» aquí abajo.\n"
            "2. Vuelve a la lista de pasarelas y pulsa el botón «Abrir chat con la pasarela».\n"
            "3. En ese chat te llegará el código QR como una IMAGEN. Sitúate sobre esa imagen y "
            "pulsa la tecla Espacio (o elige «Mostrar como código QR») para verlo en grande.\n"
            "4. En el WhatsApp de tu móvil, ve a Ajustes, «Dispositivos vinculados», «Vincular un "
            "dispositivo» y escanea con la cámara el código QR que ves en pantalla.\n"
            "Si prefieres no usar el QR, puedes emparejar por número de teléfono con el comando "
            "«pair-phone» (Emparejar teléfono) desde «Comandos del gateway».")

    def _componer_instrucciones(self, instr_servidor):
        """Junta el preámbulo propio (si es WhatsApp) con las instrucciones de la pasarela."""
        instr_servidor = (instr_servidor or "").strip()
        if self._preambulo and instr_servidor:
            return self._preambulo + "\n\n" + _("Mensaje de la pasarela:") + "\n" + instr_servidor
        return self._preambulo or instr_servidor

    def _traducir_instrucciones(self, texto):
        """Traduce las instrucciones de la pasarela al español en segundo plano (motor gratuito) y
        las muestra SOLO en español (sin dejar el original en inglés)."""
        def _bg(ctx):
            from app.services.translation.translation_service import translation_service
            return translation_service.traducir(texto, "auto", "es")

        def _fin(res, err):
            if err or not res or not self.txt_instr:
                return
            try:
                # Recomponer con el preámbulo propio + la traducción (nunca el original en inglés).
                self.txt_instr.SetValue(self._componer_instrucciones(res.strip()))
            except Exception:
                pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_registrar(self, event):
        respuestas = recoger_controles(self._controles)
        acc, gw, es_form = self.account_id, self.gateway_jid, self.datos.get("es_form", False)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.registrar_en_gateway(acc, gw, respuestas, es_form)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo registrar: {0}").format(err))
                return
            notificar(_("Registro enviado. Si la pasarela pide emparejamiento (por ejemplo el "
                        "código de WhatsApp), ábrelo en 'Comandos del gateway' o en el chat con la "
                        "pasarela: el código aparece como texto."))
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class XMPPPasarelasDialog(wx.Dialog):
    """Pasarelas (transportes) del servidor: registrarse, comandos, chat y cancelar registro."""

    def __init__(self, parent, account_id):
        self.account_id = account_id
        self.gateways = []
        super().__init__(parent, title=_("Pasarelas del servidor"),
                         size=(600, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Pasarelas y relays de XMPP"))
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "Las pasarelas conectan XMPP con otras redes (WhatsApp, Telegram…). Regístrate en una "
            "para hablar con sus contactos desde aquí. Aplicaciones o Intro para las acciones:")),
            0, wx.ALL, 6)
        self.lbl = wx.StaticText(self, label=_("Pasarelas disponibles:"))
        s.Add(self.lbl, 0, wx.LEFT | wx.RIGHT, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de pasarelas"))
        self.lista.InsertColumn(0, _("Pasarela"), width=260)
        self.lista.InsertColumn(1, _("Red"), width=140)
        self.lista.InsertColumn(2, _("JID"), width=180)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._registrar())
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, label=_("&Registrarse..."))
        b.Bind(wx.EVT_BUTTON, lambda e: self._registrar())
        fila.Add(b, 0, wx.ALL, 4)
        b = wx.Button(self, label=_("&Comandos del gateway..."))
        b.Bind(wx.EVT_BUTTON, lambda e: self._comandos())
        fila.Add(b, 0, wx.ALL, 4)
        b = wx.Button(self, label=_("Abrir &chat con la pasarela"))
        b.Bind(wx.EVT_BUTTON, lambda e: self._chat())
        fila.Add(b, 0, wx.ALL, 4)
        b = wx.Button(self, label=_("Cancelar re&gistro..."))
        b.Bind(wx.EVT_BUTTON, lambda e: self._desregistrar())
        fila.Add(b, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Ce&rrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id
        speak(_("Buscando pasarelas..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.descubrir_transportes(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron buscar pasarelas: {0}").format(err))
                return
            self.gateways = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, g in enumerate(self.gateways):
                self.lista.InsertItem(i, g["nombre"])
                self.lista.SetItem(i, 1, g["red"] or g["tipo"] or _("(desconocida)"))
                self.lista.SetItem(i, 2, g["jid"])
                self.lista.establecer_lectura(i, _("{0}, red {1}, {2}").format(
                    g["nombre"], g["red"] or g["tipo"] or _("desconocida"), g["jid"]))
            self.lbl.SetLabel(_("{0} pasarelas:").format(len(self.gateways)))
            if self.gateways:
                self.lista.Focus(0)
                self.lista.Select(0)
            else:
                notificar(_("Tu servidor no ofrece pasarelas. Necesitas un servidor con un bridge "
                            "instalado (por ejemplo slidge-whatsapp)."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.gateways[i] if 0 <= i < len(self.gateways) else None

    def _on_menu(self, event):
        g = self._actual()
        if not g:
            return
        menu = wx.Menu()
        for etiqueta, fn in ((_("Registrarse / editar registro..."), self._registrar),
                             (_("Comandos del gateway..."), self._comandos),
                             (_("Abrir chat con la pasarela"), self._chat),
                             (_("Cancelar registro..."), self._desregistrar)):
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, f=fn: f(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _registrar(self):
        g = self._actual()
        if not g:
            speak(_("Selecciona una pasarela."), interrupt=True)
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.formulario_registro(acc, g["jid"])

        def _fin(res, err):
            if err:
                notificar(_("No se pudo obtener el registro: {0}").format(err))
                return
            dlg = XMPPRegistroGatewayDialog(self, acc, g["jid"], g["nombre"], res or {})
            dlg.ShowModal()
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _comandos(self):
        g = self._actual()
        if not g:
            speak(_("Selecciona una pasarela."), interrupt=True)
            return
        dlg = XMPPComandosDialog(self, self.account_id, jid=g["jid"],
                                 titulo=_("Comandos de {0}").format(g["nombre"]))
        dlg.ShowModal()
        dlg.Destroy()

    def _chat(self):
        g = self._actual()
        if not g:
            speak(_("Selecciona una pasarela."), interrupt=True)
            return
        from .xmpp_conversation import XMPPConversationDialog
        dlg = XMPPConversationDialog(self, self.account_id, g["jid"], g["nombre"])
        dlg.ShowModal()
        dlg.Destroy()

    def _desregistrar(self):
        g = self._actual()
        if not g:
            speak(_("Selecciona una pasarela."), interrupt=True)
            return
        if confirmar(self, _("Vas a cancelar tu registro en {0}. Dejarás de usar esa pasarela y sus "
                             "contactos desaparecerán de tu lista. ¿Seguro?").format(g["nombre"]),
                     _("Cancelar registro")) != wx.ID_YES:
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.desregistrar_gateway(acc, g["jid"])
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Registro cancelado.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)
