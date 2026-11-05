# -*- coding: utf-8 -*-
"""Datos de Reddit para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import time

import pytest

from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_reddit as dr  # noqa: E402

ACC = 9981
AHORA = time.time()


def _post(fn, sub, autor, titulo, cuerpo="", score=10, ncom=3, es_self=1, url="", likes=None, saved=0, over18=0):
    if db.query_one("SELECT 1 FROM reddit_posts WHERE id = ?", (f"{ACC}:{fn}",)):
        db.execute("DELETE FROM reddit_posts WHERE id = ?", (f"{ACC}:{fn}",))
    db.execute("INSERT INTO reddit_posts (id, account_id, fullname, post_id, subreddit, author, title, selftext, url, permalink, "
               "created_utc, score, num_comments, is_self, likes, saved, over_18, domain, media_json, upvote_ratio) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}:{fn}", ACC, fn, fn[3:], sub, autor, titulo, cuerpo, url, f"/r/{sub}/comments/{fn[3:]}/x/",
                AHORA - 3600, score, ncom, es_self, likes, saved, over18, "" if es_self else "ejemplo.org",
                '[{"type": "gallery", "count": 3}]' if fn == "t3_b" else "[]", 0.9))


def _mapa(timeline, fns):
    db.execute("DELETE FROM reddit_timeline_posts WHERE account_id = ? AND timeline = ?", (ACC, timeline))
    for pos, f in enumerate(fns):
        db.execute("INSERT INTO reddit_timeline_posts (account_id, timeline, post_db_id, position) VALUES (?,?,?,?)",
                   (ACC, timeline, f"{ACC}:{f}", pos))


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('reddit', 'Reddit', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) VALUES (?, 'u/yo', 'reddit', 1, 98)", (ACC,))
    for tabla in ("reddit_posts", "reddit_comments", "reddit_messages", "reddit_subreddits"):
        db.execute(f"DELETE FROM {tabla} WHERE account_id = ?", (ACC,))
    for clave in ("saved_subreddits", "saved_searches", "hidden_buffers", "gtk_buffers", "multireddits_cache"):
        config.set("reddit", clave, [])
    config.set("reddit", "buffer_order", [k for k, _t in dr.BUFFERS])
    _post("t3_a", "python", "ana", "Novedades de Python", "Más en https://python.org/news", 120, 14, likes=1)
    _post("t3_b", "accesibilidad", "bob", "Guía", "", 45, 8, es_self=0, url="https://ejemplo.org/guia", saved=1)
    _post("t3_c", "python", "yo", "Mi proyecto", "Texto mío", 5, 1, over18=1)
    _mapa("home", ["t3_a", "t3_b", "t3_c"])
    return ACC


def _mia(raiz):
    return next(c for c in raiz.hijos if c.account_id == ACC)


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = dr.construir_nodos()
    assert raiz.servicio == "reddit" and not raiz.listable
    mia = _mia(raiz)
    assert [h.timeline for h in mia.hijos][:4] == ["home", "popular", "all", "inbox"]
    assert all(h.listable and h.servicio == "reddit" for h in mia.hijos)


def test_orden_y_ocultos_se_respetan(cuenta):
    config.set("reddit", "buffer_order", ["saved", "home"])
    config.set("reddit", "hidden_buffers", ["popular", "multireddits"])
    tls = [h.timeline for h in _mia(dr.construir_nodos()).hijos]
    assert tls[:2] == ["saved", "home"] and "popular" not in tls and "multireddits" not in tls and "all" in tls


def test_listado_con_estado_y_lectura(cuenta):
    filas = dr.listar(cuenta, "home")
    assert [f.extra["fullname"] for f in filas] == ["t3_a", "t3_b", "t3_c"]
    a, b, c = filas
    assert a.titulo.startswith("r/python. Novedades de Python. De u/ana.") and "120 puntos, 14 comentarios" in a.titulo
    assert a.favorito and a.extra["voto"] == 1 and "Voto positivo" in a.titulo
    assert b.extra["guardado"] and "Guardado" in b.titulo and "Galería de 3 imágenes" in b.titulo
    assert "Enlace a ejemplo.org" in b.titulo
    assert c.extra["propio"] and c.extra["nsfw"] and "+18" in c.titulo and not a.extra["propio"]
    assert a.enlace == "https://www.reddit.com/r/python/comments/a/x/"


def test_detalle_de_una_publicacion(cuenta):
    a, b, _c = dr.listar(cuenta, "home")
    titulo, meta, enlace, cuerpo = dr.detalle(a)
    assert titulo == "Novedades de Python" and "r/python" in meta and "u/ana" in meta
    assert "Más en https://python.org/news" in cuerpo and "120 puntos" in cuerpo and "90% de votos positivos" in cuerpo
    assert "voto positivo" in cuerpo
    assert "Enlace: https://ejemplo.org/guia" in dr.detalle(b)[3] and "guardada" in dr.detalle(b)[3]
    assert "Novedades de Python" in dr.texto_plano(a)


def test_comentarios_propios(cuenta):
    db.execute("INSERT INTO reddit_comments (id, account_id, fullname, comment_id, link_id, subreddit, author, body, created_utc, "
               "score, permalink, likes, is_submitter) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}:t1_x", ACC, "t1_x", "x", "t3_a", "python", "yo", "Mi comentario", AHORA - 60, 4, "/r/python/c/x/", 0, 1))
    db.execute("INSERT INTO reddit_timeline_comments (account_id, timeline, comment_db_id, position) VALUES (?, 'comments', ?, 0)",
               (ACC, f"{ACC}:t1_x"))
    (f,) = dr.listar(cuenta, "comments")
    assert f.extra["tipo"] == "comentario" and f.extra["propio"] and f.extra["link_id"] == "t3_a"
    assert "Voto negativo" in f.titulo and "Autor original" in f.titulo and f.titulo.startswith("u/yo: Mi comentario")
    assert dr.detalle(f)[0] == "Comentario de u/yo"


def test_bandeja_de_entrada(cuenta):
    for i, (fn, tipo, autor, nuevo) in enumerate((("t4_1", "message", "carla", 1), ("t1_2", "comment_reply", "dani", 0),
                                                  ("t4_3", "sent", "yo", 0))):
        db.execute("INSERT INTO reddit_messages (id, account_id, fullname, kind, msg_type, author, subject, body, context, "
                   "created_utc, new) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{ACC}:{fn}", ACC, fn, fn[:2], tipo, autor, "Asunto", "Cuerpo del mensaje", "/r/p/c/" if tipo == "comment_reply" else "",
                    AHORA - 600 * i, nuevo))
    assert [f.extra["fullname"] for f in dr.listar(cuenta, "inbox")] == ["t4_1", "t1_2"]
    assert [f.extra["fullname"] for f in dr.listar(cuenta, "unread")] == ["t4_1"]
    assert [f.extra["fullname"] for f in dr.listar(cuenta, "sent")] == ["t4_3"]
    nuevo, respuesta = dr.listar(cuenta, "inbox")
    assert not nuevo.leido and "(nuevo)" in nuevo.titulo and respuesta.leido
    assert respuesta.enlace == "https://www.reddit.com/r/p/c/" and dr.detalle(nuevo)[3] == "Cuerpo del mensaje"


def test_subreddits_y_multireddits(cuenta):
    db.execute("INSERT INTO reddit_subreddits (id, account_id, name, title, public_description, subscribers, user_is_subscriber) "
               "VALUES (?,?,?,?,?,?,1)", (f"{ACC}:python", ACC, "python", "Python", "Todo sobre Python", 1200000))
    (s,) = dr.listar(cuenta, "subreddits")
    assert s.extra["subreddit"] == "python" and s.titulo.startswith("r/python. Python. 1200000 suscriptores")
    config.set("reddit", "multireddits_cache", [{"owner": "yo", "name": "tec", "display": "Tecnología", "path": "/user/yo/m/tec"}])
    (m,) = dr.listar(cuenta, "multireddits")
    assert m.extra["clave"] == "multi:yo:tec" and m.titulo == "Multireddit: Tecnología"


def test_buffers_abiertos_se_guardan_y_se_cierran(cuenta):
    for tl in ("sub:rust", "search:lectores", "user:ana", "sub:rust:new", "multi:yo:tec"):
        dr.nodo_extra(cuenta, tl, dr.titulo_buffer(tl))
    assert config.get("reddit", "saved_subreddits") == [{"name": "rust"}]      # como en la versión de Windows
    assert config.get("reddit", "saved_searches") == ["lectores"]
    hijos = {h.timeline: h for h in _mia(dr.construir_nodos()).hijos}
    assert {"sub:rust", "search:lectores", "user:ana", "sub:rust:new", "multi:yo:tec"} <= set(hijos)
    assert hijos["sub:rust"].titulo == "r/rust" and hijos["user:ana"].titulo == "Publicaciones de u/ana"
    assert hijos["sub:rust"].cerrable and not hijos["home"].cerrable
    _mapa("sub:rust", ["t3_a"])
    for tl in ("sub:rust", "search:lectores", "user:ana", "sub:rust:new", "multi:yo:tec"):
        dr.cerrar_extra(hijos[tl])
    assert not {"sub:rust", "search:lectores", "user:ana", "sub:rust:new", "multi:yo:tec"} & {
        h.timeline for h in _mia(dr.construir_nodos()).hijos}
    assert not db.query("SELECT 1 FROM reddit_timeline_posts WHERE account_id = ? AND timeline = 'sub:rust'", (ACC,))


def test_clasificacion_de_buffers():
    assert all(dr.es_posts(t) for t in ("home", "sub:x", "sub:x:new", "search:a b", "user:u", "multi:o:n", "saved"))
    assert not dr.es_posts("inbox") and dr.es_bandeja("unread") and dr.es_cronologico("comments")
    assert not dr.es_cronologico("subreddits")
