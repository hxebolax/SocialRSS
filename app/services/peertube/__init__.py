# -*- coding: utf-8 -*-
"""Servicio de integración con PeerTube (API REST v1, multi-instancia).

Expone la fachada `peertube_service` (singleton) y las banderas de disponibilidad. El cliente HTTP
(`httpx`) y `pydantic` se importan de forma diferida dentro de los módulos del servicio; importar
este paquete NO carga esas librerías pesadas al arrancar.
"""
from app.services.peertube.peertube_common import HAS_HTTPX
from app.services.peertube.peertube_service import peertube_service, PeerTubeService

__all__ = ["peertube_service", "PeerTubeService", "HAS_HTTPX"]
