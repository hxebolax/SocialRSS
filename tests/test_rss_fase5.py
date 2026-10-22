# -*- coding: utf-8 -*-
"""RSS, fase 5 del informe de BlindRSS (2026-09-26): las suscripciones también hacia el
servidor (alta, baja, renombrar, mover) y el artículo completo que da Miniflux. Servidor
simulado: sin red."""
import pytest

from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.rss import rss_sync_subs
from app.services.rss.rss_service import rss_service
from app.services.rss.rss_sync import GReaderClient, MinifluxClient

ACC = 7101


class _Resp:
    def __init__(self, datos=None, texto="", estado=200):
        self._d, self.text, self.status_code = datos, texto, estado

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Sesion:
    def __init__(self, respuestas):
        self.respuestas, self.pedidos = respuestas, []

    def _r(self, metodo, url, **kw):
        self.pedidos.append((metodo, url.split("/api/0/")[-1].split("/v1/")[-1], kw.get("data") or kw.get("json")))
        for clave, resp in self.respuestas.items():
            if clave in url and (clave.startswith(metodo + ":") is False):
                return resp(kw) if callable(resp) else resp
        return _Resp({})

    def get(self, url, **kw):
        return self._r("get", url, **kw)

    def post(self, url, **kw):
        return self._r("post", url, **kw)

    def put(self, url, **kw):
        return self._r("put", url, **kw)

    def delete(self, url, **kw):
        return self._r("delete", url, **kw)


def _greader(respuestas):
    c = GReaderClient("https://srv.test", "u", "p")
    c._sid, c._wtoken = "sid", "tok"
    ses = _Sesion(respuestas)
    c._s = lambda: ses
    return c, ses


def test_greader_alta_baja_renombrar_mover():
    lista = _Resp({"subscriptions": [{"id": "feed/42", "url": "https://a.test/rss"}]})
    c, ses = _greader({"subscription/list": lista})
    assert rss_sync_subs.greader_cambio(c, "alta", "https://n.test/rss", "Nuevo", "Tec")
    assert ses.pedidos[-1] == ("post", "subscription/edit", {
        "ac": "subscribe", "s": "feed/https://n.test/rss", "t": "Nuevo", "a": "user/-/label/Tec", "T": "tok"})
    assert rss_sync_subs.greader_cambio(c, "baja", "https://a.test/rss/")
    assert ses.pedidos[-1][2] == {"ac": "unsubscribe", "s": "feed/42", "T": "tok"}
    assert rss_sync_subs.greader_cambio(c, "renombrar", "https://a.test/rss", "Otro")
    assert ses.pedidos[-1][2] == {"ac": "edit", "s": "feed/42", "t": "Otro", "T": "tok"}
    assert rss_sync_subs.greader_cambio(c, "mover", "https://a.test/rss", carpeta="B", carpeta_anterior="A")
    assert ses.pedidos[-1][2] == {"ac": "edit", "s": "feed/42", "a": "user/-/label/B",
                                  "r": "user/-/label/A", "T": "tok"}
    # Un feed que el servidor no tiene: nada que hacer.
    assert rss_sync_subs.greader_cambio(c, "baja", "https://z.test/rss") is False


def test_miniflux_alta_con_carpeta_nueva_y_baja():
    c = MinifluxClient("https://mf.test", token="t")
    ses = _Sesion({
        "categories": lambda kw: _Resp([{"id": 1, "title": "General"}]) if kw.get("json") is None
        else _Resp({"id": 9}),
        "feeds/7": _Resp({}),
        "feeds": _Resp([{"id": 7, "feed_url": "https://a.test/rss"}]),
    })
    c._s = lambda: ses
    assert rss_sync_subs.miniflux_cambio(c, "alta", "https://n.test/rss", carpeta="Ciencia")
    assert ("post", "categories", {"title": "Ciencia"}) in ses.pedidos
    assert ses.pedidos[-1] == ("post", "feeds", {"feed_url": "https://n.test/rss", "category_id": 9})
    assert rss_sync_subs.miniflux_cambio(c, "baja", "https://a.test/rss")
    assert ses.pedidos[-1][0:2] == ("delete", "feeds/7")


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'R', 'rss', 1)", (ACC,))
    db.execute("DELETE FROM rss_feeds WHERE account_id = ?", (ACC,))
    db.execute("INSERT INTO rss_feeds (id, account_id, title, url, url_normalized) VALUES (?, ?, 'Uno', "
               "'https://uno.test/rss', 'https://uno.test/rss')", (ACC, ACC))
    yield
    config.set("rss_sync", str(ACC), {})


def test_solo_se_envia_si_esta_activado(cuenta, monkeypatch):
    enviados = []
    monkeypatch.setattr(rss_sync_subs, "greader_cambio", lambda c, *a: enviados.append(a) or True)
    monkeypatch.setattr(rss_service, "_cliente_de_cuenta", lambda acc: object())
    config.set("rss_sync", str(ACC), {"tipo": "greader", "activo": True})
    rss_service.cambio_suscripcion(ACC, "baja", "https://uno.test/rss", sincrono=True)
    assert enviados == []                        # desactivado por defecto
    rss_service.fijar_enviar_suscripciones(ACC, True)
    rss_service.cambio_suscripcion(ACC, "baja", "https://uno.test/rss", sincrono=True)
    assert enviados == [("baja", "https://uno.test/rss", "", "", "")]


def test_editar_feed_envia_renombre_y_cambio_de_direccion(cuenta, monkeypatch):
    llamadas = []
    monkeypatch.setattr(rss_service, "cambio_suscripcion",
                        lambda acc, accion, url, titulo="", carpeta="", carpeta_anterior="", sincrono=False:
                        llamadas.append((accion, url, titulo)))
    rss_service.editar_feed(ACC, titulo="Uno nuevo")
    assert llamadas == [("renombrar", "https://uno.test/rss", "Uno nuevo")]
    llamadas.clear()
    rss_service.editar_feed(ACC, url="https://uno.test/nuevo.xml")
    assert llamadas == [("baja", "https://uno.test/rss", ""), ("alta", "https://uno.test/nuevo.xml", "Uno nuevo")]


def test_texto_completo_de_miniflux(cuenta, monkeypatch):
    db.execute("INSERT OR REPLACE INTO rss_sync_items (account_id, article_id, item_id) VALUES (?, 'mf:1', '555')", (ACC,))
    config.set("rss_sync", str(ACC), {"tipo": "miniflux", "activo": True})
    monkeypatch.setattr(rss_service, "_cliente_de_cuenta", lambda acc: "cliente")
    monkeypatch.setattr(rss_sync_subs, "miniflux_texto_completo",
                        lambda c, eid: f"<p>entero {eid}</p>" if c == "cliente" else "")
    assert rss_service.texto_completo_del_servidor("mf:1") == "<p>entero 555</p>"
    config.set("rss_sync", str(ACC), {"tipo": "greader", "activo": True})
    assert rss_service.texto_completo_del_servidor("mf:1") == ""
