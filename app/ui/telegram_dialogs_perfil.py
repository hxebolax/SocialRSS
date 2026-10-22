# -*- coding: utf-8 -*-
"""Diálogos de perfil, sesiones, carpetas e historias de Telegram.
"""
import wx

from app.core.speech import speak, notificar
from app.core.database import db
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl


class TelegramProfileDialog(wx.Dialog):
    """Ver y editar el perfil propio de Telegram."""

    def __init__(self, parent, account_id, perfil):
        super().__init__(parent, title=_("Mi perfil de Telegram"), size=(480, 440),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Diálogo de perfil de Telegram"))
        self.account_id = account_id
        self.perfil = perfil
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        p = self.perfil

        sizer.Add(wx.StaticText(self, label=_("Teléfono: {0}").format(p.get("phone", ""))), 0, wx.ALL, 5)
        sizer.Add(wx.StaticText(self, label=_("Premium: {0}").format(_("Sí") if p.get("is_premium") else _("No"))), 0, wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("Nombre:")), 0, wx.ALL, 5)
        self.txt_first = AccessibleTextCtrl(self, value=p.get("first_name", ""), name=_("Nombre"))
        sizer.Add(self.txt_first, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("Apellido:")), 0, wx.ALL, 5)
        self.txt_last = AccessibleTextCtrl(self, value=p.get("last_name", ""), name=_("Apellido"))
        sizer.Add(self.txt_last, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("Nombre de usuario (sin @):")), 0, wx.ALL, 5)
        self.txt_user = AccessibleTextCtrl(self, value=p.get("username", ""), name=_("Nombre de usuario"))
        sizer.Add(self.txt_user, 0, wx.EXPAND | wx.ALL, 5)

        fila_foto = wx.BoxSizer(wx.HORIZONTAL)
        b_foto = wx.Button(self, label=_("Cambiar &foto..."))
        b_foto.Bind(wx.EVT_BUTTON, self._on_cambiar_foto)
        fila_foto.Add(b_foto, 0, wx.ALL, 4)
        b_qfoto = wx.Button(self, label=_("&Quitar foto"))
        b_qfoto.Bind(wx.EVT_BUTTON, self._on_quitar_foto)
        fila_foto.Add(b_qfoto, 0, wx.ALL, 4)
        b_cumple = wx.Button(self, label=_("Cu&mpleaños..."))
        b_cumple.Bind(wx.EVT_BUTTON, self._on_cumple)
        fila_foto.Add(b_cumple, 0, wx.ALL, 4)
        b_emoji = wx.Button(self, label=_("Quitar &estado emoji"))
        b_emoji.Bind(wx.EVT_BUTTON, self._on_quitar_emoji)
        fila_foto.Add(b_emoji, 0, wx.ALL, 4)
        b_canal = wx.Button(self, label=_("Canal &personal del perfil..."))
        b_canal.Bind(wx.EVT_BUTTON, self._on_canal_personal)
        fila_foto.Add(b_canal, 0, wx.ALL, 4)
        sizer.Add(fila_foto, 0, wx.ALL, 4)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_guardar = wx.Button(self, wx.ID_OK, _("Guardar cambios"))
        btn_guardar.Bind(wx.EVT_BUTTON, self._on_guardar)
        btn_sizer.Add(btn_guardar, 0, wx.ALL, 5)
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)

    def _perfil_run(self, fn, ok_msg, enviando=None):
        if enviando:
            speak(enviando, interrupt=True)
        def _bg(ctx):
            return fn()
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else ok_msg)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_cambiar_foto(self, event):
        dlg = wx.FileDialog(self, _("Elija la nueva foto de perfil"),
                            wildcard=_("Imágenes") + "|*.jpg;*.jpeg;*.png",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        ruta = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        if ruta:
            self._perfil_run(lambda: telegram_service.establecer_foto_perfil(self.account_id, ruta),
                             _("Foto de perfil actualizada."), _("Subiendo foto..."))

    def _on_quitar_foto(self, event):
        self._perfil_run(lambda: telegram_service.borrar_foto_perfil(self.account_id),
                         _("Foto de perfil eliminada."))

    def _on_canal_personal(self, event):
        """Elige uno de tus canales públicos para mostrarlo en tu biografía, o quítalo."""
        speak(_("Cargando tus canales..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.obtener_canales_personales(self.account_id)
        def _finish(canales, err):
            if err:
                notificar(_("No se pudo cargar: {0}").format(err))
                return
            opciones = [_("(Ninguno: quitar el canal del perfil)")] + [n for _i, n in (canales or [])]
            dlg = wx.SingleChoiceDialog(self, _("Canal personal mostrado en tu perfil:"),
                                        _("Canal personal"), opciones)
            if dlg.ShowModal() == wx.ID_OK:
                sel = dlg.GetSelection()
                chat_id = None if sel == 0 else canales[sel - 1][0]
                self._perfil_run(
                    lambda: telegram_service.establecer_canal_personal(self.account_id, chat_id),
                    _("Canal del perfil quitado.") if chat_id is None else _("Canal personal actualizado."))
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_cumple(self, event):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Cumpleaños (DD/MM o DD/MM/AAAA):"), _("Cumpleaños"))
        valor = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not valor:
            return
        partes = valor.replace("-", "/").split("/")
        try:
            dia = int(partes[0]); mes = int(partes[1])
            anio = int(partes[2]) if len(partes) > 2 and partes[2] else None
        except (ValueError, IndexError):
            speak(_("Fecha no válida."), interrupt=True)
            return
        self._perfil_run(lambda: telegram_service.actualizar_cumpleanos(self.account_id, dia, mes, anio),
                         _("Cumpleaños actualizado."))

    def _on_quitar_emoji(self, event):
        self._perfil_run(lambda: telegram_service.quitar_estado_emoji(self.account_id),
                         _("Estado emoji eliminado."))

    def _on_guardar(self, event):
        first = self.txt_first.GetValue().strip()
        last = self.txt_last.GetValue().strip()
        user = self.txt_user.GetValue().strip()
        username_anterior = self.perfil.get("username", "")

        def _bg(ctx):
            telegram_service.editar_perfil(self.account_id, first_name=first, last_name=last)
            if user != username_anterior:
                telegram_service.cambiar_username(self.account_id, user)
            return True

        def _finish(res, err):
            if err:
                notificar(_("Error al guardar el perfil: {0}").format(err))
            else:
                notificar(_("Perfil actualizado."))
                self.EndModal(wx.ID_OK)

        speak(_("Guardando perfil..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class TelegramSessionsDialog(wx.Dialog):
    """Lista de sesiones activas con opción de cerrar las demás."""

    def __init__(self, parent, account_id, sesiones):
        super().__init__(parent, title=_("Sesiones activas de Telegram"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Diálogo de sesiones de Telegram"))
        self.account_id = account_id
        self.sesiones = sesiones
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de sesiones activas"))
        self.lista.InsertColumn(0, _("Dispositivo"), width=180)
        self.lista.InsertColumn(1, _("Aplicación"), width=160)
        self.lista.InsertColumn(2, _("Plataforma"), width=110)
        self.lista.InsertColumn(3, _("Actual"), width=70)
        for idx, s in enumerate(self.sesiones):
            pos = self.lista.InsertItem(idx, str(s.get("device") or ""))
            self.lista.SetItem(pos, 1, str(s.get("app") or ""))
            self.lista.SetItem(pos, 2, str(s.get("platform") or ""))
            actual = _("Sí") if s.get("current") else ""
            self.lista.SetItem(pos, 3, actual)
            self.lista.establecer_lectura(pos, f"{s.get('device','')}, {s.get('app','')}, {s.get('platform','')}. {_('Sesión actual') if s.get('current') else ''}")
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_cerrar_una = wx.Button(self, label=_("Cerrar esta sesión"), name=_("Cerrar la sesión seleccionada"))
        btn_cerrar_una.Bind(wx.EVT_BUTTON, self._on_cerrar_una)
        btn_sizer.Add(btn_cerrar_una, 0, wx.ALL, 5)
        btn_cerrar_otras = wx.Button(self, label=_("Cerrar todas las demás sesiones"), name=_("Cerrar el resto de sesiones"))
        btn_cerrar_otras.Bind(wx.EVT_BUTTON, self._on_cerrar_otras)
        btn_sizer.Add(btn_cerrar_otras, 0, wx.ALL, 5)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)

    def _on_cerrar_una(self, event):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self.sesiones):
            speak(_("Seleccione una sesión."), interrupt=True)
            return
        s = self.sesiones[idx]
        if s.get("current"):
            speak(_("No puede cerrar la sesión actual desde aquí; use Cerrar sesión."), interrupt=True)
            return
        sid = s.get("id")
        if not sid:
            notificar(_("Esta sesión no se puede cerrar."))
            return
        dlg = wx.MessageDialog(self, _("¿Cerrar la sesión de {0}?").format(s.get("device") or s.get("app") or ""),
                               _("Confirmar"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        def _bg(ctx):
            return telegram_service.cerrar_sesion_concreta(self.account_id, sid)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                self.lista.DeleteItem(idx)
                del self.sesiones[idx]
                speak(_("Sesión cerrada."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_cerrar_otras(self, event):
        dlg = wx.MessageDialog(self, _("¿Cerrar todas las demás sesiones excepto esta?"),
                               _("Confirmar"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        def _bg(ctx):
            return telegram_service.cerrar_otras_sesiones(self.account_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                speak(_("Se cerraron las demás sesiones."), interrupt=True)
                self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class TelegramCarpetasDialog(wx.Dialog):
    """Gestión de carpetas de chats: crear, renombrar y borrar."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Carpetas de Telegram"), size=(520, 420),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Gestión de carpetas de Telegram"))
        self.account_id = account_id
        self.carpetas = []
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("Carpetas:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de carpetas"))
        self.lista.InsertColumn(0, _("Carpeta"), width=420)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        b_crear = wx.Button(self, label=_("&Crear..."))
        b_crear.Bind(wx.EVT_BUTTON, self._on_crear)
        btn_sizer.Add(b_crear, 0, wx.ALL, 5)
        b_ren = wx.Button(self, label=_("&Renombrar..."))
        b_ren.Bind(wx.EVT_BUTTON, self._on_renombrar)
        btn_sizer.Add(b_ren, 0, wx.ALL, 5)
        b_del = wx.Button(self, label=_("&Borrar"))
        b_del.Bind(wx.EVT_BUTTON, self._on_borrar)
        btn_sizer.Add(b_del, 0, wx.ALL, 5)
        b_subir = wx.Button(self, label=_("Su&bir"))
        b_subir.Bind(wx.EVT_BUTTON, lambda e: self._on_mover(-1))
        btn_sizer.Add(b_subir, 0, wx.ALL, 5)
        b_bajar = wx.Button(self, label=_("Ba&jar"))
        b_bajar.Bind(wx.EVT_BUTTON, lambda e: self._on_mover(1))
        btn_sizer.Add(b_bajar, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 8)

        btn_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
        b_comp = wx.Button(self, label=_("Co&mpartir (enlace)..."))
        b_comp.Bind(wx.EVT_BUTTON, self._on_compartir)
        btn_sizer2.Add(b_comp, 0, wx.ALL, 5)
        b_unir = wx.Button(self, label=_("&Unirse a una carpeta por enlace..."))
        b_unir.Bind(wx.EVT_BUTTON, self._on_unirse)
        btn_sizer2.Add(b_unir, 0, wx.ALL, 5)
        btn_sizer2.AddStretchSpacer()
        btn_sizer2.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer2, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)

    def _cargar(self):
        def _bg(ctx):
            return telegram_service.obtener_carpetas(self.account_id)
        def _finish(res, err):
            if err:
                notificar(_("Error al cargar carpetas: {0}").format(err))
                return
            self.carpetas = res or []
            self._pintar(anunciar=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _pintar(self, foco=0, anunciar=False):
        self.lista.DeleteAllItems()
        for idx, c in enumerate(self.carpetas):
            pos = self.lista.InsertItem(idx, c.get("title") or str(c.get("id")))
            self.lista.establecer_lectura(pos, c.get("title") or "")
        if self.carpetas:
            f = max(0, min(foco, len(self.carpetas) - 1))
            self.lista.Focus(f)
            self.lista.Select(f)
        if anunciar:
            speak(_("{0} carpetas.").format(len(self.carpetas)), interrupt=True)

    def _on_mover(self, delta):
        """Sube (-1) o baja (+1) la carpeta seleccionada y guarda el nuevo orden en el servidor."""
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self.carpetas)):
            speak(_("Seleccione una carpeta."), interrupt=True)
            return
        nuevo = idx + delta
        if not (0 <= nuevo < len(self.carpetas)):
            return
        self.carpetas[idx], self.carpetas[nuevo] = self.carpetas[nuevo], self.carpetas[idx]
        self._pintar(foco=nuevo)
        speak(self.carpetas[nuevo].get("title") or "", interrupt=True)
        ids = [c.get("id") for c in self.carpetas]
        def _bg(ctx):
            return telegram_service.reordenar_carpetas(self.account_id, ids)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo guardar el orden: {0}").format(err))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_compartir(self, event):
        """Crea (o muestra/borra) un enlace para compartir la carpeta seleccionada."""
        c = self._carpeta_sel()
        if not c:
            speak(_("Seleccione una carpeta."), interrupt=True)
            return
        fid = c.get("id")
        speak(_("Preparando enlace de la carpeta..."), interrupt=True)
        def _bg(ctx):
            existentes = telegram_service.obtener_enlaces_carpeta(self.account_id, fid)
            chats = telegram_service.chats_para_enlace_carpeta(self.account_id, fid)
            return (existentes, chats)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo preparar: {0}").format(err))
                return
            existentes, chats = res
            if existentes:
                # Mostrar el enlace existente (y permitir borrarlo).
                link = existentes[0][0]
                dlg = wx.MessageDialog(
                    self, _("Enlace de la carpeta «{0}»:\n\n{1}\n\n¿Copiar al portapapeles? "
                            "(Pulsa No para borrar este enlace.)").format(c.get("title", ""), link),
                    _("Compartir carpeta"), wx.YES_NO | wx.CANCEL | wx.ICON_INFORMATION)
                resp = dlg.ShowModal()
                dlg.Destroy()
                if resp == wx.ID_YES:
                    self._copiar(link)
                elif resp == wx.ID_NO:
                    self._borrar_enlace_carpeta(fid, link)
                return
            if not chats:
                speak(_("Esta carpeta no tiene chats que se puedan compartir por enlace."), interrupt=True)
                return
            self._crear_enlace_carpeta(fid, c.get("title", ""), chats)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _crear_enlace_carpeta(self, fid, titulo, chats):
        from .accessible_controls import AccessibleCheckListBox
        dlg = wx.Dialog(self, title=_("Chats a incluir en el enlace"), size=(460, 460),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label=_("Marque los chats que quiere compartir (los grupos/canales "
                                         "donde tengas permiso):")), 0, wx.ALL, 6)
        clb = AccessibleCheckListBox(dlg, choices=[n for _i, n in chats])
        s.Add(clb, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 6)
        dlg.SetSizer(s)
        elegidos = None
        if dlg.ShowModal() == wx.ID_OK:
            elegidos = [chats[i][0] for i in clb.GetCheckedItems()]
        dlg.Destroy()
        if not elegidos:
            return
        def _bg(ctx):
            return telegram_service.crear_enlace_carpeta(self.account_id, fid, elegidos, titulo)
        def _finish(link, err):
            if err or not link:
                notificar(_("No se pudo crear el enlace: {0}").format(err or ""))
                return
            self._copiar(link)
            self._mostrar_enlace(titulo, link)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _mostrar_enlace(self, titulo, link):
        dlg = wx.MessageDialog(self, _("Enlace creado para «{0}» (copiado al portapapeles):\n\n{1}").format(titulo, link),
                               _("Compartir carpeta"), wx.OK | wx.ICON_INFORMATION)
        dlg.ShowModal()
        dlg.Destroy()

    def _borrar_enlace_carpeta(self, fid, link):
        def _bg(ctx):
            return telegram_service.borrar_enlace_carpeta(self.account_id, fid, link)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo borrar el enlace: {0}").format(err))
            else:
                notificar(_("Enlace de la carpeta borrado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_unirse(self, event):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Pega el enlace de invitación de la carpeta:"),
                                  _("Unirse a una carpeta"))
        enlace = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not enlace:
            return
        def _bg(ctx):
            return telegram_service.unirse_a_carpeta(self.account_id, enlace)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo unir a la carpeta: {0}").format(err))
            else:
                notificar(_("Te has unido a la carpeta."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _copiar(self, texto):
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(texto))
                wx.TheClipboard.Flush()
            finally:
                wx.TheClipboard.Close()

    def _carpeta_sel(self):
        idx = self.lista.GetFocusedItem()
        if 0 <= idx < len(self.carpetas):
            return self.carpetas[idx]
        return None

    def _elegir_chats(self):
        """Devuelve una lista de chat_id elegidos con casillas, o None si cancela."""
        filas = db.query(
            "SELECT chat_id, title FROM telegram_chats WHERE account_id = ? ORDER BY title COLLATE NOCASE",
            (self.account_id,))
        chats = [(r[0], r[1] or str(r[0])) for r in filas]
        if not chats:
            return []
        from .accessible_controls import AccessibleCheckListBox
        dlg = wx.Dialog(self, title=_("Elija los chats de la carpeta"), size=(460, 460),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label=_("Marque los chats a incluir:")), 0, wx.ALL, 6)
        clb = AccessibleCheckListBox(dlg, choices=[t for _c, t in chats])
        s.Add(clb, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 6)
        dlg.SetSizer(s)
        dlg.Bind(wx.EVT_CLOSE, lambda e: dlg.EndModal(wx.ID_CANCEL))
        elegidos = None
        if dlg.ShowModal() == wx.ID_OK:
            elegidos = [chats[i][0] for i in clb.GetCheckedItems()]
        dlg.Destroy()
        return elegidos

    def _on_crear(self, event):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nombre de la nueva carpeta:"), _("Crear carpeta"))
        nombre = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not nombre:
            return
        chats = self._elegir_chats()
        if chats is None:
            return
        def _bg(ctx):
            return telegram_service.crear_carpeta(self.account_id, nombre, chats)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo crear: {0}").format(err))
            else:
                notificar(_("Carpeta creada."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_renombrar(self, event):
        c = self._carpeta_sel()
        if not c:
            speak(_("Seleccione una carpeta."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nuevo nombre:"), _("Renombrar carpeta"), value=c.get("title", ""))
        nombre = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not nombre:
            return
        fid = c.get("id")
        def _bg(ctx):
            return telegram_service.editar_carpeta(self.account_id, fid, nombre=nombre)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo renombrar: {0}").format(err))
            else:
                notificar(_("Carpeta renombrada."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_borrar(self, event):
        c = self._carpeta_sel()
        if not c:
            speak(_("Seleccione una carpeta."), interrupt=True)
            return
        dlg = wx.MessageDialog(self, _("¿Borrar la carpeta «{0}»? (los chats no se borran)").format(c.get("title", "")),
                               _("Borrar carpeta"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        fid = c.get("id")
        def _bg(ctx):
            return telegram_service.borrar_carpeta(self.account_id, fid)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
            else:
                notificar(_("Carpeta borrada."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class TelegramHistoriasDialog(wx.Dialog):
    """Ver, marcar como vistas, descargar y publicar historias (stories)."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Historias de Telegram"), size=(620, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Historias de Telegram"))
        self.account_id = account_id
        self.historias = []
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("Historias activas:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de historias"))
        self.lista.InsertColumn(0, _("Autor"), width=180)
        self.lista.InsertColumn(1, _("Tipo"), width=80)
        self.lista.InsertColumn(2, _("Texto"), width=240)
        self.lista.InsertColumn(3, _("Fecha"), width=110)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        b_ver = wx.Button(self, label=_("Marcar como &vista"))
        b_ver.Bind(wx.EVT_BUTTON, self._on_ver)
        btn_sizer.Add(b_ver, 0, wx.ALL, 5)
        b_desc = wx.Button(self, label=_("&Descargar"))
        b_desc.Bind(wx.EVT_BUTTON, self._on_descargar)
        btn_sizer.Add(b_desc, 0, wx.ALL, 5)
        b_rep = wx.Button(self, label=_("&Repostear a un chat..."))
        b_rep.Bind(wx.EVT_BUTTON, self._on_repostear)
        btn_sizer.Add(b_rep, 0, wx.ALL, 5)
        b_pub = wx.Button(self, label=_("&Publicar historia..."))
        b_pub.Bind(wx.EVT_BUTTON, self._on_publicar)
        btn_sizer.Add(b_pub, 0, wx.ALL, 5)
        b_mis = wx.Button(self, label=_("&Mis historias..."))
        b_mis.Bind(wx.EVT_BUTTON, self._on_mis_historias)
        btn_sizer.Add(b_mis, 0, wx.ALL, 5)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)

    def _cargar(self):
        def _bg(ctx):
            return telegram_service.obtener_historias(self.account_id)
        def _finish(res, err):
            if err:
                notificar(_("Error al cargar historias: {0}").format(err))
                return
            self.historias = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for idx, h in enumerate(self.historias):
                pos = self.lista.InsertItem(idx, h.get("autor", ""))
                self.lista.SetItem(pos, 1, h.get("tipo", ""))
                self.lista.SetItem(pos, 2, h.get("caption", ""))
                self.lista.SetItem(pos, 3, h.get("fecha", ""))
                self.lista.establecer_lectura(pos, f"{h.get('autor','')}. {h.get('tipo','')}. {h.get('caption','')}")
            speak(_("{0} historias.").format(len(self.historias)) if self.historias else _("No hay historias."), interrupt=True)
        speak(_("Cargando historias..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        if 0 <= idx < len(self.historias):
            return self.historias[idx]
        speak(_("Seleccione una historia."), interrupt=True)
        return None

    def _on_ver(self, event):
        h = self._sel()
        if not h:
            return
        def _bg(ctx):
            return telegram_service.marcar_historia_vista(self.account_id, h["chat_id"], h["story_id"])
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else _("Marcada como vista."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_descargar(self, event):
        h = self._sel()
        if not h:
            return
        def _bg(ctx):
            return telegram_service.descargar_historia(self.account_id, h["chat_id"], h["story_id"])
        def _finish(res, err):
            if err:
                notificar(_("Error al descargar: {0}").format(err))
            elif res:
                speak(_("Historia descargada en {0}.").format(res), interrupt=True)
            else:
                speak(_("La historia no tiene archivo."), interrupt=True)
        speak(_("Descargando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_publicar(self, event):
        dlg = wx.FileDialog(self, _("Elija la foto o vídeo de la historia"),
                            wildcard=_("Medios") + "|*.jpg;*.jpeg;*.png;*.mp4;*.mov",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        ruta = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        if not ruta:
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Texto de la historia (opcional):"), _("Publicar historia"))
        caption = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        def _bg(ctx):
            return telegram_service.publicar_historia(self.account_id, ruta, caption)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo publicar: {0}").format(err))
            else:
                notificar(_("Historia publicada."))
        speak(_("Publicando historia..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _elegir_chat_destino(self):
        """Devuelve el chat_id de destino elegido entre los chats cacheados, o None."""
        filas = db.query(
            "SELECT chat_id, title FROM telegram_chats WHERE account_id = ? ORDER BY title COLLATE NOCASE",
            (self.account_id,))
        chats = [(r[0], r[1] or str(r[0])) for r in filas]
        if not chats:
            speak(_("No hay chats disponibles."), interrupt=True)
            return None
        dlg = wx.SingleChoiceDialog(self, _("Repostear la historia a:"), _("Elegir chat"),
                                    [t for _c, t in chats])
        destino = chats[dlg.GetSelection()][0] if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        return destino

    def _on_repostear(self, event):
        h = self._sel()
        if not h:
            return
        destino = self._elegir_chat_destino()
        if destino is None:
            return
        def _bg(ctx):
            return telegram_service.repostear_historia(self.account_id, h["chat_id"], h["story_id"], destino)
        def _finish(res, err):
            notificar(_("No se pudo repostear: {0}").format(err) if err else _("Historia reposteada."))
        speak(_("Reposteando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_mis_historias(self, event):
        dlg = TelegramMisHistoriasDialog(self, self.account_id)
        dlg.ShowModal()
        dlg.Destroy()


class TelegramMisHistoriasDialog(wx.Dialog):
    """Gestión de tus historias publicadas: ver quién las vio, editar pie, fijar/desfijar y borrar."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mis historias"), size=(600, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Mis historias publicadas"))
        self.account_id = account_id
        self.historias = []
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Mostrar:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice_modo = wx.Choice(self, choices=[_("Publicadas"), _("Archivadas")],
                                     name=_("Qué historias mostrar"))
        self.choice_modo.SetSelection(0)
        self.choice_modo.Bind(wx.EVT_CHOICE, lambda e: self._cargar())
        fila.Add(self.choice_modo, 0, wx.ALL, 5)
        sizer.Add(fila, 0, wx.EXPAND)

        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de tus historias"))
        self.lista.InsertColumn(0, _("Tipo"), width=90)
        self.lista.InsertColumn(1, _("Texto"), width=330)
        self.lista.InsertColumn(2, _("Fecha"), width=130)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        bs = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, handler in (
                (_("Quién la ha &visto"), self._on_vistas),
                (_("&Editar pie..."), self._on_editar_pie),
                (_("&Privacidad..."), self._on_privacidad),
                (_("&Reemplazar archivo..."), self._on_reemplazar),
                (_("&Fijar/Desfijar"), self._on_fijar),
                (_("&Borrar"), self._on_borrar)):
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, handler)
            bs.Add(b, 0, wx.ALL, 4)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 4)
        sizer.Add(bs, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(sizer)

    def _cargar(self):
        archivadas = self.choice_modo.GetSelection() == 1
        speak(_("Cargando tus historias..."), interrupt=True)
        def _bg(ctx):
            if archivadas:
                return telegram_service.mis_historias_archivadas(self.account_id)
            return telegram_service.mis_historias(self.account_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self.historias = res or []
            self.lista.DeleteAllItems()
            for i, h in enumerate(self.historias):
                pos = self.lista.InsertItem(i, h.get("tipo", ""))
                self.lista.SetItem(pos, 1, h.get("caption", ""))
                self.lista.SetItem(pos, 2, h.get("fecha", ""))
                self.lista.establecer_lectura(pos, f"{h.get('tipo','')}. {h.get('caption','')}")
            if self.historias:
                self.lista.Focus(0); self.lista.Select(0)
            speak(_("{0} historias.").format(len(self.historias)) if self.historias
                  else _("No tienes historias publicadas."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        if 0 <= idx < len(self.historias):
            return self.historias[idx]
        speak(_("Seleccione una historia."), interrupt=True)
        return None

    def _on_vistas(self, event):
        h = self._sel()
        if not h:
            return
        speak(_("Consultando quién la ha visto..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.quien_vio_historia(self.account_id, h["story_id"])
        def _finish(nombres, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not nombres:
                speak(_("Todavía no la ha visto nadie."), interrupt=True)
                return
            texto = "\n".join(nombres)
            dlg = wx.MessageDialog(self, _("La han visto {0} personas:\n\n{1}").format(len(nombres), texto),
                                   _("Vistas de la historia"), wx.OK | wx.ICON_INFORMATION)
            dlg.ShowModal()
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_editar_pie(self, event):
        h = self._sel()
        if not h:
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nuevo texto de la historia:"), _("Editar pie"),
                                  value=h.get("caption", ""))
        nuevo = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if nuevo is None:
            return
        def _bg(ctx):
            return telegram_service.editar_pie_historia(self.account_id, h["story_id"], nuevo)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo editar: {0}").format(err))
            else:
                notificar(_("Pie de la historia editado."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_privacidad(self, event):
        h = self._sel()
        if not h:
            return
        opciones = [(_("Pública (todos)"), "PUBLIC"), (_("Solo contactos"), "CONTACTS"),
                    (_("Amigos cercanos"), "CLOSE_FRIENDS")]
        dlg = wx.SingleChoiceDialog(self, _("¿Quién puede ver esta historia?"), _("Privacidad"),
                                    [e for e, _c in opciones])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        clave = opciones[dlg.GetSelection()][1]
        dlg.Destroy()
        def _bg(ctx):
            return telegram_service.editar_privacidad_historia(self.account_id, h["story_id"], clave)
        def _finish(res, err):
            notificar(_("No se pudo cambiar: {0}").format(err) if err else _("Privacidad actualizada."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_reemplazar(self, event):
        h = self._sel()
        if not h:
            return
        with wx.FileDialog(self, _("Elige la nueva foto o vídeo de la historia"),
                           wildcard=_("Medios") + "|*.jpg;*.jpeg;*.png;*.mp4;*.mov",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fd:
            if fd.ShowModal() != wx.ID_OK:
                return
            ruta = fd.GetPath()
        speak(_("Reemplazando archivo de la historia..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.editar_media_historia(self.account_id, h["story_id"], ruta)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo reemplazar: {0}").format(err))
            else:
                notificar(_("Archivo de la historia reemplazado."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_fijar(self, event):
        h = self._sel()
        if not h:
            return
        # Alternamos: estas historias ya están fijadas (get_pinned_stories), así que ofrecemos desfijar
        # o volver a fijar mediante un pequeño diálogo.
        dlg = wx.SingleChoiceDialog(self, _("¿Qué quieres hacer con esta historia en tu perfil?"),
                                    _("Fijar o desfijar"), [_("Fijar en el perfil"), _("Quitar del perfil")])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        fijar = dlg.GetSelection() == 0
        dlg.Destroy()
        def _bg(ctx):
            return telegram_service.fijar_historia(self.account_id, h["story_id"], fijar)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Historia fijada.") if fijar else _("Historia quitada del perfil."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_borrar(self, event):
        h = self._sel()
        if not h:
            return
        dlg = wx.MessageDialog(self, _("¿Borrar esta historia? No se puede deshacer."),
                               _("Borrar historia"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        def _bg(ctx):
            return telegram_service.borrar_historia(self.account_id, h["story_id"])
        def _finish(res, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
            else:
                notificar(_("Historia borrada."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class TelegramChatsSugeridosDialog(wx.Dialog):
    """Lista de chats sugeridos: tus chats más frecuentes (por categoría) o canales similares a uno.
    Intro sobre un chat abre su conversación."""

    # (etiqueta, categoría de la API, chat_type para abrir la conversación)
    CATEGORIAS = [
        (_("Personas"), "USERS", "private"),
        (_("Bots"), "BOTS", "bot"),
        (_("Grupos"), "GROUPS", "group"),
        (_("Canales"), "CHANNELS", "channel"),
    ]

    def __init__(self, parent, account_id, modo="frecuentes", chat_id_origen=None, titulo_origen=""):
        self.modo = modo
        self.chat_id_origen = chat_id_origen
        self.filas = []  # [(id, nombre, tipo), ...]
        titulo = (_("Chats frecuentes") if modo == "frecuentes"
                  else _("Canales similares a «{0}»").format(titulo_origen))
        super().__init__(parent, title=titulo, size=(560, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        self.account_id = account_id
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        if self.modo == "frecuentes":
            fila = wx.BoxSizer(wx.HORIZONTAL)
            fila.Add(wx.StaticText(self, label=_("Categoría:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
            self.choice_cat = wx.Choice(self, choices=[e for e, _c, _t in self.CATEGORIAS],
                                        name=_("Categoría de chats frecuentes"))
            self.choice_cat.SetSelection(0)
            self.choice_cat.Bind(wx.EVT_CHOICE, lambda e: self._cargar())
            fila.Add(self.choice_cat, 0, wx.ALL, 5)
            sizer.Add(fila, 0, wx.EXPAND)
        sizer.Add(wx.StaticText(self, label=_("Pulsa Intro sobre un chat para abrirlo:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de chats sugeridos"))
        self.lista.InsertColumn(0, _("Chat"), width=520)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_abrir)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)

    def _cargar(self):
        if self.modo == "frecuentes":
            cat = self.CATEGORIAS[self.choice_cat.GetSelection()][1]
            tipo_def = self.CATEGORIAS[self.choice_cat.GetSelection()][2]
            def _bg(ctx):
                return telegram_service.chats_frecuentes(self.account_id, cat)
        else:
            tipo_def = "channel"
            def _bg(ctx):
                return telegram_service.canales_similares(self.account_id, self.chat_id_origen)
        speak(_("Cargando..."), interrupt=True)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo cargar: {0}").format(err))
                return
            self.filas = []
            self.lista.DeleteAllItems()
            for i, t in enumerate(res or []):
                cid, nombre = t[0], t[1]
                extra = t[2] if len(t) > 2 else ""
                if self.modo == "similares":
                    etiqueta = _("{0} ({1} miembros)").format(nombre, extra) if extra else nombre
                    tipo = "channel"
                else:
                    etiqueta = nombre
                    tipo = tipo_def
                self.filas.append((cid, nombre, tipo))
                pos = self.lista.InsertItem(i, etiqueta)
                self.lista.establecer_lectura(pos, etiqueta)
            if self.filas:
                self.lista.Focus(0); self.lista.Select(0)
            speak(_("{0} chats.").format(len(self.filas)), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_abrir(self, event):
        idx = event.GetIndex()
        if not (0 <= idx < len(self.filas)):
            return
        cid, nombre, tipo = self.filas[idx]
        from .telegram_conversation import TelegramConversationDialog
        d = TelegramConversationDialog(self.GetParent(), self.account_id, cid, nombre, chat_type=tipo)
        d.ShowModal()
        d.Destroy()


class TelegramPrivacidadGlobalDialog(wx.Dialog):
    """Ajustes de privacidad global de la cuenta (con casillas accesibles)."""

    CAMPOS = [
        ("archive_and_mute_new_chats", _("Archivar y silenciar los chats nuevos de desconocidos")),
        ("keep_unmuted_chats_archived", _("Mantener archivados los chats aunque lleguen mensajes")),
        ("keep_chats_from_folders_archived", _("Mantener archivados los chats que estén en carpetas")),
        ("show_read_date", _("Mostrar a los demás la hora a la que lees sus mensajes")),
        ("allow_new_chats_from_unknown_users", _("Permitir que desconocidos te escriban por privado")),
    ]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Privacidad global de Telegram"), size=(560, 360),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Privacidad global"))
        self.account_id = account_id
        self.casillas = {}
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        for clave, etiqueta in self.CAMPOS:
            cb = wx.CheckBox(self, label=etiqueta, name=etiqueta)
            self.casillas[clave] = cb
            sizer.Add(cb, 0, wx.ALL, 6)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        b_guardar = wx.Button(self, wx.ID_OK, _("Guardar"))
        b_guardar.Bind(wx.EVT_BUTTON, self._on_guardar)
        btn_sizer.Add(b_guardar, 0, wx.ALL, 5)
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)

    def _cargar(self):
        def _bg(ctx):
            return telegram_service.obtener_privacidad_global(self.account_id)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo cargar: {0}").format(err))
                return
            for clave, cb in self.casillas.items():
                cb.SetValue(bool((res or {}).get(clave, False)))
            speak(_("Ajustes de privacidad global cargados."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_guardar(self, event):
        campos = {clave: cb.GetValue() for clave, cb in self.casillas.items()}
        def _bg(ctx):
            return telegram_service.establecer_privacidad_global(self.account_id, **campos)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
            else:
                notificar(_("Privacidad global actualizada."))
                self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class TelegramAjustesCuentaDialog(wx.Dialog):
    """Ajustes de cuenta: autodestrucción por inactividad, cierre de sesiones inactivas, estado en
    línea y cambio de número de teléfono."""

    OPC_TTL = [(30, _("1 mes")), (90, _("3 meses")), (180, _("6 meses")), (365, _("1 año"))]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Ajustes de cuenta de Telegram"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Ajustes de cuenta"))
        self.account_id = account_id
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("Autodestruir la cuenta tras este tiempo SIN conectarme:")),
                  0, wx.ALL, 5)
        self.choice_ttl = wx.Choice(self, choices=[e for _d, e in self.OPC_TTL],
                                    name=_("Tiempo de autodestrucción de la cuenta por inactividad"))
        sizer.Add(self.choice_ttl, 0, wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("Cerrar las sesiones inactivas tras (días):")), 0, wx.ALL, 5)
        self.spin_sesiones = wx.SpinCtrl(self, min=7, max=365, initial=180,
                                         name=_("Días para cerrar sesiones inactivas"))
        sizer.Add(self.spin_sesiones, 0, wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        b_online = wx.Button(self, label=_("Marcarme en &línea ahora"))
        b_online.Bind(wx.EVT_BUTTON, lambda e: self._estado(False))
        fila.Add(b_online, 0, wx.ALL, 4)
        b_off = wx.Button(self, label=_("Marcarme &desconectado ahora"))
        b_off.Bind(wx.EVT_BUTTON, lambda e: self._estado(True))
        fila.Add(b_off, 0, wx.ALL, 4)
        b_num = wx.Button(self, label=_("Cambiar &número de teléfono..."))
        b_num.Bind(wx.EVT_BUTTON, self._on_cambiar_numero)
        fila.Add(b_num, 0, wx.ALL, 4)
        sizer.Add(fila, 0, wx.ALL, 4)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        b_guardar = wx.Button(self, wx.ID_OK, _("Guardar autodestrucción y sesiones"))
        b_guardar.Bind(wx.EVT_BUTTON, self._on_guardar)
        btn_sizer.Add(b_guardar, 0, wx.ALL, 5)
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)

    def _cargar(self):
        def _bg(ctx):
            return telegram_service.obtener_ttl_cuenta(self.account_id)
        def _finish(dias, err):
            sel = 3
            if not err and dias:
                # Selecciona la opción más cercana por debajo o igual.
                for i, (d, _e) in enumerate(self.OPC_TTL):
                    if d <= dias:
                        sel = i
            self.choice_ttl.SetSelection(sel)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _estado(self, offline):
        def _bg(ctx):
            return telegram_service.actualizar_estado(self.account_id, offline)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo cambiar el estado: {0}").format(err))
            else:
                notificar(_("Ahora apareces desconectado.") if offline else _("Ahora apareces en línea."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_guardar(self, event):
        dias_cuenta = self.OPC_TTL[max(0, self.choice_ttl.GetSelection())][0]
        dias_ses = self.spin_sesiones.GetValue()
        def _bg(ctx):
            telegram_service.establecer_ttl_cuenta(self.account_id, dias_cuenta)
            telegram_service.establecer_ttl_sesiones(self.account_id, dias_ses)
            return True
        def _finish(res, err):
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
            else:
                notificar(_("Ajustes de cuenta guardados."))
                self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_cambiar_numero(self, event):
        from .accessible_controls import CustomTextEntryDialog
        aviso = wx.MessageDialog(
            self, _("Vas a cambiar el número de teléfono de tu cuenta de Telegram. Recibirás un código "
                    "en el NUEVO número para confirmarlo. ¿Continuar?"),
            _("Cambiar número"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        seguir = aviso.ShowModal() == wx.ID_YES
        aviso.Destroy()
        if not seguir:
            return
        d = CustomTextEntryDialog(self, _("Nuevo número con prefijo internacional (ej. +34600...):"),
                                  _("Cambiar número"))
        numero = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not numero:
            return
        speak(_("Enviando código al nuevo número..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.enviar_codigo_cambio_numero(self.account_id, numero)
        def _finish(code_hash, err):
            if err or not code_hash:
                notificar(_("No se pudo enviar el código: {0}").format(err or ""))
                return
            d2 = CustomTextEntryDialog(self, _("Escribe el código que has recibido en el nuevo número:"),
                                       _("Confirmar número"))
            codigo = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
            d2.Destroy()
            if not codigo:
                return
            def _bg2(ctx):
                return telegram_service.confirmar_cambio_numero(self.account_id, numero, code_hash, codigo)
            def _finish2(res, err2):
                if err2:
                    notificar(_("No se pudo cambiar el número: {0}").format(err2))
                else:
                    notificar(_("Número de teléfono cambiado correctamente."))
            task_manager.start_task(_bg2, callback=_finish2, pass_context=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class TelegramMisBotsDialog(wx.Dialog):
    """Gestiona los bots que posees: crear uno nuevo y editar nombre y descripciones."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mis bots de Telegram"), size=(580, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Mis bots"))
        self.account_id = account_id
        self.bots = []  # [(id, nombre, @usuario), ...]
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("Bots que posees:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de tus bots"))
        self.lista.InsertColumn(0, _("Nombre"), width=300)
        self.lista.InsertColumn(1, _("Usuario"), width=220)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        bs = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, handler in (
                (_("&Crear bot..."), self._on_crear),
                (_("Cambiar &nombre..."), self._on_nombre),
                (_("&Descripción..."), self._on_descripcion),
                (_("Descripción c&orta..."), self._on_descripcion_corta)):
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, handler)
            bs.Add(b, 0, wx.ALL, 4)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 4)
        sizer.Add(bs, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(sizer)

    def _cargar(self):
        speak(_("Cargando tus bots..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.mis_bots(self.account_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self.bots = res or []
            self.lista.DeleteAllItems()
            for i, (bid, nombre, usuario) in enumerate(self.bots):
                pos = self.lista.InsertItem(i, nombre)
                self.lista.SetItem(pos, 1, usuario)
                self.lista.establecer_lectura(pos, f"{nombre}. {usuario}")
            if self.bots:
                self.lista.Focus(0); self.lista.Select(0)
            speak(_("{0} bots.").format(len(self.bots)) if self.bots
                  else _("No posees ningún bot todavía."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _bot_sel(self):
        idx = self.lista.GetFocusedItem()
        if 0 <= idx < len(self.bots):
            return self.bots[idx]
        speak(_("Seleccione un bot."), interrupt=True)
        return None

    def _on_crear(self, event):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nombre del bot:"), _("Crear bot"))
        nombre = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not nombre:
            return
        d2 = CustomTextEntryDialog(self, _("Nombre de usuario del bot (debe terminar en 'bot'):"),
                                   _("Crear bot"))
        usuario = d2.GetValue().strip().lstrip("@") if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if not usuario:
            return
        speak(_("Creando bot..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.crear_bot(self.account_id, nombre, usuario)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo crear el bot: {0}").format(err))
            else:
                notificar(_("Bot creado. Apunta el token que te ha enviado BotFather."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _editar_texto(self, titulo, etiqueta, fn_set):
        b = self._bot_sel()
        if not b:
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, etiqueta, titulo)
        valor = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if valor is None:
            return
        def _bg(ctx):
            return fn_set(b[0], valor)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
            else:
                notificar(_("Hecho."))
                self._cargar()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_nombre(self, event):
        self._editar_texto(_("Cambiar nombre del bot"), _("Nuevo nombre del bot:"),
                           lambda bid, v: telegram_service.fijar_nombre_bot(self.account_id, bid, v))

    def _on_descripcion(self, event):
        self._editar_texto(_("Descripción del bot"), _("Descripción larga (se ve antes de iniciarlo):"),
                           lambda bid, v: telegram_service.fijar_descripcion_bot(self.account_id, bid, v))

    def _on_descripcion_corta(self, event):
        self._editar_texto(_("Descripción corta del bot"), _("Descripción corta (en el perfil del bot):"),
                           lambda bid, v: telegram_service.fijar_descripcion_corta_bot(self.account_id, bid, v))


class TelegramMediosChatDialog(wx.Dialog):
    """Multimedia y archivos compartidos de un chat, por tipo, más sus mensajes fijados."""

    # (etiqueta, clave de filtro del servicio)
    TIPOS = [
        (_("Fotos"), "fotos"), (_("Vídeos"), "videos"), (_("Música"), "musica"),
        (_("Notas de voz"), "voz"), (_("Archivos"), "archivos"), (_("GIF"), "gif"),
        (_("Enlaces"), "enlaces"), (_("Mensajes fijados"), "fijados"),
    ]

    def __init__(self, parent, account_id, chat_id, chat_title=""):
        super().__init__(parent, title=_("Multimedia y archivos del chat"), size=(640, 470),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Multimedia del chat"))
        self.account_id = account_id
        self.chat_id = chat_id
        self.chat_title = chat_title
        self.filas = []
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        self.CenterOnParent()
        self._cargar_total()
        self._cargar()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Tipo:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice = wx.Choice(self, choices=[e for e, _c in self.TIPOS], name=_("Tipo de contenido"))
        self.choice.SetSelection(0)
        self.choice.Bind(wx.EVT_CHOICE, lambda e: self._cargar())
        fila.Add(self.choice, 0, wx.ALL, 5)
        self.lbl_total = wx.StaticText(self, label=_("Total del chat: ..."))
        fila.Add(self.lbl_total, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 12)
        sizer.Add(fila, 0, wx.EXPAND)

        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de contenido del chat"))
        self.lista.InsertColumn(0, _("Descripción"), width=420)
        self.lista.InsertColumn(1, _("Fecha"), width=160)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_descargar)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_desc = wx.Button(self, label=_("&Descargar (Intro)"))
        b_desc.Bind(wx.EVT_BUTTON, self._on_descargar)
        bs.Add(b_desc, 0, wx.ALL, 5)
        # Música y notas de voz: escucharlas directamente en el SEGUNDO reproductor (radio/
        # podcast), sin salir del diálogo. Se descarga (si no lo estaba) y suena.
        b_play = wx.Button(self, label=_("&Reproducir (música y voz)"))
        b_play.Bind(wx.EVT_BUTTON, self._on_reproducir)
        bs.Add(b_play, 0, wx.ALL, 5)
        b_copia = wx.Button(self, label=_("&Copiar texto/enlace"))
        b_copia.Bind(wx.EVT_BUTTON, self._on_copiar)
        bs.Add(b_copia, 0, wx.ALL, 5)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        sizer.Add(bs, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(sizer)

    def _cargar_total(self):
        def _bg(ctx):
            return telegram_service.contar_mensajes_chat(self.account_id, self.chat_id)
        def _finish(n, err):
            if not err and n is not None:
                self.lbl_total.SetLabel(_("Total del chat: {0} mensajes").format(n))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _filtro(self):
        return self.TIPOS[self.choice.GetSelection()][1]

    def _cargar(self):
        filtro = self._filtro()
        speak(_("Cargando..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.medios_del_chat(self.account_id, self.chat_id, filtro)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo cargar: {0}").format(err))
                return
            self.filas = res or []
            self.lista.DeleteAllItems()
            for i, m in enumerate(self.filas):
                desc = m.get("file_name") or m.get("texto") or _("(sin descripción)")
                pos = self.lista.InsertItem(i, desc)
                self.lista.SetItem(pos, 1, m.get("fecha", ""))
                self.lista.establecer_lectura(pos, f"{desc}. {m.get('fecha','')}")
            if self.filas:
                self.lista.Focus(0); self.lista.Select(0)
            speak(_("{0} elementos.").format(len(self.filas)) if self.filas
                  else _("No hay elementos de este tipo."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        if 0 <= idx < len(self.filas):
            return self.filas[idx]
        speak(_("Seleccione un elemento."), interrupt=True)
        return None

    def _on_descargar(self, event):
        m = self._sel()
        if not m:
            return
        if self._filtro() in ("enlaces",):
            speak(_("Este elemento no es un archivo. Usa Copiar texto/enlace."), interrupt=True)
            return
        mid = m["message_id"]
        speak(_("Descargando..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.descargar_media(self.account_id, self.chat_id, mid)
        def _finish(ruta, err):
            if err:
                notificar(_("No se pudo descargar: {0}").format(err))
            elif ruta:
                speak(_("Descargado en {0}.").format(ruta), interrupt=True)
            else:
                speak(_("Este elemento no tiene archivo."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_reproducir(self, event):
        """Descarga (si hace falta) el audio/nota de voz enfocado y lo reproduce en el
        segundo reproductor (respeta la regla de 'una sola cosa a la vez' y el ducking)."""
        m = self._sel()
        if not m:
            return
        if self._filtro() not in ("musica", "voz", "videos"):
            speak(_("Elige un elemento de Música, Notas de voz o Vídeos."), interrupt=True)
            return
        from app.services.media_player.player2_service import player2_service
        if player2_service.esta_activo():
            desde = player2_service.descripcion_actual()
            from .accessible_controls import confirmar
            if confirmar(self, _("Ya se está reproduciendo {0}. ¿Detenerlo y reproducir esto?").format(
                    desde or _("otro audio")), _("Reproductor")) != wx.ID_YES:
                return
        mid = m["message_id"]
        titulo = m.get("file_name") or m.get("texto") or _("Audio de Telegram")
        speak(_("Preparando el audio..."), interrupt=True)

        def _bg(ctx):
            # Reutiliza la caché de descargas: si ya está en disco no vuelve a bajarlo.
            return telegram_service.descargar_media(self.account_id, self.chat_id, mid)

        def _finish(ruta, err):
            if err or not ruta:
                notificar(_("No se pudo obtener el audio: {0}").format(err or ""))
                return
            def _cb(ok):
                if not ok:
                    notificar(_("No se pudo reproducir el audio."))
            player2_service.cargar_y_reproducir(str(ruta), titulo, "podcast", callback=_cb)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_copiar(self, event):
        m = self._sel()
        if not m:
            return
        texto = m.get("texto") or m.get("file_name") or ""
        if not texto:
            speak(_("No hay texto que copiar."), interrupt=True)
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(texto))
                wx.TheClipboard.Flush()
            finally:
                wx.TheClipboard.Close()
        notificar(_("Copiado al portapapeles."))
