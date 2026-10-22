# -*- coding: utf-8 -*-
"""Servidor HTTP local con soporte de `Range` para reproducir vídeos de PeerTube con BassKit.

BASS abre bien una URL http, pero algunos CDN de PeerTube exigen un User-Agent de navegador o
entregan mejor los rangos si el cliente los pide correctamente. Este servidor local
(`127.0.0.1:puerto`) actúa de **proxy con Range**: registra la URL remota bajo un token aleatorio y,
cuando BASS pide un rango, lo reenvía al CDN con UA de navegador y devuelve un 206 con el mismo
`Content-Range`. Así se obtiene **seek real sin descargar el vídeo entero** y se resuelve el UA.

Modelado sobre `telegram_stream_server.py`, pero la fuente aquí es una URL http remota (no MTProto).
"""
import logging
import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple

from app.services.peertube.peertube_common import crear_cliente_httpx

log = logging.getLogger(__name__)

# User-Agent de navegador para los CDN que rechazan clientes desconocidos.
UA_NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
# Tope de fuentes registradas simultáneamente (LRU).
_MAX_FUENTES = 64
_TAM_CHUNK = 1024 * 256  # 256 KB por trozo al reenviar


class ServidorStreamPeerTube:
    """Proxy HTTP local con Range para servir vídeos remotos a BASS. Singleton perezoso."""

    def __init__(self) -> None:
        self._servidor: Optional[ThreadingHTTPServer] = None
        self._puerto = 0
        self._fuentes: "OrderedDict[str, Dict[str, str]]" = OrderedDict()
        self._lock = threading.Lock()
        self._cliente = None  # httpx.Client perezoso para las peticiones al CDN

    # ------------------------------------------------------------------
    def _asegurar_servidor(self) -> None:
        if self._servidor is not None:
            return
        with self._lock:
            if self._servidor is not None:
                return
            handler = _crear_handler(self)
            self._servidor = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self._puerto = self._servidor.server_address[1]
            hilo = threading.Thread(target=self._servidor.serve_forever,
                                    name="PeerTubeStream", daemon=True)
            hilo.start()
            log.debug(f"Servidor de streaming de PeerTube en 127.0.0.1:{self._puerto}")

    def _cliente_http(self):
        if self._cliente is None:
            with self._lock:
                if self._cliente is None:
                    self._cliente = crear_cliente_httpx(timeout=30.0)
        return self._cliente

    def url_para(self, remote_url: str, *, extra_headers: Optional[dict] = None) -> str:
        """Registra una URL remota y devuelve la URL local (127.0.0.1) que BASS debe reproducir."""
        self._asegurar_servidor()
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._fuentes[token] = {"url": remote_url}
            self._fuentes.move_to_end(token)
            while len(self._fuentes) > _MAX_FUENTES:
                self._fuentes.popitem(last=False)
        return f"http://127.0.0.1:{self._puerto}/s/{token}"

    def _url_de(self, token: str) -> Optional[str]:
        with self._lock:
            fuente = self._fuentes.get(token)
            if fuente:
                self._fuentes.move_to_end(token)
                return fuente["url"]
        return None

    def cerrar(self) -> None:
        with self._lock:
            if self._servidor is not None:
                try:
                    self._servidor.shutdown()
                except Exception:
                    pass
                self._servidor = None
            if self._cliente is not None:
                try:
                    self._cliente.close()
                except Exception:
                    pass
                self._cliente = None
            self._fuentes.clear()


def _crear_handler(servidor: "ServidorStreamPeerTube"):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silenciar el log de acceso
            pass

        def do_GET(self):
            if not self.path.startswith("/s/"):
                self.send_error(404)
                return
            token = self.path[3:].split("?", 1)[0]
            remote_url = servidor._url_de(token)
            if not remote_url:
                self.send_error(404)
                return
            cabeceras = {"User-Agent": UA_NAVEGADOR}
            rango = self.headers.get("Range")
            if rango:
                cabeceras["Range"] = rango
            try:
                cliente = servidor._cliente_http()
                with cliente.stream("GET", remote_url, headers=cabeceras) as r:
                    self.send_response(r.status_code)
                    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                        val = r.headers.get(h)
                        if val:
                            self.send_header(h, val)
                    self.end_headers()
                    for chunk in r.iter_bytes(_TAM_CHUNK):
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            return  # el reproductor hizo seek o paró: no es un error
            except Exception as e:
                log.debug(f"Stream PeerTube: fallo al servir el rango: {e}")
                try:
                    self.send_error(502)
                except Exception:
                    pass

    return _Handler


# Singleton global.
stream_peertube = ServidorStreamPeerTube()
