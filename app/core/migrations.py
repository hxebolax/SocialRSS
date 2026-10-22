# -*- coding: utf-8 -*-
"""
Módulo para el control de versiones y migraciones de la base de datos SQLite.
Inicializa el esquema de tablas en la primera ejecución.
"""

import logging
from .database import db

log = logging.getLogger(__name__)

from .migrations_schema import SCHEMA_V1, FTS5_SCHEMAS


def ejecutar_migraciones():
    """Ejecuta las migraciones incrementales basadas en user_version."""
    conn = db.get_connection()
    
    # Obtener versión actual de la base de datos
    cursor = conn.cursor()
    cursor.execute("PRAGMA user_version")
    version = cursor.fetchone()[0]
    
    log.info(f"Versión actual de base de datos SQLite: {version}")
    
    if version < 1:
        log.info("Aplicando esquema de Base de Datos inicial (versión 1)...")
        # Ejecutar sentencias del esquema base. Son todas CREATE TABLE IF NOT EXISTS, así que se
        # pueden re-ejecutar sin daño sobre una base IMPORTADA con esquema antiguo (retro-
        # compatibilidad). Si una sentencia concreta fallara, se registra y se SIGUE con las demás:
        # nunca se aborta el arranque por aquí (antes un fallo aislado dejaba la app sin abrir tras
        # importar una copia antigua).
        for sql in SCHEMA_V1:
            try:
                db.execute(sql)
            except Exception as e:
                log.error(f"Error al ejecutar SQL en migración v1 (se continúa): {sql[:50]}... Error: {e}")
                
        # Intentar inicializar FTS5
        for sql in FTS5_SCHEMAS:
            try:
                db.execute(sql)
            except Exception as e:
                log.warning(f"Error al crear tablas virtuales FTS5: {e}. Las búsquedas rápidas caerán en modo fallback.")
                
        # Insertar servicios base iniciales
        servicios_iniciales = [
            ("mastodon", "Mastodon", 1),
            ("rss", "Lector RSS", 1),
            ("telegram", "Telegram", 1)
        ]
        try:
            db.execute_many(
                "INSERT OR IGNORE INTO services (id, name, active) VALUES (?, ?, ?)",
                servicios_iniciales
            )
        except Exception as e:
            log.error(f"Error al insertar servicios base: {e}")
            
        # Actualizar la versión de la base de datos
        db.execute("PRAGMA user_version = 1")
        log.info("Base de datos actualizada a la versión 1.")
        
    # Migraciones dinámicas para añadir urls_json a bases de datos existentes
    try:
        db.execute("SELECT urls_json FROM mastodon_statuses LIMIT 1")
    except Exception:
        log.info("Añadiendo columna urls_json a mastodon_statuses...")
        try:
            db.execute("ALTER TABLE mastodon_statuses ADD COLUMN urls_json TEXT")
        except Exception as e:
            log.error(f"Error al añadir columna urls_json a mastodon_statuses: {e}")
            
    try:
        db.execute("SELECT urls_json FROM rss_articles LIMIT 1")
    except Exception:
        log.info("Añadiendo columna urls_json a rss_articles...")
        try:
            db.execute("ALTER TABLE rss_articles ADD COLUMN urls_json TEXT")
        except Exception as e:
            log.error(f"Error al añadir columna urls_json a rss_articles: {e}")

    # Migración dinámica: columna 'has_poll' (la publicación incluye una encuesta)
    try:
        db.execute("SELECT has_poll FROM mastodon_statuses LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE mastodon_statuses ADD COLUMN has_poll INTEGER DEFAULT 0")
        except Exception as e:
            log.error(f"Error al añadir columna has_poll: {e}")

    # Migración dinámica: columna 'application' (cliente desde el que se publicó)
    try:
        db.execute("SELECT application FROM mastodon_statuses LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE mastodon_statuses ADD COLUMN application TEXT")
        except Exception as e:
            log.error(f"Error al añadir columna application: {e}")

    # Migración dinámica: columna 'mentions_json' (todas las cuentas mencionadas en el toot,
    # para poder responder a TODA la conversación, no solo al autor).
    try:
        db.execute("SELECT mentions_json FROM mastodon_statuses LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE mastodon_statuses ADD COLUMN mentions_json TEXT")
        except Exception as e:
            log.error(f"Error al añadir columna mentions_json: {e}")

    # Migración dinámica: columna 'reblog_of_id' (id del toot ORIGINAL cuando la fila es un
    # impulso/boost). El boost se guarda como su propia entrada —con el id y la fecha del
    # impulso— para no colisionar con el original ni perderse del timeline; este campo guarda
    # el id del original para que las acciones (responder, favorito, boost...) lo apunten.
    try:
        db.execute("SELECT reblog_of_id FROM mastodon_statuses LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE mastodon_statuses ADD COLUMN reblog_of_id TEXT")
        except Exception as e:
            log.error(f"Error al añadir columna reblog_of_id: {e}")

    # Migración dinámica: columna 'quote_json' (cita NATIVA de Mastodon: autor y texto del
    # toot citado, para mostrarlo como "Citando a @usuario: ...").
    try:
        db.execute("SELECT quote_json FROM mastodon_statuses LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE mastodon_statuses ADD COLUMN quote_json TEXT")
        except Exception as e:
            log.error(f"Error al añadir columna quote_json: {e}")

    # Migración dinámica: columnas para los mensajes directos (buffer 'direct'). Cada fila de
    # ese buffer es el último mensaje de una conversación; se guarda el id de la conversación
    # (para marcarla leída) y si está sin leer. Y 'filtered_by': título del filtro del servidor
    # (modo "advertir") que coincide con el toot, para avisar al mostrarlo.
    for columna, tipo in (("conversation_id", "TEXT"), ("conversation_unread", "INTEGER DEFAULT 0"),
                          ("filtered_by", "TEXT")):
        try:
            db.execute(f"SELECT {columna} FROM mastodon_statuses LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE mastodon_statuses ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.error(f"Error al añadir columna {columna}: {e}")

    # Migración dinámica: columnas para ALINEAR con la entidad Status oficial de Mastodon
    # (edición, respuesta, idioma, contadores, fijado y tarjeta de enlace). Permiten mostrar
    # "editado", "en respuesta a", la encuesta, los contadores, etc. en la lista.
    for columna, tipo in (
        ("edited_at", "TEXT"),
        ("in_reply_to_id", "TEXT"),
        ("in_reply_to_account_id", "TEXT"),
        ("language", "TEXT"),
        ("replies_count", "INTEGER DEFAULT 0"),
        ("reblogs_count", "INTEGER DEFAULT 0"),
        ("favourites_count", "INTEGER DEFAULT 0"),
        ("quotes_count", "INTEGER DEFAULT 0"),
        ("pinned", "INTEGER DEFAULT 0"),
        ("card_json", "TEXT"),
        ("can_quote", "INTEGER"),
    ):
        try:
            db.execute(f"SELECT {columna} FROM mastodon_statuses LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE mastodon_statuses ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.error(f"Error al añadir columna {columna} a mastodon_statuses: {e}")

    # Migración dinámica: columna 'extra_json' en notificaciones, para guardar el detalle de los
    # tipos especiales (aviso de moderación con su texto, relaciones cortadas, informe de admin).
    try:
        db.execute("SELECT extra_json FROM mastodon_notifications LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE mastodon_notifications ADD COLUMN extra_json TEXT")
        except Exception as e:
            log.error(f"Error al añadir columna extra_json a mastodon_notifications: {e}")

    # Limpieza de los impulsos guardados con el FORMATO ANTIGUO (con el id y la fecha del
    # original, y sin reblog_of_id): quedaban enterrados en la fecha vieja del original o
    # colisionaban con él, "perdiéndose" del timeline. Se borran para que se vuelvan a
    # descargar como su propia entrada (id y fecha del impulso) en el próximo refresco. Un
    # impulso es exactamente una fila con reblog_by_name pero sin reblog_of_id; los toots
    # normales tienen reblog_by_name vacío, así que esto NO toca publicaciones normales.
    try:
        db.execute(
            "DELETE FROM mastodon_timeline_statuses WHERE status_db_id IN "
            "(SELECT id FROM mastodon_statuses WHERE COALESCE(reblog_by_name,'') != '' AND reblog_of_id IS NULL)")
        db.execute(
            "DELETE FROM mastodon_statuses WHERE COALESCE(reblog_by_name,'') != '' AND reblog_of_id IS NULL")
    except Exception as e:
        log.debug(f"Limpieza de impulsos en formato antiguo: {e}")

    # Migración dinámica: columnas para almacenar el autor del boost por separado
    for columna in ("reblog_by_name", "reblog_by_username"):
        try:
            db.execute(f"SELECT {columna} FROM mastodon_statuses LIMIT 1")
        except Exception:
            log.info(f"Añadiendo columna {columna} a mastodon_statuses...")
            try:
                db.execute(f"ALTER TABLE mastodon_statuses ADD COLUMN {columna} TEXT")
            except Exception as e:
                log.error(f"Error al añadir columna {columna}: {e}")

    # Migración dinámica para crear la tabla mastodon_timeline_statuses en base de datos existente
    try:
        db.execute("SELECT 1 FROM mastodon_timeline_statuses LIMIT 1")
    except Exception:
        log.info("Creando tabla mastodon_timeline_statuses en base de datos existente...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS mastodon_timeline_statuses (
                account_id INTEGER NOT NULL,
                timeline TEXT NOT NULL,
                status_db_id TEXT NOT NULL,
                PRIMARY KEY(account_id, timeline, status_db_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY(status_db_id) REFERENCES mastodon_statuses(id) ON DELETE CASCADE
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla mastodon_timeline_statuses: {e}")

    # Migración dinámica: snapshot de seguidores de Mastodon para detectar quién te ha dejado de
    # seguir (Mastodon no avisa de los "unfollow"; se comparan las listas). 'meta' guarda cuándo se
    # tomó el último snapshot y sirve para distinguir "primera vez" de "cuenta con 0 seguidores".
    try:
        db.execute("SELECT 1 FROM mastodon_seguidores_snapshot LIMIT 1")
    except Exception:
        log.info("Creando tabla mastodon_seguidores_snapshot en base de datos existente...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS mastodon_seguidores_snapshot (
                account_id INTEGER NOT NULL,
                follower_id TEXT NOT NULL,
                acct TEXT,
                display_name TEXT,
                PRIMARY KEY(account_id, follower_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla mastodon_seguidores_snapshot: {e}")
    try:
        db.execute("SELECT 1 FROM mastodon_seguidores_meta LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS mastodon_seguidores_meta (
                account_id INTEGER PRIMARY KEY,
                ultima TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla mastodon_seguidores_meta: {e}")

    # Migración dinámica: columna 'position' para ordenar cuentas, feeds y
    # categorías a gusto del usuario (Ctrl+Arriba/Abajo en el árbol).
    for tabla in ("accounts", "rss_feeds", "rss_categories"):
        try:
            db.execute(f"SELECT position FROM {tabla} LIMIT 1")
        except Exception:
            log.info(f"Añadiendo columna position a {tabla}...")
            try:
                db.execute(f"ALTER TABLE {tabla} ADD COLUMN position INTEGER DEFAULT 0")
                # Inicializar position con el id para conservar el orden actual
                db.execute(f"UPDATE {tabla} SET position = id WHERE position = 0 OR position IS NULL")
            except Exception as e:
                log.error(f"Error al añadir columna position a {tabla}: {e}")

    # Asegurar que el servicio 'telegram' existe en la tabla services (necesario
    # por la clave foránea de accounts.service_id). Sin esto, añadir una cuenta de
    # Telegram falla con "FOREIGN KEY constraint failed".
    try:
        db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('telegram', 'Telegram', 1)")
    except Exception as e:
        log.error(f"Error al asegurar el servicio Telegram: {e}")

    # Migración dinámica: tabla de contactos de Telegram (caché para evitar FLOOD_WAIT)
    try:
        db.execute("SELECT 1 FROM telegram_contacts LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS telegram_contacts (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                nombre TEXT,
                username TEXT,
                telefono TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla telegram_contacts: {e}")

    # Migración dinámica: tabla de carpetas de Telegram
    try:
        db.execute("SELECT 1 FROM telegram_folders LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS telegram_folders (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                folder_id INTEGER NOT NULL,
                title TEXT,
                chat_ids_json TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla telegram_folders: {e}")

    # Migración dinámica: tabla de historial de IA en bases de datos existentes
    try:
        db.execute("SELECT 1 FROM ai_history LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS ai_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funcion TEXT NOT NULL,
                entrada TEXT,
                respuesta TEXT,
                created_at TEXT NOT NULL
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla ai_history: {e}")

    # Migración dinámica: columna last_media_type en telegram_chats
    try:
        db.execute("SELECT last_media_type FROM telegram_chats LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE telegram_chats ADD COLUMN last_media_type TEXT")
        except Exception as e:
            log.debug(f"No se pudo añadir last_media_type a telegram_chats: {e}")

    # Migración dinámica: columna blocked en telegram_chats (estado de bloqueo del usuario/bot,
    # para que el menú alterne Bloquear/Desbloquear según el estado real).
    try:
        db.execute("SELECT blocked FROM telegram_chats LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE telegram_chats ADD COLUMN blocked INTEGER DEFAULT 0")
        except Exception as e:
            log.debug(f"No se pudo añadir blocked a telegram_chats: {e}")

    # Migración dinámica: columna protected en telegram_chats (contenido protegido / noforwards, para
    # NO ofrecer copiar/reenviar mensajes de chats donde Telegram lo prohíbe).
    try:
        db.execute("SELECT protected FROM telegram_chats LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE telegram_chats ADD COLUMN protected INTEGER DEFAULT 0")
        except Exception as e:
            log.debug(f"No se pudo añadir protected a telegram_chats: {e}")

    # Migración dinámica: columnas duration/file_name/sender_username en telegram_messages existentes.
    # sender_username guarda el @usuario aparte del nombre mostrado, para que la plantilla de mensajes
    # pueda mostrar el nombre (o el contacto renombrado) en {author} y el @usuario en {username}.
    for columna, tipo in (("duration", "REAL DEFAULT 0"), ("file_name", "TEXT"),
                          ("sender_username", "TEXT"),
                          ("forward_from", "TEXT"), ("views", "INTEGER DEFAULT 0"),
                          ("media_group_id", "INTEGER"), ("reply_markup", "TEXT"),
                          ("reactions", "TEXT"), ("thread_id", "INTEGER"),
                          ("urls", "TEXT")):
        try:
            db.execute(f"SELECT {columna} FROM telegram_messages LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE telegram_messages ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.debug(f"No se pudo añadir columna {columna} a telegram_messages: {e}")

    # Migración dinámica: tablas de Telegram en bases de datos existentes
    try:
        db.execute("SELECT 1 FROM telegram_chats LIMIT 1")
    except Exception:
        log.info("Creando tablas de Telegram en base de datos existente...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS telegram_chats (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT,
                chat_type TEXT,
                username TEXT,
                unread_count INTEGER DEFAULT 0,
                last_message TEXT,
                last_message_date TEXT,
                pinned INTEGER DEFAULT 0,
                muted INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_chats_acc ON telegram_chats(account_id)")
            db.execute("""
            CREATE TABLE IF NOT EXISTS telegram_messages (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                sender_name TEXT,
                sender_id INTEGER,
                text TEXT,
                media_type TEXT,
                media_path TEXT,
                date TEXT,
                outgoing INTEGER DEFAULT 0,
                reply_to INTEGER,
                edited INTEGER DEFAULT 0,
                media_group_id INTEGER,
                reply_markup TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_messages_chat ON telegram_messages(account_id, chat_id)")
        except Exception as e:
            log.error(f"Error al crear tablas de Telegram: {e}")

    # Migración dinámica: columna 'read_later' (Leer más tarde) en rss_articles
    try:
        db.execute("SELECT read_later FROM rss_articles LIMIT 1")
    except Exception:
        log.info("Añadiendo columna read_later a rss_articles...")
        try:
            db.execute("ALTER TABLE rss_articles ADD COLUMN read_later INTEGER DEFAULT 0")
        except Exception as e:
            log.error(f"Error al añadir columna read_later: {e}")

    # Migración dinámica: columnas 'pinned' (fijado) y 'paused' (en pausa) en rss_feeds
    for columna in ("pinned", "paused"):
        try:
            db.execute(f"SELECT {columna} FROM rss_feeds LIMIT 1")
        except Exception:
            log.info(f"Añadiendo columna {columna} a rss_feeds...")
            try:
                db.execute(f"ALTER TABLE rss_feeds ADD COLUMN {columna} INTEGER DEFAULT 0")
            except Exception as e:
                log.error(f"Error al añadir columna {columna} a rss_feeds: {e}")

    # Migración dinámica: ruta local de descarga de un episodio de podcast (escucha offline)
    try:
        db.execute("SELECT download_path FROM rss_articles LIMIT 1")
    except Exception:
        log.info("Añadiendo columna download_path a rss_articles (descargas de podcast)...")
        try:
            db.execute("ALTER TABLE rss_articles ADD COLUMN download_path TEXT")
        except Exception as e:
            log.error(f"Error al añadir download_path a rss_articles: {e}")

    # Migración dinámica: configuración del web scraper (JSON con el selector) en rss_feeds
    try:
        db.execute("SELECT scrape_config FROM rss_feeds LIMIT 1")
    except Exception:
        log.info("Añadiendo columna scrape_config a rss_feeds (web scraper)...")
        try:
            db.execute("ALTER TABLE rss_feeds ADD COLUMN scrape_config TEXT")
        except Exception as e:
            log.error(f"Error al añadir scrape_config a rss_feeds: {e}")

    # Índice para acelerar los buffers de destacados y leer más tarde
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_rss_articles_favorite ON rss_articles(favorite)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_rss_articles_read_later ON rss_articles(read_later)")
    except Exception as e:
        log.debug(f"No se pudieron crear índices de favorite/read_later: {e}")

    # Migración dinámica: categorías anidadas (parent_id en rss_categories)
    try:
        db.execute("SELECT parent_id FROM rss_categories LIMIT 1")
    except Exception:
        log.info("Añadiendo columna parent_id a rss_categories (carpetas anidadas)...")
        try:
            db.execute("ALTER TABLE rss_categories ADD COLUMN parent_id INTEGER")
        except Exception as e:
            log.error(f"Error al añadir parent_id a rss_categories: {e}")

    # Migración dinámica: papelera de artículos (deleted + deleted_at)
    for columna, tipo in (("deleted", "INTEGER DEFAULT 0"), ("deleted_at", "TEXT")):
        try:
            db.execute(f"SELECT {columna} FROM rss_articles LIMIT 1")
        except Exception:
            log.info(f"Añadiendo columna {columna} a rss_articles (papelera)...")
            try:
                db.execute(f"ALTER TABLE rss_articles ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.error(f"Error al añadir columna {columna}: {e}")

    # Migración dinámica: registro de lectura de artículos (historial + estadísticas)
    try:
        db.execute("SELECT 1 FROM rss_reading_log LIMIT 1")
    except Exception:
        log.info("Creando tabla rss_reading_log (historial y estadísticas de lectura)...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS rss_reading_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                article_db_id TEXT NOT NULL,   -- rss_articles.id del artículo leído
                feed_id INTEGER,
                title TEXT,                    -- título cacheado (por si el artículo se borra luego)
                feed_title TEXT,
                opened_at TEXT NOT NULL        -- ISO de cuando se abrió
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_reading_log_when ON rss_reading_log(opened_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_reading_log_article ON rss_reading_log(article_db_id)")
        except Exception as e:
            log.error(f"Error al crear tabla rss_reading_log: {e}")

    # Migración dinámica: tabla unificada de filtros de contenido (Mastodon + RSS)
    try:
        db.execute("SELECT 1 FROM content_filters LIMIT 1")
    except Exception:
        log.info("Creando tabla content_filters (filtros unificados)...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS content_filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL DEFAULT 'all',   -- 'mastodon', 'rss' o 'all'
                name TEXT,
                pattern TEXT NOT NULL,
                is_regex INTEGER DEFAULT 0,
                field TEXT DEFAULT 'all',              -- 'text', 'author' o 'all'
                action TEXT NOT NULL DEFAULT 'hide',   -- 'hide', 'mark_read', 'star', 'read_later'
                active INTEGER DEFAULT 1
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla content_filters: {e}")

    # ------------------------------------------------------------------
    # Bluesky / AT Protocol (servicio + tablas). Patrón idempotente.
    # ------------------------------------------------------------------
    # Asegurar el servicio 'bluesky' en services (necesario por la FK de
    # accounts.service_id, igual que con telegram).
    try:
        db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('bluesky', 'Bluesky', 1)")
    except Exception as e:
        log.error(f"Error al asegurar el servicio Bluesky: {e}")

    # Posts de Bluesky
    try:
        db.execute("SELECT 1 FROM bluesky_posts LIMIT 1")
    except Exception:
        log.info("Creando tablas de Bluesky...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_posts (
                id TEXT PRIMARY KEY,              -- "<account_id>:<uri>"
                account_id INTEGER NOT NULL,
                uri TEXT NOT NULL,
                cid TEXT NOT NULL,
                author_did TEXT,
                author_handle TEXT,
                author_name TEXT,
                content TEXT,
                created_at TEXT,
                indexed_at TEXT,
                langs TEXT,
                labels TEXT,
                reply_root_uri TEXT,
                reply_root_cid TEXT,
                reply_parent_uri TEXT,
                reply_parent_cid TEXT,
                quote_uri TEXT,
                like_count INTEGER DEFAULT 0,
                repost_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                quote_count INTEGER DEFAULT 0,
                liked_uri TEXT,
                reposted_uri TEXT,
                bookmarked INTEGER DEFAULT 0,
                media_json TEXT,
                external_json TEXT,
                urls_json TEXT,
                repost_by_name TEXT,
                repost_by_handle TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_bsky_posts_acc ON bluesky_posts(account_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_bsky_posts_indexed ON bluesky_posts(indexed_at)")

            db.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_timeline_posts (
                account_id INTEGER NOT NULL,
                timeline TEXT NOT NULL,
                post_db_id TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                PRIMARY KEY(account_id, timeline, post_db_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY(post_db_id) REFERENCES bluesky_posts(id) ON DELETE CASCADE
            )
            """)

            db.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_notifications (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                uri TEXT NOT NULL,
                cid TEXT,
                reason TEXT NOT NULL,
                reason_subject TEXT,
                subject_uri TEXT,                 -- at:// de la PUBLICACIÓN a la que se refiere
                content TEXT,                     -- texto de esa publicación (para leerlo en la lista)
                author_did TEXT,
                author_handle TEXT,
                author_name TEXT,
                is_read INTEGER DEFAULT 0,
                indexed_at TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_bsky_notif_acc ON bluesky_notifications(account_id)")

            db.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_actors (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                bucket TEXT NOT NULL,
                did TEXT NOT NULL,
                handle TEXT,
                display_name TEXT,
                description TEXT,
                following_uri TEXT,
                blocking_uri TEXT,
                muted INTEGER DEFAULT 0,
                position INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)

            db.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_feeds (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                uri TEXT NOT NULL,
                name TEXT,
                description TEXT,
                creator_handle TEXT,
                pinned INTEGER DEFAULT 0,
                position INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)

            db.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_convos (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                convo_id TEXT NOT NULL,
                members_json TEXT,
                title TEXT,
                unread_count INTEGER DEFAULT 0,
                muted INTEGER DEFAULT 0,
                last_message TEXT,
                last_message_date TEXT,
                is_group INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)

            db.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_messages (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                convo_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender_did TEXT,
                sender_name TEXT,
                text TEXT,
                embed_json TEXT,
                reactions_json TEXT,
                sent_at TEXT,
                outgoing INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_bsky_messages_convo ON bluesky_messages(account_id, convo_id)")
        except Exception as e:
            log.error(f"Error al crear tablas de Bluesky: {e}")

    # FTS5 de posts de Bluesky (búsqueda global)
    try:
        db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS bluesky_posts_fts USING fts5(
            post_db_id UNINDEXED, author_name, author_handle, content,
            tokenize='unicode61 remove_diacritics 2'
        )
        """)
    except Exception as e:
        log.warning(f"No se pudo crear bluesky_posts_fts (búsqueda caerá en modo fallback): {e}")

    # --- Denormalizar account_id en rss_articles (rendimiento de los buffers RSS globales) ---
    # Los buffers RSS globales (Todos, No leídos, Destacados, Leer más tarde, Hoy) filtraban por
    # cuenta a través del JOIN con rss_feeds (f.account_id) y ordenaban por a.published_at, lo que
    # obligaba a SQLite a ordenar con una tabla temporal (~17 ms con miles de artículos). Con
    # account_id en el propio artículo e índices (account_id, published_at DESC) el orden sale del
    # índice (~0,2 ms). Patrón idempotente: si la columna ya existe, no se hace nada.
    try:
        db.execute("SELECT account_id FROM rss_articles LIMIT 1")
    except Exception:
        log.info("Añadiendo columna account_id a rss_articles (rendimiento de listados)...")
        try:
            db.execute("ALTER TABLE rss_articles ADD COLUMN account_id INTEGER")
            # Rellenar desde el feed al que pertenece cada artículo.
            db.execute(
                "UPDATE rss_articles SET account_id = "
                "(SELECT f.account_id FROM rss_feeds f WHERE f.id = rss_articles.feed_id) "
                "WHERE account_id IS NULL")
        except Exception as e:
            log.error(f"Error al añadir/poblar account_id en rss_articles: {e}")

    # Reparar filas con account_id sin poblar (p. ej. artículos insertados antes de esta migración
    # por una versión intermedia, o feeds que cambiaron de cuenta). Barato e idempotente.
    try:
        db.execute(
            "UPDATE rss_articles SET account_id = "
            "(SELECT f.account_id FROM rss_feeds f WHERE f.id = rss_articles.feed_id) "
            "WHERE account_id IS NULL")
    except Exception as e:
        log.debug(f"No se pudo reparar account_id de rss_articles: {e}")

    # --- Índices de rendimiento (Fase 5 del análisis) ---
    # Aceleran las consultas calientes de las listas y los sondeos: las uniones de los
    # timelines (que NO tenían índice) y los "ORDER BY fecha DESC LIMIT" (índices
    # compuestos cuenta+fecha para que el orden salga del índice, sin ordenar a mano).
    # Idempotentes (IF NOT EXISTS): se aplican una vez y no estorban en reinicios.
    # Nota: las tablas de unión de timelines (mastodon_timeline_statuses y
    # bluesky_timeline_posts) tienen PRIMARY KEY(account_id, timeline, ...), cuyo
    # autoíndice ya sirve el filtro por (cuenta, timeline) del join; no se añaden
    # índices ahí para no encarecer sus inserciones (son tablas muy escritas).
    _indices_rendimiento = [
        # Mastodon: status_id acelera el LEFT JOIN de las notificaciones con los toots.
        "CREATE INDEX IF NOT EXISTS idx_mst_statusid ON mastodon_statuses(status_id)",
        # Notificaciones de Mastodon: filtro por cuenta + orden por fecha.
        "CREATE INDEX IF NOT EXISTS idx_mnotif_acc_created ON mastodon_notifications(account_id, created_at)",
        # Notificaciones de Bluesky: filtro por cuenta + orden por fecha.
        "CREATE INDEX IF NOT EXISTS idx_bnotif_acc_idx ON bluesky_notifications(account_id, indexed_at)",
        # Conversaciones de Bluesky (DMs): filtro por cuenta + orden por fecha.
        "CREATE INDEX IF NOT EXISTS idx_bconvo_acc_date ON bluesky_convos(account_id, last_message_date)",
        # Chats de Telegram: filtro por cuenta + orden por fecha del último mensaje.
        "CREATE INDEX IF NOT EXISTS idx_tgchats_acc_date ON telegram_chats(account_id, last_message_date)",
        # Mensajes de Telegram: al abrir un chat se filtra por (account_id, chat_id) y se ordena por
        # message_id; con este índice el orden sale del índice (sin tabla temporal de ORDER BY).
        "CREATE INDEX IF NOT EXISTS idx_tgmsg_chat_msgid ON telegram_messages(account_id, chat_id, message_id)",
        # Variante por fecha: recorte/poda (PARTITION BY chat_id ORDER BY date) y búsqueda por fecha.
        "CREATE INDEX IF NOT EXISTS idx_tgmsg_chat_date ON telegram_messages(account_id, chat_id, date)",
        # Feeds de RSS: filtro por cuenta (join con artículos); faltaba este índice.
        "CREATE INDEX IF NOT EXISTS idx_rssfeeds_acc ON rss_feeds(account_id)",
        # Artículos RSS por feed + fecha (buffer de un feed concreto, ordenado).
        "CREATE INDEX IF NOT EXISTS idx_rssart_feed_pub ON rss_articles(feed_id, published_at)",
        # Artículos RSS por cuenta + fecha: orden directo desde el índice en los buffers globales
        # (Todos / Hoy). Evita la tabla temporal de ORDER BY al filtrar por cuenta.
        "CREATE INDEX IF NOT EXISTS idx_rssart_acc_pub ON rss_articles(account_id, published_at)",
        # Variantes con la bandera del buffer al frente: No leídos, Destacados y Leer más tarde
        # filtran por una bandera + cuenta y ordenan por fecha; el índice cubre filtro y orden.
        "CREATE INDEX IF NOT EXISTS idx_rssart_acc_read_pub ON rss_articles(account_id, read, published_at)",
        "CREATE INDEX IF NOT EXISTS idx_rssart_acc_fav_pub ON rss_articles(account_id, favorite, published_at)",
        "CREATE INDEX IF NOT EXISTS idx_rssart_acc_later_pub ON rss_articles(account_id, read_later, published_at)",
    ]
    for _idx_sql in _indices_rendimiento:
        try:
            db.execute(_idx_sql)
        except Exception as e:
            log.debug(f"No se pudo crear índice de rendimiento ({_idx_sql[:60]}...): {e}")

    # ------------------------------------------------------------------
    # RADIO por internet (directorio RadioBrowser + favoritos + historial).
    # Patrón idempotente, igual que el resto.
    # ------------------------------------------------------------------
    try:
        db.execute("SELECT 1 FROM radio_categories LIMIT 1")
    except Exception:
        log.info("Creando tablas de radio...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS radio_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                created_at TEXT
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla radio_categories: {e}")
    try:
        db.execute("SELECT 1 FROM radio_favorites LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS radio_favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                station_uuid TEXT,
                category_id INTEGER,
                position INTEGER DEFAULT 0,
                play_count INTEGER DEFAULT 0,
                last_played TEXT,
                created_at TEXT
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla radio_favorites: {e}")
    try:
        db.execute("SELECT 1 FROM radio_history LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS radio_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                station_uuid TEXT,
                played_at TEXT NOT NULL
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla radio_history: {e}")

    # ------------------------------------------------------------------
    # RSS: podcasts y feeds de YouTube. Tipo de feed y datos del enclosure.
    # ------------------------------------------------------------------
    try:
        db.execute("SELECT feed_kind FROM rss_feeds LIMIT 1")
    except Exception:
        log.info("Añadiendo columna feed_kind a rss_feeds (rss/podcast/youtube)...")
        try:
            db.execute("ALTER TABLE rss_feeds ADD COLUMN feed_kind TEXT DEFAULT 'rss'")
        except Exception as e:
            log.error(f"Error al añadir feed_kind: {e}")
    # Ajustes por feed: intervalo propio, user-agent propio, no descargar imágenes.
    for columna, tipo in (("custom_interval", "INTEGER DEFAULT 0"),
                          ("custom_user_agent", "TEXT"),
                          ("no_images", "INTEGER DEFAULT 0"),
                          # Descargar SIEMPRE el texto completo de este feed (aunque la opción
                          # global esté apagada): útil para feeds que solo traen el resumen.
                          ("force_fulltext", "INTEGER DEFAULT 0"),
                          # Caché HTTP por feed (peticiones condicionales): si el servidor responde
                          # 304 No Modificado, NO se vuelve a descargar ni a parsear el feed. Evita
                          # los tirones de las RÁFAGAS de actualización con muchos feeds (feedparser
                          # es muy pesado y satura el GIL si se reparsea todo aunque no haya cambiado).
                          ("http_etag", "TEXT"),
                          ("http_modified", "TEXT"),
                          # Fecha de corte de importación (ISO). Si tiene valor, al descargar el feed
                          # NO se importan artículos publicados ANTES de esta fecha. Se pone al limpiar
                          # los datos de RSS (si el usuario activó "solo nuevos a partir de la limpieza")
                          # para no volver a bajar todo el histórico. NULL = sin corte (comportamiento
                          # normal, se importa lo que haya). Los feeds nuevos nacen sin corte.
                          ("import_cutoff", "TEXT")):
        try:
            db.execute(f"SELECT {columna} FROM rss_feeds LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE rss_feeds ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.error(f"Error al añadir columna {columna} a rss_feeds: {e}")
    # Datos del adjunto de audio/vídeo de cada artículo (podcasts/YouTube).
    for columna, tipo in (("enclosure_url", "TEXT"), ("enclosure_type", "TEXT"),
                          ("enclosure_length", "INTEGER DEFAULT 0"), ("is_podcast", "INTEGER DEFAULT 0")):
        try:
            db.execute(f"SELECT {columna} FROM rss_articles LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE rss_articles ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.error(f"Error al añadir columna {columna} a rss_articles: {e}")

    # ------------------------------------------------------------------
    # RSS: alertas por palabra clave y búsquedas guardadas.
    # ------------------------------------------------------------------
    try:
        db.execute("SELECT 1 FROM rss_keyword_alerts LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS rss_keyword_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                is_regex INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla rss_keyword_alerts: {e}")
    try:
        db.execute("SELECT 1 FROM rss_saved_searches LIMIT 1")
    except Exception:
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS rss_saved_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                query TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                created_at TEXT
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tabla rss_saved_searches: {e}")

    # ------------------------------------------------------------------
    # X (Twitter) [CLIENTE NO OFICIAL]. Servicio + tablas de caché. Patrón
    # idempotente. Si el cliente de X se desactiva (feature_flags), estas tablas
    # simplemente no se usan; no estorban.
    # ------------------------------------------------------------------
    try:
        db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('x', 'X (Twitter)', 1)")
    except Exception as e:
        log.error(f"Error al asegurar el servicio X: {e}")
    try:
        db.execute("SELECT 1 FROM x_tweets LIMIT 1")
    except Exception:
        log.info("Creando tablas de X (Twitter)...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_tweets (
                id TEXT PRIMARY KEY,               -- account_id:tweet_id
                account_id INTEGER NOT NULL,
                tweet_id TEXT NOT NULL,
                author_id TEXT,
                author_name TEXT,
                author_screen_name TEXT,
                text TEXT,
                created_at TEXT,
                lang TEXT,
                liked INTEGER DEFAULT 0,
                retweeted INTEGER DEFAULT 0,
                bookmarked INTEGER DEFAULT 0,
                favorite_count INTEGER DEFAULT 0,
                retweet_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                quote_count INTEGER DEFAULT 0,
                view_count INTEGER DEFAULT 0,
                in_reply_to TEXT,
                conversation_id TEXT,
                quoted_id TEXT,
                retweeted_by TEXT,
                media_json TEXT,
                urls_json TEXT,
                is_own INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_x_tweets_acc ON x_tweets(account_id)")
            # Mapeo tweet <-> buffer (Inicio, Notificaciones, Menciones, Lista:ID, etc.)
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_timeline_tweets (
                account_id INTEGER NOT NULL,
                timeline TEXT NOT NULL,
                tweet_db_id TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                PRIMARY KEY(account_id, timeline, tweet_db_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_users (
                id TEXT PRIMARY KEY,               -- account_id:user_id
                account_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                screen_name TEXT,
                name TEXT,
                description TEXT,
                followers INTEGER DEFAULT 0,
                following INTEGER DEFAULT 0,
                statuses INTEGER DEFAULT 0,
                is_following INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                is_muted INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_notifications (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                tipo TEXT,
                actor_name TEXT,
                actor_screen_name TEXT,
                text TEXT,
                tweet_id TEXT,
                created_at TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_lists (
                id TEXT PRIMARY KEY,               -- account_id:list_id
                account_id INTEGER NOT NULL,
                list_id TEXT NOT NULL,
                name TEXT,
                description TEXT,
                member_count INTEGER DEFAULT 0,
                position INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_convos (
                id TEXT PRIMARY KEY,               -- account_id:convo_id
                account_id INTEGER NOT NULL,
                convo_id TEXT NOT NULL,
                title TEXT,
                last_message TEXT,
                last_message_date TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_messages (
                id TEXT PRIMARY KEY,               -- account_id:convo_id:message_id
                account_id INTEGER NOT NULL,
                convo_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                text TEXT,
                date TEXT,
                outgoing INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                name TEXT,
                query TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            # Cronologías de usuario abiertas como buffer (las publicaciones de un @usuario).
            # modo: 'tweets' | 'replies' | 'media' (qué pestaña de su perfil se muestra).
            db.execute("""
            CREATE TABLE IF NOT EXISTS x_user_timelines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                screen_name TEXT,
                name TEXT,
                modo TEXT DEFAULT 'tweets',
                position INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
        except Exception as e:
            log.error(f"Error al crear tablas de X: {e}")

    # X: tabla de CRONOLOGÍAS DE USUARIO (buffers con las publicaciones de un @usuario). Se crea
    # aquí, FUERA del bloque de "primera instalación de X" (que solo corre si no existe x_tweets),
    # para que también aparezca en instalaciones que YA tenían X de versiones anteriores. Idempotente.
    try:
        db.execute("""
        CREATE TABLE IF NOT EXISTS x_user_timelines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            screen_name TEXT,
            name TEXT,
            modo TEXT DEFAULT 'tweets',
            position INTEGER DEFAULT 0,
            FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
        )
        """)
    except Exception as e:
        log.debug(f"No se pudo crear x_user_timelines: {e}")

    # X: columnas para los buffers de personas (seguidores / a quienes sigo).
    for columna, tipo in (("bucket", "TEXT"), ("position", "INTEGER DEFAULT 0")):
        try:
            db.execute(f"SELECT {columna} FROM x_users LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE x_users ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.debug(f"No se pudo añadir {columna} a x_users: {e}")

    # Telegram: registro de chats BORRADOS/ABANDONADOS (tombstones). Cuando sales de un grupo/canal o
    # borras un chat, a veces el servidor sigue devolviendo su diálogo en get_dialogs (típico de
    # canales/supergrupos), así que "reaparecía" al actualizar. Con esta tabla, cargar_chats NO
    # reinserta un chat borrado mientras no haya un mensaje MÁS NUEVO que el borrado (señal de que el
    # usuario volvió a entrar), en cuyo caso el tombstone se limpia y el chat reaparece. Idempotente.
    try:
        db.execute("SELECT 1 FROM telegram_deleted_chats LIMIT 1")
    except Exception:
        log.info("Creando tabla telegram_deleted_chats (chats borrados que no deben reaparecer)...")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS telegram_deleted_chats (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                deleted_at TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
        except Exception as e:
            log.error(f"No se pudo crear telegram_deleted_chats: {e}")

    # Nota: NO se crea ninguna cuenta RSS por defecto. Las cuentas las crea el
    # usuario manualmente desde "Gestionar Cuentas".

    # --- v3: Reconstruir el FTS de RSS a TEXTO PLANO (una sola vez) ---
    # Las versiones anteriores indexaban el HTML COMPLETO de cada artículo en rss_articles_fts, lo
    # que disparaba el tamaño de la base (el índice FTS llegó a ser ~80% del archivo: tokens de
    # etiquetas/atributos/URLs y una copia del texto). Ahora se indexa solo el texto.
    # Se RECREA la tabla (DROP + CREATE), no basta un DELETE: en FTS5 el borrado deja segmentos
    # "tombstone" que no liberan espacio (de hecho lo aumentan); recrearla deja el índice limpio y
    # mínimo. Después, VACUUM devuelve el espacio al disco. Una sola vez (control por user_version);
    # la primera vez tras actualizar, el arranque tardará un poco más.
    # Migración dinámica: tablas del cliente de IRC (redes, canales/chats y mensajes).
    # CREATE TABLE IF NOT EXISTS: reejecutable sin daño (mismo criterio que el resto).
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS irc_networks (
                account_id INTEGER PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER DEFAULT 6697,
                tls INTEGER DEFAULT 1,
                verify_cert INTEGER DEFAULT 1,
                nick TEXT NOT NULL,
                alt_nicks TEXT DEFAULT '',
                username TEXT DEFAULT '',
                realname TEXT DEFAULT '',
                use_sasl INTEGER DEFAULT 0,
                sasl_username TEXT DEFAULT '',
                autojoin TEXT DEFAULT '',
                auto_connect INTEGER DEFAULT 1,
                encoding TEXT DEFAULT 'utf-8',
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS irc_channels (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                topic TEXT DEFAULT '',
                unread_count INTEGER DEFAULT 0,
                joined INTEGER DEFAULT 0,
                es_privado INTEGER DEFAULT 0,
                last_message TEXT DEFAULT '',
                last_message_date TEXT DEFAULT '',
                autojoin INTEGER DEFAULT 0,
                notify_level TEXT DEFAULT 'all',
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS irc_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                msgid TEXT DEFAULT '',
                nick TEXT DEFAULT '',
                account_tag TEXT DEFAULT '',
                text TEXT DEFAULT '',
                tipo TEXT DEFAULT 'msg',
                date TEXT DEFAULT '',
                es_propio INTEGER DEFAULT 0,
                highlight INTEGER DEFAULT 0,
                leido INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_irc_messages_chan "
                   "ON irc_messages(account_id, channel, id)")
        db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('irc', 'IRC', 1)")
    except Exception as e:
        log.error(f"Error al crear las tablas de IRC: {e}")

    # Migración dinámica: columna show_events en irc_channels (mostrar u ocultar entradas/salidas
    # por canal; en canales grandes ese ruido hace inusable la conversación con lector).
    try:
        db.execute("SELECT show_events FROM irc_channels LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE irc_channels ADD COLUMN show_events INTEGER DEFAULT 1")
        except Exception as e:
            log.debug(f"No se pudo añadir show_events a irc_channels: {e}")

    # Migración dinámica: tablas del cliente de Matrix (cuentas, salas y mensajes).
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS matrix_cuentas (
                account_id INTEGER PRIMARY KEY,
                homeserver TEXT NOT NULL,
                user_id TEXT NOT NULL,
                next_batch TEXT DEFAULT '',
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS matrix_salas (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                room_id TEXT NOT NULL,
                nombre TEXT DEFAULT '',
                tema TEXT DEFAULT '',
                es_dm INTEGER DEFAULT 0,
                unread_count INTEGER DEFAULT 0,
                last_message TEXT DEFAULT '',
                last_message_date TEXT DEFAULT '',
                notify_level TEXT DEFAULT 'all',
                invitacion INTEGER DEFAULT 0,
                espacio_id TEXT DEFAULT '',
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS matrix_mensajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                room_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                sender TEXT DEFAULT '',
                sender_nombre TEXT DEFAULT '',
                texto TEXT DEFAULT '',
                tipo TEXT DEFAULT 'msg',
                date TEXT DEFAULT '',
                es_propio INTEGER DEFAULT 0,
                highlight INTEGER DEFAULT 0,
                leido INTEGER DEFAULT 0,
                media_mxc TEXT DEFAULT '',
                reply_to TEXT DEFAULT '',
                editado INTEGER DEFAULT 0,
                UNIQUE(account_id, event_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_matrix_mensajes_sala "
                   "ON matrix_mensajes(account_id, room_id, id)")
        # MX-012: relaciones (reacciones m.annotation y votos m.reference) como EVENTOS deduplicables
        # por event_id. Los agregados (recuento de reacciones, votos de encuesta) se RECALCULAN desde
        # aquí de forma idempotente, así reprocesar el mismo sync no infla los conteos y una redacción
        # (propia o ajena) sí los decrementa. rel_key: emoji en reacciones; JSON de answers en votos.
        db.execute("""
            CREATE TABLE IF NOT EXISTS matrix_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                target_event_id TEXT NOT NULL,
                rel_type TEXT NOT NULL,
                rel_key TEXT DEFAULT '',
                sender TEXT DEFAULT '',
                redacted INTEGER DEFAULT 0,
                ts INTEGER DEFAULT 0,
                UNIQUE(account_id, event_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_matrix_relations_target "
                   "ON matrix_relations(account_id, target_event_id)")
        db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('matrix', 'Matrix', 1)")
    except Exception as e:
        log.error(f"Error al crear las tablas de Matrix: {e}")

    # Migración dinámica: columna reacciones en matrix_mensajes (conteo legible "👍 2, ❤ 1").
    try:
        db.execute("SELECT reacciones FROM matrix_mensajes LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE matrix_mensajes ADD COLUMN reacciones TEXT DEFAULT ''")
        except Exception as e:
            log.debug(f"No se pudo añadir reacciones a matrix_mensajes: {e}")

    # Migración dinámica: columna media_crypto en matrix_mensajes. Guarda (JSON) las claves de
    # descifrado de un adjunto CIFRADO (key/iv/hashes) para poder descargarlo y descifrarlo luego.
    try:
        db.execute("SELECT media_crypto FROM matrix_mensajes LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE matrix_mensajes ADD COLUMN media_crypto TEXT DEFAULT ''")
        except Exception as e:
            log.debug(f"No se pudo añadir media_crypto a matrix_mensajes: {e}")

    # Migración dinámica (M7): etiqueta de sala (favorita/baja prioridad, tags de Matrix),
    # hilos (thread_root = event_id raíz del hilo) y encuestas (poll_json con pregunta/opciones/votos).
    for tabla, columna, ddl in (
        ("matrix_salas", "etiqueta", "ALTER TABLE matrix_salas ADD COLUMN etiqueta TEXT DEFAULT ''"),
        ("matrix_mensajes", "thread_root", "ALTER TABLE matrix_mensajes ADD COLUMN thread_root TEXT DEFAULT ''"),
        ("matrix_mensajes", "poll_json", "ALTER TABLE matrix_mensajes ADD COLUMN poll_json TEXT DEFAULT ''"),
        # MX-013: marca de MENCIÓN por sala tomada del servidor (unread_notifications.highlight_count).
        ("matrix_salas", "highlight", "ALTER TABLE matrix_salas ADD COLUMN highlight INTEGER DEFAULT 0"),
        # P2: estado de envío de un mensaje propio ('' = enviado; 'fallido' = no se pudo enviar).
        ("matrix_mensajes", "send_state", "ALTER TABLE matrix_mensajes ADD COLUMN send_state TEXT DEFAULT ''"),
    ):
        try:
            db.execute(f"SELECT {columna} FROM {tabla} LIMIT 1")
        except Exception:
            try:
                db.execute(ddl)
            except Exception as e:
                log.debug(f"No se pudo añadir {columna} a {tabla}: {e}")

    # Migración dinámica: columna cifrada en matrix_salas (salas con E2EE activo).
    try:
        db.execute("SELECT cifrada FROM matrix_salas LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE matrix_salas ADD COLUMN cifrada INTEGER DEFAULT 0")
        except Exception as e:
            log.debug(f"No se pudo añadir cifrada a matrix_salas: {e}")

    # Migración dinámica: columna es_espacio en matrix_salas (salas de tipo espacio, m.space).
    try:
        db.execute("SELECT es_espacio FROM matrix_salas LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE matrix_salas ADD COLUMN es_espacio INTEGER DEFAULT 0")
        except Exception as e:
            log.debug(f"No se pudo añadir es_espacio a matrix_salas: {e}")

    # Migración dinámica: FTS de mensajes de Matrix (búsqueda global, patrón IRC).
    try:
        db.execute("SELECT 1 FROM matrix_mensajes_fts LIMIT 1")
    except Exception:
        try:
            db.execute("""
                CREATE VIRTUAL TABLE matrix_mensajes_fts USING fts5(
                    msg_db_id UNINDEXED,
                    sender,
                    texto,
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
            filas = db.query("SELECT id, COALESCE(NULLIF(sender_nombre,''), sender), texto "
                             "FROM matrix_mensajes WHERE tipo IN ('msg','accion','aviso') AND texto <> ''")
            if filas:
                db.execute_many(
                    "INSERT INTO matrix_mensajes_fts (msg_db_id, sender, texto) VALUES (?, ?, ?)",
                    [(f[0], f[1] or "", f[2] or "") for f in filas])
                log.info("FTS de Matrix creado e indexado (%s mensajes).", len(filas))
        except Exception as e:
            log.warning(f"No se pudo crear el FTS de Matrix (la búsqueda caerá a LIKE): {e}")

    # Migración dinámica: FTS de mensajes de IRC (búsqueda global instantánea). Se indexa solo
    # el texto de mensajes/acciones/avisos (no joins/parts). Poblado retroactivo si hay historial.
    try:
        db.execute("SELECT 1 FROM irc_messages_fts LIMIT 1")
    except Exception:
        try:
            db.execute("""
                CREATE VIRTUAL TABLE irc_messages_fts USING fts5(
                    msg_db_id UNINDEXED,
                    nick,
                    text,
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
            filas = db.query("SELECT id, nick, text FROM irc_messages "
                             "WHERE tipo IN ('msg','action','notice') AND text <> ''")
            if filas:
                db.execute_many(
                    "INSERT INTO irc_messages_fts (msg_db_id, nick, text) VALUES (?, ?, ?)",
                    [(f[0], f[1] or "", f[2] or "") for f in filas])
                log.info("FTS de IRC creado e indexado (%s mensajes).", len(filas))
        except Exception as e:
            log.warning(f"No se pudo crear el FTS de IRC (la búsqueda caerá a LIKE): {e}")

    # Migración dinámica: tablas del cliente de XMPP (Jabber). Una fila por CUENTA en xmpp_cuentas;
    # cada contacto del roster o sala MUC es una fila en xmpp_chats (id 'acc|jid'); los mensajes en
    # xmpp_mensajes con dedupe por (account_id, stanza_id) UNIQUE — entre Carbons + MAM + eco propio
    # los duplicados son la norma en XMPP, así que el dedupe nace desde el primer mensaje.
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS xmpp_cuentas (
                account_id INTEGER PRIMARY KEY,
                jid TEXT NOT NULL,
                recurso TEXT DEFAULT 'SocialRSS',
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS xmpp_chats (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                jid TEXT NOT NULL,
                tipo TEXT DEFAULT 'contacto',
                nombre TEXT DEFAULT '',
                presencia TEXT DEFAULT '',
                estado_texto TEXT DEFAULT '',
                suscripcion TEXT DEFAULT '',
                unread_count INTEGER DEFAULT 0,
                last_message TEXT DEFAULT '',
                last_message_date TEXT DEFAULT '',
                notify_level TEXT DEFAULT 'all',
                autojoin INTEGER DEFAULT 0,
                mi_nick TEXT DEFAULT '',
                bloqueado INTEGER DEFAULT 0,
                solicitud INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS xmpp_mensajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                chat_jid TEXT NOT NULL,
                stanza_id TEXT NOT NULL,
                origin_id TEXT DEFAULT '',
                sender TEXT DEFAULT '',
                sender_nombre TEXT DEFAULT '',
                texto TEXT DEFAULT '',
                tipo TEXT DEFAULT 'msg',
                media_url TEXT DEFAULT '',
                date TEXT DEFAULT '',
                es_propio INTEGER DEFAULT 0,
                highlight INTEGER DEFAULT 0,
                leido INTEGER DEFAULT 0,
                reply_to TEXT DEFAULT '',
                editado INTEGER DEFAULT 0,
                reacciones TEXT DEFAULT '',
                cifrado INTEGER DEFAULT 0,
                UNIQUE(account_id, stanza_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_xmpp_mensajes_chat "
                   "ON xmpp_mensajes(account_id, chat_jid, id)")
        db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('xmpp', 'XMPP', 1)")
    except Exception as e:
        log.error(f"Error al crear las tablas de XMPP: {e}")

    # Migración dinámica: columna 'entregado' en xmpp_mensajes (recibo de entrega XEP-0184: el
    # cliente del contacto confirma que recibió tu mensaje). 0 = enviado, 1 = entregado.
    try:
        db.execute("SELECT entregado FROM xmpp_mensajes LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE xmpp_mensajes ADD COLUMN entregado INTEGER DEFAULT 0")
        except Exception as e:
            log.debug(f"No se pudo añadir entregado a xmpp_mensajes: {e}")
    # Migración dinámica (X5): columna 'reacciones_json' (mapa {reactor: [emojis]} para calcular el
    # recuento legible; las reacciones de XMPP, XEP-0444, sustituyen el conjunto completo por usuario).
    try:
        db.execute("SELECT reacciones_json FROM xmpp_mensajes LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE xmpp_mensajes ADD COLUMN reacciones_json TEXT DEFAULT ''")
        except Exception as e:
            log.debug(f"No se pudo añadir reacciones_json a xmpp_mensajes: {e}")

    # Migración dinámica (WhatsApp-like): archivar / fijar / silenciar chats de XMPP.
    for _col, _defsql in (("archivado", "INTEGER DEFAULT 0"),
                          ("fijado", "INTEGER DEFAULT 0"),
                          ("silenciado_hasta", "TEXT DEFAULT ''")):
        try:
            db.execute(f"SELECT {_col} FROM xmpp_chats LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE xmpp_chats ADD COLUMN {_col} {_defsql}")
            except Exception as e:
                log.debug(f"No se pudo añadir {_col} a xmpp_chats: {e}")

    # Migración dinámica: columna 'destacado' en xmpp_mensajes (mensajes marcados como favoritos).
    try:
        db.execute("SELECT destacado FROM xmpp_mensajes LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE xmpp_mensajes ADD COLUMN destacado INTEGER DEFAULT 0")
        except Exception as e:
            log.debug(f"No se pudo añadir destacado a xmpp_mensajes: {e}")

    # Migración dinámica: columnas de notas de voz en xmpp_mensajes (como WhatsApp):
    #  - 'es_voz'      : 1 si el adjunto es una nota de voz (se lee "Mensaje de voz").
    #  - 'duracion'    : duración en segundos del audio/vídeo (0 si aún no se conoce).
    #  - 'reproducido' : 1 cuando el usuario ya reprodujo esa nota de voz (estado pendiente/reproducido).
    for _col, _defsql in (("es_voz", "INTEGER DEFAULT 0"),
                          ("duracion", "REAL DEFAULT 0"),
                          ("reproducido", "INTEGER DEFAULT 0")):
        try:
            db.execute(f"SELECT {_col} FROM xmpp_mensajes LIMIT 1")
        except Exception:
            try:
                db.execute(f"ALTER TABLE xmpp_mensajes ADD COLUMN {_col} {_defsql}")
            except Exception as e:
                log.debug(f"No se pudo añadir {_col} a xmpp_mensajes: {e}")

    # Saneo: una sala a la que YA te uniste (autojoin=1) NO es una solicitud pendiente. Builds
    # antiguas dejaron salas de pasarela con solicitud=1 y autojoin=1 a la vez (aparecían como
    # invitaciones a aceptar aunque ya estabas dentro, y al "aceptar" el join se colgaba). Idempotente.
    try:
        db.execute("UPDATE xmpp_chats SET solicitud = 0 "
                   "WHERE tipo = 'muc' AND COALESCE(autojoin,0) = 1 AND COALESCE(solicitud,0) = 1")
    except Exception as e:
        log.debug(f"No se pudo sanear solicitud de MUCs ya unidas: {e}")

    # Migración dinámica (X9): columna 'pep_estado' en xmpp_chats (ánimo/actividad que publica el
    # contacto por PEP, ya en palabras, para verlo en su ficha).
    try:
        db.execute("SELECT pep_estado FROM xmpp_chats LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE xmpp_chats ADD COLUMN pep_estado TEXT DEFAULT ''")
        except Exception as e:
            log.debug(f"No se pudo añadir pep_estado a xmpp_chats: {e}")

    # Migración dinámica (X6): columna 'cifrado' en xmpp_chats (OMEMO activado para ese chat). El
    # cifrado es por conversación: se recuerda si el usuario lo activó para hablar cifrado con ese
    # contacto o sala.
    try:
        db.execute("SELECT cifrado FROM xmpp_chats LIMIT 1")
    except Exception:
        try:
            db.execute("ALTER TABLE xmpp_chats ADD COLUMN cifrado INTEGER DEFAULT 0")
        except Exception as e:
            log.debug(f"No se pudo añadir cifrado a xmpp_chats: {e}")

    # Migración dinámica: FTS de mensajes de XMPP (búsqueda global, patrón IRC/Matrix).
    try:
        db.execute("SELECT 1 FROM xmpp_mensajes_fts LIMIT 1")
    except Exception:
        try:
            db.execute("""
                CREATE VIRTUAL TABLE xmpp_mensajes_fts USING fts5(
                    msg_db_id UNINDEXED,
                    sender,
                    texto,
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
            filas = db.query("SELECT id, COALESCE(NULLIF(sender_nombre,''), sender), texto "
                             "FROM xmpp_mensajes WHERE tipo IN ('msg','accion','aviso') AND texto <> ''")
            if filas:
                db.execute_many(
                    "INSERT INTO xmpp_mensajes_fts (msg_db_id, sender, texto) VALUES (?, ?, ?)",
                    [(f[0], f[1] or "", f[2] or "") for f in filas])
                log.info("FTS de XMPP creado e indexado (%s mensajes).", len(filas))
        except Exception as e:
            log.warning(f"No se pudo crear el FTS de XMPP (la búsqueda caerá a LIKE): {e}")

    # RSS: registro persistente de artículos ya LEÍDOS (por su id estable feed_id:guid). Sobrevive al
    # vaciado "no conservar entre sesiones": cuando ese vaciado borra un artículo leído, su id queda
    # aquí anotado, de modo que si el feed lo vuelve a servir en la siguiente actualización se
    # re-inserta YA como leído (y no reaparece como novedad sin leer, que era el fallo reportado).
    # Idempotente (CREATE ... IF NOT EXISTS), se ejecuta en cada arranque.
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rss_read_ledger (
                article_id TEXT PRIMARY KEY,
                ts TEXT
            )
        """)
    except Exception as e:
        log.debug(f"No se pudo crear rss_read_ledger: {e}")

    if version < 3:
        try:
            from app.core.html_utils import html_a_texto
            filas = db.query("SELECT id, title, content, author FROM rss_articles")
            with db.transaccion():
                db.execute("DROP TABLE IF EXISTS rss_articles_fts")
                db.execute("""
                    CREATE VIRTUAL TABLE rss_articles_fts USING fts5(
                        article_db_id UNINDEXED,
                        title,
                        content,
                        author,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                """)
                for aid, title, content, author in filas:
                    txt = html_a_texto(content or "") if (content and "<" in content) else (content or "")
                    db.execute(
                        "INSERT INTO rss_articles_fts (article_db_id, title, content, author) VALUES (?,?,?,?)",
                        (aid, title or "", txt, author or ""))
            log.info("FTS de RSS reconstruido a texto plano (%s artículos).", len(filas))
            # Recuperar el espacio que ocupaba el índice antiguo (VACUUM fuera de transacción).
            try:
                db.execute("VACUUM")
                log.info("Base de datos compactada (VACUUM) tras reconstruir el FTS de RSS.")
            except Exception as e:
                log.warning(f"No se pudo compactar la base tras reconstruir el FTS: {e}")
        except Exception as e:
            log.error(f"No se pudo reconstruir el FTS de RSS: {e}")
        try:
            db.execute("PRAGMA user_version = 3")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=3: {e}")

    if version < 4:
        # Índices compuestos para el siguiente orden de magnitud (auditoría 2026-07):
        # evitan las ordenaciones temporales (temp B-tree) que aparecían en los planes
        # de ejecución. Solo los medidos como útiles; cada índice encarece la escritura.
        try:
            # X: timeline por cuenta y posición (antes ordenaba sin índice compuesto).
            db.execute("""CREATE INDEX IF NOT EXISTS idx_x_tl_acc_tl_pos
                          ON x_timeline_tweets(account_id, timeline, position)""")
        except Exception as e:
            log.debug(f"No se pudo crear idx_x_tweets_acc_tl_pos: {e}")
        try:
            # Telegram: lista de chats ordenada por fijado y fecha del último mensaje.
            db.execute("""CREATE INDEX IF NOT EXISTS idx_tgchats_acc_pin_date
                          ON telegram_chats(account_id, pinned DESC, last_message_date DESC)""")
        except Exception as e:
            log.debug(f"No se pudo crear idx_tgchats_acc_pin_date: {e}")
        try:
            # RSS: cubre el filtro por cuenta + no-borrado + orden por fecha, que es la
            # consulta del buffer principal (los parciales por read/favorite ya existen).
            db.execute("""CREATE INDEX IF NOT EXISTS idx_rssart_acc_del_pub
                          ON rss_articles(account_id, deleted, published_at DESC)""")
        except Exception as e:
            log.debug(f"No se pudo crear idx_rssart_acc_del_pub: {e}")
        try:
            db.execute("PRAGMA user_version = 4")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=4: {e}")

    if version < 5:
        # Auditoría Mastodon 2026-07: índice crítico para el listado de notificaciones (el
        # join buscaba estados solo por account_id: 57 ms con 11 filas visibles; con el
        # índice compuesto, microsegundos) y clave de idempotencia persistente por elemento
        # de hilo (sin ella, reanudar tras un timeout podía duplicar publicaciones).
        try:
            db.execute("""CREATE INDEX IF NOT EXISTS idx_mst_acc_statusid
                          ON mastodon_statuses(account_id, status_id)""")
        except Exception as e:
            log.debug(f"No se pudo crear idx_mst_acc_statusid: {e}")
        try:
            db.execute("ALTER TABLE mastodon_thread_items ADD COLUMN idem_key TEXT")
        except Exception:
            pass  # ya existía (instalación nueva)
        try:
            db.execute("ANALYZE mastodon_statuses")
        except Exception:
            pass
        try:
            db.execute("PRAGMA user_version = 5")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=5: {e}")

    if version < 6:
        # Auditoría Bluesky 2026-07 (BSKY-004): conservar el ORDEN Y CONTEXTO con que el
        # servidor entregó cada elemento. Sin sort_key, todos los buffers se ordenaban por
        # indexed_at del post: un repost de un post antiguo se hundía, los feeds
        # algorítmicos perdían su ranking y la búsqueda su relevancia. reason/feed_context/
        # req_id guardan el motivo (repost/pin) y el contexto para interacciones de feed.
        for sql in (
            "ALTER TABLE bluesky_timeline_posts ADD COLUMN sort_key INTEGER",
            "ALTER TABLE bluesky_timeline_posts ADD COLUMN reason_indexed_at TEXT",
            "ALTER TABLE bluesky_timeline_posts ADD COLUMN reason_json TEXT",
            "ALTER TABLE bluesky_timeline_posts ADD COLUMN feed_context TEXT",
            "ALTER TABLE bluesky_timeline_posts ADD COLUMN req_id TEXT",
        ):
            try:
                db.execute(sql)
            except Exception:
                pass  # columna ya existente
        try:
            db.execute("""CREATE INDEX IF NOT EXISTS idx_bsky_timeline_order
                          ON bluesky_timeline_posts(account_id, timeline, sort_key DESC)""")
        except Exception as e:
            log.debug(f"No se pudo crear idx_bsky_timeline_order: {e}")
        # Sembrar sort_key para las filas existentes según el orden actual (indexed_at),
        # para que convivan bien con las nuevas claves monotónicas.
        try:
            db.execute("""
                UPDATE bluesky_timeline_posts AS t SET sort_key = (
                    SELECT COUNT(*) FROM bluesky_timeline_posts t2
                    JOIN bluesky_posts p2 ON p2.id = t2.post_db_id
                    JOIN bluesky_posts p ON p.id = t.post_db_id
                    WHERE t2.account_id = t.account_id AND t2.timeline = t.timeline
                      AND p2.indexed_at <= p.indexed_at)
                WHERE sort_key IS NULL""")
        except Exception as e:
            log.debug(f"No se pudo sembrar sort_key: {e}")
        # Índice de mensajes de chat para lecturas cronológicas (auditoría 13.3).
        try:
            db.execute("""CREATE INDEX IF NOT EXISTS idx_bsky_msgs_convo_fecha
                          ON bluesky_messages(account_id, convo_id, sent_at)""")
        except Exception as e:
            log.debug(f"No se pudo crear idx_bsky_msgs_convo_fecha: {e}")
        try:
            db.execute("PRAGMA user_version = 6")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=6: {e}")

    if version < 7:
        # Borradores persistentes de Mastodon (auditoría N3): la tabla mastodon_drafts
        # existía sin usarse y se queda corta (sin encuesta, idioma, programación ni
        # contexto de respuesta/cita). El borrador completo del compositor va en JSON.
        for sql in (
            "ALTER TABLE mastodon_drafts ADD COLUMN updated_at TEXT",
            "ALTER TABLE mastodon_drafts ADD COLUMN contexto TEXT",
            "ALTER TABLE mastodon_drafts ADD COLUMN borrador_json TEXT",
        ):
            try:
                db.execute(sql)
            except Exception:
                pass  # columna ya existente (instalación nueva)
        try:
            db.execute("PRAGMA user_version = 7")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=7: {e}")

    if version < 8:
        # Conversaciones directas de primera clase (auditoría N6): lista real de
        # conversaciones del servidor, con participantes, último estado y no leído.
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS mastodon_conversations (
                    account_id INTEGER NOT NULL,
                    conversation_id TEXT NOT NULL,
                    participants TEXT,
                    last_status_db_id TEXT,
                    last_excerpt TEXT,
                    unread INTEGER DEFAULT 0,
                    updated_at TEXT,
                    PRIMARY KEY (account_id, conversation_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
        except Exception as e:
            log.debug(f"No se pudo crear mastodon_conversations: {e}")
        try:
            db.execute("PRAGMA user_version = 8")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=8: {e}")

    if version < 9:
        # Outbox de Bluesky (auditoría 6.6): rkey idempotente y carga persistidos ANTES de
        # enviar; tras un timeout ambiguo se reconcilia con getRecord y se puede reintentar
        # con el mismo rkey sin duplicar la publicación.
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS bluesky_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    rkey TEXT NOT NULL,
                    collection TEXT NOT NULL DEFAULT 'app.bsky.feed.post',
                    payload_json TEXT,
                    estado TEXT NOT NULL DEFAULT 'pending',
                    uri TEXT,
                    error TEXT,
                    intentos INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
        except Exception as e:
            log.debug(f"No se pudo crear bluesky_outbox: {e}")
        try:
            db.execute("PRAGMA user_version = 9")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=9: {e}")

    if version < 10:
        # ------------------------------------------------------------------
        # Auditoría RSS 2026-07-11: aislamiento por cuenta (RSS-01/02/03),
        # scheduler y salud por feed (RSS-05/11), artículos actualizables
        # (RSS-14) y URL normalizada única por cuenta (RSS-15/21).
        # ------------------------------------------------------------------
        _migrar_rss_auditoria_v10()
        try:
            db.execute("PRAGMA user_version = 10")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=10: {e}")

    if version < 11:
        # ------------------------------------------------------------------
        # Funciones nuevas de la auditoría RSS (prioridad A): reglas avanzadas
        # (varias condiciones Y/O, prioridad, ámbito, detener) y carpetas
        # inteligentes (consultas guardadas estructuradas como buffers del árbol).
        # ------------------------------------------------------------------
        try:
            cols_reglas = {r[1] for r in db.query("PRAGMA table_info(rss_rules)")}
        except Exception:
            cols_reglas = set()
        for columna, tipo in (
                ("conditions_json", "TEXT"), ("match_mode", "TEXT DEFAULT 'all'"),
                ("priority", "INTEGER DEFAULT 0"), ("stop_processing", "INTEGER DEFAULT 0"),
                ("scope_feed_id", "INTEGER"), ("scope_category_id", "INTEGER"),
                ("actions_json", "TEXT")):
            if columna in cols_reglas or not cols_reglas:
                continue
            try:
                db.execute(f"ALTER TABLE rss_rules ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.error(f"Error al añadir {columna} a rss_rules: {e}")
        # Hash del CUERPO del feed: muchos servidores no dan ETag/Last-Modified y devolvían
        # 200 con el mismo XML; con el hash se salta el parseo (lo caro) cuando no cambió.
        try:
            cols_f11 = {r[1] for r in db.query("PRAGMA table_info(rss_feeds)")}
            if cols_f11 and "body_hash" not in cols_f11:
                db.execute("ALTER TABLE rss_feeds ADD COLUMN body_hash TEXT")
        except Exception as e:
            log.error(f"Error al añadir body_hash a rss_feeds: {e}")
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS rss_smart_folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    criteria_json TEXT NOT NULL,   -- criterios estructurados, versionados
                    position INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_smart_folders_account "
                       "ON rss_smart_folders(account_id)")
        except Exception as e:
            log.error(f"Error al crear rss_smart_folders: {e}")
        try:
            db.execute("PRAGMA user_version = 11")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=11: {e}")

    if version < 12:
        # body_hash se añadió a la v11 cuando la instalación del usuario YA estaba en 11:
        # su base no recibió la columna y el ciclo RSS entero fallaba con "no such column"
        # (99 errores por ronda). Se reaplica aquí de forma idempotente. Lección: una vez
        # que una versión de migración pudo ejecutarse en cualquier equipo, NO se amplía;
        # siempre versión nueva.
        try:
            cols_f12 = {r[1] for r in db.query("PRAGMA table_info(rss_feeds)")}
            if cols_f12 and "body_hash" not in cols_f12:
                db.execute("ALTER TABLE rss_feeds ADD COLUMN body_hash TEXT")
        except Exception as e:
            log.error(f"Error al añadir body_hash a rss_feeds (v12): {e}")
        try:
            db.execute("PRAGMA user_version = 12")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=12: {e}")

    if version < 13:
        # ------------------------------------------------------------------
        # RSS prioridad B (funciones nuevas de la auditoría): múltiples adjuntos
        # por artículo (rss_enclosures), Podcast 2.0 (columnas ligeras + JSON de
        # metadatos ricos), COLA de descargas de podcast (rss_download_queue) y
        # ETIQUETAS múltiples (rss_tags + relaciones con feeds y artículos).
        # ------------------------------------------------------------------
        # Columnas ligeras de Podcast 2.0 en rss_articles (lo que se muestra en la
        # lista/detalle). Los metadatos ricos —capítulos, transcripción, apoyo,
        # personas— van en podcast_json (solo se lee al abrir el episodio, no en la
        # lista, para no penalizar el rendimiento del buffer).
        try:
            cols_art13 = {r[1] for r in db.query("PRAGMA table_info(rss_articles)")}
        except Exception:
            cols_art13 = set()
        for columna, tipo in (
                ("duration_seconds", "INTEGER"), ("explicit", "INTEGER DEFAULT 0"),
                ("episode_image", "TEXT"), ("season", "INTEGER"),
                ("episode_num", "INTEGER"), ("podcast_json", "TEXT")):
            if not cols_art13 or columna in cols_art13:
                continue
            try:
                db.execute(f"ALTER TABLE rss_articles ADD COLUMN {columna} {tipo}")
            except Exception as e:
                log.error(f"Error al añadir {columna} a rss_articles (v13): {e}")
        # Feed mudado (RSS prioridad D): URL nueva propuesta por un 301/308; se aplica
        # cuando se confirma (misma propuesta dos ciclos seguidos).
        try:
            cols_f13 = {r[1] for r in db.query("PRAGMA table_info(rss_feeds)")}
            if cols_f13 and "moved_to" not in cols_f13:
                db.execute("ALTER TABLE rss_feeds ADD COLUMN moved_to TEXT")
        except Exception as e:
            log.error(f"Error al añadir moved_to a rss_feeds (v13): {e}")
        # Varios adjuntos por artículo (RSS: "múltiples enclosures"). El "principal"
        # (audio/vídeo reproducible) sigue en rss_articles.enclosure_url; aquí van TODOS.
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS rss_enclosures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    mime TEXT,
                    length INTEGER DEFAULT 0,
                    es_audio INTEGER DEFAULT 0,
                    orden INTEGER DEFAULT 0,
                    FOREIGN KEY(article_id) REFERENCES rss_articles(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_enclosures_article "
                       "ON rss_enclosures(article_id)")
        except Exception as e:
            log.error(f"Error al crear rss_enclosures: {e}")
        # Cola de descargas de podcast: estado, bytes, prioridad y error persistentes.
        # 'estado': cola | descargando | pausado | hecho | error.
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS rss_download_queue (
                    article_id TEXT PRIMARY KEY,
                    account_id INTEGER,
                    url TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'cola',
                    bytes_hechos INTEGER DEFAULT 0,
                    bytes_total INTEGER DEFAULT 0,
                    prioridad INTEGER DEFAULT 0,
                    error TEXT,
                    ruta_part TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(article_id) REFERENCES rss_articles(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_download_queue_estado "
                       "ON rss_download_queue(estado)")
        except Exception as e:
            log.error(f"Error al crear rss_download_queue: {e}")
        # Etiquetas por cuenta + relaciones muchos-a-muchos con feeds y artículos.
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS rss_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    position INTEGER DEFAULT 0,
                    UNIQUE(account_id, name),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("""
                CREATE TABLE IF NOT EXISTS rss_feed_tags (
                    tag_id INTEGER NOT NULL,
                    feed_id INTEGER NOT NULL,
                    PRIMARY KEY (tag_id, feed_id),
                    FOREIGN KEY(tag_id) REFERENCES rss_tags(id) ON DELETE CASCADE,
                    FOREIGN KEY(feed_id) REFERENCES rss_feeds(id) ON DELETE CASCADE
                )""")
            db.execute("""
                CREATE TABLE IF NOT EXISTS rss_article_tags (
                    tag_id INTEGER NOT NULL,
                    article_id TEXT NOT NULL,
                    PRIMARY KEY (tag_id, article_id),
                    FOREIGN KEY(tag_id) REFERENCES rss_tags(id) ON DELETE CASCADE,
                    FOREIGN KEY(article_id) REFERENCES rss_articles(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_article_tags_tag "
                       "ON rss_article_tags(tag_id)")
        except Exception as e:
            log.error(f"Error al crear tablas de etiquetas RSS: {e}")
        try:
            db.execute("PRAGMA user_version = 13")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=13: {e}")

    if version < 14:
        # ------------------------------------------------------------------
        # Telegram Drive: nube personal sobre un canal privado de Telegram.
        # Jerarquía VIRTUAL de carpetas/archivos (tgdrive_items), partes de los
        # archivos grandes divididos (tgdrive_parts, cada parte es un documento/
        # mensaje del canal), cola de transferencias persistente (tgdrive_transfers)
        # y eliminaciones de mensajes pendientes (tgdrive_pending_deletes).
        # ------------------------------------------------------------------
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS tgdrive_items (
                    id TEXT PRIMARY KEY,                 -- UUID interno
                    account_id INTEGER NOT NULL,         -- cuenta de Telegram
                    channel_id INTEGER,                  -- id del canal privado
                    parent_id TEXT,                      -- carpeta padre (UUID) o NULL en la raíz
                    name TEXT NOT NULL,                  -- nombre original
                    tipo TEXT NOT NULL,                  -- 'folder' | 'file'
                    path TEXT NOT NULL DEFAULT '',       -- ruta virtual (denormalizada)
                    size INTEGER DEFAULT 0,              -- tamaño total del archivo
                    mime TEXT,
                    sha256 TEXT,                         -- hash del contenido completo
                    created_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'normal',   -- normal|trash|incomplete|deleted
                    trashed_at TEXT,                     -- cuándo fue a la papelera
                    original_parent_id TEXT,             -- para restaurar de la papelera
                    original_path TEXT,
                    num_parts INTEGER DEFAULT 1,         -- nº de partes (1 = sin dividir)
                    encrypted INTEGER DEFAULT 0,         -- cifrado local
                    meta_version INTEGER DEFAULT 1,      -- versión del formato de metadatos
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY(parent_id) REFERENCES tgdrive_items(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tgdrive_items_parent "
                       "ON tgdrive_items(account_id, parent_id, estado)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tgdrive_items_sha "
                       "ON tgdrive_items(account_id, sha256)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tgdrive_items_estado "
                       "ON tgdrive_items(account_id, estado)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS tgdrive_parts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,               -- archivo al que pertenece
                    orden INTEGER NOT NULL,              -- 0..num_parts-1
                    size INTEGER DEFAULT 0,
                    sha256 TEXT,                         -- hash de esta parte
                    message_id INTEGER,                  -- id del mensaje en el canal
                    tg_file_id TEXT,                     -- file_id del documento
                    tg_unique_id TEXT,                   -- file_unique_id (estable, dedupe)
                    estado TEXT DEFAULT 'pending',       -- pending|uploaded|failed
                    UNIQUE(item_id, orden),
                    FOREIGN KEY(item_id) REFERENCES tgdrive_items(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tgdrive_parts_item "
                       "ON tgdrive_parts(item_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS tgdrive_transfers (
                    id TEXT PRIMARY KEY,                 -- UUID de la transferencia
                    account_id INTEGER,
                    item_id TEXT,                        -- item asociado (puede ser NULL)
                    parent_id TEXT,                      -- carpeta destino en subida
                    carpeta_destino TEXT,                -- carpeta local en descarga
                    operacion TEXT NOT NULL,             -- upload|download
                    nombre TEXT,
                    local_path TEXT,                     -- origen (subida) o destino (descarga)
                    size_total INTEGER DEFAULT 0,
                    bytes_hechos INTEGER DEFAULT 0,
                    estado TEXT NOT NULL DEFAULT 'pendiente',
                    error TEXT,
                    prioridad INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY(item_id) REFERENCES tgdrive_items(id) ON DELETE SET NULL
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tgdrive_transfers_estado "
                       "ON tgdrive_transfers(estado)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS tgdrive_pending_deletes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER,
                    channel_id INTEGER,
                    message_id INTEGER,
                    created_at TEXT
                )""")
        except Exception as e:
            log.error(f"Error al crear las tablas de Telegram Drive: {e}")
        try:
            db.execute("PRAGMA user_version = 14")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=14: {e}")

    if version < 15:
        # ------------------------------------------------------------------
        # Telegram Drive: descripción OPCIONAL por elemento (carpeta o archivo), un
        # metadato de la nube que el usuario puede poner al crear/subir. No destructivo:
        # ALTER ADD COLUMN conserva los datos ya existentes (nube ya creada). Se añade
        # también a la cola para poder pasarla desde la subida hasta el item final.
        # ------------------------------------------------------------------
        try:
            cols = {r[1] for r in db.query("PRAGMA table_info(tgdrive_items)")}
            if "descripcion" not in cols:
                db.execute("ALTER TABLE tgdrive_items ADD COLUMN descripcion TEXT")
            cols_t = {r[1] for r in db.query("PRAGMA table_info(tgdrive_transfers)")}
            if "descripcion" not in cols_t:
                db.execute("ALTER TABLE tgdrive_transfers ADD COLUMN descripcion TEXT")
        except Exception as e:
            log.error(f"Error al añadir la descripción a Telegram Drive: {e}")
        try:
            db.execute("PRAGMA user_version = 15")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=15: {e}")

    if version < 16:
        # ------------------------------------------------------------------
        # Reddit (9ª red). Modelo de tres capas como Bluesky/Mastodon:
        #  - reddit_posts / reddit_comments: caché de contenido (PK "<account_id>:<fullname>").
        #  - reddit_timeline_posts / reddit_timeline_comments: relación buffer↔contenido, donde
        #    `timeline` es la clave del buffer ("home", "sub:<nombre>", "user:<nombre>",
        #    "saved", "search:<texto>"…).
        #  - reddit_subreddits: caché de subreddits (suscripciones/info).
        #  - reddit_messages: bandeja (PM, respuestas, menciones, enviados); el buffer se deriva
        #    de la columna msg_type, sin tabla de relación.
        # Todo con ON DELETE CASCADE sobre accounts: al borrar la cuenta se limpia su contenido.
        # ------------------------------------------------------------------
        # Registrar el servicio 'reddit' en services (necesario por la FK de accounts.service_id:
        # sin esto, dar de alta una cuenta de Reddit falla con FOREIGN KEY constraint failed).
        try:
            db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('reddit', 'Reddit', 1)")
        except Exception as e:
            log.error(f"No se pudo registrar el servicio Reddit: {e}")
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS reddit_posts (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<fullname>"
                    account_id INTEGER NOT NULL,
                    fullname TEXT NOT NULL,          -- t3_<base36>
                    post_id TEXT,                    -- base36 sin prefijo
                    subreddit TEXT,
                    author TEXT,
                    title TEXT,
                    selftext TEXT,
                    url TEXT,
                    permalink TEXT,
                    created_utc REAL DEFAULT 0,
                    score INTEGER DEFAULT 0,
                    upvote_ratio REAL DEFAULT 0,
                    num_comments INTEGER DEFAULT 0,
                    over_18 INTEGER DEFAULT 0,
                    spoiler INTEGER DEFAULT 0,
                    stickied INTEGER DEFAULT 0,
                    locked INTEGER DEFAULT 0,
                    saved INTEGER DEFAULT 0,
                    hidden INTEGER DEFAULT 0,
                    clicked INTEGER DEFAULT 0,
                    likes INTEGER,                   -- 1=upvote, 0=downvote, NULL=sin voto
                    link_flair_text TEXT,
                    is_self INTEGER DEFAULT 0,
                    is_gallery INTEGER DEFAULT 0,
                    post_hint TEXT,
                    domain TEXT,
                    edited INTEGER DEFAULT 0,
                    distinguished TEXT,
                    media_json TEXT,                 -- adjuntos (imágenes/vídeo/galería) en JSON
                    urls TEXT,                       -- enlaces detectados en JSON
                    crosspost_parent TEXT,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reddit_posts_acc ON reddit_posts(account_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS reddit_timeline_posts (
                    account_id INTEGER NOT NULL,
                    timeline TEXT NOT NULL,          -- clave del buffer
                    post_db_id TEXT NOT NULL,
                    position INTEGER DEFAULT 0,
                    PRIMARY KEY(account_id, timeline, post_db_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY(post_db_id) REFERENCES reddit_posts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reddit_tl "
                       "ON reddit_timeline_posts(account_id, timeline, position)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS reddit_comments (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<fullname>"
                    account_id INTEGER NOT NULL,
                    fullname TEXT NOT NULL,          -- t1_<base36>
                    comment_id TEXT,
                    link_id TEXT,                    -- fullname de la submission (t3_...)
                    parent_id TEXT,                  -- fullname del padre (t1_ o t3_)
                    subreddit TEXT,
                    author TEXT,
                    body TEXT,
                    created_utc REAL DEFAULT 0,
                    score INTEGER DEFAULT 0,
                    depth INTEGER DEFAULT 0,
                    permalink TEXT,
                    saved INTEGER DEFAULT 0,
                    likes INTEGER,
                    edited INTEGER DEFAULT 0,
                    stickied INTEGER DEFAULT 0,
                    is_submitter INTEGER DEFAULT 0,
                    distinguished TEXT,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reddit_comments_acc ON reddit_comments(account_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reddit_comments_link "
                       "ON reddit_comments(account_id, link_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS reddit_timeline_comments (
                    account_id INTEGER NOT NULL,
                    timeline TEXT NOT NULL,
                    comment_db_id TEXT NOT NULL,
                    position INTEGER DEFAULT 0,
                    PRIMARY KEY(account_id, timeline, comment_db_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY(comment_db_id) REFERENCES reddit_comments(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reddit_tlc "
                       "ON reddit_timeline_comments(account_id, timeline, position)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS reddit_subreddits (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<nombre_en_minúsculas>"
                    account_id INTEGER NOT NULL,
                    name TEXT NOT NULL,              -- nombre del subreddit (sin r/)
                    title TEXT,
                    public_description TEXT,
                    subscribers INTEGER DEFAULT 0,
                    over_18 INTEGER DEFAULT 0,
                    user_is_subscriber INTEGER DEFAULT 0,
                    user_is_moderator INTEGER DEFAULT 0,
                    subreddit_type TEXT,             -- public, restricted, private, user...
                    icon_url TEXT,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reddit_subs_acc ON reddit_subreddits(account_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS reddit_messages (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<fullname>"
                    account_id INTEGER NOT NULL,
                    fullname TEXT NOT NULL,          -- t4_ (mensaje) o t1_ (respuesta/mención)
                    kind TEXT,                       -- t1 | t4
                    msg_type TEXT,                   -- message, comment_reply, post_reply,
                                                     --   username_mention, sent
                    author TEXT,
                    dest TEXT,
                    subject TEXT,
                    body TEXT,
                    context TEXT,                    -- permalink del comentario (respuestas)
                    subreddit TEXT,
                    created_utc REAL DEFAULT 0,
                    new INTEGER DEFAULT 0,           -- 1 = no leído
                    parent_id TEXT,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reddit_msgs_acc "
                       "ON reddit_messages(account_id, msg_type)")
        except Exception as e:
            log.error(f"Error al crear las tablas de Reddit: {e}")
        try:
            db.execute("PRAGMA user_version = 16")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=16: {e}")

    if version < 17:
        # ------------------------------------------------------------------
        # PeerTube (10ª red). Cliente de VÍDEO federado y MULTI-INSTANCIA. Cada cuenta vive en su
        # instancia (URL) con su versión y capacidades. Modelo de caché por cuenta + relación
        # buffer↔contenido, como Reddit/Bluesky:
        #  - peertube_instancias: caché de instancia y sus capacidades (no cuelga de una cuenta).
        #  - peertube_cuenta_meta: datos NO secretos de la cuenta (instancia, usuario, rol). El token
        #    va cifrado en security, no aquí.
        #  - peertube_videos + peertube_video_buffers: caché de vídeos y relación con cada sección/
        #    buffer ("recientes", "populares", "sub:<canal>", "search:<texto>"...).
        #  - peertube_canales / peertube_cuentas: caché de canales y cuentas remotas.
        #  - peertube_listas / peertube_lista_items: listas de reproducción y sus elementos.
        #  - peertube_comentarios: árbol de comentarios de un vídeo (parent_id, level).
        #  - peertube_notificaciones: notificaciones.
        #  - peertube_historial: historial + posición de reproducción (resume).
        #  - peertube_busquedas_guardadas / peertube_cache: búsquedas fijadas y caché genérica.
        # Todo con ON DELETE CASCADE sobre accounts. Fases posteriores añadirán tablas propias
        # (subidas, descargas, importaciones, directos, subtítulos...) en migraciones siguientes.
        # ------------------------------------------------------------------
        try:
            db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('peertube', 'PeerTube', 1)")
        except Exception as e:
            log.error(f"No se pudo registrar el servicio PeerTube: {e}")
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_instancias (
                    host TEXT PRIMARY KEY,           -- host normalizado (sin esquema ni barra final)
                    name TEXT,
                    version TEXT,
                    signup_allowed INTEGER DEFAULT 0,
                    signup_requires_approval INTEGER DEFAULT 0,
                    live_enabled INTEGER DEFAULT 0,
                    import_videos_enabled INTEGER DEFAULT 0,
                    user_video_quota INTEGER DEFAULT 0,
                    nsfw_policy TEXT,
                    capabilities_json TEXT,          -- config pública completa en JSON
                    fetched_at TEXT
                )""")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_cuenta_meta (
                    account_id INTEGER PRIMARY KEY,
                    instancia TEXT,                  -- host de la instancia de esta cuenta
                    username TEXT,
                    rol TEXT,                        -- user | moderator | administrator
                    ultima_sync TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_videos (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<uuid>"
                    account_id INTEGER NOT NULL,
                    uuid TEXT NOT NULL,
                    short_uuid TEXT,
                    video_id INTEGER,                -- id numérico en su instancia
                    name TEXT,                       -- título
                    description TEXT,
                    channel_name TEXT,               -- handle del canal (name)
                    channel_display TEXT,
                    account_name TEXT,               -- handle de la cuenta propietaria
                    account_host TEXT,
                    instance_host TEXT,              -- instancia de origen del vídeo
                    duration INTEGER DEFAULT 0,      -- segundos
                    views INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    dislikes INTEGER DEFAULT 0,
                    category INTEGER,
                    licence INTEGER,
                    language TEXT,
                    privacy INTEGER,                 -- 1=público 2=no listado 3=privado 4=interno
                    nsfw INTEGER DEFAULT 0,
                    is_live INTEGER DEFAULT 0,
                    state INTEGER,                   -- estado de publicación/transcodificación
                    published_at TEXT,
                    updated_at TEXT,
                    thumbnail_url TEXT,
                    preview_url TEXT,
                    url TEXT,
                    embed_url TEXT,
                    files_json TEXT,                 -- fuentes de reproducción (files/HLS) en JSON
                    is_local INTEGER DEFAULT 0,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_videos_acc ON peertube_videos(account_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_videos_uuid ON peertube_videos(account_id, uuid)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_video_buffers (
                    account_id INTEGER NOT NULL,
                    buffer TEXT NOT NULL,            -- clave de la sección/buffer
                    video_db_id TEXT NOT NULL,
                    position INTEGER DEFAULT 0,
                    PRIMARY KEY(account_id, buffer, video_db_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY(video_db_id) REFERENCES peertube_videos(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_vbuf "
                       "ON peertube_video_buffers(account_id, buffer, position)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_canales (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<handle>"
                    account_id INTEGER NOT NULL,
                    name TEXT NOT NULL,              -- handle (name@host o name)
                    display_name TEXT,
                    description TEXT,
                    support TEXT,
                    followers INTEGER DEFAULT 0,
                    host TEXT,
                    avatar_url TEXT,
                    is_local INTEGER DEFAULT 0,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_canales_acc ON peertube_canales(account_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_cuentas (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<handle>"
                    account_id INTEGER NOT NULL,
                    name TEXT NOT NULL,              -- handle (name@host o name)
                    display_name TEXT,
                    description TEXT,
                    followers INTEGER DEFAULT 0,
                    host TEXT,
                    avatar_url TEXT,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_cuentas_acc ON peertube_cuentas(account_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_listas (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<uuid>"
                    account_id INTEGER NOT NULL,
                    uuid TEXT NOT NULL,
                    short_uuid TEXT,
                    display_name TEXT,
                    description TEXT,
                    privacy INTEGER,
                    video_count INTEGER DEFAULT 0,
                    owner_name TEXT,
                    is_local INTEGER DEFAULT 0,
                    thumbnail_url TEXT,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_listas_acc ON peertube_listas(account_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_lista_items (
                    account_id INTEGER NOT NULL,
                    playlist_db_id TEXT NOT NULL,
                    element_id INTEGER,              -- id del elemento dentro de la lista
                    video_uuid TEXT NOT NULL,
                    title TEXT,
                    position INTEGER DEFAULT 0,
                    start_timestamp INTEGER,
                    stop_timestamp INTEGER,
                    fetched_at TEXT,
                    PRIMARY KEY(account_id, playlist_db_id, video_uuid),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY(playlist_db_id) REFERENCES peertube_listas(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_litems "
                       "ON peertube_lista_items(account_id, playlist_db_id, position)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_comentarios (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<comment_id>"
                    account_id INTEGER NOT NULL,
                    video_uuid TEXT NOT NULL,
                    comment_id INTEGER NOT NULL,
                    thread_id INTEGER,               -- id del hilo raíz
                    parent_id INTEGER,               -- id del comentario padre (NULL = raíz)
                    account_name TEXT,               -- autor (handle)
                    account_host TEXT,
                    account_display TEXT,
                    text_html TEXT,
                    text TEXT,                       -- HTML convertido a texto accesible
                    created_at TEXT,
                    total_replies INTEGER DEFAULT 0,
                    is_deleted INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_coment "
                       "ON peertube_comentarios(account_id, video_uuid)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_notificaciones (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<notif_id>"
                    account_id INTEGER NOT NULL,
                    notif_id INTEGER NOT NULL,
                    type INTEGER,                    -- tipo de notificación de PeerTube
                    read INTEGER DEFAULT 0,
                    object_json TEXT,                -- objeto relacionado en JSON
                    created_at TEXT,
                    fetched_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_notif "
                       "ON peertube_notificaciones(account_id, read)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_historial (
                    id TEXT PRIMARY KEY,             -- "<account_id>:<video_uuid>"
                    account_id INTEGER NOT NULL,
                    video_uuid TEXT NOT NULL,
                    title TEXT,
                    position_seconds REAL DEFAULT 0, -- posición de reproducción (resume)
                    duration REAL DEFAULT 0,
                    updated_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_peertube_hist ON peertube_historial(account_id)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_busquedas_guardadas (
                    account_id INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    filtros_json TEXT,
                    creada_at TEXT,
                    PRIMARY KEY(account_id, query),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
            db.execute("""
                CREATE TABLE IF NOT EXISTS peertube_cache (
                    account_id INTEGER NOT NULL,
                    clave TEXT NOT NULL,
                    valor_json TEXT,
                    expira_at TEXT,
                    PRIMARY KEY(account_id, clave),
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )""")
        except Exception as e:
            log.error(f"Error al crear las tablas de PeerTube: {e}")
        try:
            db.execute("PRAGMA user_version = 17")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=17: {e}")

    if version < 18:
        # ------------------------------------------------------------------
        # Telegram: PUNTO DE LECTURA REAL por chat (last_read_message_id).
        #
        # Antes, al abrir un chat, la frontera entre leído y no leído se DEDUCÍA restando
        # unread_count al número de mensajes cargados. Eso fallaba: unread_count viene del servidor
        # y NO cuenta tus mensajes salientes ni los de servicio, pero esos sí ocupan una fila en la
        # lista, así que la frontera retrocedía y te dejaba varios mensajes por detrás del último
        # que habías leído de verdad. Ahora se guarda el id EXACTO del último mensaje leído y esa
        # marca manda; el conteo solo se usa como respaldo la primera vez que se abre un chat.
        # ------------------------------------------------------------------
        try:
            cols = {r[1] for r in db.query("PRAGMA table_info(telegram_chats)")}
            if cols and "last_read_message_id" not in cols:
                db.execute("ALTER TABLE telegram_chats ADD COLUMN last_read_message_id INTEGER DEFAULT 0")
        except Exception as e:
            log.error(f"No se pudo añadir last_read_message_id a telegram_chats: {e}")
        try:
            db.execute("PRAGMA user_version = 18")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=18: {e}")

    if version < 19:
        # ------------------------------------------------------------------
        # Mastodon: PRIVACIDAD DE LOS HILOS. El editor de hilos publicaba siempre en público
        # porque no había dónde elegir la visibilidad (no se podía mandar un hilo en privado).
        # Ahora se guarda con el hilo, y es imprescindible que se PERSISTA: un hilo directo que
        # falle a medias se reanuda desde "Hilos guardados", y sin esto se reanudaría en público.
        #  - visibility: public / unlisted / private / direct.
        #  - spoiler_text: aviso de contenido común a todas las publicaciones del hilo.
        #  - mentions: destinatarios que deben ir en CADA publicación (Mastodon entrega los
        #    mensajes directos solo a las cuentas mencionadas en cada uno).
        # ------------------------------------------------------------------
        try:
            cols = {r[1] for r in db.query("PRAGMA table_info(mastodon_threads)")}
            if cols:
                for col, ddl in (("visibility", "TEXT DEFAULT 'public'"),
                                 ("spoiler_text", "TEXT"),
                                 ("mentions", "TEXT")):
                    if col not in cols:
                        db.execute(f"ALTER TABLE mastodon_threads ADD COLUMN {col} {ddl}")
        except Exception as e:
            log.error(f"No se pudieron añadir las columnas de privacidad a mastodon_threads: {e}")
        try:
            db.execute("PRAGMA user_version = 19")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=19: {e}")

    if version < 20:
        # ------------------------------------------------------------------
        # W SOCIAL (11ª red). Red europea de micromensajes sobre AT Protocol (la misma tecnología
        # de Bluesky) operada por W Social AB. Sus lexicons son EXACTAMENTE los app.bsky.* /
        # com.atproto.* de referencia —comprobado contra su fork del repositorio oficial: ni un
        # lexicon propio—, así que se usa la misma biblioteca `atproto` que Bluesky. Lo que cambia
        # es el SERVIDOR: no se fija ninguno, se resuelve la identidad del usuario (handle -> DID
        # -> PDS) y se habla con el suyo.
        #
        # El modelo replica el de Bluesky (caché de contenido + relación buffer<->contenido), con
        # tablas propias para que las dos redes conviivan sin tocarse:
        #  - wsocial_posts / wsocial_timeline_posts: publicaciones y a qué buffer pertenecen.
        #  - wsocial_notifications, wsocial_actors (seguidores, bloqueados, miembros de lista...),
        #    wsocial_feeds (feeds, listas y paquetes de inicio guardados).
        #  - wsocial_convos / wsocial_messages: mensajes directos y GRUPOS de chat.
        #  - wsocial_drafts: borradores guardados en el servidor (app.bsky.draft).
        #  - wsocial_outbox: publicaciones en vuelo (reconciliación anti-duplicados).
        #  - wsocial_posts_fts: índice de búsqueda.
        # ------------------------------------------------------------------
        try:
            db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('wsocial', 'W Social', 1)")
        except Exception as e:
            log.error(f"No se pudo registrar el servicio W Social: {e}")
        try:
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_posts (
                id TEXT PRIMARY KEY,              -- "<account_id>:<uri>"
                account_id INTEGER NOT NULL,
                uri TEXT NOT NULL,
                cid TEXT NOT NULL,
                author_did TEXT,
                author_handle TEXT,
                author_name TEXT,
                content TEXT,
                created_at TEXT,
                indexed_at TEXT,
                langs TEXT,
                labels TEXT,
                reply_root_uri TEXT,
                reply_root_cid TEXT,
                reply_parent_uri TEXT,
                reply_parent_cid TEXT,
                quote_uri TEXT,
                like_count INTEGER DEFAULT 0,
                repost_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                quote_count INTEGER DEFAULT 0,
                liked_uri TEXT,
                reposted_uri TEXT,
                bookmarked INTEGER DEFAULT 0,
                media_json TEXT,
                external_json TEXT,
                urls_json TEXT,
                repost_by_name TEXT,
                repost_by_handle TEXT,
                verificado INTEGER DEFAULT 0,     -- autor con verificación (app.bsky.graph.verification)
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_ws_posts_acc ON wsocial_posts(account_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_ws_posts_indexed ON wsocial_posts(indexed_at)")
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_timeline_posts (
                account_id INTEGER NOT NULL,
                timeline TEXT NOT NULL,
                post_db_id TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                PRIMARY KEY(account_id, timeline, post_db_id),
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY(post_db_id) REFERENCES wsocial_posts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""CREATE INDEX IF NOT EXISTS idx_ws_timeline_order
                          ON wsocial_timeline_posts(account_id, timeline, position)""")
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_notifications (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                uri TEXT NOT NULL,
                cid TEXT,
                reason TEXT NOT NULL,
                reason_subject TEXT,
                subject_uri TEXT,                 -- at:// de la PUBLICACIÓN a la que se refiere
                author_did TEXT,
                author_handle TEXT,
                author_name TEXT,
                texto TEXT,                       -- texto de la publicación que la origina
                is_read INTEGER DEFAULT 0,
                indexed_at TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_ws_notif_acc ON wsocial_notifications(account_id)")
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_actors (
                id TEXT PRIMARY KEY,              -- "<account_id>:<bucket>:<did>"
                account_id INTEGER NOT NULL,
                bucket TEXT NOT NULL,             -- followers, follows, blocks, mutes, list:<uri>...
                did TEXT NOT NULL,
                handle TEXT,
                display_name TEXT,
                description TEXT,
                following_uri TEXT,
                blocking_uri TEXT,
                muted INTEGER DEFAULT 0,
                verificado INTEGER DEFAULT 0,
                position INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_ws_actors_bucket ON wsocial_actors(account_id, bucket)")
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_feeds (
                id TEXT PRIMARY KEY,              -- "<account_id>:<kind>:<uri>"
                account_id INTEGER NOT NULL,
                kind TEXT NOT NULL,               -- feed | list | starterpack | labeler
                uri TEXT NOT NULL,
                name TEXT,
                description TEXT,
                creator_handle TEXT,
                pinned INTEGER DEFAULT 0,
                position INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_convos (
                id TEXT PRIMARY KEY,              -- "<account_id>:<convo_id>"
                account_id INTEGER NOT NULL,
                convo_id TEXT NOT NULL,
                members_json TEXT,
                title TEXT,
                unread_count INTEGER DEFAULT 0,
                muted INTEGER DEFAULT 0,
                last_message TEXT,
                last_message_date TEXT,
                is_group INTEGER DEFAULT 0,       -- conversación de GRUPO (chat.bsky.group)
                group_role TEXT,                  -- tu papel en el grupo (owner, admin, member)
                is_request INTEGER DEFAULT 0,     -- solicitud de mensaje aún sin aceptar
                locked INTEGER DEFAULT 0,         -- grupo cerrado (no admite mensajes)
                last_read_message_id TEXT,        -- punto de lectura exacto (como en Telegram)
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_messages (
                id TEXT PRIMARY KEY,              -- "<account_id>:<convo_id>:<message_id>"
                account_id INTEGER NOT NULL,
                convo_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender_did TEXT,
                sender_name TEXT,
                text TEXT,
                embed_json TEXT,
                reactions_json TEXT,
                sent_at TEXT,
                outgoing INTEGER DEFAULT 0,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""CREATE INDEX IF NOT EXISTS idx_ws_msgs_convo_fecha
                          ON wsocial_messages(account_id, convo_id, sent_at)""")
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_drafts (
                id TEXT PRIMARY KEY,              -- "<account_id>:<draft_id>"
                account_id INTEGER NOT NULL,
                draft_id TEXT NOT NULL,           -- identificador en el servidor
                texto TEXT,
                payload_json TEXT,                -- contenido completo tal cual lo guarda el servidor
                updated_at TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )
            """)
            db.execute("""
            CREATE TABLE IF NOT EXISTS wsocial_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                rkey TEXT NOT NULL,
                collection TEXT NOT NULL DEFAULT 'app.bsky.feed.post',
                payload_json TEXT,
                estado TEXT NOT NULL DEFAULT 'pending',
                uri TEXT,
                error TEXT,
                intentos INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
            )""")
        except Exception as e:
            log.error(f"Error al crear las tablas de W Social: {e}")
        try:
            db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS wsocial_posts_fts USING fts5(
                post_db_id UNINDEXED, author_name, author_handle, content,
                tokenize='unicode61 remove_diacritics 2'
            )
            """)
        except Exception as e:
            log.debug(f"No se pudo crear wsocial_posts_fts: {e}")
        try:
            db.execute("PRAGMA user_version = 20")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=20: {e}")

    if version < 21:
        # ------------------------------------------------------------------
        # NOTIFICACIONES DE AT PROTOCOL (Bluesky y W Social): a qué publicación se refieren.
        #
        # La notificación que devuelve listNotifications trae en `uri` el REGISTRO que la
        # provoca, no la publicación afectada: para un "me gusta" eso es
        # at://<did>/app.bsky.feed.like/<rkey>, y pedir la conversación de ese URI devuelve
        # 400 NotFound ("Post not found") porque no es un post. Solo en mención, respuesta y
        # cita el `uri` es de verdad app.bsky.feed.post.
        #  - subject_uri: URI de la PUBLICACIÓN implicada (de reasonSubject o del propio uri).
        #  - content (solo Bluesky; en W Social ya existe `texto`): texto de esa publicación,
        #    para que la lista diga a qué mensaje corresponde cada notificación.
        # ------------------------------------------------------------------
        for tabla, columnas in (("bluesky_notifications", (("subject_uri", "TEXT"), ("content", "TEXT"))),
                                ("wsocial_notifications", (("subject_uri", "TEXT"),))):
            try:
                cols = {r[1] for r in db.query(f"PRAGMA table_info({tabla})")}
                if not cols:
                    continue
                for col, tipo in columnas:
                    if col not in cols:
                        db.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
            except Exception as e:
                log.error(f"No se pudieron añadir las columnas de asunto a {tabla}: {e}")
        try:
            db.execute("PRAGMA user_version = 21")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=21: {e}")

    if version < 22:
        # ------------------------------------------------------------------
        # SINCRONIZACIÓN DE RSS CON SERVIDOR: base para conciliar en tres vías.
        #
        # Hasta ahora la sincronización solo sabía el estado LOCAL y el REMOTO, y con eso no
        # se puede saber quién cambió qué: si un artículo está leído aquí y no en el servidor,
        # ¿lo acabo de leer aquí (hay que enviarlo) o lo han marcado como no leído en la web
        # (hay que aplicarlo)? Esta tabla guarda el ÚLTIMO ESTADO CONOCIDO DEL SERVIDOR por
        # artículo, que es la referencia que permite distinguirlo, y el item_id del servidor,
        # necesario para poder escribir los cambios con edit-tag.
        # ------------------------------------------------------------------
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS rss_sync_items (
                    account_id INTEGER NOT NULL,
                    article_id TEXT NOT NULL,
                    item_id TEXT,
                    read_srv INTEGER DEFAULT 0,
                    star_srv INTEGER DEFAULT 0,
                    PRIMARY KEY (account_id, article_id)
                )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_sync_items_cuenta "
                       "ON rss_sync_items(account_id)")
        except Exception as e:
            log.error(f"No se pudo crear la tabla de sincronización RSS: {e}")
        try:
            db.execute("PRAGMA user_version = 22")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=22: {e}")

    if version < 23:
        # ------------------------------------------------------------------
        # RSS: ARTÍCULO COMPLETO DESCARGADO DE LA WEB.
        #
        # El lector limpio descarga ahora la página del artículo cuando el feed solo trae un
        # resumen. fulltext_at guarda CUÁNDO se intentó (epoch; NULL = nunca) para no repetir
        # la descarga cada vez que se abre el mismo artículo; fulltext_metodo, qué extractor
        # dio el texto guardado ("" si el intento no sacó nada útil y se conservó el resumen).
        # ------------------------------------------------------------------
        try:
            cols = {r[1] for r in db.query("PRAGMA table_info(rss_articles)")}
            if cols:
                if "fulltext_at" not in cols:
                    db.execute("ALTER TABLE rss_articles ADD COLUMN fulltext_at INTEGER")
                if "fulltext_metodo" not in cols:
                    db.execute("ALTER TABLE rss_articles ADD COLUMN fulltext_metodo TEXT")
        except Exception as e:
            log.error(f"No se pudieron añadir las columnas de texto completo a rss_articles: {e}")
        # BLUESKY: "le gusta a N personas que sigues" (viewer.knownLikers, lexicon de 2026-08):
        # JSON {"count": n, "nombres": [...hasta 5]} o vacío.
        try:
            cols = {r[1] for r in db.query("PRAGMA table_info(bluesky_posts)")}
            if cols and "known_likers" not in cols:
                db.execute("ALTER TABLE bluesky_posts ADD COLUMN known_likers TEXT")
        except Exception as e:
            log.error(f"No se pudo añadir known_likers a bluesky_posts: {e}")
        try:
            db.execute("PRAGMA user_version = 23")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=23: {e}")

    if version < 24:
        # ------------------------------------------------------------------
        # BLUESKY Y W SOCIAL: A QUIÉN RESPONDE UNA PUBLICACIÓN.
        #
        # Hasta ahora solo se guardaba la URI del padre, así que la lista decía "Respuesta" sin
        # decir a quién. El servidor entrega el autor del padre hidratado en el FeedViewPost
        # (reply.parent.author); se guarda su handle y su nombre. Vacío = no se sabe (la lista
        # intenta resolverlo con la caché local al pintar).
        # ------------------------------------------------------------------
        for tabla in ("bluesky_posts", "wsocial_posts"):
            try:
                cols = {r[1] for r in db.query(f"PRAGMA table_info({tabla})")}
                if cols and "reply_parent_handle" not in cols:
                    db.execute(f"ALTER TABLE {tabla} ADD COLUMN reply_parent_handle TEXT")
                if cols and "reply_parent_name" not in cols:
                    db.execute(f"ALTER TABLE {tabla} ADD COLUMN reply_parent_name TEXT")
            except Exception as e:
                log.error(f"No se pudieron añadir las columnas de respuesta a {tabla}: {e}")
        try:
            db.execute("PRAGMA user_version = 24")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=24: {e}")

    if version < 25:
        # ------------------------------------------------------------------
        # X (TWITTER): A QUIÉN RESPONDE, TUIT CITADO Y ENCUESTA.
        #
        # - in_reply_to_screen_name: el @usuario al que responde (X lo da en el propio tuit), para
        #   leer "En respuesta a @x" como en Bluesky y W Social.
        # - quoted_author / quoted_text: el tuit CITADO se leía solo como un id; ahora se guarda
        #   su autor y su texto para leerlo con la publicación.
        # - poll_json: la encuesta (identificador de tarjeta, opciones, votos, cierre y tu voto),
        #   para mostrarla y poder votar sin volver a pedir el tuit.
        # ------------------------------------------------------------------
        try:
            cols = {r[1] for r in db.query("PRAGMA table_info(x_tweets)")}
            for col in ("in_reply_to_screen_name", "quoted_author", "quoted_text", "poll_json"):
                if cols and col not in cols:
                    db.execute(f"ALTER TABLE x_tweets ADD COLUMN {col} TEXT")
        except Exception as e:
            log.error(f"No se pudieron añadir las columnas nuevas a x_tweets: {e}")
        try:
            db.execute("PRAGMA user_version = 25")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=25: {e}")

    if version < 26:
        # ------------------------------------------------------------------
        # BLUESKY: DATOS DE MODERACIÓN COMPLETOS (auditoría de filtros 2026-10-17).
        #
        # mod_json (JSON compacto, ver bluesky_moddatos.py): etiquetas con su ORIGEN (para
        # descartar las de servicios no suscritos y aplicar sus definiciones propias), las de
        # la cuenta/perfil aparte de las del post, la publicación citada, y los textos que
        # miran las palabras silenciadas (etiquetas, alt, tarjeta). La columna `labels` (CSV
        # de valores) se conserva por compatibilidad. ALTER TABLE: sin recrear tablas.
        # ------------------------------------------------------------------
        for tabla in ("bluesky_posts", "bluesky_notifications"):
            try:
                cols = {r[1] for r in db.query(f"PRAGMA table_info({tabla})")}
                if cols and "mod_json" not in cols:
                    db.execute(f"ALTER TABLE {tabla} ADD COLUMN mod_json TEXT")
            except Exception as e:
                log.error(f"No se pudo añadir mod_json a {tabla}: {e}")
        try:
            db.execute("PRAGMA user_version = 26")
        except Exception as e:
            log.debug(f"No se pudo fijar user_version=26: {e}")


def _migrar_rss_auditoria_v10():
    """Migración RSS de la auditoría 2026-07-11. Idempotente y transaccional por bloques."""
    from app.core.url_utils import normalizar_url

    # Primera cuenta RSS (para adoptar las filas globales existentes). Puede no haber ninguna.
    fila = db.query_one(
        "SELECT id FROM accounts WHERE service_id = 'rss' ORDER BY position, id LIMIT 1")
    cuenta_defecto = fila[0] if fila else None

    # 1) rss_categories: cada categoría pertenece a UNA cuenta. La tabla vieja tenía
    #    name UNIQUE global, así que hay que RECREARLA (SQLite no quita constraints).
    #    Una categoría usada por feeds de VARIAS cuentas se duplica (una copia por
    #    cuenta) y los feeds se reasignan a su copia.
    try:
        cols = [r[1] for r in db.query("PRAGMA table_info(rss_categories)")]
    except Exception:
        cols = []
    if cols and "account_id" not in cols:
        log.info("Migrando rss_categories a propiedad por cuenta (auditoría RSS-01)...")
        try:
            # OJO: claves foráneas FUERA durante la recreación. Con foreign_keys=ON, el
            # DROP de la tabla vieja dispara el ON DELETE SET NULL de rss_feeds y borra
            # la categoría recién reasignada de TODOS los feeds (detectado en el ensayo
            # de migración con la base real). El PRAGMA no funciona dentro de una
            # transacción, por eso va antes y se restaura después.
            db.execute("PRAGMA foreign_keys = OFF")
            with db.transaccion():
                db.execute("""
                    CREATE TABLE IF NOT EXISTS rss_categories_v10 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id INTEGER,
                        name TEXT NOT NULL,
                        parent_id INTEGER,
                        position INTEGER DEFAULT 0,
                        UNIQUE(account_id, parent_id, name),
                        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                    )""")
                tiene_pos = "position" in cols
                tiene_padre = "parent_id" in cols
                # TODO el estado previo se lee ANTES y se trabaja EN MEMORIA: las lecturas
                # de la tabla nueva dentro de la transacción van por la conexión de
                # LECTURA y no ven las filas sin confirmar (los feeds quedaban sin carpeta).
                cats = db.query(
                    f"SELECT id, name, {'parent_id' if tiene_padre else 'NULL'}, "
                    f"{'COALESCE(position,0)' if tiene_pos else '0'} FROM rss_categories")
                feeds_con_cat = db.query(
                    "SELECT id, account_id, category_id FROM rss_feeds "
                    "WHERE category_id IS NOT NULL")
                # Cuentas que usan cada categoría (por sus feeds).
                uso = {}
                for _fid, acc, cid in feeds_con_cat:
                    if acc not in uso.setdefault(cid, []):
                        uso[cid].append(acc)
                padre_de = {cid: padre for cid, _n, padre, _p in cats}
                # nuevo_id[(cat_vieja, cuenta)] = id nuevo. En el esquema viejo el nombre
                # era UNIQUE global, así que no puede haber colisiones al insertar.
                nuevo_id = {}
                for cid, nombre, padre, pos in cats:
                    cuentas = uso.get(cid) or ([cuenta_defecto] if cuenta_defecto else [None])
                    for acc in cuentas:
                        nid = db.execute_insert(
                            "INSERT INTO rss_categories_v10 "
                            "(account_id, name, parent_id, position) VALUES (?, ?, ?, ?)",
                            (acc, nombre, padre, pos))
                        if nid:
                            nuevo_id[(cid, acc)] = nid
                # parent_id apunta aún a ids VIEJOS: remapear a la copia de la misma cuenta
                # (o a cualquier copia si esa cuenta no tiene una del padre).
                for (cid, acc), nid in nuevo_id.items():
                    padre_viejo = padre_de.get(cid)
                    if padre_viejo is None:
                        continue
                    nuevo_padre = nuevo_id.get((padre_viejo, acc))
                    if nuevo_padre is None:
                        candidatos = [v for (c, _a), v in nuevo_id.items() if c == padre_viejo]
                        nuevo_padre = candidatos[0] if candidatos else None
                    db.execute("UPDATE rss_categories_v10 SET parent_id = ? WHERE id = ?",
                               (nuevo_padre, nid))
                # Reasignar los feeds a la copia de SU cuenta.
                for f_id, f_acc, f_cat in feeds_con_cat:
                    nid = nuevo_id.get((f_cat, f_acc))
                    db.execute("UPDATE rss_feeds SET category_id = ? WHERE id = ?", (nid, f_id))
                db.execute("DROP TABLE rss_categories")
                db.execute("ALTER TABLE rss_categories_v10 RENAME TO rss_categories")
                db.execute("CREATE INDEX IF NOT EXISTS idx_rss_categories_account "
                           "ON rss_categories(account_id)")
        except Exception as e:
            log.error(f"Error al migrar rss_categories por cuenta: {e}")
        finally:
            try:
                db.execute("PRAGMA foreign_keys = ON")
            except Exception:
                pass

    # 2) Alertas y búsquedas guardadas: propiedad por cuenta (RSS-02). Las filas
    #    existentes se ADOPTAN por la primera cuenta RSS (sin duplicarlas en silencio).
    for tabla in ("rss_keyword_alerts", "rss_saved_searches"):
        try:
            cols_t = [r[1] for r in db.query(f"PRAGMA table_info({tabla})")]
            if cols_t and "account_id" not in cols_t:
                log.info(f"Añadiendo account_id a {tabla} (auditoría RSS-02)...")
                db.execute(f"ALTER TABLE {tabla} ADD COLUMN account_id INTEGER")
                if cuenta_defecto is not None:
                    db.execute(f"UPDATE {tabla} SET account_id = ? WHERE account_id IS NULL",
                               (cuenta_defecto,))
                db.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_account ON {tabla}(account_id)")
        except Exception as e:
            log.error(f"Error al migrar {tabla} por cuenta: {e}")

    # 3) rss_feeds: estado de sondeo/salud (RSS-05/11) y URL normalizada (RSS-21).
    # Detección de columnas por PRAGMA (sin el patrón de SELECT fallidos que
    # ensuciaba el log con errores; lo pide la propia auditoría, punto 10).
    try:
        cols_feeds = {r[1] for r in db.query("PRAGMA table_info(rss_feeds)")}
    except Exception:
        cols_feeds = set()
    for columna, tipo in (
            ("last_attempt_at", "TEXT"), ("last_success_at", "TEXT"),
            ("next_update_at", "TEXT"), ("fail_count", "INTEGER DEFAULT 0"),
            ("last_status", "INTEGER"), ("url_normalized", "TEXT")):
        if columna in cols_feeds:
            continue
        try:
            db.execute(f"ALTER TABLE rss_feeds ADD COLUMN {columna} {tipo}")
        except Exception as e:
            log.error(f"Error al añadir {columna} a rss_feeds: {e}")
    try:
        pendientes = db.query(
            "SELECT id, url FROM rss_feeds WHERE COALESCE(url_normalized,'') = ''")
        if pendientes:
            with db.transaccion():
                for f_id, f_url in pendientes:
                    db.execute("UPDATE rss_feeds SET url_normalized = ? WHERE id = ?",
                               (normalizar_url(f_url), f_id))
        # Unicidad por cuenta y URL normalizada; si la base ya tiene duplicados el índice
        # único fallaría, así que se degrada a índice normal (y se avisa en el log).
        try:
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rss_feeds_acc_urlnorm "
                       "ON rss_feeds(account_id, url_normalized)")
        except Exception:
            log.warning("Hay feeds duplicados por URL normalizada: se crea índice NO único.")
            db.execute("CREATE INDEX IF NOT EXISTS idx_rss_feeds_acc_urlnorm_nu "
                       "ON rss_feeds(account_id, url_normalized)")
    except Exception as e:
        log.error(f"Error al normalizar URLs de feeds: {e}")

    # 4) rss_articles: seguimiento de cambios del origen (RSS-14) y recuperación de
    #    filas sin cuenta (RSS-04: el catálogo completo de YouTube las dejaba a NULL).
    try:
        cols_arts = {r[1] for r in db.query("PRAGMA table_info(rss_articles)")}
    except Exception:
        cols_arts = set()
    for columna, tipo in (
            ("first_seen_at", "TEXT"), ("last_seen_at", "TEXT"),
            ("source_updated_at", "TEXT"), ("content_hash", "TEXT")):
        if columna in cols_arts:
            continue
        try:
            db.execute(f"ALTER TABLE rss_articles ADD COLUMN {columna} {tipo}")
        except Exception as e:
            log.error(f"Error al añadir {columna} a rss_articles: {e}")
    try:
        db.execute("""
            UPDATE rss_articles SET account_id =
                (SELECT account_id FROM rss_feeds WHERE rss_feeds.id = rss_articles.feed_id)
            WHERE account_id IS NULL""")
    except Exception as e:
        log.debug(f"No se pudieron recuperar artículos sin cuenta: {e}")

    # 5) Historial de lectura: índice por cuenta (RSS-03).
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_rss_reading_log_account "
                   "ON rss_reading_log(account_id, opened_at)")
    except Exception as e:
        log.debug(f"No se pudo crear el índice de historial por cuenta: {e}")

    # 6) Ajuste heredado sin consumidor: cleanup_days -> keep_articles_days (RSS-06).
    try:
        from app.core.config_manager import config as _cfg
        viejo = _cfg.get("rss", "cleanup_days", None)
        if viejo and not _cfg.get("rss", "keep_articles_days", 0):
            _cfg.set("rss", "keep_articles_days", int(viejo))
    except Exception as e:
        log.debug(f"No se pudo migrar cleanup_days: {e}")
