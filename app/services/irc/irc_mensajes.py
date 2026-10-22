# -*- coding: utf-8 -*-
"""Envío de mensajes IRC (PRIVMSG / ACTION / NOTICE) y chats privados.

Mixin de IRCService. Los mensajes largos los divide la propia librería (pydle trocea por
el límite de 512 bytes del protocolo con margen, línea a línea), así que aquí solo se
valida, se envía y se guarda el mensaje PROPIO en la BD (nuestro on_message ignora el eco).
"""
from app.services.irc.irc_common import (
    Any, Dict, Optional, db, log, IRCError, quitar_formato_mirc, db_id_canal, ahora_iso)


class _MensajesMixin:
    def _cliente_conectado(self, account_id: int):
        with self._lock:
            cliente = self._clients.get(account_id)
        if cliente is None or not self.esta_conectada(account_id):
            raise IRCError("La red no está conectada. Conéctala desde el menú IRC.")
        return cliente

    def enviar_mensaje(self, account_id: int, destino: str, texto: str):
        """Envía un PRIVMSG (a un canal o a un nick). Bloqueante: llamar desde task_manager."""
        texto = (texto or "").strip()
        if not texto:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.message(destino, texto), timeout=30.0)
        es_privado = not destino.startswith(("#", "&", "+", "!"))
        self._guardar_mensaje(account_id, destino, "msg", cliente.nickname or "", texto,
                              propio=True, es_privado=es_privado)

    def enviar_action(self, account_id: int, destino: str, texto: str):
        """Envía un /me (CTCP ACTION)."""
        texto = (texto or "").strip()
        if not texto:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.ctcp(destino, "ACTION", texto), timeout=30.0)
        es_privado = not destino.startswith(("#", "&", "+", "!"))
        self._guardar_mensaje(account_id, destino, "action", cliente.nickname or "", texto,
                              propio=True, es_privado=es_privado)

    def enviar_notice(self, account_id: int, destino: str, texto: str):
        """Envía un NOTICE."""
        texto = (texto or "").strip()
        if not texto:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.notice(destino, texto), timeout=30.0)
        destino_bd = destino if destino.startswith(("#", "&", "+", "!")) else destino
        self._guardar_mensaje(account_id, destino_bd, "notice", cliente.nickname or "", texto,
                              propio=True, es_privado=not destino.startswith(("#", "&", "+", "!")))

    def abrir_privado(self, account_id: int, nick: str):
        """Crea (si no existe) el chat privado con un nick, para escribirle tú primero."""
        nick = (nick or "").strip().lstrip("@")
        if not nick:
            return None
        db.execute("""
            INSERT INTO irc_channels (id, account_id, name, joined, es_privado)
            VALUES (?, ?, ?, 0, 1)
            ON CONFLICT(id) DO NOTHING
        """, (db_id_canal(account_id, nick), account_id, nick))
        return db_id_canal(account_id, nick)

    def cambiar_nick(self, account_id: int, nuevo: str):
        """Cambia tu apodo en la red (en caliente)."""
        nuevo = (nuevo or "").strip()
        if not nuevo:
            return
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.set_nickname(nuevo), timeout=20.0)

    def cambiar_tema(self, account_id: int, canal: str, tema: str):
        """Cambia el tema de un canal (si tienes permiso)."""
        cliente = self._cliente_conectado(account_id)
        self._run(cliente.set_topic(canal, tema or ""), timeout=20.0)

    def comando_crudo(self, account_id: int, linea: str):
        """/quote: envía una línea cruda al servidor (para lo aún no implementado)."""
        linea = (linea or "").strip()
        if not linea:
            return
        cliente = self._cliente_conectado(account_id)
        partes = linea.split(" ")
        self._run(cliente.rawmsg(partes[0].upper(), *partes[1:]), timeout=20.0)

    def usuarios_de_canal(self, account_id: int, canal: str):
        """Nicks presentes en un canal con su prefijo legible (operador, voz...), ordenados:
        primero por rango y luego alfabético. Devuelve [(nick, etiqueta_rango)]."""
        try:
            cliente = self._cliente_conectado(account_id)
        except IRCError:
            return []
        info = (getattr(cliente, "channels", {}) or {}).get(canal) or {}
        usuarios = sorted(info.get("users") or [], key=str.lower)
        modos = info.get("modes") or {}
        rangos = [  # (letra de modo, etiqueta, peso)
            ("q", "fundador", 0), ("a", "administrador", 1), ("o", "operador", 2),
            ("h", "medio operador", 3), ("v", "voz", 4),
        ]
        salida = []
        for nick in usuarios:
            etiqueta, peso = "", 9
            for letra, nombre, p in rangos:
                lista = modos.get(letra) or []
                if nick in lista:
                    etiqueta, peso = nombre, p
                    break
            salida.append((nick, etiqueta, peso))
        salida.sort(key=lambda t: (t[2], t[0].lower()))
        return [(n, e) for n, e, _p in salida]

    def mi_nick(self, account_id: int) -> str:
        with self._lock:
            cliente = self._clients.get(account_id)
        return getattr(cliente, "nickname", "") or ""
