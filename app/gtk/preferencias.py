# -*- coding: utf-8 -*-
"""Preferencias en pestañas: General, Inteligencia artificial y Traducción.

Las claves de configuración son las mismas que usa la versión de Windows, así que lo que se
ajuste aquí vale para ambas. Nada se escribe en disco hasta pulsar Guardar; "Cancelar" o Escape
descartan los cambios."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.i18n import NOMBRES_IDIOMA
from app.core.speech import notificar
from app.core.task_manager import task_manager

from .ventana_botonera import REDES_BOTONERA, REDES_DETALLES
from .dialogos_ia import DialogoHistorialIA, DialogoHistorialTraducciones, DialogoPrompts

log = logging.getLogger(__name__)

IDIOMAS = list(NOMBRES_IDIOMA.keys())      # códigos ISO de 2 letras
PT_NSFW = [("do_not_list", "No mostrarlo"), ("blur", "Mostrarlo difuminado"), ("display", "Mostrarlo")]
PT_PRIVACIDAD = [(1, "Público"), (2, "No listado"), (3, "Privado"), (4, "Interno")]
RSS_INTRO = [("navegador", "Abrir el enlace en el navegador (como hasta ahora)"), ("lector", "Abrir el lector limpio"),
             ("preguntar", "Preguntar cada vez")]
RD_ORDEN_COMENTARIOS = [("confidence", "Mejores (confianza)"), ("top", "Más votados"), ("new", "Nuevos"),
                        ("controversial", "Controvertidos"), ("old", "Antiguos"), ("qa", "Preguntas y respuestas")]
RD_PERIODOS = [("hour", "Última hora"), ("day", "Hoy"), ("week", "Esta semana"), ("month", "Este mes"),
               ("year", "Este año"), ("all", "Siempre")]


def _nombre_idioma(codigo: str) -> str:
    return f"{_(NOMBRES_IDIOMA.get(codigo, codigo))} ({codigo})"


class _Pagina(Gtk.ScrolledWindow):
    """Una pestaña: rejilla de 'etiqueta + campo' con la etiqueta enlazada al campo (Alt+letra)."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, min_content_height=380, min_content_width=620)
        self.rejilla = Gtk.Grid(row_spacing=8, column_spacing=10, margin_top=12, margin_bottom=12,
                                margin_start=12, margin_end=12)
        self.set_child(self.rejilla)
        self._fila = 0

    def campo(self, etiqueta: str, widget, ayuda: str = ""):
        lbl = Gtk.Label(label=etiqueta, use_underline=True, xalign=0, yalign=0, wrap=True)
        lbl.set_mnemonic_widget(widget)
        widget.set_hexpand(True)
        if ayuda:
            widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [ayuda])
        self.rejilla.attach(lbl, 0, self._fila, 1, 1)
        self.rejilla.attach(widget, 1, self._fila, 1, 1)
        self._fila += 1
        return lbl, widget

    def botones(self, *pares):
        caja = Gtk.Box(spacing=8)
        for etiqueta, fn in pares:
            b = Gtk.Button(label=etiqueta, use_underline=True)
            b.connect("clicked", lambda *_a, f=fn: f())
            caja.append(b)
        self.rejilla.attach(caja, 1, self._fila, 1, 1)
        self._fila += 1
        return caja

    @staticmethod
    def interruptor(valor: bool):
        s = Gtk.Switch(halign=Gtk.Align.START)
        s.set_active(bool(valor))
        return s

    @staticmethod
    def texto_largo(valor: str = "", alto: int = 100):
        tv = Gtk.TextView(accepts_tab=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        tv.get_buffer().set_text(valor)
        return tv, Gtk.ScrolledWindow(child=tv, min_content_height=alto, has_frame=True, hexpand=True)


def _leer(tv: Gtk.TextView) -> str:
    b = tv.get_buffer()
    return b.get_text(b.get_start_iter(), b.get_end_iter(), False)


class DialogoPreferencias(Gtk.Window):
    def __init__(self, padre):
        super().__init__(title=_("Preferencias"), transient_for=padre, modal=True)
        self.padre = padre
        self.set_default_size(720, 560)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        self.set_child(caja)
        self.cuaderno = Gtk.Notebook(vexpand=True)
        self.cuaderno.update_property([Gtk.AccessibleProperty.LABEL], [_("Categorías de preferencias")])
        caja.append(self.cuaderno)
        self._crear_general()
        self._crear_rss()
        self._crear_ia()
        self._crear_traduccion()

        botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancelar = Gtk.Button(label=_("_Cancelar"), use_underline=True)
        cancelar.connect("clicked", lambda *_a: self.close())
        self.guardar = Gtk.Button(label=_("_Guardar"), use_underline=True)
        self.guardar.add_css_class("suggested-action")
        self.guardar.connect("clicked", lambda *_a: self.aceptar())
        botones.append(cancelar)
        botones.append(self.guardar)
        caja.append(botones)
        self.set_default_widget(self.guardar)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)

    def _pestana(self, titulo: str, pagina: _Pagina):
        etiqueta = Gtk.Label(label=titulo, use_underline=True)
        self.cuaderno.append_page(pagina, etiqueta)

    # ------------------------------------------------------------------ RSS
    def _crear_rss(self):
        p = _Pagina()
        self.rss_intervalo = Gtk.SpinButton.new_with_range(1, 1440, 5)
        self.rss_intervalo.set_value(int(config.get("rss", "update_interval", 30) or 30))
        p.campo(_("_Minutos entre las descargas automáticas de artículos:"), self.rss_intervalo,
                _("Cada feed se vuelve a pedir cuando pasa este tiempo desde la última vez. Un feed puede tener el suyo propio "
                  "en «Editar el feed»."))
        self.rss_notificar = p.interruptor(config.get("rss", "notifications_enabled", True))
        p.campo(_("A_visar de los artículos nuevos:"), self.rss_notificar)
        self.rss_siempre_completo = p.interruptor(config.get("rss", "download_fulltext", False))
        p.campo(_("Intentar descargar siempre el texto _completo del artículo:"), self.rss_siempre_completo)
        self.rss_intro = Gtk.DropDown.new_from_strings([_(n) for _v, n in RSS_INTRO])
        actual = config.get("rss", "enter_action", "navegador")
        self.rss_intro.set_selected(next((i for i, (v, _n) in enumerate(RSS_INTRO) if v == actual), 0))
        p.campo(_("Al pulsar _Intro sobre un artículo:"), self.rss_intro,
                _("Vale en los buffers de RSS y en la bandeja de entrada."))
        self.rss_leido_al_abrir = p.interruptor(config.get("rss", "mark_read_on_open", True))
        p.campo(_("Marcar el artículo como leído al a_brirlo:"), self.rss_leido_al_abrir)
        self.rss_imagenes = p.interruptor(config.get("rss", "download_images", False))
        p.campo(_("Mostrar _imágenes remotas en los artículos (menos privacidad):"), self.rss_imagenes,
                _("Desactivado, las imágenes se sustituyen por su texto alternativo y no se pide nada al servidor del feed."))
        self.rss_ocultar_futuros = p.interruptor(config.get("rss", "hide_future_articles", False))
        p.campo(_("Ocultar los artículos anclados con fecha _futura:"), self.rss_ocultar_futuros)
        self.rss_busquedas_arbol = p.interruptor(config.get("rss", "saved_searches_in_tree", True))
        p.campo(_("Mostrar las búsquedas guardadas en el árbol:"), self.rss_busquedas_arbol)
        self.rss_agrupar = p.interruptor(config.get("rss", "group_by_date", False))
        p.campo(_("Agrupar los artículos por _fecha:"), self.rss_agrupar,
                _("La primera fila de cada bloque (Hoy, Ayer, Esta semana, Este mes) lleva su etiqueta delante."))
        self.rss_tope_alertas = Gtk.SpinButton.new_with_range(1, 50, 1)
        self.rss_tope_alertas.set_value(int(config.get("rss", "max_alert_notifications", 3) or 3))
        p.campo(_("Máximo de avisos de alertas por palabra clave en cada _descarga:"), self.rss_tope_alertas)
        try:
            mib = int(config.get("rss", "podcast_max_mib", 4096))
        except (TypeError, ValueError):
            mib = 4096
        self.rss_podcast_mib = Gtk.SpinButton.new_with_range(0, 1048576, 256)
        self.rss_podcast_mib.set_value(max(0, mib))
        p.campo(_("Espacio máximo para _podcasts descargados (MB, 0 = sin límite):"), self.rss_podcast_mib)
        self.rss_dias = Gtk.SpinButton.new_with_range(0, 3650, 10)
        self.rss_dias.set_value(int(config.get("rss", "keep_articles_days", 0) or 0))
        p.campo(_("Conservar los artículos (_días, 0 = para siempre):"), self.rss_dias,
                _("Los más antiguos se eliminan al limpiar. Nunca se borran los destacados ni los marcados para leer más tarde."))
        self.rss_max_feed = Gtk.SpinButton.new_with_range(0, 100000, 50)
        self.rss_max_feed.set_value(int(config.get("rss", "max_articles_per_feed", 0) or 0))
        p.campo(_("Máximo de artículos por _feed (0 = sin límite):"), self.rss_max_feed)
        self.rss_no_conservar = p.interruptor(config.get("rss", "no_conservar_entre_sesiones", False))
        p.campo(_("No conservar artículos entre _sesiones (vaciar al reiniciar):"), self.rss_no_conservar,
                _("Se conservan los destacados, los marcados para leer más tarde y los podcasts descargados."))
        self.rss_corte = p.interruptor(config.get("rss", "corte_al_limpiar", False))
        p.campo(_("Al limpiar los datos de RSS, bajar solo artículos _nuevos a partir de ese momento:"), self.rss_corte)
        self._pestana(_("_RSS"), p)

    # ------------------------------------------------------------------ General
    def _crear_general(self):
        p = _Pagina()
        self.sonidos = p.interruptor(config.get("accessibility", "enable_sounds", True))
        p.campo(_("_Sonidos de la aplicación:"), self.sonidos)
        self.voz = p.interruptor(config.get("accessibility", "speech_announcements", True))
        p.campo(_("_Avisos al lector de pantalla:"), self.voz)
        self.completo = p.interruptor(config.get("rss", "fulltext_auto", True))
        p.campo(_("Descargar el artículo completo al a_brirlo:"), self.completo)
        self.solo_no_leidos = p.interruptor(config.get("rss", "show_only_unread", False))
        p.campo(_("Mostrar solo no _leídos en carpetas y feeds:"), self.solo_no_leidos)
        self.botonera = {}
        for clave, nombre in REDES_BOTONERA:
            self.botonera[clave] = p.interruptor(config.get(clave, "show_action_buttons", False))
            p.campo(_("Mostrar la botonera de acciones en {n}:").format(n=nombre), self.botonera[clave],
                    _("Botones bajo la lista (Responder, Me gusta...) que actúan sobre el elemento enfocado. Se llega con Tab.")
                    if clave == "rss" else "")
        self.detalles = {}
        for clave, nombre in REDES_DETALLES:
            self.detalles[clave] = p.interruptor(config.get(clave, "show_details_panel", clave != "mastodon"))
            p.campo(_("Mostrar el panel de detalles en {n}:").format(n=nombre), self.detalles[clave],
                    _("El panel de lectura bajo la lista. Desactivado se oculta; Intro o Alt+3 lo enseñan hasta que cambies de buffer.")
                    if clave == "rss" else "")
        self.bandeja = p.interruptor(config.get("inbox", "enabled", False))
        p.campo(_("Mostrar la _bandeja de entrada unificada:"), self.bandeja,
                _("Un buffer que reúne publicaciones de varios buffers y redes que elijas. Se añaden desde el menú Ver "
                  "o con el menú del buffer en el árbol."))
        self.tiempo_real = p.interruptor(config.get("gtk", "mastodon_realtime", True))
        p.campo(_("_Tiempo real en Mastodon:"), self.tiempo_real)
        self.tiempo_real_bsky = p.interruptor(config.get("gtk", "bluesky_realtime", True))
        p.campo(_("Tiempo real en Blues_ky:"), self.tiempo_real_bsky)
        self.tiempo_real_ws = p.interruptor(config.get("gtk", "wsocial_realtime", True))
        p.campo(_("Avisos de novedades en _W Social:"), self.tiempo_real_ws)
        self.ws_intervalo = Gtk.SpinButton.new_with_range(20, 3600, 10)
        self.ws_intervalo.set_value(int(config.get("wsocial", "intervalo_tiempo_real", 60) or 60))
        p.campo(_("Segundos entre las comprobaciones de W Social:"), self.ws_intervalo,
                _("Cada cuánto se pregunta por notificaciones y mensajes directos nuevos. Mínimo 20 segundos."))
        self.ws_avisar_dm = p.interruptor(config.get("wsocial", "avisar_mensajes", True))
        p.campo(_("Avisar de los mensajes directos de W Social:"), self.ws_avisar_dm)
        self.tiempo_real_tg = p.interruptor(config.get("gtk", "telegram_realtime", True))
        p.campo(_("Tiempo real en Tele_gram:"), self.tiempo_real_tg)
        self.anunciar_tg = p.interruptor(config.get("telegram", "announce_new", True))
        p.campo(_("Anunciar los _mensajes nuevos de Telegram por voz:"), self.anunciar_tg,
                _("Solo de la conversación abierta y de chats privados, nunca de grupos ni canales."))
        self.anunciar_irc = p.interruptor(config.get("irc", "announce_new", True))
        p.campo(_("Anunciar los mensajes de _IRC por voz:"), self.anunciar_irc,
                _("De la conversación abierta, de privados y de menciones, según el nivel de aviso de cada canal."))
        self.anunciar_x = p.interruptor(config.get("xmpp", "announce_new", True))
        p.campo(_("Anunciar los mensajes de _XMPP por voz:"), self.anunciar_x,
                _("De la conversación abierta, de chats privados y de menciones en grupos, según el nivel de aviso."))
        self.anunciar_mx = p.interruptor(config.get("matrix", "announce_new", True))
        p.campo(_("Anunciar los mensajes de _Matrix por voz:"), self.anunciar_mx,
                _("De la conversación abierta, de mensajes directos y de menciones, según el nivel de aviso de cada sala."))
        self.auto_x = p.interruptor(config.get("x", "auto_update", False))
        p.campo(_("Actualización automática de _X:"), self.auto_x,
                _("Descargar Inicio, Menciones y Notificaciones de X cada pocos minutos. Desactivada por defecto: X limita "
                  "este cliente no oficial y un exceso de peticiones puede acabar en el bloqueo de la cuenta."))
        self.rd_intervalo = Gtk.SpinButton.new_with_range(30, 3600, 30)
        self.rd_intervalo.set_value(int(config.get("reddit", "update_interval", 120) or 120))
        p.campo(_("Segundos entre las actualizaciones de _Reddit:"), self.rd_intervalo,
                _("Se actualiza el buffer de Reddit que tienes abierto. Mínimo 30 segundos: Reddit limita las peticiones."))
        self.rd_limite = Gtk.SpinButton.new_with_range(1, 100, 5)
        self.rd_limite.set_value(int(config.get("reddit", "limit_per_load", 25) or 25))
        p.campo(_("Publicaciones de Reddit por _descarga:"), self.rd_limite)
        self.rd_orden_com = Gtk.DropDown.new_from_strings([_(n) for _v, n in RD_ORDEN_COMENTARIOS])
        actual = config.get("reddit", "comment_sort", "confidence")
        self.rd_orden_com.set_selected(next((i for i, (v, _n) in enumerate(RD_ORDEN_COMENTARIOS) if v == actual), 0))
        p.campo(_("_Orden de los comentarios de Reddit:"), self.rd_orden_com)
        self.rd_periodo = Gtk.DropDown.new_from_strings([_(n) for _v, n in RD_PERIODOS])
        actual = config.get("reddit", "time_filter", "day")
        self.rd_periodo.set_selected(next((i for i, (v, _n) in enumerate(RD_PERIODOS) if v == actual), 1))
        p.campo(_("Periodo de «Los mejores» y «Controvertidos» en Reddi_t:"), self.rd_periodo)
        self.pt_intervalo = Gtk.SpinButton.new_with_range(60, 3600, 30)
        self.pt_intervalo.set_value(int(config.get("peertube", "update_interval", 180) or 180))
        p.campo(_("Segundos entre las actualizaciones de Peer_Tube:"), self.pt_intervalo,
                _("Se actualiza la sección de PeerTube que tienes abierta. PeerTube no avisa de lo nuevo: se pregunta cada tanto."))
        self.pt_limite = Gtk.SpinButton.new_with_range(5, 100, 5)
        self.pt_limite.set_value(int(config.get("peertube", "limit_per_load", 25) or 25))
        p.campo(_("Vídeos de PeerTube por d_escarga:"), self.pt_limite)
        self.pt_nsfw = Gtk.DropDown.new_from_strings([_(n) for _v, n in PT_NSFW])
        actual = config.get("peertube", "nsfw_policy", "do_not_list")
        self.pt_nsfw.set_selected(next((i for i, (v, _n) in enumerate(PT_NSFW) if v == actual), 0))
        p.campo(_("Contenido +18 en Peer_Tube:"), self.pt_nsfw)
        self.pt_privacidad = Gtk.DropDown.new_from_strings([_(n) for _v, n in PT_PRIVACIDAD])
        actual = int(config.get("peertube", "default_privacy", 1) or 1)
        self.pt_privacidad.set_selected(next((i for i, (v, _n) in enumerate(PT_PRIVACIDAD) if v == actual), 0))
        p.campo(_("Privacidad por defecto al subir un vídeo a PeerTu_be:"), self.pt_privacidad)
        self.admin_mx = p.interruptor(config.get("matrix", "admin_tools", False))
        p.campo(_("Herramientas de _administración de Matrix:"), self.admin_mx,
                _("Añade al menú Matrix la administración del servidor Synapse. Hay que reiniciar la aplicación para verlo."))
        self.paralelas = Gtk.SpinButton.new_with_range(1, 50, 1)
        self.paralelas.set_value(int(config.get("rss", "max_parallel_updates", 5)))
        p.campo(_("Descargas si_multáneas al actualizar:"), self.paralelas)
        self.paso = Gtk.SpinButton.new_with_range(5, 120, 5)
        self.paso.set_value(float(config.get("media_player", "default_step_forward2", 15.0)))
        p.campo(_("Salto del _reproductor (segundos):"), self.paso)
        self._crear_microfono(p)
        self._pestana(_("_General"), p)

    # ------------------------------------------------------------------ Micrófono
    def _crear_microfono(self, p: _Pagina):
        """Micrófono de las notas de voz de Telegram, XMPP y Matrix (mismas claves que la versión de Windows)."""
        from app.services.audio import microphone
        self._mic_nombres = []
        self.mic = Gtk.DropDown.new_from_strings([_("(Predeterminado del sistema)")])
        p.campo(_("_Micrófono para las notas de voz:"), self.mic,
                _("Se usa al grabar con Ctrl+Alt+A en Telegram, XMPP y Matrix."))
        self.mic_volumen = Gtk.SpinButton.new_with_range(0, 200, 5)
        self.mic_volumen.set_value(int(float(microphone.config_microfono()[1]) * 100))
        p.campo(_("_Volumen del micrófono (%):"), self.mic_volumen)
        self.mic_botones = p.botones((_("A_ctualizar la lista de micrófonos"), self._cargar_microfonos),
                                     (_("Pro_bar el micrófono"), self._probar_microfono))
        self._cargar_microfonos()

    def _cargar_microfonos(self):
        from app.services.audio import microphone
        try:
            self._mic_nombres = microphone.listar_microfonos()
        except Exception:
            self._mic_nombres = []
        self.mic.set_model(Gtk.StringList.new([_("(Predeterminado del sistema)")] + self._mic_nombres))
        guardado = microphone.config_microfono()[0]
        self.mic.set_selected(self._mic_nombres.index(guardado) + 1 if guardado in self._mic_nombres else 0)
        if not self._mic_nombres:
            notificar(_("No se detectaron micrófonos"))

    def _mic_elegido(self) -> str:
        i = self.mic.get_selected()
        return self._mic_nombres[i - 1] if 0 < i <= len(self._mic_nombres) else ""

    def _probar_microfono(self):
        """Graba 4 segundos con lo elegido y los reproduce."""
        from app.services.audio import microphone
        from app.services.media_player.player2_service import KIND_PODCAST, player2_service
        dispositivo, volumen = self._mic_elegido(), self.mic_volumen.get_value() / 100.0
        notificar(_("Grabando 4 segundos de prueba. Habla ahora…"))

        def _fin(ruta, err):
            if err:
                notificar(_("Falló la prueba: {e}").format(e=err))
                return
            notificar(_("Reproduciendo la prueba grabada"))
            player2_service.cargar_y_reproducir(ruta, _("Prueba de micrófono"), KIND_PODCAST, resume=False,
                                                callback=lambda ok: None)
        task_manager.start_task(lambda ctx: microphone.grabar_prueba(dispositivo or None, volumen, segundos=4),
                                callback=_fin, pass_context=True)

    # ------------------------------------------------------------------ Inteligencia artificial
    def _crear_ia(self):
        from app.services.ai.ai_service import ai_service
        self._ai = ai_service
        p = _Pagina()
        self._ia_provs = ai_service.proveedores_disponibles()          # [(clave, etiqueta, necesita_claves)]
        self.ia_prov = Gtk.DropDown.new_from_strings([e for _k, e, _n in self._ia_provs])
        actual = config.get("ai", "provider", "gemini")
        self.ia_prov.set_selected(next((i for i, (k, _e, _n) in enumerate(self._ia_provs) if k == actual), 0))
        p.campo(_("_Proveedor de IA:"), self.ia_prov,
                _("Servicio que describe imágenes, resume y mejora textos. Desactivada apaga todas las funciones de IA."))

        # Claves en memoria por proveedor; la caja muestra las del proveedor seleccionado.
        self._ia_claves = {}
        for k, _e, necesita in self._ia_provs:
            if necesita:
                guardadas = config.get("ai", f"{k}_api_keys", [])
                if not isinstance(guardadas, list):
                    guardadas = [s for s in str(guardadas or "").splitlines() if s]
                if k == "gemini" and not guardadas and config.get("ai", "gemini_api_key", ""):
                    guardadas = [config.get("ai", "gemini_api_key", "")]
                self._ia_claves[k] = list(guardadas)
        self._ia_cargado = None
        self.ia_claves_tv, marco = p.texto_largo("", 90)
        self.ia_claves_lbl, _w = p.campo(_("_Claves API (una por línea):"), marco)
        self.ia_claves_tv.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                          [_("Si indicas varias, cuando una agote su cuota se usa la siguiente.")])
        self.ia_claves_lbl.set_mnemonic_widget(self.ia_claves_tv)
        self._ia_fila_claves = (self.ia_claves_lbl, marco)

        self.ia_modelo = Gtk.Entry(text=config.get("ai", "model", "gemini-2.5-flash"))
        self.ia_modelo_lbl, _w = p.campo(_("_Modelo:"), self.ia_modelo,
                                         _("Nombre del modelo. Usa Obtener modelos para ver los que ofrece el proveedor."))
        self.ia_url = Gtk.Entry(text=config.get("ai", "local_llama_url", "http://localhost:8080/v1"))
        self.ia_url_lbl, _w = p.campo(_("_URL del servidor local:"), self.ia_url,
                                      _("Dirección compatible con la API de chat de OpenAI (llama.cpp, Ollama...)."))
        self.ia_cache = p.interruptor(config.get("ai", "cache_responses", True))
        p.campo(_("Guardar en ca_ché las respuestas:"), self.ia_cache)
        self.ia_botones = p.botones((_("_Obtener modelos…"), self._obtener_modelos),
                                    (_("_Gestor de prompts…"), self._prompts),
                                    (_("_Probar el proveedor"), self._probar_ia),
                                    (_("_Historial de IA…"), self._historial_ia))
        self.ia_prov.connect("notify::selected", lambda *_a: self._cambiar_proveedor())
        self._cambiar_proveedor()
        self._pestana(_("_Inteligencia artificial"), p)

    def _prov_actual(self):
        i = self.ia_prov.get_selected()
        return self._ia_provs[i] if 0 <= i < len(self._ia_provs) else self._ia_provs[0]

    def _volcar_claves(self):
        if self._ia_cargado and self._ia_cargado in self._ia_claves:
            self._ia_claves[self._ia_cargado] = [k.strip() for k in _leer(self.ia_claves_tv).splitlines() if k.strip()]

    def _cambiar_proveedor(self):
        self._volcar_claves()
        clave, _et, necesita = self._prov_actual()
        for w in self._ia_fila_claves:
            w.set_visible(necesita)
        if necesita:
            self.ia_claves_tv.get_buffer().set_text("\n".join(self._ia_claves.get(clave, [])))
            self._ia_cargado = clave
        else:
            self._ia_cargado = None
        for w in (self.ia_url, self.ia_url_lbl):
            w.set_visible(clave == "local_llama")
        for w in (self.ia_modelo, self.ia_modelo_lbl):
            w.set_visible(clave != "off")

    def _aplicar_ia_en_memoria(self):
        """Pasa a la configuración (solo en memoria) lo escrito, para poder consultar o probar."""
        self._volcar_claves()
        clave, _et, _n = self._prov_actual()
        config.set("ai", "provider", clave)
        for k, lista in self._ia_claves.items():
            config.set("ai", f"{k}_api_keys", lista)
            if k == "gemini":
                config.set("ai", "gemini_api_key", lista[0] if lista else "")
        config.set("ai", "model", self.ia_modelo.get_text().strip())
        config.set("ai", "local_llama_url", self.ia_url.get_text().strip())

    def _obtener_modelos(self):
        clave, etiqueta, necesita = self._prov_actual()
        if not necesita:
            notificar(_("Este proveedor no permite obtener modelos."))
            return
        self._aplicar_ia_en_memoria()
        notificar(_("Obteniendo modelos de {p}…").format(p=etiqueta))

        def _bg(ctx):
            return self._ai.listar_modelos(clave)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron obtener los modelos: {e}").format(e=err))
                return
            if not res:
                notificar(_("El proveedor no devolvió modelos"))
                return
            from .dialogos import DialogoElegir

            def _elegido(i):
                self.ia_modelo.set_text(str(res[i]))
                notificar(_("Modelo elegido: {m}").format(m=res[i]))
            actual = self.ia_modelo.get_text().strip()
            d = DialogoElegir(self, _("Modelos de {p}").format(p=etiqueta), _("_Modelo:"), [str(m) for m in res],
                              _elegido, _("_Elegir"))
            if actual in [str(m) for m in res]:
                d.combo.set_selected([str(m) for m in res].index(actual))
            d.present()
            notificar(_("{n} modelos disponibles").format(n=len(res)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _prompts(self):
        DialogoPrompts(self).present()

    def _probar_ia(self):
        clave, etiqueta, _n = self._prov_actual()
        if clave == "off":
            notificar(_("La IA está desactivada"))
            return
        self._aplicar_ia_en_memoria()
        notificar(_("Probando {p}…").format(p=etiqueta))

        def _bg(ctx):
            return self._ai.obtener_proveedor_activo().resumir_texto(
                "SocialRSS es una aplicación accesible que reúne redes sociales, chats y noticias en una sola ventana.",
                "Resume en una frase:")

        def _fin(res, err):
            if err:
                notificar(_("La prueba falló: {e}").format(e=err))
            else:
                notificar(_("El proveedor responde: {r}").format(r=" ".join(str(res).split())[:300]))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _historial_ia(self):
        DialogoHistorialIA(self).present()

    # ------------------------------------------------------------------ Traducción
    def _crear_traduccion(self):
        from app.services.translation.translation_service import translation_service
        p = _Pagina()
        self._trad_provs = translation_service.proveedores_disponibles()   # [(clave, etiqueta)]
        self.tr_prov = Gtk.DropDown.new_from_strings([e for _k, e in self._trad_provs])
        actual = config.get("translation", "provider", "google_api_free")
        self.tr_prov.set_selected(next((i for i, (k, _e) in enumerate(self._trad_provs) if k == actual), 0))
        p.campo(_("_Motor de traducción:"), self.tr_prov,
                _("Todos son gratuitos y no necesitan clave. Si uno falla por límites del servidor, prueba con otro."))
        self.tr_mastodon = p.interruptor(config.get("translation", "mastodon_server", True))
        p.campo(_("En Mastodon, usar _primero la traducción de tu servidor:"), self.tr_mastodon,
                _("Si tu instancia ofrece traducción se usa antes que el motor externo, que queda de respaldo."))
        self.tr_destino = Gtk.DropDown.new_from_strings([_nombre_idioma(c) for c in IDIOMAS])
        destino = config.get("translation", "default_target", "es")
        self.tr_destino.set_selected(IDIOMAS.index(destino) if destino in IDIOMAS else 0)
        p.campo(_("_Idioma de destino:"), self.tr_destino)
        self.tr_origen = Gtk.DropDown.new_from_strings([_("Detectar automáticamente")] + [_nombre_idioma(c) for c in IDIOMAS])
        origen = config.get("translation", "default_source", "auto")
        self.tr_origen.set_selected(IDIOMAS.index(origen) + 1 if origen in IDIOMAS else 0)
        p.campo(_("Idioma de _origen:"), self.tr_origen)
        self.tr_copiar = p.interruptor(config.get("translation", "copy_on_translate", True))
        p.campo(_("_Copiar la traducción al portapapeles:"), self.tr_copiar)
        self.tr_historial = p.interruptor(config.get("translation", "save_history", True))
        p.campo(_("Guardar las traducciones en el _historial:"), self.tr_historial)
        p.botones((_("Historial de _traducciones…"), lambda: DialogoHistorialTraducciones(self).present()),
                  (_("_Probar la traducción"), self._probar_traduccion))
        self._pestana(_("_Traducción"), p)

    def _probar_traduccion(self):
        from app.services.translation.translation_service import translation_service
        config.set("translation", "provider", self._trad_provs[self.tr_prov.get_selected()][0])
        destino = IDIOMAS[self.tr_destino.get_selected()]
        notificar(_("Probando la traducción…"))

        def _bg(ctx):
            return translation_service.traducir("Hello, this is a test of the translation engine.", "auto", destino)

        def _fin(res, err):
            notificar(_("La prueba falló: {e}").format(e=err) if err else _("Traducción de prueba: {t}").format(t=res))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------ guardar
    def aceptar(self):
        config.set("accessibility", "enable_sounds", self.sonidos.get_active())
        config.set("accessibility", "speech_announcements", self.voz.get_active())
        config.set("rss", "fulltext_auto", self.completo.get_active())
        config.set("rss", "show_only_unread", self.solo_no_leidos.get_active())
        for clave, _nombre in REDES_BOTONERA:
            config.set(clave, "show_action_buttons", self.botonera[clave].get_active())
        for clave, _nombre in REDES_DETALLES:
            config.set(clave, "show_details_panel", self.detalles[clave].get_active())
        bandeja_cambiada = bool(config.get("inbox", "enabled", False)) != self.bandeja.get_active()
        config.set("inbox", "enabled", self.bandeja.get_active())
        config.set("rss", "max_parallel_updates", int(self.paralelas.get_value()))
        config.set("rss", "update_interval", int(self.rss_intervalo.get_value()))
        config.set("rss", "notifications_enabled", self.rss_notificar.get_active())
        config.set("rss", "download_fulltext", self.rss_siempre_completo.get_active())
        config.set("rss", "enter_action", RSS_INTRO[self.rss_intro.get_selected()][0])
        config.set("rss", "mark_read_on_open", self.rss_leido_al_abrir.get_active())
        config.set("rss", "download_images", self.rss_imagenes.get_active())
        config.set("rss", "hide_future_articles", self.rss_ocultar_futuros.get_active())
        config.set("rss", "saved_searches_in_tree", self.rss_busquedas_arbol.get_active())
        config.set("rss", "group_by_date", self.rss_agrupar.get_active())
        estado = self.padre.lookup_action("rss-agrupar-fecha")
        if estado is not None:
            estado.set_state(GLib.Variant.new_boolean(self.rss_agrupar.get_active()))
        config.set("rss", "max_alert_notifications", int(self.rss_tope_alertas.get_value()))
        config.set("rss", "podcast_max_mib", int(self.rss_podcast_mib.get_value()))
        config.set("rss", "keep_articles_days", int(self.rss_dias.get_value()))
        config.set("rss", "max_articles_per_feed", int(self.rss_max_feed.get_value()))
        config.set("rss", "no_conservar_entre_sesiones", self.rss_no_conservar.get_active())
        config.set("rss", "corte_al_limpiar", self.rss_corte.get_active())
        config.set("gtk", "mastodon_realtime", self.tiempo_real.get_active())
        config.set("gtk", "bluesky_realtime", self.tiempo_real_bsky.get_active())
        config.set("gtk", "wsocial_realtime", self.tiempo_real_ws.get_active())
        config.set("wsocial", "intervalo_tiempo_real", int(self.ws_intervalo.get_value()))
        config.set("wsocial", "avisar_mensajes", self.ws_avisar_dm.get_active())
        config.set("gtk", "telegram_realtime", self.tiempo_real_tg.get_active())
        config.set("telegram", "announce_new", self.anunciar_tg.get_active())
        config.set("irc", "announce_new", self.anunciar_irc.get_active())
        config.set("xmpp", "announce_new", self.anunciar_x.get_active())
        config.set("matrix", "announce_new", self.anunciar_mx.get_active())
        config.set("matrix", "admin_tools", self.admin_mx.get_active())
        config.set("x", "auto_update", self.auto_x.get_active())
        config.set("peertube", "update_interval", int(self.pt_intervalo.get_value()))
        config.set("peertube", "limit_per_load", int(self.pt_limite.get_value()))
        config.set("peertube", "nsfw_policy", PT_NSFW[self.pt_nsfw.get_selected()][0])
        config.set("peertube", "default_privacy", PT_PRIVACIDAD[self.pt_privacidad.get_selected()][0])
        config.set("reddit", "update_interval", int(self.rd_intervalo.get_value()))
        config.set("reddit", "limit_per_load", int(self.rd_limite.get_value()))
        config.set("reddit", "comment_sort", RD_ORDEN_COMENTARIOS[self.rd_orden_com.get_selected()][0])
        config.set("reddit", "time_filter", RD_PERIODOS[self.rd_periodo.get_selected()][0])
        config.set("audio", "mic_device", self._mic_elegido())
        config.set("audio", "mic_volume", self.mic_volumen.get_value() / 100.0)
        paso = float(self.paso.get_value())
        config.set("media_player", "default_step_forward2", paso)
        config.set("media_player", "default_step_backward2", paso)
        # IA
        self._aplicar_ia_en_memoria()
        config.set("ai", "cache_responses", self.ia_cache.get_active())
        # Traducción
        config.set("translation", "provider", self._trad_provs[self.tr_prov.get_selected()][0])
        config.set("translation", "mastodon_server", self.tr_mastodon.get_active())
        config.set("translation", "default_target", IDIOMAS[self.tr_destino.get_selected()])
        o = self.tr_origen.get_selected()
        config.set("translation", "default_source", "auto" if o == 0 else IDIOMAS[o - 1])
        config.set("translation", "copy_on_translate", self.tr_copiar.get_active())
        config.set("translation", "save_history", self.tr_historial.get_active())
        config.save()
        notificar(_("Preferencias guardadas"))
        self.close()
        if bandeja_cambiada:
            self.padre.cargar_arbol()
        self.padre.programar_botonera()
        self.padre.aplicar_visibilidad_detalles()
        self.padre.cargar_lista()
        self.padre.iniciar_tiempo_real()
