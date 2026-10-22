# -*- coding: utf-8 -*-
"""Mixin del MainFrame para el cliente IRC (tanda I1).
Metodos: _crear_menu_irc, _irc_arranque_autoconectar, _irc_cuenta_o_primera.
"""
import wx
import logging

from app.core.database import db
from app.core.speech import speak

log = logging.getLogger(__name__)


class _Mf_irc_handlers:
    def _crear_menu_irc(self, menubar):
        """Menú IRC de la barra (solo se llama si el servicio está habilitado)."""
        menu_irc = wx.Menu()
        it = menu_irc.Append(wx.ID_ANY, _("Conectar a la red"),
                             _("Conectar la red IRC activa (o la primera)"))
        self.Bind(wx.EVT_MENU, lambda e: self.irc_ctrl.conectar_red(self._irc_cuenta_o_primera()), it)
        it = menu_irc.Append(wx.ID_ANY, _("Desconectar de la red"),
                             _("Desconectar la red IRC activa"))
        self.Bind(wx.EVT_MENU, lambda e: self.irc_ctrl.desconectar_red(self._irc_cuenta_o_primera()), it)
        menu_irc.AppendSeparator()
        it = menu_irc.Append(wx.ID_ANY, _("Consola del servidor (enviar comandos)..."),
                             _("Ventana para escribir comandos al servidor (registro con NickServ, "
                               "/quote...) sin tener que entrar antes a un canal"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(
            lambda acc: self.irc_ctrl.abrir_consola_servidor(account_id=acc)), it)
        it = menu_irc.Append(wx.ID_ANY, _("Unirse a un canal..."),
                             _("Escribe un canal (ej: #nvda) y únete"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(
            lambda acc: self.irc_ctrl.unirse_canal(account_id=acc)), it)
        it = menu_irc.Append(wx.ID_ANY, _("Canales del servidor..."),
                             _("Lista de canales públicos de la red, con filtro y unirse"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(self.irc_ctrl.lista_canales_servidor), it)
        it = menu_irc.Append(wx.ID_ANY, _("Datos de un usuario (whois)..."),
                             _("Consulta los datos de un apodo de la red"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(self.irc_ctrl.whois_preguntando), it)
        it = menu_irc.Append(wx.ID_ANY, _("Usuarios ignorados..."),
                             _("Ver y quitar usuarios ignorados de la red activa"))
        self.Bind(wx.EVT_MENU, lambda e: self.irc_ctrl.gestionar_ignorados(), it)
        it = menu_irc.Append(wx.ID_ANY, _("Amigos (avisos de conexión)..."),
                             _("Apodos que la red vigila: te avisa cuando entran o salen"))
        self.Bind(wx.EVT_MENU, lambda e: self.irc_ctrl.gestionar_amigos(), it)
        menu_irc.AppendSeparator()
        # I7: estado propio y acciones de red.
        it = menu_irc.Append(wx.ID_ANY, _("Marcarme como ausente..."),
                             _("Avisar de que estás ausente con un mensaje"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(self.irc_ctrl.marcar_ausente), it)
        it = menu_irc.Append(wx.ID_ANY, _("Volver a estar disponible"),
                             _("Quitar tu estado de ausente"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(self.irc_ctrl.volver_disponible), it)
        it = menu_irc.Append(wx.ID_ANY, _("Datos de un apodo desconectado (whowas)..."),
                             _("Consulta el historial de un apodo que ya no está en la red"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(self.irc_ctrl.whowas_preguntando), it)
        it = menu_irc.Append(wx.ID_ANY, _("Hacerme operador de la red..."),
                             _("Identificarte como operador de la red (IRCop) con tus credenciales"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(self.irc_ctrl.hacerse_operador), it)
        it = menu_irc.Append(wx.ID_ANY, _("Enviar wallops..."),
                             _("Mensaje a todos los que reciben wallops (normalmente operadores)"))
        self.Bind(wx.EVT_MENU, lambda e: self._irc_con_cuenta(self.irc_ctrl.enviar_wallops), it)
        menu_irc.AppendSeparator()
        it = menu_irc.Append(wx.ID_ANY, _("Añadir red IRC..."),
                             _("Dar de alta una nueva red IRC"))
        self.Bind(wx.EVT_MENU, self._on_irc_add_red, it)
        it = menu_irc.Append(wx.ID_ANY, _("Editar red..."),
                             _("Cambiar servidor, apodo, canales o contraseñas de la red activa"))
        self.Bind(wx.EVT_MENU, lambda e: self.irc_ctrl.editar_red(self._irc_cuenta_o_primera()), it)
        menubar.Append(menu_irc, _("&IRC"))

    def _irc_cuenta_o_primera(self):
        """Red IRC activa en el árbol, o la primera dada de alta."""
        if getattr(self, "active_irc_account_id", None):
            return self.active_irc_account_id
        fila = db.query_one(
            "SELECT id FROM accounts WHERE service_id = 'irc' AND active = 1 ORDER BY position, id")
        return fila[0] if fila else None

    def _irc_con_cuenta(self, fn):
        """Ejecuta una acción del controlador PASÁNDOLE la red (la activa o la primera).
        OJO: NUNCA asignar self.active_irc_account_id aquí: esa variable es "el buffer que estás
        mirando" y la fijan solo las selecciones del árbol. Contaminarla desde el menú hacía que,
        al volver a Mastodon, el Intro (y otros despachos por red activa) cayeran en la rama de
        IRC y no abrieran los enlaces."""
        acc = self._irc_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una red IRC en Gestionar Cuentas."), interrupt=True)
            return
        fn(acc)

    def _on_irc_add_red(self, event):
        from .irc_dialogs import IRCNetworkDialog
        dlg = IRCNetworkDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_navegacion()
        dlg.Destroy()

    def _irc_arranque_autoconectar(self):
        """Al arrancar: conectar en segundo plano las redes marcadas con conexión automática.
        No bloquea nada (cada conexión va por task_manager con clave única)."""
        try:
            from app.core.feature_flags import servicio_habilitado
            if not servicio_habilitado("irc") or getattr(self, "irc_ctrl", None) is None:
                return
            self.irc_ctrl.conectar_redes_auto()
        except Exception as e:
            log.debug(f"IRC: autoconexión al arrancar no disponible: {e}")
