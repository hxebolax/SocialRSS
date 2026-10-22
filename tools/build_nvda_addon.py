# -*- coding: utf-8 -*-
"""Empaqueta el complemento de NVDA (nvda_addon/) en un archivo .nvda-addon.

Un .nvda-addon es simplemente un ZIP con el manifest.ini en la raíz y las carpetas de
complementos (globalPlugins, appModules, etc.). Este script lo genera en data/bin para que:
  - se incluya en la compilación (cx_Freeze ya empaqueta data/bin, ver setup.py), y
  - el botón de Preferencias pueda instalarlo (lo genera al vuelo si no existe al ejecutar
    desde código fuente).

Se puede usar como script (python tools/build_nvda_addon.py) o importando construir_addon().
"""

import os
import re
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_SRC = os.path.join(RAIZ, "nvda_addon")
ADDON_NAME = "socialRSSListViewFix"


def _leer_version() -> str:
	"""Versión de la app (para mantener el manifest del addon sincronizado), sin importar la app."""
	try:
		src = open(os.path.join(RAIZ, "app", "core", "version.py"), encoding="utf-8").read()
		m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', src)
		if m:
			return m.group(1)
	except Exception:
		pass
	return "1.0.0"


def construir_addon(destino: str = None) -> str:
	"""Genera el .nvda-addon. 'destino' es la carpeta de salida (por defecto data/bin).
	Devuelve la ruta del archivo generado."""
	if destino is None:
		destino = os.path.join(RAIZ, "data", "bin")
	os.makedirs(destino, exist_ok=True)

	# Manifest con la versión sincronizada con la de la app.
	manifest_path = os.path.join(ADDON_SRC, "manifest.ini")
	with open(manifest_path, encoding="utf-8") as fh:
		manifest = fh.read()
	manifest = re.sub(r'(?m)^version\s*=.*$', "version = %s" % _leer_version(), manifest)

	ruta_addon = os.path.join(destino, ADDON_NAME + ".nvda-addon")
	with zipfile.ZipFile(ruta_addon, "w", zipfile.ZIP_DEFLATED) as z:
		# manifest.ini en la raíz del addon.
		z.writestr("manifest.ini", manifest)
		# Carpetas de código del complemento (globalPlugins/...), sin cachés.
		for sub in ("globalPlugins",):
			base_sub = os.path.join(ADDON_SRC, sub)
			if not os.path.isdir(base_sub):
				continue
			for root, dirs, files in os.walk(base_sub):
				dirs[:] = [d for d in dirs if d != "__pycache__"]
				for f in files:
					if f.endswith((".pyc", ".pyo")):
						continue
					full = os.path.join(root, f)
					arc = os.path.relpath(full, ADDON_SRC).replace("\\", "/")
					z.write(full, arc)
	return ruta_addon


if __name__ == "__main__":
	ruta = construir_addon()
	print("Complemento de NVDA generado en:", ruta)
