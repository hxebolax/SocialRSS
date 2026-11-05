# -*- coding: utf-8 -*-
"""Helpers de conteo de caracteres y selector de emojis del compositor.
"""
import logging
import wx
import wx.adv

from app.core.speech import speak
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl


log = logging.getLogger(__name__)

# El conteo vive en el servicio (sin wx) para que lo usen también la interfaz GTK y el divisor de hilos.
from app.services.mastodon.mastodon_longitud import (  # noqa: E402,F401
    MASTODON_MAX_CHARS, _URL_RE, _MENTION_RE, contar_grafemas, longitud_contable_mastodon)


class EmojiPickerDialog(wx.Dialog):
    """Selector ACCESIBLE de emojis personalizados de la instancia. Como los emojis son
    imágenes con un 'atajo' (:nombre:), se listan por su atajo y categoría (lo que el lector
    puede leer); al elegir uno se inserta ':atajo:' en el texto y el servidor lo convierte."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Insertar emoji personalizado"), size=(520, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Selector de emojis"))
        self.account_id = account_id
        self.shortcode_elegido = None
        self._emojis = []
        self._filtrados = []

        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Buscar (por nombre o categoría):")), 0, wx.ALL, 5)
        self.txt_buscar = AccessibleTextCtrl(self, name=_("Buscar emoji"),
                                             ayuda_texto=_("Escribe para filtrar; flecha abajo para ir a la lista."))
        self.txt_buscar.Bind(wx.EVT_TEXT, lambda e: self._aplicar_filtro())
        self.txt_buscar.Bind(wx.EVT_KEY_DOWN, self._on_buscar_key)
        s.Add(self.txt_buscar, 0, wx.EXPAND | wx.ALL, 5)

        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de emojis"))
        self.lista.InsertColumn(0, _("Atajo"), width=230)
        self.lista.InsertColumn(1, _("Categoría"), width=230)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._insertar())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_ins = wx.Button(self, wx.ID_OK, _("&Insertar"))
        self.btn_ins.SetDefault()
        btns.Add(self.btn_ins, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_BUTTON, lambda e: self._insertar(), id=wx.ID_OK)
        self.CenterOnParent()
        self.txt_buscar.SetFocus()
        self._cargar()

    def _cargar(self):
        speak(_("Cargando emojis..."), interrupt=True)

        def _fin(res, err):
            if err:
                speak(_("No se pudieron cargar los emojis: {0}").format(err), interrupt=True)
                return
            self._emojis = res or []
            self._aplicar_filtro()
            if self._emojis:
                speak(_("{0} emojis. Escribe para filtrar o baja a la lista.").format(len(self._emojis)), interrupt=True)
            else:
                speak(_("Tu instancia no tiene emojis personalizados."), interrupt=True)
        mastodon_service.obtener_emojis_personalizados_async(self.account_id, callback=_fin)

    def _aplicar_filtro(self):
        q = self.txt_buscar.GetValue().strip().lower()
        if q:
            self._filtrados = [e for e in self._emojis
                               if q in e["shortcode"].lower() or q in (e.get("category") or "").lower()]
        else:
            self._filtrados = list(self._emojis)
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, e in enumerate(self._filtrados):
            self.lista.InsertItem(i, ":{0}:".format(e["shortcode"]))
            cat = e.get("category") or ""
            self.lista.SetItem(i, 1, cat)
            self.lista.establecer_lectura(i, ":{0}:{1}".format(e["shortcode"], (", " + cat) if cat else ""))
        if self._filtrados:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _on_buscar_key(self, event):
        # Flecha abajo desde el buscador: saltar a la lista de resultados.
        if event.GetKeyCode() == wx.WXK_DOWN and self.lista.GetItemCount() > 0:
            self.lista.SetFocus()
            self.lista.Focus(0)
            self.lista.Select(0)
        else:
            event.Skip()

    def _insertar(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self._filtrados):
            speak(_("Elige un emoji de la lista."), interrupt=True)
            return
        self.shortcode_elegido = self._filtrados[idx]["shortcode"]
        self.EndModal(wx.ID_OK)


def _borrador_a_json(borrador):
    """Copia JSON-serializable de un borrador de seguridad (la fecha de programación,
    wx.DateTime, pasa a texto ISO) para poder guardarlo como borrador persistente."""
    b = dict(borrador or {})
    pd = b.get("prog_date")
    if pd is not None and not isinstance(pd, str):
        try:
            b["prog_date"] = pd.FormatISODate()
        except Exception:
            b["prog_date"] = None
    return b


def _ofrecer_borrador_seguridad(parent, account_id, borrador, error_msg,
                                in_reply_to_id=None, quote_status_id=None, edit_status_db_id=None):
    """Tras un fallo al publicar o editar: muestra el mensaje del error y pregunta qué hacer con
    el BORRADOR DE SEGURIDAD (todo lo que iba a publicarse): reabrir el compositor para
    reintentar, guardarlo como borrador persistente (menú Mastodon → Borradores) o descartarlo.
    Se ejecuta en el hilo principal (callback de task_manager) y el diálogo compositor ya está
    cerrado, por eso recibe la ventana padre y el contexto por parámetros en vez de usar el
    diálogo original. Al fallar una EDICIÓN no se ofrece guardar borrador: un borrador se
    publicaría luego como toot NUEVO, no como edición del existente."""
    from app.core.net import humanizar_error
    from app.ui.mastodon_compose import ComposeTootDialog
    from .accessible_controls import elegir_opcion
    if edit_status_db_id:
        msg = _("No se pudo guardar la edición: {0}\n\n¿Qué quieres hacer con los cambios? "
                "(Cancelar los descarta.)").format(humanizar_error(error_msg))
        botones = [_("&Reabrir el compositor para reintentar"), _("&Descartar los cambios")]
    else:
        msg = _("No se pudo publicar: {0}\n\n¿Qué quieres hacer con lo que ibas a publicar "
                "(texto, adjuntos, encuesta...)? (Cancelar lo descarta.)").format(
                    humanizar_error(error_msg))
        botones = [_("&Reabrir el compositor para reintentar"),
                   _("&Guardar como borrador"), _("&Descartar")]
    r = elegir_opcion(parent, msg, _("No se pudo publicar"), botones)
    if not edit_status_db_id and r == 1:
        # Guardar como borrador persistente para retomarlo cuando se quiera.
        try:
            from app.services.mastodon import mastodon_borradores
            contexto = {}
            if in_reply_to_id:
                contexto["in_reply_to_id"] = in_reply_to_id
            if quote_status_id:
                contexto["quote_status_id"] = quote_status_id
            mastodon_borradores.guardar_borrador(
                account_id, (borrador or {}).get("texto", "").strip(),
                _borrador_a_json(borrador), contexto or None)
            from app.core.speech import notificar
            notificar(_("Borrador guardado. Puedes retomarlo en el menú Mastodon → Borradores."))
        except Exception as e:
            log.error("No se pudo guardar el borrador persistente: %s", e)
        return
    if r != 0:
        return  # El usuario descarta el borrador.
    try:
        nuevo = ComposeTootDialog(parent, account_id, in_reply_to_id=in_reply_to_id,
                                  quote_status_id=quote_status_id,
                                  edit_status_db_id=edit_status_db_id, borrador=borrador)
        nuevo.ShowModal()
        nuevo.Destroy()
    except Exception as e:
        log.error("No se pudo reabrir el borrador de seguridad: %s", e)
