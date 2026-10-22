# -*- coding: utf-8 -*-
"""Lector limpio con estructura (fase 2 de BlindRSS, modelo del ePubViewer del autor): texto con
marcas, navegación por letras, tablas, citas, búsqueda y posiciones con emojis.

El diálogo se crea SIN mostrarse (no roba el foco ni abre ventanas en el equipo)."""
import pytest

from app.core import texto_estructurado as te
from app.core.database import db
from app.core.migrations import ejecutar_migraciones

HTML = ("<h2>Resumen</h2><p>Texto con <a href='/otra'>un enlace</a> y un emoji 😀 aquí.</p>"
        "<table><caption>Precios</caption><tr><th>Producto</th><th>Precio</th></tr>"
        "<tr><td>Pan</td><td>1 €</td></tr><tr><td>Leche</td><td>2 €</td></tr></table>"
        "<blockquote><p>Lo que dijo</p></blockquote>"
        "<ul><li>uno</li><li>dos</li></ul><h3>Detalle</h3><p>Final del texto.</p>")


def test_estructura_basica():
    e = te.html_a_estructura(HTML)
    assert "Tabla de 3 filas y 3 columnas" not in e.texto
    assert "Tabla de 3 filas y 2 columnas: Precios" in e.texto
    assert "Pan\t1 €" in e.texto and "Fin de la tabla." in e.texto
    assert "Cita:\nLo que dijo\nFin de la cita." in e.texto
    assert "\n\n" not in e.texto
    t = e.siguiente(te.TABLA, 0)
    assert t.nivel == 3 and t.columnas == 2 and t.referencia.split("\n")[1] == "Pan\t1 €"
    assert e.contenedor_en(e.texto.index("Leche")) == t
    k = e.grupo(te.ENLACE)[0]
    assert e.texto[k.posicion:k.posicion + k.longitud] == "un enlace" and k.referencia == "/otra"
    assert [m.texto for m in e.grupo(te.ENCABEZADO)] == ["Resumen", "Detalle"]
    assert e.siguiente(te.ENCABEZADO, 0, nivel=3).texto == "Detalle"
    assert "<th scope=\"col\">Producto</th>" in te.html_de_tabla(t)


def test_tabla_de_una_columna_es_texto_normal():
    e = te.html_a_estructura("<table><tr><td>solo</td></tr><tr><td>maquetación</td></tr></table>")
    assert e.grupo(te.TABLA) == [] and e.texto == "solo\nmaquetación"


def test_texto_plano_sin_etiquetas():
    e = te.html_a_estructura("línea uno\n\n  línea   dos ")
    assert e.texto == "línea uno\nlínea dos" and e.marcas == []


def test_mapa_utf16_con_emojis():
    texto = "a😀b😀c"
    m = te.MapaUtf16(texto)
    for i in range(len(texto) + 1):
        assert m.desde_control(m.a_control(i)) == i
    assert m.a_control(texto.index("c")) == texto.index("c") + 2


# ------------------------------------------------------------------ el diálogo, sin mostrarlo
@pytest.fixture
def lector(monkeypatch):
    wx = pytest.importorskip("wx")
    app = wx.App.Get() or wx.App(False)
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (6801, 'R', 'rss', 1)")
    db.execute("INSERT OR IGNORE INTO rss_feeds (id, account_id, title, url) VALUES (6801, 6801, 'F', 'https://f.test/rss')")
    db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, published_at) "
               "VALUES ('est:1', 6801, 6801, 'Artículo', 'https://f.test/a', ?, '2026-09-26')", (HTML,))
    dichos = []
    import app.ui.rss_lector as rl
    import app.ui.rss_lector_nav as nav
    monkeypatch.setattr(rl, "speak", lambda t, **k: dichos.append(t))
    monkeypatch.setattr(nav, "speak", lambda t, **k: dichos.append(t))
    monkeypatch.setattr(rl.rss_service, "texto_completo_pendiente", lambda aid: False)
    monkeypatch.setattr(rl.task_manager, "start_task", lambda *a, **k: None)
    dlg = rl.CleanArticleViewer(None, "est:1")
    dlg._dichos = dichos
    yield dlg
    dlg.Destroy()
    del app


def test_saltar_por_letras(lector):
    lector._ir_a(0)
    lector._saltar("T", True)
    assert lector._dichos[-1].startswith("Tabla de 3 filas y 2 columnas: Precios")
    pos_tabla = lector._pos()
    assert lector.txt_lector.GetValue()[pos_tabla:].startswith("Tabla de 3 filas")
    lector._saltar("T", True)
    assert "No hay más tablas hacia delante" in lector._dichos[-1]
    lector._saltar("H", False)
    assert "Resumen" in lector._dichos[-1]
    lector._saltar("3", True)
    assert lector._dichos[-1].startswith("Detalle. Encabezado de nivel 3")
    lector._saltar("1", False)
    assert lector._dichos[-1].startswith("Artículo. Encabezado de nivel 1")
    lector._saltar("G", True)
    assert "No hay imágenes" in lector._dichos[-1]


def test_z_sale_de_la_tabla_y_busqueda(lector):
    texto = lector.txt_lector.GetValue()
    lector._ir_a(texto.index("Leche"))
    lector._saltar_contenedor(True)
    assert lector._dichos[-1].startswith("Después de la tabla.")
    assert texto[lector._pos():].startswith("Cita:")
    lector._ultima_busqueda = "final"
    lector._ir_a(0)
    lector._repetir_busqueda(True)
    desde, hasta = lector.txt_lector.GetSelection()
    # Hay un emoji antes: la selección en el control va 1 unidad por delante del índice Python.
    assert desde == texto.lower().index("final") + 1 and hasta - desde == len("final")
    assert lector._dichos[-1] == "Final del texto."
