# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.abspath("."))
"""Importa TODOS los módulos de app/ (con i18n instalado, como en el arranque real)
para detectar errores de import-time: ciclos, NameError de '_', símbolos no definidos."""
import importlib, pkgutil, sys, traceback
import app.core.i18n  # instala builtins._ como en main.py

import app
errores = []
modulos = []
for mi in pkgutil.walk_packages(app.__path__, "app."):
    modulos.append(mi.name)

for nombre in sorted(modulos):
    # saltar backends opcionales que requieren hardware/credenciales en import? no: import-time es seguro
    try:
        importlib.import_module(nombre)
    except Exception as e:
        errores.append((nombre, repr(e)))

print("módulos importados:", len(modulos))
if errores:
    print("ERRORES (%d):" % len(errores))
    for n, e in errores:
        print("  %s -> %s" % (n, e))
    sys.exit(1)
print("TODOS los módulos importan OK")
