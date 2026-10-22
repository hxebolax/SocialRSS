# -*- coding: utf-8 -*-
"""
Vistas y diálogos auxiliares para el servicio RSS.
Permite suscribirse a nuevos feeds, organizar categorías, configurar filtros/reglas de entrada
y leer artículos de forma completamente limpia y estructurada.
"""

import logging
import wx
import wx.html
from app.core.privacy import abrir_url

from app.core.speech import speak, notificar
from app.core.database import db
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, mostrar_info

log = logging.getLogger(__name__)

# El lector limpio vive en su propio módulo; se reexporta aquí por compatibilidad.
from .rss_lector import CleanArticleViewer  # noqa: E402,F401

class AddFeedDialog(wx.Dialog):
    """Diálogo para suscribirse a un nuevo feed RSS."""
    
    def __init__(self, parent, account_id: int):
        super().__init__(
            parent,
            title=_("Añadir Feed RSS"),
            size=(500, 350),
            style=wx.DEFAULT_DIALOG_STYLE,
            name=_("Diálogo agregar feed")
        )
        
        self.account_id = account_id
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # URL del Feed
        sizer.Add(wx.StaticText(self, label=_("URL del Feed RSS:")), 0, wx.ALL, 5)
        self.txt_url = AccessibleTextCtrl(
            self,
            name=_("Dirección URL del Feed"),
            ayuda_texto=_("Introduzca la dirección web completa del feed RSS o del sitio web para autodetectar.")
        )
        sizer.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 5)

        # Botón para comprobar la URL antes de añadir (valida y dice el título)
        self.btn_comprobar = wx.Button(self, label=_("&Comprobar feed"), name=_("Comprobar el feed antes de añadirlo"))
        self.btn_comprobar.Bind(wx.EVT_BUTTON, self._on_comprobar)
        fila_b = wx.BoxSizer(wx.HORIZONTAL)
        fila_b.Add(self.btn_comprobar, 0, wx.ALL, 5)
        # Si no sabes la dirección: buscar por tema o nombre en directorios públicos.
        btn_dir = wx.Button(self, label=_("Buscar en &directorios..."),
                            name=_("Buscar feeds y podcasts en directorios públicos"))
        btn_dir.Bind(wx.EVT_BUTTON, self._on_buscar_directorios)
        fila_b.Add(btn_dir, 0, wx.ALL, 5)
        sizer.Add(fila_b, 0)

        # Título personalizado
        sizer.Add(wx.StaticText(self, label=_("Título personalizado (opcional):")), 0, wx.ALL, 5)
        self.txt_title = AccessibleTextCtrl(
            self,
            name=_("Título personalizado del feed"),
            ayuda_texto=_("Si se deja vacío, se cargará el título original proveído por la fuente.")
        )
        sizer.Add(self.txt_title, 0, wx.EXPAND | wx.ALL, 5)
        
        # Categoría
        sizer.Add(wx.StaticText(self, label=_("Categoría:")), 0, wx.ALL, 5)
        self.choice_cat = wx.Choice(self, name=_("Categoría asociada"))
        self._cargar_categorias()
        sizer.Add(self.choice_cat, 0, wx.EXPAND | wx.ALL, 5)
        
        # Botones
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_guardar = wx.Button(self, wx.ID_OK, _("&Añadir Feed"), name=_("Guardar suscripción"))
        self.btn_guardar.Bind(wx.EVT_BUTTON, self._on_save)

        btn_sizer.Add(self.btn_guardar, 0, wx.ALL, 5)
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cancela&r")), 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)

    def _cargar_categorias(self):
        self.categories_list = db.query(
            "SELECT id, name FROM rss_categories WHERE account_id = ?", (self.account_id,))
        choices = [c[1] for c in self.categories_list]
        choices.insert(0, _("Sin Categoría"))
        self.choice_cat.SetItems(choices)
        self.choice_cat.SetSelection(0)

    def _on_comprobar(self, event):
        """Valida la URL sin añadirla y anuncia el resultado (título y nº de artículos)."""
        url = self.txt_url.GetValue().strip()
        if not url:
            speak(_("Debe introducir una URL."), interrupt=True)
            return
        speak(_("Comprobando feed..."), interrupt=True)
        self.btn_comprobar.Disable()

        def _bg(ctx):
            return rss_service.validar_feed(url)

        def _finish(res, err):
            self.btn_comprobar.Enable()
            if err or not res:
                notificar(_("No se pudo comprobar el feed: {0}").format(err or ""))
                return
            if res.get("valido"):
                # Varios feeds en la misma dirección (una web con su feed general y los de
                # sus secciones, un repositorio con publicaciones y cambios...): elegir.
                otros = res.get("otros") or []
                if otros:
                    opciones = [res] + otros
                    etiquetas = [_("{0} ({1} artículos)").format(o.get("title") or o.get("url"),
                                                                 o.get("entries", 0)) for o in opciones]
                    dlg = wx.SingleChoiceDialog(self, _("Esa dirección tiene varios feeds. ¿Cuál quieres?"),
                                                _("Elegir feed"), etiquetas)
                    if dlg.ShowModal() == wx.ID_OK:
                        res = dict(res, **{"url": opciones[dlg.GetSelection()]["url"],
                                           "title": opciones[dlg.GetSelection()].get("title"),
                                           "entries": opciones[dlg.GetSelection()].get("entries", 0)})
                    dlg.Destroy()
                # Rellenar la URL detectada (por si era una web) y el título si está vacío
                self.txt_url.SetValue(res.get("url", url))
                if not self.txt_title.GetValue().strip() and res.get("title"):
                    self.txt_title.SetValue(res["title"])
                speak(_("Feed válido: {0}. {1} artículos disponibles.").format(
                    res.get("title", ""), res.get("entries", 0)), interrupt=True)
            else:
                speak(_("No es un feed válido: {0}").format(res.get("error", "")), interrupt=True)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_buscar_directorios(self, event):
        """Abre el buscador de feeds con lo escrito (si no parece una dirección)."""
        texto = self.txt_url.GetValue().strip()
        consulta = "" if ("." in texto and " " not in texto) else texto
        from .rss_buscar_feeds import BuscarFeedsDialog
        dlg = BuscarFeedsDialog(self, self.account_id, consulta)
        dlg.ShowModal()
        suscritos = dlg.suscritos
        dlg.Destroy()
        if suscritos:
            # Ya se suscribió desde el buscador: cerrar este diálogo como hecho.
            self.EndModal(wx.ID_OK)

    def _on_save(self, event):
        url = self.txt_url.GetValue().strip()
        custom_title = self.txt_title.GetValue().strip()
        
        if not url:
            speak(_("Debe introducir una URL válida."), interrupt=True)
            return
            
        cat_sel = self.choice_cat.GetSelection()
        category_id = None
        if cat_sel > 0:
            category_id = self.categories_list[cat_sel - 1][0]
            
        speak(_("Validando y descargando feed RSS..."), interrupt=True)
        
        def _bg_add(ctx):
            return rss_service.añadir_feed(
                account_id=self.account_id,
                url=url,
                category_id=category_id,
                custom_title=custom_title if custom_title else None,
                ctx=ctx
            )
            
        def _finish(res, err):
            if err:
                notificar(f"Error al añadir feed: {err}")
            else:
                notificar(_("Feed añadido correctamente: ") + str(res))
                self.EndModal(wx.ID_OK)
                
        task_manager.start_task(_bg_add, callback=_finish, pass_context=True)


class CategoriasDialog(wx.Dialog):
    """Diálogo para organizar y añadir categorías para los feeds RSS (de UNA cuenta)."""

    def __init__(self, parent, account_id: int):
        super().__init__(
            parent,
            title=_("Gestionar Categorías RSS"),
            size=(450, 400),
            style=wx.DEFAULT_DIALOG_STYLE,
            name=_("Diálogo gestionar categorías")
        )
        self.account_id = account_id
        self._init_ui()
        self._cargar_categorias()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Entrada nueva categoría
        sz_add = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_cat = AccessibleTextCtrl(
            self,
            name=_("Nombre de la nueva categoría"),
            ayuda_texto=_("Introduzca un nombre para agregar una categoría nueva y presione Enter.")
        )
        self.btn_add = wx.Button(self, label=_("&Crear"), name=_("Crear categoría"))
        self.btn_add.Bind(wx.EVT_BUTTON, self._on_crear)
        
        sz_add.Add(self.txt_cat, 1, wx.EXPAND | wx.ALL, 5)
        sz_add.Add(self.btn_add, 0, wx.ALL, 5)
        sizer.Add(sz_add, 0, wx.EXPAND | wx.ALL, 5)
        
        # Lista
        self.lista_cat = AccessibleListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de categorías")
        )
        self.lista_cat.InsertColumn(0, _("Categoría"), width=350)
        
        sizer.Add(self.lista_cat, 1, wx.EXPAND | wx.ALL, 5)
        
        # Botonera
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_del = wx.Button(self, wx.ID_DELETE, _("&Eliminar"), name=_("Eliminar categoría seleccionada"))
        self.btn_del.Bind(wx.EVT_BUTTON, self._on_eliminar)

        btn_sizer.Add(self.btn_del, 0, wx.ALL, 5)
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)

    def _cargar_categorias(self):
        self.lista_cat.DeleteAllItems()
        self.lista_cat.limpiar_lecturas()
        self.cats_rows = db.query("SELECT id, name FROM rss_categories "
                                  "WHERE account_id = ? ORDER BY name", (self.account_id,))

        for idx, c in enumerate(self.cats_rows):
            pos = self.lista_cat.InsertItem(idx, c[1])
            self.lista_cat.establecer_lectura(pos, f"Categoría {c[1]}")

    def _on_crear(self, event):
        name = self.txt_cat.GetValue().strip()
        if not name:
            return

        try:
            rss_service.agregar_categoria(self.account_id, name)
            self.txt_cat.Clear()
            self._cargar_categorias()
            notificar(_("Categoría creada con éxito."))
        except Exception as e:
            notificar(_("Error al crear: la categoría podría ya existir."))

    def _on_eliminar(self, event):
        idx = self.lista_cat.GetFocusedItem()
        if idx < 0 or idx >= len(self.cats_rows):
            return
            
        c_id = self.cats_rows[idx][0]
        c_name = self.cats_rows[idx][1]
        
        dlg = wx.MessageDialog(
            self,
            _("¿Seguro que desea eliminar la categoría '{name}'? Los feeds dentro de ella se mantendrán sin categoría.").format(name=c_name),
            _("Confirmar eliminación"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
        )
        if dlg.ShowModal() == wx.ID_YES:
            db.execute("DELETE FROM rss_categories WHERE id = ?", (c_id,))
            self._cargar_categorias()
            notificar(_("Categoría eliminada."))
        dlg.Destroy()


# Reglas y carpetas inteligentes: en rss_reglas_ui.py (se reexportan aquí por compatibilidad).
from .rss_reglas_ui import (ReglasDialog, ReglaAvanzadaDialog, CarpetaInteligenteDialog,  # noqa: E402,F401
                            CarpetasInteligentesDialog)


class SincronizacionDialog(wx.Dialog):
    """Configura la SINCRONIZACIÓN de una cuenta RSS con un servidor externo (auditoría,
    prioridad C): Google Reader (FreshRSS, Inoreader...) o Miniflux. El servidor aporta las
    suscripciones y el estado leído/destacado; los artículos los sigue bajando la app."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Sincronización con servidor"), size=(620, 520),
                         style=wx.DEFAULT_DIALOG_STYLE,
                         name=_("Configurar sincronización de RSS"))
        self.account_id = account_id
        cfg = rss_service.obtener_config_sync(account_id)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "Sincroniza esta cuenta RSS con un servidor para compartir tus feeds y lo\n"
            "leído/destacado entre dispositivos. Tus credenciales se guardan cifradas.")),
            0, wx.ALL, 8)

        s.Add(wx.StaticText(self, label=_("Tipo de servidor:")), 0, wx.LEFT | wx.TOP, 6)
        self.choice_tipo = wx.Choice(self, choices=[
            _("Google Reader (FreshRSS, Inoreader, BazQux, The Old Reader...)"),
            _("Miniflux")], name=_("Tipo de servidor de sincronización"))
        self.choice_tipo.SetSelection(0 if cfg.get("tipo", "greader") == "greader" else 1)
        s.Add(self.choice_tipo, 0, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Dirección del servidor (URL del API):")), 0, wx.LEFT, 6)
        self.txt_url = AccessibleTextCtrl(
            self, value=cfg.get("base_url", ""), name=_("URL del servidor"),
            ayuda_texto=_("The Old Reader: https://theoldreader.com. FreshRSS: la dirección "
                          "de su API, que suele terminar en /api/greader.php. Miniflux: la "
                          "URL base de tu instancia."))
        s.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Usuario (déjalo vacío si usas un token de API):")), 0, wx.LEFT, 6)
        self.txt_user = AccessibleTextCtrl(self, value=cfg.get("usuario", ""),
                                           name=_("Usuario del servidor"))
        s.Add(self.txt_user, 0, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Contraseña o token de API:")), 0, wx.LEFT, 6)
        self.txt_pass = AccessibleTextCtrl(self, style=wx.TE_PASSWORD,
                                           name=_("Contraseña o token"))
        s.Add(self.txt_pass, 0, wx.EXPAND | wx.ALL, 6)

        fila_int = wx.BoxSizer(wx.HORIZONTAL)
        fila_int.Add(wx.StaticText(self, label=_("Sincronizar cada (minutos):")),
                     0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.spin_int = wx.SpinCtrl(self, min=5, max=1440,
                                    initial=int(cfg.get("intervalo_min", 30)),
                                    name=_("Intervalo de sincronización en minutos"))
        fila_int.Add(self.spin_int, 0, wx.ALL, 6)
        s.Add(fila_int, 0)
        self.cb_enviar_subs = wx.CheckBox(self, label=_(
            "&Llevar también al servidor las altas, bajas, cambios de nombre y de carpeta de los feeds"),
            name=_("Sincronizar también las suscripciones hacia el servidor"))
        self.cb_enviar_subs.SetValue(bool(cfg.get("enviar_suscripciones", False)))
        self.cb_enviar_subs.Bind(wx.EVT_CHECKBOX, lambda e: rss_service.fijar_enviar_suscripciones(
            self.account_id, self.cb_enviar_subs.GetValue()))
        s.Add(self.cb_enviar_subs, 0, wx.ALL, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_guardar = wx.Button(self, label=_("&Probar y guardar"),
                              name=_("Verificar las credenciales y guardar"))
        b_guardar.Bind(wx.EVT_BUTTON, self._on_guardar)
        botones.Add(b_guardar, 0, wx.ALL, 4)
        b_sync = wx.Button(self, label=_("&Sincronizar ahora"), name=_("Sincronizar ahora"))
        b_sync.Bind(wx.EVT_BUTTON, self._on_sync_ahora)
        botones.Add(b_sync, 0, wx.ALL, 4)
        b_off = wx.Button(self, label=_("&Desactivar"), name=_("Desactivar la sincronización"))
        b_off.Bind(wx.EVT_BUTTON, self._on_desactivar)
        botones.Add(b_off, 0, wx.ALL, 4)
        botones.AddStretchSpacer()
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(s)
        self.CenterOnParent()

    def _tipo(self):
        return "greader" if self.choice_tipo.GetSelection() == 0 else "miniflux"

    def _on_guardar(self, event):
        url = self.txt_url.GetValue().strip()
        if not url:
            notificar(_("Escribe la dirección del servidor."))
            return
        usuario = self.txt_user.GetValue().strip()
        secreto = self.txt_pass.GetValue()
        speak(_("Verificando credenciales..."), interrupt=True)
        tipo = self._tipo()
        intervalo = self.spin_int.GetValue()
        enviar = self.cb_enviar_subs.GetValue()
        # Miniflux con token: el secreto es token; sin usuario => token.
        token = secreto if (tipo == "miniflux" and not usuario) else ""
        password = "" if token else secreto

        def _bg(ctx):
            return rss_service.configurar_sync(self.account_id, tipo, url,
                                               usuario=usuario, password=password,
                                               token=token, intervalo_min=intervalo,
                                               enviar_suscripciones=enviar)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo conectar: {0}").format(err))
            else:
                notificar(_("Sincronización configurada y verificada."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_sync_ahora(self, event):
        speak(_("Sincronizando..."), interrupt=True)

        def _bg(ctx):
            return rss_service.sincronizar_cuenta(self.account_id)

        def _fin(res, err):
            if err or not res or not res.get("ok"):
                notificar(_("La sincronización falló: {0}").format(
                    (res or {}).get("error", err or "")))
            else:
                notificar(_("Sincronizado: {0} feeds nuevos, {1} leídos y {2} destacados "
                            "aplicados aquí, {3} cambios enviados al servidor.").format(
                    res.get("feeds_nuevos", 0), res.get("leidos_aplicados", 0),
                    res.get("destacados_aplicados", 0), res.get("enviados", 0)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_desactivar(self, event):
        rss_service.desactivar_sync(self.account_id)
        notificar(_("Sincronización desactivada."))


class EtiquetasDialog(wx.Dialog):
    """Gestor de ETIQUETAS de RSS (auditoría, prioridad B): crear, ver (con cuántos feeds
    y artículos etiquetan) y borrar. Las etiquetas etiquetadas aparecen en el árbol."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Etiquetas de RSS"), size=(520, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Gestor de etiquetas de RSS"))
        self.account_id = account_id
        self.hubo_cambios = False
        s = wx.BoxSizer(wx.VERTICAL)
        sz_add = wx.BoxSizer(wx.HORIZONTAL)
        self.txt = AccessibleTextCtrl(self, name=_("Nombre de la nueva etiqueta"),
                                      ayuda_texto=_("Escribe un nombre y pulsa Crear."))
        b_add = wx.Button(self, label=_("&Crear"), name=_("Crear etiqueta"))
        b_add.Bind(wx.EVT_BUTTON, self._on_crear)
        sz_add.Add(self.txt, 1, wx.EXPAND | wx.ALL, 4)
        sz_add.Add(b_add, 0, wx.ALL, 4)
        s.Add(sz_add, 0, wx.EXPAND)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de etiquetas"))
        self.lista.InsertColumn(0, _("Etiqueta"), width=240)
        self.lista.InsertColumn(1, _("Feeds"), width=90)
        self.lista.InsertColumn(2, _("Artículos"), width=90)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b_del = wx.Button(self, label=_("&Eliminar"), name=_("Eliminar la etiqueta seleccionada"))
        b_del.Bind(wx.EVT_BUTTON, self._on_borrar)
        fila.Add(b_del, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self._items = rss_service.obtener_etiquetas(self.account_id)
        for i, it in enumerate(self._items):
            pos = self.lista.InsertItem(i, it["name"])
            self.lista.SetItem(pos, 1, str(it["feeds"]))
            self.lista.SetItem(pos, 2, str(it["articulos"]))
            self.lista.establecer_lectura(pos, _("{0}. {1} feeds, {2} artículos.").format(
                it["name"], it["feeds"], it["articulos"]))
        if self._items:
            self.lista.Focus(0); self.lista.Select(0)

    def _on_crear(self, event):
        nombre = self.txt.GetValue().strip()
        if not nombre:
            return
        rss_service.crear_etiqueta(self.account_id, nombre)
        self.txt.Clear()
        self.hubo_cambios = True
        self._cargar()
        notificar(_("Etiqueta creada."))

    def _on_borrar(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._items)):
            return
        from .accessible_controls import confirmar
        it = self._items[idx]
        if confirmar(self, _("¿Eliminar la etiqueta «{0}»? Los feeds y artículos no se borran.").format(it["name"]),
                     _("Eliminar etiqueta")) == wx.ID_YES:
            rss_service.borrar_etiqueta(it["id"])
            self.hubo_cambios = True
            self._cargar()


class AsignarEtiquetasDialog(wx.Dialog):
    """Marca las etiquetas de un FEED o de un ARTÍCULO con casillas (auditoría, prioridad
    B). Puedes crear una etiqueta nueva sobre la marcha. Devuelve si hubo cambios."""

    def __init__(self, parent, account_id: int, feed_id=None, article_id=None):
        super().__init__(parent, title=_("Etiquetas"), size=(460, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Asignar etiquetas"))
        self.account_id = account_id
        self.feed_id = feed_id
        self.article_id = article_id
        self.hubo_cambios = False
        s = wx.BoxSizer(wx.VERTICAL)
        objeto = _("este feed") if feed_id else _("este artículo")
        s.Add(wx.StaticText(self, label=_("Marca las etiquetas de {0}:").format(objeto)), 0, wx.ALL, 6)
        from .accessible_controls import AccessibleCheckListBox
        self._tags = rss_service.obtener_etiquetas(account_id)
        actuales = set(rss_service.obtener_etiquetas_de_feed(feed_id) if feed_id
                       else rss_service.obtener_etiquetas_de_articulo(article_id))
        self.clb = AccessibleCheckListBox(self, choices=[t["name"] for t in self._tags],
                                          name=_("Etiquetas disponibles"))
        for i, t in enumerate(self._tags):
            if t["id"] in actuales:
                self.clb.Check(i, True)
        s.Add(self.clb, 1, wx.EXPAND | wx.ALL, 6)
        sz_new = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_new = AccessibleTextCtrl(self, name=_("Crear y asignar una etiqueta nueva"),
                                          ayuda_texto=_("Escribe un nombre y pulsa Añadir."))
        b_new = wx.Button(self, label=_("&Añadir"), name=_("Crear y marcar la etiqueta nueva"))
        b_new.Bind(wx.EVT_BUTTON, self._on_nueva)
        sz_new.Add(self.txt_new, 1, wx.EXPAND | wx.ALL, 4)
        sz_new.Add(b_new, 0, wx.ALL, 4)
        s.Add(sz_new, 0, wx.EXPAND)
        s.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizer(s)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.CenterOnParent()
        try:
            from .accessible_controls import habilitar_escape_cierre
            habilitar_escape_cierre(self)
        except Exception:
            pass
        self.clb.SetFocus()

    def _on_nueva(self, event):
        nombre = self.txt_new.GetValue().strip()
        if not nombre:
            return
        rss_service.crear_etiqueta(self.account_id, nombre)
        self.txt_new.Clear()
        # Recargar las casillas conservando lo marcado.
        marcados = {self._tags[i]["id"] for i in self.clb.GetCheckedItems()}
        self._tags = rss_service.obtener_etiquetas(self.account_id)
        self.clb.Set([t["name"] for t in self._tags])
        for i, t in enumerate(self._tags):
            if t["id"] in marcados or t["name"] == nombre:
                self.clb.Check(i, True)
        notificar(_("Etiqueta «{0}» creada y marcada.").format(nombre))

    def _on_ok(self, event):
        marcados = set(self.clb.GetCheckedItems())
        for i, t in enumerate(self._tags):
            poner = i in marcados
            if self.feed_id:
                rss_service.etiquetar_feed(t["id"], self.feed_id, poner)
            else:
                rss_service.etiquetar_articulo(t["id"], self.article_id, poner)
        self.hubo_cambios = True
        self.EndModal(wx.ID_OK)


class GaleriaImagenesDialog(wx.Dialog):
    """Galería ACCESIBLE de las imágenes de un artículo (auditoría, prioridad B): lista con
    el texto alternativo de cada imagen; abrir en el navegador, copiar la URL o describir
    con IA bajo petición. No descarga nada por sí sola (privacidad)."""

    def __init__(self, parent, article_id: str):
        super().__init__(parent, title=_("Imágenes del artículo"), size=(620, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Galería de imágenes del artículo"))
        self.article_id = article_id
        self._items = rss_service.obtener_imagenes_articulo(article_id)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Imágenes (con su texto alternativo):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de imágenes"))
        self.lista.InsertColumn(0, _("Descripción"), width=380)
        self.lista.InsertColumn(1, _("Dirección"), width=180)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etq, hnd, nombre in (
                (_("&Abrir en el navegador"), self._on_abrir, _("Abrir la imagen en el navegador")),
                (_("&Copiar dirección"), self._on_copiar, _("Copiar la URL de la imagen")),
                (_("&Describir con IA"), self._on_describir, _("Describir la imagen con inteligencia artificial"))):
            b = wx.Button(self, label=etq, name=nombre)
            b.Bind(wx.EVT_BUTTON, hnd)
            fila.Add(b, 0, wx.ALL, 3)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 3)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        for i, it in enumerate(self._items):
            alt = it["alt"] or _("(sin descripción)")
            pos = self.lista.InsertItem(i, alt)
            self.lista.SetItem(pos, 1, it["url"])
            self.lista.establecer_lectura(pos, alt)
        if self._items:
            self.lista.Focus(0); self.lista.Select(0)
        else:
            speak(_("Este artículo no tiene imágenes."), interrupt=True)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def _on_abrir(self, event):
        it = self._sel()
        if it:
            abrir_url(it["url"])

    def _on_copiar(self, event):
        it = self._sel()
        if it:
            from app.core.speech import copiar_al_portapapeles
            copiar_al_portapapeles(it["url"])
            speak(_("Dirección copiada."), interrupt=True)

    def _on_describir(self, event):
        it = self._sel()
        if not it:
            return
        speak(_("Descargando y describiendo la imagen con IA..."), interrupt=True)

        def _bg(ctx):
            # BAJO PETICIÓN (privacidad): solo aquí se descarga la imagen remota, a un
            # temporal acotado, para poder pasarla al proveedor de IA. Se borra al terminar.
            import os
            import tempfile
            import requests
            from app.services.ai.ai_service import ai_service
            from urllib.parse import urlparse
            ext = os.path.splitext(urlparse(it["url"]).path)[1][:5] or ".jpg"
            ruta = os.path.join(tempfile.gettempdir(), f"socialrss_img_{abs(hash(it['url']))}{ext}")
            MAX = 15 * 1024 * 1024
            with requests.get(it["url"], stream=True, timeout=30,
                              headers={"User-Agent": "SocialRSS Suite"}) as resp:
                resp.raise_for_status()
                hecho = 0
                with open(ruta, "wb") as fh:
                    for chunk in resp.iter_content(65536):
                        hecho += len(chunk or b"")
                        if hecho > MAX:
                            raise ValueError("Imagen demasiado grande.")
                        fh.write(chunk)
            try:
                return ai_service.describir_imagen(ruta)
            finally:
                try:
                    os.remove(ruta)
                except OSError:
                    pass

        def _fin(desc, err):
            if err or not desc:
                notificar(_("No se pudo describir la imagen: {0}").format(err or ""))
            else:
                mostrar_info(self, desc, _("Descripción de la imagen"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class HistorialLecturaDialog(wx.Dialog):
    """Lista los últimos artículos abiertos DE UNA CUENTA; Intro reabre el lector limpio."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Historial de lectura"), size=(680, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Historial de lectura de RSS"))
        self.account_id = account_id
        self._items = []
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Artículos leídos (Intro para reabrir):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista del historial de lectura"))
        self.lista.InsertColumn(0, _("Artículo"), width=360)
        self.lista.InsertColumn(1, _("Fuente"), width=160)
        self.lista.InsertColumn(2, _("Cuándo"), width=130)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_abrir)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_abrir = wx.Button(self, label=_("&Abrir"), name=_("Reabrir el artículo seleccionado"))
        self.btn_abrir.Bind(wx.EVT_BUTTON, self._on_abrir)
        fila.Add(self.btn_abrir, 0, wx.ALL, 4)
        self.btn_vaciar = wx.Button(self, label=_("&Vaciar historial"), name=_("Borrar todo el historial"))
        self.btn_vaciar.Bind(wx.EVT_BUTTON, self._on_vaciar)
        fila.Add(self.btn_vaciar, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)

    def _cargar(self):
        from .ui_helpers import formatear_tiempo_relativo
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self._items = rss_service.obtener_historial_lectura(self.account_id)
        for i, it in enumerate(self._items):
            cuando = formatear_tiempo_relativo(it.get("opened_at", "")) if it.get("opened_at") else ""
            pos = self.lista.InsertItem(i, it.get("title") or _("(sin título)"))
            self.lista.SetItem(pos, 1, it.get("feed_title") or "")
            self.lista.SetItem(pos, 2, cuando)
            self.lista.establecer_lectura(pos, "{0}. {1}. {2}".format(
                it.get("title") or "", it.get("feed_title") or "", cuando))
        if self._items:
            self.lista.Focus(0); self.lista.Select(0)
        else:
            speak(_("El historial está vacío."), interrupt=True)

    def _on_abrir(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._items)):
            return
        it = self._items[idx]
        if not it.get("existe"):
            notificar(_("Ese artículo ya no está disponible (se eliminó)."))
            return
        dlg = CleanArticleViewer(self, it["article_db_id"])
        dlg.ShowModal()
        dlg.Destroy()

    def _on_vaciar(self, event):
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Borrar todo el historial de lectura?"), _("Vaciar historial")) != wx.ID_YES:
            return
        n = rss_service.vaciar_historial_lectura(self.account_id)
        notificar(_("Historial vaciado ({0} registros).").format(n))
        self._cargar()


class EstadisticasLecturaDialog(wx.Dialog):
    """Muestra estadísticas de lectura: artículos leídos (hoy/semana/total), top feeds y pendientes."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Estadísticas de lectura"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Estadísticas de lectura de RSS"))
        self.account_id = account_id
        s = wx.BoxSizer(wx.VERTICAL)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                      name=_("Estadísticas de lectura"))
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        st = rss_service.obtener_estadisticas_lectura(self.account_id)
        lineas = [
            _("Artículos leídos hoy: {0}").format(st.get("hoy", 0)),
            _("Esta semana: {0}").format(st.get("semana", 0)),
            _("Total histórico: {0}").format(st.get("total", 0)),
            _("Sin leer pendientes: {0}").format(st.get("no_leidos", 0)),
            "",
            _("Feeds que más lees:"),
        ]
        top = st.get("top_feeds", [])
        if top:
            for f in top:
                lineas.append(_("  {0}: {1} artículos").format(f.get("feed", ""), f.get("n", 0)))
        else:
            lineas.append(_("  (todavía no hay datos)"))
        self.txt.SetValue("\n".join(lineas))


class CrearFeedWebDialog(wx.Dialog):
    """Crea un 'feed' a partir de una web SIN RSS: analiza la página, muestra los artículos
    detectados y, al confirmar, crea el feed (web scraper) que se actualiza re-analizando la web."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Crear feed desde una web"), size=(640, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Crear feed desde una web sin RSS"))
        self.account_id = account_id
        self._items = []
        self._selector = ""
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Dirección de la web (página de noticias, blog, etc.):")), 0, wx.ALL, 5)
        self.txt_url = AccessibleTextCtrl(
            self, name=_("Dirección de la web"),
            ayuda_texto=_("Pega la URL de una web que NO tenga RSS. Pulsa Analizar para detectar sus artículos."))
        s.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 5)

        self.btn_analizar = wx.Button(self, label=_("&Analizar la web"), name=_("Analizar la web y detectar artículos"))
        self.btn_analizar.Bind(wx.EVT_BUTTON, self._on_analizar)
        s.Add(self.btn_analizar, 0, wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Artículos detectados:")), 0, wx.LEFT | wx.TOP, 5)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Artículos detectados en la web"))
        self.lista.InsertColumn(0, _("Título"), width=420)
        self.lista.InsertColumn(1, _("Resumen"), width=170)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Título del feed:")), 0, wx.LEFT, 5)
        self.txt_title = AccessibleTextCtrl(self, name=_("Título del feed"))
        s.Add(self.txt_title, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Categoría:")), 0, wx.LEFT, 5)
        self.choice_cat = wx.Choice(self, name=_("Categoría asociada"))
        self.categories_list = db.query(
            "SELECT id, name FROM rss_categories WHERE account_id = ?", (self.account_id,))
        opciones = [_("Sin Categoría")] + [c[1] for c in self.categories_list]
        self.choice_cat.SetItems(opciones)
        self.choice_cat.SetSelection(0)
        s.Add(self.choice_cat, 0, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_crear = wx.Button(self, wx.ID_OK, _("&Crear feed"), name=_("Crear el feed desde la web"))
        self.btn_crear.Bind(wx.EVT_BUTTON, self._on_crear)
        self.btn_crear.Disable()
        btns.Add(self.btn_crear, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("Cancela&r")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _on_analizar(self, event):
        url = self.txt_url.GetValue().strip()
        if not url:
            speak(_("Escribe la dirección de la web."), interrupt=True)
            return
        speak(_("Analizando la web..."), interrupt=True)
        self.btn_analizar.Disable()

        def _bg(ctx):
            return rss_service.analizar_web(url)

        def _finish(res, err):
            self.btn_analizar.Enable()
            if err or not res:
                notificar(_("No se pudo analizar la web: {0}").format(err or ""))
                return
            self._items = res.get("items", [])
            self._selector = res.get("selector", "")
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, it in enumerate(self._items):
                pos = self.lista.InsertItem(i, it.get("title", "") or _("(sin título)"))
                self.lista.SetItem(pos, 1, (it.get("summary", "") or "")[:120])
            if not self.txt_title.GetValue().strip():
                self.txt_title.SetValue(res.get("titulo_pagina", "") or url)
            if self._items:
                self.btn_crear.Enable()
                self.lista.Focus(0); self.lista.Select(0)
                speak(_("{0} artículos detectados. Revisa y pulsa Crear feed.").format(len(self._items)), interrupt=True)
            else:
                speak(_("No se detectaron artículos en esta web. Puede que no sea compatible."), interrupt=True)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_crear(self, event):
        url = self.txt_url.GetValue().strip()
        title = self.txt_title.GetValue().strip()
        if not url or not self._items:
            speak(_("Primero analiza una web con artículos."), interrupt=True)
            return
        cat_sel = self.choice_cat.GetSelection()
        category_id = self.categories_list[cat_sel - 1][0] if cat_sel > 0 else None
        selector = self._selector
        speak(_("Creando el feed..."), interrupt=True)

        def _bg(ctx):
            return rss_service.crear_feed_web(self.account_id, url, title, category_id, selector, ctx=ctx)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo crear el feed: {0}").format(err))
            else:
                notificar(_("Feed creado desde la web."))
                self.EndModal(wx.ID_OK)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)


class SaludFeedsDialog(wx.Dialog):
    """CENTRO DE SALUD RSS (auditoría, prioridad A): el estado real de cada feed de la
    cuenta —último éxito, error, fallos seguidos y próxima comprobación— con acciones:
    actualizar ahora, pausar/reactivar y reintentar todos los rotos."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Salud de los feeds"), size=(760, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Centro de salud de los feeds RSS"))
        self.account_id = account_id
        self._items = []
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Estado de tus feeds (los problemáticos, primero):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de salud de los feeds"))
        self.lista.InsertColumn(0, _("Feed"), width=220)
        self.lista.InsertColumn(1, _("Estado"), width=110)
        self.lista.InsertColumn(2, _("Último éxito"), width=110)
        self.lista.InsertColumn(3, _("Próximo intento"), width=110)
        self.lista.InsertColumn(4, _("Error"), width=170)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_actualizar = wx.Button(self, label=_("&Actualizar ahora"),
                                        name=_("Actualizar ahora el feed seleccionado"))
        self.btn_actualizar.Bind(wx.EVT_BUTTON, self._on_actualizar)
        fila.Add(self.btn_actualizar, 0, wx.ALL, 4)
        self.btn_pausa = wx.Button(self, label=_("&Pausar o reactivar"),
                                   name=_("Pausar o reactivar el feed seleccionado"))
        self.btn_pausa.Bind(wx.EVT_BUTTON, self._on_pausa)
        fila.Add(self.btn_pausa, 0, wx.ALL, 4)
        self.btn_rotos = wx.Button(self, label=_("&Reintentar todos los rotos"),
                                   name=_("Reintentar ahora todos los feeds con fallos"))
        self.btn_rotos.Bind(wx.EVT_BUTTON, self._on_rotos)
        fila.Add(self.btn_rotos, 0, wx.ALL, 4)
        self.btn_copiar = wx.Button(self, label=_("&Copiar detalles"),
                                    name=_("Copiar los detalles del feed seleccionado"))
        self.btn_copiar.Bind(wx.EVT_BUTTON, self._on_copiar)
        fila.Add(self.btn_copiar, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)

    @staticmethod
    def _cuando(iso):
        if not iso:
            return _("nunca")
        from .ui_helpers import formatear_tiempo_relativo
        try:
            return formatear_tiempo_relativo(iso)
        except Exception:
            return str(iso)[:16].replace("T", " ")

    def _cargar(self, foco_id=None):
        from app.services.rss.rss_service import rss_service
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self._items = rss_service.obtener_salud_feeds(self.account_id)
        estados = {"ok": _("Correcto"), "fallando": _("Fallando"), "pausado": _("En pausa")}
        foco = 0
        for i, it in enumerate(self._items):
            estado = estados.get(it["estado"], it["estado"])
            if it["fail_count"]:
                estado += _(" ({0} fallos)").format(it["fail_count"])
            exito = self._cuando(it["last_success_at"])
            # El próximo intento es una fecha FUTURA: mostrarla como hora legible.
            prox = it["next_update_at"][:16].replace("T", " ") if it["next_update_at"] else ""
            if it["paused"]:
                prox = _("(pausado)")
            pos = self.lista.InsertItem(i, it["title"] or it["url"])
            self.lista.SetItem(pos, 1, estado)
            self.lista.SetItem(pos, 2, exito)
            self.lista.SetItem(pos, 3, prox)
            self.lista.SetItem(pos, 4, it["last_error"] or "")
            self.lista.establecer_lectura(pos, "{0}. {1}. {2}: {3}. {4}: {5}. {6}".format(
                it["title"] or it["url"], estado, _("Último éxito"), exito,
                _("Próximo intento"), prox or _("cuando toque"), it["last_error"] or ""))
            if foco_id is not None and it["id"] == foco_id:
                foco = i
        if self._items:
            self.lista.Focus(foco)
            self.lista.Select(foco)
        rotos = sum(1 for it in self._items if it["estado"] != "ok")
        if rotos:
            speak(_("{0} feeds con problemas de {1}.").format(rotos, len(self._items)),
                  interrupt=False)
        else:
            speak(_("Todos los feeds están bien ({0}).").format(len(self._items)),
                  interrupt=False)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def _on_actualizar(self, event):
        it = self._sel()
        if not it:
            return
        from app.services.rss.rss_service import rss_service
        speak(_("Actualizando {0}...").format(it["title"]), interrupt=True)
        # El refresco manual se salta la programación: se borra su próximo intento.
        db.execute("UPDATE rss_feeds SET next_update_at = NULL, paused = 0 WHERE id = ?",
                   (it["id"],))

        def _fin(n, err):
            if err:
                notificar(_("Sigue fallando: {0}").format(err))
            else:
                notificar(_("{0}: {1} artículos nuevos.").format(it["title"], n or 0))
            self._cargar(foco_id=it["id"])
        rss_service.actualizar_feed_async(it["id"], callback=_fin)

    def _on_pausa(self, event):
        it = self._sel()
        if not it:
            return
        from app.services.rss.rss_service import rss_service
        rss_service.set_feed_paused(it["id"], not it["paused"])
        speak(_("Feed reactivado.") if it["paused"] else _("Feed en pausa."), interrupt=True)
        self._cargar(foco_id=it["id"])

    def _on_rotos(self, event):
        from app.services.rss.rss_service import rss_service
        speak(_("Reintentando los feeds con fallos..."), interrupt=True)
        self.btn_rotos.Disable()

        def _fin(n, err):
            try:
                self.btn_rotos.Enable()
            except RuntimeError:
                return  # el diálogo se cerró mientras tanto
            if err:
                notificar(_("Error al reintentar: {0}").format(err))
            elif not n:
                notificar(_("No hay feeds con fallos que reintentar."))
            else:
                notificar(_("Reintentados {0} feeds; revisa su estado.").format(n))
            self._cargar()
        rss_service.reintentar_feeds_rotos_async(self.account_id, callback=_fin)

    def _on_copiar(self, event):
        it = self._sel()
        if not it:
            return
        from app.core.speech import copiar_al_portapapeles
        detalle = "{0}\n{1}\nEstado: {2} | Fallos: {3} | HTTP: {4}\nÚltimo intento: {5}\nÚltimo éxito: {6}\nPróximo: {7}\nError: {8}".format(
            it["title"], it["url"], it["estado"], it["fail_count"], it["last_status"] or "",
            it["last_attempt_at"], it["last_success_at"], it["next_update_at"],
            it["last_error"])
        copiar_al_portapapeles(detalle)
        speak(_("Detalles copiados al portapapeles."), interrupt=True)


class DescargasPodcastDialog(wx.Dialog):
    """COLA DE DESCARGAS de podcast (auditoría, prioridad B) de UNA cuenta: episodios en
    curso, en cola, pausados y ya descargados, con estado y progreso. Permite pausar,
    reanudar, cancelar, reproducir y borrar. La lista se refresca sola mientras hay
    descargas activas (sin mover el foco). Muestra también el espacio ocupado."""

    _ESTADOS = None  # se rellena en _init (necesita gettext ya cargado)

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Descargas de podcast"), size=(760, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Cola de descargas de podcast"))
        self.account_id = account_id
        self._items = []
        self._timer = None
        self._init_ui()
        self.CenterOnParent()
        self._cargar(anunciar=True)
        # Refresco periódico SOLO mientras el diálogo está abierto (no mueve el foco).
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self._cargar(anunciar=False), self._timer)
        self._timer.Start(1000)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl_espacio = wx.StaticText(self, label="")
        s.Add(self.lbl_espacio, 0, wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Episodios (Intro reproduce el descargado):")), 0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Cola de descargas de podcast"))
        self.lista.InsertColumn(0, _("Episodio"), width=300)
        self.lista.InsertColumn(1, _("Podcast"), width=150)
        self.lista.InsertColumn(2, _("Estado"), width=140)
        self.lista.InsertColumn(3, _("Progreso"), width=90)
        self.lista.InsertColumn(4, _("Tamaño"), width=80)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_reproducir)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etq, hnd, nombre in (
                (_("&Reproducir"), self._on_reproducir, _("Reproducir el episodio descargado")),
                (_("&Pausar"), self._on_pausar, _("Pausar la descarga")),
                (_("R&eanudar"), self._on_reanudar, _("Reanudar la descarga")),
                (_("&Cancelar"), self._on_cancelar, _("Cancelar y quitar de la cola")),
                (_("&Borrar descarga"), self._on_borrar, _("Borrar el archivo descargado"))):
            b = wx.Button(self, label=etq, name=nombre)
            b.Bind(wx.EVT_BUTTON, hnd)
            fila.Add(b, 0, wx.ALL, 3)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 3)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)

    def _on_close(self, event):
        if self._timer:
            self._timer.Stop()
        event.Skip()

    @staticmethod
    def _tam(n):
        n = float(n or 0)
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024 or u == "GB":
                return f"{int(n)} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return ""

    def _combinar(self):
        """Une la cola (en curso/pendiente/pausado/error) con las descargas ya hechas que
        no tengan fila de cola (descargadas antes de existir la cola). Deduplica por id."""
        cola = rss_service.obtener_cola_descargas(self.account_id)
        vistos = {c["article_id"] for c in cola}
        for d in rss_service.obtener_descargas_podcast(self.account_id):
            if d["article_id"] in vistos:
                continue
            cola.append({"article_id": d["article_id"], "title": d["title"],
                         "feed_title": d["feed_title"], "estado": "hecho",
                         "bytes_hechos": d["size"], "bytes_total": d["size"],
                         "porcentaje": 100, "error": "",
                         "reproducido": rss_service._episodio_reproducido(d["path"], None)})
        return cola

    def _cargar(self, anunciar=False):
        if not self:
            return
        estados = {"descargando": _("Descargando"), "cola": _("En cola"),
                   "pausado": _("Pausado"), "error": _("Error"), "hecho": _("Descargado")}
        foco_id = None
        idx_prev = self.lista.GetFocusedItem()
        if 0 <= idx_prev < len(self._items):
            foco_id = self._items[idx_prev]["article_id"]
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self._items = self._combinar()
        nuevo_foco = 0
        for i, it in enumerate(self._items):
            estado = estados.get(it["estado"], it["estado"])
            if it["estado"] == "hecho" and it.get("reproducido"):
                estado += ", " + _("escuchado")
            if it["estado"] == "error" and it.get("error"):
                estado = _("Error: {0}").format(it["error"][:40])
            prog = f"{it['porcentaje']}%" if it["estado"] in ("descargando", "pausado") else ""
            tam = self._tam(it["bytes_total"] or it["bytes_hechos"])
            pos = self.lista.InsertItem(i, it["title"] or _("(sin título)"))
            self.lista.SetItem(pos, 1, it["feed_title"] or "")
            self.lista.SetItem(pos, 2, estado)
            self.lista.SetItem(pos, 3, prog)
            self.lista.SetItem(pos, 4, tam)
            self.lista.establecer_lectura(pos, "{0}. {1}. {2}. {3} {4}".format(
                it["title"] or "", it["feed_title"] or "", estado, prog, tam))
            if it["article_id"] == foco_id:
                nuevo_foco = i
        # Espacio ocupado.
        try:
            ocup = rss_service.espacio_podcasts_bytes()
            self.lbl_espacio.SetLabel(_("Espacio de podcasts: {0}").format(self._tam(ocup)))
        except Exception:
            pass
        if self._items:
            self.lista.Focus(nuevo_foco)
            self.lista.Select(nuevo_foco)
        elif anunciar:
            speak(_("No hay descargas ni episodios en cola."), interrupt=True)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def _on_reproducir(self, event):
        it = self._sel()
        if not it:
            return
        if it["estado"] != "hecho":
            speak(_("Ese episodio aún no está descargado."), interrupt=True)
            return

        def _cb(ok, err):
            if not ok:
                notificar(_("No se pudo reproducir: {0}").format(err or ""))
        rss_service.reproducir_articulo_audio(it["article_id"], callback=_cb)
        speak(_("Reproduciendo: {0}").format(it["title"]), interrupt=True)

    def _on_pausar(self, event):
        it = self._sel()
        if it and it["estado"] in ("descargando", "cola"):
            rss_service.pausar_descarga(it["article_id"])
            speak(_("Descarga pausada."), interrupt=True)
            self._cargar()

    def _on_reanudar(self, event):
        it = self._sel()
        if it and it["estado"] in ("pausado", "error"):
            rss_service.reanudar_descarga(it["article_id"])
            speak(_("Descarga reanudada."), interrupt=True)
            self._cargar()

    def _on_cancelar(self, event):
        it = self._sel()
        if it and it["estado"] in ("descargando", "cola", "pausado", "error"):
            rss_service.cancelar_descarga(it["article_id"])
            speak(_("Descarga cancelada."), interrupt=True)
            self._cargar()

    def _on_borrar(self, event):
        it = self._sel()
        if not it or it["estado"] != "hecho":
            return
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Borrar la descarga de \"{0}\"?").format(it["title"]),
                     _("Borrar descarga")) != wx.ID_YES:
            return
        rss_service.borrar_descarga_podcast(it["article_id"])
        notificar(_("Descarga borrada."))
        self._cargar()
