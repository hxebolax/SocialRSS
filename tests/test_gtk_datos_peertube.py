# -*- coding: utf-8 -*-
"""Datos de PeerTube para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import pytest

from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_peertube as dp  # noqa: E402

ACC = 9991


def _video(uuid, nombre, canal, cuenta, host="", dur=725, directo=0, nsfw=0, vid=1):
    db.execute("DELETE FROM peertube_videos WHERE id = ?", (f"{ACC}:{uuid}",))
    db.execute("INSERT INTO peertube_videos (id, account_id, uuid, short_uuid, video_id, name, description, channel_name, "
               "channel_display, account_name, account_host, instance_host, duration, views, likes, dislikes, nsfw, is_live, "
               "published_at, url, is_local, privacy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}:{uuid}", ACC, uuid, uuid[:6], vid, nombre, "Descripción larga", canal, canal.capitalize(), cuenta, host,
                "ejemplo.org", dur, 1500, 84, 2, nsfw, directo, "2026-10-27T10:00:00.000Z", f"https://ejemplo.org/w/{uuid[:6]}",
                0 if host else 1, 1))


def _mapa(seccion, uuids):
    db.execute("DELETE FROM peertube_video_buffers WHERE account_id = ? AND buffer = ?", (ACC, seccion))
    for pos, u in enumerate(uuids):
        db.execute("INSERT INTO peertube_video_buffers (account_id, buffer, video_db_id, position) VALUES (?,?,?,?)",
                   (ACC, seccion, f"{ACC}:{u}", pos))


@pytest.fixture
def cuenta(monkeypatch):
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('peertube', 'PeerTube', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) VALUES (?, 'yo@ejemplo.org', 'peertube', 1, 99)",
               (ACC,))
    db.execute("DELETE FROM peertube_videos WHERE account_id = ?", (ACC,))
    for clave in ("saved_searches", "hidden_sections", "gtk_buffers"):
        config.set("peertube", clave, [])
    config.set("peertube", "section_order", list(dp.SECCIONES))
    monkeypatch.setattr("app.services.peertube.peertube_service.nombre_usuario", lambda a: "yo")
    _video("aaaaaaaa-1", "Cómo funciona la federación", "frank", "frank", vid=11)
    _video("bbbbbbbb-2", "Accesibilidad", "ana", "ana", host="otro.net", dur=3700, vid=12)
    _video("cccccccc-3", "Mi vídeo", "yo", "yo", vid=13)
    _video("dddddddd-4", "Directo", "frank", "frank", dur=0, directo=1, nsfw=1, vid=14)
    _mapa("recientes", ["aaaaaaaa-1", "bbbbbbbb-2", "cccccccc-3", "dddddddd-4"])
    dp._CACHE.clear()
    return ACC


def _mia(raiz):
    return next(c for c in raiz.hijos if c.account_id == ACC)


def test_arbol_con_grupos(cuenta):
    mia = _mia(dp.construir_nodos())
    assert [h.timeline for h in mia.hijos][:3] == ["recientes", "populares", "tendencias"]
    grupos = {h.timeline: h for h in mia.hijos if h.tipo == "grupo"}
    assert set(grupos) == {"grupo:biblioteca", "grupo:busquedas"} and not grupos["grupo:biblioteca"].listable
    assert [h.timeline for h in grupos["grupo:biblioteca"].hijos] == list(dp.BIBLIOTECA)
    assert "mis_videos" not in [h.timeline for h in mia.hijos]      # solo cuelgan de su grupo


def test_secciones_ocultas_y_busquedas_guardadas(cuenta):
    config.set("peertube", "hidden_sections", ["populares", "biblioteca"])
    config.set("peertube", "saved_searches", ["federación"])
    mia = _mia(dp.construir_nodos())
    tls = [h.timeline for h in mia.hijos]
    assert "populares" not in tls and "grupo:biblioteca" not in tls
    busquedas = next(h for h in mia.hijos if h.timeline == "grupo:busquedas")
    assert [(h.timeline, h.titulo, h.cerrable) for h in busquedas.hijos] == [("search:federación", "Búsqueda: federación", True)]


def test_listado_de_videos(cuenta):
    filas = dp.listar(cuenta, "recientes")
    assert [f.extra["uuid"] for f in filas] == ["aaaaaaaa-1", "bbbbbbbb-2", "cccccccc-3", "dddddddd-4"]
    a, b, c, d = filas
    assert a.titulo.startswith("Cómo funciona la federación. Frank. 12 minutos y 5 segundos.") and "Local" in a.titulo
    assert "1 hora, 1 minuto y 40 segundos" in b.titulo and "Remoto" in b.titulo
    assert c.extra["propio"] and not a.extra["propio"] and not b.extra["propio"]
    assert d.extra["directo"] and "En directo" in d.titulo and "+18" in d.titulo
    assert a.enlace == "https://ejemplo.org/w/aaaaaa" and dp.handle_canal(a) == "frank@ejemplo.org"
    assert dp.handle_cuenta(b) == "ana@otro.net" and dp.handle_cuenta(a) == "frank"


def test_detalle_de_un_video(cuenta):
    titulo, meta, enlace, cuerpo = dp.detalle(dp.listar(cuenta, "recientes")[0])
    assert titulo == "Cómo funciona la federación" and "Canal: Frank" in meta and "12 minutos" in meta
    assert "Descripción larga" in cuerpo and "1500 visualizaciones, 84 me gusta, 2 no me gusta" in cuerpo
    assert "Vídeo local" in cuerpo and enlace.endswith("/w/aaaaaa")


def test_notificaciones_listas_y_canales_en_memoria(cuenta):
    dp.guardar_cache(cuenta, "notificaciones", [{"id": 7, "leida": False, "descripcion": "Nuevo vídeo de Frank", "video_uuid": "aaaaaaaa-1",
                                                 "fecha": "2026-10-27T12:00:00Z"}])
    dp.guardar_cache(cuenta, "listas", [{"id": 5, "display": "Favoritos", "num_videos": 3, "es_ver_mas_tarde": True}])
    dp.guardar_cache(cuenta, "mis_canales", [{"id": 3, "handle": "yo@ejemplo.org", "display": "Mi canal", "followers": 12}])
    (n,) = dp.listar(cuenta, "notificaciones")
    assert not n.leido and n.extra["uuid"] == "aaaaaaaa-1" and "Nueva" in n.titulo and dp.detalle(n)[0] == "Nuevo vídeo de Frank"
    (lista,) = dp.listar(cuenta, "listas")
    assert "Favoritos: 3 vídeos" in lista.titulo and "ver más tarde" in lista.titulo
    (canal,) = dp.listar(cuenta, "mis_canales")
    assert canal.titulo == "Mi canal: 12 seguidores" and canal.extra["canal"]["handle"] == "yo@ejemplo.org"
    assert dp.listar(cuenta, "listas") != [] and dp.listar(cuenta + 1, "listas") == []


def test_buffers_abiertos_se_guardan_y_se_cierran(cuenta):
    for tl in ("channel:frank@ejemplo.org", "account:ana@otro.net", "playlist:5", "search:federación"):
        dp.nodo_extra(cuenta, tl, dp.titulo_buffer(tl, "Frank"))
    assert config.get("peertube", "saved_searches") == ["federación"]
    mia = _mia(dp.construir_nodos())
    hijos = {h.timeline: h for h in mia.hijos if h.tipo != "grupo"}
    assert {"channel:frank@ejemplo.org", "account:ana@otro.net", "playlist:5"} <= set(hijos)
    assert hijos["channel:frank@ejemplo.org"].titulo == "Canal: Frank" and hijos["playlist:5"].cerrable
    busquedas = next(h for h in mia.hijos if h.timeline == "grupo:busquedas")
    for nodo in list(hijos.values()) + list(busquedas.hijos):
        if nodo.cerrable:
            dp.cerrar_extra(nodo)
    mia = _mia(dp.construir_nodos())
    assert not any(h.timeline.startswith(("channel:", "account:", "playlist:")) for h in mia.hijos)
    assert next(h for h in mia.hijos if h.timeline == "grupo:busquedas").hijos == []


def test_clasificacion_de_secciones():
    assert all(dp.es_video(s) for s in ("recientes", "mis_videos", "channel:a@b", "search:x", "playlist:3", "ver_mas_tarde"))
    assert not dp.es_video("notificaciones") and not dp.es_video("listas") and not dp.es_video("mis_canales")
