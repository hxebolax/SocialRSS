# -*- coding: utf-8 -*-
"""Controlador de XMPP (Jabber) enganchado al MainFrame (patrón IRCController/MatrixController,
cuarta iteración del molde, tanda X1).

X1: nodo en el árbol (XMPP → cuenta → [Contactos, Grupos, Solicitudes, Avisos]), lista de contactos
con presencia y no-leídos, conectar/desconectar, gestionar solicitudes de suscripción y acciones de
roster (añadir/quitar/renombrar/bloquear). La conversación en vivo llega en X2.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
import wx
import logging

from app.core.database import db
from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager
from .accessible_controls import mostrar_info, confirmar, CustomTextEntryDialog

log = logging.getLogger(__name__)


def render_plantilla_xmpp(clave, valores):
    """Lectura de una fila de XMPP según la plantilla del usuario (editor de plantillas)."""
    from collections import defaultdict
    from app.core.plantillas import pulir_lectura, definicion
    d = definicion(clave)
    plantilla = config.get("accessibility", clave, "") or (d["default"] if d else "{content}")
    try:
        return pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
    except Exception:
        return pulir_lectura((d["default"] if d else "{content}").format_map(defaultdict(str, valores)))


class XMPPController:
    """Controlador de XMPP integrado en la ventana principal."""

    BUFFER_LABELS = {
        "conversaciones": _("Conversaciones"),
        "contactos": _("Contactos"),
        "grupos": _("Grupos"),
        "solicitudes": _("Solicitudes"),
        "archivados": _("Archivados"),
        "avisos": _("Avisos del servidor"),
    }

    def __init__(self, frame):
        self.f = frame
        self._refresco_pendiente = False
        self._chk_puentes_pend = False
        event_bus.subscribe("xmpp_evento", self._on_evento_xmpp)

    # ---------------- Árbol ----------------
    def construir_servicio(self, root):
        tree = self.f.tree_nav
        n_xmpp = tree.AppendItem(root, _("XMPP"),
                                 data={"type": "category_root", "service": "xmpp"})
        accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'xmpp' AND active = 1 "
                        "ORDER BY position, id")
        for r_id, r_name in accs:
            n_acc = tree.AppendItem(n_xmpp, r_name,
                                    data={"type": "account", "account_id": r_id, "service": "xmpp"})
            from app.core.config_manager import config
            ocultos = set(config.get("xmpp", "hidden_buffers", []) or [])
            # Puentes (WhatsApp, Discord…): si hay, la rama principal queda SOLO con lo del servidor
            # (contactos, grupos, solicitudes y avisos) y cada puente se lleva lo suyo (conversaciones,
            # contactos, grupos, archivados y sus propias solicitudes). Sin puentes, la principal
            # muestra todos los buffers. Todo (incluidos los puentes) es ocultable en la herramienta.
            puentes = []
            # Los puentes SOLO se muestran si la cuenta está conectada: si te desconectas, la pasarela
            # está fuera de línea, así que su rama desaparece del árbol hasta que vuelvas a conectar.
            if not config.get("xmpp", "sin_buffers_por_puente", False) and self._conectada(r_id):
                try:
                    from app.services.xmpp.xmpp_service import xmpp_service
                    puentes = [p for p in xmpp_service.puentes(r_id)
                               if ("puente:" + p["dominio"]) not in ocultos]
                except Exception as e:
                    log.debug(f"XMPP: no se pudieron detectar los puentes: {e}")
            # Recordar qué pasarelas se están mostrando, para detectar cambios (una nueva registrada
            # o una dada de baja) y reconstruir el árbol solo, sin reiniciar la app.
            if not hasattr(self, "_puentes_vistos"):
                self._puentes_vistos = {}
            self._puentes_vistos[r_id] = {p["dominio"] for p in puentes}
            # Los buffers de origen (todos menos avisos) se acotan a nativo cuando hay puentes.
            _FILTRABLES = ("conversaciones", "contactos", "grupos", "solicitudes", "archivados")
            if puentes:
                buffers_main = ("contactos", "grupos", "solicitudes", "avisos")
            else:
                buffers_main = ("conversaciones", "contactos", "grupos", "solicitudes", "archivados", "avisos")
            dom_main = "__nativo__" if puentes else None
            for clave in buffers_main:
                if clave in ocultos:
                    continue  # el usuario lo ocultó en Herramientas → Mostrar u ocultar buffers
                tree.AppendItem(n_acc, self.BUFFER_LABELS[clave], data={
                    "type": "xmpp_timeline", "xmpp_type": clave,
                    "account_id": r_id, "service": "xmpp",
                    "dominio": (dom_main if clave in _FILTRABLES else None),
                    "base_label": self.BUFFER_LABELS[clave]})
            # Cada puente, con su propio grupo de buffers (incluidos Archivados y sus Solicitudes).
            for p in puentes:
                n_p = tree.AppendItem(n_acc, p["nombre"], data={
                    "type": "category_root", "service": "xmpp",
                    "account_id": r_id, "dominio": p["dominio"]})
                for clave in ("conversaciones", "contactos", "grupos", "archivados", "solicitudes"):
                    if clave in ocultos:
                        continue  # ocultar un tipo de buffer vale también en las ramas de puente
                    tree.AppendItem(n_p, self.BUFFER_LABELS[clave], data={
                        "type": "xmpp_timeline", "xmpp_type": clave,
                        "account_id": r_id, "service": "xmpp", "dominio": p["dominio"],
                        "base_label": p["nombre"] + " - " + self.BUFFER_LABELS[clave]})
        return n_xmpp

    # ---------------- Selección ----------------
    def on_select(self, item, data, t):
        f = self.f
        f.active_xmpp_account_id = data.get("account_id")
        f._aplicar_visibilidad_detalles()
        if t == "xmpp_timeline":
            f.active_timeline_type = data.get("xmpp_type")
            f.active_xmpp_dominio = data.get("dominio")  # None = todos; un dominio = solo ese puente
            f.lbl_buffer_titulo.SetLabel("XMPP - " + f.tree_nav.GetItemText(item))
            self.cargar_listado()
        else:
            f.lista_items.DeleteAllItems()
            f.lista_items.limpiar_lecturas()

    # ---------------- Carga de la lista ----------------
    def cargar_listado(self):
        f = self.f
        if not f.active_xmpp_account_id:
            return
        tipo = f.active_timeline_type
        f._modo_lista = "xmpp_chats"
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        from app.services.xmpp.xmpp_service import xmpp_service
        acc = f.active_xmpp_account_id
        dominio = getattr(f, "active_xmpp_dominio", None)  # acotar a un puente si procede
        if tipo == "solicitudes":
            f.lista_items.InsertColumn(0, _("Pendiente"), width=320)
            for s in xmpp_service.solicitudes(acc, dominio):
                es_muc = (s.get("tipo") == "muc")
                etiqueta = (_("Invitación a la sala {0}").format(s["jid"]) if es_muc
                            else f"{s['nombre']} ({s['jid']})")
                idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), etiqueta)
                f.lista_items.establecer_lectura(
                    idx, _("Invitación a la sala {0}. Intro para entrar o rechazar.").format(s["nombre"]) if es_muc
                    else _("{0} quiere agregarte. Intro para aceptar o rechazar.").format(s["nombre"]))
                f.lista_items_db_ids.append({"tipo": "invitacion_muc" if es_muc else "solicitud",
                                             "jid": s["jid"], "nombre": s["nombre"]})
        elif tipo == "grupos":
            f.lista_items.InsertColumn(0, _("Grupo (sala)"), width=260)
            f.lista_items.InsertColumn(1, _("Último mensaje"), width=300)
            f.lista_items.InsertColumn(2, _("Sin leer"), width=80)
            for s in xmpp_service.salas_muc(acc, dominio):
                idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), s["nombre"])
                f.lista_items.SetItem(idx, 1, (s["ultimo"] or "").replace("\n", " "))
                f.lista_items.SetItem(idx, 2, str(s["no_leidos"] or 0))
                marcas = []
                if s.get("fijado"):
                    marcas.append(_("fijado"))
                if s.get("silenciado"):
                    marcas.append(_("silenciado"))
                f.lista_items.establecer_lectura(idx, render_plantilla_xmpp("xmpp_chat", {
                    "contacto": s["nombre"] + ((" (" + ", ".join(marcas) + ")") if marcas else ""),
                    "presencia": _("sala"), "content": s["ultimo"] or "",
                    "unread": _("{0} sin leer").format(s["no_leidos"]) if s["no_leidos"] else ""}))
                f.lista_items_db_ids.append({"tipo": "muc", "jid": s["jid"], "nombre": s["nombre"]})
            if not f.lista_items_db_ids:
                speak(_("Sin grupos aquí.") if dominio else _("Sin salas. Únete a una desde el menú XMPP."),
                      interrupt=False)
        elif tipo == "archivados":
            f.lista_items.InsertColumn(0, _("Chat archivado"), width=240)
            f.lista_items.InsertColumn(1, _("Tipo"), width=90)
            f.lista_items.InsertColumn(2, _("Último mensaje"), width=280)
            f.lista_items.InsertColumn(3, _("Sin leer"), width=80)
            for c in xmpp_service.archivados(acc, dominio):
                es_muc = (c["tipo"] == "muc")
                idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), c["nombre"])
                f.lista_items.SetItem(idx, 1, _("grupo") if es_muc else _("contacto"))
                f.lista_items.SetItem(idx, 2, (c["ultimo"] or "").replace("\n", " "))
                f.lista_items.SetItem(idx, 3, str(c["no_leidos"] or 0))
                f.lista_items.establecer_lectura(idx, render_plantilla_xmpp("xmpp_chat", {
                    "contacto": c["nombre"], "presencia": _("grupo") if es_muc else (c["presencia"] or ""),
                    "content": c["ultimo"] or "",
                    "unread": _("{0} sin leer").format(c["no_leidos"]) if c["no_leidos"] else ""}))
                f.lista_items_db_ids.append({"tipo": c["tipo"], "jid": c["jid"], "nombre": c["nombre"]})
            if not f.lista_items_db_ids:
                speak(_("No tienes chats archivados."), interrupt=False)
        elif tipo == "avisos":
            f.lista_items.InsertColumn(0, _("Aviso"), width=340)
            f.lista_items.InsertColumn(1, _("Fecha"), width=140)
        else:  # "conversaciones" (chats activos) o "contactos" (todo el roster)
            es_conv = (tipo == "conversaciones")
            f.lista_items.InsertColumn(0, _("Conversación") if es_conv else _("Contacto"), width=220)
            f.lista_items.InsertColumn(1, _("Presencia"), width=150)
            f.lista_items.InsertColumn(2, _("Último mensaje"), width=300)
            f.lista_items.InsertColumn(3, _("Sin leer"), width=80)
            lista = (xmpp_service.conversaciones(acc, dominio) if es_conv
                     else xmpp_service.contactos(acc, dominio))
            for c in lista:
                nombre = c["nombre"] + (_(" (bloqueado)") if c["bloqueado"] else "")
                idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), nombre)
                f.lista_items.SetItem(idx, 1, c["presencia"] or _("Sin conexión"))
                f.lista_items.SetItem(idx, 2, (c["ultimo"] or "").replace("\n", " "))
                f.lista_items.SetItem(idx, 3, str(c["no_leidos"] or 0))
                marcas = []
                if c.get("fijado"):
                    marcas.append(_("fijado"))
                if c.get("silenciado"):
                    marcas.append(_("silenciado"))
                lectura = render_plantilla_xmpp("xmpp_chat", {
                    "contacto": nombre + ((" (" + ", ".join(marcas) + ")") if marcas else ""),
                    "presencia": c["presencia"] or _("Sin conexión"),
                    "content": c["ultimo"] or "",
                    "unread": _("{0} sin leer").format(c["no_leidos"]) if c["no_leidos"] else "",
                    "estado": c["estado_texto"] or ""})
                f.lista_items.establecer_lectura(idx, lectura)
                f.lista_items_db_ids.append({"tipo": "contacto", "jid": c["jid"], "nombre": c["nombre"]})
        f._restaurar_foco_lista()
        if not f.lista_items_db_ids and tipo == "contactos" and not self._conectada(acc):
            speak(_("Sin contactos. Conecta la cuenta desde el menú XMPP."), interrupt=False)
        elif not f.lista_items_db_ids and tipo == "conversaciones":
            speak(_("Aún no tienes conversaciones. Abre un contacto para empezar a chatear, o usa "
                    "Buscar contacto."), interrupt=False)

    # ---------------- Activación e interacción ----------------
    def on_item_activated(self, index):
        f = self.f
        if f._modo_lista != "xmpp_chats":
            return
        if index < 0 or index >= len(f.lista_items_db_ids):
            return
        dato = f.lista_items_db_ids[index]
        if dato["tipo"] == "solicitud":
            self._responder_solicitud(dato["jid"], dato["nombre"])
        elif dato["tipo"] == "invitacion_muc":
            self._responder_invitacion_muc(dato["jid"], dato["nombre"])
        else:
            self.abrir_conversacion(dato["jid"], dato["nombre"], es_muc=(dato["tipo"] == "muc"))

    def abrir_conversacion(self, jid, nombre, es_muc=False):
        """Abre la ventana de Conversación en vivo con un contacto o sala."""
        acc = self.f.active_xmpp_account_id
        if not self._conectada(acc):
            speak(_("Conecta la cuenta de XMPP para conversar."), interrupt=True)
            return
        from .xmpp_conversation import XMPPConversationDialog
        dlg = XMPPConversationDialog(self.f, acc, jid, nombre, es_muc=es_muc)
        dlg.ShowModal()
        dlg.Destroy()
        if self.f.active_xmpp_account_id == acc and self.f._modo_lista == "xmpp_chats":
            with self.f._foco_silencioso():
                self.cargar_listado()

    def _responder_invitacion_muc(self, room_jid, nombre):
        r = confirmar(self.f, _("Te han invitado a la sala {0}.\n\n¿Entrar?").format(room_jid),
                      _("Invitación a una sala"), con_cancelar=True)
        acc = self.f.active_xmpp_account_id
        from app.services.xmpp.xmpp_service import xmpp_service
        if r == wx.ID_YES:
            # Si YA estabas unido a esa sala (autojoin=1, típico de los grupos de una pasarela que se
            # reenvían en cada sincronización), no hay que re-unirse (colgaría esperando una presencia
            # que no llega): basta con quitar la marca de solicitud.
            ya = db.query_one("SELECT COALESCE(autojoin,0) FROM xmpp_chats WHERE account_id = ? AND jid = ?",
                              (acc, room_jid))
            if ya and ya[0]:
                db.execute("UPDATE xmpp_chats SET solicitud = 0 WHERE account_id = ? AND jid = ?",
                           (acc, room_jid))
                notificar(_("Ya estabas en esa sala. Listo."))
                with self.f._foco_silencioso():
                    self.cargar_listado()
                return

            def _bg(ctx):
                xmpp_service.unirse_sala(acc, room_jid)
                db.execute("UPDATE xmpp_chats SET solicitud = 0 WHERE account_id = ? AND jid = ?",
                           (acc, room_jid))
                return True
            def _cb(res, e):
                notificar(_("Dentro de la sala.") if not e else _("No se pudo entrar: {0}").format(e))
                if not e:  # ya no es una solicitud: quitarla de la lista sin mover el foco
                    with self.f._foco_silencioso():
                        self.cargar_listado()
            task_manager.start_task(_bg, callback=_cb, pass_context=True)
        elif r == wx.ID_NO:
            db.execute("DELETE FROM xmpp_chats WHERE account_id = ? AND jid = ? AND tipo='muc' "
                       "AND COALESCE(solicitud,0)=1", (acc, room_jid))
            notificar(_("Invitación rechazada."))
            with self.f._foco_silencioso():
                self.cargar_listado()

    def ver_ultimos_mensajes(self, jid, nombre):
        """Visor simple de los últimos mensajes con un contacto (la Conversación en vivo llega en X2)."""
        acc = self.f.active_xmpp_account_id
        filas = db.query("""SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto, date, es_propio
                            FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ?
                            ORDER BY id DESC LIMIT 50""", (acc, jid))
        if not filas:
            mostrar_info(self.f, _("Aún no hay mensajes con {0}. Escríbele desde otro cliente o espera "
                                   "a la Conversación en vivo (próxima tanda).").format(nombre),
                         _("Conversación con {0}").format(nombre))
            return
        from app.services.xmpp.xmpp_common import db_id_chat, hora_local  # noqa: F401
        lineas = []
        for quien, texto, fecha, propio in reversed(filas):
            hora = hora_local(fecha)
            autor = _("Yo") if propio else quien
            lineas.append(f"[{hora}] {autor}: {texto}")
        frame = self.f
        if hasattr(frame, "_mostrar_texto_largo"):
            frame._mostrar_texto_largo(_("Conversación con {0}").format(nombre), "\n".join(lineas))
        else:
            mostrar_info(self.f, "\n".join(lineas), _("Conversación con {0}").format(nombre))

    def _responder_solicitud(self, jid, nombre):
        r = confirmar(self.f, _("{0} ({1}) quiere agregarte a sus contactos.\n\n¿Aceptar?").format(
            nombre, jid), _("Solicitud de contacto"), con_cancelar=True)
        acc = self.f.active_xmpp_account_id
        from app.services.xmpp.xmpp_service import xmpp_service
        if r == wx.ID_YES:
            def _bg(ctx):
                xmpp_service.aceptar_solicitud(acc, jid)
                return True
            task_manager.start_task(_bg, callback=lambda res, e: notificar(
                _("Contacto agregado.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)
        elif r == wx.ID_NO:
            def _bg(ctx):
                xmpp_service.rechazar_solicitud(acc, jid)
                return True
            task_manager.start_task(_bg, callback=lambda res, e: notificar(
                _("Solicitud rechazada.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)

    # ---------------- Menú contextual de la lista ----------------
    def on_context_menu(self, menu, idx):
        f = self.f
        if f._modo_lista != "xmpp_chats":
            return
        if idx < 0 or idx >= len(f.lista_items_db_ids):
            return
        dato = f.lista_items_db_ids[idx]
        acc = f.active_xmpp_account_id
        if dato["tipo"] == "solicitud":
            it = menu.Append(wx.ID_ANY, _("Aceptar y agregar"))
            f.Bind(wx.EVT_MENU, lambda e: self._accion_roster("aceptar", acc, dato["jid"], dato["nombre"]), it)
            it = menu.Append(wx.ID_ANY, _("Rechazar"))
            f.Bind(wx.EVT_MENU, lambda e: self._accion_roster("rechazar", acc, dato["jid"], dato["nombre"]), it)
            return
        jid, nombre = dato["jid"], dato["nombre"]
        es_muc = (dato["tipo"] == "muc")
        # --- Común a contactos y grupos ---
        it = menu.Append(wx.ID_ANY, _("Abrir conversación"))
        f.Bind(wx.EVT_MENU, lambda e: self.abrir_conversacion(jid, nombre, es_muc), it)
        it = menu.Append(wx.ID_ANY, _("Ver los últimos mensajes"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_ultimos_mensajes(jid, nombre), it)
        it = menu.Append(wx.ID_ANY, _("Marcar como no leído"))
        f.Bind(wx.EVT_MENU, lambda e: self._marcar_no_leido(acc, jid), it)
        it = menu.Append(wx.ID_ANY, _("Marcar como leído"))
        f.Bind(wx.EVT_MENU, lambda e: self._marcar_leido_lista(acc, jid), it)
        # Organización estilo WhatsApp: fijar arriba, silenciar, archivar.
        est = db.query_one("SELECT COALESCE(fijado,0), COALESCE(archivado,0), COALESCE(silenciado_hasta,'') "
                           "FROM xmpp_chats WHERE account_id=? AND jid=?", (acc, jid)) or (0, 0, "")
        fijado, archivado = bool(est[0]), bool(est[1])
        from app.services.xmpp.xmpp_service import xmpp_service as _xsil
        it = menu.Append(wx.ID_ANY, _("Desfijar de arriba") if fijado else _("Fijar arriba"))
        f.Bind(wx.EVT_MENU, lambda e, v=not fijado: self._fijar_chat(acc, jid, v), it)
        silenciado = _xsil.esta_silenciado(acc, jid)
        if silenciado:
            it = menu.Append(wx.ID_ANY, _("Quitar silencio"))
            f.Bind(wx.EVT_MENU, lambda e: self._silenciar_chat(acc, jid, 0), it)
        else:
            sub_sil = wx.Menu()
            for etiqueta, horas in ((_("1 hora"), 1), (_("8 horas"), 8), (_("1 día"), 24),
                                    (_("1 semana"), 168), (_("Para siempre"), -1)):
                itc = sub_sil.Append(wx.ID_ANY, etiqueta)
                f.Bind(wx.EVT_MENU, lambda e, h=horas: self._silenciar_chat(acc, jid, h), itc)
            menu.AppendSubMenu(sub_sil, _("Silenciar"))
        it = menu.Append(wx.ID_ANY, _("Desarchivar") if archivado else _("Archivar"))
        f.Bind(wx.EVT_MENU, lambda e, v=not archivado: self._archivar_chat(acc, jid, v), it)
        it = menu.Append(wx.ID_ANY, _("Exportar conversación..."))
        f.Bind(wx.EVT_MENU, lambda e: self.exportar_conversacion(acc, jid, nombre), it)
        it = menu.Append(wx.ID_ANY, _("Vaciar conversación (borrar mensajes)..."))
        f.Bind(wx.EVT_MENU, lambda e: self._vaciar_conversacion(acc, jid, nombre), it)
        it = menu.Append(wx.ID_ANY, _("Ver mensajes destacados..."))
        f.Bind(wx.EVT_MENU, lambda e: self._ver_destacados(acc, jid, nombre), it)
        # Cifrado OMEMO: activar/desactivar y ver huellas (solo si la pila está instalada). En los
        # chats de una PASARELA (WhatsApp, Discord…) NO se ofrece: la pasarela cifra en su lado con la
        # red legada, OMEMO no aplica ahí y marcarlo solo dejaría el envío colgado.
        from app.services.xmpp.xmpp_service import xmpp_service as _xs
        if _xs.omemo_disponible() and not self._es_jid_de_puente(acc, jid):
            cif = _xs.esta_cifrado(acc, jid)
            it = menu.Append(wx.ID_ANY, _("Desactivar cifrado OMEMO") if cif
                             else _("Activar cifrado OMEMO"))
            f.Bind(wx.EVT_MENU, lambda e, c=cif: self._toggle_cifrado(acc, jid, not c), it)
            it = menu.Append(wx.ID_ANY, _("Huellas de cifrado OMEMO..."))
            f.Bind(wx.EVT_MENU, lambda e, m=es_muc: self._ver_huellas(acc, jid, nombre, m), it)
        # Nivel de aviso: los grupos tienen además "solo menciones".
        sub = wx.Menu()
        nivel_actual = (db.query_one("SELECT COALESCE(notify_level,'all') FROM xmpp_chats WHERE account_id = ? AND jid = ?",
                                     (acc, jid)) or ["all"])[0]
        niveles = [("all", _("Avisar de todo"))]
        if es_muc:
            niveles.append(("mentions", _("Solo menciones")))
        niveles.append(("none", _("Silencio")))
        for clave, etiqueta in niveles:
            itr = sub.AppendRadioItem(wx.ID_ANY, etiqueta)
            if clave == nivel_actual:
                itr.Check(True)
            f.Bind(wx.EVT_MENU, lambda e, k=clave: self._fijar_nivel(acc, jid, k), itr)
        menu.AppendSubMenu(sub, _("Nivel de aviso"))
        menu.AppendSeparator()
        if es_muc:
            # --- Acciones propias de un GRUPO / sala ---
            it = menu.Append(wx.ID_ANY, _("Ver ocupantes..."))
            f.Bind(wx.EVT_MENU, lambda e: self._ver_ocupantes_lista(acc, jid, nombre), it)
            it = menu.Append(wx.ID_ANY, _("Ver o cambiar el tema..."))
            f.Bind(wx.EVT_MENU, lambda e: self._cambiar_tema_lista(acc, jid, nombre), it)
            it = menu.Append(wx.ID_ANY, _("Salir del grupo"))
            f.Bind(wx.EVT_MENU, lambda e: self._salir_grupo(acc, jid, nombre, olvidar=False), it)
            it = menu.Append(wx.ID_ANY, _("Salir y olvidar el grupo (no volver a entrar solo)"))
            f.Bind(wx.EVT_MENU, lambda e: self._salir_grupo(acc, jid, nombre, olvidar=True), it)
        else:
            # --- Acciones propias de un CONTACTO 1:1 ---
            it = menu.Append(wx.ID_ANY, _("Ver información del contacto"))
            f.Bind(wx.EVT_MENU, lambda e: self._info_contacto(acc, jid, nombre), it)
            it = menu.Append(wx.ID_ANY, _("Ver ficha (vCard)..."))
            f.Bind(wx.EVT_MENU, lambda e: self._ver_vcard(acc, jid, nombre), it)
            it = menu.Append(wx.ID_ANY, _("Información técnica..."))
            f.Bind(wx.EVT_MENU, lambda e: self._info_tecnica(acc, jid, nombre), it)
            it = menu.Append(wx.ID_ANY, _("Enviar un zumbido"))
            f.Bind(wx.EVT_MENU, lambda e: self._enviar_zumbido(acc, jid, nombre), it)
            it = menu.Append(wx.ID_ANY, _("Renombrar contacto..."))
            f.Bind(wx.EVT_MENU, lambda e: self._accion_roster("renombrar", acc, jid, nombre), it)
            fila = db.query_one("SELECT COALESCE(bloqueado,0) FROM xmpp_chats WHERE account_id = ? AND jid = ?",
                                (acc, jid))
            bloqueado = bool(fila and fila[0])
            it = menu.Append(wx.ID_ANY, _("Desbloquear") if bloqueado else _("Bloquear"))
            f.Bind(wx.EVT_MENU, lambda e, b=not bloqueado: self._accion_roster("bloquear", acc, jid, nombre, b), it)
            it = menu.Append(wx.ID_ANY, _("Quitar contacto..."))
            f.Bind(wx.EVT_MENU, lambda e: self._accion_roster("quitar", acc, jid, nombre), it)

    def _fijar_nivel(self, acc, jid, nivel):
        db.execute("UPDATE xmpp_chats SET notify_level = ? WHERE account_id = ? AND jid = ?",
                   (nivel, acc, jid))
        etiquetas = {"all": _("avisar de todo"), "mentions": _("solo menciones"), "none": _("silencio")}
        notificar(_("Nivel de aviso: {0}.").format(etiquetas.get(nivel, nivel)))

    def _salir_grupo(self, acc, jid, nombre, olvidar=False):
        if confirmar(self.f, (_("¿Salir y olvidar el grupo {0}? Dejarás de entrar en él "
                                "automáticamente.") if olvidar else _("¿Salir del grupo {0}?")).format(nombre),
                     _("Salir del grupo")) != wx.ID_YES:
            return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.salir_sala(acc, jid, olvidar=olvidar)
            return True

        def _fin(r, e):
            if e:
                notificar(_("No se pudo salir: {0}").format(e))
            else:
                notificar(_("Has salido del grupo."))
                self.cargar_listado()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _ver_ocupantes_lista(self, acc, jid, nombre):
        from .xmpp_dialogs import XMPPOcupantesDialog
        dlg = XMPPOcupantesDialog(self.f, acc, jid, nombre)
        dlg.ShowModal()
        dlg.Destroy()

    def _cambiar_tema_lista(self, acc, jid, nombre):
        from app.services.xmpp.xmpp_service import xmpp_service
        actual = ""
        try:
            fila = db.query_one("SELECT last_message FROM xmpp_chats WHERE account_id=? AND jid=?", (acc, jid))
        except Exception:
            fila = None
        d = CustomTextEntryDialog(self.f, _("Tema del grupo (déjalo vacío para quitarlo):"),
                                  _("Tema del grupo"))
        nuevo = d.GetValue() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if nuevo is None:
            return

        def _bg(ctx):
            xmpp_service.cambiar_tema(acc, jid, nuevo.strip())
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Tema cambiado.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)

    def _marcar_no_leido(self, acc, jid):
        from app.services.xmpp.xmpp_common import db_id_chat
        db.execute("UPDATE xmpp_chats SET unread_count = MAX(unread_count, 1) WHERE id = ?",
                   (db_id_chat(acc, jid),))
        # Marcar el último mensaje entrante como no leído para que cuente.
        db.execute("UPDATE xmpp_mensajes SET leido = 0 WHERE id = ("
                   "SELECT id FROM xmpp_mensajes WHERE account_id=? AND chat_jid=? AND es_propio=0 "
                   "ORDER BY id DESC LIMIT 1)", (acc, jid))
        notificar(_("Marcado como no leído."))
        self.cargar_listado()

    def _marcar_leido_lista(self, acc, jid):
        from app.services.xmpp.xmpp_service import xmpp_service
        try:
            xmpp_service.marcar_leido(acc, jid)
        except Exception:
            from app.services.xmpp.xmpp_common import db_id_chat
            db.execute("UPDATE xmpp_chats SET unread_count = 0 WHERE id = ?", (db_id_chat(acc, jid),))
        notificar(_("Marcado como leído."))
        self.cargar_listado()

    def _vaciar_conversacion(self, acc, jid, nombre):
        if confirmar(self.f, _("¿Vaciar la conversación con {0}? Se borrarán todos sus mensajes "
                               "(el contacto/grupo NO se quita). No se puede deshacer.").format(nombre),
                     _("Vaciar conversación")) != wx.ID_YES:
            return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.vaciar_conversacion(acc, jid)

        def _fin(n, e):
            if e:
                notificar(_("No se pudo vaciar: {0}").format(e))
            else:
                notificar(_("Conversación vaciada ({0} mensajes borrados).").format(n or 0))
                self.cargar_listado()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _ver_destacados(self, acc, jid, nombre):
        """Muestra los mensajes destacados de un chat."""
        from app.services.xmpp.xmpp_common import hora_local
        filas = db.query(
            "SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto, date FROM xmpp_mensajes "
            "WHERE account_id = ? AND chat_jid = ? AND COALESCE(destacado,0) = 1 ORDER BY id ASC",
            (acc, jid))
        if not filas:
            mostrar_info(self.f, _("No hay mensajes destacados en esta conversación."),
                         _("Destacados de {0}").format(nombre))
            return
        lineas = [f"[{hora_local(d)}] {q}: {t}" for q, t, d in filas]
        mostrar_info(self.f, "\n".join(lineas), _("Destacados de {0}").format(nombre))

    def _fijar_chat(self, acc, jid, fijar):
        from app.services.xmpp.xmpp_service import xmpp_service
        xmpp_service.fijar_chat(acc, jid, fijar)
        notificar(_("Fijado arriba.") if fijar else _("Ya no está fijado."))
        self.cargar_listado()

    def _archivar_chat(self, acc, jid, archivar):
        from app.services.xmpp.xmpp_service import xmpp_service
        xmpp_service.archivar_chat(acc, jid, archivar)
        notificar(_("Chat archivado.") if archivar else _("Chat desarchivado."))
        self.cargar_listado()

    def _silenciar_chat(self, acc, jid, horas):
        from app.services.xmpp.xmpp_service import xmpp_service
        xmpp_service.silenciar_chat(acc, jid, horas)
        if horas == 0:
            notificar(_("Silencio quitado."))
        elif horas < 0:
            notificar(_("Silenciado para siempre."))
        else:
            notificar(_("Silenciado."))
        self.cargar_listado()

    def exportar_conversacion(self, acc, jid, nombre):
        """Exporta el historial local de un chat a un .txt (mismo patrón que Matrix/IRC)."""
        seguro = "".join(c for c in (nombre or "chat") if c.isalnum() or c in "-_ ").strip() or "chat"
        dlg = wx.FileDialog(self.f, _("Guardar conversación"), defaultFile=f"xmpp_{seguro}.txt",
                            wildcard=_("Texto|*.txt"), style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ruta = dlg.GetPath()
        dlg.Destroy()

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.exportar_conversacion(acc, jid, ruta)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Conversación exportada ({0} mensajes).").format(r) if not e
            else _("No se pudo exportar: {0}").format(e)), pass_context=True)

    def _toggle_cifrado(self, acc, jid, activar):
        from app.services.xmpp.xmpp_service import xmpp_service
        try:
            nuevo = xmpp_service.fijar_cifrado(acc, jid, activar)
        except Exception as e:
            notificar(_("No se pudo cambiar el cifrado: {0}").format(e))
            return
        notificar(_("Cifrado OMEMO activado.") if nuevo else _("Cifrado OMEMO desactivado."))

    def _ver_huellas(self, acc, jid, nombre, es_muc):
        from .xmpp_dialogs import XMPPHuellasDialog
        dlg = XMPPHuellasDialog(self.f, acc, jid, nombre, es_muc=es_muc)
        dlg.ShowModal()
        dlg.Destroy()

    def _info_contacto(self, acc, jid, nombre):
        fila = db.query_one("""SELECT presencia, estado_texto, suscripcion, COALESCE(pep_estado,'')
                               FROM xmpp_chats WHERE account_id = ? AND jid = ?""", (acc, jid))
        pres, estado, susc, pep = (fila or ("", "", "", ""))
        subs = {"both": _("mutua"), "to": _("tú lo ves"), "from": _("él te ve"),
                "none": _("ninguna")}.get(susc or "none", susc or _("ninguna"))
        lineas = [_("Nombre: {0}").format(nombre), _("JID: {0}").format(jid),
                  _("Presencia: {0}").format(pres or _("Sin conexión")),
                  _("Estado: {0}").format(estado or _("(sin mensaje)")),
                  _("Suscripción: {0}").format(subs)]
        if pep:
            lineas.append(_("Ánimo/actividad: {0}").format(pep))
        mostrar_info(self.f, "\n".join(lineas), _("Información de {0}").format(nombre))

    def _ver_vcard(self, acc, jid, nombre):
        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.ver_vcard(acc, jid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo obtener la ficha: {0}").format(err))
                return
            if not res:
                mostrar_info(self.f, _("Este contacto no tiene ficha (vCard) publicada."),
                             _("Ficha de {0}").format(nombre))
                return
            lineas = [f"{etq}: {val}" for etq, val in res]
            mostrar_info(self.f, "\n".join(lineas), _("Ficha de {0}").format(nombre))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _info_tecnica(self, acc, jid, nombre):
        speak(_("Consultando..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.info_tecnica(acc, jid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo consultar: {0}").format(err))
                return
            if not res:
                mostrar_info(self.f, _("El contacto no respondió a las consultas (puede estar sin "
                                       "conexión o no permitirlas)."),
                             _("Información técnica de {0}").format(nombre))
                return
            lineas = [f"{etq}: {val}" for etq, val in res]
            mostrar_info(self.f, "\n".join(lineas), _("Información técnica de {0}").format(nombre))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _enviar_zumbido(self, acc, jid, nombre):
        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.enviar_zumbido(acc, jid)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Zumbido enviado a {0}.").format(nombre) if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def _accion_roster(self, accion, acc, jid, nombre, arg=None):
        from app.services.xmpp.xmpp_service import xmpp_service
        if accion == "quitar":
            if confirmar(self.f, _("¿Quitar a {0} de tus contactos?").format(nombre),
                         _("Confirmar")) != wx.ID_YES:
                return
        if accion == "renombrar":
            d = CustomTextEntryDialog(self.f, _("Nuevo nombre para {0}:").format(nombre),
                                      _("Renombrar contacto"), value=nombre)
            arg = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else None
            d.Destroy()
            if not arg:
                return

        def _bg(ctx):
            if accion == "aceptar":
                xmpp_service.aceptar_solicitud(acc, jid)
            elif accion == "rechazar":
                xmpp_service.rechazar_solicitud(acc, jid)
            elif accion == "quitar":
                xmpp_service.quitar_contacto(acc, jid)
            elif accion == "renombrar":
                xmpp_service.renombrar_contacto(acc, jid, arg)
            elif accion == "bloquear":
                xmpp_service.bloquear_contacto(acc, jid, bool(arg))
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Hecho."))
                if self.f.active_xmpp_account_id == acc and self.f._modo_lista == "xmpp_chats":
                    with self.f._foco_silencioso():
                        self.cargar_listado()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- Menú contextual del árbol (nodo de cuenta) ----------------
    def menu_contextual_arbol(self, menu, data, item):
        if data.get("service") != "xmpp":
            return False
        # Nodo de una PASARELA (rama de WhatsApp/Discord…): permitir darla de baja por completo.
        if data.get("type") == "category_root" and data.get("dominio"):
            acc = data.get("account_id")
            dom = data.get("dominio")
            it = menu.Append(wx.ID_ANY, _("Dar de baja esta pasarela (quitar registro)…"))
            self.f.Bind(wx.EVT_MENU, lambda e, a=acc, d=dom: self._dar_baja_pasarela(a, d), it)
            return True
        if data.get("type") != "account":
            return False
        acc = data.get("account_id")
        if self._conectada(acc):
            it = menu.Append(wx.ID_ANY, _("Desconectar"))
            self.f.Bind(wx.EVT_MENU, lambda e: self.desconectar_cuenta(acc), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Conectar"))
            self.f.Bind(wx.EVT_MENU, lambda e: self.conectar_cuenta(acc), it)
        return True

    def _dar_baja_pasarela(self, account_id, dominio):
        """Da de baja la pasarela: cancela el registro en el servidor y borra sus datos locales, para
        dejar todo limpio y poder registrar otra (p. ej. otro WhatsApp)."""
        if not self._conectada(account_id):
            speak(_("Conéctate primero para poder dar de baja la pasarela."), interrupt=True)
            return
        r = confirmar(self.f,
                      _("Vas a DAR DE BAJA esta pasarela ({0}).\n\nSe quitará tu registro en el servidor "
                        "y se borrarán en local sus contactos, grupos y conversaciones. Podrás volver a "
                        "registrar otra pasarela después.\n\n¿Continuar?").format(dominio),
                      _("Dar de baja la pasarela"), con_cancelar=True)
        if r != wx.ID_YES:
            return
        speak(_("Dando de baja la pasarela..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.dar_baja_pasarela(account_id, dominio)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo dar de baja la pasarela: {0}").format(err))
                return
            res = res or {}
            if res.get("confirmado", True):
                notificar(_("Pasarela dada de baja. Todo limpio."))
            else:
                # La orden se envió y se limpió en local; el servidor puede tardar en confirmar.
                notificar(_("Baja enviada y datos locales limpiados. El servidor puede tardar un poco "
                            "en terminar de desvincularla; ya puedes registrar otra."))
            self._refrescar_arbol_puentes()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- Conexión ----------------
    def _conectada(self, account_id) -> bool:
        # RENDIMIENTO: si el servicio XMPP AÚN no se ha importado (arranque), la cuenta no puede
        # estar conectada, así que NO forzamos aquí su import. Importar xmpp_service arrastra
        # slixmpp + omemo + peewee (pesados) y hacerlo en el hilo de la interfaz DURANTE la
        # construcción del árbol costaba ~394 ms de bloqueo (medido). Cuando XMPP conecta de verdad,
        # su módulo ya está cargado (lo hizo el hilo de conexión) y esta comprobación es instantánea.
        import sys
        if "app.services.xmpp.xmpp_service" not in sys.modules:
            return False
        try:
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.esta_conectada(account_id)
        except Exception:
            return False

    def conectar_cuenta(self, account_id=None):
        acc = account_id or self.f.active_xmpp_account_id
        if not acc:
            speak(_("Sitúate en una cuenta de XMPP del árbol."), interrupt=True)
            return
        if self._conectada(acc):
            speak(_("Esta cuenta ya está conectada."), interrupt=True)
            return
        speak(_("Conectando con XMPP..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.conectar(acc)
            return True

        def _fin(res, err):
            from app.core.task_manager import TareaDuplicadaError
            if isinstance(err, TareaDuplicadaError):
                speak(_("Ya se está conectando esta cuenta."), interrupt=False)
                return
            if err:
                notificar(_("No se pudo conectar: {0}").format(err))
            else:
                notificar(_("Conectado a XMPP."))
                # Recordar que quieres esta cuenta CONECTADA: al reiniciar volverá a conectarse sola.
                config.set("xmpp", f"autoconnect_{acc}", True)
                self._refrescar_arbol_puentes()  # ya conectados: aparece la rama de cada pasarela
                self._sincronizar_nombres_salas(acc)  # rellena nombres de grupos (pasarelas)
        task_manager.start_task(_bg, callback=_fin, pass_context=True, unique_key=f"xmpp:conectar:{acc}")

    def _sincronizar_nombres_salas(self, account_id):
        """En segundo plano: pide a la pasarela/servidor los nombres reales de los grupos y refresca
        la lista si estás en un buffer de grupos o solicitudes (sin mover el foco)."""
        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.sincronizar_nombres_salas(account_id)

        def _fin(n, err):
            if not err and n and getattr(self.f, "active_timeline_type", "") in ("grupos", "solicitudes"):
                with self.f._foco_silencioso():
                    self.cargar_listado()
        task_manager.start_task(_bg, callback=_fin, pass_context=True,
                                unique_key=f"xmpp:nombres_salas:{account_id}")

    def desconectar_cuenta(self, account_id=None):
        acc = account_id or self.f.active_xmpp_account_id
        if not acc or not self._conectada(acc):
            speak(_("Esta cuenta no está conectada."), interrupt=True)
            return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.desconectar(acc)
            return True

        def _fin_desc(r, e):
            notificar(_("Desconectado de XMPP.") if not e else _("Error al desconectar: {0}").format(e))
            # Recordar que quieres esta cuenta DESCONECTADA: al reiniciar NO se conectará sola.
            config.set("xmpp", f"autoconnect_{acc}", False)
            self._refrescar_arbol_puentes()  # desconectados: desaparece la rama de las pasarelas
        task_manager.start_task(_bg, callback=_fin_desc, pass_context=True)

    def _es_jid_de_puente(self, account_id, jid):
        """True si el JID pertenece a una pasarela (su dominio es un gateway detectado). Se usa para
        no ofrecer OMEMO ahí (la pasarela ya cifra en su lado)."""
        try:
            from app.services.xmpp.xmpp_service import xmpp_service
            dom = jid.split("@", 1)[1] if "@" in jid else jid
            return dom in {p["dominio"] for p in xmpp_service.puentes(account_id)}
        except Exception:
            return False

    def _refrescar_arbol_puentes(self):
        """Reconstruye el árbol de navegación (conservando foco/expandidos) para reflejar si las
        ramas de las pasarelas deben verse o no, según el estado de conexión."""
        try:
            self.f._cargar_navegacion()
        except Exception as e:
            log.debug(f"XMPP: no se pudo refrescar el árbol de puentes: {e}")

    def conectar_cuentas_auto(self):
        """Conecta al arrancar las cuentas de XMPP con autoconexión (en segundo plano)."""
        try:
            filas = db.query("SELECT id FROM accounts WHERE service_id = 'xmpp' AND active = 1")
        except Exception:
            filas = []
        for (acc,) in filas:
            if not config.get("xmpp", f"autoconnect_{acc}", True):
                continue

            def _bg(ctx, a=acc):
                from app.services.xmpp.xmpp_service import xmpp_service
                xmpp_service.conectar(a)
                return True
            task_manager.start_task(_bg, pass_context=True, unique_key=f"xmpp:conectar:{acc}")

    # ---------------- Eventos del servicio (refresco coalescido) ----------------
    def _on_evento_xmpp(self, datos):
        # RENDIMIENTO: el event_bus entrega desde el hilo del SERVICIO (slixmpp) y la presencia de una
        # pasarela (WhatsApp) puede ser un TORRENTE (cientos de eventos de golpe al conectar). En vez
        # de un wx.CallAfter POR evento (que inundaba la cola del hilo de la interfaz), se COALESCE
        # AQUÍ, en el hilo del emisor: se ponen los flags ANTES de saltar y una ráfaga entera produce
        # UN solo salto de cada tipo (patrón IRC/Matrix). Poner un bool es atómico con el GIL, y leer
        # atributos del frame desde otro hilo es seguro (a lo sumo un valor un instante viejo).
        acc = datos.get("account_id")
        if acc is None:
            return
        # 1) Comprobación de pasarelas (registro/baja): una sola por ráfaga, con retardo para que la
        #    pasarela termine de poblar sus contactos/grupos antes de mirar.
        if not self._chk_puentes_pend:
            self._chk_puentes_pend = True
            try:
                wx.CallAfter(self._armar_comprobar_puentes, acc)
            except Exception:
                self._chk_puentes_pend = False
        # 2) Refresco de la lista: solo si es la cuenta y el buffer que estás viendo, y una sola vez
        #    por ráfaga.
        try:
            activa = (acc == self.f.active_xmpp_account_id and self.f._modo_lista == "xmpp_chats")
        except Exception:
            activa = False
        if activa and not self._refresco_pendiente:
            self._refresco_pendiente = True
            try:
                wx.CallAfter(self._armar_refresco_coalescido)
            except Exception:
                self._refresco_pendiente = False

    def _armar_comprobar_puentes(self, acc):
        """En el hilo de la UI: programa la comprobación de pasarelas con retardo (debounce)."""
        wx.CallLater(2000, self._comprobar_cambio_puentes, acc)

    def _armar_refresco_coalescido(self):
        """En el hilo de la UI: programa el refresco de la lista con retardo (debounce)."""
        wx.CallLater(1200, self._refrescar_coalescido)

    def _comprobar_cambio_puentes(self, account_id):
        """Si las pasarelas visibles ya no coinciden con las del árbol, lo reconstruye (sin reiniciar).
        El cálculo (consulta a la BD) va en SEGUNDO PLANO; solo se reconstruye el árbol —en el hilo de
        la UI— si el conjunto de pasarelas cambió. Así no hay ninguna consulta en el hilo que pinta."""
        self._chk_puentes_pend = False
        if config.get("xmpp", "sin_buffers_por_puente", False) or not self._conectada(account_id):
            return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return {p["dominio"] for p in xmpp_service.puentes(account_id)}

        def _fin(actuales, err):
            if err or actuales is None:
                return
            ocultos = set(config.get("xmpp", "hidden_buffers", []) or [])
            actuales = {d for d in actuales if ("puente:" + d) not in ocultos}
            previos = getattr(self, "_puentes_vistos", {}).get(account_id, set())
            if actuales != previos:
                self._refrescar_arbol_puentes()  # reconstruye y actualiza _puentes_vistos
        task_manager.start_task(_bg, callback=_fin, pass_context=True,
                                unique_key=f"xmpp:chk_puentes:{account_id}")

    def _refrescar_coalescido(self):
        self._refresco_pendiente = False
        try:
            if self.f._modo_lista == "xmpp_chats":
                with self.f._foco_silencioso():
                    self.cargar_listado()
        except Exception:
            pass
