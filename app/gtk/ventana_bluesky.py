# -*- coding: utf-8 -*-
"""Bluesky en la ventana principal (mixin de VentanaPrincipal).

Las acciones comparten teclas con Mastodon (Ctrl+B me gusta, Ctrl+R responder...): los manejadores de
ventana.py llaman aquí cuando el elemento enfocado es de Bluesky."""

import logging

import time

from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.core.plataforma import mostrar_toast
from app.core.speech import speak
from app.services.bluesky.bluesky_service import bluesky_service

from . import datos_bluesky

log = logging.getLogger(__name__)


class _BlueskyMixin:
    # ------------------------------------------------------------------ contexto
    def _bsky_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de Bluesky."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "bluesky"
        n = self.nodo_actual
        return n is not None and n.servicio == "bluesky"

    def _bsky_obj(self, mensajes: bool = False):
        """La fila de Bluesky enfocada. Las conversaciones y mensajes solo cuentan si `mensajes` es True."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "bluesky":
            self.avisar(_("Selecciona una publicación de Bluesky"))
            return None
        if not mensajes and obj.fila.extra.get("tipo") in ("convo", "mensaje"):
            self.avisar(_("Esto es un mensaje directo, no una publicación"))
            return None
        return obj

    def _bsky_en_dm(self) -> bool:
        n = self.nodo_actual
        return n is not None and n.servicio == "bluesky" and (n.timeline == "dms" or str(n.timeline).startswith("dm:"))

    def _bsky_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "bluesky" and n.account_id:
            return n.account_id
        cuentas = datos_bluesky.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de Bluesky"))
        return None

    def _bsky_post_db_id(self, obj):
        """Id local de la publicación de la fila (en una notificación, la publicación a la que se refiere)."""
        x = obj.fila.extra
        if x.get("tipo") == "notificacion":
            return f"{x['account_id']}:{x['uri']}" if x.get("uri") else None
        return obj.fila.id

    def _bsky_nodos_cuenta(self, account_id):
        for raiz in getattr(self, "arbol", []):
            if raiz.servicio == "bluesky":
                return [h for c in raiz.hijos if c.account_id == account_id for h in c.hijos]
        return []

    # ------------------------------------------------------------------ actualizar y cargar más
    def bsky_actualizar_nodo(self, nodo, silencioso: bool = False):
        clave = (nodo.account_id, nodo.timeline)
        primera = clave not in self._cargados

        def _fin(res, err):
            if err:
                if not silencioso:
                    self.avisar(_("Error al actualizar: {e}").format(e=err))
                return
            self._cargados.add(clave)
            nuevos = len(res or [])
            if nodo is self.nodo_actual and not self.busqueda:
                obj = self._fila_actual()
                self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
            if nodo.timeline == "notifications":
                bluesky_service.marcar_notificaciones_vistas_async(nodo.account_id, callback=lambda r, e: None)
            if primera or not nuevos or nodo.timeline == "dms" or nodo.timeline.startswith("dm:"):
                if not silencioso and nodo is self.nodo_actual:
                    self.estado.set_text(_("{t}: actualizado").format(t=nodo.titulo))
                return
            if nodo.timeline == "notifications":
                speak(_("{n} notificaciones nuevas").format(n=nuevos), interrupt=False)
                event_bus.emit("sound_trigger", "bluesky_new_notification")
            elif nodo is self.nodo_actual:
                self.avisar(_("{n} nuevas").format(n=nuevos), interrumpir=False)
        if nodo.timeline == "dms":
            bluesky_service.cargar_convos_async(nodo.account_id, callback=_fin)
        elif nodo.timeline.startswith("dm:"):
            convo = nodo.timeline[3:]

            def _leido(res, err):
                _fin(res, err)
                if not err:
                    bluesky_service.marcar_convo_leido_async(nodo.account_id, convo, callback=lambda r, e: None)
            bluesky_service.cargar_mensajes_async(nodo.account_id, convo, callback=_leido)
        elif nodo.timeline == "bookmarks":
            bluesky_service.cargar_bookmarks_async(nodo.account_id, callback=_fin)
        else:
            bluesky_service.cargar_timeline_async(nodo.account_id, nodo.timeline, callback=_fin)

    def bsky_cargar_mas(self):
        n = self.nodo_actual
        if n is None or not n.listable:
            return
        self.avisar(_("Cargando publicaciones anteriores…"))
        actual = self._fila_actual()

        def _fin(res, err):
            if err:
                self.avisar(_("Error al cargar: {e}").format(e=err))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            if not (res or []):
                self.avisar(_("No hay más publicaciones"))
            else:
                self.avisar(_("{n} cargadas").format(n=len(res)))
        if n.timeline == "dms":
            bluesky_service.cargar_convos_async(n.account_id, cargar_mas=True, callback=_fin)
        elif n.timeline.startswith("dm:"):
            bluesky_service.cargar_mensajes_async(n.account_id, n.timeline[3:], cargar_mas=True, callback=_fin)
        elif n.timeline == "bookmarks":
            bluesky_service.cargar_bookmarks_async(n.account_id, cargar_mas=True, callback=_fin)
        else:
            bluesky_service.cargar_timeline_async(n.account_id, n.timeline, cargar_mas=True, callback=_fin)

    def _sondeo_bluesky(self):
        for acc_id, _n in datos_bluesky.cuentas():
            for nodo in self._bsky_nodos_cuenta(acc_id):
                if nodo.timeline in ("home", "notifications") and (acc_id, nodo.timeline) in self._cargados:
                    self.bsky_actualizar_nodo(nodo, silencioso=True)
            if (acc_id, "dms") in self._cargados and bluesky_service.chat_disponible(acc_id) is not False:
                self._bsky_sondeo_chat(acc_id)

    def _bsky_sondeo_chat(self, acc_id):
        """Mensajes directos nuevos: se sincroniza el registro del chat y se avisa si llegó alguno."""
        def _fin(res, err):
            if err or not res or not res.get("nuevos"):
                return
            speak(_("{n} mensajes directos nuevos").format(n=res["nuevos"]), interrupt=False)
            event_bus.emit("sound_trigger", "bluesky_new_notification")

            def _recargado(r, e):
                n = self.nodo_actual
                if n is not None and n.servicio == "bluesky" and n.account_id == acc_id and \
                        (n.timeline == "dms" or n.timeline[3:] in res.get("convos", [])):
                    obj = self._fila_actual()
                    self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
            bluesky_service.cargar_convos_async(acc_id, callback=_recargado)
        bluesky_service.sincronizar_chat_async(acc_id, callback=_fin)

    # ------------------------------------------------------------------ acciones sobre una publicación
    def _bsky_accion_estado(self, obj, on: str, off: str, propiedad: str, texto_on: str, texto_off: str):
        db_id = self._bsky_post_db_id(obj)
        if not db_id:
            self.avisar(_("Esta notificación no tiene publicación"))
            return
        activo = obj.get_property(propiedad)

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            obj.set_property(propiedad, not activo)
            self.avisar(texto_off if activo else texto_on)
        bluesky_service.ejecutar_accion_post_async(obj.fila.extra["account_id"], db_id, off if activo else on, callback=_fin)

    def bsky_me_gusta(self):
        obj = self._bsky_obj()
        if obj:
            self._bsky_accion_estado(obj, "like", "unlike", "favorito", _("Me gusta"), _("Me gusta quitado"))

    def bsky_republicar(self):
        obj = self._bsky_obj()
        if obj:
            self._bsky_accion_estado(obj, "repost", "unrepost", "impulsado", _("Republicado"), _("Republicación quitada"))

    def bsky_marcador(self):
        obj = self._bsky_obj()
        if not obj:
            return
        db_id = self._bsky_post_db_id(obj)
        if not db_id:
            return
        activo = obj.marcador

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            obj.marcador = not activo
            self.avisar(_("Marcador quitado") if activo else _("Añadido a marcadores"))
        (bluesky_service.borrar_bookmark_async if activo else bluesky_service.crear_bookmark_async)(
            obj.fila.extra["account_id"], db_id, callback=_fin)

    def bsky_nuevo(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "bluesky" and n.timeline.startswith("dm:"):
            return self._bsky_escribir_mensaje(n.account_id, n.timeline[3:], n.titulo)
        if n is not None and n.servicio == "bluesky" and n.timeline == "dms":
            return self.bsky_mensaje_nuevo()
        acc = self._bsky_cuenta()
        if acc is not None:
            from .dialogos_bluesky import DialogoPublicarBsky
            DialogoPublicarBsky(self, acc).present()

    def bsky_responder(self):
        obj = self._bsky_obj(mensajes=True)
        x = obj.fila.extra if obj else {}
        if x.get("tipo") in ("convo", "mensaje"):
            return self._bsky_escribir_mensaje(x["account_id"], x["convo_id"], x.get("titulo") or obj.fila.feed_titulo)
        if not obj or not x.get("uri"):
            return
        from .dialogos_bluesky import DialogoPublicarBsky
        DialogoPublicarBsky(self, x["account_id"], responde_a=x["uri"]).present()

    def bsky_citar(self):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri"):
            return
        from .dialogos_bluesky import DialogoPublicarBsky
        DialogoPublicarBsky(self, x["account_id"], cita_uri=x["uri"]).present()

    def bsky_borrar(self):
        obj = self._bsky_obj(mensajes=True)
        if not obj:
            return
        x = obj.fila.extra
        if x.get("tipo") in ("convo", "mensaje"):
            return self._bsky_borrar_mensaje(obj)
        if not x.get("propio") or x.get("tipo") == "notificacion":
            self.avisar(_("Solo puedes borrar tus propias publicaciones"))
            return
        from .dialogos import confirmar_async

        def _hacer():
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo borrar: {e}").format(e=err))
                    return
                for i in range(self.almacen_lista.get_n_items()):
                    if self.almacen_lista.get_item(i) is obj:
                        self.almacen_lista.remove(i)
                        n = self.almacen_lista.get_n_items()
                        if n:
                            self.seleccion_lista.set_selected(min(i, n - 1))
                        break
                self.avisar(_("Publicación borrada"))
            bluesky_service.borrar_post_async(x["account_id"], x["uri"], callback=_fin)
        confirmar_async(self, _("¿Borrar esta publicación? No se puede deshacer."), _hacer, etiqueta_si=_("Borrar"))

    # ------------------------------------------------------------------ hilos, cuentas y búsquedas
    def bsky_conversacion(self):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri"):
            return
        self._abrir_buffer(x["account_id"], f"thread:{x['uri']}", _("Conversación de {n}").format(n=obj.fila.autor),
                           servicio="bluesky")

    def bsky_abrir_cronologia(self, account_id: int, did: str, handle: str):
        self._abrir_buffer(account_id, f"author:{did}", _("Publicaciones de @{u}").format(u=handle), servicio="bluesky")

    def bsky_cronologia_usuario(self):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("usuario"):
            return
        did = x.get("did") or x["usuario"]
        self.bsky_abrir_cronologia(x["account_id"], did, x["usuario"])

    def bsky_perfil(self):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("usuario"):
            return
        from .dialogos_bluesky import DialogoPerfilBsky
        DialogoPerfilBsky(self, x["account_id"], x.get("did") or x["usuario"]).present()

    def bsky_buscar(self):
        acc = self._bsky_cuenta()
        if acc is None:
            return
        from .dialogos import DialogoTexto

        def _buscar(texto):
            self._abrir_buffer(acc, f"search:{texto}", _("Búsqueda: {t}").format(t=texto), servicio="bluesky")
        DialogoTexto(self, _("Buscar en Bluesky"), _("_Buscar:"), _buscar,
                     _("Busca publicaciones. Puedes usar #hashtags y from:usuario."), _("_Buscar")).present()

    def bsky_abrir_feed(self):
        self._bsky_elegir("feed")

    def bsky_abrir_lista(self):
        self._bsky_elegir("list")

    def _bsky_elegir(self, tipo: str):
        acc = self._bsky_cuenta()
        if acc is None:
            return
        self.avisar(_("Leyendo tus feeds…") if tipo == "feed" else _("Leyendo tus listas…"))

        def _fin(items, err):
            if err:
                self.avisar(_("No se pudo leer: {e}").format(e=err))
                return
            if not items:
                self.avisar(_("No tienes feeds guardados") if tipo == "feed" else _("No tienes listas"))
                return
            from .dialogos import DialogoElegir

            def _elegido(i):
                it = items[i]
                self._abrir_buffer(acc, f"{tipo}:{it['uri']}", (_("Feed {t}") if tipo == "feed" else _("Lista {t}")).format(t=it["name"]),
                                   servicio="bluesky")
            DialogoElegir(self, _("Abrir un feed") if tipo == "feed" else _("Abrir una lista"),
                          _("_Feed:") if tipo == "feed" else _("_Lista:"), [it["name"] for it in items], _elegido,
                          _("_Abrir")).present()
        (bluesky_service.obtener_feeds_guardados_async if tipo == "feed" else bluesky_service.obtener_listas_async)(
            acc, callback=_fin)

    def bsky_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "bluesky" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de Bluesky"))
            return
        from .dialogos import confirmar_async
        acc = n.account_id
        nombre = next((nm for i, nm in datos_bluesky.cuentas() if i == acc), "")

        def _hacer():
            bluesky_service.eliminar_cuenta(acc)
            datos_bluesky.EXTRA.pop(acc, None)
            self.cargar_arbol(conservar=False)
            self.iniciar_tiempo_real_bluesky()
            self.avisar(_("Cuenta eliminada"))
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ mensajes directos
    def bsky_abrir_convo(self, obj):
        x = obj.fila.extra
        self._abrir_buffer(x["account_id"], f"dm:{x['convo_id']}", x.get("titulo") or obj.fila.autor, servicio="bluesky")

    def _bsky_escribir_mensaje(self, account_id: int, convo_id: str, titulo: str):
        from .dialogos_bluesky_extra import DialogoMensajeBsky

        def _enviado():
            clave = (account_id, f"dm:{convo_id}")
            for n in self._bsky_nodos_cuenta(account_id):
                if n.timeline == clave[1]:
                    self.bsky_actualizar_nodo(n, silencioso=True)
        DialogoMensajeBsky(self, account_id, convo_id, titulo, _enviado).present()

    def bsky_mensaje_nuevo(self, account_id=None, actor: str = None):
        """Abre (o crea) la conversación con una cuenta. Sin `actor`, pregunta por el usuario."""
        acc = account_id if account_id is not None else self._bsky_cuenta()
        if acc is None:
            return

        def _con(did):
            self.avisar(_("Abriendo la conversación…"))

            def _fin(res, err):
                if err or not res:
                    self.avisar(_("No se pudo abrir la conversación: {e}").format(e=err))
                    return
                self._abrir_buffer(acc, f"dm:{res['convo_id']}", res["title"], servicio="bluesky")
            bluesky_service.abrir_convo_con_async(acc, did, callback=_fin)

        def _resolver(texto):
            if texto.startswith("did:"):
                return _con(texto)

            def _perfil(d, err):
                if err or not d:
                    self.avisar(_("No se encontró esa cuenta"))
                    return
                _con(d["did"])
            bluesky_service.obtener_perfil_async(acc, texto.lstrip("@"), callback=_perfil)
        if actor:
            return _resolver(actor)
        from .dialogos import DialogoTexto
        DialogoTexto(self, _("Mensaje directo"), _("_Usuario (por ejemplo ana.bsky.social):"), _resolver,
                     aceptar=_("_Abrir conversación")).present()

    def bsky_mensaje_a_autor(self):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if obj and x.get("usuario"):
            self.bsky_mensaje_nuevo(x["account_id"], x.get("did") or x["usuario"])

    def _bsky_borrar_mensaje(self, obj):
        from .dialogos import confirmar_async
        x = obj.fila.extra
        if x["tipo"] == "convo":
            def _salir():
                def _fin(res, err):
                    if err:
                        self.avisar(_("No se pudo salir: {e}").format(e=err))
                        return
                    self.cargar_lista()
                    self.avisar(_("Has salido de la conversación"))
                bluesky_service.salir_convo_async(x["account_id"], x["convo_id"], callback=_fin)
            confirmar_async(self, _("¿Salir de la conversación con {t} y borrarla de este equipo?").format(t=x["titulo"]),
                            _salir, etiqueta_si=_("Salir"))
            return

        def _borrar():
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo borrar: {e}").format(e=err))
                    return
                for i in range(self.almacen_lista.get_n_items()):
                    if self.almacen_lista.get_item(i) is obj:
                        self.almacen_lista.remove(i)
                        n = self.almacen_lista.get_n_items()
                        if n:
                            self.seleccion_lista.set_selected(min(i, n - 1))
                        break
                self.avisar(_("Mensaje borrado para ti"))
            bluesky_service.borrar_mensaje_async(x["account_id"], x["convo_id"], x["message_id"], callback=_fin)
        confirmar_async(self, _("¿Borrar este mensaje solo para ti? La otra persona lo seguirá viendo."), _borrar,
                        etiqueta_si=_("Borrar"))

    def bsky_convo_silenciar(self):
        obj = self._bsky_obj(mensajes=True)
        x = obj.fila.extra if obj else {}
        if not obj or x.get("tipo") not in ("convo", "mensaje"):
            self.avisar(_("Selecciona una conversación"))
            return
        nodo_convo = self.nodo_actual.timeline.startswith("dm:")
        silenciar = True
        fila = next((f for f in datos_bluesky._listar_convos(x["account_id"], 500) if f.extra["convo_id"] == x["convo_id"]), None)
        if fila is not None:
            silenciar = not fila.extra["silenciada"]

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.avisar(_("Conversación silenciada") if silenciar else _("Conversación reactivada"))
            if not nodo_convo:
                self.cargar_lista(conservar_id=obj.fila.id, incremental=True)
        bluesky_service.silenciar_convo_async(x["account_id"], x["convo_id"], silenciar, callback=_fin)

    def bsky_reaccionar(self):
        obj = self._bsky_obj(mensajes=True)
        x = obj.fila.extra if obj else {}
        if not obj or x.get("tipo") != "mensaje":
            self.avisar(_("Selecciona un mensaje"))
            return
        from .dialogos import DialogoTexto

        def _hacer(emoji):
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo reaccionar: {e}").format(e=err))
                    return
                self.avisar(_("Reacción enviada"))
                for n in self._bsky_nodos_cuenta(x["account_id"]):
                    if n.timeline == f"dm:{x['convo_id']}":
                        self.bsky_actualizar_nodo(n, silencioso=True)
            bluesky_service.reaccionar_mensaje_async(x["account_id"], x["convo_id"], x["message_id"], emoji[:2],
                                                     callback=_fin)
        DialogoTexto(self, _("Reaccionar al mensaje"), _("_Emoji:"), _hacer, aceptar=_("_Reaccionar")).present()

    # ------------------------------------------------------------------ interacciones, informes, ocultar
    def bsky_interacciones(self, tipo: str):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if obj and x.get("uri"):
            from .dialogos_bluesky_extra import DialogoActoresBsky
            DialogoActoresBsky(self, x["account_id"], tipo, uri=x["uri"]).present()

    def bsky_ocultar(self):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri") or x.get("tipo") == "notificacion":
            self.avisar(_("Selecciona una publicación"))
            return

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo ocultar: {e}").format(e=err))
                return
            for i in range(self.almacen_lista.get_n_items()):
                if self.almacen_lista.get_item(i) is obj:
                    self.almacen_lista.remove(i)
                    n = self.almacen_lista.get_n_items()
                    if n:
                        self.seleccion_lista.set_selected(min(i, n - 1))
                    break
            self.avisar(_("Publicación oculta para ti"))
        bluesky_service.ocultar_post_async(x["account_id"], x["uri"], True, callback=_fin)

    def _bsky_raiz(self, account_id: int, uri: str) -> str:
        """URI de la publicación raíz del hilo (se sube por las respuestas que haya en la caché)."""
        from app.core.database import db
        for _i in range(50):
            fila = db.query_one("SELECT COALESCE(reply_parent_uri, '') FROM bluesky_posts WHERE account_id = ? AND uri = ?",
                                (account_id, uri))
            if not fila or not fila[0]:
                break
            uri = fila[0]
        return uri

    def bsky_silenciar_hilo(self, silenciar: bool = True):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri"):
            return
        raiz = self._bsky_raiz(x["account_id"], x["uri"])

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.avisar(_("Hilo silenciado: no recibirás sus notificaciones") if silenciar
                        else _("Hilo reactivado"))
        bluesky_service.silenciar_hilo_async(x["account_id"], raiz, silenciar, callback=_fin)

    def bsky_reportar(self, cuenta: bool = False):
        obj = self._bsky_obj()
        x = obj.fila.extra if obj else {}
        if not obj:
            return
        from .dialogos_bluesky_extra import DialogoReportarBsky
        if cuenta:
            if x.get("did") or x.get("usuario"):
                DialogoReportarBsky(self, x["account_id"], did=x.get("did"), quien="@" + x.get("usuario", "")).present()
            return
        db_id = self._bsky_post_db_id(obj)
        if db_id:
            DialogoReportarBsky(self, x["account_id"], post_db_id=db_id, quien=_("la publicación")).present()

    # ------------------------------------------------------------------ mi cuenta
    def _bsky_dialogo(self, nombre: str, *args):
        acc = self._bsky_cuenta()
        if acc is not None:
            from . import dialogos_bluesky_extra as extra
            getattr(extra, nombre)(self, acc, *args).present()

    def bsky_actores(self, tipo: str):
        acc = self._bsky_cuenta()
        if acc is not None:
            from .dialogos_bluesky_extra import DialogoActoresBsky
            DialogoActoresBsky(self, acc, tipo).present()

    def _registrar_acciones_bluesky(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("bsky-mensaje-nuevo", self.bsky_mensaje_nuevo)
        a("bsky-mensaje-autor", lambda: self.ws_mensaje_a_autor() if self._ws_ctx() else self.bsky_mensaje_a_autor(),
          ["<Ctrl><Alt>m"])
        a("bsky-convo-silenciar", self.bsky_convo_silenciar)
        a("bsky-reaccionar", self.bsky_reaccionar)
        a("bsky-quien-gusta", lambda: self.bsky_interacciones("likes"))
        a("bsky-quien-republico", lambda: self.bsky_interacciones("reposts"))
        a("bsky-quien-cito", lambda: self.bsky_interacciones("quotes"))
        a("bsky-ocultar", self.bsky_ocultar)
        a("bsky-silenciar-hilo", lambda: self.bsky_silenciar_hilo(True))
        a("bsky-reactivar-hilo", lambda: self.bsky_silenciar_hilo(False))
        a("bsky-reportar", self.bsky_reportar)
        a("bsky-reportar-cuenta", lambda: self.bsky_reportar(True))
        a("bsky-seguidores", lambda: self.bsky_actores("followers"))
        a("bsky-siguiendo", lambda: self.bsky_actores("follows"))
        a("bsky-bloqueadas", lambda: self.bsky_actores("blocks"))
        a("bsky-silenciadas", lambda: self.bsky_actores("mutes"))
        a("bsky-listas", lambda: self._bsky_dialogo("DialogoListasBsky"))
        a("bsky-feeds", lambda: self._bsky_dialogo("DialogoFeedsBsky"))
        a("bsky-palabras", lambda: self._bsky_dialogo("DialogoPalabrasBsky"))
        a("bsky-moderacion", lambda: self._bsky_dialogo("DialogoModeracionBsky"))
        a("bsky-etiquetadores", lambda: self._bsky_dialogo("DialogoEtiquetadoresBsky"))
        a("bsky-claves", lambda: self._bsky_dialogo("DialogoClavesAppBsky"))
        a("bsky-mi-perfil", lambda: self._bsky_dialogo("DialogoMiPerfilBsky"))
        a("bsky-eliminar-cuenta", self.bsky_eliminar_cuenta)

    # ------------------------------------------------------------------ tiempo real
    def iniciar_tiempo_real_bluesky(self):
        """Arranca (o detiene) el tiempo real de Bluesky según la preferencia."""
        from .tiempo_real_bluesky import TiempoRealBluesky
        if not config.get("gtk", "bluesky_realtime", True):
            if self._tiempo_real_bsky is not None:
                self._tiempo_real_bsky.detener()
            return
        if self._tiempo_real_bsky is None:
            self._tiempo_real_bsky = TiempoRealBluesky(self._bsky_novedad, self._bsky_aviso)
        self._tiempo_real_bsky.sincronizar([a for a, _n in datos_bluesky.cuentas()])

    def _bsky_novedad(self, account_id: int):
        """Alguien a quien sigues ha publicado: se refresca el Inicio, como mucho una vez cada pocos segundos."""
        ahora = time.time()
        if ahora - getattr(self, "_ultimo_rt_bsky", 0) < 4:
            return
        self._ultimo_rt_bsky = ahora
        nodo = next((n for n in self._bsky_nodos_cuenta(account_id) if n.timeline == "home"), None)
        if nodo is not None and (account_id, "home") in self._cargados:
            self.bsky_actualizar_nodo(nodo, silencioso=True)
        else:
            bluesky_service.cargar_timeline_async(account_id, "home", callback=lambda r, e: None)

    def _bsky_aviso(self, account_id: int, info: dict):
        """Mención o respuesta a tu cuenta detectada en directo."""
        if info.get("es_mio") or not (info.get("is_mention") or info.get("is_reply")):
            return
        titulo = _("Mención en Bluesky") if info.get("is_mention") else _("Respuesta en Bluesky")
        speak(titulo, interrupt=False)
        event_bus.emit("sound_trigger", "bluesky_new_mention")
        mostrar_toast((info.get("text") or "").strip()[:120] or titulo, titulo)
        for n in self._bsky_nodos_cuenta(account_id):
            if n.timeline in ("notifications", "mentions") and (account_id, n.timeline) in self._cargados:
                self.bsky_actualizar_nodo(n, silencioso=True)
