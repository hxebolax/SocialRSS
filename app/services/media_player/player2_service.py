# -*- coding: utf-8 -*-
"""
Segundo reproductor multimedia, independiente del reproductor de redes sociales.

Está dedicado a RADIO, PODCAST y YOUTUBE. Solo puede reproducir UNA cosa a la vez
(no se puede escuchar un podcast y un vídeo de YouTube simultáneamente).

Prioridad de audio (ducking): el reproductor de redes sociales (player_service) tiene
prioridad. Cuando algo empieza a sonar en redes sociales:
  - RADIO: se silencia (mute) mientras dure el audio de la red social.
  - PODCAST y YOUTUBE: se pausan.
Al terminar (o detenerse) el audio de la red social, se deshace automáticamente:
  - RADIO: se le quita el silencio.
  - PODCAST/YOUTUBE: se reanuda donde se había quedado.

Para podcast y YouTube se recuerda la posición (continuar donde lo dejaste) reutilizando
el historial de reproducción (play_history). La radio es en directo: no tiene posición,
pero su historial se guarda aparte (radio_history) desde el servicio de radio.
"""

import logging
import threading
from typing import Optional, Dict, Any, Callable

import wx

from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager
from .backends.basskit_player_backend import BasskitPlayerBackend
from .player_history import PlayerHistory
from .player2_podcast import _Player2PodcastMixin

log = logging.getLogger(__name__)

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

# Tipos de contenido del segundo reproductor.
KIND_RADIO = "radio"
KIND_PODCAST = "podcast"
KIND_YOUTUBE = "youtube"


class Player2Service(_Player2PodcastMixin):
    """Reproductor secundario para radio, podcast y YouTube, con ducking. Lo propio de los
    podcasts (capítulos, cola, fin del episodio, saltar silencios) está en player2_podcast.py."""

    def __init__(self):
        self.backend = BasskitPlayerBackend()
        self.historial = PlayerHistory()

        self.current_url: Optional[str] = None
        self.current_title: Optional[str] = None
        self.current_kind: str = ""

        self._load_lock = threading.Lock()
        self._timer_interval = 5.0
        self._save_progress_timer: Optional[threading.Timer] = None

        # Estado de ducking (prioridad del reproductor de redes sociales).
        self._ducked = False
        self._vol_antes_duck = 0.8
        self._estaba_reproduciendo_al_duck = False
        self._monitor_timer: Optional[threading.Timer] = None

        # Silencio MANUAL del usuario (independiente del ducking; sobre todo para la radio).
        self._silenciado_usuario = False

        # Volumen y velocidad guardados (claves propias para no pisar las del otro
        # reproductor). Se recuerdan entre sesiones.
        try:
            vol = float(config.get("media_player", "volume2", 0.8))
        except (TypeError, ValueError):
            vol = 0.8
        try:
            self._velocidad_guardada = float(config.get("media_player", "speed2", 1.0))
        except (TypeError, ValueError):
            self._velocidad_guardada = 1.0
        self.backend.establecer_volumen(vol)
        self._vol_antes_duck = vol
        self.current_article_id = None
        self._clave_historial = None
        self.capitulos = []
        self.backend.al_terminar = self._al_terminar_pista
        self.aplicar_ecualizador_guardado()

        # Escuchar los cambios de estado del reproductor de redes sociales.
        event_bus.subscribe("player_state_changed", self._on_social_state)

    # ------------------------------------------------------------------
    # Reproducción
    # ------------------------------------------------------------------
    def esta_activo(self) -> bool:
        """True si hay algo cargado/sonando (para preguntar antes de sustituir)."""
        return self.backend.obtener_estado() not in ("inactivo", "detenido")

    def descripcion_actual(self) -> str:
        """Texto legible de lo que se está reproduciendo (para el aviso de sustitución)."""
        if not self.current_title:
            return ""
        etiquetas = {KIND_RADIO: _("la radio"), KIND_PODCAST: _("un podcast"),
                     KIND_YOUTUBE: _("un vídeo de YouTube")}
        return _("{0}: {1}").format(etiquetas.get(self.current_kind, _("audio")), self.current_title)

    def cargar_y_reproducir(self, media_url: str, title: str, kind: str,
                            resume: bool = True, callback: Optional[Callable[[bool], None]] = None,
                            article_id: Optional[str] = None):
        """Carga y reproduce un contenido. Sustituye lo que hubiera sonando. `article_id` (un
        episodio de RSS) comparte la posición entre el audio en línea y el descargado, marca el
        artículo leído al terminar y permite buscar sus capítulos."""
        def _bg_load(ctx):
            with self._load_lock:
                self._stop_save_progress_timer()
                self._cancel_monitor()
                self._ducked = False
                self._silenciado_usuario = False
                # Guardar dónde se quedó lo anterior antes de sustituirlo.
                try:
                    if self.backend.obtener_estado() in ("reproduciendo", "pausado"):
                        self._guardar_posicion()
                except Exception:
                    pass
                exito = self.backend.cargar(media_url)
                if exito:
                    self.current_url = media_url
                    self.current_title = title
                    self.current_kind = kind
                    self.current_article_id = article_id
                    self._clave_historial = self.clave_de_articulo(article_id) if article_id else None
                    self.capitulos = []
                    # La velocidad solo aplica a podcast/YouTube (la radio va a 1.0).
                    if kind in (KIND_PODCAST, KIND_YOUTUBE):
                        self.backend.establecer_velocidad(self._velocidad_guardada)
                    else:
                        self.backend.establecer_velocidad(1.0)
                    # Continuar donde se dejó (solo podcast/youtube; la radio es en directo).
                    # Se retrocede unos segundos para recuperar el hilo (Preferencias).
                    if resume and kind in (KIND_PODCAST, KIND_YOUTUBE):
                        last_pos = self.posicion_de_reanudacion(media_url, article_id)
                        if last_pos > 0.0:
                            self.backend.seek(last_pos)
                return exito

        def _finish(exito, error):
            ok = bool(exito) and not error
            if ok:
                self.backend.reproducir()
                self._start_save_progress_timer()
                event_bus.emit("player2_state_changed", "reproduciendo")
                if kind in (KIND_PODCAST, KIND_YOUTUBE):
                    self._cargar_capitulos()
                    self._arrancar_vigia_silencios()
                # Si el reproductor social ya está sonando, aplicar prioridad de inmediato.
                if self._social_reproduciendo():
                    self._aplicar_duck()
            else:
                detalle = error or self.backend.ultimo_error() or _("Error desconocido")
                log.error(f"Player2: fallo al cargar {media_url}: {detalle}")
                event_bus.emit("player2_error", str(detalle))
            if callback:
                callback(ok)

        task_manager.start_task(_bg_load, callback=_finish, pass_context=True)

    def reproducir(self):
        if self.backend.obtener_estado() in ("pausado", "detenido"):
            self.backend.reproducir()
            self._start_save_progress_timer()
            event_bus.emit("player2_state_changed", "reproduciendo")
            self._arrancar_vigia_silencios()

    def pausar(self):
        if self.backend.obtener_estado() == "reproduciendo":
            self.backend.pausar()
            self._stop_save_progress_timer()
            self._guardar_posicion()
            event_bus.emit("player2_state_changed", "pausado")

    def alternar_pausa(self):
        est = self.backend.obtener_estado()
        if est == "reproduciendo":
            self.pausar()
        elif est in ("pausado", "detenido"):
            self.reproducir()

    def detener(self):
        if self.backend.obtener_estado() != "inactivo":
            self._stop_save_progress_timer()
            self._cancel_monitor()
            self._guardar_posicion()
            self.backend.detener()
            self._ducked = False
            event_bus.emit("player2_state_changed", "detenido")

    def seek(self, segundos: float):
        if self.backend.obtener_estado() != "inactivo":
            self.backend.seek(segundos)
            self._guardar_posicion()

    def avanzar_segundos(self, segundos: float):
        if self.current_kind == KIND_RADIO:
            return  # la radio es en directo
        if self.backend.obtener_estado() != "inactivo":
            self.seek(self.backend.obtener_posicion() + segundos)

    def retroceder_segundos(self, segundos: float):
        if self.current_kind == KIND_RADIO:
            return
        if self.backend.obtener_estado() != "inactivo":
            self.seek(max(0.0, self.backend.obtener_posicion() - segundos))

    def establecer_volumen(self, vol: float):
        vol = max(0.0, min(1.0, vol))
        self._vol_antes_duck = vol
        # No tocar el motor si está silenciado (por prioridad social o por el usuario):
        # solo se recuerda el volumen para restaurarlo al desilenciar.
        if not self._ducked and not self._silenciado_usuario:
            self.backend.establecer_volumen(vol)
        # Diferido: agrupa las ráfagas de cambios de volumen en un solo volcado a disco.
        config.set_diferido("media_player", "volume2", vol)
        event_bus.emit("player2_vol_changed", vol)

    def esta_silenciado(self) -> bool:
        """True si el usuario ha silenciado manualmente (F2 en la radio)."""
        return self._silenciado_usuario

    def alternar_silencio(self) -> bool:
        """Silencia o quita el silencio MANUAL. Devuelve True si queda silenciado."""
        self._silenciado_usuario = not self._silenciado_usuario
        if self._silenciado_usuario:
            self.backend.establecer_volumen(0.0)
        else:
            # Restaurar el volumen recordado (salvo que el ducking siga activo).
            if not self._ducked:
                self.backend.establecer_volumen(self._vol_antes_duck)
        event_bus.emit("player2_state_changed",
                       "silenciado" if self._silenciado_usuario else "reanudado")
        return self._silenciado_usuario

    def recargar(self):
        """Vuelve a abrir y reproducir la emisora/contenido actual (útil si se corta el
        streaming de la radio)."""
        if not self.current_url:
            return
        url, title, kind = self.current_url, self.current_title or "", self.current_kind
        self._silenciado_usuario = False
        self.cargar_y_reproducir(url, title, kind, resume=(kind != KIND_RADIO),
                                 article_id=self.current_article_id)

    def establecer_velocidad(self, speed: float):
        """Ajusta la velocidad (solo afecta a podcast/YouTube) y la recuerda."""
        from .backends.basskit_player_backend import VELOCIDAD_MAX, VELOCIDAD_MIN
        speed = max(VELOCIDAD_MIN, min(VELOCIDAD_MAX, round(float(speed), 2)))
        self._velocidad_guardada = speed
        # La radio va siempre a velocidad normal.
        if self.current_kind in (KIND_PODCAST, KIND_YOUTUBE):
            self.backend.establecer_velocidad(speed)
        config.set_diferido("media_player", "speed2", speed)
        event_bus.emit("player2_speed_changed", speed)

    def obtener_informacion(self) -> Dict[str, Any]:
        pos = self.backend.obtener_posicion()
        dur = self.backend.obtener_duracion()
        return {
            "title": self.current_title or _("Ninguno"),
            "kind": self.current_kind,
            "media_url": self.current_url or "",
            "position": pos,
            "duration": dur,
            "state": self.backend.obtener_estado(),
            "volume": self._vol_antes_duck,
            "speed": self._velocidad_guardada,
            "ducked": self._ducked,
            "article_id": self.current_article_id,
            "chapter": self.capitulo_actual(),
            "chapters": len(self.capitulos),
        }

    # ------------------------------------------------------------------
    # Ducking (prioridad del reproductor de redes sociales)
    # ------------------------------------------------------------------
    def _social_reproduciendo(self) -> bool:
        """True si el reproductor de redes sociales está sonando o cargando."""
        try:
            from .player_service import player_service
            return player_service.backend.obtener_estado() in ("reproduciendo", "esperando")
        except Exception:
            return False

    def _on_social_state(self, estado: str):
        """Recibe los cambios de estado del reproductor de redes sociales."""
        if estado == "reproduciendo":
            self._aplicar_duck()

    def _aplicar_duck(self):
        """Aplica la prioridad: silencia la radio o pausa el podcast/vídeo."""
        if self._ducked:
            return
        if self.backend.obtener_estado() not in ("reproduciendo", "pausado"):
            return
        self._ducked = True
        if self.current_kind == KIND_RADIO:
            # Silenciar (recordando el volumen para restaurarlo).
            self.backend.establecer_volumen(0.0)
        else:
            # Pausar podcast/YouTube si estaba sonando.
            self._estaba_reproduciendo_al_duck = (self.backend.obtener_estado() == "reproduciendo")
            if self._estaba_reproduciendo_al_duck:
                self.backend.pausar()
                self._guardar_posicion()
        event_bus.emit("player2_state_changed", "prioridad_social")
        self._start_monitor()

    def _quitar_duck(self):
        """Deshace la prioridad cuando el audio de la red social termina."""
        if not self._ducked:
            return
        self._ducked = False
        if self.current_kind == KIND_RADIO:
            self.backend.establecer_volumen(self._vol_antes_duck)
        else:
            if self._estaba_reproduciendo_al_duck:
                self.backend.reproducir()
                self._start_save_progress_timer()
        self._estaba_reproduciendo_al_duck = False
        event_bus.emit("player2_state_changed", "reanudado")

    def _start_monitor(self):
        """Vigila el reproductor social para deshacer el ducking cuando termine.

        El reproductor social no emite un evento al terminar la pista de forma
        natural, así que se sondea su estado periódicamente."""
        self._cancel_monitor()
        self._monitor_timer = threading.Timer(1.0, self._monitor_tick)
        self._monitor_timer.daemon = True
        self._monitor_timer.start()

    def _cancel_monitor(self):
        if self._monitor_timer:
            self._monitor_timer.cancel()
            self._monitor_timer = None

    def _monitor_tick(self):
        try:
            if not self._ducked:
                return
            if not self._social_reproduciendo():
                self._quitar_duck()
                return
        except Exception as e:
            log.debug(f"Player2 monitor: {e}")
        # Seguir vigilando mientras siga el ducking.
        if self._ducked:
            self._start_monitor()

    # ------------------------------------------------------------------
    # Guardado de progreso (continuar donde lo dejaste)
    # ------------------------------------------------------------------
    def _guardar_posicion(self, finalizado: bool = False):
        if not self.current_url or self.current_kind == KIND_RADIO:
            return
        pos = self.backend.obtener_posicion()
        dur = self.backend.obtener_duracion()
        # Terminado a menos de un minuto del final (Preferencias): los últimos segundos
        # suelen ser la despedida y la música; así no se retoma para oír solo eso.
        margen = max(2.0, self.margen_terminado()) if dur > 120 else 2.0
        if dur > 0.0 and dur - pos < margen:
            finalizado = True
        self.historial.guardar_progreso(
            self._clave(), self.current_title or _("Sin título"),
            self.current_kind, None, pos, dur, finished=finalizado)

    def _start_save_progress_timer(self):
        self._stop_save_progress_timer()
        if self.current_kind == KIND_RADIO:
            return  # la radio no guarda progreso
        if config.get("media_player", "save_progress", True):
            self._save_progress_timer = threading.Timer(self._timer_interval, self._save_progress_tick)
            self._save_progress_timer.daemon = True
            self._save_progress_timer.start()

    def _stop_save_progress_timer(self):
        if self._save_progress_timer:
            self._save_progress_timer.cancel()
            self._save_progress_timer = None

    def _save_progress_tick(self):
        try:
            if self.backend.obtener_estado() == "reproduciendo":
                self._guardar_posicion()
        except Exception as e:
            log.error(f"Player2 autoguardado: {e}")
        if self.backend.obtener_estado() == "reproduciendo":
            self._start_save_progress_timer()

    def cerrar(self):
        self._stop_save_progress_timer()
        self._cancel_monitor()
        self._guardar_posicion()
        self.backend.cerrar()
        self.current_url = None
        self.current_title = None
        self.current_kind = ""
        self.current_article_id = None
        self._clave_historial = None
        self.capitulos = []


# Instancia global única del segundo reproductor.
player2_service = Player2Service()
