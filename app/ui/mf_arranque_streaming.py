# -*- coding: utf-8 -*-
"""MainFrame (mixin): arranque_streaming.
Metodos: _intervalo_auto_segundos, _reiniciar_auto_timer, _aplicar_servicio_activo, _timelines_para_streaming, _sondear_streamables_no_activos, _sincronizar_timeline_streaming, _gestionar_streaming_mastodon, _on_evento_streaming ...
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
# -*- coding: utf-8 -*-
"""
Ventana principal de la aplicación SocialRSS Accessible Suite.
Layout dividido en navegación vertical de servicios, lista de toots/artículos y panel de lectura.
Todos los procesos de sincronización y red ocurren de forma asíncrona mediante el task_manager.
"""

import logging
import wx

from app.core.config_manager import config
from app.core.database import db

from app.services.mastodon.mastodon_service import mastodon_service

from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl

log = logging.getLogger(__name__)

# Tope de HASHTAGS QUE SIGUES a streamear a la vez (cada uno es una conexión SSE). Los que sobren
# siguen llegando por el sondeo de respaldo del Inicio. Comparte presupuesto con listas/búsquedas.
_MAX_HASHTAGS_SEGUIDOS_STREAM = 4

# Ventana de COALESCENCIA del tiempo real (ms). Es de BORDE DE ENTRADA: si hace más de esta ventana
# que no se repinta ese (cuenta, timeline), la novedad se muestra AL INSTANTE (como TWBlue); si están
# llegando en ráfaga, se juntan y se repinta una vez por ventana (para no inundar la interfaz ni al
# lector). Antes era un retardo FIJO de 1,5 s en CADA evento, y por eso las novedades entraban tarde.
_VENTANA_COALESCENCIA_MS = 1000


class _Mf_arranque_streaming:
    def _intervalo_auto_segundos(self):
        """Devuelve los segundos entre sondeos del SERVICIO ACTIVO, o None si no hay cuenta activa,
        el servicio está APAGADO en Preferencias, o la actualización automática global está
        desactivada. En Mastodon con TIEMPO REAL el temporizador sigue corriendo lento, pero solo
        para refrescar los 'hace X' (los toots nuevos llegan por el streaming, no por sondeo)."""
        from app.core import feature_flags
        # Interruptor maestro de General: si está apagado, NINGÚN servicio se actualiza solo.
        if not config.get("general", "auto_update_enabled", True):
            return None
        # Bandeja de entrada activa: se sondea sola (sus fuentes) para ir añadiendo lo nuevo.
        if getattr(self, "_modo_lista", "") == "bandeja":
            return max(30, int(config.get("inbox", "update_interval", 60)))
        if self.active_mastodon_account_id and feature_flags.servicio_habilitado("mastodon"):
            if config.get("mastodon", "realtime", False):
                return 30  # solo refresco de tiempos; las novedades llegan por streaming
            return max(10, int(config.get("mastodon", "update_interval", 60)))
        if self.active_bluesky_account_id and feature_flags.servicio_habilitado("bluesky"):
            return max(15, int(config.get("bluesky", "update_interval", 60)))
        if self.active_telegram_account_id and feature_flags.servicio_habilitado("telegram"):
            return max(10, int(config.get("telegram", "update_interval", 60)))
        if self.active_x_account_id and feature_flags.servicio_habilitado("x"):
            # X limita con dureza: nunca por debajo de min_update_seconds.
            piso = int(config.get("x", "min_update_seconds", 120))
            return max(piso, int(config.get("x", "update_interval", piso)))
        if self.active_rss_account_id and feature_flags.servicio_habilitado("rss"):
            # RSS está en MINUTOS (descargar feeds es caro); lo pasamos a segundos.
            return max(60, int(config.get("rss", "update_interval", 30)) * 60)
        if getattr(self, "active_reddit_account_id", None) and feature_flags.servicio_habilitado("reddit"):
            # Reddit limita a 100 peticiones/min por client_id: intervalo holgado (mínimo 30 s).
            return max(30, int(config.get("reddit", "update_interval", 120)))
        if getattr(self, "active_peertube_account_id", None) and feature_flags.servicio_habilitado("peertube"):
            # PeerTube no tiene push: se sondea el catálogo activo por intervalo (mínimo 30 s).
            return max(30, int(config.get("peertube", "update_interval", 180)))
        return None

    def _reiniciar_auto_timer(self):
        """Arranca o detiene el temporizador según el SERVICIO ACTIVO y su propio intervalo.
        Se llama al cambiar de buffer/cuenta y al guardar preferencias."""
        try:
            if self._auto_timer.IsRunning():
                self._auto_timer.Stop()
            if not config.get("general", "auto_update_enabled", True):
                return
            seg = self._intervalo_auto_segundos()
            if seg:
                self._auto_timer.Start(seg * 1000)
        except Exception as e:
            log.debug(f"Error al reiniciar el temporizador de actualización: {e}")

    def _aplicar_servicio_activo(self):
        """Tras cambiar de buffer/cuenta o guardar preferencias: reinicia el temporizador con el
        intervalo del servicio activo y arranca/para el streaming de Mastodon según corresponda.

        El temporizador se reajusta YA (es barato). La gestión del STREAMING se hace con un pequeño
        retardo (debounce): al navegar deprisa por el árbol —o al colapsar el nodo de Mastodon y
        caer en su raíz, que deja sin cuenta activa— NO se debe arrancar y parar el streaming en
        cada item (abrir/cerrar 4 conexiones SSE por pulsación bloqueaba e iba a trompicones). Se
        espera a que te asientes y se evalúa una sola vez."""
        self._reiniciar_auto_timer()
        try:
            t = getattr(self, "_debounce_streaming", None)
            if t is not None and t.IsRunning():
                t.Stop()
            self._debounce_streaming = wx.CallLater(500, self._gestionar_streaming_y_realtime)
        except Exception:
            # Si algo falla con el temporizador, gestionarlo directamente (mejor eso que nada).
            self._gestionar_streaming_y_realtime()

    def _gestionar_streaming_y_realtime(self):
        """Gestiona el tiempo real de Mastodon y de Bluesky a la vez.

        REGLA: el tiempo real NO depende de la red que estés mirando. Si lo tienes activado, sigue
        funcionando en segundo plano aunque estés en otra red, igual que en TWBlue; así las
        novedades entran conforme llegan y ya están ahí al volver. Antes, el de Bluesky se apagaba
        en cuanto salías de Bluesky y solo volvía al entrar, con el retraso que eso supone."""
        self._gestionar_streaming_mastodon()
        try:
            ctrl = getattr(self, "bluesky_ctrl", None)
            if ctrl:
                from app.core import feature_flags
                activar = (feature_flags.servicio_habilitado("bluesky")
                           and config.get("general", "auto_update_enabled", True)
                           and config.get("bluesky", "realtime", False)
                           and bool(self._hay_cuentas_bluesky()))
                if activar:
                    ctrl.activar_tiempo_real()
                else:
                    ctrl.desactivar_tiempo_real()
        except Exception as e:
            log.debug(f"No se pudo gestionar el tiempo real de Bluesky: {e}")

    @staticmethod
    def _hay_cuentas_bluesky() -> bool:
        """True si hay alguna cuenta de Bluesky dada de alta (para no arrancar su tiempo real
        cuando no hay a quién escuchar)."""
        try:
            fila = db.query_one(
                "SELECT COUNT(*) FROM accounts WHERE service_id = 'bluesky' AND active = 1")
            return bool(fila and fila[0])
        except Exception:
            return False

    # Buffers de BAJO TRÁFICO que se streamean SIEMPRE (mientras estén visibles).
    _TIPOS_STREAMABLES = ("home", "notifications", "mentions", "direct")

    def _refrescar_hashtags_seguidos_stream(self, acc):
        """Refresca (como mucho cada 5 min) la lista CACHEADA de hashtags que sigues en esa cuenta;
        si cambió, reinicia su streaming para que se streameen los nuevos. Va en segundo plano."""
        import time as _t
        cache = getattr(self, "_ht_seguidos_cache", None)
        if cache is None:
            cache = self._ht_seguidos_cache = {}
        ultimos = getattr(self, "_ht_seguidos_ultimo", None)
        if ultimos is None:
            ultimos = self._ht_seguidos_ultimo = {}
        if _t.monotonic() - ultimos.get(acc, 0) < 300:
            return
        ultimos[acc] = _t.monotonic()

        def _cb(tags, err):
            if err or tags is None:
                return
            nuevos = [t.get("name", "").lower() for t in tags if t.get("name")]
            if cache.get(acc) != nuevos:
                cache[acc] = nuevos
                self._gestionar_streaming_mastodon()   # re-streamear con los hashtags al día
        from app.services.mastodon.mastodon_service import mastodon_service
        mastodon_service.obtener_hashtags_seguidos_async(acc, callback=_cb)

    def _timelines_para_streaming(self, acc=None):
        """En TIEMPO REAL se streamean SOLO los buffers de BAJO TRÁFICO visibles: Inicio,
        Notificaciones y Menciones (los tres en una sola conexión 'user') y Directos. Esos son los
        que de verdad quieres al instante y apenas generan tráfico.

        La línea LOCAL y la FEDERADA (y las listas y los hashtags) NO se streamean: son un
        'firehose' (un evento por CADA publicación pública). Streamearlas —aunque fuera solo al
        mirarlas— provocaba que, al entrar en ellas, se intentara traer de golpe todo el atasco y
        la lista se llenara a trompicones BLOQUEANDO la interfaz (el lector se quedaba mudo). Igual
        que TWBlue, esos buffers se refrescan con un sondeo de fondo ACOTADO (una página) cada
        pocos minutos y al pulsar F5; nunca con una avalancha. Cronologías (user:) e hilos
        (thread:) tampoco se streamean. NO depende de la red ACTIVA: el streaming sigue en segundo
        plano aunque estés en otra red, para que los toots/menciones/DM de Mastodon entren conforme
        llegan (los buffers son de bajo tráfico, así que el coste es mínimo)."""
        ocultos = set(config.get("mastodon", "hidden_buffers", []) or [])
        orden = config.get("mastodon", "buffer_order", [])
        streamables = [b for b in self._TIPOS_STREAMABLES if b in orden and b not in ocultos]
        # Si la config no trae buffer_order (instalación nueva), usar el conjunto estándar.
        streamables = streamables or list(self._TIPOS_STREAMABLES)
        # HASHTAGS QUE SIGUES: el servidor los inyecta en tu Inicio, pero el stream de Inicio solo
        # salta cuando publica gente que sigues; si tu Inicio está tranquilo, un toot de un hashtag
        # seguido esperaría al sondeo de respaldo (podían tardar). Para que lleguen AL INSTANTE (como
        # TWBlue), se streamea cada hashtag seguido (clave 'htfollow:<tag>') y su toot entra en Inicio.
        if acc is not None:
            cache = getattr(self, "_ht_seguidos_cache", {}) or {}
            for tag in (cache.get(acc) or [])[:_MAX_HASHTAGS_SEGUIDOS_STREAM]:
                streamables.append("htfollow:" + tag)
        return streamables

    def _sondear_streamables_no_activos(self):
        """Modo intervalo: mantiene al día en segundo plano los buffers streamables ESTÁTICOS
        visibles que NO son el activo (el activo ya lo refresca _actualizar_mastodon_incremental).
        Las listas y los hashtags en modo intervalo se actualizan al entrar/F5 (en tiempo real sí
        se streamean), para no disparar demasiadas llamadas por tick. Las cronologías de usuario
        (user:) sí se sondean aquí, pero POR ROTACIÓN (ver el final del método)."""
        acc = self.active_mastodon_account_id
        if not acc:
            return
        activo = self.active_timeline_type
        # En modo INTERVALO solo se sondean en segundo plano los buffers de BAJO TRÁFICO
        # (Inicio/Notificaciones/Menciones/Directos): la Local, la Federada, las listas y los
        # hashtags se actualizan al entrar o con F5 (sondearlas cada tick derrocharía peticiones).
        # En tiempo real SÍ se streamean todas (ver _timelines_para_streaming).
        ocultos = set(config.get("mastodon", "hidden_buffers", []) or [])
        orden = config.get("mastodon", "buffer_order", [])
        # OJO: NO se incluye "mentions" aparte. Menciones y Notificaciones comparten el MISMO
        # endpoint del servidor (client.notifications()): sondear ambas duplicaba las llamadas a
        # notificaciones en cada tick (se veía en el log: home, notifications, notifications,
        # direct). Con sondear "notifications" basta para mantener al día también las menciones.
        # "sent" también entra: es de bajo tráfico (tus propias publicaciones) y sin él,
        # lo publicado desde otro dispositivo no aparecía en Enviados hasta abrirlo con F5
        # (reporte de usuarios: "el último enviado es de hace 14 minutos con intervalo de 10").
        bajo_trafico = [b for b in ("home", "notifications", "direct", "sent")
                        if b in orden and b not in ocultos]
        for k in bajo_trafico:
            # Si el activo es Notificaciones o Menciones, su propio sondeo ya cubre el endpoint de
            # notificaciones; no lo repitas aquí.
            if k == activo or (k == "notifications" and activo in ("notifications", "mentions")):
                continue
            try:
                self._sincronizar_timeline_streaming(acc, k, lambda s, e: None)
            except Exception:
                pass

        # Local y Federada (firehose): se mantienen con algo de contenido en segundo plano, pero
        # ACOTADO (una sola página: no son rellenables) y CON POCA FRECUENCIA (cada ~2 min), para
        # que al entrar en ellas no estén vacías SIN disparar un torrente de peticiones ni de
        # escrituras. Así no hay que esperar a que carguen al abrirlas tras un buen rato.
        import time as _t
        ahora = _t.time()
        ultimos = getattr(self, "_ultimo_sondeo_firehose", None)
        if ultimos is None:
            ultimos = self._ultimo_sondeo_firehose = {}
        for k in ("local", "public"):
            if k not in orden or k in ocultos or k == activo:
                continue
            if ahora - ultimos.get(k, 0) < 110:   # ~2 min entre sondeos de cada firehose
                continue
            ultimos[k] = ahora
            try:
                self._sincronizar_timeline_streaming(acc, k, lambda s, e: None)
            except Exception:
                pass

        # CRONOLOGÍAS DE USUARIO (buffers 'user:' añadidos con "Añadir cronología de usuario"):
        # Mastodon no ofrece streaming por cuenta, así que se mantienen al día sondeándolas. Para no
        # disparar una petición por cronología en cada tick (puede haber muchas), se sondean por
        # ROTACIÓN: en cada tick solo la que lleva más tiempo sin refrescarse, y como mucho una vez
        # cada ~2 min cada una. La que estés mirando ya la cubre _actualizar_mastodon_incremental.
        try:
            pendientes = ["user:{0}".format(u.get("acct"))
                          for u in (config.get("mastodon", "user_timelines", []) or [])
                          if u.get("account_id") == acc and u.get("acct")]
        except Exception:
            pendientes = []
        pendientes = [k for k in pendientes if k != activo and ahora - ultimos.get(k, 0) >= 110]
        if pendientes:
            k = min(pendientes, key=lambda x: ultimos.get(x, 0))
            ultimos[k] = ahora
            try:
                self._sincronizar_timeline_streaming(acc, k, lambda s, e: None)
            except Exception:
                pass

    def _on_mastodon_bg_timer(self, event):
        """Tick del temporizador dedicado (cada ~2 min): mantiene al día las cuentas de Mastodon
        que no estás usando, sea cual sea el servicio activo. Es ligero y _sondear_otras_cuentas_
        mastodon ya descarta si Mastodon está apagado, la auto-actualización desactivada o no hay
        más de una cuenta."""
        try:
            self._sondear_otras_cuentas_mastodon()
        except Exception as e:
            log.debug(f"Sondeo en segundo plano de otras cuentas de Mastodon: {e}")

    def _on_bg_sync_timer(self, event):
        """Tick (cada ~1 min) que sincroniza en segundo plano las redes que NO son la activa."""
        try:
            self._sondeo_fondo_servicios()
        except Exception as e:
            log.debug(f"Sondeo en segundo plano de servicios: {e}")

    def _sondeo_fondo_servicios(self):
        """Sincroniza a la base de datos, en segundo plano, las redes que NO son la activa, cada
        una con SU intervalo configurado, para que al volver a ellas estén al día sin esperar. El
        servicio ACTIVO ya lo sondea _auto_timer y Mastodon (todas sus cuentas) lo cubre además
        _mastodon_bg_timer; aquí se cubren RSS, Bluesky y Telegram cuando estás en otra red.

        X solo entra si su actualización AUTOMÁTICA está activada en Preferencias (por defecto NO):
        tiene límites de peticiones muy estrictos y por defecto va bajo demanda (F5)."""
        from app.core import feature_flags
        from app.core.task_manager import task_manager
        from app.services.rss.rss_service import rss_service
        from app.services.telegram.telegram_service import telegram_service
        import time as _t
        if not config.get("general", "auto_update_enabled", True):
            return
        ahora = _t.time()
        ultima = self._ultima_sync_bg

        def _toca(serv, intervalo_seg):
            """True si toca sincronizar 'serv' (venció su intervalo). La PRIMERA vez solo registra
            la hora y NO dispara: el arranque (_auto_sincronizar) ya dejó todo al día."""
            t0 = ultima.get(serv)
            if t0 is None:
                ultima[serv] = ahora
                return False
            if ahora - t0 >= intervalo_seg:
                ultima[serv] = ahora
                return True
            return False

        # Mastodon (auditoría A4): el SONDEO DE SEGURIDAD del tiempo real (por si un stream
        # pierde eventos sin caerse) antes solo corría con Mastodon como red ACTIVA; si te
        # pasabas horas en Telegram o RSS, una conexión "viva pero sorda" dejaba la cuenta
        # desactualizada. Ahora, en tiempo real y con otra red activa, cada ~2 min se hace
        # un catch-up ligero de los buffers de bajo tráfico de TODAS las cuentas Mastodon.
        if (feature_flags.servicio_habilitado("mastodon")
                and self.active_mastodon_account_id is None
                and config.get("mastodon", "realtime", False)):
            if _toca("mastodon_seguridad", 120):
                try:
                    ocultos_m = set(config.get("mastodon", "hidden_buffers", []) or [])
                    orden_m = config.get("mastodon", "buffer_order", [])
                    bajo = [b for b in ("home", "notifications", "direct", "sent")
                            if b in orden_m and b not in ocultos_m] or ["home", "notifications"]
                    for r in db.query("SELECT id FROM accounts WHERE service_id='mastodon' AND active=1"):
                        for k in bajo:
                            self._sincronizar_timeline_streaming(r[0], k, lambda s, e: None)
                except Exception as e:
                    log.debug(f"Sondeo de seguridad Mastodon en segundo plano: {e}")

        # RSS (lo más solicitado): si no estás en RSS, sincronizar sus feeds con su intervalo (en
        # minutos). Al volver a RSS, _cargar_listado_artículos lee de la base ya actualizada.
        if feature_flags.servicio_habilitado("rss") and self.active_rss_account_id is None:
            interval = max(60, int(config.get("rss", "update_interval", 30)) * 60)
            if _toca("rss", interval):
                try:
                    # Al traer artículos nuevos, refrescar EN SU SITIO los contadores de no leídos
                    # del árbol (sin reconstruirlo ni mover el foco), aunque estés en otra red.
                    def _cb_rss(n, e):
                        if not e and n:
                            wx.CallAfter(self._actualizar_contadores_rss)
                    for r in db.query("SELECT id FROM accounts WHERE service_id='rss' AND active=1"):
                        # Sondeo de FONDO: pocos feeds a la vez (CPU) para no robarle tiempo a la
                        # interfaz mientras navegas. El refresco MANUAL (F5) usa tu concurrencia.
                        # solo_vencidos: respeta el intervalo propio de cada feed y el
                        # retroceso por errores (scheduler por vencimiento, RSS-05).
                        rss_service.actualizar_todos_async(r[0], callback=_cb_rss, max_par_override=3,
                                                           unique_key=f"rss:update_all:{r[0]}",
                                                           solo_vencidos=True)
                except Exception as e:
                    log.debug(f"Sondeo de fondo RSS: {e}")

        # Bluesky: buffers de bajo tráfico (Inicio y Notificaciones) de cada cuenta, si no estás en
        # Bluesky (si lo estás, lo cubre _auto_timer o el tiempo real).
        if feature_flags.servicio_habilitado("bluesky") and self.active_bluesky_account_id is None:
            interval = max(60, int(config.get("bluesky", "update_interval", 60)))
            if _toca("bluesky", interval):
                try:
                    from app.services.bluesky import bluesky_service as _bsky
                    for r in db.query("SELECT id FROM accounts WHERE service_id='bluesky' AND active=1"):
                        # Cuenta con la sesión caducada: no lanzar tareas condenadas a fallar. Ya
                        # se avisó al usuario cuando caducó; hasta que vuelva a iniciar sesión,
                        # sondearla solo gasta hilos y ensucia el registro (eran tres tareas
                        # fallidas por minuto, horas y horas).
                        if _bsky.sesion_caducada(r[0]):
                            continue
                        for tl in ("home", "notifications"):
                            _bsky.cargar_timeline_async(r[0], timeline_type=tl, callback=lambda x, e: None)
                        self._sondear_chat_bluesky(r[0])
                except Exception as e:
                    log.debug(f"Sondeo de fondo Bluesky: {e}")
        elif feature_flags.servicio_habilitado("bluesky") and self.active_bluesky_account_id is not None:
            # Estás DENTRO de Bluesky: los buffers los cubre el temporizador/tiempo real,
            # pero los mensajes directos se mantienen al día con el getLog incremental.
            interval = max(60, int(config.get("bluesky", "update_interval", 60)))
            if _toca("bluesky_chat", interval):
                self._sondear_chat_bluesky(self.active_bluesky_account_id)

        # Telegram: refrescar la lista de chats (los mensajes ya llegan en tiempo real por el
        # cliente conectado; esto mantiene el orden y los contadores). Si no estás en Telegram.
        if feature_flags.servicio_habilitado("telegram") and self.active_telegram_account_id is None:
            interval = max(60, int(config.get("telegram", "update_interval", 60)))
            if _toca("telegram", interval):
                try:
                    cuentas_tg = [r[0] for r in db.query(
                        "SELECT id FROM accounts WHERE service_id='telegram' AND active=1")]
                    for a in cuentas_tg:
                        def _bg(ctx, acc=a):
                            return telegram_service.cargar_chats(acc, limit=100)
                        # unique_key: si un sondeo previo (o la carga de arranque) de los chats de
                        # esta cuenta sigue en curso, no se lanza otro encima.
                        task_manager.start_task(_bg, callback=lambda r, e: None,
                                                pass_context=True, unique_key=f"telegram:chats:{a}")
                except Exception as e:
                    log.debug(f"Sondeo de fondo Telegram: {e}")

        # X: SOLO si la actualización automática de X está activada (por defecto NO, por el riesgo
        # de bloqueo del cliente no oficial). Mantiene al día en segundo plano los buffers de bajo
        # tráfico cuando no estás en X, con su propio intervalo (piso min_update_seconds).
        if (feature_flags.servicio_habilitado("x") and self.active_x_account_id is None
                and config.get("x", "auto_update", False) and self.x_ctrl is not None):
            piso = int(config.get("x", "min_update_seconds", 120))
            interval = max(piso, int(config.get("x", "update_interval", piso)))
            if _toca("x", interval):
                try:
                    self.x_ctrl.sondear_buffers_fondo()
                except Exception as e:
                    log.debug(f"Sondeo de fondo X: {e}")

    def _sondear_chat_bluesky(self, acc):
        """Sincroniza los mensajes directos de Bluesky de forma INCREMENTAL con getLog
        (auditoría §11): altas, borrados, lecturas desde otros dispositivos y reacciones,
        sin re-descargar las conversaciones. Si la credencial no tiene permiso de chat,
        no se insiste. Suena y repinta la lista de directos si la estás mirando."""
        try:
            from app.services.bluesky import bluesky_service as _bsky
        except Exception:
            return
        if _bsky.chat_disponible(acc) is False:
            return
        # Sesión caducada: no insistir hasta que el usuario vuelva a iniciarla (ya está avisado).
        if _bsky.sesion_caducada(acc):
            return

        def _fin(res, err, a=acc):
            if err or not res:
                return
            if res.get("nuevos"):
                from app.core.event_bus import event_bus
                event_bus.emit("sound_trigger", "bluesky_new_notification")
            # Repintar la lista de conversaciones si es lo que estás viendo (sin mover el foco).
            try:
                if (res.get("convos") and self.active_bluesky_account_id == a
                        and getattr(self, "_modo_lista", "") == "bluesky_convos"):
                    self.bluesky_ctrl._cargar_convos()
            except Exception as e:
                log.debug(f"No se pudo repintar los directos tras el getLog: {e}")

        try:
            _bsky.sincronizar_chat_async(acc, callback=_fin)
        except Exception as e:
            log.debug(f"Sondeo de chat Bluesky: {e}")

    def _sondear_otras_cuentas_mastodon(self):
        """Mantiene al día en segundo plano los buffers de BAJO TRÁFICO (Inicio, Notificaciones,
        Directos) de las OTRAS cuentas de Mastodon (las que NO son la activa). El streaming y los
        sondeos normales solo cubren la cuenta activa, así que al cambiar a otra cuenta sus buffers
        estaban viejos y había que pulsar F5/Ctrl+Shift+R. Con esto, al cambiar ya están al día. Se
        llama con cadencia lenta (~2 min) y solo sondea los buffers de bajo tráfico para no gastar
        peticiones."""
        from app.core import feature_flags
        if not feature_flags.servicio_habilitado("mastodon"):
            return
        if not config.get("general", "auto_update_enabled", True):
            return
        # En TIEMPO REAL todas las cuentas de Mastodon se streamean (un gestor por cuenta), así que
        # este sondeo de respaldo es redundante: lo saltamos para no hacer peticiones de más.
        if config.get("mastodon", "realtime", False):
            return
        activa = self.active_mastodon_account_id
        try:
            cuentas = [r[0] for r in db.query(
                "SELECT id FROM accounts WHERE service_id = 'mastodon' AND active = 1 "
                "ORDER BY position, id")]
        except Exception:
            return
        otras = [c for c in cuentas if c != activa]
        if not otras:
            return
        ocultos = set(config.get("mastodon", "hidden_buffers", []) or [])
        orden = config.get("mastodon", "buffer_order", [])
        bajo_trafico = [b for b in ("home", "notifications", "direct", "sent")
                        if b in orden and b not in ocultos] or ["home", "notifications"]
        for acc in otras:
            for k in bajo_trafico:
                try:
                    self._sincronizar_timeline_streaming(acc, k, lambda s, e: None)
                except Exception:
                    pass

    def _min_id_streaming(self, acc, timeline_key):
        """Id más reciente que YA tenemos de ese timeline, para pedir SOLO lo nuevo (sondeo
        incremental) en vez de traer una página entera de 40. Devuelve None (traer página, rellenando
        hueco) para los timelines de ALTA prioridad donde no queremos perder nada aunque entren muchos
        entre eventos (home, notificaciones, menciones); igual que el sondeo del buffer activo."""
        rellenables = ("home", "sent", "notifications", "mentions")
        if timeline_key in rellenables or ":" in timeline_key:
            return None
        try:
            if timeline_key == "notifications":
                row = db.query_one("SELECT notification_id FROM mastodon_notifications "
                                   "WHERE account_id = ? ORDER BY created_at DESC LIMIT 1", (acc,))
            else:
                row = db.query_one("""SELECT s.status_id FROM mastodon_statuses s
                    JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id
                    WHERE t.account_id = ? AND t.timeline = ?
                    ORDER BY s.created_at DESC LIMIT 1""", (acc, timeline_key))
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def _sincronizar_timeline_streaming(self, acc, timeline_key, callback):
        """Sincroniza a la BD el timeline indicado (lo nuevo). Usa la función adecuada según sea
        un timeline normal, una lista o una búsqueda/hashtag. Para los de bajo tráfico/firehose pide
        SOLO lo más nuevo (min_id): así incorporar una novedad del streaming no baja 40 toots cada vez.
        El min_id es una consulta indexada mínima (LIMIT 1), como el sondeo del buffer activo."""
        if timeline_key.startswith("list:"):
            mastodon_service.cargar_timeline_lista_async(
                acc, timeline_key.split(":", 1)[1], timeline_key, callback=callback)
        elif timeline_key.startswith("search:"):
            mastodon_service.buscar_y_guardar_async(
                acc, timeline_key.split(":", 1)[1], timeline_key, callback=callback)
        else:
            mastodon_service.cargar_timeline_async(
                acc, timeline_type=timeline_key,
                min_id=self._min_id_streaming(acc, timeline_key), callback=callback)

    def _gestionar_streaming_mastodon(self):
        """Mantiene el streaming en tiempo real de TODAS las cuentas de Mastodon activas (UN gestor
        por cuenta) mientras Mastodon esté habilitado, el tiempo real activado y la auto-actualización
        encendida, INCLUSO si estás en otra red. Así las novedades (Inicio, Menciones, Notificaciones,
        Directos) entran conforme llegan en CADA cuenta, no solo en la activa. Si no procede, detiene
        y descarta todos los gestores."""
        try:
            from app.core import feature_flags
            from app.services.mastodon.streaming_manager import MastodonStreamingManager
            gestores = getattr(self, "_streamings_mastodon", None)
            if gestores is None:
                self._streamings_mastodon = gestores = {}
            if feature_flags.servicio_habilitado("mastodon"):
                self._preparar_citas_pendientes_mastodon()   # una vez por sesión
            activar = (feature_flags.servicio_habilitado("mastodon")
                       and config.get("general", "auto_update_enabled", True)
                       and config.get("mastodon", "realtime", False))
            if not activar:
                for sm in list(gestores.values()):
                    try:
                        sm.detener()
                    except Exception:
                        pass
                gestores.clear()
                return
            try:
                cuentas = [r[0] for r in db.query(
                    "SELECT id FROM accounts WHERE service_id='mastodon' AND active=1 ORDER BY position, id")]
            except Exception:
                cuentas = []
            deseadas = set(cuentas)
            # Detener gestores de cuentas que ya no están activas.
            for acc in list(gestores.keys()):
                if acc not in deseadas:
                    try:
                        gestores[acc].detener()
                    except Exception:
                        pass
                    gestores.pop(acc, None)
            # Crear/iniciar un gestor por cada cuenta deseada (iniciar es idempotente). Las claves
            # son POR CUENTA (incluyen sus hashtags seguidos), y se refresca esa lista en segundo
            # plano (si cambia, esto se vuelve a llamar y re-streamea).
            for acc in deseadas:
                self._refrescar_hashtags_seguidos_stream(acc)
                claves = self._timelines_para_streaming(acc)
                sm = gestores.get(acc)
                if sm is None:
                    # El gestor entrega (timeline, tipo, payload): antes esta lambda solo
                    # aceptaba el timeline, el TypeError se tragaba y ningún evento llegaba.
                    sm = MastodonStreamingManager(
                        (lambda tk, tipo=None, payload=None, a=acc:
                         self._on_evento_streaming(a, tk, tipo, payload)),
                        on_reconnect=(lambda a=acc: wx.CallAfter(self._catchup_streaming_mastodon, a)))
                    gestores[acc] = sm
                sm.iniciar(acc, claves)
        except Exception as e:
            log.debug(f"Gestión de streaming de Mastodon: {e}")

    def _catchup_streaming_mastodon(self, account_id=None):
        """Tras RECONECTAR el streaming de una cuenta (lo dispara su watchdog), recupera lo perdido en
        el corte. Si estás en esa cuenta, refresca su lista; si no (otra red u otra cuenta), solo
        sincroniza a la BD. Si no se pasa cuenta, lo hace para todas las que tengan gestor."""
        if not config.get("mastodon", "realtime", False):
            return
        cuentas = [account_id] if account_id else list(getattr(self, "_streamings_mastodon", {}).keys())
        # Con un modal delante (p. ej. el compositor) solo se sincroniza a la BD: repintar la lista
        # tapada atascaba la escritura. Se pone al día al cerrarlo (ver mf_diferido_modal).
        modal = self._modal_delante()
        for acc in cuentas:
            if not acc:
                continue
            try:
                if self.active_mastodon_account_id == acc and not modal:
                    self._actualizar_mastodon_incremental()
                    self._sondear_streamables_no_activos()
                else:
                    cb = lambda s, e: None
                    if self.active_mastodon_account_id == acc:
                        cb = self._cb_sync_diferido_por_modal
                    for k in self._timelines_para_streaming():
                        self._sincronizar_timeline_streaming(acc, k, cb)
            except Exception as e:
                log.debug(f"Sondeo de recuperación tras reconectar el streaming: {e}")

    def _on_mastodon_publicado(self, account_id):
        """(Puede llegar de un hilo de fondo) Acabas de publicar: si estás mirando Enviados
        o Inicio de esa cuenta, insertar la publicación nueva en su sitio sin mover el foco."""
        def _en_ui():
            if not self or self.active_mastodon_account_id != account_id:
                return
            if getattr(self, "_cargando_buffers", False):
                return  # el cierre del diálogo de carga ya repinta el buffer activo
            if self.active_timeline_type in ("sent", "home", "direct"):
                if self._modal_delante():
                    self._diferir_por_modal()
                    return
                try:
                    self._insertar_items_incremental(self._filas_mastodon_actual())
                except Exception:
                    pass
        try:
            wx.CallAfter(_en_ui)
        except Exception:
            pass

    def _on_evento_streaming(self, account_id, timeline_key, tipo=None, payload=None):
        """Lo llama el HILO del streaming de una cuenta por CADA novedad.

        1) Si el evento trae su PAYLOAD (alta, edición, borrado, notificación, conversación),
           se aplica DIRECTAMENTE a la caché aquí mismo (hilo de fondo, prioridad baja): así
           las ediciones y borrados llegan aunque el refresco por min_id no pudiera verlos
           (auditoría M1), y sin repetir una consulta REST.
        2) COALESCEMOS por (cuenta, timeline): en cuanto hay un refresco pendiente para esa
           pareja, los siguientes eventos solo acumulan estado. Así, aunque lleguen ráfagas,
           se publica UN wx.CallAfter por ventana y no se inunda el hilo de la interfaz. La
           coalescencia es de BORDE DE ENTRADA: si hace rato que no se repinta ese timeline, la
           novedad se muestra AL INSTANTE; solo en ráfaga se espera a completar la ventana."""
        import time as _t
        if tipo == "filters_changed":
            # Filtros cambiados (aquí o en otro cliente): recalcular y repintar.
            from app.services.mastodon.filtros_servidor import recalcular_buffer_activo
            wx.CallAfter(recalcular_buffer_activo, self, account_id)
            return
        procesado = False
        if tipo:
            try:
                procesado = mastodon_service.procesar_evento_stream(
                    account_id, timeline_key, tipo, payload)
            except Exception as e:
                log.debug(f"Evento de streaming no aplicable en caché ({tipo}): {e}")
        clave = (account_id, timeline_key)
        with self._lock_streaming:
            info = self._estado_evento_streaming.setdefault(
                clave, {"rest": False, "borrado": False})
            if not procesado:
                info["rest"] = True
            if procesado and tipo == "delete":
                info["borrado"] = True
            if clave in self._refrescos_streaming_pendientes:
                return
            self._refrescos_streaming_pendientes.add(clave)
            # Retardo de BORDE DE ENTRADA: 0 ms si hace más de una ventana que no se repinta este
            # timeline (novedad aislada → al instante); si acaba de repintarse (ráfaga), se espera
            # solo lo que falte para completar la ventana, juntando los eventos que sigan llegando.
            ultimo = self._ultimo_refresco_streaming.get(clave, 0.0)
            transcurrido_ms = (_t.monotonic() - ultimo) * 1000.0
            retardo = 0 if transcurrido_ms >= _VENTANA_COALESCENCIA_MS else int(
                _VENTANA_COALESCENCIA_MS - transcurrido_ms)
        wx.CallAfter(self._programar_refresco_streaming, account_id, timeline_key, retardo)

    def _programar_refresco_streaming(self, account_id, timeline_key, retardo_ms=0):
        # En el hilo de UI: programar el refresco real tras la ventana de coalescencia calculada
        # (0 ms para una novedad aislada; lo que falte de la ventana durante una ráfaga).
        wx.CallLater(max(0, int(retardo_ms)), self._refrescar_por_streaming,
                     account_id, timeline_key)

    def _refrescar_por_streaming(self, account_id, timeline_key):
        import time as _t
        with self._lock_streaming:
            self._refrescos_streaming_pendientes.discard((account_id, timeline_key))
            info = self._estado_evento_streaming.pop((account_id, timeline_key), None)
            # Sellar el instante del repintado: la ventana de coalescencia del siguiente evento se
            # mide desde aquí (así una novedad aislada tras un rato tranquilo entra al instante).
            self._ultimo_refresco_streaming[(account_id, timeline_key)] = _t.monotonic()
        if not config.get("mastodon", "realtime", False):
            return
        # Con el diálogo "Cargando buffers" delante no se repinta NADA: las novedades ya
        # están en la base de datos y el cierre del diálogo repinta el buffer activo. Cada
        # inserción invisible generaba eventos para el lector de pantalla durante la carga.
        if getattr(self, "_cargando_buffers", False):
            return
        acc = account_id
        if not acc:
            return
        # Si TODOS los eventos coalescidos traían payload, ya están aplicados a la caché:
        # basta con repintar la lista si es el buffer que estás viendo (sin petición REST,
        # auditoría M1). REST queda solo para eventos sin payload y recuperaciones.
        if info is not None and not info.get("rest"):
            if self.active_mastodon_account_id != acc:
                return  # otra red/cuenta: ya quedó en la BD; aparecerá al volver
            es_activo = (timeline_key == self.active_timeline_type) or (
                timeline_key == "notifications" and self.active_timeline_type in ("notifications", "mentions"))
            if not es_activo:
                return
            if self._modal_delante():
                # Lista tapada por un modal (el compositor): ya está en la caché; se repinta al
                # cerrarlo. Repintarla ahora atascaba las teclas del compositor.
                self._diferir_por_modal(borrado=bool(info.get("borrado")))
                return
            try:
                if info.get("borrado"):
                    # Hubo un borrado: recargar desde caché (quitar filas exige reconstruir).
                    self._cargar_listado_toots(cache_only=True)
                else:
                    self._insertar_items_incremental(self._filas_mastodon_actual())
            except Exception:
                pass
            return

        # La novedad se SINCRONIZA a la base de datos (de SU cuenta), aunque estés en otra red
        # o en otra cuenta (así entra conforme llega y aparece al volver). Solo se refresca la
        # LISTA en pantalla si esa cuenta es la activa y es el buffer que estás viendo.
        def _cb(statuses, error):
            if error or not statuses:
                return
            if self.active_mastodon_account_id != acc:
                return  # otra red/cuenta: ya quedó en la BD; aparecerá al volver
            es_activo = (timeline_key == self.active_timeline_type) or (
                timeline_key == "notifications" and self.active_timeline_type in ("notifications", "mentions"))
            if es_activo and self._modal_delante():
                self._diferir_por_modal()
            elif es_activo:
                try:
                    self._insertar_items_incremental(self._filas_mastodon_actual())
                except Exception:
                    pass

        try:
            self._sincronizar_timeline_streaming(acc, timeline_key, _cb)
        except Exception:
            pass

    def _on_auto_timer(self, event):
        """Sondeo periódico del buffer activo para que entren elementos nuevos solos."""
        # Con un diálogo MODAL delante (el compositor, sobre todo) la lista está tapada: nada de
        # repintar "hace X", contadores ni insertar novedades, que es trabajo en el hilo de la
        # interfaz y retrasaba las teclas al escribir. Se recupera al cerrarlo (este mismo tick o
        # la reactivación de la ventana) con _ponerse_al_dia_tras_modal.
        if self._modal_delante():
            self._diferir_por_modal()
            return
        self._ponerse_al_dia_tras_modal()
        if self._updating:
            # Watchdog: si una actualización dejó el flag puesto (un callback que no volvió),
            # el sondeo se quedaría bloqueado para siempre y habría que pulsar F5 a mano.
            # Tras varios ticks bloqueado seguidos, se libera el flag atascado y se continúa.
            self._update_skips = getattr(self, "_update_skips", 0) + 1
            if self._update_skips >= 4:
                log.debug("Watchdog: flag de actualización atascado; se libera para reanudar el sondeo.")
                self._updating = False
                self._update_skips = 0
            else:
                return
        self._update_skips = 0
        # Mantener al día los "hace X" de la lista visible aunque no te muevas (si no, tras
        # horas abierta todo se quedaba en "hace unos segundos" hasta pulsar F5).
        try:
            self.lista_items.refrescar_todas_las_fechas()
        except Exception:
            pass
        # BANDEJA DE ENTRADA activa: sondear sus fuentes e ir insertando lo nuevo (sin mover el foco),
        # para que se vaya llenando sola conforme llegan publicaciones.
        # Se comprueba TAMBIÉN _bandeja_activa: mientras un menú contextual o "Ver publicación"
        # de la bandeja están abiertos, _fijar_contexto_bandeja cambia _modo_lista y la cuenta
        # activa a los de ESA fila; si el tick caía en ese momento, entraba por la rama de su red
        # (p. ej. RSS) y el callback insertaba sus filas encima de la bandeja con las columnas de
        # la bandeja (filas solo con autor y "hace X", y orígenes desalineados).
        if (getattr(self, "_modo_lista", "") == "bandeja"
                or getattr(self, "_bandeja_activa", False)):
            try:
                self._sondear_fuentes_bandeja()
            except Exception as e:
                log.debug(f"Sondeo automático de la bandeja: {e}")
            return
        # Mantener al día EN SU SITIO los contadores (favoritos/impulsos/respuestas) de Mastodon
        # visibles, igual que el "hace X minutos" (barato: solo repinta lo que cambió en la BD).
        try:
            self._refrescar_contadores_mastodon()
        except Exception:
            pass
        # Y, con una cadencia más lenta, RE-CONSULTAR AL SERVIDOR los contadores de las filas
        # visibles para reflejar lo que hacen OTROS (responder/favoritear/impulsar tu publicación),
        # que el streaming no actualiza en el toot original. Limitado a ~45 s para no saturar.
        try:
            import time as _t
            ahora = _t.monotonic()
            if ahora - getattr(self, "_ultimo_contadores_srv", 0) >= 45:
                self._ultimo_contadores_srv = ahora
                self._refrescar_contadores_servidor()
        except Exception:
            pass
        if self.active_mastodon_account_id:
            if not config.get("mastodon", "realtime", False):
                # Modo INTERVALO: sondeo en cada tick (buffer activo + bajo tráfico de fondo).
                self._actualizar_mastodon_incremental()
                # Y mantenemos al día en segundo plano los DEMÁS buffers streamables visibles
                # (Inicio, Notificaciones, Local, Federada, Directos), no solo el activo.
                self._sondear_streamables_no_activos()
            else:
                # Modo TIEMPO REAL: las novedades llegan por el streaming. Pero, como hace TWBlue
                # (streaming + sondeo de respaldo cada ~2 min), añadimos una RED DE SEGURIDAD: cada
                # ciertos ticks sondeamos igualmente el buffer activo y los de bajo tráfico, por si
                # el streaming pierde algún evento o el stream de notificaciones se cae sin avisar
                # (síntoma: las notificaciones dejaban de actualizarse solas). El resto de ticks
                # solo refresca los "hace X".
                self._safety_ticks = getattr(self, "_safety_ticks", 0) + 1
                if self._safety_ticks >= 4:  # con el tick de 30 s en tiempo real → ~2 min
                    self._safety_ticks = 0
                    self._actualizar_mastodon_incremental()
                    self._sondear_streamables_no_activos()
        elif self.active_telegram_account_id:
            self._actualizar_telegram_incremental()
        elif self.active_rss_account_id:
            self._actualizar_rss_incremental()
        elif self.active_bluesky_account_id:
            realtime = config.get("bluesky", "realtime", False)
            if not realtime:
                # Modo INTERVALO: sondeo del buffer activo en cada tick.
                self.bluesky_ctrl.actualizar_incremental()
            else:
                # Modo TIEMPO REAL: el firehose avisa de novedades y dispara el refresco; aquí solo
                # una RED DE SEGURIDAD periódica (por si el firehose se cae o pierde algo).
                self._bsky_safety = getattr(self, "_bsky_safety", 0) + 1
                if self._bsky_safety >= 4:
                    self._bsky_safety = 0
                    self.bluesky_ctrl.actualizar_incremental()
            # En ambos modos: cada ~4 ticks, mantener al día EN SEGUNDO PLANO los demás buffers de
            # bajo tráfico (Inicio, Notificaciones, Menciones), para que al cambiar ya estén listos.
            self._bsky_fondo = getattr(self, "_bsky_fondo", 0) + 1
            if self._bsky_fondo >= 4:
                self._bsky_fondo = 0
                self.bluesky_ctrl.sondear_buffers_fondo()
        elif self.active_x_account_id and self.x_ctrl is not None:
            # X solo se sondea solo si la actualización automática de X está activada (por
            # defecto va en MANUAL: solo F5/Ctrl+Shift+R, para no arriesgar el bloqueo de la
            # cuenta con el cliente no oficial). El refresco de "hace X" de arriba sí se hace.
            if config.get("x", "auto_update", False):
                self.x_ctrl.actualizar_incremental()
        elif getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            # Reddit: sondeo del buffer de posts activo en cada tick (modo intervalo).
            self.reddit_ctrl.actualizar_incremental()
        elif getattr(self, "active_peertube_account_id", None) and getattr(self, "peertube_ctrl", None) is not None:
            # PeerTube: sondeo del catálogo de vídeos activo en cada tick (modo intervalo).
            self.peertube_ctrl.actualizar_incremental()

    def _init_ui(self):
        # Barra de estado
        self.statusbar = self.CreateStatusBar(2)
        self.statusbar.SetStatusText(_("Listo"), 0)
        # Campo 1: contexto del buffer (red, buffer y posición). Se rellena al cargar un buffer; aquí
        # solo un valor inicial mientras no hay nada seleccionado.
        self.statusbar.SetStatusText(_("Sin buffer seleccionado"), 1)
        
        # Panel divisor principal
        self.splitter = wx.SplitterWindow(self, style=wx.SP_3D | wx.SP_LIVE_UPDATE)
        
        # Panel izquierdo: Navegación por cuentas/servicios
        self.panel_izq = wx.Panel(self.splitter)
        sizer_izq = wx.BoxSizer(wx.VERTICAL)
        
        lbl_nav = wx.StaticText(self.panel_izq, label=_("Servicios y cuentas:"))
        self.tree_nav = wx.TreeCtrl(
            self.panel_izq, 
            style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_SINGLE,
            name=_("Árbol de navegación de cuentas y servicios")
        )
        self.tree_nav.Bind(wx.EVT_TREE_SEL_CHANGED, self._on_nav_selection)
        self.tree_nav.Bind(wx.EVT_KEY_DOWN, self._on_tree_key_down)
        self.tree_nav.Bind(wx.EVT_TREE_ITEM_MENU, self._on_tree_context_menu)
        # Ayuda para Ctrl+H (describe el control enfocado).
        self.tree_nav.SetToolTip(_(
            "Árbol de cuentas y buffers. Flechas para moverte; Intro abre el buffer. Ctrl+flecha "
            "arriba/abajo reordena los servicios; Alt+1 a 9 va al servicio según su posición. La "
            "tecla de menú abre las opciones del nodo."))
        
        sizer_izq.Add(lbl_nav, 0, wx.ALL, 5)
        sizer_izq.Add(self.tree_nav, 1, wx.EXPAND | wx.ALL, 5)
        self.panel_izq.SetSizer(sizer_izq)
        
        # Panel derecho: Contenido del buffer seleccionado (Lista + Detalles)
        self.panel_der = wx.Panel(self.splitter)
        sizer_der = wx.BoxSizer(wx.VERTICAL)
        
        self.lbl_buffer_titulo = wx.StaticText(self.panel_der, label=_("Timeline / Artículos:"))
        
        # Usar AccessibleListCtrl en MODO VIRTUAL: la lista principal puede tener miles de
        # elementos (timelines/feeds/chats sin límite). En virtual solo se dibuja lo visible,
        # así que refrescar o cambiar de buffer es instantáneo y no bloquea el teclado (como
        # TWBlue/FastSM). El resto de listas (diálogos, pocas filas) siguen sin virtualizar.
        self.lista_items = AccessibleListCtrl(
            self.panel_der,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de elementos"),
            virtual=True
        )
        self.lista_items.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)
        self.lista_items.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
        self.lista_items.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
        # Ayuda para Ctrl+H (describe el control enfocado).
        self.lista_items.SetToolTip(_(
            "Lista de publicaciones o artículos. Flechas para moverte; Intro abre el elemento; la "
            "tecla de menú (o Mayús+F10) abre las acciones. Flecha derecha/izquierda o Ctrl+1 a 9 lee "
            "por columnas. Cada red tiene sus propios atajos (Ctrl+R responder, Ctrl+B impulsar…)."))
        
        # Detalles del elemento
        self.txt_detalles = AccessibleTextCtrl(
            self.panel_der,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SUNKEN,
            name=_("Detalles del elemento seleccionado"),
            ayuda_texto=_("Muestra el contenido completo del toot o artículo seleccionado.")
        )
        
        # Botonera de acciones (opcional, como el panel de detalles): botones que CAMBIAN según el
        # buffer y actúan sobre el elemento enfocado. Va DESPUÉS del panel de detalles. Se rellena
        # dinámicamente en _actualizar_botonera(); empieza vacío y oculto.
        self.panel_botonera = wx.Panel(self.panel_der, name=_("Acciones del elemento"))
        self.sizer_botonera = wx.WrapSizer(wx.HORIZONTAL)
        self.panel_botonera.SetSizer(self.sizer_botonera)

        sizer_der.Add(self.lbl_buffer_titulo, 0, wx.ALL, 5)
        sizer_der.Add(self.lista_items, 3, wx.EXPAND | wx.ALL, 5)
        sizer_der.Add(self.txt_detalles, 2, wx.EXPAND | wx.ALL, 5)
        sizer_der.Add(self.panel_botonera, 0, wx.EXPAND | wx.ALL, 3)
        self.panel_der.SetSizer(sizer_der)
        self.panel_botonera.Hide()
        
        # Configurar divisor
        self.splitter.SplitVertically(self.panel_izq, self.panel_der, 250)
        self.splitter.SetMinimumPaneSize(150)
        
        # Complementos (plugins): descubrir y activar los habilitados ANTES de construir los menús,
        # para que sus entradas aparezcan en el menú "Complementos". Aislado: si algo falla, sigue.
        try:
            from app.core.plugins import plugin_manager
            plugin_manager.main_window = self
            plugin_manager.descubrir()
            plugin_manager.activar_guardados()
        except Exception as e:
            log.error(f"No se pudo iniciar el sistema de complementos: {e}")

        # Crear menús y barra de herramientas
        self._crear_menus()
        
        # Registrar y aplicar atajos de teclado
        self._registrar_atajos_teclado()
