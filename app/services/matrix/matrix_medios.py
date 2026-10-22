# -*- coding: utf-8 -*-
"""Medios de Matrix (mxc://) y paginación de historial.

Mixin de MatrixService (tanda M3): subir y enviar adjuntos, descargar a la caché local
y traer mensajes antiguos (room_messages hacia atrás con su token por sala).
"""
import os
import mimetypes

from app.core.paths import get_data_path
from app.services.matrix.matrix_common import (
    Any, Dict, Optional, List, db, log, MatrixError, ahora_iso, ts_a_iso, db_id_sala)

# Tipos de adjunto según el msgtype de la spec (client-server-api, módulo instant_messaging).
_MSGTYPE_POR_MIME = (("image/", "m.image"), ("audio/", "m.audio"), ("video/", "m.video"))

# Extensiones de contenido ACTIVO/ejecutable: un adjunto de una sala o DM NO debe poder ejecutarse
# desde la app con un clic. Estos tipos solo se DESCARGAN (nunca se abren con el programa asociado),
# y siempre se marcan con Mark-of-the-Web para que Windows avise de su procedencia de Internet.
EXT_ACTIVAS = frozenset({
    ".exe", ".msi", ".msp", ".com", ".scr", ".pif", ".bat", ".cmd", ".ps1", ".psm1", ".vbs",
    ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".jar", ".lnk", ".url", ".reg", ".inf",
    ".cpl", ".dll", ".sys", ".gadget", ".application", ".msc", ".ade", ".adp", ".cab", ".chm",
    ".sh", ".py", ".pyw", ".apk", ".appx", ".appimage", ".command", ".scf", ".ws",
})


def es_extension_activa(nombre: str) -> bool:
    """True si el nombre/ruta acaba en una extensión de contenido ACTIVO (ejecutable/script)."""
    return os.path.splitext((nombre or "").strip().rstrip(". "))[1].lower() in EXT_ACTIVAS


def marcar_zona_internet(path: str):
    """Escribe el flujo alternativo NTFS 'Zone.Identifier' (Mark-of-the-Web) marcando el archivo
    como descargado de Internet (ZoneId=3). Así Windows/SmartScreen advierte de su procedencia al
    abrirlo. Best-effort: en sistemas de archivos sin ADS (FAT/exFAT) falla en silencio."""
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as f:
            f.write("[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=about:internet\r\n")
    except Exception:
        pass


def _msgtype_de(path: str) -> tuple:
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "application/octet-stream"
    for prefijo, msgtype in _MSGTYPE_POR_MIME:
        if mime.startswith(prefijo):
            return msgtype, mime
    return "m.file", mime


def crypto_de_media(event) -> str:
    """Si el evento es un adjunto CIFRADO (RoomEncryptedMedia), devuelve un JSON con sus claves
    de descifrado (key/iv/hashes) para poder descargarlo y descifrarlo luego; si es un adjunto en
    claro, devuelve "". Uniforma el formato que consume descargar_media()."""
    import json
    key = getattr(event, "key", None)
    iv = getattr(event, "iv", None)
    hashes = getattr(event, "hashes", None)
    if key and iv and hashes:
        try:
            return json.dumps({"key": key, "iv": iv, "hashes": hashes})
        except Exception:
            return ""
    return ""


class _MediosMixin:
    # ---------------- Envío de adjuntos ----------------
    def enviar_archivo(self, account_id: int, room_id: str, path: str) -> str:
        """Sube un archivo al servidor y lo envía a la sala (imagen/audio/vídeo/archivo según
        su tipo). Bloqueante: llamar desde task_manager. Devuelve el event_id."""
        if not path or not os.path.exists(path):
            raise MatrixError("El archivo no existe.")
        cliente = self._cliente_conectado(account_id)
        msgtype, mime = _msgtype_de(path)
        nombre = os.path.basename(path)
        tamano = os.path.getsize(path)
        # Límite de subida del servidor (M7): avisar ANTES de intentarlo, en palabras.
        limite = self.limite_subida(account_id)
        if limite and tamano > limite:
            raise MatrixError(_("El archivo pesa {0:.1f} MB y este servidor admite como máximo "
                                "{1:.1f} MB.").format(tamano / 1048576, limite / 1048576))

        from nio import UploadResponse, RoomSendResponse

        # ¿La sala está cifrada? Entonces el adjunto se sube CIFRADO y el contenido lleva la clave
        # "file" (url + claves de descifrado) en vez de "url" a secas, como manda la spec de E2EE.
        sala = cliente.rooms.get(room_id)
        cifrada = bool(getattr(sala, "encrypted", False))

        async def _subir_y_enviar():
            # filesize OBLIGATORIO: sin él, nio no fija la cabecera Content-Length y el servidor
            # rechaza la subida con "Request must specify a Content-Length". (Ver upload() en nio:
            # filesize -> content_length -> header Content-Length.)
            with open(path, "rb") as f:
                resp, claves = await cliente.upload(
                    f, content_type=mime, filename=nombre,
                    filesize=tamano, encrypt=cifrada)
            if not isinstance(resp, UploadResponse):
                raise MatrixError(getattr(resp, "message", None) or str(resp))
            contenido = {"msgtype": msgtype, "body": nombre,
                         "info": {"mimetype": mime, "size": tamano}}
            if cifrada and claves:
                # EncryptedFile: las claves que devuelve upload() + la url del contenido subido.
                contenido["file"] = {**claves, "url": resp.content_uri}
            else:
                contenido["url"] = resp.content_uri
            r2 = await cliente.room_send(room_id, "m.room.message", contenido,
                             ignore_unverified_devices=self._ignorar_no_verificados(account_id))
            if not isinstance(r2, RoomSendResponse):
                raise MatrixError(getattr(r2, "message", None) or str(r2))
            # Si fue cifrado, guardamos las claves (key/iv/hashes) para poder descifrar el propio
            # adjunto al reproducirlo luego; en claro, cadena vacía.
            crypto = ""
            if cifrada and claves:
                import json
                crypto = json.dumps({"key": claves.get("key"), "iv": claves.get("iv"),
                                     "hashes": claves.get("hashes")})
            return r2.event_id, resp.content_uri, crypto

        event_id, mxc, crypto = self._run(_subir_y_enviar(), timeout=300.0)
        etiqueta = {"m.image": _("[Imagen: {0}]"), "m.audio": _("[Mensaje de audio: {0}]"),
                    "m.video": _("[Vídeo: {0}]")}.get(msgtype, _("[Archivo: {0}]")).format(nombre)
        self._guardar_mensaje(
            account_id, room_id, "media", sender=self._user_id(account_id),
            sender_nombre=_("Yo"), texto=etiqueta, event_id=event_id, fecha=ahora_iso(),
            propio=True, leido=True, contar=False, media_mxc=mxc, media_crypto=crypto)
        return event_id

    # ---------------- Nota de voz (grabador BASS, patrón Telegram) ----------------
    def iniciar_grabacion_voz(self) -> str:
        """Inicia la grabación del micrófono (dispositivo/volumen compartidos, en Preferencias →
        Sonidos → Micrófono). Devuelve la ruta del WAV temporal."""
        import time as _t
        from app.services.audio import microphone
        ruta = str(get_data_path("temp") / f"voz_mx_{int(_t.time())}.wav")
        dispositivo, volumen = microphone.config_microfono()
        grabador = microphone.GrabadorBass()
        try:
            grabador.iniciar(dispositivo or None, volumen)
        except RuntimeError as e:
            raise MatrixError(str(e))
        self._grabacion_mx = {"grabador": grabador, "ruta": ruta}
        return ruta

    def detener_grabacion_y_enviar(self, account_id: int, room_id: str):
        """Detiene la grabación y la envía a la sala (Opus si hay ffmpeg con libopus; WAV si no)."""
        from app.services.audio import microphone
        grab = getattr(self, "_grabacion_mx", None)
        if not grab:
            raise MatrixError("No hay ninguna grabación en curso.")
        ruta = grab["ruta"]
        self._grabacion_mx = None
        try:
            grab["grabador"].detener_a_wav(ruta)
        except RuntimeError as e:
            raise MatrixError(str(e))
        opus = microphone.convertir_a_opus(ruta)
        return self.enviar_archivo(account_id, room_id, opus or ruta)

    def cancelar_grabacion_voz(self):
        grab = getattr(self, "_grabacion_mx", None)
        if not grab:
            return
        self._grabacion_mx = None
        try:
            grab["grabador"].cancelar()
        except Exception:
            pass

    # ---------------- Descarga a caché ----------------
    def descargar_media(self, account_id: int, mxc: str, nombre: str = "",
                        media_crypto: str = "") -> str:
        """Descarga un mxc:// a la caché local (si no estaba ya) y devuelve la ruta. Si media_crypto
        trae las claves (adjunto de una sala CIFRADA), descifra los bytes antes de guardarlos."""
        if not mxc or not mxc.startswith("mxc://"):
            raise MatrixError("Este mensaje no tiene un adjunto descargable.")
        carpeta = get_data_path("cache/matrix")
        carpeta.mkdir(parents=True, exist_ok=True)
        # Clave de caché por SERVIDOR + media_id (MX-017): dos servidores pueden usar el mismo
        # media_id y colisionarían si la clave fuera solo el media_id. Se sanea (el server lleva ':').
        resto = mxc[len("mxc://"):]
        server = resto.split("/", 1)[0] if "/" in resto else ""
        media_id = resto.rsplit("/", 1)[-1]
        ext = os.path.splitext(nombre or "")[1][:10]
        clave_cache = self._sanear_nombre(f"{server}_{media_id}" if server else media_id)
        destino = carpeta / f"{clave_cache}{ext}"
        if destino.exists() and destino.stat().st_size > 0:
            return str(destino)
        cliente = self._cliente_conectado(account_id)
        from nio import DownloadResponse
        resp = self._run(cliente.download(mxc=mxc, filename=nombre or None), timeout=300.0)
        if not isinstance(resp, DownloadResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        datos = resp.body
        # Adjunto cifrado: descifrar con las claves guardadas (AES-CTR + verificación SHA-256).
        if media_crypto:
            try:
                import json
                from nio.crypto import attachments
                info = json.loads(media_crypto)
                datos = attachments.decrypt_attachment(
                    datos, info["key"]["k"], info["hashes"]["sha256"], info["iv"])
            except Exception as e:
                raise MatrixError(_("No se pudo descifrar el adjunto: {0}").format(e))
        # Tope de tamaño configurable: no guardar adjuntos desmesurados (evita llenar el disco).
        limite = self._limite_descarga_bytes()
        if limite and len(datos) > limite:
            raise MatrixError(_("El adjunto pesa {0:.0f} MB y supera el límite de descarga "
                                "({1:.0f} MB). Puedes subirlo en Preferencias.").format(
                                    len(datos) / 1048576, limite / 1048576))
        # Escritura ATÓMICA: primero a un .part, fsync y luego rename. Así una descarga interrumpida
        # nunca se sirve como válida (antes: cualquier archivo >0 valía, aunque quedara truncado).
        tmp = destino.with_suffix(destino.suffix + ".part")
        try:
            with open(tmp, "wb") as f:
                f.write(datos)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, destino)
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            raise
        # Mark-of-the-Web: el adjunto viene de la red (aunque la sala sea cifrada, el ARCHIVO puede
        # ser hostil). Que Windows lo trate como descargado de Internet.
        marcar_zona_internet(str(destino))
        return str(destino)

    @staticmethod
    def _limite_descarga_bytes() -> int:
        """Tope de tamaño de descarga de adjuntos en bytes (config matrix.max_descarga_mb; 0 = sin
        límite). Por defecto 500 MB, holgado para vídeos pero suficiente para frenar abusos."""
        try:
            from app.core.config_manager import config
            mb = config.get("matrix", "max_descarga_mb", 500)
            return int(mb) * 1048576 if mb and int(mb) > 0 else 0
        except Exception:
            return 500 * 1048576

    @staticmethod
    def _sanear_nombre(nombre: str) -> str:
        """Convierte un texto en un nombre de carpeta/archivo válido en Windows."""
        import re
        nombre = (nombre or "").strip()
        if not nombre:
            return "sin_nombre"
        nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nombre)
        nombre = nombre.rstrip(". ")
        return (nombre[:60] or "sin_nombre")

    def descargar_a_descargas(self, account_id: int, mxc: str, nombre: str,
                              titulo_chat: str, media_crypto: str = "") -> str:
        """Descarga un adjunto a la carpeta de DESCARGAS del usuario, organizada por red y chat:
        data/downloads/matrix/<nombre del chat>/<nombre del archivo>. Reutiliza la caché (y el
        descifrado si la sala es cifrada) y copia con el nombre real. Devuelve la ruta final."""
        import shutil
        ruta_cache = self.descargar_media(account_id, mxc, nombre, media_crypto=media_crypto)
        carpeta = get_data_path("downloads") / "matrix" / self._sanear_nombre(titulo_chat)
        carpeta.mkdir(parents=True, exist_ok=True)
        base, ext = os.path.splitext(self._sanear_nombre(nombre) or "adjunto")
        destino = carpeta / f"{base}{ext}"
        n = 2
        # Anti-colisión: nunca pisar un archivo ya descargado (prueba.txt -> prueba (2).txt).
        while destino.exists():
            destino = carpeta / f"{base} ({n}){ext}"
            n += 1
        shutil.copy2(ruta_cache, destino)
        marcar_zona_internet(str(destino))  # el archivo copiado también lleva su procedencia
        return str(destino)

    # ---------------- Paginación hacia atrás ----------------
    def cargar_anteriores(self, account_id: int, room_id: str, limit: int = 40) -> int:
        """Trae una página de mensajes MÁS ANTIGUOS de la sala (room_messages hacia atrás) y
        los guarda (dedupe por event_id). Devuelve cuántos NUEVOS entraron. El token de
        paginación se recuerda por sala (memoria, patrón cursors de Bluesky)."""
        cliente = self._cliente_conectado(account_id)
        clave = f"{account_id}:{room_id}"
        # Primer salto: el prev_batch REAL de la timeline de ESTA sala (MX-010), capturado del sync.
        # La API de /messages hacia atrás espera el prev_batch de la sala, no el next_batch global;
        # usar el global podía saltar historial o devolver rangos inconsistentes según el homeserver.
        # Reserva: el prev_batch más antiguo que hayamos guardado, y si no, el token de sync global.
        start = (self._page_tokens.get(clave)
                 or self._prev_batch.get(f"{account_id}|{room_id}")
                 or self._leer_next_batch(account_id))
        from nio import RoomMessagesResponse
        resp = self._run(cliente.room_messages(room_id, start=start, limit=limit), timeout=60.0)
        if not isinstance(resp, RoomMessagesResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        self._page_tokens[clave] = getattr(resp, "end", None) or start
        nuevos = 0
        for ev in (resp.chunk or []):
            try:
                if self._guardar_evento_historico(account_id, room_id, ev):
                    nuevos += 1
            except Exception as e:
                log.debug(f"Matrix: evento antiguo omitido: {e}")
        return nuevos

    def _guardar_evento_historico(self, account_id: int, room_id: str, ev) -> bool:
        """Guarda un evento de la paginación (texto/acción/media) con su fecha real, como
        LEÍDO y sin tocar el resumen del chat (es historial)."""
        from nio import (RoomMessageText, RoomMessageEmote, RoomMessageNotice, RoomMessageMedia,
                         RoomEncryptedMedia)
        # Reacciones y votos que aparecen al paginar historia: registrarlos como relaciones (MX-012)
        # para que los mensajes antiguos también muestren su recuento de reacciones. No son "mensajes
        # nuevos", así que devuelven False (no cuentan para el total de la paginación).
        tipo_ev = getattr(ev, "type", "") or ""
        if tipo_ev in ("m.reaction", getattr(self, "_POLL_RESPONSE", "")):
            src = getattr(ev, "source", {}) or {}
            rel = ((src.get("content", {}) or {}).get("m.relates_to")) or {}
            eid = getattr(ev, "event_id", "") or ""
            sender = getattr(ev, "sender", "") or ""
            tsms = getattr(ev, "server_timestamp", 0)
            if tipo_ev == "m.reaction" and rel.get("rel_type") == "m.annotation" \
                    and rel.get("event_id") and rel.get("key"):
                if self._registrar_relacion(account_id, eid, rel["event_id"], "m.annotation",
                                            rel["key"], sender, tsms):
                    self._recalcular_reacciones(account_id, rel["event_id"])
            elif rel.get("rel_type") == "m.reference" and rel.get("event_id"):
                import json as _j
                answers = ((src.get("content", {}) or {}).get(self._POLL_RESPONSE) or {}).get("answers") or []
                if self._registrar_relacion(account_id, eid, rel["event_id"], "m.reference",
                                            _j.dumps(answers), sender, tsms):
                    self._recalcular_votos(account_id, rel["event_id"])
            return False
        propio = (getattr(ev, "sender", "") == self._user_id(account_id))
        comunes = dict(
            sender=getattr(ev, "sender", "") or "",
            sender_nombre="",
            event_id=getattr(ev, "event_id", "") or "",
            fecha=ts_a_iso(getattr(ev, "server_timestamp", 0)),
            propio=propio, leido=True, contar=False, actualizar_resumen=False)
        if isinstance(ev, (RoomMessageMedia, RoomEncryptedMedia)):
            etiqueta = self._etiqueta_media(ev)
            return self._guardar_mensaje(account_id, room_id, "media", texto=etiqueta,
                                         media_mxc=getattr(ev, "url", "") or "",
                                         media_crypto=crypto_de_media(ev), **comunes)
        if isinstance(ev, RoomMessageEmote):
            return self._guardar_mensaje(account_id, room_id, "accion",
                                         texto=getattr(ev, "body", "") or "", **comunes)
        if isinstance(ev, (RoomMessageText, RoomMessageNotice)):
            return self._guardar_mensaje(account_id, room_id, "msg",
                                         texto=getattr(ev, "body", "") or "", **comunes)
        return False

    @staticmethod
    def _etiqueta_media(ev) -> str:
        """Etiqueta legible en español para un adjunto (el lector nunca oye el mxc crudo). Sirve
        tanto para adjuntos en claro (RoomMessage*) como cifrados (RoomEncryptedMedia): estos no
        tienen subclase por tipo, así que se distinguen por el mimetype del evento."""
        from nio import RoomMessageImage, RoomMessageAudio, RoomMessageVideo
        nombre = getattr(ev, "body", "") or _("adjunto")
        mime = (getattr(ev, "mimetype", "") or "").lower()
        if isinstance(ev, RoomMessageImage) or mime.startswith("image/"):
            return _("[Imagen: {0}]").format(nombre)
        if isinstance(ev, RoomMessageAudio) or mime.startswith("audio/"):
            return _("[Mensaje de audio: {0}]").format(nombre)
        if isinstance(ev, RoomMessageVideo) or mime.startswith("video/"):
            return _("[Vídeo: {0}]").format(nombre)
        return _("[Archivo: {0}]").format(nombre)
