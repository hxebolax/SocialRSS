# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 5 de PeerTube: canales, listas, ver más tarde, edición/borrado,
importación, y el inicio de la subida reanudable. La red se simula con `httpx.MockTransport`.
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
                # Formulario (application/x-www-form-urlencoded): valores como cadena.
                cuerpo = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(raw).items()}
        state.setdefault("calls", []).append((m, path))
        state["last_body"] = cuerpo
        # Mis canales (users/me)
        if m == "GET" and path == "/api/v1/users/me":
            return httpx.Response(200, json={"username": "alice", "videoChannels": [
                {"id": 7, "name": "canalalice", "host": "test.instance", "displayName": "Canal de Alice",
                 "followersCount": 10}]})
        # Canales CRUD
        if m == "POST" and path == "/api/v1/video-channels":
            return httpx.Response(200, json={"videoChannel": {"id": 99}})
        if m == "PUT" and path.startswith("/api/v1/video-channels/"):
            return httpx.Response(204)
        if m == "DELETE" and path.startswith("/api/v1/video-channels/"):
            return httpx.Response(204)
        # Listas (por cuenta: PeerTube no tiene users/me/video-playlists)
        if m == "GET" and path == "/api/v1/accounts/alice/video-playlists":
            return httpx.Response(200, json={"total": 2, "data": [
                {"id": 1, "displayName": "Favoritos", "videosLength": 3, "type": {"id": 1}},
                {"id": 2, "displayName": "Ver más tarde", "videosLength": 0, "type": {"id": 2}}]})
        if m == "POST" and path == "/api/v1/video-playlists":
            return httpx.Response(200, json={"videoPlaylist": {"id": 50}})
        if m == "DELETE" and path.startswith("/api/v1/video-playlists/") and "/videos/" not in path:
            return httpx.Response(204)
        if m == "POST" and path.endswith("/videos") and "/video-playlists/" in path:
            state["added_to"] = path
            return httpx.Response(200, json={"videoPlaylistElement": {"id": 500}})
        # Vídeos de una lista (incluida "ver más tarde"): cada elemento envuelve el vídeo.
        if m == "GET" and path == "/api/v1/video-playlists/2/videos":
            return httpx.Response(200, json={"total": 1, "data": [
                {"video": {"id": 901, "uuid": "u-901", "name": "Para luego"}}]})
        # Valoraciones "me gusta" del usuario: también envuelven el vídeo en "video".
        if m == "GET" and path == "/api/v1/accounts/alice/ratings":
            return httpx.Response(200, json={"total": 1, "data": [
                {"rating": "like", "video": {"id": 900, "uuid": "u-900", "name": "Me gustó"}}]})
        # Vídeo editar/borrar
        if m == "PUT" and path.startswith("/api/v1/videos/"):
            state["video_edit"] = cuerpo or dict(req.url.params)
            return httpx.Response(204)
        if m == "DELETE" and path.startswith("/api/v1/videos/"):
            return httpx.Response(204)
        # Importación
        if m == "POST" and path == "/api/v1/videos/imports":
            state["import"] = cuerpo
            return httpx.Response(200, json={"id": 33, "video": {"name": "Importado"},
                                             "state": {"label": "Pendiente"}})
        if m == "GET" and path == "/api/v1/users/me/videos/imports":
            return httpx.Response(200, json={"total": 1, "data": [
                {"id": 33, "video": {"name": "Importado"}, "state": {"label": "Procesando"}}]})
        # Subida reanudable: init
        if m == "POST" and path == "/api/v1/videos/upload-resumable":
            return httpx.Response(201, headers={
                "Location": "/api/v1/videos/upload-resumable?upload_id=abc123"})
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
    if hasattr(peertube_service, "_watch_later"):
        peertube_service._watch_later.clear()
    peertube_service._sesiones[acc] = {
        "access_token": "acc1", "refresh_token": "ref1", "client_id": "c", "client_secret": "s",
        "instancia": "https://test.instance", "username": "alice", "rol": "user",
        "token_expira": time.time() + 100000, "refresh_expira": time.time() + 200000}
    peertube_service._sesiones_revocadas.discard(acc)
    yield acc, st
    peertube_service._sesiones.pop(acc, None)
    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


# --------------------------------------------------------------------------- canales
def test_mis_canales(cuenta):
    acc, _st = cuenta
    canales = peertube_service.mis_canales(acc)
    assert canales[0]["id"] == 7 and canales[0]["handle"] == "canalalice@test.instance"
    assert canales[0]["display"] == "Canal de Alice"


def test_crear_editar_borrar_canal(cuenta):
    acc, st = cuenta
    assert peertube_service.crear_canal(acc, "nuevo", "Canal Nuevo") == 99
    assert st["last_body"]["name"] == "nuevo" and st["last_body"]["displayName"] == "Canal Nuevo"
    assert peertube_service.editar_canal(acc, "nuevo@test.instance", displayName="X") is True
    assert peertube_service.borrar_canal(acc, "nuevo@test.instance") is True


# --------------------------------------------------------------------------- listas y watch-later
def test_mis_listas(cuenta):
    acc, _st = cuenta
    listas = peertube_service.mis_listas(acc)
    assert len(listas) == 2
    assert listas[1]["es_ver_mas_tarde"] is True


def test_crear_y_añadir_a_lista(cuenta):
    acc, st = cuenta
    assert peertube_service.crear_lista(acc, "Mi lista") == 50
    el = peertube_service.añadir_a_lista(acc, 1, 123)
    assert el == 500 and st["last_body"]["videoId"] == 123


def test_ver_mas_tarde_usa_lista_especial(cuenta):
    acc, st = cuenta
    assert peertube_service.id_ver_mas_tarde(acc) == 2   # la de tipo 2
    assert peertube_service.añadir_ver_mas_tarde(acc, 77) is True
    assert "/video-playlists/2/videos" in st.get("added_to", "")


# --------------------------------------------------------------------- secciones me gusta / ver más tarde
def test_me_gusta_resolver(cuenta):
    acc, _st = cuenta
    p, params, auth = peertube_service._resolver_seccion(acc, "me_gusta")
    assert p == "accounts/alice/ratings" and params.get("rating") == "like" and auth is True


def test_ver_mas_tarde_resolver_usa_id(cuenta):
    acc, _st = cuenta
    # Resuelve el id de la lista especial (2) y pide sus vídeos.
    p, _params, auth = peertube_service._resolver_seccion(acc, "ver_mas_tarde")
    assert p == "video-playlists/2/videos" and auth is True


def test_cargar_me_gusta_desenvuelve_video(cuenta):
    acc, _st = cuenta
    n = peertube_service._cargar_seccion_sync(acc, "me_gusta", cargar_mas=False)
    assert n == 1
    row = db.query_one("SELECT name FROM peertube_videos WHERE id = ?", (f"{acc}:u-900",))
    assert row and row[0] == "Me gustó"


def test_cargar_ver_mas_tarde_desenvuelve_video(cuenta):
    acc, _st = cuenta
    n = peertube_service._cargar_seccion_sync(acc, "ver_mas_tarde", cargar_mas=False)
    assert n == 1
    row = db.query_one("SELECT name FROM peertube_videos WHERE id = ?", (f"{acc}:u-901",))
    assert row and row[0] == "Para luego"


# --------------------------------------------------------------------------- edición de vídeo
def test_editar_y_borrar_video(cuenta):
    acc, st = cuenta
    assert peertube_service.editar_video(acc, 5, name="Nuevo título", privacy=1) is True
    assert st["video_edit"].get("name") == "Nuevo título"
    assert peertube_service.borrar_video(acc, 5) is True


# --------------------------------------------------------------------------- importación
def test_importar_url(cuenta):
    acc, st = cuenta
    res = peertube_service.importar_url(acc, "https://ejemplo/video", channel_id=7, name="Vid")
    assert res["id"] == 33
    assert st["import"]["targetUrl"] == "https://ejemplo/video" and str(st["import"]["channelId"]) == "7"


def test_listar_importaciones(cuenta):
    acc, _st = cuenta
    imps = peertube_service.listar_importaciones(acc)
    assert imps[0]["id"] == 33 and imps[0]["estado"] == "Procesando"


# --------------------------------------------------------------------------- subida reanudable (init)
def test_subida_reanudable_init_extrae_upload_id(cuenta):
    acc, _st = cuenta
    uid = peertube_service._iniciar_subida_reanudable(
        acc, "v.mp4", 1000, "video/mp4", 7, "Título", 3, {}, None)
    assert uid == "abc123"


# --------------------------------------------------------------------------- descargas (nombre seguro)
def test_nombre_seguro():
    from app.services.peertube.peertube_descargas import nombre_seguro
    assert nombre_seguro("A/B:C*?") .endswith(".mp4")
    assert "/" not in nombre_seguro("carpeta/archivo")
    assert nombre_seguro("ya.mp4", extension=".mp4") == "ya.mp4"


def test_mejor_para_descarga_mayor_resolucion():
    web = [{"tipo": "web", "resolucion": 480, "url": "b.mp4"},
           {"tipo": "web", "resolucion": 1080, "url": "a.mp4"}]
    assert peertube_service._mejor_para_descarga(web, None)["resolucion"] == 1080
