# -*- coding: utf-8 -*-
"""Ventana de Conversación IRC (hermana de la conversación de Telegram).

Mensajes en VIVO con inserción silenciosa (sin reconstruir la lista ni mover el foco, con
marca "(nuevo)"), campo de entrada con Intro para enviar, historial de envíos con las
flechas, autocompletado de apodos con Tab, comandos / básicos, fragmentos y traductor.
"""
import wx
import logging

from app.core.database import db
from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, mostrar_info, CustomTextEntryDialog

log = logging.getLogger(__name__)

_AYUDA_COMANDOS = (
    "/me <texto> — acción (\"* apodo hace algo\")\n"
    "/msg <destino> <texto> — mensaje a un canal o apodo\n"
    "/query <apodo> — abrir un chat privado\n"
    "/notice <destino> <texto> — enviar un aviso (notice)\n"
    "/join <#canal> — unirte a un canal\n"
    "/part [#canal] — salir del canal\n"
    "/cycle [#canal] — salir y volver a entrar\n"
    "/nick <apodo> — cambiar tu apodo\n"
    "/topic <texto> — cambiar el tema del canal\n"
    "/away [mensaje] — marcarte como ausente\n"
    "/back — volver a estar disponible\n"
    "/invite <apodo> [#canal] — invitar a alguien al canal\n"
    "/knock <#canal> [motivo] — pedir entrar en un canal de solo invitación\n"
    "/whowas <apodo> — datos de un apodo ya desconectado\n"
    "/ping <apodo> — medir la latencia con alguien (CTCP PING)\n"
    "/ctcp <apodo> <consulta> — CTCP a alguien (TIME, VERSION, CLIENTINFO...)\n"
    "/mode [#canal] <modos> [arg] — cambiar modos del canal (+m, +i, +k clave, +l 50...)\n"
    "/kick <apodo> [motivo] — expulsar del canal\n"
    "/ban <máscara> — banear por máscara (ej: *!*@host)\n"
    "/unban <máscara> — quitar un baneo\n"
    "/oper <usuario> <contraseña> — hacerte operador de la red\n"
    "/wallops <texto> — mensaje a quienes reciben wallops\n"
    "/umode <modos> — cambiar tus modos de usuario (+i invisible, +w wallops...)\n"
    "/metadata [clave valor|ver] — publicar/ver tu metadata (si el servidor lo admite)\n"
    "/clear — vaciar la vista (el historial local no se borra)\n"
    "/events [on|off] — mostrar u ocultar las entradas y salidas (fulano entró/salió) de este canal\n"
    "/quote <línea> — enviar una orden cruda al servidor\n"
    "/help — esta ayuda"
)


class IRCConversationDialog(wx.Dialog):
    """Conversación de un canal o privado de IRC."""

    def __init__(self, parent, account_id: int, canal: str, es_privado: bool = False,
                 es_servidor: bool = False):
        # Modo SERVIDOR (consola): sin canal, para escribir comandos al servidor (registro con
        # NickServ, /quote...) y ver sus avisos, SIN tener que entrar antes a un canal. El buffer
        # es el de avisos del servidor (channel '*').
        self.es_servidor = bool(es_servidor)
        if self.es_servidor:
            canal = "*"
            es_privado = True  # oculta los botones de canal (usuarios/tema/gestión)
            titulo = _("Consola del servidor IRC (escribe comandos)")
        else:
            titulo = _("Conversación: {0}").format(canal)
        super().__init__(parent, title=titulo, size=(720, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Ventana de conversación de IRC"))
        self.account_id = account_id
        self.canal = canal                    # nombre del canal o apodo del privado
        self._canal_bd = (canal or "").lower()
        self.es_privado = bool(es_privado)
        self.msg_rows = []                    # metadatos por fila (id, nick, texto, leido)
        self._hist_envios = []                # historial de textos enviados (flecha arriba)
        self._hist_pos = None
        self._tab_estado = None               # (prefijo, candidatos, índice) del completado
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, self._on_cerrar)
        self.CenterOnParent()
        self._cargar_mensajes()
        # Mensajes en vivo de ESTE chat: llegan del hilo IRCLoop por el bus; se insertan al
        # final SIN reconstruir ni mover el foco (regla dura de actualización silenciosa).
        event_bus.subscribe("irc_evento", self._on_evento_irc)
        self.txt_mensaje.SetFocus()

    # ---------------- UI ----------------
    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("Mensajes (los más recientes al final):")), 0, wx.ALL, 5)
        self.lista_msgs = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de mensajes de la conversación"))
        self.lista_msgs.InsertColumn(0, _("Hora"), width=70)
        self.lista_msgs.InsertColumn(1, _("Autor"), width=140)
        self.lista_msgs.InsertColumn(2, _("Mensaje"), width=470)
        self.lista_msgs.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_msg_foco)
        self.lista_msgs.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
        sizer.Add(self.lista_msgs, 1, wx.EXPAND | wx.ALL, 5)

        _lbl_esc = (_("Escribir comando para el servidor (Intro lo envía; por ejemplo /msg NickServ "
                      "REGISTER contraseña correo, o /quote para una orden cruda; /help para más):")
                    if self.es_servidor
                    else _("Escribir mensaje (Intro envía; Tab completa el apodo empezado; /help para comandos):"))
        self.lbl_escribir = wx.StaticText(self, label=_lbl_esc)
        sizer.Add(self.lbl_escribir, 0, wx.ALL, 5)
        self.txt_mensaje = AccessibleTextCtrl(
            self, style=wx.TE_PROCESS_ENTER | wx.TE_PROCESS_TAB,
            name=_("Campo de redacción de mensaje"),
            ayuda_texto=_("Escribe y pulsa Intro para enviar. Flecha arriba recupera envíos "
                          "anteriores. Tab completa el apodo EMPEZADO (si no hay nada que "
                          "completar, Tab navega por la ventana; Mayús+Tab navega siempre). "
                          "Comandos con /, p. ej. /me."))
        self.txt_mensaje.Bind(wx.EVT_TEXT_ENTER, self._on_enviar)
        self.txt_mensaje.Bind(wx.EVT_KEY_DOWN, self._on_key_entrada)
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt_mensaje)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt_mensaje)
        sizer.Add(self.txt_mensaje, 0, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_enviar = wx.Button(self, label=_("&Enviar"), name=_("Enviar mensaje"))
        self.btn_enviar.Bind(wx.EVT_BUTTON, self._on_enviar)
        fila.Add(self.btn_enviar, 0, wx.ALL, 5)
        if not self.es_privado:
            btn_usuarios = wx.Button(self, label=_("&Usuarios..."), name=_("Ver los usuarios del canal"))
            btn_usuarios.Bind(wx.EVT_BUTTON, self._on_usuarios)
            fila.Add(btn_usuarios, 0, wx.ALL, 5)
            btn_tema = wx.Button(self, label=_("&Tema..."), name=_("Ver o cambiar el tema del canal"))
            btn_tema.Bind(wx.EVT_BUTTON, self._on_tema)
            fila.Add(btn_tema, 0, wx.ALL, 5)
            btn_canal = wx.Button(self, label=_("&Canal..."),
                                  name=_("Modos y baneos del canal (para operadores)"))
            btn_canal.Bind(wx.EVT_BUTTON, self._on_gestion_canal)
            fila.Add(btn_canal, 0, wx.ALL, 5)
        btn_final = wx.Button(self, label=_("Ir al &final"), name=_("Ir al último mensaje"))
        btn_final.Bind(wx.EVT_BUTTON, lambda e: self._ir_al_final(forzar=True))
        fila.Add(btn_final, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        sizer.Add(fila, 0, wx.EXPAND | wx.ALL, 6)

        self.SetSizer(sizer)

    # ---------------- Carga y pintado ----------------
    def _fila_valores(self, mid, nick, texto, tipo, fecha, propio):
        hora = (fecha or "")[11:16]
        if tipo == "action":
            autor, cuerpo = f"* {nick}", texto or ""
        elif tipo == "notice":
            autor, cuerpo = _("{0} (aviso)").format(nick or _("Servidor")), texto or ""
        elif tipo == "join":
            autor, cuerpo = nick, _("ha entrado en el canal")
        elif tipo in ("part", "quit"):
            autor, cuerpo = nick, _("ha salido") + (f" ({texto})" if texto else "")
        elif tipo == "kick":
            autor, cuerpo = nick, _("ha expulsado a {0}").format(texto or "")
        elif tipo == "topic":
            autor, cuerpo = nick, _("ha cambiado el tema a: {0}").format(texto or "")
        elif tipo == "mode":
            autor, cuerpo = nick or _("Servidor"), _("ha cambiado los modos: {0}").format(texto or "")
        else:
            autor, cuerpo = nick, texto or ""
        return hora, autor, cuerpo

    def _volcar_fila(self, mid, nick, texto, tipo, fecha, propio, leido=True):
        hora, autor, cuerpo = self._fila_valores(mid, nick, texto, tipo, fecha, propio)
        idx = self.lista_msgs.GetItemCount()
        visible = cuerpo if leido else _("(nuevo) ") + cuerpo
        self.lista_msgs.InsertItem(idx, hora)
        self.lista_msgs.SetItem(idx, 1, autor)
        self.lista_msgs.SetItem(idx, 2, visible.replace("\n", " "))
        # La LECTURA de la fila sigue la plantilla de IRC del usuario (editor de plantillas).
        from .irc_views import render_plantilla_irc
        self.lista_msgs.establecer_lectura(idx, render_plantilla_irc("irc_message", {
            "nick": autor, "content": visible, "hora": hora, "canal": self.canal}))
        self.msg_rows.append({"id": mid, "nick": nick, "texto": texto or "", "tipo": tipo,
                              "leido": leido, "propio": bool(propio)})
        return idx

    def _tipos_visibles_sql(self):
        """Tipos de fila a mostrar. Si el canal tiene ocultas las entradas/salidas (opción por
        canal, clave en canales grandes), join/part/quit no se pintan (el kick sí: es relevante)."""
        try:
            from app.services.irc import irc_service
            if not self.es_privado and not irc_service.mostrar_eventos(self.account_id, self.canal):
                return "AND tipo NOT IN ('join','part','quit')"
        except Exception:
            pass
        return ""

    def _recargar_vista(self):
        """Vacía la lista y vuelve a cargar el scrollback (p. ej. tras cambiar si se ven las
        entradas/salidas). No toca el servidor."""
        self.lista_msgs.DeleteAllItems()
        self.lista_msgs.limpiar_lecturas()
        self.msg_rows.clear()
        self._cargar_mensajes()

    def _cargar_mensajes(self):
        """Scrollback inicial: los últimos 300 del historial local, foco en el último."""
        filas = db.query(f"""
            SELECT id, nick, text, tipo, date, es_propio, leido FROM irc_messages
            WHERE account_id = ? AND channel = ? {self._tipos_visibles_sql()}
            ORDER BY id DESC LIMIT 300
        """, (self.account_id, self._canal_bd))
        self.lista_msgs.Freeze()
        try:
            for mid, nick, texto, tipo, fecha, propio, leido in reversed(filas):
                self._volcar_fila(mid, nick, texto, tipo, fecha, propio, leido=True)
        finally:
            self.lista_msgs.Thaw()
        self._ir_al_final(forzar=True)
        self._marcar_leido_bd()

    # ---------------- Tiempo real ----------------
    def _on_evento_irc(self, datos):
        """(Hilo IRCLoop) Evento nuevo; si es de este chat, insertar lo pendiente."""
        try:
            if (datos or {}).get("account_id") != self.account_id:
                return
            canal_ev = (datos.get("canal") or "").lower()
            if canal_ev != self._canal_bd:
                return
            if datos.get("tipo") == "typing":
                wx.CallAfter(self._mostrar_typing, datos.get("nick") or "", datos.get("estado"))
                return
            wx.CallAfter(self._insertar_mensajes_nuevos)
        except Exception:
            pass

    def _mostrar_typing(self, nick, estado):
        """'X está escribiendo…' en el TÍTULO de la ventana (como Telegram: la ventana es modal
        y el lector lo anuncia al consultar el título; sin interrumpir la lectura). Se limpia
        solo a los 6 segundos o si el estado deja de ser 'active'."""
        try:
            base = _("Conversación: {0}").format(self.canal)
            if estado == "active":
                self.SetTitle(base + " — " + _("{0} está escribiendo…").format(nick))
                if getattr(self, "_typing_timer", None):
                    self._typing_timer.Stop()
                self._typing_timer = wx.CallLater(6000, lambda: self.SetTitle(base) if self else None)
            else:
                self.SetTitle(base)
        except Exception:
            pass

    def _insertar_mensajes_nuevos(self):
        """Añade al final SOLO los mensajes que aún no están (por id), sin reconstruir y sin
        mover el foco (salvo que ya estuvieras en el último, para seguir leyendo en vivo)."""
        try:
            if not self or not bool(self.lista_msgs):
                return
            ultimo_id = max((m["id"] for m in self.msg_rows), default=0)
            filas = db.query(f"""
                SELECT id, nick, text, tipo, date, es_propio FROM irc_messages
                WHERE account_id = ? AND channel = ? AND id > ? {self._tipos_visibles_sql()}
                ORDER BY id ASC
            """, (self.account_id, self._canal_bd, ultimo_id))
            if not filas:
                return
            n = self.lista_msgs.GetItemCount()
            foco_al_final = (n == 0) or (self.lista_msgs.GetFocusedItem() >= n - 1)
            self.lista_msgs.Freeze()
            try:
                for mid, nick, texto, tipo, fecha, propio in filas:
                    # Lo tuyo entra leído; lo demás con su "(nuevo)" hasta que lo enfoques.
                    self._volcar_fila(mid, nick, texto, tipo, fecha, propio, leido=bool(propio))
            finally:
                self.lista_msgs.Thaw()
            if foco_al_final:
                self._ir_al_final(forzar=True)
            self._marcar_leido_bd()
        except Exception as e:
            log.debug(f"IRC: no se pudieron insertar mensajes en vivo: {e}")

    def _on_msg_foco(self, event):
        """Al enfocar una fila no leída se le quita la marca '(nuevo)'."""
        try:
            i = event.GetIndex()
            if 0 <= i < len(self.msg_rows) and not self.msg_rows[i]["leido"]:
                m = self.msg_rows[i]
                m["leido"] = True
                _h, _a, cuerpo = self._fila_valores(m["id"], m["nick"], m["texto"], m["tipo"], "", m["propio"])
                self.lista_msgs.SetItem(i, 2, cuerpo.replace("\n", " "))
        except Exception:
            pass
        event.Skip()

    def _ir_al_final(self, forzar=False):
        n = self.lista_msgs.GetItemCount()
        if n > 0:
            self.lista_msgs.Focus(n - 1)
            self.lista_msgs.EnsureVisible(n - 1)

    def _marcar_leido_bd(self):
        try:
            db.execute("UPDATE irc_channels SET unread_count = 0 WHERE account_id = ? AND name = ?",
                       (self.account_id, self._canal_bd))
            db.execute("UPDATE irc_messages SET leido = 1 WHERE account_id = ? AND channel = ?",
                       (self.account_id, self._canal_bd))
        except Exception:
            pass

    # ---------------- Entrada: envío, historial y Tab ----------------
    def _on_key_entrada(self, event):
        code = event.GetKeyCode()
        # Tab: autocompletar apodo SOLO si hay algo que completar (una palabra pegada al cursor,
        # o un ciclo de completado en marcha). En cualquier otro caso, Tab NAVEGA por la ventana
        # con normalidad (nunca debe dejar al usuario atrapado en el campo). Shift+Tab navega
        # SIEMPRE hacia atrás, pase lo que pase.
        if code == wx.WXK_TAB:
            if event.GetModifiers() == wx.MOD_NONE and (
                    self._tab_estado is not None or self._hay_prefijo_para_completar()):
                self._completar_apodo()
                return
            self._tab_estado = None
            self.txt_mensaje.Navigate(
                wx.NavigationKeyEvent.IsBackward if event.ShiftDown()
                else wx.NavigationKeyEvent.IsForward)
            return
        # Flechas arriba/abajo: historial de envíos (campo de una sola línea).
        if code == wx.WXK_UP and self._hist_envios:
            if self._hist_pos is None:
                self._hist_pos = len(self._hist_envios)
            if self._hist_pos > 0:
                self._hist_pos -= 1
                self.txt_mensaje.SetValue(self._hist_envios[self._hist_pos])
                self.txt_mensaje.SetInsertionPointEnd()
            return
        if code == wx.WXK_DOWN and self._hist_pos is not None:
            self._hist_pos += 1
            if self._hist_pos >= len(self._hist_envios):
                self._hist_pos = None
                self.txt_mensaje.SetValue("")
            else:
                self.txt_mensaje.SetValue(self._hist_envios[self._hist_pos])
                self.txt_mensaje.SetInsertionPointEnd()
            return
        self._tab_estado = None  # cualquier otra tecla reinicia el ciclo de Tab
        event.Skip()

    def _hay_prefijo_para_completar(self):
        """True si justo antes del cursor hay una palabra empezada (candidata a apodo). Con el
        campo vacío, el cursor al principio o tras un espacio, NO hay nada que completar y Tab
        debe navegar."""
        try:
            texto = self.txt_mensaje.GetValue()
            pos = self.txt_mensaje.GetInsertionPoint()
            return pos > 0 and pos <= len(texto) and texto[pos - 1] not in (" ", "\t")
        except Exception:
            return False

    def _nicks_para_completar(self):
        """Apodos candidatos: los presentes del canal (si la red está conectada) y, si no,
        los últimos que hablaron según el historial local."""
        try:
            from app.services.irc import irc_service
            if not self.es_privado:
                nicks = [n for n, _e in irc_service.usuarios_de_canal(self.account_id, self.canal)]
                if nicks:
                    return nicks
        except Exception:
            pass
        filas = db.query("""
            SELECT DISTINCT nick FROM irc_messages
            WHERE account_id = ? AND channel = ? AND nick <> '' ORDER BY id DESC LIMIT 100
        """, (self.account_id, self._canal_bd))
        return [f[0] for f in filas]

    def _completar_apodo(self):
        texto = self.txt_mensaje.GetValue()
        pos = self.txt_mensaje.GetInsertionPoint()
        if self._tab_estado is None:
            # Tomar la palabra que hay justo antes del cursor como prefijo a completar.
            inicio = pos
            while inicio > 0 and texto[inicio - 1] not in (" ", "\t"):
                inicio -= 1
            prefijo = texto[inicio:pos]
            if not prefijo:
                speak(_("Escribe el principio de un apodo y pulsa Tab."), interrupt=True)
                return
            candidatos = [n for n in self._nicks_para_completar()
                          if n.lower().startswith(prefijo.lower())]
            if not candidatos:
                speak(_("Ningún apodo empieza por {0}.").format(prefijo), interrupt=True)
                return
            self._tab_estado = {"inicio": inicio, "fin": pos, "cands": candidatos, "i": -1}
        est = self._tab_estado
        est["i"] = (est["i"] + 1) % len(est["cands"])
        nick = est["cands"][est["i"]]
        # Al principio de la línea se completa estilo IRC clásico: "apodo: ".
        sufijo = ": " if est["inicio"] == 0 else " "
        nuevo = texto[:est["inicio"]] + nick + sufijo
        resto = texto[est["fin"]:]
        # El texto tras el cursor original se conserva.
        self.txt_mensaje.ChangeValue(nuevo + resto)
        self.txt_mensaje.SetInsertionPoint(len(nuevo))
        est["fin"] = len(nuevo)
        speak(nick, interrupt=True)

    def _on_enviar(self, event):
        texto = self.txt_mensaje.GetValue().strip()
        if not texto:
            return
        self._hist_envios.append(texto)
        self._hist_pos = None
        self._tab_estado = None
        self.txt_mensaje.SetValue("")
        if texto.startswith("/") and not texto.startswith("//"):
            self._ejecutar_comando(texto)
            return
        if texto.startswith("//"):
            texto = texto[1:]  # "//" al principio = enviar una barra literal
        if self.es_servidor:
            # En la consola del servidor no hay a quién enviar un PRIVMSG: el texto suelto se manda
            # como ORDEN CRUDA al servidor (equivale a /quote). Los comandos con / siguen valiendo.
            self._enviar_crudo(texto)
            return
        self._enviar_texto("msg", self.canal, texto)

    def _enviar_crudo(self, linea):
        """Envía una línea cruda al servidor (consola del servidor)."""
        acc = self.account_id

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.comando_crudo(acc, linea)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar al servidor: {0}").format(err))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _enviar_texto(self, tipo, destino, texto):
        """Envía en segundo plano y avisa si falla (el mensaje enviado aparece por el evento
        del guardado propio)."""
        acc = self.account_id

        def _bg(ctx):
            from app.services.irc import irc_service
            if tipo == "action":
                irc_service.enviar_action(acc, destino, texto)
            elif tipo == "notice":
                irc_service.enviar_notice(acc, destino, texto)
            else:
                irc_service.enviar_mensaje(acc, destino, texto)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar: {0}").format(err))
            else:
                # El propio ya está en BD: pintarlo (si el envío era para este chat).
                if (destino or "").lower() == self._canal_bd:
                    try:
                        self._insertar_mensajes_nuevos()
                    except Exception:
                        pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- Comandos / ----------------
    def _ejecutar_comando(self, linea):
        partes = linea.split(" ", 1)
        cmd = partes[0][1:].lower()
        resto = (partes[1] if len(partes) > 1 else "").strip()
        acc = self.account_id
        from app.services.irc import irc_service

        if cmd == "help":
            mostrar_info(self, _AYUDA_COMANDOS, _("Comandos de IRC"))
            return
        if cmd == "me":
            if resto:
                self._enviar_texto("action", self.canal, resto)
            return
        if cmd == "clear":
            self.lista_msgs.DeleteAllItems()
            self.lista_msgs.limpiar_lecturas()
            self.msg_rows.clear()
            speak(_("Vista vaciada."), interrupt=True)
            return
        if cmd == "events":
            # /events on|off  → mostrar u ocultar las entradas/salidas (join/part/quit) de ESTE
            # canal, para que no tapen los mensajes. Sin argumento, alterna.
            if self.es_privado:
                speak(_("Las entradas y salidas solo aplican a los canales."), interrupt=True)
                return
            actual = irc_service.mostrar_eventos(acc, self.canal)
            nuevo = (resto.lower() not in ("off", "no", "0", "ocultar")) if resto else (not actual)
            irc_service.fijar_mostrar_eventos(acc, self.canal, nuevo)
            self._recargar_vista()
            speak(_("Se mostrarán las entradas y salidas.") if nuevo
                  else _("Se ocultan las entradas y salidas de este canal."), interrupt=True)
            return
        if cmd in ("msg", "notice") and resto:
            sub = resto.split(" ", 1)
            if len(sub) == 2:
                self._enviar_texto("notice" if cmd == "notice" else "msg", sub[0], sub[1])
            else:
                speak(_("Uso: /{0} destino texto").format(cmd), interrupt=True)
            return
        if cmd == "query" and resto:
            nick = resto.split(" ")[0]
            db_id = irc_service.abrir_privado(acc, nick)
            if db_id:
                dlg = IRCConversationDialog(self.GetParent(), acc, nick, es_privado=True)
                dlg.ShowModal()
                dlg.Destroy()
            return

        # Comandos que van al servidor en segundo plano.
        def _bg(ctx):
            if cmd == "join" and resto:
                canal = resto.split(" ")[0]
                irc_service._run(irc_service._clients[acc].join(canal), timeout=20.0)
            elif cmd == "part":
                canal = (resto.split(" ")[0] if resto else self.canal)
                irc_service._run(irc_service._clients[acc].part(canal), timeout=20.0)
            elif cmd == "nick" and resto:
                irc_service.cambiar_nick(acc, resto.split(" ")[0])
            elif cmd == "topic":
                irc_service.cambiar_tema(acc, self.canal, resto)
            elif cmd == "quote" and resto:
                irc_service.comando_crudo(acc, resto)
            # --- I7: estado propio, invitaciones, CTCP, modos... ---
            elif cmd == "away":
                irc_service.fijar_ausente(acc, resto)
            elif cmd == "back":
                irc_service.volver_disponible(acc)
            elif cmd == "invite" and resto:
                sub = resto.split(" ")
                nick = sub[0]
                canal = sub[1] if len(sub) > 1 else self.canal
                irc_service.invitar(acc, canal, nick)
            elif cmd == "knock":
                sub = resto.split(" ", 1)
                canal = sub[0] if sub and sub[0] else self.canal
                irc_service.llamar_puerta(acc, canal, sub[1] if len(sub) > 1 else "")
            elif cmd == "cycle":
                irc_service.reentrar_canal(acc, resto.split(" ")[0] if resto else self.canal)
            elif cmd == "whowas" and resto:
                return ("whowas", irc_service.whowas_legible(acc, resto.split(" ")[0]))
            elif cmd == "ping" and resto:
                irc_service.ctcp_ping(acc, resto.split(" ")[0])
            elif cmd == "ctcp" and resto:
                sub = resto.split(" ", 1)
                if len(sub) == 2:
                    irc_service.ctcp_a(acc, sub[0], sub[1])
                else:
                    raise Exception(_("Uso: /ctcp apodo CONSULTA (por ejemplo /ctcp fulano TIME)."))
            elif cmd == "mode" and resto:
                # /mode +m  ó  /mode +b mascara  ó  /mode #otro +i ...
                sub = resto.split(" ")
                if sub[0].startswith(("#", "&", "+", "!")):
                    canal = sub[0]
                    modo = sub[1] if len(sub) > 1 else ""
                    arg = sub[2] if len(sub) > 2 else ""
                else:
                    canal = self.canal
                    modo = sub[0]
                    arg = sub[1] if len(sub) > 1 else ""
                irc_service.fijar_modo_canal(acc, canal, modo, arg)
            elif cmd == "oper" and resto:
                sub = resto.split(" ", 1)
                if len(sub) == 2:
                    irc_service.hacerse_operador(acc, sub[0], sub[1])
                else:
                    raise Exception(_("Uso: /oper usuario contraseña."))
            elif cmd == "wallops" and resto:
                irc_service.enviar_wallops(acc, resto)
            elif cmd == "kick" and resto:
                sub = resto.split(" ", 1)
                irc_service.accion_usuario_canal(acc, self.canal, sub[0], "kick",
                                                 sub[1] if len(sub) > 1 else "")
            elif cmd == "ban" and resto:
                irc_service.banear_mascara(acc, self.canal, resto.split(" ")[0])
            elif cmd == "unban" and resto:
                irc_service.quitar_baneo(acc, self.canal, resto.split(" ")[0])
            elif cmd == "umode" and resto:
                irc_service.fijar_mis_modos(acc, resto.strip())
            elif cmd == "metadata":
                sub = resto.split(" ", 1)
                if not resto or sub[0].lower() == "ver":
                    return ("whowas", irc_service.leer_metadata(acc, "*"))
                clave = sub[0]
                valor = sub[1] if len(sub) > 1 else ""
                irc_service.fijar_metadata(acc, clave, valor)
            else:
                raise Exception(_("Comando no reconocido: /{0}. Escribe /help.").format(cmd))
            return True

        def _fin(res, err):
            if err:
                notificar(str(err))
            elif isinstance(res, tuple) and res and res[0] == "whowas":
                mostrar_info(self, res[1], _("Datos anteriores"))
            else:
                speak(_("Hecho."), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- Botones ----------------
    def _on_usuarios(self, event):
        """Nicklist accesible con rangos legibles y acciones por usuario (whois, privado,
        ignorar y, si eres operador, op/voz/expulsar/banear con confirmación)."""
        from app.services.irc import irc_service
        if not irc_service.usuarios_de_canal(self.account_id, self.canal):
            mostrar_info(self, _("No hay usuarios que mostrar (¿está conectada la red?)."),
                         _("Usuarios de {0}").format(self.canal))
            return
        from .irc_dialogs import IRCUsersDialog
        dlg = IRCUsersDialog(self, self.account_id, self.canal)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_tema(self, event):
        fila = db.query_one("SELECT topic FROM irc_channels WHERE account_id = ? AND name = ?",
                            (self.account_id, self._canal_bd))
        actual = fila[0] if fila and fila[0] else ""
        dlg = CustomTextEntryDialog(self, _("Tema del canal (Aceptar para cambiarlo, si tienes permiso):"),
                                    _("Tema de {0}").format(self.canal), value=actual)
        if dlg.ShowModal() == wx.ID_OK:
            nuevo = dlg.GetValue().strip()
            if nuevo != actual:
                self._enviar_tema(nuevo)
        dlg.Destroy()

    def _enviar_tema(self, tema):
        acc = self.account_id

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.cambiar_tema(acc, self.canal, tema)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Tema cambiado.") if not e else _("No se pudo cambiar el tema: {0}").format(e)),
            pass_context=True)

    def _on_gestion_canal(self, event):
        """Menú del botón Canal: modos del canal y lista de baneos (I7)."""
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Modos del canal (moderado, invitación, clave...)..."))
        self.Bind(wx.EVT_MENU, lambda e: self._dialogo_modos_canal(), it)
        it = menu.Append(wx.ID_ANY, _("Lista de baneos del canal..."))
        self.Bind(wx.EVT_MENU, lambda e: self._dialogo_baneos(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _dialogo_modos_canal(self):
        from .irc_dialogs import IRCModosCanalDialog
        dlg = IRCModosCanalDialog(self, self.account_id, self.canal)
        dlg.ShowModal()
        dlg.Destroy()

    def _dialogo_baneos(self):
        from .irc_dialogs import IRCBaneosDialog
        dlg = IRCBaneosDialog(self, self.account_id, self.canal)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------------- Menú contextual de un mensaje ----------------
    def _on_context_menu(self, event):
        i = self.lista_msgs.GetFocusedItem()
        if i < 0 or i >= len(self.msg_rows):
            return
        m = self.msg_rows[i]
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Copiar mensaje"))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._copiar_msg(mm), it)
        # Enlaces del mensaje: abrir o copiar (antes los enlaces de IRC no se podían abrir).
        urls = self._urls_de(m.get("texto") or "")
        if urls:
            if len(urls) == 1:
                it = menu.Append(wx.ID_ANY, _("Abrir enlace"))
                self.Bind(wx.EVT_MENU, lambda e, u=urls[0]: self._abrir_enlace(u), it)
                it = menu.Append(wx.ID_ANY, _("Copiar enlace"))
                self.Bind(wx.EVT_MENU, lambda e, u=urls[0]: self._copiar_enlace(u), it)
            else:
                sub = wx.Menu()
                for u in urls[:15]:
                    it2 = sub.Append(wx.ID_ANY, u[:80])
                    self.Bind(wx.EVT_MENU, lambda e, uu=u: self._abrir_enlace(uu), it2)
                menu.AppendSubMenu(sub, _("Abrir un enlace ({0})").format(len(urls)))
        if m.get("nick") and not m.get("propio"):
            it = menu.Append(wx.ID_ANY, _("Responder mencionando a {0}").format(m["nick"]))
            self.Bind(wx.EVT_MENU, lambda e, n=m["nick"]: self._mencionar(n), it)
            if not self.es_privado:
                it = menu.Append(wx.ID_ANY, _("Privado con {0}").format(m["nick"]))
                self.Bind(wx.EVT_MENU, lambda e, n=m["nick"]: self._privado_con(n), it)
            it = menu.Append(wx.ID_ANY, _("Ver datos de {0} (whois)").format(m["nick"]))
            self.Bind(wx.EVT_MENU, lambda e, n=m["nick"]: self._whois(n), it)
            it = menu.Append(wx.ID_ANY, _("Medir la latencia con {0} (ping)").format(m["nick"]))
            self.Bind(wx.EVT_MENU, lambda e, n=m["nick"]: self._ctcp(n, "PING"), it)
            sub_ctcp = wx.Menu()
            for q, etiq in (("TIME", _("Su hora local (TIME)")),
                            ("VERSION", _("Su programa (VERSION)")),
                            ("CLIENTINFO", _("Qué admite (CLIENTINFO)"))):
                it2 = sub_ctcp.Append(wx.ID_ANY, etiq)
                self.Bind(wx.EVT_MENU, lambda e, n=m["nick"], qq=q: self._ctcp(n, qq), it2)
            menu.AppendSubMenu(sub_ctcp, _("Consultar a {0} (CTCP)").format(m["nick"]))
            if not self.es_privado:
                it = menu.Append(wx.ID_ANY, _("Invitar a {0} a otro canal...").format(m["nick"]))
                self.Bind(wx.EVT_MENU, lambda e, n=m["nick"]: self._invitar_a(n), it)
            from app.services.irc import irc_service
            if irc_service.esta_ignorado(self.account_id, m["nick"]):
                it = menu.Append(wx.ID_ANY, _("Dejar de ignorar a {0}").format(m["nick"]))
                self.Bind(wx.EVT_MENU, lambda e, n=m["nick"]: self._ignorar(n, False), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Ignorar a {0}").format(m["nick"]))
                self.Bind(wx.EVT_MENU, lambda e, n=m["nick"]: self._ignorar(n, True), it)
        self.PopupMenu(menu)
        menu.Destroy()

    @staticmethod
    def _urls_de(texto):
        """Devuelve las URLs http(s) que haya en el texto de un mensaje (quitando la puntuación
        final típica: comas, puntos, paréntesis de cierre...)."""
        import re
        crudas = re.findall(r'https?://[^\s<>"\')\]]+', texto or "")
        return [u.rstrip('.,;:!?)]}>\'"') for u in crudas]

    def _abrir_enlace(self, url):
        from app.core.privacy import abrir_url
        try:
            abrir_url(url)
        except Exception as e:
            notificar(_("No se pudo abrir el enlace: {0}").format(e))

    def _copiar_enlace(self, url):
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(url))
            finally:
                wx.TheClipboard.Close()
            notificar(_("Enlace copiado."))

    def _whois(self, nick):
        from .irc_dialogs import mostrar_whois
        mostrar_whois(self, self.account_id, nick)

    def _ctcp(self, nick, consulta):
        """CTCP a un usuario (PING mide latencia; TIME/VERSION/CLIENTINFO consultan). La respuesta
        llega por el event_bus y la anuncia el controlador."""
        acc = self.account_id
        speak(_("Consultando a {0}...").format(nick), interrupt=True)

        def _bg(ctx):
            from app.services.irc import irc_service
            if consulta == "PING":
                irc_service.ctcp_ping(acc, nick)
            else:
                irc_service.ctcp_a(acc, nick, consulta)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: (
            notificar(_("No se pudo: {0}").format(e)) if e else None), pass_context=True)

    def _invitar_a(self, nick):
        dlg = CustomTextEntryDialog(self, _("¿A qué canal invitar a {0}? (ej: #otro)").format(nick),
                                    _("Invitar a un canal"))
        canal = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not canal:
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.irc import irc_service
            irc_service.invitar(acc, canal, nick)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Invitación enviada.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def _ignorar(self, nick, valor):
        from app.services.irc import irc_service
        irc_service.ignorar(self.account_id, nick, valor)
        notificar(_("{0} ignorado. No verás sus mensajes.").format(nick) if valor
                  else _("{0} ya no está ignorado.").format(nick))

    def _copiar_msg(self, m):
        try:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(f"<{m['nick']}> {m['texto']}"))
                wx.TheClipboard.Close()
                speak(_("Copiado."), interrupt=True)
        except Exception:
            pass

    def _mencionar(self, nick):
        self.txt_mensaje.SetValue(f"{nick}: " + self.txt_mensaje.GetValue())
        self.txt_mensaje.SetInsertionPointEnd()
        self.txt_mensaje.SetFocus()

    def _privado_con(self, nick):
        from app.services.irc import irc_service
        irc_service.abrir_privado(self.account_id, nick)
        dlg = IRCConversationDialog(self.GetParent(), self.account_id, nick, es_privado=True)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------------- Cierre ----------------
    def _on_cerrar(self, event):
        try:
            event_bus.unsubscribe("irc_evento", self._on_evento_irc)
        except Exception:
            pass
        self._marcar_leido_bd()
        self.EndModal(wx.ID_CANCEL)
