# -*- coding: utf-8 -*-
"""PeerTube en la ventana principal (mixin de VentanaPrincipal).

Las acciones comparten teclas con las demás redes (Ctrl+B me gusta, Ctrl+R comentar, Ctrl+N subir...): los manejadores de
ventana.py llaman aquí cuando el elemento enfocado es de PeerTube. Todo lo de este archivo lleva el prefijo `pt_`.

Intro reproduce el vídeo (suena el audio, con el reproductor de la aplicación). PeerTube no tiene tiempo real: se descarga al
entrar en cada sección y se sondea la abierta cada `peertube/update_interval` segundos."""

import logging
import os
import time
import webbrowser

from app.core.config_manager import config
from app.core.database import db
from app.core.plataforma import llamar_en_ui
from app.core.speech import copiar_al_portapapeles, speak
from app.core.task_manager import TareaDuplicadaError
from app.services.peertube import peertube_service

from . import datos_peertube
from .dialogos import DialogoElegir, DialogoTexto, confirmar_async, ejecutar

log = logging.getLogger(__name__)

TIPOS_VIDEO = ("video",)
CALIDADES = [("auto", "Automática (menor ancho de banda)"), ("1080", "1080p"), ("720", "720p"), ("480", "480p"),
             ("360", "360p"), ("240", "240p")]


class _PeerTubeMixin:
    _pt_votos: dict = {}
    _pt_sonando = ""

    # ------------------------------------------------------------------ contexto
    def _pt_ctx(self) -> bool:
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "peertube"
        n = self.nodo_actual
        return n is not None and n.servicio == "peertube"

    def _pt_obj(self, tipos=TIPOS_VIDEO):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "peertube":
            self.avisar(_("Selecciona un elemento de PeerTube"))
            return None
        if obj.fila.extra.get("tipo") not in tipos:
            self.avisar(_("Esta acción no se puede hacer con este elemento de PeerTube"))
            return None
        return obj

    def _pt_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "peertube" and n.account_id:
            return n.account_id
        cuentas = datos_peertube.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de PeerTube"))
        return None

    def _pt_error(self, err, prefijo: str = None, silencioso: bool = False):
        if isinstance(err, TareaDuplicadaError):
            return
        if not silencioso:
            self.avisar((prefijo or _("Error: {e}")).format(e=err))

    def _pt_hacer(self, fn, ok=None, error=None, despues=None):
        def _fin(res, err):
            if err:
                self._pt_error(err, error or _("No se pudo completar la acción: {e}"))
                return
            if despues:
                despues(res)
            if ok:
                self.avisar(ok)
        ejecutar(fn, _fin)

    def _pt_repintar(self, conservar=None):
        obj = self._fila_actual()
        self.cargar_lista(conservar_id=conservar or (obj.fila.id if obj else None), incremental=True)

    # ------------------------------------------------------------------ actualizar y cargar más
    def pt_actualizar_nodo(self, nodo, silencioso: bool = False):
        sec, acc = nodo.timeline, nodo.account_id
        clave = (acc, sec)
        primera = clave not in self._cargados
        antes = {self.almacen_lista.get_item(i).fila.id for i in range(self.almacen_lista.get_n_items())} \
            if nodo is self.nodo_actual else set()

        def _fin(res, err):
            if err:
                self._pt_error(err, _("No se pudo actualizar PeerTube: {e}"), silencioso)
                return
            if sec in ("notificaciones", "listas", "mis_canales"):
                datos_peertube.guardar_cache(acc, sec, res)
            self._cargados.add(clave)
            if nodo is not self.nodo_actual or self.busqueda:
                return

            def _repintado(filas):
                nuevos = [f for f in filas if f.id not in antes]
                if primera or not nuevos:
                    if not silencioso:
                        self.estado.set_text(_("{t}: actualizado").format(t=nodo.titulo))
                    return
                self.avisar(_("{n} nuevos").format(n=len(nuevos)), interrumpir=False)
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True, despues=_repintado)
        try:
            if datos_peertube.es_video(sec):
                peertube_service.cargar_seccion_async(acc, sec, cargar_mas=False, callback=_fin)
            elif sec == "notificaciones":
                ejecutar(lambda: peertube_service.listar_notificaciones(acc), _fin)
            elif sec == "listas":
                ejecutar(lambda: peertube_service.mis_listas(acc), _fin)
            elif sec == "mis_canales":
                ejecutar(lambda: peertube_service.mis_canales(acc), _fin)
        except TareaDuplicadaError:
            pass

    def pt_cargar_mas(self):
        n = self.nodo_actual
        if n is None or not n.listable or not datos_peertube.es_cronologico(n.timeline):
            self.avisar(_("Aquí no se pueden cargar vídeos más antiguos"))
            return
        self.avisar(_("Cargando más vídeos…"))
        actual = self._fila_actual()

        def _fin(res, err):
            if err:
                self._pt_error(err, _("No se pudieron cargar más vídeos: {e}"))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            self.avisar(_("{n} vídeos más").format(n=res) if res else _("No hay más vídeos"))
        try:
            peertube_service.cargar_seccion_async(n.account_id, n.timeline, cargar_mas=True, callback=_fin)
        except TareaDuplicadaError:
            pass

    def _sondeo_peertube(self):
        n = self.nodo_actual
        if n is None or n.servicio != "peertube" or not n.listable or (n.account_id, n.timeline) not in self._cargados:
            return
        if not (datos_peertube.es_video(n.timeline) or n.timeline == "notificaciones"):
            return
        minimo = max(60, int(config.get("peertube", "update_interval", 180) or 180))
        ahora = time.monotonic()
        if ahora - getattr(self, "_pt_ultimo_sondeo", 0.0) < minimo:
            return
        self._pt_ultimo_sondeo = ahora
        self.pt_actualizar_nodo(n, silencioso=True)

    def pt_tras_cambiar(self, seccion: str = None):
        """Tras subir, importar o crear algo: se vuelve a leer la sección abierta."""
        n = self.nodo_actual
        if n is not None and n.servicio == "peertube" and n.listable and (seccion is None or n.timeline == seccion):
            self.pt_actualizar_nodo(n, silencioso=True)

    # ------------------------------------------------------------------ reproducir
    def pt_reproducir(self, uuid: str = None):
        obj = self._fila_actual()
        acc = self._pt_cuenta()
        if acc is None:
            return
        if uuid is None:
            if obj is None or obj.fila.servicio != "peertube" or not obj.fila.extra.get("uuid"):
                self.avisar(_("Selecciona un vídeo"))
                return
            uuid = obj.fila.extra["uuid"]
        self.avisar(_("Preparando la reproducción…"))

        def _preparar():
            import json
            det = peertube_service.detalle_video(acc, uuid)
            fuentes = json.loads(det.get("files_json") or "[]")
            directo = bool(det.get("is_live"))
            fuente = peertube_service.seleccionar_fuente(fuentes, es_directo=directo)
            url = ""
            if fuente:
                url = fuente["url"]
                if fuente.get("es_hls"):
                    url = peertube_service.resolver_variante_hls(acc, url) or url
            return {"det": det, "url": url, "fuente": fuente, "directo": directo}

        def _fin(res, err):
            if err:
                self._pt_error(err, _("No se pudo preparar el vídeo: {e}"))
                return
            if not res or not res.get("url") or not res.get("fuente"):
                self.avisar(_("Este vídeo no tiene una fuente de audio reproducible disponible"))
                return
            self._pt_lanzar(acc, uuid, res)
        ejecutar(_preparar, _fin)

    def _pt_lanzar(self, acc: int, uuid: str, res: dict):
        from app.services.media_player.player2_service import KIND_RADIO, KIND_YOUTUBE
        player = self._player()
        titulo = res["det"].get("name") or _("Vídeo de PeerTube")
        kind = KIND_RADIO if res["directo"] else KIND_YOUTUBE
        reanudar = (not res["directo"]) and bool(config.get("peertube", "save_position", True))
        url = res["url"]
        if res["fuente"].get("necesita_ffmpeg"):
            self.avisar(_("Este formato puede no reproducirse; intentándolo…"))

        def _sonando(ok):
            if ok:
                self._pt_sonando = uuid
                llamar_en_ui(self.avisar, _("Reproduciendo: {t}").format(t=titulo))
                return
            try:
                from app.services.peertube.peertube_stream import stream_peertube
                proxy = stream_peertube.url_para(url)
            except Exception:
                llamar_en_ui(self.avisar, _("No se pudo reproducir el vídeo"))
                return

            def _sonando2(ok2):
                if ok2:
                    self._pt_sonando = uuid
                llamar_en_ui(self.avisar, _("Reproduciendo: {t}").format(t=titulo) if ok2
                             else _("No se pudo reproducir el vídeo (formato no compatible)"))
            player.cargar_y_reproducir(proxy, titulo, kind, resume=reanudar, callback=_sonando2)
        player.cargar_y_reproducir(url, titulo, kind, resume=reanudar, callback=_sonando)
        ejecutar(lambda: peertube_service.registrar_vista(acc, uuid, 0), lambda r, e: None)

    def pt_reproducir_f2(self) -> bool:
        """F2 sobre un vídeo de PeerTube: pausa o reanuda si ya suena, y si no lo reproduce. False si no es de PeerTube."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "peertube" or obj.fila.extra.get("tipo") not in ("video", "notificacion"):
            return False
        p = self._player()
        if p.esta_activo() and self._pt_sonando and self._pt_sonando == obj.fila.extra.get("uuid"):
            p.alternar_pausa()
            self.avisar(_("Pausado") if p.backend.obtener_estado() == "pausado" else _("Reproduciendo"))
        else:
            self.pt_reproducir()
        return True

    def pt_abrir_fila(self, obj=None):
        """Intro: reproduce un vídeo, abre una lista o un canal, o reproduce el vídeo de una notificación."""
        obj = obj or self._fila_actual()
        if obj is None or obj.fila.servicio != "peertube":
            return
        x = obj.fila.extra
        if x["tipo"] == "video":
            self.pt_reproducir()
        elif x["tipo"] == "notificacion":
            if x.get("uuid"):
                self.pt_reproducir(x["uuid"])
            else:
                self.avisar(_("Esta notificación no tiene vídeo"))
        elif x["tipo"] == "lista":
            self._abrir_buffer(x["account_id"], f"playlist:{x['lista']['id']}",
                               datos_peertube.titulo_buffer("playlist:x", x["lista"].get("display", "")), servicio="peertube")
        elif x["tipo"] == "canal":
            self._abrir_buffer(x["account_id"], f"channel:{x['canal']['handle']}",
                               datos_peertube.titulo_buffer("channel:x", x["canal"].get("display", "")), servicio="peertube")

    # ------------------------------------------------------------------ valorar, suscribirse y comentar
    def _pt_votar(self, valoracion: str):
        obj = self._pt_obj()
        if not obj:
            return
        x = obj.fila.extra
        if self._pt_votos.get(x["uuid"]) == valoracion:
            valoracion = "none"
        self._pt_votos[x["uuid"]] = valoracion
        mensajes = {"like": _("Me gusta"), "dislike": _("No me gusta"), "none": _("Voto retirado")}
        self._pt_hacer(lambda: peertube_service.votar(x["account_id"], x["uuid"], valoracion), mensajes[valoracion],
                       _("No se pudo registrar tu voto: {e}"))

    def pt_me_gusta(self):
        self._pt_votar("like")

    def pt_no_me_gusta(self):
        self._pt_votar("dislike")

    def pt_suscribir(self, suscribir: bool = True):
        obj = self._pt_obj()
        if not obj:
            return
        handle = datos_peertube.handle_canal(obj.fila)
        if not handle:
            self.avisar(_("No se pudo identificar el canal"))
            return
        acc = obj.fila.extra["account_id"]
        if suscribir:
            self._pt_hacer(lambda: peertube_service.suscribirse(acc, handle), _("Suscrito al canal {c}").format(c=handle))
        else:
            self._pt_hacer(lambda: peertube_service.cancelar_suscripcion(acc, handle), _("Suscripción cancelada"))

    def pt_comentarios(self):
        obj = self._pt_obj()
        if obj:
            from .dialogos_peertube import DialogoComentariosPT
            DialogoComentariosPT(self, obj.fila.extra["account_id"], obj.fila.extra["uuid"],
                                 obj.fila.extra.get("nombre", "")).present()

    def pt_comentar(self):
        obj = self._pt_obj()
        if not obj:
            return
        x = obj.fila.extra
        from .dialogos_reddit import DialogoTextoLibre
        DialogoTextoLibre(self, _("Comentar"), _("_Comentario:"), lambda t: self._pt_hacer(
            lambda: peertube_service.publicar_comentario(x["account_id"], x["uuid"], t), _("Comentario publicado")),
            aceptar=_("_Publicar")).present()

    # ------------------------------------------------------------------ detalle, subtítulos y calidad
    def pt_detalles(self):
        obj = self._pt_obj()
        if not obj:
            return
        x = obj.fila.extra
        self.avisar(_("Cargando el vídeo…"))

        def _mostrar(det):
            from app.ui.ui_helpers import formatear_duracion_legible
            lineas = [det.get("name", "")]
            canal = det.get("channel_display") or det.get("channel_name")
            if canal:
                lineas.append(_("Canal: {0}").format(canal))
            if not det.get("is_live") and formatear_duracion_legible(det.get("duration")):
                lineas.append(_("Duración: {0}").format(formatear_duracion_legible(det.get("duration"))))
            lineas.append(_("{0} visualizaciones, {1} me gusta").format(det.get("views", 0), det.get("likes", 0)))
            if det.get("description"):
                lineas += ["", det["description"]]
            from .dialogos_mastodon_extra import DialogoTextoLargo
            DialogoTextoLargo(self, det.get("name") or _("Detalle del vídeo"), "\n".join(lineas)).present()
        self._pt_hacer(lambda: peertube_service.detalle_video(x["account_id"], x["uuid"]), None,
                       _("No se pudo cargar el vídeo: {e}"), _mostrar)

    def pt_subtitulos(self):
        obj = self._pt_obj()
        if not obj:
            return
        x = obj.fila.extra
        self.avisar(_("Buscando subtítulos…"))

        def _elegir(subs):
            if not subs:
                self.avisar(_("Este vídeo no tiene subtítulos"))
                return
            etiquetas = [(s.get("etiqueta") or s.get("idioma") or _("Desconocido")) + (_(" (automáticos)") if s.get("automatico") else "")
                         for s in subs]

            def _uno(i):
                s = subs[i]

                def _texto(texto):
                    if not texto:
                        self.avisar(_("No se pudo obtener el texto de los subtítulos"))
                        return
                    from .dialogos_mastodon_extra import DialogoTextoLargo
                    DialogoTextoLargo(self, _("Subtítulos ({0})").format(s.get("etiqueta") or s.get("idioma") or ""), texto).present()
                self.avisar(_("Descargando subtítulos…"))
                self._pt_hacer(lambda: peertube_service.descargar_subtitulo(x["account_id"], s.get("path")), None,
                               _("No se pudieron descargar los subtítulos: {e}"), _texto)
            d = DialogoElegir(self, _("Subtítulos"), _("_Idioma:"), etiquetas, _uno, _("_Ver"))
            pref = str(config.get("peertube", "preferred_subtitle_lang", "es") or "").lower()
            d.combo.set_selected(next((i for i, s in enumerate(subs) if str(s.get("idioma") or "").lower() == pref), 0))
            d.present()
        self._pt_hacer(lambda: peertube_service.listar_subtitulos(x["account_id"], x["uuid"]), None,
                       _("No se pudieron cargar los subtítulos: {e}"), _elegir)

    def pt_calidad(self):
        actual = str(config.get("peertube", "preferred_resolution", "auto"))

        def _guardar(i):
            config.set("peertube", "preferred_resolution", CALIDADES[i][0])
            self.avisar(_("Calidad guardada"))
        d = DialogoElegir(self, _("Calidad de vídeo"), _("_Calidad preferida de reproducción:"), [_(n) for _c, n in CALIDADES],
                          _guardar, _("_Guardar"))
        d.combo.set_selected(next((i for i, (c, _n) in enumerate(CALIDADES) if c == actual), 0))
        d.present()

    # ------------------------------------------------------------------ biblioteca
    def pt_ver_mas_tarde(self):
        obj = self._pt_obj()
        if obj and obj.fila.extra.get("video_id"):
            x = obj.fila.extra
            self._pt_hacer(lambda: peertube_service.añadir_ver_mas_tarde(x["account_id"], x["video_id"]),
                           _("Añadido a «ver más tarde»"))

    def pt_anadir_a_lista(self):
        obj = self._pt_obj()
        if not obj or not obj.fila.extra.get("video_id"):
            return
        x = obj.fila.extra
        self.avisar(_("Cargando tus listas…"))

        def _elegir(listas):
            elegibles = [lst for lst in (listas or []) if not lst.get("es_ver_mas_tarde")]
            if not elegibles:
                self.avisar(_("No tienes listas. Crea una primero"))
                return
            DialogoElegir(self, _("Añadir a una lista"), _("_Lista:"), [lst["display"] for lst in elegibles], lambda i: self._pt_hacer(
                lambda: peertube_service.añadir_a_lista(x["account_id"], elegibles[i]["id"], x["video_id"]),
                _("Añadido a la lista")), _("_Añadir")).present()
        self._pt_hacer(lambda: peertube_service.mis_listas(x["account_id"]), None,
                       _("No se pudieron cargar las listas: {e}"), _elegir)

    def pt_descargar(self):
        obj = self._pt_obj()
        if not obj:
            return
        x = obj.fila.extra
        carpeta = config.get("peertube", "download_dir", "") or ""

        def _bajar(ruta_carpeta):
            self.avisar(_("Descargando…"))
            hitos = self._pt_hitos(_("Descarga"))

            def _fin(ruta, err):
                if err:
                    self._pt_error(err, _("No se pudo descargar: {e}"))
                else:
                    self.avisar(_("Descargado: {f}").format(f=os.path.basename(ruta)))
            ejecutar(lambda: peertube_service.descargar_video(x["account_id"], x["uuid"], ruta_carpeta, progreso=hitos), _fin)
        if carpeta and os.path.isdir(carpeta):
            return _bajar(carpeta)
        from gi.repository import Gtk
        dlg = Gtk.FileDialog(title=_("Carpeta de descarga"))

        def _fin_dlg(origen, res):
            try:
                _bajar(origen.select_folder_finish(res).get_path())
            except Exception:
                pass
        dlg.select_folder(self, None, _fin_dlg)

    def _pt_hitos(self, etiqueta: str):
        """Progreso que solo habla en múltiplos del 20 %."""
        estado = {"ultimo": -1}

        def _p(hecho, total):
            if not total:
                return
            pct = int(hecho * 100 / total)
            if pct >= estado["ultimo"] + 20 or pct >= 100:
                estado["ultimo"] = pct - (pct % 20)
                llamar_en_ui(speak, _("{0}: {1} %").format(etiqueta, pct), False)
        return _p

    def pt_subir(self):
        acc = self._pt_cuenta()
        if acc is None:
            return
        from .dialogos_peertube import DialogoSubirPT

        def _subir(d):
            self.avisar(_("Subiendo vídeo…"))
            hitos = self._pt_hitos(_("Subida"))

            def _fin(res, err):
                if err:
                    self._pt_error(err, _("No se pudo subir el vídeo: {e}"))
                else:
                    self.avisar(_("Vídeo subido. Puede tardar en procesarse en el servidor"))
                    self.pt_tras_cambiar("mis_videos")
            ejecutar(lambda: peertube_service.subir_video_reanudable(
                acc, d["ruta"], channel_id=d["channel_id"], name=d["name"], privacy=d["privacy"],
                description=d.get("description"), tags=d.get("tags"), nsfw=d.get("nsfw"), progreso=hitos), _fin)
        DialogoSubirPT(self, acc, _subir).present()

    def pt_importar(self):
        acc = self._pt_cuenta()
        if acc is None:
            return
        from .dialogos_peertube import DialogoImportarPT

        def _importar(d):
            self.avisar(_("Iniciando la importación…"))

            def hacer():
                f = peertube_service.importar_magnet if d["es_magnet"] else peertube_service.importar_url
                return f(acc, d["url"], channel_id=d["channel_id"], name=d["name"], privacy=d["privacy"])
            self._pt_hacer(hacer, _("Importación iniciada. El servidor la irá procesando"), _("No se pudo iniciar la importación: {e}"))
        DialogoImportarPT(self, acc, _importar).present()

    def _pt_mio(self, obj) -> bool:
        return bool(obj.fila.extra.get("propio"))

    def pt_editar(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "peertube":
            self.avisar(_("Selecciona un elemento de PeerTube"))
            return
        x = obj.fila.extra
        acc = x["account_id"]
        if x["tipo"] == "video":
            if not self._pt_mio(obj):
                self.avisar(_("Solo puedes editar tus propios vídeos"))
                return
            fila = db.query_one("SELECT name, description, privacy FROM peertube_videos WHERE id = ?", (obj.fila.id,))
            from .dialogos_peertube import DialogoEditarVideoPT
            DialogoEditarVideoPT(self, {"name": fila[0], "description": fila[1], "privacy": fila[2]} if fila else {},
                                 lambda d: self._pt_hacer(lambda: peertube_service.editar_video(
                                     acc, x["video_id"], name=d["name"], description=d["description"], privacy=d["privacy"]),
                                     _("Vídeo actualizado"), None, lambda r: self.pt_tras_cambiar())).present()
        elif x["tipo"] == "lista":
            from .dialogos_peertube import DialogoListaPT
            lista = x["lista"]
            DialogoListaPT(self, lambda nombre, desc, priv: self._pt_hacer(
                lambda: peertube_service.editar_lista(acc, lista["id"], displayName=nombre, description=desc or None, privacy=priv),
                _("Lista actualizada"), None, lambda r: self.pt_actualizar_nodo(self.nodo_actual, True)), lista).present()
        elif x["tipo"] == "canal":
            from .dialogos_peertube import DialogoCanalPT
            canal = x["canal"]
            DialogoCanalPT(self, lambda ident, nombre, desc: self._pt_hacer(
                lambda: peertube_service.editar_canal(acc, canal["handle"], displayName=nombre, description=desc or None),
                _("Canal actualizado"), None, lambda r: self.pt_actualizar_nodo(self.nodo_actual, True)), canal).present()
        else:
            self.avisar(_("Esta acción no se puede hacer con este elemento de PeerTube"))

    def pt_borrar(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "peertube":
            return
        x = obj.fila.extra
        acc, tipo = x["account_id"], x["tipo"]
        n = self.nodo_actual
        if tipo == "video" and n is not None and n.timeline == "historial":
            return self._pt_hacer(lambda: peertube_service.quitar_del_historial(acc, x["uuid"]), _("Quitado del historial"),
                                  None, lambda r: self._pt_quitar_fila(obj))
        if tipo == "video":
            if not self._pt_mio(obj):
                self.avisar(_("Solo puedes borrar tus propios vídeos"))
                return
            confirmar_async(self, _("¿Borrar tu vídeo «{0}»? No se puede deshacer.").format(x.get("nombre", "")), lambda: self._pt_hacer(
                lambda: peertube_service.borrar_video(acc, x["video_id"]), _("Vídeo borrado"), None,
                lambda r: self._pt_quitar_fila(obj)), etiqueta_si=_("Borrar"))
        elif tipo == "lista":
            lista = x["lista"]
            if lista.get("es_ver_mas_tarde"):
                self.avisar(_("La lista de ver más tarde no se puede borrar"))
                return
            confirmar_async(self, _("¿Borrar la lista «{0}»?").format(lista.get("display", "")), lambda: self._pt_hacer(
                lambda: peertube_service.borrar_lista(acc, lista["id"]), _("Lista borrada"), None,
                lambda r: self.pt_actualizar_nodo(self.nodo_actual, True)), etiqueta_si=_("Borrar"))
        elif tipo == "canal":
            canal = x["canal"]
            confirmar_async(self, _("¿Borrar el canal «{0}» y sus vídeos? No se puede deshacer.").format(canal.get("display", "")),
                            lambda: self._pt_hacer(lambda: peertube_service.borrar_canal(acc, canal["handle"]), _("Canal borrado"),
                                                   None, lambda r: self.pt_actualizar_nodo(self.nodo_actual, True)),
                            etiqueta_si=_("Borrar"))

    def _pt_quitar_fila(self, obj):
        for i in range(self.almacen_lista.get_n_items()):
            if self.almacen_lista.get_item(i) is obj:
                self.almacen_lista.remove(i)
                n = self.almacen_lista.get_n_items()
                if n:
                    self.seleccion_lista.set_selected(min(i, n - 1))
                break

    def pt_nuevo(self):
        n = self.nodo_actual
        acc = self._pt_cuenta()
        if acc is None:
            return
        if n is not None and n.timeline == "listas":
            from .dialogos_peertube import DialogoListaPT
            return DialogoListaPT(self, lambda nombre, desc, priv: self._pt_hacer(
                lambda: peertube_service.crear_lista(acc, nombre, privacy=priv, description=desc), _("Lista creada"), None,
                lambda r: self.pt_actualizar_nodo(self.nodo_actual, True))).present()
        if n is not None and n.timeline == "mis_canales":
            from .dialogos_peertube import DialogoCanalPT
            return DialogoCanalPT(self, lambda ident, nombre, desc: self._pt_hacer(
                lambda: peertube_service.crear_canal(acc, ident, nombre, description=desc), _("Canal creado"), None,
                lambda r: self.pt_actualizar_nodo(self.nodo_actual, True))).present()
        self.pt_subir()

    def pt_vaciar_historial(self):
        acc = self._pt_cuenta()
        if acc is not None:
            confirmar_async(self, _("¿Vaciar todo tu historial de vídeos vistos?"), lambda: self._pt_hacer(
                lambda: peertube_service.vaciar_historial(acc), _("Historial vaciado"), None, lambda r: self.pt_tras_cambiar()),
                etiqueta_si=_("Vaciar"))

    # ------------------------------------------------------------------ navegación y búsqueda
    def pt_abrir_canal(self):
        obj = self._pt_obj()
        if obj and datos_peertube.handle_canal(obj.fila):
            handle = datos_peertube.handle_canal(obj.fila)
            self._abrir_buffer(obj.fila.extra["account_id"], f"channel:{handle}",
                               datos_peertube.titulo_buffer("channel:x", obj.fila.feed_titulo or handle), servicio="peertube")

    def pt_abrir_cuenta(self):
        obj = self._pt_obj()
        if obj and datos_peertube.handle_cuenta(obj.fila):
            handle = datos_peertube.handle_cuenta(obj.fila)
            self._abrir_buffer(obj.fila.extra["account_id"], f"account:{handle}",
                               datos_peertube.titulo_buffer("account:x", handle), servicio="peertube")

    def pt_ir_canal(self):
        acc = self._pt_cuenta()
        if acc is None:
            return
        DialogoTexto(self, _("Ir a un canal"), _("_Canal (nombre@instancia):"),
                     lambda t: self._abrir_buffer(acc, f"channel:{t.strip().lstrip('@')}",
                                                  datos_peertube.titulo_buffer("channel:x", t.strip().lstrip("@")),
                                                  servicio="peertube"),
                     _("Por ejemplo canal@framatube.org. Para un canal de tu propia instancia basta el nombre."),
                     _("_Abrir")).present()

    def pt_buscar(self):
        acc = self._pt_cuenta()
        if acc is None:
            return
        DialogoTexto(self, _("Buscar en PeerTube"), _("_Buscar:"),
                     lambda t: self._abrir_buffer(acc, f"search:{t}", datos_peertube.titulo_buffer(f"search:{t}"),
                                                  servicio="peertube"),
                     _("Busca vídeos en toda la red PeerTube. La búsqueda queda guardada; ciérrala con Ctrl+W."),
                     _("_Buscar")).present()

    def pt_buscar_canales(self):
        acc = self._pt_cuenta()
        if acc is None:
            return

        def _buscar(texto):
            self.avisar(_("Buscando canales…"))

            def _res(res):
                if not res:
                    self.avisar(_("No se encontraron canales"))
                    return
                etiquetas = [_("{0} ({1}): {2} seguidores").format(c["display"], c["handle"], c["followers"]) for c in res]
                DialogoElegir(self, _("Resultados de canales"), _("_Canal:"), etiquetas, lambda i: self._abrir_buffer(
                    acc, f"channel:{res[i]['handle']}", datos_peertube.titulo_buffer("channel:x", res[i]["display"]),
                    servicio="peertube"), _("_Abrir")).present()
            self._pt_hacer(lambda: peertube_service.buscar_canales(acc, texto), None, _("Error en la búsqueda: {e}"), _res)
        DialogoTexto(self, _("Buscar canales"), _("_Nombre o tema:"), _buscar, aceptar=_("_Buscar")).present()

    def pt_info_canal(self):
        obj = self._pt_obj()
        if not obj:
            return
        handle = datos_peertube.handle_canal(obj.fila)
        if not handle:
            return

        def _mostrar(c):
            lineas = [c["display"], c["handle"], _("{0} seguidores").format(c["followers"])]
            if c.get("owner"):
                lineas.append(_("Propietario: {0}").format(c["owner"]))
            if c.get("description"):
                lineas += ["", c["description"]]
            if c.get("support"):
                lineas += ["", _("Apoyo: {0}").format(c["support"])]
            from .dialogos_mastodon_extra import DialogoTextoLargo
            DialogoTextoLargo(self, _("Canal {0}").format(c["display"]), "\n".join(lineas)).present()
        acc = obj.fila.extra["account_id"]
        self._pt_hacer(lambda: peertube_service.info_canal(acc, handle), None, _("No se pudo cargar el canal: {e}"), _mostrar)

    # ------------------------------------------------------------------ silencios y denuncias
    def pt_silenciar(self, servidor: bool = False):
        obj = self._pt_obj()
        if not obj:
            return
        x = obj.fila.extra
        if servidor:
            host = x.get("cuenta_host") or x.get("instancia")
            if not host:
                return
            confirmar_async(self, _("¿Silenciar toda la instancia {0}?").format(host), lambda: self._pt_hacer(
                lambda: peertube_service.silenciar_servidor(x["account_id"], host), _("Instancia silenciada")),
                etiqueta_si=_("Silenciar"))
        else:
            handle = datos_peertube.handle_cuenta(obj.fila)
            if not handle:
                return
            confirmar_async(self, _("¿Silenciar a {0}? No verás su contenido.").format(handle), lambda: self._pt_hacer(
                lambda: peertube_service.silenciar_cuenta(x["account_id"], handle), _("Cuenta silenciada")),
                etiqueta_si=_("Silenciar"))

    # ------------------------------------------------------------------ directos, estadísticas, moderación y administración
    def _pt_video_id(self):
        """(cuenta, id del vídeo) del vídeo enfocado, o None."""
        obj = self._pt_obj()
        if not obj:
            return None
        x = obj.fila.extra
        return x["account_id"], (x.get("video_id") or x.get("uuid"))

    def pt_crear_directo(self):
        acc = self._pt_cuenta()
        if acc is None:
            return
        from .dialogos_peertube_instancia import DialogoCrearDirecto, DialogoDatosEmision

        def _crear(d):
            def _tras(res):
                self.avisar(_("Directo creado. Estos son sus datos de emisión."))
                if res and res.get("id"):
                    ejecutar(lambda: peertube_service.info_directo(acc, res["id"]),
                             lambda info, err: DialogoDatosEmision(self, info).present() if info and not err else
                             self._pt_error(err, _("No se pudieron obtener los datos de emisión: {e}")))
            self._pt_hacer(lambda: peertube_service.crear_directo(
                acc, d["channel_id"], d["name"], privacy=d["privacy"], permanent_live=d["permanent_live"],
                latency_mode=d["latency_mode"], save_replay=d["save_replay"]), None, _("No se pudo crear el directo: {e}"), _tras)
        DialogoCrearDirecto(self, acc, _crear).present()

    def pt_datos_emision(self):
        v = self._pt_video_id()
        if v:
            from .dialogos_peertube_instancia import DialogoDatosEmision
            ejecutar(lambda: peertube_service.info_directo(*v),
                     lambda info, err: DialogoDatosEmision(self, info).present() if info and not err else
                     self._pt_error(err, _("No se pudieron obtener los datos de emisión: {e}")))

    def pt_sesiones_directo(self):
        v = self._pt_video_id()
        if v:
            from .dialogos_peertube_instancia import sesiones_directo
            sesiones_directo(self, *v)

    def pt_estadisticas_video(self):
        v = self._pt_video_id()
        if v:
            from .dialogos_peertube_instancia import estadisticas_video
            estadisticas_video(self, *v)

    def pt_estadisticas_instancia(self):
        acc = self._pt_cuenta()
        if acc is not None:
            from .dialogos_peertube_instancia import estadisticas_instancia
            estadisticas_instancia(self, acc)

    def pt_abrir_dialogo(self, nombre: str, **kw):
        """Abre uno de los diálogos de moderación o administración de dialogos_peertube_instancia."""
        acc = self._pt_cuenta()
        if acc is not None:
            from . import dialogos_peertube_instancia as inst
            getattr(inst, nombre)(self, acc, **kw).present()

    def pt_bloquear_video(self, bloquear: bool = True):
        v = self._pt_video_id()
        if not v:
            return
        if not bloquear:
            return self._pt_hacer(lambda: peertube_service.desbloquear_video(*v), _("Vídeo desbloqueado."))
        from .dialogos_reddit import DialogoTextoLibre
        DialogoTextoLibre(self, _("Bloquear vídeo"), _("_Motivo del bloqueo (opcional):"),
                          lambda motivo: self._pt_hacer(lambda: peertube_service.bloquear_video(*v, razon=motivo),
                                                        _("Vídeo bloqueado.")), aceptar=_("_Bloquear")).present()

    def pt_reportar(self):
        obj = self._pt_obj()
        if not obj:
            return
        x = obj.fila.extra
        from .dialogos_reddit import DialogoTextoLibre

        def _enviar(motivo):
            if len(motivo) < 2:
                self.avisar(_("El motivo es demasiado corto"))
                return
            self._pt_hacer(lambda: peertube_service.reportar_video(x["account_id"], x.get("video_id") or x["uuid"], motivo),
                           _("Reporte enviado a los moderadores"))
        DialogoTextoLibre(self, _("Reportar vídeo"), _("_Motivo (para los moderadores):"), _enviar,
                          aceptar=_("_Enviar")).present()

    # ------------------------------------------------------------------ notificaciones
    def pt_leida(self):
        obj = self._pt_obj(("notificacion",))
        if not obj:
            return
        x = obj.fila.extra
        n = next((i for i in datos_peertube.cache(x["account_id"], "notificaciones") if i.get("id") == x["notif_id"]), None)

        def _hecho(_r):
            if n:
                n["leida"] = True
            self._pt_repintar(obj.fila.id)
        self._pt_hacer(lambda: peertube_service.marcar_leidas(x["account_id"], [x["notif_id"]]), _("Marcada como leída"), None,
                       _hecho)

    def pt_marcar_todo_leido(self):
        acc = self._pt_cuenta()
        if acc is None:
            return

        def _hecho(_r):
            for n in datos_peertube.cache(acc, "notificaciones"):
                n["leida"] = True
            self._pt_repintar()
        self._pt_hacer(lambda: peertube_service.marcar_todas_leidas(acc), _("Todas marcadas como leídas"), None, _hecho)

    # ------------------------------------------------------------------ cuenta
    def pt_reautorizar(self):
        n = self.nodo_actual
        if n is None or n.servicio != "peertube" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de PeerTube"))
            return
        from .dialogos_peertube import DialogoCuentaPeerTube
        DialogoCuentaPeerTube(self, lambda _id: self.avisar(_("Ya puedes usar la cuenta")), account_id=n.account_id).present()

    def pt_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "peertube" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de PeerTube"))
            return
        acc = n.account_id
        nombre = next((nm for i, nm in datos_peertube.cuentas() if i == acc), "")

        def _hacer():
            peertube_service.eliminar_cuenta(acc)
            self.cargar_arbol(conservar=False)
            self.avisar(_("Cuenta eliminada"))
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    def pt_ocultar_secciones(self):
        ocultas = set(config.get("peertube", "hidden_sections", []) or [])
        opciones = list(datos_peertube.SECCIONES.items())
        etiquetas = [_(t) + (_(" (oculta)") if c in ocultas else "") for c, t in opciones]

        def _alternar(i):
            clave = opciones[i][0]
            if clave in ocultas:
                ocultas.discard(clave)
            else:
                ocultas.add(clave)
            config.set("peertube", "hidden_sections", sorted(ocultas))
            self.cargar_arbol()
            self.avisar(_("Sección {b} {e}").format(b=_(opciones[i][1]), e=_("oculta") if clave in ocultas else _("visible")))
        DialogoElegir(self, _("Mostrar u ocultar secciones"), _("_Sección:"), etiquetas, _alternar, _("_Cambiar")).present()

    # ------------------------------------------------------------------ menú contextual y acciones
    def pt_menu_contextual(self, obj):
        x = obj.fila.extra
        tipo = x.get("tipo")
        if tipo == "notificacion":
            return ((_("Reproducir el vídeo"), "win.reproducir"), (_("Marcar como leída"), "win.leido"),
                    (_("Marcar todas como leídas"), "win.marcar-todo-leido"))
        if tipo == "lista":
            return ((_("Abrir la lista"), "win.pt-abrir-fila"), (_("Editar"), "win.editar"), (_("Borrar"), "win.papelera"),
                    (_("Nueva lista"), "win.nuevo"))
        if tipo == "canal":
            return ((_("Abrir el canal"), "win.pt-abrir-fila"), (_("Editar"), "win.editar"), (_("Borrar"), "win.papelera"),
                    (_("Nuevo canal"), "win.nuevo"))
        lista = [(_("Reproducir"), "win.reproducir"), (_("Ver detalles"), "win.pt-detalles"),
                 (_("Ver subtítulos"), "win.pt-subtitulos"), (_("Me gusta"), "win.me-gusta"),
                 (_("No me gusta"), "win.voto-negativo"), (_("Ver comentarios"), "win.conversacion"),
                 (_("Comentar"), "win.responder"), (_("Suscribirse al canal"), "win.pt-suscribir"),
                 (_("Cancelar la suscripción al canal"), "win.pt-cancelar-suscripcion"), (_("Datos del canal"), "win.perfil"),
                 (_("Abrir el canal"), "win.cronologia-usuario"), (_("Abrir la cuenta"), "win.pt-abrir-cuenta"),
                 (_("Añadir a «ver más tarde»"), "win.marcador"), (_("Añadir a una lista…"), "win.pt-anadir-lista"),
                 (_("Descargar…"), "win.pt-descargar"), (_("Calidad de reproducción…"), "win.pt-calidad"),
                 (_("Traducir"), "win.traducir"), (_("Resumir con IA"), "win.resumir-ia"),
                 (_("Silenciar la cuenta"), "win.pt-silenciar"), (_("Silenciar la instancia"), "win.pt-silenciar-instancia"),
                 (_("Reportar el vídeo…"), "win.pt-reportar"), (_("Estadísticas del vídeo…"), "win.pt-estadisticas-video"),
                 (_("Datos de emisión del directo…"), "win.pt-datos-emision"),
                 (_("Sesiones y repeticiones del directo…"), "win.pt-sesiones-directo"),
                 (_("Bloquear el vídeo (moderadores)…"), "win.pt-bloquear-video"),
                 (_("Desbloquear el vídeo (moderadores)"), "win.pt-desbloquear-video"),
                 (_("Abrir en el navegador"), "win.navegador"), (_("Copiar el enlace"), "win.copiar-enlace")]
        n = self.nodo_actual
        if n is not None and n.timeline == "historial":
            lista += [(_("Quitar del historial"), "win.papelera"), (_("Vaciar todo el historial"), "win.pt-vaciar-historial")]
        if x.get("propio"):
            lista += [(_("Editar mi vídeo"), "win.editar"), (_("Borrar mi vídeo"), "win.papelera")]
        return tuple(lista)

    def _registrar_acciones_peertube(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("pt-detalles", self.pt_detalles)
        a("pt-subtitulos", self.pt_subtitulos)
        a("pt-calidad", self.pt_calidad)
        a("pt-suscribir", lambda: self.pt_suscribir(True))
        a("pt-cancelar-suscripcion", lambda: self.pt_suscribir(False))
        a("pt-abrir-cuenta", self.pt_abrir_cuenta)
        a("pt-anadir-lista", self.pt_anadir_a_lista)
        a("pt-descargar", self.pt_descargar)
        a("pt-silenciar", lambda: self.pt_silenciar(False))
        a("pt-silenciar-instancia", lambda: self.pt_silenciar(True))
        a("pt-reportar", self.pt_reportar)
        a("pt-crear-directo", self.pt_crear_directo)
        a("pt-datos-emision", self.pt_datos_emision)
        a("pt-sesiones-directo", self.pt_sesiones_directo)
        a("pt-estadisticas-video", self.pt_estadisticas_video)
        a("pt-estadisticas-instancia", self.pt_estadisticas_instancia)
        a("pt-bloquear-video", lambda: self.pt_bloquear_video(True))
        a("pt-desbloquear-video", lambda: self.pt_bloquear_video(False))
        for nombre, clase, kw in (("pt-importaciones", "DialogoImportacionesPT", {}), ("pt-avisos", "DialogoAvisosPT", {}),
                                  ("pt-mis-denuncias", "DialogoAbusosPT", {"solo_mias": True}),
                                  ("pt-mod-denuncias", "DialogoAbusosPT", {}),
                                  ("pt-mod-bloqueados", "DialogoBloqueadosPT", {}),
                                  ("pt-admin-usuarios", "DialogoUsuariosAdminPT", {}),
                                  ("pt-admin-registros", "DialogoRegistrosAdminPT", {}),
                                  ("pt-admin-federacion", "DialogoFederacionAdminPT", {}),
                                  ("pt-admin-trabajos", "DialogoTrabajosAdminPT", {}),
                                  ("pt-admin-plugins", "DialogoPluginsAdminPT", {}),
                                  ("pt-admin-runners", "DialogoRunnersAdminPT", {})):
            a(nombre, lambda c=clase, k=kw: self.pt_abrir_dialogo(c, **k))
        a("pt-vaciar-historial", self.pt_vaciar_historial)
        a("pt-subir", self.pt_subir)
        a("pt-importar", self.pt_importar)
        a("pt-ir-canal", self.pt_ir_canal)
        a("pt-buscar", self.pt_buscar)
        a("pt-buscar-canales", self.pt_buscar_canales)
        a("pt-abrir-fila", self.pt_abrir_fila)
        a("pt-reautorizar", self.pt_reautorizar)
        a("pt-ocultar-secciones", self.pt_ocultar_secciones)
        a("pt-abrir-navegador", lambda: webbrowser.open(self._fila_actual().fila.enlace) if self._fila_actual() is not None
          and self._fila_actual().fila.enlace else None)
        a("pt-copiar", lambda: copiar_al_portapapeles(self._fila_actual().fila.enlace, _("Enlace copiado"))
          if self._fila_actual() is not None else None)
