# -*- coding: utf-8 -*-
"""
Visor de registros (logs) PROFESIONAL y accesible.

La lista es un AccessibleListCtrl VIRTUAL (LC_VIRTUAL, como los buffers): solo se pinta lo visible,
así abre al instante aunque el log tenga miles de líneas (en DEBUG llegan a varios MB). Las COLUMNAS
las define la plantilla "Registros (Logs)" del Editor de plantillas (Hora, Nivel, Módulo, Mensaje por
defecto), de modo que el lector lee las columnas en ese orden y se navega por ellas con las flechas
izquierda/derecha (Ctrl+1..9), igual que en cualquier buffer.

- Lee solo el FINAL del archivo (hasta _MAX_BYTES_COLA) y EN SEGUNDO PLANO (no bloquea).
- Filtra por nivel mínimo, busca (siguiente/anterior) situándose en la fila, copia todo, abre la
  carpeta, limpia la sesión y puede "seguir en vivo".
- Permite elegir QUÉ nivel se GUARDA en disco (general.log_level).

Es MODELESS: se consulta sin cerrar la app.
"""

import os
import re
import glob
import logging
from datetime import datetime

import wx

from app.core.speech import speak, notificar
from app.core.paths import get_data_path
from app.core.config_manager import config
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl

log = logging.getLogger(__name__)

_NIVELES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_RANK = {n: i for i, n in enumerate(_NIVELES)}
# Cabecera de una entrada: "[2026-07-05 10:09:51,441] INFO [modulo:linea] - mensaje". Se capturan la
# hora, el nivel y el resto (de donde se saca el módulo y el mensaje).
_RE_CAB = re.compile(r"^\[\d{4}-\d\d-\d\d[ T](\d\d:\d\d:\d\d)[,.]?\d*\]\s+([A-Z]+)\b(.*)$")
_RE_MODULO = re.compile(r"^\s*\[([^\]]*)\]\s*-?\s?(.*)$")

# Carga dinámica: leer solo el último ~1,2 MB del archivo (el resto es historia antigua).
_MAX_BYTES_COLA = 1_200_000
_ANCHOS = {"hora": 90, "nivel": 90, "modulo": 250, "mensaje": 1000}


def _parsear_registros(texto):
    """Trocea el texto del log en REGISTROS {hora, nivel, modulo, mensaje}. Las líneas de
    continuación (trazas de error) se unen al mensaje de la entrada anterior."""
    regs = []
    cur = None
    for linea in texto.splitlines():
        m = _RE_CAB.match(linea)
        if m:
            if cur is not None:
                regs.append(cur)
            hora, nivel, resto = m.group(1), m.group(2), m.group(3)
            modulo, mensaje = "", resto.strip()
            mm = _RE_MODULO.match(resto)
            if mm:
                modulo, mensaje = mm.group(1), mm.group(2)
            cur = {"hora": hora, "nivel": nivel if nivel in _RANK else "INFO",
                   "modulo": modulo, "mensaje": mensaje}
        else:
            if cur is None:
                cur = {"hora": "", "nivel": "INFO", "modulo": "", "mensaje": linea}
            else:
                cur["mensaje"] = (cur["mensaje"] + "\n" + linea) if cur["mensaje"] else linea
    if cur is not None:
        regs.append(cur)
    return regs


class LogViewerDialog(wx.Dialog):
    """Diálogo accesible y modeless con la lista de registros en modo virtual."""

    _CLAVE_PLANTILLA = "log_template"

    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Visor de Registros (Logs)"),
            size=(900, 580),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Visor de registros")
        )
        self._sesiones = []
        self._registros = []       # TODOS los registros parseados de la sesión
        self._mostrados = []       # los que se ven ahora en la lista (tras el filtro)
        self._texto_crudo = ""     # la cola leída tal cual (para "Copiar todo")
        self._truncado = False
        self._tam_total = 0
        self._carga_token = 0
        self._tick_en_vuelo = False
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tick, self._timer)
        self._init_ui()
        self._cargar_sesiones()
        self._cargar_log()
        self.CenterOnParent()
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    # ------------------------------------------------------------------ UI
    def _init_ui(self):
        raiz = wx.BoxSizer(wx.VERTICAL)

        # Fila 1: sesión + nivel a mostrar
        fila1 = wx.BoxSizer(wx.HORIZONTAL)
        fila1.Add(wx.StaticText(self, label=_("&Sesión:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice_sesion = wx.Choice(self, name=_("Sesión de registro a ver"))
        # No anunciamos al cambiar de sesión: el lector ya lee la fecha del desplegable.
        self.choice_sesion.Bind(wx.EVT_CHOICE, lambda e: self._cargar_log())
        fila1.Add(self.choice_sesion, 1, wx.EXPAND | wx.ALL, 5)
        fila1.Add(wx.StaticText(self, label=_("&Nivel mínimo:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice_filtro = wx.Choice(
            self, name=_("Nivel mínimo a mostrar"),
            choices=[_("Todos"), _("Depuración (DEBUG)"), _("Información (INFO)"),
                     _("Avisos (WARNING)"), _("Errores (ERROR)")])
        self.choice_filtro.SetSelection(0)
        self.choice_filtro.Bind(wx.EVT_CHOICE, lambda e: self._pintar(anunciar=True))
        fila1.Add(self.choice_filtro, 0, wx.ALL, 5)
        raiz.Add(fila1, 0, wx.EXPAND)

        # Fila 2: búsqueda
        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        fila2.Add(wx.StaticText(self, label=_("&Buscar:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_buscar = AccessibleTextCtrl(
            self, style=wx.TE_PROCESS_ENTER, name=_("Texto a buscar en el registro"),
            ayuda_texto=_("Escribe y pulsa Intro para buscar. F3 siguiente, Mayús+F3 anterior."))
        self.txt_buscar.Bind(wx.EVT_TEXT_ENTER, lambda e: self._buscar(1))
        fila2.Add(self.txt_buscar, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_prev = wx.Button(self, label=_("Buscar &anterior"), name=_("Buscar anterior"))
        self.btn_prev.Bind(wx.EVT_BUTTON, lambda e: self._buscar(-1))
        fila2.Add(self.btn_prev, 0, wx.ALL, 5)
        self.btn_next = wx.Button(self, label=_("Buscar &siguiente"), name=_("Buscar siguiente"))
        self.btn_next.Bind(wx.EVT_BUTTON, lambda e: self._buscar(1))
        fila2.Add(self.btn_next, 0, wx.ALL, 5)
        raiz.Add(fila2, 0, wx.EXPAND)

        # Lista VIRTUAL con columnas de la plantilla.
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, virtual=True,
            name=_("Entradas del registro"))
        self._cols = self._crear_columnas()
        raiz.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

        self.lbl_estado = wx.StaticText(self, label="")
        raiz.Add(self.lbl_estado, 0, wx.LEFT | wx.BOTTOM, 8)

        # Fila 3: nivel a GUARDAR + seguir en vivo
        fila3 = wx.BoxSizer(wx.HORIZONTAL)
        fila3.Add(wx.StaticText(self, label=_("&Guardar en disco a partir de:")), 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice_guardar = wx.Choice(
            self, name=_("Nivel de detalle que se guarda en disco"),
            choices=[_("Depuración (DEBUG): todo"), _("Información (INFO)"),
                     _("Avisos (WARNING)"), _("Errores (ERROR)")])
        self.choice_guardar.SetToolTip(_(
            "Cuánto detalle se registra. DEBUG guarda TODO (para reportar un fallo); INFO es lo "
            "normal. Se aplica ya y del todo al reiniciar."))
        self.choice_guardar.Bind(wx.EVT_CHOICE, self._on_cambiar_nivel_guardado)
        self._sincronizar_nivel_guardado()
        fila3.Add(self.choice_guardar, 0, wx.ALL, 5)
        self.cb_seguir = wx.CheckBox(self, label=_("Seguir en &vivo"), name=_("Seguir el registro en vivo"))
        self.cb_seguir.Bind(wx.EVT_CHECKBOX, self._on_seguir)
        fila3.Add(self.cb_seguir, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        raiz.Add(fila3, 0, wx.EXPAND)

        # Fila 4: acciones
        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_recargar = wx.Button(self, label=_("&Recargar"), name=_("Recargar el registro"))
        self.btn_recargar.Bind(wx.EVT_BUTTON, lambda e: self._cargar_log())
        botones.Add(self.btn_recargar, 0, wx.ALL, 5)
        self.btn_copiar = wx.Button(self, label=_("&Copiar lo mostrado"),
                                    name=_("Copiar al portapapeles lo que se ve ahora"))
        self.btn_copiar.SetToolTip(_("Copia solo lo que se ve con el filtro actual: si estás en "
                                     "Errores, copia los errores; en Todos, todo."))
        self.btn_copiar.Bind(wx.EVT_BUTTON, self._on_copiar_todo)
        botones.Add(self.btn_copiar, 0, wx.ALL, 5)
        self.btn_carpeta = wx.Button(self, label=_("Abrir &carpeta"), name=_("Abrir la carpeta de registros"))
        self.btn_carpeta.Bind(wx.EVT_BUTTON, self._on_abrir_carpeta)
        botones.Add(self.btn_carpeta, 0, wx.ALL, 5)
        self.btn_plantilla = wx.Button(self, label=_("&Plantilla..."), name=_("Editar la plantilla de columnas del registro"))
        self.btn_plantilla.Bind(wx.EVT_BUTTON, self._on_editar_plantilla)
        botones.Add(self.btn_plantilla, 0, wx.ALL, 5)
        self.btn_limpiar = wx.Button(self, label=_("&Limpiar esta sesión"), name=_("Vaciar el archivo de esta sesión"))
        self.btn_limpiar.Bind(wx.EVT_BUTTON, self._on_limpiar)
        botones.Add(self.btn_limpiar, 0, wx.ALL, 5)
        botones.AddStretchSpacer()
        self.btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("Cerrar"), name=_("Cerrar el visor"))
        self.btn_cerrar.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        botones.Add(self.btn_cerrar, 0, wx.ALL, 5)
        raiz.Add(botones, 0, wx.EXPAND | wx.ALL, 4)

        self.SetSizer(raiz)

    def _crear_columnas(self):
        """Crea las columnas de la lista a partir de la plantilla 'Registros (Logs)'. Devuelve la
        lista ordenada de (campo, encabezado). El usuario puede reordenarlas/quitarlas en el Editor."""
        from app.core.plantillas import columnas_de_plantilla, definicion
        plantilla = config.get("accessibility", self._CLAVE_PLANTILLA, "") or ""
        cols = columnas_de_plantilla(plantilla)
        if not cols:
            d = definicion(self._CLAVE_PLANTILLA)
            cols = columnas_de_plantilla(d["default"]) if d else [("mensaje", _("Mensaje"))]
        while self.lista.GetColumnCount() > 0:
            self.lista.DeleteColumn(0)
        for i, (campo, encabezado) in enumerate(cols):
            self.lista.InsertColumn(i, encabezado, width=_ANCHOS.get(campo, 130))
        return cols

    # -------------------------------------------------------------- sesiones
    def _dir_logs(self):
        return get_data_path("logs")

    def _cargar_sesiones(self):
        try:
            archivos = glob.glob(str(self._dir_logs() / "session_*.log"))
            archivos.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except Exception:
            archivos = []
        self._sesiones = archivos
        etiquetas = []
        for i, ruta in enumerate(archivos):
            nombre = os.path.basename(ruta)
            m = re.search(r"session_(\d{8})_(\d{6})", nombre)
            try:
                etiqueta = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime(
                    "%Y-%m-%d %H:%M:%S") if m else nombre
            except Exception:
                etiqueta = nombre
            if i == 0:
                etiqueta += "  " + _("(actual)")
            etiquetas.append(etiqueta)
        self.choice_sesion.SetItems(etiquetas or [_("(no hay registros todavía)")])
        self.choice_sesion.SetSelection(0)

    def _ruta_sesion_actual(self):
        idx = self.choice_sesion.GetSelection()
        return self._sesiones[idx] if 0 <= idx < len(self._sesiones) else None

    def _es_sesion_viva(self):
        return self.choice_sesion.GetSelection() == 0 and bool(self._sesiones)

    # ---------------------------------------------------------------- cargar
    @staticmethod
    def _leer_cola(ruta):
        """Lee solo el final del archivo (hasta _MAX_BYTES_COLA). Devuelve (texto, truncado, tam)."""
        tam = os.path.getsize(ruta)
        with open(ruta, "rb") as f:
            if tam > _MAX_BYTES_COLA:
                f.seek(tam - _MAX_BYTES_COLA)
                datos = f.read()
                corte = datos.find(b"\n")
                if corte != -1:
                    datos = datos[corte + 1:]
                truncado = True
            else:
                datos = f.read()
                truncado = False
        return datos.decode("utf-8", errors="replace"), truncado, tam

    def _cargar_log(self):
        """Lee y trocea la sesión EN SEGUNDO PLANO (no bloquea); un token descarta el resultado si
        se cambia de sesión antes de terminar. La lista virtual se pinta al instante después."""
        ruta = self._ruta_sesion_actual()
        if not ruta or not os.path.exists(ruta):
            self._registros = []
            self._pintar()
            return
        self._carga_token += 1
        token = self._carga_token
        self.lbl_estado.SetLabel(_("Cargando…"))

        def _bg(ctx):
            texto, truncado, tam = self._leer_cola(ruta)
            return _parsear_registros(texto), truncado, tam, texto

        def _fin(res, err):
            if not self or token != self._carga_token:
                return
            if err:
                self.lbl_estado.SetLabel(_("Error al leer el registro: {0}").format(err))
                return
            self._registros, self._truncado, self._tam_total, self._texto_crudo = res
            self._pintar()

        from app.core.task_manager import task_manager
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _registros_filtrados(self):
        min_idx = self.choice_filtro.GetSelection()
        if min_idx <= 0:
            return list(self._registros)
        umbral = _RANK[_NIVELES[min_idx - 1]]
        return [r for r in self._registros if _RANK.get(r["nivel"], 1) >= umbral]

    def _pintar(self, anunciar=False, al_final=False):
        """Vuelca los registros (filtrados) a la lista VIRTUAL. Rellenar el modelo (diccionario de
        celdas) es O(n) en Python puro y el conteo se fija una sola vez: pintar miles de filas es
        instantáneo (solo se dibuja lo visible)."""
        self._mostrados = self._registros_filtrados()
        cols = self._cols
        self.lista.DeleteAllItems()  # limpia el modelo (celdas/lecturas) y pone _n=0
        celdas = {}
        for idx, r in enumerate(self._mostrados):
            celdas[idx] = {i: ((r.get(campo, "") or "").replace("\n", "  |  "))
                           for i, (campo, _e) in enumerate(cols)}
        self.lista._celdas = celdas
        self.lista._n = len(self._mostrados)
        try:
            self.lista.SetItemCount(self.lista._n)
        except Exception:
            pass
        estado = _("{0} entradas ({1} en total).").format(len(self._mostrados), len(self._registros))
        if self._truncado:
            estado += " " + _("Archivo grande ({0:.1f} MB): solo el final.").format(
                self._tam_total / 1_000_000.0)
        self.lbl_estado.SetLabel(estado)
        if self.lista._n:
            fila = self.lista._n - 1 if al_final else 0
            try:
                self.lista.Focus(fila)
                self.lista.Select(fila)
                self.lista.EnsureVisible(fila)
            except Exception:
                pass
        if anunciar:
            speak(_("{0} entradas.").format(len(self._mostrados)), interrupt=True)

    # ---------------------------------------------------------------- buscar
    def _buscar(self, direccion):
        aguja = (self.txt_buscar.GetValue() or "").lower()
        if not aguja:
            speak(_("Escribe algo que buscar."), interrupt=True)
            self.txt_buscar.SetFocus()
            return
        n = len(self._mostrados)
        if not n:
            return
        try:
            desde = self.lista.GetFocusedItem()
        except Exception:
            desde = -1
        if desde < 0:
            desde = -1 if direccion > 0 else n
        orden = (list(range(desde + 1, n)) + list(range(0, desde + 1)) if direccion > 0
                 else list(range(desde - 1, -1, -1)) + list(range(n - 1, desde - 1, -1)))
        for i in orden:
            r = self._mostrados[i]
            if (aguja in r["mensaje"].lower() or aguja in r["modulo"].lower()
                    or aguja in r["nivel"].lower() or aguja in r["hora"].lower()):
                self.lista.SetFocus()
                self.lista.Focus(i)
                self.lista.Select(i)
                self.lista.EnsureVisible(i)
                return
        speak(_("No se encontró: {0}").format(self.txt_buscar.GetValue()), interrupt=True)

    # -------------------------------------------------------------- acciones
    def _on_copiar_todo(self, event):
        # Copia SOLO lo que se está viendo con el filtro actual (Todos / DEBUG / Errores...): si el
        # usuario filtró a Errores y hay 3, copia esos 3; si no hay nada, lo avisa. Cada entrada se
        # reconstruye con su hora, nivel, módulo y mensaje (el mensaje conserva las trazas).
        mostrados = getattr(self, "_mostrados", [])
        if not mostrados:
            nivel = self.choice_filtro.GetStringSelection() or _("ese nivel")
            notificar(_("No hay nada que copiar con el filtro actual ({0}).").format(nivel))
            return
        lineas = []
        for r in mostrados:
            modulo = ("[" + r["modulo"] + "] ") if r.get("modulo") else ""
            hora = (r.get("hora") or "").strip()
            lineas.append(f"{hora} {r.get('nivel','')} {modulo}{r.get('mensaje','')}".strip())
        texto = "\n".join(lineas)
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(texto))
            finally:
                wx.TheClipboard.Close()
            notificar(_("Copiadas {0} entradas al portapapeles.").format(len(mostrados)))

    def _on_abrir_carpeta(self, event):
        try:
            os.startfile(str(self._dir_logs()))
        except Exception as e:
            notificar(_("No se pudo abrir la carpeta: {0}").format(e))

    def _on_editar_plantilla(self, event):
        """Abre el Editor de plantillas para tocar las COLUMNAS del registro; al cerrarlo, se
        reconstruyen las columnas y se repinta con lo elegido."""
        try:
            from .plantillas_dialog import EditorPlantillasDialog
            dlg = EditorPlantillasDialog(self)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            notificar(_("No se pudo abrir el editor de plantillas: {0}").format(e))
            return
        self._cols = self._crear_columnas()
        self._pintar()

    def _on_limpiar(self, event):
        ruta = self._ruta_sesion_actual()
        if not ruta:
            return
        aviso = _("¿Vaciar el archivo de esta sesión?")
        if self._es_sesion_viva():
            aviso += "\n\n" + _("Es la sesión en curso: se irán añadiendo entradas nuevas.")
        dlg = wx.MessageDialog(self, aviso, _("Confirmar limpieza"), wx.YES_NO | wx.ICON_QUESTION)
        try:
            if dlg.ShowModal() != wx.ID_YES:
                return
        finally:
            dlg.Destroy()
        try:
            with open(ruta, "w", encoding="utf-8"):
                pass
            self._cargar_log()
            notificar(_("Archivo de esta sesión vaciado."))
        except Exception as e:
            notificar(_("No se pudo vaciar: {0}").format(e))

    # -------------------------------------------------- nivel guardado (config)
    def _sincronizar_nivel_guardado(self):
        actual = str(config.get("general", "log_level", "INFO")).upper()
        self.choice_guardar.SetSelection({"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}.get(actual, 1))

    def _on_cambiar_nivel_guardado(self, event):
        elegido = ["DEBUG", "INFO", "WARNING", "ERROR"][self.choice_guardar.GetSelection()]
        config.set("general", "log_level", elegido)
        try:
            logging.getLogger().setLevel(getattr(logging, elegido, logging.INFO))
        except Exception:
            pass
        notificar(_("Se guardará a partir de {0}. Se aplica del todo al reiniciar.").format(elegido))

    # ------------------------------------------------------------- seguir vivo
    def _on_seguir(self, event):
        if self.cb_seguir.GetValue():
            if not self._es_sesion_viva():
                self.choice_sesion.SetSelection(0)
                self._cargar_log()
            self._timer.Start(2500)
            speak(_("Siguiendo el registro en vivo."), interrupt=True)
        else:
            self._timer.Stop()

    def _on_tick(self, event):
        if not self._es_sesion_viva():
            self._timer.Stop()
            self.cb_seguir.SetValue(False)
            return
        if self._tick_en_vuelo:
            return
        ruta = self._ruta_sesion_actual()
        if not ruta or not os.path.exists(ruta):
            return
        self._tick_en_vuelo = True

        def _bg(ctx):
            texto, truncado, tam = self._leer_cola(ruta)
            return _parsear_registros(texto), truncado, tam, texto

        def _fin(res, err):
            if not self:
                return
            self._tick_en_vuelo = False
            if err or not self.cb_seguir.GetValue():
                return
            nuevos, self._truncado, self._tam_total, self._texto_crudo = res
            if len(nuevos) != len(self._registros):  # solo repintar si hay novedades
                self._registros = nuevos
                self._pintar(al_final=True)

        from app.core.task_manager import task_manager
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------ cierre
    def _on_char_hook(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.Close()
            return
        if key == wx.WXK_F3:
            self._buscar(-1 if event.ShiftDown() else 1)
            return
        if key == ord("F") and event.ControlDown():
            self.txt_buscar.SetFocus()
            self.txt_buscar.SelectAll()
            return
        event.Skip()

    def _on_close(self, event):
        try:
            self._timer.Stop()
        except Exception:
            pass
        padre = self.GetParent()
        if getattr(padre, "_log_viewer", None) is self:
            padre._log_viewer = None
        self.Destroy()
