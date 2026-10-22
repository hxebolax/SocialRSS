# -*- coding: utf-8 -*-
"""Matrix avanzado (tanda M7): todo el potencial restante de matrix-nio.

Perfil propio (nombre visible y avatar), presencia, gestión de dispositivos (listar,
renombrar, eliminar con contraseña), solicitud de claves de mensajes cifrados, ajustes
completos de sala (nombre/tema/alias/acceso/historial/cifrado/versión), rangos (power
levels), etiquetas de sala (favorita/baja prioridad, sincronizadas con el servidor),
usuarios ignorados, registro de cuentas nuevas, espacios (crear/añadir/quitar/jerarquía),
salir y olvidar, llamar a la puerta (knock) y límite de subida del servidor.
"""
import json as _json

from app.core.config_manager import config
from app.services.matrix.matrix_common import (
    Any, Dict, Optional, List, db, log, MatrixError, db_id_sala, ts_a_iso, q_seg)

def presencias_legibles():
    """Presencias válidas de la spec con su etiqueta legible en español. Es una función (no
    una constante) para no evaluar _() en el import, antes de instalarse gettext."""
    return (("online", _("Disponible")), ("unavailable", _("Ausente")),
            ("offline", _("Desconectado/a")))

# Etiquetas de sala de la spec (m.tag): favorita y baja prioridad.
ETIQUETAS_SALA = {"favorita": "m.favourite", "baja": "m.lowpriority"}


class _AvanzadoMixin:
    # ---------------- API HTTP directa (endpoints que nio no envuelve: tags, account_data) ----
    def _api_http(self, account_id: int, metodo: str, ruta: str, cuerpo: Optional[dict] = None,
                  _reintentos: int = 1):
        """Llamada directa a la client-server API con el token de la cuenta. Bloqueante
        (llamar desde task_manager). Para los pocos endpoints sin método en nio.

        Interpreta el JSON de error de Matrix (errcode/error) para dar un mensaje claro y respeta el
        límite de tasa (429 M_LIMIT_EXCEEDED) esperando retry_after_ms y reintentando una vez. Las
        rutas deben venir ya con sus segmentos codificados (quote(safe='')) por quien llama."""
        import time
        import urllib.request
        import urllib.error
        cliente = self._cliente_conectado(account_id)
        url = cliente.homeserver.rstrip("/") + ruta
        datos = _json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        req = urllib.request.Request(url, data=datos, method=metodo)
        req.add_header("Authorization", f"Bearer {cliente.access_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return _json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = _json.loads(e.read() or b"{}")
            except Exception:
                err = {}
            errcode = err.get("errcode") or ""
            # 429: el servidor limita la tasa. Esperar lo que pida (tope 10 s) y reintentar una vez.
            if e.code == 429 and _reintentos > 0:
                espera_ms = err.get("retry_after_ms")
                espera = min(10.0, (espera_ms / 1000.0) if espera_ms else 1.0)
                time.sleep(espera)
                return self._api_http(account_id, metodo, ruta, cuerpo, _reintentos - 1)
            msg = err.get("error") or errcode or f"HTTP {e.code}"
            raise MatrixError(_("El servidor rechazó la operación: {0}").format(msg))
        except Exception as e:
            raise MatrixError(_("El servidor rechazó la operación: {0}").format(e))

    # ---------------- Perfil propio y de otros ----------------
    def mi_perfil(self, account_id: int) -> Dict[str, str]:
        """Nombre visible y avatar (mxc) propios."""
        return self.perfil_de(account_id, self._user_id(account_id))

    def perfil_de(self, account_id: int, user_id: str) -> Dict[str, str]:
        cliente = self._cliente_conectado(account_id)
        from nio.responses import ProfileGetResponse
        resp = self._run(cliente.get_profile(user_id), timeout=30.0)
        if not isinstance(resp, ProfileGetResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        return {"nombre": resp.displayname or "", "avatar_mxc": resp.avatar_url or ""}

    def cambiar_nombre_visible(self, account_id: int, nombre: str):
        cliente = self._cliente_conectado(account_id)
        from nio.responses import ProfileSetDisplayNameResponse
        resp = self._run(cliente.set_displayname((nombre or "").strip()), timeout=30.0)
        if not isinstance(resp, ProfileSetDisplayNameResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    def cambiar_avatar(self, account_id: int, ruta: str):
        """Sube una imagen y la fija como avatar de la cuenta."""
        import os, mimetypes
        if not ruta or not os.path.exists(ruta):
            raise MatrixError(_("La imagen no existe."))
        mime = mimetypes.guess_type(ruta)[0] or ""
        if not mime.startswith("image/"):
            raise MatrixError(_("El avatar debe ser una imagen (png, jpg...)."))
        cliente = self._cliente_conectado(account_id)
        from nio import UploadResponse
        from nio.responses import ProfileSetAvatarResponse
        tamano = os.path.getsize(ruta)

        async def _subir():
            with open(ruta, "rb") as f:
                resp, _claves = await cliente.upload(
                    f, content_type=mime, filename=os.path.basename(ruta), filesize=tamano)
            return resp
        resp = self._run(_subir(), timeout=120.0)
        if not isinstance(resp, UploadResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        r2 = self._run(cliente.set_avatar(resp.content_uri), timeout=30.0)
        if not isinstance(r2, ProfileSetAvatarResponse):
            raise MatrixError(getattr(r2, "message", None) or str(r2))

    # ---------------- Perfil extendido (MSC4133 / Matrix v1.16) ----------------
    # Claves personalizadas del perfil: además del nombre y el avatar, el servidor puede
    # guardar campos arbitrarios (zona horaria, pronombres, un texto libre...). La API es
    # PUT/GET/DELETE /_matrix/client/v3/profile/{userId}/{clave}. La zona horaria tiene clave
    # estable 'm.tz' (v1.16) y, en servidores antiguos, la inestable 'us.cloke.msc4175.tz'.
    CLAVE_TZ = "m.tz"
    CLAVE_TZ_INESTABLE = "us.cloke.msc4175.tz"

    @staticmethod
    def campos_perfil_conocidos():
        """Etiqueta legible de las claves de perfil que la gente suele rellenar. Es función
        (no constante) para no evaluar _() en el import."""
        return {
            "m.tz": _("Zona horaria"),
            "us.cloke.msc4175.tz": _("Zona horaria"),
            "displayname": _("Nombre visible"),
            "avatar_url": _("Avatar"),
        }

    @staticmethod
    def zona_horaria_sistema() -> str:
        """Zona horaria IANA del equipo (p. ej. 'Europe/Madrid'). Para prerrellenar el diálogo."""
        try:
            import tzlocal
            return tzlocal.get_localzone_name() or ""
        except Exception:
            try:
                from datetime import datetime
                tz = datetime.now().astimezone().tzinfo
                return str(getattr(tz, "key", "") or getattr(tz, "zone", "") or "")
            except Exception:
                return ""

    @staticmethod
    def zonas_horarias_comunes() -> List[str]:
        """Lista de zonas IANA para elegir en el diálogo (todas si hay zoneinfo, si no un
        subconjunto útil). La zona del sistema se ofrece la primera."""
        try:
            import zoneinfo
            todas = sorted(zoneinfo.available_timezones())
            if todas:
                return todas
        except Exception:
            pass
        return ["Europe/Madrid", "Europe/London", "Europe/Paris", "Europe/Lisbon",
                "Atlantic/Canary", "America/Argentina/Buenos_Aires", "America/Mexico_City",
                "America/Bogota", "America/Santiago", "America/New_York", "UTC"]

    def leer_perfil_completo(self, account_id: int, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Perfil COMPLETO (todos los campos, incluidos los extendidos) de un usuario.
        GET /_matrix/client/v3/profile/{userId}. En servidores que no soportan MSC4133 el
        servidor solo devuelve displayname y avatar_url: no es un error."""
        uid = user_id or self._user_id(account_id)
        import urllib.parse
        ruta = "/_matrix/client/v3/profile/" + q_seg(uid)
        try:
            datos = self._api_http(account_id, "GET", ruta)
        except MatrixError:
            # Fallback al perfil clásico (solo nombre y avatar) si el servidor es antiguo.
            base = self.perfil_de(account_id, uid)
            return {"displayname": base.get("nombre", ""), "avatar_url": base.get("avatar_mxc", "")}
        return datos if isinstance(datos, dict) else {}

    def mi_perfil_completo(self, account_id: int) -> Dict[str, Any]:
        return self.leer_perfil_completo(account_id, self._user_id(account_id))

    def fijar_campo_perfil(self, account_id: int, clave: str, valor: Any):
        """Crea o cambia un campo del perfil extendido (MSC4133).
        PUT /_matrix/client/v3/profile/{userId}/{clave} con cuerpo {clave: valor}."""
        import urllib.parse
        clave = (clave or "").strip()
        if not clave:
            raise MatrixError(_("La clave del campo no puede estar vacía."))
        uid = self._user_id(account_id)
        ruta = ("/_matrix/client/v3/profile/" + q_seg(uid) +
                "/" + q_seg(clave, safe=""))
        self._api_http(account_id, "PUT", ruta, {clave: valor})

    def borrar_campo_perfil(self, account_id: int, clave: str):
        """Elimina un campo del perfil extendido.
        DELETE /_matrix/client/v3/profile/{userId}/{clave}."""
        import urllib.parse
        clave = (clave or "").strip()
        if clave in ("displayname", "avatar_url"):
            raise MatrixError(_("El nombre y el avatar se cambian desde sus propias opciones."))
        uid = self._user_id(account_id)
        ruta = ("/_matrix/client/v3/profile/" + q_seg(uid) +
                "/" + q_seg(clave, safe=""))
        self._api_http(account_id, "DELETE", ruta)

    def mi_zona_horaria(self, account_id: int) -> str:
        """Zona horaria guardada en el perfil (clave estable o la inestable), '' si no hay."""
        perfil = self.leer_perfil_completo(account_id)
        return (perfil.get(self.CLAVE_TZ) or perfil.get(self.CLAVE_TZ_INESTABLE) or "") if perfil else ""

    def fijar_zona_horaria(self, account_id: int, tz: str):
        """Guarda la zona horaria IANA (p. ej. 'Europe/Madrid') en el perfil. Escribe la clave
        estable y también la inestable para que la vean clientes que aún no soportan v1.16.
        Con tz vacío, borra ambas."""
        tz = (tz or "").strip()
        if not tz:
            for clave in (self.CLAVE_TZ, self.CLAVE_TZ_INESTABLE):
                try:
                    self.borrar_campo_perfil(account_id, clave)
                except MatrixError:
                    pass
            return
        # La estable es obligatoria; la inestable es un extra tolerante a fallos.
        self.fijar_campo_perfil(account_id, self.CLAVE_TZ, tz)
        try:
            self.fijar_campo_perfil(account_id, self.CLAVE_TZ_INESTABLE, tz)
        except MatrixError:
            pass

    # ---------------- Recibos de lectura (quién ha leído hasta aquí) ----------------
    def quien_ha_leido(self, account_id: int, room_id: str, event_id: str,
                       fecha_iso: str = "") -> List[str]:
        """Nombres de los miembros (menos tú) que han LEÍDO hasta el mensaje 'event_id' o más allá.
        Los recibos llegan por el sync (m.receipt) y se guardan en self._recibos. Para decidir si
        un usuario ha leído ESTE mensaje se compara la posición en el tiempo: el evento que marcó
        como leído debe ser el mismo o POSTERIOR. Se ordena por la fecha guardada en la BD (el
        timeline es cronológico); si el evento del recibo no está en la BD, se cae a comparar por la
        marca de tiempo del recibo frente a 'fecha_iso' del mensaje."""
        recibos = dict(self._recibos.get((account_id, room_id), {}))
        if not recibos:
            return []
        yo = self._user_id(account_id)
        # Fecha (id de fila) del mensaje objetivo, para ordenar.
        objetivo = db.query_one(
            "SELECT id, date FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
            (account_id, event_id))
        obj_id = objetivo[0] if objetivo else None
        obj_fecha = (objetivo[1] if objetivo else "") or fecha_iso
        nombres = []
        cliente = None
        try:
            cliente = self._cliente_conectado(account_id)
        except MatrixError:
            cliente = None
        for uid, (ev_leido, ts_ms) in recibos.items():
            if uid == yo:
                continue
            leido = False
            if ev_leido and ev_leido == event_id:
                leido = True
            elif obj_id is not None and ev_leido:
                fila = db.query_one(
                    "SELECT id FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
                    (account_id, ev_leido))
                if fila and fila[0] >= obj_id:
                    leido = True
                elif not fila and ts_ms and obj_fecha:
                    leido = self._ts_ms_posterior(ts_ms, obj_fecha)
            elif ts_ms and obj_fecha:
                leido = self._ts_ms_posterior(ts_ms, obj_fecha)
            if leido:
                nombre = uid
                if cliente is not None:
                    sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)
                    if sala is not None:
                        nombre = sala.user_name(uid) or uid
                nombres.append(nombre)
        nombres.sort(key=lambda s: s.lower())
        return nombres

    @staticmethod
    def _ts_ms_posterior(ts_ms: int, fecha_iso: str) -> bool:
        """True si el instante ts_ms (epoch ms) es >= la fecha ISO del mensaje."""
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat((fecha_iso or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return ts_ms >= int(dt.timestamp() * 1000)
        except Exception:
            return False

    # ---------------- Varios de nio que faltaban ----------------
    def registrar_con_token(self, homeserver: str, usuario: str, password: str,
                            token: str) -> int:
        """Crea una cuenta en un servidor de registro RESTRINGIDO usando un token de registro
        (los que genera un administrador). Envuelve nio.register_with_token. Devuelve el account_id
        local ya creado, igual que crear_cuenta."""
        from app.services.matrix.matrix_common import normalizar_homeserver
        base = normalizar_homeserver(homeserver)
        if not base:
            raise MatrixError(_("Escribe el servidor."))
        from nio import AsyncClient, RegisterResponse
        cliente = AsyncClient(base, usuario)
        try:
            resp = self._run(cliente.register_with_token(usuario, password, token), timeout=60.0)
            if not isinstance(resp, RegisterResponse):
                raise MatrixError(getattr(resp, "message", None) or str(resp))
            uid = resp.user_id
            device_id = resp.device_id
            token_acc = resp.access_token
        finally:
            try:
                self._run(cliente.close(), timeout=10.0)
            except Exception:
                pass
        # Reutiliza el alta de cuenta ya existente (guarda token cifrado, crea la fila, etc.).
        return self._persistir_cuenta(base, uid, device_id, token_acc)

    def resolver_alias(self, account_id: int, alias: str) -> Dict[str, Any]:
        """Resuelve un alias (#sala:servidor) a su room_id y servidores, SIN unirse. Útil para saber
        si una sala existe antes de entrar. Devuelve {'room_id':..., 'servers':[...]}."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomResolveAliasResponse
        resp = self._run(cliente.room_resolve_alias(alias), timeout=30.0)
        if not isinstance(resp, RoomResolveAliasResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        return {"room_id": resp.room_id, "servers": list(getattr(resp, "servers", None) or [])}

    def permitir_llamar_puerta(self, account_id: int, room_id: str):
        """En una sala que administras, activa la regla de acceso 'knock' para que la gente pueda
        LLAMAR A LA PUERTA (pedir entrar). Envuelve nio.room_enable_knocking."""
        cliente = self._cliente_conectado(account_id)
        # room_enable_knocking() de nio 0.25.2 hace un room_put_state de m.room.join_rules y
        # devuelve RoomPutStateResponse / RoomPutStateError (NO existe RoomEnableKnockingResponse:
        # importarla lanzaba ImportError y la acción fallaba siempre).
        from nio.responses import RoomPutStateResponse
        resp = self._run(cliente.room_enable_knocking(room_id), timeout=30.0)
        if not isinstance(resp, RoomPutStateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    # ---------------- Presencia ----------------
    def fijar_presencia(self, account_id: int, presencia: str, mensaje: str = ""):
        """online / unavailable / offline + mensaje de estado opcional. Se recuerda en config."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import PresenceSetResponse
        resp = self._run(cliente.set_presence(presencia, status_msg=(mensaje or None)),
                         timeout=30.0)
        if not isinstance(resp, PresenceSetResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        config.set("matrix", f"presence_{account_id}", [presencia, mensaje or ""])

    def presencia_de(self, account_id: int, user_id: str) -> str:
        """Presencia de un usuario EN PALABRAS ('Disponible desde hace 5 minutos', ...)."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import PresenceGetResponse
        resp = self._run(cliente.get_presence(user_id), timeout=30.0)
        if not isinstance(resp, PresenceGetResponse):
            return ""
        etiqueta = dict(presencias_legibles()).get(resp.presence or "", resp.presence or "")
        partes = [etiqueta]
        try:
            if resp.last_active_ago is not None:
                minutos = int(resp.last_active_ago / 60000)
                if minutos >= 60:
                    partes.append(_("(activo/a hace {0} horas)").format(minutos // 60))
                elif minutos > 0:
                    partes.append(_("(activo/a hace {0} minutos)").format(minutos))
        except Exception:
            pass
        if getattr(resp, "status_msg", None):
            partes.append(f"— {resp.status_msg}")
        return " ".join(p for p in partes if p)

    # ---------------- Dispositivos de la cuenta ----------------
    def mis_dispositivos(self, account_id: int) -> List[Dict[str, Any]]:
        """Sesiones de la cuenta: [{id, nombre, ip, ultima_vez}] ordenadas por actividad."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import DevicesResponse
        resp = self._run(cliente.devices(), timeout=30.0)
        if not isinstance(resp, DevicesResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        salida = []
        for d in (resp.devices or []):
            ts = getattr(d, "last_seen_date", None)
            salida.append({
                "id": d.id,
                "nombre": getattr(d, "display_name", "") or "",
                "ip": getattr(d, "last_seen_ip", "") or "",
                "ultima_vez": ts.strftime("%d/%m/%Y %H:%M") if ts else "",
                "actual": d.id == (getattr(cliente, "device_id", "") or ""),
            })
        salida.sort(key=lambda x: (not x["actual"], x["nombre"].lower()))
        return salida

    def renombrar_dispositivo(self, account_id: int, device_id: str, nombre: str):
        cliente = self._cliente_conectado(account_id)
        from nio.responses import UpdateDeviceResponse
        resp = self._run(cliente.update_device(device_id, {"display_name": nombre}), timeout=30.0)
        if not isinstance(resp, UpdateDeviceResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    def eliminar_dispositivos(self, account_id: int, device_ids: List[str], password: str):
        """Cierra la sesión de otros dispositivos (requiere la contraseña de la cuenta: el
        servidor exige autenticación interactiva para esta operación)."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import DeleteDevicesResponse, DeleteDevicesAuthResponse
        user_id = self._user_id(account_id)
        auth = {"type": "m.login.password", "user": user_id, "password": password,
                "identifier": {"type": "m.id.user", "user": user_id}}
        resp = self._run(cliente.delete_devices(device_ids, auth=auth), timeout=45.0)
        if isinstance(resp, DeleteDevicesAuthResponse):
            raise MatrixError(_("El servidor pidió más autenticación de la esperada."))
        if not isinstance(resp, DeleteDevicesResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    # ---------------- Claves de mensajes cifrados ----------------
    def solicitar_claves_mensaje(self, account_id: int, room_id: str, event_id: str):
        """Pide a los propios dispositivos la clave de un mensaje que no se pudo descifrar
        (key request por to-device). La respuesta llega sola por el sync si otro dispositivo
        nuestro la tiene."""
        cliente = self._cliente_conectado(account_id)
        from nio import MegolmEvent
        from nio.responses import RoomGetEventResponse
        resp = self._run(cliente.room_get_event(room_id, event_id), timeout=30.0)
        if not isinstance(resp, RoomGetEventResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        ev = resp.event
        if not isinstance(ev, MegolmEvent):
            raise MatrixError(_("Este mensaje ya no está cifrado (puede que ya se descifrara)."))
        if not ev.room_id:
            ev.room_id = room_id
        try:
            self._run(cliente.request_room_key(ev), timeout=30.0)
        except Exception as e:
            # nio lanza LocalProtocolError si la petición ya estaba en curso: no es un error real.
            if "already" in str(e).lower():
                return
            raise MatrixError(str(e))

    # ---------------- Ajustes de sala ----------------
    def estado_sala(self, account_id: int, room_id: str) -> Dict[str, Any]:
        """Ajustes actuales de la sala para el diálogo: nombre, tema, alias, regla de acceso,
        visibilidad del historial, cifrado y versión."""
        cliente = self._cliente_conectado(account_id)
        sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)
        datos = {
            "nombre": getattr(sala, "name", "") or "" if sala else "",
            "tema": getattr(sala, "topic", "") or "" if sala else "",
            "cifrada": bool(getattr(sala, "encrypted", False)) if sala else False,
            "version": getattr(sala, "room_version", "") or "" if sala else "",
            "alias": getattr(sala, "canonical_alias", "") or "" if sala else "",
            "acceso": "invite", "historial": "shared",
        }
        from nio.responses import RoomGetStateEventResponse
        for tipo, clave, campo in (("m.room.join_rules", "join_rule", "acceso"),
                                   ("m.room.history_visibility", "history_visibility", "historial")):
            try:
                r = self._run(cliente.room_get_state_event(room_id, tipo), timeout=20.0)
                if isinstance(r, RoomGetStateEventResponse) and isinstance(r.content, dict):
                    datos[campo] = r.content.get(clave) or datos[campo]
            except Exception:
                pass
        return datos

    def configurar_sala(self, account_id: int, room_id: str, cambios: Dict[str, Any]):
        """Aplica ajustes de sala (solo los presentes en `cambios`): nombre, tema, acceso
        (public/invite/knock), historial (world_readable/shared/invited/joined). El servidor
        valida los permisos."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomPutStateResponse
        acciones = []
        if "nombre" in cambios:
            acciones.append(("m.room.name", {"name": cambios["nombre"]}, ""))
        if "tema" in cambios:
            acciones.append(("m.room.topic", {"topic": cambios["tema"]}, ""))
        if "acceso" in cambios:
            acciones.append(("m.room.join_rules", {"join_rule": cambios["acceso"]}, ""))
        if "historial" in cambios:
            acciones.append(("m.room.history_visibility",
                             {"history_visibility": cambios["historial"]}, ""))
        for tipo, contenido, sk in acciones:
            resp = self._run(cliente.room_put_state(room_id, tipo, contenido, state_key=sk),
                             timeout=30.0)
            if not isinstance(resp, RoomPutStateResponse):
                raise MatrixError(getattr(resp, "message", None) or str(resp))
        if "nombre" in cambios:
            db.execute("UPDATE matrix_salas SET nombre = ? WHERE id = ?",
                       (cambios["nombre"], db_id_sala(account_id, room_id)))
        if "tema" in cambios:
            db.execute("UPDATE matrix_salas SET tema = ? WHERE id = ?",
                       (cambios["tema"], db_id_sala(account_id, room_id)))

    def fijar_alias_sala(self, account_id: int, room_id: str, alias: str):
        """Publica un alias (#nombre:servidor) y lo fija como alias canónico de la sala."""
        alias = (alias or "").strip()
        if not alias.startswith("#") or ":" not in alias:
            raise MatrixError(_("El alias debe tener la forma #nombre:servidor."))
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomPutAliasResponse, RoomPutStateResponse
        resp = self._run(cliente.room_put_alias(alias, room_id), timeout=30.0)
        if not isinstance(resp, RoomPutAliasResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        r2 = self._run(cliente.room_put_state(room_id, "m.room.canonical_alias",
                                              {"alias": alias}, state_key=""), timeout=30.0)
        if not isinstance(r2, RoomPutStateResponse):
            raise MatrixError(getattr(r2, "message", None) or str(r2))

    def activar_cifrado_sala(self, account_id: int, room_id: str):
        """Activa el cifrado E2EE de la sala. IRREVERSIBLE (la UI debe confirmar con énfasis)."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomPutStateResponse
        resp = self._run(cliente.room_put_state(room_id, "m.room.encryption",
                                                {"algorithm": "m.megolm.v1.aes-sha2"},
                                                state_key=""), timeout=30.0)
        if not isinstance(resp, RoomPutStateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        db.execute("UPDATE matrix_salas SET cifrada = 1 WHERE id = ?",
                   (db_id_sala(account_id, room_id),))

    def actualizar_version_sala(self, account_id: int, room_id: str, nueva_version: str) -> str:
        """Actualiza la sala a otra versión (crea la sala sucesora). Devuelve el room_id nuevo."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomUpgradeResponse
        resp = self._run(cliente.room_upgrade(room_id, nueva_version), timeout=60.0)
        if not isinstance(resp, RoomUpgradeResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        # RoomUpgradeResponse hereda de RoomCreateResponse: la sala sucesora es room_id.
        return resp.room_id

    def fijar_rango(self, account_id: int, room_id: str, user_id: str, nivel: int):
        """Cambia el rango de un usuario (0 miembro, 50 moderador/a, 100 administrador/a)
        editando m.room.power_levels. El servidor valida que puedas."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomGetStateEventResponse, RoomPutStateResponse
        r = self._run(cliente.room_get_state_event(room_id, "m.room.power_levels"), timeout=30.0)
        if not isinstance(r, RoomGetStateEventResponse) or not isinstance(r.content, dict):
            raise MatrixError(_("No se pudieron leer los rangos actuales de la sala."))
        contenido = dict(r.content)
        usuarios = dict(contenido.get("users") or {})
        usuarios[user_id] = int(nivel)
        contenido["users"] = usuarios
        resp = self._run(cliente.room_put_state(room_id, "m.room.power_levels", contenido,
                                                state_key=""), timeout=30.0)
        if not isinstance(resp, RoomPutStateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    def salir_sala(self, account_id: int, room_id: str, olvidar: bool = False):
        """Sale de la sala; con `olvidar` además la borra del historial del servidor
        (forget: no volverá a aparecer ni siquiera como sala abandonada)."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomLeaveResponse, RoomForgetResponse
        resp = self._run(cliente.room_leave(room_id), timeout=30.0)
        if not isinstance(resp, RoomLeaveResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        if olvidar:
            r2 = self._run(cliente.room_forget(room_id), timeout=30.0)
            if not isinstance(r2, RoomForgetResponse):
                log.debug(f"Matrix: forget no aplicado: {r2}")
        db.execute("DELETE FROM matrix_mensajes WHERE account_id = ? AND room_id = ?",
                   (account_id, room_id))
        db.execute("DELETE FROM matrix_salas WHERE account_id = ? AND room_id = ?",
                   (account_id, room_id))

    def llamar_puerta(self, account_id: int, sala: str, motivo: str = "") -> str:
        """Llama a la puerta (knock) de una sala que lo permita: pide entrar y los moderadores
        deciden. Acepta id (!...) o alias (#...). Devuelve el room_id."""
        sala = (sala or "").strip()
        if not sala:
            raise MatrixError(_("Escribe el alias o el identificador de la sala."))
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomKnockResponse
        resp = self._run(cliente.room_knock(sala, reason=(motivo or None)), timeout=45.0)
        if not isinstance(resp, RoomKnockResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        return resp.room_id

    # ---------------- Etiquetas de sala (tags del servidor) ----------------
    def fijar_etiqueta_sala(self, account_id: int, room_id: str, etiqueta: str):
        """'favorita', 'baja' o '' (ninguna). Se guarda en el SERVIDOR (m.tag), así Element y
        los demás clientes ven lo mismo, y en la BD local para ordenar la lista."""
        import urllib.parse
        user_id = q_seg(self._user_id(account_id))
        rid = q_seg(room_id)
        # Quitar las dos etiquetas conocidas y poner la elegida (son excluyentes entre sí).
        for tag in ETIQUETAS_SALA.values():
            try:
                self._api_http(account_id, "DELETE",
                               f"/_matrix/client/v3/user/{user_id}/rooms/{rid}/tags/{q_seg(tag)}")
            except MatrixError:
                pass  # no estaba puesta
        if etiqueta in ETIQUETAS_SALA:
            self._api_http(account_id, "PUT",
                           f"/_matrix/client/v3/user/{user_id}/rooms/{rid}/tags/"
                           f"{q_seg(ETIQUETAS_SALA[etiqueta])}", {"order": 0.5})
        db.execute("UPDATE matrix_salas SET etiqueta = ? WHERE id = ?",
                   (etiqueta or "", db_id_sala(account_id, room_id)))

    def _aplicar_tag_evento(self, account_id: int, room_id: str, tags: Dict[str, Any]):
        """(Hilo MatrixLoop) Un TagEvent del sync: refleja la etiqueta del servidor en la BD
        (cambios hechos desde Element se ven aquí)."""
        etiqueta = ""
        if "m.favourite" in (tags or {}):
            etiqueta = "favorita"
        elif "m.lowpriority" in (tags or {}):
            etiqueta = "baja"
        db.execute("UPDATE matrix_salas SET etiqueta = ? WHERE id = ?",
                   (etiqueta, db_id_sala(account_id, room_id)))

    # ---------------- Usuarios ignorados ----------------
    def usuarios_ignorados(self, account_id: int) -> List[str]:
        return list(config.get("matrix", f"ignored_{account_id}", []) or [])

    def ignorar_usuario(self, account_id: int, user_id: str, ignorar: bool = True):
        """Ignora/deja de ignorar a un usuario EN EL SERVIDOR (m.ignored_user_list): sus
        mensajes dejan de llegar en todos tus clientes. Localmente se filtra al instante."""
        import urllib.parse
        actuales = set(self.usuarios_ignorados(account_id))
        if ignorar:
            actuales.add(user_id)
        else:
            actuales.discard(user_id)
        uid = q_seg(self._user_id(account_id))
        self._api_http(account_id, "PUT",
                       f"/_matrix/client/v3/user/{uid}/account_data/m.ignored_user_list",
                       {"ignored_users": {u: {} for u in sorted(actuales)}})
        config.set("matrix", f"ignored_{account_id}", sorted(actuales))
        with self._estado_lock:
            self._ignorados[account_id] = actuales

    def _es_ignorado(self, account_id: int, user_id: str) -> bool:
        with self._estado_lock:
            ignorados = self._ignorados.get(account_id)
        if ignorados is None:
            ignorados = set(self.usuarios_ignorados(account_id))
            with self._estado_lock:
                self._ignorados[account_id] = ignorados
        return user_id in ignorados

    def _aplicar_ignorados_evento(self, account_id: int, contenido: Dict[str, Any]):
        """(Hilo MatrixLoop) m.ignored_user_list llegó por el sync (cambio desde otro cliente)."""
        try:
            usuarios = sorted((contenido or {}).get("ignored_users", {}).keys())
            config.set("matrix", f"ignored_{account_id}", usuarios)
            with self._estado_lock:
                self._ignorados[account_id] = set(usuarios)
        except Exception as e:
            log.debug(f"Matrix: ignored_user_list no aplicada: {e}")

    # ---------------- Límite de subida del servidor ----------------
    def limite_subida(self, account_id: int) -> int:
        """Tamaño máximo de subida que anuncia el servidor (bytes; 0 = desconocido). Cacheado."""
        with self._estado_lock:
            if account_id in self._limites_subida:
                return self._limites_subida[account_id]
        try:
            cliente = self._cliente_conectado(account_id)
            from nio.responses import ContentRepositoryConfigResponse
            resp = self._run(cliente.content_repository_config(), timeout=20.0)
            limite = int(resp.upload_size or 0) if isinstance(resp, ContentRepositoryConfigResponse) else 0
        except Exception:
            limite = 0
        with self._estado_lock:
            self._limites_subida[account_id] = limite
        return limite

    # ---------------- Registro de cuenta nueva ----------------
    def registrar_cuenta(self, homeserver: str, usuario: str, password: str) -> int:
        """Registra una cuenta NUEVA en el homeserver (solo servidores con registro abierto
        sencillo; matrix.org exige captcha en navegador y dará error claro). Si va bien,
        inicia sesión y crea la cuenta en la app. Devuelve el account_id."""
        from app.services.matrix.matrix_common import normalizar_homeserver
        hs = normalizar_homeserver(homeserver)
        from nio.responses import RegisterResponse

        async def _registrar():
            cliente = self._crear_cliente(hs)
            try:
                return await cliente.register(usuario, password, device_name="SocialRSS")
            finally:
                try:
                    await cliente.close()
                except Exception:
                    pass
        resp = self._run(_registrar(), timeout=60.0)
        if not isinstance(resp, RegisterResponse):
            msg = getattr(resp, "message", None) or str(resp)
            if "401" in msg or "flow" in msg.lower() or "auth" in msg.lower():
                raise MatrixError(_("Este servidor exige pasos extra (captcha/correo) que solo "
                                    "se pueden hacer en su web. Regístrate en el navegador y "
                                    "luego añade la cuenta aquí."))
            raise MatrixError(msg)
        # register() YA devuelve access_token + device_id: se persiste esa sesión DIRECTAMENTE. Un
        # segundo login (crear_cuenta) creaba un dispositivo extra innecesario y podía fallar en
        # servidores con políticas de login distintas tras el registro.
        return self._persistir_cuenta(hs, resp.user_id, resp.device_id, resp.access_token)

    # ---------------- Espacios ----------------
    def crear_espacio(self, account_id: int, nombre: str, tema: str = "",
                      publico: bool = False) -> str:
        """Crea un ESPACIO (contenedor de salas). Devuelve su room_id."""
        nombre = (nombre or "").strip()
        if not nombre:
            raise MatrixError(_("Escribe un nombre para el espacio."))
        cliente = self._cliente_conectado(account_id)
        from nio import RoomVisibility
        from nio.responses import RoomCreateResponse
        resp = self._run(cliente.room_create(
            visibility=RoomVisibility.public if publico else RoomVisibility.private,
            name=nombre, topic=tema or None, room_type="m.space"), timeout=45.0)
        if not isinstance(resp, RoomCreateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        db.execute("""
            INSERT INTO matrix_salas (id, account_id, room_id, nombre, tema, es_espacio)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET nombre = excluded.nombre, es_espacio = 1
        """, (db_id_sala(account_id, resp.room_id), account_id, resp.room_id, nombre, tema or ""))
        return resp.room_id

    def mis_espacios(self, account_id: int) -> List[tuple]:
        """Espacios conocidos de la cuenta: [(room_id, nombre)]."""
        return [(r, n) for r, n in db.query(
            "SELECT room_id, nombre FROM matrix_salas WHERE account_id = ? AND es_espacio = 1 "
            "ORDER BY nombre COLLATE NOCASE", (account_id,))]

    def sala_a_espacio(self, account_id: int, space_id: str, room_id: str, quitar: bool = False):
        """Añade (o quita) una sala como hija de un espacio (m.space.child en el ESPACIO).
        Hace falta permiso de estado en el espacio; el servidor valida."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomPutStateResponse
        contenido: Dict[str, Any] = {}
        if not quitar:
            dominio = (self._user_id(account_id).split(":", 1) + [""])[1]
            contenido = {"via": [d for d in [dominio] if d]}
        resp = self._run(cliente.room_put_state(space_id, "m.space.child", contenido,
                                                state_key=room_id), timeout=30.0)
        if not isinstance(resp, RoomPutStateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    def jerarquia_espacio(self, account_id: int, space_id: str) -> List[Dict[str, Any]]:
        """Jerarquía del espacio según el SERVIDOR (incluye salas a las que aún no te has
        unido, con su nombre y nº de miembros). Complementa a salas_de_espacio (estado local)."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import SpaceGetHierarchyResponse
        resp = self._run(cliente.space_get_hierarchy(space_id, limit=200), timeout=60.0)
        if not isinstance(resp, SpaceGetHierarchyResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        salida = []
        for c in (getattr(resp, "rooms", None) or []):
            get = (lambda k, d="": (c.get(k, d) if isinstance(c, dict) else getattr(c, k, d)))
            rid = get("room_id")
            if not rid or rid == space_id:
                continue
            salida.append({
                "room_id": rid,
                "nombre": get("name") or get("canonical_alias") or rid,
                "tema": get("topic") or "",
                "miembros": int(get("num_joined_members", 0) or 0),
                "es_espacio": (get("room_type") == "m.space"),
            })
        return salida

    # ---------------- Reglas de notificación del servidor (push rules) ----------------
    def silenciar_sala_servidor(self, account_id: int, room_id: str, silenciar: bool = True):
        """Silencia (o quita el silencio de) una sala EN EL SERVIDOR con una push rule de tipo
        'room' con acción 'no notificar'. Así Element y el resto de clientes también la ven
        silenciada. Se llama al fijar el nivel de aviso local a 'none'."""
        cliente = self._cliente_conectado(account_id)
        from nio.api import PushRuleKind
        from nio.events.account_data import PushDontNotify
        from nio.responses import SetPushRuleResponse, DeletePushRuleError
        if silenciar:
            resp = self._run(cliente.set_pushrule(
                "global", PushRuleKind.room, room_id, actions=[PushDontNotify()]), timeout=30.0)
            if not isinstance(resp, SetPushRuleResponse):
                raise MatrixError(getattr(resp, "message", None) or str(resp))
        else:
            resp = self._run(cliente.delete_pushrule("global", PushRuleKind.room, room_id),
                             timeout=30.0)
            if isinstance(resp, DeletePushRuleError) and "not_found" not in str(resp).lower():
                raise MatrixError(getattr(resp, "message", None) or str(resp))

    def palabras_aviso(self, account_id: int) -> List[str]:
        """Palabras clave que te AVISAN cuando alguien las escribe (push rules de tipo 'content').
        Se leen del estado de push rules del cliente y de nuestra copia local."""
        cliente = self._cliente_conectado(account_id)
        salida = []
        try:
            reglas = getattr(cliente, "push_rules", None)
            glob = getattr(reglas, "global_rules", None) or getattr(reglas, "global_", None)
            contenido = getattr(glob, "content", None) or []
            for r in contenido:
                patron = getattr(r, "pattern", None) or (r.get("pattern") if isinstance(r, dict) else None)
                rid = getattr(r, "id", None) or (r.get("rule_id") if isinstance(r, dict) else None)
                if patron and not (str(rid or "")).startswith("."):
                    salida.append(patron)
        except Exception as e:
            log.debug(f"Matrix: no se pudieron leer las palabras de aviso: {e}")
        for p in (config.get("matrix", f"keywords_{account_id}", []) or []):
            if p not in salida:
                salida.append(p)
        return salida

    def agregar_palabra_aviso(self, account_id: int, palabra: str):
        """Añade una palabra clave que te avisará (push rule 'content' con notificar + sonido).
        Se guarda en el servidor: te avisará en todos tus clientes."""
        palabra = (palabra or "").strip()
        if not palabra:
            return
        cliente = self._cliente_conectado(account_id)
        from nio.api import PushRuleKind
        from nio.events.account_data import PushNotify, PushSetTweak
        from nio.responses import SetPushRuleResponse
        resp = self._run(cliente.set_pushrule(
            "global", PushRuleKind.content, palabra,
            actions=[PushNotify(), PushSetTweak("sound", "default"),
                     PushSetTweak("highlight", True)],
            pattern=palabra), timeout=30.0)
        if not isinstance(resp, SetPushRuleResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        locales = list(config.get("matrix", f"keywords_{account_id}", []) or [])
        if palabra not in locales:
            locales.append(palabra)
            config.set("matrix", f"keywords_{account_id}", locales)

    def quitar_palabra_aviso(self, account_id: int, palabra: str):
        cliente = self._cliente_conectado(account_id)
        from nio.api import PushRuleKind
        from nio.responses import DeletePushRuleError
        resp = self._run(cliente.delete_pushrule("global", PushRuleKind.content, palabra),
                         timeout=30.0)
        if isinstance(resp, DeletePushRuleError) and "not_found" not in str(resp).lower():
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        locales = [p for p in (config.get("matrix", f"keywords_{account_id}", []) or [])
                   if p != palabra]
        config.set("matrix", f"keywords_{account_id}", locales)

    # ---------------- Contexto de un evento (resultados de búsqueda) ----------------
    def contexto_evento(self, account_id: int, room_id: str, event_id: str,
                        limit: int = 10) -> List[Dict[str, Any]]:
        """Mensajes ALREDEDOR de un evento (antes, el propio, después): "ver en su contexto" un
        resultado de búsqueda. Devuelve [{quien, texto, fecha, es_el_buscado}]."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomContextResponse
        from nio import RoomMessageText, RoomMessageNotice, RoomMessageEmote, RoomMessageMedia
        resp = self._run(cliente.room_context(room_id, event_id, limit=limit), timeout=45.0)
        if not isinstance(resp, RoomContextResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)

        def _fila(ev, buscado=False):
            if not isinstance(ev, (RoomMessageText, RoomMessageNotice, RoomMessageEmote,
                                   RoomMessageMedia)):
                return None
            quien = (sala.user_name(getattr(ev, "sender", "")) if sala else "") \
                or getattr(ev, "sender", "")
            return {"quien": quien, "texto": getattr(ev, "body", "") or "",
                    "fecha": ts_a_iso(getattr(ev, "server_timestamp", 0)),
                    "es_el_buscado": buscado}
        salida = []
        for ev in reversed(getattr(resp, "events_before", None) or []):
            f = _fila(ev)
            if f:
                salida.append(f)
        f = _fila(getattr(resp, "event", None), buscado=True)
        if f:
            salida.append(f)
        for ev in (getattr(resp, "events_after", None) or []):
            f = _fila(ev)
            if f:
                salida.append(f)
        return salida

    # ---------------- Hilos de una sala ----------------
    def hilos_de_sala(self, account_id: int, room_id: str) -> List[Dict[str, Any]]:
        """Lista las RAÍCES de los hilos de una sala. Devuelve [{event_id, quien, texto, fecha}]."""
        cliente = self._cliente_conectado(account_id)
        from nio import RoomMessageText, RoomMessageNotice, RoomMessageEmote

        async def _leer():
            eventos = []
            async for ev in cliente.room_get_threads(room_id, limit=100):
                eventos.append(ev)
            return eventos
        try:
            eventos = self._run(_leer(), timeout=60.0)
        except Exception as e:
            raise MatrixError(_("No se pudieron leer los hilos: {0}").format(e))
        sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)
        salida = []
        for ev in eventos:
            if not isinstance(ev, (RoomMessageText, RoomMessageNotice, RoomMessageEmote)):
                continue
            quien = (sala.user_name(getattr(ev, "sender", "")) if sala else "") \
                or getattr(ev, "sender", "")
            salida.append({"event_id": getattr(ev, "event_id", "") or "",
                           "quien": quien, "texto": getattr(ev, "body", "") or "",
                           "fecha": ts_a_iso(getattr(ev, "server_timestamp", 0))})
        return salida

    # ---------------- Directorio público de la sala ----------------
    def visibilidad_directorio(self, account_id: int, room_id: str) -> bool:
        """True si la sala aparece en el DIRECTORIO público del servidor."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomGetVisibilityResponse
        resp = self._run(cliente.room_get_visibility(room_id), timeout=20.0)
        if isinstance(resp, RoomGetVisibilityResponse):
            return (resp.visibility or "").lower() == "public"
        return False

    def publicar_en_directorio(self, account_id: int, room_id: str, publicar: bool):
        """Muestra u oculta la sala en el directorio público del servidor."""
        import urllib.parse
        self._api_http(account_id, "PUT",
                       f"/_matrix/client/v3/directory/list/room/{q_seg(room_id)}",
                       {"visibility": "public" if publicar else "private"})

    def borrar_alias_sala(self, account_id: int, alias: str):
        """Borra un alias público (#nombre:servidor) de una sala."""
        alias = (alias or "").strip()
        if not alias.startswith("#"):
            raise MatrixError(_("El alias debe empezar por #."))
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomDeleteAliasResponse
        resp = self._run(cliente.room_delete_alias(alias), timeout=30.0)
        if not isinstance(resp, RoomDeleteAliasResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    # ---------------- Confianza de dispositivos (E2EE) ----------------
    def dispositivos_de_sala(self, account_id: int, room_id: str) -> List[Dict[str, Any]]:
        """Dispositivos de los miembros de una sala CIFRADA, con su estado de confianza:
        [{user_id, device_id, nombre, estado}]."""
        cliente = self._cliente_conectado(account_id)
        try:
            mapa = cliente.room_devices(room_id)
        except Exception as e:
            raise MatrixError(_("Esta sala no está cifrada o no hay dispositivos: {0}").format(e))
        salida = []
        for user_id, devs in (mapa or {}).items():
            for device_id, dev in (devs or {}).items():
                if getattr(dev, "verified", False):
                    estado = _("verificado")
                elif getattr(dev, "blacklisted", False):
                    estado = _("bloqueado")
                else:
                    estado = _("sin verificar")
                salida.append({"user_id": user_id, "device_id": device_id,
                               "nombre": getattr(dev, "display_name", "") or device_id,
                               "estado": estado,
                               # Huella ed25519 del dispositivo: se muestra al usuario si va a
                               # concederle confianza a mano (para que compare la clave real).
                               "ed25519": getattr(dev, "ed25519", "") or ""})
        salida.sort(key=lambda d: (d["user_id"], d["device_id"]))
        return salida

    def _buscar_olmdevice(self, cliente, user_id: str, device_id: str):
        try:
            for dev in cliente.device_store.active_user_devices(user_id):
                if getattr(dev, "id", getattr(dev, "device_id", "")) == device_id:
                    return dev
        except Exception:
            pass
        try:
            return cliente.device_store[user_id][device_id]
        except Exception:
            return None

    def confiar_dispositivo(self, account_id: int, user_id: str, device_id: str, confiar: bool = True):
        """Marca un dispositivo de otro usuario como VERIFICADO (confiado) o lo BLOQUEA, de forma
        manual (sin comparar emojis)."""
        cliente = self._cliente_conectado(account_id)
        dev = self._buscar_olmdevice(cliente, user_id, device_id)
        if dev is None:
            raise MatrixError(_("No encuentro ese dispositivo (¿la sala está cifrada y sincronizada?)."))
        if confiar:
            cliente.verify_device(dev)
        else:
            cliente.blacklist_device(dev)

    def verificar_mi_sesion(self, account_id: int, device_id: str):
        """Inicia la verificación por EMOJIS (SAS) entre ESTE dispositivo y OTRO dispositivo TUYO
        (mismo user_id): tú comparas los emojis en ambos. Es lo que pide "verificar mi sesión desde
        la app" sin depender de que el otro cliente (Element...) la inicie."""
        return self.iniciar_verificacion(account_id, self._user_id(account_id), device_id)

    def iniciar_verificacion(self, account_id: int, user_id: str, device_id: str):
        """Inicia una verificación por EMOJIS (SAS) con un dispositivo: el otro cliente mostrará
        los mismos emojis para comparar. La respuesta llega por el flujo de verificación existente."""
        cliente = self._cliente_conectado(account_id)
        dev = self._buscar_olmdevice(cliente, user_id, device_id)
        if dev is None:
            raise MatrixError(_("No encuentro ese dispositivo para verificarlo."))
        from nio.responses import ToDeviceResponse
        resp = self._run(cliente.start_key_verification(dev), timeout=30.0)
        if not isinstance(resp, ToDeviceResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
