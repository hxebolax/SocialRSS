# -*- coding: utf-8 -*-
"""
Diálogo para migrar SocialRSS a otro ordenador: exportar e importar TODO (base de
datos, configuración, credenciales y sesiones de Telegram) en un archivo protegido por
contraseña. Es independiente de la copia de seguridad: es la forma de llevar la
instalación a otro equipo y que quede funcionando igual, sin empezar de cero.
"""

import wx
import logging
from datetime import datetime

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import mostrar_info, confirmar, CustomTextEntryDialog

log = logging.getLogger(__name__)

_WILDCARD = "Migración de SocialRSS (*.srmig)|*.srmig|Todos los archivos (*.*)|*.*"


class MigracionDialog(wx.Dialog):
    """Exportar/importar toda la instalación entre ordenadores, con contraseña."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Migrar a otro ordenador"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Migrar a otro ordenador"))
        self._parent_frame = parent
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_(
            "Lleva toda tu instalación a otro ordenador: cuentas, inicios de sesión,\n"
            "configuración y base de datos. El archivo se protege con una contraseña;\n"
            "solo quien la conozca podrá importarlo. Es independiente de las copias de\n"
            "seguridad: esta es la forma de que en el otro equipo quede todo igual."
        )), 0, wx.ALL, 10)

        self.btn_exportar = wx.Button(self, label=_("&Exportar a un archivo..."), name=_("Exportar la instalación a un archivo"))
        self.btn_exportar.Bind(wx.EVT_BUTTON, self._on_exportar)
        sizer.Add(self.btn_exportar, 0, wx.EXPAND | wx.ALL, 8)

        self.btn_importar = wx.Button(self, label=_("&Importar desde un archivo..."), name=_("Importar una instalación desde un archivo"))
        self.btn_importar.Bind(wx.EVT_BUTTON, self._on_importar)
        sizer.Add(self.btn_importar, 0, wx.EXPAND | wx.ALL, 8)

        self.btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"), name=_("Cerrar"))
        sizer.Add(self.btn_cerrar, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizerAndFit(sizer)
        self.CenterOnParent()
        self.btn_exportar.SetFocus()

    def _pedir_password(self, mensaje: str) -> str:
        """Pide una contraseña en un diálogo accesible y en español (campo oculto).
        Devuelve None si se cancela."""
        dlg = CustomTextEntryDialog(self, mensaje, _("Contraseña"), password=True)
        valor = dlg.GetValue() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        return valor

    # --- EXPORTAR ---
    def _on_exportar(self, event):
        pwd = self._pedir_password(_("Escriba una contraseña para proteger la exportación (mínimo 10 caracteres):"))
        if pwd is None:
            return
        if len(pwd) < 10:
            mostrar_info(self, _("La contraseña debe tener al menos 10 caracteres. La exportación "
                                 "contiene tus credenciales descifradas: una contraseña corta se "
                                 "puede romper por fuerza bruta."), _("Migración"))
            return
        pwd2 = self._pedir_password(_("Repita la misma contraseña para confirmar:"))
        if pwd2 is None:
            return
        if pwd != pwd2:
            mostrar_info(self, _("Las contraseñas no coinciden. Inténtelo de nuevo."), _("Migración"))
            return

        nombre = "socialrss_migracion_{0}.srmig".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
        fdlg = wx.FileDialog(self, _("Guardar exportación como"), wildcard=_WILDCARD,
                             defaultFile=nombre, style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if fdlg.ShowModal() != wx.ID_OK:
            fdlg.Destroy()
            return
        ruta = fdlg.GetPath()
        fdlg.Destroy()

        self.btn_exportar.Disable()
        speak(_("Exportando. Espere, puede tardar según el tamaño de la base de datos..."), interrupt=True)

        def _bg(ctx):
            from app.core import migration
            return migration.exportar_migracion(ruta, pwd)

        def _finish(res, err):
            self.btn_exportar.Enable()
            if err:
                notificar(_("Error al exportar: {0}").format(err))
                mostrar_info(self, _("No se pudo exportar: {0}").format(err), _("Migración"))
            else:
                notificar(_("Exportación creada correctamente."))
                mostrar_info(self, _(
                    "Exportación creada en:\n{0}\n\nLlévala al otro ordenador (en una memoria USB, "
                    "la nube, etc.), instala SocialRSS allí y usa Importar con la misma contraseña."
                ).format(res), _("Migración completada"))

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    # --- IMPORTAR ---
    def _on_importar(self, event):
        fdlg = wx.FileDialog(self, _("Elegir archivo de migración"), wildcard=_WILDCARD,
                             style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if fdlg.ShowModal() != wx.ID_OK:
            fdlg.Destroy()
            return
        ruta = fdlg.GetPath()
        fdlg.Destroy()

        pwd = self._pedir_password(_("Escriba la contraseña con la que se creó la exportación:"))
        if pwd is None:
            return

        # Validar contraseña y leer un resumen antes de tocar nada.
        from app.core import migration
        try:
            info = migration.info_paquete(ruta, pwd)
        except Exception as e:
            notificar(_("No se pudo abrir el archivo: {0}").format(e))
            mostrar_info(self, _("No se pudo abrir el archivo: {0}").format(e), _("Migración"))
            return

        resumen = _(
            "Archivo válido.\n\nFecha de exportación: {0}\nCuentas con credenciales: {1}\n"
            "Incluye base de datos: {2}\nSesiones de Telegram: {3}\n\n"
            "ATENCIÓN MUY IMPORTANTE: al importar se BORRARÁ y SUSTITUIRÁ POR COMPLETO todo lo "
            "que tengas en ESTE ordenador. Si ya habías añadido cuentas, configurado opciones, "
            "leído artículos o cualquier otra cosa, se perderá y quedará exactamente lo que hay "
            "en el archivo. Esto NO se puede deshacer. Si quieres conservar lo actual, cancela y "
            "haz antes una copia de seguridad.\n\n¿Seguro que deseas continuar e importar?"
        ).format(
            info.get("fecha", "?"), info.get("num_cuentas_aprox", 0),
            _("sí") if info.get("tiene_db") else _("no"), info.get("num_sesiones", 0)
        )
        if confirmar(self, resumen, _("Confirmar importación (se sustituye todo)")) != wx.ID_YES:
            return

        self.btn_importar.Disable()
        speak(_("Preparando la importación. Espere..."), interrupt=True)

        def _bg(ctx):
            return migration.importar_migracion(ruta, pwd)

        def _finish(res, err):
            self.btn_importar.Enable()
            if err:
                notificar(_("Error al importar: {0}").format(err))
                mostrar_info(self, _("No se pudo importar: {0}").format(err), _("Migración"))
                return
            # La sustitución real se hace al reiniciar (cuando la base de datos no está en uso).
            # Por eso ahora se REINICIA la aplicación con el mismo mecanismo fiable que usan las
            # plantillas y la actualización; al volver a arrancar, la importación queda aplicada.
            notificar(_("Importación preparada. La aplicación se reiniciará para aplicarla."))
            mostrar_info(self, _(
                "Todo listo. La aplicación se reiniciará ahora para aplicar la importación de forma "
                "segura, ya que la base de datos no se puede sustituir mientras está en uso. "
                "Cuando vuelva a abrirse, tendrá todo igual que en el otro ordenador."
            ), _("Migración: reinicio"))
            self._reiniciar_app()

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _reiniciar_app(self):
        """Reinicia la aplicación tras preparar la importación, con el MISMO mecanismo fiable que
        las plantillas y la actualización (`_reiniciar_aplicacion`: ayudante .bat desacoplado que
        espera a que muera este proceso, relanza la app y se autoelimina, seguido de os._exit). Así
        la importación pendiente se aplica al arrancar. Antes se usaba Destroy()/ExitMainLoop, que
        ni relanzaba ni cerraba de forma fiable (a veces no se reiniciaba)."""
        try:
            self.EndModal(wx.ID_OK)
        except Exception:
            pass
        frame = self._parent_frame
        if frame is not None and hasattr(frame, "_reiniciar_aplicacion"):
            frame._reiniciar_aplicacion()
        else:
            # Respaldo: si no hay frame con el mecanismo de reinicio, al menos cerrar.
            try:
                wx.GetApp().ExitMainLoop()
            except Exception:
                pass
