# -*- coding: utf-8 -*-
"""Pruebas de las funciones nuevas de RSS (fases B/C/D de la auditoría 2026-07-12):
Podcast 2.0, múltiples enclosures, cola de descargas, etiquetas, galería, digest,
sincronización externa (parseo de respuestas), detección de mudanza y corpus de parser.

TODO local: sin red. La descarga real de feeds se simula parcheando la descarga HTTP.
"""
import json
from datetime import datetime

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones


@pytest.fixture(scope="module")
def cuenta_rss():
    ejecutar_migraciones()
    from app.services.rss.rss_service import rss_service
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    row = db.query_one("SELECT id FROM accounts WHERE service_id='rss' AND name='FaseB'")
    if row:
        acc = row[0]
    else:
        acc = db.execute_insert(
            "INSERT INTO accounts (service_id, name, active) VALUES ('rss', 'FaseB', 1)")
    feed = rss_service.agregar_feed(acc, "https://podcast.test/feed", "Pod de prueba")
    return {"s": rss_service, "acc": acc, "feed": feed}


# ---------------------------------------------------------------------------
# Podcast 2.0: normalización de campos
# ---------------------------------------------------------------------------

def test_normalizar_podcast_20():
    import feedparser
    from app.services.rss.rss_parseo import _normalizar_entrada, _construir_podcast_json
    xml = """<?xml version="1.0"?>
    <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
         xmlns:podcast="https://podcastindex.org/namespace/1.0">
    <channel><title>P</title><item>
      <title>Ep</title><guid>ep1</guid><link>http://p.test/e1</link>
      <enclosure url="http://p.test/e1.mp3" type="audio/mpeg" length="1000"/>
      <enclosure url="http://p.test/e1.pdf" type="application/pdf" length="50"/>
      <itunes:duration>1:02:03</itunes:duration>
      <itunes:explicit>yes</itunes:explicit>
      <itunes:season>2</itunes:season><itunes:episode>5</itunes:episode>
      <itunes:image href="http://p.test/e1.jpg"/>
      <podcast:transcript url="http://p.test/e1.srt" type="application/srt" language="es"/>
      <podcast:chapters url="http://p.test/e1.json" type="application/json+chapters"/>
      <podcast:funding url="http://p.test/donate">Apoya</podcast:funding>
      <podcast:person role="host" href="http://p.test/ana">Ana</podcast:person>
    </item></channel></rss>"""
    e = _normalizar_entrada(feedparser.parse(xml).entries[0])
    assert e["itunes_duration"] == "1:02:03"
    assert e["itunes_explicit"] is True
    # El normalizador devuelve el valor CRUDO (str o int); el job lo convierte a int.
    assert int(e["itunes_season"]) == 2 and int(e["itunes_episode"]) == 5
    assert e["episode_image"] == "http://p.test/e1.jpg"
    # Los DOS enclosures se conservan (no solo el primero de audio).
    assert len(e["enclosures"]) == 2
    pj = json.loads(_construir_podcast_json(e))
    assert pj["transcript"]["url"] == "http://p.test/e1.srt"
    assert pj["chapters"]["url"] == "http://p.test/e1.json"
    assert pj["funding"]["url"] == "http://p.test/donate"
    assert pj["person"]["role"] == "host"


def test_duracion_a_segundos():
    from app.services.rss.rss_parseo import _duracion_a_segundos
    assert _duracion_a_segundos("1:02:03") == 3723
    assert _duracion_a_segundos("2:05") == 125
    assert _duracion_a_segundos("90") == 90
    assert _duracion_a_segundos("") is None
    assert _duracion_a_segundos(None) is None


def test_formatear_duracion(cuenta_rss):
    s = cuenta_rss["s"]
    assert s.formatear_duracion(3723) == "1:02:03"
    assert s.formatear_duracion(125) == "2:05"
    assert s.formatear_duracion(0) == ""


# ---------------------------------------------------------------------------
# Integración del job: múltiples enclosures + metadatos de podcast persistidos
# ---------------------------------------------------------------------------

def test_job_guarda_enclosures_y_podcast(cuenta_rss, monkeypatch):
    s, acc, feed = cuenta_rss["s"], cuenta_rss["acc"], cuenta_rss["feed"]
    xml = ("""<?xml version="1.0"?>
    <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
    <channel><title>P</title><item>
      <title>Episodio uno</title><guid>epuno</guid><link>http://p.test/epuno</link>
      <enclosure url="http://p.test/epuno.mp3" type="audio/mpeg" length="2048"/>
      <enclosure url="http://p.test/epuno.pdf" type="application/pdf" length="10"/>
      <itunes:duration>600</itunes:duration><itunes:explicit>no</itunes:explicit>
    </item></channel></rss>""").encode("utf-8")

    # Parchear la descarga para no salir a la red: devuelve el XML como "contenido nuevo".
    def _fake(self, *a, **k):
        if k.get("devolver_meta"):
            return (xml, "", "", False, "")
        return xml
    monkeypatch.setattr(type(s), "_descargar_con_reintentos", _fake)
    s._actualizar_feed_job(None, feed)

    art = db.query_one("SELECT id, is_podcast, duration_seconds FROM rss_articles "
                       "WHERE feed_id = ? AND guid = 'epuno'", (feed,))
    assert art is not None and art[1] == 1 and art[2] == 600
    encls = s.obtener_enclosures(art[0])
    assert len(encls) == 2
    assert any(e["url"].endswith(".mp3") and e["es_audio"] for e in encls)
    assert any(e["url"].endswith(".pdf") and not e["es_audio"] for e in encls)
    meta = s.obtener_metadatos_podcast(art[0])
    assert meta["duration_seconds"] == 600 and meta["explicit"] is False


# ---------------------------------------------------------------------------
# Etiquetas múltiples
# ---------------------------------------------------------------------------

def test_etiquetas(cuenta_rss):
    s, acc, feed = cuenta_rss["s"], cuenta_rss["acc"], cuenta_rss["feed"]
    t1 = s.crear_etiqueta(acc, "Tecnología")
    t2 = s.crear_etiqueta(acc, "Audio")
    assert t1 and t2 and t1 != t2
    assert s.crear_etiqueta(acc, "Tecnología") == t1  # no duplica
    s.etiquetar_feed(t1, feed, True)
    assert t1 in s.obtener_etiquetas_de_feed(feed)
    art = db.query_one("SELECT id FROM rss_articles WHERE feed_id = ? LIMIT 1", (feed,))
    if art:
        s.etiquetar_articulo(t2, art[0], True)
        assert t2 in s.obtener_etiquetas_de_articulo(art[0])
        s.etiquetar_articulo(t2, art[0], False)
        assert t2 not in s.obtener_etiquetas_de_articulo(art[0])
    conteos = {t["id"]: t for t in s.obtener_etiquetas(acc)}
    assert conteos[t1]["feeds"] == 1
    s.borrar_etiqueta(t1)
    assert all(t["id"] != t1 for t in s.obtener_etiquetas(acc))


def test_regla_accion_tag(cuenta_rss):
    from app.services.rss.rss_rules import RSSRulesEngine
    s, acc, feed = cuenta_rss["s"], cuenta_rss["acc"], cuenta_rss["feed"]
    tag = s.crear_etiqueta(acc, "AutoTag")
    db.execute(
        "INSERT INTO rss_rules (account_id, title, pattern, field, action, action_value, active) "
        "VALUES (?, 'r', 'python', 'title', 'tag', ?, 1)", (acc, str(tag)))
    RSSRulesEngine.invalidar_plan(acc)
    db.execute("INSERT OR IGNORE INTO rss_articles (id, feed_id, account_id, guid, link, title, read) "
               "VALUES ('tagart', ?, ?, 'tagart', 'http://p.test/t', 'Aprende Python', 0)",
               (feed, acc))

    class _Art:
        id = "tagart"; feed_id = feed; title = "Aprende Python"; content = ""; author = ""
    RSSRulesEngine.evaluar_articulo(acc, _Art())
    assert tag in s.obtener_etiquetas_de_articulo("tagart")
    db.execute("DELETE FROM rss_rules WHERE account_id = ?", (acc,))
    RSSRulesEngine.invalidar_plan(acc)


# ---------------------------------------------------------------------------
# Cola de descargas de podcast (estado, sin descargar de verdad)
# ---------------------------------------------------------------------------

def test_cola_descargas_estado(cuenta_rss, monkeypatch):
    s, acc, feed = cuenta_rss["s"], cuenta_rss["acc"], cuenta_rss["feed"]
    db.execute("INSERT OR IGNORE INTO rss_articles (id, feed_id, account_id, guid, link, title, "
               "enclosure_url, is_podcast) VALUES ('dlart', ?, ?, 'dlart', 'http://p.test/d', "
               "'Episodio descargable', 'http://p.test/d.mp3', 1)", (feed, acc))
    # No arrancar el trabajador real (no queremos red): parchear el arranque.
    monkeypatch.setattr(type(s), "_arrancar_worker", lambda self: None)
    assert s.encolar_descarga("dlart") is True
    cola = s.obtener_cola_descargas(acc)
    mio = next(c for c in cola if c["article_id"] == "dlart")
    assert mio["estado"] == "cola"
    s.pausar_descarga("dlart")
    assert next(c for c in s.obtener_cola_descargas(acc)
                if c["article_id"] == "dlart")["estado"] == "pausado"
    s.reanudar_descarga("dlart")
    assert next(c for c in s.obtener_cola_descargas(acc)
                if c["article_id"] == "dlart")["estado"] == "cola"
    s.cancelar_descarga("dlart")
    assert all(c["article_id"] != "dlart" for c in s.obtener_cola_descargas(acc))


# ---------------------------------------------------------------------------
# Digest (sin IA)
# ---------------------------------------------------------------------------

def test_digest_sin_ia(cuenta_rss):
    s, acc, feed = cuenta_rss["s"], cuenta_rss["acc"], cuenta_rss["feed"]
    db.execute("INSERT OR IGNORE INTO rss_articles (id, feed_id, account_id, guid, link, title, "
               "content, read, published_at) VALUES ('dig1', ?, ?, 'dig1', 'http://p.test/g1', "
               "'Titular reciente', 'Cuerpo del artículo', 0, ?)",
               (feed, acc, datetime.now().isoformat()))
    d = s.generar_digest(acc, limite=10, con_ia=False)
    assert d["n"] >= 1 and "Titular reciente" in d["texto"] and d["con_ia"] is False


# ---------------------------------------------------------------------------
# Galería de imágenes
# ---------------------------------------------------------------------------

def test_galeria_imagenes(cuenta_rss):
    s, acc, feed = cuenta_rss["s"], cuenta_rss["acc"], cuenta_rss["feed"]
    html = '<p>Hola</p><img src="https://p.test/a.jpg" alt="Un gato"><img src="x.png">'
    db.execute("INSERT OR IGNORE INTO rss_articles (id, feed_id, account_id, guid, link, title, "
               "content) VALUES ('imgart', ?, ?, 'imgart', 'http://p.test/i', 'Con fotos', ?)",
               (feed, acc, html))
    imgs = s.obtener_imagenes_articulo("imgart")
    # Solo la http absoluta con alt; la relativa 'x.png' se descarta.
    assert len(imgs) == 1 and imgs[0]["url"] == "https://p.test/a.jpg" and imgs[0]["alt"] == "Un gato"


# ---------------------------------------------------------------------------
# Sincronización externa: parseo de respuestas (sin red)
# ---------------------------------------------------------------------------

def test_greader_parsea_suscripciones(monkeypatch):
    from app.services.rss import rss_sync
    cli = rss_sync.GReaderClient("http://srv/api/greader.php", "u", "p")

    class _Resp:
        def __init__(self, data): self._d = data
        def json(self): return self._d
    monkeypatch.setattr(cli, "_get", lambda ruta, params=None: _Resp({
        "subscriptions": [
            {"url": "http://a.test/feed", "title": "A", "categories": [{"label": "Tec"}]},
            {"url": "http://b.test/feed", "title": "B", "categories": []},
        ]}))
    subs = cli.suscripciones()
    assert subs[0]["url"] == "http://a.test/feed" and subs[0]["categorias"] == ["Tec"]
    assert subs[1]["categorias"] == []


def test_miniflux_parsea_suscripciones(monkeypatch):
    from app.services.rss import rss_sync
    cli = rss_sync.MinifluxClient("http://mf", token="tok")

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return [
            {"feed_url": "http://a.test/feed", "title": "A", "category": {"title": "Noticias"}},
            {"feed_url": "http://b.test/feed", "title": "B"},
        ]
    monkeypatch.setattr(cli, "_s", lambda: type("S", (), {"get": lambda *a, **k: _Resp()})())
    subs = cli.suscripciones()
    assert subs[0]["categorias"] == ["Noticias"] and subs[1]["categorias"] == []


def test_sync_aplica_estado_local(cuenta_rss):
    """Lo marcado como leído en el servidor se aplica al artículo local y queda anotado en
    la base de sincronización (que es lo que luego permite detectar quién cambia qué)."""
    s, acc, feed = cuenta_rss["s"], cuenta_rss["acc"], cuenta_rss["feed"]
    db.execute("INSERT OR IGNORE INTO rss_articles "
               "(id, feed_id, account_id, guid, link, title, read, published_at) "
               "VALUES ('syncart', ?, ?, 'syncart', 'http://sync.test/x', 'Sin leer', 0, ?)",
               (feed, acc, datetime.now().isoformat()))

    from app.services.rss.rss_sync import _url_clave

    class _ServidorFalso:
        def estado_reciente(self, limite=None):
            # El servidor devuelve la URL con barra final y seguimiento: debe emparejar igual.
            return {_url_clave("http://sync.test/x/?utm_source=rss"):
                    {"item_id": "i-x", "read": True, "starred": False}}

        def enviar_cambios(self, cambios):
            raise AssertionError("no debería enviar nada: el cambio viene del servidor")

    resumen = {}
    s._conciliar_estado(acc, _ServidorFalso(), resumen)
    assert resumen["leidos_aplicados"] == 1
    fila = db.query_one("SELECT read FROM rss_articles WHERE id = 'syncart'")
    assert fila[0] == 1
    base = db.query_one("SELECT item_id, read_srv FROM rss_sync_items "
                        "WHERE account_id = ? AND article_id = 'syncart'", (acc,))
    assert base == ("i-x", 1)


# ---------------------------------------------------------------------------
# Detección de mudanza (confirmación a los dos ciclos)
# ---------------------------------------------------------------------------

def test_mudanza_confirmada(cuenta_rss):
    s, acc = cuenta_rss["s"], cuenta_rss["acc"]
    feed = s.agregar_feed(acc, "http://viejo.test/feed", "Mudable")
    nueva = "http://nuevo.test/feed"
    # Primer ciclo: solo se propone (no se aplica).
    s._procesar_mudanza_feed(feed, "http://viejo.test/feed", nueva)
    r = db.query_one("SELECT url, moved_to FROM rss_feeds WHERE id = ?", (feed,))
    assert r[0] == "http://viejo.test/feed" and r[1] == nueva
    # Segundo ciclo (misma propuesta): se aplica.
    s._procesar_mudanza_feed(feed, "http://viejo.test/feed", nueva)
    r = db.query_one("SELECT url, moved_to FROM rss_feeds WHERE id = ?", (feed,))
    assert r[0] == nueva and (r[1] is None or r[1] == "")


# ---------------------------------------------------------------------------
# Corpus de compatibilidad de parser: feedparser vs fastfeedparser
# ---------------------------------------------------------------------------

def test_corpus_parser_fastfeedparser_pierde_podcast():
    """DECISIÓN sobre promover fastfeedparser a principal: el corpus demuestra que, aunque
    fastfeedparser es más rápido, NO extrae los metadatos de Podcast 2.0/iTunes que sí da
    feedparser. Por eso se mantiene como RESPALDO (no principal) — la conclusión es objetiva,
    no una intuición."""
    import feedparser
    try:
        import fastfeedparser
    except Exception:
        pytest.skip("fastfeedparser no instalado")
    xml = """<?xml version="1.0"?>
    <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
    <channel><title>P</title><item>
      <title>Ep</title><guid>c1</guid><link>http://p.test/c1</link>
      <enclosure url="http://p.test/c1.mp3" type="audio/mpeg" length="1"/>
      <itunes:duration>10:00</itunes:duration><itunes:explicit>yes</itunes:explicit>
    </item></channel></rss>"""
    fp = feedparser.parse(xml).entries[0]
    ffp = fastfeedparser.parse(xml).entries[0]
    # feedparser SÍ da la duración iTunes...
    assert fp.get("itunes_duration") == "10:00"
    # ...y fastfeedparser NO (por eso no se promueve a principal para podcasts).
    assert not ffp.get("itunes_duration")
    # Ambos coinciden en lo básico (título y enclosure), que es donde fastfeedparser acelera.
    assert fp.get("title") == "Ep"
    assert (ffp.get("title") or (ffp.get("title_detail") or {}).get("value")) == "Ep"
