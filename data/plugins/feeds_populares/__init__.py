# -*- coding: utf-8 -*-
"""Complemento: Feeds populares (catálogo rápido).

Ofrece un catálogo de feeds RSS conocidos, agrupados por tema, para suscribirte de un clic con una
lista de casillas accesible. Inspirado en el plugin "quick feeds" de SimpleRSS; catálogo propio.
"""
from app.core.plugins.api import PluginBase, PluginInfo

# Catálogo curado (tema -> lista de (nombre, url de feed)). URLs públicas de feeds RSS conocidos.
CATALOGO = {
    "Tecnología": [
        ("Xataka", "https://www.xataka.com/feedburner.xml"),
        ("Genbeta", "https://www.genbeta.com/feedburner.xml"),
        ("Hacker News", "https://news.ycombinator.com/rss"),
        ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ],
    "Noticias": [
        ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml"),
        ("elDiario.es", "https://www.eldiario.es/rss/"),
        ("Reuters mundo", "https://www.reutersagency.com/feed/"),
    ],
    "Ciencia": [
        ("Microsiervos", "https://www.microsiervos.com/index.xml"),
        ("NASA", "https://www.nasa.gov/feed/"),
    ],
    "Accesibilidad": [
        ("Blog de NVDA", "https://www.nvaccess.org/feed/"),
    ],
}


class FeedsPopularesPlugin(PluginBase):
    @staticmethod
    def get_info() -> PluginInfo:
        return PluginInfo(
            id="feeds_populares",
            name="Feeds populares",
            version="1.0.0",
            author="SocialRSS",
            description=("Un catálogo de feeds RSS conocidos, por temas, para suscribirte de un clic "
                         "con una lista de casillas accesible."),
            has_settings=False,
        )

    def on_enable(self) -> bool:
        self.api.add_menu_item(_("Feeds populares..."), self._abrir)
        return True

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
    from app.ui.accessible_controls import AccessibleCheckListBox

    class _Dialog(wx.Dialog):
        def __init__(self, parent):
            super().__init__(parent, title=_("Feeds populares"), size=(560, 480),
                             style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                             name=_("Catálogo de feeds populares"))
            # Aplanar el catálogo a [(etiqueta, url)] con el tema como prefijo.
            self._items = []
            for tema, feeds in CATALOGO.items():
                for nombre, url in feeds:
                    self._items.append((f"{tema}: {nombre}", url))
            self._init_ui()
            self.CenterOnParent()

        def _init_ui(self):
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(wx.StaticText(self, label=_("Marca los feeds que quieras añadir:")), 0, wx.ALL, 6)
            self.clb = AccessibleCheckListBox(self, choices=[e for e, _u in self._items],
                                              size=(-1, 300), name=_("Lista de feeds populares"))
            s.Add(self.clb, 1, wx.EXPAND | wx.ALL, 6)
            btns = wx.BoxSizer(wx.HORIZONTAL)
            self.btn_add = wx.Button(self, label=_("&Añadir seleccionados"), name=_("Añadir los feeds marcados"))
            self.btn_add.Bind(wx.EVT_BUTTON, self._on_add)
            btns.Add(self.btn_add, 0, wx.ALL, 5)
            btns.AddStretchSpacer()
            btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
            s.Add(btns, 0, wx.EXPAND | wx.ALL, 6)
            self.SetSizer(s)

        def _cuenta_rss(self):
            acc = getattr(self.GetParent(), "active_rss_account_id", None)
            if acc:
                return acc
            row = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1 ORDER BY position, id LIMIT 1")
            return row[0] if row else None

        def _on_add(self, event):
            urls = [self._items[i][1] for i in range(self.clb.GetCount()) if self.clb.IsChecked(i)]
            if not urls:
                speak(_("Marca al menos un feed."), interrupt=True)
                return
            cuenta = self._cuenta_rss()
            if not cuenta:
                notificar(_("Crea primero una cuenta de RSS en Preferencias."))
                return
            self.btn_add.Disable()
            speak(_("Añadiendo {0} feeds...").format(len(urls)), interrupt=True)

            def _bg(ctx):
                ok = 0
                for u in urls:
                    try:
                        rss_service.añadir_feed(account_id=cuenta, url=u, ctx=ctx)
                        ok += 1
                    except Exception:
                        pass
                return ok

            def _finish(res, err):
                self.btn_add.Enable()
                try:
                    self.GetParent()._cargar_navegacion()
                except Exception:
                    pass
                notificar(_("Añadidos {0} feeds.").format(res or 0))
                self.EndModal(wx.ID_OK)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)

    return _Dialog


_DIALOGO_CACHE = None


def _obtener_dialogo():
    global _DIALOGO_CACHE
    if _DIALOGO_CACHE is None:
        _DIALOGO_CACHE = _crear_dialogo_class()
    return _DIALOGO_CACHE
