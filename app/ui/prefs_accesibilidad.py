# -*- coding: utf-8 -*-
"""Paneles de preferencias: Accesibilidad y casillas de servicio (mixin).
"""
import wx
from app.core.config_manager import config


class _PrefAccMixin:
    def _on_abrir_editor_invisible(self, event):
        """Abre el editor de teclas invisibles. El parent wx es este diálogo (para la modalidad),
        pero el frame que registra las teclas globales es la ventana principal (GetParent)."""
        from app.ui.invisible_keymap_editor import InvisibleKeymapEditor
        dlg = InvisibleKeymapEditor(self, frame=self.GetParent())
        dlg.ShowModal()
        dlg.Destroy()

    def _panel_acc_root(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Modo lector de pantalla
        self.cb_screen_reader = wx.CheckBox(panel, label=_("Habilitar modo optimizado para lector de pantalla"), name=_("Modo lector de pantalla"))
        self.cb_screen_reader.SetValue(config.get("accessibility", "screen_reader_mode", True))
        sizer.Add(self.cb_screen_reader, 0, wx.ALL, 10)

        # Activar sonidos
        self.cb_sounds = wx.CheckBox(panel, label=_("Activar efectos de sonido de navegación"), name=_("Efectos de sonido"))
        self.cb_sounds.SetValue(config.get("accessibility", "enable_sounds", True))
        sizer.Add(self.cb_sounds, 0, wx.ALL, 10)

        # Anuncios verbales
        self.cb_speech = wx.CheckBox(panel, label=_("Habilitar anuncios de voz en eventos del sistema"), name=_("Anuncios de voz"))
        self.cb_speech.SetValue(config.get("accessibility", "speech_announcements", True))
        sizer.Add(self.cb_speech, 0, wx.ALL, 10)

        # Contador de caracteres EN VIVO al redactar (mensajes nuevos y respuestas). Si el usuario
        # nota tirones al escribir, puede desactivarlo y consultar los caracteres con Ctrl+Mayús+Espacio.
        self.cb_contador_vivo = wx.CheckBox(panel, label=_("Actualizar el contador de caracteres mientras escribes"), name=_("Contador de caracteres en vivo"))
        self.cb_contador_vivo.SetValue(config.get("accessibility", "contador_en_vivo", True))
        self.cb_contador_vivo.SetToolTip(_(
            "Al redactar (toot/post nuevo o respuesta) se actualiza el número de caracteres restantes "
            "tras una breve pausa. Si notas tirones al escribir, desactívalo: dejará de actualizarse "
            "solo y podrás consultarlo cuando quieras con Ctrl+Mayús+Espacio."))
        sizer.Add(self.cb_contador_vivo, 0, wx.ALL, 10)

        # Cómo entregar los mensajes importantes (resultados, errores, confirmaciones)
        sizer.Add(wx.StaticText(panel, label=_("Avisos importantes (resultados de operaciones, errores y confirmaciones):")), 0, wx.LEFT | wx.TOP, 10)
        self._modos_avisos = ["voz", "mensaje", "ambos"]
        self.choice_modo_avisos = wx.Choice(
            panel,
            choices=[_("Leer por voz con el lector"),
                     _("Mostrar en un cuadro de mensaje"),
                     _("Las dos cosas (voz y cuadro de mensaje)")],
            name=_("Cómo se entregan los avisos importantes"))
        modo_actual = config.get("accessibility", "modo_avisos", "voz")
        self.choice_modo_avisos.SetSelection(self._modos_avisos.index(modo_actual) if modo_actual in self._modos_avisos else 0)
        self.choice_modo_avisos.SetToolTip(_("Elige 'cuadro de mensaje' si no quieres perderte avisos importantes aunque tengas la voz desactivada. No afecta a la lectura de las listas."))
        sizer.Add(self.choice_modo_avisos, 0, wx.EXPAND | wx.LEFT | wx.BOTTOM, 10)

        # Toast nativo de Windows (Centro de actividades) para los avisos importantes.
        self.cb_toast = wx.CheckBox(panel, label=_("Mostrar también los avisos como notificación de Windows (Centro de actividades)"), name=_("Notificaciones de Windows"))
        self.cb_toast.SetValue(config.get("accessibility", "toast_windows", False))
        self.cb_toast.SetToolTip(_("Útil cuando la aplicación está en segundo plano: el aviso queda en el historial de notificaciones de Windows."))
        sizer.Add(self.cb_toast, 0, wx.ALL, 10)

        # Interfaz invisible: teclas globales del sistema para manejar toda la app sin ventana.
        self.cb_invisible = wx.CheckBox(panel, label=_("Interfaz invisible: teclas globales para manejar la app aunque esté en segundo plano"), name=_("Interfaz invisible"))
        self.cb_invisible.SetValue(config.get("accessibility", "interfaz_invisible", False))
        self.cb_invisible.SetToolTip(_("Maneja SocialRSS sin ventana: navegar por elementos, buffers, cuentas y servicios, actuar sobre las publicaciones, y más. Personaliza las teclas con el botón de abajo."))
        sizer.Add(self.cb_invisible, 0, wx.ALL, 10)
        # Comandos invisibles SOLO con la ventana oculta (petición de usuarios): así, con la
        # ventana delante, las teclas globales no interfieren; alternas con Mostrar/Ocultar.
        self.cb_invisible_solo_oculta = wx.CheckBox(
            panel, label=_("Los comandos invisibles solo funcionan con la ventana oculta (modo invisible puro)"),
            name=_("Comandos invisibles solo con la ventana oculta"))
        self.cb_invisible_solo_oculta.SetValue(config.get("accessibility", "invisible_solo_oculta", False))
        self.cb_invisible_solo_oculta.SetToolTip(_(
            "Con esta casilla, las teclas de la interfaz invisible solo actúan cuando la ventana está "
            "oculta (modo invisible); con la ventana visible no hacen nada. La tecla de Mostrar u "
            "ocultar la ventana funciona siempre, para poder alternar entre los dos modos."))
        sizer.Add(self.cb_invisible_solo_oculta, 0, wx.LEFT | wx.BOTTOM, 15)
        # Posición al cambiar de buffer (como TWBlue): "Inicio, 3 de 120".
        self.cb_invisible_posicion = wx.CheckBox(
            panel, label=_("Al cambiar de buffer en la interfaz invisible, decir el elemento actual y el total (como TWBlue)"),
            name=_("Posición al cambiar de buffer"))
        self.cb_invisible_posicion.SetValue(config.get("accessibility", "invisible_anunciar_posicion", True))
        self.cb_invisible_posicion.SetToolTip(_(
            "Al pasar a otro buffer, cuenta o servicio con las teclas de la interfaz invisible se "
            "anuncia el nombre del buffer seguido de la posición, por ejemplo «Inicio, 3 de 120». "
            "Sin la casilla solo se dice el nombre; la posición sigue disponible con su tecla."))
        sizer.Add(self.cb_invisible_posicion, 0, wx.LEFT | wx.BOTTOM, 15)
        self.btn_editor_invisible = wx.Button(panel, label=_("Personalizar las teclas de la interfaz &invisible..."), name=_("Editor de teclas invisibles"))
        self.btn_editor_invisible.Bind(wx.EVT_BUTTON, self._on_abrir_editor_invisible)
        sizer.Add(self.btn_editor_invisible, 0, wx.LEFT | wx.BOTTOM, 15)

        # Feedback braille automático (como TWBlue)
        self.cb_braille = wx.CheckBox(panel, label=_("Enviar también los anuncios a la pantalla braille"), name=_("Anuncios en braille"))
        self.cb_braille.SetValue(config.get("accessibility", "braille_announcements", True))
        sizer.Add(self.cb_braille, 0, wx.ALL, 10)

        # Retardo de voz para no perder mensajes al cambiar el foco
        sizer.Add(wx.StaticText(panel, label=_("Retardo de los mensajes de voz (milisegundos):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_speech_delay = wx.SpinCtrl(panel, min=0, max=600, initial=int(config.get("accessibility", "speech_delay_ms", 120)), name=_("Retardo de los mensajes de voz"))
        self.spin_speech_delay.SetToolTip(_(
            "Tiempo de espera antes de emitir nuestros avisos. Súbalo si se pierden mensajes "
            "al cambiar el foco; bájelo si la voz responde con retraso. 120 recomendado."))
        sizer.Add(self.spin_speech_delay, 0, wx.LEFT | wx.BOTTOM, 10)

        # Complemento de NVDA: lectura completa de listas (parchea el límite de 511 caracteres de
        # NVDA, para que las publicaciones largas se lean enteras al navegar con las flechas).
        sizer.Add(wx.StaticText(panel, label=_(
            "Lectura completa de listas en NVDA (las publicaciones largas se leen enteras al "
            "navegar, no solo los primeros 511 caracteres):")), 0, wx.LEFT | wx.TOP, 10)
        self.btn_instalar_addon_nvda = wx.Button(
            panel, label=_("Instalar complemento para leer listas completas en &NVDA..."),
            name=_("Instalar el complemento de lectura completa de listas en NVDA"))
        self.btn_instalar_addon_nvda.SetToolTip(_(
            "Instala un complemento de NVDA que amplía cuánto texto lee de cada columna de las "
            "listas. NVDA pedirá confirmación y se reiniciará. Puedes desactivarlo cuando quieras "
            "desde el administrador de complementos de NVDA."))
        self.btn_instalar_addon_nvda.Bind(wx.EVT_BUTTON, self._on_instalar_addon_nvda)
        sizer.Add(self.btn_instalar_addon_nvda, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _on_instalar_addon_nvda(self, event):
        """Genera (si hace falta) e instala el complemento de NVDA que permite leer las listas
        completas. La instalación se hace abriendo el .nvda-addon con NVDA, que pide confirmación
        al usuario y se reinicia (vía segura y estándar)."""
        import os
        from app.core.paths import get_bin_path, ROOT_DIR
        from .accessible_controls import mostrar_info, confirmar

        ruta = get_bin_path() / "socialRSSListViewFix.nvda-addon"
        # Si no está (p. ej. ejecutando desde código fuente sin compilar), generarlo al vuelo.
        if not os.path.exists(ruta):
            try:
                import sys
                tools_dir = os.path.join(str(ROOT_DIR), "tools")
                if tools_dir not in sys.path:
                    sys.path.insert(0, tools_dir)
                from build_nvda_addon import construir_addon
                construir_addon()
            except Exception as e:
                mostrar_info(self, _(
                    "No se encontró el complemento y no se pudo generar automáticamente.\n\n"
                    "Detalle: {0}\n\nRuta esperada: {1}").format(e, ruta),
                    _("Complemento de NVDA"))
                return

        if not os.path.exists(ruta):
            mostrar_info(self, _("No se encontró el archivo del complemento en:\n{0}").format(ruta),
                         _("Complemento de NVDA"))
            return

        r = confirmar(self, _(
            "Se va a instalar en NVDA el complemento de lectura completa de listas.\n\n"
            "NVDA te pedirá confirmación y se reiniciará para aplicarlo. ¿Continuar?"),
            _("Instalar complemento de NVDA"))
        if r != wx.ID_YES:
            return

        try:
            # Abrir el .nvda-addon con su aplicación asociada (NVDA), que muestra el diálogo de
            # instalación. Es la vía recomendada: no instala en silencio ni requiere localizar nvda.exe.
            os.startfile(str(ruta))
        except Exception as e:
            mostrar_info(self, _(
                "No se pudo abrir el instalador del complemento. Asegúrate de tener NVDA "
                "instalado.\n\nPuedes instalarlo a mano abriendo este archivo:\n{0}\n\n"
                "Detalle: {1}").format(ruta, e),
                _("Complemento de NVDA"))

    def _panel_acc_multimedia(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Avisos sonoros de multimedia al situarse en una publicación (como TWBlue)
        self.cb_indicate_image = wx.CheckBox(panel, label=_("Reproducir un sonido al situarte en publicaciones con imágenes"), name=_("Aviso sonoro de imágenes"))
        self.cb_indicate_image.SetValue(config.get("accessibility", "indicate_image", False))
        sizer.Add(self.cb_indicate_image, 0, wx.ALL, 10)

        self.cb_indicate_audio = wx.CheckBox(panel, label=_("Reproducir un sonido al situarte en publicaciones con audio o vídeo"), name=_("Aviso sonoro de audio o vídeo"))
        self.cb_indicate_audio.SetValue(config.get("accessibility", "indicate_audio", False))
        sizer.Add(self.cb_indicate_audio, 0, wx.ALL, 10)

        # En las LISTAS/buffers (Inicio, Directos…) el sonido de mensaje directo se activa o desactiva
        # con su sonido en Preferencias > Sonidos ("Mensaje directo recibido"). Esta casilla es para
        # las CONVERSACIONES: cuando abres una conversación como buffer (hilo), por defecto NO suena en
        # cada mensaje (sería un goteo molesto); márcala si quieres que también suene ahí.
        self.cb_sonido_dm_buffer = wx.CheckBox(panel, label=_("Reproducir el sonido de mensaje directo también dentro de una conversación abierta (hilo)"), name=_("Sonido de directo dentro de una conversación"))
        self.cb_sonido_dm_buffer.SetValue(config.get("accessibility", "direct_sound_in_dm_buffer", False))
        self.cb_sonido_dm_buffer.SetToolTip(_("En las listas (Inicio, Directos) el sonido de directo se controla en Preferencias > Sonidos. Esto solo afecta a cuando entras en una conversación/hilo concreto."))
        sizer.Add(self.cb_sonido_dm_buffer, 0, wx.ALL, 10)

        # Auto lectura de novedades
        self.cb_auto_read = wx.CheckBox(panel, label=_("Leer automáticamente elementos nuevos al actualizar"), name=_("Auto lectura de novedades"))
        self.cb_auto_read.SetValue(config.get("accessibility", "auto_read_new", False))
        sizer.Add(self.cb_auto_read, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_acc_texto(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_show_usernames = wx.CheckBox(panel, label=_("Mostrar el @usuario junto al nombre"), name=_("Mostrar nombres de usuario"))
        self.cb_show_usernames.SetValue(config.get("accessibility", "show_screen_names", False))
        sizer.Add(self.cb_show_usernames, 0, wx.ALL, 8)

        self.cb_hide_emojis = wx.CheckBox(panel, label=_("Ocultar emojis en el contenido de las publicaciones"), name=_("Ocultar emojis en el contenido"))
        self.cb_hide_emojis.SetValue(config.get("accessibility", "hide_emojis", False))
        sizer.Add(self.cb_hide_emojis, 0, wx.ALL, 8)

        self.cb_hide_emojis_names = wx.CheckBox(panel, label=_("Ocultar emojis en los nombres de usuario"), name=_("Ocultar emojis en los nombres"))
        self.cb_hide_emojis_names.SetValue(config.get("accessibility", "hide_emojis_names", False))
        sizer.Add(self.cb_hide_emojis_names, 0, wx.ALL, 8)

        self.cb_expand_cw = wx.CheckBox(panel, label=_("Expandir las advertencias de contenido (CW): mostrar también el texto oculto"), name=_("Expandir advertencias de contenido"))
        self.cb_expand_cw.SetValue(config.get("accessibility", "expand_cw", False))
        sizer.Add(self.cb_expand_cw, 0, wx.ALL, 8)

        panel.SetSizer(sizer)
        return panel

    def _panel_acc_columnas(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_(
            "Al moverte por las columnas de las listas (flechas izquierda y derecha, o Ctrl+1 a 9),\n"
            "elige qué quieres que se diga de cada celda:")), 0, wx.ALL, 8)

        self.cb_colnav_row = wx.CheckBox(panel, label=_("Decir el número de fila (\"Fila 1\")"),
                                         name=_("Decir el número de fila"))
        self.cb_colnav_row.SetValue(config.get("accessibility", "colnav_row_num", True))
        sizer.Add(self.cb_colnav_row, 0, wx.ALL, 8)

        self.cb_colnav_header = wx.CheckBox(panel, label=_("Decir el nombre de la columna (\"Autor\", \"Contenido\"...)"),
                                            name=_("Decir el nombre de la columna"))
        self.cb_colnav_header.SetValue(config.get("accessibility", "colnav_col_header", True))
        sizer.Add(self.cb_colnav_header, 0, wx.ALL, 8)

        self.cb_colnav_value = wx.CheckBox(panel, label=_("Decir el contenido de la celda"),
                                           name=_("Decir el contenido de la celda"))
        self.cb_colnav_value.SetValue(config.get("accessibility", "colnav_cell_value", True))
        sizer.Add(self.cb_colnav_value, 0, wx.ALL, 8)

        sizer.Add(wx.StaticText(panel, label=_(
            "Si desactivas las tres, se dirá igualmente el contenido de la celda.")), 0, wx.ALL, 8)

        panel.SetSizer(sizer)
        return panel

    def _añadir_casilla_servicio(self, panel, sizer, service_id, nombre):
        """Añade al principio de un panel de servicio la casilla 'Activar este servicio' y un
        aviso de que el cambio se aplica al reiniciar. Devuelve la casilla creada."""
        from app.core.feature_flags import servicio_habilitado
        cb = wx.CheckBox(panel, label=_("Activar el servicio {0}").format(nombre),
                         name=_("Activar el servicio {0}").format(nombre))
        cb.SetValue(servicio_habilitado(service_id))
        sizer.Add(cb, 0, wx.ALL, 10)
        aviso = wx.StaticText(panel, label=_(
            "Si lo desactivas, {0} desaparece del árbol, los menús y el alta de cuentas al "
            "reiniciar la app. Tus cuentas y datos no se borran; vuelven al reactivarlo.").format(nombre))
        aviso.Wrap(520)
        sizer.Add(aviso, 0, wx.LEFT | wx.BOTTOM, 10)
        return cb
