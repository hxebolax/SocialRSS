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


def _limpiar_logs_antiguos(patron: str, conservar: int = _MAX_SESIONES):
    """Deja solo los 'conservar' archivos más recientes que casen con el patrón.

    Borra los más antiguos para que la carpeta de logs no crezca sin control.
    """
    try:
        archivos = glob.glob(patron)
        archivos.sort(key=lambda p: os.path.getmtime(p))  # más antiguos primero
        if len(archivos) > conservar:
            for p in archivos[:-conservar]:
                try:
                    os.remove(p)
                except Exception:
                    pass
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
    
    # Manejador del log de esta sesión (un archivo nuevo por arranque).
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(formatter)
    
    # Aplicar filtro de sanitización
    handler.addFilter(SanitizingFilter())
    
    # Configurar logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Limpiar manejadores previos (evitar duplicados si se vuelve a configurar)
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
        
    root_logger.addHandler(handler)

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

        json_handler = logging.FileHandler(json_file, encoding="utf-8")
        json_handler.setFormatter(_JsonFormatter())
        root_logger.addHandler(json_handler)
    except Exception:
        pass

    # Conservar solo los 5 logs de sesión más recientes de cada formato; borrar
    # los más antiguos. Se hace después de crear los de esta sesión, así que el
    # actual cuenta entre los 5 que se mantienen.
    _limpiar_logs_antiguos(str(logs_dir / "session_*.log"))
    _limpiar_logs_antiguos(str(logs_dir / "session_*.jsonl"))

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
    for _http in ("urllib3", "urllib3.connectionpool", "requests", "peewee", "asyncio",
                  "charset_normalizer", "readability", "readability.readability",
                  "bs4", "lxml", "yt_dlp"):
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
