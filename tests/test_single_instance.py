# -*- coding: utf-8 -*-
"""Pruebas de la orden «mostrar la ventana» entre instancias.

Cuando se lanza una segunda copia de la aplicación (por ejemplo desde el acceso directo con
SocialRSS ya abierto en la bandeja), esa segunda instancia deja una orden en un fichero y la
instancia viva la recoge con un temporizador y muestra su ventana con su propio código. Aquí se
prueba la parte de fichero: dejar, recoger una sola vez y descartar lo caducado.
"""

import time

from app.core import single_instance


def test_dejar_y_recoger_la_orden_una_sola_vez():
    """La orden se consume al recogerla: no debe mostrar la ventana dos veces."""
    assert single_instance.dejar_orden_mostrar() is True
    assert single_instance.recoger_orden_mostrar() is True
    assert single_instance.recoger_orden_mostrar() is False


def test_sin_orden_no_hay_nada_que_recoger():
    """Sin fichero de orden, la instancia viva no hace nada."""
    assert single_instance.recoger_orden_mostrar() is False


def test_una_orden_caducada_se_descarta_y_se_borra(monkeypatch):
    """La basura de un cierre brusco no abre la ventana «de la nada» mucho después."""
    assert single_instance.dejar_orden_mostrar() is True
    # Avanzar el reloj más allá de la caducidad solo para la recogida.
    futuro = time.time() + single_instance.MAX_EDAD_ORDEN_SEGUNDOS + 5
    monkeypatch.setattr(single_instance.time, "time", lambda: futuro)
    assert single_instance.recoger_orden_mostrar() is False
    # El fichero caducado se elimina igualmente: no queda basura pendiente.
    assert not single_instance._ruta_orden_mostrar().exists()


def test_una_orden_ilegible_no_rompe_y_se_limpia():
    """Un fichero corrupto se trata como inexistente y se borra."""
    ruta = single_instance._ruta_orden_mostrar()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("esto no es un número", encoding="utf-8")
    assert single_instance.recoger_orden_mostrar() is False
    assert not ruta.exists()
