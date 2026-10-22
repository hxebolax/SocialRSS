# -*- coding: utf-8 -*-
"""MainFrame (mixin): telegram_chats.
Metodos: _guardar_foco_buffer, _es_buffer_persistente, _persistir_posiciones_buffer, _cargar_posiciones_buffer, _insertar_items_incremental, _actualizar_lista_chats, _restaurar_foco_lista, _construir_columnas_plantilla ...
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
import wx

from app.core.config_manager import config, limite_render
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.speech import speak, notificar

from app.services.mastodon.mastodon_service import mastodon_service


log = logging.getLogger(__name__)
from .ui_helpers import (formatear_tiempo_relativo,
                         evento_media_de_attachments, medir_carga)


class _Mf_telegram_chats:
    def _es_lista_notif_mastodon(self) -> bool:
        """True si la lista visible es Notificaciones/Menciones de Mastodon con su lista
        paralela de ids de notificación (_notif_db_ids) alineada fila a fila."""
        notif = getattr(self, "_notif_db_ids", None)
        return bool(notif and self.active_mastodon_account_id
                    and self.active_timeline_type in ("notifications", "mentions")
                    and getattr(self, "_modo_lista", "") == "toots"
                    and len(notif) == len(self.lista_items_db_ids))

    def _claves_foco(self):
        """Clave ÚNICA de cada fila del buffer activo (paralela a lista_items_db_ids) para
        recordar y restaurar la posición. En Notificaciones/Menciones de Mastodon la fila
        apunta al TOOT y varias notificaciones comparten toot (tres favoritos a la misma
        publicación): con el id del toot, al actualizar la posición saltaba a la PRIMERA
        fila de ese toot. Ahí la clave es el id de la notificación."""
        if self._es_lista_notif_mastodon():
            return self._notif_db_ids
        return self.lista_items_db_ids

    def _guardar_foco_buffer(self):
        """Recuerda el id del elemento enfocado en el buffer activo."""
        idx = self.lista_items.GetFocusedItem()
        claves = self._claves_foco()
        if idx is not None and 0 <= idx < len(claves):
            self._buffer_focus[self._buffer_key()] = claves[idx]

    def _es_buffer_persistente(self, key) -> bool:
        """Indica si la posición de un buffer debe RECORDARSE entre sesiones (al
        cerrar y reabrir la app). Solo: Inicio de Mastodon y Bluesky (por cuenta), y
        los buffers de RSS Todos, No leídos y Hoy (por cuenta)."""
        if not key:
            return False
        if key.startswith(("m:", "b:")) and key.endswith(":home"):
            return True
        if key.startswith("x:") and (key.endswith(":home") or key.endswith(":following")):
            return True
        if key.startswith("r:") and (key.endswith(":all:None:None")
                                     or key.endswith(":unread:None:None")
                                     or key.endswith(":today:None:None")):
            return True
        return False

    def _persistir_posiciones_buffer(self):
        """Guarda en disco la última posición (id del elemento) de los buffers que se
        recuerdan entre sesiones, para retomar donde lo dejaste al reabrir la app."""
        try:
            self._guardar_foco_buffer()  # capturar también la del buffer activo ahora
        except Exception:
            pass
        try:
            datos = {k: v for k, v in self._buffer_focus.items()
                     if self._es_buffer_persistente(k)}
            config.set("general", "buffer_positions", datos)
        except Exception as e:
            log.debug(f"No se pudieron guardar las posiciones de buffer: {e}")

    def _cargar_posiciones_buffer(self):
        """Carga del disco las posiciones recordadas de los buffers persistentes, para
        que al ir a Inicio (Mastodon/Bluesky) o a Todos/No leídos/Hoy (RSS) tras
        reiniciar, el foco quede donde lo dejaste."""
        try:
            datos = config.get("general", "buffer_positions", {})
            if isinstance(datos, dict):
                for k, v in datos.items():
                    if self._es_buffer_persistente(k):
                        self._buffer_focus[k] = v
        except Exception as e:
            log.debug(f"No se pudieron cargar las posiciones de buffer: {e}")

    def _insertar_items_incremental(self, filas):
        """Inserta en la lista visible SOLO los elementos nuevos, en su posición
        correcta por fecha, SIN reconstruir ni mover el foco (estilo TWBlue/SimpleRSS).

        'filas' es el estado deseado completo, ordenado (más nuevo primero):
        lista de tuplas (db_id, [valores_columnas], lectura). Los que ya están en la
        lista se dejan en su sitio; los nuevos se insertan donde les corresponde (más
        nuevos que tu item: arriba; más viejos: debajo). No se eliminan elementos (en
        un sondeo no desaparecen; la limpieza se hace al pulsar F5). Devuelve cuántos
        se insertaron.

        Como no se llama a Focus/Select, el control nativo conserva el foco en tu
        elemento (desplazando su índice si hace falta) y el lector no anuncia nada.
        """
        # Notificaciones/Menciones de Mastodon: la fila puede traer en [5] su id de
        # notificación, que es la clave única (varias notificaciones comparten toot). La
        # lista paralela _notif_db_ids se mantiene alineada al insertar; si no, quedaba
        # desfasada y el marcador de lectura y "Descartar" apuntaban a otra notificación.
        es_notif = self._es_lista_notif_mastodon() or (
            not self.lista_items_db_ids and self.active_mastodon_account_id
            and self.active_timeline_type in ("notifications", "mentions")
            and getattr(self, "_modo_lista", "") == "toots")
        if es_notif:
            notif_ids = self._notif_db_ids if self.lista_items_db_ids else []
            self._notif_db_ids = notif_ids

            def _clave(f):
                return f[5] if len(f) > 5 and f[5] else f[0]
            actuales = set(notif_ids)
        else:
            def _clave(f):
                return f[0]
            actuales = set(self.lista_items_db_ids)
        # Si no hay nada nuevo, no tocar nada (evita un Freeze/Thaw inútil cada sondeo).
        if not any(_clave(f) not in actuales for f in filas):
            return 0
        # Las novedades se insertan EN LOTE en sus posiciones finales (insertar_filas_lote):
        # un solo reindexado del modelo y UN solo movimiento silencioso del foco, en vez de
        # uno por fila (que con buffers grandes costaba O(k·n) en el hilo de la interfaz).
        # La actualización sigue siendo silenciosa: el foco queda sobre TU elemento y el
        # lector no lo vuelve a leer (marca de ventana para el complemento de NVDA).
        # Freeze/Thaw: la lista se repinta una sola vez al final, no por cada fila.
        nuevos = []   # (pos_final, fila_completa)
        for i, fila in enumerate(filas):
            if _clave(fila) not in actuales:
                nuevos.append((i, fila))
        self.lista_items.Freeze()
        try:
            self.lista_items.insertar_filas_lote([(i, f[1], f[2]) for i, f in nuevos])
            col_fecha = self._col_de_campo(getattr(self, "_cols_actuales", []), "date")
            for i, fila in nuevos:
                db_id, valores = fila[0], fila[1]
                media_ev = fila[3] if len(fila) > 3 else None
                fecha_iso = fila[4] if len(fila) > 4 else None
                if media_ev:
                    self.lista_items.establecer_medios(i, media_ev)
                # Registrar la fecha (en SU columna según la plantilla) para actualizar el
                # tiempo relativo al navegar, también en los elementos recién llegados.
                if fecha_iso and col_fecha is not None:
                    rel = valores[col_fecha] if col_fecha < len(valores) else ""
                    self.lista_items.establecer_fecha(i, col_fecha, fecha_iso, rel)
                # Inserciones en orden ascendente de posición final: equivalen al estado final.
                self.lista_items_db_ids.insert(i, db_id)
                if es_notif:
                    self._notif_db_ids.insert(i, _clave(fila))
        finally:
            self.lista_items.Thaw()
        return len(nuevos)

    def _actualizar_lista_chats(self, filas):
        """Para listas tipo chat (chats de Telegram, DMs de Bluesky): actualiza el
        contenido de los chats existentes EN SU SITIO (se ve el nuevo último mensaje
        y el nº de no leídos) e inserta los chats nuevos en su posición, todo SIN
        mover el foco. No reordena los chats existentes en vivo (eso movería items
        alrededor de tu foco); el reordenado definitivo se hace al pulsar F5.
        Devuelve el nº de chats nuevos insertados."""
        # 1. Actualizar en su sitio SOLO los chats que cambiaron de contenido
        #    (comparando su cadena de lectura), no todos: así no se repintan cientos
        #    de filas por sondeo.
        actuales = {db_id: idx for idx, db_id in enumerate(self.lista_items_db_ids)}
        cambiados = []
        for db_id, cols, lectura in filas:
            idx = actuales.get(db_id)
            if idx is not None and self.lista_items.obtener_lectura(idx) != lectura:
                cambiados.append((idx, cols, lectura))
        if cambiados:
            self.lista_items.Freeze()
            try:
                for idx, cols, lectura in cambiados:
                    self.lista_items.actualizar_fila(idx, cols, lectura)
            finally:
                self.lista_items.Thaw()
        # 2. Insertar los nuevos en su posición (sin tocar el foco).
        return self._insertar_items_incremental(filas)

    def _restaurar_foco_lista(self):
        """
        Restaura el foco al elemento recordado del buffer activo. Si es la primera
        vez en este buffer, sitúa el foco en el primer elemento. No usa Select para
        no disparar efectos secundarios (como marcar leído).
        """
        n = self.lista_items.GetItemCount()
        if n == 0:
            return
        objetivo = 0
        claves = self._claves_foco()
        recordado = self._buffer_focus.get(self._buffer_key())
        if recordado is not None and recordado in claves:
            objetivo = claves.index(recordado)
        else:
            # El elemento recordado ya no está en el buffer (p. ej. artículo leído en
            # "No leídos", elemento borrado...): restaurar la POSICIÓN que ocupaba, no el
            # primero. Así, tras leer un artículo con lectura limpia sigues donde estabas
            # (sobre el siguiente artículo), como hace SimpleRSS.
            idx_recordado = self._buffer_focus_idx.get(self._buffer_key())
            if idx_recordado is not None:
                objetivo = max(0, min(int(idx_recordado), n - 1))
        # Dejar recordado el elemento que va a quedar bajo el foco (Focus() sin Select no
        # dispara el evento de selección, así que se apunta aquí explícitamente).
        if 0 <= objetivo < len(claves):
            self._buffer_focus[self._buffer_key()] = claves[objetivo]
            self._buffer_focus_idx[self._buffer_key()] = objetivo
        # En una ACTUALIZACIÓN manual (Ctrl+Shift+R) el usuario sigue en su sitio: restaurar el foco
        # SIN que el lector vuelva a locutar el elemento (si no, se "metía" la lectura del elemento
        # enfocado entre el "Actualizando..." y el resultado). Al CAMBIAR de buffer sí se locuta.
        if getattr(self, "_recarga_silenciosa", False) and hasattr(self.lista_items, "_mover_foco_silencioso"):
            self.lista_items._mover_foco_silencioso(objetivo)
        else:
            self.lista_items.Focus(objetivo)
        self.lista_items.EnsureVisible(objetivo)

    # --- COLUMNAS A PARTIR DE LA PLANTILLA ---
    # La plantilla elegida en el Editor define QUÉ columnas tiene el buffer y en qué orden (una
    # por cada {campo}). Se aplica al construir la lista, de modo que la vista queda alineada con
    # la plantilla para quien ve y para el lector (que lee las columnas), sin alt-speech.
    _ANCHOS_COLUMNA = {
        "content": 620, "title": 620, "bio": 480, "author": 160, "display": 160,
        "username": 180, "acct": 180, "feed": 150, "type": 110, "date": 140,
        "visibility": 100, "indicators": 130, "media": 120, "flags": 100, "reply_to": 200,
        "followers": 100, "following": 100, "posts": 110, "num": 60,
        "chat": 220, "unread": 90,
    }

    def _construir_columnas_plantilla(self, clave_template: str):
        """Borra las columnas actuales y crea las de la plantilla indicada. Devuelve la lista
        ordenada de (campo, encabezado) para rellenar cada fila por campo."""
        from app.core.plantillas import columnas_de_plantilla, definicion
        plantilla = config.get("accessibility", clave_template, "") or ""
        cols = columnas_de_plantilla(plantilla)
        if not cols:
            # Plantilla sin campos: caer al valor por defecto para no dejar la lista sin columnas.
            d = definicion(clave_template)
            cols = columnas_de_plantilla(d["default"]) if d else [("content", _("Contenido"))]
        while self.lista_items.GetColumnCount() > 0:
            self.lista_items.DeleteColumn(0)
        for i, (campo, encabezado) in enumerate(cols):
            self.lista_items.InsertColumn(i, encabezado, width=self._ANCHOS_COLUMNA.get(campo, 130))
        # Recordar el mapeo de columnas del buffer actual, para que los sitios que actualizan
        # una celda concreta en caliente (estado RSS, fecha...) sepan en qué columna está cada campo.
        self._cols_actuales = cols
        # Columna de autor para la navegación por "unidades de movimiento" (Alt+flechas): salto
        # al siguiente del mismo autor. Se usa 'author' y, si no, 'username'/'display'.
        col_autor = self._col_de_campo(cols, "author")
        if col_autor is None:
            col_autor = self._col_de_campo(cols, "username")
        if col_autor is None:
            col_autor = self._col_de_campo(cols, "display")
        try:
            self.lista_items._col_autor_nav = col_autor
        except Exception:
            pass
        return cols

    def _volcar_fila_plantilla(self, fila: int, cols, valores: dict) -> int:
        """Inserta una fila rellenando cada columna con el valor de su campo (según 'cols').
        Devuelve el índice de la fila insertada."""
        pos = 0
        for i, (campo, _enc) in enumerate(cols):
            val = valores.get(campo, "") or ""
            if i == 0:
                pos = self.lista_items.InsertItem(fila, val)
            else:
                self.lista_items.SetItem(pos, i, val)
        return pos

    @staticmethod
    def _col_de_campo(cols, campo: str):
        """Índice de la columna de un campo dentro de 'cols', o None si no está."""
        for i, (c, _enc) in enumerate(cols):
            if c == campo:
                return i
        return None

    # --- TIMELINES MASTODON ---
    @medir_carga
    def _cargar_listado_toots(self, cache_only: bool = True):
        # Congelar el repintado durante la recarga; se descongela al terminar (cubre
        # todas las salidas del método). Así la lista se repinta una sola vez.
        self.lista_items.congelar_diferido()
        self.lista_items.DeleteAllItems()
        self.lista_items.limpiar_lecturas()
        self.lista_items.reset_focus_col()
        self.lista_items_db_ids.clear()
        
        # Las columnas se crean MÁS ABAJO a partir de la plantilla del buffer concreto
        # (toots, menciones o notificaciones), o dentro de las funciones delegadas
        # (cuentas, anuncios). Aquí solo nos aseguramos de partir sin columnas viejas.
        while self.lista_items.GetColumnCount() > 0:
            self.lista_items.DeleteColumn(0)

        if not self.active_mastodon_account_id:
            return

        # Buffers de listas de CUENTAS (seguidores, siguiendo, bloqueados, silenciados)
        if self.active_timeline_type in ("followers", "following", "blocked", "muted"):
            self._cargar_lista_cuentas_mastodon(self.active_timeline_type, cache_only)
            return
        # Buffer de ANUNCIOS del servidor
        if self.active_timeline_type == "announcements":
            self._cargar_anuncios_mastodon(cache_only)
            return

        # En el resto de buffers la lista contiene toots
        self._modo_lista = "toots"

        # Columnas según la plantilla del buffer (menciones, notificaciones o publicaciones).
        if self.active_timeline_type == "mentions":
            _clave_tpl = "mention_template"
        elif self.active_timeline_type == "notifications":
            _clave_tpl = "notification_template"
        else:
            _clave_tpl = "mastodon_template"
        cols_buffer = self._construir_columnas_plantilla(_clave_tpl)

        # Si el tipo de timeline es notificaciones, usar consulta separada
        if self.active_timeline_type in ("notifications", "mentions"):
            # SIEMPRE se traen los MÁS RECIENTES (DESC + LIMIT) y, si el usuario quiere "antiguos
            # primero", se INVIERTE la lista para presentarlos de viejo a nuevo. Antes se hacía
            # ASC + LIMIT, que traía los más ANTIGUOS de la base y dejaba fuera los recientes.
            _newest_notif = config.get("general", "newest_first", True)
            order_notif = "DESC"
            # Separación estilo TWBlue: el buffer MENCIONES muestra solo las menciones; el de
            # NOTIFICACIONES, todo lo DEMÁS (favoritos, impulsos, seguidores, encuestas...).
            if self.active_timeline_type == "mentions":
                filtro_tipo = "AND n.type = 'mention'"
            else:
                filtro_tipo = "AND n.type != 'mention'"
            sql = f"""
            SELECT n.id, n.type, n.created_at, n.from_name, n.from_username, n.status_id, s.content, s.visibility, COALESCE(n.extra_json,''), s.filtered_by
            FROM mastodon_notifications n
            LEFT JOIN mastodon_statuses s ON s.account_id = n.account_id AND s.status_id = n.status_id
            WHERE n.account_id = ? {filtro_tipo}
            ORDER BY n.created_at {order_notif} LIMIT {limite_render("mastodon")}
            """
            rows = db.query(sql, (self.active_mastodon_account_id,))
            if not _newest_notif:
                rows = list(rows)[::-1]
            self._notif_db_ids = []

            visibilidad_map = {
                "public": _("Público"),
                "unlisted": _("No listado"),
                "private": _("Privado"),
                "direct": _("Mensaje directo")
            }
            
            _ocultar_emojis_nombres = config.get("accessibility", "hide_emojis_names", False)
            # Import y regex UNA vez para todo el bucle (no por fila).
            from app.core.html_utils import quitar_emojis as _qe
            import re as _re_pref
            _re_quitar_prefijo = _re_pref.compile(r'^\[[^\]]*\]\s*')
            from app.services.mastodon.filtros_servidor import (
                evaluar as _eval_filtro_srv, asegurar_vigentes, repintar_buffer_activo)
            _acc_f = self.active_mastodon_account_id
            asegurar_vigentes(_acc_f, al_cargar=lambda: repintar_buffer_activo(self, _acc_f))
            idx = -1
            for r in rows:
                (notif_db_id, ntype, ndate, from_name, from_username, status_id, status_content,
                 visibility, extra_json, filtered_by) = r
                # Filtro del servidor con acción "ocultar" en el contexto Notificaciones: la
                # notificación no se muestra (como la web oficial).
                _ev_srv = _eval_filtro_srv(filtered_by, self.active_mastodon_account_id, "notifications")
                if _ev_srv["accion"] == "hide":
                    continue
                # 'Avisar': el texto queda tras "[Filtrado: título]" hasta "Mostrar de todos modos".
                if status_id and status_content:
                    from app.services.mastodon.filtros_servidor import contenido_con_filtro
                    status_content = contenido_con_filtro(
                        status_content, _ev_srv, f"{self.active_mastodon_account_id}:{status_id}")
                idx += 1
                if _ocultar_emojis_nombres and from_name:
                    from_name = _qe(from_name)

                from .ui_helpers import prefijo_notificacion_mastodon
                tipo_prefijo = prefijo_notificacion_mastodon(ntype)
                
                # Detalle de los tipos especiales (texto del aviso, relaciones cortadas, informe).
                detalle_especial = ""
                if extra_json:
                    try:
                        _ex = json.loads(extra_json)
                    except Exception:
                        _ex = {}
                    mw = _ex.get("moderation_warning")
                    if isinstance(mw, dict):
                        partes_mw = [p for p in (mw.get("action", ""), mw.get("text", "")) if p]
                        detalle_especial = " — ".join(partes_mw)
                    sev = _ex.get("severance")
                    if isinstance(sev, dict):
                        detalle_especial = _("{tipo}: {n} seguidores y {m} seguidos afectados").format(
                            tipo=sev.get("type", ""), n=sev.get("followers_count", 0),
                            m=sev.get("following_count", 0))
                    rep = _ex.get("report")
                    if isinstance(rep, dict):
                        detalle_especial = " — ".join(p for p in (rep.get("category", ""), rep.get("comment", "")) if p)
                    col = _ex.get("collection")
                    if isinstance(col, dict) and col.get("name"):
                        detalle_especial = _("{autor}: colección «{nombre}»").format(
                            autor=from_name or from_username or "", nombre=col.get("name"))

                if status_content:
                    # Mensaje COMPLETO en la columna Contenido (sin recortar), igual que en el
                    # resto de buffers, para poder leerlo entero al navegar por columnas.
                    texto_toot = status_content.replace('\n', ' ').strip()
                    contenido_str = f"{tipo_prefijo} {from_username}: {texto_toot}"
                elif detalle_especial:
                    contenido_str = f"{tipo_prefijo} {detalle_especial}"
                else:
                    if ntype == "follow":
                        contenido_str = f"{tipo_prefijo} {from_name} ({from_username}) comenzó a seguirte."
                    elif ntype == "follow_request":
                        contenido_str = f"{tipo_prefijo} {from_name} ({from_username}) solicitó seguirte."
                    elif ntype == "poll":
                        contenido_str = f"{tipo_prefijo} Una encuesta ha finalizado."
                    else:
                        contenido_str = f"{tipo_prefijo} de {from_name} ({from_username})"
                
                fecha_rel = formatear_tiempo_relativo(ndate)
                vis_trad = visibilidad_map.get(visibility, "") if visibility else ""

                # Contenido limpio para su columna: el texto del toot si lo hay; si no, la
                # descripción (sin el prefijo "[Tipo]", que ya va en su propia columna).
                desc_sin_prefijo = _re_quitar_prefijo.sub('', contenido_str)
                contenido_val = (status_content.replace('\n', ' ').strip()
                                 if status_content else desc_sin_prefijo)
                # Alias local (N9): también en notificaciones se lee el alias, no el nombre.
                _alias_n = (config.get("mastodon", "alias_local", {}) or {}).get(
                    (from_username or "").lstrip("@"))
                valores = {
                    "type": tipo_prefijo.strip("[]"),
                    "author": _alias_n or from_name or "",
                    "username": from_username or "",
                    "content": contenido_val or "",
                    "date": fecha_rel or "",
                    "visibility": vis_trad or "",
                }
                pos = self._volcar_fila_plantilla(idx, cols_buffer, valores)

                if status_id:
                    self.lista_items_db_ids.append(f"{self.active_mastodon_account_id}:{status_id}")
                else:
                    self.lista_items_db_ids.append(notif_db_id)
                # Id de notificación (db_id "cuenta:notif_id") en paralelo, para poder
                # descartar la notificación enfocada aunque la fila apunte a un toot.
                self._notif_db_ids.append(notif_db_id)

                col_fecha = self._col_de_campo(cols_buffer, "date")
                if col_fecha is not None:
                    self.lista_items.establecer_fecha(pos, col_fecha, ndate, fecha_rel)
                
            # (Sin aviso al estar vacío: los buffers se rellenan solos al arrancar y al entrar; no
            # se molesta al usuario con mensajes en los buffers sin elementos.)

            # Igual que el resto de buffers: al entrar, enfocar la primera entrada (o la posición
            # recordada), para que el lector la lea y al volver retome donde lo dejaste.
            # (Se mira ANTES si había posición local: _restaurar_foco_lista la deja apuntada.)
            _habia_pos = self._buffer_key() in self._buffer_focus
            self._restaurar_foco_lista()
            # A11: primera visita de la sesión sin posición local -> retomar desde el
            # marcador de lectura del SERVIDOR (sincronizado con tus otros clientes).
            self._restaurar_marcador_lectura(_habia_pos)

            if not cache_only:
                event_bus.emit("sound_trigger", "timeline_refreshed")
            return

        # Consultar la caché local en la base de datos para timelines normales
        # Siempre los MÁS RECIENTES (DESC + LIMIT); si se quiere "antiguos primero" se invierte
        # la lista al presentar (ver más abajo), en vez de traer los más antiguos de la base.
        _newest_toots = config.get("general", "newest_first", True)
        order_by = "ORDER BY s.created_at DESC"
        
        es_timeline_toots = (
            self.active_timeline_type in ("home", "local", "public", "direct", "bookmarks", "favourites", "sent", "trending")
            or str(self.active_timeline_type).startswith("search:")
            or str(self.active_timeline_type).startswith("list:")
            or str(self.active_timeline_type).startswith("user:")
            or str(self.active_timeline_type).startswith("thread:")
        )
        if es_timeline_toots:
            sql = f"""
            SELECT s.id, s.author_name, s.author_username, s.content, s.created_at, s.visibility, s.favourited, s.reblogged, s.bookmarked, s.media_json, s.spoiler_text, s.reblog_by_name, s.reblog_by_username, s.application, s.quote_json, s.filtered_by, COALESCE(s.has_poll,0), COALESCE(s.edited_at,''), COALESCE(s.in_reply_to_id,''), COALESCE(s.replies_count,0), COALESCE(s.reblogs_count,0), COALESCE(s.favourites_count,0), COALESCE(s.card_json,''), COALESCE(s.pinned,0), COALESCE(s.language,'')
            FROM mastodon_statuses s
            JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id
            WHERE t.account_id = ? AND t.timeline = ?
            {order_by} LIMIT {limite_render("mastodon")}
            """
            params = [self.active_mastodon_account_id, self.active_timeline_type]
        else:
            # Fallback
            sql = f"""
            SELECT s.id, s.author_name, s.author_username, s.content, s.created_at, s.visibility, s.favourited, s.reblogged, s.bookmarked, s.media_json, s.spoiler_text, s.reblog_by_name, s.reblog_by_username, s.application, s.quote_json, s.filtered_by, COALESCE(s.has_poll,0), COALESCE(s.edited_at,''), COALESCE(s.in_reply_to_id,''), COALESCE(s.replies_count,0), COALESCE(s.reblogs_count,0), COALESCE(s.favourites_count,0), COALESCE(s.card_json,''), COALESCE(s.pinned,0), COALESCE(s.language,'')
            FROM mastodon_statuses s
            WHERE s.account_id = ?
            """
            params = [self.active_mastodon_account_id]
            
            # Filtrar según tipo de timeline
            if self.active_timeline_type == "bookmarks":
                sql += " AND s.bookmarked = 1"
            elif self.active_timeline_type == "favourites":
                sql += " AND s.favourited = 1"
            elif self.active_timeline_type == "direct":
                sql += " AND s.visibility = 'direct'"
            elif self.active_timeline_type == "local":
                sql += " AND s.author_username NOT LIKE '%@%@%'"
            sql += f" {order_by} LIMIT {limite_render('mastodon')}"
            
        rows = db.query(sql, tuple(params))
        if not _newest_toots:
            rows = list(rows)[::-1]

        visibilidad_map = {
            "public": _("Público"),
            "unlisted": _("No listado"),
            "private": _("Privado"),
            "direct": _("Mensaje directo")
        }

        # Filtros de contenido (se precargan una vez para toda la lista).
        from app.core.content_filters import ContentFilterEngine
        filtros_mastodon = ContentFilterEngine.cargar("mastodon")
        clientes_ignorados = set(config.get("mastodon", "ignored_clients", []))
        # Filtrado de impulsos/respuestas (como TWBlue), solo en los timelines de "todos".
        _filtrar_br = self.active_timeline_type in ("home", "local", "public")
        _show_boosts = config.get("mastodon", "show_boosts", True)
        _show_replies = config.get("mastodon", "show_replies", True)
        # Opciones de presentación del lector
        mostrar_usuario = config.get("accessibility", "show_screen_names", False)
        ocultar_emojis = config.get("accessibility", "hide_emojis", False)
        ocultar_emojis_nombres = config.get("accessibility", "hide_emojis_names", False)
        if ocultar_emojis or ocultar_emojis_nombres:
            from app.core.html_utils import quitar_emojis
        # Alias LOCALES de cuentas (auditoría N9, estilo TWBlue): cambian solo cómo se
        # lee el nombre en las listas; el @usuario real sigue en su columna y en detalles.
        alias_locales = config.get("mastodon", "alias_local", {}) or {}
        # Filtros del SERVIDOR (como la web oficial): 'hide' no se muestra, 'warn' avisa.
        from app.services.mastodon.filtros_servidor import (
            evaluar as _eval_filtro_srv, asegurar_vigentes, repintar_buffer_activo,
            evento_media_filtrado)
        from .mf_filtros_mastodon import componer_contenido_toot
        _expand_cw = config.get("accessibility", "expand_cw", False)
        _acc_f = self.active_mastodon_account_id
        asegurar_vigentes(_acc_f, al_cargar=lambda: repintar_buffer_activo(self, _acc_f))
        _tl_actual = str(self.active_timeline_type or "")
        _foco_hilo = (_tl_actual.split(":", 1)[1] if _tl_actual.startswith("thread:") else None)
        fila = 0

        for r in rows:
            (db_id, author, author_username, text, date, vis, fav, rebl, book, media, spoiler_text,
             reblog_by_name, reblog_by_username, application, quote_json, filtered_by,
             has_poll, edited_at, in_reply_to_id, replies_count, reblogs_count, favourites_count,
             card_json, pinned, idioma_toot) = r

            # Ocultar publicaciones de clientes ignorados.
            if application and application in clientes_ignorados:
                continue

            # Filtrado de impulsos/respuestas según preferencias (solo en Inicio/Local/Federada).
            if _filtrar_br:
                if not _show_boosts and reblog_by_name:
                    continue
                if not _show_replies and in_reply_to_id:
                    continue

            # Aplicar filtros de contenido: en Mastodon honramos "ocultar" (las
            # acciones destacar/leído requieren acción en el servidor o no aplican).
            if filtros_mastodon:
                autor_filtro = f"{author or ''} {author_username or ''} {reblog_by_name or ''}"
                acciones = ContentFilterEngine.evaluar("mastodon", text or "", autor_filtro, filtros_mastodon)
                if acciones["hide"]:
                    continue

            _ev_srv = _eval_filtro_srv(
                filtered_by, self.active_mastodon_account_id, _tl_actual,
                foco_detalle=bool(_foco_hilo) and str(db_id).endswith(":" + _foco_hilo))
            if _ev_srv["accion"] == "hide":
                continue

            # Contenido visible al estilo TWBlue (impulso, CW, cuerpo, cita, tarjeta) con el filtro
            # del servidor aplicado: 'avisar' lo esconde tras "[Filtrado: título]" hasta que se
            # pide "Mostrar de todos modos" (ver mf_filtros_mastodon.componer_contenido_toot).
            contenido = componer_contenido_toot(
                text, spoiler_text, reblog_by_username, author_username, quote_json, card_json,
                media, _ev_srv, db_id, expand_cw=_expand_cw, ocultar_emojis=ocultar_emojis,
                account_id=_acc_f, timeline=_tl_actual)

            # En el campo Autor se muestra quien impulsó (si es boost) o el autor.
            autor_columna = reblog_by_name if reblog_by_name else author
            # Alias local (N9): sustituye el NOMBRE mostrado (nunca el @usuario real).
            # Las claves del mapa van SIN la arroba inicial, para casar en todas las vistas.
            if alias_locales:
                clave_alias = (reblog_by_username if reblog_by_name else author_username) or ""
                autor_columna = alias_locales.get(clave_alias.lstrip("@"), autor_columna)
            if ocultar_emojis_nombres:
                autor_columna = quitar_emojis(autor_columna or "")

            # Fecha relativa y privacidad traducida.
            fecha_rel = formatear_tiempo_relativo(date)
            vis_trad = visibilidad_map.get(vis, vis)

            # Indicadores y adjuntos.
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
                # Protegido: un media_json corrupto en la base NO debe abortar el pintado
                # del buffer entero (la fila se muestra igual, solo sin adjuntos).
                try:
                    attachments = json.loads(media)
                except Exception:
                    attachments = []
                if attachments:
                    if len(attachments) == 1:
                        adj_desc = _("1 archivo adjunto")
                    else:
                        adj_desc = _("{0} archivos adjuntos").format(len(attachments))

            # Valores por campo; la plantilla decide qué columnas hay y en qué orden.
            valores = {
                "author": autor_columna or "",
                "username": author_username or "",
                "content": contenido or "",
                "date": fecha_rel or "",
                "visibility": vis_trad or "",
                "indicators": " ".join(indicadores),
                "media": adj_desc,
                "application": application or "",
                "cw": (spoiler_text or "").strip(),
                "counts": _("{f} favoritos, {r} impulsos, {p} respuestas").format(
                    f=favourites_count, r=reblogs_count, p=replies_count),
                "language": idioma_toot or "",
            }
            pos = self._volcar_fila_plantilla(fila, cols_buffer, valores)
            fila += 1
            self.lista_items_db_ids.append(db_id)

            # Multimedia difuminada por un filtro: sin sonido al enfocar (se pide confirmación al abrirla).
            self.lista_items.establecer_medios(
                pos, evento_media_filtrado(_ev_srv, evento_media_de_attachments(media), db_id))
            col_fecha = self._col_de_campo(cols_buffer, "date")
            if col_fecha is not None:
                self.lista_items.establecer_fecha(pos, col_fecha, date, fecha_rel)

        # (Sin aviso de "lista vacía": no se molesta al usuario en los buffers sin elementos.)

        # Restaurar la posición del usuario (o primer elemento si es la primera vez)
        _habia_pos = self._buffer_key() in self._buffer_focus
        self._restaurar_foco_lista()

        # Marcador de lectura sincronizado (A11): la ESCRITURA se hace ahora con debounce
        # al mover el foco (ver _programar_marcador_lectura), no al cargar el buffer (eso
        # marcaba como leído lo que aún no habías visto). Aquí solo se RESTAURA la posición
        # desde el servidor en la primera visita de la sesión.
        self._restaurar_marcador_lectura(_habia_pos)

        if not cache_only:
            # Notificar sonido
            event_bus.emit("sound_trigger", "timeline_refreshed")

    def _invalidar_cache_cuentas(self):
        """Olvida la caché de las listas de cuentas (seguidores/siguiendo/bloqueados/silenciados)
        para que la próxima vez que entres se vuelvan a obtener de la red. Se llama tras seguir,
        dejar de seguir, bloquear, desbloquear, silenciar o quitar silencio, porque esas acciones
        cambian esas listas."""
        cache = getattr(self, "_cache_cuentas", None)
        if cache:
            cache.clear()

    def _cargar_lista_cuentas_mastodon(self, tipo, cache_only):
        """Carga un buffer de cuentas (seguidores/siguiendo/bloqueados/silenciados).

        Cachea el resultado en memoria por (cuenta, tipo): al NAVEGAR (cache_only) se pinta desde
        la caché SIN llamar a la red; solo se obtiene de la red en la primera visita o al refrescar
        (F5, cache_only=False). Así no se hacen llamadas inútiles cada vez que pasas por el buffer.
        La caché se invalida al seguir/bloquear/silenciar (ver _invalidar_cache_cuentas)."""
        self._modo_lista = "cuentas"
        # Columnas según la plantilla de perfil de usuario.
        cols_cuentas = self._construir_columnas_plantilla("person_template")
        cuenta = self.active_mastodon_account_id
        cache = getattr(self, "_cache_cuentas", None)
        if cache is None:
            cache = self._cache_cuentas = {}
        clave = (cuenta, tipo)

        def _pintar(res):
            # Respetar el ORDEN configurado por el usuario también aquí: la API entrega
            # estas listas con lo más reciente primero; con "antiguos primero" se invierte,
            # igual que los buffers de publicaciones (reporte de usuarios: seguidores,
            # siguiendo, bloqueados y silenciados ignoraban la preferencia).
            if res and not config.get("general", "newest_first", True):
                res = list(reversed(res))
            self.lista_items.congelar_diferido()
            self.lista_items.DeleteAllItems()
            self.lista_items.limpiar_lecturas()
            self.lista_items_db_ids.clear()
            _ocultar_nombres = config.get("accessibility", "hide_emojis_names", False)
            if _ocultar_nombres:
                from app.core.html_utils import quitar_emojis as _qe
            for i, c in enumerate(res or []):
                _display = c["display_name"] or ""
                if _ocultar_nombres and _display:
                    _display = _qe(_display)
                valores = {
                    "display": _display,
                    "acct": "@" + (c["acct"] or ""),
                    "followers": str(c.get("followers", "")),
                    "following": str(c.get("following", "")),
                    "posts": str(c.get("statuses", "")),
                    "bio": (c["bio"] or "").replace("\n", " "),
                }
                self._volcar_fila_plantilla(i, cols_cuentas, valores)
                # Guardamos id y acct para las acciones del menú contextual
                self.lista_items_db_ids.append("user:{0}:{1}".format(c["id"], c["acct"]))
            self._restaurar_foco_lista()

        def _anunciar(res, hay_mas=False):
            if res:
                if hay_mas:
                    speak(_("{0} cuentas mostradas; hay más (Ctrl+Shift+M para cargar "
                            "las siguientes).").format(len(res)), interrupt=True)
                else:
                    speak(_("{0} cuentas.").format(len(res)), interrupt=True)
            else:
                notificar(_("Lista vacía."))

        # NAVEGACIÓN: si ya lo tenemos cacheado, pintar SIN red y SIN anunciar el recuento (como
        # el resto de buffers: navegar es silencioso; solo se restaura el foco a la 1ª/recordada).
        if cache_only and clave in cache:
            _pintar(cache[clave])
            return

        # Token de navegación: si cambias de buffer mientras esta carga está en curso, su respuesta
        # (que llega tarde) NO debe pintarse sobre el buffer nuevo (era el bug "selecciono
        # bloqueados y salen seguidos"). Se cachea igualmente para no repetir la llamada.
        gen = getattr(self, "_nav_gen", 0)

        def _finish(res, err):
            if err:
                if self._nav_vigente(gen):
                    notificar(_("No se pudo cargar la lista: {0}").format(err))
                return
            # Formato paginado (auditoría A15): dict con cuentas y si hay más páginas.
            cuentas_res = (res or {}).get("cuentas", []) if isinstance(res, dict) else (res or [])
            hay_mas = bool((res or {}).get("has_more")) if isinstance(res, dict) else False
            cache[clave] = cuentas_res
            marca_mas = getattr(self, "_cuentas_hay_mas", None)
            if marca_mas is None:
                marca_mas = self._cuentas_hay_mas = {}
            marca_mas[clave] = hay_mas
            if not self._nav_vigente(gen):
                return   # ya no estás en este buffer: no pintar (pero la caché queda lista)
            _pintar(cuentas_res)
            # Solo anunciar el recuento en F5 (acción explícita); al navegar (primera visita o
            # caché) es silencioso, como el resto de buffers.
            if not cache_only:
                _anunciar(cuentas_res, hay_mas)

        if not cache_only:
            speak(_("Cargando..."), interrupt=True)
        mastodon_service.obtener_relaciones_async(cuenta, tipo, callback=_finish)

    def _cargar_mas_cuentas_mastodon(self):
        """Ctrl+Shift+M en un buffer de CUENTAS de Mastodon (auditoría A15): trae la
        siguiente página de seguidores/siguiendo/bloqueados/silenciados y la AÑADE a la
        lista sin mover el foco, anunciando cuántas entraron."""
        tipo = self.active_timeline_type
        cuenta = self.active_mastodon_account_id
        clave = (cuenta, tipo)
        marca_mas = getattr(self, "_cuentas_hay_mas", {})
        if not marca_mas.get(clave):
            speak(_("No hay más cuentas que cargar en esta lista."), interrupt=True)
            return
        speak(_("Cargando más cuentas..."), interrupt=True)
        cache = getattr(self, "_cache_cuentas", None) or {}
        gen = getattr(self, "_nav_gen", 0)

        def _finish(res, err):
            if err:
                notificar(_("No se pudieron cargar más cuentas: {0}").format(err))
                return
            nuevas = (res or {}).get("cuentas", []) if isinstance(res, dict) else []
            marca_mas[clave] = bool((res or {}).get("has_more")) if isinstance(res, dict) else False
            if not nuevas:
                speak(_("No hay más cuentas que cargar en esta lista."), interrupt=True)
                return
            acumuladas = list(cache.get(clave, [])) + nuevas
            cache[clave] = acumuladas
            self._cache_cuentas = cache
            if not self._nav_vigente(gen) or self.active_timeline_type != tipo:
                return
            # Repintar desde la caché acumulada conservando la posición (restaurar foco usa
            # la posición recordada del buffer, que no cambió).
            foco = self.lista_items.GetFocusedItem()
            self._cargar_lista_cuentas_mastodon(tipo, cache_only=True)
            try:
                if foco is not None and foco >= 0:
                    self.lista_items.Focus(foco)
                    self.lista_items.Select(foco)
                    self.lista_items.EnsureVisible(foco)
            except Exception:
                pass
            speak(_("{0} cuentas más cargadas ({1} en total{2}).").format(
                len(nuevas), len(acumuladas),
                _("; hay más") if marca_mas.get(clave) else ""), interrupt=True)

        mastodon_service.obtener_relaciones_async(cuenta, tipo, cargar_mas=True, callback=_finish)

    def _cargar_anuncios_mastodon(self, cache_only):
        """Carga el buffer de anuncios del servidor. Igual que las listas de cuentas: al navegar
        (cache_only) se pinta desde la caché en memoria SIN red; solo se obtiene de la red en la
        primera visita o al refrescar (F5). Evita llamadas inútiles al pasar por el buffer."""
        self._modo_lista = "anuncios"
        cols_anuncios = self._construir_columnas_plantilla("announcement_template")
        cuenta = self.active_mastodon_account_id
        cache = getattr(self, "_cache_anuncios", None)
        if cache is None:
            cache = self._cache_anuncios = {}

        def _pintar(res):
            self.lista_items.congelar_diferido()
            self.lista_items.DeleteAllItems()
            self.lista_items.limpiar_lecturas()
            self.lista_items_db_ids.clear()
            for i, a in enumerate(res or []):
                fecha_rel = formatear_tiempo_relativo(a["fecha"]) if a["fecha"] else ""
                valores = {
                    "content": (a["texto"] or "").replace("\n", " "),
                    "date": fecha_rel,
                }
                pos = self._volcar_fila_plantilla(i, cols_anuncios, valores)
                self.lista_items_db_ids.append("announce:{0}".format(a["id"]))
                col_fecha = self._col_de_campo(cols_anuncios, "date")
                if col_fecha is not None and a["fecha"]:
                    self.lista_items.establecer_fecha(pos, col_fecha, a["fecha"], fecha_rel)
            self._restaurar_foco_lista()

        def _anunciar(res):
            if res:
                speak(_("{0} anuncios.").format(len(res)), interrupt=True)
            else:
                speak(_("No hay anuncios del servidor."), interrupt=True)

        # NAVEGACIÓN: si ya lo tenemos cacheado, pintar SIN red y SIN anunciar el recuento (como
        # el resto de buffers: navegar es silencioso; solo se restaura el foco).
        if cache_only and cuenta in cache:
            _pintar(cache[cuenta])
            return

        def _finish(res, err):
            if err:
                speak(_("No se pudieron cargar los anuncios: {0}").format(err), interrupt=True)
                return
            cache[cuenta] = res or []
            _pintar(res)
            if not cache_only:   # recuento solo en F5; navegar es silencioso
                _anunciar(res)

        if not cache_only:
            speak(_("Cargando anuncios..."), interrupt=True)
        mastodon_service.obtener_anuncios_async(cuenta, callback=_finish)

    def _on_timeline_refreshed(self, account_id, statuses):
        # Recibir actualizaciones del bus de eventos
        if account_id == self.active_mastodon_account_id:
            wx.CallAfter(self._cargar_listado_toots, cache_only=True)

    # --- ARTÍCULOS RSS ---
    @medir_carga
    def _cargar_listado_artículos(self):
        self.lista_items.congelar_diferido()
        self.lista_items.DeleteAllItems()
        self.lista_items.limpiar_lecturas()
        self.lista_items.reset_focus_col()
        self.lista_items_db_ids.clear()
        # FIJAR el modo de lista: sin esto, al venir de la BANDEJA el modo se quedaba en
        # "bandeja" y su sondeo seguía INSERTANDO publicaciones de otras redes (con su
        # formato de origen) encima del buffer RSS (fallo reportado: filas de Bluesky
        # dentro de "No leídos" de RSS).
        self._modo_lista = "rss"
        self._bandeja_activa = False
        
        # Columnas RSS según la plantilla.
        cols_rss = self._construir_columnas_plantilla("rss_template")
        self._enlazar_teclas_rss_lista()   # Suprimir sobre un artículo (mf_rss_organizar.py)

        if not self.active_rss_account_id:
            return
            
        # Cargar de SQLite
        # Filtramos por a.account_id (denormalizado) en vez de f.account_id: así el filtro por
        # cuenta + el ORDER BY a.published_at salen del índice idx_rssart_acc_pub (sin tabla
        # temporal de ordenación). El JOIN se mantiene solo para el título del feed (f.title) y los
        # filtros por categoría/feed; se resuelve por la PK de rss_feeds, que es barata.
        sql = """
        SELECT a.id, f.title, a.title, a.published_at, a.read, a.favorite, a.has_media, COALESCE(a.read_later, 0), a.author
        FROM rss_articles a
        JOIN rss_feeds f ON a.feed_id = f.id
        WHERE a.account_id = ? AND COALESCE(a.deleted, 0) = 0
        """
        params = [self.active_rss_account_id]

        # Filtro del buffer (el MISMO que usa la inserción incremental): rss_buffers.py.
        from app.services.rss.rss_buffers import where_buffer
        extra, extra_params = where_buffer(self.active_timeline_type, self.active_rss_category_id,
                                           self.active_rss_feed_id)
        sql += extra
        params.extend(extra_params)

        order_by = "ORDER BY a.published_at DESC" if config.get("general", "newest_first", True) else "ORDER BY a.published_at ASC"
        sql += f" {order_by} LIMIT {limite_render('rss', 'max_articles_list')}"

        rows = db.query(sql, tuple(params))

        # Metadatos por fila para poder refrescar el estado sin recargar toda la lista
        self._rss_meta = {}

        # Filtros de contenido: ocultar artículos que coincidan (por título o autor).
        from app.core.content_filters import ContentFilterEngine
        filtros_rss = ContentFilterEngine.cargar("rss")
        agrupar = config.get("rss", "group_by_date", False)
        bucket_actual = None
        fila = 0

        for r in rows:
            db_id, feed_title, title, date, leido, fav, has_media, leer_tarde, autor = r

            if filtros_rss and ContentFilterEngine.evaluar("rss", title or "", autor or "", filtros_rss)["hide"]:
                continue

            # Agrupar por fecha: anteponer la etiqueta del grupo al primer
            # artículo de cada bloque (en la columna Feed), de forma accesible.
            feed_col = feed_title
            if agrupar:
                bucket = self._bucket_fecha(date)
                if bucket and bucket != bucket_actual:
                    bucket_actual = bucket
                    feed_col = f"— {bucket} — {feed_title}"

            fecha_rel = formatear_tiempo_relativo(str(date))

            estado = _("Leído") if leido else _("No leído")
            if fav:
                estado += ", " + _("Destacado")
            if leer_tarde:
                estado += ", " + _("Leer más tarde")

            valores = {
                "feed": feed_col,
                "title": title or "",
                "author": autor or "",
                "date": fecha_rel,
                "flags": estado,
            }
            pos = self._volcar_fila_plantilla(fila, cols_rss, valores)
            fila += 1

            self.lista_items_db_ids.append(db_id)

            self._rss_meta[pos] = {
                "feed": feed_title, "title": title, "fecha": fecha_rel,
                "has_media": bool(has_media), "favorite": bool(fav), "read_later": bool(leer_tarde)
            }
            col_fecha = self._col_de_campo(cols_rss, "date")
            if col_fecha is not None:
                self.lista_items.establecer_fecha(pos, col_fecha, str(date), fecha_rel)

        # (Sin aviso al estar vacío: los feeds RSS se comprueban solos; no se molesta al usuario.)

        self._restaurar_foco_lista()
