# -*- coding: utf-8 -*-
"""Clientes (apps) de Mastodon cuyas publicaciones se ocultan de las listas.

Es una preferencia LOCAL de este equipo, común a todas las cuentas de Mastodon: el servidor no
sabe nada de ella, así que tampoco se puede consultar ni deshacer desde la web oficial. Se
gestiona en el menú Mastodon -> Clientes ignorados.
"""
from typing import List

from app.core.config_manager import config


def listar() -> List[str]:
    return list(config.get("mastodon", "ignored_clients", []) or [])


def esta_ignorado(cliente: str) -> bool:
    return bool(cliente) and cliente in listar()


def ignorar(cliente: str) -> bool:
    """Añade el cliente. Devuelve False si ya estaba o no se indica ninguno."""
    ignorados = listar()
    if not cliente or cliente in ignorados:
        return False
    ignorados.append(cliente)
    config.set("mastodon", "ignored_clients", ignorados)
    return True


def dejar_de_ignorar(cliente: str) -> bool:
    """Quita el cliente. Devuelve False si no estaba."""
    ignorados = listar()
    if cliente not in ignorados:
        return False
    ignorados.remove(cliente)
    config.set("mastodon", "ignored_clients", ignorados)
    return True
