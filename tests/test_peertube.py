# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) del cliente de PeerTube — Fase 0 (infraestructura).

Cubren el cliente HTTP (`ClienteHTTP`) con `httpx.MockTransport` (reintentos ante 5xx, respeto de
Retry-After en 429, sin reintento en 4xx, paginación start/count, cancelación, inyección del Bearer y
mensajes de error sin filtrar el token), la migración v17 (tablas + registro del servicio), la
sección de configuración, la bandera de servicio y el parseo tolerante de los modelos Pydantic. La
red se simula con MockTransport; la base de datos usa el directorio temporal aislado por conftest.py.
"""
import threading

import httpx
import pytest

from app.core.migrations import ejecutar_migraciones
from app.core.database import db
from app.core.errors import PeerTubeServiceError
from app.services.peertube.peertube_http import ClienteHTTP, PeerTubeCancelado
from app.services.peertube import peertube_modelos as M


@pytest.fixture(scope="module", autouse=True)
def _preparar_bd():
    ejecutar_migraciones()


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    """Acelera los tests: la espera de backoff/Retry-After no duerme de verdad, pero sigue
    comprobando la cancelación (se conserva la semántica)."""
    import app.services.peertube.peertube_http as http_mod
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)


def _cliente(handler, **kw):
    """Crea un ClienteHTTP cuyo httpx.Client usa un MockTransport con el `handler` dado."""
    c = ClienteHTTP("https://test.instance", **kw)
    c._client = httpx.Client(transport=httpx.MockTransport(handler),
                             base_url="https://test.instance",
                             headers={"User-Agent": "test"})
    return c


# --------------------------------------------------------------------------- HTTP básico
def test_get_ok_devuelve_json():
    def handler(req):
        assert req.url.path == "/api/v1/videos"
        return httpx.Response(200, json={"total": 0, "data": []})
    c = _cliente(handler)
    assert c.get("videos") == {"total": 0, "data": []}


def test_path_absoluto_se_respeta():
    visto = {}

    def handler(req):
        visto["path"] = req.url.path
        return httpx.Response(200, json={})
    c = _cliente(handler)
    c.get("/api/v1/config")
    assert visto["path"] == "/api/v1/config"


def test_bearer_se_inyecta_desde_el_proveedor():
    visto = {}

    def handler(req):
        visto["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={})
    c = _cliente(handler, token_provider=lambda: "TOKENSECRETO")
    c.get("users/me")
    assert visto["auth"] == "Bearer TOKENSECRETO"


def test_sin_token_no_hay_authorization():
    visto = {}

    def handler(req):
        visto["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={})
    c = _cliente(handler)  # sin token_provider
    c.get("videos", auth=True)
    assert visto["auth"] is None


# --------------------------------------------------------------------------- reintentos
def test_reintenta_en_5xx_y_acaba_ok():
    llamadas = {"n": 0}

    def handler(req):
        llamadas["n"] += 1
        if llamadas["n"] < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"ok": True})
    c = _cliente(handler, reintentos=3)
    assert c.get("videos") == {"ok": True}
    assert llamadas["n"] == 3  # 2 fallos + 1 acierto


def test_no_reintenta_en_404_y_lanza():
    llamadas = {"n": 0}

    def handler(req):
        llamadas["n"] += 1
        return httpx.Response(404, json={"error": "no existe"})
    c = _cliente(handler, reintentos=3)
    with pytest.raises(PeerTubeServiceError) as exc:
        c.get("videos/xxx")
    assert llamadas["n"] == 1                      # 4xx no se reintenta
    assert "no existe" in str(exc.value)           # incluye el detalle del servidor


def test_429_respeta_retry_after_y_reintenta():
    llamadas = {"n": 0}
    esperas = []
    orig = ClienteHTTP._retry_after

    def spy(self, resp, intento):
        val = orig(self, resp, intento)
        esperas.append(val)
        return val

    def handler(req):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={"ok": 1})
    c = _cliente(handler, reintentos=2)
    ClienteHTTP._retry_after = spy
    try:
        assert c.get("videos") == {"ok": 1}
    finally:
        ClienteHTTP._retry_after = orig
    assert llamadas["n"] == 2
    assert esperas and esperas[0] == 7.0           # usó el valor de Retry-After


def test_5xx_agotado_lanza_error_legible():
    def handler(req):
        return httpx.Response(503, json={})
    c = _cliente(handler, reintentos=1)
    with pytest.raises(PeerTubeServiceError) as exc:
        c.get("videos")
    assert "error interno" in str(exc.value).lower() or "más tarde" in str(exc.value).lower()


def test_respuesta_no_json_lanza():
    def handler(req):
        return httpx.Response(200, text="<html>proxy</html>",
                              headers={"content-type": "text/html"})
    c = _cliente(handler)
    with pytest.raises(PeerTubeServiceError):
        c.get("videos")


def test_204_devuelve_none():
    def handler(req):
        return httpx.Response(204)
    c = _cliente(handler)
    assert c.delete("videos/1") is None


# --------------------------------------------------------------------------- paginación
def test_paginar_recorre_todas_las_paginas():
    total = 5

    def handler(req):
        start = int(req.url.params.get("start", 0))
        count = int(req.url.params.get("count", 25))
        datos = list(range(start, min(start + count, total)))
        return httpx.Response(200, json={"total": total, "data": datos})
    c = _cliente(handler)
    assert list(c.paginar("videos", count=2)) == [0, 1, 2, 3, 4]


def test_paginar_respeta_limite():
    def handler(req):
        start = int(req.url.params.get("start", 0))
        count = int(req.url.params.get("count", 25))
        datos = list(range(start, start + count))   # "infinito"
        return httpx.Response(200, json={"total": 1000, "data": datos})
    c = _cliente(handler)
    assert list(c.paginar("videos", count=10, limite=15)) == list(range(15))


def test_pagina_devuelve_data_y_total():
    def handler(req):
        return httpx.Response(200, json={"total": 42, "data": [{"id": 1}, {"id": 2}]})
    c = _cliente(handler)
    data, total = c.pagina("videos", count=2)
    assert total == 42 and len(data) == 2


# --------------------------------------------------------------------------- cancelación
def test_cancelacion_aborta_antes_de_pedir():
    def handler(req):  # no debería llegar a llamarse
        raise AssertionError("no debería pedir si está cancelado")
    c = _cliente(handler)
    ev = threading.Event()
    ev.set()
    with pytest.raises(PeerTubeCancelado):
        c.get("videos", cancelar=ev)


def test_cancelacion_durante_la_espera_de_reintento():
    llamadas = {"n": 0}
    ev = threading.Event()

    def handler(req):
        llamadas["n"] += 1
        ev.set()                                   # tras el 1er fallo se cancela
        return httpx.Response(500, json={})
    c = _cliente(handler, reintentos=3)
    with pytest.raises(PeerTubeCancelado):
        c.get("videos", cancelar=ev)
    assert llamadas["n"] == 1                       # no reintentó tras cancelar


# --------------------------------------------------------------------------- migración v17
_TABLAS_PEERTUBE = [
    "peertube_instancias", "peertube_cuenta_meta", "peertube_videos", "peertube_video_buffers",
    "peertube_canales", "peertube_cuentas", "peertube_listas", "peertube_lista_items",
    "peertube_comentarios", "peertube_notificaciones", "peertube_historial",
    "peertube_busquedas_guardadas", "peertube_cache",
]


@pytest.mark.parametrize("tabla", _TABLAS_PEERTUBE)
def test_migracion_v17_crea_tablas(tabla):
    fila = db.query_one("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (tabla,))
    assert fila and fila[0] == tabla


def test_migracion_v17_registra_servicio():
    fila = db.query_one("SELECT id, name FROM services WHERE id = 'peertube'")
    assert fila and fila[0] == "peertube"


def test_migracion_cascade_borra_contenido_al_borrar_cuenta():
    acc = db.execute_insert(
        "INSERT INTO accounts (name, service_id, active, position) VALUES (?, 'peertube', 1, 0)",
        ("pt@instancia",))
    db.execute("INSERT INTO peertube_videos (id, account_id, uuid, name) VALUES (?,?,?,?)",
               (f"{acc}:uuid1", acc, "uuid1", "Vídeo"))
    db.execute("INSERT INTO peertube_video_buffers (account_id, buffer, video_db_id) VALUES (?,?,?)",
               (acc, "recientes", f"{acc}:uuid1"))
    assert db.query_one("SELECT COUNT(*) FROM peertube_videos WHERE account_id=?", (acc,))[0] == 1
    db.execute("DELETE FROM accounts WHERE id=?", (acc,))
    assert db.query_one("SELECT COUNT(*) FROM peertube_videos WHERE account_id=?", (acc,))[0] == 0
    assert db.query_one("SELECT COUNT(*) FROM peertube_video_buffers WHERE account_id=?", (acc,))[0] == 0


# --------------------------------------------------------------------------- config y feature flag
def test_config_peertube_defaults():
    from app.core.config_manager import DEFAULTS
    assert "peertube" in DEFAULTS
    pt = DEFAULTS["peertube"]
    assert pt["update_interval"] == 180 and pt["save_position"] is True
    assert "recientes" in pt["section_order"]


def test_feature_flag_peertube():
    from app.core.feature_flags import SERVICIOS, servicio_habilitado, peertube_libreria_instalada
    assert "peertube" in SERVICIOS
    assert peertube_libreria_instalada() is True    # httpx presente
    assert servicio_habilitado("peertube") is True


# --------------------------------------------------------------------------- modelos Pydantic
def test_modelo_oauth_token_ignora_extra():
    t = M.OAuthToken.model_validate({"access_token": "a", "refresh_token": "r",
                                     "expires_in": 86400, "campo_desconocido": 1})
    assert t.access_token == "a" and t.refresh_token == "r" and t.expires_in == 86400


def test_modelo_video_camelcase_y_anidados():
    v = M.Video.model_validate({
        "uuid": "u1", "shortUUID": "s1", "name": "Título", "isLive": False, "duration": 120,
        "language": {"id": "es", "label": "Español"},
        "channel": {"name": "canal", "host": "peertube.test", "displayName": "Mi Canal"},
        "files": [{"fileDownloadUrl": "https://x/f.mp4", "resolution": {"id": 720, "label": "720p"}}],
        "streamingPlaylists": [{"playlistUrl": "https://x/master.m3u8"}],
    })
    assert v.short_uuid == "s1" and v.is_live is False and v.duration == 120
    assert v.language.id == "es"                    # id de idioma es texto
    assert v.channel.handle == "canal@peertube.test"
    assert v.files[0].file_download_url == "https://x/f.mp4" and v.files[0].resolution.id == 720
    assert v.streaming_playlists[0].playlist_url.endswith("master.m3u8")


def test_modelo_server_config_capacidades():
    cfg = M.ServerConfig.model_validate({
        "serverVersion": "8.2.0",
        "instance": {"name": "Test", "defaultNSFWPolicy": "do_not_list"},
        "signup": {"allowed": True, "requiresApproval": False},
        "live": {"enabled": True, "allowReplay": True, "maxDuration": -1},
        "import": {"videos": {"http": {"enabled": True}, "torrent": {"enabled": False}}},
        "user": {"videoQuota": -1},
    })
    assert cfg.server_version == "8.2.0" and cfg.live.enabled is True
    assert cfg.signup.allowed is True and cfg.import_.videos.http.enabled is True
    assert cfg.user.video_quota == -1


def test_parsear_pagina_de_videos():
    datos = {"total": 2, "data": [{"uuid": "a", "name": "A"}, {"uuid": "b", "name": "B"}]}
    res = M.parsear_pagina(M.Video, datos)
    assert res.total == 2 and len(res.data) == 2 and res.data[0].uuid == "a"


def test_parsear_datos_invalidos_lanza_error_legible():
    with pytest.raises(PeerTubeServiceError):
        M.parsear(M.OAuthClient, {"client_id": "solo_id"})  # falta client_secret (obligatorio)


# --------------------------------------------------------------------- duración en lenguaje natural
@pytest.mark.parametrize("segundos,esperado", [
    (1087, "18 minutos y 7 segundos"),   # caso real: un tutorial de ~18 min
    (60, "1 minuto"),
    (61, "1 minuto y 1 segundo"),
    (45, "45 segundos"),
    (3600, "1 hora"),
    (3665, "1 hora, 1 minuto y 5 segundos"),
    (7200, "2 horas"),
    (86399, "23 horas, 59 minutos y 59 segundos"),
])
def test_formatear_duracion_legible(segundos, esperado):
    from app.ui.ui_helpers import formatear_duracion_legible
    assert formatear_duracion_legible(segundos) == esperado


@pytest.mark.parametrize("valor", [0, None, "", "x", -5])
def test_formatear_duracion_legible_sin_duracion(valor):
    """Sin duración válida (0, vacío o un directo) no se inventa texto: cadena vacía."""
    from app.ui.ui_helpers import formatear_duracion_legible
    assert formatear_duracion_legible(valor) == ""


# --------------------------------------------------------- ocultar buffers (Herramientas → Buffers)
def test_peertube_en_dialogo_de_buffers():
    """PeerTube debe poder ocultarse por secciones desde Herramientas → Mostrar u ocultar buffers."""
    from app.ui.buffers_dialog import _BUFFERS, _SERVICIOS, _clave_ocultos
    assert "peertube" in dict(_SERVICIOS)
    claves = [b for b, _lbl in _BUFFERS["peertube"]]
    # Las ramas y sus sub-secciones deben poder ocultarse por separado.
    for esperada in ("recientes", "directos", "biblioteca", "busquedas", "me_gusta", "ver_mas_tarde"):
        assert esperada in claves


def test_peertube_usa_hidden_sections_no_hidden_buffers():
    """PeerTube guarda sus ocultos en 'hidden_sections' (su árbol lee esa clave), no 'hidden_buffers'."""
    from app.ui.buffers_dialog import _clave_ocultos
    assert _clave_ocultos("peertube") == "hidden_sections"
    assert _clave_ocultos("mastodon") == "hidden_buffers"


def test_etiquetas_buffers_peertube_coinciden_con_secciones():
    """Las claves del diálogo deben existir en SECTION_LABELS del árbol (si no, ocultar no haría nada)."""
    from app.ui.buffers_dialog import _BUFFERS
    from app.ui.peertube_views import SECTION_LABELS
    for clave, _lbl in _BUFFERS["peertube"]:
        assert clave in SECTION_LABELS, f"'{clave}' no es una sección real del árbol de PeerTube"


def test_etiquetas_buffers_reddit_coinciden_con_sus_buffers():
    """Igual que PeerTube: las claves del diálogo deben ser buffers reales del árbol de Reddit.
    (Se salta donde praw no esté instalado; en la instalación real sí se comprueba.)"""
    pytest.importorskip("praw")
    from app.ui.buffers_dialog import _BUFFERS
    from app.ui.reddit_views import BUFFER_LABELS
    for clave, _lbl in _BUFFERS["reddit"]:
        assert clave in BUFFER_LABELS, f"'{clave}' no es un buffer real del árbol de Reddit"


def test_solo_se_ofrecen_las_redes_activas(monkeypatch):
    """El diálogo solo debe ofrecer las redes ACTIVAS: una desactivada (o sin su librería, como
    Reddit sin praw) no debe aparecer."""
    import app.ui.buffers_dialog as bd
    activas = {"mastodon", "rss", "peertube"}
    monkeypatch.setattr("app.core.feature_flags.servicio_habilitado", lambda s: s in activas)
    ofrecidas = {s for s, _l in bd.servicios_disponibles()}
    assert ofrecidas == activas
    assert "reddit" not in ofrecidas and "telegram" not in ofrecidas


def test_reddit_se_ofrece_si_esta_activo(monkeypatch):
    import app.ui.buffers_dialog as bd
    monkeypatch.setattr("app.core.feature_flags.servicio_habilitado", lambda s: True)
    ofrecidas = {s for s, _l in bd.servicios_disponibles()}
    assert "reddit" in ofrecidas and "peertube" in ofrecidas


# ------------------------------------------------------- construcción del árbol (ramas y ocultación)
class _ArbolFalso:
    """Árbol mínimo: solo registra los nodos añadidos (no necesitamos wx para esta lógica)."""

    def __init__(self):
        self.nodos = []

    def AppendItem(self, padre, label, data=None):
        self.nodos.append((padre, label, data or {}))
        return f"nodo:{label}"


def _construir(ocultas, orden=None):
    from app.ui.peertube_views import PeerTubeController
    ctrl = PeerTubeController.__new__(PeerTubeController)   # sin frame: solo probamos el volcado
    tree = _ArbolFalso()
    orden = orden or ["recientes", "populares", "tendencias", "locales", "federados",
                      "suscripciones", "biblioteca", "historial", "ver_mas_tarde", "me_gusta",
                      "listas", "mis_videos", "mis_canales", "directos", "notificaciones",
                      "busquedas"]
    ctrl._construir_secciones_cuenta(tree, "cuenta", 1, orden, set(ocultas))
    secciones = [(d.get("peertube_type"), p) for p, _l, d in tree.nodos
                 if d.get("type") == "peertube_section"]
    grupos = [d.get("group") for _p, _l, d in tree.nodos if d.get("type") == "peertube_group"]
    return secciones, grupos


def test_biblioteca_es_rama_y_sus_hijos_cuelgan_de_ella():
    secciones, grupos = _construir(ocultas=[])
    assert "biblioteca" in grupos and "busquedas" in grupos
    padres = dict(secciones)
    # Las sub-secciones personales cuelgan de la rama, no de la cuenta.
    for hijo in ("mis_videos", "mis_canales", "listas", "ver_mas_tarde", "me_gusta", "historial"):
        assert padres[hijo] == "nodo:Mi biblioteca"
    # Y no se duplican: cada una aparece UNA vez pese a estar también en el section_order plano.
    tipos = [t for t, _p in secciones]
    assert len(tipos) == len(set(tipos))


def test_ocultar_rama_biblioteca_no_filtra_sus_hijos_al_primer_nivel():
    """Ocultar la rama debe ocultar el grupo entero: sus hijos NO deben reaparecer sueltos."""
    secciones, grupos = _construir(ocultas=["biblioteca"])
    assert "biblioteca" not in grupos
    tipos = [t for t, _p in secciones]
    for hijo in ("mis_videos", "mis_canales", "listas", "ver_mas_tarde", "me_gusta", "historial"):
        assert hijo not in tipos
    assert "recientes" in tipos   # el resto sigue ahí


def test_ocultar_una_subseccion_concreta():
    secciones, _grupos = _construir(ocultas=["me_gusta"])
    tipos = [t for t, _p in secciones]
    assert "me_gusta" not in tipos and "ver_mas_tarde" in tipos


def test_ocultar_seccion_normal():
    secciones, _grupos = _construir(ocultas=["directos"])
    tipos = [t for t, _p in secciones]
    assert "directos" not in tipos and "recientes" in tipos


# ------------------------------------------------- eliminar una búsqueda guardada desde el árbol
class _FrameFalso:
    def __init__(self):
        self.nodo_borrado = "sin-borrar"

    def _eliminar_nodo_buffer(self, nodo):
        self.nodo_borrado = nodo


def test_eliminar_busqueda_guardada_quita_solo_ese_nodo(monkeypatch):
    """Quita el término de la config y borra SOLO su nodo (no reconstruye el árbol: colgaba la app)."""
    from app.core.config_manager import config
    from app.ui.peertube_views import PeerTubeController
    monkeypatch.setattr("app.core.speech.notificar", lambda *a, **k: None)
    ctrl = PeerTubeController.__new__(PeerTubeController)
    ctrl.f = _FrameFalso()
    config.set("peertube", "saved_searches", ["gatos", "perros"])
    ctrl.eliminar_busqueda_guardada("gatos", nodo="nodo-gatos")
    assert config.get("peertube", "saved_searches") == ["perros"]
    assert ctrl.f.nodo_borrado == "nodo-gatos"


def test_menu_contextual_arbol_solo_en_busquedas():
    """El menú contextual ofrece opciones en una búsqueda guardada; en otras secciones, no."""
    import wx
    from app.ui.peertube_views import PeerTubeController
    app = wx.App() if not wx.GetApp() else wx.GetApp()
    frame = wx.Frame(None)
    ctrl = PeerTubeController.__new__(PeerTubeController)
    ctrl.f = frame
    menu = wx.Menu()
    assert ctrl.menu_contextual_arbol(menu, {"peertube_type": "search:gatos"}, None) is True
    etiquetas = [menu.FindItemById(i).GetItemLabelText() for i in
                 [it.GetId() for it in menu.GetMenuItems()]]
    assert any("Eliminar" in e for e in etiquetas)
    assert ctrl.menu_contextual_arbol(wx.Menu(), {"peertube_type": "recientes"}, None) is False
    frame.Destroy()


# ------------------------------------------- despacho por red activa (regresión: Ctrl+Mayús+R)
def _fuente(ruta):
    return open(ruta, encoding="utf-8").read()


def test_nav_selection_reinicia_todas_las_redes():
    """Al cambiar de nodo del árbol deben reiniciarse las cuentas activas de TODAS las redes.

    Regresión real: 'peertube' faltaba en ese reinicio, así que al pasar de PeerTube a Mastodon su
    cuenta quedaba residual y Ctrl+Mayús+R contestaba '25 vídeos nuevos' estando en Mastodon.
    """
    import re
    redes = set(re.findall(r"active_(\w+)_account_id", _fuente("app/ui/main_frame.py")))
    src = _fuente("app/ui/mf_listados_filas.py")
    bloque = src.split("# Reiniciar filtros", 1)[1].split("_bandeja_activa", 1)[0]
    reiniciadas = set(re.findall(r"active_(\w+)_account_id\s*=\s*None", bloque))
    faltan = redes - reiniciadas
    assert not faltan, f"redes sin reiniciar en _on_nav_selection: {sorted(faltan)}"


def test_refresco_de_peertube_exige_lista_de_peertube():
    """El refresco (Ctrl+Mayús+R) de PeerTube va con cinturón de _modo_lista, como el Intro: sin él,
    una cuenta residual secuestraba el refresco de otra red."""
    import re
    src = _fuente("app/ui/mf_acciones_toot.py")
    # Rama del refresco: debe exigir que la lista mostrada sea de PeerTube.
    rama = src.split("def _on_actualizar_buffer_actual", 1)[1].split("def ", 1)[0]
    idx = rama.find("active_peertube_account_id")
    assert idx != -1, "el refresco ya no despacha a PeerTube"
    contexto = rama[idx:idx + 300]
    assert '_modo_lista' in contexto and 'startswith("peertube_")' in contexto
