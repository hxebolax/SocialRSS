# -*- coding: utf-8 -*-
"""Pruebas de la Fase 4 de Bluesky (auditoría 2026-07-11): facets robustos (6.4),
outbox con rkey idempotente (6.6) y clasificación de límites 429 (9.5).
Sin red: parser puro, SQLite aislado y excepciones fabricadas."""

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.bluesky.bluesky_facets import detectar_facets, primera_url

ACC = 991


def _preparar():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'bluesky', 'prueba-bsky', 1, 98)", (ACC,))


# ---------------- Facets (6.4): los casos de prueba de la auditoría ----------------

def _de_tipo(facets, tipo):
    return [f for f in facets if f["tipo"] == tipo]


def test_facets_url_no_absorbe_puntuacion():
    f = detectar_facets("Mira https://ejemplo.com/pagina. Y también https://otro.net/x, vale?")
    urls = [x["valor"] for x in _de_tipo(f, "link")]
    assert urls == ["https://ejemplo.com/pagina", "https://otro.net/x"]


def test_facets_parentesis_balanceados():
    # El ')' que CIERRA un '(' del propio enlace se conserva; el de la frase, no.
    f = detectar_facets("(ver https://es.wikipedia.org/wiki/Perl_(lenguaje)) y (ver https://ejemplo.com)")
    urls = [x["valor"] for x in _de_tipo(f, "link")]
    assert urls[0] == "https://es.wikipedia.org/wiki/Perl_(lenguaje)"
    assert urls[1] == "https://ejemplo.com"


def test_facets_correo_no_es_mencion():
    f = detectar_facets("Escríbeme a persona@dominio.com si quieres.")
    assert not _de_tipo(f, "mention"), "un correo no debe generar mención"


def test_facets_arroba_dentro_de_url_no_solapa():
    # URL que contiene @handle: la URL gana y no hay facet de mención solapado.
    f = detectar_facets("Perfil: https://bsky.app/profile/@usuaria.bsky.social ahí")
    assert len(_de_tipo(f, "link")) == 1
    assert not _de_tipo(f, "mention")
    # Y nunca hay solapes entre los aceptados.
    rangos = sorted((x["byte_start"], x["byte_end"]) for x in f)
    for a, b in zip(rangos, rangos[1:]):
        assert a[1] <= b[0], "facets solapados"


def test_facets_mencion_valida_y_offsets_utf8():
    # Emoji multibyte ANTES de la mención y del enlace: los offsets van en bytes UTF-8.
    texto = "🎉🎉 hola @usuaria.bsky.social mira https://ejemplo.com"
    f = detectar_facets(texto)
    m = _de_tipo(f, "mention")[0]
    datos = texto.encode("utf-8")
    assert datos[m["byte_start"]:m["byte_end"]].decode("utf-8") == "@usuaria.bsky.social"
    u = _de_tipo(f, "link")[0]
    assert datos[u["byte_start"]:u["byte_end"]].decode("utf-8") == "https://ejemplo.com"


def test_facets_dominio_desnudo_y_abreviaturas():
    f = detectar_facets("Entra en bsky.app/settings, p.ej. hoy mismo.")
    urls = [x["valor"] for x in _de_tipo(f, "link")]
    assert urls == ["https://bsky.app/settings"], "el dominio desnudo se enlaza; 'p.ej.' no"


def test_facets_hashtags_y_adyacentes():
    f = detectar_facets("#accesibilidad y #NVDA2026, cierre. @a.bsky.social #si")
    tags = [x["valor"] for x in _de_tipo(f, "tag")]
    assert tags == ["accesibilidad", "NVDA2026", "si"]
    assert len(_de_tipo(f, "mention")) == 1


def test_primera_url_para_tarjeta():
    assert primera_url("nada por aquí") is None
    assert primera_url("mira https://ejemplo.com/a.") == "https://ejemplo.com/a"


# ---------------- Outbox (6.6) y límites (9.5) ----------------

def test_outbox_tabla_y_estados():
    """La tabla existe (migración v9) y el ciclo de estados del apunte funciona."""
    _preparar()
    assert db.query_one("PRAGMA user_version")[0] >= 9
    from app.services.bluesky import bluesky_service
    db.execute("DELETE FROM bluesky_outbox WHERE account_id = ?", (ACC,))
    oid = db.execute_insert(
        "INSERT INTO bluesky_outbox (account_id, rkey, payload_json, estado, intentos, created_at) "
        "VALUES (?, 'abc123def4567', '{\"texto\": \"hola\"}', 'pending', 1, '2026-07-11T10:00:00')",
        (ACC,))
    pendientes = bluesky_service.listar_outbox(ACC)
    assert pendientes and pendientes[0]["id"] == oid
    assert pendientes[0]["payload"]["texto"] == "hola"
    bluesky_service._outbox_actualizar(oid, "sent", uri="at://did:plc:x/app.bsky.feed.post/abc123def4567")
    assert bluesky_service.listar_outbox(ACC) == [], "los enviados no se listan"
    fila = db.query_one("SELECT estado, uri FROM bluesky_outbox WHERE id = ?", (oid,))
    assert fila[0] == "sent" and fila[1].endswith("abc123def4567")
    bluesky_service.borrar_outbox(oid)
    assert db.query_one("SELECT 1 FROM bluesky_outbox WHERE id = ?", (oid,)) is None


class _RespuestaFalsa:
    def __init__(self, status_code=None, headers=None, error=None):
        self.status_code = status_code
        self.headers = headers or {}
        if error is not None:
            class _C:
                pass
            self.content = _C()
            self.content.error = error


def _exc(status=None, headers=None, error=None, texto="boom"):
    e = Exception(texto)
    e.response = _RespuestaFalsa(status, headers, error)
    return e


def test_clasificacion_de_limites_y_ambiguos():
    from app.services.bluesky import bluesky_service as s
    # Límite por código XRPC y por status HTTP; nunca por el str().
    assert s._es_error_limite(_exc(error="RateLimitExceeded")) is True
    assert s._es_error_limite(_exc(status=429)) is True
    assert s._es_error_limite(_exc(error="InvalidRequest", texto="ratelimit en el texto")) is False
    # retry-after y ratelimit-reset se respetan; sin cabeceras, 5 minutos.
    assert s._espera_de_limite(_exc(headers={"retry-after": "42"})) == 42.0
    assert s._espera_de_limite(_exc()) == 300.0
    # Ambiguo: timeout/5xx SIN código XRPC. Un error con código claro no es ambiguo.
    assert s._es_error_ambiguo(_exc(texto="read timeout")) is True
    assert s._es_error_ambiguo(_exc(status=503)) is True
    assert s._es_error_ambiguo(_exc(error="InvalidRequest", texto="connection reset")) is False


def test_cooldown_429_bloquea_y_expira():
    import time
    from app.core.errors import BlueskyServiceError
    from app.services.bluesky import bluesky_service as s
    cuenta = 99991
    s.registrar_limite_429(cuenta, _exc(headers={"retry-after": "60"}))
    try:
        s._comprobar_cooldown(cuenta)
        assert False, "debía fallar rápido durante el cooldown"
    except BlueskyServiceError as e:
        assert "reintentar" in str(e).lower()
    # Al expirar, se limpia y deja pasar.
    s._cooldown_429[cuenta] = time.time() - 1
    s._comprobar_cooldown(cuenta)
    assert cuenta not in s._cooldown_429


# ---------------- Fase 5: Jetstream (12.2) ----------------

def _evento_jetstream(did="did:plc:autor", texto="hola", facets=None, reply=None,
                      operation="create", collection="app.bsky.feed.post", time_us=1000):
    record = {"text": texto}
    if facets:
        record["facets"] = facets
    if reply:
        record["reply"] = reply
    return {"did": did, "time_us": time_us, "kind": "commit",
            "commit": {"operation": operation, "collection": collection,
                       "rkey": "abc123def4567", "record": record}}


def test_jetstream_ignora_lo_que_no_es_post_nuevo():
    from app.services.bluesky.bluesky_jetstream import analizar_evento
    assert analizar_evento({"kind": "identity"}) is None
    assert analizar_evento(_evento_jetstream(operation="delete")) is None
    assert analizar_evento(_evento_jetstream(collection="app.bsky.feed.like")) is None


def test_jetstream_mencion_por_facets_no_por_texto():
    """La mención se decide por el facet #mention con MI did (12.1: buscar el DID en
    str(record) daba falsos positivos)."""
    from app.services.bluesky.bluesky_jetstream import analizar_evento
    yo = "did:plc:yo"
    # El DID aparece en el TEXTO pero sin facet: NO es mención.
    r = analizar_evento(_evento_jetstream(texto=f"hablando de {yo} sin mencionar"), mi_did=yo)
    assert r and r["is_mention"] is False
    # Facet de mención real: SÍ.
    facets = [{"features": [{"$type": "app.bsky.richtext.facet#mention", "did": yo}]}]
    r = analizar_evento(_evento_jetstream(texto="hola @yo", facets=facets), mi_did=yo)
    assert r and r["is_mention"] is True


def test_jetstream_reply_solo_si_es_a_mi_repo():
    from app.services.bluesky.bluesky_jetstream import analizar_evento
    yo = "did:plc:yo"
    ajena = {"parent": {"uri": "at://did:plc:otra/app.bsky.feed.post/x"},
             "root": {"uri": "at://did:plc:otra/app.bsky.feed.post/x"}}
    r = analizar_evento(_evento_jetstream(reply=ajena), mi_did=yo)
    assert r and r["is_reply"] is False, "una respuesta a otra persona no es para mí"
    mia = {"parent": {"uri": f"at://{yo}/app.bsky.feed.post/x"},
           "root": {"uri": "at://did:plc:otra/app.bsky.feed.post/x"}}
    r = analizar_evento(_evento_jetstream(reply=mia), mi_did=yo)
    assert r and r["is_reply"] is True


def test_jetstream_seguidos_palabras_y_uri():
    from app.services.bluesky.bluesky_jetstream import analizar_evento
    r = analizar_evento(_evento_jetstream(did="did:plc:amiga", texto="Probando NVDA hoy"),
                        palabras=["nvda"], dids_seguidos={"did:plc:amiga"})
    assert r["de_seguido"] is True and r["hay_palabra"] is True
    assert r["uri"] == "at://did:plc:amiga/app.bsky.feed.post/abc123def4567"
    assert r["time_us"] == 1000


# ---------------- Fase 6: moderación por contexto (8.1) ----------------

def test_moderacion_sistema_hide_no_override_en_todo_contexto():
    from app.services.bluesky.bluesky_moderacion import decidir
    for ctx in ("lista", "busqueda", "detalle"):
        d = decidir(["!hide"], {}, contexto=ctx)
        assert d["filtrar"] is True and d["no_override"] is True, ctx


def test_moderacion_warn_difumina_pero_se_puede_abrir():
    from app.services.bluesky.bluesky_moderacion import decidir
    d = decidir(["!warn"], {}, contexto="lista")
    assert d["difuminar"] is True and d["filtrar"] is False and d["no_override"] is False
    d = decidir(["!warn"], {}, contexto="detalle")
    assert d["filtrar"] is False, "en detalle se revela"


def test_moderacion_preferencias_del_usuario():
    from app.services.bluesky.bluesky_moderacion import decidir
    prefs = {"adult_enabled": True, "labels": {"porn": "hide", "spam": "warn", "nudity": "ignore"}}
    # hide: filtra en lista y búsqueda, pero en DETALLE se puede abrir (no es !hide).
    assert decidir(["porn"], prefs, "lista")["filtrar"] is True
    assert decidir(["porn"], prefs, "busqueda")["filtrar"] is True
    d = decidir(["porn"], prefs, "detalle")
    # porn es "blurs: media" (oficial): en detalle el texto se lee y se oculta la multimedia.
    assert d["filtrar"] is False and d["difuminar"] is False and d["difuminar_media"] is True
    # warn: difumina sin filtrar.
    d = decidir(["spam"], prefs, "lista")
    assert d["difuminar"] is True and d["filtrar"] is False
    # ignore: el oficial no hace nada (la etiqueta sigue en la columna de aviso).
    d = decidir(["nudity"], prefs, "lista")
    assert not d["avisar"] and not d["difuminar"] and not d["difuminar_media"]


def test_moderacion_adultas_sin_preferencia_y_adult_off():
    from app.services.bluesky.bluesky_moderacion import decidir
    # Sin preferencias: como el oficial, contenido adulto DESACTIVADO por defecto -> se oculta
    # y no se puede revelar.
    d = decidir(["sexual"], {}, "lista")
    assert d["filtrar"] is True and d["no_override_media"] is True
    # Contenido adulto activado: sexual (defecto warn) solo difumina la multimedia.
    d = decidir(["sexual"], {"adult_enabled": True}, "lista")
    assert d["filtrar"] is False and d["difuminar"] is False and d["difuminar_media"] is True
    # Contenido adulto DESACTIVADO en la cuenta: se oculta (como la app oficial).
    d = decidir(["sexual"], {"adult_enabled": False}, "lista")
    assert d["filtrar"] is True


def test_moderacion_etiquetas_desconocidas_y_visibles():
    from app.services.bluesky.bluesky_moderacion import decidir, etiquetas_visibles
    # Etiqueta desconocida sin preferencia: no bloquea nada.
    d = decidir(["etiqueta-rara"], {}, "lista")
    assert not d["filtrar"] and not d["difuminar"]
    # !no-unauthenticated no afecta a un cliente autenticado.
    d = decidir(["!no-unauthenticated"], {}, "lista")
    assert not d["filtrar"] and not d["difuminar"]
    # Las de sistema no salen en la columna de aviso.
    assert etiquetas_visibles(["porn", "!hide", "spam"]) == ["porn", "spam"]
