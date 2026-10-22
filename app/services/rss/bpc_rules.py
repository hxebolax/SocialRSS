# -*- coding: utf-8 -*-
"""
Motor y catálogo de reglas de Bypass Paywalls Clean (BPC) para el lector RSS.

Permite cargar, consultar y resolver las reglas de sitios compatibles, sitios sin
solución conocida (nofix_sites), agrupaciones de medios y estrategias de obtención
de texto completo (AMP, User-Agent de crawler, desofuscación DOM y JSON-LD).
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

log = logging.getLogger(__name__)


def normalizar_dominio(url_o_dominio: str) -> str:
	"""Extrae y normaliza el dominio base en minúsculas, sin www ni puerto."""
	if not url_o_dominio:
		return ""
	cadena = url_o_dominio.strip().lower()
	if "://" in cadena:
		try:
			parsed = urlparse(cadena)
			cadena = parsed.netloc or parsed.path
		except Exception:
			pass
	else:
		cadena = cadena.split("/")[0]
	if ":" in cadena:
		cadena = cadena.split(":")[0]
	if cadena.startswith("www."):
		cadena = cadena[4:]
	elif cadena.startswith("amp."):
		cadena = cadena[4:]
	return cadena.strip()


class BPCRulesEngine:
	"""Gestor en memoria de las reglas de Bypass Paywalls Clean."""

	_instancia = None

	def __init__(self):
		"""Inicializa las estructuras de datos de reglas."""
		self.version: str = "4.4.5.3"
		self.fecha_version: str = "2024-08-10"
		self.descripcion: str = "Reglas oficiales de Bypass Paywalls Clean"
		self.sitios_por_dominio: Dict[str, Dict[str, Any]] = {}
		self.sitios_nofix: Set[str] = set()
		self.grupos: Dict[str, List[str]] = {}
		self.sitios_actualizados: Dict[str, Dict[str, Any]] = {}
		self.cargado: bool = False
		self.cargar_reglas()

	@classmethod
	def obtener(cls) -> "BPCRulesEngine":
		"""Devuelve la instancia singleton del motor de reglas."""
		if cls._instancia is None:
			cls._instancia = BPCRulesEngine()
		return cls._instancia

	def _ruta_reglas_base(self) -> str:
		"""Ruta del archivo empaquetado de reglas base."""
		dir_actual = os.path.dirname(os.path.abspath(__file__))
		return os.path.join(dir_actual, "bpc_rules.json")

	def _ruta_reglas_actualizadas(self) -> str:
		"""Ruta del archivo de reglas actualizadas por el usuario o periódicamente."""
		try:
			from app.core.paths import get_data_path
			return str(get_data_path("rss_bpc_updated.json"))
		except Exception:
			dir_actual = os.path.dirname(os.path.abspath(__file__))
			return os.path.join(dir_actual, "bpc_rules_updated.json")

	def cargar_reglas(self) -> bool:
		"""Carga las reglas base y aplica las actualizaciones si existen."""
		self.sitios_por_dominio.clear()
		self.sitios_nofix.clear()
		self.grupos.clear()
		self.sitios_actualizados.clear()

		ruta_base = self._ruta_reglas_base()
		if not os.path.exists(ruta_base):
			log.warning(f"No se encontró el archivo de reglas base en: {ruta_base}")
			return False

		try:
			with open(ruta_base, "r", encoding="utf-8") as f:
				datos_base = json.load(f)
		except Exception as e:
			log.error(f"Error al leer reglas base BPC: {e}")
			return False

		self.version = datos_base.get("version", "4.4.5.3")
		self.fecha_version = datos_base.get("release_date", "2024-08-10")
		self.descripcion = datos_base.get("description", "")

		# Sitios sin arreglo (nofix_sites)
		for dom in datos_base.get("nofix_sites", []):
			self.sitios_nofix.add(normalizar_dominio(dom))

		# Grupos
		self.grupos = datos_base.get("grouped_sites", {})

		# Sitios por defecto
		def_sites = datos_base.get("default_sites", {})
		for titulo, regla in def_sites.items():
			if not isinstance(regla, dict):
				continue
			dom = regla.get("domain", "")
			if not dom or dom.startswith("###") or dom.startswith("#options_"):
				# Verificar si es un grupo
				grupo_dominios = regla.get("group", [])
				for d in grupo_dominios:
					d_norm = normalizar_dominio(d)
					if d_norm:
						regla_copia = dict(regla)
						regla_copia["titulo_grupo"] = titulo
						regla_copia["dominio_propio"] = d_norm
						self.sitios_por_dominio[d_norm] = regla_copia
				continue

			dom_norm = normalizar_dominio(dom)
			if dom_norm:
				regla_copia = dict(regla)
				regla_copia["titulo_sitio"] = titulo
				regla_copia["dominio_propio"] = dom_norm
				self.sitios_por_dominio[dom_norm] = regla_copia

		# Cargar reglas actualizadas suplementarias
		ruta_upd = self._ruta_reglas_actualizadas()
		if os.path.exists(ruta_upd):
			try:
				with open(ruta_upd, "r", encoding="utf-8") as f:
					datos_upd = json.load(f)
				if isinstance(datos_upd, dict):
					sitios_upd = datos_upd.get("sites_updated", datos_upd)
					self._aplicar_reglas_actualizadas(sitios_upd)
					if "version" in datos_upd:
						self.version = datos_upd["version"]
					if "release_date" in datos_upd:
						self.fecha_version = datos_upd["release_date"]
			except Exception as e:
				log.error(f"Error al cargar reglas actualizadas BPC: {e}")

		self.cargado = True
		log.info(f"Reglas BPC cargadas: {len(self.sitios_por_dominio)} dominios, {len(self.sitios_nofix)} nofix. Versión {self.version}")
		return True

	def _aplicar_reglas_actualizadas(self, sitios_upd: Dict[str, Any]):
		"""Combina las reglas actualizadas sobre las reglas existentes."""
		if not isinstance(sitios_upd, dict):
			return
		self.sitios_actualizados = sitios_upd
		for titulo, regla in sitios_upd.items():
			if not isinstance(regla, dict):
				continue
			dom = regla.get("domain", "")
			grupo = regla.get("group", [])
			if grupo:
				for d in grupo:
					d_norm = normalizar_dominio(d)
					if d_norm:
						if d_norm in self.sitios_por_dominio:
							self.sitios_por_dominio[d_norm].update(regla)
						else:
							regla_nueva = dict(regla)
							regla_nueva["titulo_grupo"] = titulo
							regla_nueva["dominio_propio"] = d_norm
							self.sitios_por_dominio[d_norm] = regla_nueva
			elif dom and not dom.startswith("###"):
				dom_norm = normalizar_dominio(dom)
				if dom_norm:
					if dom_norm in self.sitios_por_dominio:
						self.sitios_por_dominio[dom_norm].update(regla)
					else:
						regla_nueva = dict(regla)
						regla_nueva["titulo_sitio"] = titulo
						regla_nueva["dominio_propio"] = dom_norm
						self.sitios_por_dominio[dom_norm] = regla_nueva

	def es_sitio_sin_arreglo(self, url_o_dominio: str) -> bool:
		"""Devuelve True si el medio tiene un muro infranqueable según BPC (nofix)."""
		dom = normalizar_dominio(url_o_dominio)
		if dom in self.sitios_nofix:
			return True
		# Comprobar subdominios
		partes = dom.split(".")
		if len(partes) > 2:
			raiz = ".".join(partes[-2:])
			if raiz in self.sitios_nofix:
				return True
		return False

	def buscar_regla(self, url_o_dominio: str) -> Optional[Dict[str, Any]]:
		"""Busca la regla aplicable a un dominio o URL."""
		dom = normalizar_dominio(url_o_dominio)
		if not dom:
			return None
		if dom in self.sitios_por_dominio:
			return self.sitios_por_dominio[dom]
		# Comprobar si coincide con un dominio raíz (ej: blogs.elpais.com -> elpais.com)
		partes = dom.split(".")
		for i in range(1, len(partes) - 1):
			candidato = ".".join(partes[i:])
			if candidato in self.sitios_por_dominio:
				return self.sitios_por_dominio[candidato]
		return None

	def obtener_url_amp(self, url: str) -> Optional[str]:
		"""Genera la URL de la versión AMP si el medio dispone de ella."""
		if not url or not url.startswith("http"):
			return None
		dom = normalizar_dominio(url)
		regla = self.buscar_regla(dom)

		# Medios específicos de España y grupos conocidos:
		# Unidad Editorial (El Mundo, Expansión, Marca)
		if dom in ("elmundo.es", "expansion.com", "marca.com"):
			if "amp." in url:
				return url
			return url.replace("://www.", "://amp.").replace(f"://{dom}", f"://amp.{dom}")

		# El País
		if dom == "elpais.com":
			if "outputType=amp" in url:
				return url
			parsed = urlparse(url)
			qs = parse_qsl(parsed.query)
			qs.append(("outputType", "amp"))
			return urlunparse(parsed._replace(query=urlencode(qs)))

		# El Diario, InfoLibre, Ara
		if dom in ("eldiario.es", "infolibre.es", "ara.cat", "arabalears.cat"):
			if url.endswith(".amp.html"):
				return url
			if ".html" in url:
				return url.replace(".html", ".amp.html")
			return url.rstrip("/") + ".amp.html"

		# Grupo Vocento (ABC, El Correo, El Diario Vasco, etc.)
		vocento_doms = (
			"abc.es", "canarias7.es", "diariosur.es", "diariovasco.com",
			"elcomercio.es", "elcorreo.com", "eldiariomontanes.es",
			"elnortedecastilla.es", "hoy.es", "ideal.es", "larioja.com",
			"lasprovincias.es", "laverdad.es", "lavozdigital.es"
		)
		if dom in vocento_doms:
			if "_amp.html" in url:
				return url
			if ".html" in url:
				return url.replace(".html", "_amp.html")

		# Regla genérica amp_redirect en BPC
		if regla and "amp_redirect" in regla:
			if ".amp.html" not in url and not url.startswith("amp."):
				if ".html" in url:
					return url.replace(".html", ".amp.html")
				return url.rstrip("/") + ".amp.html"

		return None

	def obtener_estrategias(self, url: str) -> List[str]:
		"""Determina la lista priorizada de estrategias viables para la URL."""
		estrategias: List[str] = []
		dom = normalizar_dominio(url)

		if self.es_sitio_sin_arreglo(dom):
			# Sitio con muro infranqueable sin navegador
			return []

		regla = self.buscar_regla(dom)

		# 1. Estrategia AMP si existe
		url_amp = self.obtener_url_amp(url)
		if url_amp and url_amp != url:
			estrategias.append("amp")

		# 2. Estrategia de User-Agent de crawler (Googlebot / Bingbot)
		if regla and regla.get("useragent") in ("googlebot", "bingbot"):
			estrategias.append("bot_useragent")

		# 3. Estrategia de desofuscación DOM (Vocento, El Español, El Confidencial)
		vocento_y_similares = (
			"abc.es", "canarias7.es", "diariosur.es", "diariovasco.com",
			"elcomercio.es", "elcorreo.com", "eldiariomontanes.es",
			"elnortedecastilla.es", "hoy.es", "ideal.es", "larioja.com",
			"lasprovincias.es", "laverdad.es", "lavozdigital.es",
			"elconfidencial.com", "elespanol.com"
		)
		if dom in vocento_y_similares:
			estrategias.append("dom_unhide")

		# 4. Datos estructurados JSON-LD siempre como complemento
		estrategias.append("jsonld")

		# 5. Archivo web si está marcado
		if regla and "ld_archive_is" in regla:
			estrategias.append("archive")

		return estrategias
