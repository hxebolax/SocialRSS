# -*- coding: utf-8 -*-
"""Catálogos de vídeos, detalle y persistencia (Fase 2 de PeerTube).

Mixin de `PeerTubeService`. Descarga los buffers de vídeos (recientes, populares, tendencias,
locales, federados, suscripciones y los feeds de un canal/cuenta) y los cachea en la base de datos.
Toda la RED ocurre FUERA de la transacción de escritura (regla del proyecto): primero se descarga a
memoria y luego se persiste en un único bloque. La reproducción real de los vídeos llega en la Fase 3.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from app.core.database import db
from app.core.config_manager import config
from app.core.task_manager import task_manager
from app.core.errors import PeerTubeServiceError
from app.services.peertube.peertube_common import _ahora_iso
from app.services.peertube.peertube_modelos import Video, parsear

log = logging.getLogger(__name__)

# Órdenes admitidos por la API de vídeos (para el sufijo ":sort" de un buffer).
_SORTS = ("-publishedAt", "-views", "-likes", "-trending", "-hot", "-best", "-createdAt", "name")


class _VideosMixin:
    def cargar_seccion_async(self, account_id: int, seccion: str, *, cargar_mas: bool = False,
                             callback=None) -> None:
        """Descarga un buffer de vídeos y lo cachea (en segundo plano). `seccion`: recientes,
        populares, tendencias, locales, federados, suscripciones, o "channel:<handle>" /
        "account:<handle>" / "search:<texto>". `cargar_mas` pagina con el cursor guardado."""
        def _bg(ctx):
            return self._cargar_seccion_sync(account_id, seccion, cargar_mas)

        task_manager.start_task(
            _bg, callback=callback, pass_context=True,
            unique_key=f"peertube:{account_id}:{seccion}:{'mas' if cargar_mas else 'fresh'}")

    # ------------------------------------------------------------------
    # Descarga + persistencia
    # ------------------------------------------------------------------
    def _cargar_seccion_sync(self, account_id: int, seccion: str, cargar_mas: bool) -> int:
        """Descarga (RED) una página y la persiste. Devuelve cuántos vídeos nuevos se añadieron."""
        limite = int(config.get("peertube", "limit_per_load", 25))
        path, params, _auth = self._resolver_seccion(account_id, seccion)
        if not hasattr(self, "_after_pt"):
            self._after_pt: Dict[str, int] = {}
        clave_cursor = f"{account_id}:{seccion}"
        start = 0
        if cargar_mas:
            start = self._after_pt.get(clave_cursor, 0)
            if not start:
                return 0

        base_url = self.instancia_de(account_id)
        instance_host = urlsplit(base_url).netloc if base_url else ""

        def _fn(cliente):
            return cliente.pagina(path, params=params, start=start, count=limite, auth=True)

        data, _total = self._ejecutar(account_id, _fn)
        # Algunas secciones envuelven cada vídeo en la clave "video": los elementos de una lista de
        # reproducción (incluida "ver más tarde") y las valoraciones ("me gusta").
        envuelve_video = (seccion.startswith("playlist:")
                          or seccion in ("ver_mas_tarde", "me_gusta"))
        videos = []
        for item in (data or []):
            if envuelve_video and isinstance(item, dict):
                item = item.get("video") or {}
            if not item:
                continue
            try:
                v = parsear(Video, item)
            except PeerTubeServiceError:
                continue
            videos.append(self._video_a_valores(v, account_id, instance_host))

        # Avanzar el cursor (siguiente offset). Si la página vino vacía, no hay más.
        if videos:
            self._after_pt[clave_cursor] = start + len(data or [])
        elif not cargar_mas:
            self._after_pt.pop(clave_cursor, None)

        return self._persistir_videos(account_id, seccion, videos, reemplazar=not cargar_mas)

    def _resolver_seccion(self, account_id: int, seccion: str) -> Tuple[str, Dict[str, Any], bool]:
        """Traduce la clave de sección a (path, params, requiere_auth). El NSFW se ajusta según la
        preferencia del usuario. Los buffers de usuario (suscripciones, me gusta, ver más tarde)
        requieren sesión (siempre la hay: la cuenta está autenticada)."""
        nsfw = self._param_nsfw()
        base = {"nsfw": nsfw} if nsfw else {}
        partes = seccion.split(":", 1)
        clave = partes[0]

        if clave == "recientes":
            return "videos", {**base, "sort": "-publishedAt"}, False
        if clave == "populares":
            return "videos", {**base, "sort": "-views"}, False
        if clave == "tendencias":
            return "videos", {**base, "sort": "-trending"}, False
        if clave == "locales":
            return "videos", {**base, "sort": "-publishedAt", "isLocal": "true"}, False
        if clave == "federados":
            return "videos", {**base, "sort": "-publishedAt", "isLocal": "false"}, False
        if clave == "suscripciones":
            return "users/me/subscriptions/videos", {**base, "sort": "-publishedAt"}, True
        if clave == "historial":
            return "users/me/history/videos", {**base}, True
        if clave == "mis_videos":
            return "users/me/videos", {**base, "sort": "-publishedAt"}, True
        if clave == "me_gusta":
            # Valoraciones "me gusta" del usuario. Cada elemento envuelve el vídeo en "video".
            nombre = self.nombre_usuario(account_id)
            if not nombre:
                raise PeerTubeServiceError("No hay sesión para listar tus 'me gusta'.")
            return f"accounts/{nombre}/ratings", {**base, "rating": "like",
                                                  "sort": "-createdAt"}, True
        if clave == "ver_mas_tarde":
            # Lista especial "ver más tarde": se resuelve su id y se piden sus vídeos.
            pid = self.id_ver_mas_tarde(account_id)
            if not pid:
                raise PeerTubeServiceError("No se encontró tu lista 'ver más tarde'.")
            return f"video-playlists/{pid}/videos", {**base}, True
        if clave == "directos":
            return "videos", {**base, "sort": "-publishedAt", "isLive": "true",
                              "includeScheduledLive": "true"}, False
        if clave == "playlist":
            pid = partes[1] if len(partes) > 1 else ""
            return f"video-playlists/{pid}/videos", {**base}, True
        if clave == "channel":
            handle = partes[1] if len(partes) > 1 else ""
            return f"video-channels/{handle}/videos", {**base, "sort": "-publishedAt"}, False
        if clave == "account":
            handle = partes[1] if len(partes) > 1 else ""
            return f"accounts/{handle}/videos", {**base, "sort": "-publishedAt"}, False
        if clave == "search":
            texto = partes[1] if len(partes) > 1 else ""
            return "search/videos", {**base, "search": texto}, False
        raise PeerTubeServiceError(f"Sección de PeerTube no reconocida: {seccion}")

    def _param_nsfw(self) -> Optional[str]:
        """Valor del parámetro `nsfw` según la política configurada: mostrar → 'both', ocultar →
        'false'. None deja el valor por defecto de la instancia."""
        politica = (config.get("peertube", "nsfw_policy", "do_not_list") or "").lower()
        if politica in ("display", "blur"):
            return "both"
        if politica in ("do_not_list", "hide"):
            return "false"
        return None

    def _video_a_valores(self, v: Video, account_id: int, instance_host: str) -> Dict[str, Any]:
        """Aplana un modelo Video a los valores de la fila `peertube_videos`."""
        base_url = self.instancia_de(account_id) or ""
        canal = v.channel
        cuenta = v.account
        privacy = v.privacy.id if v.privacy else None
        category = v.category.id if v.category else None
        licence = v.licence.id if v.licence else None
        language = v.language.id if v.language else None
        thumb = self._url_absoluta(base_url, v.thumbnail_path)
        preview = self._url_absoluta(base_url, v.preview_path)
        embed = self._url_absoluta(base_url, v.embed_path)
        url = v.url or (f"{base_url}/w/{v.short_uuid}" if v.short_uuid else "")
        files = self._extraer_fuentes(v)
        return {
            "id": f"{account_id}:{v.uuid}",
            "account_id": account_id,
            "uuid": v.uuid or "",
            "short_uuid": v.short_uuid or "",
            "video_id": v.id,
            "name": v.name or "",
            "description": v.description or "",
            "channel_name": (canal.name if canal else ""),
            "channel_display": (canal.display_name if canal else ""),
            "account_name": (cuenta.name if cuenta else ""),
            "account_host": (cuenta.host if cuenta else ""),
            "instance_host": instance_host,
            "duration": int(v.duration or 0),
            "views": int(v.views or 0),
            "likes": int(v.likes or 0),
            "dislikes": int(v.dislikes or 0),
            "category": category if isinstance(category, int) else None,
            "licence": licence if isinstance(licence, int) else None,
            "language": str(language) if language is not None else None,
            "privacy": privacy if isinstance(privacy, int) else None,
            "nsfw": 1 if v.nsfw else 0,
            "is_live": 1 if v.is_live else 0,
            "state": (v.state.id if v.state and isinstance(v.state.id, int) else None),
            "published_at": v.published_at or "",
            "updated_at": v.updated_at or "",
            "thumbnail_url": thumb,
            "preview_url": preview,
            "url": url,
            "embed_url": embed,
            "files_json": json.dumps(files) if files else "",
            "is_local": 1 if v.is_local else 0,
            "fetched_at": _ahora_iso(),
        }

    @staticmethod
    def _url_absoluta(base_url: str, path: Optional[str]) -> str:
        """Convierte un path relativo de la API en URL absoluta contra la instancia."""
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{base_url}{path}" if base_url else path

    def _extraer_fuentes(self, v: Video) -> List[Dict[str, Any]]:
        """Resume las fuentes de reproducción (ficheros progresivos y playlists HLS) para la Fase 3.
        Guarda tipo, resolución y URL; NO se registran en logs (pueden llevar token de fichero)."""
        fuentes: List[Dict[str, Any]] = []
        for f in (v.files or []):
            fuentes.append({"tipo": "web", "resolucion": (f.resolution.id if f.resolution else None),
                            "url": f.file_url or f.file_download_url or ""})
        for sp in (v.streaming_playlists or []):
            if sp.playlist_url:
                fuentes.append({"tipo": "hls", "resolucion": None, "url": sp.playlist_url})
            for f in (sp.files or []):
                fuentes.append({"tipo": "hls-file",
                                "resolucion": (f.resolution.id if f.resolution else None),
                                "url": f.file_url or f.file_download_url or ""})
        return fuentes

    def _persistir_videos(self, account_id: int, seccion: str, videos: List[dict],
                          reemplazar: bool) -> int:
        """Guarda los vídeos (UPSERT que NO borra, para no disparar el CASCADE de la relación) y la
        relación buffer↔vídeo. Devuelve cuántas relaciones nuevas se crearon."""
        if not videos and not reemplazar:
            return 0
        columnas = ["id", "account_id", "uuid", "short_uuid", "video_id", "name", "description",
                    "channel_name", "channel_display", "account_name", "account_host",
                    "instance_host", "duration", "views", "likes", "dislikes", "category", "licence",
                    "language", "privacy", "nsfw", "is_live", "state", "published_at", "updated_at",
                    "thumbnail_url", "preview_url", "url", "embed_url", "files_json", "is_local",
                    "fetched_at"]
        set_clause = ", ".join(f"{c}=excluded.{c}" for c in columnas if c not in ("id", "account_id"))
        marcadores = ",".join(["?"] * len(columnas))
        # Ids ya presentes en el buffer (para contar cuántos son realmente nuevos sin depender del
        # rowcount del driver, que no es fiable en la capa de BD del proyecto).
        existentes = set()
        if not reemplazar:
            existentes = {r[0] for r in db.query(
                "SELECT video_db_id FROM peertube_video_buffers WHERE account_id = ? AND buffer = ?",
                (account_id, seccion))}
        nuevos = 0
        with db.transaccion():
            if reemplazar:
                db.execute("DELETE FROM peertube_video_buffers WHERE account_id = ? AND buffer = ?",
                           (account_id, seccion))
            pos_base = db.query_one(
                "SELECT COALESCE(MAX(position), -1) FROM peertube_video_buffers "
                "WHERE account_id = ? AND buffer = ?", (account_id, seccion))
            posicion = (pos_base[0] if pos_base else -1) + 1
            for v in videos:
                db.execute(
                    f"INSERT INTO peertube_videos ({', '.join(columnas)}) VALUES ({marcadores}) "
                    f"ON CONFLICT(id) DO UPDATE SET {set_clause}",
                    tuple(v[c] for c in columnas))
                db.execute(
                    "INSERT OR IGNORE INTO peertube_video_buffers (account_id, buffer, video_db_id, "
                    "position) VALUES (?,?,?,?)", (account_id, seccion, v["id"], posicion))
                if reemplazar or v["id"] not in existentes:
                    nuevos += 1
                posicion += 1
        return nuevos

    def vaciar_cache_sesion(self, account_id: int) -> None:
        """Vacía los buffers de vídeos de esa cuenta (se re-descargan al usar). Se llama al arrancar
        si el usuario activó 'no conservar entre sesiones'. No toca la sesión ni la configuración; los
        contenidos de PeerTube siempre se pueden volver a pedir a la instancia. Se borra en orden
        (relación → vídeos) para no depender de que las claves foráneas estén activas."""
        try:
            with db.transaccion():
                db.execute("DELETE FROM peertube_video_buffers WHERE account_id = ?", (account_id,))
                db.execute("DELETE FROM peertube_videos WHERE account_id = ?", (account_id,))
        except Exception as e:
            log.debug("No se pudo vaciar la caché de PeerTube (cuenta %s): %s", account_id, e)
        self._after_pt = getattr(self, "_after_pt", {})
        for k in [k for k in self._after_pt if k.startswith(f"{account_id}:")]:
            self._after_pt.pop(k, None)

    # ------------------------------------------------------------------
    # Detalle de un vídeo
    # ------------------------------------------------------------------
    def detalle_video(self, account_id: int, id_or_uuid: str) -> Dict[str, Any]:
        """Descarga el detalle completo de un vídeo (con ficheros/HLS). Devuelve un dict de valores
        (mismo formato que la caché) más el modelo por si se necesita. Bloqueante."""
        base_url = self.instancia_de(account_id)
        instance_host = urlsplit(base_url).netloc if base_url else ""

        def _fn(cliente):
            return cliente.get(f"videos/{id_or_uuid}", auth=True)

        datos = self._ejecutar(account_id, _fn)
        v = parsear(Video, datos)
        return self._video_a_valores(v, account_id, instance_host)
