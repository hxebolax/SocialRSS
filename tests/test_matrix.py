# -*- coding: utf-8 -*-
"""Pruebas del subsistema Matrix (auditoría 2026-07-13).

Cubren la lógica PURA de seguridad y de correctitud añadida por la auditoría, sin depender de un
homeserver ni de matrix-nio con E2EE: transporte HTTPS, validación anti-SSRF de endpoints,
bloqueo de adjuntos ejecutables, menciones por palabra completa y el modelo idempotente de
relaciones (reacciones/votos) con dedupe y redacción.
"""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.matrix.matrix_common import (
    normalizar_homeserver, validar_endpoint_descubierto, MatrixError, q_seg)
from app.services.matrix.matrix_medios import es_extension_activa
from app.services.matrix.matrix_service import matrix_service


# ---------------- MX-005: transporte HTTPS y anti-SSRF ----------------
def test_homeserver_exige_https_remoto():
    assert normalizar_homeserver("matrix.org") == "https://matrix.org"
    assert normalizar_homeserver("https://matrix.org/") == "https://matrix.org"
    with pytest.raises(MatrixError):
        normalizar_homeserver("http://ejemplo.remoto")


def test_homeserver_admite_loopback_http():
    # El loopback no sufre downgrade por la red: http:// se admite.
    assert normalizar_homeserver("http://127.0.0.1:8008") == "http://127.0.0.1:8008"
    assert normalizar_homeserver("http://localhost:8008") == "http://localhost:8008"


def test_endpoint_descubierto_bloquea_metadata_y_http():
    assert validar_endpoint_descubierto("https://auth.matrix.org/oauth", "x")
    with pytest.raises(MatrixError):
        validar_endpoint_descubierto("http://auth.remoto/x", "x")          # sin HTTPS
    with pytest.raises(MatrixError):
        validar_endpoint_descubierto("https://169.254.169.254/x", "x")     # metadata cloud
    with pytest.raises(MatrixError):
        validar_endpoint_descubierto("https://127.0.0.1/x", "x")           # loopback remoto


# ---------------- MX-006: adjuntos ejecutables ----------------
@pytest.mark.parametrize("nombre,activo", [
    ("virus.exe", True), ("script.PS1", True), ("acceso.lnk", True), ("macro.vbs", True),
    ("foto.jpg", False), ("documento.pdf", False), ("cancion.mp3", False), ("sin_extension", False),
])
def test_extension_activa(nombre, activo):
    assert es_extension_activa(nombre) is activo


# ---------------- MX-020: codificación de segmentos ----------------
def test_q_seg_codifica_reservados():
    # El ':' del user_id NO debe quedar sin codificar (no puede alterar la ruta).
    assert q_seg("@ana:matrix.org") == "%40ana%3Amatrix.org"
    assert "/" not in q_seg("a/b")  # la barra tampoco pasa (safe='')


# ---------------- MX-013: menciones por palabra completa ----------------
def test_menciones_palabra_completa():
    acc = 987654
    matrix_service._user_ids[acc] = "@ana:matrix.org"
    assert matrix_service._me_mencionan(acc, None, "me gusta la banana") is False
    assert matrix_service._me_mencionan(acc, None, "hola ana, ¿todo bien?") is True


def test_menciones_intencionadas_m_mentions():
    acc = 987655
    matrix_service._user_ids[acc] = "@bob:matrix.org"

    class _Ev:
        source = {"content": {"m.mentions": {"user_ids": ["@bob:matrix.org"]}}}
        body = "sin nombre en el texto"
    assert matrix_service._me_mencionan(acc, _Ev(), "") is True

    class _Ev2:
        source = {"content": {"m.mentions": {"user_ids": ["@otro:matrix.org"]}}}
        body = "bob mencionado solo por texto"
    # m.mentions presente y NO me incluye: autoritativo -> no es mención.
    assert matrix_service._me_mencionan(acc, _Ev2(), "bob") is False


# ---------------- MX-012: relaciones idempotentes ----------------
@pytest.fixture()
def cuenta_matrix():
    ejecutar_migraciones()
    account_id = db.execute_insert(
        "INSERT INTO accounts (name, service_id, active, position) VALUES ('@t:x', 'matrix', 1, 0)")
    db.execute("INSERT INTO matrix_mensajes (account_id, room_id, event_id, tipo) "
               "VALUES (?, '!r:x', 'msg1', 'msg')", (account_id,))
    db.execute("INSERT INTO matrix_mensajes (account_id, room_id, event_id, tipo, poll_json) "
               "VALUES (?, '!r:x', 'poll1', 'encuesta', ?)",
               (account_id, '{"opciones":{"a":"Rojo","b":"Azul"},"votos":{},"cerrada":false}'))
    yield account_id
    db.execute("DELETE FROM matrix_relations WHERE account_id = ?", (account_id,))
    db.execute("DELETE FROM matrix_mensajes WHERE account_id = ?", (account_id,))
    db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def _reacciones(account_id, target):
    fila = db.query_one("SELECT reacciones FROM matrix_mensajes WHERE account_id = ? AND event_id = ?",
                        (account_id, target))
    return fila[0] if fila else ""


def test_reacciones_dedupe_y_redaccion(cuenta_matrix):
    acc = cuenta_matrix
    # Dos reacciones distintas + un duplicado (mismo event_id): el duplicado no cuenta.
    assert matrix_service._registrar_relacion(acc, "r1", "msg1", "m.annotation", "A", "@u1") is True
    assert matrix_service._registrar_relacion(acc, "r1", "msg1", "m.annotation", "A", "@u1") is False
    matrix_service._registrar_relacion(acc, "r2", "msg1", "m.annotation", "A", "@u2")
    matrix_service._registrar_relacion(acc, "r3", "msg1", "m.annotation", "B", "@u3")
    matrix_service._recalcular_reacciones(acc, "msg1")
    texto = _reacciones(acc, "msg1")
    assert "A 2" in texto and "B 1" in texto
    # Redactar una reacción ajena decrementa el agregado.
    assert matrix_service._redactar_relacion(acc, "r2") is True
    assert "A 1" in _reacciones(acc, "msg1")


# ---------------- P2: estado de envío (descartar solo filas locales) ----------------
def test_descartar_solo_borra_filas_locales(cuenta_matrix):
    acc = cuenta_matrix
    # Fila local fallida + un mensaje REAL del servidor con el mismo texto.
    matrix_service._guardar_mensaje(acc, "!r:x", "msg", sender="@t:x", sender_nombre="Yo",
                                    texto="hola", event_id="local:abc", fecha="2026-07-13T10:00:00",
                                    propio=True, leido=True, contar=False, send_state="fallido")
    matrix_service._guardar_mensaje(acc, "!r:x", "msg", sender="@t:x", sender_nombre="Yo",
                                    texto="real", event_id="$real:x", fecha="2026-07-13T10:01:00",
                                    propio=True, leido=True, contar=False)
    # Descartar la local: desaparece; el intento de descartar el real NO borra nada (no es 'local:').
    matrix_service.descartar_mensaje_local(acc, "local:abc")
    matrix_service.descartar_mensaje_local(acc, "$real:x")
    quedan = {r[0] for r in db.query(
        "SELECT event_id FROM matrix_mensajes WHERE account_id = ?", (acc,))}
    assert "local:abc" not in quedan
    assert "$real:x" in quedan  # un mensaje real nunca se borra por esta vía


def test_votos_ultimo_por_votante(cuenta_matrix):
    import json
    acc = cuenta_matrix
    matrix_service._registrar_relacion(acc, "v1", "poll1", "m.reference", json.dumps(["a"]), "@u1")
    matrix_service._registrar_relacion(acc, "v2", "poll1", "m.reference", json.dumps(["b"]), "@u1")
    matrix_service._recalcular_votos(acc, "poll1")
    poll = json.loads(db.query_one(
        "SELECT poll_json FROM matrix_mensajes WHERE account_id = ? AND event_id = 'poll1'", (acc,))[0])
    assert poll["votos"] == {"@u1": ["b"]}  # el último voto sustituye al anterior
