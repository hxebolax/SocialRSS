# -*- coding: utf-8 -*-
"""
Interfaz accesible del reproductor multimedia de SocialRSS.
Permite controlar volumen, velocidad de reproducción sin distorsión (tempo),
desplazarse por la pista, gestionar la cola y escuchar estados de reproducción.
100% operable mediante teclado y lector de pantalla.
"""

import logging
import wx

from app.core.speech import speak, notificar
from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.services.media_player.player_service import player_service
from app.ui.accessible_controls import AccessibleListCtrl, AccessibleSlider

log = logging.getLogger(__name__)

class PlayerDialog(wx.Dialog):
    """Diálogo del reproductor multimedia accesible."""
    
    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Reproductor Multimedia Accesible"),
            size=(700, 500),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo del reproductor")
        )
        
        # Suscribirse a eventos del reproductor
        event_bus.subscribe("player_state_changed", self._on_state_changed)
        event_bus.subscribe("player_error", self._on_player_error)
        
        self._init_ui()
        
        # Timer para actualizar el indicador de posición
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer_tick, self.timer)
        self.timer.Start(500) # Cada 500ms
        
        self.CenterOnParent()
        
        # Registrar atajo de cierre
        self.Bind(wx.EVT_CLOSE, self._on_close)
        
        # Anuncio inicial
        event_bus.emit("sound_trigger", "player_opened")
        self._actualizar_info_pista()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 1. Información de la pista
        self.lbl_pista = wx.StaticText(
            self, 
            label=_("Pista: Ninguna cargada actualmente"),
            name=_("Información de pista")
        )
        sizer.Add(self.lbl_pista, 0, wx.ALL | wx.EXPAND, 10)
        
        self.lbl_progreso = wx.StaticText(
            self, 
            label=_("Progreso: 00:00 / 00:00 (0%)"),
            name=_("Progreso de reproducción")
        )
        sizer.Add(self.lbl_progreso, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        # 1 bis. Botones de transporte (con acelerador Alt+letra)
        sz_transport = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_play = wx.Button(self, label=_("&Reproducir / Pausar"), name=_("Reproducir o pausar"))
        self.btn_play.Bind(wx.EVT_BUTTON, self._on_btn_play_pause)
        sz_transport.Add(self.btn_play, 0, wx.ALL, 3)

        self.btn_stop = wx.Button(self, label=_("&Detener"), name=_("Detener"))
        self.btn_stop.Bind(wx.EVT_BUTTON, lambda e: player_service.detener())
        sz_transport.Add(self.btn_stop, 0, wx.ALL, 3)

        paso_b = int(self._paso_atras())
        self.btn_back = wx.Button(self, label=_("&Atrasar {0}s").format(paso_b),
                                  name=_("Retroceder {0} segundos").format(paso_b))
        self.btn_back.Bind(wx.EVT_BUTTON, lambda e: player_service.retroceder_segundos(self._paso_atras()))
        sz_transport.Add(self.btn_back, 0, wx.ALL, 3)

        paso_f = int(self._paso_adelante())
        self.btn_fwd = wx.Button(self, label=_("A&vanzar {0}s").format(paso_f),
                                 name=_("Avanzar {0} segundos").format(paso_f))
        self.btn_fwd.Bind(wx.EVT_BUTTON, lambda e: player_service.avanzar_segundos(self._paso_adelante()))
        sz_transport.Add(self.btn_fwd, 0, wx.ALL, 3)

        self.btn_prev = wx.Button(self, label=_("A&nterior"), name=_("Pista anterior"))
        self.btn_prev.Bind(wx.EVT_BUTTON, lambda e: (player_service.reproducir_anterior(), self._cargar_cola()))
        sz_transport.Add(self.btn_prev, 0, wx.ALL, 3)

        self.btn_next = wx.Button(self, label=_("&Siguiente"), name=_("Pista siguiente"))
        self.btn_next.Bind(wx.EVT_BUTTON, lambda e: (player_service.reproducir_siguiente(), self._cargar_cola()))
        sz_transport.Add(self.btn_next, 0, wx.ALL, 3)

        sizer.Add(sz_transport, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        # 2. Deslizadores de control (Volumen y Tempo)
        sz_sliders = wx.BoxSizer(wx.HORIZONTAL)
        
        # Volumen
        sz_vol = wx.BoxSizer(wx.VERTICAL)
        sz_vol.Add(wx.StaticText(self, label=_("Volumen:")), 0, wx.ALL, 5)
        vol_act = int(config.get("media_player", "volume", 0.8) * 100)
        self.slider_vol = AccessibleSlider(
            self, 
            value=vol_act, 
            minValue=0, 
            maxValue=100, 
            name=_("Volumen de reproducción"),
            unidad="%"
        )
        self.slider_vol.Bind(wx.EVT_SLIDER, self._on_vol_changed)
        sz_vol.Add(self.slider_vol, 0, wx.EXPAND)
        sz_sliders.Add(sz_vol, 1, wx.EXPAND | wx.ALL, 10)
        
        # Velocidad / Tempo: selector de valores fijos (0.5x, 0.75x, 1x, ...) en vez de
        # un deslizador, para que sea más fácil elegir una velocidad exacta con el lector.
        sz_speed = wx.BoxSizer(wx.VERTICAL)
        sz_speed.Add(wx.StaticText(self, label=_("&Velocidad de reproducción:")), 0, wx.ALL, 5)
        self._velocidades = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        self.choice_speed = wx.Choice(
            self,
            choices=[f"{v:g}x" for v in self._velocidades],
            name=_("Velocidad de reproducción"))
        self.choice_speed.Bind(wx.EVT_CHOICE, self._on_speed_changed)
        sz_speed.Add(self.choice_speed, 0, wx.EXPAND)
        sz_sliders.Add(sz_speed, 1, wx.EXPAND | wx.ALL, 10)
        self._sincronizar_velocidad()
        
        sizer.Add(sz_sliders, 0, wx.EXPAND)
        
        # 3. Cola de reproducción
        sizer.Add(wx.StaticText(self, label=_("Cola de reproducción:")), 0, wx.LEFT | wx.TOP, 10)
        self.lista_cola = AccessibleListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de reproducción activa")
        )
        self.lista_cola.InsertColumn(0, _("Título"), width=450)
        self.lista_cola.InsertColumn(1, _("Origen"), width=150)
        self.lista_cola.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_play_queue_item)
        
        sizer.Add(self.lista_cola, 1, wx.EXPAND | wx.ALL, 10)
        
        # Acciones de la cola
        sz_cola_btns = wx.BoxSizer(wx.HORIZONTAL)
        
        self.btn_subir = wx.Button(self, label=_("Subir"), name=_("Mover elemento arriba en cola"))
        self.btn_subir.Bind(wx.EVT_BUTTON, self._on_subir_cola)
        sz_cola_btns.Add(self.btn_subir, 0, wx.ALL, 2)
        
        self.btn_bajar = wx.Button(self, label=_("Bajar"), name=_("Mover elemento abajo en cola"))
        self.btn_bajar.Bind(wx.EVT_BUTTON, self._on_bajar_cola)
        sz_cola_btns.Add(self.btn_bajar, 0, wx.ALL, 2)
        
        self.btn_del = wx.Button(self, label=_("Quitar"), name=_("Quitar elemento de la cola"))
        self.btn_del.Bind(wx.EVT_BUTTON, self._on_quitar_cola)
        sz_cola_btns.Add(self.btn_del, 0, wx.ALL, 2)
        
        self.btn_clear = wx.Button(self, label=_("Limpiar Cola"), name=_("Vaciar cola de reproducción"))
        self.btn_clear.Bind(wx.EVT_BUTTON, self._on_limpiar_cola)
        sz_cola_btns.Add(self.btn_clear, 0, wx.ALL, 2)
        
        sizer.Add(sz_cola_btns, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        # Botones inferiores estándar
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("Cerrar"), name=_("Cerrar ventana del reproductor"))
        btn_sizer.Add(self.btn_cerrar, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        
        self.SetSizer(sizer)

        # El reproductor se controla con los BOTONES (no con teclas globales como
        # Espacio), para que no interfieran con la navegación accesible.

        self._cargar_cola()

    def _on_btn_play_pause(self, event):
        """Alterna reproducir/pausar según el estado actual."""
        try:
            estado = player_service.backend.obtener_estado()
        except Exception:
            estado = ""
        if estado == "reproduciendo":
            player_service.pausar()
            speak(_("Pausado."), interrupt=True)
        else:
            player_service.reproducir()
            speak(_("Reproduciendo."), interrupt=True)

    # --- HISTORIAL Y COLA ---
    def _cargar_cola(self):
        self.lista_cola.DeleteAllItems()
        self.lista_cola.limpiar_lecturas()
        items = player_service.cola.obtener_items()
        idx_act = player_service.cola.obtener_indice_actual()
        
        for idx, item in enumerate(items):
            title = item["title"]
            origin = item.get("service_origin", "").upper()
            if idx == idx_act:
                title = f"-> {title}"
                
            pos = self.lista_cola.InsertItem(idx, title)
            self.lista_cola.SetItem(pos, 1, origin)
            
            speech_desc = f"{item['title']}. Servicio {origin}."
            if idx == idx_act:
                speech_desc = _("Reproduciéndose actualmente: ") + speech_desc
                
            self.lista_cola.establecer_lectura(pos, speech_desc)

    def _on_play_queue_item(self, event):
        idx = event.GetIndex()
        items = player_service.cola.obtener_items()
        if 0 <= idx < len(items):
            player_service.cola.establecer_indice_actual(idx)
            item = items[idx]
            player_service.cargar_async(
                item["media_url"],
                item["title"],
                item["service_origin"],
                item["account_origin_id"],
                callback=lambda exito: player_service.reproducir() if exito else None
            )
            self._cargar_cola()

    def _on_subir_cola(self, event):
        idx = self.lista_cola.GetFocusedItem()
        if idx > 0:
            if player_service.cola.subir(idx):
                self._cargar_cola()
                self.lista_cola.Focus(idx - 1)
                self.lista_cola.Select(idx - 1)
                speak(_("Subido en cola."), interrupt=True)

    def _on_bajar_cola(self, event):
        idx = self.lista_cola.GetFocusedItem()
        if idx >= 0 and idx < self.lista_cola.GetItemCount() - 1:
            if player_service.cola.bajar(idx):
                self._cargar_cola()
                self.lista_cola.Focus(idx + 1)
                self.lista_cola.Select(idx + 1)
                speak(_("Bajado en cola."), interrupt=True)

    def _on_quitar_cola(self, event):
        idx = self.lista_cola.GetFocusedItem()
        if idx >= 0:
            player_service.cola.quitar(idx)
            self._cargar_cola()
            speak(_("Elemento removido de la lista."), interrupt=True)

    def _on_limpiar_cola(self, event):
        player_service.cola.limpiar()
        self._cargar_cola()
        speak(_("Lista de reproducción vaciada."), interrupt=True)

    # --- EVENTOS TIMER Y AJUSTES ---
    def _on_timer_tick(self, event):
        self._actualizar_info_pista()

    def _actualizar_info_pista(self):
        info = player_service.obtener_informacion()
        
        title = info["title"]
        self.lbl_pista.SetLabel(f"Pista: {title}")
        
        pos_sec = info["position"]
        dur_sec = info["duration"]
        pct = info["percentage"]
        state = info["state"]
        
        # Formatear mm:ss
        pos_str = f"{int(pos_sec // 60):02d}:{int(pos_sec % 60):02d}"
        dur_str = f"{int(dur_sec // 60):02d}:{int(dur_sec % 60):02d}"
        
        estado_desc = ""
        if state == "reproduciendo": estado_desc = _(" (Reproduciendo)")
        elif state == "pausado": estado_desc = _(" (Pausado)")
        elif state == "detenido": estado_desc = _(" (Detenido)")
        
        self.lbl_progreso.SetLabel(f"Progreso: {pos_str} / {dur_str} ({int(pct)}%){estado_desc}")

    def _paso_adelante(self) -> float:
        """Segundos para avanzar (configurable en Preferencias > Reproductores)."""
        try:
            return float(config.get("media_player", "default_step_forward", 10.0))
        except (TypeError, ValueError):
            return 10.0

    def _paso_atras(self) -> float:
        """Segundos para retroceder (configurable en Preferencias > Reproductores)."""
        try:
            return float(config.get("media_player", "default_step_backward", 10.0))
        except (TypeError, ValueError):
            return 10.0

    def _sincronizar_velocidad(self):
        """Coloca el selector de velocidad en el valor guardado (el más cercano)."""
        try:
            v = float(config.get("media_player", "speed", 1.0))
        except (TypeError, ValueError):
            v = 1.0
        idx = min(range(len(self._velocidades)), key=lambda i: abs(self._velocidades[i] - v))
        self.choice_speed.SetSelection(idx)

    def _cambiar_velocidad_rel(self, delta: int):
        """Mueve el selector de velocidad una posición arriba/abajo y la aplica."""
        idx = self.choice_speed.GetSelection()
        if idx == wx.NOT_FOUND:
            idx = self._velocidades.index(1.0)
        idx = max(0, min(len(self._velocidades) - 1, idx + delta))
        self.choice_speed.SetSelection(idx)
        speed = self._velocidades[idx]
        player_service.establecer_velocidad(speed)
        speak(_("Velocidad {0}x.").format(f"{speed:g}"), interrupt=True)

    def _on_vol_changed(self, event):
        vol = float(self.slider_vol.GetValue() / 100.0)
        player_service.establecer_volumen(vol)

    def _on_speed_changed(self, event):
        # El usuario ha cambiado el selector directamente: el lector ya anuncia la opción
        # elegida, así que NO se vuelve a hablar aquí (evita el mensaje duplicado).
        idx = self.choice_speed.GetSelection()
        if 0 <= idx < len(self._velocidades):
            player_service.establecer_velocidad(self._velocidades[idx])

    # --- ENTRADAS DE TECLADO Y ATAJOS ---
    def _on_key_down(self, event):
        key = event.GetKeyCode()
        mod = event.GetModifiers()
        
        # Espacio: reproducir / pausar
        if key == wx.WXK_SPACE:
            info = player_service.obtener_informacion()
            if info["state"] == "reproduciendo":
                player_service.pausar()
            else:
                player_service.reproducir()
                
        # Ctrl+Espacio: detener
        elif key == wx.WXK_SPACE and mod == wx.ACCEL_CTRL:
            player_service.detener()
            speak(_("Reproductor detenido."), interrupt=True)
            
        # Flecha Derecha: avanzar el salto configurado
        elif key == wx.WXK_RIGHT and mod == wx.ACCEL_NORMAL:
            player_service.avanzar_segundos(self._paso_adelante())
            self._anunciar_posicion()

        # Flecha Izquierda: retroceder el salto configurado
        elif key == wx.WXK_LEFT and mod == wx.ACCEL_NORMAL:
            player_service.retroceder_segundos(self._paso_atras())
            self._anunciar_posicion()
            
        # Ctrl+Flecha Derecha: avanzar 60s
        elif key == wx.WXK_RIGHT and mod == wx.ACCEL_CTRL:
            player_service.avanzar_segundos(60.0)
            self._anunciar_posicion()
            
        # Ctrl+Flecha Izquierda: retroceder 60s
        elif key == wx.WXK_LEFT and mod == wx.ACCEL_CTRL:
            player_service.retroceder_segundos(60.0)
            self._anunciar_posicion()
            
        # Flecha Arriba: subir volumen
        elif key == wx.WXK_UP and mod == wx.ACCEL_NORMAL:
            vol = min(1.0, player_service.backend.obtener_volumen() + 0.05)
            player_service.establecer_volumen(vol)
            self.slider_vol.SetValue(int(vol * 100))
            speak(f"Volumen: {int(vol * 100)}%", interrupt=True)
            
        # Flecha Abajo: bajar volumen
        elif key == wx.WXK_DOWN and mod == wx.ACCEL_NORMAL:
            vol = max(0.0, player_service.backend.obtener_volumen() - 0.05)
            player_service.establecer_volumen(vol)
            self.slider_vol.SetValue(int(vol * 100))
            speak(f"Volumen: {int(vol * 100)}%", interrupt=True)
            
        # Tecla M: Silenciar (Mute)
        elif key == ord('M') or key == ord('m'):
            is_muted = config.get("media_player", "mute", False)
            new_mute = not is_muted
            config.set("media_player", "mute", new_mute)  # set() ya guarda (autosave)

            if new_mute:
                player_service.backend.establecer_volumen(0.0)
                notificar(_("Silenciado."))
            else:
                vol = config.get("media_player", "volume", 0.8)
                player_service.establecer_volumen(vol)
                speak(f"Sonido activado. Volumen: {int(vol * 100)}%", interrupt=True)
                
        # Ctrl+Arriba: Aumentar velocidad
        elif key == wx.WXK_UP and mod == wx.ACCEL_CTRL:
            self._cambiar_velocidad_rel(+1)

        # Ctrl+Abajo: Disminuir velocidad
        elif key == wx.WXK_DOWN and mod == wx.ACCEL_CTRL:
            self._cambiar_velocidad_rel(-1)

        # Ctrl+0: Velocidad normal
        elif key == ord('0') and mod == wx.ACCEL_CTRL:
            player_service.establecer_velocidad(1.0)
            self._sincronizar_velocidad()
            speak(_("Velocidad normal (1x)."), interrupt=True)
            
        # Inicio: Ir al principio
        elif key == wx.WXK_HOME:
            player_service.seek(0.0)
            speak(_("Posicionado al inicio."), interrupt=True)
            
        # Ctrl+I: Anunciar información detallada
        elif key == ord('I') and mod == wx.ACCEL_CTRL:
            info = player_service.obtener_informacion()
            pos = info["position"]
            dur = info["duration"]
            pos_str = f"{int(pos // 60):02d}:{int(pos % 60):02d}"
            dur_str = f"{int(dur // 60):02d}:{int(dur % 60):02d}"
            speak(f"Pista: {info['title']}. Posición: {pos_str} de {dur_str} ({int(info['percentage'])}%). Estado: {info['state']}.", interrupt=True)
            
        # Escape: Cerrar
        elif key == wx.WXK_ESCAPE:
            self.Close()
        else:
            event.Skip()

    def _anunciar_posicion(self):
        info = player_service.obtener_informacion()
        pos = info["position"]
        pos_str = f"{int(pos // 60):02d}:{int(pos % 60):02d}"
        speak(pos_str, interrupt=True)

    # --- CALLBACKS DE EVENTOS ---
    def _on_state_changed(self, state):
        if state == "reproduciendo":
            speak(_("Reproduciendo."), interrupt=True)
        elif state == "pausado":
            speak(_("Pausado."), interrupt=True)
        elif state == "detenido":
            speak(_("Detenido."), interrupt=True)

    def _on_player_error(self, err_msg):
        notificar(f"Error en el reproductor: {err_msg}")

    def _on_close(self, event):
        if self.timer.IsRunning():
            self.timer.Stop()
        event.Skip()

    def Destroy(self):
        if self.timer.IsRunning():
            self.timer.Stop()
        event_bus.unsubscribe("player_state_changed", self._on_state_changed)
        event_bus.unsubscribe("player_error", self._on_player_error)
        return super().Destroy()
