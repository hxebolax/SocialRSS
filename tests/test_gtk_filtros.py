# -*- coding: utf-8 -*-
"""Los filtros de contenido «Ocultar» quitan publicaciones de los buffers de la interfaz GTK."""
import pytest

from app.core.content_filters import ContentFilterEngine
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk.filtros_contenido import sin_ocultas  # noqa: E402

FILAS = [("1", "a", "b", "c", "ana", "Hola mundo"), ("2", "a", "b", "c", "bob", "Spoiler del final"),
         ("3", "a", "b", "c", "spamero", "Otra cosa")]


@pytest.fixture
def limpio():
    ejecutar_migraciones()
    for f in ContentFilterEngine.listar_todos():
        ContentFilterEngine.eliminar(f["id"])
    yield
    for f in ContentFilterEngine.listar_todos():
        ContentFilterEngine.eliminar(f["id"])


def test_sin_filtros_deja_todo(limpio):
    assert sin_ocultas("bluesky", FILAS, 5, 4) == FILAS


def test_oculta_por_texto_y_por_autor(limpio):
    ContentFilterEngine.crear("bluesky", "spoiler", "spoiler", False, "text", "hide")
    ContentFilterEngine.crear("all", "spamero", "spamero", False, "author", "hide")
    assert [r[0] for r in sin_ocultas("bluesky", FILAS, 5, 4)] == ["1"]


def test_filtro_de_otra_red_no_aplica(limpio):
    ContentFilterEngine.crear("mastodon", "spoiler", "spoiler", False, "text", "hide")
    assert len(sin_ocultas("bluesky", FILAS, 5, 4)) == 3


def test_acciones_que_no_ocultan_se_ignoran(limpio):
    ContentFilterEngine.crear("bluesky", "hola", "hola", False, "text", "star")
    assert len(sin_ocultas("bluesky", FILAS, 5, 4)) == 3


def test_html_se_filtra_como_texto(limpio):
    ContentFilterEngine.crear("mastodon", "secreto", "^secreto", True, "text", "hide")
    filas = [("1", "a", "b", "c", "ana", "<p>secreto de estado</p>")]
    assert sin_ocultas("mastodon", filas, 5, 4, html=True) == []
