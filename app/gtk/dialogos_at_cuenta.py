# -*- coding: utf-8 -*-
"""Bluesky y W Social: cuentas sugeridas, cambio de usuario, vista de las cronologías, hilos y publicaciones ocultas
(Bluesky) y estado de la cuenta, servidor, verificación de edad, estado temporal, privacidad del chat, copia del
repositorio, feeds populares y búsqueda con filtros (W Social)."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from app.core.privacy import abrir_url
from app.core.speech import notificar
from app.services.bluesky.bluesky_service import bluesky_service
from app.services.wsocial.wsocial_service import wsocial_service

from .datos_wsocial import adaptar
from .dialogos import DialogoBase, confirmar_async
from .dialogos_at_extra import WS, RedAT, _pedir
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube_instancia import texto_en_dialogo


def _error(e) -> str:
    return _("No se pudo: {e}").format(e=e)


# --------------------------------------------------------------------------- compartido
class DialogoSugerenciasAT(DialogoLista):
    """Cuentas que la red sugiere seguir. Intro abre el perfil."""

    def __init__(self, padre, red: RedAT, account_id: int):
        super().__init__(padre, _("Cuentas sugeridas en {r}").format(r=red.nombre), _("Cuentas sugeridas"),
                         lambda pos: self._perfil(pos))
        self.red, self.account_id, self.items = red, account_id, []
        self.boton(_("_Perfil"), lambda: self._perfil(self.lista.posicion()))
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando las sugerencias…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{a.get('display_name') or a.get('handle')} (@{a.get('handle')})"
                              + (f". {a['description']}" if a.get("description") else "") for a in self.items])
            self.mostrar_estado(_("{n} cuentas").format(n=len(self.items)) if self.items else _("No hay sugerencias."))
            self.lista.enfocar()
        if red.clave == "bluesky":
            bluesky_service.obtener_sugerencias_async(account_id, callback=_fin)
        else:
            wsocial_service.sugerencias_cuentas_async(account_id, callback=adaptar(_fin))

    def _perfil(self, pos):
        if 0 <= pos < len(self.items):
            self.red.abrir_perfil(self.padre, self.account_id, self.items[pos].get("did") or self.items[pos].get("handle"))


def cambiar_usuario(padre, red: RedAT, acc: int):
    def _nuevo(handle):
        def _seguro():
            def _fin(res, err):
                notificar(_error(err) if err else _("Tu nombre de usuario ahora es @{h}.").format(h=res or handle))
            red.servicio.cambiar_handle_async(acc, handle, callback=red.cb(_fin))
        confirmar_async(padre, _("¿Cambiar tu nombre de usuario a @{h}? Quien te mencione con el anterior dejará de encontrarte.")
                        .format(h=handle.lstrip("@")), _seguro, etiqueta_si=_("Cambiar"))
    _pedir(padre, _("Cambiar el nombre de usuario"), _("_Nuevo nombre (por ejemplo nombre.bsky.social):"), _nuevo, _("_Cambiar"))


# --------------------------------------------------------------------------- Bluesky
class DialogoVistaBsky(DialogoBase):
    """Cómo se ven tu cronología de inicio y los hilos."""

    HILOS = (("oldest", "Más antiguas primero"), ("newest", "Más recientes primero"), ("most-likes", "Más gustadas primero"),
             ("random", "Al azar"), ("hotness", "Más populares ahora"))

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Vista de las cronologías"), _("_Guardar"))
        self.account_id = account_id
        self.c_resp = self.campo("", Gtk.CheckButton(label=_("Ocultar las _respuestas en «Siguiendo»")))
        self.c_nosig = self.campo("", Gtk.CheckButton(label=_("Ocultar las respuestas a cuentas que _no sigo"), active=True))
        self.c_rep = self.campo("", Gtk.CheckButton(label=_("Ocultar los re_posts")))
        self.c_cit = self.campo("", Gtk.CheckButton(label=_("Ocultar las _citas")))
        self.likes = self.campo(_("Ocultar respuestas con menos de _me gusta (0 = ninguna):"),
                                Gtk.SpinButton.new_with_range(0, 1000, 1))
        self.hilo = self.campo(_("_Orden de las respuestas en un hilo:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in self.HILOS]))
        self.set_focus(self.c_resp)

    def aceptar(self):
        valores = {"hideReplies": self.c_resp.get_active(), "hideRepliesByUnfollowed": self.c_nosig.get_active(),
                   "hideRepliesByLikeCount": int(self.likes.get_value()), "hideReposts": self.c_rep.get_active(),
                   "hideQuotePosts": self.c_cit.get_active()}
        orden = self.HILOS[self.hilo.get_selected()][0]
        self.close()
        acc = self.account_id

        def _hilo(res, err):
            notificar(_error(err) if err else _("Vista guardada."))
        bluesky_service.fijar_feed_view_async(acc, valores, callback=lambda r, e: (
            notificar(_error(e)) if e else bluesky_service.fijar_thread_view_async(acc, orden, callback=_hilo)))


def ocultos_bsky(padre, red: RedAT, acc: int):
    hilos, posts = bluesky_service.hilos_silenciados(acc), bluesky_service.publicaciones_ocultas(acc)
    partes = [_("Hilos silenciados: {n}").format(n=len(hilos))] + hilos + ["", _("Publicaciones ocultas: {n}").format(n=len(posts))] + posts
    texto_en_dialogo(padre, _("Hilos silenciados y publicaciones ocultas"), "\n".join(partes))


# --------------------------------------------------------------------------- W Social
def estado_cuenta(padre, red: RedAT, acc: int):
    notificar(_("Consultando el estado de la cuenta…"))

    def _fin(r, err):
        if err:
            return notificar(_error(err))
        si, no = _("sí"), _("no")
        lineas = [_("Usuario: @{h}").format(h=r.get("handle", "")), _("Identificador: {d}").format(d=r.get("did", "")),
                  _("Servidor: {p}").format(p=r.get("pds", "")),
                  _("Cuenta activada: {v}").format(v=si if r.get("activada") else no),
                  _("Repositorio válido: {v}").format(v=si if r.get("repo_confirmado") else no),
                  _("Revisión del repositorio: {v}").format(v=r.get("registros", "")),
                  _("Archivos importados: {a} de {b}").format(a=r.get("blobs_importados", 0), b=r.get("blobs_esperados", 0)),
                  _("Correo: {c} ({v})").format(c=r.get("correo", "") or _("sin correo"),
                                                v=_("confirmado") if r.get("correo_confirmado") else _("sin confirmar"))]
        texto_en_dialogo(padre, _("Estado de la cuenta"), "\n".join(lineas))
    wsocial_service.estado_cuenta_async(acc, callback=adaptar(_fin))


def info_servidor(padre, red: RedAT, acc: int):
    notificar(_("Consultando el servidor…"))

    def _fin(r, err):
        if err:
            return notificar(_error(err))
        si, no = _("sí"), _("no")
        lineas = [_("Servidor: {p}").format(p=r.get("pds", "")), _("Identificador: {d}").format(d=r.get("did", "")),
                  _("Dominios disponibles: {d}").format(d=", ".join(r.get("dominios") or []) or "-"),
                  _("Exige invitación: {v}").format(v=si if r.get("requiere_invitacion") else no),
                  _("Exige verificar el teléfono: {v}").format(v=si if r.get("requiere_verificacion_telefono") else no),
                  _("Política de privacidad: {u}").format(u=r.get("privacidad") or "-"),
                  _("Condiciones del servicio: {u}").format(u=r.get("condiciones") or "-"),
                  _("Contacto: {c}").format(c=r.get("contacto") or "-")]
        texto_en_dialogo(padre, _("Información del servidor"), "\n".join(lineas))
    wsocial_service.describir_servidor_async(acc, callback=adaptar(_fin))


def verificacion_edad(padre, red: RedAT, acc: int):
    notificar(_("Consultando la verificación de edad…"))

    def _fin(r, err):
        if err:
            return notificar(_error(err))
        d = DialogoBase(padre, _("Verificación de edad"), _("_Verificar en el navegador"))
        estado = r.get("estado") or _("sin datos")
        exige = _("sí") if r.get("requerida") else _("no")
        d.rejilla.attach(Gtk.Label(label=_("Estado: {e}. ¿Exigida en tu país ({p})?: {x}. La verificación se completa en el "
                                           "navegador; esta aplicación no ve tus documentos.")
                                   .format(e=estado, p=r.get("pais") or "-", x=exige), wrap=True, max_width_chars=60, xalign=0, selectable=True), 0, 0, 2, 1)
        correo = d.campo(_("_Correo (opcional):"), Gtk.Entry(activates_default=True, width_chars=36))

        def _ok():
            d.close()

            def _url(res, e):
                if e:
                    return notificar(_error(e))
                if res.get("url"):
                    abrir_url(res["url"])
                    notificar(_("Continúa la verificación en el navegador."))
                else:
                    notificar(_("El servidor no ha devuelto ninguna dirección."))
            wsocial_service.iniciar_verificacion_edad_async(acc, correo.get_text().strip(), callback=adaptar(_url))
        d.aceptar = _ok
        d.present()
    wsocial_service.estado_verificacion_edad_async(acc, callback=adaptar(_fin))


def estado_temporal(padre, red: RedAT, acc: int):
    d = DialogoBase(padre, _("Estado temporal"), _("_Publicar"))
    texto = d.campo(_("_Estado (vacío para retirarlo):"), Gtk.Entry(activates_default=True, width_chars=36))
    minutos = d.campo(_("_Minutos que se muestra:"), Gtk.SpinButton.new_with_range(1, 1440, 5))
    minutos.set_value(60)
    d.set_focus(texto)

    def _ok():
        t, m = texto.get_text().strip(), int(minutos.get_value())
        d.close()
        wsocial_service.fijar_estado_async(acc, t, m, callback=adaptar(lambda r, e: notificar(
            _error(e) if e else (_("Estado publicado.") if t else _("Estado retirado.")))))
    d.aceptar = _ok
    d.present()


def privacidad_chat(padre, red: RedAT, acc: int):
    opciones = (("all", _("Cualquiera")), ("following", _("Solo las cuentas que sigo")), ("none", _("Nadie")))
    d = DialogoBase(padre, _("Quién puede escribirme"), _("_Guardar"))
    combo = d.campo(_("_Pueden escribirme mensajes privados:"), Gtk.DropDown.new_from_strings([n for _c, n in opciones]))

    def _actual(r, err):
        if not err and r in dict(opciones):
            combo.set_selected([c for c, _n in opciones].index(r))
    wsocial_service.preferencias_chat_async(acc, callback=adaptar(_actual))

    def _ok():
        valor = opciones[combo.get_selected()][0]
        d.close()
        wsocial_service.preferencias_chat_async(acc, valor, callback=adaptar(lambda r, e: notificar(
            _error(e) if e else _("Preferencia guardada."))))
    d.aceptar = _ok
    d.present()


def exportar_repositorio(padre, red: RedAT, acc: int):
    dlg = Gtk.FileDialog(title=_("Guardar la copia de tu cuenta"), initial_name="wsocial-repositorio.car")

    def _elegido(origen, res):
        try:
            ruta = origen.save_finish(res).get_path()
        except GLib.Error:
            return
        notificar(_("Descargando tu repositorio…"))
        wsocial_service.exportar_repositorio_async(acc, ruta, callback=adaptar(lambda r, e: notificar(
            _error(e) if e else _("Copia guardada en {r} ({k} KB).").format(r=r["ruta"], k=max(1, r["bytes"] // 1024)))))
    dlg.save(padre, None, _elegido)


class DialogoFeedsPopularesWS(DialogoLista):
    """Feeds populares; se puede buscar por texto."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Feeds populares"), _("Feeds"), lambda pos: self._abrir(pos))
        self.account_id, self.items = account_id, []
        self.boton(_("_Abrir el feed"), lambda: self._abrir(self.lista.posicion()))
        self.boton(_("_Buscar…"), self._buscar)
        self.cerrar_boton()
        self._cargar("")

    def _buscar(self):
        _pedir(self, _("Buscar feeds"), _("_Buscar:"), self._cargar, _("_Buscar"))

    def _cargar(self, texto):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                return self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
            self.items = res or []
            self.lista.poner([f"{f['name']} (@{f['creator']}), {f['likes']} " + _("me gusta")
                              + (f". {f['description']}" if f["description"] else "") for f in self.items])
            self.mostrar_estado(_("{n} feeds").format(n=len(self.items)) if self.items else _("No se encontró ningún feed."))
            self.lista.enfocar()
        wsocial_service.feeds_populares_async(self.account_id, texto, callback=adaptar(_fin))

    def _abrir(self, pos):
        if 0 <= pos < len(self.items):
            v = self.padre
            v._abrir_buffer(self.account_id, f"feed:{self.items[pos]['uri']}", _("Feed {t}").format(t=self.items[pos]["name"]), servicio="wsocial")
            self.close()


class DialogoBusquedaWS(DialogoBase):
    """Búsqueda de publicaciones con filtros: autor, fechas, idioma y orden."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Buscar con filtros"), _("_Buscar"))
        self.account_id = account_id
        self.texto = self.campo(_("_Texto:"), Gtk.Entry(activates_default=True, width_chars=40))
        self.autor = self.campo(_("_Autor (opcional):"), Gtk.Entry(activates_default=True))
        self.desde = self.campo(_("_Desde (AAAA-MM-DD, opcional):"), Gtk.Entry(activates_default=True))
        self.hasta = self.campo(_("_Hasta (AAAA-MM-DD, opcional):"), Gtk.Entry(activates_default=True))
        self.idioma = self.campo(_("_Idioma (por ejemplo es, opcional):"), Gtk.Entry(activates_default=True))
        self.orden = self.campo(_("_Ordenar por:"), Gtk.DropDown.new_from_strings([_("Más recientes"), _("Más populares")]))
        self.set_focus(self.texto)

    def aceptar(self):
        t = self.texto.get_text().strip()
        if not t:
            return
        args = dict(texto=t, orden=("latest", "top")[self.orden.get_selected()], autor=self.autor.get_text().strip(),
                    desde=self.desde.get_text().strip(), hasta=self.hasta.get_text().strip(), idioma=self.idioma.get_text().strip())
        for k in ("desde", "hasta"):        # el servicio espera una fecha y hora completa
            if args[k] and "T" not in args[k]:
                args[k] += "T00:00:00Z"
        self.close()
        ResultadosBusquedaWS(self.padre, self.account_id, args).present()


class ResultadosBusquedaWS(DialogoLista):
    def __init__(self, padre, account_id: int, args: dict):
        super().__init__(padre, _("Resultados: {t}").format(t=args["texto"]), _("Publicaciones encontradas"))
        self.items = []
        self.boton(_("_Perfil del autor"), self._perfil)
        self.cerrar_boton()
        self.account_id = account_id
        self.mostrar_estado(_("Buscando…"))

        def _fin(res, err):
            if err:
                return self.mostrar_estado(_("No se pudo buscar: {e}").format(e=err))
            self.items = res or []
            self.lista.poner([f"{p.author_name or p.author_handle} (@{p.author_handle}): " + " ".join((p.content or "").split())
                              for p in self.items])
            self.mostrar_estado(_("{n} publicaciones").format(n=len(self.items)) if self.items
                                else _("No se encontró ninguna publicación."))
            self.lista.enfocar()
        wsocial_service.buscar_publicaciones_async(account_id, args["texto"], args["orden"], args["autor"], args["desde"],
                                                   args["hasta"], args["idioma"], callback=adaptar(_fin))

    def _perfil(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            WS.abrir_perfil(self.padre, self.account_id, self.items[p].author_did)


# nombre -> función(padre, red, cuenta) que abre cada pantalla
def sugerencias(padre, red, acc):
    DialogoSugerenciasAT(padre, red, acc).present()


def vista_bsky(padre, red, acc):
    DialogoVistaBsky(padre, acc).present()


def feeds_populares(padre, red, acc):
    DialogoFeedsPopularesWS(padre, acc).present()


def busqueda_filtros(padre, red, acc):
    DialogoBusquedaWS(padre, acc).present()
