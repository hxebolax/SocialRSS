# -*- coding: utf-8 -*-
"""Controlador Bluesky: construcción de buffers y carga de listados (mixin).
"""


import logging

from app.core.config_manager import config, limite_render
from app.core.database import db
from app.core.speech import speak, notificar

from app.services.bluesky import bluesky_service

log = logging.getLogger(__name__)
from .bluesky_dialogs import (
    BlueskyConversationDialog)


def motivo_legible(reason):
    """Nombre en cristiano del motivo de una notificación (knownValues del lexicon
    app.bsky.notification.listNotifications). Sin esto se leía la clave cruda en inglés."""
    return {
        "like": _("Me gusta"), "repost": _("Repost"), "follow": _("Nuevo seguidor"),
        "mention": _("Mención"), "reply": _("Respuesta"), "quote": _("Cita"),
        "starterpack-joined": _("Starter pack"),
        "verified": _("Cuenta verificada"), "unverified": _("Verificación retirada"),
        "like-via-repost": _("Me gusta a tu repost"),
        "repost-via-repost": _("Repost de tu repost"),
        "subscribed-post": _("Publicación de una cuenta con aviso"),
        "contact-match": _("Contacto encontrado"),
    }.get(reason, reason)



def texto_known_likers(conocidos_json) -> str:
    """ ", 3 de gente que sigues: Ana, Luis y Bea" (o "" si no hay). Va pegado al recuento de
    me gusta, así aparece sin tocar las plantillas de lectura del usuario."""
    if not conocidos_json:
        return ""
    try:
        import json as _json
        datos = _json.loads(conocidos_json) or {}
    except Exception:
        return ""
    total = int(datos.get("count") or 0)
    nombres = [n for n in (datos.get("nombres") or []) if n]
    if not total:
        return ""
    if not nombres:
        return _(" ({0} de gente que sigues)").format(total)
    lista = nombres[0] if len(nombres) == 1 else _("{0} y {1}").format(", ".join(nombres[:-1]), nombres[-1])
    if total > len(nombres):
        return _(" (de gente que sigues: {0} y {1} más)").format(", ".join(nombres), total - len(nombres))
    return _(" (de gente que sigues: {0})").format(lista)

class _CargaMixin:
    def salida_pendiente(self):
        """Bandeja de SALIDA (auditoría 6.6): publicaciones sin confirmar, con reintento
        idempotente (mismo rkey: sin duplicados) y borrado."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            from app.ui.accessible_controls import mostrar_info
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        from app.ui.bluesky_outbox_ui import BlueskyOutboxDialog
        dlg = BlueskyOutboxDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    # Columnas comunes para los dos renders de posts (completo e incremental).
    _SQL_COLS_POST = ("p.id, p.author_name, p.author_handle, p.content, p.indexed_at, "
                      "p.like_count, p.repost_count, p.reply_count, p.labels, p.repost_by_handle, "
                      "p.media_json, COALESCE(p.quote_count,0), COALESCE(p.langs,''), p.liked_uri, "
                      "p.reposted_uri, p.bookmarked, p.reply_parent_uri, p.quote_uri, "
                      "COALESCE(p.known_likers,''), "
                      # A quién responde: lo guardado al descargar o, si no se supo entonces,
                      # el autor del padre si está en la caché local (búsqueda por clave primaria).
                      "COALESCE(NULLIF(p.reply_parent_handle,''), (SELECT pp.author_handle "
                      "FROM bluesky_posts pp WHERE pp.id = p.account_id || ':' || p.reply_parent_uri), ''), "
                      "COALESCE(NULLIF(p.reply_parent_name,''), (SELECT pp.author_name "
                      "FROM bluesky_posts pp WHERE pp.id = p.account_id || ':' || p.reply_parent_uri), '')")
    # Motivo del repost POR BUFFER (bluesky_timeline_posts). El repost es del elemento del
    # timeline, no del post: antes se leía de bluesky_posts y "[Reposteado por @x]" salía también
    # en el feed del autor, en hilos y en búsquedas, donde nadie lo había reposteado.
    _SQL_COL_REASON = "COALESCE(t.reason_json,'')"
    # Datos de MODERACIÓN (van DETRÁS del motivo del repost): mod_json, autor y URI.
    _SQL_COLS_MOD = "COALESCE(p.mod_json,''), COALESCE(p.author_did,''), COALESCE(p.uri,'')"

    @staticmethod
    def _indicadores_post(liked_uri, reposted_uri, bookmarked, reply_parent_uri, quote_uri):
        """Indicadores accesibles del estado del post (paridad con Mastodon)."""
        ind = []
        if liked_uri: ind.append(_("Me gusta"))
        if reposted_uri: ind.append(_("Reposteado"))
        if bookmarked: ind.append(_("Guardado"))
        if reply_parent_uri: ind.append(_("Respuesta"))
        if quote_uri: ind.append(_("Cita"))
        return " ".join(ind)

    def _valores_post(self, r):
        """Construye los valores POR CAMPO de un post (según _SQL_COLS_POST) para volcarlos con
        la plantilla bluesky_post. Devuelve (db_id, valores, fecha_iso, evento_media, texto_filtro,
        contenido_crudo)."""
        from app.ui.main_frame import formatear_tiempo_relativo, evento_media_de_attachments
        import json as _json
        r = tuple(r)
        (db_id, autor, handle, contenido, fecha, likes, reposts, replies, labels, repost_by,
         media_json, quotes, langs, liked_uri, reposted_uri, bookmarked, reply_parent_uri, quote_uri) = r[:18]
        conocidos = r[18] if len(r) > 18 else ""
        reply_handle = r[19] if len(r) > 19 else ""
        reply_name = r[20] if len(r) > 20 else ""
        from app.core.at_lectura import aplicar_repost_y_respuesta, repost_de_reason_json, mi_handle
        if len(r) > 21:
            repost_by, repost_name = repost_de_reason_json(r[21])
        else:
            repost_name = repost_by
        con_usuario = bool(config.get("accessibility", "show_screen_names", False))
        autor_col = autor or handle
        if con_usuario and handle:
            autor_col = f"{autor} (@{handle})"
        cuerpo = (contenido or "").replace("\n", " ").strip()
        # Repost y respuesta según las preferencias de Bluesky (por defecto, como siempre).
        base = aplicar_repost_y_respuesta(
            {"author": autor_col or "", "username": f"@{handle}" if handle else "",
             "content": cuerpo},
            red="bluesky", autor=autor, handle=handle, repost_handle=repost_by,
            repost_name=repost_name, reply_handle=reply_handle, reply_name=reply_name,
            formato_repost=config.get("bluesky", "formato_repost", "original"),
            prefijo_respuesta=bool(config.get("bluesky", "prefijo_respuesta", False)),
            plantilla=config.get("accessibility", "bluesky_post", "") or "",
            con_usuario=con_usuario,
            mi_handle=mi_handle(bluesky_service, self.f.active_bluesky_account_id) if reply_handle else "")
        cuerpo = base["content"]
        # MODERACIÓN (réplica del oficial: etiquetas con su origen, cuenta, palabras
        # silenciadas, publicaciones ocultadas y cita). Se decide ANTES de generar el texto
        # accesible. Difuminado = el cuerpo se sustituye por un aviso (se puede leer en
        # detalles salvo noOverride, o "Mostrar de todos modos"); difuminado de multimedia =
        # el texto se lee pero la multimedia no se describe ni suena; filtrado = la fila no se
        # pinta (los render la saltan con la marca _mod_filtrar).
        from app.services.bluesky.bluesky_moderacion import (
            moderar_post, moderar_cita, etiquetas_visibles, texto_aviso, texto_media_oculta,
            oculto_por_feed_view, prefs_feed_view)
        from app.services.bluesky.bluesky_moddatos import de_json
        from app.services.bluesky import bluesky_service as _bsky
        mod_json = r[22] if len(r) > 22 else ""
        author_did = r[23] if len(r) > 23 else ""
        uri_post = r[24] if len(r) > 24 else ""
        datos_mod = de_json(mod_json)
        prefs_mod = _bsky.prefs_moderacion(self.f.active_bluesky_account_id)
        tipo_buf = str(getattr(self.f, "active_timeline_type", "") or "")
        mod = moderar_post(datos_mod, prefs_mod, contexto="lista", uri=uri_post,
                           etiquetas=labels, author_did=author_did,
                           ignorar_cuenta=tipo_buf.startswith("author:") or tipo_buf == "sent")
        revelado = (db_id in getattr(self, "_revelados_bsky", ())) and not mod["no_override"]
        filtrar = mod["filtrar"]
        if not filtrar and tipo_buf == "home":
            # Preferencias del feed Siguiendo (feedViewPref "home", B12).
            filtrar = oculto_por_feed_view(
                prefs_feed_view(prefs_mod, "home"), bool(reply_parent_uri),
                "reasonrepost" in str(r[21] if len(r) > 21 else "").lower(), bool(quote_uri),
                likes or 0, datos_mod.get("d") or author_did,
                (prefs_mod or {}).get("user_did", ""), datos_mod)
        media_ev = evento_media_de_attachments(media_json)
        lista_etiquetas = [e for e in (labels or "").split(",") if e]

        # Descripción ACCESIBLE de los adjuntos: tipo de cada uno y su texto alternativo (para que
        # el lector lo anuncie), más "Cita" si el post cita a otro.
        partes_media = []
        if media_json and mod["difuminar_media"] and not (revelado and not mod["no_override_media"]):
            # Multimedia etiquetada (porn, sexual, nudity, graphic-media...): ni alt ni sonido.
            partes_media.append(texto_media_oculta(mod))
            media_ev = None
        elif media_json:
            try:
                att = _json.loads(media_json)
            except Exception:
                att = []
            for a in (att or []):
                tipo = (a.get("type") or "").lower()
                alt = (a.get("alt") or "").strip()
                if a.get("video") or "video" in tipo:
                    etq = _("Vídeo")
                elif "gif" in tipo or "animated" in tipo:
                    etq = _("GIF")
                else:
                    etq = _("Imagen")
                etq += (": " + alt) if alt else (" " + _("(sin descripción)"))
                partes_media.append(etq)
        if quote_uri:
            # La publicación CITADA se modera por su cuenta (B13).
            mc = moderar_cita(datos_mod, prefs_mod)
            if mc and (mc.get("difuminar") or mc.get("filtrar")):
                partes_media.append(_("Cita oculta: {0}").format(mc.get("motivo") or ""))
            else:
                partes_media.append(_("Cita"))
        media_desc = ". ".join(partes_media)
        if mod["difuminar"] and not revelado:
            cuerpo = texto_aviso(mod)
            media_desc = ""  # los adjuntos difuminados tampoco se describen
            media_ev = None
        valores = {
            "author": base["author"],
            "username": base["username"],
            "content": cuerpo,
            "reply_to": base["reply_to"],
            "date": formatear_tiempo_relativo(fecha) if fecha else "",
            "indicators": self._indicadores_post(liked_uri, reposted_uri, bookmarked, reply_parent_uri, quote_uri),
            "media": media_desc,
            "cw": ", ".join(etiquetas_visibles(lista_etiquetas)),
            "counts": _("{0} me gusta, {1} reposts, {2} respuestas, {3} citas").format(
                likes or 0, reposts or 0, replies or 0, quotes or 0) + texto_known_likers(conocidos),
            "language": langs or "",
            "_mod_filtrar": filtrar,
        }
        if mod["avisar"] and mod["alertas"] and (revelado or not mod["difuminar"]):
            valores["content"] = _("[Aviso: {0}] ").format(", ".join(mod["alertas"])) + valores["content"]
        texto_filtro = f"{autor or ''} {handle or ''} {repost_by or ''}"
        return db_id, valores, fecha, media_ev, texto_filtro, (contenido or "")

    def construir_servicio(self, root):
        """Construye el nodo del servicio Bluesky (llamado desde _cargar_navegacion)."""
        tree = self.f.tree_nav
        n_bsky = tree.AppendItem(root, _("Bluesky"),
                                 data={"type": "category_root", "service": "bluesky"})
        accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'bluesky' AND active = 1 ORDER BY position, id")
        for r_id, r_name in accs:
            n_acc = tree.AppendItem(n_bsky, r_name,
                                    data={"type": "account", "account_id": r_id, "service": "bluesky"})
            # Normalizar buffer_order (todos reordenables) y persistir si faltan.
            buffer_order = list(config.get("bluesky", "buffer_order", list(self._TODOS)))
            faltan = [b for b in self._TODOS if b not in buffer_order]
            if faltan:
                buffer_order.extend(faltan)
                config.set("bluesky", "buffer_order", buffer_order)
            ocultos = config.get("bluesky", "hidden_buffers", [])
            for b in buffer_order:
                if b in ocultos:
                    continue
                label = self.BUFFER_LABELS.get(b)
                if not label:
                    continue
                tree.AppendItem(n_acc, label, data={
                    "type": "bluesky_timeline", "bsky_type": b,
                    "account_id": r_id, "service": "bluesky", "base_label": label})

            # Feeds personalizados guardados (subnodos navegables)
            for feed in config.get("bluesky", "saved_feeds", []):
                tree.AppendItem(n_acc, _("Feed: {0}").format(feed.get("name", "")), data={
                    "type": "bluesky_timeline", "bsky_type": "feed:" + feed.get("uri", ""),
                    "account_id": r_id, "service": "bluesky",
                    "base_label": _("Feed: {0}").format(feed.get("name", ""))})
            # Listas (subnodos navegables)
            for lst in config.get("bluesky", "lists_cache", []):
                tree.AppendItem(n_acc, _("Lista: {0}").format(lst.get("name", "")), data={
                    "type": "bluesky_timeline", "bsky_type": "list:" + lst.get("uri", ""),
                    "account_id": r_id, "service": "bluesky",
                    "base_label": _("Lista: {0}").format(lst.get("name", ""))})
            # Búsquedas guardadas (grupo dinámico "searches": se puede ocultar entero desde el gestor
            # de buffers; para quitar una concreta, su menú contextual en el árbol).
            if "searches" not in ocultos:
                for termino in config.get("bluesky", "saved_searches", []):
                    tree.AppendItem(n_acc, _("Búsqueda: {0}").format(termino), data={
                        "type": "bluesky_timeline", "bsky_type": "search:" + termino,
                        "account_id": r_id, "service": "bluesky",
                        "base_label": _("Búsqueda: {0}").format(termino)})
            # Cronologías de cuentas (grupo dinámico "user_timelines"). Cada una es {did, handle}.
            if "user_timelines" not in ocultos:
                for u in config.get("bluesky", "user_timelines", []):
                    did = u.get("did") if isinstance(u, dict) else None
                    if not did:
                        continue
                    handle = (u.get("handle") if isinstance(u, dict) else "") or did
                    tree.AppendItem(n_acc, _("Cuenta: @{0}").format(handle), data={
                        "type": "bluesky_timeline", "bsky_type": "author:" + did,
                        "account_id": r_id, "service": "bluesky",
                        "base_label": _("Cuenta: @{0}").format(handle)})
            # Conversaciones abiertas como buffer (grupo dinámico "thread_buffers"), como en
            # Mastodon (petición de usuarios): cada una es {uri, display}. Se puede ocultar
            # el grupo entero; para quitar una concreta, su menú contextual en el árbol.
            if "thread_buffers" not in ocultos:
                for c in config.get("bluesky", "thread_buffers", []):
                    uri_c = c.get("uri") if isinstance(c, dict) else None
                    if not uri_c:
                        continue
                    etiqueta = _("Conversación: {0}").format(
                        (c.get("display") if isinstance(c, dict) else "") or uri_c.rsplit("/", 1)[-1])
                    tree.AppendItem(n_acc, etiqueta, data={
                        "type": "bluesky_timeline", "bsky_type": "thread:" + uri_c,
                        "account_id": r_id, "service": "bluesky", "base_label": etiqueta})
        return n_bsky

    def reordenar_buffer(self, data, subir):
        """Ctrl+Arriba/Abajo sobre un buffer de Bluesky. Los buffers DINÁMICOS
        (búsquedas, cronologías de cuentas y conversaciones) se reordenan dentro
        de su propio grupo, como en Mastodon (petición de usuarios)."""
        b_type = data.get("bsky_type") or ""
        # Grupos dinámicos: (prefijo, clave de config, cómo se identifica cada elemento).
        dinamicos = {
            "search:": ("saved_searches", lambda it, v: it == v),
            "author:": ("user_timelines", lambda it, v: isinstance(it, dict) and it.get("did") == v),
            "thread:": ("thread_buffers", lambda it, v: isinstance(it, dict) and it.get("uri") == v),
        }
        for prefijo, (clave_cfg, coincide) in dinamicos.items():
            if b_type.startswith(prefijo):
                valor = b_type.split(":", 1)[1]
                items = list(config.get("bluesky", clave_cfg, []))
                pos = next((i for i, it in enumerate(items) if coincide(it, valor)), None)
                if pos is None:
                    return False
                nuevo = pos - 1 if subir else pos + 1
                if not (0 <= nuevo < len(items)):
                    return False
                items[pos], items[nuevo] = items[nuevo], items[pos]
                config.set("bluesky", clave_cfg, items)
                return True
        buffer_order = config.get("bluesky", "buffer_order", list(self._TODOS))
        if b_type not in buffer_order:
            return False
        idx = buffer_order.index(b_type)
        new_idx = idx - 1 if subir else idx + 1
        if not (0 <= new_idx < len(buffer_order)):
            return False
        buffer_order[idx], buffer_order[new_idx] = buffer_order[new_idx], buffer_order[idx]
        config.set("bluesky", "buffer_order", buffer_order)  # set() ya guarda (autosave)
        return True

    # ---------------- Selección ----------------
    def on_select(self, item, data, t):
        f = self.f
        f.active_bluesky_account_id = data.get("account_id")
        # Panel de detalles opcional (Preferencias -> Bluesky). Visibilidad centralizada.
        f._aplicar_visibilidad_detalles()
        if t == "bluesky_timeline":
            f.active_timeline_type = data.get("bsky_type")
            f.lbl_buffer_titulo.SetLabel("Bluesky - " + f.tree_nav.GetItemText(item))
            self.cargar_listado()
        else:
            f.lista_items.DeleteAllItems()
            f.lista_items.limpiar_lecturas()
            f.txt_detalles.Clear()

    # ---------------- Carga de listas ----------------
    def _preparar_columnas_posts(self):
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        # Columnas según la plantilla de Bluesky elegida por el usuario (separada por red).
        self._cols_posts = f._construir_columnas_plantilla("bluesky_post")

    # Buffers cuyo contenido son cuentas (no posts).
    BUFFERS_ACTORES = ("followers", "follows", "blocks", "mutes")
    # Buffers "gestores": al abrirlos se descargan y reconstruyen el árbol.
    BUFFERS_GESTOR = ("feeds", "lists")

    def _es_buffer_posts(self, tipo):
        return (tipo in ("home", "mentions", "sent", "likes", "bookmarks")
                or tipo.startswith(("feed:", "list:", "search:", "author:", "thread:")))

    # Buffers que se pueden auto-descargar (todos los que on_refresh sabe traer).
    def _es_autodescargable(self, tipo):
        return (tipo in ("notifications", "trending", "starterpacks", "convos")
                or tipo in self.BUFFERS_ACTORES or tipo in self.BUFFERS_GESTOR
                or self._es_buffer_posts(tipo))

    def cargar_listado(self):
        f = self.f
        if not f.active_bluesky_account_id:
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
        elif tipo == "convos":
            self._cargar_convos()
        elif tipo in self.BUFFERS_GESTOR:
            self._cargar_gestor(tipo)
        elif self._es_buffer_posts(tipo):
            self._cargar_posts(tipo)
        else:
            # Buffers de fases posteriores (bookmarks).
            self._preparar_columnas_posts()
            f._modo_lista = "bluesky_posts"
            speak(_("Este buffer estará disponible en una próxima fase."), interrupt=True)
            return

        # AUTO-CARGA (como Mastodon): si el buffer está vacío (nunca se descargó o se vació entre
        # sesiones), traerlo del servidor EN SILENCIO, sin que el usuario pulse nada. Cuando llegan
        # los datos se repinta solo. Por eso ya no se avisa "Presione F5" en los buffers vacíos.
        if f.lista_items.GetItemCount() == 0 and self._es_autodescargable(tipo):
            self.on_refresh(silencioso=True)

    def _filas_notifs_actual(self):
        """Filas deseadas de notificaciones de Bluesky (mismo formato que
        _cargar_notificaciones), para inserción incremental."""
        f = self.f
        filas = []
        if not f.active_bluesky_account_id:
            return filas
        from app.ui.main_frame import formatear_tiempo_relativo
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        plantilla = config.get("accessibility", "bluesky_notification", "") or definicion("bluesky_notification")["default"]
        cols = getattr(f, "_cols_actuales", None) or [("type", ""), ("author", ""), ("date", "")]
        rows = db.query(f"""
            SELECT id, reason, author_name, author_handle, indexed_at, content,
                   COALESCE(mod_json,''), COALESCE(author_did,'')
            FROM bluesky_notifications WHERE account_id = ?
            ORDER BY indexed_at DESC LIMIT {limite_render("bluesky", "max_notifications")}
        """, (f.active_bluesky_account_id,))
        if not config.get("general", "newest_first", True):
            rows = list(rows)[::-1]
        for db_id, reason, autor, handle, fecha, contenido, mod_json, autor_did in rows:
            # Moderación de la notificación (B5): etiquetas, cuenta y palabras silenciadas.
            ocultar, contenido = self._moderar_notif(contenido, mod_json, autor_did)
            if ocultar:
                continue
            valores = {
                "type": motivo_legible(reason), "author": autor or "",
                "username": f"@{handle}" if handle else "",
                # Texto de la publicación aludida: sin él no se sabía a qué mensaje se refería
                # cada notificación (petición de usuarios).
                "content": (contenido or "").replace("\n", " ").strip(),
                "date": formatear_tiempo_relativo(fecha) if fecha else "",
            }
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura))
        return filas

    def _filas_convos_actual(self):
        """Filas deseadas de conversaciones (DMs) de Bluesky (mismo formato que
        _cargar_convos), para actualización en su sitio + inserción de nuevas."""
        f = self.f
        filas = []
        if not f.active_bluesky_account_id:
            return filas
        from app.ui.main_frame import formatear_tiempo_relativo
        rows = db.query("""
            SELECT convo_id, title, last_message, last_message_date, unread_count, muted
            FROM bluesky_convos WHERE account_id = ?
            ORDER BY last_message_date DESC
        """, (f.active_bluesky_account_id,))
        for convo_id, title, last, fecha, unread, muted in rows:
            titulo = title or "(conversación)"
            if muted:
                titulo = "🔇 " + titulo
            last_corto = (last or "").replace("\n", " ").strip()
            fecha_rel = formatear_tiempo_relativo(fecha) if fecha else ""
            lectura = titulo
            if unread:
                lectura += _(". {0} sin leer").format(unread)
            lectura += f". {last_corto}. {fecha_rel}."
            filas.append((f"convo:{convo_id}",
                          [titulo, last_corto, fecha_rel, str(unread) if unread else ""], lectura))
        return filas

    def _filas_posts_actual(self, tipo):
        """Filas deseadas (db_id, [cols], lectura) del buffer de posts, con el MISMO
        formato que _cargar_posts, para la inserción incremental silenciosa."""
        f = self.f
        filas = []
        if not f.active_bluesky_account_id:
            return filas
        from app.core.content_filters import ContentFilterEngine
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        filtros = ContentFilterEngine.cargar("bluesky")
        plantilla = config.get("accessibility", "bluesky_post", "") or definicion("bluesky_post")["default"]
        # Las columnas activas de la lista (las creó _construir_columnas_plantilla al cargar).
        cols = getattr(f, "_cols_actuales", None) or getattr(self, "_cols_posts", [])
        # Sin tope al pintar el buffer: se muestran todos los posts cargados (el usuario no quiere
        # límite). El control del tamaño es la poda opcional de mantenimiento.
        limite = limite_render("bluesky", "max_items_buffer")
        rows = db.query(f"""
            SELECT {self._SQL_COLS_POST}, {self._SQL_COL_REASON}, {self._SQL_COLS_MOD}
            FROM bluesky_posts p
            JOIN bluesky_timeline_posts t ON p.id = t.post_db_id
            WHERE t.account_id = ? AND t.timeline = ?
            -- Orden DEL SERVIDOR (sort_key, auditoría BSKY-004): conserva ranking de
            -- feeds, relevancia de búsqueda y la posición del repost. Las filas antiguas
            -- sin sort_key van después, por fecha.
            ORDER BY (t.sort_key IS NULL), t.sort_key DESC, p.indexed_at DESC LIMIT ?
        """, (f.active_bluesky_account_id, tipo, limite))
        if not config.get("general", "newest_first", True):
            rows = list(rows)[::-1]
        for r in rows:
            db_id, valores, fecha, media_ev, txt_filtro, contenido = self._valores_post(r)
            if valores.get("_mod_filtrar"):
                continue  # oculto por moderación (etiquetas + tus preferencias, 8.1)
            if filtros and ContentFilterEngine.evaluar("bluesky", contenido, txt_filtro, filtros)["hide"]:
                continue
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura, media_ev, fecha))
        return filas

    def _cargar_posts(self, tipo):
        f = self.f
        f.lista_items.congelar_diferido()
        self._preparar_columnas_posts()
        f._modo_lista = "bluesky_posts"
        from app.core.content_filters import ContentFilterEngine
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        cols = self._cols_posts
        filtros = ContentFilterEngine.cargar("bluesky")
        plantilla = config.get("accessibility", "bluesky_post", "") or definicion("bluesky_post")["default"]
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)

        # Sin tope al pintar el buffer: se muestran todos los posts cargados (el usuario no quiere
        # límite). El control del tamaño es la poda opcional de mantenimiento.
        limite = limite_render("bluesky", "max_items_buffer")
        rows = db.query(f"""
            SELECT {self._SQL_COLS_POST}, {self._SQL_COL_REASON}, {self._SQL_COLS_MOD}
            FROM bluesky_posts p
            JOIN bluesky_timeline_posts t ON p.id = t.post_db_id
            WHERE t.account_id = ? AND t.timeline = ?
            -- Orden DEL SERVIDOR (sort_key, auditoría BSKY-004): conserva ranking de
            -- feeds, relevancia de búsqueda y la posición del repost. Las filas antiguas
            -- sin sort_key van después, por fecha.
            ORDER BY (t.sort_key IS NULL), t.sort_key DESC, p.indexed_at DESC LIMIT ?
        """, (f.active_bluesky_account_id, tipo, limite))
        if not config.get("general", "newest_first", True):
            rows = list(rows)[::-1]

        idx = 0
        for r in rows:
            db_id, valores, fecha, media_ev, txt_filtro, contenido = self._valores_post(r)
            if valores.get("_mod_filtrar"):
                continue  # oculto por moderación (etiquetas + tus preferencias, 8.1)
            if filtros and ContentFilterEngine.evaluar("bluesky", contenido, txt_filtro, filtros)["hide"]:
                continue
            pos = f._volcar_fila_plantilla(idx, cols, valores)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            f.lista_items.establecer_medios(pos, media_ev)
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, valores["date"])
            idx += 1

        f._restaurar_foco_lista()

    def _cargar_notificaciones(self):
        f = self.f
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        cols = f._construir_columnas_plantilla("bluesky_notification")
        f._modo_lista = "bluesky_notifs"
        from app.ui.main_frame import formatear_tiempo_relativo
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        plantilla = config.get("accessibility", "bluesky_notification", "") or definicion("bluesky_notification")["default"]
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)
        rows = db.query(f"""
            SELECT id, reason, author_name, author_handle, indexed_at, content,
                   COALESCE(mod_json,''), COALESCE(author_did,'')
            FROM bluesky_notifications WHERE account_id = ?
            ORDER BY indexed_at DESC LIMIT {limite_render("bluesky", "max_notifications")}
        """, (f.active_bluesky_account_id,))
        if not config.get("general", "newest_first", True):
            rows = list(rows)[::-1]
        idx = 0
        for (db_id, reason, autor, handle, fecha, contenido, mod_json, autor_did) in rows:
            # Moderación de la notificación (B5): etiquetas, cuenta y palabras silenciadas.
            ocultar, contenido = self._moderar_notif(contenido, mod_json, autor_did)
            if ocultar:
                continue
            valores = {
                "type": motivo_legible(reason),
                "author": autor or "",
                "username": f"@{handle}" if handle else "",
                # Texto de la publicación aludida (petición de usuarios: antes iba vacío y no
                # había forma de saber a qué mensaje correspondía la notificación).
                "content": (contenido or "").replace("\n", " ").strip(),
                "date": formatear_tiempo_relativo(fecha) if fecha else "",
            }
            pos = f._volcar_fila_plantilla(idx, cols, valores)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, valores["date"])
            idx += 1
        f._restaurar_foco_lista()
        # Marcar las notificaciones como VISTAS en el servidor (updateSeen) al MOSTRAR el
        # buffer al usuario, nunca en un sondeo de fondo (auditoría BSKY-006: el método
        # existía pero nadie lo llamaba, así que el contador remoto quedaba pendiente
        # para siempre). Anti-rebote de 30 s para no repetirlo al navegar arriba y abajo.
        try:
            import time as _t
            marca = getattr(self, "_visto_notifs", {})
            self._visto_notifs = marca
            acc = f.active_bluesky_account_id
            if rows and _t.monotonic() - marca.get(acc, 0) > 30:
                marca[acc] = _t.monotonic()
                bluesky_service.marcar_notificaciones_vistas_async(acc, callback=lambda r, e: None)
        except Exception as e:
            log.debug(f"No se pudo marcar las notificaciones como vistas: {e}")

    def _cargar_actores(self, bucket):
        """Buffers de personas (seguidores/siguiendo/bloqueados/silenciados)."""
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Nombre"), width=200)
        f.lista_items.InsertColumn(1, _("Handle"), width=220)
        f.lista_items.InsertColumn(2, _("Biografía"), width=360)
        f._modo_lista = "bluesky_actores"
        rows = db.query("""
            SELECT did, handle, display_name, description, following_uri, blocking_uri, muted
            FROM bluesky_actors WHERE account_id = ? AND bucket = ?
            ORDER BY position, handle
        """, (f.active_bluesky_account_id, bucket))
        for idx, (did, handle, nombre, bio, fo_uri, bl_uri, muted) in enumerate(rows):
            # Bluesky devuelve "handle.invalid" cuando su appview no resuelve el handle
            # de una cuenta. Mostramos el mejor identificador disponible para no enseñar
            # ese texto: nombre visible -> handle válido -> DID abreviado.
            handle_valido = handle and handle != "handle.invalid"
            handle_col = f"@{handle}" if handle_valido else _("(handle no disponible)")
            handle_lectura = handle if handle_valido else (did[:24] + "…")
            nombre = nombre or (handle if handle_valido else handle_lectura)
            bio_corta = (bio or "").replace("\n", " ").strip()
            pos = f.lista_items.InsertItem(idx, nombre)
            f.lista_items.SetItem(pos, 1, handle_col)
            f.lista_items.SetItem(pos, 2, bio_corta)
            # Separador '|' (no ':'): el DID de Bluesky contiene ':' (did:plc/did:web).
            f.lista_items_db_ids.append(f"user:{did}|{handle}")
            plantilla = config.get("accessibility", "bluesky_person",
                                   "{display} (@{acct}). {bio}")
            try:
                lectura = plantilla.format(display=nombre, acct=handle_lectura, bio=bio_corta,
                                           followers="", following="", posts="")
            except Exception:
                lectura = f"{nombre} (@{handle_lectura}). {bio_corta}"
            f.lista_items.establecer_lectura(pos, lectura)
        f._restaurar_foco_lista()

    def _cargar_tendencias(self):
        """Buffer de tendencias (temas en alza)."""
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Tendencia"), width=320)
        f.lista_items.InsertColumn(1, _("Publicaciones"), width=140)
        f._modo_lista = "bluesky_trends"
        trends = getattr(self, "_trends_cache", [])
        for idx, t in enumerate(trends):
            pos = f.lista_items.InsertItem(idx, t.get("topic", ""))
            f.lista_items.SetItem(pos, 1, str(t.get("posts", "")))
            f.lista_items_db_ids.append("trend:" + (t.get("link") or t.get("topic", "")))
            f.lista_items.establecer_lectura(pos, f"{t.get('topic','')}. {t.get('posts','')} publicaciones.")
        f._restaurar_foco_lista()

    def _cargar_starterpacks(self):
        """Buffer de starter packs del usuario."""
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Starter pack"), width=320)
        f.lista_items.InsertColumn(1, _("Miembros"), width=120)
        f._modo_lista = "bluesky_packs"
        packs = getattr(self, "_packs_cache", [])
        for idx, p in enumerate(packs):
            pos = f.lista_items.InsertItem(idx, p.get("name", ""))
            f.lista_items.SetItem(pos, 1, str(p.get("members", "")))
            f.lista_items_db_ids.append("pack:" + (p.get("uri") or ""))
            f.lista_items.establecer_lectura(pos, f"{p.get('name','')}. {p.get('members','')} miembros.")
        f._restaurar_foco_lista()

    def _cargar_convos(self):
        """Buffer de mensajes directos: lista de conversaciones (tipo Telegram)."""
        f = self.f
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Conversación"), width=220)
        f.lista_items.InsertColumn(1, _("Último mensaje"), width=380)
        f.lista_items.InsertColumn(2, _("Fecha"), width=120)
        f.lista_items.InsertColumn(3, _("No leídos"), width=90)
        f._modo_lista = "bluesky_convos"
        from app.ui.main_frame import formatear_tiempo_relativo
        rows = db.query("""
            SELECT convo_id, title, last_message, last_message_date, unread_count, muted
            FROM bluesky_convos WHERE account_id = ?
            ORDER BY last_message_date DESC
        """, (f.active_bluesky_account_id,))
        for idx, (convo_id, title, last, fecha, unread, muted) in enumerate(rows):
            titulo = title or "(conversación)"
            if muted:
                titulo = "🔇 " + titulo
            last_corto = (last or "").replace("\n", " ").strip()
            fecha_rel = formatear_tiempo_relativo(fecha) if fecha else ""
            pos = f.lista_items.InsertItem(idx, titulo)
            f.lista_items.SetItem(pos, 1, last_corto)
            f.lista_items.SetItem(pos, 2, fecha_rel)
            f.lista_items.SetItem(pos, 3, str(unread) if unread else "")
            f.lista_items_db_ids.append(f"convo:{convo_id}")
            lectura = titulo
            if unread:
                lectura += _(". {0} sin leer").format(unread)
            lectura += f". {last_corto}. {fecha_rel}."
            f.lista_items.establecer_lectura(pos, lectura)
        f._restaurar_foco_lista()

    def abrir_conversacion(self, convo_id):
        """Abre la ventana de conversación de un DM."""
        row = db.query_one("SELECT title FROM bluesky_convos WHERE account_id = ? AND convo_id = ?",
                           (self.f.active_bluesky_account_id, convo_id))
        titulo = row[0] if row else "(conversación)"
        dlg = BlueskyConversationDialog(self.f, self.f.active_bluesky_account_id, convo_id, titulo)
        dlg.ShowModal()
        dlg.Destroy()
        # Al cerrar, refrescar la lista (contadores de no leídos)
        if self.f.active_timeline_type == "convos":
            bluesky_service.cargar_convos_async(
                self.f.active_bluesky_account_id,
                callback=lambda r, e: self._cargar_convos() if not e else None)

    def mensaje_directo_a(self, did):
        """Abre (o crea) una conversación con un usuario por su DID."""
        acc = self.f.active_bluesky_account_id or self._cuenta_activa_o_primera()
        if not acc:
            return
        speak(_("Abriendo conversación..."), interrupt=True)

        def _cb(res, error):
            if error or not res:
                notificar(_("No se pudo abrir la conversación: {0}").format(error or ""))
                return
            dlg = BlueskyConversationDialog(self.f, acc, res["convo_id"], res["title"])
            dlg.ShowModal()
            dlg.Destroy()
        bluesky_service.abrir_convo_con_async(acc, did, callback=_cb)

    def _cargar_gestor(self, tipo):
        """Buffers 'Feeds'/'Listas': muestran los feeds o listas guardados EN LA LISTA
        principal (no se reconstruye el árbol, para no perder el foco). Cada elemento
        se abre con Intro. Pulsa F5 para descargar/actualizar desde Bluesky."""
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Nombre"), width=280)
        f.lista_items.InsertColumn(1, _("Descripción"), width=380)
        f._modo_lista = "bluesky_gestor"
        clave = "saved_feeds" if tipo == "feeds" else "lists_cache"
        prefijo = "feed:" if tipo == "feeds" else "list:"
        items = config.get("bluesky", clave, [])
        for idx, it in enumerate(items):
            nombre = it.get("name", "") or it.get("uri", "")
            desc = it.get("description", "") or (it.get("creator", "") and ("@" + it.get("creator", "")))
            pos = f.lista_items.InsertItem(idx, nombre)
            f.lista_items.SetItem(pos, 1, (desc or "").replace("\n", " "))
            f.lista_items_db_ids.append(prefijo + it.get("uri", ""))
            f.lista_items.establecer_lectura(pos, f"{nombre}. {desc or ''}. Intro para abrir.")
        f._restaurar_foco_lista()

    def _descargar_gestor(self, tipo, silencioso=False):
        """Descarga 'Feeds'/'Listas' desde Bluesky y recarga la lista principal (sin reconstruir
        el árbol). Con silencioso=True no dice 'Descargando…' (para la auto-carga al entrar)."""
        f = self.f
        acc = f.active_bluesky_account_id
        if not silencioso:
            speak(_("Descargando..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
                return
            self._cargar_gestor(tipo)
            n = len(res or [])
            speak(_("{0} feeds.").format(n) if tipo == "feeds" else _("{0} listas.").format(n), interrupt=True)

        if tipo == "feeds":
            bluesky_service.obtener_feeds_guardados_async(acc, callback=_cb)
        else:
            bluesky_service.obtener_listas_async(acc, callback=_cb)

    def _re_seleccionar(self, bsky_type):
        """Reselecciona un buffer de Bluesky en el árbol tras reconstruirlo (solo en
        acciones explícitas de menú, como guardar una búsqueda)."""
        try:
            self.f._re_seleccionar_bluesky_timeline(self.f.active_bluesky_account_id, bsky_type)
        except Exception:
            pass

    def abrir_feed_o_lista(self, db_id):
        """Abre los posts de un feed o lista elegido en el gestor (db_id 'feed:'/'list:')."""
        f = self.f
        f.active_timeline_type = db_id
        f.lbl_buffer_titulo.SetLabel("Bluesky - " + db_id.split(":", 1)[0])
        speak(_("Abriendo..."), interrupt=True)
        bluesky_service.cargar_timeline_async(
            f.active_bluesky_account_id, timeline_type=db_id,
            callback=lambda r, e: self._cargar_posts(db_id) if not e else
            speak(_("Error: {0}").format(e), interrupt=True))

    # ---------------- Selección de fila (vista previa) ----------------
