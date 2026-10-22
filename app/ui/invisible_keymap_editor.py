# -*- coding: utf-8 -*-
"""Editor de las teclas de la INTERFAZ INVISIBLE. Es un editor SEPARADO del de atajos de ventana
(shortcuts_editor.py): otro catálogo y otra persistencia. Accesible, en español, con Aceptar/Cerrar.

Al asignar una tecla comprueba CUATRO niveles de seguridad para que no choque con nada:
  1. Debe llevar Windows (o Ctrl+Alt): la separa del tecleo y de los atajos de ventana.
  2. No puede ser una combinación reservada de Windows ni de los lectores de pantalla.
  3. No puede coincidir con otra acción invisible ni con un atajo de ventana.
  4. Registro de PRUEBA real: si otra aplicación del equipo ya usa esa tecla, se rechaza.
"""
import wx

from app.core.speech import speak, notificar
from app.ui.accessible_controls import AccessibleListCtrl, confirmar
from app.ui.invisible_keymap import (invisible_keymap, canonica, combo_legible,
                                     validar_tecla_base, es_combinacion_permitida, _TECLAS_VK)

try:
    _
except NameError:
    _ = lambda x: x


class InvisibleKeymapEditor(wx.Dialog):
    def __init__(self, parent, frame=None):
        super().__init__(parent, title=_("Teclas de la interfaz invisible"), size=(680, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Editor de teclas invisibles"))
        # 'frame' es la VENTANA PRINCIPAL (la que registra las teclas globales y despacha las
        # acciones). Puede diferir del parent wx cuando el editor se abre desde Preferencias.
        self.frame = frame or parent
        self.km = invisible_keymap
        self._acciones = []  # ids de acción en el orden de la lista visible
        self._init_ui()
        self._refrescar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Teclas globales de la interfaz invisible. Elige una acción "
                                          "y pulsa Establecer para cambiar su combinación:")), 0, wx.ALL, 5)
        # Selector de MAPA BASE (Windows 10 / Windows 11). Windows 11 reserva las combinaciones
        # Windows+Alt (Game Bar, silenciar micrófono…), así que su mapa lleva la navegación y las
        # acciones a Ctrl+Alt+Windows y deja la aplicación en Ctrl+Win (como TWBlue y FastSM).
        from app.ui.invisible_keymap import PERFILES, PERFILES_LEGIBLES
        self._perfiles = list(PERFILES)
        fila0 = wx.BoxSizer(wx.HORIZONTAL)
        fila0.Add(wx.StaticText(self, label=_("&Mapa base (según tu Windows):")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice_perfil = wx.Choice(self, choices=[PERFILES_LEGIBLES[p] for p in self._perfiles],
                                       name=_("Mapa base según tu versión de Windows"))
        try:
            self.choice_perfil.SetSelection(self._perfiles.index(self.km.perfil()))
        except Exception:
            self.choice_perfil.SetSelection(0)
        self.choice_perfil.Bind(wx.EVT_CHOICE, self._on_cambiar_perfil)
        fila0.Add(self.choice_perfil, 0, wx.ALL, 5)
        s.Add(fila0, 0, wx.EXPAND)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Sección:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice_seccion = wx.Choice(self, choices=self.km.secciones(), name=_("Sección"))
        self.choice_seccion.SetSelection(0)
        self.choice_seccion.Bind(wx.EVT_CHOICE, lambda e: self._refrescar())
        fila.Add(self.choice_seccion, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)

        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de acciones invisibles"))
        self.lista.InsertColumn(0, _("Acción"), width=380)
        self.lista.InsertColumn(1, _("Tecla"), width=250)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_set = wx.Button(self, label=_("&Establecer tecla..."))
        b_set.Bind(wx.EVT_BUTTON, self._on_establecer)
        btns.Add(b_set, 0, wx.ALL, 4)
        b_quitar = wx.Button(self, label=_("&Quitar tecla"))
        b_quitar.Bind(wx.EVT_BUTTON, self._on_quitar)
        btns.Add(b_quitar, 0, wx.ALL, 4)
        b_def = wx.Button(self, label=_("Tecla de &fábrica"))
        b_def.Bind(wx.EVT_BUTTON, self._on_restaurar_una)
        btns.Add(b_def, 0, wx.ALL, 4)
        b_probar = wx.Button(self, label=_("&Probar acción"))
        b_probar.Bind(wx.EVT_BUTTON, self._on_probar)
        btns.Add(b_probar, 0, wx.ALL, 4)
        b_todas = wx.Button(self, label=_("Restaurar &todas"))
        b_todas.Bind(wx.EVT_BUTTON, self._on_restaurar_todas)
        btns.Add(b_todas, 0, wx.ALL, 4)
        s.Add(btns, 0, wx.EXPAND)

        cierre = wx.BoxSizer(wx.HORIZONTAL)
        cierre.AddStretchSpacer()
        b_guardar = wx.Button(self, wx.ID_OK, _("&Guardar y cerrar"))
        b_guardar.Bind(wx.EVT_BUTTON, self._on_guardar)
        cierre.Add(b_guardar, 0, wx.ALL, 5)
        cierre.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(cierre, 0, wx.EXPAND)
        self.SetSizer(s)

    def _refrescar(self):
        seccion = self.choice_seccion.GetStringSelection() or self.km.secciones()[0]
        self._acciones = self.km.acciones_de(seccion)
        self.lista.DeleteAllItems()
        for i, a in enumerate(self._acciones):
            self.lista.InsertItem(i, self.km.etiqueta(a))
            tecla = self.km.tecla(a)
            self.lista.SetItem(i, 1, combo_legible(tecla) if tecla else _("(sin tecla)"))
        if self._acciones:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _accion_sel(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self._acciones):
            speak(_("Selecciona una acción."), interrupt=True)
            return None
        return self._acciones[idx]

    def _on_establecer(self, event):
        accion = self._accion_sel()
        if not accion:
            return
        dlg = _CapturaTeclaDialog(self, self.km.tecla(accion))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        tecla = dlg.get_tecla()
        dlg.Destroy()
        if not tecla:
            return
        # NIVELES 1 y 2: combinación permitida (Win/Ctrl+Alt) y no reservada.
        ok, motivo = es_combinacion_permitida(tecla)
        if not ok:
            notificar(motivo)
            return
        # NIVEL 3a: conflicto con otra acción invisible.
        otra = self.km.conflicto_interno(tecla, excepto=accion)
        if otra:
            if confirmar(self, _("«{0}» ya está asignada a «{1}». ¿Quitársela y dársela a esta "
                                 "acción?").format(combo_legible(tecla), self.km.etiqueta(otra)),
                         _("Tecla en uso")) != wx.ID_YES:
                return
            self.km.set_tecla(otra, "")  # liberar la otra
        # NIVEL 3b: conflicto con un atajo de ventana.
        app_eti = self.km.conflicto_con_app(tecla)
        if app_eti:
            notificar(_("Esa combinación ya la usa el atajo de la aplicación «{0}». Elige otra.").format(app_eti))
            return
        # NIVEL 4: registro de prueba real contra Windows (¿la tiene otra aplicación?).
        if not self.km.probar_registro(self.frame, tecla):
            notificar(_("Otra aplicación de tu equipo ya usa esa tecla, o Windows no la permite. "
                        "Elige otra combinación."))
            return
        self.km.set_tecla(accion, tecla)
        self._refrescar()
        speak(_("Asignada {0}.").format(combo_legible(tecla)), interrupt=True)

    def _on_quitar(self, event):
        accion = self._accion_sel()
        if not accion:
            return
        self.km.set_tecla(accion, "")
        self._refrescar()
        speak(_("Tecla quitada."), interrupt=True)

    def _on_restaurar_una(self, event):
        accion = self._accion_sel()
        if not accion:
            return
        self.km.restaurar(accion)
        self._refrescar()
        speak(_("Restaurada la tecla de fábrica."), interrupt=True)

    def _on_restaurar_todas(self, event):
        if confirmar(self, _("¿Restaurar TODAS las teclas invisibles a las de fábrica?"),
                     _("Restaurar teclas")) != wx.ID_YES:
            return
        self.km.restaurar_todas()
        self._refrescar()
        speak(_("Todas las teclas restauradas."), interrupt=True)

    def _on_probar(self, event):
        """Ejecuta la acción seleccionada ahora mismo (como en TWBlue)."""
        accion = self._accion_sel()
        if not accion:
            return
        try:
            self.frame._inv_dispatch(accion)
        except Exception:
            speak(_("No se pudo ejecutar la acción."), interrupt=True)

    def _on_cambiar_perfil(self, event):
        from app.ui.invisible_keymap import PERFILES_LEGIBLES
        i = self.choice_perfil.GetSelection()
        if not (0 <= i < len(self._perfiles)):
            return
        perfil = self._perfiles[i]
        if perfil == self.km.perfil():
            return
        if not confirmar(self, _("Vas a cambiar el mapa base a «{0}». Se pondrán sus teclas de fábrica "
                                 "y se PERDERÁN los cambios que hayas hecho a mano (eran del otro "
                                 "mapa).\n\n¿Continuar?").format(PERFILES_LEGIBLES[perfil]),
                         _("Cambiar el mapa de teclas")):
            # Revertir la selección visual del Choice.
            try:
                self.choice_perfil.SetSelection(self._perfiles.index(self.km.perfil()))
            except Exception:
                pass
            return
        self.km.set_perfil(perfil)
        try:
            self.frame._instalar_interfaz_invisible()  # re-registra las teclas globales en caliente
        except Exception:
            pass
        self._refrescar()
        notificar(_("Mapa de teclas cambiado a {0}.").format(PERFILES_LEGIBLES[perfil]))

    def _on_guardar(self, event):
        self.km.guardar()
        # Reaplicar en caliente si la interfaz invisible está activa.
        try:
            self.frame._instalar_interfaz_invisible()
        except Exception:
            pass
        notificar(_("Teclas de la interfaz invisible guardadas."))
        self.EndModal(wx.ID_OK)


class _CapturaTeclaDialog(wx.Dialog):
    """Captura una combinación con casillas de modificadores + una tecla base (patrón seguro)."""

    def __init__(self, parent, tecla_actual=""):
        super().__init__(parent, title=_("Establecer tecla"), size=(400, 340),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Establecer tecla"))
        self._init_ui(tecla_actual)
        self.CenterOnParent()

    def _init_ui(self, tecla_actual):
        from app.ui.invisible_keymap import _tokens
        mods, base = _tokens(tecla_actual)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Modificadores (recomendado incluir Windows):")), 0, wx.ALL, 5)
        self.cb_ctrl = wx.CheckBox(self, label=_("Control"), name="Control"); self.cb_ctrl.SetValue("ctrl" in mods)
        self.cb_alt = wx.CheckBox(self, label=_("Alt"), name="Alt"); self.cb_alt.SetValue("alt" in mods)
        self.cb_shift = wx.CheckBox(self, label=_("Mayús"), name="Mayús"); self.cb_shift.SetValue("shift" in mods)
        self.cb_win = wx.CheckBox(self, label=_("Windows"), name="Windows"); self.cb_win.SetValue("win" in mods)
        for cb in (self.cb_ctrl, self.cb_alt, self.cb_shift, self.cb_win):
            s.Add(cb, 0, wx.LEFT, 15)
        s.Add(wx.StaticText(self, label=_("Tecla (una letra, un número o un nombre como flecha "
                                          "arriba, inicio, intro, suprimir, f5):")), 0, wx.ALL, 5)
        self.txt_tecla = wx.TextCtrl(self, value=base, name=_("Tecla"))
        s.Add(self.txt_tecla, 0, wx.EXPAND | wx.ALL, 5)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, _("&Aceptar")); ok.SetDefault()
        bs.Add(ok, 0, wx.ALL, 5)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._tecla = ""

    def _on_ok(self, event):
        base = self.txt_tecla.GetValue().strip().lower()
        valida, motivo = validar_tecla_base(base)
        if not valida:
            notificar(motivo)
            return
        partes = []
        if self.cb_ctrl.GetValue(): partes.append("ctrl")
        if self.cb_alt.GetValue(): partes.append("alt")
        if self.cb_shift.GetValue(): partes.append("shift")
        if self.cb_win.GetValue(): partes.append("win")
        partes.append(base)
        self._tecla = "+".join(partes)
        self.EndModal(wx.ID_OK)

    def get_tecla(self):
        return self._tecla
