# -*- coding: utf-8 -*-
"""Citas de Mastodon que se quedaban "pendientes" para siempre.

Una cita desde OTRO servidor llega primero en estado "pending" y el servidor del autor citado la
aprueba segundos después, SIN editar el toot ni avisar por el streaming (Quote#accept! solo limpia
caché). La app guardaba el estado del primer instante y el atajo de "solo contadores" impedía
actualizarlo aunque el toot volviera a llegar ya aceptado. Sin red: todo contra SQLite aislado."""

import json
from datetime import datetime, timezone

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.mastodon import mastodon_citas
from app.services.mastodon.mastodon_citas import estado_cita_api, estado_cita_guardada
from app.services.mastodon.mastodon_service import mastodon_service

ACC = 991  # id de cuenta ficticio para estas pruebas


def _preparar(monkeypatch):
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'mastodon', 'prueba citas', 1, 99)", (ACC,))
    apuntadas = []
    # Sin temporizadores reales: solo se registra qué se habría programado.
    monkeypatch.setattr(mastodon_service, "_programar_revision_cita",
                        lambda clave, intento, retraso=None: apuntadas.append((clave, intento, retraso)))
    mastodon_service.__dict__["_citas_pend"] = {}
    return apuntadas


def _toot(sid, estado, created_at="2026-10-19T15:05:00+00:00"):
    quote = {"state": estado, "quoted_status": None}
    if estado == "accepted":
        quote["quoted_status"] = {
            "id": "117461408192923853",
            "content": "<p>Os presento Ctrl+IA.</p>",
            "account": {"display_name": "Hector", "acct": "HXeBoLaX"},
        }
    return {
        "id": sid, "uri": f"https://tkz.one/users/javic87/statuses/{sid}",
        "content": '<p class="quote-inline">RE: <a href="https://x/@a/1">x</a></p><p>esto tiene buena pinta</p>',
        "created_at": created_at, "visibility": "public",
        "sensitive": False, "favourited": False, "reblogged": False, "bookmarked": False,
        "account": {"display_name": "JC", "username": "javic87", "acct": "javic87@tkz.one"},
        "media_attachments": [], "quote": quote,
    }


def test_estados_de_cita():
    assert estado_cita_api({"quote": {"state": "pending"}}) == "pending"
    assert estado_cita_api({"quote": None}) is None
    assert estado_cita_api({}) is None
    assert estado_cita_guardada(None) is None
    assert estado_cita_guardada('{"state": "revoked", "id": ""}') == "revoked"
    # Las guardadas por versiones antiguas (sin 'state') eran siempre aceptadas.
    assert estado_cita_guardada('{"acct": "a", "content": "b"}') == "accepted"


def test_cita_aceptada_despues_actualiza_la_cache(monkeypatch):
    """El toot vuelve a llegar sin editar pero con la cita ya aceptada: debe actualizarse."""
    apuntadas = _preparar(monkeypatch)
    mastodon_service._procesar_y_guardar_toot(ACC, _toot("7001", "pending"))
    fila = db.query_one("SELECT quote_json FROM mastodon_statuses WHERE id = ?", (f"{ACC}:7001",))
    assert json.loads(fila[0])["state"] == "pending"
    assert apuntadas and apuntadas[0][0] == (ACC, "7001"), "debía programarse su revisión"

    mastodon_service._procesar_y_guardar_toot(ACC, _toot("7001", "accepted"))
    fila = db.query_one("SELECT quote_json FROM mastodon_statuses WHERE id = ?", (f"{ACC}:7001",))
    datos = json.loads(fila[0])
    assert datos["state"] == "accepted"
    assert datos["acct"] == "HXeBoLaX" and "Ctrl+IA" in datos["content"]
    from app.ui.ui_helpers import formato_cita
    assert formato_cita(fila[0]).startswith("Citando a")


def test_apuntar_es_idempotente(monkeypatch):
    apuntadas = _preparar(monkeypatch)
    mastodon_service._apuntar_cita_pendiente(ACC, "7002")
    mastodon_service._apuntar_cita_pendiente(ACC, "7002")
    assert len(apuntadas) == 1


def test_revision_detecta_aceptacion_y_avisa(monkeypatch):
    apuntadas = _preparar(monkeypatch)
    mastodon_service._procesar_y_guardar_toot(ACC, _toot("7003", "pending"))

    class _Cliente:
        def status(self, sid):
            return _toot(sid, "accepted")
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda acc: _Cliente())
    res = mastodon_service._revisar_cita_bg((ACC, "7003"))
    assert res == {"antes": "pending", "ahora": "accepted", "db_id": f"{ACC}:7003"}

    from app.core.event_bus import event_bus
    recibidos = []

    def _oyente(acc, db_id):
        recibidos.append((acc, db_id))
    event_bus.subscribe(mastodon_citas.EVENTO_CITA_ACTUALIZADA, _oyente)
    try:
        n_antes = len(apuntadas)
        mastodon_service._fin_revision_cita((ACC, "7003"), 0, res, None)
    finally:
        event_bus.unsubscribe(mastodon_citas.EVENTO_CITA_ACTUALIZADA, _oyente)
    assert recibidos == [(ACC, f"{ACC}:7003")]
    assert len(apuntadas) == n_antes, "aceptada: no debe reprogramarse"
    assert (ACC, "7003") not in mastodon_service._citas_pendientes()


def test_revision_sigue_pendiente_reprograma_y_se_rinde(monkeypatch):
    apuntadas = _preparar(monkeypatch)
    clave = (ACC, "7004")
    mastodon_service._citas_pendientes()[clave] = 0
    res = {"antes": "pending", "ahora": "pending", "db_id": f"{ACC}:7004"}
    mastodon_service._fin_revision_cita(clave, 0, res, None)
    assert apuntadas[-1][:2] == (clave, 1)
    # En el último intento ya no se reprograma: se olvida.
    ultimo = len(mastodon_citas.RETRASOS_REVISION_SEG) - 1
    n = len(apuntadas)
    mastodon_service._fin_revision_cita(clave, ultimo, res, None)
    assert len(apuntadas) == n and clave not in mastodon_service._citas_pendientes()


def test_revision_toot_borrado_no_reintenta(monkeypatch):
    apuntadas = _preparar(monkeypatch)
    clave = (ACC, "7005")
    mastodon_service._citas_pendientes()[clave] = 0
    res = {"error": "404", "definitivo": True, "db_id": f"{ACC}:7005"}
    mastodon_service._fin_revision_cita(clave, 0, res, None)
    assert not apuntadas and clave not in mastodon_service._citas_pendientes()


def test_revisar_guardadas_al_arrancar(monkeypatch):
    apuntadas = _preparar(monkeypatch)
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    mastodon_service._procesar_y_guardar_toot(ACC, _toot("7006", "pending", created_at=hoy))
    mastodon_service._procesar_y_guardar_toot(ACC, _toot("7007", "pending",
                                                          created_at="2020-01-01T00:00:00+00:00"))
    mastodon_service.__dict__["_citas_pend"] = {}
    apuntadas.clear()
    mastodon_service.revisar_citas_pendientes_guardadas(ACC)
    claves = [a[0] for a in apuntadas]
    assert (ACC, "7006") in claves, "la reciente debía revisarse"
    assert (ACC, "7007") not in claves, "la antigua (fuera de plazo) no"
