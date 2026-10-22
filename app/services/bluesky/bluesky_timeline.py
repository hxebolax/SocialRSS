# -*- coding: utf-8 -*-
"""Timelines, notificaciones, parseo de posts e hilos.

Mixin de BlueskyService (parte de la refactorización modular).
"""
from app.services.bluesky.bluesky_common import (
    json, Any, Callable, Dict, List, Optional, db, task_manager, BlueskyPost,
    log, _URL_RE, _g, _ahora_iso, uri_post_de_notificacion)



def known_likers_json(viewer) -> str:
    """viewer.knownLikers (Bluesky, 2026-08): cuántas personas que sigues dieron "me gusta" y
    hasta 5 de ellas, como JSON {"count", "nombres"}; "" si no hay ninguna."""
    kl = _g(viewer, "known_likers", "knownLikers", default=None)
    if not kl:
        return ""
    total = _g(kl, "count", default=0) or 0
    nombres = []
    for a in (_g(kl, "actors", default=None) or [])[:5]:
        nombre = _g(a, "display_name", "displayName", default="") or _g(a, "handle", default="")
        if nombre:
            nombres.append(nombre)
    if not total and not nombres:
        return ""
    return json.dumps({"count": max(int(total), len(nombres)), "nombres": nombres}, ensure_ascii=False)

class _TimelineMixin:
    def cargar_timeline_async(self, account_id: int, timeline_type: str = "home",
                             actor: Optional[str] = None, limit: int = 40,
                             cargar_mas: bool = False,
                             callback: Optional[Callable] = None):
        """
        Carga un timeline y lo cachea.

        timeline_type: home, sent, likes, author, mentions, notifications.
        actor: para 'author' (did o handle del usuario).
        cargar_mas: si True usa el cursor guardado para traer una página más antigua.
        """
        clave_cursor = f"{account_id}:{timeline_type}:{actor or ''}"
        # Sesión caducada: no se lanza la tarea (moriría con el mismo error). El usuario ya recibió
        # el aviso cuando caducó; hasta que vuelva a iniciar sesión, cada sondeo automático solo
        # gastaba un hilo y una traza en el registro.
        if self.abortar_si_sesion_caducada(account_id, callback):
            return

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            # Calentar la caché de preferencias de MODERACIÓN (una vez por página, con TTL
            # de 1 h) ANTES de abrir la transacción: el procesado por post la lee sin red.
            try:
                self.prefs_moderacion(account_id, permitir_red=True)
            except Exception:
                pass
            cursor = self._cursors.get(clave_cursor) if cargar_mas else None
            if cargar_mas and clave_cursor in getattr(self, "_agotados", set()):
                # No hay más páginas: sin esto, con el cursor a None se volvía a pedir
                # la PRIMERA página una y otra vez (auditoría BSKY-006).
                return []

            if timeline_type == "notifications":
                return self._cargar_notificaciones(account_id, client, cursor, limit, clave_cursor)
            if timeline_type == "mentions":
                return self._cargar_menciones(account_id, client, cursor, limit, clave_cursor)
            if timeline_type.startswith("thread:"):
                return self._cargar_hilo_como_buffer(account_id, client, timeline_type, clave_cursor)

            # Timelines de posts. Cada rama define la LLAMADA y se ejecuta una sola vez a
            # través de _get_con_reintentos: lecturas idempotentes con backoff+jitter para
            # errores transitorios, y cooldown centralizado ante 429 (auditoría 9.5).
            campo_feed = "feed"
            if timeline_type == "home":
                _pedir = lambda: client.get_timeline(cursor=cursor, limit=limit)  # noqa: E731
            elif timeline_type == "likes":
                yo = self._yo(account_id)
                _pedir = lambda: client.app.bsky.feed.get_actor_likes(  # noqa: E731
                    {"actor": yo["did"], "cursor": cursor, "limit": limit})
            elif timeline_type in ("sent", "author"):
                obj = actor or self._yo(account_id)["did"]
                _pedir = lambda: client.get_author_feed(actor=obj, cursor=cursor, limit=limit)  # noqa: E731
            elif timeline_type.startswith("author:"):
                # Buffer de "cronología de una cuenta" (creado por el usuario). El DID va tras el
                # primer ':' (split con maxsplit=1: los DID llevan sus propios ':', p. ej. did:plc:).
                obj = timeline_type.split(":", 1)[1]
                _pedir = lambda: client.get_author_feed(actor=obj, cursor=cursor, limit=limit)  # noqa: E731
            elif timeline_type.startswith("feed:"):
                uri = timeline_type.split(":", 1)[1]
                _pedir = lambda: client.app.bsky.feed.get_feed(  # noqa: E731
                    {"feed": uri, "cursor": cursor, "limit": limit})
            elif timeline_type.startswith("list:"):
                uri = timeline_type.split(":", 1)[1]
                _pedir = lambda: client.app.bsky.feed.get_list_feed(  # noqa: E731
                    {"list": uri, "cursor": cursor, "limit": limit})
            elif timeline_type.startswith("search:"):
                q = timeline_type.split(":", 1)[1]
                _pedir = lambda: client.app.bsky.feed.search_posts(  # noqa: E731
                    {"q": q, "cursor": cursor, "limit": limit})
                campo_feed = "posts"  # search devuelve PostView, no FeedViewPost
            else:
                _pedir = lambda: client.get_timeline(cursor=cursor, limit=limit)  # noqa: E731
            resp = self._get_con_reintentos(account_id, _pedir)
            feed = _g(resp, campo_feed, default=[])

            nuevo_cursor = _g(resp, "cursor")
            self._cursors[clave_cursor] = nuevo_cursor
            # Solo el "cargar más" marca el final: en un refresco normal (primera página)
            # el cursor sirve para paginar hacia atrás aunque venga página incompleta.
            if cargar_mas:
                self._sin_mas_paginas(clave_cursor, nuevo_cursor is None)

            # Toda la página en UNA transacción (un commit), no una por post. La asociación
            # guarda el ORDEN DEL SERVIDOR con claves monotónicas (sort_key) y el CONTEXTO
            # (motivo del repost/pin, feedContext, reqId), auditoría BSKY-004: sin esto los
            # buffers reordenaban por indexed_at y un repost antiguo se hundía, los feeds
            # perdían su ranking y la búsqueda su relevancia.
            posts: List[BlueskyPost] = []
            req_id = _g(resp, "req_id", "reqId", default=None)
            with db.transaccion() as _conn:
                fila = _conn.execute(
                    "SELECT MAX(sort_key), MIN(sort_key) FROM bluesky_timeline_posts "
                    "WHERE account_id = ? AND timeline = ?",
                    (account_id, timeline_type)).fetchone()
                maxk = fila[0] if fila and fila[0] is not None else 0
                mink = fila[1] if fila and fila[1] is not None else 0
                n = len(feed)
                # Página nueva (refresco): por ENCIMA de lo existente, conservando el orden
                # interno de la página. "Cargar más": por DEBAJO (más antiguo).
                base = (mink - 1) if cargar_mas else (maxk + n)
                for idx, fv in enumerate(feed):
                    post_obj = self._procesar_y_guardar_post(account_id, fv, conn=_conn)
                    if not post_obj:
                        continue
                    posts.append(post_obj)
                    reason = _g(fv, "reason", default=None)
                    reason_json = reason_idx = None
                    if reason is not None:
                        try:
                            reason_json = json.dumps(
                                reason.model_dump(by_alias=True, exclude_none=True), default=str)
                        except Exception:
                            reason_json = None
                        reason_idx = str(_g(reason, "indexed_at", "indexedAt", default="") or "") or None
                    fc = _g(fv, "feed_context", "feedContext", default=None)
                    _conn.execute(
                        "INSERT OR IGNORE INTO bluesky_timeline_posts "
                        "(account_id, timeline, post_db_id, sort_key, reason_indexed_at, "
                        " reason_json, feed_context, req_id) VALUES (?,?,?,?,?,?,?,?)",
                        (account_id, timeline_type, post_obj.id, base - idx,
                         reason_idx, reason_json, fc, req_id),
                    )
            return posts

        # unique_key (auditoría Bluesky 7.5): refresco manual, temporizador, sondeo de
        # buffer y aviso del firehose podían pedir el MISMO recurso a la vez. Con la
        # clave, la petición duplicada se descarta. fresh/older no comparten clave
        # (usan cursores distintos).
        task_manager.start_task(
            _bg, callback=callback, pass_context=True,
            unique_key=f"bsky:{account_id}:{timeline_type}:{actor or ''}:"
                       f"{'older' if cargar_mas else 'fresh'}")

    def _sin_mas_paginas(self, clave: str, agotado: bool):
        """Marca (o desmarca) que un buffer ya no tiene más páginas hacia atrás."""
        if not hasattr(self, "_agotados"):
            self._agotados = set()
        if agotado:
            self._agotados.add(clave)
        else:
            self._agotados.discard(clave)

    def hay_mas_paginas(self, account_id: int, timeline_type: str, actor=None) -> bool:
        """False si el buffer ya llegó al final (para desactivar/avisar en 'Cargar más')."""
        return f"{account_id}:{timeline_type}:{actor or ''}" not in getattr(self, "_agotados", set())

    def _cargar_notificaciones(self, account_id, client, cursor, limit, clave_cursor):
        resp = client.app.bsky.notification.list_notifications(
            {"cursor": cursor, "limit": limit})
        nuevo_cursor = _g(resp, "cursor")
        self._cursors[clave_cursor] = nuevo_cursor
        # Estado explícito de "no hay más páginas" (auditoría BSKY-006): cuando el cursor
        # vuelve vacío, "Cargar más" volvía a pedir la PRIMERA página en bucle.
        self._sin_mas_paginas(clave_cursor, nuevo_cursor is None)
        salida = []
        with db.transaccion() as _conn:
            for n in _g(resp, "notifications", default=[]):
                salida.append(self._procesar_y_guardar_notificacion(account_id, n, conn=_conn))
        # Fuera de la transacción (NUNCA red dentro de una escritura): completar el texto de
        # las publicaciones propias que aún no estaban en la caché local.
        self._completar_textos_notificaciones(account_id, client, salida)
        return salida

    def _completar_textos_notificaciones(self, account_id, client, notifs):
        """Descarga las publicaciones aludidas por las notificaciones cuyo texto falta.

        En "me gusta" y "repost" la publicación afectada es TUYA, y puede no estar en la caché
        (buffer nunca abierto, base recién limpiada). Sin esto la lista diría solo "Me gusta de
        Fulano", sin decir de QUÉ mensaje. Se piden en lotes de 25 (máximo de getPosts)."""
        pendientes = []
        for d in notifs:
            su = (d or {}).get("subject_uri") or ""
            if su and not (d or {}).get("content") and su not in pendientes:
                pendientes.append(su)
        if not pendientes:
            return
        for i in range(0, len(pendientes), 25):
            lote = pendientes[i:i + 25]
            try:
                pr = client.get_posts(lote)
            except Exception as e:
                log.debug(f"No se pudieron resolver publicaciones de notificaciones: {e}")
                continue
            textos = {}
            with db.transaccion() as _conn:
                for pv in _g(pr, "posts", default=[]):
                    po = self._procesar_y_guardar_post(account_id, pv, conn=_conn)
                    if po:
                        textos[po.uri] = po.content or ""
                for d in notifs:
                    su = (d or {}).get("subject_uri") or ""
                    if su in textos and textos[su]:
                        d["content"] = textos[su]
                        _conn.execute(
                            "UPDATE bluesky_notifications SET content = ? WHERE id = ?",
                            (textos[su], d["id"]))

    def _cargar_menciones(self, account_id, client, cursor, limit, clave_cursor):
        """Buffer 'Menciones': notificaciones de tipo mención/respuesta/cita -> sus posts.

        Se pide al servidor SOLO esos tipos con el filtro `reasons` (auditoría BSKY-006):
        antes se bajaba una página general y se filtraba localmente, así que una página
        llena de "me gusta" podía dejar fuera menciones más antiguas."""
        relevantes = ("mention", "reply", "quote")
        resp = client.app.bsky.notification.list_notifications(
            {"cursor": cursor, "limit": limit, "reasons": list(relevantes)})
        nuevo_cursor = _g(resp, "cursor")
        self._cursors[clave_cursor] = nuevo_cursor
        self._sin_mas_paginas(clave_cursor, nuevo_cursor is None)
        uris = []
        for n in _g(resp, "notifications", default=[]):
            if _g(n, "reason") in relevantes:
                uris.append(_g(n, "uri"))
        posts = []
        # get_posts admite hasta 25 uris por llamada. Cada lote se guarda en una transacción.
        for i in range(0, len(uris), 25):
            lote = [u for u in uris[i:i + 25] if u]
            if not lote:
                continue
            try:
                pr = client.get_posts(lote)
                with db.transaccion() as _conn:
                    for pv in _g(pr, "posts", default=[]):
                        po = self._procesar_y_guardar_post(account_id, pv, conn=_conn)
                        if po:
                            posts.append(po)
                            _conn.execute(
                                "INSERT OR IGNORE INTO bluesky_timeline_posts (account_id, timeline, post_db_id) VALUES (?, 'mentions', ?)",
                                (account_id, po.id))
            except Exception as e:
                log.debug(f"Error al cargar posts de menciones: {e}")
        return posts

    # ------------------------------------------------------------------
    # PARSEO Y ALMACENAMIENTO
    # ------------------------------------------------------------------
    def _extraer_media(self, embed) -> List[Dict[str, Any]]:
        """Extrae adjuntos (imágenes/vídeo) de un embed hidratado."""
        media = []
        if not embed:
            return media
        tipo = str(_g(embed, "py_type", "$type", default="")).lower()
        if "images" in tipo:
            for img in _g(embed, "images", default=[]) or []:
                media.append({
                    "type": "image",
                    "url": _g(img, "fullsize"),
                    "thumb": _g(img, "thumb"),
                    "alt": _g(img, "alt", default=""),
                })
        elif "video" in tipo:
            media.append({
                "type": "video",
                "url": _g(embed, "playlist"),
                "thumb": _g(embed, "thumbnail"),
                "alt": _g(embed, "alt", default=""),
            })
        elif "gallery" in tipo:
            # Galerías (app.bsky.embed.gallery): hasta 10 elementos en la práctica. Sin
            # esta rama, un post moderno con galería aparecía SIN multimedia (auditoría
            # Bluesky 6.3). De momento solo lectura; la autoría podrá venir después.
            for it in _g(embed, "items", default=[]) or []:
                media.append({
                    "type": "image",
                    "url": _g(it, "fullsize"),
                    "thumb": _g(it, "thumb"),
                    "alt": _g(it, "alt", default=""),
                })
        elif "recordwithmedia" in tipo:
            media.extend(self._extraer_media(_g(embed, "media")))
        return media

    def _extraer_external(self, embed) -> Optional[Dict[str, Any]]:
        """Extrae la tarjeta de enlace externo de un embed."""
        if not embed:
            return None
        tipo = str(_g(embed, "py_type", "$type", default="")).lower()
        if "external" in tipo:
            ext = _g(embed, "external")
            if ext:
                return {
                    "url": _g(ext, "uri", default=""),
                    "title": _g(ext, "title", default=""),
                    "description": _g(ext, "description", default=""),
                    "thumb": _g(ext, "thumb"),
                }
        if "recordwithmedia" in tipo:
            return self._extraer_external(_g(embed, "media"))
        return None

    def _procesar_y_guardar_post(self, account_id: int, feed_item, conn=None) -> Optional[BlueskyPost]:
        """Procesa un FeedViewPost o PostView, lo guarda en SQLite e indexa en FTS5.

        Si se pasa `conn` (de db.transaccion()), escribe por esa conexión SIN commit individual,
        para que el llamador agrupe toda la página en una sola transacción (rendimiento)."""
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
            # Un FeedViewPost trae .post y opcionalmente .reason (repost). Un PostView no.
            post = _g(feed_item, "post", default=None) or feed_item
            reason = _g(feed_item, "reason", default=None)

            uri = _g(post, "uri")
            cid = _g(post, "cid")
            if not uri:
                return None

            author = _g(post, "author")
            author_did = _g(author, "did", default="")
            author_handle = _g(author, "handle", default="")
            author_name = _g(author, "display_name", "displayName", default="") or author_handle

            record = _g(post, "record")
            content = _g(record, "text", default="") or ""
            created_at = _g(record, "created_at", "createdAt", default="") or ""
            indexed_at = _g(post, "indexed_at", "indexedAt", default="") or created_at

            langs_list = _g(record, "langs", default=None) or []
            langs = ",".join(langs_list) if langs_list else ""

            # Etiquetas del contenido: las PROPIAS (self-labels, la "advertencia de
            # contenido" del autor) MÁS las HIDRATADAS por el AppView y los labelers
            # suscritos (post.labels y las del autor). Antes solo se leían las propias,
            # así que contenido que un servicio de moderación pedía marcar se mostraba
            # sin aviso (auditoría BSKY-005). Las negaciones (neg=True) retiran la marca.
            etiquetas = []
            rec_labels = _g(record, "labels", default=None)
            for v in (_g(rec_labels, "values", default=None) or []):
                val = _g(v, "val", default="")
                if val and val not in etiquetas:
                    etiquetas.append(val)
            for origen in (_g(post, "labels", default=None) or []) + list(_g(author, "labels", default=None) or []):
                val = _g(origen, "val", default="")
                if not val:
                    continue
                # Las de SISTEMA (!hide, !warn) SÍ se conservan: el motor de moderación
                # las necesita para decidir por contexto (auditoría 8.1). La interfaz las
                # excluye de la columna de aviso con etiquetas_visibles().
                if _g(origen, "neg", default=False):
                    if val in etiquetas:
                        etiquetas.remove(val)
                elif val not in etiquetas:
                    etiquetas.append(val)
            labels = ",".join(etiquetas)
            # Datos de moderación COMPLETOS (B2/B7/B8/B13): etiquetas con su ORIGEN (para
            # descartar las de servicios no suscritos y aplicar sus definiciones), las de la
            # cuenta aparte de las del post, la cita y los textos para palabras silenciadas.
            from .bluesky_moddatos import extraer_datos_post, a_json, de_json, fusionar_rp
            datos_mod = extraer_datos_post(post, feed_item if feed_item is not post else None)

            # Referencias de respuesta (para responder al hilo).
            reply = _g(record, "reply", default=None)
            reply_root_uri = _g(_g(reply, "root"), "uri", default="")
            reply_root_cid = _g(_g(reply, "root"), "cid", default="")
            reply_parent_uri = _g(_g(reply, "parent"), "uri", default="")
            reply_parent_cid = _g(_g(reply, "parent"), "cid", default="")
            # A QUIÉN responde (para leer "En respuesta a @x"): el autor del padre viene
            # hidratado en el FeedViewPost; en un hilo propio se deduce del DID de la URI.
            from app.core.at_lectura import autor_del_padre
            reply_parent_handle, reply_parent_name = (
                autor_del_padre(feed_item, reply_parent_uri, author_did, author_handle, author_name)
                if reply_parent_uri else ("", ""))

            # ATAJO ANTI-TIRONES (igual que en Mastodon): si el post YA está cacheado, solo
            # refrescamos contadores/estado (like/repost/bookmark) y nos SALTAMOS la
            # extracción de media, tarjeta y enlaces (lo caro). PERO solo si el CID no ha
            # cambiado (auditoría Bluesky 7.2): en AT Protocol un registro puede
            # reemplazarse con la misma URI y CID nuevo; el atajo antiguo asumía URIs
            # inmutables y dejaba obsoletos contenido, media, cita y etiquetas.
            db_id = f"{account_id}:{uri}"
            _prev_bsky = _q1("SELECT content, cid, COALESCE(mod_json,'') FROM bluesky_posts "
                             "WHERE id = ?", (db_id,))
            if _prev_bsky is not None:
                datos_mod = fusionar_rp(datos_mod, de_json(_prev_bsky[2]))
            mod_json = a_json(datos_mod)
            if (_prev_bsky is not None and (_prev_bsky[0] or "")
                    and cid and (_prev_bsky[1] or "") == cid):
                liked_uri = _g(_g(post, "viewer", default=None), "like", default="") or ""
                reposted_uri = _g(_g(post, "viewer", default=None), "repost", default="") or ""
                bookmarked = 1 if _g(_g(post, "viewer", default=None), "bookmarked", default=False) else 0
                conocidos = known_likers_json(_g(post, "viewer", default=None))
                try:
                    _ej("UPDATE bluesky_posts SET like_count=?, repost_count=?, reply_count=?, "
                        "quote_count=?, liked_uri=?, reposted_uri=?, bookmarked=?, indexed_at=?, "
                        "known_likers=?, "
                        # Las ETIQUETAS pueden llegar más tarde que el post (un labeler lo
                        # marca minutos después) con el MISMO CID: se refrescan siempre (B3).
                        "labels=?, mod_json=?, "
                        # Lo que ya se sabía de a quién responde no se pierde si esta vez
                        # el post llega sin hidratar (búsqueda, menciones...).
                        "reply_parent_handle=CASE WHEN ?<>'' THEN ? ELSE reply_parent_handle END, "
                        "reply_parent_name=CASE WHEN ?<>'' THEN ? ELSE reply_parent_name END "
                        "WHERE id=?",
                        (_g(post, "like_count", "likeCount", default=0) or 0,
                         _g(post, "repost_count", "repostCount", default=0) or 0,
                         _g(post, "reply_count", "replyCount", default=0) or 0,
                         _g(post, "quote_count", "quoteCount", default=0) or 0,
                         liked_uri, reposted_uri, bookmarked, indexed_at, conocidos,
                         labels, mod_json,
                         reply_parent_handle, reply_parent_handle,
                         reply_parent_name, reply_parent_name, db_id))
                except Exception as e:
                    log.debug(f"No se pudo refrescar contadores del post Bluesky {db_id}: {e}")
                return BlueskyPost(id=db_id, account_id=account_id, uri=uri, cid=cid or "",
                                   author_did=author_did, author_handle=author_handle,
                                   author_name=author_name, content=_prev_bsky[0] or "",
                                   created_at=created_at, indexed_at=indexed_at,
                                   reply_root_uri=reply_root_uri, reply_root_cid=reply_root_cid,
                                   reply_parent_uri=reply_parent_uri,
                                   reply_parent_cid=reply_parent_cid, langs=langs,
                                   labels=labels, mod_json=mod_json,
                                   quote_uri=(datos_mod.get("q") or {}).get("u") or None,
                                   liked_uri=liked_uri, reposted_uri=reposted_uri,
                                   bookmarked=bool(bookmarked),
                                   like_count=_g(post, "like_count", "likeCount", default=0) or 0)

            # Embeds hidratados (media / tarjeta / cita).
            embed = _g(post, "embed", default=None)
            media = self._extraer_media(embed)
            external = self._extraer_external(embed)
            quote_uri = ""
            tipo_embed = str(_g(embed, "py_type", "$type", default="")).lower()
            if "record" in tipo_embed:
                rec = _g(embed, "record")
                quote_uri = _g(rec, "uri", default="") or _g(_g(rec, "record"), "uri", default="")

            # Contadores e info del viewer.
            like_count = _g(post, "like_count", "likeCount", default=0) or 0
            repost_count = _g(post, "repost_count", "repostCount", default=0) or 0
            reply_count = _g(post, "reply_count", "replyCount", default=0) or 0
            quote_count = _g(post, "quote_count", "quoteCount", default=0) or 0
            viewer = _g(post, "viewer", default=None)
            liked_uri = _g(viewer, "like", default="") or ""
            reposted_uri = _g(viewer, "repost", default="") or ""
            bookmarked = 1 if _g(viewer, "bookmarked", default=False) else 0

            # Quién reposteó (si el item llega por repost).
            repost_by_name = ""
            repost_by_handle = ""
            if reason and "repost" in str(_g(reason, "py_type", "$type", default="")).lower():
                by = _g(reason, "by")
                repost_by_handle = _g(by, "handle", default="")
                repost_by_name = _g(by, "display_name", "displayName", default="") or repost_by_handle

            # Enlaces: tarjeta externa + URLs sueltas del texto.
            urls = []
            if external and external.get("url"):
                urls.append(external["url"])
            for u in _URL_RE.findall(content):
                if u not in urls:
                    urls.append(u)

            media_json = json.dumps(media, ensure_ascii=False)
            external_json = json.dumps(external, ensure_ascii=False) if external else ""
            urls_json = json.dumps(urls, ensure_ascii=False)
            db_id = f"{account_id}:{uri}"

            sql = """
            INSERT INTO bluesky_posts (
                id, account_id, uri, cid, author_did, author_handle, author_name, content,
                created_at, indexed_at, langs, labels, reply_root_uri, reply_root_cid,
                reply_parent_uri, reply_parent_cid, quote_uri, like_count, repost_count,
                reply_count, quote_count, liked_uri, reposted_uri, bookmarked, media_json,
                external_json, urls_json, repost_by_name, repost_by_handle, known_likers,
                reply_parent_handle, reply_parent_name, mod_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                cid=excluded.cid, content=excluded.content, indexed_at=excluded.indexed_at,
                like_count=excluded.like_count, repost_count=excluded.repost_count,
                reply_count=excluded.reply_count, quote_count=excluded.quote_count,
                liked_uri=excluded.liked_uri, reposted_uri=excluded.reposted_uri,
                bookmarked=excluded.bookmarked, media_json=excluded.media_json,
                external_json=excluded.external_json, urls_json=excluded.urls_json,
                labels=excluded.labels, known_likers=excluded.known_likers,
                mod_json=excluded.mod_json, quote_uri=excluded.quote_uri,
                reply_parent_handle=COALESCE(NULLIF(excluded.reply_parent_handle,''),
                                             bluesky_posts.reply_parent_handle),
                reply_parent_name=COALESCE(NULLIF(excluded.reply_parent_name,''),
                                           bluesky_posts.reply_parent_name);
            """
            # FTS condicional: leer lo ya indexado antes de sobrescribir; si texto/autor no cambian
            # (refresco de contadores), nos saltamos el DELETE+INSERT de FTS.
            _prev = _q1("SELECT content, author_name, author_handle FROM bluesky_posts WHERE id = ?", (db_id,))

            _ej(sql, (
                db_id, account_id, uri, cid or "", author_did, author_handle, author_name,
                content, created_at, indexed_at, langs, labels, reply_root_uri, reply_root_cid,
                reply_parent_uri, reply_parent_cid, quote_uri, like_count, repost_count,
                reply_count, quote_count, liked_uri, reposted_uri, bookmarked, media_json,
                external_json, urls_json, repost_by_name, repost_by_handle,
                known_likers_json(viewer), reply_parent_handle, reply_parent_name, mod_json,
            ))

            try:
                # MODERACIÓN antes de indexar (auditoría 8.1): lo que las etiquetas y tus
                # preferencias piden OCULTAR no entra en la búsqueda local (y si ya estaba
                # indexado de antes, se retira).
                from .bluesky_moderacion import moderar_post
                # SIN red aquí: esto corre por cada post y DENTRO de la transacción de
                # escritura. La caché se calienta en cargar_timeline_async (fuera de la
                # transacción) una vez por página. Solo ETIQUETAS (contenido y cuenta): las
                # palabras silenciadas y las publicaciones ocultadas cambian con el tiempo y
                # se aplican al mostrar los resultados de la búsqueda.
                solo_etiquetas = {k: datos_mod[k] for k in ("d", "l", "al") if k in datos_mod}
                solo_etiquetas.setdefault("l", [])
                mod = moderar_post(solo_etiquetas, self.prefs_moderacion(account_id),
                                   contexto="busqueda")
                if mod["filtrar"]:
                    _ej("DELETE FROM bluesky_posts_fts WHERE post_db_id = ?", (db_id,))
                elif _prev is None or (_prev[0], _prev[1], _prev[2]) != (content, author_name, author_handle):
                    _ej("DELETE FROM bluesky_posts_fts WHERE post_db_id = ?", (db_id,))
                    _ej(
                        "INSERT INTO bluesky_posts_fts (post_db_id, author_name, author_handle, content) VALUES (?, ?, ?, ?)",
                        (db_id, author_name, author_handle, content))
            except Exception as fts_err:
                log.debug(f"Error al indexar FTS de post Bluesky: {fts_err}")

            return BlueskyPost(
                id=db_id, account_id=account_id, uri=uri, cid=cid or "",
                author_did=author_did, author_handle=author_handle, author_name=author_name,
                content=content, created_at=created_at, indexed_at=indexed_at,
                langs=langs, labels=labels, reply_root_uri=reply_root_uri,
                reply_root_cid=reply_root_cid, reply_parent_uri=reply_parent_uri,
                reply_parent_cid=reply_parent_cid, quote_uri=quote_uri,
                like_count=like_count, repost_count=repost_count, reply_count=reply_count,
                quote_count=quote_count, liked_uri=liked_uri, reposted_uri=reposted_uri,
                bookmarked=bool(bookmarked), media_attachments=media, external_card=external,
                urls=urls, repost_by_name=repost_by_name, repost_by_handle=repost_by_handle,
                mod_json=mod_json,
            )
        except Exception as e:
            log.error(f"Error al procesar post de Bluesky: {e}", exc_info=True)
            return None

    def _procesar_y_guardar_notificacion(self, account_id: int, n, conn=None) -> Dict[str, Any]:
        """Guarda una notificación en SQLite. Acepta `conn` para agruparse en la transacción
        del llamador (db.transaccion())."""
        uri = _g(n, "uri", default="")
        cid = _g(n, "cid", default="")
        reason = _g(n, "reason", default="")
        reason_subject = _g(n, "reason_subject", "reasonSubject", default="")
        record = _g(n, "record")
        author = _g(n, "author")
        author_did = _g(author, "did", default="")
        author_handle = _g(author, "handle", default="")
        author_name = _g(author, "display_name", "displayName", default="") or author_handle
        is_read = 1 if _g(n, "is_read", "isRead", default=False) else 0
        indexed_at = _g(n, "indexed_at", "indexedAt", default="") or ""
        # A QUÉ PUBLICACIÓN se refiere el aviso (petición de usuarios: en las notificaciones
        # no se sabía de qué mensaje se hablaba y "Ver la publicación" fallaba con 400
        # NotFound porque se pedía el hilo del registro del "me gusta", no del post).
        subject_uri = uri_post_de_notificacion(reason, uri, reason_subject, record)
        # Texto que se muestra en la lista. Si el `uri` de la notificación ES la publicación
        # (mención, respuesta, cita), su texto viene en el propio registro; si no (me gusta,
        # repost), es tu publicación: se toma de la caché local y, si aún no está, la
        # completa después `_completar_textos_notificaciones` con una sola llamada de red.
        contenido = ""
        if subject_uri == uri:
            contenido = (_g(record, "text", default="") or "").strip()
        elif subject_uri:
            fila = db.query_one("SELECT content FROM bluesky_posts WHERE id = ?",
                                (f"{account_id}:{subject_uri}",))
            contenido = (fila[0] or "") if fila else ""
        db_id = f"{account_id}:{uri}"
        # Moderación de la notificación (B5): etiquetas propias y de la cuenta, y el texto si
        # es una publicación de otra cuenta (palabras silenciadas).
        from .bluesky_moddatos import extraer_datos_notificacion, a_json
        mod_json = a_json(extraer_datos_notificacion(n))
        sql = """
        INSERT INTO bluesky_notifications (
            id, account_id, uri, cid, reason, reason_subject, subject_uri, content,
            author_did, author_handle, author_name, is_read, indexed_at, mod_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            reason=excluded.reason, is_read=excluded.is_read, indexed_at=excluded.indexed_at,
            subject_uri=excluded.subject_uri, mod_json=excluded.mod_json,
            -- Nunca se pisa un texto ya resuelto con uno vacío (la caché puede no tener
            -- todavía la publicación afectada).
            content=CASE WHEN excluded.content <> '' THEN excluded.content
                         ELSE bluesky_notifications.content END;
        """
        params = (db_id, account_id, uri, cid, reason, reason_subject, subject_uri, contenido,
                  author_did, author_handle, author_name, is_read, indexed_at, mod_json)
        if conn is not None:
            conn.execute(sql, params)
        else:
            db.execute(sql, params)
        return {
            "id": db_id, "reason": reason, "author_name": author_name,
            "author_handle": author_handle, "indexed_at": indexed_at,
            "reason_subject": reason_subject, "subject_uri": subject_uri,
            "content": contenido, "is_read": bool(is_read), "mod_json": mod_json,
        }

    def contar_no_leidas_async(self, account_id: int, callback: Optional[Callable] = None):
        """Número de notificaciones sin leer."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.notification.get_unread_count()
            return _g(resp, "count", default=0)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def marcar_notificaciones_vistas_async(self, account_id: int, callback: Optional[Callable] = None):
        """Marca todas las notificaciones como vistas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.notification.update_seen({"seen_at": _ahora_iso()})
            db.execute("UPDATE bluesky_notifications SET is_read = 1 WHERE account_id = ?", (account_id,))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # HILO Y PERFIL
    # ------------------------------------------------------------------
    def _cargar_hilo_como_buffer(self, account_id, client, timeline_key, clave_cursor):
        """Carga una CONVERSACIÓN completa como buffer navegable (paridad con el
        'thread:' de Mastodon, petición de usuarios): getPostThread con ancestros y
        respuestas en ORDEN DE LECTURA (raíz → padres → post → respuestas en
        profundidad), guardado con sort_key para que la lista respete ese orden.

        La conversación se SUSTITUYE en cada carga (una respuesta borrada en el
        servidor no debe quedarse en el buffer) y no pagina (se trae entera, con los
        topes depth/parentHeight de la API oficial)."""
        uri = timeline_key.split(":", 1)[1]
        orden_hilo = self._orden_respuestas_hilo(account_id)
        resp = client.get_post_thread(uri=uri, depth=100, parent_height=100)
        thread = _g(resp, "thread")
        if _g(thread, "post") is None:
            # La RAÍZ ya no está (borrada, o de una cuenta bloqueada o que te bloquea): el
            # servidor devuelve notFoundPost/blockedPost y el buffer se quedaba vacío. Se carga
            # entonces desde la publicación con la que se abrió la conversación, que trae sus
            # padres hasta donde se pueda (el hueco de la raíz se indica en la lectura).
            from app.core.config_manager import config
            origen = next((c.get("origen") for c in config.get("bluesky", "thread_buffers", [])
                           if isinstance(c, dict) and c.get("uri") == uri and c.get("origen")), "")
            if origen and origen != uri:
                resp = client.get_post_thread(uri=origen, depth=100, parent_height=100)
                thread = _g(resp, "thread")
        orden = []

        def _subir(nodo):
            padre = _g(nodo, "parent")
            if padre:
                _subir(padre)
            p = _g(nodo, "post")
            if p:
                orden.append(p)

        padre = _g(thread, "parent")
        if padre:
            _subir(padre)
        principal = _g(thread, "post")
        if principal:
            orden.append(principal)

        def _bajar(nodo):
            for hijo in orden_hilo(_g(nodo, "replies", default=[]) or [], thread):
                p = _g(hijo, "post")
                if p:
                    orden.append(p)
                _bajar(hijo)

        _bajar(thread)

        posts: List[BlueskyPost] = []
        with db.transaccion() as _conn:
            _conn.execute("DELETE FROM bluesky_timeline_posts WHERE account_id = ? AND timeline = ?",
                          (account_id, timeline_key))
            # sort_key CRECE con el orden de lectura: la raíz (lo más antiguo) lleva el más
            # bajo, igual que en el resto de buffers ("sort_key más alto = más nuevo"). Así la
            # interfaz aplica la preferencia "más nuevos arriba/abajo" como en cualquier otro
            # buffer. Antes era base - idx (la raíz con el más alto) y la conversación salía
            # al revés de lo elegido en Preferencias, en los dos sentidos.
            for idx, pv in enumerate(orden):
                po = self._procesar_y_guardar_post(account_id, pv, conn=_conn)
                if po:
                    posts.append(po)
                    _conn.execute(
                        "INSERT OR IGNORE INTO bluesky_timeline_posts "
                        "(account_id, timeline, post_db_id, sort_key) VALUES (?,?,?,?)",
                        (account_id, timeline_key, po.id, idx + 1))
        self._sin_mas_paginas(clave_cursor, True)   # un hilo no pagina hacia atrás
        return posts

    def _orden_respuestas_hilo(self, account_id: int):
        """Calienta las preferencias de moderación (fuera de cualquier transacción) y devuelve
        una función que ordena las respuestas de cada nivel según threadViewPref.sort, como el
        cliente oficial: primero las del autor original, después las tuyas (B12)."""
        try:
            prefs = self.prefs_moderacion(account_id, permitir_red=True)
        except Exception:
            prefs = {}
        from .bluesky_moderacion import ordenar_respuestas
        sort = ((prefs or {}).get("thread_view") or {}).get("sort", "")
        yo = (prefs or {}).get("user_did", "")

        def _did(n):
            return _g(_g(_g(n, "post"), "author"), "did", default="") or ""

        def _ordenar(replies, raiz):
            op = _did(raiz)
            raiz_p = _g(raiz, "parent")
            while raiz_p is not None and _g(raiz_p, "post") is not None:
                op = _did(raiz_p)
                raiz_p = _g(raiz_p, "parent")
            return ordenar_respuestas(
                replies, sort, op, yo,
                like_de=lambda n: _g(_g(n, "post"), "like_count", "likeCount", default=0) or 0,
                fecha_de=lambda n: _g(_g(n, "post"), "indexed_at", "indexedAt", default="") or "",
                did_de=_did)
        return _ordenar

    def obtener_hilo_async(self, account_id: int, uri: str, callback: Optional[Callable] = None):
        """Obtiene el hilo de un post (root/parent/replies) como lista de posts ordenados."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            orden_hilo = self._orden_respuestas_hilo(account_id)
            resp = client.get_post_thread(uri=uri)
            thread = _g(resp, "thread")
            posts: List[BlueskyPost] = []

            def _subir(nodo):
                """Recoge ancestros (parents) de arriba a abajo."""
                padre = _g(nodo, "parent")
                if padre:
                    _subir(padre)
                p = _g(nodo, "post")
                if p:
                    po = self._procesar_y_guardar_post(account_id, p)
                    if po:
                        posts.append(po)

            def _bajar(nodo):
                """Recoge respuestas en profundidad."""
                for hijo in orden_hilo(_g(nodo, "replies", default=[]) or [], thread):
                    p = _g(hijo, "post")
                    if p:
                        po = self._procesar_y_guardar_post(account_id, p)
                        if po:
                            posts.append(po)
                    _bajar(hijo)

            padre = _g(thread, "parent")
            if padre:
                _subir(padre)
            principal = _g(thread, "post")
            if principal:
                po = self._procesar_y_guardar_post(account_id, principal)
                if po:
                    posts.append(po)
            _bajar(thread)
            return posts
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_perfil_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Obtiene los detalles de un perfil (did o handle)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            p = client.get_profile(actor)
            return {
                "did": _g(p, "did", default=""),
                "handle": _g(p, "handle", default=""),
                "display_name": _g(p, "display_name", "displayName", default=""),
                "description": _g(p, "description", default="") or "",
                "followers": _g(p, "followers_count", "followersCount", default=0),
                "following": _g(p, "follows_count", "followsCount", default=0),
                "posts": _g(p, "posts_count", "postsCount", default=0),
                "following_uri": _g(_g(p, "viewer"), "following", default="") or "",
                "blocking_uri": _g(_g(p, "viewer"), "blocking", default="") or "",
                "muted": bool(_g(_g(p, "viewer"), "muted", default=False)),
            }
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # PUBLICAR / RESPONDER / CITAR
    # ------------------------------------------------------------------
