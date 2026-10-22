# -*- coding: utf-8 -*-
"""Helpers de UI compartidos: formato de tiempo relativo, citas y eventos de media.

Extraídos de main_frame para que otros módulos los importen sin arrastrar la ventana.
"""
import json
from datetime import datetime
from app.core.config_manager import config
import logging


log = logging.getLogger(__name__)


def medir_carga(fn):
    """Decorador de MEDICIÓN para las cargas de buffer: registra en el log (DEBUG) cuánto
    tardó la función cuando supera los 200 ms. Sirve para detectar con datos reales qué
    buffers se vuelven lentos al crecer (y decidir si conviene materialización perezosa),
    sin coste apreciable cuando todo va rápido."""
    import functools
    import time as _time

    @functools.wraps(fn)
    def _envuelto(*args, **kwargs):
        t0 = _time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            ms = (_time.perf_counter() - t0) * 1000
            if ms >= 200:
                log.debug(f"Carga lenta: {fn.__name__} tardó {ms:.0f} ms")
    return _envuelto


def formatear_duracion_legible(segundos) -> str:
    """Segundos -> duración en LENGUAJE NATURAL, para leerla con el lector de pantalla:
    1087 -> '18 minutos y 7 segundos'; 3600 -> '1 hora'; 3665 -> '1 hora, 1 minuto y 5 segundos'.
    Devuelve cadena vacía si no hay duración válida (0, negativa o no numérica; p. ej. un directo).
    Para columnas de una lista usa el formato compacto 'm:ss' de cada red, no este."""
    try:
        _
    except NameError:
        _ = lambda x: x

    try:
        s = int(segundos or 0)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    horas, resto = divmod(s, 3600)
    minutos, segs = divmod(resto, 60)
    partes = []
    if horas:
        partes.append(_("1 hora") if horas == 1 else _("{0} horas").format(horas))
    if minutos:
        partes.append(_("1 minuto") if minutos == 1 else _("{0} minutos").format(minutos))
    if segs:
        partes.append(_("1 segundo") if segs == 1 else _("{0} segundos").format(segs))
    if len(partes) == 1:
        return partes[0]
    # "1 hora, 5 minutos y 3 segundos": comas entre todas menos la última, que va con "y".
    return _("{0} y {1}").format(", ".join(partes[:-1]), partes[-1])


def formatear_tiempo_relativo(date_str: str) -> str:
    try:
        _
    except NameError:
        _ = lambda x: x

    if not date_str:
        return ""
    try:
        # Usar fromisoformat primero porque conserva la zona horaria (offset).
        # No se debe truncar la cadena, ya que eso descartaría el "+00:00" y
        # convertiría una fecha UTC en una falsa fecha local (desfase de horas).
        ds = date_str.strip()
        if ds.endswith("Z"):
            ds = ds[:-1] + "+00:00"
        dt = None
        try:
            dt = datetime.fromisoformat(ds)
        except ValueError:
            clean_str = ds.replace("T", " ")
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(clean_str[:26] if len(clean_str) > 26 else clean_str, fmt)
                    break
                except ValueError:
                    continue
        if not dt:
            return date_str[:16].replace("T", " ")

        ahora = datetime.now()
        if dt.tzinfo is not None:
            # Convertir de su zona (normalmente UTC) a la hora local antes de
            # comparar, para que "hace X" sea correcto y en tiempo real.
            dt = dt.astimezone().replace(tzinfo=None)

        # Marcas de tiempo relativas (como TWBlue). Si el usuario las desactiva en
        # Preferencias, se muestra la fecha y hora absolutas en su lugar.
        try:
            if not config.get("general", "relative_times", True):
                return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass

        diff = ahora - dt
        segundos = diff.total_seconds()
        
        if segundos < 0:
            return _("en el futuro")
        if segundos < 60:
            return _("hace unos segundos")
            
        minutos = int(segundos // 60)
        if minutos < 60:
            if minutos == 1:
                return _("hace 1 minuto")
            return _("hace {0} minutos").format(minutos)
            
        horas = int(minutos // 60)
        if horas < 24:
            if horas == 1:
                return _("hace 1 hora")
            return _("hace {0} horas").format(horas)
            
        dias = int(horas // 24)
        if dias < 30:
            if dias == 1:
                return _("hace 1 día")
            return _("hace {0} días").format(dias)
            
        meses = int(dias // 30)
        if meses < 12:
            if meses == 1:
                return _("hace 1 mes")
            return _("hace {0} meses").format(meses)
            
        años = int(meses // 12)
        if años == 1:
            return _("hace 1 año")
        return _("hace {0} años").format(años)
    except Exception as e:
        log.warning(f"Error al formatear tiempo relativo para {date_str}: {e}")
        return date_str[:16].replace("T", " ")

def prefijo_notificacion_mastodon(ntype: str) -> str:
    """Prefijo legible de un tipo de notificación de Mastodon ("[Mención]", "[Cita]"...).
    FUENTE ÚNICA: antes había dos mapas y el del buffer de notificaciones estaba incompleto
    (salía "[QUOTE]" o "[ADDED_TO_COLLECTION]" en crudo). Tipos hasta Mastodon 4.7."""
    prefijos = {
        "mention": _("[Mención]"), "reblog": _("[Boosteado]"), "favourite": _("[Favorito]"),
        "follow": _("[Seguidor]"), "follow_request": _("[Petición]"), "poll": _("[Encuesta]"),
        "status": _("[Nuevo toot]"), "update": _("[Actualizado]"), "quote": _("[Cita]"),
        "moderation_warning": _("[Aviso de moderación]"),
        "severed_relationships": _("[Relaciones cortadas]"),
        "quoted_update": _("[Cita editada]"), "admin.report": _("[Informe]"),
        "admin.sign_up": _("[Nuevo registro]"),
        # 4.6: colecciones (listas públicas de cuentas destacadas).
        "added_to_collection": _("[Añadido a una colección]"),
        "collection_update": _("[Colección actualizada]"),
    }
    return prefijos.get(ntype or "", "[{0}]".format((ntype or "").upper()))


def formato_cita(quote_json, account_id=None, timeline="", clave=None) -> str:
    """Texto accesible de una cita nativa de Mastodon (o '' si el toot no cita a nadie).

    Si se guardó el 'filtered' de la publicación CITADA y se indica la cuenta y el buffer, se
    le aplican los filtros del servidor como a cualquier toot: 'ocultar' la sustituye por un
    aviso y 'avisar' esconde su texto tras '[Filtrado: título]' hasta que se revela la fila
    ('clave' = db_id de la publicación que cita)."""
    if not quote_json:
        return ""
    try:
        import json as _json_q
        q = _json_q.loads(quote_json)
    except Exception:
        return ""
    if not isinstance(q, dict):
        return ""
    if q.get("filtered") and account_id:
        try:
            from app.services.mastodon import filtros_servidor as _fsrv
            ev = _fsrv.evaluar(q.get("filtered"), account_id, timeline or "")
            if ev.get("accion") == "hide":
                return _("Cita oculta por tu filtro: {0}").format(", ".join(ev.get("titulos") or []))
            if _fsrv.oculta_texto(ev, clave):
                q = dict(q, content=_fsrv.texto_filtrado(ev))
        except Exception:
            pass
    # Citas que NO se pueden mostrar (entidad Quote de Mastodon): se explica por qué, en vez de
    # que la cita desaparezca sin más. Las antiguas (sin "state") eran siempre aceptadas.
    estado = q.get("state") or "accepted"
    if estado != "accepted":
        motivos = {
            "pending": _("Cita pendiente de que el autor original la apruebe."),
            "rejected": _("Cita rechazada por el autor de la publicación original."),
            "revoked": _("Cita retirada: el autor original quitó el permiso."),
            "deleted": _("Cita de una publicación que se ha borrado."),
            "unauthorized": _("Cita de una publicación que no tienes permiso para ver."),
            "blocked_account": _("Cita oculta: es de una cuenta que tienes bloqueada."),
            "blocked_domain": _("Cita oculta: es de un servidor que tienes bloqueado."),
            "muted_account": _("Cita oculta: es de una cuenta que tienes silenciada."),
        }
        return motivos.get(estado, _("Cita que no se puede mostrar."))
    nombre = (q.get("name") or q.get("acct") or "").strip()
    acct = (q.get("acct") or "").strip()
    contenido = (q.get("content") or "").replace("\n", " ").strip()
    if not (nombre or contenido):
        return ""
    etiqueta = f"{nombre} (@{acct})" if acct else nombre
    return _("Citando a {0}: {1}").format(etiqueta, contenido).strip()


def evento_sonido_media(media_type) -> str:
    """Devuelve el evento de sonido identificativo según el tipo de multimedia (o None)."""
    if not media_type:
        return None
    m = str(media_type).lower()
    if "photo" in m or "sticker" in m or "animation" in m or "image" in m:
        return "media_image"
    if "voice" in m or "audio" in m:
        return "media_audio"
    if "video" in m or "gifv" in m:
        return "media_video"
    if "document" in m:
        return "media_document"
    return "status_has_media"


def evento_media_de_attachments(media_json) -> str:
    """Dado el media_json de una publicación (Mastodon/Bluesky), devuelve el evento de
    sonido del primer adjunto de imagen, audio o vídeo (para los avisos al situarse en
    la publicación, como TWBlue), o None si no hay multimedia relevante."""
    if not media_json:
        return None
    try:
        atts = json.loads(media_json) if isinstance(media_json, str) else media_json
    except Exception:
        return None
    if not atts:
        return None
    for a in atts:
        if not isinstance(a, dict):
            continue
        ev = evento_sonido_media(a.get("type", ""))
        if ev in ("media_image", "media_audio", "media_video"):
            return ev
    return "status_has_media"
