# -*- coding: utf-8 -*-
"""Datos de Telegram para la interfaz GTK: árbol de cuentas y buffers, listados y detalle.

Como con Mastodon y Bluesky, el servicio descarga y cachea en SQLite; aquí solo se LEE esa caché."""

import json
import logging
import os
from typing import Dict, List, Optional

from app.core.database import db
from .datos import Nodo, Fila, buffer_oculto
from .lectura import con_plantilla

log = logging.getLogger(__name__)

# (clave, título). Las conversaciones abiertas cuelgan de la cuenta como buffers "chat:<id>".
BUFFERS = [
    ("chats", "Chats"),
    ("unread", "Chats sin leer"),
]

EXTRA: Dict[int, List[Nodo]] = {}

TIPOS_CHAT = {"private": "Privado", "bot": "Bot", "group": "Grupo", "supergroup": "Supergrupo", "channel": "Canal"}

ETIQUETAS_MEDIA = {
    "voice": "Nota de voz", "audio": "Audio", "video": "Vídeo", "video_note": "Videomensaje", "photo": "Foto",
    "document": "Documento", "sticker": "Sticker", "animation": "GIF", "poll": "Encuesta", "web_page": "Enlace",
    "contact": "Contacto", "location": "Ubicación", "venue": "Lugar", "dice": "Emoji animado", "game": "Juego",
    "story": "Historia", "checklist": "Lista de tareas",
}
REPRODUCIBLES = ("voice", "audio", "video", "video_note")
DESCARGABLES = ("voice", "audio", "video", "video_note", "photo", "document", "animation", "sticker")


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'telegram' AND active = 1 ORDER BY position, id")]


def construir_nodos() -> Nodo:
    raiz = Nodo(_("Telegram"), "servicio", servicio="telegram")
    for acc_id, nombre in cuentas():
        cuenta = Nodo(nombre, "cuenta", servicio="telegram", account_id=acc_id)
        for clave, titulo in BUFFERS:
            if buffer_oculto("telegram", clave):
                continue
            cuenta.hijos.append(Nodo(_(titulo), "timeline", servicio="telegram", account_id=acc_id, timeline=clave))
        cuenta.hijos.extend(EXTRA.get(acc_id, []))
        raiz.hijos.append(cuenta)
    return raiz


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    for n in EXTRA.setdefault(account_id, []):
        if n.timeline == timeline:
            return n
    n = Nodo(titulo, "timeline", servicio="telegram", account_id=account_id, timeline=timeline, cerrable=True)
    EXTRA[account_id].append(n)
    return n


def cerrar_extra(nodo: Nodo):
    lista = EXTRA.get(nodo.account_id, [])
    if nodo in lista:
        lista.remove(nodo)


def id_de_chat(timeline: str) -> Optional[int]:
    """Id del chat de un buffer "chat:<id>" (None si el buffer no es una conversación)."""
    if timeline.startswith("chat:"):
        try:
            return int(timeline[5:])
        except ValueError:
            return None
    return None


def _condensar(texto: str, limite: int = 240) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


def duracion(segundos) -> str:
    try:
        s = int(segundos or 0)
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60}:{s % 60:02d}" if s > 0 else ""


def etiqueta_media(media_type: str, dur=0, nombre: str = "") -> str:
    """Descripción legible de un adjunto, con duración o nombre de archivo."""
    if not media_type:
        return ""
    base = _(ETIQUETAS_MEDIA.get(media_type, media_type))
    d = duracion(dur)
    if media_type in REPRODUCIBLES and d:
        return f"[{base} {d}]"
    if media_type in ("document", "audio") and nombre:
        return f"[{base}: {nombre}]"
    return f"[{base}]"


def titulo_chat(account_id: int, chat_id: int) -> str:
    f = db.query_one("SELECT COALESCE(title, '') FROM telegram_chats WHERE account_id = ? AND chat_id = ?",
                     (account_id, chat_id))
    return (f[0] if f else "") or str(chat_id)


def info_chat(account_id: int, chat_id: int) -> dict:
    f = db.query_one("SELECT COALESCE(title, ''), COALESCE(chat_type, ''), COALESCE(muted, 0), COALESCE(pinned, 0), "
                     "COALESCE(unread_count, 0), COALESCE(username, '') FROM telegram_chats "
                     "WHERE account_id = ? AND chat_id = ?", (account_id, chat_id))
    if not f:
        return {}
    return {"titulo": f[0], "tipo": f[1], "silenciado": bool(f[2]), "fijado": bool(f[3]), "no_leidos": f[4],
            "usuario": f[5]}


def listar(account_id: int, timeline: str, limite: int = 300) -> List[Fila]:
    cid = id_de_chat(timeline)
    if cid is not None:
        return _listar_mensajes(account_id, cid, limite)
    return _listar_chats(account_id, solo_no_leidos=(timeline == "unread"), limite=limite)


def _listar_chats(account_id: int, solo_no_leidos: bool, limite: int) -> List[Fila]:
    where = "AND COALESCE(unread_count, 0) > 0 " if solo_no_leidos else ""
    try:
        rows = db.query(
            "SELECT chat_id, COALESCE(title, ''), COALESCE(chat_type, ''), COALESCE(username, ''), "
            "COALESCE(unread_count, 0), COALESCE(last_message, ''), COALESCE(last_message_date, ''), "
            "COALESCE(pinned, 0), COALESCE(muted, 0), COALESCE(verified, 0), COALESCE(last_media_type, '') "
            f"FROM telegram_chats WHERE account_id = ? {where}"
            "ORDER BY pinned DESC, last_message_date DESC LIMIT ?", (account_id, limite))
    except Exception as e:
        log.error(f"Error al listar los chats de Telegram: {e}")
        return []
    filas = []
    for cid, titulo, tipo, usuario, no_leidos, ultimo, fecha, fijado, silenciado, verificado, medio in rows:
        nombre = titulo or usuario or str(cid)
        partes = []
        if no_leidos:
            partes.append(_("{n} sin leer").format(n=no_leidos))
        partes.append(nombre + (" " + _("(verificado)") if verificado else ""))
        resumen = _condensar(ultimo, 160) or etiqueta_media(medio)
        texto = ". ".join(partes) + (f": {resumen}" if resumen else "")
        if fijado:
            texto += " · " + _("fijado")
        if silenciado:
            texto += " · " + _("silenciado")
        if tipo in TIPOS_CHAT:
            texto += " · " + _(TIPOS_CHAT[tipo])
        filas.append(con_plantilla(
            Fila(id=str(cid), feed_id=0, feed_titulo=nombre, titulo=texto, autor=nombre, fecha=fecha,
                 leido=not no_leidos, favorito=False, mas_tarde=False, audio=False, enlace="",
                 servicio="telegram",
                 extra={"tipo": "chat", "account_id": account_id, "chat_id": cid, "titulo": nombre,
                        "chat_tipo": tipo, "usuario": usuario, "silenciado": bool(silenciado),
                        "fijado": bool(fijado), "no_leidos": no_leidos, "propio": False}),
            "telegram_chat", {
                "chat": nombre, "type": _(TIPOS_CHAT[tipo]) if tipo in TIPOS_CHAT else "", "content": resumen,
                "unread": _("{n} sin leer").format(n=no_leidos) if no_leidos else "",
                "silenciado": _("Silenciado") if silenciado else "", "fijado": _("Fijado") if fijado else "",
                "verificado": _("Verificado") if verificado else "", "protegido": ""}))
    return filas


_COLS_MSG = ("message_id, COALESCE(sender_name, ''), COALESCE(sender_username, ''), COALESCE(text, ''), "
             "COALESCE(date, ''), COALESCE(outgoing, 0), COALESCE(media_type, ''), COALESCE(media_path, ''), "
             "COALESCE(duration, 0), COALESCE(file_name, ''), COALESCE(edited, 0), COALESCE(reply_to, 0), "
             "COALESCE(forward_from, ''), COALESCE(views, 0), COALESCE(reply_markup, ''), "
             "COALESCE(media_group_id, 0), COALESCE(reactions, ''), COALESCE(urls, '')")


def _fila_mensaje(r, account_id: int, chat_id: int, leido_hasta: int) -> Fila:
    (mid, remitente, usuario, texto, fecha, saliente, medio, ruta, dur, nombre_archivo, editado, responde,
     reenviado, vistas, botones, album, reacciones, urls) = r
    quien = _("Yo") if saliente else (remitente or _("Desconocido"))
    adjunto = etiqueta_media(medio, dur, nombre_archivo)
    cuerpo = _condensar(texto, 400)
    linea = f"{quien}: {cuerpo}" if cuerpo else f"{quien}:"
    if adjunto:
        linea += " " + adjunto
        if medio in REPRODUCIBLES:
            linea += " " + _("(F2 para reproducir)")
    if reenviado:
        linea += " · " + _("Reenviado de {n}").format(n=reenviado)
    if responde:
        linea += " · " + _("Respuesta")
    if editado:
        linea += " · " + _("Editado")
    if reacciones:
        linea += " · " + _("reacciones: {r}").format(r=reacciones)
    if botones:
        linea += " · " + _("con botones")
    if vistas:
        linea += " · " + _("{n} vistas").format(n=vistas)
    descargado = bool(medio and ruta and os.path.exists(ruta))
    nuevo = (not saliente) and mid > leido_hasta > 0
    if nuevo:
        linea = _("Nuevo. ") + linea
    fila = Fila(id=f"{account_id}:{chat_id}:{mid}", feed_id=0, feed_titulo=quien, titulo=linea, autor=quien,
                fecha=fecha, leido=not nuevo, favorito=False, mas_tarde=False, audio=medio in REPRODUCIBLES,
                enlace="", servicio="telegram",
                extra={"tipo": "mensaje", "account_id": account_id, "chat_id": chat_id, "message_id": mid,
                       "usuario": usuario, "propio": bool(saliente), "texto": texto, "media_type": medio,
                       "media_path": ruta if descargado else "", "descargado": descargado,
                       "reproducible": medio in REPRODUCIBLES, "descargable": medio in DESCARGABLES,
                       "nombre_archivo": nombre_archivo, "responde_a": responde, "nuevo": nuevo,
                       "album": album, "urls": urls})
    adjunto_txt = adjunto + (" " + _("(F2 para reproducir)") if adjunto and medio in REPRODUCIBLES else "")
    return con_plantilla(fila, "telegram_message", {
        "author": quien, "username": f"@{usuario}" if usuario else "", "content": cuerpo, "media": adjunto_txt,
        "descarga": _("Descargado") if descargado else "", "flags": _("Editado") if editado else "",
        "respuesta": _("Respuesta") if responde else "",
        "reenviado": _("Reenviado de {n}").format(n=reenviado) if reenviado else "",
        "vistas": _("{n} vistas").format(n=vistas) if vistas else "", "reactions": reacciones or "",
        "botones": _("Con botones") if botones else "", "album": _("Álbum") if album else ""})


def leido_hasta(account_id: int, chat_id: int) -> int:
    try:
        f = db.query_one("SELECT COALESCE(last_read_message_id, 0) FROM telegram_chats "
                         "WHERE account_id = ? AND chat_id = ?", (account_id, chat_id))
    except Exception:
        return 0
    return (f[0] if f else 0) or 0


def guardar_leido_hasta(account_id: int, chat_id: int, mensaje_id: int):
    try:
        db.execute("UPDATE telegram_chats SET last_read_message_id = ? WHERE account_id = ? AND chat_id = ?",
                   (int(mensaje_id), account_id, chat_id))
    except Exception as e:
        log.debug(f"No se pudo guardar el punto de lectura de Telegram: {e}")


def ultimo_mensaje_id(account_id: int, chat_id: int) -> int:
    f = db.query_one("SELECT COALESCE(MAX(message_id), 0) FROM telegram_messages WHERE account_id = ? AND chat_id = ?",
                     (account_id, chat_id))
    return (f[0] if f else 0) or 0


def mensaje_mas_antiguo_id(account_id: int, chat_id: int) -> int:
    f = db.query_one("SELECT COALESCE(MIN(message_id), 0) FROM telegram_messages WHERE account_id = ? AND chat_id = ?",
                     (account_id, chat_id))
    return (f[0] if f else 0) or 0


def _listar_mensajes(account_id: int, chat_id: int, limite: int) -> List[Fila]:
    try:
        rows = db.query(f"SELECT {_COLS_MSG} FROM telegram_messages WHERE account_id = ? AND chat_id = ? "
                        "ORDER BY message_id DESC LIMIT ?", (account_id, chat_id, limite))
    except Exception as e:
        log.error(f"Error al listar los mensajes de Telegram: {e}")
        return []
    rows.reverse()      # cronológico: lo más reciente al final, como en una conversación
    hasta = leido_hasta(account_id, chat_id)
    return [_fila_mensaje(r, account_id, chat_id, hasta) for r in rows]


def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de Telegram para el panel de lectura."""
    from .datos import fecha_legible
    x = fila.extra
    if x.get("tipo") == "chat":
        info = []
        if x.get("chat_tipo") in TIPOS_CHAT:
            info.append(_(TIPOS_CHAT[x["chat_tipo"]]))
        if x.get("usuario"):
            info.append(f"@{x['usuario'].lstrip('@')}")
        return (fila.autor, " · ".join(info + [fecha_legible(fila.fecha)]), "",
                fila.titulo + "\n\n" + _("Pulsa Intro para abrir la conversación."))
    r = db.query_one(f"SELECT {_COLS_MSG} FROM telegram_messages WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                     (x["account_id"], x["chat_id"], x["message_id"]))
    if not r:
        return (fila.autor, fecha_legible(fila.fecha), "", fila.titulo)
    (mid, remitente, usuario, texto, fecha, saliente, medio, ruta, dur, nombre_archivo, editado, responde,
     reenviado, vistas, botones, album, reacciones, urls) = r
    quien = _("Yo") if saliente else (remitente or _("Desconocido"))
    meta = [quien + (f" ({usuario})" if usuario and not saliente else ""), fecha_legible(fecha)]
    if editado:
        meta.append(_("editado"))
    partes = []
    if reenviado:
        partes.append(_("Reenviado de {n}").format(n=reenviado))
    if responde:
        original = db.query_one("SELECT COALESCE(sender_name, ''), COALESCE(text, ''), COALESCE(media_type, '') "
                                "FROM telegram_messages WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                                (x["account_id"], x["chat_id"], responde))
        if original:
            partes.append(_("En respuesta a {n}: {t}").format(
                n=original[0] or _("Desconocido"), t=_condensar(original[1], 160) or etiqueta_media(original[2])))
        else:
            partes.append(_("Es una respuesta a otro mensaje."))
    if texto:
        partes.append(texto)
    if medio:
        et = etiqueta_media(medio, dur, nombre_archivo)
        estado = _("descargado") if (ruta and os.path.exists(ruta)) else (
            _("sin descargar") if medio in DESCARGABLES else "")
        partes.append(et + (f" ({estado})" if estado else ""))
    try:
        enlaces = json.loads(urls) if urls else []
    except Exception:
        enlaces = []
    if enlaces:
        partes.append(_("Enlaces:") + "\n" + "\n".join(enlaces))
    if reacciones:
        partes.append(_("Reacciones: {r}").format(r=reacciones))
    if botones:
        partes.append(_("Este mensaje tiene botones de bot."))
    if vistas:
        partes.append(_("{n} vistas").format(n=vistas))
    return (quien, " · ".join(p for p in meta if p), "", "\n\n".join(partes))


def seleccion_inicial(filas: List[Fila]) -> int:
    """Al abrir una conversación: el primer mensaje nuevo o, si no hay, el último."""
    for i, f in enumerate(filas):
        if f.extra.get("nuevo"):
            return i
    return max(0, len(filas) - 1)
