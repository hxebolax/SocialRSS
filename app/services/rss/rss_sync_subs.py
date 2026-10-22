# -*- coding: utf-8 -*-
"""Sincronización de SUSCRIPCIONES con el servidor (mixin de RSSService), en el sentido
aplicación -> servidor: dar de alta, dar de baja, renombrar y cambiar de carpeta un feed
también en FreshRSS, Inoreader, The Old Reader... (Google Reader API) o Miniflux.

Hasta ahora las suscripciones solo BAJABAN del servidor. Es OPCIONAL (casilla en
"Sincronización con servidor", desactivada por defecto): quien ya sincroniza no nota nada hasta
que la activa. Cada cambio se envía en segundo plano al hacerlo; si falla, se avisa y el cambio
local se queda hecho.

Además, con Miniflux el artículo completo se puede pedir al propio servidor (fetch-content),
que usa sus reglas de extracción; si no da más texto, se usa el extractor de la aplicación.
"""
import logging
from typing import Optional

from app.core.config_manager import config
from app.core.database import db

log = logging.getLogger(__name__)


def _url_igual(a: str, b: str) -> bool:
    from .rss_sync import _url_clave
    return _url_clave(a or "") == _url_clave(b or "")


# ------------------------------------------------------------------ Google Reader API
def _greader_post(cliente, ruta: str, datos: dict):
    datos = dict(datos)
    token = cliente._token_escritura()
    if token:
        datos["T"] = token
    r = cliente._s().post(f"{cliente.base}/reader/api/0/{ruta}", data=datos,
                          headers=cliente._headers(), timeout=cliente.timeout)
    if r.status_code == 401:
        cliente._sid = None
        r = cliente._s().post(f"{cliente.base}/reader/api/0/{ruta}", data=datos,
                              headers=cliente._headers(), timeout=cliente.timeout)
    r.raise_for_status()
    return r


def _greader_id(cliente, url: str) -> Optional[str]:
    """Id de la suscripción en el servidor (FreshRSS usa "feed/123", otros "feed/<url>")."""
    datos = cliente._get("subscription/list", {"output": "json"}).json()
    for s in datos.get("subscriptions", []):
        if _url_igual(s.get("url", ""), url):
            return s.get("id")
    return None


def _etiqueta(carpeta: str) -> str:
    return f"user/-/label/{carpeta}"


def greader_cambio(cliente, accion: str, url: str, titulo: str = "", carpeta: str = "",
                   carpeta_anterior: str = "") -> bool:
    if accion == "alta":
        datos = {"ac": "subscribe", "s": f"feed/{url}"}
        if titulo:
            datos["t"] = titulo
        if carpeta:
            datos["a"] = _etiqueta(carpeta)
        _greader_post(cliente, "subscription/edit", datos)
        return True
    sid = _greader_id(cliente, url)
    if not sid:
        return False         # el servidor no lo tiene: nada que hacer
    if accion == "baja":
        _greader_post(cliente, "subscription/edit", {"ac": "unsubscribe", "s": sid})
    elif accion == "renombrar":
        _greader_post(cliente, "subscription/edit", {"ac": "edit", "s": sid, "t": titulo})
    elif accion == "mover":
        datos = {"ac": "edit", "s": sid}
        if carpeta:
            datos["a"] = _etiqueta(carpeta)
        if carpeta_anterior:
            datos["r"] = _etiqueta(carpeta_anterior)
        _greader_post(cliente, "subscription/edit", datos)
    return True


# ------------------------------------------------------------------ Miniflux
def _mf(cliente, metodo: str, ruta: str, **kw):
    r = getattr(cliente._s(), metodo)(f"{cliente.base}/v1/{ruta}", **cliente._kw(), **kw)
    r.raise_for_status()
    return r


def _mf_feed_id(cliente, url: str) -> Optional[int]:
    for f in _mf(cliente, "get", "feeds").json():
        if _url_igual(f.get("feed_url", ""), url):
            return f.get("id")
    return None


def _mf_categoria(cliente, nombre: str) -> Optional[int]:
    if not nombre:
        return None
    for c in _mf(cliente, "get", "categories").json():
        if (c.get("title") or "").strip().lower() == nombre.strip().lower():
            return c.get("id")
    return _mf(cliente, "post", "categories", json={"title": nombre}).json().get("id")


def miniflux_cambio(cliente, accion: str, url: str, titulo: str = "", carpeta: str = "",
                    carpeta_anterior: str = "") -> bool:
    if accion == "alta":
        cuerpo = {"feed_url": url}
        cat = _mf_categoria(cliente, carpeta)
        if cat:
            cuerpo["category_id"] = cat
        _mf(cliente, "post", "feeds", json=cuerpo)
        return True
    fid = _mf_feed_id(cliente, url)
    if not fid:
        return False
    if accion == "baja":
        _mf(cliente, "delete", f"feeds/{fid}")
    elif accion == "renombrar":
        _mf(cliente, "put", f"feeds/{fid}", json={"title": titulo})
    elif accion == "mover":
        cat = _mf_categoria(cliente, carpeta)
        if cat:
            _mf(cliente, "put", f"feeds/{fid}", json={"category_id": cat})
    return True


def miniflux_texto_completo(cliente, entry_id) -> str:
    """Artículo completo que extrae el propio servidor Miniflux (con sus reglas)."""
    r = _mf(cliente, "get", f"entries/{entry_id}/fetch-content", params={"update_content": "false"})
    return (r.json() or {}).get("content") or ""


class _SyncSuscripcionesMixin:
    def enviar_suscripciones_activo(self, account_id: int) -> bool:
        cfg = self.obtener_config_sync(account_id) or {}
        return bool(cfg.get("activo") and cfg.get("enviar_suscripciones"))

    def fijar_enviar_suscripciones(self, account_id: int, activo: bool):
        cfg = self.obtener_config_sync(account_id)
        if cfg:
            cfg["enviar_suscripciones"] = bool(activo)
            config.set("rss_sync", str(account_id), cfg)

    def cambio_suscripcion(self, account_id: int, accion: str, url: str, titulo: str = "",
                           carpeta: str = "", carpeta_anterior: str = "", sincrono: bool = False):
        """Envía un cambio de suscripción al servidor si la cuenta lo tiene activado.
        accion: 'alta' | 'baja' | 'renombrar' | 'mover'. En segundo plano salvo `sincrono`."""
        if not account_id or not url or not self.enviar_suscripciones_activo(account_id):
            return None

        def _enviar(ctx=None):
            cliente = self._cliente_de_cuenta(account_id)
            if cliente is None:
                return False
            from .rss_sync import MinifluxClient
            funcion = miniflux_cambio if isinstance(cliente, MinifluxClient) else greader_cambio
            return funcion(cliente, accion, url, titulo, carpeta, carpeta_anterior)
        if sincrono:
            return _enviar()

        def _fin(res, err):
            if err:
                log.warning(f"Sync de suscripciones ({accion} {url}): {err}")
                try:
                    from app.core.speech import notificar
                    notificar(_("No se pudo aplicar el cambio del feed en el servidor: {0}").format(err))
                except Exception:
                    pass
        from app.core.task_manager import task_manager
        task_manager.start_task(_enviar, callback=_fin, pass_context=True)
        return None

    # -- ayudantes para los puntos de la interfaz que no tienen los datos a mano ----------
    def _datos_feed(self, feed_id: int):
        fila = db.query_one("SELECT f.account_id, f.url, f.title, COALESCE(c.name, '') "
                            "FROM rss_feeds f LEFT JOIN rss_categories c ON c.id = f.category_id "
                            "WHERE f.id = ?", (feed_id,))
        return fila or (None, "", "", "")

    def sus_baja_feed(self, feed_id: int):
        acc, url, _t, _c = self._datos_feed(feed_id)
        self.cambio_suscripcion(acc, "baja", url)

    def sus_renombrar_feed(self, feed_id: int):
        acc, url, titulo, _c = self._datos_feed(feed_id)
        self.cambio_suscripcion(acc, "renombrar", url, titulo=titulo)

    def sus_mover_feed(self, feed_id: int, carpeta_anterior: str = ""):
        acc, url, _t, carpeta = self._datos_feed(feed_id)
        self.cambio_suscripcion(acc, "mover", url, carpeta=carpeta, carpeta_anterior=carpeta_anterior)

    def sus_alta_feed(self, feed_id: int):
        acc, url, titulo, carpeta = self._datos_feed(feed_id)
        self.cambio_suscripcion(acc, "alta", url, titulo=titulo, carpeta=carpeta)

    # -- artículo completo con Miniflux -------------------------------------------------
    def texto_completo_del_servidor(self, article_id: str) -> str:
        """SÍNCRONO. Texto completo que da Miniflux para el artículo ("" si no aplica)."""
        fila = db.query_one("SELECT s.account_id, s.item_id FROM rss_sync_items s "
                            "WHERE s.article_id = ?", (article_id,))
        if not fila or not fila[1]:
            return ""
        cfg = self.obtener_config_sync(fila[0]) or {}
        if not cfg.get("activo") or cfg.get("tipo") != "miniflux":
            return ""
        cliente = self._cliente_de_cuenta(fila[0])
        if cliente is None:
            return ""
        try:
            return miniflux_texto_completo(cliente, fila[1])
        except Exception as e:
            log.debug(f"Miniflux fetch-content falló para {article_id}: {e}")
            return ""
