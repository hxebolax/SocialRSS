# -*- coding: utf-8 -*-
"""Salas de grupo (MUC, XEP-0045) de XMPP: unirse/salir, ocupantes con roles y afiliaciones EN
PALABRAS + acciones por rol, tema, invitaciones, moderación (0425), self-ping (0410), directorio de
salas del servidor y marcadores/bookmarks (0402 + BD local para el autojoin).
"""
import uuid

from app.services.xmpp.xmpp_common import (
    Any, Dict, Optional, List, db, log, XMPPError, ahora_iso, db_id_chat, jid_bare, nombre_desde_jid)

# Roles (por sala, temporales) y afiliaciones (permanentes) de MUC, en español.
ROLES = {"moderator": "moderador/a", "participant": "participante", "visitor": "visitante",
         "none": "sin voz"}
AFILIACIONES = {"owner": "propietario/a", "admin": "administrador/a", "member": "miembro",
                "outcast": "vetado/a", "none": "ninguna"}
NODO_BOOKMARKS = "urn:xmpp:bookmarks:1"


class _MucMixin:
    def _mi_nick_defecto(self, account_id: int) -> str:
        return (self._user_jid(account_id) or "").split("@", 1)[0] or "SocialRSS"

    # ---------------- Unirse / salir ----------------
    def unirse_sala(self, account_id: int, room_jid: str, nick: str = "", password: str = "",
                    guardar: bool = True) -> str:
        """Se une a una sala MUC. Guarda la sala localmente (y como marcador con autojoin si
        'guardar'). Devuelve el room_jid. Traduce los errores de join a mensajes claros."""
        cliente = self._cliente_conectado(account_id)
        room_jid = jid_bare(room_jid)
        nick = (nick or "").strip() or self._mi_nick_defecto(account_id)

        # Errores TRANSITORIOS típicos de una pasarela (slidge) que aún está arrancando su sesión con
        # la red legada (WhatsApp): merece la pena reintentar solos unos segundos.
        _TRANSITORIOS = ("not fully initialized", "retry later", "not logged", "not connected",
                         "legacy session", "initializ", "service-unavailable", "remote-server-timeout")

        async def _un_intento():
            """Un intento de join. Devuelve '' si entró, o el texto del error si la sala lo rechazó."""
            import asyncio as _a
            fut_err = _a.get_event_loop().create_future()

            def _on_perr(pr):
                try:
                    if jid_bare(str(pr["from"])) == room_jid and pr["type"] == "error":
                        if not fut_err.done():
                            fut_err.set_result(
                                (pr["error"]["text"] or "").strip()
                                or (pr["error"]["condition"] or "").strip() or "error")
                except Exception:
                    pass

            cliente.add_event_handler("presence", _on_perr)
            try:
                tarea = _a.ensure_future(cliente["xep_0045"].join_muc_wait(
                    room_jid, nick, seconds=0, password=(password or None), timeout=20))
                done, _pend = await _a.wait({tarea, fut_err},
                                            return_when=_a.FIRST_COMPLETED, timeout=22)
                if fut_err.done():
                    tarea.cancel()
                    return fut_err.result()
                if tarea in done:
                    tarea.result()  # propaga IqError/PresenceError si lo hubo
                    return ""
                tarea.cancel()
                return "remote-server-timeout"  # sin respuesta: se trata como transitorio y reintenta
            finally:
                cliente.del_event_handler("presence", _on_perr)

        async def _join():
            import asyncio as _a
            ultimo = ""
            for intento in range(4):
                try:
                    motivo = await _un_intento()
                except Exception as e:
                    motivo = str(e)
                if not motivo:
                    return True
                ultimo = motivo
                if any(k in motivo.lower() for k in _TRANSITORIOS) and intento < 3:
                    await _a.sleep(4)  # dar tiempo a que la pasarela termine de inicializar
                    continue
                raise XMPPError(self._error_join_texto(motivo))
            raise XMPPError(self._error_join_texto(ultimo))

        fila_id = db_id_chat(account_id, room_jid)
        ya_unido = db.query_one("SELECT 1 FROM xmpp_chats WHERE id = ? AND COALESCE(autojoin,0) = 1",
                                (fila_id,))
        try:
            self._run(_join(), timeout=120.0)
        except (XMPPError, Exception) as e:
            # Si YA estabas unido (autojoin=1), un timeout/rechazo al re-unirse no es un fallo real:
            # el servidor no reenvía la auto-presencia de una sala en la que ya estás. Sigue adelante.
            if ya_unido:
                log.debug(f"XMPP: re-join de sala ya unida ignorado ({room_jid}): {e}")
            elif isinstance(e, XMPPError):
                raise
            else:
                raise XMPPError(self._error_join(e))
        # Guardar el chat como MUC.
        if db.query_one("SELECT 1 FROM xmpp_chats WHERE id = ?", (fila_id,)):
            # Al unirse queda aceptada: se limpia 'solicitud' para que salga de Solicitudes y pase a Grupos.
            db.execute("UPDATE xmpp_chats SET tipo='muc', mi_nick=?, autojoin=?, solicitud=0 WHERE id=?",
                       (nick, 1 if guardar else 0, fila_id))
        else:
            db.execute("""INSERT INTO xmpp_chats (id, account_id, jid, tipo, nombre, mi_nick, autojoin)
                          VALUES (?, ?, ?, 'muc', ?, ?, ?)""",
                       (fila_id, account_id, room_jid, nombre_desde_jid(room_jid), nick,
                        1 if guardar else 0))
        if guardar:
            self._publicar_bookmark(account_id, room_jid, nick, autojoin=True, password=password)
        self._emit(account_id, "muc_actualizado", room_jid=room_jid)
        return room_jid

    def sincronizar_nombres_salas(self, account_id: int) -> int:
        """Rellena el nombre REAL (asunto del grupo) de todas las salas MUC guardadas cuyo nombre
        siga siendo un marcador (el JID crudo): así los grupos de una pasarela dejan de verse como
        '#12036...' y pasan a 'Cumpleaños de...'. Pide UNA vez el disco#items de cada componente MUC
        (el gateway o conference.*), que ya trae los nombres. NO pisa lo que hayas renombrado tú.
        Devuelve cuántos nombres se actualizaron."""
        cliente = self._cliente_conectado(account_id)
        filas = db.query("SELECT jid, nombre FROM xmpp_chats WHERE account_id = ? AND tipo = 'muc'",
                         (account_id,))
        # Candidatas: nombre vacío o igual al derivado del JID (marcador). Agrupadas por dominio.
        candidatas, dominios = [], set()
        for jid, nombre in (filas or []):
            if (not nombre or nombre == jid or nombre == nombre_desde_jid(jid)) and "@" in jid:
                candidatas.append(jid)
                dominios.add(jid.split("@", 1)[1])
        if not candidatas:
            return 0

        async def _t():
            import asyncio as _a
            mapa = {}
            for dom in dominios:
                try:
                    iq = await _a.wait_for(cliente["xep_0030"].get_items(jid=dom), timeout=15)
                    for it in iq["disco_items"]["items"]:  # (jid, node, name)
                        j = str(it[0]) if len(it) > 0 and it[0] else ""
                        n = (it[2] if len(it) > 2 else "") or ""
                        if j and n:
                            mapa[j] = n
                except Exception as e:
                    log.debug(f"XMPP: no se pudo listar salas de {dom}: {e}")
            return mapa
        mapa = self._run(_t(), timeout=60.0)
        n = 0
        for jid in candidatas:
            nombre = mapa.get(jid)
            if nombre and nombre != jid:
                db.execute("UPDATE xmpp_chats SET nombre = ? WHERE id = ?",
                           (nombre, db_id_chat(account_id, jid)))
                n += 1
        if n:
            self._emit(account_id, "muc_actualizado", room_jid="")
        return n

    @staticmethod
    def _error_join_texto(texto: str) -> str:
        """Traduce a un mensaje claro el texto/condición del error de join (incluidos los de una
        pasarela como WhatsApp/slidge)."""
        t = (texto or "").lower()
        if "not fully initialized" in t or "retry later" in t or "initializ" in t:
            return ("La pasarela aún está sincronizándose con WhatsApp (arrancando la sesión). Espera "
                    "un minuto a que termine y vuelve a intentar entrar en el grupo.")
        if "not logged" in t or "not connected" in t or "legacy network" in t:
            return ("La pasarela no está conectada a WhatsApp ahora mismo. Abre el chat con la "
                    "pasarela (whatsapp...) para reconectarla o vuelve a vincular tu WhatsApp; "
                    "luego reintenta entrar en el grupo.")
        if "conflict" in t:
            return "Ese apodo ya está en uso en la sala. Elige otro."
        if "not-authorized" in t or "password" in t:
            return "La sala pide contraseña (o la contraseña es incorrecta)."
        if "registration-required" in t:
            return "La sala es solo para miembros; necesitas que te inviten o registrarte."
        if "forbidden" in t:
            return "No tienes permiso para entrar en esta sala (¿estás vetado?)."
        if "item-not-found" in t:
            return "La sala no existe."
        if not texto:
            return "No se pudo entrar en la sala."
        return f"No se pudo entrar en la sala: {texto}"

    @classmethod
    def _error_join(cls, e: Exception) -> str:
        return cls._error_join_texto(str(e))

    def salir_sala(self, account_id: int, room_jid: str, olvidar: bool = False):
        """Sale de una sala MUC. Con 'olvidar', quita también el marcador (deja de autoentrar)."""
        cliente = self._cliente_conectado(account_id)
        room_jid = jid_bare(room_jid)
        fila = db.query_one("SELECT mi_nick FROM xmpp_chats WHERE id = ?",
                            (db_id_chat(account_id, room_jid),))
        nick = (fila[0] if fila else "") or self._mi_nick_defecto(account_id)

        async def _leave():
            cliente["xep_0045"].leave_muc(room_jid, nick)
            return True
        try:
            self._run(_leave(), timeout=20.0)
        except Exception:
            pass
        if olvidar:
            db.execute("DELETE FROM xmpp_chats WHERE id = ?", (db_id_chat(account_id, room_jid),))
            self._quitar_bookmark(account_id, room_jid)
        else:
            db.execute("UPDATE xmpp_chats SET autojoin = 0 WHERE id = ?",
                       (db_id_chat(account_id, room_jid),))
        self._emit(account_id, "muc_actualizado", room_jid=room_jid)

    def salas_muc(self, account_id: int, dominio=None) -> List[Dict[str, Any]]:
        """Salas MUC guardadas (para el buffer Grupos). 'dominio' acota a un puente concreto."""
        cond, extra = self._sql_dominio(dominio, account_id)
        filas = db.query("""SELECT jid, nombre, unread_count, last_message, autojoin, mi_nick,
                            COALESCE(fijado,0), COALESCE(silenciado_hasta,'')
                            FROM xmpp_chats WHERE account_id = ? AND tipo = 'muc'
                            AND COALESCE(solicitud,0) = 0 AND COALESCE(archivado,0) = 0""" + cond + """
                            ORDER BY COALESCE(fijado,0) DESC, unread_count DESC,
                                     last_message_date DESC, nombre COLLATE NOCASE""",
                         tuple([account_id] + extra))
        from datetime import datetime, timezone
        def _sil(h):
            if not h:
                return False
            try:
                return datetime.now(timezone.utc) < datetime.fromisoformat(h.replace("Z", "+00:00"))
            except Exception:
                return False
        return [{"jid": f[0], "nombre": f[1] or nombre_desde_jid(f[0]), "no_leidos": f[2] or 0,
                 "ultimo": f[3] or "", "autojoin": bool(f[4]), "mi_nick": f[5] or "",
                 "fijado": bool(f[6]), "silenciado": _sil(f[7])} for f in filas]

    # ---------------- Mensajes de sala ----------------
    # Errores TRANSITORIOS típicos de una pasarela (slidge) arrancando su sesión legada (WhatsApp).
    _JOIN_TRANSITORIOS = ("not fully initialized", "retry later", "not logged", "not connected",
                          "legacy session", "initializ", "service-unavailable", "remote-server-timeout")

    async def _asegurar_unido_muc(self, cliente, account_id: int, room_jid: str):
        """Garantiza que estamos UNIDOS a la sala ANTES de enviarle nada. Las salas de una pasarela
        (grupos de WhatsApp) llegan con autojoin=0 y NO se entra en ellas al conectar; sin la
        presencia de entrada, el servidor/pasarela DESCARTA en SILENCIO los mensajes de grupo que
        mandes (por eso "no se enviaba nada" en grupos, pero sí a contactos). Si no estamos dentro,
        entramos una vez (con reintentos si la pasarela aún arranca) y marcamos autojoin=1 para que
        en adelante se entre solo al conectar y lleguen los mensajes en vivo. Lanza XMPPError claro
        si no se puede entrar, en vez de fallar en silencio."""
        import asyncio as _a
        room_jid = jid_bare(room_jid)
        try:
            unidas = {jid_bare(str(j)) for j in cliente["xep_0045"].get_joined_rooms()}
        except Exception:
            unidas = set()
        if room_jid in unidas:
            return
        fila_id = db_id_chat(account_id, room_jid)
        fila = db.query_one("SELECT mi_nick FROM xmpp_chats WHERE id = ?", (fila_id,))
        nick = (fila[0] if fila else "") or self._mi_nick_defecto(account_id)
        ultimo = ""
        for intento in range(3):
            try:
                await cliente["xep_0045"].join_muc_wait(room_jid, nick, seconds=0, timeout=15)
                db.execute("UPDATE xmpp_chats SET autojoin = 1, mi_nick = ? WHERE id = ?",
                           (nick, fila_id))
                return
            except Exception as e:
                ultimo = str(e)
                if any(k in ultimo.lower() for k in self._JOIN_TRANSITORIOS) and intento < 2:
                    await _a.sleep(3)
                    continue
                break
        raise XMPPError(self._error_join_texto(ultimo) if ultimo else
                        "No se pudo entrar en la sala para enviar el mensaje.")

    def enviar_muc(self, account_id: int, room_jid: str, texto: str) -> str:
        """Envía un mensaje a la sala. NO lo guardamos aquí: la sala nos lo reenvía (eco) y se
        guarda entonces con dedupe; así se ve como lo ven los demás."""
        cliente = self._cliente_conectado(account_id)
        room_jid = jid_bare(room_jid)
        texto = texto or ""
        if not texto.strip():
            return ""
        oid = "sr-" + uuid.uuid4().hex
        cifrar = self.esta_cifrado(account_id, room_jid)

        async def _enviar():
            await self._asegurar_unido_muc(cliente, account_id, room_jid)
            msg = cliente.make_message(mto=room_jid, mbody=texto, mtype="groupchat")
            try:
                msg["origin_id"]["id"] = oid
            except Exception:
                pass
            if cifrar:
                destinos = await self._destinatarios_cifrado(cliente, room_jid, es_muc=True)
                if not destinos:
                    from app.services.xmpp.xmpp_common import XMPPError
                    raise XMPPError("No se pudo cifrar en la sala con OMEMO: la sala debe ser NO "
                                    "anónima (que muestre los JID reales) y de solo miembros.")
                enviado = await self._cifrar_y_enviar(cliente, msg, destinos)
                if not enviado:
                    from app.services.xmpp.xmpp_common import XMPPError
                    raise XMPPError("No se pudo cifrar el mensaje de la sala con OMEMO.")
                return True
            msg.send()
            return True
        # 60 s porque el PRIMER envío a una sala puede incluir la unión (join) a la pasarela.
        self._run(_enviar(), timeout=60.0)
        return oid

    def cambiar_tema(self, account_id: int, room_jid: str, tema: str):
        cliente = self._cliente_conectado(account_id)

        async def _t():
            cliente["xep_0045"].set_subject(jid_bare(room_jid), tema or "")
            return True
        self._run(_t(), timeout=20.0)

    # ---------------- Ocupantes y roles ----------------
    def ocupantes(self, account_id: int, room_jid: str) -> List[Dict[str, Any]]:
        """Ocupantes de la sala con su rol y afiliación EN PALABRAS, ordenados por rango."""
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return []
        room_jid = jid_bare(room_jid)
        muc = cliente["xep_0045"]
        try:
            nicks = muc.get_roster(room_jid)
        except Exception:
            nicks = []
        salida = []
        for nick in nicks:
            try:
                rol = muc.get_jid_property(room_jid, nick, "role") or "participant"
                afi = muc.get_jid_property(room_jid, nick, "affiliation") or "none"
                jid_real = muc.get_jid_property(room_jid, nick, "jid") or ""
            except Exception:
                rol, afi, jid_real = "participant", "none", ""
            salida.append({"nick": nick, "rol": ROLES.get(rol, rol), "rol_clave": rol,
                           "afiliacion": AFILIACIONES.get(afi, afi), "afi_clave": afi,
                           "jid": jid_bare(str(jid_real))})
        orden = {"moderator": 0, "participant": 1, "visitor": 2, "none": 3}
        salida.sort(key=lambda o: (orden.get(o["rol_clave"], 9), o["nick"].lower()))
        return salida

    def mi_rol_en(self, account_id: int, room_jid: str) -> str:
        """Tu rol (clave) en la sala: 'moderator'/'participant'/'visitor'/'' si no estás."""
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return ""
        room_jid = jid_bare(room_jid)
        fila = db.query_one("SELECT mi_nick FROM xmpp_chats WHERE id = ?",
                            (db_id_chat(account_id, room_jid),))
        nick = (fila[0] if fila else "") or self._mi_nick_defecto(account_id)
        try:
            return cliente["xep_0045"].get_jid_property(room_jid, nick, "role") or ""
        except Exception:
            return ""

    def accion_ocupante(self, account_id: int, room_jid: str, nick: str, accion: str, motivo: str = ""):
        """Acciones de moderador/admin sobre un ocupante: dar_voz/quitar_voz/dar_moderador/
        quitar_moderador (rol) y expulsar (kick), banear/desbanear, hacer_miembro, hacer_admin,
        hacer_propietario (afiliación). El servidor valida los permisos."""
        cliente = self._cliente_conectado(account_id)
        room_jid = jid_bare(room_jid)
        roles = {"dar_voz": "participant", "quitar_voz": "visitor",
                 "dar_moderador": "moderator", "quitar_moderador": "participant",
                 "expulsar": "none"}
        afis = {"hacer_miembro": "member", "hacer_admin": "admin", "hacer_propietario": "owner",
                "banear": "outcast", "desbanear": "none", "quitar_afiliacion": "none"}

        async def _op():
            # set_role/set_affiliation son CORRUTINAS en slixmpp 1.16: sin await la operación no se
            # enviaba de verdad (y saltaba "coroutine was never awaited"), pero la UI daba éxito
            # (XMPP-015). Con await se espera el IQ; un error de permisos/timeout se propaga.
            if accion in roles:
                await cliente["xep_0045"].set_role(room_jid, nick, roles[accion], reason=(motivo or ""))
            elif accion in afis:
                # Banear/afiliación por el JID real del ocupante si lo tenemos; si no, por nick.
                jid_real = ""
                try:
                    jid_real = jid_bare(str(cliente["xep_0045"].get_jid_property(room_jid, nick, "jid") or ""))
                except Exception:
                    jid_real = ""
                if jid_real:
                    await cliente["xep_0045"].set_affiliation(room_jid, afis[accion], jid=jid_real,
                                                              reason=(motivo or ""))
                else:
                    await cliente["xep_0045"].set_affiliation(room_jid, afis[accion], nick=nick,
                                                              reason=(motivo or ""))
            else:
                raise XMPPError(f"Acción desconocida: {accion}")
            return True
        self._run(_op(), timeout=25.0)

    # ---------------- Invitaciones y moderación ----------------
    def invitar_sala(self, account_id: int, room_jid: str, jid_invitado: str, motivo: str = "",
                     directa: bool = True):
        """Invita a alguien a la sala. Directa (XEP-0249, la ve cualquier cliente) o mediada
        (por el servidor de la sala)."""
        cliente = self._cliente_conectado(account_id)
        room_jid = jid_bare(room_jid)
        jid_invitado = jid_bare(jid_invitado)

        async def _inv():
            if directa:
                cliente["xep_0249"].send_invitation(jid_invitado, room_jid, reason=(motivo or None))
            else:
                cliente["xep_0045"].invite(room_jid, jid_invitado, reason=(motivo or ""))
            return True
        self._run(_inv(), timeout=20.0)

    def moderar_mensaje(self, account_id: int, room_jid: str, stanza_id: str, motivo: str = ""):
        """Retira (modera) un mensaje de la sala como moderador (XEP-0425). Marca el local como
        eliminado."""
        cliente = self._cliente_conectado(account_id)
        room_jid = jid_bare(room_jid)

        async def _mod():
            await cliente["xep_0425"].moderate(room_jid, id=stanza_id, reason=(motivo or ""))
            return True
        self._run(_mod(), timeout=25.0)

    # ---------------- Directorio de salas ----------------
    def directorio_salas(self, account_id: int, servicio: str = "", filtro: str = "") -> List[Dict[str, Any]]:
        """Lista las salas públicas del servicio MUC del servidor (o de uno indicado). Si no se
        indica servicio, se descubre buscando un componente de tipo 'conference'."""
        cliente = self._cliente_conectado(account_id)

        async def _dir():
            svc = servicio
            if not svc:
                # Descubrir el servicio MUC: disco items del dominio, identidad category conference.
                dominio = jid_bare(str(cliente.boundjid)).split("@", 1)[1]
                items = await cliente["xep_0030"].get_items(jid=dominio, timeout=30)
                for it in items["disco_items"]["items"]:
                    cand = it[0]
                    try:
                        info = await cliente["xep_0030"].get_info(jid=cand, timeout=20)
                        for ident in info["disco_info"]["identities"]:
                            if ident[0] == "conference":
                                svc = cand
                                break
                    except Exception:
                        continue
                    if svc:
                        break
            if not svc:
                return []
            resp = await cliente["xep_0030"].get_items(jid=svc, timeout=45)
            salida = []
            for it in resp["disco_items"]["items"]:
                jid, _node, nombre = it
                if filtro and filtro.lower() not in (str(nombre) + str(jid)).lower():
                    continue
                salida.append({"jid": str(jid), "nombre": str(nombre) or nombre_desde_jid(str(jid))})
            return salida
        try:
            return self._run(_dir(), timeout=90.0) or []
        except Exception as e:
            raise XMPPError(_("No se pudo obtener el directorio de salas: {0}").format(e))

    # ---------------- Bookmarks 0402 (best-effort; la BD local manda el autojoin) ----------------
    def _publicar_bookmark(self, account_id: int, room_jid: str, nick: str, autojoin: bool,
                           password: str = ""):
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return

        async def _pub():
            from slixmpp.plugins.xep_0402.stanza import Conference
            from slixmpp.xmlstream import ET
            conf = Conference()
            conf["name"] = self._nombre_chat(account_id, room_jid)
            conf.set_autojoin(bool(autojoin))
            if nick:
                conf["nick"] = nick
            if password:
                conf["password"] = password
            await cliente["xep_0060"].publish(jid_bare(str(cliente.boundjid)), NODO_BOOKMARKS,
                                              id=jid_bare(room_jid), payload=conf, timeout=20)
            return True
        try:
            self._run(_pub(), timeout=25.0)
        except Exception as e:
            log.debug(f"XMPP: no se pudo publicar el bookmark (no crítico): {e}")

    def _quitar_bookmark(self, account_id: int, room_jid: str):
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return

        async def _ret():
            await cliente["xep_0060"].retract(jid_bare(str(cliente.boundjid)), NODO_BOOKMARKS,
                                              jid_bare(room_jid), notify=True, timeout=20)
            return True
        try:
            self._run(_ret(), timeout=25.0)
        except Exception as e:
            log.debug(f"XMPP: no se pudo quitar el bookmark (no crítico): {e}")

    def autoentrar_salas(self, account_id: int):
        """Entra en las salas guardadas con autojoin (llamado tras conectar). Bloqueante: correr en
        segundo plano; entra sala a sala y no aborta si una falla."""
        filas = db.query("SELECT jid, mi_nick FROM xmpp_chats WHERE account_id = ? AND tipo = 'muc' "
                         "AND COALESCE(autojoin,0) = 1", (account_id,))
        for jid, nick in filas:
            try:
                self.unirse_sala(account_id, jid, nick or "", guardar=False)
            except Exception as e:
                log.debug(f"XMPP: autoentrada a {jid} falló: {e}")
