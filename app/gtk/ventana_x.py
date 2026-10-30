# -*- coding: utf-8 -*-
"""X (Twitter) en la ventana principal (mixin de VentanaPrincipal).

Las acciones comparten teclas con Mastodon y Bluesky (Ctrl+B me gusta, Ctrl+R responder...): los manejadores de ventana.py
llaman aquí cuando el elemento enfocado es de X. Todo lo de este archivo lleva el prefijo `tw_`, porque `x_` es de XMPP.

X limita las peticiones con dureza y este cliente no es oficial, así que NO se actualiza solo salvo que el usuario active
"Actualización automática" (x/auto_update): lo normal es Ctrl+Mayús+R y la primera carga de cada buffer."""

import logging
import re
import time

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.plataforma import llamar_en_ui
from app.core.speech import speak
from app.core.task_manager import task_manager
from app.services.x import x_service

from . import datos_x
from .dialogos import DialogoElegir, DialogoTexto, confirmar_async

log = logging.getLogger(__name__)

TIPOS_PUBLICACION = ("estado",)
TIPOS_CON_PUBLICACION = ("estado", "notificacion")


class _TwitterMixin:
    _tw_ult_escribiendo: dict = {}

    # ------------------------------------------------------------------ contexto
    def _tw_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de X."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "x"
        n = self.nodo_actual
        return n is not None and n.servicio == "x"

    def _tw_obj(self, tipos=TIPOS_PUBLICACION):
        """La fila de X enfocada si es de alguno de esos tipos; si no, avisa."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "x":
            self.avisar(_("Selecciona una publicación de X"))
            return None
        if obj.fila.extra.get("tipo") not in tipos:
            self.avisar(_("Esta acción no se puede hacer con este elemento de X"))
            return None
        return obj

    def _tw_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "x" and n.account_id:
            return n.account_id
        cuentas = datos_x.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de X"))
        return None

    def _tw_nodos_cuenta(self, account_id):
        for raiz in getattr(self, "arbol", []):
            if raiz.servicio == "x":
                return [h for c in raiz.hijos if c.account_id == account_id for h in c.hijos]
        return []

    @staticmethod
    def _tw_es_error_sesion(err) -> bool:
        if err is None:
            return False
        if getattr(err, "auth", False):
            return True
        t = str(err).lower()
        return any(k in t for k in ("cookies incorrectas", "caducad", "no ha reconocido", "no es válida",
                                    "no reconocido", "sesión"))

    def _tw_recuperar_sesion(self, account_id: int, reintentar) -> bool:
        """Si la sesión caducó, recarga las cookies del navegador una vez y reintenta lo que falló."""
        if getattr(self, "_tw_reauth", False):
            return False
        self._tw_reauth = True
        self.avisar(_("La sesión de X parece caducada; recargando las cookies del navegador…"))

        def _fin(res, err):
            self._tw_reauth = False
            if err:
                self.avisar(_("No se pudieron recargar las cookies: {e}. Usa Reimportar las cookies en el menú X.").format(e=err))
                return
            self.avisar(_("Cookies de X recargadas. Reintentando…"))
            reintentar()
        task_manager.start_task(lambda ctx: x_service.reimportar_desde_navegador(account_id, "auto"),
                                callback=_fin, pass_context=True)
        return True

    def _tw_error(self, err, account_id, reintentar, silencioso=False, prefijo=None):
        """Tratamiento común de un fallo de red: reautenticar si es de sesión, o avisar."""
        if self._tw_es_error_sesion(err) and self._tw_recuperar_sesion(account_id, reintentar):
            return
        if not silencioso:
            self.avisar((prefijo or _("Error: {e}")).format(e=err))

    # ------------------------------------------------------------------ actualizar y cargar más
    def tw_actualizar_nodo(self, nodo, silencioso: bool = False):
        clave = (nodo.account_id, nodo.timeline)
        primera = clave not in self._cargados
        antes = {self.almacen_lista.get_item(i).fila.id for i in range(self.almacen_lista.get_n_items())} \
            if nodo is self.nodo_actual else set()
        acc, tl = nodo.account_id, nodo.timeline

        def _fin(res, err):
            if err:
                self._tw_error(err, acc, lambda: self.tw_actualizar_nodo(nodo, silencioso), silencioso,
                               _("Error al actualizar: {e}"))
                return
            self._cargados.add(clave)
            if tl.startswith("dm:"):
                self._tw_dm_arrancar(nodo)
            if nodo is not self.nodo_actual or self.busqueda:
                return

            def _repintado(filas):
                nuevos = [f for f in filas if f.id not in antes]
                if primera or not nuevos:
                    if not silencioso:
                        self.estado.set_text(_("{t}: actualizado").format(t=nodo.titulo))
                    return
                if tl.startswith("dm:"):
                    self._tw_anunciar_dm(nuevos)
                    return
                event_bus.emit("sound_trigger", "notification" if tl == "notifications" else "timeline")
                self.avisar(_("{n} nuevas").format(n=len(nuevos)), interrumpir=False)
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True, despues=_repintado)
        if tl == "notifications":
            x_service.obtener_notificaciones_async(acc, callback=_fin)
        elif tl in datos_x.ACTORES:
            x_service.obtener_actores_async(acc, tl, callback=_fin)
        elif tl.startswith("dm:"):
            uid = tl[3:]
            x_service.cargar_dms_async(acc, uid, self._tw_screen_dm(acc, uid), callback=_fin)
        else:
            x_service.obtener_timeline_async(acc, tl, callback=_fin)

    def tw_cargar_mas(self):
        n = self.nodo_actual
        if n is None or not n.listable or not datos_x.es_cronologico(n.timeline):
            self.avisar(_("Aquí no se pueden cargar publicaciones más antiguas"))
            return
        self.avisar(_("Cargando publicaciones anteriores…"))
        actual = self._fila_actual()

        def _fin(nuevos, err):
            if err:
                self._tw_error(err, n.account_id, self.tw_cargar_mas, False, _("Error al cargar: {e}"))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            self.avisar(_("{n} cargadas").format(n=nuevos) if nuevos else _("No hay más publicaciones"))
        x_service.cargar_anteriores_async(n.account_id, n.timeline, count=40, callback=_fin)

    def _sondeo_x(self):
        """Solo con la actualización automática activada (por defecto no: el riesgo de bloqueo es real)."""
        if not config.get("x", "auto_update", False):
            return
        minimo = int(config.get("x", "min_update_seconds", 120) or 120)
        ahora = time.monotonic()
        if ahora - getattr(self, "_tw_ultimo_sondeo", 0.0) < minimo:
            return
        self._tw_ultimo_sondeo = ahora
        for acc_id, _n in datos_x.cuentas():
            for nodo in self._tw_nodos_cuenta(acc_id):
                if nodo.timeline in ("home", "mentions", "notifications") and (acc_id, nodo.timeline) in self._cargados:
                    self.tw_actualizar_nodo(nodo, silencioso=True)

    def tw_tras_publicar(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "x" and n.listable and not n.timeline.startswith("dm:"):
            self.tw_actualizar_nodo(n, silencioso=True)

    # ------------------------------------------------------------------ acciones sobre una publicación
    def _tw_accion_estado(self, obj, on: str, off: str, propiedad: str, texto_on: str, texto_off: str):
        x = obj.fila.extra
        if not x.get("tweet_id"):
            self.avisar(_("Esta notificación no tiene publicación"))
            return
        activo = obj.get_property(propiedad)

        def _fin(res, err):
            if err:
                self._tw_error(err, x["account_id"], lambda: self._tw_accion_estado(obj, on, off, propiedad, texto_on,
                                                                                    texto_off), False,
                               _("No se pudo: {e}"))
                return
            obj.set_property(propiedad, not activo)
            self.avisar(texto_off if activo else texto_on)
        x_service.accion_async(x["account_id"], x["tweet_id"], off if activo else on, callback=_fin)

    def tw_me_gusta(self):
        obj = self._tw_obj()
        if obj:
            self._tw_accion_estado(obj, "like", "unlike", "favorito", _("Me gusta"), _("Me gusta quitado"))

    def tw_retuit(self):
        obj = self._tw_obj()
        if obj:
            self._tw_accion_estado(obj, "retweet", "unretweet", "impulsado", _("Retuiteado"), _("Retweet deshecho"))

    def tw_marcador(self):
        obj = self._tw_obj()
        if obj:
            self._tw_accion_estado(obj, "bookmark", "unbookmark", "marcador", _("Añadido a marcadores"),
                                   _("Quitado de marcadores"))

    def tw_nuevo(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "x" and n.timeline.startswith("dm:"):
            return self._tw_escribir_mensaje(n.account_id, n.timeline[3:], n.titulo)
        acc = self._tw_cuenta()
        if acc is not None:
            from .dialogos_x import DialogoPublicarX
            DialogoPublicarX(self, acc).present()

    def tw_responder(self):
        obj = self._tw_obj(("estado", "notificacion", "mensaje"))
        x = obj.fila.extra if obj else {}
        if x.get("tipo") == "mensaje":
            return self._tw_escribir_mensaje(x["account_id"], x["user_id"], x.get("titulo") or obj.fila.feed_titulo)
        if not obj or not x.get("tweet_id"):
            if obj:
                self.avisar(_("Esta notificación no tiene publicación"))
            return
        from .dialogos_x import DialogoPublicarX
        DialogoPublicarX(self, x["account_id"], texto_inicial=f"@{x['usuario']} " if x.get("usuario") else "",
                         responde_a=x["tweet_id"]).present()

    def tw_citar(self):
        obj = self._tw_obj(TIPOS_CON_PUBLICACION)
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("tweet_id"):
            return
        from .dialogos_x import DialogoPublicarX
        DialogoPublicarX(self, x["account_id"], cita_id=x["tweet_id"]).present()

    def tw_borrar(self):
        obj = self._tw_obj(("estado", "mensaje", "notificacion"))
        if not obj:
            return
        x = obj.fila.extra
        if x.get("tipo") == "mensaje":
            return self._tw_borrar_mensaje(obj)
        if not x.get("propio") or x.get("tipo") != "estado":
            self.avisar(_("Solo puedes borrar tus propias publicaciones"))
            return

        def _hacer():
            def _fin(res, err):
                if err:
                    self._tw_error(err, x["account_id"], _hacer, False, _("No se pudo borrar: {e}"))
                    return
                self._tw_quitar_fila(obj)
                self.avisar(_("Publicación borrada"))
            x_service.accion_async(x["account_id"], x["tweet_id"], "delete", callback=_fin)
        confirmar_async(self, _("¿Borrar esta publicación? No se puede deshacer."), _hacer, etiqueta_si=_("Borrar"))

    def _tw_quitar_fila(self, obj):
        for i in range(self.almacen_lista.get_n_items()):
            if self.almacen_lista.get_item(i) is obj:
                self.almacen_lista.remove(i)
                n = self.almacen_lista.get_n_items()
                if n:
                    self.seleccion_lista.set_selected(min(i, n - 1))
                break

    def tw_votar(self):
        obj = self._tw_obj()
        if not obj:
            return
        x = obj.fila.extra
        enc = datos_x.datos_encuesta(x["account_id"], obj.fila.id)
        if not enc:
            self.avisar(_("Esta publicación no tiene encuesta"))
            return
        if enc.get("final") or enc.get("selected"):
            from app.ui.x_lectura import texto_encuesta_x
            self.avisar(texto_encuesta_x(db.query_one("SELECT poll_json FROM x_tweets WHERE id = ?",
                                                      (obj.fila.id,))[0]))
            return
        from .dialogos import DialogoVotar
        etiquetas = [c.get("label", "") for c in enc["choices"]]

        def _votar(elegidas):
            def _fin(res, err):
                if err:
                    self._tw_error(err, x["account_id"], lambda: _votar(elegidas), False, _("No se pudo votar: {e}"))
                    return
                self.avisar(_("Voto registrado"))
                self.cargar_lista(conservar_id=obj.fila.id, incremental=True)
            fila = db.query_one("SELECT COALESCE(poll_json, '') FROM x_tweets WHERE id = ?", (obj.fila.id,))
            x_service.votar_encuesta_async(x["account_id"], x["tweet_id"], etiquetas[elegidas[0]], callback=_fin,
                                           poll_json=fila[0] if fila else "")
        DialogoVotar(self, {"options": etiquetas, "multiple": False}, _votar).present()

    # ------------------------------------------------------------------ conversaciones, cuentas y búsquedas
    def tw_conversacion(self):
        obj = self._tw_obj(TIPOS_CON_PUBLICACION)
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("tweet_id"):
            return
        self._abrir_buffer(x["account_id"], f"thread:{x['tweet_id']}",
                           f"{obj.fila.autor}: {obj.fila.titulo.split(': ', 1)[-1]}", servicio="x")

    def tw_abrir_cronologia(self, account_id: int, uid: str, screen: str, nombre: str = "", modo: str = "tweets"):
        clave = datos_x.guardar_cronologia(account_id, uid, screen, nombre, modo)
        self._abrir_buffer(account_id, clave, datos_x.titulo_cronologia(account_id, uid, modo), servicio="x")

    def tw_cronologia_usuario(self):
        obj = self._tw_obj(("estado", "notificacion", "usuario"))
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("usuario"):
            return
        if not x.get("author_id"):
            return self._tw_cronologia_de(x["account_id"], x["usuario"])
        self.tw_abrir_cronologia(x["account_id"], x["author_id"], x["usuario"], obj.fila.autor)

    def tw_cronologia_de(self):
        acc = self._tw_cuenta()
        if acc is None:
            return

        def _con(texto):
            self._tw_cronologia_de(acc, texto.lstrip("@"))
        DialogoTexto(self, _("Cronología de un usuario"), _("_Usuario de X (con o sin @):"), _con,
                     aceptar=_("_Abrir")).present()

    def _tw_cronologia_de(self, account_id: int, screen: str):
        self.avisar(_("Buscando a @{u}…").format(u=screen))

        def _fin(res, err):
            if err or not res:
                self.avisar(_("No se encontró a @{u}").format(u=screen) if not err
                            else _("No se pudo: {e}").format(e=err))
                return

            def _modo(i):
                self.tw_abrir_cronologia(account_id, res["user_id"], res["screen_name"], res.get("name", ""),
                                         datos_x.MODOS_CRONOLOGIA[i][0])
            DialogoElegir(self, _("¿Qué mostrar?"), _("_Mostrar:"), [_(t) for _c, t in datos_x.MODOS_CRONOLOGIA], _modo,
                          _("_Abrir")).present()
        x_service.resolver_usuario_async(account_id, screen, callback=_fin)

    def tw_perfil(self):
        obj = self._tw_obj(("estado", "notificacion", "usuario"))
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("usuario"):
            return
        from .dialogos_x import DialogoPerfilX
        DialogoPerfilX(self, x["account_id"], x["usuario"]).present()

    def tw_perfil_de(self):
        acc = self._tw_cuenta()
        if acc is None:
            return

        def _con(texto):
            from .dialogos_x import DialogoPerfilX
            DialogoPerfilX(self, acc, texto.lstrip("@")).present()
        DialogoTexto(self, _("Perfil de un usuario"), _("_Usuario de X (con o sin @):"), _con,
                     aceptar=_("_Ver")).present()

    def _tw_mencionados(self, obj):
        """@usuarios que aparecen en la publicación (texto, cita y quien la retuiteó), menos tú."""
        fila = db.query_one("SELECT COALESCE(text, ''), COALESCE(quoted_author, ''), COALESCE(retweeted_by, '') "
                            "FROM x_tweets WHERE id = ?", (obj.fila.id,))
        if not fila:
            return []
        yo = (x_service._yo(obj.fila.extra["account_id"]).get("screen_name") or "").lower()
        vistos, salida = set(), []
        candidatos = re.findall(r"@(\w{1,15})", fila[0]) + [re.sub(r".*\(@|\).*", "", fila[1]), fila[2].lstrip("@")]
        for c in candidatos:
            c = c.strip().lstrip("@")
            if c and c.lower() != yo and c.lower() not in vistos:
                vistos.add(c.lower())
                salida.append(c)
        return salida

    def tw_usuario_mencionado(self, que: str):
        obj = self._tw_obj()
        if not obj:
            return
        nombres = self._tw_mencionados(obj)
        if not nombres:
            self.avisar(_("Esta publicación no menciona a nadie más"))
            return
        acc = obj.fila.extra["account_id"]

        def _hacer(screen):
            if que == "perfil":
                from .dialogos_x import DialogoPerfilX
                DialogoPerfilX(self, acc, screen).present()
            else:
                self._tw_cronologia_de(acc, screen)
        if len(nombres) == 1:
            return _hacer(nombres[0])
        DialogoElegir(self, _("Usuarios mencionados"), _("_Usuario:"), ["@" + n for n in nombres],
                      lambda i: _hacer(nombres[i]), _("_Elegir")).present()

    def tw_cuenta_accion(self, accion: str):
        obj = self._tw_obj(("estado", "notificacion", "usuario"))
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("author_id"):
            if obj:
                self.avisar(_("No se sabe de qué cuenta se trata"))
            return
        textos = {"follow": _("Ahora sigues a @{u}"), "unfollow": _("Has dejado de seguir a @{u}"),
                  "mute": _("@{u} silenciada"), "unmute": _("Silencio quitado a @{u}"),
                  "block": _("@{u} bloqueada"), "unblock": _("@{u} desbloqueada")}

        def _hacer():
            def _fin(res, err):
                self.avisar(_("No se pudo: {e}").format(e=err) if err else textos[accion].format(u=x["usuario"]))
            x_service.accion_cuenta_async(x["account_id"], x["author_id"], accion, callback=_fin)
        if accion in ("block", "mute"):
            confirmar_async(self, (_("¿Bloquear a @{u}?") if accion == "block" else _("¿Silenciar a @{u}?")).format(
                u=x["usuario"]), _hacer, etiqueta_si=_("Sí"))
        else:
            _hacer()

    def tw_quien(self, tipo: str):
        obj = self._tw_obj(TIPOS_CON_PUBLICACION)
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("tweet_id"):
            return
        titulo = _("Quién ha dado me gusta") if tipo == "likes" else _("Quién ha hecho retweet")
        self.avisar(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            if not res:
                self.avisar(_("Nadie todavía"))
                return
            from .dialogos_x import DialogoPerfilX
            DialogoElegir(self, titulo, _("_Cuenta ({n}):").format(n=len(res)),
                          [f"{a['name']} (@{a['screen_name']})" for a in res],
                          lambda i: DialogoPerfilX(self, x["account_id"], res[i]["screen_name"]).present(),
                          _("Ver _perfil")).present()
        x_service.obtener_interacciones_async(x["account_id"], x["tweet_id"], tipo, callback=_fin)

    def tw_buscar(self):
        acc = self._tw_cuenta()
        if acc is None:
            return

        def _buscar(texto):
            self._abrir_buffer(acc, f"search:{texto}", texto, servicio="x")
        DialogoTexto(self, _("Buscar en X"), _("_Buscar:"), _buscar,
                     _("Busca publicaciones. Puedes usar #hashtags, from:usuario y otros operadores de X."),
                     _("_Buscar")).present()

    def tw_buscar_usuarios(self):
        acc = self._tw_cuenta()
        if acc is None:
            return

        def _con(texto):
            self.avisar(_("Buscando cuentas…"))

            def _fin(res, err):
                if err:
                    self._tw_error(err, acc, lambda: _con(texto))
                    return
                if not res:
                    self.avisar(_("No se encontraron cuentas"))
                    return
                from .dialogos_x import DialogoPerfilX
                DialogoElegir(self, _("Cuentas de X"), _("_Cuenta:"),
                              [_("{n} (@{u}), {s} seguidores. {b}").format(n=u.get("name", ""), u=u.get("screen_name", ""),
                                                                           s=u.get("followers", 0),
                                                                           b=(u.get("description") or "")[:100]) for u in res],
                              lambda i: DialogoPerfilX(self, acc, res[i]["screen_name"]).present(),
                              _("Ver _perfil")).present()
            x_service.buscar_usuarios_async(acc, texto, callback=_fin)
        DialogoTexto(self, _("Buscar cuentas"), _("_Nombre o @usuario:"), _con, aceptar=_("_Buscar")).present()

    def tw_tendencias(self):
        acc = self._tw_cuenta()
        if acc is None:
            return
        self.avisar(_("Cargando tendencias…"))

        def _fin(res, err):
            if err:
                self._tw_error(err, acc, self.tw_tendencias)
                return
            if not res:
                self.avisar(_("No hay tendencias ahora"))
                return
            DialogoElegir(self, _("Tendencias de X"), _("_Tendencia:"),
                          [f"{t['name']} ({t['count']})" if t["count"] else t["name"] for t in res],
                          lambda i: self._abrir_buffer(acc, f"search:{res[i]['name']}", res[i]["name"], servicio="x"),
                          _("_Buscar")).present()
        x_service.obtener_tendencias_async(acc, callback=_fin)

    def tw_tendencias_lugar(self):
        """Tendencias de un país o una ciudad: se elige el lugar, luego la tendencia, que se abre como búsqueda."""
        acc = self._tw_cuenta()
        if acc is None:
            return
        self.avisar(_("Cargando los lugares…"))

        def _lugares(lugares, err):
            if err or not lugares:
                self._tw_error(err or _("no hay lugares"), acc, self.tw_tendencias_lugar, False,
                               _("No se pudieron cargar los lugares: {e}"))
                return
            nombres = [l["name"] if l["name"] == l["country"] or not l["country"] else f"{l['name']} ({l['country']})"
                       for l in lugares]

            def _lugar(i):
                self.avisar(_("Cargando tendencias de {l}…").format(l=nombres[i]))

                def _tendencias(res, err2):
                    if err2 or not res:
                        self.avisar(_("No hay tendencias para ese lugar"))
                        return
                    DialogoElegir(self, _("Tendencias en {l}").format(l=nombres[i]), _("_Tendencia:"),
                                  [f"{t['name']} ({t['count']})" if t.get("count") else t["name"] for t in res],
                                  lambda j: self._abrir_buffer(acc, f"search:{res[j]['name']}", res[j]["name"],
                                                               servicio="x"),
                                  _("_Buscar")).present()
                x_service.tendencias_lugar_async(acc, lugares[i]["woeid"], callback=_tendencias)
            DialogoElegir(self, _("Tendencias de un lugar"), _("_País o ciudad:"), nombres, _lugar, _("_Elegir")).present()
        x_service.lugares_tendencias_async(acc, callback=_lugares)

    def tw_abrir_lista(self):
        acc = self._tw_cuenta()
        if acc is None:
            return
        self.avisar(_("Leyendo tus listas…"))

        def _fin(listas, err):
            if err:
                self._tw_error(err, acc, self.tw_abrir_lista)
                return
            if not listas:
                self.avisar(_("No tienes listas en esta cuenta"))
                return
            DialogoElegir(self, _("Abrir una lista"), _("_Lista:"), [l["name"] for l in listas],
                          lambda i: self._abrir_buffer(acc, f"list:{listas[i]['list_id']}",
                                                       _("Lista: {n}").format(n=listas[i]["name"]), servicio="x"),
                          _("_Abrir")).present()
        x_service.obtener_listas_async(acc, callback=_fin)

    def tw_listas(self):
        acc = self._tw_cuenta()
        if acc is not None:
            from .dialogos_x_gestion import DialogoListasX
            DialogoListasX(self, acc).present()

    def tw_buscar_comunidades(self):
        acc = self._tw_cuenta()
        if acc is None:
            return

        def _buscar(texto):
            self.avisar(_("Buscando comunidades…"))

            def _fin(res, err):
                if err:
                    self._tw_error(err, acc, lambda: _buscar(texto))
                    return
                if not res:
                    self.avisar(_("No se encontraron comunidades"))
                    return
                from .dialogos_x_gestion import DialogoComunidadesX
                DialogoComunidadesX(self, acc, res, texto).present()
            x_service.buscar_comunidades_async(acc, texto, callback=_fin)
        DialogoTexto(self, _("Buscar comunidades"), _("_Buscar:"), _buscar, aceptar=_("_Buscar")).present()

    def tw_abrir_comunidad(self, account_id: int, community_id: str, nombre: str):
        self._abrir_buffer(account_id, f"community:{community_id}", nombre, servicio="x")

    def tw_salir_comunidad(self):
        n = self.nodo_actual
        if n is None or n.servicio != "x" or not n.timeline.startswith("community:"):
            self.avisar(_("Abre antes el buffer de una comunidad"))
            return
        cid = n.timeline.split(":", 1)[1]

        def _hacer():
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo: {e}").format(e=err))
                    return
                self.avisar(_("Has salido de la comunidad"))
            x_service.comunidad_async(n.account_id, cid, "leave", callback=_fin)
        confirmar_async(self, _("¿Salir de esta comunidad?"), _hacer, etiqueta_si=_("Salir"))

    def tw_anadir_a_lista(self):
        obj = self._tw_obj(("estado", "notificacion", "usuario"))
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("author_id"):
            return
        acc = x["account_id"]
        self.avisar(_("Leyendo tus listas…"))

        def _fin(listas, err):
            if err:
                self._tw_error(err, acc, self.tw_anadir_a_lista)
                return
            if not listas:
                self.avisar(_("No tienes listas en esta cuenta"))
                return

            def _elegida(i):
                def _hecho(res, err2):
                    self.avisar(_("No se pudo: {e}").format(e=err2) if err2
                                else _("@{u} añadido a {l}").format(u=x["usuario"], l=listas[i]["name"]))
                x_service.gestionar_miembro_lista_async(acc, listas[i]["list_id"], x["author_id"], True, callback=_hecho)
            DialogoElegir(self, _("Añadir a una lista"), _("_Lista:"), [l["name"] for l in listas], _elegida,
                          _("_Añadir")).present()
        x_service.obtener_listas_async(acc, callback=_fin)

    def tw_programados(self):
        acc = self._tw_cuenta()
        if acc is None:
            return
        self.avisar(_("Leyendo tus publicaciones programadas…"))

        def _fin(res, err):
            if err:
                self._tw_error(err, acc, self.tw_programados)
                return
            if not res:
                self.avisar(_("No hay publicaciones programadas"))
                return

            def _elegida(i):
                def _cancelar():
                    def _hecho(r, e):
                        self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Publicación programada cancelada"))
                    x_service.cancelar_programado_async(acc, res[i]["id"], callback=_hecho)
                confirmar_async(self, _("¿Cancelar la publicación programada?"), _cancelar, etiqueta_si=_("Cancelar"))
            DialogoElegir(self, _("Publicaciones programadas"), _("_Programada (elige una para cancelarla):"),
                          [f"{m['date']}: {m['text'][:120]}" for m in res], _elegida, _("_Cancelar esa")).present()
        x_service.obtener_programados_async(acc, callback=_fin)

    def tw_ocultar_buffers(self):
        """Mostrar u ocultar los buffers fijos de la cuenta (se guarda en x/hidden_buffers, como en Windows)."""
        ocultos = set(config.get("x", "hidden_buffers", []) or [])
        opciones = [(c, t) for c, t in datos_x.BUFFERS]
        etiquetas = [_(t) + (_(" (oculto)") if c in ocultos else "") for c, t in opciones]

        def _alternar(i):
            clave = opciones[i][0]
            if clave in ocultos:
                ocultos.discard(clave)
            else:
                ocultos.add(clave)
            config.set("x", "hidden_buffers", sorted(ocultos))
            self.cargar_arbol()
            self.avisar(_("Buffer {b} {e}").format(b=_(opciones[i][1]), e=_("oculto") if clave in ocultos else _("visible")))
        DialogoElegir(self, _("Mostrar u ocultar buffers"), _("_Buffer:"), etiquetas, _alternar, _("_Cambiar")).present()

    # ------------------------------------------------------------------ cuenta
    def tw_reimportar(self):
        n = self.nodo_actual
        if n is None or n.servicio != "x" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de X"))
            return
        from .dialogos_x import DialogoCuentaX
        acc = n.account_id
        DialogoCuentaX(self, lambda _id: self.avisar(_("Ya puedes usar la cuenta")), account_id=acc).present()

    def tw_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "x" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de X"))
            return
        acc = n.account_id
        nombre = next((nm for i, nm in datos_x.cuentas() if i == acc), "")

        def _hacer():
            x_service.detener_todos_streaming_dm()
            x_service.eliminar_cuenta(acc)
            self.cargar_arbol(conservar=False)
            self.avisar(_("Cuenta eliminada"))
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ mensajes directos
    def _tw_screen_dm(self, account_id: int, uid: str) -> str:
        for c in datos_x.dm_guardados(account_id):
            if str(c.get("id")) == str(uid):
                return str(c.get("display") or "").lstrip("@")
        return ""

    def tw_mensaje_a(self, account_id: int, uid: str, screen: str):
        """Abre la conversación con una cuenta como buffer (Ctrl+W la cierra)."""
        self._abrir_buffer(account_id, f"dm:{uid}", f"@{screen.lstrip('@')}", servicio="x")

    def tw_mensaje_a_autor(self):
        obj = self._tw_obj(("estado", "notificacion", "usuario"))
        x = obj.fila.extra if obj else {}
        if not obj or not x.get("author_id"):
            if obj:
                self.avisar(_("No se sabe de qué cuenta se trata"))
            return
        self.tw_mensaje_a(x["account_id"], x["author_id"], x["usuario"])

    def tw_mensaje_nuevo(self):
        acc = self._tw_cuenta()
        if acc is None:
            return

        def _con(texto):
            screen = texto.lstrip("@")

            def _fin(res, err):
                if err or not res:
                    self.avisar(_("No se encontró a @{u}").format(u=screen) if not err
                                else _("No se pudo: {e}").format(e=err))
                    return
                self.tw_mensaje_a(acc, res["user_id"], res["screen_name"])
            x_service.resolver_usuario_async(acc, screen, callback=_fin)
        DialogoTexto(self, _("Mensaje directo nuevo"), _("_Usuario de X (con o sin @):"), _con,
                     aceptar=_("_Abrir")).present()

    def _tw_escribir_mensaje(self, account_id: int, uid: str, titulo: str):
        from .dialogos_x import DialogoMensajeX
        DialogoMensajeX(self, account_id, uid, titulo, lambda: self._tw_refrescar_dm(account_id, uid)).present()

    def _tw_refrescar_dm(self, account_id: int, uid: str):
        for n in self._tw_nodos_cuenta(account_id):
            if n.timeline == f"dm:{uid}":
                self.tw_actualizar_nodo(n, silencioso=True)

    def _tw_convo_de(self, nodo):
        mi_id = str(x_service._yo(nodo.account_id).get("id", "") or "")
        return x_service._convo_id_de(mi_id, nodo.timeline[3:])

    def _tw_dm_arrancar(self, nodo):
        """Escucha en vivo la conversación abierta (X avisa por streaming de mensajes nuevos y de que escriben)."""
        if nodo is not self.nodo_actual:
            return
        if not getattr(self, "_tw_dm_suscrito", False):
            self._tw_dm_suscrito = True
            event_bus.subscribe("x_dm_update", self._tw_dm_ev)
            event_bus.subscribe("x_dm_typing", self._tw_dm_escribiendo_ev)
        x_service.iniciar_streaming_dm(nodo.account_id, self._tw_convo_de(nodo))

    def _tw_dm_ev(self, account_id, conversation_id):
        llamar_en_ui(self._tw_dm_novedad, account_id, conversation_id)

    def _tw_dm_novedad(self, account_id, conversation_id):
        n = self.nodo_actual
        if n is not None and n.servicio == "x" and n.account_id == account_id and n.timeline.startswith("dm:") \
                and self._tw_convo_de(n) == conversation_id:
            self.tw_actualizar_nodo(n, silencioso=True)

    def _tw_dm_escribiendo_ev(self, account_id, conversation_id):
        llamar_en_ui(self._tw_dm_escribiendo, account_id, conversation_id)

    def _tw_dm_escribiendo(self, account_id, conversation_id):
        n = self.nodo_actual
        if n is not None and n.servicio == "x" and n.account_id == account_id and n.timeline.startswith("dm:") \
                and self._tw_convo_de(n) == conversation_id:
            self.estado.set_text(_("{t}: está escribiendo…").format(t=n.titulo))
            # X repite el aviso mientras se escribe: un solo sonido cada 8 segundos por conversación.
            ahora = time.monotonic()
            if ahora - self._tw_ult_escribiendo.get(conversation_id, 0.0) >= 8.0:
                self._tw_ult_escribiendo[conversation_id] = ahora
                event_bus.emit("sound_trigger", "x_typing")

    def _tw_anunciar_dm(self, nuevos):
        recibidos = [f for f in nuevos if not f.extra.get("propio")]
        if recibidos:
            speak(recibidos[-1].titulo, interrupt=False)
            event_bus.emit("sound_trigger", "notification")

    def _tw_al_cambiar_nodo(self, anterior, nuevo):
        """Al dejar una conversación se deja de escucharla en vivo."""
        if anterior is None or anterior is nuevo or anterior.servicio != "x" or not anterior.timeline.startswith("dm:"):
            return
        x_service.detener_streaming_dm(self._tw_convo_de(anterior))

    def detener_tiempo_real_x(self):
        if getattr(self, "_tw_dm_suscrito", False):
            self._tw_dm_suscrito = False
            event_bus.unsubscribe("x_dm_update", self._tw_dm_ev)
            event_bus.unsubscribe("x_dm_typing", self._tw_dm_escribiendo_ev)
        try:
            x_service.detener_todos_streaming_dm()
        except Exception:
            log.debug("No se pudieron detener los streamings de X", exc_info=True)

    def tw_reaccionar(self):
        obj = self._tw_obj(("mensaje",))
        x = obj.fila.extra if obj else {}
        if not obj:
            return
        emojis = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👎", "🔥", "🎉", "🤔"]

        def _hacer(i):
            def _fin(res, err):
                self.avisar(_("No se pudo: {e}").format(e=err) if err else _("Reacción enviada"))
            x_service.reaccionar_dm_async(x["account_id"], x["message_id"], x["convo_id"], emojis[i], callback=_fin)
        DialogoElegir(self, _("Reaccionar al mensaje"), _("_Reacción:"), emojis, _hacer, _("_Reaccionar")).present()

    def _tw_borrar_mensaje(self, obj):
        x = obj.fila.extra

        def _borrar():
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo borrar: {e}").format(e=err))
                    return
                self._tw_quitar_fila(obj)
                self.avisar(_("Mensaje borrado"))
            x_service.borrar_dm_async(x["account_id"], x["message_id"], callback=_fin)
        confirmar_async(self, _("¿Borrar este mensaje?"), _borrar, etiqueta_si=_("Borrar"))

    # ------------------------------------------------------------------ menú contextual y acciones
    def tw_menu_contextual(self, obj):
        """Lista (etiqueta, acción) del menú contextual de una fila de X."""
        x = obj.fila.extra
        if x.get("tipo") == "mensaje":
            return ((_("Responder"), "win.responder"), (_("Reaccionar…"), "win.tw-reaccionar"),
                    (_("Traducir"), "win.traducir"), (_("Borrar el mensaje"), "win.papelera"))
        if x.get("tipo") == "usuario":
            return ((_("Perfil"), "win.perfil"), (_("Publicaciones de la cuenta"), "win.cronologia-usuario"),
                    (_("Seguir a la cuenta"), "win.tw-seguir"), (_("Dejar de seguir a la cuenta"), "win.tw-dejar-seguir"),
                    (_("Silenciar la cuenta"), "win.tw-silenciar"), (_("Bloquear la cuenta"), "win.tw-bloquear"),
                    (_("Escribirle un mensaje directo"), "win.tw-mensaje-autor"), (_("Abrir en el navegador"), "win.navegador"))
        lista = [(_("Responder"), "win.responder"), (_("Citar"), "win.citar"),
                 (_("Me gusta / quitar me gusta"), "win.me-gusta"), (_("Retweet / deshacer retweet"), "win.impulsar"),
                 (_("Marcador / quitar marcador"), "win.marcador"), (_("Traducir"), "win.traducir"),
                 (_("Resumir con IA"), "win.resumir-ia"), (_("Describir la imagen con IA"), "win.describir-imagen"),
                 (_("Extraer el texto de la imagen"), "win.ocr-imagen"),
                 (_("Ver la conversación"), "win.conversacion"), (_("Votar en la encuesta"), "win.votar"),
                 (_("Quién ha dado me gusta"), "win.tw-quien-gusta"), (_("Quién ha hecho retweet"), "win.tw-quien-rt"),
                 (_("Perfil del autor"), "win.perfil"), (_("Publicaciones del autor"), "win.cronologia-usuario"),
                 (_("Perfil de un usuario mencionado…"), "win.tw-perfil-mencionado"),
                 (_("Cronología de un usuario mencionado…"), "win.tw-cronologia-mencionado"),
                 (_("Seguir al autor"), "win.tw-seguir"), (_("Añadir al autor a una lista…"), "win.tw-anadir-lista"),
                 (_("Silenciar al autor"), "win.tw-silenciar"), (_("Bloquear al autor"), "win.tw-bloquear"),
                 (_("Mensaje directo al autor"), "win.tw-mensaje-autor"),
                 (_("Abrir en el navegador"), "win.navegador"), (_("Copiar el enlace"), "win.copiar-enlace")]
        if x.get("propio") and x.get("tipo") == "estado":
            lista.append((_("Borrar mi publicación"), "win.papelera"))
        return tuple(lista)

    def _registrar_acciones_x(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("tw-seguir", lambda: self.tw_cuenta_accion("follow"))
        a("tw-dejar-seguir", lambda: self.tw_cuenta_accion("unfollow"))
        a("tw-silenciar", lambda: self.tw_cuenta_accion("mute"))
        a("tw-quitar-silencio", lambda: self.tw_cuenta_accion("unmute"))
        a("tw-bloquear", lambda: self.tw_cuenta_accion("block"))
        a("tw-desbloquear", lambda: self.tw_cuenta_accion("unblock"))
        a("tw-quien-gusta", lambda: self.tw_quien("likes"))
        a("tw-quien-rt", lambda: self.tw_quien("retweets"))
        a("tw-perfil-mencionado", lambda: self.tw_usuario_mencionado("perfil"))
        a("tw-cronologia-mencionado", lambda: self.tw_usuario_mencionado("cronologia"))
        a("tw-cronologia-de", self.tw_cronologia_de)
        a("tw-perfil-de", self.tw_perfil_de)
        a("tw-buscar-usuarios", self.tw_buscar_usuarios)
        a("tw-tendencias", self.tw_tendencias)
        a("tw-tendencias-lugar", self.tw_tendencias_lugar)
        a("tw-abrir-lista", self.tw_abrir_lista)
        a("tw-listas", self.tw_listas)
        a("tw-buscar-comunidades", self.tw_buscar_comunidades)
        a("tw-salir-comunidad", self.tw_salir_comunidad)
        a("tw-anadir-lista", self.tw_anadir_a_lista)
        a("tw-programados", self.tw_programados)
        a("tw-ocultar-buffers", self.tw_ocultar_buffers)
        a("tw-reimportar", self.tw_reimportar)
        a("tw-mensaje-autor", self.tw_mensaje_a_autor)
        a("tw-mensaje-nuevo", self.tw_mensaje_nuevo)
        a("tw-reaccionar", self.tw_reaccionar)
        a("tw-buscar", self.tw_buscar)
