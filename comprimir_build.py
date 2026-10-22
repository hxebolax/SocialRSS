# -*- coding: utf-8 -*-
"""
Comprime la carpeta del ejecutable compilado (build/SocialRSS) en un .zip a la
máxima compresión, con la versión en el nombre (p. ej. SocialRSS_v1.0.1.zip).

Además genera, junto al zip, el archivo version.json LISTO PARA SUBIR AL SERVIDOR
(con la versión, el sha256 y el tamaño del zip): el actualizador de la app lo usa
para saber si hay versión nueva y para VERIFICAR la descarga antes de instalarla.
Basta con subir al servidor los dos archivos que quedan en build/.

Además FIRMA el version.json con la clave Ed25519 del proyecto
(tools/claves/socialrss_ed25519.key): el actualizador lleva la clave pública
incrustada y RECHAZA cualquier manifiesto sin firma válida, así que la firma es
OBLIGATORIA (si falta la clave privada, este script falla en alto para que no se
publique un paquete que ningún cliente aceptaría instalar).

Lo llama build.bat justo después de compilar con cx_Freeze. El zip se deja dentro
de la carpeta build/, junto a la carpeta SocialRSS.
"""
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import date

ORIGEN = os.path.join("build", "SocialRSS")
NOMBRE_BASE = "SocialRSS"
CLAVE_FIRMA = os.path.join("tools", "claves", "socialrss_ed25519.key")


def _firmar_manifiesto(ruta_json: str) -> bool:
    """Firma el version.json con la clave privada del proyecto (añade el campo
    'firma_ed25519'). Reutiliza tools/firmar_version.py para que la firma manual
    y la automática sean EXACTAMENTE el mismo código. Devuelve True si firmó."""
    if not os.path.exists(CLAVE_FIRMA):
        print("ERROR: no se encontró la clave de firma:", CLAVE_FIRMA)
        print("Sin firma, el actualizador de los clientes RECHAZARÁ este paquete.")
        print("Restaura la clave desde tu copia de seguridad o genera un par nuevo con")
        print("  python tools/firmar_version.py generar tools/claves/socialrss_ed25519.key")
        print("(si generas un par NUEVO, tienes que actualizar _CLAVE_PUBLICA_ED25519 en")
        print("app/services/updater/update_service.py y publicar antes una versión con ella).")
        return False
    sys.path.insert(0, "tools")
    try:
        from firmar_version import firmar
        firmar(CLAVE_FIRMA, ruta_json)
        return True
    except Exception as e:
        print(f"ERROR: no se pudo firmar el manifiesto: {e}")
        return False
    finally:
        try:
            sys.path.remove("tools")
        except ValueError:
            pass


def _version():
    try:
        src = open(os.path.join("app", "core", "version.py"), encoding="utf-8").read()
        m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', src)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


def main():
    if not os.path.isdir(ORIGEN):
        print(f"No se encontró la carpeta compilada: {ORIGEN}")
        print("Compila primero con cx_Freeze (opción 4).")
        return 1

    version = _version()
    # El zip se deja dentro de build/, junto a la carpeta SocialRSS.
    destino = os.path.join("build", f"{NOMBRE_BASE}_v{version}.zip")
    if os.path.exists(destino):
        os.remove(destino)

    print(f"Comprimiendo {ORIGEN} -> {destino} (máxima compresión)...")
    total = 0
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for raiz, _dirs, archivos in os.walk(ORIGEN):
            for archivo in archivos:
                ruta = os.path.join(raiz, archivo)
                # Dentro del zip todo cuelga de una carpeta "SocialRSS".
                interno = os.path.join(NOMBRE_BASE, os.path.relpath(ruta, ORIGEN))
                z.write(ruta, interno)
                total += 1

    tam = os.path.getsize(destino)
    mb = tam / (1024 * 1024)
    print(f"Listo: {destino} ({total} archivos, {mb:.1f} MB).")

    # Generar el version.json que hay que subir al servidor JUNTO al zip. El sha256 permite
    # al actualizador verificar que la descarga llegó íntegra antes de instalarla.
    print("Calculando el sha256 del zip...")
    h = hashlib.sha256()
    with open(destino, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    info = {
        "version": version,
        "file": os.path.basename(destino),
        "sha256": h.hexdigest(),
        "size": tam,
        "date": date.today().isoformat(),
    }
    ruta_json = os.path.join("build", "version.json")
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=4, ensure_ascii=False)

    # Firmar el manifiesto (obligatorio: los clientes rechazan un manifiesto sin firma).
    print("Firmando version.json con la clave Ed25519 del proyecto...")
    if not _firmar_manifiesto(ruta_json):
        return 1

    print(f"Generado y FIRMADO {ruta_json}. Sube al servidor ESTOS DOS archivos:")
    print(f"  1) {destino}")
    print(f"  2) {ruta_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
