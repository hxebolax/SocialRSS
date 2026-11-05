# -*- coding: utf-8 -*-
from app.gtk.datos import Nodo
from app.gtk.orden_arbol import _intercambiar, _reordenar_en_sitio


def test_intercambiar_con_el_vecino_visible():
    # 'x' es una clave de la otra interfaz que no se ve: el intercambio salta por encima sin tocarla
    assert _intercambiar(["a", "x", "b", "c"], ["a", "b", "c"], "b", True) == ["b", "x", "a", "c"]
    assert _intercambiar(["a", "b"], ["a", "b", "c"], "b", False) == ["a", "c", "b"]


def test_intercambiar_en_los_extremos():
    assert _intercambiar(["a", "b"], ["a", "b"], "a", True) is None
    assert _intercambiar(["a", "b"], ["a", "b"], "b", False) is None
    assert _intercambiar(["a", "b"], ["a", "b"], "z", True) is None


def test_reordenar_respeta_los_huecos_de_lo_demas():
    nodos = [Nodo(k, "timeline", servicio="mastodon", timeline=k) for k in ("home", "local", "mentions")]
    nodos.insert(1, Nodo("hilo", "timeline", servicio="mastodon", timeline="thread:1", cerrable=True))
    _reordenar_en_sitio(nodos, lambda n: None if n.cerrable else n.timeline, ["mentions", "home", "local"])
    assert [n.timeline for n in nodos] == ["mentions", "thread:1", "home", "local"]


# --------------------------------------------------------------------------- X y Reddit
def _nodo(servicio, acc, timeline, cerrable=False):
    return Nodo(timeline, "timeline", servicio=servicio, account_id=acc, timeline=timeline, cerrable=cerrable)


def test_x_reordena_listas_y_busquedas_en_su_tabla():
    from app.core.database import db
    from app.core.migrations import ejecutar_migraciones
    from app.gtk.orden_arbol import _OrdenMixin
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('x', 'X', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (9101, '@yo', 'x', 1)")
    for i, nombre in enumerate(("uno", "dos", "tres")):
        db.execute("INSERT OR REPLACE INTO x_lists (id, account_id, list_id, name, position) VALUES (?, 9101, ?, ?, ?)",
                   (f"9101:l{i}", f"l{i}", nombre, i))
    db.execute("DELETE FROM x_searches WHERE account_id = 9101")
    for q in ("a", "b"):
        db.execute("INSERT INTO x_searches (account_id, name, query, position) VALUES (9101, ?, ?, 0)", (q, q))
    m = _OrdenMixin()
    hermanos = [_nodo("x", 9101, f"list:l{i}") for i in range(3)] + [_nodo("x", 9101, f"search:{q}", True) for q in "ab"]
    assert m._mover_x(hermanos[2], hermanos, True) is True
    orden = [r[0] for r in db.query("SELECT list_id FROM x_lists WHERE account_id = 9101 ORDER BY position")]
    assert orden == ["l0", "l2", "l1"]
    assert m._mover_x(hermanos[0], hermanos, True) is False          # ya es la primera de las listas
    assert m._mover_x(hermanos[3], hermanos, False) is True          # con posiciones repetidas se fijan al moverlas
    assert [r[0] for r in db.query("SELECT query FROM x_searches WHERE account_id = 9101 ORDER BY position, id")] == ["b", "a"]


def test_x_reordena_conversaciones_guardadas():
    from app.core.config_manager import config
    from app.gtk.orden_arbol import _OrdenMixin
    config.set("x", "thread_buffers", [{"id": "1", "account_id": 5}, {"id": "9", "account_id": 6}, {"id": "2", "account_id": 5}])
    hermanos = [_nodo("x", 5, "thread:1", True), _nodo("x", 5, "thread:2", True)]
    assert _OrdenMixin()._mover_x(hermanos[1], hermanos, True) is True
    assert [c["id"] for c in config.get("x", "thread_buffers")] == ["2", "9", "1"]
    config.set("x", "thread_buffers", [])


def test_reddit_reordena_subs_y_busquedas():
    from app.core.config_manager import config
    from app.gtk.orden_arbol import _OrdenMixin
    config.set("reddit", "saved_subreddits", [{"name": "a"}, {"name": "b"}])
    config.set("reddit", "saved_searches", ["x", "y"])
    m = _OrdenMixin()
    assert m._mover_reddit(_nodo("reddit", 1, "sub:b", True), True) is True
    assert [s["name"] for s in config.get("reddit", "saved_subreddits")] == ["b", "a"]
    assert m._mover_reddit(_nodo("reddit", 1, "search:x", True), False) is True
    assert config.get("reddit", "saved_searches") == ["y", "x"]
    assert m._mover_reddit(_nodo("reddit", 1, "search:y", True), True) is False
    config.set("reddit", "saved_subreddits", [])
    config.set("reddit", "saved_searches", [])
