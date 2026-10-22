# -*- coding: utf-8 -*-
"""Diálogo de conversación de Telegram: clase base (init y construcción de UI).
"""
import wx

from app.core.event_bus import event_bus
from app.core.database import db
from app.core.config_manager import config
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl
from .telegram_conv_mensajes import _ConvMensajesMixin
from .telegram_conv_acciones import _ConvAccionesMixin
from .telegram_conv_extras import _ConvExtrasMixin
from .telegram_conv_bots import _ConvBotsMixin


class TelegramConversationDialog(_ConvMensajesMixin, _ConvAccionesMixin, _ConvExtrasMixin, _ConvBotsMixin, wx.Dialog):
    def __init__(self, parent, account_id: int, chat_id: int, chat_title: str, chat_type: str = "private"):
        super().__init__(
            parent,
            title=_("Conversación: {0}").format(chat_title),
            size=(700, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Ventana de conversación de Telegram")
        )
        self.account_id = account_id
        self.chat_id = chat_id
        self.chat_title = chat_title
        self.chat_type = chat_type
        self._reply_to = None
        # Permisos de la cuenta EN ESTE chat, para que el menú del mensaje sea dinámico (no ofrecer
        # responder/borrar/fijar donde no se puede). Se confirman en segundo plano al abrir
        # (`_comprobar_es_admin` / `_comprobar_permiso_publicar`); aquí se inicializan de forma
        # optimista salvo en canales, donde por defecto solo publican/moderan los administradores.
        self._es_admin = False
        self._puedo_publicar = (chat_type != "channel")
        # Caché en memoria de los miembros del grupo para el autocompletado de @menciones (se carga una
        # sola vez, bajo demanda, al primer Ctrl+Espacio). None = aún no cargada.
        self._miembros_cache = None
        # Tema (topic) de foro por el que está filtrada la vista, o None = todos los temas.
        self._tema_filtro = None
        # Contenido protegido (noforwards): si el chat lo tiene, NO se puede reenviar ni copiar a otro
        # chat, así que esas opciones no se ofrecerán en el menú del mensaje.
        try:
            _fp = db.query_one("SELECT protected FROM telegram_chats WHERE id = ?",
                               (f"{account_id}:{chat_id}",))
            self._protegido = bool(_fp[0]) if _fp and _fp[0] else False
        except Exception:
            self._protegido = False
        # Estado de presencia mostrado en el título (En línea / Última conexión…) y
        # acción transitoria (escribiendo… / grabando un audio…).
        self._estado_presencia = ""
        self._accion_transitoria = ""
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, self._on_cerrar_conv)
        self.CenterOnParent()
        self._cargar_mensajes_cache()
        self._descargar_historial()
        # Estados en tiempo real: en línea, escribiendo, grabando voz, última conexión.
        # Llegan por el bus de eventos desde el hilo de Telegram, así que se aplican al
        # título con wx.CallAfter. El frame principal también los muestra en su barra de
        # estado, pero al ser esta ventana modal queda oculta; por eso van en el título.
        event_bus.subscribe("telegram_user_status", self._on_presencia_estado)
        event_bus.subscribe("telegram_typing", self._on_presencia_typing)
        # TIEMPO REAL: los mensajes nuevos de ESTE chat se añaden al instante a la lista, sin
        # reconstruir ni mover el foco (los entrega el handler del cliente de Telegram).
        event_bus.subscribe("telegram_new_message", self._on_mensaje_tiempo_real)
        # TIEMPO REAL: ediciones y borrados de mensajes de ESTE chat se reflejan al instante.
        event_bus.subscribe("telegram_message_edited", self._on_mensaje_editado_tr)
        event_bus.subscribe("telegram_messages_deleted", self._on_mensajes_borrados_tr)
        self._cargar_estado_inicial()
        self._restaurar_borrador()
        self._comprobar_es_admin()
        self._comprobar_permiso_publicar()

    def _on_mensaje_tiempo_real(self, account_id, chat_id):
        """(Hilo de Telegram) Llega un mensaje nuevo; si es de este chat, refrescar la lista."""
        try:
            if account_id == self.account_id and chat_id == self.chat_id:
                import wx as _wx
                _wx.CallAfter(self._insertar_mensajes_nuevos)
        except Exception:
            pass

    def _insertar_mensajes_nuevos(self):
        """Añade al final SOLO los mensajes que aún no están en la lista, sin reconstruir y sin
        mover el foco (salvo que ya estuvieras en el último, para leer el recién llegado)."""
        try:
            ids_actuales = {m["message_id"] for m in self.msg_rows}
            rows = db.query("""
                SELECT message_id, sender_name, sender_username, text, date, outgoing, media_type, media_path, duration, file_name, COALESCE(edited, 0), reply_to, COALESCE(forward_from, ''), COALESCE(views, 0), COALESCE(reply_markup, ''), media_group_id, COALESCE(reactions, ''), COALESCE(urls, '')
                FROM telegram_messages WHERE account_id = ? AND chat_id = ? ORDER BY message_id ASC
            """, (self.account_id, self.chat_id))
            nuevos = [r for r in rows if r[0] not in ids_actuales]
            if not nuevos:
                return
            n = self.lista_msgs.GetItemCount()
            foco_al_final = (n == 0) or (self.lista_msgs.GetFocusedItem() == n - 1)
            col_content = self._col_msg("content")
            marcar_nuevo = config.get("telegram", "show_unread_marker", True)
            idx_autoplay = None  # índice de la PRIMERA nota de voz/audio entrante nueva (la cola sigue)
            self.lista_msgs.Freeze()
            try:
                for r in nuevos:
                    idx = self.lista_msgs.GetItemCount()
                    valores, meta, _rep = self._valores_msg(r, idx + 1)
                    # Los mensajes que llegan en vivo entran como NO leídos (con su aviso "(nuevo)"),
                    # salvo los tuyos. Se marcarán leídos cuando pases el foco por ellos.
                    es_no_leido = not meta.get("outgoing", False)
                    meta["leido"] = not es_no_leido
                    meta["_contenido_base"] = valores.get("content", "")
                    if es_no_leido and marcar_nuevo and col_content is not None:
                        valores["content"] = _("(nuevo) ") + (valores.get("content", "") or "")
                    self._volcar_fila_msg(idx, valores)
                    self.msg_rows.append(meta)
                    if (idx_autoplay is None and es_no_leido and meta.get("reproducible")
                            and meta.get("media_type") in ("voice", "audio")):
                        idx_autoplay = idx
            finally:
                self.lista_msgs.Thaw()
            if foco_al_final:
                ult = self.lista_msgs.GetItemCount() - 1
                self.lista_msgs.Focus(ult)
                self.lista_msgs.EnsureVisible(ult)
            # Reproducción automática de la última nota de voz/audio entrante (si está activada). Si ya
            # hay un audio de este chat sonando (cola en curso), NO lo interrumpimos: la cola lo
            # alcanzará al terminar lo que está reproduciendo.
            if (idx_autoplay is not None and config.get("telegram", "autoplay_voice", False)
                    and not self._hay_audio_tg_en_curso()):
                try:
                    self._reproducir_o_pausar_audio(idx_autoplay)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"No se pudo autorreproducir la nota de voz: {e}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"No se pudieron insertar mensajes en vivo: {e}")

    def _on_mensaje_editado_tr(self, account_id, chat_id, message_id):
        """(Hilo de Telegram) Un mensaje de este chat se editó: actualizar su texto en la lista."""
        try:
            if account_id == self.account_id and chat_id == self.chat_id:
                import wx as _wx
                _wx.CallAfter(self._actualizar_mensaje_editado, message_id)
        except Exception:
            pass

    def _actualizar_mensaje_editado(self, message_id):
        """Reescribe en la lista el texto del mensaje editado, sin tocar el foco ni reconstruir."""
        try:
            row = db.query_one(
                "SELECT text, media_type, duration, file_name FROM telegram_messages "
                "WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                (self.account_id, self.chat_id, message_id))
            if not row:
                return
            text, media_type, duration, file_name = row
            col_content = self._col_msg("content")
            col_media = self._col_msg("media")
            tiene_col_media = col_media is not None
            for i, m in enumerate(self.msg_rows):
                if m.get("message_id") != message_id:
                    continue
                cuerpo = (text or "").replace("\n", " ")
                adjunto = ""
                if media_type:
                    adjunto = self._etiqueta_media(media_type, duration, file_name)
                    if media_type in ("voice", "audio", "video", "video_note"):
                        adjunto += " " + _("(Espacio para reproducir)")
                if not cuerpo and adjunto and not tiene_col_media:
                    cuerpo = adjunto
                etiqueta = cuerpo + " " + _("(editado)") if cuerpo else cuerpo
                if col_content is not None:
                    self.lista_msgs.SetItem(i, col_content, etiqueta or "")
                if tiene_col_media:
                    self.lista_msgs.SetItem(i, col_media, adjunto or "")
                m["text"] = text or ""
                m["base_text"] = cuerpo or ""
                break
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"No se pudo actualizar el mensaje editado: {e}")

    def _on_mensajes_borrados_tr(self, account_id, chat_id, ids):
        """(Hilo de Telegram) Mensajes borrados en este chat: marcarlos como eliminados."""
        try:
            mismo = (account_id == self.account_id and (chat_id == self.chat_id or chat_id is None))
            if mismo:
                import wx as _wx
                _wx.CallAfter(self._marcar_mensajes_borrados, set(ids))
        except Exception:
            pass

    def _marcar_mensajes_borrados(self, ids):
        """Marca como '[Mensaje eliminado]' los mensajes borrados, sin quitarlos de la lista
        (quitar filas descolocaría el foco y haría releer a NVDA toda la conversación)."""
        try:
            col_content = self._col_msg("content")
            col_media = self._col_msg("media")
            for i, m in enumerate(self.msg_rows):
                if m.get("message_id") in ids:
                    if col_content is not None:
                        self.lista_msgs.SetItem(i, col_content, _("[Mensaje eliminado]"))
                    if col_media is not None:
                        self.lista_msgs.SetItem(i, col_media, "")
                    m["text"] = ""
                    m["base_text"] = _("[Mensaje eliminado]")
                    m["media_type"] = ""
                    m["reproducible"] = False
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"No se pudieron marcar mensajes borrados: {e}")

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("Mensajes (los más recientes al final):")), 0, wx.ALL, 5)
        # Paginación local: la conversación carga una VENTANA de mensajes (no todo el
        # historial). Este botón amplía la ventana hacia atrás; solo se muestra si hay más.
        self.btn_anteriores = wx.Button(self, label=_("Cargar mensajes an&teriores"),
                                        name=_("Cargar mensajes anteriores de la conversación"))
        self.btn_anteriores.Bind(wx.EVT_BUTTON, self._on_cargar_anteriores)
        self.btn_anteriores.Hide()
        sizer.Add(self.btn_anteriores, 0, wx.LEFT | wx.RIGHT, 5)
        self.lista_msgs = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de mensajes de la conversación")
        )
        # Lista tipo buffer con columnas. La plantilla "telegram_message" define qué
        # columnas tiene y en qué orden (el lector anuncia la fila por columnas).
        self._construir_columnas_msgs()
        self.lista_msgs.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
        self.lista_msgs.Bind(wx.EVT_KEY_DOWN, self._on_msg_key)
        self.lista_msgs.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_msg_selected)
        self.lista_msgs.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_msg_activado)
        sizer.Add(self.lista_msgs, 1, wx.EXPAND | wx.ALL, 5)

        # Metadatos por fila para las acciones (responder, borrar, descargar...)
        self.msg_rows = []
        # Estado de reproducción de audio dentro de la conversación
        self._audio_msg_actual = None

        self.lbl_escribir = wx.StaticText(self, label=_("Escribir mensaje (Ctrl+Enter para enviar):"))
        sizer.Add(self.lbl_escribir, 0, wx.ALL, 5)
        self.txt_mensaje = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE, name=_("Campo de redacción de mensaje"),
            ayuda_texto=_("Escriba su mensaje y pulse Ctrl+Enter o el botón Enviar.")
        )
        self.txt_mensaje.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        self.txt_mensaje.Bind(wx.EVT_TEXT, self._on_escribiendo)
        self._ultima_accion_typing = 0.0
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt_mensaje)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt_mensaje)
        sizer.Add(self.txt_mensaje, 0, wx.EXPAND | wx.ALL, 5)

        # Botonera con aceleradores (&) únicos: Alt+letra ejecuta cada acción.
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_enviar = wx.Button(self, label=_("&Enviar"), name=_("Enviar mensaje"))
        self.btn_enviar.Bind(wx.EVT_BUTTON, self._on_enviar)
        btn_sizer.Add(self.btn_enviar, 0, wx.ALL, 5)

        self.btn_adjuntar = wx.Button(self, label=_("Ad&juntar archivo..."), name=_("Adjuntar y enviar archivo"))
        self.btn_adjuntar.Bind(wx.EVT_BUTTON, self._on_adjuntar)
        btn_sizer.Add(self.btn_adjuntar, 0, wx.ALL, 5)

        self.btn_album = wx.Button(self, label=_("Enviar á&lbum..."), name=_("Enviar varias fotos o vídeos como álbum"))
        self.btn_album.Bind(wx.EVT_BUTTON, self._on_album)
        btn_sizer.Add(self.btn_album, 0, wx.ALL, 5)

        self._grabando = False
        self.btn_grabar = wx.Button(self, label=_("&Grabar nota de voz"), name=_("Grabar y enviar una nota de voz"))
        self.btn_grabar.Bind(wx.EVT_BUTTON, self._on_grabar_voz)
        btn_sizer.Add(self.btn_grabar, 0, wx.ALL, 5)

        # Descartar la nota de voz que estás grabando SIN enviarla (solo activo mientras grabas).
        self.btn_cancelar_voz = wx.Button(self, label=_("Cancelar &grabación"), name=_("Cancelar la nota de voz que estás grabando"))
        self.btn_cancelar_voz.Bind(wx.EVT_BUTTON, self._on_cancelar_voz)
        self.btn_cancelar_voz.Disable()
        btn_sizer.Add(self.btn_cancelar_voz, 0, wx.ALL, 5)

        # El botón "Administrar" solo debe aparecer si eres administrador o dueño del chat. Se crea
        # OCULTO y se muestra después (en segundo plano) si la comprobación lo confirma. Antes salía
        # para cualquier grupo/supergrupo/canal aunque no fueras admin.
        self.btn_admin = None
        if self.chat_type in ("group", "supergroup", "channel"):
            self.btn_admin = wx.Button(self, label=_("Ad&ministrar..."), name=_("Administrar grupo o canal"))
            self.btn_admin.Bind(wx.EVT_BUTTON, self._on_administrar)
            self.btn_admin.Hide()
            btn_sizer.Add(self.btn_admin, 0, wx.ALL, 5)

        self.btn_buscar = wx.Button(self, label=_("&Buscar..."), name=_("Buscar en la conversación"))
        self.btn_buscar.Bind(wx.EVT_BUTTON, self._on_buscar)
        btn_sizer.Add(self.btn_buscar, 0, wx.ALL, 5)

        self.btn_props = wx.Button(self, label=_("&Propiedades..."), name=_("Ver propiedades, perfil y multimedia"))
        self.btn_props.Bind(wx.EVT_BUTTON, self._on_propiedades)
        btn_sizer.Add(self.btn_props, 0, wx.ALL, 5)

        self.btn_actualizar = wx.Button(self, label=_("Actuali&zar"), name=_("Actualizar mensajes"))
        self.btn_actualizar.Bind(wx.EVT_BUTTON, lambda e: self._descargar_historial())
        btn_sizer.Add(self.btn_actualizar, 0, wx.ALL, 5)

        self.btn_exportar = wx.Button(self, label=_("E&xportar..."), name=_("Exportar conversación"))
        self.btn_exportar.Bind(wx.EVT_BUTTON, self._on_exportar)
        btn_sizer.Add(self.btn_exportar, 0, wx.ALL, 5)

        # Navegación por los mensajes
        self.btn_primer_no_leido = wx.Button(self, label=_("Ir al primer &no leído"), name=_("Ir al primer mensaje no leído"))
        self.btn_primer_no_leido.Bind(wx.EVT_BUTTON, self._on_ir_primer_no_leido)
        btn_sizer.Add(self.btn_primer_no_leido, 0, wx.ALL, 5)

        self.btn_final = wx.Button(self, label=_("Ir al &final"), name=_("Ir al último mensaje"))
        self.btn_final.Bind(wx.EVT_BUTTON, self._on_ir_final)
        btn_sizer.Add(self.btn_final, 0, wx.ALL, 5)

        # Chat de voz (experimental). Solo grupos, supergrupos y canales tienen
        # chat de voz; en chats privados Telegram usa llamadas 1 a 1 (no soportado).
        self._en_voz = False
        self._voz_silenciado = False
        self._permite_voz = self.chat_type in ("group", "supergroup", "channel")
        self.btn_voz = wx.Button(self, label=_("Entrar a chat de &voz"), name=_("Entrar o salir del chat de voz"))
        self.btn_voz.Bind(wx.EVT_BUTTON, self._on_chat_voz)
        self.btn_voz.Enable(self._permite_voz)
        btn_sizer.Add(self.btn_voz, 0, wx.ALL, 5)

        self.btn_mute = wx.Button(self, label=_("&Hablar por el micro"), name=_("Hablar por el micrófono o silenciarlo"))
        self.btn_mute.Bind(wx.EVT_BUTTON, self._on_mute_voz)
        self.btn_mute.Disable()
        btn_sizer.Add(self.btn_mute, 0, wx.ALL, 5)

        self.btn_mas = wx.Button(self, label=_("&Más acciones..."), name=_("Más acciones: encuesta, ubicación, programar, bloquear..."))
        self.btn_mas.Bind(wx.EVT_BUTTON, self._on_mas_acciones)
        btn_sizer.Add(self.btn_mas, 0, wx.ALL, 5)

        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(sizer)
