# -*- coding: utf-8 -*-
"""
Lector de texto en voz alta (TTS) dedicado, independiente del lector de pantalla.

Usa la voz SAPI5 de Windows (a través de win32com) para leer artículos completos
con controles de reproducir, pausar, reanudar y detener, además de velocidad y
selección de voz. Pensado para escuchar artículos de RSS (paridad N.5 de SimpleRSS)
pero es genérico y se puede reutilizar para cualquier texto largo.

Se invoca SIEMPRE desde el hilo de la interfaz (wx ya inicializa COM en ese hilo);
la lectura es asíncrona (la propia SAPI usa su hilo interno), por lo que no bloquea.
"""

import logging

log = logging.getLogger(__name__)

# Banderas de SpVoice.Speak
_SVSF_ASYNC = 1            # Hablar en segundo plano (no bloquear)
_SVSF_PURGE = 2            # Vaciar la cola antes de hablar (sirve para detener)


class TTSReader:
    """Envoltura sencilla y accesible sobre la voz SAPI5 de Windows."""

    def __init__(self):
        self._voice = None
        self._pausado = False
        self._disponible = None  # None = sin comprobar

    def disponible(self) -> bool:
        """Indica si hay motor SAPI5 disponible en el sistema."""
        if self._disponible is None:
            self._disponible = self._asegurar_voz()
        return bool(self._disponible)

    def _asegurar_voz(self) -> bool:
        if self._voice is not None:
            return True
        try:
            import win32com.client
            self._voice = win32com.client.Dispatch("SAPI.SpVoice")
            return True
        except Exception as e:
            log.warning(f"No hay TTS SAPI5 disponible: {e}")
            self._voice = None
            return False

    # --- Voces ---
    def listar_voces(self) -> list:
        """Devuelve los nombres de las voces instaladas."""
        if not self._asegurar_voz():
            return []
        nombres = []
        try:
            tokens = self._voice.GetVoices()
            for i in range(tokens.Count):
                nombres.append(tokens.Item(i).GetDescription())
        except Exception as e:
            log.debug(f"No se pudieron listar las voces: {e}")
        return nombres

    def establecer_voz(self, indice: int):
        """Selecciona la voz por índice (según listar_voces)."""
        if not self._asegurar_voz():
            return
        try:
            tokens = self._voice.GetVoices()
            if 0 <= indice < tokens.Count:
                self._voice.Voice = tokens.Item(indice)
        except Exception as e:
            log.debug(f"No se pudo establecer la voz: {e}")

    def establecer_velocidad(self, valor: int):
        """Ajusta la velocidad de lectura (rango SAPI -10 a 10)."""
        if not self._asegurar_voz():
            return
        try:
            self._voice.Rate = max(-10, min(10, int(valor)))
        except Exception as e:
            log.debug(f"No se pudo establecer la velocidad: {e}")

    def establecer_volumen(self, valor: int):
        """Ajusta el volumen (0 a 100)."""
        if not self._asegurar_voz():
            return
        try:
            self._voice.Volume = max(0, min(100, int(valor)))
        except Exception as e:
            log.debug(f"No se pudo establecer el volumen: {e}")

    # --- Control de reproducción ---
    def leer(self, texto: str):
        """Comienza a leer un texto desde el principio (detiene lo anterior)."""
        if not self._asegurar_voz() or not texto:
            return
        try:
            self.detener()
            self._pausado = False
            self._voice.Speak(texto, _SVSF_ASYNC)
        except Exception as e:
            log.error(f"Error al iniciar la lectura TTS: {e}")

    def pausar(self):
        if not self._voice:
            return
        try:
            self._voice.Pause()
            self._pausado = True
        except Exception as e:
            log.debug(f"No se pudo pausar: {e}")

    def reanudar(self):
        if not self._voice:
            return
        try:
            self._voice.Resume()
            self._pausado = False
        except Exception as e:
            log.debug(f"No se pudo reanudar: {e}")

    def alternar_pausa(self) -> bool:
        """Pausa o reanuda. Devuelve True si quedó pausado."""
        if self._pausado:
            self.reanudar()
        else:
            self.pausar()
        return self._pausado

    def detener(self):
        """Detiene la lectura y vacía la cola."""
        if not self._voice:
            return
        try:
            # Si estaba en pausa hay que reanudar antes de purgar, o se queda colgado.
            if self._pausado:
                self._voice.Resume()
                self._pausado = False
            self._voice.Speak("", _SVSF_ASYNC | _SVSF_PURGE)
        except Exception as e:
            log.debug(f"No se pudo detener: {e}")

    def esta_pausado(self) -> bool:
        return self._pausado

    def esta_hablando(self) -> bool:
        """True si SAPI está pronunciando algo ahora mismo. Sirve para encadenar la lectura por
        frases: cuando deja de hablar, se pasa a la frase siguiente. (RunningState: 2 = hablando.)"""
        if not self._voice:
            return False
        try:
            return int(self._voice.Status.RunningState) == 2
        except Exception:
            return False


# Instancia única reutilizable
tts_reader = TTSReader()
