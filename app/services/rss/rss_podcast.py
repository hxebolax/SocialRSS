# -*- coding: utf-8 -*-
"""Cola de descargas de podcast (mixin de RSSService).

Descargas OFFLINE gestionadas como una COLA persistente (tabla rss_download_queue):
encolar, pausar, reanudar y cancelar, con reanudación REAL por rangos HTTP (Range sobre
el .part) — a diferencia de descargar_episodio (rss_articulos), que trunca y borra el
.part al fallar. Un único trabajador de fondo (prioridad de disco baja) procesa la cola
respetando un límite de espacio total de la carpeta de podcasts.

NO toca el arranque: el trabajador se auto-arranca la primera vez que se encola algo.
"""
import os
import threading
import time as _t
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.core.database import db

import builtins as _bi
if not hasattr(_bi, "_"):
    _bi._ = lambda s: s  # gettext puede no estar instalado (tests/CLI)

import logging
log = logging.getLogger(__name__)

# Cuántas descargas simultáneas (audio es E/S; 1 basta para no saturar disco/red durante
# el uso normal, y evita competir con el sondeo de feeds).
_MAX_SIMULTANEAS = 1
# Cada cuántos bytes se persiste el progreso (no en cada chunk: martillearía la base).
_PERSISTIR_CADA = 512 * 1024
# Límite por defecto del espacio total de la carpeta de podcasts (MiB); 0 = sin límite.
_LIMITE_MIB_DEFECTO = 4096


class _PodcastColaMixin:
    # Lock e hilo del trabajador (perezosos, por instancia).
    def _cola_init(self):
        if not hasattr(self, "_cola_lock"):
            self._cola_lock = threading.Lock()
            self._cola_worker = None
            self._cola_cancelados = set()   # article_ids que el usuario canceló en vuelo

    # --------------------------------------------------------------- API pública
    def encolar_descarga(self, article_id: str) -> bool:
        """Añade un episodio a la cola de descargas (o lo reactiva si estaba pausado/error).
        Devuelve False si el artículo no tiene audio descargable. Arranca el trabajador."""
        self._cola_init()
        r = db.query_one(
            "SELECT a.enclosure_url, a.media_url, f.account_id FROM rss_articles a "
            "JOIN rss_feeds f ON a.feed_id = f.id WHERE a.id = ?", (article_id,))
        if not r:
            return False
        url = r[0] or r[1]
        if not url:
            return False
        # Ya descargado: no reencolar.
        if self.episodio_descargado(article_id):
            return True
        try:
            db.execute(
                "INSERT INTO rss_download_queue (article_id, account_id, url, estado, updated_at) "
                "VALUES (?, ?, ?, 'cola', ?) "
                "ON CONFLICT(article_id) DO UPDATE SET estado='cola', error=NULL, "
                "url=excluded.url, updated_at=excluded.updated_at",
                (article_id, r[2], url, datetime.now().isoformat()))
        except Exception as e:
            log.error(f"No se pudo encolar la descarga de {article_id}: {e}")
            return False
        with self._cola_lock:
            self._cola_cancelados.discard(article_id)
        self._arrancar_worker()
        return True

    def pausar_descarga(self, article_id: str):
        """Marca la descarga como pausada; el trabajador la detiene tras el chunk actual y
        CONSERVA el .part para reanudar por Range."""
        db.execute("UPDATE rss_download_queue SET estado='pausado', updated_at=? "
                   "WHERE article_id=? AND estado IN ('cola','descargando')",
                   (datetime.now().isoformat(), article_id))

    def reanudar_descarga(self, article_id: str):
        """Vuelve a poner en cola una descarga pausada o con error."""
        self._cola_init()
        db.execute("UPDATE rss_download_queue SET estado='cola', error=NULL, updated_at=? "
                   "WHERE article_id=? AND estado IN ('pausado','error')",
                   (datetime.now().isoformat(), article_id))
        with self._cola_lock:
            self._cola_cancelados.discard(article_id)
        self._arrancar_worker()

    def cancelar_descarga(self, article_id: str):
        """Cancela y quita de la cola; borra el .part si existe."""
        self._cola_init()
        with self._cola_lock:
            self._cola_cancelados.add(article_id)
        r = db.query_one("SELECT ruta_part FROM rss_download_queue WHERE article_id=?",
                         (article_id,))
        if r and r[0]:
            try:
                if os.path.exists(r[0]):
                    os.remove(r[0])
            except Exception:
                pass
        db.execute("DELETE FROM rss_download_queue WHERE article_id=?", (article_id,))

    def obtener_cola_descargas(self, account_id: int) -> List[Dict[str, Any]]:
        """Estado de la cola de UNA cuenta, para el diálogo. Cada entrada incluye título,
        podcast, estado, bytes hechos/total, porcentaje y si ya se escuchó."""
        try:
            filas = db.query(
                "SELECT q.article_id, a.title, f.title, q.estado, q.bytes_hechos, "
                "q.bytes_total, COALESCE(q.error,''), a.download_path, a.enclosure_url "
                "FROM rss_download_queue q "
                "JOIN rss_articles a ON a.id = q.article_id "
                "JOIN rss_feeds f ON f.id = a.feed_id "
                "WHERE q.account_id = ? "
                "ORDER BY CASE q.estado WHEN 'descargando' THEN 0 WHEN 'cola' THEN 1 "
                "         WHEN 'pausado' THEN 2 WHEN 'error' THEN 3 ELSE 4 END, q.updated_at",
                (account_id,))
        except Exception as e:
            log.error(f"No se pudo leer la cola de descargas: {e}")
            return []
        salida = []
        for r in filas:
            hechos, total = r[4] or 0, r[5] or 0
            pct = int(hechos * 100 / total) if total else 0
            salida.append({
                "article_id": r[0], "title": r[1] or "", "feed_title": r[2] or "",
                "estado": r[3], "bytes_hechos": hechos, "bytes_total": total,
                "porcentaje": pct, "error": r[6],
                "reproducido": self._episodio_reproducido(r[7], r[8], r[0]),
            })
        return salida

    def _episodio_reproducido(self, download_path, enclosure_url, article_id="") -> bool:
        """True si el episodio se escuchó entero (play_history.finished) por su ruta local
        o por su URL de streaming (la posición se indexa por una u otra)."""
        # "rss:<id>" es la clave compartida entre streaming y descarga (player2_podcast.py).
        for clave in (f"rss:{article_id}" if article_id else "", download_path, enclosure_url):
            if not clave:
                continue
            try:
                r = db.query_one(
                    "SELECT finished FROM play_history WHERE media_url = ?", (clave,))
                if r and r[0]:
                    return True
            except Exception:
                pass
        return False

    def espacio_podcasts_bytes(self) -> int:
        """Bytes totales ocupados por la carpeta de podcasts descargados."""
        from app.core.paths import get_data_path
        base = get_data_path("podcasts")
        total = 0
        try:
            for raiz, _dirs, ficheros in os.walk(base):
                for f in ficheros:
                    if f.endswith(".part"):
                        continue
                    try:
                        total += os.path.getsize(os.path.join(raiz, f))
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def _limite_espacio_bytes(self) -> int:
        from app.core.config_manager import config
        try:
            mib = int(config.get("rss", "podcast_max_mib", _LIMITE_MIB_DEFECTO))
        except Exception:
            mib = _LIMITE_MIB_DEFECTO
        return mib * 1024 * 1024 if mib > 0 else 0

    def reanudar_cola_al_arrancar(self) -> int:
        """Retoma la cola de descargas al abrir la app. Lo que se estaba descargando cuando se
        cerró se quedaba para siempre en "descargando" (el trabajador solo arrancaba al encolar
        o reanudar a mano): vuelve a la cola y continúa desde su .part (Range). Lo pausado por
        el usuario sigue pausado. Devuelve cuántas descargas quedan pendientes."""
        self._cola_init()
        try:
            db.execute("UPDATE rss_download_queue SET estado='cola', updated_at=? "
                       "WHERE estado='descargando'", (datetime.now().isoformat(),))
            fila = db.query_one("SELECT COUNT(*) FROM rss_download_queue WHERE estado='cola'")
        except Exception as e:
            log.debug(f"No se pudo retomar la cola de descargas: {e}")
            return 0
        pendientes = int(fila[0] or 0) if fila else 0
        if pendientes:
            self._arrancar_worker()
        return pendientes

    # --------------------------------------------------------------- trabajador
    def _arrancar_worker(self):
        self._cola_init()
        with self._cola_lock:
            if self._cola_worker is not None and self._cola_worker.is_alive():
                return
            self._cola_worker = threading.Thread(
                target=self._bucle_descargas, name="rss-podcast-dl", daemon=True)
            self._cola_worker.start()

    def _prioridad_fondo(self):
        """THREAD_MODE_BACKGROUND: baja CPU y DISCO del hilo (la descarga no compite con
        la base de datos ni con el lector). Respaldo a prioridad baja normal."""
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            if not k32.SetThreadPriority(k32.GetCurrentThread(), 0x00010000):
                k32.SetThreadPriority(k32.GetCurrentThread(), -1)
        except Exception:
            pass

    def _bucle_descargas(self):
        self._prioridad_fondo()
        try:
            while True:
                fila = db.query_one(
                    "SELECT article_id, url FROM rss_download_queue WHERE estado='cola' "
                    "ORDER BY prioridad DESC, updated_at LIMIT 1")
                if not fila:
                    return  # cola vacía: el hilo muere; se recrea al encolar
                article_id, url = fila
                with self._cola_lock:
                    if article_id in self._cola_cancelados:
                        db.execute("DELETE FROM rss_download_queue WHERE article_id=?",
                                   (article_id,))
                        continue
                # Límite de espacio: si se supera, marcar error y seguir (no bloquear la cola).
                limite = self._limite_espacio_bytes()
                if limite and self.espacio_podcasts_bytes() >= limite:
                    db.execute("UPDATE rss_download_queue SET estado='error', error=?, "
                               "updated_at=? WHERE article_id=?",
                               (_("Sin espacio: la carpeta de podcasts alcanzó el límite."),
                                datetime.now().isoformat(), article_id))
                    continue
                db.execute("UPDATE rss_download_queue SET estado='descargando', updated_at=? "
                           "WHERE article_id=?", (datetime.now().isoformat(), article_id))
                try:
                    self._descargar_encolado(article_id, url)
                except Exception as e:
                    log.debug(f"Descarga encolada de {article_id} falló: {e}")
                    db.execute("UPDATE rss_download_queue SET estado='error', error=?, "
                               "updated_at=? WHERE article_id=?",
                               (str(e)[:300], datetime.now().isoformat(), article_id))
                finally:
                    db.cerrar_lectura_hilo()
                _t.sleep(0.5)
        finally:
            db.cerrar_lectura_hilo()

    def _descargar_encolado(self, article_id: str, url: str):
        """Descarga con REANUDACIÓN por rangos: si hay un .part previo, continúa desde su
        tamaño con la cabecera Range; al pausar/cancelar NO se borra el .part. Renombrado
        atómico al terminar. Respeta el estado de la cola (pausado/cancelado) cada chunk."""
        import requests
        import re
        from urllib.parse import urlparse
        from app.core.config_manager import config

        r = db.query_one(
            "SELECT a.title, f.title FROM rss_articles a JOIN rss_feeds f ON a.feed_id=f.id "
            "WHERE a.id=?", (article_id,))
        title = (r[0] if r else "") or "episodio"
        feed_title = (r[1] if r else "") or ""
        carpeta = self._carpeta_podcasts(feed_title)
        ext = os.path.splitext(urlparse(url).path)[1] or ".mp3"
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title.strip())[:80] or "episodio"
        ruta = str(carpeta / (base + ext))
        ruta_part = ruta + ".part"
        MAX_EPISODIO = 2 * 1024 * 1024 * 1024  # 2 GiB

        # Reanudación: si hay .part, continuar desde su tamaño.
        ya_hecho = os.path.getsize(ruta_part) if os.path.exists(ruta_part) else 0
        headers = {"User-Agent": "SocialRSS Suite"}
        if ya_hecho:
            headers["Range"] = f"bytes={ya_hecho}-"
        db.execute("UPDATE rss_download_queue SET ruta_part=? WHERE article_id=?",
                   (ruta_part, article_id))

        with requests.get(url, stream=True, timeout=60, headers=headers) as resp:
            # 206 = rango aceptado (reanuda); 200 = el servidor ignora Range (reempieza).
            if ya_hecho and resp.status_code == 200:
                ya_hecho = 0  # el servidor no soporta Range: empezar de cero
            resp.raise_for_status()
            rango = resp.headers.get("Content-Range", "")
            if rango and "/" in rango:
                total = int(rango.rsplit("/", 1)[1])
            else:
                total = (int(resp.headers.get("Content-Length") or 0) + ya_hecho)
            hecho = ya_hecho
            desde_persistido = ya_hecho
            modo = "ab" if ya_hecho else "wb"
            db.execute("UPDATE rss_download_queue SET bytes_total=?, bytes_hechos=? "
                       "WHERE article_id=?", (total, hecho, article_id))
            with open(ruta_part, modo) as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    # Respetar pausa/cancelación entre chunks (consulta barata al estado).
                    est = db.query_one("SELECT estado FROM rss_download_queue WHERE article_id=?",
                                       (article_id,))
                    with self._cola_lock:
                        cancelado = article_id in self._cola_cancelados
                    if cancelado or not est:
                        return  # cancelado: el .part se borra en cancelar_descarga
                    if est[0] == "pausado":
                        db.execute("UPDATE rss_download_queue SET bytes_hechos=? "
                                   "WHERE article_id=?", (hecho, article_id))
                        return  # el .part queda para reanudar
                    fh.write(chunk)
                    hecho += len(chunk)
                    if hecho > MAX_EPISODIO:
                        raise ValueError(_("El episodio supera el tamaño máximo."))
                    if hecho - desde_persistido >= _PERSISTIR_CADA:
                        desde_persistido = hecho
                        db.execute("UPDATE rss_download_queue SET bytes_hechos=? "
                                   "WHERE article_id=?", (hecho, article_id))
            if total and hecho < total:
                raise IOError(_("Descarga incompleta ({0} de {1} bytes).").format(hecho, total))
        os.replace(ruta_part, ruta)
        db.execute("UPDATE rss_articles SET download_path=? WHERE id=?", (ruta, article_id))
        db.execute("UPDATE rss_download_queue SET estado='hecho', bytes_hechos=?, "
                   "bytes_total=?, ruta_part=NULL, updated_at=? WHERE article_id=?",
                   (hecho, total or hecho, datetime.now().isoformat(), article_id))
        log.info(f"Podcast descargado (cola): {ruta}")
