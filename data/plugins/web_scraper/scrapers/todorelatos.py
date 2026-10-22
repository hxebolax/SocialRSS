# -*- coding: utf-8 -*-
"""Módulo de scraper de sitio para TodoRelatos (todorelatos.com).

Lee los listados de relatos (categorías, búsquedas o un relato) y devuelve cada relato como un
artículo {title, link, summary}. Recorre todas las páginas del listado (paginación). Implementación
propia para esta web pública, usando su estructura (relatos en celdas 'relato2' con enlaces a
'/relato/', autor en 'relato3x' y descripción en 'relato4'/'desc1').
"""
import re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from app.services.rss.rss_scraper import SiteScraper

_BASE = "https://www.todorelatos.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SocialRSS Web Reader"
_MAX_PAGINAS = 100   # tope de seguridad para no disparar cientos de descargas


class TodoRelatosScraper(SiteScraper):
    ID = "todorelatos"
    NAME = "TodoRelatos"
    URL_PATTERN = r"todorelatos\.com/(categorias|buscar|relato)/"

    def _soup(self, url):
        import requests
        from bs4 import BeautifulSoup
        html = requests.get(url, timeout=25, headers={"User-Agent": _UA}).text
        return BeautifulSoup(html, "html.parser")

    def _urls_paginas(self, url, soup):
        """A partir del selector de página (select name='pagina'), devuelve las URLs del resto de
        páginas del listado (sin la primera, que ya se procesa aparte)."""
        urls = []
        try:
            sel = soup.find("select", {"name": "pagina"})
            if not sel:
                return urls
            numeros = []
            for op in sel.find_all("option"):
                v = (op.get("value") or op.get_text() or "").strip()
                if v.isdigit():
                    numeros.append(int(v))
            if not numeros:
                return urls
            partes = urlparse(url)
            consulta = parse_qs(partes.query)
            actual = consulta.get("pagina", ["1"])[0]
            for n in sorted(set(numeros)):
                if str(n) == str(actual):
                    continue
                consulta["pagina"] = [str(n)]
                urls.append(urlunparse(partes._replace(query=urlencode(consulta, doseq=True))))
        except Exception:
            pass
        return urls[: _MAX_PAGINAS]

    def _recoger(self, soup, items, vistos):
        """Extrae los relatos de una página y los añade a `items` (sin repetir enlaces)."""
        for celda in soup.find_all("td", class_="relato2"):
            a = celda.find("a", href=re.compile(r"/relato/"))
            if not a or not a.get("href"):
                continue
            enlace = urljoin(_BASE, a.get("href"))
            titulo = a.get_text(" ", strip=True)
            if not titulo or titulo.lower() in ("leer más", "ver más", "leer mas", "ver mas"):
                continue
            if enlace in vistos:
                continue
            # Autor (misma fila, celda relato3x) y descripción (fila siguiente, relato4/desc1).
            resumen = ""
            fila = celda.find_parent("tr")
            if fila is not None:
                td_autor = fila.find("td", class_="relato3x")
                autor = td_autor.get_text(" ", strip=True) if td_autor else ""
                sig = fila.find_next_sibling("tr")
                td_desc = sig.find("td", class_=["relato4", "desc1"]) if sig is not None else None
                desc = td_desc.get_text(" ", strip=True) if td_desc else ""
                resumen = " — ".join(x for x in (autor, desc) if x)
            vistos.add(enlace)
            items.append({"title": titulo, "link": enlace, "summary": resumen})

    def scrape(self, url: str) -> list:
        items, vistos = [], set()
        soup = self._soup(url)
        self._recoger(soup, items, vistos)
        for u in self._urls_paginas(url, soup):
            try:
                self._recoger(self._soup(u), items, vistos)
            except Exception:
                pass
        return items
