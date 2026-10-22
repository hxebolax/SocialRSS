# -*- coding: utf-8 -*-
"""Controlador de PeerTube: interacción social (mixin de UI, Fase 4).

Valoraciones (me gusta/no me gusta), suscripciones a canales, comentarios (visor de árbol), silencios
de cuenta/servidor, reportes e historial. Las valoraciones y suscripciones dan retroalimentación
inmediata (optimista) y avisan si la API falla.
"""
import logging

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _SocialUIMixin:
    # ------------------------------------------------------------------
    # Helper de acción en segundo plano
    # ------------------------------------------------------------------
    def _accion_async(self, fn_servicio, *, msg_ok=None, tras_error=None):
        acc = self.f.active_peertube_account_id

        def _bg(ctx):
            return fn_servicio(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo completar la acción: {0}").format(err))
                if tras_error:
                    tras_error()
                return
            if msg_ok:
                speak(msg_ok, interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Valoraciones
    # ------------------------------------------------------------------
    def votar(self, db_id, datos, valoracion):
        uuid = (datos or {}).get("uuid")
        if not uuid:
            return
        # Retroalimentación inmediata (optimista).
        msg = {"like": _("Me gusta."), "dislike": _("No me gusta."),
               "none": _("Voto retirado.")}.get(valoracion, "")
        speak(msg, interrupt=True)
        self._accion_async(lambda acc: peertube_service.votar(acc, uuid, valoracion),
                           tras_error=lambda: speak(_("No se pudo registrar tu voto."), interrupt=True))

    # ------------------------------------------------------------------
    # Suscripciones
    # ------------------------------------------------------------------
    def _handle_canal(self, datos):
        """Construye el identificador de canal (nombre@instancia) desde los datos del vídeo."""
        canal = (datos or {}).get("channel_name") or ""
        if not canal:
            return ""
        if "@" in canal:
            return canal
        host = (datos or {}).get("instance_host") or ""
        return f"{canal}@{host}" if host else canal

    def suscribir_canal(self, datos, suscribir=True):
        handle = self._handle_canal(datos)
        if not handle:
            speak(_("No se pudo identificar el canal."), interrupt=True)
            return
        if suscribir:
            speak(_("Suscribiéndote..."), interrupt=True)
            self._accion_async(lambda acc: peertube_service.suscribirse(acc, handle),
                               msg_ok=_("Suscrito al canal."))
        else:
            self._accion_async(lambda acc: peertube_service.cancelar_suscripcion(acc, handle),
                               msg_ok=_("Suscripción cancelada."))

    # ------------------------------------------------------------------
    # Comentarios
    # ------------------------------------------------------------------
    def ver_comentarios(self, db_id, datos):
        uuid = (datos or {}).get("uuid")
        if not uuid:
            return
        from .peertube_comment_dialog import PeerTubeComentariosDialog
        dlg = PeerTubeComentariosDialog(self.f, self.f.active_peertube_account_id, uuid,
                                        (datos or {}).get("name", ""))
        dlg.ShowModal()
        dlg.Destroy()

    def comentar(self, db_id, datos):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        uuid = (datos or {}).get("uuid")
        if not uuid:
            return
        dlg = CustomTextEntryDialog(self.f, _("Escribe tu comentario:"), _("Comentar"),
                                    multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        speak(_("Publicando comentario..."), interrupt=True)
        self._accion_async(lambda acc: peertube_service.publicar_comentario(acc, uuid, texto),
                           msg_ok=_("Comentario publicado."))

    # ------------------------------------------------------------------
    # Silencios y reportes
    # ------------------------------------------------------------------
    def silenciar_cuenta_de(self, datos):
        import wx
        from .accessible_controls import confirmar
        cuenta = (datos or {}).get("account_name") or ""
        host = (datos or {}).get("account_host") or ""
        handle = f"{cuenta}@{host}" if host else cuenta
        if not handle:
            return
        if confirmar(self.f, _("¿Silenciar a {0}? No verás su contenido.").format(handle),
                     _("Silenciar cuenta")) != wx.ID_YES:
            return
        self._accion_async(lambda acc: peertube_service.silenciar_cuenta(acc, handle),
                           msg_ok=_("Cuenta silenciada."))

    def silenciar_servidor_de(self, datos):
        import wx
        from .accessible_controls import confirmar
        host = (datos or {}).get("account_host") or (datos or {}).get("instance_host") or ""
        if not host:
            return
        if confirmar(self.f, _("¿Silenciar toda la instancia {0}?").format(host),
                     _("Silenciar servidor")) != wx.ID_YES:
            return
        self._accion_async(lambda acc: peertube_service.silenciar_servidor(acc, host),
                           msg_ok=_("Servidor silenciado."))

    def reportar_video_de(self, db_id, datos):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        # El id numérico se necesita para el reporte; se lee de la caché.
        from app.core.database import db
        row = db.query_one("SELECT video_id FROM peertube_videos WHERE id = ?", (db_id,))
        video_id = row[0] if row and row[0] else (datos or {}).get("uuid")
        dlg = CustomTextEntryDialog(self.f, _("Motivo del reporte (para los moderadores):"),
                                    _("Reportar vídeo"), multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        motivo = dlg.GetValue().strip()
        dlg.Destroy()
        if len(motivo) < 2:
            speak(_("El motivo es demasiado corto."), interrupt=True)
            return
        self._accion_async(lambda acc: peertube_service.reportar_video(acc, video_id, motivo),
                           msg_ok=_("Reporte enviado a los moderadores."))

    # ------------------------------------------------------------------
    # Historial
    # ------------------------------------------------------------------
    def vaciar_historial(self):
        import wx
        from .accessible_controls import confirmar
        if confirmar(self.f, _("¿Vaciar todo tu historial de vídeos vistos?"),
                     _("Vaciar historial")) != wx.ID_YES:
            return
        self._accion_async(lambda acc: peertube_service.vaciar_historial(acc),
                           msg_ok=_("Historial vaciado."))

    def quitar_del_historial(self, db_id, datos):
        uuid = (datos or {}).get("uuid")
        if not uuid:
            return
        self._accion_async(lambda acc: peertube_service.quitar_del_historial(acc, uuid),
                           msg_ok=_("Quitado del historial."))
