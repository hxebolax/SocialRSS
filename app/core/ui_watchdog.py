# -*- coding: utf-8 -*-
"""Vigilante de CONGELACIONES del hilo de la interfaz.

Un hilo de fondo manda cada pocos segundos un "ping" al hilo de la interfaz
(wx.CallAfter) y mide cuánto tarda en ejecutarse. Si tarda más del umbral, el hilo
de la interfaz estuvo BLOQUEADO ese tiempo (Windows lo marca "No responde" a partir
de ~5 s) y se deja constancia en el log con la duración: así un reporte de "se queda
colgada al arrancar" llega con la evidencia de cuándo y cuánto, en vez de a ciegas.

Sin coste apreciable: un CallAfter cada pocos segundos.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)

_INTERVALO = 2.0   # segundos entre pings
_UMBRAL = 3.0      # a partir de aquí se considera congelación y se loguea
# Lag FINO: si el ping no ha vuelto en este tiempo también se vuelca la pila (en INFO y como mucho
# una vez por minuto). Un bloqueo de 1-2 s ya atasca las teclas al escribir con el lector, y sin la
# pila el log solo decía "Lag de 1.36 s" sin pista del culpable.
_UMBRAL_PILA_FINA = 1.0
_PAUSA_PILA_FINA = 60.0

_hilo = None
_parar = threading.Event()


def iniciar():
    """Arranca el vigilante (una sola vez; llamadas repetidas no hacen nada)."""
    global _hilo
    if _hilo is not None and _hilo.is_alive():
        return
    _parar.clear()
    _hilo = threading.Thread(target=_bucle, name="ui-watchdog", daemon=True)
    _hilo.start()


def detener():
    _parar.set()


def _volcar_pila_hilo_principal(nivel=logging.WARNING):
    """Escribe en el log la PILA del hilo principal en este instante (si está bloqueado,
    la última línea dice en qué función/espera está atascado) y UNA línea por cada otro
    hilo con lo que está ejecutando: si el principal está simplemente hambriento de GIL,
    aquí se ve QUIÉN se lo come (imports de SDK, parseos...)."""
    import sys
    import traceback
    try:
        marcos = sys._current_frames()
        principal = threading.main_thread().ident
        marco = marcos.get(principal)
        if marco is not None:
            pila = "".join(traceback.format_stack(marco))
            log.log(nivel, "PILA del hilo de la interfaz durante el bloqueo (el culpable está "
                    "al final):\n%s", pila)
        # Resumen del resto de hilos: nombre -> última llamada (archivo:línea función).
        nombres = {t.ident: t.name for t in threading.enumerate()}
        lineas = []
        for ident, m in marcos.items():
            if ident == principal:
                continue
            tb = traceback.extract_stack(m)
            if tb:
                ult = tb[-1]
                lineas.append(f"  {nombres.get(ident, ident)}: "
                              f"{ult.filename.rsplit(chr(92), 1)[-1]}:{ult.lineno} {ult.name}")
        if lineas:
            log.log(nivel, "Qué hacían los demás hilos en ese instante:\n%s", "\n".join(lineas))
    except Exception:
        pass


def _bucle():
    import wx
    ultimo_aviso = 0.0
    ultima_pila_fina = 0.0
    while not _parar.wait(_INTERVALO):
        app = wx.App.Get()
        if app is None:
            continue
        recibido = threading.Event()

        def _pong():
            recibido.set()

        enviado = time.monotonic()
        try:
            wx.CallAfter(_pong)
        except Exception:
            continue  # la app se está cerrando
        # Primera espera corta: si no respondió en _UMBRAL_PILA_FINA, el hilo está atascado AHORA
        # MISMO: se vuelca su pila en INFO (limitado a una por minuto), porque al terminar ya no
        # se sabría quién fue. Luego, hasta el umbral: si sigue sin responder es congelación y se
        # vuelca otra vez en WARNING (la evidencia del culpable) y se sigue midiendo.
        if not recibido.wait(_UMBRAL_PILA_FINA):
            if (time.monotonic() - ultima_pila_fina) > _PAUSA_PILA_FINA:
                ultima_pila_fina = time.monotonic()
                _volcar_pila_hilo_principal(logging.INFO)
        if not recibido.wait(max(0.0, _UMBRAL - (time.monotonic() - enviado))):
            if (time.monotonic() - ultimo_aviso) > 10.0:
                _volcar_pila_hilo_principal()
            recibido.wait(30.0)
        tardo = time.monotonic() - enviado
        if tardo >= _UMBRAL and (time.monotonic() - ultimo_aviso) > 10.0:
            ultimo_aviso = time.monotonic()
            log.warning(
                "El hilo de la INTERFAZ estuvo bloqueado %.1f s (la ventana pudo aparecer "
                "como 'No responde'). Si coincide con el arranque o una acción concreta, "
                "esta marca de tiempo acota al culpable.", tardo)
        elif tardo >= 0.75:
            # LAG fino: sin llegar a congelación, este retraso ya encola las teclas del
            # lector (sus consultas a la ventana son síncronas). En INFO para poder ver
            # la distribución del lag en los reportes sin inundar el registro.
            log.info("Lag del hilo de la interfaz: %.2f s.", tardo)
