# -*- coding: utf-8 -*-
"""Artículo completo de RSS: extractor combinado, elección de motor y suscripciones de pago.

Sin red: se trabaja sobre HTML construido aquí que imita los casos reales detectados (El Mundo
con lista de "relacionadas" al principio, cuerpo recortado con articleBody en JSON-LD, página
en ISO-8859-15)."""
import json

from app.services.rss.rss_fulltext import FullTextExtractor as F, MIN_CARACTERES_ARTICULO
from app.services.rss import rss_suscripciones as sus

PARRAFOS = [
    "La casualidad o el destino ha querido que el mismo día que el festival proyectaba la serie "
    "en su sección oficial, al lado, en otra sala, se proyectara otra película premiada.",
    "Valga la reflexión precedente para ampliar la perspectiva que es, básicamente, lo que hace "
    "la serie, pensada y dirigida a ocho manos por sus cuatro creadoras.",
    "Tanto la escritura como la dirección retratan con realismo sin dejarse seducir por el "
    "recurso de la sordidez y se hacen cargo del drama sin caer en el folletín.",
    "El resultado es, por orden, la serie española del año y un ejemplo de dirección medida, "
    "precisa y empeñada en algo tan básico como mirar las cosas por primera vez.",
]


def _pagina(cuerpo_html, jsonld=None, titulo="El castillo"):
    ld = ""
    if jsonld is not None:
        ld = f'<script type="application/ld+json">{json.dumps(jsonld)}</script>'
    return (f"<html><head><title>{titulo}</title>{ld}</head><body>"
            "<nav><a href='/'>Portada</a> <a href='/cultura'>Cultura</a></nav>"
            f"<article><h1>{titulo}</h1>{cuerpo_html}</article>"
            "<footer>Aviso legal. Política de cookies.</footer></body></html>")


def _cuerpo(n=len(PARRAFOS)):
    relacionadas = ("<ul><li><a href='/a'>Otra noticia sobre cine que no es este artículo</a></li>"
                    "<li><a href='/b'>Y otra más de la misma sección del periódico</a></li>"
                    "<li><a href='/c'>La tercera noticia relacionada con la serie</a></li></ul>")
    return relacionadas + "".join(f"<p>{p}</p>" for p in PARRAFOS[:n])


def test_extrae_todos_los_parrafos_sin_relacionadas_ni_titulo():
    res = F.extraer_de_html(_pagina(_cuerpo()), "https://ejemplo.es/a.html", "El castillo", "auto")
    assert res is not None
    for p in PARRAFOS:
        assert p[:40] in res.texto
    assert "Otra noticia sobre cine" not in res.texto
    assert not res.texto.lstrip("# \n").startswith("El castillo")
    assert "Política de cookies" not in res.texto


def test_cada_motor_devuelve_algo_y_hay_respaldo():
    html = _pagina(_cuerpo())
    for motor in ("auto", "trafilatura", "readability", "jsonld"):
        res = F.extraer_de_html(html, "https://ejemplo.es/a.html", "El castillo", motor)
        # "jsonld" no tiene datos estructurados en esta página: cae a los demás extractores.
        assert res is not None, motor
        assert PARRAFOS[-1][:40] in res.texto


def test_jsonld_gana_si_el_html_viene_recortado():
    completo = "\n".join(PARRAFOS * 3)
    html = _pagina(f"<p>{PARRAFOS[0]}</p>",
                   jsonld={"@context": "https://schema.org", "@type": "NewsArticle",
                           "headline": "El castillo", "articleBody": completo})
    res = F.extraer_de_html(html, "https://ejemplo.es/a.html", "El castillo", "auto")
    # Trafilatura también sabe recurrir al articleBody; lo que importa es el texto completo.
    assert res is not None and res.metodo in ("jsonld", "trafilatura")
    assert PARRAFOS[-1][:40] in res.texto
    # Y el motor JSON-LD por sí solo lo saca entero (acentos escapados í incluidos).
    solo = F.extraer_de_html(html, "https://ejemplo.es/a.html", "El castillo", "jsonld")
    assert solo.metodo == "jsonld" and "mismo día" in solo.texto


def test_jsonld_dentro_de_graph():
    html = _pagina("<p>corto</p>", jsonld={"@graph": [{"@type": "WebPage"},
                                                      {"@type": "Article", "articleBody": "\n".join(PARRAFOS)}]})
    assert "articleBody" in html
    assert F._con_jsonld(html).count("<p>") == len(PARRAFOS)


def test_pagina_sin_articulo_devuelve_none():
    assert F.extraer_de_html("<html><body><p>Acepta las cookies.</p></body></html>") is None
    assert F.extraer_de_html("") is None
    assert MIN_CARACTERES_ARTICULO > 50


def test_decodifica_iso_8859_15_por_meta():
    html = '<html><head><meta charset="iso-8859-15"></head><body><p>mismo día €</p></body></html>'
    assert "mismo día €" in F._decodificar(html.encode("iso-8859-15"), "")
    # La cabecera HTTP manda sobre la detección.
    assert "día" in F._decodificar("<p>día</p>".encode("utf-8"), "utf-8")


def test_compatibilidad_limpiar_html_devuelve_texto():
    texto = F.limpiar_html(_pagina(_cuerpo()))
    assert PARRAFOS[1][:40] in texto and "<p>" not in texto


def test_dominios_de_suscripcion(monkeypatch):
    from app.core.config_manager import config
    assert sus.normalizar_dominio("https://www.ElPais.com/x/y") == "elpais.com"
    assert sus.normalizar_dominio("  elmundo.es  ") == "elmundo.es"
    assert sus.normalizar_dominio("no es un dominio") == ""
    monkeypatch.setattr(config, "get", lambda s, k, d=None: ["elpais.com", "www.elmundo.es"]
                        if (s, k) == ("rss", "subscription_domains") else d)
    assert sus.dominio_suscrito_de("https://elpais.com/espana/a.html") == "elpais.com"
    assert sus.dominio_suscrito_de("https://cincodias.elpais.com/a") == "elpais.com"
    assert sus.dominio_suscrito_de("https://www.elmundo.es/a.html") == "elmundo.es"
    assert sus.dominio_suscrito_de("https://noelpais.com/a") == ""
    assert sus.dominio_suscrito_de("https://abc.es/a") == ""


def test_solo_viajan_cookies_del_dominio():
    assert sus._es_del_dominio(".elpais.com", "elpais.com")
    assert sus._es_del_dominio("suscripciones.elpais.com", "elpais.com")
    assert not sus._es_del_dominio(".noelpais.com", "elpais.com")
    assert not sus._es_del_dominio("elpais.com.evil.net", "elpais.com")


def test_sin_suscripcion_no_se_leen_cookies(monkeypatch):
    from app.core.config_manager import config
    monkeypatch.setattr(config, "get", lambda s, k, d=None: [] if k == "subscription_domains" else d)

    def _no_debe_llamarse(*a, **k):
        raise AssertionError("no debe leer el navegador")
    monkeypatch.setattr(sus, "_leer_cookies", _no_debe_llamarse)
    assert sus.cookies_para("https://elpais.com/a.html") is None


def test_servicio_guarda_solo_si_mejora_y_anota_el_intento(monkeypatch):
    from app.core.database import db
    from app.core.migrations import ejecutar_migraciones
    from app.services.rss.rss_service import rss_service
    from app.services.rss.rss_fulltext import ArticuloExtraido
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    acc = db.execute_insert("INSERT INTO accounts (service_id, name, active) VALUES ('rss', 'AC', 1)")
    feed = rss_service.agregar_feed(acc, "https://completo.test/feed", "Feed completo")
    for art, contenido in (("ac-1", "<p>Solo el primer párrafo.</p>"), ("ac-2", "".join(f"<p>{p}</p>" for p in PARRAFOS))):
        db.execute("INSERT INTO rss_articles (id, feed_id, account_id, guid, link, title, content, read, "
                   "published_at) VALUES (?, ?, ?, ?, ?, 'El castillo', ?, 0, '2026-09-25T17:01:04')",
                   (art, feed, acc, art, f"https://completo.test/{art}", contenido))
    assert rss_service.texto_completo_pendiente("ac-1")

    html_completo = "".join(f"<p>{p}</p>" for p in PARRAFOS)
    monkeypatch.setattr(F, "extraer_articulo", staticmethod(
        lambda url, titulo="", timeout=20.0, motor=None, refrescar_sesion=False:
        ArticuloExtraido(html=html_completo, texto="\n\n".join(PARRAFOS), metodo="trafilatura")))

    # ac-1 solo tenía un párrafo: se sustituye por el artículo completo.
    assert rss_service.descargar_articulo_completo("ac-1") == html_completo
    fila = db.query_one("SELECT content, fulltext_at, fulltext_metodo FROM rss_articles WHERE id='ac-1'")
    assert fila[0] == html_completo and fila[1] and fila[2] == "trafilatura"
    assert not rss_service.texto_completo_pendiente("ac-1")
    assert db.query_one("SELECT COUNT(*) FROM rss_articles_fts WHERE article_db_id='ac-1'")[0] == 1

    # ac-2 ya traía el artículo entero: no se toca el contenido, pero se anota el intento.
    antes = db.query_one("SELECT content FROM rss_articles WHERE id='ac-2'")[0]
    assert rss_service.descargar_articulo_completo("ac-2") is None
    fila = db.query_one("SELECT content, fulltext_at, fulltext_metodo FROM rss_articles WHERE id='ac-2'")
    assert fila[0] == antes and fila[1] and fila[2] == ""
