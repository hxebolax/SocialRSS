# -*- coding: utf-8 -*-
"""RSS, fase 4 del informe de BlindRSS (2026-09-26): organización y avisos."""
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.rss import rss_versiones

ACC = 7201


def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'R', 'rss', 1)", (ACC,))
    db.execute("INSERT OR IGNORE INTO rss_feeds (id, account_id, title, url) VALUES (?, ?, 'F', 'https://f.test/rss')",
               (ACC, ACC))


# ------------------------------------------------------------------ versiones (B14)
def test_versiones_guardan_lo_anterior_y_explican_el_cambio():
    _preparar()
    rss_versiones.asegurar_tabla()
    db.execute("DELETE FROM rss_article_versions")
    db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, published_at) "
               "VALUES ('v:1', ?, ?, 'Titular', 'https://f.test/1', '<p>Hubo tres heridos. El alcalde habló.</p>', "
               "'2026-09-26')", (ACC, ACC))
    rss_versiones.guardar_version_anterior("v:1")
    db.execute("UPDATE rss_articles SET title = 'Titular corregido', "
               "content = '<p>Hubo cuatro heridos. El alcalde habló.</p>' WHERE id = 'v:1'")
    versiones = rss_versiones.listar("v:1")
    assert len(versiones) == 1 and versiones[0][2] == "Titular"
    texto = rss_versiones.diferencias("v:1", versiones[0][0])
    assert "Título anterior: Titular" in texto and "Título actual: Titular corregido" in texto
    assert "Quitado: Hubo tres heridos." in texto and "Añadido: Hubo cuatro heridos." in texto
    assert "El alcalde" not in texto
    for _ in range(12):
        rss_versiones.guardar_version_anterior("v:1")
    assert len(rss_versiones.listar("v:1")) == rss_versiones.MAX_VERSIONES


# ------------------------------------------------------------------ reglas (B8)
import json

from app.core.config_manager import config
from app.services.rss import rss_avisos, rss_borrados, rss_buffers
from app.services.rss.rss_rules import SIN_AVISO, RSSRulesEngine
from app.services.rss.rss_service import rss_service


def _articulos():
    _preparar()
    db.execute("DELETE FROM rss_articles WHERE account_id = ?", (ACC,))
    db.execute("DELETE FROM rss_rules WHERE account_id = ?", (ACC,))
    filas = [("r:1", "Oferta de empleo en Madrid", "Buscamos gente", "Ana", "https://empleo.test/1"),
             ("r:2", "Empleo en Sevilla", "Remoto no", "Luis", "https://otro.test/2"),
             ("r:3", "Noticias del día", "Nada de empleo", "Ana", "https://empleo.test/3")]
    for aid, t, c, a, l in filas:
        db.execute("INSERT INTO rss_articles (id, feed_id, account_id, title, content, author, link, "
                   "published_at, read) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-26', 0)", (aid, ACC, ACC, t, c, a, l))
    RSSRulesEngine.invalidar_plan(ACC)


def _regla(conds, modo, acciones, activa=1):
    db.execute("INSERT INTO rss_rules (account_id, title, pattern, field, action, active, conditions_json, "
               "match_mode, actions_json) VALUES (?, 'R', 'x', 'title', ?, ?, ?, ?, ?)",
               (ACC, acciones[0]["action"], activa, json.dumps(conds), modo, json.dumps(acciones)))
    RSSRulesEngine.invalidar_plan(ACC)


def test_operadores_campos_y_combinacion_libre():
    _articulos()
    # (título contiene "empleo" y dirección empieza por empleo.test) o autor es "Luis"
    _regla([{"field": "title", "op": "contiene", "pattern": "empleo"},
            {"field": "link", "op": "empieza", "pattern": "https://empleo.test"},
            {"field": "author", "op": "igual", "pattern": "luis"}],
           "expr:(1 y 2) o 3", [{"action": "mark_favorite"}])
    assert RSSRulesEngine.aplicar_a_existentes(ACC) == 2
    favs = {r[0] for r in db.query("SELECT id FROM rss_articles WHERE favorite = 1 AND account_id = ?", (ACC,))}
    assert favs == {"r:1", "r:2"}


def test_no_contiene_papelera_y_no_avisar():
    _articulos()
    SIN_AVISO.clear()
    _regla([{"field": "title", "op": "no_contiene", "pattern": "empleo"}], "all",
           [{"action": "delete"}, {"action": "no_avisar"}])
    assert RSSRulesEngine.aplicar_a_existentes(ACC) == 1
    assert db.query_one("SELECT deleted FROM rss_articles WHERE id = 'r:3'")[0] == 1
    # "no avisar" no es persistente: al aplicar a los existentes no se marca.
    assert "r:3" not in SIN_AVISO

    class _A:
        id, feed_id, title, content, author, link = "r:9", ACC, "Otra cosa", "", "", ""
    RSSRulesEngine.evaluar_articulo(ACC, _A())
    assert "r:9" in SIN_AVISO


def test_regla_desactivada_no_actua():
    _articulos()
    _regla([{"field": "all", "op": "contiene", "pattern": "empleo"}], "all", [{"action": "mark_read"}], activa=0)
    assert RSSRulesEngine.aplicar_a_existentes(ACC) == 0


# ------------------------------------------------------------------ carpetas y buffers
def test_carpeta_inteligente_criterios_nuevos():
    _articulos()
    sql, params = rss_service.where_carpeta_inteligente({"author": "ana", "text_not": "noticias"})
    ids = [r[0] for r in db.query("SELECT a.id FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
                                  "WHERE a.account_id = ?" + sql, (ACC, *params))]
    assert ids == ["r:1"]


def test_buffer_de_busqueda_guardada_y_etiqueta():
    _articulos()
    db.execute("DELETE FROM rss_articles_fts WHERE article_db_id LIKE 'r:%'")
    for aid, t in (("r:1", "Oferta de empleo en Madrid"), ("r:3", "Noticias del día")):
        db.execute("INSERT INTO rss_articles_fts (article_db_id, title, content, author) VALUES (?, ?, '', '')", (aid, t))
    db.execute("DELETE FROM rss_saved_searches WHERE account_id = ?", (ACC,))
    bid = db.execute_insert("INSERT INTO rss_saved_searches (account_id, name, query) VALUES (?, 'Madrid', 'madrid')", (ACC,))
    sql, params = rss_buffers.where_buffer(f"search:{bid}")
    ids = [r[0] for r in db.query("SELECT a.id FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
                                  "WHERE a.account_id = ?" + sql, (ACC, *params))]
    assert ids == ["r:1"]
    assert (bid, "Madrid") in rss_buffers.busquedas_en_arbol(ACC)
    sql, params = rss_buffers.where_buffer("tag:abc")        # id no numérico: nunca SQL libre
    assert params == [0, 0]


# ------------------------------------------------------------------ borrado y avisos (B9, B10)
def test_borrado_definitivo_no_vuelve_y_papelera_igual():
    _articulos()
    assert rss_borrados.borrar_definitivo(["r:1"]) == 1
    assert db.query_one("SELECT COUNT(*) FROM rss_articles WHERE id = 'r:1'")[0] == 0
    assert rss_borrados.borrados_entre(["r:1", "r:2"]) == {"r:1"}
    rss_service.mover_a_papelera("r:2")
    rss_service.vaciar_papelera(ACC)
    assert rss_borrados.borrados_entre(["r:2"]) == {"r:2"}


def test_modo_suprimir_por_feed():
    try:
        assert rss_borrados.modo_de_feed(ACC) == "papelera"
        rss_borrados.fijar_modo_de_feed(ACC, "definitivo")
        assert rss_borrados.modo_de_feed(ACC) == "definitivo"
    finally:
        rss_borrados.fijar_modo_de_feed(ACC, "papelera")
    assert str(ACC) not in (config.get("rss", "delete_mode_feeds", {}) or {})


def test_avisables_sin_feeds_silenciados_ni_reglas():
    _articulos()
    SIN_AVISO.clear()
    try:
        assert rss_avisos.avisables(["r:1", "r:2"]) == ["r:1", "r:2"]
        SIN_AVISO.add("r:2")
        assert rss_avisos.avisables(["r:1", "r:2"]) == ["r:1"]
        rss_avisos.fijar_feed_silenciado(ACC, True)
        assert rss_avisos.avisables(["r:1"]) == []
    finally:
        rss_avisos.fijar_feed_silenciado(ACC, False)
        SIN_AVISO.clear()
