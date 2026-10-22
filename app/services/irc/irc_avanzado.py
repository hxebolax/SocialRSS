# -*- coding: utf-8 -*-
"""IRC avanzado (tanda I7): todo el potencial restante de pydle y del protocolo.

Estado propio (ausente/disponible, modos de usuario, hacerse operador de la red),
invitaciones, llamar a la puerta (knock), reentrar en un canal (cycle), WHOWAS de apodos
desconectados, CTCP a otros (PING con latencia, TIME, VERSION, CLIENTINFO), gestión de
modos de canal (moderado, solo invitación, tema bloqueado, secreto, clave, límite),
lista de baneos (ver y quitar) y wallops.
"""
import asyncio
import time as _t

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

from app.services.irc.irc_common import (
    Any, Dict, Optional, List, db, log, IRCError, quitar_formato_mirc)

# Modos de canal "de una letra" que ofrecemos en la interfaz, con su etiqueta legible.
MODOS_CANAL = (
    ("m", _("Moderado (solo con voz pueden hablar)")),
    ("i", _("Solo por invitación")),
    ("t", _("Tema bloqueado (solo operadores lo cambian)")),
    ("n", _("Sin mensajes desde fuera del canal")),
    ("s", _("Secreto (no aparece en la lista)")),
    ("p", _("Privado")),
)


class _AvanzadoMixin:
    # ---------------- Estado propio: ausente / disponible ----------------
    def fijar_ausente(self, account_id: int, mensaje: str = ""):
        """Marca tu estado como AUSENTE con un mensaje (los demás lo ven al escribirte o en whois)."""
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.away(mensaje or _("Ausente")), timeout=15.0)

    def volver_disponible(self, account_id: int):
        """Quita el estado de ausente (vuelves a estar disponible)."""
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.back(), timeout=15.0)

    def fijar_mis_modos(self, account_id: int, modos: str):
        """Cambia TUS modos de usuario (ej: '+i' invisible, '+w' recibir wallops, '-w')."""
        modos = (modos or "").strip()
        if not modos:
            return
        cliente = self._cliente_conectado(account_id)
        # MODE sobre tu propio nick.
        self._run(cliente.rawmsg("MODE", cliente.nickname, modos), timeout=15.0)

    def hacerse_operador(self, account_id: int, usuario: str, password: str):
        """Te conviertes en OPERADOR DE LA RED (IRCop) con las credenciales del servidor.
        Solo funciona si el administrador de la red te ha dado un bloque O:line."""
        if not usuario or not password:
            raise IRCError(_("Escribe el usuario y la contraseña de operador de la red."))
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.rawmsg("OPER", usuario, password), timeout=20.0)

    def enviar_wallops(self, account_id: int, texto: str):
        """Envía un WALLOPS (mensaje a todos los que reciben wallops; normalmente operadores)."""
        texto = (texto or "").strip()
        if not texto:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.rawmsg("WALLOPS", texto), timeout=15.0)

    # ---------------- Invitar / knock / cycle ----------------
    def invitar(self, account_id: int, canal: str, nick: str):
        """Invita a un usuario a un canal (útil en canales de solo invitación; necesitas rango)."""
        nick = (nick or "").strip().lstrip("@")
        if not canal or not nick:
            raise IRCError(_("Faltan el canal o el apodo."))
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.rawmsg("INVITE", nick, canal), timeout=15.0)

    def llamar_puerta(self, account_id: int, canal: str, motivo: str = ""):
        """Llama a la puerta (KNOCK) de un canal de solo invitación: pides que te dejen entrar."""
        if not canal:
            raise IRCError(_("Escribe el canal."))
        cliente = self._cliente_conectado(account_id)
        args = ["KNOCK", canal] + ([motivo] if motivo else [])
        self._run(cliente.rawmsg(*args), timeout=15.0)

    def reentrar_canal(self, account_id: int, canal: str):
        """Sale y vuelve a entrar en un canal (cycle): útil para refrescar permisos/estado."""
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.cycle(canal), timeout=20.0)

    # ---------------- WHOWAS (apodo desconectado) ----------------
    def whowas_legible(self, account_id: int, nick: str) -> str:
        """WHOWAS: datos históricos de un apodo que ya NO está conectado. Texto en español."""
        nick = (nick or "").strip().lstrip("@")
        if not nick:
            return ""
        cliente = self._cliente_conectado(account_id)

        async def _pedir():
            cliente._whowas_lineas = []
            cliente._whowas_evento = asyncio.Event()
            await cliente.rawmsg("WHOWAS", nick)
            try:
                await asyncio.wait_for(cliente._whowas_evento.wait(), timeout=12.0)
            except asyncio.TimeoutError:
                pass
            lineas = list(cliente._whowas_lineas or [])
            cliente._whowas_lineas = None
            cliente._whowas_evento = None
            return lineas

        lineas = self._run(_pedir(), timeout=15.0)
        if not lineas:
            return _("No hay datos anteriores de {0} (quizá nunca se conectó).").format(nick)
        return "\n".join([_("HISTORIAL DE {0}").format(nick), ""] + lineas)

    # ---------------- CTCP a otros (ping/lag, time, version, clientinfo) ----------------
    def ctcp_ping(self, account_id: int, nick: str) -> None:
        """Envía un CTCP PING para medir la LATENCIA con un usuario. La respuesta llega por
        on_ctcp_reply y se anuncia por el event_bus (no bloquea aquí)."""
        nick = (nick or "").strip().lstrip("@")
        if not nick:
            return
        cliente = self._cliente_conectado(account_id)
        marca = _t.time()
        try:
            cliente._ctcp_ping_pendiente[nick.lower()] = marca
        except Exception:
            pass
        # El contenido del PING es la marca de tiempo: los servidores/clientes la devuelven tal cual.
        self._run(cliente.ctcp(nick, "PING", f"{marca:.3f}"), timeout=15.0)

    def ctcp_a(self, account_id: int, nick: str, consulta: str) -> None:
        """Envía un CTCP arbitrario a un usuario (TIME, VERSION, CLIENTINFO...). La respuesta
        llega por on_ctcp_reply y se anuncia."""
        nick = (nick or "").strip().lstrip("@")
        consulta = (consulta or "").strip().upper()
        if not nick or not consulta:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.ctcp(nick, consulta), timeout=15.0)

    # ---------------- Modos de canal ----------------
    def fijar_modo_canal(self, account_id: int, canal: str, modo: str, argumento: str = ""):
        """Aplica un modo de canal (ej: '+m', '-i', '+k clave', '+l 50'). El servidor valida
        que seas operador. `argumento` para los modos con parámetro (clave, límite)."""
        modo = (modo or "").strip()
        if not modo:
            return
        cliente = self._cliente_conectado(account_id)
        args = [canal, modo] + ([argumento] if argumento else [])
        self._run(cliente.rawmsg("MODE", *args), timeout=15.0)

    def fijar_clave_canal(self, account_id: int, canal: str, clave: str):
        """Pone (o quita, si clave vacía) la CLAVE de un canal (+k / -k)."""
        if clave:
            self.fijar_modo_canal(account_id, canal, "+k", clave)
        else:
            self.fijar_modo_canal(account_id, canal, "-k", "*")

    def fijar_limite_canal(self, account_id: int, canal: str, limite: int):
        """Pone (o quita, si limite<=0) el LÍMITE de usuarios de un canal (+l / -l)."""
        if limite and limite > 0:
            self.fijar_modo_canal(account_id, canal, "+l", str(int(limite)))
        else:
            self.fijar_modo_canal(account_id, canal, "-l")

    def modos_actuales_canal(self, account_id: int, canal: str) -> Dict[str, Any]:
        """Modos activos del canal desde el estado del cliente (los que pydle ya conoce):
        {'letras': 'mnt', 'clave': '...', 'limite': 50}."""
        try:
            cliente = self._cliente_conectado(account_id)
        except IRCError:
            return {}
        info = (getattr(cliente, "channels", {}) or {}).get(canal) or {}
        modos = info.get("modes") or {}
        letras = ""
        clave, limite = "", 0
        for k, v in modos.items():
            if k in ("k",):
                clave = v if isinstance(v, str) else (v[0] if v else "")
            elif k in ("l",):
                try:
                    limite = int(v if not isinstance(v, (list, tuple)) else v[0])
                except Exception:
                    limite = 0
            elif len(k) == 1 and not isinstance(v, (list, set, tuple)):
                letras += k
        return {"letras": "".join(sorted(set(letras))), "clave": clave, "limite": limite}

    # ---------------- Lista de baneos ----------------
    def lista_baneos(self, account_id: int, canal: str, timeout: float = 15.0) -> List[Dict[str, Any]]:
        """Pide la lista de baneos del canal (MODE +b) y devuelve [{mascara, por, cuando}]."""
        cliente = self._cliente_conectado(account_id)

        async def _pedir():
            cliente._baneos_lista = []
            cliente._baneos_evento = asyncio.Event()
            await cliente.rawmsg("MODE", canal, "+b")
            try:
                await asyncio.wait_for(cliente._baneos_evento.wait(), timeout=timeout - 3)
            except asyncio.TimeoutError:
                pass
            lista = list(cliente._baneos_lista or [])
            cliente._baneos_lista = None
            cliente._baneos_evento = None
            return lista

        return self._run(_pedir(), timeout=timeout)

    def quitar_baneo(self, account_id: int, canal: str, mascara: str):
        """Quita un baneo por su máscara (MODE -b máscara)."""
        mascara = (mascara or "").strip()
        if not mascara:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.rawmsg("MODE", canal, "-b", mascara), timeout=15.0)

    def banear_mascara(self, account_id: int, canal: str, mascara: str):
        """Añade un baneo por máscara libre (MODE +b máscara; ej: *!*@host.com)."""
        mascara = (mascara or "").strip()
        if not mascara:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.rawmsg("MODE", canal, "+b", mascara), timeout=15.0)

    # ---------------- METADATA (IRCv3, best-effort) ----------------
    # Claves estándar de metadata que un cliente puede publicar sobre sí mismo. Muy pocos
    # servidores lo soportan (Ergo con draft/metadata-2); si no, el servidor responde con error
    # y se avisa con claridad.
    CLAVES_METADATA = ("display-name", "avatar", "status", "homepage", "color", "bot")

    def fijar_metadata(self, account_id: int, clave: str, valor: str):
        """Publica un dato de METADATA sobre TU apodo (display-name, avatar, status, homepage...).
        Best-effort: si el servidor no lo soporta, lanza un error legible."""
        clave = (clave or "").strip()
        if not clave:
            return
        cliente = self._cliente_conectado(account_id)
        try:
            if valor:
                self._run(cliente.set_metadata("*", clave, valor), timeout=15.0)
            else:
                self._run(cliente.unset_metadata("*", clave), timeout=15.0)
        except IRCError:
            raise
        except Exception as e:
            raise IRCError(_("Este servidor no admite metadata (o rechazó el dato): {0}").format(e))

    def leer_metadata(self, account_id: int, nick: str = "*") -> str:
        """Lee la metadata publicada por un apodo (o la tuya con '*'). Devuelve texto legible.
        Best-effort: el soporte de lectura de metadata en la librería es de borrador y en muchos
        servidores no devuelve datos utilizables; en ese caso se dice con claridad."""
        cliente = self._cliente_conectado(account_id)
        try:
            datos = self._run(cliente.get_metadata(nick or "*"), timeout=15.0)
        except Exception as e:
            return _("Este servidor no admite leer metadata: {0}").format(e)
        # Solo un diccionario clave→valor es un resultado útil (la librería a veces devuelve un
        # objeto interno sin resolver): en cualquier otro caso, avisar con honestidad.
        if isinstance(datos, dict) and datos:
            lineas = [_("METADATA de {0}").format(nick), ""]
            for k, v in datos.items():
                lineas.append(f"{k}: {v}")
            return "\n".join(lineas)
        return _("Este servidor no devolvió metadata legible (su soporte de lectura es limitado). "
                 "Publicar tu propia metadata sí funciona en los servidores que la admiten.")
