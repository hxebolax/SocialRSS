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


def listar_microfonos() -> List[str]:
    """Devuelve los nombres de los micrófonos detectados por BASS."""
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
        log.debug(f"No se pudo convertir a Opus: {e}")
        return None
    if os.path.exists(destino) and os.path.getsize(destino) > 200:
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
    grab = GrabadorBass()
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
