# -*- coding: utf-8 -*-
"""Cuentas, listas, anuncios y trending (los filtros del servidor van en mastodon_filtros.py).

Mixin de MastodonService (parte de la refactorización modular).
"""
import logging
from typing import Dict, Any, Optional, Callable
from app.core.database import db
from app.core.errors import MastodonServiceError
from app.core.task_manager import task_manager


log = logging.getLogger(__name__)


from app.services.mastodon.mastodon_estado_cuenta import describir_estado_cuenta, campos_de_perfil  # noqa: E402

class _SocialMixin:
    def _account_a_dict(self, c) -> Dict[str, Any]:
        from app.core.html_utils import html_a_texto
        return {
            "id": c.get("id"),
            "display_name": c.get("display_name") or c.get("username") or "",
            "acct": c.get("acct") or "",
            "bio": html_a_texto(c.get("note") or "")[:300],
            "followers": c.get("followers_count", 0),
            "following": c.get("following_count", 0),
            "statuses": c.get("statuses_count", 0),
            "bot": bool(c.get("bot")),
            "url": c.get("url", ""),
        }

    def obtener_relacion_autor_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """Resuelve un acct a su id y devuelve la relación (seguir, mostrar impulsos, avisar),
        para el diálogo de 'opciones de seguimiento'."""
        acct = (acct or "").lstrip("@")
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cuenta = client.account_lookup(acct)
            rel = client.account_relationships(cuenta["id"])
            r = rel[0] if rel else {}
            return {"id": str(cuenta["id"]), "acct": acct,
                    "display": cuenta.get("display_name") or acct,
                    "following": bool(r.get("following")),
                    "showing_reblogs": bool(r.get("showing_reblogs", True)),
                    "notifying": bool(r.get("notifying", False))}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def seguir_con_opciones_async(self, account_id: int, target_id: str, reblogs: bool, notify: bool,
                                  callback: Optional[Callable] = None):
        """Sigue (o actualiza el seguimiento de) una cuenta con opciones: mostrar sus impulsos
        y avisar de sus publicaciones nuevas. Llamar a follow con opciones sobre alguien a quien
        ya sigues actualiza esas preferencias."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return client.account_follow(target_id, reblogs=reblogs, notify=notify)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def obtener_hashtags_destacados_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista los hashtags DESTACADOS en tu perfil (featured tags)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return [{"id": str(t.get("id")), "name": t.get("name", ""),
                     "statuses_count": t.get("statuses_count", 0)} for t in (client.featured_tags() or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def crear_hashtag_destacado_async(self, account_id: int, name: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return client.featured_tag_create((name or "").lstrip("#").strip())
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def borrar_hashtag_destacado_async(self, account_id: int, ft_id: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.featured_tag_delete(ft_id)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def obtener_cuentas_destacadas_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista las cuentas que tienes DESTACADAS en tu perfil (endorsements)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return [self._account_a_dict(c) for c in (client.endorsements() or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_relaciones_async(self, account_id: int, tipo: str, cargar_mas: bool = False,
                                 callback: Optional[Callable] = None):
        """Obtiene listas de cuentas: 'followers', 'following', 'blocked', 'muted'.

        PAGINADO (auditoría A15): antes se cargaba UNA página de 80 y el resto quedaba
        truncado en silencio. Con cargar_mas=True se pide la SIGUIENTE página (cursor
        real de Mastodon.py, fetch_next). El resultado es un dict:
        {"cuentas": [...], "has_more": bool, "cargar_mas": bool}."""
        if not hasattr(self, "_pag_relaciones"):
            self._pag_relaciones = {}
        clave = (account_id, tipo)

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if cargar_mas:
                pagina_previa = self._pag_relaciones.get(clave)
                if pagina_previa is None:
                    return {"cuentas": [], "has_more": False, "cargar_mas": True}
                res = client.fetch_next(pagina_previa)
            elif tipo == "followers":
                me = self._obtener_me_id(account_id, client)
                res = client.account_followers(me, limit=80)
            elif tipo == "following":
                me = self._obtener_me_id(account_id, client)
                res = client.account_following(me, limit=80)
            elif tipo == "blocked":
                res = client.blocks(limit=80)
            elif tipo == "muted":
                res = client.mutes(limit=80)
            else:
                return {"cuentas": [], "has_more": False, "cargar_mas": cargar_mas}
            # Guardar la página para poder pedir la siguiente; si no hay más, None.
            hay_mas = bool(res) and getattr(res, "_pagination_next", None) is not None
            self._pag_relaciones[clave] = res if hay_mas else None
            return {"cuentas": [self._account_a_dict(c) for c in (res or [])],
                    "has_more": hay_mas, "cargar_mas": cargar_mas}
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"mastodon:relaciones:{account_id}:{tipo}")

    # Tope de seguidores a descargar al comparar (evita descargas enormes en cuentas muy grandes).
    _MAX_SEGUIDORES_SNAPSHOT = 5000

    def novedades_seguidores_async(self, account_id: int, callback: Optional[Callable] = None):
        """Detecta quién te ha DEJADO DE SEGUIR y quién te ha SEGUIDO NUEVO desde la última vez.
        Mastodon NO avisa de los "unfollow", así que se descarga tu lista de seguidores actual y se
        compara con la guardada (snapshot); luego se guarda la actual. callback(dict, err) con:
        {primera_vez, total, nuevos:[{acct,display_name}], perdidos:[{acct,display_name}], antes}."""
        def _bg(ctx):
            from datetime import datetime, timezone
            client = self.obtener_cliente(account_id)
            me = self._obtener_me_id(account_id, client)
            # 1) Descargar TODOS los seguidores actuales (paginado), con tope de seguridad.
            #    Se registra si la descarga terminó COMPLETA: con una lista parcial (fallo de
            #    red a mitad, o tope alcanzado) NO se pueden calcular pérdidas — todas las
            #    páginas no descargadas parecerían "unfollows" falsos (auditoría A1).
            actuales = {}
            descarga = "completa"
            pagina = client.account_followers(me, limit=80)
            while pagina:
                for c in pagina:
                    fid = c.get("id")
                    if fid is not None:  # comprobar ANTES de str(): no guardar "None"
                        actuales[str(fid)] = {"acct": c.get("acct") or "",
                                              "display_name": c.get("display_name") or c.get("username") or ""}
                if len(actuales) >= self._MAX_SEGUIDORES_SNAPSHOT:
                    descarga = "limitada"
                    break
                try:
                    pagina = client.fetch_next(pagina)
                except Exception:
                    descarga = "parcial"
                    break
            # 2) Cargar el snapshot anterior y si es la primera vez.
            hubo = db.query_one("SELECT 1 FROM mastodon_seguidores_meta WHERE account_id = ?", (account_id,))
            primera_vez = not bool(hubo)
            anteriores = {}
            for fid, acct, disp in db.query(
                    "SELECT follower_id, acct, display_name FROM mastodon_seguidores_snapshot "
                    "WHERE account_id = ?", (account_id,)):
                anteriores[str(fid)] = {"acct": acct or "", "display_name": disp or ""}
            # 3) Comparar. Los NUEVOS confirmados valen siempre (están en la lista descargada);
            #    las PÉRDIDAS solo con descarga completa.
            nuevos, perdidos = [], []
            if not primera_vez:
                for fid, d in actuales.items():
                    if fid not in anteriores:
                        nuevos.append(d)
                if descarga == "completa":
                    for fid, d in anteriores.items():
                        if fid not in actuales:
                            perdidos.append(d)
                nuevos.sort(key=lambda x: (x["display_name"] or x["acct"]).lower())
                perdidos.sort(key=lambda x: (x["display_name"] or x["acct"]).lower())
            # 4) Guardar el snapshot + marca de tiempo, en una transacción. Con descarga
            #    COMPLETA se reemplaza; con parcial/limitada solo se AÑADEN los confirmados
            #    (no se borra a nadie del snapshot: podrían seguir siendo seguidores).
            ahora = datetime.now(timezone.utc).isoformat()
            with db.transaccion():
                if descarga == "completa":
                    db.execute("DELETE FROM mastodon_seguidores_snapshot WHERE account_id = ?", (account_id,))
                for fid, d in actuales.items():
                    db.execute("INSERT OR IGNORE INTO mastodon_seguidores_snapshot "
                               "(account_id, follower_id, acct, display_name) VALUES (?, ?, ?, ?)",
                               (account_id, fid, d["acct"], d["display_name"]))
                db.execute("INSERT INTO mastodon_seguidores_meta (account_id, ultima) VALUES (?, ?) "
                           "ON CONFLICT(account_id) DO UPDATE SET ultima = excluded.ultima",
                           (account_id, ahora))
            return {"primera_vez": primera_vez, "total": len(actuales),
                    "nuevos": nuevos, "perdidos": perdidos, "descarga": descarga}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_timeline_usuario_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """Devuelve las publicaciones de un usuario (por su acct): primero las FIJADAS y luego
        las recientes, como hace la web de Mastodon."""
        acct = (acct or "").lstrip("@")
        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            cuenta = client.account_lookup(acct)

            def _fila(s, pinned):
                return {
                    "id": s.get("id"),
                    # El contenido NUNCA se trunca: la columna de contenido debe poder leerse
                    # entera, tenga los caracteres que tenga (el lector lee la celda completa).
                    "texto": html_a_texto(s.get("content") or ""),
                    "fecha": str(s.get("created_at") or ""),
                    "url": s.get("url") or s.get("uri") or "",
                    "pinned": pinned,
                }

            # Publicaciones FIJADAS del perfil (account_statuses con pinned=True).
            fijados = client.account_statuses(cuenta["id"], pinned=True) or []
            ids_fijados = {str(s.get("id")) for s in fijados}
            res = [_fila(s, True) for s in fijados]
            # Publicaciones recientes (sin repetir las fijadas).
            recientes = client.account_statuses(cuenta["id"], limit=40) or []
            for s in recientes:
                if str(s.get("id")) in ids_fijados:
                    continue
                res.append(_fila(s, False))
            return {"display": cuenta.get("display_name") or acct, "acct": acct, "statuses": res}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def cargar_publicaciones_usuario_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """Descarga y CACHEA las publicaciones de un usuario (fijadas + recientes) para poder
        mostrarlas en una lista interactiva. Devuelve {display, acct, items:[{db_id, pinned}]}."""
        acct = (acct or "").lstrip("@")
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cuenta = client.account_lookup(acct)
            items = []
            ids_fijados = set()
            for s in (client.account_statuses(cuenta["id"], pinned=True) or []):
                obj = self._procesar_y_guardar_toot(account_id, s)
                ids_fijados.add(str(s.get("id")))
                items.append({"db_id": obj.id, "pinned": True})
            for s in (client.account_statuses(cuenta["id"], limit=40) or []):
                if str(s.get("id")) in ids_fijados:
                    continue
                obj = self._procesar_y_guardar_toot(account_id, s)
                items.append({"db_id": obj.id, "pinned": False})
            return {"display": cuenta.get("display_name") or acct, "acct": acct, "items": items}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def buscar_y_guardar_async(self, account_id: int, termino: str, timeline_key: str, callback: Optional[Callable] = None):
        """Busca toots (hashtag o texto) y los guarda con una clave de timeline propia."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            t = termino.strip()
            if t.startswith("#"):
                statuses = client.timeline_hashtag(t.lstrip("#"), limit=40)
            else:
                try:
                    statuses = client.search_v2(t, result_type="statuses").get("statuses", [])
                except Exception:
                    statuses = client.search(t, result_type="statuses").get("statuses", [])
            n = 0
            with db.transaccion() as _conn:
                _conn.execute("DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = ?", (account_id, timeline_key))
                for raw in (statuses or []):
                    try:
                        st = self._procesar_y_guardar_toot(account_id, raw, conn=_conn)
                        _conn.execute(
                            "INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) VALUES (?, ?, ?)",
                            (account_id, timeline_key, st.id)
                        )
                        n += 1
                    except Exception as e:
                        log.debug(f"No se pudo guardar resultado de búsqueda: {e}")
            return n
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- LISTAS DE MASTODON ---
    def obtener_listas_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return [{"id": l.get("id"), "title": l.get("title")} for l in (client.lists() or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def crear_lista_async(self, account_id: int, titulo: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return client.list_create(titulo)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def borrar_lista_async(self, account_id: int, list_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return client.list_delete(list_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def actualizar_lista_async(self, account_id: int, list_id, titulo: str,
                               replies_policy: str = None, exclusive: bool = None,
                               callback: Optional[Callable] = None):
        """Edita una lista: renombrar, política de respuestas ('followed'|'list'|'none') y si
        es exclusiva (sus miembros no aparecen también en Inicio). Como la API oficial."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            kw = {}
            if replies_policy is not None:
                kw["replies_policy"] = replies_policy
            if exclusive is not None:
                kw["exclusive"] = exclusive
            return client.list_update(list_id, titulo, **kw)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def obtener_miembros_lista_async(self, account_id: int, list_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return [self._account_a_dict(c) for c in (client.list_accounts(list_id) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def gestionar_miembro_lista_async(self, account_id: int, list_id, acct: str, añadir: bool, callback: Optional[Callable] = None):
        """Añade o quita un usuario (por acct) de una lista."""
        acct = (acct or "").lstrip("@")
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cuenta = client.account_lookup(acct)
            if añadir:
                return client.list_accounts_add(list_id, [cuenta["id"]])
            return client.list_accounts_delete(list_id, [cuenta["id"]])
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def cargar_timeline_lista_async(self, account_id: int, list_id, timeline_key: str, callback: Optional[Callable] = None):
        """Descarga el timeline de una lista y lo guarda con su clave de timeline."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            statuses = client.timeline_list(list_id, limit=40)
            n = 0
            with db.transaccion() as _conn:
                _conn.execute("DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = ?", (account_id, timeline_key))
                for raw in (statuses or []):
                    try:
                        st = self._procesar_y_guardar_toot(account_id, raw, conn=_conn)
                        _conn.execute(
                            "INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) VALUES (?, ?, ?)",
                            (account_id, timeline_key, st.id)
                        )
                        n += 1
                    except Exception as e:
                        log.debug(f"No se pudo guardar toot de lista: {e}")
            return n
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # (Los FILTROS DEL SERVIDOR están en mastodon_filtros.py, _FiltrosMixin.)

    # --- CITAS (quotes) recibidas/política ---
    def citas_de_status_async(self, account_id: int, status_id, callback: Optional[Callable] = None):
        """Lista las publicaciones que CITAN a un status tuyo (status_quotes)."""
        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            res = self._api(client, "status_quotes")(status_id, limit=40)
            citas = []
            for s in (res or []):
                cuenta = s.get("account") or {}
                citas.append({
                    "id": s.get("id"),
                    "autor": cuenta.get("acct") or cuenta.get("username") or "",
                    "texto": html_a_texto(s.get("content") or "")[:300],
                })
            return citas
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def revocar_cita_async(self, account_id: int, status_id, quoting_status_id, callback: Optional[Callable] = None):
        """Revoca una cita que han hecho de tu status (status_quote_revoke)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "status_quote_revoke")(status_id, quoting_status_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def politica_citas_async(self, account_id: int, status_id, policy: str, callback: Optional[Callable] = None):
        """Cambia quién puede citar tu status: 'public', 'followers' o 'nobody'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "status_update_quote_approval_policy")(status_id, policy)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    # --- SOLICITUDES DE NOTIFICACIÓN (filtradas) ---
    def solicitudes_notificacion_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista las solicitudes de notificación de cuentas filtradas (notification_requests)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "notification_requests")(limit=80)
            solicitudes = []
            for r in (res or []):
                cuenta = r.get("account") or {}
                solicitudes.append({
                    "id": r.get("id"),
                    "autor": cuenta.get("acct") or cuenta.get("username") or "",
                    "nombre": cuenta.get("display_name") or "",
                    "cantidad": r.get("notifications_count") or 0,
                })
            return solicitudes
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def aceptar_solicitud_notif_async(self, account_id: int, req_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "accept_notification_request")(req_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def descartar_solicitud_notif_async(self, account_id: int, req_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "dismiss_notification_request")(req_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def resolver_solicitudes_notif_lote_async(self, account_id: int, ids: list, aceptar: bool,
                                              callback: Optional[Callable] = None):
        """Acepta o descarta VARIAS solicitudes de notificación de una vez (Mastodon 4.3+,
        auditoría N8). Al aceptar, el servidor fusiona en segundo plano las notificaciones
        filtradas con las normales: se sondea notifications_merged (hasta ~10 s) para poder
        avisar cuando ya están en tu buffer. Si el endpoint por lotes no existe (servidor
        antiguo o Mastodon.py sin el método), se resuelven una a una."""
        def _bg(ctx):
            import time as _t
            client = self.obtener_cliente(account_id)
            metodo = "accept_multiple_notification_requests" if aceptar else "dismiss_multiple_notification_requests"
            try:
                self._api(client, metodo)(list(ids))
            except Exception as e:
                if not self._es_error_endpoint_inexistente(e):
                    raise
                # Reserva: una a una (servidores sin el endpoint por lotes).
                uno = "accept_notification_request" if aceptar else "dismiss_notification_request"
                for i in ids:
                    if ctx.is_cancelled():
                        break
                    self._api(client, uno)(i)
            fusionadas = None
            if aceptar:
                # Esperar (con tope) a que el servidor termine de fusionar las notificaciones.
                fusionadas = False
                for _n in range(10):
                    if ctx.is_cancelled():
                        break
                    try:
                        if self._api(client, "notifications_merged")():
                            fusionadas = True
                            break
                    except Exception:
                        break  # endpoint no disponible: se confía en el sondeo normal
                    _t.sleep(1)
            return {"total": len(ids), "fusionadas": fusionadas}
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def contador_notif_no_leidas_async(self, account_id: int, callback: Optional[Callable] = None):
        """Número de notificaciones sin leer (notifications_unread_count)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "notifications_unread_count")()
            try:
                return int(res.get("count") if isinstance(res, dict) else res)
            except Exception:
                return 0
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- INFORMACIÓN DE LA INSTANCIA ---
    def info_instancia_async(self, account_id: int, callback: Optional[Callable] = None):
        """Reglas, descripción ampliada, términos del servicio e idiomas de la instancia."""
        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            datos = {"reglas": [], "descripcion": "", "terminos": "", "idiomas": []}
            try:
                for r in (self._api(client, "instance_rules")() or []):
                    datos["reglas"].append(r.get("text") or "")
            except Exception:
                pass
            try:
                ed = self._api(client, "instance_extended_description")()
                datos["descripcion"] = html_a_texto((ed or {}).get("content") or "")
            except Exception:
                pass
            try:
                tos = self._api(client, "instance_terms_of_service")()
                datos["terminos"] = html_a_texto((tos or {}).get("content") or "")
            except Exception:
                pass
            try:
                inst = client.instance()
                datos["idiomas"] = list((inst or {}).get("languages") or [])
            except Exception:
                pass
            # Software del servidor (Mastodon/GoToSocial/Pleroma...) y versión, vía nodeinfo.
            try:
                ni = self._api(client, "instance_nodeinfo")()
                sw = (ni or {}).get("software") or {}
                datos["software"] = " ".join(p for p in (sw.get("name"), sw.get("version")) if p)
                uso = (ni or {}).get("usage") or {}
                datos["usuarios_activos_mes"] = (uso.get("users") or {}).get("active_month")
            except Exception:
                datos["software"] = ""
            # Actividad de las últimas semanas (publicaciones, registros, usuarios activos).
            try:
                sem = self._api(client, "instance_activity")() or []
                datos["actividad"] = [{"semana": str(s.get("week") or ""),
                                       "publicaciones": s.get("statuses") or 0,
                                       "registros": s.get("registrations") or 0,
                                       "activos": s.get("logins") or 0} for s in sem[:4]]
            except Exception:
                datos["actividad"] = []
            # Nº de instancias federadas conocidas.
            try:
                datos["federadas"] = len(self._api(client, "instance_peers")() or [])
            except Exception:
                datos["federadas"] = None
            # Salud del streaming (si responde, el tiempo real funciona en esta instancia).
            try:
                datos["streaming_ok"] = bool(self._api(client, "instance_health")())
            except Exception:
                datos["streaming_ok"] = None
            return datos
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # --- TIMELINE DE UN ENLACE ---
    def timeline_de_enlace_async(self, account_id: int, url: str, callback: Optional[Callable] = None):
        """Publicaciones que comentan un enlace/artículo (timeline_link). Las cachea como un buffer."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "timeline_link")(url, limit=40)
            guardados = 0
            for s in (res or []):
                try:
                    self._procesar_y_guardar_toot(account_id, s)
                    guardados += 1
                except Exception:
                    pass
            return guardados
        task_manager.start_task(_bg, callback=callback, pass_context=True)


    def obtener_anuncios_async(self, account_id: int, callback: Optional[Callable] = None):
        """Anuncios del servidor (announcements)."""
        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            res = client.announcements()
            anuncios = []
            for a in (res or []):
                anuncios.append({
                    "id": a.get("id"),
                    "texto": html_a_texto(a.get("content") or ""),
                    "fecha": str(a.get("published_at") or a.get("starts_at") or ""),
                    "leido": bool(a.get("read")),
                })
            return anuncios
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def marcar_anuncio_leido_async(self, account_id: int, announcement_id, callback: Optional[Callable] = None):
        """Marca un anuncio del servidor como leído (announcement_dismiss)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.announcement_dismiss(announcement_id)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def reaccionar_anuncio_async(self, account_id: int, announcement_id, emoji: str,
                                 añadir: bool = True, callback: Optional[Callable] = None):
        """Añade o quita una reacción (emoji o :atajo:) a un anuncio del servidor."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            nombre = (emoji or "").strip().strip(":")
            if not nombre:
                raise MastodonServiceError("Indica un emoji.")
            if añadir:
                client.announcement_reaction_create(announcement_id, nombre)
            else:
                client.announcement_reaction_delete(announcement_id, nombre)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def cargar_trending_async(self, account_id: int, callback: Optional[Callable] = None):
        """Descarga las publicaciones en tendencia y las guarda como timeline 'trending'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            try:
                res = client.trending_statuses()
            except Exception:
                res = []
            n = 0
            with db.transaccion() as _conn:
                _conn.execute("DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = 'trending'", (account_id,))
                for raw in (res or []):
                    try:
                        st = self._procesar_y_guardar_toot(account_id, raw, conn=_conn)
                        _conn.execute(
                            "INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) VALUES (?, 'trending', ?)",
                            (account_id, st.id)
                        )
                        n += 1
                    except Exception as e:
                        log.debug(f"No se pudo guardar tendencia: {e}")
            return n
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def accion_cuenta_async(self, account_id: int, target_account_id, accion: str, callback: Optional[Callable] = None):
        """Acciones sobre una cuenta: mute, unmute, block, unblock, follow, unfollow."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if accion == "mute":
                return client.account_mute(target_account_id)
            if accion == "unmute":
                return client.account_unmute(target_account_id)
            if accion == "block":
                return client.account_block(target_account_id)
            if accion == "unblock":
                return client.account_unblock(target_account_id)
            if accion == "follow":
                return client.account_follow(target_account_id)
            if accion == "unfollow":
                return client.account_unfollow(target_account_id)
            raise ValueError(f"Acción de cuenta desconocida: {accion}")
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def accion_cuenta_por_acct_async(self, account_id: int, acct: str, accion: str, callback: Optional[Callable] = None,
                                     duracion: Optional[int] = None, notificaciones: bool = True):
        """Como accion_cuenta_async pero resolviendo la cuenta por su 'acct' (desde un toot).
        Al silenciar: duracion (segundos; None = para siempre) y si se ocultan también sus
        notificaciones."""
        acct = (acct or "").lstrip("@")
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cuenta = client.account_lookup(acct)
            cid = cuenta["id"]
            if accion == "mute":
                client.account_mute(cid, notifications=notificaciones, duration=duracion)
            elif accion == "block":
                client.account_block(cid)
            elif accion == "unmute":
                client.account_unmute(cid)
            elif accion == "unblock":
                client.account_unblock(cid)
            elif accion == "follow":
                client.account_follow(cid)
            elif accion == "unfollow":
                client.account_unfollow(cid)
            else:
                raise ValueError(f"Acción desconocida: {accion}")
            return {"acct": acct, "accion": accion}
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def silenciar_conversacion_async(self, account_id: int, toot_db_id: str, silenciar: bool = True, callback: Optional[Callable] = None):
        """Silencia (o reactiva) las notificaciones de una conversación/hilo."""
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback:
                callback(None, Exception("Publicación no encontrada."))
            return
        status_id = row[0]
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return client.status_mute(status_id) if silenciar else client.status_unmute(status_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def descartar_notificacion_async(self, account_id: int, notif_id: str, callback: Optional[Callable] = None):
        """Descarta UNA notificación del servidor y la quita de la caché local."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.notifications_dismiss(notif_id)
            db.execute("DELETE FROM mastodon_notifications WHERE account_id = ? AND notification_id = ?",
                       (account_id, str(notif_id)))
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def limpiar_notificaciones_async(self, account_id: int, callback: Optional[Callable] = None):
        """Descarta TODAS las notificaciones del servidor y vacía la caché local."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.notifications_clear()
            db.execute("DELETE FROM mastodon_notifications WHERE account_id = ?", (account_id,))
            db.execute("DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = 'notifications'",
                       (account_id,))
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def obtener_emojis_personalizados_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista los emojis personalizados de la instancia (visibles en el selector), ordenados
        por categoría y atajo. Se cachean en memoria porque cambian poco."""
        cache = getattr(self, "_emojis_cache", {})
        if account_id in cache:
            if callback:
                callback(cache[account_id], None)
            return

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            emojis = client.custom_emojis() or []
            res = [{"shortcode": e.get("shortcode", ""), "category": e.get("category") or ""}
                   for e in emojis if e.get("visible_in_picker", True) and e.get("shortcode")]
            res.sort(key=lambda e: ((e["category"] or "~").lower(), e["shortcode"].lower()))
            if not hasattr(self, "_emojis_cache"):
                self._emojis_cache = {}
            self._emojis_cache[account_id] = res
            return res
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def borrar_conversacion_async(self, account_id: int, conversation_id: str, callback: Optional[Callable] = None):
        """Quita una conversación de mensajes directos de TU lista (DELETE conversations/:id).
        Los mensajes no se borran para nadie: solo deja de aparecer en tu bandeja de directos
        hasta que llegue un mensaje nuevo. Mastodon.py no lo envuelve: petición directa."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            self._peticion_directa(client, "DELETE", f"/api/v1/conversations/{conversation_id}")
            db.execute("DELETE FROM mastodon_conversations WHERE account_id = ? AND conversation_id = ?",
                       (account_id, str(conversation_id)))
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def marcar_conversacion_leida_async(self, account_id: int, conversation_id: str, callback: Optional[Callable] = None):
        """Marca una conversación de mensajes directos como leída (POST conversations/:id/read)
        y limpia su estado de 'sin leer' en la caché local."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.conversations_read(conversation_id)
            db.execute(
                "UPDATE mastodon_statuses SET conversation_unread = 0 WHERE account_id = ? AND conversation_id = ?",
                (account_id, str(conversation_id)))
            db.execute(
                "UPDATE mastodon_conversations SET unread = 0 WHERE account_id = ? AND conversation_id = ?",
                (account_id, str(conversation_id)))
            return res
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def obtener_conversaciones_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lista REAL de conversaciones de mensajes directos (GET /conversations, auditoría N6):
        cada elemento representa una conversación del servidor, con su id propio, participantes,
        último mensaje (guardado en la caché local para poder abrir su contexto), estado de no
        leído y fecha. Se persiste en mastodon_conversations."""
        def _bg(ctx):
            from datetime import datetime as _dt
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            res = client.conversations(limit=40) or []
            filas = []
            for c in res:
                conv_id = str(c.get("id") or "")
                if not conv_id:
                    continue
                participantes = ", ".join(
                    "@" + (a.get("acct") or a.get("username") or "")
                    for a in (c.get("accounts") or []))
                ultimo = c.get("last_status")
                last_db_id, extracto, fecha = None, "", ""
                if isinstance(ultimo, dict) and ultimo.get("id"):
                    # Guardar el último mensaje en la caché local: así "Abrir conversación"
                    # dispone de la fila para cargar el contexto completo.
                    try:
                        st = self._procesar_y_guardar_toot(account_id, ultimo)
                        last_db_id = st.id if st is not None else None
                    except Exception as e:
                        log.debug(f"No se pudo cachear el último mensaje de la conversación: {e}")
                    extracto = html_a_texto(ultimo.get("content") or "")[:150]
                    fecha = str(ultimo.get("created_at") or "")
                fila = {"conversation_id": conv_id, "participantes": participantes,
                        "last_db_id": last_db_id, "extracto": extracto,
                        "unread": 1 if c.get("unread") else 0, "fecha": fecha}
                filas.append(fila)
                db.execute(
                    "INSERT OR REPLACE INTO mastodon_conversations (account_id, conversation_id, "
                    "participants, last_status_db_id, last_excerpt, unread, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (account_id, conv_id, participantes, last_db_id, extracto, fila["unread"],
                     fecha or _dt.now().isoformat(timespec="seconds")))
            return filas
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"mastodon:conversaciones:{account_id}")

    def obtener_encuesta_async(self, account_id: int, toot_db_id: str, callback: Optional[Callable] = None):
        """Recupera la encuesta de un toot (si la tiene). callback(poll_dict|None, err)."""
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback:
                callback(None, Exception("Publicación no encontrada."))
            return
        status_id = row[0]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            st = client.status(status_id)
            poll = st.get("poll") if isinstance(st, dict) else getattr(st, "poll", None)
            if not poll:
                return None
            opciones = [o.get("title", "") for o in poll.get("options", [])]
            return {
                "id": poll.get("id"),
                "multiple": bool(poll.get("multiple")),
                "expired": bool(poll.get("expired")),
                "voted": bool(poll.get("voted")),
                "options": opciones,
                "votes": [o.get("votes_count", 0) for o in poll.get("options", [])],
                "votes_count": poll.get("votes_count", 0),
            }

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def votar_encuesta_async(self, account_id: int, poll_id: str, choices: list, callback: Optional[Callable] = None):
        """Emite el voto en una encuesta. choices = lista de índices (enteros)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return client.poll_vote(poll_id, choices)

        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def obtener_detalles_cuenta_async(self, account_id: int, acct: str, callback: Optional[Callable] = None):
        """Obtiene la ficha de una cuenta por su acct (bio, seguidores, etc.). callback(dict, err)."""
        acct = (acct or "").lstrip("@").strip()
        if not acct:
            if callback:
                callback(None, Exception("No se encontró la cuenta."))
            return

        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            # 1) Intento directo (cuentas locales o ya conocidas por tu instancia).
            c = None
            try:
                c = client.account_lookup(acct)
            except Exception:
                c = None
            # 2) Si no aparece (cuenta REMOTA que tu servidor no ha visto, p. ej. un perfil citado),
            #    forzamos al servidor a resolverla con una busqueda con resolve=True.
            if not c:
                try:
                    res = client.account_search(acct, resolve=True, limit=1)
                    c = res[0] if res else None
                except Exception:
                    c = None
            if not c:
                raise Exception("No se encontró la cuenta «{0}».".format(acct))
            # Relación contigo (te sigue / le sigues / te ha bloqueado…). Mastodon NO da una lista de
            # quién te bloquea, pero el campo 'blocked_by' de la relación SÍ dice si ESTA cuenta te
            # bloquea a ti. Si es tu propia cuenta, no tiene sentido pedir la relación.
            rel = {}
            try:
                me = self._obtener_me_id(account_id, client)
                if str(c.get("id")) == str(me):
                    rel = {"es_yo": True}
                else:
                    r = client.account_relationships(c["id"])
                    r = (r[0] if r else None) or {}
                    rel = {
                        "es_yo": False,
                        "following": bool(r.get("following")),        # le sigues
                        "followed_by": bool(r.get("followed_by")),    # te sigue
                        "blocking": bool(r.get("blocking")),          # le tienes bloqueado
                        "blocked_by": bool(r.get("blocked_by")),      # te ha bloqueado
                        "muting": bool(r.get("muting")),              # le tienes silenciado
                        "requested": bool(r.get("requested")),        # le pediste seguir (pendiente)
                        "requested_by": bool(r.get("requested_by")),  # te pidió seguir (pendiente)
                    }
            except Exception:
                rel = {}
            return {
                "display_name": c.get("display_name") or c.get("username"),
                "acct": c.get("acct"),
                "bio": html_a_texto(c.get("note") or ""),
                "followers": c.get("followers_count", 0),
                "following": c.get("following_count", 0),
                "statuses": c.get("statuses_count", 0),
                "bot": bool(c.get("bot")),
                "url": c.get("url", ""),
                "created_at": str(c.get("created_at", "")),
                "relacion": rel,
                "estado": describir_estado_cuenta(c),
                "campos": campos_de_perfil(c),
            }

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def obtener_status_servidor_async(self, account_id: int, toot_db_id: str, callback: Optional[Callable] = None):
        """Pide al SERVIDOR el estado de una publicación para tener sus datos al día (contadores de
        favoritos, impulsos y respuestas, idioma, fecha de edición, fijada, enlace). callback(dict,
        err). Usa el id del toot original si la fila es un impulso (sus contadores son los reales)."""
        row = db.query_one(
            "SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row or not row[0]:
            if callback:
                callback(None, Exception("No se encontró la publicación."))
            return
        sid = str(row[0])

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            st = client.status(sid)
            return {
                "favourites": st.get("favourites_count", 0),
                "reblogs": st.get("reblogs_count", 0),
                "replies": st.get("replies_count", 0),
                "language": st.get("language") or "",
                "edited_at": str(st.get("edited_at") or ""),
                "pinned": bool(st.get("pinned")),
                "url": st.get("url") or st.get("uri") or "",
            }

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def refrescar_contadores_async(self, account_id: int, db_ids, callback: Optional[Callable] = None):
        """Re-consulta al SERVIDOR los contadores (favoritos/impulsos/respuestas) de varias
        publicaciones y los actualiza en la BD. Sirve para que la columna de contadores muestre
        datos FRESCOS del servidor en tiempo real: el streaming entrega las respuestas nuevas como
        publicaciones aparte, pero NO actualiza el contador del toot ORIGINAL al que responden; con
        esto, si alguien responde/favoritea/impulsa tu publicación, su contador sube solo.
        callback(cambiados:int, err)."""
        ids = [d for d in (db_ids or []) if d]
        if not ids:
            if callback:
                callback(0, None)
            return

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            ph = ",".join("?" * len(ids))
            # Resolver el status REAL (el original si la fila es un impulso, cuyos contadores son
            # los que se muestran) por cada id de fila.
            filas = db.query(
                f"SELECT id, COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id IN ({ph})",
                ids)
            # DEDUPLICAR originales (varias filas pueden apuntar al mismo toot: impulso +
            # original) y pedirlos EN UN SOLO LOTE con GET /api/v1/statuses?id[]=...
            # (Mastodon 4.3+). Antes era una petición POR FILA visible: 20-40 filas cada
            # 45-60 s eran 1.200-2.400 peticiones/hora (auditoría A6).
            por_sid = {}
            for db_id, sid in filas:
                por_sid.setdefault(str(sid), []).append(db_id)
            sids = list(por_sid.keys())
            if not sids:
                return 0

            frescos = None
            caps = self.capacidades(account_id)
            soporta_lote = (not caps.get("version")
                            or self._version_tupla(caps["version"]) >= (4, 3))
            if soporta_lote:
                # El servidor acepta como MÁXIMO 20 ids por petición (con más responde 422
                # y se caía SIEMPRE al modo antiguo: hasta 10 peticiones sueltas por sondeo).
                try:
                    frescos = []
                    for i in range(0, len(sids), 20):
                        frescos.extend(client.statuses(sids[i:i + 20]) or [])
                except Exception as e:
                    log.debug(f"Lote de contadores no disponible ({e}); se usa el modo antiguo.")
                    frescos = None
            if frescos is None:
                # Servidor antiguo: refresco INDIVIDUAL pero acotado (solo unas pocas filas
                # y a menor ritmo lo regula el llamador) para no fundir la cuota.
                frescos = []
                for sid in sids[:10]:
                    try:
                        frescos.append(client.status(sid))
                    except Exception:
                        continue  # un status borrado/inaccesible no debe cortar el resto

            cambiados = 0
            # El servidor devuelve también 'filtered' recalculado con los filtros ACTUALES: se
            # guarda para que un filtro nuevo/editado se aplique a lo ya descargado.
            from app.services.mastodon.filtros_servidor import serializar_filtrados
            _me = getattr(self, "_me_ids", {}).get(account_id)
            with db.transaccion():
                for st in (frescos or []):
                    if not isinstance(st, dict) or not st.get("id"):
                        continue
                    _autor = str((st.get("account") or {}).get("id") or "")
                    _filt = (None if (_me is not None and _autor == str(_me))
                             else serializar_filtrados(st.get("filtered")))
                    for db_id in por_sid.get(str(st["id"]), []):
                        try:
                            db.execute(
                                "UPDATE mastodon_statuses SET favourites_count = ?, reblogs_count = ?, "
                                "replies_count = ?, filtered_by = ? WHERE id = ?",
                                (int(st.get("favourites_count") or 0), int(st.get("reblogs_count") or 0),
                                 int(st.get("replies_count") or 0), _filt, db_id))
                            cambiados += 1
                        except Exception:
                            pass
            return cambiados

        task_manager.start_task(_bg, callback=callback, pass_context=True)
