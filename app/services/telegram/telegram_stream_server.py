# -*- coding: utf-8 -*-
"""Servidor HTTP local para reproducir medios de Telegram EN STREAMING, sin descargar el archivo
entero primero.

Telegram no expone URLs http de sus adjuntos (van por MTProto), así que el reproductor (BASS) no
puede abrirlos como una URL normal. Aquí levantamos un servidor http minúsculo en 127.0.0.1 que, al
pedirle un adjunto, va sirviendo sus bytes desde `stream_media` (troceado). El reproductor abre
`http://127.0.0.1:PUERTO/s/<token>` con `BASS_StreamCreateURL` —el mismo camino ya probado para
radio/podcast— y empieza a sonar enseguida.

Solo escucha en el bucle local (127.0.0.1) y en un puerto efímero; no queda expuesto a la red.
"""
import logging
import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024  # trozo de 1 MB (el que entrega kurigram)
_MAX_TOKENS = 64       # tope de tokens vivos (LRU): un token viejo se revoca al superar el tope


class _StreamServer:
    def __init__(self):
        self._httpd = None
        self._port = 0
        self._lock = threading.Lock()
        # token -> (account_id, chat_id, message_id). OrderedDict = LRU: al pasar del tope, se
        # elimina el más antiguo (TG-009: los tokens ya no son eternos ni enumerables).
        self._reg = OrderedDict()

    def url_para(self, account_id: int, chat_id: int, message_id: int) -> str:
        """Registra un adjunto y devuelve la URL local para reproducirlo en streaming. El token es
        ALEATORIO de 256 bits (no enumerable) y el registro está acotado (LRU)."""
        self._asegurar_servidor()
        token = secrets.token_urlsafe(32)  # 256 bits: imposible de adivinar/enumerar
        with self._lock:
            self._reg[token] = (account_id, chat_id, message_id)
            self._reg.move_to_end(token)
            while len(self._reg) > _MAX_TOKENS:
                self._reg.popitem(last=False)  # revoca el token más antiguo
        return f"http://127.0.0.1:{self._port}/s/{token}"

    def revocar(self, url_o_token: str):
        """Revoca un token (o la URL que lo contiene) para que deje de servirse (al parar la
        reproducción). Silencioso si ya no existe."""
        token = (url_o_token or "").rsplit("/", 1)[-1]
        with self._lock:
            self._reg.pop(token, None)

    def _datos(self, token: str):
        with self._lock:
            return self._reg.get(token)

    def _asegurar_servidor(self):
        with self._lock:
            if self._httpd is not None:
                return
            servidor_ref = self

            class _Handler(BaseHTTPRequestHandler):
                # Silenciar el log por-petición de http.server (iría al stderr).
                def log_message(self, *a):
                    pass

                def do_GET(self):
                    servidor_ref._servir(self)

            self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self._port = self._httpd.server_address[1]
            threading.Thread(target=self._httpd.serve_forever, name="TelegramStream",
                             daemon=True).start()
            log.info(f"Servidor de streaming de Telegram escuchando en 127.0.0.1:{self._port}")

    def _servir(self, handler: BaseHTTPRequestHandler):
        token = handler.path.rsplit("/", 1)[-1]
        datos = self._datos(token)
        if not datos:
            handler.send_error(404)
            return
        account_id, chat_id, message_id = datos
        from app.services.telegram.telegram_service import telegram_service as ts
        try:
            size, mime = ts.preparar_stream(account_id, chat_id, message_id)
        except Exception as e:
            log.debug(f"No se pudo preparar el stream: {e}")
            handler.send_error(500)
            return
        if not size:
            handler.send_error(404)
            return

        # Range: el reproductor puede pedir un tramo (bytes=inicio-fin). Se respeta tanto el inicio
        # (seek) como el FIN si lo indica (antes se ignoraba el final del Range).
        inicio, fin = 0, size - 1
        rango = handler.headers.get("Range")
        es_rango = bool(rango and rango.startswith("bytes="))
        if es_rango:
            try:
                spec = rango.split("=", 1)[1].split(",", 1)[0]
                p_ini, _, p_fin = spec.partition("-")
                inicio = int(p_ini) if p_ini.strip() else 0
                if p_fin.strip():
                    fin = int(p_fin)
            except ValueError:
                inicio, fin = 0, size - 1
            # Range no satisfacible: 416 con Content-Range del tamaño real (RFC 7233).
            if inicio < 0 or inicio >= size:
                try:
                    handler.send_response(416)
                    handler.send_header("Content-Range", f"bytes */{size}")
                    handler.end_headers()
                except Exception:
                    pass
                return
            fin = max(inicio, min(fin, size - 1))

        longitud = fin - inicio + 1
        try:
            if es_rango:
                handler.send_response(206)
                handler.send_header("Content-Range", f"bytes {inicio}-{fin}/{size}")
            else:
                handler.send_response(200)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Type", mime or "application/octet-stream")
            handler.send_header("Content-Length", str(longitud))
            handler.end_headers()
        except Exception:
            return  # el reproductor cerró antes de tiempo

        offset_chunk = inicio // _CHUNK
        saltar = inicio % _CHUNK  # bytes a descartar del primer trozo
        restante = longitud       # nunca escribir más allá del fin pedido
        try:
            for chunk in ts.leer_chunks_stream(account_id, chat_id, message_id, offset_chunk):
                if saltar:
                    if saltar >= len(chunk):
                        saltar -= len(chunk)
                        continue
                    chunk = chunk[saltar:]
                    saltar = 0
                if len(chunk) > restante:
                    chunk = chunk[:restante]
                handler.wfile.write(chunk)
                restante -= len(chunk)
                if restante <= 0:
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Normal: el reproductor cerró la conexión (seek, cambio de pista o detener).
            pass
        except Exception as e:
            log.debug(f"Streaming interrumpido: {e}")

    def cerrar(self):
        with self._lock:
            self._reg.clear()  # revocar todos los tokens vivos al cerrar
            if self._httpd is not None:
                try:
                    self._httpd.shutdown()
                except Exception:
                    pass
                self._httpd = None


stream_server = _StreamServer()
