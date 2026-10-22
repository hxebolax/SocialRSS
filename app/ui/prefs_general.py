# -*- coding: utf-8 -*-
"""Paneles de preferencias: sección General (mixin).
"""
import wx
from app.core.config_manager import config


class _PrefGeneralMixin:
    def _panel_general_root(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Idioma. Las opciones se DETECTAN de la carpeta 'locales' (idiomas con traducción) más el
        # español (idioma base). Se muestran con su nombre (Español, Inglés…) pero se guarda el código.
        from app.core.i18n import idiomas_disponibles, nombre_idioma
        self._lang_codigos = idiomas_disponibles()
        sizer.Add(wx.StaticText(panel, label=_("Idioma de la aplicación:")), 0, wx.ALL, 5)
        self.choice_lang = wx.Choice(panel, choices=[nombre_idioma(c) for c in self._lang_codigos],
                                     name=_("Selector de idioma"))
        _lang_actual = config.get("general", "language", "es")
        self.choice_lang.SetSelection(self._lang_codigos.index(_lang_actual)
                                      if _lang_actual in self._lang_codigos else 0)
        sizer.Add(self.choice_lang, 0, wx.EXPAND | wx.ALL, 5)

        # Confirmar salida
        self.cb_confirm_exit = wx.CheckBox(panel, label=_("Confirmar al salir de la aplicación"), name=_("Confirmación de salida"))
        self.cb_confirm_exit.SetValue(config.get("general", "confirm_exit", True))
        sizer.Add(self.cb_confirm_exit, 0, wx.ALL, 10)

        # Sonido y aviso "listo" al terminar el arranque
        self.cb_ready_sound = wx.CheckBox(panel, label=_("Reproducir sonido y aviso al terminar de cargar"), name=_("Aviso de arranque listo"))
        self.cb_ready_sound.SetValue(config.get("general", "ready_sound", True))
        sizer.Add(self.cb_ready_sound, 0, wx.ALL, 10)

        # Organizar los menús en subcategorías (submenús descriptivos). Se aplica EN CALIENTE
        # al Aceptar: la barra de menús se reconstruye y los menús contextuales grandes se
        # reagrupan la próxima vez que se abren.
        self.cb_menus_cat = wx.CheckBox(
            panel, label=_("Organizar los menús en subcategorías (submenús por temas)"),
            name=_("Organizar menús en subcategorías"))
        self.cb_menus_cat.SetValue(config.get("general", "menus_categorizados", False))
        self.cb_menus_cat.SetToolTip(_(
            "Los menús con muchas opciones (Mastodon, Bluesky, RSS, Telegram, Herramientas... y "
            "los menús contextuales grandes) se reagrupan en submenús con nombres descriptivos "
            "(Publicar y responder, Autor y cuentas, Moderación...). Así cada menú es más corto y "
            "es más fácil encontrar cada acción. Desactivada, los menús quedan como siempre: una "
            "lista plana con todas las opciones. El cambio se aplica al instante al Aceptar."))
        sizer.Add(self.cb_menus_cat, 0, wx.ALL, 10)

        # Nivel de registro (log)
        sizer.Add(wx.StaticText(panel, label=_("Nivel de detalle del registro (log):")), 0, wx.ALL, 5)
        self.choice_log_level = wx.Choice(panel, choices=["DEBUG", "INFO", "WARNING", "ERROR"], name=_("Nivel de log"))
        self.choice_log_level.SetStringSelection(config.get("general", "log_level", "INFO"))
        sizer.Add(self.choice_log_level, 0, wx.ALL, 5)

        # Idioma del corrector ortográfico
        sizer.Add(wx.StaticText(panel, label=_("Idioma del corrector ortográfico:")), 0, wx.ALL, 5)
        self._spell_langs = [("es", _("Español")), ("en", _("Inglés")), ("fr", _("Francés")),
                             ("de", _("Alemán")), ("pt", _("Portugués"))]
        self.choice_spell_lang = wx.Choice(panel, choices=[n for _c, n in self._spell_langs], name=_("Idioma del corrector"))
        actual_lang = config.get("general", "spell_language", "es")
        idxs = [i for i, (c, _n) in enumerate(self._spell_langs) if c == actual_lang]
        self.choice_spell_lang.SetSelection(idxs[0] if idxs else 0)
        sizer.Add(self.choice_spell_lang, 0, wx.ALL, 5)

        # Editor de combinaciones de teclas: personalizar los atajos de la ventana principal y de
        # cada red, y volver a las predefinidas cuando se quiera.
        sizer.Add(wx.StaticText(panel, label=_(
            "Combinaciones de teclas (personaliza los atajos de la ventana principal y de cada red, "
            "y vuelve a las predefinidas cuando quieras):")), 0, wx.LEFT | wx.TOP, 10)
        self.btn_editor_atajos = wx.Button(
            panel, label=_("Personalizar combinaciones de &teclas..."),
            name=_("Abrir el editor de combinaciones de teclas"))
        self.btn_editor_atajos.SetToolTip(_(
            "Abre un editor donde puedes cambiar las teclas de cada acción (organizadas por red), "
            "ver cuáles has personalizado y restaurar una, una sección o todas a sus valores por "
            "defecto. Los cambios se guardan y se aplican al instante, también en los menús."))
        self.btn_editor_atajos.Bind(wx.EVT_BUTTON, self._on_abrir_editor_atajos)
        sizer.Add(self.btn_editor_atajos, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _on_abrir_editor_atajos(self, event):
        """Abre el editor de combinaciones de teclas. Los cambios se aplican en caliente en la
        ventana principal (que es la top window)."""
        from .shortcuts_editor import ShortcutsEditorDialog
        dlg = ShortcutsEditorDialog(self, frame=wx.GetApp().GetTopWindow())
        dlg.ShowModal()
        dlg.Destroy()

    def _panel_general_ventana(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Minimizar al cerrar (bandeja de sistema)
        self.cb_minimize_tray = wx.CheckBox(panel, label=_("Minimizar a la bandeja del sistema al cerrar"), name=_("Minimizar a bandeja"))
        self.cb_minimize_tray.SetValue(config.get("general", "minimize_to_tray", False))
        sizer.Add(self.cb_minimize_tray, 0, wx.ALL, 10)

        # Iniciar minimizado en la bandeja (funciona por sí sola, sin requisitos)
        self.cb_start_minimized = wx.CheckBox(panel, label=_("Iniciar minimizado en la bandeja del sistema"), name=_("Iniciar minimizado"))
        self.cb_start_minimized.SetValue(config.get("general", "start_minimized", False))
        self.cb_start_minimized.SetToolTip(_("Al abrir la aplicación queda directamente en la bandeja del sistema, sin pasar por primer plano, con todo funcionando por detrás (streaming, sondeos). No necesita ninguna otra opción."))
        sizer.Add(self.cb_start_minimized, 0, wx.ALL, 10)

        # Arrancar con Windows (entrada en la clave Run del usuario, como TWBlue). Junto con
        # "Iniciar minimizado" deja SocialRSS funcionando en segundo plano desde el encendido.
        self.cb_run_at_startup = wx.CheckBox(panel, label=_("Iniciar SocialRSS automáticamente con Windows"), name=_("Iniciar con Windows"))
        from app.core import windows_startup
        self.cb_run_at_startup.SetValue(windows_startup.esta_activado())
        self.cb_run_at_startup.SetToolTip(_(
            "Añade SocialRSS al inicio de Windows para esta cuenta de usuario (no hace falta ser "
            "administrador). Si además marcas 'Iniciar minimizado en la bandeja del sistema', el "
            "programa arranca con el equipo y se queda funcionando por detrás, sin abrir la ventana."))
        sizer.Add(self.cb_run_at_startup, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_general_actualizacion(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Interruptor MAESTRO. Si está activado, cada servicio ENCENDIDO se actualiza solo con su
        # propio intervalo (o en tiempo real, en el caso de Mastodon). Si lo apagas, se paran TODAS
        # las actualizaciones automáticas de todos los servicios.
        self.cb_auto_update = wx.CheckBox(panel, label=_("Actualización automática (interruptor general: si lo apagas, NINGÚN servicio se actualiza solo)"), name=_("Actualización automática general"))
        self.cb_auto_update.SetValue(config.get("general", "auto_update_enabled", True))
        self.cb_auto_update.SetToolTip(_(
            "Interruptor maestro. Encendido: cada servicio activo se actualiza con SU intervalo "
            "(o en tiempo real, en Mastodon). Apagado: se detienen todas las actualizaciones "
            "automáticas. Los servicios apagados en Preferencias nunca se actualizan."))
        sizer.Add(self.cb_auto_update, 0, wx.ALL, 10)

        # Comprobar actualizaciones de la APLICACIÓN al iniciar (opcional). Si se desactiva, al
        # arrancar no se comprueba si hay versión nueva (se puede comprobar a mano desde Ayuda).
        self.cb_check_updates = wx.CheckBox(panel, label=_("Comprobar si hay actualizaciones de la aplicación al iniciar"), name=_("Comprobar actualizaciones al iniciar"))
        self.cb_check_updates.SetValue(config.get("general", "check_updates_on_start", True))
        self.cb_check_updates.SetToolTip(_(
            "Si está activado, al abrir la aplicación se comprueba en segundo plano si hay una "
            "versión nueva y, si la hay, se te ofrece instalarla. Puedes comprobarlo a mano cuando "
            "quieras desde el menú Ayuda."))
        sizer.Add(self.cb_check_updates, 0, wx.ALL, 10)

        # El intervalo ya NO es general: cada servicio tiene el suyo en su propia sección de
        # Preferencias (Mastodon, Bluesky, Telegram, X y RSS), e incluso Mastodon puede ir en
        # tiempo real.
        sizer.Add(wx.StaticText(panel, label=_(
            "El intervalo de actualización ahora es de cada servicio (Mastodon, Bluesky, Telegram, "
            "X y RSS), en su propia sección. Mastodon además puede actualizarse en tiempo real. "
            "Los servicios que tengas apagados no se actualizan.")),
            0, wx.ALL, 8)

        panel.SetSizer(sizer)
        return panel

    def _panel_general_presentacion(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Marcas de tiempo relativas
        self.cb_relative_times = wx.CheckBox(panel, label=_("Mostrar marcas de tiempo relativas (hace 5 minutos) en lugar de la fecha exacta"), name=_("Marcas de tiempo relativas"))
        self.cb_relative_times.SetValue(config.get("general", "relative_times", True))
        sizer.Add(self.cb_relative_times, 0, wx.ALL, 10)

        # Bandeja de entrada unificada (multi-red): muestra/oculta su nodo en el árbol.
        self.cb_inbox = wx.CheckBox(panel, label=_("Mostrar la bandeja de entrada unificada (reúne buffers de varias redes)"), name=_("Bandeja de entrada unificada"))
        self.cb_inbox.SetValue(config.get("inbox", "enabled", False))
        self.cb_inbox.SetToolTip(_(
            "Añade un nodo 'Bandeja de entrada' en el árbol que reúne, en una sola lista, las "
            "publicaciones de los buffers de varias redes que elijas. Sobre ese nodo, en su menú "
            "contextual, puedes agregar o quitar buffers. Cada publicación conserva el menú de su red."))
        sizer.Add(self.cb_inbox, 0, wx.ALL, 10)

        # Bandeja: panel de detalles y botonera (dinámicos según la publicación enfocada, igual que el
        # menú contextual). Son independientes de las opciones por red.
        self.cb_inbox_details = wx.CheckBox(panel, label=_("En la bandeja, mostrar el panel de detalles de la publicación enfocada"), name=_("Panel de detalles en la bandeja"))
        self.cb_inbox_details.SetValue(config.get("inbox", "show_details_panel", True))
        self.cb_inbox_details.SetToolTip(_(
            "Muestra junto a la lista de la bandeja un panel de solo lectura con el contenido y los "
            "datos de la publicación enfocada. El contenido se adapta a la red de cada publicación."))
        sizer.Add(self.cb_inbox_details, 0, wx.LEFT | wx.BOTTOM, 25)

        self.cb_inbox_buttons = wx.CheckBox(panel, label=_("En la bandeja, mostrar la botonera de acciones bajo la lista"), name=_("Botonera de acciones en la bandeja"))
        self.cb_inbox_buttons.SetValue(config.get("inbox", "show_action_buttons", False))
        self.cb_inbox_buttons.SetToolTip(_(
            "Una fila de botones que cambian según la publicación enfocada y su red (Responder, "
            "Impulsar/Me gusta, Conversación…), igual que el menú contextual."))
        sizer.Add(self.cb_inbox_buttons, 0, wx.LEFT | wx.BOTTOM, 25)

        # NOTA: el panel de detalles y la botonera de acciones se configuran AHORA por red, en
        # Preferencias -> cada servicio (Mastodon, Bluesky, X, Telegram, RSS), no aquí en General.

        # Orden de aparición de elementos recibidos
        sizer.Add(wx.StaticText(panel, label=_("Orden de aparición de elementos recibidos:")), 0, wx.ALL, 5)
        self.choice_order = wx.Choice(
            panel,
            choices=[_("Los más recientes arriba (Nuevos primero)"), _("Los más recientes abajo (Antiguos primero)")],
            name=_("Orden de elementos")
        )
        self.choice_order.SetSelection(0 if config.get("general", "newest_first", True) else 1)
        sizer.Add(self.choice_order, 0, wx.EXPAND | wx.ALL, 5)

        panel.SetSizer(sizer)
        return panel
