# -*- coding: utf-8 -*-
"""Cola de transferencias de Telegram Drive (mixin del servicio).

Gestiona las subidas y descargas en SEGUNDO PLANO, sin bloquear la interfaz: una tabla
persistente (tgdrive_transfers) y un único trabajador daemon que las procesa una a una, con
cancelación cooperativa, reintentos con espera creciente y restauración de las que quedaron
a medias tras reiniciar. El progreso se persiste con moderación (no en cada byte) y se avisa
a la interfaz mediante el callback registrado (que la UI envuelve en wx.CallAfter).
"""
import os
import threading
import time as _t
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.core.database import db
from app.core.config_manager import config
from .tgdrive_models import (TR_PENDIENTE, TR_SUBIENDO, TR_DESCARGANDO, TR_CANCELANDO,
                             TR_CANCELADA, TR_COMPLETADA, TR_FALLIDA, TR_PREPARANDO,
                             ESTADOS_ACTIVOS)

import builtins as _bi
if not hasattr(_bi, "_"):
    _bi._ = lambda s: s

import logging
log = logging.getLogger(__name__)

# Cada cuánto se persiste el progreso a la base (evita martillearla en cada bloque).
_PERSISTIR_CADA_SEG = 1.0


def _ahora():
    return datetime.now().isoformat(timespec="seconds")


class _ColaTransferenciasMixin:
    """Cola de transferencias. Requiere que la clase tenga subir_archivo/descargar_item."""

    def _cola_init(self):
        if not hasattr(self, "_cola_lock"):
            self._cola_lock = threading.Lock()
            self._cola_workers = []   # trabajadores vivos (hasta N según Preferencias)

    # ---------------------------------------------------------------- encolar
    def encolar_subida(self, account_id: int, parent_id: Optional[str], ruta_local: str,
                       nombre: Optional[str] = None, descripcion: str = "") -> str:
        """Añade una subida a la cola y arranca el trabajador. Devuelve el id de transferencia."""
        import os
        self._cola_init()
        tid = uuid.uuid4().hex
        size = os.path.getsize(ruta_local) if os.path.isfile(ruta_local) else 0
        db.execute(
            "INSERT INTO tgdrive_transfers (id, account_id, parent_id, operacion, nombre, "
            "local_path, size_total, estado, descripcion, created_at, updated_at) "
            "VALUES (?, ?, ?, 'upload', ?, ?, ?, 'pendiente', ?, ?, ?)",
            (tid, account_id, parent_id, nombre or os.path.basename(ruta_local), ruta_local,
             size, descripcion or "", _ahora(), _ahora()))
        self._arrancar_worker_cola()
        self._notificar_ui()
        return tid

    def encolar_descarga(self, account_id: int, item_id: str,
                         carpeta_destino: Optional[str] = None) -> str:
        """Añade una descarga a la cola. Devuelve el id de transferencia."""
        self._cola_init()
        tid = uuid.uuid4().hex
        it = self.repo.obtener_item(item_id)
        db.execute(
            "INSERT INTO tgdrive_transfers (id, account_id, item_id, carpeta_destino, "
            "operacion, nombre, size_total, estado, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'download', ?, ?, 'pendiente', ?, ?)",
            (tid, account_id, item_id, carpeta_destino, it.name if it else "",
             it.size if it else 0, _ahora(), _ahora()))
        self._arrancar_worker_cola()
        self._notificar_ui()
        return tid

    # ---------------------------------------------------------------- control
    def cancelar_transferencia(self, tid: str):
        """Marca una transferencia para cancelación cooperativa (el trabajador la detiene en
        el siguiente bloque y limpia lo temporal)."""
        self._cola_init()
        with self._cola_lock:
            if not hasattr(self, "_cancelados"):
                self._cancelados = set()
            self._cancelados.add(tid)
        db.execute("UPDATE tgdrive_transfers SET estado = 'cancelando', updated_at = ? "
                   "WHERE id = ? AND estado IN ('pendiente','preparando','subiendo','descargando')",
                   (_ahora(), tid))
        self._notificar_ui()

    def reintentar_transferencia(self, tid: str):
        """Vuelve a poner en cola una transferencia fallida o cancelada."""
        self._cola_init()
        with self._cola_lock:
            if hasattr(self, "_cancelados"):
                self._cancelados.discard(tid)
        db.execute("UPDATE tgdrive_transfers SET estado = 'pendiente', error = NULL, "
                   "bytes_hechos = 0, updated_at = ? WHERE id = ? AND estado IN ('fallida','cancelada')",
                   (_ahora(), tid))
        self._arrancar_worker_cola()
        self._notificar_ui()

    def quitar_transferencia(self, tid: str):
        """Elimina una transferencia terminada de la lista."""
        db.execute("DELETE FROM tgdrive_transfers WHERE id = ? "
                   "AND estado IN ('completada','cancelada','fallida')", (tid,))
        self._notificar_ui()

    # Estados TERMINADOS (se pueden quitar de la lista sin cortar nada en curso).
    _ESTADOS_TERMINADOS = ("completada", "cancelada", "fallida")

    def limpiar_transferencias(self, account_id: Optional[int], estados=None) -> int:
        """Quita de la lista, EN LOTE, las transferencias TERMINADAS de los estados indicados
        (por defecto: completadas, canceladas y fallidas). Nunca toca las que están en curso.
        Devuelve cuántas se quitaron."""
        estados = tuple(estados) if estados else self._ESTADOS_TERMINADOS
        # Seguridad: solo permitir estados terminados (jamás borrar algo en curso).
        estados = tuple(e for e in estados if e in self._ESTADOS_TERMINADOS)
        if not estados:
            return 0
        marcadores = ",".join("?" * len(estados))
        if account_id is not None:
            filas = db.query("SELECT COUNT(*) FROM tgdrive_transfers WHERE account_id = ? "
                             "AND estado IN (%s)" % marcadores, (account_id, *estados))
            db.execute("DELETE FROM tgdrive_transfers WHERE account_id = ? AND estado IN (%s)"
                       % marcadores, (account_id, *estados))
        else:
            filas = db.query("SELECT COUNT(*) FROM tgdrive_transfers WHERE estado IN (%s)"
                             % marcadores, tuple(estados))
            db.execute("DELETE FROM tgdrive_transfers WHERE estado IN (%s)" % marcadores,
                       tuple(estados))
        n = (filas[0][0] if filas else 0) or 0
        self._notificar_ui()
        return n

    def obtener_transferencias(self, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Lista las transferencias (activas primero). Para el diálogo de transferencias. De paso,
        AUTOCURA la cola: si hay pendientes sin trabajador vivo, lo relanza."""
        self._asegurar_worker_si_pendientes()
        if account_id is not None:
            filas = db.query(
                "SELECT id, operacion, nombre, size_total, bytes_hechos, estado, "
                "COALESCE(error,''), local_path, carpeta_destino FROM tgdrive_transfers "
                "WHERE account_id = ? ORDER BY updated_at DESC", (account_id,))
        else:
            filas = db.query(
                "SELECT id, operacion, nombre, size_total, bytes_hechos, estado, "
                "COALESCE(error,''), local_path, carpeta_destino FROM tgdrive_transfers "
                "ORDER BY updated_at DESC")
        salida = []
        for r in filas:
            total = r[3] or 0
            pct = int(min(100, (r[4] or 0) * 100 // total)) if total else 0
            salida.append({
                "id": r[0], "operacion": r[1], "nombre": r[2], "size_total": total,
                "bytes_hechos": r[4] or 0, "porcentaje": pct, "estado": r[5],
                "error": r[6], "local_path": r[7], "carpeta_destino": r[8],
            })
        return salida

    def _worker_vivo(self) -> bool:
        return any(w.is_alive() for w in getattr(self, "_cola_workers", []))

    def reanudar_pendientes(self) -> Dict[str, int]:
        """Reanuda las transferencias pendientes. Al ARRANCAR (sin trabajador vivo), las que
        quedaron 'a medias' en la sesión anterior vuelven a pendiente; las SUBIDAS cuyo archivo
        de origen ya no existe se marcan fallidas (no se pueden reanudar). Devuelve el recuento.
        IMPORTANTE: si el trabajador YA está vivo (p. ej. al reabrir la ventana mientras algo
        sube), NO se toca lo que está en curso (antes se reseteaba y parecía que se paraba todo)."""
        self._cola_init()
        if not self._worker_vivo():
            db.execute("UPDATE tgdrive_transfers SET estado = 'pendiente' "
                       "WHERE estado IN ('preparando','subiendo','descargando','cancelando')")
        # Subidas pendientes cuyo ORIGEN ya no existe: no se pueden reanudar -> fallidas.
        huerfanas = 0
        for r in db.query("SELECT id, local_path FROM tgdrive_transfers "
                          "WHERE estado = 'pendiente' AND operacion = 'upload'"):
            lp = r[1]
            if not lp or not os.path.isfile(lp):
                db.execute("UPDATE tgdrive_transfers SET estado = 'fallida', error = ?, "
                           "updated_at = ? WHERE id = ?",
                           (_("El archivo de origen ya no existe; no se puede reanudar."),
                            _ahora(), r[0]))
                huerfanas += 1
        pend = db.query_one("SELECT COUNT(*) FROM tgdrive_transfers WHERE estado = 'pendiente'")
        n = (pend[0] if pend else 0) or 0
        if n:
            self._arrancar_worker_cola()
        if n or huerfanas:
            log.info("Drive cola: reanudadas %d pendientes; %d descartadas por origen inexistente.",
                     n, huerfanas)
        return {"pendientes": n, "huerfanas": huerfanas}

    # ---------------------------------------------------------------- trabajador
    def _arrancar_worker_cola(self):
        """Arranca tantos trabajadores CONCURRENTES como diga Preferencias (1..10), sin pasarse.
        Cada trabajador reclama una transferencia distinta de forma atómica y la procesa; las
        subidas/descargas van en paralelo de verdad (el cliente de Telegram lo permite hasta su
        techo, ver max_concurrent_transmissions)."""
        self._cola_init()
        with self._cola_lock:
            self._cola_workers = [w for w in self._cola_workers if w.is_alive()]
            objetivo = self._limite_simultaneas()
            while len(self._cola_workers) < objetivo:
                t = threading.Thread(target=self._bucle_cola,
                                     name="tgdrive-cola-%d" % (len(self._cola_workers) + 1),
                                     daemon=True)
                self._cola_workers.append(t)
                t.start()
            log.info("Drive cola: %d trabajador(es) activos (límite %d).",
                     len(self._cola_workers), objetivo)

    def _asegurar_worker_si_pendientes(self):
        """AUTOCURACIÓN: si hay transferencias pendientes pero el trabajador no está vivo (murió
        por un fallo inesperado), lo relanza. Se llama al consultar las transferencias (la UI las
        sondea cada segundo), así una cola atascada se recupera sola en ~1 s."""
        try:
            self._cola_init()
            if self._worker_vivo():
                return
            pend = db.query_one("SELECT COUNT(*) FROM tgdrive_transfers WHERE estado = 'pendiente'")
            if pend and pend[0]:
                log.info("Drive cola: había %d pendientes sin trabajador vivo; se relanza.", pend[0])
                self._arrancar_worker_cola()
        except Exception:
            pass

    def _limite_simultaneas(self) -> int:
        """Nº de transferencias simultáneas según Preferencias, acotado a [1, 10] (10 = techo del
        cliente de Telegram, ver max_concurrent_transmissions)."""
        try:
            return max(1, min(10, int(config.get("telegram_drive", "max_simultaneas", 1))))
        except Exception:
            return 1

    def _reclamar_siguiente(self):
        """Toma de forma ATÓMICA la siguiente transferencia pendiente y la marca activa, para que
        dos trabajadores no cojan la misma. Devuelve la fila o None. El candado serializa el
        SELECT+UPDATE entre los hilos de esta misma app."""
        with self._cola_lock:
            fila = db.query_one(
                "SELECT id, account_id, item_id, parent_id, carpeta_destino, operacion, "
                "nombre, local_path, COALESCE(descripcion,'') FROM tgdrive_transfers "
                "WHERE estado = 'pendiente' ORDER BY prioridad DESC, created_at LIMIT 1")
            if not fila:
                return None
            estado_activo = TR_SUBIENDO if fila[5] == "upload" else TR_DESCARGANDO
            db.execute("UPDATE tgdrive_transfers SET estado = ?, updated_at = ? "
                       "WHERE id = ? AND estado = 'pendiente'", (estado_activo, _ahora(), fila[0]))
        self._notificar_ui()
        return fila

    def _bucle_cola(self):
        """Procesa las transferencias pendientes de una en una (el límite de simultáneas se
        respeta con un solo trabajador secuencial por defecto, seguro para no saturar la
        conexión de Telegram). Prioridad baja."""
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)
        except Exception:
            pass
        try:
            while True:
                fila = self._reclamar_siguiente()
                if fila is None:
                    # Cola vacía: exportar UNA sola vez el índice de lo subido (en vez de tras
                    # cada archivo) y terminar. El hilo se recrea al encolar.
                    self._exportar_indices_pendientes()
                    log.info("Drive cola: sin pendientes; el trabajador termina.")
                    return
                # Un fallo INESPERADO en una transferencia NO debe matar al trabajador (si no, se
                # quedaría todo lo demás en 'pendiente' sin subir). Se marca fallida y se sigue.
                try:
                    self._procesar_transferencia(fila)
                except Exception as e:
                    log.exception("Drive cola: error inesperado en la transferencia %s; se continúa.",
                                  fila[0])
                    try:
                        self._marcar_transferencia(fila[0], TR_FALLIDA, error=str(e)[:400])
                    except Exception:
                        pass
                _t.sleep(0.2)
        except Exception:
            log.exception("Drive cola: el trabajador murió de forma inesperada.")
        finally:
            db.cerrar_lectura_hilo()

    def _procesar_transferencia(self, fila):
        tid, account_id, item_id, parent_id, carpeta_destino, op, nombre, local_path, descripcion = fila

        def _cancelado():
            with self._cola_lock:
                return tid in getattr(self, "_cancelados", set())

        ultimo = [0.0]

        def _prog(hecho, total):
            ahora = _t.time()
            if ahora - ultimo[0] >= _PERSISTIR_CADA_SEG:
                ultimo[0] = ahora
                try:
                    db.execute("UPDATE tgdrive_transfers SET bytes_hechos = ?, size_total = ?, "
                               "updated_at = ? WHERE id = ?",
                               (hecho, total, _ahora(), tid))
                except Exception:
                    pass
                self._notificar_ui()

        estado_activo = TR_SUBIENDO if op == "upload" else TR_DESCARGANDO
        db.execute("UPDATE tgdrive_transfers SET estado = ?, updated_at = ? WHERE id = ?",
                   (estado_activo, _ahora(), tid))
        self._notificar_ui()
        log.info("Drive cola: %s %s.", "subiendo" if op == "upload" else "descargando", nombre)
        try:
            if _cancelado():
                self._marcar_transferencia(tid, TR_CANCELADA)
                return
            if op == "upload":
                # Enlazar el item a la transferencia EN CUANTO se conoce, para poder REANUDAR
                # esta misma subida (sus partes ya subidas) si se cierra la app a media subida.
                def _on_item(iid, _tid=tid):
                    try:
                        db.execute("UPDATE tgdrive_transfers SET item_id = ? WHERE id = ?", (iid, _tid))
                    except Exception:
                        pass
                item = self.subir_archivo(account_id, parent_id, local_path, nombre=nombre,
                                          on_progress=_prog, cancelado=_cancelado,
                                          descripcion=descripcion or "",
                                          reanudar_item_id=item_id, on_item=_on_item)
                if item is None:
                    self._marcar_transferencia(tid, TR_CANCELADA)
                    return
                db.execute("UPDATE tgdrive_transfers SET item_id = ? WHERE id = ?",
                           (item.id, tid))
            else:
                ruta = self.descargar_item(account_id, item_id, carpeta_destino,
                                           on_progress=_prog, cancelado=_cancelado)
                if ruta is None:
                    self._marcar_transferencia(tid, TR_CANCELADA)
                    return
                db.execute("UPDATE tgdrive_transfers SET local_path = ? WHERE id = ?",
                           (ruta, tid))
            self._marcar_transferencia(tid, TR_COMPLETADA, bytes_full=True)
            log.info("Drive cola: completado %s (%s).", nombre, op)
            # Tras completar una subida, actualizar el índice remoto (best-effort).
            if op == "upload":
                self._exportar_indice_seguro(account_id)
        except Exception as e:
            log.warning("Transferencia %s (%s) falló: %s", tid, op, e)
            self._marcar_transferencia(tid, TR_FALLIDA, error=str(e)[:400])
        finally:
            with self._cola_lock:
                getattr(self, "_cancelados", set()).discard(tid)

    def _marcar_transferencia(self, tid, estado, error=None, bytes_full=False):
        if bytes_full:
            db.execute("UPDATE tgdrive_transfers SET estado = ?, bytes_hechos = size_total, "
                       "error = ?, updated_at = ? WHERE id = ?",
                       (estado, error, _ahora(), tid))
        else:
            db.execute("UPDATE tgdrive_transfers SET estado = ?, error = ?, updated_at = ? "
                       "WHERE id = ?", (estado, error, _ahora(), tid))
        self._notificar_ui()

    # No exportar el índice tras CADA archivo (sería un manifiesto por subida): se modera a como
    # mucho uno cada _EXPORT_MIN_SEG y se fuerza uno final al vaciarse la cola.
    _EXPORT_MIN_SEG = 20.0

    def _exportar_indice_seguro(self, account_id):
        """Marca el índice como 'sucio' y lo exporta SOLO si han pasado ya _EXPORT_MIN_SEG desde
        la última vez; si no, se deja para el volcado final (al vaciarse la cola)."""
        self._cola_init()
        with self._cola_lock:
            if not hasattr(self, "_indice_sucio"):
                self._indice_sucio = {}
            if not hasattr(self, "_ult_export"):
                self._ult_export = {}
            self._indice_sucio[account_id] = True
            ahora = _t.time()
            if ahora - self._ult_export.get(account_id, 0.0) < self._EXPORT_MIN_SEG:
                return   # demasiado pronto: se exportará luego
            self._ult_export[account_id] = ahora
            self._indice_sucio[account_id] = False
        try:
            self.exportar_indice(account_id)
        except Exception as e:
            log.debug("No se pudo actualizar el índice remoto tras subir: %s", e)

    def _exportar_indices_pendientes(self):
        """Fuerza la exportación de los índices marcados como sucios (al vaciarse la cola), para
        que el manifiesto final refleje TODO lo subido en la tanda con una sola exportación."""
        with self._cola_lock:
            cuentas = [a for a, v in getattr(self, "_indice_sucio", {}).items() if v]
            for a in cuentas:
                self._indice_sucio[a] = False
                if hasattr(self, "_ult_export"):
                    self._ult_export[a] = _t.time()
        for a in cuentas:
            try:
                self.exportar_indice(a)
            except Exception as e:
                log.debug("No se pudo exportar el índice pendiente: %s", e)
