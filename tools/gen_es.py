# -*- coding: utf-8 -*-
"""Genera el catálogo ESPAÑOL a partir de la plantilla .pot y lo compila a .mo.

El español es el idioma BASE de la app (las cadenas del código ya están en español), así que
cada traducción es msgstr = msgid. Crea:
  data/locales/es/LC_MESSAGES/socialrss.po
  data/locales/es/LC_MESSAGES/socialrss.mo

Requiere la plantilla data/locales/socialrss.pot (genérala con tools/gen_pot.py).
"""
import os

try:
    import polib
except Exception:
    print("Falta el paquete 'polib'. Instálalo con: pip install polib")
    raise SystemExit(1)

POT = "data/locales/socialrss.pot"
DEST_DIR = "data/locales/es/LC_MESSAGES"


def main():
    if not os.path.exists(POT):
        print(f"No existe {POT}. Genera primero la plantilla (tools/gen_pot.py).")
        raise SystemExit(1)

    pot = polib.pofile(POT)
    po = polib.POFile()
    po.metadata = {
        "Project-Id-Version": "1.0",
        "Language": "es",
        "Language-Team": "Español",
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": "nplurals=2; plural=(n != 1);",
    }

    vistos = set()
    for entry in pot:
        if not entry.msgid or entry.msgid in vistos:
            continue
        vistos.add(entry.msgid)
        po.append(polib.POEntry(msgid=entry.msgid, msgstr=entry.msgid))

    os.makedirs(DEST_DIR, exist_ok=True)
    po_path = os.path.join(DEST_DIR, "socialrss.po")
    mo_path = os.path.join(DEST_DIR, "socialrss.mo")
    po.save(po_path)
    # polib guarda en modo texto (CRLF en Windows): se deja en LF, la norma del proyecto.
    with open(po_path, "rb") as fh:
        datos = fh.read()
    with open(po_path, "wb") as fh:
        fh.write(datos.replace(b"\r\n", b"\n"))
    po.save_as_mofile(mo_path)
    print(f"Español generado: {po_path} y {mo_path} ({len(po)} cadenas).")


if __name__ == "__main__":
    main()
