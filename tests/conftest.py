# -*- coding: utf-8 -*-
"""Configuración de pytest: aísla las pruebas en una carpeta data/ temporal.

Sin esto, los tests escribían en la configuración REAL del usuario (data/config/settings.json)
a través de config.set(), pudiendo dejar claves alteradas (p. ej. general.service_order). Aquí
se fija SOCIALRSS_DATA_DIR a un directorio temporal ANTES de importar cualquier módulo de la app,
de modo que paths.py resuelve DATA_DIR al temporal y ni la configuración ni la base de datos de
pruebas tocan los datos reales. El temporal se borra al terminar la sesión de pytest.
"""
import os
import shutil
import tempfile

# Debe ejecutarse en el import de conftest (antes de que los módulos de prueba importen la app).
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="socialrss_test_data_")
os.environ["SOCIALRSS_DATA_DIR"] = _TMP_DATA_DIR

# Crear la estructura mínima de carpetas en el temporal (importa paths ya con la variable puesta).
from app.core.paths import inicializar_directorios  # noqa: E402
inicializar_directorios()

# Instalar la función de traducción `_` como builtin (igual que hace la app al arrancar). El módulo
# i18n instala un fallback NullTranslations al importarse, de modo que `_()` existe siempre. Sin esto,
# el código de servicio que devuelve cadenas traducidas por la ruta normal fallaría con NameError.
import app.core.i18n  # noqa: E402,F401


# Las pruebas de la interfaz de Windows (wxPython) se SALTAN donde wx no está instalado (Linux, que usa GTK),
# en lugar de dar error. El núcleo y los servicios no pueden depender de wx (tests/test_nucleo_sin_wx.py), así
# que lo que falla por wx solo puede ser una prueba de la interfaz wx.
import importlib.util  # noqa: E402

import pytest  # noqa: E402

_HAY_WX = importlib.util.find_spec("wx") is not None
_MOTIVO_WX = "necesita wxPython (interfaz de Windows), que no está en este sistema"


def _falta_wx(exc) -> bool:
    return (not _HAY_WX and isinstance(exc, ModuleNotFoundError)
            and (exc.name == "wx" or (exc.name or "").startswith("wx.")))


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    informe = yield
    if (not _HAY_WX and informe.failed and isinstance(collector, pytest.Module)
            and "No module named 'wx'" in str(informe.longrepr)):
        informe.outcome = "skipped"
        informe.longrepr = (str(collector.path), 0, f"Skipped: {_MOTIVO_WX}")
    return informe


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    try:
        return (yield)
    except ModuleNotFoundError as e:
        if _falta_wx(e):
            pytest.skip(_MOTIVO_WX)
        raise


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except ModuleNotFoundError as e:
        if _falta_wx(e):
            pytest.skip(_MOTIVO_WX)
        raise


def pytest_sessionfinish(session, exitstatus):
    """Borra la carpeta temporal de datos al terminar todas las pruebas."""
    shutil.rmtree(_TMP_DATA_DIR, ignore_errors=True)
