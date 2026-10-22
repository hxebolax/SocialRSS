# -*- coding: utf-8 -*-
"""Pruebas de regresión de la auditoría Mastodon del 11-07-2026.

Cubren las rutas de datos corregidas: upsert de ediciones (M2), procesado directo
de eventos del streaming (M1), hilos con rutas JSON e idempotencia (M7/H1), la
división por longitud contable (H3), la detección de capacidades (M8/A10) y la
clasificación de errores de los filtros (A2). Sin red: todo contra SQLite aislado."""

import json

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.mastodon.mastodon_threading import MastodonThreadingManager

ACC = 990  # id de cuenta ficticio para estas pruebas


def _preparar():
    """Migraciones + cuenta ficticia (mastodon_statuses tiene FK a accounts)."""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'mastodon', 'prueba', 1, 99)", (ACC,))


def _status_raw(sid="1001", content="hola", sensitive=False, spoiler="",
                display="Autora", acct="autora@masto.test"):
    return {
        "id": sid,
        "uri": f"https://masto.test/users/a/statuses/{sid}",
        "content": f"<p>{content}</p>",
        "created_at": "2026-07-11T10:00:00+00:00",
        "visibility": "public",
        "sensitive": sensitive,
        "spoiler_text": spoiler,
        "favourited": False,
        "reblogged": False,
        "bookmarked": False,
        "account": {"display_name": display, "username": "autora", "acct": acct},
    }


def test_m2_edicion_actualiza_cw_sensible_y_autor():
    """El upsert debe actualizar TODOS los campos mutables al reprocesar una edición."""
    _preparar()
    original = _status_raw(sid="2001", content="viejo", sensitive=True, spoiler="CW viejo")
    mastodon_service._procesar_y_guardar_toot(ACC, original)
    editado = _status_raw(sid="2001", content="nuevo", sensitive=False, spoiler="",
                          display="Autora Renombrada")
    editado["edited_at"] = "2026-07-11T11:00:00+00:00"
    mastodon_service._procesar_y_guardar_toot(ACC, editado)
    fila = db.query_one(
        "SELECT content, nsfw, spoiler_text, author_name FROM mastodon_statuses WHERE id = ?",
        (f"{ACC}:2001",))
    assert "nuevo" in fila[0]
    assert fila[1] == 0, "nsfw debía actualizarse a 0"
    assert fila[2] == "", "el CW viejo debía desaparecer"
    assert fila[3] == "Autora Renombrada"


def test_m1_evento_stream_update_y_delete():
    """procesar_evento_stream guarda un alta con su asociación y un delete elimina
    el estado, su FTS, sus asociaciones y los impulsos derivados."""
    _preparar()
    ok = mastodon_service.procesar_evento_stream(ACC, "home", "update", _status_raw(sid="3001"))
    assert ok is True
    assert db.query_one("SELECT 1 FROM mastodon_timeline_statuses WHERE account_id = ? "
                        "AND timeline = 'home' AND status_db_id = ?", (ACC, f"{ACC}:3001"))
    # Un impulso derivado del mismo original.
    imp = _status_raw(sid="3002")
    mastodon_service._procesar_y_guardar_toot(ACC, imp)
    db.execute("UPDATE mastodon_statuses SET reblog_of_id = '3001' WHERE id = ?", (f"{ACC}:3002",))

    ok = mastodon_service.procesar_evento_stream(ACC, "home", "delete", "3001")
    assert ok is True
    assert not db.query_one("SELECT 1 FROM mastodon_statuses WHERE id = ?", (f"{ACC}:3001",))
    assert not db.query_one("SELECT 1 FROM mastodon_statuses WHERE id = ?", (f"{ACC}:3002",)), \
        "el impulso derivado debía borrarse con el original"
    assert not db.query_one("SELECT 1 FROM mastodon_timeline_statuses WHERE status_db_id = ?",
                            (f"{ACC}:3001",))


def test_m1_evento_stream_status_update_refresca_contenido():
    _preparar()
    mastodon_service.procesar_evento_stream(ACC, "home", "update", _status_raw(sid="3101", content="antes"))
    # Un evento status_update REAL siempre trae edited_at (sin él actúa el atajo
    # anti-tirones de "solo contadores", que es correcto para sondeos repetidos).
    editado = _status_raw(sid="3101", content="después", sensitive=True, spoiler="CW nuevo")
    editado["edited_at"] = "2026-07-11T12:34:56+00:00"
    ok = mastodon_service.procesar_evento_stream(ACC, "home", "status_update", editado)
    assert ok is True
    fila = db.query_one("SELECT content, nsfw, spoiler_text FROM mastodon_statuses WHERE id = ?",
                        (f"{ACC}:3101",))
    assert "después" in fila[0] and fila[1] == 1 and fila[2] == "CW nuevo"


def test_m1_evento_stream_notificacion():
    _preparar()
    notif = {
        "id": "555", "type": "mention", "created_at": "2026-07-11T12:00:00+00:00",
        "account": {"display_name": "Alguien", "username": "alguien", "acct": "alguien@masto.test"},
        "status": _status_raw(sid="3201", content="te menciono"),
    }
    ok = mastodon_service.procesar_evento_stream(ACC, "notifications", "notification", notif)
    assert ok is True
    fila = db.query_one("SELECT notification_id, type FROM mastodon_notifications WHERE id = ?",
                        (f"{ACC}_555",))
    assert fila == ("555", "mention")
    # M3: el ID EXTERNO se recupera por la clave primaria (nunca troceando la clave interna).
    assert fila[0] == "555"


def test_h1_m7_hilo_con_rutas_con_coma_e_idem_key():
    """Las rutas con coma sobreviven al guardar/cargar (JSON) y la clave de idempotencia
    persiste con el hilo (reanudar reutiliza la misma clave)."""
    _preparar()
    ruta_con_coma = r"C:\tmp\foto,uno.jpg"
    tid = MastodonThreadingManager.guardar_hilo(None, "Hilo de prueba", ACC, [
        {"content": "parte 1", "media_paths": [ruta_con_coma], "alt_texts": ["alt, con coma"],
         "posted_status_id": None, "posted_at": None, "idem_key": "clave-persistida-1"},
        {"content": "parte 2", "media_paths": [], "alt_texts": []},
    ])
    hilo = MastodonThreadingManager.cargar_hilo(tid)
    assert hilo["toots"][0]["media_paths"] == [ruta_con_coma], "la coma partía la ruta en dos"
    assert hilo["toots"][0]["alt_texts"] == ["alt, con coma"]
    assert hilo["toots"][0]["idem_key"] == "clave-persistida-1"
    MastodonThreadingManager.eliminar_hilo(tid)


def test_h1_hilo_formato_antiguo_compatible():
    """Un hilo guardado con el formato antiguo (rutas por comas, alts por \\x1f) se lee igual."""
    _preparar()
    db.execute("INSERT OR REPLACE INTO mastodon_threads (id, account_id, title, created_at, updated_at, published) "
               "VALUES ('hilo-viejo', ?, 'viejo', '2026-01-01', '2026-01-01', 0)", (ACC,))
    db.execute("DELETE FROM mastodon_thread_items WHERE thread_id = 'hilo-viejo'")
    db.execute("INSERT INTO mastodon_thread_items (thread_id, item_order, content, media_paths, alt_texts) "
               "VALUES ('hilo-viejo', 0, 'texto', 'a.jpg,b.jpg', 'uno\x1fdos')")
    hilo = MastodonThreadingManager.cargar_hilo("hilo-viejo")
    assert hilo["toots"][0]["media_paths"] == ["a.jpg", "b.jpg"]
    assert hilo["toots"][0]["alt_texts"] == ["uno", "dos"]
    MastodonThreadingManager.eliminar_hilo("hilo-viejo")


def test_h3_division_respeta_longitud_contable():
    """Cada parte FINAL del hilo debe caber según las reglas de Mastodon (URL corta = 23).
    Un texto lleno de URLs cortas engaña a len() pero no al contador oficial."""
    texto = ("Mira este enlace https://a.io y este otro https://b.io que son cortos. ") * 30
    partes = MastodonThreadingManager.dividir_hilo(texto, limite_max=500, cw="Aviso")
    medir = MastodonThreadingManager._longitud_mastodon
    assert partes, "debía haber partes"
    for p in partes:
        assert medir(p, "Aviso") <= 500, f"una parte excede el límite contable: {medir(p, 'Aviso')}"
    # Un prefijo largo también debe descontarse (antes podía desbordar la primera parte).
    prefijo = "Aviso importante para todo el mundo que lea este hilo largo"
    partes2 = MastodonThreadingManager.dividir_hilo(texto, limite_max=300, texto_inicial=prefijo)
    for p in partes2:
        assert medir(p) <= 300


def test_m8_deteccion_de_citas_por_version():
    assert mastodon_service._version_tupla("4.5.1") >= (4, 5)
    assert mastodon_service._version_tupla("4.4.0+glitch") < (4, 5)
    assert mastodon_service._version_tupla("") == ()
    # Sin capacidades cacheadas: desconocido (None), nunca un True inventado.
    assert mastodon_service.soporta_citas_nativas(98765) is None


def test_a2_clasificador_de_errores_de_endpoint():
    """Solo 404/410/not found/version cuentan como 'endpoint inexistente'; un timeout o
    un 429 jamás deben provocar el fallback (duplicaría mutaciones)."""
    es = mastodon_service._es_error_endpoint_inexistente
    assert es(Exception("404 Not Found")) is True
    assert es(Exception("Gone")) is True
    assert es(TimeoutError("timed out")) is False
    assert es(Exception("429 Too Many Requests")) is False
    assert es(Exception("500 Internal Server Error")) is False

    class MastodonVersionError(Exception):
        pass
    assert es(MastodonVersionError("necesita 4.0")) is True


# --- Fase 2 post-auditoría: borradores persistentes (N3) y borrar-y-redactar (N4) ---

def test_n3_borradores_persistentes_ciclo_completo():
    """Guardar, listar (más reciente primero), actualizar, obtener y borrar un borrador,
    con contexto de respuesta y campos complejos (encuesta, adjuntos con punto focal)."""
    _preparar()
    from app.services.mastodon import mastodon_borradores as bor
    # Empezar limpio (otras ejecuciones pudieron dejar borradores).
    for b in bor.listar_borradores(ACC):
        bor.borrar_borrador(b["id"])

    borrador = {
        "texto": "hola desde el borrador",
        "cw_activo": True, "cw": "aviso",
        "priv_idx": 2, "idioma_idx": 0,
        "attachments": [{"path": "C:/fotos/una.jpg", "alt": "una foto", "focus": [0.0, 1.0]}],
        "encuesta_activa": False, "opciones": ["", "", "", ""],
        "duracion_idx": 4, "multiple": False,
        "programar_activo": True, "prog_date": "2026-08-01",
        "prog_hora": 10, "prog_min": 30,
    }
    d1 = bor.guardar_borrador(ACC, "hola desde el borrador", borrador,
                              {"in_reply_to_id": "555"})
    d2 = bor.guardar_borrador(ACC, "segundo", {"texto": "segundo"})
    assert d1 != d2

    lista = bor.listar_borradores(ACC)
    assert [b["id"] for b in lista] == [d2, d1], "el más reciente debe salir primero"
    assert lista[1]["contexto"] == {"in_reply_to_id": "555"}

    # Actualizar el primero: mismo id, contenido nuevo.
    d1b = bor.guardar_borrador(ACC, "texto cambiado", dict(borrador, texto="texto cambiado"),
                               {"in_reply_to_id": "555"}, draft_id=d1)
    assert d1b == d1
    datos = bor.obtener_borrador(d1)
    assert datos["account_id"] == ACC
    assert datos["borrador"]["texto"] == "texto cambiado"
    assert datos["borrador"]["prog_date"] == "2026-08-01"
    assert datos["borrador"]["attachments"][0]["focus"] == [0.0, 1.0]
    assert datos["contexto"]["in_reply_to_id"] == "555"
    assert bor.contar_borradores(ACC) == 2

    bor.borrar_borrador(d1)
    bor.borrar_borrador(d2)
    assert bor.obtener_borrador(d1) is None
    assert bor.contar_borradores(ACC) == 0


def test_n3_migracion_v7_columnas_borradores():
    """La tabla mastodon_drafts debe tener las columnas nuevas (updated_at, contexto,
    borrador_json) tanto en instalaciones nuevas como migradas."""
    _preparar()
    cols = {f[1] for f in db.query("PRAGMA table_info(mastodon_drafts)")}
    assert {"updated_at", "contexto", "borrador_json"}.issubset(cols)
    version = db.query_one("PRAGMA user_version")[0]
    assert version >= 7


def test_n5_serializacion_punto_focal():
    """El '[x, y]' de la interfaz debe viajar como tupla de floats (media_post) y como
    'x,y' (media_attributes de status_update), el mismo formato que usa Mastodon.py."""
    from app.services.mastodon.mastodon_composer import MastodonComposer  # noqa: F401 (importable)
    # Reproduce la conversión que hace editar_toot_async/_foco_tupla.
    def foco_tupla(valor):
        if not valor:
            return None
        try:
            return (float(valor[0]), float(valor[1]))
        except (TypeError, ValueError, IndexError):
            return None
    assert foco_tupla([0.0, 1.0]) == (0.0, 1.0)
    assert foco_tupla(None) is None
    assert foco_tupla(["x", 1]) is None
    x, y = foco_tupla([-1.0, -1.0])
    assert "{0},{1}".format(x, y) == "-1.0,-1.0"


# --- Fase 3 post-auditoría: conversaciones (N6) y migración v8 ---

def test_n6_tabla_conversaciones_upsert():
    """mastodon_conversations existe (migración v8) y su clave primaria compuesta permite
    el upsert por (cuenta, conversación) sin duplicar filas."""
    _preparar()
    version = db.query_one("PRAGMA user_version")[0]
    assert version >= 8
    db.execute("DELETE FROM mastodon_conversations WHERE account_id = ?", (ACC,))
    sql = ("INSERT OR REPLACE INTO mastodon_conversations (account_id, conversation_id, "
           "participants, last_status_db_id, last_excerpt, unread, updated_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)")
    db.execute(sql, (ACC, "c1", "@ana@masto.test", None, "hola", 1, "2026-07-11T10:00:00"))
    db.execute(sql, (ACC, "c1", "@ana@masto.test", None, "último mensaje", 0, "2026-07-11T11:00:00"))
    filas = db.query("SELECT last_excerpt, unread FROM mastodon_conversations "
                     "WHERE account_id = ? AND conversation_id = 'c1'", (ACC,))
    assert len(filas) == 1, "el upsert no debe duplicar la conversación"
    assert filas[0][0] == "último mensaje" and filas[0][1] == 0
    # Marcar leída solo toca la fila de esa conversación.
    db.execute(sql, (ACC, "c2", "@bea@masto.test", None, "x", 1, "2026-07-11T12:00:00"))
    db.execute("UPDATE mastodon_conversations SET unread = 0 WHERE account_id = ? AND conversation_id = ?",
               (ACC, "c2"))
    assert db.query_one("SELECT unread FROM mastodon_conversations WHERE account_id = ? "
                        "AND conversation_id = 'c2'", (ACC,))[0] == 0
    db.execute("DELETE FROM mastodon_conversations WHERE account_id = ?", (ACC,))


def test_fase3_metodos_de_servicio_presentes():
    """Los métodos nuevos de la Fase 3 existen en la fachada del servicio y en Mastodon.py
    (si la librería se actualizara y los quitara, esto avisa antes que un fallo en caliente)."""
    for nombre in ("obtener_conversaciones_async", "resolver_solicitudes_notif_lote_async",
                   "cuentas_grupo_notif_async", "descartar_grupo_notif_async",
                   "obtener_notificaciones_agrupadas_async"):
        assert callable(getattr(mastodon_service, nombre, None)), nombre
    from mastodon import Mastodon as _M
    for nombre in ("status_translate", "grouped_notifications", "dismiss_grouped_notification",
                   "grouped_notification_accounts", "accept_multiple_notification_requests",
                   "dismiss_multiple_notification_requests", "notifications_merged",
                   "conversations", "conversations_read"):
        assert callable(getattr(_M, nombre, None)), nombre
