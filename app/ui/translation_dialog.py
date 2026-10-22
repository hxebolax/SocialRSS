# -*- coding: utf-8 -*-
"""
Vistas para la gestión e historial de traducciones de la suite.
Permite inspeccionar traducciones previas y realizar búsquedas de texto completo en el historial con SQLite FTS5.
"""

import logging
import wx

from app.core.speech import speak, notificar
from app.core.database import db
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl

log = logging.getLogger(__name__)

class TranslationHistoryDialog(wx.Dialog):
    """Diálogo accesible para explorar el historial de traducciones."""

    def __init__(self, parent):
        super().__init__(
            parent,
            title=_("Historial de Traducciones"),
            size=(700, 500),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo historial de traducciones")
        )

        self.rows_history = []

        self._init_ui()
        self.CenterOnParent()
        self._cargar_historial()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Panel de búsqueda
        sz_busc = wx.BoxSizer(wx.HORIZONTAL)
        sz_busc.Add(wx.StaticText(self, label=_("Buscar en traducciones:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_buscar = AccessibleTextCtrl(
            self,
            name=_("Cuadro de búsqueda en traducciones"),
            ayuda_texto=_("Introduzca palabras clave para buscar en las traducciones guardadas.")
        )
        self.txt_buscar.Bind(wx.EVT_TEXT, self._on_buscar_cambio)
        sz_busc.Add(self.txt_buscar, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(sz_busc, 0, wx.EXPAND | wx.ALL, 5)

        # Lista accesible (mismo patrón sencillo que el historial de IA)
        self.lista_historial = AccessibleListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de traducciones guardadas")
        )
        self.lista_historial.InsertColumn(0, _("Original"), width=280)
        self.lista_historial.InsertColumn(1, _("Traducido"), width=280)
        self.lista_historial.InsertColumn(2, _("Idioma"), width=90)
        self.lista_historial.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
        sizer.Add(self.lista_historial, 1, wx.EXPAND | wx.ALL, 5)

        # Detalle del elemento seleccionado (original + traducción)
        self.txt_detalle = AccessibleTextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SUNKEN,
            name=_("Detalle de la traducción seleccionada")
        )
        sizer.Add(self.txt_detalle, 1, wx.EXPAND | wx.ALL, 5)

        # Acciones
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_copiar = wx.Button(self, label=_("&Copiar traducción"), name=_("Copiar texto traducido"))
        self.btn_copiar.Bind(wx.EVT_BUTTON, self._on_copiar)
        btn_sizer.Add(self.btn_copiar, 0, wx.ALL, 5)

        self.btn_limpiar = wx.Button(self, label=_("&Limpiar historial"), name=_("Vaciar historial de traducciones"))
        self.btn_limpiar.Bind(wx.EVT_BUTTON, self._on_limpiar_historial)
        btn_sizer.Add(self.btn_limpiar, 0, wx.ALL, 5)

        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 5)

        self.SetSizer(sizer)

    def _cargar_historial(self, query_text: str = "") -> None:
        self.lista_historial.DeleteAllItems()
        self.lista_historial.limpiar_lecturas()
        self.rows_history = []

        if query_text.strip():
            # Búsqueda con FTS5; si falla, se cae a una búsqueda simple con LIKE.
            words = [w for w in query_text.split() if w]
            safe_query = " ".join(f'"{w}"' for w in words)
            sql_fts = """
            SELECT h.id, h.original_text, h.translated_text, h.source_lang, h.target_lang
            FROM translation_history h
            JOIN translation_history_fts f ON f.history_id = h.id
            WHERE translation_history_fts MATCH ?
            ORDER BY h.created_at DESC LIMIT 100
            """
            try:
                self.rows_history = db.query(sql_fts, (safe_query,))
            except Exception as e:
                log.debug(f"FTS no disponible, usando LIKE: {e}")
                like = f"%{query_text.strip()}%"
                sql_like = """
                SELECT id, original_text, translated_text, source_lang, target_lang
                FROM translation_history
                WHERE original_text LIKE ? OR translated_text LIKE ?
                ORDER BY created_at DESC LIMIT 100
                """
                try:
                    self.rows_history = db.query(sql_like, (like, like))
                except Exception as e2:
                    log.error(f"Error al buscar en el historial de traducciones: {e2}")
                    self.rows_history = []
        else:
            sql = """
            SELECT id, original_text, translated_text, source_lang, target_lang
            FROM translation_history
            ORDER BY created_at DESC LIMIT 100
            """
            try:
                self.rows_history = db.query(sql)
            except Exception as e:
                log.error(f"Error al cargar el historial de traducciones: {e}")
                self.rows_history = []

        for idx, r in enumerate(self.rows_history):
            _db_id, orig, trans, sl, tl = r
            # Texto COMPLETO (sin recortar) para poder leerlo entero al navegar por columnas.
            c_orig = (orig or "").replace("\n", " ").strip()
            c_trans = (trans or "").replace("\n", " ").strip()
            lang = f"{sl}->{tl}"
            pos = self.lista_historial.InsertItem(idx, c_orig)
            self.lista_historial.SetItem(pos, 1, c_trans)
            self.lista_historial.SetItem(pos, 2, lang)
            self.lista_historial.establecer_lectura(
                pos, _("Traducción {0}. Original: {1}. Traducido: {2}.").format(idx + 1, c_orig, c_trans))

        n = len(self.rows_history)
        if n == 0:
            notificar(_("No hay traducciones guardadas."))
        else:
            speak(_("{0} traducciones.").format(n), interrupt=True)
            # Dejar el foco en la lista para poder revisarla de inmediato.
            self.lista_historial.Focus(0)
            self.lista_historial.Select(0)
            self.lista_historial.SetFocus()

    def _on_buscar_cambio(self, event):
        self._cargar_historial(self.txt_buscar.GetValue())

    def _on_item_selected(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self.rows_history):
            r = self.rows_history[idx]
            self.txt_detalle.SetValue(
                _("Original:\n{0}\n\nTraducción:\n{1}").format(r[1] or "", r[2] or ""))

    def _on_copiar(self, event):
        if not (0 <= self.lista_historial.GetFocusedItem() < len(self.rows_history)):
            return
        txt = self.rows_history[self.lista_historial.GetFocusedItem()][2] or ""
        if txt.strip():
            try:
                if wx.TheClipboard.Open():
                    wx.TheClipboard.SetData(wx.TextDataObject(txt))
                    wx.TheClipboard.Close()
                    notificar(_("Traducción copiada al portapapeles."))
            except Exception:
                pass

    def _on_limpiar_historial(self, event):
        dlg = wx.MessageDialog(
            self,
            _("¿Seguro que desea vaciar por completo el historial de traducciones?"),
            _("Confirmar vaciado"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
        )
        if dlg.ShowModal() == wx.ID_YES:
            db.execute("DELETE FROM translation_history")
            try:
                db.execute("DELETE FROM translation_history_fts")
            except Exception:
                pass
            self.txt_detalle.SetValue("")
            self._cargar_historial()
            speak(_("Historial vaciado."), interrupt=True)
        dlg.Destroy()
