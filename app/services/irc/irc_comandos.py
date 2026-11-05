# -*- coding: utf-8 -*-
"""Comandos de barra del cliente IRC (/me, /join, /mode...) independientes de la interfaz.

Una interfaz que no sea la de wxPython (la de GTK) llama a `ejecutar_comando` desde un hilo de
segundo plano: el servicio habla con la red y la función devuelve qué debe mostrarse, si algo.
"""
from typing import Any, Dict, Optional

from app.services.irc.irc_common import IRCError

AYUDA_COMANDOS = _(
    "Comandos disponibles:\n"
    "/me <texto> — acción (\"* apodo hace algo\")\n"
    "/msg <destino> <texto> — mensaje a un canal o apodo\n"
    "/query <apodo> — abrir un chat privado\n"
    "/notice <destino> <texto> — enviar un aviso (notice)\n"
    "/join <#canal> — unirte a un canal\n"
    "/part [#canal] — salir del canal\n"
    "/cycle [#canal] — salir y volver a entrar\n"
    "/nick <apodo> — cambiar tu apodo\n"
    "/topic <texto> — cambiar el tema del canal\n"
    "/away [mensaje] — marcarte como ausente\n"
    "/back — volver a estar disponible\n"
    "/invite <apodo> [#canal] — invitar a alguien al canal\n"
    "/knock <#canal> [motivo] — pedir entrar en un canal de solo invitación\n"
    "/whowas <apodo> — datos de un apodo ya desconectado\n"
    "/ping <apodo> — medir la latencia con alguien (CTCP PING)\n"
    "/ctcp <apodo> <consulta> — CTCP a alguien (TIME, VERSION, CLIENTINFO...)\n"
    "/mode [#canal] <modos> [arg] — cambiar modos del canal (+m, +i, +k clave, +l 50...)\n"
    "/kick <apodo> [motivo] — expulsar del canal\n"
    "/ban <máscara> — banear por máscara (ej: *!*@host)\n"
    "/unban <máscara> — quitar un baneo\n"
    "/oper <usuario> <contraseña> — hacerte operador de la red\n"
    "/wallops <texto> — mensaje a quienes reciben wallops\n"
    "/umode <modos> — cambiar tus modos de usuario (+i invisible, +w wallops...)\n"
    "/metadata [clave valor|ver] — publicar/ver tu metadata (si el servidor lo admite)\n"
    "/clear — vaciar la vista (el historial local no se borra)\n"
    "/events [on|off] — mostrar u ocultar las entradas y salidas (fulano entró/salió) de este canal\n"
    "/quote <línea> — enviar una orden cruda al servidor\n"
    "/help — esta ayuda"
)

_PREFIJOS_CANAL = ("#", "&", "+", "!")


def _uso(cmd: str, forma: str) -> IRCError:
    return IRCError(_("Uso: /{c} {f}").format(c=cmd, f=forma))


def ejecutar_comando(account_id: int, canal: str, linea: str) -> Optional[Dict[str, Any]]:
    """Ejecuta un comando de barra en el contexto del chat `canal` ("*" es la consola del servidor).

    Bloquea mientras habla con la red: llamar desde segundo plano. Lanza IRCError si el comando no
    es válido o falla. Devuelve None si no hay nada que mostrar o un dict con una de estas claves:
      texto/titulo      información para enseñar en un cuadro (ayuda, whowas, metadata)
      privado           apodo cuyo chat privado hay que abrir (/query)
      limpiar           vaciar la vista (/clear)
      recargar, aviso   la lista debe leerse de nuevo y qué decir (/events)
    """
    from app.services.irc import irc_service as srv

    partes = linea.strip().split(" ", 1)
    cmd = partes[0][1:].lower()
    resto = (partes[1] if len(partes) > 1 else "").strip()
    primero = resto.split(" ")[0] if resto else ""

    if cmd == "help":
        return {"texto": AYUDA_COMANDOS, "titulo": _("Comandos de IRC")}
    if cmd == "clear":
        return {"limpiar": True}
    if cmd == "me":
        if resto:
            srv.enviar_action(account_id, canal, resto)
    elif cmd in ("msg", "notice"):
        destino, _sep, texto = resto.partition(" ")
        if not destino or not texto.strip():
            raise _uso(cmd, _("destino texto"))
        (srv.enviar_notice if cmd == "notice" else srv.enviar_mensaje)(account_id, destino, texto)
    elif cmd == "query":
        if not primero:
            raise _uso(cmd, _("apodo"))
        srv.abrir_privado(account_id, primero)
        return {"privado": primero}
    elif cmd == "events":
        if not canal.startswith(_PREFIJOS_CANAL):
            raise IRCError(_("Las entradas y salidas solo aplican a los canales."))
        actual = srv.mostrar_eventos(account_id, canal)
        nuevo = (resto.lower() not in ("off", "no", "0", "ocultar")) if resto else (not actual)
        srv.fijar_mostrar_eventos(account_id, canal, nuevo)
        return {"recargar": True, "aviso": _("Se mostrarán las entradas y salidas.") if nuevo
                else _("Se ocultan las entradas y salidas de este canal.")}
    elif cmd == "join":
        if not primero:
            raise _uso(cmd, "#canal")
        srv.unirse_canal(account_id, primero)
    elif cmd == "part":
        srv.salir_canal(account_id, primero or canal)
    elif cmd == "cycle":
        srv.reentrar_canal(account_id, primero or canal)
    elif cmd == "nick":
        if not primero:
            raise _uso(cmd, _("apodo"))
        srv.cambiar_nick(account_id, primero)
    elif cmd == "topic":
        srv.cambiar_tema(account_id, canal, resto)
    elif cmd == "quote":
        if not resto:
            raise _uso(cmd, _("línea"))
        srv.comando_crudo(account_id, resto)
    elif cmd == "away":
        srv.fijar_ausente(account_id, resto)
    elif cmd == "back":
        srv.volver_disponible(account_id)
    elif cmd == "invite":
        if not primero:
            raise _uso(cmd, _("apodo [#canal]"))
        sub = resto.split(" ")
        srv.invitar(account_id, sub[1] if len(sub) > 1 else canal, sub[0])
    elif cmd == "knock":
        sub = resto.split(" ", 1)
        srv.llamar_puerta(account_id, sub[0] or canal, sub[1] if len(sub) > 1 else "")
    elif cmd == "whowas":
        if not primero:
            raise _uso(cmd, _("apodo"))
        return {"texto": srv.whowas_legible(account_id, primero), "titulo": _("Datos anteriores")}
    elif cmd == "ping":
        if not primero:
            raise _uso(cmd, _("apodo"))
        srv.ctcp_ping(account_id, primero)
    elif cmd == "ctcp":
        sub = resto.split(" ", 1)
        if len(sub) != 2:
            raise IRCError(_("Uso: /ctcp apodo CONSULTA (por ejemplo /ctcp fulano TIME)."))
        srv.ctcp_a(account_id, sub[0], sub[1])
    elif cmd == "mode":
        if not resto:
            raise _uso(cmd, _("[#canal] modos [argumento]"))
        sub = resto.split(" ")
        if sub[0].startswith(("#", "&", "!")):       # el «+» sería un modo, no un canal
            destino, modo, arg = sub[0], (sub[1] if len(sub) > 1 else ""), (sub[2] if len(sub) > 2 else "")
        else:
            destino, modo, arg = canal, sub[0], (sub[1] if len(sub) > 1 else "")
        srv.fijar_modo_canal(account_id, destino, modo, arg)
    elif cmd == "oper":
        sub = resto.split(" ", 1)
        if len(sub) != 2:
            raise IRCError(_("Uso: /oper usuario contraseña."))
        srv.hacerse_operador(account_id, sub[0], sub[1])
    elif cmd == "wallops":
        if not resto:
            raise _uso(cmd, _("texto"))
        srv.enviar_wallops(account_id, resto)
    elif cmd == "kick":
        if not primero:
            raise _uso(cmd, _("apodo [motivo]"))
        sub = resto.split(" ", 1)
        srv.accion_usuario_canal(account_id, canal, sub[0], "kick", sub[1] if len(sub) > 1 else "")
    elif cmd == "ban":
        if not primero:
            raise _uso(cmd, _("máscara"))
        srv.banear_mascara(account_id, canal, primero)
    elif cmd == "unban":
        if not primero:
            raise _uso(cmd, _("máscara"))
        srv.quitar_baneo(account_id, canal, primero)
    elif cmd == "umode":
        if not resto:
            raise _uso(cmd, _("modos"))
        srv.fijar_mis_modos(account_id, resto)
    elif cmd == "metadata":
        sub = resto.split(" ", 1)
        if not resto or sub[0].lower() == "ver":
            return {"texto": srv.leer_metadata(account_id, "*"), "titulo": _("Metadata")}
        srv.fijar_metadata(account_id, sub[0], sub[1] if len(sub) > 1 else "")
    else:
        raise IRCError(_("Comando no reconocido: /{c}. Escribe /help.").format(c=cmd))
    return None
