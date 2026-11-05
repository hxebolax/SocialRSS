# -*- coding: utf-8 -*-
"""X (Twitter), gestión: tus listas (crear, editar, borrar y sus miembros) y las comunidades (buscarlas, abrirlas, unirse y salir)."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.x import x_service

from .dialogos import DialogoBase, DialogoTexto, confirmar_async
from .dialogos_mastodon import DialogoLista

log = logging.getLogger(__name__)


class DialogoListasX(DialogoLista):
    """Tus listas: abrirlas como buffer, crearlas, editarlas, borrarlas y gestionar quién está en ellas."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Tus listas de X"), _("Lista de tus listas"), self._abrir_pos)
        self.account_id = account_id
        self.items = []
        self.boton(_("_Abrir"), lambda: self._abrir_pos(self.lista.posicion()))
        self.boton(_("_Miembros…"), self._miembros)
        self.boton(_("_Crear lista…"), self._crear)
        self.boton(_("_Editar…"), self._editar)
        self.boton(_("_Borrar lista"), self._borrar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self, conservar: int = 0):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([i["name"] for i in self.items])
            self.mostrar_estado(_("{n} listas").format(n=len(self.items)) if self.items else _("No tienes listas"))
            self.lista.enfocar()
            self.padre.cargar_arbol()     # las listas son nodos del árbol: se guardan al leerlas
        x_service.obtener_listas_async(self.account_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir_pos(self, pos):
        if not (0 <= pos < len(self.items)):
            return
        it = self.items[pos]
        self.close()
        self.padre._abrir_buffer(self.account_id, f"list:{it['list_id']}", _("Lista: {n}").format(n=it["name"]),
                                 servicio="x")

    def _miembros(self):
        it = self._actual()
        if it:
            DialogoMiembrosListaX(self, self.account_id, it).present()

    def _formulario(self, titulo: str, aceptar: str, nombre: str = "", privada: bool = None):
        d = DialogoBase(self, titulo, aceptar)
        e_nombre = d.campo(_("_Nombre:"), Gtk.Entry(text=nombre, activates_default=True, width_chars=36))
        e_desc = d.campo(_("_Descripción (opcional):"), Gtk.Entry(activates_default=True, width_chars=36),
                         _("Al editar, déjalo vacío para no cambiarla."))
        c_priv = None
        if privada is not None:
            c_priv = d.campo(_("Lista _privada (solo la ves tú):"), Gtk.Switch(active=privada, halign=Gtk.Align.START))
        d.set_focus(e_nombre)
        return d, e_nombre, e_desc, c_priv

    def _crear(self):
        d, nombre, desc, priv = self._formulario(_("Crear una lista"), _("_Crear"), privada=False)

        def _hacer():
            if not nombre.get_text().strip():
                notificar(_("Escribe un nombre"))
                return

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo crear: {e}").format(e=err))
                    return
                notificar(_("Lista creada"))
                d.close()
                self._refrescar()
            x_service.crear_lista_async(self.account_id, nombre.get_text().strip(), desc.get_text().strip(),
                                        priv.get_active(), callback=_fin)
        d.aceptar = _hacer
        d.present()

    def _editar(self):
        it = self._actual()
        if not it:
            return
        d, nombre, desc, _p = self._formulario(_("Editar la lista"), _("_Guardar"), nombre=it["name"])

        def _hacer():
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo guardar: {e}").format(e=err))
                    return
                notificar(_("Lista actualizada"))
                d.close()
                self._refrescar()
            x_service.editar_lista_async(self.account_id, it["list_id"], nombre.get_text().strip() or None,
                                         desc.get_text().strip() or None, callback=_fin)
        d.aceptar = _hacer
        d.present()

    def _borrar(self):
        it = self._actual()
        if not it:
            return

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo borrar: {e}").format(e=err) if err else _("Lista borrada"))
                self._refrescar()
            x_service.borrar_lista_async(self.account_id, it["list_id"], callback=_fin)
        confirmar_async(self, _("¿Borrar la lista «{n}»? No se puede deshacer.").format(n=it["name"]), _hacer,
                        etiqueta_si=_("Borrar"))


class DialogoMiembrosListaX(DialogoLista):
    def __init__(self, padre, account_id: int, lista: dict):
        super().__init__(padre, _("Miembros de {n}").format(n=lista["name"]), _("Lista de miembros"),
                         lambda pos: self._perfil())
        self.account_id, self.list_id = account_id, lista["list_id"]
        self.items = []
        self.boton(_("Ver _perfil"), self._perfil)
        self.boton(_("_Añadir cuenta…"), self._anadir)
        self.boton(_("_Quitar de la lista"), self._quitar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{m.get('name') or m.get('screen_name')} (@{m.get('screen_name')})" for m in self.items])
            self.mostrar_estado(_("{n} miembros").format(n=len(self.items)) if self.items else _("La lista está vacía"))
            self.lista.enfocar()
        x_service.miembros_lista_async(self.account_id, self.list_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _perfil(self):
        m = self._actual()
        if m:
            from .dialogos_x import DialogoPerfilX
            DialogoPerfilX(self.padre.padre, self.account_id, m["screen_name"]).present()

    def _anadir(self):
        def _hacer(texto):
            screen = texto.lstrip("@")

            def _resuelto(info, err):
                if err or not info or not info.get("user_id"):
                    notificar(_("No se encontró a @{u}").format(u=screen))
                    return

                def _fin(res, err2):
                    notificar(_("No se pudo añadir: {e}").format(e=err2) if err2 else _("Cuenta añadida"))
                    self._refrescar()
                x_service.gestionar_miembro_lista_async(self.account_id, self.list_id, str(info["user_id"]), True,
                                                        callback=_fin)
            x_service.resolver_usuario_async(self.account_id, screen, callback=_resuelto)
        DialogoTexto(self, _("Añadir a la lista"), _("_Usuario de X (con o sin @):"), _hacer,
                     aceptar=_("_Añadir")).present()

    def _quitar(self):
        m = self._actual()
        if not m:
            return

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo quitar: {e}").format(e=err) if err else _("Cuenta quitada"))
                self._refrescar()
            x_service.gestionar_miembro_lista_async(self.account_id, self.list_id, str(m["user_id"]), False,
                                                    callback=_fin)
        confirmar_async(self, _("¿Quitar a @{u} de la lista?").format(u=m["screen_name"]), _hacer,
                        etiqueta_si=_("Quitar"))


class DialogoComunidadesX(DialogoLista):
    """Resultado de buscar comunidades: abrirlas como buffer, unirse, salir y leer su descripción."""

    def __init__(self, padre, account_id: int, comunidades: list, consulta: str):
        super().__init__(padre, _("Comunidades de X: {q}").format(q=consulta), _("Lista de comunidades"),
                         lambda pos: self._abrir())
        self.account_id, self.items = account_id, comunidades
        self.boton(_("_Abrir como buffer"), self._abrir)
        self.boton(_("_Unirse"), lambda: self._cambiar("join"))
        self.boton(_("_Salir"), lambda: self._cambiar("leave"))
        self.boton(_("_Descripción"), self._descripcion)
        self.cerrar_boton()
        self._pintar()

    def _pintar(self):
        self.lista.poner([_("{n}. {m} miembros{s}").format(n=c["name"], m=c["member_count"],
                                                          s=_(", eres miembro") if c["is_member"] else "")
                          for c in self.items])
        self.mostrar_estado(_("{n} comunidades").format(n=len(self.items)))
        self.lista.enfocar()

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir(self):
        c = self._actual()
        if c:
            self.close()
            self.padre.tw_abrir_comunidad(self.account_id, c["id"], c["name"])

    def _descripcion(self):
        c = self._actual()
        if c:
            notificar(_("{m} miembros. {d}").format(m=c["member_count"], d=c.get("description") or _("Sin descripción")))

    def _cambiar(self, accion: str):
        c = self._actual()
        if not c:
            return

        def _hacer():
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo: {e}").format(e=err))
                    return
                c["is_member"] = accion == "join"
                notificar(_("Te has unido a la comunidad") if accion == "join" else _("Has salido de la comunidad"))
                self._pintar()
            x_service.comunidad_async(self.account_id, str(c["id"]), accion, callback=_fin)
        if accion == "leave":
            confirmar_async(self, _("¿Salir de esta comunidad?"), _hacer, etiqueta_si=_("Salir"))
        else:
            _hacer()
