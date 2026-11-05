# -*- coding: utf-8 -*-
"""Buffers que abre el usuario y que se conservan entre sesiones (Mastodon, Bluesky y W Social): conversaciones, cronologías
de otras cuentas y búsquedas.

Se guardan donde los guarda la versión de Windows (`<red>.thread_buffers`, `<red>.user_timelines` y `<red>.saved_searches`), así
que unos y otros aparecen en las dos interfaces. Cada red tiene su formato de entrada; aquí se describe una vez."""

from typing import List

from app.core.config_manager import config
from .datos import Nodo


def _etiqueta_hilo(e: dict) -> str:
    texto = e.get("display") or e.get("titulo") or ""
    return texto if texto.startswith(_("Conversación")) else _("Conversación: {t}").format(t=texto)


def _hilo_mastodon(cuenta, valor, titulo):
    return {"account_id": cuenta, "status_id": valor, "display": titulo}


def _hilo_at(cuenta, valor, titulo):
    return {"uri": valor, "display": titulo, "origen": valor}


def _hilo_wsocial(cuenta, valor, titulo):
    return {"uri": valor, "titulo": titulo, "account_id": cuenta}


def _usuario_mastodon(cuenta, valor, titulo):
    return {"account_id": cuenta, "acct": valor, "display": valor}


def _usuario_at(cuenta, valor, titulo):
    return {"did": valor, "handle": titulo.rsplit("@", 1)[-1] if "@" in titulo else valor, "account_id": cuenta}


def _titulo_usuario(e: dict) -> str:
    return _("Publicaciones de @{u}").format(u=(e.get("acct") or e.get("handle") or e.get("did") or "").lstrip("@"))


# servicio -> prefijo de la clave de buffer -> (clave de configuración, campo que identifica la entrada, cómo se crea,
# cómo se titula, si la entrada es de una cuenta concreta). Las búsquedas son una lista de textos de todas las cuentas.
_SPECS = {
    "mastodon": {
        "search:": ("saved_searches", None, None, None, False),
        "user:": ("user_timelines", "acct", _usuario_mastodon, _titulo_usuario, True),
        "thread:": ("thread_buffers", "status_id", _hilo_mastodon, _etiqueta_hilo, True),
    },
    "bluesky": {
        "search:": ("saved_searches", None, None, None, False),
        "author:": ("user_timelines", "did", _usuario_at, _titulo_usuario, True),
        "thread:": ("thread_buffers", "uri", _hilo_at, _etiqueta_hilo, False),
    },
    "wsocial": {
        "search:": ("saved_searches", None, None, None, False),
        "author:": ("user_timelines", "did", _usuario_at, _titulo_usuario, True),
        "thread:": ("thread_buffers", "uri", _hilo_wsocial, _etiqueta_hilo, True),
    },
}


def guarda(servicio: str, timeline: str) -> bool:
    return any(timeline.startswith(p) for p in _SPECS.get(servicio, {}))


def _spec(servicio: str, timeline: str):
    for prefijo, spec in _SPECS.get(servicio, {}).items():
        if timeline.startswith(prefijo):
            return prefijo, spec
    return None, None


def _de_la_cuenta(e, cuenta: int, por_cuenta: bool) -> bool:
    return not por_cuenta or not isinstance(e, dict) or e.get("account_id") in (None, cuenta)


def _valor(e, campo):
    return e if campo is None else (str(e.get(campo, "")) if isinstance(e, dict) else "")


def _entradas(servicio: str, cuenta: int, prefijo: str) -> list:
    """(índice en la lista guardada, entrada) de las que se ven bajo esta cuenta, en el orden guardado."""
    clave, campo, _n, _t, por_cuenta = _SPECS[servicio][prefijo]
    todas = list(config.get(servicio, clave, []) or [])
    return [(i, e) for i, e in enumerate(todas)
            if _de_la_cuenta(e, cuenta, por_cuenta) and _valor(e, campo) and (campo is None or isinstance(e, dict))]


def nodos(servicio: str, cuenta: int) -> List[Nodo]:
    """Los buffers guardados que cuelgan de una cuenta."""
    res = []
    for prefijo, (_c, campo, _n, titulo, _p) in _SPECS.get(servicio, {}).items():
        for _i, e in _entradas(servicio, cuenta, prefijo):
            valor = _valor(e, campo)
            texto = _("Búsqueda: {t}").format(t=valor) if campo is None else titulo(e)
            res.append(Nodo(texto, "timeline", servicio=servicio, account_id=cuenta, timeline=prefijo + valor,
                            cerrable=True))
    return res


def guardar(servicio: str, cuenta: int, timeline: str, titulo: str):
    """Guarda el buffer (si falta). Devuelve su título definitivo."""
    prefijo, spec = _spec(servicio, timeline)
    clave, campo, nueva, etiqueta, _p = spec
    valor = timeline[len(prefijo):]
    existentes = [v for _i, e in _entradas(servicio, cuenta, prefijo) for v in (_valor(e, campo),)]
    entrada = valor if campo is None else nueva(cuenta, valor, titulo)
    if valor not in existentes:
        config.set(servicio, clave, list(config.get(servicio, clave, []) or []) + [entrada])
    return _("Búsqueda: {t}").format(t=valor) if campo is None else etiqueta(entrada)


def quitar(servicio: str, cuenta: int, timeline: str):
    prefijo, spec = _spec(servicio, timeline)
    clave, campo, _n, _t, _p = spec
    valor = timeline[len(prefijo):]
    quitadas = {i for i, e in _entradas(servicio, cuenta, prefijo) if _valor(e, campo) == valor}
    config.set(servicio, clave, [e for i, e in enumerate(config.get(servicio, clave, []) or []) if i not in quitadas])


def mover(servicio: str, cuenta: int, timeline: str, subir: bool) -> bool:
    """Cambia de sitio un buffer guardado con el vecino de su mismo grupo (búsquedas, cronologías o conversaciones)."""
    prefijo, spec = _spec(servicio, timeline)
    clave, campo, _n, _t, _p = spec
    valor = timeline[len(prefijo):]
    visibles = _entradas(servicio, cuenta, prefijo)
    pos = next((k for k, (_i, e) in enumerate(visibles) if _valor(e, campo) == valor), None)
    if pos is None:
        return False
    otro = pos - 1 if subir else pos + 1
    if not 0 <= otro < len(visibles):
        return False
    todas = list(config.get(servicio, clave, []) or [])
    a, b = visibles[pos][0], visibles[otro][0]
    todas[a], todas[b] = todas[b], todas[a]
    config.set(servicio, clave, todas)
    return True
