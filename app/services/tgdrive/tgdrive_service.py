# -*- coding: utf-8 -*-
"""Servicio de alto nivel de Telegram Drive: orquesta persistencia + almacenamiento remoto.

Reúne el repositorio SQLite, el backend de Telegram, el troceado/reconstrucción, el cálculo
de hash y la cola de transferencias. La interfaz habla SOLO con este servicio. No actualiza
widgets de wxPython: notifica el progreso mediante callbacks que la interfaz envuelve en
wx.CallAfter.
"""
import json
import os
import threading
import time as _t
import uuid
from datetime import datetime
from typing import Optional, Callable, List, Dict, Any

from app.core.database import db
from app.core.config_manager import config
from app.core.paths import get_data_path

from .tgdrive_repo import tgdrive_repo
from .tgdrive_models import (ItemNube, TIPO_ARCHIVO, ESTADO_NORMAL, ESTADO_INCOMPLETO,
                             TransferenciaCancelada,
                             TR_PENDIENTE, TR_PREPARANDO, TR_HASH, TR_SUBIENDO, TR_DESCARGANDO,
                             TR_RECONSTRUYENDO, TR_VERIFICANDO, TR_CANCELADA, TR_COMPLETADA,
                             TR_FALLIDA)
from . import tgdrive_split as split
from . import tgdrive_hash as hasher
from . import tgdrive_manifest as manifest
from .tgdrive_cola import _ColaTransferenciasMixin

import builtins as _bi
if not hasattr(_bi, "_"):
    _bi._ = lambda s: s

import logging
log = logging.getLogger(__name__)

# Título por defecto del canal privado de almacenamiento.
TITULO_CANAL = "SocialRSS Telegram Drive"


class TgDriveService(_ColaTransferenciasMixin):
    """Servicio principal de la nube. Se inyecta un backend (Telegram real o un doble)."""

    def __init__(self, backend=None, repo=None):
        self.repo = repo or tgdrive_repo
        self._backend = backend
        self._lock = threading.Lock()
        self._worker = None
        self._cancelados = set()          # ids de transferencias canceladas en vuelo
        self._on_update = None            # callback de la UI para refrescar (opcional)

    # ------------------------------------------------------------- backend/canal
    def _get_backend(self):
        """Devuelve el backend; si no se inyectó uno, usa el de Telegram real (perezoso)."""
        if self._backend is None:
            from app.services.telegram.telegram_service import telegram_service
            from .tgdrive_backend import BackendTelegram
            self._backend = BackendTelegram(telegram_service)
        return self._backend

    def set_backend(self, backend):
        self._backend = backend

    def set_callback_ui(self, cb: Optional[Callable]):
        """Registra un callback (sin args) que la UI usa para refrescar la lista de
        transferencias. El servicio lo invoca con wx.CallAfter si wx está disponible."""
        self._on_update = cb

    def _notificar_ui(self):
        if self._on_update is None:
            return
        try:
            import wx
            wx.CallAfter(self._on_update)
        except Exception:
            try:
                self._on_update()
            except Exception:
                pass

    def obtener_channel_id(self, account_id: int) -> Optional[int]:
        """Canal de almacenamiento configurado para una cuenta, o None."""
        cfg = config.get("telegram_drive", str(account_id), {}) or {}
        cid = cfg.get("channel_id") if isinstance(cfg, dict) else None
        return int(cid) if cid else None

    def guardar_channel_id(self, account_id: int, channel_id: int, titulo: str = ""):
        cfg = config.get("telegram_drive", str(account_id), {}) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg.update({"channel_id": int(channel_id), "titulo": titulo or TITULO_CANAL})
        config.set("telegram_drive", str(account_id), cfg)

    def _manifest_msg_id(self, account_id: int) -> Optional[int]:
        cfg = config.get("telegram_drive", str(account_id), {}) or {}
        mid = cfg.get("manifest_msg_id") if isinstance(cfg, dict) else None
        return int(mid) if mid else None

    def _guardar_manifest_msg_id(self, account_id: int, mid: Optional[int]):
        cfg = config.get("telegram_drive", str(account_id), {}) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["manifest_msg_id"] = int(mid) if mid else None
        config.set("telegram_drive", str(account_id), cfg)

    def crear_canal(self, account_id: int, titulo: str = TITULO_CANAL) -> int:
        """Crea el canal privado de almacenamiento y lo recuerda para la cuenta."""
        channel_id = self._get_backend().crear_canal(account_id, titulo)
        self.guardar_channel_id(account_id, channel_id, titulo)
        return channel_id

    def _carpeta_temp(self) -> str:
        ruta = get_data_path("temp") / "tgdrive"
        ruta.mkdir(parents=True, exist_ok=True)
        return str(ruta)

    def carpeta_descargas(self) -> str:
        """Carpeta local de descargas de la nube (configurable)."""
        personal = config.get("telegram_drive", "carpeta_descargas", "")
        if personal and os.path.isdir(personal):
            return personal
        ruta = get_data_path("downloads") / "tgdrive"
        ruta.mkdir(parents=True, exist_ok=True)
        return str(ruta)

    # ------------------------------------------------------------- subida
    def subir_archivo(self, account_id: int, parent_id: Optional[str], ruta_local: str,
                      nombre: Optional[str] = None, reusar_duplicado: bool = True,
                      on_progress: Optional[Callable[[int, int], None]] = None,
                      cancelado: Optional[Callable[[], bool]] = None,
                      descripcion: str = "", reanudar_item_id: Optional[str] = None,
                      on_item: Optional[Callable[[str], None]] = None) -> Optional[ItemNube]:
        """Sube un archivo local a la nube: calcula su hash, deduplica si procede, lo divide
        en partes si supera el límite, sube cada parte y solo entonces lo marca COMPLETO
        (atómico). Devuelve el ItemNube, o None si se canceló.

        REANUDACIÓN: si `reanudar_item_id` apunta a un archivo INCOMPLETO cuyo contenido coincide
        (mismo SHA y tamaño), NO se empieza de cero: se saltan las partes ya subidas al canal y
        solo se suben las que faltan. `on_item(item_id)` se llama en cuanto se conoce el item (para
        que la cola lo enlace a la transferencia y pueda reanudar tras cerrar la app)."""
        backend = self._get_backend()
        channel_id = self.obtener_channel_id(account_id)
        if channel_id is None:
            raise ValueError(_("No hay un canal de almacenamiento configurado."))
        if not os.path.isfile(ruta_local):
            raise FileNotFoundError(ruta_local)
        nombre = nombre or os.path.basename(ruta_local)
        size = os.path.getsize(ruta_local)
        mime = self._adivinar_mime(nombre)

        def _cancel():
            return bool(cancelado and cancelado())

        # 1) Hash del contenido completo (por bloques).
        sha = hasher.sha256_archivo(ruta_local, cancelado=_cancel)
        if sha is None:
            return None

        limite = backend.limite_tamano(account_id)
        dividir = split.hace_falta_dividir(size, limite)
        num_parts = (max(1, -(-size // limite)) if dividir else 1)

        # 2) ¿Reanudar un incompleto anterior (mismo contenido)? Si el candidato no cuadra (el
        # archivo cambió), se descarta con sus mensajes huérfanos y se empieza de cero.
        item = None
        if reanudar_item_id:
            cand = self.repo.obtener_item(reanudar_item_id)
            if cand and cand.estado == ESTADO_INCOMPLETO:
                if cand.sha256 == sha and cand.size == size:
                    item = cand
                    log.info("Drive: reanudando subida de %s (partes ya subidas: %d).", nombre,
                             sum(1 for p in self.repo.obtener_partes(item.id) if p.estado == "uploaded"))
                else:
                    self._cancelar_incompleto_con_canal(account_id, cand.id)

        # 3) Deduplicación (solo si NO estamos reanudando): si ya existe el mismo contenido
        # COMPLETO, referenciar sus partes en vez de volver a subir.
        if item is None and reusar_duplicado:
            gemelo = self.repo.buscar_por_sha256(account_id, sha)
            if gemelo is not None:
                copia = self.repo.copiar(gemelo.id, parent_id,
                                         self.repo.nombre_disponible(account_id, parent_id, nombre))
                if descripcion and copia is not None:
                    self.repo.establecer_descripcion(copia.id, descripcion)
                    copia = self.repo.obtener_item(copia.id)
                if on_progress:
                    on_progress(size, size)
                return copia

        # 4) Fila de archivo INCOMPLETO (nueva o la que se reanuda).
        if item is None:
            item = self.repo.crear_archivo_incompleto(account_id, parent_id, nombre, size, mime,
                                                       sha, num_parts, channel_id,
                                                       descripcion=descripcion or "")
        if on_item:
            try:
                on_item(item.id)   # la cola enlaza el item a la transferencia (para reanudar)
            except Exception:
                pass

        # Partes ya subidas (para saltarlas al reanudar).
        subidas = {p.orden for p in self.repo.obtener_partes(item.id) if p.estado == "uploaded"}
        subido_total = [min(size, len(subidas) * limite) if dividir else 0]

        def _prog_parte(cur, tot, base):
            if on_progress:
                on_progress(min(size, base + cur), size)

        try:
            if not dividir:
                if 0 not in subidas:
                    if _cancel():
                        self._limpiar_item_incompleto(item.id)
                        return None
                    info = backend.subir(account_id, channel_id, ruta_local,
                                         caption=self._caption(item.id, 0),
                                         progreso=lambda c, t: _prog_parte(c, t, 0),
                                         cancelado=_cancel)
                    self.repo.registrar_parte(item.id, 0, size, sha, info.get("message_id"),
                                              info.get("file_id"), info.get("unique_id"), "uploaded")
            else:
                for orden in range(num_parts):
                    if orden in subidas:
                        continue   # ya estaba subida: reanudar sin repetir
                    if _cancel():
                        self._cancelar_incompleto_con_canal(account_id, item.id)
                        return None
                    parte = split.extraer_parte(ruta_local, orden, limite, self._carpeta_temp(),
                                                cancelado=_cancel)
                    if parte is None:
                        return None   # cancelado durante la extracción (se conserva para reanudar)
                    base = subido_total[0]
                    try:
                        info = backend.subir(account_id, channel_id, parte["ruta"],
                                             caption=self._caption(item.id, orden),
                                             progreso=lambda c, t, b=base: _prog_parte(c, t, b),
                                             cancelado=_cancel)
                    finally:
                        split._borrar_si_existe(parte["ruta"])
                    self.repo.registrar_parte(item.id, orden, parte["size"], parte["sha256"],
                                              info.get("message_id"), info.get("file_id"),
                                              info.get("unique_id"), "uploaded")
                    subido_total[0] += parte["size"]
            # 5) Finalizar de forma ATÓMICA (solo si todas las partes están subidas).
            if not self.repo.finalizar_archivo(item.id):
                raise RuntimeError(_("La subida quedó incompleta."))
            if on_progress:
                on_progress(size, size)
            return self.repo.obtener_item(item.id)
        except TransferenciaCancelada:
            # Cancelación explícita en mitad de la subida: quitar el incompleto y sus mensajes.
            self._cancelar_incompleto_con_canal(account_id, item.id)
            return None
        except Exception:
            # Fallo (red, etc.): NO se borra el incompleto; sus partes subidas se CONSERVAN para
            # poder REANUDAR en el siguiente intento sin empezar de cero.
            log.exception("Fallo al subir %s (se conserva lo subido para reanudar)", nombre)
            raise

    def _cancelar_incompleto_con_canal(self, account_id: int, item_id: str):
        """Descarta un archivo incompleto Y borra del canal los mensajes de sus partes ya subidas
        (para no dejar basura al cancelar o al descartar un incompleto que no cuadra)."""
        try:
            mensajes = self.repo.mensajes_de_subarbol(item_id)
            por_canal = {}
            for m in mensajes:
                if m.get("message_id"):
                    por_canal.setdefault(m.get("channel_id"), []).append(m["message_id"])
            backend = self._get_backend()
            for canal, ids in por_canal.items():
                if canal is None:
                    continue
                try:
                    backend.borrar_mensajes(account_id, canal, ids)
                except Exception as e:
                    log.debug("Drive: no se pudieron borrar mensajes del incompleto: %s", e)
        except Exception as e:
            log.debug("Drive: fallo al limpiar el incompleto en el canal: %s", e)
        self._limpiar_item_incompleto(item_id)

    def _limpiar_item_incompleto(self, item_id: str):
        """Borra un item incompleto y sus partes (no toca Telegram: las partes subidas se
        limpian aparte si es necesario). Se usa al cancelar/fallar."""
        try:
            self.repo.borrar_definitivo(item_id)
        except Exception as e:
            log.debug("No se pudo limpiar el item incompleto %s: %s", item_id, e)

    # ------------------------------------------------------------- descarga
    def descargar_item(self, account_id: int, item_id: str, carpeta_destino: Optional[str] = None,
                       on_progress: Optional[Callable[[int, int], None]] = None,
                       cancelado: Optional[Callable[[], bool]] = None) -> Optional[str]:
        """Descarga un archivo de la nube: baja cada parte, las reconstruye en orden, verifica
        el SHA-256 final y borra el reconstruido si no cuadra. Devuelve la ruta final o None
        si se canceló."""
        item = self.repo.obtener_item(item_id)
        if not item or item.es_carpeta:
            raise ValueError(_("Solo se pueden descargar archivos."))
        backend = self._get_backend()
        destino_dir = carpeta_destino or self.carpeta_descargas()
        os.makedirs(destino_dir, exist_ok=True)
        partes = self.repo.obtener_partes(item_id)
        if not partes:
            raise ValueError(_("El archivo no tiene partes registradas."))

        def _cancel():
            return bool(cancelado and cancelado())

        # SEGURIDAD (TG-005): el nombre del archivo puede venir de un manifiesto NO confiable con
        # '..', ruta absoluta o nombres reservados. Se confina SIEMPRE dentro de la carpeta elegida.
        destino_final = self._destino_no_colisiona(self._ruta_confinada(destino_dir, item.name))
        temp = self._carpeta_temp()
        rutas_parte = []
        base_bytes = [0]
        try:
            if len(partes) == 1:
                p = partes[0]
                ruta = backend.descargar(account_id, item.channel_id, p.message_id,
                                         destino_final, file_id=p.tg_file_id,
                                         progreso=(lambda c, t: on_progress(c, t)) if on_progress else None,
                                         cancelado=_cancel)
                if _cancel():
                    split._borrar_si_existe(ruta)
                    return None
                # Verificar integridad.
                if item.sha256:
                    real = hasher.sha256_archivo(ruta, cancelado=_cancel)
                    if real is None:
                        split._borrar_si_existe(ruta)
                        return None
                    if real != item.sha256:
                        split._borrar_si_existe(ruta)
                        raise ValueError(_("La verificación de integridad falló: el archivo está dañado."))
                return ruta
            # Varias partes: descargar cada una a un temporal, luego reconstruir.
            for p in partes:
                if _cancel():
                    split._borrar_partes([{"ruta": r} for r in rutas_parte])
                    return None
                ruta_p = os.path.join(temp, f"dl_{item.id}.part{p.orden:04d}")
                base = base_bytes[0]
                backend.descargar(account_id, item.channel_id, p.message_id, ruta_p,
                                  file_id=p.tg_file_id,
                                  progreso=(lambda c, t, b=base: on_progress(b + c, item.size)) if on_progress else None,
                                  cancelado=_cancel)
                rutas_parte.append(ruta_p)
                base_bytes[0] += p.size
            sha_final = split.reconstruir(rutas_parte, destino_final, cancelado=_cancel)
            if sha_final is None:
                return None
            if item.sha256 and sha_final != item.sha256:
                split._borrar_si_existe(destino_final)
                raise ValueError(_("La verificación de integridad falló: el archivo está dañado."))
            return destino_final
        except TransferenciaCancelada:
            # Cancelación en mitad de la descarga: limpiar el destino a medias y devolver None.
            split._borrar_si_existe(destino_final)
            return None
        finally:
            split._borrar_partes([{"ruta": r} for r in rutas_parte])

    @staticmethod
    def _nombre_archivo_seguro(nombre: str) -> str:
        """Basename saneado y confinable: sin separadores ni rutas, sin nombres reservados de Windows
        ni caracteres inválidos. Neutraliza el path traversal de un manifiesto malicioso (TG-005)."""
        import re
        n = os.path.basename((nombre or "").replace("\\", "/").rstrip("/. "))
        n = n.split("/")[-1]
        n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", n).strip().rstrip(". ")
        if not n or n in (".", ".."):
            return "archivo"
        reservados = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6",
                      "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6",
                      "LPT7", "LPT8", "LPT9"}
        if os.path.splitext(n)[0].upper() in reservados:
            n = "_" + n
        return n[:200]

    def _ruta_confinada(self, destino_dir: str, nombre: str) -> str:
        """Ruta de destino SEGURA dentro de destino_dir para un nombre no confiable: toma el basename
        saneado y verifica que la ruta resuelta no se salga de la carpeta elegida (TG-005)."""
        seguro = self._nombre_archivo_seguro(nombre)
        raiz = os.path.realpath(destino_dir)
        ruta = os.path.realpath(os.path.join(raiz, seguro))
        if raiz != ruta and os.path.commonpath([raiz, ruta]) != raiz:
            raise ValueError(_("Nombre de archivo no permitido (intento de salir de la carpeta)."))
        return ruta

    def _destino_no_colisiona(self, ruta: str) -> str:
        """Evita sobrescribir un archivo local ya existente al descargar."""
        if not os.path.exists(ruta):
            return ruta
        base, ext = os.path.splitext(ruta)
        n = 2
        while os.path.exists(f"{base} ({n}){ext}"):
            n += 1
        return f"{base} ({n}){ext}"

    # ------------------------------------------------------------- borrado remoto
    def eliminar_definitivo(self, account_id: int, item_id: str):
        """Borra un elemento (y su subárbol) de la nube: quita sus mensajes de Telegram y
        luego la fila local. Si Telegram falla, registra la eliminación como pendiente."""
        backend = self._get_backend()
        canal_defecto = self.obtener_channel_id(account_id)
        mensajes = self.repo.mensajes_de_subarbol(item_id)
        self.repo.marcar_eliminado(item_id)
        # Agrupar por canal. Si algún item no guardó su channel_id, se usa el canal configurado
        # de la cuenta (para que el mensaje se borre igualmente).
        por_canal = {}
        for m in mensajes:
            mid = m.get("message_id")
            if not mid:
                continue
            # TG-004: no borrar de Telegram un mensaje que OTRA copia deduplicada aún referencia
            # (rompería esa copia). El subárbol ya está en estado 'deleted' (marcar_eliminado), así
            # que el refcount solo cuenta referencias vivas fuera de lo que se está borrando.
            if self.repo.message_referenciado_vivo(m.get("channel_id"), mid):
                log.info("Drive: mensaje %s conservado en Telegram (aún referenciado por otra copia).",
                         mid)
                continue
            canal = m.get("channel_id") or canal_defecto
            por_canal.setdefault(canal, []).append(mid)
        for canal, ids in por_canal.items():
            if canal is None:
                continue
            try:
                backend.borrar_mensajes(account_id, canal, ids)
            except Exception as e:
                log.warning("Drive: fallo al borrar mensajes; se reintentará. %s", e)
                for mid in ids:
                    self.repo.registrar_eliminacion_pendiente(account_id, canal, mid)
        self.repo.borrar_definitivo(item_id)

    def reintentar_eliminaciones(self, account_id: int) -> int:
        """Reintenta las eliminaciones de mensajes que quedaron pendientes. Devuelve cuántas
        se resolvieron."""
        backend = self._get_backend()
        pendientes = self.repo.eliminaciones_pendientes(account_id)
        resueltas = 0
        por_canal = {}
        for p in pendientes:
            por_canal.setdefault(p["channel_id"], []).append(p)
        for canal, lista in por_canal.items():
            if canal is None:
                continue
            ids = [p["message_id"] for p in lista]
            try:
                backend.borrar_mensajes(account_id, canal, ids)
                for p in lista:
                    self.repo.quitar_eliminacion_pendiente(p["id"])
                    resueltas += 1
            except Exception as e:
                log.debug("Drive: sigue fallando la eliminación pendiente: %s", e)
        return resueltas

    def vaciar_papelera(self, account_id: int) -> int:
        """Elimina definitivamente todos los elementos de la papelera. Devuelve cuántos."""
        n = 0
        for it in self.repo.listar_papelera(account_id):
            self.eliminar_definitivo(account_id, it.id)
            n += 1
        return n

    def purgar_papelera_caducada(self, account_id: int, dias: Optional[int] = None) -> int:
        """Aplica la retención de la papelera: elimina definitivamente los elementos que llevan
        más de `dias` días en ella (0 = no caduca nunca). Devuelve cuántos se purgaron. Best-effort:
        si Telegram falla en alguno, su eliminación queda pendiente de reintento."""
        if dias is None:
            try:
                dias = int(config.get("telegram_drive", "retencion_papelera_dias", 30))
            except (TypeError, ValueError):
                dias = 30
        n = 0
        for it in self.repo.papelera_caducada(account_id, dias):
            try:
                self.eliminar_definitivo(account_id, it.id)
                n += 1
            except Exception as e:
                log.warning("Drive: no se pudo purgar el elemento caducado %s: %s", it.id, e)
        if n:
            log.info("Drive: purgados %d elementos caducados de la papelera.", n)
        return n

    def revisar_canal(self, account_id: int) -> Dict[str, Any]:
        """Cruza el CANAL con el ÍNDICE local y detecta lo que no cuadra:
          - huérfanos: documentos en el canal que no le corresponden a ningún archivo del índice
            (basura de subidas a medias, cancelaciones, etc.). Se pueden borrar del canal.
          - items_rotos: archivos del índice con alguna PARTE cuyo mensaje ya no está en el canal
            (no se podrían descargar enteros). Se pueden quitar del índice.
        No borra nada: solo informa. Devuelve el recuento y las listas."""
        channel_id = self.obtener_channel_id(account_id)
        if channel_id is None:
            raise ValueError(_("No hay un canal configurado para revisar."))
        backend = self._get_backend()
        docs = backend.listar_documentos_canal(account_id, channel_id) or []
        ids_canal = {d["message_id"] for d in docs if d.get("message_id")}
        # Mensajes que el índice espera encontrar en el canal (partes de items vivos).
        referenciados = {}
        for r in db.query(
                "SELECT p.message_id, p.item_id FROM tgdrive_parts p "
                "JOIN tgdrive_items i ON i.id = p.item_id "
                "WHERE i.account_id = ? AND i.estado IN ('normal','incomplete','trash') "
                "AND p.message_id IS NOT NULL", (account_id,)):
            referenciados[r[0]] = r[1]
        ref_ids = set(referenciados.keys())
        # Manifiestos: en el canal debe quedar SOLO el más reciente (mayor message_id). Los
        # demás son basura acumulada de subidas anteriores y se pueden borrar.
        manifiestos = sorted(d["message_id"] for d in docs
                             if d.get("message_id") and d.get("file_name") == manifest.NOMBRE_MANIFIESTO)
        manifiestos_viejos = manifiestos[:-1]   # todos menos el más nuevo
        # Huérfanos: documentos no referenciados y que NO son el manifiesto vigente, más los
        # manifiestos viejos sobrantes.
        orfanos = [d["message_id"] for d in docs
                   if d.get("message_id") and d["message_id"] not in ref_ids
                   and d.get("file_name") != manifest.NOMBRE_MANIFIESTO]
        orfanos += manifiestos_viejos
        # Faltantes: partes referenciadas cuyo mensaje ya no está en el canal.
        faltantes = [mid for mid in ref_ids if mid not in ids_canal]
        items_rotos = sorted({referenciados[mid] for mid in faltantes})
        return {
            "total_canal": len(docs),
            "referenciados": len(ref_ids),
            "orfanos": orfanos,
            "faltantes": faltantes,
            "items_rotos": items_rotos,
        }

    def listar_canal_detallado(self, account_id: int) -> List[Dict[str, Any]]:
        """Lista TODOS los documentos del canal con su relación con el índice, para el diálogo
        avanzado de purga. Cada elemento: {message_id, file_name, es_manifiesto, referenciado,
        item (nombre del archivo del índice al que pertenece, si lo hay)}."""
        channel_id = self.obtener_channel_id(account_id)
        if channel_id is None:
            raise ValueError(_("No hay un canal configurado."))
        docs = self._get_backend().listar_documentos_canal(account_id, channel_id) or []
        ref = {}
        for r in db.query("SELECT p.message_id, i.name FROM tgdrive_parts p "
                          "JOIN tgdrive_items i ON i.id = p.item_id "
                          "WHERE i.account_id = ? AND p.message_id IS NOT NULL", (account_id,)):
            ref[r[0]] = r[1]
        salida = []
        for d in docs:
            mid = d.get("message_id")
            salida.append({
                "message_id": mid,
                "file_name": d.get("file_name", "") or "",
                "es_manifiesto": d.get("file_name") == manifest.NOMBRE_MANIFIESTO,
                "referenciado": mid in ref,
                "item": ref.get(mid, ""),
            })
        return salida

    def purgar_mensajes_canal(self, account_id: int, message_ids: List[int]) -> int:
        """AVANZADO: borra DIRECTAMENTE del canal los mensajes indicados (sin papelera) y limpia
        del índice local los archivos que dependieran de ellos (quedarían rotos). Devuelve cuántos
        mensajes se pidieron borrar."""
        if not message_ids:
            return 0
        channel_id = self.obtener_channel_id(account_id)
        if channel_id is None:
            return 0
        ids = [int(m) for m in message_ids]
        # 1) Quitar del ÍNDICE local los items cuyas partes apuntan a esos mensajes (evita dejar
        #    archivos rotos que no se podrían descargar).
        afectados = set()
        for j in range(0, len(ids), 900):
            trozo = ids[j:j + 900]
            marcadores = ",".join("?" * len(trozo))
            for r in db.query("SELECT DISTINCT item_id FROM tgdrive_parts WHERE message_id IN (%s)"
                              % marcadores, tuple(trozo)):
                afectados.add(r[0])
        for iid in afectados:
            self._limpiar_item_incompleto(iid)   # borra el item y sus partes del índice local
        # 2) Si se purga el manifiesto vigente, olvidarlo (se recreará al exportar).
        if self._manifest_msg_id(account_id) in ids:
            self._guardar_manifest_msg_id(account_id, None)
        # 3) Borrar los mensajes del canal.
        self._get_backend().borrar_mensajes(account_id, channel_id, ids)
        log.info("Drive: purga avanzada, %d mensajes borrados del canal.", len(ids))
        return len(ids)

    def limpiar_orfanos_canal(self, account_id: int, message_ids: List[int]) -> int:
        """Borra del canal los mensajes huérfanos indicados (los que revisar_canal detectó como
        que no le corresponden a ningún archivo). Devuelve cuántos se borraron."""
        if not message_ids:
            return 0
        channel_id = self.obtener_channel_id(account_id)
        if channel_id is None:
            return 0
        self._get_backend().borrar_mensajes(account_id, channel_id, list(message_ids))
        log.info("Drive: borrados %d mensajes huérfanos del canal.", len(message_ids))
        return len(message_ids)

    def quitar_items_rotos(self, account_id: int, item_ids: List[str]) -> int:
        """Quita del índice los archivos rotos (con partes que faltan en el canal). Devuelve
        cuántos. Best-effort: también borra del canal los mensajes que aún les queden."""
        n = 0
        for iid in item_ids:
            try:
                self._cancelar_incompleto_con_canal(account_id, iid)  # borra restos + fila local
                n += 1
            except Exception as e:
                log.warning("Drive: no se pudo quitar el item roto %s: %s", iid, e)
        return n

    def eliminar_drive_completo(self, account_id: int, borrar_canal: bool = True) -> bool:
        """Elimina POR COMPLETO la nube de una cuenta: borra el canal privado (con TODOS los
        archivos), vacía el índice local (items, partes, transferencias y eliminaciones
        pendientes) y olvida el canal en la configuración. Es IRREVERSIBLE. Devuelve True."""
        channel_id = self.obtener_channel_id(account_id)
        if borrar_canal and channel_id is not None:
            try:
                self._get_backend().borrar_canal(account_id, channel_id)
            except Exception as e:
                # Aun si Telegram falla, se limpia lo local para no dejar un estado a medias;
                # se avisa arriba con la excepción envuelta.
                log.warning("Drive: no se pudo borrar el canal %s: %s", channel_id, e)
                raise
        # Limpiar todo lo local de esta cuenta (las partes primero, por si las claves foráneas
        # en cascada no estuvieran activas en esta conexión).
        db.execute("DELETE FROM tgdrive_parts WHERE item_id IN "
                   "(SELECT id FROM tgdrive_items WHERE account_id = ?)", (account_id,))
        db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM tgdrive_transfers WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM tgdrive_pending_deletes WHERE account_id = ?", (account_id,))
        # Olvidar el canal configurado para la cuenta.
        config.set("telegram_drive", str(account_id), {})
        return True

    # ------------------------------------------------------------- índice remoto
    def exportar_indice(self, account_id: int) -> Optional[int]:
        """Sube al canal una copia recuperable del índice (manifiesto versionado) y BORRA el
        manifiesto anterior, para que en el canal quede SOLO EL MÁS RECIENTE (antes se acumulaba
        uno por cada subida). Devuelve el message_id del nuevo manifiesto, o None."""
        channel_id = self.obtener_channel_id(account_id)
        if channel_id is None:
            return None
        backend = self._get_backend()
        anterior = self._manifest_msg_id(account_id)
        m = manifest.construir_manifiesto(account_id, channel_id)
        datos = manifest.volcar_json(m)
        ruta = os.path.join(self._carpeta_temp(), manifest.NOMBRE_MANIFIESTO)
        with open(ruta, "wb") as fh:
            fh.write(datos)
        try:
            info = backend.subir(account_id, channel_id, ruta, caption="SocialRSS Drive index")
            nuevo = info.get("message_id")
            # Borrar el manifiesto anterior (dejar solo el nuevo).
            if anterior and nuevo and anterior != nuevo:
                try:
                    backend.borrar_mensajes(account_id, channel_id, [anterior])
                except Exception as e:
                    log.debug("Drive: no se pudo borrar el manifiesto anterior: %s", e)
            self._guardar_manifest_msg_id(account_id, nuevo)
            return nuevo
        finally:
            split._borrar_si_existe(ruta)

    def reconstruir_indice(self, account_id: int, channel_id: Optional[int] = None) -> Dict[str, int]:
        """Reconstruye la base local desde el manifiesto guardado en el canal. Útil tras
        reinstalar, borrar la base o usar la cuenta en otro equipo."""
        backend = self._get_backend()
        channel_id = channel_id or self.obtener_channel_id(account_id)
        if channel_id is None:
            # No conocemos el canal (reinstalación, base borrada, otro equipo): intentar
            # LOCALIZARLO por su título entre los chats de la cuenta.
            channel_id = backend.buscar_canal(account_id, TITULO_CANAL)
            if channel_id is None:
                raise ValueError(_(
                    "No hay ningún canal de Telegram Drive en esta cuenta para reconstruir. "
                    "Si es la primera vez, crea un canal nuevo."))
        ref = backend.buscar_manifiesto(account_id, channel_id, manifest.NOMBRE_MANIFIESTO)
        if not ref:
            raise ValueError(_("No se encontró el índice en el canal."))
        ruta = os.path.join(self._carpeta_temp(), "indice_descargado.json")
        try:
            backend.descargar(account_id, channel_id, ref["message_id"], ruta,
                             file_id=ref.get("file_id"))
            with open(ruta, "rb") as fh:
                datos = fh.read()
            m = manifest.cargar_json(datos)
            res = manifest.reconstruir_base(account_id, m)
            self.guardar_channel_id(account_id, channel_id)
            return res
        finally:
            split._borrar_si_existe(ruta)

    # ------------------------------------------------------------- utilidades
    @staticmethod
    def _adivinar_mime(nombre: str) -> str:
        import mimetypes
        return mimetypes.guess_type(nombre)[0] or "application/octet-stream"

    @staticmethod
    def _caption(item_id: str, orden: int) -> str:
        """Cabecera compacta en el mensaje: identifica el archivo y la parte, para poder
        reconstruir el índice escaneando el canal aunque se pierda el manifiesto."""
        return json.dumps({"drive": 1, "item": item_id, "part": orden},
                          separators=(",", ":"))


# Instancia compartida del servicio.
tgdrive_service = TgDriveService()
