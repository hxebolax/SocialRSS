# -*- coding: utf-8 -*-
"""Datos de X (Twitter) para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import json

import pytest

from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_x as dx  # noqa: E402

ACC = 9971


def _tw(tid, screen, nombre, texto, fecha, liked=0, rt=0, bm=0, own=0, rtby="", resp="", cita=("", ""), poll="", media=""):
    db.execute("INSERT OR REPLACE INTO x_tweets (id, account_id, tweet_id, author_id, author_name, author_screen_name, text, "
               "created_at, lang, liked, retweeted, bookmarked, favorite_count, retweet_count, reply_count, quote_count, "
               "view_count, in_reply_to, conversation_id, quoted_id, retweeted_by, media_json, urls_json, is_own, "
               "in_reply_to_screen_name, quoted_author, quoted_text, poll_json) "
               "VALUES (?,?,?,?,?,?,?,?,'es',?,?,?,3,1,2,0,120,?,?,'',?,?,'[]',?,?,?,?,?)",
               (f"{ACC}:{tid}", ACC, tid, "u_" + screen, nombre, screen, texto, fecha, liked, rt, bm,
                "9" if resp else "", tid, rtby, media, own, resp, cita[0], cita[1], poll))


def _mapa(timeline, ids):
    db.execute("DELETE FROM x_timeline_tweets WHERE account_id = ? AND timeline = ?", (ACC, timeline))
    for pos, t in enumerate(ids):
        db.execute("INSERT INTO x_timeline_tweets (account_id, timeline, tweet_db_id, position) VALUES (?,?,?,?)",
                   (ACC, timeline, f"{ACC}:{t}", pos))


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('x', 'X', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) VALUES (?, '@yo', 'x', 1, 97)", (ACC,))
    for tabla in ("x_tweets", "x_timeline_tweets", "x_notifications", "x_users", "x_lists", "x_searches",
                  "x_user_timelines", "x_messages"):
        db.execute(f"DELETE FROM {tabla} WHERE account_id = ?", (ACC,))
    for clave in ("thread_buffers", "community_buffers", "dm_buffers", "hidden_buffers"):
        config.set("x", clave, [])
    poll = json.dumps({"id": "p1", "name": "poll2choice_text_only", "final": False, "selected": None,
                       "choices": [{"number": 1, "label": "Sí", "count": 5}, {"number": 2, "label": "No", "count": 2}]})
    _tw("1", "ana", "Ana", "Hola mundo https://ejemplo.org/x", "2026-10-28T09:00:00+00:00")
    _tw("2", "bob", "Bob", "Con encuesta", "2026-10-28T08:00:00+00:00", poll=poll)
    _tw("3", "yo", "Yo", "Mi tuit", "2026-10-28T07:00:00+00:00", own=1)
    _tw("4", "carla", "Carla", "Respuesta a @ana", "2026-10-28T06:00:00+00:00", resp="ana", rtby="eva", liked=1,
        cita=("Zoe (@zoe)", "algo citado"), media=json.dumps([{"type": "photo", "alt": "un gato"}]))
    _mapa("home", ["1", "2", "3", "4"])
    return ACC


def _mia(raiz):
    return next(c for c in raiz.hijos if c.account_id == ACC)


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = dx.construir_nodos()
    assert raiz.servicio == "x" and not raiz.listable
    mia = _mia(raiz)
    assert [h.timeline for h in mia.hijos][:4] == ["home", "following", "mentions", "notifications"]
    assert all(h.listable and h.servicio == "x" for h in mia.hijos)


def test_buffers_ocultos_se_respetan(cuenta):
    config.set("x", "hidden_buffers", ["bookmarks", "communities"])
    assert "bookmarks" not in [h.timeline for h in _mia(dx.construir_nodos()).hijos]


def test_listado_con_estado_y_textos_accesibles(cuenta):
    filas = dx.listar(cuenta, "home")
    assert [f.extra["tweet_id"] for f in filas] == ["1", "2", "3", "4"]
    assert filas[0].titulo.startswith("Ana (@ana): Hola mundo") and filas[0].enlace == "https://x.com/ana/status/1"
    assert "Encuesta: Sí, 5 votos; No, 2 votos" in filas[1].titulo and filas[1].extra["encuesta"]
    assert filas[2].extra["propio"] and not filas[0].extra["propio"]
    carla = filas[3]
    assert carla.favorito and carla.extra["impulsado_por"] == "eva" and carla.extra["adjuntos"] == 1
    assert "Cita de Zoe (@zoe): algo citado" in carla.titulo
    assert carla.extra["respuesta_a"] == "En respuesta a @ana"


def test_detalle_de_una_publicacion(cuenta):
    carla = dx.listar(cuenta, "home")[3]
    titulo, meta, enlace, cuerpo = dx.detalle(carla)
    assert titulo == "Carla" and "@carla" in meta and enlace.endswith("/carla/status/4")
    assert "Retuiteado por @eva" in cuerpo and "Adjunto 1: Imagen: un gato" in cuerpo and "algo citado" in cuerpo
    assert "3 me gusta" in cuerpo and "120" in cuerpo
    assert "https://ejemplo.org/x" in dx.detalle(dx.listar(cuenta, "home")[0])[3]


def test_encuesta_guardada(cuenta):
    enc = dx.datos_encuesta(cuenta, f"{ACC}:2")
    assert [c["label"] for c in enc["choices"]] == ["Sí", "No"] and dx.datos_encuesta(cuenta, f"{ACC}:1") is None


def test_notificaciones_y_cuentas(cuenta):
    db.execute("INSERT INTO x_notifications (id, account_id, tipo, actor_name, actor_screen_name, text, tweet_id, created_at) "
               "VALUES (?,?,?,?,?,?,?,?)", (f"{ACC}:n1", ACC, "like", "Ana", "ana", "", "3", "2026-10-28T10:00:00+00:00"))
    db.execute("INSERT INTO x_users (id, account_id, user_id, screen_name, name, description, followers, following, bucket, "
               "position) VALUES (?,?,?,?,?,?,?,?,?,?)", (f"{ACC}:u1:followers", ACC, "u1", "ana", "Ana", "Bio", 10, 5, "followers", 0))
    notif = dx.listar(cuenta, "notifications")
    assert len(notif) == 1
    assert any(t in notif[0].titulo for t in ("ha dado me gusta", "liked your post"))
    assert dx.detalle(notif[0])[0] == "Yo"      # el detalle de una notificación es el tuit al que se refiere
    cuentas = dx.listar(cuenta, "followers")
    assert cuentas[0].extra["tipo"] == "usuario" and cuentas[0].extra["author_id"] == "u1"
    assert "Bio" in dx.detalle(cuentas[0])[3]


def test_buffers_abiertos_se_guardan_y_se_cierran(cuenta):
    for tl, titulo in (("search:#accesibilidad", "x"), ("thread:1", "Ana: Hola"), ("dm:u_ana", "@ana")):
        dx.nodo_extra(cuenta, tl, titulo)
    clave = dx.guardar_cronologia(cuenta, "u_ana", "ana", "Ana", "media")
    assert clave == "user:u_ana:media"
    db.execute("INSERT INTO x_lists (id, account_id, list_id, name, member_count, position) VALUES (?,?,?,?,?,?)",
               (f"{ACC}:L1", ACC, "L1", "Amigos", 2, 0))
    hijos = {h.timeline: h for h in _mia(dx.construir_nodos()).hijos}
    assert {"search:#accesibilidad", "thread:1", "dm:u_ana", "user:u_ana:media", "list:L1"} <= set(hijos)
    assert hijos["user:u_ana:media"].titulo == "Cronología: @ana (solo multimedia)"
    assert hijos["search:#accesibilidad"].cerrable and not hijos["list:L1"].cerrable
    for tl in ("search:#accesibilidad", "thread:1", "dm:u_ana", "user:u_ana:media"):
        dx.cerrar_extra(hijos[tl])
    assert set(hijos) - {h.timeline for h in _mia(dx.construir_nodos()).hijos} == {
        "search:#accesibilidad", "thread:1", "dm:u_ana", "user:u_ana:media"}


def test_mensajes_directos(cuenta, monkeypatch):
    from app.services.x import x_service
    monkeypatch.setattr(x_service, "_yo", lambda acc: {"id": "100", "screen_name": "yo"})
    dx.nodo_extra(cuenta, "dm:u_ana", "@ana")
    conv = x_service._convo_id_de("100", "u_ana")
    for mid, saliente, texto, fecha in (("m1", 0, "Hola", "2026-10-28T11:00:00"), ("m2", 1, "Buenas", "2026-10-28T11:01:00")):
        db.execute("INSERT INTO x_messages (id, account_id, convo_id, message_id, sender_id, sender_name, text, date, outgoing) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (f"{ACC}:{conv}:{mid}", ACC, conv, mid, "u_ana", "", texto, fecha, saliente))
    filas = dx.listar(cuenta, "dm:u_ana")
    assert [f.titulo for f in filas] == ["@ana: Hola", "Yo: Buenas"]
    assert filas[1].extra["propio"] and filas[0].extra["user_id"] == "u_ana" and filas[0].extra["convo_id"] == conv
    assert dx.detalle(filas[0])[3] == "Hola"


def test_cronologicos():
    assert dx.es_cronologico("home") and dx.es_cronologico("search:x") and dx.es_cronologico("thread:1")
    assert not dx.es_cronologico("notifications") and not dx.es_cronologico("followers") and not dx.es_cronologico("dm:1")
