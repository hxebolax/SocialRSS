# -*- coding: utf-8 -*-
"""Resolución de IDENTIDAD de AT Protocol para W Social: handle -> DID -> PDS.

Por qué existe este módulo: un cliente de W NO debe fijar ningún servidor. Cada cuenta de AT
Protocol tiene un identificador permanente (DID) cuyo documento declara en qué servidor
(Personal Data Server) vive su repositorio. El handle legible (`nombre.wsocial.eu`) es solo un
alias que apunta a ese DID y puede cambiar. Así que, para iniciar sesión, se resuelve:

    handle  --(DNS TXT _atproto / .well-known/atproto-did)-->  DID
    DID     --(plc.directory  |  did:web)-->  documento DID
    documento DID  --(service #atproto_pds)-->  URL del PDS

Con eso el cliente habla SIEMPRE con el servidor correcto del usuario, sea el de W Social u otro.
Solo se usa `requests` (ya presente en el proyecto) y el DNS del sistema, sin dependencias nuevas.

Todo es de solo lectura y sin estado, salvo una pequeña caché en memoria para no repetir la misma
resolución en cada operación.
"""
import re
import time
import logging
from typing import Dict, Optional, Tuple

from app.core.errors import WSocialServiceError

log = logging.getLogger(__name__)

# Directorio de DIDs de método "plc" (el habitual en AT Protocol). Es un servicio público del
# protocolo, no de una red concreta.
PLC_DIRECTORY = "https://plc.directory"

# Tiempo de espera de las peticiones de identidad. Cortas a propósito: son consultas pequeñas y
# bloquear el alta de una cuenta más de unos segundos no aporta nada.
_TIMEOUT = 10

# Identificador del punto de servicio del PDS dentro del documento DID (spec de AT Protocol).
_ID_PDS = "#atproto_pds"

_RE_HANDLE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)+$")
_RE_DID = re.compile(r"^did:[a-z]+:[a-zA-Z0-9._:%\-]+$")

# Caché en memoria: identificador -> (resultado, momento). Vive lo que dure la aplicación.
_CACHE: Dict[str, Tuple[dict, float]] = {}
_CACHE_TTL = 3600.0        # una hora: los cambios de handle o de PDS son raros


def es_did(valor: str) -> bool:
    """True si la cadena ya es un DID (did:plc:..., did:web:...)."""
    return bool(_RE_DID.match((valor or "").strip()))


def es_handle(valor: str) -> bool:
    """True si la cadena tiene forma de handle de AT Protocol (un nombre de dominio)."""
    return bool(_RE_HANDLE.match((valor or "").strip().lstrip("@")))


def normalizar_identificador(valor: str) -> str:
    """Quita la arroba y los espacios; deja el DID o el handle en minúsculas."""
    v = (valor or "").strip().lstrip("@")
    return v if es_did(v) else v.lower()


def _sesion_http():
    """Sesión de requests con la configuración de red de la aplicación (proxy incluido)."""
    import requests
    return requests.Session()


def resolver_handle_a_did(handle: str, pds_url: str = "") -> str:
    """DID de un handle. Se intentan, en el orden que marca la especificación:

    1. `_atproto.<handle>` en el DNS (registro TXT `did=...`), que es el método canónico.
    2. `https://<handle>/.well-known/atproto-did`, para quien no controla su DNS.
    3. `com.atproto.identity.resolveHandle` en el servidor indicado, como último recurso (sirve
       cuando el handle es de un dominio que solo el propio servidor sabe resolver).

    Lanza WSocialServiceError si no se puede resolver: sin DID no hay forma de saber a qué
    servidor conectarse, y adivinarlo sería justo el error que este módulo evita.
    """
    handle = normalizar_identificador(handle)
    if not handle:
        raise WSocialServiceError("Indica tu identificador de usuario (por ejemplo nombre.wsocial.eu).")
    if es_did(handle):
        return handle
    if not es_handle(handle):
        raise WSocialServiceError(
            f"«{handle}» no parece un identificador válido. En W Social el usuario es un nombre "
            f"de dominio, por ejemplo nombre.wsocial.eu.")

    # 1) DNS TXT en _atproto.<handle>
    did = _resolver_por_dns(handle)
    if did:
        return did

    # 2) HTTPS bien conocido
    did = _resolver_por_well_known(handle)
    if did:
        return did

    # 3) El servidor, si nos han dado uno
    if pds_url:
        did = _resolver_por_servidor(handle, pds_url)
        if did:
            return did

    raise WSocialServiceError(
        f"No se ha podido averiguar la identidad de «{handle}». Comprueba que el nombre de "
        f"usuario es correcto y que tienes conexión a internet.")


def _resolver_por_dns(handle: str) -> str:
    """Consulta el TXT `_atproto.<handle>` buscando 'did='. Devuelve '' si no lo hay.

    Se usa dnspython si está disponible (lo arrastran otras dependencias); si no, se salta este
    método sin ruido y se prueban los siguientes.
    """
    try:
        import dns.resolver  # type: ignore
    except Exception:
        return ""
    try:
        respuestas = dns.resolver.resolve(f"_atproto.{handle}", "TXT", lifetime=_TIMEOUT)
        for r in respuestas:
            texto = b"".join(getattr(r, "strings", []) or []).decode("utf-8", "ignore") or str(r)
            texto = texto.strip('"').strip()
            if texto.startswith("did="):
                did = texto[4:].strip()
                if es_did(did):
                    return did
    except Exception as e:
        log.debug(f"W Social: sin TXT _atproto para {handle}: {e}")
    return ""


def _resolver_por_well_known(handle: str) -> str:
    """Lee https://<handle>/.well-known/atproto-did (debe devolver el DID en texto plano)."""
    try:
        with _sesion_http() as s:
            r = s.get(f"https://{handle}/.well-known/atproto-did", timeout=_TIMEOUT,
                      allow_redirects=True)
            if r.status_code == 200:
                did = (r.text or "").strip()
                if es_did(did):
                    return did
    except Exception as e:
        log.debug(f"W Social: sin .well-known/atproto-did en {handle}: {e}")
    return ""


def _resolver_por_servidor(handle: str, pds_url: str) -> str:
    """Pregunta a un servidor por el handle (com.atproto.identity.resolveHandle)."""
    base = (pds_url or "").strip().rstrip("/")
    if not base:
        return ""
    try:
        with _sesion_http() as s:
            r = s.get(f"{base}/xrpc/com.atproto.identity.resolveHandle",
                      params={"handle": handle}, timeout=_TIMEOUT)
            if r.status_code == 200:
                did = (r.json() or {}).get("did", "")
                if es_did(did):
                    return did
    except Exception as e:
        log.debug(f"W Social: el servidor {base} no resolvió {handle}: {e}")
    return ""


def documento_did(did: str) -> dict:
    """Documento DID completo. Soporta los dos métodos que usa AT Protocol:

    - `did:plc:...`: se pide al directorio PLC.
    - `did:web:dominio`: se pide a https://dominio/.well-known/did.json (el estándar did:web).
    """
    did = (did or "").strip()
    if not es_did(did):
        raise WSocialServiceError(f"«{did}» no es un identificador de cuenta válido.")
    try:
        with _sesion_http() as s:
            if did.startswith("did:plc:"):
                url = f"{PLC_DIRECTORY}/{did}"
            elif did.startswith("did:web:"):
                resto = did[len("did:web:"):]
                # did:web admite puerto y ruta codificados con ':' -> se convierten en '/'.
                partes = resto.split(":")
                host = partes[0].replace("%3A", ":")
                ruta = "/".join(partes[1:])
                url = f"https://{host}/{ruta}/did.json" if ruta else f"https://{host}/.well-known/did.json"
            else:
                raise WSocialServiceError(
                    f"El tipo de identidad «{did.split(':')[1]}» no está soportado por este cliente.")
            r = s.get(url, timeout=_TIMEOUT)
            if r.status_code != 200:
                raise WSocialServiceError(
                    f"No se pudo consultar la identidad de la cuenta (respuesta {r.status_code}).")
            return r.json() or {}
    except WSocialServiceError:
        raise
    except Exception as e:
        raise WSocialServiceError(f"No se pudo consultar la identidad de la cuenta: {e}")


def pds_de_documento(doc: dict) -> str:
    """URL del PDS declarada en un documento DID (servicio con id '#atproto_pds')."""
    for servicio in (doc or {}).get("service", []) or []:
        sid = str(servicio.get("id", ""))
        if sid.endswith(_ID_PDS) or sid == _ID_PDS:
            endpoint = (servicio.get("serviceEndpoint") or "").strip().rstrip("/")
            if endpoint:
                return endpoint
    return ""


def handles_de_documento(doc: dict) -> list:
    """Handles declarados en el documento DID (alsoKnownAs: at://nombre.dominio)."""
    salida = []
    for aka in (doc or {}).get("alsoKnownAs", []) or []:
        if isinstance(aka, str) and aka.startswith("at://"):
            salida.append(aka[len("at://"):])
    return salida


def resolver_identidad(identificador: str, pds_sugerido: str = "", usar_cache: bool = True) -> dict:
    """Resuelve un handle o DID a {did, handle, pds}. Es la puerta de entrada del módulo.

    `pds_sugerido` solo se usa como ayuda para resolver el handle (último recurso) y como
    respaldo si el documento DID no declarara PDS, cosa que no debería ocurrir.
    """
    clave = normalizar_identificador(identificador)
    if not clave:
        raise WSocialServiceError("Indica tu identificador de usuario.")
    if usar_cache:
        guardado = _CACHE.get(clave)
        if guardado and (time.time() - guardado[1]) < _CACHE_TTL:
            return dict(guardado[0])

    did = clave if es_did(clave) else resolver_handle_a_did(clave, pds_sugerido)
    doc = documento_did(did)
    pds = pds_de_documento(doc) or (pds_sugerido or "").strip().rstrip("/")
    if not pds:
        raise WSocialServiceError(
            "La identidad de esa cuenta no declara ningún servidor de datos (PDS), así que no hay "
            "dónde iniciar sesión. Revisa el identificador con tu proveedor.")
    if not pds.lower().startswith("https://"):
        # Nunca se manda una contraseña de aplicación por un canal sin cifrar.
        raise WSocialServiceError(
            f"El servidor declarado por esa identidad no usa HTTPS ({pds}); por seguridad no se "
            f"inicia sesión contra él.")
    handles = handles_de_documento(doc)
    resultado = {
        "did": did,
        "handle": (handles[0] if handles else (clave if not es_did(clave) else "")),
        "pds": pds,
        "handles": handles,
    }
    _CACHE[clave] = (dict(resultado), time.time())
    if resultado["handle"]:
        _CACHE[resultado["handle"].lower()] = (dict(resultado), time.time())
    _CACHE[did] = (dict(resultado), time.time())
    return resultado


def describir_servidor(pds_url: str) -> dict:
    """`com.atproto.server.describeServer`: datos públicos del servidor (dominios de handle
    disponibles, si exige invitación, enlaces legales). Sirve para mostrar al usuario con qué
    servidor está hablando antes de entrar. Devuelve {} si no responde."""
    base = (pds_url or "").strip().rstrip("/")
    if not base:
        return {}
    try:
        with _sesion_http() as s:
            r = s.get(f"{base}/xrpc/com.atproto.server.describeServer", timeout=_TIMEOUT)
            if r.status_code == 200:
                return r.json() or {}
    except Exception as e:
        log.debug(f"W Social: describeServer falló en {base}: {e}")
    return {}


def limpiar_cache(identificador: Optional[str] = None):
    """Olvida lo resuelto (todo, o solo un identificador). Se usa al cambiar de handle."""
    if identificador:
        _CACHE.pop(normalizar_identificador(identificador), None)
    else:
        _CACHE.clear()
