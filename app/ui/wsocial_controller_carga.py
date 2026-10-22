# -*- coding: utf-8 -*-
"""Controlador de W Social (interfaz): árbol de buffers y volcado de listas.

Mixin de WSocialController. Aquí vive todo lo que llena la lista principal: publicaciones,
notificaciones, cuentas, conversaciones, feeds, listas, tendencias y paquetes de inicio.

Como en el resto de la aplicación, la lista se pinta SIEMPRE desde la caché local (así hay algo
que leer al instante, con red o sin ella) y la descarga va en segundo plano; cuando llega, la
lista se refresca sin mover el foco.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.services.wsocial import wsocial_service


def limite_render(clave: str, defecto: int = 0) -> int:
    """Tope de filas a pintar de un buffer (0 = sin tope). Se configura en Preferencias."""
    try:
        return int(config.get("wsocial", clave, defecto) or 0) or 1000000
    except (TypeError, ValueError):
        return 1000000


class _CargaMixin:
    # Columnas que se leen de la base para pintar una publicación.
    _SQL_COLS_POST = ("p.id, p.author_name, p.author_handle, p.content, p.indexed_at, "
                      "p.like_count, p.repost_count, p.reply_count, p.labels, p.repost_by_handle, "
                      "p.media_json, p.quote_count, p.langs, p.liked_uri, p.reposted_uri, "
                      "p.bookmarked, p.reply_parent_uri, p.quote_uri, p.verificado, "
                      "COALESCE(p.repost_by_name,''), "
                      # A quién responde: lo guardado al descargar o, si no se supo entonces, el
                      # autor del padre si está en la caché local (búsqueda por clave primaria).
                      "COALESCE(NULLIF(p.reply_parent_handle,''), (SELECT pp.author_handle "
                      "FROM wsocial_posts pp WHERE pp.id = p.account_id || ':' || p.reply_parent_uri), ''), "
                      "COALESCE(NULLIF(p.reply_parent_name,''), (SELECT pp.author_name "
                      "FROM wsocial_posts pp WHERE pp.id = p.account_id || ':' || p.reply_parent_uri), '')")

    # Buffers cuyo contenido son cuentas, no publicaciones.
    BUFFERS_ACTORES = ("followers", "follows", "blocks", "mutes")
    # Buffers "gestores": al abrirlos se descargan y se reconstruye el árbol.
    BUFFERS_GESTOR = ("feeds", "lists")

    # ------------------------------------------------------------------
    # ÁRBOL
    # ------------------------------------------------------------------
    def construir_servicio(self, root):
        """Construye el nodo de W Social en el árbol de navegación."""
        tree = self.f.tree_nav
        n_raiz = tree.AppendItem(root, _("W Social"),
                                 data={"type": "category_root", "service": "wsocial"})
        cuentas = db.query("SELECT id, name FROM accounts WHERE service_id = 'wsocial' "
                           "AND active = 1 ORDER BY position, id")
        for r_id, r_name in cuentas:
            n_acc = tree.AppendItem(n_raiz, r_name,
                                    data={"type": "account", "account_id": r_id, "service": "wsocial"})
            # Orden de buffers del usuario (reordenable con Ctrl+flechas), completando los que
            # falten por si la aplicación ha añadido alguno nuevo.
            buffer_order = list(config.get("wsocial", "buffer_order", list(self._TODOS)))
            faltan = [b for b in self._TODOS if b not in buffer_order]
            if faltan:
                buffer_order.extend(faltan)
                config.set("wsocial", "buffer_order", buffer_order)
            ocultos = config.get("wsocial", "hidden_buffers", [])
            for b in buffer_order:
                if b in ocultos:
                    continue
                etiqueta = self.BUFFER_LABELS.get(b)
                if not etiqueta:
                    continue
                tree.AppendItem(n_acc, etiqueta, data={
                    "type": "wsocial_timeline", "ws_type": b, "account_id": r_id,
                    "service": "wsocial", "base_label": etiqueta})

            # Feeds guardados, listas y búsquedas: subnodos navegables propios de esta cuenta.
            for feed in config.get("wsocial", "saved_feeds", []):
                nombre = _("Feed: {0}").format(feed.get("name", ""))
                tree.AppendItem(n_acc, nombre, data={
                    "type": "wsocial_timeline", "ws_type": "feed:" + feed.get("uri", ""),
                    "account_id": r_id, "service": "wsocial", "base_label": nombre})
            for lst in config.get("wsocial", "lists_cache", []):
                nombre = _("Lista: {0}").format(lst.get("name", ""))
                tree.AppendItem(n_acc, nombre, data={
                    "type": "wsocial_timeline", "ws_type": "list:" + lst.get("uri", ""),
                    "account_id": r_id, "service": "wsocial", "base_label": nombre})
            if "searches" not in ocultos:
                for termino in config.get("wsocial", "saved_searches", []):
                    nombre = _("Búsqueda: {0}").format(termino)
                    tree.AppendItem(n_acc, nombre, data={
                        "type": "wsocial_timeline", "ws_type": "search:" + termino,
                        "account_id": r_id, "service": "wsocial", "base_label": nombre})
            # Cronologías de cuentas y conversaciones abiertas como buffer.
            for u in config.get("wsocial", "user_timelines", []):
                if u.get("account_id") != r_id:
                    continue
                nombre = _("Cronología de {0}").format(u.get("handle", ""))
                tree.AppendItem(n_acc, nombre, data={
                    "type": "wsocial_timeline", "ws_type": "author:" + u.get("did", ""),
                    "account_id": r_id, "service": "wsocial", "base_label": nombre})
            for h in config.get("wsocial", "thread_buffers", []):
                if h.get("account_id") != r_id:
                    continue
                nombre = _("Conversación: {0}").format(h.get("titulo", ""))
                tree.AppendItem(n_acc, nombre, data={
                    "type": "wsocial_timeline", "ws_type": "thread:" + h.get("uri", ""),
                    "account_id": r_id, "service": "wsocial", "base_label": nombre})

    def reordenar_buffer(self, data, subir):
        """Sube o baja un buffer en el árbol y guarda el nuevo orden."""
        tipo = data.get("ws_type", "")
        orden = list(config.get("wsocial", "buffer_order", list(self._TODOS)))
        if tipo not in orden:
            return False
        i = orden.index(tipo)
        j = i - 1 if subir else i + 1
        if j < 0 or j >= len(orden):
            return False
        orden[i], orden[j] = orden[j], orden[i]
        config.set("wsocial", "buffer_order", orden)
        return True

    def on_select(self, item, data, t):
        """El usuario ha elegido un nodo del árbol."""
        f = self.f
        f.active_wsocial_account_id = data.get("account_id")
        f._aplicar_visibilidad_detalles()
        if t == "wsocial_timeline":
            f.active_timeline_type = data.get("ws_type")
            f.lbl_buffer_titulo.SetLabel("W Social - " + f.tree_nav.GetItemText(item))
            self.cargar_listado()
        else:
            f.lista_items.DeleteAllItems()
            f.lista_items.limpiar_lecturas()
            f.txt_detalles.Clear()

    # ------------------------------------------------------------------
    # DESPACHO DE BUFFERS
    # ------------------------------------------------------------------
    def _es_buffer_posts(self, tipo: str) -> bool:
        return (tipo in ("home", "mentions", "sent", "likes", "bookmarks")
                or tipo.startswith(("feed:", "list:", "search:", "author:", "thread:", "quotes:")))

    def _es_autodescargable(self, tipo: str) -> bool:
        return (tipo in ("notifications", "trending", "starterpacks", "convos", "requests", "drafts")
                or tipo in self.BUFFERS_ACTORES or tipo in self.BUFFERS_GESTOR
                or self._es_buffer_posts(tipo))

    def cargar_listado(self):
        """Pinta el buffer activo desde la caché y, si está vacío, lo descarga en silencio."""
        f = self.f
        if not f.active_wsocial_account_id:
            return
        tipo = f.active_timeline_type
        if tipo == "notifications":
            self._cargar_notificaciones()
        elif tipo in self.BUFFERS_ACTORES:
            self._cargar_actores(tipo)
        elif tipo == "trending":
            self._cargar_tendencias()
        elif tipo == "starterpacks":
            self._cargar_starterpacks()
        elif tipo in ("convos", "requests"):
            self._cargar_convos(solicitudes=(tipo == "requests"))
        elif tipo == "drafts":
            self._cargar_borradores()
        elif tipo in self.BUFFERS_GESTOR:
            self._cargar_gestor(tipo)
        elif self._es_buffer_posts(tipo):
            self._cargar_posts(tipo)
        else:
            self._preparar_columnas_posts()
            f._modo_lista = "wsocial_posts"
            return
        if f.lista_items.GetItemCount() == 0 and self._es_autodescargable(tipo):
            self.on_refresh(silencioso=True)

    def _preparar_columnas_posts(self):
        """Deja la lista lista para publicaciones, con las columnas de la plantilla del usuario."""
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        self._cols_posts = f._construir_columnas_plantilla("wsocial_post")

    def _preparar_columnas(self, columnas):
        """Prepara la lista con columnas fijas (para buffers que no son publicaciones)."""
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        for i, (titulo, ancho) in enumerate(columnas):
            f.lista_items.InsertColumn(i, titulo, width=ancho)
        f._cols_actuales = [(t, t) for t, _a in columnas]

    # ------------------------------------------------------------------
    # PUBLICACIONES
    # ------------------------------------------------------------------
    @staticmethod
    def _indicadores_post(liked_uri, reposted_uri, bookmarked, reply_parent_uri, quote_uri,
                          verificado):
        """Indicadores accesibles del estado de una publicación."""
        ind = []
        if liked_uri:
            ind.append(_("Me gusta"))
        if reposted_uri:
            ind.append(_("Republicada"))
        if bookmarked:
            ind.append(_("Guardada"))
        if reply_parent_uri:
            ind.append(_("Respuesta"))
        if quote_uri:
            ind.append(_("Cita"))
        if verificado:
            ind.append(_("Cuenta verificada"))
        return " ".join(ind)

    def _valores_post(self, r):
        """Valores por campo de una publicación, para volcarlos con la plantilla wsocial_post."""
        from app.ui.main_frame import formatear_tiempo_relativo, evento_media_de_attachments
        import json as _json
        (db_id, autor, handle, contenido, fecha, likes, reposts, replies, labels, repost_by,
         media_json, quotes, langs, liked_uri, reposted_uri, bookmarked, reply_parent_uri,
         quote_uri, verificado) = tuple(r)[:19]
        extra = tuple(r)[19:]
        repost_name = (extra[0] if len(extra) > 0 else "") or repost_by
        reply_handle = extra[1] if len(extra) > 1 else ""
        reply_name = extra[2] if len(extra) > 2 else ""
        con_usuario = bool(config.get("accessibility", "show_screen_names", False))
        autor_col = autor or handle
        if con_usuario and handle:
            autor_col = f"{autor} (@{handle})"
        cuerpo = (contenido or "").replace("\n", " ").strip()
        # Republicación y respuesta según las preferencias de W Social (por defecto, como siempre).
        from app.core.at_lectura import aplicar_repost_y_respuesta
        base = aplicar_repost_y_respuesta(
            {"author": autor_col or "", "username": f"@{handle}" if handle else "",
             "content": cuerpo},
            red="wsocial", autor=autor, handle=handle, repost_handle=repost_by or "",
            repost_name=repost_name if repost_by else "", reply_handle=reply_handle,
            reply_name=reply_name,
            formato_repost=config.get("wsocial", "formato_repost", "original"),
            prefijo_respuesta=bool(config.get("wsocial", "prefijo_respuesta", False)),
            plantilla=config.get("accessibility", "wsocial_post", "") or "",
            con_usuario=con_usuario,
            mi_handle=self._mi_handle_at() if reply_handle else "")
        cuerpo = base["content"]

        # Descripción accesible de los adjuntos: qué son y su texto alternativo.
        partes_media = []
        if media_json:
            try:
                adjuntos = _json.loads(media_json)
            except Exception:
                adjuntos = []
            for a in (adjuntos or []):
                tipo = (a.get("type") or "").lower()
                alt = (a.get("alt") or "").strip()
                etq = _("Vídeo") if "video" in tipo else _("Imagen")
                etq += (": " + alt) if alt else (" " + _("(sin descripción)"))
                partes_media.append(etq)
        if quote_uri:
            partes_media.append(_("Cita"))
        media_desc = ". ".join(partes_media)

        # Moderación por etiquetas: se decide ANTES de construir el texto que leerá el lector.
        etiquetas = [e for e in (labels or "").split(",") if e]
        decision = self._decidir_moderacion(etiquetas)
        if decision["ocultar"]:
            cuerpo = _("[Contenido oculto: {0}]").format(decision["motivo"])
            media_desc = ""
        else:
            # Palabras silenciadas (como en Bluesky): se difumina con el aviso de la palabra.
            silenciada = self._palabra_silenciada(contenido, media_json, langs, handle)
            if silenciada:
                cuerpo = _("[Silenciado por la palabra: {0}]").format(silenciada)
                media_desc = ""
            elif decision["avisar"]:
                cuerpo = _("[Aviso: {0}] ").format(decision["motivo"]) + cuerpo

        valores = {
            "author": base["author"],
            "username": base["username"],
            "content": cuerpo,
            "reply_to": base["reply_to"],
            "date": formatear_tiempo_relativo(fecha) if fecha else "",
            "indicators": self._indicadores_post(liked_uri, reposted_uri, bookmarked,
                                                 reply_parent_uri, quote_uri, verificado),
            "media": media_desc,
            "cw": ", ".join(e for e in etiquetas if not e.startswith("!")),
            "counts": _("{0} me gusta, {1} republicaciones, {2} respuestas, {3} citas").format(
                likes or 0, reposts or 0, replies or 0, quotes or 0),
            "language": langs or "",
            "_mod_filtrar": decision["filtrar"],
        }
        texto_filtro = f"{autor or ''} {handle or ''} {repost_by or ''}"
        return (db_id, valores, fecha, evento_media_de_attachments(media_json), texto_filtro,
                (contenido or ""))

    def _mi_handle_at(self):
        """Handle de la cuenta activa (sin red), para leer "En respuesta a ti"."""
        from app.core.at_lectura import mi_handle
        return mi_handle(wsocial_service, self.f.active_wsocial_account_id)

    def _palabra_silenciada(self, contenido, media_json, langs, handle):
        """La palabra silenciada (mutedWordsPref) que casa con la publicación, o "". Mismo motor
        que Bluesky (bluesky_mutewords): texto, #etiquetas, texto alternativo de la multimedia,
        ámbitos, caducidad y "excepto las cuentas que sigo"."""
        acc = self.f.active_wsocial_account_id
        palabras = (config.get("wsocial", "palabras_silenciadas", {}) or {}).get(str(acc)) or []
        if not palabras or not (contenido or media_json):
            return ""
        import json as _json
        import re as _re
        from app.services.bluesky.bluesky_mutewords import coincidencias, valores_coincidentes
        texto = contenido or ""
        tags = _re.findall(r"(?:^|\s)#([^\s#.,;:!?()\[\]{}\"']+)", texto)
        idiomas = [l for l in (langs or "").split(",") if l]
        sigo = False
        if handle and any(w.get("actorTarget") == "exclude-following" for w in palabras):
            sigo = bool(db.query_one(
                "SELECT 1 FROM wsocial_actors WHERE account_id = ? AND bucket = 'follows' "
                "AND handle = ? LIMIT 1", (acc, handle)))
        # El texto (con sus etiquetas) y después el texto alternativo de cada adjunto.
        fuentes = [(texto, tags)]
        try:
            fuentes += [((a.get("alt") or ""), None)
                        for a in (_json.loads(media_json) if media_json else [])]
        except Exception:
            pass
        for fuente, etiquetas in fuentes:
            if not fuente and not etiquetas:
                continue
            res = coincidencias(palabras, fuente, tags=etiquetas, idiomas=idiomas,
                                sigue_al_autor=sigo)
            if res:
                return ", ".join(valores_coincidentes(res))
        return ""

    def _decidir_moderacion(self, etiquetas):
        """Qué hacer con una publicación según sus etiquetas y las preferencias del usuario.

        Las preferencias vienen del servidor (qué hacer con cada etiqueta) y se cachean en la
        configuración para no consultar la red al pintar cada fila."""
        prefs = config.get("wsocial", "prefs_etiquetas", {}) or {}
        decision = {"avisar": False, "ocultar": False, "filtrar": False, "motivo": ""}
        for etiqueta in etiquetas:
            if not etiqueta:
                continue
            if etiqueta == "!hide":
                decision.update({"filtrar": True, "motivo": etiqueta})
                return decision
            if etiqueta == "!warn":
                decision.update({"avisar": True, "motivo": _("contenido marcado")})
                continue
            que_hacer = prefs.get(etiqueta, "warn")
            if que_hacer == "hide":
                decision.update({"ocultar": True, "motivo": etiqueta})
            elif que_hacer == "warn":
                decision.update({"avisar": True, "motivo": etiqueta})
        return decision

    def _filas_posts_actual(self, tipo):
        """Filas que DEBERÍA tener el buffer, para la actualización silenciosa (sin mover foco)."""
        f = self.f
        filas = []
        if not f.active_wsocial_account_id:
            return filas
        from app.core.content_filters import ContentFilterEngine
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        filtros = ContentFilterEngine.cargar("wsocial")
        plantilla = (config.get("accessibility", "wsocial_post", "")
                     or definicion("wsocial_post")["default"])
        cols = getattr(f, "_cols_actuales", None) or getattr(self, "_cols_posts", [])
        rows = db.query(f"""
            SELECT {self._SQL_COLS_POST}
            FROM wsocial_posts p JOIN wsocial_timeline_posts t ON p.id = t.post_db_id
            WHERE t.account_id = ? AND t.timeline = ?
            ORDER BY t.position ASC LIMIT ?
        """, (f.active_wsocial_account_id, tipo, limite_render("max_items_buffer")))
        if not config.get("general", "newest_first", True):
            rows = list(rows)[::-1]
        for r in rows:
            db_id, valores, fecha, media_ev, txt_filtro, contenido = self._valores_post(r)
            if valores.get("_mod_filtrar"):
                continue
            if filtros and ContentFilterEngine.evaluar("wsocial", contenido, txt_filtro,
                                                       filtros)["hide"]:
                continue
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura, media_ev, fecha))
        return filas

    def _cargar_posts(self, tipo):
        """Vuelca las publicaciones cacheadas de un buffer en la lista principal."""
        f = self.f
        f.lista_items.congelar_diferido()
        self._preparar_columnas_posts()
        f._modo_lista = "wsocial_posts"
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        cols = self._cols_posts
        plantilla = (config.get("accessibility", "wsocial_post", "")
                     or definicion("wsocial_post")["default"])
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)
        idx = 0
        for db_id, cols_vals, lectura, media_ev, fecha in self._filas_posts_actual(tipo):
            pos = f.lista_items.InsertItem(idx, cols_vals[0] if cols_vals else "")
            for j, valor in enumerate(cols_vals[1:], start=1):
                f.lista_items.SetItem(pos, j, valor)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, lectura)
            f.lista_items.establecer_medios(pos, media_ev)
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, cols_vals[col_fecha])
            idx += 1
        f._restaurar_foco_lista()

    # ------------------------------------------------------------------
    # NOTIFICACIONES
    # ------------------------------------------------------------------
    def _filas_notifs_actual(self):
        """Filas de notificaciones (para pintar y para la actualización silenciosa)."""
        f = self.f
        filas = []
        if not f.active_wsocial_account_id:
            return filas
        from app.ui.main_frame import formatear_tiempo_relativo
        from app.services.wsocial.wsocial_common import MOTIVOS_NOTIFICACION
        rows = db.query("""
            SELECT id, reason, author_name, author_handle, indexed_at, texto, is_read
            FROM wsocial_notifications WHERE account_id = ?
            ORDER BY indexed_at DESC LIMIT ?
        """, (f.active_wsocial_account_id, limite_render("max_notifications")))
        if not config.get("general", "newest_first", True):
            rows = list(rows)[::-1]
        for db_id, reason, autor, handle, fecha, texto, leida in rows:
            tipo = MOTIVOS_NOTIFICACION.get(reason, reason)
            fecha_rel = formatear_tiempo_relativo(fecha) if fecha else ""
            resumen = (texto or "").replace("\n", " ").strip()
            lectura = f"{tipo}. {autor or handle}. {resumen}. {fecha_rel}"
            if not leida:
                lectura = _("Sin leer. ") + lectura
            filas.append((db_id, [tipo, autor or "", f"@{handle}" if handle else "",
                                  resumen, fecha_rel], lectura))
        return filas

    def _cargar_notificaciones(self):
        """Lista de notificaciones."""
        f = self.f
        f._modo_lista = "wsocial_notifs"
        self._preparar_columnas([(_("Tipo"), 150), (_("Cuenta"), 180), (_("Usuario"), 160),
                                 (_("Contenido"), 380), (_("Fecha"), 120)])
        for i, (db_id, cols, lectura) in enumerate(self._filas_notifs_actual()):
            pos = f.lista_items.InsertItem(i, cols[0])
            for j, valor in enumerate(cols[1:], start=1):
                f.lista_items.SetItem(pos, j, valor)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, lectura)
        f._restaurar_foco_lista()

    # ------------------------------------------------------------------
    # CUENTAS
    # ------------------------------------------------------------------
    def _cargar_actores(self, bucket):
        """Lista de cuentas (seguidores, seguidos, bloqueados o silenciados)."""
        f = self.f
        f._modo_lista = "wsocial_actores"
        self._preparar_columnas([(_("Nombre"), 220), (_("Usuario"), 220),
                                 (_("Descripción"), 400), (_("Estado"), 160)])
        rows = db.query("""
            SELECT id, display_name, handle, description, following_uri, blocking_uri, muted,
                   verificado, did
            FROM wsocial_actors WHERE account_id = ? AND bucket = ? ORDER BY position ASC
        """, (f.active_wsocial_account_id, bucket))
        for i, (db_id, nombre, handle, desc, siguiendo, bloqueado, silenciado, verif, did) in enumerate(rows):
            estado = []
            if siguiendo:
                estado.append(_("La sigues"))
            if bloqueado:
                estado.append(_("Bloqueada"))
            if silenciado:
                estado.append(_("Silenciada"))
            if verif:
                estado.append(_("Verificada"))
            estado_txt = ", ".join(estado)
            pos = f.lista_items.InsertItem(i, nombre or handle or "")
            f.lista_items.SetItem(pos, 1, f"@{handle}" if handle else "")
            f.lista_items.SetItem(pos, 2, (desc or "")[:300])
            f.lista_items.SetItem(pos, 3, estado_txt)
            f.lista_items_db_ids.append(f"actor:{did}")
            f.lista_items.establecer_lectura(
                pos, f"{nombre or handle}. @{handle}. {desc or ''}. {estado_txt}")
        f._restaurar_foco_lista()

    # ------------------------------------------------------------------
    # CONVERSACIONES Y GRUPOS
    # ------------------------------------------------------------------
    def _filas_convos_actual(self, solicitudes=False):
        f = self.f
        filas = []
        if not f.active_wsocial_account_id:
            return filas
        from app.ui.main_frame import formatear_tiempo_relativo
        rows = db.query("""
            SELECT convo_id, title, last_message, last_message_date, unread_count, muted,
                   is_group, locked
            FROM wsocial_convos WHERE account_id = ? AND COALESCE(is_request,0) = ?
            ORDER BY last_message_date DESC
        """, (f.active_wsocial_account_id, 1 if solicitudes else 0))
        for convo_id, titulo, ultimo, fecha, sin_leer, silenciada, grupo, cerrada in rows:
            nombre = titulo or _("(conversación)")
            marcas = []
            if grupo:
                marcas.append(_("Grupo"))
            if silenciada:
                marcas.append(_("Silenciada"))
            if cerrada:
                marcas.append(_("Cerrada"))
            ultimo_txt = (ultimo or "").replace("\n", " ").strip()
            fecha_rel = formatear_tiempo_relativo(fecha) if fecha else ""
            lectura = nombre
            if marcas:
                lectura += ". " + ", ".join(marcas)
            if sin_leer:
                lectura += _(". {0} sin leer").format(sin_leer)
            lectura += f". {ultimo_txt}. {fecha_rel}."
            filas.append((f"convo:{convo_id}",
                          [nombre, ", ".join(marcas), ultimo_txt, fecha_rel,
                           str(sin_leer) if sin_leer else ""], lectura))
        return filas

    def _cargar_convos(self, solicitudes=False):
        """Lista de conversaciones (o de solicitudes de mensaje)."""
        f = self.f
        f._modo_lista = "wsocial_convos"
        self._preparar_columnas([(_("Conversación"), 260), (_("Tipo"), 140),
                                 (_("Último mensaje"), 360), (_("Fecha"), 120),
                                 (_("Sin leer"), 90)])
        for i, (db_id, cols, lectura) in enumerate(self._filas_convos_actual(solicitudes)):
            pos = f.lista_items.InsertItem(i, cols[0])
            for j, valor in enumerate(cols[1:], start=1):
                f.lista_items.SetItem(pos, j, valor)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, lectura)
        f._restaurar_foco_lista()

    # ------------------------------------------------------------------
    # OTROS BUFFERS
    # ------------------------------------------------------------------
    def _cargar_tendencias(self):
        """Temas del momento (Intro abre uno como búsqueda)."""
        f = self.f
        f._modo_lista = "wsocial_trending"
        self._preparar_columnas([(_("Tema"), 300), (_("Publicaciones"), 140),
                                 (_("Categoría"), 200)])
        for i, t in enumerate(getattr(self, "_tendencias", []) or []):
            pos = f.lista_items.InsertItem(i, t.get("nombre", ""))
            f.lista_items.SetItem(pos, 1, str(t.get("publicaciones", "") or ""))
            f.lista_items.SetItem(pos, 2, t.get("categoria", ""))
            f.lista_items_db_ids.append("trend:" + t.get("nombre", ""))
            f.lista_items.establecer_lectura(
                pos, _("{0}. {1} publicaciones. Intro para abrirlo como búsqueda.").format(
                    t.get("nombre", ""), t.get("publicaciones", 0)))
        f._restaurar_foco_lista()

    def _cargar_starterpacks(self):
        """Paquetes de inicio guardados."""
        f = self.f
        f._modo_lista = "wsocial_starterpacks"
        self._preparar_columnas([(_("Nombre"), 280), (_("Creador"), 200),
                                 (_("Descripción"), 400)])
        rows = db.query("SELECT uri, name, description, creator_handle FROM wsocial_feeds "
                        "WHERE account_id = ? AND kind = 'starterpack' ORDER BY position",
                        (f.active_wsocial_account_id,))
        for i, (uri, nombre, desc, creador) in enumerate(rows):
            pos = f.lista_items.InsertItem(i, nombre or "")
            f.lista_items.SetItem(pos, 1, f"@{creador}" if creador else "")
            f.lista_items.SetItem(pos, 2, desc or "")
            f.lista_items_db_ids.append("starterpack:" + uri)
            f.lista_items.establecer_lectura(pos, f"{nombre}. @{creador}. {desc or ''}")
        f._restaurar_foco_lista()

    def _cargar_gestor(self, tipo):
        """Feeds o listas guardados: se muestran en la lista y se abren con Intro."""
        f = self.f
        f._modo_lista = "wsocial_gestor"
        self._preparar_columnas([(_("Nombre"), 280), (_("Descripción"), 400),
                                 (_("Creador"), 200)])
        kind = "feed" if tipo == "feeds" else "list"
        rows = db.query("SELECT uri, name, description, creator_handle FROM wsocial_feeds "
                        "WHERE account_id = ? AND kind = ? ORDER BY position",
                        (f.active_wsocial_account_id, kind))
        for i, (uri, nombre, desc, creador) in enumerate(rows):
            pos = f.lista_items.InsertItem(i, nombre or uri)
            f.lista_items.SetItem(pos, 1, desc or "")
            f.lista_items.SetItem(pos, 2, f"@{creador}" if creador else "")
            f.lista_items_db_ids.append(f"{kind}:{uri}")
            f.lista_items.establecer_lectura(
                pos, _("{0}. {1}. Intro para abrirlo.").format(nombre or uri, desc or ""))
        f._restaurar_foco_lista()

    def _cargar_borradores(self):
        """Borradores guardados en el servidor."""
        f = self.f
        f._modo_lista = "wsocial_drafts"
        self._preparar_columnas([(_("Borrador"), 500), (_("Actualizado"), 160)])
        from app.ui.main_frame import formatear_tiempo_relativo
        rows = db.query("SELECT draft_id, texto, updated_at FROM wsocial_drafts "
                        "WHERE account_id = ? ORDER BY updated_at DESC",
                        (f.active_wsocial_account_id,))
        for i, (draft_id, texto, actualizado) in enumerate(rows):
            resumen = (texto or "").replace("\n", " ").strip()
            fecha = formatear_tiempo_relativo(actualizado) if actualizado else ""
            pos = f.lista_items.InsertItem(i, resumen)
            f.lista_items.SetItem(pos, 1, fecha)
            f.lista_items_db_ids.append("draft:" + draft_id)
            f.lista_items.establecer_lectura(
                pos, _("Borrador: {0}. {1}. Intro para seguir escribiéndolo.").format(resumen, fecha))
        f._restaurar_foco_lista()

    # ------------------------------------------------------------------
    # UTILIDADES
    # ------------------------------------------------------------------
    def _re_seleccionar(self, ws_type):
        """Vuelve a seleccionar en el árbol el buffer indicado (tras reconstruirlo)."""
        f = self.f
        raiz = f.tree_nav.GetRootItem()

        def _buscar(nodo):
            hijo, cookie = f.tree_nav.GetFirstChild(nodo)
            while hijo.IsOk():
                data = f.tree_nav.GetItemData(hijo) or {}
                if (data.get("service") == "wsocial" and data.get("ws_type") == ws_type
                        and data.get("account_id") == f.active_wsocial_account_id):
                    return hijo
                encontrado = _buscar(hijo)
                if encontrado:
                    return encontrado
                hijo, cookie = f.tree_nav.GetNextChild(nodo, cookie)
            return None

        nodo = _buscar(raiz)
        if nodo:
            f.tree_nav.SelectItem(nodo)
