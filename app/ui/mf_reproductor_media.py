# -*- coding: utf-8 -*-
"""MainFrame (mixin): reproductor_media.
Metodos: _on_citar_toot, _on_boost_fav_toot, _on_marcador_toot, _on_impulsar_toot, _on_nuevo_hilo, _on_ver_hilos_guardados, _on_ver_detalles_toot, _on_add_feed ...
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
# -*- coding: utf-8 -*-
"""
Ventana principal de la aplicación SocialRSS Accessible Suite.
Layout dividido en navegación vertical de servicios, lista de toots/artículos y panel de lectura.
Todos los procesos de sincronización y red ocurren de forma asíncrona mediante el task_manager.
"""

import logging
import wx
from app.core.privacy import abrir_url

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service
from app.services.media_player.player_service import player_service


log = logging.getLogger(__name__)
from .ui_helpers import formatear_tiempo_relativo


class _Mf_reproductor_media:
    def _on_citar_toot(self, event=None, db_id=None):
        if db_id is None:
            idx = self.lista_items.GetFocusedItem()
            if idx < 0 or idx >= len(self.lista_items_db_ids):
                speak(_("Debe enfocar un toot para poder citar."), interrupt=True)
                return
            db_id = self.lista_items_db_ids[idx]

        # Cita NATIVA de Mastodon: se pasa el id del toot citado (quoted_status_id), no se
        # pega el enlace en el texto. Si la fila es un impulso, se cita el ORIGINAL.
        row = db.query_one(
            "SELECT COALESCE(reblog_of_id, status_id), visibility, can_quote, uri "
            "FROM mastodon_statuses WHERE id = ?",
            (db_id,))
        if not row or not row[0]:
            notificar(_("No se pudo obtener la publicación para citar."))
            return

        quoted_id, visibilidad, can_quote, uri = row
        # Como TWBlue: un mensaje directo no se puede citar (es una conversación privada).
        if visibilidad == "direct":
            speak(_("No se puede citar un mensaje directo."), interrupt=True)
            return
        # quote_approval (Mastodon 4.5+): el autor puede no permitir que le citen. Si el servidor
        # nos lo dice explícitamente (can_quote == 0), avisamos antes de escribir en balde.
        if can_quote == 0:
            speak(_("El autor no permite citar esta publicación."), interrupt=True)
            return

        # La cita NATIVA existe desde Mastodon 4.5: una instancia anterior IGNORA el
        # parámetro y publicaría solo el comentario, sin rastro de lo citado (auditoría
        # M8). Si la instancia no la soporta (o aún no se conoce su versión), se cita
        # MEDIANTE ENLACE: la URL de la publicación va visible en el texto.
        from .mastodon_views import ComposeTootDialog
        soporta = mastodon_service.soporta_citas_nativas(self.active_mastodon_account_id)
        if soporta is True:
            dlg = ComposeTootDialog(
                self,
                self.active_mastodon_account_id,
                quote_status_id=quoted_id
            )
        else:
            if not uri:
                notificar(_("No se pudo obtener el enlace de la publicación para citarla."))
                return
            speak(_("Este servidor no admite citas nativas: se citará mediante enlace."),
                  interrupt=True)
            dlg = ComposeTootDialog(
                self,
                self.active_mastodon_account_id,
                texto_inicial="\n\n" + uri
            )
        dlg.ShowModal()
        dlg.Destroy()

    def _on_boost_fav_toot(self, event=None, db_id=None):
        if db_id is None:
            idx = self.lista_items.GetFocusedItem()
            if idx < 0 or idx >= len(self.lista_items_db_ids):
                return
            db_id = self.lista_items_db_ids[idx]
        row = db.query_one("SELECT favourited FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row:
            return
            
        fav = row[0]
        accion = "unfavourite" if fav else "favourite"
        
        def _callback(res, err):
            if err:
                # Conservar el DETALLE real del error (auditoría §11: "Error al interactuar"
                # no decía nada; puede ser un límite, permisos o la red).
                notificar(_("No se pudo cambiar el favorito: {0}").format(err))
            else:
                notificar(_("Quitado de favoritos.") if fav else _("Marcado como favorito."))
                # Sin reconstruir la lista (movía el foco): si quitamos el favorito estando en el
                # buffer Favoritos, la fila desaparece; en cualquier otro caso, solo se actualizan
                # sus indicadores/contadores en su sitio.
                if accion == "unfavourite" and self.active_timeline_type == "favourites":
                    self._quitar_toot_de_lista(db_id)
                else:
                    self._refrescar_indicadores_toot(db_id)

        mastodon_service.ejecutar_accion_toot_async(
            self.active_mastodon_account_id,
            db_id,
            accion,
            callback=_callback
        )

    def _on_marcador_toot(self, db_id):
        """Alterna el MARCADOR (bookmark) de una publicación: si está guardada la quita, y si
        no, la guarda. Refresca la lista (en el buffer de Marcadores, al quitarla desaparece)."""
        row = db.query_one("SELECT bookmarked FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row:
            return
        accion = "unbookmark" if row[0] else "bookmark"

        def _callback(res, err):
            if err:
                notificar(_("No se pudo cambiar el marcador: {0}").format(err))
            else:
                notificar(_("Quitado de marcadores.") if row[0] else _("Guardado en marcadores."))
                # Si quitamos el marcador estando en el buffer Marcadores, la fila desaparece;
                # en cualquier otro caso, se actualizan sus indicadores en su sitio (sin mover foco).
                if accion == "unbookmark" and self.active_timeline_type == "bookmarks":
                    self._quitar_toot_de_lista(db_id)
                else:
                    self._refrescar_indicadores_toot(db_id)
        mastodon_service.ejecutar_accion_toot_async(self.active_mastodon_account_id, db_id, accion, callback=_callback)

    def _on_impulsar_toot(self, db_id):
        """Impulsa (boost) una publicación según el modo configurado: preguntar,
        impulsar directamente o citar."""
        # Reglas oficiales de Mastodon (status_policy.rb, reblog?): no se puede impulsar un
        # mensaje DIRECTO, ni una publicación solo para SEGUIDORES (privada) que no sea tuya.
        fila_vis = db.query_one("SELECT visibility FROM mastodon_statuses WHERE id = ?", (db_id,))
        vis_toot = fila_vis[0] if fila_vis else ""
        if vis_toot == "direct":
            speak(_("No se puede impulsar ni citar un mensaje directo."), interrupt=True)
            return
        if vis_toot == "private" and not self._toot_es_mio(db_id):
            speak(_("Mastodon no permite impulsar una publicación solo para seguidores que no sea tuya."), interrupt=True)
            return
        modo = config.get("mastodon", "boost_mode", "ask")
        if modo == "ask":
            # Botones EXPLÍCITOS (auditoría §11): antes era Sí/No/Cancelar y había que
            # recordar que "Sí" impulsaba y "No" citaba.
            from .accessible_controls import elegir_opcion
            r = elegir_opcion(self, _("¿Cómo quieres compartir esta publicación?"),
                              _("Impulsar publicación"), [_("&Impulsar"), _("&Citar")])
            if r is None:
                return
            modo = "boost" if r == 0 else "quote"

        if modo == "quote":
            row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (db_id,))
            quoted_id = row[0] if row else None
            from .mastodon_views import ComposeTootDialog
            dlg = ComposeTootDialog(self, self.active_mastodon_account_id, quote_status_id=quoted_id)
            dlg.ShowModal()
            dlg.Destroy()
            return

        # modo == "boost"
        def _cb(res, err):
            if err:
                notificar(_("No se pudo impulsar: {0}").format(err))
            else:
                speak(_("Publicación impulsada."), interrupt=True)
                # No reconstruir la lista: solo marcar este toot como impulsado en su sitio. Así te
                # quedas en él; el boost propio (entrada nueva) llega solo en el siguiente sondeo.
                self._refrescar_indicadores_toot(db_id)
        mastodon_service.ejecutar_accion_toot_async(self.active_mastodon_account_id, db_id, "reblog", callback=_cb)

    def _on_nuevo_hilo(self, event):
        if not self.active_mastodon_account_id:
            speak(_("No hay ninguna cuenta activa de Mastodon seleccionada."), interrupt=True)
            return
        from .thread_editor import ThreadEditorDialog
        dlg = ThreadEditorDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_ver_hilos_guardados(self, event):
        if not self.active_mastodon_account_id:
            speak(_("No hay ninguna cuenta activa de Mastodon seleccionada."), interrupt=True)
            return
        # Lanzar diálogo de hilos guardados. Si el usuario elige "Editar / Reanudar",
        # abrir el editor de hilos con ese borrador cargado.
        from .thread_editor import HilosLocalesDialog, ThreadEditorDialog
        dlg = HilosLocalesDialog(self, self.active_mastodon_account_id)
        resultado = dlg.ShowModal()
        thread_id = dlg.selected_thread_id
        dlg.Destroy()
        if resultado == wx.ID_OK and thread_id:
            editor = ThreadEditorDialog(self, self.active_mastodon_account_id, thread_id=thread_id)
            editor.ShowModal()
            editor.Destroy()

    def _on_ver_detalles_toot(self, db_id: str):
        # Abrir el Visor de Hilos (thread_viewer)
        from .thread_viewer import ThreadViewerDialog
        dlg = ThreadViewerDialog(self, self.active_mastodon_account_id, db_id)
        dlg.ShowModal()
        dlg.Destroy()

    # --- RSS ACCIONES ---
    def _on_add_feed(self, event):
        if not self.active_rss_account_id:
            # Obtener primera cuenta RSS si existe
            row = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1")
            if row:
                self.active_rss_account_id = row[0]
            else:
                speak(_("Cree primero una cuenta RSS en Preferencias."), interrupt=True)
                return
                
        from .rss_views import AddFeedDialog
        dlg = AddFeedDialog(self, self.active_rss_account_id)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_navegacion()
        dlg.Destroy()

    def _on_buscar_feeds(self, event=None, consulta=""):
        """Buscador de feeds y podcasts en directorios públicos (rss_buscar_feeds.py)."""
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            speak(_("Cree primero una cuenta RSS en Preferencias."), interrupt=True)
            return
        from .rss_buscar_feeds import BuscarFeedsDialog
        dlg = BuscarFeedsDialog(self, acc, consulta)
        dlg.ShowModal()
        suscritos = dlg.suscritos
        dlg.Destroy()
        if suscritos:
            self._cargar_navegacion()

    def _on_crear_feed_web(self, event):
        """Crea un feed desde una web sin RSS (web scraper)."""
        if not self.active_rss_account_id:
            row = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1")
            if row:
                self.active_rss_account_id = row[0]
            else:
                speak(_("Cree primero una cuenta RSS en Preferencias."), interrupt=True)
                return
        from .rss_views import CrearFeedWebDialog
        dlg = CrearFeedWebDialog(self, self.active_rss_account_id)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_navegacion()
        dlg.Destroy()

    def _on_gestionar_categorias(self, event):
        from .rss_views import CategoriasDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            return
        dlg = CategoriasDialog(self, acc)
        dlg.ShowModal()
        self._cargar_navegacion()
        dlg.Destroy()

    def _on_gestionar_reglas(self, event):
        from .rss_views import ReglasDialog
        dlg = ReglasDialog(self, self.active_rss_account_id)
        dlg.ShowModal()
        aplicadas = getattr(dlg, "aplicadas", False)
        dlg.Destroy()
        # Se aplicaron reglas a los artículos ya guardados: refrescar lista y contadores.
        if aplicadas and getattr(self, "_modo_lista", "") == "rss":
            self._cargar_listado_artículos()
            self._actualizar_contadores_rss()

    def _on_add_youtube_channel(self, event):
        if not self.active_rss_account_id:
            row = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1")
            if row:
                self.active_rss_account_id = row[0]
            else:
                speak(_("Cree primero una cuenta RSS en Preferencias."), interrupt=True)
                return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self, _("URL del canal de YouTube o su ID (UC...):"), _("Añadir canal de YouTube"))
        ok = dlg.ShowModal() == wx.ID_OK
        valor = dlg.GetValue().strip() if ok else ""
        dlg.Destroy()
        if not valor:
            return
        acc = self.active_rss_account_id
        speak(_("Añadiendo canal..."), interrupt=True)

        def _bg(ctx):
            return rss_service.añadir_canal_youtube(acc, valor, ctx=ctx)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo añadir el canal: {0}").format(err))
            else:
                notificar(_("Canal de YouTube añadido: {0}").format(res or ""))
                self._cargar_navegacion()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _primera_cuenta_rss(self):
        """Cuenta RSS por defecto cuando no hay ninguna activa (menús globales)."""
        r = db.query_one(
            "SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1 "
            "ORDER BY position, id LIMIT 1")
        return r[0] if r else None

    def _on_busquedas_guardadas(self, event):
        from .accessible_controls import CustomTextEntryDialog, confirmar
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            return
        guardadas = rss_service.obtener_busquedas_guardadas(acc)
        opciones = [f"{b['name']}: {b['query']}" for b in guardadas]
        acciones_extra = [_("[Crear nueva búsqueda guardada]")]
        sel = wx.GetSingleChoice(
            _("Búsquedas guardadas (elige una para ejecutarla):"),
            _("Búsquedas guardadas RSS"), acciones_extra + opciones, self)
        if not sel:
            return
        if sel == acciones_extra[0]:
            d1 = CustomTextEntryDialog(self, _("Texto de la búsqueda:"), _("Nueva búsqueda guardada"))
            consulta = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
            d1.Destroy()
            if not consulta:
                return
            d2 = CustomTextEntryDialog(self, _("Nombre para guardarla:"), _("Nueva búsqueda guardada"),
                                       value=consulta[:40])
            nombre = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
            d2.Destroy()
            if nombre:
                rss_service.crear_busqueda_guardada(acc, nombre, consulta)
                notificar(_("Búsqueda guardada."))
                if config.get("rss", "saved_searches_in_tree", True):
                    self._cargar_navegacion()     # aparece como buffer del árbol
            return
        # Ejecutar la búsqueda elegida (o preguntar si se quiere borrar).
        b = guardadas[opciones.index(sel)]
        que = wx.GetSingleChoice(
            _("Búsqueda «{0}». ¿Qué quieres hacer?").format(b["name"]),
            _("Búsqueda guardada"),
            [_("Buscar ahora"), _("Editar esta búsqueda"), _("Borrar esta búsqueda"),
             _("No hacer nada")], self)
        if que == _("Buscar ahora"):
            from .search_dialog import GlobalSearchDialog
            dlg = GlobalSearchDialog(self, query=b["query"], solo_rss=True)
            dlg.ShowModal()
            dlg.Destroy()
        elif que == _("Editar esta búsqueda"):
            d1 = CustomTextEntryDialog(self, _("Texto de la búsqueda:"), _("Editar búsqueda guardada"),
                                       value=b["query"])
            consulta = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
            d1.Destroy()
            if not consulta:
                return
            d2 = CustomTextEntryDialog(self, _("Nombre:"), _("Editar búsqueda guardada"),
                                       value=b["name"])
            nombre = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
            d2.Destroy()
            if nombre:
                rss_service.editar_busqueda_guardada(b["id"], nombre, consulta)
                notificar(_("Búsqueda actualizada."))
                if config.get("rss", "saved_searches_in_tree", True):
                    self._cargar_navegacion()
        elif que == _("Borrar esta búsqueda"):
            if confirmar(self, _("¿Borrar la búsqueda guardada «{0}»?").format(b["name"]),
                         _("Borrar búsqueda")):
                rss_service.borrar_busqueda_guardada(b["id"])
                self._busqueda_borrada_fuera_del_arbol(b["id"])
                notificar(_("Búsqueda borrada."))

    def _on_alertas_palabra(self, event):
        from .accessible_controls import CustomTextEntryDialog, confirmar
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            return
        alertas = rss_service.obtener_alertas(acc)
        opciones = [(_("{0} (regex)") if a["is_regex"] else _("{0}")).format(a["keyword"])
                    + ("" if a["active"] else _(" [desactivada]")) for a in alertas]
        extra = [_("[Crear nueva alerta]")]
        sel = wx.GetSingleChoice(
            _("Alertas por palabra clave. Te avisan cuando llega un artículo que las contiene:"),
            _("Alertas RSS"), extra + opciones, self)
        if not sel:
            return
        if sel == extra[0]:
            d = CustomTextEntryDialog(self, _("Palabra o frase a vigilar:"), _("Nueva alerta"))
            kw = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
            d.Destroy()
            if not kw:
                return
            es_regex = confirmar(self, _("¿Es una expresión regular?"), _("Nueva alerta"))
            rss_service.crear_alerta(acc, kw, es_regex)
            notificar(_("Alerta creada."))
            return
        a = alertas[opciones.index(sel)]
        # Antes solo se podía borrar: ahora también editar y activar/desactivar.
        opc_editar = _("Editar la alerta")
        opc_estado = _("Desactivar la alerta") if a["active"] else _("Activar la alerta")
        opc_borrar = _("Borrar la alerta")
        que = wx.GetSingleChoice(_("Alerta «{0}». ¿Qué quieres hacer?").format(a["keyword"]),
                                 _("Alerta RSS"), [opc_editar, opc_estado, opc_borrar,
                                                   _("No hacer nada")], self)
        if que == opc_editar:
            d = CustomTextEntryDialog(self, _("Palabra o frase a vigilar:"), _("Editar alerta"),
                                      value=a["keyword"])
            kw = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
            d.Destroy()
            if not kw:
                return
            es_regex = confirmar(self, _("¿Es una expresión regular?"), _("Editar alerta"))
            rss_service.editar_alerta(a["id"], kw, bool(es_regex))
            notificar(_("Alerta actualizada."))
        elif que == opc_estado:
            rss_service.activar_alerta(a["id"], not a["active"])
            notificar(_("Alerta desactivada.") if a["active"] else _("Alerta activada."))
        elif que == opc_borrar:
            if confirmar(self, _("¿Borrar la alerta «{0}»?").format(a["keyword"]), _("Borrar alerta")):
                rss_service.borrar_alerta(a["id"])
                notificar(_("Alerta borrada."))

    def _on_toggle_solo_no_leidos(self, event):
        nuevo = self.item_solo_no_leidos.IsChecked()
        config.set("rss", "show_only_unread", nuevo)
        if self.active_rss_account_id:
            self._cargar_listado_artículos()
        speak(_("Mostrando solo no leídos.") if nuevo else _("Mostrando todos los artículos."), interrupt=True)

    def _on_toggle_agrupar_fecha(self, event):
        config.set("rss", "group_by_date", self.item_agrupar_fecha.IsChecked())
        if self.active_rss_account_id:
            self._cargar_listado_artículos()
        speak(_("Agrupar por fecha activado.") if self.item_agrupar_fecha.IsChecked() else _("Agrupación desactivada."), interrupt=True)

    def _on_toggle_ocultar_futuros(self, event):
        nuevo = self.item_ocultar_futuros.IsChecked()
        config.set("rss", "hide_future_articles", nuevo)
        if self.active_rss_account_id:
            self._cargar_listado_artículos()
        speak(_("Ocultando los artículos anclados con fecha futura.") if nuevo
              else _("Mostrando también los artículos anclados."), interrupt=True)

    def _bucket_fecha(self, fecha_iso):
        """Devuelve la etiqueta de grupo (Hoy, Ayer, Esta semana, o la fecha)."""
        try:
            from datetime import datetime
            d = datetime.fromisoformat(str(fecha_iso)).date()
            hoy = datetime.now().date()
            dias = (hoy - d).days
            if dias <= 0:
                return _("Hoy")
            if dias == 1:
                return _("Ayer")
            if dias < 7:
                return _("Esta semana")
            if dias < 30:
                return _("Este mes")
            return d.strftime("%Y-%m")
        except Exception:
            return ""

    def _on_estadisticas_categoria(self, category_id):
        """Muestra total, no leídos y destacados de una categoría."""
        row = db.query_one("""
            SELECT COUNT(*),
                   SUM(CASE WHEN a.read = 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN a.favorite = 1 THEN 1 ELSE 0 END)
            FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
            WHERE f.category_id = ? AND COALESCE(a.deleted, 0) = 0
        """, (category_id,))
        cat = db.query_one("SELECT name FROM rss_categories WHERE id = ?", (category_id,))
        nombre = cat[0] if cat else ""
        total = row[0] if row else 0
        no_leidos = (row[1] or 0) if row else 0
        destacados = (row[2] or 0) if row else 0
        speak(_("Categoría {0}: {1} artículos, {2} sin leer, {3} destacados.").format(
            nombre, total, no_leidos, destacados), interrupt=True)

    def _on_crear_subcategoria(self, parent_id):
        """Crea una subcarpeta dentro de la categoría indicada."""
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Nombre de la nueva subcarpeta:"), _("Crear subcarpeta"))
        if dlg.ShowModal() == wx.ID_OK:
            nombre = dlg.GetValue().strip()
            if nombre:
                try:
                    acc = self.active_rss_account_id or self._primera_cuenta_rss()
                    rss_service.agregar_categoria(acc, nombre, parent_id=parent_id)
                    self._cargar_navegacion()
                    notificar(_("Subcarpeta creada. Mueve feeds a ella desde su menú contextual."))
                except Exception as e:
                    notificar(_("No se pudo crear: {0}").format(e))
        dlg.Destroy()

    def _on_mover_categoria(self, category_id):
        """Cambia la carpeta padre de una categoría (o la deja en la raíz). Solo se
        ofrecen carpetas de LA MISMA CUENTA (auditoría RSS-01)."""
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        cats = db.query("SELECT id, name FROM rss_categories WHERE id != ? AND account_id = ? "
                        "ORDER BY name", (category_id, acc))
        # Evitar mover una categoría dentro de sí misma o de sus descendientes.
        descendientes = self._descendientes_categoria(category_id)
        opciones_ids = [None] + [c[0] for c in cats if c[0] not in descendientes]
        opciones_txt = [_("(Raíz, sin carpeta superior)")] + [c[1] for c in cats if c[0] not in descendientes]
        dlg = wx.SingleChoiceDialog(self, _("Mover la carpeta dentro de:"), _("Mover carpeta"), opciones_txt)
        if dlg.ShowModal() == wx.ID_OK:
            nuevo_padre = opciones_ids[dlg.GetSelection()]
            db.execute("UPDATE rss_categories SET parent_id = ? WHERE id = ?", (nuevo_padre, category_id))
            self._cargar_navegacion()
            speak(_("Carpeta movida."), interrupt=True)
        dlg.Destroy()

    def _descendientes_categoria(self, category_id):
        """Conjunto de ids de categorías descendientes (para evitar ciclos)."""
        todas = db.query("SELECT id, COALESCE(parent_id, 0) FROM rss_categories")
        hijos = {}
        for cid, pid in todas:
            hijos.setdefault(pid, []).append(cid)
        res = set()
        pila = [category_id]
        while pila:
            actual = pila.pop()
            for h in hijos.get(actual, []):
                if h not in res:
                    res.add(h)
                    pila.append(h)
        res.add(category_id)
        return res

    def _on_abrir_papelera(self, event):
        if not self.active_rss_account_id:
            fila = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' ORDER BY position, id LIMIT 1")
            cuenta = fila[0] if fila else None
        else:
            cuenta = self.active_rss_account_id
        if not cuenta:
            speak(_("No hay ninguna cuenta RSS."), interrupt=True)
            return
        from .trash_dialog import TrashDialog
        dlg = TrashDialog(self, cuenta)
        dlg.ShowModal()
        dlg.Destroy()
        if self.active_rss_account_id:
            self._cargar_listado_artículos()
            self._actualizar_contadores_rss()

    def _on_limpiar_articulos_rss(self, event):
        """Elimina artículos antiguos según preferencias (N.8)."""
        dias = config.get("rss", "keep_articles_days", 0)
        if not dias or int(dias) <= 0:
            speak(_("La limpieza por antigüedad está desactivada. Active 'Conservar artículos X días' en Preferencias, RSS."), interrupt=True)
            return
        speak(_("Limpiando artículos antiguos..."), interrupt=True)

        def _bg(ctx):
            return rss_service.limpieza_mantenimiento(self.active_rss_account_id)

        def _finish(res, err):
            if err:
                notificar(_("Error en la limpieza: {0}").format(err))
            else:
                if self.active_rss_account_id:
                    self._cargar_listado_artículos()
                    self._actualizar_contadores_rss()
                notificar(_("Limpieza completada. {0} artículos eliminados.").format(res or 0))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_optimizar_bd(self, event):
        """Comprueba integridad y compacta la base de datos (N.8)."""
        speak(_("Comprobando y optimizando la base de datos..."), interrupt=True)

        def _bg(ctx):
            return rss_service.optimizar_base_datos()

        def _finish(res, err):
            if err or not res:
                notificar(_("Error al optimizar: {0}").format(err or ""))
            elif res.get("ok"):
                speak(_("Base de datos optimizada. Integridad: {0}.").format(res.get("integridad", "")), interrupt=True)
            else:
                notificar(_("No se pudo optimizar: {0}").format(res.get("error", "")))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _marcar_leido_rss(self, db_id: str, index: int = None):
        """Marca un artículo como leído (si la opción está activa) y refresca fila/contadores."""
        if not config.get("rss", "mark_read_on_open", True):
            return
        # En la BANDEJA la lista no es la de RSS: no se toca (antes, un artículo de "No
        # leídos" abierto desde la bandeja recargaba la lista de RSS ENCIMA de la bandeja).
        if getattr(self, "_bandeja_activa", False):
            index = None
        fila = db.query_one("SELECT read FROM rss_articles WHERE id = ?", (db_id,))
        if fila and not fila[0]:
            db.execute("UPDATE rss_articles SET read = 1 WHERE id = ?", (db_id,))
            # Si el artículo está en la lista visible, refrescar su fila o quitarlo
            if index is not None and 0 <= index < len(self.lista_items_db_ids):
                if self.active_timeline_type == "unread":
                    self._cargar_listado_artículos()
                else:
                    self._refrescar_fila_rss(index)
            self._actualizar_contadores_rss()

    def _on_ver_articulo_limpio(self, db_id: str):
        try:
            # Buscar el índice visible del artículo para reflejar el estado
            index = self.lista_items_db_ids.index(db_id) if db_id in self.lista_items_db_ids else None
            self._marcar_leido_rss(db_id, index)
            from .rss_views import CleanArticleViewer
            dlg = CleanArticleViewer(self, db_id)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            log.error(f"Error al abrir el lector limpio del artículo {db_id}: {e}", exc_info=True)
            speak(_("No se pudo abrir el lector limpio: {0}").format(e), interrupt=True)

    def _on_leer_tts_mastodon(self, db_id: str):
        """Abre el lector en voz alta (TTS) con el texto del toot."""
        row = db.query_one("SELECT content FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            speak(_("No hay texto que leer."), interrupt=True)
            return
        from app.core.html_utils import html_a_texto
        texto = html_a_texto(row[0])
        from .tts_dialog import TTSReaderDialog
        dlg = TTSReaderDialog(self, _("Publicación"), texto)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_leer_tts_rss(self, db_id: str):
        """Abre el lector en voz alta (TTS) con el texto limpio del artículo."""
        row = db.query_one("SELECT title, content FROM rss_articles WHERE id = ?", (db_id,))
        if not row:
            notificar(_("No se encontró el artículo."))
            return
        titulo = row[0] or _("Artículo")
        from app.core.html_utils import html_a_texto
        cuerpo = html_a_texto(row[1] or "")
        texto = f"{titulo}.\n\n{cuerpo}".strip()
        # Marcar como leído, igual que al abrirlo de otras formas
        index = self.lista_items_db_ids.index(db_id) if db_id in self.lista_items_db_ids else None
        self._marcar_leido_rss(db_id, index)
        from .tts_dialog import TTSReaderDialog
        dlg = TTSReaderDialog(self, titulo, texto)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_ver_articulo_html(self, db_id: str):
        """Genera un documento HTML con el contenido del artículo y lo abre en el navegador."""
        index = self.lista_items_db_ids.index(db_id) if db_id in self.lista_items_db_ids else None
        self._marcar_leido_rss(db_id, index)
        row = db.query_one("""
            SELECT a.title, f.title, a.published_at, a.content, a.link
            FROM rss_articles a
            JOIN rss_feeds f ON a.feed_id = f.id
            WHERE a.id = ?
        """, (db_id,))
        if not row:
            speak(_("Artículo no encontrado."), interrupt=True)
            return

        title, feed_name, date, content, link = row
        if not content:
            speak(_("Este artículo no tiene contenido HTML local. Abriendo enlace original."), interrupt=True)
            if link:
                from app.core.privacy import limpiar_url
                abrir_url(limpiar_url(link))
            return

        try:
            from app.core.html_utils import html_a_documento
            from app.core.paths import get_data_path
            import hashlib

            fuente = _("Fuente: {feed} · {date}").format(feed=feed_name, date=formatear_tiempo_relativo(str(date)))
            documento = html_a_documento(title, content, fuente=fuente, enlace=link)

            nombre = "articulo_" + hashlib.sha256(str(db_id).encode()).hexdigest()[:16] + ".html"
            ruta = get_data_path("temp") / nombre
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write(documento)

            abrir_url(ruta.as_uri())
            speak(_("Abriendo artículo como página HTML en el navegador."), interrupt=True)
        except Exception as e:
            log.error(f"Error al generar vista HTML del artículo: {e}")
            notificar(_("No se pudo generar la vista HTML."))

    # --- REPRODUCTOR MULTIMEDIA ---
    # ------------------------------------------------------------------
    # ATAJOS GLOBALES DE LOS REPRODUCTORES (F1..F8 en toda la aplicación)
    # ------------------------------------------------------------------
    _PASO_VOLUMEN_GLOBAL = 0.05
    _VELOCIDADES_GLOBAL = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

    def _reproductor_activo(self):
        """Determina qué reproductor está sonando para actuar en consecuencia.

        Devuelve una tupla (tipo, kind):
          - ("social", None): el reproductor de redes sociales (Mastodon/Bluesky/Telegram).
          - ("media2", kind): el segundo reproductor con un podcast o vídeo de YouTube.
          - ("radio", None): el segundo reproductor con una emisora de radio.
          - (None, None): no hay nada reproduciéndose.
        El reproductor de redes sociales tiene prioridad (silencia/pausa al segundo)."""
        try:
            e1 = player_service.backend.obtener_estado()
        except Exception:
            e1 = "inactivo"
        if e1 in ("reproduciendo", "pausado", "esperando"):
            return ("social", None)
        try:
            from app.services.media_player.player2_service import player2_service, KIND_RADIO
            e2 = player2_service.backend.obtener_estado()
            kind = player2_service.current_kind
        except Exception:
            e2, kind, KIND_RADIO = "inactivo", "", "radio"
        if e2 in ("reproduciendo", "pausado", "esperando"):
            if kind == KIND_RADIO:
                return ("radio", None)
            return ("media2", kind)
        return (None, None)

    def _manejar_tecla_reproductor(self, accion) -> bool:
        """Punto de entrada de los atajos globales de los reproductores. `accion` es una acción del
        catálogo de teclas (media_play_pausa, media_avanzar…), no una tecla concreta: así funcionan
        igual con las teclas por defecto (F1..F8) y con las que el usuario haya personalizado.
        Actúa sobre el reproductor que esté sonando y devuelve True si consumió la pulsación."""
        tipo, kind = self._reproductor_activo()
        if tipo is None:
            speak(_("No hay nada reproduciéndose."), interrupt=True)
            return True
        if tipo == "radio":
            return self._tecla_radio(accion)
        return self._tecla_media(accion, tipo)

    def _paso_seek_global(self, tipo: str, atras: bool) -> float:
        if tipo == "social":
            clave = "default_step_backward" if atras else "default_step_forward"
            defecto = 10.0
        else:
            clave = "default_step_backward2" if atras else "default_step_forward2"
            defecto = 15.0
        try:
            return float(config.get("media_player", clave, defecto))
        except (TypeError, ValueError):
            return defecto

    def _ajustar_volumen_global(self, srv, delta: float):
        try:
            vol = max(0.0, min(1.0, srv.backend.obtener_volumen() + delta))
        except Exception:
            return
        srv.establecer_volumen(vol)
        speak(_("Volumen {0}%.").format(int(round(vol * 100))), interrupt=True)

    def _ajustar_velocidad_global(self, srv, delta: int):
        vels = self._VELOCIDADES_GLOBAL
        try:
            actual = srv.backend.obtener_velocidad()
        except Exception:
            actual = 1.0
        from app.services.media_player.player2_service import player2_service
        if srv is player2_service:
            # Reproductor de podcasts: de 0,1 en 0,1 entre 0,5x y 3x.
            nueva = max(0.5, min(3.0, round(actual + 0.1 * delta, 1)))
            srv.establecer_velocidad(nueva)
            speak(_("Velocidad {0}x.").format(f"{nueva:g}".replace(".", ",")), interrupt=True)
            return
        idx = min(range(len(vels)), key=lambda i: abs(vels[i] - actual))
        idx = max(0, min(len(vels) - 1, idx + delta))
        srv.establecer_velocidad(vels[idx])
        speak(_("Velocidad {0}x.").format(f"{vels[idx]:g}"), interrupt=True)

    def _anunciar_posicion_global(self, srv):
        try:
            info = srv.obtener_informacion()
            pos = float(info.get("position", 0.0))
            dur = float(info.get("duration", 0.0))
        except Exception:
            return
        pos_str = f"{int(pos // 60):02d}:{int(pos % 60):02d}"
        if dur > 0.0:
            dur_str = f"{int(dur // 60):02d}:{int(dur % 60):02d}"
            speak(f"{pos_str} / {dur_str}", interrupt=True)
        else:
            speak(pos_str, interrupt=True)

    def _tecla_media(self, accion: str, tipo: str) -> bool:
        """Controles para redes sociales y para podcast/YouTube (RSS): atrasar, reproducir/
        pausar, adelantar, detener, volumen y velocidad."""
        if tipo == "social":
            srv = player_service
        else:
            from app.services.media_player.player2_service import player2_service
            srv = player2_service
        if accion == "media_retroceder":
            srv.retroceder_segundos(self._paso_seek_global(tipo, atras=True))
            self._anunciar_posicion_global(srv)
        elif accion == "media_play_pausa":
            try:
                est = srv.backend.obtener_estado()
            except Exception:
                est = "inactivo"
            if est == "reproduciendo":
                srv.pausar()
                speak(_("Pausado."), interrupt=True)
            else:
                srv.reproducir()
                speak(_("Reproduciendo."), interrupt=True)
        elif accion == "media_avanzar":
            srv.avanzar_segundos(self._paso_seek_global(tipo, atras=False))
            self._anunciar_posicion_global(srv)
        elif accion == "media_detener":
            # En redes sociales, detener LIMPIA la sesión (no deja el último cargado) pero
            # guarda la posición para poder retomarla preguntando al reproducir de nuevo.
            if tipo == "social":
                srv.detener_y_limpiar()
            else:
                srv.detener()
            speak(_("Detenido."), interrupt=True)
        elif accion == "media_bajar_volumen":
            self._ajustar_volumen_global(srv, -self._PASO_VOLUMEN_GLOBAL)
        elif accion == "media_subir_volumen":
            self._ajustar_volumen_global(srv, +self._PASO_VOLUMEN_GLOBAL)
        elif accion == "media_bajar_velocidad":
            self._ajustar_velocidad_global(srv, -1)
        elif accion == "media_subir_velocidad":
            self._ajustar_velocidad_global(srv, +1)
        return True

    def _tecla_radio(self, accion: str) -> bool:
        """Controles para la radio: favoritos, silenciar, recargar, detener y volumen. En la radio
        no hay avance ni velocidad, así que esas acciones se reinterpretan (retroceder = favoritos,
        reproducir/pausar = silenciar, avanzar = recargar) o avisan de que no valen."""
        from app.services.media_player.player2_service import player2_service
        if accion == "media_retroceder":
            self._abrir_favoritos_radio()
        elif accion == "media_play_pausa":
            silenciado = player2_service.alternar_silencio()
            speak(_("Emisora silenciada.") if silenciado
                  else _("Sonido de la emisora activado."), interrupt=True)
        elif accion == "media_avanzar":
            player2_service.recargar()
            speak(_("Recargando la emisora."), interrupt=True)
        elif accion == "media_detener":
            player2_service.detener()
            speak(_("Emisora detenida."), interrupt=True)
        elif accion == "media_bajar_volumen":
            self._ajustar_volumen_global(player2_service, -self._PASO_VOLUMEN_GLOBAL)
        elif accion == "media_subir_volumen":
            self._ajustar_volumen_global(player2_service, +self._PASO_VOLUMEN_GLOBAL)
        elif accion in ("media_bajar_velocidad", "media_subir_velocidad"):
            speak(_("Esa tecla no es válida durante la reproducción de la radio."),
                  interrupt=True)
        return True

    def _abrir_favoritos_radio(self):
        """Muestra el diálogo rápido de favoritos de radio (la tecla de retroceder, F1 por
        defecto, durante la radio)."""
        if self._dlg_fav_radio_abierto:
            return
        from .radio_dialog import DialogoFavoritosRadio
        self._dlg_fav_radio_abierto = True
        try:
            dlg = DialogoFavoritosRadio(self)
            dlg.ShowModal()
            dlg.Destroy()
        finally:
            self._dlg_fav_radio_abierto = False

    def _on_abrir_reproductor(self, event):
        """Abre el reproductor (no modal) o lo trae al frente si ya está abierto.
        Es invocable desde cualquier pantalla con la tecla global Ctrl+Alt+P."""
        from .player.player_dialog import PlayerDialog
        existente = getattr(self, "_player_dlg", None)
        if existente:
            try:
                if existente.IsShown():
                    existente.Raise()
                    existente.SetFocus()
                    return
            except Exception:
                self._player_dlg = None  # ventana ya destruida

        dlg = PlayerDialog(self)
        self._player_dlg = dlg

        def _al_cerrar(evt):
            self._player_dlg = None
            evt.Skip()
        dlg.Bind(wx.EVT_WINDOW_DESTROY, _al_cerrar)
        dlg.Show()
        dlg.Raise()
        dlg.SetFocus()

    def _on_abrir_radio(self, event):
        """Abre el diálogo de radio por internet."""
        from .radio_dialog import RadioDialog
        dlg = RadioDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_abrir_reproductor2(self, event):
        """Abre (o trae al frente) el reproductor de radio, podcast y YouTube."""
        from .player.player2_dialog import Player2Dialog
        existente = getattr(self, "_player2_dlg", None)
        if existente:
            try:
                if existente.IsShown():
                    existente.Raise()
                    existente.SetFocus()
                    return
            except Exception:
                self._player2_dlg = None
        dlg = Player2Dialog(self)
        self._player2_dlg = dlg

        def _al_cerrar(evt):
            self._player2_dlg = None
            evt.Skip()
        dlg.Bind(wx.EVT_WINDOW_DESTROY, _al_cerrar)
        dlg.Show()
        dlg.Raise()
        dlg.SetFocus()

    # --- INTELEGENCIA ARTIFICIAL ---
    def _on_describir_imagen_ia(self, event):
        """Describe la imagen adjunta en el toot seleccionado."""
        self._analizar_imagen_toot("describir")

    def _on_ocr_imagen_ia(self, event):
        """Extrae el texto literal (OCR) de la imagen del toot seleccionado."""
        self._analizar_imagen_toot("ocr")

    def _on_ocr_local_imagen(self, event):
        """OCR con el motor de Windows (sin IA ni conexión). Si falta el componente, avisa."""
        from app.services.ai import ocr_local
        if not ocr_local.disponible():
            from app.core.speech import notificar
            notificar(_("El OCR local no está disponible: falta el componente 'winsdk'. "
                        "Instálalo desde build.bat (dependencias) o usa el OCR con IA."))
            return
        self._analizar_imagen_toot("ocr_local")
