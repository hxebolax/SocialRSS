# -*- coding: utf-8 -*-
"""
Cliente de firehose (tiempo real) de Bluesky / AT Protocol — OPCIONAL.

El firehose es el flujo en vivo de TODA la red. Para un cliente personal accesible
NO se usa de forma permanente (el sondeo de notificaciones es suficiente y mucho más
ligero), pero se ofrece como capacidad avanzada: permite reaccionar al instante a
posts que mencionan o responden a la cuenta, o que contienen ciertas palabras clave.

Uso típico:
    fh = BlueskyFirehose(mi_did="did:plc:...", palabras=["python"], on_match=cb)
    fh.start()
    ...
    fh.stop()

`on_match(info: dict)` se invoca en un hilo de fondo con:
    {"uri", "did", "text", "is_mention", "is_reply"}

Se importa de forma segura: si atproto/firehose no está disponible, HAS_FIREHOSE=False.
"""

import logging
import threading
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

HAS_FIREHOSE = False
try:
    from atproto import (
        CAR,
        FirehoseSubscribeReposClient,
        models,
        parse_subscribe_repos_message,
    )
    HAS_FIREHOSE = True
except Exception:  # pragma: no cover - entorno sin firehose
    log.warning("El firehose de Bluesky no está disponible (atproto incompleto).")


class BlueskyFirehose:
    """Suscripción al firehose con filtrado por menciones/respuestas/palabras clave."""

    def __init__(self, mi_did: str = "", palabras: Optional[List[str]] = None,
                 on_match: Optional[Callable[[dict], None]] = None,
                 dids_seguidos: Optional[List[str]] = None,
                 on_follow_activity: Optional[Callable[[], None]] = None):
        self.mi_did = mi_did
        self.palabras = [p.lower() for p in (palabras or [])]
        self.on_match = on_match
        # Conjunto de DIDs que sigues: si uno publica, se dispara on_follow_activity (señal para
        # refrescar el Inicio en TIEMPO REAL). El contenido lo trae el sondeo incremental (formato
        # y orden correctos); el firehose solo actúa de aviso "hay algo nuevo de quien sigues".
        self.dids_seguidos = set(dids_seguidos or [])
        self.on_follow_activity = on_follow_activity
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def activo(self) -> bool:
        return self._running

    def start(self) -> bool:
        """Arranca la suscripción en un hilo de fondo. Devuelve False si no es posible."""
        if not HAS_FIREHOSE:
            return False
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._run, name="BlueskyFirehose", daemon=True)
        self._thread.start()
        log.info("Firehose de Bluesky iniciado.")
        return True

    def stop(self):
        """Detiene la suscripción."""
        self._running = False
        try:
            if self._client:
                self._client.stop()
        except Exception as e:
            log.debug(f"Error al detener el firehose: {e}")
        log.info("Firehose de Bluesky detenido.")

    # ------------------------------------------------------------------
    def _run(self):
        try:
            self._client = FirehoseSubscribeReposClient()

            def _on_message(message) -> None:
                if not self._running:
                    try:
                        self._client.stop()
                    except Exception:
                        pass
                    return
                try:
                    self._procesar(message)
                except Exception as e:
                    log.debug(f"Error procesando mensaje del firehose: {e}")

            self._client.start(_on_message)
        except Exception as e:
            log.error(f"Fallo en el firehose de Bluesky: {e}")
            self._running = False

    def _procesar(self, message):
        commit = parse_subscribe_repos_message(message)
        if not isinstance(commit, models.ComAtprotoSyncSubscribeRepos.Commit):
            return
        if not commit.blocks:
            return
        # TIEMPO REAL del Inicio: si el autor del commit es una cuenta que sigues y hay un post
        # nuevo, basta una señal para refrescar (no procesamos el contenido aquí).
        if self.dids_seguidos and commit.repo in self.dids_seguidos and self.on_follow_activity:
            if any(op.action == "create" and op.path.startswith("app.bsky.feed.post")
                   for op in commit.ops):
                self.on_follow_activity()

        car = CAR.from_bytes(commit.blocks)
        for op in commit.ops:
            if op.action != "create" or not op.path.startswith("app.bsky.feed.post"):
                continue
            record = car.blocks.get(op.cid)
            if not record:
                continue
            texto = (record.get("text") or "") if isinstance(record, dict) else ""
            reply = record.get("reply") if isinstance(record, dict) else None
            uri = f"at://{commit.repo}/{op.path}"

            texto_bajo = texto.lower()
            is_mention = bool(self.mi_did) and self.mi_did in str(record)
            is_reply = bool(reply)
            hay_palabra = any(p in texto_bajo for p in self.palabras) if self.palabras else False

            # Reglas de coincidencia: menciona mi DID, o contiene una palabra clave.
            if (is_mention or hay_palabra) and self.on_match:
                self.on_match({
                    "uri": uri,
                    "did": commit.repo,
                    "text": texto,
                    "is_mention": is_mention,
                    "is_reply": is_reply,
                })
