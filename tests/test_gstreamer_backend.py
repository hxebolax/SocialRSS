# -*- coding: utf-8 -*-
"""Backend de reproducción GStreamer (Linux). Se salta si GStreamer no está instalado."""
import math
import struct
import time
import wave

import pytest

pytest.importorskip("gi")
try:
    from app.services.media_player.backends.gstreamer_player_backend import GStreamerPlayerBackend
except (ImportError, ValueError):                       # pragma: no cover
    pytest.skip("GStreamer no disponible", allow_module_level=True)


@pytest.fixture
def wav(tmp_path):
    ruta = tmp_path / "tono.wav"
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i * 0.1))) for i in range(8000 * 2)))
    return str(ruta)


@pytest.fixture
def backend():
    b = GStreamerPlayerBackend()
    b.establecer_volumen(0.0)          # que la prueba no suene
    yield b
    b.cerrar()


def test_cargar_inexistente_da_error_legible(backend):
    assert backend.cargar("/no/existe.mp3") is False
    assert "archivo" in backend.ultimo_error().lower()
    assert backend.obtener_estado() == "inactivo"


def test_ciclo_basico(backend, wav):
    assert backend.cargar(wav) is True
    assert backend.obtener_duracion() == pytest.approx(2.0, abs=0.1)
    assert backend.obtener_estado() == "pausado"
    backend.reproducir()
    assert backend.obtener_estado() == "reproduciendo"
    backend.pausar()
    assert backend.obtener_estado() == "pausado"
    backend.detener()
    assert backend.obtener_estado() == "detenido"
    backend.cerrar()
    assert backend.obtener_estado() == "inactivo"


def test_seek_y_velocidad_se_acotan(backend, wav):
    backend.cargar(wav)
    backend.seek(1.0)
    assert backend.obtener_posicion() == pytest.approx(1.0, abs=0.2)
    backend.establecer_velocidad(10)
    assert backend.obtener_velocidad() == 3.0
    backend.establecer_velocidad(0.1)
    assert backend.obtener_velocidad() == 0.5
    backend.establecer_volumen(7)
    assert backend.obtener_volumen() == 1.0


def test_aviso_de_fin_de_pista(backend, wav):
    fin = []
    backend.al_terminar = lambda: fin.append(True)
    backend.cargar(wav)
    backend.seek(1.8)
    backend.reproducir()
    for _ in range(50):
        if fin:
            break
        time.sleep(0.1)
    assert fin


def test_ecualizador_se_acota(backend):
    backend.establecer_ecualizador([99] * 12, 40)
    ganancias, preamp = backend.obtener_ecualizador()
    assert len(ganancias) == 10 and max(ganancias) == 15.0 and preamp == 15.0
