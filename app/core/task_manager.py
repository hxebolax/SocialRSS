# -*- coding: utf-8 -*-
"""
Módulo para el gestor de tareas en segundo plano.
Evita el bloqueo de la interfaz gráfica y se comunica de forma segura usando wx.CallAfter.
Soporta cancelación y notificación de progreso.
"""

import logging
import re
import threading
import time
import uuid
import concurrent.futures
from typing import Callable, Any, Optional, Dict
import wx
from .speech import speak
from .event_bus import event_bus

log = logging.getLogger(__name__)


class TareaDuplicadaError(Exception):
    """La tarea se descartó porque ya hay otra EQUIVALENTE en curso (misma unique_key).
    Es un aviso inocuo: el trabajo se está haciendo igualmente; el llamador solo debe
    limpiar su estado (flags de 'actualizando') y, como mucho, informar con suavidad."""


# Señales de que un fallo de una tarea es TRANSITORIO de red (culpa del servidor remoto o de la
# conexión, no de la app): estados HTTP 5xx / 429, tiempos de espera, cortes de conexión y los
# errores típicos de las librerías (Bluesky "UpstreamFailure", Mastodon "ratelimit"...).
_SENALES_TRANSITORIAS = (
    "upstreamfailure", "upstream service unreachable",
    "timed out", "timeout", "temporarily unavailable", "ratelimit", "rate limit",
    "connection reset", "connection aborted", "connection refused", "remotedisconnected",
    "max retries", "bad gateway", "service unavailable", "gateway timeout", "read timed out",
    # No-listo-todavía: una tarea de fondo se adelantó a que la cuenta terminara de conectar. Es
    # benigno (se conectará y al siguiente ciclo funciona), no un fallo que deba salir como ERROR.
    "no está conectad", "not connected", "not logged", "no está conectado",
)

# Códigos HTTP que indican fallo del OTRO extremo. Van por expresión regular y no por subcadena:
# buscar "500" suelto dentro del texto del error casaba con cualquier número que lo contuviera
# (p. ej. el value="5000" de una página HTML devuelta por un servidor mal configurado), y así un
# error real quedaba clasificado como "transitorio" y se ocultaba.
_RE_CODIGO_TRANSITORIO = re.compile(r"(?<!\d)(429|500|502|503|504)(?!\d)")


# El servidor contesta, pero NO con la API que esperamos: devuelve una página HTML (un portal de
# acceso, la pantalla de un panel de administración, un error del proxy inverso). No es un fallo de
# la app ni un bache de red: hay que arreglar el servidor. Como cada sondeo lo reproduce, se anota
# deduplicado y sin traza; si no, una sola cuenta mal configurada llena el registro.
_SENALES_SERVIDOR_NO_API = (
    "could not parse response as json",
    "<!doctype html",
)


def _es_error_transitorio(e: Exception) -> bool:
    txt = (str(e) or type(e).__name__).lower()
    if any(s in txt for s in _SENALES_TRANSITORIAS):
        return True
    return bool(_RE_CODIGO_TRANSITORIO.search(txt))


def _es_servidor_sin_api(e: Exception) -> bool:
    txt = (str(e) or type(e).__name__).lower()
    return any(s in txt for s in _SENALES_SERVIDOR_NO_API)


def _resumen_error(e: Exception) -> str:
    """Una línea corta (sin volcar dicts enormes de cabeceras): tipo + primeros ~200 caracteres."""
    txt = str(e) or type(e).__name__
    txt = " ".join(txt.split())          # colapsa saltos de línea y espacios
    return (txt[:200] + "…") if len(txt) > 200 else txt


# Errores YA DIAGNOSTICADOS que se repiten en cada sondeo (sesión caducada, servidor que no
# habla la API…): se anota el PRIMERO y luego solo uno cada media hora, con la cuenta de las
# veces que se ha repetido mientras tanto. Sin esto, una sola cuenta con la sesión caducada
# metía miles de trazas idénticas en el registro y tapaba los fallos de verdad.
_DEDUPE_SEGUNDOS = 1800
_ultimos_conocidos: Dict[str, list] = {}   # mensaje -> [momento del último apunte, repeticiones]
_dedupe_lock = threading.Lock()


def _registrar_error_conocido(mensaje: str):
    ahora = time.monotonic()
    with _dedupe_lock:
        estado = _ultimos_conocidos.get(mensaje)
        if estado is None:
            _ultimos_conocidos[mensaje] = [ahora, 0]
            log.warning(mensaje)
            return
        estado[1] += 1
        if ahora - estado[0] < _DEDUPE_SEGUNDOS:
            return
        repes, estado[0], estado[1] = estado[1], ahora, 0
    log.warning(f"{mensaje} (se ha repetido {repes} veces desde el aviso anterior)")


# Marca por-hilo para bajar la prioridad una sola vez por hilo del pool (se reutilizan).
_prio_local = threading.local()


def _bajar_prioridad_worker():
    """Baja la prioridad del HILO de trabajo actual a "inferior a la normal" en Windows.

    Las tareas de fondo suelen ser de red (liberan el GIL al esperar), pero algunas hacen trabajo de
    CPU que lo retiene: la PRIMERA importación de los SDK (atproto, pyrogram, mastodon) al arrancar,
    el parseo de HTML de los toots, etc. Si varios de esos hilos coinciden, le roban tiempo al hilo
    de la INTERFAZ y el lector de pantalla (NVDA/JAWS) se queda mudo o a tirones. Con los hilos de
    fondo en prioridad inferior, Windows da preferencia a la interfaz cuando el GIL queda libre: el
    trabajo de red no se nota (estaba esperando igualmente) y el de CPU solo tarda un poco más, pero
    la interfaz y el lector NUNCA se bloquean. Es un ajuste suave y sin riesgo (si falla, no pasa
    nada). Se hace una vez por hilo (el pool los reutiliza)."""
    if getattr(_prio_local, "hecho", False):
        return
    _prio_local.hecho = True
    try:
        import ctypes
        # THREAD_PRIORITY_BELOW_NORMAL = -1
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), -1)
    except Exception:
        pass


class TaskContext:
    """Contexto que se pasa a las tareas en segundo plano para controlar progreso y cancelación."""
    def __init__(self, task_id: str):
        self.id = task_id
        self.cancel_event = threading.Event()
        self.progress_callback: Optional[Callable[[int, str], None]] = None
        self.unique_key: Optional[str] = None  # clave de deduplicación (ver start_unique_task)

    def is_cancelled(self) -> bool:
        """Devuelve True si el usuario solicitó cancelar la tarea."""
        return self.cancel_event.is_set()

    def report_progress(self, percent: int, msg: str = ""):
        """Informa del progreso actual de la tarea (0-100)."""
        if self.progress_callback:
            # Asegurar que se llame en el hilo principal de wx
            wx.CallAfter(self.progress_callback, percent, msg)


class TaskManager:
    """Gestor central de tareas asíncronas con ThreadPoolExecutor."""
    
    def __init__(self, max_workers: int = 10):
        # Las tareas son de ENTRADA/SALIDA (esperar respuestas de red: Mastodon, Bluesky, RSS...),
        # no de cálculo. Durante esa espera Python LIBERA el GIL, así que más hilos = más sondeos
        # en paralelo SIN coste de CPU. Con varias redes sondeando a la vez (cada una + el sondeo
        # de fondo) 5 trabajadores podían quedarse cortos y retrasar una acción del usuario; 10 da
        # margen. (multiprocessing NO ayuda aquí: es para cálculo intensivo, no para esperar red,
        # y obligaría a serializar datos entre procesos. Igual que TWBlue, usamos hilos.)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="BgWorker"
        )
        self._active_tasks: Dict[str, TaskContext] = {}
        # Deduplicación: clave lógica -> task_id activo. Evita lanzar dos veces el MISMO trabajo
        # (mismo feed, misma cuenta/buffer) cuando un sondeo de fondo y un refresco manual (F5)
        # coinciden, o cuando un timer se solapa con el anterior. Ver start_unique_task().
        self._unique_keys: Dict[str, str] = {}
        self._lock = threading.Lock()
        # True cuando la aplicación está cerrándose: no se aceptan tareas nuevas.
        self._cerrando = False

    def start_task(
        self,
        func: Callable[..., Any],
        *args,
        callback: Optional[Callable[[Any, Optional[Exception]], None]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        success_sound: Optional[str] = None,
        error_sound: Optional[str] = None,
        success_msg: Optional[str] = None,
        error_msg: Optional[str] = None,
        pass_context: bool = True,
        unique_key: Optional[str] = None,
        **kwargs
    ) -> Optional[str]:
        """
        Ejecuta una función en segundo plano.
        
        Parámetros:
            func: Función a ejecutar.
            *args: Argumentos posicionales para la función.
            callback: Función a invocar al finalizar: callback(resultado, excepcion).
            progress_callback: Función a invocar para reportar progreso: progress_callback(porcentaje, mensaje).
            success_sound: Nombre del sonido a reproducir si tiene éxito.
            error_sound: Nombre del sonido a reproducir si falla.
            success_msg: Mensaje a verbalizar por lector en caso de éxito.
            error_msg: Mensaje a verbalizar por lector en caso de fallo.
            pass_context: Si es True, pasa el objeto TaskContext como primer argumento a func.
            unique_key: Si se indica y YA hay una tarea activa con esa misma clave, NO se lanza otra
                (devuelve None). Sirve para no duplicar el mismo trabajo (sondeo + F5, timers que se
                solapan). Ver también start_unique_task().
            **kwargs: Argumentos de palabra clave para la función.

        Retorna:
            ID único de la tarea creada (str), o None si se descartó por unique_key duplicada.
        """
        task_id = str(uuid.uuid4())
        ctx = TaskContext(task_id)
        ctx.progress_callback = progress_callback
        ctx.unique_key = unique_key

        with self._lock:
            if self._cerrando:
                # La aplicación está cerrándose: no arrancar trabajo nuevo.
                log.debug("Tarea rechazada: la aplicación está cerrándose.")
                return None
            if unique_key is not None and unique_key in self._unique_keys:
                # Ya hay una tarea equivalente en curso: no duplicar. PERO el llamador
                # NUNCA debe quedarse colgado esperando: muchos flujos ponen un flag
                # (p. ej. _updating=True) que solo se limpia en el callback, y si la
                # tarea se descartaba EN SILENCIO ese flag quedaba atascado y bloqueaba
                # el ciclo de actualización entero ("El sistema está ocupado
                # actualizando" y buffers que dejaban de refrescarse solos; fallo
                # reportado). Se avisa al callback con un error inocuo de "ya en curso".
                log.debug(f"Tarea duplicada descartada (key={unique_key}).")
                if callback is not None:
                    try:
                        wx.CallAfter(callback, None,
                                     TareaDuplicadaError("Esa actualización ya está en curso."))
                    except Exception:
                        pass
                return None
            self._active_tasks[task_id] = ctx
            if unique_key is not None:
                self._unique_keys[unique_key] = task_id

        # Lanzar en el pool de hilos
        self._executor.submit(
            self._run_wrapper,
            task_id,
            ctx,
            func,
            args,
            kwargs,
            callback,
            success_sound,
            error_sound,
            success_msg,
            error_msg,
            pass_context
        )
        return task_id

    def start_unique_task(self, key: str, func: Callable[..., Any], *args, **kwargs) -> Optional[str]:
        """Como start_task, pero NO lanza la tarea si ya hay otra activa con la misma 'key'.

        Ejemplos de clave: "rss:account:7", "feed:123", "mastodon:1:home". Devuelve el task_id o
        None si se descartó por estar ya en curso una equivalente."""
        return self.start_task(func, *args, unique_key=key, **kwargs)

    def tarea_activa(self, key: str) -> bool:
        """Indica si hay una tarea con esa clave de deduplicación en curso."""
        with self._lock:
            return key in self._unique_keys

    def shutdown(self, plazo: float = 3.0) -> bool:
        """Cierre coordinado del gestor: rechaza tareas nuevas, envía la señal de
        cancelación a todas las activas, descarta las encoladas que aún no empezaron
        y espera hasta `plazo` segundos a que las que corren terminen (drenaje).

        Devuelve True si todo terminó a tiempo; False si quedó alguna tarea viva
        (queda registrado cuáles, para diagnóstico)."""
        with self._lock:
            self._cerrando = True
            activos = list(self._active_tasks.values())
        for ctx in activos:
            ctx.cancel_event.set()
        # cancel_futures descarta lo encolado que aún no empezó; no espera a lo que corre.
        self._executor.shutdown(wait=False, cancel_futures=True)
        limite = time.monotonic() + max(0.0, plazo)
        while time.monotonic() < limite:
            with self._lock:
                if not self._active_tasks:
                    return True
            time.sleep(0.05)
        with self._lock:
            vivas = [ctx.unique_key or tid for tid, ctx in self._active_tasks.items()]
        if vivas and plazo > 0:
            log.warning(f"Cierre: {len(vivas)} tarea(s) no terminaron en el plazo: {vivas[:10]}")
        return not vivas

    def cancel_task(self, task_id: str) -> bool:
        """Intenta cancelar una tarea activa enviándole la señal de parada."""
        with self._lock:
            if task_id in self._active_tasks:
                self._active_tasks[task_id].cancel_event.set()
                log.info(f"Señal de cancelación enviada a tarea {task_id}")
                return True
        return False

    def _run_wrapper(
        self,
        task_id: str,
        ctx: TaskContext,
        func: Callable[..., Any],
        args: tuple,
        kwargs: dict,
        callback: Optional[Callable],
        success_sound: Optional[str],
        error_sound: Optional[str],
        success_msg: Optional[str],
        error_msg: Optional[str],
        pass_context: bool
    ):
        """Envuelve la ejecución de la tarea, captura errores y responde al hilo de UI."""
        # La interfaz y el lector de pantalla SIEMPRE tienen preferencia: este hilo de fondo va con
        # prioridad inferior para no congelar NVDA durante ráfagas de CPU (imports de SDK al arrancar,
        # parseo de HTML, etc.). Se hace una sola vez por hilo del pool.
        _bajar_prioridad_worker()
        result = None
        error = None

        try:
            if pass_context:
                result = func(ctx, *args, **kwargs)
            else:
                result = func(*args, **kwargs)
        except Exception as e:
            error = e
            # Los fallos TRANSITORIOS de red (el servidor del OTRO extremo caído unos segundos, un
            # límite de peticiones, un tiempo de espera) NO son un error de la app: se registran en UNA
            # línea WARNING legible, sin la traza de 30 líneas que asustaba en el log. El resto sí va a
            # ERROR con traza para poder diagnosticar.
            if getattr(e, "sin_traza", False) or _es_servidor_sin_api(e):
                # Fallo ya diagnosticado que se repite en cada sondeo: una línea, sin traza y
                # sin repetirla (ver _registrar_error_conocido). El id de tarea NO entra en el
                # mensaje: cambia en cada intento y rompería la deduplicación.
                _registrar_error_conocido(_resumen_error(e))
            elif _es_error_transitorio(e):
                log.warning(f"Tarea {task_id}: fallo transitorio (se reintentará): {_resumen_error(e)}")
            else:
                log.error(f"Error al ejecutar tarea {task_id}: {e}", exc_info=True)
            
        # Eliminar de las tareas activas de forma segura (y liberar su clave de deduplicación).
        with self._lock:
            if task_id in self._active_tasks:
                del self._active_tasks[task_id]
            if ctx.unique_key is not None and self._unique_keys.get(ctx.unique_key) == task_id:
                del self._unique_keys[ctx.unique_key]
                
        # Si la tarea fue cancelada y no arrojó error previo, marcar como tal
        if ctx.is_cancelled() and not error:
            error = Exception("Tarea cancelada por el usuario.")
            
        # Ejecutar callbacks y notificaciones en el hilo principal de wxPython. Durante el
        # cierre, el bucle de wx puede ya no existir: no debe tumbar el hilo de trabajo.
        try:
            wx.CallAfter(
                self._finish_task,
                result,
                error,
                callback,
                success_sound,
                error_sound,
                success_msg,
                error_msg
            )
        except Exception:
            if not self._cerrando:
                raise

    def _finish_task(
        self,
        result: Any,
        error: Optional[Exception],
        callback: Optional[Callable],
        success_sound: Optional[str],
        error_sound: Optional[str],
        success_msg: Optional[str],
        error_msg: Optional[str]
    ):
        """Finaliza la tarea ejecutando sonidos, avisos verbales y el callback del usuario."""
        # 1. Notificaciones sonoras y verbales automáticas
        if error:
            if error_sound:
                event_bus.emit("sound_trigger", error_sound)
            if error_msg:
                # Si se especifica mensaje, se verbaliza incluyendo el error
                speak(f"{error_msg}: {str(error)}")
            elif error_sound:
                # Fallback verbal básico si falla y hay sonido
                speak(f"Error en operación: {str(error)}")
        else:
            if success_sound:
                event_bus.emit("sound_trigger", success_sound)
            if success_msg:
                speak(success_msg)
                
        # 2. Ejecutar callback del usuario si existe
        if callback:
            try:
                callback(result, error)
            except Exception as e:
                log.error(f"Error al ejecutar callback de finalización de tarea: {e}", exc_info=True)

# Instancia global del gestor de tareas
task_manager = TaskManager()
