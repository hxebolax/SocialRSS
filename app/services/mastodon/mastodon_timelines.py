# -*- coding: utf-8 -*-
"""Carga y parseo de timelines, toots y notificaciones.

Mixin de MastodonService (parte de la refactorización modular).
"""
import logging
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
from app.core.database import db
from app.core.config_manager import config
from app.core.task_manager import task_manager
from app.core.models import MastodonStatus
from app.services.mastodon.mastodon_citas import estado_cita_api, estado_cita_guardada


log = logging.getLogger(__name__)

class _TimelinesMixin:
    # ------------------------------------------------------------------
    # Cursor REAL de paginación por buffer (auditoría M4). Marcadores, favoritos y
    # conversaciones paginan por ids internos del Link header, NO por status_id:
    # se guarda aquí el `_pagination_next` de la última página traída y "cargar más
    # antiguos" lo consume pasando max_id="_NEXT_".
    # ------------------------------------------------------------------
    PAG_SIGUIENTE = "_NEXT_"   # centinela para "usa el cursor de paginación guardado"

    def _pag_next_store(self) -> dict:
        if not hasattr(self, "_pag_next"):
            self._pag_next = {}
        return self._pag_next

    def _guardar_pag_next(self, account_id, timeline_type, info):
        try:
            self._pag_next_store()[(account_id, timeline_type)] = info
        except Exception:
            pass

    def tiene_pag_next(self, account_id, timeline_type) -> bool:
        return bool(self._pag_next_store().get((account_id, timeline_type)))

    # ------------------------------------------------------------------
    # Procesado DIRECTO de eventos del streaming (auditoría M1): el payload que
    # entrega Mastodon.py se guarda tal cual en la caché, sin repetir una consulta
    # REST. Crítico para `status_update` (el id no cambia, así que el refresco por
    # min_id nunca lo recuperaba y la edición quedaba antigua para siempre) y para
    # `delete` (antes no existía manejador y el estado borrado se quedaba en la app).
    # ------------------------------------------------------------------

    def procesar_evento_stream(self, account_id: int, timeline_key: str,
                               tipo: str, payload) -> bool:
        """Aplica un evento del streaming directamente a SQLite. Devuelve True si quedó
        aplicado (la UI solo necesita repintar desde la caché); False si el llamador debe
        recurrir a la sincronización REST de siempre. Corre en el hilo del stream (prioridad
        baja); NUNCA toca wx."""
        try:
            if tipo == "update" and isinstance(payload, dict):
                with db.transaccion() as conn:
                    st = self._procesar_y_guardar_toot(account_id, payload, conn=conn)
                    conn.execute(
                        "INSERT OR IGNORE INTO mastodon_timeline_statuses "
                        "(account_id, timeline, status_db_id) VALUES (?, ?, ?)",
                        (account_id, timeline_key, st.id))
                return True
            if tipo == "status_update" and isinstance(payload, dict):
                # Edición: upsert del estado (mismo id); sus asociaciones ya existen.
                with db.transaccion() as conn:
                    self._procesar_y_guardar_toot(account_id, payload, conn=conn)
                return True
            if tipo == "notification" and isinstance(payload, dict):
                with db.transaccion() as conn:
                    self._procesar_y_guardar_notificacion(account_id, payload, conn=conn)
                return True
            if tipo == "delete" and payload:
                self.borrar_estado_cache(account_id, str(payload))
                return True
            if tipo == "conversation" and isinstance(payload, dict):
                last = payload.get("last_status")
                if not isinstance(last, dict):
                    return False
                with db.transaccion() as conn:
                    st = self._procesar_y_guardar_toot(account_id, last, conn=conn)
                    conn.execute(
                        "UPDATE mastodon_statuses SET conversation_id = ?, conversation_unread = ? "
                        "WHERE id = ?",
                        (str(payload.get("id") or "") or None,
                         1 if payload.get("unread") else 0, st.id))
                    conn.execute(
                        "INSERT OR IGNORE INTO mastodon_timeline_statuses "
                        "(account_id, timeline, status_db_id) VALUES (?, 'direct', ?)",
                        (account_id, st.id))
                return True
        except Exception as e:
            log.warning(f"No se pudo aplicar un evento '{tipo}' del streaming; se sincronizará "
                        f"por REST: {e}")
        return False

    def borrar_estado_cache(self, account_id: int, status_id: str) -> int:
        """Elimina de la caché un estado borrado en el servidor: sus asociaciones de
        timeline, su entrada FTS, la fila y también los IMPULSOS derivados de él.
        Devuelve cuántas filas de estado se eliminaron."""
        borrados = 0
        with db.transaccion() as conn:
            filas = conn.execute(
                "SELECT id FROM mastodon_statuses WHERE account_id = ? "
                "AND (status_id = ? OR reblog_of_id = ?)",
                (account_id, status_id, status_id)).fetchall()
            for (dbid,) in filas:
                conn.execute("DELETE FROM mastodon_timeline_statuses "
                             "WHERE account_id = ? AND status_db_id = ?", (account_id, dbid))
                conn.execute("DELETE FROM mastodon_statuses_fts WHERE status_db_id = ?", (dbid,))
                conn.execute("DELETE FROM mastodon_statuses WHERE id = ?", (dbid,))
                borrados += 1
        return borrados

    def cargar_timeline_async(
        self,
        account_id: int,
        timeline_type: str = "home", # home, public, local, bookmarks, favourites, direct
        hashtag: Optional[str] = None,
        limit: int = None,
        max_id: Optional[str] = None,
        min_id: Optional[str] = None,
        callback: Optional[Callable[[List[MastodonStatus], Optional[Exception]], None]] = None
    ):
        """
        Descarga el timeline asíncronamente y lo cachea.

        limit: número de elementos que se quieren por carga. Si no se indica, se usa la opción
               "Publicaciones por carga" de Preferencias (mastodon.limit_per_load). La API de
               Mastodon limita CADA petición a 40 (el servidor recorta más), así que si se pide
               más se PAGINA automáticamente (de 40 en 40) hasta reunir lo pedido.
        max_id: si se indica, descarga elementos MÁS ANTIGUOS que ese id (paginación
                hacia atrás para "cargar más").
        min_id: si se indica, descarga solo elementos MÁS NUEVOS que ese id (sondeo
                incremental para "que entren toots", igual que hace TWBlue).
        """
        # Sin cuenta no hay nada que cargar: evita un error ruidoso (cuenta ID None) cuando
        # se dispara una carga durante el arranque o al navegar por nodos sin cuenta activa.
        if not account_id:
            if callback:
                callback([], None)
            return
        # El buffer de MENCIONES comparte datos con NOTIFICACIONES (misma tabla
        # mastodon_notifications): se descarga igual (endpoint de notificaciones) y la capa de
        # UI filtra por tipo 'mention'. Así basta con tratarlo como 'notifications' al descargar.
        if timeline_type == "mentions":
            timeline_type = "notifications"
        if not limit:
            try:
                # Se permite pedir más de 40 (hasta 200): la API limita cada petición a 40, así
                # que el exceso se consigue paginando varias páginas y juntándolas.
                limit = max(1, min(200, int(config.get("mastodon", "limit_per_load", 40))))
            except (TypeError, ValueError):
                limit = 40
        objetivo = max(1, int(limit))     # cuántos elementos se quieren reunir en total
        pag = min(40, objetivo)           # tamaño REAL de cada petición (tope de la API)
        def _bg_load(ctx):
            client = self.obtener_cliente(account_id)

            # Función de descarga concreta según el tipo de timeline.
            def _fetch(**kw):
                if timeline_type == "home":
                    return client.timeline_home(**kw)
                if timeline_type == "local":
                    return client.timeline_local(**kw)
                if timeline_type == "public":
                    return client.timeline_public(**kw)
                if timeline_type == "bookmarks":
                    return client.bookmarks(**kw)
                if timeline_type == "favourites":
                    return client.favourites(**kw)
                if timeline_type == "direct":
                    return client.conversations(**kw)
                if timeline_type == "notifications":
                    return client.notifications(**kw)
                if timeline_type == "sent":
                    return client.account_statuses(self._obtener_me_id(account_id, client), **kw)
                if timeline_type.startswith("user:"):
                    # Cronología de OTRO usuario: resolver su acct a id y pedir sus toots.
                    acct = timeline_type.split(":", 1)[1].lstrip("@")
                    cuenta = client.account_lookup(acct)
                    return client.account_statuses(cuenta["id"], **kw)
                if timeline_type.startswith("thread:"):
                    # Conversación/hilo abierto como buffer: devolver toda la cadena
                    # (ancestros + el toot + descendientes), en orden cronológico por fecha. Si el
                    # contexto falla (servidor lento), al menos se devuelve el mensaje en sí.
                    sid = timeline_type.split(":", 1)[1]
                    ancestros, descendientes = [], []
                    try:
                        ctx = client.status_context(sid)
                        ancestros = list(ctx.get("ancestors", []) or [])
                        descendientes = list(ctx.get("descendants", []) or [])
                    except Exception as e:
                        log.debug(f"No se pudo obtener el contexto del hilo {sid}: {e}")
                    try:
                        actual = client.status(sid)
                    except Exception:
                        actual = None
                    cadena = list(ancestros)
                    if actual:
                        cadena.append(actual)
                    cadena.extend(descendientes)
                    return cadena
                if timeline_type == "hashtag" and hashtag:
                    return client.timeline_hashtag(hashtag, **kw)
                if timeline_type.startswith("list:"):
                    return client.timeline_list(timeline_type.split(":", 1)[1], **kw)
                if timeline_type.startswith("search:"):
                    termino = timeline_type.split(":", 1)[1]
                    if termino.startswith("#"):
                        return client.timeline_hashtag(termino.lstrip("#"), **kw)
                    raise ValueError("Una búsqueda de texto no se puede paginar hacia atrás.")
                # NUNCA caer en silencio a Inicio: eso contaminaba listas y búsquedas con
                # publicaciones de Home asociadas a la clave equivocada (auditoría M4).
                raise ValueError(f"Tipo de buffer no soportado para esta carga: {timeline_type}")

            def _fetch_hasta(cuantos, **kw):
                """Reúne ~'cuantos' elementos paginando de 40 en 40 (la API limita cada
                petición a 40). Se usa para 'Publicaciones por carga' mayores de 40 y para
                'cargar más antiguos'."""
                primera = _fetch(limit=min(40, cuantos), **kw) or []
                acum = list(primera)
                ultima = primera
                # fetch_next devuelve la siguiente página (elementos más antiguos).
                while ultima and len(acum) < cuantos:
                    try:
                        siguiente = client.fetch_next(ultima)
                    except Exception:
                        break
                    if not siguiente:
                        break
                    acum.extend(siguiente)
                    ultima = siguiente
                # Recordar el CURSOR REAL de paginación de la última página. Marcadores,
                # favoritos y conversaciones NO paginan por status_id (usan ids internos del
                # Link header): para su "cargar más antiguos" hay que usar este cursor, no
                # deducir un max_id de la fila visible (auditoría M4).
                self._guardar_pag_next(account_id, timeline_type,
                                       getattr(ultima, "_pagination_next", None))
                return acum[:cuantos]

            log.debug(f"Cargando timeline '{timeline_type}' cuenta {account_id} (objetivo={objetivo}, max_id={max_id}, min_id={min_id})")

            # Timelines en los que tiene sentido RELLENAR EL HUECO completo (paginando hacia
            # adelante) para no perder NADA: son los de bajo tráfico que de verdad importan.
            # OJO: la Local y la Federada NO entran aquí a propósito. Son un 'firehose' (cientos de
            # publicaciones por minuto); rellenar su hueco entero traía y volcaba de golpe cientos
            # de toots, BLOQUEANDO la interfaz al entrar en el buffer. Igual que TWBlue, se traen
            # solo de una página en una página (caen al 'else'/min_id), sin avalancha. Perder
            # alguna publicación pública es irrelevante; no se pierde nada de Inicio/Notificaciones.
            rellenables = ("home", "sent", "hashtag", "notifications")

            if max_id is None and min_id is None and timeline_type in rellenables:
                # Refresco: traer TODO lo más nuevo que el último guardado, paginando
                # hacia adelante para no perder toots si hay más de 'limit' nuevos.
                # Cursor por ID (no por fecha): la API pagina por id, y los toots federados traen un
                # created_at desalineado con su id local. Coger el id MAYOR como min_id del refresco.
                if timeline_type == "notifications":
                    fila = db.query_one(
                        "SELECT notification_id FROM mastodon_notifications WHERE account_id = ? "
                        "ORDER BY CAST(notification_id AS INTEGER) DESC LIMIT 1",
                        (account_id,))
                else:
                    fila = db.query_one("""
                        SELECT s.status_id FROM mastodon_statuses s
                        JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id
                        WHERE t.account_id = ? AND t.timeline = ?
                        ORDER BY CAST(s.status_id AS INTEGER) DESC LIMIT 1
                    """, (account_id, timeline_type))
                ultimo = fila[0] if fila and fila[0] else None

                if not ultimo:
                    results = _fetch_hasta(objetivo)  # primera vez: reunir lo pedido (paginando)
                else:
                    results = []
                    pagina = _fetch(limit=pag, min_id=ultimo)
                    paginas = 0
                    # Rellenar el hueco hasta alcanzar lo más nuevo. El bucle para solo cuando
                    # fetch_previous ya no devuelve nada (al día); el tope es de seguridad para
                    # no descargar sin fin si la cuenta lleva mucho tiempo sin abrirse.
                    while pagina and paginas < 25:  # tope de seguridad (25*40 = 1000)
                        results.extend(pagina)
                        try:
                            pagina = client.fetch_previous(pagina)  # aún más nuevos
                        except Exception:
                            break
                        paginas += 1
            elif min_id is not None:
                # Sondeo incremental (directos, marcadores, favoritos...): solo lo más nuevo,
                # una página basta (no tiene sentido paginar hacia atrás aquí).
                results = _fetch(limit=pag, min_id=min_id)
            elif max_id == self.PAG_SIGUIENTE:
                # "Cargar más antiguos" con el CURSOR REAL de la última página (marcadores,
                # favoritos, directos: sus max_id son ids internos, no status_id).
                info = self._pag_next_store().get((account_id, timeline_type))
                if not info:
                    results = []
                else:
                    results = client.fetch_next(info) or []
                    self._guardar_pag_next(account_id, timeline_type,
                                           getattr(results, "_pagination_next", None))
            else:
                # Primera carga de un buffer no cronológico, o "cargar más antiguos" (max_id):
                # reunir lo pedido paginando si se quieren más de 40.
                results = _fetch_hasta(objetivo, max_id=max_id)

            # Formatear el listado. Cada elemento se procesa de forma AISLADA: si uno falla (un
            # campo inesperado de la API, etc.) se salta y se sigue con los demás, para que un
            # único toot problemático no deje el timeline entero sin cargar.
            # Toda la página se guarda en UNA sola transacción (un único commit), para no tomar el
            # lock de escritura ni hacer fsync una vez por toot (antes ~160 mini-transacciones por
            # página). Cada elemento sigue AISLADO: si uno falla, se salta y se continúa.
            statuses = []
            with db.transaccion() as _conn:
                for status in results:
                    try:
                        if timeline_type == "notifications":
                            # Devolver también los dicts de notificación para que la capa de UI
                            # pueda contar las nuevas y distinguir menciones de otras notificaciones.
                            statuses.append(self._procesar_y_guardar_notificacion(account_id, status, conn=_conn))
                        else:
                            conv_id = None
                            conv_unread = 0
                            if timeline_type == "direct" and isinstance(status, dict) and "last_status" in status:
                                raw_status = status["last_status"]
                                conv_id = str(status.get("id") or "") or None
                                conv_unread = 1 if status.get("unread") else 0
                            else:
                                raw_status = status

                            status_obj = self._procesar_y_guardar_toot(account_id, raw_status, conn=_conn)
                            statuses.append(status_obj)
                            # En mensajes directos, guardar el id de la conversación (para marcarla
                            # leída) y si está sin leer, sobre la fila del último mensaje.
                            if conv_id and status_obj:
                                _conn.execute(
                                    "UPDATE mastodon_statuses SET conversation_id = ?, conversation_unread = ? WHERE id = ?",
                                    (conv_id, conv_unread, status_obj.id))
                            # Guardar relación en mastodon_timeline_statuses
                            _conn.execute(
                                "INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) VALUES (?, ?, ?)",
                                (account_id, timeline_type, status_obj.id)
                            )
                    except Exception as e:
                        _sid = status.get("id") if isinstance(status, dict) else "?"
                        log.error(f"Se omite un elemento de '{timeline_type}' (id {_sid}) por un error al procesarlo: {e}")

            return statuses


        def _bg_load_avisando(ctx):
            """Igual que _bg_load, pero si la instancia responde con una página web en lugar de con
            la API (servidor caído, proxy inverso mal apuntado), avisa al usuario UNA vez y marca el
            error como ya diagnosticado para que los sondeos siguientes no llenen el registro con la
            misma traza. Es la ruta por la que pasan TODOS los sondeos periódicos."""
            try:
                return _bg_load(ctx)
            except Exception as e:
                if self.avisar_si_servidor_sin_api(account_id, e):
                    try:
                        e.sin_traza = True
                    except Exception:
                        pass
                raise

        task_manager.start_task(
            _bg_load_avisando,
            callback=callback,
            pass_context=True
        )

    def _procesar_y_guardar_toot(self, account_id: int, raw: Dict[str, Any], conn=None) -> MastodonStatus:
        """Parse el toot retornado por la API, lo guarda en SQLite y lo indexa en FTS5.

        Si se pasa `conn` (de db.transaccion()), todas las escrituras van por esa conexión SIN
        commit individual: el llamador agrupa toda la página en una sola transacción (rendimiento).
        Sin `conn`, se comporta como siempre (cada escritura con su commit)."""
        # Atajos de escritura/lectura que respetan la transacción si la hay.
        def _ej(_sql, _params=()):
            if conn is not None:
                conn.execute(_sql, _params)
            else:
                db.execute(_sql, _params)

        def _q1(_sql, _params=()):
            if conn is not None:
                return conn.execute(_sql, _params).fetchone()
            return db.query_one(_sql, _params)
        # Filtros del servidor: la API NO quita los toots filtrados, les añade 'filtered' con
        # los filtros que casan (id, título, contextos, caducidad y acción). Se guarda como JSON
        # y la lista decide al mostrarlo (ocultar/avisar/multimedia) según el contexto del
        # buffer, igual que la web oficial (ver filtros_servidor.py). En un impulso manda el
        # 'filtered' del ORIGINAL; los toots propios nunca se filtran.
        filtered_by = None
        try:
            from app.services.mastodon.filtros_servidor import serializar_filtrados
            _orig = raw.get("reblog") if isinstance(raw.get("reblog"), dict) else None
            _filtrados = ((_orig or {}).get("filtered") or raw.get("filtered") or [])
            _autor_id = str(((_orig or raw).get("account") or {}).get("id") or "")
            _me = getattr(self, "_me_ids", {}).get(account_id)
            if _filtrados and not (_me is not None and _autor_id == str(_me)):
                filtered_by = serializar_filtrados(_filtrados)
        except Exception:
            filtered_by = None

        # Información del autor que ha impulsado (boost), si aplica. Se guarda
        # por separado para no contaminar el contenido y permitir un renderizado
        # accesible al estilo TWBlue ("Impulsado de @usuario").
        reblog_by_name = ""
        reblog_by_username = ""
        reblog_of_id = None        # id del toot ORIGINAL (para que las acciones lo apunten)
        reblog_created_at = None   # fecha del IMPULSO (para ordenar la entrada en su sitio)
        if raw.get("reblog") and isinstance(raw["reblog"], dict):
            booster = raw.get("account", {})
            reblog_by_name = booster.get("display_name") or booster.get("username", "Desconocido")
            reblog_by_username = f"@{booster.get('acct')}" if booster.get("acct") else ""
            # El impulso es su PROPIA entrada de timeline: se usa el id y la fecha del IMPULSO
            # (no los del original). Antes se tomaban los del original, así que el boost se
            # ordenaba en la fecha vieja del original o colisionaba con él, y se "perdía" del
            # timeline reciente. El id del original se guarda en reblog_of_id para las acciones.
            reblog_entry_id = str(raw["id"])
            reblog_created_at = raw.get("created_at")
            _orig_id = raw["reblog"].get("id")
            reblog_of_id = str(_orig_id) if _orig_id is not None else None
            raw = dict(raw["reblog"])  # contenido del original
            toot_id = reblog_entry_id
        else:
            toot_id = str(raw["id"])

        # ATAJO ANTI-TIRONES: en los sondeos (Inicio/Local/Federada) la API devuelve una y otra vez
        # los MISMOS toots; reparsear su HTML con BeautifulSoup cada vez (×40 por página, ×varias
        # redes a la vez) acapara el GIL y provoca tirones al navegar. Si el toot YA está cacheado,
        # tiene contenido, NO ha sido editado y NO lleva encuesta (cuyos votos cambian sin editar),
        # solo refrescamos sus CONTADORES/estado con un UPDATE barato y NOS SALTAMOS todo el parseo.
        db_id = f"{account_id}:{toot_id}"
        _edited_api = raw.get("edited_at") or ""
        if isinstance(_edited_api, datetime):
            _edited_api = _edited_api.isoformat()
        _edited_api = str(_edited_api)
        # El estado de la CITA (pending -> accepted, revoked...) cambia SIN editar el toot y Mastodon
        # no lo avisa por el streaming: si difiere del guardado, no vale el atajo (antes una cita
        # vista "pendiente" se quedaba así para siempre aunque el servidor ya la hubiera aceptado).
        _estado_cita = estado_cita_api(raw)
        if _estado_cita == "pending":
            self._apuntar_cita_pendiente(account_id, toot_id)
        if not raw.get("poll"):
            _prev = _q1("SELECT edited_at, content, quote_json FROM mastodon_statuses WHERE id = ?",
                        (db_id,))
            if (_prev is not None and (_prev[1] or "") and str(_prev[0] or "") == _edited_api
                    and estado_cita_guardada(_prev[2]) == _estado_cita):
                try:
                    _ej("UPDATE mastodon_statuses SET favourited=?, reblogged=?, bookmarked=?, "
                        "replies_count=?, reblogs_count=?, favourites_count=?, quotes_count=?, "
                        "pinned=?, filtered_by=? WHERE id=?",
                        (1 if raw.get("favourited") else 0, 1 if raw.get("reblogged") else 0,
                         1 if raw.get("bookmarked") else 0, int(raw.get("replies_count") or 0),
                         int(raw.get("reblogs_count") or 0), int(raw.get("favourites_count") or 0),
                         int(raw.get("quotes_count") or 0), 1 if raw.get("pinned") else 0,
                         filtered_by, db_id))
                except Exception as e:
                    log.debug(f"No se pudo refrescar contadores del toot {db_id}: {e}")
                _created = reblog_created_at if reblog_created_at is not None else raw.get("created_at")
                _created = _created.isoformat() if isinstance(_created, datetime) else str(_created or "")
                # Solo se usa .id en los llamadores; el resto del objeto no hace falta aquí.
                return MastodonStatus(id=db_id, account_id=account_id, status_id=toot_id, uri="",
                                      author_name="", author_username="", content=_prev[1] or "",
                                      created_at=_created, visibility="")

        # Limpiar tags HTML del contenido para lectura accesible
        from bs4 import BeautifulSoup
        from app.core.html_utils import aplicar_emojis_personalizados
        import re
        soup = BeautifulSoup(raw["content"], "html.parser")

        # Cita NATIVA de Mastodon (4.4+): cuando un toot cita a otro, el servidor ANTEPONE al
        # cuerpo un <p class="quote-inline">RE: <enlace></p> solo como compatibilidad con
        # instancias viejas. La guía oficial (https://docs.joinmastodon.org/client/quotes/) indica
        # eliminar ese bloque cuando hay cita nativa, porque es redundante con la cita que ya
        # mostramos aparte ("Citando a ..."). Sin esto aparecía un feo "RE: [@usuario (publicación)]".
        if isinstance(raw.get("quote"), dict):
            for _ql in soup.find_all(class_="quote-inline"):
                _ql.decompose()

        # Reemplazar enlaces a publicaciones con formato legible [@usuario (publicación)]
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if not href:
                continue
            clases = a.get("class", []) or []
            # Patrón estándar: https://instance/@user/status_id
            match = re.match(r'https?://[^/]+/@([^/]+)/(\d+)', href)
            if match:
                a.string = f"[@{match.group(1)} (publicación)]"
                continue
            # Patrón alternativo: https://instance/users/user/statuses/status_id
            match = re.match(r'https?://[^/]+/users/([^/]+)/statuses/(\d+)', href)
            if match:
                a.string = f"[@{match.group(1)} (publicación)]"
                continue
            # Menciones (@usuario) y hashtags (#tag): conservar su texto, pero unido en una sola
            # cadena (vienen con <span> dentro que get_text partiría con saltos).
            if "mention" in clases or "hashtag" in clases:
                a.string = a.get_text("").strip()
                continue
            # URL normal: Mastodon la muestra TROCEADA en <span> (invisible/ellipsis) para
            # acortarla; al unir con saltos de línea salía "https:// github.com/...". Usamos el
            # enlace COMPLETO como texto para que la URL quede entera y sin espacios raros.
            a.string = href

        texto_limpio = soup.get_text("\n").strip()
        # Emojis personalizados de instancia (:shortcode:) -> nombre legible para el lector.
        texto_limpio = aplicar_emojis_personalizados(texto_limpio, raw.get("emojis"))

        spoiler_text = aplicar_emojis_personalizados(raw.get("spoiler_text", ""), raw.get("emojis"))
        author = raw.get("account", {})
        author_name = author.get("display_name") or author.get("username", "Desconocido")
        author_name = aplicar_emojis_personalizados(author_name, author.get("emojis"))
        author_username = f"@{author.get('acct')}"

        # Cliente/aplicación desde la que se publicó (solo presente en tus propias
        # publicaciones de tu instancia)
        app_obj = raw.get("application") or {}
        application = app_obj.get("name", "") if isinstance(app_obj, dict) else ""
        
        # Guardar adjuntos serializados
        media_attachments = raw.get("media_attachments", [])
        media_json = json.dumps(media_attachments)

        # Cuentas mencionadas en el toot (para responder a TODA la conversación, no solo al
        # autor): se guarda la lista de 'acct' (usuario@instancia).
        mentions_json = json.dumps(
            [m.get("acct", "") for m in (raw.get("mentions") or []) if m.get("acct")])

        # Cita NATIVA de Mastodon: si el toot cita a otro, guardar el autor y el texto del
        # citado para mostrarlo de forma accesible ("Citando a @usuario: ...").
        quote_json = None
        q = raw.get("quote")
        # Solo se muestra el CONTENIDO citado si la cita está ACEPTADA (state == "accepted"); en
        # pending/rejected/revoked/blocked/muted no debe mostrarse (doc oficial de la entidad
        # Quote). Pero SÍ se guarda el estado, para decir "cita pendiente de aprobación" o "la
        # publicación citada se ha borrado" en vez de que la cita desaparezca sin explicación.
        # También se guarda el id de la publicación citada, para poder abrirla.
        if isinstance(q, dict) and q.get("state"):
            estado = q.get("state")
            qs = q.get("quoted_status") if isinstance(q.get("quoted_status"), dict) else None
            datos = {"state": estado,
                     "id": str((qs or {}).get("id") or q.get("quoted_status_id") or "")}
            if estado == "accepted" and qs:
                qacc = qs.get("account", {}) or {}
                try:
                    qtexto = BeautifulSoup(qs.get("content", "") or "", "html.parser").get_text(" ").strip()
                except Exception:
                    qtexto = ""
                datos.update({
                    "acct": qacc.get("acct", "") or "",
                    "name": qacc.get("display_name") or qacc.get("username", "") or "",
                    "content": qtexto,
                })
                # Filtros del servidor de la publicación CITADA (se aplican al mostrar la cita).
                try:
                    from app.services.mastodon.filtros_servidor import serializar_de_raw
                    _qf = serializar_de_raw(qs, getattr(self, "_me_ids", {}).get(account_id))
                    if _qf:
                        datos["filtered"] = _qf
                except Exception:
                    pass
            quote_json = json.dumps(datos)

        # Extraer todos los enlaces únicos
        urls = []
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if href and href not in urls:
                urls.append(href)
        for media in media_attachments:
            m_url = media.get("url") or media.get("remote_url")
            if m_url and m_url not in urls:
                urls.append(m_url)
        # Enlace de la tarjeta (link preview): a veces el único enlace del toot
        # vive aquí y no en el cuerpo, así que lo añadimos para poder abrirlo.
        card = raw.get("card")
        if isinstance(card, dict):
            c_url = card.get("url")
            if c_url and c_url not in urls:
                urls.append(c_url)
        urls_json = json.dumps(urls)
        
        # Para un impulso, la fecha de la entrada es la del IMPULSO (para que aparezca en su
        # sitio del timeline); para un toot normal, la suya.
        created_at_fuente = reblog_created_at if reblog_created_at is not None else raw.get("created_at")
        if isinstance(created_at_fuente, datetime):
            created_at_str = created_at_fuente.isoformat()
        else:
            created_at_str = str(created_at_fuente or "")

        # ¿La publicación incluye una encuesta?
        has_poll = 1 if raw.get("poll") else 0

        # Campos oficiales de la entidad Status para alinear con Mastodon:
        edited_at = raw.get("edited_at") or ""
        if isinstance(edited_at, datetime):
            edited_at = edited_at.isoformat()
        edited_at = str(edited_at)
        in_reply_to_id = str(raw["in_reply_to_id"]) if raw.get("in_reply_to_id") else ""
        in_reply_to_account_id = str(raw["in_reply_to_account_id"]) if raw.get("in_reply_to_account_id") else ""
        language = raw.get("language") or ""
        replies_count = int(raw.get("replies_count") or 0)
        reblogs_count = int(raw.get("reblogs_count") or 0)
        favourites_count = int(raw.get("favourites_count") or 0)
        quotes_count = int(raw.get("quotes_count") or 0)
        pinned = 1 if raw.get("pinned") else 0
        # La preview card puede traer fechas como datetime (mastodon.py las parsea), que no son
        # serializables a JSON directamente; default=str las convierte a texto sin romper.
        card_json = json.dumps(card, default=str) if isinstance(card, dict) else None
        # quote_approval (4.5+): ¿puede el usuario citar esta publicación? automatic/manual = sí,
        # denied/unsupported = no, ausente = desconocido (NULL, comportamiento de siempre).
        can_quote = None
        _qa = raw.get("quote_approval")
        if isinstance(_qa, dict) and _qa.get("current_user"):
            can_quote = 1 if _qa.get("current_user") in ("automatic", "manual") else 0

        # 1. Guardar en SQLite
        sql = """
        INSERT INTO mastodon_statuses (
            id, account_id, status_id, uri, author_name, author_username, content, created_at, visibility, nsfw, spoiler_text, favourited, reblogged, bookmarked, media_json, urls_json, reblog_by_name, reblog_by_username, application, has_poll, mentions_json, reblog_of_id, quote_json, filtered_by, edited_at, in_reply_to_id, in_reply_to_account_id, language, replies_count, reblogs_count, favourites_count, quotes_count, pinned, card_json, can_quote
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            favourited = excluded.favourited,
            reblogged = excluded.reblogged,
            bookmarked = excluded.bookmarked,
            content = excluded.content,
            author_name = excluded.author_name,
            author_username = excluded.author_username,
            nsfw = excluded.nsfw,
            spoiler_text = excluded.spoiler_text,
            visibility = excluded.visibility,
            media_json = excluded.media_json,
            urls_json = excluded.urls_json,
            reblog_by_name = excluded.reblog_by_name,
            reblog_by_username = excluded.reblog_by_username,
            application = excluded.application,
            has_poll = excluded.has_poll,
            mentions_json = excluded.mentions_json,
            reblog_of_id = excluded.reblog_of_id,
            quote_json = excluded.quote_json,
            filtered_by = excluded.filtered_by,
            edited_at = excluded.edited_at,
            in_reply_to_id = excluded.in_reply_to_id,
            in_reply_to_account_id = excluded.in_reply_to_account_id,
            language = excluded.language,
            replies_count = excluded.replies_count,
            reblogs_count = excluded.reblogs_count,
            favourites_count = excluded.favourites_count,
            quotes_count = excluded.quotes_count,
            pinned = excluded.pinned,
            card_json = excluded.card_json,
            can_quote = excluded.can_quote;
        """
        db_id = f"{account_id}:{toot_id}"

        # Para el FTS condicional: leer lo que YA había indexado (antes de sobrescribir). Si el
        # texto, el autor o el CW no cambian (caso típico del refresco de solo contadores), nos
        # ahorramos el DELETE+INSERT de FTS, que es de lo más caro de SQLite.
        _prev = _q1("SELECT content, author_name, author_username, spoiler_text "
                    "FROM mastodon_statuses WHERE id = ?", (db_id,))

        _ej(sql, (
            db_id,
            account_id,
            toot_id,
            raw.get("uri", ""),
            author_name,
            author_username,
            texto_limpio,
            created_at_str,
            raw.get("visibility", "public"),
            1 if raw.get("sensitive") else 0,
            spoiler_text,
            1 if raw.get("favourited") else 0,
            1 if raw.get("reblogged") else 0,
            1 if raw.get("bookmarked") else 0,
            media_json,
            urls_json,
            reblog_by_name,
            reblog_by_username,
            application,
            has_poll,
            mentions_json,
            reblog_of_id,
            quote_json,
            filtered_by,
            edited_at,
            in_reply_to_id,
            in_reply_to_account_id,
            language,
            replies_count,
            reblogs_count,
            favourites_count,
            quotes_count,
            pinned,
            card_json,
            can_quote
        ))

        # 2. Indexar en FTS5 SOLO si cambió lo indexable (texto, autor o CW). Si la fila ya estaba
        # y esos campos no cambian (refresco de contadores), se salta: 2 escrituras menos por toot.
        _reindexar_fts = (_prev is None
                          or (_prev[0], _prev[1], _prev[2], _prev[3])
                          != (texto_limpio, author_name, author_username, spoiler_text))
        try:
            if _reindexar_fts:
                # Primero borrar el índice anterior y reinsertar.
                _ej("DELETE FROM mastodon_statuses_fts WHERE status_db_id = ?", (db_id,))
                _ej("INSERT INTO mastodon_statuses_fts (status_db_id, author_name, author_username, content, spoiler_text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (db_id, author_name, author_username, texto_limpio, spoiler_text))
        except Exception as fts_err:
            log.debug(f"Error al indexar FTS5 de toot: {fts_err}")
            
        return MastodonStatus(
            id=db_id,
            account_id=account_id,
            status_id=toot_id,
            uri=raw.get("uri", ""),
            author_name=author_name,
            author_username=author_username,
            content=texto_limpio,
            created_at=created_at_str,
            visibility=raw.get("visibility", "public"),
            nsfw=bool(raw.get("sensitive")),
            spoiler_text=spoiler_text,
            favourited=bool(raw.get("favourited")),
            reblogged=bool(raw.get("reblogged")),
            bookmarked=bool(raw.get("bookmarked")),
            media_attachments=media_attachments,
            reblog_by_name=reblog_by_name,
            reblog_by_username=reblog_by_username,
            card=card if isinstance(card, dict) else None,
            edited_at=edited_at or None,
            in_reply_to_id=in_reply_to_id or None,
            in_reply_to_account_id=in_reply_to_account_id or None,
            language=language or None,
            replies_count=replies_count,
            reblogs_count=reblogs_count,
            favourites_count=favourites_count,
            quotes_count=quotes_count,
            pinned=bool(pinned),
        )

    def _procesar_y_guardar_notificacion(self, account_id: int, raw: Dict[str, Any], conn=None) -> Dict[str, Any]:
        """Procesa una notificación recibida de la API y la guarda en SQLite. Acepta `conn` para
        agruparse en la transacción del llamador (ver db.transaccion())."""
        def _ej(_sql, _params=()):
            if conn is not None:
                conn.execute(_sql, _params)
            else:
                db.execute(_sql, _params)
        notif_id = str(raw["id"])
        db_id = f"{account_id}_{notif_id}"
        type_val = raw["type"]
        
        created_at_str = ""
        if isinstance(raw.get("created_at"), datetime):
            created_at_str = raw["created_at"].isoformat()
        else:
            created_at_str = str(raw.get("created_at", ""))
            
        from app.core.html_utils import aplicar_emojis_personalizados
        author = raw.get("account", {})
        from_name = author.get("display_name") or author.get("username", "Desconocido")
        from_name = aplicar_emojis_personalizados(from_name, author.get("emojis"))
        from_username = f"@{author.get('acct')}" if author.get("acct") else ""

        status_id_val = None
        status_raw = raw.get("status")
        if status_raw:
            # Procesar y guardar el toot asociado para que esté en la base de datos
            self._procesar_y_guardar_toot(account_id, status_raw, conn=conn)
            status_id_val = str(status_raw["id"])

        # Detalle de los tipos ESPECIALES de notificación (texto del aviso de moderación, relaciones
        # cortadas, informe de admin). Sin esto, "[Aviso de moderación]" no decía nada útil.
        extra = {}
        mw = raw.get("moderation_warning")
        if isinstance(mw, dict):
            extra["moderation_warning"] = {
                "action": mw.get("action", ""), "text": mw.get("text", "")}
        ev = raw.get("relationship_severance_event") or raw.get("event")
        if isinstance(ev, dict):
            extra["severance"] = {
                "type": ev.get("type", ""), "target_name": ev.get("target_name", ""),
                "followers_count": ev.get("followers_count", 0),
                "following_count": ev.get("following_count", 0)}
        rep = raw.get("report")
        if isinstance(rep, dict):
            extra["report"] = {
                "category": rep.get("category", ""), "comment": rep.get("comment", "")}
        # 4.6: "te han añadido a una colección" / "una colección en la que estás cambió".
        col = raw.get("collection")
        if isinstance(col, dict):
            extra["collection"] = {
                "id": str(col.get("id") or ""), "name": col.get("name", "") or "",
                "description": col.get("description", "") or ""}
        extra_json = json.dumps(extra) if extra else None

        sql = """
        INSERT INTO mastodon_notifications (
            id, account_id, notification_id, type, created_at, from_name, from_username, status_id, extra_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            type = excluded.type,
            created_at = excluded.created_at,
            from_name = excluded.from_name,
            from_username = excluded.from_username,
            status_id = excluded.status_id,
            extra_json = excluded.extra_json;
        """
        _ej(sql, (
            db_id,
            account_id,
            notif_id,
            type_val,
            created_at_str,
            from_name,
            from_username,
            status_id_val,
            extra_json
        ))

        return {
            "id": db_id,
            "account_id": account_id,
            "notification_id": notif_id,
            "type": type_val,
            "created_at": created_at_str,
            "from_name": from_name,
            "from_username": from_username,
            "status_id": status_id_val,
            "extra_json": extra_json,
        }

    # --- INTERACCIONES DIRECTAS ---
