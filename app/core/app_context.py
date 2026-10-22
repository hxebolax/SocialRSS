# -*- coding: utf-8 -*-
"""
Módulo para el contexto global de ejecución de la aplicación.
Mantiene referencias de estado activas en tiempo de ejecución (hilo de UI principal, etc).
"""

from typing import Optional, Dict, Any

class AppContext:
    """Clase singleton para mantener el estado de ejecución global."""
    
    def __init__(self):
        self.main_frame: Optional[Any] = None
        self.active_mastodon_account_id: Optional[int] = None
        self.active_rss_account_id: Optional[int] = None
        self.services: Dict[str, Any] = {}
        self.accounts: Dict[int, Any] = {}
        self.debug_mode: bool = False
        
    def registrar_servicio(self, service_id: str, service_instance: Any):
        """Registra una instancia de servicio activa."""
        self.services[service_id] = service_instance

    def obtener_servicio(self, service_id: str) -> Optional[Any]:
        """Obtiene un servicio registrado por su ID."""
        return self.services.get(service_id)

# Instancia global única del contexto
app_context = AppContext()
