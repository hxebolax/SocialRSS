# -*- coding: utf-8 -*-
"""Diálogos de ADMINISTRACIÓN del servidor Matrix (Synapse Admin API).

Solo son útiles si tu cuenta administra un servidor Synapse. Cada acción avisa con claridad si el
servidor la rechaza (no admin / no es Synapse). Se abren desde el submenú "Administración" del
menú Matrix, que solo aparece si activas la preferencia en Preferencias → Matrix.

Todas las listas son AccessibleListCtrl seleccionables (no texto), con menú contextual (tecla
Aplicaciones) de acciones por elemento, siguiendo el patrón del resto de la aplicación.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import (AccessibleTextCtrl, AccessibleListCtrl, mostrar_info,
                                  confirmar, CustomTextEntryDialog)


def _fecha_ms(ms) -> str:
    """Un timestamp en milisegundos epoch a fecha/hora local legible ('' si no hay)."""
    try:
        if not ms:
            return ""
        from datetime import datetime
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


class MatrixAdminUsuariosDialog(wx.Dialog):
    """Cuentas del servidor: buscar, ver detalles y actuar (admin, bloquear, desactivar,
    contraseña, conexiones, directo)."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: usuarios del servidor"),
                         size=(720, 500), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Usuarios del servidor"))
        self.account_id = account_id
        self.usuarios = []
        s = wx.BoxSizer(wx.VERTICAL)
        fila_b = wx.BoxSizer(wx.HORIZONTAL)
        fila_b.Add(wx.StaticText(self, label=_("&Buscar cuenta:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.txt_buscar = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Buscar usuario"),
                                             ayuda_texto=_("Parte del nombre de usuario. Vacío = todos."))
        self.txt_buscar.Bind(wx.EVT_TEXT_ENTER, lambda e: self._cargar())
        fila_b.Add(self.txt_buscar, 1, wx.EXPAND | wx.ALL, 4)
        btn_b = wx.Button(self, label=_("Bu&scar"))
        btn_b.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        fila_b.Add(btn_b, 0, wx.ALL, 4)
        s.Add(fila_b, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        self.lbl = wx.StaticText(self, label=_("Usuarios (Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de usuarios del servidor"))
        self.lista.InsertColumn(0, _("Nombre"), width=180)
        self.lista.InsertColumn(1, _("Usuario"), width=240)
        self.lista.InsertColumn(2, _("Rol"), width=120)
        self.lista.InsertColumn(3, _("Estado"), width=130)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id
        busq = self.txt_buscar.GetValue().strip()
        speak(_("Consultando el servidor..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_listar_usuarios(acc, busq)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿tu cuenta no es administradora?): {0}").format(err))
                return
            self.usuarios = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, u in enumerate(self.usuarios):
                rol = _("Administrador") if u["admin"] else _("Usuario")
                estado = (_("Desactivado") if u["desactivado"]
                          else _("Bloqueado") if u["bloqueado"] else _("Activo"))
                self.lista.InsertItem(i, u["nombre"] or u["user_id"])
                self.lista.SetItem(i, 1, u["user_id"])
                self.lista.SetItem(i, 2, rol)
                self.lista.SetItem(i, 3, estado)
                self.lista.establecer_lectura(
                    i, _("{0}, {1}, {2}, {3}").format(u["nombre"] or u["user_id"], u["user_id"], rol, estado))
            self.lbl.SetLabel(_("{0} usuarios (Aplicaciones para acciones):").format(len(self.usuarios)))
            if self.usuarios:
                self.lista.Focus(0)
                self.lista.Select(0)
            notificar(_("{0} usuarios.").format(len(self.usuarios)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.usuarios[i] if 0 <= i < len(self.usuarios) else None

    def _on_menu(self, event):
        u = self._actual()
        if not u:
            return
        uid = u["user_id"]
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Ver detalles"))
        self.Bind(wx.EVT_MENU, lambda e: self._detalles(uid), it)
        it = menu.Append(wx.ID_ANY, _("Ver conexiones (IP y sesiones)"))
        self.Bind(wx.EVT_MENU, lambda e: self._whois(uid), it)
        it = menu.Append(wx.ID_ANY, _("Ver sus salas"))
        self.Bind(wx.EVT_MENU, lambda e: self._salas_de(uid), it)
        it = menu.Append(wx.ID_ANY, _("Ver sus dispositivos..."))
        self.Bind(wx.EVT_MENU, lambda e: self._dispositivos(uid), it)
        it = menu.Append(wx.ID_ANY, _("Ver sus archivos subidos..."))
        self.Bind(wx.EVT_MENU, lambda e: self._archivos_de(uid), it)
        it = menu.Append(wx.ID_ANY, _("Mensaje directo"))
        self.Bind(wx.EVT_MENU, lambda e: self._dm(uid), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Límite de mensajes (rate limit)..."))
        self.Bind(wx.EVT_MENU, lambda e: self._ratelimit(uid), it)
        it = menu.Append(wx.ID_ANY, _("Sombra-baneo (parece que envía, pero no llega)..."))
        self.Bind(wx.EVT_MENU, lambda e: self._shadow(uid), it)
        it = menu.Append(wx.ID_ANY, _("Suplantar: obtener una sesión como este usuario..."))
        self.Bind(wx.EVT_MENU, lambda e: self._suplantar(uid), it)
        menu.AppendSeparator()
        et_admin = _("Quitar administrador") if u["admin"] else _("Hacer administrador del servidor")
        it = menu.Append(wx.ID_ANY, et_admin)
        self.Bind(wx.EVT_MENU, lambda e: self._fijar_admin(uid, not u["admin"]), it)
        et_lock = _("Desbloquear la cuenta") if u["bloqueado"] else _("Bloquear la cuenta (no podrá entrar)")
        it = menu.Append(wx.ID_ANY, et_lock)
        self.Bind(wx.EVT_MENU, lambda e: self._bloquear(uid, not u["bloqueado"]), it)
        it = menu.Append(wx.ID_ANY, _("Cambiar la contraseña..."))
        self.Bind(wx.EVT_MENU, lambda e: self._password(uid), it)
        if not u["desactivado"]:
            it = menu.Append(wx.ID_ANY, _("Desactivar la cuenta..."))
            self.Bind(wx.EVT_MENU, lambda e: self._desactivar(uid), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _tarea(self, fn, ok_msg, recargar=True):
        """Ejecuta una acción de admin en segundo plano y avisa; recarga la lista si procede."""
        def _bg(ctx):
            fn()
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            notificar(ok_msg)
            if recargar:
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _detalles(self, uid):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_detalle_usuario(acc, uid)

        def _fin(d, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            d = d or {}
            lineas = [
                _("Usuario: {0}").format(uid),
                _("Nombre visible: {0}").format(d.get("displayname") or _("(sin nombre)")),
                _("Administrador del servidor: {0}").format(_("sí") if d.get("admin") else _("no")),
                _("Desactivado: {0}").format(_("sí") if d.get("deactivated") else _("no")),
                _("Creado: {0}").format(_fecha_ms(d.get("creation_ts", 0) and int(d["creation_ts"]) * 1000)),
            ]
            correos = [t.get("address") for t in (d.get("threepids") or []) if t.get("address")]
            if correos:
                lineas.append(_("Correos/teléfonos: {0}").format(", ".join(correos)))
            mostrar_info(self, "\n".join(lineas), _("Detalles de {0}").format(uid))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _whois(self, uid):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_whois(acc, uid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            conexiones = res or []
            if not conexiones:
                mostrar_info(self, _("Sin conexiones registradas."), _("Conexiones de {0}").format(uid))
                return
            lineas = [_("{0} conexiones de {1}:").format(len(conexiones), uid), ""]
            for c in conexiones:
                lineas.append(_("Dispositivo {0} — IP {1} — {2} — {3}").format(
                    c["dispositivo"], c["ip"], _fecha_ms(c["ultima_vez"]), c["navegador"]))
            self.GetParent()._mostrar_texto_largo(_("Conexiones de {0}").format(uid), "\n".join(lineas))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _salas_de(self, uid):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_salas_de_usuario(acc, uid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            salas = res or []
            if not salas:
                mostrar_info(self, _("No participa en ninguna sala."), _("Salas de {0}").format(uid))
                return
            lineas = [_("{0} salas de {1}:").format(len(salas), uid), ""] + list(salas)
            self.GetParent()._mostrar_texto_largo(_("Salas de {0}").format(uid), "\n".join(lineas))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _dispositivos(self, uid):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_dispositivos_usuario(acc, uid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            disp = res or []
            if not disp:
                mostrar_info(self, _("Sin dispositivos."), _("Dispositivos de {0}").format(uid))
                return
            lineas = [_("{0} dispositivos de {1}:").format(len(disp), uid), ""]
            for d in disp:
                lineas.append(_("{0} — {1} — IP {2} — {3}").format(
                    d["id"], d["nombre"] or _("(sin nombre)"), d["ip"] or "?",
                    _fecha_ms(d["ultima_vez"]) or "?"))
            self.GetParent()._mostrar_texto_largo(_("Dispositivos de {0}").format(uid), "\n".join(lineas))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _archivos_de(self, uid):
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_archivos_usuario(acc, uid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            media = res or []
            if not media:
                mostrar_info(self, _("Sin archivos subidos."), _("Archivos de {0}").format(uid))
                return
            lineas = [_("{0} archivos de {1}:").format(len(media), uid), ""]
            for m in media:
                lineas.append(_("{0} — {1} — {2:.0f} KB — {3}{4}").format(
                    m["nombre"] or m["media_id"], m["tipo"] or "?", (m["tamano"] or 0) / 1024,
                    _fecha_ms(m["creado"]) or "?",
                    _(" — EN CUARENTENA") if m["cuarentena"] else ""))
            self.GetParent()._mostrar_texto_largo(_("Archivos de {0}").format(uid), "\n".join(lineas))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _ratelimit(self, uid):
        acc = self.account_id

        def _bg_ver(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_ver_ratelimit(acc, uid)

        def _fin_ver(actual, err):
            if err:
                notificar(_("No se pudo consultar: {0}").format(err))
                return
            actual = actual or {}
            mps_ini = actual.get("messages_per_second")
            burst_ini = actual.get("burst_count")
            info = (_("Actual: {0} mensajes/segundo, ráfaga {1}.").format(mps_ini, burst_ini)
                    if mps_ini is not None else _("Ahora usa el límite general del servidor."))
            d = CustomTextEntryDialog(
                self, info + "\n\n" + _("Mensajes por segundo (0 = SIN límite; vacío = cancelar):"),
                _("Límite de mensajes"), value=str(mps_ini if mps_ini is not None else ""))
            v1 = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
            d.Destroy()
            if v1 == "":
                return
            d2 = CustomTextEntryDialog(self, _("Ráfaga (mensajes seguidos permitidos de golpe):"),
                                       _("Límite de mensajes"),
                                       value=str(burst_ini if burst_ini is not None else "10"))
            v2 = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
            d2.Destroy()
            if v2 == "":
                return
            try:
                mps, burst = int(float(v1)), int(float(v2))
            except ValueError:
                notificar(_("Escribe números."))
                return
            from app.services.matrix import matrix_service
            self._tarea(lambda: matrix_service.admin_fijar_ratelimit(acc, uid, mps, burst),
                        _("Límite actualizado."), recargar=False)
        task_manager.start_task(_bg_ver, callback=_fin_ver, pass_context=True)

    def _shadow(self, uid):
        r = confirmar(self, _("Sombra-baneo de {0}: el usuario creerá que envía mensajes con "
                              "normalidad, pero NADIE los recibirá. Elige Sí para SOMBRA-BANEAR, No "
                              "para QUITARLO.").format(uid), _("Sombra-baneo"))
        banear = (r == wx.ID_YES)
        from app.services.matrix import matrix_service
        self._tarea(lambda: matrix_service.admin_shadow_ban(self.account_id, uid, banear=banear),
                    _("Sombra-baneo aplicado.") if banear else _("Sombra-baneo quitado."),
                    recargar=False)

    def _suplantar(self, uid):
        if confirmar(self, _("SUPLANTAR a {0}: se obtiene una sesión (token) para actuar como ese "
                             "usuario. Es una acción MUY delicada (acceso total a su cuenta), pensada "
                             "solo para reproducir un problema que te reporte. El token se copiará al "
                             "portapapeles. ¿Continuar?").format(uid),
                     _("Suplantar usuario")) != wx.ID_YES:
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_login_como_usuario(acc, uid)

        def _fin(tok, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            if tok and wx.TheClipboard.Open():
                try:
                    wx.TheClipboard.SetData(wx.TextDataObject(tok))
                finally:
                    wx.TheClipboard.Close()
            mostrar_info(self, _("Sesión obtenida como {0}. El token de acceso se ha copiado al "
                                 "portapapeles; úsalo con responsabilidad.").format(uid),
                         _("Suplantar usuario"))
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

    def _fijar_admin(self, uid, es_admin):
        if es_admin and confirmar(
                self, _("Hacer a {0} ADMINISTRADOR DEL SERVIDOR le da control total sobre todo el "
                        "servidor. ¿Continuar?").format(uid), _("Confirmar")) != wx.ID_YES:
            return
        from app.services.matrix import matrix_service
        self._tarea(lambda: matrix_service.admin_fijar_admin(self.account_id, uid, es_admin),
                    _("Rol de administrador actualizado."))

    def _bloquear(self, uid, bloquear):
        from app.services.matrix import matrix_service
        self._tarea(lambda: matrix_service.admin_bloquear_usuario(self.account_id, uid, bloquear),
                    _("Cuenta bloqueada.") if bloquear else _("Cuenta desbloqueada."))

    def _password(self, uid):
        d = CustomTextEntryDialog(self, _("Nueva contraseña para {0}:").format(uid),
                                  _("Cambiar contraseña"), password=True)
        nueva = d.GetValue() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not nueva:
            return
        from app.services.matrix import matrix_service
        self._tarea(lambda: matrix_service.admin_resetear_password(self.account_id, uid, nueva),
                    _("Contraseña cambiada y sesiones cerradas."), recargar=False)

    def _desactivar(self, uid):
        if confirmar(self, _("Desactivar la cuenta {0} cierra el acceso de esa persona. ¿Continuar?").format(uid),
                     _("Confirmar")) != wx.ID_YES:
            return
        borrar = confirmar(self, _("¿Borrar además todos sus mensajes y su perfil (irreversible)? "
                                   "Elige No para solo desactivarla."), _("Borrar datos")) == wx.ID_YES
        from app.services.matrix import matrix_service
        self._tarea(lambda: matrix_service.admin_desactivar_usuario(self.account_id, uid, borrar),
                    _("Cuenta desactivada."))


class MatrixAdminSalasDialog(wx.Dialog):
    """Salas del servidor (todas, no solo las tuyas): buscar, ver detalles y borrar."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: salas del servidor"),
                         size=(720, 500), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Salas del servidor"))
        self.account_id = account_id
        self.salas = []
        s = wx.BoxSizer(wx.VERTICAL)
        fila_b = wx.BoxSizer(wx.HORIZONTAL)
        fila_b.Add(wx.StaticText(self, label=_("&Buscar sala:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.txt_buscar = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Buscar sala"),
                                             ayuda_texto=_("Parte del nombre o alias. Vacío = todas."))
        self.txt_buscar.Bind(wx.EVT_TEXT_ENTER, lambda e: self._cargar())
        fila_b.Add(self.txt_buscar, 1, wx.EXPAND | wx.ALL, 4)
        btn_b = wx.Button(self, label=_("Bu&scar"))
        btn_b.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        fila_b.Add(btn_b, 0, wx.ALL, 4)
        s.Add(fila_b, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        self.lbl = wx.StaticText(self, label=_("Salas (Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de salas del servidor"))
        self.lista.InsertColumn(0, _("Sala"), width=240)
        self.lista.InsertColumn(1, _("Miembros"), width=90)
        self.lista.InsertColumn(2, _("Alias"), width=220)
        self.lista.InsertColumn(3, _("Cifrada"), width=90)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id
        busq = self.txt_buscar.GetValue().strip()
        speak(_("Consultando el servidor..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_listar_salas(acc, busq)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿tu cuenta no es administradora?): {0}").format(err))
                return
            self.salas = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, sala in enumerate(self.salas):
                self.lista.InsertItem(i, sala["nombre"])
                self.lista.SetItem(i, 1, str(sala["miembros"]))
                self.lista.SetItem(i, 2, sala["alias"])
                self.lista.SetItem(i, 3, _("sí") if sala["cifrada"] else _("no"))
                self.lista.establecer_lectura(i, _("{0}, {1} miembros{2}").format(
                    sala["nombre"], sala["miembros"], _(", cifrada") if sala["cifrada"] else ""))
            self.lbl.SetLabel(_("{0} salas (Aplicaciones para acciones):").format(len(self.salas)))
            if self.salas:
                self.lista.Focus(0)
                self.lista.Select(0)
            notificar(_("{0} salas.").format(len(self.salas)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.salas[i] if 0 <= i < len(self.salas) else None

    def _on_menu(self, event):
        sala = self._actual()
        if not sala:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Ver detalles"))
        self.Bind(wx.EVT_MENU, lambda e: self._detalles(sala), it)
        it = menu.Append(wx.ID_ANY, _("Hacerme administrador de la sala (para moderarla)"))
        self.Bind(wx.EVT_MENU, lambda e: self._hacerme_admin(sala), it)
        it = menu.Append(wx.ID_ANY, _("Purgar historial antiguo (liberar espacio)..."))
        self.Bind(wx.EVT_MENU, lambda e: self._purgar(sala), it)
        it = menu.Append(wx.ID_ANY, _("Bloquear o desbloquear la sala..."))
        self.Bind(wx.EVT_MENU, lambda e: self._bloquear(sala), it)
        it = menu.Append(wx.ID_ANY, _("Poner sus archivos en cuarentena..."))
        self.Bind(wx.EVT_MENU, lambda e: self._cuarentena(sala), it)
        it = menu.Append(wx.ID_ANY, _("Borrar la sala del servidor..."))
        self.Bind(wx.EVT_MENU, lambda e: self._borrar(sala), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _hacerme_admin(self, sala):
        if confirmar(self, _("¿Concederte permisos de ADMINISTRADOR en «{0}» para poder moderarla? "
                             "(Requiere que la sala tenga algún administrador local.)").format(
                             sala["nombre"]), _("Hacerme administrador de la sala")) != wx.ID_YES:
            return
        acc, room_id = self.account_id, sala["room_id"]
        from app.services.matrix import matrix_service
        self._tarea(lambda: matrix_service.admin_hacerme_admin_sala(acc, room_id),
                    _("Ya eres administrador de la sala."), recargar=False)

    def _purgar(self, sala):
        d = CustomTextEntryDialog(
            self, _("Borrar los mensajes ANTERIORES a hace cuántos DÍAS (por ejemplo 30 borra lo de "
                    "hace más de un mes; deja vacío para cancelar):"),
            _("Purgar historial"), value="30")
        dias = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not dias:
            return
        try:
            dias_n = max(1, int(float(dias)))
        except ValueError:
            notificar(_("Escribe un número de días."))
            return
        if confirmar(self, _("Se borrará de forma DEFINITIVA el historial de «{0}» anterior a hace "
                             "{1} días. ¿Continuar?").format(sala["nombre"], dias_n),
                     _("Confirmar purga")) != wx.ID_YES:
            return
        import time as _t
        antes_ms = int((_t.time() - dias_n * 86400) * 1000)
        acc, room_id = self.account_id, sala["room_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_purgar_historial(acc, room_id, antes_ms)

        def _fin(pid, err):
            if err:
                notificar(_("No se pudo purgar: {0}").format(err))
            else:
                notificar(_("Purga en marcha en el servidor."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _bloquear(self, sala):
        acc, room_id = self.account_id, sala["room_id"]
        # Preguntar acción según si está bloqueada (se consulta en 2.º plano para no adivinar).
        def _bg_estado(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_sala_bloqueada(acc, room_id)

        def _fin_estado(bloqueada, err):
            if err:
                notificar(_("No se pudo consultar: {0}").format(err))
                return
            if bloqueada:
                pregunta = _("«{0}» está BLOQUEADA. ¿Desbloquearla para que se pueda volver a "
                             "entrar?").format(sala["nombre"])
                nuevo = False
            else:
                pregunta = _("¿Bloquear «{0}»? Nadie podrá entrar (los que ya estén dentro siguen "
                             "hasta que salgan). No borra la sala.").format(sala["nombre"])
                nuevo = True
            if confirmar(self, pregunta, _("Bloquear o desbloquear la sala")) != wx.ID_YES:
                return

            def _bg(ctx):
                from app.services.matrix import matrix_service
                return matrix_service.admin_bloquear_sala(acc, room_id, bloquear=nuevo)

            def _fin(res, err2):
                if err2:
                    notificar(_("No se pudo: {0}").format(err2))
                else:
                    notificar(_("Sala bloqueada.") if nuevo else _("Sala desbloqueada."))
            task_manager.start_task(_bg, callback=_fin, pass_context=True)
        task_manager.start_task(_bg_estado, callback=_fin_estado, pass_context=True)

    def _cuarentena(self, sala):
        if confirmar(self, _("¿Poner en CUARENTENA todos los archivos de «{0}»? Dejarán de servirse a "
                             "los usuarios (útil ante contenido abusivo). No borra la sala.").format(
                             sala["nombre"]), _("Cuarentena de archivos")) != wx.ID_YES:
            return
        acc, room_id = self.account_id, sala["room_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_cuarentena_media_sala(acc, room_id)

        def _fin(n, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("{0} archivo(s) en cuarentena.").format(n or 0))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _detalles(self, sala):
        acc = self.account_id
        room_id = sala["room_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_detalle_sala(acc, room_id)

        def _fin(d, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            d = d or {}
            lineas = [
                _("Sala: {0}").format(d.get("name") or room_id),
                _("Identificador: {0}").format(room_id),
                _("Alias: {0}").format(d.get("canonical_alias") or _("(ninguno)")),
                _("Miembros: {0}").format(d.get("joined_members") or 0),
                _("Creador: {0}").format(d.get("creator") or _("(desconocido)")),
                _("Cifrada: {0}").format(d.get("encryption") or _("no")),
                _("Pública: {0}").format(_("sí") if d.get("public") else _("no")),
                _("Versión de sala: {0}").format(d.get("room_version") or "?"),
            ]
            if d.get("topic"):
                lineas.append(_("Tema: {0}").format(d["topic"]))
            mostrar_info(self, "\n".join(lineas), _("Detalles de la sala"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _borrar(self, sala):
        if confirmar(self, _("Borrar «{0}» del servidor echa a TODOS sus miembros y borra su "
                             "historia. Es una acción muy fuerte. ¿Continuar?").format(sala["nombre"]),
                     _("Confirmar borrado")) != wx.ID_YES:
            return
        bloquear = confirmar(self, _("¿Bloquear además la sala para que no se pueda volver a crear "
                                     "ni entrar? Elige No para solo borrarla."),
                             _("Bloquear la sala")) == wx.ID_YES
        acc = self.account_id
        room_id = sala["room_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_borrar_sala(acc, room_id, bloquear=bloquear, purgar=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
                return
            notificar(_("Sala borrada."))
            self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixAdminInformesDialog(wx.Dialog):
    """Mensajes reportados por los usuarios del servidor, con su motivo. Ver el detalle de cada uno."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: mensajes reportados"),
                         size=(720, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Mensajes reportados"))
        self.account_id = account_id
        self.informes = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Reportes (Aplicaciones para ver el detalle):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de mensajes reportados"))
        self.lista.InsertColumn(0, _("Fecha"), width=130)
        self.lista.InsertColumn(1, _("Motivo"), width=220)
        self.lista.InsertColumn(2, _("Autor del mensaje"), width=180)
        self.lista.InsertColumn(3, _("Sala"), width=180)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._detalle())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id
        speak(_("Consultando el servidor..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_listar_informes(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿tu cuenta no es administradora?): {0}").format(err))
                return
            self.informes = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, r in enumerate(self.informes):
                self.lista.InsertItem(i, _fecha_ms(r["fecha"]))
                self.lista.SetItem(i, 1, (r["motivo"] or _("(sin motivo)")).replace("\n", " "))
                self.lista.SetItem(i, 2, r["autor"])
                self.lista.SetItem(i, 3, r["sala"])
                self.lista.establecer_lectura(i, _("{0}, motivo {1}, autor {2}, en {3}").format(
                    _fecha_ms(r["fecha"]), r["motivo"] or _("sin motivo"), r["autor"], r["sala"]))
            self.lbl.SetLabel(_("{0} reportes (Aplicaciones para ver el detalle):").format(len(self.informes)))
            if self.informes:
                self.lista.Focus(0)
                self.lista.Select(0)
            notificar(_("{0} mensajes reportados.").format(len(self.informes)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.informes[i] if 0 <= i < len(self.informes) else None

    def _on_menu(self, event):
        if not self._actual():
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Ver el detalle del reporte"))
        self.Bind(wx.EVT_MENU, lambda e: self._detalle(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _detalle(self):
        r = self._actual()
        if not r:
            return
        acc = self.account_id
        rid = r["id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_detalle_informe(acc, rid)

        def _fin(d, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            d = d or {}
            ev = d.get("event_json") or {}
            contenido = (ev.get("content") or {}).get("body") or ""
            lineas = [
                _("Motivo del reporte: {0}").format(d.get("reason") or _("(sin motivo)")),
                _("Lo reportó: {0}").format(d.get("user_id") or "?"),
                _("Autor del mensaje: {0}").format(d.get("sender") or "?"),
                _("Sala: {0}").format(d.get("canonical_alias") or d.get("name") or d.get("room_id") or "?"),
                _("Fecha: {0}").format(_fecha_ms(d.get("received_ts", 0))),
                "",
                _("Mensaje reportado:"),
                contenido or _("(sin texto: puede ser un adjunto o estar cifrado)"),
            ]
            self.GetParent()._mostrar_texto_largo(_("Detalle del reporte"), "\n".join(lineas))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixAdminTokensDialog(wx.Dialog):
    """Tokens de registro: crear o borrar tokens para servidores con registro restringido."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: tokens de registro"),
                         size=(640, 440), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Tokens de registro"))
        self.account_id = account_id
        self.tokens = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Tokens de registro:"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de tokens de registro"))
        self.lista.InsertColumn(0, _("Token"), width=220)
        self.lista.InsertColumn(1, _("Usos"), width=160)
        self.lista.InsertColumn(2, _("Caduca"), width=160)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_nuevo = wx.Button(self, label=_("&Crear un token..."))
        btn_nuevo.Bind(wx.EVT_BUTTON, lambda e: self._crear())
        fila.Add(btn_nuevo, 0, wx.ALL, 5)
        btn_del = wx.Button(self, label=_("&Borrar el token"))
        btn_del.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        fila.Add(btn_del, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id
        speak(_("Consultando el servidor..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_listar_tokens_registro(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿tu cuenta no es administradora?): {0}").format(err))
                return
            self.tokens = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, t in enumerate(self.tokens):
                usos = (_("{0} de {1}").format(t["usos_hechos"], t["usos_permitidos"])
                        if t["usos_permitidos"] is not None
                        else _("{0} (ilimitado)").format(t["usos_hechos"]))
                caduca = _fecha_ms(t["caduca"]) if t["caduca"] else _("no caduca")
                self.lista.InsertItem(i, t["token"])
                self.lista.SetItem(i, 1, usos)
                self.lista.SetItem(i, 2, caduca)
                self.lista.establecer_lectura(i, _("{0}, usos {1}, {2}").format(t["token"], usos, caduca))
            self.lbl.SetLabel(_("{0} tokens de registro:").format(len(self.tokens)))
            if self.tokens:
                self.lista.Focus(0)
                self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.tokens[i] if 0 <= i < len(self.tokens) else None

    def _on_menu(self, event):
        if not self._actual():
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Borrar este token"))
        self.Bind(wx.EVT_MENU, lambda e: self._borrar(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _crear(self):
        d = CustomTextEntryDialog(
            self, _("¿Cuántos usos permite el token? (vacío = ilimitado):"), _("Crear token"))
        txt = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if txt is None:
            return
        usos = None
        if txt:
            try:
                usos = max(1, int(txt))
            except ValueError:
                notificar(_("Escribe un número o déjalo vacío."))
                return
        acc = self.account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_crear_token_registro(acc, usos)

        def _fin(token, err):
            if err:
                notificar(_("No se pudo crear: {0}").format(err))
                return
            notificar(_("Token creado: {0}").format(token))
            self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _borrar(self):
        t = self._actual()
        if not t:
            return
        if confirmar(self, _("¿Borrar el token «{0}»? Quien no lo haya usado ya no podrá "
                             "registrarse con él.").format(t["token"]), _("Confirmar")) != wx.ID_YES:
            return
        acc = self.account_id
        token = t["token"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.admin_borrar_token_registro(acc, token)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
                return
            notificar(_("Token borrado."))
            self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class MatrixAdminFederacionDialog(wx.Dialog):
    """Federación: servidores remotos con los que federa este servidor y su estado de conexión.
    Permite reintentar YA los que están fallando (borra el backoff de reintentos)."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: federación (servidores remotos)"),
                         size=(700, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Federación"))
        self.account_id = account_id
        self.destinos = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Servidores remotos (Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de servidores remotos"))
        self.lista.InsertColumn(0, _("Servidor"), width=260)
        self.lista.InsertColumn(1, _("Estado"), width=140)
        self.lista.InsertColumn(2, _("Fallando desde"), width=160)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc = self.account_id
        speak(_("Consultando la federación..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_federacion_destinos(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿tu cuenta no es administradora?): {0}").format(err))
                return
            self.destinos = res or []
            if not self:  # el diálogo se cerró antes de que terminara la consulta de red
                return
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, d in enumerate(self.destinos):
                falla = bool(d["fallos_desde"])
                estado = _("Con problemas") if falla else _("Bien")
                self.lista.InsertItem(i, d["destino"])
                self.lista.SetItem(i, 1, estado)
                self.lista.SetItem(i, 2, _fecha_ms(d["fallos_desde"]) if falla else "")
                self.lista.establecer_lectura(i, _("{0}: {1}{2}").format(
                    d["destino"], estado,
                    _(", fallando desde {0}").format(_fecha_ms(d["fallos_desde"])) if falla else ""))
            self.lbl.SetLabel(_("{0} servidores remotos (Aplicaciones para acciones):").format(
                len(self.destinos)))
            if self.destinos:
                self.lista.Focus(0)
                self.lista.Select(0)
            notificar(_("{0} servidores remotos.").format(len(self.destinos)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.destinos[i] if 0 <= i < len(self.destinos) else None

    def _on_menu(self, event):
        d = self._actual()
        if not d:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Reintentar la conexión ahora"))
        self.Bind(wx.EVT_MENU, lambda e: self._reset(d), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _reset(self, d):
        acc, destino = self.account_id, d["destino"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_federacion_reset(acc, destino)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Reintentando la conexión con {0}.").format(destino))
                self._cargar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
