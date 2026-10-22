# -*- coding: utf-8 -*-
"""HTML -> texto plano + MARCAS de estructura, para leer un artículo en un cuadro de texto y
saltar por él con letras (encabezados, tablas, listas, citas, enlaces, imágenes...).

Mismo modelo que el visor universal del autor (ePubViewer): el texto va seguido, una línea por
bloque y sin líneas en blanco, y aparte se guarda una lista de marcas con su posición (índice
de carácter de Python) y su longitud. El lector busca la marca siguiente o anterior con bisect.

- Tablas: una línea "Tabla de N filas y M columnas" (con su título si lo tiene), una línea por
  fila con las celdas separadas por tabuladores y "Fin de la tabla.". La marca guarda la rejilla
  entera para verla en una lista o en HTML. Las tablas de una sola columna son de maquetación y
  se leen como texto normal.
- Citas: "Cita:" ... "Fin de la cita.".
- Imágenes con texto alternativo: "[Imagen: texto]". Sin texto alternativo no se dice nada.

MapaUtf16: el cuadro de texto de Windows cuenta en unidades UTF-16 (un emoji ocupa 2), así que
las posiciones de Python se traducen antes de mover el cursor y al leerlo.
"""
import bisect
import html as _html
import re
import sys
from dataclasses import dataclass, field, replace
from typing import List, Optional

try:
    _
except NameError:  # pragma: no cover - fuera de la aplicación
    def _(s):
        return s

ENCABEZADO, ENLACE, LISTA, ELEMENTO, TABLA, CITA, IMAGEN, SEPARADOR = (
    "encabezado", "enlace", "lista", "elemento", "tabla", "cita", "imagen", "separador")
CONTENEDORES = (TABLA, LISTA, CITA)

_IGNORADAS = {"script", "style", "noscript", "iframe", "object", "embed", "head", "svg",
              "template", "button", "form", "input", "select", "textarea", "nav"}
_BLOQUE = {"p", "div", "section", "article", "main", "header", "footer", "aside", "figure",
           "figcaption", "dl", "dt", "dd", "address", "details", "summary", "center", "tr",
           "caption", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "li", "pre",
           "table", "hr"}
_VINNETAS = ("•", "◦", "-")


@dataclass(frozen=True)
class Marca:
    tipo: str
    posicion: int
    texto: str = ""
    referencia: str = ""
    nivel: int = 0          # encabezado: 1-6; elemento: profundidad; lista: nº de elementos; tabla: filas
    longitud: int = 0
    columnas: int = 0       # solo tablas


@dataclass
class Estructura:
    texto: str = ""
    marcas: List[Marca] = field(default_factory=list)

    def __post_init__(self):
        self.marcas.sort(key=lambda m: m.posicion)
        self._grupos = {}

    def desplazada(self, desplazamiento: int, prefijo: str = "") -> "Estructura":
        """La misma estructura con `prefijo` delante del texto (las marcas se corren)."""
        return Estructura(prefijo + self.texto,
                          [replace(m, posicion=m.posicion + desplazamiento) for m in self.marcas])

    def grupo(self, tipo: str, nivel: int = 0) -> List[Marca]:
        clave = (tipo, nivel)
        if clave not in self._grupos:
            self._grupos[clave] = [m for m in self.marcas if m.tipo == tipo
                                   and (not nivel or m.nivel == nivel)]
        return self._grupos[clave]

    def siguiente(self, tipo: str, posicion: int, adelante: bool = True,
                  nivel: int = 0) -> Optional[Marca]:
        """La marca de ese tipo estrictamente después (o antes) de la posición."""
        g = self.grupo(tipo, nivel)
        posiciones = [m.posicion for m in g]
        if adelante:
            i = bisect.bisect_right(posiciones, posicion)
            return g[i] if i < len(g) else None
        # "Antes" = que empieza antes de la LÍNEA actual, para no volver a la misma marca.
        inicio_linea = self.texto.rfind("\n", 0, posicion) + 1
        i = bisect.bisect_left(posiciones, inicio_linea) - 1
        return g[i] if i >= 0 else None

    def contenedor_en(self, posicion: int, tipos=CONTENEDORES) -> Optional[Marca]:
        """El contenedor (tabla, lista o cita) más interno que contiene la posición."""
        candidatos = [m for m in self.marcas if m.tipo in tipos
                      and m.posicion <= posicion < m.posicion + max(m.longitud, 1)]
        return min(candidatos, key=lambda m: (-m.posicion, m.longitud)) if candidatos else None

    def en_linea(self, posicion: int, tipos) -> Optional[Marca]:
        """La marca de esos tipos que contiene la posición o, si no, la primera de su línea."""
        ini = self.texto.rfind("\n", 0, posicion) + 1
        fin = self.texto.find("\n", posicion)
        fin = len(self.texto) if fin < 0 else fin
        mejor = None
        for m in self.marcas:
            if m.tipo not in tipos:
                continue
            if m.posicion <= posicion < m.posicion + max(m.longitud, 1):
                return m
            if mejor is None and ini <= m.posicion <= fin:
                mejor = m
        return mejor

    def linea_en(self, posicion: int) -> str:
        ini = self.texto.rfind("\n", 0, posicion) + 1
        fin = self.texto.find("\n", posicion)
        return self.texto[ini:len(self.texto) if fin < 0 else fin]


# ------------------------------------------------------------------ construcción
class _Lineas:
    """Construye el texto línea a línea llevando la posición (como ConstructorLineas)."""

    def __init__(self):
        self.lineas: List[str] = []
        self._partes: List[str] = []
        self._total = 0          # caracteres de las líneas ya cerradas (con su "\n")
        self._linea = 0          # caracteres de la línea en curso
        self._espacio = False

    @property
    def posicion(self) -> int:
        return self._total + self._linea

    def texto(self, fragmento: str):
        if not fragmento:
            return
        palabras = fragmento.split()
        if not palabras:
            self._espacio = True
            return
        prefijo = " " if (self._espacio or fragmento[0].isspace()) and self._linea else ""
        trozo = prefijo + " ".join(palabras)
        self._partes.append(trozo)
        self._linea += len(trozo)
        self._espacio = fragmento[-1].isspace()

    def literal(self, fragmento: str):
        if fragmento:
            self._partes.append(fragmento)
            self._linea += len(fragmento)
            self._espacio = False

    def cerrar(self):
        """Termina la línea en curso; las líneas vacías se descartan."""
        linea = "".join(self._partes).rstrip()
        if linea.strip():
            self.lineas.append(linea)
            self._total += len(linea) + 1
        self._partes, self._linea, self._espacio = [], 0, False

    def linea(self, texto: str):
        self.cerrar()
        texto = texto.replace("\n", " ").rstrip()
        if texto.strip():
            self.lineas.append(texto)
            self._total += len(texto) + 1

    def inicio_desde(self, antes: int) -> int:
        """Dónde empieza de verdad lo añadido desde la posición `antes` (sin el espacio que se
        antepone para separarlo de lo anterior)."""
        actual = "".join(self._partes)
        desde = antes - self._total
        if 0 <= desde <= len(actual):
            anadido = actual[desde:]
            return antes + (len(anadido) - len(anadido.lstrip()))
        return antes

    def resultado(self) -> str:
        self.cerrar()
        return "\n".join(self.lineas)


def _texto_de(nodo) -> str:
    return re.sub(r"\s+", " ", nodo.get_text(" ", strip=True) if hasattr(nodo, "get_text") else str(nodo)).strip()


def _filas_de_tabla(tabla) -> List[List[str]]:
    filas = []
    for tr in tabla.find_all("tr"):
        if tr.find_parent("table") is not tabla:
            continue      # fila de una tabla anidada
        celdas = [_texto_de(c) for c in tr.find_all(["th", "td"], recursive=False)]
        if any(celdas):
            filas.append(celdas)
    return filas


def html_a_estructura(contenido: str) -> Estructura:
    """Convierte el HTML de un artículo en texto con marcas. Nunca lanza: si algo falla,
    devuelve el texto plano sin marcas."""
    contenido = contenido or ""
    if "<" not in contenido:
        lineas = [re.sub(r"[ \t]+", " ", x).strip() for x in _html.unescape(contenido).splitlines()]
        return Estructura("\n".join(x for x in lineas if x), [])
    try:
        from bs4 import BeautifulSoup, Comment, NavigableString, Tag
        try:
            soup = BeautifulSoup(contenido, "lxml")
        except Exception:
            soup = BeautifulSoup(contenido, "html.parser")
    except Exception:
        from app.core.html_utils import html_a_texto
        return Estructura(html_a_texto(contenido), [])

    out = _Lineas()
    marcas: List[Marca] = []
    listas: List[dict] = []

    def recorrer(nodo):
        if isinstance(nodo, Comment):
            return
        if isinstance(nodo, NavigableString):
            out.texto(str(nodo))
            return
        if not isinstance(nodo, Tag):
            return
        nombre = (nodo.name or "").lower()
        if nombre in _IGNORADAS:
            return
        if nombre == "br":
            out.cerrar()
            return
        if nombre == "hr":
            out.cerrar()
            marcas.append(Marca(SEPARADOR, out.posicion, "", "", 0, 40))
            out.linea("-" * 40)
            return
        if nombre == "img":
            alt = (nodo.get("alt") or nodo.get("aria-label") or nodo.get("title") or "").strip()
            if alt:
                rotulo = "[" + _("Imagen: {0}").format(re.sub(r"\s+", " ", alt)) + "]"
                antes = out.posicion
                out.texto(" " + rotulo + " ")
                marcas.append(Marca(IMAGEN, out.inicio_desde(antes), alt, nodo.get("src") or "",
                                    1, len(rotulo)))
            return
        if nombre == "table":
            filas = _filas_de_tabla(nodo)
            columnas = max((len(f) for f in filas), default=0)
            if columnas <= 1 or len(filas) == 0:
                for hijo in nodo.children:     # tabla de maquetación: texto normal
                    recorrer(hijo)
                out.cerrar()
                return
            out.cerrar()
            inicio = out.posicion
            cap = nodo.find("caption")
            titulo = _texto_de(cap) if cap else ""
            cab = _("Tabla de {0} filas y {1} columnas").format(len(filas), columnas)
            out.linea(cab + (": " + titulo if titulo else ""))
            for f in filas:
                out.linea("\t".join(c.replace("\t", " ") for c in f))
            out.linea(_("Fin de la tabla."))
            rejilla = "\n".join("\t".join(f) for f in filas)
            marcas.append(Marca(TABLA, inicio, titulo or " ".join(filas[0]), rejilla, len(filas),
                                out.posicion - inicio, columnas))
            return
        if nombre == "pre":
            out.cerrar()
            for linea in nodo.get_text().splitlines():
                out.linea(linea.replace("\t", "    "))
            return
        es_bloque = nombre in _BLOQUE
        if es_bloque:
            out.cerrar()
        inicio = out.posicion
        if nombre in ("ul", "ol"):
            elementos = len(nodo.find_all("li", recursive=False))
            try:
                numero = int(nodo.get("start") or 1)
            except ValueError:
                numero = 1
            listas.append({"ordenada": nombre == "ol", "n": numero})
            for hijo in nodo.children:
                recorrer(hijo)
            listas.pop()
            out.cerrar()
            if elementos:
                marcas.append(Marca(LISTA, inicio, "", "", elementos, out.posicion - inicio))
            return
        if nombre == "li":
            nivel = max(len(listas), 1)
            out.literal("  " * (nivel - 1))
            if listas and listas[-1]["ordenada"]:
                out.literal(f"{listas[-1]['n']}. ")
                listas[-1]["n"] += 1
            else:
                out.literal(_VINNETAS[min(nivel, 3) - 1] + " ")
            marcas.append(Marca(ELEMENTO, inicio, _texto_de(nodo)[:200], "", nivel))
            for hijo in nodo.children:
                recorrer(hijo)
            out.cerrar()
            return
        if nombre == "blockquote":
            out.linea(_("Cita:"))
            for hijo in nodo.children:
                recorrer(hijo)
            out.linea(_("Fin de la cita."))
            marcas.append(Marca(CITA, inicio, "", "", 0, out.posicion - inicio))
            return
        if len(nombre) == 2 and nombre[0] == "h" and nombre[1] in "123456":
            rotulo = _texto_de(nodo)
            for hijo in nodo.children:
                recorrer(hijo)
            out.cerrar()
            if rotulo:
                marcas.append(Marca(ENCABEZADO, inicio, rotulo, "", int(nombre[1]),
                                    out.posicion - inicio))
            return
        if nombre == "a" and nodo.get("href"):
            href = nodo["href"].strip()
            antes = out.posicion
            for hijo in nodo.children:
                recorrer(hijo)
            rotulo = _texto_de(nodo)
            if rotulo and not href.lower().startswith(("javascript:", "#")):
                inicio_enlace = out.inicio_desde(antes)
                marcas.append(Marca(ENLACE, inicio_enlace, rotulo, href, 0,
                                    max(out.posicion - inicio_enlace, 0)))
            return
        for hijo in nodo.children:
            recorrer(hijo)
        if es_bloque:
            out.cerrar()

    try:
        recorrer(soup.body or soup)
        texto = out.resultado()
    except RecursionError:
        from app.core.html_utils import html_a_texto
        return Estructura(html_a_texto(contenido), [])
    return Estructura(texto, marcas)


# ------------------------------------------------------------------ anuncios y HTML
def describir(marca: Marca, estructura: Estructura) -> str:
    """Lo que se dice al llegar a una marca con las letras."""
    if marca.tipo == ENCABEZADO:
        return _("{0}. Encabezado de nivel {1}.").format(marca.texto, marca.nivel)
    if marca.tipo == ENLACE:
        return _("{0}. Enlace.").format(marca.texto)
    if marca.tipo == TABLA:
        return estructura.linea_en(marca.posicion) + "."
    if marca.tipo == LISTA:
        return _("Lista de {0} elementos.").format(marca.nivel) + " " + estructura.linea_en(marca.posicion)
    if marca.tipo == ELEMENTO:
        return estructura.linea_en(marca.posicion).strip()
    if marca.tipo == CITA:
        siguiente = estructura.texto.find("\n", marca.posicion)
        return _("Cita.") + " " + (estructura.linea_en(siguiente + 1) if siguiente >= 0 else "")
    if marca.tipo == IMAGEN:
        return _("Imagen: {0}").format(marca.texto)
    if marca.tipo == SEPARADOR:
        return _("Separador.")
    return estructura.linea_en(marca.posicion)


def html_de_tabla(marca: Marca) -> str:
    """La tabla como HTML semántico (la primera fila como encabezado de columnas)."""
    filas = [f.split("\t") for f in (marca.referencia or "").split("\n") if f]
    if not filas:
        return "<p></p>"
    e = _html.escape
    cab = "".join(f'<th scope="col">{e(c)}</th>' for c in filas[0])
    cuerpo = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in f) + "</tr>" for f in filas[1:])
    titulo = f"<caption>{e(marca.texto)}</caption>" if marca.texto else ""
    return (f"<table border='1'>{titulo}<thead><tr>{cab}</tr></thead><tbody>{cuerpo}</tbody></table>"
            f"<p>{e(_('{0} filas por {1} columnas.').format(marca.nivel, marca.columnas))}</p>")


# ------------------------------------------------------------------ UTF-16
_RE_ASTRALES = re.compile(r"[\U00010000-\U0010FFFF]")
_PLATAFORMA_UTF16 = sys.platform in ("win32", "darwin")


class MapaUtf16:
    """Traduce posiciones de Python <-> posiciones del cuadro de texto (UTF-16 en Windows)."""
    __slots__ = ("_astrales", "directo")

    def __init__(self, texto: str):
        texto = texto or ""
        if not _PLATAFORMA_UTF16 or texto.isascii() or not _RE_ASTRALES.search(texto):
            self.directo, self._astrales = True, []
            return
        self.directo = False
        self._astrales = [m.start() for m in _RE_ASTRALES.finditer(texto)]

    def a_control(self, posicion: int) -> int:
        return posicion if self.directo else posicion + bisect.bisect_left(self._astrales, posicion)

    def desde_control(self, posicion: int) -> int:
        if self.directo:
            return posicion
        bajo, alto = 0, posicion
        while bajo < alto:
            medio = (bajo + alto) // 2
            if self.a_control(medio) < posicion:
                bajo = medio + 1
            else:
                alto = medio
        return bajo
