# -*- coding: utf-8 -*-
"""MainFrame (mixin): app_despacho.
Metodos: _filas_telegram_actual, _filas_mastodon_actual, _filas_rss_actual, _construir_lectura_rss, _lectura_rss_de_meta, _refrescar_fila_rss, _on_rss_refreshed, _cargar_listado_chats_telegram ...
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
import json
import os
import wx

from app.core.config_manager import config, limite_render
from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.telegram.telegram_service import telegram_service


log = logging.getLogger(__name__)
from .ui_helpers import (formatear_tiempo_relativo,
                         evento_media_de_attachments)


class _Mf_app_despacho:
    def _filas_telegram_actual(self):
        """Filas deseadas (db_id, [cols], lectura) de la lista de chats de Telegram
        activa, con el MISMO formato que _cargar_listado_chats_telegram."""
        filas = []
        acc = self.active_telegram_account_id
        if not acc or self.active_timeline_type == "contacts":
            return filas
        sql = """
        SELECT id, chat_id, title, chat_type, unread_count, last_message, last_message_date,
               COALESCE(muted,0), COALESCE(pinned,0), COALESCE(verified,0), COALESCE(protected,0)
        FROM telegram_chats WHERE account_id = ?
        """
        params = [acc]
        if self.active_timeline_type == "unread":
            sql += " AND unread_count > 0"
        elif self.active_timeline_type in ("private", "bot", "group", "supergroup", "channel"):
            sql += " AND chat_type = ?"
            params.append(self.active_timeline_type)
        elif str(self.active_timeline_type).startswith("folder:"):
            folder_id = self.active_timeline_type.split(":", 1)[1]
            fila = db.query_one(
                "SELECT chat_ids_json FROM telegram_folders WHERE account_id = ? AND folder_id = ?",
                (acc, folder_id))
            ids = []
            if fila and fila[0]:
                try:
                    ids = json.loads(fila[0])
                except Exception:
                    ids = []
            if ids:
                sql += f" AND chat_id IN ({','.join('?' for _ in ids)})"
                params.extend(ids)
            else:
                sql += " AND 0"
        sql += f" ORDER BY pinned DESC, last_message_date DESC LIMIT {limite_render('telegram', 'max_chats_list')}"
        tipo_labels = {"private": _("Privado"), "bot": _("Bot"), "group": _("Grupo"),
                       "supergroup": _("Supergrupo"), "channel": _("Canal")}
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        plantilla = config.get("accessibility", "telegram_chat", "") or definicion("telegram_chat")["default"]
        cols = getattr(self, "_cols_actuales", None) or definicion("telegram_chat")["default"]
        if isinstance(cols, str):
            from app.core.plantillas import columnas_de_plantilla
            cols = columnas_de_plantilla(cols)
        for r in db.query(sql, tuple(params)):
            db_id, chat_id, title, chat_type, unread, last_msg, last_date, muted, pinned, verified, protected = r
            fecha_rel = formatear_tiempo_relativo(str(last_date)) if last_date else ""
            tipo = tipo_labels.get(chat_type, "")
            valores = {
                "chat": title or str(chat_id),
                "type": tipo,
                "content": (last_msg or "").replace("\n", " "),
                "date": fecha_rel,
                "unread": (_("{0} sin leer").format(unread) if unread else ""),
                "silenciado": _("Silenciado") if muted else "",
                "fijado": _("Fijado") if pinned else "",
                "verificado": _("Verificado") if verified else "",
                "protegido": _("Protegido") if protected else "",
            }
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((f"{db_id}|{chat_id}|{title}", cols_vals, lectura))
        return filas

    def _filas_mastodon_actual(self):
        """Devuelve las filas deseadas (db_id, [cols], lectura) del timeline o las
        notificaciones de Mastodon activas, con el MISMO formato que la lista, para
        la inserción incremental silenciosa.

        Las filas que YA están en la lista no se vuelven a formatear (van con valores
        None: _insertar_items_incremental solo usa su posición). Formatear el buffer
        entero en cada sondeo era trabajo de CPU en el hilo de la interfaz que, con los
        sondeos de fondo compitiendo por el GIL, la congelaba varios segundos.
        En notificaciones, la fila lleva en [5] su id de notificación (clave única)."""
        filas = []
        acc = self.active_mastodon_account_id
        if not acc:
            return filas
        visibilidad_map = {"public": _("Público"), "unlisted": _("No listado"),
                           "private": _("Privado"), "direct": _("Mensaje directo")}
        # Siempre los MÁS RECIENTES (DESC + LIMIT); si se pide "antiguos primero" se invierte la
        # lista construida al final, en vez de traer los más antiguos de la base con ASC + LIMIT.
        order_by_desc = config.get("general", "newest_first", True)
        order_by = "DESC"

        if self.active_timeline_type in ("notifications", "mentions"):
            # Igual que en la carga normal: Menciones = solo menciones; Notificaciones = el resto.
            filtro_tipo = "AND n.type = 'mention'" if self.active_timeline_type == "mentions" else "AND n.type != 'mention'"
            sql = f"""
            SELECT n.id, n.type, n.created_at, n.from_name, n.from_username, n.status_id, s.content, s.visibility, s.filtered_by
            FROM mastodon_notifications n
            LEFT JOIN mastodon_statuses s ON s.account_id = n.account_id AND s.status_id = n.status_id
            WHERE n.account_id = ? {filtro_tipo}
            ORDER BY n.created_at {order_by} LIMIT {limite_render("mastodon")}
            """
            from .ui_helpers import prefijo_notificacion_mastodon
            plantilla_n = config.get("accessibility", "notification_template", "{type}: {author}. {content}. {date}.")
            _ocultar_nombres = config.get("accessibility", "hide_emojis_names", False)
            import re as _re_n
            ya_en_lista = set(self._claves_foco()) if self._es_lista_notif_mastodon() else set()
            from app.services.mastodon.filtros_servidor import evaluar as _eval_filtro_srv
            for r in db.query(sql, (acc,)):
                nid, ntype, ndate, fname, fuser, status_id, scontent, svis, sfiltered = r
                _ev_srv = _eval_filtro_srv(sfiltered, acc, "notifications")
                if _ev_srv["accion"] == "hide":
                    continue
                if status_id and scontent:
                    from app.services.mastodon.filtros_servidor import contenido_con_filtro
                    scontent = contenido_con_filtro(scontent, _ev_srv, f"{acc}:{status_id}")
                db_id = f"{acc}:{status_id}" if status_id else nid
                if nid in ya_en_lista:
                    filas.append((db_id, None, "", None, ndate, nid))
                    continue
                if _ocultar_nombres and fname:
                    from app.core.html_utils import quitar_emojis as _qe
                    fname = _qe(fname)
                pref = prefijo_notificacion_mastodon(ntype)
                if scontent:
                    # Mensaje COMPLETO en la columna Contenido (sin recortar).
                    t = scontent.replace('\n', ' ').strip()
                    contenido_str = f"{pref} {fuser}: {t}"
                elif ntype == "follow":
                    contenido_str = f"{pref} {fname} ({fuser}) comenzó a seguirte."
                elif ntype == "follow_request":
                    contenido_str = f"{pref} {fname} ({fuser}) solicitó seguirte."
                elif ntype == "poll":
                    contenido_str = f"{pref} Una encuesta ha finalizado."
                else:
                    contenido_str = f"{pref} de {fname} ({fuser})"
                fecha_rel = formatear_tiempo_relativo(ndate)
                vis_trad = visibilidad_map.get(svis, "") if svis else ""
                desc_sin_prefijo = _re_n.sub(r'^\[[^\]]*\]\s*', '', contenido_str)
                contenido_val = (scontent.replace('\n', ' ').strip() if scontent else desc_sin_prefijo)
                valores_dict = {
                    "type": pref.strip("[]"), "author": fname or "", "username": fuser or "",
                    "content": contenido_val or "", "date": fecha_rel or "", "visibility": vis_trad or "",
                }
                valores = [valores_dict.get(c, "") for c, _e in getattr(self, "_cols_actuales", [])]
                filas.append((db_id, valores, "", None, ndate, nid))
            if not order_by_desc:
                filas = filas[::-1]
            return filas

        es_timeline_toots = (
            self.active_timeline_type in ("home", "local", "public", "direct", "bookmarks", "favourites", "sent", "trending")
            or str(self.active_timeline_type).startswith("search:")
            or str(self.active_timeline_type).startswith("list:")
            or str(self.active_timeline_type).startswith("user:")
            or str(self.active_timeline_type).startswith("thread:"))
        if not es_timeline_toots:
            return filas
        sql = f"""
        SELECT s.id, s.author_name, s.author_username, s.content, s.created_at, s.visibility, s.favourited, s.reblogged, s.bookmarked, s.media_json, s.spoiler_text, s.reblog_by_name, s.reblog_by_username, s.application, s.quote_json, s.filtered_by, COALESCE(s.has_poll,0), COALESCE(s.edited_at,''), COALESCE(s.in_reply_to_id,''), COALESCE(s.replies_count,0), COALESCE(s.reblogs_count,0), COALESCE(s.favourites_count,0), COALESCE(s.card_json,''), COALESCE(s.pinned,0), COALESCE(s.language,'')
        FROM mastodon_statuses s
        JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id
        WHERE t.account_id = ? AND t.timeline = ?
        ORDER BY s.created_at {order_by} LIMIT {limite_render("mastodon")}
        """
        from app.core.content_filters import ContentFilterEngine
        filtros = ContentFilterEngine.cargar("mastodon")
        clientes_ignorados = set(config.get("mastodon", "ignored_clients", []))
        _filtrar_br = self.active_timeline_type in ("home", "local", "public")
        _show_boosts = config.get("mastodon", "show_boosts", True)
        _show_replies = config.get("mastodon", "show_replies", True)
        mostrar_usuario = config.get("accessibility", "show_screen_names", False)
        ocultar_emojis = config.get("accessibility", "hide_emojis", False)
        ocultar_emojis_nombres = config.get("accessibility", "hide_emojis_names", False)
        if ocultar_emojis or ocultar_emojis_nombres:
            from app.core.html_utils import quitar_emojis
        plantilla = config.get("accessibility", "mastodon_template", "De {author}. {content}. {date}. {visibility}. {indicators} {media}.")
        ya_en_lista = set(self.lista_items_db_ids)
        from app.services.mastodon.filtros_servidor import evaluar as _eval_filtro_srv, evento_media_filtrado
        from .mf_filtros_mastodon import componer_contenido_toot
        _expand_cw = config.get("accessibility", "expand_cw", False)
        _tl_actual = str(self.active_timeline_type or "")
        _foco_hilo = (_tl_actual.split(":", 1)[1] if _tl_actual.startswith("thread:") else None)
        for r in db.query(sql, (acc, self.active_timeline_type)):
            (db_id, author, author_username, text, date, vis, fav, rebl, book, media, spoiler_text,
             reblog_by_name, reblog_by_username, application, quote_json, filtered_by,
             has_poll, edited_at, in_reply_to_id, replies_count, reblogs_count, favourites_count,
             card_json, pinned, idioma_toot) = r
            if db_id in ya_en_lista:
                filas.append((db_id, None, "", None, date))
                continue
            if application and application in clientes_ignorados:
                continue
            if _filtrar_br:
                if not _show_boosts and reblog_by_name:
                    continue
                if not _show_replies and in_reply_to_id:
                    continue
            if filtros:
                autor_filtro = f"{author or ''} {author_username or ''} {reblog_by_name or ''}"
                if ContentFilterEngine.evaluar("mastodon", text or "", autor_filtro, filtros)["hide"]:
                    continue
            _ev_srv = _eval_filtro_srv(
                filtered_by, acc, _tl_actual,
                foco_detalle=bool(_foco_hilo) and str(db_id).endswith(":" + _foco_hilo))
            if _ev_srv["accion"] == "hide":
                continue
            # Mismo texto que la carga completa (filtro del servidor incluido).
            contenido = componer_contenido_toot(
                text, spoiler_text, reblog_by_username, author_username, quote_json, card_json,
                media, _ev_srv, db_id, expand_cw=_expand_cw, ocultar_emojis=ocultar_emojis,
                account_id=acc, timeline=_tl_actual)
            autor_columna = reblog_by_name if reblog_by_name else author
            if ocultar_emojis_nombres:
                autor_columna = quitar_emojis(autor_columna or "")
            fecha_rel = formatear_tiempo_relativo(date)
            vis_trad = visibilidad_map.get(vis, vis)
            indicadores = []
            if fav: indicadores.append(_("Favorito"))
            if rebl: indicadores.append(_("Boosteado"))
            if book: indicadores.append(_("Guardado"))
            if has_poll: indicadores.append(_("Encuesta"))
            if edited_at: indicadores.append(_("Editado"))
            if in_reply_to_id: indicadores.append(_("Respuesta"))
            if pinned: indicadores.append(_("Fijado"))
            adj_desc = ""
            if media:
                try:
                    att = json.loads(media)
                    if att:
                        adj_desc = _("1 archivo adjunto") if len(att) == 1 else _("{0} archivos adjuntos").format(len(att))
                except Exception:
                    pass
            valores_dict = {
                "author": autor_columna or "", "username": author_username or "", "content": contenido or "",
                "date": fecha_rel or "", "visibility": vis_trad or "", "indicators": " ".join(indicadores),
                "media": adj_desc, "application": application or "", "cw": (spoiler_text or "").strip(),
                "counts": _("{f} favoritos, {r} impulsos, {p} respuestas").format(
                    f=favourites_count, r=reblogs_count, p=replies_count),
                "language": idioma_toot or "",
            }
            valores = [valores_dict.get(c, "") for c, _e in getattr(self, "_cols_actuales", [])]
            filas.append((db_id, valores, "",
                          evento_media_filtrado(_ev_srv, evento_media_de_attachments(media), db_id), date))
        if not order_by_desc:
            filas = filas[::-1]
        return filas

    def _filas_rss_actual(self):
        """Devuelve (filas, metas_por_id) del buffer RSS activo, con el MISMO formato
        que _cargar_listado_artículos (sin la agrupación por fecha, que es cosmética),
        para la inserción incremental. filas = [(db_id, [cols], lectura)]."""
        filas = []
        metas_por_id = {}
        if not self.active_rss_account_id:
            return filas, metas_por_id
        sql = """
        SELECT a.id, f.title, a.title, a.published_at, a.read, a.favorite, a.has_media, COALESCE(a.read_later, 0), a.author
        FROM rss_articles a
        JOIN rss_feeds f ON a.feed_id = f.id
        WHERE a.account_id = ? AND COALESCE(a.deleted, 0) = 0
        """
        params = [self.active_rss_account_id]
        # Mismo filtro que la carga completa (antes no conocía carpetas inteligentes ni
        # etiquetas y en esos buffers entraban artículos ajenos al refrescar).
        from app.services.rss.rss_buffers import where_buffer
        extra, extra_params = where_buffer(self.active_timeline_type, self.active_rss_category_id,
                                           self.active_rss_feed_id)
        sql += extra
        params.extend(extra_params)
        order_by = "ORDER BY a.published_at DESC" if config.get("general", "newest_first", True) else "ORDER BY a.published_at ASC"
        sql += f" {order_by} LIMIT {int(config.get('rss', 'max_articles_list', 500))}"
        rows = db.query(sql, tuple(params))

        from app.core.content_filters import ContentFilterEngine
        filtros_rss = ContentFilterEngine.cargar("rss")
        for r in rows:
            db_id, feed_title, title, date, leido, fav, has_media, leer_tarde, autor = r
            if filtros_rss and ContentFilterEngine.evaluar("rss", title or "", autor or "", filtros_rss)["hide"]:
                continue
            fecha_rel = formatear_tiempo_relativo(str(date))
            estado = _("Leído") if leido else _("No leído")
            if fav:
                estado += ", " + _("Destacado")
            if leer_tarde:
                estado += ", " + _("Leer más tarde")
            meta = {
                "feed": feed_title, "title": title, "fecha": fecha_rel,
                "has_media": bool(has_media), "favorite": bool(fav), "read_later": bool(leer_tarde)
            }
            valores_dict = {"feed": feed_title or "", "title": title or "", "author": autor or "",
                            "date": fecha_rel, "flags": estado}
            valores = [valores_dict.get(c, "") for c, _e in getattr(self, "_cols_actuales", [])]
            filas.append((db_id, valores, "", None, str(date)))
            metas_por_id[db_id] = meta
        return filas, metas_por_id

    def _construir_lectura_rss(self, index: int, leido: bool, fav: bool) -> str:
        """Construye la cadena de lectura accesible de una fila de artículo RSS."""
        meta = getattr(self, "_rss_meta", {}).get(index)
        return self._lectura_rss_de_meta(meta, leido, fav)

    def _lectura_rss_de_meta(self, meta, leido: bool, fav: bool) -> str:
        """Igual que _construir_lectura_rss pero a partir de un dict de metadatos
        (para construir la lectura sin depender del índice; lo usa la inserción
        incremental)."""
        if not meta:
            return ""
        flags = []
        if not leido:
            flags.append(_("Sin leer"))
        if fav:
            flags.append(_("Destacado"))
        if meta.get("read_later"):
            flags.append(_("Leer más tarde"))
        if meta.get("has_media"):
            flags.append(_("Con audio o adjunto"))
        plantilla = config.get("accessibility", "rss_template", "{feed}: {title}. {date}. {flags}.")
        try:
            read_str = plantilla.format(
                feed=meta["feed"] or "",
                title=meta["title"] or "",
                date=meta["fecha"] or "",
                flags=", ".join(flags)
            )
            import re
            read_str = re.sub(r'\.+', '.', read_str)
            from app.core.plantillas import pulir_lectura
            read_str = pulir_lectura(read_str)
        except Exception as e:
            log.error(f"Error al renderizar plantilla RSS: {e}")
            read_str = f"{meta['feed']}: {meta['title']}. {meta['fecha']}. {', '.join(flags)}."
        return read_str

    def _refrescar_fila_rss(self, index: int):
        """Actualiza en sitio la columna Estado y la lectura de una fila RSS según la base de datos."""
        if index < 0 or index >= len(self.lista_items_db_ids):
            return
        db_id = self.lista_items_db_ids[index]
        row = db.query_one("SELECT read, favorite FROM rss_articles WHERE id = ?", (db_id,))
        if not row:
            return
        leido, fav = bool(row[0]), bool(row[1])
        # Reconstruir el texto de la columna "Estado" (flags) y ponerlo en su columna real.
        estado = _("Leído") if leido else _("No leído")
        if fav:
            estado += ", " + _("Destacado")
        meta = getattr(self, "_rss_meta", {}).get(index) or {}
        if meta.get("read_later"):
            estado += ", " + _("Leer más tarde")
        flags_col = self._col_de_campo(getattr(self, "_cols_actuales", []), "flags")
        if flags_col is not None:
            self.lista_items.SetItem(index, flags_col, estado)

    def _on_rss_refreshed(self, account_id):
        if account_id == self.active_rss_account_id:
            wx.CallAfter(self._cargar_listado_artículos)
        wx.CallAfter(self._actualizar_contadores_rss)

    # --- CHATS TELEGRAM ---
    def _cargar_listado_chats_telegram(self):
        self.lista_items.congelar_diferido()
        self.lista_items.DeleteAllItems()
        self.lista_items.limpiar_lecturas()
        self.lista_items.reset_focus_col()
        self.lista_items_db_ids.clear()

        if not self.active_telegram_account_id:
            return

        # El buffer de contactos se carga aparte (lista de contactos, con columnas propias).
        if self.active_timeline_type == "contacts":
            self._cargar_contactos_telegram()
            return

        # La plantilla "telegram_chat" define qué columnas tiene el buffer y en qué orden.
        cols = self._construir_columnas_plantilla("telegram_chat")
        col_fecha = self._col_de_campo(cols, "date")

        sql = """
        SELECT id, chat_id, title, chat_type, unread_count, last_message, last_message_date,
               COALESCE(muted,0), COALESCE(pinned,0), COALESCE(verified,0), COALESCE(protected,0)
        FROM telegram_chats WHERE account_id = ?
        """
        params = [self.active_telegram_account_id]
        if self.active_timeline_type == "unread":
            sql += " AND unread_count > 0"
        elif self.active_timeline_type in ("private", "bot", "group", "supergroup", "channel"):
            sql += " AND chat_type = ?"
            params.append(self.active_timeline_type)
        elif str(self.active_timeline_type).startswith("folder:"):
            # Filtrar por los chats incluidos en la carpeta
            folder_id = self.active_timeline_type.split(":", 1)[1]
            fila = db.query_one(
                "SELECT chat_ids_json FROM telegram_folders WHERE account_id = ? AND folder_id = ?",
                (self.active_telegram_account_id, folder_id)
            )
            ids = []
            if fila and fila[0]:
                try:
                    ids = json.loads(fila[0])
                except Exception:
                    ids = []
            if ids:
                placeholders = ",".join("?" for _ in ids)
                sql += f" AND chat_id IN ({placeholders})"
                params.extend(ids)
            else:
                sql += " AND 0"  # carpeta vacía
        sql += f" ORDER BY pinned DESC, last_message_date DESC LIMIT {limite_render('telegram', 'max_chats_list')}"

        rows = db.query(sql, tuple(params))
        tipo_labels = {
            "private": _("Privado"), "bot": _("Bot"), "group": _("Grupo"),
            "supergroup": _("Supergrupo"), "channel": _("Canal")
        }
        from app.core.plantillas import pulir_lectura, definicion
        from collections import defaultdict
        plantilla = config.get("accessibility", "telegram_chat", "") or definicion("telegram_chat")["default"]
        for idx, r in enumerate(rows):
            db_id, chat_id, title, chat_type, unread, last_msg, last_date, muted, pinned, verified, protected = r
            fecha_rel = formatear_tiempo_relativo(str(last_date)) if last_date else ""
            tipo = tipo_labels.get(chat_type, "")
            valores = {
                "chat": title or str(chat_id),
                "type": tipo,
                "content": (last_msg or "").replace("\n", " "),
                "date": fecha_rel,
                "unread": (_("{0} sin leer").format(unread) if unread else ""),
                "silenciado": _("Silenciado") if muted else "",
                "fijado": _("Fijado") if pinned else "",
                "verificado": _("Verificado") if verified else "",
                "protegido": _("Protegido") if protected else "",
            }
            pos = self._volcar_fila_plantilla(idx, cols, valores)
            self.lista_items_db_ids.append(f"{db_id}|{chat_id}|{title}")
            self.lista_items.establecer_lectura(pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            if col_fecha is not None and last_date:
                self.lista_items.establecer_fecha(pos, col_fecha, str(last_date), fecha_rel)

        if not rows:
            # Como Mastodon/Bluesky: si la caché de chats está VACÍA (nunca se descargó), traerla
            # SOLA, en silencio (sin pedir F5). Si hay chats pero ninguno coincide con este filtro
            # (p. ej. no tienes canales), no se dice nada: el usuario no quiere avisos en vacíos.
            total = db.query_one("SELECT COUNT(*) FROM telegram_chats WHERE account_id = ?",
                                 (self.active_telegram_account_id,))
            if not (total and total[0]):
                self._autocargar_chats_telegram()

        self._restaurar_foco_lista()

    def _autocargar_chats_telegram(self):
        """Descarga la lista de chats en segundo plano y en silencio si la caché está vacía
        (igual que Mastodon/Bluesky cargan sus buffers al arrancar). Evita solaparse."""
        if getattr(self, "_tg_cargando_chats", False):
            return
        acc = self.active_telegram_account_id
        if not acc:
            return
        self._tg_cargando_chats = True
        from app.services.telegram.telegram_service import telegram_service

        def _bg(ctx):
            return telegram_service.cargar_chats(acc, limit=200)

        def _finish(res, err):
            self._tg_cargando_chats = False
            if not err and self.active_telegram_account_id == acc:
                self._cargar_navegacion()
                self._cargar_listado_chats_telegram()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _pintar_contactos_telegram(self, contactos):
        """Muestra una lista de contactos en la lista principal."""
        if self.active_timeline_type != "contacts":
            return
        self.lista_items.DeleteAllItems()
        self.lista_items.limpiar_lecturas()
        self.lista_items_db_ids.clear()
        # Los contactos no tienen plantilla: columnas fijas propias (Nombre, Usuario, Teléfono).
        while self.lista_items.GetColumnCount() > 0:
            self.lista_items.DeleteColumn(0)
        self.lista_items.InsertColumn(0, _("Nombre"), width=240)
        self.lista_items.InsertColumn(1, _("Usuario"), width=200)
        self.lista_items.InsertColumn(2, _("Teléfono"), width=160)
        self._cols_actuales = [("display", _("Nombre")), ("username", _("Usuario")), ("phone", _("Teléfono"))]
        for idx, c in enumerate(contactos or []):
            nombre = c.get("nombre", "")
            usuario = c.get("username", "")
            telefono = c.get("telefono", "")
            pos = self.lista_items.InsertItem(idx, nombre)
            self.lista_items.SetItem(pos, 1, usuario)
            self.lista_items.SetItem(pos, 2, telefono)
            self.lista_items_db_ids.append(f"contacto|{c.get('id')}|{nombre}")
            self.lista_items.establecer_lectura(pos, ". ".join(p for p in [nombre, usuario, telefono] if p))
        self._restaurar_foco_lista()

    def _cargar_contactos_telegram(self, forzar: bool = False):
        """
        Muestra los contactos. Usa la caché local para no pedirlos en cada
        navegación (evita el error FLOOD_WAIT de Telegram). Solo descarga de la
        red si la caché está vacía o si se fuerza (F5).
        """
        cuenta = self.active_telegram_account_id
        if not cuenta:
            return
        cache = telegram_service.contactos_cacheados(cuenta)
        if cache and not forzar:
            self._pintar_contactos_telegram(cache)
            return

        speak(_("Descargando contactos..."), interrupt=True)

        def _bg(ctx):
            return telegram_service.obtener_contactos(cuenta)

        def _finish(res, err):
            if err:
                # Si hay caché, mostrarla pese al error de red
                if cache:
                    self._pintar_contactos_telegram(cache)
                speak(_("No se pudieron actualizar los contactos ahora: {0}").format(err), interrupt=True)
                return
            self._pintar_contactos_telegram(res)
            speak(_("{0} contactos.").format(len(res or [])) if res else _("No tienes contactos."), interrupt=True)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_anadir_contacto(self):
        """Añade un contacto de Telegram pidiendo identificador y nombre."""
        cuenta = self.active_telegram_account_id or self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self, _("Usuario (@usuario), teléfono o ID del contacto:"), _("Añadir contacto"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ident = dlg.GetValue().strip()
        dlg.Destroy()
        if not ident:
            return
        dlg2 = CustomTextEntryDialog(self, _("Nombre para mostrar:"), _("Añadir contacto"))
        nombre = dlg2.GetValue().strip() if dlg2.ShowModal() == wx.ID_OK else ""
        dlg2.Destroy()
        if not nombre:
            speak(_("Hace falta un nombre."), interrupt=True)
            return
        # Si parece un identificador numérico, pasarlo como entero.
        identificador = int(ident) if ident.lstrip("-").isdigit() else ident

        def _bg(ctx):
            return telegram_service.añadir_contacto(cuenta, identificador, nombre)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo añadir el contacto: {0}").format(err))
            else:
                if self.active_timeline_type == "contacts":
                    self._cargar_contactos_telegram(forzar=False)
                notificar(_("Contacto añadido."))

        speak(_("Añadiendo contacto..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_borrar_contacto(self, user_id, nombre=""):
        """Elimina un contacto de la agenda de Telegram, con confirmación."""
        cuenta = self.active_telegram_account_id or self._tg_cuenta_activa()
        if not cuenta:
            return
        dlg = wx.MessageDialog(
            self, _("¿Eliminar a {0} de tus contactos?").format(nombre or user_id),
            _("Eliminar contacto"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return

        def _bg(ctx):
            return telegram_service.borrar_contacto(cuenta, user_id)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo eliminar el contacto: {0}").format(err))
            else:
                if self.active_timeline_type == "contacts":
                    self._cargar_contactos_telegram(forzar=False)
                notificar(_("Contacto eliminado."))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_nota_contacto(self, user_id, nombre=""):
        """Pone (o quita) una nota privada en un contacto de Telegram."""
        cuenta = self.active_telegram_account_id or self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self, _("Nota privada sobre {0} (déjala vacía para quitarla):").format(nombre or user_id),
            _("Nota del contacto"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        nota = dlg.GetValue().strip()
        dlg.Destroy()

        def _bg(ctx):
            return telegram_service.nota_contacto(cuenta, user_id, nota)

        def _finish(res, err):
            if err:
                notificar(_("No se pudo guardar la nota: {0}").format(err))
            else:
                notificar(_("Nota quitada.") if not nota else _("Nota guardada."))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_importar_contactos(self):
        """Importa contactos en lote desde un archivo CSV (teléfono,nombre,apellido) o vCard (.vcf)."""
        cuenta = self.active_telegram_account_id or self._tg_cuenta_activa()
        if not cuenta:
            return
        with wx.FileDialog(
                self, _("Elige el archivo de contactos (CSV o vCard)"),
                wildcard=_("Contactos") + " (*.csv;*.vcf;*.txt)|*.csv;*.vcf;*.txt",
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fd:
            if fd.ShowModal() != wx.ID_OK:
                return
            ruta = fd.GetPath()
        contactos = self._leer_archivo_contactos(ruta)
        if not contactos:
            speak(_("No se han encontrado contactos válidos en el archivo. "
                    "Usa CSV con teléfono,nombre,apellido o un archivo vCard (.vcf)."), interrupt=True)
            return
        dlg = wx.MessageDialog(
            self, _("Se van a importar {0} contactos. ¿Continuar?").format(len(contactos)),
            _("Importar contactos"), wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return

        def _bg(ctx):
            return telegram_service.importar_contactos(cuenta, contactos)

        def _finish(n, err):
            if err:
                notificar(_("No se pudo importar: {0}").format(err))
            else:
                if self.active_timeline_type == "contacts":
                    self._cargar_contactos_telegram(forzar=False)
                notificar(_("{0} contactos importados.").format(n or 0))

        speak(_("Importando contactos..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _leer_archivo_contactos(self, ruta):
        """Lee contactos de un CSV (teléfono,nombre[,apellido]) o vCard. Devuelve [(tel, nombre, apellido)]."""
        contactos = []
        try:
            ext = os.path.splitext(ruta)[1].lower()
            with open(ruta, "r", encoding="utf-8", errors="replace") as f:
                contenido = f.read()
            if ext == ".vcf" or "BEGIN:VCARD" in contenido.upper():
                nombre = apellido = tel = ""
                for linea in contenido.splitlines():
                    L = linea.strip()
                    U = L.upper()
                    if U.startswith("BEGIN:VCARD"):
                        nombre = apellido = tel = ""
                    elif U.startswith("FN:"):
                        nombre = L[3:].strip()
                    elif U.startswith("N:") and not nombre:
                        partes = L[2:].split(";")
                        apellido = partes[0].strip() if partes else ""
                        nombre = partes[1].strip() if len(partes) > 1 else apellido
                    elif U.startswith("TEL"):
                        tel = L.split(":", 1)[1].strip() if ":" in L else ""
                    elif U.startswith("END:VCARD"):
                        if tel and nombre:
                            contactos.append((tel, nombre, apellido))
            else:
                import csv as _csv
                for fila in _csv.reader(contenido.splitlines()):
                    if not fila:
                        continue
                    tel = (fila[0] or "").strip()
                    nombre = (fila[1].strip() if len(fila) > 1 else "")
                    apellido = (fila[2].strip() if len(fila) > 2 else "")
                    # Saltar una posible cabecera.
                    if tel.lower() in ("phone", "telefono", "teléfono", "tel"):
                        continue
                    if tel and nombre:
                        contactos.append((tel, nombre, apellido))
        except Exception as e:
            log.debug(f"No se pudo leer el archivo de contactos: {e}")
        return contactos

    def _on_telegram_new_message(self, account_id, chat_id):
        # Al llegar un mensaje, actualizar la lista de chats. En chats activos llegan
        # muchos mensajes seguidos, así que COALESCEMOS: una sola actualización cada
        # ~800 ms como mucho (una consulta + un diff), no una por mensaje. El aviso por
        # sonido se sigue emitiendo por mensaje desde el servicio. (El evento llega del
        # hilo de Telegram → saltar al hilo de UI para coalescer.)
        if account_id != self.active_telegram_account_id:
            return
        wx.CallAfter(self._programar_refresco_chats_telegram)
        if config.get("telegram", "announce_new", True):
            wx.CallAfter(self._anunciar_mensaje_telegram, account_id, chat_id)

    def _on_telegram_message_edited(self, account_id, chat_id, message_id):
        # Una edición cambia el último mensaje del chat: refrescar la lista (coalescido).
        if account_id != self.active_telegram_account_id:
            return
        wx.CallAfter(self._programar_refresco_chats_telegram)

    def _on_telegram_messages_deleted(self, account_id, chat_id, ids):
        # Un borrado puede cambiar el último mensaje visible del chat: refrescar la lista.
        if account_id != self.active_telegram_account_id:
            return
        wx.CallAfter(self._programar_refresco_chats_telegram)

    def _anunciar_mensaje_telegram(self, account_id, chat_id):
        """Anuncia por voz el mensaje nuevo (remitente y texto), salvo los tuyos. SOLO en chats
        de USUARIO (privados/bots): nunca en grupos, supergrupos ni canales (serían un goteo
        constante). Con anti-saturación. Respeta 'Anunciar mensajes nuevos por voz'."""
        # Solo conversaciones individuales (con una persona o un bot), no grupales/canales.
        fila_tipo = db.query_one(
            "SELECT chat_type FROM telegram_chats WHERE account_id = ? AND chat_id = ?",
            (account_id, chat_id))
        if not fila_tipo or fila_tipo[0] not in ("private", "bot"):
            return
        import time as _t
        ahora = _t.time()
        if ahora - getattr(self, "_tg_ultimo_anuncio", 0) < 2:
            return
        self._tg_ultimo_anuncio = ahora
        row = db.query_one(
            "SELECT sender_name, text, outgoing, media_type FROM telegram_messages "
            "WHERE account_id = ? AND chat_id = ? ORDER BY message_id DESC LIMIT 1",
            (account_id, chat_id))
        if not row or row[2]:   # sin mensaje, o es tuyo (outgoing): no anunciar
            return
        sender, text, _out, media_type = row
        cuerpo = (text or "").strip() or (_("[{0}]").format(media_type) if media_type else "")
        if cuerpo:
            notificar(_("{0}: {1}").format(sender or _("Telegram"), cuerpo[:200]))

    def _programar_refresco_chats_telegram(self):
        """(Hilo de UI) Programa un único refresco de la lista de chats en ~800 ms,
        descartando los que lleguen en ese intervalo (coalescencia)."""
        if getattr(self, "_tg_refresco_programado", False):
            return
        self._tg_refresco_programado = True
        wx.CallLater(800, self._refrescar_chats_coalescido)

    def _refrescar_chats_coalescido(self):
        self._tg_refresco_programado = False
        if not self or not self.active_telegram_account_id:
            return
        self._actualizar_lista_chats(self._filas_telegram_actual())

    def _status_throttled(self, texto):
        """Muestra texto en la barra de estado como mucho cada ~400 ms (coalescido),
        mostrando siempre el más reciente. Evita inundar la UI con los avisos de
        'escribiendo…' y estados en línea de chats muy activos."""
        self._status_pendiente = texto
        if not getattr(self, "_status_flush_programado", False):
            self._status_flush_programado = True
            wx.CallAfter(self._iniciar_flush_status)

    def _iniciar_flush_status(self):
        wx.CallLater(400, self._flush_status)

    def _flush_status(self):
        self._status_flush_programado = False
        txt = getattr(self, "_status_pendiente", None)
        if txt is not None and self:
            try:
                self.statusbar.SetStatusText(txt, 0)
            except Exception:
                pass

    def _actualizar_barra_estado(self):
        """Pone en la barra de estado (campo 1) información ÚTIL del buffer activo y la posición en la
        lista: «<Red - Buffer> · elemento X de N». Es lo que se lee con «Leer barra de estado»
        (Ctrl+Mayús+B). Antes ese campo mostraba siempre 'Modo Lector de Pantalla Activo', que no
        aportaba nada. El campo 0 se reserva para estados puntuales (p. ej. 'escribiendo…' de Telegram)."""
        try:
            if not getattr(self, "statusbar", None) or not self.statusbar:
                return
        except Exception:
            return
        # Si el buffer activo NO es de Telegram, borrar el estado puntual (campo 0): el
        # 'escribiendo…' / 'en línea' de Telegram solo tiene sentido viendo Telegram. Esto se llama al
        # cambiar de buffer, así que el indicador deja de quedarse pegado en Bandeja/Mastodon/etc.
        try:
            if self._red_activa() != "telegram":
                self._limpiar_estado_puntual()
        except Exception:
            pass
        try:
            titulo = self.lbl_buffer_titulo.GetLabel().strip().rstrip(":").strip()
        except Exception:
            titulo = ""
        # El rótulo genérico ("Timeline / Artículos") no aporta contexto: se omite.
        if titulo.lower().startswith("timeline / art"):
            titulo = ""
        try:
            n = self.lista_items.GetItemCount()
            idx = self.lista_items.GetFocusedItem()
        except Exception:
            n, idx = 0, -1
        partes = []
        if titulo:
            partes.append(titulo)
        if n > 0:
            if idx is not None and idx >= 0:
                partes.append(_("elemento {0} de {1}").format(idx + 1, n))
            else:
                partes.append(_("{0} elementos").format(n))
        elif titulo:
            partes.append(_("vacío"))
        texto = " · ".join(partes) if partes else _("Sin buffer seleccionado")
        try:
            self.statusbar.SetStatusText(texto, 1)
        except Exception:
            pass

    def _limpiar_estado_puntual(self):
        """Borra el campo 0 de la barra de estado (estados puntuales de Telegram: 'escribiendo…',
        'en línea'). Se llama al cambiar a un buffer que NO es de Telegram y cuando caduca el
        'escribiendo…'. Sin esto, el texto se quedaba PEGADO al cambiar de buffer (p. ej. seguías
        viendo 'X está escribiendo…' en la Bandeja o en Mastodon mucho después)."""
        t = getattr(self, "_typing_timer", None)
        if t is not None:
            try:
                t.Stop()
            except Exception:
                pass
            self._typing_timer = None
        self._status_pendiente = ""
        try:
            if getattr(self, "statusbar", None) and self.statusbar:
                self.statusbar.SetStatusText("", 0)
        except Exception:
            pass

    def _programar_caducidad_typing(self):
        """Telegram no avisa de 'dejó de escribir'; se asume que tras ~6 s sin renovar paró. Programa
        (o reprograma) el borrado del 'escribiendo…' para que no quede pegado indefinidamente."""
        t = getattr(self, "_typing_timer", None)
        if t is not None:
            try:
                t.Stop()
            except Exception:
                pass
        try:
            self._typing_timer = wx.CallLater(6000, self._limpiar_estado_puntual)
        except Exception:
            self._typing_timer = None

    def _on_telegram_typing(self, account_id, chat_id, descripcion):
        """Muestra en la barra de estado que alguien está escribiendo/grabando. SOLO si estás viendo
        Telegram (la red activa); fuera de Telegram no tiene sentido y antes se quedaba pegado."""
        if self._red_activa() != "telegram" or account_id != self.active_telegram_account_id:
            return
        fila = db.query_one("SELECT title FROM telegram_chats WHERE id = ?", (f"{account_id}:{chat_id}",))
        titulo = fila[0] if fila else str(chat_id)
        self._status_throttled(_("{0} está {1}…").format(titulo, descripcion))
        self._programar_caducidad_typing()

    def _on_telegram_user_status(self, account_id, user_id, estado):
        """Muestra cambios de estado en línea de usuarios en la barra de estado (solo en Telegram)."""
        if self._red_activa() != "telegram" or account_id != self.active_telegram_account_id:
            return
        fila = db.query_one("SELECT title FROM telegram_chats WHERE id = ?", (f"{account_id}:{user_id}",))
        if fila:
            self._status_throttled(_("{0}: {1}").format(fila[0], estado))
            self._programar_caducidad_typing()

    def _abrir_conversacion_telegram(self, index):
        if index < 0 or index >= len(self.lista_items_db_ids):
            return
        try:
            _db_id, chat_id, title = self.lista_items_db_ids[index].split("|", 2)
            chat_id = int(chat_id)
        except (ValueError, AttributeError):
            return
        # Determinar el tipo de chat (para habilitar o no el chat de voz)
        fila = db.query_one("SELECT chat_type FROM telegram_chats WHERE id = ?",
                            (f"{self.active_telegram_account_id}:{chat_id}",))
        chat_type = fila[0] if fila else "private"
        from .telegram_views import TelegramConversationDialog
        dlg = TelegramConversationDialog(self, self.active_telegram_account_id, chat_id, title, chat_type=chat_type)
        dlg.ShowModal()
        dlg.Destroy()
        # Al cerrar, el estado de leído pudo cambiar (leídos hasta donde llegaste): refrescar la
        # lista de chats para que el contador de no leídos se actualice ya (la BD se marca al instante).
        try:
            wx.CallLater(400, self._cargar_listado_chats_telegram)
        except Exception:
            pass

    # --- SELECCIÓN DE DETALLES ---
