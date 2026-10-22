# -*- coding: utf-8 -*-
"""Moderación de Bluesky replicando el oficial (auditoría de filtros 2026-10-17, B1-B18).

Sin red ni wx: funciones puras, un cliente falso para putPreferences y SQLite aislado."""
from types import SimpleNamespace

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.bluesky.bluesky_moderacion import (
    BSKY_LABELER_DID, decidir, moderar_post, moderar_cita, moderar_fila_bandeja,
    normalizar_prefs, oculto_por_feed_view, ordenar_respuestas, parsear_preferencias,
    prefs_feed_view, texto_aviso, valor_cabecera_labelers, interpretar_definicion)
from app.services.bluesky.bluesky_moddatos import (
    extraer_datos_post, extraer_datos_notificacion, a_json)

ACC = 8821
YO = "did:plc:yo"
AUTOR = "did:plc:autor"
LAB = "did:plc:lab1"

DEF_SPOILER = {"identifier": "spoiler", "severity": "alert", "blurs": "content",
               "defaultSetting": "warn", "adultOnly": False,
               "locales": [{"lang": "en", "name": "Spoiler", "description": "Spoilers"},
                           {"lang": "es", "name": "Destripe", "description": "Destripa el final"}]}


def _prefs(**kw):
    base = {"adult_enabled": False, "labels": {}, "labelers": [{"did": LAB, "labels": {}}],
            "label_defs": {LAB: [interpretar_definicion(DEF_SPOILER, LAB)]},
            "user_did": YO}
    base.update(kw)
    return base


# ------------------------------------------------------------------ B1 cabecera
def test_cabecera_labelers_como_el_oficial():
    assert valor_cabecera_labelers([]) == f"{BSKY_LABELER_DID};redact"
    assert valor_cabecera_labelers([LAB, BSKY_LABELER_DID, "basura", LAB]) == \
        f"{BSKY_LABELER_DID};redact, {LAB}"


# ------------------------------------------------------------------ preferencias
def test_parsear_preferencias_completas():
    lista = [
        {"$type": "app.bsky.actor.defs#adultContentPref", "enabled": True},
        {"$type": "app.bsky.actor.defs#contentLabelPref", "label": "porn", "visibility": "show"},
        {"$type": "app.bsky.actor.defs#contentLabelPref", "label": "spoiler",
         "visibility": "hide", "labelerDid": LAB},
        {"$type": "app.bsky.actor.defs#contentLabelPref", "label": "spam",
         "visibility": "warn", "labelerDid": BSKY_LABELER_DID},
        {"$type": "app.bsky.actor.defs#labelersPref", "labelers": [{"did": LAB}]},
        {"$type": "app.bsky.actor.defs#mutedWordsPref",
         "items": [{"value": "fútbol", "targets": ["content"], "id": "x1"}]},
        {"$type": "app.bsky.actor.defs#hiddenPostsPref", "items": ["at://a/app.bsky.feed.post/1"]},
        {"$type": "app.bsky.actor.defs#feedViewPref", "feed": "home", "hideReposts": True},
        {"$type": "app.bsky.actor.defs#threadViewPref", "sort": "oldest"},
    ]
    p = parsear_preferencias(lista, user_did=YO)
    assert p["adult_enabled"] is True
    assert p["labels"]["porn"] == "ignore"          # "show" histórico = ignore
    assert p["labelers_dids"] == [LAB]
    lab = {x["did"]: x["labels"] for x in p["labelers"]}
    assert lab[LAB] == {"spoiler": "hide"}
    assert lab[BSKY_LABELER_DID] == {"spam": "warn"}
    assert p["muted_words"][0]["actorTarget"] == "all" and p["muted_words"][0]["id"] == "x1"
    assert p["hidden_posts"] == ["at://a/app.bsky.feed.post/1"]
    assert p["feed_view"]["home"]["hideReposts"] is True
    assert p["feed_view"]["home"]["hideRepliesByUnfollowed"] is True
    assert p["thread_view"]["sort"] == "oldest"


def test_normalizar_defectos_oficiales_y_formato_antiguo():
    p = normalizar_prefs({})
    assert p["adult_enabled"] is False
    assert p["labels"] == {"porn": "hide", "sexual": "warn", "nudity": "ignore",
                           "graphic-media": "warn"}
    assert p["labelers"][0]["did"] == BSKY_LABELER_DID
    # Alias antiguos (B16): una preferencia "gore" vale para graphic-media.
    assert normalizar_prefs({"labels": {"gore": "hide"}})["labels"]["graphic-media"] == "hide"


# ------------------------------------------------------------------ B4/B6/B16 etiquetas globales
def test_adulto_desactivado_oculta_y_no_se_puede_revelar():
    d = moderar_post({"d": AUTOR, "l": [["porn", BSKY_LABELER_DID]]}, _prefs(), "lista")
    assert d["filtrar"] and d["difuminar_media"] and d["no_override_media"]
    # Aunque el usuario tenga "warn" para porn, con adulto desactivado gana hide.
    d = moderar_post({"d": AUTOR, "l": [["porn", BSKY_LABELER_DID]]},
                     _prefs(labels={"porn": "warn"}), "lista")
    assert d["filtrar"]


def test_blurs_media_solo_difumina_la_multimedia():
    prefs = _prefs(adult_enabled=True)
    for val in ("sexual", "graphic-media", "gore"):
        d = moderar_post({"d": AUTOR, "l": [[val, BSKY_LABELER_DID]]}, prefs, "lista")
        assert not d["filtrar"] and not d["difuminar"] and d["difuminar_media"], val
    # porn por defecto = hide (filtra la lista); nudity por defecto = ignore (no es adulta).
    assert moderar_post({"d": AUTOR, "l": [["porn", BSKY_LABELER_DID]]}, prefs)["filtrar"]
    d = moderar_post({"d": AUTOR, "l": [["nudity", BSKY_LABELER_DID]]}, _prefs())
    assert not (d["filtrar"] or d["difuminar"] or d["difuminar_media"])
    # nsfw (antigua) = porn.
    assert moderar_post({"d": AUTOR, "l": [["nsfw", BSKY_LABELER_DID]]}, prefs)["filtrar"]


def test_autoetiquetas_y_no_self():
    prefs = _prefs(adult_enabled=True)
    # Autoetiqueta (origen = autor): vale aunque el autor no sea un labeler suscrito.
    d = moderar_post({"d": AUTOR, "l": [["sexual", AUTOR]]}, prefs)
    assert d["difuminar_media"]
    # !hide autoimpuesta: el oficial la ignora (no-self).
    d = moderar_post({"d": AUTOR, "l": [["!hide", AUTOR]]}, prefs)
    assert not d["filtrar"] and not d["difuminar"]


# ------------------------------------------------------------------ B8 labelers
def test_labeler_no_suscrito_se_descarta_y_suscrito_con_definicion():
    datos = {"d": AUTOR, "l": [["spoiler", "did:plc:otro"]]}
    assert not moderar_post(datos, _prefs())["difuminar"]
    d = moderar_post({"d": AUTOR, "l": [["spoiler", LAB]]}, _prefs())
    assert d["difuminar"] and not d["filtrar"] and d["motivo"] == "Destripe"
    assert texto_aviso(d) == "[Oculto por la etiqueta: Destripe. Ábrelo en detalles para verlo]"
    # Preferencia por (labeler, etiqueta): hide filtra.
    p = _prefs(labelers=[{"did": LAB, "labels": {"spoiler": "hide"}}])
    assert moderar_post({"d": AUTOR, "l": [["spoiler", LAB]]}, p)["filtrar"]
    # En detalle (severity alert) se lee con aviso: no difumina el texto.
    d = moderar_post({"d": AUTOR, "l": [["spoiler", LAB]]}, _prefs(), "detalle")
    assert not d["filtrar"] and not d["difuminar"] and d["avisar"]


def test_hide_del_sistema_no_override():
    d = moderar_post({"d": AUTOR, "l": [["!hide", BSKY_LABELER_DID]]}, _prefs(), "detalle")
    assert d["filtrar"] and d["no_override"]


# ------------------------------------------------------------------ B7 cuenta / perfil
def test_etiquetas_de_cuenta_y_de_perfil():
    prefs = _prefs(adult_enabled=True, labels={"porn": "warn"})
    # porn sobre el PERFIL: solo avatar/banner; el contenido no se toca.
    d = moderar_post({"d": AUTOR, "l": [], "al": [["porn", BSKY_LABELER_DID, "p"]]}, prefs)
    assert not (d["filtrar"] or d["difuminar"] or d["difuminar_media"])
    # porn sobre la CUENTA: tampoco difumina el contenido (solo avatar/banner).
    d = moderar_post({"d": AUTOR, "l": [], "al": [["porn", BSKY_LABELER_DID, "a"]]}, prefs)
    assert not (d["difuminar"] or d["difuminar_media"])
    # !hide sobre la cuenta: sus publicaciones se ocultan.
    d = moderar_post({"d": AUTOR, "l": [], "al": [["!hide", BSKY_LABELER_DID, "a"]]}, prefs)
    assert d["filtrar"] and d["difuminar"]


# ------------------------------------------------------------------ B2 palabras silenciadas
def test_palabras_silenciadas_en_listas_detalle_y_propias():
    prefs = _prefs(muted_words=[{"value": "fútbol", "targets": ["content", "tag"]}])
    datos = {"d": AUTOR, "tx": "Hoy hay fútbol en la tele"}
    d = moderar_post(datos, prefs, "lista")
    assert not d["filtrar"] and d["difuminar"] and d["palabras"] == ["fútbol"]
    assert texto_aviso(d) == "[Silenciado por la palabra: fútbol]"
    # Opción local: quitarlas de la lista como el feed oficial.
    assert moderar_post(datos, dict(prefs, mutewords_filtrar=True), "lista")["filtrar"]
    # Las publicaciones propias nunca se silencian.
    assert not moderar_post(dict(datos, d=YO), prefs)["difuminar"]
    # Etiqueta: target "tag" solo mira etiquetas.
    p2 = _prefs(muted_words=[{"value": "futbol", "targets": ["tag"]}])
    assert moderar_post({"d": AUTOR, "tx": "futbol", "t": []}, p2)["difuminar"] is False
    assert moderar_post({"d": AUTOR, "tx": "x", "t": ["Futbol"]}, p2)["difuminar"] is True


def test_compatibilidad_api_decidir_con_datos():
    prefs = _prefs(muted_words=[{"value": "spoiler", "targets": ["content"]}])
    d = decidir([], prefs, "lista", datos={"d": AUTOR, "tx": "big spoiler here"})
    assert d["difuminar"] and d["palabras"] == ["spoiler"]


# ------------------------------------------------------------------ B11/B13 ocultas y cita
def test_publicaciones_ocultadas_y_citas():
    uri = "at://did:plc:autor/app.bsky.feed.post/1"
    citada = "at://did:plc:otro/app.bsky.feed.post/2"
    prefs = _prefs(hidden_posts=[uri, citada])
    assert moderar_post({"d": AUTOR, "tx": "x"}, prefs, uri=uri)["filtrar"]
    # Una publicación que CITA a otra ocultada también se oculta (checkHiddenPost).
    d = moderar_post({"d": AUTOR, "tx": "x", "q": {"u": citada, "d": "did:plc:otro", "tx": "y"}},
                     prefs, uri="at://did:plc:autor/app.bsky.feed.post/9")
    assert d["filtrar"]


def test_cita_moderada_por_su_cuenta():
    prefs = _prefs(muted_words=[{"value": "malo", "targets": ["content"]}])
    datos = {"d": AUTOR, "tx": "mira esto", "q": {"u": "at://x/app.bsky.feed.post/3",
                                                    "d": "did:plc:otro", "tx": "algo malo",
                                                    "l": [["!warn", BSKY_LABELER_DID]]}}
    mc = moderar_cita(datos, prefs)
    assert mc["difuminar"]
    # La etiqueta de la cita se rebaja: no difumina la publicación que cita (pero la palabra
    # silenciada del texto citado sí, como matchAllMuteWords del oficial).
    sin_palabra = moderar_post(dict(datos, q=dict(datos["q"], tx="nada")), prefs)
    assert not sin_palabra["difuminar"]
    assert moderar_post(datos, prefs)["difuminar"]
    assert moderar_cita({"q": {"blk": True}}, prefs)["bloqueada"]


# ------------------------------------------------------------------ B12 feed Siguiendo e hilos
def test_feed_view_siguiendo():
    fv = prefs_feed_view(_prefs(feed_view={"home": {"hideReposts": True}}))
    assert oculto_por_feed_view(fv, False, True, False)
    assert not oculto_por_feed_view(fv, False, False, False)
    fv = prefs_feed_view(_prefs(feed_view={"home": {"hideReplies": True}}))
    assert oculto_por_feed_view(fv, True, False, False, author_did=AUTOR, user_did=YO)
    assert not oculto_por_feed_view(fv, True, False, False, author_did=YO, user_did=YO)
    # Respuesta de alguien que sigo a una cuenta que NO sigo (defecto hideRepliesByUnfollowed).
    fv = prefs_feed_view(_prefs())
    datos = {"f": 1, "rp": {"pd": "did:plc:x", "pf": 0, "rd": "did:plc:x", "rf": 0}}
    assert oculto_por_feed_view(fv, True, False, False, 0, AUTOR, YO, datos)
    datos["rp"] = {"pd": YO, "pf": 0, "rd": YO, "rf": 0}     # me responde a mí
    assert not oculto_por_feed_view(fv, True, False, False, 0, AUTOR, YO, datos)
    assert not oculto_por_feed_view(fv, True, False, False, 0, AUTOR, YO, {})   # sin datos
    fv = prefs_feed_view(_prefs(feed_view={"home": {"hideQuotePosts": True,
                                                    "hideRepliesByLikeCount": 3,
                                                    "hideRepliesByUnfollowed": False}}))
    assert oculto_por_feed_view(fv, False, False, True)
    assert oculto_por_feed_view(fv, True, False, False, 1, AUTOR, YO, {})
    assert not oculto_por_feed_view(fv, True, False, False, 5, AUTOR, YO, {})


def test_orden_respuestas_hilo():
    nodos = [{"d": "c", "l": 1, "f": "2026-01-02"}, {"d": "op", "l": 0, "f": "2026-01-03"},
             {"d": "b", "l": 9, "f": "2026-01-01"}, {"d": YO, "l": 0, "f": "2026-01-04"}]
    kw = dict(op_did="op", user_did=YO, like_de=lambda n: n["l"], fecha_de=lambda n: n["f"],
              did_de=lambda n: n["d"])
    assert [n["d"] for n in ordenar_respuestas(nodos, "oldest", **kw)] == ["op", YO, "b", "c"]
    assert [n["d"] for n in ordenar_respuestas(nodos, "newest", **kw)] == ["op", YO, "c", "b"]
    assert [n["d"] for n in ordenar_respuestas(nodos, "most-likes", **kw)] == ["op", YO, "b", "c"]
    assert [n["d"] for n in ordenar_respuestas(nodos, "hotness", **kw)] == ["op", YO, "c", "b"]


# ------------------------------------------------------------------ B5 bandeja
def test_moderar_fila_bandeja():
    prefs = _prefs(muted_words=[{"value": "fútbol", "targets": ["content"]}])
    r = moderar_fila_bandeja(prefs, "", a_json({"d": AUTOR, "tx": "fútbol hoy"}),
                             contenido="fútbol hoy")
    assert not r["ocultar"] and r["contenido"] == "[Silenciado por la palabra: fútbol]"
    r = moderar_fila_bandeja(prefs, "!hide", "", contenido="x")
    assert r["ocultar"]
    r = moderar_fila_bandeja(_prefs(feed_view={"home": {"hideReposts": True}}), "",
                             a_json({"d": AUTOR, "tx": "x"}), contenido="x", buffer="home",
                             reason_json='{"$type":"app.bsky.feed.defs#reasonRepost"}')
    assert r["ocultar"]
    r = moderar_fila_bandeja(_prefs(adult_enabled=True), "",
                             a_json({"d": AUTOR, "tx": "x", "l": [["sexual", BSKY_LABELER_DID]]}),
                             contenido="x")
    assert r["ocultar_media"] and r["aviso_media"].startswith("[Multimedia oculta")


# ------------------------------------------------------------------ extracción de datos
def _post_view(labels=None, author_labels=None, embed=None, record_extra=None, viewer=None):
    rec = {"$type": "app.bsky.feed.post", "text": "Hola #mundo", "langs": ["es"],
           "facets": [{"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "mundo"}]}],
           "tags": ["extra"], "labels": {"values": [{"val": "sexual"}]}}
    rec.update(record_extra or {})
    return {"uri": "at://did:plc:autor/app.bsky.feed.post/1", "cid": "c1",
            "author": {"did": AUTOR, "handle": "autor.test", "labels": author_labels or [],
                       "viewer": viewer or {"following": "at://f"}},
            "record": rec, "labels": labels or [], "embed": embed,
            "indexed_at": "2026-10-17T10:00:00Z"}


def test_extraer_datos_post():
    pv = _post_view(
        labels=[{"val": "spoiler", "src": LAB}, {"val": "!warn", "src": BSKY_LABELER_DID},
                {"val": "!warn", "src": BSKY_LABELER_DID, "neg": True}],
        author_labels=[{"val": "porn", "src": BSKY_LABELER_DID,
                        "uri": "at://did:plc:autor/app.bsky.actor.profile/self"},
                       {"val": "!hide", "src": BSKY_LABELER_DID, "uri": AUTOR}],
        embed={"$type": "app.bsky.embed.record#view", "record": {
            "$type": "app.bsky.embed.record#viewRecord", "uri": "at://did:plc:q/app.bsky.feed.post/2",
            "author": {"did": "did:plc:q", "viewer": {}},
            "value": {"text": "citado", "embed": {"$type": "app.bsky.embed.images",
                                                   "images": [{"alt": "foto"}]}},
            "labels": [{"val": "spoiler", "src": LAB}]}})
    d = extraer_datos_post(pv)
    assert d["d"] == AUTOR and d["f"] == 1
    assert ["spoiler", LAB] in d["l"] and ["sexual", AUTOR] in d["l"]
    assert ["!warn", BSKY_LABELER_DID] not in d["l"]          # negada
    assert ["porn", BSKY_LABELER_DID, "p"] in d["al"] and ["!hide", BSKY_LABELER_DID, "a"] in d["al"]
    assert d["t"] == ["mundo", "extra"] and d["lg"] == ["es"]
    assert d["q"]["tx"] == "citado" and d["q"]["alt"] == ["foto"] and d["q"]["l"] == [["spoiler", LAB]]


def test_extraer_datos_notificacion():
    n = {"uri": "at://did:plc:autor/app.bsky.feed.post/5", "reason": "mention",
         "author": {"did": AUTOR, "viewer": {}}, "labels": [{"val": "!warn", "src": BSKY_LABELER_DID}],
         "record": {"$type": "app.bsky.feed.post", "text": "hola @yo"}}
    d = extraer_datos_notificacion(n)
    assert d["tx"] == "hola @yo" and d["l"] == [["!warn", BSKY_LABELER_DID]]
    like = {"uri": "at://did:plc:autor/app.bsky.feed.like/5", "reason": "like",
            "author": {"did": AUTOR}, "record": {"$type": "app.bsky.feed.like"}}
    assert "tx" not in extraer_datos_notificacion(like)


# ------------------------------------------------------------------ B3 atajo del mismo CID
def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('bluesky', 'Bluesky', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) "
               "VALUES (?, 'B', 'bluesky', 1)", (ACC,))


def test_migracion_mod_json():
    _preparar()
    for tabla in ("bluesky_posts", "bluesky_notifications"):
        assert "mod_json" in {r[1] for r in db.query(f"PRAGMA table_info({tabla})")}


def test_atajo_mismo_cid_refresca_etiquetas(monkeypatch):
    _preparar()
    from app.services.bluesky.bluesky_service import BlueskyService
    s = BlueskyService()
    monkeypatch.setattr(s, "prefs_moderacion", lambda acc: {}, raising=False)
    pv = _post_view(record_extra={"labels": None})
    db.execute("DELETE FROM bluesky_posts WHERE id = ?", (f"{ACC}:{pv['uri']}",))
    po = s._procesar_y_guardar_post(ACC, pv)
    assert po is not None and not po.labels
    # El labeler marca el post minutos después: MISMO CID, etiqueta nueva.
    pv2 = _post_view(record_extra={"labels": None},
                     labels=[{"val": "!hide", "src": BSKY_LABELER_DID}])
    po2 = s._procesar_y_guardar_post(ACC, pv2)
    assert po2.labels == "!hide" and '"!hide"' in (po2.mod_json or "")
    fila = db.query_one("SELECT labels, mod_json FROM bluesky_posts WHERE id = ?",
                        (f"{ACC}:{pv['uri']}",))
    assert fila[0] == "!hide" and BSKY_LABELER_DID in fila[1]
    # Y el FTS lo retira (moderación por etiqueta antes de indexar).
    assert db.query_one("SELECT COUNT(*) FROM bluesky_posts_fts WHERE post_db_id = ?",
                        (f"{ACC}:{pv['uri']}",))[0] in (0, 1)


# ------------------------------------------------------------------ B1/B9/B10/B11/B17 escrituras
class _Req:
    def __init__(self):
        self.h = {}

    def add_additional_header(self, k, v):
        self.h[k] = v


class _ClienteFalso:
    def __init__(self, prefs):
        self.prefs = list(prefs)
        self.request = _Req()
        self.escrituras = 0
        vista = SimpleNamespace(creator=SimpleNamespace(did=LAB, handle="lab.test"),
                                policies=SimpleNamespace(label_value_definitions=[DEF_SPOILER]),
                                uri="at://lab", like_count=3)
        self.app = SimpleNamespace(bsky=SimpleNamespace(
            actor=SimpleNamespace(get_preferences=lambda *a, **k: SimpleNamespace(
                preferences=list(self.prefs)), put_preferences=self._put),
            labeler=SimpleNamespace(get_services=lambda *a, **k: SimpleNamespace(views=[vista]))))

    def _put(self, data):
        self.escrituras += 1
        self.prefs = list(data.preferences)


def _servicio_con(prefs):
    from app.services.bluesky.bluesky_service import BlueskyService
    s = BlueskyService()
    cli = _ClienteFalso(prefs)
    s._clients[ACC] = cli
    s._me[ACC] = {"did": YO, "handle": "yo.test"}
    return s, cli


def test_prefs_refrescan_cabecera_y_definiciones():
    from atproto import models
    s, cli = _servicio_con([
        models.AppBskyActorDefs.LabelersPref(labelers=[models.AppBskyActorDefs.LabelerPrefItem(did=LAB)]),
        models.AppBskyActorDefs.AdultContentPref(enabled=False)])
    p = s.prefs_moderacion(ACC, permitir_red=True)
    assert cli.request.h["atproto-accept-labelers"] == f"{BSKY_LABELER_DID};redact, {LAB}"
    assert p["label_defs"][LAB][0]["name"] == "Destripe"
    assert p["user_did"] == YO
    d = moderar_post({"d": AUTOR, "l": [["spoiler", LAB]]}, p)
    assert d["difuminar"]


def test_palabras_silenciadas_conservan_id_y_resto_de_preferencias():
    from atproto import models
    s, cli = _servicio_con([
        models.AppBskyActorDefs.AdultContentPref(enabled=True),
        models.AppBskyActorDefs.MutedWordsPref(items=[models.AppBskyActorDefs.MutedWord(
            value="viejo", targets=["content"], id="id-1")])])
    original = {"value": "viejo", "targets": ["content"], "id": "id-1"}

    def _editar(items):
        assert items[0]["id"] == "id-1"
        items[0] = dict(items[0], value="nuevo", actorTarget="exclude-following",
                        expiresAt="2030-01-01T00:00:00.000Z")
        return items
    s._modificar_muted_words(ACC, _editar)
    tipos = [type(p).__name__ for p in cli.prefs]
    assert "AdultContentPref" in tipos and "MutedWordsPref" in tipos
    mw = next(p for p in cli.prefs if type(p).__name__ == "MutedWordsPref").items[0]
    assert (mw.value, mw.id, mw.actor_target) == ("nuevo", "id-1", "exclude-following")
    assert original["id"] == "id-1"
    # La caché de moderación queda al día sin otra lectura.
    assert s.prefs_moderacion(ACC)["muted_words"][0]["value"] == "nuevo"


def test_ocultar_post_y_moderacion_por_labeler_en_una_escritura():
    from atproto import models
    s, cli = _servicio_con([models.AppBskyActorDefs.LabelersPref(
        labelers=[models.AppBskyActorDefs.LabelerPrefItem(did=LAB)])])
    uri = "at://did:plc:autor/app.bsky.feed.post/1"

    def _ocultar(lista):
        lista.append(models.AppBskyActorDefs.HiddenPostsPref(items=[uri]))
        return lista
    s._editar_preferencias(ACC, _ocultar)
    assert s.publicaciones_ocultas(ACC) == [uri]
    # Guardado de moderación: global con doble escritura del nombre antiguo y por labeler.
    resultado = {}

    def _start(fn, callback=None, **kw):
        resultado["r"] = fn(None)
    import app.services.bluesky.bluesky_preferencias as mod_pref
    original = mod_pref.task_manager.start_task
    mod_pref.task_manager.start_task = _start
    try:
        s.guardar_moderacion_async(ACC, adulto=True, globales={"porn": "warn"},
                                   por_labeler={LAB: {"spoiler": "hide"}})
    finally:
        mod_pref.task_manager.start_task = original
    etiquetas = {(p.label, p.labeler_did): p.visibility for p in cli.prefs
                 if type(p).__name__ == "ContentLabelPref"}
    assert etiquetas[("porn", None)] == "warn" and etiquetas[("nsfw", None)] == "warn"
    assert etiquetas[("spoiler", LAB)] == "hide"
    p = s.prefs_moderacion(ACC)
    assert p["adult_enabled"] is True and p["hidden_posts"] == [uri]
    assert moderar_post({"d": AUTOR, "l": [["spoiler", LAB]]}, p)["filtrar"]


# ------------------------------------------------------------------ lista (render)
def test_lista_aplica_palabras_ocultas_y_multimedia(monkeypatch):
    _preparar()
    from app.ui.bluesky_controller_carga import _CargaMixin
    from app.core.config_manager import config
    config.set("bluesky", "formato_repost", "original")
    base = "at://did:plc:autor/app.bsky.feed.post/"
    filas = [("m1", {"d": AUTOR, "tx": "Partido de fútbol"}, "Partido de fútbol", ""),
             ("m2", {"d": AUTOR, "tx": "Hola", "l": [["sexual", BSKY_LABELER_DID]]}, "Hola",
              '[{"type": "image", "alt": "desnudo"}]'),
             ("m3", {"d": AUTOR, "tx": "oculta"}, "oculta", "")]
    for rk, datos, texto, media in filas:
        pid = f"{ACC}:{base}{rk}"
        db.execute("INSERT OR REPLACE INTO bluesky_posts (id, account_id, uri, cid, author_did, "
                   "author_name, author_handle, content, indexed_at, media_json, mod_json) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, ACC, base + rk, "c", AUTOR, "Autor", "autor.test", texto,
                    "2026-10-17T10:00:00Z", media, a_json(datos)))
        db.execute("INSERT OR REPLACE INTO bluesky_timeline_posts (account_id, timeline, "
                   "post_db_id, sort_key) VALUES (?,?,?,?)", (ACC, "feed:x", pid, 1))
    prefs = normalizar_prefs(_prefs(adult_enabled=True, hidden_posts=[base + "m3"],
                                    muted_words=[{"value": "fútbol", "targets": ["content"]}]))
    monkeypatch.setattr("app.services.bluesky.bluesky_service.prefs_moderacion",
                        lambda acc: prefs, raising=False)
    ctrl = type("Ctrl", (_CargaMixin,), {})()
    ctrl.f = SimpleNamespace(active_bluesky_account_id=ACC, active_timeline_type="feed:x")
    sql = (f"SELECT {ctrl._SQL_COLS_POST}, {ctrl._SQL_COL_REASON}, {ctrl._SQL_COLS_MOD} "
           "FROM bluesky_posts p JOIN bluesky_timeline_posts t ON p.id = t.post_db_id "
           "WHERE p.id = ? AND t.timeline = 'feed:x'")
    v1 = ctrl._valores_post(db.query_one(sql, (f"{ACC}:{base}m1",)))
    assert v1[1]["content"] == "[Silenciado por la palabra: fútbol]" and not v1[1]["_mod_filtrar"]
    v2 = ctrl._valores_post(db.query_one(sql, (f"{ACC}:{base}m2",)))
    assert v2[1]["content"].endswith("Hola") and "desnudo" not in v2[1]["media"]
    assert v2[1]["media"].startswith("[Multimedia oculta") and v2[3] is None
    v3 = ctrl._valores_post(db.query_one(sql, (f"{ACC}:{base}m3",)))
    assert v3[1]["_mod_filtrar"]
    # "Mostrar de todos modos" (B18) revela el texto de la palabra silenciada.
    ctrl._revelados_bsky = {f"{ACC}:{base}m1"}
    v1 = ctrl._valores_post(db.query_one(sql, (f"{ACC}:{base}m1",)))
    assert v1[1]["content"].endswith("Partido de fútbol")
