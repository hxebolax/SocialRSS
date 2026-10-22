# -*- coding: utf-8 -*-
"""
Plantillas de LECTURA del lector de pantalla: definición central de cada plantilla
(campos disponibles, valor por defecto y datos de ejemplo) y utilidades para darles
formato. Cada plantilla pertenece a UNA red social (mastodon, bluesky, telegram, rss, x),
de modo que cada red tiene su PROPIO espacio configurable e independiente. Lo usa el editor
accesible de plantillas (Herramientas) y las pantallas que construyen la lectura de cada
elemento.
"""

import re
import builtins


def _t(s):
    return getattr(builtins, "_", lambda x: x)(s)


# Redes soportadas (orden y nombre mostrado en el editor de plantillas).
REDES = [
    ("mastodon", lambda: _t("Mastodon")),
    ("bluesky", lambda: _t("Bluesky")),
    ("telegram", lambda: _t("Telegram")),
    ("rss", lambda: _t("RSS")),
    ("x", lambda: _t("X (Twitter)")),
    ("irc", lambda: _t("IRC")),
    ("matrix", lambda: _t("Matrix")),
    ("xmpp", lambda: _t("XMPP")),
    ("reddit", lambda: _t("Reddit")),
    ("peertube", lambda: _t("PeerTube")),
    ("wsocial", lambda: _t("W Social")),
    ("inbox", lambda: _t("Bandeja de entrada")),
    ("logs", lambda: _t("Registros (Logs)")),
]


def nombre_red(red: str) -> str:
    for clave, nombre in REDES:
        if clave == red:
            return nombre()
    return red


def pulir_lectura(texto: str) -> str:
    """Limpia y 'pule' una cadena de lectura para el lector de pantalla:
    - Colapsa espacios repetidos.
    - GARANTIZA un espacio tras los signos '?', '!', '¿' y '¡' cuando va pegado el siguiente
      carácter (p. ej. un separador como ';'). Sin esto, algunos sintetizadores se 'comen' la
      interrogación o la exclamación al ir pegada a otro signo.
    """
    if not texto:
        return ""
    t = str(texto)
    t = re.sub(r'([?!])(?=\S)', r'\1 ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    # Si un campo del principio venía VACÍO (p. ej. {unread} cuando no hay no leídos, puesto al
    # inicio de la plantilla), quedaría un separador suelto delante (". ", "- ", etc.): se quita para
    # que la lectura empiece limpia.
    t = re.sub(r'^[\s,.;:·—\-–]+', '', t).strip()
    return t


# Encabezado (nombre de COLUMNA) de cada campo. La plantilla define qué columnas tiene el buffer
# y en qué orden: una columna por cada {campo} que aparezca, en orden de aparición.
ENCABEZADOS = {
    "author": lambda: _t("Autor"),
    "username": lambda: _t("Usuario"),
    "display": lambda: _t("Nombre"),
    "acct": lambda: _t("Usuario"),
    "content": lambda: _t("Contenido"),
    "date": lambda: _t("Fecha"),
    "visibility": lambda: _t("Privacidad"),
    "indicators": lambda: _t("Indicadores"),
    "media": lambda: _t("Adjuntos"),
    "application": lambda: _t("Cliente"),
    "cw": lambda: _t("Advertencia de contenido"),
    "type": lambda: _t("Tipo"),
    "followers": lambda: _t("Seguidores"),
    "following": lambda: _t("Siguiendo"),
    "posts": lambda: _t("Publicaciones"),
    "bio": lambda: _t("Biografía"),
    "num": lambda: _t("Nº"),
    "feed": lambda: _t("Feed"),
    "title": lambda: _t("Título"),
    "flags": lambda: _t("Estado"),
    "counts": lambda: _t("Contadores"),
    "language": lambda: _t("Idioma"),
    "chat": lambda: _t("Chat"),
    "unread": lambda: _t("No leídos"),
    "nombre": lambda: _t("Nombre"),
    "rol": lambda: _t("Rol"),
    "presencia": lambda: _t("Estado"),
    "titulo": lambda: _t("Título de admin"),
    "respuesta": lambda: _t("Respuesta"),
    "reenviado": lambda: _t("Reenviado"),
    "vistas": lambda: _t("Vistas"),
    "id": lambda: _t("Identificador"),
    "origen": lambda: _t("Origen"),
    "hora": lambda: _t("Hora"),
    "nivel": lambda: _t("Nivel"),
    "modulo": lambda: _t("Módulo"),
    "mensaje": lambda: _t("Mensaje"),
    # Telegram Drive (nube personal).
    "marcado": lambda: _t("Marcado"),
    "tipo": lambda: _t("Tipo"),
    "tam": lambda: _t("Tamaño"),
    "modificado": lambda: _t("Modificado"),
    "descripcion": lambda: _t("Descripción"),
    "estado": lambda: _t("Estado"),
    # Bluesky y W Social: a quién responde la publicación.
    "reply_to": lambda: _t("En respuesta a"),
}


def encabezado_campo(campo: str) -> str:
    f = ENCABEZADOS.get(campo)
    return f() if f else campo


def columnas_de_plantilla(plantilla_str: str):
    """Devuelve las COLUMNAS que define una plantilla: lista ordenada y sin repetir de
    (campo, encabezado), una por cada {campo} en su orden de aparición."""
    cols, vistos = [], set()
    for campo in re.findall(r"{(\w+)}", plantilla_str or ""):
        if campo not in vistos:
            vistos.add(campo)
            cols.append((campo, encabezado_campo(campo)))
    return cols


# Campos y presets reutilizables para no duplicar entre redes con estructura similar.
_CAMPOS_POST = [
    ("author", lambda: _t("nombre del autor")),
    ("username", lambda: _t("usuario (@acct)")),
    ("content", lambda: _t("texto de la publicación")),
    ("date", lambda: _t("fecha relativa")),
    ("indicators", lambda: _t("indicadores: me gusta, compartido, guardado, respuesta, cita...")),
    ("media", lambda: _t("adjuntos")),
    ("cw", lambda: _t("advertencia de contenido")),
    ("counts", lambda: _t("contadores de interacción")),
    ("language", lambda: _t("idioma (código, p. ej. es)")),
]
_PRESETS_POST = [
    (lambda: _t("Por defecto (autor, contenido, fecha...)"), "De {author}. {content}. {date}. {indicators} {media}."),
    (lambda: _t("Fecha primero"), "{date}. De {author}. {content}. {indicators} {media}."),
    (lambda: _t("Contenido primero"), "{content}. De {author}. {date}. {indicators} {media}."),
    (lambda: _t("Con usuario (@acct)"), "De {author} ({username}). {content}. {date}. {indicators} {media}."),
    (lambda: _t("Con contadores"), "De {author}. {content}. {date}. {counts}. {indicators} {media}."),
    (lambda: _t("Breve (autor: contenido)"), "{author}: {content}"),
]
# Bluesky, W Social y X añaden a quién responde la publicación (el nombre _AT es histórico: al
# principio solo lo tenían las redes de AT Protocol).
_CAMPOS_POST_AT = _CAMPOS_POST + [
    ("reply_to", lambda: _t("a quién responde (En respuesta a @usuario)")),
]
_PRESETS_POST_AT = _PRESETS_POST + [
    (lambda: _t("Con a quién responde"), "De {author}. {reply_to}. {content}. {date}. {counts}."),
]
_CAMPOS_PERSONA = [
    ("display", lambda: _t("nombre mostrado")),
    ("acct", lambda: _t("usuario (@acct)")),
    ("followers", lambda: _t("número de seguidores")),
    ("following", lambda: _t("número de seguidos")),
    ("posts", lambda: _t("número de publicaciones")),
    ("bio", lambda: _t("biografía")),
]
_PRESETS_PERSONA = [
    (lambda: _t("Por defecto (completo)"), "{display} (@{acct}). {followers} seguidores, {following} siguiendo, {posts} publicaciones. {bio}"),
    (lambda: _t("Breve (nombre y bio)"), "{display} (@{acct}). {bio}"),
    (lambda: _t("Solo cifras"), "{display}: {followers} seguidores, {following} siguiendo, {posts} publicaciones."),
]
_EJ_PERSONA = {"display": "Aurora", "acct": "aurora.bsky.social", "followers": "1200",
               "following": "300", "posts": "5400", "bio": "Programadora y café."}
_CAMPOS_CONV = [
    ("num", lambda: _t("número de mensaje")),
    ("author", lambda: _t("nombre del autor")),
    ("content", lambda: _t("texto del mensaje")),
    ("date", lambda: _t("fecha")),
]
_PRESETS_CONV = [
    (lambda: _t("Por defecto (numerado)"), "Mensaje {num} de {author}: {content}"),
    (lambda: _t("Con fecha"), "{author} ({date}): {content}"),
    (lambda: _t("Breve (autor: contenido)"), "{author}: {content}"),
]
_EJ_CONV = {"num": "2", "author": "Aurora", "content": "¿Lo vemos mañana?", "date": "hace 1 minuto"}
_CAMPOS_NOTIF = [
    ("type", lambda: _t("tipo (Mención, Me gusta...)")),
    ("author", lambda: _t("nombre del autor")),
    ("username", lambda: _t("usuario (@acct)")),
    ("content", lambda: _t("texto, si lo lleva")),
    ("date", lambda: _t("fecha relativa")),
]
_PRESETS_NOTIF = [
    (lambda: _t("Por defecto"), "{type}: {author}. {content}. {date}."),
    (lambda: _t("Breve"), "{type} de {author}: {content}"),
    (lambda: _t("Con usuario"), "{type}: {author} ({username}). {content}. {date}."),
]
_EJ_NOTIF = {"type": "Mención", "author": "Aurora", "username": "@aurora", "content": "¿Te apuntas?", "date": "hace 2 minutos"}


# Registro de plantillas: clave de config (sección 'accessibility') -> definición.
# Cada definición lleva su 'red'. NOTA: las claves de Mastodon y RSS conservan su nombre histórico
# (mastodon_template, notification_template, etc.) para no tocar sus renders; Bluesky/Telegram/X
# tienen claves propias con prefijo de red.
PLANTILLAS = [
    # ---------------- MASTODON ----------------
    {
        "clave": "mastodon_template", "red": "mastodon",
        "nombre": lambda: _t("Publicaciones"),
        "campos": [
            ("author", lambda: _t("nombre del autor")),
            ("username", lambda: _t("usuario (@acct)")),
            ("content", lambda: _t("texto de la publicación")),
            ("date", lambda: _t("fecha relativa")),
            ("visibility", lambda: _t("privacidad")),
            ("indicators", lambda: _t("indicadores: favorito, impulsado, guardado, encuesta, editado, respuesta")),
            ("media", lambda: _t("adjuntos")),
            ("application", lambda: _t("cliente/app desde el que se publicó")),
            ("cw", lambda: _t("advertencia de contenido (CW)")),
            ("counts", lambda: _t("contadores: favoritos, impulsos, respuestas")),
            ("language", lambda: _t("idioma de la publicación (código, p. ej. es)")),
        ],
        "default": "De {author}. {content}. {date}. {visibility}. {indicators} {media}.",
        "ejemplo": {
            "author": "Aurora", "username": "@aurora@mastodon.social",
            "content": "¿Vienes a la quedada?", "date": "hace 5 minutos",
            "visibility": "Público", "indicators": "Favorito Encuesta", "media": "1 archivo adjunto",
            "application": "Web", "cw": "", "counts": "9 favoritos, 5 impulsos, 3 respuestas",
            "language": "es",
        },
        "presets": [
            (lambda: _t("Por defecto (autor, contenido, fecha...)"), "De {author}. {content}. {date}. {visibility}. {indicators} {media}."),
            (lambda: _t("Fecha primero"), "{date}. De {author}. {content}. {visibility}. {indicators} {media}."),
            (lambda: _t("Contenido primero"), "{content}. De {author}. {date}. {visibility}. {indicators} {media}."),
            (lambda: _t("Con usuario (@acct)"), "De {author} ({username}). {content}. {date}. {visibility}. {indicators} {media}."),
            (lambda: _t("Con contadores"), "De {author}. {content}. {date}. {visibility}. {counts}. {indicators} {media}."),
            (lambda: _t("Con cliente"), "{author}. {content}. {date}. {visibility}. {application}. {indicators} {media}."),
            (lambda: _t("Completo (todo)"), "{author} ({username}). {cw}. {content}. {date}. {visibility}. {application}. {indicators} {media}."),
            (lambda: _t("Breve (autor: contenido)"), "{author}: {content}"),
        ],
    },
    {
        "clave": "notification_template", "red": "mastodon",
        "nombre": lambda: _t("Notificaciones"),
        "campos": _CAMPOS_NOTIF, "default": "{type}: {author}. {content}. {date}.",
        "ejemplo": dict(_EJ_NOTIF, username="@aurora@mastodon.social"), "presets": _PRESETS_NOTIF,
    },
    {
        "clave": "mention_template", "red": "mastodon",
        "nombre": lambda: _t("Menciones"),
        "campos": [
            ("author", lambda: _t("nombre del autor")),
            ("username", lambda: _t("usuario (@acct)")),
            ("content", lambda: _t("texto de la mención")),
            ("date", lambda: _t("fecha relativa")),
            ("visibility", lambda: _t("privacidad")),
        ],
        "default": "{author}. {content}. {date}.",
        "ejemplo": {"author": "Aurora", "username": "@aurora@mastodon.social",
                    "content": "¿Te apuntas a la quedada?", "date": "hace 2 minutos", "visibility": "Público"},
        "presets": [
            (lambda: _t("Por defecto (autor, contenido, fecha)"), "{author}. {content}. {date}."),
            (lambda: _t("Fecha primero"), "{date}. {author}. {content}."),
            (lambda: _t("Con usuario (@acct)"), "{author} ({username}). {content}. {date}."),
            (lambda: _t("Breve (autor, contenido)"), "{author}. {content}"),
        ],
    },
    {
        "clave": "person_template", "red": "mastodon",
        "nombre": lambda: _t("Perfiles de cuenta"),
        "campos": _CAMPOS_PERSONA,
        "default": "{display} (@{acct}). {followers} seguidores, {following} siguiendo, {posts} publicaciones. {bio}",
        "ejemplo": dict(_EJ_PERSONA, acct="aurora@mastodon.social"), "presets": _PRESETS_PERSONA,
    },
    {
        "clave": "announcement_template", "red": "mastodon",
        "nombre": lambda: _t("Anuncios del servidor"),
        "campos": [("content", lambda: _t("texto del anuncio")), ("date", lambda: _t("fecha"))],
        "default": "Anuncio: {content}. {date}.",
        "ejemplo": {"content": "Mantenimiento el domingo.", "date": "hace 1 hora"},
        "presets": [(lambda: _t("Por defecto"), "Anuncio: {content}. {date}."), (lambda: _t("Solo contenido"), "{content}")],
    },
    {
        "clave": "conversation_template", "red": "mastodon",
        "nombre": lambda: _t("Conversaciones e hilos"),
        "campos": [("num", lambda: _t("número de mensaje")), ("author", lambda: _t("nombre del autor")),
                   ("content", lambda: _t("texto del mensaje"))],
        "default": "Mensaje {num} de {author}: {content}",
        "ejemplo": {"num": "2", "author": "Aurora", "content": "¿Lo vemos mañana?"},
        "presets": [(lambda: _t("Por defecto (numerado)"), "Mensaje {num} de {author}: {content}"),
                    (lambda: _t("Breve (autor: contenido)"), "{author}: {content}")],
    },
    # ---------------- BLUESKY ----------------
    {
        "clave": "bluesky_post", "red": "bluesky",
        "nombre": lambda: _t("Publicaciones"),
        "campos": _CAMPOS_POST_AT,
        "default": "De {author}. {content}. {date}. {counts}.",
        "ejemplo": {"author": "Aurora", "username": "@aurora.bsky.social",
                    "content": "¡Buen día en Bluesky!", "date": "hace 5 minutos",
                    "indicators": "Me gusta", "media": "1 imagen", "cw": "",
                    "counts": "9 me gusta, 5 reposts, 3 respuestas, 1 cita", "language": "es",
                    "reply_to": "En respuesta a @luis.bsky.social"},
        "presets": _PRESETS_POST_AT,
    },
    {
        "clave": "bluesky_notification", "red": "bluesky",
        "nombre": lambda: _t("Notificaciones"),
        "campos": _CAMPOS_NOTIF, "default": "{type}: {author}. {content}. {date}.",
        "ejemplo": dict(_EJ_NOTIF, username="@aurora.bsky.social"), "presets": _PRESETS_NOTIF,
    },
    {
        "clave": "bluesky_person", "red": "bluesky",
        "nombre": lambda: _t("Perfiles de cuenta"),
        "campos": _CAMPOS_PERSONA,
        "default": "{display} (@{acct}). {followers} seguidores, {following} siguiendo, {posts} publicaciones. {bio}",
        "ejemplo": dict(_EJ_PERSONA), "presets": _PRESETS_PERSONA,
    },
    {
        "clave": "bluesky_conversation", "red": "bluesky",
        "nombre": lambda: _t("Mensajes directos"),
        "campos": _CAMPOS_CONV, "default": "Mensaje {num} de {author}: {content}",
        "ejemplo": dict(_EJ_CONV), "presets": _PRESETS_CONV,
    },
    # ---------------- W SOCIAL ----------------
    # W Social usa los mismos campos que Bluesky (comparten los lexicons de AT Protocol), pero
    # con plantillas propias: cada red se lee como el usuario quiera, sin arrastrar a la otra.
    {
        "clave": "wsocial_post", "red": "wsocial",
        "nombre": lambda: _t("Publicaciones"),
        "campos": _CAMPOS_POST_AT,
        "default": "De {author}. {content}. {date}. {counts}.",
        "ejemplo": {"author": "Aurora", "username": "@aurora.wsocial.eu",
                    "content": "¡Buenos días desde W Social!", "date": "hace 5 minutos",
                    "indicators": "Me gusta, Cuenta verificada", "media": "1 imagen", "cw": "",
                    "counts": "9 me gusta, 5 republicaciones, 3 respuestas, 1 cita",
                    "language": "es", "reply_to": "En respuesta a @luis.wsocial.eu"},
        "presets": _PRESETS_POST_AT,
    },
    {
        "clave": "wsocial_notification", "red": "wsocial",
        "nombre": lambda: _t("Notificaciones"),
        "campos": _CAMPOS_NOTIF, "default": "{type}: {author}. {content}. {date}.",
        "ejemplo": dict(_EJ_NOTIF, username="@aurora.wsocial.eu"), "presets": _PRESETS_NOTIF,
    },
    {
        "clave": "wsocial_person", "red": "wsocial",
        "nombre": lambda: _t("Perfiles de cuenta"),
        "campos": _CAMPOS_PERSONA,
        "default": "{display} (@{acct}). {followers} seguidores, {following} siguiendo, {posts} publicaciones. {bio}",
        "ejemplo": dict(_EJ_PERSONA), "presets": _PRESETS_PERSONA,
    },
    {
        "clave": "wsocial_conversation", "red": "wsocial",
        "nombre": lambda: _t("Mensajes directos y grupos"),
        "campos": _CAMPOS_CONV, "default": "Mensaje {num} de {author}: {content}",
        "ejemplo": dict(_EJ_CONV), "presets": _PRESETS_CONV,
    },
    # ---------------- REDDIT ----------------
    {
        "clave": "reddit_post", "red": "reddit",
        "nombre": lambda: _t("Publicaciones"),
        "campos": [
            ("subreddit", lambda: _t("subreddit (r/nombre)")),
            ("author", lambda: _t("autor (u/usuario)")),
            ("title", lambda: _t("título")),
            ("content", lambda: _t("cuerpo o dominio del enlace")),
            ("date", lambda: _t("fecha relativa")),
            ("score", lambda: _t("puntuación (votos)")),
            ("comments", lambda: _t("número de comentarios")),
            ("indicators", lambda: _t("indicadores: guardado, voto, NSFW, spoiler, fijado, bloqueado")),
            ("media", lambda: _t("adjuntos")),
            ("flair", lambda: _t("etiqueta (flair) del post")),
            ("language", lambda: _t("idioma (para traducción)")),
        ],
        "default": "{subreddit}. {title}. De {author}. {content}. {date}. {score} puntos, {comments} comentarios. {indicators} {media}",
        "ejemplo": {"subreddit": "r/python", "author": "u/aurora", "title": "Nuevo lanzamiento",
                    "content": "Ya está disponible la versión...", "date": "hace 5 minutos",
                    "score": "128", "comments": "34", "indicators": "Guardado", "media": "1 imagen",
                    "flair": "Noticia", "language": "es"},
        "presets": [
            (lambda: _t("Por defecto (subreddit, título, autor...)"), "{subreddit}. {title}. De {author}. {content}. {date}. {score} puntos, {comments} comentarios. {indicators} {media}"),
            (lambda: _t("Título primero"), "{title}. {subreddit}. De {author}. {date}. {score} puntos, {comments} comentarios."),
            (lambda: _t("Con etiqueta (flair)"), "{subreddit}. {flair} {title}. De {author}. {content}. {date}. {score} puntos, {comments} comentarios."),
            (lambda: _t("Breve (subreddit: título)"), "{subreddit}: {title}. {score} puntos, {comments} comentarios."),
        ],
    },
    {
        "clave": "reddit_comment", "red": "reddit",
        "nombre": lambda: _t("Comentarios"),
        "campos": [
            ("author", lambda: _t("autor (u/usuario)")),
            ("body", lambda: _t("texto del comentario")),
            ("date", lambda: _t("fecha relativa")),
            ("score", lambda: _t("puntuación (votos)")),
            ("indicators", lambda: _t("indicadores: guardado, voto, autor original, fijado")),
            ("subreddit", lambda: _t("subreddit (r/nombre)")),
        ],
        "default": "{author}: {body}. {date}. {score} puntos. {indicators}",
        "ejemplo": {"author": "u/aurora", "body": "Totalmente de acuerdo.", "date": "hace 2 minutos",
                    "score": "12", "indicators": "Autor original", "subreddit": "r/python"},
        "presets": [
            (lambda: _t("Por defecto"), "{author}: {body}. {date}. {score} puntos. {indicators}"),
            (lambda: _t("Con fecha primero"), "{date}. {author}: {body}. {score} puntos."),
            (lambda: _t("Breve (autor: texto)"), "{author}: {body}"),
        ],
    },
    {
        "clave": "reddit_message", "red": "reddit",
        "nombre": lambda: _t("Bandeja de entrada"),
        "campos": [
            ("type", lambda: _t("tipo (Mensaje, Respuesta, Mención...)")),
            ("author", lambda: _t("autor (u/usuario)")),
            ("subject", lambda: _t("asunto")),
            ("content", lambda: _t("texto del mensaje")),
            ("date", lambda: _t("fecha relativa")),
        ],
        "default": "{type} de {author}: {subject}. {content}. {date}",
        "ejemplo": {"type": "Respuesta a comentario", "author": "u/aurora",
                    "subject": "re: tu comentario", "content": "Gracias por la ayuda.",
                    "date": "hace 10 minutos"},
        "presets": [
            (lambda: _t("Por defecto"), "{type} de {author}: {subject}. {content}. {date}"),
            (lambda: _t("Breve"), "{type} de {author}: {content}"),
        ],
    },
    {
        "clave": "reddit_person", "red": "reddit",
        "nombre": lambda: _t("Perfiles de usuario"),
        "campos": [
            ("name", lambda: _t("usuario (u/nombre)")),
            ("post_karma", lambda: _t("karma de publicaciones")),
            ("comment_karma", lambda: _t("karma de comentarios")),
            ("created", lambda: _t("antigüedad de la cuenta")),
            ("bio", lambda: _t("descripción")),
        ],
        "default": "{name}. {post_karma} karma de publicaciones, {comment_karma} de comentarios. {created}. {bio}",
        "ejemplo": {"name": "u/aurora", "post_karma": "5400", "comment_karma": "12000",
                    "created": "cuenta creada hace 3 años", "bio": "Programadora y café."},
        "presets": [
            (lambda: _t("Por defecto (completo)"), "{name}. {post_karma} karma de publicaciones, {comment_karma} de comentarios. {created}. {bio}"),
            (lambda: _t("Breve (usuario y descripción)"), "{name}. {bio}"),
            (lambda: _t("Solo karma"), "{name}: {post_karma} karma de publicaciones, {comment_karma} de comentarios."),
        ],
    },
    {
        "clave": "reddit_subreddit", "red": "reddit",
        "nombre": lambda: _t("Lista de subreddits"),
        "campos": [
            ("subreddit", lambda: _t("subreddit (r/nombre)")),
            ("title", lambda: _t("título")),
            ("subscribers", lambda: _t("número de suscriptores")),
            ("description", lambda: _t("descripción")),
        ],
        "default": "{subreddit}. {title}. {subscribers} suscriptores. {description}",
        "ejemplo": {"subreddit": "r/python", "title": "Python", "subscribers": "1200000",
                    "description": "Noticias sobre Python."},
        "presets": [
            (lambda: _t("Por defecto"), "{subreddit}. {title}. {subscribers} suscriptores. {description}"),
            (lambda: _t("Breve"), "{subreddit}: {title}"),
        ],
    },
    # ---------------- PEERTUBE ----------------
    {
        "clave": "peertube_video", "red": "peertube",
        "nombre": lambda: _t("Vídeos"),
        "campos": [
            ("title", lambda: _t("título")),
            ("channel", lambda: _t("canal")),
            ("duration", lambda: _t("duración")),
            ("date", lambda: _t("fecha relativa")),
            ("views", lambda: _t("visualizaciones")),
            ("likes", lambda: _t("me gusta")),
            ("indicators", lambda: _t("indicadores: directo, +18, local")),
            ("description", lambda: _t("descripción")),
            ("language", lambda: _t("idioma")),
        ],
        "default": "{title}. {channel}. {duration}. {date}. {views} visualizaciones. {indicators}",
        "ejemplo": {"title": "Cómo funciona la federación", "channel": "Canal de Frank",
                    "duration": "12 minutos", "date": "hace 3 días", "views": "1520",
                    "likes": "84", "indicators": "", "description": "Una introducción...",
                    "language": "es"},
        "presets": [
            (lambda: _t("Por defecto (título, canal, duración...)"), "{title}. {channel}. {duration}. {date}. {views} visualizaciones. {indicators}"),
            (lambda: _t("Título y canal"), "{title}. De {channel}. {date}."),
            (lambda: _t("Breve (título y duración)"), "{title}. {duration}."),
            (lambda: _t("Con visualizaciones y me gusta"), "{title}. {channel}. {views} visualizaciones, {likes} me gusta. {duration}."),
        ],
    },
    {
        "clave": "peertube_canal", "red": "peertube",
        "nombre": lambda: _t("Canales"),
        "campos": [
            ("channel", lambda: _t("nombre del canal (handle)")),
            ("display", lambda: _t("nombre visible")),
            ("followers", lambda: _t("seguidores")),
            ("description", lambda: _t("descripción")),
        ],
        "default": "{display} ({channel}). {followers} seguidores. {description}",
        "ejemplo": {"channel": "frank@video.instancia", "display": "Canal de Frank",
                    "followers": "320", "description": "Vídeos sobre software libre."},
        "presets": [
            (lambda: _t("Por defecto"), "{display} ({channel}). {followers} seguidores. {description}"),
            (lambda: _t("Breve"), "{display}: {description}"),
        ],
    },
    {
        "clave": "peertube_notificacion", "red": "peertube",
        "nombre": lambda: _t("Notificaciones"),
        "campos": [
            ("descripcion", lambda: _t("descripción de la notificación")),
            ("date", lambda: _t("fecha relativa")),
            ("estado", lambda: _t("estado (nueva / leída)")),
        ],
        "default": "{descripcion}. {date}. {estado}",
        "ejemplo": {"descripcion": "Nuevo vídeo de Canal X: Introducción", "date": "hace 1 hora",
                    "estado": "Nueva"},
        "presets": [
            (lambda: _t("Por defecto"), "{descripcion}. {date}. {estado}"),
            (lambda: _t("Con estado primero"), "{estado}. {descripcion}. {date}"),
        ],
    },
    {
        "clave": "peertube_cuenta", "red": "peertube",
        "nombre": lambda: _t("Cuentas"),
        "campos": [
            ("account", lambda: _t("cuenta (handle)")),
            ("display", lambda: _t("nombre visible")),
            ("followers", lambda: _t("seguidores")),
            ("description", lambda: _t("descripción")),
        ],
        "default": "{display} ({account}). {followers} seguidores. {description}",
        "ejemplo": {"account": "frank@video.instancia", "display": "Frank",
                    "followers": "540", "description": "Creador de contenido."},
        "presets": [
            (lambda: _t("Por defecto"), "{display} ({account}). {followers} seguidores. {description}"),
            (lambda: _t("Breve"), "{display}: {description}"),
        ],
    },
    # ---------------- TELEGRAM ----------------
    {
        "clave": "telegram_chat", "red": "telegram",
        "nombre": lambda: _t("Lista de chats"),
        "campos": [("chat", lambda: _t("nombre del chat")), ("type", lambda: _t("tipo: Privado, Grupo, Canal...")),
                   ("content", lambda: _t("último mensaje")), ("date", lambda: _t("fecha")),
                   ("unread", lambda: _t("nº de no leídos")),
                   ("silenciado", lambda: _t("'Silenciado' si el chat está silenciado")),
                   ("fijado", lambda: _t("'Fijado' si está fijado en la lista")),
                   ("verificado", lambda: _t("'Verificado' si es una cuenta/canal verificado")),
                   ("protegido", lambda: _t("'Protegido' si el chat tiene contenido protegido"))],
        "default": "{unread}. {chat}: {content}. {date}",
        "ejemplo": {"chat": "Grupo de amigos", "type": "Grupo", "content": "¿Quedamos?",
                    "date": "hace 3 minutos", "unread": "2 sin leer", "silenciado": "Silenciado",
                    "fijado": "Fijado", "verificado": "Verificado", "protegido": "Protegido"},
        "presets": [(lambda: _t("Por defecto (no leídos al principio)"), "{unread}. {chat}: {content}. {date}"),
                    (lambda: _t("No leídos al final"), "{chat}: {content}. {date}. {unread}"),
                    (lambda: _t("Con tipo"), "{unread}. {chat} ({type}): {content}. {date}"),
                    (lambda: _t("Con estado (silenciado/fijado)"), "{unread}. {fijado} {silenciado} {chat}: {content}. {date}"),
                    (lambda: _t("Con verificado"), "{unread}. {chat} {verificado}: {content}. {date}"),
                    (lambda: _t("Solo chat y no leídos"), "{unread}. {chat}")],
    },
    {
        "clave": "telegram_message", "red": "telegram",
        "nombre": lambda: _t("Mensajes de una conversación"),
        "campos": [("num", lambda: _t("número de mensaje")),
                   ("author", lambda: _t("nombre del autor (nombre y apellido, o el contacto renombrado)")),
                   ("username", lambda: _t("usuario (@usuario), si lo tiene")),
                   ("content", lambda: _t("texto del mensaje")), ("media", lambda: _t("adjunto: audio, foto, vídeo...")),
                   ("descarga", lambda: _t("estado del adjunto: 'Descargado' si el archivo está en disco")),
                   ("flags", lambda: _t("estado: Editado si el mensaje se editó")),
                   ("respuesta", lambda: _t("indica 'Respuesta' si responde a otro mensaje")),
                   ("reenviado", lambda: _t("'Reenviado de ...' si es un reenvío")),
                   ("vistas", lambda: _t("número de vistas (publicaciones de canal)")),
                   ("reactions", lambda: _t("reacciones del mensaje (por ejemplo 👍 3 · ❤ 1)")),
                   ("botones", lambda: _t("'Con botones' si el mensaje tiene botones de bot")),
                   ("album", lambda: _t("'Álbum' si el mensaje forma parte de un envío de varios adjuntos")),
                   ("date", lambda: _t("fecha"))],
        "default": "{author} ({date}): {content} {media}",
        "ejemplo": {"num": "2", "author": "Pedro Pérez", "username": "@pedroperez24", "content": "Escucha esto",
                    "media": "[Nota de voz 0:15]", "descarga": "Descargado", "flags": "Editado", "respuesta": "Respuesta",
                    "reenviado": "Reenviado de Canal X", "vistas": "1200 vistas", "reactions": "👍 3 · ❤ 1",
                    "botones": "Con botones", "album": "Álbum", "date": "hace 1 minuto"},
        "presets": [(lambda: _t("Por defecto (con adjunto)"), "{author} ({date}): {content} {media}"),
                    (lambda: _t("Numerado"), "Mensaje {num} de {author}: {content} {media}"),
                    (lambda: _t("Con usuario (@usuario)"), "{author} ({username}) ({date}): {content} {media}"),
                    (lambda: _t("Con adjunto y estado de descarga"), "{author} ({date}): {content} {media} {descarga}"),
                    (lambda: _t("Con estado (editado)"), "{author} ({date}): {content} {media} {flags}"),
                    (lambda: _t("Con reenvío y respuesta"), "{author} ({date}): {reenviado} {respuesta} {content} {media}"),
                    (lambda: _t("Canal (con vistas)"), "{author} ({date}): {content} {media} {vistas}"),
                    (lambda: _t("Con reacciones"), "{author} ({date}): {content} {media} {reactions}"),
                    (lambda: _t("Completa (reacciones, botones, álbum)"), "{author} ({date}): {content} {media} {reactions} {botones} {album}"),
                    (lambda: _t("Breve (autor: contenido)"), "{author}: {content}")],
    },
    {
        "clave": "telegram_miembro", "red": "telegram",
        "nombre": lambda: _t("Miembros de un grupo/canal"),
        "campos": [("nombre", lambda: _t("nombre mostrado")),
                   ("username", lambda: _t("usuario (@usuario), si lo tiene")),
                   ("rol", lambda: _t("rol: Dueño, Administrador, Miembro, Restringido, Baneado…")),
                   ("presencia", lambda: _t("estado: En línea, última conexión…")),
                   ("titulo", lambda: _t("título de administrador, si lo tiene")),
                   ("tipo", lambda: _t("'Bot' si es un bot")),
                   ("id", lambda: _t("identificador numérico del usuario"))],
        "default": "{nombre} ({username}). {rol}. {presencia}",
        "ejemplo": {"nombre": "Héctor", "username": "@xebolax", "rol": "Dueño",
                    "presencia": "En línea", "titulo": "Jefazo", "tipo": "", "id": "123456789"},
        "presets": [(lambda: _t("Por defecto"), "{nombre} ({username}). {rol}. {presencia}"),
                    (lambda: _t("Con título de admin"), "{nombre} ({username}). {rol} {titulo}. {presencia}"),
                    (lambda: _t("Breve (nombre y rol)"), "{nombre}. {rol}"),
                    (lambda: _t("Nombre, usuario y estado"), "{nombre} ({username}). {presencia}"),
                    (lambda: _t("Con tipo (bot) e id"), "{nombre} ({username}). {rol} {tipo}. {id}")],
    },
    {
        "clave": "tgdrive_items", "red": "telegram",
        "nombre": lambda: _t("Telegram Drive (nube): lista de archivos y carpetas"),
        "campos": [("marcado", lambda: _t("estado de la casilla: Sí si está marcado, No si no")),
                   ("nombre", lambda: _t("nombre del archivo o carpeta")),
                   ("tipo", lambda: _t("tipo: Carpeta o Archivo")),
                   ("tam", lambda: _t("tamaño (vacío en las carpetas)")),
                   ("modificado", lambda: _t("fecha de modificación")),
                   ("descripcion", lambda: _t("descripción que pusiste, si la hay")),
                   ("estado", lambda: _t("estado: vacío, En la papelera, Subiendo..."))],
        "default": "{marcado} {nombre}. {tipo}. {tam}. {modificado}. {descripcion} {estado}",
        "ejemplo": {"marcado": "No", "nombre": "Prueba", "tipo": "Carpeta", "tam": "",
                    "modificado": "2026-07-12 11:29", "descripcion": "fotos de vacaciones", "estado": ""},
        "presets": [
            (lambda: _t("Por defecto (marcado, nombre, tipo, tamaño, fecha, descripción)"),
             "{marcado} {nombre}. {tipo}. {tam}. {modificado}. {descripcion} {estado}"),
            (lambda: _t("Nombre y tamaño"), "{marcado} {nombre}. {tam}"),
            (lambda: _t("Nombre y descripción"), "{marcado} {nombre}. {descripcion}"),
            (lambda: _t("Con tipo primero"), "{marcado} {tipo}: {nombre}. {tam}. {modificado}. {descripcion}"),
            (lambda: _t("Completo (todo)"), "{marcado} {nombre}. {tipo}. {tam}. {modificado}. {descripcion}. {estado}"),
            (lambda: _t("Solo el nombre"), "{marcado} {nombre}"),
        ],
    },
    # ---------------- RSS ----------------
    {
        "clave": "rss_template", "red": "rss",
        "nombre": lambda: _t("Artículos"),
        "campos": [("feed", lambda: _t("nombre del feed")), ("title", lambda: _t("título del artículo")),
                   ("author", lambda: _t("autor del artículo")), ("date", lambda: _t("fecha")),
                   ("flags", lambda: _t("marcas: leído, destacado..."))],
        "default": "{feed}: {title}. {date}. {flags}.",
        "ejemplo": {"feed": "Noticias", "title": "Novedad importante", "author": "Redacción",
                    "date": "hace 3 horas", "flags": "No leído"},
        "presets": [
            (lambda: _t("Por defecto"), "{feed}: {title}. {date}. {flags}."),
            (lambda: _t("Fecha primero"), "{date}. {feed}: {title}. {flags}."),
            (lambda: _t("Título primero"), "{title}. De {feed}. {date}. {flags}."),
            (lambda: _t("Con autor"), "{feed}: {title}. {author}. {date}. {flags}."),
            (lambda: _t("Solo título"), "{title}"),
        ],
    },
    # ---------------- IRC ----------------
    {
        "clave": "irc_chat", "red": "irc",
        "nombre": lambda: _t("Lista de canales y privados"),
        "campos": [("chat", lambda: _t("nombre del canal o privado")),
                   ("content", lambda: _t("último mensaje")),
                   ("unread", lambda: _t("nº de no leídos")),
                   ("topic", lambda: _t("tema del canal"))],
        "default": "{unread}. {chat}: {content}",
        "ejemplo": {"chat": "#nvda", "content": "ana: ¿alguien probó la beta?",
                    "unread": "3 sin leer", "topic": "Canal de NVDA en español"},
        "presets": [(lambda: _t("Por defecto (no leídos al principio)"), "{unread}. {chat}: {content}"),
                    (lambda: _t("No leídos al final"), "{chat}: {content}. {unread}"),
                    (lambda: _t("Con tema"), "{unread}. {chat}: {content}. Tema: {topic}"),
                    (lambda: _t("Solo canal y no leídos"), "{unread}. {chat}")],
    },
    {
        "clave": "irc_message", "red": "irc",
        "nombre": lambda: _t("Mensajes de una conversación"),
        "campos": [("nick", lambda: _t("apodo de quien habla (o '* apodo' en las acciones)")),
                   ("content", lambda: _t("texto del mensaje")),
                   ("hora", lambda: _t("hora del mensaje")),
                   ("canal", lambda: _t("canal o privado"))],
        "default": "{nick}: {content}. {hora}",
        "ejemplo": {"nick": "ana", "content": "¿alguien probó la beta?",
                    "hora": "12:45", "canal": "#nvda"},
        "presets": [(lambda: _t("Por defecto"), "{nick}: {content}. {hora}"),
                    (lambda: _t("Sin hora"), "{nick}: {content}"),
                    (lambda: _t("Hora primero"), "{hora}. {nick}: {content}"),
                    (lambda: _t("Con canal"), "{canal}. {nick}: {content}. {hora}")],
    },
    # ---------------- Matrix ----------------
    {
        "clave": "matrix_chat", "red": "matrix",
        "nombre": lambda: _t("Lista de salas y directos"),
        "campos": [("sala", lambda: _t("nombre de la sala o directo (con sus marcas: cifrada, espacio...)")),
                   ("content", lambda: _t("último mensaje")),
                   ("unread", lambda: _t("nº de no leídos")),
                   ("topic", lambda: _t("tema de la sala"))],
        "default": "{unread}. {sala}: {content}",
        "ejemplo": {"sala": "Accesibilidad ES (cifrada)", "content": "ana: ¿probasteis la beta?",
                    "unread": "3 sin leer", "topic": "Sala de accesibilidad en español"},
        "presets": [(lambda: _t("Por defecto (no leídos al principio)"), "{unread}. {sala}: {content}"),
                    (lambda: _t("No leídos al final"), "{sala}: {content}. {unread}"),
                    (lambda: _t("Con tema"), "{unread}. {sala}: {content}. Tema: {topic}"),
                    (lambda: _t("Solo sala y no leídos"), "{unread}. {sala}")],
    },
    {
        "clave": "matrix_message", "red": "matrix",
        "nombre": lambda: _t("Mensajes de una conversación"),
        "campos": [("author", lambda: _t("quién habla (nombre mostrado)")),
                   ("content", lambda: _t("texto del mensaje (con sus marcas: respuesta, editado, reacciones...)")),
                   ("hora", lambda: _t("hora del mensaje")),
                   ("sala", lambda: _t("sala o directo"))],
        "default": "{author}: {content}. {hora}",
        "ejemplo": {"author": "Ana", "content": "¿probasteis la beta? (editado)",
                    "hora": "12:45", "sala": "Accesibilidad ES"},
        "presets": [(lambda: _t("Por defecto"), "{author}: {content}. {hora}"),
                    (lambda: _t("Sin hora"), "{author}: {content}"),
                    (lambda: _t("Hora primero"), "{hora}. {author}: {content}"),
                    (lambda: _t("Con sala"), "{sala}. {author}: {content}. {hora}")],
    },
    # ---------------- XMPP ----------------
    {
        "clave": "xmpp_chat", "red": "xmpp",
        "nombre": lambda: _t("Lista de contactos"),
        "campos": [("contacto", lambda: _t("nombre del contacto")),
                   ("presencia", lambda: _t("presencia en palabras (Disponible, Ausente, Sin conexión...)")),
                   ("content", lambda: _t("último mensaje")),
                   ("unread", lambda: _t("nº de no leídos")),
                   ("estado", lambda: _t("mensaje de estado del contacto"))],
        "default": "{unread}. {contacto} ({presencia}). {content}",
        "ejemplo": {"contacto": "Nuk", "presencia": "Ausente", "content": "nos vemos luego",
                    "unread": "2 sin leer", "estado": "comiendo"},
        "presets": [(lambda: _t("Por defecto (no leídos al principio)"), "{unread}. {contacto} ({presencia}). {content}"),
                    (lambda: _t("No leídos al final"), "{contacto} ({presencia}). {content}. {unread}"),
                    (lambda: _t("Solo contacto y presencia"), "{contacto}: {presencia}"),
                    (lambda: _t("Con estado"), "{unread}. {contacto} ({presencia} — {estado}). {content}")],
    },
    {
        "clave": "xmpp_message", "red": "xmpp",
        "nombre": lambda: _t("Mensajes de una conversación"),
        "campos": [("author", lambda: _t("quién habla")),
                   ("content", lambda: _t("texto del mensaje (o etiqueta del adjunto, con sus marcas)")),
                   ("hora", lambda: _t("hora del mensaje")),
                   ("contacto", lambda: _t("contacto o sala")),
                   ("media", lambda: _t("tipo de adjunto: Mensaje de voz, Mensaje de audio, Imagen, Vídeo, Archivo")),
                   ("duracion", lambda: _t("duración del audio/nota de voz (m:ss), vacío si no aplica")),
                   ("estado", lambda: _t("estado de la nota de voz: pendiente o reproducido")),
                   ("reacciones", lambda: _t("reacciones al mensaje")),
                   ("cita", lambda: _t("mensaje citado al que se responde"))],
        "default": "{author}: {content}. {hora}",
        "ejemplo": {"author": "Nuk", "content": "Mensaje de voz 0:15 (pendiente)", "hora": "12:45",
                    "contacto": "Nuk", "media": "Mensaje de voz", "duracion": "0:15",
                    "estado": "pendiente", "reacciones": "👍 2", "cita": ""},
        "presets": [(lambda: _t("Por defecto"), "{author}: {content}. {hora}"),
                    (lambda: _t("Sin hora"), "{author}: {content}"),
                    (lambda: _t("Hora primero"), "{hora}. {author}: {content}"),
                    (lambda: _t("Voz con duración y estado"), "{author}: {media} {duracion} {estado}. {hora}")],
    },
    # ---------------- X (Twitter) ----------------
    {
        "clave": "x_post", "red": "x",
        "nombre": lambda: _t("Publicaciones"),
        # X también sabe a quién responde cada publicación (in_reply_to_screen_name).
        "campos": _CAMPOS_POST_AT,
        "default": "De {author}. {content}. {date}. {counts}.",
        "ejemplo": {"author": "Aurora", "username": "@aurora", "content": "Probando X.",
                    "date": "hace 5 minutos", "indicators": "Me gusta", "media": "", "cw": "",
                    "counts": "9 me gusta, 5 reposts, 3 respuestas", "language": "es"},
        "presets": _PRESETS_POST,
    },
    {
        "clave": "x_notification", "red": "x",
        "nombre": lambda: _t("Notificaciones"),
        "campos": _CAMPOS_NOTIF, "default": "{type}: {author}. {content}. {date}.",
        "ejemplo": dict(_EJ_NOTIF), "presets": _PRESETS_NOTIF,
    },
    # ---------------- BANDEJA DE ENTRADA (unificada, multi-red) ----------------
    {
        "clave": "inbox_template", "red": "inbox",
        "nombre": lambda: _t("Bandeja de entrada (unificada)"),
        "campos": [
            ("origen", lambda: _t("origen: red, cuenta y buffer de donde viene la publicación")),
            ("author", lambda: _t("nombre del autor")),
            ("content", lambda: _t("texto de la publicación")),
            ("date", lambda: _t("fecha relativa")),
        ],
        "default": "{origen}. De {author}. {content}. {date}.",
        "ejemplo": {"origen": "Mastodon · Mi cuenta · Inicio", "author": "Aurora",
                    "content": "¡Hola desde la bandeja unificada!", "date": "hace 5 minutos"},
        "presets": [
            (lambda: _t("Por defecto (origen, autor, contenido, fecha)"), "{origen}. De {author}. {content}. {date}."),
            (lambda: _t("Origen al final"), "De {author}. {content}. {date}. {origen}."),
            (lambda: _t("Contenido primero"), "{content}. De {author}. {date}. {origen}."),
            (lambda: _t("Breve (origen y contenido)"), "{origen}: {content}"),
        ],
    },
    # ---------------- REGISTROS (LOGS) ----------------
    # El Visor de Registros usa esta plantilla para decidir QUÉ columnas tiene la lista del log y en
    # qué orden (una por cada {campo}); el lector lee las columnas en ese orden y se navega por ellas.
    {
        "clave": "log_template", "red": "logs",
        "nombre": lambda: _t("Entradas del registro"),
        "campos": [
            ("hora", lambda: _t("hora de la entrada (HH:MM:SS)")),
            ("nivel", lambda: _t("nivel: DEBUG, INFO, WARNING, ERROR")),
            ("modulo", lambda: _t("módulo y línea que lo registró")),
            ("mensaje", lambda: _t("texto del mensaje (incluye la traza si es un error)")),
        ],
        "default": "{hora} {nivel} {modulo}: {mensaje}",
        "ejemplo": {"hora": "10:09:51", "nivel": "INFO", "modulo": "app.core.speech:36",
                    "mensaje": "Lector de pantalla inicializado con éxito."},
        "presets": [
            (lambda: _t("Por defecto (hora, nivel, módulo, mensaje)"), "{hora} {nivel} {modulo}: {mensaje}"),
            (lambda: _t("Nivel y mensaje"), "{nivel}: {mensaje}"),
            (lambda: _t("Hora y mensaje"), "{hora}: {mensaje}"),
            (lambda: _t("Con módulo entre corchetes"), "{hora} {nivel} [{modulo}] {mensaje}"),
            (lambda: _t("Solo mensaje"), "{mensaje}"),
        ],
    },
]


# Migración: claves históricas COMPARTIDAS cuyo valor del usuario hay que copiar a las nuevas
# claves por red al arrancar (separación total conservando lo configurado).
_MIGRACION_PLANTILLAS = {
    # clave histórica -> claves nuevas que heredan su valor si aún no están definidas
    "notification_template": ["bluesky_notification", "x_notification"],
    "person_template": ["bluesky_person"],
    "conversation_template": ["bluesky_conversation", "telegram_message"],
}


def migrar_plantillas(config) -> bool:
    """Copia el valor de las plantillas históricas compartidas a las nuevas claves por red, solo
    si el usuario las tenía personalizadas y la clave nueva aún no existe. Devuelve True si cambió
    algo (para guardar). Idempotente: no pisa lo ya migrado ni lo que el usuario haya tocado."""
    cambiado = False
    for origen, destinos in _MIGRACION_PLANTILLAS.items():
        valor = config.get("accessibility", origen, None)
        if not valor:
            continue
        for destino in destinos:
            if config.get("accessibility", destino, None) is None:
                config.set("accessibility", destino, valor)
                cambiado = True
    return cambiado


def definicion(clave):
    for p in PLANTILLAS:
        if p["clave"] == clave:
            return p
    return None


def plantillas_de_red(red: str):
    """Lista de definiciones de plantilla de una red, en orden."""
    return [p for p in PLANTILLAS if p.get("red") == red]


def render_ejemplo(clave: str, plantilla_str: str) -> str:
    """Devuelve la VISTA PREVIA de una plantilla con datos de ejemplo, ya pulida."""
    d = definicion(clave)
    if not d:
        return ""
    try:
        from collections import defaultdict
        datos = defaultdict(str, d["ejemplo"])
        texto = plantilla_str.format_map(datos)
    except Exception:
        texto = plantilla_str
    return pulir_lectura(texto)
