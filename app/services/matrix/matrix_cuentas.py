# -*- coding: utf-8 -*-
"""Cuentas de Matrix: login con descubrimiento, restauración de sesión, conexión (sync),
logout y helpers de BD (salas, mensajes, invitaciones, next_batch).

Mixin de MatrixService. El access_token, refresh_token y device_id van CIFRADOS en
security; en la BD solo quedan homeserver, user_id y next_batch (no secretos).
"""
from app.services.matrix.matrix_common import (
    asyncio, Any, Dict, Optional, List, db, security, log,
    HAS_NIO, MatrixError, ahora_iso, db_id_sala, normalizar_homeserver)

# TODOS los secretos que Matrix guarda por cuenta en security. Deben borrarse ENTEROS al eliminar
# la cuenta: si quedaba refresh_token/oauth_* huérfano, seguía siendo posible renovar el access_token
# (credencial renovable viva) aunque el usuario creyera haber borrado la cuenta.
_SECRETOS = ("access_token", "device_id", "pickle", "refresh_token",
             "oauth_token_endpoint", "oauth_client_id")


class _TokenCaducado(Exception):
    """Señal interna: el servidor rechazó el token (whoami falló). Se usa para intentar renovarlo
    con el refresh_token (cuentas OAuth/MAS) antes de rendirse."""


class _CuentasMixin:
    # ---------------- Alta (login) ----------------
    def crear_cuenta(self, homeserver: str, usuario: str, password: str) -> int:
        """Inicia sesión y da de alta la cuenta. Bloqueante: llamar desde task_manager.
        Devuelve el account_id creado."""
        url = normalizar_homeserver(homeserver)
        if not url or not usuario or not password:
            raise MatrixError("Faltan el servidor, el usuario o la contraseña.")

        async def _login():
            from nio import LoginResponse, DiscoveryInfoResponse
            cliente = self._crear_cliente(url, usuario)
            try:
                # Descubrimiento .well-known (spec: client-server-api/#well-known-uri). Si el
                # dominio delega en otro homeserver (p. ej. dominio propio), usar el real.
                try:
                    disco = await cliente.discovery_info()
                    if isinstance(disco, DiscoveryInfoResponse) and disco.homeserver_url:
                        real = normalizar_homeserver(disco.homeserver_url)
                        if real and real != url:
                            await cliente.close()
                            cliente = self._crear_cliente(real, usuario)
                except Exception:
                    pass  # sin .well-known: se usa la URL tal cual
                resp = await cliente.login(password, device_name="SocialRSS Suite")
                if not isinstance(resp, LoginResponse):
                    raise MatrixError(getattr(resp, "message", None) or str(resp))
                return {"homeserver": cliente.homeserver, "user_id": resp.user_id,
                        "device_id": resp.device_id, "access_token": resp.access_token}
            finally:
                await cliente.close()

        datos = self._run(_login(), timeout=60.0)
        return self._persistir_cuenta(datos["homeserver"], datos["user_id"],
                                      datos["device_id"], datos["access_token"])

    def _persistir_cuenta(self, homeserver: str, user_id: str, device_id: str,
                          access_token: str, secretos_extra: Optional[Dict[str, str]] = None) -> int:
        """Da de alta en la BD una cuenta de Matrix ya autenticada (token cifrado, fila de cuenta,
        fila matrix_cuentas). Lo usan crear_cuenta (login normal), el registro y OAuth/MAS.

        El alta es TRANSACCIONAL: si no se pueden guardar los secretos de forma segura, se deshacen
        las filas para no dejar una cuenta huérfana sin token (que luego diría "mal configurada").
        `secretos_extra` son otros secretos de la cuenta (refresh_token, oauth_*) que se guardan en el
        MISMO bloque atómico."""
        # Duplicados: no crear una segunda cuenta para el mismo user_id + homeserver.
        dup = db.query_one(
            "SELECT account_id FROM matrix_cuentas WHERE user_id = ? AND homeserver = ?",
            (user_id, homeserver))
        if dup:
            raise MatrixError(_("Esta cuenta de Matrix ({0}) ya está añadida.").format(user_id))
        # Cuentas nuevas SIEMPRE a position MAX+1 (regla de la casa).
        fila = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 FROM accounts")
        posicion = fila[0] if fila else 0
        account_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) VALUES (?, 'matrix', 1, ?)",
            (user_id, posicion))
        try:
            db.execute("""
                INSERT INTO matrix_cuentas (account_id, homeserver, user_id, next_batch)
                VALUES (?, ?, ?, '')
                ON CONFLICT(account_id) DO UPDATE SET homeserver = excluded.homeserver,
                                                      user_id = excluded.user_id
            """, (account_id, homeserver, user_id))
        except Exception:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))  # compensación
            raise
        # Secretos en BLOQUE (un solo ciclo atómico): access_token + device_id + los extra.
        secretos = {("matrix", f"{account_id}:access_token"): access_token,
                    ("matrix", f"{account_id}:device_id"): device_id}
        for clave, valor in (secretos_extra or {}).items():
            if valor:
                secretos[("matrix", f"{account_id}:{clave}")] = valor
        if not security.guardar_credenciales(secretos):
            # Deshacer TODO: sin token, la cuenta no serviría y confundiría al usuario.
            db.execute("DELETE FROM matrix_cuentas WHERE account_id = ?", (account_id,))
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            raise MatrixError(_("No se pudieron guardar las credenciales de forma segura; se canceló "
                                "el alta. Revisa el espacio libre en disco y reinténtalo."))
        log.info(f"Matrix: cuenta {user_id} creada (cuenta {account_id})")
        return account_id

    def datos_cuenta(self, account_id: int) -> Optional[Dict[str, Any]]:
        fila = db.query_one(
            "SELECT homeserver, user_id, next_batch FROM matrix_cuentas WHERE account_id = ?",
            (account_id,))
        if not fila:
            return None
        return {"homeserver": fila[0], "user_id": fila[1], "next_batch": fila[2] or ""}

    def _user_id(self, account_id: int) -> str:
        with self._estado_lock:
            uid = self._user_ids.get(account_id)
        if uid:
            return uid
        d = self.datos_cuenta(account_id)
        uid = d["user_id"] if d else ""
        with self._estado_lock:
            self._user_ids[account_id] = uid
        return uid

    # ---------------- Conexión (sync) ----------------
    def conectar(self, account_id: int):
        """Restaura la sesión y arranca el bucle de sincronización. Bloqueante hasta dejar la
        tarea lanzada (el sync sigue en el hilo MatrixLoop). Llamar desde task_manager."""
        if self.esta_conectada(account_id):
            return
        d = self.datos_cuenta(account_id)
        token = security.obtener_credencial("matrix", f"{account_id}:access_token")
        device = security.obtener_credencial("matrix", f"{account_id}:device_id")
        if not d or not token or not device:
            raise MatrixError("Esta cuenta de Matrix no está bien configurada. "
                              "Elimínala y vuelve a añadirla en Gestionar Cuentas.")

        async def _arrancar(tok):
            # Con device_id + account_id el cliente se crea CON cifrado y store de claves
            # persistente (restore_login carga el store olm automáticamente).
            cliente = self._crear_cliente(d["homeserver"], d["user_id"],
                                          device_id=device, account_id=account_id)
            cliente.restore_login(d["user_id"], device, tok)
            # whoami valida el token YA (mejor un error claro ahora que un sync mudo).
            from nio.responses import WhoamiResponse
            resp = await cliente.whoami()
            if not isinstance(resp, WhoamiResponse):
                await cliente.close()
                raise _TokenCaducado()
            self._registrar_callbacks(account_id, cliente)
            with self._lock:
                self._clients[account_id] = cliente
            self._sync_inicial[account_id] = True
            tarea = asyncio.get_event_loop().create_task(
                self._bucle_sync(account_id, cliente, d["next_batch"]))
            with self._lock:
                self._sync_tasks[account_id] = tarea
            return True

        try:
            self._run(_arrancar(token), timeout=45.0)
        except _TokenCaducado:
            # Token caducado: si es una cuenta OAuth/MAS, renovarlo con el refresh_token y reintentar
            # UNA vez (la renovación usa urllib, aquí estamos en un hilo de task_manager, no en el bucle).
            nuevo = self.refrescar_token_oauth(account_id)
            if not nuevo:
                raise MatrixError("La sesión ha caducado o el servidor la rechaza. "
                                  "Elimina la cuenta y vuelve a añadirla.")
            self._run(_arrancar(nuevo), timeout=45.0)
        # Mantenimiento silencioso del historial (poda por días, si está configurada).
        try:
            self.podar_historial(account_id)
        except Exception:
            pass

    def desconectar(self, account_id: int):
        """Para el sync y cierra la sesión HTTP (el access token sigue siendo válido)."""
        with self._lock:
            tarea = self._sync_tasks.pop(account_id, None)
            cliente = self._clients.pop(account_id, None)
        if tarea is not None:
            try:
                self._loop.call_soon_threadsafe(tarea.cancel)
            except Exception:
                pass
        if cliente is not None:
            async def _cerrar():
                try:
                    await cliente.close()
                except Exception:
                    pass
                return True
            try:
                self._run(_cerrar(), timeout=15.0)
            except Exception:
                pass
        self._marcar_conectada(account_id, False)

    def esta_conectada(self, account_id: int) -> bool:
        with self._lock:
            tarea = self._sync_tasks.get(account_id)
        if tarea is None or tarea.done():
            return False
        with self._estado_lock:
            return bool(self._conectadas.get(account_id))

    def _marcar_conectada(self, account_id: int, valor: bool):
        with self._estado_lock:
            self._conectadas[account_id] = bool(valor)

    def _logout_remoto(self, account_id: int) -> bool:
        """Cierra la sesión EN EL SERVIDOR (revoca el access_token con /logout). Best-effort: si no
        hay red o el token ya no vale, devuelve False. Bloqueante: llamar desde task_manager.
        La cuenta debe estar ya DESCONECTADA (cliente cerrado) antes de llamar a esto."""
        d = self.datos_cuenta(account_id)
        token = security.obtener_credencial("matrix", f"{account_id}:access_token")
        device = security.obtener_credencial("matrix", f"{account_id}:device_id") or ""
        if not d or not token:
            return False

        async def _logout():
            # Cliente EFÍMERO sin cifrado (solo necesitamos el token para /logout; el store olm se
            # borra justo después). restore_login fija user_id + device_id + access_token.
            cliente = self._crear_cliente(d["homeserver"], d["user_id"])
            try:
                cliente.restore_login(d["user_id"], device, token)
                from nio.responses import LogoutResponse
                resp = await cliente.logout()
                return isinstance(resp, LogoutResponse)
            finally:
                try:
                    await cliente.close()
                except Exception:
                    pass

        try:
            return bool(self._run(_logout(), timeout=30.0))
        except Exception as e:
            log.warning(f"Matrix: no se pudo cerrar la sesión en el servidor (cuenta {account_id}): "
                        f"{e}. El token podría seguir activo hasta que caduque.")
            return False

    def eliminar_cuenta(self, account_id: int):
        try:
            self.desconectar(account_id)
        except Exception:
            pass
        # Revocar la sesión en el SERVIDOR antes de borrar el token localmente (si no, el token
        # seguiría siendo válido en el homeserver sin forma de revocarlo). Best-effort: sin red se
        # avisa en el log y se continúa con el borrado local que el usuario pidió.
        try:
            if self._logout_remoto(account_id):
                log.info(f"Matrix: sesión revocada en el servidor (cuenta {account_id}).")
            else:
                log.warning(f"Matrix: no se revocó la sesión en el servidor (cuenta {account_id}); "
                            "el token podría seguir activo hasta caducar.")
        except Exception:
            pass
        for clave in _SECRETOS:
            try:
                security.borrar_credencial("matrix", f"{account_id}:{clave}")
            except Exception:
                pass
        # Claves privadas de firma cruzada guardadas en este equipo (M9).
        try:
            self.eliminar_cross_signing_local(account_id)
        except Exception:
            pass
        db.execute("DELETE FROM matrix_mensajes WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM matrix_salas WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM matrix_cuentas WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        # Borrar también el store de claves E2EE de la cuenta (no dejar claves huérfanas).
        try:
            import shutil
            from app.core.paths import get_data_path
            ruta = get_data_path(f"sessions/matrix/{account_id}")
            if ruta.exists():
                shutil.rmtree(ruta, ignore_errors=True)
        except Exception as e:
            log.debug(f"Matrix: no se pudo borrar el store E2EE: {e}")
        log.info(f"Matrix: cuenta {account_id} eliminada")

    def listar_cuentas(self) -> List[Dict[str, Any]]:
        filas = db.query("""
            SELECT a.id, a.name FROM accounts a
            WHERE a.service_id = 'matrix' AND a.active = 1 ORDER BY a.position, a.id""")
        return [{"account_id": f[0], "name": f[1], "conectada": self.esta_conectada(f[0])}
                for f in filas]

    # ---------------- next_batch ----------------
    def _guardar_next_batch(self, account_id: int, token: str):
        try:
            db.execute("UPDATE matrix_cuentas SET next_batch = ? WHERE account_id = ?",
                       (token or "", account_id))
        except Exception as e:
            log.error(f"Matrix: error guardando next_batch: {e}")

    def _leer_next_batch(self, account_id: int) -> str:
        d = self.datos_cuenta(account_id)
        return d["next_batch"] if d else ""

    # ---------------- Salas y mensajes (BD) ----------------
    def _es_dm(self, account_id: int, room_id: str) -> bool:
        with self._estado_lock:
            return room_id in (self._dms.get(account_id) or set())

    def nivel_aviso(self, account_id: int, room_id: str) -> str:
        fila = db.query_one("SELECT COALESCE(notify_level,'all') FROM matrix_salas WHERE id = ?",
                            (db_id_sala(account_id, room_id),))
        return fila[0] if fila and fila[0] in ("all", "mentions", "none") else "all"

    def fijar_nivel_aviso(self, account_id: int, room_id: str, nivel: str):
        if nivel not in ("all", "mentions", "none"):
            return
        db.execute("UPDATE matrix_salas SET notify_level = ? WHERE id = ?",
                   (nivel, db_id_sala(account_id, room_id)))
        # M8: "silencio" se propaga al SERVIDOR (push rule) para que Element y los demás clientes
        # también silencien la sala; cualquier otro nivel quita ese silencio del servidor. Es
        # best-effort: si la cuenta no está conectada o el servidor no lo acepta, no pasa nada.
        try:
            if self.esta_conectada(account_id):
                self.silenciar_sala_servidor(account_id, room_id, silenciar=(nivel == "none"))
        except Exception as e:
            log.debug(f"Matrix: no se pudo sincronizar el silencio con el servidor: {e}")

    @staticmethod
    def _mejor_nombre_sala(conn, dbid: str, room_id: str, sala) -> str:
        """Mejor nombre para la sala SIN pisar uno bueno de la BD. nio calcula "Empty Room" cuando
        la sala no tiene m.room.name y aún no tiene miembros cargados (típico tras un sync incremental
        al reconectar: el estado antiguo NO se reenvía). En ese caso, se CONSERVA el nombre que ya
        había en la BD en vez de machacarlo con "Empty Room" o con el propio room_id."""
        nombre_real = ""
        try:
            if sala.is_named():
                nombre_real = getattr(sala, "name", "") or getattr(sala, "canonical_alias", "") or ""
            else:
                # Sala sin m.room.name: el display_name se basa en los miembros. Solo vale si NO es
                # la etiqueta vacía de nio ("Empty Room"/"Empty Room (had ...)").
                dn = getattr(sala, "display_name", "") or ""
                if dn and not dn.startswith("Empty Room"):
                    nombre_real = dn
        except Exception:
            nombre_real = ""
        if nombre_real:
            return nombre_real
        try:
            fila = conn.execute("SELECT nombre FROM matrix_salas WHERE id = ?", (dbid,)).fetchone()
            existente = (fila[0] if fila else "") or ""
        except Exception:
            existente = ""
        return existente or room_id

    def _guardar_salas(self, account_id: int, cliente):
        """Vuelca el resumen de salas unidas del cliente a la BD (una transacción por sync)."""
        try:
            salas = list((getattr(cliente, "rooms", {}) or {}).items())
            if not salas:
                return
            with db.transaccion() as conn:
                for room_id, sala in salas:
                    dbid = db_id_sala(account_id, room_id)
                    nombre = self._mejor_nombre_sala(conn, dbid, room_id, sala)
                    tema = getattr(sala, "topic", "") or ""
                    conn.execute("""
                        INSERT INTO matrix_salas (id, account_id, room_id, nombre, tema, es_dm,
                                                  invitacion, cifrada, es_espacio)
                        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            nombre = excluded.nombre, tema = excluded.tema,
                            es_dm = excluded.es_dm, invitacion = 0,
                            cifrada = excluded.cifrada, es_espacio = excluded.es_espacio
                    """, (dbid, account_id, room_id,
                          nombre, tema, 1 if self._es_dm(account_id, room_id) else 0,
                          1 if getattr(sala, "encrypted", False) else 0,
                          1 if getattr(sala, "room_type", "") == "m.space" else 0))
        except Exception as e:
            log.error(f"Matrix: error guardando salas: {e}")

    def reparar_nombres_sala(self, account_id: int) -> int:
        """Corrige las salas cuyo nombre guardado ES su room_id: nio calcula el nombre a partir del
        estado de la sala (m.room.name / miembros), pero tras un sync incremental (o si el estado no
        llegó completo) se queda sin él y mostrábamos el room_id (p. ej. una sala llamada "bar" salía
        como '!abc:servidor'). Aquí se PIDE al servidor el estado m.room.name y, si no, el
        canonical_alias, y se actualiza el nombre. Bounded (80 por pasada). Se llama en segundo plano
        tras el sync inicial; llamar desde task_manager (usa self._run, que agenda en el bucle)."""
        try:
            cliente = self._cliente_conectado(account_id)
        except Exception:
            return 0
        from nio.responses import RoomGetStateEventResponse
        try:
            filas = db.query(
                "SELECT room_id FROM matrix_salas WHERE account_id = ? AND nombre = room_id "
                "AND COALESCE(invitacion,0) = 0 LIMIT 80", (account_id,))
        except Exception:
            return 0
        corregidos = 0
        for (rid,) in filas:
            nombre = ""
            for tipo, clave in (("m.room.name", "name"), ("m.room.canonical_alias", "alias")):
                try:
                    r = self._run(cliente.room_get_state_event(rid, tipo), timeout=15.0)
                    if isinstance(r, RoomGetStateEventResponse) and isinstance(
                            getattr(r, "content", None), dict):
                        val = (r.content.get(clave) or "").strip()
                        if val:
                            nombre = val
                            break
                except Exception:
                    pass
            if nombre and nombre != rid:
                try:
                    db.execute("UPDATE matrix_salas SET nombre = ? WHERE id = ?",
                               (nombre, db_id_sala(account_id, rid)))
                    corregidos += 1
                except Exception:
                    pass
        if corregidos:
            self._emit(account_id, "sincronizada")
            log.info(f"Matrix: {corregidos} nombre(s) de sala corregido(s) (mostraban el id) "
                     f"para la cuenta {account_id}.")
        return corregidos

    def _guardar_invitacion(self, account_id: int, room_id: str, info):
        """Registra una sala en estado 'invitación pendiente' (aceptar/rechazar en la UI)."""
        try:
            nombre = room_id
            try:
                for ev in getattr(info, "invite_state", []) or []:
                    if getattr(ev, "name", None):
                        nombre = ev.name
                        break
            except Exception:
                pass
            db.execute("""
                INSERT INTO matrix_salas (id, account_id, room_id, nombre, invitacion)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET invitacion = 1, nombre = excluded.nombre
            """, (db_id_sala(account_id, room_id), account_id, room_id, nombre))
            self._emit(account_id, "invitacion", room_id=room_id, nombre=nombre)
        except Exception as e:
            log.error(f"Matrix: error guardando invitación: {e}")

    def _guardar_mensaje(self, account_id: int, room_id: str, tipo: str, sender: str,
                         sender_nombre: str, texto: str, event_id: str, fecha: str,
                         propio: bool, leido: bool, contar: bool,
                         media_mxc: str = "", reply_to: str = "",
                         actualizar_resumen: bool = True, media_crypto: str = "",
                         thread_root: str = "", poll_json: str = "", send_state: str = "") -> bool:
        """Guarda un mensaje con DEDUPE por event_id (UNIQUE). Devuelve True si era nuevo.
        Actualiza el resumen del chat (último mensaje/no leídos) si procede; el historial
        paginado pasa actualizar_resumen=False para no pisar lo actual. media_crypto lleva (JSON)
        las claves de descifrado si el adjunto va cifrado (salas E2EE); thread_root el event_id
        raíz si el mensaje pertenece a un hilo; poll_json la encuesta (pregunta/opciones/votos)."""
        try:
            # INSERT OR IGNORE: el DEDUPE por (account_id, event_id) UNIQUE es normal y esperado —
            # el eco del sync de un mensaje que YA guardamos al enviarlo cae aquí. Con OR IGNORE el
            # duplicado es un no-op de 0 filas (rowcount 0) SIN lanzar excepción, así que no ensucia
            # el log con un ERROR de escritura por cada mensaje propio (antes salía uno por cada uno).
            try:
                filas = db.execute("""
                    INSERT OR IGNORE INTO matrix_mensajes (account_id, room_id, event_id, sender,
                                                 sender_nombre, texto, tipo, date, es_propio,
                                                 leido, media_mxc, reply_to, media_crypto,
                                                 thread_root, poll_json, send_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (account_id, room_id, event_id, sender, sender_nombre, texto, tipo,
                      fecha, 1 if propio else 0, 1 if leido else 0, media_mxc or "",
                      reply_to or "", media_crypto or "", thread_root or "", poll_json or "",
                      send_state or ""))
            except Exception as e:
                # Fallo REAL de inserción (no dedupe): marcar el lote como incompleto para que
                # _tras_sync NO avance next_batch y el servidor reenvíe el evento (MX-001).
                self._fallo_persistencia[account_id] = True
                log.error(f"Matrix: no se pudo guardar el mensaje {event_id}: {e}")
                return False
            if not filas:
                return False  # ya existía (dedupe silencioso): no re-contar ni reindexar
            # Índice de búsqueda global (FTS): solo texto real. Si la tabla no existe, se ignora.
            if tipo in ("msg", "accion", "aviso") and (texto or "").strip():
                try:
                    fila_id = db.query_one(
                        "SELECT id FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
                        (account_id, event_id))
                    if fila_id:
                        db.execute("INSERT INTO matrix_mensajes_fts (msg_db_id, sender, texto) "
                                   "VALUES (?, ?, ?)",
                                   (fila_id[0], sender_nombre or sender or "", texto or ""))
                except Exception:
                    pass
            if actualizar_resumen:
                resumen = f"{sender_nombre or sender}: {texto}"[:200]
                incremento = 1 if contar else 0
                db.execute("""
                    INSERT INTO matrix_salas (id, account_id, room_id, nombre, last_message,
                                              last_message_date, unread_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        last_message = excluded.last_message,
                        last_message_date = excluded.last_message_date,
                        unread_count = unread_count + ?
                """, (db_id_sala(account_id, room_id), account_id, room_id, room_id,
                      resumen, fecha, incremento, incremento))
            return True
        except Exception as e:
            self._fallo_persistencia[account_id] = True  # lote incompleto: no avanzar next_batch
            log.error(f"Matrix: error guardando mensaje: {e}")
            return False
