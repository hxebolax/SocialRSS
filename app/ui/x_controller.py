# -*- coding: utf-8 -*-
"""Controlador de X (Twitter) enganchado al MainFrame.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext para textos en import-time
import wx

from app.core.database import db
from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.services.x import x_service
from .accessible_controls import (mostrar_info, confirmar, CustomTextEntryDialog)
from app.core.task_manager import task_manager
from .x_dialogs import XComposeDialog
from .x_hilos import _XHilosMixin
from .x_gestion_ui import _XGestionUIMixin
from .x_cronologias import _XCronologiasMixin


class XController(_XHilosMixin, _XGestionUIMixin, _XCronologiasMixin):
    """Controlador de X integrado en la ventana principal (como BlueskyController)."""

    # Buffers principales (reordenables) y sus etiquetas.
    _TODOS = ("home", "following", "mentions", "notifications", "bookmarks",
              "followers", "verified_followers", "friends", "lists", "communities", "scheduled")
    BUFFER_LABELS = {
        "home": _("Inicio (Para ti)"), "following": _("Siguiendo"),
        "mentions": _("Menciones"), "notifications": _("Notificaciones"),
        "bookmarks": _("Marcadores"), "followers": _("Seguidores"),
        "verified_followers": _("Seguidores verificados"),
        "friends": _("Cuentas que sigo"), "lists": _("Listas"),
        "communities": _("Comunidades"), "scheduled": _("Programados"),
    }
    # Buffers cuyo contenido son CUENTAS (no publicaciones).
    BUFFERS_ACTORES = ("followers", "verified_followers", "friends")
    # Buffers que se autoactualizan en silencio (cronológicos de publicaciones).
    BUFFERS_CRONOLOGICOS = ("home", "following", "mentions", "communities")

    def _es_cronologico(self, tipo) -> bool:
        """True si el buffer es una cronología de publicaciones que se descarga con
        obtener_timeline_async: los fijos + búsquedas, listas y cronologías de usuario."""
        return (tipo in self.BUFFERS_CRONOLOGICOS
                or str(tipo).startswith(("search:", "list:", "user:", "thread:", "community:")))

    def __init__(self, frame):
        self.f = frame

    # ---------------- Árbol ----------------
    def construir_servicio(self, root):
        tree = self.f.tree_nav
        n_x = tree.AppendItem(root, _("X (Twitter)"),
                              data={"type": "category_root", "service": "x"})
        accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'x' AND active = 1 ORDER BY position, id")
        for r_id, r_name in accs:
            n_acc = tree.AppendItem(n_x, r_name,
                                    data={"type": "account", "account_id": r_id, "service": "x"})
            buffer_order = list(config.get("x", "buffer_order", list(self._TODOS)))
            faltan = [b for b in self._TODOS if b not in buffer_order]
            if faltan:
                buffer_order.extend(faltan)
                config.set("x", "buffer_order", buffer_order)
            ocultos = config.get("x", "hidden_buffers", [])
            for b in buffer_order:
                if b in ocultos:
                    continue
                label = self.BUFFER_LABELS.get(b)
                if not label:
                    continue
                tree.AppendItem(n_acc, label, data={
                    "type": "x_timeline", "x_type": b,
                    "account_id": r_id, "service": "x", "base_label": label})
            # Listas (subnodos navegables: cada una es un buffer con sus tweets). Se
            # muestran solo si el buffer "Listas" NO está oculto: así, al ocultar Listas,
            # desaparecen también sus sublistas.
            if "lists" not in ocultos:
                for fila in db.query("SELECT list_id, name FROM x_lists WHERE account_id = ? ORDER BY position", (r_id,)):
                    lid, lnombre = fila
                    tree.AppendItem(n_acc, _("Lista: {0}").format(lnombre or lid), data={
                        "type": "x_timeline", "x_type": "list:" + lid,
                        "account_id": r_id, "service": "x",
                        "base_label": _("Lista: {0}").format(lnombre or lid)})
            # Búsquedas guardadas (se ocultan en bloque desde "Mostrar u ocultar buffers"
            # desmarcando "Búsquedas guardadas"; para quitar una concreta, su menú contextual).
            if "searches" not in ocultos:
                for fila in db.query("SELECT name, query FROM x_searches WHERE account_id = ? ORDER BY position", (r_id,)):
                    nombre, q = fila
                    tree.AppendItem(n_acc, _("Búsqueda: {0}").format(nombre or q), data={
                        "type": "x_timeline", "x_type": "search:" + q,
                        "account_id": r_id, "service": "x",
                        "base_label": _("Búsqueda: {0}").format(nombre or q)})
            # Cronologías de usuario abiertas como buffer (las publicaciones de un @usuario). Se
            # ocultan en bloque con "Cronologías de usuarios"; para quitar una, su menú contextual.
            if "user_timelines" in ocultos:
                _cronos_x = []
            else:
                _cronos_x = db.query("SELECT user_id, screen_name, name, COALESCE(modo,'tweets') "
                                     "FROM x_user_timelines WHERE account_id = ? ORDER BY position", (r_id,))
            for fila in _cronos_x:
                uid_tl, screen_tl, nombre_tl, modo_tl = fila
                x_type = "user:" + str(uid_tl) + (("" if modo_tl == "tweets" else ":" + modo_tl))
                suf = {"replies": _(" (con respuestas)"), "media": _(" (solo multimedia)"),
                       "likes": _(" (sus me gusta)")}.get(modo_tl, "")
                etiqueta = _("Cronología: @{0}{1}").format(screen_tl or nombre_tl or uid_tl, suf)
                tree.AppendItem(n_acc, etiqueta, data={
                    "type": "x_timeline", "x_type": x_type,
                    "account_id": r_id, "service": "x", "base_label": etiqueta})
            # Conversaciones y comunidades abiertas como buffer (x_hilos.py).
            self._nodos_dinamicos_extra(tree, n_acc, r_id, ocultos)
        return n_x

    def reordenar_buffer(self, data, subir):
        b_type = data.get("x_type")
        orden = config.get("x", "buffer_order", list(self._TODOS))
        if b_type not in orden:
            return False
        idx = orden.index(b_type)
        new_idx = idx - 1 if subir else idx + 1
        if not (0 <= new_idx < len(orden)):
            return False
        orden[idx], orden[new_idx] = orden[new_idx], orden[idx]
        config.set("x", "buffer_order", orden)  # set() ya guarda (autosave)
        return True

    # ---------------- Selección ----------------
    def on_select(self, item, data, t):
        f = self.f
        f.active_x_account_id = data.get("account_id")
        # El panel de detalles es opcional (Preferencias -> X): se muestra u oculta según la
        # preferencia de la red activa, centralizado en la ventana principal.
        f._aplicar_visibilidad_detalles()
        if t == "x_timeline":
            f.active_timeline_type = data.get("x_type")
            f.lbl_buffer_titulo.SetLabel("X - " + f.tree_nav.GetItemText(item))
            self.cargar_listado()
        else:
            f.lista_items.DeleteAllItems()
            f.lista_items.limpiar_lecturas()
            f.txt_detalles.Clear()

    def on_item_selected(self, index):
        """Vuelca al panel de detalles (si está visible) el contenido del tweet enfocado. Si el
        panel está oculto (opción desactivada), no hace nada: la navegación es más fluida."""
        f = self.f
        if not f.txt_detalles.IsShown():
            return
        if index < 0 or index >= len(f.lista_items_db_ids):
            return
        db_id = f.lista_items_db_ids[index]
        row = db.query_one(
            "SELECT author_name, author_screen_name, text, created_at, "
            "favorite_count, retweet_count, reply_count FROM x_tweets WHERE id = ?", (db_id,))
        if not row:
            return
        nombre, screen, texto, fecha, likes, rts, replies = row
        from app.ui.main_frame import formatear_tiempo_relativo
        txt = f"{nombre or ''} (@{screen or ''})\n"
        if fecha:
            txt += _("Fecha: {0}\n").format(formatear_tiempo_relativo(str(fecha)))
        txt += _("Interacciones: {0} me gusta, {1} RT, {2} respuestas\n").format(
            likes or 0, rts or 0, replies or 0)
        txt += "\n" + (texto or "")
        f.txt_detalles.SetValue(txt)
        f.txt_detalles.SetInsertionPoint(0)

    def botones_botonera(self, db_id):
        """Botones directos para la botonera bajo la lista (solo en publicaciones; en otros buffers
        devuelve [] y se usa el botón "Acciones..."). Las etiquetas reflejan el estado."""
        row = db.query_one(
            "SELECT tweet_id, liked, retweeted FROM x_tweets WHERE id = ?", (db_id,))
        if not row:
            return []
        tid, liked, rt = row
        return [
            (_("Responder"), lambda d=db_id: self.responder(d)),
            (_("Quitar me gusta") if liked else _("Me gusta"),
             lambda t=tid, on=liked: self.accion(t, "unlike" if on else "like")),
            (_("Deshacer retweet") if rt else _("Retweet"),
             lambda t=tid, on=rt: self.accion(t, "unretweet" if on else "retweet")),
            (_("Conversación"), lambda t=tid: self.ver_hilo(t)),
        ]

    # ---------------- Carga ----------------
    def cargar_listado(self):
        """Al seleccionar un buffer: muestra AL INSTANTE lo que ya hay en caché (sin
        descargar ni decir "Cargando"). Lo nuevo va entrando solo, en silencio, por el
        sondeo periódico (igual que en Mastodon). Solo se descarga aquí si el buffer está
        vacío (nunca se había cargado), y entonces de forma silenciosa."""
        f = self.f
        if not f.active_x_account_id:
            return
        tipo = f.active_timeline_type

        # "Listas" es un buffer gestor: sus listas ya están como subnodos del árbol; al
        # entrar se refresca el catálogo en segundo plano, sin "Cargando".
        if tipo == "lists":
            self._refrescar_listas()
            return
        # "Programados" no tiene caché local: se consulta en silencio.
        if tipo == "scheduled":
            self._render_programados()
            return

        # 1. Pintar la caché ya existente, al instante.
        if tipo == "notifications":
            self._render_notificaciones()
        elif tipo in self.BUFFERS_ACTORES:
            self._render_actores(tipo)
        else:
            self._render_posts(tipo)

        # 2. Si el buffer está vacío (nunca descargado), una carga inicial SILENCIOSA.
        if f.lista_items.GetItemCount() == 0:
            self.actualizar_incremental(forzar=True)
        elif config.get("x", "auto_update", False):
            # Actualización AUTOMÁTICA activada: al entrar en un buffer con caché, ponerlo al día
            # en silencio (respetando min_update_seconds, así que no dispara si acabas de mirarlo).
            # En MANUAL (por defecto) no se hace: solo F5/Ctrl+Shift+R, para no arriesgar el bloqueo.
            self.actualizar_incremental()

    def _refrescar_listas(self):
        """Refresca el catálogo de listas en segundo plano (silencioso) y, si cambió,
        reconstruye el árbol para que aparezcan como subnodos."""
        acc = self.f.active_x_account_id

        def _cb(res, err):
            if err:
                if self._es_auth_err(err):
                    self._auto_reimportar_y_reintentar(self._refrescar_listas)
                return
            self.f._cargar_navegacion()
        x_service.obtener_listas_async(acc, callback=_cb)

    def _valores_post_x(self, r):
        """Valores POR CAMPO de un tweet para volcarlos con la plantilla x_post.
        Devuelve (db_id, valores, fecha_iso)."""
        from app.ui.main_frame import formatear_tiempo_relativo
        from .x_lectura import (describir_media_x, texto_cita_x, texto_encuesta_x,
                                indicadores_x)
        r = tuple(r)
        db_id, nombre, screen, texto, fecha, likes, rts, replies, rt_by = r[:9]
        # Columnas añadidas después (ver _SQL_COLS_POST_X); tolerante a filas cortas.
        (resp_a, cita_autor, cita_texto, encuesta, media_json, lang, liked, retuit,
         guardado, in_reply_to) = (list(r[9:19]) + [""] * 10)[:10]
        autor = nombre or screen or ""
        if config.get("accessibility", "show_screen_names", False) and screen:
            autor = f"{nombre} (@{screen})" if nombre else f"@{screen}"
        cuerpo = (texto or "").replace("\n", " ")
        # Lo que acompaña al texto: el tuit CITADO y la ENCUESTA (antes no se leían).
        extras = [x for x in (texto_cita_x(cita_autor, cita_texto), texto_encuesta_x(encuesta)) if x]
        if extras:
            cuerpo = ". ".join([cuerpo] + extras) if cuerpo else ". ".join(extras)
        media_desc = describir_media_x(media_json)
        if not cuerpo.strip() and media_desc:
            cuerpo = media_desc   # tuit de solo imagen o vídeo: que no quede vacío
        indic = indicadores_x(rt_by, liked, retuit, guardado, in_reply_to, cita_autor)
        # Si la plantilla NO tiene columna de indicadores, el RT se antepone al contenido
        # para no perderlo (igual que hace Bluesky con "[Reposteado por @x]").
        tiene_col_indic = self.f._col_de_campo(getattr(self.f, "_cols_actuales", []) or [], "indicators") is not None
        if rt_by and not tiene_col_indic:
            cuerpo = _("[RT de @{0}] ").format(rt_by) + cuerpo
        # A quién responde: campo {reply_to} y, si se pidió en Preferencias → X, delante del texto.
        from app.core.at_lectura import texto_respuesta_a
        yo = x_service._yo(self.f.active_x_account_id or 0).get("screen_name", "") if resp_a else ""
        respuesta = texto_respuesta_a(resp_a, "", yo)
        plantilla = config.get("accessibility", "x_post", "") or ""
        if respuesta and config.get("x", "prefijo_respuesta", False) and "{reply_to}" not in plantilla:
            cuerpo = _("{0}: ").format(respuesta) + cuerpo
        valores = {
            "author": autor,
            "username": f"@{screen}" if screen else "",
            "content": cuerpo,
            "reply_to": respuesta,
            "date": formatear_tiempo_relativo(fecha) if fecha else "",
            "indicators": indic,
            "media": media_desc,
            "cw": "",
            "counts": _("{0} me gusta, {1} RT, {2} resp.").format(likes or 0, rts or 0, replies or 0),
            "language": lang or "",
        }
        return db_id, valores, fecha

    # Columnas de x_tweets que lee la lista (en este orden; ver _valores_post_x).
    _SQL_COLS_POST_X = ("p.id, p.author_name, p.author_screen_name, p.text, p.created_at, "
                        "p.favorite_count, p.retweet_count, p.reply_count, p.retweeted_by, "
                        "COALESCE(p.in_reply_to_screen_name,''), COALESCE(p.quoted_author,''), "
                        "COALESCE(p.quoted_text,''), COALESCE(p.poll_json,''), "
                        "COALESCE(p.media_json,''), COALESCE(p.lang,''), p.liked, p.retweeted, "
                        "p.bookmarked, COALESCE(p.in_reply_to,'')")

    def _filas_posts_actual(self, tipo):
        """Estado deseado del buffer de publicaciones (db_id, [cols], lectura, None, fecha_iso),
        para render y para la inserción incremental silenciosa. Respeta la plantilla x_post."""
        f = self.f
        from app.core.plantillas import pulir_lectura, definicion
        from app.core.content_filters import ContentFilterEngine
        from collections import defaultdict
        filas = []
        plantilla = config.get("accessibility", "x_post", "") or definicion("x_post")["default"]
        cols = getattr(f, "_cols_actuales", None) or definicion("x_post")["default"]
        if isinstance(cols, str):
            from app.core.plantillas import columnas_de_plantilla
            cols = columnas_de_plantilla(cols)
        filtros = ContentFilterEngine.cargar("x")
        rows = db.query(f"""
            SELECT {self._SQL_COLS_POST_X}
            FROM x_tweets p JOIN x_timeline_tweets t ON p.id = t.tweet_db_id
            WHERE t.account_id = ? AND t.timeline = ? ORDER BY t.position
        """, (f.active_x_account_id, tipo))
        for r in rows:
            db_id, valores, fecha = self._valores_post_x(r)
            # Filtros de contenido: ocultar las publicaciones que casen (igual que Mastodon/Bluesky).
            if filtros:
                txt_filtro = f"{valores.get('author','')} {valores.get('username','')}"
                if ContentFilterEngine.evaluar("x", valores.get("content", ""), txt_filtro, filtros)["hide"]:
                    continue
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura, None, fecha))
        return filas

    def _render_posts(self, tipo):
        f = self.f
        f._modo_lista = "x_posts"
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        cols = f._construir_columnas_plantilla("x_post")
        col_fecha = f._col_de_campo(cols, "date")
        for db_id, cols_vals, lectura, _m, fecha in self._filas_posts_actual(tipo):
            pos = f._volcar_fila_plantilla(f.lista_items.GetItemCount(), cols, dict(zip([c for c, _e in cols], cols_vals)))
            f.lista_items.establecer_lectura(pos, lectura)
            if col_fecha is not None and fecha:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, cols_vals[col_fecha])
            f.lista_items_db_ids.append(db_id)
        f._restaurar_foco_lista()

    def _render_actores(self, bucket):
        f = self.f
        f._modo_lista = "x_actores"
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Nombre"), width=200)
        f.lista_items.InsertColumn(1, _("Usuario"), width=200)
        f.lista_items.InsertColumn(2, _("Biografía"), width=380)
        rows = db.query("""
            SELECT user_id, screen_name, name, description
            FROM x_users WHERE account_id = ? AND bucket = ? ORDER BY position
        """, (f.active_x_account_id, bucket))
        for user_id, screen, nombre, bio in rows:
            idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), nombre or screen or "")
            f.lista_items.SetItem(idx, 1, f"@{screen}" if screen else "")
            f.lista_items.SetItem(idx, 2, (bio or "").replace("\n", " "))
            f.lista_items.establecer_lectura(idx, f"{nombre or screen}. @{screen}. {bio or ''}")
            f.lista_items_db_ids.append(f"xuser:{user_id}:{screen}")
        f._restaurar_foco_lista()

    # Tipos de notificación de X a etiqueta legible.
    _TIPOS_NOTIF = {
        "like": _("Me gusta"), "favorite": _("Me gusta"), "retweet": _("Retweet"),
        "follow": _("Nuevo seguidor"), "mention": _("Mención"), "reply": _("Respuesta"),
        "quote": _("Cita"), "recommendation": _("Recomendación"),
    }

    def _filas_notifs_actual(self):
        f = self.f
        from app.ui.main_frame import formatear_tiempo_relativo
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        filas = []
        plantilla = config.get("accessibility", "x_notification", "") or definicion("x_notification")["default"]
        cols = getattr(f, "_cols_actuales", None) or definicion("x_notification")["default"]
        if isinstance(cols, str):
            from app.core.plantillas import columnas_de_plantilla
            cols = columnas_de_plantilla(cols)
        rows = db.query("""SELECT id, tipo, actor_name, actor_screen_name, created_at,
                                  COALESCE(text,'')
                           FROM x_notifications WHERE account_id = ? ORDER BY created_at DESC""",
                        (f.active_x_account_id,))
        for db_id, tipo, nombre, screen, fecha, texto in rows:
            valores = {
                "type": self._TIPOS_NOTIF.get(tipo, tipo or ""),
                "author": nombre or screen or "",
                "username": f"@{screen}" if screen else "",
                # El texto de la notificación ya se guardaba, pero no se mostraba.
                "content": " ".join((texto or "").split()),
                "date": formatear_tiempo_relativo(fecha) if fecha else "",
            }
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura))
        return filas

    def _render_notificaciones(self):
        f = self.f
        f._modo_lista = "x_notifs"
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        cols = f._construir_columnas_plantilla("x_notification")
        for db_id, cols_vals, lectura in self._filas_notifs_actual():
            pos = f._volcar_fila_plantilla(f.lista_items.GetItemCount(), cols,
                                           dict(zip([c for c, _e in cols], cols_vals)))
            f.lista_items.establecer_lectura(pos, lectura)
            f.lista_items_db_ids.append(db_id)
        f._restaurar_foco_lista()

    def _render_programados(self):
        f = self.f
        f._modo_lista = "x_scheduled"
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Fecha"), width=180)
        f.lista_items.InsertColumn(1, _("Texto"), width=500)
        speak(_("Cargando programados..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            for m in (res or []):
                idx = f.lista_items.InsertItem(f.lista_items.GetItemCount(), str(m.get("date", "")))
                f.lista_items.SetItem(idx, 1, (m.get("text", "") or "").replace("\n", " "))
                f.lista_items.establecer_lectura(idx, f"{m.get('date','')}. {m.get('text','')}")
                f.lista_items_db_ids.append(f"xsched:{m.get('id','')}")
            n = f.lista_items.GetItemCount()
            speak(_("{0} mensajes programados.").format(n) if n
                  else _("No hay mensajes programados."), interrupt=True)
            f._restaurar_foco_lista()
        x_service.obtener_programados_async(self._cuenta(), callback=_cb)

    def construir_menu_programados(self, menu, db_id):
        f = self.f
        try:
            sid = db_id.split(":", 1)[1]
        except (IndexError, AttributeError):
            return
        it = menu.Append(wx.ID_ANY, _("Cancelar mensaje programado"))
        f.Bind(wx.EVT_MENU, lambda e: self._cancelar_programado(sid), it)

    def _cancelar_programado(self, sid):
        if not confirmar(self.f, _("¿Cancelar este mensaje programado?"), _("Cancelar programado")):
            return
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Mensaje programado cancelado."))
                self.cargar_listado()
        x_service.cancelar_programado_async(self._cuenta(), sid, callback=_cb)

    def on_refresh(self):
        """F5: actualización explícita del buffer activo (sí descarga, aunque haya caché)."""
        f = self.f
        tipo = f.active_timeline_type
        if tipo == "lists":
            self._refrescar_listas()
            return
        if tipo == "scheduled":
            self._render_programados()
            return
        # Asegurar que se ve la caché y forzar una descarga silenciosa de lo nuevo.
        if f.lista_items.GetItemCount() == 0:
            if tipo == "notifications":
                self._render_notificaciones()
            elif tipo in self.BUFFERS_ACTORES:
                self._render_actores(tipo)
            else:
                self._render_posts(tipo)
        speak(_("Actualizando."), interrupt=True)
        self.actualizar_incremental(forzar=True, anunciar=True)

    def actualizar_incremental(self, forzar=False, anunciar=False):
        """Sondeo silencioso del buffer activo (como Mastodon/Bluesky): descarga en segundo
        plano y mete los elementos nuevos en su sitio SIN mover el foco ni hacer releer ni
        decir "Cargando". forzar=True salta el límite de tiempo (para la primera carga al
        entrar a un buffer vacío)."""
        f = self.f
        acc = f.active_x_account_id
        tipo = f.active_timeline_type
        if not acc:
            return
        # X limita las peticiones con dureza: el sondeo AUTOMÁTICO no se hace más a menudo
        # que x.min_update_seconds (por defecto 120 s), aunque el intervalo general sea
        # menor. F5 y la primera carga (forzar) actualizan siempre.
        import time as _t
        if not forzar:
            minimo = int(config.get("x", "min_update_seconds", 120))
            if _t.monotonic() - getattr(self, "_ultimo_auto", 0.0) < minimo:
                return
        self._ultimo_auto = _t.monotonic()
        tipo_inicio = tipo

        def _vigente():
            # Asegurar que el usuario sigue en el mismo buffer al volver del fondo.
            return f.active_x_account_id == acc and f.active_timeline_type == tipo_inicio

        if self._es_cronologico(tipo):
            def _cb(res, err):
                if err or not _vigente():
                    return
                nuevos = f._insertar_items_incremental(self._filas_posts_actual(tipo))
                if nuevos:
                    from app.core.event_bus import event_bus
                    event_bus.emit("sound_trigger", "timeline")
                if anunciar:
                    f._anunciar_descarga(set(), total_nuevos=nuevos)
            x_service.obtener_timeline_async(acc, tipo, callback=_cb)
        elif tipo == "notifications":
            def _cb(res, err):
                if err or not _vigente():
                    return
                nuevos = f._insertar_items_incremental(self._filas_notifs_actual())
                if nuevos:
                    from app.core.event_bus import event_bus
                    event_bus.emit("sound_trigger", "notification")
                if anunciar:
                    f._anunciar_descarga(set(), total_nuevos=nuevos)
            x_service.obtener_notificaciones_async(acc, callback=_cb)
        elif tipo in self.BUFFERS_ACTORES:
            # Las cuentas no son incrementales: si estaba vacío, se rellena al volver.
            def _cb(res, err):
                if err or not _vigente():
                    return
                self._render_actores(tipo)
            x_service.obtener_actores_async(acc, tipo, callback=_cb)

    def sondear_buffers_fondo(self):
        """Descarga en SEGUNDO PLANO los buffers de BAJO TRÁFICO de X (Inicio, Menciones,
        Notificaciones) que NO son el activo y los guarda en la base, SIN tocar la lista visible,
        para que al cambiar a ellos ya estén al día. Lo dispara _sondeo_fondo_servicios y SOLO se
        ejecuta si la actualización automática de X está activada (por defecto NO: X va a demanda,
        con F5, por el riesgo de bloqueo del cliente no oficial). Se limita a los buffers visibles
        (no ocultos) y a un conjunto mínimo para gastar las menos peticiones posibles."""
        f = self.f
        acc = f.active_x_account_id or self._cuenta()
        if not acc:
            return
        activo = f.active_timeline_type
        ocultos = set(config.get("x", "hidden_buffers", []) or [])
        for tipo in ("home", "mentions", "notifications"):
            if tipo == activo or tipo in ocultos:
                continue
            try:
                if tipo == "notifications":
                    x_service.obtener_notificaciones_async(acc, callback=lambda r, e: None)
                else:
                    x_service.obtener_timeline_async(acc, tipo, callback=lambda r, e: None)
            except Exception:
                pass

    def cargar_anteriores(self):
        """Ctrl+Shift+M en X: descarga una página de tweets MÁS ANTIGUOS y los añade al final del
        buffer (paginación hacia atrás), sin mover el foco. Anuncia cuántos se añadieron."""
        f = self.f
        acc = f.active_x_account_id
        tipo = f.active_timeline_type
        if not acc:
            return
        if not self._es_cronologico(tipo):
            speak(_("Aquí no se pueden cargar publicaciones más antiguas."), interrupt=True)
            return
        speak(_("Cargando publicaciones más antiguas..."), interrupt=True)

        def _cb(nuevos, err):
            if err:
                notificar(_("No se pudieron cargar más publicaciones: {0}").format(err))
                return
            if nuevos and f.active_x_account_id == acc and f.active_timeline_type == tipo:
                # Insertar los más antiguos AL FINAL de la lista, sin mover el foco (silencioso).
                f._insertar_items_incremental(self._filas_posts_actual(tipo))
            f._anunciar_descarga(set(), total_nuevos=nuevos or 0)

        x_service.cargar_anteriores_async(acc, tipo, count=40, callback=_cb)

    # ---------------- Menú contextual ----------------
    def construir_menu_contextual(self, menu, db_id):
        """Añade las opciones de X al menú contextual de la lista principal."""
        f = self.f
        row = db.query_one(
            "SELECT tweet_id, author_id, author_screen_name, liked, retweeted, bookmarked, is_own "
            "FROM x_tweets WHERE id = ?", (db_id,))
        if not row:
            return
        tid, author_id, screen, liked, rt, bm, propio = row

        it = menu.Append(wx.ID_ANY, _("Responder"))
        f.Bind(wx.EVT_MENU, lambda e: self.responder(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Citar"))
        f.Bind(wx.EVT_MENU, lambda e: self.citar(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Quitar me gusta") if liked else _("Me gusta"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion(tid, "unlike" if liked else "like"), it)
        it = menu.Append(wx.ID_ANY, _("Deshacer retweet") if rt else _("Retweet"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion(tid, "unretweet" if rt else "retweet"), it)
        it = menu.Append(wx.ID_ANY, _("Quitar marcador") if bm else _("Añadir a marcadores"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion(tid, "unbookmark" if bm else "bookmark"), it)
        it = menu.Append(wx.ID_ANY, _("Ver conversación"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_hilo(tid), it)
        it = menu.Append(wx.ID_ANY, _("Abrir la conversación como buffer"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.abrir_hilo_como_buffer_db(d), it)
        encuesta = db.query_one("SELECT COALESCE(poll_json,'') FROM x_tweets WHERE id = ?", (db_id,))
        from .x_lectura import datos_encuesta_x
        datos_poll = datos_encuesta_x(encuesta[0] if encuesta else "")
        if datos_poll and not datos_poll.get("final") and not datos_poll.get("selected"):
            it = menu.Append(wx.ID_ANY, _("Votar en la encuesta..."))
            f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.votar_encuesta(d), it)
        it = menu.Append(wx.ID_ANY, f._lbl_atajo("pub_cargar_mas", _("Cargar publicaciones más antiguas")))
        f.Bind(wx.EVT_MENU, lambda e: self.cargar_anteriores(), it)
        it = menu.Append(wx.ID_ANY, _("Ver quién dio me gusta"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_interacciones(tid, "likes"), it)
        it = menu.Append(wx.ID_ANY, _("Ver quién hizo retweet"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_interacciones(tid, "retweets"), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Ver perfil del autor"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_perfil(screen), it)
        it = menu.Append(wx.ID_ANY, _("Ver la cronología de este autor"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_cronologia_autor(author_id, screen), it)
        it = menu.Append(wx.ID_ANY, _("Ver la cronología de un usuario mencionado..."))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.cronologia_usuario_mencionado(d), it)
        it = menu.Append(wx.ID_ANY, _("Ver el perfil de un usuario mencionado..."))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.perfil_usuario_mencionado(d), it)
        it = menu.Append(wx.ID_ANY, _("Seguir al autor"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(author_id, "follow"), it)
        it = menu.Append(wx.ID_ANY, _("Seguir a un usuario mencionado..."))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.seguir_usuario_mencionado(d), it)
        it = menu.Append(wx.ID_ANY, _("Añadir al autor a una lista..."))
        f.Bind(wx.EVT_MENU, lambda e: self._anadir_a_lista(author_id, screen), it)
        it = menu.Append(wx.ID_ANY, _("Silenciar al autor"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(author_id, "mute"), it)
        it = menu.Append(wx.ID_ANY, _("Bloquear al autor"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(author_id, "block"), it)
        if propio:
            it = menu.Append(wx.ID_ANY, _("Borrar publicación"))
            f.Bind(wx.EVT_MENU, lambda e: self.borrar(tid), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Abrir en el navegador"))
        f.Bind(wx.EVT_MENU, lambda e: self._abrir_navegador(screen, tid), it)

    def construir_menu_actores(self, menu, db_id):
        """Menú contextual de los buffers de personas (Seguidores / Cuentas que sigo)."""
        f = self.f
        try:
            _p, user_id, screen = db_id.split(":", 2)
        except (ValueError, AttributeError):
            return
        it = menu.Append(wx.ID_ANY, _("Ver perfil"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_perfil(screen), it)
        it = menu.Append(wx.ID_ANY, _("Seguir"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(user_id, "follow"), it)
        it = menu.Append(wx.ID_ANY, _("Dejar de seguir"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(user_id, "unfollow"), it)
        it = menu.Append(wx.ID_ANY, _("Silenciar"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(user_id, "mute"), it)
        it = menu.Append(wx.ID_ANY, _("Bloquear"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(user_id, "block"), it)
        it = menu.Append(wx.ID_ANY, _("Mensajes directos (conversación)..."))
        f.Bind(wx.EVT_MENU, lambda e: self.abrir_dm(user_id, screen), it)

    def ver_interacciones(self, tweet_id, tipo):
        """Muestra las cuentas que dieron me gusta o hicieron retweet a una publicación."""
        titulo = _("Me gusta") if tipo == "likes" else _("Retweets")
        speak(_("Cargando..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                mostrar_info(self.f, _("Nadie todavía."), titulo)
                return
            opciones = [f"{a['name']} (@{a['screen_name']})" for a in res]
            sel = wx.GetSingleChoice(_("{0} ({1}). Elige una cuenta para ver su perfil:").format(
                titulo, len(res)), titulo, opciones, self.f)
            if sel:
                self.ver_perfil(res[opciones.index(sel)]["screen_name"])
        x_service.obtener_interacciones_async(self._cuenta(), tweet_id, tipo, callback=_cb)

    def abrir_dm(self, user_id, screen):
        """Abre la ventana de conversación de mensajes directos (tipo Telegram): historial,
        envío y TIEMPO REAL (mensajes nuevos y 'escribiendo…' por streaming)."""
        acc = self._cuenta()
        if not acc:
            return
        from .x_dialogs import XConversationDialog
        dlg = XConversationDialog(self.f, acc, user_id, screen)
        dlg.ShowModal()
        dlg.Destroy()

    def programar_publicacion(self):
        """Programa una publicación para una fecha/hora futura."""
        acc = self._cuenta()
        if not acc:
            return
        d1 = CustomTextEntryDialog(self.f, _("Texto de la publicación a programar:"),
                                   _("Programar publicación"), multiline=True)
        texto = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
        d1.Destroy()
        if not texto:
            return
        d2 = CustomTextEntryDialog(self.f, _("Fecha y hora (AAAA-MM-DD HH:MM):"),
                                   _("Programar publicación"))
        valor = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if not valor:
            return
        from datetime import datetime
        try:
            cuando = datetime.strptime(valor, "%Y-%m-%d %H:%M")
        except ValueError:
            mostrar_info(self.f, _("Formato de fecha no válido. Usa AAAA-MM-DD HH:MM."),
                         _("Programar publicación"))
            return

        def _cb(res, err):
            notificar(_("No se pudo programar: {0}").format(err) if err
                      else _("Publicación programada."))
        x_service.programar_tweet_async(acc, texto, cuando, callback=_cb)

    def tendencias(self):
        """Muestra las tendencias actuales; al elegir una, la busca."""
        acc = self._cuenta()
        if not acc:
            return
        speak(_("Cargando tendencias..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                mostrar_info(self.f, _("No hay tendencias ahora."), _("Tendencias"))
                return
            opciones = [(f"{t['name']} ({t['count']})" if t['count'] else t['name']) for t in res]
            sel = wx.GetSingleChoice(_("Tendencias (elige una para buscarla):"),
                                     _("Tendencias de X"), opciones, self.f)
            if sel:
                termino = res[opciones.index(sel)]["name"]
                db.execute_insert(
                    "INSERT INTO x_searches (account_id, name, query, position) VALUES (?,?,?,0)",
                    (acc, termino, termino))
                self.f._cargar_navegacion()
                notificar(_("Búsqueda de la tendencia guardada como buffer."))
        x_service.obtener_tendencias_async(acc, callback=_cb)

    # ---------------- Acciones ----------------
    def nuevo_post(self):
        acc = self._cuenta()
        if not acc:
            return
        dlg = XComposeDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def responder(self, db_id):
        tid = self._tweet_id(db_id)
        acc = self._cuenta()
        if acc and tid:
            dlg = XComposeDialog(self.f, acc, reply_to=tid, titulo=_("Responder en X"))
            dlg.ShowModal(); dlg.Destroy()

    def citar(self, db_id):
        tid = self._tweet_id(db_id)
        acc = self._cuenta()
        if acc and tid:
            dlg = XComposeDialog(self.f, acc, quote_id=tid, titulo=_("Citar en X"))
            dlg.ShowModal(); dlg.Destroy()

    def accion(self, tweet_id, accion):
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                speak(_("Hecho."), interrupt=True)
                self.cargar_listado()
        x_service.accion_async(self._cuenta(), tweet_id, accion, callback=_cb)

    def borrar(self, tweet_id):
        if not confirmar(self.f, _("¿Borrar esta publicación?"), _("Borrar")):
            return
        self.accion(tweet_id, "delete")

    def accion_cuenta(self, user_id, accion):
        if not user_id:
            return
        def _cb(res, err):
            notificar(_("Error: {0}").format(err) if err else _("Hecho."))
        x_service.accion_cuenta_async(self._cuenta(), user_id, accion, callback=_cb)

    def ver_perfil(self, screen_name):
        def _cb(p, err):
            if err or not p:
                notificar(_("No se pudo cargar el perfil."))
                return
            txt = _("{name} (@{screen})\n{followers} seguidores, {following} siguiendo, "
                    "{statuses} publicaciones.\n\n{bio}").format(
                name=p.get("name"), screen=p.get("screen_name"),
                followers=p.get("followers"), following=p.get("following"),
                statuses=p.get("statuses"), bio=p.get("description") or "")
            self.f._mostrar_texto_largo(_("Perfil de X"), txt)
        x_service.obtener_perfil_async(self._cuenta(), screen_name, callback=_cb)

    # ver_hilo (visor de conversación accesible): en x_hilos.py (_XHilosMixin).

    def buscar(self):
        acc = self._cuenta()
        if not acc:
            return
        dlg = CustomTextEntryDialog(self.f, _("Texto a buscar en X:"), _("Buscar en X"))
        if dlg.ShowModal() == wx.ID_OK:
            q = dlg.GetValue().strip()
            if q:
                nombre = q[:40]
                db.execute_insert(
                    "INSERT INTO x_searches (account_id, name, query, position) VALUES (?,?,?,0)",
                    (acc, nombre, q))
                self.f._cargar_navegacion()
                notificar(_("Búsqueda guardada como buffer."))
        dlg.Destroy()

    # Cronologías de usuario: en x_cronologias.py (_XCronologiasMixin).

    def menu_contextual_arbol(self, menu, data, item):
        """Menú contextual de un nodo del árbol de X (búsqueda o cronología de usuario):
        actualizar y borrar. Devuelve True si añadió alguna opción."""
        f = self.f
        x_type = data.get("x_type", "") or ""
        acc = data.get("account_id")
        if self._menu_arbol_extra(menu, x_type, acc, item):
            return True
        if x_type.startswith("search:"):
            q = x_type.split(":", 1)[1]
            it = menu.Append(wx.ID_ANY, _("Actualizar resultados"))
            f.Bind(wx.EVT_MENU, lambda e: (f.tree_nav.SelectItem(item), self.on_refresh()), it)
            it = menu.Append(wx.ID_ANY, _("Eliminar esta búsqueda"))
            f.Bind(wx.EVT_MENU, lambda e: self._borrar_nodo_x(
                "x_searches", "query", q, acc, _("Búsqueda eliminada.")), it)
            return True
        if x_type.startswith("user:"):
            resto = x_type.split(":", 1)[1]
            uid, _s, modo = resto.partition(":")
            modo = modo or "tweets"
            it = menu.Append(wx.ID_ANY, _("Actualizar publicaciones"))
            f.Bind(wx.EVT_MENU, lambda e: (f.tree_nav.SelectItem(item), self.on_refresh()), it)
            it = menu.Append(wx.ID_ANY, _("Borrar esta cronología de usuario"))
            f.Bind(wx.EVT_MENU, lambda e: self._borrar_cronologia(uid, modo, acc), it)
            return True
        return False

    def _borrar_nodo_x(self, tabla, col, valor, acc, mensaje):
        db.execute(f"DELETE FROM {tabla} WHERE account_id = ? AND {col} = ?", (acc, valor))
        self.f._cargar_navegacion()
        notificar(mensaje)

    def _borrar_cronologia(self, uid, modo, acc):
        db.execute("DELETE FROM x_user_timelines WHERE account_id = ? AND user_id = ? AND COALESCE(modo,'tweets') = ?",
                   (acc, str(uid), modo))
        self.f._cargar_navegacion()
        notificar(_("Cronología de usuario eliminada."))

    def on_item_activated(self, index):
        """Intro/doble clic: en publicaciones abre sus enlaces (o el tweet); en cuentas,
        abre el perfil en el navegador."""
        f = self.f
        if index < 0 or index >= len(f.lista_items_db_ids):
            return
        db_id = f.lista_items_db_ids[index]
        modo = getattr(f, "_modo_lista", "")
        from app.core.privacy import abrir_url, limpiar_url
        if modo == "x_actores":
            try:
                _p, _uid, screen = db_id.split(":", 2)
            except (ValueError, AttributeError):
                return
            abrir_url(f"https://x.com/{screen}")
            speak(_("Abriendo el perfil en el navegador."), interrupt=True)
            return
        if modo == "x_scheduled":
            speak(_("Un mensaje programado no tiene enlace que abrir."), interrupt=True)
            return
        # Publicaciones: abrir los enlaces del texto; si no hay, el propio tweet.
        row = db.query_one("SELECT tweet_id, author_screen_name, text FROM x_tweets WHERE id = ?", (db_id,))
        if not row:
            return
        tid, screen, texto = row
        import re
        urls = re.findall(r"https?://[^\s\)\]]+", texto or "")
        if not urls and tid:
            urls = [f"https://x.com/{screen}/status/{tid}"]
        for u in urls:
            abrir_url(limpiar_url(u))
        if urls:
            speak(_("Abriendo {0} enlace(s).").format(len(urls)), interrupt=True)
        else:
            speak(_("No hay nada que abrir."), interrupt=True)

    def _abrir_navegador(self, screen, tid):
        from app.core.privacy import abrir_url
        abrir_url(f"https://x.com/{screen}/status/{tid}")

    # ---------------- Reautenticación automática ----------------
    @staticmethod
    def _es_auth_err(err) -> bool:
        if err is None:
            return False
        if getattr(err, "auth", False):
            return True
        t = str(err).lower()
        return any(k in t for k in ("cookies incorrectas", "caducad", "no ha reconocido",
                                    "no es válida", "no reconocido", "sesión"))

    def _auto_reimportar_y_reintentar(self, reintentar_fn) -> bool:
        """Si la sesión de X caducó, intenta recargar las cookies del navegador en silencio
        y reintenta la acción una sola vez. Devuelve True si lanzó el reintento."""
        if getattr(self, "_reauth_en_curso", False):
            return False
        acc = self.f.active_x_account_id
        if not acc:
            return False
        self._reauth_en_curso = True
        notificar(_("La sesión de X parece caducada; recargando las cookies del navegador..."))

        def _bg(ctx):
            return x_service.reimportar_desde_navegador(acc, "auto")

        def _fin(res, err):
            self._reauth_en_curso = False
            if err:
                notificar(_("No se pudieron recargar las cookies automáticamente: {0}\n"
                            "Reimpórtalas desde Gestionar Cuentas (botón 'Reimportar cookies de X').").format(err))
            else:
                notificar(_("Cookies de X recargadas. Reintentando..."))
                reintentar_fn()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
        return True

    # ---------------- Helpers ----------------
    def _cuenta(self):
        # Resuelve la cuenta de X para uso LOCAL del llamador. NUNCA asigna
        # self.f.active_x_account_id: hacerlo "envenenaba" el buffer activo cuando este helper
        # se llamaba estando en OTRA red (p. ej. el sondeo de fondo de X mientras navegas por
        # Mastodon). Con active_x_account_id fijado, el Intro sobre un toot caía en la rama de X
        # de _activar_item_segun_red y no abría el enlace hasta cambiar de buffer. active_* solo
        # lo fija la selección del árbol (_on_nav_selection / on_select), que es la fuente única.
        acc = getattr(self.f, "active_x_account_id", None)
        if acc:
            return acc
        row = db.query_one("SELECT id FROM accounts WHERE service_id = 'x' AND active = 1 LIMIT 1")
        if row:
            return row[0]
        mostrar_info(self.f, _("Primero añade una cuenta de X desde Gestionar Cuentas."), _("Sin cuentas"))
        return None

    def _tweet_id(self, db_id):
        row = db.query_one("SELECT tweet_id FROM x_tweets WHERE id = ?", (db_id,))
        return row[0] if row else None
