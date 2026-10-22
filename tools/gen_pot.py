# -*- coding: utf-8 -*-
"""Genera la plantilla de traducción (.pot) extrayendo las cadenas _("...") / _('...') del
código de la carpeta app/. Lo llama build.bat (opción 6). Se ejecuta como archivo .py porque
cmd no admite scripts de Python multilínea con 'python -c'.
"""
import os
import re
from pathlib import Path

PATRON = re.compile(r"""_\(\s*"(.*?)"\s*\)|_\(\s*'(.*?)'\s*\)""", re.DOTALL)


def main():
    cadenas = set()
    for root, _dirs, files in os.walk("app"):
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                contenido = Path(root, f).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in PATRON.finditer(contenido):
                val = m.group(1) or m.group(2)
                if val:
                    cadenas.add(val)

    cabecera = (
        "# SocialRSS Accessible Suite Translation Template.\n"
        "# Copyright (C) 2026\n"
        "#\n"
        'msgid ""\n'
        'msgstr ""\n'
        '"Project-Id-Version: 1.0\\n"\n'
        '"MIME-Version: 1.0\\n"\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Content-Transfer-Encoding: 8bit\\n"\n\n'
    )
    cuerpo = []
    for s in sorted(cadenas):
        escapado = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        cuerpo.append(f'msgid "{escapado}"\nmsgstr ""\n')

    os.makedirs("data/locales", exist_ok=True)
    destino = Path("data/locales/socialrss.pot")
    # newline="\n": saltos LF, la norma del proyecto (en modo texto Windows escribiría CRLF).
    destino.write_text(cabecera + "\n".join(cuerpo) + "\n", encoding="utf-8", newline="\n")
    print(f"Plantilla {destino} creada con éxito con {len(cadenas)} cadenas.")


if __name__ == "__main__":
    main()
