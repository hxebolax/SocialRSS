# -*- coding: utf-8 -*-
"""Administración accesible de Mastodon sobre la API pública.

La web de Mastodon mezcla endpoints públicos con formularios internos de Rails. Estos diálogos
reproducen la superficie pública: listas persistentes, filtros, paginación, detalle y acciones.
"""
import webbrowser

import wx

from app.core.speech import speak, notificar
from app.services.mastodon import mastodon_service
from .accessible_controls import (AccessibleCheckListBox, AccessibleListCtrl, AccessibleTextCtrl,
                                  CustomTextEntryDialog, confirmar, mostrar_info)


_ESTADOS = {
    "active": lambda: _("Activa"), "pending": lambda: _("Pendiente"),
    "disabled": lambda: _("Desactivada"), "silenced": lambda: _("Limitada"),
    "suspended": lambda: _("Suspendida"), "sensitive": lambda: _("Multimedia sensible"),
    "unconfirmed": lambda: _("Sin confirmar"),
}


def _si(valor):
    return _("sí") if valor else _("no")


def _estado(valor):
    fn = _ESTADOS.get(valor)
    return fn() if fn else (valor or "")


class _AdminBaseDialog(wx.Dialog):
    def _error(self, err, prefijo=None):
        texto = str(err or "")
        bajo = texto.lower()
        if any(x in bajo for x in ("401", "403", "forbidden", "unauthorized", "scope")):
            notificar(_("El servidor rechazó el permiso administrativo. Vuelve a autorizar la cuenta "
                        "con los permisos de moderación y comprueba que tu rol permita esta acción."))
        else:
            notificar((prefijo or _("No se pudo completar la operación")) + ": " + texto)

    def _seleccionar_primero(self, lista):
        if lista.GetItemCount():
            lista.Focus(0)
            lista.Select(0)


class MastodonModerationActionDialog(wx.Dialog):
    """Equivalente accesible al formulario web de acción administrativa."""
    _ACCIONES = [
        ("none", lambda: _("Enviar solo una advertencia")),
        ("disable", lambda: _("Desactivar el acceso de la cuenta local")),
        ("sensitive", lambda: _("Marcar siempre su multimedia como sensible")),
        ("silence", lambda: _("Limitar la cuenta")),
        ("suspend", lambda: _("Suspender la cuenta y eliminar sus publicaciones")),
    ]

    def __init__(self, parent, acct, report_id=None):
        super().__init__(parent, title=_("Acción de moderación sobre @{0}").format(acct),
                         size=(620, 430), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.report_id = report_id
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Acción:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.accion = wx.Choice(self, choices=[f() for _k, f in self._ACCIONES],
                                name=_("Tipo de acción administrativa"))
        self.accion.SetSelection(0)
        s.Add(self.accion, 0, wx.EXPAND | wx.ALL, 8)
        s.Add(wx.StaticText(self, label=_("&Advertencia o explicación para la persona:")),
              0, wx.LEFT | wx.RIGHT, 8)
        self.texto = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE, name=_("Texto de la advertencia"),
            ayuda_texto=_("Este texto se incluye en el aviso de moderación. Puede quedar vacío."))
        s.Add(self.texto, 1, wx.EXPAND | wx.ALL, 8)
        self.email = wx.CheckBox(self, label=_("Enviar también la notificación por &correo"))
        self.email.SetValue(True)
        s.Add(self.email, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        if report_id:
            s.Add(wx.StaticText(
                self, label=_("La acción quedará asociada al informe {0} y lo resolverá.").format(report_id)),
                0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        botones = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()

    def valores(self):
        idx = self.accion.GetSelection()
        return {"accion": self._ACCIONES[max(0, idx)][0],
                "texto": self.texto.GetValue().strip(), "email": self.email.GetValue()}


class MastodonReportClassificationDialog(wx.Dialog):
    def __init__(self, parent, categoria, reglas, seleccionadas):
        super().__init__(parent, title=_("Clasificar informe"), size=(620, 470),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.reglas = reglas
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Categoría:")), 0, wx.ALL, 8)
        self.categoria = wx.Choice(self, choices=[_("Spam"), _("Infracción de las reglas"), _("Otro")],
                                   name=_("Categoría del informe"))
        claves = ["spam", "violation", "other"]
        self.categoria.SetSelection(claves.index(categoria) if categoria in claves else 2)
        s.Add(self.categoria, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        s.Add(wx.StaticText(self, label=_("&Reglas de la instancia infringidas:")), 0, wx.ALL, 8)
        self.lista = AccessibleCheckListBox(
            self, choices=[x["texto"] for x in reglas], size=(-1, 220),
            name=_("Reglas asociadas al informe"))
        elegidas = set(seleccionadas)
        for i, regla in enumerate(reglas):
            self.lista.Check(i, regla["id"] in elegidas)
        s.Add(self.lista, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        s.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()

    def valores(self):
        claves = ["spam", "violation", "other"]
        ids = [r["id"] for i, r in enumerate(self.reglas) if self.lista.IsChecked(i)]
        return claves[self.categoria.GetSelection()], ids


class MastodonReportDetailDialog(_AdminBaseDialog):
    def __init__(self, parent, account_id, report):
        super().__init__(parent, title=_("Informe {0}").format(report["id"]), size=(780, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Detalle del informe de moderación"))
        self.account_id = account_id
        self.report = report
        principal = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self, name=_("Secciones del informe"))
        self._crear_resumen()
        self._crear_contenido()
        principal.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 8)
        acciones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_asignar = wx.Button(self, label=_("&Asignarme"))
        self.btn_asignar.Bind(wx.EVT_BUTTON, lambda e: self._accion_reporte("asignar"))
        acciones.Add(self.btn_asignar, 0, wx.ALL, 4)
        self.btn_desasignar = wx.Button(self, label=_("&Desasignar"))
        self.btn_desasignar.Bind(wx.EVT_BUTTON, lambda e: self._accion_reporte("desasignar"))
        acciones.Add(self.btn_desasignar, 0, wx.ALL, 4)
        etiqueta = _("&Reabrir") if report["resuelto"] else _("&Resolver")
        self.btn_resolver = wx.Button(self, label=etiqueta)
        self.btn_resolver.Bind(wx.EVT_BUTTON,
                               lambda e: self._accion_reporte("reabrir" if self.report["resuelto"] else "resolver"))
        acciones.Add(self.btn_resolver, 0, wx.ALL, 4)
        self.btn_moderar = wx.Button(self, label=_("Aplicar &acción..."))
        self.btn_moderar.Bind(wx.EVT_BUTTON, self._moderar)
        acciones.Add(self.btn_moderar, 0, wx.ALL, 4)
        self.btn_clasificar = wx.Button(self, label=_("&Clasificar..."))
        self.btn_clasificar.Bind(wx.EVT_BUTTON, self._clasificar)
        acciones.Add(self.btn_clasificar, 0, wx.ALL, 4)
        acciones.AddStretchSpacer()
        acciones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        principal.Add(acciones, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(principal)
        self.CenterOnParent()

    def _crear_resumen(self):
        panel = wx.Panel(self.notebook)
        s = wx.BoxSizer(wx.VERTICAL)
        self.resumen = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                           name=_("Resumen completo del informe"))
        self.resumen.SetValue(self._texto_resumen())
        s.Add(self.resumen, 1, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(s)
        self.notebook.AddPage(panel, _("Resumen"))

    def _crear_contenido(self):
        panel = wx.Panel(self.notebook)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("Publicaciones incluidas en el informe:")), 0, wx.ALL, 6)
        self.lista_estados = AccessibleListCtrl(
            panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Publicaciones reportadas"))
        self.lista_estados.InsertColumn(0, _("Fecha"), width=145)
        self.lista_estados.InsertColumn(1, _("Autor"), width=180)
        self.lista_estados.InsertColumn(2, _("Contenido"), width=390)
        for i, st in enumerate(self.report.get("estados") or []):
            contenido = ((st.get("aviso") + ": ") if st.get("aviso") else "") + st.get("contenido", "")
            self.lista_estados.InsertItem(i, st.get("fecha", ""))
            self.lista_estados.SetItem(i, 1, "@" + st.get("autor", ""))
            self.lista_estados.SetItem(i, 2, contenido)
            self.lista_estados.establecer_lectura(
                i, _("{0}, @{1}, {2}, {3} adjuntos, sensible: {4}").format(
                    st.get("fecha", ""), st.get("autor", ""), contenido,
                    st.get("adjuntos", 0), _si(st.get("sensible"))))
        self.lista_estados.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._ver_estado)
        s.Add(self.lista_estados, 1, wx.EXPAND | wx.ALL, 6)
        btn = wx.Button(panel, label=_("&Ver publicación seleccionada"))
        btn.Bind(wx.EVT_BUTTON, self._ver_estado)
        s.Add(btn, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        panel.SetSizer(s)
        self.notebook.AddPage(panel, _("Contenido reportado ({0})").format(len(self.report.get("estados") or [])))

    def _texto_resumen(self):
        r = self.report
        reglas = "; ".join(x.get("texto") or x.get("id") for x in r.get("reglas") or [])
        lineas = [
            _("Estado: {0}").format(_("resuelto") if r["resuelto"] else _("sin resolver")),
            _("Creado: {0}").format(r.get("creado") or "-"),
            _("Actualizado: {0}").format(r.get("actualizado") or "-"),
            _("Reportado por: @{0}").format(r.get("reporta") or "-"),
            _("Cuenta reportada: @{0} ({1})").format(r.get("objetivo") or "-", r.get("objetivo_nombre") or ""),
            _("Categoría: {0}").format(r.get("categoria") or "other"),
            _("Comentario: {0}").format(r.get("comentario") or _("(sin comentario)")),
            _("Reglas: {0}").format(reglas or _("ninguna")),
            _("Asignado a: {0}").format(("@" + r["asignado"]) if r.get("asignado") else _("nadie")),
            _("Resuelto por: {0}").format(("@" + r["resuelto_por"]) if r.get("resuelto_por") else "-"),
            _("Reenviado a la instancia remota: {0}").format(
                _("no consta") if r.get("reenviado") is None else _si(r.get("reenviado"))),
            "",
            _("Perfil reportado:"),
            r.get("objetivo_nota") or _("(sin biografía)"),
            _("Publicaciones: {0}; seguidores: {1}; seguidos: {2}").format(
                r.get("objetivo_publicaciones", 0), r.get("objetivo_seguidores", 0),
                r.get("objetivo_seguidos", 0)),
        ]
        return "\n".join(lineas)

    def _ver_estado(self, event):
        i = self.lista_estados.GetFocusedItem()
        estados = self.report.get("estados") or []
        if not (0 <= i < len(estados)):
            return
        st = estados[i]
        texto = "\n".join(filter(None, [st.get("aviso"), st.get("contenido"), st.get("url")]))
        mostrar_info(self, texto, _("Publicación reportada"))

    def _accion_reporte(self, accion):
        def _fin(_res, err):
            if err:
                self._error(err)
                return
            notificar(_("Informe actualizado."))
            self._recargar()
        mastodon_service.admin_accion_informe_async(
            self.account_id, self.report["id"], accion, callback=_fin)

    def _recargar(self):
        def _fin(res, err):
            if err:
                self._error(err)
                return
            self.report = res
            self.resumen.SetValue(self._texto_resumen())
            self.btn_resolver.SetLabel(_("&Reabrir") if res["resuelto"] else _("&Resolver"))
        mastodon_service.admin_detalle_informe_async(self.account_id, self.report["id"], callback=_fin)

    def _moderar(self, event):
        dlg = MastodonModerationActionDialog(
            self, self.report.get("objetivo") or "", report_id=self.report["id"])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        valores = dlg.valores()
        dlg.Destroy()
        if valores["accion"] == "suspend" and confirmar(
                self, _("Suspender elimina las publicaciones de esta cuenta. ¿Continuar?"),
                _("Confirmar suspensión")) != wx.ID_YES:
            return

        def _fin(_res, err):
            if err:
                self._error(err)
            else:
                notificar(_("Acción de moderación aplicada."))
                self._recargar()
        mastodon_service.admin_moderar_cuenta_async(
            self.account_id, self.report["objetivo_id"], valores["accion"], self.report["id"],
            valores["texto"], valores["email"], callback=_fin)

    def _clasificar(self, event):
        actuales = [x["id"] for x in self.report.get("reglas") or [] if x.get("id")]

        def _reglas_fin(reglas, err):
            if err:
                self._error(err)
                return
            dlg = MastodonReportClassificationDialog(
                self, self.report.get("categoria") or "other", reglas or [], actuales)
            if dlg.ShowModal() != wx.ID_OK:
                dlg.Destroy()
                return
            categoria, rule_ids = dlg.valores()
            dlg.Destroy()

            def _fin(res, error):
                if error:
                    self._error(error)
                else:
                    self.report = res
                    self.resumen.SetValue(self._texto_resumen())
                    notificar(_("Clasificación actualizada."))
            mastodon_service.admin_actualizar_informe_async(
                self.account_id, self.report["id"], categoria, rule_ids, callback=_fin)
        mastodon_service.admin_listar_reglas_instancia_async(self.account_id, callback=_reglas_fin)


class MastodonAdminReportsDialog(_AdminBaseDialog):
    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración de Mastodon: informes"), size=(930, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Informes de moderación"))
        self.account_id = account_id
        self.items = []
        self.next_max_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        filtros = wx.BoxSizer(wx.HORIZONTAL)
        filtros.Add(wx.StaticText(self, label=_("&Estado:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.estado = wx.Choice(self, choices=[_("Sin resolver"), _("Resueltos"), _("Todos (Mastodon 4.6 o posterior)")],
                                name=_("Estado del informe"))
        self.estado.SetSelection(0)
        filtros.Add(self.estado, 0, wx.ALL, 5)
        filtros.Add(wx.StaticText(self, label=_("ID de quien &reporta:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.reporter = AccessibleTextCtrl(self, name=_("ID de quien reporta"))
        filtros.Add(self.reporter, 1, wx.ALL, 5)
        filtros.Add(wx.StaticText(self, label=_("ID de la cuenta reportada:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.target = AccessibleTextCtrl(self, name=_("ID de la cuenta reportada"))
        filtros.Add(self.target, 1, wx.ALL, 5)
        btn = wx.Button(self, label=_("&Buscar"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self._cargar(False))
        filtros.Add(btn, 0, wx.ALL, 5)
        s.Add(filtros, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.lbl = wx.StaticText(self, label=_("Informes (Intro para abrir, Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de informes de moderación"))
        for i, (titulo, ancho) in enumerate((("ID", 90), (_("Cuenta reportada"), 190),
                                             (_("Reportado por"), 170), (_("Categoría"), 105),
                                             (_("Comentario"), 270), (_("Fecha"), 145))):
            self.lista.InsertColumn(i, titulo, width=ancho)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._detalle)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, handler in ((_("&Detalles"), self._detalle), (_("Cargar &anteriores"), self._mas),
                                  (_("&Actualizar"), lambda e: self._cargar(False))):
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, handler)
            botones.Add(b, 0, wx.ALL, 4)
        botones.AddStretchSpacer()
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar(False)

    def _cargar(self, mas=False):
        speak(_("Cargando informes..."), interrupt=True)
        cursor = self.next_max_id if mas else None

        def _fin(res, err):
            if err:
                self._error(err, _("No se pudieron cargar los informes"))
                return
            pagina = res or {}
            nuevos = pagina.get("items") or []
            if mas:
                vistos = {x["id"] for x in self.items}
                self.items.extend(x for x in nuevos if x["id"] not in vistos)
            else:
                self.items = nuevos
            self.next_max_id = pagina.get("next_max_id")
            self._pintar()
            notificar(_("{0} informes cargados.").format(len(self.items)))
        mastodon_service.admin_buscar_informes_async(
            self.account_id, resolved={0: False, 1: True}.get(self.estado.GetSelection()),
            reporter_id=self.reporter.GetValue().strip() or None,
            target_id=self.target.GetValue().strip() or None, max_id=cursor, callback=_fin)

    def _pintar(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, r in enumerate(self.items):
            vals = (r["id"], "@" + r["objetivo"], "@" + r["reporta"], r["categoria"],
                    r["comentario"] or _("(sin comentario)"), r["creado"])
            self.lista.InsertItem(i, vals[0])
            for col, val in enumerate(vals[1:], 1):
                self.lista.SetItem(i, col, val)
            asignado = ("@" + r["asignado"]) if r.get("asignado") else _("sin asignar")
            self.lista.establecer_lectura(i, ", ".join(vals) + ", " + asignado)
        self.lbl.SetLabel(_("{0} informes (Intro para abrir, Aplicaciones para acciones):").format(len(self.items)))
        self._seleccionar_primero(self.lista)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.items[i] if 0 <= i < len(self.items) else None

    def _detalle(self, event):
        r = self._actual()
        if not r:
            return
        speak(_("Cargando detalle del informe..."), interrupt=True)

        def _fin(res, err):
            if err:
                self._error(err)
                return
            dlg = MastodonReportDetailDialog(self, self.account_id, res)
            dlg.ShowModal()
            dlg.Destroy()
            self._cargar(False)
        mastodon_service.admin_detalle_informe_async(self.account_id, r["id"], callback=_fin)

    def _mas(self, event):
        if not self.next_max_id:
            notificar(_("No hay más informes."))
            return
        self._cargar(True)

    def _menu(self, event):
        r = self._actual()
        if not r:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Ver detalle"))
        self.Bind(wx.EVT_MENU, self._detalle, it)
        it = menu.Append(wx.ID_ANY, _("Reabrir") if r["resuelto"] else _("Resolver"))
        self.Bind(wx.EVT_MENU, lambda e: self._accion(r, "reabrir" if r["resuelto"] else "resolver"), it)
        it = menu.Append(wx.ID_ANY, _("Desasignar") if r.get("asignado") else _("Asignarme"))
        self.Bind(wx.EVT_MENU, lambda e: self._accion(r, "desasignar" if r.get("asignado") else "asignar"), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _accion(self, r, accion):
        mastodon_service.admin_accion_informe_async(
            self.account_id, r["id"], accion,
            callback=lambda _x, e: self._error(e) if e else self._cargar(False))


class MastodonAccountDetailDialog(_AdminBaseDialog):
    def __init__(self, parent, account_id, cuenta):
        super().__init__(parent, title=_("Cuenta @{0}").format(cuenta["acct"]), size=(760, 590),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.cuenta = cuenta
        s = wx.BoxSizer(wx.VERTICAL)
        nb = wx.Notebook(self, name=_("Secciones de la cuenta administrativa"))
        p1 = wx.Panel(nb)
        s1 = wx.BoxSizer(wx.VERTICAL)
        self.txt = AccessibleTextCtrl(p1, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                      name=_("Detalles administrativos de la cuenta"))
        self.txt.SetValue(self._resumen())
        s1.Add(self.txt, 1, wx.EXPAND | wx.ALL, 8)
        p1.SetSizer(s1)
        nb.AddPage(p1, _("Resumen"))
        p2 = wx.Panel(nb)
        s2 = wx.BoxSizer(wx.VERTICAL)
        self.ips = AccessibleListCtrl(p2, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                      name=_("Direcciones IP conocidas"))
        self.ips.InsertColumn(0, _("IP"), width=260)
        self.ips.InsertColumn(1, _("Último uso"), width=220)
        for i, ip in enumerate(cuenta.get("ips") or []):
            self.ips.InsertItem(i, ip["ip"])
            self.ips.SetItem(i, 1, ip["usada"])
            self.ips.establecer_lectura(i, _("IP {0}, último uso {1}").format(ip["ip"], ip["usada"]))
        s2.Add(self.ips, 1, wx.EXPAND | wx.ALL, 8)
        p2.SetSizer(s2)
        nb.AddPage(p2, _("Direcciones IP ({0})").format(len(cuenta.get("ips") or [])))
        s.Add(nb, 1, wx.EXPAND | wx.ALL, 8)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, label=_("Aplicar &acción..."))
        b.Bind(wx.EVT_BUTTON, self._accion)
        botones.Add(b, 0, wx.ALL, 4)
        if cuenta.get("url"):
            b = wx.Button(self, label=_("Abrir &perfil"))
            b.Bind(wx.EVT_BUTTON, lambda e: webbrowser.open(cuenta["url"]))
            botones.Add(b, 0, wx.ALL, 4)
        botones.AddStretchSpacer()
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(s)
        self.CenterOnParent()

    def _resumen(self):
        c = self.cuenta
        campos = "\n".join("  {0}: {1}".format(x["nombre"], x["valor"]) for x in c.get("campos") or [])
        return "\n".join([
            _("Cuenta: @{0}").format(c["acct"]), _("Nombre: {0}").format(c.get("nombre") or "-"),
            _("Estado: {0}").format(_estado(c["estado"])), _("Origen: {0}").format(_("local") if c["local"] else _("remoto")),
            _("Correo: {0}").format(c.get("email") or "-"), _("Rol: {0}").format(c.get("rol") or "-"),
            _("Idioma: {0}").format(c.get("locale") or "-"), _("Creada: {0}").format(c.get("creado") or "-"),
            _("Última publicación: {0}").format(c.get("ultima_publicacion") or "-"),
            _("Publicaciones: {0}; seguidores: {1}; seguidos: {2}").format(c["publicaciones"], c["seguidores"], c["seguidos"]),
            _("Confirmada: {0}; aprobada: {1}; multimedia sensible: {2}").format(
                _si(c["confirmado"]), _si(c["aprobado"]), _si(c["sensible"])),
            _("Solicitud de acceso: {0}").format(c.get("solicitud") or "-"), "",
            _("Biografía:"), c.get("nota") or _("(sin biografía)"), "", _("Campos de perfil:"), campos or _("(ninguno)"),
        ])

    def _accion(self, event):
        dlg = MastodonModerationActionDialog(self, self.cuenta["acct"])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        v = dlg.valores()
        dlg.Destroy()
        if v["accion"] == "suspend" and confirmar(
                self, _("Esta acción elimina publicaciones. ¿Continuar?"),
                _("Confirmar suspensión")) != wx.ID_YES:
            return
        mastodon_service.admin_moderar_cuenta_async(
            self.account_id, self.cuenta["id"], v["accion"], texto=v["texto"],
            enviar_email=v["email"], callback=lambda _r, e: self._error(e) if e else notificar(_("Acción aplicada.")))


class MastodonAdminAccountsDialog(_AdminBaseDialog):
    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración de Mastodon: cuentas"), size=(980, 650),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Cuentas de la instancia"))
        self.account_id = account_id
        self.items = []
        self.next_max_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(2, 8, 4, 6)
        grid.AddGrowableCol(3, 1)
        grid.AddGrowableCol(7, 1)
        grid.Add(wx.StaticText(self, label=_("&Origen:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.origen = wx.Choice(self, choices=[_("Todos"), _("Locales"), _("Remotos")])
        self.origen.SetSelection(0)
        grid.Add(self.origen, 0, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("&Usuario:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.usuario = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Buscar por usuario"))
        grid.Add(self.usuario, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("&Dominio:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.dominio = AccessibleTextCtrl(self, name=_("Filtrar por dominio"))
        grid.Add(self.dominio, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("&Estado:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.estado = wx.Choice(self, choices=[_("Todos"), _("Activas"), _("Pendientes"), _("Desactivadas"),
                                               _("Limitadas"), _("Suspendidas")])
        self.estado.SetSelection(0)
        grid.Add(self.estado, 0, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("Nombre &visible:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.nombre = AccessibleTextCtrl(self, name=_("Buscar por nombre visible"))
        grid.Add(self.nombre, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("&Correo:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.email = AccessibleTextCtrl(self, name=_("Filtrar por correo"))
        grid.Add(self.email, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=_("&IP:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.ip = AccessibleTextCtrl(self, name=_("Filtrar por IP"))
        grid.Add(self.ip, 1, wx.EXPAND)
        self.staff = wx.CheckBox(self, label=_("Solo personal de la instancia"))
        grid.Add(self.staff, 0, wx.ALIGN_CENTER_VERTICAL)
        s.Add(grid, 0, wx.EXPAND | wx.ALL, 8)
        b = wx.Button(self, label=_("&Buscar"))
        b.Bind(wx.EVT_BUTTON, lambda e: self._cargar(False))
        s.Add(b, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, 8)
        self.lbl = wx.StaticText(self, label=_("Cuentas (Intro para detalle, Aplicaciones para acciones):"))
        s.Add(self.lbl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista administrativa de cuentas"))
        for i, (t, w) in enumerate((("Cuenta", 220), (_("Nombre"), 180), (_("Correo"), 210),
                                    (_("Rol"), 110), (_("Estado"), 120), (_("Creada"), 145))):
            self.lista.InsertColumn(i, t, width=w)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._detalle)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, fn in ((_("&Detalles"), self._detalle), (_("Cargar &anteriores"), self._mas),
                             (_("&Actualizar"), lambda e: self._cargar(False))):
            bb = wx.Button(self, label=etiqueta)
            bb.Bind(wx.EVT_BUTTON, fn)
            botones.Add(bb, 0, wx.ALL, 4)
        botones.AddStretchSpacer()
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        self.SetSizer(s)
        self.CenterOnParent()
        self.usuario.Bind(wx.EVT_TEXT_ENTER, lambda e: self._cargar(False))
        self._cargar(False)

    def _filtros(self):
        origen = [None, "local", "remote"][self.origen.GetSelection()]
        estado = [None, "active", "pending", "disabled", "silenced", "suspended"][self.estado.GetSelection()]
        return {"origin": origen, "status": estado, "username": self.usuario.GetValue().strip(),
                "display_name": self.nombre.GetValue().strip(), "by_domain": self.dominio.GetValue().strip(),
                "email": self.email.GetValue().strip(), "ip": self.ip.GetValue().strip(),
                "permissions": "staff" if self.staff.GetValue() else None}

    def _cargar(self, mas=False):
        speak(_("Cargando cuentas..."), interrupt=True)
        cursor = self.next_max_id if mas else None

        def _fin(res, err):
            if err:
                self._error(err)
                return
            pagina = res or {}
            nuevos = pagina.get("items") or []
            if mas:
                vistos = {x["id"] for x in self.items}
                self.items.extend(x for x in nuevos if x["id"] not in vistos)
            else:
                self.items = nuevos
            self.next_max_id = pagina.get("next_max_id")
            self._pintar()
            notificar(_("{0} cuentas cargadas.").format(len(self.items)))
        mastodon_service.admin_buscar_cuentas_async(
            self.account_id, self._filtros(), max_id=cursor, callback=_fin)

    def _pintar(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, c in enumerate(self.items):
            vals = ("@" + c["acct"], c["nombre"], c["email"], c["rol"], _estado(c["estado"]), c["creado"])
            self.lista.InsertItem(i, vals[0])
            for col, val in enumerate(vals[1:], 1):
                self.lista.SetItem(i, col, val)
            self.lista.establecer_lectura(i, ", ".join(vals))
        self.lbl.SetLabel(_("{0} cuentas (Intro para detalle, Aplicaciones para acciones):").format(len(self.items)))
        self._seleccionar_primero(self.lista)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.items[i] if 0 <= i < len(self.items) else None

    def _detalle(self, event):
        c = self._actual()
        if not c:
            return

        def _fin(res, err):
            if err:
                self._error(err)
                return
            dlg = MastodonAccountDetailDialog(self, self.account_id, res)
            dlg.ShowModal()
            dlg.Destroy()
            self._cargar(False)
        mastodon_service.admin_detalle_cuenta_async(self.account_id, c["id"], callback=_fin)

    def _mas(self, event):
        if self.next_max_id:
            self._cargar(True)
        else:
            notificar(_("No hay más cuentas."))

    def _menu(self, event):
        c = self._actual()
        if not c:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Ver detalle"))
        self.Bind(wx.EVT_MENU, self._detalle, it)
        menu.AppendSeparator()
        acciones = []
        if c["estado"] == "pending":
            acciones += [(_("Aprobar"), "aprobar"), (_("Rechazar y eliminar"), "rechazar")]
        if c["desactivado"]:
            acciones.append((_("Reactivar acceso"), "reactivar"))
        if c["silenciado"]:
            acciones.append((_("Quitar limitación"), "quitar_silencio"))
        if c["suspendido"]:
            acciones.append((_("Quitar suspensión"), "quitar_suspension"))
        if c["sensible"]:
            acciones.append((_("Dejar de forzar multimedia sensible"), "marcar_no_sensible"))
        for etiqueta, accion in acciones:
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, a=accion: self._accion_simple(c, a), it)
        it = menu.Append(wx.ID_ANY, _("Acción personalizada..."))
        self.Bind(wx.EVT_MENU, lambda e: self._personalizada(c), it)
        if c["local"]:
            it = menu.Append(wx.ID_ANY, _("Eliminar definitivamente la cuenta..."))
            self.Bind(wx.EVT_MENU, lambda e: self._eliminar(c), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _accion_simple(self, cuenta, accion):
        if confirmar(self, _("¿Aplicar esta acción a @{0}?").format(cuenta["acct"]),
                     _("Confirmar acción")) != wx.ID_YES:
            return
        mastodon_service.admin_accion_cuenta_async(
            self.account_id, cuenta["id"], accion,
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _personalizada(self, cuenta):
        dlg = MastodonModerationActionDialog(self, cuenta["acct"])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        v = dlg.valores()
        dlg.Destroy()
        mastodon_service.admin_moderar_cuenta_async(
            self.account_id, cuenta["id"], v["accion"], texto=v["texto"], enviar_email=v["email"],
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _eliminar(self, cuenta):
        if confirmar(self, _("Eliminar definitivamente @{0} no se puede deshacer. ¿Continuar?").format(
                cuenta["acct"]), _("Eliminar cuenta")) != wx.ID_YES:
            return
        mastodon_service.admin_eliminar_cuenta_async(
            self.account_id, cuenta["id"], callback=lambda _r, e: self._error(e) if e else self._cargar(False))


class MastodonDomainBlockEditDialog(wx.Dialog):
    def __init__(self, parent, item=None):
        super().__init__(parent, title=_("Editar bloqueo de dominio") if item else _("Nuevo bloqueo de dominio"),
                         size=(620, 520), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        item = item or {}
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Dominio:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.dominio = AccessibleTextCtrl(self, name=_("Dominio a moderar"))
        self.dominio.SetValue(item.get("dominio", ""))
        self.dominio.Enable(not bool(item))
        s.Add(self.dominio, 0, wx.EXPAND | wx.ALL, 8)
        s.Add(wx.StaticText(self, label=_("&Severidad:")), 0, wx.LEFT | wx.RIGHT, 8)
        self.severidad = wx.Choice(self, choices=[_("Limitar"), _("Suspender"), _("Solo medidas auxiliares")])
        claves = ["silence", "suspend", "limit"]
        self.severidad.SetSelection(claves.index(item.get("severidad")) if item.get("severidad") in claves else 0)
        s.Add(self.severidad, 0, wx.EXPAND | wx.ALL, 8)
        self.media = wx.CheckBox(self, label=_("Rechazar multimedia de este dominio"))
        self.media.SetValue(bool(item.get("rechazar_media")))
        s.Add(self.media, 0, wx.ALL, 6)
        self.informes = wx.CheckBox(self, label=_("Rechazar informes procedentes de este dominio"))
        self.informes.SetValue(bool(item.get("rechazar_informes")))
        s.Add(self.informes, 0, wx.ALL, 6)
        self.ofuscar = wx.CheckBox(self, label=_("Ofuscar el dominio al mostrarlo públicamente"))
        self.ofuscar.SetValue(bool(item.get("ofuscar")))
        s.Add(self.ofuscar, 0, wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Comentario &privado para moderadores:")), 0, wx.LEFT | wx.RIGHT, 8)
        self.privado = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, name=_("Comentario privado"))
        self.privado.SetValue(item.get("comentario_privado", ""))
        s.Add(self.privado, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(wx.StaticText(self, label=_("Comentario p&úblico:")), 0, wx.LEFT | wx.RIGHT, 8)
        self.publico = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, name=_("Comentario público"))
        self.publico.SetValue(item.get("comentario_publico", ""))
        s.Add(self.publico, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()

    def valores(self):
        return {"dominio": self.dominio.GetValue().strip(),
                "severidad": ["silence", "suspend", "limit"][self.severidad.GetSelection()],
                "rechazar_media": self.media.GetValue(), "rechazar_informes": self.informes.GetValue(),
                "ofuscar": self.ofuscar.GetValue(), "comentario_privado": self.privado.GetValue().strip(),
                "comentario_publico": self.publico.GetValue().strip()}


class MastodonAdminDomainBlocksDialog(_AdminBaseDialog):
    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: dominios bloqueados"), size=(900, 570),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.items = []
        self.next_max_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Dominios bloqueados:"))
        s.Add(self.lbl, 0, wx.ALL, 8)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de dominios bloqueados"))
        for i, (t, w) in enumerate((("Dominio", 220), (_("Severidad"), 110), (_("Multimedia"), 95),
                                    (_("Informes"), 90), (_("Comentario público"), 260), (_("Creado"), 135))):
            self.lista.InsertColumn(i, t, width=w)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._editar)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        for et, fn in ((_("&Nuevo..."), self._nuevo), (_("&Editar..."), self._editar),
                       (_("&Eliminar"), self._eliminar), (_("Cargar &anteriores"), self._mas),
                       (_("&Actualizar"), lambda e: self._cargar(False))):
            b = wx.Button(self, label=et)
            b.Bind(wx.EVT_BUTTON, fn)
            bs.Add(b, 0, wx.ALL, 4)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar(False)

    def _cargar(self, mas=False):
        cursor = self.next_max_id if mas else None

        def _fin(res, err):
            if err:
                self._error(err)
                return
            nuevos = (res or {}).get("items") or []
            if mas:
                vistos = {x["id"] for x in self.items}
                self.items.extend(x for x in nuevos if x["id"] not in vistos)
            else:
                self.items = nuevos
            self.next_max_id = (res or {}).get("next_max_id")
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, x in enumerate(self.items):
                vals = (x["dominio"], x["severidad"], _si(x["rechazar_media"]),
                        _si(x["rechazar_informes"]), x["comentario_publico"], x["creado"])
                self.lista.InsertItem(i, vals[0])
                for col, val in enumerate(vals[1:], 1):
                    self.lista.SetItem(i, col, val)
                self.lista.establecer_lectura(i, ", ".join(vals))
            self.lbl.SetLabel(_("{0} dominios bloqueados:").format(len(self.items)))
            self._seleccionar_primero(self.lista)
        mastodon_service.admin_buscar_bloqueos_dominio_async(
            self.account_id, max_id=cursor, callback=_fin)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.items[i] if 0 <= i < len(self.items) else None

    def _nuevo(self, event):
        self._form(None)

    def _editar(self, event):
        x = self._actual()
        if not x:
            return
        mastodon_service.admin_detalle_bloqueo_dominio_async(
            self.account_id, x["id"],
            callback=lambda res, err: self._error(err) if err else self._form(res))

    def _form(self, item):
        dlg = MastodonDomainBlockEditDialog(self, item)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        datos = dlg.valores()
        dlg.Destroy()
        if not datos["dominio"]:
            return
        mastodon_service.admin_guardar_bloqueo_dominio_async(
            self.account_id, datos, item["id"] if item else None,
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _eliminar(self, event):
        x = self._actual()
        if not x or confirmar(self, _("¿Retirar el bloqueo de {0}?").format(x["dominio"]),
                             _("Retirar bloqueo")) != wx.ID_YES:
            return
        mastodon_service.admin_desbloquear_dominio_async(
            self.account_id, x["id"], callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _mas(self, event):
        self._cargar(True) if self.next_max_id else notificar(_("No hay más dominios."))


class MastodonIpBlockEditDialog(wx.Dialog):
    def __init__(self, parent, item=None):
        super().__init__(parent, title=_("Editar bloqueo de IP") if item else _("Nuevo bloqueo de IP"),
                         size=(560, 380), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        item = item or {}
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&IP o rango CIDR:")), 0, wx.ALL, 7)
        self.ip = AccessibleTextCtrl(self, name=_("Dirección IP o rango"))
        self.ip.SetValue(item.get("ip", ""))
        s.Add(self.ip, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 7)
        s.Add(wx.StaticText(self, label=_("&Severidad:")), 0, wx.ALL, 7)
        self.sev = wx.Choice(self, choices=[_("Sin acceso"), _("Altas sujetas a aprobación"),
                                            _("Bloquear nuevas altas")])
        claves = ["no_access", "sign_up_requires_approval", "sign_up_block"]
        self.sev.SetSelection(claves.index(item.get("severidad")) if item.get("severidad") in claves else 0)
        s.Add(self.sev, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 7)
        s.Add(wx.StaticText(self, label=_("&Comentario interno:")), 0, wx.ALL, 7)
        self.com = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, name=_("Comentario del bloqueo"))
        self.com.SetValue(item.get("comentario", ""))
        s.Add(self.com, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 7)
        s.Add(wx.StaticText(self, label=_("Caducar en &segundos (vacío = permanente):")), 0, wx.ALL, 7)
        self.exp = AccessibleTextCtrl(self, name=_("Segundos hasta caducidad"))
        s.Add(self.exp, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 7)
        s.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()

    def valores(self):
        texto = self.exp.GetValue().strip()
        return {"ip": self.ip.GetValue().strip(),
                "severidad": ["no_access", "sign_up_requires_approval", "sign_up_block"][self.sev.GetSelection()],
                "comentario": self.com.GetValue().strip(), "expira_en": int(texto) if texto.isdigit() else None}


class MastodonAdminIpBlocksDialog(_AdminBaseDialog):
    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: bloqueos de IP"), size=(820, 530),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.items = []
        self.next_max_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de bloqueos de IP"))
        for i, (t, w) in enumerate((("IP", 210), (_("Severidad"), 180), (_("Comentario"), 240),
                                    (_("Creado"), 130), (_("Expira"), 130))):
            self.lista.InsertColumn(i, t, width=w)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._editar)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        for et, fn in ((_("&Nuevo..."), self._nuevo), (_("&Editar..."), self._editar),
                       (_("&Eliminar"), self._eliminar), (_("Cargar &anteriores"), self._mas),
                       (_("&Actualizar"), lambda e: self._cargar(False))):
            b = wx.Button(self, label=et)
            b.Bind(wx.EVT_BUTTON, fn)
            bs.Add(b, 0, wx.ALL, 4)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar(False)

    def _cargar(self, mas=False):
        cursor = self.next_max_id if mas else None

        def _fin(res, err):
            if err:
                self._error(err)
                return
            nuevos = (res or {}).get("items") or []
            if mas:
                vistos = {x["id"] for x in self.items}
                self.items.extend(x for x in nuevos if x["id"] not in vistos)
            else:
                self.items = nuevos
            self.next_max_id = (res or {}).get("next_max_id")
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, x in enumerate(self.items):
                vals = (x["ip"], x["severidad"], x["comentario"], x["creado"], x["expira"])
                self.lista.InsertItem(i, vals[0])
                for col, val in enumerate(vals[1:], 1):
                    self.lista.SetItem(i, col, val)
                self.lista.establecer_lectura(i, ", ".join(vals))
            self._seleccionar_primero(self.lista)
        mastodon_service.admin_buscar_ip_blocks_async(self.account_id, max_id=cursor, callback=_fin)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.items[i] if 0 <= i < len(self.items) else None

    def _nuevo(self, event):
        self._form(None)

    def _editar(self, event):
        x = self._actual()
        if not x:
            return
        mastodon_service.admin_detalle_ip_block_async(
            self.account_id, x["id"],
            callback=lambda res, err: self._error(err) if err else self._form(res))

    def _form(self, item):
        dlg = MastodonIpBlockEditDialog(self, item)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        datos = dlg.valores()
        dlg.Destroy()
        if not datos["ip"]:
            return
        mastodon_service.admin_guardar_ip_block_async(
            self.account_id, datos, item["id"] if item else None,
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _eliminar(self, event):
        x = self._actual()
        if not x or confirmar(self, _("¿Eliminar el bloqueo de {0}?").format(x["ip"]),
                             _("Eliminar bloqueo")) != wx.ID_YES:
            return
        mastodon_service.admin_desbloquear_ip_async(
            self.account_id, x["id"], callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _mas(self, event):
        self._cargar(True) if self.next_max_id else notificar(_("No hay más bloqueos."))


class MastodonAdminResourceDialog(_AdminBaseDialog):
    """Lista accesible común para allowlist y bloqueos de correo."""
    _DATOS = {
        "domain_allows": (lambda: _("Dominios permitidos"), lambda: _("Dominio"), lambda: _("Dominio a permitir:")),
        "email_domains": (lambda: _("Dominios de correo bloqueados"), lambda: _("Dominio"), lambda: _("Dominio de correo a bloquear:")),
        "canonical_emails": (lambda: _("Correos canónicos bloqueados"), lambda: _("Hash canónico"), lambda: _("Correo a bloquear:")),
    }

    def __init__(self, parent, account_id, recurso):
        titulo, columna, _pregunta = self._DATOS[recurso]
        super().__init__(parent, title=_("Administración: {0}").format(titulo()), size=(700, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.recurso = recurso
        self.items = []
        self.next_max_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=titulo())
        self.lista.InsertColumn(0, columna(), width=400)
        self.lista.InsertColumn(1, _("Creado"), width=170)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._detalle)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        for et, fn in ((_("&Añadir..."), self._anadir), (_("&Detalles"), self._detalle),
                       (_("&Eliminar"), self._eliminar), (_("Cargar &anteriores"), self._mas),
                       (_("&Actualizar"), lambda e: self._cargar(False))):
            b = wx.Button(self, label=et)
            b.Bind(wx.EVT_BUTTON, fn)
            bs.Add(b, 0, wx.ALL, 4)
        if recurso == "canonical_emails":
            b = wx.Button(self, label=_("&Comprobar correo..."))
            b.Bind(wx.EVT_BUTTON, self._probar_correo)
            bs.Add(b, 0, wx.ALL, 4)
            b = wx.Button(self, label=_("Añadir &hash..."))
            b.Bind(wx.EVT_BUTTON, self._anadir_hash)
            bs.Add(b, 0, wx.ALL, 4)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar(False)

    def _cargar(self, mas=False):
        cursor = self.next_max_id if mas else None

        def _fin(res, err):
            if err:
                self._error(err)
                return
            nuevos = (res or {}).get("items") or []
            if mas:
                vistos = {x["id"] for x in self.items}
                self.items.extend(x for x in nuevos if x["id"] not in vistos)
            else:
                self.items = nuevos
            self.next_max_id = (res or {}).get("next_max_id")
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, x in enumerate(self.items):
                self.lista.InsertItem(i, x["valor"])
                self.lista.SetItem(i, 1, x["creado"])
                self.lista.establecer_lectura(i, x["valor"] + ", " + x["creado"])
            self._seleccionar_primero(self.lista)
        mastodon_service.admin_listar_recurso_async(
            self.account_id, self.recurso, max_id=cursor, callback=_fin)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.items[i] if 0 <= i < len(self.items) else None

    def _anadir(self, event):
        _titulo, _columna, pregunta = self._DATOS[self.recurso]
        dlg = CustomTextEntryDialog(self, pregunta(), _("Añadir regla administrativa"))
        valor = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not valor:
            return
        aprobar = False
        if self.recurso == "email_domains":
            aprobar = confirmar(
                self, _("¿Permitir altas desde este dominio solo después de aprobación manual?\n\n"
                        "Responde No para bloquear completamente las altas."),
                _("Política del dominio de correo")) == wx.ID_YES
        mastodon_service.admin_crear_recurso_async(
            self.account_id, self.recurso, valor, allow_with_approval=aprobar,
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _anadir_hash(self, event):
        dlg = CustomTextEntryDialog(
            self, _("Hash SHA-256 canónico de 64 caracteres:"), _("Añadir hash canónico"))
        valor = dlg.GetValue().strip().lower() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if len(valor) != 64 or any(c not in "0123456789abcdef" for c in valor):
            if valor:
                notificar(_("El hash debe contener exactamente 64 caracteres hexadecimales."))
            return
        mastodon_service.admin_crear_recurso_async(
            self.account_id, self.recurso, valor, es_hash=True,
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _probar_correo(self, event):
        dlg = CustomTextEntryDialog(self, _("Correo que quieres comprobar:"), _("Comprobar bloqueo canónico"))
        valor = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not valor:
            return

        def _fin(n, err):
            if err:
                self._error(err)
            elif n:
                notificar(_("El correo coincide con {0} bloqueo(s) canónico(s).").format(n))
            else:
                notificar(_("El correo no está bloqueado por ninguna regla canónica."))
        mastodon_service.admin_probar_email_canonico_async(self.account_id, valor, callback=_fin)

    def _detalle(self, event):
        x = self._actual()
        if not x:
            return
        mastodon_service.admin_detalle_recurso_async(
            self.account_id, self.recurso, x["id"], callback=self._mostrar_detalle)

    def _mostrar_detalle(self, x, err):
        if err:
            self._error(err)
            return
        x = x or {}
        lineas = [_("Valor: {0}").format(x["valor"]), _("Creado: {0}").format(x["creado"] or "-")]
        if x.get("historial"):
            lineas += ["", _("Intentos de alta recientes:")]
            lineas += [_("{0}: {1} cuentas, {2} direcciones IP").format(
                h["dia"], h["cuentas"], h["usos"]) for h in x["historial"]]
        mostrar_info(self, "\n".join(lineas), _("Detalle de la regla"))

    def _eliminar(self, event):
        x = self._actual()
        if not x or confirmar(self, _("¿Eliminar esta regla?"), _("Eliminar regla")) != wx.ID_YES:
            return
        mastodon_service.admin_borrar_recurso_async(
            self.account_id, self.recurso, x["id"],
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))

    def _mas(self, event):
        self._cargar(True) if self.next_max_id else notificar(_("No hay más elementos."))


class MastodonHashtagEditDialog(wx.Dialog):
    def __init__(self, parent, tag):
        super().__init__(parent, title=_("Configurar hashtag #{0}").format(tag["nombre"]),
                         size=(500, 350), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Nombre para &mostrar:")), 0, wx.ALL, 8)
        self.nombre = AccessibleTextCtrl(self, name=_("Nombre visible del hashtag"))
        self.nombre.SetValue(tag.get("display_name") or tag["nombre"])
        s.Add(self.nombre, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.listable = wx.CheckBox(self, label=_("Permitir que aparezca en búsquedas y directorios"))
        self.listable.SetValue(bool(tag.get("listable")))
        s.Add(self.listable, 0, wx.ALL, 8)
        self.trendable = wx.CheckBox(self, label=_("Permitir que aparezca como tendencia"))
        self.trendable.SetValue(bool(tag.get("trendable")))
        s.Add(self.trendable, 0, wx.ALL, 8)
        self.usable = wx.CheckBox(self, label=_("Permitir su uso en publicaciones locales"))
        self.usable.SetValue(bool(tag.get("usable")))
        s.Add(self.usable, 0, wx.ALL, 8)
        s.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()

    def valores(self):
        return {"display_name": self.nombre.GetValue().strip(), "listable": self.listable.GetValue(),
                "trendable": self.trendable.GetValue(), "usable": self.usable.GetValue()}


class MastodonAdminTrendsDialog(_AdminBaseDialog):
    _TIPOS = ["tags", "statuses", "links", "all_tags", "publishers"]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: tendencias y descubrimiento"), size=(850, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.items = []
        self.next_max_id = None
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Sección:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.tipo = wx.Choice(self, choices=[_("Hashtags en tendencia"), _("Publicaciones en tendencia"),
                                             _("Enlaces en tendencia"), _("Todos los hashtags"),
                                             _("Publicadores de enlaces")])
        self.tipo.SetSelection(0)
        self.tipo.Bind(wx.EVT_CHOICE, lambda e: self._cargar(False))
        fila.Add(self.tipo, 1, wx.ALL, 6)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Elementos de descubrimiento"))
        self.lista.InsertColumn(0, _("Elemento"), width=520)
        self.lista.InsertColumn(1, _("Estado"), width=200)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        for et, fn in ((_("&Aprobar"), lambda e: self._moderar(True)),
                       (_("&Rechazar"), lambda e: self._moderar(False)),
                       (_("&Configurar hashtag..."), self._configurar_hashtag),
                       (_("Cargar &anteriores"), self._mas), (_("&Actualizar"), lambda e: self._cargar(False))):
            b = wx.Button(self, label=et)
            b.Bind(wx.EVT_BUTTON, fn)
            bs.Add(b, 0, wx.ALL, 4)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar(False)

    def _cargar(self, mas=False):
        tipo = self._TIPOS[self.tipo.GetSelection()]
        cursor = self.next_max_id if mas else None

        def _fin(res, err):
            if err:
                self._error(err)
                return
            pagina = res if isinstance(res, dict) else {"items": res or [], "next_max_id": None}
            nuevos = pagina.get("items") or []
            if mas:
                vistos = {str(x.get("id")) for x in self.items}
                self.items.extend(x for x in nuevos if str(x.get("id")) not in vistos)
            else:
                self.items = nuevos
            self.next_max_id = pagina.get("next_max_id")
            self._pintar(tipo)
        if tipo in ("tags", "statuses", "links"):
            mastodon_service.admin_listar_tendencias_async(self.account_id, tipo, callback=_fin)
        elif tipo == "all_tags":
            mastodon_service.admin_listar_hashtags_async(self.account_id, max_id=cursor, callback=_fin)
        else:
            mastodon_service.admin_listar_publicadores_async(self.account_id, max_id=cursor, callback=_fin)

    def _pintar(self, tipo):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, x in enumerate(self.items):
            if tipo == "all_tags":
                texto = "#" + x["nombre"]
                estado = _("listable {0}, utilizable {1}, tendencia {2}, revisión {3}").format(
                    _si(x["listable"]), _si(x["usable"]), _si(x["trendable"]), _si(x["requiere_revision"]))
            elif tipo == "publishers":
                texto = x["dominio"]
                estado = _("tendencia {0}, revisión {1}").format(_si(x["trendable"]), _si(x["requiere_revision"]))
            else:
                texto = x.get("texto", "")
                estado = _("Pendiente de revisión")
            self.lista.InsertItem(i, texto)
            self.lista.SetItem(i, 1, estado)
            self.lista.establecer_lectura(i, texto + ", " + estado)
        self._seleccionar_primero(self.lista)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.items[i] if 0 <= i < len(self.items) else None

    def _moderar(self, aprobar):
        x = self._actual()
        if not x:
            return
        tipo = self._TIPOS[self.tipo.GetSelection()]
        cb = lambda _r, e: self._error(e) if e else self._cargar(False)
        if tipo in ("tags", "statuses", "links"):
            mastodon_service.admin_moderar_tendencia_async(
                self.account_id, tipo, x["id"], aprobar, callback=cb)
        elif tipo == "publishers":
            mastodon_service.admin_moderar_publicador_async(
                self.account_id, x["id"], aprobar, callback=cb)
        else:
            datos = dict(x)
            datos["trendable"] = aprobar
            datos["listable"] = aprobar if x.get("requiere_revision") else x.get("listable")
            datos["usable"] = aprobar if x.get("requiere_revision") else x.get("usable")
            mastodon_service.admin_actualizar_hashtag_async(
                self.account_id, x["id"], datos, callback=cb)

    def _mas(self, event):
        if self.next_max_id:
            self._cargar(True)
        else:
            notificar(_("Esta sección no tiene más páginas."))

    def _configurar_hashtag(self, event):
        if self._TIPOS[self.tipo.GetSelection()] != "all_tags":
            notificar(_("Selecciona «Todos los hashtags» para configurar sus permisos."))
            return
        tag = self._actual()
        if not tag:
            return
        dlg = MastodonHashtagEditDialog(self, tag)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        datos = dlg.valores()
        dlg.Destroy()
        mastodon_service.admin_actualizar_hashtag_async(
            self.account_id, tag["id"], datos,
            callback=lambda _r, e: self._error(e) if e else self._cargar(False))


class MastodonAdminStatsDialog(_AdminBaseDialog):
    _CLAVES = [
        ("active_users", lambda: _("Medida: usuarios activos")),
        ("new_users", lambda: _("Medida: usuarios nuevos")),
        ("interactions", lambda: _("Medida: interacciones")),
        ("opened_reports", lambda: _("Medida: informes abiertos")),
        ("resolved_reports", lambda: _("Medida: informes resueltos")),
        ("languages", lambda: _("Dimensión: idiomas")),
        ("sources", lambda: _("Dimensión: aplicaciones cliente")),
        ("servers", lambda: _("Dimensión: servidores remotos")),
        ("space_usage", lambda: _("Dimensión: uso de espacio")),
        ("software_versions", lambda: _("Dimensión: versiones de software")),
        ("retention", lambda: _("Retención de usuarios")),
        ("instance_accounts", lambda: _("Dominio: cuentas originadas")),
        ("instance_media_attachments", lambda: _("Dominio: espacio de multimedia")),
        ("instance_reports", lambda: _("Dominio: informes recibidos")),
        ("instance_statuses", lambda: _("Dominio: publicaciones recibidas (consulta costosa)")),
        ("instance_follows", lambda: _("Dominio: cuentas seguidas por usuarios locales")),
        ("instance_followers", lambda: _("Dominio: seguidores de cuentas locales")),
        ("instance_accounts_dim", lambda: _("Dominio: cuentas más seguidas")),
        ("instance_languages", lambda: _("Dominio: idiomas más usados")),
        ("tag_accounts", lambda: _("Hashtag: cuentas que lo usaron")),
        ("tag_uses", lambda: _("Hashtag: publicaciones que lo usaron")),
        ("tag_servers", lambda: _("Hashtag: servidores de origen")),
        ("tag_servers_dim", lambda: _("Hashtag: servidores más frecuentes")),
        ("tag_languages", lambda: _("Hashtag: idiomas más frecuentes")),
    ]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Administración: estadísticas del servidor"), size=(850, 680),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Días del periodo:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.dias = wx.SpinCtrl(self, min=1, max=365, initial=30, name=_("Días de estadísticas"))
        fila.Add(self.dias, 0, wx.ALL, 6)
        fila.Add(wx.StaticText(self, label=_("&Retención:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.frecuencia = wx.Choice(self, choices=[_("Diaria"), _("Mensual")], name=_("Frecuencia de retención"))
        self.frecuencia.SetSelection(1)
        fila.Add(self.frecuencia, 0, wx.ALL, 6)
        b = wx.Button(self, label=_("&Consultar"))
        b.Bind(wx.EVT_BUTTON, self._cargar)
        fila.Add(b, 0, wx.ALL, 6)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        params = wx.BoxSizer(wx.HORIZONTAL)
        izq = wx.BoxSizer(wx.VERTICAL)
        izq.Add(wx.StaticText(self, label=_("&Métricas que se consultarán:")), 0, wx.ALL, 5)
        self.claves = AccessibleCheckListBox(
            self, choices=[f() for _k, f in self._CLAVES], size=(-1, 260),
            name=_("Métricas administrativas disponibles"))
        for i in range(11):
            self.claves.Check(i, True)
        izq.Add(self.claves, 1, wx.EXPAND | wx.ALL, 5)
        params.Add(izq, 1, wx.EXPAND | wx.ALL, 4)
        der = wx.BoxSizer(wx.VERTICAL)
        der.Add(wx.StaticText(self, label=_("&Dominio remoto para métricas de dominio:")), 0, wx.ALL, 5)
        self.dominio = AccessibleTextCtrl(self, name=_("Dominio remoto de las métricas"),
                                           ayuda_texto=_("Solo se usa en las métricas cuyo nombre empieza por Dominio."))
        der.Add(self.dominio, 0, wx.EXPAND | wx.ALL, 5)
        der.Add(wx.StaticText(self, label=_("ID numérico del &hashtag:")), 0, wx.ALL, 5)
        self.tag_id = AccessibleTextCtrl(self, name=_("ID del hashtag para métricas"),
                                         ayuda_texto=_("Se obtiene en la sección Todos los hashtags."))
        der.Add(self.tag_id, 0, wx.EXPAND | wx.ALL, 5)
        der.Add(wx.StaticText(
            self, label=_("Las consultas por publicaciones de un dominio pueden ser costosas y agotar el tiempo del servidor.")),
            0, wx.EXPAND | wx.ALL, 5)
        params.Add(der, 1, wx.EXPAND | wx.ALL, 4)
        s.Add(params, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
        self.texto = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                         name=_("Estadísticas administrativas"))
        s.Add(self.texto, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar(None)

    def _cargar(self, event):
        speak(_("Cargando estadísticas..."), interrupt=True)
        seleccionadas = {self._CLAVES[i][0] for i in range(len(self._CLAVES)) if self.claves.IsChecked(i)}
        if not seleccionadas:
            notificar(_("Selecciona al menos una métrica."))
            return
        if any(x.startswith("instance_") for x in seleccionadas) and not self.dominio.GetValue().strip():
            notificar(_("Las métricas de dominio necesitan un dominio remoto."))
            return
        if any(x.startswith("tag_") for x in seleccionadas) and not self.tag_id.GetValue().strip():
            notificar(_("Las métricas de hashtag necesitan su ID numérico."))
            return

        def _fin(d, err):
            if err:
                self._error(err)
                return
            d = d or {}
            lineas = [_("Periodo: últimos {0} días").format(self.dias.GetValue()), ""]
            for m in d.get("medidas", []):
                valor = m.get("humano") or m.get("total")
                lineas.append("{0}: {1}".format(m["clave"], valor))
                if m.get("anterior") is not None:
                    lineas.append(_("  Periodo anterior: {0}").format(m["anterior"]))
                for punto in m.get("serie") or []:
                    lineas.append("  {0}: {1}".format(punto["fecha"], punto["valor"]))
            for dim in d.get("dimensiones", []):
                lineas += ["", dim["clave"] + ":"]
                lineas += ["  {0}: {1}".format(x["clave"], x["valor"]) for x in dim["valores"]]
            if d.get("retencion"):
                lineas += ["", _("Retención:")]
                for cohorte in d["retencion"]:
                    lineas.append(_("  Cohorte {0} ({1}):").format(cohorte["periodo"], cohorte["frecuencia"]))
                    lineas += [_("    {0}: {1} personas, {2:.1f} %").format(
                        x["fecha"], x["valor"], x["tasa"] * 100) for x in cohorte["datos"]]
            self.texto.SetValue("\n".join(lineas) if len(lineas) > 2 else _("El servidor no devolvió estadísticas."))
        mastodon_service.admin_estadisticas_avanzadas_async(
            self.account_id, dias=self.dias.GetValue(), claves=seleccionadas,
            dominio=self.dominio.GetValue().strip() or None,
            tag_id=self.tag_id.GetValue().strip() or None,
            frecuencia="day" if self.frecuencia.GetSelection() == 0 else "month", callback=_fin)
