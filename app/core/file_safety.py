# -*- coding: utf-8 -*-
"""Seguridad de archivos recibidos de la red (común a todas las redes).

Dos protecciones sencillas contra el malware recibido como adjunto:
  * es_extension_activa(): detecta tipos ACTIVOS (ejecutables/scripts/accesos directos) que NUNCA
    deben abrirse desde la app con un clic; solo descargarse.
  * marcar_zona_internet(): escribe el flujo NTFS Zone.Identifier (Mark-of-the-Web) para que Windows
    avise de la procedencia de Internet del archivo al abrirlo (SmartScreen).
"""
import os

# Extensiones de contenido ACTIVO/ejecutable. Un adjunto recibido no debe poder ejecutarse con un
# clic desde la aplicación; estos tipos solo se descargan y se marcan con Mark-of-the-Web.
EXT_ACTIVAS = frozenset({
    ".exe", ".msi", ".msp", ".com", ".scr", ".pif", ".bat", ".cmd", ".ps1", ".psm1", ".vbs",
    ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".jar", ".lnk", ".url", ".reg", ".inf",
    ".cpl", ".dll", ".sys", ".gadget", ".application", ".msc", ".ade", ".adp", ".cab", ".chm",
    ".sh", ".py", ".pyw", ".apk", ".appx", ".appimage", ".command", ".scf", ".ws",
})


def es_extension_activa(nombre_o_ruta: str) -> bool:
    """True si el nombre/ruta acaba en una extensión de contenido activo (ejecutable/script)."""
    resto = (nombre_o_ruta or "").split("?")[0].strip().rstrip(". ")
    return os.path.splitext(resto)[1].lower() in EXT_ACTIVAS


def marcar_zona_internet(path: str):
    """Mark-of-the-Web: marca el archivo como descargado de Internet (ZoneId=3) escribiendo el flujo
    alternativo NTFS 'Zone.Identifier'. Best-effort: en FAT/exFAT (sin ADS) falla en silencio."""
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as f:
            f.write("[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=about:internet\r\n")
    except Exception:
        pass
