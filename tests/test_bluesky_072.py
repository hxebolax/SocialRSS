# -*- coding: utf-8 -*-
"""Bluesky con atproto 0.0.72 y el fallo del orden de las conversaciones.

Petición de usuario: "Cuando se abren conversaciones como buffer en Bluesky no respeta el orden
establecido en preferencias. Yo tengo los más nuevos abajo y me abre el buffer al contrario."
La conversación guardaba la raíz con el sort_key MÁS ALTO (al revés que cualquier otro buffer,
donde más alto = más nuevo), así que la preferencia se aplicaba invertida en los dos sentidos.

Sin red: cliente falso y SQLite aislado."""
import json

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.bluesky.bluesky_service import BlueskyService
from app.services.bluesky.bluesky_timeline import known_likers_json
from app.services.bluesky.bluesky_common import identidad_tras_login

ACC = 981


class _Post:
    def __init__(self, uri):
        self.id = f"{ACC}:{uri}"


def _hilo():
    """Raíz -> respuesta 1 -> respuesta 2 (orden de lectura: r0, r1, r2)."""
    return {"thread": {
        "post": {"uri": "at://a/app.bsky.feed.post/r0"},
        "replies": [{"post": {"uri": "at://b/app.bsky.feed.post/r1"},
                     "replies": [{"post": {"uri": "at://c/app.bsky.feed.post/r2"}, "replies": []}]}],
    }}


class _Cliente:
    def get_post_thread(self, uri, depth=None, parent_height=None):
        return _hilo()


def test_conversacion_guarda_lo_mas_nuevo_con_el_sort_key_mas_alto(monkeypatch):
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('bluesky', 'Bluesky', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'B', 'bluesky', 1)", (ACC,))
    s = BlueskyService()

    def _falso(acc, pv, conn=None):
        post = _Post(pv["uri"])
        conn.execute("INSERT OR IGNORE INTO bluesky_posts (id, account_id, uri, cid) VALUES (?, ?, ?, 'c')",
                     (post.id, ACC, pv["uri"]))
        return post
    monkeypatch.setattr(s, "_procesar_y_guardar_post", _falso)
    clave = "thread:at://a/app.bsky.feed.post/r0"
    s._cargar_hilo_como_buffer(ACC, _Cliente(), clave, "cursor-prueba")
    filas = db.query("SELECT post_db_id FROM bluesky_timeline_posts WHERE account_id = ? AND timeline = ? "
                     "ORDER BY sort_key DESC", (ACC, clave))
    # Igual que en cualquier buffer: ORDER BY sort_key DESC = de más nuevo a más antiguo. La
    # interfaz invierte la lista si el usuario quiere "los más nuevos abajo".
    assert [f[0].rsplit("/", 1)[1] for f in filas] == ["r2", "r1", "r0"]


def test_known_likers_se_resume():
    viewer = {"known_likers": {"count": 7, "actors": [
        {"display_name": "Ana", "handle": "ana.bsky.social"}, {"handle": "luis.bsky.social"}]}}
    datos = json.loads(known_likers_json(viewer))
    assert datos == {"count": 7, "nombres": ["Ana", "luis.bsky.social"]}
    assert known_likers_json({}) == ""
    from app.ui.bluesky_controller_carga import texto_known_likers
    assert "Ana" in texto_known_likers(known_likers_json(viewer)) and "5 más" in texto_known_likers(
        known_likers_json(viewer))
    assert texto_known_likers("") == ""


def test_la_migracion_anade_known_likers():
    ejecutar_migraciones()
    cols = {r[1] for r in db.query("PRAGMA table_info(bluesky_posts)")}
    assert "known_likers" in cols
    cols_rss = {r[1] for r in db.query("PRAGMA table_info(rss_articles)")}
    assert {"fulltext_at", "fulltext_metodo"} <= cols_rss


def test_identidad_sale_de_la_sesion_si_login_no_devuelve_perfil():
    from atproto_client.client.session import Session

    class _Cli:
        def export_session_string(self):
            return Session("ana.bsky.social", "did:plc:ana", "a", "r", "https://pds").encode()

    assert identidad_tras_login(_Cli(), None, "ana") == ("did:plc:ana", "ana.bsky.social")
    perfil = type("P", (), {"did": "did:plc:x", "handle": "x.test"})()
    assert identidad_tras_login(_Cli(), perfil, "x") == ("did:plc:x", "x.test")


def test_errores_de_transporte_sin_texto_se_clasifican():
    from atproto_client.exceptions import InvokeTimeoutError
    s = BlueskyService()
    assert "conexión" in s._mensaje_error(InvokeTimeoutError()).lower()
    assert s._es_error_ambiguo(InvokeTimeoutError())
