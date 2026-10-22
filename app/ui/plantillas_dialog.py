# -*- coding: utf-8 -*-
"""
Editor accesible y completo de PLANTILLAS de lectura: el usuario configura a su gusto cómo lee
el lector de pantalla cada elemento (publicaciones, notificaciones, perfiles, anuncios,
conversaciones y artículos RSS). Incluye presets, inserción de campos, vista previa EN VIVO con
datos de ejemplo, lectura de la vista previa y restaurar por defecto (una o todas). Todo por
teclado y verbalizado.
"""

import wx
import logging
from app.core.speech import speak
from app.core.config_manager import config
from app.core.plantillas import PLANTILLAS, render_ejemplo, definicion, REDES, plantillas_de_red
from .accessible_controls import AccessibleTextCtrl

log = logging.getLogger(__name__)


class EditorPlantillasDialog(wx.Dialog):
    """Editor de las plantillas de lectura del lector de pantalla."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Editor de plantillas de lectura"),
                         size=(720, 620), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Editor de plantillas de lectura"))
        # Estado de trabajo: texto actual de cada plantilla (no se guarda hasta Aceptar).
        self._valores = {}
        for p in PLANTILLAS:
            self._valores[p["clave"]] = config.get("accessibility", p["clave"], p["default"]) or p["default"]
        # Copia para detectar al guardar si hubo cambios (para avisar de que hay que reiniciar).
        self._valores_iniciales = dict(self._valores)
        self.hubo_cambios = False
        self._red_actual = REDES[0][0]
        self._plantillas_red = plantillas_de_red(self._red_actual)
        self._clave_actual = self._plantillas_red[0]["clave"]
        self._init_ui()
        self.CenterOnParent()
        self._rellenar_plantillas_de_red()
        self._cargar_plantilla(0)
        self.choice_red.SetFocus()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)

        # Red social: cada red tiene su PROPIO juego de plantillas independiente.
        s.Add(wx.StaticText(self, label=_("&Red social:")), 0, wx.LEFT | wx.TOP, 8)
        self.choice_red = wx.Choice(self, choices=[n() for _c, n in REDES], name=_("Red social"))
        self.choice_red.SetSelection(0)
        self.choice_red.Bind(wx.EVT_CHOICE, self._on_cambiar_red)
        s.Add(self.choice_red, 0, wx.EXPAND | wx.ALL, 8)

        # Plantilla a editar (de la red elegida).
        s.Add(wx.StaticText(self, label=_("&Plantilla a editar:")), 0, wx.LEFT | wx.TOP, 8)
        self.choice_plantilla = wx.Choice(self, choices=[], name=_("Plantilla a editar"))
        self.choice_plantilla.Bind(wx.EVT_CHOICE, self._on_cambiar_plantilla)
        s.Add(self.choice_plantilla, 0, wx.EXPAND | wx.ALL, 8)

        # Preset rápido.
        s.Add(wx.StaticText(self, label=_("&Modelo rápido (rellena el texto de abajo):")),
              0, wx.LEFT | wx.TOP, 8)
        self.choice_preset = wx.Choice(self, choices=[], name=_("Modelo rápido de plantilla"))
        self.choice_preset.Bind(wx.EVT_CHOICE, self._on_preset)
        s.Add(self.choice_preset, 0, wx.EXPAND | wx.ALL, 8)

        # Texto de la plantilla.
        s.Add(wx.StaticText(self, label=_("&Texto de la plantilla (usa los campos entre llaves, p. ej. {content}):")),
              0, wx.LEFT | wx.TOP, 8)
        self.txt_plantilla = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE, name=_("Texto de la plantilla"),
            ayuda_texto=_("Escribe cómo quieres que se lea. Inserta campos con la lista de abajo. "
                          "Puedes usar los separadores que prefieras. La vista previa se actualiza sola."))
        self.txt_plantilla.Bind(wx.EVT_TEXT, self._on_texto_cambiado)
        s.Add(self.txt_plantilla, 1, wx.EXPAND | wx.ALL, 8)

        # Campos disponibles + insertar.
        s.Add(wx.StaticText(self, label=_("&Campos disponibles (Intro o doble clic, o el botón Insertar):")),
              0, wx.LEFT | wx.TOP, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.lista_campos = wx.ListBox(self, name=_("Campos disponibles"))
        self.lista_campos.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self._insertar_campo())

        def _campos_key(ev):
            if ev.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
                self._insertar_campo()
            else:
                ev.Skip()
        self.lista_campos.Bind(wx.EVT_KEY_DOWN, _campos_key)
        fila.Add(self.lista_campos, 1, wx.EXPAND | wx.ALL, 4)
        col_btns = wx.BoxSizer(wx.VERTICAL)
        b_ins = wx.Button(self, label=_("&Insertar campo"))
        b_ins.Bind(wx.EVT_BUTTON, lambda e: self._insertar_campo())
        col_btns.Add(b_ins, 0, wx.EXPAND | wx.ALL, 4)
        b_leer = wx.Button(self, label=_("&Leer la vista previa"))
        b_leer.Bind(wx.EVT_BUTTON, lambda e: speak(self.txt_preview.GetValue(), interrupt=True))
        col_btns.Add(b_leer, 0, wx.EXPAND | wx.ALL, 4)
        b_def = wx.Button(self, label=_("&Restaurar esta"))
        b_def.Bind(wx.EVT_BUTTON, self._on_restaurar)
        col_btns.Add(b_def, 0, wx.EXPAND | wx.ALL, 4)
        b_def_all = wx.Button(self, label=_("Restaurar &todas"))
        b_def_all.Bind(wx.EVT_BUTTON, self._on_restaurar_todas)
        col_btns.Add(b_def_all, 0, wx.EXPAND | wx.ALL, 4)
        fila.Add(col_btns, 0)
        s.Add(fila, 1, wx.EXPAND | wx.ALL, 4)

        # Vista previa en vivo.
        s.Add(wx.StaticText(self, label=_("Vista pre&via (con datos de ejemplo):")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_preview = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY, name=_("Vista previa de la plantilla"),
            ayuda_texto=_("Así sonará la lectura con datos de ejemplo. Se actualiza al escribir."))
        s.Add(self.txt_preview, 0, wx.EXPAND | wx.ALL, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.SetDefault()
        b_ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        btns.Add(b_ok, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizer(s)

    # --- red / carga / cambio de plantilla ---
    def _rellenar_plantillas_de_red(self):
        """Rellena el desplegable de plantillas con las de la red seleccionada."""
        self._plantillas_red = plantillas_de_red(self._red_actual)
        self.choice_plantilla.Set([p["nombre"]() for p in self._plantillas_red])
        if self._plantillas_red:
            self.choice_plantilla.SetSelection(0)

    def _on_cambiar_red(self, event):
        # Conservar lo escrito antes de cambiar de red.
        self._valores[self._clave_actual] = self.txt_plantilla.GetValue()
        self._red_actual = REDES[self.choice_red.GetSelection()][0]
        self._rellenar_plantillas_de_red()
        self._cargar_plantilla(0)

    def _cargar_plantilla(self, idx):
        d = self._plantillas_red[idx]
        self._clave_actual = d["clave"]
        # Campos.
        self.lista_campos.Clear()
        self._campos_actuales = [c for c, _desc in d["campos"]]
        for campo, desc in d["campos"]:
            self.lista_campos.Append("{{{0}}} — {1}".format(campo, desc()))
        if self.lista_campos.GetCount():
            self.lista_campos.SetSelection(0)
        # Presets.
        self.choice_preset.Clear()
        self._presets_actuales = list(d.get("presets", []))
        self.choice_preset.Append(_("(elige un modelo)"))
        for nombre, _plant in self._presets_actuales:
            self.choice_preset.Append(nombre())
        self.choice_preset.SetSelection(0)
        # Texto (esto dispara _on_texto_cambiado, que refresca la vista previa).
        self.txt_plantilla.ChangeValue(self._valores.get(d["clave"], d["default"]))
        self._refrescar_preview()

    def _on_cambiar_plantilla(self, event):
        # Conservar lo escrito en la plantilla anterior antes de cambiar.
        self._valores[self._clave_actual] = self.txt_plantilla.GetValue()
        self._cargar_plantilla(self.choice_plantilla.GetSelection())

    def _on_preset(self, event):
        sel = self.choice_preset.GetSelection()
        if sel <= 0 or sel - 1 >= len(self._presets_actuales):
            return
        _nombre, plantilla = self._presets_actuales[sel - 1]
        self.txt_plantilla.ChangeValue(plantilla)
        self._refrescar_preview()
        speak(_("Modelo aplicado."), interrupt=True)

    # --- edición ---
    def _insertar_campo(self):
        i = self.lista_campos.GetSelection()
        if i < 0 or i >= len(self._campos_actuales):
            return
        marcador = "{{{0}}}".format(self._campos_actuales[i])
        pos = self.txt_plantilla.GetInsertionPoint()
        self.txt_plantilla.WriteText(marcador)
        self.txt_plantilla.SetInsertionPoint(pos + len(marcador))
        self.txt_plantilla.SetFocus()
        speak(_("Insertado {0}.").format(marcador), interrupt=True)

    def _on_texto_cambiado(self, event):
        self._refrescar_preview()

    def _refrescar_preview(self):
        # La plantilla define las COLUMNAS del buffer: una por cada {campo}, en orden. Mostramos
        # qué columnas saldrán y un ejemplo de fila, para que se entienda el resultado.
        from app.core.plantillas import columnas_de_plantilla
        plantilla = self.txt_plantilla.GetValue()
        cols = columnas_de_plantilla(plantilla)
        encabezados = ", ".join(h for _c, h in cols) if cols else _("(ninguna)")
        try:
            ejemplo = render_ejemplo(self._clave_actual, plantilla)
        except Exception:
            ejemplo = ""
        texto = _("Columnas (en orden): {0}").format(encabezados)
        if ejemplo:
            texto += "\n" + _("Ejemplo de fila: {0}").format(ejemplo)
        self.txt_preview.ChangeValue(texto)

    def _on_restaurar(self, event):
        d = definicion(self._clave_actual)
        self.txt_plantilla.ChangeValue(d["default"])
        self._valores[self._clave_actual] = d["default"]
        self._refrescar_preview()
        speak(_("Plantilla restaurada a su valor por defecto."), interrupt=True)

    def _on_restaurar_todas(self, event):
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Restaurar TODAS las plantillas a sus valores por defecto?"),
                     _("Restaurar todas")) != wx.ID_YES:
            return
        for p in PLANTILLAS:
            self._valores[p["clave"]] = p["default"]
        self.txt_plantilla.ChangeValue(self._valores[self._clave_actual])
        self._refrescar_preview()
        speak(_("Todas las plantillas restauradas."), interrupt=True)

    def _on_guardar(self, event):
        self._valores[self._clave_actual] = self.txt_plantilla.GetValue()
        # Normalizar (vacío -> valor por defecto) y detectar si algo cambió respecto a lo que
        # había al abrir. NO se persiste aquí: se guarda solo si el usuario confirma reiniciar
        # (el reinicio aplica las columnas); si dice que no, se descarta. Lo decide main_frame.
        finales = {}
        for clave, texto in self._valores.items():
            if not (texto or "").strip():
                d = definicion(clave)
                texto = d["default"] if d else texto
            finales[clave] = texto
        self._finales = finales
        self.hubo_cambios = any(
            finales.get(c) != self._valores_iniciales.get(c) for c in finales)
        self.EndModal(wx.ID_OK)
