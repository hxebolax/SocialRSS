# -*- coding: utf-8 -*-
"""
Módulo para el gestor central de efectos sonoros y eventos de audio.
Utiliza basskit como motor principal y permite asociar sonidos a eventos específicos.
"""

import logging
import threading
import ctypes
from pathlib import Path

# Intentar importar basskit
HAS_BASSKIT = False
try:
    from basskit.alto_nivel import inicializar, liberar
    from basskit.dlls.bass import bass
    from basskit.constantes.bass import BASS_UNICODE, BASS_STREAM_AUTOFREE, BASS_ATTRIB_VOL
    HAS_BASSKIT = True
except ImportError as e:
    logging.getLogger(__name__).warning(f"No se pudo importar basskit: {e}. El sistema de sonido estará desactivado.")

from app.core.paths import get_resource_path, get_data_path
from app.core.config_manager import config
from app.core.event_bus import event_bus
from .sound_generator import generar_todos_los_sonidos_predeterminados

log = logging.getLogger(__name__)


def listar_paquetes_sonido():
    """Lista los paquetes de sonido disponibles: 'default' más las carpetas que
    el usuario haya creado en data/sounds/."""
    paquetes = ["default"]
    try:
        base = get_data_path("sounds")
        if base.exists():
            for d in sorted(base.iterdir()):
                if d.is_dir() and d.name != "default":
                    paquetes.append(d.name)
    except Exception as e:
        log.debug(f"No se pudieron listar los paquetes de sonido: {e}")
    return paquetes


def descripciones_sonidos():
    """Lista ordenada de (nombre_de_archivo, descripción) de los sonidos identificativos
    de la aplicación, para el diálogo de prueba de sonidos. Se traduce al llamarla."""
    return [
        ("startup.wav", _("Inicio de la aplicación")),
        ("ready.wav", _("Operación correcta: lista actualizada, acción completada o conexión correcta")),
        ("new_item.wav", _("Novedad: nueva publicación, artículo o mensaje")),
        ("notification.wav", _("Nueva mención o notificación")),
        ("error.wav", _("Error o aviso (fallo de conexión, operación fallida)")),
        ("media.wav", _("La publicación lleva multimedia (genérico)")),
        ("direct.wav", _("Mensaje directo")),
        ("cw.wav", _("Advertencia de contenido (CW)")),
        ("poll.wav", _("La publicación lleva una encuesta")),
        ("media_image.wav", _("Adjunto: imagen o foto")),
        ("media_audio.wav", _("Adjunto: audio o nota de voz")),
        ("media_video.wav", _("Adjunto: vídeo")),
        ("media_document.wav", _("Adjunto: documento o archivo")),
    ]


def sonidos_del_paquete(pack="default"):
    """Devuelve la lista de (nombre_archivo, descripción, ruta) de los sonidos de un
    paquete. Para cada sonido usa el del paquete si existe, o el de 'default' como
    respaldo. Solo incluye los que existen físicamente."""
    resultado = []
    for fname, desc in descripciones_sonidos():
        try:
            custom = get_data_path(f"sounds/{pack}/{fname}")
            default = get_resource_path(f"sounds/default/{fname}")
            ruta = custom if custom.exists() else default
            if ruta.exists():
                resultado.append((fname, desc, ruta))
        except Exception as e:
            log.debug(f"No se pudo resolver el sonido {fname} del paquete {pack}: {e}")
    return resultado


def listar_dispositivos_salida():
    """Lista los dispositivos de salida de audio detectados por BASS.
    Devuelve una lista de nombres (índice = posición + 1, 0 = no-sound)."""
    nombres = []
    if not HAS_BASSKIT:
        return nombres
    try:
        from basskit.estructuras.bass import BASS_DEVICEINFO
        i = 1  # 0 suele ser "No sound"
        info = BASS_DEVICEINFO()
        while bass.BASS_GetDeviceInfo(i, ctypes.byref(info)):
            try:
                nombre = info.name.decode("utf-8", errors="ignore") if info.name else f"Dispositivo {i}"
            except Exception:
                nombre = f"Dispositivo {i}"
            nombres.append(nombre)
            i += 1
    except Exception as e:
        log.debug(f"No se pudieron listar los dispositivos de salida: {e}")
    return nombres


def _indice_dispositivo_salida(nombre):
    """Resuelve el índice BASS del dispositivo de salida por su nombre. -1 = predeterminado."""
    if not nombre or not HAS_BASSKIT:
        return -1
    try:
        from basskit.estructuras.bass import BASS_DEVICEINFO
        i = 1
        info = BASS_DEVICEINFO()
        while bass.BASS_GetDeviceInfo(i, ctypes.byref(info)):
            try:
                n = info.name.decode("utf-8", errors="ignore") if info.name else ""
            except Exception:
                n = ""
            if n == nombre:
                return i
            i += 1
    except Exception:
        pass
    return -1

# Mapeo de eventos a nombres de archivo por defecto
EVENT_TO_FILE = {
    "app_start": "startup.wav",
    "app_exit": "ready.wav",
    # Sonidos genéricos de éxito/refresco usados por task_manager y los timelines.
    "ready": "ready.wav",
    "timeline_refreshed": "ready.wav",
    "focus_changed": "ready.wav", # Omitido por defecto o sonido muy corto si se activa
    "buffer_changed": "ready.wav",
    "connection_ok": "ready.wav",
    "connection_error": "error.wav",
    "mastodon_new_status": "new_item.wav",
    "mastodon_new_mention": "notification.wav",
    "mastodon_new_notification": "notification.wav",
    "mastodon_error": "error.wav",
    "rss_new_article": "new_item.wav",
    "rss_error": "error.wav",
    "telegram_new_message": "new_item.wav",
    "ai_done": "ready.wav",
    "ai_error": "error.wav",
    "translation_done": "ready.wav",
    "translation_error": "error.wav",
    "thread_posted": "ready.wav",
    "thread_error": "error.wav",
    "draft_saved": "ready.wav",
    "operation_done": "ready.wav",
    "warning": "error.wav",
    "error": "error.wav",
    "player_opened": "ready.wav",
    "status_has_media": "media.wav",
    "status_direct": "direct.wav",
    "status_has_cw": "cw.wav",
    "status_has_poll": "poll.wav",
    # Eventos de Bluesky (reutilizan wavs existentes para no regenerar el pack)
    "bluesky_new_status": "new_item.wav",
    "bluesky_new_mention": "notification.wav",
    "bluesky_new_notification": "notification.wav",
    "bluesky_dm": "direct.wav",
    "bluesky_error": "error.wav",
    # Eventos de W Social (reutilizan wavs existentes, como el resto de redes)
    "wsocial_new_status": "new_item.wav",
    "wsocial_new_mention": "notification.wav",
    "wsocial_new_notification": "notification.wav",
    "wsocial_new_dm": "direct.wav",
    "wsocial_error": "error.wav",
    # Eventos de IRC (reutilizan wavs existentes)
    "irc_new_message": "new_item.wav",
    "irc_highlight": "notification.wav",
    "irc_dm": "direct.wav",
    "irc_error": "error.wav",
    # Eventos de Matrix (reutilizan wavs existentes)
    "matrix_new_message": "new_item.wav",
    "matrix_highlight": "notification.wav",
    "matrix_dm": "direct.wav",
    "matrix_error": "error.wav",
    # Eventos de XMPP (reutilizan wavs existentes)
    "xmpp_new_message": "new_item.wav",
    "xmpp_highlight": "notification.wav",
    "xmpp_dm": "direct.wav",
    "xmpp_error": "error.wav",
    # Eventos de Reddit (reutilizan wavs existentes)
    "reddit_new_status": "new_item.wav",
    "reddit_new_message": "direct.wav",
    "reddit_error": "error.wav",
    # Sonidos identificativos por tipo de multimedia (Telegram, Mastodon, RSS)
    "media_image": "media_image.wav",
    "media_audio": "media_audio.wav",
    "media_video": "media_video.wav",
    "media_document": "media_document.wav",
    "media_none": None,
}

class SoundManager:
    """Gestor de efectos sonoros que envuelve basskit."""
    
    def __init__(self):
        self._initialized = False
        self._lock = threading.Lock()
        # Worker ÚNICO de reproducción: antes cada play_file() creaba un hilo nuevo. En ráfagas
        # (varias novedades/notificaciones a la vez) eso generaba muchos hilos efímeros. Ahora un
        # solo hilo consume la cola; BASS reproduce de forma asíncrona, así que el worker no se
        # bloquea (solo crea el stream y lanza la reproducción). Se arranca de forma diferida.
        import queue as _queue
        self._sound_queue: "_queue.Queue" = _queue.Queue()
        self._sound_worker_started = False

        # Suscribirse al bus de eventos para disparar sonidos
        event_bus.subscribe("sound_trigger", self.play_event_sound)

    def inicializar(self) -> bool:
        """Inicializa BASS a través de basskit."""
        if not HAS_BASSKIT:
            return False
            
        with self._lock:
            if self._initialized:
                return True
                
            try:
                # 1. Asegurar que existan los sonidos por defecto
                generar_todos_los_sonidos_predeterminados()
                
                # 2. Inicializar la librería BASS en el dispositivo configurado
                # (-1 = predeterminado del sistema).
                disp = _indice_dispositivo_salida(config.get("audio", "output_device", ""))
                inicializar(dispositivo=disp, frecuencia=44100, flags=0)
                self._initialized = True
                log.info("Motor de sonido basskit inicializado con éxito.")
                # Cargar los plugins de streaming (AAC, HLS, Opus, FLAC...) para que la
                # radio y los podcasts en esos formatos se puedan reproducir.
                self._cargar_plugins_streaming()
                return True
            except Exception as e:
                log.error(f"Error al inicializar el motor de sonido basskit: {e}")
                self._initialized = False
                return False

    def _cargar_plugins_streaming(self):
        """Registra en BASS los add-ons necesarios para reproducir streams en formatos
        que el core no soporta por sí solo (AAC, HLS, Opus, FLAC, ALAC, AC3)."""
        try:
            import os
            import basskit
            base = os.path.join(os.path.dirname(basskit.__file__), "data", "dlls", "x64")
            for nombre in ("bass_aac", "basshls", "bassopus", "bassflac", "bassalac", "bass_ac3"):
                ruta = os.path.join(base, nombre + ".dll")
                if not os.path.exists(ruta):
                    continue
                handle = bass.BASS_PluginLoad(ruta.encode("utf-8"), 0)
                if not handle:
                    log.debug(f"No se pudo cargar el plugin {nombre} (BASS error {bass.BASS_ErrorGetCode()}).")
        except Exception as e:
            log.debug(f"Error al cargar plugins de streaming: {e}")

    def play_event_sound(self, event_name: str):
        """Busca el sonido asociado a un evento y lo reproduce."""
        if not config.get("accessibility", "enable_sounds", True) or config.get("audio", "mute", False):
            return
            
        disabled = config.get("audio", "disabled_events", [])
        if event_name in disabled:
            return

        if not self._initialized:
            if not self.inicializar():
                return

        # 1. Resolver el archivo de sonido
        sound_file = EVENT_TO_FILE.get(event_name)
        if not sound_file:
            log.debug(f"No hay sonido mapeado para el evento: {event_name}")
            return
            
        # Determinar ruta: buscar en data/sounds/ primero, y si no en resources/sounds/default/
        pack = config.get("audio", "sound_pack", "default")
        custom_path = get_data_path(f"sounds/{pack}/{sound_file}")
        default_path = get_resource_path(f"sounds/default/{sound_file}")
        
        ruta_final = custom_path if custom_path.exists() else default_path
        
        if not ruta_final.exists():
            log.warning(f"Archivo de sonido no encontrado para el evento {event_name}: {ruta_final}")
            return
            
        self.play_file(ruta_final)

    def play_file(self, file_path: Path):
        """Reproduce un archivo de audio WAV de forma asíncrona y autoliberable (fire-and-forget).

        Encola la ruta para el worker único; no crea un hilo por sonido."""
        if not self._initialized:
            return
        self._ensure_sound_worker()
        self._sound_queue.put(file_path)

    def _ensure_sound_worker(self):
        """Arranca (una sola vez) el hilo que consume la cola de sonidos."""
        if self._sound_worker_started:
            return
        with self._lock:
            if self._sound_worker_started:
                return
            threading.Thread(target=self._sound_worker, name="SoundWorker", daemon=True).start()
            self._sound_worker_started = True

    def _sound_worker(self):
        """Consume la cola de sonidos y los reproduce en orden con BASS (reproducción asíncrona)."""
        while True:
            file_path = self._sound_queue.get()
            try:
                # Ruta en UTF-16LE para soporte de caracteres especiales
                ruta_bytes = str(file_path).encode("utf-16-le") + b"\x00\x00"

                # BASS_STREAM_AUTOFREE libera el stream automáticamente al terminar
                handle = bass.BASS_StreamCreateFile(
                    0,
                    ruta_bytes,
                    0,
                    0,
                    BASS_UNICODE | BASS_STREAM_AUTOFREE
                )

                if handle == 0:
                    code = bass.BASS_ErrorGetCode()
                    log.error(f"Error al cargar sonido {file_path.name} (BASS error: {code})")
                    continue

                # Aplicar volumen global de efectos sonoros
                vol = float(config.get("audio", "volume", 0.8))
                bass.BASS_ChannelSetAttribute(handle, BASS_ATTRIB_VOL, vol)

                # Reproducir
                bass.BASS_ChannelPlay(handle, 0)
            except Exception as e:
                log.error(f"Error al reproducir archivo sonoro {file_path.name}: {e}")

    def liberar(self):
        """Libera BASS y cierra recursos."""
        with self._lock:
            if self._initialized and HAS_BASSKIT:
                try:
                    liberar()
                    self._initialized = False
                    log.info("Motor de sonido basskit liberado.")
                except Exception as e:
                    log.error(f"Error al liberar motor de sonido: {e}")

# Instancia global única del gestor de sonido
sound_manager = SoundManager()
