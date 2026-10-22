# -*- coding: utf-8 -*-
"""Coherencia de las casillas de activar/desactivar redes (Preferencias -> cada servicio).

Regresión: W Social se quedó fuera de la lista que GUARDA las casillas (estaba en la que
valida), así que desmarcarlo no tenía ningún efecto y la red volvía a aparecer al reiniciar.
Ahora Preferencias recorre feature_flags.SERVICIOS_CONMUTABLES, y estas pruebas verifican que
esa fuente única cuadra con las casillas que existen y con el resto de la aplicación.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import re
from pathlib import Path

from app.core.feature_flags import SERVICIOS_CONMUTABLES, servicio_habilitado

_UI = Path(__file__).resolve().parent.parent / "app" / "ui"


def _fuente(nombre):
    return (_UI / nombre).read_text(encoding="utf-8")


def test_todo_servicio_conmutable_tiene_su_casilla():
    """Cada red de la lista debe tener su casilla `cb_serv_<id>` en Preferencias; si no, el
    bucle de guardado la saltaría en silencio (que es justo lo que pasaba con W Social)."""
    fuente = _fuente("prefs_servicios.py")
    faltan = [s for s in SERVICIOS_CONMUTABLES if f"cb_serv_{s}" not in fuente]
    assert not faltan, f"sin casilla en Preferencias: {faltan}"


def test_todas_las_casillas_estan_en_la_lista():
    """Y al revés: una casilla que no esté en SERVICIOS_CONMUTABLES no se guardaría nunca."""
    fuente = _fuente("prefs_servicios.py")
    casillas = set(re.findall(r"self\.cb_serv_(\w+)\s*=", fuente))
    sobran = casillas - set(SERVICIOS_CONMUTABLES)
    assert not sobran, f"casillas que Preferencias no guarda: {sobran}"


def test_preferencias_no_repite_listas_de_servicios_a_mano():
    """El diálogo debe usar la fuente única en sus DOS bucles (validar y guardar)."""
    fuente = _fuente("preferences_dialog.py")
    assert fuente.count("SERVICIOS_CONMUTABLES") >= 2
    assert '("cb_serv_mastodon", "mastodon")' not in fuente, "vuelve a haber una lista a mano"


def test_wsocial_se_puede_apagar_y_encender(tmp_path, monkeypatch):
    """El caso reportado: desmarcar W Social debe dejarlo apagado de verdad."""
    from app.core import feature_flags
    from app.core.config_manager import config

    guardado = {}
    monkeypatch.setattr(config, "get",
                        lambda sec, cla, por_defecto=None: guardado.get((sec, cla), por_defecto))
    monkeypatch.setattr(config, "set",
                        lambda sec, cla, val: guardado.__setitem__((sec, cla), val))
    # La librería de W Social puede no estar instalada en el entorno de pruebas: lo que se
    # comprueba aquí es la preferencia, así que se da por disponible.
    monkeypatch.setattr(feature_flags, "wsocial_libreria_instalada", lambda: True)

    feature_flags.fijar_servicio_habilitado("wsocial", False)
    assert "wsocial" in guardado[("general", "servicios_desactivados")]
    assert feature_flags.servicio_habilitado("wsocial") is False
    feature_flags.fijar_servicio_habilitado("wsocial", True)
    assert feature_flags.servicio_habilitado("wsocial") is True


def test_apagar_una_red_no_afecta_a_las_demas(monkeypatch):
    from app.core import feature_flags
    from app.core.config_manager import config

    guardado = {}
    monkeypatch.setattr(config, "get",
                        lambda sec, cla, por_defecto=None: guardado.get((sec, cla), por_defecto))
    monkeypatch.setattr(config, "set",
                        lambda sec, cla, val: guardado.__setitem__((sec, cla), val))
    for nombre in ("wsocial", "peertube", "reddit", "irc", "matrix", "xmpp"):
        monkeypatch.setattr(feature_flags, f"{nombre}_libreria_instalada", lambda: True)

    feature_flags.fijar_servicio_habilitado("wsocial", False)
    assert feature_flags.servicio_habilitado("mastodon") is True
    assert feature_flags.servicio_habilitado("rss") is True
