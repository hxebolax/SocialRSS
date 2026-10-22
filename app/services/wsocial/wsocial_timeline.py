# -*- coding: utf-8 -*-
"""Cronologías, feeds, hilos, perfiles y notificaciones de W Social.

Mixin de WSocialService. Cada buffer de la interfaz es un `timeline_type`:

    home            Inicio (app.bsky.feed.getTimeline)
    mentions        Menciones (notificaciones de tipo mención/respuesta/cita)
    notifications   Todas las notificaciones
    sent            Mis publicaciones (getAuthorFeed sobre mi propio DID)
    likes           Mis me gusta (getActorLikes)
    bookmarks       Mis marcadores (app.bsky.bookmark.getBookmarks)
    feed:<uri>      Un feed de terceros
    list:<uri>      La cronología de una lista
    author:<did>    Las publicaciones de una cuenta
    search:<texto>  Una búsqueda guardada
    thread:<uri>    Una conversación abierta como buffer
    quotes:<uri>    Quién ha citado una publicación

El contenido se cachea en `wsocial_posts` y la pertenencia a cada buffer en
`wsocial_timeline_posts`, de modo que la interfaz siempre tiene algo que mostrar aunque no haya
red, y el orden es el que devuelve el servidor (los feeds algorítmicos conservan su ranking).
"""
from app.core.models import WSocialPost
from app.services.wsocial.wsocial_common import (
    Any, Callable, Dict, List, Optional, db, json, log, models, task_manager,
    WSocialServiceError, _g, _ahora_iso, MOTIVOS_NOTIFICACION, uri_post_de_notificacion)


class _TimelineMixin:
    # ------------------------------------------------------------------
    # CARGA DE BUFFERS
    # ------------------------------------------------------------------
    def cargar_timeline_async(self, account_id: int, timeline_type: str = "home",
                              cargar_mas: bool = False, limit: int = 50,
                              callback: Optional[Callable] = None):
        """Descarga un buffer y lo guarda en la caché local.

        `cargar_mas` continúa por el cursor guardado (paginación hacia atrás) en vez de empezar
        de cero. El callback recibe la lista de elementos nuevos (o el error)."""
        clave_cursor = f"{account_id}:{timeline_type}"

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cursor = self._cursors.get(clave_cursor) if cargar_mas else None
            yo = self._yo(account_id)

            if timeline_type == "notifications":
                return self._cargar_notificaciones(account_id, client, cursor, limit, clave_cursor)
            if timeline_type == "mentions":
                return self._cargar_menciones(account_id, client, cursor, limit, clave_cursor)
            if timeline_type.startswith("thread:"):
                return self._cargar_hilo_como_buffer(
                    account_id, client, timeline_type, clave_cursor)

            # --- Buffers de publicaciones ---
            if timeline_type == "home":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_timeline(
                        {"limit": limit, "cursor": cursor}))
                items = _g(resp, "feed", default=[]) or []
            elif timeline_type == "sent":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_author_feed(
                        {"actor": yo["did"], "limit": limit, "cursor": cursor}))
                items = _g(resp, "feed", default=[]) or []
            elif timeline_type == "likes":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_actor_likes(
                        {"actor": yo["did"], "limit": limit, "cursor": cursor}))
                items = _g(resp, "feed", default=[]) or []
            elif timeline_type == "bookmarks":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.bookmark.get_bookmarks(
                        {"limit": limit, "cursor": cursor}))
                # Cada marcador envuelve la publicación en 'item'.
                items = []
                for b in (_g(resp, "bookmarks", default=[]) or []):
                    item = _g(b, "item", default=None) or b
                    items.append(item)
            elif timeline_type.startswith("feed:"):
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_feed(
                        {"feed": timeline_type[5:], "limit": limit, "cursor": cursor}))
                items = _g(resp, "feed", default=[]) or []
            elif timeline_type.startswith("list:"):
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_list_feed(
                        {"list": timeline_type[5:], "limit": limit, "cursor": cursor}))
                items = _g(resp, "feed", default=[]) or []
            elif timeline_type.startswith("author:"):
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_author_feed(
                        {"actor": timeline_type[7:], "limit": limit, "cursor": cursor,
                         "filter": "posts_with_replies"}))
                items = _g(resp, "feed", default=[]) or []
            elif timeline_type.startswith("search:"):
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.search_posts(
                        {"q": timeline_type[7:], "limit": limit, "cursor": cursor}))
                items = _g(resp, "posts", default=[]) or []
            elif timeline_type.startswith("quotes:"):
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_quotes(
                        {"uri": timeline_type[7:], "limit": limit, "cursor": cursor}))
                items = _g(resp, "posts", default=[]) or []
            else:
                raise WSocialServiceError(f"Buffer desconocido: {timeline_type}")

            nuevo_cursor = _g(resp, "cursor", default=None)
            self._cursors[clave_cursor] = nuevo_cursor
            self._sin_mas_paginas(clave_cursor, not nuevo_cursor or not items)

            # Toda la página se escribe en UNA transacción: mucho más rápido y la lista nunca
            # se queda a medio poblar si algo falla por el camino.
            posts = []
            with db.transaccion() as conn:
                base = self._posicion_siguiente(account_id, timeline_type, conn) if cargar_mas else 0
                for i, item in enumerate(items):
                    post = self._procesar_y_guardar_post(account_id, item, conn=conn)
                    if not post:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO wsocial_timeline_posts "
                        "(account_id, timeline, post_db_id, position) VALUES (?,?,?,?)",
                        (account_id, timeline_type, post.id, base + i))
                    posts.append(post)
            return posts

        self._tarea(_bg, callback)

    @staticmethod
    def _posicion_siguiente(account_id: int, timeline_type: str, conn=None) -> int:
        """Siguiente posición libre de un buffer (para añadir al final al paginar)."""
        sql = ("SELECT COALESCE(MAX(position), -1) + 1 FROM wsocial_timeline_posts "
               "WHERE account_id = ? AND timeline = ?")
        if conn is not None:
            fila = conn.execute(sql, (account_id, timeline_type)).fetchone()
        else:
            fila = db.query_one(sql, (account_id, timeline_type))
        return (fila[0] if fila else 0) or 0

    def _sin_mas_paginas(self, clave: str, agotado: bool):
        """Recuerda si un buffer ya no tiene más páginas, para que la interfaz no ofrezca
        'cargar más' cuando no queda nada."""
        if agotado:
            self._agotados.add(clave)
        else:
            self._agotados.discard(clave)

    def hay_mas_paginas(self, account_id: int, timeline_type: str) -> bool:
        """False si el servidor ya dijo que no hay más contenido antiguo en ese buffer."""
        return f"{account_id}:{timeline_type}" not in self._agotados

    # ------------------------------------------------------------------
    # NOTIFICACIONES
    # ------------------------------------------------------------------
    def _cargar_notificaciones(self, account_id, client, cursor, limit, clave_cursor):
        """Descarga notificaciones y las guarda. Devuelve la lista de dicts."""
        resp = self._get_con_reintentos(
            account_id, lambda: client.app.bsky.notification.list_notifications(
                {"limit": limit, "cursor": cursor}))
        nuevo_cursor = _g(resp, "cursor", default=None)
        self._cursors[clave_cursor] = nuevo_cursor
        notifs = _g(resp, "notifications", default=[]) or []
        self._sin_mas_paginas(clave_cursor, not nuevo_cursor or not notifs)
        salida = []
        with db.transaccion() as conn:
            for n in notifs:
                d = self._procesar_y_guardar_notificacion(account_id, n, conn=conn)
                if d:
                    salida.append(d)
        # Fuera de la transacción (nunca red dentro de una escritura): completar el texto de las
        # publicaciones aludidas que no estaban en la caché.
        self._completar_textos_notificaciones(account_id, client, salida)
        return salida

    def _completar_textos_notificaciones(self, account_id, client, notifs):
        """Descarga las publicaciones aludidas cuyo texto falta (en "me gusta" y repost la
        publicación es tuya y puede no estar cacheada). getPosts admite 25 URIs por llamada."""
        pendientes = []
        for d in notifs:
            su = (d or {}).get("subject_uri") or ""
            if su and not (d or {}).get("texto") and su not in pendientes:
                pendientes.append(su)
        if not pendientes:
            return
        for i in range(0, len(pendientes), 25):
            try:
                resp = self._get_con_reintentos(
                    account_id,
                    lambda lote=pendientes[i:i + 25]: client.app.bsky.feed.get_posts({"uris": lote}))
            except Exception as e:
                log.debug(f"No se pudieron resolver publicaciones de notificaciones: {e}")
                continue
            textos = {}
            with db.transaccion() as conn:
                for pv in (_g(resp, "posts", default=[]) or []):
                    post = self._procesar_y_guardar_post(account_id, pv, conn=conn)
                    if post:
                        textos[post.uri] = post.content or ""
                for d in notifs:
                    su = (d or {}).get("subject_uri") or ""
                    if textos.get(su):
                        d["texto"] = textos[su]
                        conn.execute("UPDATE wsocial_notifications SET texto = ? WHERE id = ?",
                                     (textos[su], d["id"]))

    def _cargar_menciones(self, account_id, client, cursor, limit, clave_cursor):
        """Buffer de MENCIONES: las notificaciones que te interpelan (mención, respuesta o cita).
        Además de guardarlas como notificación, se descarga la publicación que las origina para
        poder actuar sobre ella (responder, citar, me gusta...) como en cualquier otro buffer."""
        resp = self._get_con_reintentos(
            account_id, lambda: client.app.bsky.notification.list_notifications(
                {"limit": limit, "cursor": cursor}))
        nuevo_cursor = _g(resp, "cursor", default=None)
        self._cursors[clave_cursor] = nuevo_cursor
        notifs = [n for n in (_g(resp, "notifications", default=[]) or [])
                  if _g(n, "reason", default="") in ("mention", "reply", "quote")]
        self._sin_mas_paginas(clave_cursor, not nuevo_cursor)
        uris = [u for u in (_g(n, "uri", default="") for n in notifs) if u]
        if not uris:
            return []
        posts = []
        # getPosts admite 25 URIs por llamada.
        for i in range(0, len(uris), 25):
            resp_posts = self._get_con_reintentos(
                account_id, lambda lote=uris[i:i + 25]: client.app.bsky.feed.get_posts({"uris": lote}))
            posts.extend(_g(resp_posts, "posts", default=[]) or [])
        salida = []
        with db.transaccion() as conn:
            base = self._posicion_siguiente(account_id, "mentions", conn) if cursor else 0
            for i, p in enumerate(posts):
                post = self._procesar_y_guardar_post(account_id, p, conn=conn)
                if not post:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO wsocial_timeline_posts "
                    "(account_id, timeline, post_db_id, position) VALUES (?,?,?,?)",
                    (account_id, "mentions", post.id, base + i))
                salida.append(post)
        return salida

    def _procesar_y_guardar_notificacion(self, account_id: int, n, conn=None) -> Dict[str, Any]:
        """Guarda una notificación en la caché y devuelve su versión legible."""
        def _ej(_sql, _params=()):
            if conn is not None:
                conn.execute(_sql, _params)
            else:
                db.execute(_sql, _params)
        try:
            uri = _g(n, "uri", default="")
            if not uri:
                return {}
            autor = _g(n, "author")
            razon = _g(n, "reason", default="")
            record = _g(n, "record", default=None)
            reason_subject = _g(n, "reason_subject", "reasonSubject", default="")
            # A QUÉ publicación se refiere el aviso: el `uri` de la notificación es el registro
            # que la genera (el "me gusta", el repost...), no el post, y pedir su hilo devuelve
            # 400 NotFound. Ver uri_post_de_notificacion.
            subject_uri = uri_post_de_notificacion(razon, uri, reason_subject, record)
            texto = _g(record, "text", default="") or ""
            if not texto and subject_uri:
                # Me gusta / repost: la publicación es TUYA; su texto está en la caché (y si no,
                # lo completa después _completar_textos_notificaciones).
                fila = db.query_one("SELECT content FROM wsocial_posts WHERE id = ?",
                                    (f"{account_id}:{subject_uri}",))
                texto = (fila[0] or "") if fila else ""
            db_id = f"{account_id}:{uri}:{razon}"
            datos = {
                "id": db_id,
                "uri": uri,
                "subject_uri": subject_uri,
                "reason": razon,
                "reason_legible": MOTIVOS_NOTIFICACION.get(razon, razon),
                "author_handle": _g(autor, "handle", default=""),
                "author_name": _g(autor, "display_name", "displayName", default="") or _g(autor, "handle", default=""),
                "author_did": _g(autor, "did", default=""),
                "texto": texto,
                "is_read": bool(_g(n, "is_read", "isRead", default=False)),
                "indexed_at": _g(n, "indexed_at", "indexedAt", default=""),
            }
            _ej("""INSERT OR REPLACE INTO wsocial_notifications
                   (id, account_id, uri, cid, reason, reason_subject, subject_uri, author_did,
                    author_handle, author_name, texto, is_read, indexed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (db_id, account_id, uri, _g(n, "cid", default=""), razon,
                 reason_subject, subject_uri, datos["author_did"],
                 datos["author_handle"], datos["author_name"], texto,
                 1 if datos["is_read"] else 0, datos["indexed_at"]))
            return datos
        except Exception as e:
            log.debug(f"No se pudo guardar una notificación de W Social: {e}")
            return {}

    def contar_no_leidas_async(self, account_id: int, callback: Optional[Callable] = None):
        """Número de notificaciones sin leer (para el contador del árbol)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.notification.get_unread_count()
            return _g(resp, "count", default=0) or 0
        self._tarea(_bg, callback)

    def marcar_notificaciones_vistas_async(self, account_id: int, callback: Optional[Callable] = None):
        """Marca todas las notificaciones como vistas en el servidor y en la caché."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.notification.update_seen({"seen_at": _ahora_iso()})
            db.execute("UPDATE wsocial_notifications SET is_read = 1 WHERE account_id = ?", (account_id,))
            return True
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # PUBLICACIONES: EXTRACCIÓN Y GUARDADO
    # ------------------------------------------------------------------
    def _extraer_media(self, embed) -> List[Dict[str, Any]]:
        """Imágenes, vídeo y galerías de un embed hidratado, en una forma común para la interfaz
        (tipo, url, texto alternativo, tamaño). Recorre también el caso 'contenido citado con
        multimedia', donde la media cuelga de embed.media."""
        salida: List[Dict[str, Any]] = []
        if embed is None:
            return salida
        tipo = str(_g(embed, "py_type", "$type", default="")).lower()
        if "recordwithmedia" in tipo:
            return self._extraer_media(_g(embed, "media", default=None))
        if "images" in tipo or "gallery" in tipo:
            for img in (_g(embed, "images", default=[]) or []):
                salida.append({
                    "type": "image",
                    "url": _g(img, "fullsize", default="") or _g(img, "thumb", default=""),
                    "thumb": _g(img, "thumb", default=""),
                    "alt": _g(img, "alt", default="") or "",
                    "aspect": _g(img, "aspect_ratio", "aspectRatio", default=None),
                })
        elif "video" in tipo:
            salida.append({
                "type": "video",
                "url": _g(embed, "playlist", default="") or "",
                "thumb": _g(embed, "thumbnail", default="") or "",
                "alt": _g(embed, "alt", default="") or "",
                "aspect": _g(embed, "aspect_ratio", "aspectRatio", default=None),
            })
        return salida

    def _extraer_external(self, embed) -> Optional[Dict[str, Any]]:
        """Tarjeta de enlace (título, descripción y miniatura) de un embed, si la hay."""
        if embed is None:
            return None
        tipo = str(_g(embed, "py_type", "$type", default="")).lower()
        if "recordwithmedia" in tipo:
            return self._extraer_external(_g(embed, "media", default=None))
        if "external" not in tipo:
            return None
        ext = _g(embed, "external", default=None)
        if ext is None:
            return None
        return {
            "url": _g(ext, "uri", default=""),
            "title": _g(ext, "title", default=""),
            "description": _g(ext, "description", default=""),
            "thumb": _g(ext, "thumb", default=""),
        }

    def _procesar_y_guardar_post(self, account_id: int, feed_item, conn=None) -> Optional[WSocialPost]:
        """Procesa una publicación (venga como FeedViewPost o como PostView), la guarda en la
        caché e indexa su texto para la búsqueda.

        Con `conn` (de db.transaccion()) escribe por esa conexión sin confirmar, para que el
        llamante agrupe toda la página en una sola transacción."""
        def _ej(_sql, _params=()):
            if conn is not None:
                conn.execute(_sql, _params)
            else:
                db.execute(_sql, _params)

        def _q1(_sql, _params=()):
            if conn is not None:
                return conn.execute(_sql, _params).fetchone()
            return db.query_one(_sql, _params)

        try:
            post = _g(feed_item, "post", default=None) or feed_item
            reason = _g(feed_item, "reason", default=None)
            uri = _g(post, "uri")
            cid = _g(post, "cid")
            if not uri:
                return None

            autor = _g(post, "author")
            author_did = _g(autor, "did", default="")
            author_handle = _g(autor, "handle", default="")
            author_name = _g(autor, "display_name", "displayName", default="") or author_handle
            # Verificación de cuenta: W destaca a los usuarios verificados y conviene poder
            # mostrarlo (el lexicon la entrega en el estado de verificación del actor).
            estado_ver = _g(autor, "verification", default=None)
            verificado = 1 if str(_g(estado_ver, "verified_status", "verifiedStatus",
                                     default="")).lower() == "valid" else 0

            record = _g(post, "record")
            content = _g(record, "text", default="") or ""
            created_at = _g(record, "created_at", "createdAt", default="") or ""
            indexed_at = _g(post, "indexed_at", "indexedAt", default="") or created_at
            langs_list = _g(record, "langs", default=None) or []
            langs = ",".join(langs_list) if langs_list else ""

            # Etiquetas: las del propio autor MÁS las que añaden los servicios de etiquetado
            # suscritos. Una etiqueta con neg=True retira la marca.
            etiquetas = []
            rec_labels = _g(record, "labels", default=None)
            for v in (_g(rec_labels, "values", default=None) or []):
                val = _g(v, "val", default="")
                if val and val not in etiquetas:
                    etiquetas.append(val)
            for origen in (_g(post, "labels", default=None) or []) + list(_g(autor, "labels", default=None) or []):
                val = _g(origen, "val", default="")
                if not val:
                    continue
                if _g(origen, "neg", default=False):
                    if val in etiquetas:
                        etiquetas.remove(val)
                elif val not in etiquetas:
                    etiquetas.append(val)
            labels = ",".join(etiquetas)

            reply = _g(record, "reply", default=None)
            reply_root_uri = _g(_g(reply, "root"), "uri", default="")
            reply_root_cid = _g(_g(reply, "root"), "cid", default="")
            reply_parent_uri = _g(_g(reply, "parent"), "uri", default="")
            reply_parent_cid = _g(_g(reply, "parent"), "cid", default="")
            # A quién responde: autor del padre hidratado (FeedViewPost) o, en un hilo propio,
            # el mismo autor (se deduce del DID de la URI del padre).
            from app.core.at_lectura import autor_del_padre
            reply_parent_handle, reply_parent_name = (
                autor_del_padre(feed_item, reply_parent_uri, author_did, author_handle, author_name)
                if reply_parent_uri else ("", ""))

            db_id = f"{account_id}:{uri}"
            viewer = _g(post, "viewer", default=None)
            liked_uri = _g(viewer, "like", default="") or ""
            reposted_uri = _g(viewer, "repost", default="") or ""
            bookmarked = 1 if _g(viewer, "bookmarked", default=False) else 0

            # Atajo anti-tirones: si la publicación ya está cacheada Y su hash de contenido no ha
            # cambiado, basta con refrescar contadores y estado; nos ahorramos volver a extraer
            # multimedia, tarjeta y enlaces, que es lo caro. El hash importa: en AT Protocol un
            # registro puede reemplazarse conservando la URI.
            previo = _q1("SELECT content, cid FROM wsocial_posts WHERE id = ?", (db_id,))
            if previo is not None and (previo[0] or "") and cid and (previo[1] or "") == cid:
                try:
                    _ej("UPDATE wsocial_posts SET like_count=?, repost_count=?, reply_count=?, "
                        "quote_count=?, liked_uri=?, reposted_uri=?, bookmarked=?, indexed_at=?, "
                        "verificado=?, "
                        "reply_parent_handle=CASE WHEN ?<>'' THEN ? ELSE reply_parent_handle END, "
                        "reply_parent_name=CASE WHEN ?<>'' THEN ? ELSE reply_parent_name END "
                        "WHERE id=?",
                        (_g(post, "like_count", "likeCount", default=0) or 0,
                         _g(post, "repost_count", "repostCount", default=0) or 0,
                         _g(post, "reply_count", "replyCount", default=0) or 0,
                         _g(post, "quote_count", "quoteCount", default=0) or 0,
                         liked_uri, reposted_uri, bookmarked, indexed_at, verificado,
                         reply_parent_handle, reply_parent_handle,
                         reply_parent_name, reply_parent_name, db_id))
                except Exception as e:
                    log.debug(f"No se pudieron refrescar los contadores de {db_id}: {e}")
                return WSocialPost(id=db_id, account_id=account_id, uri=uri, cid=cid or "",
                                   author_did=author_did, author_handle=author_handle,
                                   author_name=author_name, content=previo[0] or "",
                                   created_at=created_at, indexed_at=indexed_at,
                                   reply_root_uri=reply_root_uri, reply_root_cid=reply_root_cid,
                                   reply_parent_uri=reply_parent_uri,
                                   reply_parent_cid=reply_parent_cid,
                                   verificado=bool(verificado))

            embed = _g(post, "embed", default=None)
            media = self._extraer_media(embed)
            external = self._extraer_external(embed)
            quote_uri = ""
            tipo_embed = str(_g(embed, "py_type", "$type", default="")).lower()
            if "record" in tipo_embed:
                rec = _g(embed, "record")
                quote_uri = _g(rec, "uri", default="") or _g(_g(rec, "record"), "uri", default="")

            repost_by_name = repost_by_handle = ""
            if reason and "repost" in str(_g(reason, "py_type", "$type", default="")).lower():
                by = _g(reason, "by")
                repost_by_handle = _g(by, "handle", default="")
                repost_by_name = _g(by, "display_name", "displayName", default="") or repost_by_handle

            # Enlaces: la tarjeta más los que aparezcan en el texto (con sus facets, que es donde
            # viven de verdad los enlaces con texto visible distinto de la URL).
            urls = []
            if external and external.get("url"):
                urls.append(external["url"])
            for facet in (_g(record, "facets", default=[]) or []):
                for feature in (_g(facet, "features", default=[]) or []):
                    u = _g(feature, "uri", default="")
                    if u and u not in urls:
                        urls.append(u)
            from app.services.bluesky.bluesky_facets import detectar_facets
            for f in detectar_facets(content):
                if f["tipo"] == "link" and f["valor"] not in urls:
                    urls.append(f["valor"])

            _ej("""
            INSERT INTO wsocial_posts (
                id, account_id, uri, cid, author_did, author_handle, author_name, content,
                created_at, indexed_at, langs, labels, reply_root_uri, reply_root_cid,
                reply_parent_uri, reply_parent_cid, quote_uri, like_count, repost_count,
                reply_count, quote_count, liked_uri, reposted_uri, bookmarked, media_json,
                external_json, urls_json, repost_by_name, repost_by_handle, verificado,
                reply_parent_handle, reply_parent_name
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                cid=excluded.cid, content=excluded.content, indexed_at=excluded.indexed_at,
                labels=excluded.labels, langs=excluded.langs, quote_uri=excluded.quote_uri,
                like_count=excluded.like_count, repost_count=excluded.repost_count,
                reply_count=excluded.reply_count, quote_count=excluded.quote_count,
                liked_uri=excluded.liked_uri, reposted_uri=excluded.reposted_uri,
                bookmarked=excluded.bookmarked, media_json=excluded.media_json,
                external_json=excluded.external_json, urls_json=excluded.urls_json,
                repost_by_name=excluded.repost_by_name, repost_by_handle=excluded.repost_by_handle,
                verificado=excluded.verificado,
                reply_parent_handle=COALESCE(NULLIF(excluded.reply_parent_handle,''),
                                             wsocial_posts.reply_parent_handle),
                reply_parent_name=COALESCE(NULLIF(excluded.reply_parent_name,''),
                                           wsocial_posts.reply_parent_name)
            """, (db_id, account_id, uri, cid or "", author_did, author_handle, author_name,
                  content, created_at, indexed_at, langs, labels, reply_root_uri, reply_root_cid,
                  reply_parent_uri, reply_parent_cid, quote_uri,
                  _g(post, "like_count", "likeCount", default=0) or 0,
                  _g(post, "repost_count", "repostCount", default=0) or 0,
                  _g(post, "reply_count", "replyCount", default=0) or 0,
                  _g(post, "quote_count", "quoteCount", default=0) or 0,
                  liked_uri, reposted_uri, bookmarked,
                  json.dumps(media, ensure_ascii=False),
                  json.dumps(external, ensure_ascii=False) if external else "",
                  json.dumps(urls, ensure_ascii=False), repost_by_name, repost_by_handle,
                  verificado, reply_parent_handle, reply_parent_name))

            # Índice de búsqueda (se reescribe para que no queden versiones viejas del texto).
            try:
                _ej("DELETE FROM wsocial_posts_fts WHERE post_db_id = ?", (db_id,))
                _ej("INSERT INTO wsocial_posts_fts (post_db_id, author_name, author_handle, content) "
                    "VALUES (?,?,?,?)", (db_id, author_name, author_handle, content))
            except Exception as e:
                log.debug(f"No se pudo indexar la publicación {db_id}: {e}")

            return WSocialPost(
                id=db_id, account_id=account_id, uri=uri, cid=cid or "", author_did=author_did,
                author_handle=author_handle, author_name=author_name, content=content,
                created_at=created_at, indexed_at=indexed_at, langs=langs, labels=labels,
                reply_root_uri=reply_root_uri, reply_root_cid=reply_root_cid,
                reply_parent_uri=reply_parent_uri, reply_parent_cid=reply_parent_cid,
                quote_uri=quote_uri, liked_uri=liked_uri, reposted_uri=reposted_uri,
                bookmarked=bool(bookmarked), media_attachments=media, external_card=external,
                urls=urls, repost_by_name=repost_by_name, repost_by_handle=repost_by_handle,
                verificado=bool(verificado))
        except Exception as e:
            log.error(f"Error al procesar una publicación de W Social: {e}")
            return None

    # ------------------------------------------------------------------
    # HILOS Y PERFILES
    # ------------------------------------------------------------------
    def _cargar_hilo_como_buffer(self, account_id, client, timeline_key, clave_cursor):
        """Descarga una conversación entera y la deja como buffer navegable, en orden de lectura
        (primero los antepasados, luego la publicación y después las respuestas)."""
        uri = timeline_key[len("thread:"):]
        resp = self._get_con_reintentos(
            account_id, lambda: client.app.bsky.feed.get_post_thread({"uri": uri, "depth": 100}))
        self._cursors[clave_cursor] = None
        self._sin_mas_paginas(clave_cursor, True)
        ordenados = self._aplanar_hilo(_g(resp, "thread", default=None))
        posts = []
        with db.transaccion() as conn:
            conn.execute("DELETE FROM wsocial_timeline_posts WHERE account_id = ? AND timeline = ?",
                         (account_id, timeline_key))
            # position 0 es lo MÁS NUEVO en todos los buffers de W Social (la interfaz ordena
            # por position ASC y luego aplica "más nuevos arriba/abajo"). En una conversación
            # lo más nuevo es lo último en orden de lectura, así que la posición va al revés
            # del índice. Antes la raíz llevaba la 0 y el buffer salía invertido.
            total = len(ordenados)
            for i, nodo in enumerate(ordenados):
                post = self._procesar_y_guardar_post(account_id, nodo, conn=conn)
                if not post:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO wsocial_timeline_posts "
                    "(account_id, timeline, post_db_id, position) VALUES (?,?,?,?)",
                    (account_id, timeline_key, post.id, total - 1 - i))
                posts.append(post)
        return posts

    @staticmethod
    def _aplanar_hilo(nodo) -> list:
        """Convierte el árbol de una conversación en una lista lineal en orden de lectura."""
        antepasados, respuestas = [], []

        def _subir(n):
            padre = _g(n, "parent", default=None)
            if padre is None:
                return
            _subir(padre)
            p = _g(padre, "post", default=None)
            if p is not None:
                antepasados.append(p)

        def _bajar(n, nivel=0):
            for hijo in (_g(n, "replies", default=[]) or []):
                p = _g(hijo, "post", default=None)
                if p is not None:
                    respuestas.append(p)
                if nivel < 20:
                    _bajar(hijo, nivel + 1)

        if nodo is None:
            return []
        _subir(nodo)
        principal = _g(nodo, "post", default=None)
        _bajar(nodo)
        return antepasados + ([principal] if principal is not None else []) + respuestas

    def contexto_respuesta_async(self, account_id: int, uri: str, callback: Optional[Callable] = None):
        """A quién respondes y a quién avisará el servidor, para el editor (como en Bluesky: el
        fork de W Social del AppView usa la misma regla, REPLY_NOTIF_DEPTH = 5). Callback
        (ok, {"padre", "avisados", "yo"}), silencioso si falla: es solo información."""
        from app.core.at_lectura import AVISO_RESPUESTA_NIVELES, contexto_desde_hilo

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id) or {}
            resp = self._get_con_reintentos(
                account_id, lambda: client.app.bsky.feed.get_post_thread(
                    {"uri": uri, "depth": 0, "parentHeight": AVISO_RESPUESTA_NIVELES - 1}))
            return contexto_desde_hilo(_g(resp, "thread", default=None),
                                       yo.get("did", ""), yo.get("handle", ""))
        self._tarea(_bg, callback, silencioso=True)

    def obtener_hilo_async(self, account_id: int, uri: str, callback: Optional[Callable] = None):
        """Conversación completa de una publicación, ya en orden de lectura y cacheada (para el
        visor de hilos, que necesita poder actuar sobre cada publicación)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = self._get_con_reintentos(
                account_id, lambda: client.app.bsky.feed.get_post_thread({"uri": uri, "depth": 100}))
            ordenados = self._aplanar_hilo(_g(resp, "thread", default=None))
            salida = []
            with db.transaccion() as conn:
                for nodo in ordenados:
                    post = self._procesar_y_guardar_post(account_id, nodo, conn=conn)
                    if post:
                        salida.append(post)
            return salida
        self._tarea(_bg, callback)

    def obtener_perfil_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Ficha completa de una cuenta (por handle o DID), incluida tu relación con ella."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            p = self._get_con_reintentos(
                account_id, lambda: client.app.bsky.actor.get_profile({"actor": actor}))
            viewer = _g(p, "viewer", default=None)
            verif = _g(p, "verification", default=None)
            return {
                "did": _g(p, "did", default=""),
                "handle": _g(p, "handle", default=""),
                "display_name": _g(p, "display_name", "displayName", default=""),
                "description": _g(p, "description", default="") or "",
                "avatar": _g(p, "avatar", default=""),
                "banner": _g(p, "banner", default=""),
                "followers": _g(p, "followers_count", "followersCount", default=0),
                "following": _g(p, "follows_count", "followsCount", default=0),
                "posts": _g(p, "posts_count", "postsCount", default=0),
                "creado": _g(p, "created_at", "createdAt", default=""),
                "following_uri": _g(viewer, "following", default="") or "",
                "followed_by": _g(viewer, "followed_by", "followedBy", default="") or "",
                "blocking_uri": _g(viewer, "blocking", default="") or "",
                "blocked_by": bool(_g(viewer, "blocked_by", "blockedBy", default=False)),
                "muted": bool(_g(viewer, "muted", default=False)),
                "verificado": str(_g(verif, "verified_status", "verifiedStatus", default="")).lower() == "valid",
                "verificador": [
                    {"handle": _g(v, "issuer", default=""), "creado": _g(v, "created_at", "createdAt", default="")}
                    for v in (_g(verif, "verifications", default=[]) or [])],
                "etiquetas": [_g(l, "val", default="") for l in (_g(p, "labels", default=[]) or [])],
            }
        self._tarea(_bg, callback)

    def obtener_perfiles_async(self, account_id: int, actores: List[str],
                               callback: Optional[Callable] = None):
        """Varios perfiles de una vez (hasta 25 por llamada), para listas de cuentas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            salida = []
            for i in range(0, len(actores), 25):
                resp = self._get_con_reintentos(
                    account_id,
                    lambda lote=actores[i:i + 25]: client.app.bsky.actor.get_profiles({"actors": lote}))
                for p in (_g(resp, "profiles", default=[]) or []):
                    salida.append({
                        "did": _g(p, "did", default=""),
                        "handle": _g(p, "handle", default=""),
                        "display_name": _g(p, "display_name", "displayName", default=""),
                        "description": _g(p, "description", default="") or "",
                    })
            return salida
        self._tarea(_bg, callback)

    def obtener_publicaciones_async(self, account_id: int, uris: List[str],
                                    callback: Optional[Callable] = None):
        """Publicaciones concretas por su URI (hasta 25 por llamada), ya cacheadas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            salida = []
            with db.transaccion() as conn:
                for i in range(0, len(uris), 25):
                    resp = self._get_con_reintentos(
                        account_id,
                        lambda lote=uris[i:i + 25]: client.app.bsky.feed.get_posts({"uris": lote}))
                    for p in (_g(resp, "posts", default=[]) or []):
                        post = self._procesar_y_guardar_post(account_id, p, conn=conn)
                        if post:
                            salida.append(post)
            return salida
        self._tarea(_bg, callback)

    def interacciones_async(self, account_id: int, uri: str, tipo: str = "likes",
                            callback: Optional[Callable] = None):
        """Quién ha dado me gusta ('likes') o republicado ('reposts') una publicación."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            salida = []
            if tipo == "reposts":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_reposted_by({"uri": uri, "limit": 100}))
                actores = _g(resp, "reposted_by", "repostedBy", default=[]) or []
            else:
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.feed.get_likes({"uri": uri, "limit": 100}))
                actores = [_g(l, "actor") for l in (_g(resp, "likes", default=[]) or [])]
            for a in actores:
                salida.append({
                    "did": _g(a, "did", default=""),
                    "handle": _g(a, "handle", default=""),
                    "display_name": _g(a, "display_name", "displayName", default=""),
                })
            return salida
        self._tarea(_bg, callback)
