# -*- coding: utf-8 -*-
"""Bandeja de entrada unificada: lógica sin interfaz compartida por la ventana de wx y la de GTK."""
from datetime import datetime


def parse_fecha(s):
    """Convierte una fecha ISO en un datetime COMPARABLE entre redes, para ordenar la bandeja por
    tiempo real. CLAVE: respeta la zona horaria y la normaliza a hora local (igual que
    formatear_tiempo_relativo), porque cada red guarda la fecha distinto —Mastodon/Bluesky en UTC
    (con 'Z'), RSS a menudo en hora local—. Si no se respetara la zona, todas las de UTC quedarían
    sistemáticamente "más antiguas" y la bandeja saldría AGRUPADA POR RED en vez de mezclada por
    tiempo. Tolerante: si no se puede interpretar, devuelve la fecha mínima (va al final)."""
    if not s:
        return datetime.min
    ds = str(s).strip()
    if ds.endswith("Z"):
        ds = ds[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(ds)
    except ValueError:
        limpio = ds.replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(limpio[:26] if len(limpio) > 26 else limpio, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        # Normalizar a hora local SIN zona, para comparar de forma homogénea con las que vienen
        # ya en local (mismo criterio que el "hace X minutos" que se muestra).
        try:
            dt = dt.astimezone().replace(tzinfo=None)
        except (ValueError, OSError):
            dt = dt.replace(tzinfo=None)
    return dt
