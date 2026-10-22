# -*- coding: utf-8 -*-
"""
Módulo para la clase base de proveedores de Inteligencia Artificial.
Define los métodos comunes para todos los modelos de IA integrados.
"""

from abc import ABC, abstractmethod

class BaseAIProvider(ABC):
    """Interfaz abstracta para interactuar con modelos de IA."""
    
    @abstractmethod
    def probar_conexion(self) -> bool:
        """Verifica si la conexión y la clave API son válidas."""

    @abstractmethod
    def describir_imagen(self, ruta_imagen: str, prompt: str) -> str:
        """Describe una imagen a partir de una ruta local y un prompt."""

    @abstractmethod
    def generar_texto_alternativo(self, ruta_imagen: str, prompt: str) -> str:
        """Genera un texto descriptivo corto (alt text) para una imagen."""

    @abstractmethod
    def resumir_texto(self, texto: str, prompt: str) -> str:
        """Generates a summary of the input text using a customized prompt."""

    @abstractmethod
    def sugerir_hashtags(self, texto: str, prompt: str) -> str:
        """Sugiere hashtags relevantes para el texto proporcionado."""

    @abstractmethod
    def mejorar_redaccion(self, texto: str) -> str:
        """Corrige la ortografía y mejora la redacción del texto en español."""
