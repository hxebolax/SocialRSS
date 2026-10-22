# -*- coding: utf-8 -*-
"""Grafo social de W Social: seguir, bloquear, silenciar, listas y paquetes de inicio.

Mixin de WSocialService. Todo lo que aquí se "crea" (seguir, bloquear, una lista...) es en
realidad un registro en tu repositorio de AT Protocol, así que deshacerlo consiste en borrar ese
registro: por eso se guarda su URI junto a cada cuenta.
"""
from app.services.wsocial.wsocial_common import (
    Any, Callable, Dict, List, Optional, db, WSocialServiceError, log, models,
    _g, _ahora_iso, rkey_de_uri)


class _SocialMixin:
    # ------------------------------------------------------------------
    # LISTAS DE CUENTAS (seguidores, seguidos, bloqueados, silenciados...)
    # ------------------------------------------------------------------
    def cargar_actores_async(self, account_id: int, bucket: str, actor: str = "",
                             cargar_mas: bool = False, limit: int = 100,
                             callback: Optional[Callable] = None):
        """Descarga una lista de cuentas y la cachea. `bucket` puede ser:

        followers, follows (de `actor`, o tuyos si no se indica), blocks, mutes,
        list:<uri> (miembros de una lista), listblocks, listmutes,
        knownfollowers:<actor>, suggested, suggestedfollows:<actor>.
        """
        clave_cursor = f"{account_id}:actores:{bucket}"

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            cursor = self._cursors.get(clave_cursor) if cargar_mas else None
            objetivo = actor or yo["did"]
            campo = "subjects"
            if bucket == "followers":
                resp = self._get_con_reintentos(account_id, lambda: client.app.bsky.graph.get_followers(
                    {"actor": objetivo, "limit": limit, "cursor": cursor}))
                cuentas = _g(resp, "followers", default=[]) or []
            elif bucket == "follows":
                resp = self._get_con_reintentos(account_id, lambda: client.app.bsky.graph.get_follows(
                    {"actor": objetivo, "limit": limit, "cursor": cursor}))
                cuentas = _g(resp, "follows", default=[]) or []
            elif bucket == "blocks":
                resp = self._get_con_reintentos(account_id, lambda: client.app.bsky.graph.get_blocks(
                    {"limit": limit, "cursor": cursor}))
                cuentas = _g(resp, "blocks", default=[]) or []
            elif bucket == "mutes":
                resp = self._get_con_reintentos(account_id, lambda: client.app.bsky.graph.get_mutes(
                    {"limit": limit, "cursor": cursor}))
                cuentas = _g(resp, "mutes", default=[]) or []
            elif bucket.startswith("list:"):
                resp = self._get_con_reintentos(account_id, lambda: client.app.bsky.graph.get_list(
                    {"list": bucket[5:], "limit": limit, "cursor": cursor}))
                cuentas = [_g(i, "subject") for i in (_g(resp, "items", default=[]) or [])]
            elif bucket.startswith("knownfollowers:"):
                resp = self._get_con_reintentos(account_id, lambda: client.app.bsky.graph.get_known_followers(
                    {"actor": bucket.split(":", 1)[1], "limit": limit, "cursor": cursor}))
                cuentas = _g(resp, "followers", default=[]) or []
            elif bucket.startswith("suggestedfollows:"):
                resp = self._get_con_reintentos(
                    account_id, lambda: client.app.bsky.graph.get_suggested_follows_by_actor(
                        {"actor": bucket.split(":", 1)[1]}))
                cuentas = _g(resp, "suggestions", default=[]) or []
            elif bucket == "suggested":
                resp = self._get_con_reintentos(account_id, lambda: client.app.bsky.actor.get_suggestions(
                    {"limit": limit, "cursor": cursor}))
                cuentas = _g(resp, "actors", default=[]) or []
            else:
                raise WSocialServiceError(f"Lista de cuentas desconocida: {bucket}")

            self._cursors[clave_cursor] = _g(resp, "cursor", default=None)
            salida = []
            with db.transaccion() as conn:
                if not cargar_mas:
                    conn.execute("DELETE FROM wsocial_actors WHERE account_id = ? AND bucket = ?",
                                 (account_id, bucket))
                base = 0 if not cargar_mas else (conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM wsocial_actors "
                    "WHERE account_id = ? AND bucket = ?", (account_id, bucket)).fetchone() or [0])[0]
                for i, a in enumerate(cuentas):
                    did = _g(a, "did", default="")
                    if not did:
                        continue
                    viewer = _g(a, "viewer", default=None)
                    verif = _g(a, "verification", default=None)
                    datos = {
                        "did": did,
                        "handle": _g(a, "handle", default=""),
                        "display_name": _g(a, "display_name", "displayName", default=""),
                        "description": (_g(a, "description", default="") or "").replace("\n", " "),
                        "following_uri": _g(viewer, "following", default="") or "",
                        "blocking_uri": _g(viewer, "blocking", default="") or "",
                        "muted": 1 if _g(viewer, "muted", default=False) else 0,
                        "verificado": 1 if str(_g(verif, "verified_status", "verifiedStatus",
                                                  default="")).lower() == "valid" else 0,
                    }
                    conn.execute(
                        "INSERT OR REPLACE INTO wsocial_actors (id, account_id, bucket, did, handle, "
                        "display_name, description, following_uri, blocking_uri, muted, verificado, "
                        "position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"{account_id}:{bucket}:{did}", account_id, bucket, did, datos["handle"],
                         datos["display_name"], datos["description"], datos["following_uri"],
                         datos["blocking_uri"], datos["muted"], datos["verificado"], base + i))
                    salida.append(datos)
            return salida
        self._tarea(_bg, callback)

    def relaciones_async(self, account_id: int, actores: List[str],
                         callback: Optional[Callable] = None):
        """Tu relación con varias cuentas de una vez (si las sigues y si te siguen)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            resp = client.app.bsky.graph.get_relationships(
                {"actor": yo["did"], "others": actores[:30]})
            salida = []
            for r in (_g(resp, "relationships", default=[]) or []):
                salida.append({
                    "did": _g(r, "did", default=""),
                    "sigo": bool(_g(r, "following", default="")),
                    "me_sigue": bool(_g(r, "followed_by", "followedBy", default="")),
                })
            return salida
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # ACCIONES SOBRE UNA CUENTA
    # ------------------------------------------------------------------
    # Bucket de la caché de actores que refleja cada estado (Bloqueados, Silenciados, Siguiendo).
    _BUCKET_DE_ESTADO = (("blocking_uri", "blocks"), ("muted", "mutes"), ("following_uri", "follows"))

    def _sincronizar_estado_actor(self, client, account_id: int, did: str,
                                  conocido: Optional[dict] = None) -> dict:
        """Tras seguir/bloquear/silenciar (o deshacerlo), pide al servidor el estado REAL de tu
        relación con la cuenta (viewer de app.bsky.actor.getProfile) y lo guarda en la caché de
        actores: en TODAS sus filas y en el bucket que lo refleja (la cuenta aparece o desaparece
        de Bloqueados/Silenciados/Siguiendo). Antes solo se hacía un UPDATE de las filas que ya
        existían y, si la cuenta no estaba cacheada, el menú seguía ofreciendo "Bloquear".
        'conocido' (p. ej. el uri del bloqueo recién creado) se usa si el perfil no responde.
        La consulta de red va ANTES de abrir la transacción de escritura."""
        try:
            p = client.app.bsky.actor.get_profile({"actor": did})
            viewer = _g(p, "viewer", default=None)
            verif = _g(p, "verification", default=None)
            d = {"did": _g(p, "did", default="") or did,
                 "handle": _g(p, "handle", default="") or "",
                 "display_name": _g(p, "display_name", "displayName", default="") or "",
                 "description": (_g(p, "description", default="") or "").replace("\n", " "),
                 "following_uri": _g(viewer, "following", default="") or "",
                 "blocking_uri": _g(viewer, "blocking", default="") or "",
                 "muted": bool(_g(viewer, "muted", default=False)),
                 "verificado": 1 if str(_g(verif, "verified_status", "verifiedStatus",
                                           default="")).lower() == "valid" else 0}
        except Exception as e:
            log.debug(f"W Social: no se pudo leer el perfil tras la acción de cuenta ({e}); "
                      f"se usa lo conocido.")
            fila = db.query_one(
                "SELECT handle, display_name, description, following_uri, blocking_uri, muted, "
                "verificado FROM wsocial_actors WHERE account_id = ? AND did = ? LIMIT 1",
                (account_id, did))
            d = {"did": did, "handle": fila[0] if fila else "",
                 "display_name": fila[1] if fila else "", "description": fila[2] if fila else "",
                 "following_uri": fila[3] if fila else "", "blocking_uri": fila[4] if fila else "",
                 "muted": bool(fila[5]) if fila else False,
                 "verificado": (fila[6] or 0) if fila else 0}
            d.update(conocido or {})
        with db.transaccion() as conn:
            conn.execute("UPDATE wsocial_actors SET following_uri = ?, blocking_uri = ?, muted = ? "
                         "WHERE account_id = ? AND did = ?",
                         (d.get("following_uri") or "", d.get("blocking_uri") or "",
                          1 if d.get("muted") else 0, account_id, did))
            for campo, bucket in self._BUCKET_DE_ESTADO:
                db_id = f"{account_id}:{bucket}:{did}"
                if d.get(campo):
                    if not conn.execute("SELECT 1 FROM wsocial_actors WHERE id = ?",
                                        (db_id,)).fetchone():
                        # Arriba del todo, como lo más reciente.
                        fila = conn.execute(
                            "SELECT COALESCE(MIN(position), 0) - 1 FROM wsocial_actors "
                            "WHERE account_id = ? AND bucket = ?", (account_id, bucket)).fetchone()
                        conn.execute(
                            "INSERT OR REPLACE INTO wsocial_actors (id, account_id, bucket, did, "
                            "handle, display_name, description, following_uri, blocking_uri, muted, "
                            "verificado, position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (db_id, account_id, bucket, did, d.get("handle") or "",
                             d.get("display_name") or "", d.get("description") or "",
                             d.get("following_uri") or "", d.get("blocking_uri") or "",
                             1 if d.get("muted") else 0, d.get("verificado") or 0,
                             fila[0] if fila else 0))
                else:
                    conn.execute("DELETE FROM wsocial_actors WHERE id = ?", (db_id,))
        return d

    def accion_cuenta_async(self, account_id: int, actor: str, accion: str,
                            callback: Optional[Callable] = None):
        """follow / unfollow / block / unblock / mute / unmute sobre una cuenta (handle o DID).
        Devuelve {"accion", "did", "handle", "display_name", "following_uri", "blocking_uri",
        "muted"} con el estado YA confirmado por el servidor y guardado en la caché."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            if not did:
                raise WSocialServiceError(f"No se ha encontrado la cuenta {actor}.")
            conocido = self._ejecutar_accion_cuenta(client, account_id, did, accion)
            estado = self._sincronizar_estado_actor(client, account_id, did, conocido)
            estado["accion"] = accion
            return estado
        self._tarea(_bg, callback)

    def _ejecutar_accion_cuenta(self, client, account_id: int, did: str, accion: str) -> dict:
        """Hace la acción en el servidor y devuelve lo que se sabe de ella (el uri del registro
        creado, o el campo vaciado) por si luego no se puede leer el perfil."""
        yo = self._yo(account_id)
        if accion in ("follow", "block"):
            coleccion = "app.bsky.graph.follow" if accion == "follow" else "app.bsky.graph.block"
            registro = (models.AppBskyGraphFollow.Record(created_at=_ahora_iso(), subject=did)
                        if accion == "follow"
                        else models.AppBskyGraphBlock.Record(created_at=_ahora_iso(), subject=did))
            resp = client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=yo["did"], collection=coleccion, rkey=self._generar_tid(),
                    record=registro))
            campo = "following_uri" if accion == "follow" else "blocking_uri"
            return {campo: _g(resp, "uri", default="") or ""}
        if accion in ("unfollow", "unblock"):
            campo = "following_uri" if accion == "unfollow" else "blocking_uri"
            coleccion = ("app.bsky.graph.follow" if accion == "unfollow"
                         else "app.bsky.graph.block")
            fila = db.query_one(
                f"SELECT {campo} FROM wsocial_actors WHERE account_id = ? AND did = ? "
                f"AND COALESCE({campo}, '') <> '' LIMIT 1", (account_id, did))
            uri = fila[0] if fila else ""
            if not uri:
                # Si no lo tenemos cacheado, se pregunta al perfil.
                perfil = client.app.bsky.actor.get_profile({"actor": did})
                viewer = _g(perfil, "viewer", default=None)
                uri = _g(viewer, "following" if accion == "unfollow" else "blocking", default="") or ""
            if not uri:
                raise WSocialServiceError(
                    "No sigues a esa cuenta." if accion == "unfollow"
                    else "Esa cuenta no está bloqueada.")
            client.com.atproto.repo.delete_record(
                models.ComAtprotoRepoDeleteRecord.Data(
                    repo=yo["did"], collection=coleccion, rkey=rkey_de_uri(uri)))
            return {campo: ""}
        if accion == "mute":
            client.app.bsky.graph.mute_actor({"actor": did})
            return {"muted": True}
        if accion == "unmute":
            client.app.bsky.graph.unmute_actor({"actor": did})
            return {"muted": False}
        raise WSocialServiceError(f"Acción desconocida sobre la cuenta: {accion}")

    def _resolver_did(self, client, actor: str) -> str:
        """DID de una cuenta a partir de su handle o de su propio DID."""
        actor = (actor or "").strip().lstrip("@")
        if actor.startswith("did:"):
            return actor
        cacheado = self._did_cache.get(actor)
        if cacheado and cacheado[0]:
            return cacheado[0]
        resp = client.com.atproto.identity.resolve_handle({"handle": actor})
        did = _g(resp, "did", default="")
        if did:
            import time as _t
            self._did_cache[actor] = (did, _t.time())
        return did

    def buscar_cuentas_async(self, account_id: int, texto: str, limit: int = 50,
                             typeahead: bool = False, callback: Optional[Callable] = None):
        """Busca cuentas. Con `typeahead` usa la búsqueda rápida pensada para autocompletar."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if typeahead:
                resp = client.app.bsky.actor.search_actors_typeahead({"q": texto, "limit": min(limit, 25)})
            else:
                resp = client.app.bsky.actor.search_actors({"q": texto, "limit": limit})
            salida = []
            for a in (_g(resp, "actors", default=[]) or []):
                viewer = _g(a, "viewer", default=None)
                salida.append({
                    "did": _g(a, "did", default=""),
                    "handle": _g(a, "handle", default=""),
                    "display_name": _g(a, "display_name", "displayName", default=""),
                    "description": (_g(a, "description", default="") or "").replace("\n", " "),
                    "following_uri": _g(viewer, "following", default="") or "",
                })
            return salida
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # LISTAS DE CURACIÓN
    # ------------------------------------------------------------------
    def cargar_listas_async(self, account_id: int, actor: str = "", incluir_ajenas: bool = True,
                            callback: Optional[Callable] = None):
        """Listas de una cuenta (las tuyas si no se indica otra), más las listas de bloqueo y de
        silencio a las que estás suscrito."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            salida = []
            resp = client.app.bsky.graph.get_lists({"actor": actor or yo["did"], "limit": 100})
            for l in (_g(resp, "lists", default=[]) or []):
                salida.append(self._lista_a_dict(l, "propia"))
            if incluir_ajenas and not actor:
                for metodo, etiqueta in ((client.app.bsky.graph.get_list_blocks, "bloqueo"),
                                         (client.app.bsky.graph.get_list_mutes, "silencio")):
                    try:
                        r = metodo({"limit": 100})
                        for l in (_g(r, "lists", default=[]) or []):
                            salida.append(self._lista_a_dict(l, etiqueta))
                    except Exception as e:
                        log.debug(f"No se pudieron cargar las listas de {etiqueta}: {e}")
            with db.transaccion() as conn:
                conn.execute("DELETE FROM wsocial_feeds WHERE account_id = ? AND kind = 'list'",
                             (account_id,))
                for i, l in enumerate(salida):
                    conn.execute(
                        "INSERT OR REPLACE INTO wsocial_feeds (id, account_id, kind, uri, name, "
                        "description, creator_handle, pinned, position) VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"{account_id}:list:{l['uri']}", account_id, "list", l["uri"], l["name"],
                         l["description"], l["creator"], 0, i))
            return salida
        self._tarea(_bg, callback)

    @staticmethod
    def _lista_a_dict(l, papel: str) -> Dict[str, Any]:
        return {
            "uri": _g(l, "uri", default=""),
            "cid": _g(l, "cid", default=""),
            "name": _g(l, "name", default=""),
            "description": (_g(l, "description", default="") or "").replace("\n", " "),
            "creator": _g(_g(l, "creator"), "handle", default=""),
            "proposito": str(_g(l, "purpose", default="")),
            "papel": papel,
            "miembros": _g(l, "list_item_count", "listItemCount", default=0),
        }

    def crear_lista_async(self, account_id: int, nombre: str, descripcion: str = "",
                          proposito: str = "curatelist", callback: Optional[Callable] = None):
        """Crea una lista. `proposito` puede ser curatelist (para leer), modlist (para bloquear o
        silenciar en bloque) o referencelist."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            purposes = {"curatelist": "app.bsky.graph.defs#curatelist",
                        "modlist": "app.bsky.graph.defs#modlist",
                        "referencelist": "app.bsky.graph.defs#referencelist"}
            registro = models.AppBskyGraphList.Record(
                name=nombre, description=(descripcion or None),
                purpose=purposes.get(proposito, purposes["curatelist"]),
                created_at=_ahora_iso())
            resp = client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.list",
                    rkey=self._generar_tid(), record=registro))
            return {"uri": _g(resp, "uri", default=""), "name": nombre}
        self._tarea(_bg, callback)

    def editar_lista_async(self, account_id: int, list_uri: str, nombre: str = None,
                           descripcion: str = None, callback: Optional[Callable] = None):
        """Cambia el nombre o la descripción de una lista propia."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            rkey = rkey_de_uri(list_uri)
            actual = client.com.atproto.repo.get_record(
                {"repo": yo["did"], "collection": "app.bsky.graph.list", "rkey": rkey})
            valor = _g(actual, "value", default=None)
            registro = models.AppBskyGraphList.Record(
                name=(nombre if nombre is not None else _g(valor, "name", default="")),
                description=(descripcion if descripcion is not None
                             else _g(valor, "description", default=None)),
                purpose=_g(valor, "purpose", default="app.bsky.graph.defs#curatelist"),
                created_at=_g(valor, "created_at", "createdAt", default=_ahora_iso()))
            client.com.atproto.repo.put_record(
                models.ComAtprotoRepoPutRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.list", rkey=rkey, record=registro))
            return True
        self._tarea(_bg, callback)

    def borrar_lista_async(self, account_id: int, list_uri: str, callback: Optional[Callable] = None):
        """Borra una lista propia."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            client.com.atproto.repo.delete_record(
                models.ComAtprotoRepoDeleteRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.list", rkey=rkey_de_uri(list_uri)))
            db.execute("DELETE FROM wsocial_feeds WHERE account_id = ? AND kind = 'list' AND uri = ?",
                       (account_id, list_uri))
            return True
        self._tarea(_bg, callback)

    def anadir_miembro_lista_async(self, account_id: int, list_uri: str, actor: str,
                                   callback: Optional[Callable] = None):
        """Añade una cuenta a una lista."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            did = self._resolver_did(client, actor)
            if not did:
                raise WSocialServiceError(f"No se ha encontrado la cuenta {actor}.")
            registro = models.AppBskyGraphListitem.Record(
                subject=did, list=list_uri, created_at=_ahora_iso())
            resp = client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.listitem",
                    rkey=self._generar_tid(), record=registro))
            return _g(resp, "uri", default="")
        self._tarea(_bg, callback)

    def quitar_miembro_lista_async(self, account_id: int, item_uri: str,
                                   callback: Optional[Callable] = None):
        """Quita una cuenta de una lista (borrando el registro de pertenencia)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            client.com.atproto.repo.delete_record(
                models.ComAtprotoRepoDeleteRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.listitem",
                    rkey=rkey_de_uri(item_uri)))
            return True
        self._tarea(_bg, callback)

    def miembros_lista_async(self, account_id: int, list_uri: str, callback: Optional[Callable] = None):
        """Cuentas de una lista, con la URI de pertenencia para poder quitarlas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = self._get_con_reintentos(
                account_id, lambda: client.app.bsky.graph.get_list({"list": list_uri, "limit": 100}))
            salida = []
            for item in (_g(resp, "items", default=[]) or []):
                sujeto = _g(item, "subject")
                salida.append({
                    "item_uri": _g(item, "uri", default=""),
                    "did": _g(sujeto, "did", default=""),
                    "handle": _g(sujeto, "handle", default=""),
                    "display_name": _g(sujeto, "display_name", "displayName", default=""),
                })
            return salida
        self._tarea(_bg, callback)

    def listas_con_pertenencia_async(self, account_id: int, actor: str,
                                     callback: Optional[Callable] = None):
        """Tus listas indicando en cuáles está ya esa cuenta (para añadir o quitar de un vistazo)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            resp = client.app.bsky.graph.get_lists_with_membership({"actor": did, "limit": 100})
            salida = []
            for l in (_g(resp, "lists_with_membership", "listsWithMembership", default=[]) or []):
                lista = _g(l, "list")
                salida.append({
                    "uri": _g(lista, "uri", default=""),
                    "name": _g(lista, "name", default=""),
                    "pertenece": bool(_g(l, "list_item", "listItem", default=None)),
                    "item_uri": _g(_g(l, "list_item", "listItem", default=None), "uri", default=""),
                })
            return salida
        self._tarea(_bg, callback)

    def suscribir_lista_async(self, account_id: int, list_uri: str, tipo: str = "mute",
                              activar: bool = True, callback: Optional[Callable] = None):
        """Suscribe (o cancela) una lista de moderación: silenciar o bloquear a todos sus miembros."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            if tipo == "mute":
                if activar:
                    client.app.bsky.graph.mute_actor_list({"list": list_uri})
                else:
                    client.app.bsky.graph.unmute_actor_list({"list": list_uri})
                return activar
            # Bloqueo por lista: es un registro del repositorio.
            if activar:
                resp = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"], collection="app.bsky.graph.listblock",
                        rkey=self._generar_tid(),
                        record=models.AppBskyGraphListblock.Record(
                            subject=list_uri, created_at=_ahora_iso())))
                return _g(resp, "uri", default="")
            resp = client.app.bsky.graph.get_list_blocks({"limit": 100})
            for l in (_g(resp, "lists", default=[]) or []):
                if _g(l, "uri", default="") == list_uri:
                    bloqueo = _g(_g(l, "viewer"), "blocked", default="")
                    if bloqueo:
                        client.com.atproto.repo.delete_record(
                            models.ComAtprotoRepoDeleteRecord.Data(
                                repo=yo["did"], collection="app.bsky.graph.listblock",
                                rkey=rkey_de_uri(bloqueo)))
                    return True
            return False
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # PAQUETES DE INICIO (starter packs)
    # ------------------------------------------------------------------
    def cargar_starter_packs_async(self, account_id: int, actor: str = "",
                                   callback: Optional[Callable] = None):
        """Paquetes de inicio de una cuenta: colecciones de gente y feeds para empezar."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            resp = client.app.bsky.graph.get_actor_starter_packs(
                {"actor": actor or yo["did"], "limit": 100})
            salida = []
            with db.transaccion() as conn:
                if not actor:
                    conn.execute("DELETE FROM wsocial_feeds WHERE account_id = ? AND kind = 'starterpack'",
                                 (account_id,))
                for i, sp in enumerate(_g(resp, "starter_packs", "starterPacks", default=[]) or []):
                    registro = _g(sp, "record", default=None)
                    datos = {
                        "uri": _g(sp, "uri", default=""),
                        "name": _g(registro, "name", default="") or "Paquete de inicio",
                        "description": (_g(registro, "description", default="") or "").replace("\n", " "),
                        "creator": _g(_g(sp, "creator"), "handle", default=""),
                        "cuentas": _g(sp, "joined_all_time_count", "joinedAllTimeCount", default=0),
                        "lista": _g(_g(sp, "list"), "uri", default=""),
                    }
                    if not actor:
                        conn.execute(
                            "INSERT OR REPLACE INTO wsocial_feeds (id, account_id, kind, uri, name, "
                            "description, creator_handle, pinned, position) VALUES (?,?,?,?,?,?,?,?,?)",
                            (f"{account_id}:starterpack:{datos['uri']}", account_id, "starterpack",
                             datos["uri"], datos["name"], datos["description"], datos["creator"], 0, i))
                    salida.append(datos)
            return salida
        self._tarea(_bg, callback)

    def detalle_starter_pack_async(self, account_id: int, uri: str,
                                   callback: Optional[Callable] = None):
        """Contenido de un paquete de inicio: sus cuentas y sus feeds."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.graph.get_starter_pack({"starter_pack": uri})
            sp = _g(resp, "starter_pack", "starterPack", default=None)
            registro = _g(sp, "record", default=None)
            cuentas = []
            lista_uri = _g(_g(sp, "list"), "uri", default="")
            if lista_uri:
                r = client.app.bsky.graph.get_list({"list": lista_uri, "limit": 100})
                for item in (_g(r, "items", default=[]) or []):
                    s = _g(item, "subject")
                    cuentas.append({"did": _g(s, "did", default=""),
                                    "handle": _g(s, "handle", default=""),
                                    "display_name": _g(s, "display_name", "displayName", default="")})
            feeds = []
            for f in (_g(sp, "feeds", default=[]) or []):
                feeds.append({"uri": _g(f, "uri", default=""),
                              "name": _g(f, "display_name", "displayName", default="")})
            return {"uri": uri, "name": _g(registro, "name", default=""),
                    "description": _g(registro, "description", default=""),
                    "lista": lista_uri, "cuentas": cuentas, "feeds": feeds}
        self._tarea(_bg, callback)

    def crear_starter_pack_async(self, account_id: int, nombre: str, descripcion: str,
                                 actores: List[str], callback: Optional[Callable] = None):
        """Crea un paquete de inicio con las cuentas indicadas (crea antes su lista)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            lista = client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.list", rkey=self._generar_tid(),
                    record=models.AppBskyGraphList.Record(
                        name=nombre, description=(descripcion or None),
                        purpose="app.bsky.graph.defs#referencelist", created_at=_ahora_iso())))
            lista_uri = _g(lista, "uri", default="")
            for actor in actores:
                did = self._resolver_did(client, actor)
                if not did:
                    continue
                client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"], collection="app.bsky.graph.listitem",
                        rkey=self._generar_tid(),
                        record=models.AppBskyGraphListitem.Record(
                            subject=did, list=lista_uri, created_at=_ahora_iso())))
            resp = client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.starterpack",
                    rkey=self._generar_tid(),
                    record=models.AppBskyGraphStarterpack.Record(
                        name=nombre, description=(descripcion or None), list=lista_uri,
                        created_at=_ahora_iso())))
            return {"uri": _g(resp, "uri", default=""), "lista": lista_uri}
        self._tarea(_bg, callback)

    def buscar_starter_packs_async(self, account_id: int, texto: str,
                                   callback: Optional[Callable] = None):
        """Busca paquetes de inicio por texto."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.graph.search_starter_packs({"q": texto, "limit": 50})
            return [{"uri": _g(sp, "uri", default=""),
                     "name": _g(_g(sp, "record"), "name", default=""),
                     "creator": _g(_g(sp, "creator"), "handle", default="")}
                    for sp in (_g(resp, "starter_packs", "starterPacks", default=[]) or [])]
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # FEEDS
    # ------------------------------------------------------------------
    def cargar_feeds_async(self, account_id: int, callback: Optional[Callable] = None):
        """Feeds guardados y fijados por el usuario (según sus preferencias) más sus datos."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            prefs = client.app.bsky.actor.get_preferences()
            guardados, fijados = [], []
            for p in (_g(prefs, "preferences", default=[]) or []):
                tipo = str(_g(p, "py_type", "$type", default=""))
                if tipo.endswith("savedFeedsPrefV2"):
                    for item in (_g(p, "items", default=[]) or []):
                        valor = _g(item, "value", default="")
                        if _g(item, "type", default="") in ("feed", "list") and valor:
                            guardados.append(valor)
                            if _g(item, "pinned", default=False):
                                fijados.append(valor)
                elif tipo.endswith("savedFeedsPref"):
                    guardados.extend(list(_g(p, "saved", default=[]) or []))
                    fijados.extend(list(_g(p, "pinned", default=[]) or []))
            uris_feed = [u for u in dict.fromkeys(guardados) if "app.bsky.feed.generator" in u]
            salida = []
            if uris_feed:
                resp = client.app.bsky.feed.get_feed_generators({"feeds": uris_feed[:50]})
                for f in (_g(resp, "feeds", default=[]) or []):
                    salida.append({
                        "uri": _g(f, "uri", default=""),
                        "name": _g(f, "display_name", "displayName", default=""),
                        "description": (_g(f, "description", default="") or "").replace("\n", " "),
                        "creator": _g(_g(f, "creator"), "handle", default=""),
                        "likes": _g(f, "like_count", "likeCount", default=0),
                        "pinned": 1 if _g(f, "uri", default="") in fijados else 0,
                    })
            with db.transaccion() as conn:
                conn.execute("DELETE FROM wsocial_feeds WHERE account_id = ? AND kind = 'feed'",
                             (account_id,))
                for i, f in enumerate(salida):
                    conn.execute(
                        "INSERT OR REPLACE INTO wsocial_feeds (id, account_id, kind, uri, name, "
                        "description, creator_handle, pinned, position) VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"{account_id}:feed:{f['uri']}", account_id, "feed", f["uri"], f["name"],
                         f["description"], f["creator"], f["pinned"], i))
            return salida
        self._tarea(_bg, callback)

    def feeds_populares_async(self, account_id: int, texto: str = "", limit: int = 50,
                              callback: Optional[Callable] = None):
        """Feeds populares o sugeridos, para descubrir cosas nuevas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            try:
                resp = client.app.bsky.unspecced.get_popular_feed_generators(
                    {"limit": limit, "query": (texto or None)})
            except Exception:
                resp = client.app.bsky.feed.get_suggested_feeds({"limit": limit})
            return [{"uri": _g(f, "uri", default=""),
                     "name": _g(f, "display_name", "displayName", default=""),
                     "description": (_g(f, "description", default="") or "").replace("\n", " "),
                     "creator": _g(_g(f, "creator"), "handle", default=""),
                     "likes": _g(f, "like_count", "likeCount", default=0)}
                    for f in (_g(resp, "feeds", default=[]) or [])]
        self._tarea(_bg, callback)

    def guardar_feed_async(self, account_id: int, uri: str, guardar: bool = True,
                           fijar: bool = False, callback: Optional[Callable] = None):
        """Guarda o quita un feed de tus feeds (y opcionalmente lo fija), respetando el resto de
        preferencias del usuario."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            prefs = client.app.bsky.actor.get_preferences()
            lista = list(_g(prefs, "preferences", default=[]) or [])
            tocado = False
            for p in lista:
                if not str(_g(p, "py_type", "$type", default="")).endswith("savedFeedsPrefV2"):
                    continue
                items = list(_g(p, "items", default=[]) or [])
                items = [i for i in items if _g(i, "value", default="") != uri]
                if guardar:
                    items.append(models.AppBskyActorDefs.SavedFeed(
                        id=self._generar_tid(), type="feed", value=uri, pinned=bool(fijar)))
                p.items = items
                tocado = True
            if not tocado and guardar:
                lista.append(models.AppBskyActorDefs.SavedFeedsPrefV2(items=[
                    models.AppBskyActorDefs.SavedFeed(
                        id=self._generar_tid(), type="feed", value=uri, pinned=bool(fijar))]))
            client.app.bsky.actor.put_preferences({"preferences": lista})
            return guardar
        self._tarea(_bg, callback)
