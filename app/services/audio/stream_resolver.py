# -*- coding: utf-8 -*-
"""
Resolución de audio desde servicios en línea (YouTube, SoundCloud, etc.).

Usa yt-dlp para extraer la URL de audio directa a partir del enlace de un
servicio, de modo que el reproductor integrado (BASS) pueda reproducirlo en
streaming sin descargar el archivo completo.
"""

import logging

log = logging.getLogger(__name__)

# Dominios cuyo audio sabemos extraer con yt-dlp.
_DOMINIOS = (
    "youtube.com", "youtu.be", "soundcloud.com", "audioboom.com",
    "bandcamp.com", "vimeo.com", "twitch.tv", "mixcloud.com", "ivoox.com",
)


def disponible() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def es_url_de_servicio(url: str) -> bool:
    """Indica si la URL pertenece a un servicio de audio/vídeo soportado."""
    if not url:
        return False
    u = url.lower()
    return any(d in u for d in _DOMINIOS)


def primera_url_de_servicio(urls) -> str:
    """Devuelve la primera URL de la lista que sea de un servicio soportado, o ''."""
    for u in (urls or []):
        if es_url_de_servicio(u):
            return u
    return ""


def _es_youtube(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def _formato_para(url: str) -> str:
    """Selector de formato de yt-dlp adaptado al servicio.

    YouTube entrega su mejor audio (Opus/WebM o m4a DASH) con el índice del
    contenedor al final del archivo, por lo que BASS no puede reproducirlo en
    streaming directo (devuelve "formato no compatible"). En su lugar se prefiere
    un formato progresivo muxado (el clásico 18 / mp4 360p), cuyo índice va al
    principio y SÍ admite reproducción en directo. BASS ignora la pista de vídeo
    y reproduce solo el audio. Para el resto de servicios (SoundCloud, iVoox,
    Bandcamp...) el mejor audio ya es reproducible, así que se usa 'bestaudio'.
    """
    if _es_youtube(url):
        return ("18/best[ext=mp4][acodec!=none][vcodec!=none]"
                "/bestaudio[ext=m4a]/bestaudio/best")
    return "bestaudio/best"


def resolver_audio_url(url: str):
    """
    Devuelve (audio_url, titulo) listo para reproducir en streaming, o lanza una
    excepción. Para YouTube prefiere un formato progresivo reproducible en directo.
    """
    import yt_dlp
    opciones = {
        "quiet": True,
        "no_warnings": True,
        "format": _formato_para(url),
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opciones) as ydl:
        info = ydl.extract_info(url, download=False)
        if "entries" in info and info["entries"]:
            info = info["entries"][0]
        titulo = info.get("title") or "Audio"
        # 'url' suele venir resuelta para el formato elegido.
        audio_url = info.get("url")
        if not audio_url:
            # Buscar entre los formatos uno de solo audio.
            for f in info.get("formats", []):
                if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none"):
                    audio_url = f.get("url")
                    break
        if not audio_url:
            raise RuntimeError("No se encontró una pista de audio reproducible.")
        return audio_url, titulo


def descargar_audio_temp(url: str, ffmpeg_path: str = None):
    """Descarga el audio a un archivo temporal y devuelve (ruta, titulo).

    Es el plan B cuando el streaming directo falla (típico en YouTube si el
    único formato disponible no es reproducible en directo). El archivo se guarda
    en data/temp, que se vacía al cerrar la aplicación.
    """
    import os
    import yt_dlp
    from app.core.paths import get_data_path
    destino = get_data_path("temp")
    destino.mkdir(parents=True, exist_ok=True)
    opciones = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # Se prefiere el progresivo (18) y, si no, el mejor audio. Para YouTube se piden
        # varios "player_client": las URLs DASH de googlevideo del cliente web suelen dar
        # HTTP 403 al descargarlas; los clientes android/ios/tv entregan URLs descargables.
        "format": "18/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(destino / "audio_%(id)s.%(ext)s"),
        "overwrites": False,
    }
    if _es_youtube(url):
        opciones["extractor_args"] = {
            "youtube": {"player_client": ["tv", "android", "ios", "web_safari", "web"]}
        }
    if ffmpeg_path:
        opciones["ffmpeg_location"] = ffmpeg_path
    with yt_dlp.YoutubeDL(opciones) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info and info["entries"]:
            info = info["entries"][0]
        ruta = ydl.prepare_filename(info)
        titulo = info.get("title") or "Audio"
    if not ruta or not os.path.exists(ruta):
        raise RuntimeError("No se pudo descargar el audio del vídeo.")
    return ruta, titulo
