# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 1 de PeerTube: detección de instancia/capacidades y
autenticación (login usuario/contraseña, 2FA, refresco, alta y borrado de cuenta, carga de sesiones).

La red se simula sustituyendo la fábrica de clientes httpx por un `httpx.MockTransport` cuyo handler
imita una instancia PeerTube. La base de datos y el almacén de secretos usan el directorio temporal
aislado por conftest.py.
"""
import time
import urllib.parse

import httpx
import pytest

from app.core.migrations import ejecutar_migraciones
from app.core.database import db
from app.core.errors import PeerTubeServiceError
from app.services.peertube import peertube_service
from app.services.peertube.peertube_auth import DosFactoresRequerido


@pytest.fixture(scope="module", autouse=True)
def _preparar_bd():
    ejecutar_migraciones()


def _handler(state):
    """Construye un handler de MockTransport que imita una instancia PeerTube según `state`."""
    def handler(req):
        path = req.url.path
        metodo = req.method
        if metodo == "GET" and path == "/api/v1/config":
            if state.get("config_404"):
                return httpx.Response(404, json={"error": "no"})
            return httpx.Response(200, json={
                "serverVersion": "8.2.0",
                "instance": {"name": "Instancia Test", "defaultNSFWPolicy": "do_not_list"},
                "signup": {"allowed": True, "requiresApproval": False},
                "live": {"enabled": True, "allowReplay": True},
                "import": {"videos": {"http": {"enabled": True}, "torrent": {"enabled": False}}},
                "user": {"videoQuota": -1}})
        if metodo == "GET" and path == "/.well-known/nodeinfo":
            return httpx.Response(404, json={})   # sin nodeinfo por defecto
        if metodo == "GET" and path == "/api/v1/oauth-clients/local":
            state["oauth_calls"] = state.get("oauth_calls", 0) + 1
            return httpx.Response(200, json={"client_id": "cid", "client_secret": "csecret"})
        if metodo == "POST" and path == "/api/v1/users/token":
            body = dict(urllib.parse.parse_qsl(req.content.decode()))
            grant = body.get("grant_type")
            if grant == "password":
                if body.get("client_id") != "cid":
                    return httpx.Response(400, json={"code": "invalid_client"})
                if body.get("password") != "goodpass":
                    return httpx.Response(400, json={"code": "invalid_grant", "error": "invalid_grant"})
                otp = req.headers.get("x-peertube-otp")
                if state.get("require_2fa"):
                    if not otp:
                        return httpx.Response(401, json={"code": "missing_two_factor"})
                    if otp != "123456":
                        return httpx.Response(400, json={"code": "invalid_grant"})
                return httpx.Response(200, json={
                    "access_token": "acc1", "refresh_token": "ref1", "token_type": "Bearer",
                    "expires_in": 86400, "refresh_token_expires_in": 1209600})
            if grant == "refresh_token":
                if body.get("refresh_token") not in ("ref1", "ref2"):
                    return httpx.Response(400, json={"code": "invalid_grant"})
                return httpx.Response(200, json={
                    "access_token": "acc2", "refresh_token": "ref2",
                    "expires_in": 86400, "refresh_token_expires_in": 1209600})
        if metodo == "GET" and path == "/api/v1/users/me":
            if not (req.headers.get("authorization") or "").startswith("Bearer "):
                return httpx.Response(401, json={})
            return httpx.Response(200, json={
                "username": "alice", "role": {"id": state.get("role_id", 2), "label": "R"},
                "account": {"name": "alice"}})
        if metodo == "POST" and path == "/api/v1/users/revoke-token":
            state["revoked"] = True
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": "not found"})
    return handler


@pytest.fixture
def estado(monkeypatch):
    """Sustituye la fábrica de clientes httpx por una con MockTransport y limpia el servicio."""
    st = {}
    handler = _handler(st)

    def fake_crear(**kwargs):
        base = kwargs.get("base_url", "https://test.instance")
        return httpx.Client(transport=httpx.MockTransport(handler), base_url=base)

    monkeypatch.setattr("app.services.peertube.peertube_http.crear_cliente_httpx", fake_crear)
    monkeypatch.setattr("app.services.peertube.peertube_http.time.sleep", lambda s: None)
    # Limpiar el estado en memoria del singleton antes del test.
    peertube_service._clients.clear()
    peertube_service._sesiones.clear()
    peertube_service._instancias.clear()
    peertube_service._sesiones_revocadas.clear()
    peertube_service._oauth_cache.clear()
    yield st
    # Borrar las cuentas de PeerTube creadas por el test.
    try:
        db.execute("DELETE FROM accounts WHERE service_id = 'peertube'")
    except Exception:
        pass


def _alta(otp=None):
    return peertube_service.iniciar_alta("test.instance", "alice", "goodpass", otp=otp)


# --------------------------------------------------------------------------- instancia
def test_detectar_capacidades(estado):
    info = peertube_service.detectar_capacidades("https://test.instance/")
    assert info["es_peertube"] is True and info["version"] == "8.2.0"
    assert info["host"] == "test.instance"
    cap = peertube_service.capacidades_cacheadas("test.instance")
    assert cap["live_enabled"] is True and cap["import_videos_enabled"] is True
    assert cap["signup_allowed"] is True and cap["user_video_quota"] == -1
    assert peertube_service.instancia_soporta("test.instance", "live") is True


def test_detectar_normaliza_y_fuerza_https(estado):
    info = peertube_service.detectar_capacidades("test.instance")
    assert info["base_url"] == "https://test.instance"


def test_detectar_no_peertube(estado):
    estado["config_404"] = True
    with pytest.raises(PeerTubeServiceError) as exc:
        peertube_service.detectar_capacidades("test.instance")
    assert "peertube" in str(exc.value).lower()


# --------------------------------------------------------------------------- login
def test_login_ok(estado):
    r = peertube_service._login("https://test.instance", "alice", "goodpass")
    assert r["token"].access_token == "acc1" and r["token"].refresh_token == "ref1"
    assert r["client_id"] == "cid"


def test_login_password_incorrecta(estado):
    with pytest.raises(PeerTubeServiceError) as exc:
        peertube_service._login("https://test.instance", "alice", "malapass")
    assert "contraseña" in str(exc.value).lower()
    assert not isinstance(exc.value, DosFactoresRequerido)


def test_login_2fa_requerido(estado):
    estado["require_2fa"] = True
    with pytest.raises(DosFactoresRequerido):
        peertube_service._login("https://test.instance", "alice", "goodpass")


def test_login_2fa_ok(estado):
    estado["require_2fa"] = True
    r = peertube_service._login("https://test.instance", "alice", "goodpass", otp="123456")
    assert r["token"].access_token == "acc1"


def test_login_2fa_incorrecto(estado):
    estado["require_2fa"] = True
    with pytest.raises(PeerTubeServiceError):
        peertube_service._login("https://test.instance", "alice", "goodpass", otp="000000")


# --------------------------------------------------------------------------- alta de cuenta
def test_iniciar_alta_persiste_todo(estado):
    r = _alta()
    acc = r["account_id"]
    assert r["username"] == "alice" and r["instancia"] == "test.instance" and r["rol"] == "user"
    # accounts
    fila = db.query_one("SELECT name, service_id FROM accounts WHERE id = ?", (acc,))
    assert fila == ("alice@test.instance", "peertube")
    # meta
    meta = db.query_one("SELECT instancia, username, rol FROM peertube_cuenta_meta WHERE account_id = ?",
                        (acc,))
    assert meta == ("https://test.instance", "alice", "user")
    # credenciales cifradas
    from app.core.security import security
    assert security.obtener_credencial(f"peertube:{acc}", "access_token") == "acc1"
    assert security.obtener_credencial(f"peertube:{acc}", "refresh_token") == "ref1"
    # sesión en memoria + token_provider
    assert peertube_service._sesiones[acc]["access_token"] == "acc1"
    assert peertube_service._token_provider(acc)() == "acc1"


def test_alta_duplicada_falla(estado):
    _alta()
    with pytest.raises(PeerTubeServiceError) as exc:
        _alta()
    assert "ya está añadida" in str(exc.value)


def test_alta_con_2fa(estado):
    estado["require_2fa"] = True
    r = _alta(otp="123456")
    assert r["account_id"] and peertube_service._sesiones[r["account_id"]]["access_token"] == "acc1"


def test_rol_moderador_y_admin(estado):
    estado["role_id"] = 1
    r = _alta()
    assert peertube_service.rol(r["account_id"]) == "moderator"
    assert peertube_service.es_moderador(r["account_id"]) is True
    assert peertube_service.es_administrador(r["account_id"]) is False


# --------------------------------------------------------------------------- refresco de token
def test_refrescar_token(estado):
    acc = _alta()["account_id"]
    assert peertube_service.refrescar_token(acc) is True
    assert peertube_service._sesiones[acc]["access_token"] == "acc2"
    from app.core.security import security
    assert security.obtener_credencial(f"peertube:{acc}", "access_token") == "acc2"


def test_asegurar_sesion_renueva_si_caducado(estado):
    acc = _alta()["account_id"]
    # Forzar caducidad inmediata.
    peertube_service._sesiones[acc]["token_expira"] = time.time() - 10
    peertube_service._asegurar_sesion(acc)
    assert peertube_service._sesiones[acc]["access_token"] == "acc2"   # se refrescó


def test_asegurar_sesion_no_renueva_si_vigente(estado):
    acc = _alta()["account_id"]
    peertube_service._sesiones[acc]["token_expira"] = time.time() + 100000
    peertube_service._asegurar_sesion(acc)
    assert peertube_service._sesiones[acc]["access_token"] == "acc1"   # sin cambios


# --------------------------------------------------------------------------- sesiones y borrado
def test_cargar_sesiones_desde_almacen(estado):
    acc = _alta()["account_id"]
    peertube_service._sesiones.clear()                  # simular reinicio
    n = peertube_service.cargar_sesiones()
    assert n >= 1 and peertube_service._sesiones[acc]["access_token"] == "acc1"


def test_eliminar_cuenta(estado):
    acc = _alta()["account_id"]
    assert peertube_service.eliminar_cuenta(acc) is True
    assert estado.get("revoked") is True                # revocó en el servidor
    assert db.query_one("SELECT COUNT(*) FROM accounts WHERE id=?", (acc,))[0] == 0
    assert db.query_one("SELECT COUNT(*) FROM peertube_cuenta_meta WHERE account_id=?", (acc,))[0] == 0
    assert acc not in peertube_service._sesiones
    from app.core.security import security
    assert security.obtener_credencial(f"peertube:{acc}", "access_token") is None


def test_obtener_cliente_usa_instancia_de_la_cuenta(estado):
    acc = _alta()["account_id"]
    cli = peertube_service.obtener_cliente(acc)
    assert cli._base == "https://test.instance"
