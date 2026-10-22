# -*- coding: utf-8 -*-
"""Visor accesible del hilo de comentarios de una publicación de Reddit.

Muestra el post y su árbol de comentarios en orden de lectura (con el nivel de anidamiento
anunciado), con detalle del elemento enfocado, traducción, resumen con IA y apertura en el
navegador. Las acciones de escritura (responder, votar, guardar) se añaden en la Tanda 3.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
from datetime import datetime, timezone

import wx

from app.core.database import db
from app.core.speech import speak, notificar, copiar_al_portapapeles
from app.core.task_manager import task_manager
from app.services.reddit import reddit_service

log = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"


def _fecha_rel(epoch):
    try:
        from app.ui.main_frame import formatear_tiempo_relativo
        iso = datetime.fromtimestamp(float(epoch or 0), timezone.utc).isoformat()
        return formatear_tiempo_relativo(iso) if epoch else ""
    except Exception:
        return ""


class RedditThreadViewerDialog(wx.Dialog):
    """Visor del hilo de una publicación: el post y sus comentarios anidados."""

    def __init__(self, parent, account_id: int, post_db_id: str):
        super().__init__(
            parent, title=_("Comentarios de la publicación de Reddit"), size=(880, 640),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Visor de comentarios de Reddit"))
        self.frame = parent
        self.account_id = account_id
        self.post_db_id = post_db_id
        self.elementos = []   # [{'tipo':'post'|'comment', ...}]
        self._post_id = None
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "Publicación y comentarios en orden de lectura. Tecla Aplicaciones o el botón "
            "Acciones para actuar sobre el elemento enfocado.")), 0, wx.ALL, 6)
        from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Comentarios del hilo"))
        self.lista.InsertColumn(0, _("Nº"), width=50)
        self.lista.InsertColumn(1, _("Autor"), width=160)
        self.lista.InsertColumn(2, _("Comentario"), width=430)
        self.lista.InsertColumn(3, _("Puntos"), width=70)
        self.lista.InsertColumn(4, _("Fecha"), width=120)
        self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_seleccion)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, lambda e: self._menu())
        self.lista.Bind(wx.EVT_KEY_DOWN, self._on_key)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Texto completo:")), 0, wx.LEFT, 6)
        self.detalle = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 120),
            name=_("Texto completo del elemento enfocado"))
        s.Add(self.detalle, 0, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_acciones = wx.Button(self, label=_("&Acciones..."),
                                      name=_("Acciones sobre el elemento enfocado"))
        self.btn_acciones.Bind(wx.EVT_BUTTON, lambda e: self._menu())
        fila.Add(self.btn_acciones, 0, wx.ALL, 4)
        self.btn_resumir = wx.Button(self, label=_("&Resumir hilo con IA"),
                                     name=_("Resumir el hilo con inteligencia artificial"))
        self.btn_resumir.Bind(wx.EVT_BUTTON, self._on_resumir)
        fila.Add(self.btn_resumir, 0, wx.ALL, 4)
        self.btn_traducir = wx.Button(self, label=_("&Traducir"),
                                      name=_("Traducir el elemento enfocado"))
        self.btn_traducir.Bind(wx.EVT_BUTTON, self._on_traducir)
        fila.Add(self.btn_traducir, 0, wx.ALL, 4)
        self.btn_mas = wx.Button(self, label=_("Cargar &más comentarios"),
                                 name=_("Cargar más comentarios del hilo"))
        self.btn_mas.Bind(wx.EVT_BUTTON, lambda e: self._cargar(mas=True))
        fila.Add(self.btn_mas, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        try:
            from .accessible_controls import habilitar_escape_cierre
            habilitar_escape_cierre(self, wx.ID_CANCEL)
        except Exception:
            pass

    # ------------------------------------------------------------------ datos
    def _cargar(self, mas=False):
        row = db.query_one("SELECT post_id FROM reddit_posts WHERE id = ?", (self.post_db_id,))
        if not row or not row[0]:
            notificar(_("No se pudo localizar la publicación."))
            return
        self._post_id = row[0]
        speak(_("Cargando comentarios..."), interrupt=True)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los comentarios: {0}").format(err))
                return
            post = res.get("post") or {}
            coms = res.get("comments") or []
            self.elementos = [dict(post, tipo="post", depth=-1)] + [
                dict(c, tipo="comment") for c in coms]
            self._pintar()
            speak(_("Hilo cargado: {0} comentarios.").format(len(coms)), interrupt=True)

        reddit_service.cargar_hilo_async(self.account_id, self._post_id, callback=_fin)

    def _pintar(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, el in enumerate(self.elementos):
            if el["tipo"] == "post":
                autor = _("u/{0}").format(el.get("author", ""))
                cuerpo = (el.get("title") or "")
                if el.get("selftext"):
                    cuerpo += ". " + el["selftext"].replace("\n", " ")
                puntos = str(el.get("score", 0))
                fecha = _fecha_rel(el.get("created_utc"))
                lectura = _("Publicación. {0}: {1}. {2} puntos. {3}").format(autor, cuerpo, puntos, fecha)
            else:
                depth = int(el.get("depth", 0))
                autor = _("u/{0}").format(el.get("author", ""))
                cuerpo = (el.get("body") or "").replace("\n", " ")
                puntos = str(el.get("score", 0))
                fecha = _fecha_rel(el.get("created_utc"))
                nivel = _("Nivel {0}. ").format(depth + 1) if depth > 0 else ""
                extra = _("Autor original. ") if el.get("is_submitter") else ""
                lectura = f"{nivel}{extra}{autor}: {cuerpo}. {puntos} " + _("puntos") + f". {fecha}"
            self.lista.InsertItem(i, str(i))
            self.lista.SetItem(i, 1, autor)
            self.lista.SetItem(i, 2, cuerpo)
            self.lista.SetItem(i, 3, puntos)
            self.lista.SetItem(i, 4, fecha)
            self.lista.establecer_lectura(i, lectura)
        if self.elementos:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _actual(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or not (0 <= idx < len(self.elementos)):
            return None
        return self.elementos[idx]

    def _texto_de(self, el):
        if not el:
            return ""
        if el["tipo"] == "post":
            return f"{el.get('title','')}\n\n{el.get('selftext','')}".strip()
        return (el.get("body") or "").strip()

    def _on_seleccion(self, event):
        el = self._actual()
        if el is not None:
            self.detalle.SetValue(self._texto_de(el))
        event.Skip()

    # ------------------------------------------------------------------ acciones
    def _menu(self):
        el = self._actual()
        if el is None:
            speak(_("Enfoca primero un elemento del hilo."), interrupt=True)
            return
        menu = wx.Menu()
        opciones = [(_("Traducir"), "traducir"),
                    (_("Abrir en el navegador"), "navegador"),
                    (_("Copiar enlace"), "copiar")]
        # Las acciones de escritura (responder, votar, guardar) se añaden en la Tanda 3 vía el
        # controlador, si está disponible.
        ctrl = getattr(self.frame, "reddit_ctrl", None)
        if ctrl is not None and hasattr(ctrl, "acciones_visor_hilo"):
            try:
                ctrl.acciones_visor_hilo(self, menu, el)
            except Exception as e:
                log.error(f"Error al añadir acciones al visor de hilo de Reddit: {e}")
        for etiqueta, clave in opciones:
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, c=clave: self._accion(c), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _accion(self, cual):
        el = self._actual()
        if el is None:
            return
        if cual == "traducir":
            self._on_traducir(None)
        elif cual == "navegador":
            from app.core.privacy import abrir_url
            permalink = el.get("permalink") or ""
            if permalink:
                abrir_url(REDDIT_BASE + permalink)
        elif cual == "copiar":
            permalink = el.get("permalink") or ""
            if permalink:
                copiar_al_portapapeles(REDDIT_BASE + permalink, _("Enlace copiado al portapapeles."))

    def _on_key(self, event):
        k = event.GetKeyCode()
        if k == wx.WXK_WINDOWS_MENU:
            self._menu()
        elif event.ControlDown() and not event.ShiftDown() and k == ord("T"):
            self._on_traducir(None)
        else:
            event.Skip()

    # ------------------------------------------------------------------ IA / traducción
    def _on_traducir(self, event):
        el = self._actual()
        texto = self._texto_de(el)
        if not texto:
            speak(_("No hay texto que traducir."), interrupt=True)
            return
        speak(_("Traduciendo..."), interrupt=True)

        def _bg(ctx):
            from app.services.translation.translation_service import translation_service
            return translation_service.traducir(texto, idioma_destino="es")

        def _fin(res, err):
            if err:
                notificar(_("Error de traducción: {0}").format(err))
            else:
                from .accessible_controls import mostrar_info
                mostrar_info(self, res, _("Traducción"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_resumir(self, event):
        if not self.elementos:
            return
        lineas = []
        for el in self.elementos:
            if el["tipo"] == "post":
                lineas.append(f"[Publicación] {el.get('title','')}: {el.get('selftext','')}")
            else:
                lineas.append(f"u/{el.get('author','')}: {el.get('body','')}")
        texto = "\n\n".join(lineas)
        speak(_("Resumiendo el hilo..."), interrupt=True)

        def _bg(ctx):
            from app.services.ai.ai_service import ai_service
            return ai_service.resumir_texto("Resume este hilo de comentarios de Reddit:\n\n" + texto)

        def _fin(res, err):
            if err:
                notificar(_("Error al resumir: {0}").format(err))
            else:
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Resumen del hilo con IA"), res)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
