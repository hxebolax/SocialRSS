# -*- coding: utf-8 -*-
"""
Módulo para el compositor y publicador de publicaciones simples en Mastodon.
Soporta subida de multimedia con texto alternativo de forma asíncrona.
"""

import os
import logging
import mimetypes
from typing import List, Optional, Callable
from app.core.task_manager import task_manager
from .mastodon_service import mastodon_service

log = logging.getLogger(__name__)

# Tipo MIME por extensión, según las constantes del servidor oficial de Mastodon
# (app/models/media_attachment.rb: IMAGE/VIDEO/AUDIO_MIME_TYPES). Se usa este mapa explícito
# porque mimetypes de Python falla o acierta a medias con formatos modernos (heic, avif, opus...).
MIME_POR_EXTENSION = {
    # Imágenes
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif", ".avif": "image/avif",
    # Vídeo
    ".webm": "video/webm", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    # Audio
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg",
    ".wav": "audio/wav", ".flac": "audio/flac", ".aac": "audio/aac", ".m4a": "audio/mp4",
    ".3gp": "audio/3gpp", ".wma": "video/x-ms-asf",
}


def _adivinar_mime(path: str) -> Optional[str]:
    """Devuelve el tipo MIME del archivo: primero por el mapa oficial de Mastodon (por extensión),
    y si no, lo que adivine Python."""
    ext = os.path.splitext(path.lower())[1]
    if ext in MIME_POR_EXTENSION:
        return MIME_POR_EXTENSION[ext]
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type


def tipo_de_adjunto(path: str) -> str:
    """Clasifica un archivo en 'image', 'video', 'audio' o '' (no admitido), según su tipo MIME de
    Mastodon. Sirve para aplicar la regla oficial de combinación (el audio y el vídeo van solos;
    hasta 4 imágenes). El GIF cuenta como imagen (el servidor permite combinarlo)."""
    mime = (_adivinar_mime(path) or "").lower()
    if mime == "video/x-ms-asf":   # .wma → en realidad audio
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return ""

class MastodonComposer:
    """Clase para componer y enviar posts simples."""
    
    @staticmethod
    def publicar_toot_async(
        account_id: int,
        texto: str,
        visibility: str = "public",
        sensitive: bool = False,
        spoiler_text: Optional[str] = None,
        media_paths: Optional[List[str]] = None,
        alt_texts: Optional[List[str]] = None,
        focus_points: Optional[List] = None,
        poll_options: Optional[List[str]] = None,
        poll_expires_in: int = 86400,
        poll_multiple: bool = False,
        scheduled_at=None,
        in_reply_to_id: Optional[str] = None,
        quoted_status_id: Optional[str] = None,
        language: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        callback: Optional[Callable[[bool, Optional[str]], None]] = None,
        quote_approval_policy: Optional[str] = None,
    ):
        """Publica un toot en segundo plano cargando multimedia primero."""
        # Clave de idempotencia (estable para esta publicación): si la librería reintenta la
        # petición (límite de tasa, corte transitorio), el servidor no crea un toot duplicado. El
        # llamante puede pasar una clave propia (p. ej. la publicación cruzada, para blindar cada
        # cuenta frente a reintentos de su instancia); si no, se genera una.
        import uuid as _uuid
        idem_key = idempotency_key or _uuid.uuid4().hex

        # Regla de la API: una cita nativa no puede llevar encuesta ni multimedia. Se
        # valida AQUÍ, antes de subir ningún archivo (antes se rechazaba después de
        # haberlos subido, gastando tiempo y cuota; auditoría M8).
        if quoted_status_id:
            con_encuesta = len([o for o in (poll_options or []) if o and o.strip()]) >= 2
            if media_paths or con_encuesta:
                if callback:
                    callback(None, Exception(
                        "Una cita no puede llevar encuesta ni archivos adjuntos "
                        "(regla de Mastodon). Quita los adjuntos o publica sin citar."))
                return

        def _bg_post(ctx):
            client = mastodon_service.obtener_cliente(account_id)

            # 1. Subir archivos adjuntos si existen
            media_ids = []
            if media_paths:
                for idx, path in enumerate(media_paths):
                    if ctx.is_cancelled():
                        break
                        
                    if not os.path.exists(path):
                        raise FileNotFoundError(f"Archivo adjunto no encontrado: {path}")
                        
                    ctx.report_progress(
                        int((idx / len(media_paths)) * 100), 
                        f"Subiendo adjunto {idx + 1} de {len(media_paths)}..."
                    )
                    
                    # Adivinar tipo mime (mapa oficial de Mastodon por extensión).
                    mime_type = _adivinar_mime(path)

                    # Texto alternativo asociado
                    alt = ""
                    if alt_texts and idx < len(alt_texts):
                        alt = alt_texts[idx].strip()
                    alt_param = alt if alt else None

                    # Punto focal de la imagen (auditoría N5): tupla (x, y) en [-1, 1]
                    # que indica qué zona queda visible al recortar miniaturas.
                    focus_param = None
                    if focus_points and idx < len(focus_points) and focus_points[idx]:
                        try:
                            f = focus_points[idx]
                            focus_param = (float(f[0]), float(f[1]))
                        except (TypeError, ValueError, IndexError):
                            focus_param = None

                    # No registrar la ruta completa ni el texto alternativo: pueden contener
                    # nombres personales o descripciones sensibles (auditoría H5).
                    log.info(f"Subiendo adjunto {idx + 1}/{len(media_paths)} "
                             f"({os.path.splitext(path)[1].lower() or 'sin extensión'}, "
                             f"MIME {mime_type}, alt {'sí' if alt else 'no'})")
                    
                    with open(path, "rb") as f_media:
                        res = client.media_post(
                            f_media,
                            mime_type=mime_type,
                            description=alt_param,
                            focus=focus_param
                        )
                    # Esperar a que el servidor TERMINE de procesar el archivo (sobre todo
                    # vídeos y GIF): mientras no tenga 'url', sigue procesándose. Publicar con
                    # un adjunto a medio procesar hace que algunas instancias rechacen el toot.
                    if isinstance(res, dict) and not res.get("url"):
                        import time as _t
                        for _ in range(60):  # hasta ~60 s
                            if ctx.is_cancelled():
                                raise Exception("Publicación cancelada.")
                            _t.sleep(1)
                            try:
                                actualizado = client.media(res["id"])
                            except Exception:
                                break
                            if isinstance(actualizado, dict) and actualizado.get("url"):
                                break
                    media_ids.append(res["id"])
                    
            if ctx.is_cancelled():
                raise Exception("Publicación cancelada.")
                
            # 2. Publicar la entrada
            ctx.report_progress(90, "Publicando toot...")
            cw_param = spoiler_text.strip() if spoiler_text and spoiler_text.strip() else None

            # Construir la encuesta si se han indicado opciones (mínimo 2).
            poll_param = None
            opciones_validas = [o.strip() for o in (poll_options or []) if o and o.strip()]
            if len(opciones_validas) >= 2:
                try:
                    poll_param = client.make_poll(
                        opciones_validas, expires_in=poll_expires_in, multiple=poll_multiple
                    )
                except Exception as e:
                    log.error(f"No se pudo construir la encuesta: {e}")

            # Cita NATIVA de Mastodon (quoted_status_id). Si el servidor lo admite, crea una
            # cita real; si el cuerpo no lleva el enlace, el propio servidor lo añade por
            # compatibilidad. Una cita no puede llevar a la vez encuesta o multimedia.
            kw_quote = {}
            if quoted_status_id:
                kw_quote["quoted_status_id"] = quoted_status_id
            # Quién puede citar ESTA publicación (Mastodon 4.5+, API 7): public, followers o
            # nobody. None = la política por defecto de la cuenta. Solo tiene sentido en
            # publicaciones públicas o no listadas (en las privadas nadie más puede citarlas).
            if quote_approval_policy and visibility in ("public", "unlisted"):
                kw_quote["quote_approval_policy"] = quote_approval_policy
            status = client.status_post(
                status=texto,
                visibility=visibility,
                sensitive=sensitive,
                spoiler_text=cw_param,
                media_ids=media_ids if media_ids else None,
                poll=poll_param,
                scheduled_at=scheduled_at,
                in_reply_to_id=in_reply_to_id,
                idempotency_key=idem_key,
                language=(language or None),
                **kw_quote
            )

            # Si era programada, la respuesta es un ScheduledStatus (sin "id" de toot
            # publicado): no se guarda en la caché de timeline.
            if scheduled_at:
                log.info("Publicación programada correctamente.")
                return status.get("id")

            # Guardar en la base de datos local Y asociarlo YA a sus buffers (ENVIADOS
            # siempre; INICIO o DIRECTOS según visibilidad). Antes en Inicio se confiaba
            # solo en el stream, y si el streaming iba lento o caído tu propia publicación
            # tardaba en salir en Inicio (fallo reportado; TWBlue la inserta al momento).
            # El stream después no duplica: la asociación es INSERT OR IGNORE.
            try:
                st = mastodon_service._procesar_y_guardar_toot(account_id, status)
                if st is not None:
                    from app.core.database import db as _db
                    buffers = ["sent"]
                    buffers.append("direct" if (status.get("visibility") == "direct") else "home")
                    for _tl in buffers:
                        _db.execute(
                            "INSERT OR IGNORE INTO mastodon_timeline_statuses "
                            "(account_id, timeline, status_db_id) VALUES (?, ?, ?)",
                            (account_id, _tl, st.id))
                    from app.core.event_bus import event_bus as _bus
                    _bus.emit("mastodon_publicado", account_id)
            except Exception as e:
                log.error(f"Error al registrar toot publicado en caché local: {e}")

            # Si era una RESPUESTA, el contador de respuestas del toot original acaba de subir en el
            # servidor: lo volvemos a pedir y lo guardamos para que su contador quede al día YA (no
            # solo al pulsar actualizar). La interfaz repinta los contadores en su sitio.
            if in_reply_to_id:
                try:
                    padre = client.status(in_reply_to_id)
                    if isinstance(padre, dict):
                        mastodon_service._procesar_y_guardar_toot(account_id, padre)
                except Exception as e:
                    log.debug(f"No se pudo refrescar el contador del toot original: {e}")

            log.info(f"Toot publicado con éxito. ID original: {status['id']}")
            return status["id"]

        def _on_finish(result, error):
            if error:
                log.error(f"Fallo al publicar toot: {error}")
                if callback: callback(False, str(error))
            else:
                if callback: callback(True, str(result))

        # Disparar tarea en segundo plano
        task_manager.start_task(
            _bg_post,
            callback=_on_finish,
            success_sound="ready",
            error_sound="error",
            pass_context=True
        )
