# -*- coding: utf-8 -*-
"""Borradores persistentes de Mastodon (auditoría N3).

Guardan en SQLite (tabla mastodon_drafts) TODO lo que había en el compositor —texto,
advertencia de contenido, privacidad, idioma, adjuntos con sus descripciones, encuesta y
programación— más el contexto (respuesta o cita), para poder cerrar la aplicación y
retomarlos después tal cual. Son operaciones locales de BD (sin red): pueden llamarse
desde el hilo de interfaz.
"""
import json
import logging
from datetime import datetime

from app.core.database import db

log = logging.getLogger(__name__)


def guardar_borrador(account_id: int, texto: str, borrador: dict, contexto: dict = None,
                     draft_id: int = None) -> int:
    """Crea (o actualiza, si se pasa draft_id) un borrador persistente. `borrador` debe ser
    JSON-serializable (ver _borrador_persistible del compositor). Devuelve el id del borrador."""
    ahora = datetime.now().isoformat(timespec="seconds")
    borrador_json = json.dumps(borrador or {}, ensure_ascii=False)
    contexto_json = json.dumps(contexto, ensure_ascii=False) if contexto else None
    if draft_id:
        fila = db.query_one("SELECT id FROM mastodon_drafts WHERE id = ?", (draft_id,))
        if fila:
            db.execute(
                "UPDATE mastodon_drafts SET account_id = ?, content = ?, updated_at = ?, "
                "contexto = ?, borrador_json = ? WHERE id = ?",
                (account_id, texto or "", ahora, contexto_json, borrador_json, draft_id))
            return draft_id
    return db.execute_insert(
        "INSERT INTO mastodon_drafts (account_id, content, created_at, updated_at, contexto, "
        "borrador_json) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, texto or "", ahora, ahora, contexto_json, borrador_json))


def listar_borradores(account_id: int) -> list:
    """Borradores de una cuenta, del más reciente al más antiguo. Cada elemento:
    {id, texto, actualizado, contexto (dict)}."""
    filas = db.query(
        "SELECT id, content, COALESCE(updated_at, created_at), contexto FROM mastodon_drafts "
        "WHERE account_id = ? ORDER BY COALESCE(updated_at, created_at) DESC, id DESC",
        (account_id,))
    resultado = []
    for f in filas:
        ctx = {}
        if f[3]:
            try:
                ctx = json.loads(f[3]) or {}
            except (ValueError, TypeError):
                ctx = {}
        resultado.append({"id": f[0], "texto": f[1] or "", "actualizado": f[2] or "", "contexto": ctx})
    return resultado


def obtener_borrador(draft_id: int):
    """Devuelve {id, account_id, borrador (dict), contexto (dict)} o None si no existe."""
    fila = db.query_one(
        "SELECT id, account_id, borrador_json, contexto FROM mastodon_drafts WHERE id = ?",
        (draft_id,))
    if not fila:
        return None
    borrador, contexto = {}, {}
    try:
        borrador = json.loads(fila[2]) or {}
    except (ValueError, TypeError):
        pass
    if fila[3]:
        try:
            contexto = json.loads(fila[3]) or {}
        except (ValueError, TypeError):
            pass
    return {"id": fila[0], "account_id": fila[1], "borrador": borrador, "contexto": contexto}


def borrar_borrador(draft_id: int):
    """Elimina un borrador (al publicarse o al borrarlo el usuario)."""
    try:
        db.execute("DELETE FROM mastodon_drafts WHERE id = ?", (draft_id,))
    except Exception as e:
        log.debug("No se pudo borrar el borrador %s: %s", draft_id, e)


def contar_borradores(account_id: int) -> int:
    fila = db.query_one("SELECT COUNT(*) FROM mastodon_drafts WHERE account_id = ?", (account_id,))
    return int(fila[0]) if fila else 0
