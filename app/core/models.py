# -*- coding: utf-8 -*-
"""
Módulo para la definición de modelos de datos (dataclasses) compartidos en la aplicación.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class ServiceInfo:
    """Información básica de un servicio o plugin."""
    id: str           # Ej: 'mastodon', 'rss'
    name: str         # Ej: 'Mastodon', 'Lector RSS'
    active: bool = True

@dataclass
class AccountInfo:
    """Información de una cuenta asociada a un servicio."""
    id: int
    name: str         # Nombre local visible
    service_id: str   # ID del servicio asociado
    config: Dict[str, Any] = field(default_factory=dict)
    active: bool = True

@dataclass
class RSSCategory:
    """Categoría para organizar feeds RSS."""
    id: int
    name: str

@dataclass
class RSSFeed:
    """Feed RSS registrado."""
    id: int
    account_id: int
    url: str
    title: str
    category_id: Optional[int] = None
    update_interval: int = 30  # En minutos
    active: bool = True
    last_error: Optional[str] = None
    pinned: bool = False   # Feed fijado (aparece arriba en el árbol)
    paused: bool = False   # Feed en pausa (no se actualiza)

@dataclass
class RSSArticle:
    """Artículo RSS cacheado."""
    id: str           # Hash o GUID único
    feed_id: int
    guid: str
    link: str
    title: str
    content: str
    author: Optional[str] = None
    published_at: Optional[str] = None
    read: bool = False
    favorite: bool = False
    has_media: bool = False
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    translated_content: Optional[str] = None
    ai_summary: Optional[str] = None
    read_later: bool = False   # Marcado como "Leer más tarde"

@dataclass
class MastodonStatus:
    """Publicación de Mastodon cacheada."""
    id: str           # ID local o ID remoto
    account_id: int
    status_id: str    # ID original en la instancia
    uri: str
    author_name: str
    author_username: str
    content: str
    created_at: str
    visibility: str   # public, unlisted, private, direct
    nsfw: bool = False
    spoiler_text: Optional[str] = None
    favourited: bool = False
    reblogged: bool = False
    bookmarked: bool = False
    media_attachments: List[Dict[str, Any]] = field(default_factory=list)
    card: Optional[Dict[str, Any]] = None
    reblog: Optional[Dict[str, Any]] = None
    mentions: List[Dict[str, Any]] = field(default_factory=list)
    reblog_by_name: Optional[str] = None      # Nombre de quien impulsó (boost)
    reblog_by_username: Optional[str] = None   # Usuario de quien impulsó (boost)
    # Campos oficiales de la entidad Status (alineación con Mastodon):
    edited_at: Optional[str] = None            # fecha de última edición (vacío = no editado)
    in_reply_to_id: Optional[str] = None       # id del status al que responde
    in_reply_to_account_id: Optional[str] = None
    language: Optional[str] = None             # ISO 639-1
    replies_count: int = 0
    reblogs_count: int = 0
    favourites_count: int = 0
    quotes_count: int = 0
    pinned: bool = False

@dataclass
class BlueskyPost:
    """Publicación de Bluesky / AT Protocol cacheada."""
    id: str               # "<account_id>:<uri>"
    account_id: int
    uri: str              # at://did/app.bsky.feed.post/<rkey> (identidad)
    cid: str              # hash de contenido (necesario para like/repost/quote)
    author_did: str
    author_handle: str
    author_name: str
    content: str
    created_at: str
    indexed_at: str
    langs: Optional[str] = None
    labels: Optional[str] = None          # self-labels CSV (CW)
    reply_root_uri: Optional[str] = None
    reply_root_cid: Optional[str] = None
    reply_parent_uri: Optional[str] = None
    reply_parent_cid: Optional[str] = None
    quote_uri: Optional[str] = None
    like_count: int = 0
    repost_count: int = 0
    reply_count: int = 0
    quote_count: int = 0
    liked_uri: Optional[str] = None        # viewer.like (para quitar like)
    reposted_uri: Optional[str] = None     # viewer.repost (para quitar repost)
    bookmarked: bool = False
    media_attachments: List[Dict[str, Any]] = field(default_factory=list)
    external_card: Optional[Dict[str, Any]] = None
    urls: List[str] = field(default_factory=list)
    repost_by_name: Optional[str] = None     # autor del repost (paridad reblog_by_*)
    repost_by_handle: Optional[str] = None
    # Datos de moderación compactos (etiquetas con origen, autor, cita, textos para las
    # palabras silenciadas): ver app/services/bluesky/bluesky_moddatos.py.
    mod_json: Optional[str] = None

@dataclass
class WSocialPost:
    """Publicación de W Social (AT Protocol) cacheada.

    W Social usa los mismos lexicons que Bluesky, así que la forma es la misma; se mantiene un
    modelo propio porque cada red tiene sus tablas y su ciclo de vida (y para que un cambio en
    una no arrastre a la otra). `verificado` refleja la verificación de cuenta que W destaca."""
    id: str               # "<account_id>:<uri>"
    account_id: int
    uri: str              # at://did/app.bsky.feed.post/<rkey> (identidad)
    cid: str              # hash de contenido (necesario para me gusta, republicar y citar)
    author_did: str
    author_handle: str
    author_name: str
    content: str
    created_at: str
    indexed_at: str
    langs: Optional[str] = None
    labels: Optional[str] = None          # etiquetas de contenido (propias e hidratadas)
    reply_root_uri: Optional[str] = None
    reply_root_cid: Optional[str] = None
    reply_parent_uri: Optional[str] = None
    reply_parent_cid: Optional[str] = None
    quote_uri: Optional[str] = None
    like_count: int = 0
    repost_count: int = 0
    reply_count: int = 0
    quote_count: int = 0
    liked_uri: Optional[str] = None        # viewer.like (para quitar el me gusta)
    reposted_uri: Optional[str] = None     # viewer.repost (para deshacer la republicación)
    bookmarked: bool = False
    media_attachments: List[Dict[str, Any]] = field(default_factory=list)
    external_card: Optional[Dict[str, Any]] = None
    urls: List[str] = field(default_factory=list)
    repost_by_name: Optional[str] = None     # quién republicó (si llega por republicación)
    repost_by_handle: Optional[str] = None
    verificado: bool = False

@dataclass
class RedditPost:
    """Publicación (submission) de Reddit cacheada."""
    id: str                # "<account_id>:<fullname>" (identidad local)
    account_id: int
    fullname: str          # t3_<base36> (identidad remota para votar/guardar/etc.)
    post_id: str           # id base36 sin prefijo
    subreddit: str         # nombre del subreddit (sin r/)
    author: str            # nombre del autor (sin u/); "[deleted]" si borrado
    title: str
    selftext: str = ""     # cuerpo (markdown) de los self-posts; vacío en enlaces
    url: str = ""          # URL de destino (enlaces) o del propio post (self)
    permalink: str = ""    # ruta relativa en reddit.com
    created_utc: float = 0.0   # epoch UTC de creación
    score: int = 0
    upvote_ratio: float = 0.0
    num_comments: int = 0
    over_18: bool = False       # NSFW
    spoiler: bool = False
    stickied: bool = False
    locked: bool = False
    saved: bool = False
    hidden: bool = False
    clicked: bool = False
    likes: Optional[bool] = None   # True=upvote, False=downvote, None=sin voto
    link_flair_text: Optional[str] = None
    is_self: bool = False
    is_gallery: bool = False
    post_hint: Optional[str] = None    # image, hosted:video, rich:video, link...
    domain: Optional[str] = None
    edited: bool = False
    distinguished: Optional[str] = None   # moderator, admin, special...
    media_attachments: List[Dict[str, Any]] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    crosspost_parent: Optional[str] = None

@dataclass
class RedditComment:
    """Comentario de Reddit cacheado."""
    id: str                # "<account_id>:<fullname>" (t1_<base36>)
    account_id: int
    fullname: str          # t1_<base36>
    comment_id: str        # id base36 sin prefijo
    link_id: str           # fullname de la submission (t3_...)
    parent_id: str         # fullname del padre (t1_ o t3_)
    subreddit: str
    author: str
    body: str = ""         # cuerpo (markdown)
    created_utc: float = 0.0
    score: int = 0
    depth: int = 0
    permalink: str = ""
    saved: bool = False
    likes: Optional[bool] = None
    edited: bool = False
    stickied: bool = False
    is_submitter: bool = False   # el autor es el del post (OP)
    distinguished: Optional[str] = None

@dataclass
class PlayHistoryEntry:
    """Entrada en el historial de reproducción multimedia."""
    id: int
    media_url: str
    title: str
    service_origin: str
    account_origin_id: int
    last_position: float
    duration: float
    last_played_at: str
    finished: bool = False
