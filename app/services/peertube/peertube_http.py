# -*- coding: utf-8 -*-
"""Cliente HTTP de bajo nivel para la API REST de PeerTube.

`ClienteHTTP` envuelve un `httpx.Client` síncrono (encaja con `task_manager`) y aporta lo que exige
un cliente robusto de red: tiempos de espera, reintentos con backoff exponencial ante errores
temporales (5xx y fallos de red), respeto de `Retry-After` en los 429, validación TLS SIEMPRE
activada, inyección del `Authorization: Bearer` (mediante un proveedor de token que se consulta en
cada intento, para soportar la renovación de la fase de auth), paginación `start`/`count` y
cancelación cooperativa. Los errores se traducen a `PeerTubeServiceError` con un mensaje legible y
SIN filtrar el token ni las URLs firmadas.
"""
import time
import logging
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.core.errors import PeerTubeServiceError
from app.services.peertube.peertube_common import (
    API_BASE, TIMEOUT_CONEXION, TIMEOUT_LECTURA, TIMEOUT_ESCRITURA, TIMEOUT_POOL,
    REINTENTOS_DEFECTO, BACKOFF_BASE, ESPERA_MAXIMA,
    crear_cliente_httpx, httpx_excepciones, user_agent)

log = logging.getLogger(__name__)


class PeerTubeCancelado(PeerTubeServiceError):
    """La operación se canceló a petición del usuario (no es un error de red)."""


class ClienteHTTP:
    """Cliente REST de una instancia PeerTube (una instancia por cliente)."""

    def __init__(self, base_url: str, *, token_provider: Optional[Callable[[], Optional[str]]] = None,
                 verify: bool = True, reintentos: int = REINTENTOS_DEFECTO,
                 timeout: Any = None) -> None:
        """`base_url`: raíz de la instancia ("https://host"). `token_provider`: función SIN argumentos
        que devuelve el access token vigente (o None si anónimo); se llama en cada intento para que la
        renovación de token surta efecto sin recrear el cliente. `verify`: validación TLS (SIEMPRE
        True en producción; el parámetro existe solo para pruebas controladas)."""
        self._base = (base_url or "").rstrip("/")
        self._token_provider = token_provider
        self._verify = verify
        self._reintentos = max(0, int(reintentos))
        self._timeout = timeout
        self._client = None            # httpx.Client perezoso
        self._lock = threading.RLock()  # crear el cliente sin carreras entre hilos de fondo

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def _cliente(self):
        """Devuelve el `httpx.Client` subyacente, creándolo la primera vez (bajo lock)."""
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                httpx = httpx_excepciones()
                timeout = self._timeout
                if timeout is None and httpx is not None:
                    timeout = httpx.Timeout(connect=TIMEOUT_CONEXION, read=TIMEOUT_LECTURA,
                                            write=TIMEOUT_ESCRITURA, pool=TIMEOUT_POOL)
                self._client = crear_cliente_httpx(
                    base_url=self._base, verify=self._verify, timeout=timeout,
                    headers={"User-Agent": user_agent(), "Accept": "application/json"})
            return self._client

    def cerrar(self) -> None:
        """Cierra el cliente HTTP y libera las conexiones."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    # ------------------------------------------------------------------
    # Petición con reintentos
    # ------------------------------------------------------------------
    def _ejecutar_peticion(self, metodo: str, url: str, *, params, data, json_body, files, headers,
                           auth: bool, max_intentos: int,
                           cancelar: Optional[threading.Event], content: Any = None) -> Any:
        """Bucle de petición con reintentos: devuelve el `Response` FINAL (sea cual sea su código,
        tras reintentar los temporales 5xx/429/red hasta agotar `max_intentos`). Lanza en fallo de
        red definitivo o cancelación. No interpreta el cuerpo: eso lo hacen `solicitar`/`solicitar_cruda`."""
        httpx = httpx_excepciones()
        if httpx is None:  # pragma: no cover - entorno sin httpx
            raise PeerTubeServiceError("httpx no está disponible: PeerTube no puede funcionar.")
        intento = 0
        while True:
            self._abortar_si_cancelado(cancelar)
            cab = dict(headers or {})
            if auth:
                token = self._token_actual()
                if token:
                    cab["Authorization"] = f"Bearer {token}"
            try:
                resp = self._cliente().request(metodo.upper(), url, params=params, data=data,
                                               json=json_body, files=files, content=content,
                                               headers=cab)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                # Error de red/tiempo: reintentar con backoff si quedan intentos.
                if intento < max_intentos:
                    self._esperar(self._backoff(intento), cancelar)
                    intento += 1
                    continue
                raise PeerTubeServiceError(self._mensaje_red(e)) from None
            codigo = resp.status_code
            if codigo == 429 and intento < max_intentos:
                self._esperar(self._retry_after(resp, intento), cancelar)
                intento += 1
                continue
            if 500 <= codigo < 600 and intento < max_intentos:
                self._esperar(self._backoff(intento), cancelar)
                intento += 1
                continue
            return resp

    def solicitar(self, metodo: str, path: str, *, params: Optional[dict] = None,
                  data: Optional[dict] = None, json_body: Any = None, files: Any = None,
                  content: Any = None, headers: Optional[dict] = None, auth: bool = True,
                  reintentos: Optional[int] = None, espera_json: bool = True,
                  cancelar: Optional[threading.Event] = None) -> Any:
        """Ejecuta una petición y devuelve el JSON parseado (o el objeto `Response` si
        `espera_json=False`). Reintenta los errores temporales (5xx y red) y respeta `Retry-After` en
        los 429. Lanza `PeerTubeServiceError` en fallo definitivo y `PeerTubeCancelado` si se cancela.

        `path` puede ser absoluto ("/api/v1/...") o relativo a la API ("videos" → "/api/v1/videos").
        `content` envía un cuerpo binario crudo (subida por chunks); `files` envía multipart."""
        url = self._construir_path(path)
        max_intentos = self._reintentos if reintentos is None else max(0, int(reintentos))
        resp = self._ejecutar_peticion(metodo, url, params=params, data=data, json_body=json_body,
                                       files=files, content=content, headers=headers, auth=auth,
                                       max_intentos=max_intentos, cancelar=cancelar)
        if 200 <= resp.status_code < 300:
            return self._parsear(resp, espera_json)
        raise PeerTubeServiceError(self._mensaje_error(resp))

    def solicitar_cruda(self, metodo: str, path: str, *, params: Optional[dict] = None,
                        data: Optional[dict] = None, json_body: Any = None, files: Any = None,
                        content: Any = None, headers: Optional[dict] = None, auth: bool = True,
                        reintentos: Optional[int] = None,
                        cancelar: Optional[threading.Event] = None) -> Any:
        """Como `solicitar`, pero devuelve el `Response` CRUDO sin lanzar en 4xx: el llamante inspecciona
        el código y el cuerpo/cabeceras (p. ej. el login `missing_two_factor` o la subida reanudable,
        que lee `Location`/`Range`). Sigue reintentando los temporales y lanzando en fallo de red."""
        url = self._construir_path(path)
        max_intentos = self._reintentos if reintentos is None else max(0, int(reintentos))
        return self._ejecutar_peticion(metodo, url, params=params, data=data, json_body=json_body,
                                       files=files, content=content, headers=headers, auth=auth,
                                       max_intentos=max_intentos, cancelar=cancelar)

    # Atajos por método -------------------------------------------------
    def get(self, path: str, **kw) -> Any:
        return self.solicitar("GET", path, **kw)

    def post(self, path: str, **kw) -> Any:
        return self.solicitar("POST", path, **kw)

    def put(self, path: str, **kw) -> Any:
        return self.solicitar("PUT", path, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self.solicitar("DELETE", path, **kw)

    # ------------------------------------------------------------------
    # Paginación (start/count → {total, data})
    # ------------------------------------------------------------------
    def paginar(self, path: str, *, params: Optional[dict] = None, count: int = 25,
                limite: Optional[int] = None, auth: bool = True,
                cancelar: Optional[threading.Event] = None) -> Iterator[Any]:
        """Itera TODOS los elementos de un listado paginado de PeerTube (`start`/`count`, respuesta
        `{total, data:[...]}`), pidiendo páginas bajo demanda. `limite` corta tras N elementos."""
        base = dict(params or {})
        start = int(base.get("start", 0) or 0)
        entregados = 0
        while True:
            self._abortar_si_cancelado(cancelar)
            pagina = dict(base)
            pagina["start"] = start
            pagina["count"] = count
            resp = self.solicitar("GET", path, params=pagina, auth=auth, cancelar=cancelar)
            data, total = self._extraer_pagina(resp)
            if not data:
                return
            for item in data:
                yield item
                entregados += 1
                if limite is not None and entregados >= limite:
                    return
            start += len(data)
            if total is not None and start >= total:
                return
            # Salvaguarda: si la instancia devuelve menos de lo pedido, no hay más páginas.
            if len(data) < count:
                return

    def pagina(self, path: str, *, params: Optional[dict] = None, start: int = 0, count: int = 25,
               auth: bool = True, cancelar: Optional[threading.Event] = None) -> tuple:
        """Pide UNA página y devuelve `(data: list, total: int|None)`."""
        p = dict(params or {})
        p["start"] = start
        p["count"] = count
        resp = self.solicitar("GET", path, params=p, auth=auth, cancelar=cancelar)
        return self._extraer_pagina(resp)

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    def _construir_path(self, path: str) -> str:
        """Normaliza el path: absoluto se respeta; relativo se cuelga de la API (`/api/v1`)."""
        if not path:
            return API_BASE
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith("/"):
            return path
        return f"{API_BASE}/{path}"

    def _token_actual(self) -> Optional[str]:
        if not self._token_provider:
            return None
        try:
            return self._token_provider()
        except Exception:
            return None

    @staticmethod
    def _extraer_pagina(resp: Any) -> tuple:
        """De una respuesta de listado, devuelve `(data, total)`. Acepta `{total, data}` o una lista
        pelada (algunos endpoints devuelven la lista directamente)."""
        if isinstance(resp, dict):
            data = resp.get("data")
            if isinstance(data, list):
                total = resp.get("total")
                return data, (int(total) if isinstance(total, int) else None)
            return [], None
        if isinstance(resp, list):
            return resp, None
        return [], None

    @staticmethod
    def _parsear(resp: Any, espera_json: bool) -> Any:
        """Devuelve el JSON del cuerpo si se espera y el tipo es JSON; si no, el propio Response.
        Un 204 sin contenido devuelve None."""
        if not espera_json:
            return resp
        if resp.status_code == 204 or not resp.content:
            return None
        tipo = (resp.headers.get("content-type") or "").lower()
        if "json" in tipo:
            try:
                return resp.json()
            except Exception:
                raise PeerTubeServiceError("La instancia devolvió una respuesta JSON no válida.")
        # No es JSON (p. ej. HTML de un proxy/error): no exponer el cuerpo crudo.
        raise PeerTubeServiceError("La instancia devolvió una respuesta inesperada (no JSON).")

    def _backoff(self, intento: int) -> float:
        """Backoff exponencial acotado: BASE * 2^intento, con techo ESPERA_MAXIMA."""
        return min(ESPERA_MAXIMA, BACKOFF_BASE * (2 ** intento))

    def _retry_after(self, resp: Any, intento: int) -> float:
        """Segundos a esperar ante un 429: usa `Retry-After` si es un entero; si no, el backoff."""
        cab = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        if cab:
            try:
                return min(ESPERA_MAXIMA, max(0.0, float(int(cab))))
            except (TypeError, ValueError):
                pass  # formato de fecha HTTP: caemos al backoff
        return self._backoff(intento)

    @staticmethod
    def _esperar(segundos: float, cancelar: Optional[threading.Event]) -> None:
        """Espera troceada que atiende la cancelación (no bloquea de golpe)."""
        restante = max(0.0, float(segundos))
        while restante > 0:
            if cancelar is not None and cancelar.is_set():
                raise PeerTubeCancelado("La operación se canceló.")
            paso = min(0.2, restante)
            time.sleep(paso)
            restante -= paso

    @staticmethod
    def _abortar_si_cancelado(cancelar: Optional[threading.Event]) -> None:
        if cancelar is not None and cancelar.is_set():
            raise PeerTubeCancelado("La operación se canceló.")

    @staticmethod
    def _mensaje_red(exc: Exception) -> str:
        """Mensaje legible para un fallo de red/tiempo (sin URLs ni credenciales)."""
        httpx = httpx_excepciones()
        if httpx is not None and isinstance(exc, httpx.TimeoutException):
            return "La instancia tardó demasiado en responder. Comprueba tu conexión e inténtalo de nuevo."
        return ("No se pudo conectar con la instancia de PeerTube. Revisa la URL y tu conexión, e "
                "inténtalo de nuevo.")

    @staticmethod
    def _mensaje_error(resp: Any) -> str:
        """Mensaje legible para un código de error HTTP, extrayendo el detalle del cuerpo de PeerTube
        (`{error|detail|code}`) cuando sea JSON. Nunca incluye cabeceras ni el token."""
        codigo = resp.status_code
        detalle = ""
        try:
            tipo = (resp.headers.get("content-type") or "").lower()
            if "json" in tipo and resp.content:
                cuerpo = resp.json()
                if isinstance(cuerpo, dict):
                    detalle = str(cuerpo.get("error") or cuerpo.get("detail")
                                  or cuerpo.get("message") or "").strip()
        except Exception:
            detalle = ""
        base = {
            400: "Solicitud incorrecta.",
            401: "Sesión no válida o caducada. Vuelve a iniciar sesión.",
            403: "No tienes permisos para esta operación.",
            404: "El recurso no existe o ya no está disponible.",
            409: "Conflicto con el estado actual del recurso.",
            413: "El archivo o la petición son demasiado grandes para esta instancia.",
            429: "La instancia está limitando las peticiones. Espera un momento e inténtalo de nuevo.",
        }.get(codigo)
        if base is None:
            if 500 <= codigo < 600:
                base = "La instancia tuvo un error interno. Inténtalo más tarde."
            else:
                base = f"La instancia devolvió un error (HTTP {codigo})."
        return f"{base} {detalle}".strip() if detalle else base
