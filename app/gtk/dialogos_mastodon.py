# -*- coding: utf-8 -*-
"""Diálogos de Mastodon que listan cosas: cuentas, borradores, programadas, filtros, tendencias...

Todos comparten `DialogoLista`: una lista con nombre accesible, botones debajo y Escape para cerrar.
Tab salta de la lista a los botones (no recorre las filas); las flechas mueven por las filas."""

import logging
from datetime import datetime, timedelta

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, Pango  # noqa: E402

from app.core.speech import notificar
from app.services.mastodon.mastodon_service import mastodon_service

from .dialogos import DialogoBase, confirmar_async

log = logging.getLogger(__name__)

FORMATO_FECHA = "%Y-%m-%d %H:%M"


class ListaSimple(Gtk.ScrolledWindow):
    """Lista de textos con nombre accesible. Intro (o doble clic) llama a `al_activar(posicion)`."""

    def __init__(self, nombre: str, al_activar=None):
        super().__init__(has_frame=True, vexpand=True, hexpand=True, min_content_height=240, min_content_width=480)
        self.modelo = Gtk.StringList()
        self.seleccion = Gtk.SingleSelection(model=self.modelo)
        fabrica = Gtk.SignalListItemFactory()
        fabrica.connect("setup", self._setup)
        fabrica.connect("bind", self._bind)
        self.vista = Gtk.ListView(model=self.seleccion, factory=fabrica)
        self.vista.update_property([Gtk.AccessibleProperty.LABEL], [nombre])
        if al_activar is not None:
            self.vista.connect("activate", lambda v, pos: al_activar(pos))
        self.set_child(self.vista)
        self.siguiente = None      # widget al que va Tab desde la lista
        self.anterior = None       # y Mayús+Tab
        ctl = Gtk.EventControllerKey()
        ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._on_tecla)
        self.vista.add_controller(ctl)

    @staticmethod
    def _setup(fabrica, item):
        item.set_child(Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                 margin_top=4, margin_bottom=4, margin_start=6, margin_end=6))

    @staticmethod
    def _bind(fabrica, item):
        texto = item.get_item().get_string()
        item.get_child().set_text(texto)
        item.set_accessible_label(texto)

    def _on_tecla(self, ctl, keyval, keycode, estado):
        if keyval == Gdk.KEY_Tab and self.siguiente is not None:
            self.siguiente.grab_focus()
            return True
        if keyval == Gdk.KEY_ISO_Left_Tab and self.anterior is not None:
            self.anterior.grab_focus()
            return True
        return False

    def poner(self, textos):
        self.modelo.splice(0, self.modelo.get_n_items(), list(textos))
        if textos:
            self.seleccion.set_selected(0)

    def posicion(self) -> int:
        p = self.seleccion.get_selected()
        return -1 if p == Gtk.INVALID_LIST_POSITION else p

    def enfocar(self):
        self.vista.grab_focus()
        p = self.posicion()
        if p >= 0:
            self.vista.scroll_to(p, Gtk.ListScrollFlags.FOCUS, None)


class DialogoLista(Gtk.Window):
    def __init__(self, padre, titulo: str, nombre_lista: str, al_activar=None):
        super().__init__(title=titulo, transient_for=padre, modal=True)
        self.padre = padre
        from .mnemonicos import vigilar
        vigilar(self)
        self.set_default_size(620, 460)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        caja.append(self.estado)
        self.lista = ListaSimple(nombre_lista, al_activar)
        caja.append(self.lista)
        self.fila_botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        caja.append(self.fila_botones)
        self.botones = []
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)

    def boton(self, etiqueta: str, fn, sensible: bool = True):
        b = Gtk.Button(label=etiqueta, use_underline=True, sensitive=sensible)
        b.connect("clicked", lambda *_a: fn())
        self.fila_botones.append(b)
        self.botones.append(b)
        if len(self.botones) == 1:
            self.lista.siguiente = b
        return b

    def cerrar_boton(self):
        self.boton(_("_Cerrar"), self.close)
        self.lista.anterior = None

    def mostrar_estado(self, texto: str):
        self.estado.set_text(texto)

    def present(self):
        super().present()
        self.lista.enfocar()


# --------------------------------------------------------------------------- cuentas
TITULOS_CUENTAS = {
    "followers": ("Seguidores", "Lista de seguidores"),
    "following": ("Siguiendo", "Lista de cuentas que sigues"),
    "blocked": ("Cuentas bloqueadas", "Lista de cuentas bloqueadas"),
    "muted": ("Cuentas silenciadas", "Lista de cuentas silenciadas"),
    "solicitudes": ("Solicitudes de seguimiento", "Lista de solicitudes de seguimiento"),
    "reblog": ("Quién ha impulsado la publicación", "Lista de cuentas que impulsaron"),
    "favourite": ("Quién ha marcado la publicación como favorita", "Lista de cuentas que marcaron favorito"),
    "directorio": ("Directorio de cuentas", "Cuentas activas del directorio"),
    "sugerencias": ("Cuentas sugeridas", "Cuentas que te sugiere el servidor"),
}


class DialogoCuentas(DialogoLista):
    """Lista de cuentas (seguidores, bloqueados, quién impulsó...). Intro abre el perfil."""

    def __init__(self, padre, account_id: int, tipo: str, db_id: str = None):
        titulo, nombre = TITULOS_CUENTAS[tipo]
        super().__init__(padre, _(titulo), _(nombre), al_activar=lambda pos: self.ver_perfil())
        self.account_id, self.tipo, self.db_id = account_id, tipo, db_id
        self.cuentas = []
        self.hay_mas = False
        self.boton(_("Ver _perfil"), self.ver_perfil)
        if tipo == "solicitudes":
            self.boton(_("_Aceptar"), lambda: self._responder(True))
            self.boton(_("_Rechazar"), lambda: self._responder(False))
        if tipo == "followers":
            self.boton(_("_Quitar seguidor"), self._quitar_seguidor)
        if tipo == "sugerencias":
            self.boton(_("_Descartar la sugerencia"), self._descartar_sugerencia)
        self.b_mas = self.boton(_("Cargar _más"), lambda: self._cargar(mas=True), sensible=False)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._cargar()

    @staticmethod
    def _texto(c: dict) -> str:
        nombre = c.get("display_name") or c.get("display") or c.get("acct")
        partes = [f"{nombre} (@{c.get('acct', '')})"]
        if c.get("followers") is not None:
            partes.append(_("{n} seguidores").format(n=c.get("followers", 0)))
        bio = (c.get("bio") or "").strip()
        if bio:
            partes.append(" ".join(bio.split())[:140])
        return ". ".join(partes)

    def _cargar(self, mas: bool = False):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            if isinstance(res, dict):
                nuevas, self.hay_mas = res.get("cuentas", []), bool(res.get("has_more"))
            else:
                nuevas, self.hay_mas = res or [], False
                if self.tipo in ("reblog", "favourite", "directorio", "sugerencias"):
                    from app.core.html_utils import html_a_texto
                    for c in nuevas:
                        c["bio"] = html_a_texto(c.get("note") or "")
            self.cuentas = (self.cuentas + nuevas) if mas else list(nuevas)
            actual = self.lista.posicion()
            self.lista.poner([self._texto(c) for c in self.cuentas])
            if mas and actual >= 0:
                self.lista.seleccion.set_selected(actual)
            self.b_mas.set_sensitive(self.hay_mas)
            self.mostrar_estado(_("{n} cuentas").format(n=len(self.cuentas)) if self.cuentas else _("No hay cuentas"))
            self.lista.enfocar()
        if self.tipo == "solicitudes":
            mastodon_service.obtener_solicitudes_seguimiento_async(self.account_id, callback=_fin)
        elif self.tipo == "directorio":
            mastodon_service.cargar_directorio_async(self.account_id, callback=_fin)
        elif self.tipo == "sugerencias":
            mastodon_service.cargar_sugerencias_async(self.account_id, callback=_fin)
        elif self.tipo in ("reblog", "favourite"):
            mastodon_service.obtener_interacciones_async(self.account_id, self.db_id, self.tipo, callback=_fin)
        else:
            mastodon_service.obtener_relaciones_async(self.account_id, self.tipo, cargar_mas=mas, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.cuentas[p] if 0 <= p < len(self.cuentas) else None

    def ver_perfil(self):
        c = self._actual()
        if c:
            from .dialogos import DialogoPerfil
            DialogoPerfil(self, self.account_id, c["acct"]).present()

    def _responder(self, aceptar: bool):
        c = self._actual()
        if not c:
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            notificar(_("Solicitud aceptada") if aceptar else _("Solicitud rechazada"))
            self._cargar()
        mastodon_service.responder_solicitud_seguimiento_async(self.account_id, c["id"], aceptar, callback=_fin)

    def _descartar_sugerencia(self):
        c = self._actual()
        if not c:
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            notificar(_("Sugerencia descartada"))
            self._cargar()
        mastodon_service.descartar_sugerencia_async(self.account_id, c["acct"], callback=_fin)

    def _quitar_seguidor(self):
        c = self._actual()
        if not c:
            return

        def _hacer():
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo: {e}").format(e=err))
                    return
                notificar(_("{u} ya no te sigue").format(u=c["acct"]))
                self._cargar()
            mastodon_service.quitar_seguidor_async(self.account_id, c["acct"], callback=_fin)
        confirmar_async(self, _("¿Quitar a @{u} de tus seguidores?").format(u=c["acct"]), _hacer,
                        etiqueta_si=_("Quitar"))

    def cerrar_y_abrir(self):
        self.close()


# --------------------------------------------------------------------------- borradores y programadas
class DialogoBorradores(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Borradores"), _("Lista de borradores"), al_activar=lambda pos: self._abrir())
        self.account_id = account_id
        self.boton(_("_Abrir"), self._abrir)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        from app.services.mastodon import mastodon_borradores as mb
        self.items = mb.listar_borradores(self.account_id)
        self.lista.poner([f"{(d['texto'] or _('(sin texto)'))[:120]} — {d['actualizado'][:16]}" for d in self.items])
        self.mostrar_estado(_("{n} borradores").format(n=len(self.items)) if self.items else _("No hay borradores"))

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir(self):
        d = self._actual()
        if d:
            self.close()
            self.padre.abrir_borrador(d["id"])

    def _borrar(self):
        d = self._actual()
        if not d:
            return
        from app.services.mastodon import mastodon_borradores as mb
        mb.borrar_borrador(d["id"])
        notificar(_("Borrador borrado"))
        self._refrescar()
        self.lista.enfocar()


class DialogoProgramadas(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Publicaciones programadas"), _("Lista de publicaciones programadas"))
        self.account_id = account_id
        self.items = []
        self.boton(_("_Cambiar la fecha"), self._reprogramar)
        self.boton(_("_Cancelar la publicación"), self._cancelar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    @staticmethod
    def _fecha(texto: str) -> str:
        try:
            d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            return d.astimezone().strftime(FORMATO_FECHA)
        except ValueError:
            return texto[:16]

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{self._fecha(p['scheduled_at'])}: {(p['text'] or '')[:120]}" for p in self.items])
            self.mostrar_estado(_("{n} programadas").format(n=len(self.items)) if self.items else _("No hay publicaciones programadas"))
            self.lista.enfocar()
        mastodon_service.obtener_programados_async(self.account_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _cancelar(self):
        s = self._actual()
        if not s:
            return

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo cancelar: {e}").format(e=err) if err else _("Publicación cancelada"))
                self._refrescar()
            mastodon_service.cancelar_programado_async(self.account_id, s["id"], callback=_fin)
        confirmar_async(self, _("¿Cancelar esta publicación programada?"), _hacer, etiqueta_si=_("Cancelar publicación"))

    def _reprogramar(self):
        s = self._actual()
        if not s:
            return
        from .dialogos import DialogoTexto

        def _nueva(texto):
            try:
                fecha = datetime.strptime(texto.strip(), FORMATO_FECHA).astimezone()
            except ValueError:
                notificar(_("Escribe la fecha como AAAA-MM-DD HH:MM"))
                return
            if fecha <= datetime.now().astimezone() + timedelta(minutes=5):
                notificar(_("La fecha debe ser al menos 5 minutos en el futuro"))
                return

            def _fin(res, err):
                notificar(_("No se pudo cambiar: {e}").format(e=err) if err else _("Fecha cambiada"))
                self._refrescar()
            mastodon_service.reprogramar_programado_async(self.account_id, s["id"], fecha, callback=_fin)
        DialogoTexto(self, _("Cambiar la fecha"), _("_Nueva fecha (AAAA-MM-DD HH:MM):"), _nueva,
                     _("Hora local, al menos cinco minutos en el futuro."), _("_Cambiar")).present()


# --------------------------------------------------------------------------- historial de ediciones
class DialogoHistorial(Gtk.Window):
    def __init__(self, padre, versiones):
        super().__init__(title=_("Historial de ediciones"), transient_for=padre, modal=True)
        self.set_default_size(620, 460)
        tv = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD,
                          left_margin=10, right_margin=10, top_margin=8, bottom_margin=8)
        tv.update_property([Gtk.AccessibleProperty.LABEL], [_("Versiones de la publicación")])
        trozos = []
        for i, v in enumerate(versiones, 1):
            cab = _("Versión {i} de {n} — {f}").format(i=i, n=len(versiones), f=v["created_at"][:16])
            cw = (_("Aviso de contenido: {t}").format(t=v["spoiler"]) + "\n") if v.get("spoiler") else ""
            trozos.append(f"{cab}\n{cw}{v['content']}")
        tv.get_buffer().set_text("\n\n".join(trozos) or _("Sin ediciones"))
        self.set_child(Gtk.ScrolledWindow(child=tv))
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.set_focus(tv)


# --------------------------------------------------------------------------- filtros del servidor
CONTEXTOS_FILTRO = [("home", "Inicio"), ("public", "Públicas"), ("notifications", "Notificaciones"),
                    ("thread", "Conversaciones"), ("account", "Perfiles")]
ACCIONES_FILTRO = [("warn", "Avisar y ocultar tras un aviso"), ("hide", "Ocultar del todo")]


class DialogoFiltros(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Filtros del servidor"), _("Lista de filtros"))
        self.account_id = account_id
        self.items = []
        self.boton(_("_Añadir filtro…"), self._anadir)
        self.boton(_("_Borrar filtro"), self._borrar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            etiquetas = {c: _(n) for c, n in CONTEXTOS_FILTRO}
            textos = []
            for f in self.items:
                palabras = f.get("phrase") or f.get("title") or _("(sin palabras)")
                ctx = ", ".join(etiquetas.get(c, c) for c in f.get("context", []))
                accion = _("oculta") if f.get("filter_action") == "hide" else _("avisa")
                textos.append(f"{palabras}. {accion}. {ctx}")
            self.lista.poner(textos)
            self.mostrar_estado(_("{n} filtros").format(n=len(self.items)) if self.items else _("No hay filtros"))
            self.lista.enfocar()
        mastodon_service.obtener_filtros_servidor_async(self.account_id, callback=_fin)

    def _borrar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        f = self.items[p]

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo borrar: {e}").format(e=err) if err else _("Filtro borrado"))
                self._refrescar()
            mastodon_service.borrar_filtro_servidor_async(self.account_id, f["id"], es_v2=f.get("v2"), callback=_fin)
        confirmar_async(self, _("¿Borrar el filtro «{p}»?").format(p=f.get("phrase") or f.get("title")), _hacer,
                        etiqueta_si=_("Borrar"))

    def _anadir(self):
        d = DialogoBase(self, _("Nuevo filtro"), _("_Crear filtro"))
        palabras = d.campo(_("_Palabras (separadas por comas):"), Gtk.Entry(activates_default=True, width_chars=40),
                           _("Se ocultarán o avisarán las publicaciones que las contengan."))
        accion = d.campo(_("_Qué hacer:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in ACCIONES_FILTRO]))
        checks = []
        for c, n in CONTEXTOS_FILTRO:
            sw = d.campo(_(n) + ":", Gtk.Switch(halign=Gtk.Align.START))
            sw.set_active(c in ("home", "notifications"))
            checks.append((c, sw))
        d.set_focus(palabras)

        def _crear():
            lista = [p.strip() for p in palabras.get_text().split(",") if p.strip()]
            contextos = [c for c, sw in checks if sw.get_active()]
            if not lista or not contextos:
                notificar(_("Escribe al menos una palabra y elige un contexto"))
                return

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo crear: {e}").format(e=err))
                    return
                notificar(_("Filtro creado"))
                d.close()
                self._refrescar()
            mastodon_service.crear_filtro_servidor_async(
                self.account_id, lista[0], contextos, title=lista[0], keywords=lista,
                filter_action=ACCIONES_FILTRO[accion.get_selected()][0], callback=_fin)
        d.aceptar = _crear
        d.present()


# --------------------------------------------------------------------------- tendencias y hashtags
class DialogoHashtags(DialogoLista):
    """Hashtags en tendencia o hashtags que sigues. Intro abre el elegido como buffer."""

    def __init__(self, padre, account_id: int, tipo: str):
        titulo = _("Hashtags en tendencia") if tipo == "tendencia" else _("Hashtags que sigues")
        super().__init__(padre, titulo, titulo, al_activar=lambda pos: self._abrir())
        self.account_id, self.tipo, self.items = account_id, tipo, []
        self.boton(_("_Abrir"), self._abrir)
        if tipo == "seguidos":
            self.boton(_("_Dejar de seguir"), self._dejar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"#{t['name']}" + (_(". {n} usos recientes").format(n=t["usos"]) if t.get("usos") else "")
                              for t in self.items])
            self.mostrar_estado(_("{n} hashtags").format(n=len(self.items)) if self.items else _("No hay hashtags"))
            self.lista.enfocar()
        if self.tipo == "tendencia":
            mastodon_service.cargar_trending_tags_async(self.account_id, callback=_fin)
        else:
            mastodon_service.obtener_hashtags_seguidos_async(self.account_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir(self):
        t = self._actual()
        if t:
            self.close()
            self.padre.abrir_hashtag(self.account_id, t["name"])

    def _dejar(self):
        t = self._actual()
        if not t:
            return

        def _fin(res, err):
            notificar(_("No se pudo: {e}").format(e=err) if err else _("Has dejado de seguir #{t}").format(t=t["name"]))
            self._cargar()
        mastodon_service.seguir_hashtag_async(self.account_id, t["name"], seguir=False, callback=_fin)


# --------------------------------------------------------------------------- perfil propio
class DialogoMiPerfil(DialogoBase):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mi perfil"), _("_Guardar"))
        self.account_id = account_id
        self.nombre = self.campo(_("_Nombre para mostrar:"), Gtk.Entry(activates_default=True, width_chars=40))
        self.bio = Gtk.TextView(accepts_tab=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        marco = Gtk.ScrolledWindow(child=self.bio, min_content_height=120, min_content_width=420, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Biografía:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.bio)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.privada = self.campo(_("Cuenta _privada (apruebo a quien me sigue):"), Gtk.Switch(halign=Gtk.Align.START))
        self.bot = self.campo(_("Cuenta _automatizada (bot):"), Gtk.Switch(halign=Gtk.Align.START))
        self.boton_aceptar.set_sensitive(False)
        self.datos = {}

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo cargar tu perfil: {e}").format(e=err))
                return
            self.datos = res
            self.nombre.set_text(res["display_name"])
            self.bio.get_buffer().set_text(res["note"])
            self.privada.set_active(res["locked"])
            self.bot.set_active(res["bot"])
            self.boton_aceptar.set_sensitive(True)
        mastodon_service.obtener_mi_perfil_async(account_id, callback=_fin)
        self.set_focus(self.nombre)

    def aceptar(self):
        b = self.bio.get_buffer()

        def _fin(res, err):
            if err:
                notificar(_("No se pudo guardar: {e}").format(e=err))
                return
            notificar(_("Perfil guardado"))
            self.close()
        mastodon_service.editar_mi_perfil_async(
            self.account_id, self.nombre.get_text().strip(), b.get_text(b.get_start_iter(), b.get_end_iter(), False),
            locked=self.privada.get_active(), bot=self.bot.get_active(), callback=_fin)
