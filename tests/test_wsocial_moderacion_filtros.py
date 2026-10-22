# -*- coding: utf-8 -*-
"""W Social: etiquetadores y preferencias de moderación (auditoría de filtros 2026-10-17).

- La cabecera atproto-accept-labelers se QUITA al darse de baja del último etiquetador (antes se
  quedaba la vieja) y los suscritos del servidor (labelersPref) se unen a los locales.
- Fijar qué hacer con una etiqueta de un etiquetador no borra la de otro etiquetador.
- Reescribir las palabras silenciadas conserva su id.

Sin red: cliente falso."""
import types

from app.core.config_manager import config
from app.core.task_manager import task_manager
from app.services.wsocial.wsocial_moderacion import (
    aplicar_cabecera_labelers, dids_de_labelers_pref)
from app.services.wsocial.wsocial_service import WSocialService

ACC = 984


class _Peticion:
    def __init__(self):
        self._additional_headers = {}

    def add_additional_header(self, nombre, valor):
        self._additional_headers[nombre] = valor


class _Cliente:
    def __init__(self, prefs=None):
        self.request = _Peticion()
        self.prefs = list(prefs or [])
        self.guardadas = None
        self.app = types.SimpleNamespace(bsky=types.SimpleNamespace(actor=types.SimpleNamespace(
            get_preferences=lambda: {"preferences": self.prefs},
            put_preferences=self._put)))

    def _put(self, datos):
        self.guardadas = datos["preferences"]
        self.prefs = list(self.guardadas)


def _sincrono(monkeypatch):
    def _run(fn, callback=None, **kw):
        try:
            res, err = fn(None), None
        except Exception as e:     # pragma: no cover
            res, err = None, e
        if callback:
            callback(res, err)
    monkeypatch.setattr(task_manager, "start_task", _run)


def test_cabecera_se_quita_sin_suscritos():
    c = _Cliente()
    aplicar_cabecera_labelers(c, ["did:plc:uno", "did:plc:dos", "no-es-did"])
    assert c.request._additional_headers["atproto-accept-labelers"] == "did:plc:uno,did:plc:dos"
    aplicar_cabecera_labelers(c, [])
    assert "atproto-accept-labelers" not in c.request._additional_headers


def test_labelers_del_servidor_se_unen_a_los_locales(monkeypatch):
    mapa = dict(config.get("wsocial", "labelers_suscritos", {}) or {})
    mapa[str(ACC)] = ["did:plc:local"]
    config.set("wsocial", "labelers_suscritos", mapa)
    try:
        prefs = [{"$type": "app.bsky.actor.defs#labelersPref",
                  "labelers": [{"did": "did:plc:servidor"}, {"did": "did:plc:local"}]}]
        assert dids_de_labelers_pref({"preferences": prefs}) == ["did:plc:servidor", "did:plc:local"]
        c = _Cliente(prefs)
        todos = WSocialService().sincronizar_labelers_servidor(ACC, c)
        assert todos == ["did:plc:local", "did:plc:servidor"]
        assert config.get("wsocial", "labelers_suscritos", {})[str(ACC)] == todos
        assert "did:plc:servidor" in c.request._additional_headers["atproto-accept-labelers"]
    finally:
        mapa = dict(config.get("wsocial", "labelers_suscritos", {}) or {})
        mapa.pop(str(ACC), None)
        config.set("wsocial", "labelers_suscritos", mapa)


def test_visibilidad_de_etiqueta_respeta_otros_etiquetadores(monkeypatch):
    _sincrono(monkeypatch)
    otra = {"$type": "app.bsky.actor.defs#contentLabelPref", "label": "spoiler",
            "labelerDid": "did:plc:otro", "visibility": "hide"}
    misma = {"$type": "app.bsky.actor.defs#contentLabelPref", "label": "spoiler",
             "labelerDid": "did:plc:este", "visibility": "warn"}
    c = _Cliente([otra, misma])
    s = WSocialService()
    monkeypatch.setattr(s, "obtener_cliente", lambda acc: c)
    s.fijar_visibilidad_etiqueta_async(ACC, "spoiler", "ignore", labeler_did="did:plc:este",
                                       callback=lambda ok, r: None)
    assert otra in c.guardadas and misma not in c.guardadas
    nueva = c.guardadas[-1]
    assert nueva.label == "spoiler" and nueva.visibility == "ignore"
    assert nueva.labeler_did == "did:plc:este"


def test_palabras_silenciadas_conservan_id(monkeypatch):
    _sincrono(monkeypatch)
    c = _Cliente()
    s = WSocialService()
    monkeypatch.setattr(s, "obtener_cliente", lambda acc: c)
    s.fijar_palabras_silenciadas_async(
        ACC, [{"id": "3kpal", "valor": "spoiler", "ambitos": ["content"], "caduca": "",
               "solo_desconocidos": True}], callback=lambda ok, r: None)
    pref = c.guardadas[-1]
    palabra = pref.items[0]
    assert palabra.id == "3kpal" and palabra.value == "spoiler"
    assert palabra.actor_target == "exclude-following"


def _controlador_carga(acc):
    from app.ui.wsocial_controller_carga import _CargaMixin
    c = _CargaMixin()
    c.f = types.SimpleNamespace(active_wsocial_account_id=acc)
    return c


def test_palabras_silenciadas_se_cachean_y_se_aplican_al_pintar(monkeypatch):
    _sincrono(monkeypatch)
    c = _Cliente()
    s = WSocialService()
    monkeypatch.setattr(s, "obtener_cliente", lambda acc: c)
    s.fijar_palabras_silenciadas_async(
        ACC, [{"valor": "spoiler", "ambitos": ["content", "tag"]},
              {"valor": "fútbol", "ambitos": ["tag"]}], callback=lambda ok, r: None)
    try:
        cache = config.get("wsocial", "palabras_silenciadas", {})[str(ACC)]
        assert [w["value"] for w in cache] == ["spoiler", "fútbol"]
        ctrl = _controlador_carga(ACC)
        assert ctrl._palabra_silenciada("Ojo, spoiler del final", "", "es", "x") == "spoiler"
        # "fútbol" solo como etiqueta: el texto suelto no casa, el #hashtag sí.
        assert ctrl._palabra_silenciada("Hoy hay fútbol", "", "es", "x") == ""
        assert ctrl._palabra_silenciada("Hoy hay #fútbol", "", "es", "x") == "fútbol"
        # También en el texto alternativo de una imagen.
        media = '[{"type": "image", "alt": "captura con spoiler"}]'
        assert ctrl._palabra_silenciada("Mirad esto", media, "es", "x") == "spoiler"
        assert ctrl._palabra_silenciada("Nada que ver", media.replace("spoiler", "gato"),
                                        "es", "x") == ""
    finally:
        mapa = dict(config.get("wsocial", "palabras_silenciadas", {}) or {})
        mapa.pop(str(ACC), None)
        config.set("wsocial", "palabras_silenciadas", mapa)


def test_excepto_cuentas_que_sigo(monkeypatch):
    from app.core.database import db
    from app.core.migrations import ejecutar_migraciones
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('wsocial', 'W Social', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) "
               "VALUES (?, 'W', 'wsocial', 1)", (ACC,))
    db.execute("DELETE FROM wsocial_actors WHERE account_id = ?", (ACC,))
    db.execute("INSERT INTO wsocial_actors (id, account_id, bucket, did, handle) "
               "VALUES (?, ?, 'follows', 'did:plc:amiga', 'amiga.wsocial.eu')",
               (f"{ACC}:follows:did:plc:amiga", ACC))
    mapa = dict(config.get("wsocial", "palabras_silenciadas", {}) or {})
    mapa[str(ACC)] = [{"value": "spoiler", "targets": ["content", "tag"],
                       "actorTarget": "exclude-following", "expiresAt": "", "id": ""}]
    config.set("wsocial", "palabras_silenciadas", mapa)
    try:
        ctrl = _controlador_carga(ACC)
        assert ctrl._palabra_silenciada("spoiler", "", "es", "amiga.wsocial.eu") == ""
        assert ctrl._palabra_silenciada("spoiler", "", "es", "otra.wsocial.eu") == "spoiler"
    finally:
        mapa.pop(str(ACC), None)
        config.set("wsocial", "palabras_silenciadas", mapa)
