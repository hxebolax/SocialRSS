# -*- coding: utf-8 -*-
"""Símbolos comunes del servicio XMPP (Jabber): carga diferida de slixmpp, XMPPError y helpers.

Regla de arranque: slixmpp NO se importa a nivel de módulo (carga diferida por find_spec, como
nio/pydle/pyrogram). La referencia normativa de protocolo está en local:
Consulta_SocialRSS/XMPP/documentacion_xmpp_offline (RFC 6120/6121/7622 + XEPs) y
adjuntos/analisis/xmpp_cliente_socialrss.md (plan por tandas X1–X9).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext (los eventos llevan textos para la UI)

import asyncio
import logging
import threading
import importlib.util
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Callable, List

from app.core.database import db
from app.core.security import security
from app.core.event_bus import event_bus
from app.core.errors import ServiceError

log = logging.getLogger(__name__)

# ¿Está instalada la librería? (find_spec no la importa: no cuesta arranque)
HAS_SLIXMPP = importlib.util.find_spec("slixmpp") is not None
if not HAS_SLIXMPP:  # pragma: no cover - depende del entorno
    log.info("slixmpp no disponible. XMPP quedará deshabilitado hasta instalar dependencias.")


class XMPPError(ServiceError):
    """Error específico del servicio de XMPP."""
    pass


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hora_local(iso_str, con_fecha: bool = False) -> str:
    """Convierte un ISO 8601 (guardado en UTC) a la hora LOCAL del equipo para MOSTRARLA. Antes se
    cortaba el ISO en crudo ('[11:16]'), que enseñaba la hora UTC (p. ej. 2 h menos en la España
    peninsular en verano). Aquí se pasa a la zona horaria del sistema (mismo criterio que Matrix)."""
    try:
        dt = datetime.fromisoformat((iso_str or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()  # zona horaria local del sistema operativo
        return local.strftime("%d/%m/%Y %H:%M" if con_fecha else "%H:%M")
    except Exception:
        return (iso_str or "")[11:16] if not con_fecha else (iso_str or "")[:16].replace("T", " ")


def delay_a_iso(delay) -> str:
    """Fecha real de un mensaje del histórico (XEP-0203 delay) a ISO 8601 UTC. Si no hay delay,
    devuelve la hora actual (el mensaje es de ahora mismo)."""
    try:
        if delay is None:
            return ahora_iso()
        stamp = delay["stamp"] if not isinstance(delay, str) else delay
        if not stamp:
            return ahora_iso()
        if isinstance(stamp, datetime):
            dt = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return ahora_iso()


def db_id_chat(account_id: int, jid: str) -> str:
    """Id de fila de xmpp_chats: 'cuenta|jid' (separador '|': los JID llevan '@' y a veces '/',
    mismo criterio que los room_id de Matrix y los canales de IRC)."""
    return f"{account_id}|{jid or ''}"


def jid_bare(jid: str) -> str:
    """La parte 'usuario@dominio' de un JID, sin el recurso ('/...')."""
    return (jid or "").split("/", 1)[0]


def jid_recurso(jid: str) -> str:
    """El recurso ('/...') de un JID completo, '' si no tiene."""
    partes = (jid or "").split("/", 1)
    return partes[1] if len(partes) > 1 else ""


# Códigos de discado internacionales (para MOSTRAR de forma legible un teléfono). Ordenados de más
# largo a más corto para casar el prefijo correcto (p. ej. "1242" antes que "1"). Sirve para los
# contactos de PUENTES tipo WhatsApp cuyo "nombre" es solo el número (localpart del JID): así el
# lector dice "+55 11 99999-9999" en vez de "5511999999999". Idea tomada del cliente WhatsApp
# accesible WinZapp (formateo Brasil/internacional), reimplementada.
_CODIGOS_PAIS = (
    "1242", "1246", "1268", "1284", "1340", "1345", "1473", "1649", "1758", "1767", "1784", "1787",
    "1809", "1868", "1869", "1876", "5999", "211", "212", "213", "216", "218", "220", "221", "222",
    "223", "224", "225", "226", "227", "228", "229", "230", "231", "232", "233", "234", "235", "236",
    "237", "238", "239", "240", "241", "242", "243", "244", "245", "248", "249", "250", "251", "252",
    "253", "255", "256", "257", "258", "260", "261", "263", "264", "265", "266", "267", "268", "269",
    "291", "297", "298", "350", "351", "352", "353", "354", "355", "356", "357", "358", "359", "370",
    "371", "372", "373", "374", "376", "377", "378", "380", "381", "382", "385", "386", "387", "420",
    "421", "423", "501", "502", "503", "504", "505", "506", "507", "509", "591", "592", "593", "595",
    "597", "598", "670", "673", "674", "675", "677", "678", "679", "680", "682", "685", "688", "691",
    "692", "850", "852", "853", "855", "856", "880", "886", "960", "961", "962", "963", "964", "965",
    "966", "967", "968", "971", "972", "973", "974", "975", "976", "977", "993", "994", "995", "996",
    "998", "20", "27", "30", "32", "33", "34", "36", "39", "40", "41", "43", "45", "46", "47", "48",
    "49", "51", "52", "53", "54", "55", "56", "57", "58", "60", "61", "62", "63", "64", "65", "66",
    "81", "82", "84", "86", "90", "91", "92", "93", "94", "95", "98", "1", "7",
)


def es_telefono(nombre: str) -> bool:
    """True si 'nombre' PARECE un número de teléfono y no un nombre real (p. ej. el localpart de un
    JID de puente que llega sin nombre visible). Acepta también cadenas de puros dígitos."""
    if not nombre:
        return False
    s = str(nombre).strip()
    if s.isdigit():
        return True
    d = sum(1 for c in s if c.isdigit())
    return d >= 7 and d >= len(s) * 0.7


def formatear_telefono(numero: str) -> str:
    """Devuelve un número de teléfono (o el localpart de un JID) en forma LEGIBLE:
    - Brasil (+55): +55 DD XXXXX-XXXX (móvil de 9 cifras) o +55 DD XXXX-XXXX.
    - Resto: +CC local (sin suponer prefijo de zona).
    Los IDENTIFICADORES INTERNOS de WhatsApp (@lid, 15+ dígitos) NO se formatean como teléfono."""
    entrada = str(numero or "")
    digitos = "".join(c for c in entrada.split("@")[0] if c.isdigit())
    if not digitos:
        return entrada
    if "@lid" in entrada or len(digitos) >= 14:
        return digitos  # es un id interno del gateway, no un número marcable
    cc = next((c for c in _CODIGOS_PAIS if digitos.startswith(c)), None)
    if cc is None:
        return "+" + digitos
    local = digitos[len(cc):]
    if not local:
        return "+" + cc
    if cc == "55":  # Brasil: DDD + número (9 cifras en móvil desde 2016)
        ddd, resto = local[:2], local[2:]
        if not resto:
            return f"+{cc} {ddd}"
        if len(resto) == 9:
            return f"+{cc} {ddd} {resto[:5]}-{resto[5:]}"
        corte = max(len(resto) - 4, 1)
        return f"+{cc} {ddd} {resto[:corte]}-{resto[corte:]}"
    return f"+{cc} {local}"


def telefonos_equivalentes(a: str, b: str) -> bool:
    """Compara dos números tolerando el 9º DÍGITO brasileño (55DDD9XXXXXXXX ↔ 55DDDXXXXXXXX): desde
    2016 los móviles de Brasil llevan un 9 extra tras el DDD y WhatsApp usa ambas formas según el
    contacto. Útil para casar el mismo número escrito de las dos maneras (idea de WinZapp)."""
    a = "".join(c for c in str(a or "") if c.isdigit())
    b = "".join(c for c in str(b or "") if c.isdigit())
    if a == b:
        return True
    if a.startswith("55") and b.startswith("55"):
        if len(a) == 13 and len(b) == 12 and a[4] == "9":
            return a[:4] + a[5:] == b
        if len(b) == 13 and len(a) == 12 and b[4] == "9":
            return b[:4] + b[5:] == a
    return False


def nombre_desde_jid(jid: str) -> str:
    """Un nombre legible por defecto a partir del JID (la parte local, o el dominio si es un
    servicio). Se usa cuando el roster no da nombre visible. Si el localpart es un TELÉFONO (típico
    de contactos de puentes tipo WhatsApp sin nombre), se muestra formateado (+55 11 99999-9999)."""
    bare = jid_bare(jid)
    local = bare.split("@", 1)[0]
    if local and es_telefono(local):
        return formatear_telefono(local)
    return local or bare


def nombre_contacto(nombre: str, jid: str) -> str:
    """Nombre a MOSTRAR de un contacto: usa el del roster; si no hay, el del JID; y si lo que
    queda es un TELÉFONO (contacto de un puente WhatsApp sin nombre real, o el gateway guardó el
    número crudo como nombre), lo formatea legible (+55 11 99999-9999)."""
    n = (nombre or "").strip() or nombre_desde_jid(jid)
    return formatear_telefono(n) if es_telefono(n) else n


# Presencias de XMPP (RFC 6121 §4.7.2.1: chat/away/xa/dnd + disponible/no disponible).
def presencias_legibles():
    """Presencias válidas con su etiqueta en español. Función (no constante) para no evaluar _()
    en el import, antes de instalarse gettext. 'show' vacío = simplemente disponible."""
    return (("", _("Disponible")), ("chat", _("Con ganas de hablar")),
            ("away", _("Ausente")), ("xa", _("No disponible")), ("dnd", _("Ocupado (no molestar)")))


def presencia_a_palabras(show: str, tipo: str = "available") -> str:
    """Convierte el 'show' de una stanza de presencia a una etiqueta en español para el lector.
    tipo 'unavailable' = sin conexión."""
    if tipo == "unavailable":
        return _("Sin conexión")
    if tipo == "subscribe":
        return _("Solicita suscripción")
    return dict(presencias_legibles()).get(show or "", _("Disponible"))
