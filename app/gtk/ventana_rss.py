# -*- coding: utf-8 -*-
"""Gestión de feeds RSS en la ventana principal (mixin de VentanaPrincipal): el menú de los nodos del árbol (feeds, carpetas,
cuenta, carpetas inteligentes, búsquedas guardadas), la importación con vista previa y todas las herramientas de RSS."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.database import db
from app.core.privacy import abrir_url
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service

from .dialogos import DialogoElegir, DialogoTexto, confirmar_async

log = logging.getLogger(__name__)


class _RSSMixin:
    def _registrar_acciones_rss(self, accion):
        # sobre el nodo del árbol
        for nombre, fn in (("rss-fijar", self.rss_fijar), ("rss-pausar", self.rss_pausar),
                           ("rss-renombrar", self.rss_renombrar), ("rss-mover-feed", self.rss_mover_feed),
                           ("rss-etiquetas-feed", self.rss_etiquetas_feed), ("rss-editar-feed", self.rss_editar_feed),
                           ("rss-recuperar", self.rss_recuperar), ("rss-feed-navegador", self.rss_feed_navegador),
                           ("rss-eliminar-feed", self.rss_eliminar_feed), ("rss-leido-nodo", self.rss_leido_nodo),
                           ("rss-actualizar-nodo", self.rss_actualizar_nodo), ("rss-estadisticas-carpeta", self.rss_estadisticas_carpeta),
                           ("rss-subcarpeta", self.rss_subcarpeta), ("rss-mover-carpeta", self.rss_mover_carpeta),
                           ("rss-renombrar-carpeta", self.rss_renombrar_carpeta),
                           ("rss-eliminar-carpeta", self.rss_eliminar_carpeta),
                           ("rss-editar-nodo-guardado", self.rss_editar_nodo_guardado),
                           ("rss-borrar-nodo-guardado", self.rss_borrar_nodo_guardado),
                           ("menu-arbol", self.mostrar_menu_arbol)):
            accion(nombre, fn)
        agrupar = Gio.SimpleAction.new_stateful("rss-agrupar-fecha", None,
                                                GLib.Variant.new_boolean(bool(config.get("rss", "group_by_date", False))))
        agrupar.connect("activate", lambda *_a: self.rss_alternar_agrupar())
        self.add_action(agrupar)
        self._atajos_defecto["rss-agrupar-fecha"] = []
        # generales de RSS
        for nombre, fn in (("rss-buscar-feeds", self.rss_buscar_feeds), ("rss-feed-web", self.rss_feed_web),
                           ("rss-youtube", self.rss_canal_youtube), ("rss-takeout", self.rss_takeout),
                           ("rss-categorias", self.rss_categorias), ("rss-etiquetas", self.rss_etiquetas),
                           ("rss-reglas", self.rss_reglas), ("rss-carpetas", self.rss_carpetas),
                           ("rss-busquedas", self.rss_busquedas), ("rss-alertas", self.rss_alertas),
                           ("rss-sincronizacion", self.rss_sincronizacion), ("rss-salud", self.rss_salud),
                           ("rss-descargas", self.rss_descargas), ("rss-historial", self.rss_historial),
                           ("rss-estadisticas", self.rss_estadisticas), ("rss-resumen", self.rss_resumen),
                           ("rss-exportar", self.rss_exportar), ("rss-guardar-busqueda", self.rss_guardar_busqueda),
                           ("rss-etiquetas-articulo", self.rss_etiquetas_articulo), ("rss-galeria", self.rss_galeria),
                           ("rss-encolar", self.rss_encolar), ("rss-importar-opml", self.rss_importar_opml)):
            accion(nombre, fn)

    # ------------------------------------------------------------------ utilidades
    def _rss_cuenta(self):
        acc = self.get_application().account_id
        if acc:
            return acc
        self.avisar(_("Crea primero una cuenta de RSS en Preferencias"))
        return None

    def _rss_nodo(self, *tipos):
        """El nodo RSS seleccionado en el árbol (si es de alguno de esos tipos)."""
        n = self.nodo_actual
        if n is None or n.servicio != "rss" or (tipos and n.tipo not in tipos):
            self.avisar(_("Selecciona en el árbol un elemento de RSS"))
            return None
        return n

    def _rss_repintar(self):
        """Repinta contadores y la lista del buffer abierto."""
        self.refrescar_contadores()
        if self.nodo_actual is not None and self.nodo_actual.servicio == "rss":
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    # ------------------------------------------------------------------ menú de los nodos del árbol
    def _foco_en_arbol(self) -> bool:
        f = self.get_focus()
        return f is not None and (f is self.vista_arbol or f.is_ancestor(self.vista_arbol))

    def _menu_nodo_rss(self, n):
        """(etiqueta, acción) del menú del nodo RSS elegido, en secciones."""
        if n.tipo == "feed":
            fila = db.query_one("SELECT COALESCE(pinned,0), COALESCE(paused,0) FROM rss_feeds WHERE id = ?", (n.feed_id,))
            fijado, pausado = (bool(fila[0]), bool(fila[1])) if fila else (False, False)
            return [[(_("Actualizar este feed"), "win.rss-actualizar-nodo"),
                     (_("Marcar todo el feed como leído"), "win.rss-leido-nodo")],
                    [(_("Desfijar el feed") if fijado else _("Fijar el feed arriba"), "win.rss-fijar"),
                     (_("Reanudar el feed (actualizar)") if pausado else _("Pausar el feed (no actualizar)"), "win.rss-pausar"),
                     (_("Renombrar el feed…"), "win.rss-renombrar"), (_("Mover a una categoría…"), "win.rss-mover-feed"),
                     (_("Etiquetas del feed…"), "win.rss-etiquetas-feed"),
                     (_("Editar el feed (título, dirección, ajustes)…"), "win.rss-editar-feed")],
                    [(_("Recuperar episodios antiguos (archivo de Internet)…"), "win.rss-recuperar"),
                     (_("Abrir el feed en el navegador"), "win.rss-feed-navegador"),
                     (_("Eliminar el feed"), "win.rss-eliminar-feed")]]
        if n.tipo == "category":
            return [[(_("Actualizar la categoría"), "win.rss-actualizar-nodo"),
                     (_("Marcar toda la categoría como leída"), "win.rss-leido-nodo"),
                     (_("Ver las estadísticas de la categoría"), "win.rss-estadisticas-carpeta")],
                    [(_("Crear una subcarpeta aquí…"), "win.rss-subcarpeta"), (_("Renombrar la carpeta…"), "win.rss-renombrar-carpeta"),
                     (_("Mover esta carpeta a…"), "win.rss-mover-carpeta"), (_("Eliminar la carpeta"), "win.rss-eliminar-carpeta")]]
        if n.tipo in ("all", "unread"):
            return [[(_("Marcar todo como leído"), "win.rss-leido-nodo"), (_("Actualizar todos los feeds"), "win.rss-actualizar-nodo")]]
        if n.tipo == "servicio":
            return [[(_("Actualizar todos los feeds"), "win.rss-actualizar-nodo"),
                     (_("Marcar todo como leído"), "win.rss-leido-nodo"), (_("Añadir un feed…"), "app.anadir-feed")]]
        if n.tipo.startswith("search:"):
            return [[(_("Editar la búsqueda guardada…"), "win.rss-editar-nodo-guardado"),
                     (_("Borrar la búsqueda guardada"), "win.rss-borrar-nodo-guardado")]]
        if n.tipo.startswith("smart:"):
            return [[(_("Editar la carpeta inteligente…"), "win.rss-editar-nodo-guardado"),
                     (_("Eliminar la carpeta inteligente"), "win.rss-borrar-nodo-guardado")]]
        return []

    def rss_alternar_agrupar(self, valor=None):
        """Agrupar por fecha: a la primera fila de cada bloque (Hoy, Ayer, Esta semana...) se le antepone su etiqueta."""
        nuevo = (not config.get("rss", "group_by_date", False)) if valor is None else valor
        config.set("rss", "group_by_date", nuevo)
        accion = self.lookup_action("rss-agrupar-fecha")
        if accion is not None:
            accion.set_state(GLib.Variant.new_boolean(nuevo))
        obj = self._fila_actual()
        self.cargar_lista(conservar_id=obj.fila.id if obj else None)
        self.avisar(_("Agrupar por fecha activado.") if nuevo else _("Agrupación desactivada."))

    def mostrar_menu_arbol(self, x=None, y=None):
        n = self.nodo_actual
        if n is None:
            return
        secciones = self._menu_nodo_rss(n) if n.servicio == "rss" else []
        secciones = secciones + self.menu_bandeja_nodo(n)
        if not secciones:
            self.avisar(_("Este elemento no tiene menú"))
            return
        m = Gio.Menu()
        for sec in secciones:
            s = Gio.Menu()
            for et, ac in sec:
                s.append(et, ac)
            m.append_section(None, s)
        pop = Gtk.PopoverMenu.new_from_model(m)
        pop.set_parent(self.vista_arbol)
        pop.set_has_arrow(False)
        if x is not None:
            from gi.repository import Gdk
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
            pop.set_pointing_to(r)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    # ------------------------------------------------------------------ feeds
    def rss_fijar(self):
        n = self._rss_nodo("feed")
        if n:
            fila = db.query_one("SELECT COALESCE(pinned,0) FROM rss_feeds WHERE id = ?", (n.feed_id,))
            fijar = not (fila and fila[0])
            rss_service.set_feed_pinned(n.feed_id, fijar)
            self.cargar_arbol()
            self.avisar(_("Feed fijado.") if fijar else _("Feed desfijado."))

    def rss_pausar(self):
        n = self._rss_nodo("feed")
        if n:
            fila = db.query_one("SELECT COALESCE(paused,0) FROM rss_feeds WHERE id = ?", (n.feed_id,))
            pausar = not (fila and fila[0])
            rss_service.set_feed_paused(n.feed_id, pausar)
            self.cargar_arbol()
            self.avisar(_("Feed en pausa.") if pausar else _("Feed reactivado."))

    def rss_renombrar(self):
        n = self._rss_nodo("feed")
        if not n:
            return
        from .dialogos_rss import DialogoTextoValor
        fila = db.query_one("SELECT title FROM rss_feeds WHERE id = ?", (n.feed_id,))

        def _nuevo(texto):
            db.execute("UPDATE rss_feeds SET title = ? WHERE id = ?", (texto, n.feed_id))
            rss_service.sus_renombrar_feed(n.feed_id)
            self.cargar_arbol()
            self.avisar(_("Feed renombrado."))
        DialogoTextoValor(self, _("Renombrar el feed"), _("_Nuevo nombre del feed:"), fila[0] if fila else "", _nuevo,
                          _("_Renombrar")).present()

    def rss_mover_feed(self):
        n = self._rss_nodo("feed")
        if not n:
            return
        fr = db.query_one("SELECT account_id FROM rss_feeds WHERE id = ?", (n.feed_id,))
        cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY position, name", (fr[0] if fr else None,))

        def _mover(i):
            nueva = None if i == 0 else cats[i - 1][0]
            antes = rss_service._datos_feed(n.feed_id)[3]
            db.execute("UPDATE rss_feeds SET category_id = ? WHERE id = ?", (nueva, n.feed_id))
            rss_service.sus_mover_feed(n.feed_id, antes)
            self.cargar_arbol()
            self.avisar(_("Feed movido de categoría."))
        DialogoElegir(self, _("Mover a una categoría"), _("_Mover el feed a la categoría:"),
                      [_("Sin categoría")] + [c[1] for c in cats], _mover, _("_Mover")).present()

    def rss_etiquetas_feed(self):
        n = self._rss_nodo("feed")
        acc = self._rss_cuenta()
        if n and acc:
            from .dialogos_rss import DialogoAsignarEtiquetas
            DialogoAsignarEtiquetas(self, acc, feed_id=n.feed_id, al_cambiar=self.cargar_arbol).present()

    def rss_editar_feed(self):
        n = self._rss_nodo("feed")
        if not n:
            return
        from .dialogos_rss import DialogoEditarFeed

        def _guardado(cambio_url):
            self.cargar_arbol()
            self.avisar(_("Feed guardado."))
            if cambio_url:
                self.accion_actualizar_feed()
        try:
            DialogoEditarFeed(self, n.feed_id, _guardado).present()
        except ValueError as e:
            self.avisar(str(e))

    def rss_recuperar(self):
        n = self._rss_nodo("feed")
        if not n:
            return

        def _hacer():
            self.avisar(_("Buscando copias antiguas del feed en el archivo de Internet…"))

            def _fin(res, err):
                if err or not res:
                    self.avisar(_("No se pudieron recuperar episodios: {0}").format(err or ""))
                elif not res["capturas"]:
                    self.avisar(_("El archivo de Internet no tiene copias de este feed."))
                elif res["nuevos"]:
                    self.avisar(_("Recuperados {0} episodios antiguos de {1} copias del feed.").format(res["nuevos"], res["leidas"]))
                    self._rss_repintar()
                else:
                    self.avisar(_("No hay episodios antiguos que no tengas ya ({0} copias revisadas).").format(res["leidas"]))
            task_manager.start_task(lambda ctx: rss_service.recuperar_episodios_archivados(n.feed_id, ctx=ctx), callback=_fin,
                                    pass_context=True)
        confirmar_async(self, _("Se buscarán en el archivo de Internet (Wayback Machine) copias antiguas de «{0}» y se "
                                "añadirán los episodios que ya no están en el feed. Entran como leídos y sin avisos. Puede "
                                "tardar un par de minutos.\n\n¿Continuar?").format(n.titulo), _hacer,
                        titulo=_("Recuperar episodios antiguos"), etiqueta_si=_("Continuar"))

    def rss_feed_navegador(self):
        n = self._rss_nodo("feed")
        if n:
            fila = db.query_one("SELECT url FROM rss_feeds WHERE id = ?", (n.feed_id,))
            if fila and fila[0]:
                abrir_url(fila[0])
                self.avisar(_("Abriendo el feed en el navegador."))

    def rss_eliminar_feed(self):
        n = self._rss_nodo("feed")
        if not n:
            return

        def _hacer():
            rss_service.sus_baja_feed(n.feed_id)        # antes de borrar: necesita su dirección
            db.execute("DELETE FROM rss_articles WHERE feed_id = ?", (n.feed_id,))
            db.execute("DELETE FROM rss_feeds WHERE id = ?", (n.feed_id,))
            self.cargar_arbol(conservar=False)
            self.avisar(_("Feed eliminado."))
        confirmar_async(self, _("¿Eliminar el feed «{0}» y todos sus artículos?").format(n.titulo), _hacer,
                        titulo=_("Confirmar eliminación"), etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ marcar y actualizar por ámbito
    def rss_leido_nodo(self):
        n = self._rss_nodo()
        if not n:
            return
        if n.tipo == "feed":
            db.execute("UPDATE rss_articles SET read = 1 WHERE feed_id = ?", (n.feed_id,))
        elif n.tipo == "category":
            db.execute("UPDATE rss_articles SET read = 1 WHERE feed_id IN (SELECT id FROM rss_feeds WHERE category_id = ?)",
                       (n.category_id,))
        else:
            acc = self._rss_cuenta()
            if acc is None:
                return
            db.execute("UPDATE rss_articles SET read = 1 WHERE feed_id IN (SELECT id FROM rss_feeds WHERE account_id = ?)", (acc,))
        self._rss_repintar()
        self.avisar(_("Marcado como leído."))

    def rss_actualizar_nodo(self):
        n = self._rss_nodo()
        if not n:
            return
        if n.tipo == "feed":
            return self.accion_actualizar_feed()
        acc = self._rss_cuenta()
        if acc is None:
            return
        if n.tipo == "category":
            self.avisar(_("Actualizando la categoría…"))
            for (fid,) in db.query("SELECT id FROM rss_feeds WHERE account_id = ? AND category_id = ?", (acc, n.category_id)):
                rss_service.actualizar_feed_async(fid)
            return
        self.avisar(_("Actualizando feeds…"))

        def _fin(res, err):
            if not err:
                self._rss_repintar()
                self.avisar(_("Feeds actualizados."))
        rss_service.actualizar_todos_async(acc, callback=_fin)

    # ------------------------------------------------------------------ carpetas
    def rss_estadisticas_carpeta(self):
        n = self._rss_nodo("category")
        if not n:
            return
        fila = db.query_one(
            "SELECT COUNT(*), SUM(CASE WHEN a.read = 0 THEN 1 ELSE 0 END), SUM(CASE WHEN a.favorite = 1 THEN 1 ELSE 0 END) "
            "FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id WHERE f.category_id = ? AND COALESCE(a.deleted, 0) = 0",
            (n.category_id,))
        self.avisar(_("Categoría {0}: {1} artículos, {2} sin leer, {3} destacados.").format(
            n.titulo, fila[0] if fila else 0, (fila[1] or 0) if fila else 0, (fila[2] or 0) if fila else 0))

    def rss_subcarpeta(self):
        n = self._rss_nodo("category")
        acc = self._rss_cuenta()
        if not n or acc is None:
            return

        def _crear(texto):
            try:
                rss_service.agregar_categoria(acc, texto, parent_id=n.category_id)
            except Exception as e:
                self.avisar(_("No se pudo crear: {0}").format(e))
                return
            self.cargar_arbol()
            self.avisar(_("Subcarpeta creada. Mueve feeds a ella desde su menú."))
        DialogoTexto(self, _("Crear una subcarpeta"), _("_Nombre de la nueva subcarpeta:"), _crear, aceptar=_("_Crear")).present()

    def rss_renombrar_carpeta(self):
        n = self._rss_nodo("category")
        if not n:
            return
        from .dialogos_rss import DialogoTextoValor

        def _nuevo(texto):
            db.execute("UPDATE rss_categories SET name = ? WHERE id = ?", (texto, n.category_id))
            self.cargar_arbol()
            self.avisar(_("Carpeta renombrada."))
        DialogoTextoValor(self, _("Renombrar la carpeta"), _("_Nuevo nombre de la carpeta:"), n.titulo, _nuevo,
                          _("_Renombrar")).present()

    def rss_mover_carpeta(self):
        n = self._rss_nodo("category")
        acc = self._rss_cuenta()
        if not n or acc is None:
            return
        todas = db.query("SELECT id, COALESCE(parent_id, 0) FROM rss_categories")
        hijos = {}
        for cid, pid in todas:
            hijos.setdefault(pid, []).append(cid)
        descendientes, pila = {n.category_id}, [n.category_id]
        while pila:
            for h in hijos.get(pila.pop(), []):
                if h not in descendientes:
                    descendientes.add(h)
                    pila.append(h)
        cats = [c for c in db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name", (acc,))
                if c[0] not in descendientes]

        def _mover(i):
            db.execute("UPDATE rss_categories SET parent_id = ? WHERE id = ?", (None if i == 0 else cats[i - 1][0], n.category_id))
            self.cargar_arbol()
            self.avisar(_("Carpeta movida."))
        DialogoElegir(self, _("Mover la carpeta"), _("_Mover la carpeta dentro de:"),
                      [_("(Raíz, sin carpeta superior)")] + [c[1] for c in cats], _mover, _("_Mover")).present()

    def rss_eliminar_carpeta(self):
        n = self._rss_nodo("category")
        if not n:
            return

        def _hacer():
            db.execute("DELETE FROM rss_categories WHERE id = ?", (n.category_id,))
            self.cargar_arbol(conservar=False)
            self.avisar(_("Categoría eliminada."))
        confirmar_async(self, _("¿Eliminar la carpeta «{0}»? Los feeds dentro de ella se mantendrán sin categoría.").format(n.titulo),
                        _hacer, titulo=_("Confirmar eliminación"), etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ búsquedas guardadas y carpetas inteligentes del árbol
    def rss_editar_nodo_guardado(self):
        n = self._rss_nodo()
        acc = self._rss_cuenta()
        if not n or acc is None:
            return
        ident = int(n.tipo.split(":", 1)[1])
        if n.tipo.startswith("search:"):
            fila = db.query_one("SELECT name, query FROM rss_saved_searches WHERE id = ?", (ident,))
            if not fila:
                return
            from .dialogos_rss import DialogoBusquedaGuardada

            def _guardar(nombre, consulta):
                rss_service.editar_busqueda_guardada(ident, nombre, consulta)
                self.cargar_arbol()
                self.avisar(_("Búsqueda actualizada."))
            DialogoBusquedaGuardada(self, _("Editar búsqueda guardada"), fila[0], fila[1], _guardar).present()
        elif n.tipo.startswith("smart:"):
            from .dialogos_rss_reglas import DialogoCarpetaInteligente
            carpeta = next((c for c in rss_service.obtener_carpetas_inteligentes(acc) if c["id"] == ident), None)

            def _guardar2(nombre, criterios):
                rss_service.editar_carpeta_inteligente(ident, nombre, criterios)
                self.cargar_arbol()
                self.avisar(_("Carpeta inteligente guardada."))
            DialogoCarpetaInteligente(self, acc, carpeta, _guardar2).present()

    def rss_borrar_nodo_guardado(self):
        n = self._rss_nodo()
        if not n:
            return
        ident = int(n.tipo.split(":", 1)[1])
        es_busqueda = n.tipo.startswith("search:")

        def _hacer():
            (rss_service.borrar_busqueda_guardada if es_busqueda else rss_service.borrar_carpeta_inteligente)(ident)
            self.cargar_arbol(conservar=False)
            self.avisar(_("Búsqueda borrada.") if es_busqueda else _("Carpeta inteligente eliminada."))
        confirmar_async(self, (_("¿Borrar la búsqueda guardada «{0}»?") if es_busqueda
                               else _("¿Eliminar la carpeta inteligente «{0}»? (Los artículos no se tocan.)")).format(n.titulo),
                        _hacer, etiqueta_si=_("Borrar"))

    def rss_guardar_busqueda(self):
        """Guarda como buffer del árbol la búsqueda escrita en el cuadro de búsqueda."""
        acc = self._rss_cuenta()
        consulta = self.entrada_busqueda.get_text().strip()
        if acc is None:
            return
        from .dialogos_rss import DialogoBusquedaGuardada

        def _guardar(nombre, texto):
            rss_service.crear_busqueda_guardada(acc, nombre, texto)
            self.cargar_arbol()
            self.avisar(_("Búsqueda guardada."))
        DialogoBusquedaGuardada(self, _("Guardar la búsqueda"), consulta[:40], consulta, _guardar).present()

    # ------------------------------------------------------------------ descubrir, crear e importar feeds
    def rss_buscar_feeds(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoBuscarFeeds
            DialogoBuscarFeeds(self, acc, self.cargar_arbol).present()

    def rss_feed_web(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoFeedWeb
            DialogoFeedWeb(self, acc, self.cargar_arbol).present()

    def rss_canal_youtube(self):
        acc = self._rss_cuenta()
        if acc is None:
            return

        def _anadir(valor):
            self.avisar(_("Añadiendo canal…"))

            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo añadir el canal: {0}").format(err))
                else:
                    self.avisar(_("Canal de YouTube añadido: {0}").format(res or ""))
                    self.cargar_arbol()
            task_manager.start_task(lambda ctx: rss_service.añadir_canal_youtube(acc, valor, ctx=ctx), callback=_fin,
                                    pass_context=True)
        DialogoTexto(self, _("Añadir un canal de YouTube"), _("_URL del canal de YouTube o su ID (UC…):"), _anadir,
                     aceptar=_("_Añadir")).present()

    def _elegir_archivo(self, titulo: str, patrones, al_elegir):
        dlg = Gtk.FileDialog(title=titulo)
        filtro = Gtk.FileFilter()
        filtro.set_name(", ".join(patrones))
        for p in patrones:
            filtro.add_pattern(p)
        almacen = Gio.ListStore.new(Gtk.FileFilter)
        almacen.append(filtro)
        dlg.set_filters(almacen)

        def _fin(origen, res):
            try:
                ruta = origen.open_finish(res).get_path()
            except GLib.Error:
                return
            al_elegir(ruta)
        dlg.open(self, None, _fin)

    def rss_importar_opml(self):
        acc = self._rss_cuenta()
        if acc is None:
            return

        def _ruta(ruta):
            self.avisar(_("Analizando el archivo OPML…"))

            def _fin(res, err):
                if err or res is None:
                    self.avisar(_("No se pudo leer el OPML: {0}").format(err or ""))
                    return
                self._vista_previa_importacion(acc, res)
            task_manager.start_task(lambda ctx: rss_service.analizar_opml(acc, ruta), callback=_fin, pass_context=True)
        self._elegir_archivo(_("Importar OPML"), ["*.opml", "*.xml"], _ruta)

    def rss_takeout(self):
        acc = self._rss_cuenta()
        if acc is None:
            return

        def _ruta(ruta):
            self.avisar(_("Analizando el archivo de Google Takeout…"))

            def _fin(res, err):
                if err or not res:
                    self.avisar(_("No se pudo leer la exportación de YouTube: {0}").format(err or ""))
                    return
                from app.services.rss.rss_takeout import NOMBRES_FUENTE
                for f in res:
                    f["fuente_legible"] = _(NOMBRES_FUENTE.get(f.get("fuente"), ""))
                self._vista_previa_importacion(acc, res, _("Importar desde Google Takeout"), _("la exportación de YouTube"))
            task_manager.start_task(lambda ctx: rss_service.analizar_takeout_youtube(acc, ruta), callback=_fin, pass_context=True)
        confirmar_async(self, _("Para obtener el archivo: entra en takeout.google.com, deja marcado solo «YouTube y YouTube "
                                "Music» y pide la exportación. Google te enviará un enlace para descargar un ZIP. No hace falta "
                                "descomprimirlo: elígelo tal cual.\n\nSe ofrecen tus suscripciones, tus canales y tus listas "
                                "(marcados) y los canales de tu historial (sin marcar)."),
                        lambda: self._elegir_archivo(_("Elegir el ZIP de Google Takeout"), ["*.zip"], _ruta),
                        titulo=_("Importar desde Google Takeout"), etiqueta_si=_("Elegir el archivo"))

    def _vista_previa_importacion(self, account_id, analisis, titulo=None, origen=None):
        """Vista previa con casillas: los nuevos vienen marcados; los duplicados y los inválidos, no. Importa solo lo elegido."""
        from .dialogos_rss import DialogoCasillas
        titulo, origen = titulo or _("Importar OPML: vista previa"), origen or _("el archivo")
        if not analisis:
            self.avisar(_("No hay feeds en {0}.").format(origen))
            return
        nuevos = sum(1 for f in analisis if f["estado"] == "nuevo")
        dups = sum(1 for f in analisis if f["estado"] == "duplicado")
        invalidos = sum(1 for f in analisis if f["estado"] == "invalido")
        etiquetas = []
        for f in analisis:
            carpeta = " / ".join(f["category_path"]) if f["category_path"] else _("(sin carpeta)")
            if f["estado"] == "nuevo":
                etiquetas.append(_("{0} — {1}").format(f["title"], carpeta) + ((" — " + f["fuente_legible"]) if f.get("fuente_legible") else ""))
            elif f["estado"] == "duplicado":
                etiquetas.append(_("{0} — ya estás suscrito").format(f["title"]))
            else:
                etiquetas.append(_("{0} — dirección no válida").format(f["title"]))
        marcados = {i for i, f in enumerate(analisis) if f["estado"] == "nuevo" and f.get("marcar", True)}

        def _importar(indices):
            elegidos = [analisis[i] for i in indices if analisis[i]["estado"] != "invalido"]
            if not elegidos:
                self.avisar(_("No se marcó ningún feed importable."))
                return
            self.avisar(_("Importando {0} feeds…").format(len(elegidos)))

            def _fin(n, err):
                self.avisar(_("Error al importar OPML: {0}").format(err) if err else _("{0} feeds importados.").format(n or 0))
                if not err:
                    self.cargar_arbol()
            rss_service.importar_feeds_seleccionados_async(account_id, elegidos, callback=_fin)
        DialogoCasillas(self, titulo, _("{0} feeds en {4}: {1} nuevos, {2} ya suscritos y {3} no válidos. Marca los que quieres "
                                        "importar (los nuevos vienen marcados):").format(len(analisis), nuevos, dups, invalidos, origen),
                        etiquetas, marcados, _importar, _("_Importar")).present()

    # ------------------------------------------------------------------ gestores
    def rss_categorias(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoCategorias
            DialogoCategorias(self, acc, self.cargar_arbol).present()

    def rss_etiquetas(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoEtiquetas
            DialogoEtiquetas(self, acc, self.cargar_arbol).present()

    def rss_reglas(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss_reglas import DialogoReglas
            DialogoReglas(self, acc).present()

    def rss_carpetas(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss_reglas import DialogoCarpetasInteligentes
            DialogoCarpetasInteligentes(self, acc).present()

    def rss_busquedas(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoBusquedasGuardadas
            DialogoBusquedasGuardadas(self, acc).present()

    def rss_alertas(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoAlertas
            DialogoAlertas(self, acc).present()

    def rss_sincronizacion(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoSincronizacion
            DialogoSincronizacion(self, acc).present()

    def rss_salud(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoSaludFeeds
            DialogoSaludFeeds(self, acc).present()

    def rss_descargas(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoDescargasPodcast
            DialogoDescargasPodcast(self, acc).present()

    def rss_historial(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_rss import DialogoHistorialLectura
            DialogoHistorialLectura(self, acc).present()

    def rss_estadisticas(self):
        acc = self._rss_cuenta()
        if acc is not None:
            from .dialogos_mastodon_extra import DialogoTextoLargo
            from .dialogos_rss import texto_estadisticas_lectura
            DialogoTextoLargo(self, _("Estadísticas de lectura"), texto_estadisticas_lectura(acc)).present()

    def rss_resumen(self):
        """Un resumen (digest) de los artículos sin leer, con IA si hay proveedor."""
        acc = self._rss_cuenta()
        if acc is None:
            return
        self.avisar(_("Preparando el resumen de artículos sin leer…"))

        def _fin(res, err):
            if err or not res:
                self.avisar(_("No se pudo generar el resumen: {0}").format(err or ""))
                return
            from .dialogos_herramientas import markdown_a_texto
            from .dialogos_mastodon_extra import DialogoTextoLargo
            DialogoTextoLargo(self, _("Resumen de artículos sin leer"), markdown_a_texto(res["texto"])).present()
        task_manager.start_task(lambda ctx: rss_service.generar_digest(acc), callback=_fin, pass_context=True)

    # ------------------------------------------------------------------ sobre el artículo enfocado
    def _rss_articulo(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "rss":
            self.avisar(_("Selecciona un artículo de RSS"))
            return None
        return obj

    def rss_etiquetas_articulo(self):
        obj, acc = self._rss_articulo(), self._rss_cuenta()
        if obj and acc:
            from .dialogos_rss import DialogoAsignarEtiquetas
            DialogoAsignarEtiquetas(self, acc, article_id=obj.fila.id, al_cambiar=self.cargar_arbol).present()

    def rss_galeria(self):
        obj = self._rss_articulo()
        if obj:
            from .dialogos_rss import DialogoGaleria
            DialogoGaleria(self, obj.fila.id).present()

    def rss_encolar(self):
        obj = self._rss_articulo()
        if obj:
            from app.services.media_player import cola_podcasts
            if cola_podcasts.encolar(obj.fila.id, obj.fila.titulo or _("Episodio")):
                self.avisar(_("Añadido a la cola de podcasts, posición {0}.").format(len(cola_podcasts.listar())))
            else:
                self.avisar(_("Ya estaba en la cola de podcasts."))

    # ------------------------------------------------------------------ exportar artículos en lote
    def rss_exportar(self):
        acc = self._rss_cuenta()
        if acc is None:
            return
        ambitos = [_("La vista actual (lo que hay en la lista)"), _("Todos los destacados"),
                   _("Todo lo marcado para leer más tarde")]

        def _ambito(i):
            if i == 0:
                if self.nodo_actual is None or self.nodo_actual.servicio != "rss":
                    self.avisar(_("La lista actual no muestra artículos RSS."))
                    return
                ids = [self.almacen_lista.get_item(k).fila.id for k in range(self.almacen_lista.get_n_items())]
            else:
                campo = "favorite" if i == 1 else "read_later"
                ids = [r[0] for r in db.query(f"SELECT id FROM rss_articles WHERE account_id = ? AND COALESCE({campo},0) = 1 "
                                              "AND COALESCE(deleted,0) = 0 ORDER BY published_at DESC", (acc,))]
            if not ids:
                self.avisar(_("No hay artículos que exportar."))
                return
            self._exportar_elegir(ids)
        DialogoElegir(self, _("Exportar artículos"), _("¿Qué artículos quieres _exportar?"), ambitos, _ambito, _("_Continuar")).present()

    def _exportar_elegir(self, ids):
        from .dialogos_rss import DialogoCasillas
        info = {}
        for j in range(0, len(ids), 900):
            trozo = ids[j:j + 900]
            for r in db.query("SELECT id, COALESCE(NULLIF(title,''), '(sin título)'), COALESCE(published_at,'') FROM rss_articles "
                              "WHERE id IN ({0})".format(",".join("?" * len(trozo))), tuple(trozo)):
                info[r[0]] = (r[1], r[2])
        orden = [i for i in ids if i in info]
        etiquetas = []
        for i in orden:
            titulo, fecha = info[i]
            corta = (fecha or "")[:16].replace("T", " ")
            etiquetas.append(_("{0} — {1}").format(titulo, corta) if corta else titulo)

        def _elegidos(indices):
            elegidos = [orden[k] for k in indices]
            if not elegidos:
                self.avisar(_("No se marcó ningún artículo."))
                return
            formatos = [_("Texto (.txt)"), _("HTML (.html)"), _("Markdown (.md)")]
            DialogoElegir(self, _("Exportar artículos"), _("_Formato de los archivos:"), formatos,
                          lambda f: self._exportar_carpeta(elegidos, ("txt", "html", "md")[f]), _("_Continuar")).present()
        DialogoCasillas(self, _("Exportar artículos: elige cuáles"),
                        _("{0} artículos. Marca los que quieres exportar (todos vienen marcados):").format(len(etiquetas)),
                        etiquetas, set(range(len(etiquetas))), _elegidos, _("_Exportar")).present()

    def _exportar_carpeta(self, ids, formato):
        dlg = Gtk.FileDialog(title=_("Carpeta donde guardar los artículos"))

        def _fin(origen, res):
            try:
                carpeta = origen.select_folder_finish(res).get_path()
            except GLib.Error:
                return
            self.avisar(_("Exportando {0} artículos…").format(len(ids)))

            def _hecho(n, err):
                self.avisar(_("Error al exportar: {0}").format(err) if err
                            else _("{0} artículos exportados en {1}.").format(n or 0, carpeta))
            rss_service.exportar_articulos_lote_async(ids, carpeta, formato=formato, callback=_hecho)
        dlg.select_folder(self, None, _fin)
