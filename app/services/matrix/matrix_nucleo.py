# -*- coding: utf-8 -*-
"""Bucle asyncio del servicio Matrix, cliente nio y sincronización en tiempo real.

Mixin de MatrixService (patrón telegram_nucleo/irc_nucleo, tercera iteración): un hilo
dedicado "MatrixLoop" con su event loop; la UI le habla con run_coroutine_threadsafe a
través de _run(). El /sync de Matrix es long-polling (timeout 30 s), así que el tiempo
real sale gratis: cada respuesta del sync entrega lo nuevo al instante.
"""
from app.services.matrix.matrix_common import (
    asyncio, threading, Any, Dict, Optional, db, event_bus, log,
    HAS_NIO, MatrixError, ahora_iso, ts_a_iso, db_id_sala)

_dns_parcheado = False

# Filtro de sincronización (MX-009): acota cuántos eventos de timeline entrega el servidor por sala y
# por sync, para no descargar de golpe cientos de mensajes en cuentas grandes (el historial completo
# se trae bajo demanda al paginar). DELIBERADAMENTE no activa lazy_load_members: nio necesita los
# miembros para calcular el nombre de las salas sin m.room.name y, con carga perezosa, reaparecería el
# fallo "Empty Room". Tampoco filtra account_data (m.direct / ignorados deben llegar completos).
_SYNC_FILTER = {"room": {"timeline": {"limit": 50}}}


def _forzar_resolver_dns_os():
    """Fuerza a aiohttp (la librería HTTP de nio) a resolver DNS con el resolutor del SISTEMA
    operativo (getaddrinfo, hilo) en vez de con aiodns/c-ares.

    Desde aiohttp 3.10 el resolutor por defecto es AsyncResolver (aiodns/c-ares) cuando aiodns
    está instalado. En algunos equipos c-ares NO consigue leer los servidores DNS configurados
    (ciertas VPN, redes corporativas, IPv6, o configuraciones del registro de Windows) y lanza
    'aiodns.error.DNSError: (11, Could not contact DNS servers'), que impedía conectar a Matrix a
    esos usuarios aunque el resto de internet les funcionara. ThreadedResolver usa la resolución
    del propio Windows (la misma que el navegador), que sí funciona en esos casos. nio crea su
    ClientSession de forma perezosa con connector=None, así que basta cambiar el resolutor por
    defecto ANTES de esa primera petición. Idempotente y silencioso si aiohttp cambia por dentro."""
    global _dns_parcheado
    if _dns_parcheado:
        return
    try:
        import aiohttp.connector
        from aiohttp.resolver import ThreadedResolver
        # Solo si el actual es el asíncrono (c-ares): no pisar una elección explícita distinta.
        if aiohttp.connector.DefaultResolver.__name__ != "ThreadedResolver":
            aiohttp.connector.DefaultResolver = ThreadedResolver
            log.info("Matrix: resolutor DNS de aiohttp cambiado a ThreadedResolver (getaddrinfo del "
                     "sistema) para evitar fallos de aiodns/c-ares.")
        _dns_parcheado = True
    except Exception as e:
        log.debug(f"Matrix: no se pudo forzar el resolutor DNS del sistema: {e}")


class _NucleoMixin:
    # --- GESTIÓN DEL BUCLE ASYNCIO ---
    def _ensure_loop(self):
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="MatrixLoop", daemon=True
            )
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
        """Ejecuta una corrutina en el bucle de Matrix y espera su resultado (bloqueante).
        Llamar SIEMPRE desde un hilo de task_manager, nunca desde el de la interfaz."""
        if not HAS_NIO:
            raise MatrixError("Las dependencias de Matrix (matrix-nio) no están instaladas. "
                              "Ejecute la instalación de requisitos desde build.bat.")
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except MatrixError:
            raise
        except TimeoutError:
            future.cancel()
            raise MatrixError("La operación de Matrix tardó demasiado. Revise la conexión.")
        except Exception as e:
            raise MatrixError(self._mensaje_error_matrix(e))

    @staticmethod
    def _mensaje_error_matrix(e: Exception) -> str:
        texto = str(e) or type(e).__name__
        bajo = texto.lower()
        if "m_forbidden" in bajo or "invalid password" in bajo or "forbidden" in bajo:
            return "Usuario o contraseña incorrectos (o acceso denegado por el servidor)."
        if "m_user_deactivated" in bajo:
            return "Esta cuenta está desactivada en el servidor."
        if "m_limit_exceeded" in bajo or "429" in bajo:
            return "El servidor limita las peticiones; espera un momento y reintenta."
        if "getaddrinfo" in bajo or "cannot connect" in bajo or "nodename" in bajo:
            return "No se encontró el servidor. Revise la dirección del homeserver."
        if "certificate" in bajo or "ssl" in bajo:
            return f"Fallo de seguridad TLS al conectar. Detalle: {texto}"
        if "timed out" in bajo or "timeout" in bajo:
            return "El servidor no respondió (tiempo agotado). Revise la conexión."
        return texto

    # ---------------- Cliente nio ----------------
    def _crear_cliente(self, homeserver: str, user: str = "", device_id: str = "",
                       account_id: int = None):
        """Crea el AsyncClient de nio (importándolo de forma diferida). Con account_id y
        device_id se activa el CIFRADO con store de claves persistente por cuenta (protegido
        con una clave guardada en security); sin ellos (login inicial), cliente efímero."""
        from nio import AsyncClient, AsyncClientConfig
        from nio.store import SqliteStore
        # Antes de que nio cree su sesión aiohttp: usar el resolutor DNS del sistema (evita el
        # 'Could not contact DNS servers' de aiodns/c-ares en algunos equipos).
        _forzar_resolver_dns_os()
        if account_id and device_id and self.cifrado_disponible():
            # store_name FIJO: sin él, nio nombra el archivo con el user_id (que lleva ':')
            # y en NTFS eso crea un Alternate Data Stream invisible que se PIERDE al copiar
            # la carpeta o hacer una copia de seguridad. Con nombre propio, el store es un
            # .db normal que viaja con data/.
            #
            # store=SqliteStore OBLIGATORIO en Windows: el store por defecto (DefaultStore)
            # guarda el estado de confianza de dispositivos en TRES ficheros de texto cuyo nombre
            # es "{user_id}_{device_id}.trusted_devices" (etc.). Como el user_id lleva ':'
            # (@usuario:servidor), Windows RECHAZA ese nombre de fichero: al enviar en una sala
            # cifrada, nio comparte la sesión de grupo y escribe ese fichero con atomicwrites, que
            # hace MoveFileExW a una ruta con ':' y falla con [WinError 123]. Resultado: NO se podía
            # enviar ningún mensaje en salas cifradas. SqliteStore guarda ese mismo estado dentro de
            # la BD SQLite (tabla DeviceTrustState), sin ficheros con ':'. Comparte las tablas de
            # claves ya existentes, así que las claves guardadas no se pierden.
            cfg = AsyncClientConfig(max_timeouts=0, encryption_enabled=True,
                                    pickle_key=self._pickle_key(account_id),
                                    store_name="claves_e2ee.db", store=SqliteStore)
            return AsyncClient(homeserver, user, device_id=device_id,
                               store_path=self._store_dir(account_id), config=cfg)
        cfg = AsyncClientConfig(max_timeouts=0, encryption_enabled=False)
        return AsyncClient(homeserver, user, config=cfg)

    def _registrar_callbacks(self, account_id: int, cliente):
        """Callbacks de nio → BD + event_bus (patrón IRC). Se registran una vez por cliente."""
        from nio import (RoomMessageText, RoomMessageNotice, RoomMessageEmote, SyncResponse,
                         RoomMessageMedia, RoomEncryptedMedia, RedactionEvent, UnknownEvent)

        async def _cb_texto(room, event):
            # Una EDICIÓN (m.replace) actualiza el mensaje original en vez de insertar uno nuevo.
            rel = ((getattr(event, "source", {}) or {}).get("content", {}) or {}).get("m.relates_to") or {}
            if rel.get("rel_type") == "m.replace" and rel.get("event_id"):
                nuevo = ((getattr(event, "source", {}) or {}).get("content", {}) or {}) \
                    .get("m.new_content", {}).get("body") or getattr(event, "body", "")
                self._aplicar_edicion(account_id, rel["event_id"], nuevo or "")
                self._emit(account_id, "mensaje", room_id=room.room_id, texto=nuevo,
                           propio=False, inicial=self._sync_inicial.get(account_id, True))
                return
            self._al_recibir_mensaje(account_id, room, event, "msg")

        async def _cb_notice(room, event):
            self._al_recibir_mensaje(account_id, room, event, "aviso")

        async def _cb_emote(room, event):
            self._al_recibir_mensaje(account_id, room, event, "accion")

        async def _cb_media(room, event):
            self._al_recibir_media(account_id, room, event)

        async def _cb_redaccion(room, event):
            try:
                objetivo = getattr(event, "redacts", "") or ""
                if objetivo:
                    self._marcar_redactado(account_id, objetivo)
                    self._emit(account_id, "mensaje", room_id=room.room_id, texto="",
                               propio=False, inicial=self._sync_inicial.get(account_id, True))
            except Exception as e:
                log.debug(f"Matrix: redacción no aplicada: {e}")

        async def _cb_megolm(room, event):
            # Mensaje CIFRADO que no se pudo descifrar (sin la clave de esa sesión). Se guarda
            # una fila explicativa (dedupe por event_id): nunca un silencio inexplicable.
            try:
                inicial = self._sync_inicial.get(account_id, True)
                # tipo="cifrado": marca fiable para localizar luego estas filas y RE-DESCIFRARLAS
                # cuando lleguen las claves (import de claves / verificación). El render las trata
                # como texto normal (muestra este aviso) hasta que se descifran.
                self._guardar_mensaje(
                    account_id, room.room_id, "cifrado",
                    sender=getattr(event, "sender", "") or "",
                    sender_nombre=room.user_name(getattr(event, "sender", "")) or "",
                    texto=_("[Mensaje cifrado que no se pudo descifrar (faltan las claves; "
                            "impórtalas desde el menú Matrix o verifica esta sesión)]"),
                    event_id=getattr(event, "event_id", "") or "",
                    fecha=ts_a_iso(getattr(event, "server_timestamp", 0)),
                    propio=False, leido=inicial, contar=not inicial)
                self._emit(account_id, "mensaje", room_id=room.room_id, texto="",
                           propio=False, inicial=inicial)
            except Exception as e:
                log.debug(f"Matrix: megolm no registrado: {e}")

        async def _cb_desconocido(room, event):
            # Eventos que nio no tipa: reacciones (m.reaction) y encuestas (MSC3381, Element).
            try:
                tipo_ev = getattr(event, "type", "") or ""
                contenido = ((getattr(event, "source", {}) or {}).get("content", {}) or {})
                rel = contenido.get("m.relates_to") or {}
                inicial = self._sync_inicial.get(account_id, True)
                if tipo_ev == "m.reaction":
                    if rel.get("rel_type") == "m.annotation" and rel.get("event_id") and rel.get("key"):
                        # Registrar como relación deduplicable (MX-012): el eco de la propia y un
                        # replay del mismo sync se ignoran por event_id; el agregado se recalcula.
                        nuevo = self._registrar_relacion(
                            account_id, getattr(event, "event_id", "") or "", rel["event_id"],
                            "m.annotation", rel["key"], getattr(event, "sender", "") or "",
                            getattr(event, "server_timestamp", 0))
                        if nuevo:
                            self._recalcular_reacciones(account_id, rel["event_id"])
                            self._emit(account_id, "mensaje", room_id=room.room_id, texto="",
                                       propio=False, inicial=inicial)
                    return
                if tipo_ev == self._POLL_START:
                    if self._es_ignorado(account_id, getattr(event, "sender", "") or ""):
                        return
                    inicio = contenido.get(self._POLL_START) or {}
                    pregunta = ((inicio.get("question") or {}).get("org.matrix.msc1767.text")
                                or "") if isinstance(inicio.get("question"), dict) else ""
                    opciones = {a.get("id"): a.get("org.matrix.msc1767.text", "")
                                for a in (inicio.get("answers") or []) if a.get("id")}
                    if not pregunta or not opciones:
                        return
                    import json as _j
                    # kind undisclosed = encuesta OCULTA: no mostrar conteos hasta cerrar (MX-012).
                    oculta = "undisclosed" in str(inicio.get("kind") or "").lower()
                    poll = {"pregunta": pregunta, "opciones": opciones, "votos": {},
                            "cerrada": False, "oculta": oculta}
                    propio = (getattr(event, "sender", "") == self._user_id(account_id))
                    nuevo = self._guardar_mensaje(
                        account_id, room.room_id, "encuesta",
                        sender=getattr(event, "sender", "") or "",
                        sender_nombre=room.user_name(getattr(event, "sender", "")) or "",
                        texto=self._texto_encuesta(poll),
                        event_id=getattr(event, "event_id", "") or "",
                        fecha=ts_a_iso(getattr(event, "server_timestamp", 0)),
                        propio=propio, leido=propio or inicial,
                        contar=not (propio or inicial), poll_json=_j.dumps(poll))
                    if nuevo:
                        self._emit(account_id, "mensaje", room_id=room.room_id,
                                   texto=pregunta, propio=propio, inicial=inicial)
                    return
                if tipo_ev == self._POLL_RESPONSE:
                    if rel.get("rel_type") == "m.reference" and rel.get("event_id"):
                        quien = getattr(event, "sender", "") or ""
                        respuestas = (contenido.get(self._POLL_RESPONSE) or {}).get("answers") or []
                        import json as _j
                        # Voto como relación deduplicable (MX-012): dedupe por event_id (incluye el
                        # eco del propio) y recálculo del último voto por votante.
                        nuevo = self._registrar_relacion(
                            account_id, getattr(event, "event_id", "") or "", rel["event_id"],
                            "m.reference", _j.dumps(respuestas), quien,
                            getattr(event, "server_timestamp", 0))
                        if nuevo:
                            self._recalcular_votos(account_id, rel["event_id"])
                            self._emit(account_id, "mensaje", room_id=room.room_id, texto="",
                                       propio=False, inicial=inicial)
                    return
                if tipo_ev == self._POLL_END:
                    if rel.get("rel_type") == "m.reference" and rel.get("event_id"):
                        self._cerrar_encuesta_local(account_id, rel["event_id"])
                        self._emit(account_id, "mensaje", room_id=room.room_id, texto="",
                                   propio=False, inicial=inicial)
                    return
            except Exception as e:
                log.debug(f"Matrix: evento desconocido no aplicado: {e}")

        async def _cb_sticker(room, event):
            # Stickers (m.sticker): se tratan como una imagen con su descripción.
            try:
                if self._es_ignorado(account_id, getattr(event, "sender", "") or ""):
                    return
                propio = (getattr(event, "sender", "") == self._user_id(account_id))
                inicial = self._sync_inicial.get(account_id, True)
                nombre = getattr(event, "body", "") or _("sticker")
                nuevo = self._guardar_mensaje(
                    account_id, room.room_id, "media",
                    sender=getattr(event, "sender", "") or "",
                    sender_nombre=room.user_name(getattr(event, "sender", "")) or "",
                    texto=_("[Sticker: {0}]").format(nombre),
                    event_id=getattr(event, "event_id", "") or "",
                    fecha=ts_a_iso(getattr(event, "server_timestamp", 0)),
                    propio=propio, leido=propio or inicial, contar=not (propio or inicial),
                    media_mxc=getattr(event, "url", "") or "")
                if nuevo:
                    self._emit(account_id, "mensaje", room_id=room.room_id, texto=nombre,
                               propio=propio, inicial=inicial)
            except Exception as e:
                log.debug(f"Matrix: sticker no guardado: {e}")

        async def _cb_msg_desconocido(room, event):
            # msgtypes sin clase en nio: la UBICACIÓN (m.location) se muestra con su geo URI
            # (el menú contextual la abre en el mapa); el resto, con su body y una nota.
            try:
                if self._es_ignorado(account_id, getattr(event, "sender", "") or ""):
                    return
                contenido = ((getattr(event, "source", {}) or {}).get("content", {}) or {})
                msgtype = contenido.get("msgtype") or ""
                cuerpo = getattr(event, "body", "") or contenido.get("body") or ""
                if msgtype == "m.location":
                    geo = contenido.get("geo_uri") or ""
                    texto = _("[Ubicación: {0}] {1}").format(cuerpo or _("Ubicación"), geo).strip()
                elif cuerpo:
                    texto = cuerpo
                else:
                    return
                propio = (getattr(event, "sender", "") == self._user_id(account_id))
                inicial = self._sync_inicial.get(account_id, True)
                nuevo = self._guardar_mensaje(
                    account_id, room.room_id, "msg",
                    sender=getattr(event, "sender", "") or "",
                    sender_nombre=room.user_name(getattr(event, "sender", "")) or "",
                    texto=texto, event_id=getattr(event, "event_id", "") or "",
                    fecha=ts_a_iso(getattr(event, "server_timestamp", 0)),
                    propio=propio, leido=propio or inicial, contar=not (propio or inicial))
                if nuevo:
                    self._emit(account_id, "mensaje", room_id=room.room_id, texto=texto,
                               propio=propio, inicial=inicial)
            except Exception as e:
                log.debug(f"Matrix: msgtype desconocido no guardado: {e}")

        async def _cb_sync(response):
            self._tras_sync(account_id, response)

        cliente.add_event_callback(_cb_texto, RoomMessageText)
        cliente.add_event_callback(_cb_notice, RoomMessageNotice)
        cliente.add_event_callback(_cb_emote, RoomMessageEmote)
        cliente.add_event_callback(_cb_media, RoomMessageMedia)
        # Adjuntos CIFRADOS (salas E2EE): RoomEncryptedMedia es una clase HERMANA de
        # RoomMessageMedia (no subclase), así que sin este segundo registro los adjuntos de las
        # salas cifradas no llegaban a la app. Mismo callback; el crypto se guarda para descifrar.
        cliente.add_event_callback(_cb_media, RoomEncryptedMedia)
        # M7: stickers, msgtypes sin clase (ubicación) y account data (etiquetas/ignorados).
        try:
            from nio import StickerEvent, RoomMessageUnknown
            cliente.add_event_callback(_cb_sticker, StickerEvent)
            cliente.add_event_callback(_cb_msg_desconocido, RoomMessageUnknown)
        except Exception as e:
            log.debug(f"Matrix: callbacks de sticker/ubicación no registrados: {e}")
        try:
            from nio import TagEvent, UnknownAccountDataEvent

            async def _cb_tag(room, event):
                try:
                    self._aplicar_tag_evento(account_id, room.room_id,
                                             getattr(event, "tags", {}) or {})
                except Exception:
                    pass

            async def _cb_account_data(event):
                try:
                    if getattr(event, "type", "") == "m.ignored_user_list":
                        self._aplicar_ignorados_evento(
                            account_id, getattr(event, "content", {}) or {})
                except Exception:
                    pass

            cliente.add_room_account_data_callback(_cb_tag, TagEvent)
            cliente.add_global_account_data_callback(_cb_account_data, UnknownAccountDataEvent)
        except Exception as e:
            log.debug(f"Matrix: callbacks de account data no registrados: {e}")
        cliente.add_event_callback(_cb_redaccion, RedactionEvent)
        cliente.add_event_callback(_cb_desconocido, UnknownEvent)
        cliente.add_response_callback(_cb_sync, SyncResponse)
        # Mensajes cifrados no descifrables + verificación por emojis (si el cifrado está).
        try:
            from nio import MegolmEvent
            cliente.add_event_callback(_cb_megolm, MegolmEvent)
        except Exception:
            pass
        try:
            self._registrar_verificacion(account_id, cliente)
        except Exception as e:
            log.debug(f"Matrix: verificación no registrada: {e}")

        # Cuando LLEGA una clave de sala (m.room_key / m.forwarded_room_key), reintentar descifrar
        # los mensajes de ESA sala que estaban pendientes. Así, tras verificar la sesión desde
        # Element (que reenvía las claves) o tras un key request, los "[Mensaje cifrado...]" pasan a
        # texto SOLOS, sin que el usuario tenga que hacer nada. Se salta el PRIMER sync (llegan
        # muchas claves de golpe) y se evita solapar barridos de la misma sala.
        try:
            from nio import RoomKeyEvent, ForwardedRoomKeyEvent

            async def _cb_roomkey(event):
                try:
                    if self._sync_inicial.get(account_id, True):
                        return
                    rid = getattr(event, "room_id", "") or ""
                    if not rid:
                        return
                    en_curso = getattr(self, "_redescifrando_salas", None)
                    if en_curso is None:
                        en_curso = self._redescifrando_salas = set()
                    clave = (account_id, rid)
                    if clave in en_curso:
                        return
                    en_curso.add(clave)
                    try:
                        _rec, salas, _sol = await self._redescifrar_async(account_id, cliente, rid)
                    finally:
                        en_curso.discard(clave)
                    for r in salas:
                        self._emit(account_id, "mensaje", room_id=r, texto="",
                                   propio=False, inicial=False)
                except Exception as e:
                    log.debug(f"Matrix: re-descifrado en vivo no aplicado: {e}")

            cliente.add_to_device_callback(_cb_roomkey, (RoomKeyEvent, ForwardedRoomKeyEvent))
        except Exception as e:
            log.debug(f"Matrix: callback de clave de sala no registrado: {e}")

        # "Está escribiendo" de otros (evento efímero del sync) → la conversación lo muestra.
        try:
            from nio import TypingNoticeEvent

            async def _cb_typing(room, event):
                try:
                    yo = self._user_id(account_id)
                    nombres = []
                    for uid in (getattr(event, "users", None) or []):
                        if uid == yo:
                            continue
                        nombres.append(room.user_name(uid) or uid)
                    self._emit(account_id, "typing", room_id=room.room_id, nombres=nombres)
                except Exception:
                    pass

            cliente.add_ephemeral_callback(_cb_typing, TypingNoticeEvent)
        except Exception as e:
            log.debug(f"Matrix: typing no disponible: {e}")

        # Recibos de lectura (m.receipt): guardamos hasta qué mensaje ha leído cada usuario, para
        # poder decir en la conversación "leído por X". Es un evento efímero del sync.
        try:
            from nio import ReceiptEvent

            async def _cb_recibo(room, event):
                try:
                    mapa = self._recibos.setdefault((account_id, room.room_id), {})
                    for r in (getattr(event, "receipts", None) or []):
                        # Solo recibos de LECTURA (m.read / m.read.private); receipt_type es un enum
                        # ReceiptType, así que comparamos por su .value. Guardamos el último por
                        # usuario con su marca de tiempo (ms) para ordenar frente a los mensajes.
                        rt = getattr(r, "receipt_type", None)
                        rt = getattr(rt, "value", rt)
                        if rt not in ("m.read", "m.read.private"):
                            continue
                        uid = getattr(r, "user_id", "") or ""
                        if uid:
                            mapa[uid] = (getattr(r, "event_id", "") or "",
                                         int(getattr(r, "timestamp", 0) or 0))
                    self._emit(account_id, "recibo_lectura", room_id=room.room_id)
                except Exception:
                    pass

            cliente.add_ephemeral_callback(_cb_recibo, ReceiptEvent)
        except Exception as e:
            log.debug(f"Matrix: recibos de lectura no disponibles: {e}")

    def _emit(self, account_id: int, tipo: str, **datos):
        try:
            datos.update({"account_id": account_id, "tipo": tipo})
            event_bus.emit("matrix_evento", datos)
        except Exception as e:
            log.debug(f"Matrix: error emitiendo evento {tipo}: {e}")

    # ---------------- Recepción ----------------
    @staticmethod
    def _thread_root_de(event) -> str:
        """event_id raíz si el evento pertenece a un hilo (m.relates_to rel_type m.thread)."""
        try:
            rel = ((getattr(event, "source", {}) or {}).get("content", {}) or {}) \
                .get("m.relates_to") or {}
            if rel.get("rel_type") == "m.thread":
                return rel.get("event_id") or ""
        except Exception:
            pass
        return ""

    def _al_recibir_mensaje(self, account_id: int, room, event, tipo: str):
        """(Hilo MatrixLoop) Un mensaje de texto/aviso/acción llegó por el sync. Se guarda con
        dedupe por event_id; durante el PRIMER sync de la sesión entra como leído y sin sonido
        (es relleno de historial, no novedad)."""
        try:
            # Usuarios ignorados (m.ignored_user_list): sus mensajes no entran, como en Element.
            if self._es_ignorado(account_id, getattr(event, "sender", "") or ""):
                return
            propio = (getattr(event, "sender", "") == self._user_id(account_id))
            inicial = self._sync_inicial.get(account_id, True)
            texto = getattr(event, "body", "") or ""
            nuevo = self._guardar_mensaje(
                account_id, room.room_id, tipo,
                sender=getattr(event, "sender", "") or "",
                sender_nombre=room.user_name(getattr(event, "sender", "")) or "",
                texto=texto,
                event_id=getattr(event, "event_id", "") or "",
                fecha=ts_a_iso(getattr(event, "server_timestamp", 0)),
                propio=propio,
                leido=propio or inicial,
                contar=not (propio or inicial),
                thread_root=self._thread_root_de(event),
            )
            if not nuevo:
                return
            if not inicial and not propio:
                # Sonido: DM > mención > sala normal, respetando el nivel de aviso por sala.
                try:
                    es_dm = self._es_dm(account_id, room.room_id)
                    highlight = self._me_mencionan(account_id, event, texto)
                    if es_dm:
                        event_bus.emit("sound_trigger", "matrix_dm")
                    else:
                        nivel = self.nivel_aviso(account_id, room.room_id)
                        if highlight and nivel in ("all", "mentions"):
                            event_bus.emit("sound_trigger", "matrix_highlight")
                        elif nivel == "all":
                            event_bus.emit("sound_trigger", "matrix_new_message")
                except Exception:
                    pass
            self._emit(account_id, "mensaje", room_id=room.room_id,
                       texto=texto, propio=propio, inicial=inicial)
        except Exception as e:
            log.error(f"Matrix: error procesando mensaje entrante: {e}")

    def _al_recibir_media(self, account_id: int, room, event):
        """(Hilo MatrixLoop) Un adjunto llegó por el sync: se guarda con su etiqueta legible
        y el mxc para descargarlo bajo demanda."""
        try:
            if self._es_ignorado(account_id, getattr(event, "sender", "") or ""):
                return
            propio = (getattr(event, "sender", "") == self._user_id(account_id))
            inicial = self._sync_inicial.get(account_id, True)
            etiqueta = self._etiqueta_media(event)
            from app.services.matrix.matrix_medios import crypto_de_media
            nuevo = self._guardar_mensaje(
                account_id, room.room_id, "media",
                sender=getattr(event, "sender", "") or "",
                sender_nombre=room.user_name(getattr(event, "sender", "")) or "",
                texto=etiqueta, event_id=getattr(event, "event_id", "") or "",
                fecha=ts_a_iso(getattr(event, "server_timestamp", 0)),
                propio=propio, leido=propio or inicial, contar=not (propio or inicial),
                media_mxc=getattr(event, "url", "") or "", media_crypto=crypto_de_media(event))
            if not nuevo:
                return
            if not inicial and not propio:
                try:
                    event_bus.emit("sound_trigger",
                                   "matrix_dm" if self._es_dm(account_id, room.room_id)
                                   else "matrix_new_message")
                except Exception:
                    pass
            self._emit(account_id, "mensaje", room_id=room.room_id, texto=etiqueta,
                       propio=propio, inicial=inicial)
        except Exception as e:
            log.error(f"Matrix: error procesando adjunto entrante: {e}")

    def _tras_sync(self, account_id: int, response):
        """(Hilo MatrixLoop) Tras CADA respuesta del sync: refrescar el resumen de salas,
        registrar invitaciones y persistir next_batch. UNIDAD DE TRABAJO (MX-001): nio ya ejecutó
        los callbacks de evento de este lote ANTES de este callback de respuesta; si alguno falló al
        PERSISTIR (self._fallo_persistencia), NO se avanza next_batch, de modo que el servidor
        reenviará esos eventos y se reintenta (el dedupe por event_id los guarda una sola vez). Solo
        cuando el lote se guardó entero se confirma el token."""
        try:
            with self._lock:
                cliente = self._clients.get(account_id)
            if cliente is None:
                return
            # El flag lo pusieron los callbacks de evento de ESTE lote (corren antes que este). Se
            # lee y se limpia aquí para empezar el siguiente lote en limpio.
            lote_incompleto = bool(self._fallo_persistencia.pop(account_id, False))
            # Primer response (o primero tras una caída): estamos DENTRO.
            with self._estado_lock:
                estaba = bool(self._conectadas.get(account_id))
                reconexion = bool(self._hubo_caida.get(account_id))
            if not estaba:
                self._marcar_conectada(account_id, True)
                with self._estado_lock:
                    self._hubo_caida[account_id] = False
                self._emit(account_id, "conectado", reconexion=reconexion)
            # DMs reales: el mapa m.direct de account data (la spec: module dm.md).
            try:
                directos = set()
                for _u, salas in (cliente.direct_rooms or {}).items():
                    directos.update(salas or [])
                self._dms[account_id] = directos
            except Exception:
                pass
            # prev_batch por sala (MX-010): token REAL de la timeline de cada sala para paginar hacia
            # atrás (la API lo indica como punto de inicio de /messages, no el token global de sync).
            try:
                for room_id, info in (getattr(response.rooms, "join", {}) or {}).items():
                    pb = getattr(getattr(info, "timeline", None), "prev_batch", None)
                    if pb:
                        self._prev_batch[f"{account_id}|{room_id}"] = pb
            except Exception as e:
                log.debug(f"Matrix: no se pudo leer prev_batch del sync: {e}")
            self._guardar_salas(account_id, cliente)
            # MX-013: no-leídos y menciones del servidor, aplicados de forma SEGURA. El contador se
            # ajusta SOLO A LA BAJA (min con el valor del servidor): si has leído la sala en otro
            # dispositivo (Element…), aquí baja/se limpia; pero nunca RESUCITA no-leídos por el desfase
            # entre marcar leído local y que el servidor lo procese (respeta la actualización silenciosa).
            # La marca de mención (highlight) sí se toma tal cual del servidor (es su fuente de verdad).
            try:
                from app.services.matrix.matrix_common import db_id_sala as _dbid
                for room_id, info in (getattr(response.rooms, "join", {}) or {}).items():
                    un = getattr(info, "unread_notifications", None)
                    if un is None:
                        continue
                    notif = getattr(un, "notification_count", None)
                    high = getattr(un, "highlight_count", None)
                    if notif is not None:
                        db.execute("UPDATE matrix_salas SET unread_count = min(unread_count, ?) "
                                   "WHERE id = ?", (int(notif), _dbid(account_id, room_id)))
                    if high is not None:
                        db.execute("UPDATE matrix_salas SET highlight = ? WHERE id = ?",
                                   (1 if int(high) > 0 else 0, _dbid(account_id, room_id)))
            except Exception as e:
                log.debug(f"Matrix: no se pudieron aplicar no-leídos del servidor: {e}")
            # Invitaciones pendientes (rooms.invite del sync).
            try:
                for room_id, info in (getattr(response.rooms, "invite", {}) or {}).items():
                    self._guardar_invitacion(account_id, room_id, info)
            except Exception as e:
                log.debug(f"Matrix: error registrando invitaciones: {e}")
            # next_batch al final y SOLO si el lote se guardó entero (MX-001): si un evento no se pudo
            # persistir, se deja el token anterior para que el servidor reenvíe el lote al reconectar
            # o reiniciar. Dentro de la sesión, nio sigue avanzando su propio token en memoria; el
            # token de la BD (punto de recuperación) no se mueve hasta un lote correcto.
            if getattr(response, "next_batch", None) and not lote_incompleto:
                self._guardar_next_batch(account_id, response.next_batch)
            elif lote_incompleto:
                log.warning(f"Matrix: lote de sync con fallo de persistencia (cuenta {account_id}); "
                            "no se avanza next_batch para reintentarlo.")
            # El primer sync de la sesión terminó: lo siguiente ya es "en vivo".
            if self._sync_inicial.get(account_id, True):
                self._sync_inicial[account_id] = False
                self._emit(account_id, "sincronizada")
                # En 2.º plano, corregir las salas que se quedaron mostrando su room_id (nio sin
                # m.room.name): se pide el nombre al servidor. No en el bucle (usa self._run).
                try:
                    from app.core.task_manager import task_manager
                    task_manager.start_task(
                        lambda ctx, a=account_id: self.reparar_nombres_sala(a),
                        pass_context=True, unique_key=f"matrix:nombres:{account_id}")
                except Exception:
                    pass
            else:
                self._emit(account_id, "sync")
        except Exception as e:
            log.error(f"Matrix: error tras el sync: {e}")

    def _me_mencionan(self, account_id: int, event=None, texto: str = "") -> bool:
        """True si el evento me menciona. Prioriza m.mentions (MSC3952, menciones intencionadas,
        estable en la spec): la lista explícita de user_ids es autoritativa. Si no está, cae a una
        coincidencia por PALABRA COMPLETA del localpart o el user_id: así 'ana' ya NO salta por
        aparecer dentro de 'banana' (el falso positivo del matcher por subcadena anterior)."""
        try:
            yo = self._user_id(account_id) or ""
            if not yo:
                return False
            contenido = ((getattr(event, "source", {}) or {}).get("content", {}) or {}) \
                if event is not None else {}
            menciones = contenido.get("m.mentions")
            if isinstance(menciones, dict) and isinstance(menciones.get("user_ids"), list):
                return yo in menciones["user_ids"]  # autoritativo: solo si estoy en la lista
            import re
            localpart = yo.split(":")[0].lstrip("@")
            t = texto or (getattr(event, "body", "") if event is not None else "") or ""
            for aguja in filter(None, (localpart, yo)):
                # Límite de palabra a ambos lados (no letra/dígito/@/: pegado).
                if re.search(r"(?<![\w@:])" + re.escape(aguja) + r"(?![\w:])", t, re.IGNORECASE):
                    return True
            return False
        except Exception:
            return False

    # ---------------- Bucle de sincronización ----------------
    async def _bucle_sync(self, account_id: int, cliente, since: str):
        """sync_forever de nio con reintentos propios (backoff creciente, sin límite): si el
        servidor se cae, se avisa UNA vez y se sigue reintentando en segundo plano."""
        intento = 0
        primero = True
        while True:
            try:
                if primero:
                    # PRIMER sync tras (re)conectar con full_state=True: el sync incremental (since=
                    # token guardado) NO reenvía el estado antiguo de las salas, así que sin esto nio
                    # se queda sin el nombre (sala.name) ni los miembros y calcula "Empty Room"/0
                    # miembros. Con full_state repuebla nombre + miembros de todas las salas. Una sola
                    # vez; luego el bucle sigue en incremental (barato).
                    try:
                        await cliente.sync(timeout=30000, since=since or None, full_state=True,
                                           sync_filter=_SYNC_FILTER)
                        since = getattr(cliente, "next_batch", None) or since
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.debug(f"Matrix: sync inicial full_state falló (cuenta {account_id}): {e}")
                    primero = False
                await cliente.sync_forever(
                    timeout=30000,
                    since=since or None,
                    full_state=False,
                    loop_sleep_time=1000,
                    sync_filter=_SYNC_FILTER,
                )
                return  # solo sale si la tarea se cancela desde fuera
            except asyncio.CancelledError:
                raise
            except Exception as e:
                intento += 1
                if intento == 1:
                    self._marcar_conectada(account_id, False)
                    with self._estado_lock:
                        self._hubo_caida[account_id] = True
                    self._emit(account_id, "desconectado", esperado=False)
                # Token caducado a mitad de sesión (cuentas OAuth/MAS): renovarlo con el
                # refresh_token y aplicarlo al cliente para reconectar SIN intervención. La renovación
                # va en un ejecutor (urllib bloqueante) para no atascar el bucle. Solo si el error
                # parece de token, y como mucho una vez por corte.
                try:
                    m = str(e).lower()
                    if any(k in m for k in ("token", "401", "unauthorized", "soft_logout")):
                        nuevo = await asyncio.get_event_loop().run_in_executor(
                            None, self.refrescar_token_oauth, account_id)
                        if nuevo:
                            cliente.access_token = nuevo
                            log.info(f"Matrix: token renovado en caliente (cuenta {account_id}); "
                                     "reconectando.")
                            intento = 0
                            since = self._leer_next_batch(account_id)
                            primero = True
                            self._sync_inicial.setdefault(account_id, True)
                            continue
                except Exception:
                    pass
                espera = min(600, 5 * (2 ** min(intento - 1, 7)))
                log.warning(f"Matrix: sync caído (cuenta {account_id}), reintento en {espera}s: {e}")
                await asyncio.sleep(espera)
                since = self._leer_next_batch(account_id)
                primero = True  # al recuperar la conexión, repoblar de nuevo con full_state
                # Si reconecta, el propio _tras_sync volverá a marcar conectada (primer response).
                self._sync_inicial.setdefault(account_id, True)
