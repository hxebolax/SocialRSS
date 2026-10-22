# -*- coding: utf-8 -*-
"""Cross-signing (firma cruzada) de Matrix construido DESDE CERO sobre python-olm.

matrix-nio no trae cross-signing, así que aquí se implementa el protocolo a mano usando las
primitivas de olm (olm.PkSigning para las tres claves Ed25519 de firma cruzada) y llamadas
directas a la Client-Server API. La lógica sigue la especificación oficial (módulo E2EE,
sección "cross-signing") y se apoyó en la referencia de mautrix-python (MPL) para el orden de
firmas, pero está reescrita para nio y nuestro patrón.

Qué resuelve: cada usuario tiene TRES pares de claves de firma cruzada:
- MSK (master): la identidad del usuario; firma la SSK y la USK.
- SSK (self-signing): firma tus propios dispositivos.
- USK (user-signing): firma las claves maestras de OTROS usuarios (solo la ves tú).

Al configurarlo, ADEMÁS firmamos nuestra MSK con nuestro DISPOSITIVO. Eso, junto con la
verificación por emojis (SAS) que ya cierra bien, permite la "ruta de migración" de la spec:
quien verifica tu dispositivo puede confiar en tu clave maestra y firmarla con su USK, con lo
que confía en TODOS tus dispositivos (presentes y futuros) sin re-verificar.

LÍMITE HONESTO: no implementamos Secret Storage (SSSS). Las claves privadas de firma cruzada se
guardan CIFRADAS en este equipo (security/MachineGuid). Si reinstalas desde cero sin conservar
data/, tendrás que volver a configurarlo (las verificaciones que ya hiciste no se pierden en el
servidor, pero las claves privadas locales sí). Es suficiente para "ser de fiar" para los demás.
"""
import base64
import json

from app.services.matrix.matrix_common import (
    Any, Dict, Optional, List, security, log, MatrixError)


def _canon(cliente, data: dict) -> str:
    """JSON canónico de Matrix SIN 'signatures' ni 'unsigned' (lo que se firma)."""
    from nio.api import Api
    d = {k: v for k, v in data.items() if k not in ("signatures", "unsigned")}
    return Api.to_canonical_json(d)


class _CrossSigningMixin:
    # ---------------- Almacenamiento local de las semillas privadas ----------------
    def _guardar_semillas_xsign(self, account_id: int, master: bytes, self_s: bytes, user: bytes):
        for nombre, semilla in (("xsign_master", master), ("xsign_self", self_s),
                                ("xsign_user", user)):
            security.guardar_credencial("matrix", f"{account_id}:{nombre}",
                                        base64.b64encode(semilla).decode("ascii"))

    def _cargar_claves_xsign(self, account_id: int):
        """Devuelve (master_pk, self_pk, user_pk) de olm.PkSigning si hay semillas guardadas,
        o None si esta cuenta aún no tiene cross-signing configurado en este equipo."""
        import olm
        claves = {}
        for nombre in ("xsign_master", "xsign_self", "xsign_user"):
            b64 = security.obtener_credencial("matrix", f"{account_id}:{nombre}")
            if not b64:
                return None
            claves[nombre] = olm.PkSigning(base64.b64decode(b64))
        return claves["xsign_master"], claves["xsign_self"], claves["xsign_user"]

    def tengo_claves_xsign_local(self, account_id: int) -> bool:
        return self._cargar_claves_xsign(account_id) is not None

    # ---------------- Estado ----------------
    def estado_cross_signing(self, account_id: int) -> Dict[str, Any]:
        """Informa del estado de la firma cruzada de tu cuenta:
        {configurado_en_servidor, claves_locales, master_pub}."""
        cliente = self._cliente_conectado(account_id)
        user_id = self._user_id(account_id)
        master_pub = ""
        configurado = False
        try:
            resp = self._api_http(account_id, "POST", "/_matrix/client/v3/keys/query",
                                  {"device_keys": {user_id: []}})
            master = ((resp.get("master_keys") or {}).get(user_id) or {})
            keys = master.get("keys") or {}
            if keys:
                configurado = True
                master_pub = list(keys.values())[0]
        except Exception as e:
            log.debug(f"Matrix: no se pudo consultar el estado de cross-signing: {e}")
        return {"configurado_en_servidor": configurado,
                "claves_locales": self.tengo_claves_xsign_local(account_id),
                "master_pub": master_pub}

    # ---------------- Bootstrap (crear la firma cruzada) ----------------
    def configurar_cross_signing(self, account_id: int, password: str):
        """Crea las tres claves de firma cruzada y las sube al servidor. Devuelve un dict con el
        estado:
        - {"estado": "ok"}: se subieron y este dispositivo quedó firmado.
        - {"estado": "aprobacion_navegador", "url": ...}: el servidor (autenticación moderna
          OAuth/MAS) exige APROBAR la operación en el navegador; abre esa URL, aprueba y luego
          llama a continuar_cross_signing_tras_aprobacion(account_id).
        Con contraseña (UIA clásica) se completa aquí mismo. Guarda las semillas privadas cifradas."""
        import olm
        cliente = self._cliente_conectado(account_id)
        user_id = self._user_id(account_id)
        device_id = getattr(cliente, "device_id", "") or ""
        if not device_id:
            raise MatrixError(_("La cuenta no tiene identificador de dispositivo (¿cifrado activo?)."))

        # 1) Generar las tres claves Ed25519 desde semillas aleatorias.
        master_seed = olm.PkSigning.generate_seed()
        self_seed = olm.PkSigning.generate_seed()
        user_seed = olm.PkSigning.generate_seed()
        master = olm.PkSigning(master_seed)
        self_k = olm.PkSigning(self_seed)
        user_k = olm.PkSigning(user_seed)
        mpub, spub, upub = master.public_key, self_k.public_key, user_k.public_key

        # 2) Objeto master_key: firmado por NUESTRO DISPOSITIVO (clave de migración: el que
        #    verifica nuestro dispositivo podrá confiar en esta clave maestra).
        master_obj = {"user_id": user_id, "usage": ["master"],
                      "keys": {f"ed25519:{mpub}": mpub}}
        firma_disp = cliente.olm.account.sign(_canon(cliente, master_obj))
        master_obj["signatures"] = {user_id: {f"ed25519:{device_id}": firma_disp}}

        # 3) self_signing_key y user_signing_key: firmadas por la MASTER.
        self_obj = {"user_id": user_id, "usage": ["self_signing"],
                    "keys": {f"ed25519:{spub}": spub}}
        self_obj["signatures"] = {user_id: {f"ed25519:{mpub}": master.sign(_canon(cliente, self_obj))}}
        user_obj = {"user_id": user_id, "usage": ["user_signing"],
                    "keys": {f"ed25519:{upub}": upub}}
        user_obj["signatures"] = {user_id: {f"ed25519:{mpub}": master.sign(_canon(cliente, user_obj))}}

        cuerpo = {"master_key": master_obj, "self_signing_key": self_obj,
                  "user_signing_key": user_obj}
        seeds = (master_seed, self_seed, user_seed)

        # 4) Subir con autenticación interactiva (UIA). Puede exigir contraseña (clásica) o
        #    aprobación en el navegador (OAuth/MAS).
        resultado = self._subir_con_uia(account_id, "/_matrix/client/v3/keys/device_signing/upload",
                                        cuerpo, user_id, password)
        if resultado.get("estado") == "aprobacion_navegador":
            # Guardar lo pendiente para completar tras la aprobación del usuario en el navegador.
            with self._estado_lock:
                self._xsign_pendiente[account_id] = {
                    "cuerpo": cuerpo, "seeds": seeds, "self_seed": self_seed, "spub": spub,
                    "session": resultado.get("session") or ""}
            return {"estado": "aprobacion_navegador", "url": resultado.get("url") or ""}

        # 5) Éxito directo (contraseña): guardar semillas y firmar el dispositivo.
        self._guardar_semillas_xsign(account_id, master_seed, self_seed, user_seed)
        self._firmar_dispositivo_propio(account_id, self_k, spub)
        log.info(f"Matrix: cross-signing configurado para {user_id}.")
        return {"estado": "ok"}

    def continuar_cross_signing_tras_aprobacion(self, account_id: int, intentos: int = 40):
        """Tras aprobar en el navegador (OAuth/MAS), reintenta la subida hasta que el servidor la
        acepta (sondea cada ~1,5 s hasta ~1 minuto). Al lograrlo, guarda las semillas y firma este
        dispositivo. Llamar desde task_manager (bloquea mientras sondea)."""
        import olm
        import time as _t
        with self._estado_lock:
            pend = (self._xsign_pendiente or {}).get(account_id)
        if not pend:
            raise MatrixError(_("No hay una configuración pendiente. Vuelve a empezar."))
        cuerpo = pend["cuerpo"]
        session = pend["session"]
        user_id = self._user_id(account_id)
        ruta = "/_matrix/client/v3/keys/device_signing/upload"
        ultimo_error = ""
        for _ in range(max(1, intentos)):
            estado, resp = self._post_uia(account_id, ruta,
                                          dict(cuerpo, auth={"session": session}) if session
                                          else cuerpo)
            if estado == 200:
                master_seed, self_seed, user_seed = pend["seeds"]
                self._guardar_semillas_xsign(account_id, master_seed, self_seed, user_seed)
                self._firmar_dispositivo_propio(account_id, olm.PkSigning(pend["self_seed"]),
                                                pend["spub"])
                with self._estado_lock:
                    self._xsign_pendiente.pop(account_id, None)
                log.info(f"Matrix: cross-signing configurado (OAuth) para {user_id}.")
                return {"estado": "ok"}
            ultimo_error = (resp or {}).get("error") or (resp or {}).get("errcode") or ""
            _t.sleep(1.5)
        raise MatrixError(_("Sigo sin poder confirmarlo. Asegúrate de haber APROBADO en la página "
                            "del navegador y vuelve a intentarlo. {0}").format(ultimo_error))

    def _post_uia(self, account_id: int, ruta: str, payload: dict):
        """POST autenticado directo. Devuelve (código_http, dict_respuesta). código 0 si hubo un
        error de red. Reutilizado por todo el flujo de firma cruzada."""
        import urllib.request
        import urllib.error
        cliente = self._cliente_conectado(account_id)
        url = cliente.homeserver.rstrip("/") + ruta
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Authorization", f"Bearer {cliente.access_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:
                return e.code, {}
        except Exception as e:
            return 0, {"error": str(e)}

    @staticmethod
    def _url_aprobacion(resp: dict) -> str:
        """Busca la URL de aprobación en los params de la respuesta UIA (autenticación moderna
        OAuth/MAS): puede venir bajo 'm.oauth' o 'org.matrix.cross_signing_reset'."""
        params = (resp or {}).get("params") or {}
        for clave in ("m.oauth", "org.matrix.cross_signing_reset", "org.matrix.msc3861.oauth"):
            info = params.get(clave) or {}
            url = info.get("url") or info.get("approval_url")
            if url:
                return url
        return ""

    def _subir_con_uia(self, account_id: int, ruta: str, cuerpo: dict, user_id: str, password: str):
        """Sube con User-Interactive Auth. Devuelve un dict con el estado:
        - {"estado": "ok"}: subida hecha (contraseña correcta o no hacía falta auth).
        - {"estado": "aprobacion_navegador", "url", "session"}: el servidor (OAuth/MAS) exige
          aprobar en el navegador; el llamador abre la URL y luego sondea con la session.
        Con contraseña (UIA clásica) se completa aquí. Errores reales del servidor: se muestran
        tal cual, sin adivinar."""
        localpart = user_id.lstrip("@").split(":", 1)[0]

        estado, resp = self._post_uia(account_id, ruta, cuerpo)
        if estado == 200:
            return {"estado": "ok"}
        if estado != 401:
            raise MatrixError(_("El servidor respondió {0}: {1}").format(
                estado, (resp or {}).get("error") or (resp or {}).get("errcode") or ""))

        # ¿Autenticación moderna (OAuth/MAS)? Entonces hay una URL de aprobación: se abre en el
        # navegador y se completa aprobando ahí (accesible, sin captcha).
        url_aprob = self._url_aprobacion(resp)
        etapas = {e for f in (resp.get("flows") or []) for e in (f.get("stages") or [])}
        if url_aprob or (etapas and "m.login.password" not in etapas and any(
                x in etapas for x in ("m.oauth", "org.matrix.cross_signing_reset",
                                      "org.matrix.msc3861.oauth"))):
            if not url_aprob:
                raise MatrixError(_("Tu servidor usa autenticación por navegador para esto pero no "
                                    "indicó la dirección de aprobación. Configúralo una vez desde "
                                    "Element; después esta aplicación ya lo reconocerá."))
            return {"estado": "aprobacion_navegador", "url": url_aprob,
                    "session": resp.get("session") or ""}

        # UIA clásica por contraseña.
        if etapas and "m.login.password" not in etapas:
            raise MatrixError(_("Tu servidor pide confirmar esto con: {0}, un método que no se "
                                "puede completar desde aquí. Hazlo una vez desde Element.").format(
                                    ", ".join(sorted(etapas))))
        if not password:
            raise MatrixError(_("El servidor pide tu contraseña para configurar la verificación "
                                "cruzada."))
        sesion = resp.get("session")
        for id_user in (user_id, localpart):
            auth = {"type": "m.login.password", "session": sesion, "password": password,
                    "identifier": {"type": "m.id.user", "user": id_user}}
            estado2, resp2 = self._post_uia(account_id, ruta, dict(cuerpo, auth=auth))
            if estado2 == 200:
                return {"estado": "ok"}
            completadas = (resp2 or {}).get("completed") or []
            if estado2 == 401 and "m.login.password" in completadas:
                pendientes = {e for f in ((resp2 or {}).get("flows") or [])
                              for e in (f.get("stages") or []) if e not in completadas}
                raise MatrixError(_("Tu contraseña era correcta, pero el servidor exige un paso "
                                    "adicional ({0}) que no se puede hacer aquí. Configura la "
                                    "verificación cruzada desde Element una vez.").format(
                                        ", ".join(sorted(pendientes)) or "?"))
            errcode = (resp2 or {}).get("errcode") or ""
            if errcode not in ("M_FORBIDDEN", "M_UNAUTHORIZED"):
                mensaje = (resp2 or {}).get("error") or errcode or str(resp2)
                raise MatrixError(_("El servidor rechazó la operación ({0}): {1}").format(estado2, mensaje))
        raise MatrixError(_("El servidor dice que la contraseña no es correcta (M_FORBIDDEN). "
                            "Escribe la contraseña actual de tu cuenta de Matrix; si la cambiaste "
                            "hace poco, usa la nueva."))

    def _get_device_keys_firmadas(self, account_id: int) -> dict:
        """Reconstruye el objeto device_keys de ESTE dispositivo (igual que el que nio sube),
        firmado por el propio dispositivo. Es lo que hay que firmar con la self-signing key."""
        cliente = self._cliente_conectado(account_id)
        user_id = self._user_id(account_id)
        device_id = getattr(cliente, "device_id", "") or ""
        ids = cliente.olm.account.identity_keys
        device_keys = {
            "algorithms": ["m.olm.v1.curve25519-aes-sha2", "m.megolm.v1.aes-sha2"],
            "device_id": device_id, "user_id": user_id,
            "keys": {f"curve25519:{device_id}": ids["curve25519"],
                     f"ed25519:{device_id}": ids["ed25519"]},
        }
        firma = cliente.olm.account.sign(_canon(cliente, device_keys))
        device_keys["signatures"] = {user_id: {f"ed25519:{device_id}": firma}}
        return device_keys

    def _firmar_dispositivo_propio(self, account_id: int, self_k, spub: str):
        """Firma el device_keys de ESTE dispositivo con la self-signing key y sube la firma."""
        user_id = self._user_id(account_id)
        device_id = getattr(self._cliente_conectado(account_id), "device_id", "") or ""
        cliente = self._cliente_conectado(account_id)
        device_keys = self._get_device_keys_firmadas(account_id)
        firma_ssk = self_k.sign(_canon(cliente, device_keys))
        # Enviar SOLO la firma nueva (la spec permite subir el objeto con las firmas a añadir).
        objeto = dict(device_keys)
        objeto["signatures"] = {user_id: {f"ed25519:{spub}": firma_ssk}}
        self._api_http(account_id, "POST", "/_matrix/client/v3/keys/signatures/upload",
                       {user_id: {device_id: objeto}})

    # ---------------- Firmar la clave maestra de OTRO usuario ----------------
    def firmar_usuario(self, account_id: int, user_id: str):
        """Firma la clave maestra de otro usuario con NUESTRA user-signing key: a partir de aquí
        confías en él por firma cruzada (y en todos sus dispositivos que él haya firmado). Se hace
        DESPUÉS de verificarle (por emojis o comprobando su clave). Requiere cross-signing propio."""
        claves = self._cargar_claves_xsign(account_id)
        if claves is None:
            raise MatrixError(_("Primero configura tu verificación cruzada (menú Matrix)."))
        _master, _self_k, user_k = claves
        upub = user_k.public_key
        cliente = self._cliente_conectado(account_id)
        yo = self._user_id(account_id)
        # Obtener la clave maestra del otro usuario.
        resp = self._api_http(account_id, "POST", "/_matrix/client/v3/keys/query",
                              {"device_keys": {user_id: []}})
        master = ((resp.get("master_keys") or {}).get(user_id) or {})
        if not master.get("keys"):
            raise MatrixError(_("{0} no tiene verificación cruzada configurada.").format(user_id))
        # Firmar el objeto master (sin firmas) con nuestra USK.
        objeto = {"user_id": master.get("user_id", user_id),
                  "usage": master.get("usage", ["master"]),
                  "keys": dict(master["keys"])}
        firma = user_k.sign(_canon(cliente, objeto))
        objeto_firmado = dict(objeto)
        objeto_firmado["signatures"] = {yo: {f"ed25519:{upub}": firma}}
        self._api_http(account_id, "POST", "/_matrix/client/v3/keys/signatures/upload",
                       {user_id: {list(master["keys"].keys())[0].split(":", 1)[1]: objeto_firmado}})
        log.info(f"Matrix: clave maestra de {user_id} firmada con nuestra USK.")

    def eliminar_cross_signing_local(self, account_id: int):
        """Borra las claves privadas de firma cruzada guardadas en este equipo (no toca el
        servidor). Lo usa eliminar_cuenta."""
        for nombre in ("xsign_master", "xsign_self", "xsign_user"):
            try:
                security.borrar_credencial("matrix", f"{account_id}:{nombre}")
            except Exception:
                pass
