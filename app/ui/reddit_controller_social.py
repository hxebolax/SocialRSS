# -*- coding: utf-8 -*-
"""Controlador de Reddit: mensajería, suscripciones, perfiles y búsqueda (mixin de UI).

Complementa a _AccionesMixin con los buffers y diálogos de la Tanda 5: bandeja de entrada
(mensajes), subreddits suscritos, multireddits, perfiles de usuario y búsqueda.
"""
import logging

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.privacy import abrir_url
from app.core.task_manager import task_manager, TareaDuplicadaError
from app.services.reddit import reddit_service

log = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"


class _SocialUIMixin:
    # ------------------------------------------------------------------
    # Bandeja de entrada (mensajes)
    # ------------------------------------------------------------------
    def detalle_mensaje(self, index):
        f = self.f
        db_id = self._db_id_de_index(index)
        if not db_id:
            return
        row = db.query_one("SELECT author, subject, body, context FROM reddit_messages WHERE id = ?",
                           (db_id,))
        if not row:
            return
        author, subject, body, context = row
        lineas = []
        if author:
            lineas.append(_("De u/{0}").format(author))
        if subject:
            lineas.append(subject)
        lineas.append("")
        lineas.append(body or "")
        try:
            f.txt_detalles.SetValue("\n".join(lineas))
        except Exception:
            pass

    def menu_mensaje(self, menu, idx):
        import wx
        db_id = self._db_id_de_index(idx)
        row = db.query_one("SELECT fullname, new, context, msg_type FROM reddit_messages WHERE id = ?",
                           (db_id,)) if db_id else None
        if not row:
            return
        fullname, new, context, msg_type = row

        def add(label, cb):
            it = menu.Append(wx.ID_ANY, label)
            self.f.Bind(wx.EVT_MENU, lambda e: cb(), it)

        if msg_type != "sent":
            add(_("Responder"), lambda: self.responder_mensaje(fullname))
            add(_("Marcar como leído") if new else _("Marcar como no leído"),
                lambda: self.marcar_leido_mensaje(fullname, bool(new)))
        if context:
            add(_("Ver el comentario en el navegador"), lambda: abrir_url(REDDIT_BASE + context))
        add(_("Marcar toda la bandeja como leída"), lambda: self.marcar_todo_leido())

    def responder_mensaje(self, fullname):
        from .accessible_controls import CustomTextEntryDialog
        import wx
        dlg = CustomTextEntryDialog(self.f, _("Escribe tu respuesta:"), _("Responder mensaje"),
                                    multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        speak(_("Enviando respuesta..."), interrupt=True)
        acc = self.f.active_reddit_account_id
        self._accion_async(lambda a: reddit_service.responder_mensaje(a, fullname, texto),
                           tras_exito=lambda r: speak(_("Respuesta enviada."), interrupt=True))

    def marcar_leido_mensaje(self, fullname, leido):
        acc = self.f.active_reddit_account_id
        self._accion_async(lambda a: reddit_service.marcar_leido(a, fullname, leido),
                           tras_exito=lambda r: (self._repintar_actual(),
                                                 speak(_("Hecho."), interrupt=True)))

    def marcar_todo_leido(self):
        acc = self.f.active_reddit_account_id
        self._accion_async(lambda a: reddit_service.marcar_todo_leido(a),
                           tras_exito=lambda r: (self._repintar_actual(),
                                                 speak(_("Bandeja marcada como leída."), interrupt=True)))

    def enviar_pm(self, destinatario=""):
        import wx
        from .reddit_dialogs import RedditMessageDialog
        dlg = RedditMessageDialog(self.f, destinatario=destinatario)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        dest, asunto, cuerpo = dlg.resultado
        dlg.Destroy()
        speak(_("Enviando mensaje..."), interrupt=True)
        self._accion_async(lambda a: reddit_service.enviar_pm(a, dest, asunto, cuerpo),
                           tras_exito=lambda r: notificar(_("Mensaje enviado a u/{0}.").format(dest)))

    # ------------------------------------------------------------------
    # Subreddits y multireddits
    # ------------------------------------------------------------------
    def menu_subreddit(self, menu, idx):
        import wx
        db_id = self._db_id_de_index(idx)
        row = db.query_one("SELECT name FROM reddit_subreddits WHERE id = ?", (db_id,)) if db_id else None
        if not row:
            return
        nombre = row[0]

        def add(label, cb):
            it = menu.Append(wx.ID_ANY, label)
            self.f.Bind(wx.EVT_MENU, lambda e: cb(), it)

        add(_("Abrir r/{0}").format(nombre), lambda: self.abrir_subreddit(nombre))
        add(_("Fijar en el árbol"), lambda: self.fijar_subreddit(nombre))
        add(_("Cancelar suscripción"), lambda: self.suscribirse_a(nombre, False))
        add(_("Abrir en el navegador"), lambda: abrir_url(f"{REDDIT_BASE}/r/{nombre}"))

    def abrir_subreddit(self, nombre):
        f = self.f
        f.active_reddit_type = "sub:" + nombre
        f._modo_lista = "reddit_posts"
        try:
            f.lbl_buffer_titulo.SetLabel("Reddit - " + _("r/{0}").format(nombre))
        except Exception:
            pass
        self.cargar_listado()

    def abrir_multireddit(self, index):
        db_id = self._db_id_de_index(index)
        if not db_id:
            return
        # Multireddit navegable: "multi:<dueño>:<nombre>" se abre como un feed más en la lista.
        if db_id.startswith("multi:"):
            f = self.f
            f.active_reddit_type = db_id
            f._modo_lista = "reddit_posts"
            visible = db_id.split(":", 2)[-1]
            try:
                f.lbl_buffer_titulo.SetLabel("Reddit - " + _("Multireddit: {0}").format(visible))
            except Exception:
                pass
            self.cargar_listado()
        else:
            # Caché antigua sin dueño/nombre: se abre en el navegador como antes.
            abrir_url(REDDIT_BASE + db_id if db_id.startswith("/") else f"{REDDIT_BASE}/{db_id}")

    def fijar_subreddit(self, nombre):
        from app.core.config_manager import config
        subs = list(config.get("reddit", "saved_subreddits", []))
        if any((s.get("name") if isinstance(s, dict) else s) == nombre for s in subs):
            speak(_("Ese subreddit ya está fijado."), interrupt=True)
            return
        subs.append({"name": nombre})
        config.set("reddit", "saved_subreddits", subs)
        self.f._cargar_navegacion()
        speak(_("r/{0} fijado en el árbol.").format(nombre), interrupt=True)

    def suscribirse_a(self, nombre, suscribir=True):
        acc = self.f.active_reddit_account_id
        msg = _("Suscrito a r/{0}.").format(nombre) if suscribir else _("Suscripción cancelada.")
        self._accion_async(lambda a: reddit_service.suscribirse(a, nombre, suscribir),
                           tras_exito=lambda r: (self._repintar_actual() if self._es_comentario_modo() is False else None,
                                                 speak(msg, interrupt=True)))

    def ir_a_subreddit(self):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Nombre del subreddit (sin r/):"), _("Ir a un subreddit"))
        if dlg.ShowModal() == wx.ID_OK:
            nombre = dlg.GetValue().strip().lstrip("r/").lstrip("/")
            if nombre:
                self.abrir_subreddit(nombre)
        dlg.Destroy()

    # ------------------------------------------------------------------
    # Perfil de usuario
    # ------------------------------------------------------------------
    def ver_perfil(self, username):
        acc = self.f.active_reddit_account_id
        speak(_("Cargando perfil..."), interrupt=True)

        def _bg(ctx):
            return reddit_service._cargar_perfil_sync(acc, username)

        def _fin(p, err):
            if err:
                notificar(_("No se pudo cargar el perfil: {0}").format(err))
                return
            from .accessible_controls import mostrar_info
            from app.core.plantillas import definicion, pulir_lectura
            from collections import defaultdict
            from datetime import datetime, timezone
            created = ""
            if p.get("created_utc"):
                try:
                    created = _("cuenta creada el {0}").format(
                        datetime.fromtimestamp(p["created_utc"], timezone.utc).strftime("%d/%m/%Y"))
                except Exception:
                    pass
            valores = {"name": _("u/{0}").format(p.get("name", "")),
                       "post_karma": str(p.get("post_karma", 0)),
                       "comment_karma": str(p.get("comment_karma", 0)),
                       "created": created, "bio": p.get("bio", "")}
            plantilla = definicion("reddit_person")["default"]
            texto = pulir_lectura(plantilla.format_map(defaultdict(str, valores)))
            mostrar_info(self.f, texto, _("Perfil de u/{0}").format(p.get("name", "")))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------
    def buscar_publicaciones(self):
        import wx
        from .accessible_controls import CustomTextEntryDialog, confirmar
        dlg = CustomTextEntryDialog(self.f, _("Texto a buscar en Reddit:"), _("Buscar publicaciones"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        # Abrir como buffer de búsqueda y descargar.
        f = self.f
        f.active_reddit_type = "search:" + texto
        f._modo_lista = "reddit_posts"
        try:
            f.lbl_buffer_titulo.SetLabel("Reddit - " + _("Búsqueda: {0}").format(texto))
        except Exception:
            pass
        self.cargar_listado()
        # Ofrecer guardarla como buffer fijo.
        if confirmar(f, _("¿Guardar esta búsqueda como buffer en el árbol?"),
                     _("Guardar búsqueda")) == wx.ID_YES:
            from app.core.config_manager import config
            busquedas = list(config.get("reddit", "saved_searches", []))
            if texto not in busquedas:
                busquedas.append(texto)
                config.set("reddit", "saved_searches", busquedas)
                f._cargar_navegacion()

    def buscar_subreddits_dialogo(self):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Nombre o tema del subreddit:"), _("Buscar subreddits"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        query = dlg.GetValue().strip()
        dlg.Destroy()
        if not query:
            return
        acc = self.f.active_reddit_account_id
        speak(_("Buscando subreddits..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.buscar_subreddits(acc, query)

        def _fin(res, err):
            if err:
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            if not res:
                speak(_("No se encontraron subreddits."), interrupt=True)
                return
            import wx
            etiquetas = [_("r/{0} — {1} suscriptores").format(s["name"], s.get("subscribers", 0))
                         for s in res]
            d = wx.SingleChoiceDialog(self.f, _("Elige un subreddit para abrirlo:"),
                                      _("Resultados de subreddits"), etiquetas)
            if d.ShowModal() == wx.ID_OK:
                self.abrir_subreddit(res[d.GetSelection()]["name"])
            d.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def buscar_usuarios_dialogo(self):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Nombre de usuario a buscar:"), _("Buscar usuarios"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        query = dlg.GetValue().strip()
        dlg.Destroy()
        if not query:
            return
        acc = self.f.active_reddit_account_id
        speak(_("Buscando usuarios..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.buscar_usuarios(acc, query)

        def _fin(res, err):
            if err:
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            if not res:
                speak(_("No se encontraron usuarios."), interrupt=True)
                return
            import wx
            etiquetas = [_("u/{0}").format(u["name"]) for u in res]
            d = wx.SingleChoiceDialog(self.f, _("Elige un usuario para ver su perfil:"),
                                      _("Resultados de usuarios"), etiquetas)
            if d.ShowModal() == wx.ID_OK:
                self.ver_perfil(res[d.GetSelection()]["name"])
            d.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
