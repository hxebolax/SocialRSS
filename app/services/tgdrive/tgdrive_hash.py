# -*- coding: utf-8 -*-
"""Cálculo de SHA-256 por bloques para Telegram Drive.

Lee los archivos en trozos (nunca el archivo entero en memoria), para poder calcular el
hash de archivos de varios gigabytes sin consumir RAM. Se usa para verificar la integridad
de las descargas reconstruidas y para detectar duplicados por contenido.
"""
import hashlib
from typing import Callable, Optional

# Tamaño de bloque de lectura por defecto (1 MiB): buen equilibrio entre memoria y sistema.
BLOQUE_DEFECTO = 1024 * 1024


def sha256_archivo(ruta: str, bloque: int = BLOQUE_DEFECTO,
                   progreso: Optional[Callable[[int], None]] = None,
                   cancelado: Optional[Callable[[], bool]] = None) -> Optional[str]:
    """Calcula el SHA-256 de un archivo leyéndolo por bloques. `progreso(bytes_leidos)` se
    llama de vez en cuando (opcional). Si `cancelado()` devuelve True, aborta y retorna None.
    Devuelve el hash en hexadecimal, o None si se canceló."""
    h = hashlib.sha256()
    leido = 0
    with open(ruta, "rb") as fh:
        while True:
            if cancelado is not None and cancelado():
                return None
            datos = fh.read(bloque)
            if not datos:
                break
            h.update(datos)
            leido += len(datos)
            if progreso is not None:
                progreso(leido)
    return h.hexdigest()


def sha256_bytes(datos: bytes) -> str:
    """SHA-256 de un bloque de bytes ya en memoria (para partes pequeñas)."""
    return hashlib.sha256(datos).hexdigest()
