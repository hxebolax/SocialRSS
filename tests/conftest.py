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


def pytest_sessionfinish(session, exitstatus):
    """Borra la carpeta temporal de datos al terminar todas las pruebas."""
    shutil.rmtree(_TMP_DATA_DIR, ignore_errors=True)
