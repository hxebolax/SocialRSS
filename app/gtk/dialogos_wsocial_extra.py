# -*- coding: utf-8 -*-
"""W Social, parte avanzada: mensajes directos, listas de cuentas, informes, listas, contraseñas de aplicación
y perfil propio. Los diálogos de lista comparten `DialogoLista`."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.plataforma import copiar_texto
from app.core.speech import notificar
from app.services.wsocial.wsocial_moderacion import MOTIVOS_DENUNCIA
from app.services.wsocial.wsocial_service import wsocial_service

from .datos_wsocial import adaptar
from .dialogos import DialogoBase, DialogoTexto, confirmar_async
from .dialogos_mastodon import DialogoLista
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

LIMITE_MENSAJE = 1000
ACTORES = {
    "followers": ("Seguidores", "Lista de seguidores"),
    "follows": ("Siguiendo", "Lista de cuentas que sigues"),
    "blocks": ("Cuentas bloqueadas", "Lista de cuentas bloqueadas"),
    "mutes": ("Cuentas silenciadas", "Lista de cuentas silenciadas"),
    "likes": ("Quién ha marcado me gusta", "Lista de cuentas que dieron me gusta"),
    "reposts": ("Quién ha republicado", "Lista de cuentas que republicaron"),
}


# --------------------------------------------------------------------------- mensajes directos
class DialogoMensajeWSocial(DialogoBase):
    """Escribir un mensaje directo. Ctrl+Intro lo envía."""

    def __init__(self, padre, account_id: int, convo_id: str, titulo: str, al_enviar=None):
        super().__init__(padre, _("Mensaje a {t}").format(t=titulo), _("_Enviar"))
        self.account_id, self.convo_id, self.al_enviar = account_id, convo_id, al_enviar
        self.set_default_size(560, -1)
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        instalar_ayudas(self.texto)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=110, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Mensaje:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.contador = Gtk.Label(xalign=0)
        self.rejilla.attach(self.contador, 1, self._fila, 1, 1)
        self.texto.get_buffer().connect("changed", lambda *_a: self._contar())
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl>Return"),
                                          Gtk.CallbackAction.new(lambda *_a: self.aceptar() or True)))
        self.add_controller(ctl)
        self._contar()
        self.set_focus(self.texto)

    def _leer(self) -> str:
        b = self.texto.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def _contar(self):
        self.contador.set_text(_("Caracteres restantes: {n}").format(n=LIMITE_MENSAJE - len(self._leer())))

    def aceptar(self):
        texto = self._leer().strip()
        if not texto:
            notificar(_("Escribe el mensaje"))
            return
        if len(texto) > LIMITE_MENSAJE:
            notificar(_("El mensaje supera los {n} caracteres").format(n=LIMITE_MENSAJE))
            return
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo enviar: {e}").format(e=err))
                return
            notificar(_("Mensaje enviado"))
            self.close()
            if self.al_enviar:
                self.al_enviar()
        wsocial_service.enviar_mensaje_async(self.account_id, self.convo_id, texto, callback=adaptar(_fin))


# --------------------------------------------------------------------------- listas de cuentas
class DialogoActoresWSocial(DialogoLista):
    """Seguidores, seguidos, bloqueadas, silenciadas o quienes interactuaron con una publicación.
    Intro abre el perfil de la cuenta."""

    def __init__(self, padre, account_id: int, tipo: str, actor: str = None, uri: str = None):
        titulo, nombre = ACTORES[tipo]
        super().__init__(padre, _(titulo), _(nombre), self._perfil)
        self.account_id, self.tipo, self.actor, self.uri = account_id, tipo, actor, uri
        self.items = []
        self.boton(_("_Perfil"), lambda: self._perfil(self.lista.posicion()))
        self.b_mas = self.boton(_("Cargar _más"), lambda: self._cargar(True), False)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._cargar(False)

    def _cargar(self, mas: bool):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            nuevos = res or []
            self.items = self.items + nuevos if mas else nuevos
            self.lista.poner([f"{a['display_name'] or a['handle']} (@{a['handle']})"
                              + (". " + _("Le sigues") if a.get("following_uri") else "") for a in self.items])
            self.b_mas.set_sensitive(self.tipo not in ("likes", "reposts") and bool(nuevos))
            self.mostrar_estado(_("{n} cuentas").format(n=len(self.items)) if self.items else _("No hay cuentas"))
            self.lista.enfocar()
        if self.tipo in ("likes", "reposts"):
            wsocial_service.interacciones_async(self.account_id, self.uri, self.tipo, callback=adaptar(_fin))
        else:
            wsocial_service.cargar_actores_async(self.account_id, self.tipo, actor=self.actor or "",
                                                 cargar_mas=mas, callback=adaptar(_fin))

    def _perfil(self, pos):
        if 0 <= pos < len(self.items):
            from .dialogos_wsocial import DialogoPerfilWSocial
            DialogoPerfilWSocial(self.padre, self.account_id, self.items[pos]["did"]).present()


# --------------------------------------------------------------------------- informes
class DialogoReportarWSocial(DialogoBase):
    def __init__(self, padre, account_id: int, uri: str = "", cid: str = "", did: str = "", quien: str = ""):
        super().__init__(padre, _("Denunciar {q}").format(q=quien or _("a la cuenta")), _("_Enviar denuncia"))
        self.account_id, self.uri, self.cid, self.did = account_id, uri, cid, did
        self.motivos = [(k, v) for k, v in MOTIVOS_DENUNCIA.items() if not k.endswith("reasonAppeal")]
        self.motivo = self.campo(_("_Motivo:"), Gtk.DropDown.new_from_strings([_(v) for _k, v in self.motivos]))
        self.detalle = self.campo(_("_Detalles (opcional):"), Gtk.Entry(activates_default=True, width_chars=40))
        self.set_focus(self.motivo)

    def aceptar(self):
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo enviar la denuncia: {e}").format(e=err))
                return
            notificar(_("Denuncia enviada"))
            self.close()
        wsocial_service.denunciar_async(self.account_id, self.motivos[self.motivo.get_selected()][0],
                                        self.detalle.get_text().strip(), uri=self.uri, cid=self.cid, did=self.did,
                                        callback=adaptar(_fin))


# --------------------------------------------------------------------------- listas
class DialogoListasWSocial(DialogoLista):
    """Tus listas: abrirlas como buffer, crearlas, borrarlas y gestionar quién está en ellas."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Tus listas de W Social"), _("Lista de tus listas"), self._abrir_pos)
        self.account_id = account_id
        self.items = []
        self.boton(_("_Abrir"), lambda: self._abrir_pos(self.lista.posicion()))
        self.boton(_("_Miembros…"), self._miembros)
        self.boton(_("_Crear lista…"), self._crear)
        self.boton(_("_Borrar lista"), self._borrar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([i["name"] + ("" if i["papel"] == "propia" else
                                           " (" + (_("lista de bloqueo") if i["papel"] == "bloqueo"
                                                   else _("lista de silencio")) + ")") for i in self.items])
            self.mostrar_estado(_("{n} listas").format(n=len(self.items)) if self.items else _("No tienes listas"))
            self.lista.enfocar()
        wsocial_service.cargar_listas_async(self.account_id, callback=adaptar(_fin))

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir_pos(self, pos):
        if not (0 <= pos < len(self.items)):
            return
        it = self.items[pos]
        self.close()
        self.padre._abrir_buffer(self.account_id, f"list:{it['uri']}", _("Lista {t}").format(t=it["name"]),
                                 servicio="wsocial")

    def _miembros(self):
        it = self._actual()
        if it:
            DialogoMiembrosListaWSocial(self, self.account_id, it).present()

    def _crear(self):
        d = DialogoBase(self, _("Crear una lista"), _("_Crear"))
        nombre = d.campo(_("_Nombre:"), Gtk.Entry(activates_default=True, width_chars=36))
        desc = d.campo(_("_Descripción (opcional):"), Gtk.Entry(activates_default=True, width_chars=36))
        d.set_focus(nombre)

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
            wsocial_service.crear_lista_async(self.account_id, nombre.get_text().strip(), desc.get_text().strip(),
                                              callback=adaptar(_fin))
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
            wsocial_service.borrar_lista_async(self.account_id, it["uri"], callback=adaptar(_fin))
        confirmar_async(self, _("¿Borrar la lista «{n}»?").format(n=it["name"]), _hacer, etiqueta_si=_("Borrar"))


class DialogoMiembrosListaWSocial(DialogoLista):
    def __init__(self, padre, account_id: int, lista: dict):
        super().__init__(padre, _("Miembros de {n}").format(n=lista["name"]), _("Lista de miembros"))
        self.account_id, self.uri = account_id, lista["uri"]
        self.items = []
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
            self.lista.poner([f"{m['display_name'] or m['handle']} (@{m['handle']})" for m in self.items])
            self.mostrar_estado(_("{n} miembros").format(n=len(self.items)) if self.items else _("La lista está vacía"))
            self.lista.enfocar()
        wsocial_service.miembros_lista_async(self.account_id, self.uri, callback=adaptar(_fin))

    def _anadir(self):
        def _hacer(texto):
            def _fin(res, err):
                notificar(_("No se pudo añadir: {e}").format(e=err) if err else _("Cuenta añadida"))
                self._refrescar()
            wsocial_service.anadir_miembro_lista_async(self.account_id, self.uri, texto.lstrip("@"),
                                                       callback=adaptar(_fin))
        DialogoTexto(self, _("Añadir a la lista"), _("_Usuario (por ejemplo nombre.wsocial.eu):"), _hacer,
                     aceptar=_("_Añadir")).present()

    def _quitar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        m = self.items[p]

        def _fin(res, err):
            notificar(_("No se pudo quitar: {e}").format(e=err) if err else _("Cuenta quitada"))
            self._refrescar()
        wsocial_service.quitar_miembro_lista_async(self.account_id, m["item_uri"], callback=adaptar(_fin))


# --------------------------------------------------------------------------- contraseñas de aplicación
class DialogoClavesAppWSocial(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Contraseñas de aplicación de W Social"), _("Lista de contraseñas de aplicación"))
        self.account_id = account_id
        self.items = []
        self.boton(_("_Crear contraseña…"), self._crear)
        self.boton(_("_Revocar contraseña"), self._revocar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{c['name']}. " + (_("Con acceso a mensajes directos") if c["privileged"]
                                                  else _("Sin acceso a mensajes directos"))
                              + ". " + _("Creada el {f}").format(f=(c["created_at"] or "")[:10]) for c in self.items])
            self.mostrar_estado(_("{n} contraseñas").format(n=len(self.items)) if self.items
                                else _("No hay contraseñas"))
            self.lista.enfocar()
        wsocial_service.listar_app_passwords_async(self.account_id, callback=adaptar(_fin))

    def _crear(self):
        def _hacer(nombre):
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo crear: {e}").format(e=err))
                    return
                copiar_texto(res["password"])
                notificar(_("Contraseña creada y copiada al portapapeles: {p}. No se volverá a mostrar.")
                          .format(p=res["password"]))
                self._refrescar()
            wsocial_service.crear_app_password_async(self.account_id, nombre, callback=adaptar(_fin))
        DialogoTexto(self, _("Crear una contraseña de aplicación"), _("_Nombre:"), _hacer,
                     _("Para usarla en otro programa. La que se crea aquí no permite mensajes directos."),
                     _("_Crear")).present()

    def _revocar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        c = self.items[p]

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo revocar: {e}").format(e=err) if err else _("Contraseña revocada"))
                self._refrescar()
            wsocial_service.revocar_app_password_async(self.account_id, c["name"], callback=adaptar(_fin))
        confirmar_async(self, _("¿Revocar la contraseña «{n}»? Los programas que la usen dejarán de funcionar.")
                        .format(n=c["name"]), _hacer, etiqueta_si=_("Revocar"))


# --------------------------------------------------------------------------- perfil propio
class DialogoMiPerfilWSocial(DialogoBase):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mi perfil de W Social"), _("_Guardar"))
        self.account_id = account_id
        self.nombre = self.campo(_("_Nombre para mostrar:"), Gtk.Entry(activates_default=True, width_chars=40))
        self.bio = Gtk.TextView(accepts_tab=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6, top_margin=6,
                                bottom_margin=6)
        marco = Gtk.ScrolledWindow(child=self.bio, min_content_height=120, min_content_width=420, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Biografía:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.bio)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo cargar tu perfil: {e}").format(e=err or ""))
                return
            self.nombre.set_text(res["display_name"])
            self.bio.get_buffer().set_text(res["description"])
            self.boton_aceptar.set_sensitive(True)
        from app.core.security import security
        did = security.obtener_credencial(f"wsocial:{account_id}", "did") or ""
        wsocial_service.obtener_perfil_async(account_id, did, callback=adaptar(_fin))
        self.set_focus(self.nombre)

    def aceptar(self):
        b = self.bio.get_buffer()
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo guardar: {e}").format(e=err))
                return
            notificar(_("Perfil guardado"))
            self.close()
        wsocial_service.editar_perfil_async(self.account_id, self.nombre.get_text().strip(),
                                            b.get_text(b.get_start_iter(), b.get_end_iter(), False).strip(),
                                            callback=adaptar(_fin))
