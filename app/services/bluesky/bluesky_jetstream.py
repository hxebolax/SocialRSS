# -*- coding: utf-8 -*-
"""Cliente Jetstream de Bluesky (auditoría 12.2) — sustituto ligero del firehose CAR.

Jetstream es el servicio recomendado por la documentación oficial para aplicaciones que no
necesitan verificar el repositorio completo: entrega los eventos en JSON y permite filtrar
POR COLECCIÓN y POR DIDs en el propio servidor (hasta 10.000), con lo que el "tiempo real"
del Inicio deja de descargar el relay entero y decodificar CAR de toda la red.

Mejoras sobre el firehose antiguo (bluesky_firehose.py, que queda de reserva):
- filtro server-side: wantedCollections=app.bsky.feed.post y wantedDids con tus seguidos;
- CURSOR temporal (time_us) persistido por cuenta: al reconectar se retoma con un
  rebobinado de 5 s, sin perder eventos del corte;
- reconexión con backoff exponencial + jitter y comprobación de vida (ping al no recibir);
- mención REAL por facets (feature #mention con tu DID), no por buscar el DID en str();
- respuesta REAL solo si el padre o la raíz pertenecen a tu repo;
- stop() espera al hilo.

El análisis de eventos es una función PURA (analizar_evento), directamente testeable.
"""
import json
import logging
import random
import threading
import time
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

HAS_JETSTREAM = False
try:
    from websockets.sync.client import connect as _ws_connect
    HAS_JETSTREAM = True
except Exception:  # pragma: no cover - entorno sin websockets
    log.warning("Jetstream de Bluesky no disponible (falta la librería websockets).")

# Instancias públicas oficiales de Jetstream; se rota si una falla.
ENDPOINTS = (
    "wss://jetstream2.us-east.bsky.network/subscribe",
    "wss://jetstream1.us-east.bsky.network/subscribe",
    "wss://jetstream2.us-west.bsky.network/subscribe",
    "wss://jetstream1.us-west.bsky.network/subscribe",
)

# Máximo de DIDs que admite el filtro del servidor (límite documentado de Jetstream).
MAX_WANTED_DIDS = 10000


def analizar_evento(evento: dict, mi_did: str = "", palabras: Optional[List[str]] = None,
                    dids_seguidos=None) -> Optional[dict]:
    """Analiza UN evento JSON de Jetstream (función pura). Devuelve None si no es un post
    nuevo; si lo es: {uri, did, text, time_us, de_seguido, is_mention, is_reply,
    hay_palabra}. La mención se decide por los FACETS del record (feature #mention con
    mi_did) y la respuesta solo cuenta si el padre o la raíz son de mi repo (auditoría
    12.1: antes se buscaba el DID en str(record), con falsos positivos)."""
    if not isinstance(evento, dict) or evento.get("kind") != "commit":
        return None
    commit = evento.get("commit") or {}
    if commit.get("operation") != "create" or commit.get("collection") != "app.bsky.feed.post":
        return None
    did = evento.get("did") or ""
    record = commit.get("record") or {}
    texto = record.get("text") or ""
    res = {
        "uri": "at://{0}/app.bsky.feed.post/{1}".format(did, commit.get("rkey", "")),
        "did": did,
        "text": texto,
        "time_us": int(evento.get("time_us") or 0),
        "de_seguido": bool(dids_seguidos) and did in dids_seguidos,
        # Publicación TUYA. Se distingue porque tu propio identificador entra en el filtro (para
        # que lo que publiques aparezca al instante), pero no debe tratarse como una mención o
        # una respuesta ajena: nadie quiere que le avisen de lo que acaba de escribir él mismo.
        "es_mio": bool(mi_did) and did == mi_did,
        "is_mention": False,
        "is_reply": False,
        "hay_palabra": False,
    }
    if mi_did:
        for facet in (record.get("facets") or []):
            for feat in (facet.get("features") or []):
                if isinstance(feat, dict) and str(feat.get("$type", "")).endswith("#mention") \
                        and feat.get("did") == mi_did:
                    res["is_mention"] = True
        reply = record.get("reply") or {}
        prefijo = "at://{0}/".format(mi_did)
        for ref in (reply.get("parent"), reply.get("root")):
            if isinstance(ref, dict) and str(ref.get("uri", "")).startswith(prefijo):
                res["is_reply"] = True
    if palabras:
        bajo = texto.lower()
        res["hay_palabra"] = any(p in bajo for p in palabras)
    return res


class BlueskyJetstream:
    """Suscripción a Jetstream con la misma interfaz que BlueskyFirehose (start/stop/activo,
    on_match, on_follow_activity), más cursor persistido por `clave_cursor`."""

    def __init__(self, mi_did: str = "", palabras: Optional[List[str]] = None,
                 on_match: Optional[Callable[[dict], None]] = None,
                 dids_seguidos: Optional[List[str]] = None,
                 on_follow_activity: Optional[Callable[[], None]] = None,
                 clave_cursor: str = ""):
        self.mi_did = mi_did
        self.palabras = [p.lower() for p in (palabras or [])]
        self.on_match = on_match
        self.dids_seguidos = set(dids_seguidos or [])
        self.on_follow_activity = on_follow_activity
        self.clave_cursor = clave_cursor
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ws = None
        self._cursor_us = self._leer_cursor()
        self._cursor_guardado = 0.0

    # --------------- cursor persistido ---------------
    def _leer_cursor(self) -> int:
        if not self.clave_cursor:
            return 0
        try:
            from app.core.config_manager import config
            return int((config.get("bluesky", "jetstream_cursor", {}) or {}).get(self.clave_cursor) or 0)
        except Exception:
            return 0

    def _guardar_cursor(self):
        if not self.clave_cursor or not self._cursor_us:
            return
        try:
            from app.core.config_manager import config
            cursores = dict(config.get("bluesky", "jetstream_cursor", {}) or {})
            cursores[self.clave_cursor] = int(self._cursor_us)
            config.set("bluesky", "jetstream_cursor", cursores)
            self._cursor_guardado = time.time()
        except Exception as e:
            log.debug(f"No se pudo guardar el cursor de Jetstream: {e}")

    # --------------- ciclo de vida ---------------
    @property
    def activo(self) -> bool:
        return self._running

    def start(self) -> bool:
        if not HAS_JETSTREAM:
            return False
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._run, name="BlueskyJetstream", daemon=True)
        self._thread.start()
        log.info("Jetstream de Bluesky iniciado (filtro: %s).",
                 f"{len(self.dids_seguidos)} DIDs" if self.dids_seguidos and not self.palabras
                 else "red completa (palabras/menciones)")
        return True

    def stop(self):
        """Detiene la suscripción y ESPERA al hilo (con tope), auditoría 12.1."""
        self._running = False
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        t = self._thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=5)
            except Exception:
                pass
        self._guardar_cursor()
        log.info("Jetstream de Bluesky detenido.")

    # --------------- conexión ---------------
    def _usar_filtro_dids(self) -> bool:
        """El filtro por DIDs seguidos solo vale para el 'tiempo real' del Inicio. Con
        palabras clave o vigilancia de menciones hay que escuchar la red completa (una
        mención puede venir de cualquiera; auditoría 12.2)."""
        return bool(self.dids_seguidos) and not self.palabras and self.on_match is None

    def _url(self, endpoint: str) -> str:
        from urllib.parse import urlencode
        params = [("wantedCollections", "app.bsky.feed.post")]
        if self._cursor_us:
            # Rebobinado de 5 s recomendado por la documentación al reanudar por cursor.
            params.append(("cursor", str(max(0, int(self._cursor_us) - 5_000_000))))
        if self._usar_filtro_dids():
            # El servidor espera las opciones por mensaje (options_update) tras conectar;
            # requireHello hace que no envíe nada hasta recibirlas (evita el aluvión inicial).
            params.append(("requireHello", "true"))
        return endpoint + "?" + urlencode(params)

    def _enviar_opciones(self, ws):
        if not self._usar_filtro_dids():
            return
        dids = list(self.dids_seguidos)[:MAX_WANTED_DIDS]
        ws.send(json.dumps({
            "type": "options_update",
            "payload": {
                "wantedCollections": ["app.bsky.feed.post"],
                "wantedDids": dids,
                "maxMessageSizeBytes": 0,
            },
        }))

    def _run(self):
        intento = 0
        while self._running:
            endpoint = ENDPOINTS[intento % len(ENDPOINTS)]
            try:
                with _ws_connect(self._url(endpoint), open_timeout=15, close_timeout=5,
                                 max_size=8 * 1024 * 1024) as ws:
                    self._ws = ws
                    self._enviar_opciones(ws)
                    intento = 0  # conexión buena: reiniciar el backoff
                    while self._running:
                        try:
                            crudo = ws.recv(timeout=30)
                        except TimeoutError:
                            # Silencio prolongado: comprobar que la conexión sigue VIVA
                            # (con filtro por DIDs puede no haber tráfico un rato).
                            if not ws.ping().wait(10):
                                raise ConnectionError("Jetstream sin respuesta al ping.")
                            continue
                        self._procesar_crudo(crudo)
                        if time.time() - self._cursor_guardado > 15:
                            self._guardar_cursor()
            except Exception as e:
                if not self._running:
                    break
                intento += 1
                espera = min(60.0, 1.5 * (2 ** min(intento, 5))) * random.uniform(0.5, 1.0)
                log.debug(f"Jetstream desconectado ({e}); reintento en {espera:.1f}s.")
                time.sleep(espera)
            finally:
                self._ws = None
        self._running = False

    def _procesar_crudo(self, crudo):
        try:
            evento = json.loads(crudo)
        except (ValueError, TypeError):
            return
        if isinstance(evento, dict) and evento.get("time_us"):
            self._cursor_us = max(self._cursor_us, int(evento["time_us"]))
        res = analizar_evento(evento, self.mi_did, self.palabras, self.dids_seguidos)
        if not res:
            return
        try:
            if res["de_seguido"] and self.on_follow_activity:
                self.on_follow_activity()
            if (res["is_mention"] or res["is_reply"] or res["hay_palabra"]) and self.on_match:
                self.on_match({"uri": res["uri"], "did": res["did"], "text": res["text"],
                               "is_mention": res["is_mention"], "is_reply": res["is_reply"]})
        except Exception as e:
            log.debug(f"Error en un callback de Jetstream: {e}")
