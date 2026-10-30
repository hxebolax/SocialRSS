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


def _tiempos_cpu() -> dict:
    """{id nativo del hilo: (nombre, segundos de CPU)} de los hilos de Python vivos (Windows).

    Con la PILA no basta: si la interfaz solo dice "MainLoop" no se sabe si estaba OCUPADA en
    código nativo o ESPERANDO (el GIL, otro proceso...). Comparando la CPU consumida por cada hilo
    entre el ping y el volcado se ve quién trabajó de verdad durante el bloqueo."""
    datos = {}
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.OpenThread.restype = wintypes.HANDLE
        k32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetThreadTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(ctypes.c_ulonglong),) * 4
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        for t in threading.enumerate():
            tid = getattr(t, "native_id", None)
            if not tid:
                continue
            h = k32.OpenThread(0x0800, False, tid)  # THREAD_QUERY_LIMITED_INFORMATION
            if not h:
                continue
            try:
                c, e, kern, user = (ctypes.c_ulonglong() for _ in range(4))
                if k32.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e),
                                      ctypes.byref(kern), ctypes.byref(user)):
                    datos[tid] = (t.name, (kern.value + user.value) / 1e7)
            finally:
                k32.CloseHandle(h)
    except Exception:
        pass
    return datos


def _estado_gui_principal() -> str:
    """Si la interfaz está dentro de un bucle NATIVO (menú, mover/redimensionar...), Windows lo
    dice: en esos bucles wx no atiende sus eventos y la pila de Python solo muestra MainLoop."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                        ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                        ("rcCaret", wintypes.RECT)]

        u32 = ctypes.windll.user32
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not u32.GetGUIThreadInfo(threading.main_thread().native_id, ctypes.byref(info)):
            return ""
        nombres = [(0x04, "menú"), (0x10, "menú emergente"), (0x08, "menú del sistema"),
                   (0x02, "moviendo o redimensionando la ventana")]
        modos = [n for bit, n in nombres if info.flags & bit]

        def _clase(hwnd):
            if not hwnd:
                return "-"
            b = ctypes.create_unicode_buffer(128)
            u32.GetClassNameW(hwnd, b, 128)
            return b.value
        return (f"modo nativo: {', '.join(modos) if modos else 'ninguno'}; ventana activa: "
                f"{_clase(info.hwndActive)}; foco: {_clase(info.hwndFocus)}")
    except Exception:
        return ""


def _resumen_cpu(base: dict, transcurrido: float) -> str:
    """Texto con la CPU del hilo de la interfaz y de los hilos que más consumieron desde 'base'."""
    if not base:
        return ""
    ahora = _tiempos_cpu()
    principal = threading.main_thread().native_id
    deltas = []
    cpu_ui = None
    for tid, (nombre, cpu) in ahora.items():
        d = cpu - base.get(tid, ("", 0.0))[1]  # hilo nacido durante el bloqueo: toda su CPU
        if tid == principal:
            cpu_ui = d
        elif d >= 0.05:
            deltas.append((d, nombre))
    deltas.sort(reverse=True)
    partes = []
    if cpu_ui is not None:
        estado = ("OCUPADA en código nativo" if cpu_ui >= transcurrido * 0.6
                  else "ESPERANDO (GIL, un cerrojo u otro proceso)")
        partes.append(f"la interfaz usó {cpu_ui:.1f} s de CPU en {transcurrido:.1f} s: {estado}")
    if deltas:
        partes.append("más CPU: " + ", ".join(f"{n} {d:.1f} s" for d, n in deltas[:4]))
    gui = _estado_gui_principal()
    if gui:
        partes.append(gui)
    return "; ".join(partes)


def _bucle():
    from .plataforma import hay_interfaz, llamar_en_ui
    ultimo_aviso = 0.0
    ultima_pila_fina = 0.0
    while not _parar.wait(_INTERVALO):
        if not hay_interfaz():
            continue
        recibido = threading.Event()

        def _pong():
            recibido.set()

        base_cpu = _tiempos_cpu()
        enviado = time.monotonic()
        if not llamar_en_ui(_pong):
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
                resumen = _resumen_cpu(base_cpu, time.monotonic() - enviado)
                if resumen:
                    log.warning("Durante el bloqueo: %s.", resumen)
            recibido.wait(30.0)
        tardo = time.monotonic() - enviado
        if tardo >= _UMBRAL and (time.monotonic() - ultimo_aviso) > 10.0:
            ultimo_aviso = time.monotonic()
            resumen = _resumen_cpu(base_cpu, tardo)
            log.warning(
                "El hilo de la INTERFAZ estuvo bloqueado %.1f s (la ventana pudo aparecer "
                "como 'No responde'). Si coincide con el arranque o una acción concreta, "
                "esta marca de tiempo acota al culpable.%s", tardo,
                f" En total: {resumen}." if resumen else "")
        elif tardo >= 0.75:
            # LAG fino: sin llegar a congelación, este retraso ya encola las teclas del
            # lector (sus consultas a la ventana son síncronas). En INFO para poder ver
            # la distribución del lag en los reportes sin inundar el registro.
            log.info("Lag del hilo de la interfaz: %.2f s.", tardo)
