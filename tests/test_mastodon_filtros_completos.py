# -*- coding: utf-8 -*-
"""Filtros del servidor de Mastodon completos (auditoría 2026-10-17, M1-M14).

Sin red ni wx en primer plano: lógica pura de filtros_servidor, el servicio con un cliente
falso y la base de datos aislada de pruebas."""
import json

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
import app.services.mastodon.filtros_servidor as fs
import app.services.mastodon.mastodon_filtros as mf_mod
from app.services.mastodon.mastodon_service import mastodon_service

ACC = 991


def _filtered(fid, titulo, accion, contextos, expira=None):
    return {"filter": {"id": fid, "title": titulo, "filter_action": accion,
                       "context": contextos, "expires_at": expira},
            "keyword_matches": ["x"], "status_matches": None}


@pytest.fixture(autouse=True)
def _limpio():
    for conj in (fs._vigentes, fs._pedidos, fs._revelados):
        conj.clear()
    yield
    for conj in (fs._vigentes, fs._pedidos, fs._revelados):
        conj.clear()


@pytest.fixture
def sincrono(monkeypatch):
    """task_manager.start_task síncrono para los métodos *_async del servicio."""
    def _sinc(fn, callback=None, **kw):
        try:
            res, err = fn(None), None
        except Exception as e:
            res, err = None, e
        if callback:
            callback(res, err)
    monkeypatch.setattr(mf_mod.task_manager, "start_task", _sinc)


class _ClienteFalso:
    def __init__(self):
        self.llamadas = []

    def filters_v2(self):
        return [{"id": "7", "title": "Solo publicaciones", "keywords": [],
                 "statuses": [{"id": "s1", "status_id": "555"}], "filter_action": "warn",
                 "context": ["home"], "expires_at": None}]

    def update_filter_v2(self, filter_id, **kw):
        self.llamadas.append(("update", filter_id, kw))
        return {"id": filter_id}

    def create_filter_v2(self, title, context, action, **kw):
        self.llamadas.append(("create", title, context, action, kw))
        return {"id": "99"}

    def add_filter_status_v2(self, filter_id, status_id):
        self.llamadas.append(("add_status", filter_id, status_id))
        return {"id": "fs1"}

    def delete_filter_status_v2(self, fsid):
        self.llamadas.append(("del_status", fsid))


# ---------------------------------------------------------------- M1
def test_m1_filtro_sin_palabras_no_usa_el_titulo_como_palabra(monkeypatch, sincrono):
    cli = _ClienteFalso()
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda acc: cli)
    res = {}
    mastodon_service.obtener_filtros_servidor_async(ACC, callback=lambda r, e: res.update(r=r, e=e))
    assert res["e"] is None
    f = res["r"][0]
    assert f["phrase"] == "" and f["keywords"] == []
    assert f["statuses"] == [{"id": "s1", "status_id": "555"}]


def test_m1_editar_con_cero_palabras_en_v2(monkeypatch, sincrono):
    cli = _ClienteFalso()
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda acc: cli)
    res = {}
    mastodon_service.editar_filtro_servidor_async(
        ACC, "7", "", ["home"], title="Solo publicaciones", keywords=[],
        keywords_previas=[], es_v2=True, callback=lambda r, e: res.update(e=e))
    assert res["e"] is None
    _, fid, kw = cli.llamadas[0]
    assert fid == "7" and kw["title"] == "Solo publicaciones"
    assert kw["keywords_attributes"] is None          # no se inventa ninguna palabra


# ---------------------------------------------------------------- M2
def test_m2_caducidad_al_editar_mantener_y_quitar(monkeypatch, sincrono):
    cli = _ClienteFalso()
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda acc: cli)
    mastodon_service.editar_filtro_servidor_async(ACC, "7", "a", ["home"], expires_in=None,
                                                  keywords=["a"], es_v2=True)
    mastodon_service.editar_filtro_servidor_async(ACC, "7", "a", ["home"], expires_in="",
                                                  keywords=["a"], es_v2=True)
    assert cli.llamadas[0][2]["expires_in"] is None     # mantener: Mastodon.py no lo envía
    assert cli.llamadas[1][2]["expires_in"] == ""       # Nunca: el servidor la quita


def test_m2_mastodon_py_envia_cadena_vacia_y_descarta_none():
    from mastodon import Mastodon
    m = Mastodon(api_base_url="https://masto.test", access_token="x", version_check_mode="none")
    params = m._Mastodon__generate_params({"expires_in": "", "title": None}, for_json=True)
    assert params == {"expires_in": ""}


def test_m2_opciones_de_caducidad_del_formulario():
    from app.ui.mastodon_filtros_dialogs import NuevoFiltroServidorDialog as D
    crear = D.duraciones("", editando=False)
    assert crear[0][1] is None and (crear[0][0] == "Nunca")
    assert ("12 horas", 43200) in crear                       # M13
    con_cad = D.duraciones("2999-01-01T10:00:00Z", editando=True)
    assert con_cad[0][1] is None and con_cad[0][0].startswith("Mantener")
    assert con_cad[1][1] == ""                                 # Nunca = quitar
    sin_cad = D.duraciones("", editando=True)
    assert sin_cad[0] == ("Nunca", None)


# ---------------------------------------------------------------- M3
def test_m3_palabras_existentes_se_reenvian_con_id_y_whole_word():
    previas = [{"id": "k1", "keyword": "fútbol", "whole_word": True},
               {"id": "k2", "keyword": "liga", "whole_word": True}]
    attrs = fs.construir_keywords_attributes(["fútbol", "liga", "gol"], previas, False)
    assert {"id": "k1", "keyword": "fútbol", "whole_word": False} in attrs
    assert {"id": "k2", "keyword": "liga", "whole_word": False} in attrs
    assert {"keyword": "gol", "whole_word": False} in attrs


def test_m3_quitar_y_renombrar_por_id():
    previas = [{"id": "k1", "keyword": "a", "whole_word": True},
               {"id": "k2", "keyword": "b", "whole_word": False}]
    attrs = fs.construir_keywords_attributes(["a", "c"], previas, True)
    assert {"id": "k2", "keyword": "c", "whole_word": True} in attrs   # renombrada
    assert not any(a.get("_destroy") for a in attrs)
    attrs = fs.construir_keywords_attributes(["a"], previas, True)
    assert {"id": "k2", "_destroy": True} in attrs


def test_m3_whole_word_none_conserva_el_de_cada_palabra():
    previas = [{"id": "k1", "keyword": "a", "whole_word": True},
               {"id": "k2", "keyword": "b", "whole_word": False}]
    attrs = fs.construir_keywords_attributes(["a", "b", "n"], previas, None)
    assert {"id": "k1", "keyword": "a", "whole_word": True} in attrs
    assert {"id": "k2", "keyword": "b", "whole_word": False} in attrs
    assert {"keyword": "n", "whole_word": True} in attrs


# ---------------------------------------------------------------- M4
def test_m4_hashtag_guardado_oculta_y_busqueda_de_texto_avisa():
    fb = fs.serializar_filtrados([_filtered(1, "p", "hide", ["public"])])
    assert fs.evaluar(fb, 7, "search:#gatos")["accion"] == "hide"
    assert fs.evaluar(fb, 7, "search:gatos")["accion"] == "warn"


# ---------------------------------------------------------------- M5
def test_m5_warn_esconde_el_texto_hasta_revelarlo():
    fb = fs.serializar_filtrados([_filtered(2, "spoilers", "warn", ["home"])])
    ev = fs.evaluar(fb, 7, "home")
    segs = ["Impulsado de @x", "el final de la serie", "Enlace: algo"]
    oculto = fs.segmentos_lista(segs, ev, clave="7:1", n_fijos=1)
    assert oculto == ["Impulsado de @x", "[Filtrado: spoilers]"]
    assert fs.contenido_con_filtro("texto", ev, "7:1") == "[Filtrado: spoilers]"
    fs.revelar("7:1")
    visible = fs.segmentos_lista(segs, ev, clave="7:1", n_fijos=1)
    assert visible[0] == "[Filtro: spoilers]" and "el final de la serie" in visible
    assert fs.contenido_con_filtro("texto", ev, "7:1") == "[Filtro: spoilers] texto"
    assert fs.segmentos_lista(segs, ev, clave="7:2", n_fijos=1)[-1] == "[Filtrado: spoilers]"


def test_m5_componer_contenido_toot_aplica_el_filtro():
    from app.ui.mf_filtros_mastodon import componer_contenido_toot
    fb = fs.serializar_filtrados([_filtered(2, "spoilers", "warn", ["home"])])
    ev = fs.evaluar(fb, 7, "home")
    txt = componer_contenido_toot("cuerpo", "", "", "@a", None, "", "[]", ev, "7:5")
    assert txt == "[Filtrado: spoilers]"
    sin = componer_contenido_toot("cuerpo", "CW", "@b", "@a", None, "", "[]",
                                  fs.evaluar(None, 7, "home"), "7:5")
    assert sin == "Impulsado de @a: Advertencia de contenido: CW"


def test_m5_lineas_detalle():
    fb = fs.serializar_filtrados([_filtered(2, "s", "warn", ["home"]), _filtered(3, "m", "blur", ["home"])])
    lineas = fs.lineas_detalle(fs.evaluar(fb, 7, "home"))
    assert len(lineas) == 2 and "s" in lineas[0] and "m" in lineas[1]


class _ListaFalsa:
    def __init__(self, n):
        self.celdas, self.medios, self.n, self.releida = {}, {}, n, False

    def GetItemCount(self):
        return self.n

    def GetFocusedItem(self):
        return 0

    def SetItem(self, i, c, v):
        self.celdas[(i, c)] = v

    def establecer_medios(self, i, ev):
        self.medios[i] = ev

    def reanunciar_foco(self):
        self.releida = True


def test_m5_mostrar_de_todos_modos_actualiza_solo_su_fila():
    from app.ui.mf_filtros_mastodon import _Mf_filtros_mastodon
    _preparar_db()
    _guardar("8101", filtrado=[_filtered(2, "spoilers", "warn", ["home"])])
    _guardar("8102")

    class _Frame(_Mf_filtros_mastodon):
        active_mastodon_account_id = ACC
        active_timeline_type = "home"
        _cols_actuales = [("author", "Autor"), ("content", "Contenido")]
        lista_items_db_ids = [f"{ACC}:8101", f"{ACC}:8102"]

        def __init__(self):
            self.lista_items = _ListaFalsa(2)

        @staticmethod
        def _col_de_campo(cols, campo):
            return next((i for i, (c, _e) in enumerate(cols) if c == campo), None)

        def _es_lista_notif_mastodon(self):
            return False

    fr = _Frame()
    fr._on_mostrar_filtrado(f"{ACC}:8101")
    assert fr.lista_items.celdas == {(0, 1): "[Filtro: spoilers]: hola"}
    assert fr.lista_items.releida


# ---------------------------------------------------------------- M6
def test_m6_blur_protege_la_multimedia_hasta_revelarla():
    fb = fs.serializar_filtrados([_filtered(3, "gore", "blur", ["home"])])
    ev = fs.evaluar(fb, 7, "home")
    assert ev["accion"] is None and fs.media_filtrada(ev, "7:9")
    assert fs.evento_media_filtrado(ev, "media_image", "7:9") is None
    assert not fs.oculta_texto(ev, "7:9")                      # el texto se ve
    fs.revelar("7:9")
    assert not fs.media_filtrada(ev, "7:9")
    assert fs.evento_media_filtrado(ev, "media_image", "7:9") == "media_image"


def test_m6_warn_sin_revelar_tambien_protege_la_multimedia():
    fb = fs.serializar_filtrados([_filtered(2, "s", "warn", ["home"])])
    assert fs.media_filtrada(fs.evaluar(fb, 7, "home"), "7:3")


# ---------------------------------------------------------------- M7
def _preparar_db():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'mastodon', 'prueba', 1, 98)", (ACC,))


def _guardar(sid, filtrado=None, reblog_of=None, timeline="home"):
    raw = {"id": sid, "uri": f"https://masto.test/s/{sid}", "content": "<p>hola</p>",
           "created_at": "2026-10-17T10:00:00+00:00", "visibility": "public",
           "account": {"id": "50", "display_name": "A", "username": "a", "acct": "a@masto.test"}}
    if filtrado:
        raw["filtered"] = filtrado
    mastodon_service._procesar_y_guardar_toot(ACC, raw)
    if reblog_of:
        db.execute("UPDATE mastodon_statuses SET reblog_of_id = ? WHERE id = ?", (reblog_of, f"{ACC}:{sid}"))
    db.execute("INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) "
               "VALUES (?, ?, ?)", (ACC, timeline, f"{ACC}:{sid}"))


def test_m7_filtrar_publicacion_usa_el_id_original(monkeypatch, sincrono):
    _preparar_db()
    _guardar("8001", reblog_of="7000")
    cli = _ClienteFalso()
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda acc: cli)
    res = {}
    mastodon_service.filtrar_publicacion_async(ACC, "7", f"{ACC}:8001", callback=lambda r, e: res.update(e=e))
    assert res["e"] is None
    assert cli.llamadas[-1] == ("add_status", "7", "7000")


def test_m7_crear_filtro_para_publicacion(monkeypatch, sincrono):
    _preparar_db()
    _guardar("8002")
    cli = _ClienteFalso()
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda acc: cli)
    monkeypatch.setattr(mastodon_service, "_usar_filtros_v2", lambda *a, **k: True)
    res = {}
    mastodon_service.crear_filtro_para_publicacion_async(ACC, "Pesados", f"{ACC}:8002",
                                                         callback=lambda r, e: res.update(e=e))
    assert res["e"] is None
    crear = cli.llamadas[0]
    assert crear[0] == "create" and crear[1] == "Pesados" and crear[3] == "warn"
    assert set(crear[2]) == {"home", "notifications", "public", "thread", "account"}
    assert cli.llamadas[1] == ("add_status", "99", "8002")


# ---------------------------------------------------------------- M8
def test_m8_evaluar_raw_hilo_y_propios():
    raw = {"id": "1", "account": {"id": "50"},
           "filtered": [_filtered(4, "p", "hide", ["thread"])]}
    assert fs.evaluar_raw(raw, 7, "thread")["accion"] == "hide"
    assert fs.evaluar_raw(raw, 7, "thread", foco_detalle=True)["accion"] == "warn"
    assert fs.evaluar_raw(raw, 7, "thread", me_id="50")["accion"] is None   # propio
    impulso = {"id": "2", "account": {"id": "60"}, "reblog": raw}
    assert fs.evaluar_raw(impulso, 7, "thread")["accion"] == "hide"


def test_m8_datos_frescos_no_los_tapa_una_lista_vigente_vieja():
    fs.actualizar_vigentes(7, [])           # lista vigente sin el filtro recién creado
    raw = {"id": "1", "account": {"id": "50"}, "filtered": [_filtered(4, "p", "hide", ["account"])]}
    assert fs.evaluar_raw(raw, 7, "user:@x")["accion"] == "hide"


# ---------------------------------------------------------------- M9
def test_m9_recalcular_incluye_los_ocultos_del_buffer():
    _preparar_db()
    fb = [_filtered(5, "p", "hide", ["home"])]
    _guardar("9001", filtrado=fb)
    _guardar("9002")
    ids = fs.ids_para_recalcular(ACC, "home", [f"{ACC}:9002", "otra:1"])
    assert f"{ACC}:9002" in ids and f"{ACC}:9001" in ids and "otra:1" not in ids


# ---------------------------------------------------------------- M13
def test_m13_caducidad_legible_y_caducados():
    assert fs.describir_caducidad("") == "Nunca"
    assert "(caducado)" in fs.describir_caducidad("2000-01-01T00:00:00Z")
    txt = fs.describir_caducidad("2999-05-01T10:00:00Z")
    assert "2999" in txt and "T" not in txt and "(caducado)" not in txt


# ---------------------------------------------------------------- M14
def test_m14_cita_filtrada():
    from app.ui.ui_helpers import formato_cita
    filtrado_hide = fs.serializar_filtrados([_filtered(6, "pol", "hide", ["home"])])
    q = json.dumps({"state": "accepted", "acct": "b", "name": "B", "content": "secreto",
                    "filtered": filtrado_hide})
    assert "Cita oculta por tu filtro: pol" == formato_cita(q, account_id=7, timeline="home")
    assert "secreto" in formato_cita(q)              # sin cuenta: como siempre
    filtrado_warn = fs.serializar_filtrados([_filtered(6, "pol", "warn", ["home"])])
    q2 = json.dumps({"state": "accepted", "acct": "b", "name": "B", "content": "secreto",
                     "filtered": filtrado_warn})
    txt = formato_cita(q2, account_id=7, timeline="home", clave="7:1")
    assert "secreto" not in txt and "[Filtrado: pol]" in txt
    assert fs.cita_filtrada(q2, 7, "home")
    fs.revelar("7:1")
    assert "secreto" in formato_cita(q2, account_id=7, timeline="home", clave="7:1")


def test_m14_se_guarda_el_filtered_de_la_citada():
    _preparar_db()
    raw = {"id": "9101", "uri": "https://masto.test/s/9101", "content": "<p>cito</p>",
           "created_at": "2026-10-17T10:00:00+00:00", "visibility": "public",
           "account": {"id": "50", "display_name": "A", "username": "a", "acct": "a@masto.test"},
           "quote": {"state": "accepted", "quoted_status": {
               "id": "9100", "content": "<p>citado</p>",
               "account": {"id": "70", "acct": "c", "display_name": "C"},
               "filtered": [_filtered(8, "x", "warn", ["home"])]}}}
    mastodon_service._procesar_y_guardar_toot(ACC, raw)
    qj = db.query_one("SELECT quote_json FROM mastodon_statuses WHERE id = ?", (f"{ACC}:9101",))[0]
    assert json.loads(qj).get("filtered")
