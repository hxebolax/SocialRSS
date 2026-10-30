# -*- coding: utf-8 -*-
"""Datos de IRC para la interfaz GTK: árbol de redes y buffers, listados y detalle.

El servicio guarda todo lo que ocurre en SQLite (irc_channels, irc_messages); aquí solo se LEE esa caché."""

import logging
from typing import Dict, List, Optional

from app.core.database import db
from app.services.irc.irc_service import irc_service
from .datos import Nodo, Fila, buffer_oculto
from .lectura import con_plantilla, hora

log = logging.getLogger(__name__)

# (clave, título). Las conversaciones abiertas cuelgan de la red como buffers "chat:<nombre en minúsculas>".
BUFFERS = [
    ("channels", "Canales"),
    ("privates", "Privados"),
    ("server", "Avisos del servidor"),
]

EXTRA: Dict[int, List[Nodo]] = {}

SERVIDOR = "*"          # "canal" donde se guardan los avisos del servidor y de los servicios


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'irc' AND active = 1 ORDER BY position, id")]


def conectada(account_id: int) -> bool:
    try:
        return irc_service.esta_conectada(account_id)
    except Exception:
        return False


def construir_nodos() -> Nodo:
    raiz = Nodo(_("IRC"), "servicio", servicio="irc")
    for acc_id, nombre in cuentas():
        titulo = nombre if conectada(acc_id) else _("{n} (desconectada)").format(n=nombre)
        cuenta = Nodo(titulo, "cuenta", servicio="irc", account_id=acc_id)
        for clave, etiqueta in BUFFERS:
            if buffer_oculto("irc", clave):
                continue
            cuenta.hijos.append(Nodo(_(etiqueta), "timeline", servicio="irc", account_id=acc_id, timeline=clave))
        cuenta.hijos.extend(EXTRA.get(acc_id, []))
        raiz.hijos.append(cuenta)
    return raiz


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    for n in EXTRA.setdefault(account_id, []):
        if n.timeline == timeline:
            return n
    n = Nodo(titulo, "timeline", servicio="irc", account_id=account_id, timeline=timeline, cerrable=True)
    EXTRA[account_id].append(n)
    return n


def cerrar_extra(nodo: Nodo):
    lista = EXTRA.get(nodo.account_id, [])
    if nodo in lista:
        lista.remove(nodo)


def nombre_de_chat(timeline: str) -> Optional[str]:
    """Nombre (en minúsculas) del chat de un buffer "chat:<nombre>"; None si el buffer no es una conversación."""
    if timeline.startswith("chat:") and len(timeline) > 5:
        return timeline[5:]
    return None


def clave_chat(nombre: str) -> str:
    return "chat:" + (nombre or "").lower()


def es_canal(nombre: str) -> bool:
    return (nombre or "").startswith(("#", "&", "+", "!"))


def info_chat(account_id: int, nombre: str) -> dict:
    """Datos del chat tal como los guardó el servicio; el nombre conserva las mayúsculas de la red."""
    f = db.query_one("SELECT name, COALESCE(topic, ''), COALESCE(unread_count, 0), COALESCE(joined, 0), "
                     "COALESCE(es_privado, 0), COALESCE(notify_level, 'all') FROM irc_channels WHERE id = ?",
                     (_id_chat(account_id, nombre),))
    if not f:
        return {"nombre": nombre, "tema": "", "no_leidos": 0, "unido": False, "privado": not es_canal(nombre),
                "aviso": "all"}
    return {"nombre": f[0], "tema": f[1], "no_leidos": f[2], "unido": bool(f[3]), "privado": bool(f[4]),
            "aviso": f[5]}


def _id_chat(account_id: int, nombre: str) -> str:
    return f"{account_id}|{(nombre or '').lower()}"


def titulo_chat(account_id: int, nombre: str) -> str:
    if nombre == SERVIDOR:
        return _("Avisos del servidor")
    return info_chat(account_id, nombre)["nombre"] or nombre


def _condensar(texto: str, limite: int = 240) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


def listar(account_id: int, timeline: str, limite: int = 300) -> List[Fila]:
    nombre = nombre_de_chat(timeline)
    if nombre is not None:
        return _listar_mensajes(account_id, nombre, limite)
    if timeline == "server":
        return _listar_mensajes(account_id, SERVIDOR, limite)
    return _listar_chats(account_id, privados=(timeline == "privates"))


def _listar_chats(account_id: int, privados: bool) -> List[Fila]:
    try:
        rows = db.query(
            "SELECT name, COALESCE(topic, ''), COALESCE(unread_count, 0), COALESCE(joined, 0), "
            "COALESCE(last_message, ''), COALESCE(last_message_date, '') FROM irc_channels "
            "WHERE account_id = ? AND es_privado = ? AND name <> ? "
            "ORDER BY unread_count DESC, last_message_date DESC, name", (account_id, 1 if privados else 0, SERVIDOR))
    except Exception as e:
        log.error(f"Error al listar los chats de IRC: {e}")
        return []
    filas = []
    for nombre, tema, no_leidos, unido, ultimo, fecha in rows:
        partes = []
        if no_leidos:
            partes.append(_("{n} sin leer").format(n=no_leidos))
        partes.append(nombre if (unido or privados) else _("{n} (no unido)").format(n=nombre))
        texto = ". ".join(partes)
        if _condensar(ultimo, 160):
            texto += ": " + _condensar(ultimo, 160)
        if tema:
            texto += " · " + _("Tema: {t}").format(t=_condensar(tema, 120))
        filas.append(con_plantilla(
            Fila(id=nombre.lower(), feed_id=0, feed_titulo=nombre, titulo=texto, autor=nombre, fecha=fecha,
                 leido=not no_leidos, favorito=False, mas_tarde=False, audio=False, enlace="", servicio="irc",
                 extra={"tipo": "chat", "account_id": account_id, "chat": nombre, "titulo": nombre,
                        "privado": privados, "unido": bool(unido), "tema": tema, "no_leidos": no_leidos,
                        "propio": False}),
            "irc_chat", {"chat": nombre if (unido or privados) else _("{n} (no unido)").format(n=nombre),
                         "content": _condensar(ultimo, 160),
                         "unread": _("{n} sin leer").format(n=no_leidos) if no_leidos else "",
                         "topic": _condensar(tema, 120)}))
    return filas


def _texto_evento(nick: str, texto: str, tipo: str, propio: bool) -> str:
    """Línea legible de un mensaje o evento del canal."""
    quien = _("Yo") if propio else (nick or _("Servidor"))
    if propio and tipo == "join":
        return _("Has entrado en el canal")
    if propio and tipo in ("part", "quit"):
        return _("Has salido del canal") + (f" ({texto})" if texto else "")
    if propio and tipo == "topic":
        return _("Has cambiado el tema a: {t}").format(t=texto)
    if propio and tipo == "mode":
        return _("Has cambiado los modos: {t}").format(t=texto)
    if tipo == "action":
        return f"* {quien} {texto}"
    if tipo == "notice":
        return _("Aviso de {n}: {t}").format(n=quien, t=texto)
    if tipo == "join":
        return _("{n} ha entrado en el canal").format(n=quien)
    if tipo in ("part", "quit"):
        return _("{n} ha salido").format(n=quien) + (f" ({texto})" if texto else "")
    if tipo == "kick":
        return _("{n} ha expulsado a {t}").format(n=quien, t=texto)
    if tipo == "topic":
        return _("{n} ha cambiado el tema a: {t}").format(n=quien, t=texto)
    if tipo == "mode":
        return _("{n} ha cambiado los modos: {t}").format(n=quien, t=texto)
    return f"{quien}: {texto}"


def _ocultar_tipos(account_id: int, nombre: str) -> str:
    """Trozo de SQL que quita entradas y salidas cuando el usuario las tiene ocultas en el canal."""
    try:
        if es_canal(nombre) and not irc_service.mostrar_eventos(account_id, nombre):
            return "AND tipo NOT IN ('join', 'part', 'quit') "
    except Exception:
        pass
    return ""


def _listar_mensajes(account_id: int, nombre: str, limite: int) -> List[Fila]:
    try:
        rows = db.query("SELECT id, COALESCE(nick, ''), COALESCE(text, ''), COALESCE(tipo, 'msg'), COALESCE(date, ''), "
                        "COALESCE(es_propio, 0), COALESCE(highlight, 0), COALESCE(leido, 1) FROM irc_messages "
                        f"WHERE account_id = ? AND channel = ? {_ocultar_tipos(account_id, nombre)}"
                        "ORDER BY id DESC LIMIT ?", (account_id, nombre.lower(), limite))
    except Exception as e:
        log.error(f"Error al listar los mensajes de IRC: {e}")
        return []
    rows.reverse()      # cronológico: lo más reciente al final, como en una conversación
    yo = (irc_service.mi_nick(account_id) or "").lower()
    filas = []
    for mid, nick, texto, tipo, fecha, propio, mencion, leido in rows:
        propio = propio or bool(yo and nick.lower() == yo)       # lo que hace la red en tu nombre (tema, entradas…)
        linea = _texto_evento(nick, texto, tipo, bool(propio))
        nuevo = not leido and not propio
        if mencion and not propio:
            linea += " · " + _("te mencionan")
        if nuevo:
            linea = _("Nuevo. ") + linea
        fila = Fila(id=str(mid), feed_id=0, feed_titulo=nick, titulo=linea, autor=nick, fecha=fecha,
                    leido=not nuevo, favorito=False, mas_tarde=False, audio=False, enlace="", servicio="irc",
                    extra={"tipo": "mensaje", "account_id": account_id, "chat": nombre, "nick": nick,
                           "texto": texto, "evento": tipo, "propio": bool(propio), "mencion": bool(mencion),
                           "nuevo": nuevo, "usuario": nick})
        if tipo in ("msg", "action"):       # entradas, salidas y demás eventos se leen como frase suelta
            fila = con_plantilla(fila, "irc_message", {
                "nick": ("* " if tipo == "action" else "") + (_("Yo") if propio else nick or _("Servidor")),
                "content": _condensar(texto) + (" · " + _("te mencionan") if mencion and not propio else ""),
                "hora": hora(fecha), "canal": nombre})
        filas.append(fila)
    return filas


def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de IRC para el panel de lectura."""
    from .datos import fecha_legible
    x = fila.extra
    if x.get("tipo") == "chat":
        info = [_("Canal") if not x.get("privado") else _("Privado")]
        if not x.get("privado"):
            info.append(_("unido") if x.get("unido") else _("no unido"))
        cuerpo = fila.titulo
        if x.get("tema"):
            cuerpo += "\n\n" + _("Tema: {t}").format(t=x["tema"])
        return (fila.autor, " · ".join(info + [fecha_legible(fila.fecha)]), "",
                cuerpo + "\n\n" + _("Pulsa Intro para abrir la conversación."))
    quien = _("Yo") if x.get("propio") else (x.get("nick") or _("Servidor"))
    meta = [quien, fecha_legible(fila.fecha)]
    return (quien, " · ".join(p for p in meta if p), "", _texto_evento(x.get("nick", ""), x.get("texto", ""),
                                                                       x.get("evento", "msg"), x.get("propio", False)))


def marcar_leido(account_id: int, nombre: str):
    """Pone a cero los no leídos de un chat y deja sus mensajes como leídos."""
    try:
        db.execute("UPDATE irc_channels SET unread_count = 0 WHERE id = ?", (_id_chat(account_id, nombre),))
        db.execute("UPDATE irc_messages SET leido = 1 WHERE account_id = ? AND channel = ? AND leido = 0",
                   (account_id, (nombre or "").lower()))
    except Exception as e:
        log.debug(f"No se pudo marcar leído el chat de IRC: {e}")


def marcar_todo_leido(account_id: int):
    try:
        db.execute("UPDATE irc_channels SET unread_count = 0 WHERE account_id = ?", (account_id,))
        db.execute("UPDATE irc_messages SET leido = 1 WHERE account_id = ? AND leido = 0", (account_id,))
    except Exception as e:
        log.debug(f"No se pudo marcar todo leído en IRC: {e}")


def eliminar_chat(account_id: int, nombre: str):
    """Borra un chat de la lista junto con su historial local."""
    db.execute("DELETE FROM irc_messages WHERE account_id = ? AND channel = ?", (account_id, nombre.lower()))
    db.execute("DELETE FROM irc_channels WHERE id = ?", (_id_chat(account_id, nombre),))


def ultimo_ajeno(account_id: int, nombre: str) -> Optional[tuple]:
    """(nick, texto, tipo) del último mensaje recibido en el chat, para anunciarlo por voz."""
    f = db.query_one("SELECT COALESCE(nick, ''), COALESCE(text, ''), COALESCE(tipo, 'msg'), COALESCE(es_propio, 0) "
                     "FROM irc_messages WHERE account_id = ? AND channel = ? AND tipo IN ('msg', 'action', 'notice') "
                     "ORDER BY id DESC LIMIT 1", (account_id, nombre.lower()))
    if not f or f[3]:
        return None
    return f[0], f[1], f[2]
