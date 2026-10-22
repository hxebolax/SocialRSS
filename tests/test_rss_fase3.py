# -*- coding: utf-8 -*-
"""RSS, fase 3 del informe de BlindRSS (2026-09-26): podcasts.

Cola persistente, capítulos (Podcast 2.0 e ID3), reanudación (posición compartida, retroceso,
terminado cerca del final), fin del episodio (leído + siguiente de la cola), velocidad hasta 3x
y episodios antiguos del archivo de Internet. Sin sonido: backend simulado."""
import io
import json

import pytest

from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.media_player import cola_podcasts
from app.services.media_player.player2_service import KIND_PODCAST, player2_service
from app.services.rss import rss_archivo, rss_capitulos

ACC = 6901


def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'R', 'rss', 1)", (ACC,))
    db.execute("INSERT OR IGNORE INTO rss_feeds (id, account_id, title, url) VALUES (?, ?, 'Pod', 'https://p.test/rss')",
               (ACC, ACC))
    for aid in ("ep:1", "ep:2", "ep:3"):
        db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, published_at, "
                   "read, enclosure_url, is_podcast) VALUES (?, ?, ?, ?, '', '', '2026-01-01', 0, ?, 1)",
                   (aid, ACC, ACC, f"Episodio {aid}", f"https://p.test/{aid}.mp3"))
    cola_podcasts.vaciar()


# ------------------------------------------------------------------ cola
def test_cola_persistente():
    _preparar()
    assert cola_podcasts.encolar("ep:1", "Uno") and cola_podcasts.encolar("ep:2", "Dos")
    assert cola_podcasts.encolar("ep:1", "Uno") is False
    assert cola_podcasts.mover("ep:2", -1) and [a for a, _ in cola_podcasts.listar()] == ["ep:2", "ep:1"]
    assert cola_podcasts.mover("ep:2", -1) is False
    assert cola_podcasts.sacar_siguiente() == ("ep:2", "Dos")
    assert cola_podcasts.listar() == [("ep:1", "Uno")]
    cola_podcasts.vaciar()
    assert cola_podcasts.sacar_siguiente() is None


# ------------------------------------------------------------------ reproductor (simulado)
class _Backend:
    def __init__(self, dur=3600.0):
        self.pos, self.dur, self.estado, self.tempo = 0.0, dur, "reproduciendo", None
        self.al_terminar = None

    def obtener_posicion(self):
        return self.pos

    def obtener_duracion(self):
        return self.dur

    def obtener_estado(self):
        return self.estado

    def seek(self, s):
        self.pos = s

    def obtener_velocidad(self):
        return 1.0


@pytest.fixture
def reproductor(monkeypatch):
    _preparar()
    falso = _Backend()
    monkeypatch.setattr(player2_service, "backend", falso)
    player2_service.current_url = "https://p.test/ep:1.mp3"
    player2_service.current_title = "Episodio 1"
    player2_service.current_kind = KIND_PODCAST
    player2_service.current_article_id = "ep:1"
    player2_service._clave_historial = "rss:ep:1"
    player2_service.capitulos = []
    db.execute("DELETE FROM play_history")
    yield player2_service, falso
    player2_service.current_url = None
    player2_service.current_article_id = None
    player2_service._clave_historial = None
    player2_service.current_kind = ""


def test_posicion_compartida_y_retroceso(reproductor):
    srv, backend = reproductor
    backend.pos = 600.0
    srv._guardar_posicion()
    # La copia descargada (otra ruta) retoma donde se dejó el streaming, 5 s antes.
    assert srv.posicion_de_reanudacion(r"C:\podcasts\ep1.mp3", "ep:1") == 595.0
    # Las posiciones antiguas (guardadas por dirección) siguen valiendo.
    srv.historial.guardar_progreso("https://viejo.test/a.mp3", "A", "podcast", None, 100.0, 3600.0)
    assert srv.posicion_de_reanudacion("https://viejo.test/a.mp3", "otro") == 95.0


def test_terminado_a_menos_de_un_minuto(reproductor):
    srv, backend = reproductor
    backend.pos = 3560.0          # quedan 40 s de 3600
    srv._guardar_posicion()
    assert srv.historial.obtener_progreso("rss:ep:1") == 0.0      # terminado: empieza de cero
    fila = db.query_one("SELECT finished FROM play_history WHERE media_url = 'rss:ep:1'")
    assert fila[0] == 1


def test_fin_del_episodio_marca_leido_y_sigue_la_cola(reproductor, monkeypatch):
    srv, backend = reproductor
    import wx
    monkeypatch.setattr(wx, "CallAfter", lambda f, *a, **k: f(*a, **k))
    lanzados = []
    from app.services.rss.rss_service import rss_service
    monkeypatch.setattr(rss_service, "reproducir_articulo_audio",
                        lambda aid, callback=None: lanzados.append(aid))
    import app.core.speech as speech
    monkeypatch.setattr(speech, "notificar", lambda *a, **k: None)
    cola_podcasts.encolar("ep:2", "Dos")
    backend.pos = backend.dur
    srv._al_terminar_pista()
    assert db.query_one("SELECT read FROM rss_articles WHERE id = 'ep:1'")[0] == 1
    assert lanzados == ["ep:2"] and cola_podcasts.listar() == []


def test_capitulos_saltar(reproductor):
    srv, backend = reproductor
    srv.capitulos = [(0.0, "Intro"), (120.0, "Tema"), (600.0, "Despedida")]
    backend.pos = 130.0
    assert srv.capitulo_actual() == 1
    assert srv.capitulo_relativo(1) == "Despedida" and backend.pos == 600.0
    backend.pos = 610.0
    assert srv.capitulo_relativo(-1) == "Despedida"        # >3 s dentro: vuelve a su inicio
    assert srv.capitulo_relativo(-1) == "Tema"             # ya al inicio: al anterior


def test_velocidad_hasta_3x(monkeypatch):
    usado = []
    monkeypatch.setattr(player2_service.backend, "establecer_velocidad", lambda v: usado.append(v))
    monkeypatch.setattr(player2_service, "current_kind", KIND_PODCAST)
    anterior = player2_service._velocidad_guardada
    try:
        player2_service.establecer_velocidad(3.7)
        assert player2_service._velocidad_guardada == 3.0 and usado[-1] == 3.0
        player2_service.establecer_velocidad(2.35)
        assert player2_service._velocidad_guardada == 2.35
    finally:
        player2_service.establecer_velocidad(anterior)


# ------------------------------------------------------------------ capítulos
def test_capitulos_podcast20(monkeypatch):
    _preparar()
    db.execute("UPDATE rss_articles SET podcast_json = ? WHERE id = 'ep:3'",
               (json.dumps({"chapters": {"url": "https://p.test/c.json"}}),))

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"chapters": [{"startTime": 65, "title": "Dos"}, {"startTime": 0, "title": "Uno"},
                                 {"startTime": 30, "title": "Oculto", "toc": False}]}

    class _S:
        def get(self, *a, **k):
            return _R()
    import app.core.net as net
    monkeypatch.setattr(net, "http_session", lambda: _S())
    assert rss_capitulos.capitulos_de("ep:3") == [(0.0, "Uno"), (65.0, "Dos")]


def test_capitulos_de_mp3_local(tmp_path):
    from mutagen.id3 import ID3, CHAP, TIT2
    tag = ID3()
    tag.add(CHAP(element_id="a", start_time=0, end_time=5000, sub_frames=[TIT2(encoding=3, text=["Hola"])]))
    tag.add(CHAP(element_id="b", start_time=5000, end_time=9000, sub_frames=[TIT2(encoding=3, text=["Adiós"])]))
    f = io.BytesIO()
    tag.save(f)
    ruta = tmp_path / "e.mp3"
    ruta.write_bytes(f.getvalue() + b"\xff\xfb" + b"\0" * 100)
    assert rss_capitulos.capitulos_de("", str(ruta)) == [(0.0, "Hola"), (5.0, "Adiós")]


# ------------------------------------------------------------------ archivo de Internet
def test_elegir_capturas_repartidas():
    filas = [["timestamp", "original"]] + [[f"2020{m:02d}01000000", "u"] for m in range(1, 13)]
    elegidas = rss_archivo.elegir_capturas(filas, 4)
    assert len(elegidas) == 4 and elegidas[0][0].startswith("202001") and elegidas[-1][0].startswith("202012")


def test_recuperar_episodios_archivados(monkeypatch):
    _preparar()
    from app.services.rss.rss_service import rss_service

    def rss(items):
        cuerpo = "".join(f"<item><guid>{g}</guid><title>{t}</title><pubDate>Mon, 01 Jan 2018 10:00:00 GMT</pubDate>"
                         f"<enclosure url='https://p.test/{g}.mp3' type='audio/mpeg' length='1'/></item>"
                         for g, t in items)
        return f"<?xml version='1.0'?><rss version='2.0'><channel><title>P</title>{cuerpo}</channel></rss>".encode()

    class _R:
        def __init__(self, contenido=b"", datos=None):
            self.status_code, self.content, self._d = 200, contenido, datos

        def raise_for_status(self):
            pass

        def json(self):
            return self._d
    respuestas = {
        "cdx": _R(datos=[["timestamp", "original"], ["20180101000000", "https://p.test/rss"],
                         ["20190101000000", "https://p.test/rss"]]),
        "2018": _R(rss([("viejo-1", "Viejo uno"), ("ep:1-guid", "Ya lo tengo")])),
        "2019": _R(rss([("viejo-2", "Viejo dos"), ("viejo-1", "Viejo uno")])),
    }

    def _pedir(url, ctx=None):
        return respuestas["cdx" if "cdx" in url else ("2018" if "/2018" in url else "2019")]
    monkeypatch.setattr(rss_archivo, "_pedir", _pedir)
    # "Ya lo tengo" tiene el mismo audio que un episodio guardado: no se duplica.
    db.execute("UPDATE rss_articles SET enclosure_url = 'https://p.test/ep:1-guid.mp3' WHERE id = 'ep:1'")
    res = rss_service.recuperar_episodios_archivados(ACC)
    assert res == {"capturas": 2, "leidas": 2, "episodios": 3, "nuevos": 2}
    titulos = {r[0]: r[1] for r in db.query("SELECT title, read FROM rss_articles WHERE feed_id = ?", (ACC,))}
    assert titulos["Viejo uno"] == 1 and titulos["Viejo dos"] == 1
    assert rss_service.recuperar_episodios_archivados(ACC)["nuevos"] == 0
