# -*- coding: utf-8 -*-
"""
Módulo para la clase base de proveedores de traducción.
Define el contrato para traducir textos detectando el idioma origen de forma opcional.
"""

from abc import ABC, abstractmethod

class BaseTranslationProvider(ABC):
    """Interfaz abstracta para motores de traducción."""
    
    @abstractmethod
    def traducir(self, texto: str, idioma_origen: str, idioma_destino: str) -> str:
        """Traduce texto de un idioma de origen a uno de destino. Devuelve el texto traducido."""

    @abstractmethod
    def detectar_idioma(self, texto: str) -> str:
        """Detecta el idioma del texto de entrada. Devuelve el código de idioma ISO (ej: 'en', 'es')."""
