# -*- coding: utf-8 -*-
"""Filtros del servidor de Mastodon en la ventana principal (acciones y presentación).

- componer_contenido_toot(): el texto de la columna Contenido de un toot (impulso, CW, cuerpo,
  cita, tarjeta) con el filtro del servidor aplicado; lo comparten la carga completa, la
  inserción incremental y el "Mostrar de todos modos", para que las tres digan lo mismo.
- _Mf_filtros_mastodon (mixin del MainFrame, vía _Mf_mastodon_extra):
  * "Mostrar de todos modos": revela en la sesión un toot filtrado con 'avisar' (o con la
    multimedia difuminada) y actualiza SOLO su fila, sin recargar la lista ni mover el foco.
  * "Filtrar esta publicación…": añade el toot (su id ORIGINAL) a un filtro existente o a uno
    nuevo, como la web oficial, y recalcula el buffer activo.
  * _confirmar_media_filtrada(): pide confirmación antes de abrir o reproducir la multimedia
    de un toot con la acción 'difuminar' (o con el texto oculto por un aviso).
"""
import json
import logging

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.services.mastodon import filtros_servidor as fsrv

log = logging.getLogger(__name__)


def componer_contenido_toot(text, spoiler_text, reblog_by_username, author_username, quote_json,
                            card_json, media_json, ev, clave, expand_cw=False, ocultar_emojis=False,
                            account_id=None, timeline="") -> str:
    """Texto de la columna Contenido de un toot al estilo TWBlue, con el filtro aplicado."""
    from .ui_helpers import formato_cita
    if ocultar_emojis:
        from app.core.html_utils import quitar_emojis
    else:
        quitar_emojis = None
    segmentos = []
    n_fijos = 0
    if reblog_by_username:
        segmentos.append(_("Impulsado de {user}").format(user=author_username))
        n_fijos = 1

    def _cuerpo():
        c = (text or "").replace('\n', ' ').strip()
        return quitar_emojis(c) if quitar_emojis else c

    # Con advertencia de contenido (CW) se muestra solo el aviso; el cuerpo, si se pidió
    # expandir las CW automáticamente.
    if spoiler_text and spoiler_text.strip():
        segmentos.append(_("Advertencia de contenido: {cw}").format(cw=spoiler_text.strip()))
        if expand_cw:
            c = _cuerpo()
            if c:
                segmentos.append(c)
    else:
        segmentos.append(_cuerpo())
    cita = formato_cita(quote_json, account_id=account_id, timeline=timeline, clave=clave)
    if cita:
        segmentos.append(cita)
    # Tarjeta de previsualización del enlace (card): título y descripción del artículo.
    if card_json:
        try:
            card = json.loads(card_json)
            ctit = (card.get("title") or "").strip()
            cdesc = (card.get("description") or "").strip()
            if ctit:
                segmentos.append(_("Enlace: {0}").format(ctit + (". " + cdesc if cdesc else "")))
        except Exception:
            pass
    segmentos = fsrv.segmentos_lista(segmentos, ev, bool(media_json and media_json != "[]"),
                                     clave, n_fijos)
    return ": ".join(s for s in segmentos if s)


class _Mf_filtros_mastodon:

    # ---- evaluación del filtro para la fila / buffer activo ----
    def _ev_filtro_toot(self, db_id, timeline=None, account_id=None):
        acc = account_id or self.active_mastodon_account_id
        tl = str(timeline if timeline is not None else (self.active_timeline_type or ""))
        foco = tl.startswith("thread:") and str(db_id).endswith(":" + tl.split(":", 1)[1])
        return fsrv.evaluar_db_id(db_id, acc, tl, foco_detalle=foco)

    def _contexto_fila_filtro(self, db_id):
        """(account_id, timeline) de la fila: en la bandeja, los de SU origen."""
        if getattr(self, "_bandeja_activa", False):
            try:
                idx = self.lista_items_db_ids.index(db_id)
                o = self._bandeja_origen[idx]
                return o.get("account_id"), o.get("timeline_type")
            except (ValueError, IndexError, AttributeError):
                pass
        return self.active_mastodon_account_id, str(self.active_timeline_type or "")

    # ---- menú contextual ----
    def _items_menu_mostrar_filtrado(self, menu, db_id):
        """'Mostrar de todos modos' si la publicación está filtrada y aún oculta."""
        try:
            acc, tl = self._contexto_fila_filtro(db_id)
            ev = self._ev_filtro_toot(db_id, tl, acc)
            filtrada = fsrv.esta_filtrado(ev)
            if not filtrada:
                fq = db.query_one("SELECT COALESCE(quote_json,'') FROM mastodon_statuses WHERE id = ?",
                                  (db_id,))
                filtrada = bool(fq and fsrv.cita_filtrada(fq[0], acc, tl))
            if filtrada and not fsrv.esta_revelado(db_id):
                it = menu.Append(wx.ID_ANY, _("Mostrar de todos modos (filtrada)"))
                self.Bind(wx.EVT_MENU, lambda evt, d=db_id: self._on_mostrar_filtrado(d), it)
        except Exception as e:
            log.debug(f"No se pudo evaluar el filtro de la publicación: {e}")

    def _items_menu_filtrar_publicacion(self, menu, db_id):
        """'Filtrar esta publicación…' (no en las propias, que nunca se filtran)."""
        try:
            if self._toot_es_mio(db_id):
                return
        except Exception:
            pass
        it = menu.Append(wx.ID_ANY, _("Filtrar esta publicación..."))
        self.Bind(wx.EVT_MENU, lambda evt, d=db_id: self._on_filtrar_publicacion(d), it)

    # ---- Mostrar de todos modos ----
    def _on_mostrar_filtrado(self, db_id):
        fsrv.revelar(db_id)
        idxs = self._actualizar_fila_toot_filtro(db_id)
        foco = self.lista_items.GetFocusedItem()
        if foco in idxs:
            # El usuario ha pedido verlo: se lee la fila ya revelada (una sola vez).
            self.lista_items.reanunciar_foco()
        else:
            speak(_("Publicación mostrada."), interrupt=True)

    def _actualizar_fila_toot_filtro(self, db_id):
        """Rehace EN SU SITIO la columna Contenido (y el sonido de multimedia) de las filas de
        ese toot, sin recargar la lista ni mover el foco. Devuelve los índices tocados."""
        idxs = [i for i, d in enumerate(self.lista_items_db_ids) if d == db_id]
        n = self.lista_items.GetItemCount()
        idxs = [i for i in idxs if 0 <= i < n]
        if not idxs:
            return []
        cols = getattr(self, "_cols_actuales", [])
        col = self._col_de_campo(cols, "content")
        acc, tl = self._contexto_fila_filtro(db_id)
        ev = self._ev_filtro_toot(db_id, tl, acc)
        if getattr(self, "_bandeja_activa", False):
            nuevo = self._inbox_contenido_mastodon_db(db_id, acc, tl)
            evento = None
        else:
            fila = db.query_one(
                "SELECT content, spoiler_text, reblog_by_username, author_username, quote_json, "
                "COALESCE(card_json,''), media_json FROM mastodon_statuses WHERE id = ?", (db_id,))
            if not fila:
                return []
            text, cw, rbu, au, qj, cj, media = fila
            if self._es_lista_notif_mastodon():
                nuevo = fsrv.contenido_con_filtro((text or "").replace("\n", " ").strip(), ev, db_id)
                evento = None
            else:
                nuevo = componer_contenido_toot(
                    text, cw, rbu, au, qj, cj, media, ev, db_id,
                    expand_cw=config.get("accessibility", "expand_cw", False),
                    ocultar_emojis=config.get("accessibility", "hide_emojis", False),
                    account_id=acc, timeline=tl)
                from .ui_helpers import evento_media_de_attachments
                evento = fsrv.evento_media_filtrado(ev, evento_media_de_attachments(media), db_id)
        for i in idxs:
            try:
                if col is not None and nuevo is not None:
                    self.lista_items.SetItem(i, col, nuevo)
                if not getattr(self, "_bandeja_activa", False) and not self._es_lista_notif_mastodon():
                    self.lista_items.establecer_medios(i, evento)
            except Exception:
                pass
        return idxs

    # ---- Filtrar esta publicación ----
    def _on_filtrar_publicacion(self, db_id):
        acc = self.active_mastodon_account_id
        if not acc or not db_id:
            return
        from app.services.mastodon.mastodon_service import mastodon_service
        speak(_("Cargando tus filtros..."), interrupt=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar los filtros: {0}").format(err))
                return
            filtros = [f for f in (res or []) if f.get("v2", True)]
            if res and not filtros:
                notificar(_("Tu servidor no admite filtrar publicaciones sueltas (necesita Mastodon 4.0)."))
                return
            wx.CallAfter(self._elegir_filtro_para_publicacion, acc, db_id, filtros)
        mastodon_service.obtener_filtros_servidor_async(acc, callback=_fin)

    def _elegir_filtro_para_publicacion(self, acc, db_id, filtros):
        from app.services.mastodon.mastodon_service import mastodon_service
        from .mastodon_filtros_dialogs import ElegirFiltroDialog
        from .accessible_controls import CustomTextEntryDialog
        dlg = ElegirFiltroDialog(self, filtros)
        ok = dlg.ShowModal() == wx.ID_OK
        elegido = dlg.elegido()
        dlg.Destroy()
        if not ok:
            return

        def _hecho(res, err):
            if err:
                notificar(_("No se pudo filtrar la publicación: {0}").format(err))
                return
            notificar(_("Publicación añadida al filtro."))
            wx.CallAfter(fsrv.recalcular_buffer_activo, self, acc)

        if elegido is None:
            dlg_t = CustomTextEntryDialog(self, _("Título del filtro nuevo:"), _("Nuevo filtro"))
            titulo = dlg_t.GetValue().strip() if dlg_t.ShowModal() == wx.ID_OK else ""
            dlg_t.Destroy()
            if not titulo:
                return
            mastodon_service.crear_filtro_para_publicacion_async(acc, titulo, db_id, callback=_hecho)
        else:
            mastodon_service.filtrar_publicacion_async(acc, elegido["id"], db_id, callback=_hecho)

    # ---- multimedia difuminada ----
    def _confirmar_media_filtrada(self, db_id, parent=None, ev=None) -> bool:
        """True si se puede abrir/reproducir la multimedia del toot. Si está protegida por un
        filtro (difuminar, o aviso sin revelar), pregunta antes; al aceptar queda revelada."""
        if not db_id or not self.active_mastodon_account_id:
            return True
        try:
            if ev is None:
                acc, tl = self._contexto_fila_filtro(db_id)
                ev = self._ev_filtro_toot(db_id, tl, acc)
        except Exception:
            return True
        if not fsrv.media_filtrada(ev, db_id):
            return True
        from .accessible_controls import confirmar
        titulos = ", ".join(ev.get("titulos_media") or ev.get("titulos") or [])
        if not confirmar(parent or self,
                         _("La multimedia de esta publicación está oculta por tu filtro «{0}». "
                           "¿Abrirla de todos modos?").format(titulos),
                         _("Multimedia filtrada")):
            return False
        fsrv.revelar(db_id)
        try:
            self._actualizar_fila_toot_filtro(db_id)
        except Exception:
            pass
        return True
