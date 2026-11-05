# -*- coding: utf-8 -*-
"""Centro de notificaciones unificado: reúne en una sola lista lo que te interpela en TODAS las
redes —menciones, respuestas, me gusta, reposts y seguidores de Mastodon, Bluesky y X, más los
chats de Telegram con mensajes sin leer—, ordenado por fecha.

Es un complemento a la Bandeja de entrada (que agrega PUBLICACIONES): aquí van las
NOTIFICACIONES/menciones y Telegram, que la bandeja deja fuera a propósito. Trabaja con datos ya
cacheados en la base (sin peticiones de red al abrir) y, al activar un elemento, abre lo relevante
de esa red.
"""
import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.ui.accessible_controls import AccessibleListCtrl
from app.ui.ui_helpers import formatear_tiempo_relativo

try:
    _
except NameError:
    _ = lambda x: x


class NotifCenterDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Centro de notificaciones (todas las redes)"), size=(680, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Centro de notificaciones"))
        self.frame = parent
        self.items = []
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Todo lo que te menciona o interpela, de todas tus redes:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de notificaciones unificada"))
        self.lista.InsertColumn(0, _("Red"), width=90)
        self.lista.InsertColumn(1, _("Tipo"), width=120)
        self.lista.InsertColumn(2, _("De"), width=170)
        self.lista.InsertColumn(3, _("Contenido"), width=230)
        self.lista.InsertColumn(4, _("Cuándo"), width=110)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_abrir)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_abrir = wx.Button(self, label=_("&Abrir (Intro)"))
        b_abrir.Bind(wx.EVT_BUTTON, self._on_abrir)
        bs.Add(b_abrir, 0, wx.ALL, 5)
        b_ref = wx.Button(self, label=_("&Actualizar"))
        b_ref.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        bs.Add(b_ref, 0, wx.ALL, 5)
        bs.AddStretchSpacer()
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALL, 5)
        s.Add(bs, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(s)

    def _cargar(self):
        speak(_("Cargando notificaciones..."), interrupt=True)

        def _bg(ctx):
            from app.core.centro_notificaciones import reunir
            return reunir()

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar las notificaciones: {0}").format(err))
                return
            self.items = res or []
            self.lista.DeleteAllItems()
            for i, it in enumerate(self.items):
                self.lista.InsertItem(i, it["red"])
                self.lista.SetItem(i, 1, it["tipo"])
                self.lista.SetItem(i, 2, it["de"])
                self.lista.SetItem(i, 3, it["texto"])
                rel = formatear_tiempo_relativo(it["fecha"]) if it["fecha"] else ""
                self.lista.SetItem(i, 4, rel)
            if self.items:
                self.lista.Focus(0)
                self.lista.Select(0)
                speak(_("{0} notificaciones.").format(len(self.items)), interrupt=True)
            else:
                speak(_("No tienes notificaciones nuevas."), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_abrir(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.items):
            return
        destino = self.items[idx].get("abrir")
        if not destino:
            return
        red = destino[0]
        try:
            if red == "telegram":
                _r, acc, chat_id, titulo = destino
                from .telegram_views import TelegramConversationDialog
                d = TelegramConversationDialog(self, acc, chat_id, titulo or "")
                d.ShowModal()
                d.Destroy()
            elif red == "matrix":
                _r, acc, room_id, nombre = destino
                from .matrix_conversation import MatrixConversationDialog
                d = MatrixConversationDialog(self, acc, room_id, nombre or room_id)
                d.ShowModal()
                d.Destroy()
            elif red == "wsocial_convo":
                _r, acc, convo_id, titulo = destino
                from .wsocial_dialogs import WSocialConversationDialog
                d = WSocialConversationDialog(self, acc, convo_id, titulo or "")
                d.ShowModal()
                d.Destroy()
            elif red == "wsocial":
                # Lleva al buffer de notificaciones de esa cuenta.
                _r, acc, buffer_tipo, _t = destino
                ctrl = getattr(self.frame, "wsocial_ctrl", None)
                if ctrl is not None:
                    self.frame.active_wsocial_account_id = acc
                    self.frame.active_timeline_type = buffer_tipo
                    ctrl.cargar_listado()
                    self.EndModal(wx.ID_CANCEL)
                else:
                    notificar(_("Abre W Social para ver esta notificación."))
            elif red == "irc":
                _r, acc, canal = destino
                fila = db.query_one("SELECT es_privado FROM irc_channels WHERE account_id = ? AND name = ?",
                                    (acc, (canal or "").lower()))
                from .irc_conversation import IRCConversationDialog
                d = IRCConversationDialog(self, acc, canal, es_privado=bool(fila and fila[0]))
                d.ShowModal()
                d.Destroy()
            elif red == "xmpp":
                _r, acc, jid, nombre, es_muc = destino
                from .xmpp_conversation import XMPPConversationDialog
                d = XMPPConversationDialog(self, acc, jid, nombre or jid, es_muc=bool(es_muc))
                d.ShowModal()
                d.Destroy()
            elif red == "bluesky" and destino[2]:
                # Visor de conversación de la publicación aludida por la notificación.
                _r, acc, uri = destino
                from .bluesky_thread_viewer import BlueskyThreadViewerDialog
                d = BlueskyThreadViewerDialog(self, acc, uri)
                d.ShowModal()
                d.Destroy()
            elif red == "mastodon" and destino[2]:
                # Abrir el diálogo Ver publicación de Mastodon a partir del db_id "cuenta:status_id".
                _r, acc, status_id = destino
                db_id = f"{acc}:{status_id}"
                if hasattr(self.frame, "_on_ver_publicacion"):
                    self.frame._on_ver_publicacion(db_id)
                else:
                    notificar(_("Abre Mastodon para ver esta publicación."))
            else:
                notificar(_("Ve al buffer de {0} para ver el detalle.").format(self.items[idx]["red"]))
        except Exception as e:
            notificar(_("No se pudo abrir: {0}").format(e))
