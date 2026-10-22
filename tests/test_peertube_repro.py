# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red ni BASS) de la Fase 3 de PeerTube: selección de fuente, resolución de
variantes HLS, subtítulos y registro de vista. La red se simula con `httpx.MockTransport`.
"""
import time

import httpx
import pytest

from app.core.migrations import ejecutar_migraciones
from app.core.database import db
from app.services.peertube import peertube_service

_MASTER = ("#EXTM3U\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=426x240\n"
           "240.m3u8\n"
           "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n"
           "720.m3u8\n")
_VTT = ("WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nHola a todos\n\n"
        "2\n00:00:04.000 --> 00:00:06.000\nBienvenidos al vídeo\n")


@pytest.fixture(scope="module", autouse=True)
def _preparar_bd():
    ejecutar_migraciones()


def _handler(state):
    def handler(req):
        path = req.url.path
        if path.endswith("/master.m3u8"):
            return httpx.Response(200, text=_MASTER,
                                  headers={"content-type": "application/vnd.apple.mpegurl"})
        if path.endswith("/captions"):
            return httpx.Response(200, json={"total": 1, "data": [
                {"language": {"id": "es", "label": "Español"},
                 "captionPath": "/lazy-static/video-captions/uuid-es.vtt",
                 "automaticallyGenerated": False}]})
        if path.endswith(".vtt"):
            return httpx.Response(200, text=_VTT, headers={"content-type": "text/vtt"})
        if path.endswith("/views"):
            state["view"] = True
            return httpx.Response(204)
        return httpx.Response(404, json={"error": "no"})
    return handler


@pytest.fixture
def cuenta(monkeypatch):
    st = {}
    handler = _handler(st)

    def fake_crear(**kwargs):
        base = kwargs.get("base_url", "https://test.instance")
        return httpx.Client(transport=httpx.MockTransport(handler), base_url=base)

    monkeypatch.setattr("app.services.peertube.peertube_http.crear_cliente_httpx", fake_crear)
    acc = db.execute_insert(
        "INSERT INTO accounts (name, service_id, active, position) VALUES (?, 'peertube', 1, 0)",
        ("alice@test.instance",))
    db.execute("INSERT INTO peertube_cuenta_meta (account_id, instancia, username, rol) "
               "VALUES (?,?,?,?)", (acc, "https://test.instance", "alice", "user"))
    peertube_service._clients.clear()
    peertube_service._instancias.clear()
    peertube_service._sesiones[acc] = {
        "access_token": "acc1", "refresh_token": "ref1", "client_id": "c", "client_secret": "s",
        "instancia": "https://test.instance", "username": "alice", "rol": "user",
        "token_expira": time.time() + 100000, "refresh_expira": time.time() + 200000}
    peertube_service._sesiones_revocadas.discard(acc)
    yield acc, st
    peertube_service._sesiones.pop(acc, None)
    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


# --------------------------------------------------------------------------- selector (puro)
def test_selector_prefiere_mp4_progresivo():
    fuentes = [{"tipo": "hls", "url": "https://x/master.m3u8"},
               {"tipo": "web", "resolucion": 720, "url": "https://x/720.mp4"}]
    sel = peertube_service.seleccionar_fuente(fuentes, preferencia="auto")
    assert sel["tipo"] == "web" and sel["es_hls"] is False and sel["necesita_ffmpeg"] is False


def test_selector_auto_menor_resolucion():
    fuentes = [{"tipo": "web", "resolucion": 1080, "url": "https://x/1080.mp4"},
               {"tipo": "web", "resolucion": 360, "url": "https://x/360.mp4"}]
    assert peertube_service.seleccionar_fuente(fuentes, preferencia="auto")["resolucion"] == 360


def test_selector_prefiere_audio_only():
    fuentes = [{"tipo": "web", "resolucion": 720, "url": "https://x/720.mp4"},
               {"tipo": "web", "resolucion": 0, "url": "https://x/audio.mp4"}]
    assert peertube_service.seleccionar_fuente(fuentes)["resolucion"] == 0


def test_selector_hls_si_no_hay_web():
    sel = peertube_service.seleccionar_fuente([{"tipo": "hls", "url": "https://x/m.m3u8"}])
    assert sel["tipo"] == "hls" and sel["es_hls"] is True


def test_selector_webm_marca_ffmpeg():
    sel = peertube_service.seleccionar_fuente([{"tipo": "web", "resolucion": 480,
                                                "url": "https://x/480.webm"}])
    assert sel["necesita_ffmpeg"] is True


def test_selector_preferencia_numerica():
    fuentes = [{"tipo": "web", "resolucion": 1080, "url": "a.mp4"},
               {"tipo": "web", "resolucion": 480, "url": "b.mp4"},
               {"tipo": "web", "resolucion": 240, "url": "c.mp4"}]
    assert peertube_service.seleccionar_fuente(fuentes, preferencia="480")["resolucion"] == 480


def test_selector_sin_fuentes():
    assert peertube_service.seleccionar_fuente([]) is None


def test_vtt_a_texto_limpia_marcas():
    assert peertube_service._vtt_a_texto(_VTT) == "Hola a todos\nBienvenidos al vídeo"


# --------------------------------------------------------------------------- HLS (con red simulada)
def test_resolver_variante_hls_elige_menor(cuenta):
    acc, _st = cuenta
    url = peertube_service.resolver_variante_hls(acc, "https://test.instance/hls/master.m3u8",
                                                 preferencia="auto")
    assert url == "https://test.instance/hls/240.m3u8"   # la de menor resolución


def test_resolver_variante_hls_preferencia(cuenta):
    acc, _st = cuenta
    url = peertube_service.resolver_variante_hls(acc, "https://test.instance/hls/master.m3u8",
                                                 preferencia="720")
    assert url == "https://test.instance/hls/720.m3u8"


# --------------------------------------------------------------------------- subtítulos y vista
def test_listar_y_descargar_subtitulos(cuenta):
    acc, _st = cuenta
    subs = peertube_service.listar_subtitulos(acc, "uuid-1")
    assert subs and subs[0]["idioma"] == "es" and subs[0]["etiqueta"] == "Español"
    assert subs[0]["path"] == "https://test.instance/lazy-static/video-captions/uuid-es.vtt"
    texto = peertube_service.descargar_subtitulo(acc, subs[0]["path"])
    assert texto == "Hola a todos\nBienvenidos al vídeo"


def test_registrar_vista(cuenta):
    acc, st = cuenta
    assert peertube_service.registrar_vista(acc, "uuid-1", 42) is True
    assert st.get("view") is True


# --------------------------------------------------------------------------- servidor de streaming
def test_stream_server_url_local():
    from app.services.peertube.peertube_stream import stream_peertube
    url = stream_peertube.url_para("https://cdn.test/video.mp4")
    assert url.startswith("http://127.0.0.1:") and "/s/" in url
    stream_peertube.cerrar()
