# -*- coding: utf-8 -*-
"""Preferencias, RSS, "Artículo completo y suscripciones".

- Descargar el artículo completo al abrir el lector cuando el feed solo trae un resumen.
- Qué extractor saca el texto de la página (automático, Trafilatura, Readability, JSON-LD).
- Medios de pago en los que estás suscrito: sus artículos se descargan con tu sesión del
  navegador (ver app/services/rss/rss_suscripciones.py).
"""
import wx

from app.core.config_manager import config
from .accessible_controls import AccessibleTextCtrl, mostrar_info


class _PrefRssArticuloMixin:
    """Subpanel de RSS: artículo completo, extractor y suscripciones de pago."""

    _NOMBRES_NAVEGADOR = {
        "firefox": "Firefox (recomendado)", "chrome": "Google Chrome", "edge": "Microsoft Edge",
        "brave": "Brave", "opera": "Opera",
    }

    def _panel_rss_articulo(self, parent) -> wx.Panel:
        from app.services.rss.rss_fulltext import MOTORES_EXTRACCION
        from app.services.rss.rss_suscripciones import NAVEGADORES
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.cb_fulltext_al_abrir = wx.CheckBox(
            panel, label=_("Al abrir el lector, descargar el artículo completo si el feed solo trae un resumen"),
            name=_("Artículo completo al abrir el lector"))
        self.cb_fulltext_al_abrir.SetValue(bool(config.get("rss", "fulltext_on_open", True)))
        self.cb_fulltext_al_abrir.SetToolTip(_(
            "Muchos feeds solo traen el primer párrafo. Activado, el lector descarga la página y "
            "muestra la noticia entera en cuanto llega; se hace una vez por artículo y queda guardado."))
        sizer.Add(self.cb_fulltext_al_abrir, 0, wx.ALL, 10)

        self.cb_fulltext_sitios = wx.CheckBox(
            panel, label=_("Hilos de foros con sus comentarios y vídeos de YouTube con su transcripción"),
            name=_("Artículo completo de foros y de YouTube"))
        self.cb_fulltext_sitios.SetValue(bool(config.get("rss", "fulltext_sitios", True)))
        self.cb_fulltext_sitios.SetToolTip(_(
            "Si el artículo es un hilo de Lemmy, Discourse o Reddit (con tu cuenta de Reddit), el "
            "artículo completo incluye todos los comentarios. Si es un vídeo de YouTube, la "
            "descripción, los capítulos y la transcripción."))
        sizer.Add(self.cb_fulltext_sitios, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.cb_reddit_sesion = wx.CheckBox(
            panel, label=_("Leer los hilos de Reddit con mi sesión de Reddit del navegador"),
            name=_("Hilos de Reddit con la sesión del navegador"))
        self.cb_reddit_sesion.SetValue(bool(config.get("rss", "reddit_browser_session", True)))
        self.cb_reddit_sesion.SetToolTip(_(
            "Reddit ya no deja leer los comentarios sin iniciar sesión. Se usan las cookies de "
            "reddit.com del navegador elegido abajo (Firefox), solo para Reddit y sin guardarlas."))
        sizer.Add(self.cb_reddit_sesion, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.cb_busquedas_arbol = wx.CheckBox(
            panel, label=_("Mostrar las búsquedas guardadas como buffers del árbol"),
            name=_("Búsquedas guardadas en el árbol"))
        self.cb_busquedas_arbol.SetValue(bool(config.get("rss", "saved_searches_in_tree", True)))
        sizer.Add(self.cb_busquedas_arbol, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        fila_al = wx.BoxSizer(wx.HORIZONTAL)
        fila_al.Add(wx.StaticText(panel, label=_("Avisos de alertas por palabra clave en cada actualización (el resto, en un resumen):")),
                    0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        self.spin_tope_alertas = wx.SpinCtrl(panel, min=1, max=50,
                                             initial=int(config.get("rss", "max_alert_notifications", 3)),
                                             name=_("Máximo de avisos de alertas por actualización"))
        fila_al.Add(self.spin_tope_alertas, 0, wx.ALL, 4)
        sizer.Add(fila_al, 0)

        self._motores_extraccion = list(MOTORES_EXTRACCION)
        sizer.Add(wx.StaticText(panel, label=_("Extractor del artículo completo:")), 0, wx.LEFT | wx.TOP, 10)
        self.choice_rss_extractor = wx.Choice(panel, choices=[
            _("Automático: compara todos y elige el mejor (recomendado)"),
            _("Trafilatura: el más completo, conserva subtítulo y primeros párrafos"),
            _("Readability: el algoritmo clásico, parecido a la vista de lectura de Firefox"),
            _("Datos estructurados de la página (JSON-LD)"),
        ], name=_("Extractor del artículo completo"))
        motor = config.get("rss", "fulltext_extractor", "auto")
        self.choice_rss_extractor.SetSelection(
            self._motores_extraccion.index(motor) if motor in self._motores_extraccion else 0)
        self.choice_rss_extractor.SetToolTip(_(
            "Cómo se saca el texto de la noticia de la página web. Si el extractor elegido no "
            "consigue nada en una página, se prueban los demás antes de rendirse."))
        sizer.Add(self.choice_rss_extractor, 0, wx.LEFT | wx.BOTTOM, 10)

        # --- Suscripciones de pago ---
        sizer.Add(wx.StaticText(panel, label=_(
            "Medios de pago en los que estás suscrito (uno por línea, por ejemplo elpais.com):")),
            0, wx.LEFT | wx.TOP, 10)
        self.txt_rss_suscripciones = AccessibleTextCtrl(
            panel, value="\n".join(config.get("rss", "subscription_domains", []) or []),
            style=wx.TE_MULTILINE, size=(-1, 90),
            name=_("Medios de pago en los que estás suscrito"),
            ayuda_texto=_(
                "Los artículos de estos sitios se descargan con TU sesión del navegador, así que el "
                "lector recibe la noticia completa que ya pagas. Inicia sesión en la web del medio con "
                "ese navegador. Las cookies se leen solo al descargar un artículo de ese sitio, no se "
                "guardan y solo se envían a ese medio."))
        sizer.Add(self.txt_rss_suscripciones, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self._navegadores = list(NAVEGADORES)
        sizer.Add(wx.StaticText(panel, label=_("Navegador en el que tienes iniciada la sesión:")),
                  0, wx.LEFT | wx.TOP, 10)
        self.choice_rss_navegador = wx.Choice(
            panel, choices=[self._NOMBRES_NAVEGADOR[n] for n in self._navegadores],
            name=_("Navegador de la sesión de suscripción"))
        nav = config.get("rss", "subscription_browser", "firefox")
        self.choice_rss_navegador.SetSelection(self._navegadores.index(nav) if nav in self._navegadores else 0)
        self.choice_rss_navegador.SetToolTip(_(
            "Firefox es el que funciona de forma fiable. Chrome, Edge, Brave y Opera cifran sus "
            "cookies desde 2024 y normalmente no se pueden leer desde otra aplicación."))
        sizer.Add(self.choice_rss_navegador, 0, wx.LEFT | wx.BOTTOM, 10)

        btn_probar = wx.Button(panel, label=_("&Probar la sesión en estos medios"),
                               name=_("Probar la sesión de suscripción"))
        btn_probar.Bind(wx.EVT_BUTTON, self._on_probar_suscripciones)
        sizer.Add(btn_probar, 0, wx.LEFT | wx.BOTTOM, 10)

        # --- Reglas de medios compatibles (Bypass Paywalls Clean) ---
        sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.ALL, 10)
        sizer.Add(wx.StaticText(panel, label=_(
            "Reglas de medios compatibles (Bypass Paywalls Clean):")), 0, wx.LEFT | wx.TOP, 10)

        sizer.Add(wx.StaticText(panel, label=_(
            "&Estado y versión de las reglas:")), 0, wx.LEFT | wx.TOP, 10)

        from app.services.rss.bpc_rules import BPCRulesEngine
        motor_bpc = BPCRulesEngine.obtener()
        texto_inicial = self._generar_texto_info_bpc(motor_bpc)

        self.txt_bpc_info = AccessibleTextCtrl(
            panel,
            value=texto_inicial,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(-1, 70),
            name=_("Estado y versión de las reglas de medios compatibles (Bypass Paywalls Clean)"),
            ayuda_texto=_(
                "Muestra la versión instalada, fecha de publicación y número de medios compatibles "
                "de las reglas de Bypass Paywalls Clean."))
        sizer.Add(self.txt_bpc_info, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        bpc_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_bpc_actualizar = wx.Button(
            panel, label=_("&Buscar actualizaciones de reglas..."),
            name=_("Buscar actualizaciones de reglas BPC"))
        self.btn_bpc_actualizar.Bind(wx.EVT_BUTTON, self._on_buscar_actualizaciones_bpc)
        bpc_btn_sizer.Add(self.btn_bpc_actualizar, 0, wx.RIGHT, 10)

        self.btn_bpc_revertir = wx.Button(
            panel, label=_("&Restaurar reglas de fábrica"),
            name=_("Restaurar reglas de fábrica BPC"))
        self.btn_bpc_revertir.Bind(wx.EVT_BUTTON, self._on_revertir_reglas_bpc)
        bpc_btn_sizer.Add(self.btn_bpc_revertir, 0, wx.RIGHT, 10)
        sizer.Add(bpc_btn_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        self.cb_bpc_check_auto = wx.CheckBox(
            panel,
            label=_("Comprobar actualizaciones de reglas periódicamente al iniciar"),
            name=_("Comprobación automática de reglas BPC"))
        self.cb_bpc_check_auto.SetValue(bool(config.get("rss", "bpc_check_updates_auto", False)))
        self.cb_bpc_check_auto.SetToolTip(_(
            "Consulta el repositorio oficial al iniciar la aplicación para mantener "
            "actualizadas las reglas de los medios digitales compatibles."))
        sizer.Add(self.cb_bpc_check_auto, 0, wx.LEFT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        return panel

    def _generar_texto_info_bpc(self, motor_bpc) -> str:
        """Construye el texto informativo accesible para el cuadro de versión y estado de BPC."""
        lineas = [
            _("Versión instalada: {0} ({1})").format(motor_bpc.version, motor_bpc.fecha_version),
            _("Catálogo: {0} medios compatibles configurados ({1} con muro cerrado sin solución).").format(
                len(motor_bpc.sitios_por_dominio), len(motor_bpc.sitios_nofix)),
        ]
        if motor_bpc.sitios_actualizados:
            lineas.append(_("Reglas actualizadas activas: {0} medios con ajustes recientes.").format(
                len(motor_bpc.sitios_actualizados)))
        else:
            lineas.append(_("Estado: Utilizando reglas base oficiales."))
        return "\n".join(lineas)

    def _actualizar_etiqueta_bpc(self):
        """Actualiza el texto descriptivo del estado de las reglas BPC."""
        if not hasattr(self, "txt_bpc_info"):
            return
        from app.services.rss.bpc_rules import BPCRulesEngine
        motor_bpc = BPCRulesEngine.obtener()
        self.txt_bpc_info.SetValue(self._generar_texto_info_bpc(motor_bpc))

    def _on_buscar_actualizaciones_bpc(self, event):
        """Busca y aplica actualizaciones de reglas BPC en segundo plano."""
        from app.core.speech import speak
        from app.services.rss.bpc_updater import BPCUpdater
        self.btn_bpc_actualizar.Disable()
        speak(_("Buscando actualizaciones de reglas de medios..."), interrupt=True)

        def _fin(info, error):
            if not self:
                return
            self.btn_bpc_actualizar.Enable()
            if error:
                mostrar_info(self, _("No se pudo comprobar la actualización de reglas: {0}").format(error),
                             _("Reglas de medios"))
                speak(_("Error al comprobar reglas."), interrupt=True)
                return

            if info.get("aplicada"):
                self._actualizar_etiqueta_bpc()
                detalles = "\n".join(f"• {s}" for s in (info.get("sitios_detalle", []) or [])[:10])
                if len(info.get("sitios_detalle", []) or []) > 10:
                    detalles += f"\n... y {len(info['sitios_detalle']) - 10} más."
                cuerpo = _("Se han actualizado las reglas correctamente a la versión {0}.\n\n"
                           "{1}\n\nMedios actualizados:\n{2}").format(
                    info.get("version_remota", ""), info.get("mensaje_aplicacion", ""), detalles)
                mostrar_info(self, cuerpo, _("Reglas actualizadas"))
                speak(_("Reglas de medios actualizadas con éxito."), interrupt=True)
            elif info.get("hay_actualizacion") and info.get("es_nueva_version_app"):
                cuerpo = _("Existe una nueva versión mayor de reglas en el repositorio ({0}).\n\n"
                           "Para incorporar nuevos scripts y adaptadores ejecutables, "
                           "se recomienda actualizar la aplicación completa.").format(
                    info.get("version_remota", ""))
                mostrar_info(self, cuerpo, _("Nueva versión disponible"))
                speak(_("Nueva versión de reglas disponible."), interrupt=True)
            else:
                mostrar_info(self, info.get("mensaje") or _("Las reglas ya están al día."),
                             _("Reglas de medios"))
                speak(_("Las reglas ya están al día."), interrupt=True)

        BPCUpdater.comprobar_y_actualizar_async(_fin)

    def _on_revertir_reglas_bpc(self, event):
        """Restaura las reglas a la copia previa o a la versión original de fábrica."""
        from app.services.rss.bpc_updater import BPCUpdater
        from app.core.speech import speak
        dlg = wx.MessageDialog(
            self,
            _("¿Deseas restaurar las reglas a su versión anterior o de fábrica?"),
            _("Restaurar reglas de medios"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
        if dlg.ShowModal() != wx.ID_YES:
            dlg.Destroy()
            return
        dlg.Destroy()

        ok, msg = BPCUpdater.revertir_version_anterior()
        self._actualizar_etiqueta_bpc()
        mostrar_info(self, msg, _("Reglas de medios"))
        speak(msg, interrupt=True)

    def _dominios_del_campo(self):
        from app.services.rss.rss_suscripciones import normalizar_dominio
        texto = self.txt_rss_suscripciones.GetValue().replace(",", "\n")
        vistos = []
        for linea in texto.splitlines():
            d = normalizar_dominio(linea)
            if d and d not in vistos:
                vistos.append(d)
        return vistos

    def _on_probar_suscripciones(self, event):
        """Lee (sin guardar nada) cuántas cookies de sesión hay en el navegador para cada
        medio, y lo cuenta en un mensaje."""
        from app.services.rss.rss_suscripciones import probar_sesiones
        from app.core.task_manager import task_manager
        from app.core.speech import speak
        dominios = self._dominios_del_campo()
        if not dominios:
            mostrar_info(self, _("Escribe primero al menos un medio, por ejemplo elpais.com."),
                         _("Suscripciones"))
            return
        sel = self.choice_rss_navegador.GetSelection()
        navegador = self._navegadores[sel] if 0 <= sel < len(self._navegadores) else "firefox"
        speak(_("Comprobando la sesión en el navegador..."), interrupt=True)

        def _fin(resultado, error):
            if not self:
                return
            if error or not resultado:
                mostrar_info(self, _("No se pudo comprobar: {0}").format(error or ""), _("Suscripciones"))
                return
            lineas = []
            for dominio, n, fallo in resultado:
                if fallo:
                    lineas.append(_("{0}: no se pudieron leer las cookies ({1}).").format(dominio, fallo))
                elif n:
                    lineas.append(_("{0}: sesión encontrada ({1} cookies).").format(dominio, n))
                else:
                    lineas.append(_("{0}: no hay sesión. Inicia sesión en su web con ese navegador.").format(dominio))
            mostrar_info(self, "\n".join(lineas), _("Suscripciones"))

        task_manager.start_task(lambda ctx: probar_sesiones(dominios, navegador), callback=_fin,
                                pass_context=True)

    def _guardar_rss_articulo(self):
        """Guarda este subpanel (solo si llegó a crearse)."""
        if not hasattr(self, "cb_fulltext_al_abrir"):
            return
        config.set("rss", "fulltext_on_open", self.cb_fulltext_al_abrir.GetValue())
        config.set("rss", "fulltext_sitios", self.cb_fulltext_sitios.GetValue())
        config.set("rss", "reddit_browser_session", self.cb_reddit_sesion.GetValue())
        config.set("rss", "saved_searches_in_tree", self.cb_busquedas_arbol.GetValue())
        config.set("rss", "max_alert_notifications", int(self.spin_tope_alertas.GetValue()))
        sel = self.choice_rss_extractor.GetSelection()
        if 0 <= sel < len(self._motores_extraccion):
            config.set("rss", "fulltext_extractor", self._motores_extraccion[sel])
        config.set("rss", "subscription_domains", self._dominios_del_campo())
        sel = self.choice_rss_navegador.GetSelection()
        if 0 <= sel < len(self._navegadores):
            config.set("rss", "subscription_browser", self._navegadores[sel])
        if hasattr(self, "cb_bpc_check_auto"):
            config.set("rss", "bpc_check_updates_auto", self.cb_bpc_check_auto.GetValue())
        from app.services.rss.rss_suscripciones import olvidar_sesiones
        olvidar_sesiones()
