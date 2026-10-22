# -*- coding: utf-8 -*-
"""Mixin del MainFrame para el cliente XMPP (Jabber), tanda X1.
Metodos: _crear_menu_xmpp, _xmpp_cuenta_o_primera, _xmpp_arranque_autoconectar.
OJO (lección de IRC/Matrix): los menús NUNCA asignan self.active_xmpp_account_id (esa variable es
"el buffer que estás mirando" y solo la fija el árbol); las acciones reciben la cuenta como parámetro.
"""
import wx
import logging

from app.core.database import db
from app.core.speech import speak, notificar

log = logging.getLogger(__name__)


class _Mf_xmpp_handlers:
    def _crear_menu_xmpp(self, menubar):
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Conectar"),
                         _("Conectar la cuenta de XMPP activa (o la primera)"))
        self.Bind(wx.EVT_MENU, lambda e: self.xmpp_ctrl.conectar_cuenta(self._xmpp_cuenta_o_primera()), it)
        it = menu.Append(wx.ID_ANY, _("Desconectar"),
                         _("Desconectar la cuenta de XMPP activa"))
        self.Bind(wx.EVT_MENU, lambda e: self.xmpp_ctrl.desconectar_cuenta(self._xmpp_cuenta_o_primera()), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Agregar contacto..."),
                         _("Añadir un contacto al roster (usuario@servidor)"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_agregar_contacto(), it)
        it = menu.Append(wx.ID_ANY, _("Unirse a una sala..."),
                         _("Entrar en una sala de grupo (MUC)"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_dialogo_muc("unirse"), it)
        it = menu.Append(wx.ID_ANY, _("Directorio de salas..."),
                         _("Salas públicas del servidor; Intro para unirte"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_dialogo_muc("directorio"), it)
        it = menu.Append(wx.ID_ANY, _("Pasarelas del servidor..."),
                         _("Registrarte en una pasarela para hablar con WhatsApp, Telegram u otras redes"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_pasarelas(), it)
        # Sin acelerador: Ctrl+Shift+B está reservado GLOBALMENTE para "Leer barra de estado"
        # (shortcut_manager). Antes este ítem lo reusaba y tapaba esa lectura (conflicto reportado).
        it = menu.Append(wx.ID_ANY, _("Buscar contacto o grupo..."),
                         _("Búsqueda rápida entre tus contactos y grupos de XMPP"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_buscar_contacto(), it)
        # Presencia propia.
        sub = wx.Menu()
        from app.services.xmpp.xmpp_common import presencias_legibles
        for clave, etiqueta in presencias_legibles():
            itp = sub.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, k=clave: self._xmpp_fijar_presencia(k), itp)
        it_estado = sub.Append(wx.ID_ANY, _("Cambiar mensaje de estado..."))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_mensaje_estado(), it_estado)
        sub.AppendSeparator()
        it_inv = sub.Append(wx.ID_ANY, _("Invisible (conectado sin que te vean)"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_invisible(True), it_inv)
        it_vis = sub.Append(wx.ID_ANY, _("Volver a visible"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_invisible(False), it_vis)
        menu.AppendSubMenu(sub, _("Mi presencia"))
        # Perfil y estado enriquecido (X9): ficha, ánimo, actividad, buscar usuarios.
        it = menu.Append(wx.ID_ANY, _("Mi estado de ánimo..."),
                         _("Publica cómo te sientes (lo verán tus contactos)"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_estado("mood"), it)
        it = menu.Append(wx.ID_ANY, _("Mi actividad..."),
                         _("Publica qué estás haciendo"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_estado("activity"), it)
        it = menu.Append(wx.ID_ANY, _("Editar mi ficha (vCard)..."),
                         _("Editar tu tarjeta de contacto (nombre, correo, teléfono...)"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_editar_vcard(), it)
        it = menu.Append(wx.ID_ANY, _("Buscar usuarios..."),
                         _("Buscar personas en el directorio del servidor"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_buscar_usuarios(), it)
        it = menu.Append(wx.ID_ANY, _("Mis dispositivos de cifrado (OMEMO)..."),
                         _("Ver y CONFIRMAR tus dispositivos (sesiones) de cifrado: verifica el "
                           "nuevo desde otro para que puedan cifrarse entre sí"))
        self.Bind(wx.EVT_MENU, lambda e: self._xmpp_mis_dispositivos_omemo(), it)
        menu.AppendSeparator()
        # Submenú "Administración" (comandos ad-hoc del servidor 0133/0050 + registro en banda 0077).
        # Solo si el usuario lo activa en Preferencias → XMPP (como en Mastodon/Matrix).
        from app.core.config_manager import config
        if config.get("xmpp", "admin_tools", False):
            adm = wx.Menu()
            it = adm.Append(wx.ID_ANY, _("Comandos del servidor..."),
                            _("Ejecutar los comandos de administración que ofrece tu servidor"))
            self.Bind(wx.EVT_MENU, lambda e: self._xmpp_admin_comandos_servidor(), it)
            it = adm.Append(wx.ID_ANY, _("Ejecutar comando ad-hoc..."),
                            _("Ejecutar un comando de cualquier JID (servidor, componente o contacto)"))
            self.Bind(wx.EVT_MENU, lambda e: self._xmpp_admin_comando_adhoc(), it)
            adm.AppendSeparator()
            it = adm.Append(wx.ID_ANY, _("Cambiar mi contraseña..."),
                            _("Cambiar la contraseña de tu cuenta en el servidor"))
            self.Bind(wx.EVT_MENU, lambda e: self._xmpp_admin_cambiar_password(), it)
            it = adm.Append(wx.ID_ANY, _("Crear cuenta nueva en un servidor..."),
                            _("Registrar una cuenta nueva (si el servidor lo permite)"))
            self.Bind(wx.EVT_MENU, lambda e: self._xmpp_admin_crear_cuenta(), it)
            it = adm.Append(wx.ID_ANY, _("Cancelar (borrar) mi cuenta del servidor..."),
                            _("Eliminar tu cuenta del servidor de forma permanente"))
            self.Bind(wx.EVT_MENU, lambda e: self._xmpp_admin_cancelar_cuenta(), it)
            menu.AppendSubMenu(adm, _("Administración"))
        it = menu.Append(wx.ID_ANY, _("Añadir cuenta de XMPP..."),
                         _("Iniciar sesión en un servidor XMPP (Jabber)"))
        self.Bind(wx.EVT_MENU, self._on_xmpp_add_cuenta, it)
        menubar.Append(menu, _("&XMPP"))

    def _xmpp_cuenta_o_primera(self):
        acc = getattr(self, "active_xmpp_account_id", None)
        if acc:
            return acc
        fila = db.query_one("SELECT id FROM accounts WHERE service_id = 'xmpp' AND active = 1 "
                            "ORDER BY position, id LIMIT 1")
        return fila[0] if fila else None

    def _xmpp_mis_dispositivos_omemo(self):
        """Gestor de MIS dispositivos de cifrado OMEMO: ver/confirmar mis sesiones (device ids +
        huellas + confianza), para verificar un dispositivo nuevo desde otro. Así todos mis
        dispositivos se cifran entre sí."""
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return
        from app.services.xmpp.xmpp_service import xmpp_service
        if not xmpp_service.omemo_disponible():
            notificar(_("El cifrado OMEMO no está instalado en esta copia."))
            return
        if not xmpp_service.esta_conectada(acc):
            speak(_("Conecta la cuenta de XMPP primero."), interrupt=True)
            return
        datos = xmpp_service.datos_cuenta(acc)
        mi_jid = datos["jid"] if datos else ""
        if not mi_jid:
            return
        from .xmpp_dialogs import XMPPHuellasDialog
        dlg = XMPPHuellasDialog(self, acc, mi_jid, _("Mis dispositivos"), es_muc=False, propio=True)
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_agregar_contacto(self):
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return
        from app.services.xmpp.xmpp_service import xmpp_service
        if not xmpp_service.esta_conectada(acc):
            speak(_("Conecta la cuenta de XMPP primero."), interrupt=True)
            return
        from .xmpp_dialogs import XMPPAgregarContactoDialog
        dlg = XMPPAgregarContactoDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_dialogo_muc(self, cual):
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return
        from app.services.xmpp.xmpp_service import xmpp_service
        if not xmpp_service.esta_conectada(acc):
            speak(_("Conecta la cuenta de XMPP primero."), interrupt=True)
            return
        from .xmpp_dialogs import XMPPUnirseSalaDialog, XMPPDirectorioSalasDialog
        dlg = (XMPPUnirseSalaDialog(self, acc) if cual == "unirse"
               else XMPPDirectorioSalasDialog(self, acc))
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_buscar_contacto(self):
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return
        from .xmpp_dialogs import XMPPBuscarContactoDialog
        dlg = XMPPBuscarContactoDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_pasarelas(self):
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return
        from app.services.xmpp.xmpp_service import xmpp_service
        if not xmpp_service.esta_conectada(acc):
            speak(_("Conecta la cuenta de XMPP primero."), interrupt=True)
            return
        from .xmpp_pasarelas_dialogs import XMPPPasarelasDialog
        dlg = XMPPPasarelasDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_estado(self, tipo):
        acc = self._xmpp_perfil_listo()
        if not acc:
            return
        from .xmpp_perfil_dialogs import XMPPEstadoDialog
        dlg = XMPPEstadoDialog(self, acc, tipo)
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_editar_vcard(self):
        acc = self._xmpp_perfil_listo()
        if not acc:
            return
        from app.core.task_manager import task_manager

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.mi_vcard_campos(acc)

        def _fin(valores, err):
            from .xmpp_perfil_dialogs import XMPPEditarVCardDialog
            dlg = XMPPEditarVCardDialog(self, acc, valores or {})
            dlg.ShowModal()
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _xmpp_buscar_usuarios(self):
        acc = self._xmpp_perfil_listo()
        if not acc:
            return
        from .accessible_controls import CustomTextEntryDialog
        from app.services.xmpp.xmpp_service import xmpp_service
        sugerido = "search." + (xmpp_service.dominio_servidor(acc) or "")
        d = CustomTextEntryDialog(self, _("Servicio de búsqueda del servidor (si no lo sabes, prueba "
                                          "el sugerido o 'vjud.tu-servidor'):"),
                                  _("Buscar usuarios"), value=sugerido)
        serv = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not serv:
            return
        from .xmpp_perfil_dialogs import XMPPBuscarUsuariosDialog
        dlg = XMPPBuscarUsuariosDialog(self, acc, serv)
        dlg.ShowModal()
        dlg.Destroy()

    def _xmpp_invisible(self, invisible):
        acc = self._xmpp_perfil_listo()
        if not acc:
            return
        from app.core.task_manager import task_manager

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return (xmpp_service.fijar_invisible(acc) if invisible
                    else xmpp_service.fijar_visible(acc))
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            (_("Ahora estás invisible.") if invisible else _("Ahora estás visible."))
            if not e else _("No se pudo: {0}").format(e)), pass_context=True)

    def _xmpp_perfil_listo(self):
        """Devuelve la cuenta si hay una conectada; si no, avisa y devuelve None."""
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return None
        from app.services.xmpp.xmpp_service import xmpp_service
        if not xmpp_service.esta_conectada(acc):
            speak(_("Conecta la cuenta de XMPP primero."), interrupt=True)
            return None
        return acc

    def _xmpp_fijar_presencia(self, show):
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return
        from app.core.task_manager import task_manager

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            from app.core.config_manager import config
            pres = config.get("xmpp", f"presence_{acc}", ["", ""]) or ["", ""]
            mensaje = pres[1] if len(pres) > 1 else ""
            xmpp_service.fijar_presencia(acc, show, mensaje)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Presencia actualizada.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)

    def _xmpp_mensaje_estado(self):
        acc = self._xmpp_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de XMPP."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        from app.core.config_manager import config
        pres = config.get("xmpp", f"presence_{acc}", ["", ""]) or ["", ""]
        actual = pres[1] if len(pres) > 1 else ""
        d = CustomTextEntryDialog(self, _("Mensaje de estado (vacío para quitarlo):"),
                                  _("Mensaje de estado"), value=actual)
        nuevo = d.GetValue() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if nuevo is None:
            return
        from app.core.task_manager import task_manager
        show = pres[0] if pres else ""

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.fijar_presencia(acc, show, nuevo.strip())
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Mensaje de estado actualizado.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def _on_xmpp_add_cuenta(self, event):
        from app.core.feature_flags import xmpp_libreria_instalada
        if not xmpp_libreria_instalada():
            notificar(_("La librería de XMPP (slixmpp) no está instalada."))
            return
        from .xmpp_dialogs import XMPPLoginDialog
        dlg = XMPPLoginDialog(self)
        acc_nueva = None
        if dlg.ShowModal() == wx.ID_OK:
            acc_nueva = getattr(dlg, "account_id", None)
            try:
                self._crear_menus()      # por si aparece/cambia el menú XMPP
            except Exception:
                pass
            try:
                self._cargar_navegacion()  # reconstruir el árbol para que aparezca la rama/cuenta
            except Exception:
                pass
        dlg.Destroy()
        # Conectar la cuenta recién añadida para que su roster aparezca enseguida.
        if acc_nueva and getattr(self, "xmpp_ctrl", None) is not None:
            try:
                self.xmpp_ctrl.conectar_cuenta(acc_nueva)
            except Exception:
                pass

    def _xmpp_arranque_autoconectar(self):
        """Autoconexión de XMPP al arrancar (llamado desde el arranque de la app)."""
        try:
            if getattr(self, "xmpp_ctrl", None) is not None:
                self.xmpp_ctrl.conectar_cuentas_auto()
        except Exception as e:
            log.debug(f"XMPP: autoconexión no lanzada: {e}")
