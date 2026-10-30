# -*- coding: utf-8 -*-
"""Tiempo real de Mastodon (streaming) para la interfaz GTK.

Reutiliza MastodonStreamingManager del servicio (un gestor por cuenta, con reconexión y vigilante).
El evento llega en un hilo de fondo: se aplica a la caché allí mismo y a la interfaz solo se le avisa,
agrupando ráfagas, para no inundar el hilo de la UI ni el lector de pantalla."""

import logging
import threading

from app.core.plataforma import llamar_en_ui
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.mastodon.streaming_manager import MastodonStreamingManager

log = logging.getLogger(__name__)

# Los mismos buffers de bajo tráfico que en Windows: la Local y la Federada no se streamean.
CLAVES = ("home", "notifications", "mentions", "direct")


class TiempoReal:
    def __init__(self, al_evento, al_reconectar):
        """al_evento(account_id, timeline, procesado, tipo, payload) y al_reconectar(account_id)
        se llaman SIEMPRE en el hilo de la interfaz."""
        self._al_evento = al_evento
        self._al_reconectar = al_reconectar
        self._gestores = {}
        self._lock = threading.Lock()

    def sincronizar(self, cuentas):
        """Arranca los gestores de las cuentas dadas y detiene los de las que ya no están."""
        with self._lock:
            deseadas = set(cuentas)
            for acc in list(self._gestores):
                if acc not in deseadas:
                    self._detener_uno(acc)
            for acc in deseadas:
                gestor = self._gestores.get(acc)
                if gestor is None:
                    gestor = MastodonStreamingManager(
                        (lambda tk, tipo=None, payload=None, a=acc: self._evento(a, tk, tipo, payload)),
                        on_reconnect=(lambda a=acc: llamar_en_ui(self._al_reconectar, a)))
                    self._gestores[acc] = gestor
                try:
                    gestor.iniciar(acc, CLAVES)
                except Exception as e:
                    log.warning(f"No se pudo iniciar el tiempo real de la cuenta {acc}: {e}")

    def _detener_uno(self, acc):
        g = self._gestores.pop(acc, None)
        if g is not None:
            try:
                g.detener()
            except Exception:
                pass

    def detener(self):
        with self._lock:
            for acc in list(self._gestores):
                self._detener_uno(acc)

    def _evento(self, account_id, timeline, tipo, payload):
        """Hilo del streaming: aplica el evento a la caché y avisa a la interfaz."""
        procesado = False
        if tipo:
            try:
                procesado = mastodon_service.procesar_evento_stream(account_id, timeline, tipo, payload)
            except Exception as e:
                log.debug(f"Evento de streaming no aplicable ({tipo}): {e}")
        llamar_en_ui(self._al_evento, account_id, timeline, procesado, tipo, payload)
