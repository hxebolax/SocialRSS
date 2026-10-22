# -*- coding: utf-8 -*-
"""
Servicio central para el Lector de Noticias RSS y Podcasts.
Maneja la descarga asíncrona de feeds, almacenamiento, FTS5, importación/exportación OPML
y aplicación de filtros/reglas de contenido.
"""

import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
import feedparser

from app.core.database import db
from app.core.config_manager import config
from app.core.task_manager import task_manager
from app.core.models import RSSFeed, RSSArticle
from .opml import importar_opml, exportar_opml
from .rss_rules import RSSRulesEngine
from .rss_fulltext import FullTextExtractor
from app.core.content_filters import ContentFilterEngine

log = logging.getLogger(__name__)

from app.services.rss.rss_feeds import _FeedsMixin
from app.services.rss.rss_parseo import _ParseoMixin
from app.services.rss.rss_articulos import _ArticulosMixin
from app.services.rss.rss_youtube import _YoutubeMixin
from app.services.rss.rss_podcast import _PodcastColaMixin
from app.services.rss.rss_sync import _SyncMixin
from app.services.rss.rss_texto_completo import _TextoCompletoMixin
from app.services.rss.rss_descubrir import _DescubrirMixin
from app.services.rss.rss_buscador import _BuscadorMixin
from app.services.rss.rss_takeout import _TakeoutMixin
from app.services.rss.rss_archivo import _ArchivoMixin
from app.services.rss.rss_sync_subs import _SyncSuscripcionesMixin

class RSSService(_FeedsMixin, _ParseoMixin, _ArticulosMixin, _YoutubeMixin,
                 _PodcastColaMixin, _SyncMixin, _TextoCompletoMixin, _DescubrirMixin,
                 _BuscadorMixin, _TakeoutMixin, _ArchivoMixin,
                 _SyncSuscripcionesMixin):
    """Servicio completo de lector de feeds RSS."""
    
    def __init__(self):
        pass


# Instancia global única del servicio RSS
rss_service = RSSService()
