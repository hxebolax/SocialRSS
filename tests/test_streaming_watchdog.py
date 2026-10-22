# -*- coding: utf-8 -*-
"""Pruebas del vigilante del streaming de Mastodon: detección de conexiones COLGADAS.

Una conexión de streaming puede quedarse abierta pero sin recibir un solo byte (la corta en
silencio un proxy, un router NAT o la propia instancia). Su hilo sigue vivo, así que mirando solo
`is_alive()` parecía sana para siempre y el tiempo real se quedaba muerto sin avisar: las
publicaciones solo aparecían cuando algo forzaba un refresco. Estas pruebas fijan que el silencio
cuenta como avería.
"""
import time

from app.services.mastodon.streaming_manager import (MastodonStreamingManager, SIN_DATOS_MAX_SEC)


def _gestor():
    return MastodonStreamingManager(on_event=lambda *a, **k: None)


def test_sin_arrancar_no_hay_silencio_que_medir():
    """Antes de arrancar no se puede hablar de silencio: devolver un valor alto haría que el
    vigilante reconectara en falso nada más abrir la aplicación."""
    assert _gestor().segundos_sin_datos() == 0.0


def test_marcar_dato_reinicia_el_reloj():
    g = _gestor()
    g._ultimo_dato = time.monotonic() - 500     # medio siglo de silencio
    assert g.segundos_sin_datos() > 400
    g._marcar_dato()                            # llega un evento o un latido
    assert g.segundos_sin_datos() < 1


def test_un_latido_cuenta_como_señal_de_vida():
    """Los latidos de keep-alive no son eventos, pero demuestran que la conexión sigue viva: son
    la única señal cuando la cronología está tranquila."""
    g = _gestor()
    g._ultimo_dato = time.monotonic() - (SIN_DATOS_MAX_SEC + 30)
    assert g.segundos_sin_datos() > SIN_DATOS_MAX_SEC     # se daría por colgada
    g._marcar_dato()                                      # llega un latido
    assert g.segundos_sin_datos() <= SIN_DATOS_MAX_SEC    # ya no


def test_el_umbral_deja_margen_para_varios_latidos_perdidos():
    """Mastodon late cada ~30 s: el umbral debe permitir perder alguno sin reconectar en falso,
    pero sin dejar el tiempo real muerto minutos enteros."""
    assert 60 <= SIN_DATOS_MAX_SEC <= 180


def test_el_silencio_prolongado_se_considera_averia():
    g = _gestor()
    g._marcar_dato()
    assert g.segundos_sin_datos() <= SIN_DATOS_MAX_SEC
    g._ultimo_dato = time.monotonic() - (SIN_DATOS_MAX_SEC + 1)
    assert g.segundos_sin_datos() > SIN_DATOS_MAX_SEC


def test_detener_para_el_reloj_de_silencio():
    """Un streaming detenido a propósito no debe contar como colgado (si no, el vigilante
    intentaría reconectar algo que el usuario ha apagado)."""
    g = _gestor()
    g._marcar_dato()
    g.detener()
    assert g.segundos_sin_datos() == 0.0
