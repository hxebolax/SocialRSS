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
            from app.core.html_utils import html_a_texto
            items = []
            # Mastodon: todas las notificaciones cacheadas.
            _tipos_mast = {"mention": _("Mención"), "reblog": _("Impulso"), "favourite": _("Favorito"),
                           "follow": _("Seguidor"), "follow_request": _("Solicitud"), "poll": _("Encuesta"),
                           "status": _("Nueva publicación"), "update": _("Editada"), "quote": _("Cita")}
            try:
                for r in db.query(
                        "SELECT n.account_id, n.type, n.created_at, n.from_name, n.from_username, "
                        "n.status_id, s.content FROM mastodon_notifications n "
                        "LEFT JOIN mastodon_statuses s ON s.status_id = n.status_id "
                        "ORDER BY n.created_at DESC LIMIT 100"):
                    acc, tipo, fecha, nombre, usuario, status_id, contenido = r
                    items.append({
                        "red": _("Mastodon"), "tipo": _tipos_mast.get(tipo, tipo or ""),
                        "de": nombre or usuario or "", "texto": html_a_texto(contenido or "")[:200],
                        "fecha": fecha or "", "abrir": ("mastodon", acc, status_id)})
            except Exception:
                pass
            # Bluesky: notificaciones cacheadas.
            _tipos_bsky = {"mention": _("Mención"), "reply": _("Respuesta"), "like": _("Me gusta"),
                           "repost": _("Repost"), "follow": _("Seguidor"), "quote": _("Cita")}
            try:
                from app.services.bluesky.bluesky_common import es_uri_de_post, uri_post_de_notificacion
                for r in db.query(
                        "SELECT account_id, reason, indexed_at, author_name, author_handle, "
                        "subject_uri, reason_subject, uri, content "
                        "FROM bluesky_notifications ORDER BY indexed_at DESC LIMIT 100"):
                    acc, reason, fecha, nombre, handle, subj, motivo, uri, texto = r
                    # Solo se abre si es de verdad una PUBLICACIÓN: en filas antiguas (sin
                    # subject_uri) el reasonSubject de un "me gusta vía repost" es el repost, y
                    # el de un starter pack, el pack; pedir su hilo daba 400 NotFound.
                    if not es_uri_de_post(subj):
                        subj = uri_post_de_notificacion(reason or "", uri or "", motivo or "")
                    items.append({
                        "red": _("Bluesky"), "tipo": _tipos_bsky.get(reason, reason or ""),
                        "de": nombre or handle or "", "texto": (texto or "")[:200],
                        "fecha": fecha or "", "abrir": ("bluesky", acc, subj)})
            except Exception:
                pass
            # X: menciones cacheadas (buffer de menciones -> x_timeline_tweets con timeline
            # 'mentions_tl'; se une con x_tweets para el texto y el autor).
            try:
                for r in db.query(
                        "SELECT t.account_id, t.created_at, t.author_name, t.author_screen_name, "
                        "t.text, t.tweet_id FROM x_tweets t "
                        "JOIN x_timeline_tweets tt ON tt.tweet_db_id = t.id "
                        "WHERE tt.timeline IN ('mentions', 'mentions_tl') "
                        "ORDER BY t.created_at DESC LIMIT 50"):
                    acc, fecha, nombre, screen, texto, tid = r
                    items.append({
                        "red": _("X"), "tipo": _("Mención"), "de": nombre or screen or "",
                        "texto": (texto or "")[:200], "fecha": fecha or "",
                        "abrir": ("x", acc, tid)})
            except Exception:
                pass
            # Telegram: chats con mensajes sin leer.
            try:
                for r in db.query(
                        "SELECT account_id, chat_id, title, unread_count, last_message, last_message_date "
                        "FROM telegram_chats WHERE COALESCE(unread_count,0) > 0 "
                        "ORDER BY last_message_date DESC LIMIT 100"):
                    acc, chat_id, titulo, unread, ultimo, fecha = r
                    items.append({
                        "red": _("Telegram"), "tipo": _("{0} sin leer").format(unread),
                        "de": titulo or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                        "abrir": ("telegram", acc, chat_id, titulo)})
            except Exception:
                pass
            # W Social: notificaciones sin leer y conversaciones con mensajes pendientes.
            try:
                for r in db.query(
                        "SELECT account_id, reason, author_name, author_handle, texto, indexed_at "
                        "FROM wsocial_notifications WHERE COALESCE(is_read,0) = 0 "
                        "ORDER BY indexed_at DESC LIMIT 100"):
                    acc, motivo, autor, handle, texto, fecha = r
                    from app.services.wsocial.wsocial_common import MOTIVOS_NOTIFICACION
                    items.append({
                        "red": _("W Social"),
                        "tipo": MOTIVOS_NOTIFICACION.get(motivo, motivo),
                        "de": autor or handle or "", "texto": (texto or "")[:200],
                        "fecha": fecha or "", "abrir": ("wsocial", acc, "notifications", "")})
            except Exception:
                pass
            try:
                for r in db.query(
                        "SELECT account_id, convo_id, title, unread_count, last_message, "
                        "last_message_date FROM wsocial_convos WHERE COALESCE(unread_count,0) > 0 "
                        "ORDER BY last_message_date DESC LIMIT 50"):
                    acc, convo_id, titulo, unread, ultimo, fecha = r
                    items.append({
                        "red": _("W Social"), "tipo": _("{0} sin leer").format(unread),
                        "de": titulo or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                        "abrir": ("wsocial_convo", acc, convo_id, titulo)})
            except Exception:
                pass
            # Matrix: directos con no leídos e invitaciones pendientes.
            try:
                for r in db.query(
                        "SELECT account_id, room_id, nombre, unread_count, last_message, last_message_date "
                        "FROM matrix_salas WHERE es_dm = 1 AND COALESCE(unread_count,0) > 0 "
                        "ORDER BY last_message_date DESC LIMIT 50"):
                    acc, room_id, nombre, unread, ultimo, fecha = r
                    items.append({
                        "red": _("Matrix"), "tipo": _("{0} sin leer").format(unread),
                        "de": nombre or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                        "abrir": ("matrix", acc, room_id, nombre)})
                for r in db.query(
                        "SELECT account_id, room_id, nombre FROM matrix_salas "
                        "WHERE invitacion = 1 ORDER BY nombre LIMIT 50"):
                    acc, room_id, nombre = r
                    items.append({
                        "red": _("Matrix"), "tipo": _("Invitación"),
                        "de": nombre or "", "texto": _("Tienes una invitación pendiente."),
                        "fecha": "", "abrir": ("matrix", acc, room_id, nombre)})
            except Exception:
                pass
            # IRC: menciones a tu apodo sin leer y privados pendientes.
            try:
                for r in db.query(
                        "SELECT account_id, channel, nick, text, date FROM irc_messages "
                        "WHERE highlight = 1 AND leido = 0 ORDER BY id DESC LIMIT 50"):
                    acc, canal, nick, texto, fecha = r
                    items.append({
                        "red": _("IRC"), "tipo": _("Mención en {0}").format(canal),
                        "de": nick or "", "texto": (texto or "")[:200], "fecha": fecha or "",
                        "abrir": ("irc", acc, canal)})
                for r in db.query(
                        "SELECT account_id, name, unread_count, last_message, last_message_date "
                        "FROM irc_channels WHERE es_privado = 1 AND COALESCE(unread_count,0) > 0 "
                        "ORDER BY last_message_date DESC LIMIT 50"):
                    acc, nombre, unread, ultimo, fecha = r
                    items.append({
                        "red": _("IRC"), "tipo": _("{0} privados sin leer").format(unread),
                        "de": nombre or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                        "abrir": ("irc", acc, nombre)})
            except Exception:
                pass
            # XMPP (Jabber): contactos con mensajes sin leer, menciones en salas e invitaciones.
            try:
                for r in db.query(
                        "SELECT account_id, jid, nombre, unread_count, last_message, last_message_date, tipo "
                        "FROM xmpp_chats WHERE COALESCE(unread_count,0) > 0 AND COALESCE(solicitud,0) = 0 "
                        "ORDER BY last_message_date DESC LIMIT 100"):
                    acc, jid, nombre, unread, ultimo, fecha, tipo = r
                    es_muc = (tipo == "muc")
                    items.append({
                        "red": _("XMPP"),
                        "tipo": (_("{0} sin leer en la sala").format(unread) if es_muc
                                 else _("{0} sin leer").format(unread)),
                        "de": nombre or jid or "", "texto": (ultimo or "")[:200], "fecha": fecha or "",
                        "abrir": ("xmpp", acc, jid, nombre or jid, es_muc)})
                for r in db.query(
                        "SELECT account_id, jid, nombre, tipo FROM xmpp_chats "
                        "WHERE COALESCE(solicitud,0) = 1 ORDER BY nombre LIMIT 50"):
                    acc, jid, nombre, tipo = r
                    es_muc = (tipo == "muc")
                    items.append({
                        "red": _("XMPP"),
                        "tipo": _("Invitación a sala") if es_muc else _("Solicitud de contacto"),
                        "de": nombre or jid or "",
                        "texto": (_("Tienes una invitación a una sala.") if es_muc
                                  else _("Alguien quiere agregarte.")),
                        "fecha": "", "abrir": ("xmpp", acc, jid, nombre or jid, es_muc)})
            except Exception:
                pass
            # Ordenar todo por fecha descendente (cadenas ISO comparables; las no ISO al final).
            items.sort(key=lambda x: str(x.get("fecha") or ""), reverse=True)
            return items

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
