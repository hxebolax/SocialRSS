# -*- coding: utf-8 -*-
"""Pruebas de regresión de la auditoría RSS del 11-07-2026 (RSS-01..RSS-22).

Cubren: aislamiento por cuenta (categorías, alertas, búsquedas, historial,
estadísticas, descargas), contrato único de parseo (fechas ISO y enclosures de
fastfeedparser), scheduler por vencimiento y retroceso por errores, búsqueda FTS
filtrada por cuenta antes del límite, exportación saneada, artículos actualizables
por hash, OPML jerárquico, normalización de URLs y reglas compiladas.

TODO local: sin red y sobre el perfil temporal de conftest.py.
"""
import os
from datetime import datetime, timedelta

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones


@pytest.fixture(scope="module")
def entorno_rss():
    """Dos cuentas RSS con un feed cada una, sobre la base temporal migrada."""
    ejecutar_migraciones()
    from app.services.rss.rss_service import rss_service
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    ids = {}
    for nombre in ("Cuenta A", "Cuenta B"):
        row = db.query_one("SELECT id FROM accounts WHERE service_id='rss' AND name=?", (nombre,))
        if row:
            ids[nombre] = row[0]
        else:
            ids[nombre] = db.execute_insert(
                "INSERT INTO accounts (service_id, name, active) VALUES ('rss', ?, 1)", (nombre,))
    acc_a, acc_b = ids["Cuenta A"], ids["Cuenta B"]
    feed_a = rss_service.agregar_feed(acc_a, "https://ejemplo-a.test/feed", "Feed A")
    feed_b = rss_service.agregar_feed(acc_b, "https://ejemplo-b.test/feed", "Feed B")
    return {"servicio": rss_service, "acc_a": acc_a, "acc_b": acc_b,
            "feed_a": feed_a, "feed_b": feed_b}


def _insertar_articulo(feed_id, account_id, art_id, titulo, contenido="", leido=0,
                       publicado=None):
    db.execute(
        "INSERT OR IGNORE INTO rss_articles (id, feed_id, account_id, guid, link, title, "
        "content, read, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (art_id, feed_id, account_id, art_id, f"https://x.test/{art_id}", titulo,
         contenido, leido, publicado or datetime.now().isoformat()))
    db.execute(
        "INSERT INTO rss_articles_fts (article_db_id, title, content, author) VALUES (?, ?, ?, '')",
        (art_id, titulo, contenido))


# ---------------------------------------------------------------------------
# RSS-01/02/03: aislamiento por cuenta
# ---------------------------------------------------------------------------

def test_categorias_por_cuenta(entorno_rss):
    s = entorno_rss["servicio"]
    a, b = entorno_rss["acc_a"], entorno_rss["acc_b"]
    cat_a = s.agregar_categoria(a, "Noticias")
    cat_b = s.agregar_categoria(b, "Noticias")  # mismo nombre, OTRA cuenta: no choca
    assert cat_a and cat_b and cat_a != cat_b
    # Cada cuenta ve SOLO las suyas.
    assert {c["id"] for c in s.obtener_categorias(a)} == {cat_a}
    assert {c["id"] for c in s.obtener_categorias(b)} == {cat_b}
    # Repetir el nombre en la misma cuenta devuelve la existente (sin duplicar).
    assert s.agregar_categoria(a, "Noticias") == cat_a


def test_categoria_ciclo_detectado(entorno_rss):
    s = entorno_rss["servicio"]
    a = entorno_rss["acc_a"]
    padre = s.agregar_categoria(a, "Padre")
    hijo = s.agregar_categoria(a, "Hijo", parent_id=padre)
    assert s.categoria_crearia_ciclo(padre, hijo) is True     # colgar padre del hijo = ciclo
    assert s.categoria_crearia_ciclo(padre, padre) is True    # de sí misma = ciclo
    assert s.categoria_crearia_ciclo(hijo, None) is False     # a la raíz, siempre bien


def test_alertas_y_busquedas_por_cuenta(entorno_rss):
    s = entorno_rss["servicio"]
    a, b = entorno_rss["acc_a"], entorno_rss["acc_b"]
    id_alerta = s.crear_alerta(a, "python")
    assert [x["id"] for x in s.obtener_alertas(a)] == [id_alerta]
    assert s.obtener_alertas(b) == []           # la otra cuenta NO la ve
    assert s._cargar_alertas(b) == []           # ni el parseo la aplica en su cuenta
    s.borrar_alerta(id_alerta)

    id_b = s.crear_busqueda_guardada(a, "mi búsqueda", "wxpython")
    assert [x["id"] for x in s.obtener_busquedas_guardadas(a)] == [id_b]
    assert s.obtener_busquedas_guardadas(b) == []
    # Mismo nombre en la misma cuenta ACTUALIZA en vez de duplicar.
    assert s.crear_busqueda_guardada(a, "mi búsqueda", "otra") == id_b
    s.borrar_busqueda_guardada(id_b)


def test_historial_y_estadisticas_por_cuenta(entorno_rss):
    s = entorno_rss["servicio"]
    a, b = entorno_rss["acc_a"], entorno_rss["acc_b"]
    fa, fb = entorno_rss["feed_a"], entorno_rss["feed_b"]
    _insertar_articulo(fa, a, "hist-a1", "Artículo A")
    _insertar_articulo(fb, b, "hist-b1", "Artículo B")
    s.registrar_lectura("hist-a1")
    s.registrar_lectura("hist-b1")
    assert any(h["article_db_id"] == "hist-a1" for h in s.obtener_historial_lectura(a))
    assert not any(h["article_db_id"] == "hist-b1" for h in s.obtener_historial_lectura(a))
    # Vaciar el historial de A no toca el de B.
    s.vaciar_historial_lectura(a)
    assert s.obtener_historial_lectura(a) == []
    assert any(h["article_db_id"] == "hist-b1" for h in s.obtener_historial_lectura(b))
    # Estadísticas: los pendientes de A no cuentan los artículos de B.
    st_a = s.obtener_estadisticas_lectura(a)
    st_b = s.obtener_estadisticas_lectura(b)
    assert st_a["no_leidos"] >= 1 and st_b["no_leidos"] >= 1
    total_global = (db.query_one(
        "SELECT COUNT(*) FROM rss_articles WHERE COALESCE(read,0)=0 AND COALESCE(deleted,0)=0") or [0])[0]
    assert st_a["no_leidos"] < total_global  # ya no es el recuento global


# ---------------------------------------------------------------------------
# RSS-16: FTS filtra la cuenta ANTES del límite
# ---------------------------------------------------------------------------

def test_fts_no_se_come_el_limite_con_otras_cuentas(entorno_rss):
    s = entorno_rss["servicio"]
    a, b = entorno_rss["acc_a"], entorno_rss["acc_b"]
    fa, fb = entorno_rss["feed_a"], entorno_rss["feed_b"]
    # 30 coincidencias en B (que antes consumían el límite global) y 1 en A.
    for i in range(30):
        _insertar_articulo(fb, b, f"fts-b{i}", f"zanahoria {i}", "texto zanahoria")
    _insertar_articulo(fa, a, "fts-a0", "zanahoria propia", "texto zanahoria")
    res = s.buscar_articulos_fts(a, "zanahoria", limit=10)
    assert [x.id for x in res] == ["fts-a0"]  # la de A aparece aunque B tenga más


# ---------------------------------------------------------------------------
# RSS-07: contrato único de parseo (adaptador fastfeedparser)
# ---------------------------------------------------------------------------

def test_normalizar_entrada_estilo_fastfeedparser():
    from app.services.rss.rss_parseo import _normalizar_entrada
    entrada = {
        "title": "Episodio 1", "link": "https://pod.test/e1", "id": "e1",
        "published": "2026-07-10T08:30:00Z",                 # ISO, sin *_parsed
        "enclosures": [{"url": "https://pod.test/e1.mp3",     # 'url', no 'href'
                        "type": "audio/mpeg", "length": "123"}],
        "summary": "Resumen del episodio",
    }
    e = _normalizar_entrada(entrada)
    assert e["published_parsed"] is not None
    assert e["published_parsed"].tm_year == 2026 and e["published_parsed"].tm_hour == 8
    assert e["enclosures"][0]["href"] == "https://pod.test/e1.mp3"
    assert e["contenido_resuelto"] == "Resumen del episodio"


def test_normalizar_entrada_estilo_feedparser():
    import time
    from app.services.rss.rss_parseo import _normalizar_entrada
    st = time.gmtime(1780000000)
    entrada = {
        "title": "Post", "link": "https://blog.test/p", "id": "p",
        "published_parsed": st,
        "content": [{"value": "<p>Cuerpo completo</p>"}],
        "enclosures": [{"href": "https://blog.test/a.mp3", "type": "audio/mpeg"}],
    }
    e = _normalizar_entrada(entrada)
    assert e["published_parsed"] == st                      # se respeta el del parser
    assert e["contenido_resuelto"] == "<p>Cuerpo completo</p>"
    assert e["enclosures"][0]["href"] == "https://blog.test/a.mp3"


# ---------------------------------------------------------------------------
# RSS-05/11: scheduler por vencimiento y retroceso por errores
# ---------------------------------------------------------------------------

def test_scheduler_exito_programa_siguiente(entorno_rss):
    s = entorno_rss["servicio"]
    fa = entorno_rss["feed_a"]
    s._registrar_exito_feed(fa, custom_interval=7, nuevos=1)
    row = db.query_one("SELECT next_update_at, fail_count, last_success_at FROM rss_feeds "
                       "WHERE id = ?", (fa,))
    assert row[0] and row[1] == 0 and row[2]
    prog = datetime.fromisoformat(row[0])
    delta = prog - datetime.now()
    assert timedelta(minutes=6) < delta < timedelta(minutes=9)  # 7 min + dispersión
    # Aún no vencido: el sondeo automático NO lo elige; tras vencer, sí.
    assert fa not in s._feeds_vencidos(entorno_rss["acc_a"])
    db.execute("UPDATE rss_feeds SET next_update_at = ? WHERE id = ?",
               ((datetime.now() - timedelta(minutes=1)).isoformat(), fa))
    assert fa in s._feeds_vencidos(entorno_rss["acc_a"])


def test_scheduler_fallo_aplica_retroceso(entorno_rss):
    import requests
    s = entorno_rss["servicio"]
    fb = entorno_rss["feed_b"]
    # Fallo genérico n.º 3: retroceso 30 * 2^2 = 120 min.
    s._registrar_fallo_feed(fb, "Feed B", RuntimeError("bum"), fail_count_prev=2,
                            custom_interval=0)
    row = db.query_one("SELECT next_update_at, fail_count FROM rss_feeds WHERE id = ?", (fb,))
    assert row[1] == 3
    delta = datetime.fromisoformat(row[0]) - datetime.now()
    assert timedelta(minutes=115) < delta < timedelta(minutes=125)
    # 410: el feed se PAUSA y no se reprograma.
    resp = requests.Response()
    resp.status_code = 410
    s._registrar_fallo_feed(fb, "Feed B", requests.HTTPError("410", response=resp),
                            fail_count_prev=3, custom_interval=0)
    row = db.query_one("SELECT paused, last_error FROM rss_feeds WHERE id = ?", (fb,))
    assert row[0] == 1 and "410" in (row[1] or "")
    db.execute("UPDATE rss_feeds SET paused = 0, fail_count = 0 WHERE id = ?", (fb,))


# ---------------------------------------------------------------------------
# RSS-18: exportación saneada
# ---------------------------------------------------------------------------

def test_exportar_articulo_html_saneado(entorno_rss, tmp_path):
    s = entorno_rss["servicio"]
    a, fa = entorno_rss["acc_a"], entorno_rss["feed_a"]
    _insertar_articulo(
        fa, a, "exp-1", 'Título <script>alert("x")</script>',
        contenido='<p>Hola</p><script>alert("mal")</script>'
                  '<img src="https://mal.test/pix.gif" onerror="alert(1)">')
    db.execute("UPDATE rss_articles SET link = ? WHERE id = 'exp-1'",
               ("javascript:alert(document.cookie)",))
    ruta = str(tmp_path / "articulo.html")
    assert s.exportar_articulo("exp-1", ruta, formato="html")
    contenido = open(ruta, encoding="utf-8").read()
    assert "<script>" not in contenido            # ni del título ni del cuerpo
    assert "onerror" not in contenido
    assert "javascript:" not in contenido         # el enlace no-http se omite
    assert "Hola" in contenido                    # el contenido legítimo sobrevive


def test_quitar_imagenes_html():
    from app.core.html_utils import quitar_imagenes_html
    html = '<p>Texto</p><img src="https://t.test/pix.gif" alt="Un gato"><img src="x.png">'
    limpio = quitar_imagenes_html(html)
    assert "<img" not in limpio
    assert "[Imagen: Un gato]" in limpio and "[Imagen]" in limpio
    assert "Texto" in limpio


# ---------------------------------------------------------------------------
# RSS-21/15: normalización de URLs
# ---------------------------------------------------------------------------

def test_normalizar_url():
    from app.core.url_utils import normalizar_url
    assert normalizar_url("HTTPS://Ejemplo.TEST:443/feed/") == "https://ejemplo.test/feed"
    assert (normalizar_url("https://e.test/feed?utm_source=x&b=2&a=1#frag")
            == "https://e.test/feed?a=1&b=2")
    assert normalizar_url("https://e.test/feed") == normalizar_url("https://e.test/feed/")


def test_feed_duplicado_por_url_normalizada(entorno_rss):
    s = entorno_rss["servicio"]
    a = entorno_rss["acc_a"]
    # La variante con barra final y utm NO crea un feed nuevo.
    encontrado = s.buscar_feed_por_url(a, "https://ejemplo-a.test/feed/?utm_source=boletin")
    assert encontrado and encontrado[0] == entorno_rss["feed_a"]


# ---------------------------------------------------------------------------
# RSS-20: OPML jerárquico (round-trip)
# ---------------------------------------------------------------------------

def test_opml_conserva_jerarquia(tmp_path):
    from app.services.rss.opml import exportar_opml, importar_opml
    ruta = str(tmp_path / "subs.opml")
    exportar_opml(ruta, [
        {"title": "Raíz", "url": "https://r.test/feed"},
        {"title": "Anidado", "url": "https://n.test/feed",
         "category_path": ["Tecnología", "Linux"]},
    ])
    feeds = importar_opml(ruta)
    por_url = {f["url"]: f for f in feeds}
    assert por_url["https://r.test/feed"]["category_path"] == []
    assert por_url["https://n.test/feed"]["category_path"] == ["Tecnología", "Linux"]
    assert por_url["https://n.test/feed"]["category"] == "Linux"


# ---------------------------------------------------------------------------
# RSS-17: reglas compiladas (regex inválida no rompe; caché invalidable)
# ---------------------------------------------------------------------------

def test_plan_de_reglas_compilado(entorno_rss):
    from app.services.rss.rss_rules import RSSRulesEngine
    a = entorno_rss["acc_a"]
    db.execute("INSERT INTO rss_rules (account_id, title, pattern, field, action, active) "
               "VALUES (?, 'regla regex', 'pyth.n', 'title', 'mark_read', 1)", (a,))
    db.execute("INSERT INTO rss_rules (account_id, title, pattern, field, action, active) "
               "VALUES (?, 'regla rota', '[invalida', 'title', 'mark_read', 1)", (a,))
    RSSRulesEngine.invalidar_plan(a)
    plan = RSSRulesEngine.plan(a)
    assert len(plan) == 2
    valida = next(p for p in plan if p["title"] == "regla regex")
    rota = next(p for p in plan if p["title"] == "regla rota")
    assert valida["compilada"] is not None
    assert rota["compilada"] is None  # se usará como subcadena, sin lanzar re.error

    class _Art:
        id = "regla-art"
        feed_id = entorno_rss["feed_a"]
        title = "Aprendiendo Python"
        content = ""
        author = ""
    _insertar_articulo(entorno_rss["feed_a"], a, "regla-art", "Aprendiendo Python")
    assert RSSRulesEngine.evaluar_articulo(a, _Art(), plan=plan) is True
    fila = db.query_one("SELECT read FROM rss_articles WHERE id = 'regla-art'")
    assert fila[0] == 1
    db.execute("DELETE FROM rss_rules WHERE account_id = ?", (a,))
    RSSRulesEngine.invalidar_plan(a)


# ---------------------------------------------------------------------------
# RSS-14: columnas de seguimiento presentes (first/last_seen, hash)
# ---------------------------------------------------------------------------

def test_columnas_seguimiento_articulos(entorno_rss):
    cols = {r[1] for r in db.query("PRAGMA table_info(rss_articles)")}
    assert {"first_seen_at", "last_seen_at", "source_updated_at", "content_hash"} <= cols
    cols_f = {r[1] for r in db.query("PRAGMA table_info(rss_feeds)")}
    assert {"last_attempt_at", "last_success_at", "next_update_at",
            "fail_count", "last_status", "url_normalized"} <= cols_f


def test_scheduler_adaptativo_por_frecuencia(entorno_rss):
    """Con el intervalo global a 1 minuto, un feed que no publica desde hace horas NO se
    sondea cada minuto: suelo de 5 min (>1 h) o 15 min (>6 h). Uno que acaba de publicar
    (nuevos>0) mantiene el ritmo rápido del usuario."""
    from app.core.config_manager import config
    s = entorno_rss["servicio"]
    a, fa = entorno_rss["acc_a"], entorno_rss["feed_a"]
    config.set("rss", "update_interval", 1)
    try:
        # Última publicación hace ~3 horas, sin artículos nuevos en esta pasada -> suelo 5 min.
        db.execute("UPDATE rss_articles SET published_at = ? WHERE feed_id = ?",
                   ((datetime.now() - timedelta(hours=3)).isoformat(), fa))
        s._registrar_exito_feed(fa, custom_interval=0, nuevos=0)
        prox = datetime.fromisoformat(
            db.query_one("SELECT next_update_at FROM rss_feeds WHERE id = ?", (fa,))[0])
        assert prox - datetime.now() > timedelta(minutes=4)
        # Feed CALIENTE (trajo artículos nuevos): respeta el minuto del usuario.
        s._registrar_exito_feed(fa, custom_interval=0, nuevos=3)
        prox = datetime.fromisoformat(
            db.query_one("SELECT next_update_at FROM rss_feeds WHERE id = ?", (fa,))[0])
        assert prox - datetime.now() < timedelta(minutes=3)
    finally:
        config.set("rss", "update_interval", 30)


def test_body_hash_presente(entorno_rss):
    """La columna body_hash existe (salta el parseo cuando el servidor repite cuerpo sin ETag)."""
    cols = {r[1] for r in db.query("PRAGMA table_info(rss_feeds)")}
    assert "body_hash" in cols


# ---------------------------------------------------------------------------
# Prioridad B: JSON Feed 1.0/1.1
# ---------------------------------------------------------------------------

def test_json_feed_se_parsea_normalizado():
    from app.services.rss.rss_parseo import _parsear_json_feed, _normalizar_entrada
    cuerpo = b'''{
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Mi blog",
        "items": [{
            "id": "post-1", "url": "https://b.test/1", "title": "Hola JSON",
            "content_html": "<p>Cuerpo</p>",
            "date_published": "2026-07-01T12:00:00Z",
            "authors": [{"name": "Ana"}],
            "attachments": [{"url": "https://b.test/e.mp3", "mime_type": "audio/mpeg",
                             "size_in_bytes": 99}]
        }]
    }'''
    parsed = _parsear_json_feed(cuerpo)
    assert parsed is not None and parsed.feed.get("title") == "Mi blog"
    e = _normalizar_entrada(parsed.entries[0])
    assert e["title"] == "Hola JSON" and e["author"] == "Ana"
    assert e["contenido_resuelto"] == "<p>Cuerpo</p>"
    assert e["published_parsed"].tm_mon == 7
    assert e["enclosures"][0]["href"] == "https://b.test/e.mp3"
    # Lo que no es JSON Feed se rechaza sin romper.
    assert _parsear_json_feed(b'{"hola": 1}') is None
    assert _parsear_json_feed(b"<rss/>") is None


# ---------------------------------------------------------------------------
# Prioridad A: reglas avanzadas (Y/O, ámbito, detener, simulación)
# ---------------------------------------------------------------------------

def test_reglas_avanzadas_y_o_ambito_y_stop(entorno_rss):
    import json
    from app.services.rss.rss_rules import RSSRulesEngine
    a, fa, fb = entorno_rss["acc_a"], entorno_rss["feed_a"], entorno_rss["feed_b"]
    db.execute("DELETE FROM rss_rules WHERE account_id = ?", (a,))
    # Regla avanzada: (título contiene 'linux' Y contenido contiene 'kernel'),
    # SOLO en el feed A, con prioridad alta, DETENER, y dos acciones a la vez.
    db.execute(
        "INSERT INTO rss_rules (account_id, title, pattern, field, action, active, "
        "conditions_json, match_mode, priority, stop_processing, scope_feed_id, actions_json) "
        "VALUES (?, 'avanzada', 'linux', 'title', 'mark_read', 1, ?, 'all', 10, 1, ?, ?)",
        (a, json.dumps([{"field": "title", "pattern": "linux", "is_regex": False},
                        {"field": "content", "pattern": "kernel", "is_regex": False}]),
         fa, json.dumps([{"action": "mark_read", "value": None},
                         {"action": "mark_favorite", "value": None}])))
    # Segunda regla (prioridad menor): NO debería ejecutarse tras el stop.
    db.execute(
        "INSERT INTO rss_rules (account_id, title, pattern, field, action, active, priority) "
        "VALUES (?, 'despues', 'linux', 'title', 'read_later', 1, 0)", (a,))
    RSSRulesEngine.invalidar_plan(a)
    plan = RSSRulesEngine.plan(a)
    assert [p["title"] for p in plan] == ["avanzada", "despues"]  # orden por prioridad

    class _Art:
        pass
    art = _Art()
    art.id = "regla-adv-1"
    art.feed_id = fa
    art.title = "Novedades de Linux"
    art.content = "Cambios en el kernel"
    art.author = ""
    _insertar_articulo(fa, a, "regla-adv-1", art.title, art.content)
    assert RSSRulesEngine.evaluar_articulo(a, art, plan=plan) is True
    fila = db.query_one("SELECT read, favorite, COALESCE(read_later,0) FROM rss_articles "
                        "WHERE id = 'regla-adv-1'")
    # Las DOS acciones de la avanzada corren; la regla 'despues' NO (stop_processing).
    assert fila == (1, 1, 0)
    # Con modo 'all', si falta una condición no coincide.
    art2 = _Art()
    art2.id = "regla-adv-2"
    art2.feed_id = fa
    art2.title = "Novedades de Linux"
    art2.content = "sin la otra palabra"
    art2.author = ""
    _insertar_articulo(fa, a, "regla-adv-2", art2.title, art2.content)
    # La regla 'despues' (sin stop previo aplicable) sí lo marca leer-más-tarde.
    RSSRulesEngine.evaluar_articulo(a, art2, plan=plan)
    fila2 = db.query_one("SELECT read, COALESCE(read_later,0) FROM rss_articles "
                         "WHERE id = 'regla-adv-2'")
    assert fila2 == (0, 1)
    # ÁMBITO: el mismo artículo en el feed B no dispara la avanzada.
    art3 = _Art()
    art3.id = "regla-adv-3"
    art3.feed_id = fb
    art3.title = "Novedades de Linux"
    art3.content = "Cambios en el kernel"
    art3.author = ""
    assert RSSRulesEngine._regla_coincide(plan[0], art3) is False
    # SIMULACIÓN: no ejecuta acciones y lista coincidencias por regla.
    sim = RSSRulesEngine.simular(a)
    por_regla = {s["regla"]: s["coincidencias"] for s in sim}
    assert any("Linux" in t for t in por_regla.get("avanzada", []))
    db.execute("DELETE FROM rss_rules WHERE account_id = ?", (a,))
    RSSRulesEngine.invalidar_plan(a)


# ---------------------------------------------------------------------------
# Prioridad A: carpetas inteligentes
# ---------------------------------------------------------------------------

def test_carpetas_inteligentes(entorno_rss):
    s = entorno_rss["servicio"]
    a, fa = entorno_rss["acc_a"], entorno_rss["feed_a"]
    _insertar_articulo(fa, a, "smart-1", "Con zanahorias frescas", "receta", leido=0)
    _insertar_articulo(fa, a, "smart-2", "Otra cosa", "nada", leido=1)
    sf = s.crear_carpeta_inteligente(a, "Recetas sin leer",
                                     {"read": 0, "text": "zanahorias"})
    carpetas = s.obtener_carpetas_inteligentes(a)
    mia = next(c for c in carpetas if c["id"] == sf)
    assert mia["criteria"]["text"] == "zanahorias" and mia["criteria"]["v"] == 1
    extra, params = s.where_carpeta_inteligente(mia["criteria"])
    filas = db.query(
        "SELECT a.id FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
        "WHERE a.account_id = ? AND COALESCE(a.deleted,0) = 0" + extra,
        (a, *params))
    assert [r[0] for r in filas] == ["smart-1"]
    # Claves desconocidas se IGNORAN (nunca llegan al SQL).
    extra2, params2 = s.where_carpeta_inteligente({"read": 0, "inyeccion": "x'; DROP"})
    assert "inyeccion" not in extra2 and "DROP" not in extra2
    s.borrar_carpeta_inteligente(sf)
    assert all(c["id"] != sf for c in s.obtener_carpetas_inteligentes(a))


# ---------------------------------------------------------------------------
# Prioridad A: centro de salud y OPML con vista previa
# ---------------------------------------------------------------------------

def test_salud_feeds_y_reintento(entorno_rss):
    s = entorno_rss["servicio"]
    a, fa = entorno_rss["acc_a"], entorno_rss["feed_a"]
    db.execute("UPDATE rss_feeds SET fail_count = 2, last_error = 'bum', paused = 1 "
               "WHERE id = ?", (fa,))
    salud = s.obtener_salud_feeds(a)
    mio = next(x for x in salud if x["id"] == fa)
    assert mio["estado"] == "pausado" and mio["fail_count"] == 2
    # Los problemáticos van primero.
    assert salud[0]["estado"] != "ok" or all(x["estado"] == "ok" for x in salud)
    db.execute("UPDATE rss_feeds SET fail_count = 0, last_error = NULL, paused = 0 "
               "WHERE id = ?", (fa,))


def test_analizar_opml_clasifica(entorno_rss, tmp_path):
    from app.services.rss.opml import exportar_opml
    s = entorno_rss["servicio"]
    a = entorno_rss["acc_a"]
    ruta = str(tmp_path / "previa.opml")
    exportar_opml(ruta, [
        {"title": "Feed A", "url": "https://ejemplo-a.test/feed"},   # ya suscrito
        {"title": "Nuevo", "url": "https://nuevo.test/feed",
         "category_path": ["Tec"]},
        {"title": "Roto", "url": "ftp://malo"},
    ])
    analisis = s.analizar_opml(a, ruta)
    estados = {f["title"]: f["estado"] for f in analisis}
    assert estados == {"Feed A": "duplicado", "Nuevo": "nuevo", "Roto": "invalido"}
    assert next(f for f in analisis if f["title"] == "Nuevo")["category_path"] == ["Tec"]
