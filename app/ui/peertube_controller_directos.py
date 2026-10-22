# -*- coding: utf-8 -*-
"""Controlador de PeerTube: emisiones en directo (mixin de UI, Fase 6).

Crear un directo, consultar sus datos de emisión (URL RTMP y clave secreta) y ver sus sesiones y
repeticiones. La reproducción de un directo usa el reproductor normal (Intro sobre un directo lo
reproduce como la radio).
"""
import logging

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _DirectosUIMixin:
    def crear_directo_dialogo(self):
        import wx
        acc = self.f.active_peertube_account_id
        if not acc:
            speak(_("Añade primero una cuenta de PeerTube."), interrupt=True)
            return
        from .peertube_directos_dialogs import PeerTubeDirectoDialog
        dlg = PeerTubeDirectoDialog(self.f, acc)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        d = dlg.resultado
        dlg.Destroy()
        speak(_("Creando el directo..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.crear_directo(
                acc, d["channel_id"], d["name"], privacy=d["privacy"],
                permanent_live=d["permanent_live"], latency_mode=d["latency_mode"],
                save_replay=d["save_replay"])

        def _fin(res, err):
            if err:
                notificar(_("No se pudo crear el directo: {0}").format(err))
                return
            notificar(_("Directo creado. Estos son sus datos de emisión."))
            if res and res.get("id"):
                self._mostrar_datos_emision(acc, res["id"])
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def datos_emision(self, db_id, datos):
        """Muestra los datos de emisión (RTMP/clave) del directo enfocado."""
        acc = self.f.active_peertube_account_id
        vid = self._video_id_de(db_id) or (datos or {}).get("uuid")
        if not vid:
            return
        self._mostrar_datos_emision(acc, vid)

    def _mostrar_datos_emision(self, acc, video_id):
        speak(_("Obteniendo los datos de emisión..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.info_directo(acc, video_id)

        def _fin(info, err):
            if err or not info:
                notificar(_("No se pudieron obtener los datos de emisión: {0}").format(err or ""))
                return
            from .peertube_directos_dialogs import PeerTubeDatosEmisionDialog
            dlg = PeerTubeDatosEmisionDialog(self.f, info)
            dlg.ShowModal()
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def ver_sesiones_directo(self, db_id, datos):
        acc = self.f.active_peertube_account_id
        vid = self._video_id_de(db_id) or (datos or {}).get("uuid")
        if not vid:
            return
        speak(_("Cargando sesiones..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.sesiones_directo(acc, vid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar las sesiones: {0}").format(err))
                return
            from .accessible_controls import mostrar_info
            if not res:
                mostrar_info(self.f, _("Este directo aún no tiene sesiones."), _("Sesiones"))
                return
            from datetime import datetime, timezone
            lineas = []
            for s in res:
                inicio = s.get("inicio", "")
                try:
                    inicio = datetime.fromisoformat(inicio.replace("Z", "+00:00")).astimezone(
                        timezone.utc).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    pass
                repe = _(" — repetición: {0}").format(s["repeticion_titulo"]) if s.get(
                    "repeticion_titulo") else ""
                estado = _(" (con error)") if s.get("error") else ""
                lineas.append(_("Sesión del {0}{1}{2}").format(inicio, estado, repe))
            mostrar_info(self.f, "\n".join(lineas), _("Sesiones del directo"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
