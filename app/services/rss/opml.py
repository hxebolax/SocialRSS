# -*- coding: utf-8 -*-
"""
Módulo para la importación y exportación de archivos de suscripción OPML.

Auditoría RSS-20: la jerarquía de carpetas se CONSERVA en ambos sentidos (antes la
importación aplanaba las subcarpetas a la carpeta inmediata y la exportación perdía
las subcarpetas). La ruta de carpetas viaja como lista `category_path`; `category`
(carpeta inmediata) se mantiene por compatibilidad. Con límites de tamaño, cantidad
y profundidad para no tragar archivos malignos o rotos.
"""

import logging
import os
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

# Límites de seguridad de la importación (auditoría RSS-20).
MAX_BYTES_OPML = 20 * 1024 * 1024   # 20 MiB
MAX_FEEDS_OPML = 10000
MAX_PROFUNDIDAD_OPML = 20


def importar_opml(file_path: str) -> List[Dict[str, Any]]:
    """
    Lee un archivo OPML y devuelve una lista de diccionarios con la información de los feeds.
    Conserva la JERARQUÍA de carpetas: cada feed lleva `category_path` (lista de nombres,
    de la raíz a la carpeta inmediata) y `category` (solo la inmediata, compatibilidad).
    """
    feeds: List[Dict[str, Any]] = []
    try:
        if os.path.getsize(file_path) > MAX_BYTES_OPML:
            raise ValueError(f"El archivo OPML supera el límite de {MAX_BYTES_OPML // (1024*1024)} MiB.")
        tree = ET.parse(file_path)
        root = tree.getroot()

        body = root.find("body")
        if body is None:
            return feeds

        def procesar_elemento(elem, ruta: Optional[list] = None, profundidad: int = 0):
            if profundidad > MAX_PROFUNDIDAD_OPML:
                return
            ruta = ruta or []
            for child in elem.findall("outline"):
                if len(feeds) >= MAX_FEEDS_OPML:
                    return
                text = child.get("text", "")
                title = child.get("title", text)
                xml_url = child.get("xmlUrl", "")
                html_url = child.get("htmlUrl", "")

                if xml_url:
                    # Es un feed
                    feeds.append({
                        "title": title or text,
                        "url": xml_url,
                        "html_url": html_url,
                        "category": ruta[-1] if ruta else None,
                        "category_path": list(ruta),
                    })
                else:
                    # Es una carpeta: bajar conservando la RUTA completa
                    nombre = title or text
                    procesar_elemento(child, ruta + ([nombre] if nombre else []),
                                      profundidad + 1)

        procesar_elemento(body)
        log.info(f"Importados {len(feeds)} feeds desde archivo OPML: {file_path}")
    except Exception as e:
        log.error(f"Error al importar archivo OPML {file_path}: {e}")
        raise

    return feeds


def exportar_opml(file_path: str, feeds: List[Dict[str, Any]], titulo: str = "SocialRSS Subscriptions"):
    """
    Exporta una lista de feeds a un archivo OPML 2.0.
    Respeta la jerarquía: cada feed puede llevar `category_path` (lista, de la raíz a la
    carpeta inmediata); si solo trae `category`, se comporta como antes (un nivel).
    """
    try:
        opml = ET.Element("opml", version="2.0")

        # Cabecera
        head = ET.SubElement(opml, "head")
        title_elem = ET.SubElement(head, "title")
        title_elem.text = titulo

        # Cuerpo
        body = ET.SubElement(opml, "body")

        # Árbol de carpetas ya creadas: clave = tupla de la ruta, valor = elemento outline.
        carpetas: Dict[tuple, ET.Element] = {}

        def _carpeta(ruta: tuple) -> ET.Element:
            """Devuelve (creándolo si hace falta, con sus padres) el outline de la ruta."""
            if not ruta:
                return body
            if ruta in carpetas:
                return carpetas[ruta]
            padre = _carpeta(ruta[:-1])
            elem = ET.SubElement(padre, "outline", text=ruta[-1], title=ruta[-1])
            carpetas[ruta] = elem
            return elem

        for f in feeds:
            ruta = tuple(f.get("category_path") or ([f["category"]] if f.get("category") else []))
            destino = _carpeta(ruta)
            atributos = {
                "type": "rss",
                "text": f.get("title") or "",
                "title": f.get("title") or "",
                "xmlUrl": f.get("url") or "",
            }
            if f.get("html_url"):
                atributos["htmlUrl"] = f["html_url"]
            ET.SubElement(destino, "outline", **atributos)

        # Generar XML legible y escribir en disco
        tree = ET.ElementTree(opml)
        ET.indent(tree, space="\t", level=0)

        with open(file_path, "wb") as f:
            # Escribir cabecera XML inicial
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            tree.write(f, encoding="utf-8", xml_declaration=False)

        log.info(f"Exportados {len(feeds)} feeds con éxito a OPML: {file_path}")
    except Exception as e:
        log.error(f"Error al exportar a archivo OPML {file_path}: {e}")
        raise
