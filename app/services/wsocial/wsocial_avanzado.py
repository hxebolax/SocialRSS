# -*- coding: utf-8 -*-
"""Descubrimiento, datos de la cuenta y utilidades de W Social.

Mixin de WSocialService: tendencias, sugerencias, búsquedas, copia del repositorio, exportación
de los datos del chat y las funciones de contactos que W ofrece (con el cuidado que merecen: son
datos personales de terceros).
"""
from app.services.wsocial.wsocial_common import (
    Any, Callable, Dict, List, Optional, db, json, log, models, WSocialServiceError,
    _g, _ahora_iso)


class _AvanzadoMixin:
    # ------------------------------------------------------------------
    # DESCUBRIMIENTO
    # ------------------------------------------------------------------
    def tendencias_async(self, account_id: int, limit: int = 25,
                         callback: Optional[Callable] = None):
        """Temas del momento. Se prueban las dos formas que ofrece el protocolo (tendencias con
        datos y temas sueltos), porque no todos los servidores exponen las dos."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            salida = []
            try:
                resp = client.app.bsky.unspecced.get_trends({"limit": limit})
                for t in (_g(resp, "trends", default=[]) or []):
                    salida.append({
                        "nombre": _g(t, "display_name", "displayName", default="") or _g(t, "topic", default=""),
                        "enlace": _g(t, "link", default=""),
                        "publicaciones": _g(t, "post_count", "postCount", default=0),
                        "categoria": _g(t, "category", default=""),
                        "estado": str(_g(t, "status", default="")),
                    })
            except Exception as e:
                log.debug(f"W Social: sin tendencias con datos: {e}")
            if not salida:
                resp = client.app.bsky.unspecced.get_trending_topics({"limit": limit})
                for t in (_g(resp, "topics", default=[]) or []):
                    salida.append({"nombre": _g(t, "topic", default=""),
                                   "enlace": _g(t, "link", default=""),
                                   "publicaciones": 0, "categoria": "", "estado": ""})
            return salida
        self._tarea(_bg, callback)

    def sugerencias_cuentas_async(self, account_id: int, limit: int = 50,
                                  callback: Optional[Callable] = None):
        """Cuentas sugeridas para seguir."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.actor.get_suggestions({"limit": limit})
            return [{"did": _g(a, "did", default=""), "handle": _g(a, "handle", default=""),
                     "display_name": _g(a, "display_name", "displayName", default=""),
                     "description": (_g(a, "description", default="") or "").replace("\n", " ")}
                    for a in (_g(resp, "actors", default=[]) or [])]
        self._tarea(_bg, callback)

    def paquetes_sugeridos_async(self, account_id: int, limit: int = 25,
                                 callback: Optional[Callable] = None):
        """Paquetes de inicio sugeridos (una forma rápida de empezar a seguir gente)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.unspecced.get_suggested_starter_packs({"limit": limit})
            return [{"uri": _g(sp, "uri", default=""),
                     "name": _g(_g(sp, "record"), "name", default=""),
                     "creator": _g(_g(sp, "creator"), "handle", default="")}
                    for sp in (_g(resp, "starter_packs", "starterPacks", default=[]) or [])]
        self._tarea(_bg, callback)

    def buscar_publicaciones_async(self, account_id: int, texto: str, orden: str = "latest",
                                   autor: str = "", desde: str = "", hasta: str = "",
                                   idioma: str = "", limit: int = 50,
                                   callback: Optional[Callable] = None):
        """Búsqueda de publicaciones con los filtros que admite el protocolo: por autor, por
        fechas, por idioma y ordenada por recientes o por popularidad."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            params = {"q": texto, "limit": limit, "sort": (orden or "latest")}
            if autor:
                params["author"] = autor.lstrip("@")
            if desde:
                params["since"] = desde
            if hasta:
                params["until"] = hasta
            if idioma:
                params["lang"] = idioma
            resp = self._get_con_reintentos(
                account_id, lambda: client.app.bsky.feed.search_posts(params))
            salida = []
            with db.transaccion() as conn:
                for p in (_g(resp, "posts", default=[]) or []):
                    post = self._procesar_y_guardar_post(account_id, p, conn=conn)
                    if post:
                        salida.append(post)
            return salida
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # DATOS DE LA CUENTA
    # ------------------------------------------------------------------
    def exportar_repositorio_async(self, account_id: int, destino: str,
                                   callback: Optional[Callable] = None):
        """Descarga TODO tu repositorio (publicaciones, me gusta, seguimientos...) como archivo
        CAR. Es la copia de seguridad completa que permite el protocolo y también lo que hace
        falta para llevarse la cuenta a otro servidor."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            datos = client.com.atproto.sync.get_repo({"did": yo["did"]})
            with open(destino, "wb") as f:
                f.write(datos if isinstance(datos, (bytes, bytearray)) else bytes(datos))
            import os
            return {"ruta": destino, "bytes": os.path.getsize(destino)}
        self._tarea(_bg, callback, sonidos=True)

    def exportar_chat_async(self, account_id: int, destino: str,
                            callback: Optional[Callable] = None):
        """Exporta los datos de tus conversaciones a un archivo (formato del servicio de chat)."""
        def _bg(ctx):
            chat = self._chat_capaz(account_id)
            datos = chat.chat.bsky.actor.export_account_data()
            with open(destino, "wb") as f:
                f.write(datos if isinstance(datos, (bytes, bytearray)) else str(datos).encode("utf-8"))
            import os
            return {"ruta": destino, "bytes": os.path.getsize(destino)}
        self._tarea(_bg, callback, sonidos=True)

    def listar_registros_async(self, account_id: int, coleccion: str, limit: int = 100,
                               callback: Optional[Callable] = None):
        """Registros de una colección de tu repositorio (por ejemplo, todos tus me gusta).
        Sirve para el mantenimiento avanzado y para saber qué guarda de ti el servidor."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            resp = client.com.atproto.repo.list_records(
                {"repo": yo["did"], "collection": coleccion, "limit": limit})
            return [{"uri": _g(r, "uri", default=""), "cid": _g(r, "cid", default=""),
                     "valor": self._a_dict(_g(r, "value", default=None))}
                    for r in (_g(resp, "records", default=[]) or [])]
        self._tarea(_bg, callback)

    def describir_repositorio_async(self, account_id: int, actor: str = "",
                                    callback: Optional[Callable] = None):
        """Qué colecciones tiene un repositorio y en qué servidor está alojado."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            resp = client.com.atproto.repo.describe_repo({"repo": actor or yo["did"]})
            return {"did": _g(resp, "did", default=""),
                    "handle": _g(resp, "handle", default=""),
                    "colecciones": [str(c) for c in (_g(resp, "collections", default=[]) or [])],
                    "handle_correcto": bool(_g(resp, "handle_is_correct", "handleIsCorrect",
                                               default=True))}
        self._tarea(_bg, callback)

    def estado_alojamiento_async(self, account_id: int, callback: Optional[Callable] = None):
        """Estado del repositorio en el servidor (activo, desactivado, en migración)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            resp = client.com.atproto.sync.get_repo_status({"did": yo["did"]})
            return {"activo": bool(_g(resp, "active", default=True)),
                    "estado": str(_g(resp, "status", default="") or ""),
                    "revision": _g(resp, "rev", default="")}
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # CONTACTOS (datos personales: siempre con consentimiento explícito)
    # ------------------------------------------------------------------
    def estado_contactos_async(self, account_id: int, callback: Optional[Callable] = None):
        """Si la cuenta tiene contactos sincronizados con el servicio de W y desde cuándo."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.contact.get_sync_status()
            return {"sincronizado": bool(_g(resp, "synced", default=False)),
                    "fecha": _g(resp, "last_synced_at", "lastSyncedAt", default=""),
                    "contactos": _g(resp, "contact_count", "contactCount", default=0)}
        self._tarea(_bg, callback)

    def coincidencias_contactos_async(self, account_id: int, callback: Optional[Callable] = None):
        """Cuentas de W que coinciden con contactos ya sincronizados."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.contact.get_matches({"limit": 100})
            salida = []
            for m in (_g(resp, "matches", default=[]) or []):
                actor = _g(m, "actor", default=None) or m
                salida.append({"did": _g(actor, "did", default=""),
                               "handle": _g(actor, "handle", default=""),
                               "nombre": _g(actor, "display_name", "displayName", default="")})
            return salida
        self._tarea(_bg, callback)

    def descartar_coincidencia_async(self, account_id: int, did: str,
                                     callback: Optional[Callable] = None):
        """Descarta una coincidencia de contactos para que no vuelva a proponerse."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.contact.dismiss_match({"actor": did})
            return True
        self._tarea(_bg, callback)

    def borrar_datos_contactos_async(self, account_id: int, callback: Optional[Callable] = None):
        """Borra del servidor los contactos que se hubieran subido. Se ofrece siempre junto a la
        sincronización: es el derecho a retirar unos datos que, además, son de terceros."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.contact.remove_data()
            return True
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # LECTURA DE LA CACHÉ LOCAL (para pintar sin red)
    # ------------------------------------------------------------------
    def posts_de_buffer(self, account_id: int, timeline_type: str, limite: int = 500) -> list:
        """Publicaciones cacheadas de un buffer, en el orden que fijó el servidor."""
        return db.query("""
            SELECT p.id, p.author_name, p.author_handle, p.content, p.created_at, p.indexed_at,
                   p.like_count, p.repost_count, p.reply_count, p.quote_count, p.liked_uri,
                   p.reposted_uri, p.bookmarked, p.media_json, p.external_json, p.urls_json,
                   p.repost_by_name, p.repost_by_handle, p.labels, p.uri, p.cid, p.reply_root_uri,
                   p.quote_uri, p.verificado, p.author_did
            FROM wsocial_timeline_posts t
            JOIN wsocial_posts p ON p.id = t.post_db_id
            WHERE t.account_id = ? AND t.timeline = ?
            ORDER BY t.position ASC LIMIT ?
        """, (account_id, timeline_type, limite))

    def contar_buffer(self, account_id: int, timeline_type: str) -> int:
        """Cuántas publicaciones hay guardadas de un buffer."""
        fila = db.query_one("SELECT COUNT(*) FROM wsocial_timeline_posts "
                            "WHERE account_id = ? AND timeline = ?", (account_id, timeline_type))
        return (fila[0] if fila else 0) or 0

    def borrar_buffer(self, account_id: int, timeline_type: str):
        """Olvida el contenido de un buffer (no borra nada en el servidor)."""
        db.execute("DELETE FROM wsocial_timeline_posts WHERE account_id = ? AND timeline = ?",
                   (account_id, timeline_type))

    def buscar_local(self, texto: str, limite: int = 200) -> list:
        """Búsqueda en lo ya descargado (índice de texto completo). Es lo que usa la búsqueda
        global de la aplicación, que no debe depender de la red."""
        patron = (texto or "").strip()
        if not patron:
            return []
        try:
            return db.query("""
                SELECT f.post_db_id, p.author_name, p.author_handle, p.content, p.indexed_at,
                       p.account_id
                FROM wsocial_posts_fts f JOIN wsocial_posts p ON p.id = f.post_db_id
                WHERE wsocial_posts_fts MATCH ? ORDER BY p.indexed_at DESC LIMIT ?
            """, (patron, limite))
        except Exception:
            # Si el texto tiene caracteres que el índice no acepta, se cae a una búsqueda simple.
            like = f"%{patron}%"
            return db.query("""
                SELECT id, author_name, author_handle, content, indexed_at, account_id
                FROM wsocial_posts WHERE content LIKE ? ORDER BY indexed_at DESC LIMIT ?
            """, (like, limite))
