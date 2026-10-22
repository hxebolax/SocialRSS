# -*- coding: utf-8 -*-
"""Editar un feed RSS desde el árbol: título, DIRECCIÓN, carpeta, intervalo, identificación,
imágenes, texto completo y pausa, con un botón que comprueba la dirección y explica qué pasa
(código del servidor, si solo responde a navegadores, si redirige, si es un feed y cuántos
artículos trae, o qué feeds hay en esa página).

Sustituye a los antiguos "Ajustes de este feed", que eran una cadena de preguntas sueltas y no
permitían cambiar la dirección."""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service
from .accessible_controls import AccessibleTextCtrl, habilitar_escape_cierre, mostrar_info

# Identificación con la que se pide el feed.
_IDENTIDADES = ("", "navegador", "propia")


class EditarFeedDialog(wx.Dialog):
    def __init__(self, parent, feed_id):
        super().__init__(parent, title=_("Editar feed"), size=(640, 700),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Editar feed"))
        self.feed_id = feed_id
        fila = db.query_one(
            "SELECT account_id, title, url, category_id, COALESCE(custom_interval,0), "
            "COALESCE(custom_user_agent,''), COALESCE(no_images,0), COALESCE(force_fulltext,0), "
            "COALESCE(paused,0), COALESCE(last_error,'') FROM rss_feeds WHERE id = ?", (feed_id,))
        if not fila:
            raise ValueError(_("El feed ya no existe."))
        (self.account_id, titulo, url, cat_id, intervalo, ua, sin_img, completo, pausado,
         ultimo_error) = fila
        self._url_original = url
        self._cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name",
                              (self.account_id,))
        # Panel NORMAL (no con desplazamiento): con un ScrolledWindow, hasta que la ventana se
        # maquetaba el panel medía 20 píxeles y el cuadro del resultado quedaba tan estrecho
        # que el lector de pantalla lo leía palabra a palabra al bajar con las flechas.
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(panel, label=_("&Título:")), 0, wx.LEFT | wx.TOP, 6)
        self.txt_titulo = AccessibleTextCtrl(panel, value=titulo or "", name=_("Título del feed"))
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 4)

        s.Add(wx.StaticText(panel, label=_("&Dirección (URL) del feed:")), 0, wx.LEFT | wx.TOP, 6)
        self.txt_url = AccessibleTextCtrl(panel, value=url or "", name=_("Dirección del feed"),
                                          ayuda_texto=_("Puedes pegar también la dirección de una web: "
                                                        "«Comprobar» busca sus feeds."))
        s.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 4)
        fila_btn = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_comprobar = wx.Button(panel, label=_("C&omprobar la dirección"),
                                       name=_("Comprobar la dirección del feed"))
        self.btn_comprobar.Bind(wx.EVT_BUTTON, self._on_comprobar)
        fila_btn.Add(self.btn_comprobar, 0, wx.ALL, 4)
        self.btn_usar = wx.Button(panel, label=_("&Usar la dirección sugerida"),
                                  name=_("Usar la dirección sugerida por la comprobación"))
        self.btn_usar.Bind(wx.EVT_BUTTON, self._on_usar_sugerida)
        self.btn_usar.Disable()
        fila_btn.Add(self.btn_usar, 0, wx.ALL, 4)
        s.Add(fila_btn, 0)
        s.Add(wx.StaticText(panel, label=_("Resultado de la comprobación:")), 0, wx.LEFT, 6)
        self.txt_resultado = wx.TextCtrl(
            panel, value=(_("Último error al actualizar: {0}").format(ultimo_error)
                          if ultimo_error else ""),
            style=wx.TE_MULTILINE | wx.TE_READONLY, size=(600, 110),
            name=_("Resultado de la comprobación de la dirección"))
        self.txt_resultado.SetMinSize((560, 110))
        s.Add(self.txt_resultado, 0, wx.EXPAND | wx.ALL, 4)
        self._sugerida = ""
        self._navegador_detectado = False

        s.Add(wx.StaticText(panel, label=_("&Carpeta:")), 0, wx.LEFT | wx.TOP, 6)
        self.choice_cat = wx.Choice(panel, choices=[_("Sin carpeta")] + [n for _i, n in self._cats],
                                    name=_("Carpeta del feed"))
        ids = [None] + [i for i, _n in self._cats]
        self.choice_cat.SetSelection(ids.index(cat_id) if cat_id in ids else 0)
        s.Add(self.choice_cat, 0, wx.EXPAND | wx.ALL, 4)

        s.Add(wx.StaticText(panel, label=_("&Intervalo de actualización propio en minutos (0 = el general):")),
              0, wx.LEFT | wx.TOP, 6)
        self.spin_intervalo = wx.SpinCtrl(panel, min=0, max=10080, initial=int(intervalo or 0),
                                          name=_("Intervalo propio en minutos"))
        s.Add(self.spin_intervalo, 0, wx.ALL, 4)

        s.Add(wx.StaticText(panel, label=_("&Identificarse ante el servidor como:")), 0, wx.LEFT | wx.TOP, 6)
        self.choice_ident = wx.Choice(panel, choices=[
            _("La aplicación (y como navegador si el servidor la rechaza)"),
            _("Un navegador (Firefox)"),
            _("Una identificación propia (User-Agent)")], name=_("Identificación ante el servidor"))
        modo = "" if not ua else ("navegador" if ua == "navegador" else "propia")
        self.choice_ident.SetSelection(_IDENTIDADES.index(modo))
        self.choice_ident.Bind(wx.EVT_CHOICE, lambda e: self._ajustar_ua())
        s.Add(self.choice_ident, 0, wx.EXPAND | wx.ALL, 4)
        self.txt_ua = AccessibleTextCtrl(panel, value=ua if modo == "propia" else "",
                                         name=_("Identificación propia (User-Agent)"))
        s.Add(self.txt_ua, 0, wx.EXPAND | wx.ALL, 4)

        self.cb_sin_img = wx.CheckBox(panel, label=_("No descargar las imágenes de este feed"),
                                      name=_("No descargar imágenes"))
        self.cb_sin_img.SetValue(bool(sin_img))
        self.cb_completo = wx.CheckBox(panel, label=_("Descargar siempre el artículo completo (si el feed solo trae el resumen)"),
                                       name=_("Descargar siempre el artículo completo"))
        self.cb_completo.SetValue(bool(completo))
        self.cb_pausa = wx.CheckBox(panel, label=_("Feed en pausa (no se actualiza)"), name=_("Feed en pausa"))
        self.cb_pausa.SetValue(bool(pausado))
        from app.services.rss.rss_avisos import feed_silenciado
        self.cb_silencio = wx.CheckBox(panel, label=_("No avisar de sus artículos nuevos (sin sonido ni notificación)"),
                                       name=_("No avisar de los artículos nuevos de este feed"))
        self.cb_silencio.SetValue(feed_silenciado(feed_id))
        for cb in (self.cb_sin_img, self.cb_completo, self.cb_pausa, self.cb_silencio):
            s.Add(cb, 0, wx.ALL, 6)
        from app.services.rss.rss_borrados import MODOS, modo_de_feed
        s.Add(wx.StaticText(panel, label=_("Al pulsar &Suprimir en sus artículos:")), 0, wx.LEFT | wx.TOP, 6)
        self.choice_suprimir = wx.Choice(panel, choices=[
            _("Llevarlos a la papelera (se pueden recuperar)"),
            _("Borrarlos definitivamente (no vuelven a aparecer)")], name=_("Qué hace Suprimir en este feed"))
        self.choice_suprimir.SetSelection(MODOS.index(modo_de_feed(feed_id)))
        s.Add(self.choice_suprimir, 0, wx.EXPAND | wx.ALL, 4)

        panel.SetSizer(s)
        raiz = wx.BoxSizer(wx.VERTICAL)
        raiz.Add(panel, 1, wx.EXPAND)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        botones.Add(ok, 0, wx.ALL, 6)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cance&lar")), 0, wx.ALL, 6)
        raiz.Add(botones, 0, wx.ALIGN_RIGHT)
        self.SetSizer(raiz)
        habilitar_escape_cierre(self)
        self._ajustar_ua()
        self.Layout()   # maquetar YA: los controles tienen su tamaño real antes de mostrarse
        self.CenterOnParent()
        self.txt_titulo.SetFocus()

    def _ajustar_ua(self):
        self.txt_ua.Enable(self.choice_ident.GetSelection() == 2)

    def _on_comprobar(self, event):
        url = self.txt_url.GetValue().strip()
        if not url:
            speak(_("Escribe primero la dirección."), interrupt=True)
            return
        ident = _IDENTIDADES[self.choice_ident.GetSelection()]
        ua = self.txt_ua.GetValue().strip() if ident == "propia" else (
            "navegador" if ident == "navegador" else "")
        self.btn_comprobar.Disable()
        self.txt_resultado.SetValue(_("Comprobando la dirección..."))
        speak(_("Comprobando la dirección..."), interrupt=True)

        def _bg(ctx):
            return rss_service.diagnosticar_url(url, ua)

        def _fin(res, err):
            if not self:
                return
            self.btn_comprobar.Enable()
            if err or not res:
                self.txt_resultado.SetValue(_("No se pudo comprobar: {0}").format(err))
                notificar(_("No se pudo comprobar la dirección."))
                return
            self.txt_resultado.SetValue(res["texto"])
            self._sugerida = res.get("url_sugerida") or ""
            self._navegador_detectado = bool(res.get("navegador"))
            self.btn_usar.Enable(bool(self._sugerida) and
                                 self._sugerida.rstrip("/") != url.rstrip("/"))
            # El resultado se lee al llegar: el usuario lo pidió y está esperándolo.
            notificar(_("Comprobación terminada: {0}").format(
                _("es un feed válido") if res.get("valido") else
                (_("hay una dirección sugerida") if self._sugerida else _("no es un feed"))))
            self.txt_resultado.SetFocus()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_usar_sugerida(self, event):
        if self._sugerida:
            self.txt_url.SetValue(self._sugerida)
            speak(_("Dirección cambiada a la sugerida. Pulsa Guardar para aplicarla."), interrupt=True)
            self.txt_url.SetFocus()

    def _on_guardar(self, event):
        titulo = self.txt_titulo.GetValue().strip()
        url = self.txt_url.GetValue().strip()
        if not url:
            mostrar_info(self, _("La dirección no puede quedar vacía."), _("Editar feed"))
            return
        ident = _IDENTIDADES[self.choice_ident.GetSelection()]
        if ident == "propia":
            ua = self.txt_ua.GetValue().strip()
        elif ident == "navegador" or self._navegador_detectado:
            ua = "navegador"
        else:
            ua = ""
        sel = self.choice_cat.GetSelection()
        cat = None if sel <= 0 else self._cats[sel - 1][0]
        try:
            self.cambio_url = rss_service.editar_feed(
                self.feed_id, titulo=titulo or None, url=url, category_id=cat,
                custom_interval=int(self.spin_intervalo.GetValue()), custom_user_agent=ua,
                no_images=self.cb_sin_img.GetValue(), force_fulltext=self.cb_completo.GetValue(),
                paused=self.cb_pausa.GetValue())
        except ValueError as e:
            mostrar_info(self, str(e), _("Editar feed"))
            return
        from app.services.rss.rss_avisos import fijar_feed_silenciado
        from app.services.rss.rss_borrados import MODOS, fijar_modo_de_feed
        fijar_feed_silenciado(self.feed_id, self.cb_silencio.GetValue())
        fijar_modo_de_feed(self.feed_id, MODOS[self.choice_suprimir.GetSelection()])
        self.EndModal(wx.ID_OK)
