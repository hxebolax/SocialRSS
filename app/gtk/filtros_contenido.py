# -*- coding: utf-8 -*-
"""Aplica los filtros de contenido (Herramientas → Filtros de contenido) a las publicaciones de un buffer: los de acción
«Ocultar» quitan la publicación de la lista. Las marcas automáticas (leído, destacar) solo tienen sentido en RSS, que
las aplica al descargar los artículos."""

from app.core.content_filters import ContentFilterEngine


def sin_ocultas(servicio: str, filas, i_texto: int, i_autor: int, html: bool = False):
    """Devuelve las filas de la consulta (tuplas) menos las que algún filtro activo manda ocultar."""
    filtros = ContentFilterEngine.cargar(servicio)
    if not filtros or not any(f["action"] == "hide" for f in filtros):
        return list(filas)
    if html:
        from app.core.html_utils import html_a_texto
    salida = []
    for r in filas:
        texto = r[i_texto] or ""
        if html:
            texto = html_a_texto(texto)
        if ContentFilterEngine.evaluar(servicio, texto, r[i_autor] or "", filtros)["hide"]:
            continue
        salida.append(r)
    return salida
