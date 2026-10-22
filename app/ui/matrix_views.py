# -*- coding: utf-8 -*-
"""Controlador de Matrix enganchado al MainFrame (patrón IRCController, tanda M1).

M1: nodo en el árbol (Matrix → cuenta → Salas/Mensajes directos/Invitaciones), lista de
salas desde la BD con no-leídos, conectar/desconectar, aceptar/rechazar invitaciones y
visor simple de últimos mensajes. La Conversación en vivo llega en M2.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
import wx

from app.core.database import db
from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager
from .accessible_controls import mostrar_info, confirmar


def render_plantilla_matrix(clave, valores):
    """Lectura de una fila de Matrix según la plantilla del usuario (editor de plantillas)."""
    from collections import defaultdict
    from app.core.plantillas import pulir_lectura, definicion
    d = definicion(clave)
    plantilla = config.get("accessibility", clave, "") or (d["default"] if d else "{content}")
    try:
        return pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
    except Exception:
        return pulir_lectura((d["default"] if d else "{content}").format_map(defaultdict(str, valores)))


class MatrixController:
    """Controlador de Matrix integrado en la ventana principal."""

    BUFFER_LABELS = {
        "rooms": _("Salas"),
        "dms": _("Mensajes directos"),
        "invites": _("Invitaciones"),
    }

    def __init__(self, frame):
        self.f = frame
        self._refresco_pendiente = False
        event_bus.subscribe("matrix_evento", self._on_evento_matrix)

    # ---------------- Árbol ----------------
    def construir_servicio(self, root):
        tree = self.f.tree_nav
        n_mx = tree.AppendItem(root, _("Matrix"),
                               data={"type": "category_root", "service": "matrix"})
        accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'matrix' AND active = 1 ORDER BY position, id")
        for r_id, r_name in accs:
            n_acc = tree.AppendItem(n_mx, r_name,
                                    data={"type": "account", "account_id": r_id, "service": "matrix"})
            from app.core.config_manager import config
            ocultos = set(config.get("matrix", "hidden_buffers", []) or [])
            for clave in ("rooms", "dms", "invites"):
                if clave in ocultos:
                    continue
                tree.AppendItem(n_acc, self.BUFFER_LABELS[clave], data={
                    "type": "matrix_timeline", "mx_type": clave,
                    "account_id": r_id, "service": "matrix",
                    "base_label": self.BUFFER_LABELS[clave]})
        return n_mx

    # ---------------- Selección ----------------
    def on_select(self, item, data, t):
        f = self.f
        f.active_matrix_account_id = data.get("account_id")
        f._aplicar_visibilidad_detalles()
        if t == "matrix_timeline":
            f.active_timeline_type = data.get("mx_type")
            f.lbl_buffer_titulo.SetLabel("Matrix - " + f.tree_nav.GetItemText(item))
            self.cargar_listado()
        else:
            f.lista_items.DeleteAllItems()
            f.lista_items.limpiar_lecturas()

    # ---------------- Carga de la lista ----------------
    def cargar_listado(self):
        f = self.f
        if not f.active_matrix_account_id:
            return
        tipo = f.active_timeline_type
        f._modo_lista = "matrix_salas"
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Sala"), width=220)
        f.lista_items.InsertColumn(1, _("Último mensaje"), width=340)
        f.lista_items.InsertColumn(2, _("No leídos"), width=80)
        f.lista_items.InsertColumn(3, _("Tema"), width=240)
        if tipo == "invites":
            cond = "invitacion = 1"
        elif tipo == "dms":
            cond = "invitacion = 0 AND es_dm = 1"
        else:
            cond = "invitacion = 0 AND es_dm = 0"
        filas = db.query(f"""
            SELECT id, nombre, tema, unread_count, last_message, invitacion, COALESCE(cifrada,0),
                   COALESCE(es_espacio,0), COALESCE(etiqueta,'')
            FROM matrix_salas
            WHERE account_id = ? AND {cond}
            ORDER BY CASE COALESCE(etiqueta,'') WHEN 'favorita' THEN 0 WHEN 'baja' THEN 2 ELSE 1 END,
                     es_espacio DESC, unread_count DESC, last_message_date DESC, nombre
        """, (f.active_matrix_account_id,))
        for db_id, nombre, tema, no_leidos, ultimo, invitada, cifrada, es_espacio, tag in filas:
            etiqueta = _("{0} (invitación)").format(nombre) if invitada else (nombre or "")
            if es_espacio and not invitada:
                etiqueta = _("{0} (espacio)").format(etiqueta)
            elif cifrada and not invitada:
                etiqueta = _("{0} (cifrada)").format(etiqueta)
            if tag == "favorita" and not invitada:
                etiqueta = _("{0} (favorita)").format(etiqueta)
            idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), etiqueta)
            f.lista_items.SetItem(idx, 1, (ultimo or "").replace("\n", " "))
            f.lista_items.SetItem(idx, 2, str(no_leidos or 0))
            f.lista_items.SetItem(idx, 3, (tema or "").replace("\n", " "))
            lectura = render_plantilla_matrix("matrix_chat", {
                "sala": etiqueta, "content": ultimo or "",
                "unread": _("{0} sin leer").format(no_leidos) if no_leidos else "",
                "topic": tema or ""})
            f.lista_items.establecer_lectura(idx, lectura)
            f.lista_items_db_ids.append(db_id)
        f._restaurar_foco_lista()
        if not filas and not self._conectada(f.active_matrix_account_id):
            speak(_("Sin salas. Conecta la cuenta desde el menú Matrix."), interrupt=False)

    # ---------------- Activación y menú contextual ----------------
    def on_item_activated(self, index):
        """Intro: en una invitación pregunta aceptar; en una sala, visor de últimos mensajes
        (la Conversación en vivo llega en M2)."""
        f = self.f
        if f._modo_lista != "matrix_salas":
            return
        if index < 0 or index >= len(f.lista_items_db_ids):
            return
        db_id = f.lista_items_db_ids[index]
        fila = db.query_one("SELECT room_id, nombre, invitacion FROM matrix_salas WHERE id = ?", (db_id,))
        if not fila:
            return
        room_id, nombre, invitada = fila
        if invitada:
            self._responder_invitacion(room_id, nombre)
        else:
            self.abrir_conversacion(db_id)

    def abrir_conversacion(self, db_id):
        f = self.f
        fila = db.query_one("SELECT room_id, nombre FROM matrix_salas WHERE id = ?", (db_id,))
        if not fila:
            return
        from .matrix_conversation import MatrixConversationDialog
        dlg = MatrixConversationDialog(f, f.active_matrix_account_id, fila[0], fila[1] or fila[0])
        dlg.ShowModal()
        dlg.Destroy()
        self.cargar_listado()

    def on_context_menu(self, menu, idx):
        f = self.f
        if f._modo_lista != "matrix_salas":
            return
        if idx < 0 or idx >= len(f.lista_items_db_ids):
            return
        db_id = f.lista_items_db_ids[idx]
        fila = db.query_one("SELECT room_id, nombre, invitacion, unread_count FROM matrix_salas WHERE id = ?", (db_id,))
        if not fila:
            return
        room_id, nombre, invitada, no_leidos = fila
        if invitada:
            it = menu.Append(wx.ID_ANY, _("Aceptar la invitación"))
            f.Bind(wx.EVT_MENU, lambda e: self._unirse(room_id, nombre), it)
            it = menu.Append(wx.ID_ANY, _("Rechazar la invitación"))
            f.Bind(wx.EVT_MENU, lambda e: self._salir(room_id, nombre, rechazo=True), it)
            return
        it = menu.Append(wx.ID_ANY, _("Abrir conversación"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.abrir_conversacion(d), it)
        it = menu.Append(wx.ID_ANY, _("Ver últimos mensajes"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.ver_ultimos_mensajes(d), it)
        if no_leidos:
            it = menu.Append(wx.ID_ANY, _("Marcar como leída"))
            f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.marcar_leida(d), it)
        # Nivel de aviso sonoro de la sala (patrón IRC: todo / solo menciones / silencio).
        from app.services.matrix import matrix_service
        nivel = matrix_service.nivel_aviso(f.active_matrix_account_id, room_id)
        sub = wx.Menu()
        for clave, etiqueta in (("all", _("Avisar de todo")),
                                ("mentions", _("Solo cuando me mencionen")),
                                ("none", _("Silencio (sin sonidos)"))):
            it2 = sub.AppendRadioItem(wx.ID_ANY, etiqueta)
            if clave == nivel:
                it2.Check(True)
            f.Bind(wx.EVT_MENU, lambda e, rid=room_id, k=clave: self._fijar_nivel(rid, k), it2)
        menu.AppendSubMenu(sub, _("Nivel de aviso"))
        # Si la sala es un espacio, ofrecer ver sus salas.
        fila_esp = db.query_one("SELECT COALESCE(es_espacio,0) FROM matrix_salas WHERE id = ?", (db_id,))
        if fila_esp and fila_esp[0]:
            it = menu.Append(wx.ID_ANY, _("Ver las salas de este espacio..."))
            f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self.ver_salas_espacio(rid, n), it)
        # M7: etiqueta de sala (favorita/baja prioridad), sincronizada con el servidor (m.tag).
        etiqueta_actual = (db.query_one("SELECT COALESCE(etiqueta,'') FROM matrix_salas WHERE id = ?",
                                        (db_id,)) or [""])[0]
        sub_tag = wx.Menu()
        for clave, texto in (("favorita", _("Favorita (arriba del todo)")),
                             ("baja", _("Baja prioridad (abajo del todo)")),
                             ("", _("Sin etiqueta"))):
            it2 = sub_tag.AppendRadioItem(wx.ID_ANY, texto)
            if clave == etiqueta_actual:
                it2.Check(True)
            f.Bind(wx.EVT_MENU, lambda e, rid=room_id, k=clave: self._fijar_etiqueta(rid, k), it2)
        menu.AppendSubMenu(sub_tag, _("Etiqueta de la sala"))
        it = menu.Append(wx.ID_ANY, _("Miembros y moderación..."))
        f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self.ver_miembros(rid, n), it)
        it = menu.Append(wx.ID_ANY, _("Ver los hilos de la sala..."))
        f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._ver_hilos(rid, n), it)
        it = menu.Append(wx.ID_ANY, _("Ver los mensajes fijados..."))
        f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._ver_fijados(rid, n), it)
        it = menu.Append(wx.ID_ANY, _("Reportar la sala a los administradores..."))
        f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._reportar_sala(rid, n), it)
        it = menu.Append(wx.ID_ANY, _("Dispositivos cifrados de la sala..."))
        f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._dispositivos_sala(rid, n), it)
        # Opciones de ADMINISTRACIÓN de la sala: solo si tengo poder para cambiar sus ajustes
        # (soy administrador/moderador de la sala). Si no, ni siquiera aparecen.
        if matrix_service.soy_admin_de_sala(f.active_matrix_account_id, room_id):
            it = menu.Append(wx.ID_ANY, _("Ajustes de la sala (administración)..."))
            f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._ajustes_sala(rid, n), it)
            it = menu.Append(wx.ID_ANY, _("Permitir llamar a la puerta (knock)"))
            f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._permitir_knock(rid, n), it)
            it = menu.Append(wx.ID_ANY, _("Cambiar el avatar de la sala..."))
            f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._cambiar_avatar_sala(rid, n), it)
        it = menu.Append(wx.ID_ANY, _("Añadir a un espacio..."))
        f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self._a_espacio(rid, n), it)
        it = menu.Append(wx.ID_ANY, _("Exportar conversación..."))
        f.Bind(wx.EVT_MENU, lambda e, rid=room_id, n=nombre: self.exportar_sala(rid, n), it)
        it = menu.Append(wx.ID_ANY, _("Salir de la sala"))
        f.Bind(wx.EVT_MENU, lambda e: self._salir(room_id, nombre), it)
        it = menu.Append(wx.ID_ANY, _("Salir y OLVIDAR la sala..."))
        f.Bind(wx.EVT_MENU, lambda e: self._salir(room_id, nombre, olvidar=True), it)

    def _fijar_etiqueta(self, room_id, etiqueta):
        acc = self.f.active_matrix_account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.fijar_etiqueta_sala(acc, room_id, etiqueta)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo etiquetar: {0}").format(err))
            else:
                textos = {"favorita": _("Marcada como favorita."),
                          "baja": _("Marcada como baja prioridad.")}
                notificar(textos.get(etiqueta, _("Etiqueta quitada.")))
                self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _ajustes_sala(self, room_id, nombre):
        from .matrix_dialogs import MatrixAjustesSalaDialog
        dlg = MatrixAjustesSalaDialog(self.f, self.f.active_matrix_account_id, room_id, nombre)
        dlg.ShowModal()
        dlg.Destroy()
        self._refrescar_si_activa(self.f.active_matrix_account_id)

    def _permitir_knock(self, room_id, nombre):
        """Activa en la sala la regla de acceso 'knock' (que la gente pueda pedir entrar)."""
        acc = self.f.active_matrix_account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.permitir_llamar_puerta(acc, room_id)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Ahora se puede llamar a la puerta de «{0}».").format(nombre) if not e
            else _("No se pudo: {0}").format(e)), pass_context=True)

    def _ver_fijados(self, room_id, nombre):
        """Muestra los mensajes fijados de la sala en una lista accesible de solo lectura."""
        acc = self.f.active_matrix_account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.mensajes_fijados_detalle(acc, room_id)

        def _fin(fijados, err):
            from .accessible_controls import mostrar_info
            if err:
                notificar(_("No se pudieron leer los fijados: {0}").format(err))
                return
            if not fijados:
                notificar(_("Esta sala no tiene mensajes fijados."))
                return
            from app.services.matrix.matrix_common import hora_local
            lineas = []
            for fj in fijados:
                marca = hora_local(fj.get("date") or "", con_fecha=True) if fj.get("date") else ""
                quien = fj.get("quien") or ""
                lineas.append(f"[{marca}] <{quien}> {fj.get('texto','')}".strip())
            mostrar_info(self.f, "\n\n".join(lineas),
                         _("Mensajes fijados de {0}").format(nombre))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _reportar_sala(self, room_id, nombre):
        """Reporta la sala entera a los administradores del servidor (cualquier usuario puede)."""
        from .accessible_controls import CustomTextEntryDialog, confirmar
        if confirmar(self.f, _("¿Reportar la sala «{0}» a los administradores del servidor?").format(
                nombre), _("Reportar sala")) != wx.ID_YES:
            return
        d = CustomTextEntryDialog(self.f, _("Motivo del reporte (opcional):"), _("Reportar sala"))
        motivo = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        acc = self.f.active_matrix_account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.reportar_sala(acc, room_id, motivo)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Sala reportada a los administradores.") if not e
            else _("No se pudo reportar: {0}").format(e)), pass_context=True)

    def _cambiar_avatar_sala(self, room_id, nombre):
        """Elige una imagen y la fija como avatar de la sala (requiere permiso)."""
        dlg = wx.FileDialog(self.f, _("Elige la imagen del avatar de la sala"),
                            wildcard=_("Imágenes|*.png;*.jpg;*.jpeg;*.gif;*.webp"),
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        ruta = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not ruta:
            return
        acc = self.f.active_matrix_account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.cambiar_avatar_sala(acc, room_id, ruta)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Avatar de la sala cambiado.") if not e
            else _("No se pudo cambiar el avatar (¿tienes permiso?): {0}").format(e)),
            pass_context=True)

    def _ver_hilos(self, room_id, nombre):
        from .matrix_dialogs import MatrixHilosSalaDialog
        dlg = MatrixHilosSalaDialog(self.f, self.f.active_matrix_account_id, room_id, nombre)
        dlg.ShowModal()
        dlg.Destroy()

    def _dispositivos_sala(self, room_id, nombre):
        from .matrix_dialogs import MatrixDispositivosSalaDialog
        dlg = MatrixDispositivosSalaDialog(self.f, self.f.active_matrix_account_id, room_id, nombre)
        dlg.ShowModal()
        dlg.Destroy()

    def _a_espacio(self, room_id, nombre):
        """Añade la sala a uno de MIS espacios (elige de una lista)."""
        acc = self.f.active_matrix_account_id
        from app.services.matrix import matrix_service
        espacios = [e for e in matrix_service.mis_espacios(acc) if e[0] != room_id]
        if not espacios:
            notificar(_("No tienes espacios: crea uno desde el menú Matrix."))
            return
        opciones = [n for _r, n in espacios]
        dlg = wx.SingleChoiceDialog(self.f, _("¿A qué espacio añadir '{0}'?").format(nombre),
                                    _("Añadir a un espacio"), opciones)
        idx = dlg.GetSelection() if dlg.ShowModal() == wx.ID_OK else -1
        dlg.Destroy()
        if idx < 0:
            return
        space_id = espacios[idx][0]

        def _bg(ctx):
            matrix_service.sala_a_espacio(acc, space_id, room_id)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Sala añadida al espacio.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    # ---------------- Acciones ----------------
    def _conectada(self, account_id) -> bool:
        try:
            from app.services.matrix import matrix_service
            return matrix_service.esta_conectada(account_id)
        except Exception:
            return False

    def conectar_cuenta(self, account_id=None):
        acc = account_id or self.f.active_matrix_account_id
        if not acc:
            speak(_("Sitúate en una cuenta de Matrix del árbol."), interrupt=True)
            return
        if self._conectada(acc):
            speak(_("Esta cuenta ya está conectada."), interrupt=True)
            return
        speak(_("Conectando con Matrix..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.conectar(acc)
            return True

        def _fin(res, err):
            from app.core.task_manager import TareaDuplicadaError
            if isinstance(err, TareaDuplicadaError):
                speak(_("Ya se está conectando esta cuenta."), interrupt=False)
                return
            if err:
                notificar(_("No se pudo conectar: {0}").format(err))
            else:
                notificar(_("Conectado a Matrix; sincronizando en segundo plano."))
                config.set("matrix", f"autoconnect_{acc}", True)  # recordar: conectada al reiniciar
        task_manager.start_task(_bg, callback=_fin, pass_context=True,
                                unique_key=f"matrix:conectar:{acc}")

    def desconectar_cuenta(self, account_id=None):
        acc = account_id or self.f.active_matrix_account_id
        if not acc or not self._conectada(acc):
            speak(_("Esta cuenta no está conectada."), interrupt=True)
            return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.desconectar(acc)
            return True

        def _fin(r, e):
            notificar(_("Desconectado de Matrix.") if not e else _("Error al desconectar: {0}").format(e))
            # Recordar que quieres esta cuenta DESCONECTADA: al reiniciar NO se conectará sola.
            config.set("matrix", f"autoconnect_{acc}", False)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def conectar_cuentas_auto(self):
        """Conecta al arrancar SOLO las cuentas de Matrix que dejaste conectadas. Si desconectaste una
        a mano y cerraste la app, arranca desconectada hasta que la conectes tú."""
        try:
            filas = db.query("SELECT id FROM accounts WHERE service_id = 'matrix' AND active = 1")
        except Exception:
            filas = []
        for (acc,) in filas:
            if not config.get("matrix", f"autoconnect_{acc}", True):
                continue  # la dejaste desconectada: no la reconectamos sola
            def _bg(ctx, a=acc):
                from app.services.matrix import matrix_service
                matrix_service.conectar(a)
                return True
            task_manager.start_task(_bg, pass_context=True, unique_key=f"matrix:conectar:{acc}")

    def exportar_sala(self, room_id, nombre):
        """Exporta el historial local de la sala a un archivo de texto."""
        acc = self.f.active_matrix_account_id
        if not acc:
            return
        seguro = "".join(c for c in (nombre or "sala") if c.isalnum() or c in "-_ ").strip() or "sala"
        dlg = wx.FileDialog(self.f, _("Guardar conversación"),
                            defaultFile=f"matrix_{seguro}.txt",
                            wildcard=_("Texto|*.txt"),
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ruta = dlg.GetPath()
        dlg.Destroy()

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.exportar_sala(acc, room_id, ruta)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Conversación exportada ({0} mensajes).").format(r) if not e
            else _("No se pudo exportar: {0}").format(e)), pass_context=True)

    def _fijar_nivel(self, room_id, nivel):
        from app.services.matrix import matrix_service
        matrix_service.fijar_nivel_aviso(self.f.active_matrix_account_id, room_id, nivel)
        etiquetas = {"all": _("avisar de todo"), "mentions": _("solo menciones"), "none": _("silencio")}
        notificar(_("Nivel de aviso: {0}.").format(etiquetas.get(nivel, nivel)))

    def ver_miembros(self, room_id, nombre):
        """Diálogo de miembros con rangos legibles y acciones de moderación."""
        from .matrix_dialogs import MatrixMiembrosDialog
        dlg = MatrixMiembrosDialog(self.f, self.f.active_matrix_account_id, room_id, nombre)
        dlg.ShowModal()
        dlg.Destroy()

    def ver_salas_espacio(self, room_id, nombre):
        """Lista las salas de un espacio; Intro se une a la elegida."""
        acc = self.f.active_matrix_account_id
        speak(_("Pidiendo las salas del espacio..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            # M7: la jerarquía del SERVIDOR trae también salas a las que no te has unido, con
            # su nombre y miembros; el estado local (m.space.child) queda como respaldo.
            try:
                salas = matrix_service.jerarquia_espacio(acc, room_id)
                if salas:
                    return salas
            except Exception:
                pass
            return matrix_service.salas_de_espacio(acc, room_id)

        def _fin(salas, err):
            if err:
                notificar(_("No se pudieron pedir: {0}").format(err))
                return
            if not salas:
                mostrar_info(self.f, _("Este espacio no anuncia salas."), nombre)
                return
            opciones = [(_("{0} ({1} miembros)").format(s["nombre"], s["miembros"])
                         if s.get("miembros") else s["nombre"]) for s in salas]
            dlg = wx.SingleChoiceDialog(self.f, _("{0} salas. Elige una para unirte:").format(len(salas)),
                                        _("Salas de {0}").format(nombre), opciones)
            if dlg.ShowModal() == wx.ID_OK:
                elegida = salas[dlg.GetSelection()]
                dlg.Destroy()
                self._unirse(elegida["room_id"], elegida["nombre"])
            else:
                dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def directorio_salas(self, account_id=None):
        """Directorio público de salas del servidor, con filtro."""
        acc = account_id or self.f.active_matrix_account_id
        if not acc or not self._conectada(acc):
            speak(_("Primero conecta la cuenta (menú Matrix)."), interrupt=True)
            return
        from .matrix_dialogs import MatrixDirectorioDialog
        dlg = MatrixDirectorioDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()
        self._refrescar_si_activa(acc)

    def _responder_invitacion(self, room_id, nombre):
        """Intro sobre una invitación: preguntar si aceptar (Sí), rechazar (No) o nada."""
        r = confirmar(self.f, _("Invitación a '{0}'. ¿Aceptarla? (No la rechaza; "
                                "Cancelar la deja pendiente.)").format(nombre),
                      _("Invitación de Matrix"), con_cancelar=True)
        if r == wx.ID_YES:
            self._unirse(room_id, nombre)
        elif r == wx.ID_NO:
            self._salir(room_id, nombre, rechazo=True)

    def unirse_preguntando(self, account_id=None):
        """Menú Matrix: unirse a una sala por alias (#sala:servidor) o id."""
        acc = account_id or self.f.active_matrix_account_id
        if not acc or not self._conectada(acc):
            speak(_("Primero conecta la cuenta (menú Matrix)."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Sala a la que unirte (ej: #sala:matrix.org):"),
                                    _("Unirse a una sala"))
        sala = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not sala:
            return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.unirse_sala(acc, sala)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo entrar en {0}: {1}").format(sala, err))
            else:
                notificar(_("Dentro de {0}.").format(sala))
                self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def crear_sala_preguntando(self, account_id=None):
        acc = account_id or self.f.active_matrix_account_id
        if not acc or not self._conectada(acc):
            speak(_("Primero conecta la cuenta (menú Matrix)."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Nombre de la nueva sala (privada):"),
                                    _("Crear sala"))
        nombre = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not nombre:
            return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.crear_sala(acc, nombre, publica=False)
        task_manager.start_task(_bg, callback=lambda r, e: (
            notificar(_("Sala '{0}' creada.").format(nombre) if not e
                      else _("No se pudo crear: {0}").format(e)),
            self._refrescar_si_activa(acc) if not e else None), pass_context=True)

    def dm_preguntando(self, account_id=None):
        """Menú Matrix: abrir un mensaje directo con un usuario (@nombre:servidor)."""
        acc = account_id or self.f.active_matrix_account_id
        if not acc or not self._conectada(acc):
            speak(_("Primero conecta la cuenta (menú Matrix)."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Usuario (ej: @nombre:matrix.org):"),
                                    _("Mensaje directo"))
        usuario = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not usuario:
            return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.crear_dm(acc, usuario)

        def _fin(room_id, err):
            if err:
                notificar(_("No se pudo abrir el directo: {0}").format(err))
                return
            from .matrix_conversation import MatrixConversationDialog
            dlg2 = MatrixConversationDialog(self.f, acc, room_id, usuario)
            dlg2.ShowModal()
            dlg2.Destroy()
            self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _unirse(self, room_id, nombre):
        acc = self.f.active_matrix_account_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            from nio.responses import JoinResponse
            with matrix_service._lock:
                cliente = matrix_service._clients.get(acc)
            if cliente is None:
                raise Exception(_("Primero conecta la cuenta (menú Matrix)."))
            resp = matrix_service._run(cliente.join(room_id), timeout=30.0)
            if not isinstance(resp, JoinResponse):
                raise Exception(getattr(resp, "message", None) or str(resp))
            db.execute("UPDATE matrix_salas SET invitacion = 0 WHERE account_id = ? AND room_id = ?",
                       (acc, room_id))
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo entrar en {0}: {1}").format(nombre, err))
            else:
                notificar(_("Dentro de {0}.").format(nombre))
                self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _salir(self, room_id, nombre, rechazo=False, olvidar=False):
        acc = self.f.active_matrix_account_id
        if olvidar:
            pregunta = _("¿Salir de '{0}' y OLVIDARLA? Desaparecerá también del historial del "
                         "servidor y no podrás recuperar sus mensajes antiguos.").format(nombre)
        elif rechazo:
            pregunta = _("¿Rechazar la invitación a '{0}'?").format(nombre)
        else:
            pregunta = _("¿Salir de la sala '{0}'?").format(nombre)
        if confirmar(self.f, pregunta, _("Matrix")) != wx.ID_YES:
            return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.salir_sala(acc, room_id, olvidar=olvidar)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Invitación rechazada.") if rechazo
                          else _("Has salido de {0}.").format(nombre))
                self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def marcar_leida(self, db_id):
        db.execute("UPDATE matrix_salas SET unread_count = 0 WHERE id = ?", (db_id,))
        db.execute("""UPDATE matrix_mensajes SET leido = 1
                      WHERE account_id = ? AND room_id = (SELECT room_id FROM matrix_salas WHERE id = ?)""",
                   (self.f.active_matrix_account_id, db_id))
        self.cargar_listado()

    def ver_ultimos_mensajes(self, db_id):
        """Visor simple de los últimos mensajes de la sala (la Conversación en vivo, en M2)."""
        f = self.f
        fila = db.query_one("SELECT room_id, nombre FROM matrix_salas WHERE id = ?", (db_id,))
        if not fila:
            return
        room_id, nombre = fila
        filas = db.query("""
            SELECT sender_nombre, sender, texto, tipo, date FROM matrix_mensajes
            WHERE account_id = ? AND room_id = ?
            ORDER BY id DESC LIMIT 200
        """, (f.active_matrix_account_id, room_id))
        if not filas:
            mostrar_info(f, _("Todavía no hay mensajes guardados de esta sala."), nombre or room_id)
            return
        lineas = []
        for nom, sender, texto, tipo, fecha in reversed(filas):
            from app.services.matrix.matrix_common import hora_local
            hora = hora_local(fecha)
            quien = nom or sender
            if tipo == "accion":
                lineas.append(f"{hora} * {quien} {texto}")
            else:
                lineas.append(f"{hora} <{quien}> {texto}")
        self.marcar_leida(db_id)
        f._mostrar_texto_largo(_("Últimos mensajes de {0}").format(nombre or room_id), "\n".join(lineas))

    # ---------------- Menú contextual del árbol ----------------
    def menu_contextual_arbol(self, menu, data, item):
        f = self.f
        if data.get("type") != "account":
            return False
        acc = data.get("account_id")
        if self._conectada(acc):
            it = menu.Append(wx.ID_ANY, _("Desconectar"))
            f.Bind(wx.EVT_MENU, lambda e, a=acc: self.desconectar_cuenta(a), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Conectar"))
            f.Bind(wx.EVT_MENU, lambda e, a=acc: self.conectar_cuenta(a), it)
        return True

    # ---------------- Eventos en vivo ----------------
    def _on_evento_matrix(self, datos):
        """(Hilo MatrixLoop) Coalescer refrescos + avisos de estado."""
        try:
            acc = (datos or {}).get("account_id")
            tipo = (datos or {}).get("tipo")
        except Exception:
            return
        if tipo in ("conectado", "desconectado", "sincronizada", "invitacion",
                    "verificacion_solicitada", "verificacion_emojis", "verificacion_ok",
                    "verificacion_cancelada"):
            wx.CallAfter(self._avisar_estado, acc, tipo, datos)
        if self._refresco_pendiente:
            return
        self._refresco_pendiente = True
        wx.CallAfter(self._programar_refresco, acc)

    def _avisar_estado(self, acc, tipo, datos):
        try:
            fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (acc,))
            cuenta = fila[0] if fila else _("Matrix")
            if tipo == "desconectado" and not datos.get("esperado"):
                notificar(_("Se perdió la conexión con {0}; reintentando en segundo plano.").format(cuenta))
            elif tipo == "conectado" and datos.get("reconexion"):
                notificar(_("Reconectado a {0}.").format(cuenta))
            elif tipo == "sincronizada":
                notificar(_("{0}: salas sincronizadas.").format(cuenta))
            elif tipo == "invitacion":
                event_bus.emit("sound_trigger", "matrix_highlight")
                notificar(_("{0}: invitación a {1}.").format(cuenta, datos.get("nombre") or ""))
            elif tipo == "verificacion_solicitada":
                notificar(_("{0}: verificación iniciada desde otro dispositivo; enseguida aparecerán "
                            "los emojis para comparar.").format(cuenta))
            elif tipo == "verificacion_emojis":
                self._dialogo_verificacion(acc, datos)
            elif tipo == "verificacion_ok":
                notificar(_("{0}: ¡sesión verificada! Este dispositivo ya es de confianza.").format(cuenta))
            elif tipo == "verificacion_cancelada":
                notificar(_("{0}: verificación cancelada. {1}").format(cuenta, datos.get("motivo") or ""))
        except Exception:
            pass

    def _dialogo_verificacion(self, acc, datos):
        """Alguien (otro cliente TUYO como Element, u otro usuario que quiere comprobar que eres
        tú) inició la verificación por emojis: se muestran los emojis y, si coinciden con los del
        otro dispositivo, se confirma y ambas partes quedan verificadas."""
        txid = datos.get("txid")
        emojis = datos.get("emojis") or ""
        r = confirmar(self.f, _("Verificación de Matrix por emojis.\n\nComprueba que estos "
                                "emojis coinciden EXACTAMENTE con los que ve la otra persona (o "
                                "tu otro dispositivo):\n\n{0}\n\n¿Coinciden?").format(emojis),
                      _("Verificar en Matrix"))
        acc_local, txid_local = acc, txid

        def _bg(ctx):
            from app.services.matrix import matrix_service
            if r == wx.ID_YES:
                matrix_service.confirmar_verificacion(acc_local, txid_local)
            else:
                matrix_service.cancelar_verificacion(acc_local, txid_local)
            return True
        task_manager.start_task(_bg, pass_context=True)

    def _programar_refresco(self, acc):
        wx.CallLater(1200, self._refrescar_por_evento, acc)

    def _refrescar_por_evento(self, acc):
        self._refresco_pendiente = False
        self._refrescar_si_activa(acc)

    def _refrescar_si_activa(self, acc):
        f = self.f
        try:
            if f.active_matrix_account_id == acc and f._modo_lista == "matrix_salas":
                self.cargar_listado()
        except Exception:
            pass
