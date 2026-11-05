# -*- coding: utf-8 -*-
"""Datos de W Social para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_wsocial as dw  # noqa: E402

ACC = 9983
URI_A = "at://did:plc:ana/app.bsky.feed.post/aaa"
URI_B = "at://did:plc:yo/app.bsky.feed.post/bbb"


def _post(uri, handle, nombre, texto, nlikes=0, liked="", media="[]"):
    pid = f"{ACC}:{uri}"
    db.execute("DELETE FROM wsocial_posts WHERE id = ?", (pid,))
    db.execute("INSERT INTO wsocial_posts (id, account_id, uri, cid, author_did, author_handle, author_name, content, "
               "created_at, indexed_at, like_count, repost_count, reply_count, quote_count, liked_uri, media_json) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (pid, ACC, uri, "cid" + uri[-3:], "did:plc:" + handle, handle, nombre, texto,
                "2026-10-01T10:00:00Z", "2026-10-01T10:00:00Z", nlikes, 2, 3, 1, liked, media))
    return pid


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('wsocial', 'W Social', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) VALUES (?, '@yo.wsocial.eu', 'wsocial', 1, 97)", (ACC,))
    for tabla in ("wsocial_posts", "wsocial_notifications", "wsocial_convos", "wsocial_messages", "wsocial_timeline_posts"):
        db.execute(f"DELETE FROM {tabla} WHERE account_id = ?", (ACC,))
    dw.EXTRA.pop(ACC, None)
    a = _post(URI_A, "ana", "Ana", "Hola desde W Social", nlikes=4, liked="at://like/1",
              media='[{"type": "image", "alt": "Un gato", "url": "https://x/y.png"}]')
    b = _post(URI_B, "yo.wsocial.eu", "Yo", "Mi respuesta")
    for pos, pid in enumerate((a, b)):
        db.execute("INSERT INTO wsocial_timeline_posts (account_id, timeline, post_db_id, position) VALUES (?,?,?,?)",
                   (ACC, "home", pid, pos))
    return ACC


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = dw.construir_nodos()
    assert raiz.servicio == "wsocial" and not raiz.listable
    mia = next(c for c in raiz.hijos if c.account_id == ACC)
    claves = [h.timeline for h in mia.hijos]
    assert claves[:2] == ["home", "notifications"] and "dms" in claves


def test_listado_respeta_la_posicion_y_marca_lo_propio(cuenta):
    filas = dw.listar(ACC, "home")
    assert [f.extra["uri"] for f in filas] == [URI_A, URI_B]
    assert filas[0].favorito and not filas[1].favorito
    assert filas[1].extra["propio"] and not filas[0].extra["propio"]
    assert filas[0].extra["adjuntos"] == 1 and filas[0].extra["n_favoritos"] == 4
    assert filas[0].servicio == "wsocial" and filas[0].enlace == ""


def test_notificaciones_y_menciones(cuenta):
    db.execute("INSERT INTO wsocial_notifications (id, account_id, uri, reason, subject_uri, author_handle, author_name, texto, "
               "is_read, indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}:n1", ACC, "at://n1", "like", URI_B, "ana", "Ana", "Mi respuesta", 0, "2026-10-02T10:00:00Z"))
    filas = dw.listar(ACC, "notifications")
    assert len(filas) == 1 and "ha marcado como me gusta" in filas[0].titulo and not filas[0].leido
    assert filas[0].extra["tipo"] == "notificacion" and filas[0].extra["uri"] == URI_B
    # El detalle de una notificación enseña la publicación a la que se refiere.
    assert dw.detalle(filas[0])[3].startswith("Mi respuesta") or "Mi respuesta" in dw.detalle(filas[0])[3]
    # Las menciones son publicaciones (buffer propio), no notificaciones.
    assert dw.listar(ACC, "mentions") == []


def test_conversaciones_y_mensajes(cuenta):
    db.execute("INSERT INTO wsocial_convos (id, account_id, convo_id, title, unread_count, last_message, last_message_date) "
               "VALUES (?,?,?,?,?,?,?)", (f"{ACC}:c1", ACC, "c1", "Ana", 2, "¿Quedamos?", "2026-10-03T09:00:00Z"))
    db.execute("INSERT INTO wsocial_messages (id, account_id, convo_id, message_id, sender_name, text, sent_at, outgoing) "
               "VALUES (?,?,?,?,?,?,?,?)", (f"{ACC}:c1:m1", ACC, "c1", "m1", "Ana", "¿Quedamos?", "2026-10-03T09:00:00Z", 0))
    db.execute("INSERT INTO wsocial_messages (id, account_id, convo_id, message_id, sender_name, text, sent_at, outgoing) "
               "VALUES (?,?,?,?,?,?,?,?)", (f"{ACC}:c1:m2", ACC, "c1", "m2", "Yo", "Vale", "2026-10-03T09:05:00Z", 1))
    convos = dw.listar(ACC, "dms")
    assert len(convos) == 1 and "2 sin leer" in convos[0].titulo and not convos[0].leido
    msgs = dw.listar(ACC, "dm:c1")
    assert [m.extra["message_id"] for m in msgs] == ["m1", "m2"]
    assert msgs[1].titulo.startswith(_("Yo")) and msgs[1].extra["propio"]


def test_detalle_de_publicacion(cuenta):
    fila = dw.listar(ACC, "home")[0]
    titulo, meta, _enlace, cuerpo = dw.detalle(fila)
    assert titulo == "Ana" and "@ana" in meta
    assert "Hola desde W Social" in cuerpo and "Un gato" in cuerpo and "4 me gusta" in cuerpo


def test_buffers_extra_se_abren_y_se_cierran(cuenta):
    n = dw.nodo_extra(ACC, "thread:" + URI_A, "Conversación")
    def _hijos():
        return [h.timeline for c in dw.construir_nodos().hijos if c.account_id == ACC for h in c.hijos]
    assert n.cerrable and _hijos().count(n.timeline) == 1
    assert dw.nodo_extra(ACC, "thread:" + URI_A, "otro").timeline == n.timeline and _hijos().count(n.timeline) == 1
    dw.cerrar_extra(n)
    assert n.timeline not in _hijos()


def test_adaptar_convierte_ok_error_en_resultado_error():
    visto = []
    cb = dw.adaptar(lambda res, err: visto.append((res, err)))
    cb(True, [1, 2])
    cb(False, "fallo")
    assert visto == [([1, 2], None), (None, "fallo")]
