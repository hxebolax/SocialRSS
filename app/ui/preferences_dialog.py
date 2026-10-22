# -*- coding: utf-8 -*-
"""Diálogo de preferencias: ensamblador de los mixins de panel por sección.
"""
import wx
import logging
from app.core.config_manager import config
from app.core.event_bus import event_bus
from .prefs_general import _PrefGeneralMixin
from .prefs_accesibilidad import _PrefAccMixin
from .prefs_servicios import _PrefServiciosMixin
from .prefs_avanzado import _PrefAvanzadoMixin
from .prefs_rss_articulo import _PrefRssArticuloMixin


log = logging.getLogger(__name__)

class PreferencesDialog(_PrefGeneralMixin, _PrefAccMixin, _PrefServiciosMixin, _PrefAvanzadoMixin,
                        _PrefRssArticuloMixin, wx.Dialog):
    """Ventana de configuración accesible organizada en un árbol de secciones y
    subsecciones a la izquierda y el panel de opciones correspondiente a la derecha."""
    
    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Preferencias de la aplicación"),
            size=(600, 500),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo de preferencias")
        )
        
        # Guardar valores de configuración actuales temporalmente
        self._temp_settings = {}
        
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Navegación por árbol (secciones y subsecciones) a la izquierda y el contenido
        # de la sección seleccionada a la derecha (wx.Simplebook: un libro sin pestañas
        # que mantiene todos los paneles vivos y muestra solo uno).
        cont = wx.BoxSizer(wx.HORIZONTAL)
        self.tree_cfg = wx.TreeCtrl(
            self,
            style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_LINES_AT_ROOT | wx.TR_SINGLE | wx.TR_FULL_ROW_HIGHLIGHT,
            size=(230, -1), name=_("Secciones de configuración"))
        self.book = wx.Simplebook(self, name=_("Contenido de la sección"))

        # Definición del árbol: (nivel, etiqueta, constructor_de_panel).
        # nivel 0 = sección de primer nivel; nivel 1 = subsección de la sección anterior.
        spec = [
            (0, _("General"), self._panel_general_root),
            (1, _("Ventana y bandeja"), self._panel_general_ventana),
            (1, _("Actualización automática"), self._panel_general_actualizacion),
            (1, _("Presentación"), self._panel_general_presentacion),
            (0, _("Accesibilidad"), self._panel_acc_root),
            (1, _("Avisos de multimedia"), self._panel_acc_multimedia),
            (1, _("Presentación del texto"), self._panel_acc_texto),
            (1, _("Navegación por columnas"), self._panel_acc_columnas),
            (0, _("Mastodon"), self._panel_mastodon_root),
            (1, _("Mantenimiento de Mastodon"), self._panel_mastodon_mantenimiento),
            (0, _("Bluesky"), self._panel_bluesky_root),
            (1, _("Mantenimiento de Bluesky"), self._panel_bluesky_mantenimiento),
            (0, _("W Social"), self._panel_wsocial_root),
            (0, _("RSS"), self._panel_rss_root),
            (1, _("Artículo completo y suscripciones"), self._panel_rss_articulo),
            (1, _("Mantenimiento de RSS"), self._panel_rss_mantenimiento),
            (0, _("Telegram"), self._panel_telegram_root),
            (1, _("Telegram Drive (nube personal)"), self._panel_telegram_drive),
            (0, _("Inteligencia Artificial"), self._crear_panel_ai),]
        # X (Twitter) solo si el cliente está disponible (kill-switch). Se inserta antes
        # de IA/Traducción para que quede junto a las demás redes.
        try:
            from app.core.feature_flags import x_libreria_instalada
            if x_libreria_instalada():
                # Tras Telegram/IA, como sección de red propia.
                spec.insert(len(spec) - 1, (0, _("X (Twitter)"), self._panel_x_root))
        except Exception:
            pass
        # IRC solo si su librería (pydle) está instalada, junto a las demás redes.
        try:
            from app.core.feature_flags import irc_libreria_instalada
            if irc_libreria_instalada():
                spec.insert(len(spec) - 1, (0, _("IRC"), self._panel_irc_root))
        except Exception:
            pass
        # Matrix solo si su librería (matrix-nio) está instalada.
        try:
            from app.core.feature_flags import matrix_libreria_instalada
            if matrix_libreria_instalada():
                spec.insert(len(spec) - 1, (0, _("Matrix"), self._panel_matrix_root))
        except Exception:
            pass
        # XMPP solo si su librería (slixmpp) está instalada.
        try:
            from app.core.feature_flags import xmpp_libreria_instalada
            if xmpp_libreria_instalada():
                spec.insert(len(spec) - 1, (0, _("XMPP"), self._panel_xmpp_root))
        except Exception:
            pass
        # Reddit solo si su librería (praw) está instalada.
        try:
            from app.core.feature_flags import reddit_libreria_instalada
            if reddit_libreria_instalada():
                spec.insert(len(spec) - 1, (0, _("Reddit"), self._panel_reddit_root))
                spec.insert(len(spec) - 1, (1, _("Mantenimiento de Reddit"), self._panel_reddit_mantenimiento))
        except Exception:
            pass
        # PeerTube (vídeo federado): su librería (httpx/pydantic) es dependencia dura, pero se gatea
        # igual por consistencia. Cuatro subsecciones por categoría bajo la sección PeerTube.
        try:
            from app.core.feature_flags import peertube_libreria_instalada
            if peertube_libreria_instalada():
                spec.insert(len(spec) - 1, (0, _("PeerTube"), self._panel_peertube_root))
                spec.insert(len(spec) - 1, (1, _("Reproducción de PeerTube"), self._panel_peertube_reproduccion))
                spec.insert(len(spec) - 1, (1, _("Contenido y publicación de PeerTube"), self._panel_peertube_contenido))
                spec.insert(len(spec) - 1, (1, _("Mantenimiento de PeerTube"), self._panel_peertube_mantenimiento))
        except Exception:
            pass
        spec += [
            (0, _("Traducción"), self._crear_panel_translation),
            (0, _("Reproductores"), self._panel_reproductores),
            (0, _("Sonidos"), self._crear_panel_sounds),
            (1, _("Micrófono"), self._panel_microfono),
            (0, _("Proxy"), self._crear_panel_proxy),
        ]

        raiz = self.tree_cfg.AddRoot("root")
        seccion_actual = None
        for nivel, etiqueta, builder in spec:
            panel = builder(self.book)
            idx = self.book.GetPageCount()
            self.book.AddPage(panel, etiqueta)
            if nivel == 0:
                seccion_actual = self.tree_cfg.AppendItem(raiz, etiqueta, data=idx)
            else:
                self.tree_cfg.AppendItem(seccion_actual, etiqueta, data=idx)
        self.tree_cfg.ExpandAll()

        cont.Add(self.tree_cfg, 0, wx.EXPAND | wx.ALL, 6)
        cont.Add(self.book, 1, wx.EXPAND | wx.ALL, 6)
        sizer.Add(cont, 1, wx.EXPAND)

        # Botones de Aceptar / Cancelar estándar
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_guardar = wx.Button(self, wx.ID_OK, _("&Guardar"), name=_("Guardar configuración"))
        self.btn_cancelar = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"), name=_("Cancelar cambios"))
        btn_sizer.Add(self.btn_guardar, 0, wx.ALL, 5)
        btn_sizer.Add(self.btn_cancelar, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        self.SetSizer(sizer)

        # Mostrar la primera sección y seleccionarla en el árbol.
        self.book.ChangeSelection(0)
        primero, _cookie = self.tree_cfg.GetFirstChild(raiz)
        if primero.IsOk():
            self.tree_cfg.SelectItem(primero)

        # Enlazar guardado y cambio de sección. El foco arranca en el árbol.
        self.Bind(wx.EVT_BUTTON, self._on_save, id=wx.ID_OK)
        self.tree_cfg.Bind(wx.EVT_TREE_SEL_CHANGED, self._on_tree_sel)
        self.tree_cfg.SetFocus()

    def _on_tree_sel(self, event):
        """Al cambiar la sección/subsección seleccionada en el árbol, muestra su panel
        SIN mover el foco: el foco se queda en el árbol para que el usuario siga
        navegando con las flechas, y solo pasa al panel cuando pulsa Tab.

        wx.Simplebook.ChangeSelection da el foco a la página mostrada, así que se lo
        devolvemos al árbol justo después."""
        try:
            item = event.GetItem()
            if not item or not item.IsOk():
                return
            idx = self.tree_cfg.GetItemData(item)
            if idx is not None and 0 <= idx < self.book.GetPageCount():
                self.book.ChangeSelection(idx)
                # Recuperar el foco en el árbol (ChangeSelection lo movió al panel).
                self.tree_cfg.SetFocus()
        except RuntimeError:
            # El árbol o el libro pueden estar destruyéndose al cerrar el diálogo;
            # en ese caso no hay nada que mostrar.
            return
        event.Skip()

    # --- CREADORES DE PANELES ---
    

    def _on_save(self, event):
        """Valida, guarda los cambios en config_manager y cierra."""
        # VALIDACIÓN BLOQUEANTE: siempre debe quedar al menos UN servicio activo. Si los cambios
        # dejarían cero, se muestra un mensaje y NO se cierra el diálogo (no se guarda nada) para
        # que el usuario reactive uno antes de continuar.
        from app.core.feature_flags import (servicio_habilitado, X_CLIENTE_HABILITADO,
                                            x_libreria_instalada, SERVICIOS_CONMUTABLES)
        _intencion_serv = {}
        for _sid in SERVICIOS_CONMUTABLES:
            cb = getattr(self, f"cb_serv_{_sid}", None)
            _intencion_serv[_sid] = cb.GetValue() if cb is not None else servicio_habilitado(_sid)
        _x_cb = getattr(self, "cb_x_enabled", None)
        _x_on = (_x_cb.GetValue() if _x_cb is not None else bool(config.get("x", "enabled", True))) \
            and X_CLIENTE_HABILITADO and x_libreria_instalada()
        if sum(1 for v in _intencion_serv.values() if v) + (1 if _x_on else 0) == 0:
            wx.MessageBox(
                _("No puedes desactivar todos los servicios: ya tienes los demás desactivados y "
                  "debe quedar al menos uno activo.\n\nVuelve a activar al menos un servicio "
                  "(Mastodon, Bluesky, RSS, Telegram o X) para poder guardar."),
                _("Al menos un servicio activo"), wx.OK | wx.ICON_WARNING, self)
            # Devolver el foco a la casilla de Mastodon para que sea fácil reactivarlo.
            cb_m = getattr(self, "cb_serv_mastodon", None)
            if cb_m is not None:
                cb_m.SetFocus()
            return  # NO cerrar ni guardar: el usuario debe corregir primero.

        # Agrupar TODOS los config.set() de este método en un único guardado: antes, cada set()
        # escribía settings.json (con fsync) en el hilo de la UI, llegando a ~117 escrituras al
        # aplicar Preferencias. Con el lote, se guarda una sola vez (en config.save() del final).
        # try/finally garantiza que el lote se cierre aunque algún GetValue lance.
        config.begin_batch()
        try:
            self._recoger_preferencias_en_lote()
        finally:
            config.end_batch()  # guarda una sola vez si hubo cambios
        log.info("Nuevas preferencias guardadas por el usuario.")

        # Emitir cambio de volumen sonoro al sound_manager en tiempo real
        event_bus.emit("sound_trigger", "ready")

        # NO se añade ningún aviso hablado al cerrar: al devolverse el foco a la ventana
        # principal el lector de pantalla ya está locutando, y un mensaje aquí se solapa y se
        # entrecorta. Que activar o desactivar una red requiere reiniciar lo explica el texto
        # que hay junto a cada casilla, en su propia sección de Preferencias.
        self.EndModal(wx.ID_OK)

    def _recoger_preferencias_en_lote(self):
        """Lee todos los controles del diálogo y los vuelca a la configuración (dentro del lote de
        guardado abierto por _on_save). No guarda a disco aquí: lo hace end_batch() una sola vez."""
        # General. El selector muestra el NOMBRE del idioma pero se guarda el CÓDIGO (es, en…).
        try:
            config.set("general", "language", self._lang_codigos[self.choice_lang.GetSelection()])
        except Exception:
            config.set("general", "language", "es")
        config.set("general", "confirm_exit", self.cb_confirm_exit.GetValue())
        config.set("general", "minimize_to_tray", self.cb_minimize_tray.GetValue())
        config.set("general", "start_minimized", self.cb_start_minimized.GetValue())
        # Arranque con Windows: el estado REAL vive en el registro, no en settings.json. Se guarda
        # también en la configuración solo como reflejo, para poder mostrarlo sin leer el registro.
        if hasattr(self, "cb_run_at_startup"):
            quiere = self.cb_run_at_startup.GetValue()
            from app.core import windows_startup
            if quiere != windows_startup.esta_activado():
                if windows_startup.aplicar(quiere):
                    config.set("general", "run_at_startup", quiere)
                else:
                    from app.core.speech import notificar
                    notificar(_("No se pudo cambiar el inicio automático con Windows. "
                                "Revisa el registro de la aplicación."))
            else:
                config.set("general", "run_at_startup", quiere)
        if hasattr(self, "cb_menus_cat"):
            config.set("general", "menus_categorizados", self.cb_menus_cat.GetValue())
        config.set("general", "relative_times", self.cb_relative_times.GetValue())
        config.set("general", "newest_first", self.choice_order.GetSelection() == 0)
        config.set("general", "auto_update_enabled", self.cb_auto_update.GetValue())
        config.set("general", "check_updates_on_start", self.cb_check_updates.GetValue())
        # Bandeja de entrada unificada (activar/desactivar). Si cambia, hay que reconstruir el árbol
        # para que su nodo aparezca o desaparezca (se hace tras guardar, ver más abajo).
        self._inbox_estaba = config.get("inbox", "enabled", False)
        config.set("inbox", "enabled", self.cb_inbox.GetValue())
        if hasattr(self, "cb_inbox_details"):
            config.set("inbox", "show_details_panel", self.cb_inbox_details.GetValue())
        if hasattr(self, "cb_inbox_buttons"):
            config.set("inbox", "show_action_buttons", self.cb_inbox_buttons.GetValue())
        # El intervalo general ya no existe: cada servicio guarda el suyo más abajo.

        # Accesibilidad
        config.set("accessibility", "screen_reader_mode", self.cb_screen_reader.GetValue())
        config.set("accessibility", "enable_sounds", self.cb_sounds.GetValue())
        config.set("accessibility", "speech_announcements", self.cb_speech.GetValue())
        if hasattr(self, "cb_contador_vivo"):
            config.set("accessibility", "contador_en_vivo", self.cb_contador_vivo.GetValue())
        config.set("accessibility", "braille_announcements", self.cb_braille.GetValue())
        config.set("accessibility", "modo_avisos", self._modos_avisos[self.choice_modo_avisos.GetSelection()])
        config.set("accessibility", "toast_windows", self.cb_toast.GetValue())
        config.set("accessibility", "interfaz_invisible", self.cb_invisible.GetValue())
        config.set("accessibility", "invisible_solo_oculta", self.cb_invisible_solo_oculta.GetValue())
        config.set("accessibility", "invisible_anunciar_posicion", self.cb_invisible_posicion.GetValue())
        config.set("accessibility", "indicate_image", self.cb_indicate_image.GetValue())
        config.set("accessibility", "indicate_audio", self.cb_indicate_audio.GetValue())
        if hasattr(self, "cb_sonido_dm_buffer"):
            config.set("accessibility", "direct_sound_in_dm_buffer", self.cb_sonido_dm_buffer.GetValue())
        config.set("accessibility", "speech_delay_ms", self.spin_speech_delay.GetValue())
        config.set("accessibility", "auto_read_new", self.cb_auto_read.GetValue())
        # Las plantillas de lectura ya NO se editan aquí: tienen su propio editor accesible
        # (Herramientas → Editor de plantillas de lectura...).
        if hasattr(self, "cb_show_usernames"):
            config.set("accessibility", "show_screen_names", self.cb_show_usernames.GetValue())
            config.set("accessibility", "hide_emojis", self.cb_hide_emojis.GetValue())
            config.set("accessibility", "hide_emojis_names", self.cb_hide_emojis_names.GetValue())
        if hasattr(self, "cb_expand_cw"):
            config.set("accessibility", "expand_cw", self.cb_expand_cw.GetValue())
        if hasattr(self, "cb_colnav_row"):
            config.set("accessibility", "colnav_row_num", self.cb_colnav_row.GetValue())
            config.set("accessibility", "colnav_col_header", self.cb_colnav_header.GetValue())
            config.set("accessibility", "colnav_cell_value", self.cb_colnav_value.GetValue())

        # Mastodon
        if hasattr(self, "cb_mastodon_realtime"):
            config.set("mastodon", "realtime", self.cb_mastodon_realtime.GetValue())
        try:
            interval = int(self.txt_mastodon_interval.GetValue())
            config.set("mastodon", "update_interval", max(10, interval))
        except ValueError:
            pass
        try:
            mst_limit = int(self.txt_mastodon_limit.GetValue())
            config.set("mastodon", "limit_per_load", min(200, max(1, mst_limit)))
        except ValueError:
            pass
        # El combo muestra etiquetas en español pero se guarda el valor interno de Mastodon.
        try:
            config.set("mastodon", "default_visibility",
                       self._visibilidad_valores[self.choice_visibility.GetSelection()])
        except Exception:
            config.set("mastodon", "default_visibility", "public")
        try:
            config.set("mastodon", "reply_visibility_mode",
                       self._reply_vis_modos[self.choice_reply_vis.GetSelection()])
        except Exception:
            pass
        config.set("mastodon", "confirm_post", self.cb_confirm_post.GetValue())
        config.set("mastodon", "alt_text_reminder", self.cb_alt_reminder.GetValue())
        config.set("mastodon", "read_preferences_from_instance", self.cb_read_prefs.GetValue())
        if hasattr(self, "cb_mastodon_admin"):
            config.set("mastodon", "admin_tools", self.cb_mastodon_admin.GetValue())
        config.set("mastodon", "show_details_panel", self.cb_details_panel.GetValue())
        if hasattr(self, "cb_mst_buttons"):
            config.set("mastodon", "show_action_buttons", self.cb_mst_buttons.GetValue())
        config.set("mastodon", "max_items_buffer", int(self.spin_mst_max_buffer.GetValue()))
        config.set("mastodon", "max_notifications", int(self.spin_mst_max_notifs.GetValue()))
        if hasattr(self, "cb_mst_no_persist"):
            config.set("mastodon", "no_conservar_entre_sesiones", self.cb_mst_no_persist.GetValue())

        # Bluesky
        try:
            bsky_interval = int(self.txt_bluesky_interval.GetValue())
            config.set("bluesky", "update_interval", max(10, bsky_interval))
        except ValueError:
            pass
        try:
            bsky_limit = int(self.txt_bluesky_limit.GetValue())
            config.set("bluesky", "limit_per_load", min(100, max(1, bsky_limit)))
        except ValueError:
            pass
        # --- W Social ---
        if hasattr(self, "cb_wsocial_tiempo_real"):
            config.set("wsocial", "tiempo_real", self.cb_wsocial_tiempo_real.GetValue())
            config.set("wsocial", "avisar_mensajes", self.cb_wsocial_avisar_dm.GetValue())
            config.set("wsocial", "alt_text_reminder", self.cb_wsocial_alt.GetValue())
            config.set("wsocial", "confirm_post", self.cb_wsocial_confirm.GetValue())
            config.set("wsocial", "show_details_panel", self.cb_wsocial_detalles.GetValue())
            config.set("wsocial", "show_action_buttons", self.cb_wsocial_botonera.GetValue())
            config.set("wsocial", "no_conservar_entre_sesiones",
                       self.cb_wsocial_no_conservar.GetValue())
            for _clave, _control, _minimo, _maximo in (
                    ("intervalo_tiempo_real", self.txt_wsocial_tr_intervalo, 20, 3600),
                    ("update_interval", self.txt_wsocial_interval, 10, 3600),
                    ("limit_per_load", self.txt_wsocial_limit, 1, 100)):
                try:
                    config.set("wsocial", _clave,
                               min(_maximo, max(_minimo, int(_control.GetValue()))))
                except (TypeError, ValueError):
                    pass
        config.set("bluesky", "alt_text_reminder", self.cb_bluesky_alt.GetValue())
        if hasattr(self, "cb_bluesky_realtime"):
            config.set("bluesky", "realtime", self.cb_bluesky_realtime.GetValue())
        config.set("bluesky", "repost_mode", ["ask", "repost", "quote"][self.choice_bluesky_repost.GetSelection()])
        for _red, _casilla in (("bluesky", "cb_bluesky_mencionar"),
                               ("wsocial", "cb_wsocial_mencionar")):
            if hasattr(self, _casilla):
                config.set(_red, "mencionar_al_responder", getattr(self, _casilla).GetValue())
        # Lectura de reposts y respuestas (Bluesky y W Social).
        for _red, _choice, _casilla in (
                ("bluesky", "choice_bluesky_formato_repost", "cb_bluesky_prefijo_respuesta"),
                ("wsocial", "choice_wsocial_formato_repost", "cb_wsocial_prefijo_respuesta")):
            if hasattr(self, _choice):
                _sel = getattr(self, _choice).GetSelection()
                config.set(_red, "formato_repost",
                           self._FORMATOS_REPOST_AT[_sel] if 0 <= _sel < len(self._FORMATOS_REPOST_AT)
                           else "original")
                config.set(_red, "prefijo_respuesta", getattr(self, _casilla).GetValue())
        config.set("bluesky", "default_reply_control", self._bluesky_reply_vals[self.choice_bluesky_reply.GetSelection()])

        # X (Twitter): solo si el panel se construyó (cliente disponible).
        if hasattr(self, "cb_x_enabled"):
            config.set("x", "enabled", self.cb_x_enabled.GetValue())
            try:
                config.set("x", "limit_per_load", min(100, max(1, int(self.txt_x_limit.GetValue()))))
            except ValueError:
                pass
        if hasattr(self, "cb_x_auto"):
            config.set("x", "auto_update", self.cb_x_auto.GetValue())
        if hasattr(self, "txt_x_interval"):
            try:
                _x_min = int(config.get("x", "min_update_seconds", 120))
                config.set("x", "update_interval", max(_x_min, int(self.txt_x_interval.GetValue())))
            except ValueError:
                pass
        if hasattr(self, "cb_x_alt"):
            config.set("x", "alt_text_reminder", self.cb_x_alt.GetValue())
        if hasattr(self, "cb_x_prefijo_respuesta"):
            config.set("x", "prefijo_respuesta", self.cb_x_prefijo_respuesta.GetValue())
        if hasattr(self, "cb_x_no_persist"):
            config.set("x", "no_conservar_entre_sesiones", self.cb_x_no_persist.GetValue())
        if hasattr(self, "cb_x_details"):
            config.set("x", "show_details_panel", self.cb_x_details.GetValue())
        if hasattr(self, "cb_x_buttons"):
            config.set("x", "show_action_buttons", self.cb_x_buttons.GetValue())
        if hasattr(self, "cb_tg_buttons"):
            config.set("telegram", "show_action_buttons", self.cb_tg_buttons.GetValue())
        if hasattr(self, "txt_irc_history"):
            try:
                config.set("irc", "history_days", max(0, int(self.txt_irc_history.GetValue() or "0")))
            except ValueError:
                pass
        if hasattr(self, "cb_irc_ocultar_eventos"):
            config.set("irc", "ocultar_eventos", self.cb_irc_ocultar_eventos.GetValue())
        if hasattr(self, "txt_matrix_history"):
            try:
                config.set("matrix", "history_days", max(0, int(self.txt_matrix_history.GetValue() or "0")))
            except ValueError:
                pass
        if hasattr(self, "cb_matrix_admin"):
            config.set("matrix", "admin_tools", self.cb_matrix_admin.GetValue())
        if hasattr(self, "txt_xmpp_history"):
            try:
                config.set("xmpp", "history_days", max(0, int(self.txt_xmpp_history.GetValue() or "0")))
            except ValueError:
                pass
        if hasattr(self, "cb_xmpp_admin"):
            config.set("xmpp", "admin_tools", self.cb_xmpp_admin.GetValue())
        if hasattr(self, "cb_xmpp_puentes"):
            config.set("xmpp", "sin_buffers_por_puente", not self.cb_xmpp_puentes.GetValue())
        if hasattr(self, "txt_telegram_interval"):
            try:
                config.set("telegram", "update_interval", max(10, int(self.txt_telegram_interval.GetValue())))
            except ValueError:
                pass
        # Reddit: solo si el panel se construyó (praw instalada).
        if hasattr(self, "txt_reddit_interval"):
            try:
                config.set("reddit", "update_interval", max(30, int(self.txt_reddit_interval.GetValue())))
            except ValueError:
                pass
        if hasattr(self, "txt_reddit_limit"):
            try:
                config.set("reddit", "limit_per_load", min(100, max(1, int(self.txt_reddit_limit.GetValue()))))
            except ValueError:
                pass
        if hasattr(self, "choice_reddit_sort"):
            config.set("reddit", "comment_sort", self._reddit_sort_vals[self.choice_reddit_sort.GetSelection()])
        if hasattr(self, "choice_reddit_time"):
            config.set("reddit", "time_filter", self._reddit_time_vals[self.choice_reddit_time.GetSelection()])
        if hasattr(self, "cb_reddit_nsfw"):
            config.set("reddit", "show_nsfw", self.cb_reddit_nsfw.GetValue())
        if hasattr(self, "cb_reddit_confirm"):
            config.set("reddit", "confirm_post", self.cb_reddit_confirm.GetValue())
        if hasattr(self, "txt_reddit_client_id"):
            config.set("reddit", "client_id", self.txt_reddit_client_id.GetValue().strip())
        if hasattr(self, "cb_reddit_details"):
            config.set("reddit", "show_details_panel", self.cb_reddit_details.GetValue())
        if hasattr(self, "cb_reddit_buttons"):
            config.set("reddit", "show_action_buttons", self.cb_reddit_buttons.GetValue())
        if hasattr(self, "spin_reddit_comment_limit"):
            config.set("reddit", "comment_limit", int(self.spin_reddit_comment_limit.GetValue()))
        if hasattr(self, "spin_reddit_replace_more"):
            config.set("reddit", "replace_more_limit", int(self.spin_reddit_replace_more.GetValue()))
        if hasattr(self, "spin_reddit_max_buffer"):
            config.set("reddit", "max_items_buffer", int(self.spin_reddit_max_buffer.GetValue()))
        if hasattr(self, "spin_reddit_max_msgs"):
            config.set("reddit", "max_notifications", int(self.spin_reddit_max_msgs.GetValue()))
        if hasattr(self, "cb_reddit_no_persist"):
            config.set("reddit", "no_conservar_entre_sesiones", self.cb_reddit_no_persist.GetValue())
        # PeerTube: solo si el panel se construyó (librería disponible).
        if hasattr(self, "txt_peertube_interval"):
            try:
                config.set("peertube", "update_interval", max(30, int(self.txt_peertube_interval.GetValue())))
            except ValueError:
                pass
            try:
                config.set("peertube", "limit_per_load", min(100, max(1, int(self.txt_peertube_limit.GetValue()))))
            except ValueError:
                pass
        if hasattr(self, "choice_peertube_res"):
            config.set("peertube", "preferred_resolution",
                       self._peertube_res_vals[self.choice_peertube_res.GetSelection()])
        if hasattr(self, "txt_peertube_sub"):
            config.set("peertube", "preferred_subtitle_lang",
                       self.txt_peertube_sub.GetValue().strip().lower() or "es")
        if hasattr(self, "cb_peertube_resume"):
            config.set("peertube", "save_position", self.cb_peertube_resume.GetValue())
        if hasattr(self, "txt_peertube_downloads"):
            config.set("peertube", "download_dir", self.txt_peertube_downloads.GetValue().strip())
        if hasattr(self, "choice_peertube_nsfw"):
            config.set("peertube", "nsfw_policy",
                       self._peertube_nsfw_vals[self.choice_peertube_nsfw.GetSelection()])
        if hasattr(self, "choice_peertube_priv"):
            config.set("peertube", "default_privacy",
                       self._peertube_priv_vals[self.choice_peertube_priv.GetSelection()])
        if hasattr(self, "cb_peertube_details"):
            config.set("peertube", "show_details_panel", self.cb_peertube_details.GetValue())
        if hasattr(self, "cb_peertube_buttons"):
            config.set("peertube", "show_action_buttons", self.cb_peertube_buttons.GetValue())
        if hasattr(self, "spin_peertube_max_buffer"):
            config.set("peertube", "max_items_buffer", int(self.spin_peertube_max_buffer.GetValue()))
        if hasattr(self, "cb_peertube_no_persist"):
            config.set("peertube", "no_conservar_entre_sesiones", self.cb_peertube_no_persist.GetValue())
        # Activación/desactivación de servicios. Se aplica al reiniciar (igual que X). El "mínimo
        # un servicio activo" ya se validó (bloqueante) al principio de _on_save, así que aquí solo
        # se aplican las casillas. Se recorre SERVICIOS_CONMUTABLES (fuente única) en vez de una
        # lista escrita a mano: así ninguna red se queda sin guardar al añadirla.
        from app.core.feature_flags import fijar_servicio_habilitado, SERVICIOS_CONMUTABLES
        for _sid in SERVICIOS_CONMUTABLES:
            cb = getattr(self, f"cb_serv_{_sid}", None)
            if cb is not None:
                fijar_servicio_habilitado(_sid, cb.GetValue())
        _pds = self.txt_bluesky_pds.GetValue().strip()
        if _pds:
            config.set("bluesky", "pds_url", _pds)
        config.set("bluesky", "max_items_buffer", int(self.spin_bsky_max_buffer.GetValue()))
        config.set("bluesky", "max_notifications", int(self.spin_bsky_max_notifs.GetValue()))
        if hasattr(self, "cb_bsky_no_persist"):
            config.set("bluesky", "no_conservar_entre_sesiones", self.cb_bsky_no_persist.GetValue())
        if hasattr(self, "cb_bsky_details"):
            config.set("bluesky", "show_details_panel", self.cb_bsky_details.GetValue())
        if hasattr(self, "cb_bsky_buttons"):
            config.set("bluesky", "show_action_buttons", self.cb_bsky_buttons.GetValue())

        # RSS
        try:
            rss_interval = int(self.txt_rss_interval.GetValue())
            # Mínimo 1 minuto (antes 5): quien quiera sus noticias casi al momento, puede.
            config.set("rss", "update_interval", max(1, rss_interval))
        except ValueError:
            pass
        config.set("rss", "download_fulltext", self.cb_fulltext.GetValue())
        self._guardar_rss_articulo()
        config.set("rss", "notifications_enabled", self.cb_rss_notify.GetValue())
        if hasattr(self, "cb_rss_imagenes"):
            config.set("rss", "download_images", self.cb_rss_imagenes.GetValue())
        config.set("rss", "max_parallel_updates", self.spin_rss_parallel.GetValue())
        config.set("rss", "keep_articles_days", self.spin_rss_keep_days.GetValue())
        config.set("rss", "max_articles_per_feed", self.spin_rss_max_feed.GetValue())
        if hasattr(self, "cb_rss_details"):
            config.set("rss", "show_details_panel", self.cb_rss_details.GetValue())
        if hasattr(self, "choice_rss_intro"):
            _i = self.choice_rss_intro.GetSelection()
            config.set("rss", "enter_action",
                       self._rss_intro_vals[_i] if 0 <= _i < len(self._rss_intro_vals) else "navegador")
            config.set("rss", "fulltext_en_vista_previa", self.cb_rss_completo_vista.GetValue())
            config.set("rss", "mark_read_on_open", self.cb_rss_leido_al_abrir.GetValue())
            config.set("rss", "podcast_max_mib", int(self.spin_rss_podcast_mib.GetValue()))
        if hasattr(self, "cb_rss_buttons"):
            config.set("rss", "show_action_buttons", self.cb_rss_buttons.GetValue())
        if hasattr(self, "cb_rss_html_mode"):
            config.set("rss", "default_html_mode", self.cb_rss_html_mode.GetValue())
        if hasattr(self, "cb_rss_tts_auto"):
            config.set("rss", "tts_autostart", self.cb_rss_tts_auto.GetValue())
        if hasattr(self, "cb_rss_tts_pos"):
            config.set("rss", "remember_tts_position", self.cb_rss_tts_pos.GetValue())
        if hasattr(self, "cb_rss_no_persist"):
            config.set("rss", "no_conservar_entre_sesiones", self.cb_rss_no_persist.GetValue())
        if hasattr(self, "cb_rss_corte_limpiar"):
            config.set("rss", "corte_al_limpiar", self.cb_rss_corte_limpiar.GetValue())

        # Telegram
        from app.services.telegram.telegram_service import telegram_service
        from app.core.security import security
        tg_id = self.txt_tg_api_id.GetValue().strip()
        tg_hash = self.txt_tg_api_hash.GetValue().strip()
        if tg_id and tg_hash:
            telegram_service.set_api_credentials(tg_id, tg_hash)
        elif not tg_id and not tg_hash:
            # Ambos en blanco: borrar las credenciales guardadas
            security.borrar_credencial("telegram", "api_id")
            security.borrar_credencial("telegram", "api_hash")
        config.set("telegram", "download_dir", self.txt_tg_downloads.GetValue().strip())
        config.set("telegram", "download_subdirs", self.cb_tg_subdirs.GetValue())
        config.set("telegram", "announce_new", self.cb_tg_announce.GetValue())
        if hasattr(self, "cb_tg_autoplay_voice"):
            config.set("telegram", "autoplay_voice", self.cb_tg_autoplay_voice.GetValue())
        if hasattr(self, "cb_tg_mark_read"):
            config.set("telegram", "mark_read_on_focus", self.cb_tg_mark_read.GetValue())
        if hasattr(self, "cb_tg_unread_marker"):
            config.set("telegram", "show_unread_marker", self.cb_tg_unread_marker.GetValue())
            config.set("telegram", "show_unread_divider", self.cb_tg_unread_divider.GetValue())
        if hasattr(self, "cb_tg_sync_todo"):
            config.set("telegram", "sincronizar_todo_al_abrir", self.cb_tg_sync_todo.GetValue())
        config.set("telegram", "max_messages_per_chat", int(self.spin_max_tg_msgs.GetValue()))

        # Telegram Drive (nube personal)
        if hasattr(self, "txt_tgdrive_downloads"):
            config.set("telegram_drive", "carpeta_descargas", self.txt_tgdrive_downloads.GetValue().strip())
            config.set("telegram_drive", "max_simultaneas", int(self.spin_tgdrive_simult.GetValue()))
            idx_conf = self.choice_tgdrive_conflicto.GetSelection()
            if 0 <= idx_conf < len(self._tgdrive_conf_valores):
                config.set("telegram_drive", "politica_conflicto", self._tgdrive_conf_valores[idx_conf])
            config.set("telegram_drive", "retencion_papelera_dias", int(self.spin_tgdrive_papelera.GetValue()))
            config.set("telegram_drive", "confirmar_borrado", self.cb_tgdrive_confirmar.GetValue())
            config.set("telegram_drive", "limpiar_temporales", self.cb_tgdrive_limpiar.GetValue())
            config.set("telegram_drive", "frecuencia_progreso_seg", int(self.spin_tgdrive_progreso.GetValue()))

        # Micrófono COMPARTIDO (Preferencias → Sonidos → Micrófono): sección 'audio', no 'telegram'.
        if hasattr(self, "slider_mic_vol"):
            config.set("audio", "mic_device", self._mic_seleccionado())
            config.set("audio", "mic_volume", self.slider_mic_vol.GetValue() / 100.0)

        # IA
        if hasattr(self, "_ai_provs"):
            sel = self.choice_ai_prov.GetSelection()
            if 0 <= sel < len(self._ai_provs):
                config.set("ai", "provider", self._ai_provs[sel][0])
        self._guardar_claves_ai()
        config.set("ai", "model", self.combo_ai_model.GetValue().strip() or "gemini-2.5-flash")
        config.set("ai", "local_llama_url", self.txt_local_url.GetValue())
        config.set("ai", "cache_responses", self.cb_ai_cache.GetValue())
        
        # Traducción
        if hasattr(self, "choice_trad_prov") and getattr(self, "_trad_provs", None):
            sel = self.choice_trad_prov.GetSelection()
            if 0 <= sel < len(self._trad_provs):
                config.set("translation", "provider", self._trad_provs[sel][0])
        config.set("translation", "default_target", self.choice_target_lang.GetStringSelection())
        if hasattr(self, "cb_trad_mastodon"):
            config.set("translation", "mastodon_server", self.cb_trad_mastodon.GetValue())
        config.set("translation", "copy_on_translate", self.cb_copy_translation.GetValue())
        config.set("translation", "save_history", self.cb_save_translation_history.GetValue())

        # Reproductores: segundos de avance/retroceso de cada reproductor.
        if hasattr(self, "spin_step_fwd"):
            config.set("media_player", "default_step_forward", float(self.spin_step_fwd.GetValue()))
            config.set("media_player", "default_step_backward", float(self.spin_step_back.GetValue()))
            config.set("media_player", "default_step_forward2", float(self.spin_step_fwd2.GetValue()))
            config.set("media_player", "default_step_backward2", float(self.spin_step_back2.GetValue()))
            config.set("media_player", "rewind_on_resume2", int(self.spin_rebobinar2.GetValue()))
            config.set("media_player", "finish_margin2", int(self.spin_margen2.GetValue()))
            config.set("rss", "mark_read_on_finish", self.cb_leido_al_terminar.GetValue())
            config.set("media_player", "queue_autoplay2", self.cb_cola_auto.GetValue())

        # Proxy (se reaplica al instante a las variables de entorno)
        config.set("proxy", "enabled", self.cb_proxy_enabled.GetValue())
        config.set("proxy", "type", self._proxy_types[self.choice_proxy_type.GetSelection()])
        config.set("proxy", "server", self.txt_proxy_server.GetValue().strip())
        config.set("proxy", "port", int(self.spin_proxy_port.GetValue()))
        config.set("proxy", "user", self.txt_proxy_user.GetValue().strip())
        config.set("proxy", "password", self.txt_proxy_pass.GetValue())
        try:
            from app.core.net import aplicar_configuracion_proxy
            aplicar_configuracion_proxy()
        except Exception:
            pass
        
        # Sonidos
        config.set("audio", "volume", float(self.slider_volume.GetValue() / 100.0))
        config.set("audio", "mute", self.cb_mute.GetValue())
        if hasattr(self, "choice_sound_pack"):
            config.set("audio", "sound_pack", self._paquetes_sonido[self.choice_sound_pack.GetSelection()])
        # Dispositivo de salida (vacío = predeterminado)
        sel_dev = self.choice_output_device.GetSelection()
        if sel_dev <= 0 or sel_dev > len(self._dispositivos_salida):
            config.set("audio", "output_device", "")
        else:
            config.set("audio", "output_device", self._dispositivos_salida[sel_dev - 1])
        # Modo de impulso (boost) y nivel de log y sonido "listo"
        if hasattr(self, "choice_boost_mode"):
            config.set("mastodon", "boost_mode", ["ask", "boost", "quote"][self.choice_boost_mode.GetSelection()])
        if hasattr(self, "cb_show_boosts"):
            config.set("mastodon", "show_boosts", self.cb_show_boosts.GetValue())
        if hasattr(self, "cb_show_replies"):
            config.set("mastodon", "show_replies", self.cb_show_replies.GetValue())
        if hasattr(self, "choice_log_level"):
            config.set("general", "log_level", self.choice_log_level.GetStringSelection())
        if hasattr(self, "choice_spell_lang"):
            config.set("general", "spell_language", self._spell_langs[self.choice_spell_lang.GetSelection()][0])
        if hasattr(self, "cb_ready_sound"):
            config.set("general", "ready_sound", self.cb_ready_sound.GetValue())
        
        # Guardar sonidos individuales deshabilitados
        disabled = []
        for idx, key in enumerate(self.sound_event_keys):
            if not self.clb_sounds.IsChecked(idx):
                disabled.append(key)
        config.set("audio", "disabled_events", disabled)
