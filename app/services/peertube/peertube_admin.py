# -*- coding: utf-8 -*-
"""Administración de la instancia (Fase 9 de PeerTube).

Mixin de `PeerTubeService`. Solo tiene efecto para cuentas ADMINISTRADORAS (el servidor rechaza el
resto). Cubre: gestión de usuarios (roles y cuotas), solicitudes de registro, federación entre
instancias (seguir/seguidores), listas de bloqueo de la instancia, trabajos (jobs), plugins y tokens
de registro de *runners*.

Lo NO soportado por la API como operación directa no se implementa a ciegas: la edición completa de
la configuración de la instancia (`config/custom`) es enorme y se ofrece solo como lectura/reseteo;
para el resto, la UI puede abrir la página de administración web.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)

# Roles (User.role): 0 administrador, 1 moderador, 2 usuario.
ROL_ADMIN = 0
ROL_MODERADOR = 1
ROL_USUARIO = 2
_ROL_ES = {0: "administrador", 1: "moderador", 2: "usuario"}


class _AdminMixin:
    # ------------------------------------------------------------------
    # Usuarios
    # ------------------------------------------------------------------
    def admin_listar_usuarios(self, account_id: int, *, buscar: str = "",
                              limite: int = 50) -> List[Dict[str, Any]]:
        params = {"sort": "-createdAt"}
        if buscar:
            params["search"] = buscar

        def _fn(cliente):
            data, _total = cliente.pagina("users", params=params, start=0, count=limite, auth=True)
            res = []
            for u in (data or []):
                rol = _g(_g(u, "role") or {}, "id")
                res.append({"id": _g(u, "id"), "username": _g(u, "username") or "",
                            "email": _g(u, "email") or "",
                            "rol": _ROL_ES.get(rol, ""), "rol_id": rol,
                            "bloqueado": bool(_g(u, "blocked", default=False)),
                            "cuota": _g(u, "videoQuota")})
            return res
        return self._ejecutar(account_id, _fn)

    def admin_crear_usuario(self, account_id: int, username: str, password: str, email: str, *,
                            rol: int = ROL_USUARIO, cuota: int = -1) -> Any:
        def _fn(cliente):
            cuerpo = {"username": username, "password": password, "email": email, "role": rol,
                      "videoQuota": cuota}
            datos = cliente.post("users", json_body=cuerpo, auth=True)
            return _g(_g(datos, "user") or {}, "id")
        return self._ejecutar(account_id, _fn)

    def admin_editar_usuario(self, account_id: int, user_id: Any, **campos) -> bool:
        """Edita un usuario (role, videoQuota, videoQuotaDaily, email, adminFlags...)."""
        cuerpo = {k: v for k, v in campos.items() if v is not None}

        def _fn(cliente):
            cliente.put(f"users/{user_id}", json_body=cuerpo, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def admin_borrar_usuario(self, account_id: int, user_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"users/{user_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def admin_bloquear_usuario(self, account_id: int, user_id: Any, *, bloquear: bool = True,
                               razon: str = "") -> bool:
        def _fn(cliente):
            if bloquear:
                cliente.post(f"users/{user_id}/block",
                             json_body={"reason": razon} if razon else None, auth=True)
            else:
                cliente.post(f"users/{user_id}/unblock", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Solicitudes de registro
    # ------------------------------------------------------------------
    def admin_listar_registros(self, account_id: int, *, limite: int = 25) -> List[Dict[str, Any]]:
        def _fn(cliente):
            data, _total = cliente.pagina("users/registrations", params={"sort": "-createdAt"},
                                          start=0, count=limite, auth=True)
            res = []
            for r in (data or []):
                res.append({"id": _g(r, "id"), "username": _g(r, "username") or "",
                            "email": _g(r, "email") or "", "estado": _g(_g(r, "state") or {}, "label") or "",
                            "motivo": _g(r, "registrationReason") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def admin_aceptar_registro(self, account_id: int, reg_id: Any, *, respuesta: str = "") -> bool:
        def _fn(cliente):
            cliente.post(f"users/registrations/{reg_id}/accept",
                         json_body={"moderationResponse": respuesta}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def admin_rechazar_registro(self, account_id: int, reg_id: Any, *, respuesta: str = "") -> bool:
        def _fn(cliente):
            cliente.post(f"users/registrations/{reg_id}/reject",
                         json_body={"moderationResponse": respuesta}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Federación (seguir / seguidores)
    # ------------------------------------------------------------------
    def admin_listar_siguiendo(self, account_id: int, *, limite: int = 50) -> List[Dict[str, Any]]:
        return self._listar_federacion(account_id, "server/following", "following", limite)

    def admin_listar_seguidores(self, account_id: int, *, limite: int = 50) -> List[Dict[str, Any]]:
        return self._listar_federacion(account_id, "server/followers", "follower", limite)

    def _listar_federacion(self, account_id: int, path: str, campo: str,
                           limite: int) -> List[Dict[str, Any]]:
        def _fn(cliente):
            data, _total = cliente.pagina(path, params={"sort": "-createdAt"}, start=0, count=limite,
                                          auth=True)
            res = []
            for f in (data or []):
                actor = _g(f, campo) or {}
                res.append({"host": _g(actor, "host") or _g(actor, "name") or "",
                            "estado": _g(f, "state") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    def admin_seguir_instancias(self, account_id: int, hosts: List[str]) -> bool:
        def _fn(cliente):
            cliente.post("server/following", json_body={"hosts": list(hosts)}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def admin_dejar_instancia(self, account_id: int, host: str) -> bool:
        def _fn(cliente):
            cliente.delete(f"server/following/{host}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def admin_aceptar_seguidor(self, account_id: int, handle: str, *, aceptar: bool = True) -> bool:
        def _fn(cliente):
            accion = "accept" if aceptar else "reject"
            cliente.post(f"server/followers/{handle}/{accion}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Listas de bloqueo de la instancia
    # ------------------------------------------------------------------
    def admin_bloquear_cuenta(self, account_id: int, handle: str, *, bloquear: bool = True) -> bool:
        handle = (handle or "").lstrip("@").strip()

        def _fn(cliente):
            if bloquear:
                cliente.post("server/blocklist/accounts", json_body={"accountName": handle},
                             auth=True)
            else:
                cliente.delete(f"server/blocklist/accounts/{handle}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def admin_bloquear_servidor(self, account_id: int, host: str, *, bloquear: bool = True) -> bool:
        def _fn(cliente):
            if bloquear:
                cliente.post("server/blocklist/servers", json_body={"host": host}, auth=True)
            else:
                cliente.delete(f"server/blocklist/servers/{host}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Trabajos (jobs) — solo lectura
    # ------------------------------------------------------------------
    def admin_listar_jobs(self, account_id: int, *, estado: str = "completed",
                          limite: int = 25) -> List[Dict[str, Any]]:
        def _fn(cliente):
            data, _total = cliente.pagina(f"jobs/{estado}", params={"sort": "-createdAt"}, start=0,
                                          count=limite, auth=True)
            res = []
            for j in (data or []):
                res.append({"id": _g(j, "id"), "tipo": _g(j, "type") or "",
                            "estado": _g(j, "state") or estado, "error": _g(j, "error") or ""})
            return res
        return self._ejecutar(account_id, _fn)

    # ------------------------------------------------------------------
    # Plugins
    # ------------------------------------------------------------------
    def admin_listar_plugins(self, account_id: int, *, limite: int = 50) -> List[Dict[str, Any]]:
        def _fn(cliente):
            data, _total = cliente.pagina("plugins", params={"sort": "name"}, start=0, count=limite,
                                          auth=True)
            res = []
            for p in (data or []):
                res.append({"nombre": _g(p, "name") or "", "npm": _g(p, "npmName") or "",
                            "version": _g(p, "version") or "", "tipo": _g(p, "type")})
            return res
        return self._ejecutar(account_id, _fn)

    def admin_instalar_plugin(self, account_id: int, npm_name: str) -> bool:
        def _fn(cliente):
            cliente.post("plugins/install", json_body={"npmName": npm_name}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    def admin_desinstalar_plugin(self, account_id: int, npm_name: str) -> bool:
        def _fn(cliente):
            cliente.post("plugins/uninstall", json_body={"npmName": npm_name}, auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Tokens de registro de runners (transcodificación remota)
    # ------------------------------------------------------------------
    def admin_generar_token_runner(self, account_id: int) -> str:
        def _fn(cliente):
            datos = cliente.post("runners/registration-tokens/generate", auth=True)
            return _g(datos, "registrationToken") or ""
        return self._ejecutar(account_id, _fn)

    def admin_listar_tokens_runner(self, account_id: int) -> List[Dict[str, Any]]:
        def _fn(cliente):
            data, _total = cliente.pagina("runners/registration-tokens", start=0, count=50, auth=True)
            return [{"id": _g(t, "id"), "token": _g(t, "registrationToken") or "",
                     "fecha": _g(t, "createdAt") or ""} for t in (data or [])]
        return self._ejecutar(account_id, _fn)

    def admin_borrar_token_runner(self, account_id: int, token_id: Any) -> bool:
        def _fn(cliente):
            cliente.delete(f"runners/registration-tokens/{token_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))

    # ------------------------------------------------------------------
    # Configuración de la instancia (lectura / reseteo)
    # ------------------------------------------------------------------
    def admin_config_custom(self, account_id: int) -> Dict[str, Any]:
        """Configuración personalizada completa de la instancia (para lectura). La edición completa
        es demasiado extensa para una UI accesible: se recomienda la web de administración."""
        def _fn(cliente):
            datos = cliente.get("config/custom", auth=True)
            return datos if isinstance(datos, dict) else {}
        return self._ejecutar(account_id, _fn)
