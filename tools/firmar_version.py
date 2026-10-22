# -*- coding: utf-8 -*-
"""Firma del manifiesto de actualización (version.json) con Ed25519.

La firma separa la AUTENTICIDAD del transporte: el sha256 publicado junto al zip
protege contra descargas corruptas, pero si alguien comprometiera el servidor
podría cambiar zip y hash a la vez. Con la firma, el atacante necesitaría además
la clave PRIVADA, que no está en el servidor (guárdala fuera, p. ej. en un pendrive).

Uso:
  1) Generar el par de claves (UNA sola vez):
        python tools/firmar_version.py generar claves_socialrss.key
     Imprime la clave PÚBLICA en hex: pégala en _CLAVE_PUBLICA_ED25519 de
     app/services/updater/update_service.py (a partir de ahí, las actualizaciones
     sin firma válida se rechazan).

  2) Firmar el version.json de cada release (tras generar el zip y su sha256):
        python tools/firmar_version.py firmar claves_socialrss.key version.json
     Añade el campo "firma_ed25519" al version.json (firma de "<version>:<sha256>").
"""

import json
import sys
import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def generar(ruta_privada: str):
    priv = Ed25519PrivateKey.generate()
    datos = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    Path(ruta_privada).write_bytes(datos)
    publica = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()
    print(f"Clave privada guardada en: {ruta_privada}  (¡NO la subas al servidor!)")
    print(f"Clave pública (pégala en _CLAVE_PUBLICA_ED25519): {publica}")


def firmar(ruta_privada: str, ruta_version_json: str):
    priv = Ed25519PrivateKey.from_private_bytes(Path(ruta_privada).read_bytes())
    manifiesto = json.loads(Path(ruta_version_json).read_text(encoding="utf-8"))
    version = str(manifiesto.get("version", "")).strip()
    sha256 = str(manifiesto.get("sha256", "")).strip().lower()
    if not version or not sha256:
        raise SystemExit("El version.json debe tener 'version' y 'sha256' antes de firmarse.")
    mensaje = f"{version}:{sha256}".encode("utf-8")
    manifiesto["firma_ed25519"] = base64.b64encode(priv.sign(mensaje)).decode("ascii")
    Path(ruta_version_json).write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Firmado: {ruta_version_json} (versión {version})")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "generar":
        generar(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "firmar":
        firmar(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        raise SystemExit(1)
