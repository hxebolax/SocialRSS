# -*- coding: utf-8 -*-
"""Controlador de PeerTube: administración (mixin de UI, Fase 9).

Abre los diálogos de administración (usuarios, registros, federación, trabajos, plugins, tokens de
runners). Solo son útiles para administradores; el servidor rechaza las acciones si la cuenta no lo es.
La configuración completa de la instancia no se edita aquí (es demasiado extensa para una UI
accesible): se muestra un resumen y se recomienda la web de administración.
"""
import logging

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _AdminUIMixin:
    def es_administrador_actual(self):
        try:
            return peertube_service.es_administrador(self.f.active_peertube_account_id)
        except Exception:
            return False

    def _abrir_admin(self, clase):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        dlg = clase(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def admin_usuarios_dialogo(self):
        from .peertube_admin_dialogs import PeerTubeUsuariosDialog
        self._abrir_admin(PeerTubeUsuariosDialog)

    def admin_registros_dialogo(self):
        from .peertube_admin_dialogs import PeerTubeRegistrosDialog
        self._abrir_admin(PeerTubeRegistrosDialog)

    def admin_federacion_dialogo(self):
        from .peertube_admin_dialogs import PeerTubeFederacionDialog
        self._abrir_admin(PeerTubeFederacionDialog)

    def admin_jobs_dialogo(self):
        from .peertube_admin_dialogs import PeerTubeJobsDialog
        self._abrir_admin(PeerTubeJobsDialog)

    def admin_plugins_dialogo(self):
        from .peertube_admin_dialogs import PeerTubePluginsDialog
        self._abrir_admin(PeerTubePluginsDialog)

    def admin_runners_dialogo(self):
        from .peertube_admin_dialogs import PeerTubeRunnersDialog
        self._abrir_admin(PeerTubeRunnersDialog)

    def admin_config_resumen(self):
        """Muestra un resumen de la configuración de la instancia (solo lectura)."""
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        speak(_("Cargando la configuración..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.admin_config_custom(acc)

        def _fin(cfg, err):
            if err or not cfg:
                notificar(_("No se pudo cargar la configuración: {0}").format(err or ""))
                return
            from .accessible_controls import mostrar_info
            instancia = cfg.get("instance", {}) if isinstance(cfg, dict) else {}
            signup = cfg.get("signup", {}) if isinstance(cfg, dict) else {}
            transcoding = cfg.get("transcoding", {}) if isinstance(cfg, dict) else {}
            live = cfg.get("live", {}) if isinstance(cfg, dict) else {}
            lineas = [
                _("Nombre: {0}").format(instancia.get("name", "")),
                _("Registro abierto: {0}").format(_("sí") if signup.get("enabled") else _("no")),
                _("Transcodificación: {0}").format(
                    _("activada") if transcoding.get("enabled") else _("desactivada")),
                _("Directos: {0}").format(_("activados") if live.get("enabled") else _("desactivados")),
                "",
                _("La edición completa de la configuración se hace desde la web de administración de "
                  "la instancia (es demasiado extensa para esta ventana)."),
            ]
            mostrar_info(self.f, "\n".join(lineas), _("Configuración de la instancia"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
