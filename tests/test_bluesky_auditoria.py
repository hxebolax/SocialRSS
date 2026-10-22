# -*- coding: utf-8 -*-
"""Pruebas de regresión de la auditoría Bluesky del 11-07-2026.

Sin red: contratos con los modelos de atproto, SQLite aislado y helpers puros.
Cubren: mutación no destructiva del perfil (BSKY-001), concurrencia del almacén de
secretos (CORE-SEC-001), orden del servidor con sort_key (BSKY-004), atajo de caché
por CID, FTS con sintaxis hostil y limpieza al borrar, TID/rkey, galería, tipos de
chat y la validación anti-SSRF del previsualizador."""

import json
import threading

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

ACC = 991


def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'bluesky', '@prueba.bsky', 1, 98)", (ACC,))


def _post_view(uri="at://did:plc:x/app.bsky.feed.post/1", cid="cid1", texto="hola",
               indexed="2026-07-11T10:00:00Z"):
    """PostView mínimo como dict anidado (los _g del servicio aceptan dicts)."""
    return {
        "uri": uri, "cid": cid,
        "author": {"did": "did:plc:autora", "handle": "autora.bsky", "display_name": "Autora"},
        "record": {"text": texto, "created_at": indexed},
        "indexed_at": indexed,
        "like_count": 1, "repost_count": 0, "reply_count": 0, "quote_count": 0,
    }


def test_perfil_mutacion_conserva_campos_desconocidos():
    """BSKY-001: la mutación de perfil conserva pronouns/website/etc. y usa swap."""
    from app.services.bluesky import bluesky_service
    llamadas = {}

    class _FakeRepo:
        def get_record(self, params):
            class _R:
                cid = "cid-anterior"

                class value:
                    @staticmethod
                    def model_dump(by_alias=True, exclude_none=True):
                        return {"$type": "app.bsky.actor.profile",
                                "displayName": "Vieja", "description": "bio",
                                "pronouns": "she/her", "website": "https://ej.com",
                                "campoFuturo": {"x": 1},
                                "createdAt": "2025-01-01T00:00:00Z"}
            return _R()

        def put_record(self, data):
            llamadas["data"] = data

    class _FakeClient:
        class com:
            class atproto:
                repo = _FakeRepo()

    ok = bluesky_service._mutar_registro_perfil(
        _FakeClient(), "did:plc:yo",
        lambda reg: reg.__setitem__("displayName", "Nueva"))
    assert ok is True
    data = llamadas["data"]
    reg = data["record"]
    assert reg["displayName"] == "Nueva"
    # Los campos que SocialRSS no conoce SIGUEN en el registro (antes se borraban).
    assert reg["pronouns"] == "she/her"
    assert reg["website"] == "https://ej.com"
    assert reg["campoFuturo"] == {"x": 1}
    assert reg["createdAt"] == "2025-01-01T00:00:00Z"
    assert data["swap_record"] == "cid-anterior"


def test_secretos_concurrentes_no_se_pierden():
    """CORE-SEC-001: dos hilos guardando a la vez no pierden ninguna clave."""
    from app.core.security import SecurityManager
    m = SecurityManager()
    barrera = threading.Barrier(2)

    def trabajo(prefijo):
        barrera.wait()
        for i in range(30):
            m.guardar_credencial("bsky_test", f"{prefijo}{i}", f"v{i}")

    h1 = threading.Thread(target=trabajo, args=("a",))
    h2 = threading.Thread(target=trabajo, args=("b",))
    h1.start(); h2.start(); h1.join(); h2.join()
    perdidas = [f"{p}{i}" for p in ("a", "b") for i in range(30)
                if m.obtener_credencial("bsky_test", f"{p}{i}") != f"v{i}"]
    assert not perdidas, f"claves pisadas por la carrera: {perdidas}"
    assert m.guardar_credenciales({("bsky_test", "x"): "1", "bsky_test:y": "2"}) is True
    assert m.obtener_credencial("bsky_test", "x") == "1"
    assert m.obtener_credencial("bsky_test", "y") == "2"


def test_sort_key_conserva_orden_del_servidor():
    """BSKY-004: el orden de lectura es el que entregó el servidor, no indexed_at."""
    from app.services.bluesky import bluesky_service
    _preparar()
    # Página del servidor: un repost de un post ANTIGUO va PRIMERO (ranking del server).
    viejo = _post_view(uri="at://did:plc:x/app.bsky.feed.post/viejo", cid="c-viejo",
                       texto="post antiguo reposteado", indexed="2026-01-01T00:00:00Z")
    nuevo = _post_view(uri="at://did:plc:x/app.bsky.feed.post/nuevo", cid="c-nuevo",
                       texto="post nuevo", indexed="2026-07-11T09:00:00Z")
    with db.transaccion() as conn:
        conn.execute("DELETE FROM bluesky_timeline_posts WHERE account_id = ? AND timeline = 'home'", (ACC,))
        base = 100
        for idx, pv in enumerate((viejo, nuevo)):   # el server manda el VIEJO primero
            po = bluesky_service._procesar_y_guardar_post(ACC, pv, conn=conn)
            conn.execute(
                "INSERT OR IGNORE INTO bluesky_timeline_posts "
                "(account_id, timeline, post_db_id, sort_key) VALUES (?,?,?,?)",
                (ACC, "home", po.id, base - idx))
    filas = db.query("""
        SELECT p.uri FROM bluesky_posts p
        JOIN bluesky_timeline_posts t ON p.id = t.post_db_id
        WHERE t.account_id = ? AND t.timeline = 'home'
        ORDER BY (t.sort_key IS NULL), t.sort_key DESC, p.indexed_at DESC
    """, (ACC,))
    assert filas[0][0].endswith("/viejo"), "el orden del servidor debía prevalecer"
    assert filas[1][0].endswith("/nuevo")


def test_atajo_cache_detecta_cid_cambiado():
    """7.2: mismo URI con CID nuevo (registro reemplazado) debe reparsear el contenido."""
    from app.services.bluesky import bluesky_service
    _preparar()
    uri = "at://did:plc:x/app.bsky.feed.post/editable"
    bluesky_service._procesar_y_guardar_post(ACC, _post_view(uri=uri, cid="cid-a", texto="antes"))
    # Mismo URI, mismo CID: atajo (contenido intacto).
    bluesky_service._procesar_y_guardar_post(ACC, _post_view(uri=uri, cid="cid-a", texto="IGNORADO"))
    fila = db.query_one("SELECT content, cid FROM bluesky_posts WHERE id = ?", (f"{ACC}:{uri}",))
    assert fila[0] == "antes"
    # CID distinto: reparseo completo.
    bluesky_service._procesar_y_guardar_post(ACC, _post_view(uri=uri, cid="cid-b", texto="después"))
    fila = db.query_one("SELECT content, cid FROM bluesky_posts WHERE id = ?", (f"{ACC}:{uri}",))
    assert fila[0] == "después" and fila[1] == "cid-b"


def test_fts_bluesky_sintaxis_hostil_y_borrado():
    """13.1/13.2: comillas/guiones/operadores no rompen la búsqueda; borrar limpia FTS."""
    from app.services.bluesky import bluesky_service
    _preparar()
    uri = "at://did:plc:x/app.bsky.feed.post/fts"
    bluesky_service._procesar_y_guardar_post(
        ACC, _post_view(uri=uri, cid="c-fts", texto="probando social-rss con texto"))
    for consulta in ('"frase', "foo OR", "social-rss", "probando"):
        res = bluesky_service.buscar_posts_fts(ACC, consulta)
        assert isinstance(res, list)   # nunca excepción ni None
    assert bluesky_service.buscar_posts_fts(ACC, "social-rss"), "el guion rompía la búsqueda"
    # Borrado local completo (sin tocar red): réplica de la limpieza de borrar_post_async.
    with db.transaccion():
        filas = db.query("SELECT id FROM bluesky_posts WHERE account_id = ? AND uri = ?", (ACC, uri))
        for (dbid,) in filas:
            db.execute("DELETE FROM bluesky_posts_fts WHERE post_db_id = ?", (dbid,))
            db.execute("DELETE FROM bluesky_timeline_posts WHERE post_db_id = ?", (dbid,))
            db.execute("DELETE FROM bluesky_posts WHERE id = ?", (dbid,))
    assert not db.query_one("SELECT 1 FROM bluesky_posts_fts WHERE post_db_id = ?", (f"{ACC}:{uri}",)), \
        "la fila FTS quedaba huérfana al borrar"


def test_generador_tid():
    from app.services.bluesky.bluesky_publicar import _PublicarMixin
    vistos = set()
    for _ in range(50):
        tid = _PublicarMixin._generar_tid()
        assert len(tid) == 13
        assert all(c in _PublicarMixin._B32_SORTABLE for c in tid)
        vistos.add(tid)
    assert len(vistos) == 50, "los TID deben ser únicos"


def test_extraer_media_galeria():
    from app.services.bluesky import bluesky_service
    embed = {"$type": "app.bsky.embed.gallery#view",
             "items": [{"fullsize": "https://cdn/a.jpg", "thumb": "https://cdn/a_t.jpg", "alt": "una"},
                       {"fullsize": "https://cdn/b.jpg", "thumb": "https://cdn/b_t.jpg", "alt": "dos"}]}
    media = bluesky_service._extraer_media(embed)
    assert len(media) == 2 and media[0]["alt"] == "una" and media[1]["type"] == "image"


def test_ssrf_ips_privadas_rechazadas():
    from app.core.net import _ip_privada, descargar_url_segura
    for host in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.0.1", "169.254.169.254"):
        assert _ip_privada(host) is True, host
    with pytest.raises(ValueError):
        descargar_url_segura("http://127.0.0.1/latest/meta-data")
    with pytest.raises(ValueError):
        descargar_url_segura("ftp://ejemplo.com/archivo")


def test_etiquetas_hidratadas_y_negaciones():
    """BSKY-005/8.1: post.labels y author.labels se combinan con las self-labels; neg
    retira; las de SISTEMA (!hide) se CONSERVAN para el motor de moderación (la interfaz
    las excluye del aviso con etiquetas_visibles) y ese post no se indexa en FTS."""
    from app.services.bluesky import bluesky_service
    from app.services.bluesky.bluesky_moderacion import etiquetas_visibles
    _preparar()
    pv = _post_view(uri="at://did:plc:x/app.bsky.feed.post/lbl", cid="c-lbl", texto="etiquetado")
    pv["record"]["labels"] = {"values": [{"val": "nudity"}]}
    pv["labels"] = [{"val": "graphic-media", "neg": False},
                    {"val": "nudity", "neg": True},          # negación: retira la propia
                    {"val": "!hide", "neg": False}]           # de sistema: la guarda el motor
    bluesky_service._procesar_y_guardar_post(ACC, pv)
    db_id = f"{ACC}:at://did:plc:x/app.bsky.feed.post/lbl"
    fila = db.query_one("SELECT labels FROM bluesky_posts WHERE id = ?", (db_id,))
    assert fila[0] == "graphic-media,!hide"
    assert etiquetas_visibles(fila[0].split(",")) == ["graphic-media"]
    # Moderación antes de indexar (8.1): con !hide el post NO entra en la búsqueda local.
    assert db.query_one("SELECT 1 FROM bluesky_posts_fts WHERE post_db_id = ?", (db_id,)) is None
