# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) de la Fase 2 de PeerTube: catálogos de vídeos, persistencia, paginación,
detalle y búsqueda de canales. La red se simula con `httpx.MockTransport`.
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


def _video(uuid, nombre, **kw):
    d = {"id": kw.get("id", 1), "uuid": uuid, "shortUUID": kw.get("short", "s" + uuid),
         "name": nombre, "description": kw.get("description", "desc"), "duration": kw.get("duration", 600),
         "views": kw.get("views", 100), "likes": kw.get("likes", 10), "dislikes": 1,
         "nsfw": kw.get("nsfw", False), "isLive": kw.get("isLive", False),
         "isLocal": kw.get("isLocal", True), "publishedAt": "2026-01-01T00:00:00.000Z",
         "thumbnailPath": "/static/thumb.jpg", "embedPath": "/videos/embed/" + uuid,
         "channel": {"name": "canal", "host": "test.instance", "displayName": "Canal Uno"},
         "account": {"name": "alice", "host": "test.instance"},
         "privacy": {"id": 1, "label": "Public"}, "language": {"id": "es", "label": "Español"}}
    d.update(kw.get("extra", {}))
    return d


def _handler(state):
    def handler(req):
        path = req.url.path
        params = dict(req.url.params)
        if path == "/api/v1/videos":
            state["last_params"] = params
            start = int(params.get("start", 0))
            if start == 0:
                data = [_video("uuid-0", "Vídeo 0"), _video("uuid-1", "Vídeo 1", isLive=True)]
            elif start == 2:
                data = [_video("uuid-2", "Vídeo 2")]
            else:
                data = []
            return httpx.Response(200, json={"total": 3, "data": data})
        if path.startswith("/api/v1/videos/"):
            uuid = path.rsplit("/", 1)[-1]
            v = _video(uuid, "Detalle " + uuid, extra={
                "files": [{"fileDownloadUrl": "https://test.instance/f.mp4",
                           "resolution": {"id": 720, "label": "720p"}}],
                "streamingPlaylists": [{"playlistUrl": "https://test.instance/master.m3u8"}]})
            return httpx.Response(200, json=v)
        if path == "/api/v1/users/me/subscriptions/videos":
            return httpx.Response(200, json={"total": 1, "data": [_video("sub-1", "De suscripción")]})
        if path == "/api/v1/search/video-channels":
            return httpx.Response(200, json={"total": 1, "data": [
                {"name": "frank", "host": "test.instance", "displayName": "Frank",
                 "followersCount": 42, "description": "Canal de Frank"}]})
        if path.startswith("/api/v1/video-channels/") and path.endswith("/videos"):
            return httpx.Response(200, json={"total": 1, "data": [_video("ch-1", "Del canal")]})
        return httpx.Response(404, json={"error": "no"})
    return handler


@pytest.fixture
def cuenta(monkeypatch):
    """Crea una cuenta de PeerTube con sesión falsa vigente y enruta httpx a un MockTransport."""
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
    if not hasattr(peertube_service, "_after_pt"):
        peertube_service._after_pt = {}
    peertube_service._after_pt.clear()
    # Sesión vigente en memoria (no caduca durante el test).
    peertube_service._sesiones[acc] = {
        "access_token": "acc1", "refresh_token": "ref1", "client_id": "c", "client_secret": "s",
        "instancia": "https://test.instance", "username": "alice", "rol": "user",
        "token_expira": time.time() + 100000, "refresh_expira": time.time() + 200000}
    peertube_service._sesiones_revocadas.discard(acc)
    yield acc, st
    peertube_service._sesiones.pop(acc, None)
    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


# --------------------------------------------------------------------------- resolución de secciones
@pytest.mark.parametrize("seccion,path,clave_param,valor", [
    ("recientes", "videos", "sort", "-publishedAt"),
    ("populares", "videos", "sort", "-views"),
    ("tendencias", "videos", "sort", "-trending"),
    ("locales", "videos", "isLocal", "true"),
    ("federados", "videos", "isLocal", "false"),
    ("suscripciones", "users/me/subscriptions/videos", "sort", "-publishedAt"),
    ("search:gatos", "search/videos", "search", "gatos"),
])
def test_resolver_seccion(seccion, path, clave_param, valor):
    # account_id solo lo usan me_gusta/ver_mas_tarde; para estas secciones un id cualquiera vale.
    p, params, _auth = peertube_service._resolver_seccion(1, seccion)
    assert p == path and params.get(clave_param) == valor


# --------------------------------------------------------------------------- carga y persistencia
def test_cargar_seccion_persiste_videos(cuenta):
    acc, st = cuenta
    n = peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=False)
    assert n == 2
    assert st["last_params"].get("sort") == "-publishedAt"
    filas = db.query("SELECT v.uuid, v.name, v.is_live FROM peertube_videos v "
                     "JOIN peertube_video_buffers b ON v.id=b.video_db_id "
                     "WHERE b.account_id=? AND b.buffer='recientes' ORDER BY b.position", (acc,))
    assert [f[0] for f in filas] == ["uuid-0", "uuid-1"]
    assert filas[1][2] == 1   # uuid-1 es directo


def test_vaciar_cache_sesion_borra_buffers_y_videos(cuenta):
    acc, _st = cuenta
    peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=False)
    assert db.query_one("SELECT COUNT(*) FROM peertube_videos WHERE account_id=?", (acc,))[0] > 0
    peertube_service.vaciar_cache_sesion(acc)
    assert db.query_one("SELECT COUNT(*) FROM peertube_videos WHERE account_id=?", (acc,))[0] == 0
    assert db.query_one("SELECT COUNT(*) FROM peertube_video_buffers WHERE account_id=?", (acc,))[0] == 0


def test_video_a_valores_urls_y_canal(cuenta):
    acc, _st = cuenta
    peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=False)
    row = db.query_one("SELECT channel_display, thumbnail_url, url, language, privacy, files_json "
                       "FROM peertube_videos WHERE id = ?", (f"{acc}:uuid-0",))
    assert row[0] == "Canal Uno"
    assert row[1] == "https://test.instance/static/thumb.jpg"   # URL absoluta
    assert row[2].endswith("/w/suuid-0")                        # URL corta construida
    assert row[3] == "es" and row[4] == 1


def test_paginacion_avanza_cursor(cuenta):
    acc, _st = cuenta
    peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=False)   # start 0 → 2 vídeos
    n = peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=True)  # cursor 2 → 1 vídeo
    assert n == 1
    uuids = [r[0] for r in db.query(
        "SELECT v.uuid FROM peertube_videos v JOIN peertube_video_buffers b ON v.id=b.video_db_id "
        "WHERE b.account_id=? AND b.buffer='recientes' ORDER BY b.position", (acc,))]
    assert uuids == ["uuid-0", "uuid-1", "uuid-2"]


def test_recargar_reemplaza_relacion(cuenta):
    acc, _st = cuenta
    peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=True) if False else None
    peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=False)
    peertube_service._cargar_seccion_sync(acc, "recientes", cargar_mas=False)  # reemplaza, no duplica
    total = db.query_one("SELECT COUNT(*) FROM peertube_video_buffers WHERE account_id=? AND buffer='recientes'",
                         (acc,))[0]
    assert total == 2


def test_suscripciones_usa_endpoint_autenticado(cuenta):
    acc, _st = cuenta
    n = peertube_service._cargar_seccion_sync(acc, "suscripciones", cargar_mas=False)
    assert n == 1
    assert db.query_one("SELECT name FROM peertube_videos WHERE id=?", (f"{acc}:sub-1",))[0] == "De suscripción"


# --------------------------------------------------------------------------- detalle y búsqueda
def test_detalle_video_incluye_fuentes(cuenta):
    acc, _st = cuenta
    det = peertube_service.detalle_video(acc, "uuid-9")
    assert det["name"] == "Detalle uuid-9"
    import json
    fuentes = json.loads(det["files_json"])
    tipos = {f["tipo"] for f in fuentes}
    assert "web" in tipos and "hls" in tipos


def test_buscar_canales(cuenta):
    acc, _st = cuenta
    res = peertube_service.buscar_canales(acc, "frank")
    assert res[0]["handle"] == "frank@test.instance" and res[0]["followers"] == 42


def test_feed_de_canal(cuenta):
    acc, _st = cuenta
    n = peertube_service._cargar_seccion_sync(acc, "channel:frank@test.instance", cargar_mas=False)
    assert n == 1


# --------------------------------------------------------------------------- NSFW
def test_param_nsfw_segun_politica(cuenta, monkeypatch):
    from app.core.config_manager import config
    monkeypatch.setattr(config, "get", lambda s, k, d=None: "display" if k == "nsfw_policy" else d)
    _p, params, _a = peertube_service._resolver_seccion(1, "recientes")
    assert params.get("nsfw") == "both"
