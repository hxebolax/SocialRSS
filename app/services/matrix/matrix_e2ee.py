# -*- coding: utf-8 -*-
"""Cifrado de extremo a extremo (E2EE) de Matrix: store de claves por cuenta, salas
cifradas, mensajes no descifrables, verificación por emojis e import/export de claves.

Mixin de MatrixService (tanda M4). Requiere python-olm (se compila UNA vez en la máquina
de build con tools/build_python_olm.bat; los usuarios reciben la DLL en la build).

LÍMITE CONOCIDO (documentado): matrix-nio no soporta cross-signing ni Secure Backup, así
que este dispositivo aparecerá como "sin verificar" para otros hasta que lo verifiques
por emojis, y el historial cifrado anterior al alta del dispositivo no se recupera (salvo
importando claves exportadas de otro cliente).
"""
import os
import uuid

from app.core.paths import get_data_path
from app.services.matrix.matrix_common import (
    asyncio, Any, Dict, Optional, List, db, security, log, MatrixError, db_id_sala)


class _E2EEMixin:
    # ---------------- Store de claves ----------------
    def _store_dir(self, account_id: int) -> str:
        ruta = get_data_path(f"sessions/matrix/{account_id}")
        ruta.mkdir(parents=True, exist_ok=True)
        return str(ruta)

    def _pickle_key(self, account_id: int) -> str:
        """Clave que protege el store de claves en disco (cifrada con la clave de máquina)."""
        clave = security.obtener_credencial("matrix", f"{account_id}:pickle")
        if not clave:
            clave = uuid.uuid4().hex + uuid.uuid4().hex
            security.guardar_credencial("matrix", f"{account_id}:pickle", clave)
        return clave

    def cifrado_disponible(self) -> bool:
        try:
            from nio.crypto import ENCRYPTION_ENABLED
            return bool(ENCRYPTION_ENABLED)
        except Exception:
            return False

    def es_sala_cifrada(self, account_id: int, room_id: str) -> bool:
        fila = db.query_one("SELECT COALESCE(cifrada,0) FROM matrix_salas WHERE id = ?",
                            (db_id_sala(account_id, room_id),))
        return bool(fila and fila[0])

    # ---------------- Export / import de claves de sala ----------------
    def exportar_claves(self, account_id: int, ruta: str, passphrase: str):
        """Exporta las claves de descifrado a un archivo protegido con contraseña (compatible
        con Element: Ajustes → Seguridad → Exportar claves)."""
        cliente = self._cliente_conectado(account_id)
        if not passphrase:
            raise MatrixError("Escribe una contraseña para proteger el archivo.")
        self._run(cliente.export_keys(ruta, passphrase), timeout=120.0)

    def importar_claves(self, account_id: int, ruta: str, passphrase: str) -> int:
        """Importa claves exportadas desde otro cliente (Element...): permite leer el
        historial cifrado de las salas cuyas claves vengan en el archivo. Tras importar, vuelve a
        descifrar los mensajes que quedaron como "[Mensaje cifrado...]" y devuelve cuántos se
        recuperaron (el fallo reportado era que seguían sin descifrarse aun tras importar)."""
        if not os.path.exists(ruta):
            raise MatrixError("El archivo de claves no existe.")
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.import_keys(ruta, passphrase), timeout=300.0)
        # Ahora que las claves están en el almacén, reintentar los mensajes pendientes.
        try:
            recuperados, _solicitadas = self.redescifrar_pendientes(account_id)
            return recuperados
        except Exception as e:
            log.debug(f"Matrix: re-descifrado tras importar no completado: {e}")
            return 0

    # ---------------- Re-descifrado de mensajes pendientes ----------------
    def _datos_evento_descifrado(self, event):
        """De un evento YA descifrado por nio devuelve (tipo, texto, media_mxc, media_crypto) para
        actualizar la fila que estaba como "[Mensaje cifrado...]". Reutiliza los ayudantes de medios
        para que un adjunto cifrado quede idéntico a como habría entrado descifrado desde el sync."""
        from nio import (RoomMessageText, RoomMessageNotice, RoomMessageEmote,
                         RoomMessageMedia, RoomEncryptedMedia)
        from app.services.matrix.matrix_medios import crypto_de_media
        if isinstance(event, (RoomMessageMedia, RoomEncryptedMedia)):
            return ("media", self._etiqueta_media(event),
                    getattr(event, "url", "") or "", crypto_de_media(event))
        if isinstance(event, RoomMessageNotice):
            return ("aviso", getattr(event, "body", "") or "", "", "")
        if isinstance(event, RoomMessageEmote):
            return ("accion", getattr(event, "body", "") or "", "", "")
        if isinstance(event, RoomMessageText):
            return ("msg", getattr(event, "body", "") or "", "", "")
        # Otros tipos con cuerpo (ubicación, etc.): al menos rescatar su texto.
        cuerpo = getattr(event, "body", "") or ""
        return ("msg", cuerpo, "", "") if cuerpo else (None, None, None, None)

    async def _redescifrar_async(self, account_id: int, cliente, room_id: str = ""):
        """Núcleo (en el bucle de Matrix) del re-descifrado. Para cada fila pendiente pide el evento
        con room_get_event: nio lo descifra AHORA que la clave está en el almacén (import de claves,
        verificación o key request) y, si lo consigue, ACTUALIZA la fila con el texto/adjunto real.
        Para los que AÚN no puede descifrar (falta esa sesión), envía una PETICIÓN DE CLAVE
        (m.room_key_request) a los demás dispositivos: si alguno la tiene y la comparte —muy
        habitual con TUS PROPIOS dispositivos en línea—, la clave llega por el sync y el enganche de
        RoomKeyEvent descifra el mensaje solo. Devuelve (recuperados, salas_tocadas, solicitadas).
        No emite eventos (lo hace quien llama)."""
        from nio import MegolmEvent
        from nio.responses import RoomGetEventResponse
        marca = "[Mensaje cifrado que no se pudo descifrar"  # cubre filas antiguas (tipo 'msg')
        sql = ("SELECT id, room_id, event_id FROM matrix_mensajes "
               "WHERE account_id = ? AND (tipo = 'cifrado' OR texto LIKE ?)")
        params = [account_id, marca + "%"]
        if room_id:
            sql += " AND room_id = ?"
            params.append(room_id)
        sql += " ORDER BY id DESC LIMIT 3000"  # tope de seguridad para no saturar el servidor
        try:
            filas = db.query(sql, tuple(params))
        except Exception as e:
            log.debug(f"Matrix: no se pudieron listar mensajes cifrados pendientes: {e}")
            return 0, set(), 0
        recuperados = 0
        solicitadas = 0
        sesiones_pedidas = set()  # no repetir la petición por cada mensaje de la misma sesión
        salas_tocadas = set()
        for fila_id, rid, eid in filas:
            if not rid or not eid:
                continue
            try:
                resp = await cliente.room_get_event(rid, eid)
            except Exception as e:
                log.debug(f"Matrix: room_get_event falló al re-descifrar {eid}: {e}")
                continue
            if not isinstance(resp, RoomGetEventResponse):
                continue
            ev = resp.event
            if ev is None or isinstance(ev, MegolmEvent):
                # Sigue sin poder descifrarse: pedir la clave de esa sesión a otros dispositivos.
                if isinstance(ev, MegolmEvent):
                    sid = getattr(ev, "session_id", "") or ""
                    if sid and sid not in sesiones_pedidas:
                        sesiones_pedidas.add(sid)
                        if not getattr(ev, "room_id", ""):
                            ev.room_id = rid
                        try:
                            await cliente.request_room_key(ev)
                            solicitadas += 1
                        except Exception as e:
                            # LocalProtocolError si ya había una petición en curso: no es error.
                            if "already" not in str(e).lower():
                                log.debug(f"Matrix: request_room_key falló para {eid}: {e}")
                continue
            tipo, texto, media_mxc, media_crypto = self._datos_evento_descifrado(ev)
            if tipo is None:
                continue
            try:
                db.execute(
                    "UPDATE matrix_mensajes SET texto = ?, tipo = ?, media_mxc = ?, "
                    "media_crypto = ? WHERE account_id = ? AND id = ?",
                    (texto, tipo, media_mxc, media_crypto, account_id, fila_id))
                # Indexar en la búsqueda global el texto ya descifrado (no se indexó de cifrado).
                if tipo in ("msg", "accion", "aviso") and (texto or "").strip():
                    try:
                        db.execute("INSERT INTO matrix_mensajes_fts (msg_db_id, sender, texto) "
                                   "VALUES (?, ?, ?)", (fila_id, "", texto))
                    except Exception:
                        pass
                recuperados += 1
                salas_tocadas.add(rid)
            except Exception as e:
                log.debug(f"Matrix: no se pudo actualizar la fila re-descifrada {fila_id}: {e}")
        if recuperados or solicitadas:
            log.info(f"Matrix: re-descifrado cuenta {account_id}: {recuperados} recuperado(s), "
                     f"{solicitadas} clave(s) solicitada(s) a otros dispositivos.")
        return recuperados, salas_tocadas, solicitadas

    def redescifrar_pendientes(self, account_id: int, room_id: str = ""):
        """Reintenta descifrar los mensajes que quedaron como "[Mensaje cifrado...]" (tipo
        'cifrado'). Devuelve (recuperados, solicitadas): cuántos se descifraron y para cuántas
        sesiones se pidió la clave a otros dispositivos. Pensado para llamarse tras importar claves
        o desde el menú; llamar desde task_manager (bloquea por las peticiones al servidor)."""
        cliente = self._cliente_conectado(account_id)
        # El tope de tiempo escala con el nº de peticiones (una por mensaje pendiente).
        recuperados, salas, solicitadas = self._run(
            self._redescifrar_async(account_id, cliente, room_id), timeout=600.0)
        for rid in salas:  # refrescar las conversaciones abiertas de las salas afectadas
            self._emit(account_id, "mensaje", room_id=rid, texto="", propio=False, inicial=False)
        return recuperados, solicitadas

    # ---------------- Verificación por emojis (SAS) ----------------
    # Funciona en LOS DOS SENTIDOS: que el otro (tu Element, o @otro:servidor) inicie la
    # verificación, o que la inicies tú (iniciar_verificacion, menú de dispositivos). El flujo
    # SAS de la spec exige enviar NUESTRA clave pública (share_key) para que el otro pueda
    # calcular los mismos emojis; SIN ese paso el otro se queda esperando y te ve "sin verificar"
    # aunque a ti te lleguen los emojis (era el fallo por el que la verificación no cerraba).
    def _registrar_verificacion(self, account_id: int, cliente):
        try:
            from nio import (KeyVerificationStart, KeyVerificationAccept, KeyVerificationKey,
                             KeyVerificationMac, KeyVerificationCancel, UnknownToDeviceEvent,
                             ToDeviceMessage)
        except Exception:
            return

        # matrix-nio SOLO implementa el flujo SAS clásico (empieza en m.key.verification.start). Los
        # clientes modernos (Element, Thunderbird…) empiezan con m.key.verification.request y esperan
        # un m.key.verification.ready antes de mandar el start (flujo MSC2366). nio no conoce esos dos
        # tipos: llegan como UnknownToDeviceEvent y, si nadie responde, el otro cliente se queda
        # esperando y aquí "no se inmuta". Este puente responde el request con un ready (y el done con
        # un done), de modo que el start posterior ya lo maneja el flujo de abajo.
        async def _enviar_todevice(tipo, user, device, contenido):
            try:
                await cliente.to_device(ToDeviceMessage(tipo, user, device, contenido))
            except Exception as e:
                log.debug(f"Matrix: no se pudo enviar {tipo}: {e}")

        _done_enviados = set()   # txids para los que ya mandamos m.key.verification.done

        async def _enviar_done(txid, user, device):
            if not txid or txid in _done_enviados:
                return
            _done_enviados.add(txid)
            await _enviar_todevice("m.key.verification.done", user, device, {"transaction_id": txid})

        async def _cb_desconocido(event):
            """Puente para el flujo moderno request/ready/done que nio no entiende."""
            try:
                src = getattr(event, "source", None) or {}
                tipo = src.get("type", "")
                cont = src.get("content", {}) or {}
                sender = getattr(event, "sender", "") or src.get("sender", "")
                txid = cont.get("transaction_id", "")
                otro_dev = cont.get("from_device", "")
                if tipo == "m.key.verification.request":
                    metodos = cont.get("methods", []) or []
                    if "m.sas.v1" not in metodos or not txid or not otro_dev:
                        return
                    # Responder READY: aceptamos el método de emojis y decimos nuestro dispositivo.
                    await _enviar_todevice("m.key.verification.ready", sender, otro_dev, {
                        "from_device": cliente.device_id,
                        "methods": ["m.sas.v1"],
                        "transaction_id": txid})
                    # Avisar a la UI de que hay una verificación en marcha (los emojis llegarán luego).
                    self._emit(account_id, "verificacion_solicitada", txid=txid, de=sender)
                elif tipo == "m.key.verification.done":
                    # El otro terminó: devolvemos nuestro done para cerrar el flujo limpio.
                    await _enviar_done(txid, sender, otro_dev)
            except Exception as e:
                log.debug(f"Matrix: puente de verificación moderna: {e}")

        cliente.add_to_device_callback(_cb_desconocido, (UnknownToDeviceEvent,))

        async def _enviar_clave(txid):
            """Envía nuestra clave pública SAS (imprescindible para que el otro compute los emojis)."""
            sas = cliente.key_verifications.get(txid)
            if sas is not None:
                try:
                    await cliente.to_device(sas.share_key())
                except Exception as e:
                    log.debug(f"Matrix: share_key no enviado: {e}")

        async def _cb(event):
            try:
                txid = getattr(event, "transaction_id", "") or ""
                if isinstance(event, KeyVerificationStart):
                    # El OTRO inició: si admite emojis, aceptamos y enviamos nuestra clave.
                    if "emoji" not in (getattr(event, "short_authentication_string", None) or []):
                        await cliente.cancel_key_verification(txid, reject=True)
                        return
                    await cliente.accept_key_verification(txid)
                    await _enviar_clave(txid)
                elif isinstance(event, KeyVerificationAccept):
                    # NOSOTROS iniciamos y el otro aceptó: ahora toca enviar nuestra clave.
                    await _enviar_clave(txid)
                elif isinstance(event, KeyVerificationKey):
                    # Ya tenemos ambas claves: mostrar los emojis para que el usuario compare.
                    sas = cliente.key_verifications.get(txid)
                    if sas:
                        emojis = sas.get_emoji()  # [(emoji, nombre_en)]
                        texto = "  ".join(f"{e} ({n})" for e, n in emojis)
                        self._emit(account_id, "verificacion_emojis", txid=txid, emojis=texto)
                elif isinstance(event, KeyVerificationMac):
                    # El otro envió su MAC. Si ya confirmamos por nuestro lado, el SAS queda
                    # verificado: marcamos el dispositivo como de confianza y avisamos a la UI (una
                    # sola vez). Si su MAC llega ANTES de que confirmemos, aquí aún no está verified;
                    # se cierra luego desde confirmar_verificacion (cubrimos los dos órdenes).
                    if self._finalizar_verificacion(account_id, cliente, txid):
                        sas = cliente.key_verifications.get(txid)
                        otro = getattr(sas, "other_olm_device", None) if sas else None
                        # Flujo moderno (Element/Thunderbird): cerrar con m.key.verification.done, para
                        # que el otro cliente no se quede "esperando" tras verificarse.
                        try:
                            if otro is not None:
                                await _enviar_done(txid, otro.user_id, otro.device_id)
                        except Exception:
                            pass
                elif isinstance(event, KeyVerificationCancel):
                    self._emit(account_id, "verificacion_cancelada",
                               motivo=getattr(event, "reason", "") or "")
            except Exception as e:
                log.error(f"Matrix: error en la verificación: {e}")

        cliente.add_to_device_callback(_cb, (KeyVerificationStart, KeyVerificationAccept,
                                             KeyVerificationKey, KeyVerificationMac,
                                             KeyVerificationCancel))

    def _finalizar_verificacion(self, account_id: int, cliente, txid: str) -> bool:
        """Si el SAS de 'txid' ya está verificado, marca el dispositivo del otro como DE CONFIANZA
        (verify_device, que persiste el estado en el almacén) y avisa a la UI con 'verificacion_ok'
        UNA sola vez. Se llama en los DOS momentos posibles —al recibir el MAC del otro y justo tras
        confirmar nosotros—, porque el SAS solo queda 'verified' cuando SE HAN dado ambas cosas
        (recibir su MAC + aceptar los emojis por nuestro lado), y no sabemos cuál ocurre antes.
        Sin esto, si el otro confirmaba primero (p. ej. en Element), el dispositivo quedaba
        verificado por dentro pero la lista seguía diciendo 'sin verificar' y no había aviso.
        Devuelve True si estaba verificado."""
        try:
            sas = cliente.key_verifications.get(txid) if txid else None
        except Exception:
            sas = None
        if sas is None or not getattr(sas, "verified", False):
            return False
        otro = getattr(sas, "other_olm_device", None)
        if otro is not None:
            try:
                cliente.verify_device(otro)
            except Exception:
                pass
        completadas = getattr(self, "_verif_completadas", None)
        if completadas is None:
            completadas = self._verif_completadas = set()
        if txid not in completadas:
            completadas.add(txid)
            self._emit(account_id, "verificacion_ok")
            # Tras verificar una sesión, los mensajes que estaban "[cifrado]" deberían poder leerse:
            # se PIDEN sus claves al dispositivo recién verificado (redescifrar_pendientes solicita
            # las que falten) y el enganche de RoomKeyEvent las aplica SOLAS. Así ya no hay que
            # desconectar y reconectar para que se descifren (fallo reportado por el usuario). En 2.º
            # plano porque esto corre en el bucle y redescifrar usa self._run.
            try:
                from app.core.task_manager import task_manager
                task_manager.start_task(
                    lambda ctx, a=account_id: self.redescifrar_pendientes(a),
                    pass_context=True, unique_key=f"matrix:redescifrar:{account_id}")
            except Exception:
                pass
        return True

    def confirmar_verificacion(self, account_id: int, txid: str):
        """El usuario confirmó que los emojis coinciden: envía nuestro MAC (confirm_key_verification
        hace accept_sas + get_mac + verify_device) y, si el otro ya confirmó, ambos quedan
        verificados. Tras confirmar, cerramos el flujo por si el MAC del otro llegó ANTES (caso
        típico al verificar primero en el navegador): así se marca de confianza y se avisa a la UI."""
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.confirm_short_auth_string(txid), timeout=30.0)
        self._finalizar_verificacion(account_id, cliente, txid)

    def cancelar_verificacion(self, account_id: int, txid: str):
        cliente = self._cliente_conectado(account_id)
        try:
            self._run(cliente.cancel_key_verification(txid, reject=True), timeout=30.0)
        except Exception as e:
            log.debug(f"Matrix: no se pudo cancelar la verificación: {e}")
