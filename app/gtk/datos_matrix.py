# -*- coding: utf-8 -*-
"""Datos de Matrix para la interfaz GTK: árbol de cuentas y buffers, listados y detalle.

El servicio guarda todo en SQLite (matrix_salas, matrix_mensajes); aquí solo se LEE esa caché. El servicio
(matrix-nio, cifrado...) pesa, así que no se importa hasta que hace falta."""

import json
import logging
import mimetypes
import re
import sys
from typing import Dict, List, Optional

from app.core.database import db
from .datos import Nodo, Fila, buffer_oculto
from .lectura import con_plantilla, hora

log = logging.getLogger(__name__)

# (clave, título). Las conversaciones abiertas cuelgan de la cuenta como buffers "chat:<room_id>".
BUFFERS = [
    ("rooms", "Salas"),
    ("dms", "Mensajes directos"),
    ("invites", "Invitaciones"),
]

EXTRA: Dict[int, List[Nodo]] = {}


def servicio():
    from app.services.matrix import matrix_service
    return matrix_service


def cargado() -> bool:
    return "app.services.matrix.matrix_service" in sys.modules


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'matrix' AND active = 1 ORDER BY position, id")]


def conectada(account_id: int) -> bool:
    if not cargado():           # sin importar el servicio no puede haber conexión
        return False
    try:
        return servicio().esta_conectada(account_id)
    except Exception:
        return False


def construir_nodos() -> Nodo:
    raiz = Nodo(_("Matrix"), "servicio", servicio="matrix")
    for acc_id, nombre in cuentas():
        titulo = nombre if conectada(acc_id) else _("{n} (desconectada)").format(n=nombre)
        cuenta = Nodo(titulo, "cuenta", servicio="matrix", account_id=acc_id)
        for clave, etiqueta in BUFFERS:
            if buffer_oculto("matrix", clave):
                continue
            cuenta.hijos.append(Nodo(_(etiqueta), "timeline", servicio="matrix", account_id=acc_id, timeline=clave))
        cuenta.hijos.extend(EXTRA.get(acc_id, []))
        raiz.hijos.append(cuenta)
    return raiz


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    for n in EXTRA.setdefault(account_id, []):
        if n.timeline == timeline:
            return n
    n = Nodo(titulo, "timeline", servicio="matrix", account_id=account_id, timeline=timeline, cerrable=True)
    EXTRA[account_id].append(n)
    return n


def cerrar_extra(nodo: Nodo):
    lista = EXTRA.get(nodo.account_id, [])
    if nodo in lista:
        lista.remove(nodo)


def sala_de_chat(timeline: str) -> Optional[str]:
    """room_id de un buffer "chat:<room_id>"; None si el buffer no es una conversación."""
    if timeline.startswith("chat:") and len(timeline) > 5:
        return timeline[5:]
    return None


def clave_chat(room_id: str) -> str:
    return "chat:" + room_id


def db_id(account_id: int, room_id: str) -> str:
    return f"{account_id}|{room_id}"


def info_sala(account_id: int, room_id: str) -> dict:
    f = db.query_one(
        "SELECT COALESCE(nombre,''), COALESCE(tema,''), COALESCE(es_dm,0), COALESCE(unread_count,0), "
        "COALESCE(notify_level,'all'), COALESCE(invitacion,0), COALESCE(cifrada,0), COALESCE(es_espacio,0), "
        "COALESCE(etiqueta,'') FROM matrix_salas WHERE id = ?", (db_id(account_id, room_id),))
    if not f:
        return {"nombre": room_id, "tema": "", "dm": False, "no_leidos": 0, "aviso": "all", "invitacion": False,
                "cifrada": False, "espacio": False, "etiqueta": ""}
    return {"nombre": f[0] or room_id, "tema": f[1], "dm": bool(f[2]), "no_leidos": f[3], "aviso": f[4],
            "invitacion": bool(f[5]), "cifrada": bool(f[6]), "espacio": bool(f[7]), "etiqueta": f[8]}


def titulo_sala(account_id: int, room_id: str) -> str:
    return info_sala(account_id, room_id)["nombre"]


def _condensar(texto: str, limite: int = 240) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


def listar(account_id: int, timeline: str, limite: int = 300) -> List[Fila]:
    room_id = sala_de_chat(timeline)
    if room_id is not None:
        return _listar_mensajes(account_id, room_id, limite)
    return _listar_salas(account_id, timeline)


_ORDEN_SALAS = ("ORDER BY CASE COALESCE(etiqueta,'') WHEN 'favorita' THEN 0 WHEN 'baja' THEN 2 ELSE 1 END, "
                "es_espacio DESC, unread_count DESC, last_message_date DESC, nombre")


def _listar_salas(account_id: int, timeline: str) -> List[Fila]:
    cond = {"invites": "invitacion = 1", "dms": "invitacion = 0 AND es_dm = 1"}.get(timeline, "invitacion = 0 AND es_dm = 0")
    try:
        rows = db.query(
            "SELECT room_id, nombre, tema, unread_count, last_message, invitacion, COALESCE(cifrada,0), "
            "COALESCE(es_espacio,0), COALESCE(etiqueta,''), COALESCE(es_dm,0) FROM matrix_salas "
            f"WHERE account_id = ? AND {cond} {_ORDEN_SALAS}", (account_id,))
    except Exception as e:
        log.error(f"Error al listar las salas de Matrix: {e}")
        return []
    filas = []
    for room_id, nombre, tema, no_leidos, ultimo, invitada, cifrada, espacio, etiqueta, dm in rows:
        nombre = nombre or room_id
        no_leidos = no_leidos or 0
        partes = []
        if no_leidos and not invitada:
            partes.append(_("{n} sin leer").format(n=no_leidos))
        partes.append(nombre)
        texto = ". ".join(partes)
        if invitada:
            texto += " " + _("(invitación)")
        elif _condensar(ultimo, 160):
            texto += ": " + _condensar(ultimo, 160)
        extras = []
        if espacio:
            extras.append(_("espacio"))
        if cifrada:
            extras.append(_("cifrada"))
        if etiqueta == "favorita":
            extras.append(_("favorita"))
        elif etiqueta == "baja":
            extras.append(_("baja prioridad"))
        if tema and not dm:
            extras.append(_condensar(tema, 100))
        if extras:
            texto += " · " + ", ".join(extras)
        marcas = [m for m in extras if m != _condensar(tema, 100)] if extras else []
        filas.append(con_plantilla(
            Fila(id=room_id, feed_id=0, feed_titulo=nombre, titulo=texto, autor=nombre, fecha="",
                 leido=not no_leidos, favorito=False, mas_tarde=False, audio=False, enlace="", servicio="matrix",
                 extra={"tipo": "invitacion" if invitada else "sala", "account_id": account_id,
                        "room_id": room_id, "nombre": nombre, "dm": bool(dm), "cifrada": bool(cifrada),
                        "espacio": bool(espacio), "etiqueta": etiqueta, "no_leidos": no_leidos,
                        "propio": False}),
            "matrix_chat", {"sala": nombre + (" (" + ", ".join(marcas) + ")" if marcas else "")
                            + (" " + _("(invitación)") if invitada else ""),
                            "content": "" if invitada else _condensar(ultimo, 160),
                            "unread": _("{n} sin leer").format(n=no_leidos) if no_leidos and not invitada else "",
                            "topic": _condensar(tema, 100) if tema and not dm else ""}))
    return filas


_SQL_MSG = ("SELECT id, COALESCE(NULLIF(sender_nombre,''), sender), COALESCE(texto,''), COALESCE(tipo,'msg'), "
            "COALESCE(date,''), COALESCE(es_propio,0), event_id, COALESCE(media_mxc,''), COALESCE(reacciones,''), "
            "COALESCE(editado,0), COALESCE(reply_to,''), COALESCE(media_crypto,''), COALESCE(thread_root,''), "
            "COALESCE(poll_json,''), COALESCE(sender,''), COALESCE(send_state,''), COALESCE(leido,0), "
            "COALESCE(highlight,0) FROM matrix_mensajes WHERE account_id = ? AND room_id = ?")

_PREFIJO_ADJUNTO = re.compile(r"^\[[^:\]]+:\s*(.+)\]$")


def nombre_adjunto(texto: str, mxc: str = "") -> str:
    """Nombre real del archivo de la etiqueta «[Tipo: nombre.ext]» (si no, el id del mxc)."""
    m = _PREFIJO_ADJUNTO.match((texto or "").strip())
    if m:
        return m.group(1).strip()
    return (mxc or "adjunto").rsplit("/", 1)[-1]


def tipo_adjunto(texto: str, mxc: str) -> str:
    """voz|audio|imagen|video|archivo según el nombre del archivo y, si no basta, la etiqueta."""
    if not mxc:
        return ""
    mime = mimetypes.guess_type(nombre_adjunto(texto, mxc))[0] or ""
    t = (texto or "").lstrip()
    if mime.startswith("audio/") or t.startswith(_("[Mensaje de audio:")) or t.startswith("[Audio:"):
        return "audio"
    if mime.startswith("video/") or t.startswith(_("[Vídeo:")):
        return "video"
    if mime.startswith("image/") or t.startswith(_("[Imagen:")):
        return "imagen"
    return "archivo"


ETIQUETAS_ADJUNTO = {"audio": "Mensaje de audio", "imagen": "Imagen", "video": "Vídeo", "archivo": "Archivo"}


def _texto_de_respuesta(account_id: int, event_id: str) -> str:
    f = db.query_one("SELECT COALESCE(NULLIF(sender_nombre,''), sender), COALESCE(texto,'') FROM matrix_mensajes "
                     "WHERE account_id = ? AND event_id = ? LIMIT 1", (account_id, event_id))
    if not f:
        return ""
    cuerpo = _condensar(f[1], 60)
    return f"{f[0]}: {cuerpo}" if cuerpo else (f[0] or "")


def urls_de(texto: str) -> List[str]:
    """URLs (http/https) del texto, en orden y sin repetir."""
    vistas, salida = set(), []
    for u in re.findall(r'https?://[^\s<>"\')\]}]+', texto or ""):
        u = u.rstrip(".,;:!?")
        if u and u not in vistas:
            vistas.add(u)
            salida.append(u)
    return salida


def _fila_mensaje(r, account_id: int, room_id: str) -> Fila:
    (mid, quien, texto, tipo, fecha, propio, event_id, mxc, reacciones, editado, reply_to, crypto, hilo, poll_json,
     sender, send_state, leido, mencion) = r
    autor = _("Yo") if propio else (quien or _("Desconocido"))
    adjunto = tipo_adjunto(texto, mxc) if tipo == "media" else ""
    cuerpo = ("* " + autor + " " + texto) if tipo == "accion" else texto
    extras = []
    if reply_to:
        cita = _texto_de_respuesta(account_id, reply_to)
        extras.append(_("(en respuesta a: {c})").format(c=cita) if cita else _("(respuesta)"))
    if hilo:
        extras.append(_("(en hilo)"))
    if editado:
        extras.append(_("(editado)"))
    if send_state == "fallido":
        extras.append(_("(SIN ENVIAR: usa el menú para reintentar)"))
    if reacciones:
        extras.append(_("[Reacciones: {r}]").format(r=reacciones))
    if adjunto in ("audio", "video"):
        extras.append(_("(F2 para reproducir)"))
    elif adjunto:
        extras.append(_("(Ctrl+Alt+D para descargar)"))
    elif tipo == "encuesta":
        extras.append(_("(Ctrl+Alt+V para votar)"))
    elif urls_de(texto):
        extras.append(_("(Intro para abrir el enlace)"))
    if mencion and not propio:
        extras.append(_("(te mencionan)"))
    nuevo = not leido and not propio
    linea = f"{autor}: {' '.join([cuerpo] + extras).strip()}" if tipo != "accion" else " ".join([cuerpo] + extras)
    if nuevo:
        linea = _("Nuevo. ") + linea
    fila = Fila(id=str(mid), feed_id=0, feed_titulo=autor, titulo=linea, autor=autor, fecha=fecha, leido=not nuevo,
                favorito=False, mas_tarde=False, audio=adjunto == "audio", enlace="", servicio="matrix",
                extra={"tipo": "mensaje", "account_id": account_id, "room_id": room_id, "mid": mid,
                       "event_id": event_id or "", "texto": texto, "tipo_msg": tipo, "media_mxc": mxc,
                       "media_crypto": crypto, "adjunto": adjunto, "thread_root": hilo, "poll_json": poll_json,
                       "sender": sender, "send_state": send_state, "reply_to": reply_to, "propio": bool(propio),
                       "nuevo": nuevo, "usuario": quien, "reproducible": adjunto in ("audio", "video"),
                       "descargable": bool(adjunto)})
    if tipo == "accion":
        return fila
    return con_plantilla(fila, "matrix_message", {
        "author": autor, "content": " ".join([cuerpo] + extras).strip(), "hora": hora(fecha), "sala": ""})


def _listar_mensajes(account_id: int, room_id: str, limite: int) -> List[Fila]:
    try:
        rows = db.query(_SQL_MSG + " ORDER BY id DESC LIMIT ?", (account_id, room_id, limite))
    except Exception as e:
        log.error(f"Error al listar los mensajes de Matrix: {e}")
        return []
    rows.reverse()      # cronológico: lo más reciente al final, como en una conversación
    return [_fila_mensaje(r, account_id, room_id) for r in rows]


def encuesta(poll_json: str) -> Optional[dict]:
    """Datos de una encuesta guardada: {pregunta, opciones: {id: texto}, votos, cerrada, oculta}."""
    try:
        d = json.loads(poll_json) if poll_json else None
    except Exception:
        return None
    return d if isinstance(d, dict) and d.get("opciones") else None


def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de Matrix para el panel de lectura."""
    from .datos import fecha_legible
    x = fila.extra
    if x.get("tipo") in ("sala", "invitacion"):
        info = info_sala(x["account_id"], x["room_id"])
        meta = [x["room_id"]]
        lineas = [fila.titulo]
        if info["tema"]:
            lineas.append(_("Tema: {t}").format(t=info["tema"]))
        if info["cifrada"]:
            lineas.append(_("Sala cifrada de extremo a extremo."))
        lineas.append(_("Pulsa Intro para aceptar o rechazar la invitación.") if x["tipo"] == "invitacion"
                      else _("Pulsa Intro para abrir la conversación."))
        return (fila.autor, " · ".join(meta), "", "\n\n".join(lineas))
    quien = _("Yo") if x.get("propio") else (x.get("usuario") or _("Desconocido"))
    meta = [quien, fecha_legible(fila.fecha)]
    partes = []
    if x.get("reply_to"):
        cita = _texto_de_respuesta(x["account_id"], x["reply_to"])
        if cita:
            partes.append(_("En respuesta a {c}").format(c=cita))
    partes.append(x.get("texto") or "")
    r = db.query_one("SELECT COALESCE(reacciones,'') FROM matrix_mensajes WHERE id = ?", (x["mid"],))
    if r and r[0]:
        partes.append(_("Reacciones: {r}").format(r=r[0]))
    urls = urls_de(x.get("texto", ""))
    return (quien, " · ".join(p for p in meta if p), urls[0] if urls else "", "\n\n".join(p for p in partes if p))


def seleccion_inicial(filas: List[Fila]) -> int:
    """Al abrir una conversación: el primer mensaje nuevo o, si no hay, el último."""
    for i, f in enumerate(filas):
        if f.extra.get("nuevo"):
            return i
    return max(0, len(filas) - 1)


def marcar_leido(account_id: int, room_id: str):
    """Pone a cero los no leídos de una sala (local; el marcador de lectura al servidor lo manda el servicio)."""
    try:
        db.execute("UPDATE matrix_salas SET unread_count = 0 WHERE account_id = ? AND room_id = ?", (account_id, room_id))
        db.execute("UPDATE matrix_mensajes SET leido = 1 WHERE account_id = ? AND room_id = ?", (account_id, room_id))
    except Exception as e:
        log.debug(f"No se pudo marcar leída la sala de Matrix: {e}")


def marcar_no_leido(account_id: int, room_id: str):
    db.execute("UPDATE matrix_salas SET unread_count = MAX(COALESCE(unread_count, 0), 1) WHERE id = ?",
               (db_id(account_id, room_id),))


def marcar_todo_leido(account_id: int):
    db.execute("UPDATE matrix_salas SET unread_count = 0 WHERE account_id = ?", (account_id,))
    db.execute("UPDATE matrix_mensajes SET leido = 1 WHERE account_id = ?", (account_id,))


def ultimo_ajeno(account_id: int, room_id: str) -> Optional[tuple]:
    """(autor, texto, tipo, mención, id) del último mensaje recibido en la sala, para anunciarlo por voz."""
    f = db.query_one("SELECT COALESCE(NULLIF(sender_nombre,''), sender), COALESCE(texto,''), COALESCE(tipo,'msg'), "
                     "COALESCE(highlight,0), id FROM matrix_mensajes WHERE account_id = ? AND room_id = ? AND es_propio = 0 "
                     "ORDER BY id DESC LIMIT 1", (account_id, room_id))
    return f if f else None


def ultimo_id(account_id: Optional[int] = None) -> int:
    """Mayor id de mensaje guardado (de una cuenta o de todas): marca desde la que se anuncian las novedades."""
    f = (db.query_one("SELECT COALESCE(MAX(id), 0) FROM matrix_mensajes WHERE account_id = ?", (account_id,))
         if account_id is not None else db.query_one("SELECT COALESCE(MAX(id), 0) FROM matrix_mensajes"))
    return f[0] if f else 0
