# -*- coding: utf-8 -*-
"""MainFrame (mixin): organización y avisos de RSS (fase 4 del informe de BlindRSS).

- Suprimir sobre un artículo: a la papelera o, si el feed lo tiene así (Editar feed), borrado
  definitivo que no vuelve a aparecer. Mayúsculas+Suprimir borra sin preguntar. La fila se quita
  en su sitio, sin recargar la lista ni perder la posición.
- Aviso de artículos nuevos sin los feeds silenciados ni los que una regla marcó "no avisar";
  pulsar la notificación abre el artículo (si es uno) o lleva al primero de los nuevos.
- Búsquedas guardadas en el árbol: su menú contextual (editar y borrar solo ese nodo).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.speech import speak, notificar
from app.services.rss.rss_service import rss_service

log = logging.getLogger(__name__)


class _Mf_rss_organizar:
    # ------------------------------------------------------------------ Suprimir
    def _enlazar_teclas_rss_lista(self):
        """Suprimir en la lista principal (una sola vez). Se enlaza en la LISTA, no como
        acelerador global, para que Suprimir siga borrando texto en los cuadros de texto."""
        if getattr(self, "_teclas_rss_enlazadas", False):
            return
        self._teclas_rss_enlazadas = True
        self.lista_items.Bind(wx.EVT_KEY_DOWN, self._tecla_lista_rss)

    def _tecla_lista_rss(self, event):
        if (event.GetKeyCode() in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE)
                and not event.ControlDown() and not event.AltDown()
                and getattr(self, "_modo_lista", "") == "rss"
                and not getattr(self, "_bandeja_activa", False)):
            self._suprimir_articulo_rss(sin_preguntar=event.ShiftDown())
            return
        event.Skip()

    def _suprimir_articulo_rss(self, db_id=None, sin_preguntar=False):
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        fila = db.query_one("SELECT feed_id, title FROM rss_articles WHERE id = ?", (db_id,))
        if not fila:
            return
        from app.services.rss.rss_borrados import borrar_definitivo, modo_de_feed
        if modo_de_feed(fila[0]) == "definitivo":
            if not sin_preguntar:
                from .accessible_controls import confirmar
                if not confirmar(self, _("¿Borrar definitivamente «{0}»? No irá a la papelera y no "
                                         "volverá a aparecer.").format(fila[1] or ""),
                                 _("Borrar artículo")):
                    return
            borrar_definitivo([db_id])
            aviso = _("Artículo borrado definitivamente.")
        else:
            rss_service.mover_a_papelera(db_id)
            aviso = _("Artículo movido a la papelera.")
        self._quitar_fila_articulo_rss(db_id)
        self._actualizar_contadores_rss()
        speak(aviso, interrupt=True)

    def _quitar_fila_articulo_rss(self, db_id):
        """Quita la fila del artículo en su sitio (sin recargar la lista); el foco queda en
        la fila siguiente, que ocupa su lugar."""
        try:
            idx = self.lista_items_db_ids.index(db_id)
        except ValueError:
            return
        self.lista_items.eliminar_fila(idx)
        del self.lista_items_db_ids[idx]
        meta = getattr(self, "_rss_meta", None)
        if isinstance(meta, dict):
            self._rss_meta = {(k - 1 if k > idx else k): v for k, v in meta.items() if k != idx}
        n = self.lista_items.GetItemCount()
        if n:
            nuevo = min(idx, n - 1)
            self.lista_items.Focus(nuevo)
            self.lista_items.Select(nuevo)

    # ------------------------------------------------------------------ avisos
    def _avisar_novedades_rss(self, ids_nuevos):
        from app.services.rss.rss_avisos import avisables
        ids = avisables(list(ids_nuevos or []))
        if not ids:
            return
        event_bus.emit("sound_trigger", "rss_new_article")
        if not config.get("rss", "notifications_enabled", True):
            return
        try:
            import wx.adv
            if len(ids) == 1:
                fila = db.query_one("SELECT title FROM rss_articles WHERE id = ?", (ids[0],))
                mensaje = _("Nuevo: {0}").format((fila[0] if fila else "") or "")
            else:
                mensaje = _("{0} artículos nuevos.").format(len(ids))
            n = wx.adv.NotificationMessage(_("Novedades en RSS"), mensaje, parent=self)
            n.Bind(wx.adv.EVT_NOTIFICATION_MESSAGE_CLICK, lambda e, i=list(ids): self._al_pulsar_aviso_rss(i))
            self._aviso_rss_vivo = n    # que no lo libere el recolector antes de pulsarlo
            n.Show(timeout=8)
        except Exception as e:
            log.debug(f"No se pudo mostrar la notificación de RSS: {e}")

    def _al_pulsar_aviso_rss(self, ids):
        try:
            if self.IsIconized():
                self.Iconize(False)
            self.Show()
            self.Raise()
        except Exception:
            pass
        if len(ids) == 1:
            try:
                self._on_ver_articulo_limpio(ids[0])     # el lector limpio del artículo
                return
            except Exception as e:
                log.debug(f"Abrir el artículo del aviso: {e}")
        # Varios: llevar el foco al primero de los nuevos si está en la lista visible.
        for aid in ids:
            if aid in getattr(self, "lista_items_db_ids", []):
                i = self.lista_items_db_ids.index(aid)
                self.lista_items.Focus(i)
                self.lista_items.Select(i)
                self.lista_items.SetFocus()
                return

    # ------------------------------------------------------------------ búsquedas en el árbol
    def _menu_nodo_busqueda_rss(self, menu, data, item) -> bool:
        tipo = str(data.get("rss_type", ""))
        if not tipo.startswith("search:"):
            return False
        try:
            bid = int(tipo.split(":", 1)[1])
        except ValueError:
            return False
        it = menu.Append(wx.ID_ANY, _("Editar la búsqueda guardada..."))
        self.Bind(wx.EVT_MENU, lambda e, b=bid: self._on_editar_busqueda_nodo(b), it)
        it = menu.Append(wx.ID_ANY, _("Borrar la búsqueda guardada"))
        self.Bind(wx.EVT_MENU, lambda e, b=bid, n=item: self._on_borrar_busqueda_nodo(b, n), it)
        return True

    def _on_editar_busqueda_nodo(self, bid):
        from .accessible_controls import CustomTextEntryDialog
        fila = db.query_one("SELECT name, query FROM rss_saved_searches WHERE id = ?", (bid,))
        if not fila:
            return
        d1 = CustomTextEntryDialog(self, _("Texto de la búsqueda:"), _("Editar búsqueda guardada"), value=fila[1])
        consulta = d1.GetValue().strip() if d1.ShowModal() == wx.ID_OK else ""
        d1.Destroy()
        if not consulta:
            return
        d2 = CustomTextEntryDialog(self, _("Nombre:"), _("Editar búsqueda guardada"), value=fila[0])
        nombre = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if nombre:
            rss_service.editar_busqueda_guardada(bid, nombre, consulta)
            notificar(_("Búsqueda actualizada."))
            self._cargar_navegacion()

    def _on_borrar_busqueda_nodo(self, bid, nodo):
        from .accessible_controls import confirmar
        fila = db.query_one("SELECT name FROM rss_saved_searches WHERE id = ?", (bid,))
        if fila and confirmar(self, _("¿Borrar la búsqueda guardada «{0}»?").format(fila[0]),
                              _("Borrar búsqueda")):
            rss_service.borrar_busqueda_guardada(bid)
            # Quitar SOLO ese nodo del árbol (nunca reconstruirlo entero al borrar un buffer).
            self._eliminar_nodo_buffer(nodo)
            notificar(_("Búsqueda borrada."))

    def _nodo_rss_por_tipo(self, rss_type: str):
        """Nodo del árbol de un buffer RSS por su tipo (p. ej. "search:3"), o None."""
        def _buscar(nodo):
            hijo, cookie = self.tree_nav.GetFirstChild(nodo)
            while hijo.IsOk():
                datos = self.tree_nav.GetItemData(hijo) or {}
                if isinstance(datos, dict) and datos.get("rss_type") == rss_type:
                    return hijo
                encontrado = _buscar(hijo)
                if encontrado is not None:
                    return encontrado
                hijo, cookie = self.tree_nav.GetNextChild(nodo, cookie)
            return None
        raiz = self.tree_nav.GetRootItem()
        return _buscar(raiz) if raiz.IsOk() else None

    def _busqueda_borrada_fuera_del_arbol(self, bid):
        """Tras borrar una búsqueda desde su gestor: quitar SOLO su nodo, si está."""
        nodo = self._nodo_rss_por_tipo(f"search:{bid}")
        if nodo is not None:
            self._eliminar_nodo_buffer(nodo)
