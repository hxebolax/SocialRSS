# -*- coding: utf-8 -*-
"""W Social en la ventana principal (mixin de VentanaPrincipal).

Las acciones comparten teclas con Mastodon (Ctrl+B me gusta, Ctrl+R responder...): los manejadores de
ventana.py llaman aquí cuando el elemento enfocado es de W Social."""

import logging

from app.core.config_manager import config
from app.core.event_bus import event_bus
from app.core.plataforma import llamar_en_ui, mostrar_toast
from app.core.speech import speak
from app.services.wsocial.wsocial_service import wsocial_service

from . import datos_wsocial
from .datos_wsocial import adaptar

log = logging.getLogger(__name__)


class _WSocialMixin:
    # ------------------------------------------------------------------ contexto
    def _ws_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de W Social."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "wsocial"
        n = self.nodo_actual
        return n is not None and n.servicio == "wsocial"

    def _ws_obj(self, mensajes: bool = False):
        """La fila de W Social enfocada. Las conversaciones y mensajes solo cuentan si `mensajes` es True."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "wsocial":
            self.avisar(_("Selecciona una publicación de W Social"))
            return None
        if not mensajes and obj.fila.extra.get("tipo") in ("convo", "mensaje"):
            self.avisar(_("Esto es un mensaje directo, no una publicación"))
            return None
        return obj

    def _ws_en_dm(self) -> bool:
        n = self.nodo_actual
        return n is not None and n.servicio == "wsocial" and (n.timeline == "dms" or str(n.timeline).startswith("dm:"))

    def _ws_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "wsocial" and n.account_id:
            return n.account_id
        cuentas = datos_wsocial.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de W Social"))
        return None

    def _ws_post_db_id(self, obj):
        """Id local de la publicación de la fila (en una notificación, la publicación a la que se refiere)."""
        x = obj.fila.extra
        if x.get("tipo") == "notificacion":
            return f"{x['account_id']}:{x['uri']}" if x.get("uri") else None
        return obj.fila.id

    def _ws_nodos_cuenta(self, account_id):
        for raiz in getattr(self, "arbol", []):
            if raiz.servicio == "wsocial":
                return [h for c in raiz.hijos if c.account_id == account_id for h in c.hijos]
        return []

    # ------------------------------------------------------------------ actualizar y cargar más
    def ws_actualizar_nodo(self, nodo, silencioso: bool = False):
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
                wsocial_service.marcar_notificaciones_vistas_async(nodo.account_id, callback=lambda r, e: None)
            if primera or not nuevos or nodo.timeline == "dms" or nodo.timeline.startswith("dm:"):
                if not silencioso and nodo is self.nodo_actual:
                    self.estado.set_text(_("{t}: actualizado").format(t=nodo.titulo))
                return
            if nodo.timeline == "notifications":
                speak(_("{n} notificaciones nuevas").format(n=nuevos), interrupt=False)
                event_bus.emit("sound_trigger", "wsocial_new_notification")
            elif nodo is self.nodo_actual:
                self.avisar(_("{n} nuevas").format(n=nuevos), interrumpir=False)
        if nodo.timeline == "dms":
            wsocial_service.cargar_convos_async(nodo.account_id, callback=adaptar(_fin))
        elif nodo.timeline.startswith("dm:"):
            convo = nodo.timeline[3:]

            def _leido(res, err):
                _fin(res, err)
                if not err:
                    wsocial_service.accion_convo_async(nodo.account_id, convo, "leer", callback=lambda ok, r: None)
            wsocial_service.cargar_mensajes_async(nodo.account_id, convo, callback=adaptar(_leido))
        else:
            wsocial_service.cargar_timeline_async(nodo.account_id, nodo.timeline, callback=adaptar(_fin))

    def ws_cargar_mas(self):
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
            wsocial_service.cargar_convos_async(n.account_id, cargar_mas=True, callback=adaptar(_fin))
        elif n.timeline.startswith("dm:"):
            wsocial_service.cargar_mensajes_async(n.account_id, n.timeline[3:], cargar_mas=True, callback=adaptar(_fin))
        else:
            wsocial_service.cargar_timeline_async(n.account_id, n.timeline, cargar_mas=True, callback=adaptar(_fin))

    def _sondeo_wsocial(self):
        for acc_id, _n in datos_wsocial.cuentas():
            for nodo in self._ws_nodos_cuenta(acc_id):
                if nodo.timeline in ("home", "notifications") and (acc_id, nodo.timeline) in self._cargados:
                    self.ws_actualizar_nodo(nodo, silencioso=True)

    # ------------------------------------------------------------------ acciones sobre una publicación
    def _ws_accion_estado(self, obj, on: str, off: str, propiedad: str, texto_on: str, texto_off: str):
        db_id = self._ws_post_db_id(obj)
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
        wsocial_service.ejecutar_accion_post_async(obj.fila.extra["account_id"], db_id, off if activo else on,
                                                   callback=adaptar(_fin))

    def ws_me_gusta(self):
        obj = self._ws_obj()
        if obj:
            self._ws_accion_estado(obj, "like", "unlike", "favorito", _("Me gusta"), _("Me gusta quitado"))

    def ws_republicar(self):
        obj = self._ws_obj()
        if obj:
            self._ws_accion_estado(obj, "repost", "unrepost", "impulsado", _("Republicado"), _("Republicación quitada"))

    def ws_marcador(self):
        obj = self._ws_obj()
        if not obj:
            return
        db_id = self._ws_post_db_id(obj)
        if not db_id:
            return
        activo = obj.marcador

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            obj.marcador = not activo
            self.avisar(_("Marcador quitado") if activo else _("Añadido a marcadores"))
        wsocial_service.ejecutar_accion_post_async(obj.fila.extra["account_id"], db_id,
                                                   "unbookmark" if activo else "bookmark", callback=adaptar(_fin))

    def ws_nuevo(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "wsocial" and n.timeline.startswith("dm:"):
            return self._ws_escribir_mensaje(n.account_id, n.timeline[3:], n.titulo)
        if n is not None and n.servicio == "wsocial" and n.timeline == "dms":
            return self.ws_mensaje_nuevo()
        acc = self._ws_cuenta()
        if acc is not None:
            from .dialogos_wsocial import DialogoPublicarWSocial
            DialogoPublicarWSocial(self, acc).present()

    def ws_responder(self):
        obj = self._ws_obj(mensajes=True)
        x = obj.fila.extra if obj else {}
        if x.get("tipo") in ("convo", "mensaje"):
            return self._ws_escribir_mensaje(x["account_id"], x["convo_id"], x.get("titulo") or obj.fila.feed_titulo)
        if not obj or not x.get("uri"):
            return
        from .dialogos_wsocial import DialogoPublicarWSocial
        DialogoPublicarWSocial(self, x["account_id"], responde_a=x["uri"]).present()

    def ws_citar(self):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri"):
            return
        from .dialogos_wsocial import DialogoPublicarWSocial
        DialogoPublicarWSocial(self, x["account_id"], cita_uri=x["uri"]).present()

    def ws_borrar(self):
        obj = self._ws_obj(mensajes=True)
        if not obj:
            return
        x = obj.fila.extra
        if x.get("tipo") in ("convo", "mensaje"):
            return self._ws_borrar_mensaje(obj)
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
            wsocial_service.borrar_post_async(x["account_id"], x["uri"], callback=adaptar(_fin))
        confirmar_async(self, _("¿Borrar esta publicación? No se puede deshacer."), _hacer, etiqueta_si=_("Borrar"))

    # ------------------------------------------------------------------ hilos, cuentas y búsquedas
    def ws_conversacion(self):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri"):
            return
        self._abrir_buffer(x["account_id"], f"thread:{x['uri']}", _("Conversación de {n}").format(n=obj.fila.autor),
                           servicio="wsocial")

    def ws_abrir_cronologia(self, account_id: int, did: str, handle: str):
        self._abrir_buffer(account_id, f"author:{did}", _("Publicaciones de @{u}").format(u=handle), servicio="wsocial")

    def ws_cronologia_usuario(self):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("usuario"):
            return
        did = x.get("did") or x["usuario"]
        self.ws_abrir_cronologia(x["account_id"], did, x["usuario"])

    def ws_perfil(self):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("usuario"):
            return
        from .dialogos_wsocial import DialogoPerfilWSocial
        DialogoPerfilWSocial(self, x["account_id"], x.get("did") or x["usuario"]).present()

    def ws_buscar(self):
        acc = self._ws_cuenta()
        if acc is None:
            return
        from .dialogos import DialogoTexto

        def _buscar(texto):
            self._abrir_buffer(acc, f"search:{texto}", _("Búsqueda: {t}").format(t=texto), servicio="wsocial")
        DialogoTexto(self, _("Buscar en W Social"), _("_Buscar:"), _buscar,
                     _("Busca publicaciones. Puedes usar #hashtags y from:usuario."), _("_Buscar")).present()

    def ws_abrir_feed(self):
        self._ws_elegir("feed")

    def ws_abrir_lista(self):
        self._ws_elegir("list")

    def _ws_elegir(self, tipo: str):
        acc = self._ws_cuenta()
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
                                   servicio="wsocial")
            DialogoElegir(self, _("Abrir un feed") if tipo == "feed" else _("Abrir una lista"),
                          _("_Feed:") if tipo == "feed" else _("_Lista:"), [it["name"] for it in items], _elegido,
                          _("_Abrir")).present()
        (wsocial_service.cargar_feeds_async if tipo == "feed" else wsocial_service.cargar_listas_async)(
            acc, callback=adaptar(_fin))

    def ws_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "wsocial" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de W Social"))
            return
        from .dialogos import confirmar_async
        acc = n.account_id
        nombre = next((nm for i, nm in datos_wsocial.cuentas() if i == acc), "")

        def _hacer():
            wsocial_service.eliminar_cuenta(acc)
            datos_wsocial.EXTRA.pop(acc, None)
            self.cargar_arbol(conservar=False)
            self.iniciar_tiempo_real_wsocial()
            self.avisar(_("Cuenta eliminada"))
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ mensajes directos
    def ws_abrir_convo(self, obj):
        x = obj.fila.extra
        self._abrir_buffer(x["account_id"], f"dm:{x['convo_id']}", x.get("titulo") or obj.fila.autor, servicio="wsocial")

    def _ws_escribir_mensaje(self, account_id: int, convo_id: str, titulo: str):
        from .dialogos_wsocial_extra import DialogoMensajeWSocial

        def _enviado():
            clave = (account_id, f"dm:{convo_id}")
            for n in self._ws_nodos_cuenta(account_id):
                if n.timeline == clave[1]:
                    self.ws_actualizar_nodo(n, silencioso=True)
        DialogoMensajeWSocial(self, account_id, convo_id, titulo, _enviado).present()

    def ws_mensaje_nuevo(self, account_id=None, actor: str = None):
        """Abre (o crea) la conversación con una cuenta. Sin `actor`, pregunta por el usuario."""
        acc = account_id if account_id is not None else self._ws_cuenta()
        if acc is None:
            return

        def _con(did):
            self.avisar(_("Abriendo la conversación…"))

            def _fin(res, err):
                if err or not res:
                    self.avisar(_("No se pudo abrir la conversación: {e}").format(e=err))
                    return
                self._abrir_buffer(acc, f"dm:{res['convo_id']}", res["title"], servicio="wsocial")
            wsocial_service.abrir_convo_con_async(acc, did, callback=adaptar(_fin))

        def _resolver(texto):
            if texto.startswith("did:"):
                return _con(texto)

            def _perfil(d, err):
                if err or not d:
                    self.avisar(_("No se encontró esa cuenta"))
                    return
                _con(d["did"])
            wsocial_service.obtener_perfil_async(acc, texto.lstrip("@"), callback=adaptar(_perfil))
        if actor:
            return _resolver(actor)
        from .dialogos import DialogoTexto
        DialogoTexto(self, _("Mensaje directo"), _("_Usuario (por ejemplo nombre.wsocial.eu):"), _resolver,
                     aceptar=_("_Abrir conversación")).present()

    def ws_mensaje_a_autor(self):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if obj and x.get("usuario"):
            self.ws_mensaje_nuevo(x["account_id"], x.get("did") or x["usuario"])

    def _ws_borrar_mensaje(self, obj):
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
                wsocial_service.accion_convo_async(x["account_id"], x["convo_id"], "salir", callback=adaptar(_fin))
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
            wsocial_service.borrar_mensaje_async(x["account_id"], x["convo_id"], x["message_id"],
                                                   callback=adaptar(_fin))
        confirmar_async(self, _("¿Borrar este mensaje solo para ti? La otra persona lo seguirá viendo."), _borrar,
                        etiqueta_si=_("Borrar"))

    def ws_convo_silenciar(self):
        obj = self._ws_obj(mensajes=True)
        x = obj.fila.extra if obj else {}
        if not obj or x.get("tipo") not in ("convo", "mensaje"):
            self.avisar(_("Selecciona una conversación"))
            return
        nodo_convo = self.nodo_actual.timeline.startswith("dm:")
        silenciar = True
        fila = next((f for f in datos_wsocial._listar_convos(x["account_id"], 500) if f.extra["convo_id"] == x["convo_id"]), None)
        if fila is not None:
            silenciar = not fila.extra["silenciada"]

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.avisar(_("Conversación silenciada") if silenciar else _("Conversación reactivada"))
            if not nodo_convo:
                self.cargar_lista(conservar_id=obj.fila.id, incremental=True)
        wsocial_service.accion_convo_async(x["account_id"], x["convo_id"], "silenciar" if silenciar else "no_silenciar",
                                           callback=adaptar(_fin))

    def ws_reaccionar(self):
        obj = self._ws_obj(mensajes=True)
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
                for n in self._ws_nodos_cuenta(x["account_id"]):
                    if n.timeline == f"dm:{x['convo_id']}":
                        self.ws_actualizar_nodo(n, silencioso=True)
            wsocial_service.reaccionar_mensaje_async(x["account_id"], x["convo_id"], x["message_id"], emoji[:2],
                                                     callback=adaptar(_fin))
        DialogoTexto(self, _("Reaccionar al mensaje"), _("_Emoji:"), _hacer, aceptar=_("_Reaccionar")).present()

    # ------------------------------------------------------------------ interacciones, informes, ocultar
    def ws_interacciones(self, tipo: str):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri"):
            return
        if tipo == "quotes":
            return self._abrir_buffer(x["account_id"], f"quotes:{x['uri']}", _("Citas de {n}").format(n=obj.fila.autor),
                                      servicio="wsocial")
        from .dialogos_wsocial_extra import DialogoActoresWSocial
        DialogoActoresWSocial(self, x["account_id"], tipo, uri=x["uri"]).present()

    def _ws_raiz(self, account_id: int, uri: str) -> str:
        """URI de la publicación raíz del hilo (la guarda la caché; si no la hay, la propia publicación)."""
        from app.core.database import db
        fila = db.query_one("SELECT COALESCE(reply_root_uri, '') FROM wsocial_posts WHERE account_id = ? AND uri = ?",
                            (account_id, uri))
        return (fila[0] if fila and fila[0] else uri)

    def ws_silenciar_hilo(self, silenciar: bool = True):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("uri"):
            return
        raiz = self._ws_raiz(x["account_id"], x["uri"])

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.avisar(_("Hilo silenciado: no recibirás sus notificaciones") if silenciar
                        else _("Hilo reactivado"))
        wsocial_service.silenciar_hilo_async(x["account_id"], raiz, silenciar, callback=adaptar(_fin))

    def ws_reportar(self, cuenta: bool = False):
        obj = self._ws_obj()
        x = obj.fila.extra if obj else {}
        if not obj:
            return
        from .dialogos_wsocial_extra import DialogoReportarWSocial
        if cuenta:
            if x.get("did") or x.get("usuario"):
                DialogoReportarWSocial(self, x["account_id"], did=x.get("did") or "",
                                       quien="@" + x.get("usuario", "")).present()
            return
        db_id = self._ws_post_db_id(obj)
        if not db_id:
            return
        from app.core.database import db
        fila = db.query_one("SELECT uri, cid FROM wsocial_posts WHERE id = ?", (db_id,))
        if not fila:
            self.avisar(_("Esa publicación no está en la caché: ábrela primero"))
            return
        DialogoReportarWSocial(self, x["account_id"], uri=fila[0], cid=fila[1], quien=_("la publicación")).present()

    # ------------------------------------------------------------------ mi cuenta
    def _ws_dialogo(self, nombre: str, *args):
        acc = self._ws_cuenta()
        if acc is not None:
            from . import dialogos_wsocial_extra as extra
            getattr(extra, nombre)(self, acc, *args).present()

    def ws_actores(self, tipo: str):
        acc = self._ws_cuenta()
        if acc is not None:
            from .dialogos_wsocial_extra import DialogoActoresWSocial
            DialogoActoresWSocial(self, acc, tipo).present()

    def ws_reautorizar(self):
        acc = self._ws_cuenta()
        if acc is None:
            return
        from .dialogos_wsocial import DialogoCuentaWSocial
        DialogoCuentaWSocial(self, lambda _id: self.avisar(_("Cuenta autorizada de nuevo")), account_id=acc).present()

    def _registrar_acciones_wsocial(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("ws-mensaje-nuevo", self.ws_mensaje_nuevo)
        a("ws-mensaje-autor", self.ws_mensaje_a_autor)
        a("ws-convo-silenciar", self.ws_convo_silenciar)
        a("ws-reaccionar", self.ws_reaccionar)
        a("ws-quien-gusta", lambda: self.ws_interacciones("likes"))
        a("ws-quien-republico", lambda: self.ws_interacciones("reposts"))
        a("ws-quien-cito", lambda: self.ws_interacciones("quotes"))
        a("ws-silenciar-hilo", lambda: self.ws_silenciar_hilo(True))
        a("ws-reactivar-hilo", lambda: self.ws_silenciar_hilo(False))
        a("ws-reportar", self.ws_reportar)
        a("ws-reportar-cuenta", lambda: self.ws_reportar(True))
        a("ws-seguidores", lambda: self.ws_actores("followers"))
        a("ws-siguiendo", lambda: self.ws_actores("follows"))
        a("ws-bloqueadas", lambda: self.ws_actores("blocks"))
        a("ws-silenciadas", lambda: self.ws_actores("mutes"))
        a("ws-listas", lambda: self._ws_dialogo("DialogoListasWSocial"))
        a("ws-claves", lambda: self._ws_dialogo("DialogoClavesAppWSocial"))
        a("ws-mi-perfil", lambda: self._ws_dialogo("DialogoMiPerfilWSocial"))
        a("ws-reautorizar", self.ws_reautorizar)
        a("ws-eliminar-cuenta", self.ws_eliminar_cuenta)

    # ------------------------------------------------------------------ novedades en directo
    def iniciar_tiempo_real_wsocial(self):
        """Arranca (o detiene) la vigilancia de novedades de cada cuenta según la preferencia: el servicio
        pregunta cada cierto tiempo por el contador de notificaciones y por el registro del chat."""
        cuentas = [a for a, _n in datos_wsocial.cuentas()]
        vigiladas = getattr(self, "_ws_vigiladas", set())
        if not config.get("gtk", "wsocial_realtime", True):
            wsocial_service.detener_tiempo_real()
            self._ws_vigiladas = set()
            return
        for acc in vigiladas - set(cuentas):
            wsocial_service.detener_tiempo_real(acc)
        intervalo = int(config.get("wsocial", "intervalo_tiempo_real", 60) or 60)
        if intervalo != getattr(self, "_ws_intervalo", intervalo):
            wsocial_service.detener_tiempo_real()
            vigiladas = set()
        self._ws_intervalo = intervalo
        for acc in cuentas:
            if acc in vigiladas:
                continue
            wsocial_service.iniciar_tiempo_real(
                acc, al_haber_novedades=lambda n, a=acc: llamar_en_ui(self._ws_novedad, a, n),
                al_llegar_mensaje=lambda ev, a=acc: llamar_en_ui(self._ws_mensaje, a, ev), intervalo=intervalo)
        self._ws_vigiladas = set(cuentas)

    def _ws_novedad(self, account_id: int, cuantas: int):
        """Hay notificaciones sin leer nuevas: se actualizan el Inicio y las notificaciones abiertos."""
        speak(_("Notificaciones nuevas en W Social"), interrupt=False)
        event_bus.emit("sound_trigger", "wsocial_new_notification")
        for n in self._ws_nodos_cuenta(account_id):
            if n.timeline in ("home", "notifications", "mentions") and (account_id, n.timeline) in self._cargados:
                self.ws_actualizar_nodo(n, silencioso=True)

    def _ws_mensaje(self, account_id: int, evento: dict):
        """Ha llegado un mensaje directo."""
        texto = (evento.get("texto") or "").strip()[:120]
        speak(_("Mensaje directo en W Social"), interrupt=False)
        event_bus.emit("sound_trigger", "wsocial_new_dm")
        mostrar_toast(texto or _("Mensaje directo"), _("Mensaje directo en W Social"))
        for n in self._ws_nodos_cuenta(account_id):
            if n.timeline == "dms" or n.timeline == f"dm:{evento.get('convo_id')}":
                if (account_id, n.timeline) in self._cargados:
                    self.ws_actualizar_nodo(n, silencioso=True)
