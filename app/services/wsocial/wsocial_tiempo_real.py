# -*- coding: utf-8 -*-
"""Tiempo real de W Social: novedades sin esperar al siguiente sondeo.

AT Protocol publica un flujo de eventos de toda la red. Escucharlo entero sería absurdo en un
cliente de escritorio (son miles de eventos por segundo), así que aquí se hacen las dos cosas que
sí tienen sentido y no penalizan al resto de la aplicación:

- **Sondeo ligero e incremental** del buffer que el usuario tiene delante y de sus contadores.
- **Vigilancia del chat** con `getLog`, que devuelve solo lo ocurrido desde el último cursor.

Todo corre en hilos de fondo con prioridad baja (regla del proyecto: el lector de pantalla nunca
se queda esperando) y cada red mantiene su propio temporizador, de modo que activar W Social no
retrasa ni interrumpe el tiempo real de Mastodon, Telegram o Bluesky.
"""
import threading
import time

from app.services.wsocial.wsocial_common import log, config


class WSocialTiempoReal:
    """Vigilante de novedades de una cuenta de W Social.

    No mantiene conexiones abiertas: pregunta cada cierto tiempo y solo por lo que ha cambiado.
    Se puede parar en cualquier momento y no deja hilos vivos al cerrar la aplicación."""

    def __init__(self, servicio, account_id: int, al_haber_novedades=None, al_llegar_mensaje=None):
        self._servicio = servicio
        self.account_id = account_id
        self._al_haber_novedades = al_haber_novedades      # callback(nº notificaciones sin leer)
        self._al_llegar_mensaje = al_llegar_mensaje        # callback(evento de chat)
        self._hilo = None
        self._parar = threading.Event()
        self._cursor_chat = ""
        self._ultimo_error = 0.0

    # ------------------------------------------------------------------
    def iniciar(self, intervalo: int = 60):
        """Arranca la vigilancia. Idempotente: si ya está en marcha, no hace nada."""
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(
            target=self._bucle, args=(max(20, int(intervalo)),),
            name=f"wsocial-tiempo-real-{self.account_id}", daemon=True)
        self._hilo.start()
        log.info(f"W Social: vigilancia de novedades iniciada (cuenta {self.account_id}).")

    def detener(self):
        """Pide la parada y espera un momento a que el hilo termine su ciclo."""
        self._parar.set()
        hilo = self._hilo
        if hilo is not None and hilo.is_alive():
            hilo.join(timeout=3)
        self._hilo = None

    @property
    def activo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    # ------------------------------------------------------------------
    def _bucle(self, intervalo: int):
        """Ciclo de vigilancia. Cualquier fallo se registra y se sigue: un corte de red no debe
        matar el hilo ni llenar el registro de repeticiones."""
        self._bajar_prioridad()
        while not self._parar.is_set():
            try:
                self._comprobar_notificaciones()
                self._comprobar_chat()
            except Exception as e:
                # Se registra como mucho una vez por minuto para no inundar el archivo.
                ahora = time.time()
                if ahora - self._ultimo_error > 60:
                    self._ultimo_error = ahora
                    log.debug(f"W Social: fallo en la vigilancia de novedades: {e}")
            # Espera troceada para poder parar enseguida al cerrar la aplicación.
            self._parar.wait(intervalo)

    @staticmethod
    def _bajar_prioridad():
        """Prioridad de Windows por debajo de lo normal: los hilos de fondo nunca compiten con la
        interfaz ni con el lector de pantalla."""
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass

    def _comprobar_notificaciones(self):
        """Consulta el contador de notificaciones sin leer (una petición muy barata) y avisa solo
        si ha cambiado respecto a la última vez."""
        if not self._al_haber_novedades:
            return
        servicio = self._servicio
        try:
            client = servicio.obtener_cliente(self.account_id)
            resp = client.app.bsky.notification.get_unread_count()
            n = getattr(resp, "count", 0) or 0
        except Exception:
            return
        anterior = getattr(self, "_ultimo_conteo", None)
        self._ultimo_conteo = n
        if anterior is not None and n > anterior:
            try:
                self._al_haber_novedades(n)
            except Exception as e:
                log.debug(f"W Social: el aviso de novedades falló: {e}")

    def _comprobar_chat(self):
        """Pide al chat solo lo ocurrido desde el último cursor. Si llega algún mensaje nuevo de
        otra persona, avisa una vez por ciclo."""
        if not self._al_llegar_mensaje:
            return
        if not config.get("wsocial", "avisar_mensajes", True):
            return
        servicio = self._servicio
        try:
            chat = servicio._chat_capaz(self.account_id)
            resp = chat.chat.bsky.convo.get_log({"cursor": self._cursor_chat or None})
        except Exception:
            return
        self._cursor_chat = getattr(resp, "cursor", "") or self._cursor_chat
        mi_did = (servicio._me.get(self.account_id) or {}).get("did", "")
        for evento in (getattr(resp, "logs", None) or []):
            tipo = str(getattr(evento, "py_type", "") or "").rsplit("#", 1)[-1]
            if "createMessage" not in tipo:
                continue
            mensaje = getattr(evento, "message", None)
            remitente = getattr(getattr(mensaje, "sender", None), "did", "")
            if remitente and remitente == mi_did:
                continue        # lo que escribo yo no es una novedad
            try:
                self._al_llegar_mensaje({
                    "convo_id": getattr(evento, "convo_id", "") or "",
                    "texto": getattr(mensaje, "text", "") or "",
                    "remitente": remitente,
                })
            except Exception as e:
                log.debug(f"W Social: el aviso de mensaje falló: {e}")
