# -*- coding: utf-8 -*-
"""Pasarelas / gateways / relays de XMPP (tanda X8).

Una pasarela (transporte) es un componente del servidor que conecta XMPP con OTRA red (WhatsApp,
Telegram, Signal, IRC…). El cliente NO es el puente: es quien se REGISTRA en él (XEP-0077 + 0100) y
luego habla con los contactos "legacy" como si fueran contactos XMPP normales (el puente les crea un
JID tipo +34600...@whatsapp.tu-servidor y aparecen en el roster de siempre).

Esto es lo que permite montar un servidor propio con un bridge (p. ej. slidge-whatsapp) y usarlo
desde SocialRSS. Aquí:

  * descubrir_transportes  -> lista los componentes del servidor que son gateways (disco 0030) y de
    qué red son.
  * formulario_registro    -> pide el formulario de registro del gateway (0077); puede ser un
    formulario de datos moderno (0004, slidge) o campos legacy (usuario/contraseña/teléfono…).
  * registrar_en_gateway   -> envía el registro. Muchos bridges (slidge) responden con un comando
    ad-hoc de emparejamiento (WhatsApp devuelve un CÓDIGO/QR): se lee con el diálogo genérico de
    comandos (X7) o en el chat con la pasarela, SIEMPRE como texto accesible, nunca solo imagen.
  * desregistrar_gateway   -> cancela el registro (0077 remove) y quita la pasarela.

Los comandos propios del gateway (re-login, listar grupos, sincronizar, desregistrar…) se ejecutan
con XMPPComandosDialog apuntando al JID del gateway: el diálogo de formularios de X7 sirve tal cual.
"""
from slixmpp.xmlstream import register_stanza_plugin

from app.services.xmpp.xmpp_common import (
    Any, Dict, List, Optional, db, log, XMPPError, jid_bare)

# Registrar el formulario de datos (0004) como hijo del <query> de registro (jabber:iq:register), que
# es como los gateways modernos (slidge) piden los datos. Sin esto, slixmpp no parsea el formulario.
_FORM_EN_REGISTRO = False


def _asegurar_form_en_registro():
    global _FORM_EN_REGISTRO
    if _FORM_EN_REGISTRO:
        return
    try:
        from slixmpp.plugins.xep_0077.stanza import Register
        from slixmpp.plugins.xep_0004 import Form
        # iterable=True para poder recorrer los hijos del <query> y encontrar el formulario (0004).
        register_stanza_plugin(Register, Form, iterable=True)
        _FORM_EN_REGISTRO = True
    except Exception as e:
        log.debug(f"XMPP: no se pudo registrar Form en Register: {e}")


# Nombres legibles de los campos de registro legacy (XEP-0077 clásico).
_ETIQUETA_LEGACY = {
    "username": "Usuario", "password": "Contraseña", "nick": "Apodo", "email": "Correo",
    "first": "Nombre", "last": "Apellidos", "name": "Nombre completo", "phone": "Teléfono",
    "city": "Ciudad", "address": "Dirección", "date": "Fecha", "url": "Dirección web",
    "text": "Texto", "misc": "Otros", "state": "Provincia/Estado", "zip": "Código postal",
    "nick": "Apodo",
}
_ORDEN_LEGACY = ["username", "password", "nick", "phone", "email", "first", "last", "name",
                 "city", "address", "state", "zip", "date", "url", "text", "misc"]

# Nombres legibles de tipos de gateway habituales.
_RED_LEGIBLE = {
    "whatsapp": "WhatsApp", "telegram": "Telegram", "signal": "Signal", "discord": "Discord",
    "steam": "Steam", "irc": "IRC", "mattermost": "Mattermost", "skype": "Skype",
    "facebook": "Facebook", "messenger": "Messenger", "sms": "SMS", "xmpp": "XMPP",
}


class _PasarelasMixin:
    def _red_legible(self, tipo: str) -> str:
        return _RED_LEGIBLE.get((tipo or "").lower(), tipo or "")

    # ---------------- descubrir ----------------
    def descubrir_transportes(self, account_id: int) -> List[Dict[str, Any]]:
        """Lista los componentes del servidor que son pasarelas (gateways). Cada uno es
        {jid, nombre, tipo, red}. Vacío si el servidor no tiene ninguno."""
        cliente = self._cliente_conectado(account_id)
        dominio = self.dominio_servidor(account_id)

        async def _t():
            import asyncio as _a
            iq = await _a.wait_for(cliente["xep_0030"].get_items(jid=dominio), timeout=15)
            items = iq["disco_items"]["items"]  # set de (jid, node, name)
            salida = []
            for it in items:
                jid = it[0] if len(it) > 0 else ""
                node = it[1] if len(it) > 1 else ""
                nombre_item = (it[2] if len(it) > 2 else "") or ""
                if not jid or node:
                    continue  # los transportes son componentes "pelados", sin nodo
                try:
                    # Timeout POR componente: si uno no responde (p. ej. una pasarela con la sesión
                    # caída), no debe tumbar toda la búsqueda de las demás.
                    info = await _a.wait_for(cliente["xep_0030"].get_info(jid=jid), timeout=8)
                except Exception:
                    continue
                identidades = info["disco_info"]["identities"]  # set de (category, type, lang, name)
                gw = [i for i in identidades if (i[0] if len(i) > 0 else "") == "gateway"]
                if not gw:
                    continue
                tipo = (gw[0][1] if len(gw[0]) > 1 else "") or ""
                nombre = (nombre_item or (gw[0][3] if len(gw[0]) > 3 else "") or
                          self._red_legible(tipo) or str(jid))
                salida.append({"jid": str(jid), "nombre": nombre, "tipo": tipo,
                               "red": self._red_legible(tipo)})
            salida.sort(key=lambda g: g["nombre"].lower())
            return salida
        return self._run(_t(), timeout=45.0)

    # ---------------- registro ----------------
    def formulario_registro(self, account_id: int, gateway_jid: str) -> Dict[str, Any]:
        """Pide el formulario de registro de una pasarela (0077). Devuelve
        {registrado, instrucciones, campos, es_form}. 'es_form' indica si usa formulario de datos
        (0004, hay que reenviarlo como submit) o campos legacy."""
        _asegurar_form_en_registro()
        cliente = self._cliente_conectado(account_id)
        gateway_jid = jid_bare(gateway_jid)

        async def _t():
            iq = await cliente["xep_0077"].get_registration(jid=gateway_jid)
            from slixmpp.plugins.xep_0004 import Form
            reg = iq["register"]
            registrado = bool(reg["registered"])
            instrucciones = reg["instructions"] or ""
            formularios = [s for s in reg if isinstance(s, Form)]
            if formularios:
                form = formularios[0]
                campos = self._campos_de_form(form)
                return {"registrado": registrado,
                        "instrucciones": form["instructions"] or instrucciones,
                        "campos": campos, "es_form": True}
            # Campos legacy.
            presentes = reg["fields"] or set()
            campos = []
            for var in _ORDEN_LEGACY:
                if var in presentes:
                    tipo = "text-private" if var == "password" else "text-single"
                    campos.append({
                        "var": var, "type": tipo,
                        "label": _(_ETIQUETA_LEGACY.get(var, var)),
                        "desc": "", "required": True, "value": reg[var] or "",
                        "opciones": [], "oculto": False})
            return {"registrado": registrado, "instrucciones": instrucciones,
                    "campos": campos, "es_form": False}
        return self._run(_t(), timeout=30.0)

    def registrar_en_gateway(self, account_id: int, gateway_jid: str,
                             respuestas: Dict[str, Dict[str, Any]], es_form: bool) -> bool:
        """Envía el registro a la pasarela. 'respuestas' es {var: {'type':, 'value':}}; 'es_form' dice
        si se manda como formulario de datos (0004) o como campos legacy."""
        _asegurar_form_en_registro()
        cliente = self._cliente_conectado(account_id)
        gateway_jid = jid_bare(gateway_jid)

        async def _t():
            iq = cliente.make_iq_set(ito=gateway_jid)
            iq.enable("register")
            if es_form:
                form = cliente["xep_0004"].make_form(ftype="submit")
                for var, info in (respuestas or {}).items():
                    tipo = info.get("type") or None
                    try:
                        form.add_field(var=var, ftype=tipo, value=info.get("value"))
                    except Exception:
                        form.add_field(var=var, value=info.get("value"))
                iq["register"].append(form)
            else:
                for var, info in (respuestas or {}).items():
                    try:
                        iq["register"][var] = info.get("value")
                    except Exception:
                        pass
            await iq.send(timeout=45)
            return True
        self._run(_t(), timeout=50.0)
        return True

    def desregistrar_gateway(self, account_id: int, gateway_jid: str) -> bool:
        """Cancela el registro en una pasarela (0077 remove): deja de usarla y sus contactos legacy
        desaparecen del roster. Una pasarela como slidge-WhatsApp tarda en confirmar (tiene que
        DESVINCULAR la sesión de WhatsApp antes de responder), así que se espera con holgura."""
        cliente = self._cliente_conectado(account_id)
        gateway_jid = jid_bare(gateway_jid)

        async def _t():
            await cliente["xep_0077"].cancel_registration(jid=gateway_jid, timeout=90)
            return True
        self._run(_t(), timeout=100.0)
        return True

    def borrar_datos_pasarela_local(self, account_id: int, dominio: str) -> int:
        """Borra en LOCAL todos los chats y mensajes de una pasarela (su dominio y el de sus contactos
        legacy). Se usa tras darla de baja para dejar todo limpio y poder añadir otra pasarela (p. ej.
        otro WhatsApp). Devuelve cuántos chats se quitaron."""
        dominio = jid_bare(dominio)
        like = "%@" + dominio
        # Mensajes de todos los chats de esa pasarela (contactos legacy + salas + el propio gateway).
        db.execute("""DELETE FROM xmpp_mensajes WHERE account_id = ?
                      AND (chat_jid LIKE ? OR chat_jid = ?)""", (account_id, like, dominio))
        try:
            db.execute("""DELETE FROM xmpp_mensajes_fts WHERE msg_db_id NOT IN
                          (SELECT id FROM xmpp_mensajes)""")
        except Exception:
            pass
        filas = db.execute("""DELETE FROM xmpp_chats WHERE account_id = ?
                              AND (jid LIKE ? OR jid = ?)""", (account_id, like, dominio))
        self.invalidar_puentes(account_id)   # una pasarela menos: refrescar la caché de puentes
        self._emit(account_id, "roster_actualizado")
        return filas or 0

    def dar_baja_pasarela(self, account_id: int, gateway_jid: str) -> Dict[str, Any]:
        """Da de baja una pasarela COMPLETA: cancela el registro en el servidor (0077 remove) y borra
        sus datos locales. Deja todo limpio para volver a registrar otra pasarela distinta.

        La orden de baja SE ENVÍA siempre; si el servidor tarda en confirmar (slidge desvincula la
        red legada antes de responder, y puede pasarse del tiempo), NO se cancela la limpieza local:
        la orden ya salió y así puedes volver a registrar desde cero. Devuelve
        {'chats': nº quitados, 'confirmado': bool}."""
        gateway_jid = jid_bare(gateway_jid)
        confirmado = True
        try:
            self.desregistrar_gateway(account_id, gateway_jid)
        except XMPPError as e:
            confirmado = False
            log.warning(f"XMPP: baja de {gateway_jid} enviada pero sin confirmación clara: {e}")
        chats = self.borrar_datos_pasarela_local(account_id, gateway_jid)
        return {"chats": chats, "confirmado": confirmado}
