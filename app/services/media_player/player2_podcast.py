# -*- coding: utf-8 -*-
"""Podcasts en el reproductor 2 (mixin de Player2Service): capítulos, cola, fin del episodio,
reanudación y saltar silencios.

- Posición COMPARTIDA entre escuchar en línea y la copia descargada: para un episodio de RSS la
  clave del historial es "rss:<article_id>", no la dirección (antes eran dos posiciones).
- Al reanudar se retrocede unos segundos (5 por defecto) para recuperar el hilo.
- Un episodio se da por terminado a menos de un minuto del final (configurable), y al terminar
  se marca su artículo como leído y suena el siguiente de la cola.
- Saltar silencios: mientras suena, se mide el nivel cada 50 ms; si hay silencio más de un
  momento, se acelera (4x) hasta que vuelve la voz. No se pierde nada que se diga.
"""
import logging
import threading
import time
from typing import List, Optional, Tuple

from app.core.config_manager import config
from app.core.event_bus import event_bus

log = logging.getLogger(__name__)

UMBRAL_SILENCIO_DB = -45.0
SILENCIO_MINIMO = 0.35       # segundos de silencio antes de acelerar
VELOCIDAD_SILENCIO = 4.0


def _avisar(texto: str):
    """Aviso importante (notificar) desde cualquier hilo, aunque la ventana esté cerrada."""
    try:
        import wx
        from app.core.speech import notificar
        wx.CallAfter(notificar, texto)
    except Exception:
        pass


class _Player2PodcastMixin:
    current_article_id: Optional[str] = None
    _clave_historial: Optional[str] = None
    capitulos: List[Tuple[float, str]] = []

    # ------------------------------------------------------------------ claves y ajustes
    @staticmethod
    def clave_de_articulo(article_id: str) -> str:
        return f"rss:{article_id}"

    def _clave(self) -> Optional[str]:
        return self._clave_historial or self.current_url

    @staticmethod
    def margen_terminado() -> float:
        try:
            return max(0.0, float(config.get("media_player", "finish_margin2", 60)))
        except (TypeError, ValueError):
            return 60.0

    @staticmethod
    def rebobinado_al_reanudar() -> float:
        try:
            return max(0.0, float(config.get("media_player", "rewind_on_resume2", 5)))
        except (TypeError, ValueError):
            return 5.0

    def _progreso_guardado(self, media_url: str, article_id: Optional[str]) -> float:
        """Posición guardada; para un artículo, también la de las claves antiguas (por
        dirección) de antes de compartir la posición."""
        pos = 0.0
        if article_id:
            pos = self.historial.obtener_progreso(self.clave_de_articulo(article_id))
        return pos or self.historial.obtener_progreso(media_url)

    def posicion_de_reanudacion(self, media_url: str, article_id: Optional[str]) -> float:
        pos = self._progreso_guardado(media_url, article_id)
        return max(0.0, pos - self.rebobinado_al_reanudar()) if pos > 0 else 0.0

    # ------------------------------------------------------------------ fin del episodio
    def _al_terminar_pista(self):
        """Aviso del backend (hilo propio) al llegar al final del audio."""
        from .player2_service import KIND_RADIO
        if self.current_kind == KIND_RADIO:
            return
        try:
            self._stop_save_progress_timer()
            self._guardar_posicion(finalizado=True)
        except Exception as e:
            log.debug(f"Player2: no se pudo guardar el final: {e}")
        aid = self.current_article_id
        if aid and config.get("rss", "mark_read_on_finish", True):
            try:
                from app.services.rss.rss_service import rss_service
                rss_service.marcar_leido(aid, True)
            except Exception as e:
                log.debug(f"Player2: no se pudo marcar leído {aid}: {e}")
        event_bus.emit("player2_state_changed", "terminado")
        if config.get("media_player", "queue_autoplay2", True):
            self.reproducir_siguiente_de_la_cola(aviso_si_vacia=False)

    def reproducir_siguiente_de_la_cola(self, aviso_si_vacia: bool = True) -> bool:
        from . import cola_podcasts
        siguiente = cola_podcasts.sacar_siguiente()
        if not siguiente:
            if aviso_si_vacia:
                _avisar(_("La cola de podcasts está vacía."))
            return False
        aid, titulo = siguiente
        _avisar(_("Siguiente de la cola: {0}").format(titulo))

        def _lanzar():
            from app.services.rss.rss_service import rss_service
            rss_service.reproducir_articulo_audio(aid, callback=lambda ok, err=None: None)
        try:
            import wx
            wx.CallAfter(_lanzar)
        except Exception:
            _lanzar()
        return True

    # ------------------------------------------------------------------ capítulos
    def _cargar_capitulos(self):
        self.capitulos = []
        aid, url = self.current_article_id, self.current_url

        def _bg():
            try:
                from app.services.rss.rss_capitulos import capitulos_de
                caps = capitulos_de(aid or "", url or "")
            except Exception as e:
                log.debug(f"Player2: capítulos no disponibles: {e}")
                caps = []
            if url == self.current_url:
                self.capitulos = caps
                if caps:
                    event_bus.emit("player2_capitulos", len(caps))
        threading.Thread(target=_bg, name="player2-capitulos", daemon=True).start()

    def capitulo_actual(self) -> int:
        """Índice del capítulo que suena (-1 si no hay capítulos)."""
        pos = self.backend.obtener_posicion() + 0.5
        actual = -1
        for i, (inicio, _t) in enumerate(self.capitulos):
            if inicio <= pos:
                actual = i
        return actual

    def ir_a_capitulo(self, indice: int) -> Optional[str]:
        if not self.capitulos or not 0 <= indice < len(self.capitulos):
            return None
        inicio, titulo = self.capitulos[indice]
        self.seek(inicio)
        return titulo

    def capitulo_relativo(self, delta: int) -> Optional[str]:
        """Salta al capítulo siguiente (+1) o anterior (-1). Hacia atrás, si ya han pasado
        más de 3 segundos del capítulo actual, vuelve a su principio (como un CD)."""
        if not self.capitulos:
            return None
        actual = self.capitulo_actual()
        if delta < 0 and actual >= 0:
            if self.backend.obtener_posicion() - self.capitulos[actual][0] > 3:
                return self.ir_a_capitulo(actual)
        return self.ir_a_capitulo(max(0, actual + delta))

    # ------------------------------------------------------------------ saltar silencios
    def saltar_silencios_activo(self) -> bool:
        return bool(config.get("media_player", "skip_silence2", False))

    def activar_saltar_silencios(self, activo: bool):
        config.set("media_player", "skip_silence2", bool(activo))
        self._arrancar_vigia_silencios()

    def _arrancar_vigia_silencios(self):
        from .player2_service import KIND_PODCAST, KIND_YOUTUBE
        if not self.saltar_silencios_activo() or self.current_kind not in (KIND_PODCAST, KIND_YOUTUBE):
            return
        if getattr(self, "_vigia_silencios", None) and self._vigia_silencios.is_alive():
            return
        self._vigia_silencios = threading.Thread(target=self._vigilar_silencios,
                                                 name="player2-silencios", daemon=True)
        self._vigia_silencios.start()

    def _vigilar_silencios(self):
        from .player2_service import KIND_PODCAST, KIND_YOUTUBE
        try:
            from app.core.task_manager import _bajar_prioridad_worker
            _bajar_prioridad_worker()     # hilo de fondo: nunca por delante de la interfaz
        except Exception:
            pass
        desde = None
        acelerado = False
        while self.saltar_silencios_activo() and self.current_kind in (KIND_PODCAST, KIND_YOUTUBE):
            estado = self.backend.obtener_estado()
            if estado in ("inactivo", "detenido"):
                break
            if estado == "reproduciendo" and not self._ducked:
                silencio = self.backend.nivel_db() < UMBRAL_SILENCIO_DB
                ahora = time.monotonic()
                if silencio:
                    desde = desde or ahora
                    if not acelerado and ahora - desde >= SILENCIO_MINIMO:
                        self.backend._aplicar_tempo(max(VELOCIDAD_SILENCIO, self._velocidad_guardada))
                        acelerado = True
                else:
                    desde = None
                    if acelerado:
                        self.backend._aplicar_tempo(self._velocidad_guardada)
                        acelerado = False
            time.sleep(0.05)
        if acelerado:
            self.backend._aplicar_tempo(self._velocidad_guardada)

    # ------------------------------------------------------------------ ecualizador
    def aplicar_ecualizador_guardado(self):
        ganancias = config.get("media_player", "eq2", None) or [0.0] * 10
        try:
            preamp = float(config.get("media_player", "eq2_preamp", 0.0))
        except (TypeError, ValueError):
            preamp = 0.0
        self.backend.establecer_ecualizador(ganancias, preamp)

    def guardar_ecualizador(self, ganancias, preamp: float):
        self.backend.establecer_ecualizador(ganancias, preamp)
        config.set("media_player", "eq2", [float(g) for g in ganancias])
        config.set("media_player", "eq2_preamp", float(preamp))
