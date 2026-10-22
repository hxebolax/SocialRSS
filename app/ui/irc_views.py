# -*- coding: utf-8 -*-
"""Controlador de IRC enganchado al MainFrame (patrón XController/BlueskyController).

Tanda I1: nodo en el árbol (IRC → red → Canales/Privados/Avisos del servidor), lista de
chats desde la BD, conectar/desconectar, unirse/salir de canales y un visor simple de los
últimos mensajes. La ventana de conversación en vivo llega en la tanda I2.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext para textos en import-time
import wx

from app.core.database import db
from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager
from .accessible_controls import mostrar_info, confirmar, CustomTextEntryDialog


def render_plantilla_irc(clave, valores):
    """Lectura de una fila IRC según la plantilla del usuario (editor de plantillas, red IRC)."""
    from collections import defaultdict
    from app.core.plantillas import pulir_lectura, definicion
    d = definicion(clave)
    plantilla = config.get("accessibility", clave, "") or (d["default"] if d else "{content}")
    try:
        return pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
    except Exception:
        return pulir_lectura((d["default"] if d else "{content}").format_map(defaultdict(str, valores)))


class IRCController:
    """Controlador de IRC integrado en la ventana principal."""

    BUFFER_LABELS = {
        "channels": _("Canales"),
        "privates": _("Privados"),
        "server": _("Avisos del servidor"),
    }

    def __init__(self, frame):
        self.f = frame
        self._refresco_pendiente = False
        # Eventos en vivo del servicio (hilo IRCLoop) → refresco COALESCIDO de la lista.
        event_bus.subscribe("irc_evento", self._on_evento_irc)

    # ---------------- Árbol ----------------
    def construir_servicio(self, root):
        tree = self.f.tree_nav
        n_irc = tree.AppendItem(root, _("IRC"),
                                data={"type": "category_root", "service": "irc"})
        accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'irc' AND active = 1 ORDER BY position, id")
        for r_id, r_name in accs:
            n_acc = tree.AppendItem(n_irc, r_name,
                                    data={"type": "account", "account_id": r_id, "service": "irc"})
            from app.core.config_manager import config
            ocultos = set(config.get("irc", "hidden_buffers", []) or [])
            for clave in ("channels", "privates", "server"):
                if clave in ocultos:
                    continue
                tree.AppendItem(n_acc, self.BUFFER_LABELS[clave], data={
                    "type": "irc_timeline", "irc_type": clave,
                    "account_id": r_id, "service": "irc",
                    "base_label": self.BUFFER_LABELS[clave]})
        return n_irc

    # ---------------- Selección ----------------
    def on_select(self, item, data, t):
        f = self.f
        f.active_irc_account_id = data.get("account_id")
        f._aplicar_visibilidad_detalles()
        if t == "irc_timeline":
            f.active_timeline_type = data.get("irc_type")
            f.lbl_buffer_titulo.SetLabel("IRC - " + f.tree_nav.GetItemText(item))
            self.cargar_listado()
        else:
            f.lista_items.DeleteAllItems()
            f.lista_items.limpiar_lecturas()

    # ---------------- Carga de la lista ----------------
    def cargar_listado(self):
        f = self.f
        if not f.active_irc_account_id:
            return
        tipo = f.active_timeline_type
        if tipo == "server":
            self._cargar_avisos()
        else:
            self._cargar_chats(tipo)

    def _cargar_chats(self, tipo):
        """Lista de canales (o privados) como chats: nombre, último mensaje, no leídos, tema."""
        f = self.f
        f._modo_lista = "irc_chats"
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Nombre"), width=180)
        f.lista_items.InsertColumn(1, _("Último mensaje"), width=340)
        f.lista_items.InsertColumn(2, _("No leídos"), width=80)
        f.lista_items.InsertColumn(3, _("Tema"), width=280)
        es_privado = 1 if tipo == "privates" else 0
        filas = db.query("""
            SELECT id, name, topic, unread_count, joined, last_message
            FROM irc_channels
            WHERE account_id = ? AND es_privado = ? AND name <> '*'
            ORDER BY unread_count DESC, last_message_date DESC, name
        """, (f.active_irc_account_id, es_privado))
        for db_id, nombre, tema, no_leidos, unido, ultimo in filas:
            etiqueta = nombre if (unido or es_privado) else _("{0} (no unido)").format(nombre)
            idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), etiqueta)
            f.lista_items.SetItem(idx, 1, (ultimo or "").replace("\n", " "))
            f.lista_items.SetItem(idx, 2, str(no_leidos or 0))
            f.lista_items.SetItem(idx, 3, (tema or "").replace("\n", " "))
            lectura = render_plantilla_irc("irc_chat", {
                "chat": etiqueta, "content": ultimo or "",
                "unread": _("{0} sin leer").format(no_leidos) if no_leidos else "",
                "topic": tema or ""})
            f.lista_items.establecer_lectura(idx, lectura)
            f.lista_items_db_ids.append(db_id)
        f._restaurar_foco_lista()
        if not filas:
            conectada = self._conectada(f.active_irc_account_id)
            if not conectada:
                speak(_("Sin chats. Conecta la red desde el menú IRC."), interrupt=False)

    def _cargar_avisos(self):
        """Avisos del servidor (notices, mensajes de servicios): los últimos, más recientes arriba."""
        f = self.f
        f._modo_lista = "irc_avisos"
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("De"), width=160)
        f.lista_items.InsertColumn(1, _("Mensaje"), width=520)
        f.lista_items.InsertColumn(2, _("Fecha"), width=150)
        filas = db.query("""
            SELECT id, nick, text, date FROM irc_messages
            WHERE account_id = ? AND channel = '*'
            ORDER BY id DESC LIMIT 200
        """, (f.active_irc_account_id,))
        from app.ui.ui_helpers import formatear_tiempo_relativo
        for mid, nick, texto, fecha in filas:
            idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), nick or _("Servidor"))
            f.lista_items.SetItem(idx, 1, (texto or "").replace("\n", " "))
            f.lista_items.SetItem(idx, 2, formatear_tiempo_relativo(fecha or ""))
            f.lista_items.establecer_lectura(idx, f"{nick or _('Servidor')}: {texto or ''}")
            f.lista_items_db_ids.append(f"ircmsg:{mid}")
        f._restaurar_foco_lista()

    # ---------------- Activación y menú contextual de la lista ----------------
    def on_item_activated(self, index):
        """Intro en un chat: abre la Conversación IRC en vivo. Intro en el buffer 'Avisos del
        servidor': abre la CONSOLA del servidor (para escribir comandos sin entrar a un canal)."""
        f = self.f
        if f._modo_lista == "irc_avisos":
            self.abrir_consola_servidor()
            return
        if f._modo_lista != "irc_chats":
            return
        if index < 0 or index >= len(f.lista_items_db_ids):
            return
        self.abrir_conversacion(f.lista_items_db_ids[index])

    def abrir_consola_servidor(self, account_id=None):
        """Abre la consola del servidor IRC: una ventana con caja de texto para enviar comandos al
        servidor (registro con NickServ, /quote, etc.) y ver sus avisos, sin necesidad de estar en
        un canal."""
        f = self.f
        acc = account_id or f.active_irc_account_id
        if not acc:
            speak(_("Sitúate en una red de IRC."), interrupt=True)
            return
        from .irc_conversation import IRCConversationDialog
        dlg = IRCConversationDialog(f, acc, "*", es_servidor=True)
        dlg.ShowModal()
        dlg.Destroy()

    def abrir_conversacion(self, db_id):
        f = self.f
        fila = db.query_one("SELECT name, es_privado FROM irc_channels WHERE id = ?", (db_id,))
        if not fila:
            return
        from .irc_conversation import IRCConversationDialog
        dlg = IRCConversationDialog(f, f.active_irc_account_id, fila[0], es_privado=bool(fila[1]))
        dlg.ShowModal()
        dlg.Destroy()
        # Al volver, refrescar los contadores de la lista (ya sin no-leídos).
        self.cargar_listado()

    def on_context_menu(self, menu, idx):
        f = self.f
        if f._modo_lista != "irc_chats":
            return
        if idx < 0 or idx >= len(f.lista_items_db_ids):
            return
        db_id = f.lista_items_db_ids[idx]
        fila = db.query_one("SELECT name, joined, es_privado, unread_count FROM irc_channels WHERE id = ?", (db_id,))
        if not fila:
            return
        nombre, unido, es_privado, no_leidos = fila
        it = menu.Append(wx.ID_ANY, _("Abrir conversación"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.abrir_conversacion(d), it)
        it = menu.Append(wx.ID_ANY, _("Ver últimos mensajes"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.ver_ultimos_mensajes(d), it)
        if no_leidos:
            it = menu.Append(wx.ID_ANY, _("Marcar como leído"))
            f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.marcar_leido(d), it)
        if not es_privado:
            if unido:
                it = menu.Append(wx.ID_ANY, _("Salir del canal"))
                f.Bind(wx.EVT_MENU, lambda e, n=nombre: self.salir_canal(n), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Unirse al canal"))
                f.Bind(wx.EVT_MENU, lambda e, n=nombre: self.unirse_canal(n), it)
            it = menu.Append(wx.ID_ANY, _("Ver usuarios del canal..."))
            f.Bind(wx.EVT_MENU, lambda e, n=nombre: self._ver_usuarios(n), it)
            # Nivel de aviso sonoro del canal (todo / solo menciones / silencio).
            from app.services.irc import irc_service
            nivel = irc_service.nivel_aviso(f.active_irc_account_id, nombre)
            sub = wx.Menu()
            for clave, etiqueta in (("all", _("Avisar de todo")),
                                    ("mentions", _("Solo cuando me mencionen")),
                                    ("none", _("Silencio (sin sonidos)"))):
                it2 = sub.AppendRadioItem(wx.ID_ANY, etiqueta)
                if clave == nivel:
                    it2.Check(True)
                f.Bind(wx.EVT_MENU, lambda e, n=nombre, k=clave: self._fijar_nivel(n, k), it2)
            menu.AppendSubMenu(sub, _("Nivel de aviso"))
            # Mostrar u ocultar entradas/salidas (join/part/quit) en la conversación.
            mostrar = irc_service.mostrar_eventos(f.active_irc_account_id, nombre)
            it = menu.AppendCheckItem(wx.ID_ANY, _("Mostrar entradas y salidas"))
            it.Check(mostrar)
            f.Bind(wx.EVT_MENU, lambda e, n=nombre, v=not mostrar: self._fijar_eventos(n, v), it)
        it = menu.Append(wx.ID_ANY, _("Exportar conversación..."))
        f.Bind(wx.EVT_MENU, lambda e, n=nombre: self.exportar_chat(n), it)
        it = menu.Append(wx.ID_ANY, _("Eliminar este chat de la lista"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id, n=nombre, u=unido, p=es_privado:
               self.eliminar_chat(d, n, u, p), it)

    # ---------------- Acciones ----------------
    def _conectada(self, account_id) -> bool:
        try:
            from app.services.irc import irc_service
            return irc_service.esta_conectada(account_id)
        except Exception:
            return False

    def conectar_red(self, account_id=None):
        """Conecta la red indicada (o la activa) en segundo plano."""
        acc = account_id or self.f.active_irc_account_id
        if not acc:
            speak(_("Sitúate en una red IRC del árbol."), interrupt=True)
            return
        if self._conectada(acc):
            speak(_("Esta red ya está conectada."), interrupt=True)
            return
        speak(_("Conectando a la red IRC..."), interrupt=True)

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.conectar(acc)
            return True

        def _fin(res, err):
            from app.core.task_manager import TareaDuplicadaError
            if isinstance(err, TareaDuplicadaError):
                speak(_("Ya se está conectando esta red."), interrupt=False)
                return
            if err:
                notificar(_("No se pudo conectar: {0}").format(err))
            else:
                notificar(_("Conectado a la red IRC."))
                config.set("irc", f"autoconnect_{acc}", True)  # recordar: conectada al reiniciar
                self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True,
                                unique_key=f"irc:conectar:{acc}")

    def desconectar_red(self, account_id=None):
        acc = account_id or self.f.active_irc_account_id
        if not acc:
            speak(_("Sitúate en una red IRC del árbol."), interrupt=True)
            return
        if not self._conectada(acc):
            speak(_("Esta red no está conectada."), interrupt=True)
            return

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.desconectar(acc)
            return True

        def _fin(res, err):
            notificar(_("Desconectado de la red IRC.") if not err
                      else _("Error al desconectar: {0}").format(err))
            # Recordar que quieres esta red DESCONECTADA: ni el arranque ni el watchdog la reconectan.
            config.set("irc", f"autoconnect_{acc}", False)
            self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def conectar_redes_auto(self):
        """Conecta al arrancar las redes que dejaste conectadas. La preferencia base es la de la red
        ('conectar automáticamente'), pero si la desconectaste a mano y cerraste, arranca desconectada
        hasta que la conectes tú."""
        try:
            filas = db.query("""
                SELECT a.id, COALESCE(n.auto_connect, 0) FROM accounts a JOIN irc_networks n ON n.account_id = a.id
                WHERE a.service_id = 'irc' AND a.active = 1""")
        except Exception:
            filas = []
        for acc, autocol in filas:
            if not config.get("irc", f"autoconnect_{acc}", bool(autocol)):
                continue  # ni configurada para autoconectar, o la dejaste desconectada a mano
            def _bg(ctx, a=acc):
                from app.services.irc import irc_service
                irc_service.conectar(a)
                return True
            task_manager.start_task(_bg, pass_context=True, unique_key=f"irc:conectar:{acc}")

    def unirse_canal(self, nombre=None, account_id=None):
        acc = account_id or self.f.active_irc_account_id
        if not acc:
            speak(_("Sitúate en una red IRC del árbol."), interrupt=True)
            return
        if not self._conectada(acc):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return
        if not nombre:
            dlg = CustomTextEntryDialog(self.f, _("Canal al que unirte (ej: #nvda):"), _("Unirse a un canal"))
            nombre = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
            dlg.Destroy()
        if not nombre:
            return
        if not nombre.startswith(("#", "&")):
            nombre = "#" + nombre

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service._run(irc_service._clients[acc].join(nombre), timeout=20.0)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo entrar en {0}: {1}").format(nombre, err))
            else:
                notificar(_("Dentro de {0}.").format(nombre))
                self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def salir_canal(self, nombre):
        acc = self.f.active_irc_account_id
        if not acc or not self._conectada(acc):
            speak(_("La red no está conectada."), interrupt=True)
            return

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service._run(irc_service._clients[acc].part(nombre), timeout=20.0)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo salir de {0}: {1}").format(nombre, err))
            else:
                notificar(_("Has salido de {0}.").format(nombre))
                self._refrescar_si_activa(acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def eliminar_chat(self, db_id, nombre, unido, es_privado):
        """Quita un chat de la lista (si es un canal unido, primero sale de él)."""
        if confirmar(self.f, _("¿Eliminar el chat '{0}' y su historial local?").format(nombre),
                     _("Eliminar chat")) != wx.ID_YES:
            return
        acc = self.f.active_irc_account_id
        if unido and not es_privado and self._conectada(acc):
            self.salir_canal(nombre)
        db.execute("DELETE FROM irc_messages WHERE account_id = ? AND channel = ?",
                   (acc, (nombre or "").lower()))
        db.execute("DELETE FROM irc_channels WHERE id = ?", (db_id,))
        self.cargar_listado()
        notificar(_("Chat eliminado."))

    def marcar_leido(self, db_id):
        db.execute("UPDATE irc_channels SET unread_count = 0 WHERE id = ?", (db_id,))
        db.execute("""UPDATE irc_messages SET leido = 1
                      WHERE account_id = ? AND channel = (SELECT name FROM irc_channels WHERE id = ?)""",
                   (self.f.active_irc_account_id, db_id))
        self.cargar_listado()

    def ver_ultimos_mensajes(self, db_id):
        """Visor simple (texto largo) de los últimos mensajes del chat. En I2 será la
        Conversación IRC en vivo."""
        f = self.f
        fila = db.query_one("SELECT name FROM irc_channels WHERE id = ?", (db_id,))
        if not fila:
            return
        nombre = fila[0]
        filas = db.query("""
            SELECT nick, text, tipo, date FROM irc_messages
            WHERE account_id = ? AND channel = ? AND tipo IN ('msg','action','notice')
            ORDER BY id DESC LIMIT 200
        """, (f.active_irc_account_id, (nombre or "").lower()))
        if not filas:
            mostrar_info(f, _("Todavía no hay mensajes en este chat."), nombre)
            return
        lineas = []
        for nick, texto, tipo, fecha in reversed(filas):
            hora = (fecha or "")[11:16]
            if tipo == "action":
                lineas.append(f"{hora} * {nick} {texto}")
            else:
                lineas.append(f"{hora} <{nick}> {texto}")
        self.marcar_leido(db_id)
        f._mostrar_texto_largo(_("Últimos mensajes de {0}").format(nombre), "\n".join(lineas))

    def _fijar_nivel(self, canal, nivel):
        from app.services.irc import irc_service
        irc_service.fijar_nivel_aviso(self.f.active_irc_account_id, canal, nivel)
        etiquetas = {"all": _("avisar de todo"), "mentions": _("solo menciones"), "none": _("silencio")}
        notificar(_("Nivel de aviso de {0}: {1}.").format(canal, etiquetas.get(nivel, nivel)))

    def _fijar_eventos(self, canal, valor):
        from app.services.irc import irc_service
        irc_service.fijar_mostrar_eventos(self.f.active_irc_account_id, canal, valor)
        notificar(_("Entradas y salidas visibles en {0}.").format(canal) if valor
                  else _("Entradas y salidas ocultas en {0}.").format(canal))

    def _ver_usuarios(self, canal):
        from .irc_dialogs import IRCUsersDialog
        dlg = IRCUsersDialog(self.f, self.f.active_irc_account_id, canal)
        dlg.ShowModal()
        dlg.Destroy()

    def lista_canales_servidor(self, account_id=None):
        """Diálogo con la lista de canales del servidor (/LIST) con filtro."""
        acc = account_id or self.f.active_irc_account_id
        if not acc or not self._conectada(acc):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return
        from .irc_dialogs import IRCChannelListDialog
        dlg = IRCChannelListDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()
        self._refrescar_si_activa(acc)

    def whois_preguntando(self, account_id=None):
        """Pide un apodo y muestra sus datos (whois legible)."""
        acc = account_id or self.f.active_irc_account_id
        if not acc or not self._conectada(acc):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self.f, _("Apodo a consultar:"), _("Datos de un usuario"))
        nick = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if nick:
            from .irc_dialogs import mostrar_whois
            mostrar_whois(self.f, acc, nick)

    def gestionar_ignorados(self):
        """Lista de ignorados de la red activa, con opción de quitar."""
        from app.services.irc import irc_service
        acc = self.f.active_irc_account_id or None
        if not acc:
            fila = db.query_one("SELECT id FROM accounts WHERE service_id='irc' AND active=1 ORDER BY position, id")
            acc = fila[0] if fila else None
        if not acc:
            speak(_("Primero añade una red IRC."), interrupt=True)
            return
        lista = irc_service.ignorados(acc)
        if not lista:
            mostrar_info(self.f, _("No tienes usuarios ignorados en esta red."), _("Ignorados"))
            return
        dlg = wx.MultiChoiceDialog(self.f, _("Marca los que quieras DEJAR de ignorar:"),
                                   _("Ignorados ({0})").format(len(lista)), lista)
        if dlg.ShowModal() == wx.ID_OK:
            for i in dlg.GetSelections():
                irc_service.ignorar(acc, lista[i], False)
            if dlg.GetSelections():
                notificar(_("Hecho."))
        dlg.Destroy()

    def exportar_chat(self, canal):
        """Exporta el historial local del chat a un archivo de texto."""
        acc = self.f.active_irc_account_id
        if not acc:
            return
        seguro = "".join(c for c in canal if c.isalnum() or c in "#-_").lstrip("#") or "chat"
        dlg = wx.FileDialog(self.f, _("Guardar conversación"),
                            defaultFile=f"irc_{seguro}.txt",
                            wildcard=_("Texto|*.txt"),
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ruta = dlg.GetPath()
        dlg.Destroy()

        def _bg(ctx):
            from app.services.irc import irc_service
            return irc_service.exportar_chat(acc, canal, ruta)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Conversación exportada ({0} líneas).").format(r) if not e
            else _("No se pudo exportar: {0}").format(e)), pass_context=True)

    def gestionar_amigos(self):
        """Edita la lista de amigos (avisos de conexión) de la red activa."""
        from app.services.irc import irc_service
        acc = self.f.active_irc_account_id
        if not acc:
            fila = db.query_one("SELECT id FROM accounts WHERE service_id='irc' AND active=1 ORDER BY position, id")
            acc = fila[0] if fila else None
        if not acc:
            speak(_("Primero añade una red IRC."), interrupt=True)
            return
        actuales = irc_service.amigos(acc)
        dlg = CustomTextEntryDialog(
            self.f, _("Apodos a vigilar, separados por comas (la red te avisará cuando se "
                      "conecten o desconecten):"),
            _("Amigos (avisos de conexión)"), value=", ".join(actuales))
        if dlg.ShowModal() == wx.ID_OK:
            nicks = [n.strip() for n in dlg.GetValue().split(",") if n.strip()]
            irc_service.fijar_amigos(acc, nicks)
            notificar(_("Lista de amigos guardada ({0}).").format(len(nicks)))
        dlg.Destroy()

    def editar_red(self, account_id=None):
        acc = account_id or self.f.active_irc_account_id
        if not acc:
            speak(_("Sitúate en una red IRC del árbol."), interrupt=True)
            return
        from .irc_dialogs import IRCNetworkDialog
        dlg = IRCNetworkDialog(self.f, account_id=acc)
        if dlg.ShowModal() == wx.ID_OK:
            self.f._cargar_navegacion()
        dlg.Destroy()

    # ---------------- I7: estado propio y acciones de red ----------------
    def marcar_ausente(self, account_id):
        if not self._conectada(account_id):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self.f, _("Mensaje de ausencia (lo verán al escribirte):"),
                                    _("Marcarme como ausente"), value=_("Ausente"))
        msg = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        if msg is None:
            return

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.fijar_ausente(account_id, msg)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Estás marcado como ausente.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def volver_disponible(self, account_id):
        if not self._conectada(account_id):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.volver_disponible(account_id)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Vuelves a estar disponible.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def whowas_preguntando(self, account_id):
        if not self._conectada(account_id):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self.f, _("Apodo desconectado a consultar:"),
                                    _("Datos anteriores (whowas)"))
        nick = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not nick:
            return
        speak(_("Consultando..."), interrupt=True)

        def _bg(ctx):
            from app.services.irc import irc_service
            return irc_service.whowas_legible(account_id, nick)

        def _fin(texto, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                mostrar_info(self.f, texto, _("Datos anteriores de {0}").format(nick))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def hacerse_operador(self, account_id):
        if not self._conectada(account_id):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return
        d1 = CustomTextEntryDialog(self.f, _("Usuario de operador de la red:"),
                                   _("Hacerme operador"))
        usuario = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
        d1.Destroy()
        if not usuario:
            return
        d2 = CustomTextEntryDialog(self.f, _("Contraseña de operador:"),
                                   _("Hacerme operador"), password=True)
        password = d2.GetValue() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if not password:
            return

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.hacerse_operador(account_id, usuario, password)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Petición enviada.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def enviar_wallops(self, account_id):
        if not self._conectada(account_id):
            speak(_("Primero conecta la red (menú IRC)."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self.f, _("Mensaje wallops (lo verán quienes los reciban):"),
                                    _("Enviar wallops"))
        texto = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not texto:
            return

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.enviar_wallops(account_id, texto)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Wallops enviado.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    # ---------------- Menú contextual del árbol ----------------
    def menu_contextual_arbol(self, menu, data, item):
        """Nodo de una red IRC: conectar/desconectar/editar. Devuelve True si añadió algo."""
        f = self.f
        if data.get("type") != "account":
            return False
        acc = data.get("account_id")
        if self._conectada(acc):
            it = menu.Append(wx.ID_ANY, _("Desconectar de la red"))
            f.Bind(wx.EVT_MENU, lambda e, a=acc: self.desconectar_red(a), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Conectar a la red"))
            f.Bind(wx.EVT_MENU, lambda e, a=acc: self.conectar_red(a), it)
        it = menu.Append(wx.ID_ANY, _("Editar red..."))
        f.Bind(wx.EVT_MENU, lambda e, a=acc: self.editar_red(a), it)
        return True

    # ---------------- Eventos en vivo ----------------
    def _on_evento_irc(self, datos):
        """Llega del hilo IRCLoop por CADA evento. Coalescer: un solo refresco por ráfaga.
        Los eventos de ESTADO (caída, reconexión, apodo) además se anuncian con notificar()."""
        try:
            acc = (datos or {}).get("account_id")
            tipo = (datos or {}).get("tipo")
        except Exception:
            return
        if tipo in ("desconectado", "conectado", "nick_error", "nick_cambiado",
                    "amigo_online", "amigo_offline", "sts",
                    "invitacion", "ctcp_respuesta", "oper_ok", "kill", "mis_modos"):
            wx.CallAfter(self._avisar_estado, acc, tipo, datos)
        if self._refresco_pendiente:
            return
        self._refresco_pendiente = True
        wx.CallAfter(self._programar_refresco, acc)

    def _avisar_estado(self, acc, tipo, datos):
        """Avisos verbales de estado de la red (en el hilo de la interfaz). Solo transiciones:
        una caída avisa UNA vez (no cada reintento) y la reconexión se anuncia al lograrse."""
        try:
            fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (acc,))
            red = fila[0] if fila else _("IRC")
            if tipo == "desconectado" and not datos.get("esperado"):
                notificar(_("Se perdió la conexión con {0}; reintentando en segundo plano.").format(red))
            elif tipo == "conectado" and datos.get("reconexion"):
                notificar(_("Reconectado a {0}.").format(red))
            elif tipo == "nick_error":
                notificar(_("{0}: {1}").format(red, datos.get("motivo") or ""))
            elif tipo == "nick_cambiado":
                notificar(_("{0}: ahora eres {1}.").format(red, datos.get("ahora") or ""))
            elif tipo == "amigo_online":
                event_bus.emit("sound_trigger", "irc_highlight")
                notificar(_("{0}: {1} se ha conectado.").format(red, datos.get("nick") or ""))
            elif tipo == "amigo_offline":
                notificar(_("{0}: {1} se ha desconectado.").format(red, datos.get("nick") or ""))
            elif tipo == "sts":
                notificar(_("{0}: la red exige conexión segura; se usará TLS (puerto {1}) a partir "
                            "de la próxima conexión.").format(red, datos.get("puerto") or 6697))
            elif tipo == "invitacion":
                self._ofrecer_unirse_invitacion(acc, datos.get("canal") or "", datos.get("nick") or "")
            elif tipo == "ctcp_respuesta":
                notificar(datos.get("texto") or "")
            elif tipo == "oper_ok":
                notificar(_("{0}: ya eres operador de la red.").format(red))
            elif tipo == "kill":
                notificar(_("{0}: te han desconectado de la red ({1}: {2}).").format(
                    red, datos.get("por") or "", datos.get("razon") or ""))
            elif tipo == "mis_modos":
                notificar(_("{0}: tus modos han cambiado ({1}).").format(red, datos.get("modos") or ""))
        except Exception:
            pass

    def _ofrecer_unirse_invitacion(self, acc, canal, quien):
        """Alguien te invita a un canal: se ofrece unirse (con confirmación)."""
        if not canal:
            return
        if confirmar(self.f, _("{0} te invita al canal {1}. ¿Unirte ahora?").format(quien, canal),
                     _("Invitación a un canal")) != wx.ID_YES:
            return

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service._run(irc_service._clients[acc].join(canal), timeout=20.0)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Dentro de {0}.").format(canal) if not e else _("No se pudo entrar: {0}").format(e)),
            pass_context=True)

    def _programar_refresco(self, acc):
        wx.CallLater(1200, self._refrescar_por_evento, acc)

    def _refrescar_por_evento(self, acc):
        self._refresco_pendiente = False
        self._refrescar_si_activa(acc)

    def _refrescar_si_activa(self, acc):
        """Repinta la lista SOLO si estás mirando un buffer de esa red (actualización
        silenciosa: la lista se recarga sin mover el foco, regla de la casa)."""
        f = self.f
        try:
            if f.active_irc_account_id == acc and f._modo_lista in ("irc_chats", "irc_avisos"):
                self.cargar_listado()
        except Exception:
            pass
