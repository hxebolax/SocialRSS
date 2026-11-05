# -*- coding: utf-8 -*-
"""Datos de Mastodon para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
from datetime import datetime, timezone

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_mastodon as dm  # noqa: E402
from app.services.mastodon.mastodon_service import mastodon_service as ms  # noqa: E402

ACC = 9501


def _toot(i, user, nombre, html, **kw):
    return {"id": str(i), "uri": f"https://t.test/@{user}/{i}", "content": html,
            "created_at": datetime(2026, 10, 25, 10, i % 60, tzinfo=timezone.utc), "visibility": "public",
            "sensitive": False, "spoiler_text": kw.get("cw", ""), "favourited": False, "reblogged": False,
            "bookmarked": False, "media_attachments": kw.get("media", []),
            "account": {"id": "9", "acct": user, "username": user, "display_name": nombre, "emojis": []},
            "mentions": kw.get("mentions", []), "emojis": [], "replies_count": 2, "reblogs_count": 1,
            "favourites_count": 3, "card": None}


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('mastodon', 'Mastodon', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) "
               "VALUES (?, '@yo@t.test', 'mastodon', 1, 90)", (ACC,))
    toots = [_toot(901, "ana", "Ana", "<p>Hola <b>mundo</b></p>", mentions=[{"acct": "luis"}, {"acct": "yo"}]),
             _toot(902, "yo", "Yo", "<p>Mío</p>"),
             _toot(903, "luis", "Luis", "<p>oculto</p>", cw="Spoiler"),
             _toot(904, "eva", "Eva", "<p>foto</p>", media=[{"type": "image", "description": "Un gato", "url": "https://x/y.png"}])]
    with db.transaccion() as c:
        for t in toots:
            o = ms._procesar_y_guardar_toot(ACC, t, conn=c)
            c.execute("INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) "
                      "VALUES (?, 'home', ?)", (ACC, o.id))
        ms._procesar_y_guardar_notificacion(ACC, {
            "id": "55", "type": "favourite", "created_at": datetime(2026, 10, 25, 11, tzinfo=timezone.utc),
            "account": {"acct": "ana", "username": "ana", "display_name": "Ana", "emojis": []}, "status": toots[1]}, conn=c)
    return ACC


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = dm.construir_nodos()
    assert raiz.servicio == "mastodon" and not raiz.listable
    mia = next(c for c in raiz.hijos if c.account_id == cuenta)
    assert not mia.listable
    assert [h.timeline for h in mia.hijos][:2] == ["home", "notifications"]
    assert all(h.listable and h.servicio == "mastodon" for h in mia.hijos)


def test_listado_de_inicio_ordena_y_resume(cuenta):
    filas = dm.listar(cuenta, "home")
    assert [f.extra["status_id"] for f in filas] == ["904", "903", "902", "901"]
    por_id = {f.extra["status_id"]: f for f in filas}
    assert por_id["903"].titulo.endswith("Aviso de contenido: Spoiler")        # el CW manda sobre el texto
    assert por_id["904"].extra["adjuntos"] == 1
    assert por_id["902"].extra["propio"] is True and por_id["901"].extra["propio"] is False


def test_detalle_incluye_descripcion_de_adjuntos_y_contadores(cuenta):
    foto = next(f for f in dm.listar(cuenta, "home") if f.extra["status_id"] == "904")
    titulo, meta, enlace, cuerpo = dm.detalle(foto)
    assert "Un gato" in cuerpo and "2 respuestas" in cuerpo
    assert enlace.endswith("/904") and "público" in meta


def test_notificaciones_dicen_quien_y_que(cuenta):
    filas = dm.listar(cuenta, "notifications")
    assert len(filas) == 1
    assert "Ana" in filas[0].titulo and "favorita" in filas[0].titulo and "Mío" in filas[0].titulo
    assert dm.listar(cuenta, "mentions") == []


def test_respuesta_menciona_al_autor_y_a_los_demas_sin_incluirte(cuenta):
    ana = next(f for f in dm.listar(cuenta, "home") if f.extra["status_id"] == "901")
    assert dm.menciones_de_respuesta(ana) == "@ana @luis "


def test_buffers_extra_se_crean_una_vez_y_se_cierran(cuenta):
    def _hilos():
        return [h.timeline for c in dm.construir_nodos().hijos if c.account_id == cuenta for h in c.hijos]
    n1 = dm.nodo_extra(cuenta, "thread:901", "Hilo")      # los hilos se guardan: no se duplican y sobreviven al reabrir
    assert dm.nodo_extra(cuenta, "thread:901", "Hilo").timeline == n1.timeline and n1.cerrable
    assert _hilos().count("thread:901") == 1
    dm.cerrar_extra(n1)
    assert "thread:901" not in _hilos()


# --------------------------------------------------------------------------- IA y traducción (solo la parte de datos)
class _Obj:
    def __init__(self, fila):
        self.fila = fila


def test_texto_para_traducir_incluye_el_aviso_y_los_ids(cuenta):
    from app.gtk import acciones_ia
    cw = next(f for f in dm.listar(cuenta, "home") if f.extra["status_id"] == "903")
    texto, idioma, status_id, acc = acciones_ia.texto_de(None, _Obj(cw))
    assert texto.startswith("Spoiler") and "oculto" in texto
    assert status_id == "903" and acc == cuenta


def test_imagenes_de_una_publicacion(cuenta):
    from app.gtk import acciones_ia
    foto = next(f for f in dm.listar(cuenta, "home") if f.extra["status_id"] == "904")
    imgs = acciones_ia._imagenes_de(_Obj(foto))
    assert [i["description"] for i in imgs] == ["Un gato"]
    sin = next(f for f in dm.listar(cuenta, "home") if f.extra["status_id"] == "901")
    assert acciones_ia._imagenes_de(_Obj(sin)) == []


def test_con_la_ia_desactivada_no_se_ofrece(monkeypatch):
    from app.core.config_manager import config
    from app.gtk import acciones_ia
    avisos = []
    monkeypatch.setattr(acciones_ia, "notificar", avisos.append)
    config.set("ai", "provider", "off")
    try:
        assert acciones_ia.ia_activa() is False and avisos
    finally:
        config.set("ai", "provider", "gemini")
    assert acciones_ia.ia_activa() is True
