# -*- coding: utf-8 -*-
"""Datos de XMPP para la interfaz GTK: árbol de cuentas y buffers, listados y detalle.

El servicio guarda todo en SQLite (xmpp_chats, xmpp_mensajes); aquí solo se LEE esa caché. El servicio
(slixmpp, OMEMO...) pesa, así que no se importa hasta que hace falta."""

import logging
import sys
from typing import Dict, List, Optional

from app.core.database import db
from .datos import Nodo, Fila, buffer_oculto
from .lectura import con_plantilla, hora

log = logging.getLogger(__name__)

# (clave, título). Con pasarelas (WhatsApp, Discord...) la rama principal solo conserva lo del propio servidor y cada
# pasarela se lleva lo suyo. Las conversaciones abiertas cuelgan de la cuenta como buffers "chat:<jid>".
BUFFERS = [
    ("conversaciones", "Conversaciones"),
    ("contactos", "Contactos"),
    ("grupos", "Grupos"),
    ("solicitudes", "Solicitudes"),
    ("archivados", "Archivados"),
]
BUFFERS_CON_PUENTES = ("contactos", "grupos", "solicitudes")
BUFFERS_DE_PUENTE = ("conversaciones", "contactos", "grupos", "archivados", "solicitudes")
NATIVO = "__nativo__"

EXTRA: Dict[int, List[Nodo]] = {}


def servicio():
    from app.services.xmpp.xmpp_service import xmpp_service
    return xmpp_service


def cargado() -> bool:
    return "app.services.xmpp.xmpp_service" in sys.modules


def cuentas() -> List[tuple]:
    return [(r[0], r[1]) for r in db.query(
        "SELECT id, name FROM accounts WHERE service_id = 'xmpp' AND active = 1 ORDER BY position, id")]


def conectada(account_id: int) -> bool:
    if not cargado():           # sin importar el servicio no puede haber conexión
        return False
    try:
        return servicio().esta_conectada(account_id)
    except Exception:
        return False


def puentes(account_id: int) -> List[dict]:
    """Pasarelas con las que hay chats; solo se muestran con la cuenta conectada (si no, están fuera de línea)."""
    if not conectada(account_id):
        return []
    try:
        return servicio().puentes(account_id)
    except Exception as e:
        log.debug(f"XMPP: no se pudieron detectar las pasarelas: {e}")
        return []


def construir_nodos() -> Nodo:
    raiz = Nodo(_("XMPP"), "servicio", servicio="xmpp")
    for acc_id, nombre in cuentas():
        titulo = nombre if conectada(acc_id) else _("{n} (desconectada)").format(n=nombre)
        cuenta = Nodo(titulo, "cuenta", servicio="xmpp", account_id=acc_id)
        pasarelas = puentes(acc_id)
        for clave, etiqueta in BUFFERS:
            if (pasarelas and clave not in BUFFERS_CON_PUENTES) or buffer_oculto("xmpp", clave):
                continue
            timeline = f"{clave}@{NATIVO}" if pasarelas else clave
            cuenta.hijos.append(Nodo(_(etiqueta), "timeline", servicio="xmpp", account_id=acc_id, timeline=timeline))
        for p in pasarelas:
            if buffer_oculto("xmpp", "puente:" + p["dominio"]):
                continue
            rama = Nodo(p["nombre"], "cuenta", servicio="xmpp", account_id=acc_id, timeline="puente:" + p["dominio"])
            for clave, etiqueta in BUFFERS:
                if clave in BUFFERS_DE_PUENTE and not buffer_oculto("xmpp", clave):
                    rama.hijos.append(Nodo(_(etiqueta), "timeline", servicio="xmpp", account_id=acc_id,
                                           timeline=f"{clave}@{p['dominio']}"))
            cuenta.hijos.append(rama)
        cuenta.hijos.extend(EXTRA.get(acc_id, []))
        raiz.hijos.append(cuenta)
    return raiz


def nodo_extra(account_id: int, timeline: str, titulo: str) -> Nodo:
    for n in EXTRA.setdefault(account_id, []):
        if n.timeline == timeline:
            return n
    n = Nodo(titulo, "timeline", servicio="xmpp", account_id=account_id, timeline=timeline, cerrable=True)
    EXTRA[account_id].append(n)
    return n


def cerrar_extra(nodo: Nodo):
    lista = EXTRA.get(nodo.account_id, [])
    if nodo in lista:
        lista.remove(nodo)


def jid_de_chat(timeline: str) -> Optional[str]:
    """JID del chat de un buffer "chat:<jid>"; None si el buffer no es una conversación."""
    if timeline.startswith("chat:") and len(timeline) > 5:
        return timeline[5:]
    return None


def clave_chat(jid: str) -> str:
    return "chat:" + jid


def dividir(timeline: str) -> tuple:
    """('contactos', '__nativo__') de «contactos@__nativo__»; el dominio es None si el buffer no lo lleva."""
    clave, _sep, dominio = timeline.partition("@")
    return clave, (dominio or None)


def info_chat(account_id: int, jid: str) -> dict:
    f = db.query_one(
        "SELECT COALESCE(tipo, 'contacto'), COALESCE(nombre, ''), COALESCE(presencia, ''), COALESCE(estado_texto, ''), "
        "COALESCE(suscripcion, ''), COALESCE(unread_count, 0), COALESCE(notify_level, 'all'), COALESCE(fijado, 0), "
        "COALESCE(archivado, 0), COALESCE(silenciado_hasta, ''), COALESCE(bloqueado, 0), COALESCE(pep_estado, ''), "
        "COALESCE(cifrado, 0), COALESCE(mi_nick, '') FROM xmpp_chats WHERE account_id = ? AND jid = ?", (account_id, jid))
    if not f:
        return {"tipo": "contacto", "nombre": jid, "presencia": "", "estado": "", "suscripcion": "",
                "no_leidos": 0, "aviso": "all", "fijado": False, "archivado": False, "silenciado": False,
                "bloqueado": False, "pep": "", "cifrado": False, "mi_nick": ""}
    return {"tipo": f[0], "nombre": f[1] or jid, "presencia": f[2], "estado": f[3], "suscripcion": f[4],
            "no_leidos": f[5], "aviso": f[6], "fijado": bool(f[7]), "archivado": bool(f[8]),
            "silenciado": bool(f[9]), "bloqueado": bool(f[10]), "pep": f[11], "cifrado": bool(f[12]), "mi_nick": f[13]}


def titulo_chat(account_id: int, jid: str) -> str:
    from app.services.xmpp.xmpp_common import nombre_contacto
    f = db.query_one("SELECT COALESCE(nombre, '') FROM xmpp_chats WHERE account_id = ? AND jid = ?", (account_id, jid))
    return nombre_contacto(f[0] if f else "", jid)


def _condensar(texto: str, limite: int = 240) -> str:
    """Une los espacios; el límite se ignora a propósito."""
    t = " ".join((texto or "").split())
    return t        # sin recortar: el lector de pantalla debe leer el texto completo (la etiqueta se abrevia sola)


def listar(account_id: int, timeline: str, limite: int = 300) -> List[Fila]:
    jid = jid_de_chat(timeline)
    if jid is not None:
        return _listar_mensajes(account_id, jid, limite)
    clave, dominio = dividir(timeline)
    srv = servicio()
    try:
        if clave == "solicitudes":
            return _filas_solicitudes(account_id, srv.solicitudes(account_id, dominio))
        if clave == "grupos":
            return _filas_chats(account_id, srv.salas_muc(account_id, dominio), "muc")
        if clave == "archivados":
            return _filas_chats(account_id, srv.archivados(account_id, dominio), None)
        if clave == "contactos":
            return _filas_chats(account_id, srv.contactos(account_id, dominio), "contacto")
        return _filas_chats(account_id, srv.conversaciones(account_id, dominio), "contacto")
    except Exception as e:
        log.error(f"Error al listar XMPP ({timeline}): {e}")
        return []


def _filas_solicitudes(account_id: int, solicitudes: List[dict]) -> List[Fila]:
    filas = []
    for s in solicitudes:
        sala = s.get("tipo") == "muc"
        texto = (_("Invitación a la sala {n} ({j})").format(n=s["nombre"], j=s["jid"]) if sala
                 else _("{n} ({j}) quiere agregarte").format(n=s["nombre"], j=s["jid"]))
        filas.append(Fila(id=s["jid"], feed_id=0, feed_titulo=s["nombre"], titulo=texto, autor=s["nombre"], fecha="",
                          leido=False, favorito=False, mas_tarde=False, audio=False, enlace="", servicio="xmpp",
                          extra={"tipo": "solicitud", "account_id": account_id, "jid": s["jid"], "nombre": s["nombre"],
                                 "sala": sala, "propio": False}))
    return filas


def _filas_chats(account_id: int, chats: List[dict], tipo_fijo: Optional[str]) -> List[Fila]:
    filas = []
    for c in chats:
        sala = (c.get("tipo") == "muc") if tipo_fijo is None else tipo_fijo == "muc"
        no_leidos = c.get("no_leidos") or 0
        partes = []
        if no_leidos:
            partes.append(_("{n} sin leer").format(n=no_leidos))
        nombre = c["nombre"] + (" " + _("(bloqueado)") if c.get("bloqueado") else "")
        partes.append(nombre)
        texto = ". ".join(partes)
        if _condensar(c.get("ultimo", ""), 160):
            texto += ": " + _condensar(c["ultimo"], 160)
        extras = []
        if sala:
            extras.append(_("grupo"))
        else:
            extras.append(c.get("presencia") or _("Sin conexión"))
            if c.get("estado_texto"):
                extras.append(c["estado_texto"])
        if c.get("fijado"):
            extras.append(_("fijado"))
        if c.get("silenciado"):
            extras.append(_("silenciado"))
        texto += " · " + ", ".join(extras)
        filas.append(con_plantilla(
            Fila(id=c["jid"], feed_id=0, feed_titulo=c["nombre"], titulo=texto, autor=c["nombre"], fecha="",
                 leido=not no_leidos, favorito=False, mas_tarde=False, audio=False, enlace="", servicio="xmpp",
                 extra={"tipo": "chat", "account_id": account_id, "jid": c["jid"], "nombre": c["nombre"],
                        "sala": sala, "presencia": c.get("presencia", ""), "no_leidos": no_leidos,
                        "bloqueado": bool(c.get("bloqueado")), "propio": False}),
            "xmpp_chat", {"contacto": nombre, "presencia": _("grupo") if sala else (c.get("presencia") or _("Sin conexión")),
                          "content": _condensar(c.get("ultimo", ""), 160),
                          "unread": _("{n} sin leer").format(n=no_leidos) if no_leidos else "",
                          "estado": "" if sala else c.get("estado_texto", "")}))
    return filas


_SQL_MSG = ("SELECT id, COALESCE(NULLIF(sender_nombre,''), sender), COALESCE(texto,''), COALESCE(date,''), "
            "COALESCE(es_propio,0), COALESCE(entregado,0), COALESCE(editado,0), COALESCE(stanza_id,''), "
            "COALESCE(media_url,''), COALESCE(reacciones,''), COALESCE(reply_to,''), COALESCE(tipo,'msg'), "
            "COALESCE(cifrado,0), COALESCE(leido,0), COALESCE(destacado,0), COALESCE(es_voz,0), "
            "COALESCE(duracion,0), COALESCE(reproducido,0), COALESCE(highlight,0) "
            "FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ?")


def _texto_de_respuesta(account_id: int, reply_id: str) -> str:
    """Fragmento del mensaje al que se responde, para leer a qué contesta."""
    f = db.query_one("SELECT COALESCE(NULLIF(sender_nombre,''), sender), COALESCE(texto,'') FROM xmpp_mensajes "
                     "WHERE account_id = ? AND (stanza_id = ? OR origin_id = ?) LIMIT 1", (account_id, reply_id, reply_id))
    if not f:
        return ""
    cuerpo = _condensar(f[1], 60)
    return f"{f[0]}: {cuerpo}" if cuerpo else (f[0] or "")


def _tipo_adjunto(srv, ref: str, es_voz) -> str:
    if es_voz or srv.es_nota_voz(ref):
        return "voz"
    if srv.es_audio(ref):
        return "audio"
    if srv.es_imagen(ref):
        return "imagen"
    if srv.es_video(ref):
        return "video"
    return "archivo"


ETIQUETAS_ADJUNTO = {"voz": "Mensaje de voz", "audio": "Mensaje de audio", "imagen": "Imagen", "video": "Vídeo",
                     "archivo": "Archivo"}


def _fila_mensaje(r, account_id: int, jid: str, srv) -> Fila:
    (mid, quien, texto, fecha, propio, entregado, editado, stanza_id, media_url, reacciones, reply_to, tipo, cifrado,
     leido, destacado, es_voz, duracion, reproducido, mencion) = r
    autor = _("Yo") if propio else (quien or _("Desconocido"))
    cuerpo, adjunto = texto, ""
    extras = []
    if tipo == "media":
        ref = media_url or texto
        adjunto = _tipo_adjunto(srv, ref, es_voz)
        cuerpo = _(ETIQUETAS_ADJUNTO[adjunto])
        dur = srv.formato_duracion(duracion)
        if dur:
            extras.append(dur)
        if adjunto == "voz":
            extras.append(_("(reproducido)") if (reproducido or propio) else _("(pendiente)"))
    if destacado:
        extras.append(_("(destacado)"))
    if cifrado:
        extras.append(_("(cifrado)"))
    if reply_to:
        cita = _texto_de_respuesta(account_id, reply_to)
        extras.append(_("(en respuesta a: {c})").format(c=cita) if cita else _("(respuesta)"))
    if editado:
        extras.append(_("(editado)"))
    if reacciones:
        extras.append(_("[Reacciones: {r}]").format(r=reacciones))
    if adjunto in ("voz", "audio", "video"):
        extras.append(_("(F2 para reproducir)"))
    elif adjunto:
        extras.append(_("(Ctrl+Alt+D para descargar)"))
    if mencion and not propio:
        extras.append(_("(te mencionan)"))
    if propio and entregado:
        extras.append(_("(entregado)"))
    nuevo = not leido and not propio
    linea = f"{autor}: {' '.join([cuerpo] + extras).strip()}"
    if nuevo:
        linea = _("Nuevo. ") + linea
    fila = Fila(id=str(mid), feed_id=0, feed_titulo=autor, titulo=linea, autor=autor, fecha=fecha, leido=not nuevo,
                favorito=bool(destacado), mas_tarde=False, audio=adjunto in ("voz", "audio"), enlace="", servicio="xmpp",
                extra={"tipo": "mensaje", "account_id": account_id, "jid": jid, "mid": mid, "stanza_id": stanza_id,
                       "texto": texto, "media_url": media_url, "adjunto": adjunto, "propio": bool(propio),
                       "nuevo": nuevo, "es_voz": bool(es_voz), "destacado": bool(destacado),
                       "reproducible": adjunto in ("voz", "audio", "video"), "descargable": bool(adjunto),
                       "usuario": quien})
    cita = _texto_de_respuesta(account_id, reply_to) if reply_to else ""
    return con_plantilla(fila, "xmpp_message", {
        "author": autor, "content": " ".join([cuerpo] + extras).strip(), "hora": hora(fecha), "contacto": "",
        "media": _(ETIQUETAS_ADJUNTO[adjunto]) if adjunto else "", "duracion": srv.formato_duracion(duracion) if adjunto else "",
        "estado": ((_("reproducido") if (reproducido or propio) else _("pendiente")) if adjunto == "voz" else ""),
        "reacciones": reacciones or "", "cita": cita or ""})


def _listar_mensajes(account_id: int, jid: str, limite: int) -> List[Fila]:
    srv = servicio()
    try:
        rows = db.query(_SQL_MSG + " ORDER BY id DESC LIMIT ?", (account_id, jid, limite))
    except Exception as e:
        log.error(f"Error al listar los mensajes de XMPP: {e}")
        return []
    rows.reverse()      # cronológico: lo más reciente al final, como en una conversación
    return [_fila_mensaje(r, account_id, jid, srv) for r in rows]


def detalle(fila: Fila) -> tuple:
    """(título, meta, enlace, cuerpo) de una fila de XMPP para el panel de lectura."""
    from .datos import fecha_legible
    x = fila.extra
    if x.get("tipo") in ("chat", "solicitud"):
        info = [x["jid"]]
        if x.get("tipo") == "solicitud":
            cuerpo = fila.titulo + "\n\n" + _("Pulsa Intro para aceptar o rechazar.")
        else:
            info.insert(0, _("Grupo") if x.get("sala") else _("Contacto"))
            c = info_chat(x["account_id"], x["jid"])
            lineas = [fila.titulo]
            if not x.get("sala"):
                lineas.append(_("Presencia: {p}").format(p=c["presencia"] or _("Sin conexión")))
                if c["estado"]:
                    lineas.append(_("Estado: {e}").format(e=c["estado"]))
                if c["pep"]:
                    lineas.append(_("Ánimo o actividad: {e}").format(e=c["pep"]))
            lineas.append(_("Pulsa Intro para abrir la conversación."))
            cuerpo = "\n\n".join(lineas)
        return (fila.autor, " · ".join(info), "", cuerpo)
    quien = _("Yo") if x.get("propio") else (x.get("usuario") or _("Desconocido"))
    r = db.query_one("SELECT COALESCE(texto,''), COALESCE(reacciones,''), COALESCE(reply_to,''), COALESCE(cifrado,0) "
                     "FROM xmpp_mensajes WHERE id = ?", (x["mid"],))
    texto, reacciones, reply_to, cifrado = r if r else (x.get("texto", ""), "", "", 0)
    meta = [quien, fecha_legible(fila.fecha)]
    if cifrado:
        meta.append(_("cifrado"))
    partes = []
    if reply_to:
        cita = _texto_de_respuesta(x["account_id"], reply_to)
        if cita:
            partes.append(_("En respuesta a {c}").format(c=cita))
    if x.get("adjunto"):
        partes.append("[" + _(ETIQUETAS_ADJUNTO[x["adjunto"]]) + "]")
        if x.get("media_url") and x["media_url"] != texto:
            partes.append(x["media_url"])
    if texto and not (x.get("adjunto") and texto == x.get("media_url")):
        partes.append(texto)
    if reacciones:
        partes.append(_("Reacciones: {r}").format(r=reacciones))
    return (quien, " · ".join(p for p in meta if p), x.get("media_url") or "", "\n\n".join(partes))


def seleccion_inicial(filas: List[Fila]) -> int:
    """Al abrir una conversación: el primer mensaje nuevo o, si no hay, el último."""
    for i, f in enumerate(filas):
        if f.extra.get("nuevo"):
            return i
    return max(0, len(filas) - 1)


def marcar_leido(account_id: int, jid: str):
    """Pone a cero los no leídos de un chat (local; el marcador de lectura al contacto lo manda el servicio)."""
    try:
        db.execute("UPDATE xmpp_chats SET unread_count = 0 WHERE account_id = ? AND jid = ?", (account_id, jid))
        db.execute("UPDATE xmpp_mensajes SET leido = 1 WHERE account_id = ? AND chat_jid = ? AND leido = 0",
                   (account_id, jid))
    except Exception as e:
        log.debug(f"No se pudo marcar leído el chat de XMPP: {e}")


def marcar_no_leido(account_id: int, jid: str):
    db.execute("UPDATE xmpp_chats SET unread_count = MAX(COALESCE(unread_count, 0), 1) WHERE account_id = ? AND jid = ?",
               (account_id, jid))


def marcar_todo_leido(account_id: int):
    db.execute("UPDATE xmpp_chats SET unread_count = 0 WHERE account_id = ?", (account_id,))
    db.execute("UPDATE xmpp_mensajes SET leido = 1 WHERE account_id = ? AND leido = 0", (account_id,))


def ultimo_ajeno(account_id: int, jid: str) -> Optional[tuple]:
    """(autor, texto, tipo) del último mensaje recibido en el chat, para anunciarlo por voz."""
    f = db.query_one("SELECT COALESCE(NULLIF(sender_nombre,''), sender), COALESCE(texto,''), COALESCE(tipo,'msg'), "
                     "COALESCE(highlight,0) FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ? AND es_propio = 0 "
                     "ORDER BY id DESC LIMIT 1", (account_id, jid))
    return f if f else None
