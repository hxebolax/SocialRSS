# -*- coding: utf-8 -*-
"""Reddit en la ventana principal (mixin de VentanaPrincipal).

Las acciones comparten teclas con las demás redes (Ctrl+B voto positivo, Ctrl+R responder, Ctrl+N publicar...): los
manejadores de ventana.py llaman aquí cuando el elemento enfocado es de Reddit. Todo lo de este archivo lleva el prefijo `rd_`.

Reddit permite 100 peticiones por minuto, así que se descarga al entrar en cada buffer y se sondea el abierto cada
`reddit/update_interval` segundos (mínimo 30)."""

import logging
import time
import webbrowser

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.speech import copiar_al_portapapeles
from app.core.task_manager import TareaDuplicadaError
from app.services.reddit import reddit_service

from . import datos_reddit
from .dialogos import DialogoElegir, DialogoTexto, confirmar_async, ejecutar

log = logging.getLogger(__name__)

TIPOS_POST = ("post",)
TIPOS_CON_VOTO = ("post", "comentario")
TIPOS_RESPONDIBLES = ("post", "comentario", "mensaje")


class _RedditMixin:
    # ------------------------------------------------------------------ contexto
    def _rd_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de Reddit."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "reddit"
        n = self.nodo_actual
        return n is not None and n.servicio == "reddit"

    def _rd_obj(self, tipos=TIPOS_POST):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "reddit":
            self.avisar(_("Selecciona un elemento de Reddit"))
            return None
        if obj.fila.extra.get("tipo") not in tipos:
            self.avisar(_("Esta acción no se puede hacer con este elemento de Reddit"))
            return None
        return obj

    def _rd_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "reddit" and n.account_id:
            return n.account_id
        cuentas = datos_reddit.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de Reddit"))
        return None

    def _rd_error(self, err, prefijo: str = None, silencioso: bool = False):
        if isinstance(err, TareaDuplicadaError):
            return
        event_bus.emit("sound_trigger", "reddit_error")
        if not silencioso:
            self.avisar((prefijo or _("Error: {e}")).format(e=err))

    def _rd_hacer(self, fn, ok=None, error=None, despues=None):
        """Ejecuta una operación del servicio en segundo plano, avisa del resultado y repinta la lista."""
        def _fin(res, err):
            if err:
                self._rd_error(err, error or _("No se pudo completar la acción en Reddit: {e}"))
                return
            if despues:
                despues(res)
            if ok:
                self.avisar(ok)
        ejecutar(fn, _fin)

    def _rd_repintar(self, conservar=None):
        obj = self._fila_actual()
        self.cargar_lista(conservar_id=conservar or (obj.fila.id if obj else None), incremental=True)

    # ------------------------------------------------------------------ actualizar y cargar más
    def rd_actualizar_nodo(self, nodo, silencioso: bool = False):
        tl, acc = nodo.timeline, nodo.account_id
        clave = (acc, tl)
        primera = clave not in self._cargados
        antes = {self.almacen_lista.get_item(i).fila.id for i in range(self.almacen_lista.get_n_items())} \
            if nodo is self.nodo_actual else set()

        def _fin(res, err):
            if err:
                self._rd_error(err, _("Error al actualizar Reddit: {e}"), silencioso)
                return
            self._cargados.add(clave)
            if nodo is not self.nodo_actual or self.busqueda:
                return

            def _repintado(filas):
                nuevos = [f for f in filas if f.id not in antes]
                if primera or not nuevos:
                    if not silencioso:
                        self.estado.set_text(_("{t}: actualizado").format(t=nodo.titulo))
                    return
                event_bus.emit("sound_trigger", "reddit_new_message" if datos_reddit.es_bandeja(tl) else "reddit_new_status")
                self.avisar(_("{n} nuevas").format(n=len(nuevos)), interrumpir=False)
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True, despues=_repintado)
        if datos_reddit.es_posts(tl):
            reddit_service.cargar_timeline_async(acc, timeline_type=tl, callback=_fin)
        elif tl == "comments":
            reddit_service.cargar_mis_comentarios_async(acc, callback=_fin)
        elif datos_reddit.es_bandeja(tl):
            reddit_service.cargar_inbox_async(acc, tl, callback=_fin)
        elif tl == "subreddits":
            reddit_service.cargar_suscripciones_async(acc, callback=_fin)
        elif tl == "multireddits":
            reddit_service.cargar_multireddits_async(acc, callback=_fin)
        elif not silencioso:
            self.avisar(_("Este buffer no se descarga: son tus búsquedas guardadas"))

    def rd_cargar_mas(self):
        n = self.nodo_actual
        if n is None or not n.listable or not datos_reddit.es_cronologico(n.timeline):
            self.avisar(_("Aquí no se pueden cargar elementos más antiguos"))
            return
        self.avisar(_("Cargando elementos más antiguos…"))
        actual = self._fila_actual()

        def _fin(res, err):
            if err:
                self._rd_error(err, _("Error al cargar más: {e}"))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            self.avisar(_("{n} cargados").format(n=res) if res else _("No hay más elementos anteriores"))
        if n.timeline == "comments":
            reddit_service.cargar_mis_comentarios_async(n.account_id, cargar_mas=True, callback=_fin)
        else:
            reddit_service.cargar_timeline_async(n.account_id, timeline_type=n.timeline, cargar_mas=True, callback=_fin)

    def _sondeo_reddit(self):
        """Sondeo silencioso del buffer abierto (publicaciones, comentarios propios o bandeja)."""
        n = self.nodo_actual
        if n is None or n.servicio != "reddit" or not n.listable or (n.account_id, n.timeline) not in self._cargados:
            return
        if not (datos_reddit.es_posts(n.timeline) or n.timeline == "comments" or datos_reddit.es_bandeja(n.timeline)):
            return
        minimo = max(30, int(config.get("reddit", "update_interval", 120) or 120))
        ahora = time.monotonic()
        if ahora - getattr(self, "_rd_ultimo_sondeo", 0.0) < minimo:
            return
        self._rd_ultimo_sondeo = ahora
        self.rd_actualizar_nodo(n, silencioso=True)

    def rd_tras_publicar(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "reddit" and n.listable and datos_reddit.es_posts(n.timeline):
            self.rd_actualizar_nodo(n, silencioso=True)

    # ------------------------------------------------------------------ votar, guardar y ocultar
    def _rd_voto(self, obj, direccion: str):
        x = obj.fila.extra
        voto = x.get("voto")
        if (direccion == "up" and voto == 1) or (direccion == "down" and voto == 0):
            direccion = "clear"
        mensajes = {"up": _("Voto positivo"), "down": _("Voto negativo"), "clear": _("Voto retirado")}
        self._rd_hacer(lambda: reddit_service.votar(x["account_id"], x["fullname"], direccion), mensajes[direccion],
                       _("No se pudo votar: {e}"), lambda r: self._rd_repintar(obj.fila.id))

    def rd_me_gusta(self):
        obj = self._rd_obj(TIPOS_CON_VOTO)
        if obj:
            self._rd_voto(obj, "up")

    def rd_voto_negativo(self):
        obj = self._rd_obj(TIPOS_CON_VOTO)
        if obj:
            self._rd_voto(obj, "down")

    def rd_guardar(self):
        obj = self._rd_obj(TIPOS_CON_VOTO)
        if not obj:
            return
        x = obj.fila.extra
        guardar = not x.get("guardado")
        self._rd_hacer(lambda: reddit_service.guardar(x["account_id"], x["fullname"], guardar),
                       _("Guardado") if guardar else _("Quitado de guardados"), None,
                       lambda r: self._rd_repintar(obj.fila.id))

    def rd_ocultar(self):
        obj = self._rd_obj(TIPOS_POST)
        if not obj:
            return
        x = obj.fila.extra
        ocultar = not x.get("oculto")
        self._rd_hacer(lambda: reddit_service.ocultar(x["account_id"], x["fullname"], ocultar),
                       _("Publicación oculta") if ocultar else _("Publicación visible"), None,
                       lambda r: self._rd_repintar(obj.fila.id))

    def rd_marca(self, cual: str):
        """Marcar o quitar +18 o spoiler de una publicación propia."""
        obj = self._rd_obj(TIPOS_POST)
        if not obj:
            return
        x = obj.fila.extra
        if not x.get("propio"):
            self.avisar(_("Solo puedes cambiarlo en tus propias publicaciones"))
            return
        nsfw = cual == "nsfw"
        activar = not x.get("nsfw" if nsfw else "spoiler")
        f = reddit_service.marcar_nsfw if nsfw else reddit_service.marcar_spoiler
        textos = {("nsfw", True): _("Marcada como +18"), ("nsfw", False): _("Marca +18 quitada"),
                  ("spoiler", True): _("Marcada como spoiler"), ("spoiler", False): _("Marca de spoiler quitada")}
        self._rd_hacer(lambda: f(x["account_id"], x["fullname"], activar), textos[(cual, activar)], None,
                       lambda r: self._rd_repintar(obj.fila.id))

    def rd_flair(self):
        obj = self._rd_obj(TIPOS_POST)
        if not obj:
            return
        x = obj.fila.extra
        if not x.get("propio"):
            self.avisar(_("Solo puedes cambiar la etiqueta de tus publicaciones"))
            return

        def _opciones(res):
            if not res:
                self.avisar(_("Esta comunidad no ofrece etiquetas para las publicaciones"))
                return
            textos = [o.get("text") or _("(sin texto)") for o in res]

            def _elegida(i):
                sel = res[i]
                self._rd_hacer(lambda: reddit_service.aplicar_flair(x["account_id"], x["fullname"], sel.get("id"),
                                                                    sel.get("text")), _("Etiqueta aplicada"), None,
                               lambda r: self._rd_repintar(obj.fila.id))
            DialogoElegir(self, _("Etiqueta de la publicación"), _("_Etiqueta (flair):"), textos, _elegida,
                          _("_Aplicar")).present()
        self._rd_hacer(lambda: reddit_service.opciones_flair(x["account_id"], x["fullname"]), None,
                       _("No se pudieron obtener las etiquetas: {e}"), _opciones)

    # ------------------------------------------------------------------ publicar, responder, editar y borrar
    def rd_nuevo(self):
        n = self.nodo_actual
        acc = self._rd_cuenta()
        if acc is None:
            return
        if n is not None and datos_reddit.es_bandeja(n.timeline):
            return self.rd_mensaje_nuevo()
        sub = n.timeline.split(":")[1] if n is not None and n.timeline.startswith("sub:") else ""
        from .dialogos_reddit import DialogoPublicarReddit
        DialogoPublicarReddit(self, acc, sub).present()

    def rd_responder(self):
        obj = self._rd_obj(TIPOS_RESPONDIBLES)
        if not obj:
            return
        x = obj.fila.extra
        from .dialogos_reddit import DialogoTextoLibre
        if x["tipo"] == "mensaje":
            if x.get("tipo_mensaje") == "sent":
                self.avisar(_("Es un mensaje enviado por ti"))
                return

            def _enviar(texto):
                self._rd_hacer(lambda: reddit_service.responder_mensaje(x["account_id"], x["fullname"], texto),
                               _("Respuesta enviada"))
            DialogoTextoLibre(self, _("Responder mensaje"), _("_Respuesta:"), _enviar, aceptar=_("_Enviar")).present()
            return

        def _enviar(texto):
            self.avisar(_("Enviando respuesta…"))
            self._rd_hacer(lambda: reddit_service.responder(x["account_id"], x["fullname"], texto), _("Respuesta enviada"))
        DialogoTextoLibre(self, _("Responder en Reddit"), _("_Respuesta:"), _enviar, aceptar=_("_Enviar")).present()

    def rd_editar(self):
        obj = self._rd_obj(TIPOS_CON_VOTO)
        if not obj:
            return
        x = obj.fila.extra
        if not x.get("propio"):
            self.avisar(_("Solo puedes editar lo tuyo"))
            return
        tabla, campo = ("reddit_comments", "body") if x["tipo"] == "comentario" else ("reddit_posts", "selftext")
        fila = db.query_one(f"SELECT COALESCE({campo}, '') FROM {tabla} WHERE id = ?", (obj.fila.id,))
        if x["tipo"] == "post" and not x.get("es_texto"):
            self.avisar(_("Solo se puede editar el texto de una publicación de texto"))
            return
        from .dialogos_reddit import DialogoTextoLibre

        def _guardar(texto):
            self._rd_hacer(lambda: reddit_service.editar(x["account_id"], x["fullname"], texto), _("Editado"), None,
                           lambda r: self._rd_repintar(obj.fila.id))
        DialogoTextoLibre(self, _("Editar en Reddit"), _("_Texto:"), _guardar, texto=fila[0] if fila else "",
                          aceptar=_("_Guardar")).present()

    def rd_borrar(self):
        obj = self._rd_obj(TIPOS_CON_VOTO)
        if not obj:
            return
        x = obj.fila.extra
        if not x.get("propio"):
            self.avisar(_("Solo puedes borrar lo tuyo"))
            return

        def _hacer():
            def _fin(res, err):
                if err:
                    self._rd_error(err, _("No se pudo borrar: {e}"))
                    return
                self._rd_quitar_fila(obj)
                self.avisar(_("Borrado"))
            ejecutar(lambda: reddit_service.borrar(x["account_id"], x["fullname"]), _fin)
        confirmar_async(self, _("¿Seguro que quieres borrar este elemento? No se puede deshacer."), _hacer,
                        etiqueta_si=_("Borrar"))

    def _rd_quitar_fila(self, obj):
        for i in range(self.almacen_lista.get_n_items()):
            if self.almacen_lista.get_item(i) is obj:
                self.almacen_lista.remove(i)
                n = self.almacen_lista.get_n_items()
                if n:
                    self.seleccion_lista.set_selected(min(i, n - 1))
                break

    def rd_crosspost(self):
        obj = self._rd_obj(TIPOS_POST)
        if not obj:
            return
        x = obj.fila.extra
        from .dialogos_reddit import DialogoPublicarReddit
        DialogoPublicarReddit(self, x["account_id"], crosspost=(x["fullname"], x.get("titulo_post") or "")).present()

    def rd_mensaje_nuevo(self, destinatario: str = ""):
        acc = self._rd_cuenta()
        if acc is not None:
            from .dialogos_reddit import DialogoMensajeReddit
            DialogoMensajeReddit(self, acc, destinatario).present()

    def rd_mensaje_a_autor(self):
        obj = self._rd_obj(TIPOS_RESPONDIBLES)
        if obj and obj.fila.extra.get("usuario"):
            self.rd_mensaje_nuevo(obj.fila.extra["usuario"])

    # ------------------------------------------------------------------ hilo, perfil y navegación
    def rd_abrir_hilo(self, obj=None):
        obj = obj or self._rd_obj(("post", "comentario"))
        if not obj:
            return
        x = obj.fila.extra
        if x["tipo"] == "post":
            return self._rd_abrir_visor(x["account_id"], obj.fila.id)
        link = x.get("link_id")
        if not link:
            self.avisar(_("No se pudo localizar la publicación de este comentario"))
            return
        post_db_id = f"{x['account_id']}:{link}"
        if not db.query_one("SELECT 1 FROM reddit_posts WHERE id = ?", (post_db_id,)):
            db.execute("INSERT OR IGNORE INTO reddit_posts (id, account_id, fullname, post_id) VALUES (?, ?, ?, ?)",
                       (post_db_id, x["account_id"], link, link.split("_", 1)[-1]))
        self._rd_abrir_visor(x["account_id"], post_db_id)

    def _rd_abrir_visor(self, account_id: int, post_db_id: str):
        from .dialogos_reddit import DialogoHiloReddit
        DialogoHiloReddit(self, account_id, post_db_id).present()

    def rd_perfil(self):
        obj = self._rd_obj(("post", "comentario", "mensaje"))
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("usuario"):
            return
        from .dialogos_reddit import DialogoPerfilReddit
        DialogoPerfilReddit(self, x["account_id"], x["usuario"]).present()

    def rd_perfil_de(self):
        acc = self._rd_cuenta()
        if acc is None:
            return

        def _con(texto):
            from .dialogos_reddit import DialogoPerfilReddit
            DialogoPerfilReddit(self, acc, texto).present()
        DialogoTexto(self, _("Perfil de un usuario"), _("_Usuario de Reddit (sin u/):"), _con, aceptar=_("_Ver")).present()

    def _rd_subreddit_actual(self) -> str:
        n = self.nodo_actual
        if n is not None and n.servicio == "reddit" and n.timeline.startswith("sub:") and n.timeline.count(":") == 1:
            return n.timeline[4:]
        return ""

    def rd_wiki(self):
        acc = self._rd_cuenta()
        if acc is None:
            return

        def _con(texto):
            from .dialogos_reddit_comunidad import DialogoWikiReddit
            DialogoWikiReddit(self, acc, texto.strip().lstrip("r/").lstrip("/")).present()
        d = DialogoTexto(self, _("Wiki de un subreddit"), _("_Subreddit (sin r/):"), _con, aceptar=_("_Abrir"))
        d.entrada.set_text(self._rd_subreddit_actual())
        d.present()

    def rd_modmail(self):
        acc = self._rd_cuenta()
        if acc is None:
            return
        from .dialogos_reddit_comunidad import DialogoModmailReddit
        DialogoModmailReddit(self, acc, self._rd_subreddit_actual() or "all").present()

    def _rd_pedir_sub(self, titulo: str, clase: str):
        acc = self._rd_cuenta()
        if acc is None:
            return

        def _con(texto):
            from . import dialogos_reddit_comunidad as c
            getattr(c, clase)(self, acc, texto.strip().lstrip("r/").lstrip("/")).present()
        d = DialogoTexto(self, titulo, _("_Subreddit (sin r/):"), _con, aceptar=_("_Abrir"))
        d.entrada.set_text(self._rd_subreddit_actual())
        d.present()

    def rd_live(self):
        acc = self._rd_cuenta()
        if acc is not None:
            from .dialogos_reddit_avanzado import DialogoLiveReddit
            DialogoLiveReddit(self, acc).present()

    def rd_colecciones(self):
        acc = self._rd_cuenta()
        if acc is None:
            return

        def _con(texto):
            from .dialogos_reddit_avanzado import DialogoColeccionesReddit
            DialogoColeccionesReddit(self, acc, texto.strip().lstrip("r/").lstrip("/")).present()
        d = DialogoTexto(self, _("Colecciones de un subreddit"), _("_Subreddit (sin r/):"), _con, aceptar=_("_Abrir"))
        d.entrada.set_text(self._rd_subreddit_actual())
        d.present()

    def rd_borradores(self):
        acc = self._rd_cuenta()
        if acc is not None:
            from .dialogos_reddit_avanzado import DialogoBorradoresReddit
            DialogoBorradoresReddit(self, acc).present()

    # ------------------------------------------------------------------ moderación
    def _rd_elegir_subreddit_que_modero(self, titulo: str, fn):
        """Carga los subreddits que modera la cuenta y entrega el elegido a `fn(cuenta, subreddit)` (si solo modera uno, ese)."""
        acc = self._rd_cuenta()
        if acc is None:
            return
        self.avisar(_("Cargando los subreddits que moderas…"))

        def _fin(subs, err):
            if err:
                self.avisar(_("No se pudo obtener la lista de subreddits que moderas: {e}").format(e=err))
                return
            if not subs:
                self.avisar(_("No moderas ningún subreddit con esta cuenta."))
            elif len(subs) == 1:
                fn(acc, subs[0])
            else:
                DialogoElegir(self, titulo, _("_Subreddit:"), subs, lambda i: fn(acc, subs[i])).present()
        ejecutar(lambda: reddit_service.subreddits_que_modero(acc), _fin)

    def rd_mod_cola(self):
        from .dialogos_reddit_moderacion import DialogoColaModReddit
        self._rd_elegir_subreddit_que_modero(_("Cola de moderación"), lambda a, s: DialogoColaModReddit(self, a, s).present())

    def rd_mod_usuarios(self):
        from .dialogos_reddit_moderacion import DialogoUsuariosModReddit
        self._rd_elegir_subreddit_que_modero(_("Usuarios del subreddit"),
                                             lambda a, s: DialogoUsuariosModReddit(self, a, s).present())

    def rd_mod_motivos(self):
        from .dialogos_reddit_moderacion import DialogoMotivosModReddit
        self._rd_elegir_subreddit_que_modero(_("Motivos de eliminación"),
                                             lambda a, s: DialogoMotivosModReddit(self, a, s).present())

    def rd_mod_config(self):
        from .dialogos_reddit_moderacion import DialogoConfigSubreddit
        self._rd_elegir_subreddit_que_modero(_("Configuración del subreddit"),
                                             lambda a, s: DialogoConfigSubreddit(self, a, s).present())

    def rd_mod_registro(self):
        from .dialogos_reddit_moderacion import ver_registro
        self._rd_elegir_subreddit_que_modero(_("Registro de moderación"), lambda a, s: ver_registro(self, a, s))

    def rd_moderar_fila(self):
        """Acciones de moderación sobre la publicación o el comentario enfocado."""
        obj = self._rd_obj(("post", "comentario"))
        if obj:
            from .dialogos_reddit_moderacion import moderar_elemento
            x = obj.fila.extra
            moderar_elemento(self, x["account_id"], x["fullname"], x["tipo"] == "comentario")

    def rd_abrir_usuario(self, account_id: int, usuario: str):
        self._abrir_buffer(account_id, f"user:{usuario}", datos_reddit.titulo_buffer(f"user:{usuario}"), servicio="reddit")

    def rd_cronologia_autor(self):
        obj = self._rd_obj(("post", "comentario"))
        if obj and obj.fila.extra.get("usuario"):
            self.rd_abrir_usuario(obj.fila.extra["account_id"], obj.fila.extra["usuario"])

    def rd_abrir_subreddit(self, nombre: str, acc: int = None):
        acc = acc or self._rd_cuenta()
        if acc is not None and nombre:
            self._abrir_buffer(acc, f"sub:{nombre}", datos_reddit.titulo_buffer(f"sub:{nombre}"), servicio="reddit")

    def rd_ir_subreddit(self):
        acc = self._rd_cuenta()
        if acc is None:
            return
        DialogoTexto(self, _("Ir a un subreddit"), _("_Subreddit (sin r/):"),
                     lambda t: self.rd_abrir_subreddit(t.strip().removeprefix("/").removeprefix("r/"), acc),
                     aceptar=_("_Abrir")).present()

    def rd_autor_subreddit(self):
        obj = self._rd_obj(("post", "comentario"))
        if obj and obj.fila.extra.get("subreddit"):
            self.rd_abrir_subreddit(obj.fila.extra["subreddit"], obj.fila.extra["account_id"])

    def rd_buscar(self):
        acc = self._rd_cuenta()
        if acc is None:
            return
        DialogoTexto(self, _("Buscar en Reddit"), _("_Buscar:"),
                     lambda t: self._abrir_buffer(acc, f"search:{t}", datos_reddit.titulo_buffer(f"search:{t}"),
                                                  servicio="reddit"),
                     _("Busca publicaciones en todo Reddit. La búsqueda queda guardada como buffer; ciérrala con Ctrl+W."),
                     _("_Buscar")).present()

    def rd_buscar_subreddits(self):
        acc = self._rd_cuenta()
        if acc is None:
            return

        def _buscar(consulta):
            self.avisar(_("Buscando subreddits…"))

            def _res(res):
                if not res:
                    self.avisar(_("No se encontraron subreddits"))
                    return
                etiquetas = [_("r/{0} — {1} suscriptores").format(s["name"], s.get("subscribers", 0)) for s in res]
                DialogoElegir(self, _("Resultados de subreddits"), _("_Subreddit:"), etiquetas,
                              lambda i: self.rd_abrir_subreddit(res[i]["name"], acc), _("_Abrir")).present()
            self._rd_hacer(lambda: reddit_service.buscar_subreddits(acc, consulta), None,
                           _("Error en la búsqueda: {e}"), _res)
        DialogoTexto(self, _("Buscar subreddits"), _("_Nombre o tema:"), _buscar, aceptar=_("_Buscar")).present()

    def rd_buscar_usuarios(self):
        acc = self._rd_cuenta()
        if acc is None:
            return

        def _buscar(consulta):
            self.avisar(_("Buscando usuarios…"))

            def _res(res):
                if not res:
                    self.avisar(_("No se encontraron usuarios"))
                    return
                from .dialogos_reddit import DialogoPerfilReddit
                DialogoElegir(self, _("Resultados de usuarios"), _("_Usuario:"), [_("u/{0}").format(u["name"]) for u in res],
                              lambda i: DialogoPerfilReddit(self, acc, res[i]["name"]).present(), _("_Ver el perfil")).present()
            self._rd_hacer(lambda: reddit_service.buscar_usuarios(acc, consulta), None,
                           _("Error en la búsqueda: {e}"), _res)
        DialogoTexto(self, _("Buscar usuarios"), _("_Nombre de usuario:"), _buscar, aceptar=_("_Buscar")).present()

    def rd_orden(self):
        """Abre el buffer actual con otro orden (nuevos, mejores, en auge...). Solo para feeds y subreddits."""
        n = self.nodo_actual
        if n is None or n.servicio != "reddit" or not n.listable:
            self.avisar(_("Selecciona un buffer de Reddit"))
            return
        partes = n.timeline.split(":")
        base = ":".join(partes[:2]) if partes[0] == "sub" else partes[0]
        if partes[0] not in ("home", "popular", "all", "sub"):
            self.avisar(_("Este buffer no se puede ordenar"))
            return

        def _elegido(i):
            clave = ORDEN_CLAVES[i]
            tl = base if clave == "hot" else f"{base}:{clave}"
            if tl == n.timeline:
                return
            titulo = datos_reddit.titulo_buffer(base) + (f" ({_(ORDEN_TITULOS[i])})" if clave != "hot" else "")
            self._abrir_buffer_extra(n.account_id, tl, titulo)
        DialogoElegir(self, _("Orden de las publicaciones"), _("_Ordenar por:"), [_(t) for t in ORDEN_TITULOS], _elegido,
                      _("_Abrir")).present()

    def _abrir_buffer_extra(self, account_id: int, timeline: str, titulo: str):
        """Un orden distinto de un feed se guarda como buffer propio de la interfaz GTK (sub: ya lo guarda por su nombre)."""
        nodo = datos_reddit.nodo_extra(account_id, timeline, titulo)
        self.cargar_arbol(seleccionar=(nodo.servicio, nodo.account_id, nodo.tipo, nodo.category_id, nodo.feed_id,
                                       nodo.timeline))
        self.enfocar_panel(self.vista_lista)
        self.avisar(_("Abriendo {t}…").format(t=titulo))

    def rd_abrir_fila(self, obj=None):
        """Intro sobre una fila que no es una publicación: abre el subreddit, el multireddit o la búsqueda; responde un mensaje."""
        obj = obj or self._fila_actual()
        if obj is None or obj.fila.servicio != "reddit":
            return False
        x = obj.fila.extra
        tipo = x.get("tipo")
        if tipo == "subreddit":
            self.rd_abrir_subreddit(x["subreddit"], x["account_id"])
        elif tipo == "multi":
            if x.get("clave"):
                self._abrir_buffer(x["account_id"], x["clave"], datos_reddit.titulo_buffer(x["clave"], x.get("nombre", "")),
                                   servicio="reddit")
            elif obj.fila.enlace:
                webbrowser.open(obj.fila.enlace)
        elif tipo == "busqueda":
            self._abrir_buffer(x["account_id"], "search:" + x["termino"], datos_reddit.titulo_buffer("search:" + x["termino"]),
                               servicio="reddit")
        elif tipo == "mensaje":
            if x.get("contexto"):
                webbrowser.open(obj.fila.enlace)
            else:
                self.rd_responder()
        else:
            return False
        return True

    def rd_suscribirse(self, suscribir: bool = True):
        obj = self._fila_actual()
        sub = obj.fila.extra.get("subreddit") if obj is not None and obj.fila.servicio == "reddit" else ""
        if not sub:
            self.avisar(_("Selecciona un subreddit o una publicación"))
            return
        acc = obj.fila.extra["account_id"]
        self._rd_hacer(lambda: reddit_service.suscribirse(acc, sub, suscribir),
                       _("Suscrito a r/{0}").format(sub) if suscribir else _("Suscripción a r/{0} cancelada").format(sub),
                       None, lambda r: self._rd_repintar())

    def rd_fijar_subreddit(self):
        obj = self._fila_actual()
        sub = obj.fila.extra.get("subreddit") if obj is not None and obj.fila.servicio == "reddit" else ""
        if not sub:
            self.avisar(_("Selecciona un subreddit o una publicación"))
            return
        datos_reddit.nodo_extra(obj.fila.extra["account_id"], f"sub:{sub}", sub)
        self.cargar_arbol()
        self.avisar(_("r/{0} fijado en el árbol").format(sub))

    # ------------------------------------------------------------------ bandeja
    def rd_leido(self):
        obj = self._rd_obj(("mensaje",))
        if not obj:
            return
        x = obj.fila.extra
        nuevo = bool(x.get("nuevo"))
        self._rd_hacer(lambda: reddit_service.marcar_leido(x["account_id"], x["fullname"], nuevo),
                       _("Marcado como leído") if nuevo else _("Marcado como no leído"), None,
                       lambda r: self._rd_repintar(obj.fila.id))

    def rd_marcar_todo_leido(self):
        acc = self._rd_cuenta()
        if acc is not None:
            self._rd_hacer(lambda: reddit_service.marcar_todo_leido(acc), _("Bandeja marcada como leída"), None,
                           lambda r: self._rd_repintar())

    # ------------------------------------------------------------------ cuenta
    def rd_reautorizar(self):
        n = self.nodo_actual
        if n is None or n.servicio != "reddit" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de Reddit"))
            return
        from .dialogos_reddit import DialogoCuentaReddit
        DialogoCuentaReddit(self, lambda _id: self.avisar(_("Ya puedes usar la cuenta")), account_id=n.account_id).present()

    def rd_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "reddit" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de Reddit"))
            return
        acc = n.account_id
        nombre = next((nm for i, nm in datos_reddit.cuentas() if i == acc), "")

        def _hacer():
            reddit_service.eliminar_cuenta(acc)
            self.cargar_arbol(conservar=False)
            self.avisar(_("Cuenta eliminada"))
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    def rd_ocultar_buffers(self):
        """Mostrar u ocultar los buffers fijos (se guarda en reddit/hidden_buffers, como en Windows)."""
        ocultos = set(config.get("reddit", "hidden_buffers", []) or [])
        opciones = list(datos_reddit.BUFFERS)
        etiquetas = [_(t) + (_(" (oculto)") if c in ocultos else "") for c, t in opciones]

        def _alternar(i):
            clave = opciones[i][0]
            if clave in ocultos:
                ocultos.discard(clave)
            else:
                ocultos.add(clave)
            config.set("reddit", "hidden_buffers", sorted(ocultos))
            self.cargar_arbol()
            self.avisar(_("Buffer {b} {e}").format(b=_(opciones[i][1]), e=_("oculto") if clave in ocultos else _("visible")))
        DialogoElegir(self, _("Mostrar u ocultar buffers"), _("_Buffer:"), etiquetas, _alternar, _("_Cambiar")).present()

    # ------------------------------------------------------------------ menú contextual y acciones
    def rd_menu_contextual(self, obj):
        """Lista (etiqueta, acción) del menú contextual de una fila de Reddit."""
        x = obj.fila.extra
        tipo = x.get("tipo")
        if tipo == "mensaje":
            lista = [(_("Responder"), "win.responder"), (_("Marcar como leído o no leído"), "win.leido"),
                     (_("Perfil del autor"), "win.perfil"), (_("Traducir"), "win.traducir")]
            if x.get("contexto"):
                lista.append((_("Ver el comentario en el navegador"), "win.navegador"))
            lista.append((_("Marcar toda la bandeja como leída"), "win.marcar-todo-leido"))
            return tuple(lista)
        if tipo == "subreddit":
            return ((_("Abrir el subreddit"), "win.rd-abrir-fila"), (_("Fijar en el árbol"), "win.rd-fijar"),
                    (_("Cancelar la suscripción"), "win.rd-cancelar-suscripcion"),
                    (_("Abrir en el navegador"), "win.navegador"))
        if tipo in ("multi", "busqueda"):
            return ((_("Abrir"), "win.rd-abrir-fila"),)
        lista = [(_("Ver los comentarios"), "win.conversacion"), (_("Responder"), "win.responder"),
                 (_("Voto positivo / quitar voto"), "win.me-gusta"), (_("Voto negativo / quitar voto"), "win.voto-negativo"),
                 (_("Guardar / quitar de guardados"), "win.marcador")]
        if tipo == "post":
            lista += [(_("Ocultar / mostrar"), "win.rd-ocultar"), (_("Crosspost a otra comunidad…"), "win.impulsar")]
        lista += [(_("Traducir"), "win.traducir"), (_("Resumir con IA"), "win.resumir-ia"),
                  (_("Perfil del autor"), "win.perfil"), (_("Publicaciones del autor"), "win.cronologia-usuario"),
                  (_("Abrir el subreddit"), "win.rd-subreddit-fila"), (_("Suscribirse al subreddit"), "win.rd-suscribirse"),
                  (_("Fijar el subreddit en el árbol"), "win.rd-fijar"), (_("Mensaje privado al autor"), "win.rd-mensaje-autor")]
        if x.get("propio"):
            if tipo == "comentario" or x.get("es_texto"):
                lista.append((_("Editar"), "win.editar"))
            lista.append((_("Borrar"), "win.papelera"))
            if tipo == "post":
                lista += [(_("Marcar o quitar +18"), "win.rd-nsfw"), (_("Marcar o quitar spoiler"), "win.rd-spoiler"),
                          (_("Etiqueta (flair)…"), "win.rd-flair")]
        lista += [(_("Moderar…"), "win.rd-moderar"), (_("Abrir en el navegador"), "win.navegador"),
                  (_("Copiar el enlace"), "win.copiar-enlace")]
        return tuple(lista)

    def _registrar_acciones_reddit(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("rd-ocultar", self.rd_ocultar)
        a("rd-nsfw", lambda: self.rd_marca("nsfw"))
        a("rd-spoiler", lambda: self.rd_marca("spoiler"))
        a("rd-flair", self.rd_flair)
        a("rd-mensaje-autor", self.rd_mensaje_a_autor)
        a("rd-mensaje-nuevo", self.rd_mensaje_nuevo)
        a("rd-perfil-de", self.rd_perfil_de)
        a("rd-wiki", self.rd_wiki)
        a("rd-modmail", self.rd_modmail)
        a("rd-live", self.rd_live)
        a("rd-flairs", lambda: self._rd_pedir_sub(_("Etiquetas de un subreddit"), "DialogoFlairsReddit"))
        a("rd-emojis", lambda: self._rd_pedir_sub(_("Emojis de un subreddit"), "DialogoEmojisReddit"))
        a("rd-widgets", lambda: self._rd_pedir_sub(_("Barra lateral de un subreddit"), "DialogoWidgetsReddit"))
        a("rd-mod-cola", self.rd_mod_cola)
        a("rd-mod-usuarios", self.rd_mod_usuarios)
        a("rd-mod-motivos", self.rd_mod_motivos)
        a("rd-mod-config", self.rd_mod_config)
        a("rd-mod-registro", self.rd_mod_registro)
        a("rd-moderar", self.rd_moderar_fila)
        a("rd-colecciones", self.rd_colecciones)
        a("rd-borradores", self.rd_borradores)
        a("rd-buscar", self.rd_buscar)
        a("rd-buscar-subreddits", self.rd_buscar_subreddits)
        a("rd-buscar-usuarios", self.rd_buscar_usuarios)
        a("rd-ir-subreddit", self.rd_ir_subreddit)
        a("rd-subreddit-fila", self.rd_autor_subreddit)
        a("rd-orden", self.rd_orden)
        a("rd-abrir-fila", self.rd_abrir_fila)
        a("rd-suscribirse", lambda: self.rd_suscribirse(True))
        a("rd-cancelar-suscripcion", lambda: self.rd_suscribirse(False))
        a("rd-fijar", self.rd_fijar_subreddit)
        a("rd-reautorizar", self.rd_reautorizar)
        a("rd-ocultar-buffers", self.rd_ocultar_buffers)
        a("rd-copiar", lambda: copiar_al_portapapeles(self._fila_actual().fila.enlace, _("Enlace copiado"))
          if self._fila_actual() is not None else None)


ORDEN_CLAVES = [c for c, _t in datos_reddit.ORDENES]
ORDEN_TITULOS = [t for _c, t in datos_reddit.ORDENES]
