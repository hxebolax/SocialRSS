# -*- coding: utf-8 -*-
"""Colecciones de Mastodon (4.6, API versión 10).

Una colección es una lista PÚBLICA de cuentas recomendadas ("gente que merece la pena seguir
sobre accesibilidad"), con nombre, descripción, idioma y una etiqueta. Cualquiera puede verlas
en tu perfil y seguirlas. Las cuentas deciden si aceptan aparecer (feature_approval): unas se
añaden al momento, otras quedan pendientes hasta que las aprueban, y siempre pueden retirarse.

Mastodon.py 2.2.x trae los tipos pero NINGÚN método de colecciones, así que van por petición
directa (_peticion_directa, en _CuentasMixin), con cuerpo JSON como en la documentación oficial.

Mixin de MastodonService.
"""
import logging
from typing import Any, Callable, Dict, List, Optional

from app.core.errors import MastodonServiceError
from app.core.task_manager import task_manager

log = logging.getLogger(__name__)

# Versión de la API de Mastodon desde la que existen las colecciones.
API_COLECCIONES = 10

# Límites de la documentación oficial.
MAX_NOMBRE = 40
MAX_DESCRIPCION = 100

def _g(obj: Any, clave: str, defecto=None):
    try:
        valor = obj.get(clave) if hasattr(obj, "get") else getattr(obj, clave, defecto)
    except Exception:
        valor = defecto
    return defecto if valor is None else valor


def coleccion_a_dict(c: Any) -> Dict[str, Any]:
    """Colección de la API (Collection) -> dict plano y estable para la interfaz."""
    if not c:
        return {}
    etiqueta = _g(c, "tag") or {}
    items = []
    for it in _g(c, "items", []) or []:
        items.append({"id": str(_g(it, "id", "")), "account_id": str(_g(it, "account_id", "") or ""),
                      "state": _g(it, "state", "") or ""})
    return {
        "id": str(_g(c, "id", "")),
        "account_id": str(_g(c, "account_id", "")),
        "name": _g(c, "name", "") or "",
        "description": _g(c, "description", "") or "",
        "language": _g(c, "language", "") or "",
        "sensitive": bool(_g(c, "sensitive", False)),
        "discoverable": bool(_g(c, "discoverable", False)),
        "local": bool(_g(c, "local", False)),
        "tag": (_g(etiqueta, "name", "") or "") if etiqueta else "",
        "item_count": int(_g(c, "item_count", len(items)) or 0),
        "items": items,
        "url": _g(c, "url", "") or "",
    }


def _lista_de(respuesta: Any) -> list:
    """El listado viene envuelto: {"collections": [...]}. (La documentación de ejemplo trae un
    "collections:" con los dos puntos dentro de la clave; se acepta por si algún servidor lo
    reproduce.)"""
    if isinstance(respuesta, list):
        return respuesta
    for clave in ("collections", "collections:"):
        valor = _g(respuesta, clave)
        if valor is not None:
            return list(valor)
    return []


def validar_datos_coleccion(datos: Dict[str, Any]) -> Optional[str]:
    """Mensaje de error si los datos no cumplen los límites oficiales, o None."""
    nombre = (datos.get("name") or "").strip()
    if not nombre:
        return "La colección necesita un nombre."
    if len(nombre) > MAX_NOMBRE:
        return f"El nombre admite como máximo {MAX_NOMBRE} caracteres (tiene {len(nombre)})."
    if len(datos.get("description") or "") > MAX_DESCRIPCION:
        return f"La descripción admite como máximo {MAX_DESCRIPCION} caracteres."
    return None


def _cuerpo_coleccion(datos: Dict[str, Any]) -> Dict[str, Any]:
    cuerpo = {"name": (datos.get("name") or "").strip()}
    for clave in ("description", "language"):
        if datos.get(clave) is not None:
            cuerpo[clave] = (datos.get(clave) or "").strip()
    etiqueta = (datos.get("tag_name") or "").strip()
    if etiqueta:
        cuerpo["tag_name"] = etiqueta if etiqueta.startswith("#") else "#" + etiqueta
    for clave in ("sensitive", "discoverable"):
        if datos.get(clave) is not None:
            cuerpo[clave] = bool(datos.get(clave))
    return cuerpo


class _ColeccionesMixin:
    """Colecciones de cuentas destacadas (Mastodon 4.6+)."""

    def admite_colecciones(self, account_id: int) -> bool:
        return self.version_api(account_id) >= API_COLECCIONES

    def _cliente_colecciones(self, account_id: int):
        client = self.obtener_cliente(account_id)
        if self.version_api(account_id) and not self.admite_colecciones(account_id):
            raise MastodonServiceError(
                "Tu servidor todavía no admite colecciones (llegaron con Mastodon 4.6).")
        return client

    def _id_cuenta(self, account_id: int, client, acct: Optional[str]) -> str:
        """Id de la cuenta indicada por su @usuario (None o vacío = la tuya)."""
        if not acct:
            return str(self._obtener_me_id(account_id, client))
        cuenta = self._resolver_cuenta(client, acct)
        if not cuenta:
            raise MastodonServiceError(f"No se encontró la cuenta @{acct.lstrip('@')}.")
        return str(cuenta["id"])

    # ------------------------------------------------------------------ lectura
    def colecciones_de_cuenta_async(self, account_id: int, acct: Optional[str] = None,
                                    callback: Optional[Callable] = None):
        """Colecciones de una cuenta (acct=None: las tuyas, incluidas las no visibles)."""
        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            cid = self._id_cuenta(account_id, client, acct)
            res = self._peticion_directa(client, "GET", f"/api/v1/accounts/{cid}/collections",
                                         {"limit": 80})
            return [coleccion_a_dict(c) for c in _lista_de(res)]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def colecciones_donde_estoy_async(self, account_id: int, callback: Optional[Callable] = None):
        """Colecciones de otras personas en las que apareces tú."""
        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            yo = str(self._obtener_me_id(account_id, client))
            res = self._peticion_directa(client, "GET", f"/api/v1/accounts/{yo}/in_collections",
                                         {"limit": 80})
            colecciones = [coleccion_a_dict(c) for c in _lista_de(res)]
            for c in colecciones:
                # El elemento que te representa (para poder retirarte).
                c["mi_item"] = next((it for it in c["items"] if it["account_id"] == yo), None)
            return colecciones
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def ver_coleccion_async(self, account_id: int, coleccion_id: str, callback: Optional[Callable] = None):
        """Colección con sus cuentas: {"coleccion": {...}, "cuentas": [{item_id, estado,
        acct, nombre, id, bot}], "es_mia": bool}. El dueño ve también las pendientes."""
        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            res = self._peticion_directa(client, "GET", f"/api/v1/collections/{coleccion_id}")
            col = coleccion_a_dict(_g(res, "collection"))
            por_id = {str(_g(a, "id", "")): a for a in (_g(res, "accounts", []) or [])}
            cuentas: List[Dict[str, Any]] = []
            for it in col.get("items", []):
                a = por_id.get(it["account_id"]) or {}
                cuentas.append({
                    "item_id": it["id"], "estado": it["state"], "id": it["account_id"],
                    "acct": _g(a, "acct", "") or "",
                    "nombre": _g(a, "display_name", "") or _g(a, "username", "") or "",
                    "bot": bool(_g(a, "bot", False)),
                })
            yo = str(self._obtener_me_id(account_id, client))
            return {"coleccion": col, "cuentas": cuentas, "es_mia": col.get("account_id") == yo}
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------ escritura
    def crear_coleccion_async(self, account_id: int, datos: Dict[str, Any],
                              accts: Optional[List[str]] = None, callback: Optional[Callable] = None):
        """Crea una colección. accts: @usuarios a incluir desde el principio (opcional)."""
        error = validar_datos_coleccion(datos)
        if error:
            if callback:
                callback(None, MastodonServiceError(error))
            return

        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            cuerpo = _cuerpo_coleccion(datos)
            ids = [self._id_cuenta(account_id, client, a) for a in (accts or []) if a and a.strip()]
            if ids:
                cuerpo["account_ids"] = ids
            res = self._peticion_directa(client, "POST", "/api/v1/collections", cuerpo, use_json=True)
            return coleccion_a_dict(_g(res, "collection") or res)
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def editar_coleccion_async(self, account_id: int, coleccion_id: str, datos: Dict[str, Any],
                               callback: Optional[Callable] = None):
        error = validar_datos_coleccion(datos)
        if error:
            if callback:
                callback(None, MastodonServiceError(error))
            return

        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            res = self._peticion_directa(client, "PATCH", f"/api/v1/collections/{coleccion_id}",
                                         _cuerpo_coleccion(datos), use_json=True)
            return coleccion_a_dict(_g(res, "collection") or res)
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def borrar_coleccion_async(self, account_id: int, coleccion_id: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            self._peticion_directa(client, "DELETE", f"/api/v1/collections/{coleccion_id}")
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def anadir_a_coleccion_async(self, account_id: int, coleccion_id: str, acct: str,
                                 callback: Optional[Callable] = None):
        """Añade una cuenta. Devuelve {"estado": "accepted"|"pending"...}: según su política
        (feature_approval), la cuenta entra al momento o queda pendiente de que la acepte. Si no
        permite que la destaques, se avisa ANTES de intentarlo (el servidor daría error)."""
        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            cuenta = self._resolver_cuenta(client, acct)
            if not cuenta:
                raise MastodonServiceError(f"No se encontró la cuenta @{(acct or '').lstrip('@')}.")
            politica = _g(_g(cuenta, "feature_approval") or {}, "current_user", "") or ""
            if politica in ("denied", "unknown", "missing"):
                raise MastodonServiceError(
                    "Esa cuenta no permite que la añadas a una colección (su servidor o sus "
                    "preferencias no lo admiten).")
            res = self._peticion_directa(client, "POST", f"/api/v1/collections/{coleccion_id}/items",
                                         {"account_id": str(cuenta["id"])}, use_json=True)
            item = _g(res, "collection_item") or res
            return {"estado": _g(item, "state", "") or ("pending" if politica == "manual" else "accepted"),
                    "item_id": str(_g(item, "id", ""))}
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def quitar_de_coleccion_async(self, account_id: int, coleccion_id: str, item_id: str,
                                  callback: Optional[Callable] = None):
        """El dueño quita una cuenta de su colección."""
        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            self._peticion_directa(client, "DELETE", f"/api/v1/collections/{coleccion_id}/items/{item_id}")
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def retirarme_de_coleccion_async(self, account_id: int, coleccion_id: str, item_id: str,
                                     callback: Optional[Callable] = None):
        """Retira tu consentimiento para aparecer en la colección de otra persona."""
        def _bg(ctx):
            client = self._cliente_colecciones(account_id)
            self._peticion_directa(client, "POST",
                                   f"/api/v1/collections/{coleccion_id}/items/{item_id}/revoke")
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)
