# -*- coding: utf-8 -*-
"""Pruebas del ASUNTO de las notificaciones de AT Protocol (Bluesky y W Social).

Petición de usuarios (01-08-2026): en las notificaciones no se sabía a qué mensaje
correspondían y "Ver la publicación" fallaba con
    400 NotFound - Post not found: at://did:plc:.../app.bsky.feed.like/3mrt...
porque se pedía la conversación del REGISTRO del "me gusta", que no es una publicación.

Sin red: helpers puros y SQLite aislado.
"""

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.bluesky.bluesky_common import (
    es_uri_de_post, uri_post_de_notificacion)
from app.services.wsocial.wsocial_common import (
    uri_post_de_notificacion as uri_post_notif_wsocial)

ACC = 993
POST = "at://did:plc:autora/app.bsky.feed.post/3abc"
LIKE = "at://did:plc:xsxdzzd2onle2i5gupp2ramw/app.bsky.feed.like/3mrtxtcjv4p2l"
REPOST = "at://did:plc:mia/app.bsky.feed.repost/3rep"
FOLLOW = "at://did:plc:otra/app.bsky.graph.follow/3fol"


def test_uri_de_post_distingue_la_coleccion():
    assert es_uri_de_post(POST)
    assert not es_uri_de_post(LIKE)
    assert not es_uri_de_post(REPOST)
    assert not es_uri_de_post(FOLLOW)
    assert not es_uri_de_post("")
    assert not es_uri_de_post(None)


def test_me_gusta_usa_reason_subject_y_no_el_uri_del_like():
    """El caso exacto del error: el uri es un app.bsky.feed.like."""
    assert uri_post_de_notificacion("like", LIKE, POST) == POST


def test_repost_usa_reason_subject():
    assert uri_post_de_notificacion("repost", REPOST, POST) == POST


def test_mencion_respuesta_y_cita_usan_su_propio_uri():
    # En mención el reasonSubject viene vacío; en respuesta y cita apunta a TU publicación,
    # pero lo que interesa abrir es el mensaje nuevo, que es el propio uri.
    assert uri_post_de_notificacion("mention", POST, "") == POST
    otro = "at://did:plc:autora/app.bsky.feed.post/3resp"
    assert uri_post_de_notificacion("reply", otro, POST) == otro
    assert uri_post_de_notificacion("quote", otro, POST) == otro


def test_via_repost_cae_al_registro_porque_reason_subject_es_un_repost():
    """En like-via-repost el reasonSubject es TU repost (tampoco es un post): la publicación
    real está en el registro del like (record.subject.uri)."""
    record = {"subject": {"uri": POST, "cid": "cid1"}}
    assert uri_post_de_notificacion("like-via-repost", LIKE, REPOST, record) == POST


def test_seguidor_y_verificacion_no_tienen_publicacion():
    assert uri_post_de_notificacion("follow", FOLLOW, "") == ""
    assert uri_post_de_notificacion("verified", FOLLOW, "") == ""
    # starterpack-joined: el reasonSubject es un paquete de inicio, no un post.
    pack = "at://did:plc:otra/app.bsky.graph.starterpack/3pk"
    assert uri_post_de_notificacion("starterpack-joined", FOLLOW, pack) == ""


def test_wsocial_comparte_el_mismo_criterio():
    """W Social usa los mismos lexicons: el arreglo debe comportarse igual."""
    assert uri_post_notif_wsocial("like", LIKE, POST) == POST
    assert uri_post_notif_wsocial("follow", FOLLOW, "") == ""


def test_columnas_de_asunto_en_la_base_de_datos():
    """La migración v21 añade subject_uri/content y la notificación se guarda con ellas."""
    ejecutar_migraciones()
    cols_bsky = {r[1] for r in db.query("PRAGMA table_info(bluesky_notifications)")}
    assert {"subject_uri", "content"} <= cols_bsky
    cols_ws = {r[1] for r in db.query("PRAGMA table_info(wsocial_notifications)")}
    assert "subject_uri" in cols_ws


def test_guardar_notificacion_resuelve_asunto_y_texto():
    """Un 'me gusta' guarda la publicación afectada y su texto (que estaba en la caché)."""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'bluesky', '@prueba.bsky', 1, 97)", (ACC,))
    db.execute("DELETE FROM bluesky_notifications WHERE account_id = ?", (ACC,))
    db.execute("INSERT OR REPLACE INTO bluesky_posts (id, account_id, uri, cid, content) "
               "VALUES (?,?,?,?,?)", (f"{ACC}:{POST}", ACC, POST, "cid1", "Mi publicación"))
    from app.services.bluesky import bluesky_service
    notif = {
        "uri": LIKE, "cid": "cid2", "reason": "like", "reasonSubject": POST,
        "author": {"did": "did:plc:fan", "handle": "fan.bsky", "displayName": "Fan"},
        "record": {"subject": {"uri": POST, "cid": "cid1"}},
        "isRead": False, "indexedAt": "2026-08-01T09:59:46Z",
    }
    datos = bluesky_service._procesar_y_guardar_notificacion(ACC, notif)
    assert datos["subject_uri"] == POST
    assert datos["content"] == "Mi publicación"
    fila = db.query_one("SELECT subject_uri, content FROM bluesky_notifications WHERE id = ?",
                        (f"{ACC}:{LIKE}",))
    assert fila == (POST, "Mi publicación")


def test_pagina_de_notificaciones_resuelve_el_texto_por_red():
    """Si la publicación afectada no está cacheada, se pide con getPosts (fuera de la
    transacción de escritura) y la notificación queda con su texto."""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'bluesky', '@prueba.bsky', 1, 97)", (ACC,))
    from app.services.bluesky import bluesky_service
    post = "at://did:plc:autora/app.bsky.feed.post/3remoto"
    like = "at://did:plc:fan/app.bsky.feed.like/3likeRemoto"

    class _ClienteFalso:
        pedidos = []

        def get_posts(self, uris):
            _ClienteFalso.pedidos.append(list(uris))
            return {"posts": [{
                "uri": post, "cid": "cidR",
                "author": {"did": "did:plc:autora", "handle": "autora.bsky",
                           "display_name": "Autora"},
                "record": {"text": "Publicación que no estaba en caché",
                           "created_at": "2026-08-01T09:00:00Z"},
                "indexed_at": "2026-08-01T09:00:00Z",
            }]}

    respuesta = {"cursor": None, "notifications": [{
        "uri": like, "cid": "cidL", "reason": "like", "reasonSubject": post,
        "author": {"did": "did:plc:fan", "handle": "fan.bsky", "displayName": "Fan"},
        "record": {"subject": {"uri": post, "cid": "cidR"}},
        "isRead": False, "indexedAt": "2026-08-01T09:59:46Z",
    }]}

    class _ClienteConNotifs(_ClienteFalso):
        class app:
            class bsky:
                class notification:
                    @staticmethod
                    def list_notifications(_params):
                        return respuesta

    cliente = _ClienteConNotifs()
    salida = bluesky_service._cargar_notificaciones(ACC, cliente, None, 40, "t:notifs")
    assert salida[0]["subject_uri"] == post
    assert salida[0]["content"] == "Publicación que no estaba en caché"
    assert _ClienteFalso.pedidos == [[post]]
    fila = db.query_one("SELECT content FROM bluesky_notifications WHERE id = ?",
                        (f"{ACC}:{like}",))
    assert fila[0] == "Publicación que no estaba en caché"


def test_no_se_pisa_el_texto_ya_resuelto_con_uno_vacio():
    """Al reprocesar la misma notificación sin la publicación en caché, el texto se conserva."""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'bluesky', '@prueba.bsky', 1, 97)", (ACC,))
    from app.services.bluesky import bluesky_service
    otro_post = "at://did:plc:autora/app.bsky.feed.post/3sinCache"
    otro_like = "at://did:plc:fan/app.bsky.feed.like/3likeB"
    notif = {
        "uri": otro_like, "cid": "cid3", "reason": "like", "reasonSubject": otro_post,
        "author": {"did": "did:plc:fan", "handle": "fan.bsky", "displayName": "Fan"},
        "isRead": False, "indexedAt": "2026-08-01T10:00:00Z",
    }
    bluesky_service._procesar_y_guardar_notificacion(ACC, notif)
    db.execute("UPDATE bluesky_notifications SET content = ? WHERE id = ?",
               ("Texto resuelto por red", f"{ACC}:{otro_like}"))
    bluesky_service._procesar_y_guardar_notificacion(ACC, notif)
    fila = db.query_one("SELECT content FROM bluesky_notifications WHERE id = ?",
                        (f"{ACC}:{otro_like}",))
    assert fila[0] == "Texto resuelto por red"
