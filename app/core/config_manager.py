# -*- coding: utf-8 -*-
"""
Módulo para la gestión y persistencia de configuraciones de la aplicación.
Carga y guarda configuraciones en formato JSON con valores por defecto.
"""

import json
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict
from .paths import get_data_path

log = logging.getLogger(__name__)

# Valores por defecto para toda la aplicación
DEFAULTS: Dict[str, Any] = {
    "general": {
        "language": "es",
        "confirm_exit": True,
        "minimize_to_tray": False,
        "theme": "oscuro",
        "font_size": 11,
        "advanced_mode": False,
        "newest_first": True,
        # Mostrar marcas de tiempo relativas ("hace 5 minutos") en lugar de la
        # fecha y hora absolutas en las listas y la lectura (como TWBlue).
        "relative_times": True,
        # Iniciar minimizado en la bandeja del sistema (requiere minimize_to_tray).
        "start_minimized": False,
        "service_order": ["mastodon", "bluesky", "rss", "telegram"],
        # Servicios DESACTIVADOS por el usuario (lista vacía = todos activos). Un servicio
        # desactivado no aparece en el árbol ni en los menús ni en el alta de cuentas, y no se
        # sondea. Se aplica al reiniciar la app (igual que el cliente de X). X se controla aparte.
        "servicios_desactivados": [],
        "auto_update_enabled": True,
        "auto_update_seconds": 60,
        # Idioma del corrector ortográfico (es, en, fr, de, pt...)
        "spell_language": "es",
        # Nivel de registro (DEBUG, INFO, WARNING, ERROR)
        "log_level": "INFO",
        # Sonido y mensaje "listo" al terminar el arranque
        "ready_sound": True,
        "ready_message": True
    },
    "proxy": {
        # Conexión a través de un proxy (como TWBlue). Si enabled es True se aplican
        # estas variables a todas las peticiones de red al arrancar.
        "enabled": False,
        "type": "http",   # http, https, socks5
        "server": "",
        "port": 8080,
        "user": "",
        "password": ""
    },
    "privacy": {
        # Eliminar parámetros de seguimiento (utm_*, fbclid, etc.) de los enlaces
        "clean_urls": True,
        # Bloquear/avisar de píxeles de rastreo (reservado para vistas HTML)
        "block_trackers": True
    },
    "backup": {
        "auto_backup": False,
        "backup_interval_days": 7,
        "max_backups": 10,
        "last_backup": ""
    },
    "accessibility": {
        "screen_reader_mode": True,
        "enable_sounds": True,
        "speech_announcements": True,
        # Cómo se entregan los mensajes IMPORTANTES (resultados de operaciones, errores,
        # confirmaciones): "voz" = los dice el lector; "mensaje" = se muestran en un cuadro
        # de diálogo (no se pierden aunque la voz esté desactivada); "ambos" = las dos cosas.
        # La lectura de navegación por listas NO se ve afectada por esto.
        "modo_avisos": "voz",
        # Emitir además un toast nativo de Windows (Centro de actividades) en los avisos
        # importantes, útil cuando la app está en segundo plano. Independiente de modo_avisos.
        "toast_windows": False,
        # Interfaz invisible: teclas globales del sistema (Ctrl+Alt+M/N/U/Y) que funcionan
        # aunque la app esté en segundo plano. Desactivada de fábrica.
        "interfaz_invisible": False,
        # Al cambiar de buffer/cuenta/servicio con la interfaz invisible, decir también el
        # elemento enfocado y el total ("Inicio, 3 de 120"), como TWBlue.
        "invisible_anunciar_posicion": True,
        "announcement_delay": 50,  # Milisegundos
        # Retardo antes de emitir nuestros mensajes con interrupción, para que se
        # escuchen DESPUÉS del anuncio automático de foco del lector (corrige que
        # se perdieran mensajes al cambiar el foco). Subir si aún se pierden.
        "speech_delay_ms": 120,
        "verbal_confirmations": True,
        "sound_confirmations": True,
        # Enviar también los anuncios a la pantalla braille si el lector la soporta.
        "braille_announcements": True,
        # Reproducir un sonido al situarte en una publicación con imagen o con
        # audio/vídeo (como indicate_img / indicate_audio de TWBlue).
        "indicate_image": False,
        "indicate_audio": False,
        # Reproducir el sonido identificativo de mensaje directo TAMBIÉN dentro de una CONVERSACIÓN
        # abierta como buffer (hilo "thread:"), donde sonaría en cada mensaje. Por defecto NO, para no
        # molestar. En las listas/buffers normales (Inicio, Directos…) el sonido se controla con su
        # propio sonido en Preferencias > Sonidos ("Mensaje directo recibido").
        "direct_sound_in_dm_buffer": False,
        "auto_read_new": False,
        "keep_focus_on_update": True,
        # Visor de artículos: tema (claro/oscuro/sepia) y tamaño de fuente
        "reading_theme": "claro",
        "viewer_font_size": 14,
        "mastodon_template": "De {author}. {content}. {date}. {visibility}. {indicators} {media}.",
        "rss_template": "{feed}: {title}. {date}. {flags}.",
        # Plantillas adicionales (notificaciones, menciones, perfil de usuario, anuncios)
        "notification_template": "{type}: {author}. {content}. {date}.",
        "mention_template": "{author}. {content}. {date}.",
        "person_template": "{display} (@{acct}). {followers} seguidores, {following} siguiendo, {posts} publicaciones. {bio}",
        "announcement_template": "Anuncio: {content}. {date}.",
        "conversation_template": "Mensaje {num} de {author}: {content}",
        # Opciones de presentación del lector
        "show_screen_names": False,   # Mostrar @usuario junto al nombre
        "hide_emojis": False,         # Quitar emojis del CONTENIDO leído (publicaciones)
        "hide_emojis_names": False,   # Quitar emojis de los NOMBRES de usuario/autor
        # Expandir automáticamente las advertencias de contenido (CW): si está activo, además
        # del aviso se muestra/lee el cuerpo oculto; si no, solo el aviso (como hasta ahora).
        "expand_cw": False,
        # Navegación por columnas (flechas izquierda/derecha y Ctrl+1..9): qué se anuncia de
        # cada celda. Por defecto, todo (fila, nombre de columna y contenido), como hasta ahora.
        "colnav_row_num": True,      # Decir "Fila N"
        "colnav_col_header": True,   # Decir el nombre de la columna (Autor, Contenido...)
        "colnav_cell_value": True,   # Decir el contenido de la celda
        # Lector TTS dedicado: recuerda la última voz y velocidad usadas
        "tts_voice_name": "",
        "tts_speed": 0
    },
    "audio": {
        "sound_pack": "default",
        "volume": 0.8,
        "mute": False,
        # Dispositivo de salida de audio (vacío = predeterminado del sistema)
        "output_device": "",
        # Micrófono para grabar notas de voz (COMPARTIDO por Telegram, Matrix, XMPP...). Antes
        # vivía en la sección "telegram"; se lee con respaldo a ella para no perder el ajuste.
        "mic_device": "",       # vacío = micrófono predeterminado del sistema
        "mic_volume": 1.0,      # 1.0 = 100 %
        "event_sounds": {},  # Asignaciones dinámicas de eventos
        "disabled_events": []
    },
    "media_player": {
        "backend": "basskit",
        "save_progress": True,
        # Reproductor de redes sociales (player1): salto adelante/atrás (segundos),
        # velocidad y volumen recordados entre sesiones.
        "default_step_forward": 10.0,
        "default_step_backward": 10.0,
        "speed": 1.0,
        "volume": 0.8,
        # Segundo reproductor (radio/podcast/YouTube, player2): salto y ajustes propios
        # para no pisar los del reproductor de redes. Los podcasts/vídeos suelen querer
        # saltos un poco más grandes, de ahí el valor por defecto de 15 s.
        "default_step_forward2": 15.0,
        "default_step_backward2": 15.0,
        "speed2": 1.0,
        "volume2": 0.8,
        # Podcasts (player2_podcast.py): segundos que se retrocede al reanudar, margen antes
        # del final para darlo por terminado, siguiente de la cola al terminar, saltar
        # silencios y ecualizador (10 bandas en dB + preamplificador).
        "rewind_on_resume2": 5,
        "finish_margin2": 60,
        "queue_autoplay2": True,
        "skip_silence2": False,
        "eq2": [0.0] * 10,
        "eq2_preamp": 0.0
    },
    "ai": {
        "provider": "gemini",  # gemini, openai, anthropic, local_llama, off
        "model": "gemini-2.5-flash",
        "gemini_api_key": "",
        "gemini_api_keys": [],
        # Claves (lista, una o varias para rotación) por servicio compatible con OpenAI + Anthropic.
        "openai_api_keys": [],
        "deepseek_api_keys": [],
        "xai_api_keys": [],
        "openrouter_api_keys": [],
        "groq_api_keys": [],
        "mistral_api_keys": [],
        "anthropic_api_keys": [],
        "openai_api_key": "",
        "anthropic_api_key": "",
        "local_llama_url": "http://localhost:8080/v1",
        "cache_responses": True,
        "prompt_image_description": "Describe esta imagen detalladamente en español para una persona ciega. Sé descriptivo pero conciso.",
        "prompt_alt_text": "Genera un texto alternativo conciso (máximo 120 caracteres) en español adecuado para el atributo alt de esta imagen en una publicación de Mastodon.",
        "prompt_text_summary": "Resume el siguiente texto en español de manera clara, estructurada y concisa para una rápida lectura.",
        "prompt_hashtags": "Analiza este texto en español y sugiere de 3 a 5 hashtags relevantes separados por espacios."
    },
    "translation": {
        "provider": "google_api_free", # google_api_free, ai, off
        "default_source": "auto",
        "default_target": "es",
        "auto_detect": True,
        "save_history": True,
        "copy_on_translate": True,
        "tts_playback": False
    },
    "mastodon": {
        "update_interval": 60,   # Segundos entre sondeos automáticos (si NO usas tiempo real)
        # Actualización en TIEMPO REAL (streaming) en vez de sondear cada X segundos. Si el
        # streaming no está disponible en la instancia, se cae automáticamente al sondeo por
        # intervalo. False por defecto para no saturar instancias pequeñas sin que el usuario
        # lo decida.
        "realtime": False,
        "limit_per_load": 40,
        "default_visibility": "public",
        "default_sensitive": False,
        "confirm_post": False,
        # Avisar al publicar si hay imágenes sin texto alternativo (accesibilidad)
        "alt_text_reminder": True,
        # Comportamiento al impulsar: "ask" (preguntar), "boost" (directo), "quote" (citar)
        "boost_mode": "ask",
        # Leer del servidor (instancia) la visibilidad por defecto al publicar y si se
        # muestra el contenido sensible (read_preferences_from_instance de TWBlue).
        "read_preferences_from_instance": False,
        # Herramientas de administración de instancia (submenú "Administración" en el menú Mastodon).
        # Solo para cuentas admin/moderadoras; se activa/quita en caliente desde Preferencias.
        "admin_tools": False,
        # Mostrar el panel de detalles (como en Bluesky/RSS): el contenido de la publicación
        # enfocada. Por defecto desactivado (el lector ya lee la lista con la plantilla).
        "show_details_panel": False,
        # Mostrar la botonera de acciones (botones que cambian según el buffer y actúan sobre el
        # elemento enfocado), debajo del panel de detalles. Opcional, por defecto desactivada.
        "show_action_buttons": False,
        # Buffers ocultos: no se muestran en el árbol aunque estén en buffer_order.
        "hidden_buffers": [],
        # Mantenimiento del histórico (Fase 6). 0 = sin límite (se conserva todo).
        # Recortan en segundo plano al arrancar; nunca tocan favoritos/marcadores.
        "max_items_buffer": 0,      # máx. por buffer de alto volumen (Inicio, Local, Federada…)
        "max_notifications": 0,     # máx. de notificaciones guardadas por cuenta
        # No conservar publicaciones entre sesiones: al reiniciar se vacían los buffers
        # volátiles (Inicio, Local, Federada, Directos, Notificaciones, Menciones), conservando
        # favoritos, marcadores y enviados. Para quien no quiere guardar NADA (el 0 de los
        # límites de arriba significa "sin límite", así que no servía para esto).
        "no_conservar_entre_sesiones": False,
        # Clientes (apps) cuyas publicaciones se ocultan de las listas
        "ignored_clients": [],
        # Búsquedas guardadas (hashtags o texto) que aparecen como buffers
        "saved_searches": [],
        # Cronologías de otros usuarios abiertas como buffer [{account_id, acct, display}]
        "user_timelines": [],
        # Conversaciones (hilos) abiertas como buffer [{account_id, status_id, display}]
        "thread_buffers": [],
        # Límite de caracteres por instancia, cacheado por cuenta {"account_id": max} (la
        # instancia puede permitir más de 500; se lee de su configuración y se recuerda).
        "char_limits": {},
        # Caracteres por publicación elegidos en el editor de hilos (0 = usar el máximo de la
        # instancia). Permite trocear un hilo en publicaciones más pequeñas si se quiere.
        "thread_chars_per_post": 0,
        # Caché de listas del servidor para mostrarlas en el árbol [{id,title}]
        "lists_cache": [],
        "show_boosts": True,
        "show_replies": True,
        "buffer_order": ["home", "notifications", "mentions", "bookmarks", "favourites", "sent", "direct", "local", "public", "announcements", "trending", "followers", "following", "blocked", "muted"]
    },
    "bluesky": {
        "pds_url": "https://bsky.social",   # PDS por defecto (configurable por cuenta)
        "update_interval": 60,              # Segundos entre sondeos (si NO usas tiempo real)
        "realtime": False,                  # Tiempo real del Inicio vía firehose (más recursos)
        "no_conservar_entre_sesiones": False,  # Vaciar buffers volátiles al reiniciar
        "limit_per_load": 40,
        "confirm_post": False,
        # Avisar al publicar si hay imágenes sin texto alternativo (accesibilidad)
        "alt_text_reminder": True,
        # Comportamiento al repostear: "ask" (preguntar), "repost" (directo), "quote" (citar)
        "repost_mode": "ask",
        # Lectura de reposts: "original" = De Y. [Reposteado por @X] (el de siempre);
        # "repostea" = De X. Reposteó el mensaje de Y.
        "formato_repost": "original",
        "prefijo_respuesta": False,         # anteponer "En respuesta a @x" al texto
        # Al responder, empezar con "@autor @mencionados " (estilo Mastodon). La app oficial no lo
        # hace: el servidor avisa a los autores de encima en el hilo sin menciones.
        "mencionar_al_responder": False,
        # Mantenimiento del histórico (Fase 6). 0 = sin límite (se conserva todo).
        # Recortan en segundo plano al arrancar; nunca tocan marcadores ni los buffers curados.
        "max_items_buffer": 0,      # máx. por buffer de alto volumen (Inicio, Menciones…)
        "max_notifications": 0,     # máx. de notificaciones guardadas por cuenta
        # Búsquedas guardadas (texto) que aparecen como buffers
        "saved_searches": [],
        # Borradores locales de posts
        "drafts": [],
        # Caché de feeds personalizados guardados/fijados [{uri,name}]
        "saved_feeds": [],
        # Caché de listas para mostrarlas en el árbol [{uri,name}]
        "lists_cache": [],
        # Quién puede responder por defecto: "everybody", "nobody", "following", "mentioned"
        "default_reply_control": "everybody",
        # Panel de detalles del post enfocado (por defecto visible, como hasta ahora). Si lo
        # desactivas, al navegar no se construye ni se vuelca su texto (más fluido).
        "show_details_panel": True,
        # Botonera de acciones bajo la lista (botones según el buffer, sobre el elemento enfocado).
        "show_action_buttons": False,
        # Buffers ocultos: no se muestran en el árbol aunque estén en buffer_order.
        "hidden_buffers": [],
        "buffer_order": ["home", "notifications", "mentions", "sent", "likes", "bookmarks", "feeds", "lists", "starterpacks", "trending", "followers", "follows", "blocks", "mutes", "convos"]
    },
    "wsocial": {
        # W Social (AT Protocol). NO se fija servidor: se descubre resolviendo la identidad del
        # usuario (handle -> DID -> PDS). Esto es solo el que se OFRECE al añadir una cuenta.
        "pds_url": "https://wsocial.eu",
        "formato_repost": "original",       # igual que en Bluesky
        "prefijo_respuesta": False,
        # Al responder, empezar con "@autor @mencionados ". Activado por defecto: W Social siempre
        # había puesto la mención del autor (en Bluesky viene desactivado, como la app oficial).
        "mencionar_al_responder": True,
        "update_interval": 60,              # segundos entre sondeos del buffer activo
        "tiempo_real": True,                # avisos de notificaciones y mensajes en segundo plano
        "intervalo_tiempo_real": 60,        # cada cuánto pregunta el vigilante (mínimo 20 s)
        "avisar_mensajes": True,            # avisar de los mensajes directos nuevos
        "no_conservar_entre_sesiones": False,
        "limit_per_load": 40,
        "confirm_post": False,
        "alt_text_reminder": True,          # avisar si publicas imágenes sin descripción
        "max_items_buffer": 0,              # 0 = sin límite (mantenimiento opcional)
        "max_notifications": 0,
        "saved_searches": [],
        "saved_feeds": [],                  # feeds guardados [{uri, name}]
        "lists_cache": [],                  # listas guardadas [{uri, name}]
        "user_timelines": [],               # cronologías de cuentas [{did, handle, account_id}]
        "thread_buffers": [],               # conversaciones abiertas como buffer
        "labelers_suscritos": {},           # account_id -> [dids de servicios de etiquetado]
        "prefs_etiquetas": {},              # etiqueta -> ignore | warn | hide (caché local)
        "palabras_silenciadas": {},         # account_id -> [mutedWord] (caché local para pintar)
        "default_reply_control": "everybody",
        "show_details_panel": True,
        "show_action_buttons": False,
        "hidden_buffers": [],
        "buffer_order": ["home", "notifications", "mentions", "sent", "likes", "bookmarks",
                         "drafts", "feeds", "lists", "starterpacks", "trending", "followers",
                         "follows", "blocks", "mutes", "convos", "requests"]
    },
    "reddit": {
        # Segundos entre sondeos automáticos (Reddit limita a 100 peticiones/min POR client_id,
        # promediado en 10 min; conviene un intervalo holgado).
        "update_interval": 120,
        "limit_per_load": 25,           # ítems por carga (página estándar de Reddit)
        # Identificador de aplicación (client id) de una "installed app" registrada por el usuario
        # en reddit.com/prefs/apps. OBLIGATORIO (uno por usuario, como el api_id/api_hash de
        # Telegram): sin él no se pueden añadir cuentas. Redirect uri de la app: http://localhost:8080.
        "client_id": "",
        # Orden de comentarios: confidence, top, new, controversial, old, qa
        "comment_sort": "confidence",
        "comment_limit": 200,           # comentarios de primer nivel a cargar
        "replace_more_limit": 32,       # cuántos "cargar más comentarios" resolver (cada uno = 1 petición)
        # time_filter para top/controversial: all, day, hour, month, week, year
        "time_filter": "day",
        "show_nsfw": False,             # mostrar contenido marcado como +18
        "confirm_post": False,
        "no_conservar_entre_sesiones": False,  # vaciar buffers volátiles al reiniciar
        "max_items_buffer": 0,          # máx. por buffer de alto volumen (0 = sin límite)
        "max_notifications": 0,         # máx. de mensajes de bandeja guardados por cuenta
        # Panel de detalles del elemento enfocado y botonera de acciones bajo la lista.
        "show_details_panel": True,
        "show_action_buttons": False,
        # Subreddits fijados como buffer en el árbol [{name}]
        "saved_subreddits": [],
        # Búsquedas guardadas (texto) que aparecen como buffers
        "saved_searches": [],
        # Multireddits cacheados para el árbol [{name, path}]
        "multireddits_cache": [],
        "hidden_buffers": [],
        "buffer_order": ["home", "popular", "all", "inbox", "unread", "mentions", "sent",
                         "submitted", "comments", "saved", "upvoted", "downvoted", "hidden",
                         "subreddits", "multireddits", "searches"]
    },
    "peertube": {
        # PeerTube es una red de VÍDEO federada y MULTI-INSTANCIA: cada cuenta vive en su propia
        # instancia (URL), con su versión y capacidades. No hay push: se sondea por intervalo.
        "update_interval": 180,             # segundos entre sondeos automáticos (notificaciones)
        "limit_per_load": 25,               # vídeos por página (paginación start/count)
        "max_items_buffer": 0,              # máx. por buffer de alto volumen (0 = sin límite)
        "no_conservar_entre_sesiones": False,
        # Reproducción
        "preferred_resolution": "auto",     # "auto" o una altura: "1080", "720", "480", "360"...
        "preferred_subtitle_lang": "es",    # idioma preferido de subtítulos (código ISO)
        "autoplay_next": False,             # reproducir el siguiente al terminar
        "save_position": True,              # guardar y restaurar la posición de reproducción
        # Contenido sensible: do_not_list | display | blur | hide (política de PeerTube)
        "nsfw_policy": "do_not_list",
        # Subida por defecto (privacy PeerTube: 1=público, 2=no listado, 3=privado, 4=interno)
        "default_privacy": 1,
        "download_dir": "",                 # carpeta de descargas (vacío = preguntar)
        # Panel de detalles del vídeo enfocado y botonera de acciones bajo la lista
        "show_details_panel": True,
        "show_action_buttons": False,
        # Instancias sugeridas al añadir cuenta (URLs); se completa desde el índice de instancias
        "saved_instances": [],
        # Secciones ocultas y orden de las secciones del árbol de navegación
        "hidden_sections": [],
        "section_order": ["recientes", "populares", "tendencias", "locales", "federados",
                          "suscripciones", "biblioteca", "historial", "ver_mas_tarde", "me_gusta",
                          "listas", "mis_videos", "mis_canales", "directos", "notificaciones",
                          "busquedas"]
    },
    "rss": {
        "update_interval": 30, # Minutos
        "download_fulltext": False,
        # Lector limpio: al abrir un artículo cuyo feed solo trae un resumen, descargar de la web
        # el artículo completo (una vez por artículo; queda guardado).
        "fulltext_on_open": True,
        # Artículo completo "por sitio": hilos de foros (Lemmy, Discourse, Reddit con la cuenta)
        # con sus comentarios y vídeos de YouTube con descripción, capítulos y transcripción.
        "fulltext_sitios": True,
        # Hilos de Reddit: leerlos con la sesión de Reddit del navegador (como BlindRSS), porque
        # Reddit ya no deja leerlos sin sesión. Solo cookies de reddit.com, en memoria.
        "reddit_browser_session": True,
        # Al terminar de escuchar un episodio (reproductor de podcasts), marcarlo como leído.
        "mark_read_on_finish": True,
        # Fase 4 (organización y avisos): búsquedas guardadas como buffers del árbol, tope de
        # avisos de alertas por actualización, feeds sin aviso y qué hace Suprimir por feed.
        "saved_searches_in_tree": True,
        "max_alert_notifications": 3,
        "mute_feeds": [],
        "delete_mode": "papelera",
        "delete_mode_feeds": {},
        # Vista previa del panel (Tab): si el feed solo trae un resumen, descargar también el
        # artículo completo al quedarse en él (una vez por artículo; queda guardado).
        "fulltext_en_vista_previa": True,
        # Intro sobre un artículo (buffer RSS y bandeja): "navegador" (abrir el enlace, lo de
        # siempre), "lector" (lector limpio) o "preguntar".
        "enter_action": "navegador",
        # Extractor del artículo completo: "auto" (compara y elige el mejor), "trafilatura",
        # "readability" o "jsonld" (datos estructurados). Ver rss_fulltext.MOTORES_EXTRACCION.
        "fulltext_extractor": "auto",
        # Medios de pago en los que el usuario está suscrito (dominios): sus artículos se
        # descargan con la sesión que tiene iniciada en su navegador (rss_suscripciones.py).
        "subscription_domains": [],
        "subscription_browser": "firefox",
        "download_images": False,
        "cleanup_days": 30,
        "notifications_enabled": True,
        "mark_read_on_open": True,
        # Espacio máximo de la carpeta de podcasts descargados, en MiB (0 = sin límite).
        "podcast_max_mib": 4096,
        "max_articles_list": 500,
        # Nº de feeds que se descargan a la vez al "Actualizar todos" (1-50)
        "max_parallel_updates": 5,
        # Conservar artículos X días (0 = para siempre); no borra favoritos ni leer-más-tarde
        "keep_articles_days": 0,
        # Máximo de artículos por feed (0 = sin límite)
        "max_articles_per_feed": 0,
        # No conservar artículos entre sesiones: al reiniciar, vaciar todo excepto destacados,
        # leer-más-tarde y episodios descargados. Evita que la base de datos crezca sin fin.
        "no_conservar_entre_sesiones": False,
        # Ocultar artículos anclados con fecha futura (los que algunos feeds fijan arriba).
        "hide_future_articles": False,
        # Mostrar solo no leídos en los buffers y agrupar por fecha
        "show_only_unread": False,
        "group_by_date": False,
        # Panel de detalles (vista previa del artículo) y botonera de acciones bajo la lista.
        "show_details_panel": True,
        "show_action_buttons": False,
        "hidden_buffers": [],
        "buffer_order": ["all", "unread", "favorites", "read_later", "today"]
    },
    "telegram": {
        "update_interval": 60,   # Segundos entre sondeos automáticos de Telegram
        "download_dir": "",
        "download_subdirs": True,
        "announce_new": True,
        # Reproducir solo las notas de voz / audios que lleguen al chat abierto.
        "autoplay_voice": False,
        # Marcar mensajes como leídos al pasar el foco por ellos (y persistir al salir).
        "mark_read_on_focus": True,
        # Señalar con "(nuevo)" los mensajes sin leer en la lista de la conversación.
        "show_unread_marker": True,
        # Fila separadora "Mensajes no leídos" antes del primero sin leer (estilo Unigram).
        "show_unread_divider": True,
        # (mic_device / mic_volume se movieron a la sección "audio": son compartidos por varias
        #  redes y se configuran en Preferencias → Sonidos → Micrófono.)
        # Máx. de mensajes en caché por chat (Fase 6). 0 = sin límite (conservar todos).
        "max_messages_per_chat": 0,
        # Botonera de acciones bajo la lista de chats (Telegram no tiene panel de detalles: los
        # chats se abren en su ventana de conversación).
        "show_action_buttons": False,
        "hidden_buffers": [],
        "buffer_order": ["all", "unread", "private", "bot", "group", "supergroup", "channel", "contacts"]
    },
    "telegram_drive": {
        # Nube personal sobre Telegram. La configuración del canal por cuenta se guarda aparte,
        # con el id de cuenta como clave (p. ej. "3": {"channel_id": ..., "titulo": ...}); esas
        # claves conviven con estas opciones globales y se conservan al migrar.
        # Carpeta local donde se guardan las descargas ("" = carpeta por defecto de datos).
        "carpeta_descargas": "",
        # Cuántas transferencias se procesan a la vez (1 = secuencial, lo más suave para la
        # conexión de Telegram; subir no satura ni bloquea la interfaz).
        "max_simultaneas": 1,
        # Tamaño de bloque en bytes para leer al calcular el hash y trocear (1 MiB).
        "tam_bloque": 1048576,
        # Qué hacer por defecto ante un nombre repetido: "preguntar", "reemplazar", "ambos",
        # "omitir". "preguntar" muestra el diálogo de conflicto (nunca sobrescribe en silencio).
        "politica_conflicto": "preguntar",
        # Días que se conservan los elementos en la papelera (0 = para siempre, borrado manual).
        "retencion_papelera_dias": 30,
        # Pedir confirmación antes de enviar a la papelera.
        "confirmar_borrado": True,
        # Limpiar los archivos temporales de troceo/descarga al terminar (siempre recomendado).
        "limpiar_temporales": True,
        # Cifrado del contenido antes de subirlo. Arquitectura preparada pero DESACTIVADA: no se
        # incluye una implementación insegura. Se activará cuando el cifrado esté completo.
        "cifrado": False,
        # Cada cuántos segundos se anuncia el progreso de una transferencia con el lector (los
        # hitos —inicio, pausa, cancelación, error, fin y conflictos— se anuncian siempre).
        "frecuencia_progreso_seg": 5
    },
    "x": {
        # Cliente de X (Twitter), NO oficial. Esta sección DEBE existir en DEFAULTS: sin
        # ella, load() descartaba toda la config de X al arrancar (entre otras,
        # hidden_buffers y buffer_order volvían a su estado inicial cada vez).
        "enabled": True,
        # Motor de LECTURA de X: combina twikit (lee/escribe) y twscrape (solo lectura). "auto"
        # usa la mejor para cada cosa con respaldo cruzado; la ESCRITURA siempre va por twikit.
        "read_engine": "auto",
        "limit_per_load": 40,
        # Anteponer "En respuesta a @x" al texto de las respuestas (como en Bluesky/W Social).
        "prefijo_respuesta": False,
        # Conversaciones abiertas como buffer: [{"id": tweet_id, "display": texto}].
        "thread_buffers": [],
        # Segundos entre sondeos automáticos de X. X limita con dureza, así que el mínimo real
        # es min_update_seconds (no se sondea más a menudo que eso aunque pongas menos).
        "update_interval": 120,
        # El sondeo automático de X no se hace más a menudo que esto (X limita con dureza).
        "min_update_seconds": 120,
        # Actualización AUTOMÁTICA de X. Por defecto DESACTIVADA (manual): X solo se refresca
        # con F5/Ctrl+Shift+R. Si se activa, X se pone al día solo (al entrar en un buffer, con
        # su intervalo mientras lo miras y en segundo plano estando en otras redes), PERO al ser
        # un cliente NO oficial con límites muy estrictos, eso aumenta el riesgo de bloqueo de la
        # cuenta. Ver la advertencia en Preferencias -> X.
        "auto_update": False,
        # Avisar al publicar si hay imágenes sin texto alternativo (accesibilidad).
        "alt_text_reminder": True,
        # No conservar publicaciones entre sesiones: vaciar la caché volátil al reiniciar.
        "no_conservar_entre_sesiones": False,
        # Panel de detalles del tweet enfocado y botonera de acciones bajo la lista.
        "show_details_panel": True,
        "show_action_buttons": False,
        "hidden_buffers": [],
        "buffer_order": ["home", "following", "mentions", "notifications", "bookmarks",
                         "followers", "friends", "lists", "communities", "scheduled"]
    }
}

# Claves OBSOLETAS: opciones que existieron en versiones anteriores y que ya no usa esta versión.
# Al cargar, se eliminan del archivo del usuario para que su JSON quede al día (sin perder el resto
# de su configuración). PROTECCIÓN A FUTURO: cuando retires una opción, añádela aquí como
# ("seccion", "clave").
CLAVES_OBSOLETAS = [
    ("general", "auto_update_seconds"),  # el intervalo ya no es general; cada servicio tiene el suyo
    ("general", "show_action_buttons"),  # la botonera dejó de ser global: ahora se configura por red
    ("telegram", "mic_device"),  # el micrófono pasó a la sección "audio" (compartido por varias redes)
    ("telegram", "mic_volume"),
]

class ConfigManager:
    """Administrador de configuración que lee y escribe de un archivo JSON."""
    
    def __init__(self, filename: str = "config/settings.json"):
        self.file_path = get_data_path(filename)
        self.settings: Dict[str, Any] = {}
        # Bloqueo reentrante: todas las lecturas/escrituras que toquen self.settings o
        # el archivo se serializan, para que un hilo de fondo (RSS, Telegram, Bluesky…)
        # no mute el diccionario mientras otro lo está volcando a disco. Sin esto,
        # json.dump podía fallar a mitad ("dictionary changed size during iteration"),
        # dejar el archivo a medias y forzar una restauración de valores por defecto
        # al reiniciar (con la consiguiente pérdida del orden de servicios/buffers).
        self._lock = threading.RLock()
        # Lock de E/S a disco (separado del de datos): la escritura+fsync de save() se hace bajo
        # ESTE lock, no bajo self._lock, para que get()/set() no esperen al fsync (que es lento).
        self._io_lock = threading.Lock()
        # Soporte de guardado por LOTES: dentro de un with config.batch() (o set_many), los set()
        # acumulan cambios en memoria y se guarda UNA sola vez al cerrar el lote. Así Preferencias
        # pasa de ~117 escrituras de settings.json a 1 (cada una hacía fsync en el hilo de la UI).
        self._batch_depth = 0
        self._batch_dirty = False
        # Temporizador del guardado DIFERIDO (set_diferido): agrupa en un solo volcado
        # los cambios rápidos de controles como volumen/velocidad del reproductor.
        self._debounce_timer = None
        # True si al cargar NO existía el archivo de configuración (instalación NUEVA / primer
        # arranque). Lo usa la i18n para autodetectar el idioma del sistema solo la primera vez,
        # sin cambiárselo nunca a quien ya tenía la app configurada.
        self.instalacion_nueva = False
        self.load()

    def load(self):
        """Carga la configuración desde el disco o inicializa con los defaults."""
        with self._lock:
            if not self.file_path.exists():
                log.info("No se encontró archivo de configuración. Inicializando con valores por defecto.")
                self.instalacion_nueva = True  # primer arranque: la i18n autodetecta el idioma
                self.settings = json.loads(json.dumps(DEFAULTS))  # Copia profunda
                self.save()
                return

            loaded = None
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except Exception as e:
                # Si el archivo principal está corrupto, intentar la copia de respaldo
                # del último guardado correcto antes de rendirse a los valores por defecto.
                log.error(f"Error al leer la configuración: {e}. Intentando copia de respaldo.")
                bak = self.file_path.with_suffix(self.file_path.suffix + ".bak")
                if bak.exists():
                    try:
                        with open(bak, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                        log.info("Configuración restaurada desde la copia de respaldo.")
                    except Exception as e2:
                        log.error(f"La copia de respaldo también falló: {e2}.")

            if loaded is None:
                log.error("No se pudo cargar ninguna configuración válida. Usando valores por defecto.")
                self.settings = json.loads(json.dumps(DEFAULTS))
                self.save()
                return

            # Combinar con defaults para asegurar que no falten claves nuevas. IMPORTANTE:
            # las secciones guardadas que NO estén en DEFAULTS se conservan tal cual (antes
            # se descartaban, y eso borraba en cada arranque la config de secciones no
            # declaradas en DEFAULTS —como pasó con "x"—, perdiendo p. ej. los buffers
            # ocultos y su orden).
            self.settings = json.loads(json.dumps(DEFAULTS))
            for seccion, claves in loaded.items():
                if seccion in self.settings and isinstance(self.settings[seccion], dict) and isinstance(claves, dict):
                    self.settings[seccion].update(claves)
                else:
                    self.settings[seccion] = claves

            # MIGRACIÓN del JSON del usuario a esta versión, SIN perder su configuración:
            #  - El merge de arriba ya AÑADE las claves nuevas (con su valor por defecto).
            #  - Aquí ELIMINAMOS las claves OBSOLETAS que arrastre de versiones viejas.
            # MIGRACIÓN puntual: la botonera de acciones pasó de ser GLOBAL ("general") a
            # configurarse POR RED. Si el usuario la tenía activada globalmente, la activamos en
            # todas las redes para no perder su preferencia (luego la clave global se elimina como
            # obsoleta, justo debajo).
            old_general = loaded.get("general") if isinstance(loaded.get("general"), dict) else {}
            if old_general.get("show_action_buttons"):
                for _red in ("mastodon", "bluesky", "x", "rss", "telegram"):
                    _sec = self.settings.setdefault(_red, {})
                    if isinstance(_sec, dict):
                        _sec["show_action_buttons"] = True
            # MIGRACIÓN: el micrófono (mic_device/mic_volume) pasó de la sección "telegram" a
            # "audio" (es COMPARTIDO por Telegram/Matrix/XMPP...: Preferencias → Sonidos →
            # Micrófono). Copiar el ajuste del usuario a "audio" si no lo tenía ya allí, para no
            # perderlo; las claves viejas de "telegram" se eliminan justo debajo (son obsoletas).
            old_tg = loaded.get("telegram") if isinstance(loaded.get("telegram"), dict) else {}
            old_audio = loaded.get("audio") if isinstance(loaded.get("audio"), dict) else {}
            _audio = self.settings.setdefault("audio", {})
            if isinstance(_audio, dict):
                if "mic_device" not in old_audio and old_tg.get("mic_device"):
                    _audio["mic_device"] = old_tg["mic_device"]
                if "mic_volume" not in old_audio and "mic_volume" in old_tg:
                    _audio["mic_volume"] = old_tg["mic_volume"]
            for seccion, clave in CLAVES_OBSOLETAS:
                sec = self.settings.get(seccion)
                if isinstance(sec, dict) and clave in sec:
                    del sec[clave]
            # Si el archivo en disco no coincide con la config ya actualizada (faltaban claves
            # nuevas o sobraban obsoletas), lo reescribimos para dejarlo en el formato de esta
            # versión. Se autocorrige: la próxima vez ya coincide y no se vuelve a guardar.
            if loaded != self.settings:
                try:
                    self.save()
                    log.info("Configuración migrada al formato de esta versión (conservando tus ajustes).")
                except Exception as e:
                    log.debug(f"No se pudo reescribir la configuración migrada: {e}")
            log.debug("Configuración cargada con éxito.")

    def save(self):
        """Guarda la configuración en disco de forma atómica (archivo temporal + reemplazo).

        La SERIALIZACIÓN a texto se hace bajo self._lock (para que ningún hilo mute el diccionario
        a medio volcado), pero la ESCRITURA+fsync —que es lo lento— se hace bajo self._io_lock,
        FUERA del lock de datos: así las lecturas (get) y escrituras (set) de la UI no esperan al
        fsync. _io_lock serializa los volcados concurrentes para que no se pisen el .tmp."""
        with self._lock:
            # Un guardado completo deja obsoleto cualquier volcado diferido pendiente.
            if self._debounce_timer is not None:
                try:
                    self._debounce_timer.cancel()
                except Exception:
                    pass
                self._debounce_timer = None
            try:
                contenido = json.dumps(self.settings, indent=4, ensure_ascii=False)
            except Exception as e:
                log.error(f"Error al serializar la configuración: {e}")
                return
        with self._io_lock:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(contenido)
                    f.flush()
                    os.fsync(f.fileno())
                # Conservar una copia del último archivo válido antes de reemplazarlo.
                if self.file_path.exists():
                    try:
                        bak = self.file_path.with_suffix(self.file_path.suffix + ".bak")
                        os.replace(self.file_path, bak)
                    except Exception:
                        pass
                os.replace(tmp, self.file_path)  # reemplazo atómico
                log.debug("Configuración guardada correctamente.")
            except Exception as e:
                log.error(f"Error al guardar la configuración: {e}")

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """Obtiene un valor de la configuración."""
        with self._lock:
            try:
                return self.settings[section][key]
            except KeyError:
                # Si no existe en la configuración actual, tomar el valor de fábrica y dejarlo en
                # memoria para futuras lecturas. NO se guarda a disco aquí: hacerlo dentro de get()
                # escribía settings.json bajo el lock en plena lectura y podía dar micro-tirones en
                # la interfaz (get() lo llaman tanto la UI como los hilos de fondo). El default se
                # persiste solo cuando algo cambie de verdad (set()/guardado de preferencias); leerlo
                # de nuevo en la próxima sesión devuelve el mismo valor de fábrica.
                try:
                    val = DEFAULTS[section][key]
                    if section not in self.settings:
                        self.settings[section] = {}
                    self.settings[section][key] = val
                    return val
                except KeyError:
                    return default

    def set(self, section: str, key: str, value: Any, autosave: bool = True):
        """Establece un valor de configuración.

        Con autosave=True (por defecto) guarda a disco al instante. Con autosave=False NO guarda
        (el llamante guardará luego con save()). Dentro de un with config.batch() el guardado se
        difiere SIEMPRE hasta cerrar el lote, aunque autosave sea True."""
        with self._lock:
            if section not in self.settings:
                self.settings[section] = {}
            self.settings[section][key] = value
            if self._batch_depth > 0:
                self._batch_dirty = True
                return
        if autosave:
            self.save()

    def set_many(self, cambios):
        """Aplica varios cambios y guarda UNA sola vez.

        'cambios' puede ser un dict {(seccion, clave): valor} o un iterable de (seccion, clave, valor).
        Reduce a una única escritura de settings.json (un solo fsync) en lugar de una por clave."""
        items = cambios.items() if isinstance(cambios, dict) else None
        with self._lock:
            if items is not None:
                for (section, key), value in items:
                    self.settings.setdefault(section, {})[key] = value
            else:
                for section, key, value in cambios:
                    self.settings.setdefault(section, {})[key] = value
            if self._batch_depth > 0:
                self._batch_dirty = True
                return
        self.save()

    def begin_batch(self):
        """Inicia un lote de guardado diferido (ver batch()). Reentrante. Empareja con end_batch()."""
        with self._lock:
            self._batch_depth += 1

    def end_batch(self):
        """Cierra un lote abierto con begin_batch(); al cerrar el más externo, guarda una vez si
        hubo cambios."""
        guardar = False
        with self._lock:
            self._batch_depth = max(0, self._batch_depth - 1)
            if self._batch_depth == 0:
                guardar = self._batch_dirty
                self._batch_dirty = False
        if guardar:
            self.save()

    def set_diferido(self, section: str, key: str, value: Any, retraso: float = 0.6):
        """Como set(), pero el guardado a disco se hace con DEBOUNCE: el valor queda en
        memoria al instante y el volcado (serializar + fsync) se agrupa pasados
        `retraso` segundos desde el ÚLTIMO cambio. Para controles que cambian muchas
        veces por segundo (volumen, velocidad del reproductor): antes, cada tick de
        rueda o de flecha era un settings.json completo con fsync en el hilo de la
        interfaz. Al cerrar la app, config.save()/flush() persiste lo pendiente."""
        import threading as _th
        with self._lock:
            if section not in self.settings:
                self.settings[section] = {}
            self.settings[section][key] = value
            if self._batch_depth > 0:
                self._batch_dirty = True
                return
            if self._debounce_timer is not None:
                try:
                    self._debounce_timer.cancel()
                except Exception:
                    pass
            t = _th.Timer(max(0.1, retraso), self._guardar_diferido)
            t.daemon = True
            self._debounce_timer = t
            t.start()

    def _guardar_diferido(self):
        with self._lock:
            self._debounce_timer = None
        self.save()

    def flush(self):
        """Persiste YA cualquier guardado diferido pendiente (se llama al cerrar la app
        o al confirmar un diálogo). Inofensivo si no hay nada pendiente."""
        pendiente = False
        with self._lock:
            if self._debounce_timer is not None:
                try:
                    self._debounce_timer.cancel()
                except Exception:
                    pass
                self._debounce_timer = None
                pendiente = True
        if pendiente:
            self.save()

    @contextmanager
    def batch(self):
        """Contexto que agrupa todos los set() en un único guardado al cerrar.

        Uso:
            with config.batch():
                config.set("a", "b", 1)
                config.set("c", "d", 2)
            # aquí se guarda una sola vez (si hubo cambios).
        Es reentrante: lotes anidados solo guardan al cerrar el más externo."""
        self.begin_batch()
        try:
            yield self
        finally:
            self.end_batch()

    def reset_defaults(self):
        """Restaura la configuración a los valores de fábrica."""
        with self._lock:
            self.settings = json.loads(json.dumps(DEFAULTS))
            self.save()
            log.info("Configuración reseteada a valores por defecto.")
        
# Instancia única del gestor de configuración para toda la app
config = ConfigManager()


def limite_render(seccion: str, clave: str = "max_statuses_list") -> int:
    """Valor para una cláusula SQL LIMIT al PINTAR un buffer: devuelve el límite que el usuario
    haya puesto (si es > 0) o -1 (que en SQLite significa SIN LÍMITE) cuando está en 0 o no se
    configuró. Así NINGÚN buffer recorta los elementos que tiene cargados (regla pedida por el
    usuario: sin límite en ningún buffer de ninguna red). El control real del tamaño es la poda
    opcional de mantenimiento, que el usuario activa poniendo un máximo en Preferencias."""
    try:
        n = int(config.get(seccion, clave, 0))
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else -1
