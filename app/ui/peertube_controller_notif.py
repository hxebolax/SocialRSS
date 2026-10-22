# -*- coding: utf-8 -*-
"""Controlador de PeerTube: notificaciones y estadísticas (mixin de UI, Fase 7).

Renderiza la sección de notificaciones en la lista principal (con la plantilla peertube_notificacion),
permite marcarlas como leídas y abrir el vídeo relacionado, ajusta las preferencias, y muestra las
estadísticas de un vídeo propio o de la instancia como texto accesible.
"""
import logging
from collections import defaultdict

from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _NotifUIMixin:
    # ------------------------------------------------------------------
    # Notificaciones: carga y render
    # ------------------------------------------------------------------
    def cargar_notificaciones(self):
        """Descarga y muestra las notificaciones en la lista principal."""
        f = self.f
        acc = f.active_peertube_account_id
        if not acc:
            return
        speak(_("Cargando notificaciones..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.listar_notificaciones(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar las notificaciones: {0}").format(err))
                return
            self._notifs = res or []
            with f._foco_silencioso():
                self._pintar_notificaciones()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _pintar_notificaciones(self):
        f = self.f
        from app.ui.main_frame import formatear_tiempo_relativo
        from app.core.plantillas import pulir_lectura, definicion
        f.lista_items.congelar_diferido()
        f.lista_items.DeleteAllItems()
        f.lista_items.limpiar_lecturas()
        f.lista_items.reset_focus_col()
        f.lista_items_db_ids.clear()
        cols = f._construir_columnas_plantilla("peertube_notificacion")
        f._modo_lista = "peertube_notificaciones"
        plantilla = (config.get("accessibility", "peertube_notificacion", "")
                     or definicion("peertube_notificacion")["default"])
        col_fecha = next((i for i, (c, _e) in enumerate(cols) if c == "date"), None)
        for i, n in enumerate(getattr(self, "_notifs", [])):
            valores = {"descripcion": n.get("descripcion", ""),
                       "date": formatear_tiempo_relativo(n.get("fecha", "")) if n.get("fecha") else "",
                       "estado": _("Leída") if n.get("leida") else _("Nueva")}
            pos = f._volcar_fila_plantilla(i, cols, valores)
            f.lista_items_db_ids.append("notif:" + str(n.get("id")))
            f.lista_items.establecer_lectura(
                pos, pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
            if col_fecha is not None:
                f.lista_items.establecer_fecha(pos, col_fecha, n.get("fecha", ""), valores["date"])
        f._restaurar_foco_lista()

    def _notif_de_index(self, idx):
        notifs = getattr(self, "_notifs", [])
        return notifs[idx] if 0 <= idx < len(notifs) else None

    # ------------------------------------------------------------------
    # Notificaciones: menú y acciones
    # ------------------------------------------------------------------
    def menu_notificaciones(self, menu, idx):
        import wx
        n = self._notif_de_index(idx)

        def add(label, cb):
            it = menu.Append(wx.ID_ANY, label)
            self.f.Bind(wx.EVT_MENU, lambda e: cb(), it)

        if n:
            if not n.get("leida"):
                add(_("Marcar como leída"), lambda: self.marcar_leida_notif(idx))
            if n.get("video_uuid"):
                add(_("Abrir el vídeo"), lambda: self.abrir_video_notif(idx))
        add(_("Marcar todas como leídas"), lambda: self.marcar_todas_notif())
        add(_("Preferencias de notificaciones..."), lambda: self.preferencias_notif_dialogo())

    def marcar_leida_notif(self, idx):
        n = self._notif_de_index(idx)
        if not n or not n.get("id"):
            return
        acc = self.f.active_peertube_account_id
        self._accion_async(lambda a: peertube_service.marcar_leidas(a, [n["id"]]),
                           tras_error=None)
        n["leida"] = True
        with self.f._foco_silencioso():
            self._pintar_notificaciones()
        speak(_("Marcada como leída."), interrupt=True)

    def marcar_todas_notif(self):
        acc = self.f.active_peertube_account_id
        self._accion_async(lambda a: peertube_service.marcar_todas_leidas(a),
                           msg_ok=_("Todas marcadas como leídas."),
                           tras_error=lambda: None)
        for n in getattr(self, "_notifs", []):
            n["leida"] = True
        with self.f._foco_silencioso():
            self._pintar_notificaciones()

    def abrir_video_notif(self, idx):
        n = self._notif_de_index(idx)
        if n and n.get("video_uuid"):
            self.reproducir_video("notif", {"uuid": n["video_uuid"], "name": n.get("descripcion", "")})

    def preferencias_notif_dialogo(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        speak(_("Cargando preferencias..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.preferencias_notificaciones(acc)

        def _fin(prefs, err):
            if err:
                notificar(_("No se pudieron cargar las preferencias: {0}").format(err))
                return
            from .peertube_notif_dialog import PeerTubeNotifPrefsDialog
            import wx
            dlg = PeerTubeNotifPrefsDialog(self.f, prefs or {})
            if dlg.ShowModal() == wx.ID_OK and dlg.resultado is not None:
                ajustes = dlg.resultado
                self._accion_async(
                    lambda a: peertube_service.guardar_preferencias_notificaciones(a, ajustes),
                    msg_ok=_("Preferencias guardadas."))
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Estadísticas
    # ------------------------------------------------------------------
    def estadisticas_video(self, db_id, datos):
        acc = self.f.active_peertube_account_id
        vid = self._video_id_de(db_id) or (datos or {}).get("uuid")
        if not vid:
            return
        speak(_("Cargando estadísticas..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.stats_video(acc, vid)

        def _fin(st, err):
            if err or not st:
                notificar(_("No se pudieron cargar las estadísticas: {0}").format(err or ""))
                return
            mins = int(st.get("tiempo_total", 0) // 60)
            media = int(st.get("tiempo_medio", 0))
            lineas = [_("Espectadores totales: {0}").format(st.get("total_espectadores", 0)),
                      _("Pico de espectadores: {0}").format(st.get("pico_espectadores", 0)),
                      _("Tiempo total de reproducción: {0} minutos").format(mins),
                      _("Tiempo medio por espectador: {0} segundos").format(media)]
            if st.get("paises"):
                lineas.append("")
                lineas.append(_("Países principales:"))
                for p in st["paises"]:
                    lineas.append(_("  {0}: {1} espectadores").format(p["pais"], p["espectadores"]))
            from .accessible_controls import mostrar_info
            mostrar_info(self.f, "\n".join(lineas), _("Estadísticas del vídeo"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def estadisticas_instancia(self):
        acc = self.f.active_peertube_account_id
        if not acc:
            return
        speak(_("Cargando estadísticas de la instancia..."), interrupt=True)
        etiquetas = {"totalUsers": _("Usuarios"), "totalVideos": _("Vídeos"),
                     "totalLocalVideos": _("Vídeos locales"),
                     "totalVideoComments": _("Comentarios"),
                     "totalInstanceFollowers": _("Instancias seguidoras"),
                     "totalInstanceFollowing": _("Instancias seguidas"),
                     "totalLocalVideoViews": _("Reproducciones locales"),
                     "totalLocalVideoChannels": _("Canales locales"),
                     "totalLocalPlaylists": _("Listas locales")}

        def _bg(ctx):
            return peertube_service.stats_instancia(acc)

        def _fin(st, err):
            if err or not st:
                notificar(_("No se pudieron cargar las estadísticas: {0}").format(err or ""))
                return
            lineas = [_("{0}: {1}").format(etiquetas.get(k, k), v) for k, v in st.items()]
            from .accessible_controls import mostrar_info
            mostrar_info(self.f, "\n".join(lineas), _("Estadísticas de la instancia"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
