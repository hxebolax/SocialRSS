# -*- coding: utf-8 -*-
"""MainFrame (mixin): listados_filas.
Metodos: _construir_servicio_telegram, _on_nav_selection, _on_tree_key_down, _mover_en_arbol, _re_seleccionar_telegram_timeline, _re_seleccionar_bluesky_timeline, _re_seleccionar_rss_timeline, _re_seleccionar_nodo ...
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
import wx
from app.core.privacy import abrir_url

from app.core.app_context import app_context
from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar

from app.services.rss.rss_service import rss_service


log = logging.getLogger(__name__)


class _Mf_listados_filas:
    def _construir_servicio_telegram(self, root):
        """Construye el nodo del servicio Telegram con sus cuentas y buffer de chats."""
        n_tg = self.tree_nav.AppendItem(root, _("Telegram"), data={"type": "category_root", "service": "telegram"})

        tg_accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'telegram' AND active = 1 ORDER BY position, id")
        buffer_labels = {
            "all": _("Todos los chats"),
            "unread": _("No leídos"),
            "private": _("Usuarios"),
            "bot": _("Bots"),
            "group": _("Grupos"),
            "supergroup": _("Supergrupos"),
            "channel": _("Canales"),
            "contacts": _("Contactos"),
        }
        for r_id, r_name in tg_accs:
            # Total de no leídos de la cuenta para el contador
            fila = db.query_one("SELECT COALESCE(SUM(unread_count), 0) FROM telegram_chats WHERE account_id = ?", (r_id,))
            total_no_leidos = fila[0] if fila else 0
            n_acc = self.tree_nav.AppendItem(n_tg, self._etiqueta_con_contador(r_name, total_no_leidos), data={"type": "account", "account_id": r_id, "service": "telegram", "base_label": r_name})

            buffer_order = list(config.get("telegram", "buffer_order", ["all", "unread", "private", "bot", "group", "supergroup", "channel", "contacts"]))
            _todos_tg = ["all", "unread", "private", "bot", "group", "supergroup", "channel", "contacts"]
            faltan_tg = [b for b in _todos_tg if b not in buffer_order]
            if faltan_tg:
                buffer_order.extend(faltan_tg)
                config.set("telegram", "buffer_order", buffer_order)
            ocultos_tg = config.get("telegram", "hidden_buffers", [])
            for b in buffer_order:
                if b in ocultos_tg:
                    continue
                label = buffer_labels.get(b)
                if not label:
                    continue
                contador = total_no_leidos if b in ("all", "unread") else 0
                self.tree_nav.AppendItem(
                    n_acc, self._etiqueta_con_contador(label, contador),
                    data={"type": "telegram_timeline", "tg_type": b, "account_id": r_id, "service": "telegram", "base_label": label}
                )

            # Carpetas del usuario como buffers navegables
            carpetas = db.query("SELECT folder_id, title FROM telegram_folders WHERE account_id = ? ORDER BY folder_id", (r_id,))
            for f_id, f_title in carpetas:
                self.tree_nav.AppendItem(
                    n_acc, _("Carpeta: {0}").format(f_title),
                    data={"type": "telegram_timeline", "tg_type": f"folder:{f_id}", "account_id": r_id, "service": "telegram", "base_label": _("Carpeta: {0}").format(f_title)}
                )
        return n_tg

    def _nav_vigente(self, gen):
        """True si NO se ha cambiado de buffer desde que se lanzó una carga con ese token (gen).
        Los callbacks asíncronos lo usan para no pintar resultados de un buffer que ya no es el
        activo (evita que, al navegar rápido, se mezclen elementos de buffers distintos)."""
        return getattr(self, "_nav_gen", 0) == gen

    def _on_nav_selection(self, event):
        # Si estamos reconstruyendo el árbol, IGNORAR los eventos de selección que dispara la
        # reconstrucción (borrar/crear buffers, F5...). NO se autodesactiva aquí: el flag lo
        # baja quien reconstruye (con wx.CallAfter al terminar), para que TODOS los eventos
        # intermedios queden protegidos, no solo el primero (si no, cargar buffers sobre un árbol
        # a medio construir podía colgar la app, p. ej. al borrar una conversación).
        if getattr(self, "_restaurando_arbol", False):
            return
        try:
            if not self or not bool(self.tree_nav):
                return
            item = event.GetItem()
            if not item.IsOk():
                return
            data = self.tree_nav.GetItemData(item)
        except RuntimeError:
            return

        if not data:
            return

        # Token de navegación: cada cambio de nodo incrementa el contador. Las cargas asíncronas
        # capturan el valor al lanzarse y, al volver, comprueban con _nav_vigente() que sigue siendo
        # el buffer actual antes de pintar. Así, si navegas rápido, una respuesta que llega tarde
        # (p. ej. la de "Seguidos") ya NO se pinta sobre el buffer al que has cambiado ("Bloqueados").
        self._nav_gen = getattr(self, "_nav_gen", 0) + 1

        # Barra de estado: actualizar el contexto (buffer + nº de elementos) tras cargar el buffer.
        wx.CallAfter(self._actualizar_barra_estado)

        # A DEBUG: se dispara en CADA selección del árbol (al navegar) y, como INFO, escribía al log
        # en el hilo de la interfaz en cada pulsación. En DEBUG no hace E/S en régimen normal.
        t = data.get("type")
        log.debug(f"Seleccionado elemento del árbol: {t} con datos: {data}")

        # Recordar el último nodo (buffer/cuenta) visitado de cada servicio, para
        # que Alt+1..4 vuelva aquí en vez de al principio del servicio. Al estar en
        # el árbol, marcamos "arbol" como último panel (si luego entras en la lista,
        # _on_item_selected lo cambia a "lista").
        if data.get("service") and t != "category_root":
            self._ultimo_nodo_servicio[data["service"]] = self._clave_seleccion(data)
            self._ultimo_panel_servicio[data["service"]] = "arbol"

        # Reiniciar filtros. DEBEN estar TODAS las redes: la rama por servicio de abajo vuelve a fijar
        # la que toque. Si una se olvida aquí, su id queda RESIDUAL al cambiar de red y los
        # despachadores que la consultan antes (refrescar, seleccionar fila, Intro...) actúan sobre la
        # red equivocada: p. ej. con Mastodon delante, Ctrl+Mayús+R contestaba "25 vídeos nuevos"
        # porque seguía puesta la cuenta de PeerTube.
        self.active_mastodon_account_id = None
        self.active_rss_account_id = None
        self.active_telegram_account_id = None
        self.active_bluesky_account_id = None
        self.active_x_account_id = None
        self.active_irc_account_id = None
        self.active_matrix_account_id = None
        if hasattr(self, "active_xmpp_account_id"):
            self.active_xmpp_account_id = None
        if hasattr(self, "active_reddit_account_id"):
            self.active_reddit_account_id = None
        if hasattr(self, "active_peertube_account_id"):
            self.active_peertube_account_id = None
        if hasattr(self, "active_wsocial_account_id"):
            self.active_wsocial_account_id = None
        self.active_rss_feed_id = None
        self.active_rss_category_id = None
        # Por defecto, el nuevo buffer NO es la bandeja; si lo es, _cargar_listado_bandeja lo marca.
        self._bandeja_activa = False

        # Nodos CONTENEDORES (la raíz de una red o el nodo de una cuenta) NO son un buffer: la lista
        # debe quedar VACÍA. Se hace aquí, de forma centralizada e incondicional, ANTES de las ramas
        # por servicio, para que al situarse en una raíz —sobre todo navegando rápido por el árbol—
        # nunca se quede mostrando (ni anuncie al tabular) el buffer anterior. Como ya se reseteó
        # active_* a None, no hay servicio activo y el sondeo/streaming no actúa sobre nada.
        # EXCEPCIÓN: la bandeja de entrada es un category_root pero SÍ es un buffer cargable (cae en
        # su rama de abajo), así que no se vacía aquí.
        if t in ("category_root", "account") and data.get("service") != "inbox":
            try:
                self.lista_items.DeleteAllItems()
                self.lista_items.limpiar_lecturas()
                self.lista_items.reset_focus_col()
                self.lista_items_db_ids.clear()
                self._modo_lista = "toots"
                self.txt_detalles.Clear()
                self.lbl_buffer_titulo.SetLabel(_("Timeline / Artículos:"))
            except Exception:
                pass
            self._aplicar_servicio_activo()
            self._actualizar_botonera()
            return

        if data.get("service") == "mastodon":
            self.active_mastodon_account_id = data.get("account_id")
            app_context.active_mastodon_account_id = self.active_mastodon_account_id

            # Panel de detalles: opcional por red (Preferencias). Visibilidad centralizada.
            self._aplicar_visibilidad_detalles()

            if t == "timeline":
                self.active_timeline_type = data.get("timeline_type")
                self.lbl_buffer_titulo.SetLabel(f"Mastodon - {self.tree_nav.GetItemText(item)}")
                self._cargar_listado_toots()
                # Local y Federada no se streamean ni se sondean en cada tick: al entrar, traer YA
                # una página (en segundo plano, silencioso y acotado) para que no haya que esperar a
                # que carguen ni se vean vacías. No bloquea: la lista se rellena sola al llegar.
                if self.active_timeline_type in ("local", "public"):
                    self._actualizar_mastodon_incremental()
                # Cualquier OTRO buffer que esté vacío al entrar (favoritos, marcadores, enviados,
                # cronologías de usuario, conversaciones, listas...): descargarlo SOLO, como hacen
                # Bluesky y X. Antes solo se autocargaban user:/thread: y por eso, por ejemplo,
                # Favoritos podía quedarse vacío hasta pulsar actualizar.
                elif not self.lista_items_db_ids:
                    self._on_actualizar_buffer_actual(None)
                else:
                    # Buffer CON contenido: además de pintar la caché, pedir lo nuevo AL ENTRAR
                    # (silencioso y sin mover el foco), como hace TWBlue.
                    #
                    # Sin esto, al cambiar de cuenta o de buffer se veía lo cacheado y no se pedía
                    # nada al servidor: en tiempo real el temporizador no sondea el buffer activo
                    # (confía en el streaming), así que una publicación que el streaming no hubiera
                    # entregado todavía —por federación lenta, un evento perdido o una reconexión—
                    # no aparecía hasta 30 segundos después. Ese era el "publico en una cuenta, me
                    # paso a la otra y no está" que sí mostraba TWBlue.
                    self._sincronizar_al_entrar_mastodon()
                # El panel de detalles (si está visible) debe reflejar lo enfocado; si el buffer
                # quedó vacío, se vacía para que no muestre lo del buffer anterior.
                if not self.lista_items_db_ids:
                    self.txt_detalles.Clear()
            else:
                self.lista_items.DeleteAllItems()
                self.lista_items.limpiar_lecturas()
                self.txt_detalles.Clear()
                
        elif data.get("service") == "rss":
            self.active_rss_account_id = data.get("account_id")
            app_context.active_rss_account_id = self.active_rss_account_id
            
            # Vista previa de RSS: opcional (Preferencias -> RSS). Visibilidad centralizada.
            self._aplicar_visibilidad_detalles()
                
            if t == "rss_timeline":
                self.active_timeline_type = data.get("rss_type")
                self.lbl_buffer_titulo.SetLabel(f"RSS - {self.tree_nav.GetItemText(item)}")
                self._cargar_listado_artículos()
            elif t == "rss_category":
                self.active_timeline_type = "category"
                self.active_rss_category_id = data.get("category_id")
                self.lbl_buffer_titulo.SetLabel(f"RSS Categoría - {self.tree_nav.GetItemText(item)}")
                self._cargar_listado_artículos()
            elif t == "rss_feed":
                self.active_timeline_type = "feed"
                self.active_rss_feed_id = data.get("feed_id")
                self.lbl_buffer_titulo.SetLabel(f"RSS Feed - {self.tree_nav.GetItemText(item)}")
                self._cargar_listado_artículos()
            else:
                self.lista_items.DeleteAllItems()
                self.lista_items.limpiar_lecturas()
                self.txt_detalles.Clear()

        elif data.get("service") == "telegram":
            self.active_telegram_account_id = data.get("account_id")

            # Telegram no tiene panel de detalles (los chats se abren en su ventana de
            # conversación): se oculta de forma centralizada.
            self._aplicar_visibilidad_detalles()

            if t == "telegram_timeline":
                self.active_timeline_type = data.get("tg_type")
                self.lbl_buffer_titulo.SetLabel(f"Telegram - {self.tree_nav.GetItemText(item)}")
                self._cargar_listado_chats_telegram()
            else:
                self.lista_items.DeleteAllItems()
                self.lista_items.limpiar_lecturas()

        elif data.get("service") == "bluesky":
            # Toda la lógica de Bluesky vive en el controlador (bluesky_views.py)
            self.bluesky_ctrl.on_select(item, data, t)

        elif data.get("service") == "x" and self.x_ctrl is not None:
            # Toda la lógica de X vive en el controlador (x_views.py)
            self.x_ctrl.on_select(item, data, t)

        elif data.get("service") == "irc" and getattr(self, "irc_ctrl", None) is not None:
            # Toda la lógica de IRC vive en el controlador (irc_views.py)
            self.irc_ctrl.on_select(item, data, t)

        elif data.get("service") == "matrix" and getattr(self, "matrix_ctrl", None) is not None:
            # Toda la lógica de Matrix vive en el controlador (matrix_views.py)
            self.matrix_ctrl.on_select(item, data, t)

        elif data.get("service") == "xmpp" and getattr(self, "xmpp_ctrl", None) is not None:
            # Toda la lógica de XMPP vive en el controlador (xmpp_views.py)
            self.xmpp_ctrl.on_select(item, data, t)

        elif data.get("service") == "reddit" and getattr(self, "reddit_ctrl", None) is not None:
            # Toda la lógica de Reddit vive en el controlador (reddit_views.py)
            self.reddit_ctrl.on_select(item, data, t)

        elif data.get("service") == "peertube" and getattr(self, "peertube_ctrl", None) is not None:
            # Toda la lógica de PeerTube vive en el controlador (peertube_views.py)
            self.peertube_ctrl.on_select(item, data, t)

        elif data.get("service") == "wsocial" and getattr(self, "wsocial_ctrl", None) is not None:
            # Toda la lógica de W Social vive en el controlador (wsocial_views.py)
            self.wsocial_ctrl.on_select(item, data, t)

        elif data.get("service") == "inbox":
            # BANDEJA DE ENTRADA unificada (multi-red): carga la lista mezclada de las fuentes
            # elegidas. No hay "red activa" única; el menú contextual de cada fila se resuelve por
            # su origen (ver _menu_contextual_bandeja).
            self.active_timeline_type = "inbox"
            self.lbl_buffer_titulo.SetLabel(_("Bandeja de entrada"))
            try:
                self.txt_detalles.Clear()
            except Exception:
                pass
            self._cargar_listado_bandeja()

        # Cada servicio tiene su propio intervalo: reajustar el temporizador y el streaming al
        # servicio que acaba de quedar activo.
        self._aplicar_servicio_activo()
        # Refrescar la botonera de acciones para el nuevo buffer (si el buffer trae elementos, el
        # foco en el primero ya la habrá actualizado; esto cubre además los buffers vacíos).
        self._actualizar_botonera()

    def _on_tree_key_down(self, event):
        key = event.GetKeyCode()
        mod = event.GetModifiers()

        # Intro sobre un nodo de PeerTube que abre un diálogo (Listas, Mis canales): al navegar por el
        # árbol esas secciones NO abren el diálogo (rompía la navegación); solo avisan. Con Intro sí.
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not mod:
            item = self.tree_nav.GetSelection()
            if item.IsOk():
                data = self.tree_nav.GetItemData(item)
                if (data and data.get("service") == "peertube"
                        and getattr(self, "peertube_ctrl", None) is not None
                        and self.peertube_ctrl.abrir_con_intro(data)):
                    return
            event.Skip()
            return

        # Si es Ctrl+Up o Ctrl+Down
        if mod == wx.MOD_CONTROL and key in (wx.WXK_UP, wx.WXK_DOWN):
            item = self.tree_nav.GetSelection()
            if item.IsOk():
                data = self.tree_nav.GetItemData(item)

                # Reordenar servicios raíz (Mastodon, RSS, futuros) a gusto del usuario
                if data and data.get("type") == "category_root":
                    servicio = data.get("service")
                    orden = self._orden_servicios()
                    if servicio in orden:
                        idx = orden.index(servicio)
                        new_idx = idx - 1 if key == wx.WXK_UP else idx + 1
                        if 0 <= new_idx < len(orden):
                            orden[idx], orden[new_idx] = orden[new_idx], orden[idx]
                            config.set("general", "service_order", orden)  # set() ya guarda
                            self._cargar_navegacion()
                            self._re_seleccionar_servicio(servicio)
                            speak(_("Servicio movido."), interrupt=True)
                    return

                if data and data.get("type") == "timeline" and data.get("service") == "mastodon":
                    t_type = data.get("timeline_type") or ""

                    # Cronologías de usuarios y Conversaciones: se reordenan DENTRO de su
                    # propia rama (petición de usuarios; antes Ctrl+flechas no hacía nada).
                    if t_type.startswith("user:") or t_type.startswith("thread:"):
                        if t_type.startswith("user:"):
                            clave_cfg, campo = "user_timelines", "acct"
                            valor = data.get("user_acct") or t_type.split(":", 1)[1]
                        else:
                            clave_cfg, campo = "thread_buffers", "status_id"
                            valor = data.get("thread_status_id") or t_type.split(":", 1)[1]
                        acc = data.get("account_id")
                        todas = list(config.get("mastodon", clave_cfg, []))
                        # Posiciones de los elementos de ESTA cuenta dentro de la lista global
                        idxs = [i for i, u in enumerate(todas) if u.get("account_id") == acc]
                        pos = next((k for k, i in enumerate(idxs)
                                    if str(todas[i].get(campo) or "") == str(valor)), None)
                        if pos is not None:
                            nuevo = pos - 1 if key == wx.WXK_UP else pos + 1
                            if 0 <= nuevo < len(idxs):
                                i, j = idxs[pos], idxs[nuevo]
                                todas[i], todas[j] = todas[j], todas[i]
                                config.set("mastodon", clave_cfg, todas)
                                self._cargar_navegacion()
                                self._re_seleccionar_timeline(acc, t_type)
                                speak(_("Movido."), interrupt=True)
                        return

                    buffer_order = config.get("mastodon", "buffer_order", ["home", "notifications", "mentions", "bookmarks", "favourites", "sent", "direct", "local", "public"])
                    
                    if t_type in buffer_order:
                        idx = buffer_order.index(t_type)
                        new_idx = idx - 1 if key == wx.WXK_UP else idx + 1
                        
                        if 0 <= new_idx < len(buffer_order):
                            buffer_order[idx], buffer_order[new_idx] = buffer_order[new_idx], buffer_order[idx]
                            config.set("mastodon", "buffer_order", buffer_order)
                            
                            selected_acc_id = data.get("account_id")
                            
                            self._cargar_navegacion()
                            self._re_seleccionar_timeline(selected_acc_id, t_type)
                            speak(_("Movido"), interrupt=True)
                            return

                # Reordenar los buffers de Telegram
                if data and data.get("type") == "telegram_timeline":
                    b_type = data.get("tg_type")
                    buffer_order = config.get("telegram", "buffer_order", ["all", "unread", "private", "bot", "group", "supergroup", "channel", "contacts"])
                    if b_type in buffer_order:
                        idx = buffer_order.index(b_type)
                        new_idx = idx - 1 if key == wx.WXK_UP else idx + 1
                        if 0 <= new_idx < len(buffer_order):
                            buffer_order[idx], buffer_order[new_idx] = buffer_order[new_idx], buffer_order[idx]
                            config.set("telegram", "buffer_order", buffer_order)  # set() ya guarda
                            acc = data.get("account_id")
                            self._cargar_navegacion()
                            self._re_seleccionar_telegram_timeline(acc, b_type)
                            speak(_("Movido."), interrupt=True)
                    return

                # Reordenar los buffers de Bluesky
                if data and data.get("type") == "bluesky_timeline":
                    if self.bluesky_ctrl.reordenar_buffer(data, subir=(key == wx.WXK_UP)):
                        acc = data.get("account_id")
                        b_type = data.get("bsky_type")
                        self._cargar_navegacion()
                        self._re_seleccionar_bluesky_timeline(acc, b_type)
                        speak(_("Movido."), interrupt=True)
                    return

                # Reordenar los buffers de X (Twitter)
                if data and data.get("type") == "x_timeline" and self.x_ctrl is not None:
                    if self.x_ctrl.reordenar_buffer(data, subir=(key == wx.WXK_UP)):
                        self._cargar_navegacion()
                        speak(_("Movido."), interrupt=True)
                    return

                # Reordenar los buffers de Reddit
                if data and data.get("type") == "reddit_timeline" and getattr(self, "reddit_ctrl", None) is not None:
                    if self.reddit_ctrl.reordenar_buffer(data, subir=(key == wx.WXK_UP)):
                        self._cargar_navegacion()
                        speak(_("Movido."), interrupt=True)
                    return

                # Reordenar los buffers estándar de RSS (Todos, No leídos, Destacados, Leer más tarde, Hoy)
                if data and data.get("type") == "rss_timeline" and data.get("rss_type") in ("all", "favorites", "unread", "read_later", "today"):
                    b_type = data.get("rss_type")
                    buffer_order = config.get("rss", "buffer_order", ["all", "unread", "favorites", "read_later", "today"])
                    if b_type in buffer_order:
                        idx = buffer_order.index(b_type)
                        new_idx = idx - 1 if key == wx.WXK_UP else idx + 1
                        if 0 <= new_idx < len(buffer_order):
                            buffer_order[idx], buffer_order[new_idx] = buffer_order[new_idx], buffer_order[idx]
                            config.set("rss", "buffer_order", buffer_order)  # set() ya guarda
                            acc = data.get("account_id")
                            self._cargar_navegacion()
                            self._re_seleccionar_rss_timeline(acc, b_type)
                            speak(_("Movido."), interrupt=True)
                    return

                # Reordenar cuentas (RSS/Telegram/Mastodon), feeds y categorías
                if data and data.get("type") in ("account", "rss_feed", "rss_category"):
                    if self._mover_en_arbol(data, subir=(key == wx.WXK_UP)):
                        self._cargar_navegacion()
                        self._re_seleccionar_nodo(data)
                        speak(_("Movido."), interrupt=True)
                    return

        event.Skip()

    def _mover_en_arbol(self, data, subir: bool) -> bool:
        """Intercambia la posición de un nodo (cuenta/feed/categoría) con su vecino."""
        t = data.get("type")
        if t == "account":
            service = data.get("service")
            filas = db.query("SELECT id FROM accounts WHERE service_id = ? AND active = 1 ORDER BY position, id", (service,))
            actual, tabla = data.get("account_id"), "accounts"
        elif t == "rss_feed":
            acc = data.get("account_id")
            frow = db.query_one("SELECT category_id FROM rss_feeds WHERE id = ?", (data.get("feed_id"),))
            cat = frow[0] if frow else None
            if cat:
                filas = db.query("SELECT id FROM rss_feeds WHERE account_id = ? AND category_id = ? ORDER BY position, id", (acc, cat))
            else:
                filas = db.query("SELECT id FROM rss_feeds WHERE account_id = ? AND (category_id IS NULL OR category_id = '') ORDER BY position, id", (acc,))
            actual, tabla = data.get("feed_id"), "rss_feeds"
        elif t == "rss_category":
            acc = data.get("account_id")
            filas = db.query("""
                SELECT DISTINCT c.id FROM rss_categories c
                JOIN rss_feeds f ON f.category_id = c.id
                WHERE f.account_id = ? ORDER BY c.position, c.id
            """, (acc,))
            actual, tabla = data.get("category_id"), "rss_categories"
        else:
            return False

        ids = [r[0] for r in filas]
        if actual not in ids:
            return False
        i = ids.index(actual)
        j = i - 1 if subir else i + 1
        if j < 0 or j >= len(ids):
            return False

        pa = db.query_one(f"SELECT position FROM {tabla} WHERE id = ?", (ids[i],))[0] or i
        pb = db.query_one(f"SELECT position FROM {tabla} WHERE id = ?", (ids[j],))[0] or j
        if pa == pb:
            pa, pb = i, j
        db.execute(f"UPDATE {tabla} SET position = ? WHERE id = ?", (pb, ids[i]))
        db.execute(f"UPDATE {tabla} SET position = ? WHERE id = ?", (pa, ids[j]))
        return True

    def _re_seleccionar_telegram_timeline(self, account_id, tg_type):
        """Reselecciona un buffer de Telegram tras reconstruir el árbol."""
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return

        def buscar(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                d = self.tree_nav.GetItemData(child)
                if d and d.get("type") == "telegram_timeline" and d.get("account_id") == account_id and d.get("tg_type") == tg_type:
                    self.tree_nav.SelectItem(child)
                    self.tree_nav.SetFocus()
                    return True
                if self.tree_nav.ItemHasChildren(child) and buscar(child):
                    return True
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
            return False

        buscar(root)

    def _re_seleccionar_bluesky_timeline(self, account_id, bsky_type):
        """Reselecciona un buffer de Bluesky tras reconstruir el árbol."""
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return

        def buscar(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                d = self.tree_nav.GetItemData(child)
                if d and d.get("type") == "bluesky_timeline" and d.get("account_id") == account_id and d.get("bsky_type") == bsky_type:
                    self.tree_nav.SelectItem(child)
                    self.tree_nav.SetFocus()
                    return True
                if self.tree_nav.ItemHasChildren(child) and buscar(child):
                    return True
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
            return False

        buscar(root)

    def _re_seleccionar_x_timeline(self, account_id, x_type):
        """Reselecciona un buffer de X tras reconstruir el árbol."""
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return

        def buscar(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                d = self.tree_nav.GetItemData(child)
                if d and d.get("type") == "x_timeline" and d.get("account_id") == account_id and d.get("x_type") == x_type:
                    self.tree_nav.SelectItem(child)
                    self.tree_nav.SetFocus()
                    return True
                if self.tree_nav.ItemHasChildren(child) and buscar(child):
                    return True
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
            return False

        buscar(root)

    def _re_seleccionar_rss_timeline(self, account_id, rss_type):
        """Reselecciona un buffer RSS estándar tras reconstruir el árbol."""
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return

        def buscar(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                d = self.tree_nav.GetItemData(child)
                if d and d.get("type") == "rss_timeline" and d.get("account_id") == account_id and d.get("rss_type") == rss_type:
                    self.tree_nav.SelectItem(child)
                    self.tree_nav.SetFocus()
                    return True
                if self.tree_nav.ItemHasChildren(child) and buscar(child):
                    return True
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
            return False

        buscar(root)

    def _re_seleccionar_nodo(self, data):
        """Reselecciona un nodo (cuenta/feed/categoría) tras reconstruir el árbol."""
        t = data.get("type")
        clave = {"account": "account_id", "rss_feed": "feed_id", "rss_category": "category_id"}.get(t)
        if not clave:
            return
        valor = data.get(clave)
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return

        def buscar(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                d = self.tree_nav.GetItemData(child)
                if d and d.get("type") == t and d.get(clave) == valor and d.get("service") == data.get("service"):
                    self.tree_nav.SelectItem(child)
                    self.tree_nav.SetFocus()
                    return True
                if self.tree_nav.ItemHasChildren(child) and buscar(child):
                    return True
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
            return False

        buscar(root)

    # --- MENÚ CONTEXTUAL DEL ÁRBOL (RSS y otros) ---
    def _on_tree_context_menu(self, event):
        item = event.GetItem()
        if not item.IsOk():
            return
        self.tree_nav.SelectItem(item)
        data = self.tree_nav.GetItemData(item)
        if not data:
            return
        t = data.get("type")
        servicio = data.get("service")
        menu = wx.Menu()

        # BANDEJA DE ENTRADA: agregar o quitar buffers (fuentes), de uno en uno o varios a la vez.
        if servicio == "inbox":
            it_add = menu.Append(wx.ID_ANY, _("Agregar buffers a la bandeja..."))
            self.Bind(wx.EVT_MENU, lambda e: self._on_inbox_agregar_fuentes(), it_add)
            it_del = menu.Append(wx.ID_ANY, _("Quitar buffers de la bandeja..."))
            self.Bind(wx.EVT_MENU, lambda e: self._on_inbox_quitar_fuentes(), it_del)
            it_ref = menu.Append(wx.ID_ANY, _("Actualizar bandeja"))
            self.Bind(wx.EVT_MENU, lambda e: self._cargar_listado_bandeja(), it_ref)
            self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # Búsqueda guardada de Mastodon: permitir eliminarla
        if servicio == "mastodon" and t == "timeline" and data.get("search_term"):
            termino = data.get("search_term")
            it = menu.Append(wx.ID_ANY, self._lbl_atajo("actualizar_buffer", _("Actualizar resultados")))
            self.Bind(wx.EVT_MENU, lambda e: self._on_actualizar_buffer_actual(None), it)
            it = menu.Append(wx.ID_ANY, _("Eliminar búsqueda guardada"))
            self.Bind(wx.EVT_MENU, lambda e, term=termino, nodo=item: self._on_eliminar_busqueda(term, nodo), it)
            self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # Cronología de usuario de Mastodon: actualizar o borrar ese buffer.
        if servicio == "mastodon" and t == "timeline" and data.get("user_acct"):
            acct = data.get("user_acct")
            it = menu.Append(wx.ID_ANY, self._lbl_atajo("actualizar_buffer", _("Actualizar publicaciones")))
            self.Bind(wx.EVT_MENU, lambda e: self._on_actualizar_buffer_actual(None), it)
            it = menu.Append(wx.ID_ANY, _("Borrar esta cronología de usuario"))
            self.Bind(wx.EVT_MENU, lambda e, a=acct, nodo=item: self._borrar_cronologia_usuario(a, nodo), it)
            self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # Conversación (hilo) abierta como buffer: actualizar o borrar ese buffer.
        if servicio == "mastodon" and t == "timeline" and data.get("thread_status_id"):
            sid = data.get("thread_status_id")
            it = menu.Append(wx.ID_ANY, self._lbl_atajo("actualizar_buffer", _("Actualizar mensajes")))
            self.Bind(wx.EVT_MENU, lambda e: self._on_actualizar_buffer_actual(None), it)
            it = menu.Append(wx.ID_ANY, _("Borrar esta conversación"))
            self.Bind(wx.EVT_MENU, lambda e, s=sid, nodo=item: self._borrar_buffer_conversacion(s, nodo), it)
            self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # X (Twitter): nodos de búsqueda guardada o cronología de usuario (actualizar/borrar).
        if servicio == "x" and t == "x_timeline" and self.x_ctrl is not None:
            if self.x_ctrl.menu_contextual_arbol(menu, data, item):
                self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # Bluesky: nodos de búsqueda guardada o cronología de cuenta (actualizar/eliminar).
        if servicio == "bluesky" and t == "bluesky_timeline" and getattr(self, "bluesky_ctrl", None) is not None:
            if self.bluesky_ctrl.menu_contextual_arbol(menu, data, item):
                self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # PeerTube: nodo de una búsqueda guardada (actualizar resultados / eliminarla).
        if servicio == "peertube" and t == "peertube_section" and getattr(self, "peertube_ctrl", None) is not None:
            if self.peertube_ctrl.menu_contextual_arbol(menu, data, item):
                self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # IRC: nodo de una red (conectar/desconectar/editar).
        if servicio == "irc" and getattr(self, "irc_ctrl", None) is not None:
            if self.irc_ctrl.menu_contextual_arbol(menu, data, item):
                self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # Matrix: nodo de una cuenta (conectar/desconectar).
        if servicio == "matrix" and getattr(self, "matrix_ctrl", None) is not None:
            if self.matrix_ctrl.menu_contextual_arbol(menu, data, item):
                self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        # XMPP: nodo de una cuenta (conectar/desconectar).
        if servicio == "xmpp" and getattr(self, "xmpp_ctrl", None) is not None:
            if self.xmpp_ctrl.menu_contextual_arbol(menu, data, item):
                self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        if servicio == "rss" and str(data.get("rss_type", "")).startswith("search:"):
            if self._menu_nodo_busqueda_rss(menu, data, item):
                self.tree_nav.PopupMenu(menu)
            menu.Destroy()
            return

        if servicio == "rss" and t == "rss_feed":
            feed_id = data.get("feed_id")
            it = menu.Append(wx.ID_ANY, _("Actualizar este feed"))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_actualizar_feed(f), it)
            it = menu.Append(wx.ID_ANY, _("Marcar todo el feed como leído"))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._marcar_leido_scope(feed_id=f), it)
            # Estado actual de fijado/pausado para etiquetas dinámicas
            fila_estado = db.query_one("SELECT COALESCE(pinned,0), COALESCE(paused,0) FROM rss_feeds WHERE id = ?", (feed_id,))
            esta_fijado = bool(fila_estado[0]) if fila_estado else False
            esta_pausado = bool(fila_estado[1]) if fila_estado else False
            it = menu.Append(wx.ID_ANY, _("Desfijar feed") if esta_fijado else _("Fijar feed arriba"))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id, v=not esta_fijado: self._on_fijar_feed(f, v), it)
            it = menu.Append(wx.ID_ANY, _("Reanudar feed (actualizar)") if esta_pausado else _("Pausar feed (no actualizar)"))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id, v=not esta_pausado: self._on_pausar_feed(f, v), it)
            it = menu.Append(wx.ID_ANY, _("Renombrar feed..."))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_renombrar_feed(f), it)
            it = menu.Append(wx.ID_ANY, _("Mover a categoría..."))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_mover_feed_categoria(f), it)
            it = menu.Append(wx.ID_ANY, _("Etiquetas del feed..."))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_etiquetas_feed(f), it)
            it = menu.Append(wx.ID_ANY, _("Editar feed (título, dirección, ajustes)..."))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_ajustes_feed(f), it)
            it = menu.Append(wx.ID_ANY, _("Recuperar episodios antiguos (archivo de Internet)..."))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_recuperar_archivados(f), it)
            it = menu.Append(wx.ID_ANY, _("Abrir feed en el navegador"))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_abrir_feed_navegador(f), it)
            it = menu.Append(wx.ID_ANY, _("Eliminar feed"))
            self.Bind(wx.EVT_MENU, lambda e, f=feed_id: self._on_eliminar_feed(f), it)

        elif servicio == "rss" and t == "rss_category":
            cat_id = data.get("category_id")
            it = menu.Append(wx.ID_ANY, _("Actualizar categoría"))
            self.Bind(wx.EVT_MENU, lambda e, c=cat_id, a=data.get("account_id"): self._on_actualizar_categoria(a, c), it)
            it = menu.Append(wx.ID_ANY, _("Marcar toda la categoría como leída"))
            self.Bind(wx.EVT_MENU, lambda e, c=cat_id: self._marcar_leido_scope(category_id=c), it)
            it = menu.Append(wx.ID_ANY, _("Ver estadísticas de la categoría"))
            self.Bind(wx.EVT_MENU, lambda e, c=cat_id: self._on_estadisticas_categoria(c), it)
            it = menu.Append(wx.ID_ANY, _("Crear subcarpeta aquí..."))
            self.Bind(wx.EVT_MENU, lambda e, c=cat_id: self._on_crear_subcategoria(c), it)
            it = menu.Append(wx.ID_ANY, _("Mover esta carpeta a..."))
            self.Bind(wx.EVT_MENU, lambda e, c=cat_id: self._on_mover_categoria(c), it)

        elif servicio == "rss" and t == "account":
            acc_id = data.get("account_id")
            it = menu.Append(wx.ID_ANY, _("Actualizar todos los feeds"))
            self.Bind(wx.EVT_MENU, lambda e, a=acc_id: self._on_actualizar_cuenta_rss(a), it)
            it = menu.Append(wx.ID_ANY, _("Marcar todo como leído"))
            self.Bind(wx.EVT_MENU, lambda e, a=acc_id: self._marcar_leido_scope(account_id=a), it)
            it = menu.Append(wx.ID_ANY, _("Añadir feed a esta cuenta..."))
            self.Bind(wx.EVT_MENU, lambda e, a=acc_id: self._on_add_feed_a_cuenta(a), it)

        elif servicio == "rss" and t == "rss_timeline" and data.get("rss_type") in ("all", "unread"):
            acc_id = data.get("account_id")
            it = menu.Append(wx.ID_ANY, _("Marcar todo como leído"))
            self.Bind(wx.EVT_MENU, lambda e, a=acc_id: self._marcar_leido_scope(account_id=a), it)
            it = menu.Append(wx.ID_ANY, _("Actualizar"))
            self.Bind(wx.EVT_MENU, lambda e, a=acc_id: self._on_actualizar_cuenta_rss(a), it)

        elif servicio == "telegram" and t in ("account", "telegram_timeline"):
            acc_id = data.get("account_id")
            it = menu.Append(wx.ID_ANY, _("Marcar todos los chats como leídos"))
            self.Bind(wx.EVT_MENU, lambda e, a=acc_id: self._on_tg_marcar_todos_leidos(a), it)
            it = menu.Append(wx.ID_ANY, _("Actualizar chats"))
            self.Bind(wx.EVT_MENU, lambda e, a=acc_id: self._actualizar_telegram_incremental(), it)
            it = menu.Append(wx.ID_ANY, _("Mi perfil..."))
            self.Bind(wx.EVT_MENU, self._on_tg_perfil, it)

        if menu.GetMenuItemCount() == 0:
            menu.Destroy()
            return
        self.tree_nav.PopupMenu(menu)
        menu.Destroy()

    def _marcar_leido_scope(self, feed_id=None, category_id=None, account_id=None):
        """Marca como leídos los artículos de un feed, categoría o cuenta completos."""
        if feed_id is not None:
            db.execute("UPDATE rss_articles SET read = 1 WHERE feed_id = ?", (feed_id,))
        elif category_id is not None:
            db.execute("UPDATE rss_articles SET read = 1 WHERE feed_id IN (SELECT id FROM rss_feeds WHERE category_id = ?)", (category_id,))
        elif account_id is not None:
            db.execute("UPDATE rss_articles SET read = 1 WHERE feed_id IN (SELECT id FROM rss_feeds WHERE account_id = ?)", (account_id,))
        self._actualizar_contadores_rss()
        if self.active_rss_account_id:
            self._cargar_listado_artículos()
        speak(_("Marcado como leído."), interrupt=True)

    def _on_actualizar_feed(self, feed_id):
        speak(_("Actualizando feed..."), interrupt=True)
        def _finish(res, err):
            if err:
                notificar(_("Error al actualizar el feed: {0}").format(err))
            else:
                self._actualizar_contadores_rss()
                if self.active_rss_account_id:
                    self._cargar_listado_artículos()
                notificar(_("Feed actualizado."))
        rss_service.actualizar_feed_async(feed_id, callback=_finish)

    def _on_actualizar_categoria(self, account_id, category_id):
        feeds = db.query("SELECT id FROM rss_feeds WHERE account_id = ? AND category_id = ?", (account_id, category_id))
        speak(_("Actualizando categoría..."), interrupt=True)
        for (fid,) in feeds:
            rss_service.actualizar_feed_async(fid)

    def _on_actualizar_cuenta_rss(self, account_id):
        speak(_("Actualizando feeds..."), interrupt=True)
        def _finish(res, err):
            if not err:
                self._actualizar_contadores_rss()
                if self.active_rss_account_id:
                    self._cargar_listado_artículos()
                notificar(_("Feeds actualizados."))
        rss_service.actualizar_todos_async(account_id, callback=_finish)

    def _on_renombrar_feed(self, feed_id):
        from .accessible_controls import CustomTextEntryDialog
        row = db.query_one("SELECT title FROM rss_feeds WHERE id = ?", (feed_id,))
        actual = row[0] if row else ""
        dlg = CustomTextEntryDialog(self, _("Nuevo nombre del feed:"), _("Renombrar feed"), value=actual)
        if dlg.ShowModal() == wx.ID_OK:
            nuevo = dlg.GetValue().strip()
            if nuevo:
                db.execute("UPDATE rss_feeds SET title = ? WHERE id = ?", (nuevo, feed_id))
                rss_service.sus_renombrar_feed(feed_id)
                self._cargar_navegacion()
                notificar(_("Feed renombrado."))
        dlg.Destroy()

    def _on_ajustes_feed(self, feed_id):
        """"Editar feed...": título, DIRECCIÓN, carpeta, intervalo, identificación, imágenes,
        texto completo y pausa en un solo diálogo, con comprobación de la dirección
        (rss_editar_feed.py). Si cambia la dirección, el feed se actualiza al momento."""
        from .rss_editar_feed import EditarFeedDialog
        try:
            dlg = EditarFeedDialog(self, feed_id)
        except ValueError as e:
            notificar(str(e))
            return
        guardado = dlg.ShowModal() == wx.ID_OK
        cambio_url = bool(getattr(dlg, "cambio_url", False))
        dlg.Destroy()
        if not guardado:
            return
        self._cargar_navegacion()
        notificar(_("Feed guardado."))
        if cambio_url:
            self._on_actualizar_feed(feed_id)

    def _on_fijar_feed(self, feed_id, fijar):
        """Fija o desfija un feed (los fijados aparecen arriba en el árbol)."""
        rss_service.set_feed_pinned(feed_id, fijar)
        self._cargar_navegacion()
        notificar(_("Feed fijado.") if fijar else _("Feed desfijado."))

    def _on_pausar_feed(self, feed_id, pausar):
        """Pausa o reactiva un feed (los pausados no se actualizan con 'Actualizar todos')."""
        rss_service.set_feed_paused(feed_id, pausar)
        self._cargar_navegacion()
        speak(_("Feed en pausa.") if pausar else _("Feed reactivado."), interrupt=True)

    def _on_mover_feed_categoria(self, feed_id):
        """Mueve un feed a la categoría que elija el usuario (o sin categoría), estilo SimpleRSS.
        Solo se ofrecen carpetas de LA CUENTA del feed (auditoría RSS-01)."""
        fr = db.query_one("SELECT account_id FROM rss_feeds WHERE id = ?", (feed_id,))
        acc = fr[0] if fr else None
        cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? "
                        "ORDER BY position, name", (acc,))
        opciones = [_("Sin categoría")] + [c[1] for c in cats]
        dlg = wx.SingleChoiceDialog(self, _("Mover el feed a la categoría:"), _("Mover a categoría"), opciones)
        if dlg.ShowModal() == wx.ID_OK:
            sel = dlg.GetSelection()
            if sel == 0:
                nueva_cat = None
            else:
                nueva_cat = cats[sel - 1][0]
            carpeta_antes = rss_service._datos_feed(feed_id)[3]
            db.execute("UPDATE rss_feeds SET category_id = ? WHERE id = ?", (nueva_cat, feed_id))
            rss_service.sus_mover_feed(feed_id, carpeta_antes)
            self._cargar_navegacion()
            speak(_("Feed movido de categoría."), interrupt=True)
        dlg.Destroy()

    def _on_abrir_feed_navegador(self, feed_id):
        row = db.query_one("SELECT url FROM rss_feeds WHERE id = ?", (feed_id,))
        if row and row[0]:
            abrir_url(row[0])
            speak(_("Abriendo el feed en el navegador."), interrupt=True)

    def _on_eliminar_feed(self, feed_id):
        row = db.query_one("SELECT title FROM rss_feeds WHERE id = ?", (feed_id,))
        nombre = row[0] if row else ""
        dlg = wx.MessageDialog(self, _("¿Eliminar el feed '{0}' y todos sus artículos?").format(nombre),
                               _("Confirmar eliminación"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp == wx.ID_YES:
            rss_service.sus_baja_feed(feed_id)     # antes de borrar: necesita su dirección
            db.execute("DELETE FROM rss_articles WHERE feed_id = ?", (feed_id,))
            db.execute("DELETE FROM rss_feeds WHERE id = ?", (feed_id,))
            self._cargar_navegacion()
            notificar(_("Feed eliminado."))

    def _on_add_feed_a_cuenta(self, account_id):
        self.active_rss_account_id = account_id
        from .rss_views import AddFeedDialog
        dlg = AddFeedDialog(self, account_id)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_navegacion()
        dlg.Destroy()

    def _re_seleccionar_servicio(self, service_id):
        """Vuelve a seleccionar el nodo raíz de un servicio tras reconstruir el árbol."""
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return
        child, cookie = self.tree_nav.GetFirstChild(root)
        while child.IsOk():
            data = self.tree_nav.GetItemData(child)
            if data and data.get("type") == "category_root" and data.get("service") == service_id:
                self.tree_nav.SelectItem(child)
                self.tree_nav.SetFocus()
                return
            child, cookie = self.tree_nav.GetNextChild(root, cookie)

    def _re_seleccionar_timeline(self, account_id, timeline_type):
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return
            
        def search_child(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                data = self.tree_nav.GetItemData(child)
                if data:
                    if data.get("type") == "timeline" and data.get("account_id") == account_id and data.get("timeline_type") == timeline_type:
                        self.tree_nav.SelectItem(child)
                        self.tree_nav.SetFocus()
                        return True
                if self.tree_nav.ItemHasChildren(child):
                    if search_child(child):
                        return True
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
            return False
            
            # Si no, buscar de forma recursiva desde root
        search_child(root)

    # --- MEMORIA DE FOCO POR BUFFER ---
    def _buffer_key(self) -> str:
        """Clave única del buffer activo para recordar la posición del usuario."""
        # Bandeja de entrada: clave propia (no tiene cuenta activa) para recordar dónde te quedaste.
        if getattr(self, "_modo_lista", "") == "bandeja":
            return "inbox"
        if self.active_mastodon_account_id:
            return f"m:{self.active_mastodon_account_id}:{self.active_timeline_type}"
        if self.active_bluesky_account_id:
            return f"b:{self.active_bluesky_account_id}:{self.active_timeline_type}"
        if self.active_x_account_id:
            return f"x:{self.active_x_account_id}:{self.active_timeline_type}"
        if self.active_telegram_account_id:
            return f"t:{self.active_telegram_account_id}:{self.active_timeline_type}"
        if self.active_rss_account_id:
            return f"r:{self.active_rss_account_id}:{self.active_timeline_type}:{self.active_rss_category_id}:{self.active_rss_feed_id}"
        return ""
