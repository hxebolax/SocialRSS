# -*- coding: utf-8 -*-
"""Actualización automática en Linux (equivale a app/ui/update_dialog.py de Windows).

Pregunta si instalar la versión nueva y, si se acepta, descarga el paquete de Linux con barra de
progreso (en segundo plano), verifica la firma del manifiesto y el SHA-256, lo extrae y deja un script
que, al cerrarse la aplicación, copia la versión nueva, pone al día las dependencias del entorno .venv
y la vuelve a abrir. La base de datos y la configuración no se tocan (no van en el paquete)."""

import logging
import os
import shutil
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.plataforma import llamar_en_ui
from app.core.speech import speak
from app.services.updater import update_service as upd
from .dialogos import DialogoBase

log = logging.getLogger(__name__)


def _tam(n: int) -> str:
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _informar(padre, titulo: str, texto: str, al_cerrar=None):
    """Mensaje con un único botón Aceptar; `al_cerrar()` se llama al cerrarlo."""
    dlg = Gtk.AlertDialog()
    dlg.set_message(titulo)
    dlg.set_detail(texto)
    dlg.set_buttons([_("Aceptar")])
    dlg.set_default_button(0)
    dlg.set_cancel_button(0)

    def _fin(origen, res):
        try:
            origen.choose_finish(res)
        except GLib.Error:
            pass
        if al_cerrar is not None:
            al_cerrar()
    dlg.choose(padre, None, _fin)


class DialogoActualizacion(DialogoBase):
    def __init__(self, ventana, version_remota: str):
        super().__init__(ventana, _("Actualización disponible"), aceptar=_("_Instalar ahora"))
        self.ventana = ventana
        self.version = version_remota
        self._cancelado = False
        self._trabajando = False
        self._ultimo_aviso = -1
        texto = _("Hay una nueva versión disponible.\n\nVersión nueva: {nueva}\nVersión actual: {actual}\n\n"
                  "¿Quieres descargarla e instalarla ahora? La aplicación se reiniciará al terminar.").format(
            nueva=version_remota, actual=upd.version_local())
        self.mensaje = Gtk.Label(label=texto, xalign=0, wrap=True)
        self.rejilla.attach(self.mensaje, 0, 0, 2, 1)
        self.progreso = Gtk.ProgressBar(show_text=True, visible=False)
        self.progreso.update_property([Gtk.AccessibleProperty.LABEL], [_("Progreso de la actualización")])
        self.rejilla.attach(self.progreso, 0, 1, 2, 1)
        self._fila = 2
        self.boton_cancelar.set_label(_("_Ahora no"))
        self.boton_aceptar.update_property([Gtk.AccessibleProperty.DESCRIPTION], [texto])
        self.connect("close-request", self._al_cerrar)
        self.set_focus(self.boton_aceptar)

    # ------------------------------------------------------------------ interfaz
    def _al_cerrar(self, *_a):
        """Mientras se descarga, cerrar (Escape o el botón) CANCELA; no se deja nada a medias."""
        if self._trabajando:
            if not self._cancelado:
                self._cancelado = True
                self.boton_cancelar.set_sensitive(False)
                self._estado(_("Cancelando..."))
            return True
        return False

    def _estado(self, texto: str):
        self.mensaje.set_label(texto)
        speak(texto, interrupt=False)

    def _avance(self, fraccion, texto: str):
        self.progreso.set_visible(True)
        if fraccion is None:
            self.progreso.pulse()
        else:
            self.progreso.set_fraction(max(0.0, min(1.0, fraccion)))
            # Aviso hablado cada 25 % (la barra se actualiza sin parar; no se lee cada cambio).
            tramo = int(fraccion * 4)
            if tramo != self._ultimo_aviso and 0 < tramo < 4:
                self._ultimo_aviso = tramo
                speak(_("{0} %").format(tramo * 25), interrupt=False)
        self.progreso.set_text(texto)

    def aceptar(self):
        if self._trabajando:
            return
        if not upd.actualizacion_disponible_para_instalar():
            _informar(self, _("Instalación manual"), _(
                "Esta copia de la aplicación es un clon del repositorio o está en una carpeta sin permiso de "
                "escritura, así que no se actualiza sola. Ponla al día con git pull o descarga el paquete de "
                "Linux desde la página de descargas."))
            return
        self._trabajando = True
        self._cancelado = False
        self.boton_aceptar.set_sensitive(False)
        self.boton_cancelar.set_label(_("_Cancelar"))
        self._estado(_("Descargando la versión {0}...").format(self.version))
        threading.Thread(target=self._trabajo, name="actualizacion", daemon=True).start()

    # ------------------------------------------------------------------ trabajo en segundo plano
    def _trabajo(self):
        base = upd.ruta_base_instalacion()
        ruta_paquete = os.path.join(base, upd.NOMBRE_ZIP_DESCARGA)
        ruta_temp = os.path.join(base, upd.NOMBRE_CARPETA_TEMP)
        try:
            def _prog_descarga(leido, total):
                if total > 0:
                    llamar_en_ui(self._avance, leido / total, _("Descargando: {0} de {1}").format(_tam(leido), _tam(total)))
                else:
                    llamar_en_ui(self._avance, None, _("Descargando: {0}").format(_tam(leido)))

            upd.descargar_zip(self.version, ruta_paquete, progreso=_prog_descarga,
                              cancelado=lambda: self._cancelado)
            llamar_en_ui(self._estado, _("Verificando el paquete descargado..."))
            upd.verificar_descarga(upd.obtener_info_remota() or {}, ruta_paquete)
            llamar_en_ui(self._estado, _("Extrayendo los archivos..."))

            def _prog_extra(hechos, total):
                llamar_en_ui(self._avance, hechos / total if total else 0,
                             _("Extrayendo: {0} de {1}").format(hechos, total))
            upd.extraer_paquete(ruta_paquete, ruta_temp, progreso=_prog_extra)
            script = upd.crear_script_actualizacion(base, ruta_temp, ruta_paquete)
            llamar_en_ui(self._listo, script)
        except Exception as e:
            log.error(f"Error al instalar la actualización: {e}")
            shutil.rmtree(ruta_temp, ignore_errors=True)
            try:
                if os.path.exists(ruta_paquete):
                    os.remove(ruta_paquete)
            except OSError:
                pass
            llamar_en_ui(self._fallo, str(e))

    def _fallo(self, err: str):
        self._trabajando = False
        self.boton_aceptar.set_sensitive(True)
        self.boton_cancelar.set_sensitive(True)
        self.boton_cancelar.set_label(_("_Cerrar"))
        self.progreso.set_visible(False)
        if self._cancelado:
            self._cancelado = False
            self._estado(_("Actualización cancelada."))
            return
        self._estado(_("No se pudo completar la actualización."))
        _informar(self, _("Error de actualización"), _("No se pudo completar la actualización:\n\n{0}").format(err))

    def _listo(self, script: str):
        self._avance(1.0, _("Listo para instalar."))
        _informar(self, _("Actualización lista"), _(
            "La actualización se ha descargado correctamente.\n\n"
            "La aplicación se cerrará y se reiniciará automáticamente con la versión nueva.\n\n"
            "Pulsa Aceptar para continuar."), lambda: self._aplicar(script))

    def _aplicar(self, script: str):
        """Lanza el script (espera a que se cierre la app, copia y relanza) y cierra la aplicación."""
        try:
            config.save()
        except Exception:
            pass
        try:
            upd.lanzar_script_actualizacion(script)
        except Exception as e:
            log.error(f"No se pudo lanzar la actualización: {e}")
            self._trabajando = False
            _informar(self, _("Error de actualización"), _("No se pudo completar la actualización:\n\n{0}").format(e))
            return
        self._trabajando = False
        app = self.ventana.get_application()
        self.close()
        self.ventana.close()
        if app is not None:
            GLib.idle_add(lambda: app.quit() or GLib.SOURCE_REMOVE)
