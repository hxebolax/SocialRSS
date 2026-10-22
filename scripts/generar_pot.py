# -*- coding: utf-8 -*-
"""
Genera la plantilla de traducción `data/locales/socialrss.pot` desde el código fuente.

Extrae todas las cadenas envueltas en _( "..." ) con el módulo `ast` (preciso: respeta comillas,
escapes y concatenaciones literales, y no duplica). Escribe el .pot con `polib`, que produce un
formato gettext correcto (metadata, escapes, referencias de fichero:línea agrupadas).

Uso:  python scripts/generar_pot.py
"""

import os
import ast
import sys

_DIR_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR_SCRIPTS)
import polib

DOMINIO = "socialrss"
_RAIZ = os.path.dirname(_DIR_SCRIPTS)
# Dónde están las cadenas traducibles: todo el paquete `app` más el `main.py` de la raíz.
DIRS_SRC = [os.path.join(_RAIZ, "app")]
ARCHIVOS_SRC = [os.path.join(_RAIZ, "main.py")]
DIR_LOCALES = os.path.join(_RAIZ, "data", "locales")


def _texto_de_constante(nodo):
    """Devuelve la cadena de un nodo si es un literal str (ast.Constant/Str), o None."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    # Concatenación de literales: _("a" "b") o _("a" + "b")
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
        izq = _texto_de_constante(nodo.left)
        der = _texto_de_constante(nodo.right)
        if izq is not None and der is not None:
            return izq + der
    return None


def extraer_cadenas(archivos):
    """Recorre los .py y devuelve {msgid: [(ruta_rel, linea), ...]} para cada _( "..." )."""
    cadenas = {}
    for ruta in archivos:
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                arbol = ast.parse(f.read(), filename=ruta)
        except Exception as e:
            print(f"  Aviso: no se pudo analizar {ruta}: {e}")
            continue
        ruta_rel = os.path.relpath(ruta, _RAIZ).replace("\\", "/")
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
                    and nodo.func.id == "_" and nodo.args):
                texto = _texto_de_constante(nodo.args[0])
                if texto:
                    cadenas.setdefault(texto, []).append((ruta_rel, nodo.lineno))
    return cadenas


def _listar_py():
    """Todos los .py/.pyw de los directorios fuente más los archivos sueltos configurados."""
    archivos = list(ARCHIVOS_SRC)
    for base in DIRS_SRC:
        for raiz, _dirs, nombres in os.walk(base):
            if "__pycache__" in raiz:
                continue
            for n in nombres:
                if n.endswith((".py", ".pyw")):
                    archivos.append(os.path.join(raiz, n))
    return [a for a in archivos if os.path.isfile(a)]


def main():
    os.makedirs(DIR_LOCALES, exist_ok=True)
    ruta_pot = os.path.join(DIR_LOCALES, f"{DOMINIO}.pot")

    archivos = _listar_py()
    print(f"Analizando {len(archivos)} archivos de código...")
    cadenas = extraer_cadenas(archivos)

    pot = polib.POFile()
    pot.metadata = {
        "Project-Id-Version": "SocialRSS Accessible Suite",
        "Report-Msgid-Bugs-To": "",
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Language": "",
        "X-Generated-By": "scripts/generar_pot.py",
    }
    for msgid in sorted(cadenas):
        entrada = polib.POEntry(msgid=msgid, msgstr="",
                                occurrences=cadenas[msgid])
        pot.append(entrada)
    pot.save(ruta_pot)

    print(f"Plantilla generada: {ruta_pot}")
    print(f"Cadenas únicas: {len(cadenas)}")
    print("\nSiguiente paso: abre el editor (scripts/editor_po.py), pulsa "
          "«Generar .po desde .pot», elige idioma, traduce y compila.")


if __name__ == "__main__":
    main()
