# -*- coding: utf-8 -*-
"""Complemento: Web Scraper (módulos por sitio).

Permite leer páginas que NO tienen feed RSS escribiendo un MÓDULO personalizado por sitio. Cada
módulo es un archivo Python en data/plugins/web_scraper/scrapers/ que define una subclase de
SiteScraper con can_handle(url) y scrape(url). El complemento descubre esos módulos, los registra, y
deja crear un feed asociado a uno de ellos: al actualizarlo se vuelve a llamar al módulo.

Así, quien quiera leer una web concreta (un foro, un sitio con login o paginación, una estructura
rara…) puede tener su propio web scraper a medida creando el módulo correspondiente. Hay una
plantilla comentada en scrapers/_plantilla.py y una guía en scrapers/LEEME.md.
"""
import logging
import os

from app.core.plugins.api import PluginBase, PluginInfo

log = logging.getLogger(__name__)


class WebScraperPlugin(PluginBase):
    @staticmethod
    def get_info() -> PluginInfo:
        return PluginInfo(
            id="web_scraper",
            name="Web Scraper (módulos por sitio)",
            version="1.0.0",
            author="SocialRSS",
            description=("Lee webs sin RSS con módulos a medida por sitio. Escribe un módulo en "
                         "data/plugins/web_scraper/scrapers/ y crea un feed que lo use."),
            has_settings=False,
        )

    def on_enable(self) -> bool:
        self._cargados = []
        self._descubrir_modulos()
        self.api.add_menu_item(_("Crear feed con scraper de sitio..."), self._abrir)
        return True

    def on_disable(self):
        # Quitar del registro los scrapers que cargó este complemento.
        from app.services.rss import rss_scraper
        for inst in getattr(self, "_cargados", []):
            try:
                rss_scraper._SCRAPERS.pop(getattr(inst, "ID", ""), None)
            except Exception:
                pass
        self._cargados = []

    def _descubrir_modulos(self):
        """Carga cada módulo de scrapers/ (salvo los que empiezan por _ o .) y registra sus
        subclases de SiteScraper."""
        import importlib.util
        from app.services.rss import rss_scraper
        carpeta = os.path.join(os.path.dirname(__file__), "scrapers")
        if not os.path.isdir(carpeta):
            return
        for nombre in sorted(os.listdir(carpeta)):
            if not nombre.endswith(".py") or nombre.startswith(("_", ".")):
                continue
            ruta = os.path.join(carpeta, nombre)
            try:
                spec = importlib.util.spec_from_file_location(f"scraper_sitio_{nombre[:-3]}", ruta)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr in vars(mod).values():
                    if (isinstance(attr, type) and issubclass(attr, rss_scraper.SiteScraper)
                            and attr is not rss_scraper.SiteScraper):
                        inst = attr()
                        if getattr(inst, "ID", ""):
                            rss_scraper.registrar_scraper(inst)
                            self._cargados.append(inst)
                            log.info(f"Scraper de sitio registrado: {inst.ID} ({inst.NAME})")
            except Exception as e:
                log.error(f"No se pudo cargar el módulo de scraper '{nombre}': {e}")

    def _abrir(self):
        cls = _obtener_dialogo()
        dlg = cls(self.api.get_main_window())
        dlg.ShowModal()
        dlg.Destroy()


def _crear_dialogo_class():
    import wx
    from app.core.speech import speak, notificar
    from app.core.task_manager import task_manager
    from app.core.database import db
    from app.services.rss.rss_service import rss_service
    from app.services.rss import rss_scraper as _sc
    from app.ui.accessible_controls import AccessibleListCtrl, AccessibleTextCtrl

    class _Dialog(wx.Dialog):
        def __init__(self, parent):
            super().__init__(parent, title=_("Crear feed con scraper de sitio"), size=(640, 540),
                             style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                             name=_("Crear feed con scraper de sitio"))
            self._scrapers = _sc.listar_scrapers()
            self._items = []
            self._scraper_id = ""
            self._init_ui()
            self.CenterOnParent()

        def _init_ui(self):
            s = wx.BoxSizer(wx.VERTICAL)
            if not self._scrapers:
                s.Add(wx.StaticText(self, label=_(
                    "No hay ningún módulo de scraper instalado. Crea uno copiando la plantilla de\n"
                    "data/plugins/web_scraper/scrapers/_plantilla.py y reinicia la app.")), 0, wx.ALL, 10)
                s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
                self.SetSizer(s)
                return
            s.Add(wx.StaticText(self, label=_("Dirección de la web a leer:")), 0, wx.ALL, 6)
            self.txt_url = AccessibleTextCtrl(self, name=_("Dirección de la web"))
            s.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 6)

            s.Add(wx.StaticText(self, label=_("Módulo de scraper (Automático lo detecta por la URL):")), 0, wx.LEFT, 6)
            self.choice_scraper = wx.Choice(
                self, choices=[_("Automático")] + [sc.NAME or sc.ID for sc in self._scrapers],
                name=_("Módulo de scraper de sitio"))
            self.choice_scraper.SetSelection(0)
            s.Add(self.choice_scraper, 0, wx.EXPAND | wx.ALL, 6)

            self.btn_probar = wx.Button(self, label=_("&Probar"), name=_("Probar el scraper con esa URL"))
            self.btn_probar.Bind(wx.EVT_BUTTON, self._on_probar)
            s.Add(self.btn_probar, 0, wx.ALL, 6)

            s.Add(wx.StaticText(self, label=_("Artículos detectados:")), 0, wx.LEFT | wx.TOP, 6)
            self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                            name=_("Artículos detectados por el scraper"))
            self.lista.InsertColumn(0, _("Título"), width=560)
            s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

            s.Add(wx.StaticText(self, label=_("Título del feed:")), 0, wx.LEFT, 6)
            self.txt_title = AccessibleTextCtrl(self, name=_("Título del feed"))
            s.Add(self.txt_title, 0, wx.EXPAND | wx.ALL, 6)

            btns = wx.BoxSizer(wx.HORIZONTAL)
            self.btn_crear = wx.Button(self, wx.ID_OK, _("&Crear feed"))
            self.btn_crear.Bind(wx.EVT_BUTTON, self._on_crear)
            self.btn_crear.Disable()
            btns.Add(self.btn_crear, 0, wx.ALL, 5)
            btns.AddStretchSpacer()
            btns.Add(wx.Button(self, wx.ID_CANCEL, _("Cancela&r")), 0, wx.ALL, 5)
            s.Add(btns, 0, wx.EXPAND | wx.ALL, 6)
            self.SetSizer(s)

        def _scraper_elegido(self, url):
            sel = self.choice_scraper.GetSelection()
            if sel > 0:
                return self._scrapers[sel - 1]
            return _sc.scraper_para_url(url)   # automático

        def _on_probar(self, event):
            url = self.txt_url.GetValue().strip()
            if not url:
                speak(_("Escribe la dirección de la web."), interrupt=True)
                return
            if not url.startswith("http"):
                url = "https://" + url
            sc = self._scraper_elegido(url)
            if sc is None:
                notificar(_("Ningún módulo sabe manejar esa URL. Elige uno en la lista o crea su módulo."))
                return
            self._scraper_id = sc.ID
            speak(_("Probando con {0}...").format(sc.NAME or sc.ID), interrupt=True)
            self.btn_probar.Disable()

            def _bg(ctx):
                return _sc.scrapear_con_modulo(sc.ID, url)

            def _finish(res, err):
                self.btn_probar.Enable()
                if err:
                    notificar(_("El scraper falló: {0}").format(err))
                    return
                self._items = res or []
                self.lista.DeleteAllItems()
                self.lista.limpiar_lecturas()
                for i, it in enumerate(self._items):
                    self.lista.InsertItem(i, it.get("title", "") or _("(sin título)"))
                if not self.txt_title.GetValue().strip():
                    self.txt_title.SetValue(sc.NAME or url)
                if self._items:
                    self.btn_crear.Enable()
                    speak(_("{0} artículos. Revisa y pulsa Crear feed.").format(len(self._items)), interrupt=True)
                else:
                    speak(_("El scraper no devolvió artículos."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)

        def _cuenta_rss(self):
            acc = getattr(self.GetParent(), "active_rss_account_id", None)
            if acc:
                return acc
            row = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1 ORDER BY position, id LIMIT 1")
            return row[0] if row else None

        def _on_crear(self, event):
            url = self.txt_url.GetValue().strip()
            if not url.startswith("http"):
                url = "https://" + url
            if not self._items or not self._scraper_id:
                speak(_("Primero prueba el scraper."), interrupt=True)
                return
            cuenta = self._cuenta_rss()
            if not cuenta:
                notificar(_("Crea primero una cuenta de RSS en Preferencias."))
                return
            title = self.txt_title.GetValue().strip()
            sid = self._scraper_id
            speak(_("Creando el feed..."), interrupt=True)

            def _bg(ctx):
                return rss_service.crear_feed_web(cuenta, url, title, None, "", scraper_module=sid, ctx=ctx)

            def _finish(res, err):
                if err:
                    notificar(_("No se pudo crear el feed: {0}").format(err))
                    return
                try:
                    self.GetParent()._cargar_navegacion()
                except Exception:
                    pass
                notificar(_("Feed creado con el scraper de sitio."))
                self.EndModal(wx.ID_OK)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)

    return _Dialog


_DIALOGO_CACHE = None


def _obtener_dialogo():
    global _DIALOGO_CACHE
    if _DIALOGO_CACHE is None:
        _DIALOGO_CACHE = _crear_dialogo_class()
    return _DIALOGO_CACHE
