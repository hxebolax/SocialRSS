# -*- coding: utf-8 -*-
"""Grabador de GStreamer (Linux): graba de una fuente de prueba, escribe el WAV y lo codifica a Opus, sin micrófono real."""
import os
import wave

import pytest

pytest.importorskip("gi")
from app.services.audio import microphone  # noqa: E402

try:
    Gst = microphone._gst()
    HAY_GST = Gst.ElementFactory.find("audiotestsrc") is not None and Gst.ElementFactory.find("appsink") is not None
except Exception:      # noqa: BLE001
    HAY_GST = False

pytestmark = pytest.mark.skipif(not HAY_GST, reason="GStreamer no está disponible")


def _grabador():
    g = microphone.GrabadorGst()

    def fuente(_dispositivo):
        e = Gst.ElementFactory.make("audiotestsrc", None)
        e.set_property("is-live", True)
        return e
    g._crear_fuente = fuente
    return g


def test_graba_a_wav_mono_16_bits(tmp_path):
    import time
    g = _grabador()
    g.iniciar(None, 0.5)
    time.sleep(1.2)
    ruta = g.detener_a_wav(str(tmp_path / "voz.wav"))
    with wave.open(ruta) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 44100)
        assert w.getnframes() / 44100 > 0.8
    assert microphone.duracion_wav(ruta) > 0.8 and g._pipeline is None


def test_cancelar_libera_la_captura():
    g = _grabador()
    g.iniciar()
    g.cancelar()
    assert g._pipeline is None and g._trozos == []


def test_opus_con_gstreamer_si_no_hay_ffmpeg(tmp_path, monkeypatch):
    import time
    if Gst.ElementFactory.find("opusenc") is None:
        pytest.skip("falta opusenc")
    g = _grabador()
    g.iniciar()
    time.sleep(0.8)
    wav = g.detener_a_wav(str(tmp_path / "voz.wav"))
    monkeypatch.setattr(microphone, "_ffmpeg", lambda: "/no/existe/ffmpeg")
    ogg = microphone.convertir_a_opus(wav)
    assert ogg and ogg.endswith(".ogg") and os.path.getsize(ogg) > 200


def test_crear_grabador_elige_por_plataforma(monkeypatch):
    monkeypatch.setattr(microphone, "_usa_gstreamer", lambda: True)
    assert isinstance(microphone.crear_grabador(), microphone.GrabadorGst)
    monkeypatch.setattr(microphone, "_usa_gstreamer", lambda: False)
    assert isinstance(microphone.crear_grabador(), microphone.GrabadorBass)
