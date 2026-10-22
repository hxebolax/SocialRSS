# -*- coding: utf-8 -*-
"""Diálogos de perfil y estado de XMPP (tanda X9): editar tu vCard, publicar tu ánimo/actividad y
buscar usuarios en el directorio del servidor. Todo accesible (campos etiquetados, listas navegables).
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleTextCtrl, AccessibleListCtrl, mostrar_info, confirmar
from .xmpp_admin_dialogs import pintar_campo, recoger_controles


class XMPPEditarVCardDialog(wx.Dialog):
    """Editar tu ficha (vCard, XEP-0054)."""

    _CAMPOS = [("FN", _("Nombre")), ("NICKNAME", _("Apodo")), ("EMAIL", _("Correo")),
               ("TEL", _("Teléfono")), ("ORG", _("Organización")), ("TITLE", _("Cargo")),
               ("URL", _("Web")), ("BDAY", _("Cumpleaños (AAAA-MM-DD)")), ("DESC", _("Nota"))]

    def __init__(self, parent, account_id, valores):
        self.account_id = account_id
        super().__init__(parent, title=_("Editar mi ficha (vCard)"), size=(520, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Editar mi ficha de XMPP"))
        self.controles = {}
        panel = wx.ScrolledWindow(self, style=wx.VSCROLL)
        panel.SetScrollRate(0, 10)
        ps = wx.BoxSizer(wx.VERTICAL)
        for clave, etiqueta in self._CAMPOS:
            ps.Add(wx.StaticText(panel, label=etiqueta), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
            estilo = wx.TE_MULTILINE if clave == "DESC" else 0
            ctl = AccessibleTextCtrl(panel, value=(valores.get(clave) or ""), style=estilo,
                                     name=etiqueta, ayuda_texto=etiqueta)
            ps.Add(ctl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
            self.controles[clave] = ctl
        panel.SetSizer(ps)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(panel, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b.Bind(wx.EVT_BUTTON, self._on_guardar)
        fila.Add(b, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()

    def _on_guardar(self, event):
        campos = {k: c.GetValue().strip() for k, c in self.controles.items()}
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.editar_mi_vcard(acc, campos)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Ficha guardada.") if not e else _("No se pudo guardar: {0}").format(e)),
            pass_context=True)
        self.EndModal(wx.ID_OK)


class XMPPEstadoDialog(wx.Dialog):
    """Publicar tu ánimo (XEP-0107) o tu actividad (XEP-0108)."""

    def __init__(self, parent, account_id, tipo):
        self.account_id = account_id
        self.tipo = tipo  # 'mood' | 'activity'
        from app.services.xmpp.xmpp_perfil import MOODS, ACTIVIDADES
        self.opciones = MOODS if tipo == "mood" else ACTIVIDADES
        titulo = _("Mi estado de ánimo") if tipo == "mood" else _("Mi actividad")
        super().__init__(parent, title=titulo, size=(420, 260),
                         style=wx.DEFAULT_DIALOG_STYLE, name=titulo)
        s = wx.BoxSizer(wx.VERTICAL)
        etq = _("¿Cómo te sientes?") if tipo == "mood" else _("¿Qué estás haciendo?")
        s.Add(wx.StaticText(self, label=etq), 0, wx.ALL, 6)
        self.choice = wx.Choice(self, choices=[o[1] for o in self.opciones], name=etq)
        self.choice.SetSelection(0)
        s.Add(self.choice, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Texto opcional (un detalle):")), 0, wx.ALL, 6)
        self.txt = AccessibleTextCtrl(self, name=_("Texto del estado"),
                                      ayuda_texto=_("Una frase corta opcional para acompañar."))
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, wx.ID_OK, _("&Publicar"))
        b.Bind(wx.EVT_BUTTON, self._on_publicar)
        fila.Add(b, 0, wx.ALL, 5)
        bq = wx.Button(self, label=_("&Quitar mi estado"))
        bq.Bind(wx.EVT_BUTTON, self._on_quitar)
        fila.Add(bq, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()

    def _on_publicar(self, event):
        i = self.choice.GetSelection()
        if i < 0:
            return
        clave = self.opciones[i][0]
        texto = self.txt.GetValue().strip()
        acc, tipo = self.account_id, self.tipo

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            if tipo == "mood":
                return xmpp_service.publicar_animo(acc, clave, texto)
            return xmpp_service.publicar_actividad(acc, clave, texto)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Estado publicado.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)
        self.EndModal(wx.ID_OK)

    def _on_quitar(self, event):
        acc, tipo = self.account_id, self.tipo

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            if tipo == "mood":
                return xmpp_service.limpiar_animo(acc)
            return xmpp_service.limpiar_actividad(acc)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Estado retirado.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)
        self.EndModal(wx.ID_OK)


class XMPPBuscarUsuariosDialog(wx.Dialog):
    """Buscar usuarios en un directorio del servidor (XEP-0055)."""

    def __init__(self, parent, account_id, servicio):
        self.account_id = account_id
        self.servicio = servicio
        self.controles = []
        self.resultados = []
        super().__init__(parent, title=_("Buscar usuarios en {0}").format(servicio),
                         size=(600, 500), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Buscar usuarios de XMPP"))
        s = wx.BoxSizer(wx.VERTICAL)
        self.panel = wx.ScrolledWindow(self, style=wx.VSCROLL)
        self.panel.SetScrollRate(0, 10)
        self.ps = wx.BoxSizer(wx.VERTICAL)
        self.panel.SetSizer(self.ps)
        s.Add(self.panel, 0, wx.EXPAND | wx.ALL, 6)
        self.btn_buscar = wx.Button(self, label=_("&Buscar"))
        self.btn_buscar.Bind(wx.EVT_BUTTON, lambda e: self._buscar())
        s.Add(self.btn_buscar, 0, wx.ALL, 5)
        s.Add(wx.StaticText(self, label=_("Resultados (Aplicaciones para agregar):")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Resultados de la búsqueda"))
        self.lista.InsertColumn(0, _("JID"), width=240)
        self.lista.InsertColumn(1, _("Datos"), width=300)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Ce&rrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar_formulario()

    def _cargar_formulario(self):
        acc, serv = self.account_id, self.servicio

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.formulario_busqueda(acc, serv)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo abrir la búsqueda: {0}").format(err))
                return
            for c in (res or {}).get("campos", []):
                entrada = pintar_campo(self.panel, self.ps, c)
                if entrada is not None:
                    self.controles.append(entrada)
            self.panel.Layout()
            self.panel.FitInside()
            self.Layout()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _buscar(self):
        respuestas = recoger_controles(self.controles)
        acc, serv = self.account_id, self.servicio
        speak(_("Buscando..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.ejecutar_busqueda(acc, serv, respuestas)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo buscar: {0}").format(err))
                return
            self.resultados = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, fila in enumerate(self.resultados):
                jid = fila.get("jid", "")
                datos = ", ".join(f"{k}: {v}" for k, v in fila.items() if k != "jid" and v)
                self.lista.InsertItem(i, jid)
                self.lista.SetItem(i, 1, datos)
                self.lista.establecer_lectura(i, f"{jid}. {datos}")
            if self.resultados:
                self.lista.Focus(0)
                self.lista.Select(0)
                speak(_("{0} resultados.").format(len(self.resultados)), interrupt=True)
            else:
                notificar(_("Sin resultados."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_menu(self, event):
        i = self.lista.GetFocusedItem()
        if not (0 <= i < len(self.resultados)):
            return
        jid = self.resultados[i].get("jid", "")
        if not jid:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Agregar a mis contactos"))
        self.Bind(wx.EVT_MENU, lambda e: self._agregar(jid), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _agregar(self, jid):
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.agregar_contacto(acc, jid)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Solicitud enviada a {0}.").format(jid) if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)
