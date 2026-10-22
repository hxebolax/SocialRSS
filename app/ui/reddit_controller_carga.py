# -*- coding: utf-8 -*-
"""Controlador de Reddit: construcción de buffers, carga y render de listados (mixin).
"""
import json as _json
import logging
from datetime import datetime, timezone
from collections import defaultdict

from app.core.config_manager import config, limite_render
from app.core.database import db
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.core.task_manager import TareaDuplicadaError

from app.services.reddit import reddit_service

log = logging.getLogger(__name__)

# Columnas leídas de reddit_posts para construir cada fila (orden fijo, ver _valores_post).
_SQL_COLS_POST = ("p.id, p.subreddit, p.author, p.title, p.selftext, p.url, p.permalink, "
                  "p.created_utc, p.score, p.num_comments, p.over_18, p.spoiler, p.stickied, "
                  "p.locked, p.saved, p.likes, p.link_flair_text, p.is_self, p.is_gallery, "
                  "p.media_json, p.distinguished, p.domain, p.hidden")


class _CargaMixin:
    # Buffers cuyo contenido son publicaciones (posts).
    def _es_buffer_posts(self, tipo):
        return (tipo in ("home", "popular", "all", "submitted", "saved", "upvoted",
                         "downvoted", "hidden")
                or tipo.startswith(("sub:", "user:", "home:", "popular:", "all:", "search:", "multi:")))

    # Buffers de la bandeja de entrada (mensajes).
    def _es_buffer_inbox(self, tipo):
        return tipo in ("inbox", "unread", "mentions", "sent")

    # ------------------------------------------------------------------
    # Construcción del árbol
    # ------------------------------------------------------------------
    def construir_servicio(self, root):
        """Construye el nodo del servicio Reddit (llamado desde _cargar_navegacion)."""
        tree = self.f.tree_nav
        n_reddit = tree.AppendItem(root, _("Reddit"),
                                   data={"type": "category_root", "service": "reddit"})
        accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'reddit' AND active = 1 "
                        "ORDER BY position, id")
        for r_id, r_name in accs:
            n_acc = tree.AppendItem(n_reddit, r_name,
                                    data={"type": "account", "account_id": r_id, "service": "reddit"})
            buffer_order = list(config.get("reddit", "buffer_order", list(self._TODOS)))
            faltan = [b for b in self._TODOS if b not in buffer_order]
            if faltan:
                buffer_order.extend(faltan)
                config.set("reddit", "buffer_order", buffer_order)
            ocultos = config.get("reddit", "hidden_buffers", [])
            for b in buffer_order:
                if b in ocultos:
                    continue
                label = self.BUFFER_LABELS.get(b)
                if not label:
                    continue
                tree.AppendItem(n_acc, label, data={
                    "type": "reddit_timeline", "reddit_type": b,
                    "account_id": r_id, "service": "reddit", "base_label": label})
            # Subreddits fijados como buffer (grupo dinámico).
            for sub in config.get("reddit", "saved_subreddits", []):
                nombre = sub.get("name", "") if isinstance(sub, dict) else str(sub)
                if not nombre:
                    continue
                etiqueta = _("r/{0}").format(nombre)
                tree.AppendItem(n_acc, etiqueta, data={
                    "type": "reddit_timeline", "reddit_type": "sub:" + nombre,
                    "account_id": r_id, "service": "reddit", "base_label": etiqueta})
            # Búsquedas guardadas (grupo dinámico).
            if "searches" not in ocultos:
                for termino in config.get("reddit", "saved_searches", []):
                    etiqueta = _("Búsqueda: {0}").format(termino)
                    tree.AppendItem(n_acc, etiqueta, data={
                        "type": "reddit_timeline", "reddit_type": "search:" + termino,
                        "account_id": r_id, "service": "reddit", "base_label": etiqueta})
        return n_reddit

    def reordenar_buffer(self, data, subir):
        """Ctrl+Arriba/Abajo sobre un buffer de Reddit (fijos y dinámicos)."""
        b_type = data.get("reddit_type") or ""
        dinamicos = {
            "sub:": ("saved_subreddits",
                     lambda it, v: (it.get("name") if isinstance(it, dict) else it) == v),
            "search:": ("saved_searches", lambda it, v: it == v),
        }
        for prefijo, (clave_cfg, coincide) in dinamicos.items():
            if b_type.startswith(prefijo):
                valor = b_type.split(":", 1)[1]
                items = list(config.get("reddit", clave_cfg, []))
                pos = next((i for i, it in enumerate(items) if coincide(it, valor)), None)
                if pos is None:
                    return False
                nuevo = pos - 1 if subir else pos + 1
                if not (0 <= nuevo < len(items)):
                    return False
                items[pos], items[nuevo] = items[nuevo], items[pos]
                config.set("reddit", clave_cfg, items)
                return True
        buffer_order = list(config.get("reddit", "buffer_order", list(self._TODOS)))
        if b_type not in buffer_order:
            return False
        idx = buffer_order.index(b_type)
        new_idx = idx - 1 if subir else idx + 1
        if not (0 <= new_idx < len(buffer_order)):
            return False
        buffer_order[idx], buffer_order[new_idx] = buffer_order[new_idx], buffer_order[idx]
        config.set("reddit", "buffer_order", buffer_order)
        return True

    # ------------------------------------------------------------------
    # Selección
    # ------------------------------------------------------------------
    def on_select(self, item, data, t):
        f = self.f
        f.active_reddit_account_id = data.get("account_id")
        try:
            f._aplicar_visibilidad_detalles()
        except Exception:
            pass
        if t == "reddit_timeline":
            f.active_reddit_type = data.get("reddit_type")
            try:
                f.lbl_buffer_titulo.SetLabel("Reddit - " + f.tree_nav.GetItemText(item))
            except Exception:
                pass
            self.cargar_listado()
        else:
            try:
                f.lista_items.DeleteAllItems()
                f.lista_items.limpiar_lecturas()
                f.txt_detalles.Clear()
            except Exception:
                pass

    def cargar_listado(self):
        f = self.f
        if not f.active_reddit_account_id:
            return
        tipo = f.active_reddit_type
        if self._es_buffer_posts(tipo):
            self._cargar_posts(tipo)
            # Auto-carga silenciosa si el buffer está vacío (como Mastodon/Bluesky).
            if f.lista_items.GetItemCount() == 0:
                self.on_refresh(silencioso=True)
        elif tipo == "comments":
            self._cargar_comentarios("comments")
            if f.lista_items.GetItemCount() == 0:
                self.on_refresh(silencioso=True)
        elif self._es_buffer_inbox(tipo):
            self._cargar_mensajes(tipo)
            if f.lista_items.GetItemCount() == 0:
                self.on_refresh(silencioso=True)
        elif tipo == "subreddits":
            self._cargar_subreddits()
            if f.lista_items.GetItemCount() == 0:
                self.on_refresh(silencioso=True)
        elif tipo == "multireddits":
            self._cargar_multireddits()
            if f.lista_items.GetItemCount() == 0:
                self.on_refresh(silencioso=True)
        else:
            self._preparar_columnas_posts()
            f._modo_lista = "reddit_posts"
            speak(_("Este buffer estará disponible en una próxima tanda."), interrupt=True)

    # ------------------------------------------------------------------
    # Render de posts
    # ------------------------------------------------------------------
    def _preparar_columnas_posts(self):
        f = self.f
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        self._cols_posts = f._construir_columnas_plantilla("reddit_post")

    @staticmethod
    def _fecha_iso(created_utc):
        """Convierte el epoch UTC de Reddit a ISO-8601 (lo que espera formatear_tiempo_relativo)."""
        try:
            return datetime.fromtimestamp(float(created_utc or 0), timezone.utc).isoformat()
        except Exception:
            return ""

    def _indicadores_post(self, saved, likes, over_18, spoiler, stickied, locked, distinguished):
        ind = []
        if likes == 1:
            ind.append(_("Voto positivo"))
        elif likes == 0:
            ind.append(_("Voto negativo"))
        if saved:
            ind.append(_("Guardado"))
        if over_18:
            ind.append(_("+18"))
        if spoiler:
            ind.append(_("Spoiler"))
        if stickied:
            ind.append(_("Fijado"))
        if locked:
            ind.append(_("Bloqueado"))
        if distinguished:
            ind.append(_("Moderador") if distinguished == "moderator" else _("Destacado"))
        return " ".join(ind)

    def _valores_post(self, r):
        """Construye los valores POR CAMPO de un post (según _SQL_COLS_POST) para la plantilla
        reddit_post. Devuelve (db_id, valores, fecha_iso, media_ev, texto_filtro, contenido_crudo)."""
        from app.ui.main_frame import formatear_tiempo_relativo
        (db_id, subreddit, author, title, selftext, url, permalink, created_utc, score,
         num_comments, over_18, spoiler, stickied, locked, saved, likes, flair, is_self,
         is_gallery, media_json, distinguished, domain, hidden) = r

        # Cuerpo: en self-posts el texto; en enlaces, el dominio de destino.
        if is_self:
            cuerpo = (selftext or "").replace("\n", " ").strip()
        else:
            cuerpo = _("Enlace a {0}").format(domain) if domain else (url or "")

        # Descripción accesible de los adjuntos.
        partes_media = []
        try:
            for a in (_json.loads(media_json) if media_json else []):
                tp = (a.get("type") or "").lower()
                if tp == "gallery":
                    partes_media.append(_("Galería de {0} imágenes").format(a.get("count", 0)))
                elif tp == "video":
                    partes_media.append(_("Vídeo"))
                elif tp == "image":
                    partes_media.append(_("Imagen"))
        except Exception:
            pass
        media_desc = ". ".join(partes_media)

        fecha_iso = self._fecha_iso(created_utc)
        valores = {
            "subreddit": _("r/{0}").format(subreddit) if subreddit else "",
            "author": _("u/{0}").format(author) if author else "",
            "title": title or "",
            "content": cuerpo,
            "date": formatear_tiempo_relativo(fecha_iso) if fecha_iso else "",
            "score": str(score or 0),
            "comments": str(num_comments or 0),
            "indicators": self._indicadores_post(saved, likes, over_18, spoiler, stickied,
                                                 locked, distinguished),
            "media": media_desc,
            "flair": flair or "",
            "language": "",
        }
        texto_filtro = f"{subreddit or ''} {author or ''} {title or ''}"
        contenido = f"{title or ''}. {selftext or ''}"
        return db_id, valores, fecha_iso, None, texto_filtro, contenido

    def _consulta_posts(self, tipo, limite):
        return db.query(f"""
            SELECT {_SQL_COLS_POST}
            FROM reddit_posts p
            JOIN reddit_timeline_posts t ON p.id = t.post_db_id
            WHERE t.account_id = ? AND t.timeline = ?
            ORDER BY t.position ASC LIMIT ?
        """, (self.f.active_reddit_account_id, tipo, limite))

    def _filas_posts_actual(self, tipo):
        """Filas deseadas (db_id, [cols], lectura, media_ev, fecha) para inserción incremental."""
        f = self.f
        filas = []
        if not f.active_reddit_account_id:
            return filas
        from app.core.content_filters import ContentFilterEngine
        from app.core.plantillas import pulir_lectura, definicion
        filtros = ContentFilterEngine.cargar("reddit")
        plantilla = config.get("accessibility", "reddit_post", "") or definicion("reddit_post")["default"]
        cols = getattr(f, "_cols_actuales", None) or getattr(self, "_cols_posts", [])
        limite = limite_render("reddit", "max_items_buffer")
        rows = self._consulta_posts(tipo, limite)
        for r in rows:
            db_id, valores, fecha, media_ev, txt_filtro, contenido = self._valores_post(r)
            if filtros and ContentFilterEngine.evaluar("reddit", contenido, txt_filtro, filtros)["hide"]:
                continue
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura, media_ev, fecha))
        return filas

    def _cargar_posts(self, tipo):
        f = self.f
        f.lista_items.congelar_diferido()
        self._preparar_columnas_posts()
        f._modo_lista = "reddit_posts"
        from app.core.content_filters import ContentFilterEngine
        from app.core.plantillas import pulir_lectura, definicion
        cols = self._cols_posts
        filtros = ContentFilterEngine.cargar("reddit")
        plantilla = config.get("accessibility", "reddit_post", "") or definicion("reddit_post")["default"]
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)
        limite = limite_render("reddit", "max_items_buffer")
        rows = self._consulta_posts(tipo, limite)
        idx = 0
        for r in rows:
            db_id, valores, fecha, media_ev, txt_filtro, contenido = self._valores_post(r)
            if filtros and ContentFilterEngine.evaluar("reddit", contenido, txt_filtro, filtros)["hide"]:
                continue
            pos = f._volcar_fila_plantilla(idx, cols, valores)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            if media_ev:
                f.lista_items.establecer_medios(pos, media_ev)
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, valores["date"])
            idx += 1
        f._restaurar_foco_lista()

    # ------------------------------------------------------------------
    # Render de comentarios (buffer "Mis comentarios")
    # ------------------------------------------------------------------
    _SQL_COLS_COMMENT = ("c.id, c.subreddit, c.author, c.body, c.created_utc, c.score, c.saved, "
                         "c.likes, c.is_submitter, c.stickied, c.distinguished, c.permalink")

    def _valores_comment(self, r):
        from app.ui.main_frame import formatear_tiempo_relativo
        (db_id, subreddit, author, body, created_utc, score, saved, likes, is_submitter,
         stickied, distinguished, permalink) = r
        ind = []
        if likes == 1:
            ind.append(_("Voto positivo"))
        elif likes == 0:
            ind.append(_("Voto negativo"))
        if saved:
            ind.append(_("Guardado"))
        if is_submitter:
            ind.append(_("Autor original"))
        if stickied:
            ind.append(_("Fijado"))
        if distinguished:
            ind.append(_("Moderador") if distinguished == "moderator" else _("Destacado"))
        fecha_iso = self._fecha_iso(created_utc)
        valores = {
            "author": _("u/{0}").format(author) if author else "",
            "body": (body or "").replace("\n", " ").strip(),
            "date": formatear_tiempo_relativo(fecha_iso) if fecha_iso else "",
            "score": str(score or 0),
            "indicators": " ".join(ind),
            "subreddit": _("r/{0}").format(subreddit) if subreddit else "",
        }
        return db_id, valores, fecha_iso, (body or "")

    def _consulta_comentarios(self, tipo, limite):
        return db.query(f"""
            SELECT {self._SQL_COLS_COMMENT}
            FROM reddit_comments c
            JOIN reddit_timeline_comments t ON c.id = t.comment_db_id
            WHERE t.account_id = ? AND t.timeline = ?
            ORDER BY t.position ASC LIMIT ?
        """, (self.f.active_reddit_account_id, tipo, limite))

    def _cargar_comentarios(self, tipo):
        f = self.f
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        cols = f._construir_columnas_plantilla("reddit_comment")
        f._modo_lista = "reddit_comments"
        from app.core.plantillas import pulir_lectura, definicion
        plantilla = config.get("accessibility", "reddit_comment", "") or definicion("reddit_comment")["default"]
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)
        limite = limite_render("reddit", "max_items_buffer")
        idx = 0
        for r in self._consulta_comentarios(tipo, limite):
            db_id, valores, fecha, _crudo = self._valores_comment(r)
            pos = f._volcar_fila_plantilla(idx, cols, valores)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, valores["date"])
            idx += 1
        f._restaurar_foco_lista()

    def _filas_comentarios_actual(self, tipo):
        f = self.f
        filas = []
        if not f.active_reddit_account_id:
            return filas
        from app.core.plantillas import pulir_lectura, definicion
        plantilla = config.get("accessibility", "reddit_comment", "") or definicion("reddit_comment")["default"]
        cols = getattr(f, "_cols_actuales", None) or []
        for r in self._consulta_comentarios(tipo, limite_render("reddit", "max_items_buffer")):
            db_id, valores, fecha, _crudo = self._valores_comment(r)
            cols_vals = [valores.get(c, "") for c, _e in cols]
            lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            filas.append((db_id, cols_vals, lectura, None, fecha))
        return filas

    # ------------------------------------------------------------------
    # Render de mensajes (bandeja de entrada)
    # ------------------------------------------------------------------
    _WHERE_INBOX = {
        "inbox": "msg_type != 'sent'",
        "unread": "new = 1 AND msg_type != 'sent'",
        "mentions": "msg_type = 'username_mention'",
        "sent": "msg_type = 'sent'",
    }
    _TIPO_MSG = {
        "message": _("Mensaje privado"), "comment_reply": _("Respuesta a comentario"),
        "post_reply": _("Respuesta a publicación"), "username_mention": _("Mención"),
        "sent": _("Enviado"),
    }

    def _valores_mensaje(self, r):
        from app.ui.main_frame import formatear_tiempo_relativo
        (db_id, msg_type, author, subject, body, created_utc, new) = r
        fecha_iso = self._fecha_iso(created_utc)
        valores = {
            "type": self._TIPO_MSG.get(msg_type, msg_type or ""),
            "author": _("u/{0}").format(author) if author else "",
            "subject": subject or "",
            "content": (body or "").replace("\n", " ").strip(),
            "date": formatear_tiempo_relativo(fecha_iso) if fecha_iso else "",
        }
        if new:
            valores["type"] = _("(nuevo) ") + valores["type"]
        return db_id, valores, fecha_iso

    def _consulta_mensajes(self, tipo, limite):
        where = self._WHERE_INBOX.get(tipo, "msg_type != 'sent'")
        return db.query(f"""
            SELECT id, msg_type, author, subject, body, created_utc, new
            FROM reddit_messages WHERE account_id = ? AND {where}
            ORDER BY created_utc DESC LIMIT ?
        """, (self.f.active_reddit_account_id, limite))

    def _cargar_mensajes(self, tipo):
        f = self.f
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        cols = f._construir_columnas_plantilla("reddit_message")
        f._modo_lista = "reddit_messages"
        from app.core.plantillas import pulir_lectura, definicion
        plantilla = config.get("accessibility", "reddit_message", "") or definicion("reddit_message")["default"]
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)
        idx = 0
        for r in self._consulta_mensajes(tipo, limite_render("reddit", "max_notifications")):
            db_id, valores, fecha = self._valores_mensaje(r)
            pos = f._volcar_fila_plantilla(idx, cols, valores)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, fecha, valores["date"])
            idx += 1
        f._restaurar_foco_lista()

    def _filas_mensajes_actual(self, tipo):
        f = self.f
        filas = []
        from app.core.plantillas import pulir_lectura, definicion
        plantilla = config.get("accessibility", "reddit_message", "") or definicion("reddit_message")["default"]
        cols = getattr(f, "_cols_actuales", None) or []
        for r in self._consulta_mensajes(tipo, limite_render("reddit", "max_notifications")):
            db_id, valores, fecha = self._valores_mensaje(r)
            cols_vals = [valores.get(c, "") for c, _e in cols]
            filas.append((db_id, cols_vals, pulir_lectura(plantilla.format_map(defaultdict(str, valores))), None, fecha))
        return filas

    # ------------------------------------------------------------------
    # Render de subreddits suscritos
    # ------------------------------------------------------------------
    def _cargar_subreddits(self):
        f = self.f
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        cols = f._construir_columnas_plantilla("reddit_subreddit")
        f._modo_lista = "reddit_subreddits"
        from app.core.plantillas import pulir_lectura, definicion
        plantilla = config.get("accessibility", "reddit_subreddit", "") or definicion("reddit_subreddit")["default"]
        rows = db.query("SELECT id, name, title, subscribers, public_description FROM reddit_subreddits "
                        "WHERE account_id = ? AND user_is_subscriber = 1 ORDER BY name COLLATE NOCASE",
                        (f.active_reddit_account_id,))
        idx = 0
        for db_id, name, title, subs, desc in rows:
            valores = {"subreddit": _("r/{0}").format(name), "title": title or "",
                       "subscribers": str(subs or 0),
                       "description": (desc or "").replace("\n", " ").strip()}
            pos = f._volcar_fila_plantilla(idx, cols, valores)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            idx += 1
        f._restaurar_foco_lista()

    def _cargar_multireddits(self):
        f = self.f
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        # Lista de una columna (nombre del multireddit), con la ruta como db_id.
        while f.lista_items.GetColumnCount() > 0:
            f.lista_items.DeleteColumn(0)
        f.lista_items.InsertColumn(0, _("Multireddit"), width=300)
        f._cols_actuales = [("name", _("Multireddit"))]
        f._modo_lista = "reddit_multis"
        for m in config.get("reddit", "multireddits_cache", []):
            if isinstance(m, dict):
                visible = m.get("display") or m.get("name", "")
                owner, interno = m.get("owner", ""), m.get("name", "")
                # db_id navegable: "multi:<dueño>:<nombre>" (lo resuelve el timeline). Si falta el
                # dueño (caché antigua), se cae a la ruta para al menos abrirlo en el navegador.
                db_id = f"multi:{owner}:{interno}" if owner and interno else (m.get("path", "") or visible)
            else:
                visible, db_id = str(m), str(m)
            pos = f.lista_items.InsertItem(f.lista_items.GetItemCount(), visible)
            f.lista_items_db_ids.append(db_id)
            f.lista_items.establecer_lectura(pos, _("Multireddit: {0}").format(visible))
        f._restaurar_foco_lista()

    # ------------------------------------------------------------------
    # Descarga (manual y auto)
    # ------------------------------------------------------------------
    def on_refresh(self, silencioso=False):
        """Descarga el buffer activo. Con silencioso=True no anuncia inicio/fin (auto-carga)."""
        f = self.f
        tipo = f.active_reddit_type
        acc = f.active_reddit_account_id
        if not acc:
            return
        # Selecciona la función de repintado y la de descarga según el buffer.
        if self._es_buffer_posts(tipo):
            recargar, descargar = (lambda t: self._cargar_posts(t),
                                   lambda cb: reddit_service.cargar_timeline_async(acc, timeline_type=tipo, callback=cb))
        elif tipo == "comments":
            recargar, descargar = (lambda t: self._cargar_comentarios(t),
                                   lambda cb: reddit_service.cargar_mis_comentarios_async(acc, callback=cb))
        elif self._es_buffer_inbox(tipo):
            recargar, descargar = (lambda t: self._cargar_mensajes(t),
                                   lambda cb: reddit_service.cargar_inbox_async(acc, tipo, callback=cb))
        elif tipo == "subreddits":
            recargar, descargar = (lambda t: self._cargar_subreddits(),
                                   lambda cb: reddit_service.cargar_suscripciones_async(acc, callback=cb))
        elif tipo == "multireddits":
            recargar, descargar = (lambda t: self._cargar_multireddits(),
                                   lambda cb: reddit_service.cargar_multireddits_async(acc, callback=cb))
        else:
            if not silencioso:
                speak(_("Este buffer estará disponible en una próxima tanda."), interrupt=True)
            return
        ids_antes = set(getattr(f, "lista_items_db_ids", []) or [])

        def _cb(res, error):
            f._updating = False
            if isinstance(error, TareaDuplicadaError):
                if not silencioso:
                    speak(_("Esa actualización ya está en curso."), interrupt=False)
                return
            if error:
                if not silencioso:
                    notificar(_("Error al actualizar Reddit: {0}").format(error))
                event_bus.emit("sound_trigger", "reddit_error")
                return
            if silencioso:
                recargar(tipo)
            else:
                with f._foco_silencioso():
                    recargar(tipo)
                try:
                    f._anunciar_descarga(ids_antes)
                except Exception:
                    pass

        if not silencioso:
            speak(_("Actualizando Reddit..."), interrupt=True)
        f._updating = True
        descargar(_cb)

    def actualizar_incremental(self):
        """Sondeo silencioso del buffer de posts activo: si entran posts nuevos, los inserta sin
        mover el foco y emite un sonido."""
        f = self.f
        tipo = f.active_reddit_type
        acc = f.active_reddit_account_id
        if not acc:
            return
        es_posts = self._es_buffer_posts(tipo)
        es_comentarios = (tipo == "comments")
        es_inbox = self._es_buffer_inbox(tipo)
        if not (es_posts or es_comentarios or es_inbox):
            return
        if es_inbox:
            where = self._WHERE_INBOX.get(tipo, "msg_type != 'sent'")
            filas_fn = self._filas_mensajes_actual
            sonido = "reddit_new_message"
            descargar = lambda cb: reddit_service.cargar_inbox_async(acc, tipo, callback=cb)

            def _firma():
                r = db.query_one(f"SELECT COUNT(*) FROM reddit_messages WHERE account_id = ? AND {where}", (acc,))
                return r[0] if r else 0
        else:
            tabla = "reddit_timeline_posts" if es_posts else "reddit_timeline_comments"
            filas_fn = self._filas_posts_actual if es_posts else self._filas_comentarios_actual
            sonido = "reddit_new_status"
            if es_posts:
                descargar = lambda cb: reddit_service.cargar_timeline_async(acc, timeline_type=tipo, callback=cb)
            else:
                descargar = lambda cb: reddit_service.cargar_mis_comentarios_async(acc, callback=cb)

            def _firma():
                r = db.query_one(f"SELECT COUNT(*) FROM {tabla} WHERE account_id = ? AND timeline = ?",
                                 (acc, tipo))
                return r[0] if r else 0

        antes = _firma()

        def _cb(res, error):
            if error:
                return
            if _firma() != antes:
                try:
                    f._insertar_items_incremental(filas_fn(tipo))
                except Exception:
                    pass
                event_bus.emit("sound_trigger", sonido)

        descargar(_cb)

    def cargar_mas(self):
        """Carga una página más antigua del buffer activo (posts o comentarios; por 'after')."""
        f = self.f
        tipo = f.active_reddit_type
        acc = f.active_reddit_account_id
        es_posts = self._es_buffer_posts(tipo)
        es_comentarios = (tipo == "comments")
        if not acc or not (es_posts or es_comentarios):
            speak(_("Este buffer no admite cargar más."), interrupt=True)
            return
        speak(_("Cargando elementos más antiguos..."), interrupt=True)
        recargar = self._cargar_posts if es_posts else self._cargar_comentarios

        def _cb(res, error):
            if error and not isinstance(error, TareaDuplicadaError):
                notificar(_("Error al cargar más: {0}").format(error))
                return
            with f._foco_silencioso():
                recargar(tipo)
            if not res:
                speak(_("No hay más elementos anteriores."), interrupt=True)

        if es_posts:
            reddit_service.cargar_timeline_async(acc, timeline_type=tipo, cargar_mas=True, callback=_cb)
        else:
            reddit_service.cargar_mis_comentarios_async(acc, cargar_mas=True, callback=_cb)
