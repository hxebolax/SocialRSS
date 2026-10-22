# -*- coding: utf-8 -*-
"""Administración AVANZADA de servidor Synapse (segunda tanda): suplantar usuario, límites de tasa,
sombra-baneo, dispositivos y archivos de un usuario, purgar historial, hacerse admin de una sala,
archivos de una sala, borrado/cuarentena de archivos concretos, federación y mantenimiento.

Todo son envoltorios finos sobre `_api_http` (Bearer con el token de la cuenta, que debe tener el
scope de administración). Bloqueantes: invócalos desde task_manager. Endpoints de la Synapse Admin
API (referencia: element-hq/synapse admin_api y el cliente synapse-admin de Awesome Technologies).
"""
import urllib.parse

from app.services.matrix.matrix_common import (Any, Dict, List, MatrixError, q_seg)


class _AdminExtMixin:
    # ================= USUARIOS =================
    def admin_login_como_usuario(self, account_id: int, user_id: str) -> str:
        """(Admin) SUPLANTAR: obtiene un token de acceso para iniciar sesión COMO ese usuario (útil
        para reproducir un problema que reporta). Devuelve el access_token. Acción muy delicada."""
        ruta = "/_synapse/admin/v1/users/" + q_seg(user_id) + "/login"
        r = self._api_http(account_id, "POST", ruta, {}) or {}
        tok = r.get("access_token") or ""
        if not tok:
            raise MatrixError(_("El servidor no devolvió una sesión para ese usuario."))
        return tok

    def admin_ver_ratelimit(self, account_id: int, user_id: str) -> Dict[str, Any]:
        """(Admin) Límite de tasa (mensajes/segundo y ráfaga) de un usuario. Vacío = usa el del
        servidor; messages_per_second=0 = SIN límite para ese usuario."""
        ruta = "/_synapse/admin/v1/users/" + q_seg(user_id) + "/override_ratelimit"
        try:
            return self._api_http(account_id, "GET", ruta) or {}
        except MatrixError:
            return {}

    def admin_fijar_ratelimit(self, account_id: int, user_id: str,
                              mensajes_por_segundo: int, rafaga: int):
        """(Admin) Fija un límite de tasa propio para el usuario (0 y 0 = sin límite)."""
        ruta = "/_synapse/admin/v1/users/" + q_seg(user_id) + "/override_ratelimit"
        return self._api_http(account_id, "POST", ruta, {
            "messages_per_second": int(mensajes_por_segundo), "burst_count": int(rafaga)}) or {}

    def admin_quitar_ratelimit(self, account_id: int, user_id: str):
        """(Admin) Quita el límite propio del usuario (vuelve al del servidor)."""
        ruta = "/_synapse/admin/v1/users/" + q_seg(user_id) + "/override_ratelimit"
        return self._api_http(account_id, "DELETE", ruta) or {}

    def admin_shadow_ban(self, account_id: int, user_id: str, banear: bool = True):
        """(Admin) SOMBRA-BANEO: el usuario cree que envía mensajes con normalidad, pero los demás no
        los reciben. banear=False lo quita."""
        ruta = "/_synapse/admin/v1/users/" + q_seg(user_id) + "/shadow_ban"
        metodo = "POST" if banear else "DELETE"
        return self._api_http(account_id, metodo, ruta, {} if banear else None) or {}

    def admin_dispositivos_usuario(self, account_id: int, user_id: str) -> List[Dict[str, Any]]:
        """(Admin) Dispositivos/sesiones de un usuario."""
        ruta = "/_synapse/admin/v2/users/" + q_seg(user_id) + "/devices"
        r = self._api_http(account_id, "GET", ruta) or {}
        salida = []
        for d in (r.get("devices") or []):
            salida.append({
                "id": d.get("device_id") or "",
                "nombre": d.get("display_name") or "",
                "ip": d.get("last_seen_ip") or "",
                "ultima_vez": d.get("last_seen_ts") or 0,
            })
        return salida

    def admin_borrar_dispositivo_usuario(self, account_id: int, user_id: str, device_id: str):
        """(Admin) Cierra una sesión concreta de un usuario."""
        ruta = ("/_synapse/admin/v2/users/" + q_seg(user_id) + "/devices/"
                + q_seg(device_id))
        return self._api_http(account_id, "DELETE", ruta) or {}

    def admin_archivos_usuario(self, account_id: int, user_id: str,
                               limite: int = 100) -> List[Dict[str, Any]]:
        """(Admin) Archivos (media) subidos por un usuario, del más reciente al más antiguo."""
        params = {"from": "0", "limit": str(max(1, limite)), "order_by": "created_ts", "dir": "b"}
        ruta = ("/_synapse/admin/v1/users/" + q_seg(user_id) + "/media?"
                + urllib.parse.urlencode(params))
        r = self._api_http(account_id, "GET", ruta) or {}
        salida = []
        for m in (r.get("media") or []):
            salida.append({
                "media_id": m.get("media_id") or "",
                "tipo": m.get("media_type") or "",
                "tamano": int(m.get("media_length") or 0),
                "creado": m.get("created_ts") or 0,
                "cuarentena": bool(m.get("quarantined_by")),
                "nombre": m.get("upload_name") or "",
            })
        return salida

    # ================= SALAS =================
    def admin_hacerme_admin_sala(self, account_id: int, room_id: str, user_id: str = ""):
        """(Admin del servidor) Se concede a sí mismo (o a 'user_id') permisos de ADMINISTRADOR EN LA
        SALA para poder moderarla, aunque no fuera miembro. Requiere que haya un admin local en la
        sala."""
        ruta = "/_synapse/admin/v1/rooms/" + q_seg(room_id) + "/make_room_admin"
        cuerpo = {"user_id": user_id} if user_id else {}
        return self._api_http(account_id, "POST", ruta, cuerpo) or {}

    def admin_purgar_historial(self, account_id: int, room_id: str, antes_de_ts_ms: int,
                               borrar_locales: bool = False) -> str:
        """(Admin) Purga (borra) la HISTORIA de una sala anterior a una fecha (ms epoch), liberando
        espacio. borrar_locales=True borra también los eventos locales (más agresivo). Devuelve el
        purge_id para consultar el estado con admin_estado_purga."""
        ruta = "/_synapse/admin/v1/purge_history/" + q_seg(room_id)
        r = self._api_http(account_id, "POST", ruta, {
            "delete_local_events": bool(borrar_locales),
            "purge_up_to_ts": int(antes_de_ts_ms)}) or {}
        return r.get("purge_id") or ""

    def admin_estado_purga(self, account_id: int, purge_id: str) -> str:
        """(Admin) Estado de una purga de historial: 'active', 'complete' o 'failed'."""
        ruta = "/_synapse/admin/v1/purge_history_status/" + q_seg(purge_id)
        r = self._api_http(account_id, "GET", ruta) or {}
        return r.get("status") or ""

    def admin_archivos_sala(self, account_id: int, room_id: str) -> List[str]:
        """(Admin) Identificadores de los archivos (media) usados en una sala."""
        ruta = "/_synapse/admin/v1/room/" + q_seg(room_id) + "/media"
        r = self._api_http(account_id, "GET", ruta) or {}
        return list(r.get("local") or []) + list(r.get("remote") or [])

    # ================= ARCHIVOS (MEDIA) =================
    def admin_borrar_media(self, account_id: int, server_name: str, media_id: str):
        """(Admin) Borra un archivo concreto del servidor (por servidor + media_id)."""
        ruta = ("/_synapse/admin/v1/media/" + q_seg(server_name) + "/"
                + q_seg(media_id))
        return self._api_http(account_id, "DELETE", ruta) or {}

    def admin_cuarentena_media(self, account_id: int, server_name: str, media_id: str,
                               poner: bool = True):
        """(Admin) Pone (o quita) en cuarentena UN archivo concreto."""
        verbo = "quarantine" if poner else "unquarantine"
        ruta = ("/_synapse/admin/v1/media/" + verbo + "/" + q_seg(server_name) + "/"
                + q_seg(media_id))
        return self._api_http(account_id, "POST", ruta, {}) or {}

    def admin_borrar_media_antiguos(self, account_id: int, antes_de_ts_ms: int,
                                    tamano_min: int = 0) -> int:
        """(Admin) Borra archivos LOCALES no usados recientemente (subidos/vistos antes de la fecha
        dada, ms epoch), para liberar disco. Devuelve cuántos se borraron."""
        params = {"before_ts": str(int(antes_de_ts_ms))}
        if tamano_min > 0:
            params["size_gt"] = str(int(tamano_min))
        ruta = "/_synapse/admin/v1/media/delete?" + urllib.parse.urlencode(params)
        r = self._api_http(account_id, "POST", ruta, {}) or {}
        return len(r.get("deleted_media") or [])

    # ================= FEDERACIÓN =================
    def admin_federacion_destinos(self, account_id: int, limite: int = 100) -> List[Dict[str, Any]]:
        """(Admin) Servidores remotos con los que este federa, y si hay problemas de conexión."""
        params = {"from": "0", "limit": str(max(1, limite)), "order_by": "destination", "dir": "f"}
        ruta = "/_synapse/admin/v1/federation/destinations?" + urllib.parse.urlencode(params)
        r = self._api_http(account_id, "GET", ruta) or {}
        salida = []
        for d in (r.get("destinations") or []):
            salida.append({
                "destino": d.get("destination") or "",
                "fallos_desde": d.get("failure_ts") or 0,
                "reintento_ts": d.get("retry_last_ts") or 0,
                "reintento_intervalo": d.get("retry_interval") or 0,
                "ultima_ok": d.get("last_successful_stream_ordering") or 0,
            })
        return salida

    def admin_federacion_reset(self, account_id: int, destino: str):
        """(Admin) Reintenta YA la conexión con un servidor remoto (borra el backoff de reintentos)."""
        ruta = ("/_synapse/admin/v1/federation/destinations/" + q_seg(destino)
                + "/reset_connection")
        return self._api_http(account_id, "POST", ruta, {}) or {}

    # ================= SERVIDOR =================
    def admin_actualizaciones_fondo(self, account_id: int) -> Dict[str, Any]:
        """(Admin) Estado de las actualizaciones en segundo plano de la base de datos (si están al
        día o migrando tras una actualización de Synapse)."""
        r = self._api_http(account_id, "GET", "/_synapse/admin/v1/background_updates/status") or {}
        return {"activadas": bool(r.get("enabled")),
                "en_curso": bool((r.get("current_updates") or {}))}
