# -*- coding: utf-8 -*-
"""Arranque de la interfaz GTK 4 (Linux). Equivale a main.py de la versión de Windows, sin wx."""

import logging
import os
import sys
import warnings

log = logging.getLogger(__name__)


def main(argv=None) -> int:
    warnings.filterwarnings("ignore", message=r".*chardet.*", module="requests")
    sys.setswitchinterval(0.001)
    import socket
    socket.setdefaulttimeout(60)

    from app.core.paths import inicializar_directorios, asegurar_bin_en_path
    inicializar_directorios()
    asegurar_bin_en_path()

    # Una importación de migración o una restauración pendientes se aplican ANTES de abrir la base de datos y la
    # configuración (es el único momento en que nada las tiene abiertas).
    try:
        from app.core.migration import aplicar_importacion_pendiente
        if aplicar_importacion_pendiente():
            log.info("Se aplicó una importación de migración en el arranque.")
    except Exception as e:
        log.warning("No se pudo aplicar la importación pendiente: %s", e)
    try:
        from app.core.backup import aplicar_restauracion_pendiente
        if aplicar_restauracion_pendiente():
            log.info("Se aplicó una restauración de copia de seguridad en el arranque.")
    except Exception as e:
        log.warning("No se pudo aplicar la restauración pendiente: %s", e)

    from app.core.config_manager import config
    from app.core.logger import configurar_logging
    configurar_logging(debug_mode=config.get("general", "debug", False))
    log.info("Iniciando SocialRSS Accessible Suite (GTK 4)...")

    # Que Orca vea la aplicación aunque el escritorio no active la accesibilidad por defecto.
    os.environ.setdefault("GTK_A11Y", "atspi")

    from app.core.i18n import inicializar_traducciones
    inicializar_traducciones()

    from app.core.migrations import ejecutar_migraciones
    try:
        ejecutar_migraciones()
    except Exception:
        log.critical("Error crítico al ejecutar las migraciones de la base de datos.", exc_info=True)
        return 1

    try:
        from app.core.net import aplicar_configuracion_proxy
        aplicar_configuracion_proxy()
    except Exception as e:
        log.warning(f"No se pudo aplicar la configuración de proxy: {e}")

    try:
        from app.core.backup import comprobar_backup_automatico
        comprobar_backup_automatico()
    except Exception as e:
        log.warning("No se pudo comprobar la copia automática: %s", e)

    from app.services.audio.sound_manager import sound_manager
    sound_manager.inicializar()

    from .aplicacion import Aplicacion
    app = Aplicacion()
    codigo = app.run(argv if argv is not None else sys.argv)

    try:
        sound_manager.liberar()
    except Exception:
        pass
    return codigo


if __name__ == "__main__":
    sys.exit(main())
