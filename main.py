# -*- coding: utf-8 -*-
# SocialRSS Accessible Suite
# Copyright (C) 2026 hxebolax
#
# Este programa es software libre: puede redistribuirlo y/o modificarlo bajo los términos de la
# Licencia Pública General de GNU publicada por la Free Software Foundation, ya sea la versión 3
# de la Licencia o (a su elección) cualquier versión posterior. Se distribuye con la esperanza de
# que sea útil, pero SIN NINGUNA GARANTÍA; ni siquiera la garantía implícita de COMERCIABILIDAD o
# IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte el archivo LICENSE y el permiso adicional para
# la biblioteca BASS que se describe en README.md (apartado Licencia).
"""
Punto de entrada principal para SocialRSS Accessible Suite.
Inicializa el entorno, base de datos, configuraciones, traducciones, sonidos y lanza la UI.
"""

import sys
import os
import logging
import traceback
import warnings

# Silenciar un aviso BENIGNO de requests: la versión de chardet que arrastra readability-lxml
# (7.x) no coincide con la que requests "soporta", pero requests usa charset_normalizer (presente),
# así que todo funciona. El aviso solo ensuciaba la salida y los logs en cada arranque.
warnings.filterwarnings("ignore", message=r".*chardet.*", module="requests")

# El GIL de Python deja a cada hilo retener el intérprete hasta 5 ms (valor por defecto) antes de
# cederlo. Durante el arranque, varios hilos de fondo hacen trabajo de CPU a la vez (parsear el HTML
# de cientos de artículos/toots, cargar los SDK pesados la primera vez), y con turnos de 5 ms el hilo
# de la INTERFAZ tardaba en recuperar el GIL: el lector de pantalla (NVDA/JAWS), que consulta la UI en
# ese hilo, se quedaba mudo unos segundos. Bajar el intervalo a 1 ms hace que el GIL rote hacia la UI
# mucho más a menudo, así el lector responde aunque haya descargas/parsing en curso. El coste de
# cambiar de hilo más seguido es despreciable para una app de escritorio. (La prioridad baja de los
# hilos de fondo NO bastaba: es de Windows y no influye en cómo Python reparte el GIL.)
sys.setswitchinterval(0.001)

# Red de seguridad GLOBAL para cualquier socket sin timeout propio: algunas bibliotecas
# (feedparser al descargar, urllib en general) abren conexiones SIN límite de tiempo y un
# servidor mudo podía dejar un hilo de fondo colgado para siempre. requests NO se ve
# afectado (siempre pasamos timeout explícito); esto solo protege a las que no lo ponen.
import socket
socket.setdefaulttimeout(60)

# Añadir directorio raíz a sys.path por si acaso
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.paths import inicializar_directorios, asegurar_bin_en_path, get_data_path

log = logging.getLogger(__name__)


def _registrar_fallo_arranque(exc: BaseException):
    """Deja un archivo de diagnóstico incluso si el logger normal no llegó a arrancar."""
    try:
        inicializar_directorios()
        ruta = get_data_path("logs/startup_error.log")
        with open(ruta, "a", encoding="utf-8") as fh:
            fh.write("\n=== Fallo de arranque ===\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass


def main():
    # Instrumentación de FASES del arranque (auditoría 2026-07 §7.4): cada fase se
    # apunta con perf_counter y al final se vuelca un resumen en una línea (INFO).
    # Sirve para saber QUÉ optimizar antes de tocar nada (imports, BD, UI...).
    import time as _time
    _fases = []
    _marca = [_time.perf_counter()]

    def _fase(nombre):
        ahora = _time.perf_counter()
        _fases.append((nombre, ahora - _marca[0]))
        _marca[0] = ahora

    # 1. Asegurar directorios iniciales
    inicializar_directorios()
    # Asegurar que data/bin (ffmpeg) esté en el PATH del proceso
    asegurar_bin_en_path()

    # 1 bis. Aplicar una importación de migración pendiente ANTES de abrir la base de
    # datos o cargar la configuración (único momento en que nada las tiene abiertas).
    try:
        from app.core.migration import aplicar_importacion_pendiente
        if aplicar_importacion_pendiente():
            log.info("Se aplicó una importación de migración en el arranque.")
    except Exception as e:
        log.warning(f"No se pudo aplicar la importación pendiente: {e}")

    # 1 ter. Aplicar una restauración de copia de seguridad pendiente, también antes
    # de abrir la base de datos (la restauración nunca sustituye ficheros en uso).
    try:
        from app.core.backup import aplicar_restauracion_pendiente
        if aplicar_restauracion_pendiente():
            log.info("Se aplicó una restauración de copia de seguridad en el arranque.")
    except Exception as e:
        log.warning(f"No se pudo aplicar la restauración pendiente: {e}")

    # 2. Configurar logger
    from app.core.config_manager import config
    from app.core.logger import configurar_logging

    debug_mode = config.get("general", "debug", False)
    configurar_logging(debug_mode=debug_mode)
    _fase("directorios+config+logger")

    # Migración de plantillas a la separación POR RED (conserva lo que el usuario configuró).
    try:
        from app.core.plantillas import migrar_plantillas
        if migrar_plantillas(config):
            config.save()
            log.info("Plantillas migradas al sistema por red social.")
    except Exception as e:
        log.debug(f"No se pudo migrar plantillas por red: {e}")

    log.info("Iniciando SocialRSS Accessible Suite...")

    # 3. wx y la App lo ANTES posible (solo se importa wx, que es ligero) para
    # poder lanzar el aviso de inicio antes de las importaciones pesadas
    # (mastodon, atproto, pyrogram...) que son las que tardan en la versión
    # compilada. Así el usuario oye el mensaje nada más abrir el ejecutable.
    import wx
    from app.core.speech import speak

    app = wx.App(False)

    # 3 pre. Forzar la locale de wxWidgets a ESPAÑOL para que los botones de los
    # diálogos estándar (Aceptar, Cancelar, Sí, No, Guardar...) salgan SIEMPRE en
    # español, sin depender del idioma de Windows (antes salían "OK"/"Cancel"). Se
    # guarda la referencia en una global para que no la recoja el recolector de basura.
    global _wx_locale
    try:
        _wx_locale = wx.Locale(wx.LANGUAGE_SPANISH)
    except Exception as e:
        log.warning(f"No se pudo fijar la locale de wx a español: {e}")
        _wx_locale = None

    # 3 bis. Permitir una sola instancia de la aplicación (antes de dar el aviso).
    global _instance_checker
    _instance_checker = wx.SingleInstanceChecker("SocialRSSAccessibleSuite-" + wx.GetUserId())
    if _instance_checker.IsAnotherRunning():
        log.warning("Ya hay otra instancia en ejecución. Trayéndola al frente y saliendo.")
        # Traer al frente la ventana de la instancia que ya estaba abierta, para que llamar a
        # la app desde un atajo la enfoque directamente (lo pedían los usuarios). Se hacen DOS
        # cosas complementarias: dejar la ORDEN de mostrar (la instancia viva la recoge con su
        # temporizador y se restaura con su propio código: destruye el icono de la bandeja,
        # reactiva el streaming y lo anuncia; imprescindible cuando estaba oculta en la bandeja)
        # y el forzado por user32, que pone la ventana en primer plano al instante.
        traida = False
        orden_dejada = False
        try:
            from app.core.single_instance import dejar_orden_mostrar, traer_instancia_existente_al_frente
            orden_dejada = dejar_orden_mostrar()
            traida = traer_instancia_existente_al_frente()
        except Exception:
            pass
        if traida or orden_dejada:
            speak("SocialRSS ya estaba abierto. Mostrando su ventana.", interrupt=True)
            return
        speak("SocialRSS ya se está ejecutando.", interrupt=True)
        # Último recurso (ni orden ni forzado funcionaron): el aviso de siempre.
        try:
            dlg = wx.MessageDialog(None, "SocialRSS Accessible Suite ya se está ejecutando.",
                                   "Aplicación en ejecución", wx.ICON_INFORMATION)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception:
            pass
        return

    # 4. Aviso de inicio SOLO por voz (sin ninguna ventana). La voz se emite de
    # forma SÍNCRONA antes de las importaciones pesadas: el lector de pantalla es
    # otro proceso y locuta el mensaje aunque a continuación nos quedemos ocupados.
    #
    # IMPORTANTE: no se muestra ningún cuadro "espere por favor" durante la carga.
    # Una ventana visible mientras el hilo principal está congelado (cargando los
    # módulos pesados) se marca como "no responde" y el lector de pantalla se
    # bloquea al intentar leerla, dejando de responder hasta que aparece el frame.
    # Al no haber ventana, el lector sigue libre leyendo el escritorio mientras
    # carga, y la ventana principal solo se muestra cuando ya está lista.
    speak("Iniciando SocialRSS Accessible Suite. Espere por favor.", interrupt=True, sync=True)

    _fase("wx+aviso de inicio")

    # 5. Importaciones pesadas y preparación (sin ventana en pantalla).
    from app.core.migrations import ejecutar_migraciones
    from app.core.i18n import inicializar_traducciones
    from app.services.audio.sound_manager import sound_manager
    from app.ui.main_frame import MainFrame
    _fase("imports pesados (main_frame y servicios)")

    # 5 bis bis. Aplicar el proxy de red configurado (antes de cualquier conexión).
    try:
        from app.core.net import aplicar_configuracion_proxy
        aplicar_configuracion_proxy()
    except Exception as e:
        log.warning(f"No se pudo aplicar la configuración de proxy: {e}")

    # 5 bis. Inicializar base de datos y correr migraciones SQLite
    try:
        ejecutar_migraciones()
    except Exception as e:
        log.critical(f"Error crítico al ejecutar las migraciones de la base de datos: {e}")
        # Intentar verbalizar el error crítico antes de abortar
        speak("Error grave al inicializar la base de datos. Saliendo.", interrupt=True)
        sys.exit(1)

    # 5 bis-1b. Purga de contenido de Reddit borrado/eliminado (política de datos de Reddit): se
    # hace ANTES de compactar para que el VACUUM recupere el espacio liberado.
    try:
        from app.core.data_cleanup import purgar_reddit_borrados
        _purgados = purgar_reddit_borrados()
        if _purgados:
            log.info(f"Reddit: purgados {_purgados} elementos borrados de la caché local.")
    except Exception as e:
        log.debug(f"No se pudo purgar el contenido borrado de Reddit: {e}")

    # 5 bis-2. Compactar la base si quedó hinchada (VACUUM). AQUÍ es el momento seguro: aún no hay
    # hilos de servicio ni conexiones de lectura, así que el fichero encoge de verdad. Sin esto, los
    # borrados dejan espacio muerto que el VACUUM en pleno uso no logra recuperar (la base puede
    # llegar a pesar 6-7 veces su contenido real).
    try:
        from app.core.database import db as _db
        _db.compactar_si_procede()
    except Exception as e:
        log.warning(f"No se pudo compactar la base de datos al arranque: {e}")

    # 5 ter. Copia de seguridad automática si toca (no bloquea el arranque)
    try:
        from app.core.backup import comprobar_backup_automatico
        comprobar_backup_automatico()
    except Exception as e:
        log.warning(f"No se pudo comprobar la copia automática: {e}")

    _fase("BD: migraciones+compactación")

    # 5 quater. Si el arranque automático con Windows está activado, corregir su ruta por si la
    # instalación se ha movido (o se ha pasado de código fuente a .exe): la entrada del registro
    # apuntaría a una ruta inexistente y SocialRSS dejaría de abrirse con el equipo en silencio.
    try:
        from app.core import windows_startup
        windows_startup.sincronizar_ruta()
    except Exception as e:
        log.debug(f"No se pudo sincronizar el arranque con Windows: {e}")

    # 6. Configurar localización (gettext)
    inicializar_traducciones()

    # 6 bis. Inicializar motor de audio basskit
    if not sound_manager.inicializar():
        log.warning("El sistema de audio basskit no se pudo inicializar. La suite se ejecutará sin sonidos.")
    _fase("i18n+audio")

    # 7. Crear la ventana principal (ya con todo cargado). Si el usuario marcó "iniciar
    # en la bandeja", NO se muestra: iría a primer plano un instante y volvería a
    # esconderse (o directamente se quedaba delante, que era el fallo reportado).
    frame = MainFrame()
    iniciar_en_bandeja = config.get("general", "start_minimized", False)
    if not iniciar_en_bandeja:
        frame.Show(True)
    _fase("ventana principal (MainFrame)")
    try:
        total = sum(d for _, d in _fases)
        detalle = "; ".join(f"{n}: {d * 1000:.0f} ms" for n, d in _fases)
        log.info(f"Arranque en {total:.2f} s ({detalle}).")
    except Exception:
        pass
    # Guardar el HWND de la ventana para que una segunda instancia pueda traerla al frente.
    try:
        from app.core.single_instance import guardar_hwnd
        guardar_hwnd(frame.GetHandle())
    except Exception as e:
        log.debug(f"No se pudo registrar el HWND de la instancia: {e}")

    # Vigilar la ORDEN de mostrar que deja una segunda instancia: al recogerla, la ventana se
    # restaura con el código propio del frame (si estaba en la bandeja, destruye el icono y
    # reactiva el streaming), que es lo que el forzado por user32 no puede hacer desde fuera.
    # Todo el cuerpo va protegido: este temporizador late durante toda la vida de la app.
    class _TemporizadorOrdenMostrar(wx.Timer):
        def Notify(self):
            try:
                if not frame:
                    return
                from app.core.single_instance import recoger_orden_mostrar
                if recoger_orden_mostrar():
                    frame.mostrar_desde_otra_instancia()
            except Exception:
                pass

    global _timer_orden_mostrar
    _timer_orden_mostrar = _TemporizadorOrdenMostrar()
    _timer_orden_mostrar.Start(1500)
    # Iniciar minimizado EN LA BANDEJA del sistema (como "hide GUI on launch" de TWBlue):
    # la ventana queda oculta con su icono de bandeja, y TODO el arranque normal
    # (streaming, sondeos, temporizadores) sigue funcionando por detrás. Ya no exige
    # tener activada además la minimización a bandeja: la opción manda por sí sola.
    if iniciar_en_bandeja:
        try:
            frame._crear_tray_icon()
        except Exception as e:
            log.debug(f"No se pudo crear el icono de bandeja al arrancar: {e}")
        speak("SocialRSS iniciado en la bandeja del sistema.", interrupt=False)
    else:
        # Traer la ventana AL FRENTE al arrancar: en Windows, una ventana recién creada a veces se
        # queda detrás de otras aplicaciones (bloqueo de primer plano del sistema). Forzamos el
        # primer plano con la misma técnica robusta que usa la segunda instancia. Se hace AQUÍ, antes
        # de arrancar el bucle (todavía no hay diálogo de carga ni lector leyendo), para que el
        # "truco de ALT" del forzado no envíe una pulsación ALT espuria mientras se lee un diálogo.
        try:
            frame.Raise()
            from app.core.single_instance import forzar_al_frente
            forzar_al_frente(frame.GetHandle())
        except Exception as e:
            log.debug(f"No se pudo traer la ventana al frente al arrancar: {e}")

    # 7 bis. Aviso "listo" al terminar de cargar (sonido + voz), si está activado
    if config.get("general", "ready_sound", True):
        try:
            from app.core.event_bus import event_bus
            event_bus.emit("sound_trigger", "app_start")
        except Exception:
            pass
        if config.get("general", "ready_message", True):
            speak("SocialRSS listo.", interrupt=False)

    # 8. Correr el bucle principal de wxPython
    log.info("Lanzando bucle principal wx.App.MainLoop().")
    app.MainLoop()
    
    # Al salir, asegurar que liberamos el motor de audio
    log.info("Saliendo de la aplicación, liberando recursos.")
    try:
        _timer_orden_mostrar.Stop()
    except Exception:
        pass
    sound_manager.liberar()
    try:
        from app.core.single_instance import borrar_hwnd
        borrar_hwnd()
    except Exception:
        pass

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _registrar_fallo_arranque(e)
        logging.getLogger(__name__).critical("Error fatal al iniciar la aplicación.", exc_info=True)
        sys.exit(1)
