# -*- coding: utf-8 -*-
"""
Ventana principal de la aplicación SocialRSS Accessible Suite.
Layout dividido en navegación vertical de servicios, lista de toots/artículos y panel de lectura.
Todos los procesos de sincronización y red ocurren de forma asíncrona mediante el task_manager.
"""

import os
import sys
import logging
import json
import threading
from datetime import datetime
import wx
from app.core.privacy import abrir_url

from app.core.app_context import app_context
from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.audio.sound_manager import sound_manager
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service
from app.services.telegram.telegram_service import telegram_service
from app.services.translation.translation_service import translation_service
from app.services.ai.ai_service import ai_service
from app.services.media_player.player_service import player_service

from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, confirmar, mostrar_info
from .shortcut_manager import shortcut_manager
from .preferences_dialog import PreferencesDialog
from .bluesky_views import BlueskyController

log = logging.getLogger(__name__)

from .ui_helpers import (formatear_tiempo_relativo, formato_cita,
                         evento_sonido_media, evento_media_de_attachments)

class _FiltroTeclasReproductor(wx.EventFilter):
    """Filtro de eventos a nivel de APLICACIÓN. Al ser un filtro de la `wx.App`, intercepta
    las teclas en CUALQUIER ventana, incluso con un diálogo modal abierto. Se usa para:
      - Los atajos globales de los reproductores (F1 a F8 por defecto; el usuario puede
        cambiarlos en el editor de combinaciones de teclas).
      - Que la tecla Escape CIERRE cualquier diálogo (sin hacer nada), de forma uniforme en
        toda la aplicación (algunos diálogos no lo hacían por sí solos)."""

    def __init__(self, frame):
        super().__init__()
        self._frame = frame

    def FilterEvent(self, event):
        if event.GetEventType() == wx.wxEVT_KEY_DOWN:
            kc = event.GetKeyCode()
            sin_mod = not (event.ControlDown() or event.AltDown() or event.ShiftDown())
            # Atajos globales de los reproductores (F1..F8 por defecto, personalizables desde
            # Preferencias → Combinaciones de teclas → Reproductores). Se resuelven por ACCIÓN,
            # no por rango de teclas, para respetar lo que el usuario haya configurado.
            accion_media = None
            try:
                from .shortcut_manager import shortcut_manager
                accion_media = shortcut_manager.accion_reproductor_de_evento(event)
            except Exception as e:
                log.debug(f"No se pudo resolver la tecla de reproductor: {e}")
            if accion_media:
                try:
                    if self._frame and self._frame._manejar_tecla_reproductor(accion_media):
                        return self.Event_Processed
                except Exception as e:
                    log.debug(f"Atajo global de reproductor: {e}")
            # Ayuda contextual (Ctrl+H): anuncia el nombre y la ayuda del control enfocado en
            # CUALQUIER ventana de la aplicación, también dentro de los diálogos (la tabla de
            # aceleradores de la ventana principal no llega a los diálogos modales; este filtro
            # de aplicación sí). Se resuelve por la acción del catálogo, no por tecla fija.
            if self._es_tecla_ayuda_contextual(event):
                try:
                    # Ventanas que usan Ctrl+H para OTRA cosa (el visor de lectura limpia lo usa
                    # para alternar texto/HTML) se marcan con _ctrl_h_propio: se les cede la tecla.
                    foco = wx.Window.FindFocus()
                    top = foco.GetTopLevelParent() if foco else None
                    if not getattr(top, "_ctrl_h_propio", False):
                        from .accessible_controls import describir_control_enfocado
                        describir_control_enfocado()
                        return self.Event_Processed
                except Exception as e:
                    log.debug(f"Ayuda contextual global: {e}")
            # Escape: cerrar el diálogo activo (cancelar) de forma uniforme. PERO si el foco
            # está en un desplegable (Choice/ComboBox), se le cede Escape para que cierre su
            # LISTA desplegable sin cerrar el diálogo (así no se pierde lo que haya escrito).
            elif kc == wx.WXK_ESCAPE and sin_mod:
                try:
                    if self._foco_es_desplegable(wx.Window.FindFocus()):
                        return self.Event_Skip
                    top = wx.GetActiveWindow()
                    if isinstance(top, wx.Dialog):
                        # Diálogos marcados como NO cerrables (p. ej. la carga inicial de buffers)
                        # ignoran Escape: no se deben cerrar a medias.
                        if getattr(top, "_no_cerrable", False):
                            return self.Event_Processed
                        if top.IsModal():
                            top.EndModal(wx.ID_CANCEL)
                        else:
                            top.Close()
                        return self.Event_Processed
                except Exception as e:
                    log.debug(f"Escape para cerrar diálogo: {e}")
        return self.Event_Skip

    @staticmethod
    def _es_tecla_ayuda_contextual(event):
        """True si la tecla pulsada es la de la acción 'ayuda_contexto' del catálogo
        (Ctrl+H por defecto). Se compara contra el catálogo para respetar una posible
        personalización, igual que hace _combo_coincide en la ventana principal."""
        try:
            from .shortcut_manager import shortcut_manager
            tpl = shortcut_manager.combo_to_wx(shortcut_manager.obtener_atajo("ayuda_contexto"), 0)
            if not tpl:
                return False
            flags, keycode, _mid = tpl
            mods = 0
            if flags & wx.ACCEL_CTRL:
                mods |= wx.MOD_CONTROL
            if flags & wx.ACCEL_SHIFT:
                mods |= wx.MOD_SHIFT
            if flags & wx.ACCEL_ALT:
                mods |= wx.MOD_ALT
            return event.GetModifiers() == mods and event.GetKeyCode() == keycode
        except Exception:
            return False

    @staticmethod
    def _foco_es_desplegable(foco):
        """True si el control enfocado es un desplegable que usa Escape para cerrar su lista
        (Choice, ComboBox, ComboCtrl, OwnerDrawnComboBox). En ese caso NO cerramos el diálogo:
        dejamos que Escape lo gestione el propio control."""
        if foco is None:
            return False
        # ComboCtrl / OwnerDrawnComboBox exponen IsPopupShown(): solo cedemos Escape si la
        # lista está realmente desplegada.
        is_popup = getattr(foco, "IsPopupShown", None)
        if callable(is_popup):
            try:
                return bool(is_popup())
            except Exception:
                return True
        # Choice / ComboBox nativos no exponen el estado del popup de forma fiable: se les
        # cede Escape siempre, para que cierren su lista en vez de cerrarse el diálogo.
        return isinstance(foco, (wx.Choice, wx.ComboBox))


from .mf_arranque_streaming import _Mf_arranque_streaming
from .mf_navegacion_arbol import _Mf_navegacion_arbol
from .mf_servicios_feeds import _Mf_servicios_feeds
from .mf_listados_filas import _Mf_listados_filas
from .mf_telegram_chats import _Mf_telegram_chats
from .mf_app_despacho import _Mf_app_despacho
from .mf_acciones_toot import _Mf_acciones_toot
from .mf_rss_handlers import _Mf_rss_handlers
from .mf_reproductor_media import _Mf_reproductor_media
from .mf_ia_dialogos import _Mf_ia_dialogos
from .mf_telegram_handlers import _Mf_telegram_handlers
from .mf_media_servicio import _Mf_media_servicio
from .mf_publicacion import _Mf_publicacion
from .mf_autores_listas import _Mf_autores_listas
from .mf_mastodon_extra import _Mf_mastodon_extra
from .mf_bandeja import _Mf_bandeja
from .mf_herramientas_extra import _Mf_herramientas_extra
from .mf_interfaz_invisible import _Mf_interfaz_invisible
from .mf_irc_handlers import _Mf_irc_handlers
from .mf_matrix_handlers import _Mf_matrix_handlers
from .mf_matrix_admin import _Mf_matrix_admin
from .mf_xmpp_handlers import _Mf_xmpp_handlers
from .mf_xmpp_admin import _Mf_xmpp_admin
from .mf_rss_vista import _Mf_rss_vista
from .mf_rss_importar import _Mf_rss_importar
from .mf_rss_podcast import _Mf_rss_podcast
from .mf_rss_organizar import _Mf_rss_organizar
from .mf_diferido_modal import _Mf_diferido_modal

class MainFrame(_Mf_arranque_streaming, _Mf_diferido_modal,_Mf_navegacion_arbol, _Mf_servicios_feeds, _Mf_listados_filas, _Mf_telegram_chats, _Mf_app_despacho, _Mf_acciones_toot, _Mf_rss_handlers, _Mf_reproductor_media, _Mf_ia_dialogos, _Mf_telegram_handlers, _Mf_media_servicio, _Mf_publicacion, _Mf_autores_listas, _Mf_mastodon_extra, _Mf_bandeja, _Mf_herramientas_extra, _Mf_interfaz_invisible, _Mf_irc_handlers, _Mf_matrix_handlers, _Mf_matrix_admin, _Mf_xmpp_handlers, _Mf_xmpp_admin, _Mf_rss_vista, _Mf_rss_importar, _Mf_rss_podcast, _Mf_rss_organizar, wx.Frame):
    """Ventana principal del SocialRSS Accessible Suite."""

    def PopupMenu(self, menu, *args, **kwargs):
        """Todos los menús contextuales que se muestran desde la ventana principal (todas las
        redes de la lista, la bandeja...) pasan por aquí, así que es el sitio donde se les da el
        último retoque antes de mostrarse:
          1) la tecla de cada acción, visible en su item;
          2) si el usuario activó "Organizar los menús en subcategorías" (Preferencias ->
             General), los grandes se reagrupan en submenús. Los items solo se MUEVEN, conservan
             su id y su binding."""
        try:
            from .menu_teclas import anotar_menu
            # En los buffers de CHATS no se anotan las teclas de publicación: hay items que se
            # llaman igual pero cuya tecla actúa sobre publicaciones, no sobre el chat enfocado.
            modo = str(getattr(self, "_modo_lista", "") or "")
            es_chat = modo.startswith(("telegram", "irc_", "matrix_", "xmpp_"))
            anotar_menu(menu, incluir_publicacion=not es_chat)
        except Exception as e:
            log.error(f"No se pudieron anotar las teclas del menú contextual: {e}")
        try:
            from .menu_organizador import organizar_contextual
            organizar_contextual(menu)
        except Exception as e:
            log.error(f"No se pudo organizar el menú contextual: {e}")
        return super().PopupMenu(menu, *args, **kwargs)

    def __init__(self):
        # Traducir título
        titulo = _("SocialRSS Accessible Suite")
        super().__init__(
            None,
            id=wx.ID_ANY,
            title=titulo,
            size=(950, 650),
            style=wx.DEFAULT_FRAME_STYLE
        )
        
        # Registrar en contexto
        app_context.main_frame = self
        
        # Estado local
        self.active_mastodon_account_id = None
        self.active_rss_account_id = None
        self.active_telegram_account_id = None
        self.active_bluesky_account_id = None
        self.active_x_account_id = None
        self.active_timeline_type = "home" # home, local, public, bookmarks, favourites, direct, tag, feed, category
        self._modo_lista = "toots"  # toots | cuentas | anuncios (afecta al menú contextual)
        self._notif_db_ids = []  # ids de notificación (paralelo a la lista) para descartarlas
        self.active_rss_feed_id = None
        self.active_rss_category_id = None
        self.active_hashtag = None
        
        self.lista_items_db_ids = [] # Mapeo de índices de fila a IDs de la base de datos
        self._updating = False
        # Controlador de Bluesky (toda su lógica vive en bluesky_views.py)
        self.bluesky_ctrl = BlueskyController(self)
        # Controlador de X (Twitter), solo si el cliente está disponible (kill-switch).
        # Toda su lógica vive en x_views.py; si está desactivado, ni se importa.
        self.x_ctrl = None
        try:
            from app.core.feature_flags import x_cliente_disponible
            if x_cliente_disponible():
                from .x_views import XController
                self.x_ctrl = XController(self)
        except Exception as e:
            log.error(f"No se pudo iniciar el cliente de X: {e}")
            self.x_ctrl = None
        # Controlador de IRC (toda su lógica vive en irc_views.py); solo si pydle está
        # instalada y el servicio habilitado (si no, ni se importa: mismo criterio que X).
        self.irc_ctrl = None
        self.active_irc_account_id = None
        try:
            from app.core.feature_flags import servicio_habilitado
            if servicio_habilitado("irc"):
                from .irc_views import IRCController
                self.irc_ctrl = IRCController(self)
        except Exception as e:
            log.error(f"No se pudo iniciar el cliente de IRC: {e}")
            self.irc_ctrl = None
        # Controlador de Matrix (toda su lógica vive en matrix_views.py); solo si matrix-nio
        # está instalada y el servicio habilitado (mismo criterio que IRC).
        self.matrix_ctrl = None
        self.active_matrix_account_id = None
        try:
            from app.core.feature_flags import servicio_habilitado as _sh
            if _sh("matrix"):
                from .matrix_views import MatrixController
                self.matrix_ctrl = MatrixController(self)
        except Exception as e:
            log.error(f"No se pudo iniciar el cliente de Matrix: {e}")
            self.matrix_ctrl = None
        # Controlador de XMPP (Jabber); solo si slixmpp está instalada y el servicio habilitado.
        self.xmpp_ctrl = None
        self.active_xmpp_account_id = None
        try:
            from app.core.feature_flags import servicio_habilitado as _sh
            if _sh("xmpp"):
                from .xmpp_views import XMPPController
                self.xmpp_ctrl = XMPPController(self)
        except Exception as e:
            log.error(f"No se pudo iniciar el cliente de XMPP: {e}")
            self.xmpp_ctrl = None
        # Controlador de Reddit (toda su lógica vive en reddit_views.py); solo si praw está
        # instalada y el servicio habilitado (mismo criterio que IRC/Matrix/XMPP).
        self.reddit_ctrl = None
        self.active_reddit_account_id = None
        self.active_reddit_type = "home"
        try:
            from app.core.feature_flags import servicio_habilitado as _sh
            if _sh("reddit"):
                from .reddit_views import RedditController
                self.reddit_ctrl = RedditController(self)
        except Exception as e:
            log.error(f"No se pudo iniciar el cliente de Reddit: {e}")
            self.reddit_ctrl = None
        # Controlador de PeerTube (red de vídeo federada, multi-instancia). Solo si httpx está
        # disponible y el servicio habilitado. Al construirlo se cargan en memoria las sesiones
        # guardadas (tokens cifrados) de las cuentas de PeerTube.
        self.peertube_ctrl = None
        self.active_peertube_account_id = None
        self.active_peertube_type = "recientes"
        try:
            from app.core.feature_flags import servicio_habilitado as _sh
            if _sh("peertube"):
                from .peertube_views import PeerTubeController
                self.peertube_ctrl = PeerTubeController(self)
                try:
                    from app.services.peertube import peertube_service
                    peertube_service.cargar_sesiones()
                except Exception as e:
                    log.debug(f"No se pudieron cargar las sesiones de PeerTube: {e}")
        except Exception as e:
            log.error(f"No se pudo iniciar el cliente de PeerTube: {e}")
            self.peertube_ctrl = None
        # Controlador de W Social (red europea sobre AT Protocol). Solo si la librería atproto
        # está disponible y el servicio habilitado; toda su lógica vive en wsocial_views.py.
        self.wsocial_ctrl = None
        self.active_wsocial_account_id = None
        try:
            from app.core.feature_flags import servicio_habilitado as _sh
            if _sh("wsocial"):
                from .wsocial_views import WSocialController
                self.wsocial_ctrl = WSocialController(self)
        except Exception as e:
            log.error(f"No se pudo iniciar el cliente de W Social: {e}")
            self.wsocial_ctrl = None
        # Memoria de la posición (foco) del usuario en cada buffer, para restaurarla
        # al cambiar de buffer o al actualizar (igual que TWBlue).
        self._buffer_focus = {}
        # Índice NUMÉRICO recordado por buffer (además del id). Si el elemento recordado
        # desaparece de la lista (p. ej. en "No leídos" al marcarse leído un artículo), se
        # restaura esta POSICIÓN en vez de saltar al primero (comportamiento SimpleRSS).
        self._buffer_focus_idx = {}
        # Caché de relación con autores de Mastodon {(account_id, acct): sigo_o_no}, para que
        # el menú contextual diga "Seguir" o "Dejar de seguir" según el estado real.
        self._rel_autor_cache = {}
        # Último nodo (buffer/cuenta) visitado en cada servicio, para que Alt+1..4
        # vuelva donde estabas y no al principio del servicio.
        self._ultimo_nodo_servicio = {}
        # Cargar del disco la última posición de los buffers que se recuerdan entre
        # sesiones (Inicio de Mastodon/Bluesky y Todos/No leídos/Hoy de RSS).
        self._cargar_posiciones_buffer()
        # Último panel usado en cada servicio ("arbol" o "lista"), para que Alt+1..4
        # te devuelva al sitio exacto: a la lista de buffers o dentro del buffer.
        self._ultimo_panel_servicio = {}

        # Red de seguridad ANTES de montar menús y árbol: nunca todos los servicios apagados.
        try:
            from app.core.feature_flags import garantizar_un_servicio_activo
            garantizar_un_servicio_activo()
        except Exception:
            pass

        self._init_ui()
        self._cargar_navegacion()

        # Cerrar correctamente al pulsar la X o Alt+F4 (limpia Telegram/audio)
        self.Bind(wx.EVT_CLOSE, self._on_salir)

        # Minimizar a la bandeja del sistema (si está configurado)
        self._tray_icon = None
        self.Bind(wx.EVT_ICONIZE, self._on_iconize)
        # Al RECUPERAR el foco de la ventana, reactivar el tiempo real y sondear el buffer (con
        # anti-rebote), por si el streaming se cayó mientras la app estuvo minimizada o sin foco.
        self.Bind(wx.EVT_ACTIVATE, self._on_activate_app)
        self._player_dlg = None

        # Tecla GLOBAL para invocar el reproductor desde cualquier pantalla
        # (incluso con un diálogo abierto): Ctrl+Alt+P.
        self._HOTKEY_PLAYER = 0xB001
        try:
            if self.RegisterHotKey(self._HOTKEY_PLAYER, wx.MOD_CONTROL | wx.MOD_ALT, ord('P')):
                self.Bind(wx.EVT_HOTKEY, lambda e: self._on_abrir_reproductor(None), id=self._HOTKEY_PLAYER)
        except Exception as e:
            log.debug(f"No se pudo registrar la tecla global del reproductor: {e}")

        # Interfaz invisible: teclas globales opcionales (Ctrl+Alt+M/N/U/Y). Solo si el usuario
        # la activó en Preferencias. Ver _Mf_interfaz_invisible.
        try:
            self._instalar_interfaz_invisible()
        except Exception as e:
            log.debug(f"No se pudo instalar la interfaz invisible: {e}")

        # Atajos GLOBALES de los reproductores (F1..F8 por defecto, personalizables) en TODA
        # la aplicación, incluso con diálogos abiertos. Se usa un filtro de eventos de la wx.App.
        self._dlg_fav_radio_abierto = False
        self._filtro_teclas_reproductor = _FiltroTeclasReproductor(self)
        try:
            wx.GetApp().AddFilter(self._filtro_teclas_reproductor)
        except Exception as e:
            log.debug(f"No se pudo instalar el filtro de teclas del reproductor: {e}")

        # Suscribirse a eventos de sonidos e interacción
        event_bus.subscribe("timeline_refreshed", self._on_timeline_refreshed)
        event_bus.subscribe("mastodon_publicado", self._on_mastodon_publicado)
        event_bus.subscribe("rss_refreshed", self._on_rss_refreshed)
        event_bus.subscribe("telegram_new_message", self._on_telegram_new_message)
        event_bus.subscribe("telegram_message_edited", self._on_telegram_message_edited)
        event_bus.subscribe("telegram_messages_deleted", self._on_telegram_messages_deleted)
        event_bus.subscribe("telegram_typing", self._on_telegram_typing)
        event_bus.subscribe("telegram_user_status", self._on_telegram_user_status)
        
        # Lanzar sonido inicial
        event_bus.emit("sound_trigger", "app_start")
        speak(_("Bienvenido a SocialRSS Accessible Suite. Use Alt para el menú, o tabulador para navegar."), interrupt=True)
        
        # El hilo de la INTERFAZ va con prioridad "por encima de lo normal": NVDA/JAWS
        # consultan esta ventana con llamadas SÍNCRONAS y, si el hilo va justo de turno
        # durante las ráfagas de carga (aunque los hilos de fondo ya van en baja), el
        # lector encolaba sus teclas (fallo reportado: combinaciones muertas durante la
        # carga). Con la UI por encima y el fondo por debajo, el lector siempre gana.
        try:
            import ctypes
            _k32 = ctypes.windll.kernel32
            _k32.SetThreadPriority(_k32.GetCurrentThread(), 1)  # ABOVE_NORMAL
        except Exception:
            pass

        # Vigilante de congelaciones del hilo de la interfaz: si la ventana queda
        # bloqueada ("No responde"), deja en el log CUÁNDO y CUÁNTO duró, para que los
        # reportes de cuelgues lleguen con evidencia y no a ciegas.
        try:
            from app.core import ui_watchdog
            ui_watchdog.iniciar()
        except Exception as e:
            log.debug(f"No se pudo iniciar el vigilante de interfaz: {e}")

        # Sincronización en segundo plano automática en el arranque. Las novedades de la versión (si
        # las hay) se muestran DESPUÉS de la carga inicial de buffers, no aquí, para que su cuadro no
        # se solape con el diálogo de progreso de carga.
        wx.CallAfter(self._auto_sincronizar)

        # Temporizador de actualización automática periódica (estilo TWBlue):
        # sondea el buffer activo en busca de elementos nuevos sin mover el foco.
        # El intervalo y la activación se toman de las Preferencias. Cada servicio usa SU propio
        # intervalo (ver _intervalo_auto_segundos); ya no hay un intervalo "general".
        self._auto_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_auto_timer, self._auto_timer)
        # Temporizador DEDICADO (cada 2 min) que mantiene al día en segundo plano las cuentas de
        # Mastodon que NO estás usando, INCLUSO si el servicio activo es otro (Telegram, Bluesky,
        # RSS, X). Va aparte del _auto_timer porque ese corre a la cadencia del servicio activo (RSS
        # puede ser de 30 min) y aquí queremos un ritmo propio y constante. El propio handler decide
        # si hay algo que hacer (servicio Mastodon habilitado, auto-update activo, varias cuentas).
        self._mastodon_bg_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_mastodon_bg_timer, self._mastodon_bg_timer)
        self._mastodon_bg_timer.Start(120000)  # 2 minutos
        # Temporizador de SINCRONIZACIÓN EN SEGUNDO PLANO del resto de redes (RSS, Bluesky, Telegram)
        # cuando NO son la red activa: cada una con SU intervalo configurado, para que al volver a
        # ellas estén al día (antes solo Mastodon se mantenía al día en segundo plano; RSS, por
        # ejemplo, no se actualizaba hasta que entrabas). Revisa cada minuto qué red toca sincronizar.
        self._ultima_sync_bg = {}
        self._bg_sync_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_bg_sync_timer, self._bg_sync_timer)
        self._bg_sync_timer.Start(60000)  # cada 1 minuto revisa qué red toca
        # Streaming (tiempo real) de Mastodon: UN gestor POR CUENTA, para que TODAS las cuentas de
        # Mastodon reciban en tiempo real aunque no sean la activa (y aunque estés en otra red). Cada
        # gestor se crea bajo demanda en _gestionar_streaming_mastodon. Sus callbacks capturan el
        # account_id para saber a qué cuenta pertenece cada evento/reconexión.
        self._streamings_mastodon = {}                 # account_id -> MastodonStreamingManager
        self._refrescos_streaming_pendientes = set()   # (account_id, timeline) con refresco programado
        self._lock_streaming = threading.Lock()        # protege el set (se toca desde hilos de stream)
        # Estado acumulado de los eventos coalescidos por (cuenta, timeline): si TODOS traían
        # payload y ya quedaron aplicados a la caché (auditoría M1), el refresco solo repinta;
        # si alguno no, se sincroniza por REST. "borrado" fuerza recargar la lista visible.
        self._estado_evento_streaming = {}
        # Marca de tiempo (monotónica) del último repintado por streaming de cada (cuenta, timeline).
        # Con ella la coalescencia es de BORDE DE ENTRADA: un evento aislado se repinta al instante y
        # solo las ráfagas se limitan a un repintado por ventana (ver _on_evento_streaming).
        self._ultimo_refresco_streaming = {}
        self._reiniciar_auto_timer()
        # Arrancar el tiempo real de Mastodon (de TODAS las cuentas) al iniciar, aunque la primera red
        # que se muestre no sea Mastodon: así sus novedades entran desde el principio sin tener que
        # entrar en Mastodon. Diferido para no retrasar la aparición de la ventana.
        wx.CallAfter(self._gestionar_streaming_mastodon)
        # IRC: conectar en segundo plano las redes con "conexión automática" (diferido igual).
        wx.CallAfter(self._irc_arranque_autoconectar)
        # Matrix: conectar las cuentas en segundo plano (sync long-polling, diferido igual).
        wx.CallAfter(self._matrix_arranque_autoconectar)
        # XMPP: conectar las cuentas con autoconexión en segundo plano (stream, diferido igual).
        wx.CallAfter(self._xmpp_arranque_autoconectar)

        # Los complementos ya se descubrieron y activaron antes de construir los menús; aquí solo
        # avisamos (cuando la interfaz está lista) de que la app ha arrancado.
        try:
            from app.core.plugins import plugin_manager, PluginHook
            wx.CallAfter(plugin_manager.dispatch, PluginHook.APP_STARTED)
        except Exception as e:
            log.error(f"No se pudo notificar APP_STARTED a los complementos: {e}")

    # Intervalo de sondeo (segundos) de CADA servicio: ya no hay un único intervalo "general";
    # cada servicio usa el suyo (independientes). El mínimo evita saturar la instancia/API.
