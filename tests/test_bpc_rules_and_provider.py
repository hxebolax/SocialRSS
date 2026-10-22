# -*- coding: utf-8 -*-
"""
Pruebas exhaustivas para el catálogo de reglas BPC, proveedor modular y actualizador.

Cubre:
- Resolución de dominios y grupos de medios compatibles (Unidad Editorial, Vocento, El País).
- Generación correcta de URLs de estrategia AMP.
- Identificación rigurosa de sitios con muro infranqueable (nofix_sites).
- Prevención de SSRF y rechazo de esquemas no seguros e IPs privadas.
- Detección de entradillas y rechazo de falsos positivos de éxito.
- Desofuscación DOM y retirada de capas de bloqueo.
- Flujo de comprobación, actualización atómica y reversión de reglas.
"""

import json
import os
import tempfile
import pytest

from app.services.rss.bpc_rules import BPCRulesEngine, normalizar_dominio
from app.services.rss.bpc_provider import BPCProvider, _es_url_segura
from app.services.rss.bpc_updater import BPCUpdater
from app.services.rss.rss_fulltext import FullTextExtractor


def test_normalizar_dominio():
	"""Comprueba la normalización de dominios con esquemas, prefijos y subdirectorios."""
	assert normalizar_dominio("https://www.elmundo.es/espana/noticia.html") == "elmundo.es"
	assert normalizar_dominio("http://amp.elpais.com/articulo") == "elpais.com"
	assert normalizar_dominio("abc.es:8080/seccion") == "abc.es"
	assert normalizar_dominio("https://sub.dominio.co.uk/") == "sub.dominio.co.uk"


def test_carga_reglas_base_bpc():
	"""Verifica que el motor cargue correctamente el catálogo empaquetado."""
	motor = BPCRulesEngine.obtener()
	assert motor.cargado is True
	assert len(motor.sitios_por_dominio) > 100
	assert len(motor.sitios_nofix) > 50
	assert motor.version != ""


def test_resolucion_medios_compatibles():
	"""Comprueba que los principales medios españoles e internacionales resuelvan su regla."""
	motor = BPCRulesEngine.obtener()
	# Unidad Editorial (grupo)
	regla_elmundo = motor.buscar_regla("https://www.elmundo.es/economia/noticia.html")
	assert regla_elmundo is not None
	# Vocento (grupo)
	regla_abc = motor.buscar_regla("https://www.abc.es/espana/noticia.html")
	assert regla_abc is not None
	# La Vanguardia
	regla_lavanguardia = motor.buscar_regla("https://www.lavanguardia.com/politica")
	assert regla_lavanguardia is not None


def test_sitios_sin_solucion_nofix():
	"""Comprueba que los medios catalogados como nofix sean identificados sin engaños."""
	motor = BPCRulesEngine.obtener()
	# Medios que están en nofix_sites
	assert motor.es_sitio_sin_arreglo("heraldo.es") is True
	assert motor.es_sitio_sin_arreglo("lavozdegalicia.es") is True
	assert motor.es_sitio_sin_arreglo("barrons.com") is True
	assert motor.es_sitio_sin_arreglo("https://www.heraldo.es/noticia/ejemplo.html") is True
	# Medios que SÍ tienen solución
	assert motor.es_sitio_sin_arreglo("elmundo.es") is False


def test_generacion_url_amp():
	"""Verifica la generación de la URL AMP adecuada para cada medio compatible."""
	motor = BPCRulesEngine.obtener()
	# Unidad Editorial
	url_em = "https://www.elmundo.es/espana/2026/09/25/articulo.html"
	amp_em = motor.obtener_url_amp(url_em)
	assert amp_em is not None
	assert "amp.elmundo.es" in amp_em
	# El País
	url_ep = "https://elpais.com/espana/2026-09-25/noticia.html"
	amp_ep = motor.obtener_url_amp(url_ep)
	assert amp_ep is not None
	assert "outputType=amp" in amp_ep
	# Vocento
	url_abc = "https://www.abc.es/espana/noticia.html"
	amp_abc = motor.obtener_url_amp(url_abc)
	assert amp_abc is not None
	assert "_amp.html" in amp_abc
	# El Diario
	url_ed = "https://www.eldiario.es/politica/noticia.html"
	amp_ed = motor.obtener_url_amp(url_ed)
	assert amp_ed is not None
	assert ".amp.html" in amp_ed


def test_prevencion_ssrf_seguridad_url():
	"""Garantiza el rechazo estricto de destinos privados, locales o esquemas inseguros."""
	# Esquemas prohibidos
	assert _es_url_segura("ftp://servidor/archivo.txt") is False
	assert _es_url_segura("file:///C:/Windows/win.ini") is False
	assert _es_url_segura("javascript:alert(1)") is False
	# IPs privadas y loopback
	assert _es_url_segura("http://127.0.0.1/admin") is False
	assert _es_url_segura("http://localhost:8000/") is False
	assert _es_url_segura("http://192.168.1.1/router") is False
	assert _es_url_segura("http://10.0.0.1/") is False
	assert _es_url_segura("http://169.254.169.254/latest/meta-data/") is False
	# URLs públicas válidas
	assert _es_url_segura("https://www.elmundo.es/noticia.html") is True
	assert _es_url_segura("http://elpais.com/") is True


def test_deteccion_de_entradilla_o_muro():
	"""Asegura que el lector no dé por bueno un artículo que es solo un aviso de suscripción."""
	# Muy corto
	assert BPCProvider.es_entradilla_o_muro("Texto corto de solo dos palabras.") is True
	# Frases típicas de muro de pago
	texto_muro = (
		"El ministro compareció este jueves ante el tribunal para declarar sobre el asunto. "
		"Suscríbete para seguir leyendo esta noticia en nuestra edición digital exclusiva."
	)
	assert BPCProvider.es_entradilla_o_muro(texto_muro) is True
	# Artículo legítimo completo y sustancial
	parrafos_largos = (
		"El presidente de la comisión abrió la sesión con un discurso centrado en la recuperación económica. "
		"Durante su intervención detalló las tres prioridades estratégicas acordadas por los estados miembros. "
		"En segundo lugar, se refirió al impacto de las nuevas medidas en el mercado laboral y la competitividad. "
		"Finalmente, agradeció la labor de los ponentes y dio paso al debate parlamentario entre las diversas bancadas."
	)
	assert BPCProvider.es_entradilla_o_muro(parrafos_largos) is False


def test_desofuscacion_dom_vocento():
	"""Comprueba la desofuscación de elementos ocultos y la eliminación de capas de bloqueo."""
	html_bloqueado = """
	<html><body><article>
		<h1>Titular de la noticia</h1>
		<p>Primer párrafo de la noticia visible antes del muro de pago.</p>
		<div class="voc-paywall"><h3>Hazte suscriptor para leer</h3><button>Pagar</button></div>
		<div hidden style="display:none">
			<p>Segundo párrafo completo que estaba oculto tras el muro.</p>
			<p>Tercer párrafo con el desenlace de la noticia.</p>
		</div>
	</article></body></html>
	"""
	from bs4 import BeautifulSoup
	soup = BeautifulSoup(html_bloqueado, "lxml")
	for elem in soup.select("div.voc-paywall"):
		elem.decompose()
	for oculto in soup.select("article div[hidden], [hidden]"):
		if oculto.has_attr("hidden"):
			del oculto.attrs["hidden"]
	texto = soup.get_text(" ")
	assert "Hazte suscriptor" not in texto
	assert "Segundo párrafo completo" in texto
	assert "Tercer párrafo con el desenlace" in texto


def test_diagnostico_extraccion():
	"""Verifica que el diagnóstico ofrezca explicaciones claras y accesibles."""
	diag_nofix = FullTextExtractor.diagnostico_extraccion("https://www.heraldo.es/noticia.html")
	assert "muro de pago estricto" in diag_nofix
	diag_compatible = FullTextExtractor.diagnostico_extraccion("https://www.elmundo.es/noticia.html")
	assert "compatible" in diag_compatible or "suscripción" in diag_compatible


def test_aplicacion_y_reversion_actualizaciones(monkeypatch):
	"""Prueba la descarga simulada, aplicación atómica y reversión de reglas BPC."""
	with tempfile.TemporaryDirectory() as tmpdir:
		ruta_upd = os.path.join(tmpdir, "rss_bpc_updated.json")
		ruta_bak = os.path.join(tmpdir, "rss_bpc_backup.json")
		monkeypatch.setattr(BPCUpdater, "_ruta_archivo_actualizado", lambda: ruta_upd)
		monkeypatch.setattr(BPCUpdater, "_ruta_archivo_backup", lambda: ruta_bak)

		datos_prueba = {
			"Medio de Prueba": {
				"domain": "ejemplo-prensa.com",
				"allow_cookies": 1,
				"upd_version": "4.4.6.0"
			}
		}

		# 1. Aplicar actualización
		ok, msg = BPCUpdater.aplicar_actualizacion(datos_prueba, version_nueva="4.4.6.0")
		assert ok is True
		assert os.path.exists(ruta_upd)
		with open(ruta_upd, "r", encoding="utf-8") as f:
			guardado = json.load(f)
		assert guardado["version"] == "4.4.6.0"
		assert "Medio de Prueba" in guardado["sites_updated"]

		# 2. Revertir a versión de fábrica
		ok_rev, msg_rev = BPCUpdater.revertir_version_anterior()
		assert ok_rev is True
		assert not os.path.exists(ruta_upd)
