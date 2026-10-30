# -*- coding: utf-8 -*-
"""Datos de Matrix para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import json

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_matrix as dm  # noqa: E402

ACC = 9961
R1, R2, R3 = "!acc:m.test", "!dm:m.test", "!inv:m.test"


def _sala(rid, nombre, tema="", dmd=0, unread=0, last="", inv=0, cif=0, esp=0, tag=""):
    db.execute("INSERT OR REPLACE INTO matrix_salas (id, account_id, room_id, nombre, tema, es_dm, unread_count, last_message, "
               "last_message_date, invitacion, cifrada, es_espacio, etiqueta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}|{rid}", ACC, rid, nombre, tema, dmd, unread, last, "2026-10-27T09:00:00", inv, cif, esp, tag))


def _msg(eid, quien, texto, propio=0, tipo="msg", mxc="", reply="", react="", leido=0, hilo="", poll="", n=0, room=R1):
    db.execute("INSERT OR REPLACE INTO matrix_mensajes (account_id, room_id, event_id, sender, sender_nombre, texto, tipo, date, "
               "es_propio, leido, media_mxc, reply_to, reacciones, thread_root, poll_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (ACC, room, eid, "@" + quien.lower() + ":m.test", quien, texto, tipo, f"2026-10-27T09:0{n}:00Z", propio, leido,
                mxc, reply, react, hilo, poll))


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('matrix', 'Matrix', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) VALUES (?, '@yo:m.test', 'matrix', 1, 96)", (ACC,))
    db.execute("DELETE FROM matrix_salas WHERE account_id = ?", (ACC,))
    db.execute("DELETE FROM matrix_mensajes WHERE account_id = ?", (ACC,))
    _sala(R1, "Accesibilidad", "Lectores de pantalla", unread=2, last="Ana: hola", cif=1, tag="favorita")
    _sala(R2, "Bob", dmd=1, unread=1, last="¿Nos vemos?")
    _sala(R3, "Sala nueva", inv=1)
    _sala("!esp:m.test", "Mi espacio", esp=1)
    poll = {"pregunta": "¿Comemos?", "opciones": {"a": "Sí", "b": "No"}, "votos": {}, "cerrada": False, "oculta": False}
    _msg("$1", "Ana", "Hola a todos", leido=1, n=0)
    _msg("$2", "yo", "Buenas", propio=1, leido=1, n=1)
    _msg("$3", "Ana", "[Mensaje de audio: nota.ogg]", tipo="media", mxc="mxc://m.test/abc", n=2)
    _msg("$4", "Ana", "Mira https://ejemplo.org/p.", reply="$2", react="👍 1", n=3)
    _msg("$5", "Ana", "[Encuesta] ¿Comemos?", tipo="encuesta", poll=json.dumps(poll), n=4)
    _msg("$6", "Ana", "[Imagen: foto.png]", tipo="media", mxc="mxc://m.test/def", n=5)
    return ACC


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = dm.construir_nodos()
    assert raiz.servicio == "matrix" and not raiz.listable
    mia = next(c for c in raiz.hijos if c.account_id == cuenta)
    assert [h.timeline for h in mia.hijos][:3] == ["rooms", "dms", "invites"]
    assert "desconectada" in mia.titulo and all(h.listable for h in mia.hijos)


def test_buffers_extra(cuenta):
    n = dm.nodo_extra(cuenta, dm.clave_chat(R1), "Accesibilidad")
    assert n.cerrable and dm.sala_de_chat(n.timeline) == R1 and dm.sala_de_chat("rooms") is None
    dm.cerrar_extra(n)
    assert n not in dm.EXTRA.get(cuenta, [])


def test_listados_de_salas(cuenta):
    salas = dm.listar(cuenta, "rooms")
    assert [f.extra["room_id"] for f in salas] == [R1, "!esp:m.test"]        # la favorita primero, luego el espacio
    assert "2 sin leer" in salas[0].titulo and "cifrada" in salas[0].titulo and "favorita" in salas[0].titulo
    assert [f.extra["room_id"] for f in dm.listar(cuenta, "dms")] == [R2]
    inv = dm.listar(cuenta, "invites")
    assert [f.extra["tipo"] for f in inv] == ["invitacion"] and "invitación" in inv[0].titulo


def test_mensajes_en_orden_con_marcas(cuenta):
    filas = dm.listar(cuenta, dm.clave_chat(R1))
    assert [f.extra["event_id"] for f in filas] == ["$1", "$2", "$3", "$4", "$5", "$6"]
    assert filas[1].titulo.startswith("Yo: ")
    audio, enlace, encuesta, imagen = filas[2], filas[3], filas[4], filas[5]
    assert audio.extra["adjunto"] == "audio" and audio.extra["reproducible"] and "F2" in audio.titulo and audio.titulo.startswith("Nuevo.")
    assert imagen.extra["adjunto"] == "imagen" and "Ctrl+Alt+D" in imagen.titulo
    assert "Intro para abrir el enlace" in enlace.titulo and "en respuesta a: yo: Buenas" in enlace.titulo and "👍 1" in enlace.titulo
    assert "Ctrl+Alt+V" in encuesta.titulo and dm.encuesta(encuesta.extra["poll_json"])["pregunta"] == "¿Comemos?"
    assert dm.seleccion_inicial(filas) == 2


def test_detalle_de_sala_y_de_mensaje(cuenta):
    sala = dm.listar(cuenta, "rooms")[0]
    titulo, meta, _enlace, cuerpo = dm.detalle(sala)
    assert titulo == "Accesibilidad" and R1 in meta and "Lectores de pantalla" in cuerpo and "cifrada" in cuerpo.lower()
    enlace = dm.listar(cuenta, dm.clave_chat(R1))[3]
    titulo, _meta, url, cuerpo = dm.detalle(enlace)
    assert titulo == "Ana" and url == "https://ejemplo.org/p" and "En respuesta a yo: Buenas" in cuerpo


def test_adjuntos_nombre_y_tipo():
    assert dm.nombre_adjunto("[Imagen: foto.png]", "mxc://m/x") == "foto.png"
    assert dm.nombre_adjunto("sin etiqueta", "mxc://m/idmedia") == "idmedia"
    assert dm.tipo_adjunto("[Vídeo: peli.mp4]", "mxc://m/x") == "video"
    assert dm.tipo_adjunto("[Archivo: datos.zip]", "mxc://m/x") == "archivo"
    assert dm.tipo_adjunto("hola", "") == ""
    assert dm.urls_de("a https://x.org/a, y (https://y.org/b). https://x.org/a") == ["https://x.org/a", "https://y.org/b"]


def test_lectura_y_ultimo_ajeno(cuenta):
    antes = dm.ultimo_id(cuenta)
    assert antes > 0
    ultimo = dm.ultimo_ajeno(cuenta, R1)
    assert ultimo[0] == "Ana" and ultimo[4] == antes
    dm.marcar_leido(cuenta, R1)
    assert dm.info_sala(cuenta, R1)["no_leidos"] == 0
    assert not any(f.extra.get("nuevo") for f in dm.listar(cuenta, dm.clave_chat(R1)))
    dm.marcar_no_leido(cuenta, R1)
    assert dm.info_sala(cuenta, R1)["no_leidos"] == 1
    dm.marcar_todo_leido(cuenta)
    assert dm.info_sala(cuenta, R2)["no_leidos"] == 0
    assert dm.titulo_sala(cuenta, "!no-existe:m.test") == "!no-existe:m.test"
