# -*- coding: utf-8 -*-
"""Fragmentos de texto reutilizables (snippets).

Textos que el usuario guarda una vez (firmas, respuestas frecuentes, avisos de accesibilidad,
enlaces habituales) y luego inserta en CUALQUIER campo de redacción con un atajo (Ctrl+Shift+J).
Se guardan en la configuración (una sola lista, compartida por todas las redes) para que estén
disponibles en todos los compositores sin duplicar datos.

Sigue el mismo patrón que traductor_redaccion.instalar_traductor_redaccion: se llama una vez por
campo de texto y engancha el atajo por EVT_CHAR_HOOK, sin tocar el resto del compositor.
"""
import wx

from app.core.config_manager import config
from app.core.speech import notificar, speak
from app.ui.accessible_controls import AccessibleListCtrl, CustomTextEntryDialog

try:
    _
except NameError:
    _ = lambda x: x


def obtener_snippets() -> list:
    """Lista de fragmentos guardados: [{'titulo': str, 'texto': str}]."""
    datos = config.get("general", "snippets", [])
    return [d for d in datos if isinstance(d, dict) and d.get("texto")]


def guardar_snippets(lista: list):
    config.set("general", "snippets", lista)


def instalar_snippets(text_ctrl):
    """Activa en un campo de texto el atajo para insertar un fragmento (por defecto Ctrl+Mayús+J).
    La tecla se lee del editor de atajos de la app (acción 'insertar_fragmento'), así que si el
    usuario la cambia, aquí surte efecto. Llamar una vez por cada campo de redacción."""
    if text_ctrl is None or getattr(text_ctrl, "_snippets_instalado", False):
        return
    text_ctrl._snippets_instalado = True

    def _on_char_hook(event):
        from app.ui.shortcut_manager import shortcut_manager
        if shortcut_manager.evento_coincide("insertar_fragmento", event):
            _insertar(text_ctrl)
            return
        event.Skip()

    text_ctrl.Bind(wx.EVT_CHAR_HOOK, _on_char_hook)


def _insertar(text_ctrl):
    snippets = obtener_snippets()
    if not snippets:
        # Sin fragmentos aún: ofrecer abrir el gestor para crear el primero.
        notificar(_("No tienes fragmentos guardados. Créalos desde Herramientas → «Fragmentos de texto»."))
        return
    parent = text_ctrl.GetTopLevelParent()
    opciones = [s["titulo"] or s["texto"][:40] for s in snippets]
    sel = wx.GetSingleChoice(_("Elige un fragmento para insertar:"), _("Fragmentos"), opciones, parent)
    if not sel:
        return
    texto = snippets[opciones.index(sel)]["texto"]
    try:
        ini, fin = text_ctrl.GetSelection()
        if ini != fin:
            text_ctrl.Replace(ini, fin, texto)
        else:
            text_ctrl.WriteText(texto)
        text_ctrl.SetFocus()
        notificar(_("Fragmento insertado."))
    except Exception:
        pass


class SnippetsDialog(wx.Dialog):
    """Gestor de fragmentos: crear, editar, borrar. Accesible y en español."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Fragmentos de texto"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Diálogo de fragmentos"))
        self.snippets = list(obtener_snippets())
        self._init_ui()
        self._refrescar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Tus fragmentos reutilizables (insértalos en cualquier "
                                          "mensaje con Ctrl+Mayús+J):")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de fragmentos"))
        self.lista.InsertColumn(0, _("Título"), width=180)
        self.lista.InsertColumn(1, _("Texto"), width=340)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_add = wx.Button(self, label=_("&Nuevo..."))
        b_add.Bind(wx.EVT_BUTTON, self._on_nuevo)
        btns.Add(b_add, 0, wx.ALL, 5)
        b_ed = wx.Button(self, label=_("&Editar..."))
        b_ed.Bind(wx.EVT_BUTTON, self._on_editar)
        btns.Add(b_ed, 0, wx.ALL, 5)
        b_del = wx.Button(self, label=_("E&liminar"))
        b_del.Bind(wx.EVT_BUTTON, self._on_eliminar)
        btns.Add(b_del, 0, wx.ALL, 5)
        btns.AddStretchSpacer()
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self._cerrar(), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self._cerrar())

    def _refrescar(self):
        self.lista.DeleteAllItems()
        for i, sn in enumerate(self.snippets):
            self.lista.InsertItem(i, sn.get("titulo", "") or "")
            self.lista.SetItem(i, 1, (sn.get("texto", "") or "").replace("\n", " ")[:200])
        if self.snippets:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _pedir(self, titulo="", texto=""):
        dt = CustomTextEntryDialog(self, _("Título del fragmento (para identificarlo):"),
                                   _("Fragmento"), value=titulo)
        if dt.ShowModal() != wx.ID_OK:
            dt.Destroy()
            return None
        t = dt.GetValue().strip()
        dt.Destroy()
        dc = CustomTextEntryDialog(self, _("Texto del fragmento:"), _("Fragmento"),
                                   value=texto, multiline=True)
        if dc.ShowModal() != wx.ID_OK:
            dc.Destroy()
            return None
        c = dc.GetValue()
        dc.Destroy()
        if not c.strip():
            speak(_("El texto no puede estar vacío."), interrupt=True)
            return None
        return {"titulo": t or c.strip()[:30], "texto": c}

    def _on_nuevo(self, event):
        nuevo = self._pedir()
        if nuevo:
            self.snippets.append(nuevo)
            self._refrescar()

    def _on_editar(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.snippets):
            speak(_("Selecciona un fragmento."), interrupt=True)
            return
        actual = self.snippets[idx]
        editado = self._pedir(actual.get("titulo", ""), actual.get("texto", ""))
        if editado:
            self.snippets[idx] = editado
            self._refrescar()

    def _on_eliminar(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.snippets):
            return
        del self.snippets[idx]
        self._refrescar()
        speak(_("Fragmento eliminado."), interrupt=True)

    def _cerrar(self):
        guardar_snippets(self.snippets)
        self.EndModal(wx.ID_CLOSE)
