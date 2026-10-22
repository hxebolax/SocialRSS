# -*- coding: utf-8 -*-
"""Redes IRC: alta/edición (accounts service_id='irc' + irc_networks), secretos,
conectar/desconectar y estado.

Mixin de IRCService. Cada RED IRC (Libera.Chat, OFTC, una privada...) es una "cuenta" de
la app (fila en accounts) con su configuración en irc_networks. Los SECRETOS (contraseña
del servidor, SASL y NickServ) van cifrados en security, nunca en la BD ni en logs.
"""
from app.services.irc.irc_common import (
    Any, Dict, Optional, List, db, security, log, ahora_iso,
    HAS_PYDLE, IRCError, db_id_canal)


# Claves de secretos en security (cuenta "irc", clave "<account_id>:<tipo>").
_SECRETOS = ("server_password", "sasl_password", "nickserv_password")


class _RedesMixin:
    # ---------------- Configuración de redes ----------------
    def crear_red(self, datos: Dict[str, Any]) -> int:
        """Da de alta una red IRC. `datos` trae los campos del asistente (ver irc_dialogs).
        Devuelve el account_id creado."""
        nombre = (datos.get("name") or datos.get("host") or "IRC").strip()
        # Cuentas nuevas SIEMPRE a position MAX+1 (regla de la casa: no pisar el orden).
        fila = db.query_one("SELECT COALESCE(MAX(position), -1) + 1 FROM accounts")
        posicion = fila[0] if fila else 0
        account_id = db.execute_insert(
            "INSERT INTO accounts (name, service_id, active, position) VALUES (?, 'irc', 1, ?)",
            (nombre, posicion))
        self._guardar_config_red(account_id, datos)
        log.info(f"IRC: red '{nombre}' creada (cuenta {account_id})")
        return account_id

    def actualizar_red(self, account_id: int, datos: Dict[str, Any]):
        """Actualiza la configuración de una red existente (asistente en modo edición)."""
        nombre = (datos.get("name") or "").strip()
        if nombre:
            db.execute("UPDATE accounts SET name = ? WHERE id = ?", (nombre, account_id))
        self._guardar_config_red(account_id, datos)
        log.info(f"IRC: red de la cuenta {account_id} actualizada")

    def _guardar_config_red(self, account_id: int, datos: Dict[str, Any]):
        """Guarda irc_networks + secretos. Los secretos SOLO se tocan si vienen en `datos`
        (una cadena vacía explícita los borra; ausentes = se conservan)."""
        db.execute("""
            INSERT INTO irc_networks (account_id, host, port, tls, verify_cert, nick, alt_nicks,
                                      username, realname, use_sasl, sasl_username, autojoin,
                                      auto_connect, encoding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                host = excluded.host, port = excluded.port, tls = excluded.tls,
                verify_cert = excluded.verify_cert, nick = excluded.nick,
                alt_nicks = excluded.alt_nicks, username = excluded.username,
                realname = excluded.realname, use_sasl = excluded.use_sasl,
                sasl_username = excluded.sasl_username, autojoin = excluded.autojoin,
                auto_connect = excluded.auto_connect, encoding = excluded.encoding
        """, (
            account_id,
            (datos.get("host") or "").strip(),
            int(datos.get("port") or 6697),
            1 if datos.get("tls", True) else 0,
            1 if datos.get("verify_cert", True) else 0,
            (datos.get("nick") or "").strip(),
            (datos.get("alt_nicks") or "").strip(),
            (datos.get("username") or "").strip(),
            (datos.get("realname") or "").strip(),
            1 if datos.get("use_sasl") else 0,
            (datos.get("sasl_username") or "").strip(),
            (datos.get("autojoin") or "").strip(),
            1 if datos.get("auto_connect", True) else 0,
            (datos.get("encoding") or "utf-8").strip() or "utf-8",
        ))
        for clave in _SECRETOS:
            if clave in datos:
                valor = (datos.get(clave) or "").strip()
                if valor:
                    security.guardar_credencial("irc", f"{account_id}:{clave}", valor)
                else:
                    security.borrar_credencial("irc", f"{account_id}:{clave}")

    def obtener_red(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Configuración completa de una red (SIN secretos; para el asistente de edición se
        indica solo si existen, con *_definida)."""
        fila = db.query_one("""
            SELECT n.host, n.port, n.tls, n.verify_cert, n.nick, n.alt_nicks, n.username,
                   n.realname, n.use_sasl, n.sasl_username, n.autojoin, n.auto_connect,
                   n.encoding, a.name
            FROM irc_networks n JOIN accounts a ON a.id = n.account_id
            WHERE n.account_id = ?""", (account_id,))
        if not fila:
            return None
        d = {
            "host": fila[0], "port": fila[1], "tls": bool(fila[2]), "verify_cert": bool(fila[3]),
            "nick": fila[4], "alt_nicks": fila[5], "username": fila[6], "realname": fila[7],
            "use_sasl": bool(fila[8]), "sasl_username": fila[9], "autojoin": fila[10],
            "auto_connect": bool(fila[11]), "encoding": fila[12], "name": fila[13],
        }
        for clave in _SECRETOS:
            d[f"{clave}_definida"] = bool(security.obtener_credencial("irc", f"{account_id}:{clave}"))
        return d

    def listar_redes(self) -> List[Dict[str, Any]]:
        """Redes IRC dadas de alta (activas), en el orden del árbol."""
        filas = db.query("""
            SELECT a.id, a.name FROM accounts a
            WHERE a.service_id = 'irc' AND a.active = 1 ORDER BY a.position, a.id""")
        return [{"account_id": f[0], "name": f[1], "conectada": self.esta_conectada(f[0])}
                for f in filas]

    def eliminar_cuenta(self, account_id: int):
        """Elimina una red: desconecta, borra secretos y todas sus filas."""
        try:
            self.desconectar(account_id)
        except Exception:
            pass
        for clave in _SECRETOS:
            try:
                security.borrar_credencial("irc", f"{account_id}:{clave}")
            except Exception:
                pass
        db.execute("DELETE FROM irc_messages WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM irc_channels WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM irc_networks WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        log.info(f"IRC: red de la cuenta {account_id} eliminada")

    # ---------------- Watchdog de reconexión ----------------
    # pydle reconecta solo (reintentos sin límite con backoff), pero si su cadena de reconexión
    # muriera del todo (p. ej. una excepción no prevista en pleno connect), el cliente quedaría
    # huérfano. Este vigilante (patrón del streaming de Mastodon) lo revive: cada 90 s comprueba
    # las redes con conexión automática que deberían estar conectadas y no lo están.
    _WATCHDOG_INTERVALO_S = 90
    _WATCHDOG_MIN_ENTRE_INTENTOS_S = 180

    def _asegurar_watchdog(self):
        with self._lock:
            if getattr(self, "_watchdog", None) is not None and self._watchdog.is_alive():
                return
            import threading as _th
            self._watchdog_ultimos = getattr(self, "_watchdog_ultimos", {})
            self._watchdog = _th.Thread(target=self._bucle_watchdog, name="IRCWatchdog", daemon=True)
            self._watchdog.start()

    def _bucle_watchdog(self):
        import time as _t
        while True:
            _t.sleep(self._WATCHDOG_INTERVALO_S)
            try:
                filas = db.query("""
                    SELECT a.id FROM accounts a JOIN irc_networks n ON n.account_id = a.id
                    WHERE a.service_id = 'irc' AND a.active = 1 AND n.auto_connect = 1""")
            except Exception:
                continue
            from app.core.config_manager import config
            self._watchdog_caido_desde = getattr(self, "_watchdog_caido_desde", {})
            for (acc,) in filas:
                try:
                    # Si desconectaste la red a mano, NO la reconectamos por detrás (respeta tu decisión
                    # hasta que la conectes tú). auto_connect=1 es la preferencia base, este flag manda.
                    if not config.get("irc", f"autoconnect_{acc}", True):
                        self._watchdog_caido_desde.pop(acc, None)
                        continue
                    if self.esta_conectada(acc):
                        self._watchdog_caido_desde.pop(acc, None)
                        continue
                    ahora = _t.time()
                    caido_desde = self._watchdog_caido_desde.setdefault(acc, ahora)
                    with self._lock:
                        cliente = self._clients.get(acc)
                    if cliente is not None and getattr(cliente, "connected", False):
                        continue  # socket levantado (registrándose): no interferir
                    # Si el cliente existe pero está caído, pydle puede estar en pleno backoff
                    # (que escala hasta 600 s): NO pisárselo. Solo revivimos si lleva caído más
                    # que el backoff máximo (610 s). Un cliente inexistente se revive antes.
                    margen = 610 if cliente is not None else self._WATCHDOG_MIN_ENTRE_INTENTOS_S
                    if ahora - caido_desde < margen:
                        continue
                    ultimo = self._watchdog_ultimos.get(acc, 0)
                    if ahora - ultimo < self._WATCHDOG_MIN_ENTRE_INTENTOS_S:
                        continue
                    if cliente is not None:
                        with self._lock:
                            self._clients.pop(acc, None)
                    self._watchdog_ultimos[acc] = ahora
                    log.info(f"IRC watchdog: reviviendo la conexión de la cuenta {acc}")
                    self.conectar(acc)
                except Exception as e:
                    log.debug(f"IRC watchdog: no se pudo revivir la cuenta {acc}: {e}")

    # ---------------- Conexión ----------------
    def conectar(self, account_id: int):
        """Conecta la red (bloqueante hasta que el socket queda establecido; el registro IRC
        sigue en segundo plano y avisa por event_bus). Llamar desde task_manager."""
        if self.esta_conectada(account_id):
            return
        cfg = self.obtener_red(account_id)
        if not cfg or not cfg.get("host") or not cfg.get("nick"):
            raise IRCError("Esta red IRC no está bien configurada. Edítala en Gestionar Cuentas.")

        # Secretos (solo en memoria, jamás al log).
        server_pass = security.obtener_credencial("irc", f"{account_id}:server_password") or None
        sasl_pass = security.obtener_credencial("irc", f"{account_id}:sasl_password") or None
        ns_pass = security.obtener_credencial("irc", f"{account_id}:nickserv_password") or ""

        from app.services.irc.irc_nucleo import _clase_cliente
        Cliente = _clase_cliente()

        alt = [n.strip() for n in (cfg.get("alt_nicks") or "").split(",") if n.strip()]
        opciones = dict(cfg)
        opciones["nickserv_password"] = ns_pass

        kwargs = {
            "nickname": cfg["nick"],
            "fallback_nicknames": alt,
            "username": cfg.get("username") or None,
            "realname": cfg.get("realname") or None,
        }
        if cfg.get("use_sasl") and sasl_pass:
            kwargs["sasl_username"] = cfg.get("sasl_username") or cfg["nick"]
            kwargs["sasl_password"] = sasl_pass

        async def _crear_y_conectar():
            cliente = Cliente(account_id, self, opciones, **kwargs)
            with self._lock:
                self._clients[account_id] = cliente
            # pydle usa el event loop actual; connect() vuelve al terminar el handshake.
            await cliente.connect(
                hostname=cfg["host"],
                port=int(cfg.get("port") or (6697 if cfg.get("tls", True) else 6667)),
                tls=bool(cfg.get("tls", True)),
                tls_verify=bool(cfg.get("verify_cert", True)),
                password=server_pass,
            )
            return True

        try:
            self._run(_crear_y_conectar(), timeout=40.0)
        except Exception:
            with self._lock:
                self._clients.pop(account_id, None)
            raise
        # Con la primera conexión arranca el vigilante de reconexión (uno para todas las redes).
        self._asegurar_watchdog()

    def desconectar(self, account_id: int):
        """Desconecta la red (QUIT limpio). Llamar desde task_manager."""
        with self._lock:
            cliente = self._clients.pop(account_id, None)
        if cliente is None:
            return
        try:
            cliente.RECONNECT_ON_ERROR = False  # un QUIT pedido no debe reconectar
            self._run(cliente.quit("SocialRSS Suite"), timeout=15.0)
        except Exception as e:
            log.debug(f"IRC: error al desconectar (se ignora): {e}")
        self._marcar_conectada(account_id, False)

    def esta_conectada(self, account_id: int) -> bool:
        """True si la red está REGISTRADA (001 recibido). El socket puede estar levantado un
        instante antes; para la UI lo que cuenta es estar dentro de la red."""
        with self._lock:
            cliente = self._clients.get(account_id)
        if cliente is None or not getattr(cliente, "connected", False):
            return False
        with self._estado_lock:
            return bool(self._conectadas.get(account_id))

    def _marcar_conectada(self, account_id: int, valor: bool):
        """Estado en memoria (lo consulta la UI); si se cae la conexión, quitar el cliente
        muerto NO: pydle reconecta solo (RECONNECT_ON_ERROR)."""
        with self._estado_lock:
            self._conectadas[account_id] = bool(valor)

    # ---------------- Apoyo del núcleo (BD de canales y mensajes) ----------------
    def _canales_para_autojoin(self, account_id: int, cfg: Dict[str, Any]) -> List[str]:
        """Canales a unirse al conectar: los del campo autojoin + los que estaban unidos
        la última sesión (rejoin), sin duplicados."""
        canales = []
        for c in (cfg.get("autojoin") or "").split(","):
            c = c.strip()
            if c and c not in canales:
                canales.append(c)
        try:
            for (nombre,) in db.query(
                    "SELECT name FROM irc_channels WHERE account_id = ? AND joined = 1 "
                    "AND es_privado = 0", (account_id,)):
                if nombre and nombre != "*" and nombre not in canales:
                    canales.append(nombre)
        except Exception:
            pass
        return canales

    def _canal_unido(self, account_id: int, canal: str, unido: bool):
        """Marca un canal como unido/salido en la BD (creando la fila si no existe)."""
        try:
            db.execute("""
                INSERT INTO irc_channels (id, account_id, name, joined, es_privado)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(id) DO UPDATE SET joined = excluded.joined
            """, (db_id_canal(account_id, canal), account_id, canal, 1 if unido else 0))
        except Exception as e:
            log.error(f"IRC: error al marcar canal unido: {e}")

    def _actualizar_tema(self, account_id: int, canal: str, tema: str):
        try:
            db.execute("""
                INSERT INTO irc_channels (id, account_id, name, topic, joined, es_privado)
                VALUES (?, ?, ?, ?, 1, 0)
                ON CONFLICT(id) DO UPDATE SET topic = excluded.topic
            """, (db_id_canal(account_id, canal), account_id, canal, tema))
        except Exception as e:
            log.error(f"IRC: error al actualizar el tema: {e}")

    def _guardar_mensaje(self, account_id: int, canal: str, tipo: str, nick: str, texto: str,
                         propio: bool = False, es_privado: bool = False, highlight: bool = False,
                         fecha_forzada: str = None, actualizar_resumen: bool = True):
        """Guarda un mensaje/evento en irc_messages y actualiza el resumen del chat en
        irc_channels (último mensaje, fecha y no leídos). `fecha_forzada` la usa el historial
        remoto (chathistory: server-time real; con actualizar_resumen=False para que lo viejo
        no pise el resumen ni sume no leídos). Nunca lanza: un fallo de BD no debe tumbar el
        hilo del protocolo."""
        try:
            fecha = fecha_forzada or ahora_iso()
            msg_id = db.execute_insert("""
                INSERT INTO irc_messages (account_id, channel, nick, text, tipo, date,
                                          es_propio, highlight, leido)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (account_id, (canal or "").lower(), nick or "", texto or "", tipo, fecha,
                  1 if propio else 0, 1 if highlight else 0, 1 if propio else 0))
            # Índice de búsqueda (FTS): solo texto real (no joins/parts). Si no existe, se ignora.
            if tipo in ("msg", "action", "notice") and (texto or "").strip():
                try:
                    db.execute("INSERT INTO irc_messages_fts (msg_db_id, nick, text) VALUES (?, ?, ?)",
                               (msg_id, nick or "", texto or ""))
                except Exception:
                    pass
            # Resumen del chat (solo para mensajes visibles, no para join/part).
            if actualizar_resumen and tipo in ("msg", "notice", "action"):
                resumen = f"{nick}: {texto}"[:200] if nick else (texto or "")[:200]
                incremento = 0 if propio else 1
                db.execute("""
                    INSERT INTO irc_channels (id, account_id, name, joined, es_privado,
                                              last_message, last_message_date, unread_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        last_message = excluded.last_message,
                        last_message_date = excluded.last_message_date,
                        unread_count = unread_count + ?
                """, (db_id_canal(account_id, canal), account_id, canal,
                      0 if es_privado else 1, 1 if es_privado else 0,
                      resumen, fecha, incremento, incremento))
        except Exception as e:
            log.error(f"IRC: error al guardar mensaje: {e}")
