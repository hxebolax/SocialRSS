# -*- coding: utf-8 -*-
"""Mixin del MainFrame: handlers del submenú "Administración" del menú Matrix.

Solo se construye el submenú si la preferencia matrix.admin_tools está activa (Preferencias →
Matrix). Estos handlers abren los diálogos de administración del servidor (Synapse Admin API) o,
en el caso de comprobar/estadísticas, hacen una consulta en segundo plano y la anuncian.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager


class _Mf_matrix_admin:
    def _matrix_admin_comprobar(self):
        """Dice si tu cuenta administra el servidor (server admin de Synapse)."""
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        speak(_("Comprobando..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.es_admin_servidor(acc)

        def _fin(es_admin, err):
            if err:
                notificar(_("No se pudo comprobar: {0}").format(err))
                return
            if es_admin:
                notificar(_("Sí: tu cuenta es administradora del servidor. El submenú de "
                            "administración funcionará."))
            else:
                notificar(_("No: tu cuenta no es administradora del servidor (o el servidor no "
                            "es Synapse). Las acciones de administración serán rechazadas."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_admin_federacion(self):
        """Abre el diálogo de federación (servidores remotos y su estado de conexión)."""
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .matrix_admin_dialogs import MatrixAdminFederacionDialog
        dlg = MatrixAdminFederacionDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _matrix_admin_bg_updates(self):
        """Estado de las actualizaciones en segundo plano de la base de datos del servidor."""
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        speak(_("Consultando..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_actualizaciones_fondo(acc)

        def _fin(d, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            d = d or {}
            if d.get("en_curso"):
                notificar(_("El servidor está MIGRANDO la base de datos en segundo plano (tras una "
                            "actualización). Puede ir algo más lento hasta que termine."))
            else:
                notificar(_("La base de datos del servidor está al día (sin migraciones pendientes)."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_admin_media_mant(self):
        """Borra archivos locales no usados anteriores a X días, para liberar disco."""
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog, confirmar
        d = CustomTextEntryDialog(self, _("Borrar los archivos no usados desde hace más de cuántos "
                                          "DÍAS (por ejemplo 90; vacío para cancelar):"),
                                  _("Mantenimiento de archivos"), value="90")
        dias = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not dias:
            return
        try:
            dias_n = max(1, int(float(dias)))
        except ValueError:
            notificar(_("Escribe un número de días."))
            return
        if confirmar(self, _("Se borrarán del disco los archivos locales no vistos desde hace más de "
                             "{0} días. ¿Continuar?").format(dias_n), _("Confirmar")) != wx.ID_YES:
            return
        import time as _t
        antes_ms = int((_t.time() - dias_n * 86400) * 1000)
        speak(_("Borrando archivos antiguos..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_borrar_media_antiguos(acc, antes_ms)

        def _fin(n, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("{0} archivo(s) borrado(s).").format(n or 0))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_admin_aviso_servidor(self):
        """(Admin) Envía un aviso del servidor (mensaje directo desde la cuenta de avisos) a un
        usuario: pide el @usuario:servidor y el texto."""
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        d1 = CustomTextEntryDialog(self, _("Usuario destino (@usuario:servidor):"),
                                   _("Aviso del servidor"))
        uid = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
        d1.Destroy()
        if not uid:
            return
        d2 = CustomTextEntryDialog(self, _("Mensaje del aviso:"), _("Aviso del servidor"),
                                   multiline=True)
        mensaje = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if not mensaje:
            return
        speak(_("Enviando el aviso..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_enviar_aviso_servidor(acc, uid, mensaje)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar (¿tu cuenta no es admin o el servidor no tiene avisos "
                            "de servidor?): {0}").format(err))
            else:
                notificar(_("Aviso del servidor enviado a {0}.").format(uid))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_admin_estadisticas(self):
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        speak(_("Consultando el servidor..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.admin_estadisticas(acc)

        def _fin(datos, err):
            if err:
                notificar(_("No se pudo (¿tu cuenta no es administradora?): {0}").format(err))
                return
            datos = datos or {}
            def _num(v):
                return str(v) if v is not None else _("(no disponible)")
            lineas = [
                _("Versión de Synapse: {0}").format(datos.get("version") or _("(desconocida)")),
                _("Usuarios en el servidor: {0}").format(_num(datos.get("usuarios_total"))),
                _("Salas en el servidor: {0}").format(_num(datos.get("salas_total"))),
            ]
            self._mostrar_texto_largo(_("Estadísticas del servidor"), "\n".join(lineas))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_admin_usuarios(self):
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .matrix_admin_dialogs import MatrixAdminUsuariosDialog
        dlg = MatrixAdminUsuariosDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _matrix_admin_salas(self):
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .matrix_admin_dialogs import MatrixAdminSalasDialog
        dlg = MatrixAdminSalasDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _matrix_admin_informes(self):
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .matrix_admin_dialogs import MatrixAdminInformesDialog
        dlg = MatrixAdminInformesDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _matrix_admin_tokens(self):
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .matrix_admin_dialogs import MatrixAdminTokensDialog
        dlg = MatrixAdminTokensDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()
