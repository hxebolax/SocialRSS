# -*- coding: utf-8 -*-
"""Acciones de W Social en la interfaz: refrescar, publicar, actuar sobre una publicación,
menú contextual y apertura de elementos.

Mixin de WSocialController.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.services.wsocial import wsocial_service
from .accessible_controls import mostrar_info, confirmar, CustomTextEntryDialog

log = logging.getLogger(__name__)


def texto_inicial_respuesta(account_id, autor_handle, contenido) -> str:
    """Cómo empieza el texto de una respuesta en W Social: con las @menciones del autor y de
    quien él menciona (Preferencias → W Social, activado por defecto porque W Social siempre
    ponía la del autor) o vacío, como la app oficial: el servidor avisa sin menciones."""
    if not config.get("wsocial", "mencionar_al_responder", True):
        return ""
    from app.core.at_lectura import menciones_al_responder, mi_handle
    return menciones_al_responder(autor_handle, contenido,
                                  mi_handle(wsocial_service, account_id))


class _AccionesMixin:
    # ------------------------------------------------------------------
    # REFRESCO
    # ------------------------------------------------------------------
    def on_refresh(self, silencioso=False, cargar_mas=False):
        """Descarga el buffer activo. `silencioso` es para la auto-carga y el sondeo: no habla
        salvo que haya novedades. `cargar_mas` pagina hacia atrás."""
        f = self.f
        acc = f.active_wsocial_account_id
        if not acc:
            return
        tipo = f.active_timeline_type
        if not silencioso:
            speak(_("Actualizando...") if not cargar_mas else _("Cargando más..."), interrupt=True)

        def _repintar(mensaje=""):
            if not self.f:
                return
            self.cargar_listado()
            if mensaje and not silencioso:
                speak(mensaje, interrupt=True)

        def _cb(ok, res):
            if not ok:
                # Si ha caducado la sesión se ofrece renovarla en el momento (en vez de dejar
                # un aviso que no lleva a ninguna parte). Vale también en modo silencioso: sin
                # sesión, la cuenta no vuelve a funcionar hasta que se autorice de nuevo.
                if self.ofrecer_reautorizar(res, acc):
                    return
                if not silencioso:
                    notificar(_("No se pudo actualizar: {0}").format(res))
                return
            n = len(res) if isinstance(res, (list, tuple)) else 0
            wx.CallAfter(_repintar, _("{0} elementos.").format(n) if n else _("Sin novedades."))

        if tipo in self.BUFFERS_ACTORES:
            wsocial_service.cargar_actores_async(acc, tipo, cargar_mas=cargar_mas, callback=_cb)
        elif tipo == "trending":
            def _cb_trend(ok, res):
                if ok:
                    self._tendencias = res
                    wx.CallAfter(_repintar, _("{0} tendencias.").format(len(res)))
                elif not silencioso:
                    notificar(_("No se pudieron cargar las tendencias: {0}").format(res))
            wsocial_service.tendencias_async(acc, callback=_cb_trend)
        elif tipo == "starterpacks":
            wsocial_service.cargar_starter_packs_async(acc, callback=_cb)
        elif tipo in ("convos", "requests"):
            wsocial_service.cargar_convos_async(
                acc, cargar_mas=cargar_mas, solicitudes=(tipo == "requests"), callback=_cb)
        elif tipo == "drafts":
            wsocial_service.listar_borradores_async(acc, callback=_cb)
        elif tipo == "feeds":
            wsocial_service.cargar_feeds_async(acc, callback=_cb)
        elif tipo == "lists":
            wsocial_service.cargar_listas_async(acc, callback=_cb)
        elif self._es_buffer_posts(tipo) or tipo == "notifications":
            wsocial_service.cargar_timeline_async(acc, tipo, cargar_mas=cargar_mas, callback=_cb)

    def actualizar_incremental(self):
        """Sondeo silencioso del buffer activo: trae novedades e inserta las filas nuevas SIN
        mover el foco ni hacer releer al lector (regla de la aplicación)."""
        f = self.f
        acc = f.active_wsocial_account_id
        if not acc or not f.active_timeline_type:
            return
        tipo = f.active_timeline_type

        def _cb(ok, res):
            if not ok or not self.f:
                return
            wx.CallAfter(self._insertar_novedades, tipo)
        if self._es_buffer_posts(tipo) or tipo == "notifications":
            wsocial_service.cargar_timeline_async(acc, tipo, callback=_cb)
        elif tipo in ("convos", "requests"):
            wsocial_service.cargar_convos_async(
                acc, solicitudes=(tipo == "requests"), callback=_cb)

    def _insertar_novedades(self, tipo):
        """Inserta en la lista solo lo que no estaba, conservando la posición del usuario."""
        f = self.f
        if not f or f.active_timeline_type != tipo:
            return
        if self._es_buffer_posts(tipo):
            deseadas = self._filas_posts_actual(tipo)
        elif tipo == "notifications":
            deseadas = [(d, c, l, None, "") for d, c, l in self._filas_notifs_actual()]
        elif tipo in ("convos", "requests"):
            deseadas = [(d, c, l, None, "") for d, c, l in
                        self._filas_convos_actual(tipo == "requests")]
        else:
            return
        existentes = set(f.lista_items_db_ids)
        nuevas = [x for x in deseadas if x[0] not in existentes]
        if not nuevas:
            return
        try:
            f._insertar_items_incremental([(d, c, l) for d, c, l, _m, _f in nuevas])
        except Exception:
            # Si la inserción incremental no está disponible para este modo, se repinta entero.
            self.cargar_listado()

    # ------------------------------------------------------------------
    # PUBLICAR
    # ------------------------------------------------------------------
    def nuevo_post(self, reply_uri="", quote_uri="", texto_inicial=""):
        """Abre el compositor de W Social."""
        f = self.f
        acc = f.active_wsocial_account_id or self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(f, _("Primero añade una cuenta de W Social."), _("Sin cuentas"))
            return
        from .wsocial_dialogs import WSocialComposeDialog
        dlg = WSocialComposeDialog(f, acc, reply_uri=reply_uri, quote_uri=quote_uri,
                                   texto_inicial=texto_inicial)
        dlg.ShowModal()
        dlg.Destroy()

    def responder(self, db_id):
        """Responde a la publicación enfocada. Si está activado en Preferencias (lo está por
        defecto en W Social, como siempre), el texto empieza mencionando al autor y a quien él
        menciona, sin la cuenta propia."""
        fila = db.query_one("SELECT uri, author_handle, content FROM wsocial_posts WHERE id = ?",
                            (db_id,))
        if not fila:
            return
        uri, handle, contenido = fila
        acc = self.f.active_wsocial_account_id or self._cuenta_activa_o_primera()
        self.nuevo_post(reply_uri=uri, texto_inicial=texto_inicial_respuesta(acc, handle, contenido))

    def citar(self, db_id):
        """Cita la publicación enfocada."""
        fila = db.query_one("SELECT uri FROM wsocial_posts WHERE id = ?", (db_id,))
        if fila:
            self.nuevo_post(quote_uri=fila[0])

    def nuevo_hilo(self):
        """Editor de hilos: texto largo troceado en publicaciones encadenadas."""
        f = self.f
        acc = f.active_wsocial_account_id or self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(f, _("Primero añade una cuenta de W Social."), _("Sin cuentas"))
            return
        from .wsocial_dialogs import WSocialThreadEditorDialog
        dlg = WSocialThreadEditorDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    # ------------------------------------------------------------------
    # ACCIONES SOBRE UNA PUBLICACIÓN
    # ------------------------------------------------------------------
    def accion_post(self, db_id, accion):
        """Me gusta, republicar, marcador... con aviso hablado del resultado."""
        acc = self.f.active_wsocial_account_id
        if not acc or not db_id:
            return
        etiquetas = {"like": _("Me gusta."), "unlike": _("Me gusta quitado."),
                     "repost": _("Republicada."), "unrepost": _("Republicación deshecha."),
                     "bookmark": _("Guardada en marcadores."),
                     "unbookmark": _("Quitada de marcadores.")}

        def _cb(ok, res):
            if ok:
                speak(etiquetas.get(accion, _("Hecho.")), interrupt=True)
                wx.CallAfter(self._refrescar_fila, db_id)
            else:
                notificar(_("No se pudo: {0}").format(res))
        wsocial_service.ejecutar_accion_post_async(acc, db_id, accion, callback=_cb)

    def alternar_me_gusta(self, db_id):
        """Da o quita el me gusta según el estado real de la publicación."""
        fila = db.query_one("SELECT liked_uri FROM wsocial_posts WHERE id = ?", (db_id,))
        self.accion_post(db_id, "unlike" if (fila and fila[0]) else "like")

    def alternar_republicar(self, db_id):
        fila = db.query_one("SELECT reposted_uri FROM wsocial_posts WHERE id = ?", (db_id,))
        self.accion_post(db_id, "unrepost" if (fila and fila[0]) else "repost")

    def alternar_marcador(self, db_id):
        fila = db.query_one("SELECT bookmarked FROM wsocial_posts WHERE id = ?", (db_id,))
        self.accion_post(db_id, "unbookmark" if (fila and fila[0]) else "bookmark")

    def _refrescar_fila(self, db_id):
        """Repinta EN SU SITIO la fila de una publicación (sin recargar la lista ni mover foco)."""
        f = self.f
        if not f or db_id not in f.lista_items_db_ids:
            return
        idx = f.lista_items_db_ids.index(db_id)
        fila = db.query_one(f"SELECT {self._SQL_COLS_POST} FROM wsocial_posts p WHERE p.id = ?",
                            (db_id,))
        if not fila:
            return
        _id, valores, _fecha, _media, _txt, _cont = self._valores_post(fila)
        cols = getattr(f, "_cols_actuales", None) or getattr(self, "_cols_posts", [])
        for j, (campo, _e) in enumerate(cols):
            try:
                f.lista_items.SetItem(idx, j, valores.get(campo, ""))
            except Exception:
                pass

    def borrar_post(self, db_id):
        """Borra una publicación propia, con confirmación."""
        f = self.f
        fila = db.query_one("SELECT uri FROM wsocial_posts WHERE id = ?", (db_id,))
        if not fila:
            return
        if confirmar(f, _("¿Seguro que quieres borrar esta publicación? No se puede deshacer."),
                     _("Borrar publicación")) != wx.ID_YES:
            return

        def _cb(ok, res):
            if ok:
                notificar(_("Publicación borrada."))
                wx.CallAfter(self.cargar_listado)
            else:
                notificar(_("No se pudo borrar: {0}").format(res))
        wsocial_service.borrar_post_async(f.active_wsocial_account_id, fila[0], callback=_cb)

    def ver_hilo(self, db_id):
        """Abre la conversación completa en el visor de hilos."""
        f = self.f
        fila = db.query_one("SELECT uri FROM wsocial_posts WHERE id = ?", (db_id,))
        if not fila:
            return
        from .wsocial_dialogs import WSocialThreadViewerDialog
        dlg = WSocialThreadViewerDialog(f, f.active_wsocial_account_id, fila[0])
        dlg.ShowModal()
        dlg.Destroy()

    def abrir_como_buffer(self, db_id):
        """Convierte una conversación en un buffer permanente del árbol."""
        f = self.f
        fila = db.query_one("SELECT uri, author_handle, content FROM wsocial_posts WHERE id = ?",
                            (db_id,))
        if not fila:
            return
        uri, handle, contenido = fila
        hilos = list(config.get("wsocial", "thread_buffers", []))
        if any(h.get("uri") == uri for h in hilos):
            speak(_("Esa conversación ya está en el árbol."), interrupt=True)
            return
        titulo = f"@{handle}: " + (contenido or "")[:40]
        hilos.append({"uri": uri, "titulo": titulo, "account_id": f.active_wsocial_account_id})
        config.set("wsocial", "thread_buffers", hilos)
        f._cargar_navegacion()
        self._re_seleccionar("thread:" + uri)
        notificar(_("Conversación añadida al árbol."))

    def crear_buffer_usuario(self, did, handle=""):
        """Añade la cronología de una cuenta como buffer permanente."""
        f = self.f
        acc = f.active_wsocial_account_id
        if not acc or not did:
            return
        cronologias = list(config.get("wsocial", "user_timelines", []))
        if any(u.get("did") == did and u.get("account_id") == acc for u in cronologias):
            speak(_("Ya tienes la cronología de esa cuenta."), interrupt=True)
            return
        cronologias.append({"did": did, "handle": (handle or did).lstrip("@"), "account_id": acc})
        config.set("wsocial", "user_timelines", cronologias)
        f._cargar_navegacion()
        self._re_seleccionar("author:" + did)
        wsocial_service.cargar_timeline_async(
            acc, "author:" + did,
            callback=lambda ok, res: wx.CallAfter(self.cargar_listado) if ok else None)
        notificar(_("Cronología añadida."))

    def guardar_busqueda(self, texto=""):
        """Guarda una búsqueda como buffer del árbol."""
        f = self.f
        if not texto:
            dlg = CustomTextEntryDialog(f, _("Texto a buscar:"), _("Guardar búsqueda"))
            texto = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
            dlg.Destroy()
        if not texto:
            return
        busquedas = list(config.get("wsocial", "saved_searches", []))
        if texto not in busquedas:
            busquedas.append(texto)
            config.set("wsocial", "saved_searches", busquedas)
        f._cargar_navegacion()
        self._re_seleccionar("search:" + texto)
        wsocial_service.cargar_timeline_async(
            f.active_wsocial_account_id, "search:" + texto,
            callback=lambda ok, res: wx.CallAfter(self.cargar_listado) if ok else None)

    # ------------------------------------------------------------------
    # APERTURA DE ELEMENTOS (Intro)
    # ------------------------------------------------------------------
    def abrir_elemento(self, db_id):
        """Qué hace Intro según el tipo de fila enfocada."""
        f = self.f
        if not db_id:
            return
        if db_id.startswith("convo:"):
            self.abrir_conversacion(db_id.split(":", 1)[1])
        elif db_id.startswith("actor:"):
            self.ver_perfil(db_id.split(":", 1)[1])
        elif db_id.startswith("trend:"):
            self.guardar_busqueda(db_id.split(":", 1)[1])
        elif db_id.startswith("feed:") or db_id.startswith("list:"):
            self.abrir_feed_o_lista(db_id)
        elif db_id.startswith("starterpack:"):
            self.abrir_starter_pack(db_id.split(":", 1)[1])
        elif db_id.startswith("draft:"):
            self.abrir_borrador(db_id.split(":", 1)[1])
        elif f._modo_lista == "wsocial_notifs":
            self.abrir_notificacion(db_id)
        else:
            self.ver_hilo(db_id)

    def abrir_conversacion(self, convo_id):
        """Abre una conversación (o un grupo) en su ventana."""
        f = self.f
        fila = db.query_one("SELECT title, is_group FROM wsocial_convos WHERE id = ?",
                            (f"{f.active_wsocial_account_id}:{convo_id}",))
        titulo = fila[0] if fila else _("Conversación")
        from .wsocial_dialogs import WSocialConversationDialog
        dlg = WSocialConversationDialog(f, f.active_wsocial_account_id, convo_id, titulo,
                                        es_grupo=bool(fila[1]) if fila else False)
        dlg.ShowModal()
        dlg.Destroy()
        self.cargar_listado()

    def abrir_feed_o_lista(self, db_id):
        """Abre un feed o una lista como buffer del árbol."""
        f = self.f
        kind, uri = db_id.split(":", 1)
        clave = "saved_feeds" if kind == "feed" else "lists_cache"
        fila = db.query_one("SELECT name FROM wsocial_feeds WHERE account_id = ? AND uri = ?",
                            (f.active_wsocial_account_id, uri))
        nombre = fila[0] if fila else uri
        guardados = list(config.get("wsocial", clave, []))
        if not any(g.get("uri") == uri for g in guardados):
            guardados.append({"uri": uri, "name": nombre})
            config.set("wsocial", clave, guardados)
            f._cargar_navegacion()
        tipo = f"{kind}:{uri}"
        self._re_seleccionar(tipo)
        wsocial_service.cargar_timeline_async(
            f.active_wsocial_account_id, tipo,
            callback=lambda ok, res: wx.CallAfter(self.cargar_listado) if ok else None)

    def abrir_starter_pack(self, uri):
        """Muestra el contenido de un paquete de inicio y ofrece seguir a sus cuentas."""
        f = self.f
        speak(_("Abriendo el paquete de inicio..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo abrir: {0}").format(res))
                return
            from .wsocial_dialogs import WSocialStarterPackDialog
            def _mostrar():
                dlg = WSocialStarterPackDialog(f, f.active_wsocial_account_id, res)
                dlg.ShowModal()
                dlg.Destroy()
            wx.CallAfter(_mostrar)
        wsocial_service.detalle_starter_pack_async(f.active_wsocial_account_id, uri, callback=_cb)

    def abrir_borrador(self, draft_id):
        """Sigue escribiendo un borrador guardado en el servidor."""
        f = self.f
        fila = db.query_one("SELECT texto FROM wsocial_drafts WHERE id = ?",
                            (f"{f.active_wsocial_account_id}:{draft_id}",))
        self.nuevo_post(texto_inicial=(fila[0] if fila else ""))

    def abrir_notificacion(self, db_id):
        """Abre la publicación que originó una notificación (o el perfil, si no hay ninguna).

        La publicación NO es el `uri` de la notificación: en un "me gusta" ese URI es el
        registro del like y pedir su hilo devuelve 400 NotFound. Se usa subject_uri, y para las
        notificaciones guardadas antes de esa columna se deduce al vuelo."""
        f = self.f
        fila = db.query_one(
            "SELECT subject_uri, uri, reason, reason_subject, author_did "
            "FROM wsocial_notifications WHERE id = ?", (db_id,))
        if not fila:
            return
        subject_uri, uri, motivo, reason_subject, autor_did = fila
        if not subject_uri:
            from app.services.wsocial.wsocial_common import uri_post_de_notificacion
            subject_uri = uri_post_de_notificacion(motivo or "", uri or "", reason_subject or "")
            if subject_uri:
                try:
                    db.execute("UPDATE wsocial_notifications SET subject_uri = ? WHERE id = ?",
                               (subject_uri, db_id))
                except Exception as e:
                    log.debug(f"No se pudo guardar el asunto de la notificación: {e}")
        if not subject_uri:
            if autor_did:
                self.ver_perfil(autor_did)
            else:
                speak(_("Esta notificación no tiene ninguna publicación asociada."), interrupt=True)
            return
        from .wsocial_dialogs import WSocialThreadViewerDialog
        dlg = WSocialThreadViewerDialog(f, f.active_wsocial_account_id, subject_uri)
        dlg.ShowModal()
        dlg.Destroy()

    def ver_perfil(self, actor):
        """Ficha de una cuenta con sus acciones (seguir, silenciar, bloquear, escribir...)."""
        f = self.f
        from .wsocial_dialogs import WSocialProfileDialog
        dlg = WSocialProfileDialog(f, f.active_wsocial_account_id, actor)
        dlg.ShowModal()
        dlg.Destroy()

    # ------------------------------------------------------------------
    # MENÚ CONTEXTUAL
    # ------------------------------------------------------------------
    def construir_menu_contextual(self, menu, db_id):
        """Añade al menú contextual de la lista las opciones de W Social."""
        f = self.f
        if not db_id:
            return
        if db_id.startswith("convo:"):
            self._menu_conversacion(menu, db_id.split(":", 1)[1])
            return
        if db_id.startswith("actor:"):
            self._menu_actor(menu, db_id.split(":", 1)[1])
            return
        if f._modo_lista == "wsocial_notifs":
            it = menu.Append(wx.ID_ANY, _("Abrir la publicación"))
            f.Bind(wx.EVT_MENU, lambda e: self.abrir_notificacion(db_id), it)
            it = menu.Append(wx.ID_ANY, _("Marcar todas como leídas"))
            f.Bind(wx.EVT_MENU, lambda e: self.marcar_notificaciones_leidas(), it)
            return
        if f._modo_lista != "wsocial_posts":
            return
        self._menu_publicacion(menu, db_id)

    def _menu_publicacion(self, menu, db_id):
        """Menú de una publicación, adaptado a su estado real."""
        f = self.f
        fila = db.query_one(
            "SELECT uri, cid, author_did, author_handle, liked_uri, reposted_uri, bookmarked, "
            "reply_root_uri, quote_uri FROM wsocial_posts WHERE id = ?", (db_id,))
        if not fila:
            return
        uri, cid, autor_did, handle, liked, reposted, marcada, raiz, cita = fila
        yo = (wsocial_service._me.get(f.active_wsocial_account_id) or {}).get("did", "")
        es_mia = (autor_did == yo)

        it = menu.Append(wx.ID_ANY, _("Responder"))
        f.Bind(wx.EVT_MENU, lambda e: self.responder(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Citar"))
        f.Bind(wx.EVT_MENU, lambda e: self.citar(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Quitar me gusta") if liked else _("Me gusta"))
        f.Bind(wx.EVT_MENU, lambda e: self.alternar_me_gusta(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Deshacer republicación") if reposted else _("Republicar"))
        f.Bind(wx.EVT_MENU, lambda e: self.alternar_republicar(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Quitar de marcadores") if marcada else _("Guardar en marcadores"))
        f.Bind(wx.EVT_MENU, lambda e: self.alternar_marcador(db_id), it)
        menu.AppendSeparator()

        it = menu.Append(wx.ID_ANY, _("Ver la conversación"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_hilo(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Abrir la conversación como buffer"))
        f.Bind(wx.EVT_MENU, lambda e: self.abrir_como_buffer(db_id), it)
        it = menu.Append(wx.ID_ANY, _("Ver quién le ha dado me gusta"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_interacciones(db_id, "likes"), it)
        it = menu.Append(wx.ID_ANY, _("Ver quién la ha republicado"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_interacciones(db_id, "reposts"), it)
        it = menu.Append(wx.ID_ANY, _("Ver quién la ha citado"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_citas(db_id), it)
        menu.AppendSeparator()

        it = menu.Append(wx.ID_ANY, _("Ver el perfil del autor"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_perfil(autor_did), it)
        it = menu.Append(wx.ID_ANY, _("Crear un buffer con las publicaciones del autor"))
        f.Bind(wx.EVT_MENU, lambda e, d=autor_did, h=handle: self.crear_buffer_usuario(d, h), it)
        it = menu.Append(wx.ID_ANY, _("Crear un buffer con las publicaciones de un usuario mencionado..."))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.crear_buffer_usuario_mencionado(d), it)
        it = menu.Append(wx.ID_ANY, _("Escribir un mensaje directo al autor"))
        f.Bind(wx.EVT_MENU, lambda e: self.mensaje_directo_a(autor_did), it)
        menu.AppendSeparator()

        if raiz:
            it = menu.Append(wx.ID_ANY, _("Silenciar esta conversación"))
            f.Bind(wx.EVT_MENU, lambda e: self.silenciar_hilo(raiz), it)
        if es_mia:
            it = menu.Append(wx.ID_ANY, _("Fijar en mi perfil"))
            f.Bind(wx.EVT_MENU, lambda e: self.fijar_post(uri, cid, True), it)
            it = menu.Append(wx.ID_ANY, _("Dejar de fijar"))
            f.Bind(wx.EVT_MENU, lambda e: self.fijar_post(uri, cid, False), it)
            it = menu.Append(wx.ID_ANY, _("Borrar la publicación"))
            f.Bind(wx.EVT_MENU, lambda e: self.borrar_post(db_id), it)
        else:
            it = menu.Append(wx.ID_ANY, _("Denunciar la publicación..."))
            f.Bind(wx.EVT_MENU, lambda e: self.denunciar(uri=uri, cid=cid), it)

    def _menu_actor(self, menu, did):
        """Menú de una cuenta en las listas de seguidores, seguidos, bloqueados o silenciados."""
        f = self.f
        fila = db.query_one("SELECT handle, following_uri, blocking_uri, muted FROM wsocial_actors "
                            "WHERE account_id = ? AND did = ? LIMIT 1",
                            (f.active_wsocial_account_id, did))
        handle = fila[0] if fila else ""
        sigo = bool(fila[1]) if fila else False
        bloqueado = bool(fila[2]) if fila else False
        silenciado = bool(fila[3]) if fila else False

        it = menu.Append(wx.ID_ANY, _("Ver el perfil"))
        f.Bind(wx.EVT_MENU, lambda e: self.ver_perfil(did), it)
        it = menu.Append(wx.ID_ANY, _("Ver sus publicaciones"))
        f.Bind(wx.EVT_MENU, lambda e: self.crear_buffer_usuario(did, handle), it)
        it = menu.Append(wx.ID_ANY, _("Dejar de seguir") if sigo else _("Seguir"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_cuenta(did, "unfollow" if sigo else "follow"), it)
        it = menu.Append(wx.ID_ANY, _("Quitar el silencio") if silenciado else _("Silenciar"))
        f.Bind(wx.EVT_MENU,
               lambda e: self.accion_cuenta(did, "unmute" if silenciado else "mute"), it)
        it = menu.Append(wx.ID_ANY, _("Desbloquear") if bloqueado else _("Bloquear"))
        f.Bind(wx.EVT_MENU,
               lambda e: self.accion_cuenta(did, "unblock" if bloqueado else "block"), it)
        it = menu.Append(wx.ID_ANY, _("Añadir a una lista..."))
        f.Bind(wx.EVT_MENU, lambda e: self.anadir_a_lista(did), it)
        it = menu.Append(wx.ID_ANY, _("Escribir un mensaje directo"))
        f.Bind(wx.EVT_MENU, lambda e: self.mensaje_directo_a(did), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Denunciar la cuenta..."))
        f.Bind(wx.EVT_MENU, lambda e: self.denunciar(did=did), it)

    def _menu_conversacion(self, menu, convo_id):
        """Menú de una conversación o grupo."""
        f = self.f
        fila = db.query_one("SELECT muted, is_group, is_request, locked, group_role "
                            "FROM wsocial_convos WHERE id = ?",
                            (f"{f.active_wsocial_account_id}:{convo_id}",))
        silenciada = bool(fila[0]) if fila else False
        es_grupo = bool(fila[1]) if fila else False
        es_solicitud = bool(fila[2]) if fila else False
        cerrada = bool(fila[3]) if fila else False
        papel = (fila[4] or "") if fila else ""

        it = menu.Append(wx.ID_ANY, _("Abrir"))
        f.Bind(wx.EVT_MENU, lambda e: self.abrir_conversacion(convo_id), it)
        if es_solicitud:
            it = menu.Append(wx.ID_ANY, _("Aceptar la solicitud"))
            f.Bind(wx.EVT_MENU, lambda e: self.accion_convo(convo_id, "aceptar"), it)
        it = menu.Append(wx.ID_ANY, _("Marcar como leída"))
        f.Bind(wx.EVT_MENU, lambda e: self.accion_convo(convo_id, "leer"), it)
        it = menu.Append(wx.ID_ANY, _("Quitar el silencio") if silenciada else _("Silenciar"))
        f.Bind(wx.EVT_MENU,
               lambda e: self.accion_convo(convo_id, "no_silenciar" if silenciada else "silenciar"), it)
        if es_grupo:
            menu.AppendSeparator()
            it = menu.Append(wx.ID_ANY, _("Participantes del grupo..."))
            f.Bind(wx.EVT_MENU, lambda e: self.gestionar_grupo(convo_id), it)
            if papel in ("owner", "admin"):
                it = menu.Append(wx.ID_ANY, _("Cambiar el nombre del grupo..."))
                f.Bind(wx.EVT_MENU, lambda e: self.renombrar_grupo(convo_id), it)
                it = menu.Append(wx.ID_ANY, _("Enlace de invitación..."))
                f.Bind(wx.EVT_MENU, lambda e: self.enlace_grupo(convo_id), it)
                it = menu.Append(wx.ID_ANY, _("Solicitudes para entrar..."))
                f.Bind(wx.EVT_MENU, lambda e: self.solicitudes_grupo(convo_id), it)
                it = menu.Append(wx.ID_ANY,
                                 _("Reabrir el grupo") if cerrada else _("Cerrar el grupo"))
                f.Bind(wx.EVT_MENU,
                       lambda e: self.accion_convo(convo_id, "reabrir" if cerrada else "cerrar"), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Salir de la conversación"))
        f.Bind(wx.EVT_MENU, lambda e: self.salir_convo(convo_id), it)
