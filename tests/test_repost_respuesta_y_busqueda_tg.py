# -*- coding: utf-8 -*-
"""Peticiones de usuario de 2026-09-26:

1. Bluesky / W Social: las respuestas no decían a quién respondían, ni en la lista ni al abrir la
   conversación (padres borrados o bloqueados se descartaban en silencio).
2. Poder elegir cómo se lee un repost: "Mensaje de Y reposteado por X" (el de siempre) o
   "X reposteó el mensaje de Y".
3. Telegram: en la búsqueda, un documento salía como "[document]" sin nombre ni pie.
4. Telegram: recordar la última búsqueda tras "Ir al mensaje".

Sin red: modelos como dicts y SQLite aislado (conftest)."""
import json
from types import SimpleNamespace

from app.core.at_lectura import (
    aplicar_repost_y_respuesta, autor_del_padre, did_de_uri, repost_de_reason,
    repost_de_reason_json, texto_respuesta_a)
from app.core.database import db
from app.core.migrations import ejecutar_migraciones

ACC = 7719
PADRE = "at://did:plc:ana/app.bsky.feed.post/p1"
HIJO = "at://did:plc:bob/app.bsky.feed.post/h1"


def _feed_view(uri=HIJO, padre_hidratado=True, reason=None):
    """FeedViewPost mínimo: Bob responde a Ana."""
    post = {"uri": uri, "cid": "cid-h1",
            "author": {"did": "did:plc:bob", "handle": "bob.test", "display_name": "Bob"},
            "record": {"text": "Estoy de acuerdo", "created_at": "2026-09-26T10:00:00Z",
                       "reply": {"root": {"uri": PADRE, "cid": "cid-p1"},
                                 "parent": {"uri": PADRE, "cid": "cid-p1"}}},
            "indexed_at": "2026-09-26T10:00:01Z"}
    fv = {"post": post}
    if padre_hidratado:
        fv["reply"] = {"parent": {"uri": PADRE, "author": {
            "did": "did:plc:ana", "handle": "ana.test", "display_name": "Ana"}}}
    if reason is not None:
        fv["reason"] = reason
    return fv


# ---------------------------------------------------------------- funciones puras
def test_did_de_uri_no_parte_por_dos_puntos():
    assert did_de_uri(PADRE) == "did:plc:ana"
    assert did_de_uri("https://x") == ""


def test_autor_del_padre_hidratado_bloqueado_y_hilo_propio():
    assert autor_del_padre(_feed_view(), PADRE, "did:plc:bob", "bob.test", "Bob") == ("ana.test", "Ana")
    # Padre bloqueado: blockedAuthor solo trae el DID, no sirve para leerlo.
    fv = _feed_view()
    fv["reply"]["parent"] = {"uri": PADRE, "blocked": True, "author": {"did": "did:plc:ana"}}
    assert autor_del_padre(fv, PADRE, "did:plc:bob", "bob.test", "Bob") == ("", "")
    # PostView (búsqueda, menciones): sin hidratar; solo se sabe si es un hilo propio.
    pv = _feed_view(padre_hidratado=False)["post"]
    assert autor_del_padre(pv, PADRE, "did:plc:bob", "bob.test", "Bob") == ("", "")
    assert autor_del_padre(pv, PADRE, "did:plc:ana", "ana.test", "Ana") == ("ana.test", "Ana")


def test_repost_desde_reason_y_desde_json_guardado():
    reason = {"$type": "app.bsky.feed.defs#reasonRepost",
              "by": {"handle": "carla.test", "displayName": "Carla"}}
    assert repost_de_reason(reason) == ("carla.test", "Carla")
    assert repost_de_reason_json(json.dumps(reason)) == ("carla.test", "Carla")
    assert repost_de_reason_json(json.dumps({"$type": "app.bsky.feed.defs#reasonPin"})) == ("", "")
    assert repost_de_reason_json("") == ("", "")
    assert repost_de_reason_json("no es json") == ("", "")


def _aplicar(**kw):
    base = {"author": "Ana", "username": "@ana.test", "content": "Hola"}
    opciones = dict(red="bluesky", autor="Ana", handle="ana.test", repost_handle="",
                    repost_name="", reply_handle="", reply_name="", formato_repost="original",
                    prefijo_respuesta=False, plantilla="De {author}. {content}.",
                    con_usuario=False)
    opciones.update(kw)
    return aplicar_repost_y_respuesta(base, **opciones)


def test_por_defecto_el_repost_se_lee_como_siempre():
    v = _aplicar(repost_handle="carla.test", repost_name="Carla")
    assert v["content"] == "[Reposteado por @carla.test] Hola"
    assert v["author"] == "Ana" and v["username"] == "@ana.test"
    v = _aplicar(red="wsocial", repost_handle="carla.test", repost_name="Carla")
    assert v["content"] == "[Republicado por @carla.test] Hola"


def test_formato_quien_repostea_primero():
    v = _aplicar(repost_handle="carla.test", repost_name="Carla", formato_repost="repostea")
    assert v["author"] == "Carla"
    assert v["username"] == "@carla.test"
    assert v["content"] == "Reposteó el mensaje de Ana: Hola"
    v = _aplicar(repost_handle="carla.test", repost_name="Carla", formato_repost="repostea",
                 con_usuario=True)
    assert v["author"] == "Carla (@carla.test)"
    assert v["content"].startswith("Reposteó el mensaje de Ana (@ana.test): ")
    v = _aplicar(red="wsocial", repost_handle="carla.test", repost_name="Carla",
                 formato_repost="repostea")
    assert v["content"].startswith("Republicó el mensaje de Ana")


def test_sin_repost_nada_cambia():
    v = _aplicar(formato_repost="repostea")
    assert v == {"author": "Ana", "username": "@ana.test", "content": "Hola", "reply_to": ""}


def test_respuesta_campo_y_prefijo_opcional():
    assert texto_respuesta_a("ana.test") == "En respuesta a @ana.test"
    assert texto_respuesta_a("", "") == ""
    # Por defecto NO se toca el texto: solo se rellena el campo {reply_to}.
    v = _aplicar(reply_handle="luis.test")
    assert v["content"] == "Hola" and v["reply_to"] == "En respuesta a @luis.test"
    v = _aplicar(reply_handle="luis.test", prefijo_respuesta=True)
    assert v["content"] == "En respuesta a @luis.test: Hola"
    # Si la plantilla ya tiene {reply_to}, no se repite en el texto.
    v = _aplicar(reply_handle="luis.test", prefijo_respuesta=True,
                 plantilla="De {author}. {reply_to}. {content}.")
    assert v["content"] == "Hola"


def test_plantillas_ofrecen_reply_to_solo_en_at():
    from app.core.plantillas import definicion
    for clave in ("bluesky_post", "wsocial_post"):
        assert "reply_to" in [c for c, _d in definicion(clave)["campos"]]
    # X también lo tiene desde que guarda in_reply_to_screen_name (migración v25).
    assert "reply_to" in [c for c, _d in definicion("x_post")["campos"]]
    # El valor por defecto de la plantilla no cambia (nadie nota nada si no lo pide).
    assert definicion("bluesky_post")["default"] == "De {author}. {content}. {date}. {counts}."


# ---------------------------------------------------------------- base de datos
def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('bluesky', 'Bluesky', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) "
               "VALUES (?, 'B', 'bluesky', 1)", (ACC,))


def test_migracion_anade_columnas_de_respuesta():
    _preparar()
    for tabla in ("bluesky_posts", "wsocial_posts"):
        cols = {r[1] for r in db.query(f"PRAGMA table_info({tabla})")}
        assert {"reply_parent_handle", "reply_parent_name"} <= cols


def test_bluesky_guarda_a_quien_responde_y_no_lo_pierde(monkeypatch):
    _preparar()
    from app.services.bluesky.bluesky_service import BlueskyService
    s = BlueskyService()
    monkeypatch.setattr(s, "prefs_moderacion", lambda acc: {}, raising=False)
    db.execute("DELETE FROM bluesky_posts WHERE id = ?", (f"{ACC}:{HIJO}",))
    assert s._procesar_y_guardar_post(ACC, _feed_view()) is not None
    fila = db.query_one("SELECT reply_parent_handle, reply_parent_name FROM bluesky_posts "
                        "WHERE id = ?", (f"{ACC}:{HIJO}",))
    assert tuple(fila) == ("ana.test", "Ana")
    # Llega otra vez SIN hidratar (búsqueda) y por el atajo de caché: se conserva.
    po = s._procesar_y_guardar_post(ACC, _feed_view(padre_hidratado=False)["post"])
    assert po.reply_parent_uri == PADRE   # el atajo ya devuelve las referencias de respuesta
    fila = db.query_one("SELECT reply_parent_handle FROM bluesky_posts WHERE id = ?",
                        (f"{ACC}:{HIJO}",))
    assert fila[0] == "ana.test"


def test_lista_bluesky_resuelve_padre_por_cache_y_repost_por_buffer(monkeypatch):
    """La lista toma a quién responde de la caché si no se guardó al descargar, y el repost del
    BUFFER: el mismo post en otro buffer (hilo, feed del autor) ya no dice "Reposteado por"."""
    _preparar()
    from app.core.config_manager import config
    from app.ui.bluesky_controller_carga import _CargaMixin
    hijo2 = "at://did:plc:bob/app.bsky.feed.post/h2"
    for pid, uri, autor, handle, padre in ((f"{ACC}:{PADRE}", PADRE, "Ana", "ana.test", ""),
                                           (f"{ACC}:{hijo2}", hijo2, "Bob", "bob.test", PADRE)):
        db.execute("INSERT OR REPLACE INTO bluesky_posts (id, account_id, uri, cid, author_name, "
                   "author_handle, content, indexed_at, reply_parent_uri, repost_by_handle) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (pid, ACC, uri, "c", autor, handle, "texto", "2026-09-26T10:00:00Z", padre,
                    "carla.test"))
    reason = json.dumps({"$type": "app.bsky.feed.defs#reasonRepost",
                         "by": {"handle": "carla.test", "displayName": "Carla"}})
    for tl, rj in (("home", reason), ("author:x", None)):
        db.execute("INSERT OR REPLACE INTO bluesky_timeline_posts (account_id, timeline, "
                   "post_db_id, sort_key, reason_json) VALUES (?,?,?,?,?)",
                   (ACC, tl, f"{ACC}:{hijo2}", 1, rj))

    ctrl = type("Ctrl", (_CargaMixin,), {})()
    ctrl.f = SimpleNamespace(active_bluesky_account_id=ACC)
    monkeypatch.setattr("app.services.bluesky.bluesky_service.prefs_moderacion",
                        lambda acc: {}, raising=False)
    sql = (f"SELECT {ctrl._SQL_COLS_POST}, {ctrl._SQL_COL_REASON} FROM bluesky_posts p "
           "JOIN bluesky_timeline_posts t ON p.id = t.post_db_id "
           "WHERE t.account_id = ? AND t.timeline = ?")
    config.set("bluesky", "formato_repost", "original")
    config.set("bluesky", "prefijo_respuesta", False)
    en_inicio = ctrl._valores_post(db.query_one(sql, (ACC, "home")))[1]
    en_autor = ctrl._valores_post(db.query_one(sql, (ACC, "author:x")))[1]
    assert en_inicio["reply_to"] == "En respuesta a @ana.test"
    assert en_inicio["content"].startswith("[Reposteado por @carla.test] ")
    assert not en_autor["content"].startswith("[Reposteado")
    config.set("bluesky", "formato_repost", "repostea")
    try:
        v = ctrl._valores_post(db.query_one(sql, (ACC, "home")))[1]
        assert v["author"] == "Carla" and v["content"].startswith("Reposteó el mensaje de Bob")
    finally:
        config.set("bluesky", "formato_repost", "original")


# ---------------------------------------------------------------- Telegram
def test_resultado_de_busqueda_con_documento_dice_nombre_y_pie():
    from app.ui.telegram_busqueda import etiqueta_adjunto, texto_resultado
    doc = {"media": "document", "file_name": "acta.pdf", "texto": "El acta de ayer"}
    assert texto_resultado(doc) == "[Documento: acta.pdf] El acta de ayer"
    assert texto_resultado({"media": "document", "file_name": "acta.pdf", "texto": ""}) == \
        "[Documento: acta.pdf]"
    # Un audio con duración conserva la duración Y añade el nombre.
    assert etiqueta_adjunto({"media": "audio", "duration": 200, "file_name": "tema.mp3"}) == \
        "[Audio 3:20: tema.mp3]"
    assert texto_resultado({"media": "", "texto": "solo texto"}) == "solo texto"
    assert etiqueta_adjunto({"media": "poll"}) == "[Encuesta]"


# ---------------------------------------------------------------- 2026-09-26 (2.ª tanda)
# La bandeja no aplicaba "En respuesta a", y al responder no se sabía a quién se respondía ni a
# quién llegaba el aviso (en Bluesky lo decide el servidor: autores de encima en el hilo).
def test_en_respuesta_a_ti_como_la_app_oficial():
    assert texto_respuesta_a("yo.test", "", mi_handle="yo.test") == "En respuesta a ti"
    assert texto_respuesta_a("yo.test", "", mi_handle="@YO.test") == "En respuesta a ti"
    assert texto_respuesta_a("ana.test", "", mi_handle="yo.test") == "En respuesta a @ana.test"
    v = _aplicar(reply_handle="yo.test", prefijo_respuesta=True, mi_handle="yo.test")
    assert v["content"] == "En respuesta a ti: Hola"


def test_menciones_al_responder_estilo_mastodon():
    from app.core.at_lectura import menciones_al_responder
    texto = "Hablando con @luis.test y @yo.test, y otra vez @Luis.test"
    assert menciones_al_responder("ana.test", texto, "yo.test") == "@ana.test @luis.test "
    assert menciones_al_responder("yo.test", "sin menciones", "yo.test") == ""


def test_texto_del_campo_respondiendo_a():
    from app.core.at_lectura import texto_contexto_respuesta
    ctx = {"padre": {"handle": "oscar.test", "nombre": "Óscar", "texto": "Ese matiz\nes clave"},
           "avisados": [{"handle": "oscar.test", "nombre": "Óscar"},
                        {"handle": "hanky.test", "nombre": "Hanky"}]}
    t = texto_contexto_respuesta(ctx)
    assert t.splitlines()[0] == "Óscar (@oscar.test): Ese matiz es clave"
    assert t.splitlines()[1] == ("Recibirán aviso de tu respuesta: Óscar (@oscar.test) y "
                                 "Hanky (@hanky.test). No hace falta mencionarlos.")
    ctx["avisados"] = ctx["avisados"][:1]
    assert "Recibirá aviso de tu respuesta: Óscar (@oscar.test). No hace falta mencionarlo." in \
        texto_contexto_respuesta(ctx)
    ctx["avisados"] = []
    assert "Es una publicación tuya" in texto_contexto_respuesta(ctx)
    assert "no está disponible" in texto_contexto_respuesta({"padre": None, "avisados": []})


def test_contexto_respuesta_sigue_la_regla_oficial_de_avisos(monkeypatch):
    """Padre + hasta 3 por encima (REPLY_NOTIF_DEPTH = 5 con la respuesta en altura 0), sin
    repetir autores, sin la cuenta propia, y parando en una publicación no disponible."""
    from app.services.bluesky import bluesky_publicar
    from app.services.bluesky.bluesky_service import BlueskyService

    def nodo(did, handle, padre=None, texto="t"):
        return {"post": {"uri": f"at://{did}/app.bsky.feed.post/x", "author": {
            "did": did, "handle": handle, "display_name": handle.split(".")[0].title()},
            "record": {"text": texto}}, "parent": padre}

    arriba = nodo("did:e", "eva.test", nodo("did:f", "fer.test"))   # 4.º y 5.º nivel
    hilo = nodo("did:o", "oscar.test", nodo("did:h", "hanky.test",
                nodo("did:yo", "yo.test", nodo("did:h", "hanky.test", arriba))), "Ese matiz")
    pedido = {}

    class _Cli:
        def get_post_thread(self, uri, depth=None, parent_height=None):
            pedido.update(depth=depth, parent_height=parent_height)
            return {"thread": hilo}

    s = BlueskyService()
    monkeypatch.setattr(s, "obtener_cliente", lambda acc: _Cli())
    monkeypatch.setattr(s, "_yo", lambda acc: {"did": "did:yo", "handle": "yo.test"})
    monkeypatch.setattr(bluesky_publicar.task_manager, "start_task",
                        lambda f, callback=None, pass_context=False: callback(f(None), None))
    res = {}
    s.contexto_respuesta_async(1, "at://did:o/app.bsky.feed.post/x",
                               callback=lambda r, e: res.update(r=r, e=e))
    assert res["e"] is None and pedido == {"depth": 0, "parent_height": 3}
    r = res["r"]
    assert r["padre"]["handle"] == "oscar.test" and r["padre"]["texto"] == "Ese matiz"
    # 4 niveles: oscar, hanky, yo (excluido), hanky (repetido). eva ya queda fuera.
    assert [a["handle"] for a in r["avisados"]] == ["oscar.test", "hanky.test"]
    # Una publicación no disponible corta la cadena.
    hilo["parent"] = {"uri": "at://did:h/app.bsky.feed.post/y", "notFound": True}
    s.contexto_respuesta_async(1, "u", callback=lambda r, e: res.update(r=r))
    assert [a["handle"] for a in res["r"]["avisados"]] == ["oscar.test"]


def test_bandeja_lee_la_respuesta_y_el_repost_como_el_buffer():
    from app.core.config_manager import config
    from app.ui.mf_bandeja import _Mf_bandeja
    reason = json.dumps({"$type": "app.bsky.feed.defs#reasonRepost",
                         "by": {"handle": "carla.test", "displayName": "Carla"}})
    config.set("bluesky", "prefijo_respuesta", True)
    try:
        autor, texto = _Mf_bandeja._repost_y_respuesta_bluesky(
            None, ACC, "Óscar", "oscar.test", "Ese matiz", "hanky.test", "Hanky", "")
        assert (autor, texto) == ("Óscar", "En respuesta a @hanky.test: Ese matiz")
        autor, texto = _Mf_bandeja._repost_y_respuesta_bluesky(
            None, ACC, "Óscar", "oscar.test", "Ese matiz", "", "", reason)
        assert texto == "[Reposteado por @carla.test] Ese matiz"
    finally:
        config.set("bluesky", "prefijo_respuesta", False)


def test_bandeja_consulta_bluesky_con_las_columnas_nuevas():
    """La consulta de la bandeja funciona contra el esquema real y trae a quién responde."""
    _preparar()
    from app.ui.mf_bandeja import _Mf_bandeja
    from app.core.config_manager import config
    hijo3 = "at://did:plc:bob/app.bsky.feed.post/h3"
    for pid, uri, autor, handle, padre in ((f"{ACC}:{PADRE}", PADRE, "Ana", "ana.test", ""),
                                           (f"{ACC}:{hijo3}", hijo3, "Bob", "bob.test", PADRE)):
        db.execute("INSERT OR REPLACE INTO bluesky_posts (id, account_id, uri, cid, author_name, "
                   "author_handle, content, indexed_at, reply_parent_uri) VALUES (?,?,?,?,?,?,?,?,?)",
                   (pid, ACC, uri, "c", autor, handle, "texto", "2026-09-26T10:00:00Z", padre))
    db.execute("INSERT OR REPLACE INTO bluesky_timeline_posts (account_id, timeline, post_db_id, "
               "sort_key) VALUES (?, 'bandeja_prueba', ?, 1)", (ACC, f"{ACC}:{hijo3}"))
    config.set("bluesky", "prefijo_respuesta", True)
    try:
        filas = _Mf_bandeja._inbox_filas_de_fuente(
            _Mf_bandeja(), {"red": "bluesky", "account_id": ACC, "buffer": "bandeja_prueba"})
    finally:
        config.set("bluesky", "prefijo_respuesta", False)
    assert [f["content"] for f in filas] == ["En respuesta a @ana.test: texto"]


# ---------------------------------------------------------------- W Social igualado (3.ª tanda)
# Su servidor (fork oficial de W del AppView) usa la misma regla de avisos: REPLY_NOTIF_DEPTH = 5.
def test_wsocial_contexto_respuesta_con_la_misma_regla(monkeypatch):
    import sys
    from app.services.wsocial.wsocial_service import WSocialService
    mod_srv = sys.modules["app.services.wsocial.wsocial_service"]  # el módulo, no la instancia
    pedido = {}

    def nodo(did, handle, padre=None):
        return {"post": {"author": {"did": did, "handle": handle, "displayName": handle[:3]},
                         "record": {"text": "hola"}}, "parent": padre}

    class _Feed:
        def get_post_thread(self, params):
            pedido.update(params)
            return {"thread": nodo("did:o", "oscar.test", nodo("did:h", "hanky.test"))}

    cli = SimpleNamespace(app=SimpleNamespace(bsky=SimpleNamespace(feed=_Feed())))
    s = WSocialService()
    monkeypatch.setattr(s, "obtener_cliente", lambda acc: cli)
    monkeypatch.setattr(s, "_yo", lambda acc: {"did": "did:yo", "handle": "yo.test"})
    monkeypatch.setattr(mod_srv.task_manager, "start_task",
                        lambda f, callback=None, **kw: callback(f(None), None))
    res = {}
    s.contexto_respuesta_async(1, "at://did:o/app.bsky.feed.post/x",
                               callback=lambda ok, r: res.update(ok=ok, r=r))
    assert res["ok"] and pedido == {"uri": "at://did:o/app.bsky.feed.post/x", "depth": 0,
                                    "parentHeight": 3}
    assert [a["handle"] for a in res["r"]["avisados"]] == ["oscar.test", "hanky.test"]


def test_wsocial_mencion_al_responder_activada_por_defecto():
    """W Social siempre ponía "@autor": se conserva por defecto; desactivable como en Bluesky."""
    from app.core.config_manager import config
    from app.ui.wsocial_controller_acciones import texto_inicial_respuesta
    texto = "Charlando con @luis.test"
    anterior = config.get("wsocial", "mencionar_al_responder", True)
    try:
        config.set("wsocial", "mencionar_al_responder", True)
        assert texto_inicial_respuesta(ACC, "ana.test", texto) == "@ana.test @luis.test "
        config.set("wsocial", "mencionar_al_responder", False)
        assert texto_inicial_respuesta(ACC, "ana.test", texto) == ""
    finally:
        config.set("wsocial", "mencionar_al_responder", anterior)
    from app.core.config_manager import DEFAULTS
    assert DEFAULTS["wsocial"]["mencionar_al_responder"] is True
    assert DEFAULTS["bluesky"]["mencionar_al_responder"] is False
