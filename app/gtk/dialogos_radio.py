# -*- coding: utf-8 -*-
"""Radio por internet en la interfaz GTK: emisoras por país, favoritos con categorías, buscador e historial, con una barra
de reproducción (detener, recargar, silenciar, volumen). Usa el mismo servicio y los mismos favoritos que la versión de
Windows y suena por el segundo reproductor (respeta la prioridad del audio de las redes)."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from app.core.speech import copiar_al_portapapeles, notificar
from app.core.task_manager import task_manager
from app.services.media_player.player2_service import KIND_RADIO, player2_service
from app.services.radio.radio_service import radio_service

from .dialogos import DialogoBase, DialogoElegir, DialogoTexto, confirmar_async
from .dialogos_mastodon import DialogoLista, ListaSimple

log = logging.getLogger(__name__)

# Países habituales: nombre visible y código ISO. «TOP» son las emisoras más populares del mundo.
PAISES = [
    (_("Todas las populares"), "TOP"), (_("España"), "ES"), (_("México"), "MX"), (_("Argentina"), "AR"),
    (_("Colombia"), "CO"), (_("Chile"), "CL"), (_("Perú"), "PE"), (_("Venezuela"), "VE"), (_("Ecuador"), "EC"),
    (_("Uruguay"), "UY"), (_("Estados Unidos"), "US"), (_("Reino Unido"), "GB"), (_("Francia"), "FR"),
    (_("Alemania"), "DE"), (_("Italia"), "IT"), (_("Portugal"), "PT"), (_("Brasil"), "BR"),
]


def _texto_emisora(e: dict) -> str:
    partes = [e.get("name", "") or e.get("url", "")]
    if e.get("country"):
        partes.append(f"({e['country']})")
    if e.get("bitrate"):
        partes.append(f"[{e['bitrate']} kbps]")
    return " ".join(partes)


def _entrada(valor: str = "", ancho: int = 30) -> Gtk.Entry:
    return Gtk.Entry(text=valor, width_chars=ancho, hexpand=True)


class _Pagina(Gtk.Box):
    """Una pestaña: cabecera propia, lista de emisoras y las acciones comunes. Intro reproduce; Espacio pausa."""

    def __init__(self, dlg, nombre: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(self, f"set_margin_{m}")(8)
        self.dlg = dlg
        self.estaciones = []
        self.cabecera()
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        self.append(self.estado)
        self.lista = ListaSimple(nombre, lambda pos: self.reproducir())
        self.append(self.lista)
        ctl = Gtk.EventControllerKey()
        ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._tecla)
        self.lista.vista.add_controller(ctl)
        fila = Gtk.Box(spacing=8)
        self.fila_botones = fila
        for et, fn in ((_("_Reproducir"), self.reproducir), (_("A_ñadir o quitar de favoritos"), self.favorito),
                       (_("_Información"), self.info), (_("_Votar"), self.votar), (_("_Copiar la URL"), self.copiar)):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            fila.append(b)
        self.botones_extra(fila)
        self.append(fila)

    def cabecera(self):
        """Las pestañas añaden aquí sus controles."""

    def botones_extra(self, fila):
        """Las pestañas añaden aquí sus botones."""

    def _tecla(self, ctl, keyval, keycode, estado):
        if keyval == Gdk.KEY_space:
            self.dlg.alternar_pausa()
            return True
        return False

    def mostrar(self, estaciones):
        self.estaciones = estaciones or []
        self.lista.poner([_texto_emisora(e) for e in self.estaciones])
        self.estado.set_text(_("{0} emisoras.").format(len(self.estaciones)) if self.estaciones else _("Sin resultados."))
        notificar(self.estado.get_text())
        if self.estaciones:
            self.lista.enfocar()

    def actual(self):
        p = self.lista.posicion()
        return self.estaciones[p] if 0 <= p < len(self.estaciones) else None

    def reproducir(self):
        e = self.actual()
        if e:
            self.dlg.reproducir_estacion(e)

    def favorito(self):
        e = self.actual()
        if e:
            self.dlg.alternar_favorito(e, self)

    def info(self):
        e = self.actual()
        if e:
            self.dlg.info_estacion(e)

    def votar(self):
        e = self.actual()
        if e:
            self.dlg.votar(e)

    def copiar(self):
        e = self.actual()
        if e and e.get("url"):
            copiar_al_portapapeles(e["url"], _("URL copiada."))

    def al_mostrar(self):
        """Se llama al entrar en la pestaña."""


class _General(_Pagina):
    def cabecera(self):
        fila = Gtk.Box(spacing=8)
        self.pais = Gtk.DropDown.new_from_strings([n for n, _c in PAISES])
        self.pais.set_selected(1)       # España
        lbl = Gtk.Label(label=_("_País:"), use_underline=True)
        lbl.set_mnemonic_widget(self.pais)
        b = Gtk.Button(label=_("_Cargar emisoras"), use_underline=True)
        b.connect("clicked", lambda *_a: self.cargar())
        for w in (lbl, self.pais, b):
            fila.append(w)
        self.append(fila)

    def cargar(self):
        codigo = PAISES[self.pais.get_selected()][1]
        self.estado.set_text(_("Cargando emisoras…"))

        def _bg(ctx):
            return radio_service.api.top(1000) if codigo == "TOP" else radio_service.api.buscar(country=codigo)

        def _fin(res, err):
            if err:
                self.estado.set_text(_("No se pudieron cargar las emisoras: {0}").format(err))
                return
            self.mostrar(res)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def al_mostrar(self):
        if not self.estaciones:
            self.cargar()


class _Buscador(_Pagina):
    def cabecera(self):
        def campo(etiqueta, ancho):
            e = _entrada(ancho=ancho)
            e.connect("activate", lambda *_a: self.buscar())
            lbl = Gtk.Label(label=etiqueta, use_underline=True)
            lbl.set_mnemonic_widget(e)
            f = Gtk.Box(spacing=8)
            f.append(lbl)
            f.append(e)
            return f, e
        f1, self.nombre = campo(_("_Nombre:"), 30)
        f2, self.genero = campo(_("_Género:"), 16)
        f3, self.idioma = campo(_("_Idioma:"), 14)
        b = Gtk.Button(label=_("_Buscar"), use_underline=True)
        b.connect("clicked", lambda *_a: self.buscar())
        self.append(f1)
        fila = Gtk.Box(spacing=8)
        for w in (f2, f3, b):
            fila.append(w)
        self.append(fila)

    def buscar(self):
        nombre, genero, idioma = (e.get_text().strip() for e in (self.nombre, self.genero, self.idioma))
        if not (nombre or genero or idioma):
            notificar(_("Escribe al menos un criterio de búsqueda."))
            return
        self.estado.set_text(_("Buscando…"))

        def _fin(res, err):
            if err:
                self.estado.set_text(_("Error en la búsqueda: {0}").format(err))
                return
            self.mostrar(res)
        task_manager.start_task(lambda ctx: radio_service.api.buscar(name=nombre, tag=genero, language=idioma),
                                callback=_fin, pass_context=True)


class _Favoritos(_Pagina):
    def cabecera(self):
        fila = Gtk.Box(spacing=8)
        for et, fn in ((_("_Actualizar la lista"), lambda: self.cargar()), (_("_Nuevo favorito…"), lambda: self.nuevo()),
                       (_("Cate_gorías…"), lambda: self.dlg.gestionar_categorias())):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            fila.append(b)
        self.append(fila)

    def botones_extra(self, fila):
        for et, fn in ((_("_Editar"), self.editar), (_("E_liminar"), self.eliminar)):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            fila.append(b)

    def cargar(self):
        self.mostrar(radio_service.obtener_favoritos())

    def al_mostrar(self):
        self.cargar()

    def _categoria(self, titulo: str, con_mantener: bool, al_elegir):
        """Pregunta la categoría; entrega None (sin categoría), el id, o -1 (no cambiar)."""
        cats = radio_service.obtener_categorias()
        if not cats:
            al_elegir(-1 if con_mantener else None)
            return
        opciones = ([_("(No cambiar)")] if con_mantener else []) + [_("Sin categoría")] + [c["name"] for c in cats]

        def _hecho(i):
            if con_mantener:
                al_elegir(-1 if i == 0 else None if i == 1 else cats[i - 2]["id"])
            else:
                al_elegir(None if i == 0 else cats[i - 1]["id"])
        DialogoElegir(self.dlg, titulo, _("_Categoría:"), opciones, _hecho).present()

    def nuevo(self):
        d = DialogoBase(self.dlg, _("Nuevo favorito"))
        url = d.campo(_("_URL del stream:"), Gtk.Entry(activates_default=True, width_chars=50))
        nombre = d.campo(_("_Nombre:"), Gtk.Entry(activates_default=True, width_chars=50))

        def _aceptar():
            u = url.get_text().strip()
            if not u:
                notificar(_("Tienes que indicar la URL del stream."))
                return
            n = nombre.get_text().strip() or u
            d.close()

            def _con(cat):
                radio_service.anadir_favorito({"name": n, "url": u, "uuid": ""}, cat)
                notificar(_("Favorito añadido."))
                self.cargar()
            self._categoria(_("Nuevo favorito"), False, _con)
        d.aceptar = _aceptar
        d.set_focus(url)
        d.present()

    def editar(self):
        e = self.actual()
        if not e or not e.get("id"):
            return
        d = DialogoBase(self.dlg, _("Editar favorito"))
        nombre = d.campo(_("_Nombre:"), Gtk.Entry(text=e.get("name", ""), activates_default=True, width_chars=50))
        url = d.campo(_("_URL del stream:"), Gtk.Entry(text=e.get("url", ""), activates_default=True, width_chars=50))

        def _aceptar():
            n, u = nombre.get_text().strip(), url.get_text().strip()
            d.close()

            def _con(cat):
                radio_service.editar_favorito(e["id"], name=n or None, url=u or None, category_id=cat)
                notificar(_("Favorito actualizado."))
                self.cargar()
            self._categoria(_("Editar favorito"), True, _con)
        d.aceptar = _aceptar
        d.set_focus(nombre)
        d.present()

    def eliminar(self):
        e = self.actual()
        if not e or not e.get("id"):
            return

        def _hacer():
            radio_service.quitar_favorito(e["id"])
            notificar(_("Eliminado de favoritos."))
            self.cargar()
        confirmar_async(self.dlg, _("¿Eliminar «{0}» de favoritos?").format(e.get("name", "")), _hacer,
                        etiqueta_si=_("Eliminar"))


class _Historial(_Pagina):
    def cabecera(self):
        fila = Gtk.Box(spacing=8)
        for et, fn in ((_("_Actualizar la lista"), lambda: self.cargar()), (_("_Borrar el historial"), lambda: self.borrar())):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            fila.append(b)
        self.append(fila)

    def cargar(self):
        self.mostrar(radio_service.obtener_historial())

    def al_mostrar(self):
        self.cargar()

    def borrar(self):
        def _hacer():
            radio_service.borrar_historial()
            self.cargar()
            notificar(_("Historial borrado."))
        confirmar_async(self.dlg, _("¿Borrar todo el historial de radio?"), _hacer, etiqueta_si=_("Borrar"))


class DialogoRadio(Gtk.Window):
    def __init__(self, padre):
        super().__init__(title=_("Radio por internet"), transient_for=padre, modal=True)
        self.padre = padre
        self.set_default_size(820, 600)
        self._ultima = None
        self._silenciado = False
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(10)
        self.set_child(caja)
        self.libro = Gtk.Notebook(vexpand=True)
        self.paginas = [(_General(self, _("Lista de emisoras")), _("General")),
                        (_Favoritos(self, _("Lista de emisoras favoritas")), _("Favoritos")),
                        (_Buscador(self, _("Resultados de la búsqueda")), _("Buscador")),
                        (_Historial(self, _("Historial de emisoras")), _("Historial"))]
        for pagina, titulo in self.paginas:
            self.libro.append_page(pagina, Gtk.Label(label=titulo))
        self.libro.connect("switch-page", lambda lib, pagina, n: pagina.al_mostrar())
        caja.append(self.libro)

        barra = Gtk.Box(spacing=8)
        self.b_detener = Gtk.Button(label=_("_Detener"), use_underline=True)
        self.b_detener.connect("clicked", lambda *_a: self.detener())
        self.b_recargar = Gtk.Button(label=_("_Recargar"), use_underline=True)
        self.b_recargar.connect("clicked", lambda *_a: self.recargar())
        self.b_silencio = Gtk.Button(label=_("_Silenciar"), use_underline=True)
        self.b_silencio.connect("clicked", lambda *_a: self.silenciar())
        vol = int(player2_service.obtener_informacion()["volume"] * 100)
        self.volumen = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.volumen.set_value(vol)
        self.volumen.set_hexpand(True)
        self.volumen.update_property([Gtk.AccessibleProperty.LABEL], [_("Volumen")])
        self.volumen.connect("value-changed", lambda s: player2_service.establecer_volumen(s.get_value() / 100.0))
        for w in (self.b_detener, self.b_recargar, self.b_silencio, self.volumen):
            barra.append(w)
        caja.append(barra)
        cerrar = Gtk.Button(label=_("_Cerrar"), use_underline=True, halign=Gtk.Align.END)
        cerrar.connect("clicked", lambda *_a: self.close())
        caja.append(cerrar)

        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.paginas[0][0].al_mostrar()

    def present(self):
        super().present()
        self.paginas[0][0].lista.enfocar()

    # ---- acciones compartidas
    def reproducir_estacion(self, e: dict):
        def _poner():
            self._ultima = e
            self._silenciado = False
            self.b_silencio.set_label(_("_Silenciar"))
            radio_service.reproducir(e)
            notificar(_("Reproduciendo: {0}").format(e.get("name", "")))
        if (player2_service.esta_activo() and not (player2_service.current_kind == KIND_RADIO
                                                    and player2_service.current_url == e.get("url"))):
            confirmar_async(self, _("Ya se está reproduciendo {0}. ¿Detenerlo y poner esta emisora?").format(
                player2_service.descripcion_actual() or _("otro audio")), _poner, titulo=_("Radio"),
                etiqueta_si=_("Cambiar"))
        else:
            _poner()

    def alternar_pausa(self):
        player2_service.alternar_pausa()

    def detener(self):
        if player2_service.current_kind == KIND_RADIO and player2_service.esta_activo():
            player2_service.detener()
            notificar(_("Radio detenida."))

    def recargar(self):
        if self._ultima:
            radio_service.reproducir(self._ultima)
            notificar(_("Recargando: {0}").format(self._ultima.get("name", "")))

    def silenciar(self):
        if player2_service.current_kind != KIND_RADIO or not player2_service.esta_activo():
            return
        self._silenciado = not self._silenciado
        if self._silenciado:
            player2_service.backend.establecer_volumen(0.0)
            self.b_silencio.set_label(_("Activar _sonido"))
            notificar(_("Silenciado."))
        else:
            player2_service.establecer_volumen(self.volumen.get_value() / 100.0)
            self.b_silencio.set_label(_("_Silenciar"))
            notificar(_("Sonido activado."))

    def alternar_favorito(self, e: dict, pagina):
        url = e.get("url", "")
        if radio_service.es_favorito(url):
            radio_service.quitar_favorito_por_url(url)
            notificar(_("Quitada de favoritos."))
            if isinstance(pagina, _Favoritos):
                pagina.cargar()
            return
        cats = radio_service.obtener_categorias()

        def _anadir(cat):
            radio_service.anadir_favorito(e, cat)
            notificar(_("Añadida a favoritos."))
            if isinstance(pagina, _Favoritos):
                pagina.cargar()
        if not cats:
            _anadir(None)
            return
        opciones = [_("Sin categoría")] + [c["name"] for c in cats]
        DialogoElegir(self, _("Añadir a favoritos"), _("_Categoría:"), opciones,
                      lambda i: _anadir(None if i == 0 else cats[i - 1]["id"])).present()

    def info_estacion(self, e: dict):
        from .dialogos_mastodon_extra import DialogoTextoLargo
        DialogoTextoLargo(self, _("Información de la emisora"), _(
            "{0}\n\nPaís: {1}\nIdioma: {2}\nGénero: {3}\nCalidad: {4} kbps ({5})\nURL: {6}").format(
            e.get("name", ""), e.get("country", "") or _("desconocido"), e.get("language", "") or _("desconocido"),
            e.get("tags", "") or _("—"), e.get("bitrate", 0), e.get("codec", "") or _("—"), e.get("url", ""))).present()

    def votar(self, e: dict):
        uuid = e.get("uuid")
        if not uuid:
            notificar(_("Esta emisora no se puede votar."))
            return
        notificar(_("Votando…"))
        task_manager.start_task(lambda ctx: radio_service.api.votar(uuid),
                                callback=lambda res, err: notificar(_("Voto registrado.") if res and not err
                                                                    else _("No se pudo votar.")),
                                pass_context=True)

    def gestionar_categorias(self):
        DialogoCategoriasRadio(self).present()


class DialogoCategoriasRadio(DialogoLista):
    """Categorías para ordenar las emisoras favoritas."""

    def __init__(self, padre):
        super().__init__(padre, _("Categorías de favoritos"), _("Lista de categorías"))
        self.cats = []
        self.boton(_("_Crear una categoría"), self._crear)
        self.boton(_("_Borrar la categoría"), self._borrar)
        self.cerrar_boton()
        self._pintar()

    def _pintar(self):
        self.cats = radio_service.obtener_categorias()
        self.lista.poner([c["name"] for c in self.cats])
        self.mostrar_estado(_("{0} categorías").format(len(self.cats)) if self.cats else _("No hay categorías."))

    def _crear(self):
        def _nombre(texto):
            radio_service.crear_categoria(texto)
            notificar(_("Categoría creada."))
            self._pintar()
        DialogoTexto(self, _("Nueva categoría"), _("_Nombre de la categoría:"), _nombre, aceptar=_("_Crear")).present()

    def _borrar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.cats):
            return
        c = self.cats[p]

        def _hacer():
            radio_service.borrar_categoria(c["id"])
            notificar(_("Categoría borrada."))
            self._pintar()
        confirmar_async(self, _("¿Borrar la categoría «{0}»?").format(c["name"]), _hacer, etiqueta_si=_("Borrar"))
