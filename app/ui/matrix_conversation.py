# -*- coding: utf-8 -*-
"""Ventana de Conversación de Matrix (hermana de las de Telegram e IRC, tanda M2).

Mensajes en VIVO con inserción silenciosa (sin reconstruir la lista ni mover el foco, con
marca "(nuevo)"), campo de entrada con Intro para enviar, historial de envíos con las
flechas, /me, typing en ambos sentidos, miembros y marcas de lectura (local + servidor).
"""
import re
import time
import wx
import logging

from app.core.database import db
from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, mostrar_info

log = logging.getLogger(__name__)

_AYUDA_COMANDOS = (
    "/me <texto> — acción (\"* nombre hace algo\")\n"
    "/clear — vaciar la vista (el historial local no se borra)\n"
    "/help — esta ayuda"
)


class MatrixConversationDialog(wx.Dialog):
    """Conversación de una sala o mensaje directo de Matrix."""

    def __init__(self, parent, account_id: int, room_id: str, titulo_sala: str = ""):
        self._titulo_base = _("Conversación: {0}").format(titulo_sala or room_id)
        super().__init__(parent, title=self._titulo_base, size=(720, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Ventana de conversación de Matrix"))
        self.account_id = account_id
        self.room_id = room_id
        self.titulo_sala = titulo_sala or room_id
        self.msg_rows = []
        self._hist_envios = []
        self._hist_pos = None
        self._typing_ultimo = 0.0
        self._reply_a = None    # event_id al que se responde (None = mensaje normal)
        self._editando = None   # event_id propio en edición (None = mensaje normal)
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, self._on_cerrar)
        self.CenterOnParent()
        self._cargar_mensajes()
        event_bus.subscribe("matrix_evento", self._on_evento_matrix)
        self.txt_mensaje.SetFocus()

    # ---------------- UI ----------------
    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("Mensajes (los más recientes al final):")), 0, wx.ALL, 5)
        self.lista_msgs = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de mensajes de la conversación"))
        self.lista_msgs.InsertColumn(0, _("Hora"), width=70)
        self.lista_msgs.InsertColumn(1, _("Autor"), width=160)
        self.lista_msgs.InsertColumn(2, _("Mensaje"), width=450)
        self.lista_msgs.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_msg_foco)
        self.lista_msgs.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
        self.lista_msgs.Bind(wx.EVT_KEY_DOWN, self._on_msg_key)
        sizer.Add(self.lista_msgs, 1, wx.EXPAND | wx.ALL, 5)

        self.lbl_escribir = wx.StaticText(self, label=_("Escribir mensaje (Intro envía; /help para comandos):"))
        sizer.Add(self.lbl_escribir, 0, wx.ALL, 5)
        self.txt_mensaje = AccessibleTextCtrl(
            self, style=wx.TE_PROCESS_ENTER,
            name=_("Campo de redacción de mensaje"),
            ayuda_texto=_("Escribe y pulsa Intro para enviar. Flecha arriba recupera envíos "
                          "anteriores. Comandos con /, p. ej. /me."))
        self.txt_mensaje.Bind(wx.EVT_TEXT_ENTER, self._on_enviar)
        self.txt_mensaje.Bind(wx.EVT_KEY_DOWN, self._on_key_entrada)
        self.txt_mensaje.Bind(wx.EVT_TEXT, self._on_escribiendo)
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt_mensaje)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt_mensaje)
        sizer.Add(self.txt_mensaje, 0, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_enviar = wx.Button(self, label=_("&Enviar"), name=_("Enviar mensaje"))
        self.btn_enviar.Bind(wx.EVT_BUTTON, self._on_enviar)
        fila.Add(self.btn_enviar, 0, wx.ALL, 5)
        btn_adjuntar = wx.Button(self, label=_("Ad&juntar archivo..."), name=_("Adjuntar y enviar un archivo"))
        btn_adjuntar.Bind(wx.EVT_BUTTON, self._on_adjuntar)
        fila.Add(btn_adjuntar, 0, wx.ALL, 5)
        self._grabando = False
        self.btn_grabar = wx.Button(self, label=_("&Grabar nota de voz"), name=_("Grabar y enviar una nota de voz"))
        self.btn_grabar.Bind(wx.EVT_BUTTON, self._on_grabar_voz)
        fila.Add(self.btn_grabar, 0, wx.ALL, 5)
        btn_anteriores = wx.Button(self, label=_("Cargar an&teriores"), name=_("Cargar mensajes más antiguos"))
        btn_anteriores.Bind(wx.EVT_BUTTON, self._on_cargar_anteriores)
        fila.Add(btn_anteriores, 0, wx.ALL, 5)
        btn_miembros = wx.Button(self, label=_("&Miembros..."), name=_("Ver los miembros de la sala"))
        btn_miembros.Bind(wx.EVT_BUTTON, self._on_miembros)
        fila.Add(btn_miembros, 0, wx.ALL, 5)
        btn_final = wx.Button(self, label=_("Ir al &final"), name=_("Ir al último mensaje"))
        btn_final.Bind(wx.EVT_BUTTON, lambda e: self._ir_al_final())
        fila.Add(btn_final, 0, wx.ALL, 5)
        # M7: más envíos (encuesta y ubicación) en un botón con menú, para no saturar la fila.
        btn_mas = wx.Button(self, label=_("Má&s..."), name=_("Más acciones de envío"))
        btn_mas.Bind(wx.EVT_BUTTON, self._on_mas_envios)
        fila.Add(btn_mas, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        sizer.Add(fila, 0, wx.EXPAND | wx.ALL, 6)

        self.SetSizer(sizer)

    # ---------------- Carga y pintado ----------------
    _SQL_CAMPOS = ("id, COALESCE(NULLIF(sender_nombre,''), sender), texto, tipo, date, es_propio, "
                   "event_id, media_mxc, COALESCE(reacciones,''), COALESCE(editado,0), "
                   "COALESCE(reply_to,''), COALESCE(media_crypto,''), COALESCE(thread_root,''), "
                   "COALESCE(poll_json,''), sender, COALESCE(send_state,'')")

    def _volcar_fila(self, fila_bd, leido=True):
        (mid, quien, texto, tipo, fecha, propio, event_id, media_mxc,
         reacciones, editado, reply_to, media_crypto, thread_root, poll_json, sender,
         send_state) = fila_bd
        from app.services.matrix.matrix_common import hora_local
        hora = hora_local(fecha)
        autor = f"* {quien}" if tipo == "accion" else quien
        cuerpo = texto or ""
        extras = []
        if reply_to:
            extras.append(_("(respuesta)"))
        if thread_root:
            extras.append(_("(en hilo)"))
        if editado:
            extras.append(_("(editado)"))
        if send_state == "fallido":
            extras.append(_("(SIN ENVIAR — menú para reintentar)"))
        if reacciones:
            extras.append(_("[Reacciones: {0}]").format(reacciones))
        if tipo == "media":
            # Audio: Espacio REPRODUCE con el reproductor (como Telegram). Resto: Espacio DESCARGA
            # a la carpeta de descargas (data/downloads/matrix/<chat>).
            if self._es_audio_media(texto):
                extras.append(_("(Espacio para reproducir)"))
            else:
                extras.append(_("(Espacio para descargar)"))
        elif tipo == "encuesta":
            extras.append(_("(menú contextual para votar)"))
        # Pista de que Intro abre el enlace (solo si hay uno y no es un adjunto, para no saturar).
        if tipo != "media" and re.search(r'https?://', cuerpo):
            extras.append(_("(Intro para abrir el enlace)"))
        visible = " ".join([cuerpo] + extras).strip()
        idx = self.lista_msgs.GetItemCount()
        mostrado = visible if leido else _("(nuevo) ") + visible
        self.lista_msgs.InsertItem(idx, hora)
        self.lista_msgs.SetItem(idx, 1, autor)
        self.lista_msgs.SetItem(idx, 2, mostrado.replace("\n", " "))
        # La LECTURA de la fila sigue la plantilla de Matrix del usuario (editor de plantillas).
        from .matrix_views import render_plantilla_matrix
        self.lista_msgs.establecer_lectura(idx, render_plantilla_matrix("matrix_message", {
            "author": autor, "content": mostrado, "hora": hora, "sala": self.titulo_sala}))
        self.msg_rows.append({"id": mid, "quien": quien, "texto": cuerpo, "tipo": tipo,
                              "leido": leido, "propio": bool(propio), "event_id": event_id,
                              "media_mxc": media_mxc or "", "media_crypto": media_crypto or "",
                              "thread_root": thread_root or "", "poll_json": poll_json or "",
                              "sender": sender or "", "visible": visible,
                              "reply_to": reply_to or "", "send_state": send_state or ""})

    def _cargar_mensajes(self, foco_al_final=True):
        filas = db.query(f"""
            SELECT {self._SQL_CAMPOS} FROM matrix_mensajes
            WHERE account_id = ? AND room_id = ?
            ORDER BY id DESC LIMIT 300
        """, (self.account_id, self.room_id))
        self.lista_msgs.Freeze()
        try:
            self.lista_msgs.DeleteAllItems()
            self.lista_msgs.limpiar_lecturas()
            self.msg_rows.clear()
            for fila in reversed(filas):
                self._volcar_fila(fila, leido=True)
        finally:
            self.lista_msgs.Thaw()
        if foco_al_final:
            self._ir_al_final()
        self._marcar_leida_local()

    # ---------------- Tiempo real ----------------
    def _on_evento_matrix(self, datos):
        try:
            if (datos or {}).get("account_id") != self.account_id:
                return
            if (datos.get("room_id") or "") != self.room_id:
                return
            tipo = datos.get("tipo")
            if tipo == "typing":
                wx.CallAfter(self._mostrar_typing, datos.get("nombres") or [])
                return
            if tipo == "mensaje":
                wx.CallAfter(self._insertar_mensajes_nuevos)
        except Exception:
            pass

    def _insertar_mensajes_nuevos(self):
        try:
            if not self or not bool(self.lista_msgs):
                return
            ultimo_id = max((m["id"] for m in self.msg_rows), default=0)
            filas = db.query(f"""
                SELECT {self._SQL_CAMPOS} FROM matrix_mensajes
                WHERE account_id = ? AND room_id = ? AND id > ?
                ORDER BY id ASC
            """, (self.account_id, self.room_id, ultimo_id))
            if not filas:
                return
            n = self.lista_msgs.GetItemCount()
            foco_al_final = (n == 0) or (self.lista_msgs.GetFocusedItem() >= n - 1)
            self.lista_msgs.Freeze()
            try:
                for fila in filas:
                    self._volcar_fila(fila, leido=bool(fila[5]))
            finally:
                self.lista_msgs.Thaw()
            if foco_al_final:
                self._ir_al_final()
            self._marcar_leida_local()
        except Exception as e:
            log.debug(f"Matrix: no se pudieron insertar mensajes en vivo: {e}")

    def _on_msg_foco(self, event):
        try:
            i = event.GetIndex()
            if 0 <= i < len(self.msg_rows) and not self.msg_rows[i]["leido"]:
                m = self.msg_rows[i]
                m["leido"] = True
                self.lista_msgs.SetItem(i, 2, (m.get("visible") or m["texto"] or "").replace("\n", " "))
        except Exception:
            pass
        event.Skip()

    def _on_msg_key(self, event):
        """Espacio sobre un adjunto: reproducir si es audio, descargar si es cualquier otro.
        Intro sobre un mensaje con enlace(s): abrirlo(s)."""
        code = event.GetKeyCode()
        i = self.lista_msgs.GetFocusedItem()
        m = self.msg_rows[i] if 0 <= i < len(self.msg_rows) else None
        if code == wx.WXK_SPACE and m and m.get("media_mxc"):
            self._accion_media(m)
            return
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and m and self._urls_de(m):
            self._abrir_enlace(m)
            return
        event.Skip()

    @staticmethod
    def _es_audio_media(texto: str) -> bool:
        """True si la etiqueta del adjunto es de audio ("[Mensaje de audio: ...]"; también el
        histórico "[Audio: ...]" guardado en la base antes del cambio de etiqueta)."""
        t = (texto or "").lstrip()
        return t.startswith(_("[Mensaje de audio:")) or t.startswith("[Audio:")

    @staticmethod
    def _urls_de(m) -> list:
        """URLs (http/https) que aparecen en el texto del mensaje, en orden y sin repetir."""
        texto = m.get("texto") or ""
        # Cortamos en espacios y en cierres típicos de puntuación/paréntesis que no forman parte
        # de la URL; el resto (incluidos '?', '#', '=' de las query) se conserva.
        crudas = re.findall(r'https?://[^\s<>"\')\]}]+', texto)
        vistas, salida = set(), []
        for u in crudas:
            u = u.rstrip('.,;:!?')  # puntuación pegada al final de la frase
            if u and u not in vistas:
                vistas.add(u)
                salida.append(u)
        return salida

    def _abrir_enlace(self, m):
        """Abre el/los enlace(s) del mensaje. Uno solo se abre directo; con varios, un diálogo
        accesible para elegir (mismo patrón que Mastodon)."""
        urls = self._urls_de(m)
        if not urls:
            speak(_("Este mensaje no tiene ningún enlace."), interrupt=True)
            return
        from app.core.privacy import abrir_url, limpiar_url
        if len(urls) == 1:
            url = limpiar_url(urls[0])
            speak(_("Abriendo enlace: {0}").format(url), interrupt=True)
            abrir_url(url)
            return
        dlg = wx.SingleChoiceDialog(self, _("Selecciona el enlace que quieres abrir:"),
                                    _("Abrir enlace"), urls)
        if dlg.ShowModal() == wx.ID_OK:
            url = limpiar_url(dlg.GetStringSelection())
            speak(_("Abriendo enlace: {0}").format(url), interrupt=True)
            abrir_url(url)
        dlg.Destroy()

    def _quien_leyo(self, m):
        """Dice quién ha leído (recibo de lectura) hasta este mensaje o más allá."""
        eid = m.get("event_id")
        if not eid:
            return
        # Buscar la fecha del mensaje en la fila (para el respaldo por marca de tiempo).
        fecha = ""
        try:
            from app.core.database import db
            fila = db.query_one("SELECT date FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
                                (self.account_id, eid))
            fecha = (fila[0] if fila else "") or ""
        except Exception:
            pass
        from app.services.matrix import matrix_service
        nombres = matrix_service.quien_ha_leido(self.account_id, self.room_id, eid, fecha)
        if not nombres:
            notificar(_("Todavía nadie ha marcado como leído hasta aquí (o el servidor no lo informa)."))
            return
        if len(nombres) <= 6:
            notificar(_("Leído por: {0}.").format(", ".join(nombres)))
        else:
            notificar(_("Leído por {0} personas: {1}...").format(len(nombres), ", ".join(nombres[:6])))

    @staticmethod
    def _nombre_adjunto(m) -> str:
        """Extrae el nombre REAL del archivo de la etiqueta "[Tipo: nombre.ext]". Antes se pasaba
        la etiqueta entera como nombre y la extensión salía rota (".txt]"): el archivo cacheado no
        se podía abrir con su programa asociado."""
        import re
        enc = re.match(r"^\[[^:]+:\s*(.+)\]$", (m.get("texto") or "").strip())
        if enc:
            return enc.group(1).strip()
        # Respaldo: el media_id del mxc:// (sin extensión, pero nombre válido).
        return (m.get("media_mxc") or "adjunto").rsplit("/", 1)[-1]

    def _accion_media(self, m):
        """Acción de Espacio sobre un adjunto: audio → reproducir; resto → descargar."""
        if self._es_audio_media(m.get("texto") or ""):
            self._reproducir_audio(m)
        else:
            self._descargar_adjunto(m)

    def _reproducir_audio(self, m):
        """Reproduce el mensaje de audio con el reproductor de redes (player1), como Telegram:
        el mismo Espacio alterna pausa/reanudar si ya está sonando ese audio."""
        from app.services.media_player.player_service import player_service
        clave = m.get("event_id") or m.get("id")

        # Si es el audio que ya está sonando, alternar pausa/reproducción.
        if getattr(self, "_audio_actual", None) == clave:
            try:
                estado = player_service.backend.obtener_estado()
            except Exception:
                estado = ""
            if estado == "reproduciendo":
                player_service.pausar()
                speak(_("Pausado."), interrupt=True)
            else:
                player_service.reproducir()
                speak(_("Reproduciendo."), interrupt=True)
            return

        speak(_("Cargando audio..."), interrupt=True)
        acc, mxc = self.account_id, m["media_mxc"]
        nombre, crypto = self._nombre_adjunto(m), m.get("media_crypto") or ""

        def _bg(ctx):
            from app.services.matrix import matrix_service
            from app.services.audio import microphone
            ruta = matrix_service.descargar_media(acc, mxc, nombre, media_crypto=crypto)
            # Convertir a WAV si BASS no soporta el formato (p. ej. Opus de las notas de voz).
            return microphone.convertir_a_wav_si_necesario(ruta) if ruta else ruta

        def _fin(ruta, err):
            if err or not ruta:
                notificar(_("No se pudo reproducir el audio: {0}").format(err or ""))
                return
            self._audio_actual = clave
            player_service.cargar_y_reproducir(ruta, self.titulo_sala, service_origin="matrix",
                                               account_origin_id=self.account_id)
            speak(_("Reproduciendo. Pulse Espacio para pausar."), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _descargar_adjunto(self, m):
        """Descarga el adjunto a la carpeta de DESCARGAS organizada por red y chat
        (data/downloads/matrix/<chat>/) y anuncia la ruta final."""
        speak(_("Descargando adjunto..."), interrupt=True)
        acc, mxc = self.account_id, m["media_mxc"]
        nombre, crypto = self._nombre_adjunto(m), m.get("media_crypto") or ""
        titulo = self.titulo_sala

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.descargar_a_descargas(acc, mxc, nombre, titulo,
                                                        media_crypto=crypto)

        def _fin(ruta, err):
            if err:
                notificar(_("No se pudo descargar: {0}").format(err))
                return
            notificar(_("Descargado: {0}").format(ruta))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _abrir_media(self, m):
        """Descarga el adjunto a la caché (si hace falta) y lo abre con el programa asociado
        (opción del menú contextual; Espacio ya no abre: reproduce o descarga)."""
        acc, mxc = self.account_id, m["media_mxc"]
        nombre, crypto = self._nombre_adjunto(m), m.get("media_crypto") or ""
        # SEGURIDAD: un adjunto de tipo ACTIVO (ejecutable/script/acceso directo) NUNCA se abre desde
        # la app (evita ejecutar malware recibido con un clic). Se descarga a la carpeta de descargas
        # —con Mark-of-the-Web— y se avisa; el usuario decide qué hacer con él fuera de aquí.
        from app.services.matrix.matrix_medios import es_extension_activa
        if es_extension_activa(nombre):
            notificar(_("Por seguridad, «{0}» no se abre desde la aplicación (es un archivo "
                        "ejecutable o de script). Se descargará a tu carpeta de descargas.").format(
                            nombre))
            self._descargar_adjunto(m)
            return
        speak(_("Descargando adjunto..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.descargar_media(acc, mxc, nombre, media_crypto=crypto)

        def _fin(ruta, err):
            if err:
                notificar(_("No se pudo descargar: {0}").format(err))
                return
            try:
                import os
                os.startfile(ruta)
                speak(_("Abierto."), interrupt=True)
            except Exception as e:
                notificar(_("Descargado en {0}, pero no se pudo abrir: {1}").format(ruta, e))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _ir_al_final(self):
        n = self.lista_msgs.GetItemCount()
        if n > 0:
            self.lista_msgs.Focus(n - 1)
            self.lista_msgs.EnsureVisible(n - 1)

    def _mostrar_typing(self, nombres):
        """'X está escribiendo…' en el título (se limpia solo)."""
        try:
            if nombres:
                quien = ", ".join(nombres[:3])
                self.SetTitle(self._titulo_base + " — " + _("{0} está escribiendo…").format(quien))
                if getattr(self, "_typing_timer", None):
                    self._typing_timer.Stop()
                self._typing_timer = wx.CallLater(
                    8000, lambda: self.SetTitle(self._titulo_base) if self else None)
            else:
                self.SetTitle(self._titulo_base)
        except Exception:
            pass

    # ---------------- Entrada ----------------
    def _on_key_entrada(self, event):
        code = event.GetKeyCode()
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
        event.Skip()

    def _on_escribiendo(self, event):
        """Typing propio hacia la sala, con regulación (máximo uno cada 4 s)."""
        event.Skip()
        ahora = time.monotonic()
        if ahora - self._typing_ultimo < 4.0:
            return
        self._typing_ultimo = ahora
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.enviar_typing(acc, room, True)
            return True
        task_manager.start_task(_bg, pass_context=True, unique_key=f"matrix:typing:{room}")

    def _on_enviar(self, event):
        texto = self.txt_mensaje.GetValue().strip()
        if not texto:
            return
        self._hist_envios.append(texto)
        self._hist_pos = None
        self.txt_mensaje.SetValue("")
        emote = False
        if texto.startswith("/") and not texto.startswith("//"):
            partes = texto.split(" ", 1)
            cmd = partes[0][1:].lower()
            resto = (partes[1] if len(partes) > 1 else "").strip()
            if cmd == "help":
                mostrar_info(self, _AYUDA_COMANDOS, _("Comandos de Matrix"))
                return
            if cmd == "clear":
                self.lista_msgs.DeleteAllItems()
                self.lista_msgs.limpiar_lecturas()
                self.msg_rows.clear()
                speak(_("Vista vaciada."), interrupt=True)
                return
            if cmd == "me" and resto:
                texto, emote = resto, True
            else:
                speak(_("Comando no reconocido: /{0}. Escribe /help.").format(cmd), interrupt=True)
                return
        elif texto.startswith("//"):
            texto = texto[1:]
        acc, room = self.account_id, self.room_id
        reply_a, editando = self._reply_a, self._editando
        self._quitar_estado_entrada()

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.enviar_mensaje(acc, room, texto, emote=emote,
                                          reply_to=reply_a or "", editar_event=editando or "")
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar: {0}").format(err))
                # El envío fallido se guardó como fila local "sin enviar": mostrarla para poder
                # reintentar desde su menú (estado de envío, P2).
                if not editando:
                    try:
                        self._insertar_mensajes_nuevos()
                    except Exception:
                        pass
            else:
                try:
                    if editando:
                        self._cargar_mensajes()  # la edición cambia una fila existente
                    else:
                        self._insertar_mensajes_nuevos()
                except Exception:
                    pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _quitar_estado_entrada(self):
        """Sale del modo respuesta/edición y restaura la etiqueta del campo."""
        self._reply_a = None
        self._editando = None
        self.lbl_escribir.SetLabel(_("Escribir mensaje (Intro envía; /help para comandos):"))

    # ---------------- Adjuntar y paginar ----------------
    def _on_adjuntar(self, event):
        dlg = wx.FileDialog(self, _("Elige un archivo para enviar"),
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ruta = dlg.GetPath()
        dlg.Destroy()
        speak(_("Enviando archivo..."), interrupt=True)
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.enviar_archivo(acc, room, ruta)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar el archivo: {0}").format(err))
            else:
                notificar(_("Archivo enviado."))
                try:
                    self._insertar_mensajes_nuevos()
                except Exception:
                    pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_grabar_voz(self, event):
        """Graba con el micrófono (BASS) y envía la nota de voz a la sala (patrón Telegram)."""
        acc, room = self.account_id, self.room_id
        if not self._grabando:
            def _bg(ctx):
                from app.services.matrix import matrix_service
                return matrix_service.iniciar_grabacion_voz()

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo iniciar la grabación: {0}").format(err))
                else:
                    self._grabando = True
                    self.btn_grabar.SetLabel(_("&Detener y enviar nota de voz"))
                    speak(_("Grabando nota de voz. Pulsa de nuevo para detener y enviar."), interrupt=True)
            speak(_("Iniciando grabación..."), interrupt=True)
            task_manager.start_task(_bg, callback=_fin, pass_context=True)
        else:
            def _bg(ctx):
                from app.services.matrix import matrix_service
                return matrix_service.detener_grabacion_y_enviar(acc, room)

            def _fin(res, err):
                self._grabando = False
                self.btn_grabar.SetLabel(_("&Grabar nota de voz"))
                if err:
                    notificar(_("Error al enviar la nota de voz: {0}").format(err))
                else:
                    notificar(_("Nota de voz enviada."))
                    try:
                        self._insertar_mensajes_nuevos()
                    except Exception:
                        pass
            speak(_("Deteniendo y enviando..."), interrupt=True)
            task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_cargar_anteriores(self, event):
        speak(_("Cargando mensajes anteriores..."), interrupt=True)
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.cargar_anteriores(acc, room)

        def _fin(n, err):
            if err:
                notificar(_("No se pudieron cargar: {0}").format(err))
                return
            notificar(_("{0} mensajes antiguos añadidos.").format(n or 0))
            if n:
                # Recargar la lista SIN mover el foco al final: quedas donde estabas leyendo.
                foco = self.lista_msgs.GetFocusedItem()
                ev_foco = (self.msg_rows[foco]["event_id"]
                           if 0 <= foco < len(self.msg_rows) else None)
                self._cargar_mensajes(foco_al_final=False)
                if ev_foco:
                    for i, m in enumerate(self.msg_rows):
                        if m["event_id"] == ev_foco:
                            self.lista_msgs.Focus(i)
                            self.lista_msgs.EnsureVisible(i)
                            break
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- Miembros y menú ----------------
    def _on_miembros(self, event):
        """Abre la LISTA de miembros seleccionable (no texto): eliges a alguien y, por menú
        contextual, le mandas un directo, ves su perfil, lo firmas o lo moderas (según permisos).
        Reaprovecha MatrixMiembrosDialog, el mismo que sale desde la lista de salas."""
        from .matrix_dialogs import MatrixMiembrosDialog
        dlg = MatrixMiembrosDialog(self, self.account_id, self.room_id, self.titulo_sala)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_context_menu(self, event):
        i = self.lista_msgs.GetFocusedItem()
        if i < 0 or i >= len(self.msg_rows):
            return
        m = self.msg_rows[i]
        menu = wx.Menu()
        # Mensaje propio que NO se pudo enviar: reintentar o descartar (estado de envío, P2).
        if m.get("send_state") == "fallido":
            it = menu.Append(wx.ID_ANY, _("Reintentar el envío"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._reintentar_envio(mm), it)
            it = menu.Append(wx.ID_ANY, _("Descartar el mensaje no enviado"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._descartar_no_enviado(mm), it)
            menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Responder a este mensaje"))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._responder_a(mm), it)
        if m.get("event_id"):
            it = menu.Append(wx.ID_ANY, _("Responder en hilo / ver el hilo..."))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._ver_hilo(mm), it)
        it = menu.Append(wx.ID_ANY, _("Reaccionar..."))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._reaccionar_a(mm), it)
        it = menu.Append(wx.ID_ANY, _("Quitar mi reacción..."))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._quitar_reaccion_a(mm), it)
        if m.get("event_id"):
            it = menu.Append(wx.ID_ANY, _("¿Quién ha leído este mensaje?"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._quien_leyo(mm), it)
        if m.get("tipo") == "encuesta":
            it = menu.Append(wx.ID_ANY, _("Votar en la encuesta..."))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._votar_encuesta(mm), it)
            if m.get("propio"):
                it = menu.Append(wx.ID_ANY, _("Cerrar la encuesta..."))
                self.Bind(wx.EVT_MENU, lambda e, mm=m: self._cerrar_encuesta(mm), it)
        if (m.get("texto") or "").startswith(_("[Mensaje cifrado")):
            it = menu.Append(wx.ID_ANY, _("Solicitar las claves de este mensaje"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._solicitar_claves(mm), it)
        if (m.get("texto") or "").startswith(_("[Ubicación:")):
            it = menu.Append(wx.ID_ANY, _("Abrir la ubicación en el mapa"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._abrir_ubicacion(mm), it)
        enlaces = self._urls_de(m)
        if enlaces:
            etiqueta = (_("Abrir el enlace del mensaje") if len(enlaces) == 1
                        else _("Abrir un enlace del mensaje ({0})...").format(len(enlaces)))
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._abrir_enlace(mm), it)
        # Fijar/reportar: solo mensajes REALES del servidor (no las filas locales no enviadas).
        eid = m.get("event_id") or ""
        if eid and not eid.startswith("local:"):
            it = menu.Append(wx.ID_ANY, _("Fijar o quitar de fijados este mensaje"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._fijar_msg(mm), it)
            if not m.get("propio"):
                it = menu.Append(wx.ID_ANY, _("Reportar este mensaje a los administradores..."))
                self.Bind(wx.EVT_MENU, lambda e, mm=m: self._reportar_msg(mm), it)
        it = menu.Append(wx.ID_ANY, _("Copiar mensaje"))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._copiar_msg(mm), it)
        if m.get("media_mxc"):
            if self._es_audio_media(m.get("texto") or ""):
                it = menu.Append(wx.ID_ANY, _("Reproducir el mensaje de audio"))
                self.Bind(wx.EVT_MENU, lambda e, mm=m: self._reproducir_audio(mm), it)
            it = menu.Append(wx.ID_ANY, _("Descargar a la carpeta de descargas"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._descargar_adjunto(mm), it)
            it = menu.Append(wx.ID_ANY, _("Abrir el adjunto"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._abrir_media(mm), it)
        if m.get("propio") and m.get("tipo") == "msg":
            it = menu.Append(wx.ID_ANY, _("Editar mi mensaje"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._editar_msg(mm), it)
        if m.get("propio"):
            it = menu.Append(wx.ID_ANY, _("Borrar mi mensaje..."))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._borrar_msg(mm), it)
        if m.get("quien") and not m.get("propio"):
            it = menu.Append(wx.ID_ANY, _("Mencionar a {0}").format(m["quien"]))
            self.Bind(wx.EVT_MENU, lambda e, n=m["quien"]: self._mencionar(n), it)
        if m.get("sender") and not m.get("propio"):
            it = menu.Append(wx.ID_ANY, _("Ignorar a {0}...").format(m["quien"] or m["sender"]))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._ignorar_usuario(mm), it)
        self.PopupMenu(menu)
        menu.Destroy()

    # ---------------- Acciones M7 (hilos, encuestas, ubicación, claves, ignorar) ----------------
    def _recargar_visible(self):
        """Recarga la lista SIN mover el foco al final (tras votar/reaccionar/etc.)."""
        self._cargar_mensajes(foco_al_final=False)

    def _ver_hilo(self, m):
        """Abre el hilo del mensaje (su raíz si ya está en uno; él mismo si va a empezarlo)."""
        raiz = m.get("thread_root") or m.get("event_id")
        if not raiz:
            return
        from .matrix_dialogs import MatrixVerHiloDialog
        dlg = MatrixVerHiloDialog(self, self.account_id, self.room_id, raiz, self.titulo_sala)
        dlg.ShowModal()
        dlg.Destroy()

    def _quitar_reaccion_a(self, m):
        if not m.get("event_id"):
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("¿Qué reacción quitar? (el emoji exacto):"),
                                  _("Quitar mi reacción"))
        emoji = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not emoji:
            return
        acc, room, eid = self.account_id, self.room_id, m["event_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.quitar_reaccion(acc, room, eid, emoji)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Reacción quitada."))
                self._recargar_visible()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _votar_encuesta(self, m):
        import json as _j
        try:
            poll = _j.loads(m.get("poll_json") or "{}")
        except Exception:
            poll = {}
        opciones = poll.get("opciones") or {}
        if not opciones:
            notificar(_("Esta encuesta no tiene opciones legibles."))
            return
        if poll.get("cerrada"):
            notificar(_("La encuesta ya está cerrada."))
            return
        ids = list(opciones.keys())
        dlg = wx.SingleChoiceDialog(self, poll.get("pregunta") or _("Encuesta"),
                                    _("Votar"), [opciones[i] for i in ids])
        idx = dlg.GetSelection() if dlg.ShowModal() == wx.ID_OK else -1
        dlg.Destroy()
        if idx < 0:
            return
        acc, room, eid, aid = self.account_id, self.room_id, m["event_id"], ids[idx]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.votar_encuesta(acc, room, eid, aid)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo votar: {0}").format(err))
            else:
                notificar(_("Voto enviado."))
                self._recargar_visible()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _cerrar_encuesta(self, m):
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Cerrar la encuesta? Nadie más podrá votar."),
                     _("Cerrar encuesta")) != wx.ID_YES:
            return
        acc, room, eid = self.account_id, self.room_id, m["event_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.cerrar_encuesta(acc, room, eid)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo cerrar: {0}").format(err))
            else:
                notificar(_("Encuesta cerrada."))
                self._recargar_visible()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _solicitar_claves(self, m):
        """Pide a mis otros dispositivos la clave de un mensaje cifrado sin descifrar."""
        acc, room, eid = self.account_id, self.room_id, m.get("event_id") or ""
        if not eid:
            return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.solicitar_claves_mensaje(acc, room, eid)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Claves solicitadas: si otro de tus dispositivos las tiene, el mensaje se "
              "descifrará solo en un momento.") if not e
            else _("No se pudo: {0}").format(e)), pass_context=True)

    def _abrir_ubicacion(self, m):
        """Abre la ubicación (geo:lat,lon del texto) en OpenStreetMap."""
        import re
        enc = re.search(r"geo:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", m.get("texto") or "")
        if not enc:
            notificar(_("Este mensaje no tiene coordenadas legibles."))
            return
        lat, lon = enc.group(1), enc.group(2)
        from app.core.privacy import abrir_url
        abrir_url(f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}")
        speak(_("Abriendo el mapa..."), interrupt=True)

    def _ignorar_usuario(self, m):
        from .accessible_controls import confirmar
        quien = m.get("quien") or m.get("sender")
        if confirmar(self, _("¿Ignorar a {0}? Sus mensajes dejarán de llegarte en TODOS tus "
                             "clientes de Matrix (se guarda en el servidor). Se puede deshacer "
                             "desde este mismo menú sobre un mensaje suyo antiguo.").format(quien),
                     _("Ignorar usuario")) != wx.ID_YES:
            return
        acc, uid = self.account_id, m["sender"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.ignorar_usuario(acc, uid, True)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Usuario ignorado.") if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def _on_mas_envios(self, event):
        """Botón Más...: crear encuesta y enviar ubicación (M7)."""
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Crear encuesta..."))
        self.Bind(wx.EVT_MENU, lambda e: self._crear_encuesta(), it)
        it = menu.Append(wx.ID_ANY, _("Enviar ubicación..."))
        self.Bind(wx.EVT_MENU, lambda e: self._enviar_ubicacion(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _crear_encuesta(self):
        from .matrix_dialogs import MatrixCrearEncuestaDialog
        dlg = MatrixCrearEncuestaDialog(self)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        pregunta, opciones, oculta = dlg.pregunta, dlg.opciones, dlg.oculta
        dlg.Destroy()
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.crear_encuesta(acc, room, pregunta, opciones, oculta=oculta)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo crear la encuesta: {0}").format(err))
            else:
                notificar(_("Encuesta enviada."))
                self._recargar_visible()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _enviar_ubicacion(self):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Coordenadas 'latitud, longitud' (ej: 40.4168, -3.7038):"),
                                  _("Enviar ubicación"))
        crudo = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not crudo:
            return
        try:
            lat, lon = [float(p.strip()) for p in crudo.split(",", 1)]
        except Exception:
            notificar(_("Coordenadas no válidas: escribe 'latitud, longitud'."))
            return
        d2 = CustomTextEntryDialog(self, _("Descripción del lugar (opcional):"),
                                   _("Enviar ubicación"))
        descripcion = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.enviar_ubicacion(acc, room, lat, lon, descripcion)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar: {0}").format(err))
            else:
                notificar(_("Ubicación enviada."))
                self._recargar_visible()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _responder_a(self, m):
        """Modo respuesta: el próximo envío queda enlazado a este mensaje."""
        if not m.get("event_id"):
            return
        self._editando = None
        self._reply_a = m["event_id"]
        self.lbl_escribir.SetLabel(_("Respondiendo a {0} (Intro envía):").format(m["quien"]))
        speak(_("Respondiendo a {0}. Escribe y pulsa Intro.").format(m["quien"]), interrupt=True)
        self.txt_mensaje.SetFocus()

    def _editar_msg(self, m):
        """Modo edición: precarga el texto y el próximo envío sustituye al original."""
        if not m.get("event_id"):
            return
        self._reply_a = None
        self._editando = m["event_id"]
        self.txt_mensaje.SetValue(m.get("texto") or "")
        self.txt_mensaje.SetInsertionPointEnd()
        self.lbl_escribir.SetLabel(_("Editando tu mensaje (Intro guarda los cambios):"))
        speak(_("Editando. Cambia el texto y pulsa Intro."), interrupt=True)
        self.txt_mensaje.SetFocus()

    def _reaccionar_a(self, m):
        if not m.get("event_id"):
            return
        opciones = ["👍", "❤️", "😂", "😮", "😢", "🎉", _("Otra (escribirla)...")]
        dlg = wx.SingleChoiceDialog(self, _("Elige la reacción:"), _("Reaccionar"), opciones)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        sel = dlg.GetSelection()
        dlg.Destroy()
        if sel == len(opciones) - 1:
            from .accessible_controls import CustomTextEntryDialog
            d2 = CustomTextEntryDialog(self, _("Escribe el emoji o texto de la reacción:"), _("Reaccionar"))
            emoji = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
            d2.Destroy()
        else:
            emoji = opciones[sel]
        if not emoji:
            return
        acc, room, ev = self.account_id, self.room_id, m["event_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.reaccionar(acc, room, ev, emoji)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: (
            notificar(_("Reacción enviada.") if not e else _("No se pudo reaccionar: {0}").format(e)),
            self._cargar_mensajes(foco_al_final=False) if not e else None), pass_context=True)

    def _borrar_msg(self, m):
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Borrar este mensaje para todos?"), _("Borrar mensaje")) != wx.ID_YES:
            return
        acc, room, ev = self.account_id, self.room_id, m["event_id"]

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.borrar_mensaje(acc, room, ev)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: (
            notificar(_("Mensaje borrado.") if not e else _("No se pudo borrar: {0}").format(e)),
            self._cargar_mensajes(foco_al_final=False) if not e else None), pass_context=True)

    def _reintentar_envio(self, m):
        acc, room = self.account_id, self.room_id
        local_id, texto = m.get("event_id") or "", m.get("texto") or ""
        emote = (m.get("tipo") == "accion")
        reply_to, thread = m.get("reply_to") or "", m.get("thread_root") or ""

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.reintentar_envio(acc, room, local_id, texto, emote=emote,
                                            reply_to=reply_to, thread_root=thread)
            return True

        def _fin(res, err):
            if err:
                notificar(_("Sigue sin poder enviarse: {0}").format(err))
            else:
                notificar(_("Enviado."))
            self._cargar_mensajes(foco_al_final=False)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _descartar_no_enviado(self, m):
        acc, local_id = self.account_id, m.get("event_id") or ""

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.descartar_mensaje_local(acc, local_id)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: (
            notificar(_("Descartado.") if not e else _("No se pudo: {0}").format(e)),
            self._cargar_mensajes(foco_al_final=False) if not e else None), pass_context=True)

    def _fijar_msg(self, m):
        """Fija el mensaje si no lo está, o lo quita de fijados si ya lo estaba (toggle)."""
        acc, room, ev = self.account_id, self.room_id, m.get("event_id") or ""

        def _bg(ctx):
            from app.services.matrix import matrix_service
            fijados = matrix_service.eventos_fijados(acc, room)
            quitar = ev in fijados
            matrix_service.fijar_evento(acc, room, ev, fijar=not quitar)
            return quitar
        task_manager.start_task(_bg, callback=lambda quitar, e: notificar(
            (_("Quitado de fijados.") if quitar else _("Mensaje fijado."))
            if not e else _("No se pudo (¿tienes permiso?): {0}").format(e)), pass_context=True)

    def _reportar_msg(self, m):
        from .accessible_controls import CustomTextEntryDialog, confirmar
        if confirmar(self, _("¿Reportar este mensaje a los administradores del servidor?"),
                     _("Reportar mensaje")) != wx.ID_YES:
            return
        d = CustomTextEntryDialog(self, _("Motivo del reporte (opcional):"), _("Reportar mensaje"))
        motivo = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        acc, room, ev = self.account_id, self.room_id, m.get("event_id") or ""

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.reportar_evento(acc, room, ev, motivo)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Reporte enviado a los administradores.") if not e
            else _("No se pudo reportar: {0}").format(e)), pass_context=True)

    def _copiar_msg(self, m):
        try:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(f"<{m['quien']}> {m['texto']}"))
                wx.TheClipboard.Close()
                speak(_("Copiado."), interrupt=True)
        except Exception:
            pass

    def _mencionar(self, quien):
        self.txt_mensaje.SetValue(f"{quien}: " + self.txt_mensaje.GetValue())
        self.txt_mensaje.SetInsertionPointEnd()
        self.txt_mensaje.SetFocus()

    # ---------------- Lectura y cierre ----------------
    def _marcar_leida_local(self):
        try:
            db.execute("UPDATE matrix_salas SET unread_count = 0 WHERE account_id = ? AND room_id = ?",
                       (self.account_id, self.room_id))
            db.execute("UPDATE matrix_mensajes SET leido = 1 WHERE account_id = ? AND room_id = ?",
                       (self.account_id, self.room_id))
        except Exception:
            pass

    def _on_cerrar(self, event):
        try:
            event_bus.unsubscribe("matrix_evento", self._on_evento_matrix)
        except Exception:
            pass
        self._marcar_leida_local()
        # Marca de lectura también en el SERVIDOR (otros clientes verán la sala leída).
        acc, room = self.account_id, self.room_id

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.marcar_leida_servidor(acc, room)
            return True
        task_manager.start_task(_bg, pass_context=True)
        self.EndModal(wx.ID_CANCEL)
