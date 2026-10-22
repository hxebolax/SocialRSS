# -*- coding: utf-8 -*-
"""
Módulo para la salida de voz accesible e integración con lectores de pantalla.
Utiliza accessible_output2 para soportar NVDA, JAWS, SAPI5 y otros.
"""

import logging
import queue
import threading
from typing import Optional, Any
from .config_manager import config

log = logging.getLogger(__name__)

# Referencia global al motor de salida accesible
_output_api: Optional[Any] = None
_inicializado = False
_init_lock = threading.Lock()

# Cola y worker ÚNICO de voz: antes cada speak() creaba un hilo nuevo (threading.Thread por
# anuncio). En ráfagas (navegación rápida por la lista, lluvia de notificaciones) eso generaba
# muchos hilos efímeros y latencia impredecible. Ahora un solo hilo consume la cola en orden.
_speak_queue: "queue.Queue" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()

def inicializar_voz():
    """Inicializa la API de accessible_output2."""
    global _output_api, _inicializado
    with _init_lock:
        if _inicializado:
            return
        try:
            from accessible_output2.outputs.auto import Auto
            _output_api = Auto()
            log.info("Lector de pantalla inicializado con éxito mediante accessible_output2.")
        except Exception as e:
            log.error(f"No se pudo inicializar accessible_output2: {e}")
            _output_api = None
        _inicializado = True


def speak(text: str, interrupt: bool = True, sync: bool = False):
    """
    Envía un texto a verbalizar por el lector de pantalla activo o por SAPI5.

    Parámetros:
        text: El texto a verbalizar.
        interrupt: Si es True, detiene el habla actual inmediatamente.
        sync: Si es True, emite el texto en el hilo actual sin lanzar un hilo
            aparte ni aplicar el pequeño retardo. Se usa en el arranque, antes de
            las importaciones pesadas: si se dejara en un hilo demonio, el hilo
            principal acapararía el GIL durante esas importaciones y el mensaje no
            llegaría al lector hasta el final. El lector es otro proceso, así que
            recibe y locuta el mensaje aunque luego nos quedemos ocupados.
    """
    if not config.get("accessibility", "speech_announcements", True):
        log.debug(f"Speak (desactivado por config): {text}")
        return

    if not _inicializado:
        inicializar_voz()

    text = text.strip()
    if not text:
        return

    if sync:
        if _output_api:
            try:
                _output_api.speak(text, interrupt)
                if config.get("accessibility", "braille_announcements", True):
                    try:
                        _output_api.braille(text)
                    except Exception:
                        pass
            except Exception as e:
                log.error(f"Error al enviar voz (síncrona) mediante accessible_output2: {e}")
        return

    # Encolar para el worker único (no se crea un hilo por anuncio).
    _ensure_speak_worker()
    _speak_queue.put((text, interrupt))


def _ensure_speak_worker():
    """Arranca (una sola vez) el hilo que consume la cola de voz."""
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_speak_worker, name="SpeechWorker", daemon=True).start()
        _worker_started = True


def _speak_worker():
    """Consume la cola de voz en orden. Hace COALESCING de los anuncios interrumpibles: si llegan
    varios seguidos (navegación rápida por la lista), solo se locuta el último, que es el que el
    usuario realmente quiere oír; los intermedios se descartan en silencio. Los anuncios NO
    interrumpibles (aditivos) nunca se descartan."""
    import time
    global _output_api
    while True:
        text, interrupt = _speak_queue.get()
        if interrupt:
            # Saltar anuncios interrumpibles intermedios: el último gana. Si aparece uno aditivo
            # (interrupt=False), se respeta y se procesa.
            try:
                while True:
                    n_text, n_interrupt = _speak_queue.get_nowait()
                    text, interrupt = n_text, n_interrupt
                    if not n_interrupt:
                        break
            except queue.Empty:
                pass
        if not _output_api:
            continue
        try:
            # Pequeño retardo para que nuestro mensaje se emita JUSTO DESPUÉS del anuncio
            # automático del foco del lector y, al interrumpir, "gane" y se escuche.
            if interrupt:
                try:
                    retardo = int(config.get("accessibility", "speech_delay_ms", 120))
                except Exception:
                    retardo = 120
                if retardo > 0:
                    time.sleep(min(retardo, 600) / 1000.0)
            _output_api.speak(text, interrupt)
            # También enviamos a braille si el lector lo soporta y está activado
            if config.get("accessibility", "braille_announcements", True):
                try:
                    _output_api.braille(text)
                except Exception:
                    pass
        except Exception as e:
            log.error(f"Error al enviar voz mediante accessible_output2: {e}")


def notificar(text: str, titulo: str = None):
    """Entrega un mensaje IMPORTANTE (resultado de una operación, error o confirmación)
    según la preferencia accesibilidad → "modo_avisos":

      - "voz"     : lo dice el lector de pantalla (speak), como siempre.
      - "mensaje" : lo muestra en un CUADRO DE DIÁLOGO (wx), de modo que el usuario no se
                    pierde la información aunque tenga la voz desactivada.
      - "ambos"   : las dos cosas.

    Pensado SOLO para mensajes importantes; la lectura de navegación por listas sigue
    usando speak() y no se ve afectada. Es seguro llamarla desde cualquier hilo."""
    text = (text or "").strip()
    if not text:
        return
    modo = config.get("accessibility", "modo_avisos", "voz")
    if modo in ("voz", "ambos"):
        speak(text, interrupt=True)
    if modo in ("mensaje", "ambos"):
        _mostrar_aviso_dialogo(text, titulo)
    # Además de lo anterior, un TOAST nativo de Windows (Centro de actividades) si el usuario lo
    # activó. Útil cuando la app está en segundo plano: deja el aviso en el historial del sistema.
    # Es OPCIONAL e independiente del modo de avisos (no molesta si está desactivado, que es lo
    # de fábrica).
    if config.get("accessibility", "toast_windows", False):
        _mostrar_toast(text, titulo)


def _mostrar_toast(text: str, titulo: str = None):
    """Muestra una notificación nativa de Windows con wx.adv.NotificationMessage (Action Center).
    No requiere dependencias nuevas. Seguro desde cualquier hilo (se difiere al hilo de la UI)."""
    def _show():
        try:
            import wx
            import wx.adv
            if wx.GetApp() is None:
                return
            n = wx.adv.NotificationMessage(titulo or "SocialRSS", text)
            try:
                n.SetFlags(wx.ICON_INFORMATION)
            except Exception:
                pass
            n.Show(timeout=wx.adv.NotificationMessage.Timeout_Auto)
        except Exception as e:
            log.debug(f"No se pudo mostrar el toast de Windows: {e}")
    try:
        import wx
        if wx.GetApp() is None:
            return
        if wx.IsMainThread():
            _show()
        else:
            wx.CallAfter(_show)
    except Exception:
        pass


def _mostrar_aviso_dialogo(text: str, titulo: str = None):
    """Muestra un mensaje en un cuadro de diálogo de wx, en el hilo de la interfaz. Los
    botones salen en español gracias a la locale de wx fijada al arrancar."""
    def _show():
        try:
            import wx
            app = wx.GetApp()
            if app is None:
                return
            parent = app.GetTopWindow()
            dlg = wx.MessageDialog(parent, text, titulo or "SocialRSS Accessible Suite",
                                   wx.OK | wx.ICON_INFORMATION)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            log.debug(f"No se pudo mostrar el aviso en diálogo: {e}")
    try:
        import wx
        if wx.GetApp() is None:
            return
        if wx.IsMainThread():
            _show()
        else:
            wx.CallAfter(_show)
    except Exception:
        pass


def copiar_al_portapapeles(text: str, mensaje_anuncio: str = "Copiado al portapapeles"):
    """Copia una cadena al portapapeles de Windows de forma accesible."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        if mensaje_anuncio:
            speak(mensaje_anuncio)
        return True
    except Exception as e:
        log.error(f"Error al copiar al portapapeles: {e}")
        return False
