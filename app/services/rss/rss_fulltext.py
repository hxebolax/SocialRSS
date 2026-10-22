# -*- coding: utf-8 -*-
"""
Extracción del ARTÍCULO COMPLETO de una página web (lector limpio de RSS).

Muchos feeds solo traen el primer párrafo (El Mundo, por ejemplo), así que para leer la noticia
entera hay que descargar la página y quedarse con el cuerpo, como hace la vista de lectura de
Firefox. Se combinan tres extractores y se elige el mejor resultado:

1. Trafilatura (preferente): el extractor abierto con mejor puntuación en los bancos de prueba
   públicos (ScrapingHub, SIGIR 2023). Conserva el subtítulo y los párrafos que Readability a
   veces se come (en ABC perdía justo el primero, el que da la noticia).
2. readability-lxml: el algoritmo clásico (el de Firefox es su pariente en JavaScript). Respaldo
   cuando Trafilatura no está instalada o devuelve claramente menos texto.
3. JSON-LD (`articleBody`): muchos periódicos publican el cuerpo íntegro en los datos
   estructurados para los buscadores. Sirve de candidato extra cuando el HTML visible viene
   recortado (muros de pago "suaves", carga diferida por JavaScript).

Y como último recurso, una limpieza heurística con BeautifulSoup.

Además la DESCARGA imita a un navegador de verdad (cabeceras completas): varios periódicos (El
País) responden 403 a peticiones que solo llevan un User-Agent. El cuerpo se lee en streaming
con tope de tamaño y la codificación se detecta sobre los bytes (cabecera, <meta> o análisis),
no a ciegas.

Trafilatura se importa de forma DIFERIDA (primer uso, siempre en un hilo de fondo): no se carga
al arrancar la aplicación.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Intentar importar readability (ligero; ya se usaba antes).
HAS_READABILITY = False
try:
    from readability import Document
    HAS_READABILITY = True
except ImportError:
    pass

# Cabeceras de un Firefox de escritorio. Con solo el User-Agent, algunos CDN de prensa
# (El País) devuelven 403; con el juego completo responden igual que al navegador.
CABECERAS_NAVEGADOR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Tope de la página descargada: las portadas de prensa rondan 300-600 KB; 8 MB deja margen
# sin arriesgar la memoria con un enlace que apunte a un archivo enorme.
MAX_BYTES_PAGINA = 8 * 1024 * 1024

# Por debajo de esto no se considera que se haya obtenido un artículo (será un aviso de
# cookies, un "suscríbete" o una página de error).
MIN_CARACTERES_ARTICULO = 200

# Extractores seleccionables en Preferencias, RSS (clave rss.fulltext_extractor).
MOTORES_EXTRACCION = ("auto", "trafilatura", "readability", "jsonld")

_ETIQUETAS_BLOQUE =["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "br"]


@dataclass
class ArticuloExtraido:
    """Resultado de extraer un artículo: HTML limpio (para guardar y para la vista HTML del
    lector), su texto plano, qué método ganó y la URL final tras las redirecciones."""
    html: str
    texto: str
    metodo: str
    url_final: str = ""


def _normalizar_espacios(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def _longitud_texto(html_content: str) -> int:
    """Número de caracteres de TEXTO de un fragmento HTML (sin etiquetas ni espacios extra)."""
    if not html_content:
        return 0
    try:
        return len(_normalizar_espacios(BeautifulSoup(html_content, "lxml").get_text(" ")))
    except Exception:
        return len(_normalizar_espacios(re.sub(r"<[^>]+>", " ", html_content)))


class FullTextExtractor:
    """Extrae el contenido legible de una página HTML eliminando el ruido."""

    # ------------------------------------------------------------------ descarga
    @staticmethod
    def descargar_pagina(url: str, timeout: float = 20.0,
                         refrescar_sesion: bool = False) -> Tuple[bytes, str, str]:
        """Descarga la página como lo haría un navegador. Devuelve (bytes, charset declarado
        en la cabecera o "", URL final). Lanza excepción si la respuesta no es 200 o no es
        HTML.

        Si el dominio está marcado en Preferencias como medio en el que el usuario está
        SUSCRITO, se descarga con las cookies de su sesión del navegador (ver
        rss_suscripciones.py) y con una sesión HTTP aparte, para que esas cookies no se
        mezclen con el resto de la aplicación."""
        from app.services.rss.rss_suscripciones import cookies_para
        cookies = cookies_para(url, refrescar=refrescar_sesion)
        if cookies is not None:
            import requests
            sesion = requests.Session()
        else:
            from app.core.net import http_session
            sesion = None
        cliente = sesion or http_session()
        res = cliente.get(url, headers=CABECERAS_NAVEGADOR, timeout=(8, timeout), stream=True,
                          allow_redirects=True, cookies=cookies)
        try:
            if res.status_code != 200:
                raise ValueError(f"HTTP {res.status_code}")
            tipo = (res.headers.get("Content-Type") or "").lower()
            if tipo and "html" not in tipo and "xml" not in tipo:
                raise ValueError(f"No es una página web ({tipo})")
            trozos = []
            total = 0
            for trozo in res.iter_content(chunk_size=65536):
                total += len(trozo)
                if total > MAX_BYTES_PAGINA:
                    raise ValueError("La página supera el tamaño permitido")
                trozos.append(trozo)
            charset = ""
            m = re.search(r"charset=([\w\-]+)", tipo)
            if m:
                charset = m.group(1)
            return b"".join(trozos), charset, res.url or url
        finally:
            res.close()
            if sesion is not None:
                sesion.close()

    @staticmethod
    def _decodificar(datos: bytes, charset: str) -> str:
        """Bytes -> texto con la codificación correcta: la de la cabecera, la del <meta> o,
        si no consta ninguna, detectada por análisis (Trafilatura/charset_normalizer)."""
        candidatos = []
        if charset:
            candidatos.append(charset)
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", datos[:8192], re.I)
        if m:
            candidatos.append(m.group(1).decode("ascii", "ignore"))
        for cod in candidatos:
            try:
                return datos.decode(cod)
            except (LookupError, UnicodeDecodeError):
                continue
        try:
            from trafilatura.utils import decode_file
            return decode_file(datos)
        except Exception:
            pass
        try:
            return datos.decode("utf-8")
        except UnicodeDecodeError:
            return datos.decode("cp1252", errors="replace")

    # ------------------------------------------------------------------ extractores
    @staticmethod
    def _con_trafilatura(html_texto: str, url: str) -> str:
        """Cuerpo con Trafilatura, en HTML (párrafos, títulos, listas, citas, tablas)."""
        try:
            import trafilatura
        except ImportError:
            return ""
        try:
            salida = trafilatura.extract(
                html_texto, url=url or None, output_format="html",
                include_comments=False, include_tables=True, include_images=True,
                include_links=True, include_formatting=True, favor_recall=True) or ""
        except Exception as e:
            log.debug(f"Trafilatura falló en {url}: {e}")
            return ""
        return FullTextExtractor._quitar_listas_de_enlaces(salida)

    @staticmethod
    def _quitar_listas_de_enlaces(html_content: str) -> str:
        """Quita las listas que son solo enlaces ("noticias relacionadas", "te puede
        interesar"): Trafilatura en modo exhaustivo a veces las mete al principio o al final
        del cuerpo. Una lista se descarta si al menos 2 de sus elementos existen y en todos
        ellos casi todo el texto es un enlace."""
        if not html_content or "<li" not in html_content:
            return html_content
        try:
            soup = BeautifulSoup(html_content, "lxml")
            quitadas = 0
            for lista in soup.find_all(["ul", "ol"]):
                items = lista.find_all("li", recursive=False)
                if len(items) < 2:
                    continue
                todos_enlace = True
                for li in items:
                    total = len(_normalizar_espacios(li.get_text(" ")))
                    en_enlace = sum(len(_normalizar_espacios(a.get_text(" "))) for a in li.find_all("a"))
                    if total == 0 or en_enlace < 0.6 * total:
                        todos_enlace = False
                        break
                if todos_enlace:
                    lista.decompose()
                    quitadas += 1
            if not quitadas:
                return html_content
            cuerpo = soup.body or soup
            return "".join(str(h) for h in cuerpo.contents)
        except Exception:
            return html_content

    @staticmethod
    def _con_readability(html_texto: str) -> str:
        if not HAS_READABILITY:
            return ""
        try:
            return Document(html_texto).summary(html_partial=True) or ""
        except Exception as e:
            log.debug(f"Fallo en readability-lxml: {e}")
            return ""

    @staticmethod
    def _con_jsonld(html_texto: str) -> str:
        """Cuerpo publicado en los datos estructurados (schema.org articleBody), como HTML
        de párrafos. Devuelve el más largo si hay varios."""
        mejor = ""
        for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>",
                             html_texto, re.S | re.I):
            try:
                datos = json.loads(m.group(1).strip())
            except Exception:
                continue
            pendientes = datos if isinstance(datos, list) else [datos]
            while pendientes:
                nodo = pendientes.pop()
                if isinstance(nodo, list):
                    pendientes.extend(nodo)
                    continue
                if not isinstance(nodo, dict):
                    continue
                if isinstance(nodo.get("@graph"), list):
                    pendientes.extend(nodo["@graph"])
                cuerpo = nodo.get("articleBody")
                if isinstance(cuerpo, str) and len(cuerpo) > len(mejor):
                    mejor = cuerpo
        if not mejor:
            return ""
        import html as _html
        # A veces el articleBody trae HTML dentro; si no, se trocea por saltos de línea.
        if re.search(r"<(p|br|div)\b", mejor, re.I):
            return mejor
        parrafos = [p.strip() for p in re.split(r"\n\s*\n|\n", mejor) if p.strip()]
        return "".join(f"<p>{_html.escape(p)}</p>" for p in parrafos)

    @staticmethod
    def _heuristica_bs4(html_texto: str) -> str:
        """Último recurso: quitar la basura visual y quedarse con article/main/body."""
        try:
            soup = BeautifulSoup(html_texto, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form",
                             "iframe", "noscript"]):
                tag.decompose()
            cuerpo = None
            for tag_name in ["article", "main"]:
                cuerpo = soup.find(tag_name)
                if cuerpo:
                    break
            if not cuerpo:
                cuerpo = soup.find(attrs={"role": "main"}) or soup.body or soup
            return str(cuerpo)
        except Exception as e:
            log.error(f"Error al procesar HTML con BeautifulSoup: {e}")
            return ""

    @staticmethod
    def _quitar_titulo_repetido(html_content: str, titulo: str) -> str:
        """El lector ya muestra el titular arriba: si el cuerpo empieza repitiéndolo, fuera."""
        if not html_content or not titulo:
            return html_content
        try:
            soup = BeautifulSoup(html_content, "lxml")
            primero = None
            for el in soup.find_all(["h1", "h2", "p"]):
                if _normalizar_espacios(el.get_text(" ")):
                    primero = el
                    break
            if primero is not None and (
                    _normalizar_espacios(primero.get_text(" ")).lower()
                    == _normalizar_espacios(titulo).lower()):
                primero.decompose()
                cuerpo = soup.body or soup
                return "".join(str(h) for h in cuerpo.contents)
        except Exception:
            pass
        return html_content

    # ------------------------------------------------------------------ API
    @staticmethod
    def motor_configurado() -> str:
        """Extractor elegido en Preferencias, RSS: 'auto' (recomendado), 'trafilatura',
        'readability' o 'jsonld'. Cualquier valor desconocido se trata como 'auto'."""
        try:
            from app.core.config_manager import config
            motor = (config.get("rss", "fulltext_extractor", "auto") or "auto").strip().lower()
        except Exception:
            motor = "auto"
        return motor if motor in MOTORES_EXTRACCION else "auto"

    @staticmethod
    def extraer_de_html(html_texto: str, url: str = "", titulo: str = "",
                        motor: Optional[str] = None) -> Optional[ArticuloExtraido]:
        """Obtiene el cuerpo del artículo con el extractor elegido (motor=None: el de las
        Preferencias). En 'auto' se comparan todos y gana el mejor. Con un motor concreto se
        usa ese; si no saca nada útil, se prueba con los demás antes de rendirse (mejor un
        artículo que ninguno). Devuelve None si ninguno da un artículo con sustancia."""
        if not html_texto or not html_texto.strip():
            return None
        motor = motor if motor in MOTORES_EXTRACCION else FullTextExtractor.motor_configurado()
        funciones = {
            "trafilatura": lambda: FullTextExtractor._con_trafilatura(html_texto, url),
            "readability": lambda: FullTextExtractor._con_readability(html_texto),
            "jsonld": lambda: FullTextExtractor._con_jsonld(html_texto),
        }
        por_metodo = {}

        def _probar(metodo):
            if metodo not in por_metodo:
                fragmento = funciones[metodo]()
                longitud = _longitud_texto(fragmento)
                por_metodo[metodo] = (metodo, fragmento, longitud) if longitud else None
            return por_metodo[metodo]

        elegido = None
        if motor != "auto":
            preferido = _probar(motor)
            if preferido and preferido[2] >= MIN_CARACTERES_ARTICULO:
                elegido = preferido
            else:
                log.debug(f"El extractor {motor} no sacó un artículo de {url}; se prueban los demás.")
        if elegido is None:
            for metodo in funciones:
                _probar(metodo)
            # Trafilatura manda salvo que Readability saque claramente más (>25 %): entonces
            # es que Trafilatura se ha dejado un bloque del cuerpo.
            elegido = por_metodo.get("trafilatura")
            lectura = por_metodo.get("readability")
            if elegido is None or (lectura and lectura[2] > elegido[2] * 1.25):
                elegido = lectura or elegido
            # El articleBody de JSON-LD solo gana si el HTML visible viene claramente recortado.
            jsonld = por_metodo.get("jsonld")
            if jsonld and jsonld[2] >= MIN_CARACTERES_ARTICULO and (
                    elegido is None or jsonld[2] > elegido[2] * 1.3):
                elegido = jsonld
        if elegido is None:
            fragmento = FullTextExtractor._heuristica_bs4(html_texto)
            longitud = _longitud_texto(fragmento)
            if longitud:
                elegido = ("heuristica", fragmento, longitud)
        if elegido is None or elegido[2] < MIN_CARACTERES_ARTICULO:
            return None

        cuerpo_html = FullTextExtractor._quitar_titulo_repetido(elegido[1], titulo)
        texto = FullTextExtractor._formatear_soup_a_texto(BeautifulSoup(cuerpo_html, "lxml"))
        log.debug(f"Artículo extraído con {elegido[0]} ({elegido[2]} caracteres): {url}")
        return ArticuloExtraido(html=cuerpo_html, texto=texto, metodo=elegido[0], url_final=url)

    @staticmethod
    def diagnostico_extraccion(url: str) -> str:
        """Devuelve un mensaje explicativo y accesible de por qué no se pudo obtener
        el texto completo de una URL según las reglas de medios y muros de pago."""
        if not url:
            return "El artículo no dispone de enlace a la web."
        try:
            from .bpc_rules import BPCRulesEngine, normalizar_dominio
            dom = normalizar_dominio(url)
            motor = BPCRulesEngine.obtener()
            if motor.es_sitio_sin_arreglo(dom):
                return (f"El medio «{dom}» tiene un muro de pago estricto sin solución "
                        f"automatizada posible. Requiere suscripción activa o abrirlo en el navegador.")
            regla = motor.buscar_regla(dom)
            if regla:
                return (f"El medio «{dom}» es compatible con reglas de lectura, pero este artículo "
                        f"en concreto requiere suscripción o ejecución completa de JavaScript en navegador.")
        except Exception:
            pass
        return ("No se encontró en la web un texto más completo que el que ya tienes. "
                "Puede que el feed ya traiga el artículo entero o que la página no se deje leer.")

    @staticmethod
    def extraer_articulo(url: str, titulo: str = "", timeout: float = 20.0,
                         motor: Optional[str] = None,
                         refrescar_sesion: bool = False) -> Optional[ArticuloExtraido]:
        """Descarga la URL y extrae su artículo. Aplica primero la extracción pública estándar
        y las suscripciones del usuario, y si se detecta muro de pago, entradilla o bloqueo,
        aplica las estrategias modulares de Bypass Paywalls Clean (AMP, Googlebot, desofuscación)."""
        resultado_estandar = None
        try:
            datos, charset, url_final = FullTextExtractor.descargar_pagina(
                url, timeout=timeout, refrescar_sesion=refrescar_sesion)
            html_texto = FullTextExtractor._decodificar(datos, charset)
            resultado_estandar = FullTextExtractor.extraer_de_html(html_texto, url_final, titulo, motor)
            if resultado_estandar:
                resultado_estandar.url_final = url_final
                # Noticias partidas en varias páginas: se siguen y se unen (rss_paginas.py).
                from .rss_paginas import completar_paginas
                resultado_estandar = completar_paginas(resultado_estandar, html_texto, url_final,
                                                       titulo, motor, timeout)
        except Exception as e:
            log.warning(f"No se pudo descargar directamente el artículo de {url}: {e}")

        # Comprobar si el resultado estándar es válido y completo
        from .bpc_provider import BPCProvider
        es_incompleto = (resultado_estandar is None or
                         BPCProvider.es_entradilla_o_muro(resultado_estandar.texto))

        if not es_incompleto and resultado_estandar:
            return resultado_estandar

        # Si el resultado estándar falló o solo dio una entradilla/muro, intentar BPC
        try:
            resultado_bpc = BPCProvider.intentar_extraccion_bpc(url, titulo=titulo)
            if resultado_bpc and not BPCProvider.es_entradilla_o_muro(resultado_bpc.texto):
                log.info(f"Artículo extraído exitosamente mediante estrategia BPC ({resultado_bpc.metodo}): {url}")
                return resultado_bpc
        except Exception as e:
            log.debug(f"Fallo en intento de extracción BPC para {url}: {e}")

        # Si BPC tampoco pudo pero el estándar tenía algo de texto (sin muro flagrante)
        if resultado_estandar and len(resultado_estandar.texto.strip()) >= MIN_CARACTERES_ARTICULO:
            return resultado_estandar

        log.info(f"No se encontró un artículo completo legible en {url}")
        return None

    @staticmethod
    def descargar_limpio(url: str, timeout: float = 20.0) -> str:
        """Compatibilidad: descarga la URL y devuelve el artículo en TEXTO plano ("" si no)."""
        resultado = FullTextExtractor.extraer_articulo(url, timeout=timeout)
        return resultado.texto if resultado else ""

    @staticmethod
    def limpiar_html(html_content: str) -> str:
        """Compatibilidad: extrae el artículo de un HTML ya descargado y lo devuelve en texto."""
        resultado = FullTextExtractor.extraer_de_html(html_content)
        return resultado.texto if resultado else ""

    @staticmethod
    def _formatear_soup_a_texto(soup) -> str:
        """Formatea elementos del árbol HTML a texto ordenado con saltos de línea adecuados."""
        texto_lineas = []

        for elem in soup.find_all(_ETIQUETAS_BLOQUE):
            # Un <p> dentro de un <li> o <blockquote> ya sale con su contenedor.
            if elem.name == "p" and elem.find_parent(["li", "blockquote"]):
                continue
            text = elem.get_text().strip()
            if not text:
                continue

            if elem.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                texto_lineas.append(f"\n# {text}\n")
            elif elem.name == "li":
                texto_lineas.append(f"* {text}")
            elif elem.name == "blockquote":
                texto_lineas.append(f"\n> {text}\n")
            elif elem.name == "pre":
                texto_lineas.append(f"\n```\n{text}\n```\n")
            else:
                texto_lineas.append(text)

        cuerpo_limpio = "\n\n".join(texto_lineas)
        while "\n\n\n" in cuerpo_limpio:
            cuerpo_limpio = cuerpo_limpio.replace("\n\n\n", "\n\n")

        return cuerpo_limpio.strip()
