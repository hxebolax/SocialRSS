# -*- coding: utf-8 -*-
"""Administración de servidor y comandos ad-hoc de XMPP (tanda X7).

Cubre tres XEP que juntos dan TODA la administración de un servidor XMPP y la llave maestra de la
automatización del protocolo:

  * XEP-0050 Ad-Hoc Commands  -> descubrir y ejecutar CUALQUIER comando que anuncie un servidor, un
    componente o un contacto, con sus formularios (0004) paso a paso (multi-etapa: siguiente,
    anterior, completar, cancelar). Es lo que usan tanto la administración (0133) como las pasarelas
    (X8) y los bots.
  * XEP-0133 Service Administration -> NO es una API aparte: son comandos ad-hoc estándar contra el
    JID del servidor (add-user, delete-user, change-user-password, user-stats, announce...). Por eso
    "Comandos del servidor" ejecuta 0050 contra tu dominio; el servidor solo los ofrece si eres
    administrador.
  * XEP-0077 In-Band Registration -> crear una cuenta nueva en un servidor que lo permita, cambiar tu
    contraseña y cancelar (borrar) tu cuenta del servidor.

Los formularios se presentan como campos etiquetados accesibles (el diálogo genérico de la interfaz),
nunca como XML crudo. El puente bloqueante _run del núcleo ejecuta cada paso y devuelve un diccionario
plano con el estado, las acciones disponibles y los campos, para que la UI no toque slixmpp.
"""
import asyncio

from app.services.xmpp.xmpp_common import (
    Any, Dict, List, Optional, db, log, XMPPError, jid_bare)


class _AdminMixin:
    # ---------------- utilidades ----------------
    def dominio_servidor(self, account_id: int) -> str:
        """El dominio del servidor de la cuenta (la parte tras la @ del JID)."""
        jid = self._jids.get(account_id) or self._user_jid(account_id) or ""
        return jid.split("@", 1)[1] if "@" in jid else jid

    # ---------------- ad-hoc (0050 / 0133) ----------------
    def listar_comandos(self, account_id: int, jid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista los comandos ad-hoc que anuncia un JID (por defecto, el servidor de la cuenta).
        Cada comando es {jid, node, nombre}. Si el JID no ofrece ninguno, lista vacía."""
        cliente = self._cliente_conectado(account_id)
        destino = jid or self.dominio_servidor(account_id)

        async def _t():
            iq = await cliente["xep_0050"].get_commands(jid=destino)
            items = iq["disco_items"]["items"]  # set de tuplas (jid, node, name)
            salida = []
            for it in items:
                djid = it[0] if len(it) > 0 else destino
                node = it[1] if len(it) > 1 else ""
                nombre = (it[2] if len(it) > 2 else "") or node
                if node:
                    salida.append({"jid": str(djid), "node": node, "nombre": nombre})
            salida.sort(key=lambda c: c["nombre"].lower())
            return salida
        return self._run(_t(), timeout=30.0)

    def ejecutar_comando(self, account_id: int, jid: str, node: str, action: str = "execute",
                         sessionid: Optional[str] = None,
                         respuestas: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Ejecuta un paso de un comando ad-hoc y devuelve el estado como diccionario plano.

        - action: 'execute' (primer paso), 'next', 'prev', 'complete' o 'cancel'.
        - sessionid: el de la sesión en curso (None en el primer paso).
        - respuestas: {var: {'type': tipo, 'value': valor}} con lo que el usuario rellenó; None si no
          hay formulario que enviar (primer paso o cancelar).

        Devuelve: {status, sessionid, node, jid, titulo, instrucciones, notas[], acciones[], campos[]}
        donde campos es la lista de campos a mostrar (o '' si el comando terminó)."""
        cliente = self._cliente_conectado(account_id)

        async def _t():
            payload = None
            if respuestas:
                form = cliente["xep_0004"].make_form(ftype="submit")
                for var, info in respuestas.items():
                    tipo = info.get("type") or None
                    valor = info.get("value")
                    try:
                        form.add_field(var=var, ftype=tipo, value=valor)
                    except Exception:
                        form.add_field(var=var, value=valor)
                payload = form
            iq = await cliente["xep_0050"].send_command(
                jid, node, action=action, sessionid=sessionid, payload=payload)
            return self._parsear_comando(iq, jid, node)
        return self._run(_t(), timeout=60.0)

    @staticmethod
    def _parsear_comando(iq, jid: str, node: str) -> Dict[str, Any]:
        """Traduce el Iq de respuesta de un comando ad-hoc a un diccionario plano y accesible."""
        from slixmpp.plugins.xep_0004 import Form
        cmd = iq["command"]
        status = cmd["status"] or ""
        sessionid = cmd["sessionid"] or ""
        try:
            acciones = sorted(str(a) for a in (cmd["actions"] or []))
        except Exception:
            acciones = []
        # Notas del servidor (info / warn / error).
        notas = []
        try:
            for n in (cmd["notes"] or []):
                if isinstance(n, (tuple, list)) and len(n) >= 2:
                    notas.append((str(n[0]), str(n[1])))
                elif isinstance(n, dict):
                    notas.append((str(n.get("type", "info")), str(n.get("value", ""))))
        except Exception:
            pass
        titulo = instrucciones = ""
        campos: List[Dict[str, Any]] = []
        formularios = [s for s in cmd if isinstance(s, Form)]
        if formularios:
            form = formularios[0]
            titulo = form["title"] or ""
            instrucciones = form["instructions"] or ""
            campos = _AdminMixin._campos_de_form(form)
        return {"status": status, "sessionid": sessionid, "node": cmd["node"] or node,
                "jid": str(jid), "titulo": titulo, "instrucciones": instrucciones,
                "notas": notas, "acciones": acciones, "campos": campos,
                "completado": status in ("completed", "canceled")}

    @staticmethod
    def _campos_de_form(form) -> List[Dict[str, Any]]:
        """Traduce un formulario de datos (0004) a una lista de campos planos para la UI. Compartido
        por los comandos ad-hoc (X7) y el registro en pasarelas (X8)."""
        campos: List[Dict[str, Any]] = []
        for var, fld in form.get_fields().items():
            tipo = fld["type"] or "text-single"
            if tipo == "fixed":
                campos.append({"var": "", "type": "fixed", "label": fld["value"] or "",
                               "desc": "", "required": False, "value": "", "opciones": [],
                               "oculto": False})
                continue
            opciones = []
            try:
                for op in (fld.get_options() or []):
                    opciones.append((op.get("label") or op.get("value") or "",
                                     op.get("value") or ""))
            except Exception:
                pass
            campos.append({
                "var": var or "",
                "type": tipo,
                "label": fld["label"] or var or "",
                "desc": fld["desc"] or "",
                "required": bool(fld["required"]),
                "value": fld["value"],
                "opciones": opciones,
                "oculto": (tipo == "hidden"),
            })
        return campos

    # ---------------- registro en banda (0077) ----------------
    def cambiar_password(self, account_id: int, nuevo: str) -> bool:
        """Cambia la contraseña de TU cuenta en el servidor (XEP-0077) y actualiza la guardada."""
        nuevo = (nuevo or "").strip()
        if not nuevo:
            raise XMPPError("La contraseña no puede estar vacía.")
        cliente = self._cliente_conectado(account_id)

        async def _t():
            await cliente["xep_0077"].change_password(nuevo)
            return True
        self._run(_t(), timeout=30.0)
        # Guardar la nueva para que la reconexión automática siga funcionando.
        try:
            self.guardar_password(account_id, nuevo)
        except Exception as e:
            log.debug(f"XMPP: contraseña cambiada en el servidor pero no se pudo re-guardar: {e}")
        return True

    def cancelar_cuenta_servidor(self, account_id: int) -> bool:
        """Cancela (BORRA) tu cuenta en el servidor (XEP-0077 remove) y la elimina también en local.
        Irreversible en el servidor."""
        cliente = self._cliente_conectado(account_id)

        async def _t():
            await cliente["xep_0077"].cancel_registration()
            return True
        self._run(_t(), timeout=30.0)
        try:
            self.eliminar_cuenta(account_id)
        except Exception as e:
            log.debug(f"XMPP: cuenta cancelada en el servidor pero no se limpió local: {e}")
        return True

    def registrar_cuenta_nueva(self, jid: str, password: str, email: str = "") -> int:
        """Crea una cuenta NUEVA en un servidor que permita registro en banda (XEP-0077) y la da de
        alta en la app. Devuelve el account_id. Lanza XMPPError si el servidor no lo permite o el
        usuario ya existe."""
        jid = jid_bare(jid).strip()
        password = password or ""
        if "@" not in jid or not password:
            raise XMPPError("Escribe el JID completo (usuario@servidor) y la contraseña.")

        async def _reg():
            cli = self._crear_cliente(jid, password)  # sin account_id: no OMEMO en el temporal
            cli.register_plugin("xep_0077")
            try:
                cli["xep_0077"].force_registration = True
            except Exception:
                pass
            estado = {"ok": False, "err": ""}
            listo = asyncio.Event()

            async def _on_register(iq):
                try:
                    resp = cli.Iq()
                    resp["type"] = "set"
                    resp["to"] = cli.boundjid.host
                    resp["register"]["username"] = cli.boundjid.user
                    resp["register"]["password"] = password
                    if email:
                        try:
                            resp["register"]["email"] = email
                        except Exception:
                            pass
                    await resp.send()
                except Exception as e:
                    estado["err"] = str(e)
                    listo.set()

            def _start(_e):
                estado["ok"] = True
                listo.set()

            def _failed(_e):
                estado["err"] = estado["err"] or "auth"
                listo.set()

            cli.add_event_handler("register", _on_register)
            cli.add_event_handler("session_start", _start)
            cli.add_event_handler("failed_auth", _failed)
            try:
                cli.connect()
                await asyncio.wait_for(listo.wait(), timeout=45)
            except asyncio.TimeoutError:
                estado["err"] = estado["err"] or "timeout"
            finally:
                try:
                    cli.disconnect()
                except Exception:
                    pass
            return estado

        estado = self._run(_reg(), timeout=60.0)
        if not estado.get("ok"):
            err = (estado.get("err") or "").lower()
            if "conflict" in err:
                raise XMPPError("Ese usuario ya existe en el servidor.")
            if "not-acceptable" in err or "not-allowed" in err or "forbidden" in err:
                raise XMPPError("El servidor no permite el registro en banda (crea la cuenta desde su web).")
            raise XMPPError("No se pudo crear la cuenta: " +
                            (estado.get("err") or "el servidor rechazó el registro."))
        # Alta normal (verifica de nuevo conectando y persiste).
        return self.crear_cuenta(jid, password)
