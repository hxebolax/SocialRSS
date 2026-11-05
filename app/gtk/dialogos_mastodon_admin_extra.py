# -*- coding: utf-8 -*-
"""Mastodon: administración avanzada de la instancia: bloqueos de IP, dominios permitidos, bloqueos de correo, tendencias y
estadísticas del servidor. Los mismos servicios que usa la versión de Windows (mastodon_admin.py)."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.mastodon.mastodon_service import mastodon_service

from .dialogos import DialogoBase, confirmar_async
from .dialogos_mastodon import DialogoLista
from .dialogos_reddit import _marco, _vista_texto

SEVERIDADES_IP = [("no_access", "Sin acceso"), ("sign_up_requires_approval", "Altas sujetas a aprobación"),
                  ("sign_up_block", "Bloquear nuevas altas")]


def error_admin(err, prefijo: str = None) -> str:
    texto = str(err or "")
    if any(x in texto.lower() for x in ("401", "403", "forbidden", "unauthorized", "scope")):
        return _("El servidor rechazó el permiso administrativo. Vuelve a autorizar la cuenta con los permisos de moderación "
                 "y comprueba que tu rol permita esta acción.")
    return (prefijo or _("No se pudo completar la operación")) + ": " + texto


def _si(valor) -> str:
    return _("sí") if valor else _("no")


class _ListaPaginada(DialogoLista):
    """Base: lista con «Cargar anteriores» (cursor next_max_id) y «Actualizar»."""

    TITULO = ""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, self.TITULO, self.TITULO)
        self.account_id, self.items, self.siguiente_id = account_id, [], None
        self.botones_extra()
        self.b_mas = self.boton(_("Cargar _anteriores"), lambda: self._cargar(True), False)
        self.boton(_("A_ctualizar"), lambda: self._cargar(False))
        self.cerrar_boton()
        self._cargar(False)

    def botones_extra(self):
        pass

    def cargar_pagina(self, cursor, callback):
        raise NotImplementedError

    def fila(self, x) -> str:
        return ""

    def _cargar(self, mas: bool):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(error_admin(err))
                return
            pagina = res if isinstance(res, dict) else {"items": res or [], "next_max_id": None}
            nuevos = pagina.get("items") or []
            if mas:
                vistos = {str(x.get("id")) for x in self.items}
                self.items.extend(x for x in nuevos if str(x.get("id")) not in vistos)
            else:
                self.items = nuevos
            self.siguiente_id = pagina.get("next_max_id")
            self.b_mas.set_sensitive(bool(self.siguiente_id))
            self.lista.poner([self.fila(x) for x in self.items])
            self.mostrar_estado(_("{n} elementos").format(n=len(self.items)) if self.items else _("No hay elementos"))
            self.lista.enfocar()
        self.cargar_pagina(self.siguiente_id if mas else None, _fin)

    def actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def tras(self, ok: str = ""):
        def _fin(_res, err):
            if err:
                notificar(error_admin(err))
            else:
                if ok:
                    notificar(ok)
                self._cargar(False)
        return _fin


# --------------------------------------------------------------------------- bloqueos de IP
class DialogoBloqueosIP(_ListaPaginada):
    TITULO = _("Administración: bloqueos de IP")

    def botones_extra(self):
        self.boton(_("_Nuevo…"), lambda: self._form(None))
        self.boton(_("_Editar…"), self._editar)
        self.boton(_("_Eliminar"), self._eliminar)

    def cargar_pagina(self, cursor, callback):
        mastodon_service.admin_buscar_ip_blocks_async(self.account_id, max_id=cursor, callback=callback)

    def fila(self, x):
        return ", ".join(p for p in (x["ip"], x["severidad"], x["comentario"], x["creado"], x["expira"]) if p)

    def _editar(self):
        x = self.actual()
        if x:
            mastodon_service.admin_detalle_ip_block_async(
                self.account_id, x["id"], callback=lambda res, err: notificar(error_admin(err)) if err else self._form(res))

    def _form(self, item):
        item = item or {}
        d = DialogoBase(self, _("Editar bloqueo de IP") if item else _("Nuevo bloqueo de IP"), _("_Guardar"))
        ip = d.campo(_("_IP o rango CIDR:"), Gtk.Entry(text=item.get("ip", ""), activates_default=True, width_chars=36))
        sev = d.campo(_("_Severidad:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in SEVERIDADES_IP]))
        sev.set_selected(next((i for i, (c, _n) in enumerate(SEVERIDADES_IP) if c == item.get("severidad")), 0))
        com = d.campo(_("_Comentario interno:"), Gtk.Entry(text=item.get("comentario", ""), activates_default=True))
        exp = d.campo(_("Caducar en _segundos (vacío = permanente):"), Gtk.Entry(activates_default=True))
        d.set_focus(ip)

        def _ok():
            valor = ip.get_text().strip()
            if not valor:
                return
            texto = exp.get_text().strip()
            datos = {"ip": valor, "severidad": SEVERIDADES_IP[sev.get_selected()][0], "comentario": com.get_text().strip(),
                     "expira_en": int(texto) if texto.isdigit() else None}
            d.close()
            mastodon_service.admin_guardar_ip_block_async(self.account_id, datos, item.get("id"), callback=self.tras())
        d.aceptar = _ok
        d.present()

    def _eliminar(self):
        x = self.actual()
        if x:
            confirmar_async(self, _("¿Eliminar el bloqueo de {ip}?").format(ip=x["ip"]),
                            lambda: mastodon_service.admin_desbloquear_ip_async(self.account_id, x["id"], callback=self.tras()),
                            etiqueta_si=_("Eliminar"))


# --------------------------------------------------------------------------- dominios permitidos y bloqueos de correo
RECURSOS = {
    "domain_allows": ("Dominios permitidos", "Dominio a permitir:"),
    "email_domains": ("Dominios de correo bloqueados", "Dominio de correo a bloquear:"),
    "canonical_emails": ("Correos canónicos bloqueados", "Correo a bloquear:"),
}


class DialogoRecursosAdmin(_ListaPaginada):
    """Lista común para la lista de dominios permitidos y los bloqueos de correo."""

    def __init__(self, padre, account_id: int, recurso: str):
        self.recurso = recurso
        self.TITULO = _("Administración: {t}").format(t=_(RECURSOS[recurso][0]))
        super().__init__(padre, account_id)

    def botones_extra(self):
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Detalles"), self._detalle)
        self.boton(_("_Eliminar"), self._eliminar)
        if self.recurso == "canonical_emails":
            self.boton(_("_Comprobar correo…"), self._probar)
            self.boton(_("Añadir _hash…"), self._anadir_hash)

    def cargar_pagina(self, cursor, callback):
        mastodon_service.admin_listar_recurso_async(self.account_id, self.recurso, max_id=cursor, callback=callback)

    def fila(self, x):
        return f"{x['valor']}, {x['creado']}"

    def _pedir(self, titulo: str, etiqueta: str, al_aceptar):
        d = DialogoBase(self, titulo, _("_Aceptar"))
        e = d.campo(etiqueta, Gtk.Entry(activates_default=True, width_chars=46))
        d.set_focus(e)

        def _ok():
            valor = e.get_text().strip()
            if valor:
                d.close()
                al_aceptar(valor)
        d.aceptar = _ok
        d.present()

    def _anadir(self):
        def _con(valor, aprobar=False):
            mastodon_service.admin_crear_recurso_async(self.account_id, self.recurso, valor, allow_with_approval=aprobar,
                                                       callback=self.tras())

        def _valor(v):
            if self.recurso == "email_domains":
                confirmar_async(self, _("¿Permitir altas desde este dominio solo después de aprobación manual? Si dices que no, "
                                        "se bloquean por completo."), lambda: _con(v, True), etiqueta_si=_("Con aprobación"),
                                al_no=lambda: _con(v, False))
            else:
                _con(v)
        self._pedir(_("Añadir regla administrativa"), _(RECURSOS[self.recurso][1]), _valor)

    def _anadir_hash(self):
        def _con(v):
            v = v.lower()
            if len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
                notificar(_("El hash debe contener exactamente 64 caracteres hexadecimales."))
                return
            mastodon_service.admin_crear_recurso_async(self.account_id, self.recurso, v, es_hash=True, callback=self.tras())
        self._pedir(_("Añadir hash canónico"), _("_Hash SHA-256 canónico de 64 caracteres:"), _con)

    def _probar(self):
        def _con(valor):
            def _fin(n, err):
                if err:
                    notificar(error_admin(err))
                elif n:
                    notificar(_("El correo coincide con {n} bloqueo(s) canónico(s).").format(n=n))
                else:
                    notificar(_("El correo no está bloqueado por ninguna regla canónica."))
            mastodon_service.admin_probar_email_canonico_async(self.account_id, valor, callback=_fin)
        self._pedir(_("Comprobar bloqueo canónico"), _("_Correo que quieres comprobar:"), _con)

    def _detalle(self):
        x = self.actual()
        if not x:
            return

        def _fin(det, err):
            if err:
                notificar(error_admin(err))
                return
            det = det or {}
            lineas = [_("Valor: {v}").format(v=det["valor"]), _("Creado: {c}").format(c=det["creado"] or "-")]
            if det.get("historial"):
                lineas += ["", _("Intentos de alta recientes:")]
                lineas += [_("{d}: {c} cuentas, {u} direcciones IP").format(d=h["dia"], c=h["cuentas"], u=h["usos"])
                           for h in det["historial"]]
            from .dialogos_peertube_instancia import texto_en_dialogo
            texto_en_dialogo(self, _("Detalle de la regla"), "\n".join(lineas))
        mastodon_service.admin_detalle_recurso_async(self.account_id, self.recurso, x["id"], callback=_fin)

    def _eliminar(self):
        x = self.actual()
        if x:
            confirmar_async(self, _("¿Eliminar esta regla?"),
                            lambda: mastodon_service.admin_borrar_recurso_async(self.account_id, self.recurso, x["id"],
                                                                                callback=self.tras()),
                            etiqueta_si=_("Eliminar"))


# --------------------------------------------------------------------------- tendencias y descubrimiento
SECCIONES_TENDENCIAS = [("tags", "Hashtags en tendencia"), ("statuses", "Publicaciones en tendencia"),
                        ("links", "Enlaces en tendencia"), ("all_tags", "Todos los hashtags"),
                        ("publishers", "Publicadores de enlaces")]


class DialogoTendenciasAdmin(_ListaPaginada):
    TITULO = _("Administración: tendencias y descubrimiento")

    def botones_extra(self):
        self.combo = Gtk.DropDown.new_from_strings([_(n) for _c, n in SECCIONES_TENDENCIAS])
        self.combo.update_property([Gtk.AccessibleProperty.LABEL], [_("Sección")])
        self.combo.connect("notify::selected", lambda *_a: self._cargar(False))
        self.fila_botones.prepend(self.combo)
        self.boton(_("_Aprobar"), lambda: self._moderar(True))
        self.boton(_("_Rechazar"), lambda: self._moderar(False))
        self.boton(_("_Configurar hashtag…"), self._configurar)

    def seccion(self) -> str:
        return SECCIONES_TENDENCIAS[self.combo.get_selected()][0]

    def cargar_pagina(self, cursor, callback):
        tipo = self.seccion()
        if tipo in ("tags", "statuses", "links"):
            mastodon_service.admin_listar_tendencias_async(self.account_id, tipo, callback=callback)
        elif tipo == "all_tags":
            mastodon_service.admin_listar_hashtags_async(self.account_id, max_id=cursor, callback=callback)
        else:
            mastodon_service.admin_listar_publicadores_async(self.account_id, max_id=cursor, callback=callback)

    def fila(self, x):
        tipo = self.seccion()
        if tipo == "all_tags":
            return "#{n}, " .format(n=x["nombre"]) + _("listable {a}, utilizable {b}, tendencia {c}, revisión {d}").format(
                a=_si(x["listable"]), b=_si(x["usable"]), c=_si(x["trendable"]), d=_si(x["requiere_revision"]))
        if tipo == "publishers":
            return f"{x['dominio']}, " + _("tendencia {c}, revisión {d}").format(c=_si(x["trendable"]), d=_si(x["requiere_revision"]))
        return f"{x.get('texto', '')}, " + _("Pendiente de revisión")

    def _moderar(self, aprobar: bool):
        x = self.actual()
        if not x:
            return
        tipo = self.seccion()
        cb = self.tras()
        if tipo in ("tags", "statuses", "links"):
            mastodon_service.admin_moderar_tendencia_async(self.account_id, tipo, x["id"], aprobar, callback=cb)
        elif tipo == "publishers":
            mastodon_service.admin_moderar_publicador_async(self.account_id, x["id"], aprobar, callback=cb)
        else:
            datos = dict(x)
            datos["trendable"] = aprobar
            datos["listable"] = aprobar if x.get("requiere_revision") else x.get("listable")
            datos["usable"] = aprobar if x.get("requiere_revision") else x.get("usable")
            mastodon_service.admin_actualizar_hashtag_async(self.account_id, x["id"], datos, callback=cb)

    def _configurar(self):
        if self.seccion() != "all_tags":
            notificar(_("Selecciona «Todos los hashtags» para configurar sus permisos."))
            return
        tag = self.actual()
        if not tag:
            return
        d = DialogoBase(self, _("Configurar hashtag #{n}").format(n=tag["nombre"]), _("_Guardar"))
        nombre = d.campo(_("Nombre para _mostrar:"), Gtk.Entry(text=tag.get("display_name") or tag["nombre"], activates_default=True))
        listable = d.campo(_("Permitir que aparezca en _búsquedas y directorios:"),
                           Gtk.Switch(active=bool(tag.get("listable")), halign=Gtk.Align.START))
        trendable = d.campo(_("Permitir que aparezca como _tendencia:"), Gtk.Switch(active=bool(tag.get("trendable")),
                                                                                    halign=Gtk.Align.START))
        usable = d.campo(_("Permitir su _uso en publicaciones locales:"), Gtk.Switch(active=bool(tag.get("usable")),
                                                                                    halign=Gtk.Align.START))
        d.set_focus(nombre)

        def _ok():
            datos = {"display_name": nombre.get_text().strip(), "listable": listable.get_active(),
                     "trendable": trendable.get_active(), "usable": usable.get_active()}
            d.close()
            mastodon_service.admin_actualizar_hashtag_async(self.account_id, tag["id"], datos, callback=self.tras())
        d.aceptar = _ok
        d.present()


# --------------------------------------------------------------------------- estadísticas del servidor
CLAVES_ESTADISTICAS = [
    ("active_users", "Medida: usuarios activos"), ("new_users", "Medida: usuarios nuevos"),
    ("interactions", "Medida: interacciones"), ("opened_reports", "Medida: informes abiertos"),
    ("resolved_reports", "Medida: informes resueltos"), ("languages", "Dimensión: idiomas"),
    ("sources", "Dimensión: aplicaciones cliente"), ("servers", "Dimensión: servidores remotos"),
    ("space_usage", "Dimensión: uso de espacio"), ("software_versions", "Dimensión: versiones de software"),
    ("retention", "Retención de usuarios"), ("instance_accounts", "Dominio: cuentas originadas"),
    ("instance_media_attachments", "Dominio: espacio de multimedia"), ("instance_reports", "Dominio: informes recibidos"),
    ("instance_statuses", "Dominio: publicaciones recibidas (consulta costosa)"),
    ("instance_follows", "Dominio: cuentas seguidas por usuarios locales"),
    ("instance_followers", "Dominio: seguidores de cuentas locales"),
    ("instance_accounts_dim", "Dominio: cuentas más seguidas"), ("instance_languages", "Dominio: idiomas más usados"),
    ("tag_accounts", "Hashtag: cuentas que lo usaron"), ("tag_uses", "Hashtag: publicaciones que lo usaron"),
    ("tag_servers", "Hashtag: servidores de origen"), ("tag_servers_dim", "Hashtag: servidores más frecuentes"),
    ("tag_languages", "Hashtag: idiomas más frecuentes"),
]


class DialogoEstadisticasAdmin(Gtk.Window):
    """Las métricas del servidor que elijas, como texto que se lee con las flechas."""

    def __init__(self, padre, account_id: int):
        super().__init__(title=_("Administración: estadísticas del servidor"), transient_for=padre, modal=True)
        from .mnemonicos import vigilar
        vigilar(self)
        self.padre, self.account_id = padre, account_id
        self.set_default_size(820, 700)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(Gtk.ScrolledWindow(child=caja))
        cab = Gtk.Grid(row_spacing=6, column_spacing=10)
        caja.append(cab)

        def fila(i, etiqueta, widget):
            lbl = Gtk.Label(label=etiqueta, use_underline=True, xalign=0)
            lbl.set_mnemonic_widget(widget)
            cab.attach(lbl, 0, i, 1, 1)
            cab.attach(widget, 1, i, 1, 1)
            return widget
        self.dias = fila(0, _("_Días del periodo:"), Gtk.SpinButton.new_with_range(1, 365, 1))
        self.dias.set_value(30)
        self.frecuencia = fila(1, _("_Retención:"), Gtk.DropDown.new_from_strings([_("Diaria"), _("Mensual")]))
        self.frecuencia.set_selected(1)
        self.dominio = fila(2, _("_Dominio remoto (para las métricas de dominio):"), Gtk.Entry(hexpand=True))
        self.tag_id = fila(3, _("ID numérico del _hashtag (para las métricas de hashtag):"), Gtk.Entry(hexpand=True))
        caja.append(Gtk.Label(label=_("_Métricas que se consultarán:"), use_underline=True, xalign=0))
        self.casillas = []
        marcos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i, (clave, etq) in enumerate(CLAVES_ESTADISTICAS):
            c = Gtk.CheckButton(label=_(etq), active=i < 11)
            marcos.append(c)
            self.casillas.append((clave, c))
        caja.append(Gtk.ScrolledWindow(child=marcos, min_content_height=160, has_frame=True))
        b = Gtk.Button(label=_("_Consultar"), use_underline=True, halign=Gtk.Align.START)
        b.connect("clicked", lambda *_a: self._cargar())
        caja.append(b)
        self.texto = _vista_texto(False)
        self.texto.update_property([Gtk.AccessibleProperty.LABEL], [_("Estadísticas administrativas")])
        caja.append(_marco(self.texto, 240))
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self._cargar()

    def _cargar(self):
        elegidas = {c for c, w in self.casillas if w.get_active()}
        if not elegidas:
            notificar(_("Selecciona al menos una métrica."))
            return
        if any(x.startswith("instance_") for x in elegidas) and not self.dominio.get_text().strip():
            notificar(_("Las métricas de dominio necesitan un dominio remoto."))
            return
        if any(x.startswith("tag_") for x in elegidas) and not self.tag_id.get_text().strip():
            notificar(_("Las métricas de hashtag necesitan su ID numérico."))
            return
        self.texto.get_buffer().set_text(_("Cargando las estadísticas…"))

        def _fin(d, err):
            if err:
                self.texto.get_buffer().set_text(error_admin(err))
                return
            d = d or {}
            lineas = [_("Periodo: últimos {n} días").format(n=int(self.dias.get_value())), ""]
            for m in d.get("medidas", []):
                lineas.append("{c}: {v}".format(c=m["clave"], v=m.get("humano") or m.get("total")))
                if m.get("anterior") is not None:
                    lineas.append(_("  Periodo anterior: {v}").format(v=m["anterior"]))
                lineas += ["  {f}: {v}".format(f=p["fecha"], v=p["valor"]) for p in m.get("serie") or []]
            for dim in d.get("dimensiones", []):
                lineas += ["", dim["clave"] + ":"] + ["  {c}: {v}".format(c=x["clave"], v=x["valor"]) for x in dim["valores"]]
            if d.get("retencion"):
                lineas += ["", _("Retención:")]
                for co in d["retencion"]:
                    lineas.append(_("  Cohorte {p} ({f}):").format(p=co["periodo"], f=co["frecuencia"]))
                    lineas += [_("    {f}: {v} personas, {t:.1f} %").format(f=x["fecha"], v=x["valor"], t=x["tasa"] * 100)
                               for x in co["datos"]]
            self.texto.get_buffer().set_text("\n".join(lineas) if len(lineas) > 2 else _("El servidor no devolvió estadísticas."))
            self.texto.grab_focus()
        mastodon_service.admin_estadisticas_avanzadas_async(
            self.account_id, dias=int(self.dias.get_value()), claves=elegidas, dominio=self.dominio.get_text().strip() or None,
            tag_id=self.tag_id.get_text().strip() or None, frecuencia="day" if self.frecuencia.get_selected() == 0 else "month",
            callback=_fin)


