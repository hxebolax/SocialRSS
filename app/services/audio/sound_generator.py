# -*- coding: utf-8 -*-
"""
Módulo para la generación sintética de sonidos WAV por defecto.
Permite tener efectos sonoros de forma legal, limpia y sin dependencias de ficheros externos.
"""

import math
import struct
import wave
import logging
from pathlib import Path
from app.core.paths import get_resource_path

log = logging.getLogger(__name__)

def generar_nota_arpegio(sample_rate, t, duracion, notas_freq, volumen):
    """Genera una muestra para un arpegio ascendente, sin clics entre notas."""
    num_notas = len(notas_freq)
    dur_nota = duracion / num_notas
    nota_idx = min(int(t / dur_nota), num_notas - 1)
    freq = notas_freq[nota_idx]

    # Tiempo local dentro de la nota: reinicia la fase en cada nota (evita saltos)
    t_local = t - nota_idx * dur_nota
    val = math.sin(2.0 * math.pi * freq * t_local)
    # Cada nota con su propia envolvente suave
    return val * _env_segmento(t_local, dur_nota) * volumen

def generar_campana(sample_rate, t, duracion, freq, volumen):
    """Genera sonido de campana con decaimiento exponencial y fade final."""
    val = math.sin(2.0 * math.pi * freq * t)
    decaimiento = math.exp(-3.0 * t / duracion)
    # Fade-out en los últimos 10 ms para que no quede un corte audible (clic)
    ramp = 0.01
    if duracion - t < ramp:
        decaimiento *= max(0.0, (duracion - t) / ramp)
    return val * decaimiento * volumen

def _env_segmento(t_local, dur_segmento, ramp=0.008):
    """
    Envolvente lineal suave (fade-in/out) para un segmento, evitando 'clicks'.
    ramp = duración del desvanecimiento en segundos (8 ms por defecto).
    """
    ramp = min(ramp, dur_segmento / 2.0)
    if ramp <= 0:
        return 1.0
    if t_local < ramp:
        return t_local / ramp
    if dur_segmento - t_local < ramp:
        return max(0.0, (dur_segmento - t_local) / ramp)
    return 1.0

def generar_wav(ruta: Path, tipo: str, duracion: float = 0.3, freq: float = 440.0, volumen: float = 0.5):
    """Sintetiza un archivo WAV mono, de 16 bits, a 44100 Hz."""
    sample_rate = 44100
    num_samples = int(duracion * sample_rate)
    
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(ruta), "wb") as w:
            w.setnchannels(1)     # Mono
            w.setsampwidth(2)     # 16-bit (2 bytes por muestra)
            w.setframerate(sample_rate)
            
            for i in range(num_samples):
                t = float(i) / sample_rate
                
                if tipo == "sine":
                    # Simple tono senoidal
                    val = math.sin(2.0 * math.pi * freq * t)
                    # Envolvente
                    if t < 0.01: env = t / 0.01
                    elif duracion - t < 0.01: env = (duracion - t) / 0.01
                    else: env = 1.0
                    val *= env * volumen
                    
                elif tipo == "double":
                    # Doble pitido con silencio en medio. Cada pitido lleva su
                    # propia envolvente suave (fade-in/out) para no producir clics.
                    fin1 = 0.4 * duracion
                    ini2 = 0.6 * duracion
                    if t < fin1:
                        # Reiniciar fase en el segmento para evitar discontinuidades
                        tl = t
                        val = math.sin(2.0 * math.pi * freq * tl)
                        val *= _env_segmento(tl, fin1) * volumen
                    elif t >= ini2:
                        tl = t - ini2
                        val = math.sin(2.0 * math.pi * freq * tl)
                        val *= _env_segmento(tl, duracion - ini2) * volumen
                    else:
                        val = 0.0

                elif tipo == "arpeggio":
                    # Arpegio ascendente (Do, Mi, Sol, Do)
                    notas = [freq, freq * 1.25, freq * 1.5, freq * 2.0]
                    val = generar_nota_arpegio(sample_rate, t, duracion, notas, volumen)
                    
                elif tipo == "bell":
                    # Campana
                    val = generar_campana(sample_rate, t, duracion, freq, volumen)
                    
                elif tipo == "buzz":
                    # Sonido zumbido de error (onda cuadrada gruesa de baja frecuencia)
                    val_sin = math.sin(2.0 * math.pi * freq * t)
                    val = 1.0 if val_sin >= 0 else -1.0
                    # Zumbido doble
                    if 0.45 * duracion < t < 0.55 * duracion:
                        val = 0.0
                    else:
                        # Envolvente
                        if t < 0.01 or duracion - t < 0.01: env = 0.2
                        else: env = 1.0
                        val *= env * volumen
                else:
                    val = math.sin(2.0 * math.pi * freq * t) * volumen
                
                # Escalar a entero de 16 bits con signo
                scaled = int(max(-1.0, min(1.0, val)) * 32767)
                w.writeframesraw(struct.pack("<h", scaled))
                
        log.debug(f"Sonido autogenerado escrito en: {ruta}")
    except Exception as e:
        log.error(f"Error al generar archivo WAV {ruta}: {e}")

# Versión del set de sonidos. Al subirla, se regeneran todos para aplicar
# mejoras de síntesis (p. ej. eliminar chisporroteos) en instalaciones existentes.
SOUND_PACK_VERSION = "3"

def generar_todos_los_sonidos_predeterminados():
    """Genera el set completo de sonidos en data/sounds/default si no existen
    o si la versión del set ha cambiado (para aplicar mejoras de calidad)."""
    default_dir = get_resource_path("sounds/default")
    default_dir.mkdir(parents=True, exist_ok=True)

    # Comprobar versión; si cambió, borrar los wav para regenerarlos
    version_file = default_dir / ".version"
    version_actual = ""
    try:
        if version_file.exists():
            version_actual = version_file.read_text(encoding="utf-8").strip()
    except Exception:
        version_actual = ""
    regenerar = version_actual != SOUND_PACK_VERSION
    if regenerar:
        for wav in default_dir.glob("*.wav"):
            try:
                wav.unlink()
            except Exception:
                pass

    sonidos = {
        "startup.wav": ("arpeggio", 0.5, 261.63, 0.4),      # Do central, arpegio de entrada
        "ready.wav": ("double", 0.15, 880.0, 0.3),          # Doble pitido agudo (operación lista)
        "new_item.wav": ("arpeggio", 0.25, 523.25, 0.3),    # Pitido ascendente de dos notas (nuevo item)
        "error.wav": ("buzz", 0.35, 120.0, 0.25),           # Zumbido grave doble (error)
        "notification.wav": ("bell", 0.4, 659.25, 0.35),     # Sonido de campana (Mi agudo)
        "media.wav": ("bell", 0.2, 880.0, 0.3),             # Pitido campana agudo (adjunto multimedia genérico)
        "direct.wav": ("double", 0.2, 587.33, 0.3),         # Pitido doble medio (mensaje directo)
        "cw.wav": ("sine", 0.15, 220.0, 0.3),               # Pitido grave corto (advertencia de contenido)
        "poll.wav": ("arpeggio", 0.3, 493.88, 0.3),         # Arpegio descendente distintivo (encuesta)
        # Sonidos identificativos por tipo de multimedia
        "media_image.wav": ("bell", 0.18, 1046.5, 0.3),     # Campana muy aguda (imagen/foto)
        "media_audio.wav": ("double", 0.18, 740.0, 0.3),    # Doble pitido medio-agudo (audio / nota de voz)
        "media_video.wav": ("arpeggio", 0.22, 660.0, 0.3),  # Arpegio (vídeo)
        "media_document.wav": ("sine", 0.15, 392.0, 0.3),   # Pitido medio (documento/archivo)
    }
    
    for nombre, (tipo, dur, freq, vol) in sonidos.items():
        ruta_archivo = default_dir / nombre
        if not ruta_archivo.exists():
            log.info(f"Generando sonido: {nombre}")
            generar_wav(ruta_archivo, tipo, dur, freq, vol)

    # Guardar la versión del set generado
    try:
        version_file.write_text(SOUND_PACK_VERSION, encoding="utf-8")
    except Exception:
        pass
