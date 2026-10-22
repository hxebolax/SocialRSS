# -*- coding: utf-8 -*-
"""Conversación Telegram: encuestas, programados, IA, traducción y envío (mixin).
"""
import logging
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service
from .accessible_controls import AccessibleTextCtrl, confirmar, mostrar_info


log = logging.getLogger(__name__)


class ProgramarMensajeDialog(wx.Dialog):
    """Un ÚNICO diálogo para programar un mensaje: el texto y, debajo, la fecha (día, mes y año) y la
    hora (hora y minuto) en campos numéricos independientes, con el mismo estilo accesible que el
    selector de fechas de la Búsqueda. Devuelve (texto, datetime) con obtener()."""

    def __init__(self, parent, texto_inicial=""):
        super().__init__(parent, title=_("Programar mensaje"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Programar mensaje"))
        from datetime import datetime, timedelta
        # Por defecto, dentro de 5 minutos (Telegram exige una fecha futura).
        inicial = datetime.now() + timedelta(minutes=5)

        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Texto del mensaje:")), 0, wx.LEFT | wx.TOP, 8)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE,
                                      name=_("Texto del mensaje a programar"),
                                      ayuda_texto=_("Escribe el mensaje. Luego elige la fecha y la hora de envío."))
        self.txt.SetValue(texto_inicial or "")
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 8)

        # Fecha (día/mes/año): se reutiliza el MISMO componente de la Búsqueda.
        from .telegram_busqueda import _CamposFecha
        self._fecha = _CamposFecha(self, s, _("Fecha de envío:"), hoy=inicial.date())

        # Hora y minuto: campos numéricos independientes (como día/mes/año), agrupados para que el
        # lector anuncie "Hora de envío" al entrar en el primer campo.
        box = wx.StaticBox(self, label=_("Hora de envío"))
        bs = wx.StaticBoxSizer(box, wx.HORIZONTAL)
        bs.Add(wx.StaticText(box, label=_("Hora:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
        self.hora = wx.SpinCtrl(box, min=0, max=23, initial=inicial.hour,
                                name=_("Hora de envío, hora"))
        bs.Add(self.hora, 0, wx.RIGHT, 6)
        bs.Add(wx.StaticText(box, label=_("Minuto:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.minuto = wx.SpinCtrl(box, min=0, max=59, initial=inicial.minute, name=_("Minuto"))
        bs.Add(self.minuto, 0)
        s.Add(bs, 0, wx.ALL | wx.EXPAND, 8)

        self._resultado = (None, None)
        s.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizerAndFit(s)
        self.SetSize((540, 460))
        self.CenterOnParent()
        # Validar en Aceptar SIN cerrar si algo está mal (así no se pierde lo escrito).
        self.Bind(wx.EVT_BUTTON, self._on_aceptar, id=wx.ID_OK)
        self.txt.SetFocus()

    def _on_aceptar(self, event):
        from datetime import datetime
        texto = self.txt.GetValue().strip()
        if not texto:
            speak(_("Escribe el texto del mensaje."), interrupt=True)
            self.txt.SetFocus()
            return
        fecha = self._fecha.valor()
        if fecha is None:
            speak(_("La fecha no es válida."), interrupt=True)
            return
        cuando = datetime(fecha.year, fecha.month, fecha.day,
                          self.hora.GetValue(), self.minuto.GetValue())
        if cuando <= datetime.now():
            speak(_("La fecha y hora deben ser futuras. Elige una fecha y hora posteriores a ahora."),
                  interrupt=True)
            return
        self._resultado = (texto, cuando)
        self.EndModal(wx.ID_OK)

    def obtener(self):
        """Devuelve (texto, datetime) ya validados (solo tras Aceptar)."""
        return self._resultado


class _ConvExtrasMixin:
    def _on_ver_mensaje(self, meta):
        """Abre el diálogo «Ver mensaje» con detalles, adjuntos y respuestas del mensaje."""
        from .telegram_mensaje_dialog import TelegramMensajeDialog
        dlg = TelegramMensajeDialog(self, self.account_id, self.chat_id, meta)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_crear_encuesta(self):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Pregunta de la encuesta:"), _("Crear encuesta"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        pregunta = dlg.GetValue().strip()
        dlg.Destroy()
        if not pregunta:
            return
        dlg2 = CustomTextEntryDialog(
            self, _("Opciones (una por línea, mínimo 2):"), _("Crear encuesta"),
            value="", multiline=True)
        if dlg2.ShowModal() != wx.ID_OK:
            dlg2.Destroy(); return
        opciones = [o.strip() for o in dlg2.GetValue().splitlines() if o.strip()]
        dlg2.Destroy()
        if len(opciones) < 2:
            speak(_("Hacen falta al menos dos opciones."), interrupt=True)
            return
        multiple = wx.MessageBox(
            _("¿Permitir varias respuestas?"), _("Encuesta"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES
        anonima = wx.MessageBox(
            _("¿Encuesta anónima (sin ver quién vota)?"), _("Encuesta"),
            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self) == wx.YES
        self._accion_simple(
            lambda: telegram_service.crear_encuesta(
                self.account_id, self.chat_id, pregunta, opciones,
                anonima=anonima, multiple=multiple),
            _("Encuesta enviada."), _("Enviando encuesta..."))

    def _on_enviar_checklist(self):
        """Envía una lista de tareas (checklist): título + una tarea por línea."""
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Título de la lista de tareas:"), _("Lista de tareas"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        titulo = dlg.GetValue().strip()
        dlg.Destroy()
        if not titulo:
            return
        dlg2 = CustomTextEntryDialog(
            self, _("Tareas (una por línea, de 1 a 30):"), _("Lista de tareas"),
            value="", multiline=True)
        if dlg2.ShowModal() != wx.ID_OK:
            dlg2.Destroy(); return
        tareas = [t.strip() for t in dlg2.GetValue().splitlines() if t.strip()]
        dlg2.Destroy()
        if not tareas:
            speak(_("Hace falta al menos una tarea."), interrupt=True)
            return
        otros = wx.MessageBox(
            _("¿Permitir que los demás marquen tareas como hechas?"), _("Lista de tareas"),
            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION, self) == wx.YES
        self._accion_simple(
            lambda: telegram_service.enviar_checklist(
                self.account_id, self.chat_id, titulo, tareas, otros_pueden_marcar=otros),
            _("Lista de tareas enviada."), _("Enviando lista de tareas..."))

    def _on_enviar_ubicacion(self):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self, _("Latitud y longitud separadas por coma (ej. 40.4168, -3.7038):"),
            _("Enviar ubicación"))
        ok = dlg.ShowModal() == wx.ID_OK
        valor = dlg.GetValue().strip() if ok else ""
        dlg.Destroy()
        if not valor:
            return
        try:
            lat, lon = [float(p.strip()) for p in valor.split(",")[:2]]
        except (ValueError, IndexError):
            speak(_("Coordenadas no válidas."), interrupt=True)
            return
        self._accion_simple(
            lambda: telegram_service.enviar_ubicacion(self.account_id, self.chat_id, lat, lon),
            _("Ubicación enviada."), _("Enviando ubicación..."))

    def _on_enviar_contacto(self):
        from .accessible_controls import CustomTextEntryDialog
        d1 = CustomTextEntryDialog(self, _("Teléfono del contacto:"), _("Enviar contacto"))
        tel = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
        d1.Destroy()
        if not tel:
            return
        d2 = CustomTextEntryDialog(self, _("Nombre del contacto:"), _("Enviar contacto"))
        nombre = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if not nombre:
            return
        self._accion_simple(
            lambda: telegram_service.enviar_contacto(self.account_id, self.chat_id, tel, nombre),
            _("Contacto enviado."), _("Enviando contacto..."))

    def _on_enviar_videonota(self):
        ruta = self._elegir_archivo(_("Elija el vídeo (se enviará como vídeo-nota redonda)"),
                                    "*.mp4;*.mov;*.avi")
        if ruta:
            self._accion_simple(
                lambda: telegram_service.enviar_videonota(self.account_id, self.chat_id, ruta),
                _("Vídeo-nota enviada."), _("Enviando vídeo-nota..."))

    def _on_enviar_gif(self):
        ruta = self._elegir_archivo(_("Elija el GIF o vídeo corto"), "*.gif;*.mp4;*.webm")
        if ruta:
            self._accion_simple(
                lambda: telegram_service.enviar_gif(self.account_id, self.chat_id, ruta),
                _("GIF enviado."), _("Enviando GIF..."))

    def _on_enviar_dado(self):
        emojis = ["🎲", "🎯", "🏀", "⚽", "🎰", "🎳"]
        dlg = wx.SingleChoiceDialog(self, _("Elija el emoji animado:"), _("Enviar dado"), emojis)
        if dlg.ShowModal() == wx.ID_OK:
            emoji = dlg.GetStringSelection()
            self._accion_simple(
                lambda: telegram_service.enviar_dado(self.account_id, self.chat_id, emoji),
                _("Enviado."), None)
        dlg.Destroy()

    def _elegir_archivo(self, titulo, comodin):
        dlg = wx.FileDialog(self, titulo, wildcard=f"{titulo}|{comodin}|{_('Todos')}|*.*",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        ruta = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        return ruta

    def _on_programar_mensaje(self):
        # Un solo diálogo: texto + fecha (día/mes/año) + hora (hora/minuto), estilo Búsqueda. Se
        # precarga con lo que ya hubieras escrito en el cuadro de la conversación.
        dlg = ProgramarMensajeDialog(self, texto_inicial=self.txt_mensaje.GetValue().strip())
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            texto, cuando = dlg.obtener()
        finally:
            dlg.Destroy()
        if not texto or cuando is None:
            return
        def _bg(ctx):
            return telegram_service.enviar_mensaje(self.account_id, self.chat_id, texto, schedule_date=cuando)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo programar: {0}").format(err))
            else:
                self.txt_mensaje.Clear()
                notificar(_("Mensaje programado."))
        speak(_("Programando mensaje..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_ver_programados(self):
        def _bg(ctx):
            return telegram_service.obtener_programados(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("No hay mensajes programados."))
                return
            self._gestionar_programados(res)
        speak(_("Consultando mensajes programados..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _gestionar_programados(self, programados):
        """Lista los mensajes programados y permite enviar ahora o cancelar el elegido."""
        opciones = [_("{0} — {1}").format(m["date"], m["text"] or _("(sin texto)"))
                    for m in programados]
        sel = wx.GetSingleChoice(
            _("Mensajes programados ({0}). Elige uno para gestionarlo:").format(len(programados)),
            _("Mensajes programados"), opciones, self)
        if not sel:
            return
        m = programados[opciones.index(sel)]
        acciones = [_("Enviar ahora"), _("Cancelar este mensaje programado"), _("No hacer nada")]
        a = wx.GetSingleChoice(
            _("Mensaje «{0}». ¿Qué quieres hacer?").format(m["text"] or _("(sin texto)")),
            _("Mensaje programado"), acciones, self)
        if a == acciones[0]:
            self._accion_programado(m["message_id"], "enviar")
        elif a == acciones[1]:
            if confirmar(self, _("¿Cancelar este mensaje programado? No llegará a enviarse."),
                         _("Cancelar programado")):
                self._accion_programado(m["message_id"], "cancelar")

    def _accion_programado(self, message_id, accion):
        def _bg(ctx):
            if accion == "enviar":
                return telegram_service.enviar_programado_ahora(self.account_id, self.chat_id, message_id)
            return telegram_service.cancelar_programado(self.account_id, self.chat_id, message_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            elif accion == "enviar":
                notificar(_("Mensaje enviado."))
                self._descargar_historial()
            else:
                notificar(_("Mensaje programado cancelado."))
        speak(_("Aplicando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_descargar_chat(self):
        """Descarga en lote todo el contenido (o por tipo) del chat, con progreso."""
        categorias = [
            ("photo", _("Fotos")), ("video", _("Vídeos")), ("animation", _("GIFs")),
            ("voice", _("Notas de voz")), ("audio", _("Audios")),
            ("video_note", _("Videomensajes")), ("document", _("Documentos")),
        ]
        dlg = wx.MultiChoiceDialog(
            self, _("¿Qué quieres descargar? (no marcar nada descarga TODO)"),
            _("Descargar contenido del chat"), [etq for _c, etq in categorias])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        seleccion = dlg.GetSelections()
        dlg.Destroy()
        tipos = [categorias[i][0] for i in seleccion] or None

        progreso = wx.ProgressDialog(
            _("Descargando contenido"), _("Buscando contenido..."), maximum=100,
            parent=self, style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE)

        def _prog(hecho, total):
            if total <= 0:
                return
            progreso.SetRange(total)
            progreso.Update(min(hecho, total),
                            _("Descargando {0} de {1}...").format(hecho, total))

        def _bg(ctx):
            return telegram_service.descargar_chat_masivo(
                self.account_id, self.chat_id, tipos=tipos, progreso=_prog)

        def _finish(res, err):
            try:
                progreso.Destroy()
            except Exception:
                pass
            if err:
                notificar(_("Error en la descarga: {0}").format(err))
                return
            if not res or res.get("total", 0) == 0:
                mostrar_info(self, _("Este chat no tiene contenido descargable con ese filtro."),
                             _("Descargar contenido"))
                return
            notificar(_("Descargados {0} de {1} archivos en:\n{2}").format(
                res["descargados"], res["total"], res["carpeta"]))
        speak(_("Buscando contenido para descargar..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_reenviar_guardados(self, meta):
        mid = meta["message_id"]
        def _bg(ctx):
            return telegram_service.reenviar_a_guardados(self.account_id, self.chat_id, mid)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo reenviar: {0}").format(err))
            else:
                notificar(_("Mensaje reenviado a Mensajes guardados."))
        speak(_("Reenviando a Mensajes guardados..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_traducir_nativo(self, meta):
        mid = meta["message_id"]
        def _bg(ctx):
            return telegram_service.traducir_mensaje_nativo(self.account_id, self.chat_id, mid, "es")
        def _finish(res, err):
            if err:
                notificar(_("No se pudo traducir: {0}").format(err))
            elif res:
                self._mostrar_texto(_("Traducción"), res)
            else:
                speak(_("Sin traducción."), interrupt=True)
        speak(_("Traduciendo..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_leer_tts_msg(self, meta):
        texto = (meta.get("text") or "").strip()
        if not texto:
            speak(_("No hay texto que leer."), interrupt=True)
            return
        from .tts_dialog import TTSReaderDialog
        dlg = TTSReaderDialog(self, _("Mensaje"), texto)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_traducir_app(self, meta):
        """Traduce el mensaje con el traductor de la app (igual que en Mastodon)."""
        texto = meta.get("text") or ""
        if not texto:
            return
        from app.services.translation.translation_service import translation_service
        from app.core.config_manager import config
        def _bg(ctx):
            target = config.get("translation", "default_target", "es")
            return translation_service.traducir(texto, idioma_destino=target)
        def _finish(res, err):
            if err:
                notificar(_("Error de traducción: {0}").format(err))
                return
            if config.get("translation", "copy_on_translate", True):
                if wx.TheClipboard.Open():
                    wx.TheClipboard.SetData(wx.TextDataObject(res))
                    wx.TheClipboard.Close()
            self._mostrar_texto(_("Traducción"), res)
        speak(_("Traduciendo..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_resumir_ia(self, meta):
        """Resume el mensaje con IA (igual que en Mastodon)."""
        texto = meta.get("text") or ""
        if not texto:
            return
        from app.services.ai.ai_service import ai_service
        def _bg(ctx):
            return ai_service.resumir_texto(texto)
        def _finish(res, err):
            if err:
                notificar(_("Error de la IA: {0}").format(err))
            else:
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Resumen de IA"), res, self._mostrar_texto)
        speak(_("Generando resumen con IA..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_describir_ia(self, meta):
        """Descarga la imagen del mensaje y la describe con IA (igual que en Mastodon)."""
        from app.services.ai.ai_service import ai_service
        mid = meta["message_id"]
        def _bg(ctx):
            ruta = telegram_service.descargar_media(self.account_id, self.chat_id, mid)
            if not ruta:
                raise RuntimeError(_("No se pudo descargar la imagen."))
            return ai_service.describir_imagen(ruta)
        def _finish(res, err):
            if err:
                notificar(_("Error de la IA: {0}").format(err))
            else:
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Descripción de IA"), res, self._mostrar_texto)
        speak(_("Analizando la imagen con IA..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_enviar_sticker_set(self):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nombre del paquete de stickers (sin enlace):"), _("Enviar sticker"))
        nombre = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not nombre:
            return
        # Extraer el short_name si pegaron un enlace t.me/addstickers/NOMBRE
        if "/" in nombre:
            nombre = nombre.rstrip("/").split("/")[-1]
        def _bg(ctx):
            return telegram_service.obtener_stickers(self.account_id, nombre)
        def _finish(res, err):
            if err:
                notificar(_("No se encontró el paquete: {0}").format(err))
                return
            if not res:
                notificar(_("Paquete vacío."))
                return
            etiquetas = [f"{s['emoji']} ({i+1})" for i, s in enumerate(res)]
            dlg = wx.SingleChoiceDialog(self, _("Elija un sticker:"), _("Stickers"), etiquetas)
            if dlg.ShowModal() == wx.ID_OK:
                fid = res[dlg.GetSelection()]["file_id"]
                self._accion_simple(
                    lambda: telegram_service.enviar_sticker(self.account_id, self.chat_id, fid),
                    _("Sticker enviado."), _("Enviando sticker..."))
            dlg.Destroy()
        speak(_("Cargando stickers..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_chats_comun(self):
        def _bg(ctx):
            return telegram_service.chats_en_comun(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            elif not res:
                speak(_("No hay chats en común."), interrupt=True)
            else:
                self._mostrar_texto(_("Chats en común ({0})").format(len(res)),
                                    "\n".join(c["titulo"] for c in res))
        speak(_("Consultando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_potenciar(self):
        def _bg(ctx):
            return telegram_service.potenciar_canal(self.account_id, self.chat_id)
        def _finish(res, err):
            notificar(_("No se pudo potenciar: {0}").format(err) if err else _("Canal potenciado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_estado_boosts(self):
        def _bg(ctx):
            return telegram_service.estado_boosts(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            msg = _("Nivel {0}, {1} boosts. Siguiente nivel: {2}.").format(
                res["nivel"], res["boosts"], res["siguiente_nivel"])
            if res["mi_boost"]:
                msg += " " + _("Ya lo potencias.")
            speak(msg, interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_bloquear_usuario(self):
        bloquear = wx.MessageBox(
            _("¿Bloquear a este usuario? (Elija No para desbloquear)"), _("Bloquear usuario"),
            wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
        if bloquear == wx.CANCEL:
            return
        accion = (telegram_service.bloquear_usuario if bloquear == wx.YES
                  else telegram_service.desbloquear_usuario)
        def _bg(ctx):
            return accion(self.account_id, self.chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Usuario bloqueado.") if bloquear == wx.YES else _("Usuario desbloqueado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _mostrar_texto(self, titulo, texto):
        """Muestra un texto largo en un visor accesible de solo lectura."""
        dlg = wx.Dialog(self, title=titulo, size=(640, 460),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
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

    def _on_escribiendo(self, event):
        """Envía el indicador 'escribiendo…' al otro extremo, como mucho cada 4 s."""
        event.Skip()
        import time
        ahora = time.monotonic()
        if ahora - self._ultima_accion_typing < 4.0:
            return
        if not self.txt_mensaje.GetValue().strip():
            return
        self._ultima_accion_typing = ahora
        def _bg(ctx):
            try:
                telegram_service.enviar_accion_chat(self.account_id, self.chat_id, "typing")
            except Exception as e:
                log.debug(f"send_chat_action: {e}")
        task_manager.start_task(_bg, pass_context=True)

    def _marcar_menciones_reacciones_leidas(self):
        """Marca como leídas menciones y reacciones pendientes del chat (en segundo plano)."""
        def _bg(ctx):
            try:
                telegram_service.marcar_menciones_leidas(self.account_id, self.chat_id)
            except Exception as e:
                log.debug(f"read_mentions: {e}")
            try:
                telegram_service.marcar_reacciones_leidas(self.account_id, self.chat_id)
            except Exception as e:
                log.debug(f"read_reactions: {e}")
        task_manager.start_task(_bg, pass_context=True)

    def _on_enviar(self, event):
        texto = self.txt_mensaje.GetValue().strip()
        if not texto:
            return

        reply_to = self._reply_to

        def _bg(ctx):
            return telegram_service.enviar_mensaje(self.account_id, self.chat_id, texto, reply_to=reply_to)

        def _finish(res, err):
            if err:
                notificar(_("Error al enviar: {0}").format(err))
            else:
                self.txt_mensaje.Clear()
                self._reply_to = None
                self._descartar_borrador_local()
                self._cargar_mensajes_cache()
                notificar(_("Mensaje enviado."))

        speak(_("Enviando mensaje..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _descartar_borrador_local(self):
        """Elimina el borrador local de este chat (Telegram ya lo borra en el servidor al enviar)."""
        from app.core.config_manager import config
        drafts = dict(config.get("telegram", "drafts", {}) or {})
        if drafts.pop(self._clave_borrador(), None) is not None:
            config.set("telegram", "drafts", drafts)
