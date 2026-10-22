# -*- coding: utf-8 -*-
"""
Módulo para la clase base abstracta de backends de reproducción multimedia.
Define el contrato para poder intercambiar motores de audio (basskit, VLC, etc.).
"""

from abc import ABC, abstractmethod

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
