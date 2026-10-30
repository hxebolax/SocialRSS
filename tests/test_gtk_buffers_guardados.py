# -*- coding: utf-8 -*-
"""Buffers abiertos por el usuario (conversaciones, cronologías, búsquedas) que se conservan entre sesiones en GTK."""
import pytest

from app.core.config_manager import config
from app.gtk import buffers_guardados as bg, datos_bluesky, datos_mastodon, datos_wsocial


@pytest.fixture(autouse=True)
def _limpio():
    claves = ("saved_searches", "user_timelines", "thread_buffers")
    for red in ("mastodon", "bluesky", "wsocial"):
        for c in claves:
            config.set(red, c, [])
    yield
    for red in ("mastodon", "bluesky", "wsocial"):
        for c in claves:
            config.set(red, c, [])


def test_mastodon_guarda_con_el_formato_de_windows():
    n = datos_mastodon.nodo_extra(7, "thread:123", "Conversación de Aurora")
    assert n.timeline == "thread:123" and n.cerrable and n.titulo == "Conversación de Aurora"
    datos_mastodon.nodo_extra(7, "user:aurora@m.social", "Publicaciones de @aurora@m.social")
    datos_mastodon.nodo_extra(7, "search:#python", "#python")
    assert config.get("mastodon", "thread_buffers") == [
        {"account_id": 7, "status_id": "123", "display": "Conversación de Aurora"}]
    assert config.get("mastodon", "user_timelines") == [
        {"account_id": 7, "acct": "aurora@m.social", "display": "aurora@m.social"}]
    assert config.get("mastodon", "saved_searches") == ["#python"]
    # otra cuenta no ve los hilos ni las cronologías de la primera, pero sí las búsquedas
    assert [n.timeline for n in bg.nodos("mastodon", 8)] == ["search:#python"]
    assert sorted(n.timeline for n in bg.nodos("mastodon", 7)) == ["search:#python", "thread:123", "user:aurora@m.social"]


def test_lo_guardado_por_windows_aparece_y_no_se_duplica():
    config.set("bluesky", "thread_buffers", [{"uri": "at://d/p/1", "display": "ana: hola", "origen": "at://d/p/2"}])
    config.set("bluesky", "user_timelines", [{"did": "did:x", "handle": "ana.bsky.social"}])
    assert sorted(n.timeline for n in bg.nodos("bluesky", 3)) == ["author:did:x", "thread:at://d/p/1"]
    datos_bluesky.nodo_extra(3, "thread:at://d/p/1", "Conversación de ana")
    assert len(config.get("bluesky", "thread_buffers")) == 1


def test_cerrar_quita_solo_ese_buffer():
    datos_wsocial.nodo_extra(1, "search:uno", "x")
    n = datos_wsocial.nodo_extra(1, "search:dos", "x")
    datos_wsocial.cerrar_extra(n)
    assert config.get("wsocial", "saved_searches") == ["uno"]


def test_mover_dentro_del_grupo():
    for t in ("a", "b", "c"):
        datos_mastodon.nodo_extra(1, f"search:{t}", t)
    assert bg.mover("mastodon", 1, "search:c", True)
    assert config.get("mastodon", "saved_searches") == ["a", "c", "b"]
    assert not bg.mover("mastodon", 1, "search:a", True)
    assert not bg.mover("mastodon", 1, "search:b", False)
