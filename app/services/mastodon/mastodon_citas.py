# -*- coding: utf-8 -*-
"""Revisión diferida de las citas PENDIENTES de Mastodon.

Mixin de MastodonService (parte de la refactorización modular).

Cuando alguien de OTRO servidor cita una publicación, su toot llega primero con la cita en estado
"pending" y el servidor del autor citado la aprueba unos segundos después (QuoteRequest -> Accept,
FEP-044f). Mastodon NO avisa de ese cambio: Quote#accept! solo limpia su caché, no emite nada por
el streaming ni crea una edición. Si la app guardó el toot en esa ventana, mostraba "Cita pendiente
de que el autor original la apruebe" para siempre, aunque la web oficial ya la enseñara aceptada.

Por eso cada toot que llega con la cita pendiente se vuelve a pedir pasado un rato (con esperas
crecientes) hasta que deja de estarlo; si cambia, se avisa a la interfaz con el evento
"mastodon_cita_actualizada" (account_id, db_id) para que rehaga la fila en silencio.
"""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from app.core.database import db
from app.core.task_manager import task_manager

log = logging.getLogger(__name__)

# Esperas (segundos) antes de cada nueva consulta de una cita que sigue pendiente. La aprobación
# automática entre servidores suele tardar segundos; las últimas cubren servidores lentos o colas.
RETRASOS_REVISION_SEG = (15, 60, 300, 1800)
# Al arrancar se revisan las citas pendientes que ya había guardadas (de sesiones anteriores).
MAX_REVISION_ARRANQUE = 40
DIAS_REVISION_ARRANQUE = 7

EVENTO_CITA_ACTUALIZADA = "mastodon_cita_actualizada"

# Reentrante: _citas_pendientes() lo toma y se llama también con el lock ya cogido.
_LOCK = threading.RLock()


def estado_cita_api(raw) -> str:
    """Estado de la cita de un Status de la API ('accepted', 'pending'...) o None si no cita."""
    q = raw.get("quote") if isinstance(raw, dict) else None
    if isinstance(q, dict) and q.get("state"):
        return str(q.get("state"))
    return None


def estado_cita_guardada(quote_json) -> str:
    """Estado de la cita guardada en la columna quote_json, o None si no cita. Las citas guardadas
    por versiones antiguas (sin 'state') eran siempre aceptadas."""
    if not quote_json:
        return None
    try:
        q = json.loads(quote_json)
    except Exception:
        return None
    if not isinstance(q, dict):
        return None
    return str(q.get("state") or "accepted")


class _CitasMixin:

    def _citas_pendientes(self) -> dict:
        # (account_id, toot_id) -> nº de intento. Se crea al primer uso (bajo lock).
        with _LOCK:
            pend = self.__dict__.get("_citas_pend")
            if pend is None:
                pend = self.__dict__["_citas_pend"] = {}
            return pend

    def _apuntar_cita_pendiente(self, account_id, toot_id, retraso=None):
        """Programa la revisión de un toot cuya cita está pendiente. Idempotente: si ya hay una
        revisión en curso para ese toot no se duplica. Puede llamarse desde cualquier hilo."""
        try:
            clave = (int(account_id), str(toot_id))
        except (TypeError, ValueError):
            return
        pend = self._citas_pendientes()
        with _LOCK:
            if clave in pend:
                return
            pend[clave] = 0
        self._programar_revision_cita(clave, 0, retraso)

    def _olvidar_cita(self, clave):
        with _LOCK:
            self._citas_pendientes().pop(clave, None)

    def _programar_revision_cita(self, clave, intento, retraso=None):
        espera = RETRASOS_REVISION_SEG[intento] if retraso is None else retraso
        t = threading.Timer(espera, self._lanzar_revision_cita, args=(clave, intento))
        t.daemon = True
        t.start()

    def _lanzar_revision_cita(self, clave, intento):
        # Hilo del temporizador: la petición va al pool (prioridad baja) y el final, a la UI.
        tid = task_manager.start_task(
            self._revisar_cita_bg, clave, pass_context=False,
            callback=lambda res, err, c=clave, i=intento: self._fin_revision_cita(c, i, res, err))
        if tid is None:   # la aplicación se está cerrando
            self._olvidar_cita(clave)

    def _revisar_cita_bg(self, clave):
        """(Hilo de fondo) Vuelve a pedir el toot y lo guarda. Devuelve un dict con el estado de la
        cita antes y después, o con 'error'. No lanza: un 404 aquí es normal (toot borrado)."""
        account_id, toot_id = clave
        db_id = f"{account_id}:{toot_id}"
        try:
            fila = db.query_one("SELECT quote_json FROM mastodon_statuses WHERE id = ?", (db_id,))
            antes = estado_cita_guardada(fila[0]) if fila else None
            client = self.obtener_cliente(account_id)
            raw = client.status(toot_id)          # red FUERA de la transacción de escritura
            with db.transaccion() as conn:
                self._procesar_y_guardar_toot(account_id, raw, conn=conn)
            orig = raw.get("reblog") if isinstance(raw.get("reblog"), dict) else raw
            return {"antes": antes, "ahora": estado_cita_api(orig), "db_id": db_id}
        except Exception as e:
            definitivo = type(e).__name__ in ("MastodonNotFoundError", "MastodonUnauthorizedError")
            log.debug(f"No se pudo revisar la cita pendiente del toot {db_id}: {e}")
            return {"error": str(e), "definitivo": definitivo, "db_id": db_id}

    def _fin_revision_cita(self, clave, intento, res, err):
        """(Hilo de la UI) Reprograma si sigue pendiente y avisa si la cita cambió."""
        res = res or {}
        sigue = bool(err) or ("error" in res and not res.get("definitivo")) \
            or res.get("ahora") == "pending"
        if sigue and intento + 1 < len(RETRASOS_REVISION_SEG):
            with _LOCK:
                self._citas_pendientes()[clave] = intento + 1
            self._programar_revision_cita(clave, intento + 1)
        else:
            self._olvidar_cita(clave)
        if not err and "error" not in res and res.get("ahora") != res.get("antes"):
            log.debug(f"Cita del toot {res.get('db_id')}: {res.get('antes')} -> {res.get('ahora')}")
            try:
                from app.core.event_bus import event_bus
                event_bus.emit(EVENTO_CITA_ACTUALIZADA, clave[0], res.get("db_id"))
            except Exception:
                pass

    def revisar_citas_pendientes_guardadas(self, account_id):
        """Programa la revisión de las citas que quedaron 'pendientes' en la caché (de sesiones
        anteriores o de versiones que no las revisaban). Solo las recientes, escalonadas."""
        corte = (datetime.now(timezone.utc) - timedelta(days=DIAS_REVISION_ARRANQUE)).strftime("%Y-%m-%d")
        try:
            filas = db.query(
                "SELECT status_id FROM mastodon_statuses WHERE account_id = ? "
                "AND quote_json LIKE '%\"state\": \"pending\"%' AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (account_id, corte, MAX_REVISION_ARRANQUE))
        except Exception as e:
            log.debug(f"No se pudieron leer las citas pendientes de la cuenta {account_id}: {e}")
            return 0
        for i, (status_id,) in enumerate(filas):
            if status_id:
                self._apuntar_cita_pendiente(account_id, status_id, retraso=5 + i * 1.5)
        return len(filas)
