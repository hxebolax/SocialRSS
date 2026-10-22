# -*- coding: utf-8 -*-
"""
Compila todos los `.po` de `data/locales/<idioma>/LC_MESSAGES/` a su `.mo` (formato binario que
usa gettext en tiempo de ejecución). Usa `polib`, que maneja correctamente escapes, plurales y
metadata (más fiable que un compilador casero).

Uso:  python scripts/compilar_idiomas.py
"""

import os
import sys

_DIR_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR_SCRIPTS)
import polib

_RAIZ = os.path.dirname(_DIR_SCRIPTS)
DIR_LOCALES = os.path.join(_RAIZ, "data", "locales")


def compilar_todo():
    if not os.path.isdir(DIR_LOCALES):
        print(f"No existe el directorio de idiomas: {DIR_LOCALES}")
        return 0
    total = 0
    for idioma in sorted(os.listdir(DIR_LOCALES)):
        lc_dir = os.path.join(DIR_LOCALES, idioma, "LC_MESSAGES")
        if not os.path.isdir(lc_dir):
            continue
        for nombre in os.listdir(lc_dir):
            if not nombre.endswith(".po"):
                continue
            ruta_po = os.path.join(lc_dir, nombre)
            ruta_mo = ruta_po[:-3] + ".mo"
            try:
                po = polib.pofile(ruta_po)
                po.save_as_mofile(ruta_mo)
                traducidas = len([e for e in po.translated_entries()])
                print(f"  {idioma}: {nombre} -> {os.path.basename(ruta_mo)} "
                      f"({traducidas}/{len(po)} traducidas)")
                total += 1
            except Exception as e:
                print(f"  ERROR compilando {ruta_po}: {e}")
    return total


def main():
    print(f"Compilando idiomas en: {DIR_LOCALES}")
    total = compilar_todo()
    print(f"\nArchivos .mo generados: {total}")
    if total:
        print("Reinicia la aplicación (o cambia el idioma en Preferencias) para aplicarlos.")


if __name__ == "__main__":
    main()
