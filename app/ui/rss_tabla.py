# -*- coding: utf-8 -*-
"""Ver una tabla de un artículo en una lista accesible (Intro sobre la tabla en el lector
limpio). La primera fila son los títulos de las columnas; con arriba y abajo se va por filas y
con izquierda y derecha por columnas (cada celda se lee con su fila y su columna). "Ver como
HTML" la abre como página, navegable con los comandos de tabla del lector de pantalla."""
import wx

from app.core import texto_estructurado as te
from .accessible_controls import AccessibleListCtrl, habilitar_escape_cierre


class TablaDialog(wx.Dialog):
    def __init__(self, parent, marca):
        titulo = _("Tabla de {0} filas y {1} columnas").format(marca.nivel, marca.columnas)
        super().__init__(parent, title=titulo, size=(760, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        self.marca = marca
        filas = [f.split("\t") for f in (marca.referencia or "").split("\n") if f]
        columnas = max((len(f) for f in filas), default=1)
        s = wx.BoxSizer(wx.VERTICAL)
        if marca.texto:
            s.Add(wx.StaticText(self, label=marca.texto), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES
                                        | wx.LC_VRULES, name=_("Contenido de la tabla"))
        cab = filas[0] if filas else []
        for c in range(columnas):
            nombre = cab[c].strip() if c < len(cab) and cab[c].strip() else _("Columna {0}").format(c + 1)
            self.lista.InsertColumn(c, nombre, width=180)
        for i, fila in enumerate(filas[1:] or filas[:1]):
            self.lista.InsertItem(i, fila[0] if fila else "")
            for c in range(1, columnas):
                self.lista.SetItem(i, c, fila[c] if c < len(fila) else "")
            partes = []
            for c in range(columnas):
                valor = fila[c] if c < len(fila) else ""
                partes.append(f"{self.lista.GetColumn(c).GetText()}: {valor or _('en blanco')}")
            self.lista.establecer_lectura(i, ", ".join(partes))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_html = wx.Button(self, label=_("Ver como &HTML"), name=_("Ver la tabla como página HTML"))
        b_html.Bind(wx.EVT_BUTTON, self._ver_html)
        botones.Add(b_html, 0, wx.ALL, 5)
        b_copiar = wx.Button(self, label=_("&Copiar la tabla"), name=_("Copiar la tabla al portapapeles"))
        b_copiar.Bind(wx.EVT_BUTTON, self._copiar)
        botones.Add(b_copiar, 0, wx.ALL, 5)
        botones.AddStretchSpacer()
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 5)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self.Layout()
        self.CenterOnParent()
        if self.lista.GetItemCount():
            self.lista.Select(0)
            self.lista.Focus(0)
        self.lista.SetFocus()

    def _ver_html(self, event):
        from .html_viewer import HtmlViewerDialog
        dlg = HtmlViewerDialog(self, self.GetTitle(), te.html_de_tabla(self.marca),
                               texto_plano=self.marca.referencia)
        dlg.ShowModal()
        dlg.Destroy()

    def _copiar(self, event):
        from app.core.speech import notificar
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(self.marca.referencia))
            wx.TheClipboard.Close()
            notificar(_("Tabla copiada al portapapeles, con tabuladores entre las celdas."))
