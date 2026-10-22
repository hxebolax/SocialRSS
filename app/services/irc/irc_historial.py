# -*- coding: utf-8 -*-
"""Historial de IRC: búsqueda FTS, exportar conversaciones, poda y amigos (monitor).

Mixin de IRCService (tanda I5).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

from datetime import datetime, timedelta, timezone

from app.core.config_manager import config
from app.services.irc.irc_common import (
    Any, Dict, Optional, List, db, log, IRCError)


class _HistorialMixin:
    # ---------------- Búsqueda ----------------
    def buscar_mensajes_fts(self, account_id: int, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Busca en el historial local (FTS5; si no está disponible cae a LIKE).
        Devuelve [{db_id, channel, nick, text, date}] de más nuevo a más viejo."""
        salida = []
        try:
            filas = db.query("""
                SELECT m.id, m.channel, m.nick, m.text, m.date
                FROM irc_messages_fts f JOIN irc_messages m ON m.id = f.msg_db_id
                WHERE irc_messages_fts MATCH ? AND m.account_id = ?
                ORDER BY m.id DESC LIMIT ?
            """, (self._consulta_fts(query), account_id, limit))
        except Exception:
            filas = db.query("""
                SELECT id, channel, nick, text, date FROM irc_messages
                WHERE account_id = ? AND text LIKE ? AND tipo IN ('msg','action','notice')
                ORDER BY id DESC LIMIT ?
            """, (account_id, f"%{query}%", limit))
        for mid, canal, nick, texto, fecha in filas:
            salida.append({"db_id": mid, "channel": canal, "nick": nick,
                           "text": texto, "date": fecha})
        return salida

    @staticmethod
    def _consulta_fts(query: str) -> str:
        """Convierte el texto del usuario en una consulta FTS segura (términos entre comillas
        con AND implícito; los operadores crudos del usuario no rompen la sintaxis)."""
        terminos = [t.replace('"', "") for t in (query or "").split() if t.strip('"')]
        return " ".join(f'"{t}"' for t in terminos) if terminos else '""'

    # ---------------- Exportar ----------------
    def exportar_chat(self, account_id: int, canal: str, ruta: str) -> int:
        """Exporta el historial local de un chat a un .txt legible. Devuelve nº de líneas."""
        filas = db.query("""
            SELECT nick, text, tipo, date FROM irc_messages
            WHERE account_id = ? AND channel = ? ORDER BY id ASC
        """, (account_id, (canal or "").lower()))
        lineas = [_("Historial de {0} — exportado por SocialRSS Suite").format(canal), ""]
        for nick, texto, tipo, fecha in filas:
            marca = (fecha or "")[:16].replace("T", " ")
            if tipo == "action":
                lineas.append(f"[{marca}] * {nick} {texto}")
            elif tipo == "join":
                lineas.append(f"[{marca}] --> {nick} " + _("ha entrado"))
            elif tipo in ("part", "quit"):
                lineas.append(f"[{marca}] <-- {nick} " + _("ha salido"))
            elif tipo == "kick":
                lineas.append(f"[{marca}] <-- {nick} " + _("ha expulsado a {0}").format(texto or ""))
            elif tipo == "topic":
                lineas.append(f"[{marca}] *** {nick} " + _("cambió el tema a: {0}").format(texto or ""))
            elif tipo == "notice":
                lineas.append(f"[{marca}] -{nick or _('Servidor')}- {texto}")
            else:
                lineas.append(f"[{marca}] <{nick}> {texto}")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas))
        return len(filas)

    # ---------------- Poda ----------------
    def podar_historial(self, account_id: Optional[int] = None) -> int:
        """Borra mensajes más antiguos que config irc.history_days (0 = conservar siempre).
        Devuelve cuántos borró. Se llama al conectar (mantenimiento silencioso)."""
        try:
            dias = int(config.get("irc", "history_days", 0) or 0)
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
            viejos = [f[0] for f in db.query(f"SELECT id FROM irc_messages WHERE {cond}", tuple(params))]
            if not viejos:
                return 0
            marcadores = ",".join("?" * len(viejos))
            db.execute(f"DELETE FROM irc_messages_fts WHERE msg_db_id IN ({marcadores})", tuple(viejos))
            db.execute(f"DELETE FROM irc_messages WHERE id IN ({marcadores})", tuple(viejos))
            log.info(f"IRC: poda de historial, {len(viejos)} mensajes de más de {dias} días.")
            return len(viejos)
        except Exception as e:
            log.error(f"IRC: error en la poda del historial: {e}")
            return 0

    # ---------------- Amigos (MONITOR) ----------------
    # Lista de apodos a vigilar por red (config irc.friends {acc: [nicks]}). Al conectar se
    # registran con MONITOR y la red avisa al instante cuando entran o salen.
    def amigos(self, account_id: int) -> List[str]:
        datos = config.get("irc", "friends", {})
        return list(datos.get(str(account_id), [])) if isinstance(datos, dict) else []

    def fijar_amigos(self, account_id: int, nicks: List[str]):
        datos = config.get("irc", "friends", {})
        if not isinstance(datos, dict):
            datos = {}
        limpios = []
        for n in nicks:
            n = (n or "").strip().lstrip("@")
            if n and n.lower() not in [x.lower() for x in limpios]:
                limpios.append(n)
        anteriores = list(datos.get(str(account_id), []))
        datos[str(account_id)] = limpios
        config.set("irc", "friends", datos)
        # Si la red está conectada, sincronizar el monitor en caliente: añadir los nuevos y
        # DES-MONITORIZAR los que se quitaron (antes se quedaban vigilados hasta reconectar).
        try:
            if self.esta_conectada(account_id):
                with self._lock:
                    cliente = self._clients.get(account_id)
                if cliente is not None:
                    quitados = [n for n in anteriores
                                if n.lower() not in [x.lower() for x in limpios]]

                    async def _sync():
                        for n in limpios:
                            await cliente.monitor(n)
                        for n in quitados:
                            try:
                                await cliente.unmonitor(n)
                            except Exception:
                                pass
                    self._run(_sync(), timeout=20.0)
        except Exception as e:
            log.debug(f"IRC: no se pudo sincronizar el monitor: {e}")