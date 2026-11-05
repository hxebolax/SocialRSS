# -*- coding: utf-8 -*-
"""Menú Herramientas de la ventana principal (mixin de VentanaPrincipal): herramientas generales que no dependen de
una red concreta."""

import logging

log = logging.getLogger(__name__)


class _HerramientasMixin:
    def _registrar_acciones_herramientas(self, accion):
        accion("herr-filtros", self.herr_filtros)
        accion("herr-fragmentos", self.herr_fragmentos)
        accion("herr-copias", self.herr_copias)
        accion("herr-migrar", self.herr_migrar)
        accion("herr-borrar", self.herr_borrar)
        accion("herr-registro", self.herr_registro)
        accion("ayuda-manual", self.ayuda_manual)
        accion("ayuda-teclas-documento", self.ayuda_teclas_documento)
        accion("ayuda-changelog", self.ayuda_changelog)
        accion("ayuda-sonidos", self.ayuda_sonidos)
        accion("ayuda-actualizaciones", self.ayuda_actualizaciones)
        accion("ayuda-cafe", self.ayuda_cafe)
        accion("rss-versiones", self.rss_versiones)
        accion("herr-complementos", self.herr_complementos)
        accion("herr-atajos", self.herr_atajos)
        accion("herr-papelera", self.herr_papelera)
        accion("herr-plantillas", self.herr_plantillas)
        accion("herr-buffers", self.herr_buffers)
        accion("herr-radio", self.herr_radio)
        accion("herr-reproductor", self.herr_reproductor)
        accion("herr-ver-descripcion", self.herr_ver_descripcion, ["<Ctrl><Alt>k"])
        accion("herr-cola", self.herr_cola)
        accion("herr-estadisticas", self.herr_estadisticas)
        accion("herr-ponte-al-dia", self.herr_ponte_al_dia, ["<Ctrl><Shift>y"])
        accion("herr-buscar-global", self.herr_buscar_global, ["<Ctrl><Shift>s"])
        accion("herr-publicacion-cruzada", self.herr_publicacion_cruzada, ["<Ctrl><Shift>x"])
        accion("herr-centro-notificaciones", self.herr_centro_notificaciones, ["<Ctrl><Shift>a"])

    def herr_filtros(self):
        from .dialogos_herramientas import DialogoFiltrosContenido
        DialogoFiltrosContenido(self).present()

    def herr_fragmentos(self):
        from .dialogos_herramientas import DialogoFragmentos
        DialogoFragmentos(self).present()

    def herr_copias(self):
        from .dialogos_herramientas import DialogoCopias
        DialogoCopias(self, self.get_application()).present()

    def herr_migrar(self):
        from .dialogos_herramientas import DialogoMigracion
        DialogoMigracion(self, self.get_application()).present()

    def herr_borrar(self):
        from .dialogos_herramientas import DialogoBorrado
        DialogoBorrado(self).present()

    def herr_publicacion_cruzada(self, texto: str = ""):
        from .dialogos_herramientas import DialogoPublicacionCruzada
        DialogoPublicacionCruzada(self, texto).present()

    def herr_cola(self):
        from .dialogos_herramientas import DialogoColaUnificada
        DialogoColaUnificada(self).present()

    def herr_buffers(self):
        from .dialogos_herramientas import DialogoBuffers
        DialogoBuffers(self).present()

    def herr_plantillas(self):
        from .dialogos_herramientas import DialogoPlantillas
        DialogoPlantillas(self).present()

    def herr_papelera(self):
        from .dialogos_herramientas import DialogoPapelera
        DialogoPapelera(self, self.get_application().account_id).present()

    def herr_atajos(self):
        from .dialogos_atajos import DialogoAtajos
        DialogoAtajos(self).present()

    def herr_complementos(self):
        from .plugins_ui import DialogoComplementos
        DialogoComplementos(self).present()

    def rss_versiones(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "rss":
            self.avisar(_("Selecciona un artículo de RSS"))
            return
        from .dialogos_herramientas import DialogoVersionesArticulo
        DialogoVersionesArticulo(self, obj.fila.id).present()

    def herr_registro(self):
        from .dialogos_herramientas import DialogoRegistro
        DialogoRegistro(self).present()

    # ------------------------------------------------------------------ centro de notificaciones
    def herr_centro_notificaciones(self):
        from .dialogos_herramientas import DialogoCentroNotificaciones
        DialogoCentroNotificaciones(self, self._abrir_notificacion).present()

    def _ir_a_buffer(self, servicio: str, account_id: int, timeline: str) -> bool:
        """Selecciona en el árbol el buffer fijo de una cuenta."""
        for i in range(self.modelo_arbol.get_n_items()):
            n = self.modelo_arbol.get_row(i).get_item().nodo
            if n.servicio == servicio and n.account_id == account_id and n.timeline == timeline:
                self.seleccion_arbol.set_selected(i)
                self.enfocar_panel(self.vista_lista)
                return True
        return False

    def _abrir_notificacion(self, destino):
        """Lleva a lo que señala una notificación del centro: el hilo, el chat o el buffer de su red."""
        red = destino[0]
        try:
            if red == "mastodon" and destino[2]:
                self._abrir_buffer(destino[1], f"thread:{destino[2]}", _("Conversación"), servicio="mastodon")
            elif red == "bluesky" and destino[2]:
                self._abrir_buffer(destino[1], f"thread:{destino[2]}", _("Conversación"), servicio="bluesky")
            elif red == "x" and destino[2]:
                self._abrir_buffer(destino[1], f"thread:{destino[2]}", _("Conversación"), servicio="x")
            elif red == "telegram":
                self._tg_abrir_por_id(destino[1], destino[2], destino[3] or "")
            elif red == "matrix":
                self.mx_abrir_sala(destino[1], destino[2], destino[3] or "")
            elif red == "wsocial_convo":
                self._abrir_buffer(destino[1], f"dm:{destino[2]}", destino[3] or _("Mensajes directos"), servicio="wsocial")
            elif red == "wsocial":
                if not self._ir_a_buffer("wsocial", destino[1], "notifications"):
                    self.avisar(_("No se encontró el buffer de notificaciones de W Social"))
            elif red == "irc":
                self._irc_abrir_por_nombre(destino[1], destino[2])
            elif red == "xmpp":
                self._x_abrir_por_jid(destino[1], destino[2], destino[3])
            else:
                self.avisar(_("Ve al buffer de {0} para ver el detalle.").format(red))
        except Exception as e:
            log.warning("No se pudo abrir la notificación %s: %s", destino, e)
            self.avisar(_("No se pudo abrir: {0}").format(e))

    # ------------------------------------------------------------------ búsqueda global
    def herr_buscar_global(self, consulta: str = "", solo_rss: bool = False):
        from .dialogos_herramientas import DialogoBusquedaGlobal
        DialogoBusquedaGlobal(self, self._abrir_resultado_busqueda, consulta, solo_rss).present()

    def _abrir_resultado_busqueda(self, r: dict):
        """Abre un resultado de la búsqueda global: el hilo de la publicación, el chat o el artículo."""
        from app.core.database import db
        srv, acc = r["service"], r.get("account_id")
        try:
            if srv == "mastodon" and r.get("status_id"):
                self._abrir_buffer(acc, f"thread:{r['status_id']}", _("Conversación"), servicio="mastodon")
            elif srv in ("bluesky", "wsocial") and r.get("uri"):
                self._abrir_buffer(acc, f"thread:{r['uri']}", _("Conversación"), servicio=srv)
            elif srv == "wsocial":
                fila = db.query_one("SELECT uri FROM wsocial_posts WHERE id = ?", (r["db_id"],))
                if fila:
                    self._abrir_buffer(acc, f"thread:{fila[0]}", _("Conversación"), servicio="wsocial")
            elif srv == "x" and r.get("tweet_id"):
                self._abrir_buffer(acc, f"thread:{r['tweet_id']}", _("Conversación"), servicio="x")
            elif srv == "telegram" and r.get("chat_id") is not None:
                self._tg_abrir_por_id(acc, r["chat_id"])
            elif srv == "matrix":
                self.mx_abrir_sala(acc, r["room_id"])
            elif srv == "xmpp":
                self._x_abrir_por_jid(acc, r["chat_jid"])
            elif srv == "irc":
                self._irc_abrir_por_nombre(acc, r["channel"])
            elif srv == "rss":
                from app.services.rss.rss_service import rss_service
                from app.core.html_utils import html_a_texto
                from .dialogos_mastodon_extra import DialogoTextoLargo
                art = rss_service.obtener_articulo(r["db_id"])
                if art is None:
                    self.avisar(_("No se encontró el artículo"))
                    return
                DialogoTextoLargo(self, art.title, f"{art.title}\n\n{html_a_texto(art.content or '')}\n\n{art.link or ''}").present()
        except Exception as e:
            log.warning("No se pudo abrir el resultado %s: %s", r, e)
            self.avisar(_("No se pudo abrir: {0}").format(e))

    # ------------------------------------------------------------------ estadísticas y «ponte al día»
    def herr_estadisticas(self):
        from app.core.herramientas_extra import calcular_estadisticas, texto_estadisticas
        from app.core.task_manager import task_manager
        from .dialogos_mastodon_extra import DialogoTextoLargo
        self.avisar(_("Calculando estadísticas…"))

        def _fin(d, err):
            if err or not d:
                self.avisar(_("No se pudieron calcular las estadísticas: {0}").format(err or ""))
                return
            DialogoTextoLargo(self, _("Estadísticas de mis redes"), texto_estadisticas(d)).present()
        task_manager.start_task(lambda ctx: calcular_estadisticas(), callback=_fin, pass_context=True)

    def herr_ponte_al_dia(self):
        """Resume con la IA lo que no has leído (RSS sin leer y menciones recientes). Solo la llamada a la IA usa la red."""
        from app.core.event_bus import event_bus
        from app.core.herramientas_extra import texto_pendiente
        from app.core.task_manager import task_manager
        from .acciones_ia import ia_activa
        from .dialogos_herramientas import markdown_a_texto
        from .dialogos_mastodon_extra import DialogoTextoLargo
        if not ia_activa():
            return
        self.avisar(_("Reuniendo lo que no has leído…"))

        def _bg(ctx):
            from app.services.ai.ai_service import ai_service
            peticion = texto_pendiente()
            return ai_service.resumir_texto(peticion) if peticion else None

        def _fin(res, err):
            if err:
                event_bus.emit("sound_trigger", "ai_error")
                self.avisar(_("No se pudo generar el resumen: {0}").format(err))
                return
            if not res:
                self.avisar(_("No tienes nada pendiente de leer ahora mismo."))
                return
            event_bus.emit("sound_trigger", "ai_done")
            self.avisar(_("Resumen listo"))
            DialogoTextoLargo(self, _("Ponte al día"), markdown_a_texto(res)).present()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------ radio y reproductor
    def herr_radio(self):
        from .dialogos_radio import DialogoRadio
        DialogoRadio(self).present()

    def herr_reproductor(self):
        from .dialogos_reproductor import DialogoReproductor2
        DialogoReproductor2.mostrar(self)

    # ------------------------------------------------------------------ descripción de la multimedia
    def herr_ver_descripcion(self):
        """Muestra el texto alternativo que el autor puso a las imágenes y vídeos de la publicación enfocada."""
        import json
        from app.core.database import db
        from .dialogos_mastodon_extra import DialogoTextoLargo
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio not in ("mastodon", "bluesky", "wsocial", "x"):
            self.avisar(_("Enfoca una publicación de Mastodon, Bluesky, W Social o X para ver su descripción."))
            return
        f = obj.fila
        tabla = {"mastodon": "mastodon_statuses", "bluesky": "bluesky_posts", "wsocial": "wsocial_posts",
                 "x": "x_tweets"}[f.servicio]
        fila = db.query_one(f"SELECT COALESCE(media_json, '') FROM {tabla} WHERE id = ?",
                            (f.extra.get("estado_db_id") or f.id,))
        descripciones = []
        if fila and fila[0]:
            try:
                for a in json.loads(fila[0]) or []:
                    if isinstance(a, dict):
                        d = (a.get("description") or a.get("alt") or "").strip()
                        if d:
                            descripciones.append(d)
            except (TypeError, ValueError):
                pass
        if not descripciones:
            self.avisar(_("Esta publicación no tiene descripción en su multimedia."))
            return
        texto = descripciones[0] if len(descripciones) == 1 else "\n\n".join(
            _("Imagen {0}: {1}").format(i + 1, d) for i, d in enumerate(descripciones))
        DialogoTextoLargo(self, _("Descripción de la multimedia"), texto).present()

    # ------------------------------------------------------------------ ayuda
    def ayuda_manual(self):
        from app.core.documentos import abrir_documento_md
        if abrir_documento_md("manual", "MANUAL", _("Manual de usuario - SocialRSS"), "manual.html",
                              _("No se pudo abrir el manual.")):
            self.avisar(_("Abriendo en el navegador."))

    def ayuda_teclas_documento(self):
        from app.core.documentos import abrir_documento_md
        if abrir_documento_md("teclas", "TECLAS", _("Teclas de la aplicación - SocialRSS"), "teclas.html",
                              _("No se pudo abrir el documento de teclas.")):
            self.avisar(_("Abriendo en el navegador."))

    def ayuda_changelog(self):
        import os
        from app.core.documentos import RAIZ
        from app.core.paths import get_data_path
        from .dialogos_mastodon_extra import DialogoTextoLargo
        for ruta in (str(get_data_path("docs/changelog.txt")), os.path.join(RAIZ, "changelog.txt"),
                     os.path.join(os.getcwd(), "changelog.txt")):
            try:
                if os.path.exists(ruta):
                    with open(ruta, "r", encoding="utf-8") as fh:
                        texto = fh.read().strip()
                    if texto:
                        DialogoTextoLargo(self, _("Novedades de esta versión"), texto).present()
                        return
            except OSError as e:
                log.debug("No se pudo leer el changelog '%s': %s", ruta, e)
        self.avisar(_("No se pudo abrir el changelog."))

    def ayuda_sonidos(self):
        from .dialogos_herramientas import DialogoPruebaSonidos
        DialogoPruebaSonidos(self).present()

    def ayuda_cafe(self):
        from app.core.privacy import abrir_url
        abrir_url("https://www.paypal.com/paypalme/hjbcdonaciones?country.x=ES&locale.x=es_ES")
        self.avisar(_("Gracias. Se ha abierto la página de donaciones en el navegador."))

    def ayuda_actualizaciones(self):
        """Comprueba si hay una versión nueva. En Linux no se instala sola: se ofrece abrir la página de descargas."""
        from app.core.task_manager import task_manager
        from app.services.updater import update_service as upd
        from .dialogos import confirmar_async
        self.avisar(_("Comprobando actualizaciones…"))

        def _fin(remota, err):
            if err or not remota:
                self.avisar(_("No se pudo comprobar si hay actualizaciones: {0}").format(err or _("revisa tu conexión")))
            elif upd.hay_actualizacion(remota):
                def _abrir():
                    from app.core.privacy import abrir_url
                    abrir_url("https://github.com/hxebolax/SocialRSS/releases")
                confirmar_async(self, _("Hay una versión nueva: {r} (tienes la {l}). ¿Abrir la página de descargas?").format(
                    r=remota, l=upd.version_local()), _abrir, titulo=_("Actualización disponible"),
                    etiqueta_si=_("Abrir la página"))
            else:
                self.avisar(_("Ya tienes la última versión ({0}).").format(upd.version_local()))
        task_manager.start_task(lambda ctx: upd.obtener_version_remota(), callback=_fin, pass_context=True)
