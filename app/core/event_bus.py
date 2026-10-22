# -*- coding: utf-8 -*-
"""
Módulo para el bus de eventos interno (Publish/Subscribe) de la aplicación.
Permite la comunicación desacoplada y segura entre servicios y la interfaz.
"""

import logging
import threading
from typing import Dict, List, Callable

log = logging.getLogger(__name__)

class EventBus:
    """Bus de eventos global thread-safe."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: str, callback: Callable):
        """Suscribe una función callback a un tipo de evento."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
            log.debug(f"Suscrito a evento '{event_type}': {callback.__name__}")

    def unsubscribe(self, event_type: str, callback: Callable):
        """Cancela la suscripción de un callback."""
        with self._lock:
            if event_type in self._subscribers:
                if callback in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(callback)
                    log.debug(f"Cancelada suscripción a '{event_type}': {callback.__name__}")
                if not self._subscribers[event_type]:
                    del self._subscribers[event_type]

    def emit(self, event_type: str, *args, **kwargs):
        """Emite un evento a todos sus suscriptores de forma síncrona/segura."""
        handlers = []
        with self._lock:
            if event_type in self._subscribers:
                # Copiar la lista para evitar errores de modificación concurrente al iterar
                handlers = list(self._subscribers[event_type])
        
        if not handlers:
            return
            
        for handler in handlers:
            try:
                # Ejecutar el handler con los argumentos proporcionados
                handler(*args, **kwargs)
            except Exception as e:
                log.error(f"Error al ejecutar callback {handler.__name__} para evento '{event_type}': {e}", exc_info=True)

# Instancia única del bus de eventos para la aplicación
event_bus = EventBus()
