# -*- coding: utf-8 -*-
"""Interfaz invisible: teclas GLOBALES del sistema (funcionan aunque la app esté en segundo plano o
minimizada) para manejar TODO SocialRSS sin ventana, como TWBlue y FastSM.

El mapa de teclas y su registro viven en invisible_keymap.py (independiente del editor de atajos de
ventana). Aquí está el DESPACHADOR (_inv_dispatch) y la implementación de cada acción.

Arquitectura (modelo TWBlue): la navegación invisible mueve el foco REAL del árbol y la lista de la
ventana (aunque esté oculta) y locuta con la plantilla de lectura del buffer. Al reutilizar los
helpers de navegación por árbol ya existentes (_cambiar_cuenta, _buffers_de_cuenta,
_ir_a_buffer_nodo, activar servicio) se contempla SIN CÓDIGO EXTRA: la bandeja de entrada, varias
cuentas por red y los servicios activados/desactivados (el árbol solo contiene lo habilitado)."""
import wx

from app.core.speech import speak, notificar
from app.core.config_manager import config
from app.core.database import db
from app.ui.invisible_keymap import invisible_keymap


class _Mf_interfaz_invisible:
    # ------------------------------------------------------------------ instalación
    def _instalar_interfaz_invisible(self):
        """Registra (o quita) TODAS las teclas del mapa invisible según la preferencia. Idempotente;
        se llama al arrancar y al guardar preferencias o el editor de teclas invisibles."""
        activar = config.get("accessibility", "interfaz_invisible", False)
        if activar:
            n = invisible_keymap.registrar_todas(self)
            self._hk_invisibles_registradas = n > 0
            self._quiza_sugerir_mapa_win11()
        else:
            invisible_keymap.desregistrar_todas(self)
            self._hk_invisibles_registradas = False

    def _quiza_sugerir_mapa_win11(self):
        """En Windows 11, si la interfaz invisible está activa pero sigues con el mapa de Windows 10,
        ofrece UNA vez cambiar al mapa de Windows 11 (que evita las combinaciones Windows+Alt que el
        sistema reserva). Como FastSM/TWBlue, que también cambian de mapa al detectar Windows 11."""
        from app.ui import invisible_keymap as ikm
        if config.get("invisible_shortcuts", "win11_sugerido", False):
            return
        if not ikm.es_windows_11():
            return
        if invisible_keymap.perfil() == "win11":
            config.set("invisible_shortcuts", "win11_sugerido", True)  # ya lo usa; no volver a preguntar
            return
        # Deferir para no bloquear el arranque ni el guardado de preferencias.
        wx.CallLater(1500, self._preguntar_mapa_win11)

    def _preguntar_mapa_win11(self):
        config.set("invisible_shortcuts", "win11_sugerido", True)
        from .accessible_controls import confirmar
        r = confirmar(self, _(
            "Estás en Windows 11 y tienes activada la interfaz invisible.\n\n"
            "Windows 11 reserva varias combinaciones con Windows+Alt (por ejemplo, Windows+Alt+K "
            "silencia el micrófono), que pueden pisar los atajos de la interfaz invisible. Por eso "
            "hay un mapa de teclas pensado para Windows 11: lleva la navegación y las acciones a "
            "Ctrl+Alt+Windows y evita esos conflictos.\n\n"
            "¿Quieres usar el mapa de teclas de Windows 11? Podrás cambiarlo cuando quieras en el "
            "editor de teclas invisibles."), _("Mapa de teclas de Windows 11"))
        if r == wx.ID_YES:
            invisible_keymap.set_perfil("win11")
            invisible_keymap.registrar_todas(self)  # re-registrar con el nuevo mapa (en caliente)
            notificar(_("Activado el mapa de teclas de Windows 11 para la interfaz invisible."))

    def _on_editor_teclas_invisibles(self, event):
        """Abre el editor de teclas de la interfaz invisible (independiente del de la app)."""
        from app.ui.invisible_keymap_editor import InvisibleKeymapEditor
        dlg = InvisibleKeymapEditor(self)
        dlg.ShowModal()
        dlg.Destroy()

    # Acciones que funcionan SIEMPRE aunque el modo "solo con la ventana oculta" esté
    # activo: la alternancia (sin ella no podrías volver al modo invisible), el editor de
    # teclas y salir. Todo lo demás queda reservado al modo invisible si así se configura.
    _INV_SIEMPRE_ACTIVAS = ("inv_mostrar_ocultar", "inv_editor_teclas", "inv_salir")

    def _inv_dispatch(self, accion_id: str):
        """Ejecuta la acción invisible. Corre en el hilo de la interfaz (EVT_HOTKEY)."""
        metodo = getattr(self, "_inv_" + accion_id[4:], None) if accion_id.startswith("inv_") else None
        if metodo is None:
            return
        # Modo "solo en la interfaz invisible" (petición de usuarios): con la VENTANA
        # visible, los comandos invisibles no actúan (ahí ya están los atajos normales de
        # la app); solo responden con la ventana oculta. La alternancia siempre funciona.
        if (config.get("accessibility", "invisible_solo_oculta", False)
                and accion_id not in self._INV_SIEMPRE_ACTIVAS):
            try:
                if self.IsShown() and not self.IsIconized():
                    return
            except Exception:
                pass
        try:
            metodo()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Acción invisible {accion_id} falló: {e}")

    # ------------------------------------------------------------------ utilidades
    def _iv_lista(self):
        """Devuelve la lista principal si tiene elementos, o None (con aviso)."""
        try:
            if self.lista_items.GetItemCount() > 0:
                return self.lista_items
        except Exception:
            pass
        return None

    def _iv_locutar_fila(self, idx):
        """Mueve el foco a la fila idx y la locuta con el contenido de sus columnas."""
        lst = self.lista_items
        n = lst.GetItemCount()
        if n == 0:
            speak(_("El buffer está vacío."), interrupt=True)
            return
        idx = max(0, min(idx, n - 1))
        try:
            lst.Focus(idx)
            lst.Select(idx)
        except Exception:
            pass
        # Registrar la posición del usuario en este buffer (como al navegar con el ratón/teclado).
        try:
            claves = self._claves_foco()
            if 0 <= idx < len(claves):
                self._buffer_focus[self._buffer_key()] = claves[idx]
                self._buffer_focus_idx[self._buffer_key()] = idx
        except Exception:
            pass
        # Recalcular el "hace X minutos" ANTES de leer (fallo reportado): la lista solo
        # refresca las fechas con eventos de la ventana visible (foco/viewport), así que
        # con la interfaz invisible se leía el tiempo relativo congelado de cuando se
        # pintó la fila ("hace segundos" en un mensaje de hace una hora).
        try:
            lst._refrescar_fecha(idx)
        except Exception:
            pass
        partes = [lst.texto_celda_completo(idx, c) for c in range(lst.GetColumnCount())]
        texto = ", ".join(p for p in partes if p)
        speak(texto or _("Elemento {0}").format(idx + 1), interrupt=True)

    def _iv_foco_actual(self):
        """Índice de la fila enfocada en la lista, o -1."""
        try:
            r = self.lista_items.GetFocusedItem()
            if r == wx.NOT_FOUND:
                r = self.lista_items.GetFirstSelected()
            return r
        except Exception:
            return -1

    def _iv_sonar_limite(self):
        try:
            from app.core.event_bus import event_bus
            event_bus.emit("sound_trigger", "limit")
        except Exception:
            pass

    # ------------------------------------------------------------------ navegación: elementos
    def _inv_item_prev(self):
        lst = self._iv_lista()
        if not lst:
            speak(_("El buffer está vacío."), interrupt=True)
            return
        idx = self._iv_foco_actual()
        if idx <= 0:
            self._iv_sonar_limite()
            self._iv_locutar_fila(0)
            return
        self._iv_registrar_paso_nav()
        self._iv_locutar_fila(idx - 1)

    def _inv_item_next(self):
        lst = self._iv_lista()
        if not lst:
            speak(_("El buffer está vacío."), interrupt=True)
            return
        idx = self._iv_foco_actual()
        if idx >= lst.GetItemCount() - 1:
            self._iv_sonar_limite()
            self._iv_locutar_fila(lst.GetItemCount() - 1)
            return
        self._iv_registrar_paso_nav()
        self._iv_locutar_fila(idx + 1)

    def _inv_item_first(self):
        if self._iv_lista():
            self._iv_registrar_paso_nav()
            self._iv_locutar_fila(0)

    def _inv_item_last(self):
        lst = self._iv_lista()
        if lst:
            self._iv_registrar_paso_nav()
            self._iv_locutar_fila(lst.GetItemCount() - 1)

    def _inv_item_jump_up(self):
        lst = self._iv_lista()
        if not lst:
            return
        self._iv_registrar_paso_nav()
        self._iv_locutar_fila(max(0, self._iv_foco_actual() - 20))

    def _inv_item_jump_down(self):
        lst = self._iv_lista()
        if not lst:
            return
        self._iv_registrar_paso_nav()
        self._iv_locutar_fila(min(lst.GetItemCount() - 1, self._iv_foco_actual() + 20))

    def _inv_repeat_item(self):
        idx = self._iv_foco_actual()
        if idx >= 0:
            self._iv_locutar_fila(idx)
        else:
            speak(_("No hay ningún elemento enfocado."), interrupt=True)

    # --- unidades de movimiento (reutiliza la lógica de la lista) ---
    def _inv_unit_prev(self):
        lst = self.lista_items
        from app.ui.accessible_controls import _UNIDADES_MOV
        lst._unidad_idx = (lst._unidad_idx - 1) % len(_UNIDADES_MOV)
        speak(_UNIDADES_MOV[lst._unidad_idx]["titulo"](), interrupt=True)

    def _inv_unit_next(self):
        lst = self.lista_items
        from app.ui.accessible_controls import _UNIDADES_MOV
        lst._unidad_idx = (lst._unidad_idx + 1) % len(_UNIDADES_MOV)
        speak(_UNIDADES_MOV[lst._unidad_idx]["titulo"](), interrupt=True)

    def _inv_move_unit_up(self):
        self._iv_mover_unidad("arriba")

    def _inv_move_unit_down(self):
        self._iv_mover_unidad("abajo")

    def _iv_mover_unidad(self, direccion):
        lst = self._iv_lista()
        if not lst:
            return
        idx = self._iv_foco_actual()
        if idx < 0:
            return
        destino = lst._destino_unidad(idx, direccion)
        if destino is None:
            from app.ui.accessible_controls import _UNIDADES_MOV
            self._iv_sonar_limite()
            speak(_("No hay más por {0}.").format(_UNIDADES_MOV[lst._unidad_idx]["titulo"]()), interrupt=True)
        else:
            self._iv_registrar_paso_nav()
            self._iv_locutar_fila(destino)

    # --- deshacer navegación (pila por buffer) ---
    def _iv_registrar_paso_nav(self):
        """Guarda la posición actual en la pila de deshacer del buffer activo."""
        try:
            pila = getattr(self, "_iv_nav_hist", None)
            if pila is None:
                pila = self._iv_nav_hist = {}
            clave = self._buffer_key()
            idx = self._iv_foco_actual()
            if idx >= 0:
                pila.setdefault(clave, []).append(idx)
                # Acotar la pila para no crecer sin fin.
                if len(pila[clave]) > 100:
                    pila[clave] = pila[clave][-100:]
        except Exception:
            pass

    def _inv_undo_nav(self):
        try:
            pila = getattr(self, "_iv_nav_hist", {}).get(self._buffer_key(), [])
        except Exception:
            pila = []
        if not pila:
            self._iv_sonar_limite()
            speak(_("Nada que deshacer."), interrupt=True)
            return
        destino = pila.pop()
        self._iv_locutar_fila(destino)

    # ------------------------------------------------------------------ navegación: buffers/cuentas/servicios
    def _inv_buffer_prev(self):
        self._iv_cambiar_buffer(-1)

    def _inv_buffer_next(self):
        self._iv_cambiar_buffer(1)

    def _iv_cambiar_buffer(self, delta):
        """Va al buffer anterior/siguiente DE LA CUENTA ACTUAL (o de la bandeja). Reutiliza los
        helpers del árbol, así que respeta buffers ocultos, orden y la bandeja."""
        nodo = self._nodo_cuenta_enfocada()
        if not nodo:
            # Puede que estemos en la bandeja u otro nodo suelto: cambiar de servicio en su lugar.
            self._iv_cambiar_servicio(delta)
            return
        buffers = self._buffers_de_cuenta(nodo)
        if not buffers:
            speak(_("Esta cuenta no tiene buffers."), interrupt=True)
            return
        actual = self.tree_nav.GetSelection()
        idx = -1
        for i, (n, _l) in enumerate(buffers):
            if n == actual:
                idx = i
                break
        nuevo = (idx + delta) % len(buffers) if idx >= 0 else 0
        # _ir_a_buffer_nodo anuncia el nombre del buffer y, si está activada la opción (como
        # TWBlue), también la posición: "Inicio, 3 de 120". Siempre disponible con Ctrl+Win+B.
        self._ir_a_buffer_nodo(buffers[nuevo][0], buffers[nuevo][1],
                               con_posicion=self._iv_con_posicion())

    def _iv_con_posicion(self) -> bool:
        """Opción de Preferencias > Accesibilidad: al cambiar de buffer con la interfaz
        invisible, decir también el elemento actual y el total (como TWBlue)."""
        return bool(config.get("accessibility", "invisible_anunciar_posicion", True))

    def _inv_account_prev(self):
        self._cambiar_cuenta(-1, con_posicion=self._iv_con_posicion())

    def _inv_account_next(self):
        self._cambiar_cuenta(1, con_posicion=self._iv_con_posicion())

    def _inv_service_prev(self):
        self._iv_cambiar_servicio(-1)

    def _inv_service_next(self):
        self._iv_cambiar_servicio(1)

    def _iv_cambiar_servicio(self, delta):
        """Salta al servicio (red) anterior/siguiente entre los HABILITADOS presentes en el árbol
        (incluida la bandeja de entrada). El árbol solo contiene los servicios activos, así que los
        desactivados quedan fuera automáticamente."""
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return
        servicios = []  # nodos category_root en orden del árbol
        child, cookie = self.tree_nav.GetFirstChild(root)
        while child.IsOk():
            d = self.tree_nav.GetItemData(child)
            if d and d.get("type") == "category_root":
                servicios.append(child)
            child, cookie = self.tree_nav.GetNextChild(root, cookie)
        if not servicios:
            return
        # Servicio actual: el ancestro category_root del nodo seleccionado.
        actual = self.tree_nav.GetSelection()
        idx = -1
        for i, s in enumerate(servicios):
            nodo = actual
            while nodo and nodo.IsOk():
                if nodo == s:
                    idx = i
                    break
                nodo = self.tree_nav.GetItemParent(nodo)
            if idx >= 0:
                break
        nuevo = (idx + delta) % len(servicios) if idx >= 0 else 0
        destino = servicios[nuevo]
        # Ir a la primera cuenta/buffer del servicio: reusar la activación por servicio.
        d = self.tree_nav.GetItemData(destino)
        serv = d.get("service") if d else None
        if serv == "inbox":
            self.tree_nav.EnsureVisible(destino)
            self.tree_nav.SelectItem(destino)
            self.tree_nav.SetFocus()
            texto = _("Bandeja de entrada")
            if self._iv_con_posicion():
                texto = self._con_posicion_buffer(texto)
            speak(texto, interrupt=True)
            wx.CallLater(60, self._iv_enfocar_lista_si_hay)
            return
        # Buscar la primera cuenta bajo ese servicio y su primer buffer.
        cuenta, ck = self.tree_nav.GetFirstChild(destino)
        if cuenta.IsOk():
            buffers = self._buffers_de_cuenta(cuenta)
            if buffers:
                self._ir_a_buffer_nodo(buffers[0][0], buffers[0][1],
                                       anuncio=_("{0}. {1}").format(self.tree_nav.GetItemText(destino),
                                                                    buffers[0][1]),
                                       con_posicion=self._iv_con_posicion())
                return
            self.tree_nav.EnsureVisible(cuenta)
            self.tree_nav.SelectItem(cuenta)
            self.tree_nav.SetFocus()
            speak(self.tree_nav.GetItemText(destino), interrupt=True)
        else:
            self.tree_nav.EnsureVisible(destino)
            self.tree_nav.SelectItem(destino)
            self.tree_nav.SetFocus()
            speak(self.tree_nav.GetItemText(destino), interrupt=True)

    def _iv_enfocar_lista_si_hay(self):
        try:
            if self.lista_items.GetItemCount() > 0:
                self.lista_items.SetFocus()
                self._iv_locutar_fila(self._iv_foco_actual() if self._iv_foco_actual() >= 0 else 0)
        except Exception:
            pass

    def _iv_anunciar_buffer(self):
        """Anuncia el buffer activo y la posición (X de N), como FastSM."""
        try:
            n = self.lista_items.GetItemCount()
            titulo = self.lbl_buffer_titulo.GetLabel() if hasattr(self, "lbl_buffer_titulo") else ""
        except Exception:
            n, titulo = 0, ""
        idx = self._iv_foco_actual()
        pos = _("{0} de {1}").format(idx + 1, n) if (idx >= 0 and n) else (_("{0} elementos").format(n) if n else _("vacío"))
        speak(f"{titulo}. {pos}".strip(". "), interrupt=True)

    def _inv_say_position(self):
        self._iv_anunciar_buffer()

    def _inv_say_account(self):
        serv = self._servicio_activo()
        nombres = {"mastodon": "Mastodon", "bluesky": "Bluesky", "telegram": "Telegram",
                   "rss": "RSS", "x": "X", "irc": "IRC", "matrix": "Matrix", "xmpp": "XMPP"}
        if not serv:
            if getattr(self, "_bandeja_activa", False) or self.active_timeline_type == "inbox":
                speak(_("Bandeja de entrada"), interrupt=True)
            else:
                speak(_("Ningún servicio activo."), interrupt=True)
            return
        nodo = self._nodo_cuenta_enfocada()
        cuenta = self.tree_nav.GetItemText(nodo) if nodo else ""
        speak(_("{0}, cuenta {1}").format(nombres.get(serv, serv), cuenta).strip(", "), interrupt=True)

    # ------------------------------------------------------------------ acciones sobre el elemento
    def _iv_asegurar_foco_lista(self):
        """Antes de una acción, asegurar que la lista tiene el foco lógico en un elemento (los
        despachadores _disp_* leen el elemento enfocado de la lista)."""
        idx = self._iv_foco_actual()
        if idx < 0 and self.lista_items.GetItemCount() > 0:
            self.lista_items.Focus(0)
            self.lista_items.Select(0)
            idx = 0
        return idx >= 0

    def _inv_responder(self):
        if self._iv_asegurar_foco_lista():
            self._disp_responder(None)

    def _inv_like(self):
        if self._iv_asegurar_foco_lista():
            self._disp_boost_like(None)

    def _inv_boost(self):
        # Impulsar: en Mastodon/Bluesky/X es la acción de impulso del elemento enfocado.
        if not self._iv_asegurar_foco_lista():
            return
        db_id = self._bluesky_db_id_enfocado() if hasattr(self, "_bluesky_db_id_enfocado") else None
        if self.active_mastodon_account_id and db_id:
            self._on_impulsar_toot(db_id)
        elif self.active_bluesky_account_id and db_id:
            self.bluesky_ctrl.repostear(db_id)
        elif self.active_x_account_id and self.x_ctrl and db_id:
            tid = self.x_ctrl._tweet_id(db_id)
            if tid:
                self.x_ctrl.accion(tid, "retweet")
        else:
            speak(_("Impulsar solo está disponible en Mastodon, Bluesky y X."), interrupt=True)

    def _inv_citar(self):
        if self._iv_asegurar_foco_lista():
            self._disp_citar(None)

    def _inv_ver(self):
        if not self._iv_asegurar_foco_lista():
            return
        idx = self._iv_foco_actual()
        if 0 <= idx < len(self.lista_items_db_ids) and self.active_mastodon_account_id:
            self._on_ver_publicacion(self.lista_items_db_ids[idx], idx)
        else:
            self._disp_ver_texto_plano(None)

    def _inv_ver_conv(self):
        if self._iv_asegurar_foco_lista():
            self._disp_ver_conversacion(None)

    def _inv_url(self):
        self._inv_open_browser()

    def _inv_open_browser(self):
        """Abre en el navegador la publicación (Mastodon/Bluesky/X) o el artículo (RSS) enfocado."""
        if not self._iv_asegurar_foco_lista():
            return
        idx = self._iv_foco_actual()
        db_id = self.lista_items_db_ids[idx] if 0 <= idx < len(self.lista_items_db_ids) else None
        if not db_id:
            return
        if self.active_mastodon_account_id and hasattr(self, "_on_abrir_toot_navegador"):
            self._on_abrir_toot_navegador(db_id)
        elif self.active_rss_account_id:
            row = db.query_one("SELECT link FROM rss_articles WHERE id = ?", (db_id,))
            if row and row[0]:
                from app.core.privacy import abrir_url, limpiar_url
                abrir_url(limpiar_url(row[0]))
            else:
                speak(_("Este artículo no tiene enlace."), interrupt=True)
        elif getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            self.reddit_ctrl.abrir_en_navegador(db_id)
        else:
            # Bluesky / X: usa el menú contextual (Ctrl+Win+Alt+M) para abrir en el navegador.
            speak(_("Para abrir en el navegador, usa el menú contextual del elemento."), interrupt=True)

    def _inv_audio(self):
        if not self._iv_asegurar_foco_lista():
            return
        idx = self._iv_foco_actual()
        if 0 <= idx < len(self.lista_items_db_ids):
            db_id = self.lista_items_db_ids[idx]
            if self.active_mastodon_account_id and hasattr(self, "_on_reproducir_media_mastodon"):
                self._on_reproducir_media_mastodon(db_id)
                return
        speak(_("Aquí no hay audio o vídeo para reproducir."), interrupt=True)

    def _inv_copiar(self):
        if not self._iv_asegurar_foco_lista():
            return
        idx = self._iv_foco_actual()
        db_id = self.lista_items_db_ids[idx] if 0 <= idx < len(self.lista_items_db_ids) else None
        if not db_id:
            return
        if self.active_rss_account_id and hasattr(self, "_on_copiar_enlace_rss"):
            self._on_copiar_enlace_rss(db_id)
        elif hasattr(self, "_on_copiar_enlace_toot"):
            self._on_copiar_enlace_toot(db_id)

    def _inv_traducir(self):
        if self._iv_asegurar_foco_lista() and hasattr(self, "_on_traducir_seleccionado"):
            self._on_traducir_seleccionado(None)

    def _inv_ocr(self):
        if self._iv_asegurar_foco_lista() and hasattr(self, "_on_ocr_imagen_ia"):
            self._on_ocr_imagen_ia(None)

    def _inv_describir(self):
        if self._iv_asegurar_foco_lista() and hasattr(self, "_on_describir_imagen_ia"):
            self._on_describir_imagen_ia(None)

    def _inv_seguir(self):
        if not self._iv_asegurar_foco_lista():
            return
        idx = self._iv_foco_actual()
        if 0 <= idx < len(self.lista_items_db_ids) and self.active_mastodon_account_id \
                and hasattr(self, "_on_seguir_autor"):
            self._on_seguir_autor(self.lista_items_db_ids[idx])
        else:
            speak(_("Seguir al autor está disponible en Mastodon."), interrupt=True)

    def _inv_rss_leido(self):
        if self.active_rss_account_id and hasattr(self, "_atajo_rss_articulo"):
            self._atajo_rss_articulo("toggle_read")
        else:
            speak(_("Esto solo aplica a RSS."), interrupt=True)

    def _inv_rss_lector(self):
        if self.active_rss_account_id and hasattr(self, "_atajo_rss_ver_visor"):
            self._con_ventana_temporal(self._atajo_rss_ver_visor)
        else:
            speak(_("Esto solo aplica a RSS."), interrupt=True)

    def _inv_context_menu(self):
        """Abre el menú contextual REAL del elemento (muestra la ventana y lo despliega).
        Si estabas en modo invisible, al cerrarse el menú (y el diálogo que abriera su
        acción, que es modal) la ventana vuelve a ocultarse sola."""
        if not self._iv_asegurar_foco_lista():
            return
        estaba_invisible = (not self.IsShown()) or self.IsIconized()
        self._mostrar_ventana()
        try:
            self.lista_items.SetFocus()

            def _menu_y_ocultar():
                try:
                    self._on_context_menu(None)  # PopupMenu es síncrono: al volver, cerrado
                finally:
                    if estaba_invisible:
                        wx.CallAfter(self._ocultar_ventana_invisible)
            wx.CallAfter(_menu_y_ocultar)
        except Exception:
            pass

    # ------------------------------------------------------------------ aplicación
    def _mostrar_ventana(self):
        try:
            self.Iconize(False)
            self.Show(True)
            self.Raise()
        except Exception:
            pass

    def _ocultar_ventana_invisible(self):
        """Vuelve a esconder la ventana (modo invisible), respetando la preferencia de
        bandeja del sistema. No anuncia nada: el usuario nunca salió del modo invisible."""
        try:
            if config.get("general", "minimize_to_tray", False):
                self.Iconize(True)
            else:
                self.Hide()
        except Exception:
            pass

    def _con_ventana_temporal(self, accion):
        """Ejecuta una acción que necesita la ventana (compositor, diálogo, menú) y, si
        ANTES estabas en modo invisible, la vuelve a OCULTAR al terminar (fallo reportado:
        publicar desde la interfaz invisible dejaba la ventana en primer plano y había que
        esconderla a mano). Los diálogos implicados son modales, así que al volver de la
        acción ya están cerrados; el re-ocultado va con CallAfter para dejar que el cierre
        del diálogo termine de procesarse."""
        estaba_invisible = (not self.IsShown()) or self.IsIconized()
        self._mostrar_ventana()
        try:
            accion()
        finally:
            if estaba_invisible:
                wx.CallAfter(self._ocultar_ventana_invisible)

    def _inv_mostrar_ocultar(self):
        """Alterna entre el MODO VENTANA y el MODO INVISIBLE (como TWBlue): oculta la
        ventana del todo dejando solo las teclas invisibles, y viceversa. Los compositores
        y diálogos siguen abriendo su ventana al invocarlos desde el modo invisible."""
        try:
            if self.IsShown() and not self.IsIconized():
                self.Iconize(True) if config.get("general", "minimize_to_tray", False) else self.Hide()
                speak(_("Modo invisible. La ventana queda oculta; la misma tecla la trae de vuelta."),
                      interrupt=True)
            else:
                self._mostrar_ventana()
                speak(_("Modo ventana."), interrupt=True)
        except Exception:
            pass

    def _inv_nuevo(self):
        self._con_ventana_temporal(lambda: self._disp_nuevo(None))

    def _inv_publicar_rapido(self):
        self._con_ventana_temporal(lambda: self._disp_nuevo(None))

    def _inv_crosspost(self):
        if hasattr(self, "_on_crosspost"):
            self._con_ventana_temporal(lambda: self._on_crosspost(None))

    def _inv_actualizar(self):
        if hasattr(self, "_on_actualizar_buffer_actual"):
            self._on_actualizar_buffer_actual(None)

    def _inv_cargar_mas(self):
        if hasattr(self, "_on_cargar_mas_toots"):
            self._on_cargar_mas_toots(None)

    def _inv_preferencias(self):
        if hasattr(self, "_on_preferencias"):
            self._con_ventana_temporal(lambda: self._on_preferencias(None))

    def _inv_editor_teclas(self):
        self._con_ventana_temporal(lambda: self._on_editor_teclas_invisibles(None))

    def _inv_salir(self):
        try:
            self.Close()
        except Exception:
            pass

    # ------------------------------------------------------------------ novedades
    def _inv_ultima_mencion(self):
        try:
            fila = db.query_one(
                "SELECT n.from_username, s.content FROM mastodon_notifications n "
                "LEFT JOIN mastodon_statuses s ON s.status_id = n.status_id "
                "WHERE n.type = 'mention' ORDER BY n.created_at DESC LIMIT 1")
            if not fila:
                speak(_("No tienes menciones recientes."), interrupt=True)
                return
            from app.core.html_utils import html_a_texto
            speak(_("Última mención de {0}: {1}").format(
                fila[0] or "", html_a_texto(fila[1] or "")[:400]), interrupt=True)
        except Exception:
            speak(_("No se pudo leer la última mención."), interrupt=True)

    def _inv_siguiente_no_leido(self):
        try:
            fila = db.query_one(
                "SELECT f.title, a.title FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
                "WHERE COALESCE(a.read,0)=0 AND COALESCE(a.deleted,0)=0 "
                "ORDER BY a.published_at DESC LIMIT 1")
            if not fila:
                speak(_("No tienes artículos sin leer."), interrupt=True)
                return
            speak(_("Sin leer, de {0}: {1}").format(fila[0] or "", fila[1] or ""), interrupt=True)
        except Exception:
            speak(_("No se pudo consultar los artículos sin leer."), interrupt=True)

    def _inv_que_suena(self):
        try:
            from app.services.media_player.player2_service import player2_service
            if player2_service.esta_activo():
                speak(player2_service.descripcion_actual() or _("Reproduciendo audio."), interrupt=True)
            else:
                speak(_("No se está reproduciendo nada."), interrupt=True)
        except Exception:
            speak(_("No se pudo consultar el reproductor."), interrupt=True)

    def _inv_ponte_al_dia(self):
        if hasattr(self, "_on_ponte_al_dia"):
            self._on_ponte_al_dia(None)

    def _inv_centro_notif(self):
        if hasattr(self, "_on_centro_notificaciones"):
            self._con_ventana_temporal(lambda: self._on_centro_notificaciones(None))

    def _inv_contadores(self):
        """Dice los contadores globales: menciones sin leer, artículos RSS sin leer, chats Telegram."""
        partes = []
        try:
            m = (db.query_one("SELECT COUNT(*) FROM mastodon_notifications WHERE type='mention'") or [0])[0]
            if m:
                partes.append(_("{0} menciones").format(m))
        except Exception:
            pass
        try:
            r = (db.query_one("SELECT COUNT(*) FROM rss_articles WHERE COALESCE(read,0)=0 AND COALESCE(deleted,0)=0") or [0])[0]
            if r:
                partes.append(_("{0} artículos RSS sin leer").format(r))
        except Exception:
            pass
        try:
            t = (db.query_one("SELECT COUNT(*) FROM telegram_chats WHERE COALESCE(unread_count,0)>0") or [0])[0]
            if t:
                partes.append(_("{0} chats de Telegram con mensajes").format(t))
        except Exception:
            pass
        speak(". ".join(partes) if partes else _("No hay novedades pendientes."), interrupt=True)

    def _inv_leer_rafaga(self):
        """Lee seguidos los primeros elementos del buffer (lectura de ráfaga)."""
        lst = self._iv_lista()
        if not lst:
            speak(_("El buffer está vacío."), interrupt=True)
            return
        n = min(5, lst.GetItemCount())
        textos = []
        for i in range(n):
            partes = [lst.texto_celda_completo(i, c) for c in range(lst.GetColumnCount())]
            textos.append(", ".join(p for p in partes if p))
        speak(". ".join(t for t in textos if t), interrupt=True)
