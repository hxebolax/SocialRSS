# -*- coding: utf-8 -*-
"""
Módulo para la definición de excepciones personalizadas de la aplicación.
"""

class SocialRSSError(Exception):
    """Excepción base para todos los errores de SocialRSS Suite.

    Atributo `sin_traza`: márcalo a True cuando el fallo YA ESTÁ DIAGNOSTICADO y depende del
    usuario o del servidor remoto (una sesión caducada que hay que renovar, un servidor que
    responde cualquier cosa menos la API). Esos errores se repiten en CADA sondeo mientras la
    causa siga ahí, y volcar su traza completa cada vez llena el registro de miles de líneas
    idénticas que tapan los fallos de verdad. El gestor de tareas los anota en UNA línea y sin
    repetirla. No lo uses para errores inesperados: esos SÍ necesitan la traza."""

    sin_traza = False

class DatabaseError(SocialRSSError):
    """Excepción para errores de base de datos SQLite."""

class ServiceError(SocialRSSError):
    """Excepción base para errores en servicios externos o plugins."""

class NetworkError(ServiceError):
    """Excepción para problemas de conectividad o red."""

class MastodonServiceError(ServiceError):
    """Excepción específica para errores de la API de Mastodon o límites de tasa."""

class RSSServiceError(ServiceError):
    """Excepción para fallos al procesar, parsear o descargar feeds RSS."""

class TelegramServiceError(ServiceError):
    """Excepción específica para errores del cliente de Telegram (MTProto)."""

class BlueskyServiceError(ServiceError):
    """Excepción específica para errores de la API de Bluesky / AT Protocol."""

class WSocialServiceError(ServiceError):
    """Excepción específica para errores de W Social (AT Protocol): identidad, PDS, sesión o API."""

class IRCServiceError(ServiceError):
    """Excepción específica para errores del cliente de IRC."""

class RedditServiceError(ServiceError):
    """Excepción específica para errores de la API de Reddit (PRAW) o límites de tasa."""

class PeerTubeServiceError(ServiceError):
    """Excepción específica para errores de la API REST de PeerTube (HTTP), de detección de
    instancia, autenticación o límites de tasa."""

class AIError(SocialRSSError):
    """Excepción para errores relacionados con proveedores de Inteligencia Artificial."""

class TranslationError(SocialRSSError):
    """Excepción para fallos en el motor de traducción."""

class MediaPlayerError(SocialRSSError):
    """Excepción para fallos del reproductor de audio/vídeo o del motor basskit."""
