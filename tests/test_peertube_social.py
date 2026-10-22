# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 4 de PeerTube: valoraciones, suscripciones, bloqueos/silencios,
reportes, comentarios (árbol) e historial. La red se simula con `httpx.MockTransport`.
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
        state.setdefault("calls", []).append((m, path))
        import json as _json
        cuerpo = {}
        if req.content:
            try:
                cuerpo = _json.loads(req.content.decode())
            except Exception:
                cuerpo = {}
        state["last_body"] = cuerpo
        # Valoraciones
        if m == "PUT" and path.endswith("/rate"):
            state["rating"] = cuerpo.get("rating")
            return httpx.Response(204)
        if m == "GET" and path.endswith("/rating"):
            return httpx.Response(200, json={"rating": "like"})
        # Suscripciones
        if m == "POST" and path == "/api/v1/users/me/subscriptions":
            state["sub_uri"] = cuerpo.get("uri")
            return httpx.Response(200, json={})
        if m == "DELETE" and path.startswith("/api/v1/users/me/subscriptions/"):
            return httpx.Response(204)
        if m == "GET" and path == "/api/v1/users/me/subscriptions/exist":
            handle = req.url.params.get("uris[]")
            return httpx.Response(200, json={handle: True})
        # Bloqueos
        if m == "POST" and path == "/api/v1/users/me/blocklist/accounts":
            state["blocked_account"] = cuerpo.get("accountName")
            return httpx.Response(204)
        if m == "POST" and path == "/api/v1/users/me/blocklist/servers":
            state["blocked_server"] = cuerpo.get("host")
            return httpx.Response(204)
        # Reportes
        if m == "POST" and path == "/api/v1/abuses":
            state["abuse"] = cuerpo
            return httpx.Response(200, json={"abuse": {"id": 1}})
        # Historial
        if m == "DELETE" and path == "/api/v1/users/me/history/videos":
            state["hist_cleared"] = True
            return httpx.Response(204)
        if m == "DELETE" and path.startswith("/api/v1/users/me/history/videos/"):
            return httpx.Response(204)
        # Comentarios
        if m == "GET" and path.endswith("/comment-threads"):
            return httpx.Response(200, json={"total": 1, "data": [
                {"id": 10, "text": "<p>Primer comentario</p>", "createdAt": "2026-01-01T00:00:00Z",
                 "totalReplies": 1, "isDeleted": False,
                 "account": {"name": "bob", "host": "test.instance", "displayName": "Bob"}}]})
        if m == "GET" and "/comment-threads/" in path:
            return httpx.Response(200, json={
                "comment": {"id": 10, "text": "<p>Primer comentario</p>", "createdAt": "2026-01-01T00:00:00Z",
                            "account": {"name": "bob", "host": "test.instance", "displayName": "Bob"}},
                "children": [{"comment": {"id": 11, "text": "<p>Una respuesta</p>",
                                          "createdAt": "2026-01-01T01:00:00Z",
                                          "account": {"name": "ann", "host": "test.instance",
                                                      "displayName": "Ann"}},
                              "children": []}]})
        if m == "POST" and path.endswith("/comment-threads"):
            return httpx.Response(200, json={"comment": {"id": 20, "text": "<p>Nuevo</p>",
                                                         "account": {"name": "alice"}}})
        if m == "POST" and "/comments/" in path:
            return httpx.Response(200, json={"comment": {"id": 21, "text": "<p>Resp</p>",
                                                         "account": {"name": "alice"}}})
        if m == "DELETE" and "/comments/" in path:
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


# --------------------------------------------------------------------------- valoraciones
def test_votar(cuenta):
    acc, st = cuenta
    assert peertube_service.votar(acc, "uuid-1", "like") is True
    assert st["rating"] == "like"
    peertube_service.votar(acc, "uuid-1", "none")
    assert st["rating"] == "none"


def test_votar_valor_invalido_se_normaliza(cuenta):
    acc, st = cuenta
    peertube_service.votar(acc, "uuid-1", "inventado")
    assert st["rating"] == "none"


def test_mi_valoracion(cuenta):
    acc, _st = cuenta
    assert peertube_service.mi_valoracion(acc, "uuid-1") == "like"


# --------------------------------------------------------------------------- suscripciones
def test_suscribirse(cuenta):
    acc, st = cuenta
    assert peertube_service.suscribirse(acc, "canal@test.instance") is True
    assert st["sub_uri"] == "canal@test.instance"


def test_existe_suscripcion(cuenta):
    acc, _st = cuenta
    assert peertube_service.existe_suscripcion(acc, "canal@test.instance") is True


def test_cancelar_suscripcion(cuenta):
    acc, st = cuenta
    assert peertube_service.cancelar_suscripcion(acc, "canal@test.instance") is True
    assert ("DELETE", "/api/v1/users/me/subscriptions/canal@test.instance") in st["calls"]


# --------------------------------------------------------------------------- bloqueos y reportes
def test_silenciar_cuenta_y_servidor(cuenta):
    acc, st = cuenta
    peertube_service.silenciar_cuenta(acc, "@spammer@mala.instancia")
    assert st["blocked_account"] == "spammer@mala.instancia"   # se limpia la @ inicial
    peertube_service.silenciar_servidor(acc, "mala.instancia")
    assert st["blocked_server"] == "mala.instancia"


def test_reportar_video(cuenta):
    acc, st = cuenta
    assert peertube_service.reportar_video(acc, 55, "Contenido inapropiado") is True
    assert st["abuse"]["video"]["id"] == 55 and st["abuse"]["reason"] == "Contenido inapropiado"


# --------------------------------------------------------------------------- historial
def test_vaciar_y_quitar_historial(cuenta):
    acc, st = cuenta
    assert peertube_service.vaciar_historial(acc) is True
    assert st.get("hist_cleared") is True
    assert peertube_service.quitar_del_historial(acc, "uuid-9") is True


def test_historial_es_seccion_de_videos(cuenta):
    acc, _st = cuenta
    p, params, auth = peertube_service._resolver_seccion(acc, "historial")
    assert p == "users/me/history/videos" and auth is True


# --------------------------------------------------------------------------- comentarios
def test_listar_hilos(cuenta):
    acc, _st = cuenta
    hilos = peertube_service.listar_hilos(acc, "uuid-1")
    assert len(hilos) == 1
    assert hilos[0]["texto"] == "Primer comentario" and hilos[0]["autor"] == "bob@test.instance"
    assert hilos[0]["total_replies"] == 1


def test_cargar_hilo_aplana_con_nivel(cuenta):
    acc, _st = cuenta
    arbol = peertube_service.cargar_hilo(acc, "uuid-1", 10)
    assert [c["nivel"] for c in arbol] == [0, 1]
    assert arbol[0]["texto"] == "Primer comentario" and arbol[1]["texto"] == "Una respuesta"


def test_publicar_y_responder_comentario(cuenta):
    acc, st = cuenta
    nuevo = peertube_service.publicar_comentario(acc, "uuid-1", "Hola")
    assert nuevo["id"] == 20
    assert st["last_body"]["text"] == "Hola"
    resp = peertube_service.responder_comentario(acc, "uuid-1", 20, "Respuesta")
    assert resp["id"] == 21


def test_borrar_comentario(cuenta):
    acc, st = cuenta
    assert peertube_service.borrar_comentario(acc, "uuid-1", 20) is True
    assert any(m == "DELETE" and "/comments/20" in p for m, p in st["calls"])
