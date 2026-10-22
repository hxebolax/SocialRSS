# -*- coding: utf-8 -*-
"""Importar las suscripciones de YouTube desde el ZIP de Google Takeout.

Lee el ZIP SIN descomprimirlo (solo los CSV/HTML/JSON pequeños de la carpeta de YouTube) y
devuelve los canales y listas como feeds de YouTube suscribibles:
  - suscripciones (subscriptions.csv),
  - los canales propios (channel.csv),
  - las listas de reproducción propias (playlists.csv),
  - y, opcionalmente, los canales que aparecen en el historial de reproducciones.

Takeout traduce los nombres de carpetas, ficheros y columnas al idioma de la cuenta
("YouTube y YouTube Music/suscripciones/suscripciones.csv", "ID del canal"...), así que los
ficheros se reconocen por palabras clave en inglés y en español, y los identificadores por su
forma (UC... de 24 caracteres para canales), no por el nombre exacto de la columna.

Idea tomada de BlindRSS (core/youtube_takeout.py; Copyright (c) serrebidev and contributors,
licencia MIT), reescrita y ampliada para las exportaciones en español y el historial en JSON.
"""
import csv
import io
import json
import os
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

MAX_BYTES_MIEMBRO = 64 * 1024 * 1024
MAX_MIEMBROS = 20_000

FUENTES = ("suscripciones", "propios", "listas", "historial")
NOMBRES_FUENTE = {
    "suscripciones": "Suscripciones",
    "propios": "Tus canales",
    "listas": "Tus listas de reproducción",
    "historial": "Canales del historial de reproducciones",
}

_RE_CANAL = re.compile(r"^UC[\w-]{22}$")
_RE_LISTA = re.compile(r"^(PL|OL|UU|FL|RD)[\w-]{10,}$")
_HOSTS_YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


class TakeoutError(ValueError):
    """El archivo no es una exportación de YouTube utilizable."""


def feed_de_canal(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def feed_de_lista(playlist_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"


def canal_de_url(url: str) -> str:
    """UC... de una dirección de canal de YouTube (/channel/UC...), o cadena vacía."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return ""
    if (p.hostname or "").lower() not in _HOSTS_YOUTUBE:
        return ""
    partes = [x for x in p.path.split("/") if x]
    if len(partes) >= 2 and partes[0].lower() == "channel" and _RE_CANAL.match(partes[1]):
        return partes[1]
    return ""


def _lista_de_valor(valor: str) -> str:
    """Identificador de lista a partir del propio id o de una dirección con ?list=."""
    valor = (valor or "").strip()
    if _RE_LISTA.match(valor):
        return valor
    if valor.lower().startswith("http"):
        try:
            lista = (parse_qs(urlparse(valor).query).get("list") or [""])[0]
        except ValueError:
            lista = ""
        if _RE_LISTA.match(lista):
            return lista
    return ""


def _fuente_de_ruta(nombre: str) -> str:
    """Qué contiene un fichero de la exportación, según su ruta (inglés o español)."""
    n = nombre.lower()
    base = n.rsplit("/", 1)[-1]
    if "subscri" in n or "suscrip" in n:
        return "suscripciones"
    if base.startswith(("watch-history", "historial")) and base.endswith((".html", ".json")):
        return "historial"
    if base.endswith(".csv"):
        # Dentro de la carpeta de listas hay un CSV por lista con sus VÍDEOS: solo vale el
        # índice (playlists.csv / listas de reproducción.csv).
        if base in ("playlists.csv", "listas de reproducción.csv", "listas de reproduccion.csv",
                    "listas.csv"):
            return "listas"
        if base in ("channel.csv", "canal.csv", "channels.csv", "canales.csv"):
            return "propios"
    return ""


def _es_de_youtube(nombre: str) -> bool:
    return "youtube" in nombre.lower()


def _leer(zf, info) -> str:
    if info.file_size > MAX_BYTES_MIEMBRO:
        raise TakeoutError(f"Un fichero de la exportación es demasiado grande: {info.filename}")
    return zf.read(info).decode("utf-8-sig", errors="replace")


def _columna_titulo(cabeceras) -> str:
    """La columna del título (se prefiere la original a la traducida)."""
    candidatas = [c for c in cabeceras if c and ("title" in c.lower() or "títul" in c.lower()
                                                  or "titul" in c.lower() or "nombre" in c.lower())]
    for c in candidatas:
        if "original" in c.lower() and "language" not in c.lower() and "idioma" not in c.lower():
            return c
    return candidatas[0] if candidatas else ""


def _filas_csv(texto: str, fuente: str):
    """(título, feed) de cada fila de un CSV de canales o de listas."""
    lector = csv.DictReader(io.StringIO(texto))
    cabeceras = lector.fieldnames or []
    col_titulo = _columna_titulo(cabeceras)
    for fila in lector:
        titulo = (fila.get(col_titulo) or "").strip() if col_titulo else ""
        feed = ""
        for valor in fila.values():
            valor = (valor or "").strip() if isinstance(valor, str) else ""
            if not valor:
                continue
            if fuente == "listas":
                lista = _lista_de_valor(valor)
                if lista:
                    feed = feed_de_lista(lista)
                    break
            else:
                cid = valor if _RE_CANAL.match(valor) else canal_de_url(valor)
                if cid:
                    feed = feed_de_canal(cid)
                    break
        if feed:
            yield titulo, feed


class _CanalesDelHistorial(HTMLParser):
    """Enlaces a canales (/channel/UC...) del historial en HTML, con su nombre."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canales = []
        self._href = ""
        self._texto = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href", "") or ""
            self._texto = []

    def handle_data(self, data):
        if self._href:
            self._texto.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or not self._href:
            return
        cid = canal_de_url(self._href)
        if cid:
            self.canales.append((cid, "".join(self._texto).strip()))
        self._href = ""
        self._texto = []


def _canales_historial(texto: str, es_json: bool):
    if es_json:
        try:
            datos = json.loads(texto)
        except ValueError:
            return []
        salida = []
        for entrada in datos if isinstance(datos, list) else []:
            for sub in (entrada or {}).get("subtitles") or []:
                cid = canal_de_url((sub or {}).get("url", ""))
                if cid:
                    salida.append((cid, (sub.get("name") or "").strip()))
        return salida
    p = _CanalesDelHistorial()
    p.feed(texto)
    return p.canales


def analizar_takeout(ruta) -> list:
    """Lista de {title, url, fuente} (sin repetir la misma dirección) de un ZIP de Takeout.
    Lanza TakeoutError si no es un ZIP, no trae la carpeta de YouTube o no hay nada."""
    ruta = os.fspath(ruta)
    if not zipfile.is_zipfile(ruta):
        raise TakeoutError("El archivo elegido no es un ZIP válido.")
    vistos = {}
    orden = []

    def _anadir(titulo, url, fuente):
        if url in vistos:
            # Si ya estaba (p. ej. del historial) y ahora sale en suscripciones, gana la fuente
            # más fuerte (la que aparece antes en FUENTES) y el título que tenga.
            previo = vistos[url]
            if FUENTES.index(fuente) < FUENTES.index(previo["fuente"]):
                previo["fuente"] = fuente
            if not previo["title"] and titulo:
                previo["title"] = titulo
            return
        vistos[url] = {"title": titulo, "url": url, "fuente": fuente}
        orden.append(url)

    try:
        with zipfile.ZipFile(ruta) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_MIEMBROS:
                raise TakeoutError("El archivo contiene demasiados ficheros.")
            de_youtube = [i for i in infos if not i.is_dir() and _es_de_youtube(i.filename)]
            if not de_youtube:
                raise TakeoutError("En este archivo no hay ninguna exportación de YouTube.")
            for info in de_youtube:
                nombre = info.filename.replace("\\", "/")
                fuente = _fuente_de_ruta(nombre)
                if not fuente:
                    continue
                texto = _leer(zf, info)
                if fuente == "historial":
                    for cid, titulo in _canales_historial(texto, nombre.lower().endswith(".json")):
                        _anadir(titulo, feed_de_canal(cid), "historial")
                elif nombre.lower().endswith(".csv"):
                    for titulo, url in _filas_csv(texto, fuente):
                        _anadir(titulo, url, fuente)
    except (OSError, zipfile.BadZipFile) as e:
        raise TakeoutError(f"No se pudo leer el archivo: {e}") from e
    if not orden:
        raise TakeoutError("No se encontraron canales ni listas de YouTube a los que suscribirse.")
    salida = [vistos[u] for u in orden]
    for f in salida:
        if not f["title"]:
            f["title"] = f["url"].rsplit("=", 1)[-1]
    return salida


class _TakeoutMixin:
    def analizar_takeout_youtube(self, account_id: int, ruta: str, carpeta: str = "YouTube") -> list:
        """Vista previa de la importación (mismo formato que analizar_opml): cada canal o lista
        con su estado ('nuevo' o 'duplicado'), la carpeta destino y si se marca de entrada (los
        del historial NO: suelen ser muchos canales vistos una sola vez)."""
        resultado = []
        for f in analizar_takeout(ruta):
            ya = self.buscar_feed_por_url(account_id, f["url"])
            resultado.append({
                "title": f["title"], "url": f["url"], "fuente": f["fuente"],
                "category_path": [carpeta] if carpeta else [],
                "estado": "duplicado" if ya else "nuevo",
                "marcar": f["fuente"] != "historial",
            })
        return resultado
