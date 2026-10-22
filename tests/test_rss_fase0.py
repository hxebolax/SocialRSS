# -*- coding: utf-8 -*-
"""RSS, fase 0 del informe de BlindRSS (2026-09-26): fallos propios corregidos.

- La sincronización periódica con el servidor no arrancaba al abrir la app.
- La cola de descargas de podcasts no se retomaba (lo interrumpido quedaba "descargando").
- Los datos de Podcast 2.0 se guardaban pero no se mostraban.
- Búsquedas guardadas y alertas no se podían editar ni desactivar."""
import json

from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.rss.rss_service import rss_service

ACC = 6601
ART = "fase0:1"


def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'R', 'rss', 1)", (ACC,))
    db.execute("INSERT OR IGNORE INTO rss_feeds (id, account_id, title, url) VALUES (?, ?, ?, ?)",
               (ACC, ACC, "Mi podcast", "https://ejemplo.test/podcast.xml"))
    db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, "
               "published_at, season, episode_num, duration_seconds, explicit, podcast_json) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (ART, ACC, ACC, "Episodio", "https://ejemplo.test/e1", "<p>Notas</p>",
                "2026-09-26T10:00:00", 2, 5, 2710, 1,
                json.dumps({"transcript": {"url": "https://ejemplo.test/t.vtt"},
                            "chapters": {"url": "https://ejemplo.test/c.json"},
                            "person": {"role": "host", "url": "https://ejemplo.test/ana"}})))


def test_sync_arranca_al_abrir_si_hay_cuentas_configuradas(monkeypatch):
    _preparar()
    lanzado = []
    monkeypatch.setattr(rss_service, "_arrancar_sync_periodico", lambda: lanzado.append(1))
    config.set("rss_sync", str(ACC), {})
    assert rss_service.iniciar_sync_al_arrancar() is False and lanzado == []
    config.set("rss_sync", str(ACC), {"tipo": "greader", "base_url": "https://x", "activo": True})
    try:
        assert rss_service.iniciar_sync_al_arrancar() is True and lanzado == [1]
    finally:
        config.set("rss_sync", str(ACC), {})


def test_cola_de_descargas_se_retoma_al_abrir(monkeypatch):
    _preparar()
    arrancado = []
    monkeypatch.setattr(rss_service, "_arrancar_worker", lambda: arrancado.append(1))
    db.execute("DELETE FROM rss_download_queue")
    db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, "
               "published_at) VALUES (?,?,?,?,?,?,?)",
               ("fase0:pausado", ACC, ACC, "Otro", "https://ejemplo.test/e2", "", "2026-09-26T09:00:00"))
    for aid, estado in ((ART, "descargando"), ("fase0:pausado", "pausado")):
        db.execute("INSERT INTO rss_download_queue (article_id, account_id, url, estado, updated_at) "
                   "VALUES (?, ?, ?, ?, '2026-09-26')", (aid, ACC, "https://ejemplo.test/e.mp3", estado))
    assert rss_service.reanudar_cola_al_arrancar() == 1 and arrancado == [1]
    estados = dict(db.query("SELECT article_id, estado FROM rss_download_queue"))
    assert estados == {ART: "cola", "fase0:pausado": "pausado"}   # lo pausado sigue pausado
    db.execute("DELETE FROM rss_download_queue")
    assert rss_service.reanudar_cola_al_arrancar() == 0


def test_datos_de_podcast_2_0_se_muestran():
    _preparar()
    db.execute("DELETE FROM rss_enclosures WHERE article_id = ?", (ART,))
    for i, (url, mime, tam) in enumerate((("https://ejemplo.test/e.mp3", "audio/mpeg", 31457280),
                                         ("https://ejemplo.test/e.mp4", "video/mp4", 0))):
        db.execute("INSERT INTO rss_enclosures (article_id, url, mime, length, es_audio, orden) "
                   "VALUES (?,?,?,?,?,?)", (ART, url, mime, tam, 1 if "audio" in mime else 0, i))
    texto = rss_service.texto_detalles_episodio(ART)
    assert "Episodio: temporada 2, episodio 5" in texto
    assert "Duración: 45:10" in texto and "Contenido explícito" in texto
    assert "Transcripción: https://ejemplo.test/t.vtt" in texto
    assert "Capítulos: https://ejemplo.test/c.json" in texto
    assert "Persona (host): https://ejemplo.test/ana" in texto
    assert "Adjuntos (2):" in texto and "audio/mpeg, 30.0 MB" in texto
    assert rss_service.texto_detalles_episodio("no-existe") == ""


def test_editar_busquedas_y_alertas():
    _preparar()
    bid = rss_service.crear_busqueda_guardada(ACC, "Clima", "clima")
    rss_service.editar_busqueda_guardada(bid, "Tiempo", "tiempo OR clima")
    assert {"id": bid, "name": "Tiempo", "query": "tiempo OR clima"} in \
        rss_service.obtener_busquedas_guardadas(ACC)
    aid = rss_service.crear_alerta(ACC, "nvda", False)
    rss_service.editar_alerta(aid, "NVDA|JAWS", True)
    rss_service.activar_alerta(aid, False)
    alerta = next(a for a in rss_service.obtener_alertas(ACC) if a["id"] == aid)
    assert alerta == {"id": aid, "keyword": "NVDA|JAWS", "is_regex": True, "active": False}
    rss_service.activar_alerta(aid, True)
    assert next(a for a in rss_service.obtener_alertas(ACC) if a["id"] == aid)["active"] is True
