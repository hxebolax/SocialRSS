# -*- coding: utf-8 -*-
"""Complemento: Suscripciones de YouTube.

Inicia sesión usando las cookies del navegador (sin pedir usuario/contraseña), obtiene los canales
a los que estás suscrito en YouTube, te deja ELEGIR cuáles con una lista de casillas accesible y los
suscribe en tu app como feeds, opcionalmente dentro de una carpeta. De cada canal trae TODO su
catálogo de vídeos (sin límite); las novedades posteriores entran solas por el feed.
"""
from app.core.plugins.api import PluginBase, PluginInfo


class YoutubeSuscripcionesPlugin(PluginBase):
    @staticmethod
    def get_info() -> PluginInfo:
        return PluginInfo(
            id="youtube_suscripciones",
            name="Suscripciones de YouTube",
            version="1.0.0",
            author="SocialRSS",
            description=("Inicia sesión con las cookies de tu navegador, lee tus suscripciones de "
                         "YouTube y te deja elegir qué canales seguir en la app (con todo su "
                         "catálogo de vídeos)."),
            has_settings=False,
        )

    def on_enable(self) -> bool:
        self.api.add_menu_item(_("Suscripciones de YouTube..."), self._abrir)
        return True

    def _abrir(self):
        cls = _obtener_dialogo()
        dlg = cls(self.api.get_main_window())
        dlg.ShowModal()
        dlg.Destroy()


def _crear_dialogo_class():
    """Define la clase del diálogo de forma perezosa (necesita wx, que existe en ejecución)."""
    import wx
    from app.core.speech import speak, notificar
    from app.core.task_manager import task_manager
    from app.core.database import db
    from app.services.rss.rss_service import rss_service
    from app.services.rss.rss_youtube import NAVEGADORES_COOKIES
    from app.ui.accessible_controls import AccessibleCheckListBox

    class _Dialog(wx.Dialog):
        def __init__(self, parent):
            super().__init__(parent, title=_("Suscripciones de YouTube"), size=(640, 560),
                             style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                             name=_("Suscripciones de YouTube"))
            self._canales = []
            self._cats = []
            self._init_ui()
            self.CenterOnParent()

        def _init_ui(self):
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(wx.StaticText(self, label=_("Navegador donde tienes la sesión de YouTube abierta:")), 0, wx.ALL, 6)
            self.choice_nav = wx.Choice(self, choices=[n.capitalize() for n in NAVEGADORES_COOKIES],
                                        name=_("Navegador para las cookies"))
            self.choice_nav.SetSelection(0)
            s.Add(self.choice_nav, 0, wx.ALL, 6)

            self.btn_obtener = wx.Button(self, label=_("&Obtener mis suscripciones"), name=_("Obtener las suscripciones de YouTube"))
            self.btn_obtener.Bind(wx.EVT_BUTTON, self._on_obtener)
            s.Add(self.btn_obtener, 0, wx.ALL, 6)

            s.Add(wx.StaticText(self, label=_("Marca los canales que quieras seguir:")), 0, wx.LEFT | wx.TOP, 6)
            self.clb = AccessibleCheckListBox(self, size=(-1, 220), name=_("Lista de canales suscritos"))
            s.Add(self.clb, 1, wx.EXPAND | wx.ALL, 6)

            fila_m = wx.BoxSizer(wx.HORIZONTAL)
            self.btn_todos = wx.Button(self, label=_("&Marcar / desmarcar todos"))
            self.btn_todos.Bind(wx.EVT_BUTTON, self._on_todos)
            fila_m.Add(self.btn_todos, 0, wx.ALL, 4)
            s.Add(fila_m, 0)

            s.Add(wx.StaticText(self, label=_("Carpeta (categoría) donde clasificarlos:")), 0, wx.LEFT | wx.TOP, 6)
            self.choice_cat = wx.Choice(self, name=_("Carpeta de destino"))
            self._cargar_categorias()
            s.Add(self.choice_cat, 0, wx.EXPAND | wx.ALL, 6)
            self.btn_nueva_cat = wx.Button(self, label=_("Crear &carpeta nueva..."))
            self.btn_nueva_cat.Bind(wx.EVT_BUTTON, self._on_nueva_cat)
            s.Add(self.btn_nueva_cat, 0, wx.ALL, 4)

            btns = wx.BoxSizer(wx.HORIZONTAL)
            self.btn_suscribir = wx.Button(self, label=_("&Suscribir seleccionados"), name=_("Suscribir los canales marcados"))
            self.btn_suscribir.Bind(wx.EVT_BUTTON, self._on_suscribir)
            self.btn_suscribir.Disable()
            btns.Add(self.btn_suscribir, 0, wx.ALL, 5)
            btns.AddStretchSpacer()
            btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
            s.Add(btns, 0, wx.EXPAND | wx.ALL, 6)
            self.SetSizer(s)

        def _cargar_categorias(self):
            self._cats = rss_service.obtener_categorias()
            self.choice_cat.SetItems([_("Sin carpeta")] + [c["name"] for c in self._cats])
            self.choice_cat.SetSelection(0)

        def _on_nueva_cat(self, event):
            from app.ui.accessible_controls import CustomTextEntryDialog
            d = CustomTextEntryDialog(self, _("Nombre de la carpeta nueva:"), _("Crear carpeta"))
            nombre = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
            d.Destroy()
            if nombre:
                rss_service.agregar_categoria(nombre)
                self._cargar_categorias()
                # Seleccionar la recién creada.
                for i, c in enumerate(self._cats):
                    if c["name"] == nombre:
                        self.choice_cat.SetSelection(i + 1)
                        break
                # Que la carpeta aparezca YA en el árbol (sin esperar a reiniciar).
                try:
                    self.GetParent()._cargar_navegacion()
                except Exception:
                    pass

        def _on_obtener(self, event):
            nav = NAVEGADORES_COOKIES[self.choice_nav.GetSelection()]
            speak(_("Obteniendo tus suscripciones de YouTube..."), interrupt=True)
            self.btn_obtener.Disable()

            def _bg(ctx):
                return rss_service.youtube_listar_suscripciones(nav)

            def _finish(res, err):
                self.btn_obtener.Enable()
                if err:
                    notificar(_("No se pudieron obtener las suscripciones: {0}. ¿Has iniciado sesión "
                                "en YouTube en ese navegador?").format(err))
                    return
                self._canales = res or []
                self.clb.Clear()
                for c in self._canales:
                    self.clb.Append(c.get("title") or c.get("channel_id") or "")
                if self._canales:
                    self.btn_suscribir.Enable()
                    speak(_("{0} canales. Marca los que quieras y pulsa Suscribir.").format(len(self._canales)), interrupt=True)
                else:
                    speak(_("No se encontraron suscripciones."), interrupt=True)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)

        def _on_todos(self, event):
            n = self.clb.GetCount()
            if not n:
                return
            todos = all(self.clb.IsChecked(i) for i in range(n))
            for i in range(n):
                self.clb.Check(i, not todos)

        def _cuenta_rss(self):
            acc = getattr(self.GetParent(), "active_rss_account_id", None)
            if acc:
                return acc
            row = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1 ORDER BY position, id LIMIT 1")
            return row[0] if row else None

        def _on_suscribir(self, event):
            marcados = [self._canales[i] for i in range(self.clb.GetCount()) if self.clb.IsChecked(i)]
            if not marcados:
                speak(_("Marca al menos un canal."), interrupt=True)
                return
            cuenta = self._cuenta_rss()
            if not cuenta:
                notificar(_("Crea primero una cuenta de RSS en Preferencias."))
                return
            cat_sel = self.choice_cat.GetSelection()
            category_id = self._cats[cat_sel - 1]["id"] if cat_sel > 0 else None
            nav = NAVEGADORES_COOKIES[self.choice_nav.GetSelection()]
            self.btn_suscribir.Disable()
            total = len(marcados)
            # Diálogo de progreso de la importación, para que el usuario sepa el estado.
            self._prog = wx.ProgressDialog(
                _("Importando suscripciones de YouTube"),
                _("Preparando..."), maximum=total, parent=self,
                style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE)
            speak(_("Importando {0} canales. Esto puede tardar...").format(total), interrupt=True)

            def _avanzar(i, titulo):
                try:
                    self._prog.Update(i, _("Canal {0} de {1}: {2}\nTrayendo todos sus vídeos...").format(
                        i + 1, total, titulo))
                except Exception:
                    pass

            def _bg(ctx):
                total_ok, total_videos = 0, 0
                for i, canal in enumerate(marcados):
                    wx.CallAfter(_avanzar, i, canal.get("title", ""))
                    try:
                        r = rss_service.youtube_suscribir_canal_completo(
                            cuenta, canal, category_id=category_id, cookies_browser=nav)
                        total_ok += 1
                        total_videos += r.get("videos", 0)
                    except Exception as ex:
                        import logging
                        logging.getLogger(__name__).error(f"Canal de YouTube falló: {ex}")
                return (total_ok, total_videos)

            def _finish(res, err):
                try:
                    self._prog.Destroy()
                except Exception:
                    pass
                self.btn_suscribir.Enable()
                if err:
                    notificar(_("Error al suscribir: {0}").format(err))
                    return
                ok, vids = res or (0, 0)
                # Refrescar el árbol para que aparezcan ya los canales/carpeta importados.
                try:
                    self.GetParent()._cargar_navegacion()
                except Exception:
                    pass
                notificar(_("Importación terminada: {0} canales y {1} vídeos.").format(ok, vids))
                self.EndModal(wx.ID_OK)
            task_manager.start_task(_bg, callback=_finish, pass_context=True)

    return _Dialog


# El diálogo se crea perezosamente la primera vez que se usa (para no requerir wx al importar).
_DIALOGO_CACHE = None


def _obtener_dialogo():
    global _DIALOGO_CACHE
    if _DIALOGO_CACHE is None:
        _DIALOGO_CACHE = _crear_dialogo_class()
    return _DIALOGO_CACHE
