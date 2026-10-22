# -*- coding: utf-8 -*-
"""
Diálogos de Mastodon para gestionar LISTAS y FILTROS DEL SERVIDOR.
"""

import wx
from app.core.speech import speak, notificar
from app.core.config_manager import config
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog


class ListasDialog(wx.Dialog):
    """Gestiona las listas de Mastodon: crear, eliminar y administrar miembros."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Listas de Mastodon"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Diálogo de listas de Mastodon"))
        self.account_id = account_id
        self.listas = []
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Tus listas:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de listas de Mastodon"))
        self.lista.InsertColumn(0, _("Título"), width=440)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b1 = wx.Button(self, label=_("&Crear lista..."))
        b1.Bind(wx.EVT_BUTTON, self._on_crear)
        btns.Add(b1, 0, wx.ALL, 5)
        b_ed = wx.Button(self, label=_("Edi&tar..."))
        b_ed.Bind(wx.EVT_BUTTON, self._on_editar)
        btns.Add(b_ed, 0, wx.ALL, 5)
        b2 = wx.Button(self, label=_("&Eliminar"))
        b2.Bind(wx.EVT_BUTTON, self._on_eliminar)
        btns.Add(b2, 0, wx.ALL, 5)
        b3 = wx.Button(self, label=_("&Miembros..."))
        b3.Bind(wx.EVT_BUTTON, self._on_miembros)
        btns.Add(b3, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _cargar(self):
        speak(_("Cargando listas..."), interrupt=True)

        def _fin(res, err):
            if err:
                speak(_("No se pudieron cargar las listas: {0}").format(err), interrupt=True)
                return
            self.listas = res or []
            self.lista.DeleteAllItems()
            for i, l in enumerate(self.listas):
                self.lista.InsertItem(i, l["title"])
            config.set("mastodon", "lists_cache", self.listas)
            if not self.listas:
                speak(_("No tienes listas."), interrupt=True)
        mastodon_service.obtener_listas_async(self.account_id, callback=_fin)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.listas):
            return None
        return self.listas[idx]

    def _on_crear(self, event):
        dlg = CustomTextEntryDialog(self, _("Nombre de la nueva lista:"), _("Crear lista"))
        if dlg.ShowModal() == wx.ID_OK:
            titulo = dlg.GetValue().strip()
            if titulo:
                def _fin(res, err):
                    if err:
                        notificar(_("No se pudo crear: {0}").format(err))
                    else:
                        notificar(_("Lista creada."))
                        self._cargar()
                mastodon_service.crear_lista_async(self.account_id, titulo, callback=_fin)
        dlg.Destroy()

    def _on_eliminar(self, event):
        l = self._sel()
        if not l:
            return
        dlg = wx.MessageDialog(self, _("¿Eliminar la lista '{0}'?").format(l["title"]),
                               _("Confirmar"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if dlg.ShowModal() == wx.ID_YES:
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo eliminar: {0}").format(err))
                else:
                    notificar(_("Lista eliminada."))
                    self._cargar()
            mastodon_service.borrar_lista_async(self.account_id, l["id"], callback=_fin)
        dlg.Destroy()

    def _on_editar(self, event):
        l = self._sel()
        if not l:
            return
        dlg = EditarListaDialog(self, l)
        if dlg.ShowModal() == wx.ID_OK:
            datos = dlg.obtener_datos()

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo editar la lista: {0}").format(err))
                else:
                    notificar(_("Lista actualizada."))
                    self._cargar()
            mastodon_service.actualizar_lista_async(
                self.account_id, l["id"], datos["title"],
                replies_policy=datos["replies_policy"], exclusive=datos["exclusive"], callback=_fin)
        dlg.Destroy()

    def _on_miembros(self, event):
        l = self._sel()
        if not l:
            return
        dlg = MiembrosListaDialog(self, self.account_id, l["id"], l["title"])
        dlg.ShowModal()
        dlg.Destroy()


class EditarListaDialog(wx.Dialog):
    """Editar una lista de Mastodon: nombre, política de respuestas y exclusividad."""

    _POLITICAS = [("list", _("Solo de miembros de la lista")),
                  ("followed", _("De cualquiera a quien sigo")),
                  ("none", _("No mostrar respuestas"))]

    def __init__(self, parent, lista):
        super().__init__(parent, title=_("Editar lista"), style=wx.DEFAULT_DIALOG_STYLE,
                         name=_("Editar lista"))
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Nombre de la lista:")), 0, wx.ALL, 5)
        self.txt_titulo = AccessibleTextCtrl(self, name=_("Nombre de la lista"))
        self.txt_titulo.SetValue(lista.get("title", "") or "")
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("&Mostrar respuestas de:")), 0, wx.ALL, 5)
        self.choice_pol = wx.Choice(self, choices=[n for _c, n in self._POLITICAS],
                                    name=_("Política de respuestas de la lista"))
        codigos = [c for c, _n in self._POLITICAS]
        pol = lista.get("replies_policy", "list") or "list"
        self.choice_pol.SetSelection(codigos.index(pol) if pol in codigos else 0)
        s.Add(self.choice_pol, 0, wx.EXPAND | wx.ALL, 5)

        self.cb_excl = wx.CheckBox(self, label=_("Exclusiva (sus miembros no aparecen también en Inicio)"),
                                   name=_("Lista exclusiva"))
        self.cb_excl.SetValue(bool(lista.get("exclusive", False)))
        s.Add(self.cb_excl, 0, wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.SetDefault()
        btns.Add(b_ok, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizerAndFit(s)
        self.CenterOnParent()
        self.txt_titulo.SetFocus()

    def obtener_datos(self):
        idx = self.choice_pol.GetSelection()
        pol = self._POLITICAS[idx][0] if 0 <= idx < len(self._POLITICAS) else "list"
        return {"title": self.txt_titulo.GetValue().strip(),
                "replies_policy": pol, "exclusive": self.cb_excl.GetValue()}


class MiembrosListaDialog(wx.Dialog):
    """Añade o quita miembros de una lista de Mastodon."""

    def __init__(self, parent, account_id, list_id, titulo):
        super().__init__(parent, title=_("Miembros de {0}").format(titulo), size=(520, 400),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Diálogo de miembros de lista"))
        self.account_id = account_id
        self.list_id = list_id
        self.miembros = []
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Miembros actuales:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Miembros de la lista"))
        self.lista.InsertColumn(0, _("Nombre"), width=200)
        self.lista.InsertColumn(1, _("Usuario"), width=260)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b0 = wx.Button(self, label=_("Añadir desde los que &sigo..."))
        b0.Bind(wx.EVT_BUTTON, self._on_anadir_desde_seguidos)
        btns.Add(b0, 0, wx.ALL, 5)
        b1 = wx.Button(self, label=_("&Añadir por usuario..."))
        b1.Bind(wx.EVT_BUTTON, self._on_anadir)
        btns.Add(b1, 0, wx.ALL, 5)
        b2 = wx.Button(self, label=_("&Quitar"))
        b2.Bind(wx.EVT_BUTTON, self._on_quitar)
        btns.Add(b2, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _cargar(self):
        def _fin(res, err):
            if err:
                speak(_("No se pudieron cargar los miembros: {0}").format(err), interrupt=True)
                return
            self.miembros = res or []
            self.lista.DeleteAllItems()
            for i, m in enumerate(self.miembros):
                self.lista.InsertItem(i, m["display_name"])
                self.lista.SetItem(i, 1, "@" + m["acct"])
            if not self.miembros:
                speak(_("La lista no tiene miembros."), interrupt=True)
        mastodon_service.obtener_miembros_lista_async(self.account_id, self.list_id, callback=_fin)

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Usuario a añadir (acct, ej. nombre@instancia):"), _("Añadir miembro"))
        if dlg.ShowModal() == wx.ID_OK:
            acct = dlg.GetValue().strip()
            if acct:
                self._anadir_acct(acct)
        dlg.Destroy()

    def _anadir_acct(self, acct):
        """Añade un acct a la lista. Mastodon solo permite añadir a cuentas que SIGUES; si
        no la sigues (o el acct no existe), el aviso lo explica."""
        def _fin(res, err):
            if err:
                # Mensaje claro: la causa más habitual de que 'no funcione' es no seguir aún
                # a esa cuenta, requisito de Mastodon para meterla en una lista.
                notificar(_("No se pudo añadir a {0}. Recuerda que en Mastodon solo puedes "
                            "añadir a una lista a cuentas que SIGUES, y el usuario debe existir "
                            "(escríbelo como nombre@instancia). Detalle: {1}").format(acct, err))
            else:
                notificar(_("Miembro añadido."))
                self._cargar()
        mastodon_service.gestionar_miembro_lista_async(self.account_id, self.list_id, acct, True, callback=_fin)

    def _on_anadir_desde_seguidos(self, event):
        """Carga las cuentas que sigues y deja elegir varias para añadirlas a la lista (más
        cómodo y fiable que escribir el acct a mano)."""
        speak(_("Cargando las cuentas que sigues..."), interrupt=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar las cuentas que sigues: {0}").format(err))
                return
            seguidos = res or []
            if not seguidos:
                from .accessible_controls import mostrar_info
                mostrar_info(self, _("No sigues a ninguna cuenta todavía, o no se han podido cargar."),
                             _("Añadir desde los que sigo"))
                return
            etiquetas = ["{0} (@{1})".format(c.get("display_name") or c.get("acct", ""), c.get("acct", ""))
                         for c in seguidos]
            sel = wx.MultiChoiceDialog(self, _("Marca las cuentas que quieres añadir a la lista:"),
                                       _("Añadir desde los que sigo"), etiquetas)
            if sel.ShowModal() == wx.ID_OK:
                indices = sel.GetSelections()
                for i in indices:
                    self._anadir_acct(seguidos[i].get("acct", ""))
                if indices:
                    notificar(_("Añadiendo {0} cuenta(s) a la lista...").format(len(indices)))
            sel.Destroy()

        mastodon_service.obtener_relaciones_async(self.account_id, "following", callback=_fin)

    def _on_quitar(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.miembros):
            return
        m = self.miembros[idx]

        def _fin(res, err):
            if err:
                notificar(_("No se pudo quitar: {0}").format(err))
            else:
                notificar(_("Miembro quitado."))
                self._cargar()
        mastodon_service.gestionar_miembro_lista_async(self.account_id, self.list_id, m["acct"], False, callback=_fin)


# Los diálogos de FILTROS DEL SERVIDOR viven en mastodon_filtros_dialogs.py; se reexportan
# aquí para no romper los imports existentes.
from .mastodon_filtros_dialogs import FiltrosServidorDialog, NuevoFiltroServidorDialog  # noqa: E402,F401


class PollViewDialog(wx.Dialog):
    """Muestra el texto de una publicación y su encuesta, y permite votar."""

    def __init__(self, parent, account_id, toot_db_id, contenido):
        super().__init__(parent, title=_("Publicación y encuesta"), size=(560, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Diálogo de encuesta"))
        self.account_id = account_id
        self.toot_db_id = toot_db_id
        self.contenido = contenido or ""
        self.poll = None
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        from .accessible_controls import AccessibleTextCtrl, AccessibleListCtrl
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Publicación:")), 0, wx.ALL, 5)
        self.txt_contenido = AccessibleTextCtrl(self, value=self.contenido,
                                                style=wx.TE_MULTILINE | wx.TE_READONLY, name=_("Texto de la publicación"))
        s.Add(self.txt_contenido, 1, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Encuesta (elige una opción y pulsa Votar):")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Opciones de la encuesta"))
        self.lista.InsertColumn(0, _("Opción"), width=320)
        self.lista.InsertColumn(1, _("Votos"), width=90)
        self.lista.InsertColumn(2, _("Porcentaje"), width=100)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        self.lbl_estado = wx.StaticText(self, label="")
        s.Add(self.lbl_estado, 0, wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_votar = wx.Button(self, label=_("&Votar"))
        self.btn_votar.Bind(wx.EVT_BUTTON, self._on_votar)
        self.btn_votar.Disable()
        btns.Add(self.btn_votar, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _cargar(self):
        speak(_("Cargando encuesta..."), interrupt=True)

        def _fin(poll, err):
            if err or not poll:
                self.lbl_estado.SetLabel(_("No se pudo cargar la encuesta."))
                notificar(_("No se pudo cargar la encuesta."))
                return
            self.poll = poll
            self.lista.DeleteAllItems()
            total = poll.get("votes_count", 0) or 0
            opciones = poll.get("options", [])
            votos = poll.get("votes", [])
            # Como en Mastodon: los resultados (votos y porcentajes) NO se muestran hasta que has
            # votado o la encuesta ha finalizado. Antes se podian ver antes de votar.
            mostrar_resultados = bool(poll.get("voted")) or bool(poll.get("expired"))
            for i, op in enumerate(opciones):
                self.lista.InsertItem(i, op)
                if mostrar_resultados:
                    v = votos[i] if (i < len(votos) and votos[i] is not None) else 0
                    pct = (v * 100 // total) if total else 0
                    self.lista.SetItem(i, 1, str(v))
                    self.lista.SetItem(i, 2, f"{pct}%")
                else:
                    self.lista.SetItem(i, 1, "—")
                    self.lista.SetItem(i, 2, "—")
            if opciones:
                self.lista.Focus(0)
                self.lista.Select(0)
            estado = []
            if poll.get("multiple"):
                estado.append(_("Opción múltiple"))
            if poll.get("expired"):
                estado.append(_("Finalizada"))
            if poll.get("voted"):
                estado.append(_("Ya has votado"))
            estado.append(_("{0} votos en total").format(total))
            self.lbl_estado.SetLabel(". ".join(estado))
            if not poll.get("expired") and not poll.get("voted"):
                self.btn_votar.Enable()
            speak(_("Encuesta cargada. {0} opciones.").format(len(opciones)), interrupt=True)

        mastodon_service.obtener_encuesta_async(self.account_id, self.toot_db_id, callback=_fin)

    def _on_votar(self, event):
        if not self.poll:
            return
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0:
            speak(_("Elige una opción."), interrupt=True)
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo votar: {0}").format(err))
            else:
                speak(_("Voto emitido."), interrupt=True)
                self.btn_votar.Disable()
                self._cargar()

        mastodon_service.votar_encuesta_async(self.account_id, self.poll.get("id"), [idx], callback=_fin)


class OpcionesSeguimientoDialog(wx.Dialog):
    """Opciones de seguimiento de una cuenta: mostrar sus impulsos y avisar de sus
    publicaciones nuevas (account_follow con reblogs/notify)."""

    def __init__(self, parent, relacion):
        super().__init__(parent, title=_("Opciones de seguimiento de {0}").format(relacion.get("display", "")),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Opciones de seguimiento"))
        self.relacion = relacion
        s = wx.BoxSizer(wx.VERTICAL)
        if not relacion.get("following"):
            s.Add(wx.StaticText(self, label=_("Aún no sigues a esta cuenta; al guardar empezarás a seguirla con estas opciones.")),
                  0, wx.ALL, 8)
        self.cb_reblogs = wx.CheckBox(self, label=_("Mostrar sus impulsos (boosts) en mi Inicio"),
                                      name=_("Mostrar impulsos"))
        self.cb_reblogs.SetValue(bool(relacion.get("showing_reblogs", True)))
        s.Add(self.cb_reblogs, 0, wx.ALL, 8)
        self.cb_notify = wx.CheckBox(self, label=_("Avisarme cuando publique algo nuevo"),
                                     name=_("Avisar de nuevas publicaciones"))
        self.cb_notify.SetValue(bool(relacion.get("notifying", False)))
        s.Add(self.cb_notify, 0, wx.ALL, 8)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.SetDefault()
        btns.Add(b_ok, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizerAndFit(s)
        self.CenterOnParent()

    def obtener_datos(self):
        return {"reblogs": self.cb_reblogs.GetValue(), "notify": self.cb_notify.GetValue()}


class HashtagsDestacadosDialog(wx.Dialog):
    """Gestiona los hashtags DESTACADOS en tu perfil (featured tags)."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Hashtags destacados de mi perfil"), size=(520, 400),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Hashtags destacados"))
        self.account_id = account_id
        self.items = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Hashtags que aparecen destacados en tu perfil:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de hashtags destacados"))
        self.lista.InsertColumn(0, _("Hashtag"), width=240)
        self.lista.InsertColumn(1, _("Publicaciones"), width=160)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        b1 = wx.Button(self, label=_("&Destacar un hashtag..."))
        b1.Bind(wx.EVT_BUTTON, self._on_anadir)
        btns.Add(b1, 0, wx.ALL, 5)
        b2 = wx.Button(self, label=_("&Quitar"))
        b2.Bind(wx.EVT_BUTTON, self._on_quitar)
        btns.Add(b2, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                speak(_("No se pudieron cargar: {0}").format(err), interrupt=True)
                return
            self.items = res or []
            self.lista.DeleteAllItems()
            for i, t in enumerate(self.items):
                self.lista.InsertItem(i, "#" + t.get("name", ""))
                self.lista.SetItem(i, 1, str(t.get("statuses_count", 0)))
            if not self.items:
                speak(_("No tienes hashtags destacados."), interrupt=True)
        mastodon_service.obtener_hashtags_destacados_async(self.account_id, callback=_fin)

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Hashtag a destacar (sin el símbolo #):"), _("Destacar hashtag"))
        if dlg.ShowModal() == wx.ID_OK:
            nombre = dlg.GetValue().strip().lstrip("#")
            if nombre:
                def _fin(res, err):
                    if err:
                        notificar(_("No se pudo destacar: {0}").format(err))
                    else:
                        notificar(_("Hashtag destacado."))
                        self._cargar()
                mastodon_service.crear_hashtag_destacado_async(self.account_id, nombre, callback=_fin)
        dlg.Destroy()

    def _on_quitar(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.items):
            return
        ft = self.items[idx]

        def _fin(res, err):
            if err:
                notificar(_("No se pudo quitar: {0}").format(err))
            else:
                notificar(_("Hashtag quitado de destacados."))
                self._cargar()
        mastodon_service.borrar_hashtag_destacado_async(self.account_id, ft.get("id"), callback=_fin)


class CuentasDestacadasDialog(wx.Dialog):
    """Lista de cuentas DESTACADAS en tu perfil (endorsements), con opción de dejar de
    destacar."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Cuentas destacadas en mi perfil"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Cuentas destacadas"))
        self.account_id = account_id
        self.cuentas = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Cuentas que destacas en tu perfil:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de cuentas destacadas"))
        self.lista.InsertColumn(0, _("Nombre"), width=220)
        self.lista.InsertColumn(1, _("Usuario"), width=300)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        b1 = wx.Button(self, label=_("&Dejar de destacar"))
        b1.Bind(wx.EVT_BUTTON, self._on_quitar)
        btns.Add(b1, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                speak(_("No se pudieron cargar: {0}").format(err), interrupt=True)
                return
            self.cuentas = res or []
            self.lista.DeleteAllItems()
            for i, c in enumerate(self.cuentas):
                self.lista.InsertItem(i, c.get("display_name", ""))
                self.lista.SetItem(i, 1, "@" + c.get("acct", ""))
            if not self.cuentas:
                speak(_("No destacas a ninguna cuenta."), interrupt=True)
        mastodon_service.obtener_cuentas_destacadas_async(self.account_id, callback=_fin)

    def _on_quitar(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.cuentas):
            return
        acct = self.cuentas[idx].get("acct", "")

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Ya no destacas a esa cuenta."))
                self._cargar()
        mastodon_service.destacar_cuenta_async(self.account_id, acct, destacar=False, callback=_fin)


class PoliticaNotificacionesDialog(wx.Dialog):
    """Configura la política de filtrado de notificaciones del servidor (Mastodon 4.3+):
    para cada tipo de remitente puedes Aceptar, Filtrar (a una bandeja aparte) o Descartar."""

    # (clave de la API, etiqueta visible)
    CATEGORIAS = [
        ("for_not_following", _("Personas que no sigues")),
        ("for_not_followers", _("Personas que no te siguen")),
        ("for_new_accounts", _("Cuentas creadas recientemente")),
        ("for_private_mentions", _("Menciones privadas no solicitadas")),
        ("for_limited_accounts", _("Cuentas moderadas (limitadas)")),
    ]
    # (valor de la API, etiqueta visible)
    VALORES = [("accept", _("Aceptar")), ("filter", _("Filtrar")), ("drop", _("Descartar"))]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Política de notificaciones"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Política de notificaciones"))
        self.account_id = account_id
        self.choices = {}
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Cómo tratar las notificaciones según quién las envía:")),
              0, wx.ALL, 8)
        etiquetas = [v[1] for v in self.VALORES]
        rejilla = wx.FlexGridSizer(cols=2, vgap=6, hgap=10)
        for clave, etiqueta in self.CATEGORIAS:
            rejilla.Add(wx.StaticText(self, label=etiqueta + ":"), 0, wx.ALIGN_CENTER_VERTICAL)
            ch = wx.Choice(self, choices=etiquetas, name=etiqueta)
            ch.SetSelection(0)
            self.choices[clave] = ch
            rejilla.Add(ch, 0, wx.EXPAND)
        s.Add(rejilla, 0, wx.ALL, 8)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.SetDefault()
        b_ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        btns.Add(b_ok, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizerAndFit(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                speak(_("No se pudo cargar la política: {0}").format(err), interrupt=True)
                return
            valores = [v[0] for v in self.VALORES]
            for clave, ch in self.choices.items():
                actual = (res or {}).get(clave, "accept")
                if actual in valores:
                    ch.SetSelection(valores.index(actual))
            speak(_("Política de notificaciones cargada."), interrupt=True)
        mastodon_service.obtener_politica_notificaciones_async(self.account_id, callback=_fin)

    def _on_guardar(self, event):
        valores = [v[0] for v in self.VALORES]
        opciones = {clave: valores[ch.GetSelection()] for clave, ch in self.choices.items()}

        def _fin(res, err):
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
            else:
                notificar(_("Política de notificaciones actualizada."))
                self.EndModal(wx.ID_OK)
        mastodon_service.fijar_politica_notificaciones_async(self.account_id, opciones, callback=_fin)
