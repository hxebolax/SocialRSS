# -*- coding: utf-8 -*-
"""
Diálogo de BÚSQUEDA GLOBAL en texto completo (FTS5) sobre todo el contenido
local: publicaciones de Mastodon y artículos de RSS de todas las cuentas.

Los resultados son navegables y se pueden abrir con Intro (artículos en el visor
limpio; toots en el navegador).
"""

import wx
from app.core.privacy import abrir_url
from app.core.speech import speak, notificar
from app.core.database import db
from app.core.task_manager import task_manager
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service
from app.core.html_utils import html_a_texto
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, mostrar_info


class GlobalSearchDialog(wx.Dialog):
    """Busca en todos los toots y artículos guardados con FTS5."""

    def __init__(self, parent, query=None, solo_rss=False):
        super().__init__(parent, title=_("Buscar en todo el contenido"), size=(720, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Diálogo de búsqueda global"))
        self.main_frame = parent
        self.resultados = []
        self._init_ui()
        self.CenterOnParent()
        # Prefijar una búsqueda (p. ej. desde una búsqueda guardada de RSS) y lanzarla.
        if solo_rss:
            self.choice_ambito.SetSelection(3)  # Solo RSS
        if query:
            self.txt_query.SetValue(query)
            wx.CallAfter(self._on_buscar, None)

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Buscar:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_query = AccessibleTextCtrl(
            self, style=wx.TE_PROCESS_ENTER, name=_("Texto a buscar"),
            ayuda_texto=_("Escriba palabras a buscar y pulse Intro o el botón Buscar.")
        )
        self.txt_query.Bind(wx.EVT_TEXT_ENTER, self._on_buscar)
        fila.Add(self.txt_query, 1, wx.EXPAND | wx.ALL, 5)
        self.btn_buscar = wx.Button(self, label=_("&Buscar"))
        self.btn_buscar.Bind(wx.EVT_BUTTON, self._on_buscar)
        fila.Add(self.btn_buscar, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)

        # Filtro por servicio
        self.choice_ambito = wx.Choice(
            self, choices=[_("Todos"), _("Solo Mastodon"), _("Solo Bluesky"), _("Solo RSS"),
                           _("Solo Telegram"), _("Solo X (Twitter)"), _("Solo IRC"),
                           _("Solo Matrix"), _("Solo XMPP"), _("Solo W Social")],
            name=_("Ámbito de búsqueda")
        )
        self.choice_ambito.SetSelection(0)
        s.Add(self.choice_ambito, 0, wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Resultados:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de resultados de la búsqueda")
        )
        self.lista.InsertColumn(0, _("Servicio"), width=90)
        self.lista.InsertColumn(1, _("Origen"), width=160)
        self.lista.InsertColumn(2, _("Contenido"), width=330)
        self.lista.InsertColumn(3, _("Fecha"), width=110)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_abrir)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu_resultado)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_abrir = wx.Button(self, label=_("&Abrir resultado"))
        self.btn_abrir.Bind(wx.EVT_BUTTON, self._on_abrir)
        btns.Add(self.btn_abrir, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        self.txt_query.SetFocus()

    @staticmethod
    def _cuentas(service):
        try:
            return [r[0] for r in db.query("SELECT id FROM accounts WHERE service_id = ?", (service,))]
        except Exception:
            return []

    def _on_buscar(self, event):
        query = self.txt_query.GetValue().strip()
        if not query:
            speak(_("Escriba algo que buscar."), interrupt=True)
            return
        # 0=todos, 1=mastodon, 2=bluesky, 3=rss, 4=telegram, 5=X
        ambito = self.choice_ambito.GetSelection()
        speak(_("Buscando..."), interrupt=True)
        self.btn_buscar.Disable()

        from app.core.feature_flags import servicio_habilitado

        def _bg(ctx):
            res = []
            # Un servicio DESACTIVADO no se busca, ni siquiera con el ámbito "Todos".
            if ambito in (0, 1) and servicio_habilitado("mastodon"):
                for acc in self._cuentas("mastodon"):
                    for st in mastodon_service.buscar_statuses_fts(acc, query, limit=100):
                        res.append({
                            "service": "mastodon", "db_id": st.id,
                            "origen": getattr(st, "author_name", "") or "",
                            "texto": html_a_texto(getattr(st, "content", "") or ""),
                            "fecha": str(getattr(st, "created_at", "") or ""),
                        })
            if ambito in (0, 2) and servicio_habilitado("bluesky"):
                from app.services.bluesky import bluesky_service
                for acc in self._cuentas("bluesky"):
                    for po in bluesky_service.buscar_posts_fts(acc, query, limit=100):
                        res.append({
                            "service": "bluesky", "db_id": po.id,
                            "origen": getattr(po, "author_name", "") or "",
                            "texto": (getattr(po, "content", "") or ""),
                            "fecha": str(getattr(po, "indexed_at", "") or ""),
                        })
            # W Social: se busca en lo YA descargado (índice de texto completo local), como en
            # el resto de redes; la búsqueda global nunca depende de la red.
            if ambito in (0, 9) and servicio_habilitado("wsocial"):
                from app.services.wsocial import wsocial_service
                for fila in wsocial_service.buscar_local(query, limite=100):
                    res.append({
                        "service": "wsocial", "db_id": fila[0],
                        "origen": fila[1] or fila[2] or "",
                        "texto": fila[3] or "",
                        "fecha": str(fila[4] or ""),
                    })
            if ambito in (0, 3) and servicio_habilitado("rss"):
                for acc in self._cuentas("rss"):
                    for art in rss_service.buscar_articulos_fts(acc, query, limit=100):
                        res.append({
                            "service": "rss", "db_id": art.id,
                            "origen": getattr(art, "author", "") or "",
                            "texto": (getattr(art, "title", "") or ""),
                            "fecha": str(getattr(art, "published_at", "") or ""),
                        })
            # Telegram y X no tienen FTS; se buscan con LIKE sobre el texto cacheado.
            like = f"%{query}%"
            if ambito in (0, 4) and servicio_habilitado("telegram"):
                for acc in self._cuentas("telegram"):
                    for row in db.query(
                            "SELECT id, sender_name, text, date, chat_id FROM telegram_messages "
                            "WHERE account_id = ? AND text LIKE ? ORDER BY date DESC LIMIT 100",
                            (acc, like)):
                        res.append({
                            "service": "telegram", "db_id": row[0], "chat_id": row[4],
                            "origen": row[1] or "", "texto": row[2] or "", "fecha": str(row[3] or ""),
                        })
            if ambito in (0, 5) and servicio_habilitado("x"):
                for acc in self._cuentas("x"):
                    for row in db.query(
                            "SELECT id, author_name, author_screen_name, text, created_at, tweet_id "
                            "FROM x_tweets WHERE account_id = ? AND text LIKE ? ORDER BY created_at DESC LIMIT 100",
                            (acc, like)):
                        res.append({
                            "service": "x", "db_id": row[0], "screen": row[2] or "", "tweet_id": row[5],
                            "origen": row[1] or row[2] or "", "texto": row[3] or "", "fecha": str(row[4] or ""),
                        })
            if ambito in (0, 7) and servicio_habilitado("matrix"):
                from app.services.matrix import matrix_service
                for acc in self._cuentas("matrix"):
                    for m in matrix_service.buscar_mensajes_fts(acc, query, limit=100):
                        res.append({
                            "service": "matrix", "db_id": m["db_id"], "account_id": acc,
                            "room_id": m["room_id"], "origen": m["quien"] or "",
                            "texto": m["texto"] or "", "fecha": str(m["date"] or ""),
                        })
            if ambito in (0, 6) and servicio_habilitado("irc"):
                from app.services.irc import irc_service
                for acc in self._cuentas("irc"):
                    for m in irc_service.buscar_mensajes_fts(acc, query, limit=100):
                        res.append({
                            "service": "irc", "db_id": m["db_id"], "account_id": acc,
                            "channel": m["channel"],
                            "origen": f"{m['nick']} ({m['channel']})" if m.get("nick") else m["channel"],
                            "texto": m["text"] or "", "fecha": str(m["date"] or ""),
                        })
            if ambito in (0, 8) and servicio_habilitado("xmpp"):
                from app.services.xmpp.xmpp_service import xmpp_service
                for acc in self._cuentas("xmpp"):
                    for m in xmpp_service.buscar_mensajes_fts(acc, query, limit=100):
                        res.append({
                            "service": "xmpp", "db_id": m["db_id"], "account_id": acc,
                            "chat_jid": m["chat_jid"], "origen": m["quien"] or m["chat_jid"],
                            "texto": m["texto"] or "", "fecha": str(m["date"] or ""),
                        })
            return res

        def _finish(res, err):
            self.btn_buscar.Enable()
            if err:
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            self._mostrar(res or [])

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _mostrar(self, resultados):
        self.resultados = resultados
        self.lista.DeleteAllItems()
        from .main_frame import formatear_tiempo_relativo
        etiquetas = {"mastodon": _("Mastodon"), "bluesky": _("Bluesky"), "rss": _("RSS"),
                     "telegram": _("Telegram"), "x": _("X"), "irc": _("IRC"),
                     "matrix": _("Matrix"), "xmpp": _("XMPP")}
        for i, r in enumerate(resultados):
            pos = self.lista.InsertItem(i, etiquetas.get(r["service"], r["service"]))
            self.lista.SetItem(pos, 1, r["origen"])
            self.lista.SetItem(pos, 2, (r["texto"] or "").replace("\n", " "))
            try:
                self.lista.SetItem(pos, 3, formatear_tiempo_relativo(r["fecha"]) if r["fecha"] else "")
            except Exception:
                self.lista.SetItem(pos, 3, r["fecha"] or "")
        if resultados:
            speak(_("{0} resultados encontrados.").format(len(resultados)), interrupt=True)
            self.lista.Focus(0)
            self.lista.Select(0)
        else:
            speak(_("Sin resultados."), interrupt=True)

    def _on_menu_resultado(self, event):
        """Menú contextual de un resultado: en Matrix, 'ver en su contexto' (mensajes alrededor)."""
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.resultados):
            return
        r = self.resultados[idx]
        if r.get("service") != "matrix":
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Ver este mensaje en su contexto..."))
        self.lista.Bind(wx.EVT_MENU, lambda e, rr=r: self._ver_contexto_matrix(rr), it)
        self.lista.PopupMenu(menu)
        menu.Destroy()

    def _ver_contexto_matrix(self, r):
        """Muestra los mensajes de alrededor del resultado (room_context)."""
        from app.core.database import db as _db
        fila = _db.query_one("SELECT event_id, room_id FROM matrix_mensajes WHERE id = ?",
                             (r["db_id"],))
        if not fila or not fila[0]:
            notificar(_("No se puede localizar ese mensaje."))
            return
        acc, event_id, room_id = r.get("account_id"), fila[0], fila[1]
        speak(_("Cargando el contexto..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.contexto_evento(acc, room_id, event_id)

        def _fin(filas, err):
            if err:
                notificar(_("No se pudo cargar el contexto: {0}").format(err))
                return
            lineas = []
            for f in (filas or []):
                hora = (f.get("fecha") or "")[11:16]
                marca = ">>> " if f.get("es_el_buscado") else ""
                lineas.append(f"{marca}[{hora}] {f.get('quien')}: {f.get('texto')}")
            texto = "\n".join(lineas) or _("Sin contexto disponible.")
            try:
                self.main_frame._mostrar_texto_largo(_("Contexto del mensaje"), texto)
            except Exception:
                mostrar_info(self, texto, _("Contexto del mensaje"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_abrir(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.resultados):
            return
        r = self.resultados[idx]
        if r["service"] == "rss":
            try:
                self.main_frame._on_ver_articulo_limpio(r["db_id"])
            except Exception as e:
                notificar(_("No se pudo abrir el artículo: {0}").format(e))
        elif r["service"] == "bluesky":
            try:
                from app.core.database import db as _db
                row = _db.query_one("SELECT author_handle, uri FROM bluesky_posts WHERE id = ?", (r["db_id"],))
                if row and row[1]:
                    rkey = row[1].rsplit("/", 1)[-1]
                    abrir_url(f"https://bsky.app/profile/{row[0]}/post/{rkey}")
            except Exception as e:
                notificar(_("No se pudo abrir la publicación: {0}").format(e))
        elif r["service"] == "x":
            # Abrir el tweet en el navegador (tienen URL pública).
            try:
                screen = r.get("screen") or "i"
                abrir_url(f"https://x.com/{screen}/status/{r.get('tweet_id')}")
            except Exception as e:
                notificar(_("No se pudo abrir la publicación: {0}").format(e))
        elif r["service"] == "matrix":
            # Abrir la conversación de Matrix donde está el mensaje.
            try:
                from .matrix_conversation import MatrixConversationDialog
                acc, room_id = r.get("account_id"), r.get("room_id")
                fila = db.query_one("SELECT nombre FROM matrix_salas WHERE account_id = ? AND room_id = ?",
                                    (acc, room_id))
                d = MatrixConversationDialog(self.main_frame, acc, room_id,
                                             (fila[0] if fila else room_id))
                d.ShowModal()
                d.Destroy()
            except Exception as e:
                notificar(_("No se pudo abrir la conversación: {0}").format(e))
        elif r["service"] == "xmpp":
            # Abrir la conversación de XMPP donde está el mensaje.
            try:
                from .xmpp_conversation import XMPPConversationDialog
                acc, jid = r.get("account_id"), r.get("chat_jid")
                fila = db.query_one("SELECT nombre FROM xmpp_chats WHERE account_id = ? AND jid = ?",
                                    (acc, jid))
                d = XMPPConversationDialog(self.main_frame, acc, jid, (fila[0] if fila else jid))
                d.ShowModal()
                d.Destroy()
            except Exception as e:
                notificar(_("No se pudo abrir la conversación: {0}").format(e))
        elif r["service"] == "irc":
            # Abrir la conversación de IRC donde está el mensaje.
            try:
                from .irc_conversation import IRCConversationDialog
                acc, canal = r.get("account_id"), r.get("channel")
                fila = db.query_one("SELECT es_privado FROM irc_channels WHERE account_id = ? AND name = ?",
                                    (acc, (canal or "").lower()))
                d = IRCConversationDialog(self.main_frame, acc, canal, es_privado=bool(fila and fila[0]))
                d.ShowModal()
                d.Destroy()
            except Exception as e:
                notificar(_("No se pudo abrir la conversación: {0}").format(e))
        elif r["service"] == "telegram":
            # Abrir la conversación de Telegram donde está el mensaje.
            try:
                from .telegram_views import TelegramConversationDialog
                cuenta = self.main_frame.active_telegram_account_id or None
                if cuenta is None:
                    row = db.query_one("SELECT account_id FROM telegram_messages WHERE id = ?", (r["db_id"],))
                    cuenta = row[0] if row else None
                chat_id = r.get("chat_id")
                if cuenta and chat_id is not None:
                    fila = db.query_one("SELECT title FROM telegram_chats WHERE id = ?", (f"{cuenta}:{chat_id}",))
                    titulo = fila[0] if fila else str(chat_id)
                    d = TelegramConversationDialog(self.main_frame, cuenta, chat_id, titulo)
                    d.ShowModal()
                    d.Destroy()
            except Exception as e:
                notificar(_("No se pudo abrir la conversación: {0}").format(e))
        else:
            try:
                self.main_frame._on_abrir_toot_navegador(r["db_id"])
            except Exception as e:
                notificar(_("No se pudo abrir la publicación: {0}").format(e))
