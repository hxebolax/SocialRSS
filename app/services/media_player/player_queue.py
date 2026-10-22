# -*- coding: utf-8 -*-
"""
Módulo para la gestión de la cola de reproducción multimedia.
Mantiene una lista ordenada de pistas con soporte de avance y retroceso.
"""

import logging
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

class PlayerQueue:
    """Administrador de la cola de reproducción."""
    
    def __init__(self):
        self._items: List[Dict[str, Any]] = []
        self._index: int = -1

    def agregar(self, media_url: str, title: str, service_origin: str = "", account_origin_id: Optional[int] = None) -> Dict[str, Any]:
        """Añade un elemento a la cola de reproducción."""
        item = {
            "media_url": media_url,
            "title": title,
            "service_origin": service_origin,
            "account_origin_id": account_origin_id
        }
        self._items.append(item)
        log.info(f"Añadido a la cola: '{title}' ({media_url[:40]})")
        
        # Si la cola estaba vacía, apuntar al primer elemento recién agregado
        if self._index == -1:
            self._index = 0
            
        return item

    def quitar(self, index: int) -> Optional[Dict[str, Any]]:
        """Quita un elemento de la cola por su índice."""
        if 0 <= index < len(self._items):
            removed = self._items.pop(index)
            # Ajustar índice actual
            if self._index >= len(self._items):
                self._index = len(self._items) - 1
            if not self._items:
                self._index = -1
            log.info(f"Quitado de la cola: '{removed['title']}'")
            return removed
        return None

    def subir(self, index: int) -> bool:
        """Mueve un elemento hacia arriba en la cola."""
        if 0 < index < len(self._items):
            # Intercambiar
            self._items[index], self._items[index - 1] = self._items[index - 1], self._items[index]
            # Ajustar puntero del elemento seleccionado si es necesario
            if self._index == index:
                self._index = index - 1
            elif self._index == index - 1:
                self._index = index
            return True
        return False

    def bajar(self, index: int) -> bool:
        """Mueve un elemento hacia abajo en la cola."""
        if 0 <= index < len(self._items) - 1:
            # Intercambiar
            self._items[index], self._items[index + 1] = self._items[index + 1], self._items[index]
            # Ajustar puntero
            if self._index == index:
                self._index = index + 1
            elif self._index == index + 1:
                self._index = index
            return True
        return False

    def limpiar(self):
        """Limpia por completo la cola."""
        self._items.clear()
        self._index = -1
        log.info("Cola de reproducción vaciada.")

    def obtener_items(self) -> List[Dict[str, Any]]:
        """Devuelve una lista con todos los elementos de la cola."""
        return self._items

    def obtener_indice_actual(self) -> int:
        """Devuelve el índice del elemento activo."""
        return self._index

    def establecer_indice_actual(self, index: int):
        """Establece la posición actual en la cola."""
        if -1 <= index < len(self._items):
            self._index = index

    def obtener_actual(self) -> Optional[Dict[str, Any]]:
        """Devuelve el elemento que se está reproduciendo actualmente."""
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return None

    def avanzar(self) -> Optional[Dict[str, Any]]:
        """Pasa al siguiente elemento de la cola."""
        if self._index < len(self._items) - 1:
            self._index += 1
            return self.obtener_actual()
        return None

    def retroceder(self) -> Optional[Dict[str, Any]]:
        """Vuelve al elemento anterior de la cola."""
        if self._index > 0:
            self._index -= 1
            return self.obtener_actual()
        return None

    def __len__(self) -> int:
        return len(self._items)
