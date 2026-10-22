# -*- coding: utf-8 -*-
"""Web Scraper: crear un "feed" a partir de una página web SIN RSS nativo.

Analiza el HTML de una página, detecta la estructura repetida de artículos (contenedores tipo
article/.post/.entry… o, si no, por la estructura de enlaces) y extrae una lista de elementos
{título, enlace, resumen}. Esos elementos se tratan luego como artículos del feed, de modo que
"actualizar" el feed = volver a analizar la página.

Implementación propia basada en BeautifulSoup (ya es dependencia). No depende de feedparser.
"""
import logging
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; SocialRSS Suite Web Reader)"

# Selectores candidatos para el CONTENEDOR de cada artículo, de más específico a más genérico.
_SELECTORES_CONTENEDOR = [
    "article",
    ".post", ".article", ".entry", ".item", ".news-item", ".story", ".card",
    '[class*="post"]', '[class*="article"]', '[class*="entry"]', '[class*="story"]',
    "li",
]
# Dentro de un contenedor, dónde está el título (con su enlace).
_SELECTORES_TITULO = ["h1 a", "h2 a", "h3 a", "h4 a", "a.title", ".title a", ".entry-title a", "a"]
# Dónde está el resumen/extracto.
_SELECTORES_RESUMEN = [".excerpt", ".summary", ".description", ".entry-summary", ".lead", "p"]


def _descargar_html(url: str, user_agent: str = "") -> str:
    from app.core.net import http_session
    if user_agent == "navegador":
        # Feed marcado para identificarse como navegador (cortafuegos que rechazan aplicaciones).
        from app.services.rss.rss_fulltext import CABECERAS_NAVEGADOR
        headers = dict(CABECERAS_NAVEGADOR)
    else:
        headers = {"User-Agent": user_agent or _USER_AGENT}
    resp = http_session().get(url, timeout=20, headers=headers)
    resp.raise_for_status()
    return resp.text


def _texto(elemento) -> str:
    try:
        return " ".join((elemento.get_text(" ", strip=True) or "").split())
    except Exception:
        return ""


def _extraer_item(contenedor, base_url: str) -> dict:
    """Extrae {title, link, summary} de un contenedor de artículo, o {} si no es válido."""
    from bs4 import BeautifulSoup  # noqa: F401  (asegura que bs4 está disponible)
    title = ""
    link = ""
    for sel in _SELECTORES_TITULO:
        a = contenedor.select_one(sel)
        if a is not None and a.get("href"):
            title = _texto(a)
            link = urljoin(base_url, a.get("href"))
            if title and len(title) >= 8 and link.startswith("http"):
                break
            title, link = "", ""
    if not title or not link:
        return {}
    # Resumen: el primer párrafo/extracto que no sea el propio título.
    summary = ""
    for sel in _SELECTORES_RESUMEN:
        p = contenedor.select_one(sel)
        if p is not None:
            t = _texto(p)
            if t and t != title and len(t) > 20:
                summary = t
                break
    return {"title": title, "link": link, "summary": summary}


def analizar(url: str, user_agent: str = "") -> dict:
    """Analiza una web y devuelve {titulo_pagina, items: [{title, link, summary}], selector}.

    Prueba varios selectores de contenedor y se queda con el que produce MÁS artículos válidos
    (mínimo 3). Si ninguno funciona, recurre a la estructura de enlaces de la página."""
    from bs4 import BeautifulSoup
    html = _descargar_html(url, user_agent)
    soup = BeautifulSoup(html, "html.parser")
    titulo_pagina = ""
    if soup.title and soup.title.string:
        titulo_pagina = " ".join(soup.title.string.split())

    mejor = {"items": [], "selector": ""}
    for selector in _SELECTORES_CONTENEDOR:
        try:
            contenedores = soup.select(selector)
        except Exception:
            continue
        if not contenedores or len(contenedores) < 3:
            continue
        items = []
        vistos = set()
        for c in contenedores:
            it = _extraer_item(c, url)
            if it and it["link"] not in vistos:
                vistos.add(it["link"])
                items.append(it)
        # Nos quedamos con el selector que más artículos válidos detecte.
        if len(items) > len(mejor["items"]):
            mejor = {"items": items, "selector": selector}

    if len(mejor["items"]) >= 3:
        return {"titulo_pagina": titulo_pagina, "items": mejor["items"][:60], "selector": mejor["selector"]}

    # Respaldo: por estructura de enlaces (enlaces con texto largo dentro del mismo dominio).
    items = _detectar_por_enlaces(soup, url)
    return {"titulo_pagina": titulo_pagina, "items": items[:60], "selector": ""}


def _detectar_por_enlaces(soup, base_url: str) -> list:
    """Respaldo: artículos = enlaces con texto suficientemente largo, agrupados por su contenedor
    padre más común, del mismo dominio que la página."""
    dominio = urlparse(base_url).netloc
    candidatos = []
    vistos = set()
    for a in soup.find_all("a", href=True):
        texto = _texto(a)
        href = urljoin(base_url, a.get("href"))
        if (len(texto) >= 25 and href.startswith("http")
                and href not in vistos and urlparse(href).netloc.endswith(dominio.split(":")[0])):
            vistos.add(href)
            candidatos.append({"title": texto, "link": href, "summary": ""})
    return candidatos


# ----------------------------------------------------------------------------------------------
# SCRAPERS PERSONALIZADOS POR SITIO (módulos de usuario)
# ----------------------------------------------------------------------------------------------
# Quien quiera leer una web concreta que el detector automático no maneja bien (foros, sitios con
# login, paginación, estructura rara…) puede escribir un MÓDULO propio: una subclase de SiteScraper
# en data/plugins/web_scraper/scrapers/. El complemento "web_scraper" descubre esos módulos y los
# REGISTRA aquí; al actualizar un feed asociado a un módulo, se llama a su método scrape().

class SiteScraper:
    """Base de un scraper de sitio personalizado. Subclasea esto en un módulo dentro de
    data/plugins/web_scraper/scrapers/ y define ID, NAME, (opcional) URL_PATTERN y scrape()."""
    ID = ""             # identificador único en snake_case (p. ej. "miforo")
    NAME = ""           # nombre legible para la interfaz
    URL_PATTERN = ""    # regex opcional para auto-detectar las URLs que maneja

    def can_handle(self, url: str) -> bool:
        """True si este scraper sabe leer esa URL (por defecto, según URL_PATTERN)."""
        import re
        return bool(self.URL_PATTERN and re.search(self.URL_PATTERN, url, re.I))

    def scrape(self, url: str) -> list:
        """Devuelve una lista de artículos. Cada uno: dict con al menos 'title' y 'link', y
        opcionalmente 'summary' (o 'content'). Implementar en la subclase."""
        raise NotImplementedError


# Registro de scrapers de sitio activos (lo puebla el complemento web_scraper al activarse).
_SCRAPERS = {}


def registrar_scraper(instancia: "SiteScraper"):
    if getattr(instancia, "ID", ""):
        _SCRAPERS[instancia.ID] = instancia


def quitar_scrapers():
    _SCRAPERS.clear()


def obtener_scraper(scraper_id: str):
    return _SCRAPERS.get(scraper_id)


def listar_scrapers() -> list:
    return list(_SCRAPERS.values())


def scraper_para_url(url: str):
    """Devuelve el scraper de sitio que sabe manejar esa URL, o None."""
    for s in _SCRAPERS.values():
        try:
            if s.can_handle(url):
                return s
        except Exception:
            pass
    return None


def scrapear_con_modulo(scraper_id: str, url: str) -> list:
    """Ejecuta el scraper de sitio indicado y normaliza su resultado a [{title, link, summary}]."""
    s = obtener_scraper(scraper_id)
    if s is None:
        raise ValueError(f"El scraper de sitio '{scraper_id}' no está disponible (¿complemento activo?).")
    items = s.scrape(url) or []
    salida = []
    for it in items:
        if not isinstance(it, dict):
            continue
        link = it.get("link", "") or ""
        if not link:
            continue
        salida.append({"title": it.get("title", "") or link,
                       "link": link,
                       "summary": it.get("summary") or it.get("content") or ""})
    return salida


def scrapear(url: str, selector: str = "", user_agent: str = "") -> list:
    """Devuelve la lista actual de artículos {title, link, summary} de la web. Usa el selector
    guardado del feed; si no hay o ya no funciona, vuelve a auto-detectar."""
    from bs4 import BeautifulSoup
    html = _descargar_html(url, user_agent)
    soup = BeautifulSoup(html, "html.parser")
    if selector:
        try:
            contenedores = soup.select(selector)
            items, vistos = [], set()
            for c in contenedores:
                it = _extraer_item(c, url)
                if it and it["link"] not in vistos:
                    vistos.add(it["link"])
                    items.append(it)
            if len(items) >= 1:
                return items[:80]
        except Exception as e:
            log.debug(f"Selector '{selector}' falló en {url}: {e}")
    # Sin selector o sin resultados: auto-detectar de nuevo.
    return analizar(url, user_agent).get("items", [])[:80]
