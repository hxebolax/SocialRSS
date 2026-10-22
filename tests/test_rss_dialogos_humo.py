# -*- coding: utf-8 -*-
"""Humo de los diálogos nuevos o cambiados en las fases 1-5 de BlindRSS: se CONSTRUYEN sin
mostrarse (no roban el foco ni abren ventanas) para cazar errores de ejecución."""
import json

import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

ACC = 7301


@pytest.fixture(scope="module")
def app_wx():
    wx = pytest.importorskip("wx")
    app = wx.App.Get() or wx.App(False)
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'R', 'rss', 1)", (ACC,))
    db.execute("INSERT OR IGNORE INTO rss_feeds (id, account_id, title, url) VALUES (?, ?, 'F', 'https://f.test/rss')",
               (ACC, ACC))
    db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, published_at) "
               "VALUES ('h:1', ?, ?, 'T', 'https://f.test/1', '<p>x</p>', '2026-09-26')", (ACC, ACC))
    yield app


def _construir(clase, *args, **kw):
    dlg = clase(None, *args, **kw)
    dlg.Destroy()


def test_dialogos_rss(app_wx):
    from app.ui.rss_buscar_feeds import BuscarFeedsDialog
    from app.ui.rss_editar_feed import EditarFeedDialog
    from app.ui.rss_reglas_ui import (CarpetaInteligenteDialog, CarpetasInteligentesDialog,
                                      ReglaAvanzadaDialog, ReglasDialog)
    from app.ui.rss_versiones_dialog import VersionesArticuloDialog
    _construir(BuscarFeedsDialog, ACC)
    _construir(EditarFeedDialog, ACC)
    _construir(ReglasDialog, ACC)
    _construir(ReglaAvanzadaDialog, ACC)
    rid = db.execute_insert(
        "INSERT INTO rss_rules (account_id, title, pattern, field, action, active, conditions_json, match_mode, "
        "actions_json) VALUES (?, 'R', 'a', 'title', 'mark_read', 1, ?, 'expr:1 o 2', ?)",
        (ACC, json.dumps([{"field": "link", "op": "empieza", "pattern": "https"},
                          {"field": "title", "pattern": "b", "is_regex": True}]),
         json.dumps([{"action": "delete"}, {"action": "tag", "value": "99"}])))
    dlg = ReglaAvanzadaDialog(None, ACC, regla_id=rid)
    assert dlg.choice_modo.GetSelection() == 2 and dlg.txt_expr.GetValue() == "1 o 2"
    assert dlg.cb_acciones["delete"].GetValue()
    dlg.Destroy()
    _construir(CarpetasInteligentesDialog, ACC)
    dlg = CarpetaInteligenteDialog(None, ACC, carpeta={"name": "C", "criteria": {"author": "ana", "updated": 1}})
    assert dlg.txt_autor.GetValue() == "ana" and dlg.cb_corregidos.GetValue()
    dlg.Destroy()
    _construir(VersionesArticuloDialog, "h:1")


def test_dialogos_reproductor_y_tabla(app_wx):
    from app.core import texto_estructurado as te
    from app.ui.player.player2_dialog import Player2Dialog
    from app.ui.player.player2_extras import ColaPodcastsDialog, EcualizadorDialog
    from app.ui.rss_tabla import TablaDialog
    _construir(Player2Dialog)
    _construir(ColaPodcastsDialog)
    _construir(EcualizadorDialog)
    marca = te.html_a_estructura("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>").grupo(te.TABLA)[0]
    _construir(TablaDialog, marca)
