# -*- coding: utf-8 -*-
"""Controlador de Reddit: selección de fila, panel de detalles y acciones (mixin).

Acciones sobre la publicación o comentario enfocado (votar, guardar, ocultar, responder, editar,
borrar, NSFW/spoiler, flair), el menú contextual, la botonera y las acciones del visor de hilo.
Toda la red va por task_manager; tras cada acción se repinta el buffer desde la caché ya actualizada.
"""
import logging

from app.core.database import db
from app.core.speech import speak, notificar, copiar_al_portapapeles
from app.core.privacy import abrir_url
from app.core.task_manager import task_manager, TareaDuplicadaError
from app.services.reddit import reddit_service

log = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"


class _AccionesMixin:
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _db_id_de_index(self, index):
        f = self.f
        if index is None or index < 0 or index >= len(f.lista_items_db_ids):
            return None
        return f.lista_items_db_ids[index]

    @staticmethod
    def _fullname_de(db_id):
        return db_id.split(":", 1)[1] if db_id and ":" in db_id else db_id

    def _es_comentario_modo(self):
        return str(getattr(self.f, "_modo_lista", "")) == "reddit_comments"

    def _datos_fila(self, db_id):
        """Devuelve un dict con los datos de la fila (post o comentario) para decidir el menú."""
        if not db_id:
            return None
        if self._es_comentario_modo():
            row = db.query_one("SELECT author, saved, likes, body FROM reddit_comments WHERE id = ?",
                               (db_id,))
            if not row:
                return None
            return {"tipo": "comment", "author": row[0], "saved": row[1], "likes": row[2],
                    "body": row[3], "fullname": self._fullname_de(db_id)}
        row = db.query_one("SELECT author, saved, likes, hidden, over_18, spoiler, is_self, permalink "
                           "FROM reddit_posts WHERE id = ?", (db_id,))
        if not row:
            return None
        return {"tipo": "post", "author": row[0], "saved": row[1], "likes": row[2], "hidden": row[3],
                "over_18": row[4], "spoiler": row[5], "is_self": row[6], "permalink": row[7],
                "fullname": self._fullname_de(db_id)}

    def _es_propio(self, author):
        try:
            yo = reddit_service.nombre_usuario(self.f.active_reddit_account_id) or ""
            return bool(author) and author.lower() == yo.lower()
        except Exception:
            return False

    def _repintar_actual(self):
        f = self.f
        tipo = getattr(f, "active_reddit_type", None)
        if not tipo:
            return
        with f._foco_silencioso():
            if self._es_comentario_modo():
                self._cargar_comentarios(tipo)
            else:
                self._cargar_posts(tipo)

    def _accion_async(self, fn_servicio, msg_ok=None, tras_exito=None):
        """Ejecuta una operación del servicio en segundo plano y avisa del resultado."""
        acc = self.f.active_reddit_account_id

        def _bg(ctx):
            return fn_servicio(acc)

        def _fin(res, err):
            if isinstance(err, TareaDuplicadaError):
                return
            if err:
                notificar(_("No se pudo completar la acción en Reddit: {0}").format(err))
                return
            if tras_exito:
                tras_exito(res)
            elif msg_ok:
                speak(msg_ok, interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Selección de fila -> panel de detalles
    # ------------------------------------------------------------------
    def on_item_selected(self, index):
        f = self.f
        try:
            if not f._panel_detalles_activo():
                return
        except Exception:
            pass
        db_id = self._db_id_de_index(index)
        if not db_id:
            return
        if str(getattr(f, "_modo_lista", "")) == "reddit_messages":
            self.detalle_mensaje(index)
            return
        if str(getattr(f, "_modo_lista", "")) in ("reddit_subreddits", "reddit_multis"):
            return
        if self._es_comentario_modo():
            row = db.query_one("SELECT subreddit, author, body, score FROM reddit_comments WHERE id = ?",
                               (db_id,))
            if not row:
                return
            subreddit, author, body, score = row
            lineas = [_("r/{0}").format(subreddit) if subreddit else "",
                      _("Por u/{0}").format(author) if author else "", "", body or "",
                      "", _("{0} puntos").format(score or 0)]
        else:
            row = db.query_one("SELECT subreddit, author, title, selftext, url, score, num_comments, "
                               "is_self, link_flair_text FROM reddit_posts WHERE id = ?", (db_id,))
            if not row:
                return
            (subreddit, author, title, selftext, url, score, num_comments, is_self, flair) = row
            cab = _("r/{0}").format(subreddit) if subreddit else ""
            if flair:
                cab += f" [{flair}]"
            lineas = [cab, title or "", _("Por u/{0}").format(author) if author else ""]
            if is_self and selftext:
                lineas += ["", selftext]
            elif not is_self and url:
                lineas.append(_("Enlace: {0}").format(url))
            lineas += ["", _("{0} puntos · {1} comentarios").format(score or 0, num_comments or 0)]
        try:
            self.f.txt_detalles.SetValue("\n".join(x for x in lineas if x is not None))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Activar (Intro) -> visor de hilo
    # ------------------------------------------------------------------
    def on_item_activated(self, index):
        f = self.f
        db_id = self._db_id_de_index(index)
        if not db_id:
            return
        modo = str(getattr(f, "_modo_lista", ""))
        if modo == "reddit_subreddits":
            row = db.query_one("SELECT name FROM reddit_subreddits WHERE id = ?", (db_id,))
            if row:
                self.abrir_subreddit(row[0])
            return
        if modo == "reddit_multis":
            self.abrir_multireddit(index)
            return
        if modo == "reddit_messages":
            # Intro sobre un mensaje: ver el contexto (comentario) o responder.
            row = db.query_one("SELECT context FROM reddit_messages WHERE id = ?", (db_id,))
            if row and row[0]:
                abrir_url(REDDIT_BASE + row[0])
            else:
                fn = self._fullname_de(db_id)
                self.responder_mensaje(fn)
            return
        if self._es_comentario_modo():
            row = db.query_one("SELECT link_id FROM reddit_comments WHERE id = ?", (db_id,))
            if not row or not row[0]:
                speak(_("No se pudo localizar la publicación de este comentario."), interrupt=True)
                return
            link_fullname = row[0]
            post_db_id = f"{f.active_reddit_account_id}:{link_fullname}"
            if not db.query_one("SELECT 1 FROM reddit_posts WHERE id = ?", (post_db_id,)):
                self._abrir_hilo_por_fullname(link_fullname)
                return
            self._abrir_visor_hilo(post_db_id)
            return
        self._abrir_visor_hilo(db_id)

    def _abrir_visor_hilo(self, post_db_id):
        from .reddit_thread_viewer import RedditThreadViewerDialog
        dlg = RedditThreadViewerDialog(self.f, self.f.active_reddit_account_id, post_db_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _abrir_hilo_por_fullname(self, link_fullname):
        f = self.f
        post_id = link_fullname.split("_", 1)[1] if "_" in link_fullname else link_fullname
        db_id = f"{f.active_reddit_account_id}:{link_fullname}"
        try:
            db.execute("INSERT OR IGNORE INTO reddit_posts (id, account_id, fullname, post_id) "
                       "VALUES (?, ?, ?, ?)", (db_id, f.active_reddit_account_id, link_fullname, post_id))
        except Exception:
            pass
        self._abrir_visor_hilo(db_id)

    # ------------------------------------------------------------------
    # Menú contextual
    # ------------------------------------------------------------------
    def on_context_menu(self, menu, idx):
        import wx
        modo = str(getattr(self.f, "_modo_lista", ""))
        if modo == "reddit_messages":
            self.menu_mensaje(menu, idx)
            return
        if modo == "reddit_subreddits":
            self.menu_subreddit(menu, idx)
            return
        if modo == "reddit_multis":
            return
        db_id = self._db_id_de_index(idx)
        d = self._datos_fila(db_id)
        if not d:
            return
        fn = d["fullname"]
        propio = self._es_propio(d.get("author"))

        def add(label, cb):
            it = menu.Append(wx.ID_ANY, label)
            self.f.Bind(wx.EVT_MENU, lambda e: cb(), it)

        add(_("Voto positivo"), lambda: self.votar(fn, "up"))
        add(_("Voto negativo"), lambda: self.votar(fn, "down"))
        add(_("Quitar voto"), lambda: self.votar(fn, "clear"))
        add(_("Quitar de guardados") if d.get("saved") else _("Guardar"),
            lambda: self.alternar_guardado(fn, not d.get("saved")))
        add(_("Responder"), lambda: self.responder(fn))
        if d["tipo"] == "post":
            add(_("Ver comentarios"), lambda: self._abrir_visor_hilo(db_id))
            add(_("Crosspost a otra comunidad..."), lambda: self.crosspost(db_id))
            add(_("Mostrar") if d.get("hidden") else _("Ocultar"),
                lambda: self.alternar_oculto(fn, not d.get("hidden")))
        else:
            add(_("Ver hilo"), lambda: self.on_item_activated(idx))
        if propio:
            if d["tipo"] == "comment" or d.get("is_self"):
                add(_("Editar"), lambda: self.editar(fn))
            add(_("Borrar"), lambda: self.borrar(fn))
            if d["tipo"] == "post":
                add(_("Quitar +18") if d.get("over_18") else _("Marcar +18"),
                    lambda: self.alternar_nsfw(fn, not d.get("over_18")))
                add(_("Quitar spoiler") if d.get("spoiler") else _("Marcar spoiler"),
                    lambda: self.alternar_spoiler(fn, not d.get("spoiler")))
                add(_("Etiqueta (flair)..."), lambda: self.elegir_flair(fn))
        add(_("Abrir en el navegador"), lambda: self.abrir_en_navegador(db_id))
        add(_("Copiar enlace"), lambda: self.copiar_enlace(db_id))
        # Submenú de moderación (sin efecto si no eres moderador del subreddit; el servicio avisa).
        try:
            sub_mod = wx.Menu()
            self.menu_moderacion_para(sub_mod, fn, d["tipo"])
            menu.AppendSubMenu(sub_mod, _("Moderar"))
        except Exception:
            pass

    def botones_botonera(self, db_id):
        """Botones de acción bajo la lista para el elemento enfocado."""
        d = self._datos_fila(db_id)
        if not d:
            return []
        fn = d["fullname"]
        botones = [
            (_("Voto +"), lambda: self.votar(fn, "up")),
            (_("Voto -"), lambda: self.votar(fn, "down")),
            (_("Quitar guardado") if d.get("saved") else _("Guardar"),
             lambda: self.alternar_guardado(fn, not d.get("saved"))),
            (_("Responder"), lambda: self.responder(fn)),
        ]
        if d["tipo"] == "post":
            botones.append((_("Comentarios"), lambda: self._abrir_visor_hilo(db_id)))
        return botones

    # ------------------------------------------------------------------
    # Acciones (por fullname)
    # ------------------------------------------------------------------
    def votar(self, fullname, direccion):
        msg = {"up": _("Voto positivo."), "down": _("Voto negativo."),
               "clear": _("Voto retirado.")}.get(direccion, "")
        self._accion_async(lambda acc: reddit_service.votar(acc, fullname, direccion),
                           tras_exito=lambda r: (self._repintar_actual(), speak(msg, interrupt=True)))

    def alternar_guardado(self, fullname, guardar):
        msg = _("Guardado.") if guardar else _("Quitado de guardados.")
        self._accion_async(lambda acc: reddit_service.guardar(acc, fullname, guardar),
                           tras_exito=lambda r: (self._repintar_actual(), speak(msg, interrupt=True)))

    def alternar_oculto(self, fullname, ocultar):
        msg = _("Ocultado.") if ocultar else _("Mostrado.")
        self._accion_async(lambda acc: reddit_service.ocultar(acc, fullname, ocultar),
                           tras_exito=lambda r: (self._repintar_actual(), speak(msg, interrupt=True)))

    def alternar_nsfw(self, fullname, nsfw):
        msg = _("Marcado como +18.") if nsfw else _("Quitada la marca +18.")
        self._accion_async(lambda acc: reddit_service.marcar_nsfw(acc, fullname, nsfw),
                           tras_exito=lambda r: (self._repintar_actual(), speak(msg, interrupt=True)))

    def alternar_spoiler(self, fullname, spoiler):
        msg = _("Marcado como spoiler.") if spoiler else _("Quitada la marca de spoiler.")
        self._accion_async(lambda acc: reddit_service.marcar_spoiler(acc, fullname, spoiler),
                           tras_exito=lambda r: (self._repintar_actual(), speak(msg, interrupt=True)))

    def responder(self, fullname):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Escribe tu respuesta:"), _("Responder en Reddit"),
                                    multiline=True)
        import wx
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        speak(_("Enviando respuesta..."), interrupt=True)
        self._accion_async(lambda acc: reddit_service.responder(acc, fullname, texto),
                           tras_exito=lambda r: speak(_("Respuesta enviada."), interrupt=True))

    def editar(self, fullname):
        from .accessible_controls import CustomTextEntryDialog
        import wx
        # Texto actual desde la caché.
        campo = "body" if fullname.startswith("t1_") else "selftext"
        tabla = "reddit_comments" if fullname.startswith("t1_") else "reddit_posts"
        row = db.query_one(f"SELECT {campo} FROM {tabla} WHERE id = ?",
                           (f"{self.f.active_reddit_account_id}:{fullname}",))
        actual = row[0] if row else ""
        dlg = CustomTextEntryDialog(self.f, _("Edita el texto:"), _("Editar en Reddit"),
                                    value=actual or "", multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        speak(_("Guardando cambios..."), interrupt=True)
        self._accion_async(lambda acc: reddit_service.editar(acc, fullname, texto),
                           tras_exito=lambda r: (self._repintar_actual(), speak(_("Editado."), interrupt=True)))

    def borrar(self, fullname):
        from .accessible_controls import confirmar
        import wx
        if confirmar(self.f, _("¿Seguro que quieres borrar este elemento? No se puede deshacer."),
                     _("Confirmar borrado")) != wx.ID_YES:
            return
        self._accion_async(lambda acc: reddit_service.borrar(acc, fullname),
                           tras_exito=lambda r: (self._repintar_actual(), speak(_("Borrado."), interrupt=True)))

    def elegir_flair(self, fullname):
        """Descarga las opciones de flair y deja elegir una."""
        acc = self.f.active_reddit_account_id

        def _bg(ctx):
            return reddit_service.opciones_flair(acc, fullname)

        def _fin(opciones, err):
            if err:
                notificar(_("No se pudieron obtener las etiquetas: {0}").format(err))
                return
            if not opciones:
                speak(_("Esta comunidad no ofrece etiquetas para las publicaciones."), interrupt=True)
                return
            import wx
            textos = [o.get("text") or _("(sin texto)") for o in opciones]
            dlg = wx.SingleChoiceDialog(self.f, _("Elige una etiqueta (flair):"),
                                        _("Etiqueta de la publicación"), textos)
            if dlg.ShowModal() == wx.ID_OK:
                sel = opciones[dlg.GetSelection()]
                self._accion_async(
                    lambda a: reddit_service.aplicar_flair(a, fullname, sel.get("id"), sel.get("text")),
                    tras_exito=lambda r: (self._repintar_actual(), speak(_("Etiqueta aplicada."), interrupt=True)))
            dlg.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Abrir / copiar
    # ------------------------------------------------------------------
    def abrir_en_navegador(self, db_id):
        if self._es_comentario_modo():
            row = db.query_one("SELECT permalink FROM reddit_comments WHERE id = ?", (db_id,))
            destino = (REDDIT_BASE + row[0]) if row and row[0] else ""
        else:
            row = db.query_one("SELECT permalink, url, is_self FROM reddit_posts WHERE id = ?", (db_id,))
            if not row:
                return
            permalink, url, is_self = row
            destino = (REDDIT_BASE + permalink) if permalink else (url or "")
        if destino:
            abrir_url(destino)

    def copiar_enlace(self, db_id):
        tabla = "reddit_comments" if self._es_comentario_modo() else "reddit_posts"
        row = db.query_one(f"SELECT permalink FROM {tabla} WHERE id = ?", (db_id,))
        if row and row[0]:
            copiar_al_portapapeles(REDDIT_BASE + row[0], _("Enlace copiado al portapapeles."))

    # ------------------------------------------------------------------
    # Publicar
    # ------------------------------------------------------------------
    def nuevo_post(self):
        """Abre el diálogo de redacción y publica en segundo plano."""
        import wx
        f = self.f
        acc = f.active_reddit_account_id
        if not acc:
            speak(_("Primero selecciona una cuenta de Reddit."), interrupt=True)
            return
        # Prerrellenar el subreddit si el buffer activo es uno concreto.
        sub_ini = ""
        tipo_buf = str(getattr(f, "active_reddit_type", "") or "")
        if tipo_buf.startswith("sub:"):
            sub_ini = tipo_buf.split(":", 1)[1].split(":", 1)[0]
        from .reddit_dialogs import RedditComposeDialog
        dlg = RedditComposeDialog(f, acc, subreddit_inicial=sub_ini)
        if dlg.ShowModal() != wx.ID_OK or not dlg.resultado:
            dlg.Destroy()
            return
        d = dlg.resultado
        dlg.Destroy()
        speak(_("Publicando en Reddit..."), interrupt=True)
        from app.services.reddit import reddit_service
        comun = dict(flair_id=d.get("flair_id"), flair_text=d.get("flair_text"),
                     nsfw=d.get("nsfw"), spoiler=d.get("spoiler"), send_replies=d.get("send_replies"))
        sub, titulo = d["subreddit"], d["title"]
        tipo = d["tipo"]

        def _bg(ctx):
            if tipo == "texto":
                return reddit_service.publicar_texto(acc, sub, titulo, d.get("selftext", ""), **comun)
            if tipo == "enlace":
                return reddit_service.publicar_enlace(acc, sub, titulo, d.get("url", ""), **comun)
            if tipo == "imagen":
                return reddit_service.publicar_imagen(acc, sub, titulo, d.get("path", ""), **comun)
            if tipo == "video":
                return reddit_service.publicar_video(acc, sub, titulo, d.get("path", ""), **comun)
            if tipo == "galeria":
                return reddit_service.publicar_galeria(acc, sub, titulo, d.get("gallery", []), **comun)
            if tipo == "encuesta":
                return reddit_service.publicar_encuesta(
                    acc, sub, titulo, d.get("poll_options", []), d.get("poll_duration", 3),
                    selftext=d.get("selftext", ""), **comun)
            return None

        def _fin(res, err):
            if isinstance(err, TareaDuplicadaError):
                return
            if err:
                notificar(_("No se pudo publicar en Reddit: {0}").format(err))
                return
            notificar(_("Publicado en r/{0}.").format(sub))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def crosspost(self, db_id):
        """Crospostea la publicación enfocada a otra comunidad."""
        import wx
        from .accessible_controls import CustomTextEntryDialog
        f = self.f
        fullname = self._fullname_de(db_id)
        row = db.query_one("SELECT title FROM reddit_posts WHERE id = ?", (db_id,))
        titulo_ini = row[0] if row else ""
        dlg_sub = CustomTextEntryDialog(f, _("Comunidad de destino (subreddit, sin r/):"),
                                        _("Crosspost"))
        if dlg_sub.ShowModal() != wx.ID_OK:
            dlg_sub.Destroy()
            return
        destino = dlg_sub.GetValue().strip()
        dlg_sub.Destroy()
        if not destino:
            return
        dlg_t = CustomTextEntryDialog(f, _("Título:"), _("Crosspost"), value=titulo_ini)
        if dlg_t.ShowModal() != wx.ID_OK:
            dlg_t.Destroy()
            return
        titulo = dlg_t.GetValue().strip()
        dlg_t.Destroy()
        if not titulo:
            return
        speak(_("Publicando el crosspost..."), interrupt=True)
        acc = f.active_reddit_account_id
        from app.services.reddit import reddit_service

        def _bg(ctx):
            return reddit_service.crosspost(acc, destino, fullname, titulo)

        def _fin(res, err):
            if err and not isinstance(err, TareaDuplicadaError):
                notificar(_("No se pudo crospostear: {0}").format(err))
            elif not err:
                notificar(_("Crosspost publicado en r/{0}.").format(destino))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Acciones dentro del visor de hilo de comentarios
    # ------------------------------------------------------------------
    def acciones_visor_hilo(self, dlg, menu, el):
        """Añade al menú del visor de hilo las acciones de escritura sobre 'el' (post o comment)."""
        import wx
        fn = el.get("fullname")
        if not fn:
            return
        propio = self._es_propio(el.get("author"))

        def add(label, cb):
            it = menu.Append(wx.ID_ANY, label)
            dlg.Bind(wx.EVT_MENU, lambda e: cb(), it)

        recargar = lambda: dlg._cargar()
        add(_("Voto positivo"), lambda: self._accion_async(
            lambda a: reddit_service.votar(a, fn, "up"), tras_exito=lambda r: speak(_("Voto positivo."), interrupt=True)))
        add(_("Voto negativo"), lambda: self._accion_async(
            lambda a: reddit_service.votar(a, fn, "down"), tras_exito=lambda r: speak(_("Voto negativo."), interrupt=True)))
        add(_("Guardar / quitar guardado"), lambda: self._accion_async(
            lambda a: reddit_service.guardar(a, fn, not el.get("saved")),
            tras_exito=lambda r: speak(_("Hecho."), interrupt=True)))
        add(_("Responder"), lambda: (self.responder(fn)))
        if propio:
            add(_("Editar"), lambda: self.editar(fn))
            add(_("Borrar"), lambda: (self.borrar(fn), recargar()))
