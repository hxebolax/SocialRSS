# -*- coding: utf-8 -*-
"""Diálogos del cliente Matrix: alta de cuenta (M1), miembros con moderación y
directorio público de salas (M5)."""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import (AccessibleTextCtrl, AccessibleListCtrl, mostrar_info,
                                  confirmar, CustomTextEntryDialog)


def _modo_experto_confianza() -> bool:
    """¿Está activado el MODO EXPERTO de confianza (config matrix.modo_experto_confianza)?

    Conceder confianza criptográfica SIN verificar (marcar un dispositivo de confianza "sin emojis"
    o firmar la identidad de un usuario por una simple advertencia) permite que un dispositivo/identidad
    suplantado reciba claves de sala. Por defecto esas acciones NO aparecen: el camino normal es la
    verificación por emojis (SAS). El override manual queda reservado a este modo experto, y aun así
    exige la huella completa y doble confirmación."""
    try:
        from app.core.config_manager import config
        return bool(config.get("matrix", "modo_experto_confianza", False))
    except Exception:
        return False


class MatrixLoginDialog(wx.Dialog):
    """Alta de una cuenta de Matrix: homeserver + usuario + contraseña.
    El descubrimiento .well-known se hace solo (si el dominio delega en otro servidor)."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Añadir cuenta de Matrix"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Añadir cuenta de Matrix"))
        self.account_id = None
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(self, label=_("&Servidor (homeserver, ej: matrix.org):")), 0, wx.ALL, 6)
        self.txt_hs = AccessibleTextCtrl(self, value="matrix.org", name=_("Servidor de Matrix"),
                                         ayuda_texto=_("El servidor donde tienes la cuenta. Si tu "
                                                       "dominio delega en otro, se descubre solo."))
        s.Add(self.txt_hs, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        s.Add(wx.StaticText(self, label=_("&Usuario (ej: @nombre:matrix.org o solo nombre):")), 0, wx.ALL, 6)
        self.txt_user = AccessibleTextCtrl(self, name=_("Usuario de Matrix"))
        s.Add(self.txt_user, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        s.Add(wx.StaticText(self, label=_("&Contraseña:")), 0, wx.ALL, 6)
        self.txt_pass = AccessibleTextCtrl(self, style=wx.TE_PASSWORD, name=_("Contraseña de Matrix"),
                                           ayuda_texto=_("Solo se usa para iniciar sesión; se guarda "
                                                         "el token de sesión cifrado, no la contraseña."))
        s.Add(self.txt_pass, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        # Servidores modernos (Matrix Authentication Service, MAS): el inicio de sesión se hace en el
        # NAVEGADOR (no por contraseña) y SocialRSS lo detecta solo. En ese caso el usuario y la
        # contraseña de arriba se ignoran. La casilla pide, si eres admin, los permisos de
        # administración del servidor (el servidor solo los concede si de verdad lo eres).
        self.cb_admin = wx.CheckBox(self, label=_("Soy administrador del servidor (pedir permisos de "
                                                  "administración; solo servidores con navegador/MAS)"),
                                    name=_("Pedir permisos de administración del servidor"))
        s.Add(self.cb_admin, 0, wx.ALL, 8)
        s.Add(wx.StaticText(self, label=_(
            "Nota: si tu servidor usa inicio de sesión por navegador (MAS), se abrirá el navegador "
            "para autenticarte y el usuario y la contraseña de arriba se ignoran.")), 0, wx.ALL, 6)

        # Nota DINÁMICA según el estado real del cifrado (olm instalado o no).
        try:
            from app.services.matrix import matrix_service
            hay_cifrado = matrix_service.cifrado_disponible()
        except Exception:
            hay_cifrado = False
        if hay_cifrado:
            nota = _("El cifrado de extremo a extremo está ACTIVO: las salas y directos cifrados\n"
                     "(candado en otros clientes) funcionan. Para leer tu historial cifrado anterior,\n"
                     "importa las claves desde el menú Matrix.")
        else:
            nota = _("AVISO: el motor de cifrado (olm) no está instalado en esta copia, así que\n"
                     "las salas CIFRADAS no se podrán leer. Quien compila la aplicación debe\n"
                     "ejecutar una vez tools\\build_python_olm.bat.")
        s.Add(wx.StaticText(self, label=nota), 0, wx.ALL, 8)

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
        self.txt_hs.SetFocus()

    def _on_login(self, event):
        hs = self.txt_hs.GetValue().strip()
        usuario = self.txt_user.GetValue().strip()
        password = self.txt_pass.GetValue()
        pedir_admin = self.cb_admin.GetValue()
        if not hs:
            speak(_("Escribe al menos el servidor."), interrupt=True)
            return
        self.btn_ok.Disable()
        speak(_("Conectando con el servidor de Matrix..."), interrupt=True)

        def _prog(msg):
            wx.CallAfter(notificar, msg)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            # DETECCIÓN: ¿el servidor usa inicio de sesión por navegador (MAS/OAuth)? Si sí, ese es
            # el único método válido (la contraseña está deshabilitada); si no, login clásico.
            meta = None
            try:
                meta = matrix_service.descubrir_auth_navegador(hs)
            except Exception:
                meta = None
            if meta:
                return matrix_service.crear_cuenta_oauth(hs, pedir_admin=pedir_admin, progreso=_prog)
            if not usuario or not password:
                from app.services.matrix.matrix_common import MatrixError
                raise MatrixError(_("Este servidor pide usuario y contraseña; escríbelos."))
            return matrix_service.crear_cuenta(hs, usuario, password)

        def _fin(res, err):
            try:
                self.btn_ok.Enable()
            except Exception:
                pass
            if err:
                notificar(_("No se pudo iniciar sesión: {0}").format(err))
                return
            self.account_id = res
            notificar(_("Cuenta de Matrix añadida."))
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixMiembrosDialog(wx.Dialog):
    """Miembros de una sala con su rango EN PALABRAS (administrador/a, moderador/a...) y
    acciones de moderación por usuario (el servidor valida los permisos reales)."""

    def __init__(self, parent, account_id, room_id, nombre_sala):
        super().__init__(parent, title=_("Miembros de {0}").format(nombre_sala),
                         size=(560, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Miembros de la sala"))
        self.account_id = account_id
        self.room_id = room_id
        self.miembros = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Miembros (Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de miembros"))
        self.lista.InsertColumn(0, _("Nombre"), width=220)
        self.lista.InsertColumn(1, _("Rango"), width=140)
        self.lista.InsertColumn(2, _("Usuario"), width=180)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_inv = wx.Button(self, label=_("&Invitar a un usuario..."))
        btn_inv.Bind(wx.EVT_BUTTON, self._on_invitar)
        fila.Add(btn_inv, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        # La lista de miembros se pide al servidor (/joined_members), así que se hace en segundo
        # plano para no bloquear la interfaz (antes se leía el estado local, que con lazy-loading
        # salía 0 hasta escribir).
        self.miembros = []
        self._mi_nivel = 0
        acc, room = self.account_id, self.room_id
        speak(_("Cargando miembros..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            miembros = matrix_service.miembros_con_rango(acc, room)
            yo = ""
            try:
                yo = matrix_service._user_id(acc) or ""
            except Exception:
                yo = ""
            mi_nivel = next((n for uid, _nm, _r, n in miembros if uid == yo), 0)
            return (miembros, mi_nivel)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudieron cargar los miembros: {0}").format(err or ""))
                return
            self.miembros, self._mi_nivel = res
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, (uid, nombre, rango, _nivel) in enumerate(self.miembros):
                self.lista.InsertItem(i, nombre)
                self.lista.SetItem(i, 1, rango or "")
                self.lista.SetItem(i, 2, uid)
                self.lista.establecer_lectura(i, f"{nombre}, {rango}" if rango else nombre)
            self.lbl.SetLabel(_("{0} miembros (Aplicaciones para acciones):").format(len(self.miembros)))
            if self.miembros:
                self.lista.Focus(0)
                self.lista.Select(0)

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.miembros[i] if 0 <= i < len(self.miembros) else None

    def _on_menu(self, event):
        m = self._actual()
        if not m:
            return
        uid, nombre, _rango, nivel = m
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Mensaje directo con {0}").format(nombre))
        self.Bind(wx.EVT_MENU, lambda e, u=uid: self._dm(u), it)
        it = menu.Append(wx.ID_ANY, _("Ver perfil y presencia"))
        self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._ver_perfil(u, n), it)
        # Firmar la identidad de otro usuario (cross-signing) es un override manual de confianza:
        # solo en modo experto (por defecto, la confianza se establece verificando por emojis).
        if _modo_experto_confianza():
            it = menu.Append(wx.ID_ANY, _("[Experto] Firmar la identidad de este usuario..."))
            self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._firmar_usuario(u, n), it)
        # Acciones de moderación: solo si mi nivel supera al del objetivo (regla habitual).
        if self._mi_nivel > nivel:
            menu.AppendSeparator()
            it = menu.Append(wx.ID_ANY, _("Cambiar el rango..."))
            self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._cambiar_rango(u, n), it)
            it = menu.Append(wx.ID_ANY, _("Expulsar de la sala..."))
            self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._moderar("expulsar", u, n), it)
            it = menu.Append(wx.ID_ANY, _("Banear..."))
            self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._moderar("banear", u, n), it)
            it = menu.Append(wx.ID_ANY, _("Quitar el baneo"))
            self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._moderar("desbanear", u, n, sin_confirmar=True), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _ver_perfil(self, uid, nombre):
        """Perfil (nombre visible) y presencia EN PALABRAS de un miembro (M7)."""
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            perfil = matrix_service.perfil_de(acc, uid)
            presencia = ""
            try:
                presencia = matrix_service.presencia_de(acc, uid)
            except Exception:
                pass
            return perfil, presencia

        def _fin(res, err):
            if err:
                notificar(_("No se pudo leer el perfil: {0}").format(err))
                return
            perfil, presencia = res
            lineas = [_("Usuario: {0}").format(uid),
                      _("Nombre visible: {0}").format(perfil.get("nombre") or _("(sin nombre)"))]
            if presencia:
                lineas.append(_("Presencia: {0}").format(presencia))
            if perfil.get("avatar_mxc"):
                lineas.append(_("Tiene avatar."))
            mostrar_info(self, "\n".join(lineas), _("Perfil de {0}").format(nombre))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _firmar_usuario(self, uid, nombre):
        """Firma la clave maestra de este usuario con nuestra user-signing key (cross-signing):
        hazlo SOLO después de haberte verificado con él por emojis. Requiere tener tu propia
        verificación cruzada configurada (menú Matrix). Acción de MODO EXPERTO: doble confirmación."""
        if not _modo_experto_confianza():
            mostrar_info(self, _("Para confiar en una persona, verifícala por emojis desde sus "
                                 "dispositivos (menú de la sala cifrada). Firmar su identidad a mano "
                                 "es una acción avanzada desactivada por seguridad."),
                         _("Verificación necesaria"))
            return
        if confirmar(self, _("MODO EXPERTO. Vas a FIRMAR la identidad de {0} ({1}).\n\nEsto solo es "
                             "seguro si YA te has verificado con esta persona por emojis y confías en "
                             "que su clave es la correcta. Firmar una identidad suplantada permitiría "
                             "que un atacante recibiera claves. ¿Seguro que quieres continuar?").format(
                                 nombre, uid),
                     _("Firmar identidad (experto)")) != wx.ID_YES:
            return
        if confirmar(self, _("Última confirmación: firmar la identidad de {0} es una decisión de "
                             "confianza permanente. ¿Continuar?").format(uid),
                     _("Confirmar firma")) != wx.ID_YES:
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.firmar_usuario(acc, uid)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Hecho: ahora confías en {0} por verificación cruzada.").format(nombre) if not e
            else _("No se pudo: {0}").format(e)), pass_context=True)

    def _cambiar_rango(self, uid, nombre):
        """Cambiar el rango (power level) de un miembro, en palabras (M7)."""
        rangos = ((0, _("Miembro")), (50, _("Moderador/a")), (100, _("Administrador/a")))
        dlg = wx.SingleChoiceDialog(self, _("Nuevo rango para {0}:").format(nombre),
                                    _("Cambiar rango"), [r[1] for r in rangos])
        idx = dlg.GetSelection() if dlg.ShowModal() == wx.ID_OK else -1
        dlg.Destroy()
        if idx < 0:
            return
        nivel = rangos[idx][0]
        if nivel >= 100 and confirmar(
                self, _("Hacer administrador/a a {0} NO se puede deshacer (tendrá tu mismo "
                        "poder). ¿Continuar?").format(nombre), _("Confirmar")) != wx.ID_YES:
            return
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.fijar_rango(acc, room, uid, nivel)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo cambiar el rango: {0}").format(err))
            else:
                notificar(_("Rango cambiado."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _dm(self, uid):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.crear_dm(acc, uid)

        def _fin(room_id, err):
            if err:
                notificar(_("No se pudo abrir el directo: {0}").format(err))
                return
            from .matrix_conversation import MatrixConversationDialog
            dlg = MatrixConversationDialog(self.GetParent(), acc, room_id, uid)
            dlg.ShowModal()
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_invitar(self, event):
        dlg = CustomTextEntryDialog(self, _("Usuario a invitar (ej: @nombre:matrix.org):"),
                                    _("Invitar a la sala"))
        uid = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if uid:
            self._moderar("invitar", uid, uid, sin_confirmar=True)

    def _moderar(self, accion, uid, nombre, sin_confirmar=False):
        motivo = ""
        if not sin_confirmar:
            etiquetas = {"expulsar": _("expulsar a"), "banear": _("banear a")}
            if confirmar(self, _("¿Seguro que quieres {0} {1}?").format(etiquetas.get(accion, accion), nombre),
                         _("Confirmar")) != wx.ID_YES:
                return
            d2 = CustomTextEntryDialog(self, _("Motivo (opcional):"), _("Motivo"))
            motivo = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
            d2.Destroy()
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.accion_moderacion(acc, room, uid, accion, motivo)
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


class MatrixDirectorioDialog(wx.Dialog):
    """Directorio público de salas del servidor, con filtro; Intro para unirse."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Directorio de salas"),
                         size=(680, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Directorio de salas de Matrix"))
        self.account_id = account_id
        self.salas = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Buscar (se pregunta al servidor):")), 0, wx.ALL, 6)
        fila_b = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_filtro = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Filtro de salas"))
        self.txt_filtro.Bind(wx.EVT_TEXT_ENTER, lambda e: self._descargar())
        fila_b.Add(self.txt_filtro, 1, wx.EXPAND | wx.ALL, 4)
        # M7: explorar el directorio de OTRO servidor federado (vacío = el propio).
        fila_b.Add(wx.StaticText(self, label=_("&En el servidor:")), 0,
                   wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        self.txt_server = AccessibleTextCtrl(
            self, style=wx.TE_PROCESS_ENTER, name=_("Servidor a explorar"),
            ayuda_texto=_("Vacío: tu servidor. Escribe otro (ej: matrix.org) para ver su directorio."))
        self.txt_server.Bind(wx.EVT_TEXT_ENTER, lambda e: self._descargar())
        fila_b.Add(self.txt_server, 1, wx.EXPAND | wx.ALL, 4)
        btn_buscar = wx.Button(self, label=_("Bu&scar"))
        btn_buscar.Bind(wx.EVT_BUTTON, lambda e: self._descargar())
        fila_b.Add(btn_buscar, 0, wx.ALL, 4)
        s.Add(fila_b, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
        self.lbl = wx.StaticText(self, label=_("Salas (Intro para unirte):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de salas públicas"))
        self.lista.InsertColumn(0, _("Sala"), width=220)
        self.lista.InsertColumn(1, _("Miembros"), width=80)
        self.lista.InsertColumn(2, _("Tema"), width=320)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_unirse)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_unirse = wx.Button(self, label=_("&Unirse a la sala"))
        btn_unirse.Bind(wx.EVT_BUTTON, self._on_unirse)
        fila.Add(btn_unirse, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._descargar()

    def _descargar(self):
        filtro = self.txt_filtro.GetValue().strip()
        server = self.txt_server.GetValue().strip()
        speak(_("Buscando salas..."), interrupt=True)
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.directorio_salas(acc, filtro, server=server)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo obtener el directorio: {0}").format(err))
                return
            self.salas = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, sala in enumerate(self.salas):
                self.lista.InsertItem(i, sala["nombre"])
                self.lista.SetItem(i, 1, str(sala["miembros"]))
                self.lista.SetItem(i, 2, (sala["tema"] or "").replace("\n", " "))
                self.lista.establecer_lectura(i, _("{0}, {1} miembros. {2}").format(
                    sala["nombre"], sala["miembros"], sala["tema"] or ""))
            self.lbl.SetLabel(_("{0} salas (Intro para unirte):").format(len(self.salas)))
            notificar(_("{0} salas.").format(len(self.salas)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_unirse(self, event):
        i = self.lista.GetFocusedItem()
        if i < 0 or i >= len(self.salas):
            return
        sala = self.salas[i]
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.unirse_sala(acc, sala["alias"] or sala["room_id"])
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Dentro de {0}.").format(sala["nombre"]) if not e
            else _("No se pudo entrar: {0}").format(e)), pass_context=True)


class MatrixPerfilDialog(wx.Dialog):
    """Mi perfil y estado (M7): nombre visible, avatar y presencia con mensaje de estado."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mi perfil y estado de Matrix"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Mi perfil y estado de Matrix"))
        self.account_id = account_id
        from app.services.matrix.matrix_avanzado import presencias_legibles
        self._presencias = presencias_legibles()
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Nombre visible:")), 0, wx.ALL, 6)
        self.txt_nombre = AccessibleTextCtrl(self, name=_("Nombre visible"))
        s.Add(self.txt_nombre, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        btn_avatar = wx.Button(self, label=_("Cambiar &avatar (elegir imagen)..."))
        btn_avatar.Bind(wx.EVT_BUTTON, self._on_avatar)
        s.Add(btn_avatar, 0, wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("&Presencia:")), 0, wx.ALL, 6)
        self.cbo_presencia = wx.Choice(self, choices=[p[1] for p in self._presencias],
                                       name=_("Presencia"))
        self.cbo_presencia.SetSelection(0)
        s.Add(self.cbo_presencia, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Mensaje de estado (opcional):")), 0, wx.ALL, 6)
        self.txt_estado = AccessibleTextCtrl(self, name=_("Mensaje de estado"))
        s.Add(self.txt_estado, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        # Zona horaria (perfil extendido MSC4133): editable, con la lista IANA y prerrellenada
        # con la del sistema para que la hora que otros ven coincida con la tuya.
        s.Add(wx.StaticText(self, label=_("&Zona horaria (así otros ven bien tu hora):")), 0, wx.ALL, 6)
        from app.services.matrix.matrix_avanzado import _AvanzadoMixin
        zonas = _AvanzadoMixin.zonas_horarias_comunes()
        self._tz_sistema = _AvanzadoMixin.zona_horaria_sistema()
        self.cbo_tz = wx.ComboBox(self, choices=[""] + zonas, style=wx.CB_DROPDOWN,
                                  name=_("Zona horaria"))
        s.Add(self.cbo_tz, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        btn_tz_sis = wx.Button(self, label=_("Usar la zona horaria de &este equipo"))
        btn_tz_sis.Bind(wx.EVT_BUTTON, lambda e: self.cbo_tz.SetValue(self._tz_sistema))
        s.Add(btn_tz_sis, 0, wx.ALL, 6)
        btn_campos = wx.Button(self, label=_("Más &campos del perfil (pronombres, web...)..."))
        btn_campos.Bind(wx.EVT_BUTTON, self._on_campos)
        s.Add(btn_campos, 0, wx.ALL, 6)
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
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return {"nombre": matrix_service.mi_perfil(acc).get("nombre") or "",
                    "tz": matrix_service.mi_zona_horaria(acc)}

        def _fin(perfil, err):
            if err or not perfil:
                return
            try:
                self.txt_nombre.SetValue(perfil.get("nombre") or "")
                from app.core.config_manager import config
                pres = config.get("matrix", f"presence_{acc}", ["online", ""]) or ["online", ""]
                for i, (clave, _et) in enumerate(self._presencias):
                    if clave == (pres[0] if pres else "online"):
                        self.cbo_presencia.SetSelection(i)
                self.txt_estado.SetValue(pres[1] if len(pres) > 1 else "")
                # La zona guardada, o la del sistema si aún no había ninguna, como sugerencia.
                self.cbo_tz.SetValue(perfil.get("tz") or self._tz_sistema or "")
            except Exception:
                pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_campos(self, event):
        dlg = MatrixCamposPerfilDialog(self, self.account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_avatar(self, event):
        dlg = wx.FileDialog(self, _("Elige la imagen del avatar"),
                            wildcard=_("Imágenes|*.png;*.jpg;*.jpeg;*.gif;*.webp"),
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        ruta = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not ruta:
            return
        acc = self.account_id
        speak(_("Subiendo avatar..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.cambiar_avatar(acc, ruta)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Avatar cambiado.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def _on_guardar(self, event):
        acc = self.account_id
        nombre = self.txt_nombre.GetValue().strip()
        idx = max(0, self.cbo_presencia.GetSelection())
        presencia = self._presencias[idx][0]
        estado = self.txt_estado.GetValue().strip()
        tz = self.cbo_tz.GetValue().strip()
        self.btn_ok.Disable()

        def _bg(ctx):
            from app.services.matrix import matrix_service
            if nombre:
                matrix_service.cambiar_nombre_visible(acc, nombre)
            matrix_service.fijar_presencia(acc, presencia, estado)
            try:
                matrix_service.fijar_zona_horaria(acc, tz)
            except Exception:
                pass  # el servidor puede no soportar perfil extendido: no es fatal
            return True

        def _fin(res, err):
            try:
                self.btn_ok.Enable()
            except Exception:
                pass
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
                return
            notificar(_("Perfil y estado guardados."))
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixCamposPerfilDialog(wx.Dialog):
    """Campos personalizados del perfil extendido de Matrix (MSC4133 / v1.16): pronombres,
    página web, un texto libre... Cada campo es una clave y un valor que el servidor guarda
    y otros clientes pueden mostrar. El nombre, el avatar y la zona horaria se editan en la
    pantalla anterior; aquí se ve todo lo demás y se pueden añadir claves propias."""

    OCULTAR = {"displayname", "avatar_url", "m.tz", "us.cloke.msc4175.tz"}

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Más campos del perfil de Matrix"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Más campos del perfil de Matrix"))
        self.account_id = account_id
        self.campos = []  # [(clave, valor_texto)]
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Campos del perfil (Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de campos del perfil"))
        self.lista.InsertColumn(0, _("Campo"), width=200)
        self.lista.InsertColumn(1, _("Valor"), width=320)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_add = wx.Button(self, label=_("&Añadir campo..."))
        btn_add.Bind(wx.EVT_BUTTON, self._on_añadir)
        fila.Add(btn_add, 0, wx.ALL, 5)
        btn_edit = wx.Button(self, label=_("&Editar valor..."))
        btn_edit.Bind(wx.EVT_BUTTON, self._on_editar)
        fila.Add(btn_edit, 0, wx.ALL, 5)
        btn_del = wx.Button(self, label=_("&Borrar campo..."))
        btn_del.Bind(wx.EVT_BUTTON, self._on_borrar)
        fila.Add(btn_del, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.leer_perfil_completo(acc)

        def _fin(perfil, err):
            if err:
                notificar(_("No se pudo leer el perfil: {0}").format(err))
                return
            self.campos = []
            for clave, valor in (perfil or {}).items():
                if clave in self.OCULTAR:
                    continue
                if isinstance(valor, (dict, list)):
                    import json
                    valor = json.dumps(valor, ensure_ascii=False)
                self.campos.append((clave, "" if valor is None else str(valor)))
            self.campos.sort(key=lambda t: t[0].lower())
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, (clave, valor) in enumerate(self.campos):
                self.lista.InsertItem(i, clave)
                self.lista.SetItem(i, 1, valor.replace("\n", " "))
                self.lista.establecer_lectura(i, f"{clave}: {valor}")
            self.lbl.SetLabel(_("{0} campos (Aplicaciones para acciones):").format(len(self.campos)))
            if self.campos:
                self.lista.Focus(0)
                self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.campos[i] if 0 <= i < len(self.campos) else None

    def _on_menu(self, event):
        if not self._actual():
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Editar valor..."))
        self.Bind(wx.EVT_MENU, lambda e: self._on_editar(e), it)
        it = menu.Append(wx.ID_ANY, _("Borrar campo..."))
        self.Bind(wx.EVT_MENU, lambda e: self._on_borrar(e), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _guardar_campo(self, clave, valor, aviso_ok):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.fijar_campo_perfil(acc, clave, valor)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
                return
            notificar(aviso_ok)
            self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_añadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Nombre del campo (p. ej. m.pronouns, web, sobre_mí):"),
                                    _("Añadir campo del perfil"))
        clave = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not clave:
            return
        if clave in self.OCULTAR:
            notificar(_("Ese campo se edita en la pantalla del perfil, no aquí."))
            return
        dlg2 = CustomTextEntryDialog(self, _("Valor de «{0}»:").format(clave),
                                     _("Valor del campo"))
        valor = dlg2.GetValue() if dlg2.ShowModal() == wx.ID_OK else None
        dlg2.Destroy()
        if valor is None:
            return
        self._guardar_campo(clave, valor.strip(), _("Campo añadido."))

    def _on_editar(self, event):
        campo = self._actual()
        if not campo:
            return
        clave, valor = campo
        dlg = CustomTextEntryDialog(self, _("Nuevo valor de «{0}»:").format(clave),
                                    _("Editar campo"), value=valor)
        nuevo = dlg.GetValue() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        if nuevo is None:
            return
        self._guardar_campo(clave, nuevo.strip(), _("Campo actualizado."))

    def _on_borrar(self, event):
        campo = self._actual()
        if not campo:
            return
        clave = campo[0]
        if confirmar(self, _("¿Borrar el campo «{0}» del perfil?").format(clave),
                     _("Confirmar")) != wx.ID_YES:
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.borrar_campo_perfil(acc, clave)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
                return
            notificar(_("Campo borrado."))
            self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixDispositivosDialog(wx.Dialog):
    """Mis dispositivos/sesiones de Matrix (M7): renombrar y cerrar sesiones con contraseña."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mis dispositivos de Matrix"), size=(620, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Mis dispositivos de Matrix"))
        self.account_id = account_id
        self.dispositivos = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Dispositivos:"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de dispositivos"))
        self.lista.InsertColumn(0, _("Nombre"), width=220)
        self.lista.InsertColumn(1, _("Identificador"), width=130)
        self.lista.InsertColumn(2, _("Última actividad"), width=140)
        self.lista.InsertColumn(3, _("IP"), width=110)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_ver = wx.Button(self, label=_("&Verificar por emojis..."))
        btn_ver.SetToolTip(_("Verifica ESTA sesión con OTRO dispositivo tuyo (por ejemplo tu "
                             "Element): aparecerán unos emojis para comparar en ambos."))
        btn_ver.Bind(wx.EVT_BUTTON, self._on_verificar)
        fila.Add(btn_ver, 0, wx.ALL, 5)
        btn_ren = wx.Button(self, label=_("&Renombrar..."))
        btn_ren.Bind(wx.EVT_BUTTON, self._on_renombrar)
        fila.Add(btn_ren, 0, wx.ALL, 5)
        btn_del = wx.Button(self, label=_("Cerrar la &sesión del dispositivo..."))
        btn_del.Bind(wx.EVT_BUTTON, self._on_eliminar)
        fila.Add(btn_del, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.mis_dispositivos(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron leer los dispositivos: {0}").format(err))
                return
            self.dispositivos = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, d in enumerate(self.dispositivos):
                nombre = d["nombre"] or d["id"]
                if d.get("actual"):
                    nombre += " " + _("(este dispositivo)")
                self.lista.InsertItem(i, nombre)
                self.lista.SetItem(i, 1, d["id"])
                self.lista.SetItem(i, 2, d["ultima_vez"])
                self.lista.SetItem(i, 3, d["ip"])
                self.lista.establecer_lectura(i, _("{0}. Última actividad {1}").format(
                    nombre, d["ultima_vez"] or _("desconocida")))
            self.lbl.SetLabel(_("{0} dispositivos:").format(len(self.dispositivos)))
            if self.dispositivos:
                self.lista.Focus(0)
                self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.dispositivos[i] if 0 <= i < len(self.dispositivos) else None

    def _on_verificar(self, event):
        """Inicia la verificación por emojis entre ESTE dispositivo y el seleccionado (otro tuyo).
        Los emojis para comparar aparecen solos (evento de verificación) en ambos clientes."""
        d = self._actual()
        if not d:
            return
        if d.get("actual"):
            notificar(_("Ese es este mismo dispositivo. Elige OTRA de tus sesiones (por ejemplo tu "
                        "Element o el móvil) para verificarla."))
            return
        acc, did = self.account_id, d["id"]
        nombre = d["nombre"] or d["id"]
        speak(_("Iniciando la verificación con {0}. En cuanto el otro dispositivo la acepte "
                "aparecerán los emojis para comparar.").format(nombre), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.verificar_mi_sesion(acc, did)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo iniciar la verificación: {0}").format(err))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_renombrar(self, event):
        d = self._actual()
        if not d:
            return
        dlg = CustomTextEntryDialog(self, _("Nuevo nombre del dispositivo:"),
                                    _("Renombrar dispositivo"), value=d["nombre"])
        nombre = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not nombre:
            return
        acc, did = self.account_id, d["id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.renombrar_dispositivo(acc, did, nombre)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Renombrado."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_eliminar(self, event):
        d = self._actual()
        if not d:
            return
        if d.get("actual"):
            notificar(_("Este es el dispositivo actual: no se puede cerrar desde aquí."))
            return
        if confirmar(self, _("¿Cerrar la sesión del dispositivo '{0}'? Dejará de recibir "
                             "mensajes y sus claves de cifrado se perderán.").format(
                                 d["nombre"] or d["id"]), _("Confirmar")) != wx.ID_YES:
            return
        dlg = CustomTextEntryDialog(self, _("Contraseña de la cuenta (el servidor la exige "
                                            "para esta operación):"),
                                    _("Cerrar sesión del dispositivo"), password=True)
        password = dlg.GetValue() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not password:
            return
        acc, did = self.account_id, d["id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.eliminar_dispositivos(acc, [did], password)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Sesión cerrada."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixAjustesSalaDialog(wx.Dialog):
    """Ajustes de la sala (M7): nombre, tema, alias, acceso, historial, cifrado y versión.
    El servidor valida los permisos reales (si no puedes, lo dice en palabras)."""

    def __init__(self, parent, account_id, room_id, nombre_sala):
        super().__init__(parent, title=_("Ajustes de la sala {0}").format(nombre_sala),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Ajustes de la sala"))
        self.account_id = account_id
        self.room_id = room_id
        # En __init__ (no a nivel de clase) para no evaluar _() durante el import del módulo.
        self._ACCESOS = (("public", _("Pública (cualquiera puede entrar)")),
                         ("invite", _("Privada (solo con invitación)")),
                         ("knock", _("Llamando a la puerta (pides entrar y te aceptan)")))
        self._HISTORIALES = (("world_readable", _("Visible para todo el mundo (incluso sin entrar)")),
                             ("shared", _("Visible para los miembros (todo el historial)")),
                             ("invited", _("Desde que cada miembro fue invitado")),
                             ("joined", _("Desde que cada miembro entró")))
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Nombre:")), 0, wx.ALL, 6)
        self.txt_nombre = AccessibleTextCtrl(self, name=_("Nombre de la sala"))
        s.Add(self.txt_nombre, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Tema:")), 0, wx.ALL, 6)
        self.txt_tema = AccessibleTextCtrl(self, name=_("Tema de la sala"))
        s.Add(self.txt_tema, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Alias público (ej: #sala:servidor; vacío = no tocar):")), 0, wx.ALL, 6)
        self.txt_alias = AccessibleTextCtrl(self, name=_("Alias de la sala"))
        s.Add(self.txt_alias, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("Regla de &acceso:")), 0, wx.ALL, 6)
        self.cbo_acceso = wx.Choice(self, choices=[a[1] for a in self._ACCESOS], name=_("Acceso"))
        s.Add(self.cbo_acceso, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Historial visible:")), 0, wx.ALL, 6)
        self.cbo_historial = wx.Choice(self, choices=[h[1] for h in self._HISTORIALES],
                                       name=_("Historial visible"))
        s.Add(self.cbo_historial, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        self.chk_directorio = wx.CheckBox(self, label=_("Aparece en el &directorio público del servidor"))
        s.Add(self.chk_directorio, 0, wx.ALL, 6)
        self.lbl_cifrado = wx.StaticText(self, label="")
        s.Add(self.lbl_cifrado, 0, wx.ALL, 6)
        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_cifrar = wx.Button(self, label=_("Activar el ci&frado (irreversible)..."))
        self.btn_cifrar.Bind(wx.EVT_BUTTON, self._on_cifrar)
        fila2.Add(self.btn_cifrar, 0, wx.ALL, 5)
        btn_upgrade = wx.Button(self, label=_("Actualizar la &versión de la sala..."))
        btn_upgrade.Bind(wx.EVT_BUTTON, self._on_upgrade)
        fila2.Add(btn_upgrade, 0, wx.ALL, 5)
        s.Add(fila2, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
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
        self._estado = {}
        self._cargar()

    def _cargar(self):
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.estado_sala(acc, room)

        def _fin(datos, err):
            if err or not datos:
                notificar(_("No se pudieron leer los ajustes: {0}").format(err or ""))
                return
            self._estado = datos
            try:
                self.txt_nombre.SetValue(datos.get("nombre") or "")
                self.txt_tema.SetValue(datos.get("tema") or "")
                self.txt_alias.SetValue(datos.get("alias") or "")
                for i, (clave, _et) in enumerate(self._ACCESOS):
                    if clave == datos.get("acceso"):
                        self.cbo_acceso.SetSelection(i)
                for i, (clave, _et) in enumerate(self._HISTORIALES):
                    if clave == datos.get("historial"):
                        self.cbo_historial.SetSelection(i)
                if datos.get("cifrada"):
                    self.lbl_cifrado.SetLabel(_("Esta sala está CIFRADA de extremo a extremo."))
                    self.btn_cifrar.Disable()
                else:
                    self.lbl_cifrado.SetLabel(_("Esta sala NO está cifrada."))
                if datos.get("version"):
                    self.lbl_cifrado.SetLabel(self.lbl_cifrado.GetLabel()
                                              + " " + _("Versión de sala: {0}.").format(datos["version"]))
            except Exception:
                pass
            # Directorio público (consulta aparte, no bloqueante).
            self._cargar_directorio()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _cargar_directorio(self):
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.visibilidad_directorio(acc, room)

        def _fin(publico, err):
            if not err:
                self._dir_inicial = bool(publico)
                try:
                    self.chk_directorio.SetValue(bool(publico))
                except Exception:
                    pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_guardar(self, event):
        acc, room = self.account_id, self.room_id
        cambios = {}
        nombre = self.txt_nombre.GetValue().strip()
        tema = self.txt_tema.GetValue().strip()
        alias = self.txt_alias.GetValue().strip()
        if nombre and nombre != (self._estado.get("nombre") or ""):
            cambios["nombre"] = nombre
        if tema != (self._estado.get("tema") or ""):
            cambios["tema"] = tema
        i = self.cbo_acceso.GetSelection()
        if i >= 0 and self._ACCESOS[i][0] != self._estado.get("acceso"):
            cambios["acceso"] = self._ACCESOS[i][0]
        i = self.cbo_historial.GetSelection()
        if i >= 0 and self._HISTORIALES[i][0] != self._estado.get("historial"):
            cambios["historial"] = self._HISTORIALES[i][0]
        alias_nuevo = alias if (alias and alias != (self._estado.get("alias") or "")) else ""
        dir_nuevo = self.chk_directorio.GetValue()
        dir_cambia = dir_nuevo != getattr(self, "_dir_inicial", None)
        if not cambios and not alias_nuevo and not dir_cambia:
            self.EndModal(wx.ID_OK)
            return
        self.btn_ok.Disable()

        def _bg(ctx):
            from app.services.matrix import matrix_service
            if cambios:
                matrix_service.configurar_sala(acc, room, cambios)
            if alias_nuevo:
                matrix_service.fijar_alias_sala(acc, room, alias_nuevo)
            if dir_cambia:
                matrix_service.publicar_en_directorio(acc, room, dir_nuevo)
            return True

        def _fin(res, err):
            try:
                self.btn_ok.Enable()
            except Exception:
                pass
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
                return
            notificar(_("Ajustes guardados."))
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_cifrar(self, event):
        if confirmar(self, _("Activar el cifrado es IRREVERSIBLE: la sala quedará cifrada para "
                             "siempre y los clientes sin cifrado no podrán leerla. ¿Continuar?"),
                     _("Activar cifrado")) != wx.ID_YES:
            return
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.activar_cifrado_sala(acc, room)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Cifrado activado."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_upgrade(self, event):
        dlg = CustomTextEntryDialog(self, _("Nueva versión de sala (ej: 10; consulta la "
                                            "documentación de tu servidor):"),
                                    _("Actualizar la sala"))
        version = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not version:
            return
        if confirmar(self, _("Se creará una sala NUEVA que sustituye a esta (los miembros "
                             "verán un aviso para pasarse). ¿Continuar?"),
                     _("Actualizar la sala")) != wx.ID_YES:
            return
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.actualizar_version_sala(acc, room, version)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Sala actualizada.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)


class MatrixCrearEncuestaDialog(wx.Dialog):
    """Crear una encuesta (M7, formato de Element): pregunta + hasta 6 opciones."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Crear encuesta"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Crear encuesta"))
        self.pregunta = ""
        self.opciones = []
        self.oculta = False
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Pregunta:")), 0, wx.ALL, 6)
        self.txt_pregunta = AccessibleTextCtrl(self, name=_("Pregunta de la encuesta"))
        s.Add(self.txt_pregunta, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        self.txt_ops = []
        for i in range(6):
            s.Add(wx.StaticText(self, label=_("Opción {0}:").format(i + 1)), 0, wx.ALL, 4)
            t = AccessibleTextCtrl(self, name=_("Opción {0}").format(i + 1))
            self.txt_ops.append(t)
            s.Add(t, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        self.chk_oculta = wx.CheckBox(self, label=_("&Votos ocultos hasta cerrar la encuesta"))
        s.Add(self.chk_oculta, 0, wx.ALL, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        btn_ok = wx.Button(self, wx.ID_OK, _("&Crear"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        fila.Add(btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_pregunta.SetFocus()

    def _on_ok(self, event):
        self.pregunta = self.txt_pregunta.GetValue().strip()
        self.opciones = [t.GetValue().strip() for t in self.txt_ops if t.GetValue().strip()]
        self.oculta = self.chk_oculta.GetValue()
        if not self.pregunta or len(self.opciones) < 2:
            speak(_("Escribe la pregunta y al menos dos opciones."), interrupt=True)
            return
        self.EndModal(wx.ID_OK)


class MatrixVerHiloDialog(wx.Dialog):
    """Ver un hilo (M7): la raíz y sus respuestas, con campo para responder EN el hilo."""

    def __init__(self, parent, account_id, room_id, thread_root, titulo_sala=""):
        super().__init__(parent, title=_("Hilo en {0}").format(titulo_sala or room_id),
                         size=(640, 440), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Hilo de Matrix"))
        self.account_id = account_id
        self.room_id = room_id
        self.thread_root = thread_root
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Mensajes del hilo:"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Mensajes del hilo"))
        self.lista.InsertColumn(0, _("Hora"), width=110)
        self.lista.InsertColumn(1, _("Quién"), width=140)
        self.lista.InsertColumn(2, _("Mensaje"), width=340)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("&Responder en el hilo (Intro envía):")), 0, wx.ALL, 6)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER,
                                      name=_("Respuesta al hilo"))
        self.txt.Bind(wx.EVT_TEXT_ENTER, self._on_enviar)
        s.Add(self.txt, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc, room, raiz = self.account_id, self.room_id, self.thread_root

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.mensajes_de_hilo(acc, room, raiz)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo leer el hilo: {0}").format(err))
                return
            mensajes = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, m in enumerate(mensajes):
                from app.services.matrix.matrix_common import hora_local
                hora = hora_local(m.get("fecha"))
                self.lista.InsertItem(i, hora)
                self.lista.SetItem(i, 1, m.get("quien") or "")
                self.lista.SetItem(i, 2, (m.get("texto") or "").replace("\n", " "))
                self.lista.establecer_lectura(i, "{0}: {1}".format(
                    m.get("quien") or "", m.get("texto") or ""))
            self.lbl.SetLabel(_("{0} mensajes del hilo:").format(len(mensajes)))
            if mensajes:
                self.lista.Focus(len(mensajes) - 1)
                self.lista.Select(len(mensajes) - 1)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_enviar(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            return
        self.txt.SetValue("")
        acc, room, raiz = self.account_id, self.room_id, self.thread_root

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.enviar_mensaje(acc, room, texto, thread_root=raiz)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar: {0}").format(err))
            else:
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixRegistroDialog(wx.Dialog):
    """Registrar una cuenta NUEVA en un homeserver con registro abierto (M7)."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Registrar cuenta nueva de Matrix"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Registrar cuenta de Matrix"))
        self.account_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Servidor (con registro abierto):")), 0, wx.ALL, 6)
        self.txt_hs = AccessibleTextCtrl(self, name=_("Servidor"))
        s.Add(self.txt_hs, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("Nombre de &usuario (sin @ ni servidor):")), 0, wx.ALL, 6)
        self.txt_user = AccessibleTextCtrl(self, name=_("Usuario"))
        s.Add(self.txt_user, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_("&Contraseña:")), 0, wx.ALL, 6)
        self.txt_pass = AccessibleTextCtrl(self, style=wx.TE_PASSWORD, name=_("Contraseña"))
        s.Add(self.txt_pass, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        # Token de registro (opcional): en servidores con registro RESTRINGIDO por token (los que
        # genera un administrador), pégalo aquí. Si el servidor tiene registro abierto, déjalo vacío.
        s.Add(wx.StaticText(self, label=_("&Token de registro (opcional, solo si el servidor lo pide):")),
              0, wx.ALL, 6)
        self.txt_token = AccessibleTextCtrl(
            self, name=_("Token de registro"),
            ayuda_texto=_("Para servidores que solo permiten registrarse con un token dado por un "
                          "administrador. Déjalo vacío si el servidor tiene registro abierto."))
        s.Add(self.txt_token, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        s.Add(wx.StaticText(self, label=_(
            "Nota: matrix.org y otros servidores grandes exigen captcha y solo permiten\n"
            "registrarse desde su web; en ese caso se avisará con claridad.")), 0, wx.ALL, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        self.btn_ok = wx.Button(self, wx.ID_OK, _("&Registrar"))
        self.btn_ok.Bind(wx.EVT_BUTTON, self._on_registrar)
        fila.Add(self.btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_hs.SetFocus()

    def _on_registrar(self, event):
        hs = self.txt_hs.GetValue().strip()
        usuario = self.txt_user.GetValue().strip().lstrip("@").split(":")[0]
        password = self.txt_pass.GetValue()
        token = self.txt_token.GetValue().strip()
        if not hs or not usuario or not password:
            speak(_("Rellena el servidor, el usuario y la contraseña."), interrupt=True)
            return
        self.btn_ok.Disable()
        speak(_("Registrando la cuenta..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            if token:
                return matrix_service.registrar_con_token(hs, usuario, password, token)
            return matrix_service.registrar_cuenta(hs, usuario, password)

        def _fin(res, err):
            try:
                self.btn_ok.Enable()
            except Exception:
                pass
            if err:
                notificar(_("No se pudo registrar: {0}").format(err))
                return
            self.account_id = res
            notificar(_("Cuenta registrada y añadida."))
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixHilosSalaDialog(wx.Dialog):
    """Lista los HILOS de una sala (M8): elige uno para abrirlo con su conversación de hilo."""

    def __init__(self, parent, account_id, room_id, titulo_sala=""):
        super().__init__(parent, title=_("Hilos de {0}").format(titulo_sala or room_id),
                         size=(620, 420), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Hilos de la sala"))
        self.account_id = account_id
        self.room_id = room_id
        self.titulo_sala = titulo_sala
        self.hilos = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Hilos (Intro abre el hilo):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de hilos"))
        self.lista.InsertColumn(0, _("Quién"), width=150)
        self.lista.InsertColumn(1, _("Mensaje raíz"), width=340)
        self.lista.InsertColumn(2, _("Fecha"), width=120)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_abrir)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn = wx.Button(self, label=_("&Abrir el hilo"))
        btn.Bind(wx.EVT_BUTTON, self._on_abrir)
        fila.Add(btn, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        speak(_("Pidiendo los hilos..."), interrupt=True)
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.hilos_de_sala(acc, room)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron leer los hilos: {0}").format(err))
                return
            self.hilos = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, h in enumerate(self.hilos):
                self.lista.InsertItem(i, h.get("quien") or "")
                self.lista.SetItem(i, 1, (h.get("texto") or "").replace("\n", " "))
                from app.services.matrix.matrix_common import hora_local
                self.lista.SetItem(i, 2, hora_local(h.get("fecha"), con_fecha=True))
                self.lista.establecer_lectura(i, "{0}: {1}".format(
                    h.get("quien") or "", h.get("texto") or ""))
            self.lbl.SetLabel(_("{0} hilos (Intro abre el hilo):").format(len(self.hilos)))
            if self.hilos:
                self.lista.Focus(0)
                self.lista.Select(0)
            else:
                notificar(_("Esta sala no tiene hilos."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_abrir(self, event):
        i = self.lista.GetFocusedItem()
        if i < 0 or i >= len(self.hilos):
            return
        raiz = self.hilos[i].get("event_id")
        if not raiz:
            return
        from .matrix_dialogs import MatrixVerHiloDialog
        dlg = MatrixVerHiloDialog(self, self.account_id, self.room_id, raiz, self.titulo_sala)
        dlg.ShowModal()
        dlg.Destroy()


class MatrixDispositivosSalaDialog(wx.Dialog):
    """Dispositivos cifrados de los miembros de una sala (M8): verificar (confiar) o bloquear
    manualmente cada uno, e iniciar una verificación por emojis."""

    def __init__(self, parent, account_id, room_id, titulo_sala=""):
        super().__init__(parent, title=_("Dispositivos de {0}").format(titulo_sala or room_id),
                         size=(640, 420), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Dispositivos cifrados de la sala"))
        self.account_id = account_id
        self.room_id = room_id
        self.dispositivos = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Dispositivos (menú contextual para actuar):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de dispositivos de la sala"))
        self.lista.InsertColumn(0, _("Usuario"), width=200)
        self.lista.InsertColumn(1, _("Dispositivo"), width=180)
        self.lista.InsertColumn(2, _("Estado"), width=140)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        # Refrescar la lista cuando una verificación termine (para que un dispositivo pase de "sin
        # verificar" a "verificado" al vuelo, sin tener que cerrar y reabrir el diálogo).
        from app.core.event_bus import event_bus
        self._event_bus = event_bus
        event_bus.subscribe("matrix_evento", self._on_evento_bus)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._cargar()

    def _on_evento_bus(self, datos):
        try:
            d = datos or {}
            if d.get("tipo") == "verificacion_ok" and d.get("account_id") == self.account_id:
                wx.CallAfter(self._cargar)
        except Exception:
            pass

    def _on_destroy(self, event):
        # Solo cuando se destruye ESTE diálogo (EVT_WINDOW_DESTROY también salta por los hijos).
        if event.GetEventObject() is self:
            try:
                self._event_bus.unsubscribe("matrix_evento", self._on_evento_bus)
            except Exception:
                pass
        event.Skip()

    def _cargar(self):
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.dispositivos_de_sala(acc, room)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron leer los dispositivos: {0}").format(err))
                return
            self.dispositivos = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, d in enumerate(self.dispositivos):
                self.lista.InsertItem(i, d["user_id"])
                self.lista.SetItem(i, 1, "{0} ({1})".format(d["nombre"], d["device_id"]))
                self.lista.SetItem(i, 2, d["estado"])
                self.lista.establecer_lectura(i, _("{0}, dispositivo {1}: {2}").format(
                    d["user_id"], d["nombre"], d["estado"]))
            self.lbl.SetLabel(_("{0} dispositivos (menú contextual para actuar):").format(
                len(self.dispositivos)))
            if self.dispositivos:
                self.lista.Focus(0)
                self.lista.Select(0)
            else:
                notificar(_("No hay dispositivos (¿la sala está cifrada?)."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_menu(self, event):
        i = self.lista.GetFocusedItem()
        if i < 0 or i >= len(self.dispositivos):
            return
        d = self.dispositivos[i]
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Verificar por emojis (comparar en el otro cliente)..."))
        self.Bind(wx.EVT_MENU, lambda e, dd=d: self._accion(dd, "verificar"), it)
        # "Marcar de confianza sin emojis" es un override criptográfico: solo en modo experto y con
        # doble confirmación mostrando la huella. Fuera de él, el camino es verificar por emojis.
        if _modo_experto_confianza():
            it = menu.Append(wx.ID_ANY, _("[Experto] Marcar como de confianza sin emojis..."))
            self.Bind(wx.EVT_MENU, lambda e, dd=d: self._accion(dd, "confiar"), it)
        it = menu.Append(wx.ID_ANY, _("Bloquear este dispositivo"))
        self.Bind(wx.EVT_MENU, lambda e, dd=d: self._accion(dd, "bloquear"), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _accion(self, d, accion):
        acc = self.account_id
        uid, did = d["user_id"], d["device_id"]
        # Confiar SIN emojis exige, aun en modo experto, confirmar mostrando la huella completa del
        # dispositivo (para que el usuario la contraste por un canal seguro con la otra persona).
        if accion == "confiar":
            huella = d.get("ed25519") or _("(no disponible)")
            if confirmar(self, _("MODO EXPERTO. Vas a marcar de confianza el dispositivo «{0}» de {1} "
                                 "SIN verificarlo por emojis.\n\nHuella de la clave (ed25519):\n{2}\n\n"
                                 "Confía solo si has comprobado esta huella con la otra persona por un "
                                 "canal seguro. ¿Continuar?").format(d.get("nombre") or did, uid, huella),
                         _("Confiar sin emojis (experto)")) != wx.ID_YES:
                return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            if accion == "verificar":
                matrix_service.iniciar_verificacion(acc, uid, did)
            elif accion == "confiar":
                matrix_service.confiar_dispositivo(acc, uid, did, True)
            else:
                matrix_service.confiar_dispositivo(acc, uid, did, False)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            if accion == "verificar":
                notificar(_("Verificación iniciada: compara los emojis en el otro cliente."))
            else:
                notificar(_("Hecho."))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
