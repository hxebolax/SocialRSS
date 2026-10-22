# -*- coding: utf-8 -*-
"""Controlador de PeerTube: selección de fila, panel de detalles y acciones de lectura (mixin).

Fase 2: panel de detalles del vídeo enfocado, apertura del detalle (Intro) y menú contextual de
lectura (abrir en el navegador, copiar enlace, ver detalles, abrir el canal o la cuenta). Las
acciones sociales (me gusta, suscribirse, comentar) y la reproducción llegan en fases posteriores.
"""
import logging

from app.core.database import db
from app.core.speech import speak, notificar, copiar_al_portapapeles
from app.core.privacy import abrir_url
from app.core.task_manager import task_manager

from app.services.peertube import peertube_service

log = logging.getLogger(__name__)


class _AccionesMixin:
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _db_id_de_index(self, index):
        f = self.f
        if index is None or index < 0 or index >= len(f.lista_items_db_ids):
            return None
        return f.lista_items_db_ids[index]

    def _datos_video(self, db_id):
        """Devuelve un dict con los datos cacheados del vídeo (o None)."""
        if not db_id:
            return None
        row = db.query_one(
            "SELECT uuid, name, channel_display, channel_name, account_name, account_host, "
            "duration, views, likes, dislikes, is_live, nsfw, is_local, description, url, "
            "instance_host FROM peertube_videos WHERE id = ?", (db_id,))
        if not row:
            return None
        return {"uuid": row[0], "name": row[1], "channel_display": row[2], "channel_name": row[3],
                "account_name": row[4], "account_host": row[5], "duration": row[6], "views": row[7],
                "likes": row[8], "dislikes": row[9], "is_live": row[10], "nsfw": row[11],
                "is_local": row[12], "description": row[13], "url": row[14], "instance_host": row[15]}

    # ------------------------------------------------------------------
    # Selección de fila → panel de detalles
    # ------------------------------------------------------------------
    def on_item_selected(self, index):
        f = self.f
        try:
            if not f._panel_detalles_activo():
                return
        except Exception:
            pass
        d = self._datos_video(self._db_id_de_index(index))
        if not d:
            return
        lineas = [d.get("name", "")]
        canal = d.get("channel_display") or d.get("channel_name") or ""
        if canal:
            lineas.append(_("Canal: {0}").format(canal))
        estado = []
        if d.get("is_live"):
            estado.append(_("En directo"))
        if d.get("nsfw"):
            estado.append(_("+18"))
        estado.append(_("Local") if d.get("is_local") else _("Remoto"))
        lineas.append(" · ".join(estado))
        lineas.append(_("{0} visualizaciones, {1} me gusta").format(d.get("views", 0), d.get("likes", 0)))
        if d.get("description"):
            lineas.append("")
            lineas.append(d["description"])
        try:
            f.txt_detalles.SetValue("\n".join(lineas))
        except Exception:
            pass

    def on_item_activated(self, index):
        """Intro: reproduce el vídeo; en la sección de notificaciones, abre el vídeo relacionado."""
        if str(getattr(self.f, "_modo_lista", "")) == "peertube_notificaciones":
            self.abrir_video_notif(index)
            return
        db_id = self._db_id_de_index(index)
        d = self._datos_video(db_id)
        if d:
            self.reproducir_video(db_id, d)

    # ------------------------------------------------------------------
    # Detalle del vídeo
    # ------------------------------------------------------------------
    def ver_detalle(self, db_id, datos):
        """Muestra el detalle del vídeo. Descarga la ficha completa (descripción larga, etc.) en
        segundo plano y la presenta en un cuadro accesible de solo lectura."""
        f = self.f
        acc = f.active_peertube_account_id
        uuid = datos.get("uuid")
        speak(_("Cargando el vídeo..."), interrupt=True)

        def _bg(ctx):
            try:
                return peertube_service.detalle_video(acc, uuid)
            except Exception:
                return datos  # si falla la red, al menos mostramos lo cacheado

        def _fin(det, err):
            info = det if isinstance(det, dict) and det.get("name") else datos
            self._mostrar_detalle(info)

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _mostrar_detalle(self, info):
        from .accessible_controls import mostrar_info
        from .ui_helpers import formatear_duracion_legible
        canal = info.get("channel_display") or info.get("channel_name") or ""
        lineas = [info.get("name", "")]
        if canal:
            lineas.append(_("Canal: {0}").format(canal))
        if not info.get("is_live"):
            # En lenguaje natural ("18 minutos y 7 segundos"), no en segundos crudos.
            legible = formatear_duracion_legible(info.get("duration"))
            if legible:
                lineas.append(_("Duración: {0}").format(legible))
        lineas.append(_("{0} visualizaciones, {1} me gusta").format(
            info.get("views", 0), info.get("likes", 0)))
        estado = []
        if info.get("is_live"):
            estado.append(_("En directo"))
        if info.get("nsfw"):
            estado.append(_("Contenido +18"))
        estado.append(_("Vídeo local") if info.get("is_local") else _("Vídeo remoto"))
        lineas.append(", ".join(estado))
        if info.get("description"):
            lineas.append("")
            lineas.append(info["description"])
        lineas.append("")
        lineas.append(_("Pulsa Intro sobre el vídeo para reproducirlo (suena el audio), o usa el menú "
                        "contextual para subtítulos, comentarios, calidad, descargar o abrir en el navegador."))
        mostrar_info(self.f, "\n".join(lineas), info.get("name") or _("Detalle del vídeo"))

    # ------------------------------------------------------------------
    # Menú contextual
    # ------------------------------------------------------------------
    def on_context_menu(self, menu, idx):
        import wx
        # Modo notificaciones: menú propio (no es un vídeo).
        if str(getattr(self.f, "_modo_lista", "")) == "peertube_notificaciones":
            self.menu_notificaciones(menu, idx)
            return
        db_id = self._db_id_de_index(idx)
        d = self._datos_video(db_id)
        if not d:
            return

        def add(label, cb):
            it = menu.Append(wx.ID_ANY, label)
            self.f.Bind(wx.EVT_MENU, lambda e: cb(), it)

        add(_("Reproducir"), lambda: self.reproducir_video(db_id, d))
        add(_("Ver subtítulos..."), lambda: self.ver_subtitulos(db_id, d))
        # Valoraciones y social.
        add(_("Me gusta"), lambda: self.votar(db_id, d, "like"))
        add(_("No me gusta"), lambda: self.votar(db_id, d, "dislike"))
        add(_("Quitar mi voto"), lambda: self.votar(db_id, d, "none"))
        add(_("Ver comentarios..."), lambda: self.ver_comentarios(db_id, d))
        add(_("Comentar..."), lambda: self.comentar(db_id, d))
        canal = d.get("channel_name")
        if canal:
            add(_("Suscribirse al canal"), lambda: self.suscribir_canal(d, True))
            add(_("Cancelar suscripción al canal"), lambda: self.suscribir_canal(d, False))
        # Reproducción / navegación.
        add(_("Calidad de reproducción..."), lambda: self.cambiar_calidad())
        add(_("Ver detalles"), lambda: self.ver_detalle(db_id, d))
        if d.get("url"):
            add(_("Abrir en el navegador"), lambda: abrir_url(d["url"]))
            add(_("Copiar enlace"), lambda: copiar_al_portapapeles(
                d["url"], _("Enlace copiado.")))
        if canal:
            add(_("Abrir el canal"), lambda: self._abrir_feed("channel:" + canal,
                                                              d.get("channel_display") or canal))
        cuenta = d.get("account_name")
        if cuenta:
            handle = f"{cuenta}@{d['account_host']}" if d.get("account_host") else cuenta
            add(_("Abrir la cuenta"), lambda: self._abrir_feed("account:" + handle, handle))
        # Historial: quitar del historial solo tiene sentido en esa sección.
        if str(getattr(self.f, "active_peertube_type", "")) == "historial":
            add(_("Quitar del historial"), lambda: self.quitar_del_historial(db_id, d))
            add(_("Vaciar todo el historial"), lambda: self.vaciar_historial())
        # Biblioteca: listas, descarga, y edición si el vídeo es del usuario.
        add(_("Añadir a 'ver más tarde'"), lambda: self.ver_mas_tarde(db_id, d))
        add(_("Añadir a una lista..."), lambda: self.añadir_a_lista_dialogo(db_id, d))
        add(_("Descargar..."), lambda: self.descargar_video_ui(db_id, d))
        if self._es_mio(d):
            add(_("Editar mi vídeo..."), lambda: self.editar_mi_video(db_id, d))
            add(_("Borrar mi vídeo"), lambda: self.borrar_mi_video(db_id, d))
            add(_("Estadísticas del vídeo..."), lambda: self.estadisticas_video(db_id, d))
            if d.get("is_live"):
                add(_("Datos de emisión (RTMP/clave)..."), lambda: self.datos_emision(db_id, d))
                add(_("Ver sesiones y repeticiones..."), lambda: self.ver_sesiones_directo(db_id, d))
        # Moderación personal / reportes.
        if cuenta:
            add(_("Silenciar la cuenta"), lambda: self.silenciar_cuenta_de(d))
            add(_("Silenciar la instancia"), lambda: self.silenciar_servidor_de(d))
        add(_("Reportar el vídeo..."), lambda: self.reportar_video_de(db_id, d))
        # Herramientas de moderador (bloquear/desbloquear el vídeo en la instancia).
        if self.es_moderador_actual():
            add(_("Bloquear el vídeo (moderación)..."), lambda: self.bloquear_video_ui(db_id, d))
            add(_("Desbloquear el vídeo (moderación)"), lambda: self.desbloquear_video_ui(db_id, d))

    def _abrir_feed(self, seccion, titulo):
        """Abre un feed de canal/cuenta como buffer temporal en la lista."""
        f = self.f
        f.active_peertube_type = seccion
        f._modo_lista = "peertube_videos"
        try:
            f.lbl_buffer_titulo.SetLabel("PeerTube - " + titulo)
        except Exception:
            pass
        self.cargar_listado()

    def botones_botonera(self, db_id):
        """Botones de acción bajo la lista para el vídeo enfocado."""
        d = self._datos_video(db_id)
        if not d:
            return []
        botones = [(_("Ver detalles"), lambda: self.ver_detalle(db_id, d))]
        if d.get("url"):
            botones.append((_("Abrir en el navegador"), lambda: abrir_url(d["url"])))
        return botones

    # ------------------------------------------------------------------
    # Búsqueda y navegación
    # ------------------------------------------------------------------
    def buscar_videos_dialogo(self):
        import wx
        from .accessible_controls import CustomTextEntryDialog, confirmar
        if not self.f.active_peertube_account_id:
            speak(_("Añade primero una cuenta de PeerTube."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self.f, _("Texto a buscar en PeerTube:"), _("Buscar vídeos"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        self._abrir_feed("search:" + texto, _("Búsqueda: {0}").format(texto))
        if confirmar(self.f, _("¿Guardar esta búsqueda en el árbol?"),
                     _("Guardar búsqueda")) == wx.ID_YES:
            from app.core.config_manager import config
            guardadas = list(config.get("peertube", "saved_searches", []))
            if texto not in guardadas:
                guardadas.append(texto)
                config.set("peertube", "saved_searches", guardadas)
                self.f._cargar_navegacion()

    def buscar_canales_dialogo(self):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        if not self.f.active_peertube_account_id:
            speak(_("Añade primero una cuenta de PeerTube."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self.f, _("Nombre o tema del canal:"), _("Buscar canales"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        query = dlg.GetValue().strip()
        dlg.Destroy()
        if not query:
            return
        acc = self.f.active_peertube_account_id
        speak(_("Buscando canales..."), interrupt=True)

        def _bg(ctx):
            return peertube_service.buscar_canales(acc, query)

        def _fin(res, err):
            if err:
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            if not res:
                speak(_("No se encontraron canales."), interrupt=True)
                return
            import wx
            etiquetas = [_("{0} ({1}) — {2} seguidores").format(
                c["display"], c["handle"], c["followers"]) for c in res]
            d = wx.SingleChoiceDialog(self.f, _("Elige un canal para ver sus vídeos:"),
                                      _("Resultados de canales"), etiquetas)
            if d.ShowModal() == wx.ID_OK:
                c = res[d.GetSelection()]
                self._abrir_feed("channel:" + c["handle"], c["display"])
            d.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def ir_a_canal_dialogo(self):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        if not self.f.active_peertube_account_id:
            speak(_("Añade primero una cuenta de PeerTube."), interrupt=True)
            return
        dlg = CustomTextEntryDialog(self.f, _("Identificador del canal (nombre@instancia):"),
                                    _("Ir a un canal"))
        if dlg.ShowModal() == wx.ID_OK:
            handle = dlg.GetValue().strip().lstrip("@")
            if handle:
                self._abrir_feed("channel:" + handle, handle)
        dlg.Destroy()
