# -*- coding: utf-8 -*-
"""
Servicio central de reproducción multimedia.
Administra el motor activo, la cola de reproducción y el historial.
Ejecuta la carga de contenidos en segundo plano para no congelar la UI.
"""

import logging
import threading
from typing import Optional, Dict, Any, Callable
import wx

from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager
from .backends.basskit_player_backend import BasskitPlayerBackend
from .player_queue import PlayerQueue
from .player_history import PlayerHistory

log = logging.getLogger(__name__)

class PlayerService:
    """Servicio principal del reproductor multimedia."""
    
    def __init__(self):
        self.backend = BasskitPlayerBackend()
        self.cola = PlayerQueue()
        self.historial = PlayerHistory()

        # Restaurar el volumen y la velocidad de la sesión anterior en el motor, para
        # que la reproducción empiece tal y como el usuario lo dejó (no solo el slider).
        try:
            self.backend.establecer_volumen(float(config.get("media_player", "volume", 0.8)))
        except (TypeError, ValueError):
            pass
        try:
            self.backend.establecer_velocidad(float(config.get("media_player", "speed", 1.0)))
        except (TypeError, ValueError):
            pass
        
        # Pista actual cargada
        self.current_url: Optional[str] = None
        self.current_title: Optional[str] = None
        self.current_service_origin: str = ""
        self.current_account_origin_id: Optional[int] = None
        # Enlace ORIGINAL del servicio (YouTube/SoundCloud...) que se está reproduciendo. La pista
        # cargada (current_url) es el WAV temporal resuelto, así que para saber si ya está sonando
        # un enlace concreto guardamos aparte el enlace de origen.
        self.current_source_link: str = ""
        
        # Bloqueo interno para evitar concurrencia en la carga
        self._load_lock = threading.Lock()
        
        # Guardado automático de progreso periódico
        self._timer_interval = 5.0 # Guardar progreso cada 5 segundos
        self._save_progress_timer: Optional[threading.Timer] = None

    def cargar_async(
        self,
        media_url: str,
        title: str,
        service_origin: str = "",
        account_origin_id: Optional[int] = None,
        source_link: str = "",
        resume: bool = True,
        callback: Optional[Callable[[bool], None]] = None
    ):
        """Carga una pista en segundo plano y avisa al finalizar.

        Si `resume` es True y hay una posición guardada para esa pista, se reanuda ahí. Si es
        False, empieza desde el principio y OLVIDA la posición previa (para que no vuelva a
        ofrecer continuar desde un punto que el usuario decidió descartar). La decisión de
        reanudar o empezar de cero la toma la interfaz preguntando al usuario."""
        def _bg_load(ctx):
            with self._load_lock:
                log.info(f"Cargando asíncronamente en backend: {title} ({media_url})")

                # Detener guardados previos antes de cargar nuevo
                self._stop_save_progress_timer()

                # Cargar en el motor
                exito = self.backend.cargar(media_url)

                if exito:
                    self.current_url = media_url
                    self.current_title = title
                    self.current_service_origin = service_origin
                    self.current_account_origin_id = account_origin_id
                    self.current_source_link = source_link or ""

                    if resume:
                        # Recuperar posición guardada de SQLite y saltar a ella.
                        last_pos = self.historial.obtener_progreso(media_url)
                        if last_pos > 0.0:
                            log.info(f"Recuperando posición anterior: {last_pos} segundos")
                            self.backend.seek(last_pos)
                    else:
                        # Empezar desde el principio: olvidar cualquier posición guardada.
                        try:
                            self.historial.borrar_progreso(media_url)
                        except Exception:
                            pass

                    # Iniciar timer de guardado de progreso periódico
                    self._start_save_progress_timer()

                return exito

        def _on_load_finish(exito, error):
            status = exito if not error else False
            if status:
                event_bus.emit("player_state_changed", "cargado")
            else:
                detalle = error or self.backend.ultimo_error() or "Error desconocido"
                log.error(f"Fallo al cargar medio: {detalle}")
                event_bus.emit("player_error", str(detalle))
                
            if callback:
                callback(status)

        # Usar el task_manager global
        task_manager.start_task(
            _bg_load,
            callback=_on_load_finish,
            pass_context=True
        )

    def cargar_y_reproducir(
        self,
        media_url: str,
        title: str,
        service_origin: str = "",
        account_origin_id: Optional[int] = None,
        source_link: str = "",
        resume: bool = True
    ):
        """Carga una pista y, en cuanto esté lista, comienza a reproducirla."""
        def _cb(exito):
            if exito:
                wx.CallAfter(self.reproducir)
        self.cargar_async(media_url, title, service_origin=service_origin,
                          account_origin_id=account_origin_id, source_link=source_link,
                          resume=resume, callback=_cb)

    def esta_reproduciendo_enlace(self, source_link: str) -> bool:
        """True si ahora mismo está SONANDO (reproduciendo, en pausa o cargando) ese enlace. OJO:
        tras detener, el backend queda en estado 'detenido' (no 'inactivo'), por eso se comprueban
        explícitamente los estados activos y no '!= inactivo' (si no, el menú seguía en 'Detener')."""
        if not source_link or not self.current_source_link:
            return False
        try:
            estado = self.backend.obtener_estado()
        except Exception:
            estado = "inactivo"
        return estado in ("reproduciendo", "pausado", "esperando") and self.current_source_link == source_link

    def detener_y_olvidar(self):
        """Detiene la reproducción y OLVIDA la posición guardada de la pista (al volver a
        reproducirla, empieza desde el principio). Limpia también el enlace en curso para que el
        menú vuelva a ofrecer 'Reproducir'."""
        url = self.current_url
        self._stop_save_progress_timer()
        try:
            if self.backend.obtener_estado() != "inactivo":
                self.backend.detener()
                event_bus.emit("player_state_changed", "detenido")
        except Exception:
            pass
        if url:
            try:
                self.historial.borrar_progreso(url)
            except Exception:
                pass
        self.current_source_link = ""
        self.current_url = None

    def detener_y_limpiar(self):
        """Detiene la reproducción, GUARDA la posición actual (para poder retomarla después) y
        LIMPIA la sesión: libera la pista del motor y olvida current_url/source_link. Así, al
        abrir el reproductor no aparece ni se reanuda lo último que sonó. La posición queda en el
        historial; cuando el usuario vuelva a reproducir ese mismo audio/vídeo, la interfaz le
        preguntará si quiere continuar desde ahí o empezar desde el principio."""
        self._stop_save_progress_timer()
        try:
            self.guardar_posicion_actual(finalizado=False)
        except Exception:
            pass
        try:
            # Liberar la pista del motor para dejar el reproductor en estado inactivo
            # (si solo se 'detuviera', seguiría cargada y se podría reanudar sin querer).
            self.backend.cerrar()
        except Exception:
            pass
        self.current_url = None
        self.current_title = None
        self.current_source_link = ""
        self.current_service_origin = ""
        self.current_account_origin_id = None
        event_bus.emit("player_state_changed", "detenido")

    def reproducir(self):
        """Inicia o reanuda la reproducción."""
        if self.backend.obtener_estado() != "inactivo":
            self.backend.reproducir()
            self._start_save_progress_timer()
            event_bus.emit("player_state_changed", "reproduciendo")

    def pausar(self):
        """Pausa la pista actual."""
        if self.backend.obtener_estado() == "reproduciendo":
            self.backend.pausar()
            self._stop_save_progress_timer()
            self.guardar_posicion_actual()
            event_bus.emit("player_state_changed", "pausado")

    def detener(self):
        """Detiene la reproducción actual."""
        if self.backend.obtener_estado() != "inactivo":
            self._stop_save_progress_timer()
            self.guardar_posicion_actual(finalizado=False)
            self.backend.detener()
            event_bus.emit("player_state_changed", "detenido")

    def seek(self, segundos: float):
        """Salta a un segundo concreto."""
        if self.backend.obtener_estado() != "inactivo":
            self.backend.seek(segundos)
            self.guardar_posicion_actual()
            event_bus.emit("player_pos_changed", segundos)

    def avanzar_segundos(self, segundos: float):
        """Avanza la reproducción N segundos."""
        if self.backend.obtener_estado() != "inactivo":
            pos = self.backend.obtener_posicion()
            self.seek(pos + segundos)

    def retroceder_segundos(self, segundos: float):
        """Retrocede la reproducción N segundos."""
        if self.backend.obtener_estado() != "inactivo":
            pos = self.backend.obtener_posicion()
            self.seek(pos - segundos)

    def establecer_volumen(self, vol: float):
        """Ajusta el volumen global del reproductor (0.0 a 1.0)."""
        self.backend.establecer_volumen(vol)
        # Diferido: el volumen cambia muchas veces seguidas (flechas/rueda) y cada set()
        # normal es un settings.json completo con fsync en el hilo de la interfaz.
        config.set_diferido("media_player", "volume", vol)
        event_bus.emit("player_vol_changed", vol)

    def establecer_velocidad(self, speed: float):
        """Establece la velocidad de reproducción sin distorsión (0.5 a 2.0)."""
        self.backend.establecer_velocidad(speed)
        config.set_diferido("media_player", "speed", speed)
        event_bus.emit("player_speed_changed", speed)

    def reproducir_siguiente(self) -> bool:
        """Avanza al siguiente elemento de la cola si existe y lo carga."""
        siguiente = self.cola.avanzar()
        if siguiente:
            self.cargar_async(
                siguiente["media_url"],
                siguiente["title"],
                siguiente["service_origin"],
                siguiente["account_origin_id"],
                callback=lambda exito: self.reproducir() if exito else None
            )
            return True
        return False

    def reproducir_anterior(self) -> bool:
        """Retrocede al anterior elemento de la cola si existe y lo carga."""
        anterior = self.cola.retroceder()
        if anterior:
            self.cargar_async(
                anterior["media_url"],
                anterior["title"],
                anterior["service_origin"],
                anterior["account_origin_id"],
                callback=lambda exito: self.reproducir() if exito else None
            )
            return True
        return False

    def guardar_posicion_actual(self, finalizado: bool = False):
        """Persiste el progreso actual en base de datos SQLite."""
        if not self.current_url:
            return
            
        pos = self.backend.obtener_posicion()
        dur = self.backend.obtener_duracion()
        
        # Si la duración es válida y estamos al final, marcar como terminado
        if dur > 0.0 and abs(pos - dur) < 2.0:
            finalizado = True
            
        self.historial.guardar_progreso(
            self.current_url,
            self.current_title or "Sin título",
            self.current_service_origin,
            self.current_account_origin_id,
            pos,
            dur,
            finished=finalizado
        )

    def _start_save_progress_timer(self):
        """Inicia el timer periódico para autoguardado de progreso en disco."""
        self._stop_save_progress_timer()
        if config.get("media_player", "save_progress", True):
            self._save_progress_timer = threading.Timer(
                self._timer_interval, 
                self._save_progress_tick
            )
            self._save_progress_timer.daemon = True
            self._save_progress_timer.start()

    def _stop_save_progress_timer(self):
        """Cancela el timer periódico."""
        if self._save_progress_timer:
            self._save_progress_timer.cancel()
            self._save_progress_timer = None

    def _save_progress_tick(self):
        """Tick del timer asíncrono para guardar posición."""
        try:
            if self.backend.obtener_estado() == "reproduciendo":
                self.guardar_posicion_actual()
        except Exception as e:
            log.error(f"Error en timer de autoguardado: {e}")
            
        # Reencolar si seguimos activos
        if self.backend.obtener_estado() == "reproduciendo":
            self._start_save_progress_timer()

    def obtener_informacion(self) -> Dict[str, Any]:
        """Obtiene un diccionario detallado con la posición, duración, etc."""
        pos = self.backend.obtener_posicion()
        dur = self.backend.obtener_duracion()
        pct = 0.0
        if dur > 0.0:
            pct = (pos / dur) * 100.0
            
        return {
            "title": self.current_title or "Ninguno",
            "media_url": self.current_url or "",
            "position": pos,
            "duration": dur,
            "percentage": pct,
            "state": self.backend.obtener_estado(),
            "volume": self.backend.obtener_volumen(),
            "speed": self.backend.obtener_velocidad()
        }

    def cerrar(self):
        """Cierra el reproductor y detiene timers."""
        self._stop_save_progress_timer()
        self.guardar_posicion_actual()
        self.backend.cerrar()
        self.current_url = None
        self.current_title = None

# Instancia global única del reproductor multimedia
player_service = PlayerService()
