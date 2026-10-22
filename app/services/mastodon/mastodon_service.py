# -*- coding: utf-8 -*-
"""
Servicio central de integración con Mastodon utilizando Mastodon.py.
Gestiona el ciclo de vida de las cuentas, OAuth, obtención de timelines y almacenamiento local.
"""

import logging
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Callable
from mastodon import Mastodon
from app.core.database import db
from app.core.config_manager import config
from app.core.security import security
from app.core.errors import MastodonServiceError
from app.core.task_manager import task_manager
from app.core.models import MastodonStatus

log = logging.getLogger(__name__)

from app.services.mastodon.mastodon_cuentas import _CuentasMixin
from app.services.mastodon.mastodon_timelines import _TimelinesMixin
from app.services.mastodon.mastodon_acciones import _AccionesMixin
from app.services.mastodon.mastodon_social import _SocialMixin
from app.services.mastodon.mastodon_perfil import _PerfilMixin
from app.services.mastodon.mastodon_admin import _AdminMixin
from app.services.mastodon.mastodon_colecciones import _ColeccionesMixin
from app.services.mastodon.mastodon_filtros import _FiltrosMixin
from app.services.mastodon.mastodon_citas import _CitasMixin

class MastodonService(_CuentasMixin, _TimelinesMixin, _AccionesMixin, _SocialMixin, _PerfilMixin, _AdminMixin,
                      _ColeccionesMixin, _FiltrosMixin, _CitasMixin):
    """Administrador de cuentas y operaciones de la API de Mastodon."""
    
    def __init__(self):
        self._clients: Dict[int, Mastodon] = {}
        self._me_ids: Dict[int, int] = {}  # caché del id de usuario propio por cuenta
        self._prefs_cache: Dict[int, dict] = {}  # preferencias del servidor por cuenta
        self._char_limit_cache: Dict[int, int] = {}  # límite de caracteres de la instancia por cuenta
        self._desc_limit_cache: Dict[int, int] = {}   # límite de la descripción (alt-text) por cuenta


# Instancia global única del servicio de Mastodon
mastodon_service = MastodonService()
