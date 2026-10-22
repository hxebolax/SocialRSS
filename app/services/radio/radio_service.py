# -*- coding: utf-8 -*-
"""
Servicio de radio por internet.

Usa la API pública de RadioBrowser (radio-browser.info) para buscar emisoras por
nombre, país, idioma, etiqueta/género, populares y recientes. Guarda favoritos
(con categorías) e historial en la base de datos de la app y reproduce las emisoras
con el segundo reproductor (player2_service), que aplica la prioridad del audio de
las redes sociales.

Adaptado del complemento de NVDA zRadioBASS (obra del propio autor de la app).
"""

import logging
import random
import socket
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

from app.core.database import db
from app.services.media_player.player2_service import player2_service, KIND_RADIO

log = logging.getLogger(__name__)

try:
    import requests
    _HTTP = True
except Exception:
    _HTTP = False
    log.warning("La librería requests no está disponible; la radio no funcionará.")


class RadioBrowserAPI:
    """Cliente de la API de RadioBrowser con failover entre servidores."""

    # Mirrors conocidos (respaldo si el descubrimiento por DNS falla). Lo ideal es descubrirlos por
    # DNS (all.api.radio-browser.info); esta lista solo se usa si el DNS falla. Se mantienen los
    # servidores ACTIVOS (fi1 fue retirado por radio-browser, por eso ya no está).
    FALLBACK_SERVERS = [
        "de1.api.radio-browser.info",
        "de2.api.radio-browser.info",
        "nl1.api.radio-browser.info",
        "at1.api.radio-browser.info",
    ]
    TIMEOUT = 12

    def __init__(self):
        self._servers: List[str] = []   # lista barajada de mirrors (base URLs https)
        self._idx = 0                   # servidor que se está usando ahora
        self._lock = threading.Lock()
        self._session = None

    def _ensure(self):
        if self._servers:
            return
        with self._lock:
            if self._servers:
                return
            # Mirrors descubiertos por DNS (están VIVOS) PRIMERO, barajados entre sí para repartir
            # carga; los de respaldo solo DESPUÉS, por si el descubrimiento no dio ninguno. Así,
            # cuando el DNS funciona, no se pierde tiempo agotando timeouts contra servidores de la
            # lista de respaldo que pudieran estar retirados.
            descubiertos = self._discover_hosts()
            random.shuffle(descubiertos)
            extra = [h for h in self.FALLBACK_SERVERS if h not in descubiertos]
            hosts = descubiertos + extra
            self._servers = [f"https://{h}" for h in hosts]
            self._idx = 0
            if _HTTP and self._session is None:
                self._session = requests.Session()
                self._session.headers.update({"User-Agent": "SocialRSS/1.0 (radio)"})
            log.info(f"Radio: {len(self._servers)} servidores disponibles.")

    def _discover_hosts(self) -> List[str]:
        """Descubre los mirrors vivos resolviendo all.api.radio-browser.info por DNS."""
        hosts = []
        try:
            ips = socket.getaddrinfo("all.api.radio-browser.info", 443,
                                     socket.AF_INET, socket.SOCK_STREAM)
            vistos = set()
            for ip in ips:
                addr = ip[4][0]
                if addr in vistos:
                    continue
                vistos.add(addr)
                try:
                    host = socket.gethostbyaddr(addr)[0]
                    if host not in hosts:
                        hosts.append(host)
                except (socket.herror, socket.gaierror, OSError):
                    pass
        except Exception as e:
            log.warning(f"Radio: error descubriendo servidores por DNS: {e}")
        return hosts

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Petición con failover SECUENCIAL: prueba cada mirror distinto una vez.

        Antes se reintentaba siempre contra el mismo servidor (de ahí los 503 en
        bucle); ahora, si uno falla, se pasa al SIGUIENTE mirror de la lista."""
        if not _HTTP:
            raise RuntimeError("La librería requests no está disponible.")
        self._ensure()
        if not self._servers:
            raise RuntimeError("No hay servidores de radio disponibles.")
        ultimo_error = None
        total = len(self._servers)
        for intento in range(total):
            base = self._servers[self._idx]
            url = urljoin(base + "/json/", endpoint)
            try:
                resp = self._session.get(url, params=params, timeout=self.TIMEOUT)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                ultimo_error = e
                # Pasar al siguiente mirror para la próxima vuelta y este reintento.
                self._idx = (self._idx + 1) % total
                log.warning(f"Radio: {base} falló ({e}); probando otro mirror...")
        # Todos fallaron: OLVIDAR la lista de servidores para volver a DESCUBRIRLOS la próxima vez
        # (los mirrors de radio-browser cambian, y un fallo puntual de DNS no debe quedar fijado).
        with self._lock:
            self._servers = []
            self._idx = 0
        log.error(f"Radio: todos los servidores fallaron en {endpoint}: {ultimo_error}")
        # Mensaje claro: lo más habitual es que no haya conexión / DNS (getaddrinfo failed).
        txt = str(ultimo_error or "")
        if any(k in txt.lower() for k in ("getaddrinfo", "name", "resolve", "max retries",
                                          "connection", "timed out", "timeout")):
            raise RuntimeError(
                "No se pudo conectar con el servicio de emisoras de radio (radio-browser.info). "
                "Comprueba tu conexión a internet y vuelve a intentarlo en unos segundos.")
        raise ultimo_error if ultimo_error else RuntimeError("Sin servidores de radio.")

    @staticmethod
    def _estacion(item: Dict[str, Any]) -> Dict[str, Any]:
        """Normaliza una estación de la API a un diccionario sencillo."""
        return {
            "name": (item.get("name") or "").strip(),
            "url": (item.get("url_resolved") or item.get("url") or "").strip(),
            "uuid": item.get("stationuuid", ""),
            "country": item.get("country", ""),
            "countrycode": item.get("countrycode", ""),
            "language": item.get("language", ""),
            "tags": item.get("tags", ""),
            "codec": item.get("codec", ""),
            "bitrate": int(item.get("bitrate", 0) or 0),
            "votes": int(item.get("votes", 0) or 0),
        }

    # Tope alto que la API trata como "todas" (sin él, el servidor limita a 1000).
    SIN_LIMITE = 100000

    def buscar(self, name: str = "", country: str = "", language: str = "",
               tag: str = "", limit: int = 0) -> List[Dict[str, Any]]:
        # limit=0 => sin límite real (se pide un tope muy alto; si no, la API
        # devuelve solo 1000 por defecto).
        params = {"order": "votes", "reverse": "true", "hidebroken": "true",
                  "limit": limit if (limit and limit > 0) else self.SIN_LIMITE}
        if name:
            params["name"] = name
        if country:
            params["countrycode"] = country
        if language:
            params["language"] = language
        if tag:
            params["tag"] = tag
        return [self._estacion(x) for x in (self._get("stations/search", params) or [])]

    def top(self, limit: int = 100) -> List[Dict[str, Any]]:
        return [self._estacion(x) for x in (self._get("stations/topvote", {"limit": limit}) or [])]

    def recientes(self, limit: int = 100) -> List[Dict[str, Any]]:
        return [self._estacion(x) for x in (self._get("stations/lastchange", {"limit": limit}) or [])]

    def paises(self) -> List[Dict[str, Any]]:
        data = self._get("countries") or []
        return sorted(
            [{"name": d.get("name", ""), "code": d.get("iso_3166_1", ""),
              "count": d.get("stationcount", 0)} for d in data if d.get("name")],
            key=lambda c: c["name"])

    def idiomas(self) -> List[Dict[str, Any]]:
        data = self._get("languages") or []
        return sorted(
            [{"name": d.get("name", ""), "count": d.get("stationcount", 0)}
             for d in data if d.get("name")],
            key=lambda l: -l["count"])

    def etiquetas(self, limit: int = 300) -> List[Dict[str, Any]]:
        data = self._get("tags", {"order": "stationcount", "reverse": "true", "limit": limit}) or []
        return [{"name": d.get("name", ""), "count": d.get("stationcount", 0)}
                for d in data if d.get("name")]

    def registrar_click(self, uuid: str):
        if not uuid:
            return
        try:
            self._get(f"url/{uuid}")
        except Exception:
            pass

    def votar(self, uuid: str) -> bool:
        if not uuid:
            return False
        try:
            return bool((self._get(f"vote/{uuid}") or {}).get("ok", False))
        except Exception:
            return False


class RadioService:
    """Fachada: API de RadioBrowser + favoritos/historial en BD + reproducción."""

    def __init__(self):
        self.api = RadioBrowserAPI()

    # --- Reproducción ---
    def reproducir(self, estacion: Dict[str, Any]):
        """Reproduce una emisora en el segundo reproductor y la registra."""
        url = estacion.get("url") or ""
        nombre = estacion.get("name") or url
        if not url:
            return
        player2_service.cargar_y_reproducir(url, nombre, KIND_RADIO, resume=False)
        self._guardar_historial(estacion)
        # Registrar el click en RadioBrowser (en segundo plano, sin bloquear).
        uuid = estacion.get("uuid")
        if uuid:
            threading.Thread(target=self.api.registrar_click, args=(uuid,), daemon=True).start()

    # --- Historial ---
    def _guardar_historial(self, estacion: Dict[str, Any]):
        try:
            db.execute(
                "INSERT INTO radio_history (name, url, station_uuid, played_at) VALUES (?,?,?,?)",
                (estacion.get("name", ""), estacion.get("url", ""),
                 estacion.get("uuid", ""), datetime.now().isoformat()))
            # Conservar solo los 200 más recientes.
            db.execute("""
                DELETE FROM radio_history WHERE id NOT IN (
                    SELECT id FROM radio_history ORDER BY played_at DESC LIMIT 200)""")
        except Exception as e:
            log.debug(f"Radio: no se pudo guardar historial: {e}")

    def obtener_historial(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = db.query(
            "SELECT name, url, station_uuid, played_at FROM radio_history "
            "ORDER BY played_at DESC LIMIT ?", (limit,))
        return [{"name": r[0], "url": r[1], "uuid": r[2], "played_at": r[3]} for r in rows]

    def borrar_historial(self):
        db.execute("DELETE FROM radio_history")

    # --- Categorías de favoritos ---
    def obtener_categorias(self) -> List[Dict[str, Any]]:
        rows = db.query("SELECT id, name FROM radio_categories ORDER BY position, name")
        return [{"id": r[0], "name": r[1]} for r in rows]

    def crear_categoria(self, nombre: str) -> int:
        return db.execute_insert(
            "INSERT INTO radio_categories (name, position, created_at) VALUES (?,?,?)",
            (nombre, 0, datetime.now().isoformat()))

    def borrar_categoria(self, categoria_id: int):
        db.execute("UPDATE radio_favorites SET category_id = NULL WHERE category_id = ?", (categoria_id,))
        db.execute("DELETE FROM radio_categories WHERE id = ?", (categoria_id,))

    # --- Favoritos ---
    def obtener_favoritos(self, categoria_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if categoria_id is None:
            rows = db.query(
                "SELECT id, name, url, station_uuid, category_id FROM radio_favorites "
                "ORDER BY position, name")
        else:
            rows = db.query(
                "SELECT id, name, url, station_uuid, category_id FROM radio_favorites "
                "WHERE category_id = ? ORDER BY position, name", (categoria_id,))
        return [{"id": r[0], "name": r[1], "url": r[2], "uuid": r[3], "category_id": r[4]} for r in rows]

    def es_favorito(self, url: str) -> bool:
        return db.query_one("SELECT 1 FROM radio_favorites WHERE url = ?", (url,)) is not None

    def anadir_favorito(self, estacion: Dict[str, Any], categoria_id: Optional[int] = None) -> int:
        return db.execute_insert(
            "INSERT INTO radio_favorites (name, url, station_uuid, category_id, position, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (estacion.get("name", ""), estacion.get("url", ""), estacion.get("uuid", ""),
             categoria_id, 0, datetime.now().isoformat()))

    def editar_favorito(self, favorito_id: int, name: Optional[str] = None,
                        url: Optional[str] = None, category_id: Optional[int] = -1):
        """Edita un favorito. category_id=-1 significa 'no cambiar'."""
        if name is not None:
            db.execute("UPDATE radio_favorites SET name = ? WHERE id = ?", (name, favorito_id))
        if url is not None:
            db.execute("UPDATE radio_favorites SET url = ? WHERE id = ?", (url, favorito_id))
        if category_id != -1:
            db.execute("UPDATE radio_favorites SET category_id = ? WHERE id = ?", (category_id, favorito_id))

    def quitar_favorito(self, favorito_id: int):
        db.execute("DELETE FROM radio_favorites WHERE id = ?", (favorito_id,))

    def quitar_favorito_por_url(self, url: str):
        db.execute("DELETE FROM radio_favorites WHERE url = ?", (url,))


# Instancia global única.
radio_service = RadioService()
