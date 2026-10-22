# -*- coding: utf-8 -*-
"""
Editor de combinaciones de teclas, accesible y en español.

Muestra las acciones agrupadas por sección (Generales / Publicaciones / RSS), permite cambiar la
tecla de cada una (capturando la combinación), restaurar una/una sección/todo, y avisa de conflictos
y de teclas reservadas. Los cambios se guardan al instante (entre sesiones) y se aplican EN CALIENTE
en la ventana principal (tabla de aceleradores, menús y menús contextuales) llamando a
frame._reaplicar_atajos().
"""

import wx

from .shortcut_manager import shortcut_manager
from .accessible_controls import AccessibleListCtrl, mostrar_info, confirmar
from app.core.speech import speak


# Teclas especiales aceptadas al capturar (keycode wx -> nombre interno).
_KEY_ESPECIALES = {
    wx.WXK_TAB: "Tab", wx.WXK_SPACE: "Space", wx.WXK_RETURN: "Enter",
    wx.WXK_F1: "F1", wx.WXK_F2: "F2", wx.WXK_F3: "F3", wx.WXK_F4: "F4",
    wx.WXK_F5: "F5", wx.WXK_F6: "F6", wx.WXK_F7: "F7", wx.WXK_F8: "F8",
    wx.WXK_F9: "F9", wx.WXK_F10: "F10", wx.WXK_F11: "F11", wx.WXK_F12: "F12",
}
_SOLO_MODIFICADOR = {wx.WXK_CONTROL, wx.WXK_SHIFT, wx.WXK_ALT, wx.WXK_RAW_CONTROL}


def evento_a_combo(event) -> str:
    """Convierte un evento de teclado en una cadena de combinación ('Ctrl+Shift+J') o '' si la
    pulsación no es una combinación válida (solo un modificador, o tecla no soportada)."""
    kc = event.GetKeyCode()
    if kc in _SOLO_MODIFICADOR:
        return ""
    partes = []
    if event.ControlDown():
        partes.append("Ctrl")
    if event.ShiftDown():
        partes.append("Shift")
    if event.AltDown():
        partes.append("Alt")
    if kc in _KEY_ESPECIALES:
        tecla = _KEY_ESPECIALES[kc]
    elif 33 <= kc <= 126:
        tecla = chr(kc).upper()
    else:
        return ""
    partes.append(tecla)
    return "+".join(partes)


class _CapturaTeclaDialog(wx.Dialog):
    """Mini-diálogo que espera a que el usuario pulse una combinación y la devuelve en self.combo."""
    def __init__(self, parent, accion_etiqueta):
        super().__init__(parent, title=_("Pulsa la nueva combinación"))
        self.combo = ""
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        txt = wx.StaticText(panel, label=_(
            "Pulsa la combinación de teclas para «{0}».\n\n"
            "Usa Ctrl, Alt y/o Mayús con una tecla. Pulsa Escape para cancelar.").format(accion_etiqueta))
        txt.Wrap(380)
        sizer.Add(txt, 0, wx.ALL, 12)
        panel.SetSizer(sizer)
        self.Fit()
        # Capturamos a nivel de diálogo para no depender del foco de un control concreto.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        panel.SetFocus()

    def _on_key(self, event):
        kc = event.GetKeyCode()
        if kc == wx.WXK_ESCAPE:
            self.combo = ""
            self.EndModal(wx.ID_CANCEL)
            return
        combo = evento_a_combo(event)
        if combo:
            self.combo = combo
            self.EndModal(wx.ID_OK)
            return
        # Solo se pulsó un modificador: seguir esperando.
        event.Skip(False)


class ShortcutsEditorDialog(wx.Dialog):
    """Diálogo principal del editor de atajos."""
    def __init__(self, parent, frame=None):
        super().__init__(parent, title=_("Personalizar combinaciones de teclas"),
                         size=(660, 540), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._frame = frame or wx.GetApp().GetTopWindow()
        self._acciones_visibles = []   # accion_id por fila de la lista (orden actual)

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(panel, label=_(
            "Elige una sección, selecciona una acción y pulsa «Cambiar tecla». Los cambios se guardan "
            "y se aplican al instante, también en los menús. Las teclas comunes valen para Mastodon, "
            "Bluesky y X."))
        intro.Wrap(620)
        root.Add(intro, 0, wx.ALL, 10)

        # Selector de sección
        fila_sec = wx.BoxSizer(wx.HORIZONTAL)
        fila_sec.Add(wx.StaticText(panel, label=_("&Sección:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_seccion = wx.Choice(panel, choices=shortcut_manager.secciones(),
                                        name=_("Sección de atajos"))
        self.choice_seccion.SetSelection(0)
        self.choice_seccion.Bind(wx.EVT_CHOICE, lambda e: self._recargar_lista())
        fila_sec.Add(self.choice_seccion, 1)
        root.Add(fila_sec, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Lista de acciones de la sección
        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
                                        name=_("Acciones y sus teclas"))
        self.lista.InsertColumn(0, _("Acción"), width=380)
        self.lista.InsertColumn(1, _("Tecla"), width=200)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._on_cambiar())
        root.Add(self.lista, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # Botones
        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_cambiar = wx.Button(panel, label=_("&Cambiar tecla..."))
        self.btn_cambiar.Bind(wx.EVT_BUTTON, lambda e: self._on_cambiar())
        botones.Add(self.btn_cambiar, 0, wx.ALL, 4)
        self.btn_rest_una = wx.Button(panel, label=_("Restaurar es&ta"))
        self.btn_rest_una.Bind(wx.EVT_BUTTON, lambda e: self._on_restaurar_una())
        botones.Add(self.btn_rest_una, 0, wx.ALL, 4)
        self.btn_rest_sec = wx.Button(panel, label=_("Restaurar la se&cción"))
        self.btn_rest_sec.Bind(wx.EVT_BUTTON, lambda e: self._on_restaurar_seccion())
        botones.Add(self.btn_rest_sec, 0, wx.ALL, 4)
        self.btn_rest_todo = wx.Button(panel, label=_("Restaurar &todo"))
        self.btn_rest_todo.Bind(wx.EVT_BUTTON, lambda e: self._on_restaurar_todo())
        botones.Add(self.btn_rest_todo, 0, wx.ALL, 4)
        self.btn_cerrar = wx.Button(panel, wx.ID_CLOSE, _("Cerra&r"))
        self.btn_cerrar.Bind(wx.EVT_BUTTON, lambda e: self._cerrar())
        botones.Add(self.btn_cerrar, 0, wx.ALL, 4)
        root.Add(botones, 0, wx.ALIGN_CENTER | wx.ALL, 6)

        panel.SetSizer(root)
        # Escape cierra. El diálogo se abre con ShowModal, así que para cerrar hay que terminar el
        # bucle modal con EndModal (self.Close() no lo termina y "no pasaba nada").
        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self._recargar_lista()
        self.lista.SetFocus()

    def _cerrar(self):
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()

    def _on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._cerrar()
            return
        event.Skip()

    # --- Datos ---
    def _seccion_actual(self):
        return self.choice_seccion.GetStringSelection()

    def _recargar_lista(self):
        self.lista.DeleteAllItems()
        self._acciones_visibles = shortcut_manager.acciones_de(self._seccion_actual())
        for i, accion in enumerate(self._acciones_visibles):
            etiqueta = shortcut_manager.etiqueta(accion)
            combo = shortcut_manager.combo_legible(accion) or _("Sin asignar")
            if shortcut_manager.es_personalizado(accion):
                combo = _("{0} (personalizada)").format(combo)
            idx = self.lista.InsertItem(i, etiqueta)
            self.lista.SetItem(idx, 1, combo)
        if self.lista.GetItemCount() > 0:
            self.lista.Select(0)
            self.lista.Focus(0)

    def _accion_seleccionada(self):
        idx = self.lista.GetFirstSelected()
        if idx < 0 or idx >= len(self._acciones_visibles):
            speak(_("Selecciona primero una acción de la lista."), interrupt=True)
            return None, -1
        return self._acciones_visibles[idx], idx

    def _aplicar_en_caliente(self):
        if self._frame is not None and hasattr(self._frame, "_reaplicar_atajos"):
            try:
                self._frame._reaplicar_atajos()
            except Exception:
                pass

    # --- Acciones ---
    def _on_cambiar(self):
        accion, idx = self._accion_seleccionada()
        if accion is None:
            return
        etiqueta = shortcut_manager.etiqueta(accion)
        dlg = _CapturaTeclaDialog(self, etiqueta)
        res = dlg.ShowModal()
        combo = dlg.combo
        dlg.Destroy()
        if res != wx.ID_OK or not combo:
            return
        # Validaciones: reservada o en conflicto.
        if shortcut_manager.es_reservada(combo):
            mostrar_info(self, _(
                "La combinación {0} está reservada por el sistema (navegación o reproductores) y no "
                "se puede asignar.").format(shortcut_manager.combo_a_legible(combo)),
                _("Tecla reservada"))
            return
        # Los reproductores se manejan desde cualquier ventana, así que su tecla no puede ser algo
        # que se escriba (se comería la pulsación en los campos de texto).
        if (shortcut_manager.es_accion_reproductor(accion)
                and not shortcut_manager.combo_valido_reproductor(combo)):
            mostrar_info(self, _(
                "Las teclas de los reproductores funcionan en toda la aplicación, incluso con un "
                "diálogo abierto, así que no pueden ser una tecla que se escriba. Usa una tecla de "
                "función (de F1 a F12) o una combinación con Ctrl, Alt o Mayús."),
                _("Combinación no válida para el reproductor"))
            return
        choca = shortcut_manager.conflicto(accion, combo)
        if choca:
            mostrar_info(self, _(
                "La combinación {0} ya está asignada a «{1}». Elige otra o cámbiala primero.").format(
                shortcut_manager.combo_a_legible(combo), shortcut_manager.etiqueta(choca)),
                _("Combinación en uso"))
            return
        if shortcut_manager.establecer_atajo(accion, combo):
            self._recargar_lista()
            self.lista.Select(idx)
            self.lista.Focus(idx)
            self._aplicar_en_caliente()
            speak(_("Asignada {0} a {1}.").format(
                shortcut_manager.combo_a_legible(combo), shortcut_manager.etiqueta(accion)), interrupt=True)

    def _on_restaurar_una(self):
        accion, idx = self._accion_seleccionada()
        if accion is None:
            return
        shortcut_manager.restaurar(accion)
        self._recargar_lista()
        self.lista.Select(idx)
        self.lista.Focus(idx)
        self._aplicar_en_caliente()
        speak(_("Restaurada la tecla por defecto de {0}.").format(
            shortcut_manager.etiqueta(accion)), interrupt=True)

    def _on_restaurar_seccion(self):
        sec = self._seccion_actual()
        if confirmar(self, _("¿Restaurar las teclas por defecto de toda la sección «{0}»?").format(sec),
                     _("Restaurar la sección")) != wx.ID_YES:
            return
        shortcut_manager.restaurar_seccion(sec)
        self._recargar_lista()
        self._aplicar_en_caliente()
        speak(_("Sección restaurada."), interrupt=True)

    def _on_restaurar_todo(self):
        if confirmar(self, _("¿Restaurar TODAS las combinaciones de teclas a sus valores por defecto?"),
                     _("Restaurar todo")) != wx.ID_YES:
            return
        shortcut_manager.restaurar_todo()
        self._recargar_lista()
        self._aplicar_en_caliente()
        speak(_("Todas las teclas se han restaurado a sus valores por defecto."), interrupt=True)
