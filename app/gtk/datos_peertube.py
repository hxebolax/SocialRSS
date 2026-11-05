# -*- coding: utf-8 -*-
"""Datos de PeerTube para la interfaz GTK: árbol de cuentas y secciones, listados y detalle.

Igual que con las demás redes: el servicio descarga y cachea los vídeos en SQLite y aquí solo se LEE esa caché. Las secciones
fijas, su orden, las ocultas y las búsquedas guardadas usan las mismas claves de configuración que la versión de Windows. Las
notificaciones, las listas de reproducción y los canales propios no se guardan en la base de datos: se descargan al entrar y
se guardan en memoria."""

import logging
from collections import defaultdict
from typing import Dict, List

from app.core.config_manager import config, limite_render
from app.core.database import db
from app.core.plantillas import definicion, pulir_lectura
from app.ui.ui_helpers import formatear_duracion_legible
from .datos import Nodo, Fila, fecha_legible

log = logging.getLogger(__name__)

SECCIONES = {
    "recientes": "Vídeos recientes", "populares": "Populares", "tendencias": "Tendencias", "locales": "Vídeos locales",
    "federados": "Vídeos federados", "suscripciones": "Suscripciones", "biblioteca": "Mi biblioteca",
    "historial": "Historial", "ver_mas_tarde": "Ver más tarde", "me_gusta": "Me gusta", "listas": "Listas de reproducción",
    "mis_videos": "Mis vídeos", "mis_canales": "Mis canales", "directos": "Directos", "notificaciones": "Notificaciones",
    "busquedas": "Búsquedas guardadas",
}
BIBLIOTECA = ("mis_videos", "mis_canales", "listas", "ver_mas_tarde", "me_gusta", "historial")
SECCIONES_VIDEO = ("recientes", "populares", "tendencias", "locales", "federados", "suscripciones", "historial", "mis_videos",
                   "directos", "me_gusta", "ver_mas_tarde")
PREFIJOS_VIDEO = ("channel:", "account:", "search:", "playlist:")
PRIVACIDADES = [(1, "Público"), (2, "No listado"), (3, "Privado"), (4, "Interno")]

# Lo que se descarga y no se guarda en la base de datos: (cuenta, sección) -> elementos.
_CACHE: Dict[tuple, list] = {}


def guardar_cache(account_id: int, seccion: str, elementos: list):
    _CACHE[(account_id, seccion)] = list(elementos or [])


def cache(account_id: int, seccion: str) -> list:
    return _CACHE.get((account_id, seccion), [])


def es_video(seccion: str) -> bool:
    return seccion in SECCIONES_VIDEO or str(seccion).startswith(PREFIJOS_VIDEO)


def es_cronologico(seccion: str) -> bool:
    return es_video(seccion)


def disponible() -> bool:
    """Punto único de decisión: interruptor del servicio en Preferencias y librería HTTP (httpx) instalada."""
    try:
        from app.core.feature_flags import servicio_habilitado
        return bool(servicio_habilitado("peertube"))
    except Exception:
        return False


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'peertube' AND active = 1 ORDER BY position, id")]


def _nodo(account_id: int, seccion: str, titulo: str, cerrable: bool = False) -> Nodo:
    return Nodo(titulo, "timeline", servicio="peertube", account_id=account_id, timeline=seccion, cerrable=cerrable)


def buffers_extra(account_id: int) -> List[dict]:
    """Canales, cuentas y listas de reproducción abiertos como buffer (solo los guarda la interfaz GTK)."""
    return [c for c in (config.get("peertube", "gtk_buffers", []) or [])
            if isinstance(c, dict) and c.get("account_id") in (None, account_id)]


def titulo_buffer(seccion: str, extra: str = "") -> str:
    if seccion.startswith("search:"):
        return _("Búsqueda: {0}").format(seccion.split(":", 1)[1])
    if seccion.startswith("channel:"):
        return _("Canal: {0}").format(extra or seccion.split(":", 1)[1])
    if seccion.startswith("account:"):
        return _("Cuenta: {0}").format(extra or seccion.split(":", 1)[1])
    if seccion.startswith("playlist:"):
        return _("Lista: {0}").format(extra or seccion.split(":", 1)[1])
    return _(SECCIONES.get(seccion, seccion))


def construir_nodos() -> Nodo:
    raiz = Nodo(_("PeerTube"), "servicio", servicio="peertube")
    orden = list(config.get("peertube", "section_order", list(SECCIONES)) or [])
    orden += [s for s in SECCIONES if s not in orden]
    ocultas = set(config.get("peertube", "hidden_sections", []) or [])
    for acc_id, nombre in cuentas():
        cuenta = Nodo(nombre, "cuenta", servicio="peertube", account_id=acc_id)
        hechas = set()
        for sec in orden:
            if sec in hechas or sec not in SECCIONES:
                continue
            hechas.add(sec)
            if sec == "biblioteca":
                hechas.update(BIBLIOTECA)
                if sec in ocultas:
                    continue
                grupo = Nodo(_(SECCIONES[sec]), "grupo", servicio="peertube", account_id=acc_id, timeline="grupo:biblioteca")
                for hija in BIBLIOTECA:
                    if hija not in ocultas:
                        grupo.hijos.append(_nodo(acc_id, hija, _(SECCIONES[hija])))
                cuenta.hijos.append(grupo)
            elif sec == "busquedas":
                if sec in ocultas:
                    continue
                grupo = Nodo(_(SECCIONES[sec]), "grupo", servicio="peertube", account_id=acc_id, timeline="grupo:busquedas")
                for termino in config.get("peertube", "saved_searches", []) or []:
                    grupo.hijos.append(_nodo(acc_id, "search:" + termino, titulo_buffer("search:" + termino), True))
                cuenta.hijos.append(grupo)
            elif sec not in ocultas:
                cuenta.hijos.append(_nodo(acc_id, sec, _(SECCIONES[sec])))
        for c in buffers_extra(acc_id):
            cuenta.hijos.append(_nodo(acc_id, c["timeline"], c.get("titulo") or c["timeline"], True))
        raiz.hijos.append(cuenta)
    return raiz


def nodo_extra(account_id: int, seccion: str, titulo: str) -> Nodo:
    """Persiste el buffer que se abre (si hace falta) y devuelve su nodo; el árbol se reconstruye desde lo guardado."""
    if seccion.startswith("search:"):
        termino = seccion.split(":", 1)[1]
        guardadas = list(config.get("peertube", "saved_searches", []) or [])
        if termino not in guardadas:
            guardadas.append(termino)
            config.set("peertube", "saved_searches", guardadas)
        titulo = titulo_buffer(seccion)
    else:
        guardados = list(config.get("peertube", "gtk_buffers", []) or [])
        if not any(isinstance(c, dict) and c.get("timeline") == seccion and c.get("account_id") in (None, account_id)
                   for c in guardados):
            guardados.append({"account_id": account_id, "timeline": seccion, "titulo": titulo})
            config.set("peertube", "gtk_buffers", guardados)
    return _nodo(account_id, seccion, titulo, True)


def cerrar_extra(nodo: Nodo):
    """Quita de lo guardado el buffer que el usuario cierra."""
    tl, acc = nodo.timeline, nodo.account_id
    if tl.startswith("search:"):
        termino = tl.split(":", 1)[1]
        config.set("peertube", "saved_searches", [s for s in (config.get("peertube", "saved_searches", []) or [])
                                                  if s != termino])
    else:
        config.set("peertube", "gtk_buffers", [c for c in (config.get("peertube", "gtk_buffers", []) or [])
                                               if not (isinstance(c, dict) and c.get("timeline") == tl
                                                       and c.get("account_id") in (None, acc))])
    db.execute("DELETE FROM peertube_video_buffers WHERE account_id = ? AND buffer = ?", (acc, tl))


# ---------------------------------------------------------------------------------------------- listados
_COLS_VIDEO = ("v.id, v.name, v.channel_display, v.channel_name, v.duration, v.published_at, v.views, v.likes, v.is_live, "
               "v.nsfw, v.is_local, v.description, v.language, v.uuid, v.url, v.account_name, v.account_host, "
               "v.instance_host, v.video_id, v.privacy")


def _plantilla(clave: str) -> str:
    return config.get("accessibility", clave, "") or definicion(clave)["default"]


def _yo(account_id: int) -> str:
    try:
        from app.services.peertube import peertube_service
        return (peertube_service.nombre_usuario(account_id) or "").lower()
    except Exception:
        return ""


def _fila_video(r, plantilla: str, yo: str, account_id: int) -> Fila:
    (db_id, nombre, canal_vis, canal, duracion, publicado, vistas, likes, en_directo, nsfw, es_local, descripcion, idioma,
     uuid, url, cuenta, cuenta_host, instancia, video_id, privacidad) = r
    indicadores = []
    if en_directo:
        indicadores.append(_("En directo"))
    if nsfw:
        indicadores.append(_("+18"))
    indicadores.append(_("Local") if es_local else _("Remoto"))
    valores = {"title": nombre or "", "channel": canal_vis or canal or "",
               "duration": _("En directo") if en_directo else formatear_duracion_legible(duracion),
               "date": fecha_legible(publicado), "views": str(vistas or 0), "likes": str(likes or 0),
               "indicators": " ".join(indicadores), "description": (descripcion or "").replace("\n", " ").strip(),
               "language": idioma or ""}
    lectura = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
    return Fila(id=db_id, feed_id=0, feed_titulo=valores["channel"], titulo=lectura, autor=valores["channel"],
                fecha=publicado or "", leido=True, favorito=False, mas_tarde=False, audio=False, enlace=url or "",
                servicio="peertube",
                extra={"tipo": "video", "account_id": account_id, "uuid": uuid or "", "video_id": video_id,
                       "canal": canal or "", "instancia": instancia or "", "usuario": cuenta or "",
                       "cuenta_host": cuenta_host or "", "directo": bool(en_directo), "nsfw": bool(nsfw),
                       "privacidad": privacidad, "nombre": nombre or "",
                       "propio": bool(cuenta) and not cuenta_host and cuenta.lower() == yo})


def listar(account_id: int, seccion: str, limite: int = None) -> List[Fila]:
    if seccion == "notificaciones":
        return _listar_notificaciones(account_id)
    if seccion == "listas":
        return _listar_listas(account_id)
    if seccion == "mis_canales":
        return _listar_canales(account_id)
    limite = limite or limite_render("peertube", "max_items_buffer")
    plantilla, yo = _plantilla("peertube_video"), _yo(account_id)
    try:
        rows = db.query(f"SELECT {_COLS_VIDEO} FROM peertube_videos v JOIN peertube_video_buffers b ON v.id = b.video_db_id "
                        "WHERE b.account_id = ? AND b.buffer = ? ORDER BY b.position ASC LIMIT ?",
                        (account_id, seccion, limite or 100000))
    except Exception as e:
        log.error(f"Error al listar la sección '{seccion}' de PeerTube: {e}")
        return []
    from app.core.content_filters import ContentFilterEngine
    filtros = ContentFilterEngine.cargar("peertube")
    filas = []
    for r in rows:
        if filtros and ContentFilterEngine.evaluar("peertube", f"{r[1] or ''}. {r[11] or ''}", r[1] or "", filtros)["hide"]:
            continue
        filas.append(_fila_video(r, plantilla, yo, account_id))
    return filas


def _listar_notificaciones(account_id: int) -> List[Fila]:
    plantilla = _plantilla("peertube_notificacion")
    filas = []
    for n in cache(account_id, "notificaciones"):
        valores = {"descripcion": n.get("descripcion", ""), "date": fecha_legible(n.get("fecha", "")),
                   "estado": _("Leída") if n.get("leida") else _("Nueva")}
        filas.append(Fila(id="notif:" + str(n.get("id")), feed_id=0, feed_titulo="", titulo=pulir_lectura(
            plantilla.format_map(defaultdict(str, valores))), autor="", fecha=n.get("fecha", ""),
            leido=bool(n.get("leida")), favorito=False, mas_tarde=False, audio=False, enlace="", servicio="peertube",
            extra={"tipo": "notificacion", "account_id": account_id, "notif_id": n.get("id"), "uuid": n.get("video_uuid") or "",
                   "descripcion": n.get("descripcion", ""), "propio": False}))
    return filas


def _listar_listas(account_id: int) -> List[Fila]:
    filas = []
    for p in cache(account_id, "listas"):
        linea = _("{0}: {1} vídeos").format(p.get("display", ""), p.get("num_videos", 0))
        if p.get("es_ver_mas_tarde"):
            linea += ". " + _("Es tu lista de ver más tarde")
        filas.append(Fila(id=f"lista:{p['id']}", feed_id=0, feed_titulo="", titulo=linea, autor="", fecha="", leido=True,
                          favorito=False, mas_tarde=False, audio=False, enlace="", servicio="peertube",
                          extra={"tipo": "lista", "account_id": account_id, "lista": p, "propio": False}))
    return filas


def _listar_canales(account_id: int) -> List[Fila]:
    filas = []
    for c in cache(account_id, "mis_canales"):
        linea = _("{0}: {1} seguidores").format(c.get("display", ""), c.get("followers", 0))
        filas.append(Fila(id=f"canal:{c['id']}", feed_id=0, feed_titulo="", titulo=linea, autor="", fecha="", leido=True,
                          favorito=False, mas_tarde=False, audio=False, enlace="", servicio="peertube",
                          extra={"tipo": "canal", "account_id": account_id, "canal": c, "propio": False}))
    return filas


# ---------------------------------------------------------------------------------------------- detalle
def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de PeerTube para el panel de lectura."""
    x = fila.extra
    tipo = x.get("tipo")
    if tipo == "notificacion":
        return (x.get("descripcion") or fila.titulo, fecha_legible(fila.fecha), "", x.get("descripcion") or fila.titulo)
    if tipo == "lista":
        p = x["lista"]
        return (p.get("display", ""), _("{0} vídeos").format(p.get("num_videos", 0)), "",
                p.get("descripcion") or _("Intro abre la lista de reproducción."))
    if tipo == "canal":
        c = x["canal"]
        return (c.get("display", ""), _("{0} seguidores").format(c.get("followers", 0)), "",
                c.get("description") or _("Intro abre las publicaciones del canal."))
    r = db.query_one("SELECT name, channel_display, channel_name, duration, published_at, views, likes, dislikes, is_live, "
                     "nsfw, is_local, description, language, account_name, account_host FROM peertube_videos WHERE id = ?",
                     (fila.id,))
    if not r:
        return (fila.titulo, "", fila.enlace, "")
    nombre, canal_vis, canal, duracion, publicado, vistas, likes, dislikes, directo, nsfw, local, descripcion, idioma, \
        cuenta, cuenta_host = r
    meta = " · ".join(p for p in (_("Canal: {0}").format(canal_vis or canal) if (canal_vis or canal) else "",
                                  fecha_legible(publicado),
                                  _("En directo") if directo else formatear_duracion_legible(duracion)) if p)
    partes = []
    if descripcion:
        partes.append(descripcion)
    estado = [_("En directo")] if directo else []
    if nsfw:
        estado.append(_("Contenido +18"))
    estado.append(_("Vídeo local") if local else _("Vídeo remoto"))
    partes.append("\n" + _("{0} visualizaciones, {1} me gusta, {2} no me gusta").format(vistas or 0, likes or 0, dislikes or 0))
    partes.append(", ".join(estado))
    if cuenta:
        partes.append(_("Cuenta: {0}").format(f"{cuenta}@{cuenta_host}" if cuenta_host else cuenta))
    return (nombre or fila.titulo, meta, fila.enlace, "\n".join(partes))


def texto_plano(fila: Fila) -> str:
    titulo, _meta, _enlace, cuerpo = detalle(fila)
    return (titulo + "\n\n" + cuerpo).strip()


def handle_canal(fila: Fila) -> str:
    """Identificador nombre@instancia del canal de un vídeo."""
    x = fila.extra
    canal = x.get("canal") or ""
    if not canal:
        return ""
    return canal if "@" in canal else (f"{canal}@{x['instancia']}" if x.get("instancia") else canal)


def handle_cuenta(fila: Fila) -> str:
    x = fila.extra
    cuenta = x.get("usuario") or ""
    return f"{cuenta}@{x['cuenta_host']}" if cuenta and x.get("cuenta_host") else cuenta
