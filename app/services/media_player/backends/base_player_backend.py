# -*- coding: utf-8 -*-
"""
Módulo para la clase base abstracta de backends de reproducción multimedia.
Define el contrato para poder intercambiar motores de audio (basskit, VLC, etc.).
"""

from abc import ABC, abstractmethod

# Ecualizador de 10 bandas (Hz) y límites de velocidad: iguales para todos los motores.
BANDAS_EQ = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
VELOCIDAD_MIN, VELOCIDAD_MAX = 0.5, 3.0

class BasePlayerBackend(ABC):
    """Interfaz abstracta para motores de reproducción de audio/vídeo."""
    
    @abstractmethod
    def cargar(self, ruta_o_url: str) -> bool:
        """Carga un archivo local o URL de streaming. Devuelve True si tiene éxito."""
        
    @abstractmethod
    def reproducir(self):
        """Inicia o reanuda la reproducción."""
        
    @abstractmethod
    def pausar(self):
        """Pausa la reproducción."""
        
    @abstractmethod
    def detener(self):
        """Detiene la reproducción y vuelve al segundo 0."""
        
    @abstractmethod
    def seek(self, segundos: float):
        """Salta a una posición de tiempo en segundos."""
        
    @abstractmethod
    def obtener_posicion(self) -> float:
        """Devuelve la posición actual de reproducción en segundos."""
        
    @abstractmethod
    def obtener_duracion(self) -> float:
        """Devuelve la duración total en segundos (o 0.0 si es streaming)."""
        
    @abstractmethod
    def obtener_estado(self) -> str:
        """Devuelve el estado: 'reproduciendo', 'pausado', 'detenido', 'esperando', 'inactivo'."""
        
    @abstractmethod
    def establecer_volumen(self, vol: float):
        """Ajusta el volumen (de 0.0 a 1.0)."""
        
    @abstractmethod
    def obtener_volumen(self) -> float:
        """Devuelve el volumen actual (de 0.0 a 1.0)."""
        
    @abstractmethod
    def establecer_velocidad(self, speed: float):
        """Establece la velocidad/tempo de reproducción (de 0.5 a 3.0)."""
        
    @abstractmethod
    def obtener_velocidad(self) -> float:
        """Devuelve la velocidad actual de reproducción."""
        
    @abstractmethod
    def cerrar(self):
        """Cierra el canal actual y libera recursos asociados."""

    # --- Opcionales: un motor que no los ofrezca conserva este comportamiento neutro ---
    al_terminar = None  # callable sin argumentos que el motor llama (en otro hilo) al acabar la pista

    def ultimo_error(self) -> str:
        """Mensaje legible del último fallo de carga, o cadena vacía."""
        return ""

    def establecer_ecualizador(self, ganancias, preamp_db: float = 0.0):
        """Ganancias en dB (-15..+15) de las bandas de BANDAS_EQ y preamplificador en dB."""

    def obtener_ecualizador(self):
        return [0.0] * len(BANDAS_EQ), 0.0

    def nivel_db(self, ventana: float = 0.05) -> float:
        """Nivel (RMS, dB) de lo que suena ahora; -100 si no hay señal o no se puede medir."""
        return -100.0
