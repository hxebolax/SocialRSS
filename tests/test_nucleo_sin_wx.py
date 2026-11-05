# -*- coding: utf-8 -*-
"""El núcleo, los servicios y la interfaz GTK no pueden depender de wxPython: en Linux no está instalado.
Lo que necesiten de la interfaz va por app.core.plataforma. Se comprueba sobre el código (sin importarlo),
así que también vigila los imports que están dentro de funciones y solo se ejecutan en un caso concreto."""
import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
# Módulos de app/ui que son Python puro y por eso se pueden usar desde cualquier interfaz.
UI_SIN_WX = {"app.ui.ui_helpers", "app.ui.x_lectura"}
# Único sitio permitido: el respaldo de wx de la capa de plataforma (solo si wx ya está cargado).
PERMITIDOS = {"app/core/plataforma.py"}


def _imports(ruta: Path):
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                yield nodo.lineno, alias.name
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            yield nodo.lineno, nodo.module


def test_nucleo_servicios_y_gtk_no_importan_wx():
    malos = []
    for carpeta in ("app/core", "app/services", "app/gtk"):
        for ruta in sorted((RAIZ / carpeta).rglob("*.py")):
            rel = ruta.relative_to(RAIZ).as_posix()
            if rel in PERMITIDOS:
                continue
            for linea, modulo in _imports(ruta):
                if modulo == "wx" or modulo.startswith("wx.") or (
                        modulo.startswith("app.ui") and modulo not in UI_SIN_WX):
                    malos.append(f"{rel}:{linea} importa {modulo}")
    assert not malos, "Dependencias de wx fuera de app/ui:\n" + "\n".join(malos)


def test_los_modulos_de_ui_compartidos_son_python_puro():
    for modulo in UI_SIN_WX:
        ruta = RAIZ / (modulo.replace(".", "/") + ".py")
        for linea, importado in _imports(ruta):
            assert not (importado == "wx" or importado.startswith(("wx.", "app.ui"))), \
                f"{modulo}:{linea} importa {importado}"
