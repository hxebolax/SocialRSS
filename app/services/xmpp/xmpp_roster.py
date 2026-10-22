# -*- coding: utf-8 -*-
"""Roster XMPP: contactos, suscripciones de presencia (pedir/aceptar/rechazar), añadir/quitar y
bloqueo (XEP-0191). El roster con suscripciones y presencia es lo genuinamente nuevo de XMPP: los
contactos se PIDEN (suscripción mutua) y tienen estados; no existía en Telegram/IRC/Matrix.
"""
from app.services.xmpp.xmpp_common import (
    Any, Dict, List, db, log, XMPPError, db_id_chat, jid_bare, nombre_desde_jid, nombre_contacto)


class _RosterMixin:
    # ---------------- Consultas para la UI ----------------
    def contactos(self, account_id: int, dominio=None) -> List[Dict[str, Any]]:
        """Contactos del roster (no solicitudes), con presencia y no leídos. 'dominio' acota a un
        puente concreto (o '__nativo__' para excluir puentes)."""
        cond, extra = self._sql_dominio(dominio, account_id)
        filas = db.query("""
            SELECT jid, nombre, presencia, estado_texto, suscripcion, unread_count, last_message,
                   COALESCE(bloqueado,0)
            FROM xmpp_chats
            WHERE account_id = ? AND tipo = 'contacto' AND COALESCE(solicitud,0) = 0
        """ + cond + " ORDER BY unread_count DESC, nombre COLLATE NOCASE", tuple([account_id] + extra))
        return [{"jid": f[0], "nombre": nombre_contacto(f[1], f[0]), "presencia": f[2] or "",
                 "estado_texto": f[3] or "", "suscripcion": f[4] or "", "no_leidos": f[5] or 0,
                 "ultimo": f[6] or "", "bloqueado": bool(f[7])} for f in filas]

    def conversaciones(self, account_id: int, dominio=None) -> List[Dict[str, Any]]:
        """Contactos 1:1 con una CONVERSACIÓN activa (tienen mensajes, un último mensaje o no leídos),
        ordenados por lo más reciente arriba. Es la lista tipo "chats" de WhatsApp. 'dominio' acota a
        un puente concreto (o '__nativo__' para excluir puentes)."""
        cond, extra = self._sql_dominio(dominio, account_id)
        filas = db.query("""
            SELECT jid, nombre, presencia, estado_texto, suscripcion, unread_count, last_message,
                   COALESCE(bloqueado,0), COALESCE(fijado,0), COALESCE(silenciado_hasta,'')
            FROM xmpp_chats
            WHERE account_id = ? AND tipo = 'contacto' AND COALESCE(solicitud,0) = 0
              AND COALESCE(archivado,0) = 0
              AND (COALESCE(last_message,'') <> '' OR COALESCE(unread_count,0) > 0
                   OR COALESCE(last_message_date,'') <> '')
        """ + cond + """
            ORDER BY COALESCE(fijado,0) DESC, unread_count DESC, last_message_date DESC,
                     nombre COLLATE NOCASE
        """, tuple([account_id] + extra))
        return [{"jid": f[0], "nombre": nombre_contacto(f[1], f[0]), "presencia": f[2] or "",
                 "estado_texto": f[3] or "", "suscripcion": f[4] or "", "no_leidos": f[5] or 0,
                 "ultimo": f[6] or "", "bloqueado": bool(f[7]), "fijado": bool(f[8]),
                 "silenciado": self._silencio_activo(f[9])} for f in filas]

    @staticmethod
    def _silencio_activo(hasta: str) -> bool:
        from datetime import datetime, timezone
        if not hasta:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(hasta.replace("Z", "+00:00"))
        except Exception:
            return False

    # ---------------- Puentes / pasarelas: buffers por red ----------------
    _RED_LEGIBLE = {
        "whatsapp": "WhatsApp", "telegram": "Telegram", "signal": "Signal", "discord": "Discord",
        "steam": "Steam", "irc": "IRC", "mattermost": "Mattermost", "skype": "Skype",
        "facebook": "Facebook", "messenger": "Messenger", "instagram": "Instagram", "sms": "SMS",
    }
    # Componentes ESTÁNDAR del propio servidor (no son puentes): salas MUC, subida de archivos,
    # proxy, pubsub, directorio, etc. Se excluyen de la detección de pasarelas.
    _COMPONENTES_ESTANDAR = {
        "conference", "conferences", "muc", "rooms", "chat", "chatrooms", "salas",
        "upload", "uploads", "httpupload", "http-upload", "share", "files", "filetransfer",
        "proxy", "proxy65", "bytestreams", "pubsub", "pep", "vjud", "search", "directory",
        "echo", "ping", "cloud", "meet", "jitsi", "turn", "stun",
    }

    def dominio_cuenta(self, account_id: int) -> str:
        jid = self._jids.get(account_id) or self._user_jid(account_id) or ""
        return jid.split("@", 1)[1] if "@" in jid else jid

    def invalidar_puentes(self, account_id: int = None):
        """Invalida la caché de puentes (al registrar/dar de baja una pasarela o al aparecer un chat
        de un dominio nuevo). Con account_id None limpia toda la caché."""
        cache = getattr(self, "_puentes_cache", None)
        if cache is None:
            return
        if account_id is None:
            cache.clear()
        else:
            cache.pop(account_id, None)

    def puentes(self, account_id: int) -> List[Dict[str, Any]]:
        """Detecta los PUENTES (pasarelas) con los que tienes chats: son los dominios de componente
        distintos del servidor de tu cuenta. Devuelve [{dominio, nombre}] para agrupar sus buffers.

        RENDIMIENTO: lo llaman varios sitios calientes (al enviar un mensaje —esta_cifrado—, al
        comprobar cambios de pasarela, al construir el árbol). El resultado solo cambia cuando aparece
        o se quita un dominio de pasarela, así que se CACHEA por cuenta con un TTL corto; la caché se
        invalida al registrar/dar de baja una pasarela y al aparecer un chat de un dominio nuevo."""
        import time as _t
        cache = getattr(self, "_puentes_cache", None)
        if cache is None:
            cache = self._puentes_cache = {}
        ent = cache.get(account_id)
        if ent is not None and (_t.monotonic() - ent[0]) < 10.0:
            return ent[1]
        resultado = self._calcular_puentes(account_id)
        cache[account_id] = (_t.monotonic(), resultado)
        return resultado

    def _calcular_puentes(self, account_id: int) -> List[Dict[str, Any]]:
        servidor = self.dominio_cuenta(account_id)
        filas = db.query("SELECT DISTINCT jid FROM xmpp_chats WHERE account_id = ? "
                         "AND COALESCE(solicitud,0) = 0", (account_id,))
        doms = {}
        for (jid,) in filas:
            jid = jid or ""
            dom = jid.split("@", 1)[1] if "@" in jid else jid  # dominio (o el JID de un componente)
            if not dom or dom == servidor:
                continue  # nativo del servidor, no es un puente
            if servidor and not dom.endswith("." + servidor):
                continue  # solo son puentes los SUBdominios (componentes locales) de tu servidor
            etiqueta = dom.split(".", 1)[0]
            if etiqueta.lower() in self._COMPONENTES_ESTANDAR:
                continue  # es un componente normal del servidor (MUC, upload…), no una pasarela
            if dom not in doms:
                doms[dom] = self._RED_LEGIBLE.get(etiqueta.lower(), etiqueta.capitalize())
        return [{"dominio": d, "nombre": n} for d, n in sorted(doms.items(), key=lambda x: x[1].lower())]

    def _sql_dominio(self, dominio, account_id):
        """Fragmento WHERE + params para acotar por origen:
        - dominio None  -> sin filtro (todo).
        - '__nativo__'  -> todo lo que NO pertenece a un puente (servidor propio + sus componentes
          normales como salas MUC), excluyendo los dominios de las pasarelas.
        - un dominio    -> solo ese puente (jids de ese componente, incluido el propio gateway)."""
        if not dominio:
            return "", []
        if dominio == "__nativo__":
            doms = [p["dominio"] for p in self.puentes(account_id)]
            if not doms:
                return "", []
            cond = "".join(" AND jid NOT LIKE ? AND jid <> ?" for _ in doms)
            params = []
            for d in doms:
                params += ["%@" + d, d]
            return cond, params
        return " AND (jid LIKE ? OR jid = ?)", ["%@" + dominio, dominio]

    # ---------------- Organización de chats (estilo WhatsApp) ----------------
    def archivar_chat(self, account_id: int, jid: str, archivar: bool = True):
        """Archiva (o desarchiva) un chat: sale de Conversaciones/Grupos y va a Archivados."""
        from app.services.xmpp.xmpp_common import db_id_chat
        db.execute("UPDATE xmpp_chats SET archivado = ? WHERE id = ?",
                   (1 if archivar else 0, db_id_chat(account_id, jid)))
        self._emit(account_id, "chats_organizados")

    def fijar_chat(self, account_id: int, jid: str, fijar: bool = True):
        """Fija (ancla) un chat arriba (o lo suelta)."""
        from app.services.xmpp.xmpp_common import db_id_chat
        db.execute("UPDATE xmpp_chats SET fijado = ? WHERE id = ?",
                   (1 if fijar else 0, db_id_chat(account_id, jid)))
        self._emit(account_id, "chats_organizados")

    def silenciar_chat(self, account_id: int, jid: str, horas: float):
        """Silencia un chat durante 'horas' (0 = quitar silencio; horas < 0 = para siempre)."""
        from datetime import datetime, timedelta, timezone
        from app.services.xmpp.xmpp_common import db_id_chat
        if horas == 0:
            hasta = ""
        elif horas < 0:
            hasta = "9999-12-31T23:59:59+00:00"  # "para siempre"
        else:
            hasta = (datetime.now(timezone.utc) + timedelta(hours=horas)).isoformat()
        db.execute("UPDATE xmpp_chats SET silenciado_hasta = ? WHERE id = ?",
                   (hasta, db_id_chat(account_id, jid)))
        self._emit(account_id, "chats_organizados")

    def esta_silenciado(self, account_id: int, jid: str) -> bool:
        """True si el chat está silenciado y el silencio aún no ha caducado."""
        from datetime import datetime, timezone
        from app.services.xmpp.xmpp_common import db_id_chat
        fila = db.query_one("SELECT COALESCE(silenciado_hasta,'') FROM xmpp_chats WHERE id = ?",
                            (db_id_chat(account_id, jid),))
        hasta = (fila[0] if fila else "") or ""
        if not hasta:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(hasta.replace("Z", "+00:00"))
        except Exception:
            return False

    def archivados(self, account_id: int, dominio=None) -> List[Dict[str, Any]]:
        """Chats (contactos o grupos) archivados, con su último mensaje y no leídos. 'dominio' acota
        a un puente concreto (o '__nativo__' para solo el servidor propio)."""
        cond, extra = self._sql_dominio(dominio, account_id)
        filas = db.query("""
            SELECT jid, nombre, tipo, COALESCE(presencia,''), COALESCE(unread_count,0),
                   COALESCE(last_message,'')
            FROM xmpp_chats WHERE account_id = ? AND COALESCE(archivado,0) = 1
              AND COALESCE(solicitud,0) = 0
        """ + cond + " ORDER BY last_message_date DESC, nombre COLLATE NOCASE",
                         tuple([account_id] + extra))
        return [{"jid": f[0], "nombre": f[1] or nombre_desde_jid(f[0]), "tipo": f[2] or "contacto",
                 "presencia": f[3], "no_leidos": f[4] or 0, "ultimo": f[5]} for f in filas]

    def solicitudes(self, account_id: int, dominio=None) -> List[Dict[str, Any]]:
        """Pendientes: suscripciones de presencia (contacto) e invitaciones a salas (muc).
        'dominio' acota igual que en contactos/grupos: None todo, '__nativo__' solo el servidor
        propio, o el dominio de un puente para ver solo sus solicitudes."""
        cond, extra = self._sql_dominio(dominio, account_id)
        filas = db.query("""SELECT jid, nombre, tipo FROM xmpp_chats
                            WHERE account_id = ? AND COALESCE(solicitud,0) = 1""" + cond + """
                            ORDER BY nombre COLLATE NOCASE""", (account_id, *extra))
        return [{"jid": f[0], "nombre": f[1] or nombre_desde_jid(f[0]), "tipo": f[2] or "contacto"}
                for f in filas]

    # ---------------- Acciones sobre el roster ----------------
    def agregar_contacto(self, account_id: int, jid: str, nombre: str = ""):
        """Añade un contacto y le pide suscripción de presencia (para verlo conectado)."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid).strip()
        if not jid or "@" not in jid:
            raise XMPPError("Escribe un JID válido (usuario@servidor).")

        async def _add():
            # update_roster es CORRUTINA (slixmpp 1.16): con await se espera el IQ del servidor, así
            # que un error de permisos/servicio se detecta antes de tocar la BD local (XMPP-020).
            await cliente.update_roster(jid, name=(nombre or None))
            cliente.send_presence_subscription(pto=jid, ptype="subscribe")
            return True
        self._run(_add(), timeout=20.0)
        fila_id = db_id_chat(account_id, jid)
        if not db.query_one("SELECT 1 FROM xmpp_chats WHERE id = ?", (fila_id,)):
            db.execute("""INSERT INTO xmpp_chats (id, account_id, jid, tipo, nombre)
                          VALUES (?, ?, ?, 'contacto', ?)""",
                       (fila_id, account_id, jid, nombre or nombre_desde_jid(jid)))
        self._emit(account_id, "roster_actualizado")

    def aceptar_solicitud(self, account_id: int, jid: str, pedir_tambien: bool = True):
        """Acepta una suscripción entrante y, opcionalmente, pide la del contacto (suscripción
        mutua: así ambos os veis conectados)."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _ok():
            cliente.send_presence_subscription(pto=jid, ptype="subscribed")
            if pedir_tambien:
                cliente.send_presence_subscription(pto=jid, ptype="subscribe")
            return True
        self._run(_ok(), timeout=20.0)
        db.execute("UPDATE xmpp_chats SET solicitud = 0 WHERE id = ?", (db_id_chat(account_id, jid),))
        self._emit(account_id, "roster_actualizado")

    def rechazar_solicitud(self, account_id: int, jid: str):
        """Rechaza (o revoca) una suscripción de presencia."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _no():
            cliente.send_presence_subscription(pto=jid, ptype="unsubscribed")
            return True
        self._run(_no(), timeout=20.0)
        db.execute("UPDATE xmpp_chats SET solicitud = 0 WHERE id = ?", (db_id_chat(account_id, jid),))
        self._emit(account_id, "roster_actualizado")

    def quitar_contacto(self, account_id: int, jid: str):
        """Elimina un contacto del roster (rompe la suscripción en ambos sentidos)."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _del():
            await cliente.del_roster_item(jid)  # corrutina: esperar el IQ antes de borrar local
            return True
        self._run(_del(), timeout=20.0)
        db.execute("DELETE FROM xmpp_chats WHERE id = ? AND tipo = 'contacto'",
                   (db_id_chat(account_id, jid),))
        self._emit(account_id, "roster_actualizado")

    def renombrar_contacto(self, account_id: int, jid: str, nombre: str):
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _ren():
            await cliente.update_roster(jid, name=(nombre or None))  # corrutina: esperar IQ
            return True
        self._run(_ren(), timeout=20.0)
        db.execute("UPDATE xmpp_chats SET nombre = ? WHERE id = ?",
                   (nombre or nombre_desde_jid(jid), db_id_chat(account_id, jid)))
        self._emit(account_id, "roster_actualizado")

    # ---------------- Bloqueo (XEP-0191) ----------------
    def bloquear_contacto(self, account_id: int, jid: str, bloquear: bool = True):
        """Bloquea o desbloquea a un contacto en el SERVIDOR (XEP-0191): sus stanzas se descartan
        para todas tus sesiones. Patrón de los ignorados de IRC pero del lado del servidor."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _blk():
            plugin = cliente.plugin.get("xep_0191")
            if plugin is None:
                raise XMPPError("Este servidor no admite el bloqueo de contactos.")
            if bloquear:
                await plugin.block(jid)
            else:
                await plugin.unblock(jid)
            return True
        self._run(_blk(), timeout=20.0)
        db.execute("UPDATE xmpp_chats SET bloqueado = ? WHERE id = ?",
                   (1 if bloquear else 0, db_id_chat(account_id, jid)))
        self._emit(account_id, "roster_actualizado")
