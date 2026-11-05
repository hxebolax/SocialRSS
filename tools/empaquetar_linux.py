# -*- coding: utf-8 -*-
"""
Genera el paquete de Linux de una versión: build/SocialRSS_v<versión>_linux.tar.gz.

En Linux la aplicación se ejecuta desde el código (la interfaz GTK 4 usa PyGObject y GStreamer del
sistema), así que el paquete lleva el código y los datos de solo lectura, sin nada de Windows: ni el
wheel de basskit, ni ffmpeg.exe, ni el complemento de NVDA, ni los scripts de compilación. Dentro todo
cuelga de una carpeta «SocialRSS»; instalar.sh crea el entorno de Python y la entrada del menú.

Lo llama comprimir_build.py (que añade su sha256 al version.json firmado); también se puede lanzar
solo: python tools/empaquetar_linux.py
"""
import hashlib
import os
import re
import subprocess
import sys
import tarfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOMBRE_BASE = "SocialRSS"

# Lo que viaja en el paquete (rutas relativas a la raíz, con «/»).
CARPETAS = ("app/", "data/docs/", "data/locales/", "data/plugins/", "data/sounds/", "scripts/")
ARCHIVOS = ("main.py", "README.md", "MANUAL.md", "TECLAS.md", "LICENSE", "requirements-linux.txt",
            "socialrss.desktop", "instalar.sh")
EJECUTABLES = ("instalar.sh", "scripts/socialrss")


def version() -> str:
    with open(os.path.join(RAIZ, "app", "core", "version.py"), encoding="utf-8") as f:
        m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', f.read())
    return m.group(1) if m else "0.0.0"


def archivos_del_paquete() -> list:
    """Archivos del proyecto (los de git, también los nuevos sin confirmar; nunca los ignorados)."""
    salida = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=RAIZ, capture_output=True, check=True).stdout.decode("utf-8")
    elegidos = []
    for rel in sorted(set(r for r in salida.split("\0") if r)):
        if "__pycache__/" in rel or rel.endswith((".pyc", ".pyo")):
            continue
        if not os.path.isfile(os.path.join(RAIZ, rel)):
            continue          # borrado en el árbol de trabajo
        if rel in ARCHIVOS or rel.startswith(CARPETAS):
            elegidos.append(rel)
    return elegidos


def _info(tf, nombre, ruta=None, es_dir=False, ejecutable=False):
    ti = tf.gettarinfo(ruta, arcname=nombre) if ruta else tarfile.TarInfo(nombre)
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if es_dir:
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
    else:
        ti.mode = 0o755 if ejecutable else 0o644
    return ti


def empaquetar(destino_dir: str = None):
    """Crea el .tar.gz y devuelve (ruta, sha256, tamaño)."""
    destino_dir = destino_dir or os.path.join(RAIZ, "build")
    os.makedirs(destino_dir, exist_ok=True)
    destino = os.path.join(destino_dir, f"{NOMBRE_BASE}_v{version()}_linux.tar.gz")
    rels = archivos_del_paquete()
    obligatorios = [a for a in ("main.py", "instalar.sh", "requirements-linux.txt", "scripts/socialrss")
                    if a not in rels]
    if obligatorios:
        raise SystemExit(f"Faltan archivos imprescindibles para el paquete de Linux: {obligatorios}")
    carpetas = sorted({"/".join(r.split("/")[:i]) for r in rels for i in range(1, r.count("/") + 1)})
    with tarfile.open(destino, "w:gz", compresslevel=9) as tf:
        tf.addfile(_info(tf, NOMBRE_BASE, es_dir=True))
        for c in carpetas:
            tf.addfile(_info(tf, f"{NOMBRE_BASE}/{c}", es_dir=True))
        for rel in rels:
            ruta = os.path.join(RAIZ, rel)
            with open(ruta, "rb") as f:
                tf.addfile(_info(tf, f"{NOMBRE_BASE}/{rel}", ruta, ejecutable=rel in EJECUTABLES), f)
    h = hashlib.sha256()
    with open(destino, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    tam = os.path.getsize(destino)
    print(f"Paquete de Linux: {destino} ({len(rels)} archivos, {tam / (1024 * 1024):.1f} MB).")
    return destino, h.hexdigest(), tam


def actualizar_pkgbuild(ver: str, sha256: str, ruta: str = None) -> bool:
    """Pone en packaging/arch/PKGBUILD la versión y el sha256 del .tar.gz recién generado (el que se
    sube a la release), para que el paquete de Arch se construya siempre desde la última versión.
    Con una versión nueva pkgrel vuelve a 1. Devuelve True si cambió algo."""
    ruta = ruta or os.path.join(RAIZ, "packaging", "arch", "PKGBUILD")
    if not os.path.isfile(ruta):
        return False
    with open(ruta, encoding="utf-8", newline="") as f:
        texto = f.read()
    nuevo = texto
    m = re.search(r"^pkgver=(\S+)$", nuevo, re.M)
    if m and m.group(1) != ver:
        nuevo = re.sub(r"^pkgver=\S+$", f"pkgver={ver}", nuevo, count=1, flags=re.M)
        nuevo = re.sub(r"^pkgrel=\S+$", "pkgrel=1", nuevo, count=1, flags=re.M)
    # La primera suma de sha256sums es la del .tar.gz (primer elemento de source).
    nuevo = re.sub(r"^(sha256sums=\(')[0-9a-fA-F]{64}(')", rf"\g<1>{sha256}\g<2>", nuevo, count=1, flags=re.M)
    if nuevo == texto:
        return False
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write(nuevo)
    print(f"PKGBUILD de Arch actualizado a la versión {ver}.")
    return True


if __name__ == "__main__":
    _ruta, _sha, _tam = empaquetar(sys.argv[1] if len(sys.argv) > 1 else None)
    actualizar_pkgbuild(version(), _sha)
