# -*- coding: utf-8 -*-
"""MainFrame (mixin): importar y exportar suscripciones RSS.
OPML (con vista previa) y suscripciones de YouTube desde el ZIP de Google Takeout, que reutiliza
la misma vista previa con casillas."""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service

log = logging.getLogger(__name__)


class _Mf_rss_importar:
    def _on_importar_opml(self, event):
        if not self.active_rss_account_id:
            notificar(_("Active primero una cuenta RSS."))
            return
            
        dlg = wx.FileDialog(
            self, 
            message=_("Seleccionar archivo OPML"),
            defaultDir="", 
            defaultFile="",
            wildcard="Archivos OPML (*.opml;*.xml)|*.opml;*.xml",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        )
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        path = dlg.GetPath()
        dlg.Destroy()
        # VISTA PREVIA antes de escribir (auditoría, prioridad A): se analiza el OPML y se
        # muestra qué es nuevo, qué ya está suscrito y qué no vale, con su carpeta destino.
        acc = self.active_rss_account_id
        speak(_("Analizando el archivo OPML..."), interrupt=True)

        def _bg(ctx):
            return rss_service.analizar_opml(acc, path)

        def _con_analisis(res, err):
            if err or res is None:
                notificar(_("No se pudo leer el OPML: {0}").format(err or ""))
                return
            self._mostrar_vista_previa_opml(acc, res)

        task_manager.start_task(_bg, callback=_con_analisis, pass_context=True)

    def _mostrar_vista_previa_opml(self, account_id, analisis, titulo=None, origen=None):
        """Muestra la vista previa del OPML con casillas: los NUEVOS vienen marcados (salvo los
        que traen "marcar": False); los duplicados e inválidos, desmarcados e identificados.
        Importa solo lo elegido. También la usa la importación de Google Takeout."""
        titulo = titulo or _("Importar OPML: vista previa")
        origen = origen or _("el archivo")
        from .accessible_controls import AccessibleCheckListBox, habilitar_escape_cierre
        nuevos = [f for f in analisis if f["estado"] == "nuevo"]
        dups = sum(1 for f in analisis if f["estado"] == "duplicado")
        invalidos = sum(1 for f in analisis if f["estado"] == "invalido")
        if not analisis:
            notificar(_("No hay feeds en {0}.").format(origen))
            return
        etiquetas = []
        for f in analisis:
            carpeta = " / ".join(f["category_path"]) if f["category_path"] else _("(sin carpeta)")
            if f["estado"] == "nuevo":
                extra = (" — " + f["fuente_legible"]) if f.get("fuente_legible") else ""
                etiquetas.append(_("{0} — {1}").format(f["title"], carpeta) + extra)
            elif f["estado"] == "duplicado":
                etiquetas.append(_("{0} — ya estás suscrito").format(f["title"]))
            else:
                etiquetas.append(_("{0} — dirección no válida").format(f["title"]))
        dlg = wx.Dialog(self, title=titulo, size=(640, 500),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label=_(
            "{0} feeds en {4}: {1} nuevos, {2} ya suscritos y {3} no válidos.\n"
            "Marca los que quieres importar (los nuevos vienen marcados):").format(
                len(analisis), len(nuevos), dups, invalidos, origen)), 0, wx.ALL, 8)
        clb = AccessibleCheckListBox(dlg, choices=etiquetas, name=_("Feeds para importar"))
        for i, f in enumerate(analisis):
            if f["estado"] == "nuevo" and f.get("marcar", True):
                clb.Check(i, True)
        s.Add(clb, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizer(s)
        try:
            habilitar_escape_cierre(dlg)
        except Exception:
            pass
        if etiquetas:
            clb.SetSelection(0)
        clb.SetFocus()
        elegidos = None
        if dlg.ShowModal() == wx.ID_OK:
            elegidos = [analisis[i] for i in clb.GetCheckedItems()
                        if analisis[i]["estado"] != "invalido"]
        dlg.Destroy()
        if not elegidos:
            if elegidos is not None:
                notificar(_("No se marcó ningún feed importable."))
            return
        speak(_("Importando {0} feeds...").format(len(elegidos)), interrupt=True)

        def _finish(n, err):
            if err:
                notificar(_("Error al importar OPML: {0}").format(err))
            else:
                notificar(_("{0} feeds importados.").format(n or 0))
                self._cargar_navegacion()
        rss_service.importar_feeds_seleccionados_async(account_id, elegidos, callback=_finish)

    def _on_exportar_opml(self, event):
        if not self.active_rss_account_id:
            notificar(_("Active primero una cuenta RSS."))
            return
            
        dlg = wx.FileDialog(
            self, 
            message=_("Guardar archivo OPML como"),
            defaultDir="", 
            defaultFile="suscripciones.opml",
            wildcard="Archivos OPML (*.opml)|*.opml",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            
            def _bg_export(ctx):
                rss_service.exportar_feeds_opml(self.active_rss_account_id, path)
                return True
                
            def _finish(res, err):
                if err:
                    notificar(f"Error al exportar OPML: {err}")
                else:
                    notificar(_("OPML exportado con éxito."))
                    
            task_manager.start_task(_bg_export, callback=_finish, pass_context=True)
        dlg.Destroy()

    # ------------------------------------------------------------------ Google Takeout
    def _on_importar_takeout_youtube(self, event=None):
        """Suscribirse a los canales y listas de YouTube desde el ZIP de Google Takeout (sin
        cookies ni sesión): se analiza el ZIP sin descomprimirlo y se muestra la vista previa."""
        if not self.active_rss_account_id:
            notificar(_("Active primero una cuenta RSS."))
            return
        from .accessible_controls import mostrar_info
        mostrar_info(self, _(
            "Para obtener el archivo: entra en takeout.google.com, deja marcado solo "
            "«YouTube y YouTube Music» y pide la exportación. Google te enviará un enlace para "
            "descargar un archivo ZIP. No hace falta descomprimirlo: elígelo tal cual en la "
            "siguiente ventana.\n\n"
            "Se ofrecen tus suscripciones, tus canales y tus listas de reproducción (marcados) y "
            "los canales de tu historial de reproducciones (sin marcar)."),
            _("Importar desde Google Takeout"))
        dlg = wx.FileDialog(self, message=_("Elegir el ZIP de Google Takeout"),
                            wildcard=_("Archivos ZIP (*.zip)|*.zip"),
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        path = dlg.GetPath()
        dlg.Destroy()
        acc = self.active_rss_account_id
        speak(_("Analizando el archivo de Google Takeout..."), interrupt=True)

        def _bg(ctx):
            return rss_service.analizar_takeout_youtube(acc, path)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo leer la exportación de YouTube: {0}").format(err or ""))
                return
            from app.services.rss.rss_takeout import NOMBRES_FUENTE
            for f in res:
                f["fuente_legible"] = _(NOMBRES_FUENTE.get(f.get("fuente"), ""))
            self._mostrar_vista_previa_opml(acc, res, titulo=_("Importar desde Google Takeout"),
                                            origen=_("la exportación de YouTube"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
