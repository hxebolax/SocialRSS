# -*- coding: utf-8 -*-
"""Archivos y notas de voz de XMPP (tanda X5): HTTP File Upload (XEP-0363) para subir y compartir
la URL (+ OOB XEP-0066 para que cualquier cliente lo reconozca), descarga a caché/descargas y notas
de voz (grabador BASS + Opus, reutilizando el de Telegram/Matrix). En XMPP un adjunto es una URL
HTTPS normal, así que descargarlo es una simple petición GET.
"""
import os
import uuid
import mimetypes

from app.core.paths import get_data_path
from app.services.xmpp.xmpp_common import (
    db, log, XMPPError, ahora_iso, jid_bare)

_EXT_AUDIO = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".aac", ".flac")
_EXT_IMG = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
_EXT_VIDEO = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".3gp")
# Códecs típicos de las NOTAS DE VOZ (push-to-talk) en XMPP/WhatsApp: se leen "Mensaje de voz".
# Un .mp3/.m4a/.flac compartido se considera música (Mensaje de audio), no nota de voz.
_EXT_VOZ = (".ogg", ".oga", ".opus", ".amr", ".aac")

# Extensiones de contenido ACTIVO/ejecutable: un adjunto recibido por XMPP NO debe poder ejecutarse
# desde la app con un clic; solo se descarga y se marca con Mark-of-the-Web (XMPP-008).
_EXT_ACTIVAS = frozenset({
    ".exe", ".msi", ".msp", ".com", ".scr", ".pif", ".bat", ".cmd", ".ps1", ".psm1", ".vbs",
    ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".jar", ".lnk", ".url", ".reg", ".inf",
    ".cpl", ".dll", ".sys", ".gadget", ".application", ".msc", ".ade", ".adp", ".cab", ".chm",
    ".sh", ".py", ".pyw", ".apk", ".appx", ".appimage", ".command", ".scf", ".ws",
})


def es_extension_activa(nombre: str) -> bool:
    """True si el nombre/ruta acaba en una extensión de contenido activo (ejecutable/script)."""
    return os.path.splitext((nombre or "").split("?")[0].strip().rstrip(". "))[1].lower() in _EXT_ACTIVAS


def marcar_zona_internet(path: str):
    """Mark-of-the-Web (flujo NTFS Zone.Identifier, ZoneId=3): que Windows avise de que el archivo
    viene de Internet al abrirlo. Best-effort (FAT/exFAT no tienen ADS)."""
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as f:
            f.write("[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=about:internet\r\n")
    except Exception:
        pass


def _url_apunta_a_interno(url: str) -> bool:
    """True si el host de la URL resuelve a una dirección INTERNA (loopback/privada/link-local/
    metadata/reservada) o no se puede resolver: en esos casos NO se debe descargar (anti-SSRF)."""
    import ipaddress
    import socket
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").strip("[]")
    if not host:
        return True
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            puerto = p.port or (443 if p.scheme == "https" else 80)
            ips = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, puerto)]
        except Exception:
            return True  # no resuelve: no arriesgar
    if not ips:
        return True
    for ip in ips:
        if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            return True
    return False


class _MediosMixin:
    def enviar_archivo(self, account_id: int, jid: str, path: str, es_muc: bool = False,
                       es_voz: bool = False, duracion: float = 0.0) -> str:
        """Sube un archivo por HTTP Upload y comparte su URL en el chat (con OOB). Guarda el propio
        (en 1:1; en MUC lo pinta el eco). Devuelve el origin-id. 'es_voz'/'duracion' marcan una nota
        de voz (se lee "Mensaje de voz" con su duración, igual que las que recibes)."""
        import pathlib
        cliente = self._cliente_conectado(account_id)
        if not path or not os.path.exists(path):
            raise XMPPError("El archivo no existe.")
        jid = jid_bare(jid)
        # SEGURIDAD (XMPP-006): en un chat con OMEMO activado NO se puede enviar un adjunto en claro
        # (la URL y el archivo quedarían fuera del cifrado). Como todavía no hay transferencia cifrada
        # interoperable (XEP-0454/0448), se BLOQUEA con explicación en vez de degradar en silencio.
        try:
            if not es_muc and self.esta_cifrado(account_id, jid):
                raise XMPPError(_("Este chat usa cifrado OMEMO y el envío de archivos cifrados aún no "
                                  "está disponible. Para no mandar el archivo sin cifrar, se ha "
                                  "cancelado. Desactiva el cifrado de este chat si quieres enviarlo en "
                                  "claro, sabiendo que no irá protegido."))
        except XMPPError:
            raise
        except Exception:
            pass  # si no se puede saber el estado de cifrado, no bloquear
        tamano = os.path.getsize(path)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        nombre = os.path.basename(path)

        async def _subir():
            return await cliente["xep_0363"].upload_file(
                pathlib.Path(path), size=tamano, content_type=mime, timeout=300)
        try:
            url = self._run(_subir(), timeout=360.0)
        except Exception as e:
            raise XMPPError(self._error_upload(e))
        oid = "sr-" + uuid.uuid4().hex

        async def _enviar():
            if es_muc:
                await self._asegurar_unido_muc(cliente, account_id, jid)
            msg = cliente.make_message(mto=jid, mbody=url, mtype=("groupchat" if es_muc else "chat"))
            try:
                msg["oob"]["url"] = url          # XEP-0066: marca la URL como adjunto
                msg["origin_id"]["id"] = oid
            except Exception:
                pass
            msg.send()
            return True
        # 60 s: el PRIMER envío a una sala puede incluir la unión (join) a la pasarela.
        self._run(_enviar(), timeout=60.0)
        if not es_muc:
            # Nota de voz propia: etiqueta limpia "Mensaje de voz" (sin nombre de archivo) y se marca
            # ya como reproducida (la grabaste tú). El resto de adjuntos, su etiqueta normal.
            etiqueta = _("Mensaje de voz") if es_voz else self.etiqueta_media(nombre, mime)
            self._guardar_mensaje(account_id, jid, oid, self._user_jid(account_id),
                                  self._nombre_propio(account_id), etiqueta, "media", ahora_iso(),
                                  propio=True, leido=True, contar=False, media_url=url, origin_id=oid,
                                  es_voz=es_voz, duracion=duracion)
        return oid

    @staticmethod
    def _error_upload(e: Exception) -> str:
        t = str(e).lower()
        if "not-acceptable" in t or "too large" in t or "413" in t or "file-too-large" in t:
            return "El archivo es demasiado grande para el límite de subida del servidor."
        if "service-unavailable" in t or "no upload" in t or "not found" in t:
            return "Este servidor no ofrece subida de archivos (HTTP Upload)."
        return f"No se pudo subir el archivo: {e}"

    @staticmethod
    def es_audio(nombre_o_url: str) -> bool:
        return os.path.splitext((nombre_o_url or "").split("?")[0])[1].lower() in _EXT_AUDIO

    @staticmethod
    def es_imagen(nombre_o_url: str) -> bool:
        return os.path.splitext((nombre_o_url or "").split("?")[0])[1].lower() in _EXT_IMG

    @staticmethod
    def es_video(nombre_o_url: str) -> bool:
        return os.path.splitext((nombre_o_url or "").split("?")[0])[1].lower() in _EXT_VIDEO

    @staticmethod
    def es_nota_voz(nombre_o_url: str) -> bool:
        """True si el adjunto parece una NOTA DE VOZ (códec push-to-talk), para leerla 'Mensaje de voz'."""
        return os.path.splitext((nombre_o_url or "").split("?")[0])[1].lower() in _EXT_VOZ

    @staticmethod
    def formato_duracion(seg) -> str:
        """Segundos -> 'm:ss' (o 'h:mm:ss') como WhatsApp. '' si no hay duración."""
        try:
            seg = int(round(float(seg or 0)))
        except Exception:
            seg = 0
        if seg <= 0:
            return ""
        h, r = divmod(seg, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @staticmethod
    def etiqueta_media(nombre: str, mime: str = "") -> str:
        """Etiqueta legible en español según el tipo (el lector nunca oye la URL cruda)."""
        ext = os.path.splitext((nombre or "").split("?")[0])[1].lower()
        m = (mime or "").lower()
        if ext in _EXT_AUDIO or m.startswith("audio/"):
            return _("[Mensaje de audio: {0}]").format(nombre)
        if ext in _EXT_IMG or m.startswith("image/"):
            return _("[Imagen: {0}]").format(nombre)
        if ext in _EXT_VIDEO or m.startswith("video/"):
            return _("[Vídeo: {0}]").format(nombre)
        return _("[Archivo: {0}]").format(nombre)

    @staticmethod
    def nombre_desde_url(url: str) -> str:
        return (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "adjunto"

    # ---------------- Descarga ----------------
    @staticmethod
    def _limite_descarga_bytes() -> int:
        """Tope de descarga de adjuntos en bytes (config xmpp.max_descarga_mb; 0 = sin límite; 500
        por defecto): frena archivos enormes/zip-bomb que agotarían RAM o disco."""
        try:
            from app.core.config_manager import config
            mb = config.get("xmpp", "max_descarga_mb", 500)
            return int(mb) * 1048576 if mb and int(mb) > 0 else 0
        except Exception:
            return 500 * 1048576

    def descargar_media(self, account_id: int, url: str, nombre: str = "") -> str:
        """Descarga una URL a la caché local (si no estaba) y devuelve la ruta. Descarga ENDURECIDA
        (XMPP-007): valida que el destino no sea una dirección interna (anti-SSRF, incluidos los
        saltos de redirección), aplica un tope de tamaño con streaming y escribe de forma atómica."""
        if not url or not url.startswith(("http://", "https://")):
            raise XMPPError("Este mensaje no tiene un adjunto descargable.")
        carpeta = get_data_path("cache/xmpp")
        carpeta.mkdir(parents=True, exist_ok=True)
        nombre = nombre or self.nombre_desde_url(url)
        # Nombre estable por hash de la URL + extensión real, para reutilizar la caché.
        import hashlib
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        ext = os.path.splitext(nombre)[1][:10]
        destino = carpeta / f"{h}{ext}"
        if destino.exists() and destino.stat().st_size > 0:
            return str(destino)
        if _url_apunta_a_interno(url):
            raise XMPPError(_("El adjunto apunta a una dirección interna o no resoluble; se ha "
                              "cancelado la descarga por seguridad."))
        import urllib.request
        import urllib.error

        # Handler de redirección que VALIDA cada salto (un servidor podría redirigir de una URL
        # pública a una interna para hacer SSRF).
        class _RedirValidado(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if _url_apunta_a_interno(newurl):
                    raise urllib.error.HTTPError(newurl, code, "redirección a dirección interna "
                                                 "bloqueada", headers, fp)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        limite = self._limite_descarga_bytes()
        tmp = destino.with_suffix(destino.suffix + ".part")
        try:
            opener = urllib.request.build_opener(_RedirValidado())
            req = urllib.request.Request(url, headers={"User-Agent": "SocialRSS"})
            with opener.open(req, timeout=120) as r:
                # Rechazar antes de descargar si el servidor anuncia un tamaño mayor que el límite.
                cl = r.headers.get("Content-Length")
                if limite and cl and cl.isdigit() and int(cl) > limite:
                    raise XMPPError(_("El adjunto supera el tamaño máximo de descarga."))
                total = 0
                with open(tmp, "wb") as f:
                    while True:
                        trozo = r.read(65536)
                        if not trozo:
                            break
                        total += len(trozo)
                        if limite and total > limite:
                            raise XMPPError(_("El adjunto supera el tamaño máximo de descarga."))
                        f.write(trozo)
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp, destino)  # atómico: una descarga a medias nunca queda como válida
        except XMPPError:
            self._borrar_silencioso(tmp)
            raise
        except Exception as e:
            self._borrar_silencioso(tmp)
            raise XMPPError(_("No se pudo descargar el adjunto: {0}").format(e))
        marcar_zona_internet(str(destino))
        return str(destino)

    @staticmethod
    def _borrar_silencioso(ruta):
        try:
            if ruta.exists():
                ruta.unlink()
        except Exception:
            pass

    def descargar_a_descargas(self, account_id: int, url: str, nombre: str, titulo_chat: str) -> str:
        """Descarga a la carpeta de descargas del usuario, por red y chat:
        data/downloads/xmpp/<chat>/<archivo>, sin pisar los ya descargados."""
        import shutil
        ruta_cache = self.descargar_media(account_id, url, nombre)
        carpeta = get_data_path("downloads") / "xmpp" / self._sanear_nombre(titulo_chat)
        carpeta.mkdir(parents=True, exist_ok=True)
        base, ext = os.path.splitext(self._sanear_nombre(nombre or self.nombre_desde_url(url)) or "adjunto")
        destino = carpeta / f"{base}{ext}"
        n = 2
        while destino.exists():
            destino = carpeta / f"{base} ({n}){ext}"
            n += 1
        shutil.copy2(ruta_cache, destino)
        return str(destino)

    @staticmethod
    def _sanear_nombre(nombre: str) -> str:
        return "".join(c for c in (nombre or "") if c.isalnum() or c in "-_ .()").strip() or "chat"

    # ---------------- Notas de voz (grabador BASS + Opus, común con Telegram/Matrix) ----------------
    def iniciar_grabacion_voz(self) -> str:
        import time as _t
        from app.services.audio import microphone
        ruta = str(get_data_path("temp") / f"voz_xmpp_{int(_t.time())}.wav")
        dispositivo, volumen = microphone.config_microfono()
        grabador = microphone.GrabadorBass()
        try:
            grabador.iniciar(dispositivo or None, volumen)
        except RuntimeError as e:
            raise XMPPError(str(e))
        self._grabacion_xmpp = {"grabador": grabador, "ruta": ruta}
        return ruta

    def detener_grabacion_y_enviar(self, account_id: int, jid: str, es_muc: bool = False):
        from app.services.audio import microphone
        grab = getattr(self, "_grabacion_xmpp", None)
        if not grab:
            raise XMPPError("No hay ninguna grabación en curso.")
        ruta = grab["ruta"]
        self._grabacion_xmpp = None
        try:
            grab["grabador"].detener_a_wav(ruta)
        except RuntimeError as e:
            raise XMPPError(str(e))
        dur = microphone.duracion_wav(ruta)  # duración real de la nota de voz (desde el WAV)
        opus = microphone.convertir_a_opus(ruta)
        return self.enviar_archivo(account_id, jid, opus or ruta, es_muc=es_muc,
                                   es_voz=True, duracion=dur)

    def cancelar_grabacion_voz(self):
        grab = getattr(self, "_grabacion_xmpp", None)
        if not grab:
            return
        self._grabacion_xmpp = None
        try:
            grab["grabador"].cancelar()
        except Exception:
            pass
