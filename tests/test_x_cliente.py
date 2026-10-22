# -*- coding: utf-8 -*-
"""Cliente de X (2026-09-26): hilos, lectura, router y robustez.

Probado antes EN VIVO con una sesión real (solo lecturas). Estas pruebas fijan lo aprendido sin
red: las respuestas de X se simulan con dicts del MISMO formato que devuelve hoy TweetDetail
(datos inventados, no de ninguna cuenta real)."""
import json

import wx

from app.core.database import db
from app.core.migrations import ejecutar_migraciones


def _tuit(tid, autor="ana", responde_a=None, texto="hola"):
    """Resultado de tuit tal como viene dentro de una entrada de TweetDetail."""
    legacy = {"full_text": texto, "created_at": "Fri Sep 26 10:00:00 +0000 2026", "lang": "es",
              "conversation_id_str": "100"}
    if responde_a:
        legacy["in_reply_to_status_id_str"] = responde_a
        legacy["in_reply_to_screen_name"] = "ana"
    return {"tweet_results": {"result": {
        "__typename": "Tweet", "rest_id": tid, "legacy": legacy,
        "core": {"user_results": {"result": {"rest_id": "1", "legacy": {"screen_name": autor}}}}}}}


def _entrada(tid, **kw):
    return {"entryId": f"tweet-{tid}", "content": {"entryType": "TimelineTimelineItem",
                                                   "itemContent": _tuit(tid, **kw)}}


def _modulo(*tids, raiz="100"):
    items = [{"entryId": f"conversationthread-{tids[0]}-tweet-{t}",
              "item": {"itemContent": _tuit(t, responde_a=raiz)}} for t in tids]
    items.append({"entryId": f"conversationthread-{tids[0]}-cursor-showmore-1",
                  "item": {"itemContent": {"value": "mas"}}})
    return {"entryId": f"conversationthread-{tids[0]}",
            "content": {"entryType": "TimelineTimelineModule", "items": items}}


def _respuesta(*entradas):
    return {"data": {"threaded_conversation_with_injections_v2": {"instructions": [
        {"type": "TimelineClearCache"},
        {"type": "TimelineAddEntries", "entries": list(entradas)},
        {"type": "TimelineTerminateTimeline"}]}}}


# ---------------------------------------------------------------- TweetDetail
def test_tweet_detail_formato_actual_con_cursor_en_value():
    """El formato de X de 2026: el cursor de "más respuestas" va en content.value (twikit lo
    buscaba en itemContent y reventaba con KeyError 'itemContent')."""
    from app.services.x.x_timeline import entradas_tweet_detail
    cursor = {"entryId": "cursor-bottom-9", "content": {"entryType": "TimelineTimelineCursor",
                                                        "cursorType": "Bottom", "value": "PAG2"}}
    resp = _respuesta(_entrada("100"), _modulo("101", "102"), _modulo("103"), cursor)
    arriba, foco, abajo, sig = entradas_tweet_detail(resp, "100")
    assert arriba == [] and foco["entryId"] == "tweet-100"
    assert [e["entryId"].rsplit("-", 1)[1] for e in abajo] == ["101", "102", "103"]
    assert sig == "PAG2"


def test_tweet_detail_desde_una_respuesta_trae_lo_de_encima():
    from app.services.x.x_timeline import entradas_tweet_detail
    resp = _respuesta(_entrada("100"), _entrada("101", responde_a="100"),
                      _entrada("102", responde_a="101"), _modulo("103", raiz="102"))
    arriba, foco, abajo, sig = entradas_tweet_detail(resp, "102")
    assert [e["entryId"] for e in arriba] == ["tweet-100", "tweet-101"]
    assert foco["entryId"] == "tweet-102" and len(abajo) == 1 and sig == ""


def test_tweet_detail_formato_antiguo_y_basura_no_rompen():
    from app.services.x.x_timeline import entradas_tweet_detail
    viejo = {"entryId": "cursor-bottom-1", "content": {"itemContent": {"value": "V"}}}
    _a, foco, _b, sig = entradas_tweet_detail(_respuesta(_entrada("5"), viejo), "5")
    assert foco is not None and sig == "V"
    assert entradas_tweet_detail({}, "5") == ([], None, [], "")
    assert entradas_tweet_detail({"data": None}, "5") == ([], None, [], "")


def test_las_entradas_las_entiende_twikit():
    """Cada entrada que devolvemos se convierte en Tweet con tweet_from_data de twikit."""
    from app.services.x.x_timeline import entradas_tweet_detail
    from twikit.tweet import tweet_from_data
    _a, foco, abajo, _s = entradas_tweet_detail(_respuesta(_entrada("100"), _modulo("101")), "100")
    assert tweet_from_data(None, foco).id == "100"
    assert tweet_from_data(None, abajo[0]).id == "101"


# ---------------------------------------------------------------- datos del tuit
def test_encuesta_y_datos_extra():
    from app.services.x.x_timeline import datos_encuesta, datos_extra_tweet

    class _Poll:
        id, name = "card://1", "poll2choice_text_only"
        choices = [{"number": "1", "label": "Sí", "count": 3}, {"number": "2", "label": "No", "count": 1}]
        end_datetime_utc, counts_are_final, selected_choice = "2026-09-27", False, None
    d = json.loads(datos_encuesta(_Poll()))
    assert d["id"] == "card://1" and d["choices"][0] == {"number": "1", "label": "Sí", "count": 3}
    assert datos_encuesta(None) == ""
    extra = datos_extra_tweet({"in_reply_to_screen_name": "luis"}, None, None)
    assert extra == {"in_reply_to_screen_name": "luis", "quoted_author": "", "quoted_text": "",
                     "poll_json": ""}


def test_textos_de_lectura_de_x():
    from app.ui.x_lectura import describir_media_x, texto_cita_x, texto_encuesta_x, indicadores_x
    assert describir_media_x(json.dumps([{"type": "photo", "alt": "Un perro"},
                                         {"type": "video", "video": True}])) == \
        "Imagen: Un perro. Vídeo (sin descripción)"
    assert texto_cita_x("Ana (@ana)", "Texto\ncitado") == "Cita de Ana (@ana): Texto citado"
    assert texto_cita_x("", "x") == ""
    poll = json.dumps({"id": "c", "name": "n", "selected": "2", "final": False,
                       "choices": [{"number": "1", "label": "Sí", "count": 1},
                                   {"number": "2", "label": "No", "count": 0}]})
    assert texto_encuesta_x(poll) == "Encuesta: Sí, 1 voto; No, 0 votos (tu voto). Abierta"
    assert indicadores_x("luis", 1, 0, 1, "5", "") == "RT de @luis, Respuesta, Me gusta, Guardado"


def test_respuesta_en_el_visor_de_conversacion():
    from app.ui.x_hilos import respuesta_a_en_hilo
    por_id = {"100": "ana"}
    assert respuesta_a_en_hilo({"in_reply_to": "100"}, por_id) == "En respuesta a @ana"
    assert respuesta_a_en_hilo({"in_reply_to": "7", "in_reply_to_screen_name": "luis"}, por_id) == \
        "En respuesta a @luis"
    assert respuesta_a_en_hilo({"in_reply_to": "7"}, por_id) == \
        "En respuesta a una publicación no disponible"
    assert respuesta_a_en_hilo({"in_reply_to": ""}, por_id) == ""


# ---------------------------------------------------------------- robustez
def test_fallos_pasajeros_de_x():
    """404 SIN cuerpo (X rechaza la cabecera de transacción de esa petición): se repite. 404 con
    mensaje (el recurso no existe): no."""
    from app.services.x.x_sesion import _SesionMixin

    class NotFound(Exception):
        pass
    es = _SesionMixin._es_transitorio
    assert es(NotFound('status: 404, message: ""'))
    assert not es(NotFound('status: 404, message: "{"errors":[{"code":34}]}"'))
    assert es(Exception("status: 503, message: x"))
    assert not es(ValueError("otra cosa"))


def test_reintento_de_404_vacio_y_escrituras(monkeypatch):
    from app.services.x.x_service import XService
    from app.services.x.x_common import XServiceError
    s = XService()
    llamadas = []

    def _run(coro, timeout=60):
        llamadas.append(1)
        if len(llamadas) < 3:
            e = XServiceError("rechazo")
            e.transitorio, e.es_404, e.auth = True, True, True
            raise e
        return "ok"
    monkeypatch.setattr(s, "_run", _run)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert s._run_con_reauth(1, lambda: None) == "ok" and len(llamadas) == 3
    # Una escritura NO repite un 5xx (podría haberse publicado ya).
    llamadas.clear()

    def _run_5xx(coro, timeout=60):
        llamadas.append(1)
        e = XServiceError("500")
        e.transitorio, e.es_404, e.auth = True, False, False
        raise e
    monkeypatch.setattr(s, "_run", _run_5xx)
    try:
        s._run_con_reauth(1, lambda: None, escritura=True)
    except XServiceError:
        pass
    assert len(llamadas) == 1


def test_router_prefiere_twikit_y_cae_a_twscrape(monkeypatch):
    from app.services.x.x_service import XService
    s = XService()
    monkeypatch.setattr(s, "twscrape_disponible", lambda: True)
    orden = [n for n, _f in s._fuentes(lambda: 1, lambda: 2)]
    assert orden == ["twikit", "twscrape"]
    monkeypatch.setattr(s, "_tw_hilo", lambda a, t: (_ for _ in ()).throw(RuntimeError("roto")))
    monkeypatch.setattr(s, "_tws_hilo", lambda a, t: [{"tweet_id": "1"}])
    assert s.r_hilo(1, "1") == [{"tweet_id": "1"}]
    # Los buffers nuevos pasan por el router.
    monkeypatch.setattr(s, "r_hilo", lambda a, t: [{"tweet_id": t}])
    assert s.r_timeline(1, "thread:77", 20) == [{"tweet_id": "77"}]


def test_confirmar_no_ya_no_equivale_a_si():
    """confirmar() devuelve el id de wx y ~25 sitios lo usaban como booleano: "No" no
    cancelaba (p. ej. "¿Borrar esta publicación?" → No → se borraba)."""
    from app.ui.accessible_controls import RespuestaConfirmar
    assert bool(RespuestaConfirmar(wx.ID_YES)) is True
    assert bool(RespuestaConfirmar(wx.ID_NO)) is False
    assert bool(RespuestaConfirmar(wx.ID_CANCEL)) is False
    assert RespuestaConfirmar(wx.ID_NO) == wx.ID_NO and RespuestaConfirmar(wx.ID_YES) != wx.ID_NO


# ---------------------------------------------------------------- base de datos y bandeja
def test_migracion_v25_y_bandeja_lee_menciones_y_cita():
    ejecutar_migraciones()
    cols = {r[1] for r in db.query("PRAGMA table_info(x_tweets)")}
    assert {"in_reply_to_screen_name", "quoted_author", "quoted_text", "poll_json"} <= cols
    acc = 4401
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('x', 'X', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'X', 'x', 1)", (acc,))
    db.execute("INSERT OR REPLACE INTO x_tweets (id, account_id, tweet_id, author_name, text, "
               "created_at, quoted_author, quoted_text) VALUES (?,?,?,?,?,?,?,?)",
               (f"{acc}:9", acc, "9", "Ana", "Mira esto", "2026-09-26T10:00:00+00:00",
                "Luis (@luis)", "Lo citado"))
    db.execute("INSERT OR REPLACE INTO x_timeline_tweets (account_id, timeline, tweet_db_id, position) "
               "VALUES (?, 'mentions', ?, 0)", (acc, f"{acc}:9"))
    from app.ui.mf_bandeja import _Mf_bandeja
    filas = _Mf_bandeja._inbox_filas_de_fuente(
        _Mf_bandeja(), {"red": "x", "account_id": acc, "buffer": "mentions_tl"})
    assert [f["content"] for f in filas] == ["Mira esto: Cita de Luis (@luis): Lo citado"]
