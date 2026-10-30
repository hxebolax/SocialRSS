# -*- coding: utf-8 -*-
"""Documentos de ayuda (manual y teclas) en Markdown: se localizan, se convierten a HTML con un índice navegable y se abren
en el navegador. Lo usan las interfaces de Windows (wx) y de Linux (GTK)."""

import logging
import os

from app.core.config_manager import config
from app.core.paths import get_data_path
from app.core.privacy import abrir_url
from app.core.speech import notificar

log = logging.getLogger(__name__)

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ruta_documento(base_compilado: str, base_raiz: str):
    """Ruta del documento: <base>_<idioma>.md y <base>_es.md en data/docs (compilado), y en la raíz
    <base>_<idioma>.md y <BASE_RAIZ>.md (código fuente). None si no existe."""
    lang = config.get("general", "language", "es")
    candidatos = [
        str(get_data_path(f"docs/{base_compilado}_{lang}.md")),
        str(get_data_path(f"docs/{base_compilado}_es.md")),
        os.path.join(RAIZ, f"{base_compilado}_{lang}.md"),
        os.path.join(RAIZ, f"{base_raiz}.md"),
        os.path.join(os.getcwd(), f"{base_raiz}.md"),
    ]
    return next((c for c in candidatos if os.path.exists(c)), None)


def abrir_documento_md(base_compilado: str, base_raiz: str, titulo: str, salida: str, aviso: str) -> bool:
    """Abre el documento en el navegador. Devuelve True si pudo; si no, avisa con `aviso`."""
    ruta_md = ruta_documento(base_compilado, base_raiz)
    if not ruta_md:
        notificar(aviso)
        return False
    try:
        with open(ruta_md, "r", encoding="utf-8") as fh:
            texto_md = fh.read()
        try:
            import markdown
            if "[TOC]" not in texto_md:
                texto_md = "[TOC]\n\n" + texto_md
            cuerpo = markdown.markdown(texto_md, extensions=["tables", "toc", "fenced_code", "sane_lists", "attr_list"])
        except Exception as e_md:
            log.warning(f"No se pudo convertir el documento con markdown: {e_md}")
            import html as _html
            cuerpo = "<pre>" + _html.escape(texto_md) + "</pre>"
        from app.core.html_utils import html_a_documento
        ruta = get_data_path("temp") / salida
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write(html_a_documento(titulo, cuerpo))
        abrir_url(ruta.as_uri())
        return True
    except Exception as e:
        log.error(f"Error al abrir el documento {salida}: {e}")
        notificar(aviso)
        return False
