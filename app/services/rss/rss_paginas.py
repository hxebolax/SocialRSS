# -*- coding: utf-8 -*-
"""Artículos partidos en varias páginas: seguir el enlace "página siguiente" y unirlas.

Solo se sigue un enlace que sea CLARAMENTE la página siguiente de la MISMA noticia: rel="next" o
un texto como "Siguiente", "Página siguiente", "Next" o el número de la página que toca, y cuya
dirección sea la de la noticia con otro número de página (/2/, /page/2, -2, ?page=2...). Así no
se salta a la noticia siguiente del periódico. Como mucho 6 páginas. Al unirlas se quitan los
párrafos repetidos (cabeceras, entradillas y pies que se repiten en cada página).

Idea tomada de BlindRSS (core/article_extractor.py, _find_next_page y _merge_texts; Copyright (c)
serrebidev and contributors, licencia MIT), reescrita.
"""
import logging
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

log = logging.getLogger(__name__)

MAX_PAGINAS = 6
_TEXTOS_SIGUIENTE = {"siguiente", "página siguiente", "pagina siguiente", "siguiente página",
                     "siguiente pagina", "next", "next page", "continuar", "continúa", "continua",
                     "›", "»", "siguiente »", "siguiente ›", "next »", "next ›"}
_PARAMS_PAGINA = {"page", "p", "pagina", "pag", "pg", "paged", "start"}


def _raiz(url: str) -> str:
    """La dirección SIN el número de página, para comparar si dos páginas son la misma noticia."""
    p = urlparse(url)
    ruta = p.path.lower().rstrip("/")
    ruta = re.sub(r"/(page|pagina|pag|p)/\d+$", "", ruta)
    ruta = re.sub(r"(/|-|_)\d{1,2}$", "", ruta)
    ruta = re.sub(r"\.(html?|php|aspx?)$", "", ruta)
    ruta = re.sub(r"(-|_)\d{1,2}$", "", ruta)
    consulta = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if k.lower() not in _PARAMS_PAGINA))
    return f"{p.netloc.lower().removeprefix('www.')}{ruta}?{consulta}"


def _numero_pagina(url: str) -> int:
    p = urlparse(url)
    for k, v in parse_qsl(p.query):
        if k.lower() in _PARAMS_PAGINA and v.isdigit():
            return int(v)
    m = re.search(r"(?:/(?:page|pagina|pag|p)/|[/_-])(\d{1,2})/?(?:\.(?:html?|php|aspx?))?$", p.path.lower())
    return int(m.group(1)) if m else 1


def siguiente_pagina(html_texto: str, url_actual: str, visitadas=(), pagina: int = 1) -> Optional[str]:
    """Dirección de la página siguiente de la misma noticia, o None. `pagina` es la página en
    la que se está (la primera, 1): la siguiente tiene que llevar el número que toca."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_texto or "", "lxml")
    except Exception:
        return None
    raiz = _raiz(url_actual)
    toca = pagina + 1
    candidatas = []
    for etiqueta in soup.find_all(["link", "a"], href=True):
        rel = " ".join(etiqueta.get("rel") or []).lower() if isinstance(etiqueta.get("rel"), list) \
            else str(etiqueta.get("rel") or "").lower()
        texto = re.sub(r"\s+", " ", etiqueta.get_text(" ", strip=True) if etiqueta.name == "a" else "").lower()
        clases = " ".join(etiqueta.get("class") or []).lower()
        if "next" in rel or texto in _TEXTOS_SIGUIENTE or texto == str(toca) \
                or ("next" in clases and etiqueta.name == "a"):
            candidatas.append(urljoin(url_actual, etiqueta["href"]).split("#")[0])
    vistas = {u.rstrip("/") for u in visitadas} | {url_actual.rstrip("/")}
    for u in candidatas:
        if u.startswith(("http://", "https://")) and u.rstrip("/") not in vistas \
                and _raiz(u) == raiz and _numero_pagina(u) == toca:
            return u
    return None


def unir_paginas(html_primera: str, htmls_siguientes) -> str:
    """Une los cuerpos quitando de las páginas siguientes los bloques cuyo texto ya salió."""
    from bs4 import BeautifulSoup
    vistos = set()

    def _clave(el):
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip().lower()

    for el in BeautifulSoup(html_primera or "", "lxml").find_all(["p", "li", "h2", "h3", "h4", "blockquote"]):
        if _clave(el):
            vistos.add(_clave(el))
    partes = [html_primera]
    for h in htmls_siguientes:
        soup = BeautifulSoup(h or "", "lxml")
        for el in soup.find_all(["p", "li", "h2", "h3", "h4", "blockquote"]):
            k = _clave(el)
            if k and k in vistos:
                el.decompose()
            elif k:
                vistos.add(k)
        cuerpo = soup.body or soup
        resto = "".join(str(x) for x in cuerpo.contents).strip()
        if re.sub(r"<[^>]+>|\s", "", resto):
            partes.append(resto)
    return "\n".join(partes)


def completar_paginas(resultado, html_texto: str, url: str, titulo: str, motor, timeout: float):
    """Si la noticia sigue en otras páginas, las descarga (hasta 6 en total) y devuelve el
    ArticuloExtraido con todo unido. Si no hay más páginas o algo falla, el mismo resultado."""
    from .rss_fulltext import ArticuloExtraido, FullTextExtractor
    siguiente = siguiente_pagina(html_texto, url, pagina=1)
    if not siguiente or resultado is None:
        return resultado
    visitadas, extra = [url], []
    while siguiente and len(visitadas) < MAX_PAGINAS:
        try:
            datos, charset, final = FullTextExtractor.descargar_pagina(siguiente, timeout=timeout)
            html_pag = FullTextExtractor._decodificar(datos, charset)
            trozo = FullTextExtractor.extraer_de_html(html_pag, final, titulo, motor)
        except Exception as e:
            log.debug(f"No se pudo leer la página siguiente {siguiente}: {e}")
            break
        visitadas.append(siguiente)
        if not trozo:
            break
        extra.append(trozo.html)
        siguiente = siguiente_pagina(html_pag, siguiente, visitadas, pagina=len(visitadas))
    if not extra:
        return resultado
    from bs4 import BeautifulSoup
    unido = unir_paginas(resultado.html, extra)
    texto = FullTextExtractor._formatear_soup_a_texto(BeautifulSoup(unido, "lxml"))
    log.debug(f"Artículo en {len(visitadas)} páginas unido: {url}")
    return ArticuloExtraido(html=unido, texto=texto, metodo=resultado.metodo + "+paginas",
                            url_final=resultado.url_final)
