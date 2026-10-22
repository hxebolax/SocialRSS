# -*- coding: utf-8 -*-
"""Perfil, reportes, hashtags, marcadores y notif. avanzadas.

Mixin de MastodonService (parte de la refactorización modular).
"""
import logging
import json
from typing import List, Dict, Any, Optional, Callable
from app.core.database import db
from app.core.errors import MastodonServiceError
from app.core.task_manager import task_manager
from app.core.models import MastodonStatus


log = logging.getLogger(__name__)

class _PerfilMixin:
    def obtener_detalles_autor_async(self, account_id: int, toot_db_id: str, callback: Optional[Callable] = None):
        """Obtiene la ficha del autor de un toot (bio, seguidores, etc.). callback(dict, err)."""
        row = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row or not row[0]:
            if callback:
                callback(None, Exception("No se encontró el autor."))
            return
        self.obtener_detalles_cuenta_async(account_id, row[0], callback=callback)

    def describir_media_url_async(self, media_url: str, callback: Optional[Callable] = None):
        """Genera una descripción (texto alternativo) de una imagen ya subida a la
        instancia, descargándola y pasándola al servicio de IA. callback(texto, err)."""
        def _bg(ctx):
            import tempfile, os, requests
            from app.services.ai.ai_service import ai_service
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(media_url, headers=headers, timeout=60)
            resp.raise_for_status()
            ext = os.path.splitext(media_url.split("?")[0])[1] or ".jpg"
            ruta = os.path.join(tempfile.gettempdir(), "altgen_" + str(abs(hash(media_url)))[:12] + ext)
            with open(ruta, "wb") as f:
                f.write(resp.content)
            return ai_service.describir_imagen(ruta)

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- BÚSQUEDAS ---
    def buscar_statuses_fts(self, account_id: int, query_text: str, limit: int = 50) -> List[MastodonStatus]:
        """Busca toots usando SQLite FTS5.

        El filtro por CUENTA se aplica DENTRO de la consulta FTS, antes del LIMIT
        (auditoría A12): antes se tomaban los 50 mejores resultados GLOBALES y luego se
        filtraba la cuenta, así que coincidencias de otras cuentas podían ocupar el cupo
        y esconder resultados de la cuenta activa."""
        sql_fts = """
        SELECT f.status_db_id, f.rank
        FROM mastodon_statuses_fts f
        JOIN mastodon_statuses s ON s.id = f.status_db_id
        WHERE mastodon_statuses_fts MATCH ? AND s.account_id = ?
        ORDER BY f.rank
        LIMIT ?
        """
        # Sanitizar: cada término va entre comillas FTS y las comillas INTERNAS se doblan
        # (una comilla suelta en la búsqueda producía sintaxis FTS inválida).
        words = [w for w in query_text.split() if w]
        if not words:
            return []
        safe_query = " ".join('"' + w.replace('"', '""') + '"' for w in words)

        statuses = []
        try:
            results = db.query(sql_fts, (safe_query, account_id, limit))
            if not results:
                return []
                
            placeholders = ",".join("?" for _ in results)
            ids = [r[0] for r in results]
            
            sql_status = f"""
            SELECT id, account_id, status_id, uri, author_name, author_username, content, created_at, visibility, nsfw, spoiler_text, favourited, reblogged, bookmarked, media_json
            FROM mastodon_statuses
            WHERE account_id = ? AND id IN ({placeholders})
            """
            rows = db.query(sql_status, (account_id, *ids))
            
            # Ordenar por FTS rank
            dict_rows = {r[0]: r for r in rows}
            for db_id, _ in results:
                if db_id in dict_rows:
                    r = dict_rows[db_id]
                    statuses.append(MastodonStatus(
                        id=r[0], account_id=r[1], status_id=r[2], uri=r[3], author_name=r[4],
                        author_username=r[5], content=r[6], created_at=r[7], visibility=r[8],
                        nsfw=bool(r[9]), spoiler_text=r[10], favourited=bool(r[11]),
                        reblogged=bool(r[12]), bookmarked=bool(r[13]),
                        media_attachments=json.loads(r[14]) if r[14] else []
                    ))
        except Exception as e:
            log.error(f"Error en búsqueda FTS de Mastodon: {e}")
        return statuses

    # ============================================================================
    # FUNCIONES AÑADIDAS (paridad con FastSM / Mastodon.py 2.x)
    # ============================================================================

    @staticmethod
    def _cuenta_a_dict(c) -> Dict[str, Any]:
        """Convierte una cuenta de la API en un dict ligero para las listas."""
        return {
            "id": c.get("id"),
            "acct": c.get("acct", ""),
            "display": c.get("display_name") or c.get("username", "") or c.get("acct", ""),
            "note": c.get("note", "") or "",
            "followers": c.get("followers_count", 0),
            "following": c.get("following_count", 0),
            "url": c.get("url", "") or "",
        }

    def _resolver_cuenta(self, client, acct: str):
        """Devuelve la cuenta (API) a partir de su acct (@usuario@dominio), o None."""
        acct = (acct or "").lstrip("@")
        if not acct:
            return None
        try:
            return client.account_lookup(acct)
        except Exception:
            try:
                res = client.account_search(acct, limit=1)
                return res[0] if res else None
            except Exception:
                return None

    # --- Reportar ---
    def reglas_instancia_async(self, account_id: int, callback: Optional[Callable] = None):
        """Normas del servidor como [{"id", "text"}] (para marcar cuáles incumple una denuncia)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return [{"id": str(r.get("id")), "text": r.get("text") or ""}
                    for r in (self._api(client, "instance_rules")() or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def reportar_toot_async(self, account_id: int, toot_db_id: str, comentario: str = "",
                            reenviar: bool = False, callback: Optional[Callable] = None,
                            categoria: Optional[str] = None, reglas: Optional[List[str]] = None):
        """Reporta una publicación (y a su autor) a la instancia. categoria: spam, legal,
        violation u other; reglas: ids de las normas incumplidas (solo con violation)."""
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback: callback(None, Exception("Toot no encontrado."))
            return
        status_id = row[0]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            st = client.status(status_id)
            cuenta_id = st["account"]["id"]
            extra = {}
            if categoria:
                extra["category"] = categoria
            if categoria == "violation" and reglas:
                extra["rule_ids"] = list(reglas)
            client.report(cuenta_id, status_ids=[status_id], comment=comentario or "", forward=reenviar, **extra)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    # --- Perfil propio ---
    def obtener_mi_perfil_async(self, account_id: int, callback: Optional[Callable] = None):
        """Devuelve los datos editables de tu perfil (nombre, biografía y campos)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            me = client.account_verify_credentials()
            fuente = me.get("source", {}) or {}
            campos = []
            for f in (fuente.get("fields") or me.get("fields") or []):
                campos.append({"name": f.get("name", ""), "value": f.get("value", "")})
            return {
                "display_name": me.get("display_name", "") or "",
                "note": fuente.get("note", "") or "",
                "locked": bool(me.get("locked")),
                "bot": bool(me.get("bot")),
                "discoverable": bool(me.get("discoverable")),
                "fields": campos,
                # Opciones de privacidad y descubrimiento (4.1-4.2).
                "indexable": bool(me.get("indexable") if me.get("indexable") is not None
                                  else fuente.get("indexable")),
                "hide_collections": bool(me.get("hide_collections")),
                # Valores por defecto al publicar (source[...]); quote_policy desde 4.5.
                "default_privacy": fuente.get("privacy") or "public",
                "default_sensitive": bool(fuente.get("sensitive")),
                "default_language": fuente.get("language") or "",
                "default_quote_policy": fuente.get("quote_policy") or "",
                # Texto alternativo del avatar y de la cabecera (4.6, API 9).
                "avatar_description": me.get("avatar_description") or "",
                "header_description": me.get("header_description") or "",
                "max_profile_fields": int((self.capacidades(account_id) or {}).get("max_profile_fields") or 4),
                "admite_descripciones": self.version_api(account_id) >= 9,
                "admite_citas": self.version_api(account_id) >= 7,
            }
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # Opciones extra de account_update_credentials (Mastodon.py 2.2) que acepta el editor.
    _OPCIONES_PERFIL = ("indexable", "hide_collections", "default_privacy", "default_sensitive",
                        "default_language", "default_quote_policy")

    def editar_mi_perfil_async(self, account_id: int, display_name: str, note: str,
                               locked=None, bot=None, discoverable=None, fields=None,
                               avatar_path: str = None, header_path: str = None,
                               callback: Optional[Callable] = None, opciones: dict = None,
                               descripciones: dict = None):
        """Actualiza tu perfil de Mastodon (nombre, biografía, opciones, campos, avatar/banner).

        opciones: {indexable, hide_collections, default_privacy, default_sensitive,
        default_language, default_quote_policy}; solo se envían las que no sean None.
        descripciones: {avatar_description, header_description} (texto alternativo de las
        imágenes del perfil, Mastodon 4.6+, vía PATCH /api/v1/profile)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            kwargs = {"display_name": display_name, "note": note}
            if locked is not None: kwargs["locked"] = locked
            if bot is not None: kwargs["bot"] = bot
            if discoverable is not None: kwargs["discoverable"] = discoverable
            # OJO: distinguir None (no tocar los campos) de [] (el usuario los BORRÓ todos).
            # Con "if fields:" una lista vacía se omitía y el servidor conservaba los campos
            # antiguos, así que era imposible eliminarlos (auditoría Mastodon A14).
            # SIN recortar a 4: desde 4.6 el máximo lo fija cada servidor; recortar aquí hacía
            # perder los campos 5 y siguientes al guardar. El servidor valida su propio límite.
            if fields is not None:
                kwargs["fields"] = [(f.get("name", ""), f.get("value", "")) for f in fields]
            if avatar_path: kwargs["avatar"] = avatar_path
            if header_path: kwargs["header"] = header_path
            for clave in self._OPCIONES_PERFIL:
                valor = (opciones or {}).get(clave)
                if valor is not None and valor != "":
                    kwargs[clave] = valor
            client.account_update_credentials(**kwargs)
            # Texto alternativo del avatar y la cabecera: solo existe en /api/v1/profile (4.6).
            desc = {k: v for k, v in (descripciones or {}).items()
                    if k in ("avatar_description", "header_description") and v is not None}
            if desc:
                self._peticion_directa(client, "PATCH", "/api/v1/profile", desc)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def quitar_avatar_async(self, account_id: int, callback: Optional[Callable] = None):
        """Quita la foto de perfil (account_delete_avatar)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            self._api(client, "account_delete_avatar")()
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def quitar_cabecera_async(self, account_id: int, callback: Optional[Callable] = None):
        """Quita la imagen de cabecera/banner (account_delete_header)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            self._api(client, "account_delete_header")()
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def hashtags_destacados_de_async(self, account_id: int, target, callback: Optional[Callable] = None):
        """Hashtags destacados en el perfil de OTRA cuenta (account_featured_tags). `target` puede ser
        un id numérico o un acct (@usuario o usuario@instancia), que se resuelve antes."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            tid = target
            if isinstance(target, str) and not str(target).isdigit():
                cuenta = client.account_lookup(str(target).lstrip("@"))
                tid = cuenta.get("id") if cuenta else None
            if not tid:
                return []
            res = self._api(client, "account_featured_tags")(tid)
            return [{"nombre": t.get("name") or "", "url": t.get("url") or ""} for t in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- Publicaciones programadas ---
    def obtener_programados_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista las publicaciones programadas pendientes."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.scheduled_statuses()
            programados = []
            for s in (res or []):
                params = s.get("params", {}) or {}
                programados.append({
                    "id": s.get("id"),
                    "scheduled_at": str(s.get("scheduled_at", "")),
                    "text": params.get("text", "") or "",
                    "visibility": params.get("visibility", "") or "",
                })
            return programados
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cancelar_programado_async(self, account_id: int, sched_id, callback: Optional[Callable] = None):
        """Cancela una publicación programada."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.scheduled_status_delete(sched_id)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def reprogramar_programado_async(self, account_id: int, sched_id, nueva_fecha,
                                     callback: Optional[Callable] = None):
        """Cambia la fecha/hora de una publicación programada SIN cancelarla ni rehacerla
        (scheduled_status_update). 'nueva_fecha' es un datetime (hora local)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "scheduled_status_update")(sched_id, nueva_fecha)
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    # --- Hashtags seguidos (nativo) ---
    def seguir_hashtag_async(self, account_id: int, hashtag: str, seguir: bool = True,
                             callback: Optional[Callable] = None):
        """Sigue o deja de seguir un hashtag (sus publicaciones entran en tu Inicio)."""
        nombre = (hashtag or "").lstrip("#").strip()

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if seguir:
                client.tag_follow(nombre)
            else:
                client.tag_unfollow(nombre)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def obtener_hashtags_seguidos_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista los hashtags que sigues."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.followed_tags()
            return [{"name": t.get("name", ""), "url": t.get("url", "")} for t in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- Marcadores de lectura sincronizados ---
    def reportar_marcador_lectura(self, account_id: int, timeline: str, last_id: str):
        """Guarda en el servidor la posición de lectura de un timeline ('home' o
        'notifications') para sincronizarla con otros clientes. No bloquea ni avisa: se
        llama en segundo plano al cargar/actualizar el buffer."""
        if timeline not in ("home", "notifications") or not last_id:
            return

        def _bg(ctx):
            try:
                client = self.obtener_cliente(account_id)
                client.markers_set(timeline, last_id)
            except Exception as e:
                log.debug(f"No se pudo guardar el marcador de lectura: {e}")
            return True
        task_manager.start_task(_bg, pass_context=True)

    def obtener_marcador_async(self, account_id: int, timeline: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.markers_get([timeline])
            m = res.get(timeline) if isinstance(res, dict) else None
            return m.get("last_read_id") if m else None
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- Descubrimiento ---
    def cargar_trending_tags_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.trending_tags()
            salida = []
            for t in (res or []):
                usos = 0
                hist = t.get("history") or []
                if hist:
                    try:
                        usos = sum(int(h.get("uses", 0)) for h in hist[:2])
                    except Exception:
                        usos = 0
                salida.append({"name": t.get("name", ""), "usos": usos})
            return salida
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cargar_trending_links_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.trending_links()
            return [{"title": l.get("title", "") or l.get("url", ""), "url": l.get("url", "")}
                    for l in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cargar_directorio_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.directory(limit=50, order="active", local=False)
            return [self._cuenta_a_dict(c) for c in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def buscar_cuentas_async(self, account_id: int, query: str, limit: int = 40,
                             resolve: bool = True, callback: Optional[Callable] = None):
        """Busca CUENTAS por nombre, @usuario o usuario@instancia (account_search de la API oficial).
        Con resolve=True, si escribes un acct remoto completo (usuario@instancia) el servidor lo trae
        aunque no lo conociera todavía. Devuelve la lista de cuentas en el formato estándar."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.account_search(query, limit=limit, resolve=resolve)
            return [self._cuenta_a_dict(c) for c in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cargar_sugerencias_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.suggestions()
            cuentas = []
            for s in (res or []):
                c = s.get("account", s) if isinstance(s, dict) else s
                cuentas.append(self._cuenta_a_dict(c))
            return cuentas
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- Interacciones de una publicación ---
    def obtener_interacciones_async(self, account_id: int, toot_db_id: str, tipo: str,
                                    callback: Optional[Callable] = None):
        """tipo: 'reblog' (quién impulsó) o 'favourite' (quién marcó favorito)."""
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback: callback(None, Exception("Toot no encontrado."))
            return
        status_id = row[0]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.status_reblogged_by(status_id) if tipo == "reblog" else client.status_favourited_by(status_id)
            return [self._cuenta_a_dict(c) for c in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- Historial de ediciones ---
    def obtener_historial_async(self, account_id: int, toot_db_id: str, callback: Optional[Callable] = None):
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback: callback(None, Exception("Toot no encontrado."))
            return
        status_id = row[0]

        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            res = client.status_history(status_id)
            versiones = []
            for v in (res or []):
                try:
                    texto = html_a_texto(v.get("content", "") or "")
                except Exception:
                    texto = v.get("content", "") or ""
                versiones.append({"created_at": str(v.get("created_at", "")), "content": texto,
                                  "spoiler": v.get("spoiler_text", "") or ""})
            return versiones
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- Sobre una cuenta (autor) ---
    def nota_cuenta_async(self, account_id: int, acct: str, texto: str, callback: Optional[Callable] = None):
        """Establece (o borra, con texto vacío) tu nota personal sobre una cuenta."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            c = self._resolver_cuenta(client, acct)
            if not c:
                raise MastodonServiceError("No se encontró la cuenta.")
            client.account_note_set(c["id"], texto or "")
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def destacar_cuenta_async(self, account_id: int, acct: str, destacar: bool = True,
                              callback: Optional[Callable] = None):
        """Destaca (endorse) o deja de destacar una cuenta en tu perfil."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            c = self._resolver_cuenta(client, acct)
            if not c:
                raise MastodonServiceError("No se encontró la cuenta.")
            if destacar:
                client.account_endorse(c["id"])
            else:
                client.account_unendorse(c["id"])
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def quitar_seguidor_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """Quita a una cuenta de tus seguidores (sin bloquearla)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            c = self._resolver_cuenta(client, acct)
            if not c:
                raise MastodonServiceError("No se encontró la cuenta.")
            client.account_remove_from_followers(c["id"])
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    # --- Dominios bloqueados ---
    def bloquear_dominio_async(self, account_id: int, dominio: str, bloquear: bool = True,
                               callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            d = (dominio or "").strip().lstrip("@").split("@")[-1]
            if bloquear:
                client.domain_block(d)
            else:
                client.domain_unblock(d)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def obtener_dominios_bloqueados_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return list(client.domain_blocks() or [])
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # === Funciones opcionales de prioridad baja ===

    def obtener_notificaciones_agrupadas_async(self, account_id: int, callback: Optional[Callable] = None):
        """Notificaciones AGRUPADAS reales del servidor (API v2, Mastodon 4.3+; auditoría N2):
        devuelve {"nativo": bool, "grupos": [...]}, cada grupo con group_key (para expandir o
        descartar), tipo, total, autores de muestra, extracto de la publicación objetivo y
        fecha. Si el servidor no soporta la API v2, se agrupan localmente las notificaciones
        normales por (tipo, publicación) y group_key queda vacío (sin descarte por grupo)."""
        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            from app.services.mastodon import filtros_servidor as fsrv
            client = self.obtener_cliente(account_id)
            grupos_out = []
            _me = getattr(self, "_me_ids", {}).get(account_id)

            def _filtro(st):
                # Filtros del servidor en el contexto Notificaciones (como la web): 'ocultar'
                # quita la notificación; 'avisar' no deja leer el extracto.
                return fsrv.evaluar_raw(st, account_id, "notifications", me_id=_me) if st else \
                    {"accion": None, "titulos": [], "titulos_media": []}
            try:
                res = client.grouped_notifications(limit=40, expand_accounts="full")
                grupos = res.get("notification_groups", []) if isinstance(res, dict) else []
                cuentas = {}
                for a in (res.get("accounts", []) if isinstance(res, dict) else []):
                    cuentas[str(a.get("id"))] = a
                for a in (res.get("partial_accounts", []) if isinstance(res, dict) else []):
                    cuentas.setdefault(str(a.get("id")), a)
                estados = {str(s.get("id")): s for s in
                           (res.get("statuses", []) if isinstance(res, dict) else [])}
                for g in grupos:
                    ids = [str(i) for i in (g.get("sample_account_ids") or [])]
                    autores = []
                    for i in ids[:3]:
                        a = cuentas.get(i)
                        if a:
                            autores.append(a.get("display_name") or a.get("acct") or "")
                    st = estados.get(str(g.get("status_id") or ""))
                    extracto = html_a_texto((st or {}).get("content") or "")[:120] if st else ""
                    _ev = _filtro(st)
                    if _ev.get("accion") == "hide":
                        continue
                    if extracto and fsrv.oculta_texto(_ev):
                        extracto = fsrv.texto_filtrado(_ev)
                    grupos_out.append({
                        "group_key": g.get("group_key") or "",
                        "tipo": g.get("type", ""),
                        "total": int(g.get("notifications_count", 0) or len(ids)),
                        "autores": autores,
                        "extracto": extracto,
                        "fecha": str(g.get("latest_page_notification_at") or ""),
                    })
                return {"nativo": True, "grupos": grupos_out}
            except Exception as e:
                log.debug(f"Sin notificaciones agrupadas del servidor, se agrupa localmente: {e}")
            # Reserva local: agrupar por (tipo, publicación objetivo), no solo por tipo.
            paquetes = {}
            for nt in (client.notifications(limit=80) or []):
                tipo = nt.get("type", "")
                st = nt.get("status") or {}
                _ev = _filtro(st)
                if _ev.get("accion") == "hide":
                    continue
                clave = (tipo, str(st.get("id") or ""))
                p = paquetes.setdefault(clave, {"group_key": "", "tipo": tipo, "total": 0,
                                                "autores": [], "extracto": "", "fecha": ""})
                p["total"] += 1
                a = nt.get("account") or {}
                nombre = a.get("display_name") or a.get("acct") or ""
                if nombre and nombre not in p["autores"] and len(p["autores"]) < 3:
                    p["autores"].append(nombre)
                if not p["extracto"] and st.get("content"):
                    p["extracto"] = (fsrv.texto_filtrado(_ev) if fsrv.oculta_texto(_ev)
                                     else html_a_texto(st.get("content") or "")[:120])
                if not p["fecha"]:
                    p["fecha"] = str(nt.get("created_at") or "")
            return {"nativo": False, "grupos": list(paquetes.values())}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cuentas_grupo_notif_async(self, account_id: int, group_key: str,
                                  callback: Optional[Callable] = None):
        """Todas las cuentas de un grupo de notificaciones (expandir grupo, N2)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "grouped_notification_accounts")(group_key)
            return [{"acct": a.get("acct") or "", "nombre": a.get("display_name") or ""}
                    for a in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def descartar_grupo_notif_async(self, account_id: int, group_key: str,
                                    callback: Optional[Callable] = None):
        """Descarta un grupo entero de notificaciones (N2)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            self._api(client, "dismiss_grouped_notification")(group_key)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def obtener_politica_notificaciones_async(self, account_id: int, callback: Optional[Callable] = None):
        """Devuelve la política de filtrado de notificaciones (API 4.3+)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            try:
                return dict(client.notifications_policy() or {})
            except Exception as e:
                raise MastodonServiceError("Tu servidor no soporta la política de notificaciones.") from e
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def fijar_politica_notificaciones_async(self, account_id: int, opciones: dict,
                                            callback: Optional[Callable] = None):
        """Actualiza la política de notificaciones. `opciones` admite claves como
        for_not_following, for_not_followers, for_new_accounts, for_private_mentions,
        for_limited_accounts con valor 'accept' / 'filter' / 'drop'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            try:
                return dict(client.update_notifications_policy(**opciones) or {})
            except Exception as e:
                raise MastodonServiceError("No se pudo actualizar la política: {0}".format(e)) from e
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def destacar_hashtag_async(self, account_id: int, hashtag: str, destacar: bool = True,
                               callback: Optional[Callable] = None):
        """Destaca (o deja de destacar) un hashtag en tu perfil. `tag_feature/tag_unfeature`."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            nombre = (hashtag or "").lstrip("#").strip()
            if not nombre:
                raise MastodonServiceError("Indica un hashtag.")
            if destacar:
                client.tag_feature(nombre)
            else:
                client.tag_unfeature(nombre)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def seguidores_conocidos_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """'Seguido por X a quien tú sigues': cuentas que TÚ sigues y que siguen a `acct`.
        Devuelve lista de nombres (acct)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            c = self._resolver_cuenta(client, acct)
            if not c:
                raise MastodonServiceError("No se encontró la cuenta.")
            res = client.account_familiar_followers(c["id"]) or []
            nombres = []
            for grupo in res:
                for a in (grupo.get("accounts", []) or []):
                    nombres.append(a.get("acct") or a.get("display_name") or "")
            return nombres
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def descartar_sugerencia_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """Quita una cuenta de las sugerencias del servidor. `suggestion_delete`."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            c = self._resolver_cuenta(client, acct)
            if not c:
                raise MastodonServiceError("No se encontró la cuenta.")
            client.suggestion_delete(c["id"])
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def listas_de_cuenta_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """Devuelve las listas (tuyas) a las que pertenece una cuenta. `account_lists`.
        Lista de dicts {id, title}."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            c = self._resolver_cuenta(client, acct)
            if not c:
                raise MastodonServiceError("No se encontró la cuenta.")
            return [{"id": str(l["id"]), "title": l.get("title", "")}
                    for l in (client.account_lists(c["id"]) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_tarjeta_enlace_async(self, account_id: int, status_db_id: str,
                                     callback: Optional[Callable] = None):
        """Devuelve la tarjeta de previsualización de enlace de un toot. `status_card`.
        Dict {title, description, url, author, provider} o None."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            real = db.query_one(
                "SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?",
                (status_db_id,))
            if not real or not real[0]:
                raise MastodonServiceError("No se encontró la publicación.")
            try:
                card = client.status_card(real[0])
            except Exception:
                card = (client.status(real[0]) or {}).get("card")
            if not card:
                return None
            return {"title": card.get("title", ""), "description": card.get("description", ""),
                    "url": card.get("url", ""), "author": card.get("author_name", ""),
                    "provider": card.get("provider_name", "")}
        task_manager.start_task(_bg, callback=callback, pass_context=True)
