# -*- coding: utf-8 -*-
"""Bucle asyncio del servicio IRC y cliente pydle con nuestros eventos.

Mixin de IRCService (patrón telegram_nucleo): un hilo dedicado "IRCLoop" con su event
loop; la UI le habla con run_coroutine_threadsafe a través de _run(). pydle se importa
de forma DIFERIDA (regla de arranque rápido).
"""
from app.services.irc.irc_common import (
    asyncio, threading, Any, Dict, Optional, db, event_bus, log,
    HAS_PYDLE, IRCError, quitar_formato_mirc, ahora_iso, db_id_canal)


# Clase de cliente pydle (se construye UNA vez, al primer uso, para no importar pydle al arrancar).
_ClaseCliente = None


def _clase_cliente():
    """Devuelve (creándola la primera vez) la clase de cliente IRC basada en pydle.Client.

    pydle.Client ya compone RFC1459 + TLS + SASL + IRCv3 (3.1 completo, 3.2 base) + ISUPPORT +
    CTCP + WHOX + Monitoring: justo el perfil moderno que queremos (ver análisis IRC)."""
    global _ClaseCliente
    if _ClaseCliente is not None:
        return _ClaseCliente
    if not HAS_PYDLE:
        raise IRCError("Las dependencias de IRC (pydle) no están instaladas. "
                       "Ejecute la instalación de requisitos desde build.bat.")
    import pydle

    class _ClienteSocialRSS(pydle.Client):
        """Cliente pydle de UNA red: traduce los eventos del protocolo a (a) filas en la BD y
        (b) eventos en el event_bus para que la interfaz se entere en vivo, sin acoplarse."""

        # Respuesta CTCP VERSION configurable (I3: hacerla opcional/ocultable desde Preferencias).
        VERSION_TEXTO = "SocialRSS Suite"

        def __init__(self, account_id: int, servicio, opciones: Dict[str, Any], **kwargs):
            super().__init__(**kwargs)
            self._acc = account_id
            self._srv = servicio
            self._opciones = opciones          # dict de irc_networks (autojoin, nickserv...)
            self.RECONNECT_ON_ERROR = True     # reconexión propia de pydle (backoff creciente)
            # SIN límite de reintentos (pydle trae 3 por defecto: poco para un cliente de chat).
            # El backoff de pydle escala hasta 600 s, así que no machaca al servidor.
            self.RECONNECT_MAX_ATTEMPTS = None
            self._hubo_caida = False           # para avisar UNA vez y anunciar la reconexión
            # Anti-flood de SALIDA (token bucket): ráfaga de 4 y ~1 mensaje cada 0,7 s después.
            # Sin esto, pegar varias líneas o un bucle de la UI podría hacer que la red te
            # expulsara por flood ("Excess Flood").
            self._tokens = 4.0
            self._t_ultimo_envio = 0.0
            # CTCP PING pendientes por nick (fallback si el servidor no devuelve la marca de tiempo
            # tal cual): nick_en_minusculas -> time.time() del envío, para calcular la latencia.
            self._ctcp_ping_pendiente = {}
            # Buffers de captura de respuestas por sondeo (WHOWAS 314/369, baneos 367/368).
            self._whowas_lineas = None
            self._whowas_evento = None
            self._baneos_lista = None
            self._baneos_evento = None

        async def handle_forever(self):
            """CORRECCIÓN para Windows: cuando el servidor cae, el recv principal de pydle lanza
            ConnectionResetError (WinError 64) que pydle NO captura (solo la maneja en la rama del
            ping): el task muere en silencio, on_disconnect nunca corre y el cliente queda "zombi"
            con connected=True para siempre (sin reconexión y con la app creyéndose conectada).
            Capturamos el error y forzamos la desconexión NO esperada, que activa la reconexión
            automática con backoff, igual que ocurre en Linux con el EOF limpio."""
            try:
                await super().handle_forever()
            except (ConnectionError, OSError) as e:
                log.warning(f"IRC: conexión rota detectada (cuenta {self._acc}): {e}")
                try:
                    if self.connected:
                        await self.disconnect(expected=False)
                    else:
                        await self.on_disconnect(expected=False)
                except Exception as e2:
                    log.error(f"IRC: fallo al gestionar la conexión rota: {e2}")

        async def _throttle_salida(self):
            """Espera lo necesario para respetar el límite de envío (burst 4, 1/0,7s)."""
            import time as _t
            ahora = _t.monotonic()
            if self._t_ultimo_envio:
                self._tokens = min(4.0, self._tokens + (ahora - self._t_ultimo_envio) / 0.7)
            self._t_ultimo_envio = ahora
            if self._tokens < 1.0:
                espera = (1.0 - self._tokens) * 0.7
                await asyncio.sleep(espera)
                self._t_ultimo_envio = _t.monotonic()
            self._tokens = max(0.0, self._tokens - 1.0)

        async def message(self, target, message):
            await self._throttle_salida()
            await super().message(target, message)

        async def notice(self, target, message):
            await self._throttle_salida()
            await super().notice(target, message)

        async def ctcp(self, target, query, contents=None):
            await self._throttle_salida()
            # pydle.construct_ctcp hace " ".join(parts) y revienta si contents es None (CTCP sin
            # argumento, como VERSION/TIME/CLIENTINFO). Construimos el CTCP a mano en ese caso.
            if contents is None:
                from pydle.features.ctcp import construct_ctcp
                await self.message(target, construct_ctcp(query))
            else:
                await super().ctcp(target, query, contents)

        # ---- ajustes de comportamiento de pydle ----
        async def _create_user(self, nickname):
            """SIN el WHOIS automático de pydle (WHOXSupport lo lanza por CADA usuario nuevo si
            la red no anuncia WHOX): en un canal grande serían cientos de WHOIS seguidos (flood,
            riesgo de desconexión) y además el JOIN se queda BLOQUEADO esperando respuestas si el
            servidor no contesta. Los datos de un usuario se piden bajo demanda (whois de la UI)."""
            import pydle as _pydle
            _pydle.features.account.AccountSupport._create_user(self, nickname)

        # ---- utilidades internas ----
        def _emit(self, tipo: str, **datos):
            """Evento hacia la interfaz. SIEMPRE protegido: un fallo del bus no debe tumbar el
            hilo del protocolo."""
            try:
                datos.update({"account_id": self._acc, "tipo": tipo})
                event_bus.emit("irc_evento", datos)
            except Exception as e:
                log.debug(f"IRC: error emitiendo evento {tipo}: {e}")

        # ---- ciclo de conexión ----
        async def on_connect(self):
            await super().on_connect()
            log.info(f"IRC: conectado y registrado en la red (cuenta {self._acc}) como {self.nickname}")
            self._srv._marcar_conectada(self._acc, True)
            # Si venimos de una caída, anunciarlo (la UI lo verbaliza); primera conexión, no.
            self._emit("conectado", nick=self.nickname, reconexion=self._hubo_caida)
            self._hubo_caida = False
            # NickServ (compatibilidad para redes sin SASL). La contraseña viene de security.
            try:
                ns_pass = self._opciones.get("nickserv_password") or ""
                if ns_pass:
                    await self.message("NickServ", f"IDENTIFY {ns_pass}")
            except Exception as e:
                log.error(f"IRC: fallo al identificar con NickServ: {e}")
            # Autojoin de canales configurados + los que estaban unidos la última vez.
            try:
                for canal in self._srv._canales_para_autojoin(self._acc, self._opciones):
                    await self.join(canal)
            except Exception as e:
                log.error(f"IRC: fallo en el autojoin: {e}")
            # Amigos (MONITOR): registrar la lista para recibir avisos de conexión.
            try:
                for nick in self._srv.amigos(self._acc):
                    await self.monitor(nick)
            except Exception as e:
                log.debug(f"IRC: monitor no disponible: {e}")
            # Mantenimiento silencioso del historial (poda por días, si está configurada).
            try:
                self._srv.podar_historial(self._acc)
            except Exception:
                pass

        async def on_disconnect(self, expected):
            # Avisar ANTES del super(): pydle duerme el backoff dentro de on_disconnect y el
            # aviso debe salir al instante. Solo la PRIMERA caída de la racha (no cada reintento).
            log.info(f"IRC: desconectado de la red (cuenta {self._acc}, esperado={expected})")
            self._srv._marcar_conectada(self._acc, False)
            if not expected and not self._hubo_caida:
                self._hubo_caida = True
                self._emit("desconectado", esperado=False)
            elif expected:
                self._emit("desconectado", esperado=True)
            await super().on_disconnect(expected)

        # ---- apodo: errores y cambios ----
        async def on_raw_432(self, message):  # ERR_ERRONEUSNICKNAME
            self._emit("nick_error", motivo=_("El apodo no es válido para esta red."))

        async def on_raw_433(self, message):  # ERR_NICKNAMEINUSE
            # pydle prueba solo los apodos alternativos durante el registro.
            await super().on_raw_433(message)
            self._emit("nick_error", motivo=_("El apodo está ocupado; probando los alternativos."))

        async def on_raw_436(self, message):  # ERR_NICKCOLLISION
            self._emit("nick_error", motivo=_("Colisión de apodo en la red."))

        async def on_nick_change(self, old, new):
            await super().on_nick_change(old, new)
            # Solo el cambio de NUESTRO apodo se anuncia (el nuestro es 'new' tras el cambio).
            if (new or "").lower() == (self.nickname or "").lower():
                self._emit("nick_cambiado", antes=old, ahora=new)

        # ---- amigos (MONITOR): la red avisa cuando entran/salen ----
        async def on_user_online(self, nickname):
            await super().on_user_online(nickname)
            self._emit("amigo_online", nick=nickname)

        async def on_user_offline(self, nickname):
            await super().on_user_offline(nickname)
            self._emit("amigo_offline", nick=nickname)

        # ---- "está escribiendo" (client tag +typing sobre TAGMSG) ----
        async def on_raw_tagmsg(self, message):
            """TAGMSG con +typing: alguien está escribiendo (en un privado o canal). Solo se
            emite el evento (la conversación lo muestra); no se guarda nada."""
            try:
                tags = getattr(message, "tags", None) or {}
                estado = tags.get("+typing") or tags.get("typing")
                if not estado:
                    return
                nick, _meta = self._parse_user(message.source)
                if not nick or nick.lower() == (self.nickname or "").lower():
                    return
                destino = message.params[0] if message.params else ""
                canal = destino if self.is_channel(destino) else nick
                self._emit("typing", canal=canal, nick=nick, estado=estado)
            except Exception:
                pass

        # ---- STS (Strict Transport Security): la red exige conexión segura ----
        async def on_capability_sts_available(self, value):
            """El servidor anuncia STS. Si esta red está configurada SIN TLS, se sube la
            configuración a TLS (puerto anunciado) para las próximas conexiones y se avisa.
            No se solicita la capacidad (STS actúa por su valor, no por REQ)."""
            try:
                if not self._opciones.get("tls", True):
                    puerto = 6697
                    for parte in str(value or "").split(","):
                        if parte.strip().lower().startswith("port="):
                            try:
                                puerto = int(parte.split("=", 1)[1])
                            except ValueError:
                                pass
                    db.execute("UPDATE irc_networks SET tls = 1, verify_cert = 1, port = ? "
                               "WHERE account_id = ?", (puerto, self._acc))
                    self._opciones["tls"] = True
                    log.info(f"IRC: STS anunciado; la red {self._acc} pasa a TLS:{puerto}.")
                    self._emit("sts", puerto=puerto)
            except Exception as e:
                log.debug(f"IRC: error aplicando STS: {e}")
            return False

        # ---- historial remoto (draft/chathistory: servidores modernos y bouncers ZNC/Soju) ----
        async def on_capability_draft_chathistory_available(self, value):
            return True  # pedir la capacidad si el servidor la anuncia

        async def on_capability_batch_available(self, value):
            return True

        async def on_raw_privmsg(self, message):
            """Los PRIVMSG dentro de un BATCH (chathistory) son HISTÓRICOS: se guardan con su
            fecha real (tag server-time), con dedupe, sin sonidos ni contadores. El resto sigue
            el camino normal."""
            tags = getattr(message, "tags", None) or {}
            if "batch" in tags:
                try:
                    self._guardar_historico(message, tags)
                except Exception as e:
                    log.debug(f"IRC: error guardando histórico: {e}")
                return
            await super().on_raw_privmsg(message)

        def _guardar_historico(self, message, tags):
            nick, _meta = self._parse_user(message.source)
            if not nick:
                return
            destino = message.params[0]
            texto = quitar_formato_mirc(message.params[1] if len(message.params) > 1 else "")
            if texto.startswith("\x01ACTION") :
                tipo, texto = "action", texto[8:].rstrip("\x01").strip()
            else:
                tipo = "msg"
            fecha = tags.get("time") or ahora_iso()
            es_privado = not self.is_channel(destino)
            canal = (nick if (es_privado and nick.lower() != (self.nickname or "").lower()) else destino)
            if es_privado and nick.lower() == (self.nickname or "").lower():
                canal = destino  # histórico de un privado enviado por nosotros
            # Dedupe: el mismo mensaje (canal+nick+texto+fecha) no se guarda dos veces aunque se
            # repita la petición de historial.
            fila = db.query_one(
                "SELECT 1 FROM irc_messages WHERE account_id=? AND channel=? AND nick=? AND text=? AND date=?",
                (self._acc, (canal or "").lower(), nick, texto, fecha))
            if fila:
                return
            propio = nick.lower() == (self.nickname or "").lower()
            self._srv._guardar_mensaje(self._acc, canal, tipo, nick, texto, propio=propio,
                                       es_privado=es_privado, fecha_forzada=fecha,
                                       actualizar_resumen=False)
            self._emit("historico", canal=canal)

        # ---- canales ----
        async def on_join(self, channel, user):
            await super().on_join(channel, user)
            propio = (user or "").lower() == (self.nickname or "").lower()
            if propio:
                self._srv._canal_unido(self._acc, channel, True)
                # Historial remoto: si el servidor/bouncer lo soporta, rellenar lo perdido.
                try:
                    if (self._capabilities or {}).get("draft/chathistory"):
                        await self.rawmsg("CHATHISTORY", "LATEST", channel, "*", "50")
                except Exception:
                    pass
            self._srv._guardar_mensaje(self._acc, channel, "join", user, "", propio=propio)
            self._emit("join", canal=channel, nick=user, propio=propio)

        async def on_part(self, channel, user, message=None):
            await super().on_part(channel, user, message)
            propio = (user or "").lower() == (self.nickname or "").lower()
            if propio:
                self._srv._canal_unido(self._acc, channel, False)
            self._srv._guardar_mensaje(self._acc, channel, "part", user,
                                       quitar_formato_mirc(message or ""), propio=propio)
            self._emit("part", canal=channel, nick=user, propio=propio)

        async def on_kick(self, channel, target, by, reason=None):
            await super().on_kick(channel, target, by, reason)
            propio = (target or "").lower() == (self.nickname or "").lower()
            if propio:
                self._srv._canal_unido(self._acc, channel, False)
            self._srv._guardar_mensaje(self._acc, channel, "kick", by,
                                       f"{target}: {quitar_formato_mirc(reason or '')}", propio=False)
            self._emit("kick", canal=channel, nick=target, por=by, propio=propio)

        async def on_topic_change(self, channel, message, by):
            await super().on_topic_change(channel, message, by)
            tema = quitar_formato_mirc(message or "")
            self._srv._actualizar_tema(self._acc, channel, tema)
            self._srv._guardar_mensaje(self._acc, channel, "topic", by, tema, propio=False)
            self._emit("topic", canal=channel, nick=by, tema=tema)

        async def on_quit(self, user, message=None):
            """Un usuario se DESCONECTA de la red (QUIT). pydle llama a esto ANTES de sacarlo de
            los canales, así que se guarda el evento en CADA canal donde estaba (como los clientes
            de siempre). Si es un amigo, además suena por MONITOR (on_user_offline)."""
            propio = (user or "").lower() == (self.nickname or "").lower()
            razon = quitar_formato_mirc(message or "")
            canales = []
            try:
                for nombre, info in (self.channels or {}).items():
                    if user in (info.get("users") or set()):
                        canales.append(nombre)
            except Exception:
                pass
            await super().on_quit(user, message)
            for canal in canales:
                self._srv._guardar_mensaje(self._acc, canal, "quit", user, razon, propio=propio)
                self._emit("quit", canal=canal, nick=user, razon=razon)

        # ---- cambios de modo (canal y propios) ----
        async def on_mode_change(self, channel, modes, by):
            """Cambios de modo en un CANAL (+o operador, +b baneo, +m moderado, +k clave...).
            Se guardan como evento legible en el canal y se emite para refrescar la nicklist."""
            await super().on_mode_change(channel, modes, by)
            try:
                texto = " ".join(str(m) for m in (modes or []))
                self._srv._guardar_mensaje(self._acc, channel, "mode", by or "", texto, propio=False)
                self._emit("modo_canal", canal=channel, nick=by or "", modos=texto)
            except Exception as e:
                log.debug(f"IRC: error en cambio de modo: {e}")

        async def on_user_mode_change(self, modes):
            """Cambian MIS modos de usuario (+i invisible, +o IRCop, +w wallops...)."""
            await super().on_user_mode_change(modes)
            self._emit("mis_modos", modos=" ".join(str(m) for m in (modes or [])))

        # ---- invitaciones y kill ----
        async def on_invite(self, channel, by):
            """Me invitan a un canal: se guarda en 'Avisos', se emite (para ofrecer unirse) y
            suena como un privado (es algo dirigido a mí)."""
            await super().on_invite(channel, by)
            try:
                self._srv._guardar_mensaje(
                    self._acc, "*", "notice", by or "",
                    _("Te invita al canal {0}").format(channel), propio=False)
                self._emit("invitacion", canal=channel, nick=by or "")
                event_bus.emit("sound_trigger", "irc_dm")
            except Exception as e:
                log.debug(f"IRC: error procesando invitación: {e}")

        async def on_kill(self, target, by, reason):
            """Me han 'matado' (desconexión forzada por un operador de la red)."""
            try:
                await super().on_kill(target, by, reason)
            except Exception:
                pass
            self._emit("kill", por=by or "", razon=quitar_formato_mirc(reason or ""))

        # ---- CTCP: responder consultas entrantes y medir lag con PING ----
        async def on_ctcp(self, by, target, what, contents):
            """CTCP entrante (aparte de VERSION y ACTION, ya cubiertos). Se responde a las
            consultas estándar que esperan respuesta: PING (eco), TIME (hora local), CLIENTINFO,
            SOURCE, USERINFO y FINGER. Nunca se responde a otro NOTICE/ACTION."""
            try:
                q = (what or "").upper()
                if q == "PING":
                    await self.ctcp_reply(by, "PING", contents or "")
                elif q == "TIME":
                    import time as _t
                    await self.ctcp_reply(by, "TIME", _t.strftime("%a %d %b %Y %H:%M:%S %z"))
                elif q == "CLIENTINFO":
                    await self.ctcp_reply(by, "CLIENTINFO", "ACTION CLIENTINFO PING SOURCE TIME USERINFO VERSION")
                elif q == "SOURCE":
                    await self.ctcp_reply(by, "SOURCE", "https://github.com/")
                elif q == "USERINFO":
                    await self.ctcp_reply(by, "USERINFO", self.VERSION_TEXTO)
                elif q == "FINGER":
                    await self.ctcp_reply(by, "FINGER", self.VERSION_TEXTO)
            except Exception as e:
                log.debug(f"IRC: error respondiendo CTCP {what}: {e}")

        async def on_ctcp_reply(self, by, target, what, response):
            """Respuesta CTCP a una consulta NUESTRA. Para PING calculamos la LATENCIA (guardamos
            el instante al enviarlo); el resto (TIME/VERSION/CLIENTINFO) se muestra tal cual."""
            try:
                q = (what or "").upper()
                if q == "PING":
                    import time as _t
                    marca = None
                    try:
                        marca = float(response)
                    except (TypeError, ValueError):
                        marca = (self._ctcp_ping_pendiente or {}).pop(by.lower(), None)
                    if marca:
                        lag = max(0.0, _t.time() - marca)
                        self._emit("ctcp_respuesta", nick=by, ctcp="PING",
                                   texto=_("Latencia con {0}: {1:.2f} segundos.").format(by, lag))
                        return
                self._emit("ctcp_respuesta", nick=by, ctcp=q,
                           texto=_("Respuesta {0} de {1}: {2}").format(q, by,
                                                                       quitar_formato_mirc(response or "")))
            except Exception as e:
                log.debug(f"IRC: error en respuesta CTCP: {e}")

        # ---- mensajes ----
        async def on_message(self, target, by, message):
            await super().on_message(target, by, message)
            # Ignorar el eco de lo nuestro (echo-message lo entrega si el servidor lo soporta;
            # el envío propio ya se guarda en el momento de enviar).
            if (by or "").lower() == (self.nickname or "").lower():
                return
            # Usuario IGNORADO (/ignore): como si no hablara. Ni se guarda, ni suena, ni cuenta.
            try:
                if self._srv.esta_ignorado(self._acc, by):
                    return
            except Exception:
                pass
            texto = quitar_formato_mirc(message or "")
            if self.is_channel(target):
                destino = target
                es_privado = False
            else:
                # Privado (query): el "canal" es el nick del interlocutor.
                destino = by
                es_privado = True
            highlight = (not es_privado) and self._me_nombran(texto)
            self._srv._guardar_mensaje(self._acc, destino, "msg", by, texto,
                                       propio=False, es_privado=es_privado, highlight=highlight)
            # Sonido identificativo según el NIVEL DE AVISO del canal (todo / solo menciones /
            # silencio). Los privados suenan siempre. El contador de no leídos sube igual.
            try:
                if es_privado:
                    event_bus.emit("sound_trigger", "irc_dm")
                else:
                    nivel = self._srv.nivel_aviso(self._acc, destino)
                    if highlight and nivel in ("all", "mentions"):
                        event_bus.emit("sound_trigger", "irc_highlight")
                    elif nivel == "all":
                        event_bus.emit("sound_trigger", "irc_new_message")
            except Exception:
                pass
            self._emit("mensaje", canal=destino, nick=by, texto=texto,
                       privado=es_privado, highlight=highlight)

        # ---- lista de canales del servidor (/LIST: numéricos 321/322/323) ----
        async def on_raw_321(self, message):
            pass  # RPL_LISTSTART: nada que hacer

        async def on_raw_322(self, message):
            """RPL_LIST: una fila 'canal usuarios :tema'."""
            try:
                params = message.params
                fila = {"name": params[1],
                        "users": int(params[2]) if str(params[2]).isdigit() else 0,
                        "topic": quitar_formato_mirc(params[3] if len(params) > 3 else "")}
                if getattr(self, "_list_resultado", None) is not None:
                    self._list_resultado.append(fila)
            except Exception:
                pass

        async def on_raw_323(self, message):
            """RPL_LISTEND: fin de la lista."""
            ev = getattr(self, "_list_terminado", None)
            if ev is not None:
                ev.set()

        # ---- WHOWAS (datos de un apodo desconectado): 314 filas, 369 fin ----
        async def on_raw_314(self, message):  # RPL_WHOWASUSER
            try:
                if self._whowas_lineas is None:
                    return
                p = message.params  # [me, nick, user, host, *, realname]
                nick = p[1] if len(p) > 1 else ""
                user = p[2] if len(p) > 2 else ""
                host = p[3] if len(p) > 3 else ""
                real = quitar_formato_mirc(p[5]) if len(p) > 5 else ""
                self._whowas_lineas.append(
                    _("{0} era {1}@{2} ({3})").format(nick, user, host, real))
            except Exception:
                pass

        async def on_raw_369(self, message):  # RPL_ENDOFWHOWAS
            if self._whowas_evento is not None:
                self._whowas_evento.set()

        # ---- lista de baneos de un canal: 367 filas, 368 fin ----
        async def on_raw_367(self, message):  # RPL_BANLIST
            try:
                if self._baneos_lista is None:
                    return
                p = message.params  # [me, channel, mask, by, timestamp]
                self._baneos_lista.append({
                    "mascara": p[2] if len(p) > 2 else "",
                    "por": p[3] if len(p) > 3 else "",
                    "cuando": p[4] if len(p) > 4 else "",
                })
            except Exception:
                pass

        async def on_raw_368(self, message):  # RPL_ENDOFBANLIST
            if self._baneos_evento is not None:
                self._baneos_evento.set()

        async def on_raw_381(self, message):  # RPL_YOUREOPER
            self._emit("oper_ok")

        async def on_notice(self, target, by, message):
            await super().on_notice(target, by, message)
            texto = quitar_formato_mirc(message or "")
            # Avisos del servidor / servicios → buffer especial "Avisos" (canal '*'). Los notices
            # a un canal concreto se guardan en ese canal.
            destino = target if self.is_channel(target) else "*"
            self._srv._guardar_mensaje(self._acc, destino, "notice", by or "", texto,
                                       propio=False, es_privado=False)
            self._emit("notice", canal=destino, nick=by or "", texto=texto)

        async def on_ctcp_version(self, by, target, contents):
            try:
                await self.ctcp_reply(by, "VERSION", self.VERSION_TEXTO)
            except Exception:
                pass

        def _me_nombran(self, texto: str) -> bool:
            """True si el texto contiene nuestro nick (highlight)."""
            try:
                nick = (self.nickname or "").lower()
                return bool(nick) and nick in (texto or "").lower()
            except Exception:
                return False

    _ClaseCliente = _ClienteSocialRSS
    return _ClaseCliente


class _NucleoMixin:
    # --- GESTIÓN DEL BUCLE ASYNCIO (calco del patrón Telegram) ---
    def _ensure_loop(self):
        """Arranca el bucle de eventos de IRC en un hilo dedicado si aún no existe."""
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="IRCLoop", daemon=True
            )
            self._thread.start()

    def _run_loop(self):
        # Prioridad BAJA del hilo (regla de la casa: los hilos de fondo nunca compiten con NVDA).
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout: float = 60.0):
        """Ejecuta una corrutina en el bucle de IRC y espera su resultado (bloqueante).
        Llamar SIEMPRE desde un hilo de task_manager, nunca desde el de la interfaz."""
        if not HAS_PYDLE:
            raise IRCError("Las dependencias de IRC (pydle) no están instaladas. "
                           "Ejecute la instalación de requisitos desde build.bat.")
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except IRCError:
            raise
        except TimeoutError:
            future.cancel()
            raise IRCError("La operación de IRC tardó demasiado. Revise la conexión.")
        except Exception as e:
            raise IRCError(self._mensaje_error_irc(e))

    def _lanzar(self, coro):
        """Lanza una corrutina en el bucle de IRC SIN esperar el resultado (fire-and-forget).
        Para operaciones largas como connect(), que no termina hasta la desconexión."""
        if not HAS_PYDLE:
            raise IRCError("Las dependencias de IRC (pydle) no están instaladas.")
        self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @staticmethod
    def _mensaje_error_irc(e: Exception) -> str:
        """Traduce errores técnicos a un mensaje claro en español."""
        texto = str(e) or type(e).__name__
        bajo = texto.lower()
        if "certificate" in bajo or "ssl" in bajo or "tls" in bajo:
            return ("Fallo de seguridad TLS al conectar (certificado no válido). "
                    "Revise el servidor o desactive la verificación SOLO si confía en la red. "
                    f"Detalle: {texto}")
        if "getaddrinfo" in bajo or "name or service" in bajo or "11001" in bajo:
            return "No se encontró el servidor. Revise la dirección de la red IRC."
        if "refused" in bajo or "10061" in bajo:
            return "El servidor rechazó la conexión. Revise el puerto (¿6697 con TLS?)."
        if "timed out" in bajo or "timeout" in bajo:
            return "El servidor no respondió (tiempo agotado). Revise la conexión."
        return texto
