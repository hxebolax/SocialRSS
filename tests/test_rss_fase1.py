# -*- coding: utf-8 -*-
"""RSS, fase 1 del informe de BlindRSS (2026-09-26): encontrar feeds y suscribirse.

- Convertir direcciones de redes en su feed y leer los feeds anunciados de una página.
- Reintento como navegador cuando un cortafuegos web rechaza a la aplicación (403/406).
- Editar un feed, incluida su dirección.
- Buscador en directorios (sin duplicados, fuentes que fallan).
- Importar suscripciones de YouTube desde Google Takeout (inglés y español)."""
import json
import zipfile

import pytest

import app.core.net as net
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.rss import rss_buscador
from app.services.rss.rss_descubrir import convertir_url_social, feeds_anunciados, normalizar_entrada
from app.services.rss.rss_service import rss_service
from app.services.rss.rss_takeout import TakeoutError, analizar_takeout

ACC = 6701
UC1 = "UC" + "a" * 22
UC2 = "UC" + "b" * 22
UC3 = "UC" + "c" * 22


def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'R', 'rss', 1)", (ACC,))
    db.execute("DELETE FROM rss_feeds WHERE account_id = ?", (ACC,))
    for fid, titulo, url in ((ACC, "Uno", "https://uno.test/feed.xml"),
                             (ACC + 1, "Dos", "https://dos.test/rss")):
        db.execute("INSERT INTO rss_feeds (id, account_id, title, url, url_normalized, http_etag, "
                   "fail_count, last_error) VALUES (?, ?, ?, ?, ?, 'etag-viejo', 4, 'Error 406')",
                   (fid, ACC, titulo, url, url))


# ------------------------------------------------------------------ direcciones
def test_normalizar_entrada_anade_https():
    assert normalizar_entrada(" ejemplo.com/feed ") == "https://ejemplo.com/feed"
    assert normalizar_entrada("http://a.test") == "http://a.test"


@pytest.mark.parametrize("url, esperado", [
    ("https://www.reddit.com/r/python/", ["https://www.reddit.com/r/python/.rss"]),
    ("reddit.com/user/pepe", ["https://www.reddit.com/user/pepe/.rss"]),
    ("https://bsky.app/profile/ana.bsky.social", ["https://bsky.app/profile/ana.bsky.social/rss"]),
    ("https://www.youtube.com/playlist?list=PL123abc",
     ["https://www.youtube.com/feeds/videos.xml?playlist_id=PL123abc"]),
    (f"https://www.youtube.com/channel/{UC1}",
     [f"https://www.youtube.com/feeds/videos.xml?channel_id={UC1}"]),
    ("https://github.com/org/repo", ["https://github.com/org/repo/releases.atom",
                                     "https://github.com/org/repo/commits.atom"]),
    ("https://github.com/org/repo/commits", ["https://github.com/org/repo/commits.atom"]),
    ("https://medium.com/@autora", ["https://medium.com/feed/@autora"]),
    ("https://lemmy.world/c/linux", ["https://lemmy.world/feeds/c/linux.xml"]),
    ("https://mastodon.social/@gargron", ["https://mastodon.social/@gargron.rss"]),
    ("https://mastodon.social/tags/rss", ["https://mastodon.social/tags/rss.rss"]),
    ("https://www.elpais.com/", []),
])
def test_convertir_url_social(url, esperado):
    assert convertir_url_social(url) == esperado


def test_feeds_anunciados_resuelve_relativas_y_quita_repetidos():
    html = ('<html><head>'
            '<link rel="alternate" type="application/rss+xml" title="Portada" href="/rss.xml">'
            '<link rel="alternate" type="application/atom+xml" href="https://b.test/atom">'
            '<link rel="alternate" type="application/rss+xml" href="/rss.xml">'
            '<link rel="stylesheet" href="/estilo.css">'
            '</head></html>')
    feeds = feeds_anunciados(html, "https://b.test/noticias/")
    assert [f["url"] for f in feeds] == ["https://b.test/rss.xml", "https://b.test/atom"]
    assert feeds[0]["titulo"] == "Portada"


# ------------------------------------------------------------------ cortafuegos (WAF)
class _Resp:
    def __init__(self, estado, cuerpo=b""):
        self.status_code = estado
        self.headers = {}
        self.history = []
        self.url = ""
        self._cuerpo = cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def iter_content(self, chunk_size=0, decode_unicode=False):
        yield self._cuerpo

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code), response=self)


class _SesionWAF:
    """Rechaza con 406 a quien no se presenta como Firefox."""

    def __init__(self):
        self.agentes = []

    def get(self, url, timeout=0, headers=None, stream=False, **kw):
        ua = (headers or {}).get("User-Agent", "")
        self.agentes.append(ua)
        return _Resp(200, b"<rss/>") if "Firefox" in ua else _Resp(406)


def test_waf_reintenta_como_navegador_y_lo_recuerda(monkeypatch):
    _preparar()
    sesion = _SesionWAF()
    monkeypatch.setattr(net, "http_session", lambda: sesion)
    assert rss_service._descargar_con_reintentos("https://uno.test/feed.xml", intentos=1,
                                                 feed_id=ACC) == b"<rss/>"
    assert len(sesion.agentes) == 2 and "Firefox" not in sesion.agentes[0]
    ua = db.query_one("SELECT custom_user_agent FROM rss_feeds WHERE id = ?", (ACC,))[0]
    assert ua == rss_service.UA_NAVEGADOR


def test_waf_no_pisa_una_identificacion_propia(monkeypatch):
    _preparar()
    db.execute("UPDATE rss_feeds SET custom_user_agent = 'MiLector/1.0' WHERE id = ?", (ACC,))
    sesion = _SesionWAF()
    monkeypatch.setattr(net, "http_session", lambda: sesion)
    with pytest.raises(Exception):
        rss_service._descargar_con_reintentos("https://uno.test/feed.xml", intentos=1,
                                              user_agent="MiLector/1.0", feed_id=ACC)
    # Con identificación propia no se prueba como navegador ni se cambia lo elegido.
    assert sesion.agentes == ["MiLector/1.0"]
    assert db.query_one("SELECT custom_user_agent FROM rss_feeds WHERE id = ?", (ACC,))[0] == "MiLector/1.0"


# ------------------------------------------------------------------ editar feed
def test_editar_feed_cambia_direccion_y_reinicia_estado():
    _preparar()
    assert rss_service.editar_feed(ACC, titulo="Nuevo título", url="uno.test/nuevo.xml") is True
    fila = db.query_one("SELECT title, url, http_etag, fail_count, last_error FROM rss_feeds "
                        "WHERE id = ?", (ACC,))
    assert fila == ("Nuevo título", "https://uno.test/nuevo.xml", None, 0, None)


def test_editar_feed_sin_cambiar_direccion_conserva_estado():
    _preparar()
    assert rss_service.editar_feed(ACC, url="https://uno.test/feed.xml") is False
    assert db.query_one("SELECT http_etag FROM rss_feeds WHERE id = ?", (ACC,))[0] == "etag-viejo"


def test_editar_feed_rechaza_direccion_de_otro_feed():
    _preparar()
    with pytest.raises(ValueError):
        rss_service.editar_feed(ACC, url="https://dos.test/rss")
    assert db.query_one("SELECT url FROM rss_feeds WHERE id = ?", (ACC,))[0] == "https://uno.test/feed.xml"


# ------------------------------------------------------------------ buscador
def test_buscador_junta_sin_repetir_y_anota_fallos(monkeypatch):
    def _a(q):
        return [rss_buscador._r("Uno", "https://x.test/feed/", "itunes", "podcast"),
                rss_buscador._r("Dos", "https://y.test/rss", "itunes", "podcast")]

    def _b(q):
        return [rss_buscador._r("Uno otra vez", "https://X.test/feed", "fyyd", "podcast")]

    def _c(q):
        raise RuntimeError("caído")
    monkeypatch.setitem(rss_buscador.TIPOS, "prueba", ("itunes", "fyyd", "gpodder"))
    monkeypatch.setitem(rss_buscador._FUENTES, "itunes", _a)
    monkeypatch.setitem(rss_buscador._FUENTES, "fyyd", _b)
    monkeypatch.setitem(rss_buscador._FUENTES, "gpodder", _c)
    lotes = []
    res = rss_buscador.buscar_en_directorios("algo", "prueba", al_llegar=lotes.append)
    # Gana la primera fuente que responda (en paralelo): se compara sin mayúsculas ni barra final.
    assert sorted(r["url"].lower().rstrip("/") for r in res["resultados"]) == \
        ["https://x.test/feed", "https://y.test/rss"]
    assert list(res["errores"]) == ["gpodder"]
    assert sum(len(lote) for lote in lotes) == 2


def test_buscador_mastodon_solo_con_una_palabra():
    assert rss_buscador._mastodon("dos palabras") == []
    assert rss_buscador._mastodon("#Accesibilidad")[0]["url"] == \
        "https://mastodon.social/tags/Accesibilidad.rss"


# ------------------------------------------------------------------ Google Takeout
def _zip(ruta, ficheros):
    with zipfile.ZipFile(ruta, "w") as z:
        for nombre, contenido in ficheros.items():
            z.writestr(nombre, contenido)
    return ruta


def test_takeout_en_ingles(tmp_path):
    base = "Takeout/YouTube and YouTube Music/"
    ruta = _zip(tmp_path / "t.zip", {
        base + "subscriptions/subscriptions.csv":
            f"Channel Id,Channel Url,Channel Title\n{UC1},http://www.youtube.com/channel/{UC1},Canal Uno\n",
        base + "channels/channel.csv":
            f"Channel ID,Channel title (Original),Channel visibility\n{UC2},Mi canal,Public\n",
        base + "playlists/playlists.csv":
            "Playlist ID,Add new videos to top,Playlist title (original),Playlist title (original) language\n"
            "PLabcdefghijkl,False,Mi lista,es\nWL,False,Ver más tarde,es\n",
        base + "playlists/Mi lista-videos.csv": "Video ID,Playlist video creation timestamp\nabc,2024\n",
        base + "history/watch-history.html":
            f'<a href="https://www.youtube.com/watch?v=x">Vídeo</a>'
            f'<a href="https://www.youtube.com/channel/{UC3}">Canal visto</a>'
            f'<a href="https://www.youtube.com/channel/{UC1}">Canal Uno</a>',
    })
    feeds = {f["url"]: f for f in analizar_takeout(ruta)}
    canal = "https://www.youtube.com/feeds/videos.xml?channel_id="
    assert feeds[canal + UC1]["fuente"] == "suscripciones" and feeds[canal + UC1]["title"] == "Canal Uno"
    assert feeds[canal + UC2]["fuente"] == "propios" and feeds[canal + UC2]["title"] == "Mi canal"
    assert feeds[canal + UC3]["fuente"] == "historial"
    lista = "https://www.youtube.com/feeds/videos.xml?playlist_id=PLabcdefghijkl"
    assert feeds[lista]["title"] == "Mi lista"
    # "Ver más tarde" (WL) no es pública y los CSV de vídeos de cada lista no son listas.
    assert len(feeds) == 4


def test_takeout_en_espanol_e_historial_json(tmp_path):
    base = "Takeout/YouTube y YouTube Music/"
    ruta = _zip(tmp_path / "t.zip", {
        base + "suscripciones/suscripciones.csv":
            f"ID del canal,URL del canal,Título del canal\n{UC1},http://www.youtube.com/channel/{UC1},Uno\n",
        base + "historial/historial-de-reproducciones.json": json.dumps([
            {"title": "Viste algo", "subtitles": [{"name": "Tres", "url": f"https://www.youtube.com/channel/{UC3}"}]}]),
    })
    feeds = {f["url"]: f for f in analizar_takeout(ruta)}
    canal = "https://www.youtube.com/feeds/videos.xml?channel_id="
    assert feeds[canal + UC1]["title"] == "Uno" and feeds[canal + UC1]["fuente"] == "suscripciones"
    assert feeds[canal + UC3]["title"] == "Tres" and feeds[canal + UC3]["fuente"] == "historial"


def test_takeout_errores(tmp_path):
    no_zip = tmp_path / "a.zip"
    no_zip.write_text("hola")
    with pytest.raises(TakeoutError):
        analizar_takeout(no_zip)
    with pytest.raises(TakeoutError):
        analizar_takeout(_zip(tmp_path / "b.zip", {"Takeout/Drive/x.txt": "x"}))


def test_takeout_vista_previa_marca_duplicados_y_no_marca_historial(tmp_path):
    _preparar()
    canal = "https://www.youtube.com/feeds/videos.xml?channel_id="
    db.execute("INSERT INTO rss_feeds (id, account_id, title, url, url_normalized) VALUES (?, ?, ?, ?, ?)",
               (ACC + 2, ACC, "Ya suscrito", canal + UC1, canal + UC1))
    base = "Takeout/YouTube and YouTube Music/"
    ruta = _zip(tmp_path / "t.zip", {
        base + "subscriptions/subscriptions.csv": f"Channel Id,Channel Title\n{UC1},Uno\n{UC2},Dos\n",
        base + "history/watch-history.html": f'<a href="https://www.youtube.com/channel/{UC3}">Tres</a>',
    })
    res = {f["url"]: f for f in rss_service.analizar_takeout_youtube(ACC, str(ruta))}
    assert res[canal + UC1]["estado"] == "duplicado"
    assert res[canal + UC2]["estado"] == "nuevo" and res[canal + UC2]["marcar"] is True
    assert res[canal + UC3]["marcar"] is False
    assert res[canal + UC2]["category_path"] == ["YouTube"]
