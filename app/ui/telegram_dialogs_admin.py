# -*- coding: utf-8 -*-
"""Diálogos de administración y propiedades de chat de Telegram.
"""
import wx
from app.core.privacy import abrir_url

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, mostrar_info, confirmar


class TelegramAdminDialog(wx.Dialog):
    """Administración de un grupo/canal: miembros, título, descripción, invitación y temas."""

    def __init__(self, parent, account_id, chat_id, chat_title, chat_type):
        super().__init__(parent, title=_("Administrar: {0}").format(chat_title), size=(640, 540),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Administración del chat"))
        self.account_id = account_id
        self.chat_id = chat_id
        self.chat_type = chat_type
        self._miembros = []
        self._init_ui()
        self.CenterOnParent()
        self._cargar_miembros()

    # Roles de miembro en español (ChatMemberStatus). Se evalúan al usarse (traducción en runtime).
    @staticmethod
    def _rol_es(rol):
        return {"owner": _("Dueño"), "administrator": _("Administrador"), "member": _("Miembro"),
                "restricted": _("Restringido"), "banned": _("Baneado"),
                "left": _("Salió")}.get(rol, rol or "")

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Cuadro de SOLO LECTURA con toda la información interesante del chat (no se pide nada al
        # entrar; para cambiar el título/descripción se usa el botón "Editar información...").
        sizer.Add(wx.StaticText(self, label=_("Información del canal/grupo:")), 0, wx.LEFT | wx.TOP, 4)
        self.txt_info = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                           size=(-1, 130), name=_("Información del chat"))
        sizer.Add(self.txt_info, 0, wx.EXPAND | wx.ALL, 4)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, label=_("&Editar información..."))
        b.Bind(wx.EVT_BUTTON, self._on_editar_info)
        fila.Add(b, 0, wx.ALL, 4)
        b_env = wx.Button(self, label=_("Enviar &mensaje al canal/grupo..."))
        b_env.Bind(wx.EVT_BUTTON, self._on_enviar_al_chat)
        fila.Add(b_env, 0, wx.ALL, 4)
        b2 = wx.Button(self, label=_("Crear en&lace de invitación"))
        b2.Bind(wx.EVT_BUTTON, self._on_invitacion)
        fila.Add(b2, 0, wx.ALL, 4)
        if self.chat_type == "supergroup":
            b3 = wx.Button(self, label=_("Crear &tema..."))
            b3.Bind(wx.EVT_BUTTON, self._on_crear_tema)
            fila.Add(b3, 0, wx.ALL, 4)
            b4 = wx.Button(self, label=_("Gestionar te&mas..."))
            b4.Bind(wx.EVT_BUTTON, self._on_gestionar_temas)
            fila.Add(b4, 0, wx.ALL, 4)
        sizer.Add(fila, 0, wx.ALL, 2)

        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        b_mas = wx.Button(self, label=_("Más &administración..."))
        b_mas.Bind(wx.EVT_BUTTON, self._on_mas_admin)
        fila2.Add(b_mas, 0, wx.ALL, 4)
        b_sol = wx.Button(self, label=_("&Solicitudes de unión..."))
        b_sol.Bind(wx.EVT_BUTTON, self._on_solicitudes)
        fila2.Add(b_sol, 0, wx.ALL, 4)
        b_enl = wx.Button(self, label=_("En&laces de invitación..."))
        b_enl.Bind(wx.EVT_BUTTON, self._on_enlaces)
        fila2.Add(b_enl, 0, wx.ALL, 4)
        b_reg = wx.Button(self, label=_("&Registro de admin..."))
        b_reg.Bind(wx.EVT_BUTTON, self._on_registro_admin)
        fila2.Add(b_reg, 0, wx.ALL, 4)
        sizer.Add(fila2, 0, wx.ALL, 2)

        # Lista de miembros: las columnas las define la plantilla "telegram_miembro" (Herramientas).
        sizer.Add(wx.StaticText(self, label=_("Miembros (menú contextual para administrar):")), 0, wx.ALL, 4)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de miembros"))
        from app.core.plantillas import columnas_de_plantilla, definicion
        from app.core.config_manager import config as _cfg
        plantilla = _cfg.get("accessibility", "telegram_miembro", "") or definicion("telegram_miembro")["default"]
        self._cols_miembros = columnas_de_plantilla(plantilla) or columnas_de_plantilla(
            definicion("telegram_miembro")["default"])
        anchos = {"nombre": 220, "username": 160, "rol": 130, "presencia": 200, "titulo": 140,
                  "tipo": 70, "id": 120}
        for i, (campo, enc) in enumerate(self._cols_miembros):
            self.lista.InsertColumn(i, enc, width=anchos.get(campo, 140))
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu_miembro)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 4)

        sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self._info = {}
        self._cargar_info()

    def _cargar_info(self):
        """Rellena el cuadro de solo lectura con la info del chat (y guarda título/descripción
        actuales para precargarlos al editar)."""
        def _bg(ctx):
            return telegram_service.obtener_info_chat(self.account_id, self.chat_id)
        def _finish(res, err):
            if err or not res:
                self.txt_info.SetValue(_("No se pudo cargar la información: {0}").format(err or ""))
                return
            self._info = res
            tipos = {"group": _("Grupo"), "supergroup": _("Supergrupo"), "channel": _("Canal")}
            L = [_("Nombre: {0}").format(res.get("title", "")),
                 _("Tipo: {0}").format(tipos.get(res.get("type"), res.get("type", "")))]
            if res.get("username"):
                L.append(_("Usuario público: {0}").format(res["username"]))
            if res.get("members_count"):
                L.append(_("Miembros: {0}").format(res["members_count"]))
            if res.get("online_count"):
                L.append(_("En línea: {0}").format(res["online_count"]))
            if res.get("es_foro"):
                L.append(_("Temas (foros): activados"))
            if res.get("protegido"):
                L.append(_("Contenido protegido: no se puede reenviar"))
            if res.get("chat_vinculado") and res["chat_vinculado"].get("titulo"):
                etq = _("Grupo de comentarios: {0}") if res.get("type") == "channel" \
                    else _("Canal asociado: {0}")
                L.append(etq.format(res["chat_vinculado"]["titulo"]))
            if res.get("verificado"):
                L.append(_("Verificado"))
            if res.get("bio"):
                L.append("")
                L.append(_("Descripción: {0}").format(res["bio"]))
            self.txt_info.SetValue("\n".join(L))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _cargar_miembros(self):
        speak(_("Cargando miembros..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.obtener_miembros(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error al cargar miembros: {0}").format(err))
                return
            self._miembros = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            from app.core.plantillas import pulir_lectura, definicion
            from app.core.config_manager import config as _cfg
            from collections import defaultdict
            plantilla = _cfg.get("accessibility", "telegram_miembro", "") or definicion("telegram_miembro")["default"]
            for i, m in enumerate(self._miembros):
                vals = {
                    "nombre": m.get("nombre", ""),
                    "username": m.get("username", ""),
                    "rol": self._rol_es(m.get("rol", "")),
                    "presencia": m.get("presencia", ""),
                    "titulo": m.get("titulo", ""),
                    "tipo": _("Bot") if m.get("bot") else "",
                    "id": str(m.get("id", "")),
                }
                pos = None
                for c, (campo, _e) in enumerate(self._cols_miembros):
                    val = vals.get(campo, "")
                    if c == 0:
                        pos = self.lista.InsertItem(i, val)
                    else:
                        self.lista.SetItem(pos, c, val)
                self.lista.establecer_lectura(pos if pos is not None else i,
                                              pulir_lectura(plantilla.format_map(defaultdict(str, vals))))
            speak(_("{0} miembros.").format(len(self._miembros)), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_editar_info(self, event):
        """Edita el título y la descripción del chat, precargados con los valores actuales."""
        dlg = wx.Dialog(self, title=_("Editar información"), style=wx.DEFAULT_DIALOG_STYLE,
                        name=_("Editar información del chat"))
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label=_("Título:")), 0, wx.ALL, 4)
        t_tit = AccessibleTextCtrl(dlg, name=_("Título"))
        t_tit.SetValue(self._info.get("title", ""))
        s.Add(t_tit, 0, wx.EXPAND | wx.ALL, 4)
        s.Add(wx.StaticText(dlg, label=_("Descripción:")), 0, wx.ALL, 4)
        t_desc = AccessibleTextCtrl(dlg, style=wx.TE_MULTILINE, size=(-1, 120), name=_("Descripción"))
        t_desc.SetValue(self._info.get("bio", ""))
        s.Add(t_desc, 1, wx.EXPAND | wx.ALL, 4)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        btns.Add(wx.Button(dlg, wx.ID_OK, _("&Guardar")), 0, wx.ALL, 5)
        btns.Add(wx.Button(dlg, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT)
        dlg.SetSizerAndFit(s)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        titulo = t_tit.GetValue().strip()
        desc = t_desc.GetValue().strip()
        dlg.Destroy()
        def _bg(ctx):
            if titulo and titulo != self._info.get("title", ""):
                telegram_service.editar_titulo_chat(self.account_id, self.chat_id, titulo)
            if desc != self._info.get("bio", ""):
                telegram_service.editar_descripcion_chat(self.account_id, self.chat_id, desc)
            return True
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Información actualizada."))
                self._cargar_info()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_enviar_al_chat(self, event):
        """Publica un mensaje en el canal/grupo (notificación a todos sus miembros)."""
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Mensaje para enviar al canal/grupo:"),
                                  _("Enviar mensaje"), multiline=True)
        if d.ShowModal() != wx.ID_OK:
            d.Destroy(); return
        texto = d.GetValue().strip()
        d.Destroy()
        if not texto:
            return
        def _bg(ctx):
            return telegram_service.enviar_mensaje(self.account_id, self.chat_id, texto)
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else _("Mensaje enviado."))
        speak(_("Enviando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_invitacion(self, event):
        def _bg(ctx):
            return telegram_service.crear_enlace_invitacion(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                if wx.TheClipboard.Open():
                    wx.TheClipboard.SetData(wx.TextDataObject(res))
                    wx.TheClipboard.Close()
                notificar(_("Enlace de invitación creado y copiado al portapapeles."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_gestionar_temas(self, event):
        """Lista los temas del foro y permite renombrar, cerrar/reabrir, fijar o borrar."""
        def _bg(ctx):
            return telegram_service.obtener_temas(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error al cargar temas: {0}").format(err))
                return
            if not res:
                speak(_("No hay temas en este grupo."), interrupt=True)
                return
            etiquetas = [t.get("title") or str(t.get("id")) for t in res]
            dlg = wx.SingleChoiceDialog(self, _("Elija un tema:"), _("Gestionar temas"), etiquetas)
            if dlg.ShowModal() == wx.ID_OK:
                self._accion_sobre_tema(res[dlg.GetSelection()])
            dlg.Destroy()
        speak(_("Cargando temas..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _accion_sobre_tema(self, tema):
        tid = tema.get("id")
        acciones = [
            (_("Renombrar"), "renombrar"),
            (_("Cerrar"), "cerrar"),
            (_("Reabrir"), "reabrir"),
            (_("Fijar"), "fijar"),
            (_("Desfijar"), "desfijar"),
            (_("Borrar"), "borrar"),
        ]
        dlg = wx.SingleChoiceDialog(self, _("Acción sobre «{0}»:").format(tema.get("title", "")),
                                    _("Tema"), [etq for etq, _a in acciones])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        accion = acciones[dlg.GetSelection()][1]
        dlg.Destroy()
        nombre = None
        if accion == "renombrar":
            from .accessible_controls import CustomTextEntryDialog
            d = CustomTextEntryDialog(self, _("Nuevo título:"), _("Renombrar tema"),
                                      value=tema.get("title", ""))
            nombre = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
            d.Destroy()
            if not nombre:
                return
        if accion == "borrar":
            c = wx.MessageDialog(self, _("¿Borrar el tema y sus mensajes?"), _("Confirmar"),
                                 wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            ok = c.ShowModal() == wx.ID_YES
            c.Destroy()
            if not ok:
                return
        def _bg(ctx):
            if accion == "renombrar":
                return telegram_service.editar_tema(self.account_id, self.chat_id, tid, nombre)
            if accion == "cerrar":
                return telegram_service.cerrar_tema(self.account_id, self.chat_id, tid, cerrar=True)
            if accion == "reabrir":
                return telegram_service.cerrar_tema(self.account_id, self.chat_id, tid, cerrar=False)
            if accion == "fijar":
                return telegram_service.fijar_tema(self.account_id, self.chat_id, tid, fijar=True)
            if accion == "desfijar":
                return telegram_service.fijar_tema(self.account_id, self.chat_id, tid, fijar=False)
            if accion == "borrar":
                return telegram_service.borrar_tema(self.account_id, self.chat_id, tid)
            return None
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else _("Hecho."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_crear_tema(self, event):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Título del nuevo tema:"), _("Crear tema"))
        if dlg.ShowModal() == wx.ID_OK:
            titulo = dlg.GetValue().strip()
            if titulo:
                def _bg(ctx):
                    return telegram_service.crear_tema(self.account_id, self.chat_id, titulo)
                def _finish(res, err):
                    notificar(_("Error: {0}").format(err) if err else _("Tema creado."))
                task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_mas_admin(self, event):
        """Menú con la administración avanzada del grupo/canal (§J)."""
        es_canal = self.chat_type == "channel"
        menu = wx.Menu()
        acciones = [
            (_("Cambiar foto..."), self._adm_foto),
            (_("Quitar foto"), self._adm_quitar_foto),
            (_("Username público..."), self._adm_username),
            (_("Autodestrucción de mensajes..."), self._adm_ttl),
        ]
        if not es_canal:
            acciones.insert(3, (_("Modo lento..."), self._adm_modo_lento))
            acciones.append((_("Añadir miembros..."), self._adm_anadir_miembros))
        # Estado real del chat (cargado al abrir el diálogo) para que estos pares sean DINÁMICOS.
        info = getattr(self, "_info", {}) or {}
        protegido = bool(info.get("protegido"))
        es_foro = bool(info.get("es_foro"))
        tiene_comentarios = bool(info.get("chat_vinculado") and info["chat_vinculado"].get("titulo"))
        # Contenido protegido (no reenviar): una sola opción según el estado real.
        acciones.append((None, None))
        if protegido:
            acciones.append((_("Permitir reenvíos de nuevo (ahora protegido)"), lambda: self._adm_reenvios(False)))
        else:
            acciones.append((_("Restringir reenvíos (proteger contenido)"), lambda: self._adm_reenvios(True)))
        # Temas (modo foro): solo en grupos/supergrupos, y según estén activados o no.
        if not es_canal:
            if es_foro:
                acciones.append((_("Desactivar temas (foros)"), lambda: self._adm_temas(False)))
            else:
                acciones.append((_("Activar temas (foros)"), lambda: self._adm_temas(True)))
        # Grupo de comentarios: solo en canales, según haya uno vinculado o no.
        if es_canal:
            if tiene_comentarios:
                acciones.append((_("Quitar grupo de comentarios"), self._adm_quitar_comentarios))
            else:
                acciones.append((_("Vincular grupo de comentarios..."), self._adm_vincular_comentarios))
        acciones.append((None, None))
        acciones.append((_("Transferir propiedad..."), self._adm_transferir))
        acciones.append((_("Cerrar y eliminar (canal/grupo/supergrupo)..."), self._adm_eliminar))
        for etiqueta, handler in acciones:
            if etiqueta is None:
                menu.AppendSeparator(); continue
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, h=handler: h(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _adm_run(self, fn, ok_msg, enviando=None):
        if enviando:
            speak(enviando, interrupt=True)
        def _bg(ctx):
            return fn()
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else ok_msg)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _adm_foto(self):
        dlg = wx.FileDialog(self, _("Elija la imagen de perfil"),
                            wildcard=_("Imágenes") + "|*.jpg;*.jpeg;*.png",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        ruta = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        if ruta:
            self._adm_run(lambda: telegram_service.establecer_foto_chat(self.account_id, self.chat_id, ruta),
                          _("Foto actualizada."), _("Subiendo foto..."))

    def _adm_quitar_foto(self):
        self._adm_run(lambda: telegram_service.borrar_foto_chat(self.account_id, self.chat_id),
                      _("Foto eliminada."))

    def _adm_username(self):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Username público (vacío para quitarlo):"), _("Username del chat"))
        if d.ShowModal() != wx.ID_OK:
            d.Destroy(); return
        user = d.GetValue().strip().lstrip("@")
        d.Destroy()
        self._adm_run(lambda: telegram_service.establecer_username_chat(self.account_id, self.chat_id, user),
                      _("Username actualizado."))

    def _adm_modo_lento(self):
        opciones = [("0", _("Desactivado")), ("10", _("10 segundos")), ("30", _("30 segundos")),
                    ("60", _("1 minuto")), ("300", _("5 minutos")), ("900", _("15 minutos")),
                    ("3600", _("1 hora"))]
        dlg = wx.SingleChoiceDialog(self, _("Intervalo del modo lento:"), _("Modo lento"),
                                    [etq for _s, etq in opciones])
        if dlg.ShowModal() == wx.ID_OK:
            seg = int(opciones[dlg.GetSelection()][0])
            self._adm_run(lambda: telegram_service.establecer_modo_lento(self.account_id, self.chat_id, seg),
                          _("Modo lento configurado."))
        dlg.Destroy()

    def _adm_ttl(self):
        opciones = [("0", _("Desactivado")), ("86400", _("1 día")), ("604800", _("7 días")),
                    ("2678400", _("1 mes"))]
        dlg = wx.SingleChoiceDialog(self, _("Autoborrado de mensajes:"), _("Autodestrucción"),
                                    [etq for _s, etq in opciones])
        if dlg.ShowModal() == wx.ID_OK:
            seg = int(opciones[dlg.GetSelection()][0])
            self._adm_run(lambda: telegram_service.establecer_autodestruccion_chat(self.account_id, self.chat_id, seg),
                          _("Autodestrucción configurada."))
        dlg.Destroy()

    def _adm_reenvios(self, restringir):
        msg = _("Reenvíos restringidos: el contenido ya no se puede reenviar.") if restringir \
            else _("Reenvíos permitidos de nuevo.")
        self._adm_run(lambda: telegram_service.restringir_reenvios(self.account_id, self.chat_id, restringir), msg)

    def _adm_temas(self, activar):
        msg = _("Temas (foros) activados.") if activar else _("Temas (foros) desactivados.")
        self._adm_run(lambda: telegram_service.activar_temas_foro(self.account_id, self.chat_id, activar), msg)

    def _adm_quitar_comentarios(self):
        self._adm_run(lambda: telegram_service.desvincular_grupo_discusion(self.account_id, self.chat_id),
                      _("Grupo de comentarios quitado."))

    def _adm_vincular_comentarios(self):
        speak(_("Buscando grupos que puedes vincular..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.chats_aptos_discusion(self.account_id)
        def _finish(grupos, err):
            if err:
                notificar(_("Error: {0}").format(err)); return
            if not grupos:
                mostrar_info(self, _("No tienes ningún grupo apto. El grupo de comentarios debe ser un "
                                     "grupo del que seas administrador y que admita historial visible."),
                             _("Grupo de comentarios")); return
            etiquetas = [g["titulo"] for g in grupos]
            dlg = wx.SingleChoiceDialog(self, _("Elige el grupo de comentarios para el canal:"),
                                        _("Vincular grupo de comentarios"), etiquetas)
            if dlg.ShowModal() == wx.ID_OK:
                g = grupos[dlg.GetSelection()]
                self._adm_run(lambda: telegram_service.vincular_grupo_discusion(self.account_id, self.chat_id, g["id"]),
                              _("Grupo de comentarios vinculado."))
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _adm_anadir_miembros(self):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Usuarios a añadir (@usuario o id, uno por línea):"),
                                  _("Añadir miembros"), multiline=True)
        if d.ShowModal() != wx.ID_OK:
            d.Destroy(); return
        ids = []
        for linea in d.GetValue().splitlines():
            v = linea.strip()
            if v:
                ids.append(int(v) if v.lstrip("-").isdigit() else v)
        d.Destroy()
        if not ids:
            return
        self._adm_run(lambda: telegram_service.añadir_miembros(self.account_id, self.chat_id, ids),
                      _("Miembros añadidos."), _("Añadiendo..."))

    def _adm_transferir(self):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("ID o @usuario del nuevo propietario:"), _("Transferir propiedad"))
        dest = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not dest:
            return
        d2 = CustomTextEntryDialog(self, _("Tu contraseña de verificación en dos pasos:"), _("Confirmar"))
        pwd = d2.GetValue() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if not pwd:
            return
        destino = int(dest) if dest.lstrip("-").isdigit() else dest.lstrip("@")
        self._adm_run(lambda: telegram_service.transferir_propiedad(self.account_id, self.chat_id, destino, pwd),
                      _("Propiedad transferida."))

    def _adm_eliminar(self):
        etq = {"channel": _("el canal"), "supergroup": _("el supergrupo"),
               "group": _("el grupo")}.get(self.chat_type, _("el grupo/canal"))
        dlg = wx.MessageDialog(self, _("¿Cerrar y eliminar {0} por completo? No se puede deshacer.").format(etq),
                               _("Eliminar"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        ok = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        if not ok:
            return
        tipo = self.chat_type
        def _bg(ctx):
            return telegram_service.eliminar_chat(self.account_id, self.chat_id, tipo=tipo)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Eliminado."))
                # Refrescar la ventana principal para que el chat eliminado desaparezca de la lista
                # y del árbol (la fila local ya se borró en el servicio).
                try:
                    from app.core.app_context import app_context
                    f = app_context.main_frame
                    if f is not None:
                        f._cargar_navegacion()
                        if getattr(f, "active_telegram_account_id", None) == self.account_id:
                            f._cargar_listado_chats_telegram()
                except Exception:
                    pass
                self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_solicitudes(self, event):
        def _bg(ctx):
            return telegram_service.listar_solicitudes_union(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("No hay solicitudes de unión pendientes."), interrupt=True)
                return
            etiquetas = [f"{s['nombre']} — {s['fecha']}" for s in res]
            etiquetas += [_("» Aprobar TODAS"), _("» Rechazar TODAS")]
            dlg = wx.SingleChoiceDialog(self, _("Solicitudes de unión:"), _("Solicitudes"), etiquetas)
            if dlg.ShowModal() == wx.ID_OK:
                sel = dlg.GetSelection()
                if sel == len(res):
                    self._adm_run(lambda: telegram_service.aprobar_todas_solicitudes(self.account_id, self.chat_id),
                                  _("Todas aprobadas."))
                elif sel == len(res) + 1:
                    self._adm_run(lambda: telegram_service.rechazar_todas_solicitudes(self.account_id, self.chat_id),
                                  _("Todas rechazadas."))
                else:
                    uid = res[sel]["user_id"]
                    nombre = res[sel]["nombre"]
                    acc = wx.MessageBox(_("¿Aprobar a {0}? (No para rechazar)").format(nombre),
                                        _("Solicitud"), wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
                    if acc == wx.YES:
                        self._adm_run(lambda: telegram_service.aprobar_solicitud(self.account_id, self.chat_id, uid),
                                      _("Aprobada."))
                    elif acc == wx.NO:
                        self._adm_run(lambda: telegram_service.rechazar_solicitud(self.account_id, self.chat_id, uid),
                                      _("Rechazada."))
            dlg.Destroy()
        speak(_("Consultando solicitudes..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_enlaces(self, event):
        def _bg(ctx):
            return telegram_service.listar_enlaces_invitacion(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("No hay enlaces de invitación."), interrupt=True)
                return
            etiquetas = []
            for l in res:
                estado = _("revocado") if l["revocado"] else _("{0} miembros").format(l["miembros"])
                etiquetas.append(f"{l['nombre'] or l['enlace']} ({estado})")
            dlg = wx.SingleChoiceDialog(self, _("Enlaces de invitación (Aceptar para gestionar):"),
                                        _("Enlaces"), etiquetas)
            if dlg.ShowModal() == wx.ID_OK:
                l = res[dlg.GetSelection()]
                self._gestionar_enlace(l)
            dlg.Destroy()
        speak(_("Consultando enlaces..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _gestionar_enlace(self, enlace):
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(enlace["enlace"]))
            wx.TheClipboard.Close()
        opciones = [_("Copiado al portapapeles (cerrar)"), _("Ver quién se unió..."), _("Revocar"), _("Borrar")]
        dlg = wx.SingleChoiceDialog(self, _("Acción sobre el enlace:"), _("Enlace"), opciones)
        if dlg.ShowModal() == wx.ID_OK:
            sel = dlg.GetSelection()
            if sel == 1:
                self._ver_quien_se_unio(enlace["enlace"])
            elif sel == 2:
                self._adm_run(lambda: telegram_service.revocar_enlace_invitacion(self.account_id, self.chat_id, enlace["enlace"]),
                              _("Enlace revocado."))
            elif sel == 3:
                self._adm_run(lambda: telegram_service.borrar_enlace_invitacion(self.account_id, self.chat_id, enlace["enlace"]),
                              _("Enlace borrado."))
        dlg.Destroy()

    def _ver_quien_se_unio(self, enlace):
        speak(_("Consultando quién se unió..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.quien_se_unio_por_enlace(self.account_id, self.chat_id, enlace)
        def _finish(personas, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not personas:
                speak(_("Nadie se ha unido todavía por este enlace."), interrupt=True)
                return
            texto = "\n".join(f"{n} {u}".strip() + (f" — {f}" if f else "") for n, u, f in personas)
            d = wx.MessageDialog(self, _("Se unieron {0} personas por este enlace:\n\n{1}").format(len(personas), texto),
                                 _("Quién se unió"), wx.OK | wx.ICON_INFORMATION)
            d.ShowModal()
            d.Destroy()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_registro_admin(self, event):
        def _bg(ctx):
            return telegram_service.obtener_registro_admin(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("El registro está vacío."))
                return
            texto = "\n\n".join(_("{0}:\n{1}").format(e["fecha"], e["accion"]) for e in res)
            vd = wx.Dialog(self, title=_("Registro de administración"), size=(640, 460),
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
            s = wx.BoxSizer(wx.VERTICAL)
            t = AccessibleTextCtrl(vd, style=wx.TE_MULTILINE | wx.TE_READONLY, name=_("Registro"))
            t.SetValue(texto)
            s.Add(t, 1, wx.EXPAND | wx.ALL, 8)
            s.Add(wx.Button(vd, wx.ID_OK, _("Aceptar")), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
            vd.SetSizer(s)
            vd.Bind(wx.EVT_CLOSE, lambda e: vd.EndModal(wx.ID_OK))
            vd.ShowModal()
            vd.Destroy()
        speak(_("Cargando registro..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_menu_miembro(self, event):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self._miembros):
            return
        m = self._miembros[idx]
        uid = m.get("id")
        # Menú DINÁMICO según el estado real del miembro: no ofrecer "Quitar baneo" a quien no está
        # baneado, ni "Promover" a quien ya es administrador, etc. Así la acción nunca es ambigua.
        rol = (m.get("rol") or "").lower()
        es_dueno = rol == "owner"
        es_admin = rol == "administrator"
        esta_baneado = rol == "banned"
        esta_restringido = rol == "restricted"
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Enviar mensaje a este usuario..."))
        self.Bind(wx.EVT_MENU, lambda e: self._enviar_a_miembro(uid, m.get("nombre", "")), it)
        it = menu.Append(wx.ID_ANY, _("Ver estado y datos"))
        self.Bind(wx.EVT_MENU, lambda e: self._ver_datos_miembro(m), it)
        if es_dueno:
            # Al dueño no se le puede promover/degradar/banear: solo mensaje y datos.
            self.lista.PopupMenu(menu)
            menu.Destroy()
            return
        menu.AppendSeparator()
        # ADMINISTRADOR: degradar / cambiar permisos / título. MIEMBRO: promover.
        if es_admin:
            it = menu.Append(wx.ID_ANY, _("Quitar administrador (degradar a miembro)"))
            self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("degradar", uid), it)
            it = menu.Append(wx.ID_ANY, _("Cambiar sus permisos de administrador..."))
            self.Bind(wx.EVT_MENU, lambda e: self._promover_con_permisos(uid), it)
            it = menu.Append(wx.ID_ANY, _("Título de administrador..."))
            self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("titulo", uid), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Promover a administrador"))
            self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("promover", uid), it)
            it = menu.Append(wx.ID_ANY, _("Promover con permisos concretos..."))
            self.Bind(wx.EVT_MENU, lambda e: self._promover_con_permisos(uid), it)
        menu.AppendSeparator()
        # BANEO / RESTRICCIÓN: la acción cambia según el estado actual.
        if esta_baneado:
            it = menu.Append(wx.ID_ANY, _("Quitar el baneo (volver a admitir)"))
            self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("desbanear", uid), it)
        else:
            if esta_restringido:
                it = menu.Append(wx.ID_ANY, _("Quitar restricciones (podrá volver a escribir)"))
                self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("quitar_restriccion", uid), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Silenciar (no podrá escribir)"))
                self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("silenciar", uid), it)
            it = menu.Append(wx.ID_ANY, _("Banear (expulsar del grupo)"))
            self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("banear", uid), it)
        it = menu.Append(wx.ID_ANY, _("Borrar todos sus mensajes (limpiar spam)"))
        self.Bind(wx.EVT_MENU, lambda e: self._accion_miembro("borrar_mensajes", uid), it)
        self.lista.PopupMenu(menu)
        menu.Destroy()

    def _accion_miembro(self, accion, uid):
        titulo_admin = None
        if accion == "titulo":
            from .accessible_controls import CustomTextEntryDialog
            d = CustomTextEntryDialog(self, _("Título personalizado del administrador:"), _("Título de administrador"))
            titulo_admin = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else None
            d.Destroy()
            if not titulo_admin:
                return
        def _bg(ctx):
            if accion == "promover":
                return telegram_service.promover_admin(self.account_id, self.chat_id, uid)
            if accion == "degradar":
                return telegram_service.degradar_admin(self.account_id, self.chat_id, uid)
            if accion == "banear":
                return telegram_service.banear_miembro(self.account_id, self.chat_id, uid)
            if accion == "desbanear":
                return telegram_service.desbanear_miembro(self.account_id, self.chat_id, uid)
            if accion == "silenciar":
                return telegram_service.silenciar_miembro(self.account_id, self.chat_id, uid)
            if accion == "quitar_restriccion":
                return telegram_service.quitar_restriccion_miembro(self.account_id, self.chat_id, uid)
            if accion == "borrar_mensajes":
                return telegram_service.borrar_mensajes_de_usuario(self.account_id, self.chat_id, uid)
            if accion == "titulo":
                return telegram_service.titulo_administrador(self.account_id, self.chat_id, uid, titulo_admin)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                speak(_("Acción realizada."), interrupt=True)
                self._cargar_miembros()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _promover_con_permisos(self, uid):
        """Abre un diálogo con una casilla por cada permiso de administrador (precargado con los
        permisos actuales del usuario) y promueve con exactamente esos permisos."""
        campos = telegram_service.PRIVILEGIOS_ADMIN
        speak(_("Cargando permisos actuales..."), interrupt=True)

        def _bg(ctx):
            try:
                return telegram_service.obtener_privilegios_admin(self.account_id, self.chat_id, uid)
            except Exception:
                return {}

        def _finish(actuales, err):
            actuales = actuales or {}
            dlg = wx.Dialog(self, title=_("Permisos de administrador"),
                            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                            name=_("Permisos de administrador"))
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(wx.StaticText(dlg, label=_("Marca los permisos que tendrá este administrador:")),
                  0, wx.ALL, 8)
            casillas = {}
            for campo, etiqueta in campos:
                cb = wx.CheckBox(dlg, label=_(etiqueta), name=_(etiqueta))
                # Por defecto, un conjunto razonable si el usuario aún no es admin.
                pred = actuales.get(campo, campo in (
                    "can_manage_chat", "can_delete_messages", "can_restrict_members",
                    "can_invite_users", "can_pin_messages")) if actuales else campo in (
                    "can_manage_chat", "can_delete_messages", "can_restrict_members",
                    "can_invite_users", "can_pin_messages")
                cb.SetValue(bool(pred))
                casillas[campo] = cb
                s.Add(cb, 0, wx.ALL, 4)
            btns = wx.BoxSizer(wx.HORIZONTAL)
            btns.Add(wx.Button(dlg, wx.ID_OK, _("&Aceptar")), 0, wx.ALL, 5)
            btns.Add(wx.Button(dlg, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
            s.Add(btns, 0, wx.ALIGN_RIGHT)
            dlg.SetSizerAndFit(s)
            if dlg.ShowModal() == wx.ID_OK:
                privilegios = {campo: casillas[campo].GetValue() for campo, _e in campos}
                dlg.Destroy()
                def _bg2(ctx):
                    return telegram_service.promover_admin(self.account_id, self.chat_id, uid, privilegios)
                def _fin2(res, err2):
                    if err2:
                        notificar(_("Error: {0}").format(err2))
                    else:
                        speak(_("Permisos aplicados."), interrupt=True)
                        self._cargar_miembros()
                task_manager.start_task(_bg2, callback=_fin2, pass_context=True)
            else:
                dlg.Destroy()

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _enviar_a_miembro(self, uid, nombre):
        """Envía un mensaje directo al usuario seleccionado de la lista de miembros."""
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Mensaje para {0}:").format(nombre or uid),
                                  _("Enviar mensaje"), multiline=True)
        if d.ShowModal() != wx.ID_OK:
            d.Destroy(); return
        texto = d.GetValue().strip()
        d.Destroy()
        if not texto:
            return
        def _bg(ctx):
            return telegram_service.enviar_mensaje(self.account_id, uid, texto)
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else _("Mensaje enviado."))
        speak(_("Enviando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _ver_datos_miembro(self, m):
        """Muestra en un cuadro de solo lectura todos los datos del miembro (rol, estado, etc.)."""
        L = [_("Nombre: {0}").format(m.get("nombre", ""))]
        if m.get("username"):
            L.append(_("Usuario: {0}").format(m["username"]))
        L.append(_("Identificador: {0}").format(m.get("id", "")))
        L.append(_("Rol: {0}").format(self._rol_es(m.get("rol", ""))))
        if m.get("titulo"):
            L.append(_("Título de administrador: {0}").format(m["titulo"]))
        if m.get("presencia"):
            L.append(_("Estado: {0}").format(m["presencia"]))
        if m.get("bot"):
            L.append(_("Es un bot"))
        mostrar_info(self, "\n".join(L), _("Datos del miembro"))


class TelegramPropiedadesDialog(wx.Dialog):
    """Propiedades de un chat: información/perfil y multimedia compartida por pestañas."""

    CATEGORIAS = [
        ("photo", "Fotos"), ("video", "Vídeos"), ("audio", "Audios"),
        ("voice", "Notas de voz"), ("document", "Documentos"), ("url", "Enlaces"),
    ]

    def __init__(self, parent, account_id, chat_id, chat_title):
        super().__init__(parent, title=_("Propiedades: {0}").format(chat_title), size=(640, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Propiedades del chat de Telegram"))
        self.account_id = account_id
        self.chat_id = chat_id
        self.chat_title = chat_title
        self._listas = {}
        self._init_ui()
        self.CenterOnParent()
        self._cargar_info()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self, name=_("Pestañas de propiedades"))

        # Pestaña Información
        panel_info = wx.Panel(self.notebook)
        s_info = wx.BoxSizer(wx.VERTICAL)
        self.txt_info = AccessibleTextCtrl(panel_info, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                           name=_("Información del chat"))
        s_info.Add(self.txt_info, 1, wx.EXPAND | wx.ALL, 5)
        panel_info.SetSizer(s_info)
        self.notebook.AddPage(panel_info, _("Información"))

        # Pestañas de multimedia compartida
        for cat, etiqueta in self.CATEGORIAS:
            panel = wx.Panel(self.notebook)
            s = wx.BoxSizer(wx.VERTICAL)
            lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                       name=_("Lista de {0}").format(etiqueta))
            lista.InsertColumn(0, _("Elemento"), width=360)
            lista.InsertColumn(1, _("Fecha"), width=120)
            # La columna de estado solo tiene sentido en los adjuntos descargables (no en Enlaces).
            if cat != "url":
                lista.InsertColumn(2, _("Estado"), width=120)
            lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e, c=cat: self._on_abrir_media(c, e.GetIndex()))
            lista.Bind(wx.EVT_CONTEXT_MENU, lambda e, c=cat: self._on_menu_media(c))
            lista.Bind(wx.EVT_KEY_DOWN, lambda e, c=cat: self._on_media_key(e, c))
            lista.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e, c=cat: self._on_media_selected(c))
            s.Add(lista, 1, wx.EXPAND | wx.ALL, 5)
            panel.SetSizer(s)
            self.notebook.AddPage(panel, _(etiqueta))
            self._listas[cat] = {"lista": lista, "cargado": False, "items": []}

        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_page)
        sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)

        # Buscador de la PESTAÑA activa: filtra solo lo de esa pestaña; al vaciarlo, vuelve a mostrar
        # todo. No aplica a la pestaña Información (es texto).
        fila_b = wx.BoxSizer(wx.HORIZONTAL)
        fila_b.Add(wx.StaticText(self, label=_("&Buscar en esta pestaña:")), 0,
                   wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.txt_buscar_media = AccessibleTextCtrl(
            self, name=_("Buscar en la pestaña actual"),
            ayuda_texto=_("Filtra lo que se ve en la pestaña elegida. Vacíalo para ver todo de nuevo."))
        self.txt_buscar_media.Bind(wx.EVT_TEXT, self._on_buscar_pestana)
        fila_b.Add(self.txt_buscar_media, 1, wx.EXPAND | wx.ALL, 4)
        self.lbl_buscar = wx.StaticText(self, label="")
        fila_b.Add(self.lbl_buscar, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        sizer.Add(fila_b, 0, wx.EXPAND)

        sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)

    def _cargar_info(self):
        def _bg(ctx):
            return telegram_service.obtener_info_chat(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                self.txt_info.SetValue(_("Error al cargar la información: {0}").format(err))
                return
            tipos = {"private": _("Usuario"), "bot": _("Bot"), "group": _("Grupo"),
                     "supergroup": _("Supergrupo"), "channel": _("Canal")}
            lineas = [
                _("Nombre: {0}").format(res.get("title", "")),
                _("Tipo: {0}").format(tipos.get(res.get("type"), res.get("type", ""))),
            ]
            if res.get("username"):
                lineas.append(_("Usuario: {0}").format(res["username"]))
            if res.get("telefono"):
                lineas.append(_("Teléfono: {0}").format(res["telefono"]))
            if res.get("estado"):
                lineas.append(_("Estado: {0}").format(res["estado"]))
            if res.get("members_count"):
                lineas.append(_("Miembros: {0}").format(res["members_count"]))
            if res.get("online_count"):
                lineas.append(_("En línea: {0}").format(res["online_count"]))
            if res.get("es_foro"):
                lineas.append(_("Temas (foros): activados"))
            if res.get("protegido"):
                lineas.append(_("Contenido protegido: no se puede reenviar"))
            if res.get("chat_vinculado") and res["chat_vinculado"].get("titulo"):
                etq = _("Grupo de comentarios: {0}") if res.get("type") == "channel" \
                    else _("Canal asociado: {0}")
                lineas.append(etq.format(res["chat_vinculado"]["titulo"]))
            if res.get("verificado"):
                lineas.append(_("Cuenta verificada"))
            if res.get("bio"):
                lineas.append("")
                lineas.append(res["bio"])
            self.txt_info.SetValue("\n".join(lineas))
            speak(_("Información cargada."), interrupt=True)
        speak(_("Cargando información..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_page(self, event):
        idx = event.GetSelection()
        # Al cambiar de pestaña, vaciar el buscador (cada pestaña empieza mostrando TODO). Se
        # deshabilita en la pestaña Información (no es una lista).
        try:
            self.txt_buscar_media.ChangeValue("")  # ChangeValue no dispara EVT_TEXT
            self.lbl_buscar.SetLabel("")
            self.txt_buscar_media.Enable(idx >= 1)
        except Exception:
            pass
        if idx == 0:
            event.Skip()
            return
        cat = self.CATEGORIAS[idx - 1][0]
        info = self._listas[cat]
        if not info["cargado"]:
            self._cargar_categoria(cat)
        elif self.txt_buscar_media.GetValue().strip():
            # Ya estaba cargada: reaplicar el estado sin filtro (mostrar todo).
            self._pintar_categoria(cat, info["items"])
        event.Skip()

    def _cargar_categoria(self, cat):
        info = self._listas[cat]
        info["cargado"] = True
        speak(_("Cargando..."), interrupt=True)

        def _bg(ctx):
            # limit=0 = TODO (antes se quedaba en 100 y se veía parcial).
            return telegram_service.obtener_media_compartida(self.account_id, self.chat_id, cat,
                                                             limit=0)

        def _finish(res, err):
            if not self:
                return
            if err:
                notificar(_("Error: {0}").format(err))
                return
            info["items"] = res or []
            self._pintar_categoria(cat, info["items"], anunciar=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _pintar_categoria(self, cat, items, anunciar=False):
        """Vuelca 'items' a la lista de la pestaña 'cat' (Freeze para que sea rápido con muchos)."""
        from .main_frame import formatear_tiempo_relativo
        lista = self._listas[cat]["lista"]
        tiene_estado = cat != "url"
        lista.Freeze()
        try:
            lista.DeleteAllItems()
            lista.limpiar_lecturas()
            for i, it in enumerate(items or []):
                # Mostrar el NOMBRE del archivo y, si tiene comentario (pie), también el comentario.
                # Antes, al haber file_name (documentos, audios...) se ocultaba el comentario; en
                # fotos sí salía porque no tienen nombre. Ahora se ve siempre que exista.
                nombre = it.get("file_name") or ""
                comentario = (it.get("caption") or "").replace("\n", " ")
                if nombre:
                    texto = nombre
                    if comentario:
                        texto += _(" — comentario: {0}").format(comentario)
                else:
                    texto = comentario or it.get("texto") or _("(sin texto)")
                fecha = formatear_tiempo_relativo(str(it.get("fecha"))) if it.get("fecha") else ""
                pos = lista.InsertItem(i, texto)
                lista.SetItem(pos, 1, fecha)
                if tiene_estado:
                    estado = _("Descargado") if it.get("descargado") else _("No descargado")
                    lista.SetItem(pos, 2, estado)
                    lista.establecer_lectura(pos, f"{texto}. {fecha}. {estado}")
                else:
                    lista.establecer_lectura(pos, f"{texto}. {fecha}")
        finally:
            lista.Thaw()
        if anunciar:
            speak(_("No hay elementos.") if not items
                  else _("{0} elementos.").format(len(items)), interrupt=True)

    # ---------------- Búsqueda dentro de la pestaña activa ----------------
    def _cat_activa(self):
        idx = self.notebook.GetSelection()
        return self.CATEGORIAS[idx - 1][0] if idx >= 1 else None

    def _on_buscar_pestana(self, event=None):
        """Filtra la lista de la pestaña ACTIVA con lo escrito; si el campo está vacío, restaura la
        lista original completa. Solo muestra lo encontrado en ESA pestaña."""
        cat = self._cat_activa()
        if not cat:
            return
        q = (self.txt_buscar_media.GetValue() or "").strip().lower()
        items = self._listas[cat].get("items", [])
        if not q:
            self._pintar_categoria(cat, items)
            self.lbl_buscar.SetLabel("")
            return
        filtrados = [it for it in items
                     if q in (it.get("file_name") or "").lower()
                     or q in (it.get("texto") or "").lower()
                     or q in (it.get("caption") or "").lower()]
        self._pintar_categoria(cat, filtrados)
        self.lbl_buscar.SetLabel(_("{0} de {1}").format(len(filtrados), len(items)))

    def _on_media_selected(self, cat):
        """Reproduce el sonido identificativo del tipo de la pestaña al navegar."""
        if cat in ("photo", "video", "audio", "voice", "document"):
            from .main_frame import evento_sonido_media
            from app.core.event_bus import event_bus
            evento = evento_sonido_media(cat)
            if evento:
                event_bus.emit("sound_trigger", evento)

    def _on_media_key(self, event, cat):
        """Espacio: reproducir audio/voz/vídeo. Ctrl+I: describir la foto con IA."""
        code = event.GetKeyCode()
        mods = event.GetModifiers()
        if code == wx.WXK_SPACE and mods == wx.MOD_NONE and cat in ("audio", "voice", "video"):
            lista = self._listas[cat]["lista"]
            idx = lista.GetFocusedItem()
            if idx >= 0:
                self._on_abrir_media(cat, idx, reproducir=True)
                return
        elif mods == wx.MOD_CONTROL and code == ord('I') and cat == "photo":
            info = self._listas[cat]
            idx = info["lista"].GetFocusedItem()
            if 0 <= idx < len(info["items"]):
                self._describir_foto_compartida(info["items"][idx]["message_id"])
                return
        event.Skip()

    def _describir_foto_compartida(self, mid):
        """Descarga la foto compartida y la describe con IA (pestaña Fotos)."""
        from app.services.ai.ai_service import ai_service
        # Progreso de la descarga solo por voz (cada 25 %); aquí no procede tocar la columna Estado.
        prog = {"hito": 0}
        def cb(current, total):
            if not self:
                return
            pct = int(current * 100 / total) if total else 0
            hito = (pct // 25) * 25
            if 0 < hito < 100 and hito != prog["hito"]:
                prog["hito"] = hito
                speak(_("Descargando imagen, {0} por ciento").format(hito), interrupt=False)
        def _bg(ctx):
            ruta = telegram_service.descargar_media(self.account_id, self.chat_id, mid, progreso=cb)
            if not ruta:
                raise RuntimeError(_("No se pudo descargar la imagen."))
            return ai_service.describir_imagen(ruta)
        def _finish(res, err):
            if err:
                notificar(_("Error de la IA: {0}").format(err))
            else:
                # Markdown -> visor HTML navegable; texto plano -> cuadro de solo lectura (que el
                # lector ya lee al abrirse; por eso no se locuta también aquí).
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Descripción de IA"), res, self._mostrar_texto)
        speak(_("Analizando la imagen con IA..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _mostrar_texto(self, titulo, texto):
        """Muestra un texto en un diálogo accesible de solo lectura (TextCtrl)."""
        dlg = wx.Dialog(self, title=titulo, size=(640, 460),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        s = wx.BoxSizer(wx.VERTICAL)
        txt = AccessibleTextCtrl(dlg, style=wx.TE_MULTILINE | wx.TE_READONLY, name=titulo)
        txt.SetValue(texto)
        s.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(wx.Button(dlg, wx.ID_OK, _("Aceptar")), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizer(s)
        dlg.Bind(wx.EVT_CLOSE, lambda e: dlg.EndModal(wx.ID_OK))
        txt.SetFocus()
        dlg.ShowModal()
        dlg.Destroy()

    def _estado_descarga_item(self, cat, idx):
        """Re-evalúa AHORA si el adjunto sigue descargado en disco (detecta descargas borradas) y
        actualiza el item y la columna Estado. Devuelve la ruta local o None."""
        info = self._listas.get(cat)
        if not info or not (0 <= idx < len(info["items"])):
            return None
        item = info["items"][idx]
        ruta = telegram_service.ruta_descarga_valida(self.account_id, self.chat_id, item["message_id"])
        item["media_path"] = ruta or ""
        item["descargado"] = bool(ruta)
        if cat != "url":
            try:
                info["lista"].SetItem(idx, 2, _("Descargado") if ruta else _("No descargado"))
            except Exception:
                pass
        return ruta

    def _hacer_progreso_descarga(self, cat, idx, nombre):
        """Devuelve un callback (current, total) que refresca la columna Estado con el porcentaje de
        descarga y anuncia por voz cada 25 %. El servicio ya lo invoca en el hilo de la UI."""
        estado = {"pct": -1, "hito": 0}
        def cb(current, total):
            if not self:
                return
            pct = int(current * 100 / total) if total else 0
            if pct == estado["pct"]:
                return
            estado["pct"] = pct
            info = self._listas.get(cat)
            if info and cat != "url":
                try:
                    info["lista"].SetItem(idx, 2, _("Descargando {0} %").format(pct))
                except Exception:
                    pass
            hito = (pct // 25) * 25
            if 0 < hito < 100 and hito != estado["hito"]:
                estado["hito"] = hito
                speak(_("{0}, {1} por ciento").format(nombre, hito), interrupt=False)
        return cb

    def _on_menu_media(self, cat):
        info = self._listas.get(cat)
        lista = info["lista"]
        idx = lista.GetFocusedItem()
        if idx < 0 or idx >= len(info["items"]):
            return
        item = info["items"][idx]
        menu = wx.Menu()
        if cat == "url":
            it = menu.Append(wx.ID_ANY, _("Abrir enlace"))
            self.Bind(wx.EVT_MENU, lambda e: self._on_abrir_media(cat, idx), it)
        else:
            # Menú DINÁMICO según el estado real de descarga (comprobado al abrir el menú).
            ruta = self._estado_descarga_item(cat, idx)
            if ruta:
                # Ya descargado: abrir, abrir carpeta, reproducir, volver a descargar, borrar local.
                if cat in ("audio", "voice", "video"):
                    it = menu.Append(wx.ID_ANY, _("Reproducir (Espacio)"))
                    self.Bind(wx.EVT_MENU, lambda e: self._on_abrir_media(cat, idx, reproducir=True), it)
                it = menu.Append(wx.ID_ANY, _("Abrir archivo"))
                self.Bind(wx.EVT_MENU, lambda e, r=ruta: self._abrir_archivo(r), it)
                it = menu.Append(wx.ID_ANY, _("Abrir carpeta del archivo"))
                self.Bind(wx.EVT_MENU, lambda e, r=ruta: self._abrir_carpeta(r), it)
                it = menu.Append(wx.ID_ANY, _("Volver a descargar"))
                self.Bind(wx.EVT_MENU, lambda e: self._on_abrir_media(cat, idx, reproducir=False), it)
                it = menu.Append(wx.ID_ANY, _("Borrar descarga local"))
                self.Bind(wx.EVT_MENU, lambda e: self._on_borrar_descarga(cat, idx), it)
            else:
                # No descargado: descargar (y, si es reproducible, descargar y reproducir).
                it = menu.Append(wx.ID_ANY, _("Descargar"))
                self.Bind(wx.EVT_MENU, lambda e: self._on_abrir_media(cat, idx, reproducir=False), it)
                if cat in ("audio", "voice", "video"):
                    it = menu.Append(wx.ID_ANY, _("Descargar y reproducir"))
                    self.Bind(wx.EVT_MENU, lambda e: self._on_abrir_media(cat, idx, reproducir=True), it)
        if item.get("outgoing"):
            it = menu.Append(wx.ID_ANY, _("Borrar (lo envié yo)"))
            self.Bind(wx.EVT_MENU, lambda e: self._on_borrar_media(cat, idx), it)
        lista.PopupMenu(menu)
        menu.Destroy()

    def _abrir_archivo(self, ruta):
        """Abre el archivo descargado con su aplicación predeterminada."""
        import os
        if not ruta or not os.path.exists(ruta):
            notificar(_("El archivo ya no está descargado."))
            return
        # SEGURIDAD (TG-008): no abrir tipos activos (ejecutable/script/acceso directo) con un clic.
        from app.core.file_safety import es_extension_activa
        if es_extension_activa(ruta):
            notificar(_("Por seguridad, «{0}» no se abre desde la aplicación (es un archivo "
                        "ejecutable o de script). Se abrirá su carpeta.").format(
                            os.path.basename(ruta)))
            self._abrir_carpeta(ruta)
            return
        try:
            os.startfile(ruta)  # type: ignore[attr-defined]
            speak(_("Abriendo archivo."), interrupt=True)
        except Exception as e:
            notificar(_("No se pudo abrir el archivo: {0}").format(e))

    def _abrir_carpeta(self, ruta):
        """Abre el explorador en la carpeta del archivo y lo selecciona."""
        import os, subprocess
        if not ruta or not os.path.exists(ruta):
            notificar(_("El archivo ya no está descargado."))
            return
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(ruta)])
        except Exception:
            try:
                os.startfile(os.path.dirname(ruta))  # type: ignore[attr-defined]
            except Exception as e:
                notificar(_("No se pudo abrir la carpeta: {0}").format(e))

    def _on_borrar_descarga(self, cat, idx):
        info = self._listas.get(cat)
        if not info or not (0 <= idx < len(info["items"])):
            return
        mid = info["items"][idx]["message_id"]

        def _bg(ctx):
            return telegram_service.borrar_descarga_local(self.account_id, self.chat_id, mid)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo borrar la descarga: {0}").format(err))
            else:
                self._estado_descarga_item(cat, idx)  # refresca la columna a "No descargado"
                notificar(_("Descarga local borrada."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_borrar_media(self, cat, index):
        info = self._listas.get(cat)
        if not info or index >= len(info["items"]):
            return
        mid = info["items"][index]["message_id"]
        dlg = wx.MessageDialog(self, _("¿Borrar este elemento para todos?"), _("Confirmar borrado"),
                               wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        def _bg(ctx):
            return telegram_service.borrar_mensaje(self.account_id, self.chat_id, mid, revoke=True)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
            else:
                self._cargar_categoria(cat)
                notificar(_("Elemento borrado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_abrir_media(self, cat, index, reproducir=None):
        info = self._listas.get(cat)
        if not info or index < 0 or index >= len(info["items"]):
            return
        # Pestaña Enlaces: Intro abre la URL en el navegador (no se descarga nada).
        if cat == "url":
            import re
            texto = info["items"][index].get("texto") or ""
            urls = re.findall(r'https?://[^\s\)\]]+', texto)
            if urls:
                abrir_url(urls[0])
                speak(_("Abriendo enlace: {0}").format(urls[0]), interrupt=True)
            else:
                notificar(_("Este elemento no contiene un enlace."))
            return
        item = info["items"][index]
        mid = item["message_id"]
        if reproducir is None:
            reproducir = cat in ("audio", "voice", "video")
        nombre = item.get("file_name") or (item.get("caption") or "").replace("\n", " ") or _("archivo")
        cb = self._hacer_progreso_descarga(cat, index, nombre)
        speak(_("Descargando..."), interrupt=True)
        def _bg(ctx):
            ruta = telegram_service.descargar_media(self.account_id, self.chat_id, mid, progreso=cb)
            if ruta and reproducir:
                from app.services.audio import microphone
                ruta = microphone.convertir_a_wav_si_necesario(ruta)
            return ruta
        def _finish(res, err):
            if err or not res:
                notificar(_("No se pudo descargar: {0}").format(err) if err else _("No se pudo descargar."))
                return
            # Refrescar la columna Estado a "Descargado" (y guardar la ruta en el item).
            self._estado_descarga_item(cat, index)
            if reproducir:
                from app.services.media_player.player_service import player_service
                player_service.cargar_y_reproducir(res, self.chat_title, service_origin="telegram", account_origin_id=self.account_id)
                speak(_("Reproduciendo."), interrupt=True)
            else:
                speak(_("Descargado en: {0}").format(res), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)
