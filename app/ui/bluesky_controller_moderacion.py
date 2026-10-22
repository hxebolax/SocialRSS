# -*- coding: utf-8 -*-
"""Controlador Bluesky: MODERACIÓN (mixin).

- Panel de detalles con la decisión en el contexto "detalle" (etiquetas, palabras
  silenciadas, multimedia difuminada, cita moderada).
- Notificaciones moderadas.
- Menú de la publicación: "Mostrar de todos modos", "Ocultar esta publicación para mí",
  silenciar / quitar silencio del hilo.
- Diálogos: preferencias de moderación (contenido adulto y etiquetas), palabras silenciadas,
  servicios de etiquetado, publicaciones ocultadas y feed Siguiendo / hilos.

Va aparte para que bluesky_controller_acciones no supere el tamaño máximo de archivo.
"""
import json
import logging

import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.ui.accessible_controls import mostrar_info, confirmar

from app.services.bluesky import bluesky_service

log = logging.getLogger(__name__)


class _ModeracionMixin:
    # ------------------------------------------------------------------
    # Decisiones
    # ------------------------------------------------------------------
    def _prefs_mod(self, acc=None):
        acc = acc or self.f.active_bluesky_account_id
        try:
            return bluesky_service.prefs_moderacion(acc) if acc else {}
        except Exception as e:
            log.debug(f"Preferencias de moderación no disponibles: {e}")
            return {}

    def _moderacion_post_db(self, db_id, contexto="detalle"):
        """(decisión, datos_mod) de una publicación guardada."""
        from app.services.bluesky.bluesky_moderacion import moderar_post
        from app.services.bluesky.bluesky_moddatos import de_json
        fila = db.query_one("SELECT COALESCE(labels,''), COALESCE(mod_json,''), "
                            "COALESCE(author_did,''), COALESCE(uri,'') FROM bluesky_posts "
                            "WHERE id = ?", (db_id,))
        if not fila:
            return None, {}
        datos = de_json(fila[1])
        res = moderar_post(datos, self._prefs_mod(), contexto=contexto, uri=fila[3],
                           etiquetas=fila[0], author_did=fila[2])
        return res, datos

    def _moderar_notif(self, contenido, mod_json, autor_did, contexto="lista"):
        """(ocultar, contenido) de una notificación: etiquetas de la notificación y de la
        cuenta que la provoca, y palabras silenciadas en el texto si es una publicación de otra
        cuenta (mención, respuesta, cita)."""
        from app.services.bluesky.bluesky_moderacion import moderar_post, texto_aviso
        from app.services.bluesky.bluesky_moddatos import de_json
        datos = de_json(mod_json)
        if not datos:
            return False, contenido
        try:
            res = moderar_post(datos, self._prefs_mod(), contexto=contexto,
                               author_did=autor_did or "")
        except Exception as e:
            log.debug(f"Moderación de notificación fallida: {e}")
            return False, contenido
        if contexto == "lista" and res["filtrar"]:
            return True, contenido
        if res["difuminar"] and (contexto == "lista" or res["no_override"]):
            return False, texto_aviso(res, contexto)
        if contexto == "detalle" and res["difuminar"]:
            return False, texto_aviso(res, "detalle") + "\n" + (contenido or "")
        return False, contenido

    def _confirmar_media_moderada(self, db_id) -> bool:
        """Antes de abrir/reproducir multimedia difuminada por una etiqueta (B6): pregunta.
        Si no se puede revelar (no_override), avisa y no deja abrirla."""
        try:
            res, _d = self._moderacion_post_db(db_id, contexto="detalle")
        except Exception:
            return True
        if not res or not (res["difuminar_media"] or res["difuminar"]):
            return True
        if res["no_override"] or res.get("no_override_media"):
            mostrar_info(self.f, _("Esta multimedia está oculta por moderación y no se puede "
                                   "mostrar."), _("Multimedia oculta"))
            return False
        motivo = res.get("motivo_media") or res.get("motivo") or ""
        return bool(confirmar(self.f, _("La multimedia de esta publicación está marcada como "
                                        "«{0}». ¿Quieres abrirla igualmente?").format(motivo),
                              _("Multimedia con advertencia")))

    # ------------------------------------------------------------------
    # Panel de detalles
    # ------------------------------------------------------------------
    def _detalle_post_bsky(self, db_id):
        f = self.f
        row = db.query_one("""
            SELECT author_name, author_handle, content, indexed_at, labels,
                   like_count, repost_count, reply_count, quote_count, media_json,
                   liked_uri, reposted_uri
            FROM bluesky_posts WHERE id = ?
        """, (db_id,))
        if not row:
            return
        (autor, handle, contenido, fecha, labels, likes, reposts, replies,
         quotes, media_json, liked_uri, reposted_uri) = row
        try:
            mod, datos = self._moderacion_post_db(db_id, contexto="detalle")
        except Exception as e:
            log.debug(f"Moderación del detalle fallida: {e}")
            mod, datos = None, {}
        _adj = []
        if media_json:
            try:
                _adj = json.loads(media_json)
            except Exception:
                _adj = []
        media_oculta = bool(mod and (mod["difuminar_media"] or mod["no_override"]))
        if (mod and mod["difuminar_media"] and db_id in getattr(self, "_revelados_bsky", ())
                and not mod.get("no_override_media")):
            media_oculta = False   # "Mostrar de todos modos" también revela la multimedia
        # Sonido de adjunto: suena al navegar (también con el panel oculto), salvo si la
        # multimedia está difuminada por una etiqueta (B6).
        if _adj and not media_oculta:
            event_bus.emit("sound_trigger", "status_has_media")
        if not f.txt_detalles.IsShown():
            return
        from datetime import datetime
        from app.services.bluesky.bluesky_moderacion import (
            etiquetas_visibles, texto_aviso, texto_media_oculta, moderar_cita)
        try:
            dt = datetime.fromisoformat((fecha or "").replace("Z", "+00:00"))
            fecha_str = dt.astimezone().strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            fecha_str = (fecha or "").replace("T", " ")[:19]
        txt = _("Autor: {0} (@{1})\n").format(autor or "", handle or "")
        txt += _("Fecha: {0}\n").format(fecha_str)
        lista_etiquetas = [e for e in (labels or "").split(",") if e]
        visibles = ", ".join(etiquetas_visibles(lista_etiquetas))
        if visibles:
            txt += _("ADVERTENCIA DE CONTENIDO: {0}\n").format(visibles)
        if mod and mod.get("alertas"):
            txt += _("Aviso: {0}\n").format(", ".join(mod["alertas"]))
        reacc = []
        if liked_uri:
            reacc.append(_("Te gusta"))
        if reposted_uri:
            reacc.append(_("Reposteado"))
        txt += _("Interacciones: {0} me gusta, {1} reposts, {2} respuestas, {3} citas\n").format(
            likes or 0, reposts or 0, replies or 0, quotes or 0)
        if reacc:
            txt += _("Tus reacciones: {0}\n").format(", ".join(reacc))
        if mod and mod["no_override"] and mod["difuminar"]:
            txt += "\n" + _("[Contenido retirado por moderación: no puede mostrarse]")
            f.txt_detalles.SetValue(txt)
            return
        if mod and mod["difuminar"]:
            # En detalle se muestra con su aviso delante (el usuario lo abrió a propósito).
            txt += texto_aviso(mod, "detalle") + "\n"
        if _adj and media_oculta:
            txt += texto_media_oculta(mod) + "\n"
        else:
            for att in _adj:
                txt += _("[Adjunto ({0}): {1}]\n").format(
                    att.get("type"), att.get("alt") or _("Sin descripción"))
        q = (datos or {}).get("q") or {}
        if q:
            mc = moderar_cita(datos, self._prefs_mod())
            if q.get("blk"):
                txt += _("[Cita de una publicación no disponible]") + "\n"
            elif mc and mc.get("filtrar") and mc.get("no_override"):
                txt += _("[Cita oculta por moderación]") + "\n"
            elif mc and (mc.get("difuminar") or mc.get("filtrar")):
                txt += _("[Cita oculta: {0}]").format(mc.get("motivo") or "") + "\n"
            elif q.get("tx"):
                txt += _("Cita: {0}").format(q.get("tx")) + "\n"
        txt += "\n" + (contenido or "")
        f.txt_detalles.SetValue(txt)

    # ------------------------------------------------------------------
    # Filas de la lista (en su sitio, sin mover el foco)
    # ------------------------------------------------------------------
    def _refrescar_fila_bsky(self, db_id):
        f = self.f
        if db_id not in getattr(f, "lista_items_db_ids", []):
            return
        idx = f.lista_items_db_ids.index(db_id)
        if idx >= f.lista_items.GetItemCount():
            return
        fila = db.query_one(
            f"SELECT {self._SQL_COLS_POST}, COALESCE((SELECT t.reason_json FROM "
            "bluesky_timeline_posts t WHERE t.post_db_id = p.id AND t.account_id = p.account_id "
            "AND t.timeline = ?), ''), " + self._SQL_COLS_MOD +
            " FROM bluesky_posts p WHERE p.id = ?", (f.active_timeline_type, db_id))
        if not fila:
            return
        from app.core.plantillas import pulir_lectura, definicion
        from app.core.config_manager import config
        from collections import defaultdict
        _id, valores, _fecha, media_ev, _t, _c = self._valores_post(fila)
        cols = getattr(f, "_cols_actuales", None) or getattr(self, "_cols_posts", [])
        plantilla = config.get("accessibility", "bluesky_post", "") or definicion("bluesky_post")["default"]
        f.lista_items.actualizar_fila(idx, [valores.get(c, "") for c, _e in cols],
                                      pulir_lectura(plantilla.format_map(defaultdict(str, valores))))
        f.lista_items.establecer_medios(idx, media_ev)
        return valores

    def _quitar_fila_bsky(self, db_id):
        f = self.f
        try:
            idx = f.lista_items_db_ids.index(db_id)
        except ValueError:
            return
        if idx >= f.lista_items.GetItemCount():
            return
        f.lista_items.eliminar_fila(idx)
        del f.lista_items_db_ids[idx]
        n = f.lista_items.GetItemCount()
        if n:
            nuevo = min(idx, n - 1)
            f.lista_items.Focus(nuevo)
            f.lista_items.Select(nuevo)

    # ------------------------------------------------------------------
    # Menú de la publicación
    # ------------------------------------------------------------------
    def _menu_moderacion_post(self, menu, db_id, uri, root_uri):
        f = self.f
        acc = f.active_bluesky_account_id
        try:
            mod, _d = self._moderacion_post_db(db_id, contexto="lista")
        except Exception:
            mod = None
        revelados = getattr(self, "_revelados_bsky", set())
        if mod and (mod["difuminar"] or mod["difuminar_media"]) and not mod["no_override"]:
            if db_id in revelados:
                it = menu.Append(wx.ID_ANY, _("Volver a ocultar el contenido con advertencia"))
                f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.revelar_publicacion(d, False), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Mostrar de todos modos"))
                f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.revelar_publicacion(d, True), it)
        ocultas = bluesky_service.publicaciones_ocultas(acc) if acc else []
        if uri in ocultas:
            it = menu.Append(wx.ID_ANY, _("Dejar de ocultar esta publicación"))
            f.Bind(wx.EVT_MENU, lambda e, u=uri, d=db_id: self.ocultar_publicacion(d, u, False), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Ocultar esta publicación para mí"))
            f.Bind(wx.EVT_MENU, lambda e, u=uri, d=db_id: self.ocultar_publicacion(d, u, True), it)
        if acc and root_uri in bluesky_service.hilos_silenciados(acc):
            it = menu.Append(wx.ID_ANY, _("Quitar silencio del hilo"))
            f.Bind(wx.EVT_MENU, lambda e, r=root_uri: self.quitar_silencio_hilo(r), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Silenciar hilo"))
            f.Bind(wx.EVT_MENU, lambda e, r=root_uri: self.silenciar_hilo(r), it)

    def revelar_publicacion(self, db_id, revelar=True):
        """"Mostrar de todos modos" (B18): el contenido difuminado de ESA publicación se
        muestra en la lista durante la sesión (nunca lo que no se puede revelar)."""
        if not hasattr(self, "_revelados_bsky"):
            self._revelados_bsky = set()
        mod, _d = self._moderacion_post_db(db_id, contexto="lista")
        if mod and mod["no_override"]:
            notificar(_("Este contenido está retirado por moderación y no se puede mostrar."))
            return
        if revelar:
            self._revelados_bsky.add(db_id)
        else:
            self._revelados_bsky.discard(db_id)
        valores = self._refrescar_fila_bsky(db_id)
        if revelar:
            texto = (valores or {}).get("content") or ""
            speak(_("Mostrando: {0}").format(texto) if texto else _("Contenido mostrado."),
                  interrupt=True)
        else:
            speak(_("Contenido ocultado de nuevo."), interrupt=True)

    def ocultar_publicacion(self, db_id, uri, ocultar=True):
        """"Ocultar esta publicación para mí" (hiddenPostsPref, B11): desaparece de las listas
        (también las que la citan) y se puede recuperar en Bluesky > Publicaciones ocultadas."""
        acc = self.f.active_bluesky_account_id
        if not acc or not uri:
            return

        def _cb(res, error):
            if error:
                notificar(_("No se pudo guardar: {0}").format(error))
        bluesky_service.ocultar_post_async(acc, uri, ocultar, callback=_cb)
        if ocultar:
            self._quitar_fila_bsky(db_id)
            notificar(_("Publicación ocultada. Puedes recuperarla en Bluesky, Publicaciones "
                        "ocultadas."))
        else:
            notificar(_("La publicación vuelve a mostrarse."))

    def quitar_silencio_hilo(self, root_uri):
        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
            else:
                notificar(_("Ya no está silenciado el hilo."))
        bluesky_service.silenciar_hilo_async(self.f.active_bluesky_account_id, root_uri, False,
                                             callback=_cb)

    # ------------------------------------------------------------------
    # Diálogos
    # ------------------------------------------------------------------
    def _cuenta_o_aviso(self):
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
        return acc

    def _tras_cambio_moderacion(self):
        """Tras cambiar las preferencias, repintar el buffer de posts activo (la decisión
        cambia para muchas filas). Se hace con el foco silencioso para que el lector no relea."""
        f = self.f
        try:
            if f.active_bluesky_account_id and getattr(f, "_modo_lista", "") in (
                    "bluesky_posts", "bluesky_notifs"):
                with f._foco_silencioso():
                    self.cargar_listado()
        except Exception as e:
            log.debug(f"No se pudo repintar tras cambiar la moderación: {e}")

    def palabras_silenciadas(self):
        acc = self._cuenta_o_aviso()
        if not acc:
            return
        from .bluesky_dialogs_moderacion import BlueskyMutedWordsDialog
        dlg = BlueskyMutedWordsDialog(self.f, acc)
        dlg.ShowModal()
        cambiado = getattr(dlg, "cambiado", False)
        dlg.Destroy()
        if cambiado:
            self._tras_cambio_moderacion()

    def preferencias_moderacion(self, labeler_did=None):
        acc = self._cuenta_o_aviso()
        if not acc:
            return
        from .bluesky_dialogs_moderacion import BlueskyModeracionDialog
        # El repintado se hace cuando el servidor confirma el guardado (al_guardar).
        dlg = BlueskyModeracionDialog(self.f, acc, labeler_did=labeler_did,
                                      al_guardar=self._tras_cambio_moderacion)
        dlg.ShowModal()
        dlg.Destroy()

    def servicios_etiquetas(self):
        acc = self._cuenta_o_aviso()
        if not acc:
            return
        from .bluesky_dialogs_moderacion import BlueskyLabelersDialog
        dlg = BlueskyLabelersDialog(self.f, acc, ajustar=self.preferencias_moderacion)
        dlg.ShowModal()
        cambiado = getattr(dlg, "cambiado", False)
        dlg.Destroy()
        if cambiado:
            self._tras_cambio_moderacion()

    def publicaciones_ocultadas(self):
        acc = self._cuenta_o_aviso()
        if not acc:
            return
        from .bluesky_dialogs_moderacion import BlueskyHiddenPostsDialog
        dlg = BlueskyHiddenPostsDialog(self.f, acc)
        dlg.ShowModal()
        cambiado = getattr(dlg, "cambiado", False)
        dlg.Destroy()
        if cambiado:
            self._tras_cambio_moderacion()

    def preferencias_feed_siguiendo(self):
        acc = self._cuenta_o_aviso()
        if not acc:
            return
        from .bluesky_dialogs_moderacion import BlueskyFeedViewDialog
        dlg = BlueskyFeedViewDialog(self.f, acc, al_guardar=self._tras_cambio_moderacion)
        dlg.ShowModal()
        dlg.Destroy()
