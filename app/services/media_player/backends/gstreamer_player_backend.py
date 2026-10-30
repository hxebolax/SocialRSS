# -*- coding: utf-8 -*-
"""
Backend de reproducción basado en GStreamer (Linux).

`playbin` abre archivos locales y URL de streaming. Antes de la salida se intercala un filtro de
audio con `scaletempo` (velocidad sin cambiar el tono), un ecualizador de 10 bandas y `level`
(para medir el nivel y poder saltar silencios). No necesita un bucle de GLib en marcha: los
mensajes del bus se atienden en un manejador síncrono.
"""

import logging
import math
import threading
from pathlib import Path

from .base_player_backend import BasePlayerBackend, BANDAS_EQ, VELOCIDAD_MIN, VELOCIDAD_MAX

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

log = logging.getLogger(__name__)

Gst.init(None)

_SEG = Gst.SECOND


class GStreamerPlayerBackend(BasePlayerBackend):
    def __init__(self):
        self._lock = threading.RLock()
        self._volumen = 0.8
        self._velocidad = 1.0
        self._eq_ganancias = [0.0] * len(BANDAS_EQ)
        self._eq_preamp = 0.0
        self._ultimo_error_texto = ""
        self._nivel = -100.0
        self._cargado = False
        self._reproduciendo = False
        self._buffering = False
        self._final = False
        self._detenido = False
        self._es_directo = False
        self.al_terminar = None
        self._pipe = None
        self._eq = None
        self._tempo = None
        self._level = None

    # ------------------------------------------------------------------ construcción
    def _crear_pipeline(self):
        pipe = Gst.ElementFactory.make("playbin", "player")
        if pipe is None:
            raise RuntimeError("GStreamer no tiene el elemento playbin (instala gst-plugins-base).")
        # Solo audio: sin vídeo ni subtítulos.
        flags = pipe.get_property("flags")
        pipe.set_property("flags", (flags | 0x2) & ~(0x1 | 0x4))   # audio on; video, text off
        elementos = []
        for nombre in ("audioconvert", "scaletempo", "audioconvert", "equalizer-10bands",
                       "audioconvert", "level"):
            el = Gst.ElementFactory.make(nombre, None)
            if el is None:
                if nombre in ("scaletempo", "equalizer-10bands", "level"):
                    log.warning(f"Falta el elemento GStreamer '{nombre}': se omite.")
                    continue
                raise RuntimeError(f"Falta el elemento GStreamer '{nombre}'.")
            if nombre == "scaletempo":
                self._tempo = el
            elif nombre == "equalizer-10bands":
                self._eq = el
            elif nombre == "level":
                self._level = el
                el.set_property("post-messages", True)
                el.set_property("interval", 50 * 1000 * 1000)
            elementos.append(el)
        caja = Gst.Bin.new("filtro")
        for el in elementos:
            caja.add(el)
        for a, b in zip(elementos, elementos[1:]):
            a.link(b)
        caja.add_pad(Gst.GhostPad.new("sink", elementos[0].get_static_pad("sink")))
        caja.add_pad(Gst.GhostPad.new("src", elementos[-1].get_static_pad("src")))
        pipe.set_property("audio-filter", caja)
        bus = pipe.get_bus()
        bus.set_sync_handler(self._on_bus)
        return pipe

    # ------------------------------------------------------------------ bus
    def _on_bus(self, bus, msg):
        t = msg.type
        try:
            if t == Gst.MessageType.EOS:
                self._final = True
                self._reproduciendo = False
                aviso = self.al_terminar
                if aviso:
                    threading.Thread(target=aviso, name="player-fin", daemon=True).start()
            elif t == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                log.error(f"GStreamer: {err.message} ({dbg})")
                self._ultimo_error_texto = _mensaje_error(err)
                self._reproduciendo = False
            elif t == Gst.MessageType.BUFFERING:
                pct = msg.parse_buffering()
                self._buffering = pct < 100
                if self._reproduciendo and self._pipe is not None and not self._es_directo:
                    self._pipe.set_state(Gst.State.PAUSED if pct < 100 else Gst.State.PLAYING)
            elif t == Gst.MessageType.ELEMENT:
                st = msg.get_structure()
                if st is not None and st.get_name() == "level":
                    rms = st.get_value("rms")
                    if rms:
                        self._nivel = float(sum(rms) / len(rms))
        except Exception as e:
            log.debug(f"Mensaje de GStreamer no atendido: {e}")
        return Gst.BusSyncReply.DROP

    # ------------------------------------------------------------------ contrato
    def ultimo_error(self) -> str:
        return self._ultimo_error_texto

    def cargar(self, ruta_o_url: str) -> bool:
        with self._lock:
            self.cerrar()
            self._ultimo_error_texto = ""
            es_url = ruta_o_url.lower().startswith(("http://", "https://", "rtsp://", "mms://"))
            if es_url:
                uri = ruta_o_url
            else:
                ruta = Path(ruta_o_url)
                if not ruta.exists():
                    self._ultimo_error_texto = "No se encontró el archivo de audio."
                    return False
                uri = ruta.resolve().as_uri()
            try:
                self._pipe = self._crear_pipeline()
                self._pipe.set_property("uri", uri)
                self._pipe.set_property("volume", self._volumen)
                self._final = False
                self._es_directo = False
                self._aplicar_ecualizador()
                r = self._pipe.set_state(Gst.State.PAUSED)
                if r == Gst.StateChangeReturn.FAILURE:
                    raise RuntimeError(self._ultimo_error_texto or "No se pudo abrir el medio.")
                self._es_directo = (r == Gst.StateChangeReturn.NO_PREROLL)
                if not self._es_directo:
                    # Esperar a que el medio esté listo (o falle) sin bloquear para siempre.
                    r, _est, _pend = self._pipe.get_state(15 * _SEG)
                    if r == Gst.StateChangeReturn.FAILURE:
                        raise RuntimeError(self._ultimo_error_texto or "No se pudo abrir el medio.")
                    self._es_directo = not self._duracion_valida()
                self._cargado = True
                if self._velocidad != 1.0:
                    self._aplicar_velocidad()
                return True
            except Exception as e:
                log.error(f"Error al cargar {ruta_o_url[:80]}: {e}")
                self._ultimo_error_texto = self._ultimo_error_texto or \
                    "No se pudo iniciar la reproducción del medio."
                self.cerrar()
                return False

    def _duracion_valida(self) -> bool:
        ok, d = self._pipe.query_duration(Gst.Format.TIME)
        return bool(ok and d > 0)

    def reproducir(self):
        with self._lock:
            if self._pipe is None:
                return
            if self._final:
                self._pipe.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
                self._final = False
            self._reproduciendo = True
            self._detenido = False
            self._pipe.set_state(Gst.State.PLAYING)

    def pausar(self):
        with self._lock:
            if self._pipe is not None:
                self._reproduciendo = False
                self._pipe.set_state(Gst.State.PAUSED)

    def detener(self):
        with self._lock:
            if self._pipe is not None:
                self._reproduciendo = False
                self._pipe.set_state(Gst.State.PAUSED)
                self._pipe.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
                self._final = False
                self._detenido = True

    def seek(self, segundos: float):
        with self._lock:
            if self._pipe is None:
                return
            pos = max(0.0, float(segundos))
            dur = self.obtener_duracion()
            if dur > 0 and pos >= dur:
                pos = max(0.0, dur - 0.1)
            self._final = False
            self._seek_con_velocidad(int(pos * _SEG))

    def _seek_con_velocidad(self, pos_ns: int):
        self._pipe.seek(self._velocidad, Gst.Format.TIME,
                        Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                        Gst.SeekType.SET, pos_ns, Gst.SeekType.NONE, -1)
        if not self._es_directo:
            # El salto es asíncrono: se espera (poco) a que termine para que obtener_posicion() ya
            # devuelva la posición nueva y no la anterior.
            self._pipe.get_state(_SEG // 2)

    def obtener_posicion(self) -> float:
        p = self._pipe
        if p is None:
            return 0.0
        ok, pos = p.query_position(Gst.Format.TIME)
        return max(0.0, pos / _SEG) if ok else 0.0

    def obtener_duracion(self) -> float:
        p = self._pipe
        if p is None or self._es_directo:
            return 0.0
        ok, d = p.query_duration(Gst.Format.TIME)
        return max(0.0, d / _SEG) if ok else 0.0

    def obtener_estado(self) -> str:
        if self._pipe is None or not self._cargado:
            return "inactivo"
        if self._final:
            return "detenido"
        if self._reproduciendo:
            return "esperando" if self._buffering else "reproduciendo"
        return "detenido" if self._detenido else "pausado"

    def establecer_volumen(self, vol: float):
        self._volumen = max(0.0, min(1.0, float(vol)))
        if self._pipe is not None:
            self._pipe.set_property("volume", self._volumen)

    def obtener_volumen(self) -> float:
        return self._volumen

    def establecer_velocidad(self, speed: float):
        self._velocidad = max(VELOCIDAD_MIN, min(VELOCIDAD_MAX, round(float(speed), 2)))
        with self._lock:
            self._aplicar_velocidad()

    def _aplicar_velocidad(self):
        if self._pipe is None or not self._cargado or self._es_directo:
            return
        pos = int(self.obtener_posicion() * _SEG)
        self._seek_con_velocidad(pos)

    def obtener_velocidad(self) -> float:
        return self._velocidad

    # ------------------------------------------------------------------ ecualizador y nivel
    def establecer_ecualizador(self, ganancias, preamp_db: float = 0.0):
        g = [max(-15.0, min(15.0, float(x or 0))) for x in list(ganancias or [])[:len(BANDAS_EQ)]]
        self._eq_ganancias = g + [0.0] * (len(BANDAS_EQ) - len(g))
        self._eq_preamp = max(-15.0, min(15.0, float(preamp_db or 0)))
        self._aplicar_ecualizador()

    def obtener_ecualizador(self):
        return list(self._eq_ganancias), self._eq_preamp

    def _aplicar_ecualizador(self):
        if self._eq is None:
            return
        try:
            # El preamplificador se suma a todas las bandas (equalizer-10bands no tiene uno propio).
            for i, g in enumerate(self._eq_ganancias):
                self._eq.set_property(f"band{i}", max(-24.0, min(12.0, g + self._eq_preamp)))
        except Exception as e:
            log.debug(f"No se pudo aplicar el ecualizador: {e}")

    def nivel_db(self, ventana: float = 0.05) -> float:
        if self._pipe is None or not self._reproduciendo:
            return -100.0
        return self._nivel if not math.isnan(self._nivel) else -100.0

    def cerrar(self):
        with self._lock:
            if self._pipe is not None:
                try:
                    self._pipe.set_state(Gst.State.NULL)
                except Exception as e:
                    log.debug(f"Error al liberar el pipeline: {e}")
            self._pipe = self._eq = self._tempo = self._level = None
            self._cargado = False
            self._reproduciendo = False
            self._buffering = False
            self._final = False
            self._detenido = False
            self._nivel = -100.0


def _mensaje_error(err) -> str:
    texto = (err.message or "").lower()
    if "not found" in texto or "no such file" in texto:
        return "No se pudo abrir el medio (no se encontró o el servidor no respondió)."
    if "missing" in texto and "plugin" in texto or "decoder" in texto:
        return "Falta el códec para reproducir este formato."
    if "resolve" in texto or "connect" in texto or "could not" in texto:
        return "Sin conexión con el servidor."
    if "forbidden" in texto or "unauthorized" in texto or "403" in texto or "401" in texto:
        return "Acceso denegado por el servidor."
    return "Error de reproducción: " + (err.message or "desconocido")
