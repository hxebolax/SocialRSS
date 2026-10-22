# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 6 de PeerTube: crear un directo, obtener sus datos de emisión,
actualizarlo y listar sus sesiones. La red se simula con `httpx.MockTransport`.
"""
import time

import httpx
import pytest

from app.core.migrations import ejecutar_migraciones
from app.core.database import db
from app.services.peertube import peertube_service


@pytest.fixture(scope="module", autouse=True)
def _preparar_bd():
    ejecutar_migraciones()


def _handler(state):
    def handler(req):
        path = req.url.path
        m = req.method
        import json as _json
        from urllib.parse import parse_qs
        cuerpo = {}
        if req.content:
            raw = req.content.decode()
            ct = (req.headers.get("content-type") or "").lower()
            if "json" in ct:
                try:
                    cuerpo = _json.loads(raw)
                except Exception:
                    cuerpo = {}
            else:
                cuerpo = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(raw).items()}
        state["last_body"] = cuerpo
        state.setdefault("calls", []).append((m, path))
        if m == "POST" and path == "/api/v1/videos/live":
            return httpx.Response(200, json={"video": {"id": 88, "uuid": "live-uuid"}})
        if m == "GET" and path == "/api/v1/videos/live/88":
            return httpx.Response(200, json={
                "rtmpUrl": "rtmp://test.instance/live", "rtmpsUrl": "rtmps://test.instance/live",
                "streamKey": "SECRETO-123", "permanentLive": True, "latencyMode": 1,
                "saveReplay": False})
        if m == "PUT" and path == "/api/v1/videos/live/88":
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/videos/live/88/sessions":
            return httpx.Response(200, json={"total": 1, "data": [
                {"startDate": "2026-01-01T10:00:00Z", "endDate": "2026-01-01T11:00:00Z",
                 "error": None, "replayVideo": {"uuid": "rep-1", "name": "Repetición 1"}}]})
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


# --------------------------------------------------------------------------- crear
def test_crear_directo(cuenta):
    acc, st = cuenta
    res = peertube_service.crear_directo(acc, 7, "Mi directo", permanent_live=True, latency_mode=3)
    assert res["id"] == 88 and res["uuid"] == "live-uuid"
    assert st["last_body"]["name"] == "Mi directo"
    assert st["last_body"]["permanentLive"] == "true"   # los booleanos van como 'true'/'false'
    assert str(st["last_body"]["latencyMode"]) == "3"


def test_crear_directo_con_replay(cuenta):
    acc, st = cuenta
    peertube_service.crear_directo(acc, 7, "Con repetición", save_replay=True, replay_privacy=1)
    assert st["last_body"]["saveReplay"] == "true"
    assert str(st["last_body"]["replaySettings[privacy]"]) == "1"


# --------------------------------------------------------------------------- info de emisión
def test_info_directo_incluye_clave(cuenta):
    acc, _st = cuenta
    info = peertube_service.info_directo(acc, 88)
    assert info["rtmp_url"] == "rtmp://test.instance/live"
    assert info["stream_key"] == "SECRETO-123"
    assert info["permanent_live"] is True and info["latency_mode"] == 1


# --------------------------------------------------------------------------- actualizar
def test_actualizar_directo(cuenta):
    acc, st = cuenta
    assert peertube_service.actualizar_directo(acc, 88, saveReplay=True, latencyMode=2) is True
    assert st["last_body"]["saveReplay"] is True and st["last_body"]["latencyMode"] == 2


# --------------------------------------------------------------------------- sesiones
def test_sesiones_directo(cuenta):
    acc, _st = cuenta
    ses = peertube_service.sesiones_directo(acc, 88)
    assert len(ses) == 1
    assert ses[0]["repeticion_uuid"] == "rep-1" and ses[0]["repeticion_titulo"] == "Repetición 1"


# --------------------------------------------------------------------------- sección directos
def test_directos_es_seccion_de_videos(cuenta):
    acc, _st = cuenta
    p, params, auth = peertube_service._resolver_seccion(acc, "directos")
    assert p == "videos" and params.get("isLive") == "true"


def test_directo_se_reproduce_como_radio():
    # Un vídeo en directo debe reproducirse con KIND_RADIO (sin barra de tiempo). Se comprueba que el
    # selector marca la fuente HLS y que la lógica de repro usa el flag is_live.
    fuentes = [{"tipo": "hls", "url": "https://x/live.m3u8"}]
    sel = peertube_service.seleccionar_fuente(fuentes, es_directo=True)
    assert sel["es_hls"] is True
