# -*- coding: utf-8 -*-
"""Buscar feeds y podcasts en directorios públicos y suscribirse desde la lista de resultados
(rss_buscador.py). La lista se va llenando a medida que responde cada directorio, sin mover el
foco; Intro lee la descripción completa y "Suscribirse" añade el elegido a la carpeta indicada."""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.rss.rss_buscador import NOMBRES_FUENTE
from app.services.rss.rss_service import rss_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, habilitar_escape_cierre,
                                  mostrar_info)

_TIPOS = (("todo", _("Todo (podcasts, feeds y comunidades)")), ("podcasts", _("Solo podcasts")),
          ("feeds", _("Solo feeds de noticias y comunidades")))
_TIPO_LEGIBLE = {"podcast": _("Podcast"), "feed": _("Feed"), "comunidad": _("Comunidad")}


class BuscarFeedsDialog(wx.Dialog):
    def __init__(self, parent, account_id, consulta=""):
        super().__init__(parent, title=_("Buscar feeds y podcasts"), size=(860, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Buscar feeds y podcasts"))
        self.account_id = account_id
        self.resultados = []
        self.suscritos = 0
        self._busqueda = 0
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Buscar:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt = AccessibleTextCtrl(self, value=consulta, style=wx.TE_PROCESS_ENTER,
                                      name=_("Qué buscar"),
                                      ayuda_texto=_("Tema, nombre del podcast o de la web. Intro busca."))
        self.txt.Bind(wx.EVT_TEXT_ENTER, lambda e: self._buscar())
        fila.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)
        self.choice_tipo = wx.Choice(self, choices=[t for _c, t in _TIPOS], name=_("Dónde buscar"))
        self.choice_tipo.SetSelection(0)
        fila.Add(self.choice_tipo, 0, wx.ALL, 5)
        btn = wx.Button(self, label=_("B&uscar"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self._buscar())
        fila.Add(btn, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)
        self.lbl = wx.StaticText(self, label="")
        s.Add(self.lbl, 0, wx.LEFT, 8)

        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Resultados"))
        for i, (col, ancho) in enumerate(((_("Título"), 260), (_("Tipo"), 90), (_("Directorio"), 110),
                                          (_("Suscriptores"), 90), (_("Descripción"), 300))):
            self.lista.InsertColumn(i, col, width=ancho)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._ver_detalle())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        fila2.Add(wx.StaticText(self, label=_("&Carpeta:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self._cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name",
                              (account_id,))
        self.choice_cat = wx.Choice(self, choices=[_("Sin carpeta")] + [n for _i, n in self._cats],
                                    name=_("Carpeta donde suscribirse"))
        self.choice_cat.SetSelection(0)
        fila2.Add(self.choice_cat, 0, wx.ALL, 5)
        self.btn_sub = wx.Button(self, label=_("&Suscribirse"), name=_("Suscribirse al resultado elegido"))
        self.btn_sub.Bind(wx.EVT_BUTTON, lambda e: self._suscribirse())
        fila2.Add(self.btn_sub, 0, wx.ALL, 5)
        fila2.AddStretchSpacer()
        fila2.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fila2, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self.CenterOnParent()
        self.txt.SetFocus()
        if consulta:
            wx.CallAfter(self._buscar)

    # ------------------------------------------------------------------ búsqueda
    def _buscar(self):
        q = self.txt.GetValue().strip()
        if not q:
            speak(_("Escribe qué quieres buscar."), interrupt=True)
            return
        self._busqueda += 1
        numero = self._busqueda
        self.resultados = []
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        tipo = _TIPOS[self.choice_tipo.GetSelection()][0]
        self.lbl.SetLabel(_("Buscando..."))
        speak(_("Buscando en los directorios..."), interrupt=True)

        def _al_llegar(lote):
            wx.CallAfter(self._anadir, lote, numero)

        def _fin(res, err):
            if not self or numero != self._busqueda:
                return
            n = len(self.resultados)
            fallos = ", ".join(NOMBRES_FUENTE.get(f, f) for f in ((res or {}).get("errores") or {}))
            texto = (_("{0} resultados.").format(n) if n else _("Sin resultados."))
            if fallos:
                texto += " " + _("No respondieron: {0}.").format(fallos)
            self.lbl.SetLabel(texto)
            notificar(texto)
            if n and self.FindFocus() is self.txt:
                self.lista.Focus(0)
                self.lista.Select(0)
                self.lista.SetFocus()
        rss_service.buscar_feeds_async(q, tipo, al_llegar=_al_llegar, callback=_fin)

    def _anadir(self, lote, numero):
        """Añade un lote de resultados al final, sin mover el foco (se leen al llegar a ellos)."""
        if not self or numero != self._busqueda:
            return
        for r in lote:
            i = self.lista.GetItemCount()
            self.resultados.append(r)
            tipo = _TIPO_LEGIBLE.get(r["tipo"], r["tipo"])
            fuente = NOMBRES_FUENTE.get(r["fuente"], r["fuente"])
            subs = str(r["suscriptores"]) if r["suscriptores"] else ""
            self.lista.InsertItem(i, r["titulo"] or r["url"])
            self.lista.SetItem(i, 1, tipo)
            self.lista.SetItem(i, 2, fuente)
            self.lista.SetItem(i, 3, subs)
            self.lista.SetItem(i, 4, r["descripcion"])
            partes = [r["titulo"] or r["url"], tipo]
            if r.get("autor"):
                partes.append(r["autor"])
            if subs:
                partes.append(_("{0} suscriptores").format(subs))
            partes.append(fuente)
            if r["descripcion"]:
                partes.append(r["descripcion"])
            self.lista.establecer_lectura(i, ". ".join(partes))
        self.lbl.SetLabel(_("{0} resultados, buscando más...").format(len(self.resultados)))

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.resultados[i] if 0 <= i < len(self.resultados) else None

    def _ver_detalle(self):
        r = self._actual()
        if r:
            lineas = [r["titulo"], _("Tipo: {0}").format(_TIPO_LEGIBLE.get(r["tipo"], r["tipo"])),
                      _("Directorio: {0}").format(NOMBRES_FUENTE.get(r["fuente"], r["fuente"]))]
            if r.get("autor"):
                lineas.append(_("Autor: {0}").format(r["autor"]))
            lineas += [_("Dirección: {0}").format(r["url"]), "", r["descripcion"] or ""]
            mostrar_info(self, "\n".join(lineas), _("Resultado"))

    def _suscribirse(self):
        r = self._actual()
        if not r:
            speak(_("Elige primero un resultado."), interrupt=True)
            return
        sel = self.choice_cat.GetSelection()
        cat = None if sel <= 0 else self._cats[sel - 1][0]
        speak(_("Suscribiendo a {0}...").format(r["titulo"]), interrupt=True)
        indice = self.lista.GetFocusedItem()

        def _bg(ctx):
            return rss_service.añadir_feed(account_id=self.account_id, url=r["url"],
                                           category_id=cat, ctx=ctx)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo suscribir: {0}").format(err))
                return
            self.suscritos += 1
            notificar(_("Suscrito: {0}").format(res))
            if 0 <= indice < self.lista.GetItemCount():
                self.lista.SetItem(indice, 0, _("{0} (suscrito)").format(r["titulo"]))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
