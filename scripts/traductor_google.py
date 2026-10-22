# -*- coding: utf-8 -*-
"""
Motor de traducción usando la API gratuita de Google Translate.
Adaptado del traductor de Héctor J. Benítez Corredera.

Reglas de traducción profesional para archivos .po:
- Preserva todas las variables: {}, %s, %d, %(name)s, %1, etc.
- Preserva secuencias de escape: \\n, \\t, \\r.
- Preserva atajos de teclado tras tabulador: \\tCtrl+Shift+F5.
- Reubica aceleradores de menú (&X) en la palabra equivalente
  del idioma destino en lugar de mantenerlos literalmente.
- Preserva nombres técnicos, rutas y extensiones.
"""

import re
import ssl
import json
import urllib.request
from urllib.parse import quote
from random import choice

ssl._create_default_https_context = ssl._create_unverified_context

ENDPOINTS = [
	"https://translate.googleapis.com/translate_a/single?client=gtx&sl={origen}&tl={destino}&dt=t&q={texto}&dj=1",
	"https://translate.google.com/translate_a/single?client=gtx&sl={origen}&tl={destino}&dt=t&q={texto}&dj=1",
	"https://translate.google.es/translate_a/single?client=gtx&sl={origen}&tl={destino}&dt=t&q={texto}&dj=1",
]

PATRON_DIVISION = re.compile(r'[،؛؟]|[　-〿︐-︟︰-﹯！-｠]|[.,!?;:]')

# Patrones inmutables (variables y escape) — SIN aceleradores &
PATRON_VARIABLES = re.compile(
	r'(\{[^}]*\})'                             # {variable}, {0}, {nombre}
	r'|(%(?:\([^)]+\))?[sdifFeEgGxXocrba%])'   # %s, %d, %(name)s
	r'|(%\d+)'                                  # %1, %2
	r'|(\\n|\\t|\\r)'                            # Secuencias de escape
)

# Patrón para detectar aceleradores de menú: & seguido de una letra
PATRON_ACELERADOR = re.compile(r'&([A-Za-zÀ-ÿ\u0400-\u04FF])')

# Patrón para atajos de teclado tras tabulador: \tCtrl+X, \tAlt+F4, etc.
PATRON_ATAJO_TECLADO = re.compile(r'(\t.+)$')

IDIOMAS = {
	'es': 'Español', 'en': 'Inglés', 'fr': 'Francés',
	'de': 'Alemán', 'it': 'Italiano', 'pt': 'Portugués',
	'ru': 'Ruso', 'zh-CN': 'Chino simplificado', 'ja': 'Japonés',
	'ko': 'Coreano', 'ar': 'Árabe', 'hi': 'Hindi',
	'nl': 'Neerlandés', 'pl': 'Polaco', 'tr': 'Turco',
	'vi': 'Vietnamita', 'cs': 'Checo', 'sv': 'Sueco',
	'da': 'Danés', 'fi': 'Finés', 'el': 'Griego',
	'he': 'Hebreo', 'hu': 'Húngaro', 'no': 'Noruego',
	'ro': 'Rumano', 'sk': 'Eslovaco', 'uk': 'Ucraniano',
	'bg': 'Búlgaro', 'ca': 'Catalán', 'hr': 'Croata',
	'lt': 'Lituano', 'lv': 'Letón', 'sl': 'Esloveno',
	'et': 'Estonio',
}


def _dividir_texto(texto, tamano_max=3000):
	if len(texto) <= tamano_max:
		return [texto]
	fragmentos = []
	posicion = 0
	posicion_potencial = 0
	for match in PATRON_DIVISION.finditer(texto):
		if (match.start() - posicion + 1) < tamano_max:
			posicion_potencial = match.start()
		else:
			fragmentos.append(texto[posicion:posicion_potencial + 1])
			posicion = posicion_potencial + 1
			posicion_potencial = match.start()
	fragmentos.append(texto[posicion:])
	return fragmentos


# ── Gestión de aceleradores de menú ─────────────────────────────────

def _extraer_aceleradores(texto):
	"""
	Extrae los aceleradores de menú (&X) y devuelve:
	- El texto sin los &
	- Una lista de (indice_palabra, letra_original) para cada acelerador

	Ejemplo:
	  '&Archivo' → ('Archivo', [(0, 'A')])
	  'Guardar &como...' → ('Guardar como...', [(1, 'c')])
	"""
	# Encontrar aceleradores y en qué palabra están
	info_aceleradores = []
	palabras = texto.split()
	for i, palabra in enumerate(palabras):
		for m in PATRON_ACELERADOR.finditer(palabra):
			info_aceleradores.append((i, m.group(1)))

	# Eliminar todos los & de acelerador
	texto_limpio = PATRON_ACELERADOR.sub(r'\1', texto)
	return texto_limpio, info_aceleradores


def _reubicar_aceleradores(texto_traducido, info_aceleradores):
	"""
	Reubica los aceleradores de menú en el texto traducido.

	Estrategia por orden de prioridad para cada acelerador:
	1. Si la misma letra (insensible a mayúsculas) existe en la
	   palabra de la misma posición → usarla.
	2. Si no, usar la primera letra de la palabra en la misma posición.
	3. Si la posición no existe, usar la primera letra de la última palabra.

	:param texto_traducido: Texto ya traducido sin aceleradores.
	:param info_aceleradores: Lista de (indice_palabra, letra_original).
	:return: Texto con los & reubicados.
	"""
	if not info_aceleradores:
		return texto_traducido

	# Limpiar cualquier & espurio que Google haya dejado
	texto_limpio = PATRON_ACELERADOR.sub(r'\1', texto_traducido)

	palabras = texto_limpio.split()
	if not palabras:
		return texto_traducido

	usados = set()  # Índices de palabras que ya tienen acelerador

	for idx_palabra, letra_orig in info_aceleradores:
		# Determinar palabra destino
		target = min(idx_palabra, len(palabras) - 1)

		# Si ya se usó esa palabra, buscar la siguiente disponible
		if target in usados:
			encontrado = False
			for alt in range(len(palabras)):
				if alt not in usados:
					target = alt
					encontrado = True
					break
			if not encontrado:
				continue  # No quedan palabras disponibles

		palabra = palabras[target]

		# Intentar reubicar el &:
		# Prioridad 1: Misma letra (insensible) en la misma palabra
		pos_misma = -1
		for j, c in enumerate(palabra):
			if c.lower() == letra_orig.lower() and c.isalpha():
				pos_misma = j
				break

		if pos_misma >= 0:
			palabras[target] = (
				palabra[:pos_misma] + '&' + palabra[pos_misma:]
			)
		else:
			# Prioridad 2: Primera letra alfabética de la palabra
			for j, c in enumerate(palabra):
				if c.isalpha():
					palabras[target] = (
						palabra[:j] + '&' + palabra[j:]
					)
					break

		usados.add(target)

	return ' '.join(palabras)


# ── Función principal de traducción ─────────────────────────────────

def traducir(texto, idioma_origen='es', idioma_destino='en'):
	"""
	Traduce texto para archivos .po respetando todas las convenciones:
	- Variables y placeholders se preservan intactos.
	- Atajos de teclado (\\tCtrl+X) se preservan intactos.
	- Aceleradores de menú (&X) se reubican en el idioma destino.
	- Secuencias de escape se preservan.

	:param texto: Texto a traducir.
	:param idioma_origen: Código ISO del idioma origen.
	:param idioma_destino: Código ISO del idioma destino.
	:return: Texto traducido o None si falla.
	"""
	if not texto or not texto.strip():
		return texto

	# 0. Separar atajo de teclado tras tabulador (\tCtrl+X)
	#    No se traduce nunca.
	atajo_final = ""
	m_atajo = PATRON_ATAJO_TECLADO.search(texto)
	if m_atajo:
		atajo_final = m_atajo.group(1)
		texto = texto[:m_atajo.start()]

	# 1. Extraer aceleradores de menú (&X)
	texto_sin_accel, info_aceleradores = _extraer_aceleradores(texto)

	# 2. Proteger variables reemplazándolas con tokens
	tokens = {}
	contador = [0]

	def reemplazar_variable(match):
		token = f"__VAR{contador[0]}__"
		tokens[token] = match.group(0)
		contador[0] += 1
		return token

	texto_protegido = PATRON_VARIABLES.sub(
		reemplazar_variable, texto_sin_accel
	)

	# 3. Traducir preservando saltos de línea
	lineas = texto_protegido.split('\n')
	lineas_traducidas = []
	for linea in lineas:
		if not linea.strip():
			lineas_traducidas.append(linea)
			continue
		traducida = _traducir_texto(linea, idioma_origen, idioma_destino)
		if traducida is None:
			return None
		lineas_traducidas.append(traducida)

	resultado = '\n'.join(lineas_traducidas)

	# 4. Restaurar variables
	for token, original in tokens.items():
		resultado = resultado.replace(token, original)

	# 5. Reubicar aceleradores en la traducción
	resultado = _reubicar_aceleradores(resultado, info_aceleradores)

	# 6. Re-adjuntar atajo de teclado
	resultado += atajo_final

	return resultado


def _traducir_chunk(chunk, idioma_origen, idioma_destino):
	"""Traduce UN fragmento probando los distintos endpoints de Google hasta que uno responda.
	Devuelve el texto traducido o None si todos fallan (más resiliente que usar uno solo)."""
	opener = urllib.request.build_opener()
	opener.addheaders = [('User-agent', 'Mozilla/5.0')]
	texto_cod = quote(chunk.encode('utf-8', 'surrogatepass'))
	orden = list(ENDPOINTS)
	primero = choice(orden)
	orden.remove(primero)
	for endpoint in [primero] + orden:
		try:
			url = endpoint.format(origen=idioma_origen, destino=idioma_destino, texto=texto_cod)
			respuesta = json.load(opener.open(url, timeout=30))
			if 'sentences' in respuesta:
				return ''.join(s.get('trans', '') for s in respuesta['sentences'])
		except Exception:
			continue
	return None


def _traducir_texto(texto, idioma_origen, idioma_destino):
	"""Traduce un texto (dividiéndolo si es largo) con la API de Google, reintentando endpoints."""
	resultado = ""
	for chunk in _dividir_texto(texto, 3000):
		traducido = _traducir_chunk(chunk, idioma_origen, idioma_destino)
		if traducido is None:
			return None
		resultado += traducido
	return resultado.strip()
