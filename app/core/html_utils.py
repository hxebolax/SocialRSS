# -*- coding: utf-8 -*-
"""
Utilidades para convertir contenido HTML (de feeds RSS o toots de Mastodon)
en texto plano estructurado y legible para lectores de pantalla, así como en
documentos HTML autónomos para visualizarlos en el navegador.

La lógica de conversión a texto estructurado está inspirada conceptualmente en
el cliente accesible SimpleRSS, pero es una implementación propia y
simplificada. Su objetivo es que el texto resultante mantenga párrafos,
encabezados y listas con saltos de línea apropiados, en lugar de pegar todo el
texto sin formato (que es el problema clásico de BeautifulSoup.get_text()).
"""

import re
import html as html
import logging

log = logging.getLogger(__name__)

try:
	import nh3
	HAS_NH3 = True
except ImportError:
	HAS_NH3 = False

# Lista blanca para el HTML NO CONFIABLE (artículos RSS, toots, mensajes): solo
# estructura y formato. Nada de script, iframe, object, embed, form, style ni
# manejadores on*. Los medios (audio/vídeo) tampoco entran: los reproduce la app.
_TAGS_SEGUROS = {
	"a", "abbr", "address", "article", "aside", "b", "blockquote", "br",
	"caption", "cite", "code", "dd", "del", "details", "dfn", "div", "dl",
	"dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6",
	"hr", "i", "img", "ins", "kbd", "li", "main", "mark", "ol", "p", "pre",
	"q", "s", "section", "small", "span", "strong", "sub", "summary", "sup",
	"table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}
_ATTRS_SEGUROS = {
	"a": {"href", "title"},
	"img": {"src", "alt", "title", "width", "height", "loading"},
	"td": {"colspan", "rowspan"},
	"th": {"colspan", "rowspan", "scope"},
	"ol": {"start", "type"},
	"abbr": {"title"},
	"q": {"cite"},
	"blockquote": {"cite"},
	"*": {"lang", "dir"},
}
_ESQUEMAS_URL = {"http", "https", "mailto"}


def _sanear_html_respaldo(contenido: str) -> str:
	"""Respaldo si nh3 no está disponible: lista blanca con BeautifulSoup. Elimina
	por completo el contenido activo (script, style, iframe...), desenvuelve las
	etiquetas desconocidas y filtra atributos y esquemas de URL."""
	from bs4 import BeautifulSoup, Comment, Tag
	try:
		soup = BeautifulSoup(contenido, "lxml")
	except Exception:
		soup = BeautifulSoup(contenido, "html.parser")
	for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
		c.extract()
	for tag in soup.find_all(["script", "style", "noscript", "iframe", "object",
							  "embed", "applet", "form", "input", "button",
							  "select", "textarea", "link", "meta", "base",
							  "audio", "video", "source", "track", "svg", "math"]):
		tag.decompose()
	for tag in list(soup.find_all(True)):
		if not isinstance(tag, Tag):
			continue
		nombre = (tag.name or "").lower()
		if nombre not in _TAGS_SEGUROS:
			tag.unwrap()
			continue
		permitidos = _ATTRS_SEGUROS.get(nombre, set()) | _ATTRS_SEGUROS["*"]
		for attr in list(tag.attrs.keys()):
			if attr.lower() not in permitidos or attr.lower().startswith("on"):
				del tag.attrs[attr]
		for attr in ("href", "src", "cite"):
			val = tag.attrs.get(attr)
			if not val:
				continue
			val = str(val).strip()
			esquema = val.split(":", 1)[0].lower() if ":" in val.split("/", 1)[0] else ""
			if esquema and esquema not in _ESQUEMAS_URL:
				del tag.attrs[attr]
		if nombre == "a" and tag.attrs.get("href"):
			tag.attrs["rel"] = "noopener noreferrer nofollow"
	cuerpo = soup.body or soup
	return cuerpo.decode_contents() if hasattr(cuerpo, "decode_contents") else str(soup)


def sanear_html(contenido: str) -> str:
	"""Sanea HTML procedente de fuentes NO confiables (feeds, publicaciones,
	mensajes) con una lista blanca de etiquetas y atributos: elimina scripts,
	manejadores on*, object/embed/iframe, formularios y estilos, y solo deja
	enlaces http/https/mailto. Devuelve HTML seguro para insertar en un WebView."""
	if not contenido:
		return ""
	try:
		if HAS_NH3:
			return nh3.clean(
				contenido,
				tags=_TAGS_SEGUROS,
				attributes=_ATTRS_SEGUROS,
				url_schemes=_ESQUEMAS_URL,
				link_rel="noopener noreferrer nofollow",
				strip_comments=True,
			)
		return _sanear_html_respaldo(contenido)
	except Exception as e:
		log.warning(f"No se pudo sanear el HTML; se muestra como texto plano: {e}")
		return "<p>" + html.escape(html_a_texto(contenido)) + "</p>"

# Conjuntos de etiquetas que determinan cómo se insertan los saltos de línea.
_BLOQUE_DOBLE = {
	"p", "div", "article", "section", "main", "aside", "blockquote",
	"figure", "figcaption", "details", "summary", "address", "pre",
	"table", "thead", "tbody", "tfoot", "form", "fieldset", "dl", "dd", "dt"
}
_ENCABEZADOS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOQUE_SIMPLE = {"br", "hr", "li", "tr", "th", "td", "option"}
_LISTAS = {"ul", "ol"}


def _limpiar_texto(texto: str) -> str:
	"""Normaliza saltos de línea y elimina espacios y líneas en blanco sobrantes."""
	if not texto:
		return ""

	# Normalizar saltos de línea
	texto = texto.replace("\r\n", "\n").replace("\r", "\n")

	# Quitar espacios al final de cada línea (preservando indentación inicial)
	lineas = []
	for linea in texto.split("\n"):
		if not linea.strip():
			lineas.append("")
		else:
			lineas.append(linea.rstrip())
	texto = "\n".join(lineas)

	# Colapsar espacios/tabuladores múltiples dentro de las líneas
	texto = re.sub(r"[ \t]+", " ", texto)

	# Máximo una línea en blanco consecutiva
	texto = re.sub(r"\n{3,}", "\n\n", texto)

	return texto.strip()


_RE_EMOJI = re.compile(
	"["
	"\U0001F000-\U0001FAFF"  # TODOS los pictogramas suplementarios (emoticonos, transporte,
	                          # símbolos, banderas 1F1E6-1F1FF, símbolos extendidos-A, etc.)
	"\U00002600-\U000027BF"  # símbolos misceláneos y dingbats (✅ ☀ ✂ ✨...)
	"\U00002300-\U000023FF"  # técnicos: reloj ⏰, ⌚, play/pausa ▶ ⏸, ⏳...
	"\U00002B00-\U00002BFF"  # estrellas ⭐, cuadros ⬛, flechas ⬅...
	"\U00002190-\U000021FF"  # flechas ← → ↔ ↩...
	"\U00002934\U00002935"   # flechas curvas emoji ⤴ ⤵
	"\U000025AA\U000025AB\U000025B6\U000025C0\U000025FB-\U000025FE"  # formas geométricas emoji ▪ ▫ ▶ ◀ ◻ ◼
	"\U00002122\U00002139"   # ™ ℹ
	"\U00003030\U0000303D\U00003297\U00003299"  # 〰 〽 ㊗ ㊙
	"\U0000FE00-\U0000FE0F"  # selectores de variación (el "️" que vuelve emoji a un signo)
	"\U0000200D"             # ZWJ: une secuencias (familias, profesiones...)
	"\U000020E3"             # combinante de "keycap" (los 1️⃣ 2️⃣...)
	"]+",
	flags=re.UNICODE,
)


def quitar_emojis(texto: str) -> str:
	"""Elimina los emojis de un texto (para lectura más limpia si el usuario lo pide).
	Cubre el rango completo de pictogramas, banderas, signos técnicos, selectores de
	variación, ZWJ de secuencias (familias) y los 'keycaps'."""
	if not texto:
		return texto
	limpio = _RE_EMOJI.sub("", texto)
	# Limpiar restos: espacios duplicados que deja un emoji entre palabras, y espacios
	# pegados a comas/puntos tras quitar el emoji.
	limpio = re.sub(r"\s{2,}", " ", limpio)
	limpio = re.sub(r"\s+([,.;:!?])", r"\1", limpio)
	return limpio.strip()


def quitar_imagenes_html(contenido: str) -> str:
	"""Sustituye cada <img> por su texto alternativo entre corchetes ("[Imagen: alt]")
	para poder mostrar HTML SIN pedir recursos remotos (privacidad: los píxeles de
	seguimiento de los feeds no se cargan; auditoría RSS-06). Si la imagen no trae
	alt, se deja un marcador genérico."""
	if not contenido or "<img" not in contenido.lower():
		return contenido or ""
	try:
		from bs4 import BeautifulSoup
		soup = BeautifulSoup(contenido, "html.parser")
		for img in soup.find_all("img"):
			alt = (img.get("alt") or "").strip()
			marcador = soup.new_tag("span")
			marcador.string = (f"[Imagen: {alt}]" if alt else "[Imagen]")
			img.replace_with(marcador)
		return str(soup)
	except Exception:
		import re as _re
		return _re.sub(r"(?is)<img\b[^>]*>", "[Imagen]", contenido)


def aplicar_emojis_personalizados(texto: str, emojis) -> str:
	"""Normaliza los emojis personalizados ':shortcode:' a su nombre legible.

	Mastodon deja los emojis de instancia como ':shortcode:' dentro del texto/nombre y entrega
	la lista en el campo 'emojis' de la entidad (Status o Account). Para un lector de pantalla,
	':blobcat:' se leería "dos puntos blobcat dos puntos"; aquí se deja solo 'blobcat'. Solo se
	tocan los shortcodes que la API declara como emoji, no cualquier texto entre dos puntos."""
	if not texto or not emojis:
		return texto
	for e in emojis:
		sc = e.get("shortcode") if isinstance(e, dict) else None
		if sc:
			texto = texto.replace(f":{sc}:", sc)
	return texto


def html_a_texto(html_content: str) -> str:
	"""
	Convierte HTML a texto plano preservando la estructura básica
	(párrafos, encabezados y listas). Devuelve cadena vacía si no hay contenido.
	"""
	if not html_content:
		return ""

	# Si el contenido no parece tener etiquetas, solo desescapamos entidades.
	if "<" not in html_content:
		return _limpiar_texto(html.unescape(html_content))

	try:
		from bs4 import BeautifulSoup, Tag, NavigableString, Comment

		try:
			soup = BeautifulSoup(html_content, "lxml")
		except Exception:
			soup = BeautifulSoup(html_content, "html.parser")

		# Eliminar elementos no visibles
		for tag in soup.find_all(["script", "style", "noscript", "head"]):
			tag.decompose()

		partes = []
		# Pila de listas anidadas: una entrada por <ul>/<ol> abierta, con su CONTADOR de
		# elementos. Numerar cada <li> con el contador de su lista es O(1); antes se
		# recalculaba la lista de hermanos y su .index() por CADA <li> (O(n²) en listas
		# largas de artículos RSS).
		pila_listas = []

		def recorrer(nodo):
			if isinstance(nodo, Comment):
				return

			if isinstance(nodo, NavigableString):
				txt = re.sub(r"[ \t]+", " ", str(nodo)).replace("\n", " ")
				if txt:
					partes.append(txt)
				return

			if not isinstance(nodo, Tag):
				return

			nombre = nodo.name.lower()

			# Marcadores ANTES del contenido
			if nombre in _ENCABEZADOS or nombre in _BLOQUE_DOBLE:
				partes.append("\n\n")
			elif nombre in _LISTAS:
				partes.append("\n")
				pila_listas.append({"tipo": nombre, "n": 0})
			elif nombre == "li":
				sangria = "  " * (len(pila_listas) - 1) if pila_listas else ""
				if pila_listas and pila_listas[-1]["tipo"] == "ol":
					pila_listas[-1]["n"] += 1
					partes.append("\n%s%d. " % (sangria, pila_listas[-1]["n"]))
				else:
					partes.append("\n%s* " % sangria)
			elif nombre == "br":
				partes.append("\n")
			elif nombre == "hr":
				partes.append("\n---\n")
			elif nombre in _BLOQUE_SIMPLE:
				partes.append("\n")

			for hijo in nodo.children:
				recorrer(hijo)

			# Marcadores DESPUÉS del contenido
			if nombre in _ENCABEZADOS or nombre in _BLOQUE_DOBLE:
				partes.append("\n")
			elif nombre in _LISTAS:
				if pila_listas:
					pila_listas.pop()
				partes.append("\n")

		recorrer(soup)
		return _limpiar_texto("".join(partes))

	except Exception as e:
		log.warning(f"Error al convertir HTML a texto estructurado: {e}")
		# Respaldo básico: eliminar etiquetas con expresión regular
		texto = re.sub(r"<[^>]+>", " ", html_content)
		return _limpiar_texto(html.unescape(texto))


def html_a_documento(titulo: str, html_content: str, fuente: str = "", enlace: str = "") -> str:
	"""
	Genera un documento HTML autónomo, accesible y con buen contraste para
	visualizar el contenido de un artículo o toot en el navegador.

	El contenido se inserta tal cual (ya es HTML), por lo que conserva enlaces,
	listas, imágenes y formato original.
	"""
	titulo_seguro = html.escape(titulo or "")
	fuente_segura = html.escape(fuente or "")

	cabecera_enlace = ""
	if enlace:
		enlace_seguro = html.escape(enlace)
		cabecera_enlace = f'<p><a href="{enlace_seguro}">{enlace_seguro}</a></p>'

	# El contenido llega de fuentes NO confiables: SIEMPRE se sanea con lista
	# blanca (sin scripts, on*, object/embed/iframe ni estilos).
	cuerpo = sanear_html(html_content) or "<p>(Sin contenido)</p>"

	# Privacidad: si está activado el bloqueo de rastreadores, quitar además las
	# imágenes remotas (posibles píxeles de seguimiento).
	try:
		from app.core.config_manager import config
		bloquear = config.get("privacy", "block_trackers", True)
	except Exception:
		bloquear = True
	if bloquear and cuerpo:
		cuerpo = re.sub(r"<img\b[^>]*>", "", cuerpo, flags=re.IGNORECASE)

	# CSP restrictiva de refuerzo: sin scripts ni marcos en ningún caso; las
	# imágenes solo si el bloqueo de rastreadores está desactivado. El estilo
	# 'unsafe-inline' es el <style> propio de esta plantilla.
	csp_img = "'none'" if bloquear else "https: data:"
	csp = ("default-src 'none'; style-src 'unsafe-inline'; "
		   f"img-src {csp_img}; frame-src 'none'; script-src 'none'")

	return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo_seguro}</title>
<style>
	body {{
		font-family: Segoe UI, Arial, sans-serif;
		font-size: 18px;
		line-height: 1.6;
		max-width: 800px;
		margin: 0 auto;
		padding: 1.5em;
		background: #ffffff;
		color: #1a1a1a;
	}}
	h1 {{ font-size: 1.6em; }}
	a {{ color: #0b5fff; }}
	img {{ max-width: 100%; height: auto; }}
	header {{ border-bottom: 1px solid #ccc; margin-bottom: 1em; padding-bottom: 0.5em; }}
	.fuente {{ color: #555; font-size: 0.9em; }}
</style>
</head>
<body>
<header>
	<h1>{titulo_seguro}</h1>
	<p class="fuente">{fuente_segura}</p>
	{cabecera_enlace}
</header>
<main>
{cuerpo}
</main>
</body>
</html>
"""
