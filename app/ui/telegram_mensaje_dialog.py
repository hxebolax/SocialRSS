# -*- coding: utf-8 -*-
"""Diálogo «Ver mensaje» de Telegram.

Muestra TODOS los datos obtenibles de un mensaje y, cuando el envío tiene varios
adjuntos (un álbum / grupo de medios), permite seleccionar y descargar uno o varios
con su porcentaje y avisos, igual que la descarga de un único archivo. Tiene tres
pestañas: Detalles, Adjuntos y Respuestas (interactuables, con sus propios adjuntos).
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, AccessibleCheckListBox
from .ui_helpers import formatear_tiempo_relativo


class TelegramMensajeDialog(wx.Dialog):
    """Ver un mensaje de Telegram con detalles, adjuntos y respuestas.

    conv: el TelegramConversationDialog padre (se reutilizan sus ayudantes:
    _etiqueta_media, _abrir_carpeta, _on_responder, _valores_msg)."""

    def __init__(self, conv, account_id: int, chat_id: int, meta: dict):
        super().__init__(
            conv,
            title=_("Ver mensaje"),
            size=(720, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Ver mensaje de Telegram"),
        )
        self.conv = conv
        self.account_id = account_id
        self.chat_id = chat_id
        self.meta = meta or {}
        self.message_id = self.meta.get("message_id")
        self._adjuntos = []          # lista de dicts de obtener_grupo_medios
        self._adj_base = {}          # idx -> etiqueta base (sin %)
        self._respuestas = []        # lista de meta de respuestas
        self._descargando = False
        self._ultima_descarga = None

        self._init_ui()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_OK))
        self.CenterOnParent()
        self._cargar_detalles()
        self._cargar_adjuntos()
        self._cargar_respuestas()

    # --- Construcción de la interfaz ---
    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self, name=_("Pestañas del mensaje"))

        self.notebook.AddPage(self._panel_detalles(), _("Detalles"))
        self.notebook.AddPage(self._panel_adjuntos(), _("Adjuntos"))
        self.notebook.AddPage(self._panel_respuestas(), _("Respuestas"))
        sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        botones.AddStretchSpacer()
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 6)
        sizer.Add(botones, 0, wx.EXPAND)
        self.SetSizer(sizer)

    def _panel_detalles(self):
        panel = wx.Panel(self.notebook)
        s = wx.BoxSizer(wx.VERTICAL)
        self.txt_detalles = AccessibleTextCtrl(
            panel, style=wx.TE_MULTILINE | wx.TE_READONLY,
            name=_("Detalles del mensaje"))
        self.txt_detalles.SetValue(_("Cargando detalles..."))
        s.Add(self.txt_detalles, 1, wx.EXPAND | wx.ALL, 6)
        panel.SetSizer(s)
        return panel

    def _panel_adjuntos(self):
        panel = wx.Panel(self.notebook)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("Adjuntos del envío (marca los que quieras descargar):")),
              0, wx.ALL, 6)
        self.clb_adjuntos = AccessibleCheckListBox(
            panel, size=(-1, 220), name=_("Lista de adjuntos"))
        self.clb_adjuntos.Append(_("Cargando adjuntos..."))
        self.clb_adjuntos.Enable(False)
        s.Add(self.clb_adjuntos, 1, wx.EXPAND | wx.ALL, 6)

        self.lbl_estado_adj = wx.StaticText(panel, label="")
        s.Add(self.lbl_estado_adj, 0, wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_descargar = wx.Button(panel, label=_("&Descargar seleccionados"),
                                       name=_("Descargar los adjuntos marcados"))
        self.btn_descargar.Bind(wx.EVT_BUTTON, self._on_descargar_seleccionados)
        self.btn_descargar.Enable(False)
        fila.Add(self.btn_descargar, 0, wx.ALL, 4)

        self.btn_marcar = wx.Button(panel, label=_("&Marcar / desmarcar todos"),
                                    name=_("Marcar o desmarcar todos los adjuntos"))
        self.btn_marcar.Bind(wx.EVT_BUTTON, self._on_marcar_todos)
        self.btn_marcar.Enable(False)
        fila.Add(self.btn_marcar, 0, wx.ALL, 4)

        self.btn_carpeta = wx.Button(panel, label=_("Abrir &carpeta"),
                                     name=_("Abrir la carpeta del último archivo descargado"))
        self.btn_carpeta.Bind(wx.EVT_BUTTON, self._on_abrir_carpeta)
        self.btn_carpeta.Enable(False)
        fila.Add(self.btn_carpeta, 0, wx.ALL, 4)
        s.Add(fila, 0, wx.ALL, 4)
        panel.SetSizer(s)
        return panel

    def _panel_respuestas(self):
        panel = wx.Panel(self.notebook)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("Respuestas a este mensaje (Intro para verlas):")),
              0, wx.ALL, 6)
        self.lista_resp = AccessibleListCtrl(
            panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de respuestas"))
        self.lista_resp.InsertColumn(0, _("Autor"), width=170)
        self.lista_resp.InsertColumn(1, _("Contenido"), width=380)
        self.lista_resp.InsertColumn(2, _("Adjunto"), width=140)
        self.lista_resp.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_resp_activada)
        self.lista_resp.Bind(wx.EVT_CONTEXT_MENU, self._on_resp_menu)
        s.Add(self.lista_resp, 1, wx.EXPAND | wx.ALL, 6)
        panel.SetSizer(s)
        return panel

    # --- Pestaña Detalles ---
    def _cargar_detalles(self):
        mid = self.message_id

        def _bg(ctx):
            return telegram_service.obtener_detalles_mensaje(self.account_id, self.chat_id, mid)

        def _finish(res, err):
            if err or not res:
                self.txt_detalles.SetValue(
                    _("No se pudieron obtener los detalles: {0}").format(err or _("mensaje no disponible")))
                return
            self.txt_detalles.SetValue(self._formatear_detalles(res))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _formatear_detalles(self, d: dict) -> str:
        lineas = []
        def añade(etiqueta, valor):
            if valor not in (None, "", 0):
                lineas.append("{0}: {1}".format(etiqueta, valor))

        autor = d.get("autor") or _("Desconocido")
        if d.get("outgoing"):
            autor = _("Tú")
        añade(_("Autor"), autor)
        añade(_("Usuario"), d.get("autor_usuario"))
        añade(_("Identificador del autor"), d.get("autor_id"))
        añade(_("Tipo de chat"), d.get("tipo_chat"))
        fecha = d.get("fecha")
        if fecha:
            añade(_("Fecha"), "{0} ({1})".format(fecha, formatear_tiempo_relativo(fecha)))
        añade(_("Identificador del mensaje"), d.get("message_id"))
        if d.get("editado"):
            añade(_("Editado"), "{0} ({1})".format(d["editado"], formatear_tiempo_relativo(d["editado"])))
        if d.get("reenviado"):
            añade(_("Reenviado de"), d["reenviado"])
            añade(_("Fecha del original"), d.get("fecha_reenvio"))
        añade(_("Vía bot"), d.get("via_bot"))
        añade(_("Firma del autor"), d.get("firma"))
        añade(_("Vistas"), d.get("vistas"))
        añade(_("Reenvíos"), d.get("reenvios"))
        if d.get("fijado"):
            lineas.append(_("Mensaje fijado"))
        if d.get("reply_to"):
            añade(_("Es respuesta al mensaje"), d["reply_to"])

        reacciones = d.get("reacciones") or []
        if reacciones:
            partes = []
            for r in reacciones:
                marca = " ✓" if r.get("chosen") else ""
                partes.append("{0} {1}{2}".format(r.get("emoji", "?"), r.get("count", 0), marca))
            lineas.append(_("Reacciones: {0}").format("   ".join(partes)))

        media = d.get("media") or {}
        if media:
            lineas.append("")
            lineas.append(_("--- Adjunto ---"))
            añade(_("Tipo"), self._nombre_tipo(media.get("media_type")))
            añade(_("Nombre de archivo"), media.get("file_name"))
            tam = telegram_service._tamano_legible(media.get("size"))
            añade(_("Tamaño"), tam)
            dur = media.get("duration")
            if dur:
                añade(_("Duración"), self._dur(dur))
            if media.get("width") and media.get("height"):
                añade(_("Dimensiones"), "{0} x {1}".format(media["width"], media["height"]))
            añade(_("Tipo MIME"), media.get("mime"))
        if d.get("es_encuesta"):
            lineas.append(_("Este mensaje es una encuesta."))
        if d.get("es_ubicacion"):
            lineas.append(_("Este mensaje comparte una ubicación."))
        if d.get("es_contacto"):
            lineas.append(_("Este mensaje comparte un contacto."))
        if d.get("es_web_page"):
            lineas.append(_("Este mensaje incluye una vista previa de enlace."))

        # Enlaces del mensaje (hipervínculos del texto y/o vista previa web), numerados para poder
        # dictarlos/copiarlos. También aparecen como "Abrir enlace" en el menú del mensaje.
        urls = d.get("urls") or []
        if urls:
            lineas.append("")
            lineas.append(_("--- Enlaces ---"))
            for i, u in enumerate(urls, 1):
                lineas.append("{0}. {1}".format(i, u))

        texto = (d.get("texto") or "").strip()
        if texto:
            lineas.append("")
            lineas.append(_("--- Texto ---"))
            lineas.append(texto)
        return "\n".join(lineas)

    @staticmethod
    def _nombre_tipo(media_type):
        nombres = {
            "voice": _("Nota de voz"), "audio": _("Audio"), "video": _("Vídeo"),
            "video_note": _("Videomensaje"), "photo": _("Foto"), "document": _("Documento"),
            "sticker": _("Sticker"), "animation": _("GIF"),
        }
        return nombres.get(media_type, media_type or "")

    @staticmethod
    def _dur(segundos):
        try:
            s = int(segundos or 0)
        except (TypeError, ValueError):
            return ""
        return "{0}:{1:02d}".format(s // 60, s % 60) if s > 0 else ""

    # --- Pestaña Adjuntos ---
    def _cargar_adjuntos(self):
        mid = self.message_id

        def _bg(ctx):
            return telegram_service.obtener_grupo_medios(self.account_id, self.chat_id, mid)

        def _finish(res, err):
            self.clb_adjuntos.Clear()
            self._adjuntos = res or []
            self._adj_base = {}
            if err or not self._adjuntos:
                self.clb_adjuntos.Append(_("Este mensaje no tiene archivos descargables."))
                self.clb_adjuntos.Enable(False)
                self.lbl_estado_adj.SetLabel("")
                return
            for i, a in enumerate(self._adjuntos):
                etiqueta = self._etiqueta_adjunto(a, i + 1)
                self._adj_base[i] = etiqueta
                self.clb_adjuntos.Append(etiqueta)
            self.clb_adjuntos.Enable(True)
            self.btn_descargar.Enable(True)
            self.btn_marcar.Enable(True)
            self.lbl_estado_adj.SetLabel(_("{0} adjunto(s).").format(len(self._adjuntos)))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _etiqueta_adjunto(self, a: dict, num: int) -> str:
        partes = ["{0}.".format(num), self._nombre_tipo(a.get("media_type"))]
        if a.get("file_name"):
            partes.append(a["file_name"])
        tam = telegram_service._tamano_legible(a.get("size"))
        if tam:
            partes.append("({0})".format(tam))
        dur = self._dur(a.get("duration"))
        if dur:
            partes.append(dur)
        return " ".join(p for p in partes if p)

    def _on_marcar_todos(self, event):
        n = self.clb_adjuntos.GetCount()
        if not n or not self._adjuntos:
            return
        todos = all(self.clb_adjuntos.IsChecked(i) for i in range(n))
        for i in range(n):
            self.clb_adjuntos.Check(i, not todos)
        speak(_("Todos desmarcados.") if todos else _("Todos marcados."), interrupt=True)

    def _on_descargar_seleccionados(self, event):
        if self._descargando:
            speak(_("Ya hay una descarga en curso."), interrupt=True)
            return
        marcados = [i for i in range(self.clb_adjuntos.GetCount()) if self.clb_adjuntos.IsChecked(i)]
        if not marcados:
            speak(_("Marca al menos un adjunto."), interrupt=True)
            return
        pendientes = [(i, self._adjuntos[i].get("message_id")) for i in marcados
                      if self._adjuntos[i].get("message_id")]
        if not pendientes:
            return
        self._descargando = True
        self.btn_descargar.Enable(False)
        self.btn_marcar.Enable(False)
        speak(_("Descargando {0} archivo(s)...").format(len(pendientes)), interrupt=True)
        self._descargar_cadena(pendientes, total=len(pendientes), hechos=0)

    def _descargar_cadena(self, pendientes, total, hechos):
        """Descarga los adjuntos uno a uno (secuencial) mostrando el porcentaje y avisando
        de cada archivo, igual que la descarga de un único archivo."""
        if not pendientes:
            self._descargando = False
            self.btn_descargar.Enable(True)
            self.btn_marcar.Enable(True)
            notificar(_("Descarga terminada: {0} de {1} archivo(s).").format(hechos, total))
            return
        idx, mid = pendientes[0]
        resto = pendientes[1:]
        base = self._adj_base.get(idx, "")
        ultimo = {"anunciado": 0}

        def _progreso(current, total_bytes):
            if not total_bytes:
                return
            pct = int(current * 100 / total_bytes)
            self._fijar_pct(idx, base, pct)
            if pct >= ultimo["anunciado"] + 25 and pct < 100:
                ultimo["anunciado"] = pct - (pct % 25)
                speak(_("{0} por ciento").format(pct), interrupt=True)

        def _bg(ctx):
            return telegram_service.descargar_media(self.account_id, self.chat_id, mid, progreso=_progreso)

        def _finish(res, err):
            nuevos = hechos
            if err:
                self._fijar_etiqueta(idx, base + " " + _("[error]"))
                notificar(_("Error al descargar: {0}").format(err))
            elif res:
                nuevos += 1
                self._ultima_descarga = res
                self.btn_carpeta.Enable(True)
                self._fijar_etiqueta(idx, base + " " + _("(descargado)"))
                self.clb_adjuntos.Check(idx, False)
                speak(_("Archivo descargado: {0}").format(res), interrupt=True)
            else:
                self._fijar_etiqueta(idx, base + " " + _("(sin archivo)"))
            self._descargar_cadena(resto, total, nuevos)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _fijar_pct(self, idx, base, pct):
        self._fijar_etiqueta(idx, "{0}  [{1}%]".format(base, pct) if pct < 100 else base)

    def _fijar_etiqueta(self, idx, texto):
        try:
            marcado = self.clb_adjuntos.IsChecked(idx)
            self.clb_adjuntos.SetString(idx, texto)
            self.clb_adjuntos.Check(idx, marcado)
        except Exception:
            pass

    def _on_abrir_carpeta(self, event):
        if self._ultima_descarga:
            self.conv._abrir_carpeta(self._ultima_descarga)
        else:
            speak(_("Aún no has descargado ningún archivo."), interrupt=True)

    # --- Pestaña Respuestas ---
    def _cargar_respuestas(self):
        mid = self.message_id

        def _bg(ctx):
            return telegram_service.obtener_respuestas(self.account_id, self.chat_id, mid)

        def _finish(res, err):
            self.lista_resp.DeleteAllItems()
            self.lista_resp.limpiar_lecturas()
            self._respuestas = []
            if err or not res:
                return
            for i, r in enumerate(res):
                valores, meta, _rep = self.conv._valores_msg(r, i + 1)
                pos = self.lista_resp.InsertItem(i, valores.get("author", "") or "")
                self.lista_resp.SetItem(pos, 1, valores.get("content", "") or "")
                self.lista_resp.SetItem(pos, 2, valores.get("media", "") or "")
                self._respuestas.append(meta)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_resp_activada(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self._respuestas):
            meta = self._respuestas[idx]
            dlg = TelegramMensajeDialog(self.conv, self.account_id, self.chat_id, meta)
            dlg.ShowModal()
            dlg.Destroy()

    def _on_resp_menu(self, event):
        idx = self.lista_resp.GetFocusedItem()
        if not (0 <= idx < len(self._respuestas)):
            return
        meta = self._respuestas[idx]
        menu = wx.Menu()
        it_ver = menu.Append(wx.ID_ANY, _("Ver mensaje..."))
        self.Bind(wx.EVT_MENU, lambda e: self._abrir_respuesta(meta), it_ver)
        # Responder solo si en el chat de origen SE PUEDE publicar (no en un canal de solo lectura).
        if getattr(self.conv, "_puedo_publicar", True):
            it_resp = menu.Append(wx.ID_ANY, _("Responder"))
            self.Bind(wx.EVT_MENU, lambda e: self.conv._on_responder(meta), it_resp)
        self.lista_resp.PopupMenu(menu)
        menu.Destroy()

    def _abrir_respuesta(self, meta):
        dlg = TelegramMensajeDialog(self.conv, self.account_id, self.chat_id, meta)
        dlg.ShowModal()
        dlg.Destroy()
