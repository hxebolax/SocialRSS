# -*- coding: utf-8 -*-
"""Normaliza los saltos de línea del proyecto.

Norma (la misma que fija .editorconfig):
- LF para todo el texto del proyecto (.py, .md, .json, .txt, .po, .ini...).
- CRLF solo para los scripts de Windows (.bat y .cmd): cmd.exe puede fallar con saltos de
  etiqueta (goto, call :x) en archivos con LF.

Solo se tocan los bytes de fin de línea; el contenido queda idéntico. Los archivos binarios se
saltan.

Uso (desde la raíz del proyecto):
    python tools/normalizar_saltos.py              -> corrige y dice qué ha cambiado
    python tools/normalizar_saltos.py --comprobar  -> solo lista lo que no cumple (sale con 1)

Útil sobre todo después de restaurar una copia antigua, que trae los formatos de entonces.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Qué se revisa: el código y la documentación del proyecto (no las dependencias ni las copias).
# De data/ solo lo que forma parte del proyecto (documentación y traducciones): el resto son datos
# de ejecución que escribe la propia aplicación (configuración, base de datos, registros...).
CARPETAS = ("app", "tests", "tools", "scripts", "nvda_addon",
            os.path.join("data", "docs"), os.path.join("data", "locales"))
ARCHIVOS_RAIZ = ("main.py", "setup.py", "comprimir_build.py", "build.bat", "MANUAL.md",
                 "TECLAS.md", "README.md", "requirements.txt", "requirements-dev.txt",
                 "setup.cfg", "pytest.ini", ".gitignore", ".editorconfig")
EXCLUIR_DIRS = {".venv", "__pycache__", ".pytest_cache", ".codex_tmp", "%temp%", "__pip__",
                ".claude", "build", "dist", ".git"}
EXT_LF = {".py", ".pyw", ".md", ".txt", ".json", ".po", ".pot", ".ini", ".cfg", ".toml",
          ".yml", ".yaml", ".html", ".htm", ".css", ".js", ".xml", ".csv", ".ps1", ".manifest"}
EXT_CRLF = {".bat", ".cmd"}


def _ext(ruta):
    nombre = os.path.basename(ruta)
    if nombre in (".gitignore", ".editorconfig"):
        return nombre
    return os.path.splitext(nombre)[1].lower()


def fin_esperado(ruta):
    """b"\\n", b"\\r\\n" o None si el archivo no entra en la norma."""
    ext = _ext(ruta)
    if ext in EXT_CRLF:
        return b"\r\n"
    if ext in EXT_LF or ext in (".gitignore", ".editorconfig"):
        return b"\n"
    return None


def archivos(raiz=RAIZ):
    """Rutas (absolutas) de los archivos de texto del proyecto sujetos a la norma."""
    for nombre in ARCHIVOS_RAIZ:
        p = os.path.join(raiz, nombre)
        if os.path.isfile(p) and fin_esperado(p):
            yield p
    for carpeta in CARPETAS:
        base = os.path.join(raiz, carpeta)
        for d, dirs, fs in os.walk(base):
            dirs[:] = [x for x in dirs if x not in EXCLUIR_DIRS]
            for f in fs:
                p = os.path.join(d, f)
                if fin_esperado(p):
                    yield p


def normalizado(datos, fin):
    """Contenido con todos los saltos (CRLF, LF o CR suelto) convertidos a `fin`."""
    unificado = datos.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return unificado if fin == b"\n" else unificado.replace(b"\n", b"\r\n")


def revisar(corregir=False, raiz=RAIZ):
    """Devuelve la lista de rutas relativas que no cumplen la norma (y las corrige si se pide)."""
    malos = []
    for p in archivos(raiz):
        try:
            with open(p, "rb") as fh:
                datos = fh.read()
        except OSError:
            continue
        if b"\0" in datos[:4096]:
            continue  # binario
        fin = fin_esperado(p)
        nuevo = normalizado(datos, fin)
        if nuevo != datos:
            malos.append(os.path.relpath(p, raiz))
            if corregir:
                with open(p, "wb") as fh:
                    fh.write(nuevo)
    return malos


def main(argv):
    solo_comprobar = "--comprobar" in argv
    malos = revisar(corregir=not solo_comprobar)
    if not malos:
        print("Todo cumple la norma de saltos de línea (LF; CRLF solo en .bat y .cmd).")
        return 0
    verbo = "No cumplen" if solo_comprobar else "Corregidos"
    print(f"{verbo} {len(malos)} archivos:")
    for r in malos:
        print("  " + r)
    return 1 if solo_comprobar else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
