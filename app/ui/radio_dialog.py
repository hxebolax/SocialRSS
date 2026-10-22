# -*- coding: utf-8 -*-
"""
Diálogo de radio por internet, con la interfaz del complemento zRadioBASS:
pestañas General, Favoritos, Buscador e Historial, lista de emisoras y una barra de
reproducción abajo (Detener, Recargar, Silenciar, Volumen).

Reproduce con el segundo reproductor (radio/podcast/YouTube), que respeta la prioridad
del audio de las redes sociales.
"""

import wx

from app.core.task_manager import task_manager
from app.core.speech import speak, notificar
from app.services.radio.radio_service import radio_service
from app.services.media_player.player2_service import player2_service, KIND_RADIO
from .accessible_controls import mostrar_info, confirmar, CustomTextEntryDialog


# Lista de países comunes (como en zRadioBASS): nombre visible -> código ISO.
# "TOP" = emisoras más populares del mundo.
_PAISES = [
    (_("Todas las populares"), "TOP"),
    (_("España"), "ES"), (_("México"), "MX"), (_("Argentina"), "AR"),
    (_("Colombia"), "CO"), (_("Chile"), "CL"), (_("Perú"), "PE"),
    (_("Venezuela"), "VE"), (_("Ecuador"), "EC"), (_("Uruguay"), "UY"),
    (_("Estados Unidos"), "US"), (_("Reino Unido"), "GB"), (_("Francia"), "FR"),
    (_("Alemania"), "DE"), (_("Italia"), "IT"), (_("Portugal"), "PT"),
    (_("Brasil"), "BR"),
]


class _EmisoraPanel(wx.Panel):
    """Panel base: lista de emisoras (ListBox) con acciones comunes.

    Las subclases rellenan la lista llamando a `mostrar(estaciones)`. La reproducción y
    los favoritos los gestiona el diálogo padre (`self.dlg`)."""

    LOTE = 200  # emisoras que se añaden por tanda

    def __init__(self, parent, dlg):
        super().__init__(parent)
        self.dlg = dlg
        self._estaciones = []
        self._cadenas = []      # textos a mostrar, precalculados
        self._carga_idx = 0
        self._gen = 0           # generación de carga (cancela las anteriores)
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self._construir_cabecera()
        self.sizer.Add(wx.StaticText(self, label=_("Emisoras (Intro reproduce):")), 0, wx.LEFT | wx.TOP, 5)
        self.lst = wx.ListBox(self, style=wx.LB_SINGLE, name=_("Lista de emisoras"))
        self.lst.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self._reproducir())
        self.lst.Bind(wx.EVT_CONTEXT_MENU, self._on_context)
        # CHAR_HOOK (no KEY_DOWN): en un diálogo, el Intro lo intercepta el botón por defecto
        # antes de llegar al ListBox; con CHAR_HOOK sí lo capturamos para reproducir.
        self.lst.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.sizer.Add(self.lst, 1, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(self.sizer)

    def _construir_cabecera(self):
        """Las subclases añaden sus controles (combos, campos) a self.sizer."""

    @staticmethod
    def _fmt(e):
        partes = [e.get("name", "")]
        if e.get("country"):
            partes.append(f"({e['country']})")
        if e.get("bitrate"):
            partes.append(f"[{e['bitrate']} kbps]")
        return " ".join(partes)

    def mostrar(self, estaciones):
        """Carga la lista de emisoras por tandas, sin bloquear, aunque sean miles."""
        self._estaciones = estaciones or []
        self._cadenas = [self._fmt(e) for e in self._estaciones]
        self._carga_idx = 0
        self._gen += 1
        self.lst.Clear()
        total = len(self._estaciones)
        if not total:
            speak(_("Sin resultados."), interrupt=True)
            return
        speak(_("{0} emisoras. Cargando...").format(total), interrupt=True)
        self._cargar_lote(self._gen)

    def _cargar_lote(self, gen):
        # Si llegó una búsqueda nueva (otra generación), abandonar esta carga.
        if gen != self._gen:
            return
        try:
            if not self.lst:
                return
        except RuntimeError:
            return
        lote = self._cadenas[self._carga_idx:self._carga_idx + self.LOTE]
        if lote:
            self.lst.AppendItems(lote)
            if self._carga_idx == 0:
                self.lst.SetSelection(0)
            self._carga_idx += len(lote)
        if self._carga_idx < len(self._cadenas):
            # Ceder al bucle de eventos para que la interfaz siga respondiendo.
            wx.CallLater(1, self._cargar_lote, gen)

    def seleccion(self):
        i = self.lst.GetSelection()
        if i == wx.NOT_FOUND or i >= len(self._estaciones):
            return None
        return self._estaciones[i]

    def _reproducir(self):
        est = self.seleccion()
        if est:
            self.dlg.reproducir_estacion(est)

    def _on_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._reproducir()
        elif key == wx.WXK_SPACE:
            self.dlg.alternar_pausa()
        else:
            event.Skip()

    def _on_context(self, event):
        est = self.seleccion()
        if not est:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("&Reproducir"))
        self.Bind(wx.EVT_MENU, lambda e: self._reproducir(), it)
        if radio_service.es_favorito(est.get("url", "")):
            it = menu.Append(wx.ID_ANY, _("&Quitar de favoritos"))
            self.Bind(wx.EVT_MENU, lambda e: self.dlg.toggle_favorito(est, self), it)
        else:
            it = menu.Append(wx.ID_ANY, _("&Añadir a favoritos"))
            self.Bind(wx.EVT_MENU, lambda e: self.dlg.toggle_favorito(est, self), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("&Información de la emisora"))
        self.Bind(wx.EVT_MENU, lambda e: self.dlg.info_estacion(est), it)
        if est.get("uuid"):
            it = menu.Append(wx.ID_ANY, _("&Votar emisora"))
            self.Bind(wx.EVT_MENU, lambda e: self.dlg.votar(est), it)
        it = menu.Append(wx.ID_ANY, _("&Copiar URL"))
        self.Bind(wx.EVT_MENU, lambda e: self.dlg.copiar_url(est), it)
        # Las subclases (p. ej. Favoritos) pueden añadir más opciones.
        self._extra_context(menu, est)
        self.lst.PopupMenu(menu)
        menu.Destroy()

    def _extra_context(self, menu, est):
        """Gancho para que las subclases añadan opciones al menú contextual."""


class _GeneralPanel(_EmisoraPanel):
    """Emisoras por país (o las más populares)."""

    def _construir_cabecera(self):
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&País:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.cmb_pais = wx.ComboBox(self, style=wx.CB_READONLY,
                                    choices=[n for n, _c in _PAISES], name=_("País"))
        self.cmb_pais.SetSelection(1)  # España
        fila.Add(self.cmb_pais, 1, wx.EXPAND | wx.ALL, 5)
        btn = wx.Button(self, label=_("&Cargar emisoras"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self.cargar())
        fila.Add(btn, 0, wx.ALL, 5)
        self.sizer.Add(fila, 0, wx.EXPAND)

    def cargar(self):
        idx = self.cmb_pais.GetSelection()
        if idx < 0:
            idx = 1
        code = _PAISES[idx][1]
        speak(_("Cargando emisoras..."), interrupt=True)

        def _bg(ctx):
            if code == "TOP":
                return radio_service.api.top(1000)
            return radio_service.api.buscar(country=code)  # sin límite

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar las emisoras: {0}").format(err))
                return
            self.mostrar(res)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class _BuscadorPanel(_EmisoraPanel):
    """Búsqueda por nombre, país, idioma o género."""

    def _construir_cabecera(self):
        f1 = wx.BoxSizer(wx.HORIZONTAL)
        f1.Add(wx.StaticText(self, label=_("&Nombre:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_nombre = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Nombre"))
        self.txt_nombre.Bind(wx.EVT_TEXT_ENTER, lambda e: self.buscar())
        f1.Add(self.txt_nombre, 1, wx.EXPAND | wx.ALL, 5)
        self.sizer.Add(f1, 0, wx.EXPAND)

        f2 = wx.BoxSizer(wx.HORIZONTAL)
        f2.Add(wx.StaticText(self, label=_("&Género:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_genero = wx.TextCtrl(self, name=_("Género o etiqueta"))
        f2.Add(self.txt_genero, 1, wx.EXPAND | wx.ALL, 5)
        f2.Add(wx.StaticText(self, label=_("&Idioma:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_idioma = wx.TextCtrl(self, name=_("Idioma"))
        f2.Add(self.txt_idioma, 1, wx.EXPAND | wx.ALL, 5)
        btn = wx.Button(self, label=_("&Buscar"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self.buscar())
        f2.Add(btn, 0, wx.ALL, 5)
        self.sizer.Add(f2, 0, wx.EXPAND)

    def buscar(self):
        nombre = self.txt_nombre.GetValue().strip()
        genero = self.txt_genero.GetValue().strip()
        idioma = self.txt_idioma.GetValue().strip()
        if not (nombre or genero or idioma):
            mostrar_info(self, _("Escribe al menos un criterio de búsqueda."), _("Radio"))
            return
        speak(_("Buscando..."), interrupt=True)

        def _bg(ctx):
            return radio_service.api.buscar(name=nombre, tag=genero, language=idioma)  # sin límite

        def _fin(res, err):
            if err:
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            self.mostrar(res)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class _FavoritosPanel(_EmisoraPanel):
    """Emisoras favoritas guardadas."""

    def _construir_cabecera(self):
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn = wx.Button(self, label=_("&Actualizar lista"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self.cargar())
        fila.Add(btn, 0, wx.ALL, 5)
        btn_nuevo = wx.Button(self, label=_("&Nuevo favorito..."))
        btn_nuevo.Bind(wx.EVT_BUTTON, lambda e: self._nuevo_manual())
        fila.Add(btn_nuevo, 0, wx.ALL, 5)
        btn_cat = wx.Button(self, label=_("&Categorías..."))
        btn_cat.Bind(wx.EVT_BUTTON, lambda e: self.dlg.gestionar_categorias())
        fila.Add(btn_cat, 0, wx.ALL, 5)
        self.sizer.Add(fila, 0, wx.EXPAND)

    def cargar(self):
        self.mostrar(radio_service.obtener_favoritos())

    def _nuevo_manual(self):
        """Crea un favorito desde cero introduciendo nombre y URL del stream a mano."""
        d2 = CustomTextEntryDialog(self, _("URL del stream:"), _("Nuevo favorito"))
        if d2.ShowModal() != wx.ID_OK:
            d2.Destroy()
            return
        url = d2.GetValue().strip()
        d2.Destroy()
        if not url:
            mostrar_info(self, _("Tienes que indicar la URL del stream."), _("Nuevo favorito"))
            return
        d1 = CustomTextEntryDialog(self, _("Nombre:"), _("Nuevo favorito"))
        nombre = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
        d1.Destroy()
        if not nombre:
            nombre = url
        # Categoría (opcional).
        categoria_id = None
        cats = radio_service.obtener_categorias()
        if cats:
            opciones = [_("Sin categoría")] + [c["name"] for c in cats]
            sel = wx.GetSingleChoice(_("Categoría:"), _("Nuevo favorito"), opciones, self)
            if sel and sel != _("Sin categoría"):
                categoria_id = cats[opciones.index(sel) - 1]["id"]
        radio_service.anadir_favorito({"name": nombre, "url": url, "uuid": ""}, categoria_id)
        notificar(_("Favorito añadido."))
        self.cargar()

    def _extra_context(self, menu, est):
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("&Editar favorito..."))
        self.Bind(wx.EVT_MENU, lambda e: self._editar(est), it)
        it = menu.Append(wx.ID_ANY, _("E&liminar de favoritos"))
        self.Bind(wx.EVT_MENU, lambda e: self._eliminar(est), it)

    def _editar(self, est):
        if not est.get("id"):
            return
        d1 = CustomTextEntryDialog(self, _("Nombre:"), _("Editar favorito"),
                                   value=est.get("name", ""))
        if d1.ShowModal() != wx.ID_OK:
            d1.Destroy()
            return
        nombre = d1.GetValue().strip()
        d1.Destroy()
        d2 = CustomTextEntryDialog(self, _("URL del stream:"), _("Editar favorito"),
                                   value=est.get("url", ""))
        url = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else est.get("url", "")
        d2.Destroy()
        # Categoría (opcional).
        cats = radio_service.obtener_categorias()
        categoria_id = -1  # no cambiar por defecto
        if cats:
            opciones = [_("(No cambiar)"), _("Sin categoría")] + [c["name"] for c in cats]
            sel = wx.GetSingleChoice(_("Categoría:"), _("Editar favorito"), opciones, self)
            if sel == _("Sin categoría"):
                categoria_id = None
            elif sel and sel != _("(No cambiar)"):
                categoria_id = cats[opciones.index(sel) - 2]["id"]
        radio_service.editar_favorito(est["id"], name=nombre or None,
                                      url=url or None, category_id=categoria_id)
        notificar(_("Favorito actualizado."))
        self.cargar()

    def _eliminar(self, est):
        if not est.get("id"):
            return
        if confirmar(self, _("¿Eliminar «{0}» de favoritos?").format(est.get("name", "")),
                     _("Eliminar favorito")):
            radio_service.quitar_favorito(est["id"])
            notificar(_("Eliminado de favoritos."))
            self.cargar()


class _HistorialPanel(_EmisoraPanel):
    """Historial de emisoras escuchadas."""

    def _construir_cabecera(self):
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn = wx.Button(self, label=_("&Actualizar lista"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self.cargar())
        fila.Add(btn, 0, wx.ALL, 5)
        btn_borrar = wx.Button(self, label=_("&Borrar historial"))
        btn_borrar.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        fila.Add(btn_borrar, 0, wx.ALL, 5)
        self.sizer.Add(fila, 0, wx.EXPAND)

    def cargar(self):
        self.mostrar(radio_service.obtener_historial())

    def _borrar(self):
        if confirmar(self, _("¿Borrar todo el historial de radio?"), _("Historial")):
            radio_service.borrar_historial()
            self.cargar()
            notificar(_("Historial borrado."))


class RadioDialog(wx.Dialog):
    """Ventana principal de la radio por internet."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Radio por internet"), size=(780, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Radio por internet"))
        self._ultima_estacion = None
        self._silenciado = False
        self._init_ui()
        self.CenterOnParent()
        # Cargar la pestaña General al abrir.
        wx.CallAfter(self.panel_general.cargar)

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self)
        self.panel_general = _GeneralPanel(self.notebook, self)
        self.panel_favoritos = _FavoritosPanel(self.notebook, self)
        self.panel_buscador = _BuscadorPanel(self.notebook, self)
        self.panel_historial = _HistorialPanel(self.notebook, self)
        self.notebook.AddPage(self.panel_general, _("General"))
        self.notebook.AddPage(self.panel_favoritos, _("Favoritos"))
        self.notebook.AddPage(self.panel_buscador, _("Buscador"))
        self.notebook.AddPage(self.panel_historial, _("Historial"))
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_cambiar_pagina)
        s.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)

        # Barra de reproducción (siempre visible).
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_stop = wx.Button(self, label=_("&Detener"))
        self.btn_stop.Bind(wx.EVT_BUTTON, lambda e: self._detener())
        bar.Add(self.btn_stop, 0, wx.ALL, 5)
        self.btn_reload = wx.Button(self, label=_("&Recargar"))
        self.btn_reload.Bind(wx.EVT_BUTTON, lambda e: self._recargar())
        bar.Add(self.btn_reload, 0, wx.ALL, 5)
        self.btn_mute = wx.Button(self, label=_("&Silenciar"))
        self.btn_mute.Bind(wx.EVT_BUTTON, lambda e: self._silenciar())
        bar.Add(self.btn_mute, 0, wx.ALL, 5)
        bar.Add(wx.StaticText(self, label=_("&Volumen:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        vol = int(player2_service.obtener_informacion()["volume"] * 100)
        self.slider_vol = wx.Slider(self, value=vol, minValue=0, maxValue=100, name=_("Volumen"))
        self.slider_vol.Bind(wx.EVT_SLIDER,
                             lambda e: player2_service.establecer_volumen(self.slider_vol.GetValue() / 100.0))
        bar.Add(self.slider_vol, 1, wx.EXPAND | wx.ALL, 5)
        s.Add(bar, 0, wx.EXPAND)

        bottom = wx.BoxSizer(wx.HORIZONTAL)
        bottom.AddStretchSpacer()
        bottom.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(bottom, 0, wx.EXPAND | wx.ALL, 3)
        self.SetSizer(s)

    def _on_cambiar_pagina(self, event):
        # Al entrar en Favoritos o Historial, refrescar desde la BD.
        pagina = self.notebook.GetPage(event.GetSelection())
        if pagina is self.panel_favoritos or pagina is self.panel_historial:
            pagina.cargar()
        event.Skip()

    # ------------------------------------------------------------------
    # Acciones compartidas (llamadas desde los paneles)
    # ------------------------------------------------------------------
    def reproducir_estacion(self, est):
        # Regla de "una sola cosa a la vez" del segundo reproductor.
        if (player2_service.esta_activo()
                and not (player2_service.current_kind == KIND_RADIO
                         and player2_service.current_url == est.get("url"))):
            if not confirmar(
                    self,
                    _("Ya se está reproduciendo {0}. ¿Detenerlo y poner esta emisora?").format(
                        player2_service.descripcion_actual() or _("otro audio")),
                    _("Radio")):
                return
        self._ultima_estacion = est
        self._silenciado = False
        self.btn_mute.SetLabel(_("&Silenciar"))
        radio_service.reproducir(est)
        notificar(_("Reproduciendo: {0}").format(est.get("name", "")))

    def alternar_pausa(self):
        player2_service.alternar_pausa()

    def _detener(self):
        if player2_service.current_kind == KIND_RADIO and player2_service.esta_activo():
            player2_service.detener()
            speak(_("Radio detenida."), interrupt=True)

    def _recargar(self):
        if self._ultima_estacion:
            radio_service.reproducir(self._ultima_estacion)
            notificar(_("Recargando: {0}").format(self._ultima_estacion.get("name", "")))

    def _silenciar(self):
        if player2_service.current_kind != KIND_RADIO or not player2_service.esta_activo():
            return
        self._silenciado = not self._silenciado
        if self._silenciado:
            player2_service.backend.establecer_volumen(0.0)
            self.btn_mute.SetLabel(_("&Activar sonido"))
            speak(_("Silenciado."), interrupt=True)
        else:
            player2_service.establecer_volumen(self.slider_vol.GetValue() / 100.0)
            self.btn_mute.SetLabel(_("&Silenciar"))
            speak(_("Sonido activado."), interrupt=True)

    def toggle_favorito(self, est, panel):
        url = est.get("url", "")
        if radio_service.es_favorito(url):
            radio_service.quitar_favorito_por_url(url)
            notificar(_("Quitada de favoritos."))
        else:
            cats = radio_service.obtener_categorias()
            categoria_id = None
            if cats:
                opciones = [_("Sin categoría")] + [c["name"] for c in cats]
                sel = wx.GetSingleChoice(_("¿En qué categoría?"), _("Añadir a favoritos"), opciones, self)
                if sel and sel != _("Sin categoría"):
                    categoria_id = cats[opciones.index(sel) - 1]["id"]
            radio_service.anadir_favorito(est, categoria_id)
            notificar(_("Añadida a favoritos."))
        if panel is self.panel_favoritos:
            panel.cargar()

    def info_estacion(self, est):
        txt = _("{0}\n\nPaís: {1}\nIdioma: {2}\nGénero: {3}\nCalidad: {4} kbps ({5})\nURL: {6}").format(
            est.get("name", ""), est.get("country", "") or _("desconocido"),
            est.get("language", "") or _("desconocido"), est.get("tags", "") or _("—"),
            est.get("bitrate", 0), est.get("codec", "") or _("—"), est.get("url", ""))
        mostrar_info(self, txt, _("Información de la emisora"))

    def votar(self, est):
        uuid = est.get("uuid")
        if not uuid:
            mostrar_info(self, _("Esta emisora no se puede votar."), _("Radio"))
            return
        speak(_("Votando..."), interrupt=True)
        task_manager.start_task(
            lambda ctx: radio_service.api.votar(uuid),
            callback=lambda res, err: notificar(_("Voto registrado.") if res and not err
                                                else _("No se pudo votar.")),
            pass_context=True)

    def copiar_url(self, est):
        url = est.get("url", "")
        if url and wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(url))
            wx.TheClipboard.Close()
            notificar(_("URL copiada."))

    def gestionar_categorias(self):
        acciones = [_("Crear categoría"), _("Borrar categoría"), _("Cerrar")]
        sel = wx.GetSingleChoice(_("Categorías de favoritos:"), _("Categorías"), acciones, self)
        if sel == acciones[0]:
            d = CustomTextEntryDialog(self, _("Nombre de la categoría:"), _("Nueva categoría"))
            if d.ShowModal() == wx.ID_OK and d.GetValue().strip():
                radio_service.crear_categoria(d.GetValue().strip())
                notificar(_("Categoría creada."))
            d.Destroy()
        elif sel == acciones[1]:
            cats = radio_service.obtener_categorias()
            if not cats:
                mostrar_info(self, _("No hay categorías."), _("Categorías"))
                return
            nombres = [c["name"] for c in cats]
            s2 = wx.GetSingleChoice(_("¿Qué categoría borrar?"), _("Borrar categoría"), nombres, self)
            if s2 and confirmar(self, _("¿Borrar la categoría «{0}»?").format(s2), _("Borrar categoría")):
                radio_service.borrar_categoria(cats[nombres.index(s2)]["id"])
                notificar(_("Categoría borrada."))


class DialogoFavoritosRadio(wx.Dialog):
    """Diálogo rápido de favoritos de radio (atajo global F1 durante la radio).

    Lista las emisoras favoritas; al pulsar Intro (o el botón Reproducir) sobre una,
    se reproduce de inmediato y se cierra."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Favoritos de radio"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._favoritos = radio_service.obtener_favoritos()

        panel = wx.Panel(self)
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(wx.StaticText(panel, label=_("Emisoras favoritas (Intro para reproducir):")),
              0, wx.ALL, 8)
        self.lst = wx.ListBox(panel, style=wx.LB_SINGLE)
        for f in self._favoritos:
            self.lst.Append(f.get("name") or f.get("url") or _("(sin nombre)"))
        v.Add(self.lst, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_reproducir = wx.Button(panel, label=_("&Reproducir"))
        btn_cerrar = wx.Button(panel, wx.ID_CANCEL, _("Cerrar"))
        botones.Add(self.btn_reproducir, 0, wx.RIGHT, 8)
        botones.Add(btn_cerrar, 0)
        v.Add(botones, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        panel.SetSizer(v)
        self.SetSize((480, 400))

        self.btn_reproducir.Bind(wx.EVT_BUTTON, self._on_reproducir)
        self.lst.Bind(wx.EVT_LISTBOX_DCLICK, self._on_reproducir)
        self.lst.Bind(wx.EVT_KEY_DOWN, self._on_key)

        if self._favoritos:
            self.lst.SetSelection(0)
            self.lst.SetFocus()
        else:
            self.btn_reproducir.Enable(False)
            speak(_("No tienes emisoras en favoritos."), interrupt=True)

    def _on_key(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._on_reproducir(event)
        else:
            event.Skip()

    def _on_reproducir(self, event):
        i = self.lst.GetSelection()
        if i == wx.NOT_FOUND or i >= len(self._favoritos):
            return
        radio_service.reproducir(self._favoritos[i])
        self.EndModal(wx.ID_OK)
