# -*- coding: utf-8 -*-
"""PLANTILLA de un módulo de scraper de sitio.

Copia este archivo con OTRO nombre (sin el guion bajo inicial), por ejemplo "miforo.py", dentro de
esta misma carpeta (data/plugins/web_scraper/scrapers/) y adáptalo a la web que quieras leer.
Reinicia la app (o desactiva y reactiva el complemento "Web Scraper") para que se cargue.

Cada módulo define UNA subclase de SiteScraper con:
  - ID: identificador único en snake_case.
  - NAME: nombre legible para la interfaz.
  - URL_PATTERN: (opcional) una expresión regular para que "Automático" detecte sus URLs.
  - scrape(url): devuelve una lista de artículos. Cada artículo es un dict con al menos
    'title' y 'link', y opcionalmente 'summary' (o 'content').

Los archivos que empiezan por "_" (como este) se IGNORAN: son plantillas, no scrapers activos.
"""
from app.services.rss.rss_scraper import SiteScraper


class MiSitioScraper(SiteScraper):
    ID = "mi_sitio"                       # cámbialo por algo único
    NAME = "Mi sitio de ejemplo"          # nombre que verás en la lista
    URL_PATTERN = r"ejemplo\.com"         # regex de las URLs que maneja (o déjalo vacío)

    def scrape(self, url: str) -> list:
        """Descarga la página y devuelve sus artículos. Aquí va TU lógica a medida: puedes usar
        requests + BeautifulSoup, manejar login, recorrer varias páginas, etc."""
        import requests
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        html = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (SocialRSS Web Reader)"}).text
        soup = BeautifulSoup(html, "html.parser")

        articulos = []
        # EJEMPLO: cada artículo está en un <article> con un <h2><a>...</a></h2> y un <p>.
        # Cámbialo por los selectores reales de la web que quieras leer.
        for art in soup.select("article"):
            a = art.select_one("h2 a, h3 a, a")
            if not a or not a.get("href"):
                continue
            titulo = a.get_text(" ", strip=True)
            enlace = urljoin(url, a.get("href"))
            p = art.select_one("p")
            resumen = p.get_text(" ", strip=True) if p else ""
            if titulo and enlace.startswith("http"):
                articulos.append({"title": titulo, "link": enlace, "summary": resumen})
        return articulos
