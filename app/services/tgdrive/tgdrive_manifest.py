# -*- coding: utf-8 -*-
"""Manifiesto del índice de Telegram Drive: copia recuperable de la estructura de la nube.

El manifiesto es un documento JSON VERSIONADO que se sube al canal privado (y se fija). Con
él, la base de datos local se puede reconstruir por completo si se reinstala la app, se borra
la base, se usa la cuenta desde otro ordenador o el índice queda inconsistente. Formato
documentado y preparado para migraciones futuras (`manifest_version`).
"""
import json
from datetime import datetime
from typing import Dict, Any

from app.core.database import db

import logging
log = logging.getLogger(__name__)

# Versión del formato del manifiesto. Subir SOLO al cambiar la estructura del JSON.
MANIFEST_VERSION = 1

# Nombre del documento del manifiesto dentro del canal.
NOMBRE_MANIFIESTO = "socialrss_drive_index.json"


def construir_manifiesto(account_id: int, channel_id: int) -> Dict[str, Any]:
    """Serializa TODA la estructura de una cuenta (items y partes, salvo los eliminados
    definitivos) a un diccionario versionado, listo para volcar a JSON y subir al canal."""
    items = []
    for r in db.query(
            "SELECT id, parent_id, name, tipo, path, size, mime, sha256, created_at, "
            "modified_at, estado, trashed_at, original_parent_id, original_path, num_parts, "
            "encrypted, meta_version, descripcion FROM tgdrive_items "
            "WHERE account_id = ? AND estado != 'deleted'", (account_id,)):
        items.append({
            "id": r[0], "parent_id": r[1], "name": r[2], "tipo": r[3], "path": r[4],
            "size": r[5], "mime": r[6], "sha256": r[7], "created_at": r[8],
            "modified_at": r[9], "estado": r[10], "trashed_at": r[11],
            "original_parent_id": r[12], "original_path": r[13], "num_parts": r[14],
            "encrypted": r[15], "meta_version": r[16], "descripcion": r[17] or "",
        })
    ids = {it["id"] for it in items}
    partes = []
    for r in db.query(
            "SELECT p.item_id, p.orden, p.size, p.sha256, p.message_id, p.tg_file_id, "
            "p.tg_unique_id, p.estado FROM tgdrive_parts p "
            "JOIN tgdrive_items i ON i.id = p.item_id WHERE i.account_id = ?", (account_id,)):
        if r[0] not in ids:
            continue
        partes.append({
            "item_id": r[0], "orden": r[1], "size": r[2], "sha256": r[3],
            "message_id": r[4], "tg_file_id": r[5], "tg_unique_id": r[6], "estado": r[7],
        })
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "account_id": account_id,
        "channel_id": channel_id,
        "items": items,
        "parts": partes,
    }


def volcar_json(manifiesto: Dict[str, Any]) -> bytes:
    """Convierte el manifiesto a bytes UTF-8 (JSON compacto)."""
    return json.dumps(manifiesto, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def cargar_json(datos: bytes) -> Dict[str, Any]:
    """Interpreta los bytes de un manifiesto. Lanza ValueError si el formato no es válido o
    la versión es más nueva de la que esta app entiende."""
    try:
        texto = datos.decode("utf-8-sig")
        m = json.loads(texto)
    except Exception as e:
        raise ValueError(f"El manifiesto no es un JSON válido: {e}")
    if not isinstance(m, dict) or "manifest_version" not in m:
        raise ValueError("El manifiesto no tiene el formato esperado.")
    if int(m.get("manifest_version", 0)) > MANIFEST_VERSION:
        raise ValueError("El manifiesto es de una versión más nueva de la aplicación.")
    _validar_estructura(m)
    return m


# Tope defensivo: un manifiesto no confiable no debe poder inflar la BD sin límite (TG-005).
_MAX_ITEMS = 200000
_MAX_PARTS = 2000000


def _validar_estructura(m: Dict[str, Any]):
    """Valida que items/parts del manifiesto tengan tipos y valores razonables ANTES de reconstruir la
    base local (TG-005): rechaza estructuras absurdas o maliciosas. El confinamiento de rutas al
    DESCARGAR es la protección final; esto evita además ciclos e IDs duplicados/incoherentes."""
    items = m.get("items")
    partes = m.get("parts")
    if not isinstance(items, list) or not isinstance(partes, list):
        raise ValueError("El manifiesto no contiene listas de elementos y partes válidas.")
    if len(items) > _MAX_ITEMS or len(partes) > _MAX_PARTS:
        raise ValueError("El manifiesto es demasiado grande; se rechaza por seguridad.")
    ids = set()
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("Elemento de manifiesto no válido.")
        iid = it.get("id")
        if not isinstance(iid, str) or not iid or iid in ids:
            raise ValueError("Identificador de elemento ausente o duplicado en el manifiesto.")
        ids.add(iid)
        if it.get("tipo") not in ("folder", "file"):
            raise ValueError("Tipo de elemento desconocido en el manifiesto.")
        if not isinstance(it.get("name", ""), str):
            raise ValueError("Nombre de elemento no válido en el manifiesto.")
        tam = it.get("size") or 0
        if not isinstance(tam, int) or tam < 0:
            raise ValueError("Tamaño de elemento no válido en el manifiesto.")
    # parent_id debe apuntar a un id existente (o ser nulo/raíz); un parent ausente rompería el árbol.
    for it in items:
        par = it.get("parent_id")
        if par is not None and par not in ids:
            raise ValueError("El manifiesto referencia una carpeta padre inexistente.")
    # Grafo ACÍCLICO: subir por parent_id desde cada nodo no debe formar un ciclo.
    por_id = {it["id"]: it.get("parent_id") for it in items}
    for iid in ids:
        visto = set()
        cur = iid
        while cur is not None:
            if cur in visto:
                raise ValueError("El manifiesto contiene un ciclo de carpetas.")
            visto.add(cur)
            cur = por_id.get(cur)


def reconstruir_base(account_id: int, manifiesto: Dict[str, Any]) -> Dict[str, int]:
    """Reconstruye las tablas locales de una cuenta a partir de un manifiesto. Borra lo local
    de esa cuenta y lo repuebla EN UNA transacción (todo o nada). Devuelve el recuento."""
    items = manifiesto.get("items", [])
    partes = manifiesto.get("parts", [])
    channel_id = manifiesto.get("channel_id")
    with db.transaccion():
        db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (account_id,))
        # Insertar carpetas antes que archivos y por profundidad de ruta, para que las
        # claves foráneas de parent_id no fallen (el padre siempre existe antes que el hijo).
        orden = sorted(items, key=lambda it: (0 if it.get("tipo") == "folder" else 1,
                                              (it.get("path") or "").count("/")))
        for it in orden:
            db.execute(
                "INSERT INTO tgdrive_items (id, account_id, channel_id, parent_id, name, tipo, "
                "path, size, mime, sha256, created_at, modified_at, estado, trashed_at, "
                "original_parent_id, original_path, num_parts, encrypted, meta_version, "
                "descripcion) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (it["id"], account_id, channel_id, it.get("parent_id"), it.get("name"),
                 it.get("tipo"), it.get("path") or "", it.get("size") or 0, it.get("mime"),
                 it.get("sha256"), it.get("created_at") or "", it.get("modified_at") or "",
                 it.get("estado") or "normal", it.get("trashed_at"),
                 it.get("original_parent_id"), it.get("original_path"),
                 it.get("num_parts") or 1, 1 if it.get("encrypted") else 0,
                 it.get("meta_version") or 1, it.get("descripcion") or ""))
        ids = {it["id"] for it in items}
        for p in partes:
            if p.get("item_id") not in ids:
                continue
            db.execute(
                "INSERT INTO tgdrive_parts (item_id, orden, size, sha256, message_id, "
                "tg_file_id, tg_unique_id, estado) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (p["item_id"], p.get("orden") or 0, p.get("size") or 0, p.get("sha256"),
                 p.get("message_id"), p.get("tg_file_id"), p.get("tg_unique_id"),
                 p.get("estado") or "uploaded"))
    return {"items": len(items), "parts": len(partes)}
