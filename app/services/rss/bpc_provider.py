# -*- coding: utf-8 -*-
"""
Proveedor modular de extracción de artículos para medios compatibles con Bypass Paywalls Clean.

Implementa las estrategias compatibles de BPC en Python puro sin ejecutar código remoto
inseguro: redirección a versiones AMP oficiales, simulación de crawler de indexación
(Googlebot) en medios con reglas de User-Agent, desofuscación DOM y retirada de capas
de bloqueo en HTML servido completo, y extracción de datos estructurados JSON-LD.
Garantiza aislamiento de cookies por medio, prevención de SSRF y detección de entradillas.
"""

import json
import logging
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.core.net import _ip_privada
from .bpc_rules import BPCRulesEngine, normalizar_dominio
from .rss_fulltext import ArticuloExtraido, FullTextExtractor, MIN_CARACTERES_ARTICULO

log = logging.getLogger(__name__)

USER_AGENT_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
REFERER_GOOGLE = "https://www.google.com/"

CABECERAS_BASE = {
	"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
	"Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
	"Upgrade-Insecure-Requests": "1",
	"Sec-Fetch-Dest": "document",
	"Sec-Fetch-Mode": "navigate",
	"Sec-Fetch-Site": "cross-site",
}

# Indicadores típicos de muro de pago o entradilla recortada
FRASES_MURO = (
	"suscríbete para seguir leyendo",
	"contenido exclusivo para suscriptores",
	"artículo reservado para suscriptores",
	"para seguir leyendo esta noticia",
	"inicia sesión o suscríbete",
	"hazte suscriptor para leer",
	"sólo para suscriptores",
	"solo para suscriptores",
	"este contenido es exclusivo para socios",
	"subscribe to continue reading",
	"you have reached your free article limit",
)


def _es_url_segura(url: str) -> bool:
	"""Valida que la URL utilice http/https y no apunte a direcciones locales o privadas."""
	if not url:
		return False
	try:
		p = urlparse(url)
		if p.scheme not in ("http", "https"):
			return False
		if not p.hostname or _ip_privada(p.hostname):
			return False
		return True
	except Exception:
		return False


def _descargar_aislado(url: str, user_agent: Optional[str] = None, referer: Optional[str] = None,
					   timeout: float = 15.0) -> Tuple[bytes, str, str]:
	"""Descarga una página web utilizando una sesión HTTP aislada por petición."""
	if not _es_url_segura(url):
		raise ValueError(f"URL rechazada por seguridad: {url}")

	import requests
	sesion = requests.Session()
	cabeceras = dict(CABECERAS_BASE)
	cabeceras["User-Agent"] = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
	if referer:
		cabeceras["Referer"] = referer

	try:
		resp = sesion.get(url, headers=cabeceras, timeout=(8, timeout), stream=True, allow_redirects=True)
		if resp.status_code != 200:
			raise ValueError(f"Respuesta HTTP {resp.status_code}")

		tipo = (resp.headers.get("Content-Type") or "").lower()
		if tipo and "html" not in tipo and "xml" not in tipo:
			raise ValueError(f"Tipo de contenido no compatible: {tipo}")

		max_bytes = 8 * 1024 * 1024
		trozos = []
		total = 0
		for trozo in resp.iter_content(chunk_size=65536):
			total += len(trozo)
			if total > max_bytes:
				raise ValueError("El tamaño de la página excede el límite permitido.")
			trozos.append(trozo)

		charset = ""
		m = re.search(r"charset=([\w\-]+)", tipo)
		if m:
			charset = m.group(1)

		return b"".join(trozos), charset, resp.url or url
	finally:
		sesion.close()


class BPCProvider:
	"""Proveedor de extracción asistida por reglas BPC."""

	@classmethod
	def es_entradilla_o_muro(cls, texto: str) -> bool:
		"""Comprueba si el texto recuperado es solo una entradilla o mensaje de suscripción."""
		if not texto or len(texto.strip()) < MIN_CARACTERES_ARTICULO:
			return True
		texto_bajo = texto.lower()
		for frase in FRASES_MURO:
			if frase in texto_bajo:
				# Si la frase aparece al final o en un texto corto, es corte por muro
				if len(texto) < 1200 or texto_bajo.rfind(frase) > len(texto) * 0.7:
					return True
		return False

	@classmethod
	def intentar_estrategia_amp(cls, url: str, titulo: str = "") -> Optional[ArticuloExtraido]:
		"""Intenta recuperar el artículo desde su versión AMP oficial."""
		motor = BPCRulesEngine.obtener()
		url_amp = motor.obtener_url_amp(url)
		if not url_amp:
			return None

		log.debug(f"Probando estrategia AMP en: {url_amp}")
		try:
			datos, charset, url_final = _descargar_aislado(url_amp)
			html_texto = FullTextExtractor._decodificar(datos, charset)
			# Desocultar secciones AMP
			html_desocultado = cls._desocultar_secciones_amp(html_texto)
			res = FullTextExtractor.extraer_de_html(html_desocultado, url=url_final, titulo=titulo)
			if res and not cls.es_entradilla_o_muro(res.texto):
				res.metodo = "bpc_amp"
				res.url_final = url_final
				return res
		except Exception as e:
			log.debug(f"Fallo en estrategia AMP para {url}: {e}")

		return None

	@classmethod
	def _desocultar_secciones_amp(cls, html_texto: str) -> str:
		"""Retira atributos de bloqueo amp-access en el HTML de la página AMP."""
		if not html_texto or "amp-access" not in html_texto:
			return html_texto
		try:
			soup = BeautifulSoup(html_texto, "lxml")
			# Elementos con amp-access="authorized" o similar
			for tag in soup.find_all(attrs={"amp-access": True}):
				val = str(tag.get("amp-access", ""))
				if "NOT" in val or "no-access" in val:
					tag.decompose()
				else:
					if tag.has_attr("amp-access-hide"):
						del tag.attrs["amp-access-hide"]
			cuerpo = soup.body or soup
			return "".join(str(c) for c in cuerpo.contents)
		except Exception:
			return html_texto

	@classmethod
	def intentar_estrategia_crawler(cls, url: str, titulo: str = "") -> Optional[ArticuloExtraido]:
		"""Intenta descargar presentándose como Googlebot con Referer de Google Search."""
		log.debug(f"Probando estrategia Crawler (Googlebot) en: {url}")
		try:
			datos, charset, url_final = _descargar_aislado(
				url, user_agent=USER_AGENT_GOOGLEBOT, referer=REFERER_GOOGLE
			)
			html_texto = FullTextExtractor._decodificar(datos, charset)
			res = FullTextExtractor.extraer_de_html(html_texto, url=url_final, titulo=titulo)
			if res and not cls.es_entradilla_o_muro(res.texto):
				res.metodo = "bpc_crawler"
				res.url_final = url_final
				return res
		except Exception as e:
			log.debug(f"Fallo en estrategia Crawler para {url}: {e}")

		return None

	@classmethod
	def intentar_estrategia_desofuscacion_dom(cls, url: str, titulo: str = "") -> Optional[ArticuloExtraido]:
		"""Retira capas de muro y desoculta párrafos protegidos con CSS/HTML inline."""
		log.debug(f"Probando estrategia de desofuscación DOM en: {url}")
		try:
			datos, charset, url_final = _descargar_aislado(url)
			html_texto = FullTextExtractor._decodificar(datos, charset)
			soup = BeautifulSoup(html_texto, "lxml")

			# 1. Retirar capas conocidas de muro de pago
			selectores_muro = [
				"div.voc-paywall", "div.container-wall-exclusive", "ev-engagement",
				"div.ev-em-modal", "div.full-suscriptor-container", "div.newsType__content--closed",
				"p.ft-helper-closenews", "div.modal-paywall", "div.piano-paywall"
			]
			for sel in selectores_muro:
				for elem in soup.select(sel):
					elem.decompose()

			# 2. Desocultar elementos ocultos
			for oculto in soup.select("article div[hidden], div.paywall, [hidden]"):
				if oculto.has_attr("hidden"):
					del oculto.attrs["hidden"]
				if oculto.has_attr("style"):
					estilo = oculto["style"]
					if "display:none" in estilo or "display: none" in estilo:
						del oculto.attrs["style"]

			# 3. Quitar clases de bloqueo
			for el in soup.find_all(class_=re.compile(r"(content-not-granted-paywall|newsType__content--closed)")):
				clases = [c for c in el.get("class", []) if "paywall" not in c and "closed" not in c]
				el["class"] = clases

			cuerpo = soup.body or soup
			html_limpio = "".join(str(c) for c in cuerpo.contents)
			res = FullTextExtractor.extraer_de_html(html_limpio, url=url_final, titulo=titulo)
			if res and not cls.es_entradilla_o_muro(res.texto):
				res.metodo = "bpc_dom"
				res.url_final = url_final
				return res
		except Exception as e:
			log.debug(f"Fallo en estrategia de desofuscación DOM para {url}: {e}")

		return None

	@classmethod
	def intentar_extraccion_bpc(cls, url: str, titulo: str = "") -> Optional[ArticuloExtraido]:
		"""Aplica de forma secuencial y modular las estrategias BPC compatibles para la URL."""
		motor = BPCRulesEngine.obtener()
		dom = normalizar_dominio(url)

		# Comprobar si el medio es estrictamente nofix
		if motor.es_sitio_sin_arreglo(dom):
			log.info(f"El dominio {dom} está catalogado como sitio sin solución (nofix). Se requiere navegador.")
			return None

		estrategias = motor.obtener_estrategias(url)
		log.debug(f"Estrategias disponibles para {dom}: {estrategias}")

		# 1. Estrategia AMP
		if "amp" in estrategias:
			res = cls.intentar_estrategia_amp(url, titulo)
			if res:
				return res

		# 2. Estrategia Crawler (Googlebot)
		if "bot_useragent" in estrategias:
			res = cls.intentar_estrategia_crawler(url, titulo)
			if res:
				return res

		# 3. Estrategia de desofuscación DOM
		if "dom_unhide" in estrategias:
			res = cls.intentar_estrategia_desofuscacion_dom(url, titulo)
			if res:
				return res

		# Si ninguna estrategia específica tuvo éxito pero el medio está soportado en BPC,
		# probar el crawler de Googlebot como recurso habitual de indexación de prensa
		regla = motor.buscar_regla(dom)
		if regla and "bot_useragent" not in estrategias:
			res = cls.intentar_estrategia_crawler(url, titulo)
			if res:
				return res

		return None
