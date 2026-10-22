# -*- coding: utf-8 -*-
"""Controlador Bluesky: refresco, búsquedas, listas, firehose y perfil (mixin).
"""
import logging

import wx
from app.core.privacy import abrir_url

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.core.event_bus import event_bus
from app.core.task_manager import task_manager, TareaDuplicadaError
from app.ui.accessible_controls import mostrar_info, confirmar

from app.services.bluesky import bluesky_service
from .bluesky_dialogs import (
    BlueskyComposeDialog, BlueskyProfileDialog, BlueskyAppPasswordsDialog,
    BlueskyThreadEditorDialog)


log = logging.getLogger(__name__)

class _SocialMixin:
    def on_refresh(self, silencioso=False):
        """Descarga el buffer activo. Con silencioso=True NO dice 'Actualizando…' ni
        'Bluesky actualizado.' (se usa para la AUTO-CARGA al entrar a un buffer vacío, como hace
        Mastodon: el buffer se llena solo sin que tengas que pulsar nada). Los errores sí se avisan."""
        f = self.f
        tipo = f.active_timeline_type
        acc = f.active_bluesky_account_id
        if not acc:
            return

        # Estado previo de la lista para anunciar cuántos elementos nuevos se han descargado (o 0).
        ids_antes = set(getattr(f, "lista_items_db_ids", []) or [])

        def _fin(recargar):
            def _cb(res, error):
                f._updating = False
                if isinstance(error, TareaDuplicadaError):
                    # Aviso inocuo: esa misma descarga ya está en marcha (p. ej. la lanzó el
                    # sondeo de fondo). No es un error: solo liberar el flag y, si fue
                    # manual, decirlo con suavidad.
                    if not silencioso:
                        speak(_("Esa actualización ya está en curso."), interrupt=False)
                    return
                if error:
                    if not silencioso:
                        notificar(_("Error al actualizar: {0}").format(error))
                    event_bus.emit("sound_trigger", "bluesky_error")
                else:
                    if silencioso:
                        recargar(res)
                    else:
                        # Actualización manual: recargar sin que el lector relea el elemento enfocado.
                        with f._foco_silencioso():
                            recargar(res)
                        f._anunciar_descarga(ids_antes)
            return _cb

        if not silencioso:
            speak(_("Actualizando Bluesky..."), interrupt=True)
        f._updating = True

        # Buffers de personas
        if tipo in self.BUFFERS_ACTORES:
            bluesky_service.cargar_actores_async(
                acc, tipo, callback=_fin(lambda res: self._cargar_actores(tipo)))
        # Tendencias
        elif tipo == "trending":
            def _r(res):
                self._trends_cache = res or []
                self._cargar_tendencias()
            bluesky_service.cargar_tendencias_async(acc, callback=_fin(_r))
        # Starter packs
        elif tipo == "starterpacks":
            def _r(res):
                self._packs_cache = res or []
                self._cargar_starterpacks()
            bluesky_service.obtener_starter_packs_async(acc, callback=_fin(_r))
        # Conversaciones (DMs)
        elif tipo == "convos":
            bluesky_service.cargar_convos_async(
                acc, callback=_fin(lambda res: self._cargar_convos()))
        # Marcadores (API propia, no get_timeline)
        elif tipo == "bookmarks":
            # Limpiar la relación previa para reflejar solo lo que sigue marcado.
            db.execute("DELETE FROM bluesky_timeline_posts WHERE account_id = ? AND timeline = 'bookmarks'", (acc,))
            bluesky_service.cargar_bookmarks_async(
                acc, callback=_fin(lambda res: self._cargar_posts("bookmarks")))
        # Gestores de feeds/listas: descargar y recargar la lista (sin tocar el árbol)
        elif tipo in self.BUFFERS_GESTOR:
            f._updating = False
            self._descargar_gestor(tipo, silencioso=silencioso)
        # Posts (home/mentions/sent/likes/feed:/list:/search:)
        elif self._es_buffer_posts(tipo) or tipo == "notifications":
            def _r(res):
                if tipo == "notifications":
                    self._cargar_notificaciones()
                else:
                    self._cargar_posts(tipo)
            bluesky_service.cargar_timeline_async(acc, timeline_type=tipo, callback=_fin(_r))
        else:
            f._updating = False
            speak(_("Este buffer estará disponible en una próxima fase."), interrupt=True)

    def actualizar_incremental(self):
        """Sondeo silencioso del buffer activo (auto-actualización). Solo cronológicos.
        Si entran elementos nuevos, emite el sonido correspondiente (mención, notificación,
        publicación o mensaje directo)."""
        f = self.f
        tipo = f.active_timeline_type
        acc = f.active_bluesky_account_id
        if not acc:
            return

        def _firma_posts(t):
            r = db.query_one(
                "SELECT COUNT(*) FROM bluesky_timeline_posts WHERE account_id = ? AND timeline = ?",
                (acc, t))
            return r[0] if r else 0

        def _firma_notifs():
            r = db.query_one("SELECT COUNT(*) FROM bluesky_notifications WHERE account_id = ?", (acc,))
            return r[0] if r else 0

        def _firma_convos():
            # Un mensaje nuevo cambia la fecha del último mensaje o el nº de no leídos.
            r = db.query_one(
                "SELECT COALESCE(MAX(last_message_date),''), COALESCE(SUM(unread_count),0) "
                "FROM bluesky_convos WHERE account_id = ?", (acc,))
            return (r[0], r[1]) if r else ("", 0)

        def _silencioso(firma, sonido, filas_fn=None, inserter=None):
            """Sondea SIN reconstruir la lista. Si la firma de la BD cambió (entraron
            elementos nuevos) y se pasa filas_fn, inserta/actualiza solo lo necesario
            sin mover el foco (silencioso); si no, solo avisa con sonido. En ambos
            casos el foco se queda en tu elemento y el lector no relee. 'inserter'
            permite usar el modo chats (actualizar en su sitio + insertar nuevos)."""
            antes = firma()

            def _cb(r, e):
                if e:
                    return
                if firma() != antes:
                    if filas_fn is not None:
                        (inserter or self.f._insertar_items_incremental)(filas_fn())
                    event_bus.emit("sound_trigger", sonido)
            return _cb

        if tipo == "notifications":
            bluesky_service.cargar_timeline_async(
                acc, timeline_type="notifications",
                callback=_silencioso(_firma_notifs, "bluesky_new_notification",
                                     filas_fn=lambda: self._filas_notifs_actual()))
        elif tipo == "mentions":
            bluesky_service.cargar_timeline_async(
                acc, timeline_type="mentions",
                callback=_silencioso(lambda: _firma_posts("mentions"), "bluesky_new_mention",
                                     filas_fn=lambda: self._filas_posts_actual("mentions")))
        elif tipo == "convos":
            bluesky_service.cargar_convos_async(
                acc, callback=_silencioso(_firma_convos, "bluesky_dm",
                                          filas_fn=lambda: self._filas_convos_actual(),
                                          inserter=self.f._actualizar_lista_chats))
        elif self._es_buffer_posts(tipo) and tipo != "bookmarks":
            bluesky_service.cargar_timeline_async(
                acc, timeline_type=tipo,
                callback=_silencioso(lambda t=tipo: _firma_posts(t), "bluesky_new_status",
                                     filas_fn=lambda tt=tipo: self._filas_posts_actual(tt)))
        # actores/tendencias/starterpacks/gestores/bookmarks NO se auto-actualizan.

    def sondear_buffers_fondo(self):
        """Descarga en SEGUNDO PLANO los buffers cronológicos que NO son el activo (Inicio,
        Notificaciones, Menciones) y los guarda en la base, SIN tocar la lista visible. Así, al
        cambiar a esos buffers, ya están al día (fluidez, como en Mastodon). Si entra algo en
        Notificaciones/Menciones mientras estás en otro buffer, también suena el aviso."""
        f = self.f
        acc = f.active_bluesky_account_id
        if not acc:
            return
        activo = f.active_timeline_type

        def _firma_posts(t):
            r = db.query_one(
                "SELECT COUNT(*) FROM bluesky_timeline_posts WHERE account_id = ? AND timeline = ?",
                (acc, t))
            return r[0] if r else 0

        def _firma_notifs():
            r = db.query_one("SELECT COUNT(*) FROM bluesky_notifications WHERE account_id = ?", (acc,))
            return r[0] if r else 0

        for tipo in ("home", "notifications", "mentions"):
            if tipo == activo:
                continue
            if tipo == "notifications":
                antes = _firma_notifs()

                def _cb_n(r, e, _a=antes):
                    if not e and _firma_notifs() != _a:
                        event_bus.emit("sound_trigger", "bluesky_new_notification")
                bluesky_service.cargar_timeline_async(acc, timeline_type="notifications", callback=_cb_n)
            else:
                antes = _firma_posts(tipo)
                son = "bluesky_new_mention" if tipo == "mentions" else "bluesky_new_status"

                def _cb_p(r, e, _t=tipo, _a=antes, _s=son):
                    if not e and _firma_posts(_t) != _a:
                        event_bus.emit("sound_trigger", _s)
                bluesky_service.cargar_timeline_async(acc, timeline_type=tipo, callback=_cb_p)

    def cargar_mas(self):
        """Carga una página más antigua del buffer de posts activo (paginación por cursor).
        En los buffers de CUENTAS (seguidores/siguiendo/bloqueados/silenciados) añade la
        página siguiente de cuentas (auditoría 10.3: estaban truncados a 80)."""
        f = self.f
        tipo = f.active_timeline_type
        acc = f.active_bluesky_account_id
        if acc and tipo in self.BUFFERS_ACTORES:
            self._cargar_mas_actores(tipo)
            return
        if not acc or not self._es_buffer_posts(tipo):
            speak(_("Este buffer no admite cargar más."), interrupt=True)
            return
        # Final de paginación conocido: avisar en vez de repetir la primera página (BSKY-006).
        if tipo != "bookmarks" and not bluesky_service.hay_mas_paginas(acc, tipo):
            speak(_("No hay más publicaciones anteriores en este buffer."), interrupt=True)
            return
        speak(_("Cargando publicaciones más antiguas..."), interrupt=True)
        if tipo == "bookmarks":
            bluesky_service.cargar_bookmarks_async(
                acc, cargar_mas=True,
                callback=lambda r, e: self._fin_cargar_mas(tipo, e))
        else:
            bluesky_service.cargar_timeline_async(
                acc, timeline_type=tipo, cargar_mas=True,
                callback=lambda r, e: self._fin_cargar_mas(tipo, e))

    def _fin_cargar_mas(self, tipo, error):
        if isinstance(error, TareaDuplicadaError):
            speak(_("Esa carga ya está en curso."), interrupt=False)
            return
        if error:
            notificar(_("Error al cargar más: {0}").format(error))
        else:
            self._cargar_posts(tipo)
            speak(_("Cargadas más publicaciones."), interrupt=True)

    def _cargar_mas_actores(self, tipo):
        """Añade la página SIGUIENTE de una lista de cuentas (10.3), sin mover el foco,
        anunciando cuántas entraron y si quedan más (mismo Ctrl+Shift+M que en Mastodon)."""
        f = self.f
        acc = f.active_bluesky_account_id
        speak(_("Cargando más cuentas..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error al cargar más: {0}").format(err))
                return
            actores = (res or {}).get("actores") or []
            if not actores:
                speak(_("No hay más cuentas en esta lista."), interrupt=True)
                return
            # Repintar conservando la posición del usuario (las nuevas van al final).
            with f._foco_silencioso():
                self._cargar_actores(tipo)
            total = f.lista_items.GetItemCount()
            if (res or {}).get("has_more"):
                speak(_("{0} cuentas más cargadas ({1} en total; hay más: pulsa Ctrl+Shift+M "
                        "para seguir).").format(len(actores), total), interrupt=True)
            else:
                speak(_("{0} cuentas más cargadas ({1} en total; no hay más).").format(
                    len(actores), total), interrupt=True)
        bluesky_service.cargar_actores_async(acc, tipo, cargar_mas=True, callback=_cb)

    # ---------------- Menú: nuevo post ----------------
    # (El alta y la eliminación de cuentas de Bluesky se gestionan desde
    #  "Gestionar Cuentas...", en app/ui/mastodon_views.py CuentasDialog.)
    def nuevo_post(self):
        acc = self.f.active_bluesky_account_id
        if not acc:
            # Tomar la primera cuenta de Bluesky disponible
            row = db.query_one("SELECT id FROM accounts WHERE service_id = 'bluesky' AND active = 1 ORDER BY position, id LIMIT 1")
            if not row:
                mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
                return
            acc = row[0]
        dlg = BlueskyComposeDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def nuevo_hilo(self):
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        dlg = BlueskyThreadEditorDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _cuenta_activa_o_primera(self):
        acc = self.f.active_bluesky_account_id
        if acc:
            return acc
        row = db.query_one("SELECT id FROM accounts WHERE service_id = 'bluesky' AND active = 1 ORDER BY position, id LIMIT 1")
        return row[0] if row else None

    def crear_buffer_busqueda(self, termino):
        """Crea (o reutiliza) el buffer de búsqueda 'search:<término>' y lo abre con
        resultados. Lo usan 'Guardar búsqueda como buffer' y las TENDENCIAS (abrir una
        tendencia dentro de la app). El buffer resultante se puede ocultar y eliminar
        como cualquier búsqueda guardada."""
        acc = self._cuenta_activa_o_primera()
        termino = (termino or "").strip()
        if not acc or not termino:
            return
        guardadas = list(config.get("bluesky", "saved_searches", []))
        if termino not in guardadas:
            guardadas.append(termino)
            config.set("bluesky", "saved_searches", guardadas)
        self.f.active_bluesky_account_id = acc
        self.f._cargar_navegacion()
        self._re_seleccionar("search:" + termino)
        # Descargar resultados inmediatamente.
        bluesky_service.cargar_timeline_async(
            acc, timeline_type="search:" + termino,
            callback=lambda res, err: self._cargar_posts("search:" + termino) if not err else None)

    def guardar_busqueda(self):
        """Crea un buffer de búsqueda de posts (paridad Mastodon)."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Texto a buscar:"), _("Guardar búsqueda"))
        if dlg.ShowModal() == wx.ID_OK:
            self.crear_buffer_busqueda(dlg.GetValue())
        dlg.Destroy()

    def crear_buffer_usuario(self, did, handle=""):
        """Crea un buffer con las publicaciones de una cuenta (desde el menú contextual de un post).
        Se guarda en config y se reconstruye el árbol; el nodo va bajo el grupo 'Cronologías'."""
        acc = self._cuenta_activa_o_primera()
        if not acc or not did:
            return
        # Si no llega el handle (p. ej. desde la lista de seguidores), intentar resolverlo de la BD.
        if not handle:
            fila = db.query_one(
                "SELECT author_handle FROM bluesky_posts WHERE author_did = ? AND author_handle <> '' LIMIT 1",
                (did,))
            handle = fila[0] if fila and fila[0] else did
        guardadas = list(config.get("bluesky", "user_timelines", []))
        if not any(isinstance(u, dict) and u.get("did") == did for u in guardadas):
            guardadas.append({"did": did, "handle": (handle or "").lstrip("@")})
            config.set("bluesky", "user_timelines", guardadas)
        self.f.active_bluesky_account_id = acc
        self.f._cargar_navegacion()
        self._re_seleccionar("author:" + did)
        # Descargar sus publicaciones ya.
        bluesky_service.cargar_timeline_async(
            acc, timeline_type="author:" + did,
            callback=lambda res, err: self._cargar_posts("author:" + did) if not err else None)
        notificar(_("Buffer de la cuenta creado."))

    def crear_buffer_usuario_por_handle(self, handle):
        """Crea el buffer de cronología de una cuenta a partir de su HANDLE (lo que se ve escrito
        en un post: @nombre.bsky.social). Bluesky identifica las cuentas por DID, así que primero
        se resuelve el perfil; si el handle no existe (por ejemplo, una arroba suelta del texto),
        se avisa y no se crea nada."""
        acc = self._cuenta_activa_o_primera()
        handle = (handle or "").strip().lstrip("@")
        if not acc or not handle:
            return
        if handle.startswith("did:"):
            self.crear_buffer_usuario(handle)
            return
        # Si ya tenemos posts suyos en la base, no hace falta preguntar al servidor.
        fila = db.query_one(
            "SELECT author_did FROM bluesky_posts WHERE LOWER(author_handle) = ? AND author_did <> '' LIMIT 1",
            (handle.lower(),))
        if fila and fila[0]:
            self.crear_buffer_usuario(fila[0], handle)
            return
        speak(_("Buscando la cuenta..."), interrupt=True)

        def _cb(info, err):
            if err or not info or not info.get("did"):
                notificar(_("No se encontró la cuenta @{0} en Bluesky.").format(handle))
                return
            self.crear_buffer_usuario(info["did"], info.get("handle") or handle)
        bluesky_service.obtener_perfil_async(acc, handle, callback=_cb)

    # ------------------------------------------------------------------
    # GRUPOS DE CHAT
    # ------------------------------------------------------------------
    # Bluesky admite conversaciones de GRUPO ademas de los mensajes de dos. Se manejan igual que
    # en W Social (comparten los lexicons chat.bsky.group), asi que la experiencia es la misma.
    def _pedir(self, mensaje, titulo, valor=""):
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, mensaje, titulo, value=valor)
        texto = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        return texto

    def _aviso_grupo(self, ok, res, exito):
        notificar(exito if ok else _("No se pudo: {0}").format(res))

    def crear_grupo(self):
        """Crea un grupo de chat con las cuentas que indiques."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        nombre = self._pedir(_("Nombre del grupo:"), _("Nuevo grupo"))
        if not nombre:
            return
        cuentas = self._pedir(_("Cuentas a anadir, separadas por comas (por ejemplo "
                                "nombre.bsky.social):"), _("Nuevo grupo"))
        actores = [c.strip() for c in cuentas.replace(";", ",").split(",") if c.strip()]
        if not actores:
            mostrar_info(self.f, _("Anade al menos una cuenta al grupo."), _("Nuevo grupo"))
            return
        bluesky_service.crear_grupo_async(
            acc, nombre, actores,
            callback=lambda ok, res: self._aviso_grupo(ok, res, _("Grupo creado.")))

    def entrar_en_grupo(self):
        """Pide entrar en un grupo a partir de su enlace de invitacion."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        enlace = self._pedir(_("Pega el enlace de invitacion al grupo:"), _("Entrar en un grupo"))
        if not enlace:
            return
        bluesky_service.pedir_entrada_grupo_async(
            acc, enlace,
            callback=lambda ok, res: self._aviso_grupo(
                ok, res, _("Solicitud enviada.") if (isinstance(res, dict) and res.get("pendiente"))
                else _("Ya estas en el grupo.")))

    def grupo_anadir_miembros(self, convo_id):
        acc = self._cuenta_activa_o_primera()
        cuentas = self._pedir(_("Cuentas a anadir, separadas por comas:"), _("Anadir al grupo"))
        actores = [c.strip() for c in cuentas.replace(";", ",").split(",") if c.strip()]
        if not actores:
            return
        bluesky_service.gestionar_miembros_grupo_async(
            acc, convo_id, actores, quitar=False,
            callback=lambda ok, res: self._aviso_grupo(ok, res, _("Cuentas anadidas al grupo.")))

    def grupo_expulsar_miembro(self, convo_id):
        acc = self._cuenta_activa_o_primera()
        cuenta = self._pedir(_("Cuenta a expulsar (usuario o identificador):"), _("Expulsar"))
        if not cuenta:
            return
        if confirmar(self.f, _("Vas a expulsar a {0} del grupo. Continuar?").format(cuenta),
                     _("Expulsar del grupo")) != wx.ID_YES:
            return
        bluesky_service.gestionar_miembros_grupo_async(
            acc, convo_id, [cuenta], quitar=True,
            callback=lambda ok, res: self._aviso_grupo(ok, res, _("Cuenta expulsada.")))

    def grupo_renombrar(self, convo_id):
        acc = self._cuenta_activa_o_primera()
        nombre = self._pedir(_("Nuevo nombre del grupo:"), _("Cambiar el nombre"))
        if not nombre:
            return
        bluesky_service.editar_grupo_async(
            acc, convo_id, nombre,
            callback=lambda ok, res: self._aviso_grupo(ok, res, _("Nombre cambiado.")))

    def grupo_enlace(self, convo_id):
        """Crea el enlace de invitacion y lo deja en el portapapeles."""
        acc = self._cuenta_activa_o_primera()
        speak(_("Preparando el enlace..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo obtener el enlace: {0}").format(res))
                return
            enlace = (res or {}).get("enlace", "")

            def _mostrar():
                try:
                    if enlace and wx.TheClipboard.Open():
                        wx.TheClipboard.SetData(wx.TextDataObject(enlace))
                        wx.TheClipboard.Close()
                except Exception:
                    pass
                mostrar_info(self.f, _("Enlace de invitacion (copiado al portapapeles):\n\n{0}")
                             .format(enlace or _("el servidor no ha devuelto ningun enlace")),
                             _("Invitar al grupo"))
            wx.CallAfter(_mostrar)
        bluesky_service.enlace_grupo_async(acc, convo_id, "crear", callback=_cb)

    def grupo_solicitudes(self, convo_id):
        """Acepta o rechaza a quien ha pedido entrar en el grupo."""
        acc = self._cuenta_activa_o_primera()

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron consultar las solicitudes: {0}").format(res))
                return
            if not res:
                wx.CallAfter(mostrar_info, self.f, _("No hay solicitudes pendientes."),
                             _("Solicitudes del grupo"))
                return

            def _elegir():
                opciones = [f"{s.get('nombre') or s.get('handle')} (@{s.get('handle')})" for s in res]
                sel = wx.GetSingleChoice(_("Elige la solicitud:"), _("Solicitudes del grupo"),
                                         opciones, self.f)
                if not sel:
                    return
                solicitud = res[opciones.index(sel)]
                aceptar = confirmar(
                    self.f, _("Aceptar a {0} en el grupo? Si eliges No, se rechazara.").format(
                        solicitud.get("handle")), _("Solicitud de entrada")) == wx.ID_YES
                bluesky_service.resolver_solicitud_grupo_async(
                    acc, convo_id, solicitud.get("did"), aceptar,
                    callback=lambda o, r: self._aviso_grupo(
                        o, r, _("Solicitud aceptada.") if aceptar else _("Solicitud rechazada.")))
            wx.CallAfter(_elegir)
        bluesky_service.solicitudes_grupo_async(acc, convo_id, callback=_cb)

    def crear_buffer_usuario_mencionado(self, db_id=None):
        """Cronología de alguien que aparece en el post (mencionado, citado o quien lo reposteó):
        se ofrecen todas esas cuentas —menos la tuya— y se abre la elegida."""
        handle = self.f._elegir_usuario_mencionado(_("Publicaciones de un usuario mencionado"), db_id)
        if handle:
            self.crear_buffer_usuario_por_handle(handle)

    def crear_buffer_conversacion(self, db_id):
        """Abre la CONVERSACIÓN del post enfocado como buffer navegable del árbol (paridad
        con Mastodon, petición de usuarios): la raíz del hilo con todos sus padres y
        respuestas en orden de lectura, con el menú contextual completo de publicaciones."""
        acc = self.f.active_bluesky_account_id
        fila = db.query_one(
            "SELECT uri, reply_root_uri, author_name, content FROM bluesky_posts WHERE id = ?",
            (db_id,))
        if not fila:
            notificar(_("No se pudo obtener la publicación."))
            return
        uri, root_uri, autor, contenido = fila
        # El buffer se ancla a la RAÍZ del hilo (si el post es una respuesta), para
        # mostrar la conversación completa, igual que hace Mastodon.
        uri_hilo = root_uri or uri
        display = "{0}: {1}".format(autor or "", (contenido or "").replace("\n", " ")[:40]).strip(": ")
        guardadas = list(config.get("bluesky", "thread_buffers", []))
        if not any(isinstance(c, dict) and c.get("uri") == uri_hilo for c in guardadas):
            # "origen": la publicación desde la que se abrió. Si la raíz desaparece (borrada o
            # bloqueada), la conversación se carga desde ella en vez de quedarse vacía.
            guardadas.append({"uri": uri_hilo, "display": display, "origen": uri})
            config.set("bluesky", "thread_buffers", guardadas)
        self.f.active_bluesky_account_id = acc
        self.f._cargar_navegacion()
        self._re_seleccionar("thread:" + uri_hilo)
        # Descargar la conversación ya.
        bluesky_service.cargar_timeline_async(
            acc, timeline_type="thread:" + uri_hilo,
            callback=lambda res, err: self._cargar_posts("thread:" + uri_hilo) if not err else None)
        notificar(_("Conversación abierta como buffer."))

    def eliminar_buffer_dinamico(self, bsky_type):
        """Quita del árbol un buffer creado por el usuario (búsqueda, cronología de
        cuenta o conversación)."""
        if bsky_type.startswith("search:"):
            termino = bsky_type.split(":", 1)[1]
            guardadas = [t for t in config.get("bluesky", "saved_searches", []) if t != termino]
            config.set("bluesky", "saved_searches", guardadas)
            mensaje = _("Búsqueda eliminada.")
        elif bsky_type.startswith("author:"):
            did = bsky_type.split(":", 1)[1]
            guardadas = [u for u in config.get("bluesky", "user_timelines", [])
                         if not (isinstance(u, dict) and u.get("did") == did)]
            config.set("bluesky", "user_timelines", guardadas)
            mensaje = _("Cronología de cuenta eliminada.")
        elif bsky_type.startswith("thread:"):
            uri = bsky_type.split(":", 1)[1]
            guardadas = [c for c in config.get("bluesky", "thread_buffers", [])
                         if not (isinstance(c, dict) and c.get("uri") == uri)]
            config.set("bluesky", "thread_buffers", guardadas)
            mensaje = _("Conversación eliminada del árbol.")
        else:
            return
        self.f._cargar_navegacion()
        notificar(mensaje)

    def menu_contextual_arbol(self, menu, data, item):
        """Menú contextual de un nodo del árbol de Bluesky. Devuelve True si añadió opciones.
        Para búsquedas, cronologías de cuenta y conversaciones: actualizar y eliminar."""
        f = self.f
        bsky_type = data.get("bsky_type", "") or ""
        if bsky_type.startswith(("search:", "author:", "thread:")):
            it = menu.Append(wx.ID_ANY, _("Actualizar publicaciones"))
            f.Bind(wx.EVT_MENU, lambda e: (f.tree_nav.SelectItem(item), self.on_refresh()), it)
            if bsky_type.startswith("search:"):
                etiqueta = _("Eliminar esta búsqueda")
            elif bsky_type.startswith("thread:"):
                etiqueta = _("Eliminar esta conversación del árbol")
            else:
                etiqueta = _("Borrar esta cronología de cuenta")
            it = menu.Append(wx.ID_ANY, etiqueta)
            f.Bind(wx.EVT_MENU, lambda e, b=bsky_type: self.eliminar_buffer_dinamico(b), it)
            return True
        return False

    def ver_perfil_por_handle(self):
        """Pide un handle o DID ESCRITO y muestra su perfil. Útil para perfiles citados que no
        aparecen en ninguna lista. atproto get_profile acepta tanto el handle como el DID."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self.f, _("Cuenta a ver (handle, ej. nombre.bsky.social, o un DID):"),
            _("Ver perfil de una cuenta"))
        actor = dlg.GetValue().strip().lstrip("@") if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if actor:
            self.ver_perfil(actor)

    def buscar_cuentas(self):
        """Busca cuentas y permite seguir/ver perfil."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Nombre o handle a buscar:"), _("Buscar cuentas"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        q = dlg.GetValue().strip()
        dlg.Destroy()
        if not q:
            return
        speak(_("Buscando..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error en la búsqueda: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("Sin resultados."), _("Buscar cuentas"))
                return
            opciones = [f"{a['display_name'] or a['handle']} (@{a['handle']})" for a in res]
            sel = wx.GetSingleChoice(_("Resultados (elige para ver el perfil):"),
                                     _("Buscar cuentas"), opciones, self.f)
            if sel:
                idx = opciones.index(sel)
                self.ver_perfil(res[idx]["did"])
        bluesky_service.buscar_actores_async(acc, q, callback=_cb)

    def cuentas_sugeridas(self):
        """Muestra cuentas sugeridas para seguir (descubrimiento, como FastSM/TWBlue) y
        permite abrir su perfil para seguirlas."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        speak(_("Cargando cuentas sugeridas..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error al obtener sugerencias: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("No hay sugerencias en este momento."), _("Cuentas sugeridas"))
                return
            opciones = [f"{a['display_name'] or a['handle']} (@{a['handle']})" for a in res]
            sel = wx.GetSingleChoice(_("Cuentas sugeridas (elige para ver el perfil y seguir):"),
                                     _("Cuentas sugeridas"), opciones, self.f)
            if sel:
                idx = opciones.index(sel)
                self.ver_perfil(res[idx]["did"])
        bluesky_service.obtener_sugerencias_async(acc, callback=_cb)

    def feeds_populares(self):
        """Muestra feeds populares para inspeccionar (abre en el navegador)."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        speak(_("Cargando feeds populares..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("No se encontraron feeds."), _("Feeds populares"))
                return
            opciones = [f"{x['name']} (@{x['creator']}) — {x['likes']} me gusta" for x in res]
            sel = wx.GetSingleChoice(_("Feeds populares (elige uno):"),
                                     _("Feeds populares"), opciones, self.f)
            if sel:
                self._accion_sobre_feed(acc, res[opciones.index(sel)])
        bluesky_service.buscar_feeds_async(acc, callback=_cb)

    def _accion_sobre_feed(self, acc, feed):
        """Menú de acciones sobre un feed encontrado: añadirlo a tus guardados (fijado o no)
        o abrirlo en el navegador."""
        uri = feed["uri"]
        opciones = [_("Añadir a mis feeds y fijarlo"), _("Añadir a mis feeds"),
                    _("Abrir en el navegador")]
        sel = wx.GetSingleChoice(_("¿Qué quieres hacer con «{0}»?").format(feed.get("name", "")),
                                 _("Feed"), opciones, self.f)
        if not sel:
            return

        def _recargar(r, e):
            if not e:
                bluesky_service.obtener_feeds_guardados_async(
                    acc, callback=lambda rr, ee: self.f._cargar_navegacion() if not ee else None)

        if sel == opciones[0] or sel == opciones[1]:
            fijar = (sel == opciones[0])
            def _cb(r, e):
                if e:
                    notificar(_("No se pudo guardar el feed: {0}").format(e))
                else:
                    notificar(_("Feed añadido y fijado.") if fijar else _("Feed añadido a tus guardados."))
                    _recargar(r, e)
            bluesky_service.guardar_feed_async(acc, uri, tipo="feed", fijar=fijar, callback=_cb)
        else:
            rkey = uri.rsplit("/", 1)[-1]
            did = uri.split("/")[2] if uri.startswith("at://") else ""
            abrir_url(f"https://bsky.app/profile/{did}/feed/{rkey}")

    def gestionar_feeds_guardados(self):
        """Lista tus feeds guardados y permite fijar/desfijar, subir/bajar o quitar cada uno."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        speak(_("Cargando tus feeds guardados..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("No tienes feeds guardados. Añade alguno desde Feeds populares o sugeridos."),
                             _("Feeds guardados"))
                return
            # res viene de bluesky_feeds; consultar estado de fijado desde la base.
            filas = db.query("SELECT uri, name, COALESCE(pinned,0) FROM bluesky_feeds "
                             "WHERE account_id = ? AND kind = 'feed' ORDER BY position, id", (acc,))
            self._menu_feeds_guardados(acc, [{"uri": u, "name": n, "pinned": bool(p)} for u, n, p in filas])
        bluesky_service.obtener_feeds_guardados_async(acc, callback=_cb)

    def _menu_feeds_guardados(self, acc, feeds):
        if not feeds:
            mostrar_info(self.f, _("No tienes feeds guardados."), _("Feeds guardados"))
            return
        opciones = ["{0}{1}".format(f["name"], _(" (fijado)") if f["pinned"] else "") for f in feeds]
        sel = wx.GetSingleChoice(_("Tus feeds guardados (elige uno):"), _("Feeds guardados"), opciones, self.f)
        if not sel:
            return
        f = feeds[opciones.index(sel)]

        def _tras(r, e, msg):
            if e:
                notificar(_("Error: {0}").format(e))
            else:
                notificar(msg)
                bluesky_service.obtener_feeds_guardados_async(
                    acc, callback=lambda rr, ee: self.f._cargar_navegacion() if not ee else None)

        acciones = [_("Desfijar") if f["pinned"] else _("Fijar arriba"),
                    _("Quitar de mis feeds")]
        a = wx.GetSingleChoice(_("Acción sobre «{0}»:").format(f["name"]), _("Feed"), acciones, self.f)
        if a == acciones[0]:
            bluesky_service.fijar_feed_async(acc, f["uri"], fijar=not f["pinned"],
                                             callback=lambda r, e: _tras(r, e, _("Hecho.")))
        elif a == acciones[1]:
            bluesky_service.quitar_feed_guardado_async(acc, f["uri"],
                                                       callback=lambda r, e: _tras(r, e, _("Feed quitado.")))

    def crear_lista(self):
        """Crea una lista de curación."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Nombre de la nueva lista:"), _("Crear lista"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        nombre = dlg.GetValue().strip()
        dlg.Destroy()
        if not nombre:
            return

        def _cb(res, error):
            if error:
                notificar(_("Error al crear la lista: {0}").format(error))
            else:
                notificar(_("Lista creada."))
                bluesky_service.obtener_listas_async(
                    acc, callback=lambda r, e: self.f._cargar_navegacion() if not e else None)
        bluesky_service.crear_lista_async(acc, nombre, callback=_cb)

    def feeds_sugeridos(self):
        """Muestra feeds recomendados por Bluesky (getSuggestedFeeds) y abre el elegido."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        speak(_("Cargando feeds sugeridos..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("No hay feeds sugeridos en este momento."), _("Feeds sugeridos"))
                return
            opciones = [f"{x['name']} (@{x['creator']}) — {x['likes']} me gusta" for x in res]
            sel = wx.GetSingleChoice(_("Feeds sugeridos (elige uno):"),
                                     _("Feeds sugeridos"), opciones, self.f)
            if sel:
                self._accion_sobre_feed(acc, res[opciones.index(sel)])
        bluesky_service.obtener_feeds_sugeridos_async(acc, callback=_cb)

    def ver_interacciones(self, db_id, tipo):
        """Muestra las cuentas que interactuaron con un post. tipo: 'likes'|'reposts'|'quotes'."""
        uri = self._uri_post(db_id)
        if not uri:
            return
        titulos = {"likes": _("Me gusta"), "reposts": _("Reposts"), "quotes": _("Citas")}
        titulo = titulos.get(tipo, _("Interacciones"))
        speak(_("Cargando..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("Nadie todavía."), titulo)
                return
            opciones = [f"{a['display_name'] or a['handle']} (@{a['handle']})" for a in res]
            sel = wx.GetSingleChoice(_("{0} ({1}). Elige una cuenta para ver su perfil:").format(
                titulo, len(res)), titulo, opciones, self.f)
            if sel:
                idx = opciones.index(sel)
                self.ver_perfil(res[idx]["did"])
        bluesky_service.obtener_interacciones_post_async(
            self.f.active_bluesky_account_id, uri, tipo, callback=_cb)

    def _elegir_lista(self, acc, callback, titulo=None):
        """Carga las listas de la cuenta y deja elegir una. Llama callback({uri,name}) o callback(None)."""
        titulo = titulo or _("Elegir lista")

        def _cb(res, error):
            if error:
                notificar(_("Error al cargar las listas: {0}").format(error))
                callback(None)
                return
            if not res:
                mostrar_info(self.f, _("No tienes ninguna lista. Crea una primero desde el menú Bluesky."),
                             titulo)
                callback(None)
                return
            opciones = [x["name"] for x in res]
            sel = wx.GetSingleChoice(_("Elige una lista:"), titulo, opciones, self.f)
            if not sel:
                callback(None)
                return
            callback(res[opciones.index(sel)])
        bluesky_service.obtener_listas_async(acc, callback=_cb)

    def anadir_autor_a_lista(self, actor):
        """Añade el autor de un post (DID o handle) a una de tus listas de curación."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return

        def _con_lista(lista):
            if not lista:
                return

            def _cb(res, error):
                if error:
                    notificar(_("No se pudo añadir a la lista: {0}").format(error))
                else:
                    notificar(_("Cuenta añadida a la lista «{0}».").format(lista["name"]))
            bluesky_service.anadir_miembro_lista_async(acc, lista["uri"], actor, callback=_cb)
        self._elegir_lista(acc, _con_lista, _("Añadir a lista"))

    def gestionar_miembros_lista(self):
        """Elige una lista y permite ver, quitar o añadir cuentas (gestión de miembros)."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        self._elegir_lista(acc, lambda lista: self._menu_lista(acc, lista) if lista else None,
                           _("Gestionar miembros de listas"))

    def _menu_lista(self, acc, lista):
        """Ofrece las acciones disponibles sobre una lista concreta."""
        acciones = [
            _("Ver y quitar miembros"),
            _("Añadir una cuenta por su handle"),
            _("Silenciar esta lista (suscribirse a la lista de silencio)"),
            _("Quitar el silencio de esta lista"),
        ]
        sel = wx.GetSingleChoice(_("Lista «{0}». ¿Qué quieres hacer?").format(lista["name"]),
                                 _("Gestionar lista"), acciones, self.f)
        if not sel:
            return
        idx = acciones.index(sel)
        if idx == 0:
            self._ver_miembros_lista(acc, lista)
        elif idx == 1:
            self._anadir_handle_a_lista(acc, lista)
        elif idx == 2:
            self._silenciar_lista(acc, lista, True)
        else:
            self._silenciar_lista(acc, lista, False)

    def _ver_miembros_lista(self, acc, lista):
        speak(_("Cargando miembros..."), interrupt=True)

        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("La lista «{0}» no tiene cuentas. Añade alguna.").format(lista["name"]),
                             _("Miembros de la lista"))
                return
            opciones = [f"{a['display_name'] or a['handle']} (@{a['handle']})" for a in res]
            sel = wx.GetSingleChoice(
                _("Miembros de «{0}» ({1}). Elige una para quitarla de la lista:").format(
                    lista["name"], len(res)), _("Miembros de la lista"), opciones, self.f)
            if not sel:
                return
            miembro = res[opciones.index(sel)]
            if not confirmar(self.f, _("¿Quitar a @{0} de la lista «{1}»?").format(
                    miembro["handle"], lista["name"]), _("Quitar de la lista")):
                return

            def _cb2(r2, e2):
                if e2:
                    notificar(_("No se pudo quitar: {0}").format(e2))
                else:
                    notificar(_("Cuenta quitada de la lista."))
            bluesky_service.quitar_miembro_lista_async(acc, miembro["item_uri"], callback=_cb2)
        bluesky_service.obtener_miembros_lista_async(acc, lista["uri"], callback=_cb)

    def _anadir_handle_a_lista(self, acc, lista):
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Handle de la cuenta a añadir (por ejemplo, nombre.bsky.social):"),
                                    _("Añadir a la lista"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        handle = dlg.GetValue().strip()
        dlg.Destroy()
        if not handle:
            return

        def _cb(res, error):
            if error:
                notificar(_("No se pudo añadir a la lista: {0}").format(error))
            else:
                notificar(_("Cuenta añadida a la lista «{0}».").format(lista["name"]))
        bluesky_service.anadir_miembro_lista_async(acc, lista["uri"], handle, callback=_cb)

    def _silenciar_lista(self, acc, lista, silenciar):
        def _cb(res, error):
            if error:
                notificar(_("Error: {0}").format(error))
            elif silenciar:
                notificar(_("Te has suscrito a la lista de silencio «{0}».").format(lista["name"]))
            else:
                notificar(_("Has quitado el silencio de la lista «{0}».").format(lista["name"]))
        bluesky_service.silenciar_lista_async(acc, lista["uri"], silenciar, callback=_cb)

    # palabras_silenciadas y servicios_etiquetas: ver bluesky_controller_moderacion.

    def toggle_firehose(self):
        """Activa/desactiva la monitorización en vivo (firehose): avisa al instante de las
        menciones a tu cuenta. Apagado por defecto (un cliente personal no lo necesita
        siempre); es una capacidad avanzada y consume más recursos."""
        fh = getattr(self, "_firehose", None)
        if fh and fh.activo:
            fh.stop()
            self._firehose = None
            speak(_("Monitorización en vivo de Bluesky desactivada."), interrupt=True)
            return
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        clase = self._clase_tiempo_real()
        if clase is None:
            mostrar_info(self.f, _("El tiempo real no está disponible en esta instalación."), _("Bluesky"))
            return
        speak(_("Activando monitorización en vivo..."), interrupt=True)

        def _bg(ctx):
            return bluesky_service._yo(acc)["did"]

        def _cb(did, error):
            if error or not did:
                notificar(_("No se pudo activar la monitorización."))
                return
            self._firehose = clase(
                mi_did=did, on_match=lambda info: wx.CallAfter(self._on_firehose_match, info))
            if self._firehose.start():
                speak(_("Monitorización en vivo activada. Recibirás un aviso de las menciones y respuestas a tu cuenta."), interrupt=True)
            else:
                notificar(_("No se pudo iniciar la monitorización en vivo."))
        task_manager.start_task(_bg, callback=_cb, pass_context=True)

    @staticmethod
    def _clase_tiempo_real():
        """Devuelve la clase de tiempo real preferida: JETSTREAM (JSON, filtros del
        servidor, cursor y reconexión robusta; auditoría 12.2) y, si no hay websockets,
        el firehose CAR clásico de reserva. None si no hay ninguna."""
        try:
            from app.services.bluesky.bluesky_jetstream import HAS_JETSTREAM, BlueskyJetstream
            if HAS_JETSTREAM:
                return BlueskyJetstream
        except Exception:
            pass
        try:
            from app.services.bluesky.bluesky_firehose import HAS_FIREHOSE, BlueskyFirehose
            if HAS_FIREHOSE:
                return BlueskyFirehose
        except Exception:
            pass
        return None

    def _on_firehose_match(self, info):
        """Se invoca (en el hilo de UI) cuando el tiempo real detecta una mención o una
        respuesta REAL a un post tuyo (verificada por facets/StrongRef, auditoría 12.1)."""
        if info.get("es_mio"):
            return          # es tuyo: entra en la lista, pero no se avisa
        if not (info.get("is_mention") or info.get("is_reply")):
            return
        event_bus.emit("sound_trigger", "bluesky_new_mention")
        try:
            import wx.adv
            titulo = _("Mención en Bluesky") if info.get("is_mention") else _("Respuesta en Bluesky")
            texto = (info.get("text") or "").strip()[:120] or titulo
            n = wx.adv.NotificationMessage(titulo, texto, parent=self.f)
            n.Show(timeout=8)
        except Exception as e:
            log.debug(f"No se pudo notificar la mención del tiempo real: {e}")

    def detener_firehose(self):
        """Detiene la monitorización si está activa (llamado al cerrar la app)."""
        for attr in ("_firehose", "_fh_realtime"):
            fh = getattr(self, attr, None)
            if fh:
                try:
                    fh.stop()
                except Exception:
                    pass
            setattr(self, attr, None)

    # ------------------------------------------------------------------
    # TIEMPO REAL (Inicio en directo vía firehose filtrado por las cuentas que sigues)
    # ------------------------------------------------------------------
    def activar_tiempo_real(self):
        """Arranca el firehose filtrado por las cuentas que SIGUES: cuando una de ellas publica,
        el Inicio se refresca al instante. El contenido lo trae el sondeo (formato/orden correctos);
        el firehose solo avisa. Consume más recursos que el modo por intervalo."""
        if getattr(self, "_fh_realtime", None) and self._fh_realtime.activo:
            return
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        clase = self._clase_tiempo_real()
        if clase is None:
            return

        def _bg(ctx):
            client = bluesky_service.obtener_cliente(acc)
            mi_did = bluesky_service._yo(acc)["did"]
            dids, cursor = [], None
            # Con Jetstream el filtro por DIDs lo aplica EL SERVIDOR (hasta 10.000), así
            # que merece la pena traer TODOS los seguidos (antes se cortaba en ~1000 y las
            # cuentas grandes quedaban incompletas, auditoría 12.1).
            for _ in range(100):   # hasta ~10.000 cuentas seguidas
                res = client.get_follows(mi_did, cursor=cursor)
                dids.extend(getattr(fo, "did", "") for fo in (getattr(res, "follows", []) or []))
                cursor = getattr(res, "cursor", None)
                if not cursor or len(dids) >= 10000:
                    break
            # TU PROPIO identificador también entra en el filtro. Sin él, lo que publicas no
            # dispara el tiempo real (no te sigues a ti mismo) y tus publicaciones tardaban en
            # aparecer lo que tardara el siguiente sondeo, aunque las hubieras escrito tú. Ahora
            # entran al instante, las escribas aquí, en el móvil o en la web.
            if mi_did and mi_did not in dids:
                dids.append(mi_did)
            return mi_did, dids

        def _cb(data, error):
            if error or not data:
                log.debug(f"No se pudo activar el tiempo real de Bluesky: {error}")
                return
            mi_did, dids = data
            kwargs = {"mi_did": mi_did, "dids_seguidos": dids,
                      "on_follow_activity": lambda: wx.CallAfter(self._refrescar_tiempo_real)}
            try:
                from app.services.bluesky.bluesky_jetstream import BlueskyJetstream
                if clase is BlueskyJetstream:
                    kwargs["clave_cursor"] = f"rt:{acc}"  # cursor persistido por cuenta
            except Exception:
                pass
            self._fh_realtime = clase(**kwargs)
            self._rt_cuenta = acc          # de qué cuenta es este tiempo real
            self._fh_realtime.start()
            log.info(f"Tiempo real de Bluesky activado ({len(dids)} cuentas seguidas).")
        task_manager.start_task(_bg, callback=_cb, pass_context=True)

    def desactivar_tiempo_real(self):
        fh = getattr(self, "_fh_realtime", None)
        if fh:
            try:
                fh.stop()
            except Exception:
                pass
            self._fh_realtime = None
            self._rt_cuenta = None

    def _refrescar_tiempo_real(self):
        """Trae la novedad que ha detectado el tiempo real, con anti-rebote (un refresco cada
        pocos segundos aunque lleguen muchas publicaciones seguidas).

        Funciona ESTÉS O NO en Bluesky: si lo estás, se actualiza la lista sin moverte el foco;
        si estás en otra red, la publicación se descarga igualmente a la base de datos y ya está
        ahí cuando vuelves. Antes, fuera de Bluesky no se traía nada y las novedades se
        acumulaban hasta que entrabas."""
        import time as _t
        ahora = _t.time()
        if ahora - getattr(self, "_ultimo_rt", 0) < 4:
            return
        self._ultimo_rt = ahora
        f = self.f
        try:
            if f and f.active_bluesky_account_id:
                self.actualizar_incremental()
                return
            # Estás en otra red: sincronizar en segundo plano el Inicio de la cuenta que tiene el
            # tiempo real. No se toca la interfaz (no es su buffer), solo la caché.
            acc = getattr(self, "_rt_cuenta", None) or self._cuenta_activa_o_primera()
            if acc:
                bluesky_service.cargar_timeline_async(acc, timeline_type="home")
        except Exception as e:
            log.debug(f"Refresco de tiempo real falló: {e}")

    def crear_starter_pack(self):
        """Crea un starter pack propio a partir de una de tus LISTAS ya existentes (Bluesky exige
        que un starter pack referencie una lista). Pide nombre, descripción y la lista."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        from app.ui.accessible_controls import CustomTextEntryDialog

        def _con_listas(res, error):
            if error:
                notificar(_("Error al cargar tus listas: {0}").format(error))
                return
            if not res:
                mostrar_info(self.f, _("Necesitas al menos una lista. Crea una con 'Crear lista...' "
                                       "y añádele cuentas antes de hacer el paquete."), _("Sin listas"))
                return
            opciones = [l["name"] for l in res]
            sel = wx.GetSingleChoice(_("Elige la lista que incluirá el paquete de bienvenida:"),
                                     _("Crear starter pack"), opciones, self.f)
            if not sel:
                return
            lista = res[opciones.index(sel)]
            dn = CustomTextEntryDialog(self.f, _("Nombre del paquete de bienvenida:"), _("Crear starter pack"))
            if dn.ShowModal() != wx.ID_OK:
                dn.Destroy()
                return
            nombre = dn.GetValue().strip()
            dn.Destroy()
            if not nombre:
                return
            dd = CustomTextEntryDialog(self.f, _("Descripción (opcional):"), _("Crear starter pack"), multiline=True)
            desc = dd.GetValue().strip() if dd.ShowModal() == wx.ID_OK else ""
            dd.Destroy()

            def _cb(r, e):
                if e:
                    notificar(_("No se pudo crear el paquete: {0}").format(e))
                else:
                    notificar(_("Paquete de bienvenida creado."))
            bluesky_service.crear_starter_pack_async(acc, nombre, desc, lista["uri"], callback=_cb)

        speak(_("Cargando tus listas..."), interrupt=True)
        bluesky_service.obtener_listas_async(acc, callback=_con_listas)

    def mi_perfil(self):
        """Abre el editor de perfil."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        dlg = BlueskyProfileDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def app_passwords(self):
        """Abre el gestor de contraseñas de aplicación."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        dlg = BlueskyAppPasswordsDialog(self.f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def borradores(self):
        """Lista los borradores locales y abre el elegido en el compositor."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            mostrar_info(self.f, _("Primero añade una cuenta de Bluesky."), _("Sin cuentas"))
            return
        drafts = list(config.get("bluesky", "drafts", []))
        if not drafts:
            mostrar_info(self.f, _("No hay borradores guardados."), _("Borradores"))
            return
        opciones = [(d.get("text", "") or "(vacío)")[:60] for d in drafts]
        sel = wx.GetSingleChoice(_("Elige un borrador para abrir:"), _("Borradores"), opciones, self.f)
        if not sel:
            return
        idx = opciones.index(sel)
        draft = drafts[idx]
        dlg = BlueskyComposeDialog(self.f, acc, texto_inicial=draft.get("text", ""))
        dlg.ShowModal()
        dlg.Destroy()
        # Ofrecer eliminar el borrador usado
        if confirmar(self.f, _("¿Eliminar este borrador de la lista?"), _("Borradores")):
            drafts.pop(idx)
            config.set("bluesky", "drafts", drafts)
