# -*- coding: utf-8 -*-
"""
Diálogo de gestión de FILTROS DE CONTENIDO unificados (Mastodon + RSS).

Permite crear filtros que ocultan publicaciones/artículos por palabra clave o
expresión regular, o que los marcan automáticamente como leídos, destacados o
para leer más tarde.
"""

import wx
from app.core.speech import speak, notificar
from app.core.content_filters import ContentFilterEngine
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl


# Mapas de etiquetas legibles <-> valores internos
_SERVICIOS = [("all", _("Todos")), ("mastodon", _("Solo Mastodon")), ("bluesky", _("Solo Bluesky")),
              ("wsocial", _("Solo W Social")),
              ("rss", _("Solo RSS")), ("x", _("Solo X (Twitter)"))]
_CAMPOS = [("all", _("Texto y autor")), ("text", _("Solo el texto")), ("author", _("Solo el autor"))]
_ACCIONES = [
    ("hide", _("Ocultar")),
    ("mark_read", _("Marcar como leído")),
    ("star", _("Destacar")),
    ("read_later", _("Leer más tarde (solo RSS)")),
]


def _etiqueta(mapa, valor):
    for v, txt in mapa:
        if v == valor:
            return txt
    return valor


class FiltersDialog(wx.Dialog):
    """Crear, ver y eliminar filtros de contenido."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Filtros de contenido"), size=(640, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Diálogo de filtros de contenido"))
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("Filtros existentes:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de filtros de contenido")
        )
        self.lista.InsertColumn(0, _("Servicio"), width=140)
        self.lista.InsertColumn(1, _("Patrón"), width=180)
        self.lista.InsertColumn(2, _("Tipo"), width=80)
        self.lista.InsertColumn(3, _("Campo"), width=110)
        self.lista.InsertColumn(4, _("Acción"), width=120)
        self.lista.InsertColumn(5, _("Activo"), width=70)
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        # Botonera
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_nuevo = wx.Button(self, label=_("&Nuevo filtro..."))
        self.btn_nuevo.Bind(wx.EVT_BUTTON, self._on_nuevo)
        btns.Add(self.btn_nuevo, 0, wx.ALL, 5)

        self.btn_toggle = wx.Button(self, label=_("Activar/&Desactivar"))
        self.btn_toggle.Bind(wx.EVT_BUTTON, self._on_toggle)
        btns.Add(self.btn_toggle, 0, wx.ALL, 5)

        self.btn_eliminar = wx.Button(self, label=_("&Eliminar"))
        self.btn_eliminar.Bind(wx.EVT_BUTTON, self._on_eliminar)
        btns.Add(self.btn_eliminar, 0, wx.ALL, 5)

        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        sizer.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(sizer)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _cargar(self):
        self.lista.DeleteAllItems()
        self.filtros = ContentFilterEngine.listar_todos()
        for i, f in enumerate(self.filtros):
            pos = self.lista.InsertItem(i, _etiqueta(_SERVICIOS, f["service"]))
            self.lista.SetItem(pos, 1, f["pattern"])
            self.lista.SetItem(pos, 2, _("Regex") if f["is_regex"] else _("Texto"))
            self.lista.SetItem(pos, 3, _etiqueta(_CAMPOS, f["field"]))
            self.lista.SetItem(pos, 4, _etiqueta(_ACCIONES, f["action"]))
            self.lista.SetItem(pos, 5, _("Sí") if f["active"] else _("No"))
        if not self.filtros:
            speak(_("No hay filtros. Pulse Nuevo filtro para crear uno."), interrupt=True)

    def _seleccionado(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.filtros):
            return None
        return self.filtros[idx]

    def _on_nuevo(self, event):
        dlg = NewFilterDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            datos = dlg.get_datos()
            if datos and datos["pattern"]:
                ContentFilterEngine.crear(
                    datos["service"], datos["name"], datos["pattern"],
                    datos["is_regex"], datos["field"], datos["action"]
                )
                self._cargar()
                notificar(_("Filtro creado."))
        dlg.Destroy()

    def _on_toggle(self, event):
        f = self._seleccionado()
        if not f:
            return
        ContentFilterEngine.set_activo(f["id"], not f["active"])
        self._cargar()
        speak(_("Filtro activado.") if not f["active"] else _("Filtro desactivado."), interrupt=True)

    def _on_eliminar(self, event):
        f = self._seleccionado()
        if not f:
            return
        dlg = wx.MessageDialog(self, _("¿Eliminar este filtro?"), _("Confirmar"),
                               wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if dlg.ShowModal() == wx.ID_YES:
            ContentFilterEngine.eliminar(f["id"])
            self._cargar()
            notificar(_("Filtro eliminado."))
        dlg.Destroy()


class NewFilterDialog(wx.Dialog):
    """Formulario para crear un filtro nuevo."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Nuevo filtro de contenido"), size=(460, 420),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Diálogo nuevo filtro"))
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(self, label=_("Palabra, frase o expresión regular a buscar:")), 0, wx.ALL, 5)
        self.txt_patron = AccessibleTextCtrl(self, name=_("Patrón del filtro"),
                                             ayuda_texto=_("El texto que, si aparece, activa la acción del filtro."))
        s.Add(self.txt_patron, 0, wx.EXPAND | wx.ALL, 5)

        self.cb_regex = wx.CheckBox(self, label=_("Es una expresión regular"), name=_("Usar expresión regular"))
        s.Add(self.cb_regex, 0, wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Aplicar a:")), 0, wx.ALL, 5)
        self.choice_service = wx.Choice(self, choices=[t for _v, t in _SERVICIOS], name=_("Servicio del filtro"))
        self.choice_service.SetSelection(0)
        s.Add(self.choice_service, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Buscar en:")), 0, wx.ALL, 5)
        self.choice_field = wx.Choice(self, choices=[t for _v, t in _CAMPOS], name=_("Campo a evaluar"))
        self.choice_field.SetSelection(0)
        s.Add(self.choice_field, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Acción:")), 0, wx.ALL, 5)
        self.choice_action = wx.Choice(self, choices=[t for _v, t in _ACCIONES], name=_("Acción del filtro"))
        self.choice_action.SetSelection(0)
        s.Add(self.choice_action, 0, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(self, wx.ID_OK, _("&Crear"))
        btn_ok.SetDefault()
        btns.Add(btn_ok, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))

    def get_datos(self):
        return {
            "pattern": self.txt_patron.GetValue().strip(),
            "is_regex": self.cb_regex.GetValue(),
            "service": _SERVICIOS[self.choice_service.GetSelection()][0],
            "field": _CAMPOS[self.choice_field.GetSelection()][0],
            "action": _ACCIONES[self.choice_action.GetSelection()][0],
            "name": self.txt_patron.GetValue().strip(),
        }
