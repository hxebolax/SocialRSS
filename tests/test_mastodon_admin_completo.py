# -*- coding: utf-8 -*-
"""Contratos de la administración Mastodon accesible, sin tocar un servidor real."""
import app.services.mastodon.mastodon_admin as admin_mod
from app.services.mastodon.mastodon_service import MastodonService


class _Ctx:
    pass


def _tareas_inmediatas(monkeypatch):
    def ejecutar(func, *args, callback=None, pass_context=True, **kwargs):
        try:
            resultado = func(_Ctx(), *args) if pass_context else func(*args)
            error = None
        except Exception as exc:  # el callback de producción recibe el error, no lo relanza
            resultado, error = None, exc
        if callback:
            callback(resultado, error)
        return "test-task"
    monkeypatch.setattr(admin_mod.task_manager, "start_task", ejecutar)


def _capturar(llamada):
    caja = {}
    llamada(lambda res, err: caja.update(res=res, err=err))
    assert caja["err"] is None
    return caja["res"]


def test_serializa_cuenta_admin_completa():
    dato = admin_mod._serializar_cuenta_admin({
        "id": "12", "username": "ana", "domain": None, "email": "ana@example.test",
        "confirmed": True, "approved": True, "sensitized": True, "locale": "es",
        "role": {"name": "Moderación"}, "ips": [{"ip": "192.0.2.1", "used_at": "2026-07-01"}],
        "account": {"acct": "ana", "display_name": "Ana", "note": "<p>Bio</p>",
                    "statuses_count": 20, "followers_count": 4, "following_count": 5,
                    "fields": [{"name": "Web", "value": "<a>ejemplo</a>"}]},
    })
    assert dato["acct"] == "ana"
    assert dato["estado"] == "sensitive"
    assert dato["rol"] == "Moderación"
    assert dato["ips"][0]["ip"] == "192.0.2.1"
    assert dato["nota"] == "Bio"
    assert dato["campos"][0]["valor"] == "ejemplo"


def test_serializa_informe_con_contenido_y_reglas():
    dato = admin_mod._serializar_informe({
        "id": "9", "category": "violation", "comment": "Regla rota", "forwarded": True,
        "account": {"id": "1", "acct": "reporta"},
        "target_account": {"id": "2", "acct": "objetivo", "statuses_count": 7},
        "rules": [{"id": "3", "text": "No acoso"}],
        "statuses": [{"id": "4", "content": "<p>Contenido</p>", "spoiler_text": "Aviso",
                      "account": {"acct": "objetivo"}, "media_attachments": [{"id": "m"}]}],
    })
    assert dato["objetivo_id"] == "2"
    assert dato["reglas"] == [{"id": "3", "texto": "No acoso"}]
    assert dato["estados"][0]["contenido"] == "Contenido"
    assert dato["estados"][0]["adjuntos"] == 1


def test_busqueda_cuentas_usa_v2_y_todos_los_filtros(monkeypatch):
    _tareas_inmediatas(monkeypatch)

    class Cliente:
        def admin_accounts_v2(self, **kwargs):
            self.kwargs = kwargs
            return [{"id": "5", "username": "mod", "approved": True,
                     "account": {"acct": "mod", "display_name": "Mod"}}]

    cliente = Cliente()
    servicio = MastodonService()
    monkeypatch.setattr(servicio, "obtener_cliente", lambda _id: cliente)
    res = _capturar(lambda cb: servicio.admin_buscar_cuentas_async(
        1, {"origin": "local", "status": "active", "username": "mod",
            "display_name": "Mod", "email": "m@example.test", "ip": "192.0.2.2",
            "permissions": "staff", "by_domain": "example.test"},
        max_id="99", limit=50, callback=cb))
    assert cliente.kwargs == {
        "origin": "local", "status": "active", "username": "mod", "display_name": "Mod",
        "email": "m@example.test", "ip": "192.0.2.2", "permissions": "staff",
        "by_domain": "example.test", "max_id": "99", "limit": 50,
    }
    assert res["items"][0]["acct"] == "mod"


def test_bloqueo_dominio_envia_todos_los_parametros(monkeypatch):
    _tareas_inmediatas(monkeypatch)

    class Cliente:
        def admin_create_domain_block(self, domain, **kwargs):
            self.domain, self.kwargs = domain, kwargs
            return {"id": "1", "domain": domain, **kwargs}

    cliente = Cliente()
    servicio = MastodonService()
    monkeypatch.setattr(servicio, "obtener_cliente", lambda _id: cliente)
    datos = {"dominio": "bad.example", "severidad": "suspend", "rechazar_media": True,
             "rechazar_informes": True, "comentario_privado": "interno",
             "comentario_publico": "público", "ofuscar": True}
    res = _capturar(lambda cb: servicio.admin_guardar_bloqueo_dominio_async(1, datos, callback=cb))
    assert cliente.domain == "bad.example"
    assert cliente.kwargs == {
        "severity": "suspend", "reject_media": True, "reject_reports": True,
        "private_comment": "interno", "public_comment": "público", "obfuscate": True,
    }
    assert res["comentario_privado"] == "interno"


def test_endpoint_publico_no_envuelto_actualiza_informe(monkeypatch):
    _tareas_inmediatas(monkeypatch)

    class Cliente:
        def __init__(self):
            setattr(self, "_Mastodon__api_request", self.peticion)

        def peticion(self, metodo, ruta, params):
            self.recibido = (metodo, ruta, params)
            return {"id": "44", "category": params["category"], "account": {"acct": "a"},
                    "target_account": {"id": "2", "acct": "b"}, "rules": [], "statuses": []}

    cliente = Cliente()
    servicio = MastodonService()
    monkeypatch.setattr(servicio, "obtener_cliente", lambda _id: cliente)
    res = _capturar(lambda cb: servicio.admin_actualizar_informe_async(
        1, "44", "violation", ["7"], callback=cb))
    assert cliente.recibido == (
        "PUT", "/api/v1/admin/reports/44", {"category": "violation", "rule_ids": ["7"]})
    assert res["categoria"] == "violation"


def test_metricas_avanzadas_mapean_dominio_tag_y_series(monkeypatch):
    _tareas_inmediatas(monkeypatch)

    class Cliente:
        def admin_measures(self, inicio, fin, **kwargs):
            self.medidas = kwargs
            return [{"key": "active_users", "total": "10", "data": [{"date": inicio, "value": 4}]}]

        def admin_dimensions(self, inicio, fin, **kwargs):
            self.dimensiones = kwargs
            return [{"key": "languages", "data": [{"human_key": "Español", "value": 8}]}]

        def admin_retention(self, inicio, fin, frequency):
            self.frecuencia = frequency
            return [{"period": inicio, "frequency": frequency,
                     "data": [{"date": fin, "rate": 0.5, "value": 3}]}]

    cliente = Cliente()
    servicio = MastodonService()
    monkeypatch.setattr(servicio, "obtener_cliente", lambda _id: cliente)
    claves = {"active_users", "instance_reports", "tag_uses", "languages",
              "instance_languages", "tag_languages", "retention"}
    res = _capturar(lambda cb: servicio.admin_estadisticas_avanzadas_async(
        1, 30, claves, dominio="remote.test", tag_id="42", frecuencia="day", callback=cb))
    assert cliente.medidas["active_users"] is True
    assert cliente.medidas["instance_reports"] == "remote.test"
    assert cliente.medidas["tag_uses"] == "42"
    assert cliente.dimensiones["languages"] is True
    assert cliente.dimensiones["instance_languages"] == "remote.test"
    assert cliente.dimensiones["tag_languages"] == "42"
    assert res["medidas"][0]["serie"][0]["valor"] == 4
    assert res["retencion"][0]["datos"][0]["tasa"] == 0.5


def test_dialogos_admin_no_usan_checklistbox_sin_capa_accesible():
    from pathlib import Path
    ruta = Path(__file__).parents[1] / "app" / "ui" / "mastodon_admin_dialogs.py"
    codigo = ruta.read_text(encoding="utf-8")
    assert "wx.CheckListBox(" not in codigo
    assert codigo.count("AccessibleCheckListBox(") == 2
