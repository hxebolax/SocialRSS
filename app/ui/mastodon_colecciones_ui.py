# -*- coding: utf-8 -*-
"""
Colecciones de Mastodon (4.6): listas públicas de cuentas recomendadas.

- ColeccionesDialog: tus colecciones (crear, editar, borrar, ver sus cuentas), las de otra cuenta
  (solo lectura) o las de otras personas en las que apareces tú (con "Retirarme").
- ColeccionCuentasDialog: las cuentas de una colección, con su estado (aceptada, pendiente...);
  en las tuyas se pueden añadir y quitar cuentas.
- ColeccionFormDialog: nombre, descripción, idioma, etiqueta, sensible y visible.
"""

import wx

from app.core.speech import speak, notificar
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.mastodon.mastodon_colecciones import MAX_NOMBRE, MAX_DESCRIPCION
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                  confirmar, habilitar_escape_cierre, mostrar_info)


def _estado_item(estado: str) -> str:
    return {
        "accepted": _("aceptada"),
        "pending": _("pendiente de que la acepte"),
        "rejected": _("rechazada"),
        "revoked": _("se retiró"),
    }.get(estado or "", estado or "")


class ColeccionFormDialog(wx.Dialog):
    """Crear o editar una colección."""

    def __init__(self, parent, datos=None):
        datos = datos or {}
        titulo = _("Editar colección") if datos.get("id") else _("Nueva colección")
        super().__init__(parent, title=titulo, size=(480, 460), style=wx.DEFAULT_DIALOG_STYLE,
                         name=titulo)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Nombre (máximo {0} caracteres):").format(MAX_NOMBRE)), 0, wx.ALL, 5)
        self.txt_nombre = AccessibleTextCtrl(self, value=datos.get("name", ""), name=_("Nombre de la colección"))
        self.txt_nombre.SetMaxLength(MAX_NOMBRE)
        s.Add(self.txt_nombre, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(wx.StaticText(self, label=_("Descripción (máximo {0} caracteres):").format(MAX_DESCRIPCION)), 0, wx.ALL, 5)
        self.txt_desc = AccessibleTextCtrl(self, value=datos.get("description", ""), style=wx.TE_MULTILINE,
                                           size=(-1, 60), name=_("Descripción de la colección"))
        self.txt_desc.SetMaxLength(MAX_DESCRIPCION)
        s.Add(self.txt_desc, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(wx.StaticText(self, label=_("Idioma (código, p. ej. es):")), 0, wx.ALL, 5)
        self.txt_idioma = AccessibleTextCtrl(self, value=datos.get("language", "") or "es",
                                             name=_("Idioma de la colección"))
        s.Add(self.txt_idioma, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(wx.StaticText(self, label=_("Etiqueta que la describe (opcional, sin #):")), 0, wx.ALL, 5)
        self.txt_tag = AccessibleTextCtrl(self, value=datos.get("tag", ""), name=_("Etiqueta de la colección"))
        s.Add(self.txt_tag, 0, wx.EXPAND | wx.ALL, 5)
        self.cb_visible = wx.CheckBox(self, label=_("Mostrarla en mi perfil, en las búsquedas y en las recomendaciones"),
                                      name=_("Colección visible"))
        self.cb_visible.SetValue(bool(datos.get("discoverable", True)))
        s.Add(self.cb_visible, 0, wx.ALL, 5)
        self.cb_sensible = wx.CheckBox(self, label=_("Marcarla como contenido sensible"), name=_("Colección sensible"))
        self.cb_sensible.SetValue(bool(datos.get("sensitive", False)))
        s.Add(self.cb_sensible, 0, wx.ALL, 5)
        if not datos.get("id"):
            s.Add(wx.StaticText(self, label=_("Cuentas que incluir desde el principio (opcional, @usuario separados por espacios):")),
                  0, wx.ALL, 5)
            self.txt_cuentas = AccessibleTextCtrl(self, name=_("Cuentas iniciales"))
            s.Add(self.txt_cuentas, 0, wx.EXPAND | wx.ALL, 5)
        else:
            self.txt_cuentas = None
        btns = wx.StdDialogButtonSizer()
        ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        ok.SetDefault()
        btns.AddButton(ok)
        btns.AddButton(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")))
        btns.Realize()
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self.CenterOnParent()
        self.txt_nombre.SetFocus()

    def get_datos(self):
        return {
            "name": self.txt_nombre.GetValue().strip(),
            "description": self.txt_desc.GetValue().strip(),
            "language": self.txt_idioma.GetValue().strip().lower(),
            "tag_name": self.txt_tag.GetValue().strip().lstrip("#"),
            "discoverable": self.cb_visible.GetValue(),
            "sensitive": self.cb_sensible.GetValue(),
        }

    def get_cuentas(self):
        if not self.txt_cuentas:
            return []
        return [c.strip().lstrip("@") for c in self.txt_cuentas.GetValue().replace(",", " ").split() if c.strip()]


class ColeccionCuentasDialog(wx.Dialog):
    """Cuentas de una colección. En las tuyas: añadir y quitar cuentas."""

    def __init__(self, parent, account_id, coleccion_id, frame=None):
        super().__init__(parent, title=_("Cuentas de la colección"), size=(620, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Cuentas de la colección"))
        self.account_id = account_id
        self.coleccion_id = coleccion_id
        self.frame = frame
        self.cuentas = []
        self.es_mia = False
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Cargando..."))
        s.Add(self.lbl, 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Cuentas de la colección"))
        self.lista.InsertColumn(0, _("Cuenta"), width=320)
        self.lista.InsertColumn(1, _("Estado"), width=220)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._on_perfil(None))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_perfil = wx.Button(self, label=_("Ver &perfil"))
        self.btn_perfil.Bind(wx.EVT_BUTTON, self._on_perfil)
        btns.Add(self.btn_perfil, 0, wx.ALL, 5)
        self.btn_anadir = wx.Button(self, label=_("&Añadir cuenta..."))
        self.btn_anadir.Bind(wx.EVT_BUTTON, self._on_anadir)
        btns.Add(self.btn_anadir, 0, wx.ALL, 5)
        self.btn_quitar = wx.Button(self, label=_("&Quitar de la colección"))
        self.btn_quitar.Bind(wx.EVT_BUTTON, self._on_quitar)
        btns.Add(self.btn_quitar, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        habilitar_escape_cierre(self, wx.ID_CLOSE)
        self.btn_anadir.Disable()
        self.btn_quitar.Disable()
        self.CenterOnParent()
        self._cargar()

    def _cargar(self, foco=0):
        def _fin(res, err):
            if not self:
                return
            if err or not res:
                notificar(_("No se pudo cargar la colección: {0}").format(err or ""))
                return
            col = res["coleccion"]
            self.es_mia = bool(res.get("es_mia"))
            self.cuentas = res.get("cuentas", [])
            self.SetTitle(_("Colección «{0}»").format(col.get("name", "")))
            desc = col.get("description") or ""
            self.lbl.SetLabel(_("«{0}»: {1} cuentas. {2}").format(col.get("name", ""), len(self.cuentas), desc))
            self.lista.DeleteAllItems()
            for i, c in enumerate(self.cuentas):
                nombre = c.get("nombre") or c.get("acct")
                self.lista.InsertItem(i, f"{nombre} (@{c.get('acct', '')})")
                self.lista.SetItem(i, 1, _estado_item(c.get("estado")))
            self.btn_anadir.Enable(self.es_mia)
            self.btn_quitar.Enable(self.es_mia and bool(self.cuentas))
            if self.cuentas:
                idx = min(max(foco, 0), len(self.cuentas) - 1)
                self.lista.Select(idx)
                self.lista.Focus(idx)
                self.lista.SetFocus()
            speak(_("{0} cuentas en la colección.").format(len(self.cuentas)), interrupt=True)
        mastodon_service.ver_coleccion_async(self.account_id, self.coleccion_id, callback=_fin)

    def _seleccionada(self):
        idx = self.lista.GetFirstSelected()
        return (idx, self.cuentas[idx]) if 0 <= idx < len(self.cuentas) else (idx, None)

    def _on_perfil(self, event):
        _idx, c = self._seleccionada()
        if c and c.get("acct") and self.frame is not None and hasattr(self.frame, "_mostrar_detalles_de_cuenta"):
            self.frame._mostrar_detalles_de_cuenta(c["acct"])

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Cuenta que añadir (@usuario o @usuario@servidor):"),
                                    _("Añadir a la colección"))
        acct = dlg.GetValue().strip().lstrip("@") if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not acct:
            return
        speak(_("Añadiendo..."), interrupt=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo añadir: {0}").format(err))
                return
            if (res or {}).get("estado") == "pending":
                notificar(_("@{0} añadida. Queda pendiente de que acepte aparecer en la colección.").format(acct))
            else:
                notificar(_("@{0} añadida a la colección.").format(acct))
            if self:
                self._cargar(foco=len(self.cuentas))
        mastodon_service.anadir_a_coleccion_async(self.account_id, self.coleccion_id, acct, callback=_fin)

    def _on_quitar(self, event):
        idx, c = self._seleccionada()
        if not c:
            return
        if confirmar(self, _("¿Quitar a @{0} de la colección?").format(c.get("acct", "")),
                     _("Quitar de la colección")) != wx.ID_YES:
            return

        def _fin(res, err):
            notificar(_("No se pudo quitar: {0}").format(err) if err else _("Cuenta quitada de la colección."))
            if not err and self:
                self._cargar(foco=idx)
        mastodon_service.quitar_de_coleccion_async(self.account_id, self.coleccion_id, c["item_id"], callback=_fin)


class ColeccionesDialog(wx.Dialog):
    """Lista de colecciones. modo: "mias", "de" (las de la cuenta `acct`) o "donde_estoy"."""

    def __init__(self, parent, account_id, modo="mias", acct=None, frame=None):
        titulos = {"mias": _("Mis colecciones"),
                   "de": _("Colecciones de @{0}").format((acct or "").lstrip("@")),
                   "donde_estoy": _("Colecciones en las que apareces")}
        super().__init__(parent, title=titulos.get(modo, titulos["mias"]), size=(640, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulos.get(modo))
        self.account_id = account_id
        self.modo = modo
        self.acct = acct
        self.frame = frame or parent
        self.colecciones = []
        s = wx.BoxSizer(wx.VERTICAL)
        ayuda = {"mias": _("Tus colecciones: listas públicas de cuentas que recomiendas. Intro muestra sus cuentas."),
                 "de": _("Colecciones que ha publicado esta cuenta. Intro muestra sus cuentas."),
                 "donde_estoy": _("Colecciones de otras personas en las que apareces. Puedes retirarte de cualquiera.")}
        s.Add(wx.StaticText(self, label=ayuda.get(modo, "")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de colecciones"))
        self.lista.InsertColumn(0, _("Nombre"), width=230)
        self.lista.InsertColumn(1, _("Cuentas"), width=80)
        self.lista.InsertColumn(2, _("Descripción"), width=300)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._on_ver(None))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_ver = wx.Button(self, label=_("&Ver cuentas"))
        b_ver.Bind(wx.EVT_BUTTON, self._on_ver)
        btns.Add(b_ver, 0, wx.ALL, 5)
        if modo == "mias":
            for etiqueta, manejador in ((_("&Nueva..."), self._on_nueva), (_("&Editar..."), self._on_editar),
                                        (_("&Borrar"), self._on_borrar)):
                b = wx.Button(self, label=etiqueta)
                b.Bind(wx.EVT_BUTTON, manejador)
                btns.Add(b, 0, wx.ALL, 5)
        elif modo == "donde_estoy":
            b = wx.Button(self, label=_("&Retirarme de esta colección"))
            b.Bind(wx.EVT_BUTTON, self._on_retirarme)
            btns.Add(b, 0, wx.ALL, 5)
        b_url = wx.Button(self, label=_("Abrir en el &navegador"))
        b_url.Bind(wx.EVT_BUTTON, self._on_navegador)
        btns.Add(b_url, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        habilitar_escape_cierre(self, wx.ID_CLOSE)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self, foco=0):
        speak(_("Cargando colecciones..."), interrupt=True)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las colecciones: {0}").format(err))
                return
            self.colecciones = res or []
            self.lista.DeleteAllItems()
            for i, c in enumerate(self.colecciones):
                nombre = c.get("name", "")
                if self.modo == "mias" and not c.get("discoverable"):
                    nombre += " " + _("(oculta)")
                if self.modo == "donde_estoy" and c.get("mi_item"):
                    nombre += " — " + _estado_item(c["mi_item"].get("state"))
                self.lista.InsertItem(i, nombre)
                self.lista.SetItem(i, 1, str(c.get("item_count", 0)))
                self.lista.SetItem(i, 2, c.get("description", ""))
            if self.colecciones:
                idx = min(max(foco, 0), len(self.colecciones) - 1)
                self.lista.Select(idx)
                self.lista.Focus(idx)
                self.lista.SetFocus()
                speak(_("{0} colecciones.").format(len(self.colecciones)), interrupt=True)
            else:
                vacio = {"mias": _("Todavía no tienes colecciones. Pulsa Nueva para crear una."),
                         "de": _("Esta cuenta no tiene colecciones públicas."),
                         "donde_estoy": _("No apareces en ninguna colección.")}
                speak(vacio.get(self.modo, ""), interrupt=True)

        if self.modo == "donde_estoy":
            mastodon_service.colecciones_donde_estoy_async(self.account_id, callback=_fin)
        else:
            mastodon_service.colecciones_de_cuenta_async(
                self.account_id, acct=self.acct if self.modo == "de" else None, callback=_fin)

    def _seleccionada(self):
        idx = self.lista.GetFirstSelected()
        return (idx, self.colecciones[idx]) if 0 <= idx < len(self.colecciones) else (idx, None)

    def _on_ver(self, event):
        _idx, c = self._seleccionada()
        if not c:
            return
        dlg = ColeccionCuentasDialog(self, self.account_id, c["id"], frame=self.frame)
        dlg.ShowModal()
        dlg.Destroy()
        if self.modo == "mias":
            self._cargar(foco=_idx)

    def _on_navegador(self, event):
        _idx, c = self._seleccionada()
        if c and c.get("url"):
            from app.core.privacy import abrir_url
            abrir_url(c["url"])

    def _on_nueva(self, event):
        dlg = ColeccionFormDialog(self)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        datos, cuentas = dlg.get_datos(), dlg.get_cuentas()
        dlg.Destroy()
        speak(_("Creando la colección..."), interrupt=True)

        def _fin(res, err):
            if err:
                mostrar_info(self, _("No se pudo crear la colección: {0}").format(err), _("Colecciones"))
                return
            notificar(_("Colección «{0}» creada.").format(datos["name"]))
            if self:
                self._cargar(foco=len(self.colecciones))
        mastodon_service.crear_coleccion_async(self.account_id, datos, accts=cuentas, callback=_fin)

    def _on_editar(self, event):
        idx, c = self._seleccionada()
        if not c:
            return
        dlg = ColeccionFormDialog(self, c)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        datos = dlg.get_datos()
        dlg.Destroy()

        def _fin(res, err):
            if err:
                mostrar_info(self, _("No se pudo guardar: {0}").format(err), _("Colecciones"))
                return
            notificar(_("Colección actualizada."))
            if self:
                self._cargar(foco=idx)
        mastodon_service.editar_coleccion_async(self.account_id, c["id"], datos, callback=_fin)

    def _on_borrar(self, event):
        idx, c = self._seleccionada()
        if not c:
            return
        if confirmar(self, _("¿Borrar la colección «{0}»? No se puede deshacer.").format(c.get("name", "")),
                     _("Borrar colección")) != wx.ID_YES:
            return

        def _fin(res, err):
            notificar(_("No se pudo borrar: {0}").format(err) if err else _("Colección borrada."))
            if not err and self:
                self._cargar(foco=idx)
        mastodon_service.borrar_coleccion_async(self.account_id, c["id"], callback=_fin)

    def _on_retirarme(self, event):
        idx, c = self._seleccionada()
        if not c:
            return
        item = c.get("mi_item")
        if not item:
            mostrar_info(self, _("No se encontró tu entrada en esta colección."), _("Colecciones"))
            return
        if confirmar(self, _("¿Retirarte de la colección «{0}»? Dejarás de aparecer en ella.").format(
                c.get("name", "")), _("Retirarme")) != wx.ID_YES:
            return

        def _fin(res, err):
            notificar(_("No se pudo: {0}").format(err) if err else _("Te has retirado de la colección."))
            if not err and self:
                self._cargar(foco=idx)
        mastodon_service.retirarme_de_coleccion_async(self.account_id, c["id"], item["id"], callback=_fin)


def elegir_mi_coleccion_y_anadir(parent, account_id, acct):
    """Menú contextual "Añadir a una de mis colecciones": elige colección y añade la cuenta."""
    speak(_("Cargando tus colecciones..."), interrupt=True)

    def _fin(res, err):
        if err:
            notificar(_("No se pudieron cargar tus colecciones: {0}").format(err))
            return
        if not res:
            mostrar_info(parent, _("Todavía no tienes colecciones. Créalas en Mastodon, Colecciones, Mis colecciones."),
                         _("Colecciones"))
            return
        nombres = [c.get("name", "") for c in res]
        sel = wx.GetSingleChoice(_("¿A qué colección añadir a @{0}?").format(acct), _("Añadir a una colección"),
                                 nombres, parent)
        if not sel:
            return
        col = res[nombres.index(sel)]

        def _fin2(r2, e2):
            if e2:
                notificar(_("No se pudo añadir: {0}").format(e2))
            elif (r2 or {}).get("estado") == "pending":
                notificar(_("@{0} añadida a «{1}». Queda pendiente de que acepte.").format(acct, col["name"]))
            else:
                notificar(_("@{0} añadida a «{1}».").format(acct, col["name"]))
        mastodon_service.anadir_a_coleccion_async(account_id, col["id"], acct, callback=_fin2)

    mastodon_service.colecciones_de_cuenta_async(account_id, callback=_fin)
