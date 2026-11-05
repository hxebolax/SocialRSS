# -*- coding: utf-8 -*-
"""
Captura de audio del micrófono mediante **basskit (BASS)**.

Se usa BASS para grabar (en lugar de ffmpeg) porque no depende de códecs externos
ni del nombre exacto del dispositivo dshow: BASS captura PCM directamente del
micrófono elegido. El audio se guarda como WAV (PCM 16 bits), que luego se envía.

Para REPRODUCIR audios que BASS no soporta de forma nativa (p. ej. Opus de las
notas de voz), se usa ffmpeg para decodificarlos a WAV (ffmpeg decodifica Opus
sin necesidad de libopus).
"""

import os
import sys
import time
import wave
import ctypes
import logging
import subprocess
from typing import List, Optional

from app.core.paths import get_data_path, get_ffmpeg_path

log = logging.getLogger(__name__)

_NO_WINDOW = 0
if os.name == "nt":
    _NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_FREQ = 44100  # Frecuencia de grabación


def _ffmpeg() -> str:
    return get_ffmpeg_path() or "ffmpeg"


# ----------------------------------------------------------------------------
# ENUMERACIÓN DE MICRÓFONOS (BASS)
# ----------------------------------------------------------------------------
def config_microfono():
    """Devuelve (dispositivo, volumen) del micrófono configurado, COMPARTIDO por todas las redes
    (Telegram, Matrix, XMPP...). Se guarda en la sección "audio" (Preferencias → Sonidos →
    Micrófono); con respaldo a la antigua "telegram" para no perder el ajuste de quien lo tuviera
    puesto antes de moverlo. Dispositivo vacío = micrófono predeterminado del sistema."""
    from app.core.config_manager import config
    dispositivo = config.get("audio", "mic_device", config.get("telegram", "mic_device", ""))
    volumen = config.get("audio", "mic_volume", config.get("telegram", "mic_volume", 1.0))
    return dispositivo, volumen


def _usa_gstreamer() -> bool:
    """BASS solo se usa en Windows; en Linux y macOS se graba con GStreamer."""
    return sys.platform != "win32"


def _gst():
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    return Gst


def _dispositivos_gst():
    """[(nombre, Gst.Device)] de las entradas de audio reales (sin los «monitor» de las salidas)."""
    Gst = _gst()
    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Audio/Source", None)
    monitor.start()
    try:
        salida, vistos = [], set()
        for d in monitor.get_devices():
            props = d.get_properties()
            if props is not None and props.has_field("device.class") and props.get_string("device.class") == "monitor":
                continue
            nombre = d.get_display_name()
            if nombre and nombre not in vistos:
                vistos.add(nombre)
                salida.append((nombre, d))
        return salida
    finally:
        monitor.stop()


def listar_microfonos() -> List[str]:
    """Devuelve los nombres de los micrófonos detectados (BASS en Windows, GStreamer en el resto)."""
    if _usa_gstreamer():
        try:
            return [n for n, _d in _dispositivos_gst()]
        except Exception as e:
            log.error(f"Error al listar micrófonos con GStreamer: {e}")
            return []
    nombres = []
    try:
        from basskit.dlls.bass import bass
        from basskit.estructuras.bass import BASS_DEVICEINFO
        i = 1  # 0 suele ser "No sound"; los micrófonos reales empiezan en 1
        info = BASS_DEVICEINFO()
        while bass.BASS_RecordGetDeviceInfo(i, ctypes.byref(info)):
            try:
                nombre = info.name.decode("utf-8", errors="ignore") if info.name else f"Micrófono {i}"
            except Exception:
                nombre = f"Micrófono {i}"
            if nombre:
                nombres.append(nombre)
            i += 1
    except Exception as e:
        log.error(f"Error al listar micrófonos con BASS: {e}")
    return nombres


def _indice_de(nombre: Optional[str]) -> int:
    """Resuelve el índice de dispositivo BASS a partir del nombre. -1 = predeterminado."""
    if not nombre:
        return -1
    try:
        from basskit.dlls.bass import bass
        from basskit.estructuras.bass import BASS_DEVICEINFO
        i = 1
        info = BASS_DEVICEINFO()
        while bass.BASS_RecordGetDeviceInfo(i, ctypes.byref(info)):
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


# ----------------------------------------------------------------------------
# GRABACIÓN CON BASS
# ----------------------------------------------------------------------------
class GrabadorBass:
    """Graba del micrófono con BASS a memoria y guarda el resultado en WAV."""

    def __init__(self):
        self._buffers = []
        self._handle = 0
        self._callback_ref = None
        self._volumen = 1.0
        self._activo = False

    def iniciar(self, dispositivo: Optional[str] = None, volumen: float = 1.0):
        from basskit.dlls.bass import bass
        from basskit.callbacks.bass import RECORDPROC

        self._buffers = []
        try:
            self._volumen = float(volumen)
        except (TypeError, ValueError):
            self._volumen = 1.0

        indice = _indice_de(dispositivo)

        # Inicializar el dispositivo de grabación
        if not bass.BASS_RecordInit(indice):
            err = bass.BASS_ErrorGetCode()
            if err != 14:  # 14 = BASS_ERROR_ALREADY (ya inicializado)
                raise RuntimeError(f"No se pudo abrir el micrófono (BASS error {err}).")
        try:
            bass.BASS_RecordSetDevice(indice if indice >= 0 else 1)
        except Exception:
            pass

        @RECORDPROC
        def _cb(handle, buffer, length, user):
            try:
                if buffer and length > 0:
                    arr = (ctypes.c_byte * length).from_address(buffer)
                    self._buffers.append(bytes(arr))
            except Exception as e:
                log.debug(f"Error en callback de grabación: {e}")
            return 1  # continuar

        self._callback_ref = _cb
        ptr = ctypes.cast(_cb, ctypes.c_void_p)
        # 1 canal (mono), flags=0 → PCM 16 bits con signo
        self._handle = bass.BASS_RecordStart(_FREQ, 1, 0, ptr, None)
        if self._handle == 0:
            err = bass.BASS_ErrorGetCode()
            try:
                bass.BASS_RecordFree()
            except Exception:
                pass
            raise RuntimeError(f"No se pudo iniciar la grabación (BASS error {err}).")
        self._activo = True

    def detener_a_wav(self, ruta: str) -> str:
        """Detiene la grabación y escribe el WAV. Devuelve la ruta."""
        from basskit.dlls.bass import bass
        if self._handle:
            try:
                bass.BASS_ChannelStop(self._handle)
            except Exception:
                pass
        try:
            bass.BASS_RecordFree()
        except Exception:
            pass
        self._handle = 0
        self._activo = False

        pcm = b"".join(self._buffers)
        self._buffers = []
        if len(pcm) < 1000:
            raise RuntimeError("La grabación quedó vacía. Revise el micrófono seleccionado.")

        # Aplicar volumen sobre las muestras de 16 bits si procede
        if abs(self._volumen - 1.0) > 0.001:
            pcm = self._aplicar_volumen(pcm, self._volumen)

        with wave.open(ruta, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_FREQ)
            w.writeframes(pcm)
        return ruta

    def cancelar(self):
        from basskit.dlls.bass import bass
        if self._handle:
            try:
                bass.BASS_ChannelStop(self._handle)
            except Exception:
                pass
        try:
            bass.BASS_RecordFree()
        except Exception:
            pass
        self._handle = 0
        self._activo = False
        self._buffers = []

    @staticmethod
    def _aplicar_volumen(pcm: bytes, volumen: float) -> bytes:
        import array
        muestras = array.array("h")
        muestras.frombytes(pcm)
        for i in range(len(muestras)):
            v = int(muestras[i] * volumen)
            muestras[i] = max(-32768, min(32767, v))
        return muestras.tobytes()


class GrabadorGst:
    """Graba del micrófono con GStreamer a memoria (mono, 44,1 kHz, 16 bits) y guarda el resultado en WAV.
    Misma interfaz que GrabadorBass, para que los servicios de las redes no sepan con cuál graban."""

    def __init__(self):
        self._pipeline = None
        self._trozos = []
        self._volumen = 1.0

    def _crear_fuente(self, dispositivo: Optional[str]):
        """Elemento de captura: el dispositivo elegido por nombre o, si no, el micrófono predeterminado del sistema."""
        Gst = _gst()
        if dispositivo:
            for nombre, d in _dispositivos_gst():
                if nombre == dispositivo:
                    return d.create_element("microfono")
        for fabrica in ("pulsesrc", "pipewiresrc", "autoaudiosrc"):
            elemento = Gst.ElementFactory.make(fabrica, "microfono")
            if elemento is not None:
                return elemento
        raise RuntimeError("GStreamer no tiene ninguna fuente de audio para el micrófono.")

    def iniciar(self, dispositivo: Optional[str] = None, volumen: float = 1.0):
        Gst = _gst()
        try:
            self._volumen = max(0.0, min(10.0, float(volumen)))
        except (TypeError, ValueError):
            self._volumen = 1.0
        self._trozos = []
        try:
            fuente = self._crear_fuente(dispositivo)
            pipeline = Gst.Pipeline.new("grabador")
            conv = Gst.ElementFactory.make("audioconvert", None)
            remuestreo = Gst.ElementFactory.make("audioresample", None)
            vol = Gst.ElementFactory.make("volume", None)
            vol.set_property("volume", self._volumen)
            filtro = Gst.ElementFactory.make("capsfilter", None)
            filtro.set_property("caps", Gst.Caps.from_string(f"audio/x-raw,format=S16LE,channels=1,rate={_FREQ}"))
            sink = Gst.ElementFactory.make("appsink", None)
            sink.set_property("emit-signals", True)
            sink.set_property("sync", False)
            sink.connect("new-sample", self._nueva_muestra)
            elementos = (fuente, conv, remuestreo, vol, filtro, sink)
            if any(e is None for e in elementos):
                raise RuntimeError("Faltan complementos de GStreamer (audioconvert, audioresample, volume o appsink).")
            for e in elementos:
                pipeline.add(e)
            for a, b in zip(elementos, elementos[1:]):
                if not a.link(b):
                    raise RuntimeError("No se pudo conectar la captura de audio.")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"No se pudo preparar la grabación: {e}")
        self._pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)
        estado = pipeline.get_state(3 * Gst.SECOND)[0]
        if estado == Gst.StateChangeReturn.FAILURE:
            self._liberar()
            raise RuntimeError("No se pudo abrir el micrófono. Comprueba que está conectado y que la aplicación puede usarlo.")

    def _nueva_muestra(self, sink):
        Gst = _gst()
        muestra = sink.emit("pull-sample")
        if muestra is not None:
            buf = muestra.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                try:
                    self._trozos.append(bytes(info.data))
                finally:
                    buf.unmap(info)
        return Gst.FlowReturn.OK

    def _liberar(self):
        if self._pipeline is not None:
            try:
                self._pipeline.set_state(_gst().State.NULL)
            except Exception:
                pass
            self._pipeline = None

    def detener_a_wav(self, ruta: str) -> str:
        """Detiene la grabación y escribe el WAV. Devuelve la ruta."""
        Gst = _gst()
        if self._pipeline is not None:
            try:
                self._pipeline.send_event(Gst.Event.new_eos())
                self._pipeline.get_bus().timed_pop_filtered(2 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            except Exception:
                pass
        self._liberar()
        pcm = b"".join(self._trozos)
        self._trozos = []
        if len(pcm) < 1000:
            raise RuntimeError("La grabación quedó vacía. Revise el micrófono seleccionado.")
        with wave.open(ruta, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_FREQ)
            w.writeframes(pcm)
        return ruta

    def cancelar(self):
        self._liberar()
        self._trozos = []


def crear_grabador():
    """El grabador de esta plataforma: BASS en Windows, GStreamer en el resto."""
    return GrabadorGst() if _usa_gstreamer() else GrabadorBass()


def _opus_con_gstreamer(ruta_wav: str, destino: str) -> bool:
    """Codifica un WAV a OGG/Opus con GStreamer (opusenc), para cuando no hay ffmpeg con libopus."""
    try:
        Gst = _gst()
        nombres = ("filesrc", "wavparse", "audioconvert", "audioresample", "opusenc", "oggmux", "filesink")
        el = [Gst.ElementFactory.make(n, None) for n in nombres]
        if any(e is None for e in el):
            return False
        el[0].set_property("location", ruta_wav)
        el[4].set_property("bitrate", 32000)
        el[6].set_property("location", destino)
        pipeline = Gst.Pipeline.new("opus")
        for e in el:
            pipeline.add(e)
        for a, b in zip(el, el[1:]):
            if not a.link(b):
                return False
        pipeline.set_state(Gst.State.PLAYING)
        msg = pipeline.get_bus().timed_pop_filtered(120 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        pipeline.set_state(Gst.State.NULL)
        return msg is not None and msg.type == Gst.MessageType.EOS
    except Exception as e:
        log.debug(f"No se pudo convertir a Opus con GStreamer: {e}")
        return False


def convertir_a_opus(ruta_wav: str) -> Optional[str]:
    """
    Convierte un WAV a OGG/Opus con ffmpeg (necesario para que Telegram lo trate
    como nota de voz). Devuelve la ruta .ogg, o None si ffmpeg no puede (p. ej.
    sin libopus).
    """
    destino = os.path.splitext(ruta_wav)[0] + ".ogg"
    cmd = [_ffmpeg(), "-y", "-i", ruta_wav, "-ac", "1", "-c:a", "libopus", "-b:a", "32k", destino]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, creationflags=_NO_WINDOW)
    except Exception as e:
        log.debug(f"No se pudo convertir a Opus con ffmpeg: {e}")
    if os.path.exists(destino) and os.path.getsize(destino) > 200:
        return destino
    # Sin ffmpeg (o sin libopus): en Linux se intenta con GStreamer, que suele traer opusenc.
    if _usa_gstreamer() and _opus_con_gstreamer(ruta_wav, destino) and os.path.getsize(destino) > 200:
        return destino
    return None


def duracion_wav(ruta_wav: str) -> float:
    """Duración en segundos de un WAV (frames / framerate), 0 si no se puede leer. Sin dependencias."""
    try:
        with wave.open(ruta_wav, "rb") as w:
            fr = w.getframerate() or 0
            return round(w.getnframes() / fr, 1) if fr else 0.0
    except Exception:
        return 0.0


def duracion_audio(ruta: str) -> float:
    """Duración en segundos de cualquier audio local: si es WAV se lee directo; si no, se
    decodifica a WAV con ffmpeg (Opus/ogg/m4a...) y se mide. 0 si no se puede."""
    if not ruta or not os.path.exists(ruta):
        return 0.0
    if os.path.splitext(ruta)[1].lower() == ".wav":
        return duracion_wav(ruta)
    try:
        wav = convertir_a_wav_si_necesario(ruta)
        return duracion_wav(wav) if wav and wav.lower().endswith(".wav") else 0.0
    except Exception:
        return 0.0


def grabar_prueba(dispositivo: Optional[str] = None, volumen: float = 1.0, segundos: int = 4) -> str:
    """Graba unos segundos de prueba con BASS y devuelve la ruta del WAV."""
    ruta = str(get_data_path("temp") / f"prueba_micro_{int(time.time())}.wav")
    grab = crear_grabador()
    grab.iniciar(dispositivo, volumen)
    time.sleep(max(1, int(segundos)))
    return grab.detener_a_wav(ruta)


# ----------------------------------------------------------------------------
# CONVERSIÓN PARA REPRODUCCIÓN (ffmpeg, solo decodificación)
# ----------------------------------------------------------------------------
_FORMATOS_BASS_OK = {".mp3", ".mp2", ".wav", ".aiff", ".aif", ".ogg"}


def convertir_a_wav_si_necesario(ruta: str, forzar: bool = False) -> str:
    """
    Si el archivo no es reproducible por BASS (p. ej. Opus de notas de voz, m4a,
    aac, flac...), lo convierte a WAV con ffmpeg (decodifica Opus de forma nativa,
    sin libopus) y devuelve la ruta del WAV. Si ya es reproducible, devuelve la
    ruta original. Nota: los .ogg de Telegram suelen ser Opus, así que se intenta
    reproducir y, si BASS falla, conviene convertir; aquí convertimos siempre los
    formatos no nativos y también los .ogg para asegurar compatibilidad.

    Con forzar=True se convierte siempre, sin mirar la extensión. Se usa para las
    URLs de audio resueltas de servicios (YouTube, etc.): suelen ser webm/opus que
    BASS no decodifica, y además BASS no puede abrir esas URLs por streaming. ffmpeg
    lee la URL y extrae el audio a un WAV local reproducible.
    """
    if not ruta:
        return ruta
    es_url = ruta.lower().startswith(("http://", "https://"))
    ext = os.path.splitext(ruta.split("?")[0])[1].lower()
    # Los .ogg de Telegram son Opus (BASS no los soporta), por eso se convierten.
    if not forzar and ext in _FORMATOS_BASS_OK and ext != ".ogg":
        return ruta
    import hashlib
    nombre = "play_" + hashlib.sha256(ruta.encode("utf-8")).hexdigest()[:16] + ".wav"
    destino = str(get_data_path("temp") / nombre)
    if os.path.exists(destino) and os.path.getsize(destino) > 200:
        return destino
    cmd = [_ffmpeg(), "-y"]
    if es_url:
        # Algunos CDN exigen un User-Agent de navegador para servir el flujo.
        cmd += ["-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"]
    cmd += ["-i", ruta, "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", destino]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=180, creationflags=_NO_WINDOW)
    except Exception as e:
        log.error(f"No se pudo convertir el audio a WAV: {e}")
        return ruta
    if os.path.exists(destino) and os.path.getsize(destino) > 200:
        return destino
    return ruta
