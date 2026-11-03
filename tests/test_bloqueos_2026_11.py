# -*- coding: utf-8 -*-
"""Bloqueos de la interfaz del 2026-11-03: caché de tipos de Mastodon.py, registro asíncrono con
tope de tamaño y diagnóstico de CPU del vigilante."""
import json
import logging
import os
import threading
import time


_ESTADO = {
    "id": "117552530849555427", "created_at": "2026-11-03T12:34:31.000Z", "in_reply_to_id": None,
    "sensitive": False, "spoiler_text": "", "visibility": "public", "language": "es", "uri": "x",
    "url": "x", "replies_count": 0, "reblogs_count": 0, "favourites_count": 0, "favourited": False,
    "reblogged": False, "bookmarked": False, "content": "<p>hola</p>", "reblog": None,
    "account": {"id": "1", "username": "a", "acct": "a@b", "display_name": "A",
                "created_at": "2024-01-01T00:00:00.000Z", "note": "", "url": "x", "emojis": [],
                "fields": [{"name": "a", "value": "b", "verified_at": None}]},
    "media_attachments": [{"id": "9", "type": "image", "url": "x", "preview_url": "x",
                           "meta": {"original": {"width": 1, "height": 1}}, "description": "d"}],
    "mentions": [], "tags": [{"name": "t", "url": "x"}], "emojis": [], "card": None, "poll": None,
}


def test_cache_de_tipos_de_mastodon_misma_salida_y_mucho_mas_rapida():
    import typing
    from mastodon import types_base
    from mastodon.return_types import Status
    from app.services.mastodon import mastodon_rendimiento
    original = types_base.get_type_hints
    try:
        types_base.get_type_hints = typing.get_type_hints   # sin caché, como trae la librería
        mastodon_rendimiento._aplicado = False
        t = time.perf_counter()
        for _ in range(10):
            sin = Status(**json.loads(json.dumps(_ESTADO)))
        lento = time.perf_counter() - t
        mastodon_rendimiento.aplicar()
        assert getattr(types_base.get_type_hints, "_socialrss_cache", False)
        t = time.perf_counter()
        for _ in range(10):
            con = Status(**json.loads(json.dumps(_ESTADO)))
        rapido = time.perf_counter() - t
        # La copia protege la caché: Mastodon.py hace update() sobre lo devuelto.
        h = types_base.get_type_hints(Status)
        h["inventado"] = int
        assert "inventado" not in types_base.get_type_hints(Status)
    finally:
        types_base.get_type_hints = original
        mastodon_rendimiento._aplicado = True
    # Mismos tipos y valores
    assert type(con.account) is type(sin.account)
    assert type(con.media_attachments[0]) is type(sin.media_attachments[0])
    assert con.created_at == sin.created_at and con.account.fields[0].name == "a"
    assert rapido < lento / 3


def test_registro_asincrono_con_tope_y_limpieza(tmp_path, monkeypatch):
    import app.core.logger as L
    raiz = logging.getLogger()
    previos, nivel = list(raiz.handlers), raiz.level
    escritor_previo = L._escritor
    monkeypatch.setattr(L, "get_data_path", lambda *a: tmp_path)
    monkeypatch.setattr(L, "_MAX_BYTES_LOG", 2000)
    for i in range(7):
        for suf in (".log", ".log.1", ".jsonl"):
            f = tmp_path / f"session_2020010{i}_000000{suf}"
            f.write_text("x")
            os.utime(f, (1000 + i, 1000 + i))
    try:
        L._escritor = None
        L.configurar_logging(debug_mode=False)
        raiz.setLevel(logging.DEBUG)
        log = logging.getLogger("prueba.bloqueos")
        for i in range(200):
            log.debug("linea %d %s", i, "x" * 40)
        log.warning("aviso con password=secreto")
        logging.getLogger("trafilatura.main_extractor").debug("ruido de trafilatura")
        logging.getLogger("websockets.client").debug("> PING")
        L.vaciar()
        nombres = sorted(p.name for p in tmp_path.iterdir())
        actual = [n for n in nombres if not n.startswith("session_2020")]
        assert any(n.endswith(".log.1") for n in actual)          # rotó al llegar al tope
        sesiones = {n.split(".", 1)[0] for n in nombres}
        assert len(sesiones) == 5                                   # 4 antiguas + la actual
        texto = "".join((tmp_path / n).read_text(encoding="utf-8")
                        for n in actual if ".log" in n)
        assert "secreto" not in texto and "aviso con password" in texto
        assert "trafilatura" not in texto and "PING" not in texto
        jsonl = [n for n in actual if n.endswith(".jsonl")]
        lineas = (tmp_path / jsonl[0]).read_text(encoding="utf-8").splitlines()
        assert len(lineas) == 1 and json.loads(lineas[0])["nivel"] == "WARNING"
    finally:
        L.vaciar()
        for h in list(raiz.handlers):
            raiz.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        for h in previos:
            raiz.addHandler(h)
        raiz.setLevel(nivel)
        L._escritor = escritor_previo


def test_vigilante_dice_que_hilo_se_come_la_cpu():
    from app.core import ui_watchdog
    fin = time.time() + 0.8

    def quemar():
        while time.time() < fin:
            sum(range(500))
    base = ui_watchdog._tiempos_cpu()
    h = threading.Thread(target=quemar, name="QuemadorDePrueba")
    h.start()
    time.sleep(0.5)
    resumen = ui_watchdog._resumen_cpu(base, 0.5)
    h.join()
    assert "QuemadorDePrueba" in resumen
    assert "la interfaz usó" in resumen
