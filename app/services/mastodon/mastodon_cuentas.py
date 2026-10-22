# -*- coding: utf-8 -*-
"""Cuentas, OAuth, clientes, preferencias y ciclo de vida.

Mixin de MastodonService (parte de la refactorización modular).
"""
import logging
from typing import List, Dict, Any, Optional, Tuple, Callable
from mastodon import Mastodon
from app.core.database import db
from app.core.config_manager import config
from app.core.security import security
from app.core.errors import MastodonServiceError
from app.core.task_manager import task_manager


log = logging.getLogger(__name__)

class _CuentasMixin:
    def obtener_preferencias_servidor(self, account_id: int, force: bool = False):
        """Lee del servidor (instancia) las preferencias de publicación: visibilidad por
        defecto y contenido sensible (como read_preferences_from_instance de TWBlue). Cachea
        el resultado por sesión. Hace la llamada de red, así que conviene invocarla en segundo
        plano. Devuelve un dict {visibility, sensitive} o None si la opción está desactivada
        o falla la consulta."""
        if not config.get("mastodon", "read_preferences_from_instance", False):
            return None
        if not force and account_id in self._prefs_cache:
            return self._prefs_cache[account_id]
        try:
            client = self.obtener_cliente(account_id)
            prefs = client.preferences()
            res = {
                "visibility": prefs.get("posting:default:visibility",
                                        config.get("mastodon", "default_visibility", "public")),
                "sensitive": bool(prefs.get("posting:default:sensitive",
                                            config.get("mastodon", "default_sensitive", False))),
            }
            self._prefs_cache[account_id] = res
            # Persistir la visibilidad del servidor para que esté disponible AL INSTANTE en la
            # siguiente sesión (sin esperar a la carga en segundo plano de ~12 s tras arrancar).
            try:
                if res["visibility"] in ("public", "unlisted", "private", "direct"):
                    guardados = dict(config.get("mastodon", "server_visibility", {}) or {})
                    if guardados.get(str(account_id)) != res["visibility"]:
                        guardados[str(account_id)] = res["visibility"]
                        config.set("mastodon", "server_visibility", guardados)
            except Exception:
                pass
            return res
        except Exception as e:
            if self.avisar_si_servidor_sin_api(account_id, e):
                return None
            log.warning(f"No se pudieron leer las preferencias del servidor para la cuenta {account_id}: {e}")
            return None

    def avisar_si_servidor_sin_api(self, account_id: int, error: Exception) -> bool:
        """Si el error es "el servidor contesta, pero no con la API de Mastodon" (devuelve una
        página HTML: un portal de acceso, el panel del NAS, un proxy inverso mal apuntado), AVISA
        al usuario UNA vez por cuenta y sesión y devuelve True.

        Sin esto el fallo solo existía en el registro: la cuenta se quedaba sin traer nada y el
        usuario no tenía forma de saber que el problema estaba en SU servidor, no en la app. Se
        sigue reintentando en cada sondeo a propósito: en cuanto el servidor vuelva, la cuenta
        funciona sola sin tocar nada."""
        texto = str(error or "").lower()
        if "could not parse response as json" not in texto and "<!doctype html" not in texto:
            return False
        if not hasattr(self, "_avisadas_sin_api"):
            self._avisadas_sin_api = set()
        if account_id in self._avisadas_sin_api:
            return True
        self._avisadas_sin_api.add(account_id)
        try:
            fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
            nombre = fila[0] if fila and fila[0] else f"la cuenta {account_id}"
        except Exception:
            nombre = f"la cuenta {account_id}"
        try:
            from app.core.speech import notificar
            notificar(_("El servidor de {0} responde con una página web en lugar de con la API de "
                        "Mastodon. Comprueba que la instancia esté funcionando y que la dirección "
                        "de la cuenta sea la correcta; mientras tanto esa cuenta no traerá nada.")
                      .format(nombre))
        except Exception as e:
            log.debug(f"No se pudo avisar del servidor sin API de la cuenta {account_id}: {e}")
        log.warning(f"La instancia de {nombre} no responde la API de Mastodon (devuelve HTML). "
                    f"Se seguirá reintentando en cada sondeo.")
        return True

    def obtener_limite_caracteres(self, account_id: int) -> int:
        """Devuelve (SIN red) el límite de caracteres de la instancia para esa cuenta: lo que
        haya en caché de memoria, o lo persistido en config, o 500 por defecto. Para usar al
        construir el compositor sin bloquear."""
        if account_id in self._char_limit_cache:
            return self._char_limit_cache[account_id]
        guardados = config.get("mastodon", "char_limits", {}) or {}
        val = guardados.get(str(account_id))
        try:
            val = int(val)
        except (TypeError, ValueError):
            val = 0
        return val if val and val > 0 else 500

    def obtener_limite_descripcion(self, account_id: int) -> int:
        """Devuelve (SIN red) el límite de caracteres de la DESCRIPCIÓN (texto alternativo) de los
        adjuntos para esa cuenta, según lo que marque el servidor (configuration.media_attachments.
        description_limit). Toma el valor de la caché de memoria, o el persistido, o 1500 por
        defecto (el valor clásico de Mastodon; los servidores modernos suelen permitir 10000 y se
        actualiza en segundo plano al abrir el compositor)."""
        if account_id in self._desc_limit_cache:
            return self._desc_limit_cache[account_id]
        guardados = config.get("mastodon", "desc_limits", {}) or {}
        val = guardados.get(str(account_id))
        try:
            val = int(val)
        except (TypeError, ValueError):
            val = 0
        return val if val and val > 0 else 1500

    # ------------------------------------------------------------------
    # CAPACIDADES de la instancia (auditoría A10/M8): además del límite de
    # caracteres, se cachean y persisten la versión del servidor y los límites
    # reales de adjuntos y encuestas, para no dar por sentados los valores
    # típicos (4 adjuntos, encuesta 4x50, citas nativas...).
    # ------------------------------------------------------------------

    @staticmethod
    def _version_tupla(version: str):
        """'4.5.1+glitch' -> (4, 5, 1). Tolerante a sufijos; () si no hay números."""
        import re as _re
        numeros = _re.findall(r"\d+", str(version or "").split("+")[0])
        return tuple(int(n) for n in numeros[:3])

    def capacidades(self, account_id: int) -> dict:
        """Devuelve (SIN red) las capacidades cacheadas de la instancia de esa cuenta:
        {} si aún no se han descargado. Se rellenan en actualizar_limite_caracteres_async
        (que corre al abrir el compositor y en el arranque)."""
        if not hasattr(self, "_caps_cache"):
            self._caps_cache = {}
        caps = self._caps_cache.get(account_id)
        if caps:
            return caps
        guardadas = config.get("mastodon", "capacidades", {}) or {}
        caps = guardadas.get(str(account_id)) or {}
        if caps:
            self._caps_cache[account_id] = caps
        return caps

    def soporta_citas_nativas(self, account_id: int):
        """True/False si se conoce la versión de la instancia (citas nativas = Mastodon
        4.5+); None si aún no se ha podido determinar (sin capacidades cacheadas)."""
        caps = self.capacidades(account_id)
        version = caps.get("version")
        if not version:
            return None
        return self._version_tupla(version) >= (4, 5)

    def actualizar_limite_caracteres_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lee de la instancia su límite real de caracteres (configuration.statuses.
        max_characters, o el antiguo max_toot_chars) y, de paso, el límite de la descripción de
        adjuntos (configuration.media_attachments.description_limit). Cachea/persiste ambos. El
        callback recibe el límite de caracteres (int). Hace red, así que va en segundo plano."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            info = client.instance() or {}
            conf = info.get("configuration") or {}
            limite = 0
            try:
                statuses = conf.get("statuses") or {}
                limite = int(statuses.get("max_characters") or 0)
            except Exception:
                limite = 0
            if not limite:
                # Mastodon antiguo / Pleroma / Akkoma: campo max_toot_chars en la raíz.
                try:
                    limite = int(info.get("max_toot_chars") or 0)
                except Exception:
                    limite = 0
            if not limite or limite <= 0:
                limite = 500
            self._char_limit_cache[account_id] = limite
            # Límite de la descripción (texto alternativo) de los adjuntos.
            desc = 0
            try:
                media = conf.get("media_attachments") or {}
                desc = int(media.get("description_limit") or 0)
            except Exception:
                desc = 0
            if desc and desc > 0:
                self._desc_limit_cache[account_id] = desc
            # CAPACIDADES completas de la instancia (auditoría A10): versión, límites de
            # adjuntos y encuestas y traducción, para validar antes de subir/publicar.
            try:
                statuses = conf.get("statuses") or {}
                media_cfg = conf.get("media_attachments") or {}
                polls = conf.get("polls") or {}
                caps = {
                    "version": str(info.get("version") or ""),
                    "max_characters": limite,
                    "description_limit": desc or 1500,
                    "max_media_attachments": int(statuses.get("max_media_attachments") or 4),
                    "characters_reserved_per_url": int(statuses.get("characters_reserved_per_url") or 23),
                    "supported_mime_types": list(media_cfg.get("supported_mime_types") or []),
                    "poll_max_options": int(polls.get("max_options") or 4),
                    "poll_max_chars_por_opcion": int(polls.get("max_characters_per_option") or 50),
                    "poll_min_expiracion": int(polls.get("min_expiration") or 300),
                    "poll_max_expiracion": int(polls.get("max_expiration") or 2629746),
                    "traduccion": bool((conf.get("translation") or {}).get("enabled")),
                    # 4.6+: el máximo de campos del perfil es configurable por el servidor
                    # (antes siempre 4). Sin esto, guardar el perfil recortaba a 4 y se perdían
                    # los demás.
                    "max_profile_fields": int((conf.get("accounts") or {}).get("max_profile_fields") or 4),
                    "max_featured_tags": int((conf.get("accounts") or {}).get("max_featured_tags") or 10),
                    "max_pinned_statuses": int((conf.get("accounts") or {}).get("max_pinned_statuses") or 5),
                    # Versión de la API (4.3+): más fiable que interpretar el texto de versión,
                    # sobre todo en variantes de Mastodon.
                    "api_version": int((info.get("api_versions") or {}).get("mastodon") or 0),
                    # 4.5+: qué cronologías públicas deja ver la instancia ("public",
                    # "authenticated" o "disabled"), para no ofrecer buffers que no cargan.
                    "timelines_access": dict(conf.get("timelines_access") or {}),
                }
                if not hasattr(self, "_caps_cache"):
                    self._caps_cache = {}
                self._caps_cache[account_id] = caps
                guardadas = dict(config.get("mastodon", "capacidades", {}) or {})
                if guardadas.get(str(account_id)) != caps:
                    guardadas[str(account_id)] = caps
                    config.set("mastodon", "capacidades", guardadas)
            except Exception as e:
                log.debug(f"No se pudieron cachear las capacidades de la instancia: {e}")
            # Persistir para que el compositor lo muestre al instante la próxima vez.
            try:
                guardados = dict(config.get("mastodon", "char_limits", {}) or {})
                if guardados.get(str(account_id)) != limite:
                    guardados[str(account_id)] = limite
                    config.set("mastodon", "char_limits", guardados)
                if desc and desc > 0:
                    guardados_d = dict(config.get("mastodon", "desc_limits", {}) or {})
                    if guardados_d.get(str(account_id)) != desc:
                        guardados_d[str(account_id)] = desc
                        config.set("mastodon", "desc_limits", guardados_d)
            except Exception:
                pass
            return limite
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def visibilidad_predeterminada(self, account_id: int) -> str:
        """Visibilidad por defecto a usar al componer: la del servidor si la opción está
        activada y ya está en caché; si no, la configurada localmente. Nunca bloquea."""
        if config.get("mastodon", "read_preferences_from_instance", False):
            cache = self._prefs_cache.get(account_id)
            if cache and cache.get("visibility"):
                return cache["visibility"]
            # Caché de sesión vacía (aún no se ha consultado al servidor esta sesión): usar la
            # última visibilidad del servidor PERSISTIDA, para que la opción funcione al instante
            # sin esperar a la carga en segundo plano del arranque.
            persistida = (config.get("mastodon", "server_visibility", {}) or {}).get(str(account_id))
            if persistida in ("public", "unlisted", "private", "direct"):
                return persistida
        return config.get("mastodon", "default_visibility", "public")

    def _obtener_me_id(self, account_id: int, client) -> int:
        """Devuelve (cacheado) el id de usuario propio de la cuenta."""
        if account_id not in self._me_ids:
            me = client.account_verify_credentials()
            self._me_ids[account_id] = me["id"]
        return self._me_ids[account_id]

    def _limpiar_url(self, instance_url: str) -> str:
        """Asegura el protocolo HTTPS y limpia espacios de la URL de la instancia."""
        url = instance_url.strip()
        if not url.startswith("http"):
            url = "https://" + url
        return url.rstrip("/")

    def registrar_aplicacion(self, instance_url: str, scopes: List[str] = ["read", "write", "follow"]) -> Tuple[str, str, str]:
        """
        Registra la aplicación en la instancia y devuelve (client_id, client_secret, auth_url).
        """
        base_url = self._limpiar_url(instance_url)
        try:
            log.info(f"Registrando aplicación en la instancia: {base_url}")
            client_id, client_secret = Mastodon.create_app(
                client_name="SocialRSS Suite",
                scopes=scopes,
                api_base_url=base_url
            )
            
            # Instanciar cliente temporal para obtener la URL de login
            client = Mastodon(client_id=client_id, client_secret=client_secret, api_base_url=base_url)
            auth_url = client.auth_request_url(scopes=scopes)
            
            return client_id, client_secret, auth_url
        except Exception as e:
            log.error(f"Error al registrar la aplicación en {base_url}: {e}")
            raise MastodonServiceError(f"No se pudo conectar con la instancia {instance_url}: {e}")

    def completar_inicio_sesion(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        auth_code: str,
        scopes: List[str] = ["read", "write", "follow"]
    ) -> Dict[str, Any]:
        """
        Intercambia el código de autorización por el token y guarda la cuenta.
        Devuelve el perfil del usuario autenticado.
        """
        base_url = self._limpiar_url(instance_url)
        try:
            client = Mastodon(
                client_id=client_id,
                client_secret=client_secret,
                api_base_url=base_url
            )
            
            # Obtener el token de acceso
            access_token = client.log_in(code=auth_code, scopes=scopes)
            
            # Verificar credenciales y obtener información del perfil
            profile = client.account_verify_credentials()
            username_completo = f"@{profile['username']}@{base_url.replace('https://', '')}"
            
            # 1. Guardar la cuenta en base de datos (id de forma atómica). La posición se
            #    asigna al FINAL (max+1) para que una cuenta nueva (o re-añadida) no salte al
            #    principio y altere el orden que el usuario haya dejado en el gestor/árbol.
            row_id = db.execute_insert(
                "INSERT INTO accounts (name, service_id, active, position) "
                "VALUES (?, 'mastodon', 1, (SELECT COALESCE(MAX(position), -1) + 1 FROM accounts))",
                (username_completo,)
            )
            
            # 2. Guardar credenciales de forma segura
            security.guardar_credencial(str(row_id), "access_token", access_token)
            security.guardar_credencial(str(row_id), "instance_url", base_url)
            
            log.info(f"Cuenta de Mastodon registrada con éxito: {username_completo} (ID: {row_id})")
            return {
                "account_id": row_id,
                "username": username_completo,
                "profile": profile
            }
        except Exception as e:
            log.error(f"Error al completar login de Mastodon: {e}")
            raise MastodonServiceError(f"Código de autenticación inválido o error de servidor: {e}")

    def _api(self, client, nombre: str):
        """Devuelve el método `nombre` del cliente Mastodon, o lanza un error LEGIBLE si la versión
        instalada de Mastodon.py (o la instancia) no lo admite. Evita un AttributeError críptico para
        funciones recientes (citas, informe anual, timeline de enlace, etc.)."""
        fn = getattr(client, nombre, None)
        if fn is None:
            raise MastodonServiceError(
                "Esta función («{0}») necesita una versión más reciente de Mastodon.py (o tu "
                "instancia no la admite todavía). Actualiza Mastodon.py para usarla.".format(nombre))
        return fn

    @staticmethod
    def _peticion_directa(client, metodo: str, ruta: str, parametros=None, use_json: bool = False):
        """Llama a un endpoint de la API que Mastodon.py 2.2.x aún NO envuelve (colecciones,
        /api/v1/profile, borrar conversación...). Usa el propio requestor de la librería, así
        que hereda la autenticación, el proxy, los reintentos y el tratamiento de errores del
        cliente. Concentrado aquí para que una actualización de la librería solo toque un punto."""
        request = getattr(client, "_Mastodon__api_request", None)
        if request is None:
            raise MastodonServiceError(
                "La versión instalada de Mastodon.py no permite esta operación. Actualízala.")
        return request(metodo, ruta, parametros or {}, use_json=use_json)

    def version_api(self, account_id: int) -> int:
        """Versión de la API de Mastodon de la instancia (api_versions.mastodon, 4.3+), o 0 si
        no se conoce todavía. Sirve para ofrecer solo lo que el servidor admite."""
        try:
            return int((self.capacidades(account_id) or {}).get("api_version") or 0)
        except Exception:
            return 0

    def obtener_cliente(self, account_id: int) -> Mastodon:
        """Devuelve un cliente Mastodon inicializado para la cuenta especificada (con caché)."""
        if account_id in self._clients:
            return self._clients[account_id]
            
        # Cargar credenciales
        token = security.obtener_credencial(str(account_id), "access_token")
        url = security.obtener_credencial(str(account_id), "instance_url")
        
        if not token or not url:
            raise MastodonServiceError(f"No se encontraron credenciales seguras para la cuenta ID {account_id}")
            
        try:
            client = Mastodon(
                access_token=token,
                api_base_url=url,
                request_timeout=25,
                # ratelimit_method="throw": ante un límite de tasa (429), la librería LANZA el error
                # en vez de esperar y REINTENTAR sola. El reintento automático (el valor por defecto
                # "wait") era el único mecanismo del cliente capaz de REENVIAR una publicación: en
                # instancias que no respetan la clave de idempotencia (o detrás de un proxy que
                # reparte la petición), ese reenvío creaba un toot DUPLICADO. Además, "wait" podía
                # bloquear un hilo del pool hasta un minuto esperando el reinicio del límite; con
                # "throw" el error se registra y el siguiente sondeo reintenta la LECTURA, y una
                # publicación limitada falla limpiamente (se avisa) sin duplicar.
                ratelimit_method="throw",
            )
            self._clients[account_id] = client
            return client
        except Exception as e:
            log.error(f"Error al instanciar cliente Mastodon para ID {account_id}: {e}")
            raise MastodonServiceError(f"No se pudo conectar a la cuenta: {e}")

    def podar_antiguos(self, account_id: int, max_buffer: int = 0, max_notif: int = 0):
        """Mantenimiento (Fase 6): recorta el histórico para que las tablas no crezcan
        sin fin. Conserva favoritos y marcadores y los buffers curados (bookmarks,
        favourites, sent). Cada límite con valor 0 significa SIN LÍMITE (no se poda eso):
        - max_buffer > 0: recorta los timelines de alto volumen a esos elementos y borra
          los toots huérfanos (fuera de todo buffer y no guardados) + su índice FTS.
        - max_notif > 0: recorta las notificaciones a ese número."""
        try:
            if max_buffer and max_buffer > 0:
                for tl in ("home", "local", "public", "direct"):
                    db.execute("""
                        DELETE FROM mastodon_timeline_statuses
                        WHERE account_id = ? AND timeline = ? AND status_db_id NOT IN (
                            SELECT t.status_db_id FROM mastodon_timeline_statuses t
                            JOIN mastodon_statuses s ON s.id = t.status_db_id
                            WHERE t.account_id = ? AND t.timeline = ?
                            ORDER BY s.created_at DESC LIMIT ?
                        )
                    """, (account_id, tl, account_id, tl, max_buffer))
                cond = ("account_id = ? AND favourited = 0 AND bookmarked = 0 "
                        "AND id NOT IN (SELECT status_db_id FROM mastodon_timeline_statuses WHERE account_id = ?)")
                try:
                    db.execute(f"DELETE FROM mastodon_statuses_fts WHERE status_db_id IN (SELECT id FROM mastodon_statuses WHERE {cond})",
                               (account_id, account_id))
                except Exception:
                    pass
                db.execute(f"DELETE FROM mastodon_statuses WHERE {cond}", (account_id, account_id))
            if max_notif and max_notif > 0:
                db.execute("""
                    DELETE FROM mastodon_notifications
                    WHERE account_id = ? AND id NOT IN (
                        SELECT id FROM mastodon_notifications WHERE account_id = ?
                        ORDER BY created_at DESC LIMIT ?
                    )
                """, (account_id, account_id, max_notif))
        except Exception as e:
            log.debug(f"No se pudo podar el histórico de Mastodon (cuenta {account_id}): {e}")

    def vaciar_cache_sesion(self, account_id: int):
        """Vacía los buffers VOLÁTILES de una cuenta (Inicio, Local, Federada, Directos y las
        notificaciones/menciones), CONSERVANDO favoritos, marcadores y enviados. Para la opción
        "no conservar publicaciones entre sesiones". No avisa ni bloquea."""
        try:
            for tl in ("home", "local", "public", "direct"):
                db.execute("DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = ?",
                           (account_id, tl))
            # Notificaciones y menciones comparten esta tabla.
            db.execute("DELETE FROM mastodon_notifications WHERE account_id = ?", (account_id,))
            # Borrar los toots que quedan huérfanos (fuera de todo buffer y no guardados) y su FTS.
            cond = ("account_id = ? AND favourited = 0 AND bookmarked = 0 "
                    "AND id NOT IN (SELECT status_db_id FROM mastodon_timeline_statuses WHERE account_id = ?)")
            try:
                db.execute(f"DELETE FROM mastodon_statuses_fts WHERE status_db_id IN (SELECT id FROM mastodon_statuses WHERE {cond})",
                           (account_id, account_id))
            except Exception:
                pass
            db.execute(f"DELETE FROM mastodon_statuses WHERE {cond}", (account_id, account_id))
        except Exception as e:
            log.debug(f"No se pudo vaciar la caché de sesión de Mastodon (cuenta {account_id}): {e}")

    def eliminar_cuenta(self, account_id: int):
        """Elimina una cuenta, su caché de base de datos y sus secretos."""
        try:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            security.borrar_credencial(str(account_id), "access_token")
            security.borrar_credencial(str(account_id), "instance_url")
            if account_id in self._clients:
                del self._clients[account_id]
            log.info(f"Cuenta ID {account_id} eliminada con éxito.")
        except Exception as e:
            log.error(f"Error al eliminar la cuenta ID {account_id}: {e}")
            raise

    # --- CARGA Y CACHÉ DE TIMELINES ---
