# -*- coding: utf-8 -*-
"""Diálogos avanzados (Fase 4 de Reddit): modmail (mensajería de moderación), hilos en directo,
colecciones y borradores.

Ventanas accesibles en español; la red va por task_manager y el resultado se avisa por voz. Columnas
fijas (no plantillas): son diálogos especializados, no buffers navegables.
"""
from datetime import datetime, timezone

import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.reddit import reddit_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                   mostrar_info, confirmar, habilitar_escape_cierre)


def _fmt_fecha(epoch):
    try:
        if not epoch:
            return ""
        return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


def _ejecutar(parent, fn, ok_msg="", tras_exito=None):
    def _bg(ctx):
        return fn()

    def _fin(res, err):
        try:
            vivo = bool(parent)
        except Exception:
            vivo = False
        if err:
            notificar(_("No se pudo completar la operación: {0}").format(err))
            return
        if tras_exito and vivo:
            tras_exito(res)
        if ok_msg:
            speak(ok_msg, interrupt=True)
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


# Estados de modmail visibles (clave API -> etiqueta).
_ESTADOS = [("all", lambda: _("Todas")), ("new", lambda: _("Nuevas")),
            ("inprogress", lambda: _("En curso")), ("archived", lambda: _("Archivadas")),
            ("appeals", lambda: _("Apelaciones")), ("mod", lambda: _("De moderadores")),
            ("notifications", lambda: _("Notificaciones")),
            ("join_requests", lambda: _("Solicitudes de unión"))]


class RedditModmailDialog(wx.Dialog):
    """Modmail de un subreddit (o de todos con 'all'): conversaciones, lectura y respuesta."""

    def __init__(self, parent, account_id, subreddit="all"):
        super().__init__(parent, title=_("Modmail de r/{0}").format(subreddit), size=(760, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Modmail de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._convs = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Bandeja:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_estado = wx.Choice(self, choices=[e[1]() for e in _ESTADOS], name=_("Bandeja"))
        self.choice_estado.SetSelection(0)
        self.choice_estado.Bind(wx.EVT_CHOICE, lambda e: self._cargar())
        fila.Add(self.choice_estado, 0, wx.RIGHT, 8)
        b_nueva = wx.Button(self, label=_("&Nueva conversación..."))
        b_nueva.Bind(wx.EVT_BUTTON, lambda e: self._nueva())
        fila.Add(b_nueva, 0)
        s.Add(fila, 0, wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Conversaciones (Intro para abrir, menú contextual para más):")),
              0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Conversaciones de modmail"))
        for i, (enc, ancho) in enumerate([(_("Asunto"), 300), (_("Usuario"), 160),
                                          (_("Mensajes"), 90), (_("Actualizado"), 130)]):
            self.lista.InsertColumn(i, enc, width=ancho)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._abrir(e.GetIndex()))
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _estado(self):
        return _ESTADOS[self.choice_estado.GetSelection()][0]

    def _cargar(self):
        estado = self._estado()
        speak(_("Cargando conversaciones..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.listar_conversaciones_modmail(self.account_id, self.subreddit, estado)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar el modmail: {0}").format(err))
                return
            self._convs = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, c in enumerate(self._convs):
                pos = self.lista.InsertItem(i, c.get("subject") or _("(sin asunto)"))
                self.lista.SetItem(pos, 1, _("u/{0}").format(c.get("participant", "")))
                self.lista.SetItem(pos, 2, str(c.get("num_messages", 0)))
                self.lista.SetItem(pos, 3, _fmt_fecha(c.get("last_updated")))
                try:
                    self.lista.establecer_lectura(pos, _("{0}, de u/{1}, {2} mensajes").format(
                        c.get("subject") or _("sin asunto"), c.get("participant", ""),
                        c.get("num_messages", 0)))
                except Exception:
                    pass
            if self._convs:
                self.lista.Focus(0)
                self.lista.Select(0)
            speak(_("{0} conversaciones.").format(len(self._convs)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _sel(self):
        i = self.lista.GetFirstSelected()
        return self._convs[i] if 0 <= i < len(self._convs) else None

    def _abrir(self, idx):
        if not (0 <= idx < len(self._convs)):
            return
        c = self._convs[idx]
        sub = c.get("subreddit") or self.subreddit
        speak(_("Cargando conversación..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.leer_conversacion_modmail(self.account_id, sub, c.get("id"))

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo abrir la conversación: {0}").format(err))
                return
            lineas = [_("Asunto: {0}").format(res.get("subject", "")), ""]
            for m in res.get("messages", []):
                etq = _(" (nota interna)") if m.get("es_interno") else ""
                lineas.append(_("u/{0}{1} — {2}:").format(m.get("author", ""), etq,
                                                          _fmt_fecha(m.get("date"))))
                lineas.append(m.get("body", ""))
                lineas.append("")
            dlg = _ModmailConversacionDialog(self, res.get("subject", ""), "\n".join(lineas))
            resp = dlg.ShowModal()
            interno = dlg.interno
            texto = dlg.respuesta
            dlg.Destroy()
            if resp == wx.ID_OK and texto:
                _ejecutar(self, lambda: reddit_service.responder_modmail(
                    self.account_id, sub, c.get("id"), texto, interno), _("Respuesta enviada."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_menu(self, event):
        c = self._sel()
        if not c:
            return
        sub = c.get("subreddit") or self.subreddit
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Abrir"))
        self.Bind(wx.EVT_MENU, lambda e: self._abrir(self.lista.GetFirstSelected()), it)
        it = menu.Append(wx.ID_ANY, _("Archivar"))
        self.Bind(wx.EVT_MENU, lambda e: _ejecutar(
            self, lambda: reddit_service.archivar_modmail(self.account_id, sub, c.get("id"), True),
            _("Archivada."), tras_exito=lambda r: self._cargar()), it)
        it = menu.Append(wx.ID_ANY, _("Desarchivar"))
        self.Bind(wx.EVT_MENU, lambda e: _ejecutar(
            self, lambda: reddit_service.archivar_modmail(self.account_id, sub, c.get("id"), False),
            _("Desarchivada."), tras_exito=lambda r: self._cargar()), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _nueva(self):
        dlg = _ModmailNuevaDialog(self)
        if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
            d = dlg.resultado
            sub = self.subreddit if self.subreddit != "all" else d["subreddit"]
            if not sub or sub == "all":
                mostrar_info(self, _("Indica el subreddit de la conversación."), _("Falta el subreddit"))
            else:
                _ejecutar(self, lambda: reddit_service.crear_modmail(
                    self.account_id, sub, d["asunto"], d["cuerpo"], d["destinatario"]),
                    _("Conversación creada."), tras_exito=lambda r: self._cargar())
        dlg.Destroy()


class _ModmailConversacionDialog(wx.Dialog):
    """Muestra la conversación (solo lectura) y permite responder (con opción de nota interna)."""

    def __init__(self, parent, asunto, texto):
        super().__init__(parent, title=asunto or _("Conversación"), size=(640, 540),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Conversación de modmail"))
        self.respuesta = ""
        self.interno = False
        s = wx.BoxSizer(wx.VERTICAL)
        txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 280),
                                 name=_("Mensajes"))
        txt.SetValue(texto)
        s.Add(txt, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Tu respuesta:")), 0, wx.LEFT, 6)
        self.txt_resp = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 100),
                                           name=_("Respuesta"))
        s.Add(self.txt_resp, 0, wx.EXPAND | wx.ALL, 6)
        self.cb_interno = wx.CheckBox(self, label=_("Enviar como nota interna (solo moderadores)"),
                                      name=_("Nota interna"))
        s.Add(self.cb_interno, 0, wx.ALL, 4)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Responder"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)

    def _on_ok(self, event):
        self.respuesta = self.txt_resp.GetValue().strip()
        self.interno = self.cb_interno.GetValue()
        self.EndModal(wx.ID_OK)


class _ModmailNuevaDialog(wx.Dialog):
    """Nueva conversación de modmail. `self.resultado` = dict o None."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Nueva conversación de modmail"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Nueva conversación de modmail"))
        self.resultado = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Subreddit (sin r/; si abriste 'todos'):")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_sub = AccessibleTextCtrl(self, name=_("Subreddit"))
        s.Add(self.txt_sub, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Destinatario (usuario, sin u/):")), 0, wx.LEFT, 8)
        self.txt_dest = AccessibleTextCtrl(self, name=_("Destinatario"))
        s.Add(self.txt_dest, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Asunto:")), 0, wx.LEFT, 8)
        self.txt_asunto = AccessibleTextCtrl(self, name=_("Asunto"), max_length=100)
        s.Add(self.txt_asunto, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Mensaje:")), 0, wx.LEFT, 8)
        self.txt_cuerpo = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 120),
                                             name=_("Mensaje"))
        s.Add(self.txt_cuerpo, 0, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Enviar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_sub.SetFocus()

    def _on_ok(self, event):
        dest = self.txt_dest.GetValue().strip().lstrip("u/").lstrip("/")
        cuerpo = self.txt_cuerpo.GetValue().strip()
        if not dest or not cuerpo:
            mostrar_info(self, _("El destinatario y el mensaje son obligatorios."), _("Faltan datos"))
            return
        self.resultado = {"subreddit": self.txt_sub.GetValue().strip().lstrip("r/").lstrip("/"),
                          "destinatario": dest, "asunto": self.txt_asunto.GetValue().strip(),
                          "cuerpo": cuerpo}
        self.EndModal(wx.ID_OK)


class RedditLiveDialog(wx.Dialog):
    """Hilo en directo: crear uno nuevo o abrir uno por su identificador, ver y publicar novedades."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Hilos en directo"), size=(680, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Hilos en directo"))
        self.account_id = account_id
        self.thread_id = ""
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Identificador del hilo:")), 0,
                 wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.txt_id = AccessibleTextCtrl(self, name=_("Identificador del hilo en directo"))
        fila.Add(self.txt_id, 1, wx.EXPAND | wx.RIGHT, 6)
        b_abrir = wx.Button(self, label=_("&Abrir"))
        b_abrir.Bind(wx.EVT_BUTTON, lambda e: self._abrir())
        fila.Add(b_abrir, 0, wx.RIGHT, 4)
        b_nuevo = wx.Button(self, label=_("&Crear nuevo..."))
        b_nuevo.Bind(wx.EVT_BUTTON, lambda e: self._crear())
        fila.Add(b_nuevo, 0)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Novedades:")), 0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Novedades del hilo"))
        self.lista.InsertColumn(0, _("Autor"), width=150)
        self.lista.InsertColumn(1, _("Novedad"), width=460)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_pub = wx.Button(self, label=_("&Publicar novedad..."))
        self.btn_pub.Bind(wx.EVT_BUTTON, lambda e: self._publicar())
        self.btn_pub.Enable(False)
        b_ref = wx.Button(self, label=_("A&ctualizar"))
        b_ref.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        botones.Add(self.btn_pub, 0, wx.RIGHT, 4)
        botones.Add(b_ref, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _abrir(self):
        tid = self.txt_id.GetValue().strip()
        if tid:
            self.thread_id = tid
            self._cargar()

    def _crear(self):
        dlg = wx.Dialog(self, title=_("Nuevo hilo en directo"), style=wx.DEFAULT_DIALOG_STYLE,
                        name=_("Nuevo hilo en directo"))
        sd = wx.BoxSizer(wx.VERTICAL)
        sd.Add(wx.StaticText(dlg, label=_("Título:")), 0, wx.LEFT | wx.TOP, 8)
        t_tit = AccessibleTextCtrl(dlg, name=_("Título"))
        sd.Add(t_tit, 0, wx.EXPAND | wx.ALL, 6)
        sd.Add(wx.StaticText(dlg, label=_("Descripción (opcional):")), 0, wx.LEFT, 8)
        t_desc = AccessibleTextCtrl(dlg, style=wx.TE_MULTILINE, size=(-1, 90), name=_("Descripción"))
        sd.Add(t_desc, 0, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        bs.Add(wx.Button(dlg, wx.ID_OK, _("&Aceptar")), 0, wx.ALL, 4)
        bs.Add(wx.Button(dlg, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        sd.Add(bs, 0, wx.ALIGN_CENTER)
        dlg.SetSizer(sd); dlg.Fit(); dlg.CenterOnParent(); t_tit.SetFocus()
        if dlg.ShowModal() == wx.ID_OK and t_tit.GetValue().strip():
            titulo = t_tit.GetValue().strip()
            desc = t_desc.GetValue().strip()

            def _tras(nuevo_id):
                if self and nuevo_id:
                    self.thread_id = nuevo_id
                    self.txt_id.SetValue(nuevo_id)
                    self._cargar()
            _ejecutar(self, lambda: reddit_service.crear_live_thread(self.account_id, titulo, desc),
                      _("Hilo creado."), tras_exito=_tras)
        dlg.Destroy()

    def _cargar(self):
        if not self.thread_id:
            return
        self.btn_pub.Enable(True)
        speak(_("Cargando novedades..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.leer_updates_live(self.account_id, self.thread_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar el hilo: {0}").format(err))
                return
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, u in enumerate(res or []):
                texto = (u.get("body", "") or "").replace("\n", " ")
                if u.get("stricken"):
                    texto = _("[tachada] ") + texto
                pos = self.lista.InsertItem(i, _("u/{0}").format(u.get("author", "")))
                self.lista.SetItem(pos, 1, texto)
                try:
                    self.lista.establecer_lectura(pos, _("u/{0}: {1}").format(u.get("author", ""), texto))
                except Exception:
                    pass
            speak(_("{0} novedades.").format(len(res or [])), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _publicar(self):
        if not self.thread_id:
            return
        dlg = CustomTextEntryDialog(self, _("Texto de la novedad:"), _("Publicar novedad"),
                                    multiline=True)
        if dlg.ShowModal() == wx.ID_OK:
            texto = dlg.GetValue().strip()
            if texto:
                _ejecutar(self, lambda: reddit_service.publicar_update_live(
                    self.account_id, self.thread_id, texto), _("Novedad publicada."),
                    tras_exito=lambda r: self._cargar())
        dlg.Destroy()


class RedditCollectionsDialog(wx.Dialog):
    """Colecciones de un subreddit: listar, crear, ver contenido y borrar."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Colecciones de r/{0}").format(subreddit), size=(640, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Colecciones de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._cols = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Colecciones (Intro para ver su contenido):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Colecciones"))
        self.lista.InsertColumn(0, _("Título"), width=280)
        self.lista.InsertColumn(1, _("Publicaciones"), width=120)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._ver(e.GetIndex()))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_add = wx.Button(self, label=_("&Nueva colección..."))
        b_add.Bind(wx.EVT_BUTTON, lambda e: self._nueva())
        b_del = wx.Button(self, label=_("&Borrar"))
        b_del.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        botones.Add(b_add, 0, wx.RIGHT, 4)
        botones.Add(b_del, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _cargar(self):
        speak(_("Cargando colecciones..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.listar_collections(self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las colecciones: {0}").format(err))
                return
            self._cols = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, c in enumerate(self._cols):
                pos = self.lista.InsertItem(i, c.get("title") or _("(sin título)"))
                self.lista.SetItem(pos, 1, str(c.get("num_links", 0)))
                try:
                    self.lista.establecer_lectura(pos, _("{0}, {1} publicaciones").format(
                        c.get("title") or _("sin título"), c.get("num_links", 0)))
                except Exception:
                    pass
            if self._cols:
                self.lista.Focus(0)
                self.lista.Select(0)
            speak(_("{0} colecciones.").format(len(self._cols)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _sel(self):
        i = self.lista.GetFirstSelected()
        return self._cols[i] if 0 <= i < len(self._cols) else None

    def _ver(self, idx):
        if not (0 <= idx < len(self._cols)):
            return
        c = self._cols[idx]
        speak(_("Cargando..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.leer_collection(self.account_id, self.subreddit, c.get("id"))

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo abrir la colección: {0}").format(err))
                return
            lineas = [res.get("title", ""), res.get("description", ""), ""]
            for p in res.get("posts", []):
                lineas.append(_("• {0} (u/{1})").format(p.get("title", ""), p.get("author", "")))
            mostrar_info(self, "\n".join(lineas).strip(),
                         _("Colección: {0}").format(res.get("title", "")))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _nueva(self):
        dlg = wx.Dialog(self, title=_("Nueva colección"), style=wx.DEFAULT_DIALOG_STYLE,
                        name=_("Nueva colección"))
        sd = wx.BoxSizer(wx.VERTICAL)
        sd.Add(wx.StaticText(dlg, label=_("Título:")), 0, wx.LEFT | wx.TOP, 8)
        t_tit = AccessibleTextCtrl(dlg, name=_("Título"))
        sd.Add(t_tit, 0, wx.EXPAND | wx.ALL, 6)
        sd.Add(wx.StaticText(dlg, label=_("Descripción (opcional):")), 0, wx.LEFT, 8)
        t_desc = AccessibleTextCtrl(dlg, style=wx.TE_MULTILINE, size=(-1, 90), name=_("Descripción"))
        sd.Add(t_desc, 0, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        bs.Add(wx.Button(dlg, wx.ID_OK, _("&Aceptar")), 0, wx.ALL, 4)
        bs.Add(wx.Button(dlg, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        sd.Add(bs, 0, wx.ALIGN_CENTER)
        dlg.SetSizer(sd); dlg.Fit(); dlg.CenterOnParent(); t_tit.SetFocus()
        if dlg.ShowModal() == wx.ID_OK and t_tit.GetValue().strip():
            titulo, desc = t_tit.GetValue().strip(), t_desc.GetValue().strip()
            _ejecutar(self, lambda: reddit_service.crear_collection(
                self.account_id, self.subreddit, titulo, desc), _("Colección creada."),
                tras_exito=lambda r: self._cargar())
        dlg.Destroy()

    def _borrar(self):
        c = self._sel()
        if not c:
            speak(_("Selecciona una colección."), interrupt=True)
            return
        if confirmar(self, _("¿Borrar la colección «{0}»?").format(c.get("title", "")),
                     _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: reddit_service.borrar_collection(
            self.account_id, self.subreddit, c.get("id")), _("Colección borrada."),
            tras_exito=lambda r: self._cargar())


class RedditDraftsDialog(wx.Dialog):
    """Borradores de publicaciones: listar, crear, enviar y borrar."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Borradores"), size=(640, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Borradores"))
        self.account_id = account_id
        self._drafts = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Borradores:")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de borradores"))
        self.lista.InsertColumn(0, _("Título"), width=280)
        self.lista.InsertColumn(1, _("Comunidad"), width=160)
        self.lista.InsertColumn(2, _("Tipo"), width=100)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_add = wx.Button(self, label=_("&Nuevo..."))
        b_add.Bind(wx.EVT_BUTTON, lambda e: self._nuevo())
        b_env = wx.Button(self, label=_("&Publicar"))
        b_env.Bind(wx.EVT_BUTTON, lambda e: self._enviar())
        b_del = wx.Button(self, label=_("&Borrar"))
        b_del.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        for b in (b_add, b_env, b_del):
            botones.Add(b, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _cargar(self):
        speak(_("Cargando borradores..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.listar_drafts(self.account_id)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los borradores: {0}").format(err))
                return
            self._drafts = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, d in enumerate(self._drafts):
                pos = self.lista.InsertItem(i, d.get("title") or _("(sin título)"))
                self.lista.SetItem(pos, 1, _("r/{0}").format(d.get("subreddit", "")) if d.get("subreddit") else "")
                self.lista.SetItem(pos, 2, _("Enlace") if d.get("is_link") else _("Texto"))
                try:
                    self.lista.establecer_lectura(pos, _("{0}, en r/{1}").format(
                        d.get("title") or _("sin título"), d.get("subreddit", "")))
                except Exception:
                    pass
            if self._drafts:
                self.lista.Focus(0)
                self.lista.Select(0)
            speak(_("{0} borradores.").format(len(self._drafts)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _sel(self):
        i = self.lista.GetFirstSelected()
        return self._drafts[i] if 0 <= i < len(self._drafts) else None

    def _nuevo(self):
        dlg = _DraftNuevoDialog(self)
        if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
            d = dlg.resultado
            _ejecutar(self, lambda: reddit_service.crear_draft(
                self.account_id, d["subreddit"], d["title"], d["selftext"], d["url"]),
                _("Borrador guardado."), tras_exito=lambda r: self._cargar())
        dlg.Destroy()

    def _enviar(self):
        d = self._sel()
        if not d:
            speak(_("Selecciona un borrador."), interrupt=True)
            return
        if confirmar(self, _("¿Publicar el borrador «{0}»?").format(d.get("title", "")),
                     _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: reddit_service.enviar_draft(self.account_id, d.get("id")),
                  _("Borrador publicado."), tras_exito=lambda r: self._cargar())

    def _borrar(self):
        d = self._sel()
        if not d:
            speak(_("Selecciona un borrador."), interrupt=True)
            return
        if confirmar(self, _("¿Borrar el borrador «{0}»?").format(d.get("title", "")),
                     _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: reddit_service.borrar_draft(self.account_id, d.get("id")),
                  _("Borrador eliminado."), tras_exito=lambda r: self._cargar())


class _DraftNuevoDialog(wx.Dialog):
    """Nuevo borrador (texto o enlace). `self.resultado` = dict o None."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Nuevo borrador"), style=wx.DEFAULT_DIALOG_STYLE,
                         name=_("Nuevo borrador"))
        self.resultado = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Comunidad (subreddit, sin r/; opcional):")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_sub = AccessibleTextCtrl(self, name=_("Comunidad"))
        s.Add(self.txt_sub, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Título:")), 0, wx.LEFT, 8)
        self.txt_tit = AccessibleTextCtrl(self, name=_("Título"), max_length=300)
        s.Add(self.txt_tit, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Enlace (URL; déjalo vacío para un borrador de texto):")),
              0, wx.LEFT, 8)
        self.txt_url = AccessibleTextCtrl(self, name=_("Enlace URL"))
        s.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Texto (si no es enlace):")), 0, wx.LEFT, 8)
        self.txt_cuerpo = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 120),
                                             name=_("Texto"))
        s.Add(self.txt_cuerpo, 0, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_tit.SetFocus()

    def _on_ok(self, event):
        titulo = self.txt_tit.GetValue().strip()
        if not titulo:
            mostrar_info(self, _("El título es obligatorio."), _("Falta el título"))
            return
        self.resultado = {"subreddit": self.txt_sub.GetValue().strip().lstrip("r/").lstrip("/"),
                          "title": titulo, "url": self.txt_url.GetValue().strip(),
                          "selftext": self.txt_cuerpo.GetValue()}
        self.EndModal(wx.ID_OK)
