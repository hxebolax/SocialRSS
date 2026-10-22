# -*- coding: utf-8 -*-
"""Conversación Telegram: descargas, adjuntos, presencia y barra de acciones (mixin).
"""
import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager
from app.core.config_manager import config
from app.services.telegram.telegram_service import telegram_service
from .telegram_dialogs_admin import TelegramAdminDialog, TelegramPropiedadesDialog


class _ConvAccionesMixin:
    def _on_descargar(self, meta, reproducir=False):
        mid = meta["message_id"]
        fila = self._fila_de_mensaje(mid)
        speak(_("Descargando archivo..."), interrupt=True)
        # Estado para limitar los anuncios de voz (cada 25%) sin saturar al lector.
        ultimo = {"anunciado": 0}

        def _progreso(current, total):
            # Se ejecuta en el hilo de la UI (descargar_media usa wx.CallAfter).
            if not total:
                return
            pct = int(current * 100 / total)
            self._actualizar_progreso_fila(fila, pct)
            if pct >= ultimo["anunciado"] + 25 and pct < 100:
                ultimo["anunciado"] = pct - (pct % 25)
                speak(_("{0} por ciento").format(pct), interrupt=True)

        def _bg(ctx):
            return telegram_service.descargar_media(self.account_id, self.chat_id, mid, progreso=_progreso)

        def _finish(res, err):
            self._actualizar_progreso_fila(fila, 100)
            if err:
                notificar(_("Error al descargar: {0}").format(err))
            elif res:
                self._ultima_descarga = res
                meta["media_path"] = res
                meta["descargado"] = True
                # Reflejar 'Descargado' en la columna de estado (si la plantilla la muestra).
                if hasattr(self, "_refrescar_descarga_fila"):
                    self._refrescar_descarga_fila(fila, True)
                speak(_("Archivo descargado: {0}").format(res), interrupt=True)
                if reproducir:
                    from app.services.media_player.player_service import player_service
                    def _bg2(ctx2):
                        player_service.cargar_y_reproducir(res, meta.get("text") or "Telegram", service_origin="telegram", account_origin_id=self.account_id)
                        return True
                    task_manager.start_task(_bg2, pass_context=True)
            else:
                speak(_("Este mensaje no tiene archivo descargable."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_adjuntar(self, event):
        """Abre el diálogo para elegir UNO O VARIOS archivos, ponerle a cada uno su comentario, y los
        envía mostrando el progreso de subida en la propia lista de mensajes."""
        from .telegram_envio import TelegramEnviarArchivosDialog
        caption_inicial = self.txt_mensaje.GetValue().strip()
        dlg = TelegramEnviarArchivosDialog(self, caption_inicial=caption_inicial)
        if dlg.ShowModal() == wx.ID_OK and dlg.items:
            items = list(dlg.items)
            self.txt_mensaje.Clear()   # el texto ya viajó como comentario del primer archivo
            self._enviar_archivos_con_progreso(items)
        dlg.Destroy()

    def _enviar_archivos_con_progreso(self, items):
        """Envía los archivos de uno en uno insertando en la lista una fila provisional por archivo
        ("Subiendo X — N %") que se va actualizando con el porcentaje real de subida. Al terminar,
        recarga el caché para consolidar los mensajes reales y deja el foco en el último enviado."""
        import os
        # 1) Insertar YA las filas provisionales (en el hilo de UI) y quedarnos con sus índices.
        self._subida_hitos = {}
        filas = []
        for it in items:
            nombre = os.path.basename(it["ruta"])
            filas.append(self._insertar_fila_subida(nombre))
        n = len(items)
        speak(_("Enviando {0} archivo(s)...").format(n), interrupt=True)

        def _hacer_cb(idx, nombre):
            # Se ejecuta en el bucle de Telegram: solo dispara actualizaciones ligeras a la UI.
            estado = {"pct": -1}
            def cb(current, total):
                pct = int(current * 100 / total) if total else 0
                if pct != estado["pct"]:
                    estado["pct"] = pct
                    wx.CallAfter(self._actualizar_fila_subida, idx, nombre, pct)
            return cb

        def _bg(ctx):
            for it, idx in zip(items, filas):
                nombre = os.path.basename(it["ruta"])
                telegram_service.enviar_archivo(
                    self.account_id, self.chat_id, it["ruta"], caption=it.get("caption", ""),
                    tipo=it.get("tipo", "auto"), progress_cb=_hacer_cb(idx, nombre))
            return n

        def _finish(res, err):
            if not self:
                return
            if err:
                notificar(_("Error al enviar: {0}").format(err))
            self._cargar_mensajes_cache()
            total = self.lista_msgs.GetItemCount()
            if total > 0:
                self.lista_msgs.Focus(total - 1)
                self.lista_msgs.Select(total - 1)
                self.lista_msgs.EnsureVisible(total - 1)
            if not err:
                notificar(_("{0} archivo(s) enviado(s).").format(res or n))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _insertar_fila_subida(self, nombre):
        """Inserta al final de la lista una fila provisional de subida y devuelve su índice. No mueve
        el foco (actualización silenciosa)."""
        idx = self.lista_msgs.GetItemCount()
        texto = _("⬆ Subiendo {0} — 0 %").format(nombre)
        valores = {"author": _("Tú"), "content": texto}
        pos = self._volcar_fila_msg(idx, valores)
        self.lista_msgs.establecer_lectura(pos, _("Subiendo {0}, 0 por ciento").format(nombre))
        # Marcador en msg_rows para mantener la correspondencia índice↔fila con el resto de código.
        self.msg_rows.append({"message_id": None, "_subiendo": True})
        return pos

    def _actualizar_fila_subida(self, idx, nombre, pct):
        """Refresca el porcentaje de una fila de subida. Silencioso salvo hitos de 25 % (para que se
        oiga cuánto queda sin inundar al lector)."""
        if not self:
            return
        if not (0 <= idx < self.lista_msgs.GetItemCount()):
            return
        col = self._col_msg("content")
        if col is not None:
            self.lista_msgs.SetItem(idx, col, _("⬆ Subiendo {0} — {1} %").format(nombre, pct))
        self.lista_msgs.establecer_lectura(idx, _("Subiendo {0}, {1} por ciento").format(nombre, pct))
        hito = (pct // 25) * 25
        if 0 < hito < 100 and hito != self._subida_hitos.get(idx):
            self._subida_hitos[idx] = hito
            speak(_("{0}, {1} por ciento").format(nombre, hito), interrupt=False)

    # --- ESTADO DE PRESENCIA EN EL TÍTULO (en línea, escribiendo, última conexión) ---
    def _componer_titulo(self):
        """Compone el título con el nombre del chat y, si lo hay, la acción transitoria
        (escribiendo…) o el estado de presencia (En línea / Última conexión…)."""
        base = _("Conversación: {0}").format(self.chat_title)
        extra = self._accion_transitoria or self._estado_presencia
        return f"{base} — {extra}" if extra else base

    def _refrescar_titulo(self):
        """Aplica el título compuesto. Seguro desde cualquier hilo vía wx.CallAfter."""
        def _aplicar():
            try:
                self.SetTitle(self._componer_titulo())
            except RuntimeError:
                pass  # la ventana ya se cerró
        wx.CallAfter(_aplicar)

    def _cargar_estado_inicial(self):
        """Para chats privados/bots, consulta el estado actual (en línea / última
        conexión) en segundo plano y lo pone en el título nada más abrir, ya que los
        eventos en tiempo real solo llegan cuando el estado CAMBIA."""
        if self.chat_type not in ("private", "bot"):
            return

        def _bg(ctx):
            return telegram_service.obtener_estado_usuario(self.account_id, self.chat_id)

        def _finish(res, err):
            if not err and res:
                self._estado_presencia = res
                self._refrescar_titulo()

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_presencia_estado(self, account_id, user_id, estado):
        """Cambio de estado en línea del interlocutor (solo en chats privados/bots)."""
        if account_id != self.account_id or user_id != self.chat_id:
            return
        self._estado_presencia = estado or ""
        self._refrescar_titulo()

    def _on_presencia_typing(self, account_id, chat_id, descripcion):
        """El interlocutor está escribiendo / grabando voz, etc. Se muestra de forma
        transitoria en el título y se vuelve al estado de presencia tras unos segundos."""
        if account_id != self.account_id or chat_id != self.chat_id:
            return
        self._accion_transitoria = _("está {0}").format(descripcion)
        self._refrescar_titulo()
        # Programar el borrado de la acción transitoria en el hilo de la UI.
        wx.CallAfter(self._programar_fin_typing)

    def _programar_fin_typing(self):
        if not self:
            return
        # Reinicia el temporizador en cada actividad: la acción dura ~6 s sin novedades.
        if getattr(self, "_typing_timer", None):
            self._typing_timer.Stop()
        self._typing_timer = wx.CallLater(6000, self._limpiar_typing)

    def _limpiar_typing(self):
        self._accion_transitoria = ""
        self._refrescar_titulo()

    def _on_cerrar_conv(self, event):
        # Persistir el estado de leído: marcar leídos hasta donde llegaste y dejar el resto sin leer.
        try:
            self._persistir_leido_al_salir()
        except Exception:
            pass
        # Cortar la cola de autorreproducción de notas de voz si estaba encadenando.
        try:
            self._autoplay_detener_cadena()
        except Exception:
            pass
        # Dejar de escuchar los eventos de presencia y de mensajes en vivo de este chat.
        event_bus.unsubscribe("telegram_user_status", self._on_presencia_estado)
        event_bus.unsubscribe("telegram_typing", self._on_presencia_typing)
        event_bus.unsubscribe("telegram_new_message", self._on_mensaje_tiempo_real)
        event_bus.unsubscribe("telegram_message_edited", self._on_mensaje_editado_tr)
        event_bus.unsubscribe("telegram_messages_deleted", self._on_mensajes_borrados_tr)
        if getattr(self, "_typing_timer", None):
            try:
                self._typing_timer.Stop()
            except Exception:
                pass
        # Si hay una grabación de voz en curso, cancelarla al cerrar
        if getattr(self, "_grabando", False):
            try:
                telegram_service.cancelar_grabacion_voz()
            except Exception:
                pass
        # Guardar (o limpiar) el borrador del chat si quedó texto sin enviar.
        self._guardar_borrador()
        event.Skip()

    # --- BORRADORES POR CHAT ---
    def _clave_borrador(self):
        return f"{self.account_id}:{self.chat_id}"

    def _restaurar_borrador(self):
        """Si este chat tenía un borrador guardado, lo recupera en el campo de escritura."""
        from app.core.config_manager import config
        drafts = config.get("telegram", "drafts", {}) or {}
        texto = drafts.get(self._clave_borrador(), "")
        if texto and not self.txt_mensaje.GetValue().strip():
            self.txt_mensaje.SetValue(texto)
            speak(_("Tienes un borrador guardado en este chat."), interrupt=False)

    def _guardar_borrador(self):
        """Guarda el borrador localmente y lo sincroniza con el servidor de Telegram.
        Si el campo está vacío, borra cualquier borrador previo."""
        from app.core.config_manager import config
        texto = self.txt_mensaje.GetValue().strip()
        drafts = dict(config.get("telegram", "drafts", {}) or {})
        clave = self._clave_borrador()
        cambia = False
        if texto:
            if drafts.get(clave) != texto:
                drafts[clave] = texto
                cambia = True
        elif clave in drafts:
            drafts.pop(clave, None)
            cambia = True
        if not cambia:
            return
        config.set("telegram", "drafts", drafts)
        # Sincronizar con Telegram en segundo plano (no bloquea el cierre).
        def _bg(ctx):
            try:
                telegram_service.guardar_borrador_servidor(self.account_id, self.chat_id, texto)
            except Exception:
                pass
        task_manager.start_task(_bg, pass_context=True)

    def _on_propiedades(self, event):
        dlg = TelegramPropiedadesDialog(self, self.account_id, self.chat_id, self.chat_title)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_exportar(self, event):
        dlg = wx.FileDialog(
            self, message=_("Exportar conversación como"),
            defaultFile="conversacion.txt",
            wildcard="Texto (*.txt)|*.txt|HTML (*.html)|*.html|JSON (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        )
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            import os
            ext = os.path.splitext(ruta)[1].lower().lstrip(".") or "txt"
            formato = ext if ext in ("txt", "html", "json") else "txt"
            def _bg(ctx):
                return telegram_service.exportar_chat(self.account_id, self.chat_id, ruta, formato=formato)
            def _finish(res, err):
                if err:
                    notificar(_("Error al exportar: {0}").format(err))
                else:
                    notificar(_("Conversación exportada."))
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_chat_voz(self, event):
        if not self._en_voz:
            # Entrar EN SILENCIO (reproduce un WAV mudo): es la operación soportada. Emitir el
            # micrófono es aparte (botón "Hablar por el micro"), porque en algunos equipos ntgcalls
            # no puede capturar el micro en vivo y eso NO debe impedir entrar a escuchar.
            speak(_("Entrando al chat de voz..."), interrupt=True)
            def _bg(ctx):
                return telegram_service.unirse_llamada_voz(self.account_id, self.chat_id)
            def _finish(res, err):
                if err:
                    notificar(_("No se pudo entrar al chat de voz: {0}").format(err))
                else:
                    self._en_voz = True
                    self._voz_silenciado = True   # entras callado, solo escuchando
                    self.btn_voz.SetLabel(_("Salir del chat de &voz"))
                    self.btn_mute.SetLabel(_("&Hablar por el micro"))
                    self.btn_mute.Enable()
                    speak(_("Estás en el chat de voz, escuchando. Pulsa Hablar por el micro para "
                            "intervenir."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        else:
            def _bg(ctx):
                telegram_service.salir_llamada_voz(self.account_id, self.chat_id)
                return True
            def _finish(res, err):
                self._en_voz = False
                self._voz_silenciado = False
                self.btn_voz.SetLabel(_("Entrar a chat de &voz"))
                self.btn_mute.SetLabel(_("&Hablar por el micro"))
                self.btn_mute.Disable()
                speak(_("Has salido del chat de voz."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_mute_voz(self, event):
        """Alterna entre HABLAR (emitir el micrófono) y CALLAR (volver al silencio). Si el equipo no
        puede capturar el micro, se avisa con claridad y sigues dentro escuchando."""
        if not self._en_voz:
            return
        hablar = self._voz_silenciado   # si estabas callado, ahora quieres hablar
        def _bg(ctx):
            if hablar:
                return telegram_service.hablar_microfono(self.account_id, self.chat_id)
            # Volver al silencio = reproducir de nuevo el WAV mudo (más fiable que mute/unmute).
            return telegram_service.unirse_llamada_voz(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                # Típicamente el micro no soportado: mensaje claro, seguimos dentro y callados.
                notificar(_("No se pudo activar el micrófono: {0}").format(err) if hablar
                          else _("Error: {0}").format(err))
                return
            self._voz_silenciado = not hablar
            self.btn_mute.SetLabel(_("&Silenciar micro") if hablar else _("&Hablar por el micro"))
            notificar(_("Micrófono activo. Estás hablando.") if hablar else _("Micrófono silenciado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_album(self, event):
        dlg = wx.FileDialog(self, message=_("Seleccionar fotos/vídeos para el álbum"),
                            style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST,
                            wildcard="Imágenes y vídeos|*.jpg;*.jpeg;*.png;*.gif;*.mp4;*.mkv;*.mov;*.webm")
        if dlg.ShowModal() == wx.ID_OK:
            rutas = dlg.GetPaths()
            caption = self.txt_mensaje.GetValue().strip()
            if len(rutas) < 2:
                speak(_("Seleccione al menos dos archivos para un álbum."), interrupt=True)
            else:
                speak(_("Enviando álbum..."), interrupt=True)
                def _bg(ctx):
                    return telegram_service.enviar_album(self.account_id, self.chat_id, rutas, caption=caption)
                def _finish(res, err):
                    if err:
                        notificar(_("Error al enviar el álbum: {0}").format(err))
                    else:
                        self.txt_mensaje.Clear()
                        self._cargar_mensajes_cache()
                        notificar(_("Álbum enviado."))
                task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_grabar_voz(self, event):
        if not self._grabando:
            def _bg(ctx):
                return telegram_service.iniciar_grabacion_voz()
            def _finish(res, err):
                if err:
                    notificar(_("No se pudo iniciar la grabación: {0}").format(err))
                else:
                    self._grabando = True
                    self.btn_grabar.SetLabel(_("&Detener y enviar nota de voz"))
                    if getattr(self, "btn_cancelar_voz", None):
                        self.btn_cancelar_voz.Enable()
                    speak(_("Grabando nota de voz. Pulse de nuevo para detener y enviar, o Cancelar "
                            "grabación para descartarla."), interrupt=True)
            speak(_("Iniciando grabación..."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        else:
            def _bg(ctx):
                return telegram_service.detener_grabacion_y_enviar(self.account_id, self.chat_id)
            def _finish(res, err):
                self._grabando = False
                self.btn_grabar.SetLabel(_("&Grabar nota de voz"))
                if getattr(self, "btn_cancelar_voz", None):
                    self.btn_cancelar_voz.Disable()
                if err:
                    notificar(_("Error al enviar la nota de voz: {0}").format(err))
                else:
                    self._cargar_mensajes_cache()
                    notificar(_("Nota de voz enviada."))
            speak(_("Deteniendo y enviando..."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_cancelar_voz(self, event):
        """Descarta la nota de voz que se está grabando, sin enviarla."""
        if not self._grabando:
            return
        self._grabando = False
        self.btn_grabar.SetLabel(_("&Grabar nota de voz"))
        if getattr(self, "btn_cancelar_voz", None):
            self.btn_cancelar_voz.Disable()
        def _bg(ctx):
            telegram_service.cancelar_grabacion_voz()
            return True
        def _finish(res, err):
            notificar(_("Grabación cancelada."))
        speak(_("Cancelando grabación..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _comprobar_es_admin(self):
        """Muestra el botón 'Administrar' solo si la cuenta es admin/dueño del chat (en segundo
        plano para no bloquear la apertura de la conversación)."""
        if not getattr(self, "btn_admin", None):
            return

        def _bg(ctx):
            return telegram_service.soy_administrador(self.account_id, self.chat_id)

        def _finish(res, err):
            if err or not res:
                return
            # Guardar el estado para el menú del mensaje (borrar ajenos, fijar...).
            self._es_admin = bool(res)
            # Confirmado admin: mostrar el botón y recolocar la botonera.
            try:
                if self.btn_admin:
                    self.btn_admin.Show()
                    self.btn_admin.GetParent().Layout()
            except RuntimeError:
                pass  # la ventana ya se cerró
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _comprobar_permiso_publicar(self):
        """Si en este chat NO se puede publicar (canal de solo lectura donde no eres admin, grupo
        restringido…), oculta la barra de redacción y los botones de publicar/grabar/adjuntar, para
        que la interfaz sea dinámica y no muestre acciones que no tienen sentido. En segundo plano."""
        # Los chats privados y bots siempre permiten escribir: no gastamos una consulta.
        if self.chat_type in ("private", "bot"):
            return

        def _bg(ctx):
            return telegram_service.puedo_publicar(self.account_id, self.chat_id)

        def _finish(res, err):
            if err:
                return
            # Guardar el estado real para el menú del mensaje (Responder solo si puedo publicar).
            self._puedo_publicar = bool(res)
            if res:
                return  # sí puede publicar: no tocar la interfaz
            # No se puede publicar: ocultar todo lo de redactar/enviar/grabar/adjuntar.
            for nombre in ("lbl_escribir", "txt_mensaje", "btn_enviar", "btn_adjuntar",
                           "btn_album", "btn_grabar", "btn_mas"):
                ctrl = getattr(self, nombre, None)
                if ctrl is not None:
                    try:
                        ctrl.Hide()
                    except Exception:
                        pass
            try:
                self.Layout()
                self.lista_msgs.SetFocus()
                speak(_("Este chat es de solo lectura."), interrupt=False)
            except Exception:
                pass
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_administrar(self, event):
        dlg = TelegramAdminDialog(self, self.account_id, self.chat_id, self.chat_title, self.chat_type)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_buscar(self, event):
        """Abre la búsqueda en una VENTANA APARTE (no mezcla los resultados con la conversación).
        Desde ahí se puede leer cada resultado o traerlo a la conversación."""
        from .telegram_busqueda import TelegramBusquedaChatDialog
        # Si se salió de la búsqueda con "Ir al mensaje", se reabre como estaba (texto, fechas,
        # resultados y el resultado en el que estabas), sin repetirla. Cerrar la olvida.
        dlg = TelegramBusquedaChatDialog(self, self.account_id, self.chat_id,
                                         getattr(self, "chat_title", ""), conversacion=self,
                                         estado=getattr(self, "_busqueda_guardada", None))
        dlg.ShowModal()
        mid = getattr(dlg, "mensaje_objetivo", None)
        try:
            self._busqueda_guardada = dlg.estado() if mid is not None else None
        except Exception:
            self._busqueda_guardada = None
        dlg.Destroy()
        # "Ir al mensaje": ya cerrado el modal y con el foco de vuelta aquí, situarlo en el mensaje.
        if mid is not None:
            wx.CallAfter(self._ir_a_message_id, mid)

    def _ir_a_message_id(self, mid):
        """Recarga la caché y deja el foco en el mensaje de ese message_id (tras traerlo por fecha
        desde la búsqueda). Si no aparece, avisa sin mover el foco."""
        if not self:
            return
        # El mensaje puede ser más antiguo que la ventana de mensajes cargada: se amplía
        # la ventana hasta abarcarlo (acotado por si el id no existe en la caché).
        fila = db.query_one(
            "SELECT COUNT(*) FROM telegram_messages "
            "WHERE account_id = ? AND chat_id = ? AND message_id >= ?",
            (self.account_id, self.chat_id, mid))
        necesarios = (fila[0] if fila else 0) or 0
        if necesarios > int(getattr(self, "_ventana_msgs", 0) or 0):
            self._ventana_msgs = necesarios + 50
        self._cargar_mensajes_cache()
        objetivo = next((i for i, m in enumerate(self.msg_rows)
                         if m.get("message_id") == mid), None)
        if objetivo is None:
            speak(_("No se pudo situar el mensaje en la conversación."), interrupt=True)
            return
        self.lista_msgs.Focus(objetivo)
        self.lista_msgs.Select(objetivo)
        self.lista_msgs.EnsureVisible(objetivo)
        self.lista_msgs.SetFocus()

    def _descargar_historial(self):
        # Si el usuario activó "Sincronizar todo el historial al abrir", se trae TODO el chat del
        # servidor (no solo lo que hubiera en la base de datos): así, aunque se hubiera vaciado o
        # borrado la base, vuelves a tener el historial completo. Solo se recorta si TÚ vacías el
        # chat a mano (y en la medida que Telegram lo permita). Por defecto va apagado, porque en
        # chats enormes traer todo puede tardar.
        todo = config.get("telegram", "sincronizar_todo_al_abrir", False)
        speak(_("Descargando todo el historial...") if todo else _("Descargando mensajes..."),
              interrupt=True)

        def _bg(ctx):
            # limit=0 = TODO el historial; si no, los últimos 50 (comportamiento clásico).
            return telegram_service.cargar_historial(self.account_id, self.chat_id,
                                                     limit=0 if todo else 50)

        def _finish(res, err):
            # El diálogo pudo cerrarse mientras se descargaba: si su objeto wx ya está destruido,
            # NO tocar la lista (evita "wrapped C/C++ object ... has been deleted").
            if not self:
                return
            if err:
                notificar(_("Error al cargar mensajes: {0}").format(err))
            else:
                self._cargar_mensajes_cache()
                self._marcar_menciones_reacciones_leidas()
                notificar(_("{0} mensajes del historial cargados.").format(res) if todo
                          else _("Mensajes actualizados."))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_mas_acciones(self, event):
        """Menú con las acciones adicionales de la Tanda 2."""
        menu = wx.Menu()
        acciones = [
            (_("Crear encuesta..."), self._on_crear_encuesta),
            (_("Enviar lista de tareas..."), self._on_enviar_checklist),
            (_("Enviar ubicación..."), self._on_enviar_ubicacion),
            (_("Enviar contacto..."), self._on_enviar_contacto),
            (_("Enviar vídeo-nota..."), self._on_enviar_videonota),
            (_("Enviar GIF..."), self._on_enviar_gif),
            (_("Enviar dado / emoji animado"), self._on_enviar_dado),
            (None, None),
            (_("Programar mensaje..."), self._on_programar_mensaje),
            (_("Ver mensajes programados..."), self._on_ver_programados),
            (None, None),
            (_("Multimedia y archivos del chat..."), self._on_medios_chat),
            (_("Ir a una fecha..."), self._on_ir_a_fecha),
            (_("Traer más mensajes antiguos (100)"), lambda e: self._on_mas_antiguos(100)),
            (_("Traer TODO el historial de mensajes (puede tardar)..."), lambda e: self._on_mas_antiguos(0)),
            (_("Descargar todo el contenido del chat..."), self._on_descargar_chat),
        ]
        acciones.append((None, None))
        acciones.append((_("Enviar sticker..."), self._on_enviar_sticker_set))
        # Acciones de BOTS: comandos e inicio solo para chats de bot; la consulta inline sirve para
        # cualquier chat (eliges el bot dentro del diálogo).
        acciones.append((None, None))
        if self.chat_type == "bot":
            acciones.append((_("Comandos del bot..."), self._on_comandos_bot))
            acciones.append((_("Iniciar bot (/start)..."), self._on_iniciar_bot))
        acciones.append((_("Consulta inline a un bot..."), self._on_consulta_inline))
        if self.chat_type == "private":
            acciones.append((_("Bloquear / desbloquear usuario"), self._on_bloquear_usuario))
            acciones.append((_("Chats en común"), self._on_chats_comun))
        if self.chat_type in ("group", "supergroup", "channel"):
            acciones.append((_("Enviar como (elegir perfil/canal)..."), self._on_enviar_como))
        if self.chat_type == "supergroup":
            acciones.append((_("Temas del foro (ver por tema)..."), self._on_temas_foro))
        if self.chat_type == "channel":
            acciones.append((_("Potenciar canal (boost)"), self._on_potenciar))
            acciones.append((_("Estado de boosts"), self._on_estado_boosts))
        for etiqueta, handler in acciones:
            if etiqueta is None:
                menu.AppendSeparator()
                continue
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, h=handler: h(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_temas_foro(self):
        """Lista los temas del foro y filtra la vista para mostrar SOLO los mensajes del tema elegido
        (o todos). Filtra lo que hay en la base; con 'Sincronizar todo el historial' tendrás el foro
        completo por temas. No descarga por tema (kurigram no lo permite en get_chat_history)."""
        speak(_("Cargando temas..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.obtener_temas(self.account_id, self.chat_id)
        def _finish(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los temas: {0}").format(err))
                return
            temas = res or []
            if not temas:
                speak(_("Este grupo no tiene temas de foro."), interrupt=True)
                return
            opciones = [_("Todos los temas")] + [(t.get("title") or _("(sin título)")) for t in temas]
            dlg = wx.SingleChoiceDialog(self, _("Elige un tema para verlo (o Todos):"),
                                        _("Temas del foro"), opciones)
            if dlg.ShowModal() == wx.ID_OK:
                sel = dlg.GetSelection()
                self._tema_filtro = None if sel == 0 else temas[sel - 1].get("id")
                self._cargar_mensajes_cache()
                nombre = _("todos los temas") if sel == 0 else (temas[sel - 1].get("title") or "")
                speak(_("Mostrando {0}.").format(nombre), interrupt=True)
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_enviar_como(self):
        """Elegir con qué perfil o canal publicar en este chat (Enviar como…)."""
        speak(_("Cargando perfiles disponibles..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.obtener_enviar_como(self.account_id, self.chat_id)
        def _finish(opciones, err):
            if err:
                notificar(_("No se pudo obtener la lista: {0}").format(err))
                return
            if not opciones:
                speak(_("No hay otros perfiles con los que publicar aquí."), interrupt=True)
                return
            etiquetas = [nombre for _id, nombre in opciones]
            dlg = wx.SingleChoiceDialog(self, _("Publicar en este chat como:"),
                                        _("Enviar como"), etiquetas)
            if dlg.ShowModal() == wx.ID_OK:
                send_as_id = opciones[dlg.GetSelection()][0]
                def _bg2(ctx):
                    return telegram_service.establecer_enviar_como(self.account_id, self.chat_id, send_as_id)
                def _finish2(res, err2):
                    if err2:
                        notificar(_("No se pudo cambiar: {0}").format(err2))
                    else:
                        notificar(_("A partir de ahora publicarás como {0}.").format(etiquetas[dlg.GetSelection()]))
                task_manager.start_task(_bg2, callback=_finish2, pass_context=True)
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_medios_chat(self):
        """Abre la multimedia/archivos/fijados del chat por tipo."""
        from .telegram_views import TelegramMediosChatDialog
        dlg = TelegramMediosChatDialog(self, self.account_id, self.chat_id, self.chat_title)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_ir_a_fecha(self):
        """Trae al caché los mensajes alrededor de una fecha para poder consultar conversaciones
        antiguas que aún no se habían descargado."""
        ids_previos = {m.get("message_id") for m in self.msg_rows}
        # Selector de fecha ACCESIBLE (campos día/mes/año), no un calendario desplegable.
        from .telegram_busqueda import pedir_fecha
        fecha = pedir_fecha(self, _("Ir a una fecha"), _("Elige la fecha a la que saltar:"))
        if fecha is None:
            return
        speak(_("Buscando mensajes de esa fecha..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.cargar_mensajes_desde_fecha(self.account_id, self.chat_id, fecha)
        def _finish(n, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            self._cargar_mensajes_cache()
            # Dejar el foco en el primer mensaje recién traído (el tramo de esa fecha). La marca de
            # no leídos no cuenta: no es un mensaje y no debe quedarse ella con el foco.
            objetivo = next((i for i, m in enumerate(self.msg_rows)
                             if not m.get("_separador") and m.get("message_id") not in ids_previos), 0)
            if self.msg_rows:
                self.lista_msgs.Focus(objetivo); self.lista_msgs.Select(objetivo)
                self.lista_msgs.EnsureVisible(objetivo); self.lista_msgs.SetFocus()
            speak(_("{0} mensajes traídos de esa fecha.").format(n or 0), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_mas_antiguos(self, limite):
        """Trae al caché mensajes MÁS ANTIGUOS que el más viejo que ya tienes (paginación hacia
        atrás). limite=0 = TODO el historial hasta el principio (se confirma porque puede tardar)."""
        ids = [m.get("message_id") for m in self.msg_rows if m.get("message_id")]
        if not ids:
            speak(_("Primero abre la conversación para tener alguna referencia."), interrupt=True)
            return
        mas_antiguo = min(ids)
        if limite == 0:
            from .accessible_controls import confirmar
            if confirmar(self, _("Traer TODO el historial de este chat puede tardar y descargar "
                                 "muchos mensajes. ¿Continuar?"), _("Traer todo el historial")) != wx.ID_YES:
                return
        ids_previos = set(ids)
        speak(_("Trayendo mensajes antiguos...") if limite else _("Trayendo todo el historial..."),
              interrupt=True)
        acc, chat = self.account_id, self.chat_id

        def _bg(ctx):
            return telegram_service.cargar_mas_antiguos(acc, chat, mas_antiguo, limit=limite)

        def _finish(n, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            self._cargar_mensajes_cache()
            # Dejar el foco en el primer mensaje nuevo (el más reciente de los recién traídos); la
            # marca de no leídos se salta, que no es un mensaje.
            objetivo = next((i for i, m in enumerate(self.msg_rows)
                             if not m.get("_separador") and m.get("message_id") not in ids_previos), 0)
            if self.msg_rows:
                self.lista_msgs.Focus(objetivo)
                self.lista_msgs.Select(objetivo)
                self.lista_msgs.EnsureVisible(objetivo)
                self.lista_msgs.SetFocus()
            notificar(_("{0} mensajes antiguos traídos.").format(n or 0))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _accion_simple(self, fn, ok_msg, enviando_msg=None):
        """Lanza una llamada al servicio en segundo plano con avisos hablados."""
        if enviando_msg:
            speak(enviando_msg, interrupt=True)
        def _bg(ctx):
            return fn()
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                self._cargar_mensajes_cache()
                speak(ok_msg, interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)
