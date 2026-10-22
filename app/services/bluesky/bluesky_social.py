# -*- coding: utf-8 -*-
"""Actores, feeds, listas, perfil propio y app passwords.

Mixin de BlueskyService (parte de la refactorización modular).
"""
from app.services.bluesky.bluesky_common import (
    Any, Callable, Dict, Optional, db, security, BlueskyServiceError, task_manager, config,
    log, models, _g, _ahora_iso)


class _SocialMixin:
    def _mutar_registro_perfil(self, client, did: str, mutador: Callable) -> bool:
        """Muta el registro de perfil de forma NO DESTRUCTIVA (auditoría Bluesky BSKY-001):

        1. Lee el registro crudo y su CID.
        2. Lo vuelca a un dict con alias camelCase CONSERVANDO los campos que SocialRSS no
           conoce (pronouns, website, joinedViaStarterPack, futuros del lexicón): los
           modelos de atproto llevan extra='allow', así que model_dump no los pierde.
           Antes se reconstruía un Record nuevo solo con nombre/descición/avatar/banner y
           cualquier otro campo se BORRABA del servidor.
        3. Aplica `mutador(registro)` (cambia solo las claves solicitadas).
        4. Escribe con swapRecord para no pisar una edición hecha desde otro cliente; ante
           InvalidSwap, relee, reaplica y reintenta UNA vez.

        Todas las mutaciones de perfil quedan serializadas con un candado del servicio."""
        import threading as _th
        if not hasattr(self, "_perfil_mutex"):
            self._perfil_mutex = _th.RLock()
        with self._perfil_mutex:
            ultimo_error = None
            for intento in (0, 1):
                swap_cid = None
                registro = {"$type": "app.bsky.actor.profile"}
                try:
                    resp = client.com.atproto.repo.get_record({
                        "repo": did, "collection": "app.bsky.actor.profile", "rkey": "self"})
                    swap_cid = _g(resp, "cid")
                    val = _g(resp, "value")
                    if val is not None:
                        try:
                            registro = val.model_dump(by_alias=True, exclude_none=True)
                        except Exception:
                            registro = dict(val) if isinstance(val, dict) else registro
                        registro["$type"] = "app.bsky.actor.profile"
                except Exception:
                    pass  # perfil sin registro todavía: se crea desde cero
                registro.setdefault("createdAt", _ahora_iso())
                mutador(registro)
                data = {"repo": did, "collection": "app.bsky.actor.profile",
                        "rkey": "self", "record": registro}
                if swap_cid:
                    data["swap_record"] = swap_cid
                try:
                    client.com.atproto.repo.put_record(data)
                    return True
                except Exception as e:
                    ultimo_error = e
                    if intento == 0 and "invalidswap" in str(e).replace(" ", "").lower():
                        continue   # otro cliente editó a la vez: releer y reaplicar una vez
                    raise
            if ultimo_error:
                raise ultimo_error
            return False

    @staticmethod
    def _blob_a_dict(blob):
        """Un BlobRef recién subido, como dict con alias (para el registro crudo)."""
        if blob is None or isinstance(blob, dict):
            return blob
        try:
            return blob.model_dump(by_alias=True, exclude_none=True)
        except Exception:
            return blob

    def _actor_a_dict(self, a) -> Dict[str, Any]:
        """Normaliza un ProfileView a diccionario."""
        viewer = _g(a, "viewer", default=None)
        return {
            "did": _g(a, "did", default=""),
            "handle": _g(a, "handle", default=""),
            "display_name": _g(a, "display_name", "displayName", default="") or "",
            "description": _g(a, "description", default="") or "",
            "following_uri": _g(viewer, "following", default="") or "",
            "blocking_uri": _g(viewer, "blocking", default="") or "",
            "muted": bool(_g(viewer, "muted", default=False)),
        }

    def cargar_actores_async(self, account_id: int, bucket: str,
                            actor: Optional[str] = None, limit: int = 80,
                            cargar_mas: bool = False,
                            callback: Optional[Callable] = None):
        """Carga y cachea una lista de cuentas: followers, follows, blocks, mutes.

        Paginación por cursor (auditoría 10.3: estas listas estaban TRUNCADAS a una
        página de 80): con cargar_mas=True usa el cursor guardado y AÑADE la página
        siguiente detrás de lo ya cargado, sin borrar. Devuelve
        {"actores": [...], "has_more": bool, "cargar_mas": bool}."""
        clave = f"{account_id}:actores:{bucket}:{actor or ''}"

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            obj = actor or self._yo(account_id)["did"]
            cursor = self._cursors.get(clave) if cargar_mas else None
            if cargar_mas and not cursor:
                return {"actores": [], "has_more": False, "cargar_mas": True}
            if bucket == "followers":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.get_followers(obj, cursor=cursor, limit=limit))
                actores = _g(resp, "followers", default=[])
            elif bucket == "follows":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.get_follows(obj, cursor=cursor, limit=limit))
                actores = _g(resp, "follows", default=[])
            elif bucket == "blocks":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.graph.get_blocks(
                        {"limit": limit, "cursor": cursor}))
                actores = _g(resp, "blocks", default=[])
            elif bucket == "mutes":
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.graph.get_mutes(
                        {"limit": limit, "cursor": cursor}))
                actores = _g(resp, "mutes", default=[])
            else:
                raise BlueskyServiceError(f"Bucket de actores desconocido: {bucket}")
            nuevo_cursor = _g(resp, "cursor")
            self._cursors[clave] = nuevo_cursor

            # Refrescar/ampliar la caché de este bucket en UNA transacción. En la primera
            # página se sustituye todo; al cargar más se añade detrás (position correlativa).
            salida = []
            with db.transaccion():
                if cargar_mas:
                    fila = db.query_one(
                        "SELECT COALESCE(MAX(position), -1) FROM bluesky_actors "
                        "WHERE account_id = ? AND bucket = ?", (account_id, bucket))
                    base = (fila[0] if fila else -1) + 1
                else:
                    db.execute("DELETE FROM bluesky_actors WHERE account_id = ? AND bucket = ?",
                               (account_id, bucket))
                    base = 0
                for pos, a in enumerate(actores):
                    d = self._actor_a_dict(a)
                    db_id = f"{account_id}:{bucket}:{d['did']}"
                    db.execute("""
                        INSERT OR REPLACE INTO bluesky_actors
                        (id, account_id, bucket, did, handle, display_name, description,
                         following_uri, blocking_uri, muted, position)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (db_id, account_id, bucket, d["did"], d["handle"], d["display_name"],
                          d["description"], d["following_uri"], d["blocking_uri"],
                          1 if d["muted"] else 0, base + pos))
                    salida.append(d)
            return {"actores": salida, "has_more": bool(nuevo_cursor), "cargar_mas": cargar_mas}
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"bsky:actores:{clave}:{1 if cargar_mas else 0}")

    # Las PREFERENCIAS DE MODERACIÓN (prefs_moderacion) viven en bluesky_preferencias.

    def obtener_feeds_guardados_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lee los feeds personalizados guardados/fijados del usuario y los cachea."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            prefs = client.app.bsky.actor.get_preferences()
            uris, pinned_map = [], {}
            for p in _g(prefs, "preferences", default=[]):
                items = _g(p, "items", default=None)
                if items:  # SavedFeedsPrefV2
                    for it in items:
                        if _g(it, "type") == "feed":
                            val = _g(it, "value")
                            if val:
                                uris.append(val)
                                pinned_map[val] = bool(_g(it, "pinned", default=False))
            # Resolver nombres de los generadores (en lotes).
            nombres = {}
            for i in range(0, len(uris), 25):
                lote = uris[i:i + 25]
                try:
                    gens = client.app.bsky.feed.get_feed_generators({"feeds": lote})
                    for g in _g(gens, "feeds", default=[]):
                        nombres[_g(g, "uri")] = {
                            "name": _g(g, "display_name", "displayName", default="") or _g(g, "uri"),
                            "creator": _g(_g(g, "creator"), "handle", default=""),
                        }
                except Exception as e:
                    log.debug(f"No se pudieron resolver nombres de feeds: {e}")
            salida = []
            with db.transaccion():
                db.execute("DELETE FROM bluesky_feeds WHERE account_id = ? AND kind = 'feed'", (account_id,))
                for pos, uri in enumerate(uris):
                    info = nombres.get(uri, {"name": uri, "creator": ""})
                    db.execute("""
                        INSERT OR REPLACE INTO bluesky_feeds
                        (id, account_id, kind, uri, name, creator_handle, pinned, position)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (f"{account_id}:{uri}", account_id, "feed", uri, info["name"],
                          info["creator"], 1 if pinned_map.get(uri) else 0, pos))
                    salida.append({"uri": uri, "name": info["name"]})
            # Reflejar en config para que el árbol los pinte (paridad Mastodon lists_cache).
            config.set("bluesky", "saved_feeds",
                       [{"uri": s["uri"], "name": s["name"]} for s in salida])
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _editar_saved_feeds(self, client, transform):
        """Lee las preferencias, transforma la lista de SavedFeedsPrefV2 (feeds/listas guardados
        y fijados) y las vuelve a guardar. 'transform' recibe y devuelve la lista de items
        (cada uno con id, type, value, pinned). Mantiene el resto de preferencias intactas.
        Bajo el candado de preferencias (B17): leer → modificar → escribir sin pisar otra
        edición simultánea (moderación, palabras silenciadas...)."""
        with self._mutex_prefs():
            self._editar_saved_feeds_sin_lock(client, transform)

    def _editar_saved_feeds_sin_lock(self, client, transform):
        prefs_resp = client.app.bsky.actor.get_preferences()
        prefs = list(_g(prefs_resp, "preferences", default=[]))
        idx = None
        for i, p in enumerate(prefs):
            if "savedFeedsPrefV2" in str(_g(p, "py_type", "$type", default="")):
                idx = i
                break
        items = list(_g(prefs[idx], "items", default=[]) or []) if idx is not None else []
        items = transform(items)
        nueva = models.AppBskyActorDefs.SavedFeedsPrefV2(items=items)
        if idx is not None:
            prefs[idx] = nueva
        else:
            prefs.append(nueva)
        client.app.bsky.actor.put_preferences(
            models.AppBskyActorPutPreferences.Data(preferences=prefs))

    def guardar_feed_async(self, account_id: int, feed_uri: str, tipo: str = "feed",
                           fijar: bool = False, callback: Optional[Callable] = None):
        """Añade un feed (o lista) a tus guardados. Si ya está, solo actualiza si va fijado.
        'tipo': 'feed' (generador) o 'list' (lista como timeline)."""
        import uuid as _uuid

        def _bg(ctx):
            client = self.obtener_cliente(account_id)

            def _t(items):
                for i, it in enumerate(items):
                    if _g(it, "value") == feed_uri:
                        # Ya guardado: si se pide FIJARLO, actualizar pinned (antes se
                        # devolvía la lista sin tocar y el fijado se perdía en silencio;
                        # auditoría Bluesky §14).
                        if fijar and not _g(it, "pinned", default=False):
                            items[i] = models.AppBskyActorDefs.SavedFeed(
                                id=_g(it, "id", default=_uuid.uuid4().hex[:16]),
                                type=_g(it, "type", default=tipo),
                                value=feed_uri, pinned=True)
                        return items
                items.append(models.AppBskyActorDefs.SavedFeed(
                    id=_uuid.uuid4().hex[:16], type=tipo, value=feed_uri, pinned=bool(fijar)))
                return items
            self._editar_saved_feeds(client, _t)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def quitar_feed_guardado_async(self, account_id: int, feed_uri: str,
                                   callback: Optional[Callable] = None):
        """Quita un feed/lista de tus guardados."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            self._editar_saved_feeds(client, lambda items: [it for it in items
                                                            if _g(it, "value") != feed_uri])
            db.execute("DELETE FROM bluesky_feeds WHERE account_id = ? AND uri = ?", (account_id, feed_uri))
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def fijar_feed_async(self, account_id: int, feed_uri: str, fijar: bool = True,
                         callback: Optional[Callable] = None):
        """Fija o desfija un feed guardado (los fijados salen primero, como en la app oficial)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)

            def _t(items):
                for it in items:
                    if _g(it, "value") == feed_uri:
                        # Los items del SDK son objetos; recrear con pinned cambiado.
                        it.pinned = bool(fijar)
                return items
            self._editar_saved_feeds(client, _t)
            db.execute("UPDATE bluesky_feeds SET pinned = ? WHERE account_id = ? AND uri = ?",
                       (1 if fijar else 0, account_id, feed_uri))
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def reordenar_feeds_async(self, account_id: int, orden_uris: list,
                              callback: Optional[Callable] = None):
        """Reordena los feeds guardados según la lista de URIs indicada (los que no aparezcan
        se conservan al final en su orden actual)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)

            def _t(items):
                por_uri = {_g(it, "value"): it for it in items}
                nuevos = [por_uri[u] for u in orden_uris if u in por_uri]
                nuevos += [it for it in items if _g(it, "value") not in set(orden_uris)]
                return nuevos
            self._editar_saved_feeds(client, _t)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def buscar_feeds_async(self, account_id: int, query: str = "", limit: int = 30,
                          callback: Optional[Callable] = None):
        """Busca feeds populares (generadores) por texto."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.unspecced.get_popular_feed_generators(
                {"query": query, "limit": limit} if query else {"limit": limit})
            return [{
                "uri": _g(g, "uri"),
                "name": _g(g, "display_name", "displayName", default="") or _g(g, "uri"),
                "description": _g(g, "description", default="") or "",
                "creator": _g(_g(g, "creator"), "handle", default=""),
                "likes": _g(g, "like_count", "likeCount", default=0),
            } for g in _g(resp, "feeds", default=[])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_listas_async(self, account_id: int, actor: Optional[str] = None,
                            callback: Optional[Callable] = None):
        """Obtiene las listas del usuario y las cachea."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            obj = actor or self._yo(account_id)["did"]
            resp = client.app.bsky.graph.get_lists({"actor": obj, "limit": 100})
            salida = []
            with db.transaccion():
                db.execute("DELETE FROM bluesky_feeds WHERE account_id = ? AND kind = 'list'", (account_id,))
                for pos, l in enumerate(_g(resp, "lists", default=[])):
                    uri = _g(l, "uri")
                    nombre = _g(l, "name", default="") or uri
                    db.execute("""
                        INSERT OR REPLACE INTO bluesky_feeds
                        (id, account_id, kind, uri, name, description, creator_handle, position)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (f"{account_id}:{uri}", account_id, "list", uri, nombre,
                          _g(l, "description", default="") or "",
                          _g(_g(l, "creator"), "handle", default=""), pos))
                    salida.append({"uri": uri, "name": nombre})
            config.set("bluesky", "lists_cache",
                       [{"uri": s["uri"], "name": s["name"]} for s in salida])
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def crear_lista_async(self, account_id: int, nombre: str, descripcion: str = "",
                         callback: Optional[Callable] = None):
        """Crea una lista de curación (purpose=curatelist)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            record = models.AppBskyGraphList.Record(
                name=nombre, description=descripcion or None,
                purpose="app.bsky.graph.defs#curatelist", created_at=_ahora_iso())
            client.com.atproto.repo.create_record(models.ComAtprotoRepoCreateRecord.Data(
                repo=yo["did"], collection="app.bsky.graph.list", record=record))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def borrar_lista_async(self, account_id: int, list_uri: str, callback: Optional[Callable] = None):
        """Borra una lista propia."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            rkey = list_uri.rsplit("/", 1)[-1]
            client.com.atproto.repo.delete_record(models.ComAtprotoRepoDeleteRecord.Data(
                repo=yo["did"], collection="app.bsky.graph.list", rkey=rkey))
            db.execute("DELETE FROM bluesky_feeds WHERE account_id = ? AND uri = ?", (account_id, list_uri))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_feeds_sugeridos_async(self, account_id: int, limit: int = 50,
                                      callback: Optional[Callable] = None):
        """Feeds recomendados por Bluesky (getSuggestedFeeds)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.feed.get_suggested_feeds({"limit": min(int(limit), 100)})
            return [{
                "uri": _g(f, "uri"),
                "name": _g(f, "display_name", "displayName", default="") or _g(f, "uri"),
                "description": _g(f, "description", default="") or "",
                "creator": _g(_g(f, "creator"), "handle", default=""),
                "likes": _g(f, "like_count", "likeCount", default=0),
            } for f in _g(resp, "feeds", default=[])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_interacciones_post_async(self, account_id: int, post_uri: str,
                                         tipo: str, limit: int = 80,
                                         callback: Optional[Callable] = None):
        """Cuentas que interactuaron con un post. tipo: 'likes' | 'reposts' | 'quotes'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if tipo == "reposts":
                resp = client.app.bsky.feed.get_reposted_by({"uri": post_uri, "limit": min(int(limit), 100)})
                actores = _g(resp, "reposted_by", "repostedBy", default=[])
                return [self._actor_a_dict(a) for a in actores]
            if tipo == "quotes":
                resp = client.app.bsky.feed.get_quotes({"uri": post_uri, "limit": min(int(limit), 100)})
                return [self._actor_a_dict(_g(p, "author")) for p in _g(resp, "posts", default=[])
                        if _g(p, "author", default=None) is not None]
            # likes (por defecto)
            resp = client.app.bsky.feed.get_likes({"uri": post_uri, "limit": min(int(limit), 100)})
            return [self._actor_a_dict(_g(l, "actor")) for l in _g(resp, "likes", default=[])
                    if _g(l, "actor", default=None) is not None]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_miembros_lista_async(self, account_id: int, list_uri: str,
                                     callback: Optional[Callable] = None):
        """Cuentas que pertenecen a una lista. Cada dict lleva 'item_uri' (registro listitem) para poder quitarla."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cursor = None
            salida = []
            while True:
                params = {"list": list_uri, "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                resp = client.app.bsky.graph.get_list(params)
                for it in _g(resp, "items", default=[]):
                    subj = _g(it, "subject", default=None)
                    if subj is None:
                        continue
                    d = self._actor_a_dict(subj)
                    d["item_uri"] = _g(it, "uri", default="")
                    salida.append(d)
                cursor = _g(resp, "cursor", default=None)
                if not cursor or len(salida) >= 1000:
                    break
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _resolver_did(self, client, actor: str) -> str:
        """Devuelve el DID de un actor a partir de su handle o DID."""
        actor = (actor or "").strip().lstrip("@")
        if actor.startswith("did:"):
            return actor
        resp = client.com.atproto.identity.resolve_handle({"handle": actor})
        return _g(resp, "did", default="")

    def anadir_miembro_lista_async(self, account_id: int, list_uri: str, actor: str,
                                   callback: Optional[Callable] = None):
        """Añade una cuenta (handle o DID) a una lista de curación creando un registro listitem."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            did = self._resolver_did(client, actor)
            if not did:
                raise ValueError("No se ha podido identificar esa cuenta.")
            record = models.AppBskyGraphListitem.Record(
                subject=did, list=list_uri, created_at=_ahora_iso())
            client.com.atproto.repo.create_record(models.ComAtprotoRepoCreateRecord.Data(
                repo=yo["did"], collection="app.bsky.graph.listitem", record=record))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def quitar_miembro_lista_async(self, account_id: int, item_uri: str,
                                   callback: Optional[Callable] = None):
        """Quita una cuenta de una lista borrando su registro listitem (item_uri)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            rkey = item_uri.rsplit("/", 1)[-1]
            client.com.atproto.repo.delete_record(models.ComAtprotoRepoDeleteRecord.Data(
                repo=yo["did"], collection="app.bsky.graph.listitem", rkey=rkey))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def silenciar_lista_async(self, account_id: int, list_uri: str, silenciar: bool = True,
                              callback: Optional[Callable] = None):
        """Suscribe (silenciar=True) o cancela la suscripción a una lista de moderación de silencio."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if silenciar:
                client.app.bsky.graph.mute_actor_list({"list": list_uri})
            else:
                client.app.bsky.graph.unmute_actor_list({"list": list_uri})
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_starter_packs_async(self, account_id: int, actor: Optional[str] = None,
                                    callback: Optional[Callable] = None):
        """Obtiene los starter packs creados por el usuario."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            obj = actor or self._yo(account_id)["did"]
            resp = client.app.bsky.graph.get_actor_starter_packs({"actor": obj, "limit": 50})
            salida = []
            for sp in _g(resp, "starter_packs", "starterPacks", default=[]):
                rec = _g(sp, "record")
                salida.append({
                    "uri": _g(sp, "uri"),
                    "name": _g(rec, "name", default="") or "Starter pack",
                    "members": _g(sp, "list_item_count", "listItemCount", default=0),
                })
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def buscar_actores_async(self, account_id: int, query: str, limit: int = 25,
                            callback: Optional[Callable] = None):
        """Busca cuentas por texto/handle."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.actor.search_actors({"q": query, "limit": limit})
            return [self._actor_a_dict(a) for a in _g(resp, "actors", default=[])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cargar_tendencias_async(self, account_id: int, callback: Optional[Callable] = None):
        """Obtiene los temas en tendencia."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            try:
                resp = client.app.bsky.unspecced.get_trends({"limit": 25})
                trends = _g(resp, "trends", default=[])
            except Exception:
                resp = client.app.bsky.unspecced.get_trending_topics({"limit": 25})
                trends = _g(resp, "topics", default=[])
            return [{
                "topic": _g(t, "topic", "display_name", "displayName", default=""),
                "link": _g(t, "link", default=""),
                "posts": _g(t, "post_count", "postCount", default=0),
            } for t in trends]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # TANDA 5 — PERFIL PROPIO Y CUENTA
    # ------------------------------------------------------------------
    def obtener_perfil_propio_async(self, account_id: int, callback: Optional[Callable] = None):
        """Devuelve los datos editables del perfil propio + info de sesión."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            display_name, description = "", ""
            try:
                rec = client.com.atproto.repo.get_record({
                    "repo": yo["did"], "collection": "app.bsky.actor.profile", "rkey": "self"})
                val = _g(rec, "value")
                display_name = _g(val, "display_name", "displayName", default="") or ""
                description = _g(val, "description", default="") or ""
            except Exception as e:
                log.debug(f"No hay record de perfil aún: {e}")
            email = ""
            try:
                ses = client.com.atproto.server.get_session()
                email = _g(ses, "email", default="") or ""
            except Exception:
                pass
            return {"did": yo["did"], "handle": yo["handle"],
                    "display_name": display_name, "description": description, "email": email}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def editar_perfil_async(self, account_id: int, display_name: str, description: str,
                           avatar_path: Optional[str] = None, banner_path: Optional[str] = None,
                           callback: Optional[Callable] = None):
        """Actualiza nombre, biografía y, opcionalmente, avatar/banner del perfil.

        NO destructivo: solo toca las claves editadas; pronouns, website, pinnedPost,
        labels y cualquier campo futuro del lexicón se conservan tal cual (BSKY-001)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            avatar_nuevo = self._blob_a_dict(self._subir_blob(client, avatar_path, para_perfil=True)) if avatar_path else None
            banner_nuevo = self._blob_a_dict(self._subir_blob(client, banner_path, para_perfil=True)) if banner_path else None

            def _mutar(registro):
                if display_name:
                    registro["displayName"] = display_name
                else:
                    registro.pop("displayName", None)
                if description:
                    registro["description"] = description
                else:
                    registro.pop("description", None)
                if avatar_nuevo is not None:
                    registro["avatar"] = avatar_nuevo
                if banner_nuevo is not None:
                    registro["banner"] = banner_nuevo

            return self._mutar_registro_perfil(client, yo["did"], _mutar)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cambiar_handle_async(self, account_id: int, nuevo_handle: str, callback: Optional[Callable] = None):
        """Cambia el handle de la cuenta y actualiza el nombre local."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            handle = nuevo_handle.strip().lstrip("@")
            client.update_handle(handle)
            self._me[account_id] = {"did": self._yo(account_id)["did"], "handle": handle}
            security.guardar_credencial(f"bluesky:{account_id}", "handle", handle)
            db.execute("UPDATE accounts SET name = ? WHERE id = ?", (f"@{handle}", account_id))
            return handle
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def listar_app_passwords_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista las contraseñas de aplicación de la cuenta."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.com.atproto.server.list_app_passwords()
            return [{
                "name": _g(p, "name", default=""),
                "created_at": _g(p, "created_at", "createdAt", default=""),
                "privileged": bool(_g(p, "privileged", default=False)),
            } for p in _g(resp, "passwords", default=[])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def crear_app_password_async(self, account_id: int, nombre: str, callback: Optional[Callable] = None):
        """Crea una contraseña de aplicación nueva y devuelve su valor (mostrar una vez)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.com.atproto.server.create_app_password(
                models.ComAtprotoServerCreateAppPassword.Data(name=nombre))
            return {"name": _g(resp, "name", default=nombre),
                    "password": _g(resp, "password", default="")}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def revocar_app_password_async(self, account_id: int, nombre: str, callback: Optional[Callable] = None):
        """Revoca una contraseña de aplicación."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.com.atproto.server.revoke_app_password(
                models.ComAtprotoServerRevokeAppPassword.Data(name=nombre))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # APIs ADICIONALES DE ATPROTO (descubrimiento social y notificaciones)
    # ------------------------------------------------------------------
    def seguidores_conocidos_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Seguidores que TÚ sigues y que también siguen a 'actor' (graph.get_known_followers)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            res = client.app.bsky.graph.get_known_followers({"actor": did, "limit": 50})
            return [self._actor_a_dict(a) for a in (_g(res, "followers", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def sugerencias_seguir_actor_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Cuentas sugeridas para seguir, parecidas a 'actor' (graph.get_suggested_follows_by_actor)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            res = client.app.bsky.graph.get_suggested_follows_by_actor({"actor": did})
            return [self._actor_a_dict(a) for a in (_g(res, "suggestions", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def relaciones_async(self, account_id: int, otros_dids: list, callback: Optional[Callable] = None):
        """Relación (sigo / me sigue) con varias cuentas a la vez (graph.get_relationships)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.app.bsky.graph.get_relationships(
                {"actor": self._yo(account_id)["did"], "others": otros_dids})
            return [{"did": _g(r, "did", default=""),
                     "siguiendo": bool(_g(r, "following", default=None)),
                     "me_sigue": bool(_g(r, "followed_by", default=None))}
                    for r in (_g(res, "relationships", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def buscar_actores_typeahead_async(self, account_id: int, query: str, callback: Optional[Callable] = None):
        """Autocompletado rápido de cuentas al escribir (actor.search_actors_typeahead)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.app.bsky.actor.search_actors_typeahead({"q": query, "limit": 10})
            return [self._actor_a_dict(a) for a in (_g(res, "actors", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def feeds_de_actor_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Feeds personalizados creados por una cuenta (feed.get_actor_feeds)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            res = client.app.bsky.feed.get_actor_feeds({"actor": did, "limit": 50})
            return [{"uri": _g(fe, "uri", default=""), "name": _g(fe, "display_name", default=""),
                     "description": _g(fe, "description", default=""),
                     "likes": _g(fe, "like_count", default=0)}
                    for fe in (_g(res, "feeds", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def listar_suscripciones_actividad_async(self, account_id: int, callback: Optional[Callable] = None):
        """Cuentas a cuya ACTIVIDAD estás suscrito (te avisan al publicar)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.app.bsky.notification.list_activity_subscriptions({"limit": 100})
            return [self._actor_a_dict(a) for a in (_g(res, "subscriptions", default=[]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def suscribir_actividad_async(self, account_id: int, actor: str, posts: bool = True,
                                  replies: bool = False, callback: Optional[Callable] = None):
        """Suscribe/actualiza la notificación de actividad de una cuenta (avísame cuando publique).
        posts y replies a False = darse de baja (notification.put_activity_subscription)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            client.app.bsky.notification.put_activity_subscription(
                {"subject": did, "activity_subscription": {"post": bool(posts), "reply": bool(replies)}})
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def preferencias_notif_async(self, account_id: int, callback: Optional[Callable] = None):
        """Preferencias de notificaciones del servidor (notification.get_preferences)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return client.app.bsky.notification.get_preferences()
        task_manager.start_task(_bg, callback=callback, pass_context=True)
