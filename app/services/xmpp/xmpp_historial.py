# -*- coding: utf-8 -*-
"""Historial y sincronización de XMPP (tanda X3): MAM (XEP-0313) con paginación RSM (XEP-0059),
catch-up al conectar/abrir un chat, poda por días y búsqueda FTS. El stanza-id estable (XEP-0359)
que el servidor archiva es el MISMO que llega en vivo, así el dedupe UNIQUE(account_id, stanza_id)
cubre a la vez el histórico, los Carbons y el eco propio.
"""
from datetime import datetime, timedelta, timezone

from app.core.config_manager import config
from app.services.xmpp.xmpp_common import (
    Any, Dict, Optional, List, db, log, XMPPError, ahora_iso, delay_a_iso, jid_bare)


class _HistorialMixin:
    def _mam_disponible(self, account_id: int) -> bool:
        try:
            cliente = self._cliente_conectado(account_id)
            return cliente.plugin.get("xep_0313") is not None
        except Exception:
            return False

    def catch_up(self, account_id: int, jid: str, limite: int = 50) -> int:
        """Trae del archivo del servidor (MAM) los mensajes de un chat MÁS RECIENTES que el último
        que tenemos guardado. Devuelve cuántos nuevos entraron. Si el servidor no tiene MAM, 0."""
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return 0
        jid = jid_bare(jid)
        fila = db.query_one(
            "SELECT date FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ? ORDER BY id DESC LIMIT 1",
            (account_id, jid))
        inicio = self._iso_a_dt(fila[0]) if fila and fila[0] else None
        return self._consulta_mam(account_id, cliente, jid, start=inicio, limite=limite)

    def cargar_anteriores(self, account_id: int, jid: str, limite: int = 40) -> int:
        """Trae una página de mensajes MÁS ANTIGUOS (paginación hacia atrás con RSM 'before'),
        usando como cursor el stanza-id más viejo que tenemos de ese chat."""
        try:
            cliente = self._cliente_conectado(account_id)
        except XMPPError:
            return 0
        jid = jid_bare(jid)
        fila = db.query_one(
            "SELECT stanza_id FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ? ORDER BY id ASC LIMIT 1",
            (account_id, jid))
        antes = (fila[0] if fila and fila[0] else "")
        return self._consulta_mam(account_id, cliente, jid, before=antes, limite=limite)

    def _consulta_mam(self, account_id: int, cliente, jid: str, start=None, before=None,
                      limite: int = 50) -> int:
        rsm = {"max": max(1, int(limite))}
        if before is not None:
            rsm["before"] = before  # página anterior a este id ('' = la última página)

        async def _q():
            return await cliente["xep_0313"].retrieve(with_jid=jid, start=start, rsm=rsm,
                                                      timeout=45)
        try:
            iq = self._run(_q(), timeout=60.0)
        except Exception as e:
            log.debug(f"XMPP: MAM no disponible o falló: {e}")
            return 0
        nuevos = 0
        yo = jid_bare(str(cliente.boundjid))
        try:
            resultados = iq["mam"]["results"] or []
        except Exception:
            resultados = []
        for result in resultados:
            try:
                fwd = result["forwarded"]
                m = fwd["stanza"]
                if m is None or (m["body"] or "") == "":
                    continue
                archivo_id = result["id"] or ""
                stanza_id = self._stanza_id_de(m) or archivo_id
                if not stanza_id:
                    continue
                desde = jid_bare(str(m["from"]))
                propio = (desde == yo)
                chat_jid = jid_bare(str(m["to"])) if propio else desde
                fecha = delay_a_iso(fwd["delay"]) if fwd["delay"] else ahora_iso()
                media_url, etiqueta, es_voz = self._media_de(m)
                texto = etiqueta if media_url else (m["body"] or "")
                tipo = "media" if media_url else "msg"
                if self._guardar_mensaje(account_id, chat_jid, stanza_id, desde,
                                         self._nombre_chat(account_id, chat_jid if propio else desde),
                                         texto, tipo, fecha, propio,
                                         leido=True, contar=False,
                                         media_url=media_url or "", es_voz=es_voz):
                    nuevos += 1
            except Exception as e:
                log.debug(f"XMPP: resultado MAM no procesado: {e}")
        if nuevos:
            self._emit(account_id, "historial", chat_jid=jid, nuevos=nuevos)
        return nuevos

    @staticmethod
    def _iso_a_dt(iso_str) -> Optional[datetime]:
        try:
            dt = datetime.fromisoformat((iso_str or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    # ---------------- Búsqueda (FTS con fallback LIKE) ----------------
    def buscar_mensajes_fts(self, account_id: int, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        salida = []
        try:
            filas = db.query("""
                SELECT m.id, m.chat_jid, COALESCE(NULLIF(m.sender_nombre,''), m.sender), m.texto, m.date
                FROM xmpp_mensajes_fts f JOIN xmpp_mensajes m ON m.id = f.msg_db_id
                WHERE xmpp_mensajes_fts MATCH ? AND m.account_id = ?
                ORDER BY m.id DESC LIMIT ?
            """, (self._consulta_fts_xmpp(query), account_id, limit))
        except Exception:
            filas = db.query("""
                SELECT id, chat_jid, COALESCE(NULLIF(sender_nombre,''), sender), texto, date
                FROM xmpp_mensajes WHERE account_id = ? AND texto LIKE ? AND tipo IN ('msg','accion','aviso')
                ORDER BY id DESC LIMIT ?
            """, (account_id, f"%{query}%", limit))
        for mid, chat_jid, quien, texto, fecha in filas:
            salida.append({"db_id": mid, "chat_jid": chat_jid, "quien": quien,
                           "texto": texto, "date": fecha})
        return salida

    @staticmethod
    def _consulta_fts_xmpp(query: str) -> str:
        terminos = [t.replace('"', "") for t in (query or "").split() if t.strip('"')]
        return " ".join(f'"{t}"' for t in terminos) if terminos else '""'

    # ---------------- Poda ----------------
    def podar_historial(self, account_id: Optional[int] = None) -> int:
        """Borra mensajes más antiguos que config xmpp.history_days (0 = nunca)."""
        try:
            dias = int(config.get("xmpp", "history_days", 0) or 0)
        except Exception:
            dias = 0
        if dias <= 0:
            return 0
        limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        try:
            cond, params = "date < ?", [limite]
            if account_id:
                cond += " AND account_id = ?"
                params.append(account_id)
            viejos = [f[0] for f in db.query(f"SELECT id FROM xmpp_mensajes WHERE {cond}", tuple(params))]
            if not viejos:
                return 0
            for i in range(0, len(viejos), 500):
                lote = viejos[i:i + 500]
                ph = ",".join("?" * len(lote))
                with db.transaccion():
                    db.execute(f"DELETE FROM xmpp_mensajes_fts WHERE msg_db_id IN ({ph})", tuple(lote))
                    db.execute(f"DELETE FROM xmpp_mensajes WHERE id IN ({ph})", tuple(lote))
            log.info(f"XMPP: poda de historial, {len(viejos)} mensajes de más de {dias} días.")
            return len(viejos)
        except Exception as e:
            log.error(f"XMPP: error en la poda: {e}")
            return 0

    def vaciar_conversacion(self, account_id: int, jid: str) -> int:
        """Borra los MENSAJES de un chat (y su índice FTS) SIN quitar el contacto/grupo del roster.
        Resetea el resumen y el contador de no leídos. Devuelve nº de mensajes borrados."""
        from app.services.xmpp.xmpp_common import db_id_chat
        jid = jid_bare(jid)
        ids = [r[0] for r in db.query(
            "SELECT id FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ?", (account_id, jid))]
        n = len(ids)
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" * len(lote))
            with db.transaccion():
                db.execute(f"DELETE FROM xmpp_mensajes_fts WHERE msg_db_id IN ({ph})", tuple(lote))
                db.execute(f"DELETE FROM xmpp_mensajes WHERE id IN ({ph})", tuple(lote))
        db.execute("UPDATE xmpp_chats SET last_message = '', last_message_date = '', unread_count = 0 "
                   "WHERE id = ?", (db_id_chat(account_id, jid),))
        self._emit(account_id, "mensaje", chat_jid=jid, propio=False, cuenta=False)
        return n

    def exportar_conversacion(self, account_id: int, jid: str, ruta: str) -> int:
        """Exporta el historial local de un chat (1:1 o sala) a un .txt legible. Nº de mensajes."""
        from app.services.xmpp.xmpp_common import db_id_chat, nombre_desde_jid
        fila = db.query_one("SELECT nombre FROM xmpp_chats WHERE id = ?",
                            (db_id_chat(account_id, jid),))
        nombre = (fila[0] if fila and fila[0] else nombre_desde_jid(jid))
        filas = db.query(
            "SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto, tipo, date, "
            "COALESCE(editado,0), COALESCE(reacciones,''), COALESCE(cifrado,0) "
            "FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ? ORDER BY id ASC",
            (account_id, jid))
        lineas = [_("Historial de {0} — exportado por SocialRSS Suite").format(nombre), ""]
        from app.services.xmpp.xmpp_common import hora_local
        for quien, texto, tipo, fecha, editado, reacciones, cifrado in filas:
            marca = hora_local(fecha, con_fecha=True)
            extra = ""
            if cifrado:
                extra += " " + _("(cifrado)")
            if editado:
                extra += " " + _("(editado)")
            if reacciones:
                extra += " " + _("[Reacciones: {0}]").format(reacciones)
            lineas.append(f"[{marca}] <{quien}> {texto}{extra}")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas))
        return len(filas)
