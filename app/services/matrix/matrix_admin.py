# -*- coding: utf-8 -*-
"""Administración de servidor Matrix (Synapse Admin API).

Solo funciona si (a) el servidor es Synapse y expone /_synapse/admin, y (b) TU cuenta es
administradora del servidor (server admin, distinto de administrador de una sala). Los endpoints
están documentados por el proyecto Synapse (Admin API); el único endpoint de administración que
está en la especificación oficial de Matrix es whois (/_matrix/client/v3/admin/whois).

Todas las llamadas son bloqueantes (usan _api_http de _AvanzadoMixin): invócalas desde
task_manager. Si el servidor no es Synapse o la cuenta no es admin, _api_http lanza MatrixError
con el mensaje del servidor (normalmente 403/404), que la UI muestra tal cual.
"""
import urllib.parse

from app.services.matrix.matrix_common import (
    Any, Dict, Optional, List, MatrixError, q_seg)


class _AdminMixin:
    # ---------------- Detección ----------------
    def es_admin_servidor(self, account_id: int) -> bool:
        """True si TU cuenta puede ADMINISTRAR el servidor Synapse.

        Se comprueba INTENTANDO un endpoint de administración (listar usuarios, limit 1): si el
        servidor responde, tienes acceso admin. Antes se leía el flag `is_admin` de la BD de Synapse
        (/_synapse/admin/v1/users/{uid}/admin), pero eso FALLA en servidores con MAS (Matrix
        Authentication Service): ahí el permiso de administración lo concede el SCOPE del token
        (urn:synapse:admin:*), y el flag is_admin de la BD puede estar a false aunque de verdad
        puedas administrar. Resultado del fallo: a un administrador de un servidor con MAS se le decía
        que NO lo era y no le dejaba administrar. Probar el endpoint funciona en AMBOS casos (admin
        clásico por flag y admin por scope de MAS). No lanza: ante 403 (no admin), 404 (no es
        Synapse) o cualquier error, devuelve False."""
        try:
            self._api_http(account_id, "GET", "/_synapse/admin/v2/users?from=0&limit=1")
            return True
        except MatrixError:
            return False

    def admin_version_servidor(self, account_id: int) -> str:
        """Versión de Synapse (para el diálogo de estadísticas)."""
        try:
            r = self._api_http(account_id, "GET", "/_synapse/admin/v1/server_version")
            return r.get("server_version") or ""
        except MatrixError:
            return ""

    # ---------------- Usuarios ----------------
    def admin_listar_usuarios(self, account_id: int, busqueda: str = "", limite: int = 100,
                              incluir_desactivados: bool = True) -> List[Dict[str, Any]]:
        """Cuentas del servidor. 'busqueda' filtra por nombre/localpart del lado servidor."""
        params = {"from": "0", "limit": str(max(1, limite)), "order_by": "name", "dir": "f"}
        if busqueda:
            params["name"] = busqueda
        if not incluir_desactivados:
            params["deactivated"] = "false"
        ruta = "/_synapse/admin/v2/users?" + urllib.parse.urlencode(params)
        r = self._api_http(account_id, "GET", ruta)
        salida = []
        for u in (r.get("users") or []):
            salida.append({
                "user_id": u.get("name") or "",
                "nombre": u.get("displayname") or "",
                "admin": bool(u.get("admin")),
                "desactivado": bool(u.get("deactivated")),
                "bloqueado": bool(u.get("locked")),
                "invitado": bool(u.get("is_guest")),
                "tipo": u.get("user_type") or "",
            })
        return salida

    def admin_detalle_usuario(self, account_id: int, user_id: str) -> Dict[str, Any]:
        ruta = "/_synapse/admin/v2/users/" + q_seg(user_id)
        return self._api_http(account_id, "GET", ruta) or {}

    def admin_desactivar_usuario(self, account_id: int, user_id: str, borrar_datos: bool = False):
        """Desactiva (cierra) una cuenta. Con borrar_datos=True además borra sus mensajes y perfil
        (GDPR erase). Es una acción fuerte y, sin reactivar con contraseña, irreversible."""
        ruta = "/_synapse/admin/v1/deactivate/" + q_seg(user_id)
        self._api_http(account_id, "POST", ruta, {"erase": bool(borrar_datos)})

    def admin_fijar_admin(self, account_id: int, user_id: str, es_admin: bool):
        """Concede o quita el rol de ADMINISTRADOR DEL SERVIDOR a una cuenta."""
        ruta = "/_synapse/admin/v2/users/" + q_seg(user_id)
        self._api_http(account_id, "PUT", ruta, {"admin": bool(es_admin)})

    def admin_bloquear_usuario(self, account_id: int, user_id: str, bloquear: bool):
        """Bloquea (lock) o desbloquea una cuenta: no puede iniciar sesión pero conserva sus datos.
        Más suave que desactivar."""
        ruta = "/_synapse/admin/v2/users/" + q_seg(user_id)
        self._api_http(account_id, "PUT", ruta, {"locked": bool(bloquear)})

    def admin_resetear_password(self, account_id: int, user_id: str, nueva: str,
                                cerrar_sesiones: bool = True):
        """Cambia la contraseña de una cuenta (útil para ayudar a alguien que la perdió)."""
        ruta = "/_synapse/admin/v1/reset_password/" + q_seg(user_id)
        self._api_http(account_id, "POST", ruta,
                       {"new_password": nueva, "logout_devices": bool(cerrar_sesiones)})

    def admin_salas_de_usuario(self, account_id: int, user_id: str) -> List[str]:
        """(Admin) Salas en las que participa un usuario (para ayudar/moderar)."""
        ruta = "/_synapse/admin/v1/users/" + q_seg(user_id) + "/joined_rooms"
        r = self._api_http(account_id, "GET", ruta) or {}
        return list(r.get("joined_rooms") or [])

    def admin_enviar_aviso_servidor(self, account_id: int, user_id: str, mensaje: str):
        """(Admin) Envía un AVISO DEL SERVIDOR a un usuario: le llega como un mensaje directo de la
        cuenta de avisos del servidor (útil para avisos de mantenimiento, advertencias, etc.).
        Requiere que el servidor tenga configurado 'server_notices'. Endpoint de la Admin API de
        Synapse (/_synapse/admin/v1/send_server_notice)."""
        cuerpo = {"user_id": user_id,
                  "content": {"msgtype": "m.text", "body": (mensaje or "").strip()}}
        return self._api_http(account_id, "POST", "/_synapse/admin/v1/send_server_notice", cuerpo)

    def admin_whois(self, account_id: int, user_id: str) -> List[Dict[str, Any]]:
        """Conexiones de un usuario (IP, hora y navegador de cada sesión). Endpoint oficial de la
        spec de Matrix (/_matrix/client/v3/admin/whois)."""
        ruta = "/_matrix/client/v3/admin/whois/" + q_seg(user_id)
        r = self._api_http(account_id, "GET", ruta) or {}
        salida = []
        for device_id, dev in (r.get("devices") or {}).items():
            for ses in (dev.get("sessions") or []):
                for con in (ses.get("connections") or []):
                    salida.append({
                        "dispositivo": device_id,
                        "ip": con.get("ip") or "",
                        "ultima_vez": con.get("last_seen") or 0,
                        "navegador": con.get("user_agent") or "",
                    })
        return salida

    # ---------------- Salas ----------------
    def admin_listar_salas(self, account_id: int, busqueda: str = "",
                           limite: int = 100) -> List[Dict[str, Any]]:
        """Todas las salas del servidor (no solo las tuyas). 'busqueda' filtra por nombre/alias."""
        params = {"from": "0", "limit": str(max(1, limite)), "order_by": "name", "dir": "f"}
        if busqueda:
            params["search_term"] = busqueda
        ruta = "/_synapse/admin/v1/rooms?" + urllib.parse.urlencode(params)
        r = self._api_http(account_id, "GET", ruta)
        salida = []
        for s in (r.get("rooms") or []):
            salida.append({
                "room_id": s.get("room_id") or "",
                "nombre": s.get("name") or s.get("canonical_alias") or s.get("room_id") or "",
                "alias": s.get("canonical_alias") or "",
                "miembros": int(s.get("joined_members") or 0),
                "cifrada": bool(s.get("encryption")),
                "publica": bool(s.get("public")),
                "creador": s.get("creator") or "",
            })
        return salida

    def admin_detalle_sala(self, account_id: int, room_id: str) -> Dict[str, Any]:
        ruta = "/_synapse/admin/v1/rooms/" + q_seg(room_id)
        return self._api_http(account_id, "GET", ruta) or {}

    def admin_borrar_sala(self, account_id: int, room_id: str, bloquear: bool = False,
                          purgar: bool = True) -> Dict[str, Any]:
        """Elimina una sala del servidor: echa a todos y (con purgar) borra su historia. Con
        bloquear=True, además impide que se vuelva a crear/entrar. Devuelve el resumen del server."""
        ruta = "/_synapse/admin/v1/rooms/" + q_seg(room_id)
        return self._api_http(account_id, "DELETE", ruta,
                              {"block": bool(bloquear), "purge": bool(purgar)}) or {}

    def admin_bloquear_sala(self, account_id: int, room_id: str, bloquear: bool = True):
        """(Admin) Bloquea o desbloquea una sala SIN borrarla: bloqueada, nadie puede entrar (los que
        estén dentro siguen hasta salir). Útil para congelar una sala problemática sin destruirla."""
        ruta = "/_synapse/admin/v1/rooms/" + q_seg(room_id) + "/block"
        return self._api_http(account_id, "PUT", ruta, {"block": bool(bloquear)}) or {}

    def admin_sala_bloqueada(self, account_id: int, room_id: str) -> bool:
        """(Admin) True si la sala está bloqueada."""
        ruta = "/_synapse/admin/v1/rooms/" + q_seg(room_id) + "/block"
        try:
            return bool((self._api_http(account_id, "GET", ruta) or {}).get("block"))
        except MatrixError:
            return False

    def admin_cuarentena_media_sala(self, account_id: int, room_id: str) -> int:
        """(Admin) Pone en CUARENTENA todos los archivos (imágenes/vídeos/adjuntos) de una sala: dejan
        de servirse a los usuarios (útil ante contenido ilegal o abusivo) sin borrarlos del disco.
        Devuelve cuántos se pusieron en cuarentena."""
        ruta = "/_synapse/admin/v1/room/" + q_seg(room_id) + "/media/quarantine"
        r = self._api_http(account_id, "POST", ruta, {}) or {}
        return int(r.get("num_quarantined") or 0)

    # ---------------- Informes (contenido reportado) ----------------
    def admin_listar_informes(self, account_id: int, limite: int = 100) -> List[Dict[str, Any]]:
        """Mensajes reportados por los usuarios del servidor, del más reciente al más antiguo."""
        params = {"from": "0", "limit": str(max(1, limite)), "dir": "b"}
        ruta = "/_synapse/admin/v1/event_reports?" + urllib.parse.urlencode(params)
        r = self._api_http(account_id, "GET", ruta)
        salida = []
        for e in (r.get("event_reports") or []):
            salida.append({
                "id": e.get("id"),
                "motivo": e.get("reason") or "",
                "quien_reporta": e.get("user_id") or "",
                "sala": e.get("canonical_alias") or e.get("name") or e.get("room_id") or "",
                "room_id": e.get("room_id") or "",
                "event_id": e.get("event_id") or "",
                "autor": e.get("sender") or "",
                "fecha": int(e.get("received_ts") or 0),
            })
        return salida

    def admin_detalle_informe(self, account_id: int, report_id) -> Dict[str, Any]:
        ruta = "/_synapse/admin/v1/event_reports/" + q_seg(str(report_id))
        return self._api_http(account_id, "GET", ruta) or {}

    # ---------------- Tokens de registro ----------------
    def admin_listar_tokens_registro(self, account_id: int) -> List[Dict[str, Any]]:
        """Tokens que permiten registrarse en un servidor de registro restringido."""
        r = self._api_http(account_id, "GET", "/_synapse/admin/v1/registration_tokens")
        salida = []
        for t in (r.get("registration_tokens") or []):
            salida.append({
                "token": t.get("token") or "",
                "usos_permitidos": t.get("uses_allowed"),   # None = ilimitado
                "usos_hechos": int(t.get("completed") or 0),
                "pendientes": int(t.get("pending") or 0),
                "caduca": t.get("expiry_time"),              # None = no caduca
            })
        return salida

    def admin_crear_token_registro(self, account_id: int, usos_permitidos: Optional[int] = None,
                                   longitud: int = 16) -> str:
        """Crea un token de registro. usos_permitidos None = ilimitado. Devuelve el token."""
        cuerpo: Dict[str, Any] = {"length": max(1, int(longitud))}
        if usos_permitidos is not None:
            cuerpo["uses_allowed"] = int(usos_permitidos)
        r = self._api_http(account_id, "POST", "/_synapse/admin/v1/registration_tokens/new", cuerpo)
        return (r or {}).get("token") or ""

    def admin_borrar_token_registro(self, account_id: int, token: str):
        ruta = "/_synapse/admin/v1/registration_tokens/" + q_seg(token)
        self._api_http(account_id, "DELETE", ruta)

    # ---------------- Estadísticas (resumen) ----------------
    def admin_estadisticas(self, account_id: int) -> Dict[str, Any]:
        """Resumen del servidor: versión, nº de usuarios y nº de salas. Combina varias llamadas;
        cada parte tolera fallo (devuelve lo que se pudo)."""
        datos: Dict[str, Any] = {"version": self.admin_version_servidor(account_id)}
        try:
            u = self._api_http(account_id, "GET",
                               "/_synapse/admin/v2/users?from=0&limit=1&guests=false")
            datos["usuarios_total"] = int(u.get("total") or 0)
        except MatrixError:
            datos["usuarios_total"] = None
        try:
            s = self._api_http(account_id, "GET", "/_synapse/admin/v1/rooms?from=0&limit=1")
            datos["salas_total"] = int(s.get("total_rooms") or 0)
        except MatrixError:
            datos["salas_total"] = None
        return datos
