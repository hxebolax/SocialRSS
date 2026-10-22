# -*- coding: utf-8 -*-
"""
Medios de pago en los que el usuario ESTÁ SUSCRITO: descargar sus artículos con su sesión.

Quien paga varios periódicos no debería tener que ir a su web (a menudo poco accesible con lector
de pantalla) para leer lo que ya ha pagado. Para los dominios que el usuario marca en
Preferencias, RSS, el lector descarga el artículo con las cookies de la sesión que ya tiene
iniciada en su navegador, igual que haría el propio navegador: el periódico entrega la noticia
completa porque reconoce al suscriptor. No se salta ningún control: sin sesión válida el
periódico responde lo mismo que a cualquiera.

Privacidad y seguridad:
- Las cookies se leen del navegador SOLO al descargar un artículo de un dominio marcado, se
  guardan unos minutos EN MEMORIA (nunca en disco ni en el registro) y se filtran para que solo
  viajen las de ese dominio y sus subdominios.
- Se usa una sesión HTTP propia por descarga, que no comparte su tarro de cookies con el resto de
  la aplicación.

Mismo motor que la importación de cookies de X (browser-cookie3). Firefox es el que funciona de
forma fiable: los navegadores basados en Chrome cifran sus cookies desde 2024 (App-Bound
Encryption) y normalmente no se pueden leer desde fuera.
"""

import logging
import threading
import time
from typing import Dict, List, Tuple
from urllib.parse import urlparse

log = logging.getLogger(__name__)

NAVEGADORES = ("firefox", "chrome", "edge", "brave", "opera")

# Minutos que se reutilizan las cookies leídas antes de volver a leerlas del navegador.
_VIGENCIA_S = 15 * 60

_cache: Dict[Tuple[str, str], Tuple[float, list]] = {}
_lock = threading.Lock()


def normalizar_dominio(texto: str) -> str:
    """'https://www.ElPais.com/loquesea' -> 'elpais.com'. Vacío si no parece un dominio."""
    t = (texto or "").strip().lower()
    if not t:
        return ""
    if "://" in t:
        t = urlparse(t).hostname or ""
    t = t.split("/")[0].split(":")[0].strip(".")
    if t.startswith("www."):
        t = t[4:]
    return t if "." in t and " " not in t else ""


def dominios_suscritos() -> List[str]:
    from app.core.config_manager import config
    lista = config.get("rss", "subscription_domains", []) or []
    return [d for d in (normalizar_dominio(x) for x in lista) if d]


def navegador_configurado() -> str:
    from app.core.config_manager import config
    nav = (config.get("rss", "subscription_browser", "firefox") or "firefox").lower()
    return nav if nav in NAVEGADORES else "firefox"


def dominio_suscrito_de(url: str) -> str:
    """Dominio marcado que corresponde a la URL ("" si ninguno)."""
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return ""
    for d in dominios_suscritos():
        if host == d or host.endswith("." + d):
            return d
    return ""


def _es_del_dominio(dominio_cookie: str, dominio: str) -> bool:
    dc = (dominio_cookie or "").lower().lstrip(".")
    return dc == dominio or dc.endswith("." + dominio)


def _leer_cookies(dominio: str, navegador: str) -> list:
    """Lee del navegador las cookies del dominio (lista de http.cookiejar.Cookie)."""
    import browser_cookie3 as bc
    funcion = getattr(bc, navegador, None)
    if funcion is None:
        raise ValueError(f"Navegador no soportado: {navegador}")
    tarro = funcion(domain_name=dominio)
    # browser-cookie3 filtra con un LIKE '%dominio%': se afina para no arrastrar cookies de
    # otros sitios cuyo nombre contenga el del periódico.
    return [c for c in tarro if _es_del_dominio(c.domain, dominio)]


def cookies_para(url: str, refrescar: bool = False):
    """Tarro de cookies (RequestsCookieJar) de la sesión del usuario para la URL, o None si su
    dominio no está marcado como suscripción o no se pudieron leer. Nunca lanza."""
    dominio = dominio_suscrito_de(url)
    if not dominio:
        return None
    return cookies_sesion(dominio, refrescar=refrescar)


def cookies_sesion(dominio: str, refrescar: bool = False, navegador: str = ""):
    """Tarro de cookies de la sesión del usuario en ESE dominio (y sus subdominios), leído del
    navegador configurado, o None. Lo usan los medios de pago y los hilos de Reddit (que ya no
    se pueden leer sin sesión). Mismas garantías: solo ese dominio, en memoria. Nunca lanza."""
    navegador = navegador or navegador_configurado()
    clave = (dominio, navegador)
    with _lock:
        guardado = _cache.get(clave)
        if refrescar or not guardado or time.time() - guardado[0] > _VIGENCIA_S:
            try:
                cookies = _leer_cookies(dominio, navegador)
            except Exception as e:
                log.warning(f"No se pudieron leer las cookies de {navegador} para {dominio}: "
                            f"{type(e).__name__}")
                return None
            guardado = (time.time(), cookies)
            _cache[clave] = guardado
            log.debug(f"Sesión de suscripción de {dominio} leída de {navegador}: {len(cookies)} cookies")
    import requests
    tarro = requests.cookies.RequestsCookieJar()
    for c in guardado[1]:
        tarro.set_cookie(c)
    return tarro if len(tarro) else None


def olvidar_sesiones():
    """Vacía la caché en memoria (al cambiar las preferencias)."""
    with _lock:
        _cache.clear()


def probar_sesiones(dominios: List[str], navegador: str) -> List[Tuple[str, int, str]]:
    """Para el botón "Probar" de Preferencias: cuántas cookies se leen de cada dominio.
    Devuelve [(dominio, n_cookies, error)]. SÍNCRONO (llamar desde un hilo de fondo)."""
    resultado = []
    for d in dominios:
        try:
            resultado.append((d, len(_leer_cookies(d, navegador)), ""))
        except Exception as e:
            resultado.append((d, 0, f"{type(e).__name__}: {e}"[:200]))
    return resultado
