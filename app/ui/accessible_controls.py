# -*- coding: utf-8 -*-
"""
Controles de interfaz gráfica optimizados para accesibilidad y lectores de pantalla.
Implementa listas con anuncios de voz directos, sliders parlantes y controles con ayuda contextual.
"""

import wx
import logging
from app.core.speech import speak
from app.core.config_manager import config
# Importado a nivel de módulo (vive en ui_helpers, sin dependencias de wx ni de main_frame): evita
# el `from app.ui.main_frame import ...` que hacía _refrescar_fecha en cada refresco de fecha.
from app.ui.ui_helpers import formatear_tiempo_relativo

log = logging.getLogger(__name__)


def describir_control_enfocado(ventana=None):
    """Anuncia por voz la AYUDA del control que tiene el foco (Ctrl+H): su nombre accesible y, si lo
    tiene, su texto de ayuda (atributo `ayuda_texto`), su descripción larga de ayuda (HelpText) o su
    tooltip. Funciona con cualquier widget de wx. Si no hay nada útil, lo dice."""
    win = ventana or wx.Window.FindFocus()
    if not win:
        speak(_("No hay ningún control enfocado."), interrupt=True)
        return
    # Nombre accesible: el name= del control si es PROPIO. wx pone names genéricos por defecto
    # ("check", "button", "text"...); en ese caso se usa la etiqueta visible del control
    # (GetLabel, sin el & de acelerador), para que Ctrl+H diga algo útil en cualquier diálogo.
    _names_genericos = {
        "check", "checkBox", "button", "text", "textctrl", "choice", "comboBox", "combo",
        "listCtrl", "listBox", "list", "panel", "staticText", "slider", "radioButton",
        "radioBox", "notebook", "treeCtrl", "treeListCtrl", "spinCtrl", "toggle", "groupBox",
        "scrolledpanel", "dialog", "frame", "staticBitmap", "gauge", "datectrl", "timectrl",
        "wxSpinCtrl", "grid", "htmlWindow", "GenericDirCtrl", "filepicker", "dirpicker",
    }
    nombre = ""
    try:
        nombre = (win.GetName() or "").strip()
    except Exception:
        pass
    if not nombre or nombre in _names_genericos:
        try:
            etiqueta_visible = (win.GetLabel() or "").replace("&", "").strip()
            if etiqueta_visible:
                nombre = etiqueta_visible
        except Exception:
            pass
    if not nombre or nombre in _names_genericos:
        # Campos sin etiqueta propia (TextCtrl, Choice, listas...): usar el StaticText
        # ANTERIOR entre los hermanos, que es la etiqueta visual que los presenta (misma
        # heurística que aplican los lectores de pantalla).
        try:
            hermanos = list(win.GetParent().GetChildren()) if win.GetParent() else []
            if win in hermanos:
                for previo in reversed(hermanos[:hermanos.index(win)]):
                    if isinstance(previo, wx.StaticText):
                        texto = (previo.GetLabel() or "").replace("&", "").strip().rstrip(":")
                        if texto:
                            nombre = texto
                        break
        except Exception:
            pass
    ayuda = (getattr(win, "ayuda_texto", "") or "").strip()
    if not ayuda:
        try:
            ayuda = (win.GetHelpText() or "").strip()
        except Exception:
            ayuda = ""
    if not ayuda:
        try:
            tt = win.GetToolTip()
            if tt:
                ayuda = (tt.GetTip() or "").strip()
        except Exception:
            pass
    etiqueta = nombre or _("este control")
    if ayuda:
        speak(_("Ayuda sobre {0}: {1}").format(etiqueta, ayuda), interrupt=True)
    else:
        speak(_("{0}. No hay ayuda detallada para este control.").format(etiqueta), interrupt=True)


# Catálogo de "unidades de movimiento" para la navegación por saltos (Alt+flechas), inspirado en
# FastSM. "mismo autor" usa la columna de autor; las de tiempo usan la fecha ISO ya registrada por
# fila. Se recorre en orden con Alt+Izquierda/Derecha.
_UNIDADES_MOV = [
    {"kind": "same_user", "titulo": lambda: _("Mismo autor")},
    {"kind": "time", "seconds": 3600, "titulo": lambda: _("1 hora")},
    {"kind": "time", "seconds": 21600, "titulo": lambda: _("6 horas")},
    {"kind": "time", "seconds": 86400, "titulo": lambda: _("1 día")},
]


class AccessibleListCtrl(wx.ListCtrl):
    """
    Subclase de wx.ListCtrl optimizada para lectores de pantalla.
    Permite asociar una función generadora de lectura de voz para cada fila,
    verbalizando el elemento enfocado de forma completa e instantánea.
    Soporta navegación por columnas con flechas izquierda/derecha y atajos Ctrl+1..9.
    """
    
    def __init__(self, parent, id=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize, style=wx.LC_REPORT, name="Lista", virtual=False):
        # MODO VIRTUAL (LC_VIRTUAL): el control nativo NO guarda los elementos; solo pide a
        # OnGetItemText el texto de las celdas VISIBLES. Así una lista con miles de filas se
        # pinta en O(filas visibles) y no bloquea la interfaz al refrescar (como TWBlue/FastSM).
        # Los datos viven en los diccionarios por fila de Python (self._celdas, etc.), que ya
        # existían para leer el texto completo; en virtual son además la ÚNICA fuente de verdad.
        self._virtual = bool(virtual)
        if self._virtual:
            style |= wx.LC_VIRTUAL
        super().__init__(parent, id, pos, size, style, name=name)
        # Nº de filas en modo virtual (el control nativo no las cuenta solo). Fuera de virtual no se usa.
        self._n = 0
        # Durante una carga masiva (entre congelar_diferido y su Thaw diferido) NO se llama a
        # SetItemCount por cada fila: se acumula en los diccionarios y se sincroniza UNA vez al final.
        self._cargando = False
        # Bandera para insertar/recolocar el foco SIN que el lector vuelva a locutar (actualización
        # silenciosa: las novedades entran sin mover ni hacer releer al usuario).
        self._silencioso = False

        # Mapeo de índices de fila a cadenas de texto descriptivas para lectura de voz
        self._lecturas: dict[int, str] = {}
        # Mapeo de índices de fila al evento de sonido de su multimedia (media_image,
        # media_audio, media_video...) para los avisos de TWBlue al situarse encima.
        self._medios: dict[int, str] = {}
        # Mapeo de fila -> info de su fecha relativa, para RECALCULARLA cada vez que el
        # usuario se sitúa encima (como TWBlue): {"col": índice de la columna de fecha,
        # "iso": fecha absoluta original, "rel": último texto relativo mostrado}. Así
        # "hace 4 minutos" pasa a "hace 5 minutos" sin tener que pulsar F5.
        self._fechas: dict[int, dict] = {}
        # Texto COMPLETO de cada celda {fila: {columna: texto}}. Windows trunca el texto de las
        # columnas de un ListCtrl a ~511 caracteres, así que GetItemText no vale para LEER el
        # contenido completo (toots largos en instancias con límite alto). Guardamos aquí el
        # texto íntegro para que la navegación por columnas lea todo, en TODAS las listas.
        self._celdas: dict[int, dict[int, str]] = {}
        self._focus_col = 0
        # Navegación por "unidades de movimiento" (estilo FastSM): salto por el MISMO autor o por
        # TRAMOS DE TIEMPO. La columna de autor y la de fecha las fija el frame al construir el
        # buffer (_col_autor_nav / la fecha vive ya en self._fechas). _unidad_idx es la unidad activa.
        self._col_autor_nav = None
        self._unidad_idx = 0
        self.flags = {
            'row_num': True,
            'col_hdr': True,
            'cell_val': True,
            'row_cnt': True,
            'empty': True
        }

        # Atajos de teclado específicos para la lista
        self.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_selected)
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        self.Bind(wx.EVT_SET_FOCUS, self._on_set_focus)

    # --- MODO VIRTUAL: el control pide el texto de las celdas visibles a este método ---
    def OnGetItemText(self, row, col):
        """wx llama a esto SOLO en modo virtual, para cada celda visible. El texto vive en
        self._celdas (la misma fuente que usamos para leer el contenido completo)."""
        return self._celdas.get(row, {}).get(col, "")

    def _sync_count(self):
        """Sincroniza el nº de filas del control nativo con el modelo en modo virtual. Durante
        una carga masiva (self._cargando) NO se hace: se difiere a un único SetItemCount al final,
        que es lo que hace que pintar miles de filas sea instantáneo."""
        if self._virtual and not self._cargando:
            try:
                self.SetItemCount(self._n)
            except Exception:
                pass

    def _asegurar_conteo_nativo(self):
        """Fuerza que el control nativo conozca ya el nº real de filas (aunque estemos en mitad
        de una carga diferida), necesario antes de mover el foco a una fila concreta."""
        if self._virtual:
            try:
                if super().GetItemCount() != self._n:
                    self.SetItemCount(self._n)
            except Exception:
                pass

    def Focus(self, idx):
        # En virtual el foco a una fila exige que el control ya sepa cuántas filas hay (el
        # SetItemCount puede estar diferido durante la carga). Se asegura antes de enfocar.
        if self._virtual:
            self._asegurar_conteo_nativo()
        return super().Focus(idx)

    def EnsureVisible(self, idx):
        if self._virtual:
            self._asegurar_conteo_nativo()
        return super().EnsureVisible(idx)

    # --- Captura del texto COMPLETO de cada celda (Windows trunca el del control a ~511) ---
    def InsertItem(self, *args, **kwargs):
        # En virtual NO existe el item nativo: solo se guarda la celda 0 y se ajusta el contador.
        # Se asume inserción al final (volcado de un buffer: filas 0,1,2,... crecientes); para
        # insertar EN MEDIO sin reconstruir se usa insertar_fila().
        if self._virtual:
            try:
                pos = args[0] if args and isinstance(args[0], int) else self._n
                label = args[1] if len(args) >= 2 and isinstance(args[1], str) else ""
                self._celdas.setdefault(pos, {})[0] = label
                if pos >= self._n:
                    self._n = pos + 1
                self._sync_count()
                return pos
            except Exception:
                return -1
        idx = super().InsertItem(*args, **kwargs)
        try:
            if len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], str):
                self._celdas.setdefault(args[0], {})[0] = args[1]
        except Exception:
            pass
        return idx

    def SetItem(self, *args, **kwargs):
        # En virtual, fijar una celda = actualizar el modelo y repintar esa fila (si ya es visible).
        if self._virtual:
            try:
                if len(args) >= 3 and isinstance(args[0], int) and isinstance(args[1], int) and isinstance(args[2], str):
                    self._celdas.setdefault(args[0], {})[args[1]] = args[2]
                    if not self._cargando and 0 <= args[0] < self._n:
                        self.RefreshItem(args[0])
            except Exception:
                pass
            return True
        res = super().SetItem(*args, **kwargs)
        try:
            if len(args) >= 3 and isinstance(args[0], int) and isinstance(args[1], int) and isinstance(args[2], str):
                self._celdas.setdefault(args[0], {})[args[1]] = args[2]
        except Exception:
            pass
        return res

    def DeleteAllItems(self):
        # Limpiar TODOS los diccionarios por fila (no solo las celdas): si un llamador no
        # llama además a limpiar_lecturas(), quedaban lecturas/medios/fechas de filas viejas
        # asociadas a índices que ahora ocupan otras filas.
        self._celdas.clear()
        self._lecturas.clear()
        self._medios.clear()
        self._fechas.clear()
        if self._virtual:
            self._n = 0
            self._sync_count()
            return True
        return super().DeleteAllItems()

    def GetItemCount(self):
        # En virtual durante la carga el control aún no sabe el conteo (SetItemCount es diferido);
        # devolvemos el del modelo para que los llamadores (acotar índices, etc.) acierten siempre.
        if self._virtual and self._cargando:
            return self._n
        return super().GetItemCount()

    def texto_celda_completo(self, row: int, col: int) -> str:
        """Devuelve el texto ÍNTEGRO de una celda (sin el recorte de Windows)."""
        v = self._celdas.get(row, {}).get(col)
        if v is not None:
            return v
        if self._virtual:
            return ""
        return self.GetItemText(row, col)

    def reanunciar_foco(self):
        """Vuelve a leer en voz la fila enfocada AHORA (sin retardo). Se usa tras cambiar opciones
        de presentación/plantillas para que el cambio se oiga al instante. Como ya NO usamos
        alt-speech al navegar (lo lee el lector por columnas), aquí releemos el contenido de las
        columnas de la fila; esto NO duplica al lector porque se dispara por un cambio de opción,
        no por moverse por la lista."""
        row = self.GetFocusedItem()
        if row == wx.NOT_FOUND:
            row = self.GetFirstSelected()
        if row != wx.NOT_FOUND:
            partes = [self.texto_celda_completo(row, c) for c in range(self.GetColumnCount())]
            texto = ", ".join(p for p in partes if p)
            if texto:
                speak(texto, interrupt=True, sync=True)

    def _on_set_focus(self, event):
        """Al recibir el foco: refrescar la fecha de la fila y reproducir el sonido de multimedia
        si lo tiene. NO se locuta la fila: ya la lee el lector de pantalla por columnas (un speak
        aquí provocaba DOBLE lectura, lo que reportaban los usuarios)."""
        if self._silencioso:
            event.Skip()
            return
        row = self.GetFocusedItem()
        if row == wx.NOT_FOUND:
            row = self.GetFirstSelected()
        if row != wx.NOT_FOUND:
            self._refrescar_fecha(row)
            self._sonido_media(row)
        event.Skip()

    def establecer_lectura(self, index: int, text: str):
        """Asocia un texto descriptivo a una fila de la lista."""
        self._lecturas[index] = text

    def establecer_medios(self, index: int, evento: str):
        """Asocia el evento de sonido de multimedia (o None) de una fila, para los
        avisos sonoros al situarse en publicaciones con imagen o audio/vídeo."""
        if evento:
            self._medios[index] = evento
        else:
            self._medios.pop(index, None)

    def establecer_fecha(self, index: int, col_fecha: int, fecha_iso: str, fecha_rel: str):
        """Registra la fecha de una fila para poder ACTUALIZAR su tiempo relativo al
        navegar. col_fecha: columna donde se muestra la fecha; fecha_iso: fecha absoluta
        original (para recalcular); fecha_rel: el texto relativo que se puso al construir
        (para sustituirlo en la lectura cuando cambie)."""
        if fecha_iso:
            self._fechas[index] = {"col": col_fecha, "iso": fecha_iso, "rel": fecha_rel or ""}

    def _refrescar_fecha(self, row: int):
        """Recalcula el tiempo relativo de la fila y actualiza, si cambió, la columna de
        fecha y la cadena de lectura (sustituyendo el texto relativo anterior). Así, al
        situarte en un elemento, el tiempo está siempre al día sin pulsar F5."""
        info = self._fechas.get(row)
        if not info:
            return
        try:
            nuevo = formatear_tiempo_relativo(info["iso"])
        except Exception:
            return
        viejo = info.get("rel") or ""
        if not nuevo or nuevo == viejo:
            return
        col = info.get("col")
        try:
            if col is not None and 0 <= col < self.GetColumnCount():
                self.SetItem(row, col, nuevo)
        except Exception:
            pass
        # Actualizar la lectura sustituyendo SOLO la primera aparición del texto viejo.
        lectura = self._lecturas.get(row)
        if lectura and viejo and viejo in lectura:
            self._lecturas[row] = lectura.replace(viejo, nuevo, 1)
        info["rel"] = nuevo

    def refrescar_todas_las_fechas(self):
        """Recalcula el tiempo relativo de las filas VISIBLES (el viewport) para que los 'hace X' no
        se queden congelados aunque no te muevas. Se llama periódicamente y al recuperar el foco.

        IMPORTANTE: solo recorre lo que se ve en pantalla, NO todas las filas. Con la lista virtual
        (miles de filas) recorrerlas todas en el hilo de la interfaz bloqueaba momentáneamente y
        cortaba al lector ('se queda tururú') en cada tick y al cambiar de ventana. Las filas que no
        se ven no necesitan refresco (al entrar al viewport se refrescan en el siguiente tick)."""
        if not self._fechas:
            return 0
        try:
            n = self.GetItemCount()
            top = self.GetTopItem()
            por_pagina = self.GetCountPerPage()
        except Exception:
            return 0
        if n <= 0:
            return 0
        ini = max(0, top)
        fin = min(n, ini + max(1, por_pagina) + 1)
        # NO refrescar la fila ENFOCADA: repintar su fecha (SetItem) haría que NVDA RELEYERA el
        # elemento enfocado sin que el usuario se haya movido (es lo que rompía la "lectura
        # silenciosa"; TWBlue tampoco repinta el elemento con foco). La fecha de la fila enfocada se
        # actualiza igualmente al navegar A/DESDE ella (_on_set_focus/_speak_cell la refrescan), que
        # es cuando el lector ya va a leerla de todos modos.
        try:
            foco = self.GetFocusedItem()
        except Exception:
            foco = wx.NOT_FOUND
        cambiadas = 0
        for row in range(ini, fin):
            if row == foco:
                continue
            if row in self._fechas:
                antes = self._fechas[row].get("rel")
                self._refrescar_fecha(row)
                if self._fechas.get(row, {}).get("rel") != antes:
                    cambiadas += 1
        return cambiadas

    def _sonido_media(self, row: int):
        """Reproduce el sonido identificativo si la fila tiene multimedia y el usuario
        activó el aviso correspondiente (indicate_image / indicate_audio, como TWBlue)."""
        ev = self._medios.get(row)
        if not ev:
            return
        if ev == "media_image":
            if not config.get("accessibility", "indicate_image", False):
                return
        elif ev in ("media_audio", "media_video"):
            if not config.get("accessibility", "indicate_audio", False):
                return
        else:
            return
        try:
            from app.services.audio.sound_manager import sound_manager
            sound_manager.play_event_sound(ev)
        except Exception:
            pass

    def obtener_lectura(self, index: int) -> str:
        """Devuelve la cadena de lectura asociada a una fila (o '' si no hay)."""
        return self._lecturas.get(index, "")

    def congelar_diferido(self):
        """Congela el repintado y lo descongela al terminar el manejador actual (con
        wx.CallAfter), de modo que una recarga repinta la lista una sola vez sea cual
        sea la salida del método. Si no hay wx.App (p. ej. en pruebas), descongela de
        inmediato para no requerir un bucle de eventos.

        En modo virtual marca además una carga en curso: durante ella InsertItem/SetItem
        solo tocan el modelo (Python puro) y el conteo del control se fija UNA sola vez al
        descongelar, que es lo que evita el bloqueo al pintar miles de filas."""
        self.Freeze()
        if self._virtual:
            self._cargando = True
        def _fin_carga():
            if self._virtual:
                self._cargando = False
                self._sync_count()
            self.Thaw()
        if wx.GetApp() is not None:
            wx.CallAfter(_fin_carga)
        else:
            _fin_carga()

    def _marcar_movimiento_silencioso(self, activar: bool):
        """Pone (o quita) una PROPIEDAD DE VENTANA en la lista para avisar al complemento de NVDA
        (socialRSSListViewFix) de que el siguiente cambio de foco es AUTOMÁTICO (entró una novedad),
        para que NO lo anuncie y el lector no relea tu elemento (como hace TWBlue con su lista
        nativa). En modo virtual mover el foco dispara un evento de foco que NVDA leería; con esta
        marca el complemento lo ignora. Si el complemento no está instalado, esto es inofensivo
        (solo es una propiedad de ventana que nadie consulta)."""
        try:
            import ctypes
            hwnd = self.GetHandle()
            if not hwnd:
                return
            u = ctypes.windll.user32
            nombre = ctypes.c_wchar_p("SocialRSSSilentMove")
            if activar:
                u.SetPropW(ctypes.c_void_p(int(hwnd)), nombre, ctypes.c_void_p(1))
            else:
                u.RemovePropW(ctypes.c_void_p(int(hwnd)), nombre)
        except Exception:
            pass

    def _mover_foco_silencioso(self, idx: int):
        """Coloca el foco en 'idx' SIN que el lector vuelva a locutar. En modo virtual el
        control no desplaza el foco solo al insertar/eliminar filas: hay que recolocarlo a mano
        para que el usuario siga sobre SU elemento (actualización silenciosa). El complemento de
        NVDA silencia el anuncio gracias a la marca de ventana (ver _marcar_movimiento_silencioso)."""
        if not (0 <= idx < self._n):
            return
        self._silencioso = True
        # Marcar ANTES de mover el foco: NVDA consulta la marca al procesar el evento de foco.
        self._marcar_movimiento_silencioso(True)
        try:
            self._asegurar_conteo_nativo()
            # Mantener foco Y selección juntos sobre el mismo elemento (en single-sel van de la
            # mano); Select dispararía la locución, pero la marca hace que el complemento la ignore.
            estaba_sel = self.GetFirstSelected() != wx.NOT_FOUND
            super().Focus(idx)
            if estaba_sel:
                self.Select(idx)
        except Exception:
            pass
        # Restaurar tras procesar los eventos de foco que pueda haber disparado el Focus().
        if wx.GetApp() is not None:
            wx.CallAfter(lambda: setattr(self, "_silencioso", False))
            # Quitar la marca un poco después, para que NVDA haya procesado ya el evento de foco
            # (lo hace casi al instante). Si se quitara demasiado pronto, no le daría tiempo a verla.
            wx.CallLater(150, lambda: self._marcar_movimiento_silencioso(False))
        else:
            self._silencioso = False
            self._marcar_movimiento_silencioso(False)

    def insertar_fila(self, pos: int, valores, lectura: str = ""):
        """Inserta una fila en la posición 'pos' con sus columnas y su lectura, SIN
        tocar el foco. Al insertar, el item enfocado se desplaza con su estado (lo
        gestiona el control nativo), por lo que el foco se mantiene en el mismo
        elemento y el lector no anuncia nada (técnica usada por TWBlue para que las
        novedades entren en silencio sin mover al usuario).

        Reindexa las lecturas (las de posición >= pos suben una posición) para que
        sigan correspondiendo con sus filas tras la inserción.
        """
        if self._virtual:
            # Capturar el foco para preservar la posición LÓGICA del usuario tras la inserción.
            try:
                foco = self.GetFocusedItem()
            except Exception:
                foco = wx.NOT_FOUND
            # Hacer hueco desplazando una posición las filas >= pos en TODOS los diccionarios.
            self._celdas = {(k + 1 if k >= pos else k): v for k, v in self._celdas.items()}
            self._lecturas = {(k + 1 if k >= pos else k): v for k, v in self._lecturas.items()}
            self._medios = {(k + 1 if k >= pos else k): v for k, v in self._medios.items()}
            self._fechas = {(k + 1 if k >= pos else k): v for k, v in self._fechas.items()}
            # Escribir la fila nueva directamente en el modelo.
            self._celdas[pos] = {i: (v or "") for i, v in enumerate(valores or [])}
            self._lecturas[pos] = lectura
            self._n += 1
            self._sync_count()
            # Si insertamos en o por encima del foco, el elemento del usuario bajó una posición:
            # recolocar el foco sin locutar para que siga sobre lo mismo.
            if foco != wx.NOT_FOUND and pos <= foco:
                self._mover_foco_silencioso(foco + 1)
            return
        # Hacer hueco en el texto completo de celdas ANTES de insertar (los overrides de
        # InsertItem/SetItem escribirán en 'pos' al construir la fila nueva).
        self._celdas = {(k + 1 if k >= pos else k): v for k, v in self._celdas.items()}
        self.InsertItem(pos, valores[0] if valores else "")
        for i in range(1, len(valores)):
            self.SetItem(pos, i, valores[i])
        # Reindexar lecturas (desplazar las de índice >= pos).
        nuevas = {}
        for k, v in self._lecturas.items():
            nuevas[k + 1 if k >= pos else k] = v
        nuevas[pos] = lectura
        self._lecturas = nuevas
        # Reindexar también los eventos de multimedia para que sigan su fila.
        nuevos_m = {}
        for k, v in self._medios.items():
            nuevos_m[k + 1 if k >= pos else k] = v
        self._medios = nuevos_m
        # Reindexar las fechas (las nuevas filas insertadas registran su fecha aparte).
        nuevos_f = {}
        for k, v in self._fechas.items():
            nuevos_f[k + 1 if k >= pos else k] = v
        self._fechas = nuevos_f

    def insertar_filas_lote(self, nuevas):
        """Inserta VARIAS filas de una vez, en sus posiciones FINALES (lista de tuplas
        (pos, valores, lectura), con pos ascendente), sin tocar el foco del usuario.

        Hace lo mismo que llamar a insertar_fila una vez por fila, pero con UNA sola pasada
        de reindexado por diccionario y UN solo movimiento silencioso del foco. Antes, cada
        inserción reconstruía los 4 diccionarios (O(n) por fila): una ráfaga de k novedades
        sobre un buffer grande costaba O(k·n) en el hilo de la interfaz. La actualización
        sigue siendo silenciosa: el foco se recoloca una vez con la marca para el lector."""
        if not nuevas:
            return
        if not self._virtual:
            # Fuera de virtual el control nativo gestiona el desplazamiento: fila a fila.
            for pos, valores, lectura in nuevas:
                self.insertar_fila(pos, valores, lectura)
            return
        try:
            foco = self.GetFocusedItem()
        except Exception:
            foco = wx.NOT_FOUND
        n_final = self._n + len(nuevas)
        pos_nuevas = {p for p, _v, _l in nuevas}
        # Índice final que ocupará cada fila VIEJA (las viejas rellenan, en orden, los
        # huecos que dejan las nuevas).
        destinos_viejos = [i for i in range(n_final) if i not in pos_nuevas]
        mapa = {k: destinos_viejos[k] for k in range(self._n)}
        self._celdas = {mapa.get(k, k): v for k, v in self._celdas.items()}
        self._lecturas = {mapa.get(k, k): v for k, v in self._lecturas.items()}
        self._medios = {mapa.get(k, k): v for k, v in self._medios.items()}
        self._fechas = {mapa.get(k, k): v for k, v in self._fechas.items()}
        for pos, valores, lectura in nuevas:
            self._celdas[pos] = {i: (v or "") for i, v in enumerate(valores or [])}
            self._lecturas[pos] = lectura
        self._n = n_final
        self._sync_count()
        # Recolocar el foco (una sola vez) sobre el MISMO elemento del usuario, en silencio.
        if foco != wx.NOT_FOUND and 0 <= foco < len(destinos_viejos):
            nuevo_foco = destinos_viejos[foco]
            if nuevo_foco != foco:
                self._mover_foco_silencioso(nuevo_foco)

    def eliminar_fila(self, pos: int):
        """Elimina la fila 'pos' reindexando las lecturas. No re-enfoca: si se borra
        una fila por encima del item enfocado, el control nativo conserva el foco en
        el mismo elemento (desplazando su índice), en silencio."""
        if self._virtual:
            try:
                foco = self.GetFocusedItem()
            except Exception:
                foco = wx.NOT_FOUND
            self._celdas = {(k - 1 if k > pos else k): v
                            for k, v in self._celdas.items() if k != pos}
            self._lecturas = {(k - 1 if k > pos else k): v
                              for k, v in self._lecturas.items() if k != pos}
            self._medios = {(k - 1 if k > pos else k): v
                            for k, v in self._medios.items() if k != pos}
            self._fechas = {(k - 1 if k > pos else k): v
                            for k, v in self._fechas.items() if k != pos}
            self._n = max(0, self._n - 1)
            self._sync_count()
            # Si la fila borrada estaba por encima del foco, el elemento del usuario subió una
            # posición: recolocar el foco sin locutar para que siga sobre lo mismo.
            if foco != wx.NOT_FOUND and pos < foco:
                self._mover_foco_silencioso(foco - 1)
            return
        self.DeleteItem(pos)
        self._celdas = {(k - 1 if k > pos else k): v
                        for k, v in self._celdas.items() if k != pos}
        nuevas = {}
        for k, v in self._lecturas.items():
            if k == pos:
                continue
            nuevas[k - 1 if k > pos else k] = v
        self._lecturas = nuevas
        nuevos_m = {}
        for k, v in self._medios.items():
            if k == pos:
                continue
            nuevos_m[k - 1 if k > pos else k] = v
        self._medios = nuevos_m
        nuevos_f = {}
        for k, v in self._fechas.items():
            if k == pos:
                continue
            nuevos_f[k - 1 if k > pos else k] = v
        self._fechas = nuevos_f

    def actualizar_fila(self, pos: int, valores, lectura: str = ""):
        """Actualiza el contenido (columnas y lectura) de una fila ya existente, en su
        sitio, sin moverla ni tocar el foco."""
        for i in range(len(valores)):
            self.SetItem(pos, i, valores[i])
        self._lecturas[pos] = lectura

    def limpiar_lecturas(self):
        """Limpia las descripciones de lectura cached."""
        self._lecturas.clear()
        self._medios.clear()
        self._fechas.clear()
        self._celdas.clear()
        if self._virtual:
            self._n = 0
            self._sync_count()

    def reset_focus_col(self):
        """Restablece el índice de la columna enfocada a 0."""
        self._focus_col = 0

    def _destino_unidad(self, desde: int, direccion: str):
        """Índice al que saltar desde 'desde' según la unidad de movimiento activa, o None.
        'direccion': 'abajo' (índices mayores) o 'arriba' (menores). Usa datos ya en memoria
        (columna de autor y fechas por fila), sin tocar la base de datos: O(n) en el peor caso
        y sin trabajo de red, así que es instantáneo para el lector."""
        n = self.GetItemCount()
        if not (0 <= desde < n):
            return None
        paso = 1 if direccion == "abajo" else -1
        unidad = _UNIDADES_MOV[self._unidad_idx]
        if unidad["kind"] == "same_user":
            col = self._col_autor_nav
            if col is None:
                return None
            autor = (self._celdas.get(desde, {}).get(col) or "").strip().lower()
            if not autor:
                return None
            i = desde + paso
            while 0 <= i < n:
                if (self._celdas.get(i, {}).get(col) or "").strip().lower() == autor:
                    return i
                i += paso
            return None
        # Unidad de tiempo: saltar al primer elemento de un BLOQUE DE CALENDARIO distinto
        # (hora en punto, bloque de 6 horas o día natural, en hora LOCAL). Antes se exigía
        # una diferencia de 24 horas EXACTAS: desde un toot de anoche, el de ayer por la
        # mañana distaba menos de 24 h y "1 día" se lo saltaba (reporte de usuarios). Con
        # bloques de calendario, "1 día" = el primer mensaje de OTRO día, como se espera.
        umbral = unidad.get("seconds", 3600)
        base = self._bucket_tiempo(desde, umbral)
        if base is None:
            return None
        i = desde + paso
        while 0 <= i < n:
            b = self._bucket_tiempo(i, umbral)
            if b is not None and b != base:
                return i
            i += paso
        return None

    def _bucket_tiempo(self, fila: int, umbral: int):
        """Bloque de calendario (en hora LOCAL) al que pertenece la fecha de una fila:
        día natural para '1 día'; bloques de N horas alineados al reloj para el resto."""
        info = self._fechas.get(fila)
        if not info or not info.get("iso"):
            return None
        try:
            from datetime import datetime
            s = str(info["iso"]).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone()   # a hora local del usuario
            if umbral >= 86400:
                return dt.date().toordinal()
            horas = max(1, umbral // 3600)
            return (dt.date().toordinal(), dt.hour // horas)
        except Exception:
            return None

    def _fecha_epoch(self, fila: int):
        """Segundos epoch de la fecha ISO registrada para una fila, o None."""
        info = self._fechas.get(fila)
        if not info or not info.get("iso"):
            return None
        try:
            from datetime import datetime
            s = str(info["iso"]).strip().replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return None

    def _on_selected(self, event):
        """Al seleccionar una fila: refrescar su fecha y reproducir el sonido de multimedia. NO se
        locuta la fila: la lee el lector por columnas (el speak aquí causaba DOBLE lectura)."""
        if self._silencioso:
            event.Skip()
            return
        index = event.GetIndex()
        self._refrescar_fecha(index)
        self._sonido_media(index)
        event.Skip()

    def _speak_cell(self, row: int, col: int):
        # Recalcular el tiempo relativo para que, al leer por columnas, la fecha esté al día.
        self._refrescar_fecha(row)
        # Qué se anuncia es configurable (Accesibilidad → Navegación por columnas). Si el usuario
        # lo desactivara todo, se dice al menos el contenido para no quedar en silencio.
        decir_fila = config.get("accessibility", "colnav_row_num", True)
        decir_cabecera = config.get("accessibility", "colnav_col_header", True)
        decir_valor = config.get("accessibility", "colnav_cell_value", True)
        if not (decir_fila or decir_cabecera or decir_valor):
            decir_valor = True
        parts = []
        if decir_fila and self.flags.get('row_num', True):
            parts.append(f"Fila {row + 1}")
        if decir_cabecera and self.flags.get('col_hdr', True) and col < self.GetColumnCount():
            parts.append(self.GetColumn(col).GetText())
        if decir_valor and self.flags.get('cell_val', True):
            # Texto COMPLETO de la celda (no el recortado a ~511 por Windows).
            parts.append(self.texto_celda_completo(row, col))
        # Emisión directa: la navegación por columnas no cambia la selección, así
        # que el lector no anuncia nada por su cuenta y solo queda nuestra voz; en
        # directo se locuta de inmediato y sin latencia.
        speak(", ".join(p for p in parts if p), interrupt=True, sync=True)

    def _on_key_down(self, event):
        """Permite atajos rápidos dentro de las listas."""
        key = event.GetKeyCode()
        mod = event.GetModifiers()
        ncols = self.GetColumnCount()

        # Fila enfocada; si no hay foco-rectángulo, usar la seleccionada.
        row = self.GetFocusedItem()
        if row == wx.NOT_FOUND:
            row = self.GetFirstSelected()

        # La barra espaciadora no debe activar el elemento (abrir el navegador,
        # etc.). La consumimos para que no dispare ninguna acción; abrir o activar
        # se hace solo con Intro o doble clic. (En las listas donde el Espacio sí
        # reproduce audio —chat de Telegram, pestañas multimedia— ese Espacio lo
        # gestiona antes el manejador propio de ese diálogo.)
        if mod == wx.MOD_NONE and key == wx.WXK_SPACE:
            return

        # Atajos de flecha izquierda/derecha para navegar por columnas
        if mod == wx.MOD_NONE and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            if row == wx.NOT_FOUND:
                event.Skip()
                return
            self._focus_col = max(0, self._focus_col - 1) if key == wx.WXK_LEFT \
                else min(ncols - 1, self._focus_col + 1)
            self._speak_cell(row, self._focus_col)
            return

        # NAVEGACIÓN POR UNIDADES DE MOVIMIENTO (estilo FastSM):
        #   Alt+Izquierda/Derecha: cambia la unidad activa (mismo autor / tramos de tiempo).
        #   Alt+Arriba/Abajo: salta al anterior/siguiente elemento que cumple la unidad.
        if mod == wx.MOD_ALT and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
            self._unidad_idx = (self._unidad_idx + (1 if key == wx.WXK_RIGHT else -1)) % len(_UNIDADES_MOV)
            speak(_UNIDADES_MOV[self._unidad_idx]["titulo"](), interrupt=True)
            return
        if mod == wx.MOD_ALT and key in (wx.WXK_UP, wx.WXK_DOWN):
            if row == wx.NOT_FOUND:
                event.Skip()
                return
            destino = self._destino_unidad(row, "abajo" if key == wx.WXK_DOWN else "arriba")
            if destino is None:
                speak(_("No hay más por {0}.").format(_UNIDADES_MOV[self._unidad_idx]["titulo"]()), interrupt=True)
            else:
                self.Focus(destino)
                self.Select(destino)
                self.EnsureVisible(destino)
            return

        if row == wx.NOT_FOUND:
            event.Skip()
            return
            
        # Ctrl+1..9 para leer columnas específicas
        if mod == wx.MOD_CONTROL and ord('1') <= key <= ord('9'):
            col = key - ord('1')
            if col < ncols:
                self._speak_cell(row, col)
            return
            
        # Si se presiona Enter sobre un elemento, simular doble clic (abrir detalles)
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            # Lanzar evento de activación del ítem
            act_event = wx.ListEvent(wx.wxEVT_COMMAND_LIST_ITEM_ACTIVATED, self.GetId())
            act_event.SetIndex(row)
            self.GetEventHandler().ProcessEvent(act_event)
        else:
            event.Skip()


class AccessibleSlider(wx.Slider):
    """
    Control deslizante accesible. Es un wx.Slider estándar: el lector de pantalla
    (NVDA) ya anuncia por su cuenta el cambio de valor al ajustarlo con las flechas,
    así que NO se vuelve a hablar desde el programa (evitaba un mensaje duplicado).
    Se conservan los parámetros 'name' y 'unidad' por compatibilidad con los llamadores.
    """

    def __init__(self, parent, id=wx.ID_ANY, value=0, minValue=0, maxValue=100,
                 pos=wx.DefaultPosition, size=wx.DefaultSize, style=wx.SL_HORIZONTAL,
                 name="Control deslizante", unidad=""):
        super().__init__(parent, id, value, minValue, maxValue, pos, size, style, name=name)
        self.unidad = unidad


class AccessibleTextCtrl(wx.TextCtrl):
    """
    Control de edición de texto accesible.
    Permite invocar ayuda contextual de forma predeterminada al presionar Ctrl+H.
    """
    
    def __init__(self, parent, id=wx.ID_ANY, value="", pos=wx.DefaultPosition, 
                 size=wx.DefaultSize, style=0, name="Campo de texto", ayuda_texto=""):
        super().__init__(parent, id, value, pos, size, style, name=name)
        self.ayuda_texto = ayuda_texto
        
        # Enlazar atajo de ayuda local Ctrl+H
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)

    def _on_key_down(self, event):
        key = event.GetKeyCode()
        mod = event.GetModifiers()

        if key == ord('H') and mod == wx.ACCEL_CTRL:
            # Ayuda contextual del propio campo (misma función que Ctrl+H en toda la app).
            describir_control_enfocado(self)
        elif (key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and mod == wx.MOD_SHIFT
              and self.HasFlag(wx.TE_MULTILINE)):
            # En campos multilínea con TE_RICH2, Shift+Enter inserta un salto "blando" (tabulador
            # vertical) que el lector de pantalla lee como un carácter raro. Forzamos un salto de
            # línea normal, igual que Enter, para que sea limpio y legible.
            self.WriteText("\n")
        else:
            event.Skip()


class CustomTextEntryDialog(wx.Dialog):
    """Diálogo de entrada de texto accesible con botones Aceptar y Cancelar personalizados."""
    def __init__(self, parent, message, title, value="", multiline=False, password=False,
                 max_length=0):
        tam = (460, 360) if multiline else (400, 200)
        super().__init__(parent, title=title, size=tam, style=wx.DEFAULT_DIALOG_STYLE, name=title)

        try:
            _
        except NameError:
            _ = lambda x: x

        sizer = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(self, label=message)
        sizer.Add(lbl, 0, wx.ALL | wx.EXPAND, 10)

        estilo = wx.TE_MULTILINE if multiline else (wx.TE_PASSWORD if password else 0)
        self.txt = AccessibleTextCtrl(self, value=value, name=message, style=estilo)
        # Límite duro de caracteres (p. ej. el de la descripción de adjuntos que marca el servidor):
        # el control impide escribir más, igual que en los campos de opción de encuesta.
        if max_length and max_length > 0:
            try:
                self.txt.SetMaxLength(int(max_length))
            except Exception:
                pass
        sizer.Add(self.txt, 1 if multiline else 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_ok = wx.Button(self, wx.ID_OK, _("&Aceptar"), name=_("Confirmar y guardar"))
        self.btn_cancel = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"), name=_("Descartar cambios"))
        
        btn_sizer.Add(self.btn_ok, 0, wx.ALL, 5)
        btn_sizer.Add(self.btn_cancel, 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        
        self.SetSizer(sizer)
        self.txt.SetFocus()
        
    def GetValue(self):
        return self.txt.GetValue()


class _CheckListAccessible(wx.Accessible):
    """Implementación accesible para listas con casillas: expone cada elemento como
    una casilla de verificación con su estado marcado/desmarcado, para que el
    lector de pantalla lo anuncie correctamente (técnica de NVDA)."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window

    def GetRole(self, childId):
        if childId == 0:  # CHILDID_SELF
            return super().GetRole(childId)
        return (wx.ACC_OK, wx.ROLE_SYSTEM_CHECKBUTTON)

    def GetState(self, childId):
        if childId == 0:
            return super().GetState(childId)
        estados = wx.ACC_STATE_SYSTEM_SELECTABLE | wx.ACC_STATE_SYSTEM_FOCUSABLE
        try:
            if self._win.IsChecked(childId - 1):
                estados |= wx.ACC_STATE_SYSTEM_CHECKED
            if self._win.IsSelected(childId - 1):
                estados |= wx.ACC_STATE_SYSTEM_SELECTED
                if self._win.HasFocus():
                    estados |= wx.ACC_STATE_SYSTEM_FOCUSED
        except Exception:
            pass
        return (wx.ACC_OK, estados)


class AccessibleCheckListBox(wx.CheckListBox):
    """Lista con casillas accesible: corrige que el lector no anuncie si un
    elemento está marcado o no. Inspirada en CustomCheckListBox de NVDA."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.SetAccessible(_CheckListAccessible(self))
        except Exception as e:
            log.debug(f"No se pudo asignar el accesible de la lista de casillas: {e}")
        # Al marcar/desmarcar, notificar el cambio de estado al lector y verbalizarlo.
        self.Bind(wx.EVT_CHECKLISTBOX, self._on_toggle)

    def _on_toggle(self, evt):
        evt.Skip()
        idx = evt.GetSelection()
        # Notificar a Windows (y por tanto al lector) el cambio de estado.
        try:
            import ctypes
            EVENT_OBJECT_STATECHANGE = 0x800A
            OBJID_CLIENT = 0xFFFFFFFC  # -4
            ctypes.windll.user32.NotifyWinEvent(
                EVENT_OBJECT_STATECHANGE, int(self.GetHandle()), OBJID_CLIENT, idx + 1
            )
        except Exception:
            pass
        # Respaldo verbal explícito (por si el lector no lo capta).
        try:
            estado = _("marcado") if self.IsChecked(idx) else _("desmarcado")
            speak(f"{self.GetString(idx)}, {estado}", interrupt=True)
        except Exception:
            pass


class MensajeDialog(wx.Dialog):
    """Diálogo genérico accesible con botones correctos en español.

    estilo: "info" (Aceptar), "confirm" (Sí/No), "confirm_cancel" (Sí/No/Cancelar).
    Devuelve wx.ID_OK / wx.ID_YES / wx.ID_NO / wx.ID_CANCEL desde ShowModal().
    """

    def __init__(self, parent, mensaje, titulo="", estilo="info"):
        # Blindaje: si el 'parent' ya fue destruido (p. ej. un diálogo que se cerró antes de que
        # llegara un callback diferido), wx lanzaría un RuntimeError. Lo detectamos y usamos None.
        try:
            if parent is not None and not parent:
                parent = None
        except Exception:
            parent = None
        super().__init__(parent, title=titulo or " ",
                         style=wx.DEFAULT_DIALOG_STYLE, name=titulo or _("Mensaje"))
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Texto del mensaje en un control de solo lectura (navegable y copiable).
        multilinea = "\n" in (mensaje or "")
        if multilinea:
            txt = wx.TextCtrl(self, value=mensaje, style=wx.TE_MULTILINE | wx.TE_READONLY,
                              size=(420, 180), name=titulo or _("Mensaje"))
            sizer.Add(txt, 1, wx.EXPAND | wx.ALL, 12)
        else:
            lbl = wx.StaticText(self, label=mensaje)
            sizer.Add(lbl, 0, wx.ALL, 14)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        if estilo in ("confirm", "confirm_cancel"):
            b_si = wx.Button(self, wx.ID_YES, _("&Sí"))
            b_si.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_YES))
            btns.Add(b_si, 0, wx.ALL, 5)
            b_no = wx.Button(self, wx.ID_NO, _("&No"))
            b_no.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_NO))
            btns.Add(b_no, 0, wx.ALL, 5)
            b_si.SetDefault()
            if estilo == "confirm_cancel":
                b_c = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"))
                b_c.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
                btns.Add(b_c, 0, wx.ALL, 5)
        else:
            b_ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
            b_ok.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
            b_ok.SetDefault()
            btns.Add(b_ok, 0, wx.ALL, 5)

        sizer.Add(btns, 0, wx.ALIGN_CENTER | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        self.CenterOnParent()


def mostrar_info(parent, mensaje, titulo=""):
    """Muestra un mensaje informativo con un botón Aceptar (en español)."""
    dlg = MensajeDialog(parent, mensaje, titulo, estilo="info")
    dlg.ShowModal()
    dlg.Destroy()


class RespuestaConfirmar(int):
    """Id de wx del botón pulsado (wx.ID_YES / wx.ID_NO / wx.ID_CANCEL) que, además, vale
    VERDADERO solo si se pulsó Sí.

    confirmar() siempre ha devuelto el id de wx, pero en ~25 sitios de la app se usaba como si
    fuera un booleano ("if not confirmar(...): return"). Como wx.ID_NO es un número distinto de
    cero, contestar NO no cancelaba nada: por ejemplo, "¿Borrar esta publicación?" → No → se
    borraba igual. Con esta clase las dos formas de uso son correctas: las comparaciones
    (== wx.ID_YES, != wx.ID_YES) siguen funcionando porque es un int, y el uso booleano ahora
    significa "ha dicho que sí"."""

    def __bool__(self):
        return int(self) == wx.ID_YES


def confirmar(parent, mensaje, titulo="", con_cancelar=False):
    """Pregunta Sí/No (y opcionalmente Cancelar). Devuelve el id de wx pulsado, que se puede
    comparar con wx.ID_YES o usar directamente como booleano (verdadero = Sí)."""
    estilo = "confirm_cancel" if con_cancelar else "confirm"
    dlg = MensajeDialog(parent, mensaje, titulo, estilo=estilo)
    r = dlg.ShowModal()
    dlg.Destroy()
    return RespuestaConfirmar(r)


def elegir_opcion(parent, mensaje, titulo, botones):
    """Diálogo accesible con BOTONES EXPLÍCITOS (auditoría Mastodon §11: "Impulsar /
    Citar / Cancelar" en vez de Sí/No/Cancelar, que obligaba a recordar qué era cada
    cosa). Devuelve el ÍNDICE del botón pulsado, o None si se cancela con Escape.

    `botones`: lista de etiquetas (con '&' para el acelerador); el primero es el
    predeterminado. Siempre se añade un botón Cancelar al final (devuelve None)."""
    dlg = wx.Dialog(parent, title=titulo, style=wx.DEFAULT_DIALOG_STYLE, name=titulo)
    s = wx.BoxSizer(wx.VERTICAL)
    txt = wx.StaticText(dlg, label=mensaje)
    txt.Wrap(460)
    s.Add(txt, 0, wx.ALL, 12)
    fila = wx.BoxSizer(wx.HORIZONTAL)
    resultado = {"idx": None}

    def _hacer(i):
        def _on(evt):
            resultado["idx"] = i
            dlg.EndModal(wx.ID_OK)
        return _on

    for i, etiqueta in enumerate(botones):
        b = wx.Button(dlg, label=etiqueta)
        b.Bind(wx.EVT_BUTTON, _hacer(i))
        if i == 0:
            b.SetDefault()
        fila.Add(b, 0, wx.ALL, 5)
    b_cancel = wx.Button(dlg, wx.ID_CANCEL, _("Cancelar"))
    fila.Add(b_cancel, 0, wx.ALL, 5)
    s.Add(fila, 0, wx.ALIGN_CENTER | wx.ALL, 6)
    dlg.SetSizerAndFit(s)
    habilitar_escape_cierre(dlg, wx.ID_CANCEL)
    dlg.CenterOnParent()
    dlg.ShowModal()
    dlg.Destroy()
    return resultado["idx"]


def habilitar_escape_cierre(dialog, cerrar_id=wx.ID_CANCEL):
    """Hace que la tecla Escape cierre el diálogo (modal), SALVO cuando el foco está en un
    Choice, ComboBox o ListBox: esos controles usan Escape para su propio desplegable/selección,
    así que se deja pasar. Llamar en el __init__ del diálogo.

    `cerrar_id` es el id con el que se cierra (wx.ID_CANCEL en compositores/formularios, o
    wx.ID_CLOSE en los diálogos de solo lectura/gestión). Pensado para diálogos que se muestran
    con ShowModal(); usa EndModal."""
    def _on_char_hook(event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            foco = dialog.FindFocus()
            # Estos widgets gestionan Escape por sí mismos (cerrar lista desplegable, etc.).
            if isinstance(foco, (wx.Choice, wx.ComboBox, wx.ListBox)):
                event.Skip()
                return
            if dialog.IsModal():
                dialog.EndModal(cerrar_id)
            else:
                dialog.Close()
            return
        event.Skip()
    dialog.Bind(wx.EVT_CHAR_HOOK, _on_char_hook)
