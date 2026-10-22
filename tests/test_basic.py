# -*- coding: utf-8 -*-
"""
Pruebas unitarias básicas para verificar la lógica de la suite.
Prueba la división de hilos, base de datos FTS5 y servicios básicos de traducción.
"""

import os
import pytest
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.mastodon.mastodon_threading import MastodonThreadingManager
from app.services.translation.translation_service import translation_service
from app.services.audio.sound_manager import sound_manager

def test_dividir_hilo_simple():
    """Verifica que el divisor de hilos respecte los límites de caracteres y formatee correctamente."""
    texto_largo = (
        "Este es un párrafo de prueba bastante largo para verificar que el divisor inteligente "
        "funcione correctamente. Debería ser dividido de forma limpia en múltiples partes sin cortar "
        "palabras ni romper enlaces importantes que se agreguen en el cuerpo del mensaje.\n\n"
        "Además, probamos que los párrafos se preserven cuando sea posible y que la numeración "
        "se inserte de forma ordenada al final de cada toot del hilo de Mastodon. "
        "Por ejemplo, si el límite es muy corto, se generarán varios toots."
    )
    
    # Probar con un límite corto de 140 caracteres
    toots = MastodonThreadingManager.dividir_hilo(
        texto_largo,
        limite_max=140,
        margen=10,
        numerar=True,
        formato="simple"
    )
    
    assert len(toots) > 1
    for t in toots:
        assert len(t) <= 140
        # Verificar que acabe con número de parte (ej: "1/4")
        assert "/" in t

def test_database_y_fts5():
    """Comprueba que la base de datos esté activa e indexe en texto completo FTS5."""
    ejecutar_migraciones()
    # Insertar cuenta de prueba para foreign key
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (1, 'Test Account', 'mastodon', 1)")
    # Insertar un registro de prueba
    db_id = "test:1"
    db.execute("DELETE FROM mastodon_statuses WHERE id = ?", (db_id,))
    db.execute("DELETE FROM mastodon_statuses_fts WHERE status_db_id = ?", (db_id,))
    
    sql = """
    INSERT INTO mastodon_statuses (
        id, account_id, status_id, uri, author_name, author_username, content, created_at, visibility, favourited, reblogged, bookmarked
    ) VALUES (?, 1, '1', 'http://test', 'Tester', '@tester', 'Este es un toot de prueba sobre accesibilidad y lectores de pantalla.', '2026-06-13T00:00:00', 'public', 0, 0, 0)
    """
    db.execute(sql, (db_id,))
    
    # Insertar en FTS
    db.execute(
        "INSERT INTO mastodon_statuses_fts (status_db_id, author_name, author_username, content, spoiler_text) VALUES (?, ?, ?, ?, ?)",
        (db_id, "Tester", "@tester", "Este es un toot de prueba sobre accesibilidad y lectores de pantalla.", "")
    )
    
    # Buscar
    sql_fts = """
    SELECT status_db_id FROM mastodon_statuses_fts
    WHERE mastodon_statuses_fts MATCH '"accesibilidad"'
    """
    res = db.query(sql_fts)
    assert len(res) >= 1
    assert res[0][0] == db_id

def test_translation_cache():
    """Verifica que el servicio de traducción guarde historial y responda."""
    ejecutar_migraciones()
    texto = "Hello world"
    # Traducir al español
    traduccion = translation_service.traducir(texto, idioma_destino="es")
    assert "mundo" in traduccion.lower() or "hola" in traduccion.lower()
    
    # Comprobar que existe en la base de datos de historial
    import hashlib
    text_hash = hashlib.md5(texto.encode("utf-8")).hexdigest()
    row = db.query_one("SELECT translated_text FROM translation_history WHERE text_hash = ?", (text_hash,))
    assert row is not None
    assert row[0] == traduccion

def test_sound_manager_config():
    """Comprueba que el SoundManager responda a cambios de volumen en config."""
    from app.core.config_manager import config
    config.set("audio", "volume", 0.5)
    config.save()
    
    assert config.get("audio", "volume", 0.8) == 0.5


def test_mastodon_quoted_links_cleaning():
    """Verifica que los enlaces de toots citados se limpien en _procesar_y_guardar_toot."""
    ejecutar_migraciones()
    # Mock status con un link a otro status
    raw_status = {
        "id": "999999",
        "uri": "http://test-quote",
        "content": 'RE: <a href="https://comunidad.nvda.es/@HXeBoLaX/116740886312671733">https://comunidad.nvda.es/@HXeBoLaX/116740886312671733</a>\nLo han desactivado?',
        "created_at": "2026-06-13T07:56:58+00:00",
        "visibility": "public",
        "account": {"display_name": "Test User", "acct": "testuser"},
        "sensitive": False,
        "favourited": False,
        "reblogged": False,
        "bookmarked": False,
        "media_attachments": []
    }
    from app.services.mastodon.mastodon_service import mastodon_service
    # Insertar la cuenta
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (1, 'Test Account', 'mastodon', 1)")
    
    status_obj = mastodon_service._procesar_y_guardar_toot(1, raw_status)
    assert "[@HXeBoLaX (publicación)]" in status_obj.content
    assert "https://comunidad.nvda.es" not in status_obj.content

def test_relative_time_formatting():
    """Comprueba que la función formatear_tiempo_relativo formatee de forma correcta en español."""
    from app.ui.main_frame import formatear_tiempo_relativo
    from app.core.config_manager import config
    import datetime

    # Esta prueba comprueba el formato RELATIVO, así que se fuerza esa preferencia (y se
    # restaura), para no depender del valor que tenga el usuario en su configuración.
    orig = config.get("general", "relative_times", True)
    config.set("general", "relative_times", True)
    try:
        now = datetime.datetime.now()

        # Pruebas con distintos tiempos relativos
        t_seg = now - datetime.timedelta(seconds=15)
        t_min = now - datetime.timedelta(minutes=5)
        t_hour = now - datetime.timedelta(hours=3)
        t_day = now - datetime.timedelta(days=2)

        assert "segundos" in formatear_tiempo_relativo(t_seg.isoformat())
        assert "5 minutos" in formatear_tiempo_relativo(t_min.isoformat())
        assert "3 horas" in formatear_tiempo_relativo(t_hour.isoformat())
        assert "2 días" in formatear_tiempo_relativo(t_day.isoformat())
    finally:
        config.set("general", "relative_times", orig)

def test_auto_creation_rss_account_and_urls_json():
    """Verifica que NO se auto-crea cuenta RSS y la extracción de URLs de toots y artículos."""
    ejecutar_migraciones()

    # 1. No debe crearse ninguna cuenta RSS automáticamente; las crea el usuario
    db.execute("DELETE FROM accounts WHERE service_id = 'rss'")
    row = db.query_one("SELECT COUNT(*) FROM accounts WHERE service_id = 'rss'")
    assert row[0] == 0

    # 2. Comprobar extracción de URLs de toots
    from app.services.mastodon.mastodon_service import mastodon_service
    import json
    
    raw_status = {
        "id": "123456",
        "uri": "http://uri-quote",
        "content": 'Visita <a href="https://google.com">Google</a> o <a href="https://github.com">GitHub</a>',
        "created_at": "2026-06-13T07:56:58+00:00",
        "visibility": "public",
        "account": {"display_name": "Test User", "acct": "testuser"},
        "sensitive": False,
        "favourited": False,
        "reblogged": False,
        "bookmarked": False,
        "media_attachments": [{"url": "https://test.com/media.mp3", "type": "audio"}]
    }
    
    mastodon_service._procesar_y_guardar_toot(1, raw_status)
    urls_row = db.query_one("SELECT urls_json FROM mastodon_statuses WHERE id = '1:123456'")
    assert urls_row is not None
    urls = json.loads(urls_row[0])
    
    # Debería contener los 2 enlaces del HTML y el del adjunto
    assert "https://google.com" in urls
    assert "https://github.com" in urls
    assert "https://test.com/media.mp3" in urls


def test_mastodon_notification_processing():
    """Verifica el procesamiento e inserción de notificaciones en la base de datos."""
    ejecutar_migraciones()
    
    # 1. Asegurar cuenta
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (1, 'Test Account', 'mastodon', 1)")
    
    # Mock de notificación
    raw_notif = {
        "id": "78910",
        "type": "mention",
        "created_at": "2026-06-13T08:00:00+00:00",
        "account": {"display_name": "Notifier User", "acct": "notifier@example.com"},
        "status": {
            "id": "111213",
            "uri": "http://status-notif",
            "content": "Hola @testuser, esto es una mención.",
            "created_at": "2026-06-13T08:00:00+00:00",
            "visibility": "public",
            "account": {"display_name": "Notifier User", "acct": "notifier@example.com"},
            "sensitive": False,
            "favourited": False,
            "reblogged": False,
            "bookmarked": False,
            "media_attachments": []
        }
    }
    
    from app.services.mastodon.mastodon_service import mastodon_service
    res = mastodon_service._procesar_y_guardar_notificacion(1, raw_notif)
    
    assert res["notification_id"] == "78910"
    assert res["type"] == "mention"
    assert res["from_name"] == "Notifier User"
    assert res["from_username"] == "@notifier@example.com"
    assert res["status_id"] == "111213"
    
    # Comprobar en base de datos
    row = db.query_one("SELECT type, from_username FROM mastodon_notifications WHERE id = '1_78910'")
    assert row is not None
    assert row[0] == "mention"
    assert row[1] == "@notifier@example.com"
    
    # El status asociado también debió guardarse
    status_row = db.query_one("SELECT content FROM mastodon_statuses WHERE id = '1:111213'")
    assert status_row is not None
    assert "Hola" in status_row[0]


def test_local_username_filtering():
    """Verifica que la lógica de filtrado de toots locales (NOT LIKE '%@%@%') distinga correctamente locales de remotos."""
    ejecutar_migraciones()
    
    # Limpiar
    db.execute("DELETE FROM mastodon_statuses")
    
    # Insertar toots locales y remotos
    # En local el formato de username es '@username' (un solo @ al inicio)
    # En remoto es '@username@domain' (dos @)
    db.execute("""
        INSERT INTO mastodon_statuses (id, account_id, status_id, uri, author_username, content, visibility)
        VALUES ('1:100', 1, '100', 'uri1', '@localuser', 'Toot local', 'public')
    """)
    db.execute("""
        INSERT INTO mastodon_statuses (id, account_id, status_id, uri, author_username, content, visibility)
        VALUES ('1:101', 1, '101', 'uri2', '@remoteuser@domain.com', 'Toot remoto', 'public')
    """)
    
    # Comprobar filtrado local
    local_rows = db.query("SELECT id FROM mastodon_statuses WHERE author_username NOT LIKE '%@%@%'")
    assert len(local_rows) == 1
    assert local_rows[0][0] == "1:100"
    
    # Comprobar filtrado remoto
    remote_rows = db.query("SELECT id FROM mastodon_statuses WHERE author_username LIKE '%@%@%'")
    assert len(remote_rows) == 1
    assert remote_rows[0][0] == "1:101"


def test_boost_unpacking():
    """Verifica que el procesamiento de un boost (reblog) desempaquete el original y añada el prefijo."""
    ejecutar_migraciones()
    
    # Mock de un toot reblog
    raw_boost = {
        "id": "111",
        "uri": "http://boost-uri",
        "created_at": "2026-06-13T09:00:00+00:00",
        "visibility": "public",
        "account": {"display_name": "Booster Name", "username": "booster"},
        "sensitive": False,
        "favourited": False,
        "reblogged": False,
        "bookmarked": False,
        "media_attachments": [],
        "reblog": {
            "id": "222",
            "uri": "http://original-uri",
            "content": "Original content from original author",
            "created_at": "2026-06-13T08:00:00+00:00",
            "visibility": "public",
            "account": {"display_name": "Original Name", "username": "original"},
            "sensitive": False,
            "favourited": False,
            "reblogged": False,
            "bookmarked": False,
            "media_attachments": []
        }
    }
    
    from app.services.mastodon.mastodon_service import mastodon_service
    # Procesar
    status_obj = mastodon_service._procesar_y_guardar_toot(1, raw_boost)

    # El impulso es su PROPIA entrada de timeline: el id es el del IMPULSO (1:111), no el del
    # original, para no colisionar con el original ni perderse del timeline.
    assert status_obj.id == "1:111"
    # El contenido debe ser el del toot original, limpio y sin prefijos
    assert status_obj.content == "Original content from original author"
    # La información de quién impulsó (boost) se guarda por separado
    assert status_obj.reblog_by_name == "Booster Name"
    # El autor mostrado es el autor original del toot impulsado
    assert status_obj.author_name == "Original Name"
    # El id del ORIGINAL se guarda en reblog_of_id (para que las acciones lo apunten) y la
    # fecha de la entrada es la del IMPULSO (para ordenarla en su sitio del timeline).
    fila = db.query_one("SELECT reblog_of_id, created_at FROM mastodon_statuses WHERE id = ?", ("1:111",))
    assert fila[0] == "222"
    assert str(fila[1]).startswith("2026-06-13T09:00")


def test_mastodon_timeline_statuses_relation():
    """Verifica que al cargar un timeline se guarden las relaciones en mastodon_timeline_statuses."""
    ejecutar_migraciones()
    
    # Limpiar
    db.execute("DELETE FROM mastodon_timeline_statuses")
    db.execute("DELETE FROM mastodon_statuses")
    
    # Asegurar que existe la cuenta
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (1, 'Test Account', 'mastodon', 1)")
    
    # Insertar status primero
    db.execute("""
        INSERT INTO mastodon_statuses (id, account_id, status_id, uri, author_name, author_username, content, created_at, visibility)
        VALUES ('1:222', 1, '222', 'http://original-uri', 'Original Name', '@original', 'Content', '2026-06-13T08:00:00', 'public')
    """)
    
    # Insertar relación de prueba
    db.execute("""
        INSERT INTO mastodon_timeline_statuses (account_id, timeline, status_db_id)
        VALUES (1, 'home', '1:222')
    """)
    
    # Recuperar usando JOIN como en _cargar_listado_toots
    sql = """
    SELECT s.id, s.content FROM mastodon_statuses s
    JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id
    WHERE t.account_id = ? AND t.timeline = ?
    """
    rows = db.query(sql, (1, "home"))
    assert len(rows) == 1
    assert rows[0][0] == "1:222"
    assert rows[0][1] == "Content"





def test_html_a_texto_estructurado():
    """Verifica que la conversión de HTML a texto preserve estructura y limpie etiquetas."""
    from app.core.html_utils import html_a_texto, html_a_documento

    html = "<p>Primer párrafo</p><ul><li>uno</li><li>dos</li></ul><p>Fin &amp; otro</p>"
    texto = html_a_texto(html)
    # No debe quedar ninguna etiqueta HTML
    assert "<" not in texto and ">" not in texto
    # Debe desescapar entidades
    assert "Fin & otro" in texto
    # Debe conservar los elementos de la lista
    assert "uno" in texto and "dos" in texto
    # Las viñetas se representan como asteriscos
    assert "* uno" in texto

    # Contenido vacío devuelve cadena vacía
    assert html_a_texto("") == ""
    assert html_a_texto(None) == ""

    # El documento HTML debe escapar el título y contener el contenido
    doc = html_a_documento("Mi <título>", "<p>Hola</p>", fuente="Feed", enlace="https://ej.com")
    assert "<!DOCTYPE html>" in doc
    assert "Mi &lt;título&gt;" in doc
    assert "<p>Hola</p>" in doc


def test_telegram_tables_y_servicio_seguro():
    """Las tablas de Telegram existen tras migrar y el servicio es seguro sin Kurigram."""
    ejecutar_migraciones()
    # Las tablas deben existir
    db.execute("SELECT 1 FROM telegram_chats LIMIT 1")
    db.execute("SELECT 1 FROM telegram_messages LIMIT 1")

    from app.services.telegram.telegram_service import telegram_service, HAS_KURIGRAM
    from app.core.errors import TelegramServiceError

    # credenciales_configuradas devuelve un booleano sin fallar
    assert isinstance(telegram_service.credenciales_configuradas(), bool)

    # Si Kurigram no está instalado, las operaciones de red deben fallar con
    # un error claro en vez de romper la aplicación.
    if not HAS_KURIGRAM:
        try:
            telegram_service.cargar_chats(99999)
            assert False, "Debería haber lanzado TelegramServiceError"
        except TelegramServiceError:
            pass


def test_telegram_sent_code_info_con_reenvio():
    """El login conserva destino, siguiente metodo y timeout de SentCode."""
    from app.services.telegram.telegram_service import TelegramService

    class EnumFalso:
        def __init__(self, name):
            self.name = name

    class SentCodeFalso:
        type = EnumFalso("APP")
        next_type = EnumFalso("SMS")
        timeout = 42
        phone_code_hash = "hash-prueba"

    info = TelegramService._info_codigo(SentCodeFalso())

    assert "aplicacion de Telegram" in info["destino"]
    assert info["siguiente_destino"] == "un SMS"
    assert info["timeout"] == 42
    assert info["puede_reenviar"] is True
    assert info["phone_code_hash"] == "hash-prueba"


def test_telegram_normaliza_telefono_internacional():
    """El login normaliza telefonos a formato internacional."""
    from app.services.telegram.telegram_service import TelegramService

    assert TelegramService._normalizar_telefono(" +34 600 112 233 ") == "+34600112233"
    assert TelegramService._normalizar_telefono("0034600112233") == "+34600112233"
    assert TelegramService._telefono_enmascarado("+34600112233").startswith("+34")


def test_orden_servicios_por_defecto():
    """El orden de servicios incluye siempre mastodon, rss y telegram."""
    from app.core.config_manager import config
    config.set("general", "service_order", ["rss"])
    # Reconstruir la lógica de _orden_servicios de forma aislada
    orden = config.get("general", "service_order", ["mastodon", "rss", "telegram"])
    for s in ("mastodon", "rss", "telegram"):
        if s not in orden:
            orden.append(s)
    assert "mastodon" in orden and "rss" in orden and "telegram" in orden


def test_migracion_position_y_servicio_telegram():
    """La migración añade columnas position y registra el servicio telegram."""
    ejecutar_migraciones()
    # Columnas position presentes
    db.execute("SELECT position FROM accounts LIMIT 1")
    db.execute("SELECT position FROM rss_feeds LIMIT 1")
    db.execute("SELECT position FROM rss_categories LIMIT 1")
    # Servicio telegram registrado (evita el FOREIGN KEY al añadir cuenta)
    row = db.query_one("SELECT id FROM services WHERE id = 'telegram'")
    assert row is not None
    # Se puede insertar una cuenta de telegram sin violar la clave foránea
    db.execute("INSERT INTO accounts (name, service_id, active) VALUES ('+34600', 'telegram', 1)")
    db.execute("DELETE FROM accounts WHERE service_id = 'telegram' AND name = '+34600'")


def test_config_telegram_defaults_presentes():
    """La configuracion conserva la seccion telegram al cargar defaults."""
    from app.core.config_manager import DEFAULTS

    assert DEFAULTS["telegram"]["download_dir"] == ""
    assert DEFAULTS["telegram"]["announce_new"] is True


def test_bluesky_migracion_y_servicio():
    """La migración registra el servicio bluesky y crea sus tablas."""
    ejecutar_migraciones()
    assert db.query_one("SELECT id FROM services WHERE id = 'bluesky'") is not None
    for tabla in ("bluesky_posts", "bluesky_timeline_posts", "bluesky_notifications",
                  "bluesky_actors", "bluesky_feeds", "bluesky_convos", "bluesky_messages"):
        db.query(f"SELECT 1 FROM {tabla} LIMIT 1")
    db.query("SELECT 1 FROM bluesky_posts_fts LIMIT 1")
    # Se puede insertar una cuenta de bluesky sin violar la clave foránea
    db.execute("INSERT INTO accounts (name, service_id, active) VALUES ('@x.bsky.social', 'bluesky', 1)")
    db.execute("DELETE FROM accounts WHERE service_id = 'bluesky' AND name = '@x.bsky.social'")


def test_bluesky_parseo_y_fts():
    """El parser guarda un post simulado, extrae URLs e indexa en FTS5."""
    ejecutar_migraciones()
    from app.services.bluesky import bluesky_service

    db.execute("INSERT INTO accounts (name, service_id, active) VALUES ('@t.bsky.social', 'bluesky', 1)")
    acc = db.query_one("SELECT id FROM accounts WHERE name = '@t.bsky.social'")[0]

    class _N:
        def __init__(self, **k):
            self.__dict__.update(k)

    post = _N(uri="at://did:plc:abc/app.bsky.feed.post/xyz", cid="bafy",
              author=_N(did="did:plc:abc", handle="t.bsky.social", display_name="Tester"),
              record=_N(text="Hola red https://example.com", created_at="2026-06-14T10:00:00Z",
                        langs=["es"], labels=None, reply=None),
              embed=None, like_count=5, repost_count=2, reply_count=1, quote_count=0,
              indexed_at="2026-06-14T10:00:01Z", viewer=_N(like=None, repost=None))
    po = bluesky_service._procesar_y_guardar_post(acc, _N(post=post, reason=None))
    assert po is not None
    assert "https://example.com" in po.urls
    assert po.like_count == 5
    assert bluesky_service.buscar_posts_fts(acc, "red")

    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


def test_config_bluesky_defaults_presentes():
    """La configuración incluye la sección bluesky y la registra en service_order."""
    from app.core.config_manager import DEFAULTS

    assert DEFAULTS["bluesky"]["pds_url"] == "https://bsky.social"
    assert "home" in DEFAULTS["bluesky"]["buffer_order"]
    assert "feeds" in DEFAULTS["bluesky"]["buffer_order"]
    assert "drafts" in DEFAULTS["bluesky"]
    assert "bluesky" in DEFAULTS["general"]["service_order"]


def test_bluesky_servicio_perfil_y_cuenta():
    """Existen los métodos de perfil/handle/app passwords y los modelos se construyen."""
    from app.services.bluesky import bluesky_service
    from app.ui.bluesky_views import BlueskyController

    for m in ("obtener_perfil_propio_async", "editar_perfil_async", "cambiar_handle_async",
              "listar_app_passwords_async", "crear_app_password_async", "revocar_app_password_async"):
        assert hasattr(bluesky_service, m)
    for m in ("mi_perfil", "app_passwords", "borradores"):
        assert hasattr(BlueskyController, m)

    # El record de perfil se construye sin red
    from atproto import models
    rec = models.AppBskyActorProfile.Record(
        display_name="N", description="B", avatar=None, banner=None,
        created_at="2026-01-01T00:00:00Z")
    assert rec.display_name == "N"


def test_bluesky_tanda6_avanzado():
    """Hilos, vídeo, interacciones y firehose disponibles; el divisor respeta 300."""
    from app.services.bluesky import bluesky_service
    from app.ui.bluesky_views import BlueskyController

    for m in ("publicar_hilo_async", "publicar_video_async", "limites_video_async",
              "enviar_interaccion_async"):
        assert hasattr(bluesky_service, m)
    assert hasattr(BlueskyController, "nuevo_hilo")

    # El editor de hilos reutiliza el divisor; los segmentos no superan 300
    from app.services.mastodon.mastodon_threading import (
        dividir_texto_recursivo, obtener_regiones_protegidas)
    texto = ("Hilo de prueba para Bluesky con texto repetido. " * 20).strip()
    segs = dividir_texto_recursivo(texto, 300 - 8, obtener_regiones_protegidas(texto))
    assert len(segs) >= 2
    assert all(len(s) <= 300 for s in segs)

    # El helper de firehose se importa de forma segura
    from app.services.bluesky.bluesky_firehose import BlueskyFirehose
    fh = BlueskyFirehose(mi_did="did:plc:x", palabras=["python"])
    assert fh.activo is False


class _FakeList:
    """ListCtrl mínima para probar los cargadores del controlador sin GUI."""
    def __init__(self):
        self.rows = []
        self.cols = 0
    def DeleteAllItems(self): self.rows = []
    def limpiar_lecturas(self): pass
    def reset_focus_col(self): pass
    def GetColumnCount(self): return self.cols
    def DeleteColumn(self, i): self.cols -= 1
    def InsertColumn(self, i, t, width=0): self.cols += 1
    def InsertItem(self, i, t): self.rows.append([t]); return len(self.rows) - 1
    def SetItem(self, p, c, v):
        while len(self.rows[p]) <= c:
            self.rows[p].append("")
        self.rows[p][c] = v
    def establecer_lectura(self, p, t): pass
    def establecer_medios(self, p, e): pass
    def establecer_fecha(self, p, c, iso, rel): pass
    def obtener_lectura(self, p): return ""
    def Freeze(self): pass
    def Thaw(self): pass
    def congelar_diferido(self): pass
    def Focus(self, i): pass
    def EnsureVisible(self, i): pass
    def GetItemCount(self): return len(self.rows)


class _FakeFrame:
    def __init__(self, acc):
        self.active_bluesky_account_id = acc
        self.active_timeline_type = None
        self.lista_items = _FakeList()
        self.lista_items_db_ids = []
        self._modo_lista = ""

    def _restaurar_foco_lista(self):
        pass

    def _construir_columnas_plantilla(self, clave):
        # Simula el helper de MainFrame: construye columnas desde la plantilla.
        from app.core.plantillas import columnas_de_plantilla, definicion
        from app.core.config_manager import config as _cfg
        plantilla = _cfg.get("accessibility", clave, "") or (definicion(clave) or {}).get("default", "")
        cols = columnas_de_plantilla(plantilla) or [("content", "Contenido")]
        while self.lista_items.GetColumnCount() > 0:
            self.lista_items.DeleteColumn(0)
        for i, (c, e) in enumerate(cols):
            self.lista_items.InsertColumn(i, e)
        self._cols_actuales = cols
        return cols

    def _volcar_fila_plantilla(self, fila, cols, valores):
        pos = 0
        for i, (campo, _e) in enumerate(cols):
            val = valores.get(campo, "") or ""
            if i == 0:
                pos = self.lista_items.InsertItem(fila, val)
            else:
                self.lista_items.SetItem(pos, i, val)
        return pos


def test_bluesky_router_y_actores():
    """El router de buffers reconoce prefijos y el cargador de actores funciona."""
    ejecutar_migraciones()
    from app.ui.bluesky_views import BlueskyController

    db.execute("INSERT INTO accounts (name, service_id, active) VALUES ('@r.bsky.social', 'bluesky', 1)")
    acc = db.query_one("SELECT id FROM accounts WHERE name = '@r.bsky.social'")[0]
    db.execute("""INSERT OR REPLACE INTO bluesky_actors
        (id, account_id, bucket, did, handle, display_name, description,
         following_uri, blocking_uri, muted, position)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (f"{acc}:followers:did:plc:x", acc, "followers", "did:plc:x",
         "amigo.bsky.social", "Amigo", "Bio", "", "", 0, 0))

    ctrl = BlueskyController(_FakeFrame(acc))
    # Prefijos de buffers de posts
    assert ctrl._es_buffer_posts("feed:at://x")
    assert ctrl._es_buffer_posts("list:at://y")
    assert ctrl._es_buffer_posts("search:hola")
    assert ctrl._es_buffer_posts("home")
    assert not ctrl._es_buffer_posts("followers")
    # Cargador de actores
    ctrl.f.active_timeline_type = "followers"
    ctrl.cargar_listado()
    assert ctrl.f._modo_lista == "bluesky_actores"
    assert ctrl.f.lista_items.rows[0][0] == "Amigo"
    # El db_id usa '|' como separador (no ':'): el DID contiene ':' (did:plc/did:web).
    assert ctrl.f.lista_items_db_ids[0] == "user:did:plc:x|amigo.bsky.social"
    # Y el parseo recupera el DID COMPLETO (antes 'did:plc:x' se rompía en 'did').
    from app.ui.bluesky_controller_acciones import parse_actor_dbid
    assert parse_actor_dbid(ctrl.f.lista_items_db_ids[0]) == ("did:plc:x", "amigo.bsky.social")

    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


def test_bluesky_convos_loader():
    """El buffer de mensajes directos carga las conversaciones cacheadas."""
    ejecutar_migraciones()
    from app.ui.bluesky_views import BlueskyController

    db.execute("INSERT INTO accounts (name, service_id, active) VALUES ('@c.bsky.social', 'bluesky', 1)")
    acc = db.query_one("SELECT id FROM accounts WHERE name = '@c.bsky.social'")[0]
    db.execute("""INSERT OR REPLACE INTO bluesky_convos
        (id, account_id, convo_id, members_json, title, unread_count, muted,
         last_message, last_message_date, is_group)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f"{acc}:cv1", acc, "cv1", "[]", "Amigo", 2, 0, "Hola", "2026-06-14T10:00:00Z", 0))

    ctrl = BlueskyController(_FakeFrame(acc))
    ctrl.f.active_timeline_type = "convos"
    ctrl.cargar_listado()
    assert ctrl.f._modo_lista == "bluesky_convos"
    assert ctrl.f.lista_items.rows[0][0] == "Amigo"
    assert ctrl.f.lista_items.rows[0][3] == "2"   # no leídos
    assert ctrl.f.lista_items_db_ids[0] == "convo:cv1"

    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


def test_bluesky_bookmarks_buffer_y_reasons():
    """El buffer de marcadores carga posts y los motivos de reporte están mapeados."""
    ejecutar_migraciones()
    from app.ui.bluesky_views import BlueskyController
    from app.services.bluesky import bluesky_service

    # Motivos de reporte válidos (mapeados a constantes ATProto)
    assert set(bluesky_service._REASON_MAP) == {
        "spam", "violation", "misleading", "sexual", "rude", "other"}

    db.execute("INSERT INTO accounts (name, service_id, active) VALUES ('@b.bsky.social', 'bluesky', 1)")
    acc = db.query_one("SELECT id FROM accounts WHERE name = '@b.bsky.social'")[0]

    class _N:
        def __init__(self, **k): self.__dict__.update(k)
    post = _N(uri="at://did:plc:abc/app.bsky.feed.post/bm", cid="c",
              author=_N(did="did:plc:abc", handle="b.bsky.social", display_name="B"),
              record=_N(text="marcame", created_at="2026-06-14T10:00:00Z", langs=["es"], labels=None, reply=None),
              embed=None, like_count=0, repost_count=0, reply_count=0, quote_count=0,
              indexed_at="2026-06-14T10:00:01Z", viewer=_N(like=None, repost=None))
    po = bluesky_service._procesar_y_guardar_post(acc, _N(post=post, reason=None))
    db.execute("INSERT OR IGNORE INTO bluesky_timeline_posts (account_id, timeline, post_db_id) VALUES (?, 'bookmarks', ?)", (acc, po.id))

    ctrl = BlueskyController(_FakeFrame(acc))
    assert ctrl._es_buffer_posts("bookmarks")
    ctrl.f.active_timeline_type = "bookmarks"
    ctrl.cargar_listado()
    assert ctrl.f.lista_items.rows[0][0] == "B"

    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


def test_mastodon_cita_nativa_elimina_quote_inline():
    """Una cita NATIVA de Mastodon (quote aceptado) elimina el bloque <p class="quote-inline">
    'RE: ...' que el servidor añade por compatibilidad, y guarda la cita aparte en quote_json.
    Doc oficial: https://docs.joinmastodon.org/client/quotes/"""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (1, 'Test Account', 'mastodon', 1)")
    raw = {
        "id": "555",
        "uri": "http://quote-test",
        "content": ('<p class="quote-inline">RE: <a href="https://comunidad.nvda.es/@HXeBoLaX/116">'
                    'https://comunidad.nvda.es/@HXeBoLaX/116</a></p>'
                    '<p>Vaya, ha ganado invierno.</p>'),
        "created_at": "2026-06-20T07:56:58+00:00",
        "visibility": "public",
        "account": {"display_name": "Hector", "acct": "hector"},
        "sensitive": False, "favourited": False, "reblogged": False, "bookmarked": False,
        "media_attachments": [],
        "quote": {
            "state": "accepted",
            "quoted_status": {
                "content": "<p>Buenos días. Se de uno que se va de vacaciones.</p>",
                "account": {"display_name": "Hector Benitez", "acct": "HXeBoLaX"},
            },
        },
    }
    from app.services.mastodon.mastodon_service import mastodon_service
    import json
    obj = mastodon_service._procesar_y_guardar_toot(1, raw)
    # El "RE: [@... (publicación)]" redundante NO debe quedar en el contenido visible
    assert "RE:" not in obj.content
    assert "(publicación)" not in obj.content
    assert "Vaya, ha ganado invierno" in obj.content
    # La cita nativa se guarda aparte (la muestra la vista como "Citando a ...")
    fila = db.query_one("SELECT quote_json FROM mastodon_statuses WHERE id = '1:555'")
    qj = json.loads(fila[0])
    assert qj["acct"] == "HXeBoLaX"
    assert "Buenos días" in qj["content"]
    # El helper de formato produce el texto accesible esperado
    from app.ui.ui_helpers import formato_cita
    txt = formato_cita(fila[0])
    assert txt.startswith("Citando a")
    assert "Buenos días" in txt


def test_mastodon_cita_no_aceptada_no_se_muestra():
    """Si el quote no está 'accepted' (p.ej. pending), NO se guarda ni se muestra el contenido
    citado (doc oficial de Quote); sí su estado, para explicar por qué no se ve la cita."""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (1, 'Test Account', 'mastodon', 1)")
    raw = {
        "id": "556", "uri": "http://quote-pending",
        "content": "<p>Texto sin cita visible.</p>",
        "created_at": "2026-06-20T08:00:00+00:00", "visibility": "public",
        "account": {"display_name": "X", "acct": "x"},
        "sensitive": False, "favourited": False, "reblogged": False, "bookmarked": False,
        "media_attachments": [],
        "quote": {"state": "pending", "quoted_status": None},
    }
    from app.services.mastodon.mastodon_service import mastodon_service
    mastodon_service._procesar_y_guardar_toot(1, raw)
    fila = db.query_one("SELECT quote_json FROM mastodon_statuses WHERE id = '1:556'")
    import json as _json
    datos = _json.loads(fila[0])
    assert datos["state"] == "pending"
    assert not datos.get("content") and not datos.get("acct")
    from app.ui.ui_helpers import formato_cita
    assert "pendiente" in formato_cita(fila[0])


def test_mastodon_alineacion_campos_oficiales():
    """El parseo captura los campos oficiales de la entidad Status (edición, respuesta, idioma,
    contadores, fijado, tarjeta, quote_approval) y normaliza los emojis personalizados."""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id,name,service_id,active) VALUES (1,'T','mastodon',1)")
    from app.services.mastodon.mastodon_service import mastodon_service
    raw = {
        "id": "900", "uri": "u", "content": "<p>Hola :blobcat: a todos.</p>",
        "created_at": "2026-06-20T10:00:00Z", "edited_at": "2026-06-20T10:05:00Z",
        "visibility": "public", "language": "es",
        "in_reply_to_id": "899", "in_reply_to_account_id": "7",
        "replies_count": 4, "reblogs_count": 6, "favourites_count": 11, "quotes_count": 1,
        "pinned": True, "emojis": [{"shortcode": "blobcat"}],
        "account": {"display_name": "Aurora :verified:", "acct": "aurora",
                    "emojis": [{"shortcode": "verified"}]},
        "card": {"title": "Titular", "description": "Resumen", "url": "https://e.com"},
        "quote_approval": {"current_user": "automatic"},
        "sensitive": False, "favourited": False, "reblogged": False, "bookmarked": False,
        "media_attachments": [],
    }
    o = mastodon_service._procesar_y_guardar_toot(1, raw)
    assert ":blobcat:" not in o.content and "blobcat" in o.content       # emoji normalizado
    assert ":verified:" not in o.author_name and "verified" in o.author_name
    assert o.edited_at and o.in_reply_to_id == "899" and o.language == "es"
    assert (o.replies_count, o.reblogs_count, o.favourites_count, o.quotes_count) == (4, 6, 11, 1)
    assert o.pinned is True
    fila = db.query_one("SELECT card_json, can_quote FROM mastodon_statuses WHERE id='1:900'")
    import json
    assert json.loads(fila[0])["title"] == "Titular"
    assert fila[1] == 1   # automatic -> puede citar


def test_mastodon_notif_moderation_warning_detalle():
    """El aviso de moderación guarda su texto en extra_json para poder mostrarlo."""
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id,name,service_id,active) VALUES (1,'T','mastodon',1)")
    from app.services.mastodon.mastodon_service import mastodon_service
    raw = {
        "id": "950", "type": "moderation_warning", "created_at": "2026-06-20T11:00:00Z",
        "account": {"display_name": "Mod", "acct": "mod"},
        "moderation_warning": {"action": "disable", "text": "No publiques spam."},
    }
    res = mastodon_service._procesar_y_guardar_notificacion(1, raw)
    import json
    ex = json.loads(res["extra_json"])
    assert ex["moderation_warning"]["text"] == "No publiques spam."
    assert ex["moderation_warning"]["action"] == "disable"


def test_x_cuenta_no_contamina_buffer_activo():
    """REGRESIÓN (Intro no abre enlaces en Mastodon): el helper XController._cuenta() NO debe
    fijar self.f.active_x_account_id. Antes lo hacía como efecto colateral y, cuando el sondeo de
    fondo de X lo llamaba estando en un buffer de Mastodon, dejaba active_x_account_id fijado; el
    siguiente Intro sobre un toot caía en la rama de X de _activar_item_segun_red y no abría el
    enlace hasta cambiar de buffer y volver."""
    ejecutar_migraciones()
    # Hay una cuenta de X activa en la base.
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (5, 'Cuenta X', 'x', 1)")

    from app.ui.x_controller import XController

    class _FakeFrame:
        active_x_account_id = None  # estamos en OTRA red (p. ej. Mastodon)

    f = _FakeFrame()
    ctrl = XController(f)
    acc = ctrl._cuenta()

    # Resuelve la cuenta para uso local del llamador...
    assert acc == 5
    # ...pero NO envenena el buffer activo: sigue siendo None (seguimos "en Mastodon").
    assert f.active_x_account_id is None


def test_matrix_media_crypto_roundtrip():
    """REGRESIÓN (nota de voz / adjunto en sala cifrada): valida que el formato JSON de claves que
    guardamos (key/iv/hashes) es el que espera decrypt_attachment de nio, cifrando y descifrando un
    adjunto de ida y vuelta con las propias funciones de la librería."""
    import json
    from nio.crypto import attachments

    original = b"nota de voz de prueba \x00\x01\x02 con bytes binarios"
    ciphertext, claves = attachments.encrypt_attachment(original)

    # Guardamos EXACTAMENTE como en enviar_archivo / crypto_de_media.
    media_crypto = json.dumps({"key": claves["key"], "iv": claves["iv"],
                               "hashes": claves["hashes"]})

    # Y desciframos EXACTAMENTE como en descargar_media.
    info = json.loads(media_crypto)
    descifrado = attachments.decrypt_attachment(
        ciphertext, info["key"]["k"], info["hashes"]["sha256"], info["iv"])

    assert descifrado == original


def test_matrix_store_sqlite_sin_ficheros_con_dospuntos(tmp_path):
    """REGRESIÓN ([WinError 123] al enviar en sala cifrada): con store=SqliteStore, nio guarda el
    estado de confianza en la BD (tabla DeviceTrustState), SIN crear los ficheros de texto
    '{user_id}_{device_id}.trusted_devices' cuyo nombre lleva ':' y Windows rechaza."""
    from nio.store import SqliteStore
    user_id = "@xebolax:nvda.es"
    device_id = "ABCDEF"
    store = SqliteStore(user_id, device_id, str(tmp_path), "", "claves_e2ee.db")
    # No debe haber creado NINGÚN fichero cuyo nombre contenga ':' (inválido en Windows).
    hijos = [p.name for p in tmp_path.iterdir()]
    assert not any(":" in n for n in hijos), f"nio creó ficheros con ':': {hijos}"
    # El trust vive en la BD, no en KeyStore de texto: no hay atributos trust_db/blacklist_db.
    assert not hasattr(store, "trust_db")
