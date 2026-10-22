# -*- coding: utf-8 -*-
"""MainFrame (mixin): telegram_handlers.
Metodos: _on_context_menu, _tg_cuenta_activa, _on_tg_perfil, _on_tg_sesiones, _on_tg_buscar_global, _on_tg_guardados, _on_tg_chat_carpeta, _on_tg_privacidad ...
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

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service

from app.services.telegram.telegram_service import telegram_service


log = logging.getLogger(__name__)


class _Mf_telegram_handlers:
    def _on_context_menu(self, event, _popup_target=None, _idx_forzado=None):
        # _popup_target: ventana sobre la que mostrar el menú (por defecto la propia lista). El
        # diálogo "Ver publicación" lo usa para reaprovechar EXACTAMENTE este menú de acciones; los
        # eventos de menú siguen ligados al frame y se propagan hacia él, así que funcionan igual.
        # _idx_forzado: índice del elemento sobre el que actuar (lo pasa el diálogo Ver publicación;
        # con el modal abierto, GetFocusedItem de la lista puede devolver -1 y el menú no salía).
        idx = _idx_forzado if _idx_forzado is not None else self.lista_items.GetFocusedItem()
        if idx < 0:
            return

        # BANDEJA DE ENTRADA: el menú contextual es el ESPECÍFICO de la red de origen de la fila.
        # _menu_contextual_bandeja fija temporalmente el contexto de esa red y vuelve a llamar aquí
        # (ya con _modo_lista de esa red, no "bandeja"), así que no hay recursión.
        if getattr(self, "_modo_lista", "") == "bandeja":
            self._menu_contextual_bandeja(event, idx)
            return

        menu = wx.Menu()

        # Menú de Bluesky (delegado al controlador)
        if self.active_bluesky_account_id:
            # Protegido: si la construcción del menú falla a medias (p. ej. sesión
            # de Bluesky caducada al resolver datos), se muestra lo que se haya
            # podido añadir en lugar de no mostrar nada.
            try:
                self.bluesky_ctrl.on_context_menu(menu, idx)
            except Exception as e:
                log.error(f"Error al construir el menú contextual de Bluesky: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú de Reddit (delegado al controlador). Solo si la lista mostrada es de Reddit.
        if (getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("reddit_")):
            try:
                self.reddit_ctrl.on_context_menu(menu, idx)
            except Exception as e:
                log.error(f"Error al construir el menú contextual de Reddit: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú de PeerTube (delegado al controlador). Solo si la lista mostrada es de PeerTube.
        if (getattr(self, "active_peertube_account_id", None) and getattr(self, "peertube_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("peertube_")):
            try:
                self.peertube_ctrl.on_context_menu(menu, idx)
            except Exception as e:
                log.error(f"Error al construir el menú contextual de PeerTube: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú de IRC (chats), delegado al controlador. Solo si la lista mostrada es de IRC
        # (cinturón frente a estados residuales de "cuenta activa").
        if (self.active_irc_account_id and getattr(self, "irc_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") in ("irc_chats", "irc_avisos")):
            try:
                self.irc_ctrl.on_context_menu(menu, idx)
            except Exception as e:
                log.error(f"Error al construir el menú contextual de IRC: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú de Matrix (salas), delegado al controlador (mismo cinturón por modo de lista).
        if (self.active_matrix_account_id and getattr(self, "matrix_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") == "matrix_salas"):
            try:
                self.matrix_ctrl.on_context_menu(menu, idx)
            except Exception as e:
                log.error(f"Error al construir el menú contextual de Matrix: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú de XMPP (contactos/solicitudes), delegado al controlador.
        if (self.active_xmpp_account_id and getattr(self, "xmpp_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") == "xmpp_chats"):
            try:
                self.xmpp_ctrl.on_context_menu(menu, idx)
            except Exception as e:
                log.error(f"Error al construir el menú contextual de XMPP: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú de W Social, delegado al controlador (publicaciones, cuentas, conversaciones y
        # notificaciones). Mismo cinturón por modo de lista que el resto de redes.
        if (getattr(self, "active_wsocial_account_id", None)
                and getattr(self, "wsocial_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("wsocial_")):
            if 0 <= idx < len(self.lista_items_db_ids):
                try:
                    self.wsocial_ctrl.construir_menu_contextual(menu, self.lista_items_db_ids[idx])
                except Exception as e:
                    log.error(f"Error al construir el menú contextual de W Social: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú de X (Twitter), delegado al controlador (publicaciones, cuentas o programados).
        if self.active_x_account_id and self.x_ctrl is not None and getattr(self, "_modo_lista", "") in ("x_posts", "x_actores", "x_scheduled"):
            if 0 <= idx < len(self.lista_items_db_ids):
                try:
                    if self._modo_lista == "x_actores":
                        self.x_ctrl.construir_menu_actores(menu, self.lista_items_db_ids[idx])
                    elif self._modo_lista == "x_scheduled":
                        self.x_ctrl.construir_menu_programados(menu, self.lista_items_db_ids[idx])
                    else:
                        self.x_ctrl.construir_menu_contextual(menu, self.lista_items_db_ids[idx])
                except Exception as e:
                    log.error(f"Error al construir el menú contextual de X: {e}")
            if menu.GetMenuItemCount() > 0:
                self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú del buffer de CONTACTOS de Telegram
        if self.active_telegram_account_id and self.active_timeline_type == "contacts":
            uid = None
            nombre = ""
            if idx < len(self.lista_items_db_ids):
                partes = (self.lista_items_db_ids[idx] or "").split("|")
                if len(partes) >= 3 and partes[0] == "contacto":
                    try:
                        uid = int(partes[1])
                    except (ValueError, TypeError):
                        uid = None
                    nombre = partes[2]
            item_add = menu.Append(wx.ID_ANY, _("Añadir contacto..."))
            self.Bind(wx.EVT_MENU, lambda e: self._on_tg_anadir_contacto(), item_add)
            item_imp = menu.Append(wx.ID_ANY, _("Importar contactos desde archivo..."))
            self.Bind(wx.EVT_MENU, lambda e: self._on_tg_importar_contactos(), item_imp)
            if uid:
                item_del = menu.Append(wx.ID_ANY, _("Eliminar contacto"))
                self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._on_tg_borrar_contacto(u, n), item_del)
                item_nota = menu.Append(wx.ID_ANY, _("Nota del contacto..."))
                self.Bind(wx.EVT_MENU, lambda e, u=uid, n=nombre: self._on_tg_nota_contacto(u, n), item_nota)
            self.PopupMenu(menu)
            menu.Destroy()
            return

        # Menú especial para buffers de CUENTAS (seguidores/siguiendo/bloqueados/silenciados)
        if self.active_mastodon_account_id and getattr(self, "_modo_lista", "toots") == "cuentas":
            if idx >= len(self.lista_items_db_ids):
                return
            try:
                _p, uid, uacct = self.lista_items_db_ids[idx].split(":", 2)
            except (ValueError, AttributeError):
                return
            item_seg = menu.Append(wx.ID_ANY, _("Seguir / dejar de seguir"))
            self.Bind(wx.EVT_MENU, lambda e, u=uid, a=uacct: self._on_accion_cuenta(u, a, "follow_toggle"), item_seg)
            if self.active_timeline_type == "muted":
                item_m = menu.Append(wx.ID_ANY, _("Quitar silencio"))
                self.Bind(wx.EVT_MENU, lambda e, u=uid, a=uacct: self._on_accion_cuenta(u, a, "unmute"), item_m)
            else:
                item_m = menu.Append(wx.ID_ANY, _("Silenciar"))
                self.Bind(wx.EVT_MENU, lambda e, u=uid, a=uacct: self._on_accion_cuenta(u, a, "mute"), item_m)
            if self.active_timeline_type == "blocked":
                item_b = menu.Append(wx.ID_ANY, _("Desbloquear"))
                self.Bind(wx.EVT_MENU, lambda e, u=uid, a=uacct: self._on_accion_cuenta(u, a, "unblock"), item_b)
            else:
                item_b = menu.Append(wx.ID_ANY, _("Bloquear"))
                self.Bind(wx.EVT_MENU, lambda e, u=uid, a=uacct: self._on_accion_cuenta(u, a, "block"), item_b)

            # En el buffer de SEGUIDORES: quitar a esta cuenta de tus seguidores.
            if self.active_timeline_type == "followers":
                item_quitar = menu.Append(wx.ID_ANY, _("Quitar de mis seguidores"))
                self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._quitar_seguidor_acct(a), item_quitar)

            menu.AppendSeparator()
            # Acciones sobre la cuenta enfocada que valen para cualquiera de estos buffers.
            item_detalles_c = menu.Append(wx.ID_ANY, _("Ver detalles de la cuenta..."))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._mostrar_detalles_de_cuenta(a), item_detalles_c)
            item_ver = menu.Append(wx.ID_ANY, _("Ver sus publicaciones..."))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._abrir_timeline_acct(a), item_ver)
            item_cron = menu.Append(wx.ID_ANY, _("Crear buffer con su cronología"))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._anadir_cronologia_usuario(a), item_cron)
            item_dest = menu.Append(wx.ID_ANY, _("Destacar en mi perfil"))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._destacar_acct(a, True), item_dest)
            item_undest = menu.Append(wx.ID_ANY, _("Dejar de destacar"))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._destacar_acct(a, False), item_undest)
            item_conoc = menu.Append(wx.ID_ANY, _("Seguidores conocidos..."))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._mostrar_seguidores_conocidos(a), item_conoc)
            item_ht_otros = menu.Append(wx.ID_ANY, _("Hashtags destacados de esta cuenta..."))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._on_hashtags_destacados_de(a), item_ht_otros)
            item_listas = menu.Append(wx.ID_ANY, _("Listas en las que está..."))
            self.Bind(wx.EVT_MENU, lambda e, a=uacct: self._mostrar_listas_de_cuenta(a), item_listas)
            self.PopupMenu(menu)
            menu.Destroy()
            return

        if self.active_mastodon_account_id and getattr(self, "_modo_lista", "toots") == "anuncios":
            if idx >= len(self.lista_items_db_ids):
                return
            ann = self.lista_items_db_ids[idx]
            ann_id = ann.split(":", 1)[1] if ":" in ann else ann
            item_leido = menu.Append(wx.ID_ANY, _("Marcar este anuncio como leído"))
            self.Bind(wx.EVT_MENU, lambda e, a=ann_id: self._on_anuncio_leido(a), item_leido)
            item_reac = menu.Append(wx.ID_ANY, _("Reaccionar a este anuncio..."))
            self.Bind(wx.EVT_MENU, lambda e, a=ann_id: self._on_anuncio_reaccionar(a, True), item_reac)
            item_quitar_reac = menu.Append(wx.ID_ANY, _("Quitar mi reacción..."))
            self.Bind(wx.EVT_MENU, lambda e, a=ann_id: self._on_anuncio_reaccionar(a, False), item_quitar_reac)
            self.PopupMenu(menu)
            menu.Destroy()
            return

        # En los buffers de notificaciones y menciones, permitir descartar la notificación
        # enfocada (una mención también es una notificación en el servidor).
        if self.active_mastodon_account_id and self.active_timeline_type in ("notifications", "mentions"):
            notif_db_ids = getattr(self, "_notif_db_ids", [])
            if 0 <= idx < len(notif_db_ids):
                ndb = notif_db_ids[idx]
                # El ID EXTERNO de la notificación se consulta por la clave primaria: nunca
                # se deduce troceando la clave interna (es "cuenta_id", no "cuenta:id", y
                # el servidor recibía la clave entera, con lo que el descarte fallaba).
                fila = db.query_one(
                    "SELECT notification_id FROM mastodon_notifications WHERE id = ?", (ndb,))
                notif_id = fila[0] if fila else None
                if notif_id:
                    item_desc = menu.Append(wx.ID_ANY, _("Descartar esta notificación"))
                    self.Bind(wx.EVT_MENU, lambda e, n=notif_id: self._on_descartar_notificacion(n), item_desc)
            # Si la notificación NO lleva un toot (por ejemplo un nuevo seguidor), las acciones
            # de publicación no aplican: solo se ofrece descartarla. (Las menciones siempre
            # llevan toot, así que en Menciones se ofrece el menú completo.)
            if not db.query_one("SELECT 1 FROM mastodon_statuses WHERE id = ?", (self.lista_items_db_ids[idx],)):
                self.PopupMenu(menu)
                menu.Destroy()
                return

        if self.active_mastodon_account_id:
            # El buffer de MENSAJES DIRECTOS no admite citar ni impulsar (boost): se ocultan
            # esas opciones, que no tienen sentido en una conversación privada, y se deja el
            # resto (responder, favorito, ver conversación, borrar, acciones del autor...).
            es_dm = (self.active_timeline_type == "direct")

            # "Ver publicación" solo se ofrece desde la lista (no dentro del propio diálogo Ver
            # publicación, para no anidar el mismo diálogo).
            if _popup_target is None:
                item_ver_pub = menu.Append(wx.ID_ANY,
                                           self._lbl_atajo("pub_ver", _("Ver mensaje directo...")) if es_dm
                                           else self._lbl_atajo("pub_ver", _("Ver publicación...")))
                self.Bind(wx.EVT_MENU,
                          lambda evt, d=self.lista_items_db_ids[idx], i=idx: self._on_ver_publicacion(d, i),
                          item_ver_pub)

            # Filtro del servidor con 'avisar'/'difuminar': "Mostrar de todos modos" (mf_filtros_mastodon).
            self._items_menu_mostrar_filtrado(menu, self.lista_items_db_ids[idx])

            item_rep = menu.Append(wx.ID_ANY, self._lbl_atajo("pub_responder", _("Responder")))
            self.Bind(wx.EVT_MENU, self._on_responder_toot, item_rep)

            if not es_dm:
                item_citar = menu.Append(wx.ID_ANY, self._lbl_atajo("pub_citar", _("Citar toot")))
                self.Bind(wx.EVT_MENU, self._on_citar_toot, item_citar)

            # Favorito y marcador con etiqueta SEGÚN EL ESTADO actual de la publicación, así en
            # los buffers de Favoritos y Marcadores la opción dice directamente "Quitar de...".
            _estado = db.query_one(
                "SELECT COALESCE(favourited,0), COALESCE(bookmarked,0), COALESCE(reblogged,0), "
                "reblog_of_id, COALESCE(reblog_by_username,''), COALESCE(pinned,0) "
                "FROM mastodon_statuses WHERE id = ?",
                (self.lista_items_db_ids[idx],))
            es_fav = bool(_estado and _estado[0])
            es_marcado = bool(_estado and _estado[1])
            es_impulsado = bool(_estado and _estado[2])
            es_fila_impulso = bool(_estado and _estado[3])
            impulsor = (_estado[4] if _estado else "") or ""
            es_fijado = bool(_estado and _estado[5])

            item_boost = menu.Append(wx.ID_ANY,
                                     self._lbl_atajo("pub_boost_like", _("Quitar de favoritos")) if es_fav
                                     else self._lbl_atajo("pub_boost_like", _("Marcar como favorito")))
            self.Bind(wx.EVT_MENU, self._on_boost_fav_toot, item_boost)

            item_marcador = menu.Append(wx.ID_ANY,
                                        _("Quitar de marcadores") if es_marcado else _("Guardar en marcadores"))
            self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_marcador_toot(d), item_marcador)

            if not es_dm:
                # Dinámico según estado (auditoría §11/A13): si ya lo impulsaste, la opción
                # es DESHACER el impulso, no volver a preguntar impulsar/citar.
                if es_impulsado or (es_fila_impulso and self._usuario_es(impulsor)):
                    item_impulsar = menu.Append(wx.ID_ANY, _("Deshacer mi impulso"))
                    self.Bind(wx.EVT_MENU,
                              lambda evt, d=self.lista_items_db_ids[idx], i=idx: self._on_deshacer_impulso(d, i),
                              item_impulsar)
                else:
                    item_impulsar = menu.Append(wx.ID_ANY, _("Impulsar publicación (boost)"))
                    self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_impulsar_toot(d), item_impulsar)

            item_hilo = menu.Append(wx.ID_ANY,
                                    _("Ver conversación de mensajes directos") if es_dm else _("Ver conversación"))
            self.Bind(wx.EVT_MENU, lambda evt: self._on_ver_detalles_toot(self.lista_items_db_ids[idx]), item_hilo)

            item_hilo_buf = menu.Append(wx.ID_ANY,
                                        _("Abrir conversación de directos como buffer") if es_dm
                                        else _("Abrir conversación como buffer"))
            self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._anadir_buffer_conversacion(d), item_hilo_buf)

            # Citas: abrir la publicación citada (si la cita está aceptada) y ver quién cita una
            # publicación AJENA (las propias lo tienen más abajo, con la opción de retirarlas).
            if not es_dm:
                _fila_q = db.query_one("SELECT COALESCE(quote_json, '') FROM mastodon_statuses WHERE id = ?",
                                       (self.lista_items_db_ids[idx],))
                if self.id_cita_aceptada(_fila_q[0] if _fila_q else ""):
                    item_citada = menu.Append(wx.ID_ANY, _("Abrir la publicación citada"))
                    self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_abrir_cita_toot(d), item_citada)
                if not self._toot_es_mio(self.lista_items_db_ids[idx]):
                    item_citas_aj = menu.Append(wx.ID_ANY, _("Ver quién cita esta publicación..."))
                    self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_citas_toot(d), item_citas_aj)

            # En mensajes directos: marcar la conversación como leída.
            if es_dm:
                item_dm_leido = menu.Append(wx.ID_ANY, _("Marcar conversación como leída"))
                self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_marcar_dm_leido(d), item_dm_leido)

            # Editar, borrar y fijar: SOLO en tus propias publicaciones (fijar no aplica a DMs).
            if self._toot_es_mio(self.lista_items_db_ids[idx]):
                item_editar = menu.Append(wx.ID_ANY, _("Editar publicación..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_editar_toot(d), item_editar)
                if not es_dm:
                    # Fijar O Desfijar según el estado real, no ambas a la vez (auditoría §11).
                    if es_fijado:
                        item_fijar = menu.Append(wx.ID_ANY, _("Quitar publicación fijada de tu perfil"))
                        self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_fijar_toot(d, False), item_fijar)
                    else:
                        item_fijar = menu.Append(wx.ID_ANY, _("Fijar publicación en tu perfil"))
                        self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_fijar_toot(d, True), item_fijar)
                item_borrar = menu.Append(wx.ID_ANY, _("Borrar publicación..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx], i=idx: self._on_borrar_toot(d, i), item_borrar)
                item_redraft = menu.Append(wx.ID_ANY, _("Borrar y volver a redactar..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx], i=idx: self._on_borrar_y_redactar(d, i), item_redraft)
                if not es_dm:
                    item_citas = menu.Append(wx.ID_ANY, _("Ver quién cita esta publicación..."))
                    self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_citas_toot(d), item_citas)
                    item_pol_citas = menu.Append(wx.ID_ANY, _("Quién puede citar esta publicación..."))
                    self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_politica_citas_toot(d), item_pol_citas)

            menu.AppendSeparator()
            
            db_id_m = self.lista_items_db_ids[idx]
            row_m = db.query_one("SELECT media_json, uri FROM mastodon_statuses WHERE id = ?", (db_id_m,))

            item_tr = menu.Append(wx.ID_ANY, self._lbl_atajo("ia_traducir", _("Traducir toot")))
            self.Bind(wx.EVT_MENU, self._on_traducir_seleccionado, item_tr)

            # "Describir imagen" SOLO si el toot tiene alguna imagen adjunta (petición de los
            # usuarios: las acciones que no aplican al toot no deben salir en el menú).
            if self._media_tiene_imagen(row_m[0] if row_m else None):
                item_ia = menu.Append(wx.ID_ANY, self._lbl_atajo("ia_describir", _("Describir imagen")))
                self.Bind(wx.EVT_MENU, self._on_describir_imagen_ia, item_ia)

            item_tts_m = menu.Append(wx.ID_ANY, _("Leer en voz alta (TTS)"))
            self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_leer_tts_mastodon(d), item_tts_m)

            if not es_dm:
                item_col_autor = menu.Append(wx.ID_ANY, _("Colecciones de este autor..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_colecciones_de_autor(d), item_col_autor)
                if not self._toot_es_mio(self.lista_items_db_ids[idx]):
                    item_col_add = menu.Append(wx.ID_ANY, _("Añadir el autor a una de mis colecciones..."))
                    self.Bind(wx.EVT_MENU, lambda evt, d=self.lista_items_db_ids[idx]: self._on_anadir_autor_a_coleccion(d), item_col_add)

            item_alias = menu.Append(wx.ID_ANY, _("Alias local para este autor..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_alias_autor(d), item_alias)

            from app.services.media_player.player_service import player_service as _ps
            # Reproducir/Detener el audio o vídeo ADJUNTO del toot: si ese mismo adjunto ya está
            # sonando, el item pasa a "Detener audio o vídeo" (manteniendo la 'v' de acelerador). Si
            # suena otro (o nada), es "Reproducir", y al pulsarlo se detiene lo anterior y suena este.
            media_url = self._url_media_reproducible(row_m[0]) if (row_m and row_m[0]) else None
            if media_url:
                if _ps.esta_reproduciendo_enlace(media_url):
                    item_play_m = menu.Append(wx.ID_ANY, _("Detener audio o &vídeo"))
                    self.Bind(wx.EVT_MENU, lambda evt: self._on_detener_media_mastodon(), item_play_m)
                else:
                    item_play_m = menu.Append(wx.ID_ANY, _("Reproducir audio o &vídeo"))
                    self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_reproducir_media_mastodon(d), item_play_m)

            # Audio de un enlace de servicio (YouTube/SoundCloud...): SOLO si el toot contiene un
            # enlace reproducible (antes salía siempre, aunque no hubiera nada que reproducir). Si
            # ya está sonando ESE MISMO enlace, el item pasa a "Detener".
            enlace_srv = self._enlace_servicio_de_db(db_id_m)
            if enlace_srv:
                if _ps.esta_reproduciendo_enlace(enlace_srv):
                    item_audio_srv = menu.Append(wx.ID_ANY, _("Detene&r audio del enlace (YouTube, SoundCloud...)"))
                    self.Bind(wx.EVT_MENU, lambda evt: self._on_detener_audio_servicio(), item_audio_srv)
                else:
                    item_audio_srv = menu.Append(wx.ID_ANY, _("Reproducir audio del &enlace (YouTube, SoundCloud...)"))
                    self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_reproducir_audio_servicio_mastodon(d), item_audio_srv)

            menu.AppendSeparator()
            item_abrir_inst = menu.Append(wx.ID_ANY, _("Abrir publicación en el navegador"))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_abrir_toot_navegador(d), item_abrir_inst)

            item_copiar_enlace = menu.Append(wx.ID_ANY, _("Copiar enlace de la publicación"))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_copiar_enlace_toot(d), item_copiar_enlace)

            # Las acciones de RELACIÓN con el autor no tienen sentido sobre tus PROPIOS toots
            # (seguirte, silenciarte, bloquearte a ti mismo...): se omiten del menú.
            _es_mio_autor = self._toot_es_mio(db_id_m)
            if not _es_mio_autor:
                # Etiqueta según la relación actual, si está cacheada; si aún no se conoce, se
                # ofrece la combinada y se precarga la relación en segundo plano para la próxima.
                _rel = self._relacion_autor_cacheada(db_id_m)
                if _rel is True:
                    _lbl_seguir = _("Dejar de seguir al autor")
                elif _rel is False:
                    _lbl_seguir = _("Seguir al autor")
                else:
                    _lbl_seguir = _("Seguir o dejar de seguir al autor")
                    self._precargar_relacion_autor(db_id_m)
                item_seguir = menu.Append(wx.ID_ANY, _lbl_seguir)
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_seguir_autor(d), item_seguir)

                item_opc_seg = menu.Append(wx.ID_ANY, _("Opciones de seguimiento del autor..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_opciones_seguimiento(d), item_opc_seg)

                item_add_lista = menu.Append(wx.ID_ANY, _("Añadir el autor a una lista..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_anadir_autor_a_lista(d), item_add_lista)

                item_silenciar = menu.Append(wx.ID_ANY, _("Silenciar al autor"))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_silenciar_o_bloquear_autor(d, "mute"), item_silenciar)

                item_bloquear = menu.Append(wx.ID_ANY, _("Bloquear al autor"))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_silenciar_o_bloquear_autor(d, "block"), item_bloquear)

            item_mute_conv = menu.Append(wx.ID_ANY, _("Silenciar esta conversación"))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_silenciar_conversacion(d), item_mute_conv)
            self._items_menu_filtrar_publicacion(menu, db_id_m)

            item_ignorar_cliente = menu.Append(wx.ID_ANY, _("Ignorar publicaciones de este cliente"))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_ignorar_cliente(d), item_ignorar_cliente)

            item_detalles = menu.Append(wx.ID_ANY, _("Ver detalles del autor..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_detalles_autor(d), item_detalles)

            item_tl_autor = menu.Append(wx.ID_ANY, _("Ver publicaciones del autor o de un usuario mencionado..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_timeline_autor(d), item_tl_autor)

            item_cron_autor = menu.Append(wx.ID_ANY, _("Crear buffer con la cronología del autor o de un usuario mencionado..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_crear_cronologia_autor(d), item_cron_autor)

            if not _es_mio_autor:
                item_nota = menu.Append(wx.ID_ANY, _("Nota personal sobre el autor..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_nota_autor(d), item_nota)
                item_destacar = menu.Append(wx.ID_ANY, _("Destacar al autor en mi perfil"))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_destacar_autor(d, True), item_destacar)
                item_undestacar = menu.Append(wx.ID_ANY, _("Dejar de destacar al autor"))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_destacar_autor(d, False), item_undestacar)
                item_quitar_seg = menu.Append(wx.ID_ANY, _("Quitar al autor de mis seguidores"))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_quitar_seguidor(d), item_quitar_seg)
                item_block_dom = menu.Append(wx.ID_ANY, _("Bloquear todo el dominio del autor..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_bloquear_dominio_autor(d), item_block_dom)
            item_conocidos = menu.Append(wx.ID_ANY, _("Seguidores conocidos del autor..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_seguidores_conocidos(d), item_conocidos)
            item_listas_autor = menu.Append(wx.ID_ANY, _("Listas en las que está el autor..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_listas_de_autor(d), item_listas_autor)

            menu.AppendSeparator()
            if not _es_mio_autor:
                item_reportar = menu.Append(wx.ID_ANY, _("Reportar publicación..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_reportar_toot(d), item_reportar)
            item_tarjeta = menu.Append(wx.ID_ANY, _("Ver tarjeta de enlace de la publicación..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_tarjeta_enlace(d), item_tarjeta)
            if not es_dm:
                item_quien_boost = menu.Append(wx.ID_ANY, _("Ver quién la impulsó..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_interacciones_toot(d, "reblog"), item_quien_boost)
                item_quien_fav = menu.Append(wx.ID_ANY, _("Ver quién la marcó favorita..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_interacciones_toot(d, "favourite"), item_quien_fav)
            item_historial = menu.Append(wx.ID_ANY, _("Ver historial de ediciones..."))
            self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_historial_toot(d), item_historial)

            # Solo si la publicación contiene una encuesta.
            _fila_poll = db.query_one("SELECT COALESCE(has_poll, 0) FROM mastodon_statuses WHERE id = ?", (db_id_m,))
            if _fila_poll and _fila_poll[0]:
                item_poll = menu.Append(wx.ID_ANY, _("Ver publicación y encuesta..."))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id_m: self._on_ver_encuesta(d), item_poll)

            # Acciones propias de un buffer de HASHTAG (búsqueda guardada de un #tag).
            _tag_buffer = self._hashtag_del_buffer()
            if _tag_buffer:
                menu.AppendSeparator()
                item_seguir_tag = menu.Append(wx.ID_ANY, _("Seguir el hashtag #{0}").format(_tag_buffer))
                self.Bind(wx.EVT_MENU, lambda evt: self._on_seguir_hashtag_buffer(True), item_seguir_tag)
                item_dejar_tag = menu.Append(wx.ID_ANY, _("Dejar de seguir el hashtag #{0}").format(_tag_buffer))
                self.Bind(wx.EVT_MENU, lambda evt: self._on_seguir_hashtag_buffer(False), item_dejar_tag)
                item_dest_tag = menu.Append(wx.ID_ANY, _("Destacar el hashtag #{0} en mi perfil").format(_tag_buffer))
                self.Bind(wx.EVT_MENU, lambda evt: self._on_destacar_hashtag_buffer(), item_dest_tag)

        elif self.active_rss_account_id:
            db_id = self.lista_items_db_ids[idx]

            # Cargar estado
            row = db.query_one("SELECT read, favorite, media_url, COALESCE(read_later, 0) FROM rss_articles WHERE id = ?", (db_id,))
            if row:
                read, fav, media_url, leer_tarde = row

                txt_leido = (self._lbl_atajo("rss_toggle_read", _("Marcar como no leído")) if read
                             else self._lbl_atajo("rss_toggle_read", _("Marcar como leído")))
                item_read = menu.Append(wx.ID_ANY, txt_leido)
                self.Bind(wx.EVT_MENU, lambda evt, i=idx, d_id=db_id, val=read: self._on_toggle_read_rss(i, d_id, val), item_read)

                txt_fav = (self._lbl_atajo("pub_ver", _("Quitar de destacados")) if fav
                           else self._lbl_atajo("pub_ver", _("Destacar")))
                item_fav = menu.Append(wx.ID_ANY, txt_fav)
                self.Bind(wx.EVT_MENU, lambda evt, i=idx, d_id=db_id, val=fav: self._on_toggle_fav_rss(i, d_id, val), item_fav)

                txt_lt = (self._lbl_atajo("rss_leer_tarde", _("Quitar de Leer más tarde")) if leer_tarde
                          else self._lbl_atajo("rss_leer_tarde", _("Leer más tarde")))
                item_lt = menu.Append(wx.ID_ANY, txt_lt)
                self.Bind(wx.EVT_MENU, lambda evt, i=idx, d_id=db_id, val=leer_tarde: self._on_toggle_read_later_rss(i, d_id, val), item_lt)

                item_all_read = menu.Append(wx.ID_ANY, _("Marcar todo el buffer como leído"))
                self.Bind(wx.EVT_MENU, lambda evt: self._on_marcar_todo_leido_rss(), item_all_read)
                # Versiones anteriores (el medio corrigió la noticia): solo si hay alguna.
                from app.services.rss.rss_versiones import listar as _versiones
                if _versiones(db_id):
                    item_ver = menu.Append(wx.ID_ANY, _("Versiones anteriores del artículo..."))
                    self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_versiones_articulo(d_id), item_ver)

                if media_url:
                    item_play = menu.Append(wx.ID_ANY, _("Reproducir podcast / audio"))
                    self.Bind(wx.EVT_MENU, lambda evt, url=media_url, t_db=db_id: self._on_play_podcast(url, t_db), item_play)
                    item_cola = menu.Append(wx.ID_ANY, _("Añadir a la cola de podcasts"))
                    self.Bind(wx.EVT_MENU, lambda evt, t_db=db_id: self._on_encolar_podcast(t_db), item_cola)
                    # Descarga offline: etiqueta dinámica según si ya está descargado.
                    descargado = rss_service.episodio_descargado(db_id)
                    if descargado:
                        item_dl = menu.Append(wx.ID_ANY, _("Borrar descarga (offline)"))
                        self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_borrar_descarga_podcast(d_id), item_dl)
                    else:
                        item_dl = menu.Append(wx.ID_ANY, _("Descargar episodio (escuchar offline)"))
                        self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_descargar_episodio(d_id), item_dl)

            menu.AppendSeparator()

            item_copiar_t = menu.Append(wx.ID_ANY, self._lbl_atajo("rss_copiar_tit", _("Copiar título")))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_copiar_titulo_rss(d_id), item_copiar_t)

            item_copiar_a = menu.Append(wx.ID_ANY, _("Copiar artículo completo"))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_copiar_articulo_rss(d_id), item_copiar_a)

            item_exportar_a = menu.Append(wx.ID_ANY, _("Exportar artículo..."))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_exportar_articulo_rss(d_id), item_exportar_a)

            item_tags_a = menu.Append(wx.ID_ANY, _("Etiquetas del artículo..."))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_etiquetas_articulo(d_id), item_tags_a)

            item_galeria = menu.Append(wx.ID_ANY, _("Imágenes del artículo..."))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_galeria_imagenes(d_id), item_galeria)

            item_copiar_e = menu.Append(wx.ID_ANY, _("Copiar enlace"))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_copiar_enlace_rss(d_id), item_copiar_e)

            item_compartir = menu.Append(wx.ID_ANY, _("Compartir (Mastodon o Bluesky)..."))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_compartir_rss(d_id), item_compartir)

            # Enviar a Wallabag (leer más tarde externo): solo si el usuario lo configuró.
            from app.services.rss import wallabag as _wb
            if _wb.esta_configurado():
                item_wb = menu.Append(wx.ID_ANY, _("Enviar a Wallabag (leer más tarde)"))
                self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_enviar_wallabag(d_id), item_wb)

            item_audio_srv_rss = menu.Append(wx.ID_ANY, _("Reproducir audio del enlace (YouTube, SoundCloud...)"))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_reproducir_audio_servicio_rss(d_id), item_audio_srv_rss)

            item_eliminar_art = menu.Append(wx.ID_ANY, _("Eliminar artículo (a la papelera)"))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_eliminar_articulo_rss(d_id), item_eliminar_art)

            menu.AppendSeparator()

            item_tr = menu.Append(wx.ID_ANY, self._lbl_atajo("ia_traducir", _("Traducir artículo")))
            self.Bind(wx.EVT_MENU, self._on_traducir_seleccionado, item_tr)

            item_clean = menu.Append(wx.ID_ANY, _("Modo de lectura limpia"))
            self.Bind(wx.EVT_MENU, lambda evt: self._on_ver_articulo_limpio(db_id), item_clean)

            item_tts = menu.Append(wx.ID_ANY, _("Leer en voz alta (TTS)"))
            self.Bind(wx.EVT_MENU, lambda evt, d_id=db_id: self._on_leer_tts_rss(d_id), item_tts)

            item_html = menu.Append(wx.ID_ANY, _("Ver como HTML en el navegador"))
            self.Bind(wx.EVT_MENU, lambda evt: self._on_ver_articulo_html(db_id), item_html)

        elif self.active_telegram_account_id:
            try:
                _dbid, chat_id, _title = self.lista_items_db_ids[idx].split("|", 2)
                chat_id = int(chat_id)
            except (ValueError, AttributeError):
                self.PopupMenu(menu)
                menu.Destroy()
                return

            # Estado actual para que el menú sea DINÁMICO y específico por tipo de chat.
            est = db.query_one(
                "SELECT unread_count, muted, pinned, chat_type, COALESCE(blocked,0) FROM telegram_chats WHERE id = ?",
                (f"{self.active_telegram_account_id}:{chat_id}",))
            unread = (est[0] if est else 0) or 0
            muted = bool(est[1]) if est else False
            pinned = bool(est[2]) if est else False
            tipo = (est[3] if est else "private") or "private"
            blocked = bool(est[4]) if est else False
            es_usuario = tipo in ("private", "bot")
            es_grupo = tipo in ("group", "supergroup")
            es_canal = tipo == "channel"

            item_abrir = menu.Append(wx.ID_ANY, _("Abrir conversación (Enter)"))
            self.Bind(wx.EVT_MENU, lambda evt, i=idx: self._abrir_conversacion_telegram(i), item_abrir)

            if unread:
                item_leido = menu.Append(wx.ID_ANY, _("Marcar como leído ({0} sin leer)").format(unread))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "leido"), item_leido)
            else:
                item_no_leido = menu.Append(wx.ID_ANY, _("Marcar como no leído"))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "no_leido"), item_no_leido)

            item_pin = menu.Append(wx.ID_ANY, _("Desfijar de la lista") if pinned else _("Fijar en la lista"))
            self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "fijar"), item_pin)

            # Archivar / Desarchivar: Telegram no nos da de forma fiable si el chat ya está archivado,
            # así que se ofrecen ambas en un submenú (la que no aplique simplemente no hace nada).
            submenu_arch = wx.Menu()
            it_arch = submenu_arch.Append(wx.ID_ANY, _("Archivar este chat"))
            self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "archivar"), it_arch)
            it_desarch = submenu_arch.Append(wx.ID_ANY, _("Sacar del archivo"))
            self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "desarchivar"), it_desarch)
            menu.AppendSubMenu(submenu_arch, _("Archivar / desarchivar"))

            # En canales/supergrupos, vaciar borra los mensajes PARA TODOS y exige ser admin (lo dice
            # la etiqueta); en privados y grupos básicos vacía tu historial.
            _lbl_vaciar = (_("Vaciar canal (borra los mensajes para todos, solo admin)...")
                           if tipo in ("channel", "supergroup")
                           else _("Vaciar conversación..."))
            item_vaciar = menu.Append(wx.ID_ANY, _lbl_vaciar)
            self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "vaciar"), item_vaciar)

            # SILENCIAR: refleja el estado. Si ya está silenciado, ofrece reactivar; si no, un
            # submenú para elegir cuánto tiempo o para siempre (como otras apps de Telegram).
            if muted:
                item_mute = menu.Append(wx.ID_ANY, _("Reactivar notificaciones (ahora silenciado)"))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "reactivar"), item_mute)
            else:
                submenu_mute = wx.Menu()
                for etiqueta, accion in (
                        (_("1 hora"), "silenciar_min:60"),
                        (_("8 horas"), "silenciar_min:480"),
                        (_("2 días"), "silenciar_min:2880"),
                        (_("Para siempre"), "silenciar_siempre")):
                    it = submenu_mute.Append(wx.ID_ANY, etiqueta)
                    self.Bind(wx.EVT_MENU, lambda evt, c=chat_id, a=accion: self._on_telegram_chat_accion(c, a), it)
                menu.AppendSubMenu(submenu_mute, _("Silenciar notificaciones"))

            menu.AppendSeparator()
            # Acciones ESPECÍFICAS según el tipo de chat.
            if es_usuario:
                # Refleja el estado real de bloqueo: una sola opción que alterna.
                if blocked:
                    item_bloq = menu.Append(wx.ID_ANY, _("Desbloquear (ahora bloqueado)"))
                    self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "desbloquear"), item_bloq)
                else:
                    etiqueta_bloq = _("Bloquear al bot") if tipo == "bot" else _("Bloquear al usuario")
                    item_bloq = menu.Append(wx.ID_ANY, etiqueta_bloq)
                    self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "bloquear"), item_bloq)
                item_borrar = menu.Append(wx.ID_ANY, _("Borrar este chat..."))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "borrar"), item_borrar)
                if tipo != "bot":
                    item_captura = menu.Append(wx.ID_ANY, _("Avisar de captura de pantalla"))
                    self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "aviso_captura"), item_captura)
            elif es_grupo:
                item_miembros = menu.Append(wx.ID_ANY, _("Ver miembros..."))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_tg_ver_miembros(c), item_miembros)
                item_salir = menu.Append(wx.ID_ANY, _("Salir del grupo..."))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "salir"), item_salir)
            elif es_canal:
                titulo_chat = (db.query_one(
                    "SELECT title FROM telegram_chats WHERE id = ?",
                    (f"{self.active_telegram_account_id}:{chat_id}",)) or [""])[0] or ""
                item_similares = menu.Append(wx.ID_ANY, _("Canales similares..."))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id, t=titulo_chat: self._on_tg_canales_similares(c, t), item_similares)
                item_hist_canal = menu.Append(wx.ID_ANY, _("Historias del canal..."))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id, t=titulo_chat: self._on_tg_historias_canal(c, t), item_hist_canal)
                item_salir = menu.Append(wx.ID_ANY, _("Dejar de seguir el canal..."))
                self.Bind(wx.EVT_MENU, lambda evt, c=chat_id: self._on_telegram_chat_accion(c, "salir"), item_salir)
            menu.AppendSeparator()

            # Añadir o quitar este chat de tus carpetas. DINÁMICO: por cada carpeta se ofrece SOLO
            # "Quitar" si el chat ya está en ella, o SOLO "Añadir" si no (según chat_ids_json).
            import json as _json
            carpetas = db.query(
                "SELECT folder_id, title, COALESCE(chat_ids_json, '[]') FROM telegram_folders WHERE account_id = ? ORDER BY folder_id",
                (self.active_telegram_account_id,))
            if carpetas:
                submenu_carp = wx.Menu()
                for f_id, f_title, ids_json in carpetas:
                    try:
                        ids = {str(x) for x in _json.loads(ids_json or "[]")}
                    except Exception:
                        ids = set()
                    if str(chat_id) in ids:
                        s_del = submenu_carp.Append(wx.ID_ANY, _("Quitar de «{0}»").format(f_title))
                        self.Bind(wx.EVT_MENU,
                                  lambda evt, c=chat_id, fi=f_id: self._on_tg_chat_carpeta(c, fi, False), s_del)
                    else:
                        s_add = submenu_carp.Append(wx.ID_ANY, _("Añadir a «{0}»").format(f_title))
                        self.Bind(wx.EVT_MENU,
                                  lambda evt, c=chat_id, fi=f_id: self._on_tg_chat_carpeta(c, fi, True), s_add)
                menu.AppendSubMenu(submenu_carp, _("Carpetas"))

        # Si el menú se muestra sobre otra ventana (diálogo "Ver publicación"), no pasa por
        # MainFrame.PopupMenu, así que la organización en subcategorías se aplica aquí.
        if _popup_target is not None:
            try:
                from .menu_teclas import anotar_menu
                anotar_menu(menu, incluir_publicacion=True)
                from .menu_organizador import organizar_contextual
                organizar_contextual(menu)
            except Exception as e:
                log.error(f"No se pudo preparar el menú contextual: {e}")
            _popup_target.PopupMenu(menu)
        else:
            self.PopupMenu(menu)
        menu.Destroy()

    # --- MENÚ TELEGRAM (herramientas de cuenta) ---
    def _tg_cuenta_activa(self):
        """Devuelve el id de la cuenta de Telegram activa, o avisa si no hay."""
        if self.active_telegram_account_id:
            return self.active_telegram_account_id
        row = db.query_one("SELECT id FROM accounts WHERE service_id = 'telegram' AND active = 1 LIMIT 1")
        if row:
            return row[0]
        speak(_("No hay ninguna cuenta de Telegram. Añádala desde Gestionar Cuentas."), interrupt=True)
        return None

    def _on_tg_perfil(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        def _bg(ctx):
            return telegram_service.obtener_mi_perfil(cuenta)
        def _finish(res, err):
            if err:
                notificar(_("Error al obtener el perfil: {0}").format(err))
                return
            from .telegram_views import TelegramProfileDialog
            dlg = TelegramProfileDialog(self, cuenta, res)
            dlg.ShowModal()
            dlg.Destroy()
        speak(_("Cargando perfil..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_sesiones(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        def _bg(ctx):
            return telegram_service.listar_sesiones(cuenta)
        def _finish(res, err):
            if err:
                notificar(_("Error al listar sesiones: {0}").format(err))
                return
            from .telegram_views import TelegramSessionsDialog
            dlg = TelegramSessionsDialog(self, cuenta, res)
            dlg.ShowModal()
            dlg.Destroy()
        speak(_("Cargando sesiones..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_buscar_global(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Texto a buscar en todos los chats:"), _("Búsqueda global en Telegram"))
        if dlg.ShowModal() == wx.ID_OK:
            query = dlg.GetValue().strip()
            if query:
                def _bg(ctx):
                    return telegram_service.buscar_global(cuenta, query)
                def _finish(res, err):
                    if err:
                        notificar(_("Error en la búsqueda: {0}").format(err))
                        return
                    if not res:
                        speak(_("Sin resultados."), interrupt=True)
                        return
                    opciones = [f"{r['chat_title']}: {r['text']}" for r in res]
                    sel = wx.SingleChoiceDialog(self, _("Resultados (Aceptar abre la conversación):"), _("Resultados"), opciones)
                    if sel.ShowModal() == wx.ID_OK:
                        r = res[sel.GetSelection()]
                        from .telegram_views import TelegramConversationDialog
                        d = TelegramConversationDialog(self, cuenta, r["chat_id"], r["chat_title"])
                        d.ShowModal()
                        d.Destroy()
                    sel.Destroy()
                speak(_("Buscando..."), interrupt=True)
                task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_tg_buscar_canales(self, event):
        """Busca por HASHTAG publicaciones públicas de canales de TODO Telegram (search_posts),
        aunque no estés suscrito al canal. Complementa a la búsqueda global (que solo busca en
        tus chats)."""
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Hashtag a buscar en canales públicos (con o sin #):"),
                                    _("Buscar publicaciones de canales"))
        query = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not query:
            return

        def _bg(ctx):
            return telegram_service.buscar_publicaciones_canales(cuenta, query)

        def _finish(res, err):
            if err:
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            if not res:
                speak(_("Sin resultados para #{0}.").format(query.lstrip("#")), interrupt=True)
                return
            opciones = [f"{r['chat_title']}: {r['text'][:120]}" for r in res]
            sel = wx.SingleChoiceDialog(self, _("Publicaciones de canales (Aceptar para ver opciones):"),
                                        _("Resultados"), opciones)
            if sel.ShowModal() == wx.ID_OK:
                r = res[sel.GetSelection()]
                self._accion_publicacion_canal(cuenta, r)
            sel.Destroy()
        speak(_("Buscando en canales públicos..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _accion_publicacion_canal(self, cuenta, r):
        """Acciones sobre un resultado de la búsqueda de canales: ver el texto completo,
        abrir el canal (como conversación) o copiar el enlace t.me."""
        opciones = [_("Ver el texto completo"), _("Abrir el canal")]
        enlace = ""
        if r.get("chat_username"):
            enlace = f"https://t.me/{r['chat_username']}/{r['message_id']}"
            opciones.append(_("Copiar enlace de la publicación"))
        d = wx.SingleChoiceDialog(self, _("¿Qué quieres hacer?"), _("Publicación de canal"), opciones)
        if d.ShowModal() != wx.ID_OK:
            d.Destroy()
            return
        sel = d.GetSelection()
        d.Destroy()
        if sel == 0:
            self._mostrar_texto_largo(_("Publicación de {0}").format(r.get("chat_title") or _("canal")),
                                      r.get("text") or "")
        elif sel == 1:
            from .telegram_views import TelegramConversationDialog
            dd = TelegramConversationDialog(self, cuenta, r["chat_id"], r.get("chat_title") or "")
            dd.ShowModal()
            dd.Destroy()
        else:
            from app.core.speech import copiar_al_portapapeles
            copiar_al_portapapeles(enlace, _("Enlace copiado al portapapeles."))

    def _on_tg_buscar_chats(self, event):
        """Busca USUARIOS, GRUPOS, SUPERGRUPOS y CANALES públicos por nombre o @usuario (como la
        lupa de Telegram) y abre el elegido."""
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self, _("Nombre o @usuario de la persona, grupo o canal a buscar:"),
            _("Buscar en Telegram"))
        query = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not query:
            return
        tipo_label = {"private": _("Usuario"), "bot": _("Bot"), "group": _("Grupo"),
                      "supergroup": _("Supergrupo"), "channel": _("Canal")}

        def _bg(ctx):
            return telegram_service.buscar_contactos(cuenta, query)

        def _finish(res, err):
            if err:
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            if not res:
                speak(_("Sin resultados."), interrupt=True)
                return
            opciones = []
            for r in res:
                etiqueta = tipo_label.get(r.get("tipo"), "")
                usuario = r.get("username") or ""
                opciones.append(" — ".join(p for p in [r["nombre"], etiqueta, usuario] if p))
            sel = wx.SingleChoiceDialog(
                self, _("Resultados (Aceptar abre la conversación):"),
                _("Buscar en Telegram"), opciones)
            if sel.ShowModal() == wx.ID_OK:
                r = res[sel.GetSelection()]
                from .telegram_views import TelegramConversationDialog
                d = TelegramConversationDialog(self, cuenta, r["id"], r["nombre"],
                                               chat_type=r.get("tipo", "private"))
                d.ShowModal()
                d.Destroy()
            sel.Destroy()
        speak(_("Buscando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_ver_miembros(self, chat_id):
        """Muestra los miembros de un grupo o supergrupo (nombre y estado)."""
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return

        def _bg(ctx):
            return telegram_service.obtener_miembros(cuenta, chat_id)

        def _finish(res, err):
            if err:
                notificar(_("No se pudieron cargar los miembros: {0}").format(err))
                return
            if not res:
                speak(_("Sin miembros visibles."), interrupt=True)
                return
            lineas = [f"{m['nombre']} — {m['estado']}" for m in res]
            self._mostrar_texto_largo(_("Miembros ({0})").format(len(res)), "\n".join(lineas))
        speak(_("Cargando miembros..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_guardados(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        def _bg(ctx):
            return telegram_service.obtener_id_guardados(cuenta)
        def _finish(res, err):
            if err or not res:
                notificar(_("No se pudo abrir Mensajes guardados: {0}").format(err or ""))
                return
            from .telegram_views import TelegramConversationDialog
            d = TelegramConversationDialog(self, cuenta, res, _("Mensajes guardados"), chat_type="private")
            d.ShowModal()
            d.Destroy()
        speak(_("Abriendo Mensajes guardados..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_chat_carpeta(self, chat_id, folder_id, anadir):
        """Añade o quita un chat de una carpeta desde el menú contextual de la lista."""
        cuenta = self.active_telegram_account_id
        def _bg(ctx):
            if anadir:
                return telegram_service.anadir_chat_a_carpeta(cuenta, folder_id, chat_id)
            return telegram_service.quitar_chat_de_carpeta(cuenta, folder_id, chat_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            elif res is False:
                notificar(_("El chat ya estaba en esa carpeta.") if anadir
                          else _("El chat no estaba en esa carpeta."))
            else:
                notificar(_("Carpeta actualizada."))
                self._cargar_navegacion()
        speak(_("Aplicando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_privacidad(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        claves = [
            ("last_seen", _("Última vez y en línea")),
            ("profile_photo", _("Foto de perfil")),
            ("phone_number", _("Número de teléfono")),
            ("forwards", _("Enlace al reenviar mensajes")),
            ("calls", _("Llamadas")),
            ("voice_messages", _("Mensajes de voz")),
            ("chat_invite", _("Invitaciones a grupos")),
            ("bio", _("Biografía")),
        ]
        dlg = wx.SingleChoiceDialog(
            self, _("¿Qué ajuste de privacidad quieres cambiar?"), _("Privacidad de Telegram"),
            [etq for _c, etq in claves])
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        clave = claves[dlg.GetSelection()][0]
        dlg.Destroy()
        modos = [("todos", _("Todos")), ("contactos", _("Mis contactos")), ("nadie", _("Nadie"))]
        dlg2 = wx.SingleChoiceDialog(
            self, _("¿Quién puede verlo?"), _("Privacidad"), [etq for _m, etq in modos])
        if dlg2.ShowModal() != wx.ID_OK:
            dlg2.Destroy(); return
        modo = modos[dlg2.GetSelection()][0]
        dlg2.Destroy()
        def _bg(ctx):
            return telegram_service.fijar_privacidad(cuenta, clave, modo)
        def _finish(res, err):
            if err:
                notificar(_("No se pudo cambiar: {0}").format(err))
            else:
                notificar(_("Privacidad actualizada."))
        speak(_("Aplicando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_bloqueados(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        def _bg(ctx):
            return telegram_service.listar_bloqueados(cuenta)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("No tienes usuarios bloqueados."))
                return
            etiquetas = [f"{b['nombre']} {b['username']}".strip() for b in res]
            dlg = wx.SingleChoiceDialog(
                self, _("Usuarios bloqueados (Aceptar para desbloquear):"),
                _("Usuarios bloqueados"), etiquetas)
            if dlg.ShowModal() == wx.ID_OK:
                uid = res[dlg.GetSelection()]["id"]
                def _bg2(ctx):
                    return telegram_service.desbloquear_usuario(cuenta, uid)
                def _finish2(r2, e2):
                    notificar(_("Error: {0}").format(e2) if e2 else _("Usuario desbloqueado."))
                task_manager.start_task(_bg2, callback=_finish2, pass_context=True)
            dlg.Destroy()
        speak(_("Consultando bloqueados..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_carpetas(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .telegram_views import TelegramCarpetasDialog
        dlg = TelegramCarpetasDialog(self, cuenta)
        dlg.ShowModal()
        dlg.Destroy()
        self._cargar_navegacion()

    def _on_tg_frecuentes(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .telegram_views import TelegramChatsSugeridosDialog
        dlg = TelegramChatsSugeridosDialog(self, cuenta, modo="frecuentes")
        dlg.ShowModal()
        dlg.Destroy()

    def _on_tg_privacidad_global(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .telegram_views import TelegramPrivacidadGlobalDialog
        dlg = TelegramPrivacidadGlobalDialog(self, cuenta)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_tg_ajustes_cuenta(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .telegram_views import TelegramAjustesCuentaDialog
        dlg = TelegramAjustesCuentaDialog(self, cuenta)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_tg_resumen(self, event):
        """Muestra el número total de chats y de contactos de la cuenta."""
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        speak(_("Calculando..."), interrupt=True)
        def _bg(ctx):
            return (telegram_service.contar_dialogos(cuenta), telegram_service.contar_contactos(cuenta))
        def _finish(res, err):
            if err:
                notificar(_("No se pudo calcular: {0}").format(err))
                return
            chats, contactos = res
            notificar(_("Tienes {0} chats y {1} contactos.").format(chats or 0, contactos or 0))
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_historias_canal(self, chat_id, titulo=""):
        """Muestra las historias activas de un canal; al elegir una, la descarga."""
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        import wx as _wx
        speak(_("Cargando historias del canal..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.historias_de_chat(cuenta, chat_id)
        def _finish(historias, err):
            if err:
                notificar(_("No se pudo cargar: {0}").format(err))
                return
            if not historias:
                speak(_("Este canal no tiene historias activas."), interrupt=True)
                return
            etiquetas = [f"{h.get('tipo','')}. {h.get('caption','') or _('(sin texto)')}. {h.get('fecha','')}"
                         for h in historias]
            dlg = _wx.SingleChoiceDialog(self, _("Historias de «{0}» (Aceptar para descargar):").format(titulo),
                                         _("Historias del canal"), etiquetas)
            if dlg.ShowModal() == _wx.ID_OK:
                h = historias[dlg.GetSelection()]
                def _bg2(ctx):
                    return telegram_service.descargar_historia(cuenta, h["chat_id"], h["story_id"])
                def _finish2(ruta, err2):
                    if err2:
                        notificar(_("No se pudo descargar: {0}").format(err2))
                    elif ruta:
                        speak(_("Historia descargada en {0}.").format(ruta), interrupt=True)
                    else:
                        speak(_("La historia no tiene archivo."), interrupt=True)
                speak(_("Descargando..."), interrupt=True)
                task_manager.start_task(_bg2, callback=_finish2, pass_context=True)
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_canales_similares(self, chat_id, titulo=""):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .telegram_views import TelegramChatsSugeridosDialog
        dlg = TelegramChatsSugeridosDialog(self, cuenta, modo="similares",
                                           chat_id_origen=chat_id, titulo_origen=titulo)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_tg_historias(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .telegram_views import TelegramHistoriasDialog
        dlg = TelegramHistoriasDialog(self, cuenta)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_tg_mis_bots(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .telegram_views import TelegramMisBotsDialog
        dlg = TelegramMisBotsDialog(self, cuenta)
        dlg.ShowModal()
        dlg.Destroy()
