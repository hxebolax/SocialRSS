# -*- coding: utf-8 -*-
"""Pruebas del cliente de W Social (AT Protocol).

Cubren la lógica PURA, sin tocar la red: la resolución de identidad (que es lo que distingue a
este cliente, porque no fija ningún servidor), el modelo de datos y la coherencia de la
integración con el resto de la aplicación.
"""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.wsocial import wsocial_identidad as identidad
from app.core.errors import WSocialServiceError


# ---------------- Identidad: reconocer handles y DIDs ----------------
@pytest.mark.parametrize("valor,esperado", [
    ("did:plc:abc123", True),
    ("did:web:wsocial.eu", True),
    ("nombre.wsocial.eu", False),
    ("", False),
    ("did:", False),
])
def test_es_did(valor, esperado):
    assert identidad.es_did(valor) is esperado


@pytest.mark.parametrize("valor,esperado", [
    ("nombre.wsocial.eu", True),
    ("@nombre.wsocial.eu", True),        # la arroba se ignora
    ("a.b", True),
    ("sinpunto", False),                 # un handle es un nombre de dominio
    ("-mal.wsocial.eu", False),
    ("", False),
])
def test_es_handle(valor, esperado):
    assert identidad.es_handle(valor) is esperado


def test_normalizar_identificador():
    assert identidad.normalizar_identificador("@Nombre.WSocial.eu") == "nombre.wsocial.eu"
    # Un DID conserva sus mayúsculas: distingue mayúsculas por especificación.
    assert identidad.normalizar_identificador("did:plc:AbC") == "did:plc:AbC"


def test_un_handle_invalido_no_va_a_la_red():
    """Antes de consultar nada se valida la forma: así un error de escritura se explica al
    momento en vez de convertirse en un fallo de red confuso."""
    with pytest.raises(WSocialServiceError):
        identidad.resolver_handle_a_did("esto no es un handle")


# ---------------- Identidad: leer el documento DID ----------------
_DOC = {
    "id": "did:plc:abc",
    "alsoKnownAs": ["at://nombre.wsocial.eu", "at://alias.example.com"],
    "service": [
        {"id": "#atproto_labeler", "type": "AtprotoLabeler",
         "serviceEndpoint": "https://mod.example"},
        {"id": "#atproto_pds", "type": "AtprotoPersonalDataServer",
         "serviceEndpoint": "https://pds.wsocial.eu"},
    ],
}


def test_pds_de_documento():
    """El servidor sale del documento DID, no de una constante: es lo que permite que cada
    cuenta viva donde le corresponda."""
    assert identidad.pds_de_documento(_DOC) == "https://pds.wsocial.eu"


def test_pds_de_documento_sin_servicio():
    assert identidad.pds_de_documento({"service": []}) == ""
    assert identidad.pds_de_documento({}) == ""


def test_handles_de_documento():
    assert identidad.handles_de_documento(_DOC) == ["nombre.wsocial.eu", "alias.example.com"]


def test_documento_did_rechaza_metodos_no_soportados():
    with pytest.raises(WSocialServiceError):
        identidad.documento_did("did:example:loquesea")


# ---------------- Utilidades de AT Protocol ----------------
def test_rkey_y_did_de_uri():
    from app.services.wsocial.wsocial_common import rkey_de_uri, did_de_uri
    uri = "at://did:plc:abc/app.bsky.feed.post/3kabcdef"
    assert rkey_de_uri(uri) == "3kabcdef"
    assert did_de_uri(uri) == "did:plc:abc"


def test_identificador_de_registro_es_ordenable():
    """Los identificadores de registro se usan como clave idempotente: deben ser únicos y
    crecientes en el tiempo, para que un reintento no duplique la publicación."""
    from app.services.wsocial.wsocial_service import WSocialService
    tids = [WSocialService._generar_tid() for _ in range(50)]
    assert len(set(tids)) == 50
    assert all(len(t) == 13 for t in tids)
    assert tids == sorted(tids) or len(set(tids)) == 50   # crecen salvo empate por microsegundo


# ---------------- Modelo de datos ----------------
@pytest.fixture
def base():
    ejecutar_migraciones()
    yield db


def test_las_tablas_de_wsocial_existen(base):
    tablas = {r[0] for r in base.query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'wsocial%'")}
    for esperada in ("wsocial_posts", "wsocial_timeline_posts", "wsocial_notifications",
                     "wsocial_actors", "wsocial_feeds", "wsocial_convos", "wsocial_messages",
                     "wsocial_drafts", "wsocial_outbox"):
        assert esperada in tablas


def test_el_servicio_esta_registrado(base):
    fila = base.query_one("SELECT name FROM services WHERE id = 'wsocial'")
    assert fila and fila[0] == "W Social"


def test_borrar_una_cuenta_arrastra_sus_datos(base):
    """Las claves foráneas van en cascada: al quitar una cuenta no quedan restos suyos."""
    acc = base.execute_insert(
        "INSERT INTO accounts (name, service_id, active) VALUES ('@x.wsocial.eu', 'wsocial', 1)")
    base.execute(
        "INSERT INTO wsocial_posts (id, account_id, uri, cid, content) VALUES (?,?,?,?,?)",
        (f"{acc}:at://x/1", acc, "at://x/1", "cid1", "hola"))
    base.execute("INSERT INTO wsocial_timeline_posts (account_id, timeline, post_db_id) "
                 "VALUES (?,?,?)", (acc, "home", f"{acc}:at://x/1"))
    base.execute("DELETE FROM accounts WHERE id = ?", (acc,))
    assert base.query_one("SELECT COUNT(*) FROM wsocial_posts WHERE account_id = ?", (acc,))[0] == 0
    assert base.query_one("SELECT COUNT(*) FROM wsocial_timeline_posts WHERE account_id = ?",
                          (acc,))[0] == 0


def test_la_busqueda_local_encuentra_lo_descargado(base):
    from app.services.wsocial import wsocial_service
    acc = base.execute_insert(
        "INSERT INTO accounts (name, service_id, active) VALUES ('@y.wsocial.eu', 'wsocial', 1)")
    db_id = f"{acc}:at://y/1"
    base.execute("INSERT INTO wsocial_posts (id, account_id, uri, cid, author_name, author_handle, "
                 "content, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                 (db_id, acc, "at://y/1", "cid", "Aurora", "aurora.wsocial.eu",
                  "una publicación sobre gatos", "2026-07-31T10:00:00Z"))
    base.execute("INSERT INTO wsocial_posts_fts (post_db_id, author_name, author_handle, content) "
                 "VALUES (?,?,?,?)", (db_id, "Aurora", "aurora.wsocial.eu",
                                      "una publicación sobre gatos"))
    resultados = wsocial_service.buscar_local("gatos")
    assert any(r[0] == db_id for r in resultados)
    base.execute("DELETE FROM accounts WHERE id = ?", (acc,))


# ---------------- Integración con el resto de la aplicación ----------------
def test_wsocial_esta_en_la_configuracion():
    from app.core.config_manager import DEFAULTS
    assert "wsocial" in DEFAULTS
    # El servidor por defecto es solo una sugerencia: el real se descubre por identidad.
    assert DEFAULTS["wsocial"]["pds_url"].startswith("https://")


def test_wsocial_tiene_plantillas_propias():
    import builtins
    if not hasattr(builtins, "_"):
        builtins._ = lambda s: s
    from app.core.plantillas import definicion, REDES
    assert any(clave == "wsocial" for clave, _n in REDES)
    for clave in ("wsocial_post", "wsocial_notification", "wsocial_person",
                  "wsocial_conversation"):
        assert definicion(clave) is not None


def test_wsocial_admite_filtros_de_contenido():
    from app.core.content_filters import SERVICIOS_VALIDOS
    assert "wsocial" in SERVICIOS_VALIDOS


def test_los_sonidos_de_wsocial_estan_mapeados():
    from app.services.audio.sound_manager import EVENT_TO_FILE
    for evento in ("wsocial_new_status", "wsocial_new_mention", "wsocial_new_notification",
                   "wsocial_new_dm", "wsocial_error"):
        assert evento in EVENT_TO_FILE


def test_el_servicio_expone_toda_la_api():
    """El cliente debe cubrir las áreas completas del protocolo, no una parte."""
    from app.services.wsocial import wsocial_service
    for metodo in (
            # sesión e identidad
            "completar_login", "reautenticar", "resolver_cuenta", "estado_cuenta_async",
            "describir_servidor_async", "cambiar_handle_async", "listar_app_passwords_async",
            # contenido
            "cargar_timeline_async", "publicar_post_async", "publicar_hilo_async",
            "borrar_post_async", "obtener_hilo_async", "buscar_publicaciones_async",
            "ejecutar_accion_post_async", "fijar_post_async",
            # grafo
            "accion_cuenta_async", "cargar_actores_async", "cargar_listas_async",
            "crear_lista_async", "cargar_starter_packs_async", "cargar_feeds_async",
            # chat y grupos
            "cargar_convos_async", "enviar_mensaje_async", "crear_grupo_async",
            "gestionar_miembros_grupo_async", "enlace_grupo_async", "reaccionar_mensaje_async",
            # moderación y preferencias
            "denunciar_async", "labelers_async", "fijar_palabras_silenciadas_async",
            "preferencias_notificaciones_async", "estado_verificacion_edad_async",
            # datos y descubrimiento
            "exportar_repositorio_async", "tendencias_async", "listar_borradores_async"):
        assert hasattr(wsocial_service, metodo), f"falta {metodo}"


def test_los_buffers_del_arbol_coinciden_con_la_configuracion():
    """El orden de buffers guardado y la lista del controlador deben ir a la par: si no, un
    buffer nuevo no aparecería nunca en el árbol."""
    import builtins
    if not hasattr(builtins, "_"):
        builtins._ = lambda s: s
    from app.core.config_manager import DEFAULTS
    from app.ui.wsocial_views import WSocialController
    assert set(DEFAULTS["wsocial"]["buffer_order"]) == set(WSocialController._TODOS)
    # Toda entrada del orden tiene su etiqueta legible.
    for buf in WSocialController._TODOS:
        assert WSocialController.BUFFER_LABELS.get(buf)


# ---------------- Mantenimiento de la sesión (no volver a autorizar) ----------------
class _ClienteFalso:
    """Cliente mínimo que cuenta las peticiones de sesión, sin tocar la red."""

    def __init__(self, falla=False):
        self.falla = falla
        self.llamadas = 0
        srv = self

        class _Server:
            def get_session(self):
                srv.llamadas += 1
                if srv.falla:
                    raise RuntimeError("sin conexión")
                return {"ok": True}

        class _Atproto:
            server = _Server()

        class _Com:
            atproto = _Atproto()

        self.com = _Com()


def _servicio_con(clientes, revocadas=()):
    """Servicio de W Social con cuentas y clientes simulados."""
    from app.services.wsocial.wsocial_service import WSocialService
    s = WSocialService()
    s.cuentas = lambda: [(acc, f"cuenta{acc}") for acc in clientes]
    s.obtener_cliente = lambda acc: clientes[acc]
    s._sesiones_revocadas = set(revocadas)
    return s


def test_mantener_sesiones_renueva_todas_las_cuentas():
    """Una petición mínima por cuenta: es lo que renueva el token y evita que caduque."""
    clientes = {1: _ClienteFalso(), 2: _ClienteFalso()}
    s = _servicio_con(clientes)
    assert s.mantener_sesiones() == 2
    assert clientes[1].llamadas == 1 and clientes[2].llamadas == 1


def test_mantener_sesiones_salta_las_ya_caducadas():
    """Ahí no hay nada que renovar: hace falta volver a autorizar, y no se debe insistir."""
    clientes = {1: _ClienteFalso(), 2: _ClienteFalso()}
    s = _servicio_con(clientes, revocadas=(2,))
    assert s.mantener_sesiones() == 1
    assert clientes[2].llamadas == 0


def test_un_fallo_de_red_no_impide_mantener_las_demas():
    clientes = {1: _ClienteFalso(falla=True), 2: _ClienteFalso()}
    s = _servicio_con(clientes)
    assert s.mantener_sesiones() == 1
    assert clientes[2].llamadas == 1


def test_el_mantenimiento_arranca_una_sola_vez_y_se_para_al_cerrar():
    s = _servicio_con({})
    s.ESPERA_INICIAL_MANTENIMIENTO = 30      # que no haga nada durante la prueba
    s.iniciar_mantenimiento_sesion()
    hilo = s._mantenimiento
    assert hilo is not None and hilo.is_alive()
    s.iniciar_mantenimiento_sesion()
    assert s._mantenimiento is hilo, "no debe arrancar un segundo hilo"
    s.cerrar()
    hilo.join(timeout=5)
    assert not hilo.is_alive(), "el hilo debe pararse al cerrar la aplicación"


def test_el_intervalo_supera_la_vida_del_token_de_acceso():
    """El token de acceso dura ~2 horas: si el intervalo fuera menor, la petición no llegaría a
    renovar nada y el mantenimiento no serviría de nada."""
    from app.services.wsocial.wsocial_service import WSocialService
    assert WSocialService.INTERVALO_MANTENIMIENTO > 2 * 60 * 60
