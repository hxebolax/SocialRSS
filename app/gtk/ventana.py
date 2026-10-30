# -*- coding: utf-8 -*-
"""
Ventana principal GTK 4: tres paneles (navegación, artículos, lectura) pensados para Orca.

Decisiones de accesibilidad:
  * Cada panel tiene un nombre accesible y se recorre con Tab / F6.
  * Cada fila de la lista lleva una descripción completa (estado, título, feed, autor, fecha) como
    etiqueta accesible, de modo que Orca la lee entera al moverse con las flechas.
  * Los cambios de estado (leído, favorito, actualizaciones, reproductor) se anuncian con
    `Gtk.Accessible.announce` y no mueven el foco; las listas se actualizan en su sitio.
  * El texto del artículo está en un `Gtk.TextView` de solo lectura con cursor, para leerlo con flechas.
"""

import logging
import webbrowser
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, Gio, GLib, Pango  # noqa: E402

from app.core.config_manager import config
from app.core.speech import speak
from app.services.rss.rss_service import rss_service
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.wsocial.wsocial_service import wsocial_service

from app.core.event_bus import event_bus
from app.core.plataforma import llamar_en_ui

from . import (datos, datos_bluesky, datos_wsocial, datos_irc, datos_mastodon, datos_matrix, datos_peertube, datos_reddit, datos_telegram, datos_x,
               datos_xmpp)
from .ventana_bluesky import _BlueskyMixin
from .ventana_wsocial import _WSocialMixin
from .ventana_voz import _VozMixin
from .ventana_herramientas import _HerramientasMixin
from .ventana_botonera import _BotoneraMixin
from .orden_arbol import _OrdenMixin, aplicar_orden
from .ventana_bandeja import _BandejaMixin
from .ventana_rss import _RSSMixin
from .ventana_peertube import _PeerTubeMixin
from .ventana_reddit import _RedditMixin
from .ventana_x import _TwitterMixin
from .ventana_irc import _IRCMixin
from .ventana_telegram import _TelegramMixin
from .ventana_matrix import _MatrixMixin
from .ventana_xmpp import _XMPPMixin
from .ventana_mastodon_extra import _MastodonExtraMixin
from .ventana_at import _ATMixin
from .datos import Nodo
from .modelos import NodoObj, FilaObj

log = logging.getLogger(__name__)

# Espera, en milisegundos, desde que el usuario se detiene en un nodo (o en una fila) hasta que se trabaja:
# al recorrer el árbol o la lista con las flechas no se lee ni se descarga nada en cada paso.
PAUSA_LISTA_MS = 120
PAUSA_TEXTO_MS = 150
PAUSA_RED_MS = 700
LISTA_DIRECTA, LISTA_VENTANA, LISTA_LOTE = 50, 20, 30     # filas que se ponen de golpe, a cada lado de la selección, y por tanda

# Intervalo del sondeo automático de feeds (los que ya vencieron según su propio intervalo).
SONDEO_SEGUNDOS = 300


def _etiquetar(widget, nombre: str, descripcion: str = ""):
    widget.update_property([Gtk.AccessibleProperty.LABEL], [nombre])
    if descripcion:
        widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [descripcion])


def _clave_fila(f) -> str:
    """Identidad de una fila en la lista: en la bandeja los ids de redes distintas pueden coincidir."""
    return f.extra.get("clave_lista") or f.id


class VentanaPrincipal(_ATMixin, _MastodonExtraMixin, _BotoneraMixin, _OrdenMixin, _BandejaMixin, _RSSMixin, _HerramientasMixin, _BlueskyMixin, _WSocialMixin, _TwitterMixin, _RedditMixin, _PeerTubeMixin, _VozMixin, _TelegramMixin, _IRCMixin, _XMPPMixin, _MatrixMixin, Gtk.ApplicationWindow):
    def __init__(self, app, account_id: int):
        super().__init__(application=app, title="SocialRSS Accessible Suite")
        self.account_id = account_id
        self.nodo_actual = None
        self.busqueda = ""
        self._articulo_abierto = None
        self._tick_reproductor = 0
        self._actualizando = False
        self._cargados = set()      # buffers de Mastodon ya descargados en esta sesión
        self._id_pendiente = None   # fila a enfocar en la próxima carga de lista (ir al origen desde la bandeja)
        self._nodo_listado = None   # nodo cuya lista está en pantalla (para actualizar sin reconstruir)
        self._eventos_pendientes = {}
        self._encuestas = {}        # db id del toot -> última encuesta descargada
        self._tiempo_real = None
        self._tiempo_real_bsky = None
        self._ws_vigiladas = set()
        # Todo lo que lee la base de datos para pintar (listas y texto) va a un hilo propio: el hilo de la
        # interfaz solo aplica resultados. Cada petición lleva un número de generación; si el usuario ya
        # se movió a otra cosa, el resultado viejo se descarta.
        self._lector = ThreadPoolExecutor(max_workers=1, thread_name_prefix="LectorUI")
        self._relleno_id, self._relleno_despues = 0, None
        self._gen_lista = 0
        self._gen_texto = 0
        self._timer_lista = 0
        self._timer_red = 0
        self._timer_texto = 0
        self.set_default_size(1180, 760)

        self._iniciar_complementos()
        self._construir_interfaz()
        self._configurar_tabulador()
        self._configurar_orden_arbol()
        self._configurar_menu()
        self._registrar_acciones()
        self.cargar_arbol()
        self.connect("close-request", self._al_cerrar)
        self.iniciar_tiempo_real()
        GLib.idle_add(self._avisar_complementos_arranque)
        GLib.timeout_add_seconds(20, self._mantenimiento_datos)
        GLib.timeout_add_seconds(SONDEO_SEGUNDOS, self._sondeo_automatico)
        GLib.timeout_add_seconds(1, self._latido_reproductor)

    # ------------------------------------------------------------------ construcción
    def _construir_interfaz(self):
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(raiz)

        # Barra de búsqueda
        barra = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        barra.set_margin_start(6)
        barra.set_margin_end(6)
        barra.set_margin_top(4)
        barra.set_margin_bottom(4)
        etiqueta = Gtk.Label(label=_("_Buscar:"), use_underline=True)
        self.entrada_busqueda = Gtk.SearchEntry(hexpand=True)
        self.entrada_busqueda.set_placeholder_text(_("Buscar en los artículos del panel actual"))
        etiqueta.set_mnemonic_widget(self.entrada_busqueda)
        _etiquetar(self.entrada_busqueda, _("Buscar en los artículos"),
                   _("Escribe y pulsa Intro. Escape borra la búsqueda."))
        self.entrada_busqueda.connect("activate", self._on_buscar)
        self.entrada_busqueda.connect("stop-search", self._on_limpiar_busqueda)
        barra.append(etiqueta)
        barra.append(self.entrada_busqueda)
        raiz.append(barra)

        # Paneles
        self.paned_horizontal = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        self.paned_horizontal.set_position(260)
        self.paned_horizontal.set_shrink_start_child(False)
        self.paned_horizontal.set_resize_start_child(False)
        raiz.append(self.paned_horizontal)

        self.paned_vertical = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.paned_vertical.set_position(330)
        self.paned_horizontal.set_start_child(self._crear_arbol())
        self.paned_horizontal.set_end_child(self.paned_vertical)
        lista = self._crear_lista()
        lista.set_vexpand(True)
        caja_lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja_lista.append(lista)
        caja_lista.append(self._crear_botonera())
        self.paned_vertical.set_start_child(caja_lista)
        self.paned_vertical.set_end_child(self._crear_lectura())

        # Barra de estado (rol "status": el lector la anuncia al cambiar)
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS, ellipsize=Pango.EllipsizeMode.END)
        self.estado.set_margin_start(6)
        self.estado.set_margin_end(6)
        self.estado.set_margin_top(3)
        self.estado.set_margin_bottom(3)
        raiz.append(self.estado)

    def _crear_arbol(self):
        self.raiz_arbol = Gio.ListStore.new(NodoObj)
        self.modelo_arbol = Gtk.TreeListModel.new(self.raiz_arbol, False, False, self._hijos_arbol)
        self.seleccion_arbol = Gtk.SingleSelection(model=self.modelo_arbol)
        self.seleccion_arbol.connect("notify::selected", self._on_nodo_seleccionado)

        fabrica = Gtk.SignalListItemFactory()
        fabrica.connect("setup", self._arbol_setup)
        fabrica.connect("bind", self._arbol_bind)
        fabrica.connect("unbind", self._arbol_unbind)
        self.vista_arbol = Gtk.ListView(model=self.seleccion_arbol, factory=fabrica)
        self.vista_arbol.set_name("arbol")
        _etiquetar(self.vista_arbol, _("Navegación"), _("Árbol de carpetas y feeds. Flecha derecha expande."))
        ctl = Gtk.ShortcutController()
        ctl.set_scope(Gtk.ShortcutScope.LOCAL)
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Menu"),
                                          Gtk.CallbackAction.new(lambda *_a: self.mostrar_menu_arbol() or True)))
        self.vista_arbol.add_controller(ctl)
        clic = Gtk.GestureClick(button=3)
        clic.connect("pressed", lambda g, n, x, y: self.mostrar_menu_arbol(x, y))
        self.vista_arbol.add_controller(clic)
        marco = Gtk.ScrolledWindow(child=self.vista_arbol, min_content_width=160)
        return marco

    def _hijos_arbol(self, obj):
        return obj.almacen_hijos()

    def _arbol_setup(self, fabrica, item):
        exp = Gtk.TreeExpander()
        etiqueta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        exp.set_child(etiqueta)
        item.set_child(exp)

    def _arbol_bind(self, fabrica, item):
        fila = item.get_item()
        exp = item.get_child()
        exp.set_list_row(fila)
        obj = fila.get_item()
        etiqueta = exp.get_child()
        etiqueta.set_text(obj.etiqueta)
        item.set_accessible_label(self._texto_nodo(obj))

        def _cambio(o, _p, item=item, etiqueta=etiqueta):
            etiqueta.set_text(o.etiqueta)
            item.set_accessible_label(self._texto_nodo(o))
        item._id_notify = obj.connect("notify::etiqueta", _cambio)
        item._obj = obj

    def _arbol_unbind(self, fabrica, item):
        obj = getattr(item, "_obj", None)
        if obj is not None and hasattr(item, "_id_notify"):
            obj.disconnect(item._id_notify)
        item._obj = None

    @staticmethod
    def _texto_nodo(obj) -> str:
        n = obj.nodo
        base = n.titulo
        if n.no_leidos:
            base += ". " + _("{n} sin leer").format(n=n.no_leidos)
        if n.error:
            base += ". " + _("con errores")
        if n.tipo == "category":
            base += ". " + _("carpeta")
        return base

    def _crear_lista(self):
        self.almacen_lista = Gio.ListStore.new(FilaObj)
        self.seleccion_lista = Gtk.SingleSelection(model=self.almacen_lista)
        self.seleccion_lista.connect("notify::selected", self._on_fila_seleccionada)

        fabrica = Gtk.SignalListItemFactory()
        fabrica.connect("setup", self._lista_setup)
        fabrica.connect("bind", self._lista_bind)
        fabrica.connect("unbind", self._lista_unbind)
        self.vista_lista = Gtk.ListView(model=self.seleccion_lista, factory=fabrica)
        self.vista_lista.set_name("lista")
        self.vista_lista.set_single_click_activate(False)
        self.vista_lista.connect("activate", self._on_abrir_fila)
        _etiquetar(self.vista_lista, _("Artículos"),
                   _("Intro abre el artículo. Control M marca leído, Control D favorito, "
                     "Control L leer más tarde, Suprimir a la papelera."))

        ctl = Gtk.ShortcutController()
        ctl.set_scope(Gtk.ShortcutScope.LOCAL)
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Delete"),
                                          Gtk.CallbackAction.new(lambda *_a: self.accion_papelera() or True)))
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Menu"),
                                          Gtk.CallbackAction.new(lambda *_a: self.mostrar_menu_contextual() or True)))
        self.vista_lista.add_controller(ctl)
        clic = Gtk.GestureClick(button=3)
        clic.connect("pressed", lambda g, n, x, y: self.mostrar_menu_contextual(x, y))
        self.vista_lista.add_controller(clic)
        return Gtk.ScrolledWindow(child=self.vista_lista, min_content_height=120)

    def _lista_setup(self, fabrica, item):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        caja.set_margin_top(3)
        caja.set_margin_bottom(3)
        caja.set_margin_start(6)
        caja.set_margin_end(6)
        titulo = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        meta.add_css_class("dim-label")
        caja.append(titulo)
        caja.append(meta)
        item.set_child(caja)

    def _lista_bind(self, fabrica, item):
        obj = item.get_item()
        caja = item.get_child()
        titulo = caja.get_first_child()
        meta = titulo.get_next_sibling()

        def _pintar(*_a):
            f = obj.fila
            texto = ("★ " if obj.favorito else "") + f.titulo
            titulo.set_markup(f"<b>{GLib.markup_escape_text(texto)}</b>" if not obj.leido
                              else GLib.markup_escape_text(texto))
            partes = [p for p in (f"— {f.extra['grupo']} —" if f.extra.get("grupo") else "", f.extra.get("origen", ""), f.feed_titulo, f.autor if f.autor != f.feed_titulo else "",
                                  datos.fecha_legible(f.fecha)) if p]
            meta.set_text(" · ".join(partes))
            item.set_accessible_label(obj.descripcion())
        _pintar()
        item._ids = [obj.connect(f"notify::{p}", _pintar) for p in ("leido", "favorito", "mas_tarde")]
        item._obj = obj

    def _lista_unbind(self, fabrica, item):
        obj = getattr(item, "_obj", None)
        if obj is not None:
            for i in getattr(item, "_ids", []):
                obj.disconnect(i)
        item._obj = None

    def _crear_lectura(self):
        self.texto = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                  left_margin=10, right_margin=10, top_margin=8, bottom_margin=8)
        self.texto.set_name("lectura")
        _etiquetar(self.texto, _("Contenido del artículo"))
        self.buffer = self.texto.get_buffer()
        self.etiqueta_titulo = self.buffer.create_tag("titulo", weight=Pango.Weight.BOLD, scale=1.3)
        self.etiqueta_meta = self.buffer.create_tag("meta", foreground_rgba=Gdk.RGBA(0.45, 0.45, 0.45, 1))
        self.panel_lectura = Gtk.ScrolledWindow(child=self.texto, min_content_height=120)
        return self.panel_lectura

    # ------------------------------------------------------------------ foco entre paneles
    def _configurar_tabulador(self):
        """Tab y Mayús+Tab saltan ENTRE los paneles (árbol, lista, texto), no entre las filas de una lista.
        Las flechas siguen moviendo por las filas. Se intercepta antes de que GTK reparta el foco."""
        ctl = Gtk.EventControllerKey()
        ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._on_tecla_ventana)
        self.add_controller(ctl)

    def _panel_con_foco(self):
        foco = self.get_focus()
        if foco is None:
            return None
        for panel in (self.entrada_busqueda, self.vista_arbol, self.vista_lista, self.botonera, self.texto):
            if foco is panel or foco.is_ancestor(panel):
                return panel
        return None

    def _on_tecla_ventana(self, ctl, keyval, keycode, estado):
        if keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return False
        if estado & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        actual = self._panel_con_foco()
        if actual is None:
            return False
        atras = keyval == Gdk.KEY_ISO_Left_Tab or bool(estado & Gdk.ModifierType.SHIFT_MASK)
        orden = [self.entrada_busqueda, self.vista_arbol, self.vista_lista]
        if self.botonera.get_visible():
            orden.append(self.botonera)
        if self.panel_lectura.get_visible():
            orden.append(self.texto)
        if actual not in orden:
            return False
        i = orden.index(actual)
        j = i - 1 if atras else i + 1
        if j < 0 or j >= len(orden):
            return False        # fuera de los paneles: el recorrido normal de GTK
        self.enfocar_panel(orden[j])
        return True

    # ------------------------------------------------------------------ foco y barra de menús
    def _barra_menu(self):
        """El Gtk.PopoverMenuBar que crea set_show_menubar (si ya existe)."""
        def buscar(w):
            c = w.get_first_child()
            while c is not None:
                if isinstance(c, Gtk.PopoverMenuBar):
                    return c
                r = buscar(c)
                if r is not None:
                    return r
                c = c.get_next_sibling()
            return None
        if getattr(self, "_barra_cache", None) is None:
            self._barra_cache = buscar(self)
        return self._barra_cache

    def _en_barra(self, widget) -> bool:
        barra = self._barra_menu()
        return bool(widget is not None and barra is not None and (widget is barra or widget.is_ancestor(barra)))

    def _hay_menu_abierto(self) -> bool:
        barra = self._barra_menu()
        if barra is None:
            return False

        def visible(w):
            c = w.get_first_child()
            while c is not None:
                if isinstance(c, Gtk.Popover) and c.is_visible():
                    return True
                if visible(c):
                    return True
                c = c.get_next_sibling()
            return False
        return visible(barra)

    def _configurar_menu(self):
        """Escape en el menú lo cierra y devuelve el foco a donde estaba antes de entrar en la barra."""
        self._foco_antes_menu = None
        self._menu_estuvo_abierto = False
        self._revision_menu = 0
        self.connect("notify::focus-widget", self._on_foco_ventana)
        ctl = Gtk.EventControllerKey()
        ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._on_escape_menu)
        self.add_controller(ctl)

    def _on_foco_ventana(self, *_a):
        foco = self.get_focus()
        if foco is None:
            return
        if self._en_barra(foco):
            # Se entró (o se sigue) en la barra de menús: se mira, ya con la interfaz asentada, si hay
            # un menú abierto, y si acaba de cerrarse se devuelve el foco.
            if not self._revision_menu:
                self._revision_menu = GLib.idle_add(self._revisar_menu)
        else:
            self._foco_antes_menu = foco

    def _revisar_menu(self):
        self._revision_menu = 0
        if not self._en_barra(self.get_focus()):
            self._menu_estuvo_abierto = False
            return GLib.SOURCE_REMOVE
        if self._hay_menu_abierto():
            self._menu_estuvo_abierto = True
            # Cuando ese menú se cierre, GTK deja el foco en la barra: se vuelve a mirar.
            self._vigilar_cierre_menus()
        elif self._menu_estuvo_abierto:
            self._menu_estuvo_abierto = False
            self.devolver_foco_tras_menu()
        return GLib.SOURCE_REMOVE

    def _vigilar_cierre_menus(self):
        barra = self._barra_menu()
        vistos = getattr(self, "_popovers_vigilados", set())
        self._popovers_vigilados = vistos

        def recorrer(w):
            c = w.get_first_child()
            while c is not None:
                if isinstance(c, Gtk.Popover) and id(c) not in vistos:
                    vistos.add(id(c))
                    c.connect("closed", lambda *_a: self._programar_revision())
                recorrer(c)
                c = c.get_next_sibling()
        recorrer(barra)

    def _programar_revision(self):
        if not self._revision_menu:
            self._revision_menu = GLib.idle_add(self._revisar_menu)

    def _on_escape_menu(self, ctl, keyval, keycode, estado):
        """Escape con el foco en la barra y sin menú desplegado: se sale de la barra."""
        if keyval == Gdk.KEY_Escape and self._en_barra(self.get_focus()) and not self._hay_menu_abierto():
            self.devolver_foco_tras_menu()
            return True
        return False

    def devolver_foco_tras_menu(self):
        destino = self._foco_antes_menu
        for panel in (self.vista_arbol, self.vista_lista, self.texto, self.entrada_busqueda):
            if destino is not None and (destino is panel or destino.is_ancestor(panel)):
                self.enfocar_panel(panel)
                return
        if destino is not None and destino.get_mapped() and not self._en_barra(destino):
            destino.grab_focus()
        else:
            self.enfocar_panel(self.vista_lista)

    def enfocar_panel(self, panel):
        """Lleva el foco a un panel. En una lista cae en la fila que estaba seleccionada (la última enfocada),
        no en la primera."""
        seleccion = {self.vista_arbol: self.seleccion_arbol, self.vista_lista: self.seleccion_lista}.get(panel)
        if seleccion is not None:
            pos = seleccion.get_selected()
            panel.grab_focus()
            if pos != Gtk.INVALID_LIST_POSITION:
                # grab_focus() deja el foco en el primer elemento visible; se lleva al seleccionado.
                panel.scroll_to(pos, Gtk.ListScrollFlags.FOCUS, None)
            return
        if panel is self.botonera:
            self.enfocar_botonera()
            return
        if panel is self.texto:
            self.mostrar_panel_lectura()
        panel.grab_focus()

    # ------------------------------------------------------------------ acciones
    def _registrar_acciones(self):
        self._atajos_defecto = {}
        personalizados = config.get("gtk", "atajos", {}) or {}

        def accion(nombre, fn, aceleradores=()):
            a = Gio.SimpleAction.new(nombre, None)
            envuelta = self._con_contexto_bandeja(nombre, fn)
            a.connect("activate", lambda *_a: envuelta())
            self.add_action(a)
            self._atajos_defecto[nombre] = list(aceleradores)
            # Las teclas que el usuario cambió en el editor de atajos mandan sobre las de fábrica.
            teclas = personalizados.get(nombre, aceleradores)
            if teclas:
                self.get_application().set_accels_for_action(f"win.{nombre}", list(teclas))
        self._accion = accion
        accion("actualizar", self.accion_actualizar, ["<Ctrl><Shift>r"])
        accion("arbol-subir", lambda: self.mover_nodo_arbol(True))
        accion("arbol-bajar", lambda: self.mover_nodo_arbol(False))
        accion("actualizar-feed", self.accion_actualizar_feed)
        accion("buscar", lambda: self.entrada_busqueda.grab_focus(), ["<Ctrl>f"])
        accion("leido", self.accion_alternar_leido, ["<Ctrl>m"])
        accion("favorito", self.accion_alternar_favorito, ["<Ctrl>d"])
        accion("me-gusta", self.accion_alternar_favorito, ["<Ctrl>b"])
        accion("impulsar", self.accion_impulsar, ["<Ctrl><Shift>p"])
        accion("marcador", self.accion_marcador, ["<Ctrl><Shift>j"])
        accion("responder", self.accion_responder, ["<Ctrl>r"])
        accion("nuevo", self.accion_nuevo, ["<Ctrl>n"])
        accion("conversacion", self.accion_conversacion, ["<Ctrl><Shift>v"])
        accion("cronologia-usuario", self.accion_cronologia_usuario, ["<Ctrl><Shift>g"])
        accion("cargar-mas", self.accion_cargar_mas, ["<Ctrl><Shift>m"])
        accion("cerrar-buffer", self.accion_cerrar_buffer, ["<Ctrl>w"])
        accion("eliminar-cuenta", self.accion_eliminar_cuenta)
        accion("menu-contextual", self.mostrar_menu_contextual, ["<Shift>F10"])
        accion("votar", self.accion_votar, ["<Ctrl><Alt>v"])
        accion("perfil", self.accion_perfil, ["<Ctrl><Alt>p"])
        accion("editar", self.accion_editar, ["<Ctrl><Alt>e"])
        accion("citar", self.accion_citar, ["<Ctrl><Shift>u"])
        accion("reportar", self.accion_reportar)
        accion("abrir-lista", self.accion_abrir_lista, ["<Ctrl><Alt>l"])
        accion("abrir-feed", lambda: self.ws_abrir_feed() if self._ws_ctx() else self.bsky_abrir_feed(),
               ["<Ctrl><Alt>f"])
        self._registrar_acciones_bluesky(accion)
        self._registrar_acciones_wsocial(accion)
        self._registrar_acciones_herramientas(accion)
        self._registrar_acciones_rss(accion)
        self._registrar_acciones_bandeja(accion)
        self._registrar_acciones_telegram(accion)
        self._registrar_acciones_irc(accion)
        self._registrar_acciones_xmpp(accion)
        self._registrar_acciones_matrix(accion)
        self._registrar_acciones_x(accion)
        self._registrar_acciones_reddit(accion)
        self._registrar_acciones_peertube(accion)
        self._registrar_acciones_mastodon_extra(accion)
        self._registrar_acciones_at(accion)
        accion("voto-negativo", self.accion_voto_negativo, ["<Ctrl><Shift>d"])
        self._registrar_acciones_voz(accion)
        accion("unirse-canal", self.accion_unirse, ["<Ctrl><Alt>j"])
        accion("usuarios-canal", self.accion_usuarios, ["<Ctrl><Alt>i"])
        accion("abrir-chat", self.accion_abrir_chat, [])
        accion("abrir-hashtag", self.accion_abrir_hashtag, ["<Ctrl><Alt>h"])
        accion("buscar-mastodon", self.accion_buscar_mastodon, ["<Ctrl><Shift>f"])
        for tipo in ("followers", "following", "blocked", "muted", "solicitudes"):
            accion(f"cuentas-{tipo}", lambda t=tipo: self.abrir_cuentas(t))
        accion("quien-impulso", lambda: self.accion_interacciones("reblog"))
        accion("quien-favorito", lambda: self.accion_interacciones("favourite"))
        accion("historial", self.accion_historial)
        accion("silenciar-conversacion", lambda: self.accion_conversacion_silencio(True))
        accion("reactivar-conversacion", lambda: self.accion_conversacion_silencio(False))
        accion("fijar", lambda: self.accion_fijar(True))
        accion("desfijar", lambda: self.accion_fijar(False))
        accion("borradores", self.accion_borradores, ["<Ctrl><Alt>b"])
        accion("programadas", self.accion_programadas)
        accion("filtros", self.accion_filtros)
        accion("mi-perfil", self.accion_mi_perfil)
        accion("tendencias", lambda: self.abrir_hashtags("tendencia"))
        accion("hashtags-seguidos", lambda: self.abrir_hashtags("seguidos"))
        accion("seguir-hashtag", lambda: self.accion_seguir_hashtag(True))
        accion("dejar-hashtag", lambda: self.accion_seguir_hashtag(False))
        from . import acciones_ia
        accion("traducir", lambda: acciones_ia.traducir(self), ["<Ctrl>t"])
        accion("resumir-ia", lambda: acciones_ia.resumir(self), ["<Ctrl><Alt>r"])
        accion("describir-imagen", lambda: acciones_ia.analizar_imagen(self, "describir"), ["<Ctrl>i"])
        accion("ocr-imagen", lambda: acciones_ia.analizar_imagen(self, "ocr"), ["<Ctrl><Shift>o"])
        accion("historial-traducciones", lambda: self._historial("DialogoHistorialTraducciones"))
        accion("historial-ia", lambda: self._historial("DialogoHistorialIA"))
        accion("nuevo-hilo", self.accion_nuevo_hilo, ["<Ctrl><Shift>n"])
        accion("hilos-guardados", lambda: self._dialogo_extra("DialogoHilos"))
        accion("notif-agrupadas", lambda: self._dialogo_extra("DialogoNotifAgrupadas"))
        accion("notif-politica", lambda: self._dialogo_extra("DialogoPoliticaNotif"))
        accion("notif-solicitudes", lambda: self._dialogo_extra("DialogoSolicitudesNotif"))
        accion("admin-informes", lambda: self._dialogo_extra("DialogoInformes"))
        accion("admin-cuentas", lambda: self._dialogo_extra("DialogoCuentasAdmin"))
        accion("admin-dominios", lambda: self._dialogo_extra("DialogoDominios"))
        accion("descartar-notificacion", self.accion_descartar_notificacion)
        accion("limpiar-notificaciones", self.accion_limpiar_notificaciones)
        accion("mas-tarde", self.accion_alternar_mas_tarde, ["<Ctrl>l"])
        accion("papelera", self.accion_papelera)
        accion("siguiente-no-leido", lambda: self.accion_saltar_no_leido(True), ["<Ctrl>k"])
        accion("anterior-no-leido", lambda: self.accion_saltar_no_leido(False), ["<Ctrl><Shift>k"])
        accion("navegador", self.accion_abrir_navegador, ["<Ctrl>Return"])
        accion("copiar-enlace", self.accion_copiar_enlace)
        accion("copiar-titulo", self.accion_copiar_titulo, ["<Ctrl><Shift>c"])
        accion("texto-completo", self.accion_texto_completo, ["<Alt>a"])
        accion("marcar-todo-leido", self.accion_marcar_todo_leido)
        accion("ir-arbol", lambda: self.enfocar_panel(self.vista_arbol), ["<Alt>1"])
        accion("ir-lista", lambda: self.enfocar_panel(self.vista_lista), ["<Alt>2"])
        accion("ir-lectura", lambda: self.enfocar_panel(self.texto), ["<Alt>3"])
        # Reproductor (mismas teclas que en Windows)
        accion("reproducir", self.accion_reproducir_pausa, ["F2"])
        accion("retroceder", lambda: self.accion_saltar(-1), ["F1"])
        accion("avanzar", lambda: self.accion_saltar(+1), ["F3"])
        accion("detener", self.accion_detener, ["F4"])
        accion("volumen-menos", lambda: self.accion_volumen(-0.05), ["F5"])
        accion("volumen-mas", lambda: self.accion_volumen(+0.05), ["F6"])
        accion("velocidad-menos", lambda: self.accion_velocidad(-0.1), ["F7"])
        accion("velocidad-mas", lambda: self.accion_velocidad(+0.1), ["F8"])
        accion("reproducir-articulo", self.accion_reproducir_articulo)
        accion("decir-estado", self.accion_decir_estado, ["<Ctrl><Shift>b"])

    # ------------------------------------------------------------------ utilidades
    def avisar(self, texto: str, interrumpir: bool = True):
        """Actualiza la barra de estado y lo dice por el lector."""
        self.estado.set_text(texto)
        speak(texto, interrupt=interrumpir)

    def _fila_actual(self):
        i = self.seleccion_lista.get_selected()
        if i == Gtk.INVALID_LIST_POSITION:
            return None
        return self.almacen_lista.get_item(i)

    def _nodo_obj_actual(self):
        i = self.seleccion_arbol.get_selected()
        if i == Gtk.INVALID_LIST_POSITION:
            return None
        row = self.modelo_arbol.get_row(i)
        return row.get_item() if row else None

    # ------------------------------------------------------------------ árbol
    def _arbol_completo(self):
        """RSS y Mastodon como dos servicios en la raíz del árbol."""
        rss = Nodo(_("RSS y podcasts"), "servicio", servicio="rss", hijos=datos.construir_arbol(self.account_id))
        arbol = [rss, datos_mastodon.construir_nodos(), datos_bluesky.construir_nodos(), datos_wsocial.construir_nodos(), datos_telegram.construir_nodos(),
                 datos_irc.construir_nodos(), datos_xmpp.construir_nodos(), datos_matrix.construir_nodos()]
        if datos_x.disponible():
            arbol.insert(3, datos_x.construir_nodos())
        if self.bandeja_habilitada():
            arbol.insert(1, self.bandeja_nodo_raiz())
        if datos_reddit.disponible():
            arbol.append(datos_reddit.construir_nodos())
        if datos_peertube.disponible():
            arbol.append(datos_peertube.construir_nodos())
        aplicar_orden(arbol)
        return arbol

    def cargar_arbol(self, conservar=True, seleccionar=None):
        """Reconstruye el árbol. `seleccionar` es la clave de un nodo a enfocar (si no, se conserva el actual)."""
        previo = seleccionar or (self._nodo_obj_actual().clave() if (conservar and self._nodo_obj_actual()) else None)
        # Los grupos que el usuario tenía desplegados siguen así tras reconstruir el árbol.
        abiertos = {self.modelo_arbol.get_row(i).get_item().clave() for i in range(self.modelo_arbol.get_n_items())
                    if self.modelo_arbol.get_row(i).get_item().nodo.tipo in ("grupo", "category")
                    and self.modelo_arbol.get_row(i).get_expanded()}

        def _contiene(nodo):
            return any(NodoObj(h).clave() == previo or _contiene(h) for h in nodo.hijos)
        self.arbol = self._arbol_completo()
        self.raiz_arbol.remove_all()
        for n in self.arbol:
            self.raiz_arbol.append(NodoObj(n))
        # Servicios y cuentas desplegados: lo que se usa a diario queda a un paso.
        i = 0
        while i < self.modelo_arbol.get_n_items():
            row = self.modelo_arbol.get_row(i)
            nodo_fila = row.get_item().nodo
            # Los grupos (Mi biblioteca, Búsquedas guardadas) se quedan plegados salvo que contengan el nodo a enfocar.
            if nodo_fila.tipo in ("servicio", "cuenta") or (
                    nodo_fila.tipo in ("grupo", "category") and (row.get_item().clave() in abiertos or (
                        previo and _contiene(nodo_fila)))):
                row.set_expanded(True)
            i += 1
        destino = None
        if previo:
            for i in range(self.modelo_arbol.get_n_items()):
                if self.modelo_arbol.get_row(i).get_item().clave() == previo:
                    destino = i
                    break
        if destino is None:
            destino = next((i for i in range(self.modelo_arbol.get_n_items())
                            if self.modelo_arbol.get_row(i).get_item().nodo.listable), 0)
        self.seleccion_arbol.set_selected(destino)
        self._on_nodo_seleccionado()

    def seleccionar_nodo(self, nodo: Nodo):
        """Enfoca un nodo concreto del árbol (tras abrir un buffer, por ejemplo)."""
        for i in range(self.modelo_arbol.get_n_items()):
            if self.modelo_arbol.get_row(i).get_item().nodo is nodo:
                self.seleccion_arbol.set_selected(i)
                self.vista_arbol.scroll_to(i, Gtk.ListScrollFlags.FOCUS, None)
                return

    def refrescar_contadores(self):
        """Actualiza los contadores del árbol SIN reconstruirlo (no se pierde el foco ni lo expandido)."""
        nuevo = {}

        def _recoger(nodos):
            for n in nodos:
                nuevo[(n.tipo, n.category_id, n.feed_id)] = n
                _recoger(n.hijos)
        _recoger(self._arbol_completo())
        for i in range(self.modelo_arbol.get_n_items()):
            obj = self.modelo_arbol.get_row(i).get_item()
            n = nuevo.get(obj.clave())
            if n is not None:
                obj.nodo.no_leidos = n.no_leidos
                obj.nodo.error = n.error
                obj.sincronizar()

    def _on_nodo_seleccionado(self, *_a):
        """Cambio de nodo: lo barato (título y estado) es inmediato; la lista se lee cuando el usuario se
        detiene un instante, para no trabajar en cada paso mientras recorre el árbol con las flechas."""
        obj = self._nodo_obj_actual()
        if obj is None:
            return
        self._tg_al_cambiar_nodo(self.nodo_actual, obj.nodo)
        self._irc_al_cambiar_nodo(self.nodo_actual, obj.nodo)
        self._x_al_cambiar_nodo(self.nodo_actual, obj.nodo)
        self._mx_al_cambiar_nodo(self.nodo_actual, obj.nodo)
        self._tw_al_cambiar_nodo(self.nodo_actual, obj.nodo)
        self.nodo_actual = obj.nodo
        self.aplicar_visibilidad_detalles()
        self.busqueda = ""
        n = obj.nodo
        self.set_title(f"{n.titulo} — SocialRSS")
        self.estado.set_text(_("Cargando {t}…").format(t=n.titulo) if n.listable else n.titulo)
        self._cancelar_timer("_timer_lista")
        self._cancelar_timer("_timer_red")
        self._timer_lista = GLib.timeout_add(PAUSA_LISTA_MS, self._tras_pausa_lista, n)

    def _cancelar_timer(self, nombre: str):
        id_ = getattr(self, nombre)
        if id_:
            GLib.source_remove(id_)
            setattr(self, nombre, 0)

    def _tras_pausa_lista(self, nodo):
        self._timer_lista = 0
        if nodo is not self.nodo_actual:
            return GLib.SOURCE_REMOVE
        self.cargar_lista()
        # La descarga de red se retrasa más: solo si el usuario se queda en el buffer.
        if nodo.servicio in ("mastodon", "bluesky", "wsocial", "telegram", "x", "reddit", "peertube") and nodo.listable and (nodo.account_id, nodo.timeline) not in self._cargados:
            self._timer_red = GLib.timeout_add(PAUSA_RED_MS, self._descarga_tras_pausa, nodo)
        return GLib.SOURCE_REMOVE

    def _descarga_tras_pausa(self, nodo):
        self._timer_red = 0
        if nodo is self.nodo_actual and (nodo.account_id, nodo.timeline) not in self._cargados:
            if nodo.servicio == "bluesky":
                self.bsky_actualizar_nodo(nodo, silencioso=True)
            elif nodo.servicio == "wsocial":
                self.ws_actualizar_nodo(nodo, silencioso=True)
            elif nodo.servicio == "telegram":
                self.tg_actualizar_nodo(nodo, silencioso=True)
            elif nodo.servicio == "reddit":
                self.rd_actualizar_nodo(nodo, silencioso=True)
            elif nodo.servicio == "peertube":
                self.pt_actualizar_nodo(nodo, silencioso=True)
            elif nodo.servicio == "x":
                # X limita con dureza: solo se descarga al entrar si el buffer no tiene nada en caché
                # (o con la actualización automática activada); lo demás va con Ctrl+Mayús+R.
                if self.almacen_lista.get_n_items() == 0 or config.get("x", "auto_update", False) \
                        or nodo.timeline.startswith("dm:"):
                    self.tw_actualizar_nodo(nodo, silencioso=True)
            else:
                self._actualizar_mastodon(nodo, silencioso=True)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ lista
    def _leer_filas(self, nodo, busqueda: str):
        """Hilo lector: consulta la base de datos y devuelve las filas (sin tocar widgets)."""
        if not nodo.listable:
            return []
        if nodo.servicio == "bandeja":
            filas = self.bandeja_filas()
            if busqueda:
                q = busqueda.lower()
                filas = [f for f in filas if q in f.titulo.lower() or q in f.autor.lower()]
            return filas
        if nodo.servicio in ("mastodon", "bluesky", "wsocial", "telegram", "irc", "xmpp", "matrix", "x", "reddit", "peertube"):
            modulo = {"mastodon": datos_mastodon, "bluesky": datos_bluesky, "wsocial": datos_wsocial, "telegram": datos_telegram,
                      "irc": datos_irc, "xmpp": datos_xmpp, "matrix": datos_matrix, "x": datos_x,
                      "reddit": datos_reddit, "peertube": datos_peertube}[nodo.servicio]
            filas = modulo.listar(nodo.account_id, nodo.timeline)
            if busqueda:
                q = busqueda.lower()
                filas = [f for f in filas if q in f.titulo.lower()]
            return filas
        filas = datos.listar(self.account_id, nodo, busqueda=busqueda)
        if config.get("rss", "group_by_date", False):
            self._marcar_grupos_fecha(filas)
        return filas

    @staticmethod
    def _marcar_grupos_fecha(filas):
        """Anota en la primera fila de cada bloque de fechas su etiqueta (Hoy, Ayer...): se lee y se ve antes del feed."""
        from app.core.fechas_grupo import bucket_fecha
        actual = None
        for f in filas:
            grupo = bucket_fecha(f.fecha)
            if grupo and grupo != actual:
                actual = grupo
                f.extra["grupo"] = grupo

    def cargar_lista(self, conservar_id: str = None, incremental: bool = False, despues=None):
        """Rellena la lista SIN bloquear la interfaz: lee en un hilo y aplica el resultado en el de la UI.
        Con `incremental`, si es el mismo buffer que ya se ve, solo se insertan, quitan y repintan las filas
        que cambian (el resto y el foco no se tocan). `despues(filas)` se llama al terminar."""
        if self.nodo_actual is None:
            return
        self._gen_lista += 1
        gen, nodo, busqueda = self._gen_lista, self.nodo_actual, self.busqueda
        if conservar_id is None and self._id_pendiente is not None:
            conservar_id, self._id_pendiente = self._id_pendiente, None

        def _trabajo():
            try:
                filas = self._leer_filas(nodo, busqueda)
            except Exception:
                log.exception("Error al leer la lista")
                filas = []
            llamar_en_ui(self._aplicar_lista, gen, nodo, busqueda, filas, conservar_id, incremental, despues)
        self._lector.submit(_trabajo)

    def _aplicar_lista(self, gen, n, busqueda, filas, conservar_id, incremental, despues):
        if gen != self._gen_lista or n is not self.nodo_actual:
            return      # el usuario ya está en otra cosa
        self.programar_botonera()
        titulo = n.titulo + (f" — {_('búsqueda')}: {busqueda}" if busqueda else "")
        if incremental and self._nodo_listado is n and self._sincronizar_lista(filas):
            self.estado.set_text(_("{t}: {n} elementos").format(t=n.titulo, n=len(filas)))
            pendiente, self._relleno_despues = self._relleno_despues, None
            self._cancelar_relleno()        # la sincronización ya ha completado la lista
            for cb in (pendiente, despues):
                if cb:
                    cb(filas)
            return
        self._nodo_listado = n
        self._cancelar_relleno()
        self._relleno_despues = None
        seleccionar = datos_telegram.seleccion_inicial(filas) if self._es_conversacion(n) else 0
        if conservar_id:
            for i, f in enumerate(filas):
                if f.id == conservar_id or f.extra.get("clave_lista") == conservar_id:
                    seleccionar = i
                    break
        # Una sola operación sobre el modelo (quitar lo viejo y poner lo nuevo): menos señales y menos
        # eventos de accesibilidad que remove_all() seguido de splice().
        # Cada fila cuesta tiempo del hilo de la interfaz aunque no se vea, así que una lista larga se pone por
        # tandas: primero la zona de la selección y luego, en los huecos, el resto (el foco no se mueve).
        if len(filas) <= LISTA_DIRECTA:
            tanda, resto = filas, None
            primera = 0
        else:
            primera = max(0, seleccionar - LISTA_VENTANA)
            fin = min(len(filas), seleccionar + LISTA_VENTANA + 1)
            tanda, resto = filas[primera:fin], (primera, fin)
        self.almacen_lista.splice(0, self.almacen_lista.get_n_items(), [FilaObj(f) for f in tanda])
        if filas:
            self.seleccion_lista.set_selected(seleccionar - primera)
        self.estado.set_text(_("{t}: {n} elementos").format(t=titulo, n=len(filas)) if n.listable else n.titulo)
        if not filas:
            if not n.listable:
                vacio = (_("Usa Archivo, Añadir cuenta de Mastodon para empezar.")
                         if n.servicio == "mastodon" and not n.hijos else "")
                self._mostrar_texto("", vacio, "", "")
            else:
                self._mostrar_texto("", self.bandeja_vacia() if n.servicio == "bandeja" else _("No hay elementos."), "", "")
        if resto is None:
            if despues:
                despues(filas)
            return
        self._relleno_despues = despues
        self._relleno_id = GLib.idle_add(self._rellenar_lista, gen, n, filas, resto[0], resto[1], True, despues,
                                         priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _cancelar_relleno(self):
        if getattr(self, "_relleno_id", 0):
            GLib.source_remove(self._relleno_id)
        self._relleno_id = 0

    def _rellenar_lista(self, gen, n, filas, ini, fin, hacia_abajo, despues):
        """Una tanda del resto de la lista: alterna entre añadir por debajo y por encima de lo que ya hay."""
        self._relleno_id = 0
        if gen != self._gen_lista or n is not self.nodo_actual:
            return GLib.SOURCE_REMOVE
        if (hacia_abajo and fin < len(filas)) or ini == 0:
            nuevo = min(len(filas), fin + LISTA_LOTE)
            self.almacen_lista.splice(self.almacen_lista.get_n_items(), 0, [FilaObj(f) for f in filas[fin:nuevo]])
            fin = nuevo
        elif ini > 0:
            nuevo = max(0, ini - LISTA_LOTE)
            self.almacen_lista.splice(0, 0, [FilaObj(f) for f in filas[nuevo:ini]])
            ini = nuevo
        if ini == 0 and fin >= len(filas):
            self._relleno_despues = None
            if despues:
                despues(filas)
            return GLib.SOURCE_REMOVE
        self._relleno_id = GLib.idle_add(self._rellenar_lista, gen, n, filas, ini, fin, not hacia_abajo, despues,
                                         priority=GLib.PRIORITY_DEFAULT_IDLE)
        return GLib.SOURCE_REMOVE

    def _sincronizar_lista(self, filas) -> bool:
        """Actualiza la lista en su sitio. False si el orden relativo cambió y hay que rehacerla."""
        almacen = self.almacen_lista
        viejos = [_clave_fila(almacen.get_item(i).fila) for i in range(almacen.get_n_items())]
        nuevos = {_clave_fila(f) for f in filas}
        viejos_set = set(viejos)
        if [i for i in viejos if i in nuevos] != [_clave_fila(f) for f in filas if _clave_fila(f) in viejos_set]:
            return False
        for i in range(almacen.get_n_items() - 1, -1, -1):
            if _clave_fila(almacen.get_item(i).fila) not in nuevos:
                almacen.remove(i)
        for i, f in enumerate(filas):
            if i < almacen.get_n_items() and _clave_fila(almacen.get_item(i).fila) == _clave_fila(f):
                obj = almacen.get_item(i)
                obj.fila = f
                obj.favorito, obj.leido = f.favorito, f.leido
                obj.impulsado, obj.marcador = bool(f.extra.get("impulsado")), bool(f.extra.get("marcador"))
                obj.notify("leido")      # repinta contadores y estado
            else:
                almacen.insert(i, FilaObj(f))
        if almacen.get_n_items() and self.seleccion_lista.get_selected() == Gtk.INVALID_LIST_POSITION:
            self.seleccion_lista.set_selected(0)
        return True

    def _on_fila_seleccionada(self, *_a):
        self.programar_botonera()
        obj = self._fila_actual()
        if obj is not None:
            self._vista_previa(obj)

    def _preparar_texto(self, fila):
        """Hilo lector: arma (título, meta, enlace, cuerpo) de una fila; None si no hay nada que mostrar."""
        if fila.servicio == "mastodon":
            return datos_mastodon.detalle(fila)
        if fila.servicio == "bluesky":
            return datos_bluesky.detalle(fila)
        if fila.servicio == "wsocial":
            return datos_wsocial.detalle(fila)
        if fila.servicio == "x":
            return datos_x.detalle(fila)
        if fila.servicio == "reddit":
            return datos_reddit.detalle(fila)
        if fila.servicio == "peertube":
            return datos_peertube.detalle(fila)
        if fila.servicio == "telegram":
            return datos_telegram.detalle(fila)
        if fila.servicio == "irc":
            return datos_irc.detalle(fila)
        if fila.servicio == "xmpp":
            return datos_xmpp.detalle(fila)
        if fila.servicio == "matrix":
            return datos_matrix.detalle(fila)
        art = rss_service.obtener_articulo(fila.id)
        if art is None:
            return None
        from app.core.html_utils import html_a_texto
        cuerpo = html_a_texto(art.translated_content or art.content or "") or _("(sin contenido)")
        meta = " · ".join(p for p in (fila.feed_titulo, fila.autor, datos.fecha_legible(fila.fecha)) if p)
        return (fila.titulo, meta, fila.enlace, cuerpo)

    def _vista_previa(self, obj, ahora: bool = False, despues=None):
        """Carga el texto en el panel de lectura sin marcar como leído ni mover el foco. Al recorrer la lista
        con las flechas espera a que el usuario se detenga; la lectura se hace fuera del hilo de la UI."""
        if not ahora and not self.panel_lectura.get_visible():
            return          # panel de detalles oculto: no se lee nada mientras no se vea
        self._gen_texto += 1
        self._cancelar_timer("_timer_texto")
        gen = self._gen_texto

        def _lanzar():
            self._timer_texto = 0

            def _trabajo():
                try:
                    texto = self._preparar_texto(obj.fila)
                except Exception:
                    log.exception("Error al preparar el texto")
                    texto = None
                llamar_en_ui(self._aplicar_texto, gen, obj, texto, despues)
            self._lector.submit(_trabajo)
            return GLib.SOURCE_REMOVE
        if ahora:
            _lanzar()
        else:
            self._timer_texto = GLib.timeout_add(PAUSA_TEXTO_MS, _lanzar)

    def _aplicar_texto(self, gen, obj, texto, despues):
        if gen != self._gen_texto:
            return
        if texto is not None:
            self._mostrar_texto(*texto)
            self._articulo_abierto = obj.fila.id
            if obj.fila.servicio == "mastodon" and obj.fila.extra.get("encuesta"):
                self._anadir_encuesta_a_lectura(obj)
        if despues:
            despues()

    def _mostrar_texto(self, titulo, meta, enlace, cuerpo):
        self.buffer.set_text("")
        fin = self.buffer.get_end_iter()
        if titulo:
            self.buffer.insert_with_tags(fin, titulo + "\n", self.etiqueta_titulo)
        if meta:
            self.buffer.insert_with_tags(self.buffer.get_end_iter(), meta + "\n", self.etiqueta_meta)
        if enlace:
            self.buffer.insert_with_tags(self.buffer.get_end_iter(), enlace + "\n", self.etiqueta_meta)
        if cuerpo:
            self.buffer.insert(self.buffer.get_end_iter(), "\n" + cuerpo)
        self.buffer.place_cursor(self.buffer.get_start_iter())

    def _on_abrir_fila(self, vista, posicion):
        """Intro sobre un artículo: marcar leído, descargar el texto completo si falta y pasar a leerlo."""
        obj = self.almacen_lista.get_item(posicion)
        if obj is None:
            return
        if obj.fila.servicio == "bluesky" and obj.fila.extra.get("tipo") == "convo":
            return self.bsky_abrir_convo(obj)
        if obj.fila.servicio == "wsocial" and obj.fila.extra.get("tipo") == "convo":
            return self.ws_abrir_convo(obj)
        if obj.fila.servicio == "telegram" and obj.fila.extra.get("tipo") == "chat":
            return self.tg_abrir_chat(obj)
        if obj.fila.servicio == "irc" and obj.fila.extra.get("tipo") == "chat":
            return self.irc_abrir_chat(obj)
        if obj.fila.servicio == "xmpp" and obj.fila.extra.get("tipo") in ("chat", "solicitud"):
            return self.x_abrir_fila()
        if obj.fila.servicio == "matrix" and obj.fila.extra.get("tipo") in ("sala", "invitacion"):
            return self.mx_abrir_fila()
        if obj.fila.servicio == "reddit":
            if obj.fila.extra.get("tipo") in ("post", "comentario"):
                return self.rd_abrir_hilo(obj)
            return self.rd_abrir_fila(obj)
        if obj.fila.servicio == "peertube":
            return self.pt_abrir_fila(obj)
        if obj.fila.servicio == "rss" and self._intro_rss(obj):
            return
        self._abrir_en_lector(obj)

    def _intro_rss(self, obj) -> bool:
        """Preferencia `rss.enter_action` (la misma que en Windows): navegador | lector | preguntar.
        True si ya está resuelto aquí; False si hay que abrir el artículo en el panel de lectura."""
        accion = config.get("rss", "enter_action", "navegador")
        if accion == "preguntar":
            dlg = Gtk.AlertDialog()
            dlg.set_message(_("Abrir artículo"))
            dlg.set_detail(_("¿Cómo quieres abrir este artículo?"))
            dlg.set_buttons([_("Cancelar"), _("Abrir el lector limpio"), _("Abrir en el navegador")])
            dlg.set_default_button(1)
            dlg.set_cancel_button(0)

            def _fin(origen, res):
                try:
                    elegido = origen.choose_finish(res)
                except Exception:
                    elegido = 0
                if elegido == 1:
                    self._abrir_en_lector(obj)
                elif elegido == 2:
                    self.accion_abrir_navegador(obj)
            dlg.choose(self, None, _fin)
            return True
        if accion == "navegador" and obj.fila.enlace:
            self.accion_abrir_navegador(obj)
            return True
        return False

    def _abrir_en_lector(self, obj):
        """El texto del artículo en el panel de lectura, ya cargado para que el lector de pantalla lo encuentre completo."""
        self._vista_previa(obj, ahora=True, despues=lambda: self.enfocar_panel(self.texto))
        if obj.fila.servicio == "rss":
            if config.get("rss", "mark_read_on_open", True):
                self._marcar_leido(obj, True)
            rss_service.registrar_lectura(obj.fila.id)
            try:
                from app.core.plugins import plugin_manager, PluginHook
                plugin_manager.dispatch(PluginHook.ARTICLE_OPENED, article={
                    "id": obj.fila.id, "title": obj.fila.titulo, "link": obj.fila.enlace})
            except Exception:
                pass
            if rss_service.texto_completo_pendiente(obj.fila.id) and config.get("rss", "fulltext_auto", True):
                self._descargar_completo(obj, silencioso=True)

    def _marcar_leido(self, obj, leido: bool):
        if obj.leido == leido:
            return
        rss_service.marcar_leido(obj.fila.id, leido)
        obj.leido = leido
        self._contadores_pendientes()

    def _contadores_pendientes(self):
        if getattr(self, "_id_contadores", 0):
            return

        def _hacer():
            self._id_contadores = 0
            self.refrescar_contadores()
            return GLib.SOURCE_REMOVE
        self._id_contadores = GLib.timeout_add(400, _hacer)

    # ------------------------------------------------------------------ acciones sobre el artículo
    def accion_alternar_leido(self):
        obj = self._fila_actual()
        if obj is None:
            return
        if obj.fila.servicio == "reddit":
            return self.rd_leido()
        if obj.fila.servicio == "peertube":
            return self.pt_leida()
        if obj.fila.servicio != "rss":
            self.avisar(_("Esta acción es solo de RSS"))
            return
        self._marcar_leido(obj, not obj.leido)
        self.avisar(_("Leído") if obj.leido else _("Sin leer"))

    def accion_alternar_favorito(self):
        if self._bsky_ctx():
            return self.bsky_me_gusta()
        if self._ws_ctx():
            return self.ws_me_gusta()
        if self._tw_ctx():
            return self.tw_me_gusta()
        if self._rd_ctx():
            return self.rd_me_gusta()
        if self._pt_ctx():
            return self.pt_me_gusta()
        obj = self._fila_actual()
        if obj is None:
            return
        if obj.fila.servicio == "xmpp":
            if not self.x_destacar(obj):
                self.avisar(_("Selecciona un mensaje de XMPP"))
            return
        if obj.fila.servicio == "matrix":
            return self.mx_fijar_mensaje()
        if obj.fila.servicio == "mastodon":
            self._accion_estado(obj, "favourite", "unfavourite", "favorito",
                                _("Marcado como favorito"), _("Quitado de favoritos"))
            return
        rss_service.marcar_favorito(obj.fila.id, not obj.favorito)
        obj.favorito = not obj.favorito
        self.avisar(_("Destacado") if obj.favorito else _("Ya no está destacado"))

    def accion_alternar_mas_tarde(self):
        obj = self._fila_actual()
        if obj is None:
            return
        if obj.fila.servicio != "rss":
            self.avisar(_("Esta acción es solo de RSS"))
            return
        rss_service.marcar_leer_mas_tarde(obj.fila.id, not obj.mas_tarde)
        obj.mas_tarde = not obj.mas_tarde
        self.avisar(_("Leer más tarde") if obj.mas_tarde else _("Quitado de leer más tarde"))

    def accion_papelera(self):
        if self._bsky_ctx():
            return self.bsky_borrar()
        if self._ws_ctx():
            return self.ws_borrar()
        if self._tw_ctx():
            return self.tw_borrar()
        if self._rd_ctx():
            return self.rd_borrar()
        if self._pt_ctx():
            return self.pt_borrar()
        if self._tg_ctx():
            return self.tg_borrar()
        if self._irc_ctx():
            return self.irc_borrar()
        if self._x_ctx():
            return self.x_borrar()
        if self._mx_ctx():
            return self.mx_borrar()
        obj = self._fila_actual()
        if obj is None:
            return
        if obj.fila.servicio == "mastodon":
            self._borrar_publicacion(obj)
            return
        i = self.seleccion_lista.get_selected()
        rss_service.mover_a_papelera(obj.fila.id)
        self.almacen_lista.remove(i)
        n = self.almacen_lista.get_n_items()
        if n:
            self.seleccion_lista.set_selected(min(i, n - 1))
        self._contadores_pendientes()
        self.avisar(_("Movido a la papelera"))

    def accion_saltar_no_leido(self, adelante: bool):
        if self.nodo_actual is None or self.nodo_actual.servicio != "rss":
            self.avisar(_("Esta acción es solo de RSS"))
            return
        n = self.almacen_lista.get_n_items()
        actual = self.seleccion_lista.get_selected()
        if n == 0:
            return
        rango = range(actual + 1, n) if adelante else range(actual - 1, -1, -1)
        if actual == Gtk.INVALID_LIST_POSITION:
            rango = range(0, n) if adelante else range(n - 1, -1, -1)
        for i in rango:
            if not self.almacen_lista.get_item(i).leido:
                self.seleccion_lista.set_selected(i)
                self.vista_lista.scroll_to(i, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)
                return
        self.avisar(_("No hay más artículos sin leer"))

    def accion_marcar_todo_leido(self):
        if self.nodo_actual is not None and self.nodo_actual.servicio == "reddit":
            return self.rd_marcar_todo_leido()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "peertube":
            return self.pt_marcar_todo_leido()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "telegram":
            return self.tg_marcar_todo_leido()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "irc":
            return self.irc_marcar_todo_leido()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "xmpp":
            return self.x_marcar_todo_leido()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "matrix":
            return self.mx_marcar_todo_leido()
        if self.nodo_actual is None or self.nodo_actual.servicio != "rss":
            self.avisar(_("Esta acción es solo de RSS"))
            return
        n = self.almacen_lista.get_n_items()
        for i in range(n):
            obj = self.almacen_lista.get_item(i)
            if not obj.leido:
                rss_service.marcar_leido(obj.fila.id, True)
                obj.leido = True
        self.refrescar_contadores()
        self.avisar(_("Todo marcado como leído"))

    def accion_abrir_navegador(self, obj=None):
        obj = obj or self._fila_actual()
        if obj is None or not obj.fila.enlace:
            self.avisar(_("Este artículo no tiene enlace"))
            return
        webbrowser.open(obj.fila.enlace)
        self._marcar_leido(obj, True)
        self.avisar(_("Abierto en el navegador"))

    def accion_copiar_enlace(self):
        obj = self._fila_actual()
        if obj is None or not obj.fila.enlace:
            return
        from app.core.speech import copiar_al_portapapeles
        copiar_al_portapapeles(obj.fila.enlace, _("Enlace copiado"))

    def accion_copiar_titulo(self):
        obj = self._fila_actual()
        if obj is None:
            return
        from app.core.speech import copiar_al_portapapeles
        copiar_al_portapapeles(obj.fila.titulo, _("Título copiado"))

    def accion_texto_completo(self):
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "rss":
            self._descargar_completo(obj, silencioso=False)

    def _descargar_completo(self, obj, silencioso: bool):
        if not silencioso:
            self.avisar(_("Descargando el artículo completo…"))

        def _fin(html, err):
            if err:
                if not silencioso:
                    self.avisar(_("No se pudo descargar el artículo completo"))
                return
            if html is None:
                if not silencioso:
                    self.avisar(_("No hay nada mejor que lo que ya tienes"))
                return
            if self._articulo_abierto == obj.fila.id:
                self._vista_previa(obj, ahora=True)
            if not silencioso:
                self.avisar(_("Artículo completo descargado"))
        rss_service.descargar_articulo_completo_async(obj.fila.id, _fin)

    # ------------------------------------------------------------------ búsqueda
    def _on_buscar(self, entrada):
        self.busqueda = entrada.get_text().strip()

        def _listo(filas):
            if filas:
                self.enfocar_panel(self.vista_lista)
            else:
                self.avisar(_("Sin resultados"))
        self.cargar_lista(despues=_listo)

    def _on_limpiar_busqueda(self, entrada):
        entrada.set_text("")
        if self.busqueda:
            self.busqueda = ""
            self.cargar_lista()
        self.enfocar_panel(self.vista_lista)

    # ------------------------------------------------------------------ actualización
    def accion_actualizar(self):
        n0 = self.nodo_actual
        if n0 is not None and n0.servicio == "bandeja":
            self.bandeja_actualizar()
            return
        if n0 is not None and n0.servicio == "telegram":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.tg_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "irc":
            if n0.listable:
                self.irc_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "xmpp":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.x_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "matrix":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.mx_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "bluesky":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.bsky_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "wsocial":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.ws_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "x":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.tw_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "reddit":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.rd_actualizar_nodo(n0)
            return
        if n0 is not None and n0.servicio == "peertube":
            if n0.listable:
                self.avisar(_("Actualizando {t}…").format(t=n0.titulo))
                self.pt_actualizar_nodo(n0)
            return
        n = self.nodo_actual
        if n is not None and n.servicio == "mastodon":
            if n.listable:
                self.avisar(_("Actualizando {t}…").format(t=n.titulo))
                self._actualizar_mastodon(n)
            return
        if self._actualizando:
            self.avisar(_("Ya se está actualizando"))
            return
        self._actualizando = True
        self.avisar(_("Actualizando todos los feeds…"))

        def _fin(nuevos, err):
            self._actualizando = False
            if err:
                self.avisar(_("Error al actualizar: {e}").format(e=err))
                return
            self._tras_actualizar()
            self.avisar(_("{n} artículos nuevos").format(n=nuevos or 0) if nuevos else _("No hay artículos nuevos"))
        rss_service.actualizar_todos_async(self.account_id, callback=_fin)

    def accion_actualizar_feed(self):
        n = self.nodo_actual
        if n is None or n.servicio != "rss" or n.tipo != "feed":
            self.avisar(_("Selecciona un feed en el árbol"))
            return
        self.avisar(_("Actualizando {t}…").format(t=n.titulo))

        def _fin(nuevos, err):
            if err:
                self.avisar(_("Error al actualizar: {e}").format(e=err))
                return
            self._tras_actualizar()
            self.avisar(_("{n} artículos nuevos").format(n=nuevos or 0) if nuevos else _("No hay artículos nuevos"))
        rss_service.actualizar_feed_async(n.feed_id, callback=_fin)

    def _tras_actualizar(self):
        """Repinta contadores y añade lo nuevo a la lista, conservando el elemento seleccionado."""
        self.refrescar_contadores()
        if self.nodo_actual is not None and not self.busqueda:
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _sondeo_automatico(self):
        if not self._actualizando:
            def _fin(nuevos, err):
                if not err and nuevos:
                    self._tras_actualizar()
                    speak(_("{n} artículos nuevos").format(n=nuevos), interrupt=False)
            rss_service.actualizar_todos_async(self.account_id, callback=_fin, max_par_override=3,
                                               unique_key=f"rss:update_all:{self.account_id}",
                                               solo_vencidos=True)
        self._sondeo_mastodon()
        self._sondeo_bluesky()
        self._sondeo_wsocial()
        self._sondeo_telegram()
        self._sondeo_x()
        self._sondeo_reddit()
        self._sondeo_peertube()
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------ Mastodon
    def _sondeo_mastodon(self):
        """Trae lo nuevo de Inicio y Notificaciones de cada cuenta; avisa sin interrumpir al lector."""
        for acc_id, _nombre in datos_mastodon.cuentas():
            for clave in ("home", "notifications"):
                nodo = next((h for c in self._cuentas_nodos() if c.account_id == acc_id for h in c.hijos
                             if h.timeline == clave), None)
                if nodo is not None and (acc_id, clave) in self._cargados:
                    self._actualizar_mastodon(nodo, silencioso=True)

    def _cuentas_nodos(self):
        for raiz in getattr(self, "arbol", []):
            if raiz.servicio == "mastodon":
                return raiz.hijos
        return []

    def _actualizar_mastodon(self, nodo, silencioso: bool = False):
        clave = (nodo.account_id, nodo.timeline)
        primera = clave not in self._cargados

        def _fin(res, err):
            if err:
                if not silencioso:
                    self.avisar(_("Error al actualizar: {e}").format(e=err))
                return
            self._cargados.add(clave)
            nuevos = len(res or [])
            if nodo is self.nodo_actual and not self.busqueda:
                obj = self._fila_actual()
                self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
            if primera or not nuevos:
                if not silencioso and nodo is self.nodo_actual:
                    self.estado.set_text(_("{t}: actualizado").format(t=nodo.titulo))
                return
            if nodo.timeline == "notifications":
                speak(_("{n} notificaciones nuevas").format(n=nuevos), interrupt=False)
            elif nodo is self.nodo_actual:
                self.avisar(_("{n} nuevas").format(n=nuevos), interrumpir=False)
        if nodo.timeline.startswith("search:"):
            mastodon_service.buscar_y_guardar_async(nodo.account_id, nodo.timeline.split(":", 1)[1], nodo.timeline,
                                                    callback=lambda n, e: _fin([None] * (n or 0) if not e else None, e))
        else:
            mastodon_service.cargar_timeline_async(nodo.account_id, nodo.timeline, callback=_fin)

    def _contexto_mastodon(self, obj=None):
        """(account_id, fila_obj) de la publicación enfocada, o None si no hay una de Mastodon."""
        obj = obj or self._fila_actual()
        if obj is None or obj.fila.servicio != "mastodon":
            self.avisar(_("Esta acción es de Mastodon"))
            return None
        return obj

    def _estado_db_id(self, obj):
        return obj.fila.extra.get("estado_db_id") or obj.fila.id

    def _accion_estado(self, obj, on: str, off: str, propiedad: str, texto_on: str, texto_off: str):
        x = obj.fila.extra
        if x.get("tipo") == "notificacion" and not x.get("estado_db_id"):
            self.avisar(_("Esta notificación no tiene publicación"))
            return
        activo = obj.get_property(propiedad)

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            obj.set_property(propiedad, not activo)
            self.avisar(texto_off if activo else texto_on)
        mastodon_service.ejecutar_accion_toot_async(x["account_id"], self._estado_db_id(obj),
                                                    off if activo else on, callback=_fin)

    def accion_impulsar(self):
        if self._bsky_ctx():
            return self.bsky_republicar()
        if self._ws_ctx():
            return self.ws_republicar()
        if self._tw_ctx():
            return self.tw_retuit()
        if self._rd_ctx():
            return self.rd_crosspost()
        obj = self._contexto_mastodon()
        if obj:
            self._accion_estado(obj, "reblog", "unreblog", "impulsado",
                                _("Impulsado"), _("Impulso quitado"))

    def accion_marcador(self):
        if self._bsky_ctx():
            return self.bsky_marcador()
        if self._ws_ctx():
            return self.ws_marcador()
        if self._tw_ctx():
            return self.tw_marcador()
        if self._rd_ctx():
            return self.rd_guardar()
        if self._pt_ctx():
            return self.pt_ver_mas_tarde()
        obj = self._contexto_mastodon()
        if obj:
            self._accion_estado(obj, "bookmark", "unbookmark", "marcador",
                                _("Añadido a marcadores"), _("Quitado de marcadores"))

    def _cuenta_activa(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "mastodon" and n.account_id:
            return n.account_id
        cuentas = datos_mastodon.cuentas()
        return cuentas[0][0] if len(cuentas) == 1 else None

    def accion_nuevo(self):
        if self._bsky_ctx():
            return self.bsky_nuevo()
        if self._ws_ctx():
            return self.ws_nuevo()
        if self._tw_ctx():
            return self.tw_nuevo()
        if self._rd_ctx():
            return self.rd_nuevo()
        if self._pt_ctx():
            return self.pt_nuevo()
        if self._tg_ctx():
            return self.tg_nuevo()
        if self._irc_ctx():
            return self.irc_nuevo()
        if self._x_ctx():
            return self.x_nuevo()
        if self._mx_ctx():
            return self.mx_nuevo()
        n = self.nodo_actual
        if n is None or n.servicio == "rss":
            self.get_application().anadir_feed()
            return
        acc = self._cuenta_activa()
        if acc is None:
            self.avisar(_("Selecciona una cuenta de Mastodon"))
            return
        from .dialogos import DialogoPublicar
        DialogoPublicar(self, acc).present()

    def accion_responder(self):
        if self._bsky_ctx():
            return self.bsky_responder()
        if self._ws_ctx():
            return self.ws_responder()
        if self._tw_ctx():
            return self.tw_responder()
        if self._rd_ctx():
            return self.rd_responder()
        if self._pt_ctx():
            return self.pt_comentar()
        if self._tg_ctx():
            return self.tg_responder()
        if self._irc_ctx():
            return self.irc_responder()
        if self._x_ctx():
            return self.x_responder()
        if self._mx_ctx():
            return self.mx_responder()
        obj = self._contexto_mastodon()
        if not obj:
            return
        x = obj.fila.extra
        if not x.get("status_id"):
            self.avisar(_("Esta notificación no tiene publicación"))
            return
        from .dialogos import DialogoPublicar
        visib = x.get("visibilidad")
        DialogoPublicar(self, x["account_id"], texto_inicial=datos_mastodon.menciones_de_respuesta(obj.fila),
                        responde_a=x["status_id"], visibilidad=visib if visib in ("unlisted", "private", "direct") else None
                        ).present()

    def tras_publicar(self):
        """Tras publicar, vuelve a leer Mis publicaciones y el buffer actual."""
        n = self.nodo_actual
        if n is not None and n.servicio == "mastodon" and n.listable:
            self._actualizar_mastodon(n, silencioso=True)

    def _borrar_publicacion(self, obj):
        x = obj.fila.extra
        if not x.get("propio"):
            self.avisar(_("Solo puedes borrar tus propias publicaciones"))
            return
        from .dialogos import confirmar_async

        def _hacer():
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo borrar: {e}").format(e=err))
                    return
                for i in range(self.almacen_lista.get_n_items()):
                    if self.almacen_lista.get_item(i) is obj:
                        self.almacen_lista.remove(i)
                        n = self.almacen_lista.get_n_items()
                        if n:
                            self.seleccion_lista.set_selected(min(i, n - 1))
                        break
                self.avisar(_("Publicación borrada"))
            mastodon_service.ejecutar_accion_toot_async(x["account_id"], self._estado_db_id(obj), "delete", callback=_fin)
        confirmar_async(self, _("¿Borrar esta publicación? No se puede deshacer."), _hacer, etiqueta_si=_("Borrar"))

    def _por_red(self, nombre: str):
        """Acción de las teclas que comparten los chats (Ctrl+Alt+X, C, D, O y U): cada red tiene la suya."""
        def _hacer():
            prefijo = "x_" if self._x_ctx() else ("mx_" if self._mx_ctx() else "tg_")
            getattr(self, prefijo + nombre)()
        return _hacer

    @staticmethod
    def _modulo_datos(servicio: str):
        return {"bluesky": datos_bluesky, "wsocial": datos_wsocial, "telegram": datos_telegram, "irc": datos_irc,
                "xmpp": datos_xmpp, "matrix": datos_matrix, "x": datos_x, "reddit": datos_reddit,
                "peertube": datos_peertube}.get(servicio, datos_mastodon)

    @staticmethod
    def _es_conversacion(nodo) -> bool:
        """Buffers que se leen como una conversación: lo último, abajo (mensajes directos y chats)."""
        return (nodo.servicio in ("telegram", "irc", "xmpp", "matrix") and nodo.timeline.startswith("chat:")) or (
            nodo.servicio in ("bluesky", "wsocial", "x") and nodo.timeline.startswith("dm:")) or (
            nodo.servicio == "irc" and nodo.timeline == "server")

    def _abrir_buffer(self, account_id: int, timeline: str, titulo: str, servicio: str = "mastodon"):
        """Abre un hilo o una cronología como buffer del árbol (bajo su cuenta) y pasa a la lista."""
        nodo = self._modulo_datos(servicio).nodo_extra(account_id, timeline, titulo)
        self.cargar_arbol(seleccionar=NodoObj(nodo).clave())
        self.enfocar_panel(self.vista_lista)
        self.avisar(_("Abriendo {t}…").format(t=titulo))

    def accion_conversacion(self):
        if self._bsky_ctx():
            return self.bsky_conversacion()
        if self._ws_ctx():
            return self.ws_conversacion()
        if self._tw_ctx():
            return self.tw_conversacion()
        if self._rd_ctx():
            return self.rd_abrir_hilo()
        if self._pt_ctx():
            return self.pt_comentarios()
        obj = self._contexto_mastodon()
        if not obj or not obj.fila.extra.get("status_id"):
            return
        x = obj.fila.extra
        self._abrir_buffer(x["account_id"], f"thread:{x['status_id']}", _("Conversación de {n}").format(n=obj.fila.autor))

    def accion_cronologia_usuario(self):
        if self._bsky_ctx():
            return self.bsky_cronologia_usuario()
        if self._ws_ctx():
            return self.ws_cronologia_usuario()
        if self._tw_ctx():
            return self.tw_cronologia_usuario()
        if self._rd_ctx():
            return self.rd_cronologia_autor()
        if self._pt_ctx():
            return self.pt_abrir_canal()
        obj = self._contexto_mastodon()
        if not obj:
            return
        x = obj.fila.extra
        usuario = x.get("usuario") or x.get("de_usuario") or ""
        if not usuario:
            return
        self._abrir_buffer(x["account_id"], f"user:{usuario.lstrip('@')}", _("Publicaciones de {u}").format(u=usuario))

    def accion_voto_negativo(self):
        if self._rd_ctx():
            return self.rd_voto_negativo()
        if self._pt_ctx():
            return self.pt_no_me_gusta()
        self.avisar(_("Esta acción es de Reddit y PeerTube"))

    def accion_unirse(self):
        if self._mx_ctx():
            return self.mx_unirse()
        return self.x_unirse_sala() if self._x_ctx() else self.irc_unirse()

    def accion_usuarios(self):
        if self._mx_ctx():
            return self.mx_miembros()
        return self.x_ocupantes() if self._x_ctx() else self.irc_usuarios()

    def accion_abrir_chat(self):
        if self._mx_ctx():
            return self.mx_abrir_fila()
        if self._x_ctx():
            return self.x_abrir_fila()
        if self._irc_ctx():
            return self.irc_abrir_fila()
        return self.tg_abrir_fila()

    def accion_cerrar_buffer(self):
        n = self.nodo_actual
        if n is None or not n.cerrable:
            self.avisar(_("Este buffer no se puede cerrar"))
            return
        self._modulo_datos(n.servicio).cerrar_extra(n)
        inicio = {"telegram": "chats", "irc": "channels", "xmpp": "conversaciones", "matrix": "rooms",
                  "peertube": "recientes"}.get(n.servicio, "home")
        self.cargar_arbol(seleccionar=(n.servicio, n.account_id, "timeline", None, None, inicio))
        self.avisar(_("Buffer cerrado"))

    def accion_cargar_mas(self):
        if self._bsky_ctx():
            return self.bsky_cargar_mas()
        if self._ws_ctx():
            return self.ws_cargar_mas()
        if self._tw_ctx():
            return self.tw_cargar_mas()
        if self._rd_ctx():
            return self.rd_cargar_mas()
        if self._pt_ctx():
            return self.pt_cargar_mas()
        if self._tg_ctx():
            return self.tg_cargar_mas()
        if self._x_ctx():
            return self.x_cargar_mas()
        if self._mx_ctx():
            return self.mx_cargar_mas()
        n = self.nodo_actual
        if n is None or n.servicio != "mastodon" or not n.listable:
            self.avisar(_("Esta acción es de Mastodon"))
            return
        ultima = self.almacen_lista.get_n_items()
        if not ultima:
            return
        x = self.almacen_lista.get_item(ultima - 1).fila.extra
        if n.timeline in ("bookmarks", "favourites", "direct"):
            max_id = mastodon_service.PAG_SIGUIENTE
        elif n.timeline in ("notifications", "mentions"):
            max_id = x.get("notificacion_id")
        else:
            max_id = x.get("entrada_id")
        if not max_id:
            return
        self.avisar(_("Cargando publicaciones anteriores…"))
        actual = self._fila_actual()

        def _fin(res, err):
            if err:
                self.avisar(_("Error al cargar: {e}").format(e=err))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            self.avisar(_("{n} cargadas").format(n=len(res or [])))
        mastodon_service.cargar_timeline_async(n.account_id, n.timeline, max_id=max_id, callback=_fin)

    def accion_eliminar_cuenta(self):
        if self.nodo_actual is not None and self.nodo_actual.servicio == "bluesky":
            return self.bsky_eliminar_cuenta()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "wsocial":
            return self.ws_eliminar_cuenta()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "x":
            return self.tw_eliminar_cuenta()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "reddit":
            return self.rd_eliminar_cuenta()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "peertube":
            return self.pt_eliminar_cuenta()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "telegram":
            return self.tg_eliminar_cuenta()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "irc":
            return self.irc_eliminar_red()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "xmpp":
            return self.x_eliminar_cuenta()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "matrix":
            return self.mx_eliminar_cuenta()
        n = self.nodo_actual
        if n is None or n.servicio != "mastodon" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de Mastodon"))
            return
        from .dialogos import confirmar_async
        acc = n.account_id
        nombre = next((nm for i, nm in datos_mastodon.cuentas() if i == acc), "")

        def _hacer():
            mastodon_service.eliminar_cuenta(acc)
            datos_mastodon.EXTRA.pop(acc, None)
            self.cargar_arbol(conservar=False)
            self.avisar(_("Cuenta eliminada"))
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ tiempo real
    def iniciar_tiempo_real(self):
        """Arranca (o detiene) el streaming de Mastodon según la preferencia."""
        from .tiempo_real import TiempoReal
        self.iniciar_tiempo_real_bluesky()
        self.iniciar_tiempo_real_wsocial()
        self.iniciar_tiempo_real_telegram()
        self.iniciar_tiempo_real_irc()
        self.iniciar_tiempo_real_xmpp()
        self.iniciar_tiempo_real_matrix()
        activo = config.get("gtk", "mastodon_realtime", True)
        if not activo:
            if self._tiempo_real is not None:
                self._tiempo_real.detener()
            return
        if self._tiempo_real is None:
            self._tiempo_real = TiempoReal(self._evento_mastodon, self._reconectado_mastodon)
        self._tiempo_real.sincronizar([a for a, _n in datos_mastodon.cuentas()])

    def _mantenimiento_datos(self):
        """Poda del histórico en segundo plano: la primera vez poco después de arrancar y luego cada 20 minutos."""
        from app.core.mantenimiento import podar_historico
        from app.core.task_manager import task_manager
        task_manager.start_task(lambda ctx: podar_historico(), pass_context=True)
        GLib.timeout_add_seconds(20 * 60, self._mantenimiento_datos)
        return False

    def _iniciar_complementos(self):
        """Descubre y activa los complementos que el usuario tenía habilitados (antes de la primera descarga de feeds)."""
        try:
            from app.core.plugins import plugin_manager
            plugin_manager.main_window = self
            plugin_manager.descubrir()
            plugin_manager.activar_guardados()
        except Exception:
            log.error("No se pudo iniciar el sistema de complementos", exc_info=True)

    def _avisar_complementos_arranque(self):
        try:
            from app.core.plugins import plugin_manager, PluginHook
            plugin_manager.dispatch(PluginHook.APP_STARTED)
        except Exception:
            log.error("No se pudo notificar APP_STARTED a los complementos", exc_info=True)
        return False

    def _al_cerrar(self, *_a):
        try:
            from app.core.plugins import plugin_manager, PluginHook
            plugin_manager.dispatch(PluginHook.APP_CLOSING)
        except Exception:
            pass
        self.voz_cancelar(avisar=False)
        self._tg_al_cambiar_nodo(self.nodo_actual, None)
        self.detener_tiempo_real_telegram()
        self._irc_al_cambiar_nodo(self.nodo_actual, None)
        self.detener_tiempo_real_irc()
        self._x_al_cambiar_nodo(self.nodo_actual, None)
        self.detener_tiempo_real_xmpp()
        self._mx_al_cambiar_nodo(self.nodo_actual, None)
        self.detener_tiempo_real_matrix()
        self.detener_tiempo_real_x()
        if self._tiempo_real is not None:
            self._tiempo_real.detener()
        if self._tiempo_real_bsky is not None:
            self._tiempo_real_bsky.detener()
        wsocial_service.detener_tiempo_real()
        self._lector.shutdown(wait=False, cancel_futures=True)
        return False

    def _reconectado_mastodon(self, account_id: int):
        """Tras una reconexión del streaming se recupera lo perdido durante el corte."""
        for nodo in [h for c in self._cuentas_nodos() if c.account_id == account_id for h in c.hijos
                     if h.timeline in ("home", "notifications")]:
            if (account_id, nodo.timeline) in self._cargados:
                self._actualizar_mastodon(nodo, silencioso=True)

    def _evento_mastodon(self, account_id, timeline, procesado, tipo, payload):
        """Una novedad llegó por el streaming (hilo de la UI). Las ráfagas se juntan en un solo repintado."""
        if tipo == "delete":
            self._eventos_pendientes.setdefault((account_id, timeline), {})["borrado"] = True
        if not procesado:
            # Sin contenido aplicable: se trae por REST y se repinta al terminar.
            for nodo in [h for c in self._cuentas_nodos() if c.account_id == account_id for h in c.hijos
                         if h.timeline == timeline]:
                self._actualizar_mastodon(nodo, silencioso=True)
            return
        info = self._eventos_pendientes.setdefault((account_id, timeline), {})
        info["n"] = info.get("n", 0) + (1 if tipo in ("update", "notification", "conversation") else 0)
        if tipo == "notification" and isinstance(payload, dict):
            info.setdefault("avisos", []).append(self._texto_aviso(payload))
        if not info.get("programado"):
            info["programado"] = True
            GLib.timeout_add(250, self._vaciar_eventos, account_id, timeline)

    def _vaciar_eventos(self, account_id, timeline):
        info = self._eventos_pendientes.pop((account_id, timeline), {})
        n = self.nodo_actual
        visible = (n is not None and n.servicio == "mastodon" and n.account_id == account_id and
                   (n.timeline == timeline or (timeline == "notifications" and n.timeline == "mentions")))
        if visible:
            self.cargar_lista(conservar_id=(self._fila_actual().fila.id if self._fila_actual() else None),
                              incremental=True)
        if timeline == "notifications" and info.get("avisos"):
            for texto in info["avisos"][:3]:
                speak(texto, interrupt=False)
            if len(info["avisos"]) > 3:
                speak(_("{n} notificaciones más").format(n=len(info["avisos"]) - 3), interrupt=False)
            event_bus.emit("sound_trigger", "mastodon_new_notification")
        elif timeline == "home" and info.get("n"):
            event_bus.emit("sound_trigger", "mastodon_new_status")
        elif timeline == "direct" and info.get("n"):
            speak(_("Mensaje directo nuevo"), interrupt=False)
            event_bus.emit("sound_trigger", "mastodon_new_mention")
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _texto_aviso(notif: dict) -> str:
        from app.core.html_utils import html_a_texto
        cuenta = notif.get("account") or {}
        nombre = cuenta.get("display_name") or cuenta.get("username") or ""
        accion = _(datos_mastodon.TIPOS_NOTIFICACION.get(notif.get("type"), notif.get("type", "")))
        texto = f"{nombre} {accion}"
        estado = notif.get("status") or {}
        if estado.get("content") and notif.get("type") in ("mention", "status", "update", "quote"):
            texto += ": " + datos_mastodon._condensar(html_a_texto(estado["content"]), 140)
        return texto

    # ------------------------------------------------------------------ encuestas
    def _pedir_encuesta(self, obj, al_tener):
        x = obj.fila.extra
        dbid = self._estado_db_id(obj)

        def _fin(enc, err):
            if err or not enc:
                self.avisar(_("No se pudo leer la encuesta") if err else _("Esta publicación no tiene encuesta"))
                return
            self._encuestas[dbid] = enc
            al_tener(enc)
        mastodon_service.obtener_encuesta_async(x["account_id"], dbid, callback=_fin)

    @staticmethod
    def _texto_encuesta(enc: dict) -> str:
        total = max(1, sum(enc["votes"]))
        lineas = [_("Encuesta ({s}):").format(s=_("cerrada") if enc["expired"] else _("abierta"))]
        for t, v in zip(enc["options"], enc["votes"]):
            lineas.append(_("- {t}: {v} votos ({p} %)").format(t=t, v=v, p=round(100 * v / total)))
        lineas.append(_("{n} votos en total").format(n=enc["votes_count"]))
        if enc["voted"]:
            lineas.append(_("Ya has votado"))
        return "\n".join(lineas)

    def _anadir_encuesta_a_lectura(self, obj):
        """Añade el estado de la encuesta al final del texto abierto, cuando llega."""
        def _poner(enc):
            if self._articulo_abierto != obj.fila.id:
                return
            self.buffer.insert(self.buffer.get_end_iter(), "\n\n" + self._texto_encuesta(enc))
        self._pedir_encuesta(obj, _poner)

    def accion_votar(self):
        if self._tw_ctx():
            return self.tw_votar()
        if self.mx_votar():
            return
        obj = self._contexto_mastodon()
        if not obj:
            return
        if not obj.fila.extra.get("encuesta"):
            self.avisar(_("Esta publicación no tiene encuesta"))
            return

        def _tengo(enc):
            if enc["expired"] or enc["voted"]:
                self.avisar(self._texto_encuesta(enc).replace("\n", ". "))
                return
            from .dialogos import DialogoVotar

            def _votar(elegidas):
                def _fin(res, err):
                    if err:
                        self.avisar(_("No se pudo votar: {e}").format(e=err))
                        return
                    self.avisar(_("Voto registrado"))
                    self._anadir_encuesta_a_lectura(obj)
                mastodon_service.votar_encuesta_async(obj.fila.extra["account_id"], enc["id"], elegidas, callback=_fin)
            DialogoVotar(self, enc, _votar).present()
        self._pedir_encuesta(obj, _tengo)

    # ------------------------------------------------------------------ perfil, edición, cita, informe
    def _usuario_de(self, obj) -> str:
        x = obj.fila.extra
        return x.get("usuario") or x.get("de_usuario") or ""

    def accion_perfil(self):
        if self._bsky_ctx():
            return self.bsky_perfil()
        if self._ws_ctx():
            return self.ws_perfil()
        if self._tw_ctx():
            return self.tw_perfil()
        if self._rd_ctx():
            return self.rd_perfil()
        if self._pt_ctx():
            return self.pt_info_canal()
        if self._tg_ctx():
            return self.tg_info()
        if self._irc_ctx():
            return self.irc_info()
        if self._x_ctx():
            return self.x_info()
        if self._mx_ctx():
            return self.mx_info()
        obj = self._contexto_mastodon()
        if not obj:
            return
        usuario = self._usuario_de(obj)
        if not usuario:
            return
        from .dialogos import DialogoPerfil
        DialogoPerfil(self, obj.fila.extra["account_id"], usuario).present()

    def abrir_cronologia(self, account_id: int, acct: str):
        self._abrir_buffer(account_id, f"user:{acct.lstrip('@')}", _("Publicaciones de @{u}").format(u=acct.lstrip("@")))

    def accion_editar(self):
        if self._rd_ctx():
            return self.rd_editar()
        if self._pt_ctx():
            return self.pt_editar()
        if self._tg_ctx():
            return self.tg_editar()
        if self._x_ctx():
            return self.x_editar()
        if self._mx_ctx():
            return self.mx_editar()
        obj = self._contexto_mastodon()
        if not obj:
            return
        x = obj.fila.extra
        if not x.get("propio") or x.get("impulsado_por"):
            self.avisar(_("Solo puedes editar tus propias publicaciones"))
            return
        self.avisar(_("Leyendo la publicación original…"))

        def _fuente(res, err):
            if err or not res:
                self.avisar(_("No se pudo leer la publicación: {e}").format(e=err))
                return
            from .dialogos import DialogoPublicar
            DialogoPublicar(self, x["account_id"], texto_inicial=res["text"], aviso=res["spoiler_text"],
                            editar_db_id=self._estado_db_id(obj)).present()
        mastodon_service.obtener_fuente_toot_async(x["account_id"], self._estado_db_id(obj), _fuente)

    def accion_citar(self):
        if self._bsky_ctx():
            return self.bsky_citar()
        if self._ws_ctx():
            return self.ws_citar()
        if self._tw_ctx():
            return self.tw_citar()
        obj = self._contexto_mastodon()
        if not obj or not obj.fila.extra.get("status_id"):
            return
        x = obj.fila.extra
        from .dialogos import DialogoPublicar
        DialogoPublicar(self, x["account_id"], cita_id=x["status_id"]).present()

    def accion_reportar(self):
        obj = self._contexto_mastodon()
        if not obj or not obj.fila.extra.get("status_id"):
            return
        from .dialogos import DialogoReportar
        DialogoReportar(self, obj.fila.extra["account_id"], self._estado_db_id(obj), self._usuario_de(obj)).present()

    # ------------------------------------------------------------------ listas, hashtags y búsqueda
    def accion_abrir_lista(self):
        if self._bsky_ctx():
            return self.bsky_abrir_lista()
        if self._ws_ctx():
            return self.ws_abrir_lista()
        if self._tw_ctx():
            return self.tw_abrir_lista()
        if self._rd_ctx():
            return self.rd_ir_subreddit()
        if self._pt_ctx():
            return self.pt_ir_canal()
        acc = self._cuenta_activa()
        if acc is None:
            self.avisar(_("Selecciona una cuenta de Mastodon"))
            return
        self.avisar(_("Leyendo tus listas…"))

        def _fin(listas, err):
            if err:
                self.avisar(_("No se pudieron leer las listas: {e}").format(e=err))
                return
            if not listas:
                self.avisar(_("No tienes listas en esta cuenta"))
                return
            from .dialogos import DialogoElegir

            def _elegida(i):
                lista = listas[i]
                self._abrir_buffer(acc, f"list:{lista['id']}", _("Lista {t}").format(t=lista["title"]))
            DialogoElegir(self, _("Abrir una lista"), _("_Lista:"), [l["title"] for l in listas], _elegida,
                          _("_Abrir")).present()
        mastodon_service.obtener_listas_async(acc, callback=_fin)

    def accion_abrir_hashtag(self):
        if self._bsky_ctx():
            return self.bsky_buscar()
        if self._ws_ctx():
            return self.ws_buscar()
        if self._tw_ctx():
            return self.tw_buscar()
        if self._rd_ctx():
            return self.rd_buscar_subreddits()
        if self._pt_ctx():
            return self.pt_buscar_canales()
        acc = self._cuenta_activa()
        if acc is None:
            self.avisar(_("Selecciona una cuenta de Mastodon"))
            return
        from .dialogos import DialogoTexto

        def _tag(texto):
            tag = texto.strip().lstrip("#")
            if tag:
                self._abrir_buffer(acc, f"search:#{tag}", f"#{tag}")
        DialogoTexto(self, _("Abrir un hashtag"), _("_Hashtag:"), _tag, _("Sin la almohadilla, por ejemplo accesibilidad."),
                     _("_Abrir")).present()

    def accion_buscar_mastodon(self):
        if self._bsky_ctx():
            return self.bsky_buscar()
        if self._ws_ctx():
            return self.ws_buscar()
        if self._tw_ctx():
            return self.tw_buscar()
        if self._rd_ctx():
            return self.rd_buscar()
        if self._pt_ctx():
            return self.pt_buscar()
        acc = self._cuenta_activa()
        if acc is None:
            self.avisar(_("Selecciona una cuenta de Mastodon"))
            return
        from .dialogos import DialogoTexto

        def _buscar(texto):
            self._abrir_buffer(acc, f"search:{texto}", _("Búsqueda: {t}").format(t=texto))
        DialogoTexto(self, _("Buscar en Mastodon"), _("_Buscar:"), _buscar,
                     _("Busca publicaciones en tu instancia. Empieza por # para un hashtag."), _("_Buscar")).present()

    # ------------------------------------------------------------------ cuentas, borradores, filtros...
    def _cuenta_o_aviso(self):
        acc = self._cuenta_activa()
        if acc is None:
            self.avisar(_("Selecciona una cuenta de Mastodon"))
        return acc

    def _historial(self, nombre: str):
        from . import dialogos_ia
        getattr(dialogos_ia, nombre)(self).present()

    def accion_nuevo_hilo(self):
        """Un hilo largo: en Bluesky y W Social se divide en publicaciones encadenadas; en Mastodon, en mensajes."""
        if self._bsky_ctx() or self._ws_ctx():
            red = "bluesky" if self._bsky_ctx() else "wsocial"
            acc = self._bsky_cuenta() if red == "bluesky" else self._ws_cuenta()
            if acc is not None:
                from .dialogos_hilo import DialogoHiloLargo
                DialogoHiloLargo(self, acc, red).present()
            return
        self._dialogo_extra("DialogoHilo")

    def _dialogo_extra(self, nombre: str):
        """Abre uno de los diálogos de dialogos_mastodon_extra para la cuenta activa."""
        acc = self._cuenta_o_aviso()
        if acc is not None:
            from . import dialogos_mastodon_extra as extra
            getattr(extra, nombre)(self, acc).present()

    def abrir_cuentas(self, tipo: str):
        acc = self._cuenta_o_aviso()
        if acc is not None:
            from .dialogos_mastodon import DialogoCuentas
            DialogoCuentas(self, acc, tipo).present()

    def accion_interacciones(self, tipo: str):
        obj = self._contexto_mastodon()
        if not obj or not obj.fila.extra.get("status_id"):
            return
        from .dialogos_mastodon import DialogoCuentas
        DialogoCuentas(self, obj.fila.extra["account_id"], tipo, self._estado_db_id(obj)).present()

    def accion_historial(self):
        obj = self._contexto_mastodon()
        if not obj or not obj.fila.extra.get("status_id"):
            return
        x = obj.fila.extra

        def _fin(versiones, err):
            if err:
                self.avisar(_("No se pudo leer el historial: {e}").format(e=err))
                return
            from .dialogos_mastodon import DialogoHistorial
            DialogoHistorial(self, versiones or []).present()
        mastodon_service.obtener_historial_async(x["account_id"], self._estado_db_id(obj), callback=_fin)

    def accion_conversacion_silencio(self, silenciar: bool):
        obj = self._contexto_mastodon()
        if not obj or not obj.fila.extra.get("status_id"):
            return

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.avisar(_("Conversación silenciada") if silenciar else _("Conversación reactivada"))
        mastodon_service.silenciar_conversacion_async(obj.fila.extra["account_id"], self._estado_db_id(obj),
                                                      silenciar, callback=_fin)

    def accion_fijar(self, fijar: bool):
        obj = self._contexto_mastodon()
        if not obj:
            return
        x = obj.fila.extra
        if not x.get("propio") or x.get("impulsado_por"):
            self.avisar(_("Solo puedes fijar tus propias publicaciones"))
            return

        def _fin(res, err):
            self.avisar(_("No se pudo: {e}").format(e=err) if err else
                        (_("Publicación fijada en tu perfil") if fijar else _("Publicación desfijada")))
        mastodon_service.ejecutar_accion_toot_async(x["account_id"], self._estado_db_id(obj),
                                                    "pin" if fijar else "unpin", callback=_fin)

    def accion_borradores(self):
        acc = self._cuenta_o_aviso()
        if acc is not None:
            from .dialogos_mastodon import DialogoBorradores
            DialogoBorradores(self, acc).present()

    def abrir_borrador(self, draft_id: int):
        from app.services.mastodon import mastodon_borradores as mb
        from .dialogos import DialogoPublicar
        d = mb.obtener_borrador(draft_id)
        if not d:
            self.avisar(_("El borrador ya no existe"))
            return
        ctx = d["contexto"]
        DialogoPublicar(self, d["account_id"], responde_a=ctx.get("in_reply_to_id"), cita_id=ctx.get("cita_id"),
                        borrador=d["borrador"], borrador_id=draft_id).present()

    def accion_programadas(self):
        acc = self._cuenta_o_aviso()
        if acc is not None:
            from .dialogos_mastodon import DialogoProgramadas
            DialogoProgramadas(self, acc).present()

    def accion_filtros(self):
        acc = self._cuenta_o_aviso()
        if acc is not None:
            from .dialogos_mastodon import DialogoFiltros
            DialogoFiltros(self, acc).present()

    def accion_mi_perfil(self):
        acc = self._cuenta_o_aviso()
        if acc is not None:
            from .dialogos_mastodon import DialogoMiPerfil
            DialogoMiPerfil(self, acc).present()

    def abrir_hashtags(self, tipo: str):
        acc = self._cuenta_o_aviso()
        if acc is not None:
            from .dialogos_mastodon import DialogoHashtags
            DialogoHashtags(self, acc, tipo).present()

    def abrir_hashtag(self, account_id: int, nombre: str):
        self._abrir_buffer(account_id, f"search:#{nombre.lstrip('#')}", f"#{nombre.lstrip('#')}")

    def accion_seguir_hashtag(self, seguir: bool):
        n = self.nodo_actual
        if n is None or n.servicio != "mastodon" or not n.timeline.startswith("search:#"):
            self.avisar(_("Abre antes un hashtag"))
            return
        tag = n.timeline.split("#", 1)[1]

        def _fin(res, err):
            self.avisar(_("No se pudo: {e}").format(e=err) if err else
                        (_("Sigues #{t}: sus publicaciones llegarán a Inicio").format(t=tag) if seguir
                         else _("Has dejado de seguir #{t}").format(t=tag)))
        mastodon_service.seguir_hashtag_async(n.account_id, tag, seguir=seguir, callback=_fin)

    def accion_descartar_notificacion(self):
        obj = self._contexto_mastodon()
        if not obj or obj.fila.extra.get("tipo") != "notificacion":
            self.avisar(_("Selecciona una notificación"))
            return
        x = obj.fila.extra

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.cargar_lista(incremental=True)
            self.avisar(_("Notificación descartada"))
        mastodon_service.descartar_notificacion_async(x["account_id"], x["notificacion_id"], callback=_fin)

    def accion_limpiar_notificaciones(self):
        n = self.nodo_actual
        if n is None or n.servicio != "mastodon" or n.timeline not in ("notifications", "mentions"):
            self.avisar(_("Abre antes las notificaciones"))
            return
        from .dialogos import confirmar_async

        def _hacer():
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo: {e}").format(e=err))
                    return
                self.cargar_lista()
                self.avisar(_("Notificaciones borradas"))
            mastodon_service.limpiar_notificaciones_async(n.account_id, callback=_fin)
        confirmar_async(self, _("¿Borrar todas las notificaciones de esta cuenta?"), _hacer, etiqueta_si=_("Borrar"))

    # ------------------------------------------------------------------ menú contextual
    def mostrar_menu_contextual(self, x=None, y=None):
        if self._foco_en_arbol():
            return self.mostrar_menu_arbol()
        obj = self._fila_actual()
        if obj is None:
            return
        m = Gio.Menu()
        if obj.fila.servicio in ("bluesky", "wsocial"):
            a = Gio.Menu()
            for et, ac in ((_("Responder"), "win.responder"), (_("Citar"), "win.citar"),
                           (_("Me gusta / quitar me gusta"), "win.me-gusta"),
                           (_("Republicar / quitar republicación"), "win.impulsar"),
                           (_("Marcador / quitar marcador"), "win.marcador"),
                           (_("Traducir"), "win.traducir"), (_("Resumir con IA"), "win.resumir-ia"),
                           (_("Describir la imagen con IA"), "win.describir-imagen"),
                           (_("Extraer el texto de la imagen"), "win.ocr-imagen")):
                a.append(et, ac)
            m.append_section(None, a)
            b = Gio.Menu()
            for et, ac in ((_("Ver la conversación"), "win.conversacion"), (_("Perfil del autor"), "win.perfil"),
                           (_("Publicaciones del autor"), "win.cronologia-usuario"),
                           (_("Abrir en el navegador"), "win.navegador"), (_("Copiar el enlace"), "win.copiar-enlace")):
                b.append(et, ac)
            m.append_section(None, b)
            if obj.fila.extra.get("propio") and obj.fila.extra.get("tipo") != "notificacion":
                c = Gio.Menu()
                c.append(_("Borrar mi publicación"), "win.papelera")
                m.append_section(None, c)
        elif obj.fila.servicio == "telegram":
            if obj.fila.extra.get("tipo") == "chat":
                a = Gio.Menu()
                for et, ac in ((_("Abrir la conversación"), "win.abrir-chat"), (_("Nuevo mensaje"), "win.nuevo"),
                               (_("Silenciar o reactivar"), "win.tg-silenciar"), (_("Fijar o desfijar"), "win.tg-fijar"),
                               (_("Archivar"), "win.tg-archivar"), (_("Marcar como leído"), "win.tg-leido"),
                               (_("Marcar como no leído"), "win.tg-no-leido"), (_("Información del chat"), "win.perfil"),
                               (_("Salir del chat"), "win.papelera")):
                    a.append(et, ac)
                m.append_section(None, a)
            else:
                a = Gio.Menu()
                for et, ac in ((_("Responder"), "win.responder"), (_("Reaccionar…"), "win.tg-reaccionar"),
                               (_("Reenviar…"), "win.tg-reenviar"), (_("Copiar el texto"), "win.tg-copiar"),
                               (_("Traducir"), "win.traducir"), (_("Resumir con IA"), "win.resumir-ia")):
                    a.append(et, ac)
                m.append_section(None, a)
                b = Gio.Menu()
                for et, ac in ((_("Reproducir"), "win.reproducir"), (_("Descargar el adjunto"), "win.tg-descargar"),
                               (_("Abrir el adjunto"), "win.tg-abrir-adjunto"),
                               (_("Quién ha reaccionado"), "win.tg-quien-reacciono")):
                    b.append(et, ac)
                m.append_section(None, b)
                if obj.fila.extra.get("propio"):
                    c = Gio.Menu()
                    c.append(_("Editar el mensaje"), "win.editar")
                    c.append(_("Borrar el mensaje"), "win.papelera")
                    m.append_section(None, c)
        elif obj.fila.servicio == "irc":
            a = Gio.Menu()
            if obj.fila.extra.get("tipo") == "chat":
                chat = (((_("Abrir la conversación"), "win.abrir-chat"), (_("Escribir en el chat"), "win.nuevo"),
                         (_("Usuarios del canal"), "win.irc-usuarios"), (_("Información del chat"), "win.irc-info-canal"),
                         (_("Nivel de aviso"), "win.irc-nivel-aviso"), (_("Marcar como leído"), "win.irc-leido"),
                         (_("Salir del canal"), "win.irc-salir"), (_("Eliminar el chat"), "win.papelera")))
            else:
                chat = (((_("Responder mencionando"), "win.responder"), (_("Datos del usuario"), "win.perfil"),
                         (_("Copiar el texto"), "win.irc-copiar"), (_("Traducir"), "win.traducir"),
                         (_("Resumir con IA"), "win.resumir-ia")))
            for et, ac in chat:
                a.append(et, ac)
            m.append_section(None, a)
        elif obj.fila.servicio == "xmpp":
            if obj.fila.extra.get("tipo") == "solicitud":
                lista = ((_("Aceptar"), "win.x-aceptar"), (_("Rechazar"), "win.x-rechazar"))
            elif obj.fila.extra.get("tipo") == "chat":
                lista = ((_("Abrir la conversación"), "win.abrir-chat"), (_("Escribir"), "win.nuevo"),
                         (_("Información"), "win.perfil"), (_("Fijar o desfijar"), "win.x-fijar"),
                         (_("Silenciar o reactivar"), "win.x-silenciar"), (_("Archivar o desarchivar"), "win.x-archivar"),
                         (_("Nivel de aviso"), "win.x-nivel-aviso"), (_("Marcar como leído"), "win.x-leido"),
                         (_("Marcar como no leído"), "win.x-no-leido"), (_("Quitar o salir"), "win.papelera"))
            else:
                lista = ((_("Responder"), "win.responder"), (_("Reaccionar…"), "win.x-reaccionar"),
                         (_("Copiar el texto"), "win.x-copiar"), (_("Destacar"), "win.favorito"),
                         (_("Reproducir"), "win.reproducir"), (_("Descargar el adjunto"), "win.x-descargar"),
                         (_("Abrir el adjunto"), "win.x-abrir-adjunto"), (_("Traducir"), "win.traducir"),
                         (_("Resumir con IA"), "win.resumir-ia"), (_("Retirar el mensaje"), "win.papelera"))
            a = Gio.Menu()
            for et, ac in lista:
                a.append(et, ac)
            m.append_section(None, a)
        elif obj.fila.servicio == "x":
            a = Gio.Menu()
            for et, ac in self.tw_menu_contextual(obj):
                a.append(et, ac)
            m.append_section(None, a)
        elif obj.fila.servicio == "reddit":
            a = Gio.Menu()
            for et, ac in self.rd_menu_contextual(obj):
                a.append(et, ac)
            m.append_section(None, a)
        elif obj.fila.servicio == "peertube":
            a = Gio.Menu()
            for et, ac in self.pt_menu_contextual(obj):
                a.append(et, ac)
            m.append_section(None, a)
        elif obj.fila.servicio == "matrix":
            a = Gio.Menu()
            for et, ac in self.mx_menu_contextual(obj):
                a.append(et, ac)
            m.append_section(None, a)
        elif obj.fila.servicio == "mastodon":
            a = Gio.Menu()
            for et, ac in ((_("Responder"), "win.responder"), (_("Citar"), "win.citar"),
                           (_("Traducir"), "win.traducir"), (_("Resumir con IA"), "win.resumir-ia"),
                           (_("Describir la imagen con IA"), "win.describir-imagen"),
                           (_("Extraer el texto de la imagen"), "win.ocr-imagen"),
                           (_("Me gusta / quitar me gusta"), "win.me-gusta"),
                           (_("Impulsar / quitar impulso"), "win.impulsar"),
                           (_("Marcador / quitar marcador"), "win.marcador")):
                a.append(et, ac)
            m.append_section(None, a)
            b = Gio.Menu()
            for et, ac in ((_("Ver la conversación"), "win.conversacion"),
                           (_("Perfil del autor"), "win.perfil"),
                           (_("Publicaciones del autor"), "win.cronologia-usuario"),
                           (_("Votar en la encuesta"), "win.votar"),
                           (_("Abrir en el navegador"), "win.navegador"), (_("Copiar el enlace"), "win.copiar-enlace")):
                b.append(et, ac)
            m.append_section(None, b)
            m2 = Gio.Menu()
            for et, ac in ((_("Quién la ha impulsado"), "win.quien-impulso"),
                           (_("Quién la ha marcado como favorita"), "win.quien-favorito"),
                           (_("Historial de ediciones"), "win.historial"),
                           (_("Silenciar la conversación"), "win.silenciar-conversacion"),
                           (_("Reactivar la conversación"), "win.reactivar-conversacion")):
                m2.append(et, ac)
            m.append_section(None, m2)
            c = Gio.Menu()
            if obj.fila.extra.get("propio"):
                c.append(_("Editar mi publicación"), "win.editar")
                c.append(_("Fijar en mi perfil"), "win.fijar")
                c.append(_("Desfijar de mi perfil"), "win.desfijar")
                c.append(_("Borrar mi publicación"), "win.papelera")
            else:
                c.append(_("Reportar"), "win.reportar")
            if obj.fila.extra.get("tipo") == "notificacion":
                c.append(_("Descartar la notificación"), "win.descartar-notificacion")
            m.append_section(None, c)
        else:
            a = Gio.Menu()
            for et, ac in ((_("Traducir"), "win.traducir"), (_("Resumir con IA"), "win.resumir-ia"),
                           (_("Leído / no leído"), "win.leido"), (_("Destacar"), "win.favorito"),
                           (_("Leer más tarde"), "win.mas-tarde"), (_("A la papelera"), "win.papelera"),
                           (_("Abrir en el navegador"), "win.navegador"), (_("Copiar el enlace"), "win.copiar-enlace"),
                           (_("Reproducir el audio"), "win.reproducir-articulo")):
                a.append(et, ac)
            m.append_section(None, a)
        pop = Gtk.PopoverMenu.new_from_model(m)
        pop.set_parent(self.vista_lista)
        pop.set_has_arrow(False)
        if x is not None:
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
            pop.set_pointing_to(r)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def _player(self):
        from app.services.media_player.player2_service import player2_service
        return player2_service

    def accion_reproducir_articulo(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "rss":
            return
        self.avisar(_("Cargando el audio…"))

        def _fin(ok, err=None):
            if ok:
                self.avisar(_("Reproduciendo: {t}").format(t=obj.fila.titulo))
            else:
                self.avisar(err or _("No se pudo reproducir"))
        rss_service.reproducir_articulo_audio(obj.fila.id, callback=_fin)

    def accion_reproducir_pausa(self):
        if self.tg_reproducir() or self.x_reproducir() or self.mx_reproducir() or self.pt_reproducir_f2():
            return
        p = self._player()
        if p.esta_activo():
            p.alternar_pausa()
            est = p.backend.obtener_estado()
            self.avisar(_("Pausado") if est == "pausado" else _("Reproduciendo"))
        else:
            obj = self._fila_actual()
            if obj is not None and obj.fila.servicio == "rss" and obj.fila.audio:
                self.accion_reproducir_articulo()
            else:
                self.avisar(_("No hay nada reproduciéndose"))

    def accion_saltar(self, signo: int):
        p = self._player()
        if not p.esta_activo():
            self.avisar(_("No hay nada reproduciéndose"))
            return
        paso = float(config.get("media_player", "default_step_forward2" if signo > 0 else "default_step_backward2", 15.0))
        (p.avanzar_segundos if signo > 0 else p.retroceder_segundos)(paso)
        self.avisar(self._tiempo(p), interrumpir=True)

    def accion_detener(self):
        p = self._player()
        if p.esta_activo():
            p.detener()
            self.avisar(_("Detenido"))
        else:
            self.avisar(_("No hay nada reproduciéndose"))

    def accion_volumen(self, delta: float):
        p = self._player()
        vol = max(0.0, min(1.0, p.backend.obtener_volumen() + delta))
        p.establecer_volumen(vol)
        self.avisar(_("Volumen {n} por ciento").format(n=round(vol * 100)))

    def accion_velocidad(self, delta: float):
        p = self._player()
        vel = max(0.5, min(3.0, round(p.backend.obtener_velocidad() + delta, 2)))
        p.establecer_velocidad(vel)
        self.avisar(_("Velocidad {v}").format(v=vel))

    @staticmethod
    def _mm_ss(seg: float) -> str:
        seg = int(max(0, seg))
        h, r = divmod(seg, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _tiempo(self, p) -> str:
        info = p.obtener_informacion()
        if info["duration"]:
            return _("{a} de {b}").format(a=self._mm_ss(info["position"]), b=self._mm_ss(info["duration"]))
        return self._mm_ss(info["position"])

    def accion_decir_estado(self):
        speak(self.estado.get_text(), interrupt=True)

    def _latido_reproductor(self):
        """Muestra en la barra de estado lo que suena; no lo dice (para no interrumpir al lector)."""
        try:
            p = self._player()
            if p.esta_activo() and p.current_title:
                info = p.obtener_informacion()
                est = {"reproduciendo": _("Reproduciendo"), "pausado": _("Pausado"),
                       "esperando": _("Cargando")}.get(info["state"], "")
                if est:
                    self.estado.set_text(f"{est}: {p.current_title} — {self._tiempo(p)}")
        except Exception:
            pass
        return GLib.SOURCE_CONTINUE
