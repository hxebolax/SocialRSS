# -*- coding: utf-8 -*-
"""Complementos (plugins) en la interfaz GTK: el gestor para activarlos, desactivarlos, configurarlos y usarlos, y las
ventanas de los complementos que se entregan con la aplicación (feeds populares, suscripciones de YouTube, web scraper y
configuración de anti-clickbait). Los complementos son los mismos que usa la versión de Windows."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.database import db
from app.core.plugins import plugin_manager
from app.core.speech import notificar
from app.core.task_manager import task_manager

from .dialogos import DialogoBase, DialogoElegir, DialogoTexto
from .dialogos_mastodon import DialogoLista

log = logging.getLogger(__name__)


def cuenta_rss(ventana) -> int:
    """La cuenta de RSS en uso (o la primera activa)."""
    acc = ventana.get_application().account_id
    if acc:
        return acc
    fila = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1 ORDER BY position, id LIMIT 1")
    return fila[0] if fila else None


# --------------------------------------------------------------------------- gestor
class DialogoComplementos(DialogoLista):
    """Complementos descubiertos en data/plugins: Intro los activa o desactiva."""

    def __init__(self, padre):
        super().__init__(padre, _("Complementos"), _("Lista de complementos"), al_activar=lambda pos: self._alternar())
        plugin_manager.descubrir()
        self.plugins = []
        self.descripcion = Gtk.Label(xalign=0, wrap=True, selectable=True, width_chars=60, max_width_chars=60)
        self.descripcion.update_property([Gtk.AccessibleProperty.LABEL], [_("Descripción del complemento")])
        self.get_child().insert_child_after(self.descripcion, self.lista)
        self.lista.seleccion.connect("notify::selected", lambda *_a: self._mostrar_descripcion())
        self.boton(_("_Activar / desactivar"), self._alternar)
        self.b_usar = self.boton(_("_Usar…"), self._usar)
        self.b_config = self.boton(_("_Configurar…"), self._configurar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.plugins = plugin_manager.listar()
        if not self.plugins:
            self.lista.poner([])
            self.mostrar_estado(_("No hay complementos instalados. Coloca complementos en la carpeta data/plugins."))
            return
        self.lista.poner([
            "{0}. {1}. v{2}. {3}".format(
                p.info.name, _("Activado") if p.activo else (_("Error") if p.error else _("Desactivado")),
                p.info.version, p.info.author or "").rstrip(". ") for p in self.plugins])
        self.lista.seleccion.set_selected(min(pos, len(self.plugins) - 1))
        self.mostrar_estado(_("{0} complementos").format(len(self.plugins)))
        self._mostrar_descripcion()

    def _actual(self):
        p = self.lista.posicion()
        return self.plugins[p] if 0 <= p < len(self.plugins) else None

    def _mostrar_descripcion(self):
        p = self._actual()
        if p is None:
            return
        partes = [p.info.description or ""]
        if p.error:
            partes.append(_("Error: {0}").format(p.error))
        if p.info.website:
            partes.append(p.info.website)
        self.descripcion.set_text("\n".join(x for x in partes if x))
        self.b_usar.set_sensitive(bool(p.activo and self._entradas(p)))
        self.b_config.set_sensitive(bool(p.activo and p.info.has_settings))

    @staticmethod
    def _entradas(p) -> list:
        return [(etiqueta, cb) for pid, etiqueta, cb in plugin_manager.menu_items() if pid == p.info.id]

    def _alternar(self):
        p = self._actual()
        if p is None:
            return
        pos = self.lista.posicion()
        if p.activo:
            plugin_manager.desactivar(p.info.id)
            notificar(_("Complemento desactivado: {0}").format(p.info.name))
        elif plugin_manager.activar(p.info.id):
            notificar(_("Complemento activado: {0}").format(p.info.name))
        else:
            notificar(_("No se pudo activar: {0}").format(p.error or p.info.name))
        self._cargar(pos)
        self.lista.enfocar()

    def _usar(self):
        p = self._actual()
        entradas = self._entradas(p) if p else []
        if not entradas:
            return
        if len(entradas) == 1:
            self._ejecutar(entradas[0][1])
            return
        DialogoElegir(self, p.info.name, _("_Acción:"), [e for e, _c in entradas],
                      lambda i: self._ejecutar(entradas[i][1]), _("_Abrir")).present()

    def _ejecutar(self, callback):
        try:
            callback()
        except Exception as e:
            log.warning("El complemento falló: %s", e, exc_info=True)
            notificar(_("El complemento no se pudo abrir: {0}").format(e))

    def _configurar(self):
        p = self._actual()
        if not (p and p.activo and p.instancia and p.info.has_settings):
            notificar(_("Este complemento no tiene configuración o no está activado."))
            return
        abrir = getattr(p.instancia, "get_settings_dialog_gtk", None)
        if abrir is None:
            notificar(_("La configuración de este complemento solo está disponible en la versión de Windows."))
            return
        try:
            d = abrir(self)
            if d is not None:
                d.present()
        except Exception as e:
            notificar(_("Error al abrir la configuración: {0}").format(e))


# --------------------------------------------------------------------------- anti-clickbait
class DialogoAjustesClickbait(DialogoBase):
    ACCIONES = ["avisar", "leido", "ocultar"]

    def __init__(self, padre, plugin):
        super().__init__(padre, _("Configurar: {0}").format("Anti-clickbait"), _("_Guardar"))
        self.plugin = plugin
        self.umbral = self.campo(_("_Sensibilidad (umbral de puntuación, 1 = muy sensible):"),
                                 Gtk.SpinButton.new_with_range(1, 10, 1))
        self.umbral.set_value(plugin._umbral)
        self.accion = self.campo(_("¿Qué hacer con los titulares _clickbait?"), Gtk.DropDown.new_from_strings([
            _("Avisar (anteponer un símbolo al título)"), _("Marcarlos como leídos"), _("Ocultarlos (no añadirlos)")]))
        self.accion.set_selected(self.ACCIONES.index(plugin._accion) if plugin._accion in self.ACCIONES else 0)
        self.set_focus(self.umbral)

    def aceptar(self):
        self.plugin._umbral = int(self.umbral.get_value())
        self.plugin._accion = self.ACCIONES[self.accion.get_selected()]
        self.plugin.api.save_config({"umbral": self.plugin._umbral, "accion": self.plugin._accion})
        notificar(_("Configuración de Anti-clickbait guardada."))
        self.close()


# --------------------------------------------------------------------------- feeds populares
class DialogoFeedsPopulares(DialogoBase):
    def __init__(self, padre, catalogo: dict):
        super().__init__(padre, _("Feeds populares"), _("_Añadir los marcados"))
        self.items = [(f"{tema}: {nombre}", url) for tema, feeds in catalogo.items() for nombre, url in feeds]
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.checks = []
        for etiqueta, _url in self.items:
            cb = Gtk.CheckButton(label=etiqueta)
            caja.append(cb)
            self.checks.append(cb)
        marco = Gtk.ScrolledWindow(child=caja, min_content_height=300, min_content_width=440, has_frame=True)
        self.campo(_("_Marca los feeds que quieras añadir:"), marco)
        self.set_focus(self.checks[0] if self.checks else None)

    def aceptar(self):
        from app.services.rss.rss_service import rss_service
        urls = [u for (_e, u), cb in zip(self.items, self.checks) if cb.get_active()]
        if not urls:
            notificar(_("Marca al menos un feed."))
            return
        cuenta = cuenta_rss(self.padre)
        if not cuenta:
            notificar(_("Crea primero una cuenta de RSS en Preferencias."))
            return
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Añadiendo {0} feeds…").format(len(urls)))

        def _bg(ctx):
            ok = 0
            for u in urls:
                try:
                    rss_service.añadir_feed(account_id=cuenta, url=u, ctx=ctx)
                    ok += 1
                except Exception:
                    pass
            return ok

        def _fin(res, err):
            self.boton_aceptar.set_sensitive(True)
            self.padre.cargar_arbol()
            notificar(_("Añadidos {0} feeds.").format(res or 0))
            self.close()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- suscripciones de YouTube
class DialogoSuscripcionesYouTube(DialogoBase):
    def __init__(self, padre):
        from app.services.rss.rss_youtube import NAVEGADORES_COOKIES
        super().__init__(padre, _("Suscripciones de YouTube"), _("_Suscribir los marcados"))
        self.navegadores = NAVEGADORES_COOKIES
        self.canales = []
        self.cats = []
        self.nav = self.campo(_("_Navegador donde tienes la sesión de YouTube abierta:"),
                              Gtk.DropDown.new_from_strings([n.capitalize() for n in self.navegadores]))
        self.b_obtener = Gtk.Button(label=_("_Obtener mis suscripciones"), use_underline=True)
        self.b_obtener.connect("clicked", lambda *_a: self._obtener())
        self.rejilla.attach(self.b_obtener, 1, self._fila, 1, 1)
        self._fila += 1
        self.caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.checks = []
        self.campo(_("_Canales a los que seguir:"), Gtk.ScrolledWindow(child=self.caja, min_content_height=220,
                                                                       min_content_width=440, has_frame=True))
        b_todos = Gtk.Button(label=_("_Marcar o desmarcar todos"), use_underline=True)
        b_todos.connect("clicked", lambda *_a: self._todos())
        self.rejilla.attach(b_todos, 1, self._fila, 1, 1)
        self._fila += 1
        self.carpeta = self.campo(_("_Carpeta donde clasificarlos:"), Gtk.DropDown.new_from_strings([_("Sin carpeta")]))
        b_nueva = Gtk.Button(label=_("Crear carpeta _nueva…"), use_underline=True)
        b_nueva.connect("clicked", lambda *_a: self._nueva_carpeta())
        self.rejilla.attach(b_nueva, 1, self._fila, 1, 1)
        self._fila += 1
        self.boton_aceptar.set_sensitive(False)
        self._cargar_carpetas()
        self.set_focus(self.nav)

    def _cargar_carpetas(self, elegir: str = ""):
        from app.services.rss.rss_service import rss_service
        self.cats = rss_service.obtener_categorias(cuenta_rss(self.padre))
        self.carpeta.set_model(Gtk.StringList.new([_("Sin carpeta")] + [c["name"] for c in self.cats]))
        for i, c in enumerate(self.cats):
            if c["name"] == elegir:
                self.carpeta.set_selected(i + 1)

    def _nueva_carpeta(self):
        def _crear(nombre):
            from app.services.rss.rss_service import rss_service
            rss_service.agregar_categoria(cuenta_rss(self.padre), nombre)
            self._cargar_carpetas(nombre)
            self.padre.cargar_arbol()
        DialogoTexto(self, _("Crear carpeta"), _("_Nombre de la carpeta nueva:"), _crear, aceptar=_("_Crear")).present()

    def _obtener(self):
        from app.services.rss.rss_service import rss_service
        nav = self.navegadores[self.nav.get_selected()]
        notificar(_("Obteniendo tus suscripciones de YouTube…"))
        self.b_obtener.set_sensitive(False)

        def _fin(res, err):
            self.b_obtener.set_sensitive(True)
            if err:
                notificar(_("No se pudieron obtener las suscripciones: {0}. ¿Has iniciado sesión en YouTube en ese "
                            "navegador?").format(err))
                return
            self.canales = res or []
            while (h := self.caja.get_first_child()) is not None:
                self.caja.remove(h)
            self.checks = []
            for c in self.canales:
                cb = Gtk.CheckButton(label=c.get("title") or c.get("channel_id") or "")
                self.caja.append(cb)
                self.checks.append(cb)
            self.boton_aceptar.set_sensitive(bool(self.canales))
            notificar(_("{0} canales. Marca los que quieras y pulsa Suscribir.").format(len(self.canales))
                      if self.canales else _("No se encontraron suscripciones."))
            if self.checks:
                self.checks[0].grab_focus()
        task_manager.start_task(lambda ctx: rss_service.youtube_listar_suscripciones(nav), callback=_fin,
                                pass_context=True)

    def _todos(self):
        if self.checks:
            marcar = not all(c.get_active() for c in self.checks)
            for c in self.checks:
                c.set_active(marcar)

    def aceptar(self):
        from app.core.event_bus import event_bus
        from app.services.rss.rss_service import rss_service
        from .plataforma_gtk import llamar_en_ui
        marcados = [c for c, cb in zip(self.canales, self.checks) if cb.get_active()]
        if not marcados:
            notificar(_("Marca al menos un canal."))
            return
        cuenta = cuenta_rss(self.padre)
        if not cuenta:
            notificar(_("Crea primero una cuenta de RSS en Preferencias."))
            return
        sel = self.carpeta.get_selected()
        categoria = self.cats[sel - 1]["id"] if sel > 0 else None
        nav = self.navegadores[self.nav.get_selected()]
        self.boton_aceptar.set_sensitive(False)
        total = len(marcados)
        notificar(_("Importando {0} canales. Esto puede tardar…").format(total))

        def _bg(ctx):
            ok, videos = 0, 0
            for i, canal in enumerate(marcados):
                llamar_en_ui(notificar, _("Canal {0} de {1}: {2}").format(i + 1, total, canal.get("title", "")))
                try:
                    r = rss_service.youtube_suscribir_canal_completo(cuenta, canal, category_id=categoria,
                                                                     cookies_browser=nav)
                    ok += 1
                    videos += r.get("videos", 0)
                except Exception as e:
                    log.error("Canal de YouTube falló: %s", e)
            return ok, videos

        def _fin(res, err):
            self.boton_aceptar.set_sensitive(True)
            if err:
                notificar(_("Error al suscribir: {0}").format(err))
                return
            ok, videos = res or (0, 0)
            self.padre.cargar_arbol()
            event_bus.emit("sound_trigger", "ai_done")
            notificar(_("Importación terminada: {0} canales y {1} vídeos.").format(ok, videos))
            self.close()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- web scraper
class DialogoScraperSitio(DialogoBase):
    def __init__(self, padre):
        from app.services.rss import rss_scraper
        super().__init__(padre, _("Crear feed con scraper de sitio"), _("_Crear el feed"))
        self.sc = rss_scraper
        self.scrapers = rss_scraper.listar_scrapers()
        self.items = []
        self.scraper_id = ""
        if not self.scrapers:
            self.rejilla.attach(Gtk.Label(wrap=True, xalign=0, width_chars=60, max_width_chars=60, label=_(
                "No hay ningún módulo de scraper instalado. Crea uno copiando la plantilla de "
                "data/plugins/web_scraper/scrapers/_plantilla.py y reinicia la aplicación.")), 0, 0, 2, 1)
            self.boton_aceptar.set_sensitive(False)
            return
        self.url = self.campo(_("_Dirección de la web a leer:"), Gtk.Entry(activates_default=False, width_chars=50))
        self.modulo = self.campo(_("_Módulo de scraper (Automático lo detecta por la URL):"), Gtk.DropDown.new_from_strings(
            [_("Automático")] + [s.NAME or s.ID for s in self.scrapers]))
        self.b_probar = Gtk.Button(label=_("_Probar"), use_underline=True)
        self.b_probar.connect("clicked", lambda *_a: self._probar())
        self.rejilla.attach(self.b_probar, 1, self._fila, 1, 1)
        self._fila += 1
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        self.rejilla.attach(self.estado, 0, self._fila, 2, 1)
        self._fila += 1
        self.vista = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.campo(_("Artículos _detectados:"), Gtk.ScrolledWindow(child=self.vista, min_content_height=160, has_frame=True))
        self.rejilla.get_child_at(0, self._fila - 1).set_mnemonic_widget(self.vista)
        self.titulo = self.campo(_("_Título del feed:"), Gtk.Entry(width_chars=50))
        self.boton_aceptar.set_sensitive(False)
        self.set_focus(self.url)

    def _direccion(self) -> str:
        url = self.url.get_text().strip()
        return url if url.startswith("http") or not url else "https://" + url

    def _probar(self):
        url = self._direccion()
        if not url:
            notificar(_("Escribe la dirección de la web."))
            return
        sel = self.modulo.get_selected()
        sc = self.scrapers[sel - 1] if sel > 0 else self.sc.scraper_para_url(url)
        if sc is None:
            notificar(_("Ningún módulo sabe manejar esa URL. Elige uno en la lista o crea su módulo."))
            return
        self.scraper_id = sc.ID
        self.estado.set_text(_("Probando con {0}…").format(sc.NAME or sc.ID))
        self.b_probar.set_sensitive(False)

        def _fin(res, err):
            self.b_probar.set_sensitive(True)
            if err:
                self.estado.set_text(_("El scraper falló: {0}").format(err))
                return
            self.items = res or []
            self.vista.get_buffer().set_text("\n".join(it.get("title", "") or _("(sin título)") for it in self.items))
            if not self.titulo.get_text().strip():
                self.titulo.set_text(sc.NAME or url)
            self.boton_aceptar.set_sensitive(bool(self.items))
            self.estado.set_text(_("{0} artículos. Revisa y pulsa Crear el feed.").format(len(self.items)) if self.items
                                 else _("El scraper no devolvió artículos."))
            notificar(self.estado.get_text())
        task_manager.start_task(lambda ctx: self.sc.scrapear_con_modulo(sc.ID, url), callback=_fin, pass_context=True)

    def aceptar(self):
        from app.services.rss.rss_service import rss_service
        url = self._direccion()
        if not self.items or not self.scraper_id:
            notificar(_("Primero prueba el scraper."))
            return
        cuenta = cuenta_rss(self.padre)
        if not cuenta:
            notificar(_("Crea primero una cuenta de RSS en Preferencias."))
            return
        titulo, sid = self.titulo.get_text().strip(), self.scraper_id
        notificar(_("Creando el feed…"))

        def _fin(res, err):
            if err:
                notificar(_("No se pudo crear el feed: {0}").format(err))
                return
            self.padre.cargar_arbol()
            notificar(_("Feed creado con el scraper de sitio."))
            self.close()
        task_manager.start_task(lambda ctx: rss_service.crear_feed_web(cuenta, url, titulo, None, "", scraper_module=sid, ctx=ctx),
                                callback=_fin, pass_context=True)
