# -*- coding: utf-8 -*-
"""Mensajería 1:1 de XMPP (tanda X2): enviar mensajes con origin-id (XEP-0359) y recibo de entrega
(XEP-0184), estados de chat "escribiendo…" (XEP-0085) y marcar leído. La recepción en vivo (incluida
la sincronización con otros dispositivos por Carbons) se maneja en el núcleo.
"""
import uuid

from app.services.xmpp.xmpp_common import (
    Any, Dict, Optional, db, log, XMPPError, ahora_iso, db_id_chat, jid_bare)


class _MensajesMixin:
    def enviar_mensaje(self, account_id: int, jid: str, texto: str) -> str:
        """Envía un mensaje de chat 1:1. Añade un origin-id estable (para deduplicar el eco por
        Carbons) y PIDE recibo de entrega. Guarda el mensaje propio al momento. Devuelve el
        origin-id (que usamos como stanza-id local)."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)
        texto = texto or ""
        if not texto.strip():
            return ""
        oid = "sr-" + uuid.uuid4().hex
        cifrar = self.esta_cifrado(account_id, jid)

        async def _enviar():
            msg = cliente.make_message(mto=jid, mbody=texto, mtype="chat")
            try:
                msg["origin_id"]["id"] = oid
            except Exception:
                pass
            try:
                msg["request_receipt"] = True   # pedir confirmación de entrega (0184)
            except Exception:
                pass
            try:
                msg["chat_state"] = "active"     # ya no estoy escribiendo
            except Exception:
                pass
            try:
                msg["markable"] = True           # pedir marcador de lectura (0333)
            except Exception:
                pass
            if cifrar:
                # Cifrar con OMEMO para el contacto (y para mis otros dispositivos). Si algo falla,
                # NO se cae a texto claro: se aborta el envío para no filtrar un mensaje que el
                # usuario pidió cifrado.
                enviado = await self._cifrar_y_enviar(cliente, msg, {self._jid_obj(jid)})
                if not enviado:
                    from app.services.xmpp.xmpp_common import XMPPError
                    raise XMPPError("No se pudo cifrar el mensaje con OMEMO (¿el contacto no tiene "
                                    "claves publicadas?). No se envió en claro.")
                return True
            msg.send()
            return True
        self._run(_enviar(), timeout=30.0)
        # Guardar el propio (dedupe por origin-id: si volviera por un carbon, no se duplica).
        yo = self._user_jid(account_id)
        self._guardar_mensaje(account_id, jid, oid, yo, self._nombre_propio(account_id),
                              texto, "msg", ahora_iso(), propio=True, leido=True, contar=False,
                              origin_id=oid, cifrado=cifrar)
        return oid

    def enviar_correccion(self, account_id: int, jid: str, texto: str, id_original: str) -> str:
        """Corrige tu ÚLTIMO mensaje enviado (XEP-0308): sustituye su texto en el cliente del otro.
        'id_original' es el origin-id del mensaje corregido. Actualiza el local (marcado editado)."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)
        texto = (texto or "").strip()
        if not texto or not id_original:
            return ""
        oid = "sr-" + uuid.uuid4().hex
        cifrar = self.esta_cifrado(account_id, jid)

        async def _enviar():
            msg = cliente.make_message(mto=jid, mbody=texto, mtype="chat")
            try:
                msg["replace"]["id"] = id_original
                msg["origin_id"]["id"] = oid
            except Exception:
                pass
            if cifrar:
                enviado = await self._cifrar_y_enviar(cliente, msg, {self._jid_obj(jid)})
                if not enviado:
                    from app.services.xmpp.xmpp_common import XMPPError
                    raise XMPPError("No se pudo cifrar la corrección con OMEMO. No se envió en claro.")
                return True
            msg.send()
            return True
        self._run(_enviar(), timeout=30.0)
        db.execute("UPDATE xmpp_mensajes SET texto = ?, editado = 1 "
                   "WHERE account_id = ? AND (origin_id = ? OR stanza_id = ?)",
                   (texto, account_id, id_original, id_original))
        self._reindexar_fts(account_id, id_original, texto)
        self._emit(account_id, "mensaje", chat_jid=jid, propio=True, cuenta=False)
        return oid

    def ultimo_mensaje_propio(self, account_id: int, jid: str) -> Optional[Dict[str, Any]]:
        """Datos del último mensaje que TÚ enviaste a un chat (para corregirlo). None si no hay."""
        fila = db.query_one(
            "SELECT origin_id, stanza_id, texto FROM xmpp_mensajes "
            "WHERE account_id = ? AND chat_jid = ? AND es_propio = 1 ORDER BY id DESC LIMIT 1",
            (account_id, jid_bare(jid)))
        if not fila:
            return None
        return {"id": fila[0] or fila[1], "texto": fila[2] or ""}

    def enviar_marcador_leido(self, account_id: int, jid: str, id_mensaje: str):
        """Envía un marcador 'visto' (XEP-0333 displayed) del mensaje id_mensaje: el contacto sabe
        que lo has LEÍDO. Se manda al marcar el chat como leído."""
        if not id_mensaje:
            return
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return

        async def _enviar():
            try:
                cliente["xep_0333"].send_marker(jid_bare(jid), id_mensaje, "displayed")
            except Exception:
                return False
            return True
        try:
            self._run(_enviar(), timeout=10.0)
        except Exception:
            pass

    def mis_reacciones(self, account_id: int, message_id: str) -> set:
        """Mis emojis actuales sobre un mensaje (guardados bajo '__yo__')."""
        import json
        fila = db.query_one("SELECT reacciones_json FROM xmpp_mensajes "
                            "WHERE account_id = ? AND (stanza_id = ? OR origin_id = ?)",
                            (account_id, message_id, message_id))
        if not fila or not fila[0]:
            return set()
        try:
            return set(json.loads(fila[0]).get("__yo__", []))
        except Exception:
            return set()

    def enviar_reaccion(self, account_id: int, jid: str, message_id: str, emoji: str,
                        quitar: bool = False, es_muc: bool = False):
        """Reacciona (o quita la reacción) a un mensaje (XEP-0444). XMPP manda el CONJUNTO completo
        de tus reacciones, así que se calcula tu conjunto actual +/- el emoji y se reenvía entero."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)
        mias = self.mis_reacciones(account_id, message_id)
        if quitar:
            mias.discard(emoji)
        else:
            mias.add(emoji)

        async def _r():
            if es_muc:
                await self._asegurar_unido_muc(cliente, account_id, jid)
            msg = cliente.make_message(mto=jid, mtype=("groupchat" if es_muc else "chat"))
            cliente["xep_0444"].set_reactions(msg, message_id, mias)
            msg.send()
            return True
        self._run(_r(), timeout=60.0)  # el 1.er envío a una sala puede incluir la unión (join)
        # En 1:1 no hay eco: registro mi conjunto localmente. En MUC lo pinta el eco de la sala.
        if not es_muc:
            self._actualizar_reacciones(account_id, message_id, "__yo__", list(mias))

    def enviar_respuesta(self, account_id: int, jid: str, reply_id: str, texto: str,
                         es_muc: bool = False) -> str:
        """Responde a un mensaje concreto citándolo (XEP-0461)."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)
        texto = (texto or "").strip()
        if not texto:
            return ""
        oid = "sr-" + uuid.uuid4().hex
        cifrar = self.esta_cifrado(account_id, jid)

        async def _r():
            if es_muc:
                await self._asegurar_unido_muc(cliente, account_id, jid)
            msg = cliente.make_message(mto=jid, mbody=texto, mtype=("groupchat" if es_muc else "chat"))
            try:
                msg["reply"]["id"] = reply_id
                msg["reply"]["to"] = jid
                msg["origin_id"]["id"] = oid
            except Exception:
                pass
            if cifrar:
                destinatarios = await self._destinatarios_cifrado(cliente, jid, es_muc)
                enviado = await self._cifrar_y_enviar(cliente, msg, destinatarios)
                if not enviado:
                    from app.services.xmpp.xmpp_common import XMPPError
                    raise XMPPError("No se pudo cifrar la respuesta con OMEMO. No se envió en claro.")
                return True
            msg.send()
            return True
        self._run(_r(), timeout=60.0)  # el 1.er envío a una sala puede incluir la unión (join)
        if not es_muc:
            self._guardar_mensaje(account_id, jid, oid, self._user_jid(account_id),
                                  self._nombre_propio(account_id), texto, "msg", ahora_iso(),
                                  propio=True, leido=True, contar=False, origin_id=oid,
                                  reply_to=reply_id, cifrado=cifrar)
        return oid

    def retractar_mensaje(self, account_id: int, jid: str, message_id: str, es_muc: bool = False):
        """Retira (borra) un mensaje TUYO (XEP-0424). El local queda como '[Mensaje retirado]'."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _r():
            if es_muc:
                await self._asegurar_unido_muc(cliente, account_id, jid)
            cliente["xep_0424"].send_retraction(jid, message_id,
                                                mtype=("groupchat" if es_muc else "chat"))
            return True
        self._run(_r(), timeout=60.0)  # el 1.er envío a una sala puede incluir la unión (join)
        db.execute("UPDATE xmpp_mensajes SET texto = ?, editado = 1 "
                   "WHERE account_id = ? AND (stanza_id = ? OR origin_id = ?)",
                   (_("[Mensaje retirado]"), account_id, message_id, message_id))
        self._reindexar_fts(account_id, message_id, "")
        self._emit(account_id, "mensaje", chat_jid=jid, propio=True, cuenta=False)

    def enviar_estado_chat(self, account_id: int, jid: str, estado: str = "composing",
                           es_muc: bool = False):
        """Envía un estado de chat (composing/paused/active/gone) — el "está escribiendo" (0085).
        No se guarda ni suena; es efímero. En salas (es_muc) va como groupchat."""
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return
        jid = jid_bare(jid)

        async def _enviar():
            msg = cliente.make_message(mto=jid, mtype=("groupchat" if es_muc else "chat"))
            try:
                msg["chat_state"] = estado
            except Exception:
                return False
            msg.send()
            return True
        try:
            self._run(_enviar(), timeout=10.0)
        except Exception:
            pass

    def marcar_leido(self, account_id: int, jid: str):
        """Marca como leídos los mensajes de un chat (local) y envía un marcador 'visto' (XEP-0333)
        del último mensaje recibido, para que el contacto sepa que lo has leído."""
        jid = jid_bare(jid)
        # Antes de resetear, coge el stanza-id del último mensaje entrante para el marcador.
        ultimo = db.query_one(
            "SELECT stanza_id FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ? AND es_propio = 0 "
            "ORDER BY id DESC LIMIT 1", (account_id, jid))
        db.execute("UPDATE xmpp_chats SET unread_count = 0 WHERE id = ?",
                   (db_id_chat(account_id, jid),))
        db.execute("UPDATE xmpp_mensajes SET leido = 1 WHERE account_id = ? AND chat_jid = ? AND leido = 0",
                   (account_id, jid))
        if ultimo and ultimo[0]:
            self.enviar_marcador_leido(account_id, jid, ultimo[0])

    def _nombre_propio(self, account_id: int) -> str:
        """Nombre corto para mostrar tus propios mensajes."""
        jid = self._user_jid(account_id)
        return (jid or "").split("@", 1)[0] or _("Yo")
