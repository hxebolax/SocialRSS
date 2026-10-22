# -*- coding: utf-8 -*-
"""Diálogos de personalización de comunidades (Fase 3 de Reddit): wiki, etiquetas (flair), emojis y
widgets de la barra lateral.

Ventanas accesibles en español; la red va por task_manager y el resultado se avisa por voz. Se usan
columnas fijas (no plantillas): son diálogos especializados, no buffers navegables.
"""
from datetime import datetime, timezone

import wx

from app.core.speech import speak, notificar
from app.core.privacy import abrir_url
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
    """Ejecuta una operación en segundo plano y avisa del resultado (comprueba que el diálogo vive)."""
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


class RedditWikiDialog(wx.Dialog):
    """Wiki de un subreddit: lista de páginas, contenido, edición, creación y revisiones."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Wiki de r/{0}").format(subreddit), size=(760, 580),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Wiki de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._paginas = []
        self._actual = None  # dict de la página cargada
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar_paginas()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Páginas (elige una para ver su contenido):")), 0, wx.ALL, 6)
        self.lst = wx.ListBox(self, name=_("Páginas de la wiki"))
        self.lst.Bind(wx.EVT_LISTBOX, lambda e: self._abrir_pagina())
        s.Add(self.lst, 0, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Contenido:")), 0, wx.LEFT, 6)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 260),
                                      name=_("Contenido de la página"))
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 6)
        self.lbl_meta = wx.StaticText(self, label="")
        s.Add(self.lbl_meta, 0, wx.LEFT | wx.BOTTOM, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_guardar = wx.Button(self, label=_("&Guardar cambios"))
        self.btn_guardar.Bind(wx.EVT_BUTTON, lambda e: self._guardar())
        b_nueva = wx.Button(self, label=_("Página &nueva..."))
        b_nueva.Bind(wx.EVT_BUTTON, lambda e: self._nueva_pagina())
        b_rev = wx.Button(self, label=_("Ver &revisiones"))
        b_rev.Bind(wx.EVT_BUTTON, lambda e: self._revisiones())
        for b in (self.btn_guardar, b_nueva, b_rev):
            botones.Add(b, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.btn_guardar.Enable(False)

    def _cargar_paginas(self):
        speak(_("Cargando páginas..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.listar_paginas_wiki(self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar la wiki: {0}").format(err))
                return
            self._paginas = res or []
            self.lst.Set(self._paginas)
            if self._paginas:
                self.lst.SetSelection(0)
                self._abrir_pagina()
            else:
                speak(_("Esta comunidad no tiene páginas wiki."), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _pagina_sel(self):
        i = self.lst.GetSelection()
        return self._paginas[i] if 0 <= i < len(self._paginas) else None

    def _abrir_pagina(self):
        page = self._pagina_sel()
        if not page:
            return
        speak(_("Cargando {0}...").format(page), interrupt=True)

        def _bg(ctx):
            return reddit_service.leer_pagina_wiki(self.account_id, self.subreddit, page)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo abrir la página: {0}").format(err))
                return
            self._actual = res
            self.txt.SetValue(res.get("content_md", ""))
            puede = bool(res.get("may_revise"))
            self.btn_guardar.Enable(puede)
            meta = _("Última edición: {0}").format(_fmt_fecha(res.get("revision_date")))
            if res.get("revision_by"):
                meta += _(" por u/{0}").format(res["revision_by"])
            if not puede:
                meta += _("  (solo lectura)")
            self.lbl_meta.SetLabel(meta)
            speak(_("Página cargada."), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _guardar(self):
        if not self._actual:
            return
        page = self._actual.get("name")
        contenido = self.txt.GetValue()
        dlg = CustomTextEntryDialog(self, _("Motivo del cambio (opcional):"), _("Guardar página"))
        motivo = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        _ejecutar(self, lambda: reddit_service.editar_pagina_wiki(
            self.account_id, self.subreddit, page, contenido, motivo), _("Página guardada."))

    def _nueva_pagina(self):
        dlg = CustomTextEntryDialog(self, _("Nombre de la nueva página (p. ej. reglas/spam):"),
                                    _("Página nueva"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        nombre = dlg.GetValue().strip().strip("/")
        dlg.Destroy()
        if not nombre:
            return
        _ejecutar(self, lambda: reddit_service.crear_pagina_wiki(
            self.account_id, self.subreddit, nombre, _("(nueva página)")),
            _("Página creada."), tras_exito=lambda r: self._cargar_paginas())

    def _revisiones(self):
        page = self._pagina_sel()
        if not page:
            return
        speak(_("Cargando revisiones..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.revisiones_wiki(self.account_id, self.subreddit, page)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las revisiones: {0}").format(err))
                return
            if not res:
                mostrar_info(self, _("No hay revisiones."), _("Revisiones de {0}").format(page))
                return
            lineas = [_("{0} — u/{1}: {2}").format(_fmt_fecha(r.get("timestamp")), r.get("author", ""),
                      r.get("reason") or _("(sin motivo)")) for r in res]
            mostrar_info(self, "\n".join(lineas), _("Revisiones de {0}").format(page))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class RedditFlairsDialog(wx.Dialog):
    """Etiquetas (flair) de un subreddit: plantillas de usuario/publicación y etiqueta propia."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Etiquetas de r/{0}").format(subreddit), size=(680, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Etiquetas (flair) de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._filas = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Tipo:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_tipo = wx.Choice(self, choices=[_("Etiquetas de usuario"),
                                                    _("Etiquetas de publicación")],
                                     name=_("Tipo de etiqueta"))
        self.choice_tipo.SetSelection(0)
        self.choice_tipo.Bind(wx.EVT_CHOICE, lambda e: self._cargar())
        fila.Add(self.choice_tipo, 0)
        s.Add(fila, 0, wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Plantillas:")), 0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Plantillas de etiqueta"))
        self.lista.InsertColumn(0, _("Texto"), width=280)
        self.lista.InsertColumn(1, _("Solo moderadores"), width=140)
        self.lista.InsertColumn(2, _("Editable"), width=120)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_add = wx.Button(self, label=_("&Nueva plantilla..."))
        b_add.Bind(wx.EVT_BUTTON, lambda e: self._nueva())
        b_del = wx.Button(self, label=_("&Borrar plantilla"))
        b_del.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        self.btn_mia = wx.Button(self, label=_("Establecer &mi etiqueta..."))
        self.btn_mia.Bind(wx.EVT_BUTTON, lambda e: self._mi_flair())
        self.btn_asig = wx.Button(self, label=_("&Asignar a un usuario..."))
        self.btn_asig.Bind(wx.EVT_BUTTON, lambda e: self._asignar())
        for b in (b_add, b_del, self.btn_mia, self.btn_asig):
            botones.Add(b, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _es_link(self):
        return self.choice_tipo.GetSelection() == 1

    def _cargar(self):
        es_link = self._es_link()
        # Etiqueta propia y asignación solo aplican a etiquetas de usuario.
        self.btn_mia.Enable(not es_link)
        self.btn_asig.Enable(not es_link)
        speak(_("Cargando..."), interrupt=True)
        fn = (reddit_service.plantillas_flair_enlace if es_link
              else reddit_service.plantillas_flair_usuario)

        def _bg(ctx):
            return fn(self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las plantillas: {0}").format(err))
                return
            self._filas = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, t in enumerate(self._filas):
                pos = self.lista.InsertItem(i, t.get("text") or _("(sin texto)"))
                self.lista.SetItem(pos, 1, _("Sí") if t.get("mod_only") else _("No"))
                self.lista.SetItem(pos, 2, _("Sí") if t.get("text_editable") else _("No"))
                try:
                    self.lista.establecer_lectura(pos, _("{0}. {1}").format(
                        t.get("text") or _("sin texto"),
                        _("solo moderadores") if t.get("mod_only") else _("todos")))
                except Exception:
                    pass
            if self._filas:
                self.lista.Focus(0)
                self.lista.Select(0)
            speak(_("{0} plantillas.").format(len(self._filas)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _sel(self):
        i = self.lista.GetFirstSelected()
        return self._filas[i] if 0 <= i < len(self._filas) else None

    def _nueva(self):
        dlg = _NuevaPlantillaFlairDialog(self)
        if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
            d = dlg.resultado
            tipo = "link" if self._es_link() else "user"
            _ejecutar(self, lambda: reddit_service.crear_plantilla_flair(
                self.account_id, self.subreddit, d["text"], tipo=tipo, mod_only=d["mod_only"],
                text_editable=d["editable"]), _("Plantilla creada."),
                tras_exito=lambda r: self._cargar())
        dlg.Destroy()

    def _borrar(self):
        t = self._sel()
        if not t:
            speak(_("Selecciona una plantilla."), interrupt=True)
            return
        if confirmar(self, _("¿Borrar la plantilla «{0}»?").format(t.get("text") or ""),
                     _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: reddit_service.borrar_plantilla_flair(
            self.account_id, self.subreddit, t.get("id", "")), _("Plantilla borrada."),
            tras_exito=lambda r: self._cargar())

    def _mi_flair(self):
        dlg = CustomTextEntryDialog(self, _("Texto de tu etiqueta (vacío para quitarla):"),
                                    _("Mi etiqueta"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        _ejecutar(self, lambda: reddit_service.establecer_mi_flair(
            self.account_id, self.subreddit, text=texto), _("Etiqueta actualizada."))

    def _asignar(self):
        dlg = CustomTextEntryDialog(self, _("Usuario (sin u/):"), _("Asignar etiqueta"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        usuario = dlg.GetValue().strip()
        dlg.Destroy()
        if not usuario:
            return
        dlg2 = CustomTextEntryDialog(self, _("Texto de la etiqueta (vacío para quitarla):"),
                                     _("Asignar etiqueta a u/{0}").format(usuario))
        if dlg2.ShowModal() != wx.ID_OK:
            dlg2.Destroy()
            return
        texto = dlg2.GetValue().strip()
        dlg2.Destroy()
        if texto:
            _ejecutar(self, lambda: reddit_service.asignar_flair_usuario(
                self.account_id, self.subreddit, usuario, text=texto), _("Etiqueta asignada."))
        else:
            _ejecutar(self, lambda: reddit_service.quitar_flair_usuario(
                self.account_id, self.subreddit, usuario), _("Etiqueta quitada."))


class _NuevaPlantillaFlairDialog(wx.Dialog):
    """Datos de una plantilla de flair nueva. `self.resultado` = dict o None."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Nueva plantilla de etiqueta"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Nueva plantilla de etiqueta"))
        self.resultado = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Texto de la etiqueta:")), 0, wx.LEFT | wx.TOP, 8)
        self.txt = AccessibleTextCtrl(self, name=_("Texto de la etiqueta"), max_length=64)
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 6)
        self.cb_mod = wx.CheckBox(self, label=_("Solo la pueden usar los moderadores"),
                                  name=_("Solo moderadores"))
        self.cb_edit = wx.CheckBox(self, label=_("El usuario puede editar el texto"),
                                   name=_("Texto editable"))
        s.Add(self.cb_mod, 0, wx.ALL, 4)
        s.Add(self.cb_edit, 0, wx.ALL, 4)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt.SetFocus()

    def _on_ok(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            mostrar_info(self, _("Escribe el texto de la etiqueta."), _("Falta el texto"))
            return
        self.resultado = {"text": texto, "mod_only": self.cb_mod.GetValue(),
                          "editable": self.cb_edit.GetValue()}
        self.EndModal(wx.ID_OK)


class RedditEmojisDialog(wx.Dialog):
    """Emojis personalizados de un subreddit: listar, añadir y borrar."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Emojis de r/{0}").format(subreddit), size=(560, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Emojis de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._emojis = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Emojis:")), 0, wx.LEFT | wx.TOP, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de emojis"))
        self.lista.InsertColumn(0, _("Nombre"), width=220)
        self.lista.InsertColumn(1, _("Solo moderadores"), width=160)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_add = wx.Button(self, label=_("&Añadir..."))
        b_add.Bind(wx.EVT_BUTTON, lambda e: self._añadir())
        b_del = wx.Button(self, label=_("&Borrar"))
        b_del.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        botones.Add(b_add, 0, wx.RIGHT, 4)
        botones.Add(b_del, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _cargar(self):
        speak(_("Cargando emojis..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.listar_emojis(self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los emojis: {0}").format(err))
                return
            self._emojis = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, e in enumerate(self._emojis):
                pos = self.lista.InsertItem(i, e.get("name", ""))
                self.lista.SetItem(pos, 1, _("Sí") if e.get("mod_flair_only") else _("No"))
                try:
                    self.lista.establecer_lectura(pos, _(":{0}:").format(e.get("name", "")))
                except Exception:
                    pass
            if self._emojis:
                self.lista.Focus(0)
                self.lista.Select(0)
            speak(_("{0} emojis.").format(len(self._emojis)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _añadir(self):
        dlg = CustomTextEntryDialog(self, _("Nombre del emoji (sin dos puntos):"), _("Nuevo emoji"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        nombre = dlg.GetValue().strip().strip(":")
        dlg.Destroy()
        if not nombre:
            return
        wildcard = _("Imágenes") + " (*.png;*.jpg;*.jpeg;*.gif)|*.png;*.jpg;*.jpeg;*.gif"
        with wx.FileDialog(self, _("Imagen del emoji"), wildcard=wildcard,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fd:
            if fd.ShowModal() != wx.ID_OK:
                return
            ruta = fd.GetPath()
        _ejecutar(self, lambda: reddit_service.añadir_emoji(
            self.account_id, self.subreddit, nombre, ruta), _("Emoji añadido."),
            tras_exito=lambda r: self._cargar())

    def _borrar(self):
        i = self.lista.GetFirstSelected()
        if i < 0 or i >= len(self._emojis):
            speak(_("Selecciona un emoji."), interrupt=True)
            return
        nombre = self._emojis[i].get("name", "")
        if confirmar(self, _("¿Borrar el emoji «{0}»?").format(nombre), _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar(self, lambda: reddit_service.borrar_emoji(
            self.account_id, self.subreddit, nombre), _("Emoji borrado."),
            tras_exito=lambda r: self._cargar())


class RedditWidgetsDialog(wx.Dialog):
    """Widgets de la barra lateral de un subreddit (solo lectura)."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Barra lateral de r/{0}").format(subreddit), size=(600, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Widgets de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._widgets = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Elementos de la barra lateral:")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Widgets"))
        self.lista.InsertColumn(0, _("Nombre"), width=200)
        self.lista.InsertColumn(1, _("Tipo"), width=140)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._ver(e.GetIndex()))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Contenido del elemento seleccionado:")), 0, wx.LEFT, 6)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 150),
                                      name=_("Contenido del widget"))
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self._ver(e.GetIndex()))

    def _cargar(self):
        speak(_("Cargando la barra lateral..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.listar_widgets_sidebar(self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar la barra lateral: {0}").format(err))
                return
            self._widgets = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, w in enumerate(self._widgets):
                nombre = w.get("shortName") or w.get("kind") or _("(elemento)")
                pos = self.lista.InsertItem(i, nombre)
                self.lista.SetItem(pos, 1, w.get("kind", ""))
                try:
                    self.lista.establecer_lectura(pos, _("{0}, tipo {1}").format(nombre, w.get("kind", "")))
                except Exception:
                    pass
            if self._widgets:
                self.lista.Focus(0)
                self.lista.Select(0)
            speak(_("{0} elementos.").format(len(self._widgets)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _ver(self, idx):
        if 0 <= idx < len(self._widgets):
            self.txt.SetValue(self._widgets[idx].get("text", "") or _("(sin texto)"))
