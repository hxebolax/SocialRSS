# -*- coding: utf-8 -*-
"""Alta de cuenta, sesión, clientes y ciclo de vida de W Social.

Mixin de WSocialService. La diferencia con un cliente atado a un solo servidor es que aquí el PDS
se DESCUBRE a partir de la identidad del usuario (ver wsocial_identidad) y se guarda con la
cuenta: cada cuenta puede vivir en un servidor distinto.
"""
import threading

from app.core.config_manager import config
from app.services.wsocial.wsocial_common import (
    Any, Dict, db, security, WSocialServiceError, log, HAS_ATPROTO, Client, _g, PDS_POR_DEFECTO)
from app.services.wsocial import wsocial_identidad as identidad
from app.services.bluesky.bluesky_common import identidad_tras_login

# Mensaje ÚNICO de sesión caducada. Está en una constante porque la interfaz lo usa para
# reconocer este error concreto y ofrecer volver a autorizar la cuenta en el momento, en vez
# de dejar al usuario con un aviso que no lleva a ninguna parte.
MSG_SESION_CADUCADA = "La sesión de W Social ha caducado. Vuelve a iniciar sesión en la cuenta."


class _SesionMixin:
    def sesion_revocada(self, account_id: int) -> bool:
        """True si la sesión de esa cuenta está marcada como caducada o revocada. Es la forma
        FIABLE de saberlo (no depende del texto del mensaje de error)."""
        try:
            return int(account_id) in self._sesiones_revocadas
        except Exception:
            return False

    def _marcar_sesion_revocada(self, account_id: int):
        """Marca la sesión como caducada y AVISA una sola vez, para que la interfaz pueda
        ofrecer volver a autorizar la cuenta en el momento. Se hace aquí, en el único punto
        donde se detecta, y no en cada operación: así vale igual para lo que estés haciendo
        tú que para el sondeo de fondo o el tiempo real."""
        if account_id in self._sesiones_revocadas:
            return
        self._sesiones_revocadas.add(account_id)
        try:
            from app.core.event_bus import event_bus
            event_bus.emit("wsocial_sesion_caducada", account_id)
        except Exception as e:
            log.debug(f"No se pudo avisar de la sesión caducada de W Social: {e}")
    # ------------------------------------------------------------------
    # ALTA Y SESIÓN
    # ------------------------------------------------------------------
    def resolver_cuenta(self, identificador: str, pds_sugerido: str = "") -> Dict[str, Any]:
        """Averigua a qué servidor pertenece un usuario ANTES de pedirle la contraseña.

        Devuelve {did, handle, pds, servidor} para poder enseñárselo al usuario: es importante
        que vea con qué servidor va a hablar, sobre todo en una red donde cada cuenta puede
        estar alojada en un sitio distinto."""
        info = identidad.resolver_identidad(identificador, pds_sugerido or PDS_POR_DEFECTO)
        info["servidor"] = identidad.describir_servidor(info["pds"])
        return info

    def completar_login(self, identificador: str, app_password: str,
                        pds_url: str = "") -> Dict[str, Any]:
        """Inicia sesión con el usuario y una contraseña de aplicación, guarda la sesión cifrada
        y crea la cuenta. Devuelve {account_id, handle, did, pds}.

        El servidor NO se impone: se resuelve la identidad del usuario y se usa el PDS que ella
        declare. `pds_url` solo actúa como pista (y como respaldo si la resolución no diera con
        el servidor), nunca pisa lo que diga la identidad.
        """
        if not HAS_ATPROTO:
            raise WSocialServiceError("La biblioteca atproto no está instalada.")
        info = identidad.resolver_identidad(identificador, (pds_url or PDS_POR_DEFECTO))
        did, pds = info["did"], info["pds"]
        handle = info.get("handle") or identidad.normalizar_identificador(identificador)
        try:
            client = Client(base_url=pds)
            profile = client.login(handle or did, app_password)
            session_string = client.export_session_string()
            did_login, handle_real = identidad_tras_login(client, profile, handle)
            did = did_login or did

            # Impedir DIDs DUPLICADOS: la misma cuenta dos veces haría que ambas rotasen el mismo
            # token de refresco y se revocasen la sesión mutuamente.
            existente = self._cuenta_con_did(did)
            if existente:
                raise WSocialServiceError(
                    "Esa cuenta de W Social ya está añadida (@{0}). Si su sesión caducó, usa "
                    "'Volver a autorizar' en el gestor de cuentas en vez de añadirla otra vez.".format(
                        security.obtener_credencial(f"wsocial:{existente}", "handle") or handle_real))

            row_id = db.execute_insert(
                "INSERT INTO accounts (name, service_id, active, position) "
                "VALUES (?, 'wsocial', 1, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))",
                (f"@{handle_real}",),
            )
            # Credenciales en UNA escritura atómica: si el almacén seguro falla, se deshace el
            # alta entera en vez de dejar una cuenta que fallará en cada arranque.
            ok = security.guardar_credenciales({
                (f"wsocial:{row_id}", "session_string"): session_string,
                (f"wsocial:{row_id}", "pds_url"): pds,
                (f"wsocial:{row_id}", "handle"): handle_real,
                (f"wsocial:{row_id}", "did"): did,
            })
            if ok is False:
                db.execute("DELETE FROM accounts WHERE id = ?", (row_id,))
                raise WSocialServiceError(
                    "No se pudieron guardar las credenciales en el almacén seguro; el alta se ha "
                    "deshecho. Revisa el espacio en disco y vuelve a intentarlo.")

            self._clients[row_id] = client
            self._me[row_id] = {"did": did, "handle": handle_real, "pds": pds}
            self._registrar_persistencia_sesion(row_id, client)
            log.info(f"Cuenta de W Social registrada: @{handle_real} en {pds} (ID {row_id})")
            return {"account_id": row_id, "handle": handle_real, "did": did, "pds": pds}
        except WSocialServiceError:
            raise
        except Exception as e:
            log.error(f"Error al iniciar sesión en W Social: {e}")
            raise WSocialServiceError(self._mensaje_error(e))

    def _cuenta_con_did(self, did: str):
        """account_id de la cuenta de W Social ya registrada con ese DID, o None."""
        if not did:
            return None
        for (acc_id,) in db.query("SELECT id FROM accounts WHERE service_id = 'wsocial'"):
            if (security.obtener_credencial(f"wsocial:{acc_id}", "did") or "") == did:
                return acc_id
        return None

    def reautenticar(self, account_id: int, identificador: str, app_password: str,
                     pds_url: str = None) -> Dict[str, Any]:
        """'Volver a autorizar': renueva la sesión de una cuenta EXISTENTE conservando su
        account_id, su DID y toda su caché local. Comprueba que la sesión nueva es de la MISMA
        cuenta (mismo DID) para no mezclar datos de dos identidades."""
        if not HAS_ATPROTO:
            raise WSocialServiceError("La biblioteca atproto no está instalada.")
        did_guardado = security.obtener_credencial(f"wsocial:{account_id}", "did") or ""
        handle_guardado = security.obtener_credencial(f"wsocial:{account_id}", "handle") or ""
        pds_guardado = security.obtener_credencial(f"wsocial:{account_id}", "pds_url") or ""
        identificador = (identificador or "").strip().lstrip("@") or handle_guardado or did_guardado
        # Se vuelve a resolver la identidad: el usuario pudo cambiar de handle o MIGRAR su cuenta
        # a otro servidor, y en ese caso hay que entrar en el nuevo, no en el de antes.
        try:
            info = identidad.resolver_identidad(identificador, pds_url or pds_guardado or PDS_POR_DEFECTO,
                                                usar_cache=False)
            pds = info["pds"]
            handle = info.get("handle") or identificador
        except WSocialServiceError:
            if not (pds_url or pds_guardado):
                raise
            pds = (pds_url or pds_guardado).strip().rstrip("/")
            handle = identificador
        try:
            client = Client(base_url=pds)
            profile = client.login(handle, app_password)
            did, handle_real = identidad_tras_login(client, profile, handle)
            if did_guardado and did and did != did_guardado:
                raise WSocialServiceError(
                    "Has iniciado sesión con OTRA cuenta distinta (@{0}). Para no mezclar los "
                    "datos locales, vuelve a autorizar con la misma cuenta o añade la nueva desde "
                    "'Gestionar cuentas'.".format(handle_real))
            self._registrar_persistencia_sesion(account_id, client)
            credenciales = {
                (f"wsocial:{account_id}", "session_string"): client.export_session_string(),
                (f"wsocial:{account_id}", "pds_url"): pds,
                (f"wsocial:{account_id}", "handle"): handle_real,
            }
            if did:
                credenciales[(f"wsocial:{account_id}", "did")] = did
            if security.guardar_credenciales(credenciales) is False:
                raise WSocialServiceError("No se pudo guardar la sesión renovada en el almacén seguro.")
            self._clients[account_id] = client
            self._chats.pop(account_id, None)
            self._me[account_id] = {"did": did or did_guardado, "handle": handle_real, "pds": pds}
            self._sesiones_revocadas.discard(account_id)
            self._cooldown_429.pop(account_id, None)
            try:
                db.execute("UPDATE accounts SET name = ? WHERE id = ?", (f"@{handle_real}", account_id))
            except Exception:
                pass
            log.info(f"Cuenta W Social {account_id} reautorizada (@{handle_real} en {pds}).")
            return {"account_id": account_id, "handle": handle_real,
                    "did": did or did_guardado, "pds": pds}
        except WSocialServiceError:
            raise
        except Exception as e:
            log.error(f"Error al reautorizar la cuenta de W Social {account_id}: {e}")
            raise WSocialServiceError(self._mensaje_error(e))

    def _registrar_persistencia_sesion(self, account_id: int, client: Any):
        """Persiste la sesión cada vez que atproto rota los tokens."""
        try:
            def _on_change(event, session):
                try:
                    s = client.export_session_string()
                    security.guardar_credencial(f"wsocial:{account_id}", "session_string", s)
                except Exception as e:
                    log.debug(f"No se pudo persistir la sesión de W Social {account_id}: {e}")
            client.on_session_change(_on_change)
        except Exception as e:
            log.debug(f"No se pudo registrar on_session_change: {e}")

    def _lock_de_cuenta(self, account_id: int):
        """Lock (reentrante) que serializa la creación del Client de una cuenta: si varias tareas
        de fondo lo piden a la vez, solo UNA reanuda la sesión. Sin esto, varios Client reanudarían
        con el mismo token de refresco y el servidor los revocaría todos menos uno."""
        with self._client_locks_guard:
            lock = self._client_locks.get(account_id)
            if lock is None:
                lock = threading.RLock()
                self._client_locks[account_id] = lock
            return lock

    def obtener_cliente(self, account_id: int) -> Any:
        """Client autenticado de la cuenta, con caché y reanudación de sesión."""
        cli = self._clients.get(account_id)
        if cli is not None:
            return cli
        if not HAS_ATPROTO:
            raise WSocialServiceError("La biblioteca atproto no está instalada.")
        if account_id in self._sesiones_revocadas:
            raise WSocialServiceError(
                MSG_SESION_CADUCADA)
        self._comprobar_cooldown(account_id)

        with self._lock_de_cuenta(account_id):
            cli = self._clients.get(account_id)
            if cli is not None:
                return cli
            if account_id in self._sesiones_revocadas:
                raise WSocialServiceError(
                    MSG_SESION_CADUCADA)

            session_string = security.obtener_credencial(f"wsocial:{account_id}", "session_string")
            pds_url = security.obtener_credencial(f"wsocial:{account_id}", "pds_url") or PDS_POR_DEFECTO
            if not session_string:
                raise WSocialServiceError(f"No hay sesión guardada para la cuenta de W Social {account_id}.")

            import time as _time
            ultimo_error = None
            # Hasta 3 intentos: un bache de red no debe leerse como "sesión perdida". Los errores
            # de TOKEN sí son definitivos. NUNCA se borra la sesión guardada.
            for intento in range(3):
                try:
                    client = Client(base_url=pds_url)
                    # La persistencia se registra ANTES del login: al reanudar, atproto puede rotar
                    # el token durante el propio login, y esa rotación hay que guardarla.
                    self._registrar_persistencia_sesion(account_id, client)
                    client.login(session_string=session_string)
                    # Cabecera con los servicios de etiquetado suscritos: sin ella, sus etiquetas
                    # no llegan hidratadas en las respuestas.
                    try:
                        self.sincronizar_labelers_servidor(account_id, client)
                    except Exception as e:
                        log.debug(f"No se pudo configurar la cabecera de etiquetadores: {e}")
                    self._clients[account_id] = client
                    self._sesiones_revocadas.discard(account_id)
                    try:
                        nueva = client.export_session_string()
                        if nueva and nueva != session_string:
                            security.guardar_credencial(f"wsocial:{account_id}", "session_string", nueva)
                    except Exception:
                        pass
                    return client
                except Exception as e:
                    ultimo_error = e
                    codigo = self._codigo_error(e)
                    if codigo in ("ExpiredToken", "InvalidToken", "AccountTakedown") or \
                            "LoginRequired" in codigo:
                        self._marcar_sesion_revocada(account_id)
                        break
                    if self._es_error_limite(e):
                        self.registrar_limite_429(account_id, e)
                        break
                    if intento < 2:
                        _time.sleep(1.5 * (intento + 1))
            log.error(f"Error al reanudar la sesión de W Social {account_id}: {ultimo_error}")
            raise WSocialServiceError(self._mensaje_error(ultimo_error))

    def _chat_capaz(self, account_id: int) -> Any:
        """Cliente con el proxy de chat (mensajes directos y grupos). Es un clon del Client que
        COMPARTE su sesión; se crea una sola vez bajo el lock de la cuenta."""
        cli = self._chats.get(account_id)
        if cli is not None:
            return cli
        with self._lock_de_cuenta(account_id):
            cli = self._chats.get(account_id)
            if cli is None:
                client = self.obtener_cliente(account_id)
                try:
                    cli = client.with_bsky_chat_proxy()
                except Exception as e:
                    raise WSocialServiceError(
                        "Este servidor no ofrece el servicio de mensajes directos: {0}".format(e))
                self._chats[account_id] = cli
            return cli

    def _yo(self, account_id: int) -> Dict[str, str]:
        """{did, handle, pds} de la cuenta (cacheado)."""
        cached = self._me.get(account_id)
        if cached is not None:
            return cached
        with self._lock_de_cuenta(account_id):
            cached = self._me.get(account_id)
            if cached is None:
                client = self.obtener_cliente(account_id)
                yo = getattr(client, "me", None)
                cached = {
                    "did": _g(yo, "did", default=security.obtener_credencial(f"wsocial:{account_id}", "did") or ""),
                    "handle": _g(yo, "handle", default=security.obtener_credencial(f"wsocial:{account_id}", "handle") or ""),
                    "pds": security.obtener_credencial(f"wsocial:{account_id}", "pds_url") or PDS_POR_DEFECTO,
                }
                self._me[account_id] = cached
            return cached

    def pds_de_cuenta(self, account_id: int) -> str:
        """URL del servidor (PDS) donde vive esa cuenta. Para mostrarlo en la interfaz."""
        return security.obtener_credencial(f"wsocial:{account_id}", "pds_url") or ""

    def cerrar_sesion_servidor(self, account_id: int) -> bool:
        """Revoca la sesión en el SERVIDOR (com.atproto.server.deleteSession) antes de olvidarla
        aquí. Devuelve False si no se pudo (la cuenta se borra igual en local, pero conviene
        avisar de que la sesión sigue viva en el servidor)."""
        try:
            client = self.obtener_cliente(account_id)
            client.com.atproto.server.delete_session()
            return True
        except Exception as e:
            log.debug(f"W Social: no se pudo revocar la sesión en el servidor: {e}")
            return False

    # ------------------------------------------------------------------
    # CONTRASEÑAS DE APLICACIÓN Y ESTADO DE LA CUENTA
    # ------------------------------------------------------------------
    def listar_app_passwords_async(self, account_id: int, callback=None):
        """Contraseñas de aplicación de la cuenta (nombre y fecha, nunca el secreto)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.com.atproto.server.list_app_passwords()
            salida = []
            for p in (_g(resp, "passwords", default=[]) or []):
                salida.append({
                    "name": _g(p, "name", default=""),
                    "created_at": _g(p, "created_at", "createdAt", default=""),
                    "privileged": bool(_g(p, "privileged", default=False)),
                })
            return salida
        self._tarea(_bg, callback)

    def crear_app_password_async(self, account_id: int, nombre: str, privilegiada: bool = False,
                                 callback=None):
        """Crea una contraseña de aplicación. `privilegiada` la habilita para mensajes directos."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.com.atproto.server.create_app_password(
                {"name": nombre, "privileged": bool(privilegiada)})
            return {"name": _g(resp, "name", default=nombre),
                    "password": _g(resp, "password", default=""),
                    "created_at": _g(resp, "created_at", "createdAt", default="")}
        self._tarea(_bg, callback)

    def revocar_app_password_async(self, account_id: int, nombre: str, callback=None):
        """Revoca una contraseña de aplicación por su nombre."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.com.atproto.server.revoke_app_password({"name": nombre})
            return True
        self._tarea(_bg, callback)

    def estado_cuenta_async(self, account_id: int, callback=None):
        """`com.atproto.server.checkAccountStatus` + datos de sesión: si está activada, cuántos
        registros tiene el repositorio, blobs pendientes de importar, etc. Es la información que
        de verdad dice si una cuenta está sana o a medio migrar."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            estado = client.com.atproto.server.check_account_status()
            sesion = client.com.atproto.server.get_session()
            return {
                "activada": bool(_g(estado, "activated", default=False)),
                "repo_confirmado": bool(_g(estado, "valid_did", "validDid", default=False)),
                "registros": _g(estado, "repo_rev", "repoRev", default=""),
                "blobs_importados": _g(estado, "imported_blobs", "importedBlobs", default=0),
                "blobs_esperados": _g(estado, "expected_blobs", "expectedBlobs", default=0),
                "handle": _g(sesion, "handle", default=""),
                "did": _g(sesion, "did", default=""),
                "correo": _g(sesion, "email", default=""),
                "correo_confirmado": bool(_g(sesion, "email_confirmed", "emailConfirmed", default=False)),
                "pds": self.pds_de_cuenta(account_id),
            }
        self._tarea(_bg, callback)

    def describir_servidor_async(self, account_id: int, callback=None):
        """Datos públicos del servidor de la cuenta (dominios disponibles, si exige invitación,
        enlaces a sus condiciones y privacidad)."""
        def _bg(ctx):
            pds = self.pds_de_cuenta(account_id)
            datos = identidad.describir_servidor(pds)
            enlaces = datos.get("links", {}) or {}
            return {
                "pds": pds,
                "did": datos.get("did", ""),
                "dominios": list(datos.get("availableUserDomains", []) or []),
                "requiere_invitacion": bool(datos.get("inviteCodeRequired", False)),
                "requiere_verificacion_telefono": bool(datos.get("phoneVerificationRequired", False)),
                "privacidad": enlaces.get("privacyPolicy", ""),
                "condiciones": enlaces.get("termsOfService", ""),
                "contacto": (datos.get("contact", {}) or {}).get("email", ""),
            }
        self._tarea(_bg, callback)

    def cambiar_handle_async(self, account_id: int, nuevo_handle: str, callback=None):
        """Cambia el nombre de usuario (com.atproto.identity.updateHandle) y actualiza lo local."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            handle = (nuevo_handle or "").strip().lstrip("@").lower()
            if not handle:
                raise ValueError("Escribe el nuevo nombre de usuario.")
            client.com.atproto.identity.update_handle({"handle": handle})
            security.guardar_credencial(f"wsocial:{account_id}", "handle", handle)
            db.execute("UPDATE accounts SET name = ? WHERE id = ?", (f"@{handle}", account_id))
            self._me.pop(account_id, None)
            identidad.limpiar_cache()
            return handle
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # MANTENIMIENTO DE LA CACHÉ LOCAL
    # ------------------------------------------------------------------
    def podar_antiguos(self, account_id: int, max_buffer: int = 0, max_notif: int = 0):
        """Recorta el histórico local. Conserva marcadores y los buffers curados (me gusta,
        marcadores, enviados). Un límite a 0 significa SIN límite."""
        try:
            if max_buffer and max_buffer > 0:
                for tl in ("home", "mentions", "sent"):
                    db.execute("""
                        DELETE FROM wsocial_timeline_posts
                        WHERE account_id = ? AND timeline = ? AND post_db_id NOT IN (
                            SELECT t.post_db_id FROM wsocial_timeline_posts t
                            JOIN wsocial_posts p ON p.id = t.post_db_id
                            WHERE t.account_id = ? AND t.timeline = ?
                            ORDER BY p.indexed_at DESC LIMIT ?
                        )
                    """, (account_id, tl, account_id, tl, max_buffer))
                cond = ("account_id = ? AND COALESCE(bookmarked, 0) = 0 "
                        "AND id NOT IN (SELECT post_db_id FROM wsocial_timeline_posts WHERE account_id = ?)")
                try:
                    db.execute(f"DELETE FROM wsocial_posts_fts WHERE post_db_id IN "
                               f"(SELECT id FROM wsocial_posts WHERE {cond})", (account_id, account_id))
                except Exception:
                    pass
                db.execute(f"DELETE FROM wsocial_posts WHERE {cond}", (account_id, account_id))
            if max_notif and max_notif > 0:
                db.execute("""
                    DELETE FROM wsocial_notifications
                    WHERE account_id = ? AND id NOT IN (
                        SELECT id FROM wsocial_notifications WHERE account_id = ?
                        ORDER BY indexed_at DESC LIMIT ?
                    )
                """, (account_id, account_id, max_notif))
            try:
                db.execute("DELETE FROM wsocial_posts_fts WHERE post_db_id NOT IN "
                           "(SELECT id FROM wsocial_posts)")
            except Exception:
                pass
        except Exception as e:
            log.debug(f"No se pudo podar el histórico de W Social (cuenta {account_id}): {e}")

    def vaciar_cache_sesion(self, account_id: int):
        """Vacía los buffers VOLÁTILES (Inicio, Menciones, feeds, listas, búsquedas y
        notificaciones) conservando Me gusta, Marcadores, Enviados y los mensajes directos."""
        try:
            db.execute(
                "DELETE FROM wsocial_timeline_posts WHERE account_id = ? AND ("
                "timeline IN ('home','mentions') OR timeline LIKE 'feed:%' "
                "OR timeline LIKE 'list:%' OR timeline LIKE 'search:%')", (account_id,))
            db.execute("DELETE FROM wsocial_notifications WHERE account_id = ?", (account_id,))
            cond = ("account_id = ? AND id NOT IN "
                    "(SELECT post_db_id FROM wsocial_timeline_posts WHERE account_id = ?)")
            try:
                db.execute(f"DELETE FROM wsocial_posts_fts WHERE post_db_id IN "
                           f"(SELECT id FROM wsocial_posts WHERE {cond})", (account_id, account_id))
            except Exception:
                pass
            db.execute(f"DELETE FROM wsocial_posts WHERE {cond}", (account_id, account_id))
        except Exception as e:
            log.debug(f"No se pudo vaciar la caché de sesión de W Social (cuenta {account_id}): {e}")

    def eliminar_cuenta(self, account_id: int):
        """Elimina la cuenta local, su caché y sus secretos (la cuenta del servidor no se toca)."""
        try:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            for clave in ("session_string", "pds_url", "handle", "did"):
                security.borrar_credencial(f"wsocial:{account_id}", clave)
            self._clients.pop(account_id, None)
            self._chats.pop(account_id, None)
            self._me.pop(account_id, None)
            self._sesiones_revocadas.discard(account_id)
            log.info(f"Cuenta de W Social ID {account_id} eliminada.")
        except Exception as e:
            log.error(f"Error al eliminar la cuenta de W Social {account_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # POLÍTICA CENTRAL DE LÍMITES (429) Y ERRORES
    # ------------------------------------------------------------------
    def _es_error_limite(self, e: Exception) -> bool:
        """True si el error es un límite de peticiones (código RateLimitExceeded o HTTP 429)."""
        if "ratelimit" in self._codigo_error(e).lower():
            return True
        try:
            return getattr(getattr(e, "response", None), "status_code", None) == 429
        except Exception:
            return False

    @staticmethod
    def _espera_de_limite(e: Exception) -> float:
        """Segundos hasta poder reintentar tras un 429, respetando las cabeceras del servidor
        (retry-after o ratelimit-reset). Sin cabeceras, 5 minutos."""
        import time as _t
        try:
            headers = getattr(getattr(e, "response", None), "headers", None) or {}
            h = {str(k).lower(): v for k, v in dict(headers).items()}
            if h.get("retry-after"):
                return max(1.0, float(h["retry-after"]))
            if h.get("ratelimit-reset"):
                return max(1.0, float(h["ratelimit-reset"]) - _t.time())
        except Exception:
            pass
        return 300.0

    def registrar_limite_429(self, account_id: int, e: Exception) -> float:
        """Pone la cuenta en pausa hasta el momento que indique el servidor (tope 1 h)."""
        import time as _t
        hasta = _t.time() + min(self._espera_de_limite(e), 3600.0)
        self._cooldown_429[account_id] = hasta
        log.warning(f"W Social {account_id}: límite de peticiones; en pausa hasta "
                    f"{_t.strftime('%H:%M:%S', _t.localtime(hasta))}")
        return hasta

    def _comprobar_cooldown(self, account_id: int):
        """Si la cuenta está en pausa por un 429, falla ya con la hora de reintento (la interfaz
        sigue mostrando lo que tenga descargado)."""
        import time as _t
        hasta = self._cooldown_429.get(account_id, 0)
        if hasta <= _t.time():
            self._cooldown_429.pop(account_id, None)
            return
        raise WSocialServiceError(
            "W Social ha limitado temporalmente las peticiones de esta cuenta. Se podrá "
            "reintentar a las {0}; mientras, se muestran los datos ya descargados.".format(
                _t.strftime("%H:%M", _t.localtime(hasta))))

    def _get_con_reintentos(self, account_id: int, fn, intentos: int = 3):
        """Ejecuta una LECTURA idempotente con reintentos (espera exponencial con azar). Los
        límites registran la pausa y NO se reintentan; los errores de token se propagan. Las
        ESCRITURAS no pasan por aquí: su seguridad es la clave idempotente y la bandeja de salida."""
        import random as _rnd
        import time as _t
        ultimo = None
        for i in range(max(1, intentos)):
            try:
                return fn()
            except Exception as e:
                ultimo = e
                if self._es_error_limite(e):
                    self.registrar_limite_429(account_id, e)
                    raise WSocialServiceError(self._mensaje_error(e))
                codigo = self._codigo_error(e)
                if codigo in ("ExpiredToken", "InvalidToken", "AccountTakedown") or \
                        "LoginRequired" in codigo:
                    raise
                if i < intentos - 1:
                    _t.sleep(_rnd.uniform(0, 0.8 * (2 ** i)))
        raise ultimo

    @staticmethod
    def _codigo_error(e: Exception) -> str:
        """Código de error limpio de atproto (p. ej. 'ExpiredToken'), leído de
        e.response.content.error. Más fiable que str(e), que arrastra las cabeceras HTTP."""
        try:
            resp = getattr(e, "response", None)
            content = getattr(resp, "content", None)
            cod = getattr(content, "error", None)
            if cod:
                return str(cod)
        except Exception:
            pass
        return ""

    def _mensaje_error(self, e: Exception) -> str:
        """Traduce los errores habituales a mensajes claros en español."""
        codigo = self._codigo_error(e)
        cb = codigo.lower()
        texto = str(e)
        bajo = texto.lower()
        if codigo:
            if cb in ("expiredtoken", "invalidtoken") or "loginrequired" in cb:
                return MSG_SESION_CADUCADA
            if "ratelimit" in cb:
                return "Has alcanzado el límite de peticiones de W Social. Espera unos minutos e inténtalo de nuevo."
            if cb in ("invalidrequest", "authenticationrequired") and (
                    "password" in bajo or "identifier" in bajo or "credential" in bajo):
                return ("Usuario o contraseña de aplicación incorrectos. Recuerda usar una "
                        "contraseña de aplicación, no la de tu cuenta.")
            if cb == "accounttakedown":
                return "La cuenta está suspendida en el servidor de W Social."
        if "invalid" in bajo and ("password" in bajo or "identifier" in bajo or "credential" in bajo):
            return ("Usuario o contraseña de aplicación incorrectos. Recuerda usar una contraseña "
                    "de aplicación, no la de tu cuenta.")
        if not codigo and ("rate limit" in bajo or "ratelimitexceeded" in bajo):
            return "Has alcanzado el límite de peticiones de W Social. Espera unos minutos e inténtalo de nuevo."
        if "could not find" in bajo or "not found" in bajo:
            return "No se ha encontrado el recurso solicitado en W Social."
        if "expired" in bajo or "revoked" in bajo:
            return MSG_SESION_CADUCADA
        if "network" in bajo or "timeout" in bajo or "connection" in bajo:
            return "Problema de conexión con W Social. Comprueba tu red e inténtalo de nuevo."
        return f"Error de W Social: {texto}"
