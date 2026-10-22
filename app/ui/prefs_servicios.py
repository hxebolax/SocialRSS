# -*- coding: utf-8 -*-
"""Paneles de preferencias por servicio (Mastodon, X, Bluesky, RSS, Telegram) (mixin).
"""
import wx
from app.core.config_manager import config
from app.core.speech import speak, notificar
from .accessible_controls import AccessibleSlider, AccessibleTextCtrl


class _PrefServiciosMixin:
    def _panel_mastodon_root(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_mastodon = self._añadir_casilla_servicio(panel, sizer, "mastodon", _("Mastodon"))

        def _encabezado(texto):
            # Separador de grupo dentro del panel, para tener juntas las opciones del mismo tema.
            t = wx.StaticText(panel, label=texto)
            f = t.GetFont()
            f.SetWeight(wx.FONTWEIGHT_BOLD)
            t.SetFont(f)
            sizer.Add(t, 0, wx.LEFT | wx.TOP, 12)

        # ===================== ACTUALIZACIÓN =====================
        _encabezado(_("Actualización"))

        # Tiempo real (streaming) vs intervalo. Si se activa el tiempo real, los toots,
        # notificaciones y directos llegan al instante (streaming) sin sondear; si la instancia
        # no lo soporta, se cae solo al intervalo. Si NO se activa, se usa el intervalo de abajo.
        self.cb_mastodon_realtime = wx.CheckBox(
            panel, label=_("Actualizar Mastodon en TIEMPO REAL (streaming, sin sondear)"),
            name=_("Mastodon en tiempo real"))
        self.cb_mastodon_realtime.SetValue(bool(config.get("mastodon", "realtime", False)))
        self.cb_mastodon_realtime.SetToolTip(_(
            "Recibe las novedades al instante mediante streaming, sin pedirlas cada X segundos. "
            "Si tu instancia no tiene streaming, se usa automáticamente el intervalo de abajo. "
            "Desactívalo si quieres un intervalo fijo para no cargar instancias pequeñas."))
        sizer.Add(self.cb_mastodon_realtime, 0, wx.ALL, 8)

        # Intervalo (se usa cuando NO está el tiempo real). Mínimo 10 s.
        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualización de Mastodon (segundos, mínimo 10):")), 0, wx.ALL, 5)
        self.txt_mastodon_interval = AccessibleTextCtrl(
            panel,
            value=str(config.get("mastodon", "update_interval", 60)),
            name=_("Intervalo actualización Mastodon"),
            ayuda_texto=_("Segundos entre comprobaciones automáticas. Solo se usa si NO has activado el tiempo real.")
        )
        sizer.Add(self.txt_mastodon_interval, 0, wx.EXPAND | wx.ALL, 5)

        # Publicaciones por carga
        sizer.Add(wx.StaticText(panel, label=_("Publicaciones por carga (1-200):")), 0, wx.ALL, 5)
        self.txt_mastodon_limit = AccessibleTextCtrl(
            panel,
            value=str(config.get("mastodon", "limit_per_load", 40)),
            name=_("Publicaciones por carga"),
            ayuda_texto=_("Cuántas publicaciones se reúnen en cada carga del timeline (1 a 200). "
                          "Mastodon limita cada petición a 40, así que si pides más, la app "
                          "descarga varias páginas y las junta.")
        )
        sizer.Add(self.txt_mastodon_limit, 0, wx.EXPAND | wx.ALL, 5)

        # ===================== PUBLICAR =====================
        _encabezado(_("Publicar"))

        # Visibilidad por defecto. Las etiquetas se muestran en español, pero el valor que se GUARDA
        # sigue siendo el interno de Mastodon ("public"/"unlisted"/"private"/"direct"), que es lo que
        # usa el resto de la app. Por eso se mapea por índice con _visibilidad_valores.
        sizer.Add(wx.StaticText(panel, label=_("Visibilidad por defecto para toots nuevos:")), 0, wx.ALL, 5)
        self._visibilidad_valores = ["public", "unlisted", "private", "direct"]
        self.choice_visibility = wx.Choice(panel, choices=[
            _("Público"),
            _("No listado"),
            _("Privado (solo seguidores)"),
            _("Directo (mensaje directo)"),
        ], name=_("Selector de privacidad por defecto"))
        _vis = config.get("mastodon", "default_visibility", "public")
        self.choice_visibility.SetSelection(
            self._visibilidad_valores.index(_vis) if _vis in self._visibilidad_valores else 0)
        sizer.Add(self.choice_visibility, 0, wx.EXPAND | wx.ALL, 5)

        # Leer las preferencias de publicación desde el servidor (va junto a la visibilidad por defecto
        # porque también determina la privacidad inicial al componer).
        self.cb_read_prefs = wx.CheckBox(panel, label=_("Tomar la visibilidad por defecto del servidor al publicar"), name=_("Leer preferencias del servidor"))
        self.cb_read_prefs.SetValue(config.get("mastodon", "read_preferences_from_instance", False))
        self.cb_read_prefs.SetToolTip(_("Al componer, la privacidad inicial será la que tengas configurada en tu instancia de Mastodon en lugar de la elegida aquí."))
        sizer.Add(self.cb_read_prefs, 0, wx.ALL, 10)

        # Visibilidad PROPUESTA al RESPONDER a una publicación.
        sizer.Add(wx.StaticText(panel, label=_("Visibilidad propuesta al responder a una publicación:")), 0, wx.ALL, 5)
        self._reply_vis_modos = ["oficial", "no_listado"]
        self.choice_reply_vis = wx.Choice(panel, choices=[
            _("Como Mastodon oficial: la más privada entre la del mensaje y tu visibilidad por defecto"),
            _("Responder siempre como No listado (los mensajes directos siguen siendo directos)"),
        ], name=_("Visibilidad al responder"))
        _modo_resp = config.get("mastodon", "reply_visibility_mode", "oficial")
        self.choice_reply_vis.SetSelection(
            self._reply_vis_modos.index(_modo_resp) if _modo_resp in self._reply_vis_modos else 0)
        self.choice_reply_vis.SetToolTip(_(
            "Qué privacidad se propone al responder. Con la opción oficial, responder a un mensaje "
            "público usa tu visibilidad por defecto, y a uno más privado mantiene esa privacidad. "
            "Con la otra opción tus respuestas no aparecen en la línea pública. En ambos casos "
            "puedes cambiar la privacidad en el compositor antes de publicar."))
        sizer.Add(self.choice_reply_vis, 0, wx.EXPAND | wx.ALL, 5)

        # Modo de impulso (boost)
        sizer.Add(wx.StaticText(panel, label=_("Al impulsar una publicación:")), 0, wx.ALL, 5)
        self.choice_boost_mode = wx.Choice(panel, choices=[_("Preguntar (impulsar o citar)"), _("Impulsar directamente"), _("Citar siempre")], name=_("Modo de impulso"))
        self.choice_boost_mode.SetSelection({"ask": 0, "boost": 1, "quote": 2}.get(config.get("mastodon", "boost_mode", "ask"), 0))
        sizer.Add(self.choice_boost_mode, 0, wx.EXPAND | wx.ALL, 5)

        # Confirmar antes de publicar
        self.cb_confirm_post = wx.CheckBox(panel, label=_("Confirmar antes de publicar toots"), name=_("Confirmar publicación"))
        self.cb_confirm_post.SetValue(config.get("mastodon", "confirm_post", False))
        sizer.Add(self.cb_confirm_post, 0, wx.ALL, 10)

        # Recordatorio de texto alternativo
        self.cb_alt_reminder = wx.CheckBox(panel, label=_("Avisar al publicar si hay imágenes sin descripción (texto alternativo)"), name=_("Recordatorio de texto alternativo"))
        self.cb_alt_reminder.SetValue(config.get("mastodon", "alt_text_reminder", True))
        sizer.Add(self.cb_alt_reminder, 0, wx.ALL, 10)

        # ===================== MOSTRAR EN LA LISTA =====================
        _encabezado(_("Mostrar en la lista"))

        self.cb_details_panel = wx.CheckBox(panel, label=_("Mostrar el panel de detalles de la publicación enfocada"), name=_("Mostrar panel de detalles"))
        self.cb_details_panel.SetValue(config.get("mastodon", "show_details_panel", False))
        self.cb_details_panel.SetToolTip(_("Igual que en Bluesky: junto a la lista se muestra un panel con el contenido y los datos de la publicación que tengas enfocada. Desactivado por defecto."))
        sizer.Add(self.cb_details_panel, 0, wx.ALL, 10)

        self.cb_mst_buttons = wx.CheckBox(panel, label=_("Mostrar la botonera de acciones bajo la lista"), name=_("Mostrar botonera de acciones en Mastodon"))
        self.cb_mst_buttons.SetValue(config.get("mastodon", "show_action_buttons", False))
        self.cb_mst_buttons.SetToolTip(_("Una fila de botones que cambian según el buffer (Responder, Favorito, Impulsar, Conversación...) y actúan sobre la publicación enfocada. Si también muestras el panel de detalles, los botones van debajo de él."))
        sizer.Add(self.cb_mst_buttons, 0, wx.ALL, 10)

        # ===================== FILTRAR TIMELINES (Inicio, Local, Federada) =====================
        _encabezado(_("Filtrar los timelines (Inicio, Local y Federada)"))

        # Filtrado del timeline: ocultar impulsos y/o respuestas en Inicio/Local/Federada.
        self.cb_show_boosts = wx.CheckBox(panel, label=_("Mostrar impulsos (boosts) en los timelines de Inicio, Local y Federada"), name=_("Mostrar impulsos"))
        self.cb_show_boosts.SetValue(config.get("mastodon", "show_boosts", True))
        self.cb_show_boosts.SetToolTip(_("Si lo desactivas, en Inicio, Local y Federada no se mostraran las publicaciones que son un impulso (boost) de otra cuenta."))
        sizer.Add(self.cb_show_boosts, 0, wx.ALL, 10)

        self.cb_show_replies = wx.CheckBox(panel, label=_("Mostrar respuestas en los timelines de Inicio, Local y Federada"), name=_("Mostrar respuestas"))
        self.cb_show_replies.SetValue(config.get("mastodon", "show_replies", True))
        self.cb_show_replies.SetToolTip(_("Si lo desactivas, en Inicio, Local y Federada no se mostraran las publicaciones que son una respuesta a otra."))
        sizer.Add(self.cb_show_replies, 0, wx.ALL, 10)

        # ===================== OTRAS OPCIONES =====================
        _encabezado(_("Otras opciones"))

        # Herramientas de administración de instancia (solo para admins/moderadores). Si se activa,
        # aparece un submenú "Administración" en el menú Mastodon, en caliente.
        self.cb_mastodon_admin = wx.CheckBox(
            panel, label=_("Activar herramientas de administración de instancia (solo admins/moderadores)"),
            name=_("Herramientas de administración de Mastodon"))
        self.cb_mastodon_admin.SetValue(bool(config.get("mastodon", "admin_tools", False)))
        self.cb_mastodon_admin.SetToolTip(_(
            "Si tu cuenta administra o modera tu instancia, añade un submenú 'Administración' al menú "
            "Mastodon con informes, cuentas, dominios, bloqueos de email/IP y tendencias por aprobar. "
            "Se añade o se quita al instante al marcar o desmarcar esta casilla."))
        sizer.Add(self.cb_mastodon_admin, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_mastodon_mantenimiento(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Mantenimiento del histórico (Fase 6). 0 = sin límite, se conserva todo.
        sizer.Add(wx.StaticText(panel, label=_("Máximo de elementos por buffer (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_mst_max_buffer = wx.SpinCtrl(panel, min=0, max=100000, initial=int(config.get("mastodon", "max_items_buffer", 0)), name=_("Máximo de elementos por buffer de Mastodon"))
        self.spin_mst_max_buffer.SetToolTip(_("Al arrancar se recortan los buffers de alto volumen (Inicio, Local, Federada…) a este número, conservando siempre favoritos y marcadores. 0 = no se recorta nada."))
        sizer.Add(self.spin_mst_max_buffer, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Máximo de notificaciones guardadas por cuenta (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_mst_max_notifs = wx.SpinCtrl(panel, min=0, max=100000, initial=int(config.get("mastodon", "max_notifications", 0)), name=_("Máximo de notificaciones por cuenta de Mastodon"))
        sizer.Add(self.spin_mst_max_notifs, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_mst_no_persist = wx.CheckBox(panel, label=_("No conservar publicaciones entre sesiones (vaciar al reiniciar)"),
                                             name=_("No conservar publicaciones entre sesiones"))
        self.cb_mst_no_persist.SetValue(config.get("mastodon", "no_conservar_entre_sesiones", False))
        self.cb_mst_no_persist.SetToolTip(_("Si lo activas, al reiniciar la app se vacían los buffers de Inicio, Local, Federada, Directos, Notificaciones y Menciones (se conservan favoritos, marcadores y tus publicaciones enviadas). Útil si no quieres guardar NADA entre sesiones (los límites de arriba con 0 significan 'sin límite', no 'ninguno')."))
        sizer.Add(self.cb_mst_no_persist, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_x_root(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        aviso = wx.StaticText(panel, label=_(
            "El cliente de X (Twitter) es NO OFICIAL: usa la API interna con tus cookies. "
            "Puede incumplir las condiciones de X y conllevar riesgo de bloqueo de la cuenta, "
            "y puede dejar de funcionar cuando X cambie su web."))
        aviso.Wrap(520)
        sizer.Add(aviso, 0, wx.ALL, 8)

        self.cb_x_enabled = wx.CheckBox(panel, label=_("Activar el cliente de X (Twitter)"),
                                        name=_("Activar cliente de X"))
        self.cb_x_enabled.SetValue(bool(config.get("x", "enabled", True)))
        sizer.Add(self.cb_x_enabled, 0, wx.ALL, 10)
        sizer.Add(wx.StaticText(panel, label=_(
            "(Si lo desactivas, X desaparece de los menús y el árbol al reiniciar la app.)")),
            0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Publicaciones por carga:")), 0, wx.ALL, 5)
        self.txt_x_limit = AccessibleTextCtrl(
            panel, value=str(config.get("x", "limit_per_load", 40)),
            name=_("Publicaciones por carga de X"),
            ayuda_texto=_("Cuántas publicaciones se piden en cada página."))
        sizer.Add(self.txt_x_limit, 0, wx.EXPAND | wx.ALL, 5)

        self.cb_x_auto = wx.CheckBox(
            panel, label=_("Actualizar X automáticamente (aumenta el riesgo de bloqueo)"),
            name=_("Actualizar X automáticamente"))
        self.cb_x_auto.SetValue(bool(config.get("x", "auto_update", False)))
        self.cb_x_auto.SetToolTip(_(
            "Si lo activas, X se pone al día solo: al entrar en un buffer, con el intervalo de "
            "abajo mientras lo miras, y en segundo plano cuando estás en otras redes. ADVERTENCIA: "
            "X es un cliente NO oficial con límites de peticiones muy estrictos; actualizar solo "
            "hace muchas más peticiones y AUMENTA EL RIESGO DE QUE X BLOQUEE TU CUENTA. Si lo dejas "
            "desactivado (recomendado), X solo se actualiza cuando pulsas F5 o Ctrl+Shift+R."))
        sizer.Add(self.cb_x_auto, 0, wx.ALL, 10)
        sizer.Add(wx.StaticText(panel, label=_(
            "(Recomendado: desactivado. Con actualización manual, refresca con F5 o Ctrl+Shift+R.)")),
            0, wx.LEFT | wx.BOTTOM, 10)

        _x_min = int(config.get("x", "min_update_seconds", 120))
        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualización de X (segundos, mínimo {0}):").format(_x_min)), 0, wx.ALL, 5)
        self.txt_x_interval = AccessibleTextCtrl(
            panel, value=str(config.get("x", "update_interval", _x_min)),
            name=_("Intervalo actualización X"),
            ayuda_texto=_("Segundos entre comprobaciones automáticas. X limita con dureza, así que no baja del mínimo."))
        sizer.Add(self.txt_x_interval, 0, wx.EXPAND | wx.ALL, 5)

        self.cb_x_alt = wx.CheckBox(panel, label=_("Avisar al publicar si hay imágenes sin descripción (texto alternativo)"),
                                    name=_("Recordatorio de texto alternativo en X"))
        self.cb_x_alt.SetValue(bool(config.get("x", "alt_text_reminder", True)))
        sizer.Add(self.cb_x_alt, 0, wx.ALL, 10)

        self.cb_x_prefijo_respuesta = wx.CheckBox(
            panel, label=_("Decir a quién responde al principio del texto de las respuestas"),
            name=_("Decir a quién responde en X"))
        self.cb_x_prefijo_respuesta.SetValue(bool(config.get("x", "prefijo_respuesta", False)))
        self.cb_x_prefijo_respuesta.SetToolTip(_(
            "Antepone \"En respuesta a @usuario\" al texto. También puedes añadir el campo "
            "{reply_to} a la plantilla de lectura para tenerlo en su propia columna."))
        sizer.Add(self.cb_x_prefijo_respuesta, 0, wx.ALL, 10)

        self.cb_x_no_persist = wx.CheckBox(panel, label=_("No conservar publicaciones entre sesiones (vaciar al reiniciar)"),
                                           name=_("No conservar publicaciones entre sesiones en X"))
        self.cb_x_no_persist.SetValue(bool(config.get("x", "no_conservar_entre_sesiones", False)))
        self.cb_x_no_persist.SetToolTip(_("Si lo activas, al reiniciar la app se vacían los buffers de publicaciones, notificaciones y personas de X (todo se vuelve a descargar al usar). No afecta a tu sesión ni a las listas/búsquedas guardadas."))
        sizer.Add(self.cb_x_no_persist, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_x_details = wx.CheckBox(panel, label=_("Mostrar el panel de detalles del tweet enfocado"), name=_("Mostrar panel de detalles en X"))
        self.cb_x_details.SetValue(bool(config.get("x", "show_details_panel", True)))
        sizer.Add(self.cb_x_details, 0, wx.ALL, 10)

        self.cb_x_buttons = wx.CheckBox(panel, label=_("Mostrar la botonera de acciones bajo la lista"), name=_("Mostrar botonera de acciones en X"))
        self.cb_x_buttons.SetValue(bool(config.get("x", "show_action_buttons", False)))
        self.cb_x_buttons.SetToolTip(_("Botones que cambian según el buffer (Responder, Me gusta, Retweet, Conversación...) y actúan sobre el tweet enfocado."))
        sizer.Add(self.cb_x_buttons, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_wsocial_root(self, parent) -> wx.Panel:
        """Preferencias de W Social. El servidor NO se configura aqui a proposito: se descubre
        resolviendo la identidad de cada cuenta al anadirla, que es como funciona AT Protocol."""
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_wsocial = self._añadir_casilla_servicio(panel, sizer, "wsocial", _("W Social"))

        self.cb_wsocial_tiempo_real = wx.CheckBox(
            panel, label=_("Avisos en tiempo real (notificaciones y mensajes)"),
            name=_("Tiempo real en W Social"))
        self.cb_wsocial_tiempo_real.SetValue(bool(config.get("wsocial", "tiempo_real", True)))
        self.cb_wsocial_tiempo_real.SetToolTip(_(
            "Comprueba en segundo plano si hay notificaciones o mensajes nuevos y avisa al momento. "
            "Cada red tiene su propio vigilante, asi que esto no afecta al tiempo real de las demas."))
        sizer.Add(self.cb_wsocial_tiempo_real, 0, wx.ALL, 10)

        sizer.Add(wx.StaticText(panel, label=_("Cada cuantos segundos comprueba las novedades:")),
                  0, wx.ALL, 5)
        self.txt_wsocial_tr_intervalo = AccessibleTextCtrl(
            panel, value=str(config.get("wsocial", "intervalo_tiempo_real", 60)),
            name=_("Intervalo del tiempo real de W Social"),
            ayuda_texto=_("Minimo 20 segundos. Cuanto mas bajo, antes se entera, pero mas consulta."))
        sizer.Add(self.txt_wsocial_tr_intervalo, 0, wx.EXPAND | wx.ALL, 5)

        self.cb_wsocial_avisar_dm = wx.CheckBox(
            panel, label=_("Avisar de los mensajes directos nuevos"),
            name=_("Avisar de mensajes de W Social"))
        self.cb_wsocial_avisar_dm.SetValue(bool(config.get("wsocial", "avisar_mensajes", True)))
        sizer.Add(self.cb_wsocial_avisar_dm, 0, wx.ALL, 10)

        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualizacion del buffer (segundos):")),
                  0, wx.ALL, 5)
        self.txt_wsocial_interval = AccessibleTextCtrl(
            panel, value=str(config.get("wsocial", "update_interval", 60)),
            name=_("Intervalo de actualizacion de W Social"),
            ayuda_texto=_("Segundos entre comprobaciones del buffer que tienes abierto."))
        sizer.Add(self.txt_wsocial_interval, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("Publicaciones por carga:")), 0, wx.ALL, 5)
        self.txt_wsocial_limit = AccessibleTextCtrl(
            panel, value=str(config.get("wsocial", "limit_per_load", 40)),
            name=_("Publicaciones por carga en W Social"),
            ayuda_texto=_("Cuantas publicaciones se piden en cada pagina (1 a 100)."))
        sizer.Add(self.txt_wsocial_limit, 0, wx.EXPAND | wx.ALL, 5)

        self.cb_wsocial_alt = wx.CheckBox(
            panel, label=_("Recordarme describir las imagenes al publicar"),
            name=_("Recordatorio de texto alternativo en W Social"))
        self.cb_wsocial_alt.SetValue(bool(config.get("wsocial", "alt_text_reminder", True)))
        sizer.Add(self.cb_wsocial_alt, 0, wx.ALL, 10)

        self.cb_wsocial_confirm = wx.CheckBox(
            panel, label=_("Pedirme confirmacion antes de publicar"),
            name=_("Confirmar publicaciones en W Social"))
        self.cb_wsocial_confirm.SetValue(bool(config.get("wsocial", "confirm_post", False)))
        sizer.Add(self.cb_wsocial_confirm, 0, wx.ALL, 10)

        self.cb_wsocial_detalles = wx.CheckBox(
            panel, label=_("Mostrar el panel de detalles de la publicacion enfocada"),
            name=_("Panel de detalles en W Social"))
        self.cb_wsocial_detalles.SetValue(bool(config.get("wsocial", "show_details_panel", True)))
        sizer.Add(self.cb_wsocial_detalles, 0, wx.ALL, 10)

        self.cb_wsocial_botonera = wx.CheckBox(
            panel, label=_("Mostrar la botonera de acciones bajo la lista"),
            name=_("Botonera de acciones en W Social"))
        self.cb_wsocial_botonera.SetValue(bool(config.get("wsocial", "show_action_buttons", False)))
        sizer.Add(self.cb_wsocial_botonera, 0, wx.ALL, 10)

        # Lectura de republicaciones y respuestas en las listas
        self.choice_wsocial_formato_repost, self.cb_wsocial_prefijo_respuesta = \
            self._controles_lectura_at(panel, sizer, "wsocial", _("republicado por"),
                                       _("republicó"), _("W Social"))
        # Activada por defecto en W Social: siempre había puesto la mención del autor.
        self.cb_wsocial_mencionar = self._casilla_mencionar(panel, sizer, "wsocial", True,
                                                            _("W Social"))

        self.cb_wsocial_no_conservar = wx.CheckBox(
            panel, label=_("No conservar las publicaciones entre sesiones"),
            name=_("No conservar publicaciones de W Social"))
        self.cb_wsocial_no_conservar.SetValue(
            bool(config.get("wsocial", "no_conservar_entre_sesiones", False)))
        self.cb_wsocial_no_conservar.SetToolTip(_(
            "Al arrancar se vacian Inicio, Menciones, feeds, listas y busquedas. Se conservan "
            "siempre tus me gusta, marcadores, enviados y mensajes directos."))
        sizer.Add(self.cb_wsocial_no_conservar, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    # Valores guardados de "formato_repost", en el orden de las opciones del desplegable.
    _FORMATOS_REPOST_AT = ("original", "repostea")

    def _controles_lectura_at(self, panel, sizer, red, participio, verbo, nombre_red):
        """Controles de lectura de Bluesky / W Social: cómo se cuenta un repost en la lista y si se
        antepone "En respuesta a @usuario" al texto de las respuestas. Devuelve (choice, casilla)."""
        sizer.Add(wx.StaticText(panel, label=_("Cómo se leen los reposts en las listas:")),
                  0, wx.ALL, 5)
        choice = wx.Choice(panel, choices=[
            _("Mensaje del autor, {0} quien lo comparte (como hasta ahora)").format(participio),
            _("Quien lo comparte primero: X {0} el mensaje de Y").format(verbo),
        ], name=_("Lectura de reposts en {0}").format(nombre_red))
        actual = config.get(red, "formato_repost", "original")
        choice.SetSelection(self._FORMATOS_REPOST_AT.index(actual)
                            if actual in self._FORMATOS_REPOST_AT else 0)
        sizer.Add(choice, 0, wx.EXPAND | wx.ALL, 5)
        casilla = wx.CheckBox(
            panel, label=_("Decir a quién responde al principio del texto de las respuestas"),
            name=_("Decir a quién responde en {0}").format(nombre_red))
        casilla.SetValue(bool(config.get(red, "prefijo_respuesta", False)))
        casilla.SetToolTip(_(
            "Antepone \"En respuesta a @usuario\" al texto. También puedes añadir el campo "
            "{reply_to} a la plantilla de lectura para tenerlo en su propia columna."))
        sizer.Add(casilla, 0, wx.ALL, 10)
        return choice, casilla

    def _casilla_mencionar(self, panel, sizer, red, defecto, nombre_red):
        """Casilla "mencionar al responder" de Bluesky / W Social (estilo Mastodon)."""
        casilla = wx.CheckBox(
            panel, label=_("Al responder, empezar el texto mencionando al autor y a quien él "
                           "menciona (como en Mastodon)"),
            name=_("Mencionar al responder en {0}").format(nombre_red))
        casilla.SetValue(bool(config.get(red, "mencionar_al_responder", defecto)))
        casilla.SetToolTip(_(
            "No hace falta para que se enteren: el servidor ya avisa de tu respuesta a los "
            "autores de encima en el hilo, y cada mención ocupa caracteres. El editor siempre te "
            "dice a quién respondes y quién recibirá el aviso."))
        sizer.Add(casilla, 0, wx.ALL, 10)
        return casilla

    def _panel_bluesky_root(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_bluesky = self._añadir_casilla_servicio(panel, sizer, "bluesky", _("Bluesky"))

        # Tiempo real (firehose): va ENCIMA del intervalo, porque cuando está activo el Inicio se
        # actualiza al instante y el intervalo solo sirve de respaldo.
        self.cb_bluesky_realtime = wx.CheckBox(
            panel, label=_("Tiempo real: actualizar el Inicio al instante (firehose)"),
            name=_("Tiempo real en Bluesky"))
        self.cb_bluesky_realtime.SetValue(bool(config.get("bluesky", "realtime", False)))
        self.cb_bluesky_realtime.SetToolTip(_(
            "Como en Mastodon: en lugar de comprobar cada cierto tiempo, el Inicio se actualiza al "
            "instante cuando una cuenta que sigues publica. Usa el firehose de Bluesky y consume "
            "algo más de CPU y datos; si lo dejas desactivado, se usa el intervalo de abajo."))
        sizer.Add(self.cb_bluesky_realtime, 0, wx.ALL, 10)

        # Intervalo de actualización
        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualización de Bluesky (segundos):")), 0, wx.ALL, 5)
        self.txt_bluesky_interval = AccessibleTextCtrl(
            panel,
            value=str(config.get("bluesky", "update_interval", 60)),
            name=_("Intervalo actualización Bluesky"),
            ayuda_texto=_("Segundos entre comprobaciones automáticas del timeline activo.")
        )
        sizer.Add(self.txt_bluesky_interval, 0, wx.EXPAND | wx.ALL, 5)

        # Publicaciones por carga
        sizer.Add(wx.StaticText(panel, label=_("Publicaciones por carga:")), 0, wx.ALL, 5)
        self.txt_bluesky_limit = AccessibleTextCtrl(
            panel,
            value=str(config.get("bluesky", "limit_per_load", 40)),
            name=_("Publicaciones por carga"),
            ayuda_texto=_("Cuántas publicaciones se piden en cada página (1-100).")
        )
        sizer.Add(self.txt_bluesky_limit, 0, wx.EXPAND | wx.ALL, 5)

        # Recordatorio de texto alternativo
        self.cb_bluesky_alt = wx.CheckBox(panel, label=_("Avisar al publicar si hay imágenes sin descripción (texto alternativo)"), name=_("Recordatorio de texto alternativo en Bluesky"))
        self.cb_bluesky_alt.SetValue(config.get("bluesky", "alt_text_reminder", True))
        sizer.Add(self.cb_bluesky_alt, 0, wx.ALL, 10)

        # Modo de repost
        sizer.Add(wx.StaticText(panel, label=_("Al repostear una publicación:")), 0, wx.ALL, 5)
        self.choice_bluesky_repost = wx.Choice(panel, choices=[_("Preguntar (repostear o citar)"), _("Repostear directamente"), _("Citar siempre")], name=_("Modo de repost"))
        self.choice_bluesky_repost.SetSelection({"ask": 0, "repost": 1, "quote": 2}.get(config.get("bluesky", "repost_mode", "ask"), 0))
        sizer.Add(self.choice_bluesky_repost, 0, wx.EXPAND | wx.ALL, 5)

        # Lectura de reposts y respuestas en las listas
        self.choice_bluesky_formato_repost, self.cb_bluesky_prefijo_respuesta = \
            self._controles_lectura_at(panel, sizer, "bluesky", _("reposteado por"), _("reposteó"),
                                       _("Bluesky"))
        self.cb_bluesky_mencionar = self._casilla_mencionar(panel, sizer, "bluesky", False,
                                                            _("Bluesky"))

        # Quién puede responder por defecto
        sizer.Add(wx.StaticText(panel, label=_("Quién puede responder por defecto:")), 0, wx.ALL, 5)
        self._bluesky_reply_vals = ["everybody", "following", "mentioned", "nobody"]
        self.choice_bluesky_reply = wx.Choice(panel, choices=[_("Todos"), _("A quienes sigo"), _("Solo mencionados"), _("Nadie")], name=_("Control de respuestas por defecto"))
        pred = config.get("bluesky", "default_reply_control", "everybody")
        self.choice_bluesky_reply.SetSelection(self._bluesky_reply_vals.index(pred) if pred in self._bluesky_reply_vals else 0)
        sizer.Add(self.choice_bluesky_reply, 0, wx.EXPAND | wx.ALL, 5)

        # Servidor / PDS (avanzado)
        sizer.Add(wx.StaticText(panel, label=_("Servidor / PDS por defecto (avanzado):")), 0, wx.ALL, 5)
        self.txt_bluesky_pds = AccessibleTextCtrl(
            panel,
            value=str(config.get("bluesky", "pds_url", "https://bsky.social")),
            name=_("Servidor PDS de Bluesky"),
            ayuda_texto=_("Servidor por defecto al añadir cuentas nuevas. Normalmente https://bsky.social.")
        )
        sizer.Add(self.txt_bluesky_pds, 0, wx.EXPAND | wx.ALL, 5)

        self.cb_bsky_details = wx.CheckBox(panel, label=_("Mostrar el panel de detalles de la publicación enfocada"), name=_("Mostrar panel de detalles en Bluesky"))
        self.cb_bsky_details.SetValue(bool(config.get("bluesky", "show_details_panel", True)))
        sizer.Add(self.cb_bsky_details, 0, wx.ALL, 10)

        self.cb_bsky_buttons = wx.CheckBox(panel, label=_("Mostrar la botonera de acciones bajo la lista"), name=_("Mostrar botonera de acciones en Bluesky"))
        self.cb_bsky_buttons.SetValue(bool(config.get("bluesky", "show_action_buttons", False)))
        self.cb_bsky_buttons.SetToolTip(_("Botones que cambian según el buffer (Responder, Me gusta, Repostear, Conversación...) y actúan sobre la publicación enfocada."))
        sizer.Add(self.cb_bsky_buttons, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_bluesky_mantenimiento(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Mantenimiento del histórico (Fase 6). 0 = sin límite, se conserva todo.
        sizer.Add(wx.StaticText(panel, label=_("Máximo de elementos por buffer (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_bsky_max_buffer = wx.SpinCtrl(panel, min=0, max=100000, initial=int(config.get("bluesky", "max_items_buffer", 0)), name=_("Máximo de elementos por buffer de Bluesky"))
        self.spin_bsky_max_buffer.SetToolTip(_("Al arrancar se recortan los buffers de alto volumen (Inicio, Menciones…) a este número, conservando siempre los marcadores. 0 = no se recorta nada."))
        sizer.Add(self.spin_bsky_max_buffer, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Máximo de notificaciones guardadas por cuenta (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_bsky_max_notifs = wx.SpinCtrl(panel, min=0, max=100000, initial=int(config.get("bluesky", "max_notifications", 0)), name=_("Máximo de notificaciones por cuenta de Bluesky"))
        sizer.Add(self.spin_bsky_max_notifs, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_bsky_no_persist = wx.CheckBox(panel, label=_("No conservar publicaciones entre sesiones (vaciar al reiniciar)"),
                                              name=_("No conservar publicaciones entre sesiones en Bluesky"))
        self.cb_bsky_no_persist.SetValue(config.get("bluesky", "no_conservar_entre_sesiones", False))
        self.cb_bsky_no_persist.SetToolTip(_("Si lo activas, al reiniciar la app se vacían los buffers de Inicio, Menciones, feeds, listas, búsquedas y Notificaciones de Bluesky (se conservan Me gusta, Marcadores, Enviados y los mensajes directos). Igual que la opción de Mastodon."))
        sizer.Add(self.cb_bsky_no_persist, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_reddit_root(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_reddit = self._añadir_casilla_servicio(panel, sizer, "reddit", _("Reddit"))

        # Intervalo de actualización (Reddit limita a 100 peticiones/min por client_id).
        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualización de Reddit (segundos, mínimo 30):")), 0, wx.ALL, 5)
        self.txt_reddit_interval = AccessibleTextCtrl(
            panel,
            value=str(config.get("reddit", "update_interval", 120)),
            name=_("Intervalo actualización Reddit"),
            ayuda_texto=_("Segundos entre comprobaciones automáticas del buffer activo. Reddit limita "
                          "a 100 peticiones por minuto por aplicación, así que conviene un intervalo holgado.")
        )
        sizer.Add(self.txt_reddit_interval, 0, wx.EXPAND | wx.ALL, 5)

        # Elementos por carga
        sizer.Add(wx.StaticText(panel, label=_("Elementos por carga (1-100):")), 0, wx.ALL, 5)
        self.txt_reddit_limit = AccessibleTextCtrl(
            panel,
            value=str(config.get("reddit", "limit_per_load", 25)),
            name=_("Elementos por carga"),
            ayuda_texto=_("Cuántas publicaciones se piden en cada página (1-100).")
        )
        sizer.Add(self.txt_reddit_limit, 0, wx.EXPAND | wx.ALL, 5)

        # Orden de comentarios
        sizer.Add(wx.StaticText(panel, label=_("Orden de los comentarios:")), 0, wx.ALL, 5)
        self._reddit_sort_vals = ["confidence", "top", "new", "controversial", "old", "qa"]
        self.choice_reddit_sort = wx.Choice(
            panel, choices=[_("Mejores (confianza)"), _("Más votados"), _("Nuevos"),
                            _("Controvertidos"), _("Antiguos"), _("Preguntas y respuestas")],
            name=_("Orden de los comentarios"))
        pred = config.get("reddit", "comment_sort", "confidence")
        self.choice_reddit_sort.SetSelection(
            self._reddit_sort_vals.index(pred) if pred in self._reddit_sort_vals else 0)
        sizer.Add(self.choice_reddit_sort, 0, wx.EXPAND | wx.ALL, 5)

        # Periodo por defecto para "más votados"/"controvertidos"
        sizer.Add(wx.StaticText(panel, label=_("Periodo por defecto (para Más votados / Controvertidos):")), 0, wx.ALL, 5)
        self._reddit_time_vals = ["hour", "day", "week", "month", "year", "all"]
        self.choice_reddit_time = wx.Choice(
            panel, choices=[_("Última hora"), _("Hoy"), _("Esta semana"), _("Este mes"),
                            _("Este año"), _("Siempre")],
            name=_("Periodo por defecto"))
        predt = config.get("reddit", "time_filter", "day")
        self.choice_reddit_time.SetSelection(
            self._reddit_time_vals.index(predt) if predt in self._reddit_time_vals else 1)
        sizer.Add(self.choice_reddit_time, 0, wx.EXPAND | wx.ALL, 5)

        # Mostrar NSFW
        self.cb_reddit_nsfw = wx.CheckBox(panel, label=_("Mostrar contenido para adultos (+18 / NSFW)"),
                                          name=_("Mostrar contenido para adultos en Reddit"))
        self.cb_reddit_nsfw.SetValue(bool(config.get("reddit", "show_nsfw", False)))
        sizer.Add(self.cb_reddit_nsfw, 0, wx.ALL, 10)

        # Confirmar antes de publicar
        self.cb_reddit_confirm = wx.CheckBox(panel, label=_("Pedir confirmación antes de publicar"),
                                             name=_("Confirmar antes de publicar en Reddit"))
        self.cb_reddit_confirm.SetValue(bool(config.get("reddit", "confirm_post", False)))
        sizer.Add(self.cb_reddit_confirm, 0, wx.ALL, 10)

        # Identificador de aplicación (client id): OBLIGATORIO, uno por usuario, igual que el
        # api_id/api_hash de Telegram. Sin él no se pueden añadir cuentas.
        encab = wx.StaticText(panel, label=_("Aplicación de Reddit (necesaria para añadir cuentas)"))
        _f = encab.GetFont(); _f.SetWeight(wx.FONTWEIGHT_BOLD); encab.SetFont(_f)
        sizer.Add(encab, 0, wx.LEFT | wx.TOP, 12)
        sizer.Add(wx.StaticText(panel, label=_(
            "Reddit exige que cada aplicación se identifique con un \"client id\" (como el api_id de "
            "Telegram). Créalo UNA vez: abre reddit.com/prefs/apps, pulsa \"create another app\", elige "
            "el tipo \"installed app\", pon en \"redirect uri\" exactamente http://localhost:8080 y copia "
            "el identificador que aparece bajo el nombre de la app.")), 0, wx.ALL, 5)
        btn_reddit_apps = wx.Button(panel, label=_("Abrir la página de aplicaciones de Reddit"),
                                    name=_("Abrir reddit.com/prefs/apps en el navegador"))
        def _abrir_reddit_apps(_e):
            from app.core.privacy import abrir_url
            abrir_url("https://www.reddit.com/prefs/apps")
        btn_reddit_apps.Bind(wx.EVT_BUTTON, _abrir_reddit_apps)
        sizer.Add(btn_reddit_apps, 0, wx.ALL, 5)
        sizer.Add(wx.StaticText(panel, label=_("Identificador de aplicación (client id):")), 0, wx.ALL, 5)
        self.txt_reddit_client_id = AccessibleTextCtrl(
            panel,
            value=str(config.get("reddit", "client_id", "")),
            name=_("Identificador de aplicación de Reddit (client id)"),
            ayuda_texto=_("Pega aquí el identificador de tu aplicación de Reddit (installed app). Es "
                          "obligatorio para poder añadir cuentas. Cada usuario usa el suyo, con su "
                          "propio límite de peticiones.")
        )
        sizer.Add(self.txt_reddit_client_id, 0, wx.EXPAND | wx.ALL, 5)

        # Panel de detalles / botonera
        self.cb_reddit_details = wx.CheckBox(panel, label=_("Mostrar el panel de detalles del elemento enfocado"),
                                             name=_("Mostrar panel de detalles en Reddit"))
        self.cb_reddit_details.SetValue(bool(config.get("reddit", "show_details_panel", True)))
        sizer.Add(self.cb_reddit_details, 0, wx.ALL, 10)

        self.cb_reddit_buttons = wx.CheckBox(panel, label=_("Mostrar la botonera de acciones bajo la lista"),
                                             name=_("Mostrar botonera de acciones en Reddit"))
        self.cb_reddit_buttons.SetValue(bool(config.get("reddit", "show_action_buttons", False)))
        sizer.Add(self.cb_reddit_buttons, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_reddit_mantenimiento(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_("Comentarios de primer nivel a cargar por hilo:")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_reddit_comment_limit = wx.SpinCtrl(panel, min=1, max=1000,
            initial=int(config.get("reddit", "comment_limit", 200)),
            name=_("Comentarios por hilo en Reddit"))
        sizer.Add(self.spin_reddit_comment_limit, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("\"Cargar más comentarios\" a resolver por hilo (cada uno es una petición):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_reddit_replace_more = wx.SpinCtrl(panel, min=0, max=200,
            initial=int(config.get("reddit", "replace_more_limit", 32)),
            name=_("Cargar más comentarios en Reddit"))
        sizer.Add(self.spin_reddit_replace_more, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Máximo de elementos por buffer (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_reddit_max_buffer = wx.SpinCtrl(panel, min=0, max=100000,
            initial=int(config.get("reddit", "max_items_buffer", 0)),
            name=_("Máximo de elementos por buffer de Reddit"))
        sizer.Add(self.spin_reddit_max_buffer, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Máximo de mensajes de bandeja guardados (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_reddit_max_msgs = wx.SpinCtrl(panel, min=0, max=100000,
            initial=int(config.get("reddit", "max_notifications", 0)),
            name=_("Máximo de mensajes de bandeja de Reddit"))
        sizer.Add(self.spin_reddit_max_msgs, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_reddit_no_persist = wx.CheckBox(panel, label=_("No conservar publicaciones entre sesiones (vaciar al reiniciar)"),
                                                name=_("No conservar publicaciones entre sesiones en Reddit"))
        self.cb_reddit_no_persist.SetValue(config.get("reddit", "no_conservar_entre_sesiones", False))
        sizer.Add(self.cb_reddit_no_persist, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    # ==================================================================
    # PeerTube (vídeo federado): raíz + subsecciones por categoría.
    # ==================================================================
    def _panel_peertube_root(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_peertube = self._añadir_casilla_servicio(panel, sizer, "peertube", _("PeerTube"))

        sizer.Add(wx.StaticText(panel, label=_(
            "Las cuentas de PeerTube se añaden en Archivo → Gestionar Cuentas → Añadir Cuenta →\n"
            "Cuenta de PeerTube (dirección de la instancia, usuario y contraseña; la sesión se\n"
            "guarda cifrada). Puedes tener varias cuentas en varias instancias."
        )), 0, wx.ALL, 8)

        # PeerTube no tiene envío push: se sondea por intervalo (mínimo 30 s).
        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualización de PeerTube (segundos, mínimo 30):")), 0, wx.ALL, 5)
        self.txt_peertube_interval = AccessibleTextCtrl(
            panel, value=str(config.get("peertube", "update_interval", 180)),
            name=_("Intervalo actualización PeerTube"),
            ayuda_texto=_("Segundos entre comprobaciones automáticas del catálogo o sección activa. "
                          "PeerTube no envía avisos en tiempo real, así que se consulta cada tanto."))
        sizer.Add(self.txt_peertube_interval, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("Vídeos por carga (1-100):")), 0, wx.ALL, 5)
        self.txt_peertube_limit = AccessibleTextCtrl(
            panel, value=str(config.get("peertube", "limit_per_load", 25)),
            name=_("Vídeos por carga"),
            ayuda_texto=_("Cuántos vídeos se piden en cada página (al abrir una sección y al 'cargar más')."))
        sizer.Add(self.txt_peertube_limit, 0, wx.EXPAND | wx.ALL, 5)

        panel.SetSizer(sizer)
        return panel

    def _panel_peertube_reproduccion(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Calidad preferida (de un vídeo suena el audio; la menor calidad basta y gasta menos datos).
        sizer.Add(wx.StaticText(panel, label=_("Calidad preferida de reproducción:")), 0, wx.LEFT | wx.TOP, 10)
        self._peertube_res_vals = ["auto", "1080", "720", "480", "360", "240"]
        self.choice_peertube_res = wx.Choice(panel, choices=[
            _("Automática (menor ancho de banda)"), _("1080p"), _("720p"), _("480p"), _("360p"),
            _("240p")], name=_("Calidad preferida de PeerTube"))
        _r = str(config.get("peertube", "preferred_resolution", "auto"))
        self.choice_peertube_res.SetSelection(
            self._peertube_res_vals.index(_r) if _r in self._peertube_res_vals else 0)
        self.choice_peertube_res.SetToolTip(_("Como de un vídeo solo suena el audio, la calidad más "
                                              "baja suele bastar y consume menos datos."))
        sizer.Add(self.choice_peertube_res, 0, wx.EXPAND | wx.ALL, 6)

        # Idioma preferido de subtítulos: se preselecciona al abrir "Ver subtítulos".
        sizer.Add(wx.StaticText(panel, label=_("Idioma preferido de subtítulos (código, p. ej. es, en):")), 0, wx.LEFT | wx.TOP, 10)
        self.txt_peertube_sub = AccessibleTextCtrl(
            panel, value=str(config.get("peertube", "preferred_subtitle_lang", "es")),
            name=_("Idioma preferido de subtítulos"),
            ayuda_texto=_("Al elegir 'Ver subtítulos', si el vídeo tiene ese idioma, se preselecciona."))
        sizer.Add(self.txt_peertube_sub, 0, wx.EXPAND | wx.ALL, 6)

        self.cb_peertube_resume = wx.CheckBox(
            panel, label=_("Continuar la reproducción donde la dejé"),
            name=_("Continuar reproducción de PeerTube"))
        self.cb_peertube_resume.SetValue(bool(config.get("peertube", "save_position", True)))
        self.cb_peertube_resume.SetToolTip(_("Al volver a reproducir un vídeo, empieza donde lo "
                                             "dejaste (no aplica a los directos)."))
        sizer.Add(self.cb_peertube_resume, 0, wx.ALL, 10)

        # Carpeta de descargas (vacío = preguntar cada vez).
        sizer.Add(wx.StaticText(panel, label=_("Carpeta de descargas (vacío = preguntar cada vez):")), 0, wx.LEFT | wx.TOP, 10)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_peertube_downloads = AccessibleTextCtrl(
            panel, value=str(config.get("peertube", "download_dir", "")),
            name=_("Carpeta de descargas de PeerTube"),
            ayuda_texto=_("Dónde se guardan los vídeos descargados. Si lo dejas vacío, se pregunta al descargar."))
        b_dir = wx.Button(panel, label=_("&Examinar..."))

        def _elegir_dir(_e):
            with wx.DirDialog(panel, _("Carpeta de descargas de PeerTube"),
                              style=wx.DD_DEFAULT_STYLE) as dd:
                if dd.ShowModal() == wx.ID_OK:
                    self.txt_peertube_downloads.SetValue(dd.GetPath())
        b_dir.Bind(wx.EVT_BUTTON, _elegir_dir)
        fila.Add(self.txt_peertube_downloads, 1, wx.EXPAND | wx.RIGHT, 4)
        fila.Add(b_dir, 0)
        sizer.Add(fila, 0, wx.EXPAND | wx.ALL, 6)

        panel.SetSizer(sizer)
        return panel

    def _panel_peertube_contenido(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Política de contenido sensible (+18).
        sizer.Add(wx.StaticText(panel, label=_("Contenido para adultos (+18):")), 0, wx.LEFT | wx.TOP, 10)
        self._peertube_nsfw_vals = ["do_not_list", "blur", "display"]
        self.choice_peertube_nsfw = wx.Choice(panel, choices=[
            _("No mostrar (ocultarlo de las listas)"),
            _("Mostrar avisando de que es +18"),
            _("Mostrar todo")], name=_("Política de contenido +18 en PeerTube"))
        _n = str(config.get("peertube", "nsfw_policy", "do_not_list"))
        self.choice_peertube_nsfw.SetSelection(
            self._peertube_nsfw_vals.index(_n) if _n in self._peertube_nsfw_vals else 0)
        sizer.Add(self.choice_peertube_nsfw, 0, wx.EXPAND | wx.ALL, 6)

        # Privacidad por defecto al subir/importar vídeos.
        sizer.Add(wx.StaticText(panel, label=_("Privacidad por defecto al subir o importar vídeos:")), 0, wx.LEFT | wx.TOP, 10)
        self._peertube_priv_vals = [1, 2, 3, 4]
        self.choice_peertube_priv = wx.Choice(panel, choices=[
            _("Público"), _("No listado"), _("Privado"), _("Interno")],
            name=_("Privacidad por defecto en PeerTube"))
        _p = int(config.get("peertube", "default_privacy", 1) or 1)
        self.choice_peertube_priv.SetSelection(
            self._peertube_priv_vals.index(_p) if _p in self._peertube_priv_vals else 0)
        sizer.Add(self.choice_peertube_priv, 0, wx.EXPAND | wx.ALL, 6)

        self.cb_peertube_details = wx.CheckBox(
            panel, label=_("Mostrar el panel de detalles del vídeo enfocado"),
            name=_("Mostrar panel de detalles en PeerTube"))
        self.cb_peertube_details.SetValue(bool(config.get("peertube", "show_details_panel", True)))
        sizer.Add(self.cb_peertube_details, 0, wx.ALL, 10)

        self.cb_peertube_buttons = wx.CheckBox(
            panel, label=_("Mostrar la botonera de acciones bajo la lista"),
            name=_("Mostrar botonera de acciones en PeerTube"))
        self.cb_peertube_buttons.SetValue(bool(config.get("peertube", "show_action_buttons", False)))
        self.cb_peertube_buttons.SetToolTip(_("Botones que actúan sobre el vídeo enfocado (Ver "
                                              "detalles, Abrir en el navegador) y un botón con el menú completo."))
        sizer.Add(self.cb_peertube_buttons, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_peertube_mantenimiento(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_("Máximo de vídeos por sección (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_peertube_max_buffer = wx.SpinCtrl(panel, min=0, max=100000,
            initial=int(config.get("peertube", "max_items_buffer", 0)),
            name=_("Máximo de vídeos por sección de PeerTube"))
        self.spin_peertube_max_buffer.SetToolTip(_("Al arrancar se recorta cada sección a este número "
                                                   "de vídeos (los más recientes). 0 = no se recorta."))
        sizer.Add(self.spin_peertube_max_buffer, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_peertube_no_persist = wx.CheckBox(
            panel, label=_("No conservar los vídeos entre sesiones (vaciar al reiniciar)"),
            name=_("No conservar vídeos de PeerTube entre sesiones"))
        self.cb_peertube_no_persist.SetValue(bool(config.get("peertube", "no_conservar_entre_sesiones", False)))
        self.cb_peertube_no_persist.SetToolTip(_("Al reiniciar la app se vacían los vídeos cacheados "
                                                 "de PeerTube (se vuelven a pedir a la instancia al usar). No afecta a tu sesión ni a tus "
                                                 "búsquedas guardadas."))
        sizer.Add(self.cb_peertube_no_persist, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_rss_root(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_rss = self._añadir_casilla_servicio(panel, sizer, "rss", _("Lector RSS"))

        # Intervalo actualización RSS
        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualización de RSS (minutos):")), 0, wx.ALL, 5)
        self.txt_rss_interval = AccessibleTextCtrl(
            panel,
            value=str(config.get("rss", "update_interval", 30)),
            name=_("Intervalo actualización RSS"),
            ayuda_texto=_("Minutos entre descargas automáticas de nuevos artículos (mínimo 1).")
        )
        sizer.Add(self.txt_rss_interval, 0, wx.EXPAND | wx.ALL, 5)

        # Descargar texto completo
        self.cb_fulltext = wx.CheckBox(panel, label=_("Intentar descargar siempre el texto completo del artículo"), name=_("Descargar contenido completo"))
        self.cb_fulltext.SetValue(config.get("rss", "download_fulltext", False))
        sizer.Add(self.cb_fulltext, 0, wx.ALL, 10)

        # El artículo completo, el extractor y las suscripciones de pago están en el
        # subpanel "Artículo completo y suscripciones" (prefs_rss_articulo.py).

        # Alertas sonoras RSS
        self.cb_rss_notify = wx.CheckBox(panel, label=_("Habilitar notificaciones y sonidos para nuevos artículos RSS"), name=_("Notificaciones RSS"))
        self.cb_rss_notify.SetValue(config.get("rss", "notifications_enabled", True))
        sizer.Add(self.cb_rss_notify, 0, wx.ALL, 10)

        # Imágenes remotas en el visor (auditoría RSS-06): desactivado, las <img> se
        # sustituyen por su texto alternativo y no se pide nada al servidor del feed.
        self.cb_rss_imagenes = wx.CheckBox(
            panel, label=_("Mostrar imágenes remotas en el visor de artículos (menos privacidad)"),
            name=_("Imágenes remotas en el visor"))
        self.cb_rss_imagenes.SetValue(bool(config.get("rss", "download_images", False)))
        self.cb_rss_imagenes.SetToolTip(_(
            "Desactivado, las imágenes se sustituyen por su texto alternativo y el visor no "
            "carga nada del servidor del feed (evita píxeles de seguimiento)."))
        sizer.Add(self.cb_rss_imagenes, 0, wx.LEFT | wx.BOTTOM, 10)

        # Descargas en paralelo
        sizer.Add(wx.StaticText(panel, label=_("Feeds a actualizar a la vez (1-50):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_rss_parallel = wx.SpinCtrl(panel, min=1, max=50, initial=int(config.get("rss", "max_parallel_updates", 5)), name=_("Feeds simultáneos al actualizar"))
        self.spin_rss_parallel.SetToolTip(_("Cuántos feeds se descargan simultáneamente al pulsar 'Actualizar todos'. Más rápido, pero usa más red."))
        sizer.Add(self.spin_rss_parallel, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_rss_details = wx.CheckBox(panel, label=_("Mostrar el panel de vista previa del artículo enfocado"), name=_("Mostrar panel de detalles en RSS"))
        self.cb_rss_details.SetValue(bool(config.get("rss", "show_details_panel", True)))
        sizer.Add(self.cb_rss_details, 0, wx.ALL, 10)

        self.cb_rss_buttons = wx.CheckBox(panel, label=_("Mostrar la botonera de acciones bajo la lista"), name=_("Mostrar botonera de acciones en RSS"))
        self.cb_rss_buttons.SetValue(bool(config.get("rss", "show_action_buttons", False)))
        self.cb_rss_buttons.SetToolTip(_("Botones que actúan sobre el artículo enfocado (marcar leído, destacar...) y un botón 'Acciones...' con todo el menú."))
        sizer.Add(self.cb_rss_buttons, 0, wx.ALL, 10)

        # --- Lectura de artículos ---
        sizer.Add(wx.StaticText(panel, label=_("Lectura de artículos:")), 0, wx.LEFT | wx.TOP, 10)
        # Qué hace Intro sobre un artículo (también en la bandeja de entrada).
        sizer.Add(wx.StaticText(panel, label=_("Al pulsar Intro sobre un artículo:")), 0, wx.LEFT | wx.TOP, 10)
        self._rss_intro_vals = ("navegador", "lector", "preguntar")
        self.choice_rss_intro = wx.Choice(panel, choices=[
            _("Abrir el enlace en el navegador (como hasta ahora)"),
            _("Abrir el lector limpio"),
            _("Preguntar cada vez"),
        ], name=_("Al pulsar Intro sobre un artículo"))
        _actual_intro = config.get("rss", "enter_action", "navegador")
        self.choice_rss_intro.SetSelection(self._rss_intro_vals.index(_actual_intro)
                                           if _actual_intro in self._rss_intro_vals else 0)
        self.choice_rss_intro.SetToolTip(_("Vale en los buffers de RSS y en la bandeja de entrada. "
                                           "El lector limpio también se abre siempre con Ctrl+Mayús+E."))
        sizer.Add(self.choice_rss_intro, 0, wx.EXPAND | wx.ALL, 5)
        # Opciones que ya existían pero no tenían control en Preferencias.
        self.cb_rss_leido_al_abrir = wx.CheckBox(
            panel, label=_("Marcar el artículo como leído al abrirlo"),
            name=_("Marcar como leído al abrir"))
        self.cb_rss_leido_al_abrir.SetValue(bool(config.get("rss", "mark_read_on_open", True)))
        sizer.Add(self.cb_rss_leido_al_abrir, 0, wx.ALL, 10)
        sizer.Add(wx.StaticText(panel, label=_("Espacio máximo para podcasts descargados (MB, 0 = sin límite):")),
                  0, wx.LEFT | wx.TOP, 10)
        try:
            _mib = int(config.get("rss", "podcast_max_mib", 4096))
        except (TypeError, ValueError):
            _mib = 4096
        self.spin_rss_podcast_mib = wx.SpinCtrl(panel, min=0, max=1048576, initial=max(0, _mib),
                                                name=_("Espacio máximo para podcasts descargados, en megas"))
        sizer.Add(self.spin_rss_podcast_mib, 0, wx.ALL, 5)
        self.cb_rss_completo_vista = wx.CheckBox(
            panel, label=_("En la vista previa, descargar el artículo completo cuando el feed solo trae un resumen"),
            name=_("Artículo completo en la vista previa"))
        self.cb_rss_completo_vista.SetValue(bool(config.get("rss", "fulltext_en_vista_previa", True)))
        self.cb_rss_completo_vista.SetToolTip(_(
            "Solo si el panel de vista previa está visible: al quedarte un momento en un artículo, "
            "se descarga entero de la web (una vez; queda guardado) y el panel se actualiza solo."))
        sizer.Add(self.cb_rss_completo_vista, 0, wx.ALL, 10)
        self.cb_rss_html_mode = wx.CheckBox(panel, label=_("Abrir el lector directamente en vista HTML"), name=_("Abrir en HTML"))
        self.cb_rss_html_mode.SetValue(bool(config.get("rss", "default_html_mode", False)))
        self.cb_rss_html_mode.SetToolTip(_("Al abrir un artículo, mostrarlo en la vista HTML accesible en lugar de la de texto. Puedes alternar con Ctrl+H."))
        sizer.Add(self.cb_rss_html_mode, 0, wx.ALL, 10)

        self.cb_rss_tts_auto = wx.CheckBox(panel, label=_("Empezar a leer en voz alta automáticamente al abrir un artículo"), name=_("Autoarranque de lectura por voz"))
        self.cb_rss_tts_auto.SetValue(bool(config.get("rss", "tts_autostart", False)))
        self.cb_rss_tts_auto.SetToolTip(_("Abre el lector de voz (TTS) y comienza a leer el artículo nada más abrirlo."))
        sizer.Add(self.cb_rss_tts_auto, 0, wx.ALL, 10)

        self.cb_rss_tts_pos = wx.CheckBox(panel, label=_("Recordar por dónde iba la lectura en voz alta de cada artículo"), name=_("Recordar posición de lectura por voz"))
        self.cb_rss_tts_pos.SetValue(bool(config.get("rss", "remember_tts_position", True)))
        self.cb_rss_tts_pos.SetToolTip(_("Al reabrir un artículo, ofrece continuar la lectura en voz alta donde la dejaste."))
        sizer.Add(self.cb_rss_tts_pos, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_rss_mantenimiento(self, parent) -> wx.Panel:
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Conservar artículos X días
        sizer.Add(wx.StaticText(panel, label=_("Conservar artículos (días, 0 = para siempre):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_rss_keep_days = wx.SpinCtrl(panel, min=0, max=3650, initial=int(config.get("rss", "keep_articles_days", 0)), name=_("Días que se conservan los artículos"))
        self.spin_rss_keep_days.SetToolTip(_("Los artículos más antiguos se eliminan al limpiar. Nunca se borran los destacados ni los marcados para leer más tarde."))
        sizer.Add(self.spin_rss_keep_days, 0, wx.LEFT | wx.BOTTOM, 10)

        # Máximo de artículos por feed
        sizer.Add(wx.StaticText(panel, label=_("Máximo de artículos por feed (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_rss_max_feed = wx.SpinCtrl(panel, min=0, max=100000, initial=int(config.get("rss", "max_articles_per_feed", 0)), name=_("Máximo de artículos por feed"))
        self.spin_rss_max_feed.SetToolTip(_("Al actualizar, cada feed conserva como mucho este número de artículos (los más recientes), salvo destacados y leer más tarde."))
        sizer.Add(self.spin_rss_max_feed, 0, wx.LEFT | wx.BOTTOM, 10)

        # No conservar entre sesiones (igual que Mastodon/Bluesky/X): vaciar al reiniciar.
        self.cb_rss_no_persist = wx.CheckBox(panel, label=_("No conservar artículos entre sesiones (vaciar al reiniciar)"),
                                             name=_("No conservar artículos RSS entre sesiones"))
        self.cb_rss_no_persist.SetValue(bool(config.get("rss", "no_conservar_entre_sesiones", False)))
        self.cb_rss_no_persist.SetToolTip(_("Al reiniciar la app se vacían los artículos de RSS para que la base de datos no crezca sin fin. Se CONSERVAN los destacados, los marcados para leer más tarde y los episodios de podcast descargados."))
        sizer.Add(self.cb_rss_no_persist, 0, wx.LEFT | wx.BOTTOM, 10)

        # Corte de importación al limpiar: si se marca, cuando limpies TODOS los datos de RSS los
        # feeds no volverán a bajar su histórico entero; solo traerán lo publicado a partir de ese
        # momento. Es justo lo que evita que, al limpiar, se repita todo el backlog.
        self.cb_rss_corte_limpiar = wx.CheckBox(
            panel, label=_("Al limpiar los datos de RSS, descargar solo artículos nuevos a partir de ese momento"),
            name=_("Solo artículos nuevos tras limpiar RSS"))
        self.cb_rss_corte_limpiar.SetValue(bool(config.get("rss", "corte_al_limpiar", False)))
        self.cb_rss_corte_limpiar.SetToolTip(_(
            "Cuando vacías por completo los datos de RSS, cada feed queda marcado con la fecha de la "
            "limpieza: al volver a descargar, NO se re-importa todo el histórico, solo se traen los "
            "artículos publicados a partir de ese momento. Los feeds que añadas DESPUÉS de la limpieza "
            "sí traen sus artículos como siempre. Si lo dejas desmarcado, al limpiar se vuelve a bajar "
            "todo lo que el feed publique en su archivo (comportamiento anterior)."))
        sizer.Add(self.cb_rss_corte_limpiar, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_irc_root(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_irc = self._añadir_casilla_servicio(panel, sizer, "irc", _("IRC"))

        sizer.Add(wx.StaticText(panel, label=_(
            "Las redes IRC se dan de alta en Archivo → Gestionar Cuentas → Añadir Cuenta → Red IRC.\n"
            "Cada red guarda su servidor, apodo, canales y contraseñas (cifradas), y puede\n"
            "conectarse automáticamente al abrir la aplicación."
        )), 0, wx.ALL, 8)

        sizer.Add(wx.StaticText(panel, label=_("Días a conservar el historial de mensajes (0 = para siempre):")), 0, wx.ALL, 5)
        self.txt_irc_history = AccessibleTextCtrl(
            panel, value=str(config.get("irc", "history_days", 0)),
            name=_("Días de historial de IRC"),
            ayuda_texto=_("Los mensajes más antiguos se borran solos al conectar. 0 los conserva todos."))
        sizer.Add(self.txt_irc_history, 0, wx.EXPAND | wx.ALL, 5)

        # Ocultar entradas/salidas (join/part/quit) en TODOS los canales, para que no tapen los
        # mensajes. (Además, por canal, con /events on|off en la conversación.)
        self.cb_irc_ocultar_eventos = wx.CheckBox(
            panel, label=_("Ocultar en todos los canales las entradas y salidas (fulano entró/salió, "
                           "conexiones y desconexiones)"),
            name=_("Ocultar entradas y salidas de IRC"))
        self.cb_irc_ocultar_eventos.SetValue(bool(config.get("irc", "ocultar_eventos", False)))
        sizer.Add(self.cb_irc_ocultar_eventos, 0, wx.ALL, 8)

        panel.SetSizer(sizer)
        return panel

    def _panel_matrix_root(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_matrix = self._añadir_casilla_servicio(panel, sizer, "matrix", _("Matrix"))

        sizer.Add(wx.StaticText(panel, label=_(
            "Las cuentas de Matrix se añaden en Archivo → Gestionar Cuentas → Añadir Cuenta →\n"
            "Cuenta de Matrix (servidor, usuario y contraseña; la sesión queda guardada cifrada)."
        )), 0, wx.ALL, 8)

        sizer.Add(wx.StaticText(panel, label=_("Días a conservar el historial de mensajes (0 = para siempre):")), 0, wx.ALL, 5)
        self.txt_matrix_history = AccessibleTextCtrl(
            panel, value=str(config.get("matrix", "history_days", 0)),
            name=_("Días de historial de Matrix"),
            ayuda_texto=_("Los mensajes más antiguos se borran solos al conectar. 0 los conserva todos."))
        sizer.Add(self.txt_matrix_history, 0, wx.EXPAND | wx.ALL, 5)

        # Herramientas de administración del SERVIDOR (solo si tu cuenta es admin de un servidor
        # Synapse). Si se activa, aparece un submenú "Administración" en el menú Matrix, en caliente.
        self.cb_matrix_admin = wx.CheckBox(
            panel, label=_("Activar herramientas de administración del servidor (solo administradores)"),
            name=_("Herramientas de administración de Matrix"))
        self.cb_matrix_admin.SetValue(bool(config.get("matrix", "admin_tools", False)))
        self.cb_matrix_admin.SetToolTip(_(
            "Si tu cuenta ADMINISTRA un servidor Synapse (no solo una sala), añade un submenú "
            "'Administración' al menú Matrix con usuarios, salas, mensajes reportados, tokens de "
            "registro y estadísticas del servidor. Se añade o se quita al instante al marcar o "
            "desmarcar esta casilla. Si tu servidor no es Synapse o tu cuenta no es administradora, "
            "las acciones avisarán de que el servidor las rechaza."))
        sizer.Add(self.cb_matrix_admin, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_xmpp_root(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_xmpp = self._añadir_casilla_servicio(panel, sizer, "xmpp", _("XMPP (Jabber)"))

        sizer.Add(wx.StaticText(panel, label=_(
            "Las cuentas de XMPP se añaden en Archivo → Gestionar Cuentas → Añadir Cuenta →\n"
            "Cuenta de XMPP (Jabber): JID (usuario@servidor) y contraseña. La conexión va siempre\n"
            "cifrada (TLS) y la sesión se guarda cifrada en tu equipo."
        )), 0, wx.ALL, 8)

        sizer.Add(wx.StaticText(panel, label=_("Días a conservar el historial de mensajes (0 = para siempre):")), 0, wx.ALL, 5)
        self.txt_xmpp_history = AccessibleTextCtrl(
            panel, value=str(config.get("xmpp", "history_days", 0)),
            name=_("Días de historial de XMPP"),
            ayuda_texto=_("Los mensajes más antiguos se borran solos al conectar. 0 los conserva todos."))
        sizer.Add(self.txt_xmpp_history, 0, wx.EXPAND | wx.ALL, 5)

        # Herramientas de administración del servidor (comandos ad-hoc 0133/0050 + registro 0077).
        # Si se activa, aparece un submenú "Administración" en el menú XMPP, en caliente.
        self.cb_xmpp_admin = wx.CheckBox(
            panel, label=_("Activar herramientas de administración del servidor (solo administradores)"),
            name=_("Herramientas de administración de XMPP"))
        self.cb_xmpp_admin.SetValue(bool(config.get("xmpp", "admin_tools", False)))
        self.cb_xmpp_admin.SetToolTip(_(
            "Añade un submenú 'Administración' al menú XMPP con los comandos que ofrece tu servidor "
            "(añadir/borrar usuarios, anuncios, estadísticas…), ejecutar cualquier comando ad-hoc, "
            "cambiar tu contraseña, crear una cuenta nueva y cancelar (borrar) tu cuenta. Los "
            "comandos de administración solo funcionan si tu cuenta administra el servidor; el resto "
            "los ofrece cualquier servidor que lo permita. Se añade o se quita al instante."))
        sizer.Add(self.cb_xmpp_admin, 0, wx.ALL, 10)

        # Buffers separados por puente (WhatsApp, Discord…). Si se activa (por defecto), cada pasarela
        # en la que te registres tiene su propio grupo de Conversaciones/Contactos/Grupos en el árbol.
        self.cb_xmpp_puentes = wx.CheckBox(
            panel, label=_("Crear buffers separados por cada puente (WhatsApp, Discord…)"),
            name=_("Buffers separados por puente"))
        self.cb_xmpp_puentes.SetValue(not bool(config.get("xmpp", "sin_buffers_por_puente", False)))
        self.cb_xmpp_puentes.SetToolTip(_(
            "Si usas pasarelas/puentes (WhatsApp, Discord…), cada uno aparece como su propio grupo en "
            "el árbol de XMPP con sus Conversaciones, Contactos y Grupos, para no mezclarlos entre sí "
            "ni con los contactos de tu servidor. Desactívalo para verlo todo junto en los buffers "
            "generales."))
        sizer.Add(self.cb_xmpp_puentes, 0, wx.ALL, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_telegram_root(self, parent) -> wx.Panel:
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_serv_telegram = self._añadir_casilla_servicio(panel, sizer, "telegram", _("Telegram"))

        sizer.Add(wx.StaticText(panel, label=_("Intervalo de actualización de Telegram (segundos, mínimo 10):")), 0, wx.ALL, 5)
        self.txt_telegram_interval = AccessibleTextCtrl(
            panel, value=str(config.get("telegram", "update_interval", 60)),
            name=_("Intervalo actualización Telegram"),
            ayuda_texto=_("Segundos entre comprobaciones automáticas de los chats."))
        sizer.Add(self.txt_telegram_interval, 0, wx.EXPAND | wx.ALL, 5)

        from app.services.telegram.telegram_service import telegram_service

        sizer.Add(wx.StaticText(panel, label=_(
            "Para usar Telegram necesita un api_id y api_hash propios.\n"
            "Obténgalos gratis iniciando sesión en https://my.telegram.org, sección 'API development tools'."
        )), 0, wx.ALL, 5)

        api_id, api_hash = telegram_service.get_api_credentials()

        sizer.Add(wx.StaticText(panel, label=_("api_id (numérico):")), 0, wx.ALL, 5)
        self.txt_tg_api_id = AccessibleTextCtrl(
            panel,
            value=api_id or "",
            name=_("api_id de Telegram"),
            ayuda_texto=_("Identificador numérico de su aplicación de Telegram obtenido en my.telegram.org.")
        )
        sizer.Add(self.txt_tg_api_id, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("api_hash:")), 0, wx.ALL, 5)
        self.txt_tg_api_hash = AccessibleTextCtrl(
            panel,
            value=api_hash or "",
            style=wx.TE_PASSWORD,
            name=_("api_hash de Telegram"),
            ayuda_texto=_("Cadena secreta de su aplicación de Telegram. Se guarda de forma segura.")
        )
        sizer.Add(self.txt_tg_api_hash, 0, wx.EXPAND | wx.ALL, 5)

        # Carpeta de descargas
        sizer.Add(wx.StaticText(panel, label=_("Carpeta de descargas de Telegram:")), 0, wx.ALL, 5)
        self.txt_tg_downloads = AccessibleTextCtrl(
            panel,
            value=config.get("telegram", "download_dir", ""),
            name=_("Carpeta de descargas de Telegram"),
            ayuda_texto=_("Ruta donde se guardarán fotos, audios y documentos descargados. Vacío usa data/downloads.")
        )
        sizer.Add(self.txt_tg_downloads, 0, wx.EXPAND | wx.ALL, 5)

        # Organizar descargas en subcarpetas
        self.cb_tg_subdirs = wx.CheckBox(panel, label=_("Organizar descargas en subcarpetas por chat y tipo"), name=_("Organizar descargas en subcarpetas"))
        self.cb_tg_subdirs.SetValue(config.get("telegram", "download_subdirs", True))
        sizer.Add(self.cb_tg_subdirs, 0, wx.ALL, 5)

        # Anunciar mensajes nuevos (SOLO en chats de usuario, no grupos/canales)
        self.cb_tg_announce = wx.CheckBox(
            panel, label=_("Anunciar por voz los mensajes nuevos (solo de usuarios, no de grupos, supergrupos ni canales)"),
            name=_("Anunciar mensajes nuevos de Telegram"))
        self.cb_tg_announce.SetValue(config.get("telegram", "announce_new", True))
        self.cb_tg_announce.SetToolTip(_(
            "Lee en voz el remitente y el texto de los mensajes directos de una persona. No se "
            "anuncian los mensajes de grupos, supergrupos ni canales (serían un goteo constante)."))
        sizer.Add(self.cb_tg_announce, 0, wx.ALL, 5)

        # Reproducir automáticamente las notas de voz / audios que lleguen mientras tienes el chat
        # abierto (como otros clientes de Telegram).
        self.cb_tg_autoplay_voice = wx.CheckBox(
            panel, label=_("Reproducir automáticamente las notas de voz y audios que lleguen al chat abierto"),
            name=_("Reproducir automáticamente las notas de voz"))
        self.cb_tg_autoplay_voice.SetValue(bool(config.get("telegram", "autoplay_voice", False)))
        self.cb_tg_autoplay_voice.SetToolTip(_(
            "Si lo activas, las notas de voz y audios se reproducen en cadena: al terminar uno pasa "
            "solo al siguiente (tuyo o de quien te escribe) hasta que no quede ninguno. Pausas y "
            "reanudas con Espacio. Si lo desactivas, reproduces una a una con Espacio."))
        sizer.Add(self.cb_tg_autoplay_voice, 0, wx.ALL, 5)

        # Marcar mensajes como leídos al pasar el foco por ellos (estado de leído como otros clientes).
        self.cb_tg_mark_read = wx.CheckBox(
            panel, label=_("Marcar los mensajes como leídos al pasar por ellos"),
            name=_("Marcar leídos al navegar"))
        self.cb_tg_mark_read.SetValue(bool(config.get("telegram", "mark_read_on_focus", True)))
        self.cb_tg_mark_read.SetToolTip(_(
            "Al situarte sobre un mensaje (o reproducir su audio) se marca como leído, y al salir se "
            "guarda hasta dónde llegaste. Si lo desactivas, los chats no se marcarán leídos solos."))
        sizer.Add(self.cb_tg_mark_read, 0, wx.ALL, 5)

        # Mostrar el aviso "(nuevo)" delante de los mensajes sin leer.
        self.cb_tg_unread_marker = wx.CheckBox(
            panel, label=_("Señalar con \"(nuevo)\" los mensajes sin leer"),
            name=_("Marcar los mensajes sin leer"))
        self.cb_tg_unread_marker.SetValue(bool(config.get("telegram", "show_unread_marker", True)))
        sizer.Add(self.cb_tg_unread_marker, 0, wx.ALL, 5)

        # Fila separadora "Mensajes no leídos" antes del primer mensaje sin leer (como Unigram).
        self.cb_tg_unread_divider = wx.CheckBox(
            panel, label=_("Poner una marca \"Mensajes no leídos\" antes del primero sin leer"),
            name=_("Marca de mensajes no leídos"))
        self.cb_tg_unread_divider.SetValue(bool(config.get("telegram", "show_unread_divider", True)))
        self.cb_tg_unread_divider.SetToolTip(_(
            "Al abrir un chat con mensajes sin leer, inserta una línea separadora justo antes del "
            "primero que no has leído, como hace Unigram, para ver de un vistazo por dónde te "
            "quedaste. No es un mensaje: no tiene acciones y el foco se coloca en el mensaje."))
        sizer.Add(self.cb_tg_unread_divider, 0, wx.ALL, 5)

        # Sincronizar TODO el historial al abrir un chat (no solo lo que hubiera en la base de datos).
        self.cb_tg_sync_todo = wx.CheckBox(
            panel, label=_("Sincronizar TODO el historial al abrir un chat (traer todo del servidor)"),
            name=_("Sincronizar todo el historial al abrir"))
        self.cb_tg_sync_todo.SetValue(bool(config.get("telegram", "sincronizar_todo_al_abrir", False)))
        self.cb_tg_sync_todo.SetToolTip(_(
            "Al abrir un chat, grupo o canal, trae TODO su historial desde el servidor de Telegram, no "
            "solo lo que tengas guardado. Así, aunque hayas vaciado o borrado la base de datos, "
            "recuperas todo. Solo se recorta cuando TÚ vacías el chat (y hasta donde Telegram lo "
            "permita). En chats muy grandes puede tardar en cargar."))
        sizer.Add(self.cb_tg_sync_todo, 0, wx.ALL, 5)

        # Mantenimiento del histórico (Fase 6). 0 = sin límite, se conserva todo.
        sizer.Add(wx.StaticText(panel, label=_("Máximo de mensajes en caché por chat (0 = sin límite):")), 0, wx.LEFT | wx.TOP, 10)
        self.spin_max_tg_msgs = wx.SpinCtrl(panel, min=0, max=100000, initial=int(config.get("telegram", "max_messages_per_chat", 0)), name=_("Máximo de mensajes por chat de Telegram"))
        self.spin_max_tg_msgs.SetToolTip(_("Los mensajes más antiguos se quitan de la caché local (se vuelven a pedir al abrir el chat). 0 = no se recorta nada."))
        sizer.Add(self.spin_max_tg_msgs, 0, wx.LEFT | wx.BOTTOM, 10)

        # Telegram no tiene panel de detalles (los chats se abren en su ventana de conversación),
        # pero sí botonera de acciones bajo la lista de chats.
        self.cb_tg_buttons = wx.CheckBox(panel, label=_("Mostrar la botonera de acciones bajo la lista de chats"), name=_("Mostrar botonera de acciones en Telegram"))
        self.cb_tg_buttons.SetValue(bool(config.get("telegram", "show_action_buttons", False)))
        self.cb_tg_buttons.SetToolTip(_("Botones sobre el chat enfocado: abrir la conversación y un botón 'Acciones...' con el menú contextual."))
        sizer.Add(self.cb_tg_buttons, 0, wx.LEFT | wx.BOTTOM, 10)

        # Aviso si faltan dependencias de Telegram
        from app.services.telegram import telegram_service as _tgmod
        if not _tgmod.HAS_KURIGRAM:
            aviso = wx.StaticText(panel, label=_(
                "AVISO: La librería de Telegram (kurigram) no está instalada. "
                "Instale las dependencias desde build.bat para activar Telegram."
            ))
            sizer.Add(aviso, 0, wx.ALL, 5)

        panel.SetSizer(sizer)
        return panel

    def _panel_telegram_drive(self, parent) -> wx.Panel:
        """Subsección de Telegram: opciones de Telegram Drive (la nube personal). Es un nodo
        HIJO de Telegram en el árbol de preferencias, con sus ajustes agrupados aparte."""
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_(
            "Telegram Drive guarda tus archivos en un canal privado de Telegram (solo tuyo). Se "
            "abre desde el menú Telegram → «Telegram Drive (tu nube personal)...».")), 0, wx.ALL, 8)

        sizer.Add(wx.StaticText(panel, label=_("Carpeta de descargas de la nube (vacío = carpeta por defecto):")), 0, wx.ALL, 5)
        self.txt_tgdrive_downloads = AccessibleTextCtrl(
            panel, value=config.get("telegram_drive", "carpeta_descargas", ""),
            name=_("Carpeta de descargas de Telegram Drive"))
        sizer.Add(self.txt_tgdrive_downloads, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label=_("Transferencias simultáneas (1 = una a una; hasta 10 a la vez):")), 0, wx.ALL, 5)
        self.spin_tgdrive_simult = wx.SpinCtrl(panel, min=1, max=10,
            initial=int(config.get("telegram_drive", "max_simultaneas", 1)),
            name=_("Transferencias simultáneas de Telegram Drive"))
        self.spin_tgdrive_simult.SetToolTip(_("Cuántas subidas o descargas van a la vez. Más rápido en total, "
                                              "pero un valor muy alto puede dar problemas de red o esperas de Telegram."))
        sizer.Add(self.spin_tgdrive_simult, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Al repetirse un nombre al subir:")), 0, wx.ALL, 5)
        self._tgdrive_conf_valores = ["preguntar", "reemplazar", "ambos", "omitir"]
        self.choice_tgdrive_conflicto = wx.Choice(panel, choices=[
            _("Preguntar siempre"), _("Reemplazar"), _("Conservar ambos"), _("Omitir")],
            name=_("Política ante nombres repetidos"))
        pol = config.get("telegram_drive", "politica_conflicto", "preguntar")
        self.choice_tgdrive_conflicto.SetSelection(
            self._tgdrive_conf_valores.index(pol) if pol in self._tgdrive_conf_valores else 0)
        sizer.Add(self.choice_tgdrive_conflicto, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Días que se conservan los elementos en la papelera (0 = para siempre):")), 0, wx.ALL, 5)
        self.spin_tgdrive_papelera = wx.SpinCtrl(panel, min=0, max=3650,
            initial=int(config.get("telegram_drive", "retencion_papelera_dias", 30)),
            name=_("Días de retención de la papelera de Telegram Drive"))
        sizer.Add(self.spin_tgdrive_papelera, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_tgdrive_confirmar = wx.CheckBox(panel, label=_("Pedir confirmación antes de enviar a la papelera"),
            name=_("Confirmar borrado en Telegram Drive"))
        self.cb_tgdrive_confirmar.SetValue(bool(config.get("telegram_drive", "confirmar_borrado", True)))
        sizer.Add(self.cb_tgdrive_confirmar, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_tgdrive_limpiar = wx.CheckBox(panel, label=_("Limpiar los archivos temporales al terminar cada transferencia"),
            name=_("Limpiar temporales de Telegram Drive"))
        self.cb_tgdrive_limpiar.SetValue(bool(config.get("telegram_drive", "limpiar_temporales", True)))
        sizer.Add(self.cb_tgdrive_limpiar, 0, wx.LEFT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(panel, label=_("Anunciar el progreso cada (segundos):")), 0, wx.ALL, 5)
        self.spin_tgdrive_progreso = wx.SpinCtrl(panel, min=1, max=60,
            initial=int(config.get("telegram_drive", "frecuencia_progreso_seg", 5)),
            name=_("Frecuencia de anuncio de progreso de Telegram Drive"))
        self.spin_tgdrive_progreso.SetToolTip(_("El inicio, la pausa, la cancelación, los errores y el fin se anuncian siempre."))
        sizer.Add(self.spin_tgdrive_progreso, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _panel_microfono(self, parent) -> wx.Panel:
        """Panel COMPARTIDO del micrófono (Preferencias → Sonidos → Micrófono). El micrófono lo
        usan varias redes para grabar notas de voz (Telegram, Matrix, XMPP...), por eso vive aquí
        y no dentro de una red concreta. Se guarda en la sección de config 'audio'."""
        panel = wx.ScrolledWindow(parent)
        panel.SetScrollRate(0, 10)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label=_("Micrófono para grabar notas de voz (común a "
                                               "Telegram, Matrix, XMPP...):")), 0, wx.ALL, 5)
        self.choice_mic = wx.Choice(panel, name=_("Selector de micrófono"))
        sizer.Add(self.choice_mic, 0, wx.EXPAND | wx.ALL, 5)
        self._mics = []
        self._cargar_microfonos()

        btn_mic_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_mic = wx.Button(panel, label=_("&Actualizar lista de micrófonos"), name=_("Volver a detectar micrófonos"))
        self.btn_refresh_mic.Bind(wx.EVT_BUTTON, lambda e: self._cargar_microfonos())
        btn_mic_sizer.Add(self.btn_refresh_mic, 0, wx.ALL, 5)
        self.btn_test_mic = wx.Button(panel, label=_("&Probar micrófono"), name=_("Grabar una prueba y reproducirla"))
        self.btn_test_mic.Bind(wx.EVT_BUTTON, self._on_probar_microfono)
        btn_mic_sizer.Add(self.btn_test_mic, 0, wx.ALL, 5)
        sizer.Add(btn_mic_sizer, 0, wx.ALL, 2)

        sizer.Add(wx.StaticText(panel, label=_("Volumen del micrófono (porcentaje):")), 0, wx.ALL, 5)
        from app.services.audio import microphone
        _dev, _vol = microphone.config_microfono()
        self.slider_mic_vol = AccessibleSlider(
            panel, value=int(float(_vol) * 100),
            minValue=0, maxValue=200, name=_("Volumen del micrófono"), unidad="%"
        )
        sizer.Add(self.slider_mic_vol, 0, wx.EXPAND | wx.ALL, 5)

        panel.SetSizer(sizer)
        return panel

    def _cargar_microfonos(self):
        """Detecta los micrófonos disponibles y rellena el selector."""
        from app.services.audio import microphone
        try:
            self._mics = microphone.listar_microfonos()
        except Exception:
            self._mics = []
        opciones = [_("(Predeterminado del sistema)")] + self._mics
        self.choice_mic.SetItems(opciones)
        # Seleccionar el guardado (sección 'audio', con respaldo a la antigua 'telegram').
        guardado = microphone.config_microfono()[0]
        if guardado and guardado in self._mics:
            self.choice_mic.SetSelection(self._mics.index(guardado) + 1)
        else:
            self.choice_mic.SetSelection(0)
        if not self._mics:
            notificar(_("No se detectaron micrófonos. Asegúrese de tener ffmpeg en data/bin."))

    def _mic_seleccionado(self) -> str:
        """Devuelve el nombre del micrófono elegido, o cadena vacía si predeterminado."""
        idx = self.choice_mic.GetSelection()
        if idx <= 0:
            return ""
        return self._mics[idx - 1]

    def _on_probar_microfono(self, event):
        """Graba unos segundos de prueba con el micrófono y volumen elegidos y los reproduce."""
        from app.core.task_manager import task_manager
        from app.services.audio import microphone
        from app.services.media_player.player_service import player_service
        dispositivo = self._mic_seleccionado()
        volumen = self.slider_mic_vol.GetValue() / 100.0
        speak(_("Grabando 4 segundos de prueba. Hable ahora..."), interrupt=True)
        self.btn_test_mic.Disable()

        def _bg(ctx):
            return microphone.grabar_prueba(dispositivo or None, volumen, segundos=4)

        def _finish(res, err):
            self.btn_test_mic.Enable()
            if err:
                notificar(_("Fallo en la prueba: {0}").format(err))
            else:
                speak(_("Reproduciendo la prueba grabada."), interrupt=True)
                player_service.cargar_y_reproducir(res, _("Prueba de micrófono"))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)
