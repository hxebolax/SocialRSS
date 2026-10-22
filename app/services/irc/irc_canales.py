# -*- coding: utf-8 -*-
"""Canales y usuarios de IRC: whois legible, lista de canales del servidor (/LIST),
acciones de operador (op/voz/expulsar/banear), ignorados y niveles de aviso.

Mixin de IRCService (tanda I3).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext (el whois legible genera texto para la UI)

from app.core.config_manager import config
from app.services.irc.irc_common import (
    asyncio, Any, Dict, Optional, List, db, log, IRCError, quitar_formato_mirc)


class _CanalesMixin:
    # ---------------- WHOIS legible ----------------
    def whois_legible(self, account_id: int, nick: str) -> str:
        """Hace un WHOIS y devuelve un texto legible en español (para el visor)."""
        nick = (nick or "").strip().lstrip("@")
        if not nick:
            return ""
        cliente = self._cliente_conectado(account_id)
        info = self._run(cliente.whois(nick), timeout=15.0)
        if not info:
            return _("No hay información de {0} (¿existe ese apodo?).").format(nick)
        lineas = [_("DATOS DE {0}").format(nick), ""]
        if info.get("username") or info.get("hostname"):
            lineas.append(_("Identidad: {0}@{1}").format(info.get("username", ""), info.get("hostname", "")))
        if info.get("realname"):
            lineas.append(_("Nombre real: {0}").format(quitar_formato_mirc(str(info.get("realname")))))
        if info.get("account"):
            lineas.append(_("Cuenta registrada (servicios): {0}").format(info.get("account")))
        elif info.get("identified"):
            lineas.append(_("Identificado con los servicios de la red."))
        if info.get("away", False):
            msg = info.get("away_message") or ""
            lineas.append(_("Ausente: {0}").format(quitar_formato_mirc(str(msg))) if msg else _("Ausente."))
        if info.get("server"):
            servidor = info.get("server")
            extra = info.get("server_info") or ""
            lineas.append(_("Servidor: {0} {1}").format(servidor, f"({extra})" if extra else "").strip())
        if info.get("oper"):
            lineas.append(_("Es operador/a de la RED (IRCop)."))
        if info.get("secure"):
            lineas.append(_("Conexión segura (TLS)."))
        canales = sorted(info.get("channels") or [], key=str.lower)
        if canales:
            lineas.append(_("Canales ({0}): {1}").format(len(canales), ", ".join(canales)))
        if info.get("idle"):
            try:
                seg = int(info["idle"])
                lineas.append(_("Inactivo desde hace {0} minutos.").format(seg // 60))
            except Exception:
                pass
        if len(lineas) <= 2:
            lineas.append(_("Sin más datos."))
        return "\n".join(lineas)

    # ---------------- Lista de canales del servidor (/LIST) ----------------
    def canales_del_servidor(self, account_id: int, timeout: float = 45.0) -> List[Dict[str, Any]]:
        """Pide /LIST y devuelve [{name, users, topic}] (ordenada por usuarios, de más a menos).
        OJO: en redes grandes puede tardar y devolver miles de canales; el diálogo filtra."""
        cliente = self._cliente_conectado(account_id)

        async def _pedir():
            cliente._list_resultado = []
            cliente._list_terminado = asyncio.Event()
            await cliente.rawmsg("LIST")
            try:
                await asyncio.wait_for(cliente._list_terminado.wait(), timeout=timeout - 5)
            except asyncio.TimeoutError:
                pass  # devolver lo acumulado hasta ahora
            return list(cliente._list_resultado)

        filas = self._run(_pedir(), timeout=timeout)
        filas.sort(key=lambda d: -(d.get("users") or 0))
        return filas

    # ---------------- Acciones de canal / operador ----------------
    def accion_usuario_canal(self, account_id: int, canal: str, nick: str, accion: str, motivo: str = ""):
        """Acciones de operador sobre un usuario del canal: op, deop, voice, devoice,
        kick, ban, bankick. Requiere tener rango en el canal (si no, el servidor lo dirá)."""
        cliente = self._cliente_conectado(account_id)
        modos = {"op": "+o", "deop": "-o", "voice": "+v", "devoice": "-v"}
        if accion in modos:
            self._run(cliente.set_mode(canal, modos[accion], nick), timeout=20.0)
        elif accion == "kick":
            self._run(cliente.kick(canal, nick, motivo or None), timeout=20.0)
        elif accion == "ban":
            self._run(cliente.ban(canal, nick), timeout=20.0)
        elif accion == "bankick":
            # kickban de pydle: banea y expulsa en un solo paso (más limpio que ban + kick sueltos).
            self._run(cliente.kickban(canal, nick, reason=(motivo or None)), timeout=20.0)
        else:
            raise IRCError(f"Acción desconocida: {accion}")

    def mi_rango_en(self, account_id: int, canal: str) -> str:
        """Tu rango legible en un canal ('' si ninguno)."""
        yo = self.mi_nick(account_id)
        for nick, etiqueta in self.usuarios_de_canal(account_id, canal):
            if nick.lower() == yo.lower():
                return etiqueta
        return ""

    def soy_operador(self, account_id: int, canal: str) -> bool:
        return self.mi_rango_en(account_id, canal) in ("fundador", "administrador", "operador", "medio operador")

    # ---------------- Ignorados ----------------
    # Guardados en config: {"<account_id>": ["nick1", ...]} (en minúsculas). Un ignorado no se
    # guarda, no suena, no cuenta ni aparece: como si no hablara (el /ignore de toda la vida).
    def _ignorados_todos(self) -> Dict[str, List[str]]:
        datos = config.get("irc", "ignored", {})
        return datos if isinstance(datos, dict) else {}

    def ignorados(self, account_id: int) -> List[str]:
        return list(self._ignorados_todos().get(str(account_id), []))

    def esta_ignorado(self, account_id: int, nick: str) -> bool:
        return (nick or "").lower() in self._ignorados_todos().get(str(account_id), [])

    def ignorar(self, account_id: int, nick: str, valor: bool = True):
        nick = (nick or "").lower().strip()
        if not nick:
            return
        datos = self._ignorados_todos()
        lista = list(datos.get(str(account_id), []))
        if valor and nick not in lista:
            lista.append(nick)
        elif not valor and nick in lista:
            lista.remove(nick)
        datos[str(account_id)] = lista
        config.set("irc", "ignored", datos)

    # ---------------- Niveles de aviso y eventos por canal ----------------
    def nivel_aviso(self, account_id: int, canal: str) -> str:
        """Nivel de aviso sonoro del canal: 'all' (todo), 'mentions' (solo menciones), 'none'."""
        fila = db.query_one("SELECT COALESCE(notify_level,'all') FROM irc_channels "
                            "WHERE account_id = ? AND name = ?", (account_id, (canal or "").lower()))
        return fila[0] if fila and fila[0] in ("all", "mentions", "none") else "all"

    def fijar_nivel_aviso(self, account_id: int, canal: str, nivel: str):
        if nivel not in ("all", "mentions", "none"):
            return
        db.execute("UPDATE irc_channels SET notify_level = ? WHERE account_id = ? AND name = ?",
                   (nivel, account_id, (canal or "").lower()))

    def mostrar_eventos(self, account_id: int, canal: str) -> bool:
        """¿Se muestran entradas/salidas (join/part/quit) en la conversación de este canal? El ajuste
        GLOBAL 'irc.ocultar_eventos' (Preferencias) los oculta en TODOS los canales; si está
        desactivado, decide el ajuste por canal (show_events, alternable con /events)."""
        try:
            from app.core.config_manager import config
            if config.get("irc", "ocultar_eventos", False):
                return False
        except Exception:
            pass
        fila = db.query_one("SELECT COALESCE(show_events,1) FROM irc_channels "
                            "WHERE account_id = ? AND name = ?", (account_id, (canal or "").lower()))
        return bool(fila[0]) if fila else True

    def fijar_mostrar_eventos(self, account_id: int, canal: str, valor: bool):
        db.execute("UPDATE irc_channels SET show_events = ? WHERE account_id = ? AND name = ?",
                   (1 if valor else 0, account_id, (canal or "").lower()))
