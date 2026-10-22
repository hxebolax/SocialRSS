# -*- coding: utf-8 -*-
"""Paneles de preferencias: IA, traducción, sonidos, proxy y reproductores (mixin).
"""
import wx
from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.services.audio.sound_generator import generar_todos_los_sonidos_predeterminados
from .accessible_controls import AccessibleSlider, AccessibleTextCtrl, AccessibleCheckListBox


class _PrefAvanzadoMixin:
    def _crear_panel_ai(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        self._ai_panel = panel
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Proveedor activo (Gemini, OpenAI, DeepSeek, xAI, OpenRouter, Groq, Mistral, Claude, local...).
        from app.services.ai.ai_service import ai_service
        self._ai_provs = ai_service.proveedores_disponibles()  # [(clave, etiqueta, necesita_claves)]
        sizer.Add(wx.StaticText(panel, label=_("Proveedor activo de Inteligencia Artificial:")), 0, wx.ALL, 5)
        self.choice_ai_prov = wx.Choice(panel, choices=[et for _k, et, _n in self._ai_provs],
                                        name=_("Selector de proveedor de IA"))
        actual = config.get("ai", "provider", "gemini")
        self.choice_ai_prov.SetSelection(next((i for i, (k, _e, _n) in enumerate(self._ai_provs) if k == actual), 0))
        self.choice_ai_prov.Bind(wx.EVT_CHOICE, lambda e: self._cambiar_proveedor_ia())
        sizer.Add(self.choice_ai_prov, 0, wx.EXPAND | wx.ALL, 5)

        # --- CLAVES API (genérico, una caja para el proveedor seleccionado) ---
        # Cargamos en memoria las claves de cada proveedor; al cambiar de proveedor se vuelca la caja.
        self._ai_claves_mem = {}
        for k, _e, necesita in self._ai_provs:
            if necesita:
                guardadas = config.get("ai", f"{k}_api_keys", [])
                if not isinstance(guardadas, list):
                    guardadas = [s for s in str(guardadas or "").splitlines() if s]
                if k == "gemini" and not guardadas:
                    unica = config.get("ai", "gemini_api_key", "")
                    if unica:
                        guardadas = [unica]
                self._ai_claves_mem[k] = list(guardadas)

        self._caja_claves = wx.StaticBoxSizer(wx.VERTICAL, panel, _("Claves API"))
        cont_k = self._caja_claves.GetStaticBox()
        self.lbl_ai_keys = wx.StaticText(cont_k, label=_("Claves API (una por línea; se rotan al agotarse):"))
        self._caja_claves.Add(self.lbl_ai_keys, 0, wx.ALL, 5)
        self.txt_ai_keys = AccessibleTextCtrl(
            cont_k, value="", style=wx.TE_MULTILINE, name=_("Claves API del proveedor"),
            ayuda_texto=_("Una clave por línea. Cuando una agote su cuota, se usa la siguiente automáticamente."))
        self._caja_claves.Add(self.txt_ai_keys, 0, wx.EXPAND | wx.ALL, 5)

        self._caja_claves.Add(wx.StaticText(cont_k, label=_("Modelo:")), 0, wx.ALL, 5)
        self.combo_ai_model = wx.ComboBox(cont_k, value=config.get("ai", "model", "gemini-2.5-flash"),
                                          name=_("Modelo de IA"))
        self._caja_claves.Add(self.combo_ai_model, 0, wx.EXPAND | wx.ALL, 5)
        self.btn_obtener_modelos = wx.Button(cont_k, label=_("&Obtener modelos disponibles"),
                                             name=_("Obtener la lista de modelos del proveedor"))
        self.btn_obtener_modelos.Bind(wx.EVT_BUTTON, self._on_obtener_modelos)
        self._caja_claves.Add(self.btn_obtener_modelos, 0, wx.ALL, 5)
        sizer.Add(self._caja_claves, 0, wx.EXPAND | wx.ALL, 5)
        self._ai_prov_cargado = None  # proveedor cuyas claves están en la caja ahora

        # --- GRUPO LLAMA LOCAL ---
        self._caja_llama = wx.StaticBoxSizer(wx.VERTICAL, panel, _("Servidor local (llama.cpp / Ollama)"))
        cont_l = self._caja_llama.GetStaticBox()
        self._caja_llama.Add(wx.StaticText(cont_l, label=_("URL del servidor local:")), 0, wx.ALL, 5)
        self.txt_local_url = AccessibleTextCtrl(
            cont_l, value=config.get("ai", "local_llama_url", "http://localhost:8080/v1"),
            name=_("Endpoint de Llama local"),
            ayuda_texto=_("Dirección HTTP local compatible con la API de chat/completions de OpenAI.")
        )
        self._caja_llama.Add(self.txt_local_url, 0, wx.EXPAND | wx.ALL, 5)
        sizer.Add(self._caja_llama, 0, wx.EXPAND | wx.ALL, 5)

        # Caché de respuestas
        self.cb_ai_cache = wx.CheckBox(panel, label=_("Guardar en caché las respuestas de IA"), name=_("Caché de IA"))
        self.cb_ai_cache.SetValue(config.get("ai", "cache_responses", True))
        sizer.Add(self.cb_ai_cache, 0, wx.ALL, 8)

        # Gestor de prompts
        btn_prompts = wx.Button(panel, label=_("&Gestor de prompts..."), name=_("Abrir el gestor de prompts de IA"))
        btn_prompts.Bind(wx.EVT_BUTTON, lambda e: self._on_gestor_prompts())
        sizer.Add(btn_prompts, 0, wx.ALL, 5)

        panel.SetSizer(sizer)
        self._cambiar_proveedor_ia()
        return panel

    def _ai_prov_actual(self):
        """(clave, etiqueta, necesita_claves) del proveedor de IA seleccionado."""
        i = self.choice_ai_prov.GetSelection()
        return self._ai_provs[i] if 0 <= i < len(self._ai_provs) else self._ai_provs[0]

    def _volcar_claves_caja(self):
        """Guarda en memoria lo escrito en la caja de claves para el proveedor que estaba cargado."""
        if self._ai_prov_cargado and self._ai_prov_cargado in self._ai_claves_mem:
            self._ai_claves_mem[self._ai_prov_cargado] = [
                k.strip() for k in self.txt_ai_keys.GetValue().splitlines() if k.strip()]

    def _cambiar_proveedor_ia(self):
        """Muestra los ajustes del proveedor seleccionado y carga sus claves en la caja."""
        self._volcar_claves_caja()
        clave, _et, necesita = self._ai_prov_actual()
        # Caja de claves: visible solo para proveedores que las necesitan.
        self._caja_claves.GetStaticBox().Show(necesita)
        self._caja_claves.ShowItems(necesita)
        if necesita:
            self.txt_ai_keys.SetValue("\n".join(self._ai_claves_mem.get(clave, [])))
            self._ai_prov_cargado = clave
        else:
            self._ai_prov_cargado = None
        # Caja del servidor local: solo para local_llama.
        self._caja_llama.GetStaticBox().Show(clave == "local_llama")
        self._caja_llama.ShowItems(clave == "local_llama")
        self._ai_panel.Layout()

    def _on_obtener_modelos(self, event):
        clave, etiqueta, necesita = self._ai_prov_actual()
        if not necesita:
            speak(_("Este proveedor no permite obtener modelos."), interrupt=True)
            return
        # Guardar las claves del proveedor seleccionado para poder consultar.
        self._volcar_claves_caja()
        config.set("ai", f"{clave}_api_keys", self._ai_claves_mem.get(clave, []))
        if clave == "gemini":
            config.set("ai", "gemini_api_key", (self._ai_claves_mem.get("gemini") or [""])[0])
        speak(_("Obteniendo modelos de {0}...").format(etiqueta), interrupt=True)
        from app.core.task_manager import task_manager
        from app.services.ai.ai_service import ai_service

        def _bg(ctx):
            return ai_service.listar_modelos(clave)

        def _finish(res, err):
            if err:
                speak(_("No se pudieron obtener los modelos: {0}").format(err), interrupt=True)
                return
            actual = self.combo_ai_model.GetValue()
            self.combo_ai_model.SetItems(res or [])
            if actual:
                self.combo_ai_model.SetValue(actual)
            speak(_("{0} modelos disponibles.").format(len(res or [])), interrupt=True)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _guardar_claves_ai(self):
        """Persiste las claves de TODOS los proveedores de IA (cada uno en ai.<prov>_api_keys)."""
        self._volcar_claves_caja()
        for clave, _et, necesita in self._ai_provs:
            if necesita:
                lista = self._ai_claves_mem.get(clave, [])
                config.set("ai", f"{clave}_api_keys", lista)
                if clave == "gemini":
                    config.set("ai", "gemini_api_key", lista[0] if lista else "")

    def _on_gestor_prompts(self):
        from .ai_dialogs import PromptManagerDialog
        dlg = PromptManagerDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _crear_panel_translation(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Motor de traducción (todos GRATUITOS, sin clave). El usuario elige cuál usar.
        sizer.Add(wx.StaticText(panel, label=_("Motor de traducción (gratuitos, sin clave):")), 0, wx.ALL, 5)
        from app.services.translation.translation_service import translation_service
        self._trad_provs = translation_service.proveedores_disponibles()
        self.choice_trad_prov = wx.Choice(panel, choices=[e for _k, e in self._trad_provs],
                                          name=_("Selector de motor de traducción"))
        actual = config.get("translation", "provider", "google_api_free")
        idx = next((i for i, (k, _e) in enumerate(self._trad_provs) if k == actual), 0)
        self.choice_trad_prov.SetSelection(idx)
        self.choice_trad_prov.SetToolTip(_(
            "Todos son gratuitos y no necesitan clave. Si uno te falla (por límites del servidor), "
            "prueba con otro."))
        sizer.Add(self.choice_trad_prov, 0, wx.EXPAND | wx.ALL, 5)

        # Traducción NATIVA de Mastodon (auditoría N1): si la instancia la tiene habilitada,
        # se usa antes que el motor externo (el texto no sale hacia terceros elegidos por
        # la app; lo traduce el proveedor configurado por TU servidor). El motor de arriba
        # queda como respaldo.
        self.cb_trad_mastodon = wx.CheckBox(
            panel,
            label=_("En Mastodon, usar primero la traducción del propio servidor (si tu instancia la ofrece)"),
            name=_("Traducción nativa de Mastodon"))
        self.cb_trad_mastodon.SetValue(config.get("translation", "mastodon_server", True))
        self.cb_trad_mastodon.SetToolTip(_(
            "Tu instancia traduce con su propio proveedor (p. ej. DeepL o LibreTranslate del servidor) "
            "y se te dice cuál fue. Si tu servidor no tiene traducción o falla, se usa el motor de arriba."))
        sizer.Add(self.cb_trad_mastodon, 0, wx.ALL, 10)

        # Idioma destino por defecto
        sizer.Add(wx.StaticText(panel, label=_("Idioma destino predeterminado para traducción:")), 0, wx.ALL, 5)
        self.choice_target_lang = wx.Choice(
            panel, 
            choices=["es", "en", "fr", "pt", "de", "it"], 
            name=_("Selector de idioma destino")
        )
        self.choice_target_lang.SetStringSelection(config.get("translation", "default_target", "es"))
        sizer.Add(self.choice_target_lang, 0, wx.EXPAND | wx.ALL, 5)
        
        # Copiar automáticamente al portapapeles
        self.cb_copy_translation = wx.CheckBox(
            panel, 
            label=_("Copiar traducción automáticamente al portapapeles"), 
            name=_("Copiar traducción")
        )
        self.cb_copy_translation.SetValue(config.get("translation", "copy_on_translate", True))
        sizer.Add(self.cb_copy_translation, 0, wx.ALL, 10)
        
        # Historial de traducciones
        self.cb_save_translation_history = wx.CheckBox(
            panel, 
            label=_("Guardar traducciones en el historial local"), 
            name=_("Persistencia de traducciones")
        )
        self.cb_save_translation_history.SetValue(config.get("translation", "save_history", True))
        sizer.Add(self.cb_save_translation_history, 0, wx.ALL, 10)
        
        panel.SetSizer(sizer)
        return panel

    def _crear_panel_sounds(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Volumen
        sizer.Add(wx.StaticText(panel, label=_("Volumen general de sonidos de interacción:")), 0, wx.ALL, 5)
        vol_actual = int(config.get("audio", "volume", 0.8) * 100)
        self.slider_volume = AccessibleSlider(
            panel, 
            value=vol_actual, 
            minValue=0, 
            maxValue=100,
            name=_("Volumen de sonidos"),
            unidad="%"
        )
        sizer.Add(self.slider_volume, 0, wx.EXPAND | wx.ALL, 5)
        
        # Mute global
        self.cb_mute = wx.CheckBox(
            panel,
            label=_("Silenciar todos los sonidos (Mute)"),
            name=_("Silenciar sonidos")
        )
        self.cb_mute.SetValue(config.get("audio", "mute", False))
        sizer.Add(self.cb_mute, 0, wx.ALL, 10)

        # Paquete de sonidos
        sizer.Add(wx.StaticText(panel, label=_("Paquete de sonidos:")), 0, wx.ALL, 5)
        try:
            from app.services.audio.sound_manager import listar_paquetes_sonido
            self._paquetes_sonido = listar_paquetes_sonido()
        except Exception:
            self._paquetes_sonido = ["default"]
        self.choice_sound_pack = wx.Choice(panel, choices=self._paquetes_sonido, name=_("Paquete de sonidos"))
        pack_actual = config.get("audio", "sound_pack", "default")
        self.choice_sound_pack.SetSelection(self._paquetes_sonido.index(pack_actual) if pack_actual in self._paquetes_sonido else 0)
        self.choice_sound_pack.SetToolTip(_("Crea una carpeta en data/sounds con tus propios WAV para añadir paquetes."))
        sizer.Add(self.choice_sound_pack, 0, wx.EXPAND | wx.ALL, 5)

        # Dispositivo de salida de audio
        sizer.Add(wx.StaticText(panel, label=_("Dispositivo de salida de audio:")), 0, wx.ALL, 5)
        try:
            from app.services.audio.sound_manager import listar_dispositivos_salida
            self._dispositivos_salida = listar_dispositivos_salida()
        except Exception:
            self._dispositivos_salida = []
        opciones_dev = [_("Predeterminado del sistema")] + self._dispositivos_salida
        self.choice_output_device = wx.Choice(panel, choices=opciones_dev, name=_("Dispositivo de salida de audio"))
        actual_dev = config.get("audio", "output_device", "")
        if actual_dev in self._dispositivos_salida:
            self.choice_output_device.SetSelection(self._dispositivos_salida.index(actual_dev) + 1)
        else:
            self.choice_output_device.SetSelection(0)
        self.choice_output_device.SetToolTip(_("Cambiar el dispositivo requiere reiniciar la aplicación."))
        sizer.Add(self.choice_output_device, 0, wx.EXPAND | wx.ALL, 5)
        
        # Lista de sonidos individuales
        sizer.Add(wx.StaticText(panel, label=_("Activar o desactivar sonidos individuales:")), 0, wx.ALL, 5)
        
        self.sound_event_keys = [
            "app_start", "app_exit", "focus_changed", "buffer_changed", 
            "connection_ok", "connection_error", "mastodon_new_status", 
            "mastodon_new_mention", "mastodon_new_notification", "mastodon_error", 
            "rss_new_article", "rss_error", "ai_done", "ai_error", 
            "translation_done", "translation_error", "thread_posted", "thread_error", 
            "draft_saved", "operation_done", "warning", "error", "player_opened",
            "status_has_media", "status_direct", "status_has_cw", "status_has_poll",
            "bluesky_new_status", "bluesky_new_mention", "bluesky_new_notification",
            "bluesky_dm", "bluesky_error", "telegram_new_message",
            "irc_new_message", "irc_highlight", "irc_dm",
            "matrix_new_message", "matrix_highlight", "matrix_dm",
            "wsocial_new_status", "wsocial_new_mention", "wsocial_new_notification",
            "wsocial_new_dm", "wsocial_error"
        ]
        
        descriptions = [
            _("Inicio de aplicación"),
            _("Salida de aplicación"),
            _("Cambio de enfoque"),
            _("Cambio de buffer / timeline"),
            _("Conexión establecida"),
            _("Error de conexión"),
            _("Nuevo status en Mastodon"),
            _("Nueva mención en Mastodon"),
            _("Nueva notificación en Mastodon"),
            _("Error de Mastodon"),
            _("Nuevo artículo RSS"),
            _("Error de RSS"),
            _("Operación de IA finalizada"),
            _("Error de IA"),
            _("Traducción finalizada"),
            _("Error de traducción"),
            _("Hilo publicado"),
            _("Error de publicación de hilo"),
            _("Borrador guardado"),
            _("Operación completada"),
            _("Advertencia"),
            _("Error general"),
            _("Reproductor multimedia abierto"),
            _("Publicación con multimedia"),
            _("Mensaje directo recibido"),
            _("Publicación con CW"),
            _("Publicación con encuesta"),
            _("Nueva publicación en Bluesky"),
            _("Nueva mención en Bluesky"),
            _("Nueva notificación en Bluesky"),
            _("Mensaje directo de Bluesky"),
            _("Error de Bluesky"),
            _("Nuevo mensaje en Telegram"),
            _("Mensaje de canal de IRC"),
            _("Mención a tu apodo en IRC"),
            _("Privado de IRC"),
            _("Mensaje de sala de Matrix"),
            _("Mención a tu usuario en Matrix"),
            _("Directo de Matrix"),
            _("Nueva publicación en W Social"),
            _("Nueva mención en W Social"),
            _("Nueva notificación en W Social"),
            _("Mensaje directo de W Social"),
            _("Error de W Social")
        ]
        
        self.clb_sounds = AccessibleCheckListBox(
            panel,
            choices=descriptions,
            size=(-1, 150),
            name=_("Lista de sonidos individuales")
        )
        
        # Marcar los habilitados
        disabled = config.get("audio", "disabled_events", [])
        for idx, key in enumerate(self.sound_event_keys):
            self.clb_sounds.Check(idx, key not in disabled)
            
        sizer.Add(self.clb_sounds, 1, wx.EXPAND | wx.ALL, 5)
        
        # Botón para restaurar sonidos predeterminados
        self.btn_restore_sounds = wx.Button(
            panel, 
            label=_("&Generar / Restaurar sonidos por defecto"),
            name=_("Restaurar sonidos")
        )
        sizer.Add(self.btn_restore_sounds, 0, wx.ALL, 10)
        
        self.btn_restore_sounds.Bind(wx.EVT_BUTTON, self._on_restore_sounds)
        
        panel.SetSizer(sizer)
        return panel

    def _crear_panel_proxy(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_(
            "Conecta a través de un proxy. Los cambios se aplican al guardar; algunas "
            "conexiones ya abiertas pueden necesitar reiniciar la aplicación."
        )), 0, wx.ALL, 5)

        self.cb_proxy_enabled = wx.CheckBox(
            panel, label=_("Usar un proxy para las conexiones"), name=_("Activar proxy"))
        self.cb_proxy_enabled.SetValue(config.get("proxy", "enabled", False))
        sizer.Add(self.cb_proxy_enabled, 0, wx.ALL, 10)

        sizer.Add(wx.StaticText(panel, label=_("Tipo de proxy:")), 0, wx.LEFT | wx.TOP, 5)
        self._proxy_types = ["http", "https", "socks5"]
        self.choice_proxy_type = wx.Choice(panel, choices=self._proxy_types, name=_("Tipo de proxy"))
        tipo = config.get("proxy", "type", "http")
        self.choice_proxy_type.SetSelection(self._proxy_types.index(tipo) if tipo in self._proxy_types else 0)
        sizer.Add(self.choice_proxy_type, 0, wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("Servidor:")), 0, wx.LEFT | wx.TOP, 5)
        self.txt_proxy_server = AccessibleTextCtrl(
            panel, value=str(config.get("proxy", "server", "")),
            name=_("Servidor del proxy"),
            ayuda_texto=_("Dirección o nombre del servidor proxy, sin el http delante."))
        sizer.Add(self.txt_proxy_server, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("Puerto:")), 0, wx.LEFT | wx.TOP, 5)
        self.spin_proxy_port = wx.SpinCtrl(
            panel, min=1, max=65535,
            initial=int(config.get("proxy", "port", 8080) or 8080),
            name=_("Puerto del proxy"))
        sizer.Add(self.spin_proxy_port, 0, wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("Usuario (opcional):")), 0, wx.LEFT | wx.TOP, 5)
        self.txt_proxy_user = AccessibleTextCtrl(
            panel, value=str(config.get("proxy", "user", "")),
            name=_("Usuario del proxy"),
            ayuda_texto=_("Déjelo en blanco si el proxy no requiere autenticación."))
        sizer.Add(self.txt_proxy_user, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("Contraseña (opcional):")), 0, wx.LEFT | wx.TOP, 5)
        self.txt_proxy_pass = AccessibleTextCtrl(
            panel, value=str(config.get("proxy", "password", "")),
            style=wx.TE_PASSWORD, name=_("Contraseña del proxy"),
            ayuda_texto=_("Contraseña para el proxy, si la requiere."))
        sizer.Add(self.txt_proxy_pass, 0, wx.EXPAND | wx.ALL, 5)

        panel.SetSizer(sizer)
        return panel

    def _on_restore_sounds(self, event):
        try:
            generar_todos_los_sonidos_predeterminados()
            notificar(_("Sonidos restaurados con éxito."))
        except Exception as e:
            notificar(_("Error al restaurar sonidos."))

    # --- GUARDADO ---
    def _panel_reproductores(self, parent) -> wx.Panel:
        """Ajustes de los dos reproductores: cuántos segundos saltan al avanzar/retroceder.

        El volumen y la velocidad NO se ponen aquí: se recuerdan solos según los dejes
        en cada reproductor."""
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_(
            "El volumen y la velocidad se guardan automáticamente tal y como los dejes "
            "en cada reproductor. Aquí se configura cuántos segundos saltan los botones "
            "de avanzar y retroceder.")), 0, wx.ALL, 8)

        # --- Reproductor de redes sociales (player1) ---
        box1 = wx.StaticBoxSizer(wx.VERTICAL, panel, _("Reproductor de redes sociales"))
        f1 = wx.BoxSizer(wx.HORIZONTAL)
        f1.Add(wx.StaticText(panel, label=_("Avanzar (segundos):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.spin_step_fwd = wx.SpinCtrl(panel, min=1, max=600,
                                         initial=int(config.get("media_player", "default_step_forward", 10.0)),
                                         name=_("Segundos al avanzar en el reproductor de redes"))
        f1.Add(self.spin_step_fwd, 0, wx.ALL, 5)
        f1.Add(wx.StaticText(panel, label=_("Retroceder (segundos):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.spin_step_back = wx.SpinCtrl(panel, min=1, max=600,
                                          initial=int(config.get("media_player", "default_step_backward", 10.0)),
                                          name=_("Segundos al retroceder en el reproductor de redes"))
        f1.Add(self.spin_step_back, 0, wx.ALL, 5)
        box1.Add(f1, 0, wx.ALL, 3)
        sizer.Add(box1, 0, wx.EXPAND | wx.ALL, 8)

        # --- Segundo reproductor (radio, podcast y YouTube) (player2) ---
        box2 = wx.StaticBoxSizer(wx.VERTICAL, panel, _("Reproductor de radio, podcast y YouTube"))
        f2 = wx.BoxSizer(wx.HORIZONTAL)
        f2.Add(wx.StaticText(panel, label=_("Avanzar (segundos):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.spin_step_fwd2 = wx.SpinCtrl(panel, min=1, max=600,
                                          initial=int(config.get("media_player", "default_step_forward2", 15.0)),
                                          name=_("Segundos al avanzar en el reproductor de radio, podcast y YouTube"))
        f2.Add(self.spin_step_fwd2, 0, wx.ALL, 5)
        f2.Add(wx.StaticText(panel, label=_("Retroceder (segundos):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.spin_step_back2 = wx.SpinCtrl(panel, min=1, max=600,
                                           initial=int(config.get("media_player", "default_step_backward2", 15.0)),
                                           name=_("Segundos al retroceder en el reproductor de radio, podcast y YouTube"))
        f2.Add(self.spin_step_back2, 0, wx.ALL, 5)
        box2.Add(f2, 0, wx.ALL, 3)
        f3 = wx.BoxSizer(wx.HORIZONTAL)
        f3.Add(wx.StaticText(panel, label=_("Al reanudar un podcast, retroceder (segundos):")), 0,
               wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.spin_rebobinar2 = wx.SpinCtrl(panel, min=0, max=60,
                                           initial=int(config.get("media_player", "rewind_on_resume2", 5)),
                                           name=_("Segundos que se retrocede al reanudar un podcast"))
        f3.Add(self.spin_rebobinar2, 0, wx.ALL, 5)
        f3.Add(wx.StaticText(panel, label=_("Dar por terminado a (segundos del final):")), 0,
               wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.spin_margen2 = wx.SpinCtrl(panel, min=0, max=600,
                                        initial=int(config.get("media_player", "finish_margin2", 60)),
                                        name=_("Segundos antes del final en los que un episodio se da por terminado"))
        f3.Add(self.spin_margen2, 0, wx.ALL, 5)
        box2.Add(f3, 0, wx.ALL, 3)
        self.cb_leido_al_terminar = wx.CheckBox(panel, label=_("Al terminar un episodio, marcar su artículo como leído"),
                                                name=_("Marcar leído al terminar el episodio"))
        self.cb_leido_al_terminar.SetValue(bool(config.get("rss", "mark_read_on_finish", True)))
        box2.Add(self.cb_leido_al_terminar, 0, wx.ALL, 5)
        self.cb_cola_auto = wx.CheckBox(panel, label=_("Al terminar un episodio, reproducir el siguiente de la cola"),
                                        name=_("Reproducir el siguiente de la cola al terminar"))
        self.cb_cola_auto.SetValue(bool(config.get("media_player", "queue_autoplay2", True)))
        box2.Add(self.cb_cola_auto, 0, wx.ALL, 5)
        sizer.Add(box2, 0, wx.EXPAND | wx.ALL, 8)

        panel.SetSizer(sizer)
        return panel
