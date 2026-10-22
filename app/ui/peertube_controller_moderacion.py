# -*- coding: utf-8 -*-
"""Controlador de PeerTube: moderación (mixin de UI, Fase 8).

Abre la cola de denuncias y los vídeos bloqueados, y permite bloquear/desbloquear el vídeo enfocado.
Las acciones que retiran contenido piden confirmación.
"""
import logging

from app.core.speech import speak
from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _ModeracionUIMixin:
    def es_moderador_actual(self):
        try:
            return peertube_service.es_moderador(self.f.active_peertube_account_id)
        except Exception:
            return False

    def cola_denuncias_dialogo(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        from .peertube_moderacion_dialogs import PeerTubeAbusosDialog
        dlg = PeerTubeAbusosDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def mis_denuncias_dialogo(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        from .peertube_moderacion_dialogs import PeerTubeAbusosDialog
        dlg = PeerTubeAbusosDialog(self.f, acc, solo_mias=True)
        dlg.ShowModal()
        dlg.Destroy()

    def videos_bloqueados_dialogo(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        from .peertube_moderacion_dialogs import PeerTubeBloqueadosDialog
        dlg = PeerTubeBloqueadosDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def bloquear_video_ui(self, db_id, datos):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        vid = self._video_id_de(db_id) or (datos or {}).get("uuid")
        if not vid:
            return
        dlg = CustomTextEntryDialog(self.f, _("Motivo del bloqueo (opcional):"),
                                    _("Bloquear vídeo"), multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        razon = dlg.GetValue().strip()
        dlg.Destroy()
        self._accion_async(lambda a: peertube_service.bloquear_video(a, vid, razon=razon),
                           msg_ok=_("Vídeo bloqueado."))

    def desbloquear_video_ui(self, db_id, datos):
        vid = self._video_id_de(db_id) or (datos or {}).get("uuid")
        if not vid:
            return
        self._accion_async(lambda a: peertube_service.desbloquear_video(a, vid),
                           msg_ok=_("Vídeo desbloqueado."))
