# -*- coding: utf-8 -*-
"""
Diálogos de configuración de prompts y caché del servicio de IA.
Permite configurar las plantillas de prompts utilizadas para resúmenes, descripciones de imágenes y alt-texts.
"""

import logging
import wx

from app.core.speech import speak, notificar
from app.core.config_manager import config
from .accessible_controls import AccessibleTextCtrl

log = logging.getLogger(__name__)

class AIPromptsDialog(wx.Dialog):
    """Diálogo accesible para editar plantillas de prompts de IA."""
    
    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Configuración de Prompts de IA"),
            size=(600, 500),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo configuración de prompts")
        )
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        notebook = wx.Notebook(self, name=_("Pestañas de prompts"))
        
        # 1. Descripción de imagen
        p_desc = wx.Panel(notebook)
        sz_desc = wx.BoxSizer(wx.VERTICAL)
        sz_desc.Add(wx.StaticText(p_desc, label=_("Prompt para describir imágenes detalladamente:")), 0, wx.ALL, 5)
        self.txt_desc = AccessibleTextCtrl(
            p_desc,
            style=wx.TE_MULTILINE,
            value=config.get("ai", "prompt_image_description", "Describe esta imagen de forma detallada, enfocándote en las personas, objetos, texto legible y ambiente general. Tu descripción debe ser útil para una persona ciega."),
            name=_("Prompt descripción detallada")
        )
        sz_desc.Add(self.txt_desc, 1, wx.EXPAND | wx.ALL, 5)
        p_desc.SetSizer(sz_desc)
        notebook.AddPage(p_desc, _("Descripción"))
        
        # 2. Alt-text
        p_alt = wx.Panel(notebook)
        sz_alt = wx.BoxSizer(wx.VERTICAL)
        sz_alt.Add(wx.StaticText(p_alt, label=_("Prompt para generar texto alternativo conciso:")), 0, wx.ALL, 5)
        self.txt_alt = AccessibleTextCtrl(
            p_alt,
            style=wx.TE_MULTILINE,
            value=config.get("ai", "prompt_alt_text", "Genera un texto alternativo conciso y descriptivo (máximo dos frases) para esta imagen, apto para un atributo alt de HTML."),
            name=_("Prompt alt-text")
        )
        sz_alt.Add(self.txt_alt, 1, wx.EXPAND | wx.ALL, 5)
        p_alt.SetSizer(sz_alt)
        notebook.AddPage(p_alt, _("Alt-Text"))
        
        # 3. Resúmenes
        p_sum = wx.Panel(notebook)
        sz_sum = wx.BoxSizer(wx.VERTICAL)
        sz_sum.Add(wx.StaticText(p_sum, label=_("Prompt para resumir artículos o hilos:")), 0, wx.ALL, 5)
        self.txt_sum = AccessibleTextCtrl(
            p_sum,
            style=wx.TE_MULTILINE,
            value=config.get("ai", "prompt_text_summary", "Genera un resumen estructurado, claro y sintético del siguiente texto, destacando los puntos clave en no más de tres párrafos:"),
            name=_("Prompt de resumen")
        )
        sz_sum.Add(self.txt_sum, 1, wx.EXPAND | wx.ALL, 5)
        p_sum.SetSizer(sz_sum)
        notebook.AddPage(p_sum, _("Resumen"))
        
        # 4. Hashtags
        p_hash = wx.Panel(notebook)
        sz_hash = wx.BoxSizer(wx.VERTICAL)
        sz_hash.Add(wx.StaticText(p_hash, label=_("Prompt para sugerir hashtags:")), 0, wx.ALL, 5)
        self.txt_hash = AccessibleTextCtrl(
            p_hash,
            style=wx.TE_MULTILINE,
            value=config.get("ai", "prompt_hashtags", "Analiza el siguiente texto y sugiere entre 3 y 5 hashtags relevantes en español:"),
            name=_("Prompt de hashtags")
        )
        sz_hash.Add(self.txt_hash, 1, wx.EXPAND | wx.ALL, 5)
        p_hash.SetSizer(sz_hash)
        notebook.AddPage(p_hash, _("Hashtags"))
        
        sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 10)
        
        # Botones
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_save = wx.Button(self, wx.ID_OK, _("&Guardar"), name=_("Guardar prompts"))
        btn_cancel = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"))
        
        btn_sizer.Add(btn_save, 0, wx.ALL, 5)
        btn_sizer.Add(btn_cancel, 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)
        
        self.Bind(wx.EVT_BUTTON, self._on_save, id=wx.ID_OK)
        notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_page_changed)

    def _on_page_changed(self, event):
        page_text = event.GetEventObject().GetPageText(event.GetSelection())
        speak(f"Plantilla {page_text}", interrupt=True)
        event.Skip()

    def _on_save(self, event):
        with config.batch():
            config.set("ai", "prompt_image_description", self.txt_desc.GetValue())
            config.set("ai", "prompt_alt_text", self.txt_alt.GetValue())
            config.set("ai", "prompt_text_summary", self.txt_sum.GetValue())
            config.set("ai", "prompt_hashtags", self.txt_hash.GetValue())
        log.info("Plantillas de prompts de IA actualizadas.")
        notificar(_("Prompts guardados."))
        self.EndModal(wx.ID_OK)


# El gestor de prompts es el diálogo de plantillas
PromptManagerDialog = AIPromptsDialog


class AIHistoryDialog(wx.Dialog):
    """Visor del historial de operaciones de IA."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Historial de IA"), size=(680, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Diálogo historial de IA"))
        from .accessible_controls import AccessibleListCtrl
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista del historial de IA"))
        self.lista.InsertColumn(0, _("Función"), width=160)
        self.lista.InsertColumn(1, _("Resultado"), width=380)
        self.lista.InsertColumn(2, _("Fecha"), width=120)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        self.txt_detalle = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                              name=_("Detalle de la operación"))
        sizer.Add(self.txt_detalle, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        btn_limpiar = wx.Button(self, label=_("&Limpiar historial"))
        btn_limpiar.Bind(wx.EVT_BUTTON, self._on_limpiar)
        btns.Add(btn_limpiar, 0, wx.ALL, 5)
        btns.AddStretchSpacer()
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        sizer.Add(btns, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(sizer)
        self.CenterOnParent()

        self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_sel)
        self._cargar()

    def _cargar(self):
        from app.core.database import db
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self.rows = db.query("SELECT funcion, entrada, respuesta, created_at FROM ai_history ORDER BY id DESC LIMIT 200")
        for idx, r in enumerate(self.rows):
            funcion, entrada, respuesta, fecha = r
            pos = self.lista.InsertItem(idx, funcion or "")
            # Respuesta COMPLETA en la columna (sin recortar), para poder leerla entera al
            # navegar por columnas, igual que en los buffers.
            self.lista.SetItem(pos, 1, (respuesta or "").replace("\n", " "))
            self.lista.SetItem(pos, 2, str(fecha or "")[:19].replace("T", " "))
            self.lista.establecer_lectura(pos, f"{funcion}: {respuesta or ''}")
        if not self.rows:
            notificar(_("El historial de IA está vacío."))

    def _on_sel(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self.rows):
            funcion, entrada, respuesta, fecha = self.rows[idx]
            self.txt_detalle.SetValue(
                _("Función: {0}\nFecha: {1}\n\nEntrada:\n{2}\n\nResultado:\n{3}").format(
                    funcion, fecha, entrada or "", respuesta or "")
            )

    def _on_limpiar(self, event):
        from app.core.database import db
        db.execute("DELETE FROM ai_history")
        self._cargar()
        self.txt_detalle.SetValue("")
        notificar(_("Historial de IA borrado."))
