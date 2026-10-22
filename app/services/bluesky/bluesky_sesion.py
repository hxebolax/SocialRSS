# -*- coding: utf-8 -*-
"""Alta de cuenta, sesión, clientes y ciclo de vida.

Mixin de BlueskyService (parte de la refactorización modular).
"""
from app.core.config_manager import config
from app.services.bluesky.bluesky_common import (
    Any, Dict, db, security, BlueskyServiceError, log, HAS_ATPROTO, Client, _g,
    identidad_tras_login)


class _SesionMixin:
    # ------------------------------------------------------------------
    # ALTA Y SESIÓN
    # ------------------------------------------------------------------
    def completar_login(self, handle: str, app_password: str,
                        pds_url: str = "https://bsky.social") -> Dict[str, Any]:
        """
        Inicia sesión con handle + app password, guarda la sesión cifrada y crea la cuenta.
        Devuelve {account_id, handle, did}.
        """
        if not HAS_ATPROTO:
            raise BlueskyServiceError("La biblioteca atproto no está instalada.")
        pds_url = (pds_url or "https://bsky.social").strip().rstrip("/")
        # El PDS introducido a mano debe ser HTTPS (auditoría 9.2): un http:// mandaría la
        # contraseña de aplicación en claro.
        if not pds_url.lower().startswith("https://"):
            raise BlueskyServiceError("La dirección del servidor (PDS) debe empezar por https://")
        handle = handle.strip().lstrip("@")
        try:
            client = Client(base_url=pds_url)
            profile = client.login(handle, app_password)
            session_string = client.export_session_string()

            did, handle_real = identidad_tras_login(client, profile, handle)

            # Impedir DIDs DUPLICADOS (auditoría 9.2): la misma cuenta dos veces provoca
            # que ambas roten el mismo refresh token y se revoquen mutuamente la sesión.
            existente = self._cuenta_con_did(did)
            if existente:
                raise BlueskyServiceError(
                    "Esa cuenta de Bluesky ya está añadida (@{0}). Si su sesión caducó, usa "
                    "'Volver a autorizar' en el gestor de cuentas en vez de añadirla otra vez.".format(
                        security.obtener_credencial(f"bluesky:{existente}", "handle") or handle_real))

            # Posición al FINAL (max+1): una cuenta nueva no salta al principio del gestor.
            row_id = db.execute_insert(
                "INSERT INTO accounts (name, service_id, active, position) "
                "VALUES (?, 'bluesky', 1, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))",
                (f"@{handle_real}",),
            )

            # Guardado EN BLOQUE y COMPENSADO (auditoría 9.2): las credenciales van en una
            # única actualización atómica; si el almacén (DPAPI/disco) falla, se deshace el
            # alta entera en vez de dejar una cuenta huérfana que falla en cada arranque.
            ok = security.guardar_credenciales({
                (f"bluesky:{row_id}", "session_string"): session_string,
                (f"bluesky:{row_id}", "pds_url"): pds_url,
                (f"bluesky:{row_id}", "handle"): handle_real,
                (f"bluesky:{row_id}", "did"): did,
            })
            if ok is False:
                db.execute("DELETE FROM accounts WHERE id = ?", (row_id,))
                raise BlueskyServiceError(
                    "No se pudieron guardar las credenciales en el almacén seguro; el alta "
                    "se ha deshecho. Revisa el espacio en disco y vuelve a intentarlo.")

            # Cachear cliente ya autenticado y registrar persistencia de sesión.
            self._clients[row_id] = client
            self._me[row_id] = {"did": did, "handle": handle_real}
            self._registrar_persistencia_sesion(row_id, client)
            # Cabecera de labelers (B1): Bluesky Moderation desde el primer momento; los
            # suscritos se añaden al leer las preferencias (primer refresco del timeline).
            self.aplicar_cabecera_labelers_cacheada(row_id, client)

            log.info(f"Cuenta de Bluesky registrada: @{handle_real} (ID {row_id})")
            return {"account_id": row_id, "handle": handle_real, "did": did}
        except BlueskyServiceError:
            raise
        except Exception as e:
            log.error(f"Error al iniciar sesión en Bluesky: {e}")
            raise BlueskyServiceError(self._mensaje_error(e))

    def _cuenta_con_did(self, did: str):
        """Devuelve el account_id de la cuenta de Bluesky ya registrada con ese DID, o None."""
        if not did:
            return None
        for (acc_id,) in db.query("SELECT id FROM accounts WHERE service_id = 'bluesky'"):
            if (security.obtener_credencial(f"bluesky:{acc_id}", "did") or "") == did:
                return acc_id
        return None

    def reautenticar(self, account_id: int, handle: str, app_password: str,
                     pds_url: str = None) -> Dict[str, Any]:
        """'Volver a autorizar' (auditoría 9.2): renueva la sesión de una cuenta EXISTENTE
        conservando su account_id, su DID y toda su caché local (buffers, marcadores...).
        Antes, una sesión revocada obligaba a borrar la cuenta y añadirla de nuevo,
        perdiendo las asociaciones. Verifica que la nueva sesión es de la MISMA cuenta
        (mismo DID): si no, se rechaza para no mezclar datos de dos identidades."""
        if not HAS_ATPROTO:
            raise BlueskyServiceError("La biblioteca atproto no está instalada.")
        pds_url = (pds_url or security.obtener_credencial(f"bluesky:{account_id}", "pds_url")
                   or "https://bsky.social").strip().rstrip("/")
        if not pds_url.lower().startswith("https://"):
            raise BlueskyServiceError("La dirección del servidor (PDS) debe empezar por https://")
        handle = (handle or "").strip().lstrip("@") or \
            (security.obtener_credencial(f"bluesky:{account_id}", "handle") or "")
        did_guardado = security.obtener_credencial(f"bluesky:{account_id}", "did") or ""
        try:
            client = Client(base_url=pds_url)
            profile = client.login(handle, app_password)
            did, handle_real = identidad_tras_login(client, profile, handle)
            if did_guardado and did and did != did_guardado:
                raise BlueskyServiceError(
                    "Has iniciado sesión con OTRA cuenta distinta (@{0}). Para no mezclar los "
                    "datos locales, vuelve a autorizar con la misma cuenta o añade la nueva "
                    "desde 'Gestionar cuentas'.".format(handle_real))
            self._registrar_persistencia_sesion(account_id, client)
            credenciales = {
                (f"bluesky:{account_id}", "session_string"): client.export_session_string(),
                (f"bluesky:{account_id}", "pds_url"): pds_url,
                (f"bluesky:{account_id}", "handle"): handle_real,
            }
            if did:
                credenciales[(f"bluesky:{account_id}", "did")] = did
            if security.guardar_credenciales(credenciales) is False:
                raise BlueskyServiceError(
                    "No se pudo guardar la sesión renovada en el almacén seguro.")
            # Sustituir el cliente en caliente y levantar los bloqueos de la cuenta.
            self.aplicar_cabecera_labelers_cacheada(account_id, client)
            self._clients[account_id] = client
            self._chats.pop(account_id, None)
            self._me[account_id] = {"did": did or did_guardado, "handle": handle_real}
            self._sesiones_revocadas.discard(account_id)
            self._cooldown_429.pop(account_id, None)
            try:
                db.execute("UPDATE accounts SET name = ? WHERE id = ?",
                           (f"@{handle_real}", account_id))
            except Exception:
                pass
            log.info(f"Cuenta Bluesky {account_id} reautorizada (@{handle_real}).")
            return {"account_id": account_id, "handle": handle_real, "did": did or did_guardado}
        except BlueskyServiceError:
            raise
        except Exception as e:
            log.error(f"Error al reautorizar la cuenta Bluesky {account_id}: {e}")
            raise BlueskyServiceError(self._mensaje_error(e))

    def _registrar_persistencia_sesion(self, account_id: int, client: Any):
        """Persiste el session string cada vez que atproto rota los tokens."""
        try:
            def _on_change(event, session):
                try:
                    s = client.export_session_string()
                    security.guardar_credencial(f"bluesky:{account_id}", "session_string", s)
                except Exception as e:
                    log.debug(f"No se pudo persistir la sesión Bluesky {account_id}: {e}")
            client.on_session_change(_on_change)
        except Exception as e:
            log.debug(f"No se pudo registrar on_session_change: {e}")

    def _lock_de_cuenta(self, account_id: int):
        """Devuelve (creándolo si hace falta) el lock que serializa la creación del Client de
        una cuenta, para que solo se reanude la sesión UNA vez aunque varias tareas concurran."""
        with self._client_locks_guard:
            lock = self._client_locks.get(account_id)
            if lock is None:
                import threading
                # RLock (reentrante): _chat y _yo también lo toman y por dentro llaman a
                # obtener_cliente, que vuelve a tomarlo; con un Lock normal eso se autobloquearía.
                lock = threading.RLock()
                self._client_locks[account_id] = lock
            return lock

    def sesion_caducada(self, account_id: int) -> bool:
        """True si la sesión de esta cuenta está caducada/revocada y hace falta volver a iniciarla.

        Los sondeos automáticos lo consultan para NO lanzar tareas condenadas a fallar: una cuenta
        caducada generaba varias tareas fallidas por minuto durante horas."""
        return account_id in self._sesiones_revocadas

    def abortar_si_sesion_caducada(self, account_id: int, callback=None) -> bool:
        """Si la sesión está caducada, avisa al llamador por su callback y devuelve True para que
        NO llegue a lanzar la tarea en segundo plano.

        Es el freno de los sondeos automáticos: sin él, cada temporizador (buffers, directos,
        bandeja) creaba su tarea, esta pedía el cliente y moría con el mismo error. El callback se
        llama igual —con el error— para que quien esperaba respuesta cierre su estado
        ('actualizando…') en vez de quedarse colgado."""
        if account_id not in self._sesiones_revocadas:
            return False
        if callback:
            try:
                import wx
                wx.CallAfter(callback, None, self._error_sesion_caducada(account_id))
            except Exception:
                pass
        return True

    def _error_sesion_caducada(self, account_id: int) -> BlueskyServiceError:
        """Error de sesión caducada, ya marcado como diagnosticado para que el registro no se
        llene con la misma traza en cada sondeo (ver SocialRSSError.sin_traza)."""
        nombre = self._nombre_cuenta(account_id)
        err = BlueskyServiceError(
            f"La sesión de Bluesky de {nombre} ha caducado. Vuelve a iniciar sesión en la cuenta.")
        err.sin_traza = True
        return err

    def _nombre_cuenta(self, account_id: int) -> str:
        """Nombre visible de la cuenta (para los avisos), o su id si no se puede leer."""
        try:
            fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
            if fila and fila[0]:
                return str(fila[0])
        except Exception:
            pass
        return f"la cuenta {account_id}"

    def _marcar_sesion_revocada(self, account_id: int):
        """Marca la sesión como caducada y AVISA al usuario UNA sola vez.

        Antes esto era mudo: la cuenta dejaba de traer nada y el único rastro estaba en el
        registro, así que se podía pasar días sin Bluesky sin enterarse de que solo había que
        volver a iniciar sesión."""
        if account_id in self._sesiones_revocadas:
            return
        self._sesiones_revocadas.add(account_id)
        try:
            from app.core.speech import notificar
            notificar(_("La sesión de Bluesky de {0} ha caducado. Vuelve a iniciar sesión en la "
                        "cuenta desde Preferencias para seguir recibiendo publicaciones.")
                      .format(self._nombre_cuenta(account_id)))
        except Exception as e:
            log.debug(f"No se pudo avisar de la sesión Bluesky caducada {account_id}: {e}")

    def obtener_cliente(self, account_id: int) -> Any:
        """Devuelve un Client autenticado para la cuenta (con caché y reanudación de sesión).

        La creación va protegida por un lock POR CUENTA: si varias tareas en segundo plano piden
        el cliente a la vez (arranque, sincronización…), solo UNA crea el Client y reanuda la
        sesión; las demás reutilizan el ya creado. Así se evita la carrera en la que varios Client
        reanudaban con el mismo refresh token y atproto revocaba todos menos uno."""
        cli = self._clients.get(account_id)
        if cli is not None:
            return cli
        if not HAS_ATPROTO:
            raise BlueskyServiceError("La biblioteca atproto no está instalada.")
        # Si la sesión ya se marcó como revocada/caducada, fallar YA sin volver a intentar el login
        # (evita que decenas de tareas en segundo plano martilleen la API y llenen el log con el
        # mismo error). Se limpia al reautenticar la cuenta.
        if account_id in self._sesiones_revocadas:
            raise self._error_sesion_caducada(account_id)
        # Cuenta en cooldown por un 429: fallar rápido con la hora de reintento (9.5).
        self._comprobar_cooldown(account_id)

        with self._lock_de_cuenta(account_id):
            # Doble comprobación: otra tarea pudo crear el cliente mientras esperábamos el lock.
            cli = self._clients.get(account_id)
            if cli is not None:
                return cli
            if account_id in self._sesiones_revocadas:
                raise self._error_sesion_caducada(account_id)

            session_string = security.obtener_credencial(f"bluesky:{account_id}", "session_string")
            pds_url = security.obtener_credencial(f"bluesky:{account_id}", "pds_url") or "https://bsky.social"
            if not session_string:
                raise BlueskyServiceError(f"No hay sesión guardada para la cuenta Bluesky {account_id}.")

            import time as _time
            ultimo_error = None
            # Hasta 3 intentos: un bache de red momentáneo no debe interpretarse como "sesión
            # perdida" (era una causa de falsos "tienes que volver a iniciar sesión"). Los errores
            # de TOKEN (caducado/revocado/inválido) sí son definitivos y no se reintentan. NUNCA se
            # borra la session_string guardada: si de verdad caducó, el usuario reautentica.
            for intento in range(3):
                try:
                    client = Client(base_url=pds_url)
                    # IMPORTANTE: registrar la persistencia ANTES del login. Al reanudar con
                    # session_string, si el token de acceso ya caducó atproto renueva la sesión
                    # durante el propio login y ROTA el refresh token (el anterior queda
                    # invalidado). Si el callback se registrara después, esa rotación no se
                    # guardaría y en el siguiente arranque cargaríamos un refresh token revocado.
                    self._registrar_persistencia_sesion(account_id, client)
                    client.login(session_string=session_string)
                    # Cabecera atproto-accept-labelers como el oficial (B1): Bluesky
                    # Moderation con ";redact" + los suscritos SIN redact (cacheados de
                    # LabelersPref; se rehace al refrescar las preferencias). Antes solo se
                    # enviaba si había suscritos, y TODOS con ";redact".
                    try:
                        self.aplicar_cabecera_labelers_cacheada(account_id, client)
                    except Exception as e:
                        log.debug(f"No se pudo configurar el header de labelers: {e}")
                    self._clients[account_id] = client
                    self._sesiones_revocadas.discard(account_id)  # recuperada
                    # Cinturón y tirantes: si el login renovó la sesión, guardarla ya.
                    try:
                        nueva = client.export_session_string()
                        if nueva and nueva != session_string:
                            security.guardar_credencial(f"bluesky:{account_id}", "session_string", nueva)
                    except Exception:
                        pass
                    return client
                except Exception as e:
                    ultimo_error = e
                    codigo = self._codigo_error(e)
                    # Token caducado/revocado/inválido => definitivo: no reintentar. Se mira el
                    # CÓDIGO de error de atproto, no el str() del Response (que arrastra cabeceras
                    # como 'ratelimit-*' o 'Authorization' y daba falsos positivos).
                    if codigo in ("ExpiredToken", "InvalidToken", "AccountTakedown") or \
                            "LoginRequired" in codigo:
                        # Token muerto: marcar la cuenta como revocada para que las siguientes
                        # llamadas fallen rápido sin volver a golpear la API (hasta reautenticar),
                        # y avisar al usuario, que es quien tiene que volver a iniciar sesión.
                        self._marcar_sesion_revocada(account_id)
                        break
                    if self._es_error_limite(e):
                        # Límite de peticiones: JAMÁS reintentar (la restauración de sesión
                        # reintentaba a 1,5/3 s incluso ante 429; auditoría 9.5). Se registra
                        # el cooldown con la hora del servidor y se falla con ella.
                        self.registrar_limite_429(account_id, e)
                        break
                    if intento < 2:  # error transitorio (red): esperar y reintentar
                        _time.sleep(1.5 * (intento + 1))
            # Si el token está muerto, el fallo ya está diagnosticado y avisado: se sale con el
            # error "sin traza" para que los sondeos siguientes no llenen el registro. Solo los
            # fallos INESPERADOS dejan la traza y la línea de ERROR.
            if account_id in self._sesiones_revocadas:
                raise self._error_sesion_caducada(account_id)
            log.error(f"Error al reanudar sesión Bluesky {account_id}: {ultimo_error}")
            raise BlueskyServiceError(self._mensaje_error(ultimo_error))

    def _chat(self, account_id: int) -> Any:
        """Devuelve (cacheado) un cliente con el proxy de chat de Bluesky para DMs. El proxy es un
        clon del Client que COMPARTE su sesión; se crea una sola vez bajo el lock de la cuenta para
        no abrir un segundo refresco en paralelo."""
        cli = self._chats.get(account_id)
        if cli is not None:
            return cli
        with self._lock_de_cuenta(account_id):
            cli = self._chats.get(account_id)
            if cli is None:
                client = self.obtener_cliente(account_id)
                cli = client.with_bsky_chat_proxy()
                self._chats[account_id] = cli
            return cli

    def _yo(self, account_id: int) -> Dict[str, str]:
        """Devuelve (cacheado) {did, handle} de la cuenta."""
        cached = self._me.get(account_id)
        if cached is not None:
            return cached
        with self._lock_de_cuenta(account_id):
            cached = self._me.get(account_id)
            if cached is None:
                client = self.obtener_cliente(account_id)
                did = getattr(client, "me", None)
                cached = {
                    "did": _g(did, "did", default=security.obtener_credencial(f"bluesky:{account_id}", "did") or ""),
                    "handle": _g(did, "handle", default=security.obtener_credencial(f"bluesky:{account_id}", "handle") or ""),
                }
                self._me[account_id] = cached
            return cached

    def podar_antiguos(self, account_id: int, max_buffer: int = 0, max_notif: int = 0):
        """Mantenimiento (Fase 6): recorta el histórico de Bluesky. Conserva marcadores
        y los buffers curados (bookmarks, likes). Cada límite con valor 0 = SIN LÍMITE:
        - max_buffer > 0: recorta los timelines de alto volumen a esos elementos y borra
          los posts huérfanos (fuera de todo buffer y no marcadores) + su índice FTS.
        - max_notif > 0: recorta las notificaciones a ese número."""
        try:
            if max_buffer and max_buffer > 0:
                for tl in ("home", "mentions", "sent"):
                    db.execute("""
                        DELETE FROM bluesky_timeline_posts
                        WHERE account_id = ? AND timeline = ? AND post_db_id NOT IN (
                            SELECT t.post_db_id FROM bluesky_timeline_posts t
                            JOIN bluesky_posts p ON p.id = t.post_db_id
                            WHERE t.account_id = ? AND t.timeline = ?
                            ORDER BY p.indexed_at DESC LIMIT ?
                        )
                    """, (account_id, tl, account_id, tl, max_buffer))
                cond = ("account_id = ? AND COALESCE(bookmarked, 0) = 0 "
                        "AND id NOT IN (SELECT post_db_id FROM bluesky_timeline_posts WHERE account_id = ?)")
                try:
                    db.execute(f"DELETE FROM bluesky_posts_fts WHERE post_db_id IN (SELECT id FROM bluesky_posts WHERE {cond})",
                               (account_id, account_id))
                except Exception:
                    pass
                db.execute(f"DELETE FROM bluesky_posts WHERE {cond}", (account_id, account_id))
            if max_notif and max_notif > 0:
                db.execute("""
                    DELETE FROM bluesky_notifications
                    WHERE account_id = ? AND id NOT IN (
                        SELECT id FROM bluesky_notifications WHERE account_id = ?
                        ORDER BY indexed_at DESC LIMIT ?
                    )
                """, (account_id, account_id, max_notif))
            # Reparación IDEMPOTENTE de huérfanos FTS (auditoría 13.2): la tabla virtual no
            # tiene claves foráneas, así que cualquier ruta antigua de borrado pudo dejar
            # filas sueltas que la búsqueda seguía encontrando. Se limpia en cada poda.
            try:
                db.execute("DELETE FROM bluesky_posts_fts WHERE post_db_id NOT IN "
                           "(SELECT id FROM bluesky_posts)")
            except Exception:
                pass
        except Exception as e:
            log.debug(f"No se pudo podar el histórico de Bluesky (cuenta {account_id}): {e}")

    def vaciar_cache_sesion(self, account_id: int):
        """Vacía los buffers VOLÁTILES de una cuenta de Bluesky (Inicio, Menciones, feeds, listas,
        búsquedas y notificaciones), CONSERVANDO Me gusta, Marcadores, Enviados y los mensajes
        directos. Para la opción "no conservar publicaciones entre sesiones". No avisa ni bloquea."""
        try:
            db.execute(
                "DELETE FROM bluesky_timeline_posts WHERE account_id = ? AND ("
                "timeline IN ('home','mentions') OR timeline LIKE 'feed:%' "
                "OR timeline LIKE 'list:%' OR timeline LIKE 'search:%')",
                (account_id,))
            db.execute("DELETE FROM bluesky_notifications WHERE account_id = ?", (account_id,))
            # Posts que quedan huérfanos (fuera de TODO buffer conservado) y su índice FTS.
            cond = ("account_id = ? AND id NOT IN "
                    "(SELECT post_db_id FROM bluesky_timeline_posts WHERE account_id = ?)")
            try:
                db.execute(
                    f"DELETE FROM bluesky_posts_fts WHERE post_db_id IN "
                    f"(SELECT id FROM bluesky_posts WHERE {cond})", (account_id, account_id))
            except Exception:
                pass
            db.execute(f"DELETE FROM bluesky_posts WHERE {cond}", (account_id, account_id))
        except Exception as e:
            log.debug(f"No se pudo vaciar la caché de sesión de Bluesky (cuenta {account_id}): {e}")

    def eliminar_cuenta(self, account_id: int):
        """Elimina la cuenta, su caché y sus secretos."""
        try:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            for clave in ("session_string", "pds_url", "handle", "did"):
                security.borrar_credencial(f"bluesky:{account_id}", clave)
            self._clients.pop(account_id, None)
            self._chats.pop(account_id, None)
            self._me.pop(account_id, None)
            self._sesiones_revocadas.discard(account_id)
            log.info(f"Cuenta Bluesky ID {account_id} eliminada.")
        except Exception as e:
            log.error(f"Error al eliminar cuenta Bluesky {account_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # POLÍTICA CENTRAL DE LÍMITES (429) — auditoría Bluesky 9.5
    # ------------------------------------------------------------------
    def _es_error_limite(self, e: Exception) -> bool:
        """True si la excepción es un límite de peticiones: código XRPC RateLimitExceeded
        o estado HTTP 429 (se mira el código y el status, no el str())."""
        if "ratelimit" in self._codigo_error(e).lower():
            return True
        try:
            return getattr(getattr(e, "response", None), "status_code", None) == 429
        except Exception:
            return False

    @staticmethod
    def _espera_de_limite(e: Exception) -> float:
        """Segundos hasta poder reintentar tras un 429, respetando las cabeceras del
        servidor (retry-after en segundos, o ratelimit-reset en tiempo UNIX). Sin
        cabeceras, 5 minutos: la ventana típica de los límites del PDS."""
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
        """Pone la cuenta en COOLDOWN hasta el momento que indique el servidor (tope 1 h).
        Mientras dure, obtener_cliente falla rápido con la hora de reintento, sin que
        decenas de tareas de fondo sigan martilleando la API. Devuelve la marca 'hasta'."""
        import time as _t
        hasta = _t.time() + min(self._espera_de_limite(e), 3600.0)
        self._cooldown_429[account_id] = hasta
        log.warning(f"Bluesky {account_id}: límite de peticiones; en pausa hasta "
                    f"{_t.strftime('%H:%M:%S', _t.localtime(hasta))}")
        return hasta

    def _comprobar_cooldown(self, account_id: int):
        """Si la cuenta está en cooldown por un 429, falla YA con la hora de reintento
        (la interfaz puede seguir mostrando los datos cacheados)."""
        import time as _t
        hasta = self._cooldown_429.get(account_id, 0)
        if hasta <= _t.time():
            self._cooldown_429.pop(account_id, None)
            return
        raise BlueskyServiceError(
            "Bluesky ha limitado temporalmente las peticiones de esta cuenta. Se podrá "
            "reintentar a las {0}; mientras, se muestran los datos ya descargados.".format(
                _t.strftime("%H:%M", _t.localtime(hasta))))

    def _get_con_reintentos(self, account_id: int, fn, intentos: int = 3):
        """Ejecuta una LECTURA idempotente con reintentos: backoff exponencial con
        jitter completo para errores transitorios; los límites (429) registran el
        cooldown y NO se reintentan; los errores de token se propagan tal cual. Las
        ESCRITURAS no deben pasar por aquí (su seguridad es el rkey idempotente y la
        reconciliación de la outbox, auditoría 6.6)."""
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
                    raise BlueskyServiceError(self._mensaje_error(e))
                codigo = self._codigo_error(e)
                if codigo in ("ExpiredToken", "InvalidToken", "AccountTakedown") or \
                        "LoginRequired" in codigo:
                    raise
                if i < intentos - 1:
                    _t.sleep(_rnd.uniform(0, 0.8 * (2 ** i)))
        raise ultimo

    @staticmethod
    def _codigo_error(e: Exception) -> str:
        """Devuelve el código de error LIMPIO de atproto (p. ej. 'ExpiredToken',
        'RateLimitExceeded'), leyéndolo de e.response.content.error. Devuelve '' si no lo hay.

        Es más fiable que str(e): el repr de un Response incluye las cabeceras HTTP (con
        'ratelimit-*' y 'vary: Authorization'), que hacían que un ExpiredToken se confundiera con
        un límite de peticiones o que cualquier error pareciera de 'auth'."""
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
        """Traduce errores comunes de atproto a mensajes claros en español. Se prioriza el código
        de error oficial (e.response.content.error) sobre el texto, que arrastra cabeceras."""
        codigo = self._codigo_error(e)
        cb = codigo.lower()
        # Los fallos de TRANSPORTE de atproto (InvokeTimeoutError, NetworkError) llegan con el
        # texto vacío: se completa con el nombre del tipo para poder clasificarlos y no mostrar
        # un "Error de Bluesky: " sin nada detrás.
        texto = str(e) or type(e).__name__
        bajo = texto.lower()
        # 1) Por código oficial (lo más fiable).
        if codigo:
            if cb in ("expiredtoken", "invalidtoken") or "loginrequired" in cb:
                return "La sesión de Bluesky ha caducado. Vuelve a iniciar sesión en la cuenta."
            if "ratelimit" in cb:  # RateLimitExceeded
                return "Has alcanzado el límite de peticiones de Bluesky. Espera unos minutos e inténtalo de nuevo."
            if cb in ("invalidrequest", "authenticationrequired") and (
                    "password" in bajo or "identifier" in bajo or "credential" in bajo):
                return "Handle o contraseña de aplicación incorrectos. Recuerda usar una contraseña de aplicación, no la de tu cuenta."
        # 2) Respaldo por texto (errores sin código: red, etc.).
        if "invalid" in bajo and ("password" in bajo or "identifier" in bajo or "credential" in bajo):
            return "Handle o contraseña de aplicación incorrectos. Recuerda usar una contraseña de aplicación, no la de tu cuenta."
        if not codigo and ("rate limit" in bajo or "ratelimitexceeded" in bajo):
            return "Has alcanzado el límite de peticiones de Bluesky. Espera unos minutos e inténtalo de nuevo."
        if "could not find" in bajo or "not found" in bajo:
            return "No se ha encontrado el recurso solicitado en Bluesky."
        if "expired" in bajo or "revoked" in bajo:
            return "La sesión de Bluesky ha caducado. Vuelve a iniciar sesión en la cuenta."
        if "network" in bajo or "timeout" in bajo or "connection" in bajo:
            return "Problema de conexión con Bluesky. Comprueba tu red e inténtalo de nuevo."
        return f"Error de Bluesky: {texto}"

    # ------------------------------------------------------------------
    # CARGA DE TIMELINES
    # ------------------------------------------------------------------
