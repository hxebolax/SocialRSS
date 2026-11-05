# -*- coding: utf-8 -*-
"""Mastodon: colecciones de cuentas (Mastodon 4.6). Tus colecciones, las de otra cuenta y aquellas en las que apareces tú.

Mismos servicios que la versión de Windows (mastodon_colecciones.py)."""

import webbrowser

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.mastodon.mastodon_colecciones import MAX_DESCRIPCION, MAX_NOMBRE
from app.services.mastodon.mastodon_service import mastodon_service

from .dialogos import DialogoBase, DialogoElegir, confirmar_async
from .dialogos_mastodon import DialogoLista

ESTADOS = {"accepted": "aceptada", "pending": "pendiente de que la acepte", "rejected": "rechazada", "revoked": "se retiró"}


def _estado(e: str) -> str:
    return _(ESTADOS[e]) if e in ESTADOS else (e or "")


class DialogoFormColeccion(DialogoBase):
    """Crear o editar una colección. Entrega (datos, cuentas) a `al_aceptar`."""

    def __init__(self, padre, al_aceptar, datos: dict = None):
        datos = datos or {}
        super().__init__(padre, _("Editar colección") if datos.get("id") else _("Nueva colección"), _("_Guardar"))
        self.al_aceptar = al_aceptar
        self.nombre = self.campo(_("_Nombre (máximo {n} caracteres):").format(n=MAX_NOMBRE),
                                 Gtk.Entry(text=datos.get("name", ""), max_length=MAX_NOMBRE, activates_default=True,
                                           width_chars=40))
        self.descripcion = self.campo(_("_Descripción (máximo {n} caracteres):").format(n=MAX_DESCRIPCION),
                                      Gtk.Entry(text=datos.get("description", ""), max_length=MAX_DESCRIPCION,
                                                activates_default=True))
        self.idioma = self.campo(_("_Idioma (código, por ejemplo es):"),
                                 Gtk.Entry(text=datos.get("language", "") or "es", activates_default=True))
        self.etiqueta = self.campo(_("_Etiqueta que la describe (opcional, sin #):"),
                                   Gtk.Entry(text=datos.get("tag", ""), activates_default=True))
        self.visible = self.campo(_("Mostrarla en mi _perfil, en las búsquedas y en las recomendaciones:"),
                                  Gtk.Switch(active=bool(datos.get("discoverable", True)), halign=Gtk.Align.START))
        self.sensible = self.campo(_("Marcarla como contenido _sensible:"),
                                   Gtk.Switch(active=bool(datos.get("sensitive", False)), halign=Gtk.Align.START))
        self.cuentas = None
        if not datos.get("id"):
            self.cuentas = self.campo(_("_Cuentas que incluir desde el principio (opcional, @usuario separados por espacios):"),
                                      Gtk.Entry(activates_default=True))
        self.set_focus(self.nombre)

    def aceptar(self):
        nombre = self.nombre.get_text().strip()
        if not nombre:
            notificar(_("La colección necesita un nombre."))
            return
        datos = {"name": nombre, "description": self.descripcion.get_text().strip(),
                 "language": self.idioma.get_text().strip().lower(), "tag_name": self.etiqueta.get_text().strip().lstrip("#"),
                 "discoverable": self.visible.get_active(), "sensitive": self.sensible.get_active()}
        cuentas = ([c.strip().lstrip("@") for c in self.cuentas.get_text().replace(",", " ").split() if c.strip()]
                   if self.cuentas else [])
        self.close()
        self.al_aceptar(datos, cuentas)


class DialogoCuentasColeccion(DialogoLista):
    """Las cuentas de una colección con su estado; en las tuyas se pueden añadir y quitar."""

    def __init__(self, padre, account_id: int, coleccion_id: str):
        super().__init__(padre, _("Cuentas de la colección"), _("Cuentas de la colección"))
        self.account_id, self.coleccion_id, self.cuentas, self.es_mia = account_id, coleccion_id, [], False
        self.b_anadir = self.boton(_("_Añadir cuenta…"), self._anadir, False)
        self.b_quitar = self.boton(_("_Quitar de la colección"), self._quitar, False)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err or not res:
                self.mostrar_estado(_("No se pudo cargar la colección: {e}").format(e=err or ""))
                return
            col = res["coleccion"]
            self.es_mia = bool(res.get("es_mia"))
            self.cuentas = res.get("cuentas", [])
            self.set_title(_("Colección «{n}»").format(n=col.get("name", "")))
            self.lista.poner([_("{n} (@{a}), {e}").format(n=c.get("nombre") or c.get("acct"), a=c.get("acct", ""),
                                                           e=_estado(c.get("estado"))) for c in self.cuentas])
            self.mostrar_estado(" ".join(x for x in (_("«{n}»: {c} cuentas.").format(n=col.get("name", ""), c=len(self.cuentas)),
                                                     col.get("description") or "") if x))
            self.b_anadir.set_sensitive(self.es_mia)
            self.b_quitar.set_sensitive(self.es_mia and bool(self.cuentas))
            self.lista.enfocar()
        mastodon_service.ver_coleccion_async(self.account_id, self.coleccion_id, callback=_fin)

    def _anadir(self):
        d = DialogoBase(self, _("Añadir a la colección"), _("_Añadir"))
        cuenta = d.campo(_("_Cuenta que añadir (@usuario o @usuario@servidor):"), Gtk.Entry(activates_default=True, width_chars=40))
        d.set_focus(cuenta)

        def _ok():
            acct = cuenta.get_text().strip().lstrip("@")
            if not acct:
                return
            d.close()

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo añadir: {e}").format(e=err))
                    return
                notificar(_("@{a} añadida. Queda pendiente de que acepte aparecer en la colección.").format(a=acct)
                          if (res or {}).get("estado") == "pending" else _("@{a} añadida a la colección.").format(a=acct))
                self._cargar()
            mastodon_service.anadir_a_coleccion_async(self.account_id, self.coleccion_id, acct, callback=_fin)
        d.aceptar = _ok
        d.present()

    def _quitar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.cuentas):
            return
        c = self.cuentas[p]

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo quitar: {e}").format(e=err) if err else _("Cuenta quitada de la colección."))
                if not err:
                    self._cargar()
            mastodon_service.quitar_de_coleccion_async(self.account_id, self.coleccion_id, c["item_id"], callback=_fin)
        confirmar_async(self, _("¿Quitar a @{a} de la colección?").format(a=c.get("acct", "")), _hacer, etiqueta_si=_("Quitar"))


class DialogoColecciones(DialogoLista):
    """Lista de colecciones. modo: «mias», «de» (las de la cuenta `acct`) o «donde_estoy»."""

    def __init__(self, padre, account_id: int, modo: str = "mias", acct: str = None):
        titulos = {"mias": _("Mis colecciones"), "de": _("Colecciones de @{a}").format(a=(acct or "").lstrip("@")),
                   "donde_estoy": _("Colecciones en las que apareces")}
        super().__init__(padre, titulos.get(modo, titulos["mias"]), _("Lista de colecciones"), lambda pos: self._ver(pos))
        self.account_id, self.modo, self.acct, self.colecciones = account_id, modo, acct, []
        self.boton(_("_Ver cuentas"), lambda: self._ver(self.lista.posicion()))
        if modo == "mias":
            self.boton(_("_Nueva…"), self._nueva)
            self.boton(_("_Editar…"), self._editar)
            self.boton(_("_Borrar"), self._borrar)
        elif modo == "donde_estoy":
            self.boton(_("_Retirarme de esta colección"), self._retirarme)
        self.boton(_("Abrir en el _navegador"), self._navegador)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando las colecciones…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las colecciones: {e}").format(e=err))
                return
            self.colecciones = res or []
            lineas = []
            for c in self.colecciones:
                nombre = c.get("name", "")
                if self.modo == "mias" and not c.get("discoverable"):
                    nombre += " " + _("(oculta)")
                if self.modo == "donde_estoy" and c.get("mi_item"):
                    nombre += " — " + _estado(c["mi_item"].get("state"))
                lineas.append(_("{n}, {c} cuentas. {d}").format(n=nombre, c=c.get("item_count", 0), d=c.get("description", "")))
            self.lista.poner(lineas)
            vacio = {"mias": _("Todavía no tienes colecciones. Pulsa Nueva para crear una."),
                     "de": _("Esta cuenta no tiene colecciones públicas."), "donde_estoy": _("No apareces en ninguna colección.")}
            self.mostrar_estado(_("{n} colecciones").format(n=len(lineas)) if lineas else vacio.get(self.modo, ""))
            self.lista.enfocar()
        if self.modo == "donde_estoy":
            mastodon_service.colecciones_donde_estoy_async(self.account_id, callback=_fin)
        else:
            mastodon_service.colecciones_de_cuenta_async(self.account_id, acct=self.acct if self.modo == "de" else None,
                                                         callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.colecciones[p] if 0 <= p < len(self.colecciones) else None

    def _ver(self, pos):
        if 0 <= pos < len(self.colecciones):
            DialogoCuentasColeccion(self, self.account_id, self.colecciones[pos]["id"]).present()

    def _navegador(self):
        c = self._actual()
        if c and c.get("url"):
            webbrowser.open(c["url"])

    def _nueva(self):
        def _crear(datos, cuentas):
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo crear la colección: {e}").format(e=err))
                    return
                notificar(_("Colección «{n}» creada.").format(n=datos["name"]))
                self._cargar()
            mastodon_service.crear_coleccion_async(self.account_id, datos, accts=cuentas, callback=_fin)
        DialogoFormColeccion(self, _crear).present()

    def _editar(self):
        c = self._actual()
        if not c:
            return

        def _guardar(datos, _cuentas):
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo guardar: {e}").format(e=err))
                    return
                notificar(_("Colección actualizada."))
                self._cargar()
            mastodon_service.editar_coleccion_async(self.account_id, c["id"], datos, callback=_fin)
        DialogoFormColeccion(self, _guardar, c).present()

    def _borrar(self):
        c = self._actual()
        if not c:
            return

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo borrar: {e}").format(e=err) if err else _("Colección borrada."))
                if not err:
                    self._cargar()
            mastodon_service.borrar_coleccion_async(self.account_id, c["id"], callback=_fin)
        confirmar_async(self, _("¿Borrar la colección «{n}»? No se puede deshacer.").format(n=c.get("name", "")), _hacer,
                        etiqueta_si=_("Borrar"))

    def _retirarme(self):
        c = self._actual()
        item = c.get("mi_item") if c else None
        if not item:
            notificar(_("No se encontró tu entrada en esta colección."))
            return

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo: {e}").format(e=err) if err else _("Te has retirado de la colección."))
                if not err:
                    self._cargar()
            mastodon_service.retirarme_de_coleccion_async(self.account_id, c["id"], item["id"], callback=_fin)
        confirmar_async(self, _("¿Retirarte de la colección «{n}»? Dejarás de aparecer en ella.").format(n=c.get("name", "")),
                        _hacer, etiqueta_si=_("Retirarme"))


def anadir_a_mi_coleccion(padre, account_id: int, acct: str):
    """«Añadir a una de mis colecciones»: elige colección y añade la cuenta."""
    def _fin(res, err):
        if err:
            notificar(_("No se pudieron cargar tus colecciones: {e}").format(e=err))
            return
        if not res:
            notificar(_("Todavía no tienes colecciones. Créalas en Mastodon, Colecciones, Mis colecciones."))
            return

        def _elegida(i):
            col = res[i]

            def _fin2(r2, e2):
                if e2:
                    notificar(_("No se pudo añadir: {e}").format(e=e2))
                else:
                    notificar((_("@{a} añadida a «{c}». Queda pendiente de que acepte.") if (r2 or {}).get("estado") == "pending"
                               else _("@{a} añadida a «{c}».")).format(a=acct, c=col["name"]))
            mastodon_service.anadir_a_coleccion_async(account_id, col["id"], acct, callback=_fin2)
        DialogoElegir(padre, _("Añadir a una colección"), _("_Colección:"), [c.get("name", "") for c in res], _elegida,
                      aceptar=_("_Añadir")).present()
    mastodon_service.colecciones_de_cuenta_async(account_id, callback=_fin)
