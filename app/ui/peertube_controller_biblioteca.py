# -*- coding: utf-8 -*-
"""Controlador de PeerTube: biblioteca y creación (mixin de UI, Fase 5).

Subir e importar vídeos, descargar, añadir a listas / "ver más tarde", editar o borrar los vídeos
propios, y gestionar listas, canales e importaciones. La subida/descarga muestran progreso por hitos
(cada 20 %) para no saturar al lector de pantalla.
"""
import logging
import threading

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _BibliotecaUIMixin:
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _video_id_de(self, db_id):
        row = db.query_one("SELECT video_id FROM peertube_videos WHERE id = ?", (db_id,))
        return row[0] if row and row[0] else None

    def _es_mio(self, datos):
        try:
            yo = peertube_service.nombre_usuario(self.f.active_peertube_account_id) or ""
            return bool(datos.get("account_name")) and datos["account_name"].lower() == yo.lower()
        except Exception:
            return False

    def _progreso_hitos(self, etiqueta):
        """Devuelve una función progreso(hecho, total) que solo habla en múltiplos de 20 %."""
        estado = {"ultimo": -1}

        def _p(hecho, total):
            if not total:
                return
            pct = int(hecho * 100 / total)
            if pct >= estado["ultimo"] + 20 or pct >= 100:
                estado["ultimo"] = pct - (pct % 20)
                speak(_("{0}: {1} %.").format(etiqueta, pct), interrupt=False)
        return _p

    # ------------------------------------------------------------------
    # Subir e importar
    # ------------------------------------------------------------------
    def subir_dialogo(self):
        import wx
        acc = self.f.active_peertube_account_id
        if not acc:
            speak(_("Añade primero una cuenta de PeerTube."), interrupt=True)
            return
        from .peertube_biblioteca_dialogs import PeerTubeSubirDialog
        dlg = PeerTubeSubirDialog(self.f, acc)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        d = dlg.resultado
        dlg.Destroy()
        speak(_("Subiendo vídeo..."), interrupt=True)
        prog = self._progreso_hitos(_("Subida"))

        def _bg(ctx):
            return peertube_service.subir_video_reanudable(
                acc, d["ruta"], channel_id=d["channel_id"], name=d["name"], privacy=d["privacy"],
                description=d.get("description"), tags=d.get("tags"), nsfw=d.get("nsfw"),
                progreso=prog)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo subir el vídeo: {0}").format(err))
            else:
                notificar(_("Vídeo subido. Puede tardar en procesarse en el servidor."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def importar_dialogo(self):
        import wx
        acc = self.f.active_peertube_account_id
        if not acc:
            speak(_("Añade primero una cuenta de PeerTube."), interrupt=True)
            return
        from .peertube_biblioteca_dialogs import PeerTubeImportarDialog
        dlg = PeerTubeImportarDialog(self.f, acc)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        d = dlg.resultado
        dlg.Destroy()
        speak(_("Iniciando importación..."), interrupt=True)

        def _bg(ctx):
            if d["es_magnet"]:
                return peertube_service.importar_magnet(acc, d["url"], channel_id=d["channel_id"],
                                                        name=d["name"], privacy=d["privacy"])
            return peertube_service.importar_url(acc, d["url"], channel_id=d["channel_id"],
                                                 name=d["name"], privacy=d["privacy"])

        def _fin(res, err):
            if err:
                notificar(_("No se pudo iniciar la importación: {0}").format(err))
            else:
                notificar(_("Importación iniciada. Revisa su estado en 'Mis importaciones'."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def mis_importaciones_dialogo(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        from .peertube_gestion_dialogs import PeerTubeImportacionesDialog
        dlg = PeerTubeImportacionesDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def gestionar_listas_dialogo(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        from .peertube_gestion_dialogs import PeerTubeListasDialog
        dlg = PeerTubeListasDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def gestionar_canales_dialogo(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        from .peertube_gestion_dialogs import PeerTubeCanalesDialog
        dlg = PeerTubeCanalesDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    # ------------------------------------------------------------------
    # Añadir a listas / ver más tarde
    # ------------------------------------------------------------------
    def ver_mas_tarde(self, db_id, datos):
        acc = self.f.active_peertube_account_id
        vid = self._video_id_de(db_id)
        if not vid:
            return
        self._accion_async(lambda a: peertube_service.añadir_ver_mas_tarde(a, vid),
                           msg_ok=_("Añadido a 'ver más tarde'."))

    def añadir_a_lista_dialogo(self, db_id, datos):
        import wx
        acc = self.f.active_peertube_account_id
        vid = self._video_id_de(db_id)
        if not vid:
            return
        speak(_("Cargando tus listas..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.mis_listas(acc)

        def _fin(listas, err):
            if err:
                notificar(_("No se pudieron cargar las listas: {0}").format(err))
                return
            elegibles = [l for l in (listas or []) if not l.get("es_ver_mas_tarde")]
            if not elegibles:
                speak(_("No tienes listas. Crea una primero."), interrupt=True)
                return
            d = wx.SingleChoiceDialog(self.f, _("Elige la lista:"), _("Añadir a una lista"),
                                      [l["display"] for l in elegibles])
            if d.ShowModal() == wx.ID_OK:
                lid = elegibles[d.GetSelection()]["id"]
                self._accion_async(lambda a: peertube_service.añadir_a_lista(a, lid, vid),
                                   msg_ok=_("Añadido a la lista."))
            d.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Descargar
    # ------------------------------------------------------------------
    def descargar_video_ui(self, db_id, datos):
        import wx
        acc = self.f.active_peertube_account_id
        uuid = (datos or {}).get("uuid")
        if not uuid:
            return
        from app.core.config_manager import config
        carpeta = config.get("peertube", "download_dir", "") or ""
        if not carpeta:
            with wx.DirDialog(self.f, _("Carpeta de descarga"),
                              style=wx.DD_DEFAULT_STYLE) as dd:
                if dd.ShowModal() != wx.ID_OK:
                    return
                carpeta = dd.GetPath()
        speak(_("Descargando..."), interrupt=True)
        prog = self._progreso_hitos(_("Descarga"))

        def _bg(ctx):
            return peertube_service.descargar_video(acc, uuid, carpeta, progreso=prog)

        def _fin(ruta, err):
            if err:
                notificar(_("No se pudo descargar: {0}").format(err))
            else:
                import os
                notificar(_("Descargado: {0}").format(os.path.basename(ruta)))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Editar / borrar mis vídeos
    # ------------------------------------------------------------------
    def editar_mi_video(self, db_id, datos):
        import wx
        vid = self._video_id_de(db_id)
        if not vid:
            return
        from .peertube_biblioteca_dialogs import PeerTubeEditarVideoDialog
        row = db.query_one("SELECT name, description, privacy FROM peertube_videos WHERE id = ?",
                           (db_id,))
        actuales = {"name": row[0] if row else "", "description": row[1] if row else "",
                    "privacy": row[2] if row else 3}
        dlg = PeerTubeEditarVideoDialog(self.f, actuales)
        if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
            d = dlg.resultado
            self._accion_async(lambda a: peertube_service.editar_video(
                a, vid, name=d["name"], description=d["description"], privacy=d["privacy"]),
                msg_ok=_("Vídeo actualizado."))
        dlg.Destroy()

    def borrar_mi_video(self, db_id, datos):
        import wx
        from .accessible_controls import confirmar
        vid = self._video_id_de(db_id)
        if not vid:
            return
        if confirmar(self.f, _("¿Borrar tu vídeo «{0}»? No se puede deshacer.").format(
                (datos or {}).get("name", "")), _("Borrar vídeo")) != wx.ID_YES:
            return
        self._accion_async(lambda a: peertube_service.borrar_video(a, vid),
                           msg_ok=_("Vídeo borrado."), tras_error=None)
