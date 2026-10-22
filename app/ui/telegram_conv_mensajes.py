# -*- coding: utf-8 -*-
"""Conversación Telegram: lista de mensajes, navegación y menú contextual (mixin).
"""
import wx
from app.core.privacy import abrir_url

from app.core.speech import speak, notificar
from app.core.database import db
from app.core.config_manager import config
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service

# Tipos de adjunto que REALMENTE se descargan como archivo. Otros media (encuesta, ubicación,
# contacto, enlace, dado, juego...) NO tienen archivo, así que su menú NO debe ofrecer descargar.
_MEDIA_DESCARGABLE = {"photo", "video", "audio", "voice", "video_note", "document", "animation", "sticker"}


class _ConvMensajesMixin:
    # La plantilla "telegram_message" define qué columnas tiene la lista de mensajes y en qué orden.
    _ANCHOS_MSG = {"num": 60, "author": 150, "username": 150, "content": 430, "media": 150, "descarga": 110, "flags": 90, "date": 140}

    def _construir_columnas_msgs(self):
        """Crea las columnas de la lista de mensajes según la plantilla telegram_message.
        Devuelve la lista ordenada de (campo, encabezado)."""
        from app.core.plantillas import columnas_de_plantilla, definicion
        plantilla = config.get("accessibility", "telegram_message", "") or definicion("telegram_message")["default"]
        cols = columnas_de_plantilla(plantilla)
        if not cols:
            cols = columnas_de_plantilla(definicion("telegram_message")["default"])
        while self.lista_msgs.GetColumnCount() > 0:
            self.lista_msgs.DeleteColumn(0)
        for i, (campo, enc) in enumerate(cols):
            self.lista_msgs.InsertColumn(i, enc, width=self._ANCHOS_MSG.get(campo, 140))
        self._cols_msgs = cols
        return cols

    def _col_msg(self, campo):
        """Índice de la columna de un campo en la lista de mensajes, o None si no está."""
        for i, (c, _e) in enumerate(getattr(self, "_cols_msgs", []) or []):
            if c == campo:
                return i
        return None

    def _valores_msg(self, r, num):
        """Construye (valores_por_campo, metadatos_fila, es_reproducible) de un mensaje."""
        from .main_frame import formatear_tiempo_relativo
        # La fila puede traer (o no) las últimas columnas reply_markup, media_group_id, reactions y urls.
        media_group_id = r[15] if len(r) >= 16 else None
        reactions = r[16] if len(r) >= 17 else ""
        urls_json = r[17] if len(r) >= 18 else ""
        if len(r) >= 15:
            (message_id, sender, sender_username, text, date, outgoing, media_type, media_path,
             duration, file_name, edited, reply_to, forward_from, views, reply_markup) = r[:15]
        else:
            (message_id, sender, sender_username, text, date, outgoing, media_type, media_path,
             duration, file_name, edited, reply_to, forward_from, views) = r
            reply_markup = ""
        tiene_botones = bool(reply_markup)
        remitente = _("Tú") if outgoing else (sender or _("Desconocido"))
        cuerpo = (text or "").replace("\n", " ")
        es_reproducible = media_type in ("voice", "audio", "video", "video_note")
        adjunto = ""
        if media_type:
            adjunto = self._etiqueta_media(media_type, duration, file_name)
            if es_reproducible:
                adjunto += " " + _("(Espacio para reproducir)")
        # Si la plantilla NO tiene columna de adjunto y el mensaje es solo multimedia,
        # metemos la etiqueta en el contenido para no perderla.
        tiene_col_media = self._col_msg("media") is not None
        if not cuerpo and adjunto and not tiene_col_media:
            cuerpo = adjunto
        # Media SIN archivo (encuesta, ubicación...) que SÍ tiene texto (p. ej. la pregunta): anteponer
        # la etiqueta al texto para que se sepa que es una encuesta y no un mensaje normal.
        elif cuerpo and adjunto and not tiene_col_media and media_type not in _MEDIA_DESCARGABLE:
            cuerpo = f"{adjunto} {cuerpo}"
        # Botones de bot y reacciones: si la plantilla tiene su PROPIA columna ({botones}/{reactions})
        # se muestran ahí; si no, se añaden al content para no perderlos (mismo criterio que {media}).
        if tiene_botones and self._col_msg("botones") is None:
            cuerpo = (cuerpo + " " + _("(con botones)")).strip()
        if reactions and self._col_msg("reactions") is None:
            cuerpo = (cuerpo + " " + _("[reacciones: {0}]").format(reactions)).strip()
        # Estado de descarga: 'Descargado' solo si hay adjunto y el archivo SIGUE en disco (si el
        # usuario lo borró, media_path apunta a un archivo inexistente y se considera no descargado).
        descargado = False
        if media_type and media_path:
            import os
            descargado = os.path.exists(media_path)
        valores = {
            "num": str(num),
            "author": remitente,
            "username": "" if outgoing else (sender_username or ""),
            "content": cuerpo,
            "media": adjunto,
            "descarga": _("Descargado") if descargado else "",
            "date": formatear_tiempo_relativo(str(date)) if date else "",
            "flags": _("Editado") if edited else "",
            "respuesta": _("Respuesta") if reply_to else "",
            "reenviado": _("Reenviado de {0}").format(forward_from) if forward_from else "",
            "vistas": _("{0} vistas").format(views) if views else "",
            "reactions": reactions or "",
            "botones": _("Con botones") if tiene_botones else "",
            "album": _("Álbum") if media_group_id else "",
        }
        # Enlaces del mensaje: de la columna urls (JSON, rellenada al guardar con las entidades y la
        # vista previa web). Si la fila es vieja y no la trae, respaldo con las URLs sueltas del texto.
        urls = self._urls_de_fila(urls_json, text)
        meta = {
            "message_id": message_id, "media_type": media_type,
            "media_path": media_path if descargado else "",
            "outgoing": bool(outgoing), "text": text or "", "reproducible": es_reproducible,
            "duration": duration, "file_name": file_name, "base_text": cuerpo or "",
            "tiene_botones": tiene_botones, "descargado": descargado, "sender_name": remitente,
            "media_group_id": media_group_id or None, "reactions": reactions or "", "urls": urls,
        }
        return valores, meta, es_reproducible

    @staticmethod
    def _urls_de_fila(urls_json, text):
        """Lista de URLs de un mensaje: primero las guardadas (JSON de la columna urls); si no hay
        (fila antigua), las URLs http(s) sueltas del texto como respaldo."""
        import json, re
        if urls_json:
            try:
                datos = json.loads(urls_json)
                if isinstance(datos, list) and datos:
                    return [str(u) for u in datos if u]
            except Exception:
                pass
        return re.findall(r'https?://[^\s\)\]\}<>"]+', text or "")

    def _volcar_fila_msg(self, idx, valores):
        """Inserta una fila de mensaje rellenando cada columna con el valor de su campo."""
        cols = getattr(self, "_cols_msgs", []) or []
        pos = 0
        for i, (campo, _e) in enumerate(cols):
            val = valores.get(campo, "") or ""
            if i == 0:
                pos = self.lista_msgs.InsertItem(idx, val)
            else:
                self.lista_msgs.SetItem(pos, i, val)
        return pos
    def _on_key_down(self, event):
        code = event.GetKeyCode()
        mods = event.GetModifiers()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and mods == wx.MOD_CONTROL:
            self._on_enviar(None)
        elif code == wx.WXK_SPACE and mods == wx.MOD_CONTROL and self.chat_type in ("group", "supergroup"):
            # Ctrl+Espacio: completar una @mención con un miembro del grupo.
            self._autocompletar_mencion()
        else:
            event.Skip()

    def _autocompletar_mencion(self):
        """Al escribir '@' seguido de parte de un nombre y pulsar Ctrl+Espacio, ofrece elegir a quién
        mencionar. Los miembros se cargan UNA sola vez (bajo demanda) y se cachean en memoria; el
        filtrado es local, así que no afecta al rendimiento ni consulta la red al teclear."""
        import re
        pos = self.txt_mensaje.GetInsertionPoint()
        antes = self.txt_mensaje.GetValue()[:pos]
        m = re.search(r'@(\w*)$', antes)
        if not m:
            speak(_("Escribe arroba y parte del nombre, y pulsa Control+Espacio para elegir a quién "
                    "mencionar."), interrupt=True)
            return
        prefijo = m.group(1).lower()
        inicio = m.start()
        if getattr(self, "_miembros_cache", None) is None:
            speak(_("Cargando miembros…"), interrupt=True)
            def _bg(ctx):
                return telegram_service.obtener_miembros(self.account_id, self.chat_id, limit=200)
            def _fin(res, err):
                if not self:
                    return
                if err:
                    notificar(_("No se pudieron cargar los miembros: {0}").format(err))
                    return
                self._miembros_cache = res or []
                self._elegir_mencion(prefijo, inicio, pos)
            task_manager.start_task(_bg, callback=_fin, pass_context=True)
        else:
            self._elegir_mencion(prefijo, inicio, pos)

    def _elegir_mencion(self, prefijo, inicio, pos):
        cands = []
        for mb in (self._miembros_cache or []):
            nombre = mb.get("nombre") or ""
            user = (mb.get("username") or "").lstrip("@")
            if not prefijo or prefijo in nombre.lower() or prefijo in user.lower():
                etiqueta = _("{0} (@{1})").format(nombre, user) if user else nombre
                cands.append((etiqueta, user, nombre))
        if not cands:
            speak(_("Ningún miembro coincide con eso."), interrupt=True)
            return
        dlg = wx.SingleChoiceDialog(self, _("Elige a quién mencionar:"), _("Mencionar"),
                                    [c[0] for c in cands])
        if dlg.ShowModal() == wx.ID_OK:
            _etq, user, nombre = cands[dlg.GetSelection()]
            mencion = f"@{user}" if user else nombre
            val = self.txt_mensaje.GetValue()
            nuevo = val[:inicio] + mencion + " " + val[pos:]
            self.txt_mensaje.SetValue(nuevo)
            self.txt_mensaje.SetInsertionPoint(inicio + len(mencion) + 1)
        dlg.Destroy()
        self.txt_mensaje.SetFocus()

    @staticmethod
    def _formato_duracion(segundos) -> str:
        try:
            s = int(segundos or 0)
        except (TypeError, ValueError):
            s = 0
        if s <= 0:
            return ""
        return f"{s // 60}:{s % 60:02d}"

    def _etiqueta_media(self, media_type, duration, file_name) -> str:
        """Construye una etiqueta legible del adjunto, con duración o nombre."""
        etiquetas = {
            "voice": _("Nota de voz"), "audio": _("Audio"), "video": _("Vídeo"),
            "video_note": _("Videomensaje"), "photo": _("Foto"), "document": _("Documento"),
            "sticker": _("Sticker"), "animation": _("GIF"),
            # Media SIN archivo: etiquetas legibles para que el lector no diga el tipo crudo ("poll").
            "poll": _("Encuesta"), "web_page": _("Enlace"), "contact": _("Contacto"),
            "location": _("Ubicación"), "venue": _("Lugar"), "dice": _("Emoji animado"),
            "game": _("Juego"), "story": _("Historia"), "checklist": _("Lista de tareas"),
        }
        base = etiquetas.get(media_type, media_type or "")
        dur = self._formato_duracion(duration)
        if media_type in ("voice", "audio", "video", "video_note") and dur:
            return f"[{base} {dur}]"
        if media_type in ("document", "audio") and file_name:
            return f"[{base}: {file_name}]"
        return f"[{base}]" if base else ""

    def _cargar_mensajes_cache(self):
        self.lista_msgs.DeleteAllItems()
        self.lista_msgs.limpiar_lecturas()
        self.msg_rows = []
        if not getattr(self, "_cols_msgs", None):
            self._construir_columnas_msgs()
        # Filtro por TEMA de foro (si el usuario eligió uno): el tema "General" (id 1) agrupa también
        # los mensajes sin thread_id. Es un simple WHERE, no penaliza el rendimiento.
        where_tema, params_tema = "", []
        tema = getattr(self, "_tema_filtro", None)
        if tema is not None:
            if tema == 1:
                where_tema = " AND (thread_id = 1 OR thread_id IS NULL)"
            else:
                where_tema = " AND thread_id = ?"
                params_tema = [tema]
        # Estado de NO LEÍDOS por message_id (NO por índice): así sobrevive a las recargas de la
        # lista durante la sesión (descargar historial, editar, buscar…) sin perder por dónde vas.
        # _max_leido_msg_id = id del último mensaje que ya has leído; lo que tenga id mayor está sin
        # leer y lleva el aviso "(nuevo)". El id se GUARDA en la base (last_read_message_id), así que
        # al volver a abrir el chat se retoma exactamente donde lo dejaste, no "unos mensajes atrás".
        fila = db.query_one("SELECT unread_count, COALESCE(last_read_message_id, 0) "
                            "FROM telegram_chats WHERE id = ?",
                            (f"{self.account_id}:{self.chat_id}",))
        no_leidos = (fila[0] if fila else 0) or 0
        leido_guardado = (fila[1] if fila else 0) or 0

        # PAGINACIÓN: la conversación NO carga todo el historial (un chat grande congelaba
        # la ventana y disparaba la memoria; la lista no es virtual). Se cargan los últimos
        # `_ventana_msgs` mensajes —suficiente para cubrir todos los no leídos con margen—
        # y el botón "Cargar mensajes anteriores" amplía la ventana hacia atrás por lotes.
        if not getattr(self, "_ventana_msgs", 0):
            self._ventana_msgs = max(300, no_leidos + 50)
        total_bd = (db.query_one("""
            SELECT COUNT(*) FROM telegram_messages
            WHERE account_id = ? AND chat_id = ?""" + where_tema,
            (self.account_id, self.chat_id, *params_tema)) or (0,))[0]
        rows = db.query("""
            SELECT message_id, sender_name, sender_username, text, date, outgoing, media_type, media_path, duration, file_name, COALESCE(edited, 0), reply_to, COALESCE(forward_from, ''), COALESCE(views, 0), COALESCE(reply_markup, ''), media_group_id, COALESCE(reactions, ''), COALESCE(urls, '')
            FROM telegram_messages
            WHERE account_id = ? AND chat_id = ?""" + where_tema + """
            ORDER BY message_id DESC LIMIT ?
        """, (self.account_id, self.chat_id, *params_tema, self._ventana_msgs))
        rows.reverse()   # se pintan en orden cronológico (los recientes al final)

        n_total = len(rows)
        self._actualizar_boton_anteriores(total_bd - n_total)
        if not getattr(self, "_lectura_inicializada", False):
            self._lectura_inicializada = True
            self._max_leido_msg_id = self._frontera_inicial(rows, no_leidos, leido_guardado)

        col_content = self._col_msg("content")
        marcar_nuevo = config.get("telegram", "show_unread_marker", True)
        poner_separador = config.get("telegram", "show_unread_divider", True)
        objetivo = None
        fila = 0                     # índice REAL en la lista (el separador también ocupa uno)
        for num_msg, r in enumerate(rows, start=1):
            # La numeración cuenta MENSAJES, no filas: el separador no lleva número y no descoloca
            # la cuenta que oye el usuario ("mensaje 12 de 300").
            valores, meta, _rep = self._valores_msg(r, num_msg)
            es_no_leido = (r[0] > self._max_leido_msg_id)
            # Separador de no leídos (como en Unigram): una fila justo ANTES del primer mensaje
            # sin leer, para que se vea de un vistazo por dónde te quedaste.
            if es_no_leido and objetivo is None and poner_separador and fila > 0:
                self._insertar_separador_no_leidos(fila)
                fila += 1
            meta["leido"] = not es_no_leido
            meta["_contenido_base"] = valores.get("content", "")
            if es_no_leido and marcar_nuevo and col_content is not None:
                valores["content"] = _("(nuevo) ") + (valores.get("content", "") or "")
            if es_no_leido and objetivo is None:
                objetivo = fila      # primer no leído real (según lo ya leído)
            self._volcar_fila_msg(fila, valores)
            self.msg_rows.append(meta)
            fila += 1

        # Posicionar el foco en el primer mensaje aún sin leer; si todo está leído, en el último.
        # (Situarte en él lo marca como leído, como en cualquier app.) El foco va al MENSAJE, no al
        # separador: la marca está para verla al navegar hacia atrás, no para tropezar con ella.
        n = self.lista_msgs.GetItemCount()
        if n > 0:
            if objetivo is None:
                objetivo = n - 1
            self.lista_msgs.Focus(objetivo)
            self.lista_msgs.Select(objetivo)
            self.lista_msgs.EnsureVisible(objetivo)
            if no_leidos > 0:
                speak(_("{0} mensajes sin leer. Te dejo en el primero.").format(no_leidos), interrupt=False)

    def _insertar_separador_no_leidos(self, fila):
        """Inserta la fila separadora "Mensajes no leídos" y su metadato en msg_rows.

        Va con message_id None y la marca _separador para que el resto del código (marcar leído,
        reproducir, menú contextual…) la ignore sin descuadrar la correspondencia índice↔fila."""
        texto = _("--- Mensajes no leídos ---")
        pos = self._volcar_fila_msg(fila, {"content": texto, "author": ""})
        try:
            self.lista_msgs.establecer_lectura(pos, _("Mensajes no leídos"))
        except Exception:
            pass
        # Se rellenan las claves que consulta el resto del código (outgoing, media_type…) para que
        # ninguna acción falle si el foco pasa por encima de la marca.
        self.msg_rows.append({"message_id": None, "_separador": True, "leido": True,
                              "text": "", "base_text": texto, "_contenido_base": texto, "urls": [],
                              "outgoing": False, "media_type": None, "media_path": "",
                              "reproducible": False, "tiene_botones": False, "descargado": False,
                              "sender_name": "", "media_group_id": None, "reactions": ""})

    @staticmethod
    def _frontera_inicial(rows, no_leidos, leido_guardado):
        """Id del último mensaje LEÍDO al abrir el chat (la frontera con lo nuevo).

        Manda `leido_guardado` (columna last_read_message_id): es el id exacto por el que ibas la
        última vez, así que se retoma justo ahí. Solo si nunca se guardó (chat abierto por primera
        vez desde que existe la columna) se deduce del contador del servidor, y entonces se cuentan
        SOLO los mensajes ENTRANTES: unread_count no incluye los tuyos ni los de servicio, pero esos
        sí ocupan fila, y restarlos a ciegas era justo lo que dejaba la marca varios mensajes atrás.
        """
        if not rows:
            return leido_guardado or 0
        if leido_guardado:
            return leido_guardado
        if no_leidos <= 0:
            return rows[-1][0]                       # todo leído: la frontera es el último
        # Recorrer del final hacia atrás dejando pasar `no_leidos` mensajes entrantes (r[5] es
        # 'outgoing'); el primero que quede por detrás es el último que habías leído.
        pendientes = no_leidos
        for i in range(len(rows) - 1, -1, -1):
            if not rows[i][5]:
                pendientes -= 1
                if pendientes <= 0:
                    return rows[i - 1][0] if i > 0 else 0
        return 0                                     # todo lo cargado está sin leer

    def _actualizar_boton_anteriores(self, restantes: int):
        """Muestra/oculta el botón "Cargar mensajes anteriores" según queden mensajes
        más antiguos fuera de la ventana cargada, y actualiza su etiqueta."""
        btn = getattr(self, "btn_anteriores", None)
        if btn is None:
            return
        try:
            if restantes > 0:
                btn.SetLabel(_("Cargar mensajes an&teriores ({0} más)").format(restantes))
                if not btn.IsShown():
                    btn.Show()
                    self.Layout()
            elif btn.IsShown():
                btn.Hide()
                self.Layout()
        except Exception:
            pass

    def _on_cargar_anteriores(self, event):
        """Amplía la ventana de mensajes hacia atrás en lotes de 300, conservando el foco
        en el mensaje donde estabas (la lista se reconstruye, pero se busca su message_id)."""
        idx_foco = self.lista_msgs.GetFocusedItem()
        msg_id_foco = None
        if idx_foco is not None and 0 <= idx_foco < len(self.msg_rows):
            msg_id_foco = self.msg_rows[idx_foco].get("message_id")
        self._ventana_msgs = int(getattr(self, "_ventana_msgs", 300)) + 300
        self._cargar_mensajes_cache()
        # Restaurar el foco donde estaba (tras recargar, hay filas nuevas por encima).
        if msg_id_foco is not None:
            for i, m in enumerate(self.msg_rows):
                if m.get("message_id") == msg_id_foco:
                    self.lista_msgs.Focus(i)
                    self.lista_msgs.Select(i)
                    self.lista_msgs.EnsureVisible(i)
                    break
        speak(_("Cargados mensajes anteriores."), interrupt=True)

    def _on_ir_final(self, event):
        """Sitúa el foco en el último mensaje."""
        n = self.lista_msgs.GetItemCount()
        if n > 0:
            self.lista_msgs.Focus(n - 1)
            self.lista_msgs.Select(n - 1)
            self.lista_msgs.EnsureVisible(n - 1)
            self.lista_msgs.SetFocus()

    def _on_ir_primer_no_leido(self, event):
        """Sitúa el foco en el primer mensaje AÚN sin leer (la marca va bajando según lees). Si ya
        lo has leído todo, va al último."""
        n = self.lista_msgs.GetItemCount()
        if n <= 0:
            return
        mx = getattr(self, "_max_leido_msg_id", 0)
        objetivo = next((i for i, m in enumerate(self.msg_rows)
                         if (m.get("message_id") or 0) > mx), n - 1)
        self.lista_msgs.Focus(objetivo)
        self.lista_msgs.Select(objetivo)
        self.lista_msgs.EnsureVisible(objetivo)
        self.lista_msgs.SetFocus()

    def _on_msg_activado(self, event):
        """Intro sobre un mensaje: abre su enlace. Si tiene varios (hipervínculos del texto y/o la
        vista previa web), deja elegir cuál. Ahora reconoce también los enlaces cuyo texto visible NO
        es la URL (lo típico en canales: "Descargar aquí")."""
        idx = event.GetIndex()
        if not (0 <= idx < len(self.msg_rows)):
            return
        if self.msg_rows[idx].get("_separador"):
            return
        self._abrir_enlaces_de(self.msg_rows[idx])

    def _abrir_enlaces_de(self, meta):
        """Abre el/los enlace(s) de un mensaje. Uno solo: lo abre; varios: los ofrece en una lista."""
        urls = meta.get("urls") or []
        if not urls:
            notificar(_("Este mensaje no contiene enlaces."))
            return
        if len(urls) == 1:
            abrir_url(urls[0])
            speak(_("Abriendo enlace: {0}").format(urls[0]), interrupt=True)
            return
        dlg = wx.SingleChoiceDialog(self, _("Este mensaje tiene varios enlaces. Elige cuál abrir:"),
                                    _("Abrir enlace"), list(urls))
        if dlg.ShowModal() == wx.ID_OK:
            url = urls[dlg.GetSelection()]
            abrir_url(url)
            speak(_("Abriendo enlace: {0}").format(url), interrupt=True)
        dlg.Destroy()

    def _on_msg_selected(self, event):
        """Reproduce un sonido identificativo según el tipo de multimedia del mensaje y MARCA como
        leídos los mensajes por los que vas pasando (hasta el enfocado)."""
        idx = event.GetIndex()
        if 0 <= idx < len(self.msg_rows):
            from .main_frame import evento_sonido_media
            from app.core.event_bus import event_bus
            evento = evento_sonido_media(self.msg_rows[idx].get("media_type"))
            if evento:
                event_bus.emit("sound_trigger", evento)
            if config.get("telegram", "mark_read_on_focus", True):
                self._marcar_leido_hasta_idx(idx)
        event.Skip()

    def _marcar_leido_hasta_idx(self, idx):
        """Al pasar el foco por un mensaje (índice `idx`), marca como leídos todos los de id menor o
        igual: actualiza _max_leido_msg_id, quita su aviso "(nuevo)" y programa el marcado en el
        servidor (con debounce). Solo avanza."""
        if not (0 <= idx < len(self.msg_rows)):
            return
        mid = self.msg_rows[idx].get("message_id") or 0
        if mid <= getattr(self, "_max_leido_msg_id", 0):
            return
        self._max_leido_msg_id = mid
        self._guardar_punto_lectura(mid)
        # Quitar el aviso "(nuevo)" de todas las filas ya leídas (id <= max).
        col_content = self._col_msg("content")
        if col_content is not None:
            for j, m in enumerate(self.msg_rows):
                if not m.get("leido", True) and (m.get("message_id") or 0) <= mid:
                    m["leido"] = True
                    try:
                        self.lista_msgs.SetItem(j, col_content, m.get("_contenido_base", ""))
                    except Exception:
                        pass
        self._programar_marcar_leido_servidor()

    def _guardar_punto_lectura(self, mid):
        """Guarda EN LOCAL el id del último mensaje leído (solo si avanza). Es lo que hace que al
        volver a abrir el chat te deje justo donde lo dejaste, sin depender de unread_count del
        servidor —que no cuenta tus mensajes ni los de servicio y desplazaba la marca hacia atrás."""
        if not mid:
            return
        try:
            db.execute(
                "UPDATE telegram_chats SET last_read_message_id = ? "
                "WHERE id = ? AND COALESCE(last_read_message_id, 0) < ?",
                (mid, f"{self.account_id}:{self.chat_id}", mid))
        except Exception:
            pass

    def _programar_marcar_leido_servidor(self):
        """Marca leído en el SERVIDOR hasta _max_leido_msg_id, con un pequeño retardo para juntar la
        navegación rápida (debounce). Así el estado se guarda según lees, no solo al salir, y resiste
        que la lista de chats se vuelva a sincronizar con el servidor."""
        try:
            t = getattr(self, "_timer_leido", None)
            if t is not None and t.IsRunning():
                t.Stop()
            self._timer_leido = wx.CallLater(1500, self._marcar_leido_servidor_ahora)
        except Exception:
            self._marcar_leido_servidor_ahora()

    def _restantes_no_leidos(self):
        """Número de mensajes cargados con id mayor que _max_leido_msg_id (los que quedan sin leer)."""
        mx = getattr(self, "_max_leido_msg_id", 0)
        return sum(1 for m in self.msg_rows if (m.get("message_id") or 0) > mx)

    def _marcar_leido_servidor_ahora(self):
        max_id = getattr(self, "_max_leido_msg_id", 0)
        if not max_id:
            return
        cuenta, chat = self.account_id, self.chat_id
        restantes = self._restantes_no_leidos()

        def _bg(ctx):
            telegram_service.marcar_leido_hasta(cuenta, chat, max_id, restantes)
        task_manager.start_task(_bg, pass_context=True)

    def _persistir_leido_al_salir(self):
        """Al salir, guarda inmediatamente en el servidor el punto al que llegaste leyendo."""
        try:
            t = getattr(self, "_timer_leido", None)
            if t is not None and t.IsRunning():
                t.Stop()
        except Exception:
            pass
        self._marcar_leido_servidor_ahora()

    def _on_msg_key(self, event):
        """Espacio: si el mensaje tiene botones de bot, abre el diálogo de botones; si no, reproduce/
        pausa su audio. Ctrl+I: describir imagen. Ctrl+T: traducir."""
        code = event.GetKeyCode()
        mods = event.GetModifiers()
        if code == wx.WXK_SPACE and mods == wx.MOD_NONE:
            idx = self.lista_msgs.GetFocusedItem()
            if 0 <= idx < len(self.msg_rows):
                meta = self.msg_rows[idx]
                # Prioridad: los botones de un bot se abren con Espacio (lo más habitual en mensajes
                # de bot, que rara vez son audio). Si no hay botones, Espacio reproduce el audio.
                if meta.get("tiene_botones"):
                    self._on_botones_mensaje(meta)
                    return
                if meta.get("reproducible"):
                    self._reproducir_o_pausar_audio(idx)
                    return
        elif mods == wx.MOD_CONTROL and code == ord('I'):
            idx = self.lista_msgs.GetFocusedItem()
            if 0 <= idx < len(self.msg_rows):
                meta = self.msg_rows[idx]
                if meta.get("media_type") == "photo":
                    self._on_describir_ia(meta)
                else:
                    notificar(_("Este mensaje no contiene una imagen."))
                return
        elif mods == wx.MOD_CONTROL and code == ord('T'):
            idx = self.lista_msgs.GetFocusedItem()
            if 0 <= idx < len(self.msg_rows):
                meta = self.msg_rows[idx]
                if meta.get("text"):
                    self._on_traducir_app(meta)
                else:
                    notificar(_("Este mensaje no contiene texto para traducir."))
                return
        elif mods == wx.MOD_CONTROL and code == ord('C'):
            idx = self.lista_msgs.GetFocusedItem()
            if 0 <= idx < len(self.msg_rows):
                meta = self.msg_rows[idx]
                if (meta.get("text") or "").strip():
                    self._on_copiar_texto_mensaje(meta)
                else:
                    notificar(_("Este mensaje no contiene texto que copiar."))
                return
        elif mods == (wx.MOD_CONTROL | wx.MOD_SHIFT) and code == ord('D'):
            # Ver descripción de la multimedia (Telegram no tiene texto alternativo como
            # Mastodon/Bluesky; se muestra el pie/descripción del mensaje multimedia).
            idx = self.lista_msgs.GetFocusedItem()
            if 0 <= idx < len(self.msg_rows):
                meta = self.msg_rows[idx]
                if not meta.get("media_type"):
                    notificar(_("Este mensaje no contiene multimedia."))
                else:
                    desc = (meta.get("text") or "").strip()
                    if desc:
                        speak(_("Descripción: {0}").format(desc), interrupt=True)
                        self._mostrar_texto(_("Descripción de la multimedia"), desc)
                    else:
                        speak(_("Esta multimedia no tiene descripción."), interrupt=True)
                return
        event.Skip()

    def _reproducir_o_pausar_audio(self, idx, encadenado=False):
        """Descarga (si hace falta) y reproduce/pausa el audio del mensaje enfocado. Con `encadenado`
        (cuando lo lanza la cola de autorreproducción) los avisos por voz son más discretos."""
        from app.services.media_player.player_service import player_service
        meta = self.msg_rows[idx]
        mid = meta["message_id"]

        # Si es el audio que ya está sonando, alternar pausa/reproducción
        if self._audio_msg_actual == mid:
            estado = player_service.backend.obtener_estado()
            if estado == "reproduciendo":
                player_service.pausar()
                speak(_("Pausado."), interrupt=True)
            else:
                player_service.reproducir()
                speak(_("Reproduciendo."), interrupt=True)
                self._autoplay_iniciar_cadena()  # reanudar el encadenado si estaba activo
            return

        if not encadenado:
            speak(_("Cargando audio..."), interrupt=True)

        def _bg(ctx):
            from app.services.audio import microphone
            ruta = meta.get("media_path")
            if not ruta:
                ruta = telegram_service.descargar_media(self.account_id, self.chat_id, mid)
            # Convertir a WAV si BASS no soporta el formato (p. ej. Opus de las notas de voz)
            if ruta:
                ruta = microphone.convertir_a_wav_si_necesario(ruta)
            return ruta

        def _finish(res, err):
            if err or not res:
                notificar(_("No se pudo reproducir el audio."))
                return
            meta["media_path"] = res
            self._audio_msg_actual = mid
            player_service.cargar_y_reproducir(res, self.chat_title, service_origin="telegram", account_origin_id=self.account_id)
            if encadenado:
                # En la cola, un aviso corto de quién manda la siguiente nota (sin interrumpir tanto).
                quien = (meta.get("sender_name") or "").strip()
                speak(_("Siguiente nota de voz de {0}.").format(quien) if quien
                      else _("Siguiente nota de voz."), interrupt=True)
            else:
                speak(_("Reproduciendo. Pulse Espacio para pausar."), interrupt=True)
            # Si está activada la autorreproducción, encadenar las siguientes notas de voz/audio.
            self._autoplay_iniciar_cadena()

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    # --- COLA DE AUTORREPRODUCCIÓN DE NOTAS DE VOZ / AUDIOS ---
    def _autoplay_activo(self):
        return bool(config.get("telegram", "autoplay_voice", False))

    def _hay_audio_tg_en_curso(self):
        """¿El reproductor está ocupado con un audio de ESTA conversación (sonando o en pausa)?"""
        from app.services.media_player.player_service import player_service
        if (player_service.current_service_origin != "telegram"
                or player_service.current_account_origin_id != self.account_id
                or self._audio_msg_actual is None):
            return False
        return player_service.backend.obtener_estado() in ("reproduciendo", "pausado", "esperando")

    def _idx_por_mid(self, mid):
        for j, m in enumerate(self.msg_rows):
            if m.get("message_id") == mid:
                return j
        return None

    def _siguiente_audio_idx(self, desde_idx):
        """Índice de la primera nota de voz/audio reproducible posterior a `desde_idx` (de quien sea)."""
        for j in range(desde_idx + 1, len(self.msg_rows)):
            m = self.msg_rows[j]
            if m.get("reproducible") and m.get("media_type") in ("voice", "audio"):
                return j
        return None

    def _autoplay_iniciar_cadena(self):
        """Arranca la vigilancia para encadenar la siguiente nota de voz/audio cuando termine la actual."""
        if not self._autoplay_activo():
            return
        self._autoplay_cola_activa = True
        self._autoplay_visto_reproduciendo = False
        self._autoplay_rearmar()

    def _autoplay_rearmar(self):
        try:
            t = getattr(self, "_autoplay_timer", None)
            if t is not None and t.IsRunning():
                t.Stop()
        except Exception:
            pass
        self._autoplay_timer = wx.CallLater(700, self._autoplay_vigilar)

    def _autoplay_detener_cadena(self):
        self._autoplay_cola_activa = False
        try:
            t = getattr(self, "_autoplay_timer", None)
            if t is not None and t.IsRunning():
                t.Stop()
        except Exception:
            pass

    def _autoplay_vigilar(self):
        """Vigila el reproductor: cuando la nota de voz/audio actual termina, lanza la siguiente; si se
        pausa o sigue sonando, espera. Si el reproductor pasa a otra cosa, abandona la cadena."""
        if not getattr(self, "_autoplay_cola_activa", False):
            return
        if not self._autoplay_activo():
            self._autoplay_detener_cadena()
            return
        from app.services.media_player.player_service import player_service
        # ¿Seguimos siendo dueños del reproductor? Si lo tomó la radio/podcast u otra cuenta, salimos.
        if (player_service.current_service_origin != "telegram"
                or player_service.current_account_origin_id != self.account_id
                or self._audio_msg_actual is None):
            self._autoplay_detener_cadena()
            return
        estado = player_service.backend.obtener_estado()
        if estado == "reproduciendo":
            self._autoplay_visto_reproduciendo = True
            self._autoplay_rearmar()
            return
        if estado in ("pausado", "esperando", "cargado", "inactivo"):
            # Pausa manual o buffering: esperamos sin avanzar.
            self._autoplay_rearmar()
            return
        # estado == "detenido": solo encadenamos si llegó a reproducirse (evita falsos finales).
        if not getattr(self, "_autoplay_visto_reproduciendo", False):
            self._autoplay_rearmar()
            return
        idx_actual = self._idx_por_mid(self._audio_msg_actual)
        sig = self._siguiente_audio_idx(idx_actual) if idx_actual is not None else None
        if sig is None:
            self._autoplay_detener_cadena()
            return
        # Marcar como leído lo escuchado (si está activado) y reproducir la siguiente.
        if config.get("telegram", "mark_read_on_focus", True):
            try:
                self._marcar_leido_hasta_idx(sig)
            except Exception:
                pass
        self._reproducir_o_pausar_audio(sig, encadenado=True)

    # --- ACCIONES SOBRE MENSAJES (menú contextual) ---
    def _on_context_menu(self, event):
        idx = self.lista_msgs.GetFocusedItem()
        if idx < 0 or idx >= len(self.msg_rows):
            return
        meta = self.msg_rows[idx]
        # La marca de "mensajes no leídos" no es un mensaje: no tiene acciones.
        if meta.get("_separador"):
            speak(_("Marca de mensajes no leídos."), interrupt=True)
            return
        menu = wx.Menu()

        item_ver = menu.Append(wx.ID_ANY, _("Ver mensaje..."))
        self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_ver_mensaje(m), item_ver)

        if meta.get("tiene_botones"):
            item_btns = menu.Append(wx.ID_ANY, _("Botones del mensaje..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_botones_mensaje(m), item_btns)

        # Abrir enlace(s): solo si el mensaje TIENE enlaces (hipervínculos del texto o vista previa web).
        urls_meta = meta.get("urls") or []
        if urls_meta:
            etiqueta = _("Abrir enlace...") if len(urls_meta) == 1 else _("Abrir enlaces ({0})...").format(len(urls_meta))
            item_enlaces = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._abrir_enlaces_de(m), item_enlaces)
        menu.AppendSeparator()

        # Responder: solo si en este chat SE PUEDE publicar (en un canal de solo lectura, no).
        if getattr(self, "_puedo_publicar", True):
            item_resp = menu.Append(wx.ID_ANY, _("Responder"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_responder(m), item_resp)

        # Responder en privado: en grupos, escribir al AUTOR en un chat 1 a 1 (no a sus mensajes ajenos
        # propios ni en privados, donde ya estás con la persona).
        if self.chat_type in ("group", "supergroup") and not meta["outgoing"]:
            item_priv = menu.Append(wx.ID_ANY, _("Responder en privado..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_responder_privado(m), item_priv)

        # Editar: solo TUS mensajes de texto.
        if meta["outgoing"] and meta["text"]:
            item_edit = menu.Append(wx.ID_ANY, _("Editar"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_editar(m), item_edit)

        # Mensaje con ARCHIVO propio: editar su pie o reemplazar el archivo (no aplica a encuestas,
        # ubicaciones, contactos... que no tienen archivo).
        if meta["outgoing"] and meta.get("media_type") in _MEDIA_DESCARGABLE:
            item_pie = menu.Append(wx.ID_ANY, _("Editar pie (descripción)..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_editar_pie(m), item_pie)
            item_remplazar = menu.Append(wx.ID_ANY, _("Reemplazar archivo..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_reemplazar_media(m), item_remplazar)

        # Borrar: tus propios mensajes siempre; los ajenos solo en privado/bot o si eres admin del
        # grupo/canal (en un canal ajeno de solo lectura no puedes borrar nada).
        if meta["outgoing"] or self.chat_type in ("private", "bot") or getattr(self, "_es_admin", False):
            item_borrar = menu.Append(wx.ID_ANY, _("Borrar"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_borrar(m), item_borrar)

        # Reaccionar: no tiene sentido donde no puedes interactuar (canal de solo lectura sin permiso).
        if getattr(self, "_puedo_publicar", True) or self.chat_type == "channel":
            item_react = menu.Append(wx.ID_ANY, _("Reaccionar..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_reaccionar(m), item_react)

        # Ver quién reaccionó: solo si el mensaje TIENE reacciones (evita una consulta en vano).
        if (meta.get("reactions") or "").strip():
            item_ver_react = menu.Append(wx.ID_ANY, _("Ver quién reaccionó..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_quien_reacciono(m), item_ver_react)

        # Moderación (admin) en grupos: quitar todas las reacciones de un mensaje. Solo si eres admin.
        if self.chat_type in ("group", "supergroup") and getattr(self, "_es_admin", False):
            item_quitar_react = menu.Append(wx.ID_ANY, _("Quitar todas las reacciones (admin)"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_quitar_reacciones(m), item_quitar_react)

        # Fijar/Desfijar: en privado/bot ambos pueden; en grupos/canales, solo administradores.
        if self.chat_type in ("private", "bot") or getattr(self, "_es_admin", False):
            item_fijar = menu.Append(wx.ID_ANY, _("Fijar mensaje"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_fijar_mensaje(m, True), item_fijar)
            item_desfijar = menu.Append(wx.ID_ANY, _("Desfijar mensaje"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_fijar_mensaje(m, False), item_desfijar)

        if (meta.get("text") or "").strip():
            item_copiar_txt = menu.Append(wx.ID_ANY, _("Copiar texto del mensaje"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_copiar_texto_mensaje(m), item_copiar_txt)

        # Copiar/reenviar SOLO si el chat no tiene contenido protegido (noforwards): si lo tiene,
        # Telegram no deja reenviarlo y ofrecerlo sería engañoso.
        if not getattr(self, "_protegido", False):
            item_copiar = menu.Append(wx.ID_ANY, _("Copiar a otro chat..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_copiar_a_chat(m), item_copiar)

            if meta.get("media_group_id"):
                item_album = menu.Append(wx.ID_ANY, _("Reenviar el álbum completo a otro chat..."))
                self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_reenviar_album(m), item_album)

            item_guardar = menu.Append(wx.ID_ANY, _("Reenviar a Mensajes guardados"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_reenviar_guardados(m), item_guardar)

        # Encuestas: solo si el mensaje enfocado ES una encuesta.
        if meta.get("media_type") == "poll":
            item_poll = menu.Append(wx.ID_ANY, _("Votar / ver encuesta..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_ver_encuesta(m), item_poll)
            if meta["outgoing"]:
                item_cerrar_poll = menu.Append(wx.ID_ANY, _("Cerrar encuesta"))
                self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_cerrar_encuesta(m), item_cerrar_poll)

        # Lista de tareas: solo si el mensaje enfocado ES una lista de tareas (checklist).
        if meta.get("media_type") == "checklist":
            item_checklist = menu.Append(wx.ID_ANY, _("Lista de tareas (marcar/añadir)..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_checklist_mensaje(m), item_checklist)

        if meta.get("media_type") == "animation":
            item_gif = menu.Append(wx.ID_ANY, _("Guardar en Mis GIF"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_guardar_gif(m, False), item_gif)
            item_gif_q = menu.Append(wx.ID_ANY, _("Quitar de Mis GIF"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_guardar_gif(m, True), item_gif_q)

        if self.chat_type == "channel":
            item_coment = menu.Append(wx.ID_ANY, _("Ver comentarios..."))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_ver_comentarios(m), item_coment)

        if meta["text"]:
            item_trad = menu.Append(wx.ID_ANY, _("Traducir (Telegram)"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_traducir_nativo(m), item_trad)
            item_trad_app = menu.Append(wx.ID_ANY, _("Traducir con la app"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_traducir_app(m), item_trad_app)
            item_resumir = menu.Append(wx.ID_ANY, _("Resumir con IA"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_resumir_ia(m), item_resumir)
            item_tts = menu.Append(wx.ID_ANY, _("Leer en voz alta (TTS)"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_leer_tts_msg(m), item_tts)

        if meta.get("media_type") == "photo":
            item_desc_ia = menu.Append(wx.ID_ANY, _("Describir imagen con IA"))
            self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_describir_ia(m), item_desc_ia)

        if meta.get("media_type") in _MEDIA_DESCARGABLE:
            # Menú DINÁMICO según el estado real de descarga (comprobado AHORA: detecta si ya se
            # descargó o si el archivo se borró del disco desde la última vez).
            ruta_local = telegram_service.ruta_descarga_valida(self.account_id, self.chat_id, meta["message_id"])
            meta["media_path"] = ruta_local or ""
            meta["descargado"] = bool(ruta_local)
            if ruta_local:
                if meta.get("reproducible"):
                    item_rep = menu.Append(wx.ID_ANY, _("Reproducir"))
                    self.Bind(wx.EVT_MENU, lambda e, m=meta: self._reproducir_local(m), item_rep)
                item_abrir = menu.Append(wx.ID_ANY, _("Abrir archivo"))
                self.Bind(wx.EVT_MENU, lambda e, r=ruta_local: self._abrir_archivo(r), item_abrir)
                item_carpeta = menu.Append(wx.ID_ANY, _("Abrir carpeta del archivo"))
                self.Bind(wx.EVT_MENU, lambda e, r=ruta_local: self._abrir_carpeta(r), item_carpeta)
                item_redesc = menu.Append(wx.ID_ANY, _("Volver a descargar"))
                self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_descargar(m), item_redesc)
                item_borrar_desc = menu.Append(wx.ID_ANY, _("Borrar descarga local"))
                self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_borrar_descarga(m), item_borrar_desc)
            else:
                item_desc = menu.Append(wx.ID_ANY, _("Descargar archivo"))
                self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_descargar(m), item_desc)
                item_play = menu.Append(wx.ID_ANY, _("Descargar y reproducir"))
                self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_descargar(m, reproducir=True), item_play)
                # Streaming: empezar a oír YA sin bajar el archivo entero (audio, música, vídeo).
                if meta.get("reproducible"):
                    item_stream = menu.Append(wx.ID_ANY, _("Reproducir en streaming (sin descargar)"))
                    self.Bind(wx.EVT_MENU, lambda e, m=meta: self._on_reproducir_streaming(m), item_stream)

        self.PopupMenu(menu)
        menu.Destroy()

    def _abrir_archivo(self, ruta):
        """Abre un archivo descargado con su aplicación predeterminada."""
        import os
        if not ruta or not os.path.exists(ruta):
            notificar(_("El archivo ya no está descargado."))
            return
        # SEGURIDAD (TG-008): un archivo de tipo ACTIVO (ejecutable/script/acceso directo) NO se abre
        # desde la app (evita ejecutar malware recibido con un clic). Se ofrece abrir su carpeta.
        from app.core.file_safety import es_extension_activa
        if es_extension_activa(ruta):
            notificar(_("Por seguridad, «{0}» no se abre desde la aplicación (es un archivo "
                        "ejecutable o de script). Se abrirá su carpeta para que decidas qué "
                        "hacer.").format(os.path.basename(ruta)))
            self._abrir_carpeta(ruta)
            return
        try:
            os.startfile(ruta)  # type: ignore[attr-defined]
            speak(_("Abriendo archivo."), interrupt=True)
        except Exception as e:
            notificar(_("No se pudo abrir el archivo: {0}").format(e))

    def _on_reproducir_streaming(self, meta):
        """Reproduce el audio/vídeo EN STREAMING (empieza a sonar sin bajar el archivo entero). Usa el
        servidor local de Telegram, que el reproductor abre como una URL de radio/podcast."""
        mid = meta["message_id"]
        speak(_("Abriendo en streaming…"), interrupt=True)
        def _bg(ctx):
            return telegram_service.url_streaming(self.account_id, self.chat_id, mid)
        def _finish(url, err):
            if not self:
                return
            if err or not url:
                notificar(_("No se pudo iniciar el streaming: {0}").format(err or ""))
                return
            from app.services.media_player.player_service import player_service
            player_service.cargar_y_reproducir(url, self.chat_title, service_origin="telegram",
                                               account_origin_id=self.account_id)
            speak(_("Reproduciendo en streaming."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _reproducir_local(self, meta):
        """Reproduce el archivo YA descargado (sin volver a la red)."""
        ruta = meta.get("media_path")
        import os
        if not ruta or not os.path.exists(ruta):
            # Se borró: volver a descargar y reproducir.
            self._on_descargar(meta, reproducir=True)
            return
        from app.services.media_player.player_service import player_service
        from app.services.audio import microphone
        def _bg(ctx):
            return microphone.convertir_a_wav_si_necesario(ruta)
        def _finish(res, err):
            r = res or ruta
            player_service.cargar_y_reproducir(r, self.chat_title, service_origin="telegram", account_origin_id=self.account_id)
            speak(_("Reproduciendo."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_borrar_descarga(self, meta):
        """Borra la descarga local del adjunto y refresca el estado en la lista."""
        mid = meta["message_id"]
        fila = self._fila_de_mensaje(mid)
        def _bg(ctx):
            return telegram_service.borrar_descarga_local(self.account_id, self.chat_id, mid)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo borrar la descarga: {0}").format(err))
                return
            meta["media_path"] = ""
            meta["descargado"] = False
            self._refrescar_descarga_fila(fila, False)
            notificar(_("Descarga local borrada."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _refrescar_descarga_fila(self, fila, descargado):
        """Actualiza la columna {descarga} de una fila (si la plantilla la muestra)."""
        col = self._col_msg("descarga")
        if col is None or not (0 <= fila < self.lista_msgs.GetItemCount()):
            return
        try:
            self.lista_msgs.SetItem(fila, col, _("Descargado") if descargado else "")
        except Exception:
            pass

    def _on_responder(self, meta):
        self.txt_mensaje.SetFocus()
        self._reply_to = meta["message_id"]
        speak(_("Respondiendo. Escriba su mensaje y pulse Ctrl+Enter."), interrupt=True)

    def _on_responder_privado(self, meta):
        """Abre (o crea) el chat 1 a 1 con el AUTOR del mensaje, para escribirle en privado. El
        sender_id se consulta puntualmente por message_id (no está en la fila, para no recargar la
        lista); es una sola consulta a la base, no afecta al rendimiento."""
        mid = meta["message_id"]
        fila = db.query_one("SELECT sender_id, sender_name FROM telegram_messages WHERE id = ?",
                            (f"{self.account_id}:{self.chat_id}:{mid}",))
        if not fila or not fila[0]:
            speak(_("No se puede identificar al autor de este mensaje."), interrupt=True)
            return
        user_id = int(fila[0])
        nombre = fila[1] or meta.get("sender_name") or _("Usuario")
        from .telegram_views import TelegramConversationDialog
        dlg = TelegramConversationDialog(self, self.account_id, user_id, nombre, chat_type="private")
        dlg.ShowModal()   # modal sobre modal: al cerrar la privada vuelves al grupo
        dlg.Destroy()

    def _on_editar(self, meta):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Editar mensaje:"), _("Editar"), value=meta["text"])
        if dlg.ShowModal() == wx.ID_OK:
            nuevo = dlg.GetValue().strip()
            mid = meta["message_id"]
            def _bg(ctx):
                return telegram_service.editar_mensaje(self.account_id, self.chat_id, mid, nuevo)
            def _finish(res, err):
                if err:
                    notificar(_("Error al editar: {0}").format(err))
                else:
                    self._cargar_mensajes_cache()
                    speak(_("Mensaje editado."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_borrar(self, meta):
        dlg = wx.MessageDialog(self, _("¿Borrar este mensaje para todos?"), _("Confirmar borrado"),
                               wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        mid = meta["message_id"]
        def _bg(ctx):
            return telegram_service.borrar_mensaje(self.account_id, self.chat_id, mid, revoke=True)
        def _finish(res, err):
            if err:
                notificar(_("Error al borrar: {0}").format(err))
            else:
                self._cargar_mensajes_cache()
                notificar(_("Mensaje borrado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_reaccionar(self, meta):
        emojis = ["👍", "❤️", "🔥", "😂", "😮", "😢", "👏", "🎉"]
        dlg = wx.SingleChoiceDialog(self, _("Elija una reacción:"), _("Reaccionar"), emojis)
        if dlg.ShowModal() == wx.ID_OK:
            emoji = dlg.GetStringSelection()
            mid = meta["message_id"]
            def _bg(ctx):
                return telegram_service.enviar_reaccion(self.account_id, self.chat_id, mid, emoji)
            def _finish(res, err):
                if err:
                    notificar(_("Error al reaccionar: {0}").format(err))
                else:
                    notificar(_("Reacción enviada."))
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_editar_pie(self, meta):
        """Editar el pie (caption) de un mensaje multimedia propio."""
        from .accessible_controls import CustomTextEntryDialog
        actual = meta.get("text") or ""
        dlg = CustomTextEntryDialog(self, _("Editar el pie (descripción) del archivo:"),
                                    _("Editar pie"), value=actual)
        if dlg.ShowModal() == wx.ID_OK:
            nuevo = dlg.GetValue().strip()
            mid = meta["message_id"]
            def _bg(ctx):
                return telegram_service.editar_pie_mensaje(self.account_id, self.chat_id, mid, nuevo)
            def _finish(res, err):
                if err:
                    notificar(_("Error al editar el pie: {0}").format(err))
                else:
                    self._cargar_mensajes_cache()
                    speak(_("Pie editado."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_reenviar_album(self, meta):
        """Reenvía a otro chat el álbum completo al que pertenece este mensaje."""
        filas = db.query(
            "SELECT chat_id, title FROM telegram_chats WHERE account_id = ? ORDER BY title COLLATE NOCASE",
            (self.account_id,))
        chats = [(r[0], r[1] or str(r[0])) for r in filas]
        if not chats:
            speak(_("No hay chats disponibles."), interrupt=True)
            return
        dlg = wx.SingleChoiceDialog(self, _("Reenviar el álbum a:"), _("Reenviar álbum"),
                                    [t for _c, t in chats])
        if dlg.ShowModal() == wx.ID_OK:
            destino = chats[dlg.GetSelection()][0]
            mid = meta["message_id"]
            def _bg(ctx):
                return telegram_service.reenviar_album(self.account_id, self.chat_id, mid, destino)
            def _finish(res, err):
                notificar(_("No se pudo reenviar el álbum: {0}").format(err) if err
                          else _("Álbum reenviado."))
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_reemplazar_media(self, meta):
        """Reemplazar el archivo multimedia de un mensaje propio por otro del disco."""
        with wx.FileDialog(self, _("Elija el archivo que reemplazará al actual"),
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            ruta = dlg.GetPath()
        mid = meta["message_id"]
        pie = meta.get("text") or ""
        speak(_("Reemplazando archivo..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.editar_media_mensaje(self.account_id, self.chat_id, mid, ruta, pie)
        def _finish(res, err):
            if err:
                notificar(_("Error al reemplazar el archivo: {0}").format(err))
            else:
                self._cargar_mensajes_cache()
                speak(_("Archivo reemplazado."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_quitar_reacciones(self, meta):
        """(Admin) Quitar todas las reacciones de un mensaje del grupo."""
        dlg = wx.MessageDialog(self, _("¿Quitar todas las reacciones de este mensaje? "
                                       "Necesitas permisos de administración."),
                               _("Quitar reacciones"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        mid = meta["message_id"]
        def _bg(ctx):
            return telegram_service.quitar_todas_reacciones(self.account_id, self.chat_id, mid)
        def _finish(res, err):
            if err:
                notificar(_("No se pudieron quitar las reacciones: {0}").format(err))
            else:
                notificar(_("Reacciones quitadas."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_quien_reacciono(self, meta):
        """Muestra en un diálogo accesible quién ha reaccionado al mensaje y con qué emoji."""
        mid = meta["message_id"]
        speak(_("Consultando reacciones..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.quien_reacciono(self.account_id, self.chat_id, mid)
        def _finish(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo consultar: {0}").format(err))
                return
            if not res:
                speak(_("Nadie ha reaccionado, o no se puede ver quién."), interrupt=True)
                return
            # Agrupar por emoji para una lectura clara: "👍 (3): Ana, Luis, Marta".
            from collections import OrderedDict
            por_emoji = OrderedDict()
            for r in res:
                por_emoji.setdefault(r["emoji"], []).append(r["nombre"])
            lineas = [_("{0} ({1}): {2}").format(emoji, len(gente), ", ".join(gente))
                      for emoji, gente in por_emoji.items()]
            from .accessible_controls import mostrar_info
            mostrar_info(self, "\n".join(lineas), _("Quién reaccionó"))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_fijar_mensaje(self, meta, fijar):
        mid = meta["message_id"]
        def _bg(ctx):
            if fijar:
                return telegram_service.fijar_mensaje(self.account_id, self.chat_id, mid, notificar=False)
            return telegram_service.desfijar_mensaje(self.account_id, self.chat_id, mid)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Mensaje fijado.") if fijar else _("Mensaje desfijado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_copiar_texto_mensaje(self, meta):
        # Copiar el texto del mensaje al portapapeles del sistema (estilo otros clientes).
        texto = (meta.get("text") or "").strip()
        if not texto:
            speak(_("El mensaje no tiene texto que copiar."), interrupt=True)
            return
        copiado = False
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(texto))
                wx.TheClipboard.Flush()
                copiado = True
            finally:
                wx.TheClipboard.Close()
        if copiado:
            notificar(_("Texto del mensaje copiado al portapapeles."))
        else:
            speak(_("No se pudo copiar el texto."), interrupt=True)

    def _on_copiar_a_chat(self, meta):
        # Elegir destino entre los chats cacheados de esta cuenta.
        filas = db.query(
            "SELECT chat_id, title FROM telegram_chats WHERE account_id = ? ORDER BY title COLLATE NOCASE",
            (self.account_id,)
        )
        chats = [(r[0], r[1] or str(r[0])) for r in filas]
        if not chats:
            speak(_("No hay chats disponibles."), interrupt=True)
            return
        etiquetas = [t for _cid, t in chats]
        dlg = wx.SingleChoiceDialog(self, _("Copiar el mensaje a:"), _("Copiar a otro chat"), etiquetas)
        if dlg.ShowModal() == wx.ID_OK:
            destino = chats[dlg.GetSelection()][0]
            mid = meta["message_id"]
            def _bg(ctx):
                return telegram_service.copiar_mensaje(self.account_id, self.chat_id, mid, destino)
            def _finish(res, err):
                if err:
                    notificar(_("Error al copiar: {0}").format(err))
                else:
                    notificar(_("Mensaje copiado."))
            task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_ver_encuesta(self, meta):
        mid = meta["message_id"]
        def _bg(ctx):
            return telegram_service.obtener_encuesta(self.account_id, self.chat_id, mid)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("Este mensaje no es una encuesta."), interrupt=True)
                return
            self._mostrar_encuesta(meta, res)
        speak(_("Leyendo encuesta..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _mostrar_encuesta(self, meta, poll):
        # Resumen con resultados
        partes = [poll["pregunta"], ""]
        for o in poll["opciones"]:
            marca = "✓ " if o["indice"] in poll["elegidas"] else ""
            partes.append(_("{0}{1} — {2} votos ({3}%)").format(
                marca, o["texto"], o["votos"], o["porcentaje"]))
        partes.append("")
        partes.append(_("Total de votos: {0}").format(poll["total"]))
        if poll["cerrada"]:
            partes.append(_("(Encuesta cerrada)"))
        # Acciones disponibles según el estado de la encuesta (votar, retirar voto, y si es tuya,
        # añadir/quitar opciones).
        resumen = "\n".join(partes)
        acciones, claves = [], []
        if not poll["cerrada"] and not poll["elegidas"]:
            acciones.append(_("Votar...")); claves.append("votar")
        if not poll["cerrada"] and poll["elegidas"]:
            acciones.append(_("Retirar mi voto")); claves.append("retirar")
        if meta.get("outgoing") and not poll["cerrada"]:
            acciones.append(_("Añadir una opción...")); claves.append("anadir")
            acciones.append(_("Quitar una opción...")); claves.append("quitar")
        acciones.append(_("Solo leer la encuesta")); claves.append("leer")

        dlg = wx.SingleChoiceDialog(self, resumen, _("Encuesta"), acciones)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        clave = claves[dlg.GetSelection()]
        dlg.Destroy()
        mid = meta["message_id"]
        if clave == "leer":
            self._mostrar_texto(_("Encuesta"), resumen)
        elif clave == "votar":
            self._votar_en_encuesta(meta, poll)
        elif clave == "retirar":
            self._accion_encuesta(mid, "retirar")
        elif clave == "anadir":
            from .accessible_controls import CustomTextEntryDialog
            d = CustomTextEntryDialog(self, _("Texto de la nueva opción:"), _("Añadir opción"))
            if d.ShowModal() == wx.ID_OK and d.GetValue().strip():
                self._accion_encuesta(mid, "anadir", d.GetValue().strip())
            d.Destroy()
        elif clave == "quitar":
            textos = [o["texto"] for o in poll["opciones"]]
            d = wx.SingleChoiceDialog(self, _("Elija la opción a quitar:"), _("Quitar opción"), textos)
            if d.ShowModal() == wx.ID_OK:
                self._accion_encuesta(mid, "quitar", textos[d.GetSelection()])
            d.Destroy()

    def _votar_en_encuesta(self, meta, poll):
        etiquetas = [o["texto"] for o in poll["opciones"]]
        dlg = wx.MultiChoiceDialog(self, _("Elija su voto:"), _("Votar"), etiquetas) \
            if poll["multiple"] else \
            wx.SingleChoiceDialog(self, _("Elija su voto:"), _("Votar"), etiquetas)
        if dlg.ShowModal() == wx.ID_OK:
            seleccion = list(dlg.GetSelections()) if poll["multiple"] else dlg.GetSelection()
            if seleccion or seleccion == 0:
                self._votar_encuesta(meta["message_id"], seleccion)
        dlg.Destroy()

    def _accion_encuesta(self, mid, accion, opcion=None):
        def _bg(ctx):
            if accion == "retirar":
                return telegram_service.retirar_voto_encuesta(self.account_id, self.chat_id, mid)
            if accion == "anadir":
                return telegram_service.anadir_opcion_encuesta(self.account_id, self.chat_id, mid, opcion)
            if accion == "quitar":
                return telegram_service.quitar_opcion_encuesta(self.account_id, self.chat_id, mid, opcion)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo completar la acción de la encuesta: {0}").format(err))
            else:
                notificar({
                    "retirar": _("Voto retirado."),
                    "anadir": _("Opción añadida."),
                    "quitar": _("Opción quitada."),
                }.get(accion, _("Hecho.")))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _votar_encuesta(self, mid, opciones):
        def _bg(ctx):
            return telegram_service.votar_encuesta(self.account_id, self.chat_id, mid, opciones)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo votar: {0}").format(err))
            else:
                notificar(_("Voto registrado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_cerrar_encuesta(self, meta):
        mid = meta["message_id"]
        def _bg(ctx):
            return telegram_service.cerrar_encuesta(self.account_id, self.chat_id, mid)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                speak(_("Encuesta cerrada."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_guardar_gif(self, meta, quitar):
        """Guarda (o quita) el GIF del mensaje en tus 'GIF guardados'."""
        mid = meta["message_id"]
        speak(_("Procesando GIF..."), interrupt=True)
        def _bg(ctx):
            fid = telegram_service.file_id_de_mensaje(self.account_id, self.chat_id, mid)
            if not fid:
                return None
            telegram_service.guardar_gif(self.account_id, fid, quitar=quitar)
            return True
        def _finish(res, err):
            if err or not res:
                notificar(_("No se pudo: {0}").format(err or _("el mensaje no es un GIF")))
            else:
                notificar(_("Quitado de Mis GIF.") if quitar else _("Guardado en Mis GIF."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_checklist_mensaje(self, meta):
        """Abre la lista de tareas de un mensaje para marcarlas hechas/no hechas o añadir nuevas."""
        mid = meta["message_id"]
        speak(_("Leyendo lista de tareas..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.leer_checklist(self.account_id, self.chat_id, mid)
        def _finish(chk, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not chk:
                speak(_("Este mensaje no es una lista de tareas."), interrupt=True)
                return
            self._mostrar_checklist(mid, chk)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _mostrar_checklist(self, mid, chk):
        tareas = chk.get("tareas", [])
        opciones = [_("Marcar tareas como hechas/no hechas..."), _("Añadir tareas nuevas...")]
        titulo = chk.get("titulo") or _("Lista de tareas")
        resumen = titulo + "\n\n" + "\n".join(
            ("[x] " if hecha else "[ ] ") + texto for _i, texto, hecha in tareas)
        dlg = wx.SingleChoiceDialog(self, resumen, _("Lista de tareas"), opciones)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        accion = dlg.GetSelection()
        dlg.Destroy()
        if accion == 0:
            if not tareas:
                speak(_("No hay tareas."), interrupt=True)
                return
            from .accessible_controls import AccessibleCheckListBox
            d = wx.Dialog(self, title=_("Marca las tareas hechas"), size=(460, 420),
                          style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(wx.StaticText(d, label=_("Marca las que estén hechas:")), 0, wx.ALL, 6)
            clb = AccessibleCheckListBox(d, choices=[t for _i, t, _h in tareas])
            for i, (_id, _t, hecha) in enumerate(tareas):
                clb.Check(i, hecha)
            s.Add(clb, 1, wx.EXPAND | wx.ALL, 6)
            s.Add(d.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 6)
            d.SetSizer(s)
            if d.ShowModal() == wx.ID_OK:
                marcadas = set(clb.GetCheckedItems())
                hechas = [tareas[i][0] for i in range(len(tareas)) if i in marcadas]
                no_hechas = [tareas[i][0] for i in range(len(tareas)) if i not in marcadas]
                self._accion_checklist(mid, "marcar", hechas=hechas, no_hechas=no_hechas)
            d.Destroy()
        else:
            from .accessible_controls import CustomTextEntryDialog
            d = CustomTextEntryDialog(self, _("Tareas nuevas (una por línea):"), _("Añadir tareas"),
                                      value="", multiline=True)
            textos = [t.strip() for t in d.GetValue().splitlines() if t.strip()] if d.ShowModal() == wx.ID_OK else []
            d.Destroy()
            if textos:
                self._accion_checklist(mid, "anadir", textos=textos)

    def _accion_checklist(self, mid, accion, hechas=None, no_hechas=None, textos=None):
        def _bg(ctx):
            if accion == "marcar":
                return telegram_service.marcar_tareas_checklist(self.account_id, self.chat_id, mid, hechas, no_hechas)
            return telegram_service.anadir_tareas_checklist(self.account_id, self.chat_id, mid, textos)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo actualizar la lista: {0}").format(err))
            else:
                notificar(_("Lista actualizada."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_ver_comentarios(self, meta):
        mid = meta["message_id"]
        def _bg(ctx):
            comentarios = telegram_service.obtener_comentarios(self.account_id, self.chat_id, mid)
            destino = telegram_service.obtener_chat_comentarios(self.account_id, self.chat_id, mid)
            return comentarios, destino
        def _finish(res, err):
            if err:
                speak(_("No se pudieron cargar los comentarios: {0}").format(err), interrupt=True)
                return
            comentarios, destino = res
            if not comentarios:
                speak(_("Esta publicación no tiene comentarios."), interrupt=True)
            else:
                lineas = [_("{0} ({1}):\n{2}").format(c["autor"], c["fecha"], c["texto"]) for c in comentarios]
                self._mostrar_texto(_("Comentarios ({0})").format(len(comentarios)), "\n\n".join(lineas))
            # Ofrecer responder si hay grupo de discusión
            if destino and destino.get("chat_id"):
                resp = wx.MessageBox(_("¿Escribir un comentario?"), _("Comentar"),
                                     wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self)
                if resp == wx.YES:
                    self._comentar(destino)
        speak(_("Cargando comentarios..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _comentar(self, destino):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Tu comentario:"), _("Comentar"), multiline=True)
        texto = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not texto:
            return
        chat = destino["chat_id"]
        reply = destino.get("message_id")
        def _bg(ctx):
            return telegram_service.enviar_mensaje(self.account_id, chat, texto, reply_to=reply)
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else _("Comentario enviado."))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _fila_de_mensaje(self, mid):
        """Índice de fila en la lista para un message_id, o -1."""
        for i, r in enumerate(self.msg_rows):
            if r.get("message_id") == mid:
                return i
        return -1

    def _actualizar_progreso_fila(self, fila, porcentaje):
        """Muestra el % de descarga en la propia fila del mensaje."""
        if not (0 <= fila < len(self.msg_rows)):
            return
        base = self.msg_rows[fila].get("base_text", "")
        if porcentaje >= 100:
            texto = base
        else:
            texto = "{0}  [{1}%]".format(base, porcentaje)
        col = self._col_msg("content")
        if col is None:
            col = self._col_msg("media")
        if col is None:
            col = 0
        try:
            self.lista_msgs.SetItem(fila, col, texto)
        except Exception:
            pass

    def _abrir_carpeta(self, ruta):
        """Abre el explorador en la carpeta del archivo y lo selecciona."""
        import os, subprocess
        if not ruta or not os.path.exists(ruta):
            speak(_("No se encuentra el archivo."), interrupt=True)
            return
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(ruta)])
        except Exception:
            try:
                os.startfile(os.path.dirname(ruta))
            except Exception as e:
                notificar(_("No se pudo abrir la carpeta: {0}").format(e))
