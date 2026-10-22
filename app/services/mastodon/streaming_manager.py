# -*- coding: utf-8 -*-
"""Gestor de STREAMING (tiempo real) de Mastodon.

Abre, en segundo plano y con reconexión automática, un stream por cada buffer STREAMABLE que el
usuario tenga VISIBLE (sin ocultar):
  - stream_user   → Inicio + Notificaciones + Menciones (una sola conexión)
  - stream_direct → mensajes directos
  - stream_local  → línea local
  - stream_public → línea federada
  - stream_list   → cada lista
  - stream_hashtag→ cada búsqueda guardada que sea un hashtag

No procesa el payload: cuando llega algo, avisa con un callback indicando DE QUÉ timeline es, para
que la interfaz sincronice ese timeline (reutilizando el sondeo ya probado). Mastodon.py usa SSE
(una conexión HTTP por stream), así que se limita el número de listas/hashtags simultáneos para no
abrir demasiadas conexiones. Si la instancia no tiene streaming, no arranca (se usa el sondeo).
"""
import logging
import threading

log = logging.getLogger(__name__)

# Prioridad de hilo INFERIOR para los hilos de fondo del streaming (regla del proyecto: los hilos de
# fondo no deben robarle tiempo a la interfaz ni al lector de pantalla). El trabajo real (parsear el
# evento SSE y guardarlo) corre en los hilos INTERNOS de Mastodon.py, así que se baja la prioridad
# desde DENTRO del callback, además de en nuestros propios hilos. Idempotente por hilo (una vez).
_prio_local = threading.local()


def _bajar_prioridad_hilo():
    if getattr(_prio_local, "hecho", False):
        return
    _prio_local.hecho = True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    except Exception:
        pass

# Tope de streams de LISTAS + HASHTAGS a la vez (además de user/direct/local/public), para no
# abrir un número excesivo de conexiones HTTP persistentes.
MAX_STREAMS_EXTRA = 6

# Espera (segundos) entre reintentos de reconexión de cada stream cuando el servidor falla. El
# PRIMER reintento de Mastodon.py es inmediato; esta espera solo se aplica entre fallos seguidos.
# 10 s (en vez de 30) hace que vuelva al tiempo real mucho antes tras un corte, como TWBlue.
RECONNECT_WAIT_SEC = 10

# Cada cuántos segundos el watchdog comprueba que las conexiones siguen vivas.
WATCHDOG_INTERVALO_SEC = 15

# Segundos SIN RECIBIR NADA (ni un evento ni un latido de keep-alive) tras los que una conexión se
# considera MUERTA aunque su hilo siga vivo.
#
# Es la avería que no se detectaba: una conexión de streaming puede quedarse "colgada" —abierta a
# efectos del sistema, pero sin que llegue un solo byte— cuando la corta silenciosamente un proxy,
# un router NAT o la propia instancia. El hilo sigue vivo, así que el vigilante la daba por buena
# PARA SIEMPRE y las publicaciones dejaban de llegar al instante: solo aparecían cuando algo forzaba
# un refresco (cambiar de buffer, el repaso de contadores...), con el retraso de casi un minuto que
# se notaba. Mastodon manda un latido cada ~30 s, así que 90 segundos de silencio son tres latidos
# perdidos: suficiente para no reconectar en falso con una instancia lenta.
SIN_DATOS_MAX_SEC = 90


class MastodonStreamingManager:
    def __init__(self, on_event, on_reconnect=None):
        # on_event(timeline_key, tipo=None, payload=None): se invoca (en hilo de fondo) cuando
        # llega algo de ese timeline. `tipo` es el evento del stream ("update", "status_update",
        # "delete", "notification", "conversation") y `payload` el OBJETO COMPLETO que entrega
        # Mastodon.py (o el id, en delete): el receptor puede guardarlo directamente en la caché
        # sin repetir una consulta REST (auditoría M1). Si tipo/payload van vacíos, el receptor
        # debe sincronizar por REST como siempre. El receptor DEBE usar wx.CallAfter para la UI.
        # on_reconnect(): se invoca (en hilo de fondo) cuando el watchdog RELANZA un stream caído,
        # para que la interfaz sondee y recupere lo que se perdiera durante el corte.
        self._on_event = on_event
        self._on_reconnect = on_reconnect
        self._account_id = None
        self._claves = ()          # claves de timeline actualmente en streaming
        self._handles = []
        self._lock = threading.Lock()
        # GENERACIÓN de arranque (auditoría A3): cada iniciar() sube el número; un arranque
        # que termina cuando ya hay una generación más nueva CIERRA sus conexiones en vez de
        # registrarlas (antes, dos iniciar() rápidos dejaban conexiones huérfanas abiertas).
        self._gen = 0
        # Inventario del último arranque: cuántos streams se INTENTARON abrir. El watchdog
        # compara con los vivos: si faltan (uno falló al abrir o murió), relanza. Antes, un
        # stream ausente de la lista se daba por bueno para siempre.
        self._esperados = 0
        self._arranque_completado = False
        # Backoff exponencial con jitter para los relanzamientos del watchdog.
        self._reintentos = 0
        self._proximo_reintento = 0.0
        # Watchdog: vigila que los hilos de los streams sigan vivos y reconecta si alguno cae (p. ej.
        # por una excepción de red de tipo no contemplado por Mastodon.py, que mata su hilo sin
        # reconectar). Sin esto, un stream muerto no volvía hasta reiniciar la app.
        self._watchdog = None
        self._watchdog_stop = threading.Event()
        # Momento del ÚLTIMO dato recibido por el streaming de esta cuenta: cuenta tanto un evento
        # real como un latido de keep-alive. Es lo que permite distinguir "conexión sana pero
        # tranquila" de "conexión colgada", que a efectos del hilo son idénticas.
        self._ultimo_dato = 0.0
        self._n_eventos = 0        # novedades recibidas por el streaming
        self._n_latidos = 0        # latidos de mantenimiento del servidor

    def _marcar_dato(self, evento=False):
        """Apunta que ACABA de llegar algo por el streaming.

        `evento` distingue una novedad de verdad de un simple latido de mantenimiento. Se llevan
        las dos cuentas porque juntas dicen mucho al diagnosticar: latidos subiendo y eventos a
        cero significa que la conexión está bien pero el servidor no envía novedades."""
        import time
        self._ultimo_dato = time.monotonic()
        if evento:
            self._n_eventos += 1
        else:
            self._n_latidos += 1

    def segundos_sin_datos(self) -> float:
        """Cuánto hace que no llega nada por el streaming. 0 si aún no ha arrancado del todo."""
        import time
        return (time.monotonic() - self._ultimo_dato) if self._ultimo_dato else 0.0

    @staticmethod
    def _handle_vivo(h):
        """True si el hilo del stream sigue vivo. Si aún no se puede determinar (justo al arrancar,
        antes de que el hilo registre su referencia), se asume vivo para no relanzar en falso."""
        try:
            return h.is_alive()
        except Exception:
            return True

    def iniciar(self, account_id, claves):
        """Arranca el streaming para la cuenta con la lista de timelines streamables visibles.
        Idempotente: si ya está con la MISMA cuenta y las MISMAS claves Y todas sus conexiones
        siguen VIVAS (y no falta ninguna del inventario), no hace nada."""
        claves = tuple(claves or ())
        with self._lock:
            if (self._account_id == account_id and self._claves == claves
                    and self._handles and len(self._handles) >= self._esperados
                    and all(self._handle_vivo(h) for h in self._handles)):
                return
            self._detener_locked()
            self._account_id = account_id
            self._claves = claves
            self._gen += 1
            gen = self._gen
            self._arranque_completado = False
        threading.Thread(target=self._arrancar, args=(account_id, claves, gen), daemon=True).start()
        self._asegurar_watchdog()

    def _asegurar_watchdog(self):
        """Arranca el hilo watchdog si no está ya en marcha. Bajo lock: dos llamadas
        simultáneas a iniciar() podían arrancar DOS watchdogs (carrera benigna pero inútil)."""
        with self._lock:
            if self._watchdog is not None and self._watchdog.is_alive():
                return
            self._watchdog_stop.clear()
            self._watchdog = threading.Thread(target=self._bucle_watchdog, daemon=True)
            self._watchdog.start()

    def _bucle_watchdog(self):
        """Cada pocos segundos: si el streaming debe seguir activo pero alguna conexión cayó,
        FALTA alguna del inventario (falló al abrir) o no quedó ninguna (fallo total en el
        arranque), relanza con BACKOFF exponencial con jitter y avisa para sondear lo perdido."""
        import random
        import time
        _bajar_prioridad_hilo()
        while not self._watchdog_stop.wait(WATCHDOG_INTERVALO_SEC):
            with self._lock:
                account_id = self._account_id
                claves = self._claves
                handles = list(self._handles)
                esperados = self._esperados
                completado = self._arranque_completado
            if not account_id or not claves:
                continue                      # streaming parado: nada que vigilar
            if not completado:
                continue                      # arranque en curso: espera al siguiente ciclo
            sanos = [h for h in handles if self._handle_vivo(h)]
            # Conexión COLGADA: el hilo vive, pero no llega ni un latido. Es la avería que dejaba
            # el tiempo real muerto sin que nadie se enterara, porque a efectos del hilo todo
            # parecía correcto. Se trata igual que una caída: relanzar y recuperar lo perdido.
            silencio = self.segundos_sin_datos()
            colgado = silencio > SIN_DATOS_MAX_SEC
            if handles and len(sanos) >= esperados > 0 and not colgado:
                self._reintentos = 0          # todo sano: resetear el backoff
                log.debug("Streaming de Mastodon (cuenta %s): sano. %d eventos y %d latidos "
                          "recibidos; %.0f s desde el último dato.",
                          account_id, self._n_eventos, self._n_latidos, silencio)
                continue
            if colgado:
                log.warning("Streaming de Mastodon (cuenta %s): %.0f s sin recibir nada (ni "
                            "latidos); la conexión está colgada. Reconectando.",
                            account_id, silencio)
                # Al reconectar por silencio se limpia el inventario para que iniciar() NO se
                # considere idempotente y abra conexiones nuevas de verdad.
                with self._lock:
                    self._claves = ()
                self._marcar_dato()           # da margen antes de volver a evaluar el silencio
            # Falta algo (o no se abrió nada): relanzar, pero respetando el backoff para no
            # martillear a un servidor caído (10 s, 20, 40... hasta 5 min, con jitter).
            ahora = time.monotonic()
            if ahora < self._proximo_reintento:
                continue
            espera = min(300, RECONNECT_WAIT_SEC * (2 ** min(self._reintentos, 5)))
            self._proximo_reintento = ahora + espera + random.uniform(0, espera / 4)
            self._reintentos += 1
            log.info("Watchdog de streaming de Mastodon: %d/%d conexiones vivas; reconectando "
                     "(reintento %d).", len(sanos), esperados, self._reintentos)
            self.iniciar(account_id, claves)  # inventario incompleto -> no idempotente -> relanza
            if self._on_reconnect:
                try:
                    self._on_reconnect()
                except Exception:
                    pass

    def _arrancar(self, account_id, claves, gen=None):
        _bajar_prioridad_hilo()
        intentados = 0
        try:
            from app.services.mastodon.mastodon_service import mastodon_service
            from mastodon import CallbackStreamListener
            client = mastodon_service.obtener_cliente(account_id)
            if client is None:
                return
            try:
                if hasattr(client, "stream_healthy") and not client.stream_healthy():
                    log.info("Streaming de Mastodon no disponible en la instancia; se usará sondeo.")
                    with self._lock:
                        if self._account_id == account_id:
                            self._account_id = None
                            self._claves = ()
                    return
            except Exception:
                pass

            def ev(key, tipo=None, payload=None):
                # Corre en el hilo INTERNO de Mastodon.py (el que parsea el evento SSE): bajar su
                # prioridad para que el parseo/guardado no le robe tiempo a la interfaz ni al lector.
                _bajar_prioridad_hilo()
                self._marcar_dato(evento=True)
                log.debug("Streaming de Mastodon (cuenta %s): recibido '%s' de %s.",
                          account_id, tipo or "?", key)
                if self._account_id == account_id:
                    try:
                        self._on_event(key, tipo, payload)
                    except Exception:
                        pass

            def abrir(func, *args):
                nonlocal intentados
                intentados += 1
                try:
                    return func(*args, run_async=True, reconnect_async=True,
                                reconnect_async_wait_sec=RECONNECT_WAIT_SEC)
                except Exception as e:
                    log.debug("No se pudo abrir un stream de Mastodon: %s", e)
                    return None

            def _listener(key, con_notif=False, con_conv=False):
                """Listener con TODOS los eventos relevantes de ese timeline, entregando el
                payload completo (altas, ediciones y borrados; notificaciones/conversaciones
                donde procede). Un evento desconocido no debe romper el stream.

                Además APUNTA LA HORA de cada dato recibido —incluidos los latidos de keep-alive,
                que no son eventos pero demuestran que la conexión sigue viva—, para que el
                vigilante pueda detectar una conexión colgada."""
                kw = {
                    "update_handler": lambda s, kk=key: ev(kk, "update", s),
                    "status_update_handler": lambda s, kk=key: ev(kk, "status_update", s),
                    "delete_handler": lambda sid, kk=key: ev(kk, "delete", sid),
                    "unknown_event_handler": lambda *_a, **_kw: None,
                }
                if con_notif:
                    kw["notification_handler"] = lambda n: ev("notifications", "notification", n)
                    # Filtros del servidor cambiados (en esta u otra app): la UI los recalcula.
                    kw["filters_changed_handler"] = lambda: ev("notifications", "filters_changed", None)
                if con_conv:
                    kw["conversation_handler"] = lambda c, kk=key: ev(kk, "conversation", c)
                listener = CallbackStreamListener(**kw)
                # Mastodon.py no admite un manejador de latidos en el constructor, así que se
                # engancha sobre la instancia (conservando el comportamiento original).
                latido_original = listener.handle_heartbeat

                def _latido():
                    self._marcar_dato()
                    try:
                        latido_original()
                    except Exception:
                        pass
                listener.handle_heartbeat = _latido
                return listener

            handles = []
            claves_set = set(claves)

            # Inicio + Notificaciones + Menciones → una sola conexión (stream_user).
            if claves_set & {"home", "notifications", "mentions"}:
                handles.append(abrir(client.stream_user, _listener("home", con_notif=True)))
            if "direct" in claves_set:
                handles.append(abrir(client.stream_direct, _listener("direct", con_conv=True)))
            if "local" in claves_set:
                handles.append(abrir(client.stream_local, _listener("local")))
            if "public" in claves_set:
                handles.append(abrir(client.stream_public, _listener("public")))

            # Listas y hashtags (limitados a MAX_STREAMS_EXTRA en total).
            extra = 0
            for k in claves:
                if extra >= MAX_STREAMS_EXTRA:
                    break
                if k.startswith("list:"):
                    lid = k.split(":", 1)[1]
                    handles.append(abrir(client.stream_list, lid, _listener(k)))
                    extra += 1
                elif k.startswith("search:#"):
                    tag = k.split(":", 1)[1].lstrip("#")
                    handles.append(abrir(client.stream_hashtag, tag, _listener(k)))
                    extra += 1
                elif k.startswith("htfollow:"):
                    # Hashtag que SIGUES: se streamea, pero su toot se enruta a INICIO (ahí lo mete el
                    # servidor), para que aparezca al instante en tu Inicio como en TWBlue.
                    tag = k.split(":", 1)[1].lstrip("#")
                    handles.append(abrir(client.stream_hashtag, tag, _listener("home")))
                    extra += 1

            handles = [h for h in handles if h is not None]
            sobrantes = None
            with self._lock:
                # Si mientras se abrían las conexiones llegó una GENERACIÓN más nueva (otro
                # iniciar() o un detener()), estas conexiones sobran: no se registran
                # (antes quedaban huérfanas y duplicaban eventos).
                if ((gen is not None and gen != self._gen)
                        or self._account_id != account_id or self._claves != claves):
                    sobrantes = handles
                else:
                    self._handles = handles
                    self._esperados = intentados
                    self._arranque_completado = True
                    # Punto de partida del reloj de silencio: desde aquí se cuenta cuánto lleva
                    # la conexión sin dar señales de vida.
                    self._marcar_dato()
            if sobrantes is not None:
                # FUERA del lock y en un hilo aparte: cerrar una conexión SSE espera a la
                # red y puede tardar SEGUNDOS. Con el cierre dentro del lock, el hilo de la
                # INTERFAZ (que pide este mismo lock en iniciar() al navegar) se quedaba
                # congelado 8-19 s en el arranque ("No responde", Alt+Tab y el lector sin
                # reaccionar; cazado por el volcado de pila del vigilante). La misma regla
                # que ya aplicaba _detener_locked.
                threading.Thread(target=self._cerrar_handles, args=(sobrantes,),
                                 daemon=True).start()
                return
            if handles:
                log.info("Streaming de Mastodon iniciado (cuenta %s, %d/%d conexiones).",
                         account_id, len(handles), intentados)
            elif intentados:
                log.warning("Streaming de Mastodon: ninguna de las %d conexiones se pudo abrir; "
                            "el watchdog reintentará con espera creciente.", intentados)
        except Exception as e:
            with self._lock:
                if gen is None or gen == self._gen:
                    self._arranque_completado = True   # que el watchdog pueda reintentar
                    self._esperados = max(1, intentados)
            log.warning("No se pudo iniciar el streaming de Mastodon: %s", e)

    def detener(self):
        # Parar el watchdog ANTES de limpiar el estado, para que no intente reconectar mientras se
        # detiene a propósito (cambio de servicio, Mastodon a modo intervalo, etc.).
        self._watchdog_stop.set()
        with self._lock:
            self._detener_locked()
            self._account_id = None
            self._claves = ()
            self._gen += 1                    # invalida cualquier arranque en curso
            self._esperados = 0
            self._arranque_completado = False
            self._ultimo_dato = 0.0

    def _detener_locked(self):
        # IMPORTANTE: cerrar cada conexión SSE (h.close()) puede BLOQUEAR (espera de red / unión
        # del hilo lector). Como esto se llama desde el hilo de la INTERFAZ (al navegar por el
        # árbol o al colapsar el nodo de Mastodon), cerrar aquí dejaba la app sin responder unos
        # segundos. Se cierra en un hilo daemon de "usar y tirar": soltamos los handles y que se
        # cierren en segundo plano.
        handles = self._handles
        self._handles = []
        if handles:
            threading.Thread(target=self._cerrar_handles, args=(handles,), daemon=True).start()

    @staticmethod
    def _cerrar_handles(handles):
        _bajar_prioridad_hilo()
        for h in handles:
            try:
                h.close()
            except Exception:
                pass
