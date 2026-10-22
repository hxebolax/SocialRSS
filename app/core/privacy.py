# -*- coding: utf-8 -*-
"""
Utilidades de privacidad: limpieza de parámetros de seguimiento de las URLs.

Cuando el usuario abre un enlace (de un toot o un artículo), eliminamos los
parámetros típicos de rastreo (utm_*, fbclid, gclid, etc.) para no propagarlos.
Se puede desactivar con la opción `privacy.clean_urls`.
"""

import logging
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from app.core.config_manager import config

log = logging.getLogger(__name__)


# Esquemas que se abren sin preguntar. Cualquier otro (ms-settings:, search-ms:,
# javascript:, telnet:, protocolos de aplicaciones registradas...) puede lanzar
# programas locales, así que requiere confirmación explícita del usuario.
_ESQUEMAS_SEGUROS = {"http", "https", "mailto"}


def _esquema(url: str) -> str:
    try:
        return (urlparse(url).scheme or "").lower()
    except Exception:
        return ""


def es_ruta_propia(url: str) -> bool:
    """True si es una ruta file:/local que apunta DENTRO de la carpeta de datos de
    SocialRSS (temporales, descargas, documentos generados por la propia app)."""
    try:
        from app.core.paths import get_data_path
        from urllib.request import url2pathname
        if _esquema(url) == "file":
            ruta = url2pathname(urlparse(url).path)
        else:
            ruta = url
        base = str(get_data_path("").resolve())
        destino = str(Path(ruta).resolve())
        return os.path.commonpath([base, destino]) == base
    except Exception:
        return False


def _confirmar_esquema(url: str) -> bool:
    """Pregunta al usuario antes de abrir un enlace con protocolo no estándar. Un
    enlace llegado en un artículo o mensaje podría activar aplicaciones locales.
    Si no hay interfaz disponible, se deniega por seguridad."""
    try:
        import wx
        if not wx.App.Get():
            return False
        esquema = _esquema(url) or "?"
        resumen = url if len(url) <= 120 else url[:120] + "…"

        def _preguntar():
            dlg = wx.MessageDialog(
                None,
                _("Este enlace usa el protocolo \"{0}\", que puede abrir una aplicación "
                  "de tu equipo:\n\n{1}\n\n¿Quieres abrirlo?").format(esquema, resumen),
                _("Abrir enlace con protocolo no estándar"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            resp = dlg.ShowModal()
            dlg.Destroy()
            return resp == wx.ID_YES
        if wx.IsMainThread():
            return _preguntar()
        # Desde un hilo de fondo: pasar la pregunta al hilo de interfaz y esperar.
        import threading
        resultado = {}
        hecho = threading.Event()

        def _en_ui():
            try:
                resultado["ok"] = _preguntar()
            finally:
                hecho.set()
        wx.CallAfter(_en_ui)
        hecho.wait(timeout=120)
        return bool(resultado.get("ok"))
    except Exception as e:
        log.debug(f"No se pudo confirmar el protocolo de '{url}': {e}")
        return False


def abrir_url(url: str) -> bool:
    """Abre una URL en el navegador/aplicación predeterminada de forma robusta.

    En Windows usa os.startfile, que es MUCHO más fiable que webbrowser.open,
    sobre todo en la aplicación compilada (cx_Freeze), donde webbrowser a veces
    no detecta el navegador y falla en silencio. Devuelve True si lo logró.

    Seguridad: solo http/https/mailto se abren directamente. Los enlaces file:
    o rutas locales solo si apuntan a archivos generados por SocialRSS (su
    carpeta de datos). Cualquier otro protocolo pide confirmación, porque puede
    activar aplicaciones registradas en el equipo.
    """
    if not url:
        return False
    esquema = _esquema(url)
    if esquema not in _ESQUEMAS_SEGUROS and not (not esquema and url.lower().startswith("www.")):
        es_local = esquema == "file" or (os.name == "nt" and len(esquema) == 1)  # "C:" → esquema "c"
        if es_local or not esquema:
            if not es_ruta_propia(url):
                if not _confirmar_esquema(url):
                    log.info(f"Apertura de ruta local no propia denegada: {url[:120]}")
                    return False
        elif not _confirmar_esquema(url):
            log.info(f"Apertura de URL con esquema '{esquema}' denegada: {url[:120]}")
            return False
    # Windows: el método más fiable, también en el ejecutable congelado.
    if os.name == "nt":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except Exception as e:
            log.debug(f"os.startfile falló para '{url}': {e}")
    # Resto de plataformas (o respaldo): webbrowser y, si falla, el abridor del SO.
    try:
        if webbrowser.open(url):
            return True
    except Exception as e:
        log.debug(f"webbrowser.open falló para '{url}': {e}")
    try:
        import subprocess
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif os.name == "posix":
            subprocess.Popen(["xdg-open", url])
        else:
            return False
        return True
    except Exception as e:
        log.error(f"No se pudo abrir la URL '{url}': {e}")
        return False

# Parámetros de seguimiento conocidos (prefijos y nombres exactos).
_PREFIJOS_RASTREO = ("utm_", "ga_", "mc_", "pk_", "piwik_", "matomo_")
_EXACTOS_RASTREO = {
    "fbclid", "gclid", "dclid", "gclsrc", "msclkid", "yclid", "twclid",
    "igshid", "mkt_tok", "wt_mc", "ref", "ref_src", "ref_url", "cmpid",
    "campaign", "spm", "scm", "_hsenc", "_hsmi", "vero_id", "oly_anon_id",
    "oly_enc_id", "s_cid", "ncid", "icid",
}


def _es_rastreo(clave: str) -> bool:
    clave_l = clave.lower()
    if clave_l in _EXACTOS_RASTREO:
        return True
    return any(clave_l.startswith(p) for p in _PREFIJOS_RASTREO)


def limpiar_url(url: str) -> str:
    """Devuelve la URL sin parámetros de seguimiento (si la opción está activa)."""
    if not url or not config.get("privacy", "clean_urls", True):
        return url
    try:
        partes = urlparse(url)
        if not partes.query:
            return url
        params = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                  if not _es_rastreo(k)]
        nueva_query = urlencode(params)
        return urlunparse(partes._replace(query=nueva_query))
    except Exception as e:
        log.debug(f"No se pudo limpiar la URL '{url}': {e}")
        return url
