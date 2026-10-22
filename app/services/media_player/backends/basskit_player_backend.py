# -*- coding: utf-8 -*-
"""
Módulo para el backend de reproducción multimedia basado en basskit.
Soporta audio local, streaming de URLs directas y control de tempo (velocidad) sin alterar el tono.
"""

import ctypes
import logging
from pathlib import Path
from .base_player_backend import BasePlayerBackend

# Cargar dependencias de basskit. Desde basskit 0.4 los tipos de callback (DOWNLOADPROC...)
# viven en basskit.callbacks.bass (antes también se exportaban desde basskit.dlls.bass); este
# import vale para las dos versiones.
from basskit.dlls.bass import bass
from basskit.callbacks.bass import DOWNLOADPROC, SYNCPROC
from basskit.dlls.bass_fx import bass_fx
from basskit.constantes.bass import (
    BASS_UNICODE, BASS_STREAM_DECODE, BASS_ATTRIB_VOL, BASS_POS_BYTE,
    BASS_ACTIVE_PLAYING, BASS_ACTIVE_PAUSED, BASS_ACTIVE_STALLED,
    BASS_SAMPLE_FLOAT,
)
from basskit.constantes.bass_fx import (
    BASS_FX_FREESOURCE, BASS_ATTRIB_TEMPO,
)

# Ecualizador de 10 bandas (BASS_FX PEAKEQ, una banda por frecuencia, ancho de una octava) y
# preamplificador (BASS_FX VOLUME). Se aplican sobre el canal que suena y se reaplican al cargar.
BANDAS_EQ = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
VELOCIDAD_MIN, VELOCIDAD_MAX = 0.5, 3.0

log = logging.getLogger(__name__)

# QWORD de BASS: desde basskit 0.4 es SIN signo de 64 bits y el error vale 0xFFFF...FFFF (en la
# 0.3.0 era -1). Con la comprobación antigua ("< 0") una radio daba una duración de 52 billones de
# segundos y retroceder más allá del principio saltaba al FINAL del audio.
_QWORD_ERROR = 0xFFFFFFFFFFFFFFFF


def _qword_ok(v) -> bool:
    """True si un QWORD devuelto por BASS es un valor válido (ni error ni negativo)."""
    try:
        return v is not None and 0 <= int(v) < _QWORD_ERROR
    except (TypeError, ValueError):
        return False

# Mensajes legibles (en español) para los códigos de error más habituales de BASS,
# para que el usuario reciba algo más útil que "Error desconocido".
_MENSAJES_ERROR_BASS = {
    1: "No hay memoria suficiente.",
    2: "No se pudo abrir el medio (no se encontró o el servidor no respondió).",
    3: "No hay dispositivo de salida de audio disponible.",
    5: "Identificador de audio no válido.",
    6: "Formato de audio no soportado.",
    8: "El sistema de audio no está inicializado.",
    10: "Error de certificado SSL al conectar con el servidor.",
    17: "El contenido no es audio reproducible.",
    19: "Tipo de stream no válido.",
    20: "Parámetros de reproducción no válidos.",
    32: "Sin conexión de red.",
    37: "Función no disponible para este medio.",
    38: "No se pudo decodificar el audio.",
    40: "Tiempo de espera agotado al conectar con el servidor.",
    41: "Formato de audio no compatible con el reproductor (el servidor no lo entrega en un formato reproducible en directo).",
    44: "Falta el códec para reproducir este formato.",
    46: "El reproductor está ocupado, inténtalo de nuevo.",
    47: "El contenido no se puede reproducir en streaming.",
    48: "Error de protocolo del servidor.",
    49: "Acceso denegado por el servidor.",
}


def _mensaje_error_bass(codigo: int) -> str:
    """Texto legible para un código de error de BASS."""
    return _MENSAJES_ERROR_BASS.get(codigo, f"Error de reproducción (código BASS {codigo}).")


class BasskitPlayerBackend(BasePlayerBackend):
    """Implementación de BasePlayerBackend utilizando BASS y BASS_FX."""

    def __init__(self):
        self._decode_handle = 0
        self._tempo_handle = 0
        self._volumen = 0.8
        self._velocidad = 1.0
        # Último error de carga (código BASS y mensaje legible) para informar al usuario.
        self._ultimo_error_codigo = 0
        self._ultimo_error_texto = ""
        self._eq_ganancias = [0.0] * len(BANDAS_EQ)
        self._eq_preamp = 0.0
        self._fx_eq = 0
        self._fx_preamp = 0
        # Aviso de FIN de la pista (BASS_SYNC_END). La función se crea UNA vez y se guarda en
        # el objeto: si ctypes la liberase, BASS llamaría a memoria ya liberada.
        self.al_terminar = None
        self._sync_fin = SYNCPROC(self._on_sync_fin)

    def _on_sync_fin(self, handle, channel, data, user):
        """Llega desde un hilo de BASS: no se hace nada aquí, se delega a otro hilo."""
        aviso = self.al_terminar
        if aviso:
            import threading
            threading.Thread(target=aviso, name="player-fin", daemon=True).start()

    def ultimo_error(self) -> str:
        """Devuelve el mensaje legible del último fallo de carga, o cadena vacía."""
        return self._ultimo_error_texto

    def cargar(self, ruta_o_url: str) -> bool:
        """Carga un audio local o una URL de streaming en un canal con soporte de tempo."""
        self.cerrar()
        self._ultimo_error_codigo = 0
        self._ultimo_error_texto = ""

        # 1. Detectar si es una URL o ruta local
        es_url = ruta_o_url.lower().startswith(("http://", "https://"))

        try:
            if es_url:
                # Para URLs NO se usa BASS_UNICODE: la cadena va como bytes utf-8.
                # (Con BASS_UNICODE, BASS esperaría utf-16 y la url utf-8 daría
                # BASS_ERROR_ILLPARAM, error 20, y la radio no sonaría.)
                flags = BASS_STREAM_DECODE | BASS_SAMPLE_FLOAT
                url_bytes = ruta_o_url.encode("utf-8")
                # El 4º argumento (DOWNLOADPROC): en basskit 0.3.0 no admitía None y había que
                # pasar un puntero de función nulo del tipo correcto (si no, TypeError y la
                # radio no sonaba). Desde la 0.4 es c_void_p y acepta las dos formas.
                null_proc = ctypes.cast(None, DOWNLOADPROC)
                decode = bass.BASS_StreamCreateURL(url_bytes, 0, flags, null_proc, None)
            else:
                # Para archivos locales sí se usa BASS_UNICODE con la ruta en utf-16.
                flags = BASS_STREAM_DECODE | BASS_UNICODE | BASS_SAMPLE_FLOAT
                ruta = Path(ruta_o_url)
                if not ruta.exists():
                    log.error(f"Archivo de audio no encontrado: {ruta}")
                    self._ultimo_error_codigo = 2
                    self._ultimo_error_texto = "No se encontró el archivo de audio."
                    return False
                ruta_bytes = str(ruta).encode("utf-16-le") + b"\x00\x00"
                decode = bass.BASS_StreamCreateFile(0, ruta_bytes, 0, 0, flags)
                if decode == 0:
                    # Intentar como módulo de música tracker (XM, MOD, etc.)
                    decode = bass.BASS_MusicLoad(0, ruta_bytes, 0, 0, flags, 0)

            if decode == 0:
                code = bass.BASS_ErrorGetCode()
                self._ultimo_error_codigo = code
                self._ultimo_error_texto = _mensaje_error_bass(code)
                log.error(f"Error al abrir stream decodificador (BASS error: {code}) para {ruta_o_url[:80]}")
                return False
                
            self._decode_handle = decode
            
            # 2. Envolver el decodificador con el plugin BASS_FX para permitir control de velocidad
            # BASS_FX_FREESOURCE indica que liberar el stream tempo liberará el stream decodificador automáticamente.
            tempo = bass_fx.BASS_FX_TempoCreate(self._decode_handle, BASS_FX_FREESOURCE)
            if tempo == 0:
                code = bass.BASS_ErrorGetCode()
                log.error(f"Error al crear canal de tempo BASS_FX (BASS error: {code})")
                # Fallback al stream decodificador directamente sin tempo si falla (aunque perderemos tempo)
                self._tempo_handle = self._decode_handle
            else:
                self._tempo_handle = tempo
                
            # 3. Aplicar configuraciones de volumen, velocidad y ecualizador previas
            self.establecer_volumen(self._volumen)
            self.establecer_velocidad(self._velocidad)
            self._fx_eq = self._fx_preamp = 0
            self._aplicar_ecualizador()
            try:
                from basskit.constantes.bass import BASS_SYNC_END
                bass.BASS_ChannelSetSync(self._tempo_handle, BASS_SYNC_END, 0, self._sync_fin, None)
            except Exception as e:
                log.debug(f"No se pudo pedir el aviso de fin de pista: {e}")
            
            log.info(f"Medio cargado correctamente en backend basskit: {ruta_o_url[:60]}")
            return True
            
        except Exception as e:
            log.error(f"Excepción al cargar medio {ruta_o_url}: {e}")
            self._ultimo_error_codigo = -1
            self._ultimo_error_texto = "No se pudo iniciar la reproducción del medio."
            self.cerrar()
            return False

    def reproducir(self):
        if self._tempo_handle:
            bass.BASS_ChannelPlay(self._tempo_handle, 0)

    def pausar(self):
        if self._tempo_handle:
            bass.BASS_ChannelPause(self._tempo_handle)

    def detener(self):
        if self._tempo_handle:
            bass.BASS_ChannelStop(self._tempo_handle)
            bass.BASS_ChannelSetPosition(self._tempo_handle, 0, BASS_POS_BYTE)

    def seek(self, segundos: float):
        if self._tempo_handle:
            # Nunca antes del principio: un segundo negativo, en QWORD sin signo, era un número
            # enorme y la búsqueda saltaba al final.
            pos_bytes = bass.BASS_ChannelSeconds2Bytes(self._tempo_handle, max(0.0, float(segundos)))
            if not _qword_ok(pos_bytes):
                return
            # Asegurar límites
            total_bytes = bass.BASS_ChannelGetLength(self._tempo_handle, BASS_POS_BYTE)
            if _qword_ok(total_bytes) and 0 < total_bytes <= pos_bytes:
                # BASS no acepta colocarse EXACTAMENTE en el final (la búsqueda fallaba y la
                # posición no cambiaba): se deja una décima antes.
                margen = bass.BASS_ChannelSeconds2Bytes(self._tempo_handle, 0.1)
                margen = margen if _qword_ok(margen) else 0
                pos_bytes = max(0, total_bytes - margen)
            bass.BASS_ChannelSetPosition(self._tempo_handle, pos_bytes, BASS_POS_BYTE)

    def obtener_posicion(self) -> float:
        if not self._tempo_handle:
            return 0.0
        pos_bytes = bass.BASS_ChannelGetPosition(self._tempo_handle, BASS_POS_BYTE)
        if not _qword_ok(pos_bytes):
            return 0.0
        return max(0.0, float(bass.BASS_ChannelBytes2Seconds(self._tempo_handle, pos_bytes)))

    def obtener_duracion(self) -> float:
        if not self._tempo_handle:
            return 0.0
        len_bytes = bass.BASS_ChannelGetLength(self._tempo_handle, BASS_POS_BYTE)
        if not _qword_ok(len_bytes) or len_bytes == 0:
            # Directo (radio, streaming en vivo): sin duración.
            return 0.0
        return max(0.0, float(bass.BASS_ChannelBytes2Seconds(self._tempo_handle, len_bytes)))

    def obtener_estado(self) -> str:
        if not self._tempo_handle:
            return "inactivo"
        act = bass.BASS_ChannelIsActive(self._tempo_handle)
        if act == BASS_ACTIVE_PLAYING:
            return "reproduciendo"
        elif act == BASS_ACTIVE_PAUSED:
            return "pausado"
        elif act == BASS_ACTIVE_STALLED:
            return "esperando" # Buffering
        else:
            return "detenido"

    def establecer_volumen(self, vol: float):
        self._volumen = max(0.0, min(1.0, vol))
        if self._tempo_handle:
            bass.BASS_ChannelSetAttribute(self._tempo_handle, BASS_ATTRIB_VOL, self._volumen)

    def obtener_volumen(self) -> float:
        return self._volumen

    def establecer_velocidad(self, speed: float):
        self._velocidad = max(VELOCIDAD_MIN, min(VELOCIDAD_MAX, round(float(speed), 2)))
        self._aplicar_tempo(self._velocidad)

    def _aplicar_tempo(self, velocidad: float):
        """Pone el tempo del canal sin cambiar la velocidad elegida (saltar silencios lo usa
        para acelerar un momento). En BASS_FX el tempo es un porcentaje: 0 % es 1x, 50 % 1,5x."""
        if self._tempo_handle and self._tempo_handle != self._decode_handle:
            porcentaje = (float(velocidad) - 1.0) * 100.0
            bass.BASS_ChannelSetAttribute(self._tempo_handle, BASS_ATTRIB_TEMPO, float(porcentaje))

    # ------------------------------------------------------------------ ecualizador y nivel
    def establecer_ecualizador(self, ganancias, preamp_db: float = 0.0):
        """Ganancias en dB (-15..+15) de las 10 bandas de BANDAS_EQ y preamplificador en dB.
        Todo a 0 quita los efectos (sin coste de procesado)."""
        g = [max(-15.0, min(15.0, float(x or 0))) for x in list(ganancias or [])[:len(BANDAS_EQ)]]
        self._eq_ganancias = g + [0.0] * (len(BANDAS_EQ) - len(g))
        self._eq_preamp = max(-15.0, min(15.0, float(preamp_db or 0)))
        self._aplicar_ecualizador()

    def obtener_ecualizador(self):
        return list(self._eq_ganancias), self._eq_preamp

    def _aplicar_ecualizador(self):
        canal = self._tempo_handle
        if not canal:
            return
        try:
            from basskit.constantes.bass_fx import BASS_FX_BFX_PEAKEQ, BASS_FX_BFX_VOLUME, BASS_BFX_CHANALL
            from basskit.estructuras.addons import BASS_BFX_PEAKEQ, BASS_BFX_VOLUME
            activo = any(abs(x) > 0.01 for x in self._eq_ganancias)
            if not activo and self._fx_eq:
                bass.BASS_ChannelRemoveFX(canal, self._fx_eq)
                self._fx_eq = 0
            if activo:
                if not self._fx_eq:
                    self._fx_eq = bass.BASS_ChannelSetFX(canal, BASS_FX_BFX_PEAKEQ, 0) or 0
                for i, (freq, ganancia) in enumerate(zip(BANDAS_EQ, self._eq_ganancias)):
                    p = BASS_BFX_PEAKEQ(lBand=i, fBandwidth=1.0, fQ=0.0, fCenter=float(freq),
                                        fGain=float(ganancia), lChannel=BASS_BFX_CHANALL)
                    bass.BASS_FXSetParameters(self._fx_eq, ctypes.byref(p))
            if abs(self._eq_preamp) <= 0.01 and self._fx_preamp:
                bass.BASS_ChannelRemoveFX(canal, self._fx_preamp)
                self._fx_preamp = 0
            elif abs(self._eq_preamp) > 0.01:
                if not self._fx_preamp:
                    self._fx_preamp = bass.BASS_ChannelSetFX(canal, BASS_FX_BFX_VOLUME, 1) or 0
                p = BASS_BFX_VOLUME(lChannel=0, fVolume=float(10 ** (self._eq_preamp / 20.0)))
                bass.BASS_FXSetParameters(self._fx_preamp, ctypes.byref(p))
        except Exception as e:
            log.debug(f"No se pudo aplicar el ecualizador: {e}")

    def nivel_db(self, ventana: float = 0.05) -> float:
        """Nivel (RMS, en dB) de lo que está sonando ahora; -100 si no hay señal o no suena.
        Lo usa "saltar silencios" para saber cuándo hay un silencio."""
        if not self._tempo_handle:
            return -100.0
        try:
            import math
            from basskit.constantes.bass import BASS_LEVEL_MONO, BASS_LEVEL_RMS
            nivel = (ctypes.c_float * 1)()
            if not bass.BASS_ChannelGetLevelEx(self._tempo_handle, nivel, float(ventana),
                                               BASS_LEVEL_MONO | BASS_LEVEL_RMS):
                return -100.0
            return 20.0 * math.log10(nivel[0]) if nivel[0] > 0 else -100.0
        except Exception:
            return -100.0

    def obtener_velocidad(self) -> float:
        return self._velocidad

    def cerrar(self):
        """Libera canales de reproducción abiertos."""
        if self._tempo_handle:
            try:
                # BASS_StreamFree sobre el tempo liberará el decodificador original si BASS_FX_FREESOURCE está activo
                bass.BASS_StreamFree(self._tempo_handle)
            except Exception as e:
                log.debug(f"Error al liberar canal de tempo: {e}")
            finally:
                self._tempo_handle = 0
                self._decode_handle = 0
