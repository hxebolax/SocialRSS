# -*- coding: utf-8 -*-
"""División y reconstrucción de archivos grandes para Telegram Drive.

Telegram limita el tamaño de cada documento. Un archivo mayor que ese límite se divide en
PARTES (cada una un documento en el canal); al descargar se reconstruyen en orden. Todo se
hace por BLOQUES en streaming, nunca cargando el archivo entero en memoria, para soportar
archivos de varios gigabytes. Se calcula el SHA-256 de cada parte y del archivo final.
"""
import hashlib
import os
from typing import List, Dict, Optional, Callable

BLOQUE = 1024 * 1024  # 1 MiB por lectura


def hace_falta_dividir(size: int, limite: int) -> bool:
    """True si un archivo de `size` bytes supera el límite de un solo documento."""
    return limite > 0 and size > limite


def dividir(ruta: str, tam_parte: int, carpeta_temp: str,
            cancelado: Optional[Callable[[], bool]] = None) -> List[Dict]:
    """Divide `ruta` en archivos de como mucho `tam_parte` bytes dentro de `carpeta_temp`.
    Devuelve una lista de dicts {orden, ruta, size, sha256}. Lee y escribe por bloques.
    Si `cancelado()` devuelve True, borra lo escrito y devuelve []."""
    os.makedirs(carpeta_temp, exist_ok=True)
    partes = []
    base = os.path.basename(ruta)
    with open(ruta, "rb") as fh:
        orden = 0
        while True:
            if cancelado is not None and cancelado():
                _borrar_partes(partes)
                return []
            ruta_parte = os.path.join(carpeta_temp, f"{base}.part{orden:04d}")
            h = hashlib.sha256()
            escrito = 0
            with open(ruta_parte, "wb") as pf:
                while escrito < tam_parte:
                    if cancelado is not None and cancelado():
                        pf.close()
                        _borrar_partes(partes + [{"ruta": ruta_parte}])
                        return []
                    leer = min(BLOQUE, tam_parte - escrito)
                    datos = fh.read(leer)
                    if not datos:
                        break
                    pf.write(datos)
                    h.update(datos)
                    escrito += len(datos)
            if escrito == 0:
                # No había más datos: esta parte sobra.
                os.remove(ruta_parte)
                break
            partes.append({"orden": orden, "ruta": ruta_parte, "size": escrito,
                           "sha256": h.hexdigest()})
            orden += 1
            if escrito < tam_parte:
                break  # última parte
    return partes


def extraer_parte(ruta: str, orden: int, tam_parte: int, carpeta_temp: str,
                  cancelado: Optional[Callable[[], bool]] = None) -> Optional[Dict]:
    """Extrae UNA sola parte (la de índice `orden`) de `ruta`, sin reescribir el archivo entero.
    Sirve para REANUDAR una subida por partes: solo se materializa la parte que falta. Devuelve
    {orden, ruta, size, sha256} o None si se cancela. Lee por bloques (streaming)."""
    os.makedirs(carpeta_temp, exist_ok=True)
    inicio = orden * tam_parte
    base = os.path.basename(ruta)
    ruta_parte = os.path.join(carpeta_temp, f"{base}.part{orden:04d}")
    h = hashlib.sha256()
    escrito = 0
    with open(ruta, "rb") as fh:
        fh.seek(inicio)
        with open(ruta_parte, "wb") as pf:
            while escrito < tam_parte:
                if cancelado is not None and cancelado():
                    pf.close()
                    _borrar_si_existe(ruta_parte)
                    return None
                leer = min(BLOQUE, tam_parte - escrito)
                datos = fh.read(leer)
                if not datos:
                    break
                pf.write(datos)
                h.update(datos)
                escrito += len(datos)
    if escrito == 0:
        _borrar_si_existe(ruta_parte)
        return None
    return {"orden": orden, "ruta": ruta_parte, "size": escrito, "sha256": h.hexdigest()}


def reconstruir(rutas_ordenadas: List[str], destino: str,
                progreso: Optional[Callable[[int], None]] = None,
                cancelado: Optional[Callable[[], bool]] = None) -> Optional[str]:
    """Une las partes (ya en orden) en `destino`, por bloques. Devuelve el SHA-256 del
    archivo final, o None si se canceló (y borra el archivo a medias)."""
    h = hashlib.sha256()
    hecho = 0
    try:
        with open(destino, "wb") as out:
            for ruta in rutas_ordenadas:
                with open(ruta, "rb") as pf:
                    while True:
                        if cancelado is not None and cancelado():
                            out.close()
                            _borrar_si_existe(destino)
                            return None
                        datos = pf.read(BLOQUE)
                        if not datos:
                            break
                        out.write(datos)
                        h.update(datos)
                        hecho += len(datos)
                        if progreso is not None:
                            progreso(hecho)
    except Exception:
        _borrar_si_existe(destino)
        raise
    return h.hexdigest()


def _borrar_partes(partes: List[Dict]):
    for p in partes:
        _borrar_si_existe(p.get("ruta"))


def _borrar_si_existe(ruta: Optional[str]):
    if ruta and os.path.exists(ruta):
        try:
            os.remove(ruta)
        except OSError:
            pass
