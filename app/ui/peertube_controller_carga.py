# -*- coding: utf-8 -*-
"""Controlador de PeerTube: construcción del árbol, carga y render de listados de vídeos (mixin).

Fase 2: catálogos (recientes/populares/tendencias/locales/federados/suscripciones), feeds de un
canal/cuenta y resultados de búsqueda, volcados en la lista principal virtual con la plantilla
`peertube_video`. La reproducción real llega en la Fase 3; aquí Intro abre el detalle del vídeo.
"""
import logging
from collections import defaultdict

from app.core.config_manager import config, limite_render
from app.core.database import db
from app.core.speech import speak
from app.core.task_manager import TareaDuplicadaError

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)

# Columnas leídas de peertube_videos para construir cada fila (orden fijo, ver _valores_video).
_SQL_COLS_VIDEO = ("v.id, v.name, v.channel_display, v.channel_name, v.duration, v.published_at, "
                   "v.views, v.likes, v.is_live, v.nsfw, v.is_local, v.description, v.language, "
                   "v.uuid, v.url")

# Secciones cuyo contenido son vídeos (se vuelcan en la lista principal con la plantilla de vídeo).
_SECCIONES_VIDEO = ("recientes", "populares", "tendencias", "locales", "federados", "suscripciones",
                    "historial", "mis_videos", "directos", "me_gusta", "ver_mas_tarde")

# "Mi biblioteca" es una RAMA que agrupa lo que el usuario posee o ha marcado. Sus sub-secciones
# cuelgan de ella en el árbol (no se repiten como secciones sueltas de primer nivel).
_BIBLIOTECA_HIJAS = ("mis_videos", "mis_canales", "listas", "ver_mas_tarde", "me_gusta", "historial")


class _CargaMixin:
    # ------------------------------------------------------------------
    # Árbol de navegación
    # ------------------------------------------------------------------
    def construir_servicio(self, root):
        """Construye el nodo del servicio PeerTube: un nodo raíz, una cuenta por instancia y, bajo
        cada cuenta, las secciones configuradas. "Mi biblioteca" y "Búsquedas guardadas" son ramas
        que agrupan sus sub-secciones (no aparecen como listas sueltas)."""
        tree = self.f.tree_nav
        n_root = tree.AppendItem(root, _("PeerTube"),
                                 data={"type": "category_root", "service": "peertube"})
        accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'peertube' AND active = 1 "
                        "ORDER BY position, id")
        orden = list(config.get("peertube", "section_order", list(self.SECTION_LABELS.keys())))
        ocultas = set(config.get("peertube", "hidden_sections", []) or [])
        for a_id, a_name in accs:
            n_acc = tree.AppendItem(n_root, a_name,
                                    data={"type": "account", "account_id": a_id, "service": "peertube"})
            self._construir_secciones_cuenta(tree, n_acc, a_id, orden, ocultas)
        return n_root

    def _nodo_seccion(self, tree, padre, a_id, sec, label=None):
        """Añade un nodo de sección de vídeos/diálogo bajo `padre`."""
        label = label or self.SECTION_LABELS.get(sec)
        if not label:
            return
        tree.AppendItem(padre, label, data={
            "type": "peertube_section", "peertube_type": sec,
            "account_id": a_id, "service": "peertube", "base_label": label})

    def _construir_secciones_cuenta(self, tree, n_acc, a_id, orden, ocultas):
        """Vuelca las secciones de una cuenta. `renderizadas` evita duplicados: las sub-secciones de
        "Mi biblioteca" cuelgan de su rama y NO se repiten como secciones de primer nivel aunque
        section_order (plano, de instalaciones previas) las liste también ahí."""
        renderizadas = set()
        for sec in orden:
            if sec in renderizadas:
                continue
            if sec == "biblioteca":
                # Ocultar la RAMA oculta también sus sub-secciones (se marcan como renderizadas para
                # que no reaparezcan sueltas en el primer nivel desde el section_order plano).
                renderizadas.add("biblioteca")
                renderizadas.update(_BIBLIOTECA_HIJAS)
                if "biblioteca" in ocultas:
                    continue
                n_bib = tree.AppendItem(n_acc, self.SECTION_LABELS.get("biblioteca", _("Mi biblioteca")),
                                        data={"type": "peertube_group", "group": "biblioteca",
                                              "account_id": a_id, "service": "peertube"})
                for hijo in _BIBLIOTECA_HIJAS:
                    if hijo not in ocultas:
                        self._nodo_seccion(tree, n_bib, a_id, hijo)
            elif sec == "busquedas":
                renderizadas.add("busquedas")
                if "busquedas" in ocultas:
                    continue
                # Rama "Búsquedas guardadas": cada búsqueda guardada cuelga de aquí. Se conserva
                # aunque esté vacía (es una sección fija; se oculta desde Herramientas → Buffers).
                n_bus = tree.AppendItem(n_acc, self.SECTION_LABELS.get("busquedas", _("Búsquedas guardadas")),
                                        data={"type": "peertube_group", "group": "busquedas",
                                              "account_id": a_id, "service": "peertube"})
                for termino in config.get("peertube", "saved_searches", []):
                    etiqueta = _("Búsqueda: {0}").format(termino)
                    self._nodo_seccion(tree, n_bus, a_id, "search:" + termino, label=etiqueta)
            else:
                renderizadas.add(sec)
                if sec in ocultas:
                    continue
                self._nodo_seccion(tree, n_acc, a_id, sec)

        # Compatibilidad: si la configuración guardada no incluye la rama "Búsquedas guardadas",
        # las búsquedas se cuelgan directamente de la cuenta (como antes) para no perderlas.
        if "busquedas" not in renderizadas:
            for termino in config.get("peertube", "saved_searches", []):
                etiqueta = _("Búsqueda: {0}").format(termino)
                self._nodo_seccion(tree, n_acc, a_id, "search:" + termino, label=etiqueta)

    def on_select(self, item, data, texto):
        """Selección de un nodo del árbol de PeerTube (cuenta, rama o sección)."""
        f = self.f
        tipo_nodo = data.get("type")
        if tipo_nodo in ("account", "peertube_group"):
            # La cuenta y las ramas (Mi biblioteca, Búsquedas) solo fijan el contexto; no cargan lista.
            f.active_peertube_account_id = data.get("account_id")
            return
        if tipo_nodo != "peertube_section":
            return
        f.active_peertube_account_id = data.get("account_id")
        f.active_peertube_type = data.get("peertube_type")
        base = data.get("base_label") or ""
        try:
            f.lbl_buffer_titulo.SetLabel("PeerTube - " + base)
        except Exception:
            pass
        self.cargar_listado()

    # ------------------------------------------------------------------
    # Carga del buffer activo
    # ------------------------------------------------------------------
    def _es_seccion_videos(self, tipo):
        return (tipo in _SECCIONES_VIDEO
                or str(tipo).startswith(("channel:", "account:", "search:", "playlist:")))

    def cargar_listado(self):
        f = self.f
        if not f.active_peertube_account_id:
            return
        tipo = f.active_peertube_type
        if self._es_seccion_videos(tipo):
            self._cargar_videos(tipo)
            if f.lista_items.GetItemCount() == 0:
                self.on_refresh(silencioso=True)
        elif tipo == "notificaciones":
            self.cargar_notificaciones()
        elif tipo in self._SECCIONES_DIALOGO:
            # Listas y Mis canales abren un diálogo. NO se abre al navegar (rompía la navegación por
            # el árbol): al seleccionar solo se avisa; se abre con Intro (ver abrir_con_intro).
            self._preparar_seccion_dialogo(tipo)
        else:
            # Sección desconocida (no debería ocurrir): dejar la lista vacía y consistente.
            self._preparar_columnas_videos()
            f._modo_lista = "peertube_videos"
            log.debug("Sección de PeerTube sin manejador: %s", tipo)

    # Secciones cuyo contenido es un DIÁLOGO de gestión (no una lista de vídeos). Clave -> método.
    _SECCIONES_DIALOGO = {"listas": "gestionar_listas_dialogo",
                          "mis_canales": "gestionar_canales_dialogo"}

    def _preparar_seccion_dialogo(self, tipo):
        """Al seleccionar en el árbol una sección que abre diálogo: dejar la lista vacía y avisar de
        que se abre con Intro (así navegar con las flechas no abre el diálogo sin querer)."""
        f = self.f
        self._preparar_columnas_videos()
        f._modo_lista = "peertube_dialogo"
        etiqueta = self.SECTION_LABELS.get(tipo, tipo)
        aviso = _("Pulsa Intro para gestionar «{0}».").format(etiqueta)
        try:
            f.txt_detalles.SetValue(aviso)
        except Exception:
            pass
        speak(aviso, interrupt=True)

    def abrir_con_intro(self, data):
        """Abre el diálogo de la sección seleccionada (Listas, Mis canales) al pulsar Intro en el
        árbol. Devuelve True si lo gestionó; False si ese nodo no abre diálogo."""
        metodo = self._SECCIONES_DIALOGO.get((data or {}).get("peertube_type"))
        if not metodo:
            return False
        getattr(self, metodo)()
        return True

    # ------------------------------------------------------------------
    # Menú contextual del árbol
    # ------------------------------------------------------------------
    def menu_contextual_arbol(self, menu, data, item):
        """Menú contextual de un nodo del árbol de PeerTube. Devuelve True si añadió opciones.
        En una búsqueda guardada: actualizar sus resultados o eliminarla del árbol."""
        import wx
        f = self.f
        tipo = str((data or {}).get("peertube_type") or "")
        if tipo.startswith("search:"):
            termino = tipo.split(":", 1)[1]
            it = menu.Append(wx.ID_ANY, _("Actualizar resultados"))
            f.Bind(wx.EVT_MENU, lambda e: (f.tree_nav.SelectItem(item), self.on_refresh()), it)
            it = menu.Append(wx.ID_ANY, _("Eliminar esta búsqueda guardada"))
            f.Bind(wx.EVT_MENU,
                   lambda e, t=termino, nodo=item: self.eliminar_busqueda_guardada(t, nodo), it)
            return True
        return False

    def eliminar_busqueda_guardada(self, termino, nodo=None):
        """Quita una búsqueda guardada de la configuración y su nodo del árbol. Solo se borra ESE
        nodo (regla del proyecto): reconstruir el árbol entero al borrar dejaba la app colgada."""
        guardadas = [t for t in config.get("peertube", "saved_searches", []) if t != termino]
        config.set("peertube", "saved_searches", guardadas)
        self.f._eliminar_nodo_buffer(nodo)
        from app.core.speech import notificar
        notificar(_("Búsqueda eliminada."))

    # ------------------------------------------------------------------
    # Render de vídeos
    # ------------------------------------------------------------------
    def _preparar_columnas_videos(self):
        f = self.f
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        self._cols_videos = f._construir_columnas_plantilla("peertube_video")

    @staticmethod
    def _formato_duracion(seg):
        """Segundos → 'm:ss' o 'h:mm:ss'. Cadena vacía si no hay duración (p. ej. un directo)."""
        try:
            s = int(seg or 0)
        except (TypeError, ValueError):
            return ""
        if s <= 0:
            return ""
        h, resto = divmod(s, 3600)
        m, sg = divmod(resto, 60)
        if h:
            return f"{h}:{m:02d}:{sg:02d}"
        return f"{m}:{sg:02d}"

    def _indicadores_video(self, is_live, nsfw, is_local):
        ind = []
        if is_live:
            ind.append(_("En directo"))
        if nsfw:
            ind.append(_("+18"))
        ind.append(_("Local") if is_local else _("Remoto"))
        return " ".join(ind)

    def _valores_video(self, r):
        """Construye los valores por campo de un vídeo (según _SQL_COLS_VIDEO) para la plantilla
        peertube_video. Devuelve (db_id, valores, fecha_iso, contenido_crudo)."""
        from app.ui.main_frame import formatear_tiempo_relativo
        (db_id, name, channel_display, channel_name, duration, published_at, views, likes,
         is_live, nsfw, is_local, description, language, uuid, url) = r
        canal = channel_display or channel_name or ""
        fecha_iso = published_at or ""
        valores = {
            "title": name or "",
            "channel": canal,
            "duration": _("En directo") if is_live else self._formato_duracion(duration),
            "date": formatear_tiempo_relativo(fecha_iso) if fecha_iso else "",
            "views": str(views or 0),
            "likes": str(likes or 0),
            "indicators": self._indicadores_video(is_live, nsfw, is_local),
            "description": (description or "").replace("\n", " ").strip(),
            "language": language or "",
        }
        contenido = f"{name or ''}. {description or ''}"
        return db_id, valores, fecha_iso, contenido

    def _consulta_videos(self, tipo, limite):
        return db.query(f"""
            SELECT {_SQL_COLS_VIDEO}
            FROM peertube_videos v
            JOIN peertube_video_buffers b ON v.id = b.video_db_id
            WHERE b.account_id = ? AND b.buffer = ?
            ORDER BY b.position ASC LIMIT ?
        """, (self.f.active_peertube_account_id, tipo, limite))

    def _cargar_videos(self, tipo):
        f = self.f
        self._preparar_columnas_videos()
        f._modo_lista = "peertube_videos"
        from app.core.content_filters import ContentFilterEngine
        from app.core.plantillas import pulir_lectura, definicion
        cols = self._cols_videos
        filtros = ContentFilterEngine.cargar("peertube")
        plantilla = (config.get("accessibility", "peertube_video", "")
                     or definicion("peertube_video")["default"])
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)
        limite = limite_render("peertube", "max_items_buffer")
        idx = 0
        for r in self._consulta_videos(tipo, limite):
            db_id, valores, fecha, contenido = self._valores_video(r)
            if filtros and ContentFilterEngine.evaluar("peertube", contenido, valores["title"],
                                                       filtros)["hide"]:
                continue
            pos = f._volcar_fila_plantilla(idx, cols, valores)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(
                pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, valores["date"])
            idx += 1
        f._restaurar_foco_lista()

    def _filas_videos_actual(self, tipo):
        """Filas deseadas (db_id, [cols], lectura, None, fecha) para la inserción incremental."""
        f = self.f
        filas = []
        if not f.active_peertube_account_id:
            return filas
        from app.core.content_filters import ContentFilterEngine
        from app.core.plantillas import pulir_lectura, definicion
        filtros = ContentFilterEngine.cargar("peertube")
        plantilla = (config.get("accessibility", "peertube_video", "")
                     or definicion("peertube_video")["default"])
        cols = getattr(f, "_cols_actuales", None) or getattr(self, "_cols_videos", [])
        for r in self._consulta_videos(tipo, limite_render("peertube", "max_items_buffer")):
            db_id, valores, fecha, contenido = self._valores_video(r)
            if filtros and ContentFilterEngine.evaluar("peertube", contenido, valores["title"],
                                                       filtros)["hide"]:
                continue
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura, None, fecha))
        return filas

    # ------------------------------------------------------------------
    # Descarga (manual y automática)
    # ------------------------------------------------------------------
    def on_refresh(self, silencioso=False):
        """Descarga el buffer activo. Con silencioso=True no anuncia inicio/fin (auto-carga)."""
        f = self.f
        tipo = f.active_peertube_type
        acc = f.active_peertube_account_id
        if not acc or not self._es_seccion_videos(tipo):
            return
        if not silencioso:
            speak(_("Actualizando..."), interrupt=True)

        def _fin(res, err):
            if isinstance(err, TareaDuplicadaError):
                return
            if err:
                from app.core.speech import notificar
                notificar(_("No se pudo actualizar PeerTube: {0}").format(err))
                return
            with f._foco_silencioso():
                self._cargar_videos(tipo)
            if not silencioso:
                speak(_("{0} vídeos nuevos.").format(res or 0), interrupt=True)

        try:
            peertube_service.cargar_seccion_async(acc, tipo, cargar_mas=False, callback=_fin)
        except TareaDuplicadaError:
            pass

    def actualizar_incremental(self):
        """Sondeo automático: descarga novedades e inserta solo lo nuevo sin mover el foco."""
        f = self.f
        tipo = getattr(f, "active_peertube_type", None)
        acc = getattr(f, "active_peertube_account_id", None)
        if not acc or not self._es_seccion_videos(tipo):
            return

        def _fin(res, err):
            if err or not res:
                return
            try:
                f._insertar_incremental(self._filas_videos_actual(tipo))
            except Exception:
                pass

        try:
            peertube_service.cargar_seccion_async(acc, tipo, cargar_mas=False, callback=_fin)
        except TareaDuplicadaError:
            pass

    def cargar_mas(self):
        """Descarga la página siguiente del buffer activo y la añade al final."""
        f = self.f
        tipo = f.active_peertube_type
        acc = f.active_peertube_account_id
        if not acc or not self._es_seccion_videos(tipo):
            return
        speak(_("Cargando más vídeos..."), interrupt=True)

        def _fin(res, err):
            if isinstance(err, TareaDuplicadaError):
                return
            if err:
                from app.core.speech import notificar
                notificar(_("No se pudieron cargar más vídeos: {0}").format(err))
                return
            with f._foco_silencioso():
                self._cargar_videos(tipo)
            speak(_("{0} vídeos más.").format(res or 0), interrupt=True)

        try:
            peertube_service.cargar_seccion_async(acc, tipo, cargar_mas=True, callback=_fin)
        except TareaDuplicadaError:
            pass
