# -*- coding: utf-8 -*-
"""
Compilador de reglas de Bypass Paywalls Clean para el lector RSS.

Lee los archivos sites.js y sites_updated.json del repositorio clonado
o extraído de BPC y genera el archivo estructurado bpc_rules.json listo
para su uso inmediato y seguro por el lector en Python sin ejecutar JS remoto.
"""

import json
import os
import re
import subprocess
import sys


def compilar_con_node(ruta_sites_js: str, ruta_sites_updated: str, ruta_salida: str) -> bool:
	"""Compila sites.js usando Node.js si está instalado en el sistema."""
	script_node = f"""
const fs = require('fs');
const content = fs.readFileSync('{ruta_sites_js.replace('\\\\', '/')}', 'utf8');
const vm = require('vm');
const sandbox = {{
	chrome: {{ runtime: {{ getManifest: () => ({{ permissions: [] }}) }} }},
	browser: {{ runtime: {{ getManifest: () => ({{ permissions: [] }}) }} }}
}};
vm.createContext(sandbox);
vm.runInContext(content, sandbox);

function cleanRule(obj) {{
	if (!obj || typeof obj !== 'object') return obj;
	if (Array.isArray(obj)) return obj.map(cleanRule);
	const res = {{}};
	for (const [k, v] of Object.entries(obj)) {{
		if (v instanceof RegExp) {{
			res[k] = v.source;
			res[k + '_flags'] = v.flags;
		}} else if (typeof v === 'object' && v !== null) {{
			res[k] = cleanRule(v);
		}} else {{
			res[k] = v;
		}}
	}}
	return res;
}}

const cleanedDefaultSites = cleanRule(sandbox.defaultSites || {{}});
const nofix = sandbox.nofix_sites || [];
const grouped = sandbox.grouped_sites || {{}};
const customFlexNot = cleanRule(sandbox.custom_flex_not || {{}});

let updated = {{}};
try {{
	if (fs.existsSync('{ruta_sites_updated.replace('\\\\', '/')}')) {{
		const updContent = fs.readFileSync('{ruta_sites_updated.replace('\\\\', '/')}', 'utf8');
		updated = JSON.parse(updContent);
	}}
}} catch (e) {{
	console.error('Aviso al leer sites_updated.json:', e.message);
}}

const fullRules = {{
	version: '4.4.5.3',
	release_date: '2024-08-10',
	description: 'Reglas de sitios y muros de pago de Bypass Paywalls Clean',
	total_sites: Object.keys(cleanedDefaultSites).length,
	default_sites: cleanedDefaultSites,
	sites_updated: updated,
	nofix_sites: nofix,
	grouped_sites: grouped,
	custom_flex_not: customFlexNot
}};

fs.writeFileSync('{ruta_salida.replace('\\\\', '/')}', JSON.stringify(fullRules, null, 2), 'utf8');
console.log('Reglas compiladas correctamente. Total sitios:', Object.keys(cleanedDefaultSites).length);
"""
	try:
		res = subprocess.run(["node", "-e", script_node], capture_output=True, text=True, check=True)
		print(res.stdout.strip())
		return True
	except Exception as e:
		print(f"No se pudo compilar con Node.js: {e}")
		return False


def main():
	"""Punto de entrada principal para compilar las reglas de BPC."""
	base_dir = r"E:\Antigravity\Consulta_SocialRSS\LectorEntero"
	ruta_js = os.path.join(base_dir, "bpc_extracted", "sites.js")
	if not os.path.exists(ruta_js):
		ruta_js = os.path.join(base_dir, "bypass-paywalls-firefox-clean", "sites.js")
	ruta_upd = os.path.join(base_dir, "bpc_extracted", "sites_updated.json")
	ruta_salida = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "services", "rss", "bpc_rules.json")
	ruta_salida = os.path.abspath(ruta_salida)
	if not os.path.exists(ruta_js):
		print(f"ERROR: No se encuentra sites.js en {ruta_js}")
		sys.exit(1)
	ok = compilar_con_node(ruta_js, ruta_upd, ruta_salida)
	if ok:
		print(f"Archivo generado en: {ruta_salida}")
	else:
		print("Fallo en la compilación de reglas.")
		sys.exit(1)


if __name__ == "__main__":
	main()
