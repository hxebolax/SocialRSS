# -*- coding: utf-8 -*-
"""Bluesky y W Social: tendencias, paquetes de inicio, grupos de chat, buscar cuentas y actividad (mixin de VentanaPrincipal).

Las acciones llevan el prefijo `at-` y valen para las dos redes: usan la del elemento enfocado."""

from app.services.bluesky.bluesky_service import bluesky_service

from .dialogos import DialogoTexto


class _ATMixin:
    def _at_red(self):
        """(RedAT, cuenta) de la red del elemento enfocado, o None."""
        from . import dialogos_at_extra as at
        if self._ws_ctx():
            acc = self._ws_cuenta()
            return (at.WS, acc) if acc is not None else None
        if self._bsky_ctx():
            acc = self._bsky_cuenta()
            return (at.BSKY, acc) if acc is not None else None
        self.avisar(_("Selecciona un elemento de Bluesky o de W Social"))
        return None

    def at_dialogo(self, nombre: str):
        r = self._at_red()
        if r:
            from . import dialogos_at_extra as at
            getattr(at, nombre)(self, r[0], r[1]).present()

    def at_funcion(self, nombre: str):
        r = self._at_red()
        if r:
            from . import dialogos_at_extra as at
            getattr(at, nombre)(self, r[0], r[1])

    def at_buscar_cuentas(self):
        r = self._at_red()
        if not r:
            return
        red, acc = r

        def _buscar(texto):
            from .dialogos_at_extra import DialogoBuscarCuentasAT
            DialogoBuscarCuentasAT(self, red, acc, texto).present()
        DialogoTexto(self, _("Buscar cuentas"), _("_Buscar:"), _buscar, _("Un nombre, un usuario o parte de la biografía."),
                     _("_Buscar")).present()

    def _at_convo(self):
        """(id, título) del grupo o conversación enfocado: una fila de la lista de chats o el buffer abierto de un grupo."""
        obj = self._fila_actual()
        if obj is not None and obj.fila.extra.get("tipo") == "convo":
            return obj.fila.extra["convo_id"], obj.fila.extra.get("titulo") or obj.fila.autor
        n = self.nodo_actual
        if n is not None and n.timeline.startswith("dm:"):
            return n.timeline[3:], n.titulo
        self.avisar(_("Selecciona una conversación de grupo"))
        return None

    def at_grupo_gestionar(self):
        r, c = self._at_red(), self._at_convo()
        if r and c:
            from .dialogos_at_extra import DialogoGrupoAT
            DialogoGrupoAT(self, r[0], r[1], c[0], c[1]).present()

    def at_solicitudes_mensajes(self):
        if self._ws_ctx():
            return self.avisar(_("Esto solo está en Bluesky"))
        acc = self._bsky_cuenta()
        if acc is not None:
            from .dialogos_at_extra import DialogoSolicitudesMensajesBsky
            DialogoSolicitudesMensajesBsky(self, acc).present()

    def at_actividad(self):
        if self._ws_ctx():
            return self.avisar(_("Esto solo está en Bluesky"))
        acc = self._bsky_cuenta()
        if acc is not None:
            from .dialogos_at_extra import DialogoActividadBsky
            DialogoActividadBsky(self, acc).present()

    def _at_autor_bsky(self):
        """(cuenta, did) del autor de la publicación enfocada de Bluesky."""
        if self._ws_ctx():
            self.avisar(_("Esto solo está en Bluesky"))
            return None
        obj = self._bsky_obj()
        if not obj or not obj.fila.extra.get("did"):
            return None
        return obj.fila.extra["account_id"], obj.fila.extra["did"]

    def at_autor(self, que: str):
        a = self._at_autor_bsky()
        if not a:
            return
        acc, did = a
        from . import dialogos_at_extra as at
        if que == "similares":
            bluesky_service.sugerencias_seguir_actor_async(acc, did, callback=lambda r, e: self.avisar(
                _("Error: {e}").format(e=e)) if e else at.lista_de_cuentas(self, _("Cuentas similares"), r or [],
                                                                            _("No hay sugerencias.")))
        elif que == "conocidos":
            bluesky_service.seguidores_conocidos_async(acc, did, callback=lambda r, e: self.avisar(
                _("Error: {e}").format(e=e)) if e else at.lista_de_cuentas(self, _("Seguidores en común"), r or [],
                                                                            _("No hay seguidores en común.")))
        elif que == "feeds":
            def _fin(res, err):
                if err:
                    return self.avisar(_("Error: {e}").format(e=err))
                if not res:
                    return self.avisar(_("Esta cuenta no ha creado feeds."))
                from .dialogos_peertube_instancia import texto_en_dialogo
                texto_en_dialogo(self, _("Feeds de la cuenta"), "\n".join(f"{f['name']}: {f.get('description', '')}" for f in res))
            bluesky_service.feeds_de_actor_async(acc, did, callback=_fin)
        elif que == "suscribir":
            bluesky_service.suscribir_actividad_async(acc, did, posts=True, callback=lambda r, e: self.avisar(
                _("No se pudo suscribir: {e}").format(e=e) if e else _("Te avisaré cuando esta cuenta publique.")))

    def at_cuenta(self, nombre: str, solo: str = ""):
        """Abre una pantalla de dialogos_at_cuenta; `solo` limita la acción a una red («bluesky» o «wsocial»)."""
        if solo == "bluesky" and self._ws_ctx():
            return self.avisar(_("Esto solo está en Bluesky"))
        if solo == "wsocial" and not self._ws_ctx():
            return self.avisar(_("Esto solo está en W Social"))
        r = self._at_red()
        if r:
            from . import dialogos_at_cuenta as c
            getattr(c, nombre)(self, r[0], r[1])

    def at_wsocial_dialogo(self, nombre: str):
        acc = self._ws_cuenta()
        if acc is not None:
            from . import dialogos_wsocial_moderacion as m
            getattr(m, nombre)(self, acc).present()

    def _registrar_acciones_at(self, accion):
        accion("at-salida", lambda: self.at_dialogo("DialogoSalidaAT"))
        for nombre, clase in (("ws-palabras", "DialogoPalabrasWSocial"), ("ws-contenido", "DialogoContenidoWSocial"),
                              ("ws-etiquetadores", "DialogoEtiquetadoresWSocial"), ("ws-borradores", "DialogoBorradoresWSocial")):
            accion(nombre, lambda c=clase: self.at_wsocial_dialogo(c))
        accion("at-tendencias", lambda: self.at_dialogo("DialogoTendenciasAT"))
        accion("at-paquetes", lambda: self.at_dialogo("DialogoPaquetesAT"))
        accion("at-buscar-cuentas", self.at_buscar_cuentas)
        accion("at-grupo-crear", lambda: self.at_funcion("crear_grupo"))
        accion("at-grupo-entrar", lambda: self.at_funcion("entrar_en_grupo"))
        accion("at-grupo-gestionar", self.at_grupo_gestionar)
        accion("at-solicitudes-mensajes", self.at_solicitudes_mensajes)
        accion("at-actividad", self.at_actividad)
        accion("at-cuentas-similares", lambda: self.at_autor("similares"))
        accion("at-seguidores-conocidos", lambda: self.at_autor("conocidos"))
        accion("at-feeds-de", lambda: self.at_autor("feeds"))
        accion("at-suscribir-actividad", lambda: self.at_autor("suscribir"))
        for nombre, fn, solo in (("at-sugerencias", "sugerencias", ""), ("at-cambiar-usuario", "cambiar_usuario", ""),
                                 ("at-vista", "vista_bsky", "bluesky"), ("at-ocultos", "ocultos_bsky", "bluesky"),
                                 ("at-estado-cuenta", "estado_cuenta", "wsocial"), ("at-servidor", "info_servidor", "wsocial"),
                                 ("at-verificacion-edad", "verificacion_edad", "wsocial"),
                                 ("at-estado-temporal", "estado_temporal", "wsocial"),
                                 ("at-privacidad-chat", "privacidad_chat", "wsocial"),
                                 ("at-exportar-repositorio", "exportar_repositorio", "wsocial"),
                                 ("at-feeds-populares", "feeds_populares", "wsocial"),
                                 ("at-busqueda-filtros", "busqueda_filtros", "wsocial")):
            accion(nombre, lambda f=fn, s=solo: self.at_cuenta(f, s))
