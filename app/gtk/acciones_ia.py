# -*- coding: utf-8 -*-
"""Traducir, resumir y describir imágenes con IA desde la ventana principal.

Todo el trabajo de red va en segundo plano (task_manager); el resultado se muestra en un cuadro de
texto de solo lectura y se anuncia. Los ajustes salen de Preferencias (IA y Traducción)."""

import json
import logging
import os

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.html_utils import html_a_texto
from app.core.speech import notificar
from app.core.task_manager import task_manager

log = logging.getLogger(__name__)


def ia_activa() -> bool:
    if config.get("ai", "provider", "gemini") == "off":
        notificar(_("La IA está desactivada. Actívala en Preferencias, pestaña Inteligencia artificial."))
        return False
    return True


def _mostrar(v, titulo: str, texto: str):
    from .dialogos_mastodon_extra import DialogoTextoLargo
    DialogoTextoLargo(v, titulo, texto).present()


def texto_de(v, obj):
    """(texto plano, idioma declarado, status_id de Mastodon o None, account_id) de la fila, o None."""
    f = obj.fila
    if f.servicio == "mastodon":
        dbid = f.extra.get("estado_db_id") or f.id
        r = db.query_one("SELECT COALESCE(spoiler_text, ''), COALESCE(content, ''), COALESCE(language, ''), "
                         "COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (dbid,))
        if not r:
            return None
        texto = (r[0] + "\n" if r[0] else "") + r[1]
        return texto.strip(), r[2], r[3], f.extra.get("account_id")
    if f.servicio == "reddit":
        from . import datos_reddit
        return datos_reddit.texto_plano(f), "", None, None
    if f.servicio in ("bluesky", "wsocial"):
        uri = f.extra.get("uri") or ""
        r = db.query_one(f"SELECT COALESCE(content, ''), COALESCE(langs, '') FROM {f.servicio}_posts WHERE account_id = ? AND uri = ?",
                         (f.extra.get("account_id"), uri))
        if not r:
            return None
        return r[0].strip(), (r[1].split(",")[0] if r[1] else ""), None, None
    from app.services.rss.rss_service import rss_service
    art = rss_service.obtener_articulo(f.id)
    if art is None:
        return None
    cuerpo = html_a_texto(art.content or "")
    return ((f.titulo + "\n\n" + cuerpo).strip(), "", None, None)


# --------------------------------------------------------------------------- traducir
def traducir(v):
    obj = v._fila_actual()
    if obj is None:
        v.avisar(_("Selecciona un elemento para traducir"))
        return
    datos = texto_de(v, obj)
    if not datos or not datos[0]:
        v.avisar(_("No hay texto para traducir"))
        return
    texto, declarado, status_id, account_id = datos
    traducir_texto(v, texto, declarado, status_id, account_id)


def traducir_texto(v, texto: str, declarado: str = "", status_id=None, account_id=None):
    """Traduce `texto` y lo muestra. `v` es la ventana (o diálogo) que avisa y de la que cuelga el resultado."""
    v.avisar(_("Traduciendo…"))

    def _bg(ctx):
        from app.services.translation.translation_service import translation_service
        destino = config.get("translation", "default_target", "es")
        base = destino.split("-")[0].lower()
        declarado_b = (declarado or "").split("-")[0].lower()
        detectado = ""
        if config.get("translation", "auto_detect", True):
            try:
                detectado = (translation_service.detectar_idioma(texto) or "").split("-")[0].lower()
            except Exception:
                detectado = ""
        # Solo se da por traducido si el idioma declarado y el detectado coinciden con el de destino.
        if declarado_b == base and detectado == base:
            return {"skip": True, "idioma": destino}
        if status_id and account_id and config.get("translation", "mastodon_server", True):
            from app.services.mastodon.mastodon_service import mastodon_service
            caps = mastodon_service.capacidades(account_id)
            if not caps or caps.get("traduccion"):
                try:
                    cli = mastodon_service.obtener_cliente(account_id)
                    r = cli.status_translate(status_id, lang=base)
                    contenido = (r or {}).get("content") or ""
                    if contenido:
                        return {"texto": html_a_texto(contenido) or contenido, "nativa": True,
                                "proveedor": r.get("provider") or "",
                                "detectado": r.get("detected_source_language") or detectado or declarado_b}
                except Exception as e:
                    log.debug(f"Traducción del servidor no disponible, se usa el motor externo: {e}")
        return {"texto": translation_service.traducir(texto, idioma_destino=destino), "detectado": detectado or declarado_b}

    def _fin(res, err):
        if err:
            event_bus.emit("sound_trigger", "translation_error")
            v.avisar(_("Error de traducción: {e}").format(e=err))
            return
        if res.get("skip"):
            v.avisar(_("Ya está en tu idioma ({i})").format(i=res["idioma"]))
            return
        event_bus.emit("sound_trigger", "translation_done")
        cabecera = ""
        if res.get("nativa"):
            cabecera = _("Traducido por tu servidor") + (f" ({res['proveedor']})" if res.get("proveedor") else "")
        elif res.get("detectado"):
            cabecera = _("Traducido desde {i}").format(i=res["detectado"])
        v.avisar(_("Traducción lista. {c}").format(c=cabecera))
        if config.get("translation", "copy_on_translate", True):
            from app.core.speech import copiar_al_portapapeles
            copiar_al_portapapeles(res["texto"], _("Traducción copiada al portapapeles"))
        _mostrar(v, _("Traducción"), (cabecera + "\n\n" if cabecera else "") + res["texto"])
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- resumir
def resumir(v):
    if not ia_activa():
        return
    obj = v._fila_actual()
    datos = texto_de(v, obj) if obj else None
    if not datos or not datos[0]:
        v.avisar(_("No hay texto para resumir"))
        return
    v.avisar(_("Resumiendo con IA…"))

    def _bg(ctx):
        from app.services.ai.ai_service import ai_service
        return ai_service.resumir_texto(datos[0])

    def _fin(res, err):
        if err:
            event_bus.emit("sound_trigger", "ai_error")
            v.avisar(_("Error de la IA: {e}").format(e=err))
            return
        event_bus.emit("sound_trigger", "ai_done")
        v.avisar(_("Resumen listo"))
        _mostrar(v, _("Resumen"), res)
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- imágenes
def _imagenes_de(obj) -> list:
    f = obj.fila
    if f.servicio in ("bluesky", "wsocial"):
        r = db.query_one(f"SELECT media_json FROM {f.servicio}_posts WHERE account_id = ? AND uri = ?",
                         (f.extra.get("account_id"), f.extra.get("uri") or ""))
        try:
            adj = json.loads(r[0]) if r and r[0] else []
        except Exception:
            adj = []
        return [{"type": a.get("type"), "url": a.get("url"), "description": a.get("alt") or ""}
                for a in adj if a.get("type") == "image" and a.get("url")]
    if f.servicio != "mastodon":
        return []
    dbid = f.extra.get("estado_db_id") or f.id
    r = db.query_one("SELECT media_json FROM mastodon_statuses WHERE id = ?", (dbid,))
    try:
        adj = json.loads(r[0]) if r and r[0] else []
    except Exception:
        adj = []
    return [a for a in adj if a.get("type") in ("image", "photo") and a.get("url")]


def analizar_imagen(v, modo: str = "describir"):
    """modo: 'describir' o 'ocr'. Si la publicación tiene varias imágenes, se elige una."""
    if not ia_activa():
        return
    obj = v._fila_actual()
    imagenes = _imagenes_de(obj) if obj else []
    if not imagenes:
        v.avisar(_("La publicación seleccionada no tiene imágenes"))
        return
    if len(imagenes) == 1:
        _lanzar(v, imagenes[0]["url"], modo)
        return
    from .dialogos import DialogoElegir
    nombres = [_("Imagen {i}: {d}").format(i=i, d=(a.get("description") or _("sin descripción"))[:60])
               for i, a in enumerate(imagenes, 1)]
    DialogoElegir(v, _("Elegir imagen"), _("_Imagen:"), nombres, lambda i: _lanzar(v, imagenes[i]["url"], modo),
                  _("_Analizar")).present()


def _lanzar(v, url: str, modo: str):
    v.avisar(_("Extrayendo el texto de la imagen…") if modo == "ocr" else _("Describiendo la imagen con IA…"))

    def _bg(ctx):
        import requests
        from app.core.paths import get_data_path
        from app.services.ai.ai_service import ai_service
        ruta = get_data_path("temp/ia_imagen.jpg")
        ruta.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ruta.write_bytes(r.content)
        try:
            return ai_service.ocr_imagen(str(ruta)) if modo == "ocr" else ai_service.describir_imagen(str(ruta))
        finally:
            try:
                os.remove(ruta)
            except OSError:
                pass

    def _fin(res, err):
        if err:
            event_bus.emit("sound_trigger", "ai_error")
            v.avisar(_("Error de la IA: {e}").format(e=err))
            return
        if modo == "ocr" and not (res or "").strip():
            v.avisar(_("No se reconoció ningún texto en la imagen"))
            return
        event_bus.emit("sound_trigger", "ai_done")
        v.avisar(_("Resultado listo"))
        _mostrar(v, _("Texto de la imagen") if modo == "ocr" else _("Descripción de la imagen"), res)
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- en el editor
def texto_alternativo(ruta: str, al_tener):
    """Genera con IA el texto alternativo de un archivo de imagen; `al_tener(texto)` en la UI."""
    if not ia_activa():
        return
    notificar(_("Generando el texto alternativo…"))

    def _bg(ctx):
        from app.services.ai.ai_service import ai_service
        return ai_service.generar_texto_alternativo(ruta)

    def _fin(res, err):
        if err:
            notificar(_("Error de la IA: {e}").format(e=err))
            return
        notificar(_("Texto alternativo generado"))
        al_tener(res)
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


def transformar_texto(texto: str, que: str, al_tener):
    """que: 'mejorar' o 'hashtags'."""
    if not ia_activa():
        return
    if not texto.strip():
        notificar(_("Escribe antes el texto"))
        return
    notificar(_("Consultando a la IA…"))

    def _bg(ctx):
        from app.services.ai.ai_service import ai_service
        return ai_service.mejorar_redaccion(texto) if que == "mejorar" else ai_service.sugerir_hashtags(texto)

    def _fin(res, err):
        if err:
            event_bus.emit("sound_trigger", "ai_error")
            notificar(_("Error de la IA: {e}").format(e=err))
            return
        event_bus.emit("sound_trigger", "ai_done")
        al_tener((res or "").strip())
    task_manager.start_task(_bg, callback=_fin, pass_context=True)
