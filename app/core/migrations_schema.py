# -*- coding: utf-8 -*-
"""Esquema inicial (versión 1) de la base de datos SQLite.

Extraído de `migrations.py` para modularizar. Se importa desde `ejecutar_migraciones`.
"""

SCHEMA_V1 = [
    # Tabla de servicios
    """
    CREATE TABLE IF NOT EXISTS services (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        active INTEGER DEFAULT 1
    );
    """,
    # Tabla de cuentas
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        service_id TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        FOREIGN KEY(service_id) REFERENCES services(id) ON DELETE CASCADE
    );
    """,
    # Configuración de cuentas (clave/valor)
    """
    CREATE TABLE IF NOT EXISTS account_settings (
        account_id INTEGER,
        key TEXT,
        value TEXT,
        PRIMARY KEY(account_id, key),
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,
    # Timelines de Mastodon
    """
    CREATE TABLE IF NOT EXISTS mastodon_statuses (
        id TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL,
        status_id TEXT NOT NULL,
        uri TEXT NOT NULL,
        author_name TEXT,
        author_username TEXT,
        content TEXT,
        created_at TEXT,
        visibility TEXT,
        nsfw INTEGER DEFAULT 0,
        spoiler_text TEXT,
        favourited INTEGER DEFAULT 0,
        reblogged INTEGER DEFAULT 0,
        bookmarked INTEGER DEFAULT 0,
        media_json TEXT,
        urls_json TEXT,
        reblog_by_name TEXT,
        reblog_by_username TEXT,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,
    # Índices para statuses
    "CREATE INDEX IF NOT EXISTS idx_mastodon_statuses_acc ON mastodon_statuses(account_id);",
    "CREATE INDEX IF NOT EXISTS idx_mastodon_statuses_created ON mastodon_statuses(created_at);",
    
    # Relación de timelines y statuses
    """
    CREATE TABLE IF NOT EXISTS mastodon_timeline_statuses (
        account_id INTEGER NOT NULL,
        timeline TEXT NOT NULL,
        status_db_id TEXT NOT NULL,
        PRIMARY KEY(account_id, timeline, status_db_id),
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
        FOREIGN KEY(status_db_id) REFERENCES mastodon_statuses(id) ON DELETE CASCADE
    );
    """,
    
    # Notificaciones de Mastodon
    """
    CREATE TABLE IF NOT EXISTS mastodon_notifications (
        id TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL,
        notification_id TEXT NOT NULL,
        type TEXT NOT NULL,
        created_at TEXT,
        from_name TEXT,
        from_username TEXT,
        status_id TEXT,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,
    
    # Hilos de Mastodon locales
    """
    CREATE TABLE IF NOT EXISTS mastodon_threads (
        id TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        published INTEGER DEFAULT 0,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,
    # Ítems individuales de hilos locales
    """
    CREATE TABLE IF NOT EXISTS mastodon_thread_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        item_order INTEGER NOT NULL,
        content TEXT NOT NULL,
        media_paths TEXT, -- Rutas locales en JSON (formatos antiguos: separadas por comas)
        alt_texts TEXT,   -- Descripciones en JSON (formatos antiguos: separador \x1f)
        posted_status_id TEXT,
        posted_at TEXT,
        idem_key TEXT,    -- clave de idempotencia persistida ANTES del primer envío
        FOREIGN KEY(thread_id) REFERENCES mastodon_threads(id) ON DELETE CASCADE
    );
    """,
    
    # Borradores locales de Mastodon. content guarda el TEXTO plano (para listar y buscar);
    # borrador_json guarda el borrador completo del compositor (CW, privacidad, idioma,
    # adjuntos, encuesta y programación) y contexto el destino (respuesta/cita), ambos en JSON.
    # Las columnas visibility/sensitive/... antiguas se conservan por compatibilidad.
    """
    CREATE TABLE IF NOT EXISTS mastodon_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        visibility TEXT DEFAULT 'public',
        sensitive INTEGER DEFAULT 0,
        spoiler_text TEXT,
        media_paths TEXT,
        alt_texts TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        contexto TEXT,
        borrador_json TEXT,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,

    # Bandeja de SALIDA de Bluesky (auditoría 6.6): cada publicación registra aquí su rkey
    # idempotente y su carga ANTES de enviarse. Estados: pending (en camino), sent (confirmada,
    # con uri), unknown (timeout ambiguo sin reconciliar) y failed (fallida, reintetable con el
    # MISMO rkey: el PDS rechaza duplicados).
    """
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
    );
    """,

    # Conversaciones de mensajes directos de Mastodon (auditoría N6): la lista representa
    # conversaciones REALES del servidor (id propio, participantes, último estado y no
    # leído), no se infiere de los estados sueltos del buffer de directos.
    """
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
    );
    """,
    
    # Categorías de feeds RSS
    """
    CREATE TABLE IF NOT EXISTS rss_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );
    """,
    # Feeds RSS registrados
    """
    CREATE TABLE IF NOT EXISTS rss_feeds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        category_id INTEGER,
        update_interval INTEGER DEFAULT 30, -- En minutos
        active INTEGER DEFAULT 1,
        last_error TEXT,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE,
        FOREIGN KEY(category_id) REFERENCES rss_categories(id) ON DELETE SET NULL
    );
    """,
    # Artículos RSS cacheados
    """
    CREATE TABLE IF NOT EXISTS rss_articles (
        id TEXT PRIMARY KEY,
        feed_id INTEGER NOT NULL,
        guid TEXT,
        link TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT,
        author TEXT,
        published_at TEXT,
        read INTEGER DEFAULT 0,
        favorite INTEGER DEFAULT 0,
        has_media INTEGER DEFAULT 0,
        media_url TEXT,
        media_type TEXT,
        translated_content TEXT,
        ai_summary TEXT,
        urls_json TEXT,
        FOREIGN KEY(feed_id) REFERENCES rss_feeds(id) ON DELETE CASCADE
    );
    """,
    # Índices para artículos
    "CREATE INDEX IF NOT EXISTS idx_rss_articles_feed ON rss_articles(feed_id);",
    "CREATE INDEX IF NOT EXISTS idx_rss_articles_published ON rss_articles(published_at);",
    "CREATE INDEX IF NOT EXISTS idx_rss_articles_read ON rss_articles(read);",

    # Reglas automáticas para RSS
    """
    CREATE TABLE IF NOT EXISTS rss_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        pattern TEXT NOT NULL,             -- Texto o Regex
        field TEXT DEFAULT 'title',        -- title, content, author, all
        action TEXT NOT NULL,              -- mark_read, mark_favorite, categorize, play_sound
        action_value TEXT,                 -- valor asociado a la acción
        active INTEGER DEFAULT 1,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,
    
    # Registro de lectura de artículos RSS (historial + estadísticas de lectura)
    """
    CREATE TABLE IF NOT EXISTS rss_reading_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER,
        article_db_id TEXT NOT NULL,       -- rss_articles.id del artículo leído
        feed_id INTEGER,
        title TEXT,                        -- título cacheado (por si el artículo se borra)
        feed_title TEXT,
        opened_at TEXT NOT NULL            -- ISO de cuándo se abrió
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_rss_reading_log_when ON rss_reading_log(opened_at);",
    "CREATE INDEX IF NOT EXISTS idx_rss_reading_log_article ON rss_reading_log(article_db_id);",

    # Sincronización con servidor (Google Reader / Miniflux): último estado CONOCIDO del
    # servidor por artículo. Es la referencia que permite saber quién cambió qué (local o
    # remoto) y conciliar en los dos sentidos; item_id es el id del artículo en el servidor,
    # necesario para poder escribir allí los cambios hechos aquí.
    """
    CREATE TABLE IF NOT EXISTS rss_sync_items (
        account_id INTEGER NOT NULL,
        article_id TEXT NOT NULL,
        item_id TEXT,
        read_srv INTEGER DEFAULT 0,
        star_srv INTEGER DEFAULT 0,
        PRIMARY KEY (account_id, article_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_rss_sync_items_cuenta ON rss_sync_items(account_id);",

    # Caché de IA (Prompts y respuestas)
    """
    CREATE TABLE IF NOT EXISTS ai_cache (
        prompt_hash TEXT PRIMARY KEY,
        response TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    # Historial de operaciones de IA
    """
    CREATE TABLE IF NOT EXISTS ai_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        funcion TEXT NOT NULL,
        entrada TEXT,
        respuesta TEXT,
        created_at TEXT NOT NULL
    );
    """,
    
    # Historial de traducciones
    """
    CREATE TABLE IF NOT EXISTS translation_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text_hash TEXT NOT NULL,
        original_text TEXT NOT NULL,
        translated_text TEXT NOT NULL,
        source_lang TEXT NOT NULL,
        target_lang TEXT NOT NULL,
        provider TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_trans_hist_hash ON translation_history(text_hash);",
    
    # Perfiles y eventos de sonido
    """
    CREATE TABLE IF NOT EXISTS sound_events (
        event_id TEXT PRIMARY KEY,
        sound_path TEXT,
        volume REAL DEFAULT 1.0,
        active INTEGER DEFAULT 1
    );
    """,
    
    # Configuración de atajos de teclado personalizados
    """
    CREATE TABLE IF NOT EXISTS shortcuts (
        shortcut_id TEXT PRIMARY KEY,
        key_combination TEXT NOT NULL,
        description TEXT
    );
    """,
    
    # Chats de Telegram cacheados
    """
    CREATE TABLE IF NOT EXISTS telegram_chats (
        id TEXT PRIMARY KEY,          -- formato account_id:chat_id
        account_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        title TEXT,
        chat_type TEXT,               -- private, bot, group, supergroup, channel
        username TEXT,
        unread_count INTEGER DEFAULT 0,
        last_message TEXT,
        last_message_date TEXT,
        pinned INTEGER DEFAULT 0,
        muted INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0,
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_telegram_chats_acc ON telegram_chats(account_id);",
    # Mensajes de Telegram cacheados
    """
    CREATE TABLE IF NOT EXISTS telegram_messages (
        id TEXT PRIMARY KEY,          -- formato account_id:chat_id:message_id
        account_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        sender_name TEXT,             -- nombre mostrado del remitente (nombre y apellido, o renombre del contacto)
        sender_username TEXT,         -- @usuario del remitente, si lo tiene (campo {username} de la plantilla)
        sender_id INTEGER,
        text TEXT,
        media_type TEXT,              -- photo, video, audio, voice, document, sticker...
        media_path TEXT,             -- ruta local si se descargó
        date TEXT,
        outgoing INTEGER DEFAULT 0,
        reply_to INTEGER,
        edited INTEGER DEFAULT 0,
        duration REAL DEFAULT 0,
        file_name TEXT,
        forward_from TEXT,            -- nombre de quien lo reenvió originalmente, si es un reenvío
        views INTEGER DEFAULT 0,      -- número de vistas (publicaciones de canal)
        media_group_id INTEGER,       -- identificador de álbum: agrupa varios adjuntos del mismo envío
        reply_markup TEXT,            -- JSON del teclado del mensaje (botones de bot), si lo tiene
        FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_telegram_messages_chat ON telegram_messages(account_id, chat_id);",

    # Historial de reproducción multimedia
    """
    CREATE TABLE IF NOT EXISTS play_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media_url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        service_origin TEXT NOT NULL,
        account_origin_id INTEGER,
        last_position REAL DEFAULT 0.0,
        duration REAL DEFAULT 0.0,
        last_played_at TEXT NOT NULL,
        finished INTEGER DEFAULT 0
    );
    """
]

# Tablas virtuales FTS5
FTS5_SCHEMAS = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS mastodon_statuses_fts USING fts5(
        status_db_id UNINDEXED,
        author_name,
        author_username,
        content,
        spoiler_text,
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS rss_articles_fts USING fts5(
        article_db_id UNINDEXED,
        title,
        content,
        author,
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS translation_history_fts USING fts5(
        history_id UNINDEXED,
        original_text,
        translated_text,
        tokenize='unicode61 remove_diacritics 2'
    );
    """
]
