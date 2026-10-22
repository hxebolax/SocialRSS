# -*- coding: utf-8 -*-
"""Cifrado de extremo a extremo OMEMO para XMPP (tanda X6).

OMEMO (XEP-0384) es el cifrado E2E moderno de XMPP: cada mensaje se cifra por dispositivo, con la
misma familia criptográfica que Signal (Double Ratchet + X3DH). Aquí:

  * `StorageImpl`     -> almacén persistente de las claves OMEMO de UNA cuenta, en un JSON propio bajo
                         data/sessions (escritura ATÓMICA, regla de la casa: nunca corromper el store).
  * `XEP_0384Impl`    -> el plugin de slixmpp con BTBV (Blind Trust Before Verification): confía a
                         ciegas en los dispositivos nuevos la primera vez (como Conversations/Gajim),
                         y deja la verificación manual de huellas para el diálogo de la interfaz.
  * `_OmemoMixin`     -> lo que el resto del servicio usa: registrar el plugin por cuenta, saber si un
                         chat está cifrado, cifrar al enviar, descifrar al recibir y listar/confiar en
                         huellas para la verificación.

El diseño sigue el ejemplo oficial echo_client.py de slixmpp-omemo, escrito de cero y adaptado a la
casa (almacén por cuenta, cifrado por conversación, huellas legibles). Detalle de UX heredado de
Matrix: cuando llega un cifrado que no se puede descifrar, se guarda una fila explicativa
"[Mensaje cifrado que no se pudo descifrar]" — nunca un silencio inexplicable.
"""
import asyncio
import json
import os
import sys
import threading
from importlib.util import find_spec
from typing import Any, Dict, FrozenSet, List, Optional, Set

from app.core.database import db
from app.core.paths import get_data_path

try:
    from app.core.i18n import _
except Exception:  # pragma: no cover
    def _(s):
        return s

import logging
log = logging.getLogger(__name__)

# ¿Está instalada toda la pila OMEMO? (slixmpp-omemo + omemo + los backends). Es pip-instalable en
# Windows sin compilar; si falta, XMPP funciona igual pero sin cifrado (carga diferida, nunca al
# arrancar la app).
HAS_OMEMO = all(find_spec(m) is not None for m in ("slixmpp_omemo", "omemo", "oldmemo", "twomemo"))

# El plugin y el almacén se construyen de forma perezosa (import pesado) la primera vez que se
# registran; aquí solo dejamos los símbolos preparados.
_PLUGIN_REGISTRADO = False
_reg_lock = threading.Lock()


def _construir_clases():
    """Importa la pila OMEMO y define StorageImpl + XEP_0384Impl. Se llama una sola vez, de forma
    diferida, al registrar el plugin en la primera cuenta que lo use."""
    from omemo.storage import Just, Maybe, Nothing, Storage
    from omemo.types import DeviceInformation, JSONType
    from slixmpp_omemo import TrustLevel, XEP_0384

    class StorageImpl(Storage):
        """Almacén OMEMO de una cuenta en un único JSON (escritura atómica + lock)."""

        def __init__(self, ruta_json: str) -> None:
            super().__init__()
            self._ruta = ruta_json
            self._datos: Dict[str, Any] = {}
            self._lock = threading.Lock()
            try:
                with open(self._ruta, encoding="utf-8") as f:
                    self._datos = json.load(f)
            except Exception:
                self._datos = {}

        def _guardar(self) -> None:
            # Atómico: escribir a un temporal y reemplazar (nunca dejar el store a medias).
            tmp = self._ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._datos, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._ruta)

        async def _load(self, key: str) -> "Maybe[JSONType]":
            with self._lock:
                if key in self._datos:
                    return Just(self._datos[key])
            return Nothing()

        async def _store(self, key: str, value: "JSONType") -> None:
            with self._lock:
                self._datos[key] = value
                self._guardar()

        async def _delete(self, key: str) -> None:
            with self._lock:
                self._datos.pop(key, None)
                self._guardar()

    class XEP_0384Impl(XEP_0384):
        """Plugin OMEMO de la casa: BTBV activado, almacén por cuenta desde la config."""

        default_config = {
            "fallback_message": _("Este mensaje está cifrado con OMEMO."),
            "storage_path": None,
        }

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.__storage: Optional[Storage] = None

        def plugin_init(self) -> None:
            ruta = getattr(self, "storage_path", None)
            if not ruta:
                raise RuntimeError("OMEMO: falta la ruta del almacén (storage_path).")
            self.__storage = StorageImpl(ruta)
            super().plugin_init()

        @property
        def storage(self) -> "Storage":
            return self.__storage

        @property
        def _btbv_enabled(self) -> bool:
            # Blind Trust Before Verification: confía a ciegas en los dispositivos nuevos hasta que
            # el usuario verifique una huella a mano (como Conversations/Gajim). Cómodo y seguro
            # frente a atacantes pasivos; la verificación manual sube el listón cuando se quiere.
            return True

        async def _devices_blindly_trusted(
            self, blindly_trusted: "FrozenSet[DeviceInformation]", identifier: Optional[str]
        ) -> None:
            log.debug("OMEMO: %s dispositivos de confianza ciega (%s).",
                      len(blindly_trusted), identifier)

        async def _prompt_manual_trust(
            self, manually_trusted: "FrozenSet[DeviceInformation]", identifier: Optional[str]
        ) -> None:
            # Con BTBV activo, esto solo se dispara para dispositivos ya marcados como pendientes de
            # verificación manual. No bloqueamos el envío por consola (no hay consola): se dejan
            # UNDECIDED y el diálogo de huellas de la interfaz permite confiar/desconfiar. Lanzar la
            # excepción hace que ese dispositivo concreto se omita del cifrado sin romper el resto.
            from omemo import TrustDecisionFailed
            raise TrustDecisionFailed(
                "Verificación manual pendiente: use el diálogo de huellas OMEMO.")

    return StorageImpl, XEP_0384Impl, TrustLevel


def _asegurar_plugin_registrado():
    """Registra la clase del plugin en slixmpp (una sola vez para todo el proceso). Devuelve el
    módulo donde vive la clase, para pasarlo a cliente.register_plugin(..., module=...)."""
    global _PLUGIN_REGISTRADO
    with _reg_lock:
        if _PLUGIN_REGISTRADO:
            return sys.modules[__name__]
        from slixmpp.plugins import register_plugin as _slix_register
        StorageImpl, XEP_0384Impl, TrustLevel = _construir_clases()
        # Exponer las clases a nivel de módulo para que register_plugin(module=este_módulo) las vea.
        globals()["StorageImpl"] = StorageImpl
        globals()["XEP_0384Impl"] = XEP_0384Impl
        globals()["TrustLevel"] = TrustLevel
        _slix_register(XEP_0384Impl)
        _PLUGIN_REGISTRADO = True
        return sys.modules[__name__]


class _OmemoMixin:
    """Métodos OMEMO de XMPPService. El servicio ya tiene _run/_clients/_jids del núcleo."""

    # ---------------- disponibilidad y registro ----------------
    @staticmethod
    def omemo_disponible() -> bool:
        return HAS_OMEMO

    def _ruta_almacen_omemo(self, account_id: int) -> str:
        carpeta = get_data_path("sessions")
        carpeta.mkdir(parents=True, exist_ok=True)
        return str(carpeta / f"xmpp_{account_id}_omemo.json")

    def _registrar_omemo(self, account_id: int, cliente) -> bool:
        """Registra el plugin xep_0384 en un ClientXMPP recién creado, con el almacén de ESTA cuenta.
        Se llama desde _crear_cliente si OMEMO está disponible. Devuelve True si quedó registrado."""
        if not HAS_OMEMO:
            return False
        try:
            modulo = _asegurar_plugin_registrado()
            cliente.register_plugin(
                "xep_0384",
                {"storage_path": self._ruta_almacen_omemo(account_id)},
                module=modulo,
            )
            return True
        except Exception as e:
            log.debug("OMEMO: no se pudo registrar el plugin en la cuenta %s: %s", account_id, e)
            return False

    # ---------------- estado por conversación ----------------
    def esta_cifrado(self, account_id: int, jid: str) -> bool:
        """¿Está OMEMO activado para este chat? (columna xmpp_chats.cifrado). NUNCA para los chats de
        una PASARELA (WhatsApp, Discord…): la pasarela cifra en su lado (con la red legada), sus
        contactos/salas no publican claves OMEMO y, si se intentara, el cifrado se quedaría colgado
        esperando dispositivos que no existen (envío con 'tiempo agotado')."""
        from app.services.xmpp.xmpp_common import db_id_chat
        try:
            dom = jid.split("@", 1)[1] if "@" in jid else jid
            if dom in {p["dominio"] for p in self.puentes(account_id)}:
                return False
        except Exception:
            pass
        fila = db.query_one("SELECT cifrado FROM xmpp_chats WHERE id = ?",
                            (db_id_chat(account_id, jid),))
        return bool(fila and fila[0])

    def fijar_cifrado(self, account_id: int, jid: str, activar: bool) -> bool:
        """Activa o desactiva OMEMO para un chat. Devuelve el estado final. Si OMEMO no está
        instalado, no se puede activar."""
        from app.services.xmpp.xmpp_common import db_id_chat, nombre_desde_jid
        if activar and not HAS_OMEMO:
            raise self._error_omemo_no_instalado()
        fid = db_id_chat(account_id, jid)
        existe = db.query_one("SELECT 1 FROM xmpp_chats WHERE id = ?", (fid,))
        if not existe:
            db.execute("INSERT INTO xmpp_chats (id, account_id, jid, tipo, nombre, cifrado) "
                       "VALUES (?, ?, ?, 'contacto', ?, ?)",
                       (fid, account_id, jid, nombre_desde_jid(jid), 1 if activar else 0))
        else:
            db.execute("UPDATE xmpp_chats SET cifrado = ? WHERE id = ?",
                       (1 if activar else 0, fid))
        return bool(activar)

    @staticmethod
    def _error_omemo_no_instalado():
        from app.services.xmpp.xmpp_common import XMPPError
        return XMPPError("El cifrado OMEMO no está instalado. Instale 'slixmpp-omemo' "
                         "(pip install slixmpp-omemo) para poder cifrar.")

    # ---------------- cifrar al enviar ----------------
    async def _cifrar_y_enviar(self, cliente, msg, destinatarios: "Set[Any]") -> bool:
        """Cifra un mensaje ya construido para el conjunto de JIDs y lo envía. Devuelve True si se
        envió cifrado, False si no había nada que cifrar (en cuyo caso el llamante decide)."""
        xep = cliente["xep_0384"]
        cifrado, errores = await xep.encrypt_message(msg, destinatarios)
        if errores:
            log.debug("OMEMO: errores no críticos al cifrar: %s", errores)
        if cifrado is None:
            return False
        # Marcar el mecanismo de cifrado explícito (XEP-0380) apuntando a oldmemo, que es lo que casi
        # todos los clientes entienden (Conversations, Gajim, Dino…).
        try:
            import oldmemo
            cifrado["eme"]["namespace"] = oldmemo.oldmemo.NAMESPACE
            cifrado["eme"]["name"] = cliente["xep_0380"].mechanisms[oldmemo.oldmemo.NAMESPACE]
        except Exception:
            pass
        cifrado.send()
        return True

    def _jid_obj(self, jid: str):
        from slixmpp import JID
        return JID(jid)

    async def _destinatarios_cifrado(self, cliente, jid: str, es_muc: bool) -> "Set[Any]":
        """Conjunto de JIDs para los que cifrar. En 1:1 es el contacto; en una sala MUC son los JID
        reales de los ocupantes (OMEMO en MUC exige sala no anónima: se conocen los JID reales)."""
        from slixmpp import JID
        if not es_muc:
            return {JID(jid)}
        destinos: Set[Any] = set()
        try:
            xep45 = cliente["xep_0045"]
            for nick in xep45.get_roster(jid):
                real = xep45.get_jid_property(jid, nick, "jid")
                if real:
                    destinos.add(JID(JID(str(real)).bare))
        except Exception as e:
            log.debug("OMEMO: no se pudo resolver ocupantes de %s: %s", jid, e)
        destinos.discard(None)
        return destinos

    # ---------------- descifrar al recibir ----------------
    async def _descifrar_stanza(self, cliente, stanza):
        """Descifra una stanza entrante si viene cifrada con OMEMO.

        Devuelve (texto, fue_cifrado, hubo_error):
          * fue_cifrado False  -> no era OMEMO, procésalo como texto claro.
          * fue_cifrado True, hubo_error False, texto=str -> descifrado correcto (texto puede ser '').
          * fue_cifrado True, hubo_error True  -> venía cifrado pero no se pudo descifrar.
        """
        xep = cliente["xep_0384"]
        try:
            espacios = xep.is_encrypted(stanza)
        except Exception:
            espacios = []
        if not espacios:
            return "", False, False
        try:
            mensaje, _info = await xep.decrypt_message(stanza)
            cuerpo = mensaje["body"] or ""
            return cuerpo, True, False
        except Exception as e:
            log.debug("OMEMO: no se pudo descifrar: %s", e)
            return "", True, True

    def _guardar_cifrado_fallido(self, account_id: int, cliente, msg, es_muc: bool = False):
        """Guarda la fila explicativa de un mensaje cifrado que no se pudo descifrar (para no dejar un
        hueco mudo en la conversación)."""
        from app.services.xmpp.xmpp_common import jid_bare, ahora_iso
        try:
            desde = str(msg["from"])
            if es_muc:
                room = jid_bare(desde)
                nick = desde.split("/", 1)[1] if "/" in desde else room
                stanza_id = self._stanza_id_de(msg) or msg["id"] or ahora_iso()
                self._guardar_mensaje(
                    account_id, room, stanza_id, nick, nick,
                    _("[Mensaje cifrado que no se pudo descifrar]"), "msg", ahora_iso(),
                    propio=False, leido=False, contar=True, cifrado=True)
            else:
                yo = jid_bare(str(cliente.boundjid))
                debare = jid_bare(desde)
                propio = (debare == yo)
                chat_jid = jid_bare(str(msg["to"])) if propio else debare
                stanza_id = self._stanza_id_de(msg) or msg["id"] or ahora_iso()
                self._guardar_mensaje(
                    account_id, chat_jid, stanza_id, debare,
                    self._nombre_chat(account_id, chat_jid),
                    _("[Mensaje cifrado que no se pudo descifrar]"), "msg", ahora_iso(),
                    propio=propio, leido=propio, contar=not propio, cifrado=True)
        except Exception as e:
            log.debug("OMEMO: no se pudo registrar el cifrado fallido: %s", e)

    # ---------------- huellas y verificación ----------------
    def _huella_legible(self, identity_key: bytes) -> str:
        """Formatea una clave de identidad como huella legible en grupos (para leerla en voz alta)."""
        try:
            hexc = identity_key.hex()
        except Exception:
            hexc = str(identity_key)
        # Grupos de 8 caracteres separados por espacio.
        return " ".join(hexc[i:i + 8] for i in range(0, len(hexc), 8))

    def mi_huella(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Mi propia huella OMEMO (device id + huella legible) para enseñarla y que la verifiquen."""
        if not HAS_OMEMO:
            return None
        cliente = self._clients.get(account_id)
        if cliente is None:
            return None

        async def _t():
            xep = cliente["xep_0384"]
            sm = await xep.get_session_manager()
            propio, _otros = await sm.get_own_device_information()
            return {"device_id": propio.device_id,
                    "huella": self._huella_legible(propio.identity_key),
                    "confianza": propio.trust_level_name}
        try:
            return self._run(_t(), timeout=20.0)
        except Exception as e:
            log.debug("OMEMO: no se pudo obtener mi huella: %s", e)
            return None

    def huellas_de(self, account_id: int, jid: str) -> List[Dict[str, Any]]:
        """Huellas OMEMO de los dispositivos de un contacto (para verificar/confiar)."""
        if not HAS_OMEMO:
            return []
        cliente = self._clients.get(account_id)
        if cliente is None:
            return []
        from app.services.xmpp.xmpp_common import jid_bare

        async def _t():
            xep = cliente["xep_0384"]
            sm = await xep.get_session_manager()
            try:
                await sm.refresh_device_list("twomemo", jid_bare(jid))
            except Exception:
                pass
            try:
                await sm.refresh_device_list("oldmemo", jid_bare(jid))
            except Exception:
                pass
            devices = await sm.get_device_information(jid_bare(jid))
            out = []
            for d in devices:
                out.append({
                    "device_id": d.device_id,
                    "huella": self._huella_legible(d.identity_key),
                    "confianza": d.trust_level_name,
                    "activo": bool(d.active),
                    "etiqueta": d.label or "",
                    "_ik": d.identity_key,
                    "_jid": d.bare_jid,
                })
            return out
        try:
            return self._run(_t(), timeout=30.0)
        except Exception as e:
            log.debug("OMEMO: no se pudieron obtener las huellas de %s: %s", jid, e)
            return []

    def confiar_dispositivo(self, account_id: int, jid: str, identity_key, confiar: bool) -> bool:
        """Marca un dispositivo de un contacto como de confianza (verificado) o desconfiado."""
        if not HAS_OMEMO:
            return False
        cliente = self._clients.get(account_id)
        if cliente is None:
            return False
        from app.services.xmpp.xmpp_common import jid_bare

        async def _t():
            xep = cliente["xep_0384"]
            sm = await xep.get_session_manager()
            TrustLevel = globals().get("TrustLevel")
            nivel = (TrustLevel.TRUSTED if confiar else TrustLevel.DISTRUSTED).value
            await sm.set_trust(jid_bare(jid), identity_key, nivel)
            return True
        try:
            return bool(self._run(_t(), timeout=20.0))
        except Exception as e:
            log.debug("OMEMO: no se pudo fijar la confianza: %s", e)
            return False
