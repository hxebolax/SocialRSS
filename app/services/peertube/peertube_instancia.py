# -*- coding: utf-8 -*-
"""Detección de instancias PeerTube y lectura de sus capacidades (Fase 1).

Mixin de `PeerTubeService`. Dada la URL que escribe el usuario, normaliza, comprueba que es una
instancia de PeerTube (por `GET /api/v1/config`, con NodeInfo como confirmación de respaldo), obtiene
la versión y las capacidades públicas (registro, directos, importación, cuota, política NSFW,
idiomas/categorías/licencias...) y las cachea en `peertube_instancias`. Una capacidad ausente en una
instancia hará que la UI oculte/desactive la función correspondiente, nunca que falle.
"""
import json
import logging
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from app.core.database import db
from app.core.errors import PeerTubeServiceError
from app.services.peertube.peertube_common import _ahora_iso, normalizar_base_url
from app.services.peertube.peertube_modelos import ServerConfig, parsear

log = logging.getLogger(__name__)


class _InstanciaMixin:
    def detectar_capacidades(self, url: str, *,
                             cancelar: Optional[threading.Event] = None) -> Dict[str, Any]:
        """Detecta la instancia y devuelve sus datos y capacidades. NO requiere cuenta (anónimo).

        Devuelve un dict: {host, base_url, es_peertube, version, config (ServerConfig), config_json}.
        Lanza `PeerTubeServiceError` si la URL no responde o no parece PeerTube. Cachea el resultado
        en `peertube_instancias`. Bloqueante: llamar desde task_manager."""
        base_url = normalizar_base_url(url)
        host = urlsplit(base_url).netloc
        cliente = self.cliente_para(base_url)
        try:
            try:
                datos = cliente.get("config", auth=False, cancelar=cancelar)
            except PeerTubeServiceError as e:
                # /config falló: puede no ser PeerTube o estar caída. Intentar confirmar por NodeInfo
                # para dar un mensaje más claro, y si tampoco, reportar que no parece PeerTube.
                if self._es_peertube_por_nodeinfo(cliente, cancelar):
                    raise PeerTubeServiceError(
                        "La instancia parece PeerTube pero su configuración no está accesible ahora. "
                        "Inténtalo más tarde.") from e
                raise PeerTubeServiceError(
                    "Esa dirección no responde como una instancia de PeerTube. Revisa la URL.") from e
            if not isinstance(datos, dict):
                raise PeerTubeServiceError("La instancia devolvió una configuración inesperada.")
            cfg = parsear(ServerConfig, datos)
            version = (cfg.server_version or "").strip()
            # Señal de que es PeerTube: /api/v1/config con serverVersion o bloque instance.
            if not version and not datos.get("instance"):
                if not self._es_peertube_por_nodeinfo(cliente, cancelar):
                    raise PeerTubeServiceError(
                        "Esa dirección no parece una instancia de PeerTube. Revisa la URL.")
            self._guardar_instancia(host, cfg, datos)
            return {"host": host, "base_url": base_url, "es_peertube": True,
                    "version": version, "config": cfg, "config_json": datos}
        finally:
            cliente.cerrar()

    def _es_peertube_por_nodeinfo(self, cliente, cancelar) -> bool:
        """Confirmación de respaldo: `/.well-known/nodeinfo` → documento con software.name=peertube."""
        try:
            indice = cliente.get("/.well-known/nodeinfo", auth=False, cancelar=cancelar)
            enlaces = (indice or {}).get("links") if isinstance(indice, dict) else None
            href = None
            for l in (enlaces or []):
                if isinstance(l, dict) and l.get("href"):
                    href = l["href"]  # el último rel suele ser el esquema más nuevo
            if not href:
                return False
            doc = cliente.get(href, auth=False, cancelar=cancelar)
            nombre = ((doc or {}).get("software") or {}).get("name") if isinstance(doc, dict) else ""
            return str(nombre or "").lower() == "peertube"
        except Exception:
            return False

    def _guardar_instancia(self, host: str, cfg: ServerConfig, config_json: dict) -> None:
        """Guarda/actualiza la instancia y sus capacidades en `peertube_instancias`."""
        instancia = cfg.instance
        signup = cfg.signup
        live = cfg.live
        importar = cfg.import_
        import_http = bool(importar and importar.videos and importar.videos.http
                           and importar.videos.http.enabled)
        try:
            db.execute(
                "INSERT OR REPLACE INTO peertube_instancias "
                "(host, name, version, signup_allowed, signup_requires_approval, live_enabled, "
                " import_videos_enabled, user_video_quota, nsfw_policy, capabilities_json, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (host,
                 (instancia.name if instancia else None),
                 cfg.server_version,
                 1 if (signup and signup.allowed) else 0,
                 1 if (signup and signup.requires_approval) else 0,
                 1 if (live and live.enabled) else 0,
                 1 if import_http else 0,
                 int(cfg.user.video_quota) if (cfg.user and cfg.user.video_quota is not None) else 0,
                 (instancia.default_nsfw_policy if instancia else None),
                 json.dumps(config_json)[:200000],  # cota defensiva de tamaño
                 _ahora_iso()))
        except Exception as e:
            log.debug(f"No se pudo cachear la instancia {host}: {e}")

    def capacidades_cacheadas(self, host: str) -> Optional[Dict[str, Any]]:
        """Devuelve las capacidades cacheadas de una instancia (dict) o None si no hay caché."""
        try:
            fila = db.query_one(
                "SELECT name, version, signup_allowed, signup_requires_approval, live_enabled, "
                "import_videos_enabled, user_video_quota, nsfw_policy, capabilities_json "
                "FROM peertube_instancias WHERE host = ?", (host,))
        except Exception:
            fila = None
        if not fila:
            return None
        try:
            cfg_json = json.loads(fila[8]) if fila[8] else {}
        except Exception:
            cfg_json = {}
        return {"host": host, "name": fila[0], "version": fila[1],
                "signup_allowed": bool(fila[2]), "signup_requires_approval": bool(fila[3]),
                "live_enabled": bool(fila[4]), "import_videos_enabled": bool(fila[5]),
                "user_video_quota": fila[6], "nsfw_policy": fila[7], "config_json": cfg_json}

    def instancia_soporta(self, host: str, capacidad: str) -> bool:
        """Consulta rápida de una capacidad cacheada ('live', 'import', 'signup'). Conservadora: si
        no hay caché, devuelve False (la función se ofrecerá solo cuando se confirme que existe)."""
        cap = self.capacidades_cacheadas(host)
        if not cap:
            return False
        return {"live": cap["live_enabled"], "import": cap["import_videos_enabled"],
                "signup": cap["signup_allowed"]}.get(capacidad, False)
