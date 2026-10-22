# -*- coding: utf-8 -*-
"""Etiquetas ID3 de los episodios de podcast: notas del episodio y capítulos DENTRO del MP3.

Muchos podcasts guardan en el propio MP3 las notas del programa (COMM, USLT, TIT3, TXXX) y los
capítulos (CHAP/CTOC). Se leen SIN descargar el audio: solo el principio del fichero, con una
petición de rango (Range), porque la etiqueta ID3v2 va al principio. Su cabecera dice cuánto
ocupa, así que se pide lo justo (con un tope de 4 MB por si la etiqueta lleva imágenes).

Sirve para el lector cuando el feed no trae texto del episodio, y para los capítulos del
reproductor de podcasts. Idea tomada de BlindRSS (utils.embedded_id3_metadata; Copyright (c)
serrebidev and contributors, licencia MIT), reescrita con mutagen.
"""
import html
import io
import logging
import re
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

PRIMER_TROZO = 256 * 1024
MAX_ETIQUETA = 4 * 1024 * 1024
_DESCRIPCIONES_NOTAS = ("description", "comment", "summary", "notes", "descripción", "notas")


def _tam_etiqueta(cabecera: bytes) -> int:
    """Tamaño total de la etiqueta ID3v2 (cabecera incluida) o 0 si no hay etiqueta."""
    if len(cabecera) < 10 or cabecera[:3] != b"ID3":
        return 0
    b = cabecera[6:10]
    tam = (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]
    pie = 10 if cabecera[5] & 0x10 else 0
    return 10 + tam + pie


def _descargar_principio(url: str) -> bytes:
    from app.core.net import http_session
    from .rss_fulltext import CABECERAS_NAVEGADOR

    def _rango(hasta: int) -> bytes:
        cab = dict(CABECERAS_NAVEGADOR, Range=f"bytes=0-{hasta - 1}")
        r = http_session().get(url, headers=cab, timeout=(8, 25), stream=True)
        try:
            r.raise_for_status()
            trozos, total = [], 0
            for t in r.iter_content(65536):
                trozos.append(t)
                total += len(t)
                if total >= hasta:      # el servidor puede ignorar el rango: se corta aquí
                    break
            return b"".join(trozos)[:hasta]
        finally:
            r.close()

    datos = _rango(PRIMER_TROZO)
    necesario = min(_tam_etiqueta(datos), MAX_ETIQUETA)
    if necesario > len(datos):
        datos = _rango(necesario)
    return datos


def analizar_id3(datos: bytes) -> Dict:
    """{'notas': str, 'capitulos': [(segundos, título)], 'titulo': str, 'artista': str} de los
    bytes del principio de un MP3. Diccionario vacío si no hay etiqueta ID3v2."""
    if _tam_etiqueta(datos[:10]) == 0:
        return {}
    try:
        from mutagen.id3 import ID3
        etiqueta = ID3(io.BytesIO(datos))
    except Exception as e:
        log.debug(f"Etiqueta ID3 ilegible: {e}")
        return {}
    notas: List[str] = []

    def _anadir(texto):
        texto = (texto or "").strip()
        if texto and len(texto) > 3 and texto not in notas:
            notas.append(texto)

    for frame in etiqueta.getall("TIT3"):
        _anadir(" ".join(str(t) for t in frame.text))
    for frame in etiqueta.getall("COMM"):
        if (frame.desc or "").lower() in ("", *_DESCRIPCIONES_NOTAS) or len(" ".join(frame.text)) > 40:
            _anadir(" ".join(str(t) for t in frame.text))
    for frame in etiqueta.getall("TXXX"):
        if (frame.desc or "").lower() in _DESCRIPCIONES_NOTAS:
            _anadir(" ".join(str(t) for t in frame.text))
    for frame in etiqueta.getall("USLT"):
        _anadir(frame.text)
    capitulos = []
    for frame in etiqueta.getall("CHAP"):
        titulo = ""
        for sub in (getattr(frame, "sub_frames", None) or {}).values():
            if getattr(sub, "FrameID", "") == "TIT2":
                titulo = " ".join(str(t) for t in sub.text)
        capitulos.append((max(0, int(frame.start_time or 0)) / 1000.0, titulo or frame.element_id))
    capitulos.sort(key=lambda c: c[0])
    return {
        "notas": "\n\n".join(notas),
        "capitulos": capitulos,
        "titulo": " ".join(str(t) for f in etiqueta.getall("TIT2") for t in f.text),
        "artista": " ".join(str(t) for f in etiqueta.getall("TPE1") for t in f.text),
    }


def leer_id3_remoto(url: str) -> Dict:
    """Etiquetas ID3 de un MP3 remoto (solo el principio del fichero). {} si no hay o falla."""
    if not (url or "").lower().startswith(("http://", "https://")):
        return {}
    try:
        return analizar_id3(_descargar_principio(url))
    except Exception as e:
        log.debug(f"No se pudieron leer las etiquetas ID3 de {url}: {e}")
        return {}


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def html_de_id3(datos: Dict) -> str:
    """Las notas y los capítulos como HTML para el lector ("" si no hay nada)."""
    partes = []
    if datos.get("notas"):
        partes.append("<h2>" + _("Notas del episodio (del archivo de audio)") + "</h2>")
        for p in re.split(r"\n\s*\n", datos["notas"]):
            if p.strip():
                partes.append("<p>" + html.escape(p.strip()).replace("\n", "<br>") + "</p>")
    if datos.get("capitulos"):
        partes.append("<h2>" + _("Capítulos") + "</h2><ol>")
        partes += [f"<li>{_hms(s)} {html.escape(t)}</li>" for s, t in datos["capitulos"]]
        partes.append("</ol>")
    return "\n".join(partes)


def notas_para_articulo(enclosure_url: str, enclosure_type: str = "") -> Optional[str]:
    """HTML con las notas y capítulos del MP3 del episodio, o None si no hay."""
    tipo = (enclosure_type or "").lower()
    if tipo and "mpeg" not in tipo and "mp3" not in tipo and not tipo.startswith("audio/"):
        return None
    if not tipo and not re.search(r"\.mp3(\?|$)", enclosure_url or "", re.I):
        return None
    contenido = html_de_id3(leer_id3_remoto(enclosure_url))
    return contenido or None
