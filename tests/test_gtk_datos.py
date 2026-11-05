# -*- coding: utf-8 -*-
"""Capa de datos de la interfaz GTK (sin GTK ni pantalla): árbol, listado y búsqueda."""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos  # noqa: E402


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    acc = datos.cuenta_rss()
    db.execute("INSERT OR IGNORE INTO rss_categories (id, account_id, name, position) VALUES (8801, ?, 'Noticias', 0)", (acc,))
    db.execute("INSERT OR REPLACE INTO rss_feeds (id, account_id, title, url, category_id) "
               "VALUES (8801, ?, 'Blog', 'https://b.test/rss', 8801)", (acc,))
    db.execute("INSERT OR REPLACE INTO rss_feeds (id, account_id, title, url) VALUES (8802, ?, 'Suelto', 'https://s.test/rss')", (acc,))
    for aid, feed, titulo, leido in (("g1", 8801, "Primero", 0), ("g2", 8801, "Segundo", 1), ("g3", 8802, "Tercero", 0)):
        db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, published_at, read) "
                   "VALUES (?, ?, ?, ?, 'https://x.test/' || ?, '', '2026-01-02', ?)", (aid, feed, acc, titulo, aid, leido))
    return acc


def test_cuenta_rss_es_estable():
    ejecutar_migraciones()
    assert datos.cuenta_rss() == datos.cuenta_rss()


def test_arbol_con_carpetas_feeds_y_contadores(cuenta):
    arbol = datos.construir_arbol(cuenta)
    tipos = [n.tipo for n in arbol]
    assert tipos[:5] == ["all", "unread", "favorites", "read_later", "today"]
    carpeta = next(n for n in arbol if n.tipo == "category" and n.titulo == "Noticias")
    assert [h.titulo for h in carpeta.hijos] == ["Blog"]
    assert carpeta.no_leidos == 1 and carpeta.etiqueta == "Noticias (1)"
    assert any(n.tipo == "feed" and n.titulo == "Suelto" for n in arbol)


def test_listar_por_buffer(cuenta):
    arbol = datos.construir_arbol(cuenta)
    sin_leer = next(n for n in arbol if n.tipo == "unread")
    assert {f.id for f in datos.listar(cuenta, sin_leer)} >= {"g1", "g3"}
    assert "g2" not in {f.id for f in datos.listar(cuenta, sin_leer)}
    carpeta = next(n for n in arbol if n.tipo == "category" and n.titulo == "Noticias")
    assert {f.id for f in datos.listar(cuenta, carpeta)} == {"g1", "g2"}
    feed = carpeta.hijos[0]
    filas = datos.listar(cuenta, feed)
    assert [f.titulo for f in filas] and all(f.feed_titulo == "Blog" for f in filas)


def test_fecha_legible_en_formas_basicas():
    assert datos.fecha_legible("") == ""
    assert datos.fecha_legible("2020-03-04T10:00:00") == "04/03/2020"
