# -*- coding: utf-8 -*-
"""Datos de Bluesky para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import json

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_bluesky as db_  # noqa: E402

ACC = 9701


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('bluesky', 'Bluesky', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) "
               "VALUES (?, '@yo.bsky.social', 'bluesky', 1, 91)", (ACC,))
    datos = [("ana.bsky.social", "Ana", "Hola desde Linux", 3, 1, 2, "", []),
             ("yo.bsky.social", "Yo", "Mi publicación", 0, 0, 0, "", []),
             ("eva.bsky.social", "Eva", "Con foto", 5, 2, 1, "porn", [{"type": "image", "url": "https://x/y.jpg", "alt": "Un paisaje"}])]
    for i, (h, n, t, likes, rep, resp, lab, media) in enumerate(datos):
        uri = f"at://did:plc:{h.split('.')[0]}/app.bsky.feed.post/p{i}"
        dbid = f"{ACC}:{uri}"
        db.execute("INSERT OR REPLACE INTO bluesky_posts (id, account_id, uri, cid, author_did, author_handle, author_name, "
                   "content, created_at, indexed_at, labels, like_count, repost_count, reply_count, quote_count, media_json, "
                   "urls_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (dbid, ACC, uri, f"c{i}", "did:plc:" + h.split(".")[0], h, n, t, f"2026-10-25T1{i}:00:00Z",
                    f"2026-10-25T1{i}:00:00Z", lab, likes, rep, resp, 0, json.dumps(media), "[]"))
        db.execute("INSERT OR REPLACE INTO bluesky_timeline_posts (account_id, timeline, post_db_id, sort_key) "
                   "VALUES (?, 'home', ?, ?)", (ACC, dbid, i))
    db.execute("INSERT OR REPLACE INTO bluesky_notifications (id, account_id, uri, cid, reason, subject_uri, content, "
               "author_did, author_handle, author_name, is_read, indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}:at://n/1", ACC, "at://n/1", "c", "like", "at://did:plc:yo/app.bsky.feed.post/p1", "Mi publicación",
                "did:plc:ana", "ana.bsky.social", "Ana", 0, "2026-10-25T20:00:00Z"))
    return ACC


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = db_.construir_nodos()
    assert raiz.servicio == "bluesky" and not raiz.listable
    mia = next(c for c in raiz.hijos if c.account_id == cuenta)
    assert [h.timeline for h in mia.hijos][:3] == ["home", "notifications", "mentions"]
    assert all(h.listable and h.servicio == "bluesky" for h in mia.hijos)


def test_inicio_respeta_el_orden_del_servidor_y_reconoce_lo_propio(cuenta):
    filas = db_.listar(cuenta, "home")
    assert [f.extra["usuario"] for f in filas] == ["eva.bsky.social", "yo.bsky.social", "ana.bsky.social"]
    por = {f.extra["usuario"]: f for f in filas}
    assert por["yo.bsky.social"].extra["propio"] is True and por["ana.bsky.social"].extra["propio"] is False
    assert por["eva.bsky.social"].extra["adjuntos"] == 1 and por["eva.bsky.social"].extra["n_favoritos"] == 5
    assert por["ana.bsky.social"].enlace == "https://bsky.app/profile/ana.bsky.social/post/p0"


def test_detalle_con_adjunto_y_advertencia(cuenta):
    eva = next(f for f in db_.listar(cuenta, "home") if f.extra["usuario"] == "eva.bsky.social")
    titulo, meta, enlace, cuerpo = db_.detalle(eva)
    assert "Un paisaje" in cuerpo and "porn" in cuerpo and "1 respuestas" in cuerpo
    assert enlace.endswith("/post/p2")


def test_notificaciones_dicen_quien_y_que(cuenta):
    filas = db_.listar(cuenta, "notifications")
    assert len(filas) == 1 and "Ana" in filas[0].titulo and "me gusta" in filas[0].titulo
    assert "Mi publicación" in filas[0].titulo and filas[0].leido is False
    assert db_.listar(cuenta, "mentions") == []
    # el detalle de una notificación abre la publicación a la que se refiere
    assert "Mi publicación" in db_.detalle(filas[0])[3]


def test_url_web_y_buffers_extra(cuenta):
    assert db_.url_web("at://did:plc:x/app.bsky.feed.post/abc", "x.bsky.social") == "https://bsky.app/profile/x.bsky.social/post/abc"
    n = db_.nodo_extra(cuenta, "thread:at://z", "Hilo")
    assert db_.nodo_extra(cuenta, "thread:at://z", "Hilo").timeline == n.timeline and n.cerrable
    hijos = lambda: [h.timeline for c in db_.construir_nodos().hijos if c.account_id == cuenta for h in c.hijos]  # noqa: E731
    assert hijos().count("thread:at://z") == 1
    db_.cerrar_extra(n)
    assert "thread:at://z" not in hijos()


@pytest.fixture
def mensajes(cuenta):
    db.execute("DELETE FROM bluesky_convos WHERE account_id = ?", (cuenta,))
    db.execute("DELETE FROM bluesky_messages WHERE account_id = ?", (cuenta,))
    db.execute("INSERT INTO bluesky_convos (id, account_id, convo_id, members_json, title, unread_count, muted, "
               "last_message, last_message_date, is_group) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f"{cuenta}:c1", cuenta, "c1", "[]", "Ana", 2, 1, "¿Quedamos mañana?", "2026-10-25T20:00:00Z", 0))
    for i, (quien, texto, saliente, reacc) in enumerate([("Ana", "Hola", 0, ""), ("Yo", "Bien", 1, ""),
                                                          ("Ana", "¿Quedamos?", 0, json.dumps([{"emoji": "👍"}]))]):
        db.execute("INSERT INTO bluesky_messages (id, account_id, convo_id, message_id, sender_did, sender_name, text, "
                   "embed_json, reactions_json, sent_at, outgoing) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{cuenta}:c1:m{i}", cuenta, "c1", f"m{i}", "did:x", quien, texto, "", reacc,
                    f"2026-10-25T19:0{i}:00Z", saliente))
    return cuenta


def test_mensajes_directos_aparecen_como_buffer(cuenta):
    mia = next(c for c in db_.construir_nodos().hijos if c.account_id == cuenta)
    assert "dms" in [h.timeline for h in mia.hijos]


def test_conversaciones_con_no_leidos_y_silencio(mensajes):
    (fila,) = db_.listar(mensajes, "dms")
    assert fila.extra["tipo"] == "convo" and fila.extra["convo_id"] == "c1" and fila.leido is False
    assert "2 sin leer" in fila.titulo and "silenciada" in fila.titulo and "¿Quedamos mañana?" in fila.titulo
    assert "Intro" in db_.detalle(fila)[3]


def test_mensajes_de_una_conversacion_en_orden_cronologico(mensajes):
    filas = db_.listar(mensajes, "dm:c1")
    assert [f.extra["texto"] for f in filas] == ["Hola", "Bien", "¿Quedamos?"]
    assert filas[1].extra["propio"] is True and filas[1].titulo.startswith("Yo: ")
    assert "👍" in filas[2].titulo
    assert db_.detalle(filas[0])[3] == "Hola"
