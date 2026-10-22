# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 8 de PeerTube: cola de denuncias (abusos), resolución, mensajes,
mis denuncias y bloqueo/desbloqueo de vídeos. La red se simula con `httpx.MockTransport`.
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


def _abuso(id_, estado):
    return {"id": id_, "reason": "Spam", "state": {"id": estado, "label": "x"},
            "video": {"uuid": "v1", "name": "Vídeo malo"},
            "reporterAccount": {"name": "ann", "host": "test.instance"},
            "flaggedAccount": {"name": "bad", "host": "test.instance"},
            "moderationComment": "", "countMessages": 1, "createdAt": "2026-01-01T00:00:00Z"}


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
        if m == "GET" and path == "/api/v1/abuses":
            return httpx.Response(200, json={"total": 1, "data": [_abuso(9, 1)]})
        if m == "GET" and path == "/api/v1/users/me/abuses":
            return httpx.Response(200, json={"total": 1, "data": [_abuso(3, 3)]})
        if m == "PUT" and path == "/api/v1/abuses/9":
            return httpx.Response(204)
        if m == "DELETE" and path == "/api/v1/abuses/9":
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/abuses/9/messages":
            return httpx.Response(200, json={"total": 1, "data": [
                {"message": "Revisado", "account": {"name": "mod", "host": "test.instance"},
                 "createdAt": "x"}]})
        if m == "POST" and path == "/api/v1/abuses/9/messages":
            return httpx.Response(200, json={"abuseMessage": {"id": 1}})
        if m == "GET" and path == "/api/v1/videos/blacklist":
            return httpx.Response(200, json={"total": 1, "data": [
                {"video": {"id": 5, "uuid": "v5", "name": "Bloqueado"}, "reason": "Ilegal",
                 "unfederated": True}]})
        if m == "POST" and path == "/api/v1/videos/5/blacklist":
            return httpx.Response(204)
        if m == "DELETE" and path == "/api/v1/videos/5/blacklist":
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
        ("mod@test.instance",))
    db.execute("INSERT INTO peertube_cuenta_meta (account_id, instancia, username, rol) "
               "VALUES (?,?,?,?)", (acc, "https://test.instance", "mod", "moderator"))
    peertube_service._clients.clear()
    peertube_service._instancias.clear()
    peertube_service._sesiones[acc] = {
        "access_token": "acc1", "refresh_token": "ref1", "client_id": "c", "client_secret": "s",
        "instancia": "https://test.instance", "username": "mod", "rol": "moderator",
        "token_expira": time.time() + 100000, "refresh_expira": time.time() + 200000}
    peertube_service._sesiones_revocadas.discard(acc)
    yield acc, st
    peertube_service._sesiones.pop(acc, None)
    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


# --------------------------------------------------------------------------- abusos
def test_listar_abusos(cuenta):
    acc, _st = cuenta
    ab = peertube_service.listar_abusos(acc)
    assert ab[0]["id"] == 9 and ab[0]["estado"] == "pendiente"
    assert ab[0]["video_titulo"] == "Vídeo malo" and ab[0]["denunciado"] == "bad@test.instance"


def test_mis_denuncias(cuenta):
    acc, _st = cuenta
    md = peertube_service.mis_denuncias(acc)
    assert md[0]["id"] == 3 and md[0]["estado"] == "aceptada"


def test_aceptar_y_rechazar_abuso(cuenta):
    acc, st = cuenta
    assert peertube_service.aceptar_abuso(acc, 9, comentario="ok") is True
    assert st["last_body"]["state"] == 3 and st["last_body"]["moderationComment"] == "ok"
    peertube_service.rechazar_abuso(acc, 9)
    assert st["last_body"]["state"] == 2


def test_borrar_abuso(cuenta):
    acc, st = cuenta
    assert peertube_service.borrar_abuso(acc, 9) is True
    assert ("DELETE", "/api/v1/abuses/9") in st["calls"]


def test_mensajes_abuso(cuenta):
    acc, _st = cuenta
    msgs = peertube_service.mensajes_abuso(acc, 9)
    assert msgs[0]["mensaje"] == "Revisado" and msgs[0]["autor"] == "mod@test.instance"
    assert peertube_service.añadir_mensaje_abuso(acc, 9, "Hola") is True


# --------------------------------------------------------------------------- bloqueo de vídeos
def test_listar_videos_bloqueados(cuenta):
    acc, _st = cuenta
    bl = peertube_service.listar_videos_bloqueados(acc)
    assert bl[0]["video_id"] == 5 and bl[0]["titulo"] == "Bloqueado" and bl[0]["sin_federar"] is True


def test_bloquear_y_desbloquear_video(cuenta):
    acc, st = cuenta
    assert peertube_service.bloquear_video(acc, 5, razon="Ilegal", unfederate=True) is True
    assert st["last_body"]["reason"] == "Ilegal" and st["last_body"]["unfederate"] is True
    assert peertube_service.desbloquear_video(acc, 5) is True


# --------------------------------------------------------------------------- rol moderador
def test_es_moderador(cuenta):
    acc, _st = cuenta
    assert peertube_service.es_moderador(acc) is True
    assert peertube_service.es_administrador(acc) is False
