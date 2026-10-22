# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 9 de PeerTube: administración (usuarios, registros, federación,
listas de bloqueo, trabajos, plugins, tokens de runners). La red se simula con `httpx.MockTransport`.
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
        if m == "GET" and path == "/api/v1/users":
            return httpx.Response(200, json={"total": 1, "data": [
                {"id": 4, "username": "bob", "email": "bob@x", "role": {"id": 2}, "blocked": False,
                 "videoQuota": -1}]})
        if m == "POST" and path == "/api/v1/users":
            return httpx.Response(200, json={"user": {"id": 100}})
        if m == "PUT" and path == "/api/v1/users/4":
            return httpx.Response(204)
        if m == "DELETE" and path == "/api/v1/users/4":
            return httpx.Response(204)
        if m == "POST" and path == "/api/v1/users/4/block":
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/users/registrations":
            return httpx.Response(200, json={"total": 1, "data": [
                {"id": 7, "username": "nuevo", "email": "n@x", "state": {"label": "Pendiente"},
                 "registrationReason": "Quiero entrar"}]})
        if m == "POST" and path == "/api/v1/users/registrations/7/accept":
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/server/following":
            return httpx.Response(200, json={"total": 1, "data": [
                {"following": {"host": "otra.instancia"}, "state": "accepted"}]})
        if m == "GET" and path == "/api/v1/server/followers":
            return httpx.Response(200, json={"total": 1, "data": [
                {"follower": {"host": "seguidora.instancia"}, "state": "pending"}]})
        if m == "POST" and path == "/api/v1/server/following":
            return httpx.Response(204)
        if m == "DELETE" and path == "/api/v1/server/following/otra.instancia":
            return httpx.Response(204)
        if m == "POST" and path == "/api/v1/server/followers/seguidora.instancia/accept":
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/jobs/completed":
            return httpx.Response(200, json={"total": 1, "data": [
                {"id": "j1", "type": "video-transcoding", "state": "completed", "error": None}]})
        if m == "GET" and path == "/api/v1/plugins":
            return httpx.Response(200, json={"total": 1, "data": [
                {"name": "livechat", "npmName": "peertube-plugin-livechat", "version": "1.0",
                 "type": 1}]})
        if m == "POST" and path == "/api/v1/plugins/uninstall":
            return httpx.Response(204)
        if m == "POST" and path == "/api/v1/runners/registration-tokens/generate":
            return httpx.Response(200, json={"registrationToken": "ptrt-ABC"})
        if m == "GET" and path == "/api/v1/runners/registration-tokens":
            return httpx.Response(200, json={"total": 1, "data": [
                {"id": 1, "registrationToken": "ptrt-ABC", "createdAt": "x"}]})
        if m == "GET" and path == "/api/v1/config/custom":
            return httpx.Response(200, json={"instance": {"name": "Test"},
                                             "signup": {"enabled": True}})
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
        ("admin@test.instance",))
    db.execute("INSERT INTO peertube_cuenta_meta (account_id, instancia, username, rol) "
               "VALUES (?,?,?,?)", (acc, "https://test.instance", "admin", "administrator"))
    peertube_service._clients.clear()
    peertube_service._instancias.clear()
    peertube_service._sesiones[acc] = {
        "access_token": "acc1", "refresh_token": "ref1", "client_id": "c", "client_secret": "s",
        "instancia": "https://test.instance", "username": "admin", "rol": "administrator",
        "token_expira": time.time() + 100000, "refresh_expira": time.time() + 200000}
    peertube_service._sesiones_revocadas.discard(acc)
    yield acc, st
    peertube_service._sesiones.pop(acc, None)
    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


# --------------------------------------------------------------------------- usuarios
def test_listar_y_crear_usuario(cuenta):
    acc, st = cuenta
    us = peertube_service.admin_listar_usuarios(acc)
    assert us[0]["username"] == "bob" and us[0]["rol"] == "usuario"
    assert peertube_service.admin_crear_usuario(acc, "carol", "clave", "c@x", rol=1) == 100
    assert st["last_body"]["username"] == "carol" and st["last_body"]["role"] == 1


def test_editar_bloquear_borrar_usuario(cuenta):
    acc, st = cuenta
    assert peertube_service.admin_editar_usuario(acc, 4, role=1) is True
    assert st["last_body"]["role"] == 1
    assert peertube_service.admin_bloquear_usuario(acc, 4, bloquear=True, razon="spam") is True
    assert peertube_service.admin_borrar_usuario(acc, 4) is True


# --------------------------------------------------------------------------- registros
def test_registros(cuenta):
    acc, _st = cuenta
    regs = peertube_service.admin_listar_registros(acc)
    assert regs[0]["username"] == "nuevo" and regs[0]["motivo"] == "Quiero entrar"
    assert peertube_service.admin_aceptar_registro(acc, 7, respuesta="ok") is True


# --------------------------------------------------------------------------- federación
def test_federacion(cuenta):
    acc, st = cuenta
    sig = peertube_service.admin_listar_siguiendo(acc)
    assert sig[0]["host"] == "otra.instancia"
    seg = peertube_service.admin_listar_seguidores(acc)
    assert seg[0]["host"] == "seguidora.instancia"
    assert peertube_service.admin_seguir_instancias(acc, ["nueva.instancia"]) is True
    assert st["last_body"]["hosts"] == ["nueva.instancia"]
    assert peertube_service.admin_dejar_instancia(acc, "otra.instancia") is True
    assert peertube_service.admin_aceptar_seguidor(acc, "seguidora.instancia") is True


# --------------------------------------------------------------------------- jobs, plugins, runners
def test_jobs(cuenta):
    acc, _st = cuenta
    jobs = peertube_service.admin_listar_jobs(acc, estado="completed")
    assert jobs[0]["tipo"] == "video-transcoding" and jobs[0]["estado"] == "completed"


def test_plugins(cuenta):
    acc, _st = cuenta
    pl = peertube_service.admin_listar_plugins(acc)
    assert pl[0]["nombre"] == "livechat" and pl[0]["npm"] == "peertube-plugin-livechat"
    assert peertube_service.admin_desinstalar_plugin(acc, "peertube-plugin-livechat") is True


def test_runner_tokens(cuenta):
    acc, _st = cuenta
    assert peertube_service.admin_generar_token_runner(acc) == "ptrt-ABC"
    toks = peertube_service.admin_listar_tokens_runner(acc)
    assert toks[0]["token"] == "ptrt-ABC"


def test_config_custom_lectura(cuenta):
    acc, _st = cuenta
    cfg = peertube_service.admin_config_custom(acc)
    assert cfg["instance"]["name"] == "Test" and cfg["signup"]["enabled"] is True


def test_es_administrador(cuenta):
    acc, _st = cuenta
    assert peertube_service.es_administrador(acc) is True and peertube_service.es_moderador(acc) is True
