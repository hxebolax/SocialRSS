# -*- coding: utf-8 -*-
"""Novedades de Mastodon hasta 4.7.2, sin tocar un servidor real: colecciones (4.6), estados de
cuenta (invalid_handle de 4.7), estados de cita, prefijos de notificación, perfil sin recorte de
campos y con texto alternativo del avatar, filtros "blur" y política de citas al publicar."""
import json

import app.services.mastodon.mastodon_colecciones as col_mod
import app.services.mastodon.mastodon_perfil as perfil_mod
from app.services.mastodon.mastodon_service import MastodonService
from app.services.mastodon.mastodon_estado_cuenta import describir_estado_cuenta, campos_de_perfil


class _Ctx:
    def report_progress(self, *a, **k):
        pass

    def is_cancelled(self):
        return False


def _tareas_inmediatas(monkeypatch, *modulos):
    def ejecutar(func, *args, callback=None, pass_context=True, **kwargs):
        try:
            resultado, error = (func(_Ctx(), *args) if pass_context else func(*args)), None
        except Exception as exc:
            resultado, error = None, exc
        if callback:
            callback(resultado, error)
        return "test-task"
    for m in modulos:
        monkeypatch.setattr(m.task_manager, "start_task", ejecutar)


def _capturar(llamada):
    caja = {}
    llamada(lambda res, err: caja.update(res=res, err=err))
    return caja.get("res"), caja.get("err")


class _ClienteGrabador:
    """Cliente falso: graba las peticiones directas y devuelve respuestas preparadas."""

    def __init__(self, respuestas=None):
        self.peticiones = []
        self.respuestas = respuestas or {}
        self.credenciales = None

    def _Mastodon__api_request(self, metodo, ruta, params=None, use_json=False, **kw):
        self.peticiones.append((metodo, ruta, dict(params or {}), use_json))
        return self.respuestas.get((metodo, ruta), {})

    def account_lookup(self, acct):
        return {"id": "77", "acct": acct, "feature_approval": {"current_user": "automatic"}}

    def account_verify_credentials(self):
        return {"id": "1"}

    def account_update_credentials(self, **kw):
        self.credenciales = kw
        return {}


def _servicio(monkeypatch, cliente, api=10):
    s = MastodonService()
    monkeypatch.setattr(s, "obtener_cliente", lambda _id: cliente)
    monkeypatch.setattr(s, "capacidades", lambda _id: {"api_version": api, "max_profile_fields": 6})
    return s


# --------------------------------------------------------------------------- colecciones
def test_coleccion_a_dict_y_listados_envueltos():
    c = col_mod.coleccion_a_dict({
        "id": 5, "account_id": "1", "name": "Accesibilidad", "description": "Gente que sabe",
        "language": "es", "discoverable": True, "tag": {"name": "a11y"}, "item_count": 2,
        "items": [{"id": "9", "account_id": "77", "state": "pending"}], "url": "https://x/c/5"})
    assert c["id"] == "5" and c["tag"] == "a11y" and c["items"][0]["state"] == "pending"
    assert col_mod._lista_de({"collections": [1, 2]}) == [1, 2]
    assert col_mod._lista_de({"collections:": [3]}) == [3]  # errata de la documentación


def test_validacion_de_limites_oficiales():
    assert col_mod.validar_datos_coleccion({"name": ""})
    assert col_mod.validar_datos_coleccion({"name": "x" * 41})
    assert col_mod.validar_datos_coleccion({"name": "ok", "description": "d" * 101})
    assert col_mod.validar_datos_coleccion({"name": "ok", "description": "d" * 100}) is None


def test_crear_coleccion_envia_json_con_cuentas_y_etiqueta(monkeypatch):
    _tareas_inmediatas(monkeypatch, col_mod)
    cli = _ClienteGrabador({("POST", "/api/v1/collections"): {"collection": {"id": "5", "name": "A"}}})
    s = _servicio(monkeypatch, cli)
    res, err = _capturar(lambda cb: s.crear_coleccion_async(
        1, {"name": "A", "description": "d", "tag_name": "a11y", "discoverable": True,
            "sensitive": False}, accts=["ana@x.social"], callback=cb))
    assert err is None and res["id"] == "5"
    metodo, ruta, cuerpo, use_json = cli.peticiones[-1]
    assert (metodo, ruta, use_json) == ("POST", "/api/v1/collections", True)
    assert cuerpo["tag_name"] == "#a11y" and cuerpo["account_ids"] == ["77"]


def test_colecciones_rechaza_servidor_antiguo(monkeypatch):
    _tareas_inmediatas(monkeypatch, col_mod)
    s = _servicio(monkeypatch, _ClienteGrabador(), api=8)
    res, err = _capturar(lambda cb: s.colecciones_de_cuenta_async(1, callback=cb))
    assert res is None and "4.6" in str(err)


def test_anadir_cuenta_respeta_feature_approval(monkeypatch):
    _tareas_inmediatas(monkeypatch, col_mod)
    cli = _ClienteGrabador({("POST", "/api/v1/collections/5/items"):
                            {"collection_item": {"id": "9", "state": "pending"}}})
    s = _servicio(monkeypatch, cli)
    res, err = _capturar(lambda cb: s.anadir_a_coleccion_async(1, "5", "ana", callback=cb))
    assert err is None and res == {"estado": "pending", "item_id": "9"}
    assert cli.peticiones[-1][2] == {"account_id": "77"}
    # Una cuenta que no permite ser destacada: se avisa sin llamar al servidor.
    cli.account_lookup = lambda acct: {"id": "78", "feature_approval": {"current_user": "denied"}}
    antes = len(cli.peticiones)
    res, err = _capturar(lambda cb: s.anadir_a_coleccion_async(1, "5", "bea", callback=cb))
    assert res is None and err is not None and len(cli.peticiones) == antes


def test_retirarme_y_donde_estoy(monkeypatch):
    _tareas_inmediatas(monkeypatch, col_mod)
    cli = _ClienteGrabador({("GET", "/api/v1/accounts/1/in_collections"): {"collections": [
        {"id": "5", "name": "A", "items": [{"id": "3", "account_id": "1", "state": "accepted"}]}]}})
    s = _servicio(monkeypatch, cli)
    res, err = _capturar(lambda cb: s.colecciones_donde_estoy_async(1, callback=cb))
    assert err is None and res[0]["mi_item"]["id"] == "3"
    _capturar(lambda cb: s.retirarme_de_coleccion_async(1, "5", "3", callback=cb))
    assert cli.peticiones[-1][:2] == ("POST", "/api/v1/collections/5/items/3/revoke")


# --------------------------------------------------------------------------- estados y citas
def test_estado_de_cuenta_incluye_invalid_handle_y_mudanza():
    avisos = describir_estado_cuenta({"moved": {"acct": "nueva@otro.social"}, "invalid_handle": True,
                                      "limited": True, "memorial": True})
    texto = " ".join(avisos)
    assert "nueva@otro.social" in texto and "provisional" in texto
    assert "limitada" in texto and "memoria" in texto
    assert describir_estado_cuenta({}) == []
    assert campos_de_perfil({"fields": [{"name": "Web", "value": "<a>x.es</a>",
                                         "verified_at": "2026-01-01"}]}) == [("Web", "x.es", True)]


def test_cita_no_aceptada_se_explica():
    from app.ui.ui_helpers import formato_cita
    assert "pendiente" in formato_cita(json.dumps({"state": "pending"}))
    assert "borrado" in formato_cita(json.dumps({"state": "deleted"}))
    assert formato_cita(json.dumps({"state": "accepted", "acct": "a", "name": "A", "content": "hola"})).endswith("hola")
    # Citas guardadas antes (sin estado) siguen leyéndose como aceptadas.
    assert "hola" in formato_cita(json.dumps({"acct": "a", "name": "A", "content": "hola"}))


def test_prefijos_de_notificacion_unificados():
    from app.ui.ui_helpers import prefijo_notificacion_mastodon as p
    assert p("quote") == "[Cita]"
    assert "colección" in p("added_to_collection").lower()
    assert p("tipo_futuro") == "[TIPO_FUTURO]"


# --------------------------------------------------------------------------- perfil
def test_perfil_no_recorta_campos_y_envia_texto_alternativo(monkeypatch):
    _tareas_inmediatas(monkeypatch, perfil_mod)
    cli = _ClienteGrabador()
    s = _servicio(monkeypatch, cli)
    campos = [{"name": f"c{i}", "value": f"v{i}"} for i in range(6)]
    res, err = _capturar(lambda cb: s.editar_mi_perfil_async(
        1, "Nombre", "Bio", fields=campos, callback=cb,
        opciones={"default_quote_policy": "followers", "indexable": True, "default_language": None},
        descripciones={"avatar_description": "Foto de perfil: yo sonriendo"}))
    assert err is None
    assert len(cli.credenciales["fields"]) == 6  # antes se recortaba a 4 y se perdían
    assert cli.credenciales["default_quote_policy"] == "followers"
    assert "default_language" not in cli.credenciales
    assert cli.peticiones[-1][:2] == ("PATCH", "/api/v1/profile")
    assert cli.peticiones[-1][2] == {"avatar_description": "Foto de perfil: yo sonriendo"}


# --------------------------------------------------------------------------- filtros y citas al publicar
def test_filtro_blur_se_conserva():
    from app.ui.mastodon_lists import NuevoFiltroServidorDialog
    assert NuevoFiltroServidorDialog._ACCIONES_FILTRO == ("warn", "hide", "blur")


def test_publicar_envia_politica_de_citas_solo_en_publicas(monkeypatch):
    import app.services.mastodon.mastodon_composer as comp_mod
    llamadas = []

    class _Cli:
        def status_post(self, **kw):
            llamadas.append(kw)
            return {"id": "1"}

    monkeypatch.setattr(comp_mod.mastodon_service, "obtener_cliente", lambda _id: _Cli())

    def ejecutar(func, *args, callback=None, pass_context=True, **kwargs):
        try:
            res, err = func(_Ctx()), None
        except Exception as exc:
            res, err = None, exc
        if callback:
            callback(res, err)
    monkeypatch.setattr(comp_mod.task_manager, "start_task", ejecutar)
    # La programada evita guardar en la caché local (el test solo mira lo enviado).
    comp_mod.MastodonComposer.publicar_toot_async(1, "hola", visibility="public", scheduled_at="x",
                                                  quote_approval_policy="nobody")
    comp_mod.MastodonComposer.publicar_toot_async(1, "hola", visibility="private", scheduled_at="x",
                                                  quote_approval_policy="nobody")
    assert llamadas[0].get("quote_approval_policy") == "nobody"
    assert "quote_approval_policy" not in llamadas[1]
