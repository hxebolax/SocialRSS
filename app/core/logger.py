# -*- coding: utf-8 -*-
"""
Módulo para el registro de logs con rotación de archivos.
Sanitiza automáticamente cualquier información sensible (claves API, tokens, contraseñas).
"""

import os
import sys
import glob
import logging
import logging.handlers
import re
from datetime import datetime
from .paths import get_data_path

# Número de archivos de log por sesión que se conservan (se borran los más viejos).
_MAX_SESIONES = 5

# TOPE de tamaño por sesión. Una sesión de varios días con el nivel en DEBUG llegó a 150 MB de
# .log + 200 MB de .jsonl (2026-11-03). Al llegar al tope, el archivo pasa a session_X.log.1 y se
# empieza otro; se guardan 2 partes antiguas: como mucho ~30 MB de texto por sesión.
_MAX_BYTES_LOG = 10 * 1024 * 1024
_PARTES_LOG = 2
# El .jsonl es para analizar problemas a distancia: solo WARNING o más, con su propio tope.
_MAX_BYTES_JSONL = 5 * 1024 * 1024

# Escritura ASÍNCRONA: los hilos (también el de la interfaz) solo dejan el registro en una cola y
# un hilo propio lo escribe en disco. Antes cada línea se escribía en el acto bajo el cerrojo del
# manejador: con el log en DEBUG y un disco lento, el hilo de la interfaz podía quedarse esperando
# a que otro hilo terminara de escribir.
_escritor = None


def _limpiar_logs_antiguos(logs_dir, conservar: int = _MAX_SESIONES):
    """Deja solo las 'conservar' sesiones más recientes (con TODAS sus partes: .log, .log.1,
    .jsonl...) y borra el resto para que la carpeta de logs no crezca sin control."""
    try:
        principales = glob.glob(os.path.join(str(logs_dir), "session_*.log"))
        principales.sort(key=lambda p: os.path.getmtime(p))  # más antiguos primero
        vivas = {os.path.basename(p)[:-len(".log")] for p in principales[-conservar:]}
        for p in glob.glob(os.path.join(str(logs_dir), "session_*")):
            nombre = os.path.basename(p)
            sesion = nombre.split(".", 1)[0]
            if sesion not in vivas:
                try:
                    os.remove(p)
                except Exception:
                    pass
    except Exception:
        pass


def vaciar(timeout: float = 2.0):
    """Escribe en disco lo que quede en la cola del registro. Llamar justo antes de os._exit
    (que no ejecuta atexit): sin esto se perderían las últimas líneas de la sesión."""
    escritor = _escritor
    if escritor is None:
        return
    try:
        hilo = getattr(escritor, "_thread", None)
        if hilo is None:
            return
        escritor.enqueue_sentinel()
        hilo.join(timeout)
        if not hilo.is_alive():
            escritor._thread = None
    except Exception:
        pass

# Expresión regular para detectar términos sensibles comunes
RE_SENSITIVE = re.compile(
    r"(token|secret|password|key|contraseña|clave|api_key|oauth_token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.\=\+]+)['\"]?",
    re.IGNORECASE
)

class SanitizingFilter(logging.Filter):
    """Filtro que oculta información sensible de los mensajes de log."""
    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = self.sanitize(record.msg)
        return True

    @staticmethod
    def sanitize(text: str) -> str:
        def replace_match(match):
            key = match.group(1)
            return f"{key}: [REDACTADO]"
        return RE_SENSITIVE.sub(replace_match, text)

def configurar_logging(debug_mode: bool = False):
    """Configura el sistema de logs global, con un archivo por sesión.

    Cada arranque crea un log nuevo con la fecha y hora en el nombre. Se conservan
    solo los 5 más recientes (de cada formato); los anteriores se borran. Así
    siempre quedan los 5 registros más nuevos y la carpeta no se llena.
    """
    logs_dir = get_data_path("logs")
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Borrar los logs del esquema antiguo (un único app.log/app.jsonl que crecía
    # sin parar), si existen, para no dejar archivos enormes huérfanos.
    for legado in ("app.log", "app.jsonl"):
        ruta_legado = logs_dir / legado
        if ruta_legado.exists():
            try:
                ruta_legado.unlink()
            except Exception:
                pass

    sesion = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"session_{sesion}.log"
    json_file = logs_dir / f"session_{sesion}.jsonl"

    # Nivel de log: el modo debug fuerza DEBUG; si no, se respeta la preferencia.
    if debug_mode:
        level = logging.DEBUG
    else:
        try:
            from .config_manager import config
            level = getattr(logging, str(config.get("general", "log_level", "INFO")).upper(), logging.INFO)
        except Exception:
            level = logging.INFO
    
    # Formateador
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] - %(message)s"
    )
    
    # Manejador del log de esta sesión (un archivo nuevo por arranque, con tope de tamaño).
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES_LOG, backupCount=_PARTES_LOG, encoding="utf-8")
    handler.setFormatter(formatter)
    
    # Aplicar filtro de sanitización (corre en el hilo escritor, no en el que registra)
    handler.addFilter(SanitizingFilter())
    manejadores = [handler]
    
    # Configurar logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Limpiar manejadores previos (evitar duplicados si se vuelve a configurar)
    global _escritor
    if _escritor is not None:
        vaciar()
        _escritor = None
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    # Manejador adicional en formato JSONL (un objeto JSON por línea) para
    # facilitar el análisis remoto cuando el usuario envía sus logs.
    try:
        import json as _json

        class _JsonFormatter(logging.Formatter):
            def format(self, record):
                record.msg = SanitizingFilter.sanitize(str(record.msg))
                datos = {
                    "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                    "nivel": record.levelname,
                    "modulo": record.name,
                    "linea": record.lineno,
                    "mensaje": record.getMessage(),
                }
                return _json.dumps(datos, ensure_ascii=False)

        json_handler = logging.handlers.RotatingFileHandler(
            json_file, maxBytes=_MAX_BYTES_JSONL, backupCount=1, encoding="utf-8", delay=True)
        json_handler.setFormatter(_JsonFormatter())
        json_handler.setLevel(logging.WARNING)
        manejadores.append(json_handler)
    except Exception:
        pass

    # Cola + hilo escritor (respect_handler_level: el .jsonl conserva su nivel WARNING).
    try:
        import queue as _queue
        cola = _queue.SimpleQueue()
        _escritor = logging.handlers.QueueListener(cola, *manejadores, respect_handler_level=True)
        _escritor.start()
        try:
            _escritor._thread.name = "log-writer"
        except Exception:
            pass
        root_logger.addHandler(logging.handlers.QueueHandler(cola))
        import atexit
        atexit.register(vaciar)
    except Exception:
        _escritor = None
        for h in manejadores:
            root_logger.addHandler(h)

    # Conservar solo las 5 sesiones más recientes (con todas sus partes); borrar las
    # más antiguas. Se hace después de crear la de esta sesión, así que la actual
    # cuenta entre las 5 que se mantienen.
    _limpiar_logs_antiguos(logs_dir)

    # Silenciar el ruido de librerías de terceros muy verbosas. pyrogram (Telegram) emite MILES de
    # WARNING de propiedades obsoletas (forward_from, forward_from_chat, forward_sender_name...) cada
    # vez que se procesa un mensaje reenviado: inundan el log (varios miles por sesión) y cuestan E/S
    # sin aportar nada útil. Dejamos solo sus ERROR. (Salvo en modo debug, donde se quiere todo.)
    if not debug_mode:
        for _ruidoso in (
            "pyrogram",
            "pyrogram.types.messages_and_media.message",
            "pyrogram.types.user_and_chats.chat",
        ):
            try:
                logging.getLogger(_ruidoso).setLevel(logging.ERROR)
            except Exception:
                pass
        # httpx registra en INFO CADA petición HTTP (Bluesky/Mastodon/X sondean sin parar) y nio
        # emite en INFO cada operación de cifrado y cada evento de sala: juntos suman miles de líneas
        # por sesión sin valor de diagnóstico. Se dejan en WARNING (sus problemas reales siguen).
        for _ruidoso in ("httpx", "httpcore", "nio", "nio.crypto", "nio.crypto.log",
                         "nio.rooms", "nio.client", "nio.client.base_client", "nio.responses"):
            try:
                logging.getLogger(_ruidoso).setLevel(logging.WARNING)
            except Exception:
                pass

    # urllib3 (la capa HTTP de requests, que usa el lector de RSS) emite en DEBUG una línea por CADA
    # conexión y petición ("Starting new HTTPS connection", "GET ... 200"...). Con muchos feeds son
    # MILES de líneas por sesión que hinchan el log a varios MB (y ralentizan el visor). Casi nunca se
    # depura urllib3 en sí, así que se silencia a WARNING SIEMPRE, incluso en modo debug: para
    # diagnosticar RSS ya están los logs de la app (app.services.rss.*), que sí son útiles.
    # Además de urllib3, otras librerías vomitan DEBUG a raudales sin valor de diagnóstico para la
    # app: peewee (el ORM de slixmpp-omemo) registra CADA consulta SQL, y asyncio marca cada
    # socket/selector. Se silencian a WARNING SIEMPRE (incluso en debug) para que el registro no se
    # dispare a varios MB por sesión (lo que ralentizaba el visor y la app).
    # readability-lxml (extracción de texto completo de RSS) registra en DEBUG el análisis
    # de CADA nodo de CADA página ("readability score..."): con la cola de texto completo
    # llegó a meter 7.300+ líneas en una sesión (el 92% del registro), inflando el archivo
    # y ralentizando el equipo justo durante la carga (fallo reportado). bs4/lxml, igual.
    # trafilatura (+ htmldate, courlan, justext) hace lo mismo que readability: era el 30 % del
    # registro del 2026-11-03 ("extra in p: ..." por párrafo). websockets registra cada PING/PONG
    # del tiempo real de Mastodon (cada pocos segundos por cuenta) y aiosqlite cada operación.
    for _http in ("urllib3", "urllib3.connectionpool", "requests", "peewee", "asyncio",
                  "charset_normalizer", "readability", "readability.readability",
                  "bs4", "lxml", "yt_dlp", "trafilatura", "htmldate", "courlan", "justext",
                  "websockets", "websockets.client", "aiosqlite"):
        try:
            logging.getLogger(_http).setLevel(logging.WARNING)
        except Exception:
            pass

    # Añadir consola si no estamos congelados o si debug está activo
    # Pero para no interferir con lectores de pantalla en producción, solo en debug.
    if debug_mode and not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(SanitizingFilter())
        root_logger.addHandler(console)

    logging.info("Sistema de registro de eventos (logging) inicializado.")
