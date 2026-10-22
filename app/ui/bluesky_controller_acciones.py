# -*- coding: utf-8 -*-
"""Controlador Bluesky: selección, menú contextual y acciones sobre posts (mixin).
"""
import json
import logging

import wx
from app.core.privacy import abrir_url

from app.core.database import db
from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager
from app.ui.accessible_controls import confirmar

from app.services.bluesky import bluesky_service
from .bluesky_dialogs import BlueskyComposeDialog

log = logging.getLogger(__name__)


def parse_actor_dbid(db_id):
    """Extrae (did, handle) de un db_id de actor de Bluesky con formato 'user:{did}|{handle}'.

    El DID de Bluesky CONTIENE dos puntos (did:plc:..., did:web:...), así que NUNCA se puede
    partir por ':' (eso dejaba el did en literal 'did' y rompía dejar de seguir / ver perfil).
    Por eso el separador entre did y handle es '|', que no aparece ni en el did ni en el handle."""
    resto = db_id[5:] if db_id.startswith("user:") else db_id
    if "|" in resto:
        did, handle = resto.split("|", 1)
        return did, handle
    # Compatibilidad con db_ids del formato antiguo 'user:{did}:{handle}': solo era correcto si el
    # did NO tenía ':'. Si lo tiene (did:plc/did:web), se toma todo como did (el handle se resuelve
    # luego desde la base de datos por el did).
    return resto, ""


class _AccionesMixin:
    def on_item_selected(self, index):
        f = self.f
        if index < 0 or index >= len(f.lista_items_db_ids):
            return
        db_id = f.lista_items_db_ids[index]
        modo = getattr(f, "_modo_lista", "")
        if modo == "bluesky_notifs":
            # Panel de detalles de una notificación: quién, qué hizo y el texto de la
            # publicación implicada (petición de usuarios: no se sabía de qué mensaje era).
            if not f.txt_detalles.IsShown():
                return
            fila = db.query_one("""
                SELECT reason, author_name, author_handle, indexed_at, content,
                       COALESCE(mod_json,''), COALESCE(author_did,'')
                FROM bluesky_notifications WHERE id = ?""", (db_id,))
            if not fila:
                return
            reason, autor, handle, fecha, contenido, mod_json, autor_did = fila
            _oc, contenido = self._moderar_notif(contenido, mod_json, autor_did,
                                                 contexto="detalle")
            from app.ui.main_frame import formatear_tiempo_relativo
            from .bluesky_controller_carga import motivo_legible
            txt = _("Autor: {0} (@{1})\n").format(autor or "", handle or "")
            txt += _("Motivo: {0}\n").format(motivo_legible(reason))
            txt += _("Fecha: {0}\n").format(formatear_tiempo_relativo(fecha) if fecha else "")
            if contenido:
                txt += "\n" + _("Publicación:") + "\n" + contenido
            else:
                txt += "\n" + _("(Sin publicación asociada)")
            f.txt_detalles.SetValue(txt)
            return
        if modo in ("bluesky_trends", "bluesky_packs", "bluesky_gestor"):
            return
        if modo == "bluesky_actores" and db_id.startswith("user:"):
            did, handle = parse_actor_dbid(db_id)
            r = db.query_one(
                "SELECT display_name, description, muted FROM bluesky_actors WHERE id = ?",
                (f"{f.active_bluesky_account_id}:{f.active_timeline_type}:{did}",))
            if r:
                nombre, bio, muted = r
                txt = f"{nombre or handle} (@{handle})\n"
                if muted:
                    txt += _("[Silenciado]\n")
                txt += "\n" + (bio or "")
                if f.txt_detalles.IsShown():
                    f.txt_detalles.SetValue(txt)
            return
        # Panel de detalles con MODERACIÓN en el contexto "detalle" (ver
        # bluesky_controller_moderacion._detalle_post_bsky).
        self._detalle_post_bsky(db_id)

    # ---------------- Activación (Intro) ----------------
    def on_item_activated(self, index):
        f = self.f
        if index < 0 or index >= len(f.lista_items_db_ids):
            return
        db_id = f.lista_items_db_ids[index]

        # Conversaciones: Intro abre la ventana de DM
        modo = getattr(f, "_modo_lista", "")
        if modo == "bluesky_convos" and db_id.startswith("convo:"):
            self.abrir_conversacion(db_id.split(":", 1)[1])
            return

        # Gestor de feeds/listas: Intro abre los posts del feed o lista
        if modo == "bluesky_gestor" and db_id.startswith(("feed:", "list:")):
            self.abrir_feed_o_lista(db_id)
            return

        # Modos especiales: cuentas, tendencias, starter packs -> abrir en web
        if modo == "bluesky_actores" and db_id.startswith("user:"):
            did, handle = parse_actor_dbid(db_id)
            # Si no hay handle válido (handle.invalid o vacío), abrir por DID (bsky.app lo resuelve).
            destino = handle if (handle and handle != "handle.invalid") else did
            abrir_url(f"https://bsky.app/profile/{destino}")
            return
        if modo == "bluesky_trends" and db_id.startswith("trend:"):
            # Intro abre la tendencia DENTRO de la app, como buffer de búsqueda (petición
            # de usuarios: antes solo se abría el navegador, que queda en el menú contextual).
            self._tendencia_a_buffer(index)
            return
        if modo == "bluesky_packs" and db_id.startswith("pack:"):
            speak(_("Starter pack."), interrupt=True)
            return

        # NOTIFICACIONES: Intro abre la publicación aludida (o el perfil, si el aviso es de
        # cuenta: seguidor nuevo, verificación...). Antes caía en la búsqueda de enlaces por
        # id de post, que nunca encontraba nada y solo decía "No hay enlaces".
        if modo == "bluesky_notifs":
            uri = self._uri_publicacion_notif(db_id)
            if uri:
                self.ver_hilo_uri(uri)
                return
            fila = db.query_one(
                "SELECT author_did FROM bluesky_notifications WHERE id = ?", (db_id,))
            if fila and fila[0]:
                self.ver_perfil(fila[0])
            else:
                speak(_("Esta notificación no tiene ninguna publicación asociada."), interrupt=True)
            return

        row = db.query_one("SELECT urls_json, uri FROM bluesky_posts WHERE id = ?", (db_id,))
        urls = []
        if row:
            if row[0]:
                try:
                    urls = json.loads(row[0])
                except Exception:
                    urls = []
            if not urls and row[1]:
                urls = [self._url_web(row[1], db_id)]
        if not urls:
            speak(_("No hay enlaces en esta publicación."), interrupt=True)
            event_bus.emit("sound_trigger", "warning")
            return
        from app.core.privacy import limpiar_url
        if len(urls) == 1:
            url = limpiar_url(urls[0])
            speak(_("Abriendo enlace: {0}").format(url), interrupt=True)
            abrir_url(url)
        else:
            dlg = wx.SingleChoiceDialog(f, _("Seleccione el enlace que desea abrir:"),
                                        _("Abrir Enlace"), urls)
            if dlg.ShowModal() == wx.ID_OK:
                abrir_url(limpiar_url(dlg.GetStringSelection()))
            dlg.Destroy()

    def _tendencia_a_buffer(self, idx):
        """Abre una tendencia DENTRO de la app como buffer de búsqueda (petición de
        usuarios): las tendencias de Bluesky enlazan a una búsqueda (/search?q=...), así
        que se usa esa consulta (o el propio tema) con el buffer search:, que ya se puede
        ocultar, eliminar y reordenar como cualquier búsqueda guardada."""
        trends = getattr(self, "_trends_cache", [])
        if idx < 0 or idx >= len(trends):
            return
        t = trends[idx]
        termino = ""
        link = t.get("link") or ""
        if "q=" in link:
            try:
                from urllib.parse import urlparse, parse_qs
                termino = (parse_qs(urlparse(link).query).get("q") or [""])[0]
            except Exception:
                termino = ""
        termino = (termino or t.get("topic") or "").strip()
        if not termino:
            speak(_("No se pudo identificar el tema de la tendencia."), interrupt=True)
            return
        speak(_("Abriendo la tendencia {0} como buffer...").format(termino), interrupt=True)
        self.crear_buffer_busqueda(termino)

    def _tendencia_en_navegador(self, idx):
        """Abre la tendencia en bsky.app (el comportamiento clásico, ahora en el menú)."""
        trends = getattr(self, "_trends_cache", [])
        if idx < 0 or idx >= len(trends):
            return
        destino = trends[idx].get("link") or ""
        if destino.startswith("/"):
            destino = "https://bsky.app" + destino
        abrir_url(destino or "https://bsky.app")

    def _url_web(self, uri, db_id):
        """Convierte un at:// URI en una URL web de bsky.app."""
        try:
            # at://did/app.bsky.feed.post/<rkey>
            partes = uri.split("/")
            rkey = partes[-1]
            row = db.query_one("SELECT author_handle FROM bluesky_posts WHERE id = ?", (db_id,))
            handle = row[0] if row else ""
            if handle:
                return f"https://bsky.app/profile/{handle}/post/{rkey}"
        except Exception:
            pass
        return uri

    def _uri_publicacion_notif(self, db_id):
        """URI de la publicación a la que se refiere una notificación (o "" si no hay ninguna).

        El `uri` de la notificación es el REGISTRO que la genera (el "me gusta", el repost, el
        seguimiento...), así que pedir su conversación devolvía el error 400 NotFound "Post not
        found" que reportaron los usuarios. La columna subject_uri guarda ya la publicación
        buena; para las notificaciones guardadas ANTES de esa columna se deduce aquí (y se
        guarda, para no repetirlo)."""
        row = db.query_one(
            "SELECT subject_uri, reason, uri, reason_subject FROM bluesky_notifications WHERE id = ?",
            (db_id,))
        if not row:
            return ""
        subject_uri, reason, uri, reason_subject = row
        if subject_uri:
            return subject_uri
        from app.services.bluesky.bluesky_common import uri_post_de_notificacion
        subject_uri = uri_post_de_notificacion(reason or "", uri or "", reason_subject or "")
        if subject_uri:
            try:
                db.execute("UPDATE bluesky_notifications SET subject_uri = ? WHERE id = ?",
                           (subject_uri, db_id))
            except Exception as e:
                log.debug(f"No se pudo guardar el asunto de la notificación: {e}")
        return subject_uri

    # ---------------- Menú contextual ----------------
    def on_context_menu(self, menu, idx):
        f = self.f
        if idx < 0 or idx >= len(f.lista_items_db_ids):
            return
        modo = getattr(f, "_modo_lista", "")
        db_id = f.lista_items_db_ids[idx]

        # Menú del buffer de NOTIFICACIONES: ir a la publicación y acciones sobre el autor.
        if modo == "bluesky_notifs":
            row = db.query_one(
                "SELECT author_did FROM bluesky_notifications WHERE id = ?", (db_id,))
            if not row:
                return
            autor_did = row[0]
            # La publicación aludida NO es el `uri` de la notificación (ver
            # _uri_publicacion_notif): usarlo daba "Post not found" en los "me gusta".
            post_uri = self._uri_publicacion_notif(db_id)
            if post_uri:
                it = menu.Append(wx.ID_ANY, _("Ver la publicación"))
                f.Bind(wx.EVT_MENU, lambda e, u=post_uri: self.ver_hilo_uri(u), it)
            if autor_did:
                n_sigo, n_bloq, n_silenciado = self._estado_actor(autor_did)
                it = menu.Append(wx.ID_ANY, _("Ver perfil del autor"))
                f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.ver_perfil(a), it)
                it = menu.Append(wx.ID_ANY, _("Dejar de seguir autor") if n_sigo else _("Seguir autor"))
                f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.seguir_autor(a), it)
                it = menu.Append(wx.ID_ANY, _("Mensaje directo al autor"))
                f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.mensaje_directo_a(a), it)
                if n_silenciado:
                    it = menu.Append(wx.ID_ANY, _("Quitar silencio al autor"))
                    f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.accion_cuenta(a, "unmute"), it)
                else:
                    it = menu.Append(wx.ID_ANY, _("Silenciar autor"))
                    f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.accion_cuenta(a, "mute"), it)
                    it = menu.Append(wx.ID_ANY, _("Silenciar solo sus reposts"))
                    f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.accion_cuenta(a, "mute_reposts"), it)
                    it = menu.Append(wx.ID_ANY, _("Silenciar solo sus citas"))
                    f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.accion_cuenta(a, "mute_citas"), it)
                if n_bloq:
                    it = menu.Append(wx.ID_ANY, _("Desbloquear autor"))
                    f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.accion_cuenta(a, "unblock"), it)
                else:
                    it = menu.Append(wx.ID_ANY, _("Bloquear autor"))
                    f.Bind(wx.EVT_MENU, lambda e, a=autor_did: self.accion_cuenta(a, "block"), it)
            menu.AppendSeparator()
            it = menu.Append(wx.ID_ANY, _("Ver preferencias de notificación..."))
            f.Bind(wx.EVT_MENU, lambda e: self.ver_preferencias_notif(), it)
            it = menu.Append(wx.ID_ANY, _("Ver cuentas a cuya actividad sigo..."))
            f.Bind(wx.EVT_MENU, lambda e: self.ver_suscripciones_actividad(), it)
            return

        # Menú de TENDENCIAS y STARTER PACKS: abrir el elemento.
        if modo == "bluesky_trends":
            it = menu.Append(wx.ID_ANY, _("Abrir la tendencia como buffer de búsqueda"))
            f.Bind(wx.EVT_MENU, lambda e, i=idx: self._tendencia_a_buffer(i), it)
            it = menu.Append(wx.ID_ANY, _("Abrir esta tendencia en el navegador"))
            f.Bind(wx.EVT_MENU, lambda e, i=idx: self._tendencia_en_navegador(i), it)
            return
        if modo == "bluesky_packs":
            it = menu.Append(wx.ID_ANY, _("Abrir este starter pack en el navegador"))
            f.Bind(wx.EVT_MENU, lambda e, i=idx: self.on_item_activated(i), it)
            return

        # Menú del GESTOR de feeds y listas guardados.
        if modo == "bluesky_gestor":
            it = menu.Append(wx.ID_ANY, _("Abrir"))
            f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.abrir_feed_o_lista(d), it)
            if db_id.startswith("list:"):
                it = menu.Append(wx.ID_ANY, _("Gestionar miembros de la lista..."))
                f.Bind(wx.EVT_MENU, lambda e: self.gestionar_miembros_lista(), it)
            return

        # Menú del buffer de CONVERSACIONES (DMs)
        if modo == "bluesky_convos" and db_id.startswith("convo:"):
            convo_id = db_id.split(":", 1)[1]
            row = db.query_one("SELECT muted FROM bluesky_convos WHERE account_id = ? AND convo_id = ?",
                               (f.active_bluesky_account_id, convo_id))
            muted = bool(row[0]) if row else False
            it = menu.Append(wx.ID_ANY, _("Abrir conversación"))
            f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self.abrir_conversacion(c), it)
            it = menu.Append(wx.ID_ANY, _("Marcar como leída"))
            f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self._convo_accion(c, "read"), it)
            it = menu.Append(wx.ID_ANY, _("Ver miembros de la conversación"))
            f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self.ver_miembros_convo(c), it)
            it = menu.Append(wx.ID_ANY, _("Quitar silencio") if muted else _("Silenciar"))
            f.Bind(wx.EVT_MENU, lambda e, c=convo_id, m=muted: self._convo_accion(c, "unmute" if m else "mute"), it)
            it = menu.Append(wx.ID_ANY, _("Abandonar conversación"))
            f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self._convo_accion(c, "leave"), it)
            # GRUPOS: Bluesky admite conversaciones de grupo con administracion propia.
            es_grupo = False
            try:
                fila_g = db.query_one(
                    "SELECT is_group FROM bluesky_convos WHERE account_id = ? AND convo_id = ?",
                    (f.active_bluesky_account_id, convo_id))
                es_grupo = bool(fila_g[0]) if fila_g else False
            except Exception:
                es_grupo = False
            if es_grupo:
                menu.AppendSeparator()
                it = menu.Append(wx.ID_ANY, _("Anadir cuentas al grupo..."))
                f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self.grupo_anadir_miembros(c), it)
                it = menu.Append(wx.ID_ANY, _("Expulsar una cuenta del grupo..."))
                f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self.grupo_expulsar_miembro(c), it)
                it = menu.Append(wx.ID_ANY, _("Cambiar el nombre del grupo..."))
                f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self.grupo_renombrar(c), it)
                it = menu.Append(wx.ID_ANY, _("Enlace de invitacion..."))
                f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self.grupo_enlace(c), it)
                it = menu.Append(wx.ID_ANY, _("Solicitudes para entrar..."))
                f.Bind(wx.EVT_MENU, lambda e, c=convo_id: self.grupo_solicitudes(c), it)
            menu.AppendSeparator()
            it = menu.Append(wx.ID_ANY, _("Ver solicitudes de mensajes..."))
            f.Bind(wx.EVT_MENU, lambda e: self.ver_solicitudes_mensajes(), it)
            return

        # Menú del modo CUENTAS (seguidores/siguiendo/bloqueados/silenciados). Es DINÁMICO según el
        # estado real (cacheado): cada opción dice directamente lo que hará (Dejar de seguir,
        # Desbloquear, Quitar silencio...), nunca el ambiguo "seguir / dejar de seguir".
        if modo == "bluesky_actores" and db_id.startswith("user:"):
            did, handle = parse_actor_dbid(db_id)
            sigo, bloqueado, silenciado = self._estado_actor(did)
            # El propio buffer es una pista fiable del estado (estás en él porque sigues/bloqueas/
            # silencias a esa cuenta), por si el viewer no se hubiera cacheado.
            sigo = sigo or f.active_timeline_type in ("following", "follows")
            bloqueado = bloqueado or f.active_timeline_type == "blocks"
            silenciado = silenciado or f.active_timeline_type == "mutes"
            it = menu.Append(wx.ID_ANY, _("Ver perfil"))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.ver_perfil(a), it)
            it = menu.Append(wx.ID_ANY, _("Crear un buffer con las publicaciones de esta cuenta"))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.crear_buffer_usuario(a), it)
            it = menu.Append(wx.ID_ANY, _("Mensaje directo"))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.mensaje_directo_a(a), it)
            # Seguir / dejar de seguir SEGÚN el estado.
            if sigo:
                it = menu.Append(wx.ID_ANY, _("Dejar de seguir"))
                f.Bind(wx.EVT_MENU, lambda e, a=did: self.seguir_autor(a), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Seguir"))
                f.Bind(wx.EVT_MENU, lambda e, a=did: self.seguir_autor(a), it)
            # Silenciar / quitar silencio SEGÚN el estado.
            if silenciado:
                it = menu.Append(wx.ID_ANY, _("Quitar silencio"))
                f.Bind(wx.EVT_MENU, lambda e, a=did: self.accion_cuenta(a, "unmute"), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Silenciar"))
                f.Bind(wx.EVT_MENU, lambda e, a=did: self.accion_cuenta(a, "mute"), it)
            # Bloquear / desbloquear SEGÚN el estado.
            if bloqueado:
                it = menu.Append(wx.ID_ANY, _("Desbloquear"))
                f.Bind(wx.EVT_MENU, lambda e, a=did: self.accion_cuenta(a, "unblock"), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Bloquear"))
                f.Bind(wx.EVT_MENU, lambda e, a=did: self.accion_cuenta(a, "block"), it)
            menu.AppendSeparator()
            it = menu.Append(wx.ID_ANY, _("Añadir a una lista..."))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.anadir_autor_a_lista(a), it)
            it = menu.Append(wx.ID_ANY, _("Avisarme cuando publique"))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.suscribir_actividad(a), it)
            it = menu.Append(wx.ID_ANY, _("Cuentas similares"))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.cuentas_similares(a), it)
            it = menu.Append(wx.ID_ANY, _("Seguidores en común"))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.seguidores_en_comun(a), it)
            it = menu.Append(wx.ID_ANY, _("Ver sus feeds personalizados"))
            f.Bind(wx.EVT_MENU, lambda e, a=did: self.ver_feeds_de(a), it)
            return

        row = db.query_one(
            "SELECT author_did, author_handle, liked_uri, reposted_uri, bookmarked, "
            "reply_root_uri, uri, cid FROM bluesky_posts WHERE id = ?", (db_id,))
        if not row:
            return
        author_did, handle, liked_uri, reposted_uri, bookmarked, reply_root_uri, uri, cid = row
        # Determinar si el post es propio. _yo() puede necesitar el cliente y, si la
        # sesión está caducada o hay límite de peticiones, lanzaría una excepción
        # que abortaría la construcción del menú (dejándolo sin aparecer). Por eso
        # se protege: si no se puede resolver, se asume que no es propio.
        try:
            yo = bluesky_service._yo(f.active_bluesky_account_id)
            es_propio = (author_did == yo.get("did"))
        except Exception:
            es_propio = False
        root_uri = reply_root_uri or uri   # raíz del hilo (o el propio post)

        it = menu.Append(wx.ID_ANY, _("Responder"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.responder(d), it)
        it = menu.Append(wx.ID_ANY, _("Citar"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.citar(d), it)
        it = menu.Append(wx.ID_ANY, _("Quitar me gusta") if liked_uri else _("Me gusta"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id, on=bool(liked_uri): self.accion(d, "unlike" if on else "like"), it)
        it = menu.Append(wx.ID_ANY, _("Deshacer repost") if reposted_uri else _("Repostear"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id, on=bool(reposted_uri): (self.accion(d, "unrepost") if on else self.repostear(d)), it)
        it = menu.Append(wx.ID_ANY, _("Quitar marcador") if bookmarked else _("Añadir a marcadores"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id, on=bool(bookmarked): self.marcador(d, not on), it)
        it = menu.Append(wx.ID_ANY, _("Ver conversación"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.ver_hilo(d), it)
        it = menu.Append(wx.ID_ANY, _("Abrir la conversación como buffer"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.crear_buffer_conversacion(d), it)
        # Moderación: silenciar/quitar silencio del hilo, ocultar para mí, mostrar de todos
        # modos (bluesky_controller_moderacion).
        self._menu_moderacion_post(menu, db_id, uri, root_uri)
        it = menu.Append(wx.ID_ANY, _("Ver quién dio me gusta"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.ver_interacciones(d, "likes"), it)
        it = menu.Append(wx.ID_ANY, _("Ver quién la reposteó"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.ver_interacciones(d, "reposts"), it)
        it = menu.Append(wx.ID_ANY, _("Ver quién la citó"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.ver_interacciones(d, "quotes"), it)

        menu.AppendSeparator()
        # Acciones sobre el AUTOR, dinámicas según el estado conocido (si no es propio).
        a_sigo, a_bloq, a_silenciado = self._estado_actor(author_did)
        it = menu.Append(wx.ID_ANY, _("Ver perfil del autor"))
        f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.ver_perfil(a), it)
        it = menu.Append(wx.ID_ANY, _("Crear un buffer con las publicaciones de esta cuenta"))
        f.Bind(wx.EVT_MENU, lambda e, a=author_did, h=handle: self.crear_buffer_usuario(a, h), it)
        it = menu.Append(wx.ID_ANY, _("Crear un buffer con las publicaciones de un usuario mencionado..."))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.crear_buffer_usuario_mencionado(d), it)
        it = menu.Append(wx.ID_ANY, _("Mensaje directo al autor"))
        f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.mensaje_directo_a(a), it)
        if not es_propio:
            it = menu.Append(wx.ID_ANY, _("Dejar de seguir autor") if a_sigo else _("Seguir autor"))
            f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.seguir_autor(a), it)
        it = menu.Append(wx.ID_ANY, _("Añadir el autor a una lista..."))
        f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.anadir_autor_a_lista(a), it)
        it = menu.Append(wx.ID_ANY, _("Avisarme cuando el autor publique"))
        f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.suscribir_actividad(a), it)
        it = menu.Append(wx.ID_ANY, _("Cuentas similares al autor"))
        f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.cuentas_similares(a), it)
        it = menu.Append(wx.ID_ANY, _("Seguidores en común con el autor"))
        f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.seguidores_en_comun(a), it)
        if not es_propio:
            if a_silenciado:
                it = menu.Append(wx.ID_ANY, _("Quitar silencio al autor"))
                f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.accion_cuenta(a, "unmute"), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Silenciar autor"))
                f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.accion_cuenta(a, "mute"), it)
            if a_bloq:
                it = menu.Append(wx.ID_ANY, _("Desbloquear autor"))
                f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.accion_cuenta(a, "unblock"), it)
            else:
                it = menu.Append(wx.ID_ANY, _("Bloquear autor"))
                f.Bind(wx.EVT_MENU, lambda e, a=author_did: self.accion_cuenta(a, "block"), it)
        if not es_propio:
            it = menu.Append(wx.ID_ANY, _("Reportar publicación..."))
            f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.reportar_post(d), it)
        if es_propio:
            it = menu.Append(wx.ID_ANY, _("Fijar esta publicación en tu perfil"))
            f.Bind(wx.EVT_MENU, lambda e, u=uri, c=cid: self.fijar_post(u, c, True), it)
            it = menu.Append(wx.ID_ANY, _("Quitar la publicación fijada de tu perfil"))
            f.Bind(wx.EVT_MENU, lambda e, u=uri, c=cid: self.fijar_post(u, c, False), it)
            it = menu.Append(wx.ID_ANY, _("Borrar post"))
            f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.borrar(d), it)

        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Abrir en el navegador"))
        f.Bind(wx.EVT_MENU, lambda e, i=idx: self.on_item_activated(i), it)
        it = menu.Append(wx.ID_ANY, _("Copiar enlace"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.copiar_enlace(d), it)
        if self._media_video_url(db_id):
            it = menu.Append(wx.ID_ANY, _("Reproducir vídeo de la publicación"))
            f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.reproducir_video_nativo(d), it)
        it = menu.Append(wx.ID_ANY, _("Reproducir audio del enlace (YouTube, SoundCloud...)"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.reproducir_audio(d), it)
        menu.AppendSeparator()
        it = menu.Append(wx.ID_ANY, _("Traducir con la app"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.traducir(d), it)
        it = menu.Append(wx.ID_ANY, _("Resumir con IA"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.resumir(d), it)
        it = menu.Append(wx.ID_ANY, _("Leer en voz alta (TTS)"))
        f.Bind(wx.EVT_MENU, lambda e, d=db_id: self.leer_tts(d), it)

    # ---------------- Botonera de acciones (ventana principal) ----------------
    def botones_botonera(self, db_id):
        """Botones directos para la botonera bajo la lista: solo en buffers de POSTS (en el resto de
        modos -notificaciones, tendencias, cuentas, conversaciones...- devuelve [] y la botonera usa
        un único botón "Acciones..." que abre el menú contextual). Las etiquetas reflejan el estado
        (me gusta / repost) igual que el menú."""
        modo = getattr(self.f, "_modo_lista", "")
        if modo in ("bluesky_notifs", "bluesky_trends", "bluesky_packs",
                    "bluesky_gestor", "bluesky_actores", "bluesky_convos"):
            return []
        row = db.query_one(
            "SELECT liked_uri, reposted_uri FROM bluesky_posts WHERE id = ?", (db_id,))
        if not row:
            return []
        liked, reposted = bool(row[0]), bool(row[1])
        return [
            (_("Responder"), lambda d=db_id: self.responder(d)),
            (_("Quitar me gusta") if liked else _("Me gusta"),
             lambda d=db_id, on=liked: self.accion(d, "unlike" if on else "like")),
            (_("Deshacer repost") if reposted else _("Repostear"),
             lambda d=db_id, on=reposted: (self.accion(d, "unrepost") if on else self.repostear(d))),
            (_("Conversación"), lambda d=db_id: self.ver_hilo(d)),
        ]

    # ---------------- Acciones ----------------
    def _texto_post(self, db_id):
        row = db.query_one("SELECT content FROM bluesky_posts WHERE id = ?", (db_id,))
        return row[0] if row else ""

    def _uri_post(self, db_id):
        # En el buffer de NOTIFICACIONES la fila es una notificación, no un post: los atajos
        # globales (ver conversación, responder, citar...) actúan sobre la publicación a la que
        # se refiere. Antes buscaban el id en bluesky_posts, no lo encontraban y "Ver
        # conversación" no hacía nada o "Responder" abría una publicación nueva.
        if getattr(self.f, "_modo_lista", "") == "bluesky_notifs":
            return self._uri_publicacion_notif(db_id) or None
        row = db.query_one("SELECT uri FROM bluesky_posts WHERE id = ?", (db_id,))
        return row[0] if row else None

    def responder(self, db_id):
        uri = self._uri_post(db_id)
        # Título con el nombre de a quién respondes y, si se pidió en Preferencias, el texto
        # empezando por las @menciones del autor y de quien él menciona (estilo Mastodon).
        titulo, texto_inicial = _("Responder en Bluesky"), ""
        fila = db.query_one("SELECT author_name, author_handle, content FROM bluesky_posts "
                            "WHERE uri = ? LIMIT 1", (uri,)) if uri else None
        if fila:
            nombre, handle, contenido = fila
            titulo = _("Responder a {0}").format(nombre or handle)
            if config.get("bluesky", "mencionar_al_responder", False):
                from app.core.at_lectura import menciones_al_responder, mi_handle
                texto_inicial = menciones_al_responder(
                    handle, contenido, mi_handle(bluesky_service, self.f.active_bluesky_account_id))
        dlg = BlueskyComposeDialog(self.f, self.f.active_bluesky_account_id,
                                   reply_to_uri=uri, titulo=titulo, texto_inicial=texto_inicial)
        dlg.ShowModal()
        dlg.Destroy()

    def citar(self, db_id):
        uri = self._uri_post(db_id)
        dlg = BlueskyComposeDialog(self.f, self.f.active_bluesky_account_id,
                                   quote_uri=uri, titulo=_("Citar en Bluesky"))
        dlg.ShowModal()
        dlg.Destroy()

    def repostear(self, db_id):
        """Repostea según el modo configurado (Preferencias -> Bluesky): preguntar,
        repostear directamente o citar siempre. Equivalente al modo de impulso de Mastodon."""
        modo = config.get("bluesky", "repost_mode", "ask")
        if modo == "ask":
            # Botones EXPLÍCITOS, igual que "Impulsar" en Mastodon: antes era Sí/No/Cancelar y
            # había que recordar que "Sí" reposteaba y "No" citaba.
            from .accessible_controls import elegir_opcion
            r = elegir_opcion(self.f, _("¿Cómo quieres compartir esta publicación?"),
                              _("Repostear publicación"), [_("&Repostear"), _("&Citar")])
            if r is None:
                return
            modo = "repost" if r == 0 else "quote"
        if modo == "quote":
            self.citar(db_id)
            return
        self.accion(db_id, "repost")

    # --- Descubrimiento social (APIs de atproto) ---
    def suscribir_actividad(self, did):
        """Suscribe la actividad de una cuenta: te avisa cuando publique."""
        def _cb(res, err):
            if err:
                notificar(_("No se pudo suscribir: {0}").format(err))
            else:
                speak(_("Te avisaré cuando esta cuenta publique."), interrupt=True)
        bluesky_service.suscribir_actividad_async(
            self.f.active_bluesky_account_id, did, posts=True, callback=_cb)

    def _mostrar_lista_actores(self, titulo, actores, vacio):
        if not actores:
            speak(vacio, interrupt=True)
            return
        texto = "\n".join(
            "{0} (@{1})".format(a.get("display_name") or a.get("handle"), a.get("handle"))
            for a in actores)
        self.f._mostrar_texto_largo(titulo, texto)

    def cuentas_similares(self, did):
        """Cuentas parecidas a una dada (sugerencias de seguimiento)."""
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self._mostrar_lista_actores(_("Cuentas similares"), res or [], _("No hay sugerencias."))
        bluesky_service.sugerencias_seguir_actor_async(self.f.active_bluesky_account_id, did, callback=_cb)

    def seguidores_en_comun(self, did):
        """Personas que tú sigues y que también siguen a esta cuenta."""
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self._mostrar_lista_actores(_("Seguidores en común"), res or [], _("No hay seguidores en común."))
        bluesky_service.seguidores_conocidos_async(self.f.active_bluesky_account_id, did, callback=_cb)

    def ver_feeds_de(self, did):
        """Muestra los feeds personalizados creados por una cuenta."""
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("Esta cuenta no ha creado feeds."), interrupt=True)
                return
            texto = "\n".join("{0}: {1}".format(fe.get("name", ""), fe.get("description", "")) for fe in res)
            self.f._mostrar_texto_largo(_("Feeds de la cuenta"), texto)
        bluesky_service.feeds_de_actor_async(self.f.active_bluesky_account_id, did, callback=_cb)

    def ver_miembros_convo(self, convo_id):
        """Muestra los miembros de una conversación (DM, sobre todo de grupo)."""
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self._mostrar_lista_actores(_("Miembros de la conversación"), res or [], _("Sin miembros."))
        bluesky_service.miembros_convo_async(self.f.active_bluesky_account_id, convo_id, callback=_cb)

    def ver_solicitudes_mensajes(self):
        """Lista las solicitudes de mensaje (DMs de gente que no sigues) y permite aceptar y abrir."""
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("No tienes solicitudes de mensaje."), interrupt=True)
                return
            opciones = [s["title"] for s in res]
            sel = wx.GetSingleChoice(
                _("Solicitudes de mensaje. Elige una para aceptarla y abrir la conversación:"),
                _("Solicitudes de mensajes"), opciones, self.f)
            if not sel:
                return
            s = next((x for x in res if x["title"] == sel), None)
            if not s:
                return

            def _cb2(r2, e2):
                if e2:
                    notificar(_("No se pudo aceptar: {0}").format(e2))
                    return
                self.abrir_conversacion(s["convo_id"])
            bluesky_service.aceptar_solicitud_convo_async(
                self.f.active_bluesky_account_id, s["convo_id"], callback=_cb2)
        bluesky_service.solicitudes_mensajes_async(self.f.active_bluesky_account_id, callback=_cb)

    def ver_preferencias_notif(self):
        """Muestra (solo lectura) las preferencias de notificación del servidor."""
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            try:
                prefs = getattr(res, "preferences", res)
                d = prefs.model_dump() if hasattr(prefs, "model_dump") else dict(prefs)
                lineas = []
                for k, v in d.items():
                    if v is None:
                        continue
                    if isinstance(v, dict):
                        estados = ", ".join("{0}={1}".format(kk, vv) for kk, vv in v.items() if vv is not None)
                        lineas.append("{0}: {1}".format(k, estados))
                    else:
                        lineas.append("{0}: {1}".format(k, v))
                texto = "\n".join(lineas) or _("Sin preferencias configuradas.")
            except Exception:
                texto = str(res)
            self.f._mostrar_texto_largo(_("Preferencias de notificación de Bluesky"), texto)
        bluesky_service.preferencias_notif_async(self.f.active_bluesky_account_id, callback=_cb)

    def ver_suscripciones_actividad(self):
        """Lista las cuentas a cuya actividad estás suscrito (te avisan cuando publican)."""
        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self._mostrar_lista_actores(_("Cuentas a cuya actividad sigues"), res or [],
                                        _("No sigues la actividad de ninguna cuenta."))
        bluesky_service.listar_suscripciones_actividad_async(self.f.active_bluesky_account_id, callback=_cb)

    def accion(self, db_id, accion):
        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
            else:
                speak(_("Hecho."), interrupt=True)
                self.cargar_listado()
        bluesky_service.ejecutar_accion_post_async(
            self.f.active_bluesky_account_id, db_id, accion, callback=_cb)

    def marcador(self, db_id, añadir):
        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
            else:
                notificar(_("Añadido a marcadores.") if añadir else _("Marcador quitado."))
                if self.f.active_timeline_type == "bookmarks":
                    self._cargar_posts("bookmarks")
        if añadir:
            bluesky_service.crear_bookmark_async(self.f.active_bluesky_account_id, db_id, callback=_cb)
        else:
            bluesky_service.borrar_bookmark_async(self.f.active_bluesky_account_id, db_id, callback=_cb)

    def silenciar_hilo(self, root_uri):
        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
            else:
                notificar(_("Hilo silenciado."))
        bluesky_service.silenciar_hilo_async(self.f.active_bluesky_account_id, root_uri, True, callback=_cb)

    def reportar_post(self, db_id):
        motivos = [
            ("spam", _("Spam")),
            ("violation", _("Infracción de normas")),
            ("misleading", _("Engañoso")),
            ("sexual", _("Contenido sexual no etiquetado")),
            ("rude", _("Comportamiento grosero")),
            ("other", _("Otro")),
        ]
        etiquetas = [m[1] for m in motivos]
        sel = wx.GetSingleChoice(_("Motivo del reporte:"), _("Reportar publicación"), etiquetas, self.f)
        if not sel:
            return
        reason = motivos[etiquetas.index(sel)][0]
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Comentario (opcional):"), _("Reportar"), multiline=True)
        comentario = ""
        if dlg.ShowModal() == wx.ID_OK:
            comentario = dlg.GetValue().strip()
        dlg.Destroy()

        def _cb(res, error):
            if error:
                notificar(_("Error al reportar: {0}").format(error))
            else:
                notificar(_("Reporte enviado. Gracias."))
        bluesky_service.reportar_async(self.f.active_bluesky_account_id, reason, comentario,
                                       post_db_id=db_id, callback=_cb)

    def ver_hilo(self, db_id):
        uri = self._uri_post(db_id)
        if not uri:
            speak(_("Esta fila no tiene ninguna publicación asociada."), interrupt=True)
            return
        self.ver_hilo_uri(uri)

    def ver_hilo_uri(self, uri):
        """Abre el VISOR DE CONVERSACIÓN completo (paridad con Mastodon, petición de
        usuarios): lista navegable con detalle, acciones sobre cada mensaje, abrir como
        buffer, resumir con IA, traducir y exportar. Antes era un volcado de texto."""
        if not uri:
            return
        from .bluesky_thread_viewer import BlueskyThreadViewerDialog
        dlg = BlueskyThreadViewerDialog(self.f, self.f.active_bluesky_account_id, uri)
        dlg.ShowModal()
        dlg.Destroy()

    def ver_perfil(self, did):
        def _cb(p, error):
            if error or not p:
                notificar(_("No se pudo cargar el perfil."))
                return
            txt = _("{display} (@{handle})\n{followers} seguidores, {following} siguiendo, "
                    "{posts} publicaciones.\n\n{bio}").format(
                display=p.get("display_name") or p.get("handle"), handle=p.get("handle"),
                followers=p.get("followers"), following=p.get("following"),
                posts=p.get("posts"), bio=p.get("description") or "")
            self.f._mostrar_texto_largo(_("Perfil de Bluesky"), txt)
        bluesky_service.obtener_perfil_async(self.f.active_bluesky_account_id, did, callback=_cb)

    def seguir_autor(self, actor):
        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
            elif res:
                speak(_("Ahora sigues a este usuario.") if res.get("seguido")
                      else _("Has dejado de seguir a este usuario."), interrupt=True)
        bluesky_service.seguir_autor_async(self.f.active_bluesky_account_id, actor, callback=_cb)

    def _estado_actor(self, did):
        """(sigo, bloqueado, silenciado) de una cuenta según la caché local de actores. Se usa para
        que el menú diga directamente la acción correcta (Dejar de seguir, Desbloquear...)."""
        row = db.query_one(
            "SELECT MAX(CASE WHEN following_uri IS NOT NULL AND following_uri != '' THEN 1 ELSE 0 END), "
            "MAX(CASE WHEN blocking_uri IS NOT NULL AND blocking_uri != '' THEN 1 ELSE 0 END), "
            "MAX(COALESCE(muted, 0)) "
            "FROM bluesky_actors WHERE account_id = ? AND did = ?",
            (self.f.active_bluesky_account_id, did))
        if not row:
            return (False, False, False)
        return (bool(row[0]), bool(row[1]), bool(row[2]))

    def accion_cuenta(self, did, accion):
        # Para deshacer (unfollow/unblock) hace falta el URI del registro; se recupera de la caché.
        following_uri = blocking_uri = ""
        if accion in ("unfollow", "unblock"):
            row = db.query_one(
                "SELECT following_uri, blocking_uri FROM bluesky_actors "
                "WHERE account_id = ? AND did = ? AND (COALESCE(following_uri,'') != '' "
                "OR COALESCE(blocking_uri,'') != '') LIMIT 1",
                (self.f.active_bluesky_account_id, did))
            if row:
                following_uri = row[0] or ""
                blocking_uri = row[1] or ""

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
                return
            # Confirmar CON el estado real del servidor (antes solo "Hecho." y no quedaba claro
            # si el bloqueo era efectivo). El menú ya ofrece la acción contraria.
            est = res if isinstance(res, dict) else {}
            quien = est.get("handle") and "@" + est["handle"] or _("La cuenta")
            mensajes = {
                "block": (_("{0} está bloqueada.") if est.get("blocking_uri", True)
                          else _("No se pudo confirmar el bloqueo de {0}.")),
                "unblock": _("{0} ya no está bloqueada."),
                "follow": _("Ahora sigues a {0}."),
                "unfollow": _("Has dejado de seguir a {0}."),
                "mute": _("{0} está silenciada."),
                "mute_reposts": _("Silenciados los reposts de {0}."),
                "mute_citas": _("Silenciadas las citas de {0}."),
                "unmute": _("{0} ya no está silenciada."),
            }
            notificar(mensajes.get(accion, _("Hecho: {0}.")).format(quien))
            # (La caché de actores ya está al día: Bloqueados/Silenciados/Siguiendo lo muestran al
            # volver a entrar; no se recarga aquí para no mover el foco.)
        bluesky_service.accion_cuenta_async(
            self.f.active_bluesky_account_id, did, accion,
            following_uri=following_uri, blocking_uri=blocking_uri, callback=_cb)

    def _convo_accion(self, convo_id, accion):
        acc = self.f.active_bluesky_account_id

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
            else:
                speak(_("Hecho."), interrupt=True)
                if self.f.active_timeline_type == "convos":
                    self._cargar_convos()
        if accion == "read":
            bluesky_service.marcar_convo_leido_async(acc, convo_id, callback=_cb)
        elif accion == "mute":
            bluesky_service.silenciar_convo_async(acc, convo_id, True, callback=_cb)
        elif accion == "unmute":
            bluesky_service.silenciar_convo_async(acc, convo_id, False, callback=_cb)
        elif accion == "leave":
            if confirmar(self.f, _("¿Abandonar esta conversación?"), _("Confirmar")):
                bluesky_service.salir_convo_async(acc, convo_id, callback=_cb)

    def borrar(self, db_id):
        if not confirmar(self.f, _("¿Borrar este post definitivamente?"), _("Confirmar")):
            return
        uri = self._uri_post(db_id)

        def _cb(res, error):
            if error:
                notificar(_("Error al borrar: {0}").format(error))
            else:
                notificar(_("Post borrado."))
                self.cargar_listado()
        bluesky_service.borrar_post_async(self.f.active_bluesky_account_id, uri, callback=_cb)

    def fijar_post(self, uri, cid, fijar):
        """Fija o quita esta publicación tuya del perfil (pinnedPost)."""
        def _cb(res, error):
            if error:
                notificar(_("No se pudo cambiar la publicación fijada: {0}").format(error))
            elif fijar:
                notificar(_("Publicación fijada en tu perfil."))
            else:
                notificar(_("Publicación quitada de tu perfil."))
        bluesky_service.fijar_post_async(self.f.active_bluesky_account_id, uri, cid, fijar, callback=_cb)

    def copiar_enlace(self, db_id):
        url = self._url_web(self._uri_post(db_id) or "", db_id)
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(url))
            wx.TheClipboard.Close()
            notificar(_("Enlace copiado."))

    def _media_video_url(self, db_id):
        """Devuelve la URL del vídeo nativo embebido del post, o None."""
        row = db.query_one("SELECT media_json FROM bluesky_posts WHERE id = ?", (db_id,))
        if not row or not row[0]:
            return None
        try:
            for att in json.loads(row[0]):
                if att.get("type") == "video" and att.get("url"):
                    return att["url"]
        except Exception:
            pass
        return None

    def reproducir_video_nativo(self, db_id):
        """Reproduce el audio del vídeo embebido del post con el reproductor.

        El vídeo de Bluesky es un streaming HLS (lista .m3u8). BASS no reproduce
        HLS directamente y tampoco es un servicio tipo YouTube, así que dejamos
        que ffmpeg (incluido) lea el HLS desde la URL y extraiga el audio a un WAV
        local, que sí se puede reproducir.
        """
        url = self._media_video_url(db_id)
        if not url:
            speak(_("Esta publicación no tiene vídeo."), interrupt=True)
            return
        # Multimedia etiquetada (difuminada): confirmar antes de reproducir (B6).
        if not self._confirmar_media_moderada(db_id):
            return
        speak(_("Cargando audio del vídeo..."), interrupt=True)

        def _bg(ctx):
            from app.services.audio import microphone
            return microphone.convertir_a_wav_si_necesario(url)

        def _fin(res, err):
            if err or not res or res == url:
                notificar(_("No se pudo reproducir el vídeo: {0}").format(err or ""))
                return
            from app.services.media_player.player_service import player_service
            player_service.cargar_y_reproducir(
                res, _("Vídeo de Bluesky"), service_origin="bluesky",
                account_origin_id=self.f.active_bluesky_account_id)
            speak(_("Reproduciendo."), interrupt=True)

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def reproducir_audio(self, db_id):
        """Extrae las URLs del post y reproduce su audio con el resolutor de streams."""
        row = db.query_one("SELECT urls_json, content FROM bluesky_posts WHERE id = ?", (db_id,))
        urls = []
        if row:
            if row[0]:
                try:
                    urls = json.loads(row[0])
                except Exception:
                    urls = []
            if not urls and row[1]:
                import re
                urls = re.findall(r"https?://[^\s\)\]]+", row[1])
        if not urls:
            speak(_("No hay enlaces reproducibles en esta publicación."), interrupt=True)
            return
        try:
            self.f._reproducir_audio_servicio_desde_urls(
                urls, _("Audio de Bluesky"), "bluesky", self.f.active_bluesky_account_id)
        except Exception as e:
            notificar(_("No se pudo reproducir: {0}").format(e))

    def traducir(self, db_id):
        texto = self._texto_post(db_id)
        if not texto:
            return
        from app.services.translation.translation_service import translation_service

        def _bg(ctx):
            return translation_service.traducir(texto)

        def _cb(res, error):
            if error:
                notificar(_("Error al traducir: {0}").format(error))
            else:
                self.f._mostrar_texto_largo(_("Traducción"), res)
        task_manager.start_task(_bg, callback=_cb, pass_context=True)
        speak(_("Traduciendo..."), interrupt=True)

    def resumir(self, db_id):
        texto = self._texto_post(db_id)
        if not texto:
            return
        from app.services.ai.ai_service import ai_service

        def _bg(ctx):
            return ai_service.resumir_texto(texto)

        def _cb(res, error):
            if error:
                notificar(_("Error al resumir: {0}").format(error))
            else:
                # Markdown -> visor HTML navegable; texto plano -> cuadro de solo lectura.
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self.f, _("Resumen IA"), res, self.f._mostrar_texto_largo)
        task_manager.start_task(_bg, callback=_cb, pass_context=True)
        speak(_("Resumiendo..."), interrupt=True)

    def leer_tts(self, db_id):
        texto = self._texto_post(db_id)
        if not texto:
            return
        try:
            from app.services.audio.tts_reader import tts_reader
            tts_reader.leer(texto)
        except Exception as e:
            notificar(_("No se pudo leer: {0}").format(e))

    # ---------------- Refresco (F5) ----------------
