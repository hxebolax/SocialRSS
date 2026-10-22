# -*- coding: utf-8 -*-
"""Cuentas XMPP: alta (verificando credenciales), conexión/desconexión, presencia propia y baja.

La contraseña se guarda cifrada en security ("xmpp:<acc>:password"); XMPP la usa en CADA conexión
(no hay token de sesión como en Matrix). El stream se mantiene abierto (push nativo); slixmpp
reconecta solo al caerse y XEP-0198 reanuda la sesión sin perder stanzas.
"""
from app.services.xmpp.xmpp_common import (
    asyncio, Any, Dict, Optional, db, security, log, XMPPError, jid_bare)


class _CuentasMixin:
    def crear_cuenta(self, jid: str, password: str, recurso: str = "SocialRSS",
                     no_verificar_cert: bool = False) -> int:
        """Verifica las credenciales conectando de verdad y, si van bien, da de alta la cuenta.
        `no_verificar_cert` acepta certificados TLS caducados/autofirmados (la conexión sigue
        cifrada). Bloqueante: llamar desde task_manager. Devuelve el account_id creado."""
        jid = jid_bare(jid).strip()
        if not jid or "@" not in jid or not password:
            raise XMPPError("Escribe el JID completo (usuario@servidor) y la contraseña.")

        async def _verificar():
            cliente = self._crear_cliente(jid, password, recurso,
                                          no_verificar_cert=no_verificar_cert)
            listo = asyncio.Event()
            estado = {"ok": False, "err": ""}

            def _start(_e):
                estado["ok"] = True
                listo.set()

            def _fail(_e):
                estado["err"] = "auth"
                listo.set()

            def _connfail(_e):
                estado["err"] = "conn"
                listo.set()

            def _certfail(_e):
                estado["err"] = "cert"
                listo.set()

            cliente.add_event_handler("session_start", _start)
            cliente.add_event_handler("failed_auth", _fail)
            cliente.add_event_handler("connection_failed", _connfail)
            cliente.add_event_handler("ssl_invalid_cert", _certfail)
            try:
                cliente.connect()
                # 90 s: hay servidores (reportado con conversations.im) donde la
                # negociación completa (DNS/SRV + TLS + SASL con reintento de mecanismo +
                # bind) tarda más de 45 s; con el margen corto se declaraba "no se pudo
                # conectar" y DOS SEGUNDOS después el bind llegaba bien (visto en el log
                # del reporte). Si aún así vence, el mensaje ya no culpa al JID.
                try:
                    await asyncio.wait_for(listo.wait(), timeout=90)
                except asyncio.TimeoutError:
                    estado["err"] = estado.get("err") or "timeout"
            finally:
                try:
                    cliente.disconnect()
                except Exception:
                    pass
            return estado

        estado = self._run(_verificar(), timeout=110.0)
        if not estado.get("ok"):
            if estado.get("err") == "auth":
                raise XMPPError("Usuario o contraseña incorrectos.")
            if estado.get("err") == "cert":
                raise XMPPError("El certificado del servidor no es válido (¿caducado o autofirmado?). "
                                "Si es tu servidor y confías en él, marca 'No verificar el "
                                "certificado' al añadir la cuenta.")
            if estado.get("err") == "timeout":
                raise XMPPError("El servidor no respondió a tiempo (90 segundos). No es un problema "
                                "de usuario o contraseña: el servidor va lento o la red está "
                                "congestionada. Vuelve a intentarlo en un momento.")
            raise XMPPError("No se pudo conectar con el servidor. Revisa el JID y tu conexión.")
        account_id = self._persistir_cuenta(jid, recurso)
        # Recordar la opción de certificado para las futuras conexiones de esta cuenta.
        if no_verificar_cert:
            from app.core.config_manager import config
            config.set("xmpp", f"no_verificar_cert_{account_id}", True)
        return account_id

    def _persistir_cuenta(self, jid: str, recurso: str = "SocialRSS") -> int:
        """Da de alta en la BD una cuenta XMPP ya verificada (fila de cuenta + fila xmpp_cuentas +
        contraseña cifrada). La contraseña se guarda aparte con guardar_password()."""
        fila = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 FROM accounts")
        posicion = fila[0] if fila else 0
        account_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) VALUES (?, 'xmpp', 1, ?)",
            (jid, posicion))
        db.execute("""INSERT INTO xmpp_cuentas (account_id, jid, recurso) VALUES (?, ?, ?)
                      ON CONFLICT(account_id) DO UPDATE SET jid = excluded.jid,
                                                            recurso = excluded.recurso""",
                   (account_id, jid, recurso or "SocialRSS"))
        log.info(f"XMPP: cuenta {jid} creada (cuenta {account_id})")
        return account_id

    def guardar_password(self, account_id: int, password: str):
        security.guardar_credencial("xmpp", f"{account_id}:password", password)

    def datos_cuenta(self, account_id: int) -> Optional[Dict[str, Any]]:
        fila = db.query_one("SELECT jid, recurso FROM xmpp_cuentas WHERE account_id = ?", (account_id,))
        if not fila:
            return None
        return {"jid": fila[0], "recurso": fila[1] or "SocialRSS"}

    def _user_jid(self, account_id: int) -> str:
        with self._estado_lock:
            j = self._jids.get(account_id)
        if j:
            return j
        d = self.datos_cuenta(account_id)
        j = d["jid"] if d else ""
        with self._estado_lock:
            self._jids[account_id] = j
        return j

    # ---------------- Conexión ----------------
    def conectar(self, account_id: int):
        """Abre el stream y deja la sesión en marcha. Bloqueante hasta que hay sesión (session_start)
        o falla. Llamar desde task_manager."""
        if self.esta_conectada(account_id):
            return
        d = self.datos_cuenta(account_id)
        password = security.obtener_credencial("xmpp", f"{account_id}:password")
        if not d or not password:
            raise XMPPError("Esta cuenta de XMPP no está bien configurada. Elimínala y vuelve a "
                            "añadirla en Gestionar Cuentas.")
        with self._estado_lock:
            self._fallo_auth[account_id] = False

        from app.core.config_manager import config
        _no_cert = bool(config.get("xmpp", f"no_verificar_cert_{account_id}", False))

        async def _conectar_async():
            cliente = self._crear_cliente(d["jid"], password, d["recurso"], account_id=account_id,
                                          no_verificar_cert=_no_cert)
            self._registrar_callbacks(account_id, cliente)
            with self._lock:
                self._clients[account_id] = cliente
            listo = asyncio.Event()
            # Causa TERMINAL explícita (XMPP-012): antes, un timeout o un connection_failed hacían que
            # conectar() devolviera "éxito" y la UI diera la cuenta por conectada aunque no lo estuviera.
            # Solo 'session_start' es éxito real; el resto son fallos con su propio mensaje.
            estado = {"ok": False, "fail": False}

            def _ok(_e):
                estado["ok"] = True
                listo.set()

            def _fail(_e):
                estado["fail"] = True
                listo.set()

            cliente.add_event_handler("session_start", _ok)
            cliente.add_event_handler("failed_auth", lambda _e: listo.set())  # _fallo_auth lo marca el callback
            cliente.add_event_handler("connection_failed", _fail)
            cliente.connect()
            try:
                await asyncio.wait_for(listo.wait(), timeout=45)
            except asyncio.TimeoutError:
                pass
            return estado

        estado = self._run(_conectar_async(), timeout=60.0)
        with self._estado_lock:
            fallo_auth = bool(self._fallo_auth.get(account_id))
        if fallo_auth:
            self.desconectar(account_id)  # limpiar cliente/tareas antes de propagar
            raise XMPPError("Usuario o contraseña incorrectos.")
        if not (isinstance(estado, dict) and estado.get("ok")):
            self.desconectar(account_id)
            if isinstance(estado, dict) and estado.get("fail"):
                raise XMPPError("No se pudo conectar con el servidor. Revisa la dirección y, si su "
                                "certificado TLS ha caducado o es autofirmado, activa en esta cuenta "
                                "la opción de no verificar el certificado.")
            raise XMPPError("El servidor no respondió a tiempo (tiempo de conexión agotado). "
                            "Revisa tu conexión y la dirección del servidor.")

    def desconectar(self, account_id: int):
        with self._lock:
            cliente = self._clients.pop(account_id, None)
        with self._estado_lock:
            self._conectadas[account_id] = False
        if cliente is None:
            return

        async def _desc():
            try:
                cliente.disconnect()
            except Exception:
                pass
            return True
        try:
            self._run(_desc(), timeout=20.0)
        except Exception:
            pass

    def esta_conectada(self, account_id: int) -> bool:
        with self._estado_lock:
            return bool(self._conectadas.get(account_id))

    def _cliente_conectado(self, account_id: int):
        with self._lock:
            cliente = self._clients.get(account_id)
        if cliente is None or not self.esta_conectada(account_id):
            raise XMPPError("La cuenta de XMPP no está conectada.")
        return cliente

    # ---------------- Presencia propia ----------------
    def fijar_presencia(self, account_id: int, show: str = "", mensaje: str = ""):
        """Fija tu presencia: '' disponible, 'chat', 'away', 'xa', 'dnd'. Se recuerda en config y se
        restaura al reconectar."""
        from app.core.config_manager import config
        cliente = self._cliente_conectado(account_id)
        self._llamar_en_loop(lambda: cliente.send_presence(
            pshow=(show or None), pstatus=(mensaje or None)))
        config.set("xmpp", f"presence_{account_id}", [show or "", mensaje or ""])

    def _restaurar_presencia(self, account_id: int, cliente):
        try:
            from app.core.config_manager import config
            pres = config.get("xmpp", f"presence_{account_id}", ["", ""]) or ["", ""]
            show, mensaje = (pres + ["", ""])[:2]
            if show or mensaje:
                cliente.send_presence(pshow=(show or None), pstatus=(mensaje or None))
        except Exception as e:
            log.debug(f"XMPP: no se pudo restaurar la presencia: {e}")

    # ---------------- Baja ----------------
    def eliminar_cuenta(self, account_id: int):
        """Desconecta y borra la cuenta y sus datos locales (la cuenta del servidor NO se toca)."""
        try:
            self.desconectar(account_id)
        except Exception:
            pass
        try:
            security.eliminar_credencial("xmpp", f"{account_id}:password")
        except Exception:
            pass
        # Almacén OMEMO de la cuenta (claves de cifrado): se borra con la cuenta.
        try:
            import os
            ruta = self._ruta_almacen_omemo(account_id)
            if os.path.exists(ruta):
                os.remove(ruta)
        except Exception:
            pass
        # Borrado EXPLÍCITO de los datos locales de esta cuenta (no confiar solo en ON DELETE CASCADE:
        # si el pragma foreign_keys no estuviera activo, o si un id de cuenta se reutilizara luego,
        # quedarían chats/mensajes "fantasma" de la sesión anterior). Se hace antes de borrar la fila
        # de accounts, que la elimina la capa de cuentas genérica.
        try:
            ids = [r[0] for r in db.query(
                "SELECT id FROM xmpp_mensajes WHERE account_id = ?", (account_id,))]
            for i in range(0, len(ids), 500):
                lote = ids[i:i + 500]
                ph = ",".join("?" for _ in lote)
                with db.transaccion():
                    db.execute(f"DELETE FROM xmpp_mensajes_fts WHERE msg_db_id IN ({ph})", tuple(lote))
            with db.transaccion():
                db.execute("DELETE FROM xmpp_mensajes WHERE account_id = ?", (account_id,))
                db.execute("DELETE FROM xmpp_chats WHERE account_id = ?", (account_id,))
                db.execute("DELETE FROM xmpp_cuentas WHERE account_id = ?", (account_id,))
        except Exception as e:
            log.debug(f"XMPP: limpieza explícita al eliminar cuenta {account_id}: {e}")
        with self._estado_lock:
            self._jids.pop(account_id, None)
            self._conectadas.pop(account_id, None)
