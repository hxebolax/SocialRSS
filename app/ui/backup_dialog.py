# -*- coding: utf-8 -*-
"""
Diálogo de copias de seguridad y restauración de SocialRSS.
"""

import wx
from app.core.speech import speak, notificar
from app.core import backup
from .accessible_controls import AccessibleListCtrl


class BackupDialog(wx.Dialog):
    """Crear, restaurar, exportar e importar copias de seguridad."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Copias de seguridad"), size=(620, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Diálogo de copias de seguridad"))
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(self, label=_(
            "Una copia incluye tus feeds, artículos, preferencias y credenciales cifradas.\n"
            "Las credenciales solo se podrán usar en este mismo equipo.")), 0, wx.ALL, 8)

        s.Add(wx.StaticText(self, label=_("Copias disponibles:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de copias de seguridad")
        )
        self.lista.InsertColumn(0, _("Archivo"), width=320)
        self.lista.InsertColumn(1, _("Fecha"), width=150)
        self.lista.InsertColumn(2, _("Tamaño (KB)"), width=100)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_crear = wx.Button(self, label=_("&Crear copia ahora"))
        self.btn_crear.Bind(wx.EVT_BUTTON, self._on_crear)
        btns.Add(self.btn_crear, 0, wx.ALL, 5)

        self.btn_restaurar = wx.Button(self, label=_("&Restaurar seleccionada"))
        self.btn_restaurar.Bind(wx.EVT_BUTTON, self._on_restaurar)
        btns.Add(self.btn_restaurar, 0, wx.ALL, 5)

        self.btn_exportar = wx.Button(self, label=_("&Exportar a archivo..."))
        self.btn_exportar.Bind(wx.EVT_BUTTON, self._on_exportar)
        btns.Add(self.btn_exportar, 0, wx.ALL, 5)

        self.btn_importar = wx.Button(self, label=_("&Importar desde archivo..."))
        self.btn_importar.Bind(wx.EVT_BUTTON, self._on_importar)
        btns.Add(self.btn_importar, 0, wx.ALL, 5)

        btns.Add(wx.Button(self, wx.ID_CLOSE, _("Ce&rrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))

    def _cargar(self):
        self.lista.DeleteAllItems()
        self.copias = backup.listar_backups()
        for i, c in enumerate(self.copias):
            pos = self.lista.InsertItem(i, c["nombre"])
            self.lista.SetItem(pos, 1, c["fecha"])
            self.lista.SetItem(pos, 2, str(c["tamano_kb"]))

    def _seleccionada(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.copias):
            return None
        return self.copias[idx]

    def _lanzar_bg(self, trabajo, exito, fallo):
        """Ejecuta la operación de copia FUERA del hilo de interfaz (una copia de la
        base de datos puede tardar) y desactiva los botones mientras tanto."""
        from app.core.task_manager import task_manager
        for b in (self.btn_crear, self.btn_restaurar, self.btn_exportar, self.btn_importar):
            b.Disable()

        def _bg(ctx):
            return trabajo()

        def _fin(res, err):
            for b in (self.btn_crear, self.btn_restaurar, self.btn_exportar, self.btn_importar):
                b.Enable()
            from app.core.task_manager import TareaDuplicadaError
            if isinstance(err, TareaDuplicadaError):
                notificar(_("Ya hay una operación de copias en curso; espera a que termine."))
                return
            if err:
                notificar(fallo.format(err))
            else:
                exito()
        task_manager.start_task(_bg, callback=_fin, pass_context=True,
                                unique_key="backup:operacion")

    def _on_crear(self, event):
        speak(_("Creando la copia de seguridad..."), interrupt=True)

        def _ok():
            self._cargar()
            notificar(_("Copia de seguridad creada."))
        self._lanzar_bg(lambda: (backup.crear_backup(), backup.rotar_backups(10)),
                        _ok, _("No se pudo crear la copia: {0}"))

    def _on_restaurar(self, event):
        c = self._seleccionada()
        if not c:
            speak(_("Seleccione una copia."), interrupt=True)
            return
        dlg = wx.MessageDialog(
            self,
            _("Tus datos actuales se reemplazarán con los de esta copia al REINICIAR la "
              "aplicación (se guarda una copia previa por seguridad).\n\n¿Continuar?"),
            _("Confirmar restauración"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
        )
        resp = dlg.ShowModal()
        dlg.Destroy()
        if resp != wx.ID_YES:
            return
        speak(_("Preparando la restauración..."), interrupt=True)

        def _ok():
            notificar(_("Restauración preparada. Cierra y vuelve a abrir SocialRSS para aplicarla."))
            from .accessible_controls import mostrar_info
            mostrar_info(self, _("Restauración preparada. Al cerrar y volver a abrir SocialRSS "
                                 "se aplicará la copia."), _("Restauración preparada"))
        self._lanzar_bg(lambda: backup.restaurar_backup(c["ruta"]),
                        _ok, _("No se pudo restaurar: {0}"))

    def _on_exportar(self, event):
        dlg = wx.FileDialog(self, message=_("Guardar copia de seguridad como"),
                            defaultFile="socialrss_backup.zip", wildcard="ZIP (*.zip)|*.zip",
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            speak(_("Exportando la copia..."), interrupt=True)
            self._lanzar_bg(lambda: backup.crear_backup(ruta),
                            lambda: notificar(_("Copia exportada.")),
                            _("No se pudo exportar: {0}"))
        dlg.Destroy()

    def _on_importar(self, event):
        dlg = wx.FileDialog(self, message=_("Seleccionar copia para importar/restaurar"),
                            wildcard="ZIP (*.zip)|*.zip",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            confirm = wx.MessageDialog(
                self, _("Tus datos actuales se reemplazarán con los del archivo al REINICIAR "
                        "la aplicación. ¿Continuar?"),
                _("Confirmar importación"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            r = confirm.ShowModal()
            confirm.Destroy()
            if r == wx.ID_YES:
                speak(_("Preparando la importación..."), interrupt=True)

                def _ok():
                    notificar(_("Importación preparada. Cierra y vuelve a abrir SocialRSS."))
                    from .accessible_controls import mostrar_info
                    mostrar_info(self, _("Importación preparada. Al cerrar y volver a abrir "
                                         "SocialRSS se aplicará la copia."), _("Importación preparada"))
                self._lanzar_bg(lambda: backup.restaurar_backup(ruta),
                                _ok, _("No se pudo importar: {0}"))
        dlg.Destroy()
