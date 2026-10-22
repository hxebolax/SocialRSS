# -*- coding: utf-8 -*-
"""Compila los archivos de traducción .po a .mo dentro de data/locales/. Lo llama build.bat
(opción 7). Se ejecuta como archivo .py porque cmd no admite 'python -c' multilínea.
"""
import os

try:
    import polib
except Exception:
    print("Falta el paquete 'polib'. Instálalo con: pip install polib")
    raise SystemExit(1)


def main():
    locales_dir = "data/locales"
    encontrados = False
    for root, _dirs, files in os.walk(locales_dir):
        for f in files:
            if not f.endswith(".po"):
                continue
            po_path = os.path.join(root, f)
            mo_path = po_path[:-3] + ".mo"
            try:
                po = polib.pofile(po_path)
                po.save_as_mofile(mo_path)
                print(f"Compilado: {po_path} -> {mo_path}")
                encontrados = True
            except Exception as e:
                print(f"Error al compilar {po_path}: {e}")
    if not encontrados:
        print(f"No se encontraron archivos .po para compilar en {locales_dir}/")


if __name__ == "__main__":
    main()
