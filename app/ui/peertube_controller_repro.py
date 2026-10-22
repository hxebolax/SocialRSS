# -*- coding: utf-8 -*-
"""Controlador de PeerTube: reproducción con BassKit y subtítulos (mixin, Fase 3).

Intro sobre un vídeo lo reproduce (por player2, el reproductor con ducking; de un vídeo suena solo el
audio). La fuente se elige automáticamente (MP4 progresivo preferido; HLS resuelto a una variante) y
se ofrece continuar donde se dejó. Los subtítulos se muestran como texto accesible. Si el CDN rechaza
al reproductor, se reintenta a través del proxy local con Range.
"""
import json
import logging

from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _ReproMixin:
    def reproducir_video(self, db_id, datos):
        """Prepara y reproduce el vídeo enfocado. Descarga la ficha (con las fuentes), elige la mejor,
        resuelve la variante HLS si hace falta y la manda a player2. Todo en segundo plano."""
        f = self.f
        acc = f.active_peertube_account_id
        uuid = (datos or {}).get("uuid")
        if not acc or not uuid:
            return
        speak(_("Preparando la reproducción..."), interrupt=True)

        def _bg(ctx):
            det = peertube_service.detalle_video(acc, uuid)
            fuentes = json.loads(det.get("files_json") or "[]")
            es_directo = bool(det.get("is_live"))
            fuente = peertube_service.seleccionar_fuente(fuentes, es_directo=es_directo)
            url = ""
            if fuente:
                url = fuente["url"]
                if fuente.get("es_hls"):
                    url = peertube_service.resolver_variante_hls(acc, url) or url
            return {"det": det, "url": url, "fuente": fuente, "es_directo": es_directo}

        def _fin(res, err):
            if err:
                notificar(_("No se pudo preparar el vídeo: {0}").format(err))
                return
            if not res or not res.get("url") or not res.get("fuente"):
                notificar(_("Este vídeo no tiene una fuente de audio reproducible disponible."))
                return
            self._lanzar_reproduccion(acc, uuid, res)

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _lanzar_reproduccion(self, acc, uuid, res):
        from app.services.media_player.player2_service import (player2_service, KIND_YOUTUBE,
                                                               KIND_RADIO)
        det = res["det"]
        fuente = res["fuente"]
        es_directo = res["es_directo"]
        url_directa = res["url"]
        titulo = det.get("name") or _("Vídeo de PeerTube")
        kind = KIND_RADIO if es_directo else KIND_YOUTUBE
        # Continuar donde se dejó, salvo directos (no tiene sentido) o si el usuario lo desactivó.
        resume = (not es_directo) and bool(config.get("peertube", "save_position", True))
        if fuente.get("necesita_ffmpeg"):
            speak(_("Este formato puede no reproducirse; intentándolo..."), interrupt=True)

        def _played(ok):
            if ok:
                speak(_("Reproduciendo: {0}").format(titulo), interrupt=True)
                return
            # El reproductor no pudo con la URL directa (posible User-Agent del CDN): reintentar a
            # través del proxy local con Range.
            try:
                from app.services.peertube.peertube_stream import stream_peertube
                url_proxy = stream_peertube.url_para(url_directa)
            except Exception:
                notificar(_("No se pudo reproducir el vídeo."))
                return

            def _played2(ok2):
                if ok2:
                    speak(_("Reproduciendo: {0}").format(titulo), interrupt=True)
                else:
                    notificar(_("No se pudo reproducir el vídeo (formato no compatible)."))
            player2_service.cargar_y_reproducir(url_proxy, titulo, kind, resume=resume,
                                                callback=_played2)

        # Camino principal: URL directa (permite continuar donde se dejó, con clave estable).
        player2_service.cargar_y_reproducir(url_directa, titulo, kind, resume=resume,
                                            callback=_played)
        # Registrar la vista en el historial remoto (best-effort, en segundo plano).
        task_manager.start_task(lambda ctx: peertube_service.registrar_vista(acc, uuid, 0),
                                pass_context=True)

    # ------------------------------------------------------------------
    # Subtítulos
    # ------------------------------------------------------------------
    def ver_subtitulos(self, db_id, datos):
        f = self.f
        acc = f.active_peertube_account_id
        uuid = (datos or {}).get("uuid")
        if not acc or not uuid:
            return
        speak(_("Buscando subtítulos..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.listar_subtitulos(acc, uuid)

        def _fin(subs, err):
            if err:
                notificar(_("No se pudieron cargar los subtítulos: {0}").format(err))
                return
            if not subs:
                speak(_("Este vídeo no tiene subtítulos."), interrupt=True)
                return
            import wx
            etiquetas = [_("{0}{1}").format(s.get("etiqueta") or s.get("idioma") or _("Desconocido"),
                                            _(" (automáticos)") if s.get("automatico") else "")
                         for s in subs]
            d = wx.SingleChoiceDialog(f, _("Elige el idioma de los subtítulos:"),
                                      _("Subtítulos"), etiquetas)
            # Preseleccionar el idioma preferido de Preferencias (si ese vídeo lo tiene).
            pref = str(config.get("peertube", "preferred_subtitle_lang", "es") or "").lower()
            idx_pref = next((i for i, s in enumerate(subs)
                             if str(s.get("idioma") or "").lower() == pref), -1)
            if idx_pref >= 0:
                d.SetSelection(idx_pref)
            if d.ShowModal() == wx.ID_OK:
                self._mostrar_subtitulo(acc, subs[d.GetSelection()])
            d.Destroy()

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _mostrar_subtitulo(self, acc, sub):
        speak(_("Descargando subtítulos..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.descargar_subtitulo(acc, sub.get("path"))

        def _fin(texto, err):
            if err or not texto:
                notificar(_("No se pudo obtener el texto de los subtítulos."))
                return
            from .accessible_controls import mostrar_info
            mostrar_info(self.f, texto,
                         _("Subtítulos ({0})").format(sub.get("etiqueta") or sub.get("idioma") or ""))

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Calidad de reproducción
    # ------------------------------------------------------------------
    def cambiar_calidad(self):
        import wx
        opciones = [(_("Automática (menor ancho de banda)"), "auto"), (_("1080p"), "1080"),
                    (_("720p"), "720"), (_("480p"), "480"), (_("360p"), "360"), (_("240p"), "240")]
        etiquetas = [o[0] for o in opciones]
        actual = str(config.get("peertube", "preferred_resolution", "auto"))
        sel_ini = next((i for i, o in enumerate(opciones) if o[1] == actual), 0)
        d = wx.SingleChoiceDialog(self.f, _("Calidad preferida de reproducción:"),
                                  _("Calidad de vídeo"), etiquetas)
        d.SetSelection(sel_ini)
        if d.ShowModal() == wx.ID_OK:
            config.set("peertube", "preferred_resolution", opciones[d.GetSelection()][1])
            speak(_("Calidad guardada."), interrupt=True)
        d.Destroy()
