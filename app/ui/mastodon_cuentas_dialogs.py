# -*- coding: utf-8 -*-
"""Diálogos de gestión de cuentas y asistente OAuth de Mastodon.
"""
import re
import wx
import wx.adv
from app.core.privacy import abrir_url

from app.core.speech import speak, notificar
from app.core.database import db
from app.core.task_manager import task_manager
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog, confirmar


class CuentasDialog(wx.Dialog):
    """Diálogo para listar, añadir y eliminar cuentas de Mastodon y RSS."""
    
    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Administrar Cuentas"),
            size=(550, 420),
            style=wx.DEFAULT_DIALOG_STYLE,
            name=_("Diálogo cuentas")
        )
        self._init_ui()
        self._cargar_cuentas()
        self.CenterOnParent()
        # Alt+F4 / botón de cerrar deben cerrar SOLO este diálogo, no la app
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _on_close(self, event):
        self.EndModal(wx.ID_CANCEL)

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("Cuentas registradas (Mastodon, Bluesky, RSS y Telegram):")), 0, wx.ALL, 5)
        
        self.lista_cuentas = AccessibleListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de cuentas registradas")
        )
        self.lista_cuentas.InsertColumn(0, _("Nombre / Instancia"), width=250)
        self.lista_cuentas.InsertColumn(1, _("Servicio"), width=120)
        self.lista_cuentas.InsertColumn(2, _("Estado"), width=100)
        
        sizer.Add(self.lista_cuentas, 1, wx.EXPAND | wx.ALL, 5)
        
        # Acciones
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.btn_add = wx.Button(self, label=_("&Añadir Cuenta..."), name=_("Añadir una nueva cuenta"))
        self.btn_add.Bind(wx.EVT_BUTTON, self._on_add_account_click)
        btn_sizer.Add(self.btn_add, 0, wx.ALL, 5)

        self.btn_rename = wx.Button(self, label=_("&Renombrar"), name=_("Renombrar cuenta seleccionada"))
        self.btn_rename.Bind(wx.EVT_BUTTON, self._on_renombrar)
        btn_sizer.Add(self.btn_rename, 0, wx.ALL, 5)

        self.btn_delete = wx.Button(self, wx.ID_DELETE, _("&Eliminar"), name=_("Eliminar cuenta seleccionada"))
        self.btn_delete.Bind(wx.EVT_BUTTON, self._on_eliminar)
        btn_sizer.Add(self.btn_delete, 0, wx.ALL, 5)

        # Reautorizar una cuenta de Bluesky o de W Social cuya sesión caducó/fue revocada, SIN
        # borrarla (auditoría Bluesky 9.2): conserva el DID, los buffers y toda la caché local.
        self.btn_reauth_bsky = wx.Button(
            self, label=_("&Volver a autorizar (Bluesky o W Social)"),
            name=_("Volver a autorizar la cuenta de Bluesky o W Social seleccionada"))
        self.btn_reauth_bsky.Bind(wx.EVT_BUTTON, self._on_reautorizar_bluesky)
        btn_sizer.Add(self.btn_reauth_bsky, 0, wx.ALL, 5)

        # Reimportar cookies de la cuenta de X seleccionada (sin borrarla ni rehacerla).
        try:
            from app.core.feature_flags import x_cliente_disponible
            if x_cliente_disponible():
                self.btn_reimport_x = wx.Button(self, label=_("Reimportar coo&kies de X"),
                                                name=_("Reimportar cookies de la cuenta de X seleccionada"))
                self.btn_reimport_x.Bind(wx.EVT_BUTTON, self._on_reimportar_x)
                btn_sizer.Add(self.btn_reimport_x, 0, wx.ALL, 5)
        except Exception:
            pass

        btn_sizer.AddStretchSpacer()

        btn_sizer.Add(wx.Button(self, wx.ID_OK, _("Acep&tar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        self.SetSizer(sizer)

    def _cargar_cuentas(self):
        self.lista_cuentas.DeleteAllItems()
        self.lista_cuentas.limpiar_lecturas()
        self.cuentas_rows = db.query("SELECT id, name, service_id, active FROM accounts")
        
        for idx, r in enumerate(self.cuentas_rows):
            name = r[1]
            service = r[2].upper()
            act = _("Activa") if r[3] else _("Inactiva")
            pos = self.lista_cuentas.InsertItem(idx, name)
            self.lista_cuentas.SetItem(pos, 1, service)
            self.lista_cuentas.SetItem(pos, 2, act)
            # Sin alt-speech: las columnas (Nombre, Servicio, Estado) ya las lee el lector. Añadir
            # aquí un establecer_lectura provocaba DOBLE lectura (NVDA + nuestra voz).

    def _on_add_account_click(self, event):
        # Desplegar un menú contextual para seleccionar el tipo de cuenta. Solo se ofrecen los
        # servicios ACTIVOS: un servicio desactivado por el usuario no deja añadir cuentas.
        from app.core.feature_flags import servicio_habilitado
        menu = wx.Menu()
        alguno = False
        if servicio_habilitado("mastodon"):
            item_mastodon = menu.Append(wx.ID_ANY, _("Cuenta de Mastodon"))
            self.Bind(wx.EVT_MENU, self._on_add_mastodon, item_mastodon)
            alguno = True
        if servicio_habilitado("bluesky"):
            item_bluesky = menu.Append(wx.ID_ANY, _("Cuenta de Bluesky"))
            self.Bind(wx.EVT_MENU, self._on_add_bluesky, item_bluesky)
            alguno = True
        if servicio_habilitado("rss"):
            item_rss = menu.Append(wx.ID_ANY, _("Cuenta RSS"))
            self.Bind(wx.EVT_MENU, self._on_add_rss, item_rss)
            alguno = True
        if servicio_habilitado("telegram"):
            item_telegram = menu.Append(wx.ID_ANY, _("Cuenta de Telegram"))
            self.Bind(wx.EVT_MENU, self._on_add_telegram, item_telegram)
            alguno = True
        if servicio_habilitado("x"):
            item_x = menu.Append(wx.ID_ANY, _("Cuenta de X (Twitter) [no oficial]"))
            self.Bind(wx.EVT_MENU, self._on_add_x, item_x)
            alguno = True
        if servicio_habilitado("irc"):
            item_irc = menu.Append(wx.ID_ANY, _("Red IRC"))
            self.Bind(wx.EVT_MENU, self._on_add_irc, item_irc)
            alguno = True
        if servicio_habilitado("matrix"):
            item_mx = menu.Append(wx.ID_ANY, _("Cuenta de Matrix"))
            self.Bind(wx.EVT_MENU, self._on_add_matrix, item_mx)
            alguno = True
        if servicio_habilitado("xmpp"):
            item_xmpp = menu.Append(wx.ID_ANY, _("Cuenta de XMPP (Jabber)"))
            self.Bind(wx.EVT_MENU, self._on_add_xmpp, item_xmpp)
            alguno = True
        if servicio_habilitado("reddit"):
            item_reddit = menu.Append(wx.ID_ANY, _("Cuenta de Reddit"))
            self.Bind(wx.EVT_MENU, self._on_add_reddit, item_reddit)
        if servicio_habilitado("peertube"):
            item_peertube = menu.Append(wx.ID_ANY, _("Cuenta de PeerTube"))
            self.Bind(wx.EVT_MENU, self._on_add_peertube, item_peertube)
            alguno = True
        if servicio_habilitado("wsocial"):
            item_wsocial = menu.Append(wx.ID_ANY, _("Cuenta de W Social"))
            self.Bind(wx.EVT_MENU, self._on_add_wsocial, item_wsocial)
            alguno = True

        if not alguno:
            notificar(_("No hay servicios activos. Actívalos en Preferencias para poder añadir cuentas."))
            menu.Destroy()
            return

        self.PopupMenu(menu)
        menu.Destroy()

    def _on_add_x(self, event):
        from app.core.feature_flags import x_cliente_disponible
        if not x_cliente_disponible():
            notificar(_("El cliente de X no está disponible."))
            return
        from .x_views import XLoginDialog, AVISO_RIESGOS
        # Aviso de riesgos OBLIGATORIO antes de nada.
        if not confirmar(self, AVISO_RIESGOS, _("Cliente de X (Twitter) - riesgos")):
            return
        from app.services.x import x_service
        dlg = XLoginDialog(self)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        metodo, datos = dlg.resultado
        dlg.Destroy()
        speak(_("Iniciando sesión en X..."), interrupt=True)

        def _bg(ctx):
            if metodo == "navegador":
                cookies = x_service.cookies_desde_navegador(datos)
                return x_service.login_con_cookies(cookies)
            if metodo == "cookies":
                return x_service.login_con_cookies(datos)
            usuario, password, totp = datos
            return x_service.login_programatico(usuario, password, totp)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo iniciar sesión en X: {0}").format(err))
            else:
                self._cargar_cuentas()
                notificar(_("Cuenta de X añadida: @{0}").format(res.get("screen_name", "")))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_add_irc(self, event):
        from app.core.feature_flags import irc_libreria_instalada
        if not irc_libreria_instalada():
            notificar(_("La librería de IRC (pydle) no está instalada. Instale las dependencias desde build.bat."))
            return
        from .irc_dialogs import IRCNetworkDialog
        dlg = IRCNetworkDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_cuentas()
        dlg.Destroy()

    def _on_add_matrix(self, event):
        from app.core.feature_flags import matrix_libreria_instalada
        if not matrix_libreria_instalada():
            notificar(_("La librería de Matrix (matrix-nio) no está instalada. Instale las dependencias desde build.bat."))
            return
        from .matrix_dialogs import MatrixLoginDialog
        dlg = MatrixLoginDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_cuentas()
        dlg.Destroy()

    def _on_add_xmpp(self, event):
        from app.core.feature_flags import xmpp_libreria_instalada
        if not xmpp_libreria_instalada():
            notificar(_("La librería de XMPP (slixmpp) no está instalada. Instale las dependencias desde build.bat."))
            return
        from .xmpp_dialogs import XMPPLoginDialog
        dlg = XMPPLoginDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_cuentas()
        dlg.Destroy()

    def _on_add_telegram(self, event):
        from app.services.telegram.telegram_service import telegram_service
        from app.services.telegram import telegram_service as _tgmod
        if not _tgmod.HAS_KURIGRAM:
            notificar(_("La librería de Telegram no está instalada. Instale las dependencias desde build.bat."))
            return
        if not telegram_service.credenciales_configuradas():
            notificar(_("Configure primero api_id y api_hash en Preferencias, pestaña Telegram."))
            return
        from .telegram_views import TelegramLoginDialog
        dlg = TelegramLoginDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_cuentas()
        dlg.Destroy()

    def _on_add_reddit(self, event):
        from app.core.feature_flags import reddit_libreria_instalada
        if not reddit_libreria_instalada():
            notificar(_("La librería de Reddit (praw) no está instalada. Instale las dependencias desde build.bat."))
            return
        from app.services.reddit import reddit_service
        # Patrón Telegram: el client id (identificador de aplicación) se configura una vez en
        # Preferencias → Reddit. Cada usuario usa el suyo. Si falta, se avisa (no se abre nada).
        if not reddit_service.client_id_configurado():
            notificar(_("Configure primero su identificador de aplicación (client id) en "
                        "Preferencias, sección Reddit. Se obtiene en reddit.com/prefs/apps creando "
                        "una 'installed app' con la URL de redirección http://localhost:8080."))
            return
        speak(_("Se abrirá el navegador para autorizar el acceso a Reddit; inicia sesión y aprueba."),
              interrupt=True)

        def _progreso(msg):
            wx.CallAfter(speak, msg, interrupt=True)

        def _bg(ctx):
            return reddit_service.iniciar_alta_oauth(progreso=_progreso)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo añadir la cuenta de Reddit: {0}").format(err))
            else:
                self._cargar_cuentas()
                notificar(_("Cuenta de Reddit añadida: u/{0}").format(res.get("username", "")))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_add_wsocial(self, event):
        """Alta de una cuenta de W Social. El asistente resuelve la identidad del usuario para
        saber en qué servidor vive su cuenta antes de pedirle la contraseña."""
        from app.core.feature_flags import wsocial_libreria_instalada
        if not wsocial_libreria_instalada():
            notificar(_("Falta la biblioteca atproto, necesaria para W Social."))
            return
        from .wsocial_dialogs import WSocialLoginDialog
        dlg = WSocialLoginDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_cuentas()
        dlg.Destroy()

    def _on_reautorizar_wsocial(self, acc_id):
        """Renueva la sesión de una cuenta de W Social conservando sus datos locales."""
        from .wsocial_dialogs import WSocialLoginDialog
        dlg = WSocialLoginDialog(self, account_id=acc_id)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_cuentas()
        dlg.Destroy()

    def _on_add_peertube(self, event):
        """Alta de una cuenta de PeerTube: pide instancia/usuario/contraseña e inicia sesión. Si la
        cuenta tiene verificación en dos pasos, pide el código y reintenta."""
        from app.core.feature_flags import peertube_libreria_instalada
        if not peertube_libreria_instalada():
            notificar(_("La librería HTTP (httpx) no está disponible. Instale las dependencias desde build.bat."))
            return
        from .peertube_dialogs import PeerTubeAltaDialog
        dlg = PeerTubeAltaDialog(self)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        instancia, usuario, password = dlg.resultado
        dlg.Destroy()
        self._alta_peertube(instancia, usuario, password, otp=None)

    def _alta_peertube(self, instancia, usuario, password, otp=None):
        """Ejecuta el alta en segundo plano; si falta el 2FA, pide el código y reintenta."""
        from app.services.peertube import peertube_service
        from app.services.peertube.peertube_auth import DosFactoresRequerido
        speak(_("Iniciando sesión en PeerTube..."), interrupt=True)

        def _progreso(msg):
            wx.CallAfter(speak, msg, interrupt=True)

        def _bg(ctx):
            return peertube_service.iniciar_alta(instancia, usuario, password, otp=otp,
                                                 progreso=_progreso)

        def _finish(res, err):
            if isinstance(err, DosFactoresRequerido):
                from .accessible_controls import CustomTextEntryDialog
                d = CustomTextEntryDialog(self, _("Introduce el código de verificación en dos pasos:"),
                                          _("Verificación en dos pasos"))
                if d.ShowModal() == wx.ID_OK:
                    codigo = d.GetValue().strip()
                    d.Destroy()
                    if codigo:
                        self._alta_peertube(instancia, usuario, password, otp=codigo)
                        return
                else:
                    d.Destroy()
                notificar(_("Alta de PeerTube cancelada."))
                return
            if err:
                notificar(_("No se pudo añadir la cuenta de PeerTube: {0}").format(err))
            else:
                self._cargar_cuentas()
                notificar(_("Cuenta de PeerTube añadida: {0}@{1}").format(
                    res.get("username", ""), res.get("instancia", "")))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_add_mastodon(self, event):
        dlg = OAuthWizardDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_cuentas()
        dlg.Destroy()

    def _on_add_bluesky(self, event):
        from app.services.bluesky import bluesky_service, HAS_ATPROTO
        from .bluesky_views import BlueskyLoginDialog
        if not HAS_ATPROTO:
            notificar(_("La biblioteca atproto no está instalada."))
            return
        dlg = BlueskyLoginDialog(self)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        handle, pwd, pds = dlg.handle, dlg.app_password, dlg.pds_url
        dlg.Destroy()
        speak(_("Iniciando sesión en Bluesky..."), interrupt=True)

        def _bg(ctx):
            return bluesky_service.completar_login(handle, pwd, pds)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo iniciar sesión: {0}").format(err))
            else:
                self._cargar_cuentas()
                notificar(_("Cuenta de Bluesky añadida con éxito."))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_add_rss(self, event):
        dlg = CustomTextEntryDialog(
            self,
            _("Introduzca el nombre para la nueva cuenta RSS (ej: Noticias, Tecnología):"),
            _("Añadir Cuenta RSS"),
            value=_("Mis Noticias")
        )
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue().strip()
            if name:
                db.execute(
                    "INSERT INTO accounts (name, service_id, active) VALUES (?, 'rss', 1)",
                    (name,)
                )
                self._cargar_cuentas()
                notificar(_("Cuenta RSS '{name}' añadida con éxito.").format(name=name))
            else:
                notificar(_("El nombre de la cuenta no puede estar vacío."))
        dlg.Destroy()

    def _on_reautorizar_bluesky(self, event):
        """Renueva la sesión de la cuenta seleccionada SIN borrarla (9.2): conserva el
        account_id, el DID, los buffers y la caché. Verifica que la nueva sesión sea de la
        misma cuenta. Vale para Bluesky y para W Social, que usan el mismo protocolo y
        pierden la autorización de la misma manera."""
        idx = self.lista_cuentas.GetFocusedItem()
        if idx < 0 or idx >= len(self.cuentas_rows):
            notificar(_("Selecciona de la lista la cuenta de Bluesky o de W Social."))
            return
        acc_id, service_id = self.cuentas_rows[idx][0], self.cuentas_rows[idx][2]
        if service_id == "wsocial":
            self._on_reautorizar_wsocial(acc_id)
            return
        if service_id != "bluesky":
            notificar(_("Esta opción es solo para cuentas de Bluesky o de W Social."))
            return
        from app.services.bluesky import bluesky_service, HAS_ATPROTO
        if not HAS_ATPROTO:
            notificar(_("La biblioteca atproto no está instalada."))
            return
        from .bluesky_views import BlueskyLoginDialog
        dlg = BlueskyLoginDialog(self)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        handle, pwd, pds = dlg.handle, dlg.app_password, dlg.pds_url
        dlg.Destroy()
        speak(_("Renovando la sesión de Bluesky..."), interrupt=True)

        def _bg(ctx):
            return bluesky_service.reautenticar(acc_id, handle, pwd, pds)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo volver a autorizar: {0}").format(err))
            else:
                self._cargar_cuentas()
                notificar(_("Cuenta @{0} vuelta a autorizar; se conservan tus buffers y datos.").format(
                    (res or {}).get("handle", "")))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_reimportar_x(self, event):
        """Reimporta las cookies de la cuenta de X seleccionada (sin borrarla)."""
        idx = self.lista_cuentas.GetFocusedItem()
        if idx < 0 or idx >= len(self.cuentas_rows):
            notificar(_("Selecciona la cuenta de X de la lista."))
            return
        acc_id, _nombre, service_id = self.cuentas_rows[idx][0], self.cuentas_rows[idx][1], self.cuentas_rows[idx][2]
        if service_id != "x":
            notificar(_("Esta opción es solo para cuentas de X (Twitter)."))
            return
        from .x_views import XLoginDialog
        from app.services.x import x_service
        dlg = XLoginDialog(self)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        metodo, datos = dlg.resultado
        dlg.Destroy()
        speak(_("Reimportando cookies de X..."), interrupt=True)

        def _bg(ctx):
            if metodo == "navegador":
                return x_service.reimportar_desde_navegador(acc_id, datos)
            if metodo == "cookies":
                return x_service.reimportar_cookies(acc_id, datos)
            usuario, password, totp = datos
            cookies = x_service.obtener_cookies_login(usuario, password, totp)
            return x_service.reimportar_cookies(acc_id, cookies)

        def _finish(res, err):
            if err:
                notificar(_("No se pudieron reimportar las cookies: {0}").format(err))
            else:
                self._cargar_cuentas()
                notificar(_("Cookies de X reimportadas. Ya puedes usar la cuenta @{0}.").format(
                    res.get("screen_name", "")))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_renombrar(self, event):
        idx = self.lista_cuentas.GetFocusedItem()
        if idx < 0 or idx >= len(self.cuentas_rows):
            speak(_("Seleccione una cuenta de la lista para renombrar."), interrupt=True)
            return
            
        acc_id = self.cuentas_rows[idx][0]
        acc_name = self.cuentas_rows[idx][1]
        
        dlg = CustomTextEntryDialog(
            self,
            _("Introduzca el nuevo nombre para la cuenta:"),
            _("Renombrar Cuenta"),
            value=acc_name
        )
        if dlg.ShowModal() == wx.ID_OK:
            new_name = dlg.GetValue().strip()
            if new_name:
                db.execute("UPDATE accounts SET name = ? WHERE id = ?", (new_name, acc_id))
                self._cargar_cuentas()
                self.lista_cuentas.SetItemState(idx, wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED, wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED)
                notificar(_("Cuenta renombrada con éxito a '{name}'.").format(name=new_name))
            else:
                notificar(_("El nombre de la cuenta no puede estar vacío."))
        dlg.Destroy()

    def _on_eliminar(self, event):
        idx = self.lista_cuentas.GetFocusedItem()
        if idx < 0 or idx >= len(self.cuentas_rows):
            return
            
        acc_id = self.cuentas_rows[idx][0]
        acc_name = self.cuentas_rows[idx][1]
        service_id = self.cuentas_rows[idx][2]
        
        dlg = wx.MessageDialog(
            self,
            _("¿Seguro que desea eliminar la cuenta '{name}'? Se borrarán todos sus feeds, artículos y configuraciones asociadas.").format(name=acc_name),
            _("Confirmar eliminación"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
        )
        if dlg.ShowModal() == wx.ID_YES:
            if service_id == "mastodon":
                mastodon_service.eliminar_cuenta(acc_id)
            elif service_id == "bluesky":
                from app.services.bluesky import bluesky_service
                bluesky_service.eliminar_cuenta(acc_id)
            elif service_id == "telegram":
                from app.services.telegram.telegram_service import telegram_service
                def _bg(ctx):
                    return telegram_service.cerrar_sesion(acc_id)
                def _fin_tg(revocada, err):
                    # TG-022: si no se revocó en el servidor, avisar de que la sesión sigue activa.
                    if not err and revocada is False:
                        notificar(_("La cuenta se quitó de este equipo, pero NO se pudo cerrar la "
                                    "sesión en Telegram. Revócala desde otro dispositivo en "
                                    "Ajustes, Dispositivos."))
                task_manager.start_task(_bg, callback=_fin_tg, pass_context=True)
            elif service_id == "x":
                from app.services.x import x_service
                x_service.eliminar_cuenta(acc_id)
            elif service_id == "irc":
                from app.services.irc import irc_service
                irc_service.eliminar_cuenta(acc_id)
            elif service_id == "matrix":
                from app.services.matrix import matrix_service
                def _bg_mx(ctx):
                    matrix_service.eliminar_cuenta(acc_id)
                    return True
                task_manager.start_task(_bg_mx, pass_context=True)
            elif service_id == "reddit":
                from app.services.reddit import reddit_service
                reddit_service.eliminar_cuenta(acc_id)
            elif service_id == "wsocial":
                from app.services.wsocial import wsocial_service
                def _bg_ws(ctx):
                    # Se intenta revocar la sesión en el servidor antes de olvidarla aquí.
                    wsocial_service.cerrar_sesion_servidor(acc_id)
                    wsocial_service.eliminar_cuenta(acc_id)
                    return True
                task_manager.start_task(_bg_ws, pass_context=True)
            elif service_id == "peertube":
                from app.services.peertube import peertube_service
                def _bg_pt(ctx):
                    peertube_service.eliminar_cuenta(acc_id)
                    return True
                task_manager.start_task(_bg_pt, pass_context=True)
            else:
                db.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))
            self._cargar_cuentas()
            notificar(_("Cuenta eliminada con éxito."))
        dlg.Destroy()


class OAuthWizardDialog(wx.Dialog):
    """Asistente para inicio de sesión en Mastodon mediante OAuth."""
    
    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Asistente de Inicio de Sesión Mastodon"),
            size=(500, 380),
            style=wx.DEFAULT_DIALOG_STYLE,
            name=_("Diálogo asistente de login")
        )
        
        self.client_id = None
        self.client_secret = None
        self.instance_url = None
        
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 1. Dirección instancia
        sizer.Add(wx.StaticText(self, label=_("1. Servidor / Instancia (ej: mastodon.social o comunidad.nvda.es):")), 0, wx.ALL, 5)
        self.txt_instance = AccessibleTextCtrl(
            self,
            value="mastodon.social",
            name=_("Dirección del servidor"),
            ayuda_texto=_("Dirección web del servidor Mastodon donde tiene su cuenta de usuario.")
        )
        sizer.Add(self.txt_instance, 0, wx.EXPAND | wx.ALL, 5)

        # Permisos de MODERACIÓN/ADMINISTRACIÓN: por defecto NO se piden (la mayoría no es staff y
        # algunas instancias rechazan autorizar con scopes admin a quien no lo es). Si marcas esto,
        # se piden 'admin:read' y 'admin:write' además de los normales, para poder ver reportes y
        # gestionar la instancia. Solo funciona si de verdad eres moderador/admin de ese servidor.
        self.cb_admin = wx.CheckBox(
            self, label=_("Soy moderador o administrador de esta instancia (pedir permisos de moderación)"),
            name=_("Pedir permisos de moderación/administración"))
        self.cb_admin.SetToolTip(_(
            "Márcalo solo si eres STAFF de este servidor. Pide los permisos admin:read/admin:write para "
            "ver y gestionar reportes, cuentas y estadísticas del servidor. Si ya tienes la cuenta "
            "añadida sin estos permisos, tendrás que volver a añadirla con esta casilla marcada."))
        sizer.Add(self.cb_admin, 0, wx.ALL, 5)

        self.btn_auth_link = wx.Button(self, label=_("&Registrar App y Obtener Código"), name=_("Generar enlace de autorización"))
        self.btn_auth_link.Bind(wx.EVT_BUTTON, self._on_obtener_codigo)
        sizer.Add(self.btn_auth_link, 0, wx.ALL, 5)
        
        sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.ALL, 10)
        
        # 2. Código de autorización
        sizer.Add(wx.StaticText(self, label=_("2. Pegue aquí el Código de Autorización obtenido en su navegador:")), 0, wx.ALL, 5)
        self.txt_auth_code = AccessibleTextCtrl(
            self,
            name=_("Código de autorización OAuth"),
            ayuda_texto=_("Pegue el código devuelto por el servidor Mastodon tras aceptar los permisos en el navegador.")
        )
        sizer.Add(self.txt_auth_code, 0, wx.EXPAND | wx.ALL, 5)
        
        # Botones finales
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.btn_completar = wx.Button(self, wx.ID_OK, _("&Completar Registro"), name=_("Finalizar login"))
        self.btn_completar.Bind(wx.EVT_BUTTON, self._on_completar_login)
        btn_sizer.Add(self.btn_completar, 0, wx.ALL, 5)

        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)

    def _on_obtener_codigo(self, event):
        self.instance_url = self.txt_instance.GetValue().strip()
        if not self.instance_url:
            return
            
        speak(_("Conectando con el servidor para registrar la aplicación..."), interrupt=True)
        # Los scopes se fijan AHORA (según la casilla) y se reutilizan en el paso 2: deben ser los
        # MISMOS en create_app, en la URL de autorización y en el intercambio del código.
        self._scopes = ["read", "write", "follow"]
        if self.cb_admin.GetValue():
            self._scopes += ["admin:read", "admin:write"]

        def _bg_register(ctx):
            return mastodon_service.registrar_aplicacion(self.instance_url, scopes=self._scopes)
            
        def _finish(res, err):
            if err:
                notificar(f"Error de conexión: {err}")
            else:
                self.client_id, self.client_secret, auth_url = res
                abrir_url(auth_url)
                speak(_("Se ha abierto el navegador web para que autorice los permisos de la aplicación. Tras aceptar, copie el código y péguelo en el paso 2."), interrupt=True)
                # Enfocar el campo del código
                self.txt_auth_code.SetFocus()
                
        task_manager.start_task(_bg_register, callback=_finish, pass_context=True)

    def _on_completar_login(self, event):
        code = self.txt_auth_code.GetValue().strip()
        if not code or not self.client_id:
            speak(_("Complete primero el paso 1 y copie el código."), interrupt=True)
            return
            
        speak(_("Verificando credenciales de acceso..."), interrupt=True)
        
        def _bg_login(ctx):
            return mastodon_service.completar_inicio_sesion(
                self.instance_url,
                self.client_id,
                self.client_secret,
                code,
                scopes=getattr(self, "_scopes", ["read", "write", "follow"])
            )
            
        def _finish(res, err):
            if err:
                notificar(f"Error al iniciar sesión: {err}")
            else:
                notificar(_("Inicio de sesión completado con éxito. Bienvenido: ") + res["username"])
                self.EndModal(wx.ID_OK)
                
        task_manager.start_task(_bg_login, callback=_finish, pass_context=True)


# Conteo de caracteres EXACTAMENTE como Mastodon (StatusLengthValidator del repositorio
# oficial): incluye el texto de la advertencia de contenido (CW); cada URL http(s) cuenta como
# 23 caracteres, y cada mención cuenta solo como @usuario (sin el @dominio). Así el contador
# coincide con lo que el servidor acepta.
# 500 es solo el DEFAULT: el límite real se lee de cada instancia (puede ser mayor) y el
# compositor usa self._max_chars (ver ComposeTootDialog).
MASTODON_MAX_CHARS = 500
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
# Mención: @usuario con un @dominio opcional (se descarta el dominio al contar).
_MENTION_RE = re.compile(r'@(\w+)(?:@[\w.-]+)?')
