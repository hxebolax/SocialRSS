# -*- coding: utf-8 -*-
"""Bucle asyncio del servicio XMPP, cliente slixmpp y stream en tiempo real.

Mixin de XMPPService (patrón telegram/irc/matrix_nucleo, CUARTA iteración del molde): un hilo
dedicado "XMPPLoop" con su event loop; la UI le habla con run_coroutine_threadsafe a través de
_run(). El stream XML de XMPP es push nativo, así que el tiempo real sale gratis: cada stanza que
llega dispara su callback al instante, sin ningún temporizador.
"""
from app.services.xmpp.xmpp_common import (
    asyncio, threading, Any, Dict, Optional, db, event_bus, log,
    HAS_SLIXMPP, XMPPError, ahora_iso, delay_a_iso, db_id_chat, jid_bare,
    nombre_desde_jid, presencia_a_palabras)


class _NucleoMixin:
    # --- GESTIÓN DEL BUCLE ASYNCIO ---
    def _ensure_loop(self):
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, name="XMPPLoop", daemon=True)
            self._thread.start()

    def _run_loop(self):
        # Prioridad BAJA del hilo (regla de la casa: el fondo nunca compite con NVDA).
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)
        except Exception:
            pass
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro, timeout: float = 60.0):
        """Ejecuta una corrutina en el bucle de XMPP y espera su resultado (bloqueante).
        Llamar SIEMPRE desde un hilo de task_manager, nunca desde el de la interfaz."""
        if not HAS_SLIXMPP:
            raise XMPPError("Las dependencias de XMPP (slixmpp) no están instaladas. "
                            "Ejecute la instalación de requisitos desde build.bat.")
        self._ensure_loop()
        # Guard anti-deadlock (XMPP-001): _run() agenda la corrutina en el XMPPLoop y ESPERA su
        # resultado; si se llamara desde el propio XMPPLoop (p. ej. un callback que invoca un método
        # síncrono), el bucle se quedaría esperándose a sí mismo. Se detecta y se lanza un error de
        # programación claro en vez de colgar el bucle (y con él todas las cuentas).
        try:
            _corriendo = asyncio.get_running_loop()
        except RuntimeError:
            _corriendo = None
        if _corriendo is not None and _corriendo is self._loop:
            raise XMPPError("Error interno de XMPP: se intentó una operación bloqueante desde el "
                            "propio bucle de red. Usa la variante asíncrona o task_manager.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except XMPPError:
            raise
        except TimeoutError:
            future.cancel()
            raise XMPPError("La operación de XMPP tardó demasiado. Revise la conexión.")
        except Exception as e:
            raise XMPPError(self._mensaje_error_xmpp(e))

    def _llamar_en_loop(self, fn):
        """Ejecuta una función SÍNCRONA de slixmpp (send_presence, send_message...) en el hilo del
        bucle, sin esperar resultado (call_soon_threadsafe). Para stanzas de 'disparar y olvidar'."""
        self._ensure_loop()
        try:
            self._loop.call_soon_threadsafe(fn)
        except Exception as e:
            log.debug(f"XMPP: no se pudo ejecutar en el loop: {e}")

    @staticmethod
    def _mensaje_error_xmpp(e: Exception) -> str:
        texto = str(e) or type(e).__name__
        bajo = texto.lower()
        if "not-authorized" in bajo or "authentication failed" in bajo or "failed_auth" in bajo:
            return "Usuario o contraseña incorrectos (el servidor rechazó la autenticación)."
        if "getaddrinfo" in bajo or "name or service not known" in bajo or "nodename" in bajo:
            return "No se encontró el servidor. Revise la dirección del JID (usuario@servidor)."
        if "certificate" in bajo or "ssl" in bajo or "tls" in bajo:
            return f"Fallo de seguridad TLS al conectar. Detalle: {texto}"
        if "timed out" in bajo or "timeout" in bajo:
            return "El servidor no respondió (tiempo agotado). Revise la conexión."
        if "connection" in bajo and ("refused" in bajo or "reset" in bajo):
            return "No se pudo conectar con el servidor (conexión rechazada)."
        return texto

    # ---------------- Cliente slixmpp ----------------
    # Plugins XEP que se registran en TODA cuenta: el perfil moderno de un cliente XMPP (disco,
    # caps, stream management, ping, carbons, MAM, receipts, chat states, marcadores, reacciones,
    # respuestas, corrección, retractación, HTTP Upload, bookmarks, blocking, PEP, ad-hoc...).
    # Registrarlos es barato y prepara TODAS las tandas; cada tanda solo cablea su UX.
    PLUGINS = [
        "xep_0030",  # Service Discovery
        "xep_0115",  # Entity Capabilities
        "xep_0004",  # Data Forms (admin/ad-hoc/registro)
        "xep_0012",  # Last Activity (info técnica de contacto)
        "xep_0045",  # MUC
        "xep_0047",  # In-Band Bytestreams (respaldo de transferencia)
        "xep_0055",  # Jabber Search (buscar usuarios)
        "xep_0048",  # Bookmarks (legacy)
        "xep_0049",  # Private XML storage (para bookmarks legacy)
        "xep_0050",  # Ad-Hoc Commands
        "xep_0054",  # vCard-temp
        "xep_0060",  # PubSub
        "xep_0066",  # Out of Band Data
        "xep_0071",  # XHTML-IM
        "xep_0077",  # In-Band Registration
        "xep_0080",  # User Location (geoloc, PEP)
        "xep_0084",  # User Avatar
        "xep_0085",  # Chat State Notifications
        "xep_0092",  # Software Version
        "xep_0107",  # User Mood (PEP)
        "xep_0108",  # User Activity (PEP)
        "xep_0118",  # User Tune (PEP)
        "xep_0128",  # Service Discovery Extensions
        "xep_0153",  # vCard-Based Avatars
        "xep_0163",  # PEP
        "xep_0172",  # User Nickname
        "xep_0184",  # Message Receipts
        "xep_0186",  # Invisible Command (presencia invisible)
        "xep_0191",  # Blocking Command
        "xep_0198",  # Stream Management
        "xep_0199",  # XMPP Ping
        "xep_0202",  # Entity Time
        "xep_0203",  # Delayed Delivery
        "xep_0223",  # Persistent Storage of Private Data via PubSub
        "xep_0224",  # Attention (zumbido)
        "xep_0231",  # Bits of Binary
        "xep_0249",  # Direct MUC Invitations
        "xep_0280",  # Message Carbons
        "xep_0292",  # vCard4 Over XMPP
        "xep_0300",  # Use of Cryptographic Hash Functions
        "xep_0308",  # Last Message Correction
        "xep_0313",  # Message Archive Management (MAM)
        "xep_0333",  # Chat Markers
        "xep_0334",  # Message Processing Hints
        "xep_0352",  # Client State Indication
        "xep_0359",  # Unique and Stable Stanza IDs
        "xep_0363",  # HTTP File Upload
        "xep_0380",  # Explicit Message Encryption
        "xep_0402",  # PEP Native Bookmarks
        "xep_0421",  # Occupant Ids
        "xep_0424",  # Message Retraction
        "xep_0425",  # Moderated Message Retraction (MUC)
        "xep_0428",  # Fallback Indication
        "xep_0444",  # Message Reactions
        "xep_0461",  # Message Replies
        "xep_0490",  # Message Displayed Synchronization
    ]

    def _crear_cliente(self, jid: str, password: str, recurso: str = "SocialRSS",
                       account_id: Optional[int] = None, no_verificar_cert: bool = False):
        """Crea el ClientXMPP de slixmpp (importándolo de forma diferida) con el perfil de plugins
        de la casa. TLS obligatorio (sin opción de texto claro). Si se pasa account_id, se registra
        OMEMO con el almacén de esa cuenta (en la verificación de alta no hace falta)."""
        from slixmpp import ClientXMPP
        jid_completo = f"{jid_bare(jid)}/{recurso or 'SocialRSS'}"
        cliente = ClientXMPP(jid_completo, password)
        cliente._sr_account_id = account_id
        # Seguridad y compatibilidad del cifrado del stream (slixmpp 1.16). Estos son los atributos
        # REALES que controlan el TLS (no existe 'force_starttls' como atributo):
        #   - enable_starttls=True  -> STARTTLS en el puerto 5222 (lo estándar en casi todos los
        #     servidores; es lo que exige, por ejemplo, un Synology/Prosody/ejabberd típico).
        #   - enable_direct_tls=False-> NO intentar TLS directo "a ciegas" en el 5222 cuando el
        #     servidor no tiene registros SRV: hacerlo rompía con "WRONG_VERSION_NUMBER" (se hablaba
        #     TLS a un puerto que empieza en texto claro y luego sube a STARTTLS). Si el servidor SÍ
        #     anuncia DirectTLS por SRV (_xmpps-client), slixmpp lo usa igualmente por el SRV.
        #   - enable_plaintext=False -> jamás enviar credenciales sin cifrar (la conexión SIEMPRE va
        #     cifrada, como promete la interfaz).
        cliente.enable_starttls = True
        cliente.enable_direct_tls = False
        cliente.enable_plaintext = False
        # Opción por cuenta: NO verificar el certificado TLS. La conexión SIGUE cifrada (TLS), pero
        # se acepta un certificado caducado o autofirmado. Necesario para servidores cuyo certificado
        # ha caducado (p. ej. se reportó xmpp.eco.br: "certificate has expired"). Es una decisión
        # consciente del usuario (menos seguro), por eso es opcional y va con aviso en la UI.
        if no_verificar_cert:
            try:
                import ssl
                cliente.ssl_context.check_hostname = False
                cliente.ssl_context.verify_mode = ssl.CERT_NONE
            except Exception as e:
                log.debug(f"XMPP: no se pudo relajar la verificación del certificado: {e}")
        # Stream Management: reanudar la sesión sin perder stanzas al cortarse la red (XEP-0198).
        for p in self.PLUGINS:
            try:
                cliente.register_plugin(p)
            except Exception as e:
                log.debug(f"XMPP: plugin {p} no disponible: {e}")
        # OMEMO (XEP-0384): cifrado de extremo a extremo. Se registra con el almacén de ESTA cuenta
        # si la pila está instalada; al conectar publica nuestro dispositivo para que otros puedan
        # cifrarnos, y habilita cifrar/descifrar por conversación. Si no está, XMPP va sin cifrado.
        if account_id is not None:
            try:
                self._registrar_omemo(account_id, cliente)
            except Exception as e:
                log.debug(f"XMPP: OMEMO no registrado: {e}")
        return cliente

    # ---------------- Registro de callbacks (eventos del stream) ----------------
    def _registrar_callbacks(self, account_id: int, cliente):
        """Engancha los manejadores de eventos del stream a la BD y al event_bus. Los que faltan
        (mensajes ricos, MAM, MUC…) se añaden en sus tandas; aquí van los de X1: sesión, presencia,
        roster, suscripciones y mensajes 1:1 básicos."""

        async def _on_session_start(_e):
            try:
                cliente.send_presence()  # anunciarnos disponibles
                await cliente.get_roster()
            except Exception as ex:
                log.debug(f"XMPP: session_start: {ex}")
            # Carbons (XEP-0280): recibir copia de lo que llega/envías desde OTROS dispositivos, para
            # sincronizar la conversación entre ellos. Si el servidor no lo soporta, no pasa nada.
            try:
                await cliente["xep_0280"].enable()
            except Exception as ex:
                log.debug(f"XMPP: Carbons no disponibles: {ex}")
            with self._estado_lock:
                self._conectadas[account_id] = True
            self._sincronizar_roster(account_id, cliente)
            self._restaurar_presencia(account_id, cliente)
            try:
                self.podar_historial(account_id)  # poda por días al conectar (X3)
            except Exception:
                pass
            # Autojoin a las salas MUC: unirse_sala() es SÍNCRONO y usa _run(), así que NO puede
            # correr aquí (estamos dentro del XMPPLoop: se autobloquearía, XMPP-001). Se lanza en un
            # hilo de task_manager, donde _run() sí puede esperar al bucle; así una sala lenta no
            # detiene el resto de la conexión ni las demás cuentas.
            try:
                from app.core.task_manager import task_manager
                task_manager.start_task(
                    lambda ctx, a=account_id: self.autoentrar_salas(a),
                    pass_context=True, unique_key=f"xmpp:autojoin:{account_id}")
            except Exception:
                pass
            self._emit(account_id, "conectado")

        def _on_changed_status(presence):
            self._actualizar_presencia(account_id, presence)

        def _on_got_offline(presence):
            self._actualizar_presencia(account_id, presence, offline=True)

        def _on_roster_update(_iq):
            self._sincronizar_roster(account_id, cliente)

        def _on_subscribe(presence):
            self._registrar_solicitud(account_id, str(presence["from"].bare))

        async def _on_message(msg):
            # OMEMO: si viene cifrado, descifrar antes de guardar; si no se puede, fila explicativa.
            if self.omemo_disponible():
                try:
                    texto, cifrado, err = await self._descifrar_stanza(cliente, msg)
                    if cifrado:
                        if err:
                            self._guardar_cifrado_fallido(account_id, cliente, msg, es_muc=False)
                            return
                        try:
                            msg["body"] = texto
                        except Exception:
                            pass
                        self._al_recibir_mensaje(account_id, cliente, msg, cifrado=True)
                        return
                except Exception as ex:
                    log.debug(f"XMPP: descifrado 1:1: {ex}")
            self._al_recibir_mensaje(account_id, cliente, msg)

        async def _on_muc_message(msg):
            if self.omemo_disponible():
                try:
                    texto, cifrado, err = await self._descifrar_stanza(cliente, msg)
                    if cifrado:
                        if err:
                            self._guardar_cifrado_fallido(account_id, cliente, msg, es_muc=True)
                            return
                        try:
                            msg["body"] = texto
                        except Exception:
                            pass
                        self._al_recibir_muc(account_id, cliente, msg, cifrado=True)
                        return
                except Exception as ex:
                    log.debug(f"XMPP: descifrado MUC: {ex}")
            self._al_recibir_muc(account_id, cliente, msg)

        def _on_muc_subject(msg):
            try:
                room = jid_bare(str(msg["from"]))
                tema = msg["subject"] or ""
                fila = db.query_one("SELECT mi_nick FROM xmpp_chats WHERE id = ?",
                                    (db_id_chat(account_id, room),))
                if not fila:
                    return
                self._guardar_mensaje(account_id, room, "tema-" + ahora_iso(), room,
                                      "", _("Tema de la sala: {0}").format(tema) if tema else _("(sin tema)"),
                                      "aviso", ahora_iso(), propio=False, leido=True, contar=False)
            except Exception:
                pass

        def _on_muc_invite(msg):
            try:
                # Invitación mediada (0045): la sala está en el 'from'.
                room = jid_bare(str(msg["from"]))
                self._registrar_invitacion_muc(account_id, room)
            except Exception:
                pass

        def _on_muc_invite_directa(msg):
            try:
                room = jid_bare(str(msg["groupchat_invite"]["jid"]))
                if room:
                    self._registrar_invitacion_muc(account_id, room)
            except Exception:
                pass

        def _on_reactions(msg):
            # Reacciones entrantes (XEP-0444): el conjunto completo de un usuario a un mensaje.
            try:
                mid = msg["reactions"]["id"]
                if not mid:
                    return
                valores = [str(v) for v in (msg["reactions"]["values"] or [])]
                desde = str(msg["from"])
                if msg["type"] == "groupchat":
                    reactor = desde.split("/", 1)[1] if "/" in desde else desde
                    room = jid_bare(desde)
                    minick = (db.query_one("SELECT mi_nick FROM xmpp_chats WHERE id = ?",
                                           (db_id_chat(account_id, room),)) or [""])[0]
                    if reactor == minick:
                        return  # mi propia reacción ya la registré al enviarla
                else:
                    reactor = jid_bare(desde)
                self._actualizar_reacciones(account_id, mid, reactor, valores)
            except Exception:
                pass

        def _on_retract(msg):
            # Retractación de un mensaje (XEP-0424): márcalo como eliminado.
            try:
                ref = ""
                try:
                    ref = msg["retract"]["id"]
                except Exception:
                    ref = ""
                if not ref:
                    return
                db.execute("UPDATE xmpp_mensajes SET texto = ?, editado = 1 "
                           "WHERE account_id = ? AND (stanza_id = ? OR origin_id = ?)",
                           (_("[Mensaje retirado]"), account_id, ref, ref))
                self._reindexar_fts(account_id, ref, "")
                self._emit(account_id, "mensaje", chat_jid="", propio=False, cuenta=False)
            except Exception:
                pass

        def _on_chatstate(msg):
            # "Está escribiendo…" (XEP-0085): solo interesa para el título de la conversación.
            try:
                estado = msg["chat_state"]
                desde = jid_bare(str(msg["from"]))
                yo = jid_bare(str(cliente.boundjid))
                if desde and desde != yo:
                    self._emit(account_id, "chatstate", jid=desde, estado=estado)
            except Exception:
                pass

        async def _procesar_carbon(interno):
            if self.omemo_disponible():
                try:
                    texto, cifrado, err = await self._descifrar_stanza(cliente, interno)
                    if cifrado:
                        if err:
                            self._guardar_cifrado_fallido(account_id, cliente, interno)
                            return
                        try:
                            interno["body"] = texto
                        except Exception:
                            pass
                        self._al_recibir_mensaje(account_id, cliente, interno,
                                                 es_carbon=True, cifrado=True)
                        return
                except Exception as ex:
                    log.debug(f"XMPP: descifrado carbon: {ex}")
            self._al_recibir_mensaje(account_id, cliente, interno, es_carbon=True)

        async def _on_carbon_received(msg):
            # Copia de un mensaje que te llegó en OTRO dispositivo (Carbons).
            try:
                await _procesar_carbon(msg["carbon_received"]["message"])
            except Exception:
                pass

        async def _on_carbon_sent(msg):
            # Copia de un mensaje que TÚ enviaste desde otro dispositivo (Carbons).
            try:
                await _procesar_carbon(msg["carbon_sent"]["message"])
            except Exception:
                pass

        def _on_receipt(msg):
            # Recibo de entrega (XEP-0184): el contacto confirmó que recibió tu mensaje.
            try:
                ref = msg["receipt"]  # el id del mensaje entregado
                if ref:
                    db.execute("UPDATE xmpp_mensajes SET entregado = 1 "
                               "WHERE account_id = ? AND (origin_id = ? OR stanza_id = ?)",
                               (account_id, ref, ref))
                    self._emit(account_id, "entregado", ref=ref)
            except Exception:
                pass

        def _on_marcador_visto(msg):
            # El contacto marcó como LEÍDO (XEP-0333 displayed) tu mensaje: leído implica entregado.
            try:
                ref = msg["displayed"]["id"]
                if ref:
                    db.execute("UPDATE xmpp_mensajes SET entregado = 1 "
                               "WHERE account_id = ? AND (origin_id = ? OR stanza_id = ?)",
                               (account_id, ref, ref))
                    self._emit(account_id, "leido_por", ref=ref)
            except Exception:
                pass

        def _on_attention(msg):
            # Zumbido / llamada de atención entrante (XEP-0224): sonido + aviso.
            try:
                desde = jid_bare(str(msg["from"]))
                yo = jid_bare(str(cliente.boundjid))
                if desde == yo:
                    return
                nombre = self._nombre_chat(account_id, desde)
                self._emit(account_id, "zumbido", jid=desde, nombre=nombre)
                try:
                    event_bus.emit("sound_trigger", "xmpp_highlight")
                except Exception:
                    pass
            except Exception:
                pass

        def _on_pep_mood(msg):
            self._guardar_pep(account_id, msg, "mood")

        def _on_pep_activity(msg):
            self._guardar_pep(account_id, msg, "activity")

        def _on_pep_tune(msg):
            self._guardar_pep(account_id, msg, "tune")

        def _on_disconnected(_e):
            with self._estado_lock:
                ya = self._conectadas.get(account_id, False)
                self._conectadas[account_id] = False
            if ya:
                self._emit(account_id, "desconectado")

        def _on_failed_auth(_e):
            with self._estado_lock:
                self._fallo_auth[account_id] = True

        cliente.add_event_handler("session_start", _on_session_start)
        cliente.add_event_handler("changed_status", _on_changed_status)
        cliente.add_event_handler("got_offline", _on_got_offline)
        cliente.add_event_handler("roster_update", _on_roster_update)
        cliente.add_event_handler("presence_subscribe", _on_subscribe)
        cliente.add_event_handler("message", _on_message)
        cliente.add_event_handler("groupchat_message", _on_muc_message)
        cliente.add_event_handler("groupchat_subject", _on_muc_subject)
        cliente.add_event_handler("groupchat_invite", _on_muc_invite)
        cliente.add_event_handler("groupchat_direct_invite", _on_muc_invite_directa)
        cliente.add_event_handler("reactions", _on_reactions)
        cliente.add_event_handler("message_retract", _on_retract)
        cliente.add_event_handler("chatstate", _on_chatstate)
        cliente.add_event_handler("carbon_received", _on_carbon_received)
        cliente.add_event_handler("carbon_sent", _on_carbon_sent)
        cliente.add_event_handler("receipt_received", _on_receipt)
        cliente.add_event_handler("marker_displayed", _on_marcador_visto)
        cliente.add_event_handler("attention", _on_attention)
        cliente.add_event_handler("user_mood_publish", _on_pep_mood)
        cliente.add_event_handler("user_activity_publish", _on_pep_activity)
        cliente.add_event_handler("user_tune_publish", _on_pep_tune)
        cliente.add_event_handler("disconnected", _on_disconnected)
        cliente.add_event_handler("failed_auth", _on_failed_auth)

    def _emit(self, account_id: int, tipo: str, **datos):
        try:
            datos.update({"account_id": account_id, "tipo": tipo})
            event_bus.emit("xmpp_evento", datos)
        except Exception as e:
            log.debug(f"XMPP: error emitiendo evento {tipo}: {e}")

    # ---------------- Roster y presencia → BD ----------------
    def _sincronizar_roster(self, account_id: int, cliente):
        """Vuelca el roster de slixmpp a xmpp_chats (contactos), conservando lo local (no-leídos,
        nivel de aviso). NO toca el foco de ninguna lista (lo hace la UI con su refresco coalescido).

        RENDIMIENTO: con cientos de contactos (WhatsApp) esto se disparaba en cada push del roster.
        Ahora (a) se resuelven TODAS las filas existentes con UNA sola consulta, (b) se SALTA la
        escritura si nombre y suscripción no cambian (en régimen estable casi ningún push cambia
        nada) y (c) las escrituras que queden van en UNA transacción (un solo commit, una sola toma
        del candado de escritura), en vez de un SELECT+commit por contacto."""
        try:
            roster = cliente.client_roster
        except Exception:
            return
        yo = jid_bare(str(cliente.boundjid))
        # Estado local actual de esta cuenta en UNA consulta (id -> (tipo, nombre, suscripcion)).
        existentes = {}
        try:
            for r in db.query("SELECT id, tipo, COALESCE(nombre,''), COALESCE(suscripcion,'') "
                              "FROM xmpp_chats WHERE account_id = ?", (account_id,)):
                existentes[r[0]] = (r[1], r[2], r[3])
        except Exception:
            pass
        pendientes = []   # (sql, params) a escribir; solo lo que REALMENTE cambia
        cambios = 0
        for jid in list(roster.keys()):
            if not jid or jid == yo:
                continue
            item = roster[jid]
            nombre_srv = item["name"] or ""   # nombre que da el servidor (puede venir vacío)
            suscripcion = item["subscription"] or ""
            fila_id = db_id_chat(account_id, jid)
            fila = existentes.get(fila_id)
            if fila:
                tipo_actual, nombre_actual, susc_actual = fila
                if tipo_actual == "muc":
                    continue  # una sala NO se toca desde el roster (conservar su nombre y tipo)
                # El nombre solo se actualiza si el servidor da uno REAL; si no, se conserva el que
                # ya tuvieras (puede ser uno que le pusiste tú), nunca se pisa con el JID crudo.
                nuevo_nombre = nombre_srv or nombre_actual or nombre_desde_jid(jid)
                if nuevo_nombre == nombre_actual and suscripcion == susc_actual:
                    continue  # nada que cambiar: la mayoría de los pushes en régimen estable
                pendientes.append(("UPDATE xmpp_chats SET nombre = ?, suscripcion = ? WHERE id = ?",
                                   (nuevo_nombre, suscripcion, fila_id)))
            else:
                pendientes.append(("""INSERT INTO xmpp_chats (id, account_id, jid, tipo, nombre, suscripcion)
                                      VALUES (?, ?, ?, 'contacto', ?, ?)""",
                                   (fila_id, account_id, jid, nombre_srv or nombre_desde_jid(jid), suscripcion)))
                cambios += 1
        if pendientes:
            with db.transaccion():   # un solo commit para TODO el push
                for sql, params in pendientes:
                    db.execute(sql, params)
            if cambios:
                self.invalidar_puentes(account_id)   # llegaron contactos nuevos: quizá un dominio nuevo
            # Solo se avisa a la UI (para que refresque) si REALMENTE cambió algo: un push que no
            # trae novedades ya no dispara un refresco de lista innecesario.
            self._emit(account_id, "roster_actualizado")

    def _actualizar_presencia(self, account_id: int, presence, offline: bool = False):
        """Anota el cambio de presencia (en palabras) de un contacto para escribirlo COALESCIDO. La
        presencia es RUIDOSA (WhatsApp la emite sin parar): en vez de un UPDATE+commit por stanza, se
        acumula en un buffer y se vuelca cada ~1,5 s en UNA transacción, con un solo evento de refresco
        por cuenta (la lista ya coalesce en la UI y JAMÁS se mueve el foco)."""
        try:
            desde = jid_bare(str(presence["from"]))
            if not desde:
                return
            show = "" if offline else (presence["show"] or "")
            tipo = "unavailable" if offline else (presence["type"] or "available")
            estado_texto = "" if offline else (presence["status"] or "")
            etiqueta = presencia_a_palabras(show, tipo)
            fila_id = db_id_chat(account_id, desde)
            armar = False
            with self._presencia_lock:
                self._presencia_buffer[fila_id] = (etiqueta, estado_texto, account_id)
                if not self._presencia_flush_armado:
                    self._presencia_flush_armado = True
                    armar = True
            if armar:
                # IMPORTANTE: el volcado (que toma el candado de ESCRITURA) se hace EN EL PROPIO BUCLE
                # de slixmpp, no en un hilo aparte. Si un hilo de fondo retuviera el candado, el bucle
                # (hilo único) se BLOQUEARÍA al escribir y congelaría TODO el XMPP (envíos, aperturas).
                # Al hacerlo en el bucle, la escritura se serializa con el resto de su trabajo, sin
                # bloqueos entre hilos, y sigue siendo UNA sola escritura por ráfaga.
                try:
                    self._loop.call_later(1.5, self._volcar_presencia)
                except Exception:
                    self._volcar_presencia()
        except Exception as e:
            log.debug(f"XMPP: presencia no procesada: {e}")

    def _volcar_presencia(self):
        """Vuelca a la BD, en UNA transacción, todas las presencias acumuladas y avisa (un evento por
        cuenta tocada). Se ejecuta EN EL BUCLE de slixmpp (call_later) ~1,5 s tras la primera presencia
        de la ráfaga: así la escritura no la hace un hilo de fondo que pudiera bloquear el bucle."""
        with self._presencia_lock:
            pendientes = self._presencia_buffer
            self._presencia_buffer = {}
            self._presencia_flush_armado = False
        if not pendientes:
            return
        cuentas = set()
        try:
            with db.transaccion():
                for fila_id, (etiqueta, estado_texto, account_id) in pendientes.items():
                    if db.execute("UPDATE xmpp_chats SET presencia = ?, estado_texto = ? "
                                  "WHERE id = ? AND tipo = 'contacto'",
                                  (etiqueta, estado_texto, fila_id)):
                        cuentas.add(account_id)
        except Exception as e:
            log.debug(f"XMPP: no se pudo volcar la presencia: {e}")
            return
        for account_id in cuentas:
            self._emit(account_id, "presencia")

    def _registrar_solicitud(self, account_id: int, jid: str):
        """Una suscripción de presencia pendiente (alguien quiere añadirte). Se guarda como fila de
        'solicitud' para el buffer Solicitudes."""
        fila_id = db_id_chat(account_id, jid)
        existe = db.query_one("SELECT 1 FROM xmpp_chats WHERE id = ?", (fila_id,))
        if existe:
            db.execute("UPDATE xmpp_chats SET solicitud = 1 WHERE id = ?", (fila_id,))
        else:
            db.execute("""INSERT INTO xmpp_chats (id, account_id, jid, tipo, nombre, solicitud)
                          VALUES (?, ?, ?, 'contacto', ?, 1)""",
                       (fila_id, account_id, jid, nombre_desde_jid(jid)))
        self._emit(account_id, "solicitud", jid=jid)

    # ---------------- Mensajes 1:1 en vivo (X2) ----------------
    def _al_recibir_mensaje(self, account_id: int, cliente, msg, es_carbon: bool = False,
                            cifrado: bool = False):
        """Guarda un mensaje de chat 1:1 (directo o por Carbons) con dedupe por stanza-id, actualiza
        el resumen del chat, suena y avisa si procede. 'cifrado' marca los que llegaron por OMEMO."""
        try:
            if msg["type"] not in ("chat", "normal"):
                return
            cuerpo = msg["body"] or ""
            if not cuerpo:
                return  # chat-states/receipts sin cuerpo no son mensajes visibles
            desde = jid_bare(str(msg["from"]))
            yo = jid_bare(str(cliente.boundjid))
            propio = (desde == yo)
            chat_jid = jid_bare(str(msg["to"])) if propio else desde
            # Corrección de un mensaje ya recibido (XEP-0308): actualiza en vez de insertar.
            try:
                reemplaza = msg["replace"]["id"]
            except Exception:
                reemplaza = ""
            if reemplaza:
                afec = db.execute(
                    "UPDATE xmpp_mensajes SET texto = ?, editado = 1 "
                    "WHERE account_id = ? AND chat_jid = ? AND (origin_id = ? OR stanza_id = ?)",
                    (cuerpo, account_id, chat_jid, reemplaza, reemplaza))
                if afec:
                    self._reindexar_fts(account_id, reemplaza, cuerpo)
                    self._emit(account_id, "mensaje", chat_jid=chat_jid, propio=propio, cuenta=False)
                    return
            stanza_id = self._stanza_id_de(msg) or msg["id"] or ahora_iso()
            media_url, etiqueta, es_voz = self._media_de(msg)
            reply_to = self._reply_de(msg)
            texto = etiqueta if media_url else cuerpo
            tipo = "media" if media_url else "msg"
            nuevo = self._guardar_mensaje(
                account_id, chat_jid, stanza_id, desde,
                self._nombre_chat(account_id, desde) if not propio else self._nombre_chat(account_id, chat_jid),
                texto, tipo, ahora_iso(), propio, leido=propio, contar=not propio,
                media_url=media_url or "", reply_to=reply_to, cifrado=cifrado, es_voz=es_voz)
            if nuevo and not propio:
                self._avisar_incoming(account_id, chat_jid, desde, cuerpo)
        except Exception as e:
            log.debug(f"XMPP: mensaje no procesado: {e}")

    def _al_recibir_muc(self, account_id: int, cliente, msg, cifrado: bool = False):
        """Guarda un mensaje de una sala MUC (chat_jid = jid de la sala; autor = apodo). El eco de
        NUESTRO propio mensaje también llega por aquí (así se ve como lo ven los demás); se detecta
        por el apodo. Dedupe por stanza-id de la sala."""
        try:
            cuerpo = msg["body"] or ""
            desde = str(msg["from"])
            room = jid_bare(desde)
            nick = desde.split("/", 1)[1] if "/" in desde else ""
            if not cuerpo:
                # Sin cuerpo: puede ser un "está escribiendo" (0085) de un ocupante → avisar con su
                # apodo; el resto (temas, presencias) no interesa aquí.
                try:
                    estado = msg["chat_state"]
                except Exception:
                    estado = ""
                if estado and nick:
                    minick = (db.query_one("SELECT mi_nick FROM xmpp_chats WHERE id = ?",
                                           (db_id_chat(account_id, room),)) or [""])[0]
                    if nick != (minick or ""):
                        self._emit(account_id, "chatstate", jid=room, estado=estado, nick=nick)
                return
            fila = db.query_one("SELECT mi_nick FROM xmpp_chats WHERE id = ?",
                                (db_id_chat(account_id, room),))
            if not fila:
                return  # no estamos (o no guardamos) esa sala
            mi_nick = fila[0] or ""
            propio = (nick == mi_nick)
            stanza_id = self._stanza_id_de(msg) or msg["id"] or ahora_iso()
            fecha = delay_a_iso(msg["delay"]) if msg["delay"] else ahora_iso()
            highlight = bool(mi_nick) and (mi_nick.lower() in cuerpo.lower())
            media_url, etiqueta, es_voz = self._media_de(msg)
            texto = etiqueta if media_url else cuerpo
            tipo = "media" if media_url else "msg"
            nuevo = self._guardar_mensaje(account_id, room, stanza_id, nick or room, nick or room,
                                          texto, tipo, fecha, propio, leido=propio,
                                          contar=not propio, highlight=highlight,
                                          media_url=media_url or "", reply_to=self._reply_de(msg),
                                          cifrado=cifrado, es_voz=es_voz)
            if nuevo and not propio:
                self._avisar_muc(account_id, room, highlight)
        except Exception as e:
            log.debug(f"XMPP: mensaje MUC no procesado: {e}")

    def _avisar_muc(self, account_id: int, room: str, highlight: bool):
        fila = db.query_one("SELECT COALESCE(notify_level,'all'), COALESCE(silenciado_hasta,'') "
                            "FROM xmpp_chats WHERE id = ?", (db_id_chat(account_id, room),))
        nivel = (fila[0] if fila else "all")
        if fila and self._silencio_vigente(fila[1]) and not highlight:
            return  # silenciado temporalmente (las menciones sí avisan, como WhatsApp)
        if nivel == "none" and not highlight:
            return
        if nivel == "mentions" and not highlight:
            return
        try:
            event_bus.emit("sound_trigger", "xmpp_highlight" if highlight else "xmpp_new_message")
        except Exception:
            pass

    def _registrar_invitacion_muc(self, account_id: int, room: str):
        """Guarda una invitación a una sala como fila 'solicitud' de tipo muc (buffer Solicitudes).
        Los puentes (WhatsApp…) reenvían las invitaciones de TUS grupos en cada sincronización; si
        ya te uniste (autojoin=1) NO se vuelve a marcar como pendiente, así no reaparece una y otra
        vez en Solicitudes una sala que ya aceptaste."""
        fila_id = db_id_chat(account_id, room)
        fila = db.query_one("SELECT COALESCE(autojoin,0), COALESCE(solicitud,0) FROM xmpp_chats WHERE id = ?", (fila_id,))
        if fila:
            if fila[0]:            # ya unida/aceptada -> no resucitar como solicitud
                return
            db.execute("UPDATE xmpp_chats SET tipo='muc', solicitud=1 WHERE id=?", (fila_id,))
        else:
            db.execute("""INSERT INTO xmpp_chats (id, account_id, jid, tipo, nombre, solicitud)
                          VALUES (?, ?, ?, 'muc', ?, 1)""",
                       (fila_id, account_id, room, nombre_desde_jid(room)))
            self.invalidar_puentes(account_id)   # sala nueva: podría ser de una pasarela nueva
        self._emit(account_id, "invitacion_muc", room_jid=room)
        # La invitación llega sin nombre (solo el JID). Pedimos el nombre REAL del grupo (asunto) por
        # disco#info, en el propio bucle, y lo guardamos: así la solicitud aparece ya con su nombre.
        try:
            import asyncio as _a
            _a.ensure_future(self._rellenar_nombre_sala(account_id, room))
        except Exception:
            pass

    async def _rellenar_nombre_sala(self, account_id: int, room: str):
        """Pide por disco#info el nombre (asunto) de una sala y lo guarda si el actual es un marcador
        (el JID crudo). NO pisa un nombre que hayas puesto tú a mano."""
        with self._lock:
            cliente = self._clients.get(account_id)
        if cliente is None:
            return
        try:
            import asyncio as _a
            info = await _a.wait_for(cliente["xep_0030"].get_info(jid=room), timeout=15)
            nombre = ""
            for ident in info["disco_info"]["identities"]:  # (category, type, lang, name)
                if len(ident) > 3 and ident[3]:
                    nombre = ident[3]
                    break
            if not nombre or nombre == room:
                return
            fila_id = db_id_chat(account_id, room)
            actual = db.query_one("SELECT nombre FROM xmpp_chats WHERE id = ?", (fila_id,))
            if actual and (not actual[0] or actual[0] == room or actual[0] == nombre_desde_jid(room)):
                db.execute("UPDATE xmpp_chats SET nombre = ? WHERE id = ?", (nombre, fila_id))
                self._emit(account_id, "invitacion_muc", room_jid=room)
        except Exception as e:
            log.debug(f"XMPP: no se pudo obtener el nombre de la sala {room}: {e}")

    def _avisar_incoming(self, account_id: int, chat_jid: str, sender: str, texto: str):
        """Sonido + aviso por voz de un mensaje entrante, respetando el nivel de aviso y el silencio.
        Si la conversación de ese chat está abierta y enfocada, no molesta (lo gestiona la UI)."""
        fila = db.query_one("SELECT COALESCE(notify_level,'all'), COALESCE(silenciado_hasta,'') "
                            "FROM xmpp_chats WHERE id = ?", (db_id_chat(account_id, chat_jid),))
        nivel = (fila[0] if fila else "all")
        if nivel == "none":
            return
        if fila and self._silencio_vigente(fila[1]):
            return  # silenciado temporalmente: no suena ni avisa
        try:
            event_bus.emit("sound_trigger", "xmpp_dm")
        except Exception:
            pass

    @staticmethod
    def _silencio_vigente(hasta: str) -> bool:
        """True si 'silenciado_hasta' está en el futuro (silencio activo)."""
        from datetime import datetime, timezone
        if not hasta:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(str(hasta).replace("Z", "+00:00"))
        except Exception:
            return False

    @staticmethod
    def _media_de(msg):
        """Si el mensaje trae un adjunto (URL por OOB, XEP-0066), devuelve (url, etiqueta_legible,
        es_voz); si no, (None, None, False). El cuerpo suele ser la propia URL. Una NOTA DE VOZ
        (códec push-to-talk) se etiqueta "Mensaje de voz", igual que las que envías tú."""
        try:
            url = msg["oob"]["url"] or ""
        except Exception:
            url = ""
        if not url:
            # Algunos clientes solo mandan la URL como cuerpo (sin OOB): detéctala si es un archivo.
            cuerpo = (msg["body"] or "").strip()
            if cuerpo.startswith(("http://", "https://")) and " " not in cuerpo:
                from app.services.xmpp.xmpp_medios import _MediosMixin
                if _MediosMixin.es_audio(cuerpo) or "." in cuerpo.rsplit("/", 1)[-1]:
                    url = cuerpo
        if not url:
            return None, None, False
        from app.services.xmpp.xmpp_medios import _MediosMixin
        nombre = _MediosMixin.nombre_desde_url(url)
        if _MediosMixin.es_nota_voz(url):
            return url, _("Mensaje de voz"), True
        return url, _MediosMixin.etiqueta_media(nombre), False

    @staticmethod
    def _reply_de(msg) -> str:
        """El id del mensaje al que responde (XEP-0461), '' si no responde a ninguno."""
        try:
            return msg["reply"]["id"] or ""
        except Exception:
            return ""

    @staticmethod
    def _stanza_id_de(msg) -> str:
        """El stanza-id estable (XEP-0359) del mensaje, para el dedupe. Si no hay, ''."""
        try:
            sid = msg["stanza_id"]["id"]
            if sid:
                return sid
        except Exception:
            pass
        try:
            return msg["origin_id"]["id"] or ""
        except Exception:
            return ""

    def _actualizar_reacciones(self, account_id: int, message_id: str, reactor: str,
                               emojis) -> bool:
        """Actualiza el mapa de reacciones {reactor: [emojis]} de un mensaje y recalcula el recuento
        legible ('👍 2, ❤'). Las reacciones de XMPP (0444) sustituyen el CONJUNTO por usuario."""
        import json
        from collections import Counter
        fila = db.query_one(
            "SELECT id, reacciones_json, chat_jid FROM xmpp_mensajes "
            "WHERE account_id = ? AND (stanza_id = ? OR origin_id = ?)",
            (account_id, message_id, message_id))
        if not fila:
            return False
        mid, raw, chat_jid = fila
        try:
            mapa = json.loads(raw or "{}")
        except Exception:
            mapa = {}
        lista = [e for e in (emojis or []) if e]
        if lista:
            mapa[reactor] = lista
        else:
            mapa.pop(reactor, None)
        cnt = Counter()
        for vals in mapa.values():
            for e in vals:
                cnt[e] += 1
        legible = ", ".join(f"{e} {n}" if n > 1 else e for e, n in cnt.items())
        db.execute("UPDATE xmpp_mensajes SET reacciones_json = ?, reacciones = ? WHERE id = ?",
                   (json.dumps(mapa, ensure_ascii=False), legible, mid))
        self._emit(account_id, "mensaje", chat_jid=chat_jid, propio=False, cuenta=False)
        return True

    def _reindexar_fts(self, account_id: int, ref: str, texto: str):
        """Actualiza el índice de búsqueda (FTS) de un mensaje tras corregirlo (por origin/stanza id).
        Sin esto, la búsqueda seguiría encontrando el texto ANTIGUO."""
        try:
            fila = db.query_one(
                "SELECT id, COALESCE(NULLIF(sender_nombre,''), sender) FROM xmpp_mensajes "
                "WHERE account_id = ? AND (origin_id = ? OR stanza_id = ?)",
                (account_id, ref, ref))
            if fila:
                db.execute("DELETE FROM xmpp_mensajes_fts WHERE msg_db_id = ?", (fila[0],))
                if (texto or "").strip():
                    db.execute("INSERT INTO xmpp_mensajes_fts (msg_db_id, sender, texto) VALUES (?, ?, ?)",
                               (fila[0], fila[1] or "", texto or ""))
        except Exception as e:
            log.debug(f"XMPP: no se pudo reindexar FTS: {e}")

    def _guardar_pep(self, account_id: int, msg, tipo: str):
        """Guarda en palabras el ánimo/actividad/canción que publica un contacto por PEP (0107/0108/
        0118) en xmpp_chats.pep_estado, para verlo en su ficha. Silencioso, nunca mueve el foco."""
        try:
            desde = jid_bare(str(msg["from"]))
            if not desde:
                return
            item = msg["pubsub_event"]["items"]["item"]
            elem = item.xml if item is not None else None
            texto = self._pep_a_palabras(elem, tipo)
            fila_id = db_id_chat(account_id, desde)
            if db.query_one("SELECT 1 FROM xmpp_chats WHERE id = ?", (fila_id,)):
                db.execute("UPDATE xmpp_chats SET pep_estado = ? WHERE id = ?", (texto, fila_id))
                self._emit(account_id, "pep", jid=desde, estado=texto)
        except Exception as e:
            log.debug(f"XMPP: PEP no procesado: {e}")

    @staticmethod
    def _pep_a_palabras(elem, tipo: str) -> str:
        """Extrae del XML del evento PEP un texto legible ('Feliz: genial', 'Trabajando', 'Escuchando
        X de Y'). Cadena vacía si el contacto retiró su estado."""
        if elem is None:
            return ""
        from app.services.xmpp import xmpp_perfil as _p
        try:
            if tipo == "mood":
                nodo = elem.find("{http://jabber.org/protocol/mood}mood")
                if nodo is None:
                    return ""
                valor = next((c.tag.rsplit("}", 1)[-1] for c in nodo
                              if not c.tag.endswith("}text")), "")
                texto = (nodo.findtext("{http://jabber.org/protocol/mood}text") or "").strip()
                etq = _p._MOOD_MAP.get(valor, valor)
                return (etq + (": " + texto if texto else "")) if etq else texto
            if tipo == "activity":
                nodo = elem.find("{http://jabber.org/protocol/activity}activity")
                if nodo is None:
                    return ""
                general = next((c.tag.rsplit("}", 1)[-1] for c in nodo
                               if not c.tag.endswith("}text")), "")
                texto = (nodo.findtext("{http://jabber.org/protocol/activity}text") or "").strip()
                etq = _p._ACT_MAP.get(general, general)
                return (etq + (": " + texto if texto else "")) if etq else texto
            if tipo == "tune":
                nodo = elem.find("{http://jabber.org/protocol/tune}tune")
                if nodo is None:
                    return ""
                ns = "{http://jabber.org/protocol/tune}"
                titulo = (nodo.findtext(ns + "title") or "").strip()
                artista = (nodo.findtext(ns + "artist") or "").strip()
                if titulo and artista:
                    return _("Escuchando {0} de {1}").format(titulo, artista)
                return _("Escuchando {0}").format(titulo or artista) if (titulo or artista) else ""
        except Exception:
            return ""
        return ""

    def _nombre_chat(self, account_id: int, jid: str) -> str:
        fila = db.query_one("SELECT nombre FROM xmpp_chats WHERE id = ?",
                            (db_id_chat(account_id, jid),))
        return (fila[0] if fila and fila[0] else nombre_desde_jid(jid))

    def _guardar_mensaje(self, account_id: int, chat_jid: str, stanza_id: str, sender: str,
                         sender_nombre: str, texto: str, tipo: str, fecha: str,
                         propio: bool, leido: bool, contar: bool, media_url: str = "",
                         reply_to: str = "", origin_id: str = "", highlight: bool = False,
                         cifrado: bool = False, es_voz: bool = False, duracion: float = 0.0) -> bool:
        """Guarda un mensaje con DEDUPE por (account_id, stanza_id) UNIQUE. Devuelve True si era
        nuevo. INSERT OR IGNORE: el duplicado (Carbons + MAM + eco) es lo NORMAL en XMPP, no un
        error, así que no ensucia el log."""
        try:
            filas = db.execute("""
                INSERT OR IGNORE INTO xmpp_mensajes (account_id, chat_jid, stanza_id, origin_id,
                    sender, sender_nombre, texto, tipo, media_url, date, es_propio, leido, reply_to,
                    highlight, cifrado, es_voz, duracion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (account_id, chat_jid, stanza_id, origin_id or "", sender, sender_nombre, texto,
                  tipo, media_url or "", fecha, 1 if propio else 0, 1 if leido else 0, reply_to or "",
                  1 if highlight else 0, 1 if cifrado else 0, 1 if es_voz else 0, float(duracion or 0)))
        except Exception:
            return False
        if not filas:
            return False  # ya existía (dedupe silencioso)
        # Índice FTS (solo texto real).
        if tipo in ("msg", "accion", "aviso") and (texto or "").strip():
            try:
                fila_id = db.query_one(
                    "SELECT id FROM xmpp_mensajes WHERE account_id = ? AND stanza_id = ?",
                    (account_id, stanza_id))
                if fila_id:
                    db.execute("INSERT INTO xmpp_mensajes_fts (msg_db_id, sender, texto) VALUES (?, ?, ?)",
                               (fila_id[0], sender_nombre or sender or "", texto or ""))
            except Exception:
                pass
        # Resumen del chat (último mensaje / no leídos).
        resumen = f"{sender_nombre or sender}: {texto}"[:200] if not propio else texto[:200]
        fila_id = db_id_chat(account_id, chat_jid)
        existe = db.query_one("SELECT 1 FROM xmpp_chats WHERE id = ?", (fila_id,))
        if not existe:
            db.execute("""INSERT INTO xmpp_chats (id, account_id, jid, tipo, nombre)
                          VALUES (?, ?, ?, 'contacto', ?)""",
                       (fila_id, account_id, chat_jid, self._nombre_chat(account_id, chat_jid)))
        db.execute("""UPDATE xmpp_chats SET last_message = ?, last_message_date = ?,
                      unread_count = unread_count + ? WHERE id = ?""",
                   (resumen, fecha, 1 if contar else 0, fila_id))
        self._emit(account_id, "mensaje", chat_jid=chat_jid, propio=propio, cuenta=not propio)
        return True
