# -*- coding: utf-8 -*-
"""W Social: al bloquear (o seguir/silenciar) la caché de actores refleja el estado REAL.

Mismo fallo que se arregló en Bluesky: el bloqueo llegaba al servidor, pero solo se hacía un
UPDATE de las filas que ya existían en wsocial_actors; si la cuenta no estaba cacheada, el menú
seguía ofreciendo "Bloquear". Ahora se lee el viewer del perfil y se añade/quita la fila del
bucket (blocks/mutes/follows).

Sin red: cliente falso y SQLite aislado."""
import types

from app.core.task_manager import task_manager
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.wsocial.wsocial_service import WSocialService
from app.ui.wsocial_controller_social import mensaje_estado_cuenta

ACC = 983
DID = "did:plc:bloqueadaws"
URI_BLOQUEO = "at://did:plc:yo/app.bsky.graph.block/3kws"
URI_FOLLOW = "at://did:plc:yo/app.bsky.graph.follow/3kwf"


class _Cliente:
    """Imita lo que usa W Social: client.com.atproto.repo y client.app.bsky.{actor,graph}."""

    def __init__(self):
        self.estado = {"blocking": "", "following": "", "muted": False}
        self.borrados = []
        self.perfil_falla = False
        self.com = types.SimpleNamespace(atproto=types.SimpleNamespace(
            repo=types.SimpleNamespace(create_record=self._crear, delete_record=self._borrar)))
        self.app = types.SimpleNamespace(bsky=types.SimpleNamespace(
            actor=types.SimpleNamespace(get_profile=self._perfil),
            graph=types.SimpleNamespace(mute_actor=self._mute, unmute_actor=self._unmute)))

    def _crear(self, data):
        if data.collection == "app.bsky.graph.block":
            self.estado["blocking"] = URI_BLOQUEO
            return types.SimpleNamespace(uri=URI_BLOQUEO)
        self.estado["following"] = URI_FOLLOW
        return types.SimpleNamespace(uri=URI_FOLLOW)

    def _borrar(self, data):
        self.borrados.append((data.collection, data.rkey))
        campo = "blocking" if data.collection == "app.bsky.graph.block" else "following"
        self.estado[campo] = ""

    def _perfil(self, params):
        if self.perfil_falla:
            raise RuntimeError("sin red")
        return {"did": params["actor"], "handle": "persona.wsocial.eu", "displayName": "Persona",
                "viewer": {"blocking": self.estado["blocking"] or None,
                           "following": self.estado["following"] or None,
                           "muted": self.estado["muted"]}}

    def _mute(self, params):
        self.estado["muted"] = True

    def _unmute(self, params):
        self.estado["muted"] = False


def _servicio(monkeypatch, cliente):
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('wsocial', 'W Social', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) "
               "VALUES (?, 'W', 'wsocial', 1)", (ACC,))
    db.execute("DELETE FROM wsocial_actors WHERE account_id = ?", (ACC,))
    s = WSocialService()
    monkeypatch.setattr(s, "obtener_cliente", lambda acc: cliente)
    monkeypatch.setattr(s, "_yo", lambda acc: {"did": "did:plc:yo"})

    def _sincrono(fn, callback=None, **kw):
        try:
            res, err = fn(None), None
        except Exception as e:     # pragma: no cover - el test lo comprobaría
            res, err = None, e
        if callback:
            callback(res, err)
    monkeypatch.setattr(task_manager, "start_task", _sincrono)
    return s


def _accion(s, accion):
    salida = {}
    s.accion_cuenta_async(ACC, DID, accion, callback=lambda ok, r: salida.update(ok=ok, r=r))
    return salida


def _fila(bucket):
    return db.query_one("SELECT handle, blocking_uri, following_uri, muted FROM wsocial_actors "
                        "WHERE id = ?", (f"{ACC}:{bucket}:{DID}",))


def test_bloquear_cuenta_no_cacheada_queda_bloqueada_y_en_bloqueados(monkeypatch):
    s = _servicio(monkeypatch, _Cliente())
    salida = _accion(s, "block")
    assert salida["ok"], salida
    assert salida["r"]["blocking_uri"] == URI_BLOQUEO
    assert salida["r"]["handle"] == "persona.wsocial.eu"
    fila = _fila("blocks")
    assert fila and fila[0] == "persona.wsocial.eu" and fila[1] == URI_BLOQUEO
    assert mensaje_estado_cuenta("block", salida["r"]) == "@persona.wsocial.eu está bloqueada."


def test_bloquear_actualiza_todas_las_filas_del_did(monkeypatch):
    s = _servicio(monkeypatch, _Cliente())
    db.execute("INSERT INTO wsocial_actors (id, account_id, bucket, did, handle) "
               "VALUES (?, ?, 'followers', ?, 'persona.wsocial.eu')", (f"{ACC}:followers:{DID}", ACC, DID))
    _accion(s, "block")
    assert _fila("followers")[1] == URI_BLOQUEO


def test_desbloquear_sin_uri_cacheado_lo_pide_al_perfil(monkeypatch):
    cliente = _Cliente()
    cliente.estado["blocking"] = URI_BLOQUEO   # bloqueada desde otra app: la caché no lo sabe
    s = _servicio(monkeypatch, cliente)
    salida = _accion(s, "unblock")
    assert salida["ok"], salida
    assert cliente.borrados == [("app.bsky.graph.block", "3kws")]
    assert not _fila("blocks")
    assert mensaje_estado_cuenta("unblock", salida["r"]) == "@persona.wsocial.eu ya no está bloqueada."


def test_seguir_y_silenciar_mueven_la_cuenta_de_bucket(monkeypatch):
    s = _servicio(monkeypatch, _Cliente())
    _accion(s, "follow")
    assert _fila("follows")[2] == URI_FOLLOW
    _accion(s, "mute")
    assert _fila("mutes")[3] == 1
    assert _fila("follows")[3] == 1          # el resto de filas también se entera
    _accion(s, "unmute")
    assert not _fila("mutes")
    _accion(s, "unfollow")
    assert not _fila("follows")


def test_sin_perfil_se_usa_lo_conocido(monkeypatch):
    cliente = _Cliente()
    cliente.perfil_falla = True
    s = _servicio(monkeypatch, cliente)
    salida = _accion(s, "block")
    assert salida["ok"], salida
    assert _fila("blocks")[1] == URI_BLOQUEO
    # Sin handle conocido, el mensaje no se inventa un nombre.
    assert mensaje_estado_cuenta("block", salida["r"]) == "La cuenta está bloqueada."


def test_mensaje_avisa_si_el_servidor_no_confirma():
    assert "no confirma" in mensaje_estado_cuenta("block", {"handle": "x", "blocking_uri": ""})
