# -*- coding: utf-8 -*-
"""
Módulo para el historial de reproducción y guardado de progreso de pistas en SQLite.
Permite reanudar podcasts o audios largos en el punto exacto donde se dejaron.
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.core.database import db

log = logging.getLogger(__name__)

class PlayerHistory:
    """Gestiona el historial de reproducción persistente en SQLite."""
    
    @staticmethod
    def guardar_progreso(
        media_url: str,
        title: str,
        service_origin: str,
        account_origin_id: Optional[int],
        last_position: float,
        duration: float,
        finished: bool = False
    ):
        """Guarda o actualiza el progreso de reproducción de un medio."""
        sql = """
        INSERT INTO play_history (
            media_url, title, service_origin, account_origin_id, last_position, duration, last_played_at, finished
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(media_url) DO UPDATE SET
            last_position = excluded.last_position,
            duration = excluded.duration,
            last_played_at = excluded.last_played_at,
            finished = excluded.finished;
        """
        now_str = datetime.now().isoformat()
        try:
            db.execute(sql, (
                media_url,
                title,
                service_origin,
                account_origin_id,
                last_position,
                duration,
                now_str,
                1 if finished else 0
            ))
        except Exception as e:
            log.error(f"Error al guardar progreso de reproducción para {media_url}: {e}")

    @staticmethod
    def obtener_progreso(media_url: str) -> float:
        """Devuelve la última posición guardada en segundos. Devuelve 0.0 si no existe."""
        sql = "SELECT last_position, finished FROM play_history WHERE media_url = ?"
        try:
            row = db.query_one(sql, (media_url,))
            if row:
                pos, finished = row
                # Si ya finalizó, empezar de nuevo (0.0)
                return 0.0 if finished == 1 else float(pos)
        except Exception as e:
            log.error(f"Error al obtener progreso de reproducción para {media_url}: {e}")
        return 0.0

    @staticmethod
    def borrar_progreso(media_url: str):
        """Olvida la posición guardada de un medio (al volver a reproducirlo, empieza de cero)."""
        try:
            db.execute("DELETE FROM play_history WHERE media_url = ?", (media_url,))
        except Exception as e:
            log.error(f"Error al borrar el progreso de reproducción para {media_url}: {e}")

    @staticmethod
    def obtener_historial(limit: int = 100) -> List[Dict[str, Any]]:
        """Recupera el historial completo de reproducción ordenado por fecha de audición."""
        sql = """
        SELECT id, media_url, title, service_origin, account_origin_id, last_position, duration, last_played_at, finished
        FROM play_history
        ORDER BY last_played_at DESC
        LIMIT ?
        """
        historial = []
        try:
            rows = db.query(sql, (limit,))
            for r in rows:
                historial.append({
                    "id": r[0],
                    "media_url": r[1],
                    "title": r[2],
                    "service_origin": r[3],
                    "account_origin_id": r[4],
                    "last_position": r[5],
                    "duration": r[6],
                    "last_played_at": r[7],
                    "finished": bool(r[8])
                })
        except Exception as e:
            log.error(f"Error al obtener historial de reproducción: {e}")
        return historial

    @staticmethod
    def borrar_historial():
        """Elimina todos los registros de historial en la base de datos."""
        sql = "DELETE FROM play_history"
        try:
            db.execute(sql)
            log.info("Historial de reproducción vaciado correctamente de la base de datos.")
        except Exception as e:
            log.error(f"Error al borrar el historial de reproducción: {e}")
