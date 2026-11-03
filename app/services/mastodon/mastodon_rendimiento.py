# -*- coding: utf-8 -*-
"""Caché de anotaciones de tipos para Mastodon.py (bloqueos de la interfaz, 2026-11-03).

Mastodon.py 2.x convierte cada respuesta en objetos tipados (Status, Account, MediaAttachment...)
y, en CADA asignación de campo (`AttribAccessDict.__setitem__`), llama DOS veces a
`typing.get_type_hints()` (la de la clase y la de su `__init__`), que vuelve a evaluar todas las
anotaciones de la clase desde cero. Medido: 40 toots = 1,7 s de CPU en Python puro.

Con tres cuentas, varias líneas de tiempo sondeadas cada minuto y el streaming, eso eran decenas
de segundos de CPU por minuto en hilos de fondo. En Python solo un hilo ejecuta código a la vez
(GIL): el hilo de la interfaz tenía que esperar turno en cada llamada a wx, la ventana se quedaba
"No responde" medio minuto y NVDA, que consulta la ventana de forma síncrona, se congelaba con
ella (sus registros de "freeze recovery" coincidían al segundo con los del vigilante de la app).

Las anotaciones de una clase no cambian en tiempo de ejecución, así que se calculan una vez por
clase y se reutilizan (copia del dict, porque Mastodon.py lo modifica con `update`). Mismos tipos
de salida; 30 veces menos CPU.
"""
import logging

log = logging.getLogger(__name__)

_aplicado = False


def aplicar():
    """Instala la caché en `mastodon.types_base` (idempotente; si la librería cambia, no hace nada)."""
    global _aplicado
    if _aplicado:
        return
    _aplicado = True
    try:
        from mastodon import types_base
        original = getattr(types_base, "get_type_hints", None)
        if original is None or getattr(original, "_socialrss_cache", False):
            return
        cache = {}

        def get_type_hints_cacheado(obj, *args, **kwargs):
            if args or kwargs:
                return original(obj, *args, **kwargs)
            try:
                hints = cache[obj]
            except KeyError:
                # Si falla, que falle como siempre (Mastodon.py lo captura) y sin cachear.
                hints = cache[obj] = original(obj)
            except TypeError:
                return original(obj)  # objeto no hashable: sin caché
            return dict(hints)

        get_type_hints_cacheado._socialrss_cache = True
        types_base.get_type_hints = get_type_hints_cacheado
    except Exception as e:
        log.debug(f"No se pudo instalar la caché de tipos de Mastodon.py: {e}")
