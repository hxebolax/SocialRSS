# -*- coding: utf-8 -*-
"""Repositorio SQLite de Telegram Drive (persistencia; sin Telegram ni interfaz).

Encapsula TODO el acceso a las tablas tgdrive_* : la jerarquía virtual de carpetas y
archivos, sus partes, la papelera, los movimientos (con detección de ciclos), la
deduplicación por contenido y la cola de transferencias. Usa transacciones para que un
archivo a medias nunca quede registrado como completo, y claves foráneas para la
integridad referencial.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from app.core.database import db
from .tgdrive_models import (
    ItemNube, ParteNube, TIPO_CARPETA, TIPO_ARCHIVO,
    ESTADO_NORMAL, ESTADO_PAPELERA, ESTADO_INCOMPLETO, ESTADO_ELIMINADO, META_VERSION)

import logging
log = logging.getLogger(__name__)


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _nuevo_uuid() -> str:
    return uuid.uuid4().hex


class TgDriveRepo:
    """Acceso a datos de Telegram Drive. Todos los métodos trabajan sobre UNA cuenta."""

    # ---------------------------------------------------------------- lectura
    def _fila_a_item(self, r) -> ItemNube:
        return ItemNube(
            id=r[0], account_id=r[1], channel_id=r[2], parent_id=r[3], name=r[4],
            tipo=r[5], path=r[6], size=r[7] or 0, mime=r[8], sha256=r[9],
            created_at=r[10], modified_at=r[11], estado=r[12], trashed_at=r[13],
            original_parent_id=r[14], original_path=r[15], num_parts=r[16] or 1,
            encrypted=bool(r[17]), meta_version=r[18] or META_VERSION,
            descripcion=r[19] or "")

    _COLS = ("id, account_id, channel_id, parent_id, name, tipo, path, size, mime, sha256, "
             "created_at, modified_at, estado, trashed_at, original_parent_id, original_path, "
             "num_parts, encrypted, meta_version, descripcion")

    def obtener_item(self, item_id: str) -> Optional[ItemNube]:
        """Devuelve un elemento por su UUID, o None."""
        r = db.query_one(f"SELECT {self._COLS} FROM tgdrive_items WHERE id = ?", (item_id,))
        return self._fila_a_item(r) if r else None

    def listar(self, account_id: int, parent_id: Optional[str],
               estado: str = ESTADO_NORMAL) -> List[ItemNube]:
        """Lista los elementos de una carpeta (parent_id None = raíz), en un estado dado.
        Las CARPETAS van primero, luego los archivos, ambos por nombre."""
        if parent_id is None:
            sql = (f"SELECT {self._COLS} FROM tgdrive_items WHERE account_id = ? "
                   "AND parent_id IS NULL AND estado = ? "
                   "ORDER BY CASE tipo WHEN 'folder' THEN 0 ELSE 1 END, name COLLATE NOCASE")
            filas = db.query(sql, (account_id, estado))
        else:
            sql = (f"SELECT {self._COLS} FROM tgdrive_items WHERE account_id = ? "
                   "AND parent_id = ? AND estado = ? "
                   "ORDER BY CASE tipo WHEN 'folder' THEN 0 ELSE 1 END, name COLLATE NOCASE")
            filas = db.query(sql, (account_id, parent_id, estado))
        return [self._fila_a_item(r) for r in filas]

    def listar_papelera(self, account_id: int) -> List[ItemNube]:
        """Elementos en la papelera de una cuenta (los de nivel superior que se enviaron a
        la papelera; sus descendientes van implícitos)."""
        filas = db.query(
            f"SELECT {self._COLS} FROM tgdrive_items WHERE account_id = ? AND estado = ? "
            "ORDER BY trashed_at DESC", (account_id, ESTADO_PAPELERA))
        return [self._fila_a_item(r) for r in filas]

    def papelera_caducada(self, account_id: int, dias: int) -> List[ItemNube]:
        """Elementos de la papelera cuyo `trashed_at` es más antiguo que `dias` días (para la
        limpieza automática por retención). Si `dias` <= 0, no caduca nada."""
        if not dias or dias <= 0:
            return []
        from datetime import timedelta
        limite = (datetime.now() - timedelta(days=int(dias))).isoformat(timespec="seconds")
        filas = db.query(
            f"SELECT {self._COLS} FROM tgdrive_items WHERE account_id = ? AND estado = ? "
            "AND trashed_at IS NOT NULL AND trashed_at < ? ORDER BY trashed_at",
            (account_id, ESTADO_PAPELERA, limite))
        return [self._fila_a_item(r) for r in filas]

    def obtener_partes(self, item_id: str) -> List[ParteNube]:
        """Partes de un archivo, en orden."""
        filas = db.query(
            "SELECT orden, size, sha256, message_id, tg_file_id, tg_unique_id, estado, id "
            "FROM tgdrive_parts WHERE item_id = ? ORDER BY orden", (item_id,))
        return [ParteNube(orden=r[0], size=r[1] or 0, sha256=r[2], message_id=r[3],
                          tg_file_id=r[4], tg_unique_id=r[5], estado=r[6], id=r[7])
                for r in filas]

    def buscar_por_sha256(self, account_id: int, sha256: str) -> Optional[ItemNube]:
        """Busca un archivo YA subido con el mismo contenido (para no volver a subir sus
        bytes). Solo considera archivos completos y con partes válidas."""
        if not sha256:
            return None
        r = db.query_one(
            f"SELECT {self._COLS} FROM tgdrive_items WHERE account_id = ? AND sha256 = ? "
            "AND tipo = 'file' AND estado = 'normal' LIMIT 1", (account_id, sha256))
        return self._fila_a_item(r) if r else None

    def buscar(self, account_id: int, texto: str, estado: str = ESTADO_NORMAL) -> List[ItemNube]:
        """Búsqueda por nombre (subcadena, sin distinguir mayúsculas) en toda la cuenta."""
        patron = f"%{(texto or '').strip()}%"
        filas = db.query(
            f"SELECT {self._COLS} FROM tgdrive_items WHERE account_id = ? AND estado = ? "
            "AND name LIKE ? COLLATE NOCASE "
            "ORDER BY CASE tipo WHEN 'folder' THEN 0 ELSE 1 END, name COLLATE NOCASE LIMIT 500",
            (account_id, estado, patron))
        return [self._fila_a_item(r) for r in filas]

    def existe_nombre(self, account_id: int, parent_id: Optional[str], name: str) -> bool:
        """True si ya hay un elemento (normal) con ese nombre en la carpeta."""
        if parent_id is None:
            r = db.query_one("SELECT 1 FROM tgdrive_items WHERE account_id = ? "
                             "AND parent_id IS NULL AND name = ? AND estado = 'normal'",
                             (account_id, name))
        else:
            r = db.query_one("SELECT 1 FROM tgdrive_items WHERE account_id = ? "
                             "AND parent_id = ? AND name = ? AND estado = 'normal'",
                             (account_id, parent_id, name))
        return r is not None

    def nombre_disponible(self, account_id: int, parent_id: Optional[str], nombre: str) -> str:
        """Devuelve un nombre que no colisione en la carpeta: si 'foto.jpg' existe, prueba
        'foto (2).jpg', 'foto (3).jpg'... (para la opción 'conservar ambos')."""
        if not self.existe_nombre(account_id, parent_id, nombre):
            return nombre
        import os
        base, ext = os.path.splitext(nombre)
        n = 2
        while True:
            candidato = f"{base} ({n}){ext}"
            if not self.existe_nombre(account_id, parent_id, candidato):
                return candidato
            n += 1

    # ---------------------------------------------------------------- rutas
    def _path_de(self, account_id: int, parent_id: Optional[str], name: str) -> str:
        """Construye la ruta virtual de un elemento a partir de su padre."""
        if parent_id is None:
            return "/" + name
        padre = self.obtener_item(parent_id)
        base = padre.path if padre else ""
        return (base.rstrip("/") + "/" + name) if base else "/" + name

    def _recalcular_paths_descendientes(self, item_id: str):
        """Tras mover o renombrar, actualiza la ruta denormalizada del elemento y de todos
        sus descendientes (recorrido en anchura)."""
        it = self.obtener_item(item_id)
        if not it:
            return
        it.path = self._path_de(it.account_id, it.parent_id, it.name)
        db.execute("UPDATE tgdrive_items SET path = ? WHERE id = ?", (it.path, item_id))
        # Descendientes directos, recursivo.
        for (hijo_id,) in db.query("SELECT id FROM tgdrive_items WHERE parent_id = ?", (item_id,)):
            self._recalcular_paths_descendientes(hijo_id)

    # ---------------------------------------------------------------- escritura
    def crear_carpeta(self, account_id: int, parent_id: Optional[str], name: str,
                      channel_id: Optional[int] = None, descripcion: str = "") -> ItemNube:
        """Crea una carpeta virtual. El nombre se asume ya validado por el llamador."""
        item_id = _nuevo_uuid()
        ahora = _ahora()
        path = self._path_de(account_id, parent_id, name)
        db.execute(
            "INSERT INTO tgdrive_items (id, account_id, channel_id, parent_id, name, tipo, "
            "path, created_at, modified_at, estado, num_parts, meta_version, descripcion) "
            "VALUES (?, ?, ?, ?, ?, 'folder', ?, ?, ?, 'normal', 0, ?, ?)",
            (item_id, account_id, channel_id, parent_id, name, path, ahora, ahora, META_VERSION,
             descripcion or ""))
        return self.obtener_item(item_id)

    def crear_archivo_incompleto(self, account_id: int, parent_id: Optional[str], name: str,
                                 size: int, mime: Optional[str], sha256: Optional[str],
                                 num_parts: int, channel_id: Optional[int],
                                 encrypted: bool = False, descripcion: str = "") -> ItemNube:
        """Crea la fila de un archivo en estado INCOMPLETO (aún subiéndose). No se mostrará
        como archivo válido hasta que `finalizar_archivo` lo marque normal."""
        item_id = _nuevo_uuid()
        ahora = _ahora()
        path = self._path_de(account_id, parent_id, name)
        db.execute(
            "INSERT INTO tgdrive_items (id, account_id, channel_id, parent_id, name, tipo, "
            "path, size, mime, sha256, created_at, modified_at, estado, num_parts, encrypted, "
            "meta_version, descripcion) "
            "VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, 'incomplete', ?, ?, ?, ?)",
            (item_id, account_id, channel_id, parent_id, name, path, size, mime, sha256,
             ahora, ahora, num_parts, 1 if encrypted else 0, META_VERSION, descripcion or ""))
        return self.obtener_item(item_id)

    def establecer_descripcion(self, item_id: str, descripcion: str) -> bool:
        """Cambia la descripción de un elemento (carpeta o archivo)."""
        it = self.obtener_item(item_id)
        if not it:
            return False
        db.execute("UPDATE tgdrive_items SET descripcion = ?, modified_at = ? WHERE id = ?",
                   (descripcion or "", _ahora(), item_id))
        return True

    def registrar_parte(self, item_id: str, orden: int, size: int, sha256: Optional[str],
                        message_id: Optional[int], tg_file_id: Optional[str],
                        tg_unique_id: Optional[str], estado: str = "uploaded"):
        """Guarda (o actualiza) una parte ya subida de un archivo."""
        db.execute(
            "INSERT INTO tgdrive_parts (item_id, orden, size, sha256, message_id, tg_file_id, "
            "tg_unique_id, estado) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(item_id, orden) DO UPDATE SET size=excluded.size, "
            "sha256=excluded.sha256, message_id=excluded.message_id, "
            "tg_file_id=excluded.tg_file_id, tg_unique_id=excluded.tg_unique_id, "
            "estado=excluded.estado",
            (item_id, orden, size, sha256, message_id, tg_file_id, tg_unique_id, estado))

    def finalizar_archivo(self, item_id: str):
        """Marca un archivo como COMPLETO (normal) SOLO si todas sus partes están subidas.
        Atómico: si falta alguna parte, no cambia el estado (queda incompleto)."""
        it = self.obtener_item(item_id)
        if not it:
            return False
        subidas = (db.query_one(
            "SELECT COUNT(*) FROM tgdrive_parts WHERE item_id = ? AND estado = 'uploaded'",
            (item_id,)) or [0])[0]
        if subidas < it.num_parts:
            return False
        db.execute("UPDATE tgdrive_items SET estado = 'normal', modified_at = ? WHERE id = ?",
                   (_ahora(), item_id))
        return True

    def renombrar(self, item_id: str, nuevo_nombre: str) -> bool:
        """Cambia el nombre de un elemento y recalcula su ruta (y la de sus descendientes)."""
        it = self.obtener_item(item_id)
        if not it:
            return False
        db.execute("UPDATE tgdrive_items SET name = ?, modified_at = ? WHERE id = ?",
                   (nuevo_nombre, _ahora(), item_id))
        self._recalcular_paths_descendientes(item_id)
        return True

    def es_descendiente(self, posible_ancestro: str, item_id: str) -> bool:
        """True si `item_id` es el mismo o un descendiente de `posible_ancestro` (para
        impedir mover una carpeta dentro de sí misma o de una hija: crearía un ciclo)."""
        actual = item_id
        visto = set()
        while actual is not None and actual not in visto:
            if actual == posible_ancestro:
                return True
            visto.add(actual)
            r = db.query_one("SELECT parent_id FROM tgdrive_items WHERE id = ?", (actual,))
            actual = r[0] if r else None
        return False

    def mover(self, item_id: str, nuevo_parent_id: Optional[str]) -> bool:
        """Mueve un elemento a otra carpeta. Rechaza el movimiento si crearía un CICLO
        (mover una carpeta dentro de sí misma o de un descendiente)."""
        it = self.obtener_item(item_id)
        if not it:
            return False
        if nuevo_parent_id is not None:
            if nuevo_parent_id == item_id or self.es_descendiente(item_id, nuevo_parent_id):
                raise ValueError("No se puede mover una carpeta dentro de sí misma.")
            destino = self.obtener_item(nuevo_parent_id)
            if not destino or not destino.es_carpeta:
                raise ValueError("El destino no es una carpeta.")
        db.execute("UPDATE tgdrive_items SET parent_id = ?, modified_at = ? WHERE id = ?",
                   (nuevo_parent_id, _ahora(), item_id))
        self._recalcular_paths_descendientes(item_id)
        return True

    def copiar(self, item_id: str, nuevo_parent_id: Optional[str],
               nuevo_nombre: Optional[str] = None) -> Optional[ItemNube]:
        """Copia un elemento (y su subárbol si es carpeta) a otra carpeta. Los archivos
        REUTILIZAN los mensajes de Telegram ya subidos (las partes apuntan a los mismos
        message_id/file_id): copiar no re-sube bytes. Devuelve el nuevo elemento raíz."""
        it = self.obtener_item(item_id)
        if not it:
            return None
        nombre = nuevo_nombre or it.name
        nombre = self.nombre_disponible(it.account_id, nuevo_parent_id, nombre)
        if it.es_carpeta:
            nueva = self.crear_carpeta(it.account_id, nuevo_parent_id, nombre, it.channel_id,
                                       it.descripcion)
            for hijo in self.listar(it.account_id, item_id, ESTADO_NORMAL):
                self.copiar(hijo.id, nueva.id, None)
            return nueva
        # Archivo: nueva fila con las MISMAS partes (referencia a los mismos mensajes).
        nuevo_id = _nuevo_uuid()
        ahora = _ahora()
        path = self._path_de(it.account_id, nuevo_parent_id, nombre)
        db.execute(
            "INSERT INTO tgdrive_items (id, account_id, channel_id, parent_id, name, tipo, "
            "path, size, mime, sha256, created_at, modified_at, estado, num_parts, encrypted, "
            "meta_version, descripcion) "
            "VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?, ?)",
            (nuevo_id, it.account_id, it.channel_id, nuevo_parent_id, nombre, path, it.size,
             it.mime, it.sha256, ahora, ahora, it.num_parts, 1 if it.encrypted else 0,
             META_VERSION, it.descripcion or ""))
        for p in self.obtener_partes(item_id):
            self.registrar_parte(nuevo_id, p.orden, p.size, p.sha256, p.message_id,
                                 p.tg_file_id, p.tg_unique_id, p.estado)
        return self.obtener_item(nuevo_id)

    # ---------------------------------------------------------------- papelera
    def a_papelera(self, item_id: str) -> bool:
        """Envía un elemento (y su subárbol) a la papelera, recordando su ubicación original
        para poder restaurarlo. Los descendientes quedan marcados como papelera también, pero
        conservan su parent_id (para reconstruir el árbol al restaurar)."""
        it = self.obtener_item(item_id)
        if not it or it.estado != ESTADO_NORMAL:
            return False
        ahora = _ahora()
        db.execute(
            "UPDATE tgdrive_items SET estado = 'trash', trashed_at = ?, "
            "original_parent_id = parent_id, original_path = path WHERE id = ?",
            (ahora, item_id))
        # Descendientes: a papelera SIN marcar original_parent (cuelgan del raíz papeleado).
        self._marcar_subarbol(item_id, ESTADO_PAPELERA, ahora)
        return True

    def _marcar_subarbol(self, item_id: str, estado: str, ts: str):
        for (hijo_id,) in db.query("SELECT id FROM tgdrive_items WHERE parent_id = ?", (item_id,)):
            db.execute("UPDATE tgdrive_items SET estado = ?, trashed_at = ? WHERE id = ?",
                       (estado, ts, hijo_id))
            self._marcar_subarbol(hijo_id, estado, ts)

    def restaurar(self, item_id: str) -> bool:
        """Restaura un elemento de la papelera a su ubicación original (o a la raíz si el
        padre original ya no existe). Restaura también su subárbol."""
        it = self.obtener_item(item_id)
        if not it or it.estado != ESTADO_PAPELERA:
            return False
        destino = it.original_parent_id
        if destino is not None and self.obtener_item(destino) is None:
            destino = None  # el padre ya no existe: restaurar a la raíz
        # Evitar colisión de nombre en el destino.
        nombre = self.nombre_disponible(it.account_id, destino, it.name)
        db.execute("UPDATE tgdrive_items SET estado = 'normal', parent_id = ?, name = ?, "
                   "trashed_at = NULL, original_parent_id = NULL, original_path = NULL "
                   "WHERE id = ?", (destino, nombre, item_id))
        self._recalcular_paths_descendientes(item_id)
        self._marcar_subarbol(item_id, ESTADO_NORMAL, None)
        return True

    def marcar_eliminado(self, item_id: str):
        """Marca un elemento como eliminado definitivo (se quitará de Telegram y luego de la
        base). Estado intermedio para no perder los message_id antes de borrarlos."""
        db.execute("UPDATE tgdrive_items SET estado = 'deleted' WHERE id = ?", (item_id,))
        self._marcar_subarbol(item_id, ESTADO_ELIMINADO, _ahora())

    def borrar_definitivo(self, item_id: str):
        """Borra el elemento y su subárbol de la base (las partes caen por ON DELETE CASCADE)."""
        db.execute("DELETE FROM tgdrive_items WHERE id = ?", (item_id,))

    def mensajes_de_subarbol(self, item_id: str) -> List[Dict[str, Any]]:
        """Devuelve los (channel_id, message_id) de TODOS los archivos del subárbol, para
        poder borrarlos de Telegram. Incluye el propio elemento si es archivo."""
        salida = []
        it = self.obtener_item(item_id)
        if not it:
            return salida
        if it.es_archivo:
            for p in self.obtener_partes(item_id):
                if p.message_id:
                    salida.append({"channel_id": it.channel_id, "message_id": p.message_id})
        for hijo in db.query("SELECT id FROM tgdrive_items WHERE parent_id = ?", (item_id,)):
            salida.extend(self.mensajes_de_subarbol(hijo[0]))
        return salida

    def message_referenciado_vivo(self, channel_id: Optional[int], message_id: int) -> bool:
        """True si ALGÚN elemento NO marcado como eliminado ('deleted') todavía referencia ese mensaje
        de Telegram (TG-004). Al deduplicar, una copia reutiliza los mismos message_id; si se borra el
        mensaje remoto mientras otra copia lo referencia, esa copia se rompe. Por eso, antes de borrar
        en Telegram, se comprueba el refcount: si hay otra referencia viva, NO se borra el mensaje.

        Debe llamarse DESPUÉS de marcar_eliminado() del subárbol que se está borrando (así sus filas ya
        están en estado 'deleted' y no cuentan como referencia viva). Se es conservador: ante cualquier
        duda con channel_id NULL se considera referenciado (mejor dejar un mensaje huérfano que romper
        una copia)."""
        fila = db.query_one(
            "SELECT COUNT(*) FROM tgdrive_parts p JOIN tgdrive_items i ON i.id = p.item_id "
            "WHERE p.message_id = ? AND i.estado <> 'deleted' "
            "AND (i.channel_id = ? OR i.channel_id IS NULL OR ? IS NULL)",
            (message_id, channel_id, channel_id))
        return bool(fila and fila[0] > 0)

    def registrar_eliminacion_pendiente(self, account_id: int, channel_id: Optional[int],
                                        message_id: int):
        """Anota un mensaje cuya eliminación en Telegram falló, para reintentarla luego."""
        db.execute(
            "INSERT INTO tgdrive_pending_deletes (account_id, channel_id, message_id, created_at) "
            "VALUES (?, ?, ?, ?)", (account_id, channel_id, message_id, _ahora()))

    def eliminaciones_pendientes(self, account_id: int) -> List[Dict[str, Any]]:
        filas = db.query(
            "SELECT id, channel_id, message_id FROM tgdrive_pending_deletes WHERE account_id = ?",
            (account_id,))
        return [{"id": r[0], "channel_id": r[1], "message_id": r[2]} for r in filas]

    def quitar_eliminacion_pendiente(self, pend_id: int):
        db.execute("DELETE FROM tgdrive_pending_deletes WHERE id = ?", (pend_id,))

    # ---------------------------------------------------------------- estadísticas
    def contar(self, account_id: int) -> Dict[str, int]:
        """Cuenta carpetas, archivos, bytes y elementos en papelera de una cuenta."""
        def _u(sql, params):
            return (db.query_one(sql, params) or [0])[0] or 0
        return {
            "carpetas": _u("SELECT COUNT(*) FROM tgdrive_items WHERE account_id=? "
                           "AND tipo='folder' AND estado='normal'", (account_id,)),
            "archivos": _u("SELECT COUNT(*) FROM tgdrive_items WHERE account_id=? "
                           "AND tipo='file' AND estado='normal'", (account_id,)),
            "bytes": _u("SELECT COALESCE(SUM(size),0) FROM tgdrive_items WHERE account_id=? "
                        "AND tipo='file' AND estado='normal'", (account_id,)),
            "papelera": _u("SELECT COUNT(*) FROM tgdrive_items WHERE account_id=? "
                           "AND estado='trash'", (account_id,)),
        }


# Instancia compartida del repositorio.
tgdrive_repo = TgDriveRepo()
