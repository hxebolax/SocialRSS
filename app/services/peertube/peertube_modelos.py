# -*- coding: utf-8 -*-
"""Modelos Pydantic (v2) de las entidades de la API de PeerTube.

Todos heredan de `_Base`, que ACEPTA la notación camelCase de la API (por alias) y sus nombres
snake_case en Python, e **ignora los campos desconocidos** (`extra="ignore"`). Así el cliente no se
rompe cuando una instancia devuelve campos de más (versión nueva) o de menos (versión antigua): los
campos ausentes quedan a None. La validación estricta se evita a propósito por la variedad de
versiones de las instancias.
"""
from typing import Any, Generic, List, Optional, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class _Base(BaseModel):
    """Base común: permite alias camelCase + nombre snake_case e ignora campos desconocidos."""
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class OAuthClient(_Base):
    """Respuesta de `GET /oauth-clients/local`: cliente local para poder iniciar sesión."""
    client_id: str
    client_secret: str


class OAuthToken(_Base):
    """Respuesta de `POST /users/token`: tokens de acceso y refresco."""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_in: Optional[int] = None
    refresh_token_expires_in: Optional[int] = None


# ---------------------------------------------------------------------------
# Actores (cuentas y canales) y objetos auxiliares
# ---------------------------------------------------------------------------
class Avatar(_Base):
    path: Optional[str] = None
    width: Optional[int] = None


class Actor(_Base):
    """Campos comunes de cuentas y canales."""
    id: Optional[int] = None
    name: Optional[str] = None
    display_name: Optional[str] = Field(default=None, alias="displayName")
    host: Optional[str] = None
    url: Optional[str] = None
    followers_count: Optional[int] = Field(default=None, alias="followersCount")
    following_count: Optional[int] = Field(default=None, alias="followingCount")
    avatars: List[Avatar] = Field(default_factory=list)

    @property
    def handle(self) -> str:
        """Identificador legible: `nombre@host` (o solo `nombre` si es local)."""
        if self.name and self.host:
            return f"{self.name}@{self.host}"
        return self.name or ""


class Account(Actor):
    """Cuenta de usuario (propietaria de vídeos/canales)."""
    description: Optional[str] = None


class VideoChannel(Actor):
    """Canal de vídeo."""
    description: Optional[str] = None
    support: Optional[str] = None
    owner_account: Optional[Account] = Field(default=None, alias="ownerAccount")


class LabelId(_Base):
    """Par `{id, label}` de PeerTube (privacidad, categoría, licencia, idioma...). El id puede ser
    numérico (privacidad/categoría) o texto (idioma: "es")."""
    id: Optional[Union[int, str]] = None
    label: Optional[str] = None


# ---------------------------------------------------------------------------
# Ficheros de reproducción
# ---------------------------------------------------------------------------
class Resolution(_Base):
    id: Optional[int] = None
    label: Optional[str] = None


class VideoFile(_Base):
    """Un fichero de vídeo (una resolución concreta), progresivo o de una playlist HLS."""
    id: Optional[int] = None
    resolution: Optional[Resolution] = None
    size: Optional[int] = None
    fps: Optional[int] = None
    torrent_url: Optional[str] = Field(default=None, alias="torrentUrl")
    torrent_download_url: Optional[str] = Field(default=None, alias="torrentDownloadUrl")
    file_url: Optional[str] = Field(default=None, alias="fileUrl")
    file_download_url: Optional[str] = Field(default=None, alias="fileDownloadUrl")
    metadata_url: Optional[str] = Field(default=None, alias="metadataUrl")
    width: Optional[int] = None
    height: Optional[int] = None


class StreamingPlaylist(_Base):
    """Playlist HLS (máster) con sus ficheros por resolución."""
    id: Optional[int] = None
    type: Optional[int] = None
    playlist_url: Optional[str] = Field(default=None, alias="playlistUrl")
    segments_sha256_url: Optional[str] = Field(default=None, alias="segmentsSha256Url")
    files: List[VideoFile] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Vídeos
# ---------------------------------------------------------------------------
class Video(_Base):
    """Un vídeo. En los listados vienen los campos básicos; en el detalle, además `files`,
    `streamingPlaylists`, `description` completa, etc."""
    id: Optional[int] = None
    uuid: Optional[str] = None
    short_uuid: Optional[str] = Field(default=None, alias="shortUUID")
    name: Optional[str] = None
    description: Optional[str] = None
    duration: Optional[int] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    dislikes: Optional[int] = None
    nsfw: Optional[bool] = None
    is_live: Optional[bool] = Field(default=None, alias="isLive")
    is_local: Optional[bool] = Field(default=None, alias="isLocal")
    state: Optional[LabelId] = None
    privacy: Optional[LabelId] = None
    category: Optional[LabelId] = None
    licence: Optional[LabelId] = None
    language: Optional[LabelId] = None
    published_at: Optional[str] = Field(default=None, alias="publishedAt")
    originally_published_at: Optional[str] = Field(default=None, alias="originallyPublishedAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")
    thumbnail_path: Optional[str] = Field(default=None, alias="thumbnailPath")
    preview_path: Optional[str] = Field(default=None, alias="previewPath")
    embed_path: Optional[str] = Field(default=None, alias="embedPath")
    url: Optional[str] = None
    channel: Optional[VideoChannel] = None
    account: Optional[Account] = None
    files: List[VideoFile] = Field(default_factory=list)
    streaming_playlists: List[StreamingPlaylist] = Field(default_factory=list,
                                                         alias="streamingPlaylists")


# ---------------------------------------------------------------------------
# Comentarios, listas y notificaciones
# ---------------------------------------------------------------------------
class Comment(_Base):
    id: Optional[int] = None
    text: Optional[str] = None
    thread_id: Optional[int] = Field(default=None, alias="threadId")
    in_reply_to_comment_id: Optional[int] = Field(default=None, alias="inReplyToCommentId")
    video_id: Optional[int] = Field(default=None, alias="videoId")
    created_at: Optional[str] = Field(default=None, alias="createdAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")
    total_replies: Optional[int] = Field(default=None, alias="totalReplies")
    is_deleted: Optional[bool] = Field(default=None, alias="isDeleted")
    account: Optional[Account] = None


class Playlist(_Base):
    id: Optional[int] = None
    uuid: Optional[str] = None
    short_uuid: Optional[str] = Field(default=None, alias="shortUUID")
    display_name: Optional[str] = Field(default=None, alias="displayName")
    description: Optional[str] = None
    privacy: Optional[LabelId] = None
    videos_length: Optional[int] = Field(default=None, alias="videosLength")
    thumbnail_path: Optional[str] = Field(default=None, alias="thumbnailPath")
    is_local: Optional[bool] = Field(default=None, alias="isLocal")
    owner_account: Optional[Account] = Field(default=None, alias="ownerAccount")
    video_channel: Optional[VideoChannel] = Field(default=None, alias="videoChannel")


class Notification(_Base):
    id: Optional[int] = None
    type: Optional[int] = None
    read: Optional[bool] = None
    created_at: Optional[str] = Field(default=None, alias="createdAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")


# ---------------------------------------------------------------------------
# Configuración de la instancia (capacidades) — subconjunto útil
# ---------------------------------------------------------------------------
class ConfigInstance(_Base):
    name: Optional[str] = None
    short_description: Optional[str] = Field(default=None, alias="shortDescription")
    is_nsfw: Optional[bool] = Field(default=None, alias="isNSFW")
    default_nsfw_policy: Optional[str] = Field(default=None, alias="defaultNSFWPolicy")


class ConfigSignup(_Base):
    allowed: Optional[bool] = None
    requires_approval: Optional[bool] = Field(default=None, alias="requiresApproval")
    requires_email_verification: Optional[bool] = Field(default=None,
                                                       alias="requiresEmailVerification")


class ConfigLive(_Base):
    enabled: Optional[bool] = None
    allow_replay: Optional[bool] = Field(default=None, alias="allowReplay")
    max_duration: Optional[int] = Field(default=None, alias="maxDuration")


class ConfigImportSource(_Base):
    enabled: Optional[bool] = None


class ConfigImportVideos(_Base):
    http: Optional[ConfigImportSource] = None
    torrent: Optional[ConfigImportSource] = None


class ConfigImport(_Base):
    videos: Optional[ConfigImportVideos] = None


class ConfigUser(_Base):
    video_quota: Optional[int] = Field(default=None, alias="videoQuota")
    video_quota_daily: Optional[int] = Field(default=None, alias="videoQuotaDaily")


class ServerConfig(_Base):
    """Subconjunto de `GET /config` con las capacidades que el cliente necesita para decidir qué
    mostrar. Los campos no modelados se ignoran; el JSON íntegro se cachea aparte."""
    server_version: Optional[str] = Field(default=None, alias="serverVersion")
    instance: Optional[ConfigInstance] = None
    signup: Optional[ConfigSignup] = None
    live: Optional[ConfigLive] = None
    import_: Optional[ConfigImport] = Field(default=None, alias="import")
    user: Optional[ConfigUser] = None


# ---------------------------------------------------------------------------
# Paginación
# ---------------------------------------------------------------------------
class ResultadoPaginado(_Base, Generic[T]):
    """Envoltorio de un listado paginado de PeerTube: `{total, data:[...]}`."""
    total: int = 0
    data: List[T] = Field(default_factory=list)


def parsear(modelo: type, datos: Any) -> Any:
    """Valida `datos` contra `modelo` de forma tolerante. Devuelve la instancia validada o, si algo
    va muy mal, lanza `PeerTubeServiceError` con un mensaje legible (no una traza de pydantic)."""
    from app.core.errors import PeerTubeServiceError
    try:
        return modelo.model_validate(datos)
    except Exception as e:
        raise PeerTubeServiceError(
            f"La instancia devolvió datos con un formato inesperado ({modelo.__name__}).") from e


def parsear_pagina(modelo: type, datos: Any) -> "ResultadoPaginado":
    """Valida una respuesta `{total, data}` cuyos elementos son de tipo `modelo`."""
    total = 0
    filas = datos
    if isinstance(datos, dict):
        total = int(datos.get("total") or 0)
        filas = datos.get("data") or []
    elementos = [parsear(modelo, x) for x in (filas or [])]
    res = ResultadoPaginado()
    res.total = total or len(elementos)
    res.data = elementos
    return res
