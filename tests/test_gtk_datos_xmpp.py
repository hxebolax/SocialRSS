# -*- coding: utf-8 -*-
"""Datos de XMPP para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
pytest.importorskip("slixmpp")
from app.gtk import datos_xmpp as dx  # noqa: E402

ACC = 9951


def _chat(jid, tipo, nombre, pres="", unread=0, last="", fecha="2026-10-27T09:00:00", solicitud=0):
    db.execute("INSERT OR REPLACE INTO xmpp_chats (id, account_id, jid, tipo, nombre, presencia, suscripcion, unread_count, "
               "last_message, last_message_date, solicitud) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}|{jid}", ACC, jid, tipo, nombre, pres, "both", unread, last, fecha, solicitud))


def _msg(sid, quien, texto, propio=0, tipo="msg", url="", reply="", react="", leido=0, n=0, voz=0):
    db.execute("INSERT OR REPLACE INTO xmpp_mensajes (account_id, chat_jid, stanza_id, origin_id, sender, sender_nombre, "
               "texto, tipo, media_url, date, es_propio, leido, reply_to, reacciones, es_voz) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (ACC, "ana@j.test", sid, sid, quien, quien, texto, tipo, url, f"2026-10-27T09:0{n}:00Z", propio, leido,
                reply, react, voz))


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('xmpp', 'XMPP', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) "
               "VALUES (?, 'yo@j.test', 'xmpp', 1, 97)", (ACC,))
    db.execute("INSERT OR REPLACE INTO xmpp_cuentas (account_id, jid, recurso) VALUES (?, 'yo@j.test', 'SocialRSS')", (ACC,))
    db.execute("DELETE FROM xmpp_chats WHERE account_id = ?", (ACC,))
    db.execute("DELETE FROM xmpp_mensajes WHERE account_id = ?", (ACC,))
    _chat("ana@j.test", "contacto", "Ana", "Disponible", 2, "¿Quedamos?")
    _chat("sala@conference.j.test", "muc", "Sala", "", 0, "hola", "2026-10-26T09:00:00")
    _chat("carl@j.test", "contacto", "Carl", solicitud=1)
    _chat("nueva@conference.j.test", "muc", "Nueva", solicitud=1)
    _msg("m1", "Ana", "Hola", leido=1, n=0)
    _msg("m2", "yo", "Muy bien", propio=1, leido=1, n=1)
    _msg("m3", "Ana", "", tipo="media", url="https://h.test/nota.opus", n=2, voz=1)
    _msg("m4", "Ana", "¿Quedamos?", reply="m2", react="👍 1", n=3)
    return ACC


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = dx.construir_nodos()
    assert raiz.servicio == "xmpp" and not raiz.listable
    mia = next(c for c in raiz.hijos if c.account_id == cuenta)
    assert [h.timeline for h in mia.hijos][:5] == ["conversaciones", "contactos", "grupos", "solicitudes", "archivados"]
    assert "desconectada" in mia.titulo and all(h.listable for h in mia.hijos)


def test_buffers_extra_y_division_de_dominio(cuenta):
    n = dx.nodo_extra(cuenta, dx.clave_chat("ana@j.test"), "Ana")
    assert n.cerrable and dx.jid_de_chat(n.timeline) == "ana@j.test" and dx.jid_de_chat("contactos") is None
    dx.cerrar_extra(n)
    assert n not in next(c for c in dx.construir_nodos().hijos if c.account_id == cuenta).hijos
    assert dx.dividir("contactos@__nativo__") == ("contactos", "__nativo__") and dx.dividir("grupos") == ("grupos", None)


def test_listas_de_chats(cuenta):
    (ana,) = dx.listar(cuenta, "conversaciones")
    assert ana.extra["jid"] == "ana@j.test" and ana.leido is False
    assert "2 sin leer" in ana.titulo and "Disponible" in ana.titulo and "¿Quedamos?" in ana.titulo
    (sala,) = dx.listar(cuenta, "grupos")
    assert sala.extra["sala"] is True and "grupo" in sala.titulo
    assert [f.extra["jid"] for f in dx.listar(cuenta, "contactos")] == ["ana@j.test"]


def test_solicitudes_de_contacto_y_de_sala(cuenta):
    filas = {f.extra["jid"]: f for f in dx.listar(cuenta, "solicitudes")}
    assert "quiere agregarte" in filas["carl@j.test"].titulo and filas["carl@j.test"].extra["sala"] is False
    assert "Invitación a la sala" in filas["nueva@conference.j.test"].titulo
    assert "aceptar" in dx.detalle(filas["carl@j.test"])[3]


def test_conversacion_con_voz_respuesta_y_reacciones(cuenta):
    filas = dx.listar(cuenta, "chat:ana@j.test")
    textos = [f.titulo for f in filas]
    assert textos[0] == "Ana: Hola" and textos[1] == "Yo: Muy bien"
    assert textos[2].startswith("Nuevo. Ana: Mensaje de voz") and "F2" in textos[2] and "pendiente" in textos[2]
    assert "en respuesta a: yo: Muy bien" in textos[3] and "👍 1" in textos[3]
    assert filas[2].extra["reproducible"] is True and filas[2].extra["adjunto"] == "voz"
    assert dx.seleccion_inicial(filas) == 2
    assert "[Mensaje de voz]" in dx.detalle(filas[2])[3] and dx.detalle(filas[2])[2].endswith("nota.opus")


def test_marcar_leido_y_no_leido(cuenta):
    dx.marcar_leido(cuenta, "ana@j.test")
    assert dx.listar(cuenta, "conversaciones")[0].leido is True
    assert all(f.leido for f in dx.listar(cuenta, "chat:ana@j.test"))
    dx.marcar_no_leido(cuenta, "ana@j.test")
    assert dx.info_chat(cuenta, "ana@j.test")["no_leidos"] == 1
    dx.marcar_todo_leido(cuenta)
    assert dx.info_chat(cuenta, "ana@j.test")["no_leidos"] == 0


def test_ultimo_ajeno_para_anunciar(cuenta):
    assert dx.ultimo_ajeno(cuenta, "ana@j.test")[:2] == ("Ana", "¿Quedamos?")
