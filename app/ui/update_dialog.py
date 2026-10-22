# -*- coding: utf-8 -*-
"""
Diálogo de actualización de SocialRSS (descarga e instalación desde el servidor propio).

Muestra que hay una versión nueva, descarga el ZIP con barra de progreso (todo en segundo plano y
protegido contra fallos), lo extrae, prepara la actualización en caliente (con la app abierta) y
reinicia la aplicación con la versión nueva. La base de datos y la configuración del usuario NO se
tocan (no van en el paquete).
"""

import os
import logging
import threading

import wx

from app.core.speech import speak
from app.services.updater import update_service as upd
from .accessible_controls import mostrar_info

log = logging.getLogger(__name__)


class DialogoActualizacion(wx.Dialog):
    """Pregunta si instalar la versión nueva y, si se acepta, la descarga e instala con progreso."""

    def __init__(self, frame, version_remota):
        super().__init__(frame, title=_("Actualización disponible"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Actualización disponible"))
        self._frame = frame
        self._version = version_remota
        self._cancelado = False
        self._trabajando = False

        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_(
            "Hay una nueva versión disponible.\n\n"
            "Versión nueva: {nueva}\nVersión actual: {actual}\n\n"
            "¿Quieres descargarla e instalarla ahora? La aplicación se reiniciará al terminar.").format(
            nueva=version_remota, actual=upd.version_local()))
        s.Add(self.lbl, 0, wx.ALL | wx.EXPAND, 12)

        self.gauge = wx.Gauge(self, range=100, size=(-1, 22))
        self.gauge.Hide()
        s.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        self.lbl_prog = wx.StaticText(self, label="")
        self.lbl_prog.Hide()
        s.Add(self.lbl_prog, 0, wx.ALL, 12)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_instalar = wx.Button(self, label=_("&Instalar ahora"))
        self.btn_instalar.Bind(wx.EVT_BUTTON, self._on_instalar)
        self.btn_instalar.SetDefault()
        btns.Add(self.btn_instalar, 0, wx.ALL, 5)
        self.btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Ahora no"))
        self.btn_cerrar.Bind(wx.EVT_BUTTON, self._on_boton_cerrar)
        btns.Add(self.btn_cerrar, 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizerAndFit(s)
        self.CenterOnParent()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.btn_instalar.SetFocus()

    def _on_char_hook(self, event):
        # Escape cierra solo si no se está descargando/instalando.
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            if not self._trabajando:
                self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    def _on_boton_cerrar(self, event):
        # Mientras se trabaja, este botón es "Cancelar" (de la descarga); si no, cierra el diálogo.
        if self._trabajando:
            self._cancelado = True
            self.btn_cerrar.Disable()
            self.lbl.SetLabel(_("Cancelando..."))
        else:
            self.EndModal(wx.ID_CANCEL)

    def _on_close(self, event):
        # No permitir cerrar mientras se descarga/instala (evita dejar la actualización a medias).
        if self._trabajando:
            return
        self.EndModal(wx.ID_CANCEL)

    # --- Avance en la interfaz (siempre vía wx.CallAfter desde el hilo de trabajo) ---
    def _ui_estado(self, texto):
        if self:
            self.lbl.SetLabel(texto)
            self.Layout()

    def _ui_progreso(self, valor, texto):
        if not self:
            return
        if not self.gauge.IsShown():
            self.gauge.Show()
            self.lbl_prog.Show()
            self.Layout()
        if valor is None:
            self.gauge.Pulse()
        else:
            self.gauge.SetValue(max(0, min(100, int(valor))))
        if texto:
            self.lbl_prog.SetLabel(texto)

    @staticmethod
    def _fmt(n):
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / (1024 * 1024):.1f} MB"

    def _on_instalar(self, event):
        # Solo se puede instalar sobre la versión compilada; en código fuente no hay paquete.
        if not upd.actualizacion_disponible_para_instalar():
            mostrar_info(self, _(
                "La actualización automática solo está disponible en la versión instalada de la "
                "aplicación.\n\nDescarga la nueva versión manualmente desde el sitio habitual."),
                _("Solo en la versión instalada"))
            return
        self._trabajando = True
        self.btn_instalar.Disable()
        self.btn_cerrar.SetLabel(_("&Cancelar"))
        speak(_("Descargando la actualización..."), interrupt=True)
        threading.Thread(target=self._trabajo, daemon=True).start()

    def _on_cancelar_durante(self):
        self._cancelado = True

    def _trabajo(self):
        """Descarga + extrae + prepara el reinicio. Todo protegido: cualquier fallo se informa y se
        limpian los temporales, sin dejar la app en mal estado."""
        ruta_base = upd.ruta_base_instalacion()
        ruta_zip = os.path.join(ruta_base, upd.NOMBRE_ZIP_DESCARGA)
        ruta_temp = os.path.join(ruta_base, upd.NOMBRE_CARPETA_TEMP)
        try:
            # 1. Descargar con progreso.
            def _prog_descarga(leido, total):
                if total > 0:
                    pct = int(leido / total * 100)
                    wx.CallAfter(self._ui_progreso, pct, _("Descargando: {0} de {1} ({2}%)").format(
                        self._fmt(leido), self._fmt(total), pct))
                else:
                    wx.CallAfter(self._ui_progreso, None, _("Descargando: {0}").format(self._fmt(leido)))

            wx.CallAfter(self._ui_estado, _("Descargando la versión {0}...").format(self._version))
            upd.descargar_zip(self._version, ruta_zip, progreso=_prog_descarga,
                              cancelado=lambda: self._cancelado)

            # 1 bis. Verificar la integridad del paquete con el sha256 publicado en version.json
            # (lo genera build.bat junto al zip). Si no coincide, se aborta sin instalar nada.
            wx.CallAfter(self._ui_estado, _("Verificando el paquete descargado..."))
            info = upd.obtener_info_remota() or {}
            upd.verificar_manifiesto(info)
            upd.verificar_zip(ruta_zip, info.get("sha256", ""))

            # 2. Extraer con progreso.
            wx.CallAfter(self._ui_estado, _("Extrayendo los archivos..."))

            def _prog_extra(hechos, total):
                pct = int(hechos / total * 100) if total else 0
                wx.CallAfter(self._ui_progreso, pct, _("Extrayendo: {0} de {1}").format(hechos, total))

            upd.extraer_zip(ruta_zip, ruta_temp, progreso=_prog_extra)

            # 3. Preparar el script de actualización en caliente.
            wx.CallAfter(self._ui_estado, _("Preparando el reinicio..."))
            script = upd.crear_script_actualizacion(ruta_base, ruta_temp, ruta_zip)

            # 4. Confirmar y reiniciar (en el hilo de la interfaz).
            wx.CallAfter(self._confirmar_reinicio, script)

        except Exception as e:
            log.error(f"Error al instalar la actualización: {e}")
            # Limpiar lo que se haya quedado a medias.
            try:
                if os.path.exists(ruta_temp):
                    import shutil
                    shutil.rmtree(ruta_temp, ignore_errors=True)
                if os.path.exists(ruta_zip):
                    os.remove(ruta_zip)
            except Exception:
                pass
            wx.CallAfter(self._fallo, str(e))

    def _fallo(self, err):
        self._trabajando = False
        if not self:
            return
        if self._cancelado:
            self._ui_estado(_("Actualización cancelada."))
        else:
            self._ui_estado(_("No se pudo completar la actualización."))
            mostrar_info(self, _("No se pudo completar la actualización:\n\n{0}").format(err),
                         _("Error de actualización"))
        self.btn_instalar.Enable()
        self.btn_cerrar.Enable()
        self.btn_cerrar.SetLabel(_("&Cerrar"))
        self._cancelado = False

    def _confirmar_reinicio(self, script_path):
        if not self:
            return
        self._ui_progreso(100, _("Listo para instalar."))
        mostrar_info(self, _(
            "La actualización se ha descargado correctamente.\n\n"
            "La aplicación se cerrará y se reiniciará automáticamente con la versión nueva.\n\n"
            "Pulsa Aceptar para continuar."), _("Actualización lista"))
        # Cerrar este diálogo y entregar el control al frame, que guarda lo esencial, lanza el
        # script (que espera a que cerremos, copia y relanza) y sale de inmediato.
        try:
            self.EndModal(wx.ID_OK)
        except Exception:
            pass
        self._frame._lanzar_y_salir_actualizacion(script_path)
