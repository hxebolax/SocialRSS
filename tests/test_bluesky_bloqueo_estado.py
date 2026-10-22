# -*- coding: utf-8 -*-
"""Bluesky: al bloquear (o seguir/silenciar) la caché de actores refleja el estado REAL.

Petición de usuario: "Acabo de bloquear a una persona y no me dice que esté bloqueado [...] en
las opciones de sus mensajes lo único que ofrece es bloquear". El bloqueo llegaba al servidor,
pero la caché local no se enteraba y el menú seguía diciendo "Bloquear autor".

Sin red: cliente falso y SQLite aislado."""
import types

import app.services.bluesky.bluesky_publicar as pub_mod
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.bluesky.bluesky_service import BlueskyService

ACC = 982
DID = "did:plc:bloqueada"
URI_BLOQUEO = "at://did:plc:yo/app.bsky.graph.block/3kabc"


class _Repo:
    def __init__(self, estado):
        self.estado = estado
        self.borrados = []

    def create_record(self, data):
        self.estado["blocking"] = URI_BLOQUEO
        return types.SimpleNamespace(uri=URI_BLOQUEO)

    def delete_record(self, data):
        self.borrados.append(data.rkey)
        self.estado["blocking"] = ""


class _Cliente:
    def __init__(self):
        self.estado = {"blocking": "", "following": "", "muted": False}
        self.repo = _Repo(self.estado)
        self.com = types.SimpleNamespace(atproto=types.SimpleNamespace(repo=self.repo))

    def get_profile(self, did):
        return {"did": did, "handle": "persona.bsky.social", "displayName": "Persona",
                "viewer": {"blocking": self.estado["blocking"] or None,
                           "following": self.estado["following"] or None,
                           "muted": self.estado["muted"]}}

    def mute(self, did):
        self.estado["muted"] = True

    def unmute(self, did):
        self.estado["muted"] = False


def _servicio(monkeypatch, cliente):
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('bluesky', 'Bluesky', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'B', 'bluesky', 1)", (ACC,))
    db.execute("DELETE FROM bluesky_actors WHERE account_id = ?", (ACC,))
    s = BlueskyService()
    monkeypatch.setattr(s, "obtener_cliente", lambda acc: cliente)
    monkeypatch.setattr(s, "_yo", lambda acc: {"did": "did:plc:yo"})

    def _sincrono(fn, callback=None, **kw):
        try:
            res, err = fn(None), None
        except Exception as e:     # pragma: no cover - el test lo comprobaría
            res, err = None, e
        if callback:
            callback(res, err)
    monkeypatch.setattr(pub_mod.task_manager, "start_task", _sincrono)
    return s


def _estado():
    return db.query_one(
        "SELECT MAX(CASE WHEN COALESCE(blocking_uri,'') != '' THEN 1 ELSE 0 END), MAX(COALESCE(muted,0)) "
        "FROM bluesky_actors WHERE account_id = ? AND did = ?", (ACC, DID))


def test_bloquear_cuenta_no_cacheada_queda_bloqueada_y_en_bloqueados(monkeypatch):
    cliente = _Cliente()
    s = _servicio(monkeypatch, cliente)
    salida = {}
    s.accion_cuenta_async(ACC, DID, "block", callback=lambda r, e: salida.update(r=r, e=e))
    assert salida["e"] is None
    assert salida["r"]["blocking_uri"] == URI_BLOQUEO and salida["r"]["handle"] == "persona.bsky.social"
    assert _estado()[0] == 1
    fila = db.query_one("SELECT handle FROM bluesky_actors WHERE id = ?", (f"{ACC}:blocks:{DID}",))
    assert fila and fila[0] == "persona.bsky.social"


def test_desbloquear_sin_uri_cacheado_lo_pide_al_perfil(monkeypatch):
    cliente = _Cliente()
    cliente.estado["blocking"] = URI_BLOQUEO   # bloqueada desde otra app: la caché no lo sabe
    s = _servicio(monkeypatch, cliente)
    salida = {}
    s.accion_cuenta_async(ACC, DID, "unblock", callback=lambda r, e: salida.update(r=r, e=e))
    assert salida["e"] is None
    assert cliente.repo.borrados == ["3kabc"]
    assert not db.query_one("SELECT 1 FROM bluesky_actors WHERE id = ?", (f"{ACC}:blocks:{DID}",))


def test_silenciar_actualiza_el_estado(monkeypatch):
    cliente = _Cliente()
    s = _servicio(monkeypatch, cliente)
    s.accion_cuenta_async(ACC, DID, "mute", callback=lambda r, e: None)
    assert _estado()[1] == 1
    s.accion_cuenta_async(ACC, DID, "unmute", callback=lambda r, e: None)
    assert not db.query_one("SELECT 1 FROM bluesky_actors WHERE id = ?", (f"{ACC}:mutes:{DID}",))
