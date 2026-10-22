# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 7 de PeerTube: notificaciones (listar, describir, marcar,
preferencias) y estadísticas (vídeo e instancia). La red se simula con `httpx.MockTransport`.
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
        cuerpo = {}
        if req.content:
            try:
                cuerpo = _json.loads(req.content.decode())
            except Exception:
                cuerpo = {}
        state["last_body"] = cuerpo
        state.setdefault("calls", []).append((m, path))
        if m == "GET" and path == "/api/v1/users/me/notifications":
            if req.url.params.get("unread") == "true" and req.url.params.get("count") == "0":
                return httpx.Response(200, json={"total": 4, "data": []})
            return httpx.Response(200, json={"total": 2, "data": [
                {"id": 1, "type": 1, "read": False, "createdAt": "2026-01-01T00:00:00Z",
                 "video": {"uuid": "u1", "name": "Vídeo nuevo",
                           "channel": {"displayName": "Canal X"}}},
                {"id": 2, "type": 10, "read": True, "createdAt": "2026-01-01T01:00:00Z",
                 "actorFollow": {"follower": {"displayName": "Bob"}}}]})
        if m == "POST" and path == "/api/v1/users/me/notifications/read":
            state["marked"] = cuerpo.get("ids")
            return httpx.Response(204)
        if m == "POST" and path == "/api/v1/users/me/notifications/read-all":
            state["read_all"] = True
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/users/me/notification-settings":
            return httpx.Response(200, json={"newVideoFromSubscription": 1, "newCommentOnMyVideo": 0})
        if m == "PUT" and path == "/api/v1/users/me/notification-settings":
            state["prefs"] = cuerpo
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/videos/5/stats/overall":
            return httpx.Response(200, json={
                "totalViewers": 1200, "viewersPeak": 45, "totalWatchTime": 36000,
                "averageWatchTime": 30, "countries": [{"isoCode": "ES", "viewers": 800}]})
        if m == "GET" and path == "/api/v1/server/stats":
            return httpx.Response(200, json={"totalUsers": 50, "totalVideos": 300,
                                             "totalLocalVideos": 120, "otraClave": "texto"})
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


# --------------------------------------------------------------------------- notificaciones
def test_listar_notificaciones_con_descripcion(cuenta):
    acc, _st = cuenta
    notifs = peertube_service.listar_notificaciones(acc)
    assert len(notifs) == 2
    assert "Canal X" in notifs[0]["descripcion"] and notifs[0]["video_uuid"] == "u1"
    assert notifs[0]["leida"] is False
    assert "Bob" in notifs[1]["descripcion"] and notifs[1]["leida"] is True


def test_contador_no_leidas(cuenta):
    acc, _st = cuenta
    assert peertube_service.contador_no_leidas(acc) == 4


def test_marcar_leidas_y_todas(cuenta):
    acc, st = cuenta
    assert peertube_service.marcar_leidas(acc, [1, 2]) is True
    assert st["marked"] == [1, 2]
    assert peertube_service.marcar_todas_leidas(acc) is True
    assert st.get("read_all") is True


def test_preferencias_notificaciones(cuenta):
    acc, st = cuenta
    prefs = peertube_service.preferencias_notificaciones(acc)
    assert prefs["newVideoFromSubscription"] == 1
    assert peertube_service.guardar_preferencias_notificaciones(
        acc, {"newVideoFromSubscription": 0}) is True
    assert st["prefs"]["newVideoFromSubscription"] == 0


# --------------------------------------------------------------------------- estadísticas
def test_stats_video(cuenta):
    acc, _st = cuenta
    st = peertube_service.stats_video(acc, 5)
    assert st["total_espectadores"] == 1200 and st["pico_espectadores"] == 45
    assert st["paises"][0]["pais"] == "ES" and st["paises"][0]["espectadores"] == 800


def test_stats_instancia_solo_numericas(cuenta):
    acc, _st = cuenta
    st = peertube_service.stats_instancia(acc)
    assert st["totalUsers"] == 50 and st["totalVideos"] == 300
    assert "otraClave" not in st   # se descartan las no numéricas
