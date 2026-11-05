# -*- coding: utf-8 -*-
"""RSS en la interfaz GTK: carpetas inteligentes, búsquedas guardadas y etiquetas en el árbol, y los textos de las reglas."""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.rss.rss_reglas_textos import (condiciones_de_fila, describir_criterios, resumen_regla)

pytest.importorskip("gi")
from app.gtk import datos  # noqa: E402

ACC = 9971


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) VALUES (?, 'RSS prueba', 'rss', 1, 96)", (ACC,))
    for tabla in ("rss_smart_folders", "rss_saved_searches", "rss_tags"):
        db.execute(f"DELETE FROM {tabla} WHERE account_id = ?", (ACC,))
    yield ACC
    for tabla in ("rss_smart_folders", "rss_saved_searches", "rss_tags"):
        db.execute(f"DELETE FROM {tabla} WHERE account_id = ?", (ACC,))


def test_arbol_incluye_carpetas_inteligentes_y_busquedas(cuenta):
    from app.services.rss.rss_service import rss_service
    rss_service.crear_carpeta_inteligente(cuenta, "Sin leer", {"read": 0})
    rss_service.crear_busqueda_guardada(cuenta, "jirafas", "jirafas")
    titulos = {n.titulo: n.tipo for n in datos.construir_arbol(cuenta)}
    assert titulos["Sin leer"].startswith("smart:")
    assert titulos["Búsqueda: jirafas"].startswith("search:")


def test_etiqueta_sin_nada_etiquetado_no_sale_en_el_arbol(cuenta):
    from app.services.rss.rss_service import rss_service
    rss_service.crear_etiqueta(cuenta, "Vacía")
    assert not any(n.titulo == "#Vacía" for n in datos.construir_arbol(cuenta))


def test_buffer_oculto_de_rss(cuenta):
    from app.core.config_manager import config
    previo = config.get("rss", "hidden_buffers", [])
    try:
        config.set("rss", "hidden_buffers", ["today"])
        assert not any(n.tipo == "today" for n in datos.construir_arbol(cuenta))
        config.set("rss", "hidden_buffers", [])
        assert any(n.tipo == "today" for n in datos.construir_arbol(cuenta))
    finally:
        config.set("rss", "hidden_buffers", previo)


def test_condiciones_de_reglas_antiguas():
    conds = condiciones_de_fila("jirafa", "title", "")
    assert conds[0]["op"] == "contiene" and conds[0]["pattern"] == "jirafa"
    conds = condiciones_de_fila("^a.*z$", "title", "")
    assert conds[0]["op"] == "regex"


def test_resumen_de_regla():
    texto = resumen_regla([{"field": "title", "pattern": "x", "op": "contiene"}], "all", [{"action": "mark_read"}])
    assert "«x»" in texto and "→" in texto


def test_describir_criterios():
    assert "no leídos" in describir_criterios({"read": 0, "text": "cine"})
    assert "«cine»" in describir_criterios({"text": "cine"})
