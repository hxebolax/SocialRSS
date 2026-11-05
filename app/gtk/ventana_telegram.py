# -*- coding: utf-8 -*-
"""Telegram en la ventana principal (mixin de VentanaPrincipal).

Las acciones comparten teclas con Mastodon y Bluesky (Ctrl+N nuevo, Ctrl+R responder, Supr borrar...): los
manejadores de ventana.py llaman aquí cuando el elemento enfocado es de Telegram. Cada cuenta tiene los buffers
"Chats" y "Chats sin leer"; Intro sobre un chat abre su conversación como un buffer más, cerrable con Ctrl+W."""

import logging
import os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.plataforma import llamar_en_ui, mostrar_aviso_dialogo
from app.core.speech import copiar_al_portapapeles, speak
from app.services.telegram.telegram_common import HAS_KURIGRAM
from app.services.telegram.telegram_service import telegram_service

from . import datos_telegram
from .dialogos_telegram import REACCIONES, ejecutar

log = logging.getLogger(__name__)

ANUNCIAR_MAX = 3            # anuncios por voz de una misma ráfaga de mensajes
ANTIRREBOTE_MS = 500        # las ráfagas del tiempo real se juntan en un solo repintado


class _TelegramMixin:
    # ------------------------------------------------------------------ contexto
    def _tg_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de Telegram."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "telegram"
        n = self.nodo_actual
        return n is not None and n.servicio == "telegram"

    def _tg_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "telegram" and n.account_id:
            return n.account_id
        cuentas = datos_telegram.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de Telegram"))
        return None

    def _tg_chat_abierto(self):
        """(account_id, chat_id) de la conversación abierta, o None."""
        n = self.nodo_actual
        if n is not None and n.servicio == "telegram":
            cid = datos_telegram.id_de_chat(n.timeline)
            if cid is not None:
                return n.account_id, cid
        return None

    def _tg_chat_de_fila(self, obj=None):
        """(account_id, chat_id, título) del chat de la fila enfocada (un chat o un mensaje) o del buffer abierto."""
        obj = obj or self._fila_actual()
        if obj is not None and obj.fila.servicio == "telegram":
            x = obj.fila.extra
            if x.get("chat_id") is not None:
                return x["account_id"], x["chat_id"], x.get("titulo") or datos_telegram.titulo_chat(
                    x["account_id"], x["chat_id"])
        abierto = self._tg_chat_abierto()
        if abierto:
            return abierto[0], abierto[1], datos_telegram.titulo_chat(*abierto)
        self.avisar(_("Selecciona un chat de Telegram"))
        return None

    def _tg_mensaje(self, propio: bool = False):
        """La fila de mensaje enfocada; con `propio`, además debe ser tuya."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "telegram" or obj.fila.extra.get("tipo") != "mensaje":
            self.avisar(_("Selecciona un mensaje de Telegram"))
            return None
        if propio and not obj.fila.extra.get("propio"):
            self.avisar(_("Solo puedes hacer esto con tus propios mensajes"))
            return None
        return obj

    def _tg_nodos_cuenta(self, account_id):
        for raiz in getattr(self, "arbol", []):
            if raiz.servicio == "telegram":
                return [h for c in raiz.hijos if c.account_id == account_id for h in c.hijos]
        return []

    def _tg_disponible(self) -> bool:
        if not HAS_KURIGRAM:
            self.avisar(_("Falta Kurigram. Instálalo con: pip install kurigram tgcrypto"))
            return False
        if not telegram_service.credenciales_configuradas():
            self.avisar(_("Faltan las credenciales de la API. Indícalas en el menú Telegram, Credenciales de la API."))
            return False
        return True

    # ------------------------------------------------------------------ actualizar y cargar más
    def tg_actualizar_nodo(self, nodo, silencioso: bool = False):
        if not self._tg_disponible():
            return
        clave = (nodo.account_id, nodo.timeline)
        cid = datos_telegram.id_de_chat(nodo.timeline)

        def _fin(res, err):
            if err:
                if not silencioso:
                    self.avisar(_("Error al actualizar: {e}").format(e=err))
                return
            self._cargados.add(clave)
            if nodo is self.nodo_actual and not self.busqueda:
                obj = self._fila_actual()
                self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
            if not silencioso and nodo is self.nodo_actual:
                self.estado.set_text(_("{t}: actualizado").format(t=nodo.titulo))
        if cid is not None:
            ejecutar(lambda: telegram_service.cargar_historial(nodo.account_id, cid, 50), _fin)
        else:
            ejecutar(lambda: telegram_service.cargar_chats(nodo.account_id), _fin)

    def tg_cargar_mas(self):
        n = self.nodo_actual
        if n is None or not n.listable or not self._tg_disponible():
            return
        cid = datos_telegram.id_de_chat(n.timeline)
        actual = self._fila_actual()
        self.avisar(_("Cargando mensajes anteriores…") if cid is not None else _("Cargando más chats…"))

        def _fin(res, err):
            if err:
                self.avisar(_("Error al cargar: {e}").format(e=err))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            self.avisar(_("{n} cargados").format(n=res) if res else _("No hay más"))
        if cid is not None:
            antes = datos_telegram.mensaje_mas_antiguo_id(n.account_id, cid)
            ejecutar(lambda: telegram_service.cargar_mas_antiguos(n.account_id, cid, antes, 50), _fin)
        else:
            total = self.almacen_lista.get_n_items() + 200
            ejecutar(lambda: telegram_service.cargar_chats(n.account_id, limit=total), _fin)

    def _sondeo_telegram(self):
        """Respaldo del tiempo real: refresca la lista de chats cargada (los mensajes llegan por eventos)."""
        if config.get("gtk", "telegram_realtime", True):
            return
        for acc_id, _n in datos_telegram.cuentas():
            for nodo in self._tg_nodos_cuenta(acc_id):
                if nodo.timeline == "chats" and (acc_id, "chats") in self._cargados:
                    self.tg_actualizar_nodo(nodo, silencioso=True)

    # ------------------------------------------------------------------ abrir conversaciones
    def tg_abrir_chat(self, obj):
        x = obj.fila.extra
        self._abrir_buffer(x["account_id"], f"chat:{x['chat_id']}", x.get("titulo") or obj.fila.autor, servicio="telegram")

    def tg_abrir_fila(self):
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "telegram" and obj.fila.extra.get("tipo") == "chat":
            self.tg_abrir_chat(obj)
        else:
            self.avisar(_("Selecciona un chat de la lista"))

    def _tg_abrir_por_id(self, account_id: int, chat_id: int, titulo: str = ""):
        self._abrir_buffer(account_id, f"chat:{chat_id}", titulo or datos_telegram.titulo_chat(account_id, chat_id),
                           servicio="telegram")

    def tg_chat_nuevo(self):
        """Abre la conversación con un usuario, canal o grupo públicos por su @nombre."""
        acc = self._tg_cuenta()
        if acc is None or not self._tg_disponible():
            return
        from .dialogos import DialogoTexto

        def _resolver(texto):
            usuario = texto.strip()
            if not usuario.startswith("@") and not usuario.lstrip("-").isdigit():
                usuario = "@" + usuario
            self.avisar(_("Buscando {u}…").format(u=usuario))

            def _fin(info, err):
                if err or not info:
                    self.avisar(_("No se encontró: {e}").format(e=err or usuario))
                    return
                self._tg_abrir_por_id(acc, info["id"], info.get("title") or usuario)
            ident = int(usuario) if usuario.lstrip("-").isdigit() else usuario
            ejecutar(lambda: telegram_service.obtener_info_chat(acc, ident), _fin)
        DialogoTexto(self, _("Nuevo chat"), _("_Usuario (por ejemplo @ana):"), _resolver,
                     aceptar=_("_Abrir conversación")).present()

    def tg_guardados(self):
        acc = self._tg_cuenta()
        if acc is None or not self._tg_disponible():
            return
        self.avisar(_("Abriendo los mensajes guardados…"))

        def _fin(cid, err):
            if err or cid is None:
                self.avisar(_("No se pudo abrir: {e}").format(e=err))
                return
            self._tg_abrir_por_id(acc, cid, _("Mensajes guardados"))
        ejecutar(lambda: telegram_service.obtener_id_guardados(acc), _fin)

    # ------------------------------------------------------------------ escribir
    def tg_nuevo(self):
        """Ctrl+N: mensaje al chat abierto o al chat enfocado; en otro caso, abrir un chat nuevo."""
        if not self._tg_disponible():
            return
        n = self.nodo_actual
        if n is not None and n.servicio == "telegram" and n.listable:
            datos = None
            if datos_telegram.id_de_chat(n.timeline) is not None or (
                    self._fila_actual() is not None and self._fila_actual().fila.extra.get("tipo") == "chat"):
                datos = self._tg_chat_de_fila()
            if datos:
                return self._tg_escribir(*datos)
        self.tg_chat_nuevo()

    def _tg_escribir(self, account_id: int, chat_id: int, titulo: str, **kw):
        from .dialogos_telegram import DialogoMensajeTelegram

        def _enviado():
            self._tg_refrescar_chat(account_id, chat_id, ir_al_final=True)
        DialogoMensajeTelegram(self, account_id, chat_id, titulo, _enviado, **kw).present()

    def tg_responder(self):
        obj = self._tg_mensaje()
        if obj is None:
            return
        x = obj.fila.extra
        cita = f"{obj.fila.autor}: {x.get('texto') or datos_telegram.etiqueta_media(x.get('media_type', ''))}"
        self._tg_escribir(x["account_id"], x["chat_id"], datos_telegram.titulo_chat(x["account_id"], x["chat_id"]),
                          responde_a=x["message_id"], cita=cita[:200])

    def tg_editar(self):
        obj = self._tg_mensaje(propio=True)
        if obj is None:
            return
        x = obj.fila.extra
        if not x.get("texto"):
            self.avisar(_("Este mensaje no tiene texto que editar"))
            return
        self._tg_escribir(x["account_id"], x["chat_id"], datos_telegram.titulo_chat(x["account_id"], x["chat_id"]),
                          editar=x["message_id"], texto_inicial=x["texto"])

    def tg_enviar_archivo(self):
        datos = self._tg_chat_de_fila()
        if not datos or not self._tg_disponible():
            return
        acc, cid, titulo = datos
        dlg = Gtk.FileDialog(title=_("Enviar archivo a {t}").format(t=titulo))

        def _fin(origen, res):
            try:
                ruta = origen.open_finish(res).get_path()
            except Exception:
                return
            self.avisar(_("Enviando {f}…").format(f=os.path.basename(ruta)))
            progreso = {"pct": -1}

            def _avance(actual, total):
                pct = int(actual * 100 / total) // 25 * 25 if total else 0
                if pct != progreso["pct"] and pct not in (0, 100):
                    progreso["pct"] = pct
                    llamar_en_ui(self.avisar, _("Enviando: {p}%").format(p=pct), False)

            def _listo(res, err):
                if err:
                    self.avisar(_("No se pudo enviar: {e}").format(e=err))
                    return
                self.avisar(_("Archivo enviado"))
                self._tg_refrescar_chat(acc, cid, ir_al_final=True)
            ejecutar(lambda: telegram_service.enviar_archivo(acc, cid, ruta, progress_cb=_avance), _listo)
        dlg.open(self, None, _fin)

    # ------------------------------------------------------------------ acciones sobre mensajes
    def tg_borrar(self):
        """Supr: en una conversación borra el mensaje; en la lista de chats, sale del chat."""
        if not self._tg_disponible():
            return
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "telegram":
            return
        from .dialogos import confirmar_async
        x = obj.fila.extra
        if x.get("tipo") == "chat":
            acc, cid, tipo = x["account_id"], x["chat_id"], x.get("chat_tipo")

            def _salir():
                def _fin(res, err):
                    if err:
                        self.avisar(_("No se pudo salir: {e}").format(e=err))
                        return
                    self.cargar_lista(incremental=True)
                    self.avisar(_("Chat eliminado de tu lista"))
                ejecutar(lambda: telegram_service.salir_chat(acc, cid, eliminar=True, tipo=tipo), _fin)
            confirmar_async(self, _("¿Salir de {t} y quitarlo de tu lista de chats?").format(t=x["titulo"]), _salir,
                            etiqueta_si=_("Salir"))
            return
        acc, cid, mid = x["account_id"], x["chat_id"], x["message_id"]

        def _borrar():
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo borrar: {e}").format(e=err))
                    return
                self.cargar_lista(incremental=True)
                self.avisar(_("Mensaje borrado"))
            ejecutar(lambda: telegram_service.borrar_mensaje(acc, cid, mid, revoke=True), _fin)
        confirmar_async(self, _("¿Borrar este mensaje para todos los participantes del chat?"), _borrar,
                        etiqueta_si=_("Borrar"))

    def tg_reaccionar(self, emoji: str = None):
        obj = self._tg_mensaje()
        if obj is None or not self._tg_disponible():
            return
        x = obj.fila.extra

        def _poner(e):
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo reaccionar: {e}").format(e=err))
                    return
                self.avisar(_("Reacción enviada"))
                self._tg_refrescar_chat(x["account_id"], x["chat_id"])
            ejecutar(lambda: telegram_service.enviar_reaccion(x["account_id"], x["chat_id"], x["message_id"], e), _fin)
        if emoji:
            return _poner(emoji)
        from .dialogos import DialogoElegir
        DialogoElegir(self, _("Reaccionar"), _("_Reacción:"), REACCIONES, lambda i: _poner(REACCIONES[i]),
                      aceptar=_("_Reaccionar")).present()

    def tg_quien_reacciono(self):
        obj = self._tg_mensaje()
        if obj is None or not self._tg_disponible():
            return
        x = obj.fila.extra

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo consultar: {e}").format(e=err))
                return
            if not res:
                self.avisar(_("Nadie ha reaccionado a este mensaje"))
                return
            mostrar_aviso_dialogo("\n".join(f"{r['emoji']} {r['nombre']}" for r in res), _("Quién ha reaccionado"))
        ejecutar(lambda: telegram_service.quien_reacciono(x["account_id"], x["chat_id"], x["message_id"]), _fin)

    def tg_reenviar(self):
        obj = self._tg_mensaje()
        if obj is None or not self._tg_disponible():
            return
        x = obj.fila.extra
        chats = db.query("SELECT chat_id, COALESCE(title, '') FROM telegram_chats WHERE account_id = ? "
                         "ORDER BY pinned DESC, last_message_date DESC LIMIT 100", (x["account_id"],))
        if not chats:
            self.avisar(_("Primero carga la lista de chats"))
            return
        from .dialogos import DialogoElegir

        def _enviar(i):
            destino = chats[i][0]

            def _fin(res, err):
                self.avisar(_("No se pudo reenviar: {e}").format(e=err) if err else _("Mensaje reenviado"))
            from .dialogos_telegram_extra import compartir
            ejecutar(lambda: compartir(x["account_id"], x["chat_id"], x["message_id"], destino), _fin)
        DialogoElegir(self, _("Reenviar mensaje"), _("_Reenviar a:"), [t or str(c) for c, t in chats], _enviar,
                      aceptar=_("_Reenviar")).present()

    def tg_copiar(self):
        obj = self._tg_mensaje()
        if obj is None:
            return
        texto = obj.fila.extra.get("texto") or ""
        if not texto:
            self.avisar(_("Este mensaje no tiene texto"))
            return
        copiar_al_portapapeles(texto, _("Texto copiado"))

    def _tg_descargar(self, obj, despues=None):
        x = obj.fila.extra
        if not x.get("descargable"):
            self.avisar(_("Este mensaje no tiene un archivo que descargar"))
            return
        if x.get("descargado") and x.get("media_path"):
            return despues(x["media_path"]) if despues else self.avisar(_("Ya está descargado: {r}").format(r=x["media_path"]))
        if not self._tg_disponible():
            return
        self.avisar(_("Descargando…"))
        progreso = {"pct": -1}

        def _avance(actual, total):
            pct = int(actual * 100 / total) // 25 * 25 if total else 0
            if pct != progreso["pct"] and pct not in (0, 100):
                progreso["pct"] = pct
                self.avisar(_("Descargando: {p}%").format(p=pct), interrumpir=False)

        def _fin(ruta, err):
            if err or not ruta:
                self.avisar(_("No se pudo descargar: {e}").format(e=err or _("sin archivo")))
                return
            x["media_path"], x["descargado"] = ruta, True
            self.avisar(_("Descargado en {r}").format(r=ruta))
            if despues:
                despues(ruta)
        ejecutar(lambda: telegram_service.descargar_media(x["account_id"], x["chat_id"], x["message_id"],
                                                          progreso=_avance), _fin)

    def tg_descargar(self):
        obj = self._tg_mensaje()
        if obj is not None:
            self._tg_descargar(obj)

    def tg_abrir_adjunto(self):
        obj = self._tg_mensaje()
        if obj is None:
            return

        def _abrir(ruta):
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(ruta).get_uri(), None)
                self.avisar(_("Abriendo el archivo"))
            except GLib.Error as e:
                self.avisar(_("No se pudo abrir el archivo: {e}").format(e=e.message))
        self._tg_descargar(obj, _abrir)

    def tg_reproducir(self) -> bool:
        """Reproduce una nota de voz, un audio o un vídeo del chat. True si la fila era reproducible."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "telegram" or not obj.fila.extra.get("reproducible"):
            return False
        x = obj.fila.extra
        titulo = f"{obj.fila.autor}: {datos_telegram.etiqueta_media(x['media_type'])}"

        def _sonar(ruta):
            from app.services.media_player.player2_service import KIND_PODCAST, player2_service

            def _fin(ok):
                self.avisar(_("Reproduciendo: {t}").format(t=titulo) if ok else _("No se pudo reproducir"))
            player2_service.cargar_y_reproducir(ruta, titulo, KIND_PODCAST, resume=False, callback=_fin)
        self._tg_descargar(obj, _sonar)
        return True

    # ------------------------------------------------------------------ acciones sobre chats
    def _tg_chat_obj(self):
        """(account_id, chat_id, extra) del chat enfocado, o None."""
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "telegram" and obj.fila.extra.get("chat_id") is not None:
            x = obj.fila.extra
            return x["account_id"], x["chat_id"], datos_telegram.info_chat(x["account_id"], x["chat_id"])
        abierto = self._tg_chat_abierto()
        if abierto:
            return abierto[0], abierto[1], datos_telegram.info_chat(*abierto)
        self.avisar(_("Selecciona un chat de Telegram"))
        return None

    def _tg_alternar(self, que: str):
        datos = self._tg_chat_obj()
        if not datos or not self._tg_disponible():
            return
        acc, cid, info = datos
        if que == "silenciar":
            nuevo = not info.get("silenciado")
            llamada, textos = (lambda: telegram_service.silenciar_chat(acc, cid, silenciar=nuevo),
                               (_("Chat silenciado"), _("Notificaciones del chat reactivadas")))
        elif que == "fijar":
            nuevo = not info.get("fijado")
            llamada, textos = (lambda: telegram_service.fijar_chat(acc, cid, fijar=nuevo),
                               (_("Chat fijado"), _("Chat desfijado")))
        else:
            nuevo = True
            llamada, textos = (lambda: telegram_service.archivar_chat(acc, cid, archivar=True),
                               (_("Chat archivado"), ""))

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.avisar(textos[0] if nuevo else textos[1])
            self._tg_refrescar_chats(acc)
        ejecutar(llamada, _fin)

    def tg_marcar_leido(self, leido: bool = True):
        datos = self._tg_chat_obj()
        if not datos or not self._tg_disponible():
            return
        acc, cid, _info = datos

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo marcar: {e}").format(e=err))
                return
            if leido:
                datos_telegram.guardar_leido_hasta(acc, cid, datos_telegram.ultimo_mensaje_id(acc, cid))
            self.avisar(_("Chat marcado como leído") if leido else _("Chat marcado como no leído"))
            self._tg_refrescar_chats(acc)
        if leido:
            ejecutar(lambda: telegram_service.marcar_chat_leido(acc, cid), _fin)
        else:
            ejecutar(lambda: telegram_service.marcar_chat_no_leido(acc, cid), _fin)

    def tg_marcar_todo_leido(self):
        acc = self._tg_cuenta()
        if acc is None or not self._tg_disponible():
            return

        def _fin(n, err):
            if err:
                self.avisar(_("No se pudo marcar: {e}").format(e=err))
                return
            self.avisar(_("{n} chats marcados como leídos").format(n=n))
            self._tg_refrescar_chats(acc)
        ejecutar(lambda: telegram_service.marcar_todos_leidos(acc), _fin)

    def tg_info(self):
        datos = self._tg_chat_obj()
        if not datos or not self._tg_disponible():
            return
        acc, cid, _info = datos
        self.avisar(_("Consultando…"))

        def _fin(info, err):
            if err or not info:
                self.avisar(_("No se pudo consultar: {e}").format(e=err))
                return
            lineas = [info["title"], _(datos_telegram.TIPOS_CHAT.get(info["type"], info["type"]))]
            if info.get("username"):
                lineas.append(info["username"])
            if info.get("estado"):
                lineas.append(info["estado"])
            if info.get("members_count"):
                lineas.append(_("{n} miembros").format(n=info["members_count"]))
            if info.get("online_count"):
                lineas.append(_("{n} en línea").format(n=info["online_count"]))
            if info.get("telefono"):
                lineas.append(_("Teléfono: {t}").format(t=info["telefono"]))
            if info.get("verificado"):
                lineas.append(_("Cuenta verificada"))
            if info.get("protegido"):
                lineas.append(_("Contenido protegido: no se puede reenviar"))
            if info.get("bio"):
                lineas.append("\n" + info["bio"])
            mostrar_aviso_dialogo("\n".join(lineas), _("Información del chat"))
        ejecutar(lambda: telegram_service.obtener_info_chat(acc, cid), _fin)

    def tg_credenciales(self):
        from .dialogos_telegram import DialogoCredencialesTelegram
        DialogoCredencialesTelegram(self).present()

    def tg_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "telegram" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de Telegram"))
            return
        from .dialogos import confirmar_async
        acc = n.account_id
        nombre = next((nm for i, nm in datos_telegram.cuentas() if i == acc), "")

        def _hacer():
            def _fin(revocada, err):
                datos_telegram.EXTRA.pop(acc, None)
                self.cargar_arbol(conservar=False)
                if err:
                    self.avisar(_("Cuenta quitada de este equipo, pero hubo un error: {e}").format(e=err))
                elif revocada:
                    self.avisar(_("Sesión cerrada y cuenta eliminada"))
                else:
                    self.avisar(_("Cuenta quitada de este equipo. La sesión sigue abierta en Telegram: "
                                  "ciérrala desde Ajustes, Dispositivos."))
            ejecutar(lambda: telegram_service.cerrar_sesion(acc), _fin)
        confirmar_async(self, _("¿Cerrar la sesión de {n} y eliminar la cuenta de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Cerrar sesión"))

    # ------------------------------------------------------------------ refrescos y lectura
    def _tg_refrescar_chats(self, account_id: int):
        n = self.nodo_actual
        if n is not None and n.servicio == "telegram" and n.account_id == account_id and n.timeline in ("chats", "unread"):
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _tg_refrescar_chat(self, account_id: int, chat_id: int, ir_al_final: bool = False):
        """Repinta la conversación si es la que se ve; con `ir_al_final`, deja el foco en el último mensaje."""
        n = self.nodo_actual
        if n is None or n.servicio != "telegram" or n.account_id != account_id:
            return
        if n.timeline in ("chats", "unread"):
            return self._tg_refrescar_chats(account_id)
        if datos_telegram.id_de_chat(n.timeline) != chat_id:
            return
        obj = self._fila_actual()
        if ir_al_final:
            def _al_final(filas):
                if filas:
                    self.seleccion_lista.set_selected(len(filas) - 1)
            self.cargar_lista(incremental=True, despues=_al_final)
        else:
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _tg_al_cambiar_nodo(self, anterior, nuevo):
        """Al dejar una conversación se marca como leída (aquí y en Telegram)."""
        if anterior is None or anterior is nuevo or anterior.servicio != "telegram":
            return
        cid = datos_telegram.id_de_chat(anterior.timeline)
        if cid is None:
            return
        self._tg_leer_conversacion(anterior.account_id, cid)

    def _tg_leer_conversacion(self, account_id: int, chat_id: int):
        ultimo = datos_telegram.ultimo_mensaje_id(account_id, chat_id)
        if not ultimo or ultimo <= datos_telegram.leido_hasta(account_id, chat_id):
            return
        datos_telegram.guardar_leido_hasta(account_id, chat_id, ultimo)
        if HAS_KURIGRAM:
            ejecutar(lambda: telegram_service.marcar_chat_leido(account_id, chat_id), lambda r, e: None)

    # ------------------------------------------------------------------ tiempo real
    def iniciar_tiempo_real_telegram(self):
        """Suscribe los eventos de Telegram y arranca el cliente de cada cuenta para recibir las novedades."""
        if not HAS_KURIGRAM:
            return
        if not getattr(self, "_tg_suscrito", False):
            self._tg_suscrito = True
            self._tg_pendientes = {}
            event_bus.subscribe("telegram_new_message", self._tg_ev_mensaje)
            event_bus.subscribe("telegram_message_edited", self._tg_ev_cambio)
            event_bus.subscribe("telegram_messages_deleted", self._tg_ev_cambio)
        if not config.get("gtk", "telegram_realtime", True) or not telegram_service.credenciales_configuradas():
            return
        for acc_id, _n in datos_telegram.cuentas():
            if acc_id not in telegram_service._clients:
                ejecutar(lambda a=acc_id: telegram_service.obtener_cliente(a), self._tg_cliente_listo)

    def _tg_cliente_listo(self, res, err):
        if err:
            log.warning(f"No se pudo conectar con Telegram: {err}")

    def detener_tiempo_real_telegram(self):
        if getattr(self, "_tg_suscrito", False):
            self._tg_suscrito = False
            event_bus.unsubscribe("telegram_new_message", self._tg_ev_mensaje)
            event_bus.unsubscribe("telegram_message_edited", self._tg_ev_cambio)
            event_bus.unsubscribe("telegram_messages_deleted", self._tg_ev_cambio)

    def _tg_ev_mensaje(self, account_id, chat_id):
        llamar_en_ui(self._tg_novedad, account_id, chat_id, True)

    def _tg_ev_cambio(self, account_id, chat_id, *_resto):
        llamar_en_ui(self._tg_novedad, account_id, chat_id, False)

    def _tg_novedad(self, account_id, chat_id, nuevo: bool):
        """Llega en el hilo de la UI. Las ráfagas se juntan en un solo repintado y en pocos anuncios."""
        info = self._tg_pendientes.setdefault(account_id, {"chats": set(), "nuevos": []})
        info["chats"].add(chat_id)
        if nuevo:
            info["nuevos"].append(chat_id)
        if not info.get("programado"):
            info["programado"] = True
            GLib.timeout_add(ANTIRREBOTE_MS, self._tg_vaciar, account_id)

    def _tg_vaciar(self, account_id):
        info = self._tg_pendientes.pop(account_id, {"chats": set(), "nuevos": []})
        n = self.nodo_actual
        if n is not None and n.servicio == "telegram" and n.account_id == account_id:
            abierto = datos_telegram.id_de_chat(n.timeline)
            if n.timeline in ("chats", "unread") or abierto in info["chats"]:
                obj = self._fila_actual()
                self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
        self._tg_anunciar(account_id, info["nuevos"])
        return GLib.SOURCE_REMOVE

    def _tg_anunciar(self, account_id, chats_nuevos):
        """Dice el mensaje nuevo si es de la conversación abierta o de un chat privado (nunca de grupos ni canales)."""
        if not chats_nuevos or not config.get("telegram", "announce_new", True):
            return
        n = self.nodo_actual
        abierto = datos_telegram.id_de_chat(n.timeline) if (n is not None and n.servicio == "telegram"
                                                            and n.account_id == account_id) else None
        dichos = 0
        for chat_id in dict.fromkeys(chats_nuevos):
            fila = db.query_one(
                "SELECT m.sender_name, COALESCE(m.text, ''), COALESCE(m.media_type, ''), COALESCE(m.outgoing, 0), "
                "COALESCE(c.chat_type, ''), COALESCE(c.title, '') FROM telegram_messages m "
                "LEFT JOIN telegram_chats c ON c.account_id = m.account_id AND c.chat_id = m.chat_id "
                "WHERE m.account_id = ? AND m.chat_id = ? ORDER BY m.message_id DESC LIMIT 1", (account_id, chat_id))
            if not fila or fila[3]:
                continue
            quien, texto, medio, _saliente, tipo, titulo = fila
            if chat_id != abierto and tipo not in ("private", "bot"):
                continue
            cuerpo = " ".join(texto.split())[:300] or datos_telegram.etiqueta_media(medio)
            speak(f"{quien or titulo}: {cuerpo}", interrupt=False)
            dichos += 1
            if dichos >= ANUNCIAR_MAX:
                break

    # ------------------------------------------------------------------ acciones
    # ------------------------------------------------------------------ administrar el chat
    def tg_admin_dialogo(self, nombre: str):
        """Abre un diálogo de dialogos_telegram_admin sobre el chat enfocado."""
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from . import dialogos_telegram_admin as adm
            getattr(adm, nombre)(self, *datos).present()

    def tg_admin_funcion(self, nombre: str, *extra):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from . import dialogos_telegram_admin as adm
            getattr(adm, nombre)(self, *datos, *extra)

    def tg_vaciar(self):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from .dialogos_telegram_admin import vaciar_chat
            tipo = datos_telegram.info_chat(datos[0], datos[1]).get("tipo")
            vaciar_chat(self, datos[0], datos[1], datos[2], tipo, lambda: self._tg_refrescar_chat(datos[0], datos[1]))

    def tg_enviar_como(self):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from .dialogos_telegram_admin import enviar_como
            enviar_como(self, datos[0], datos[1])

    # ------------------------------------------------------------------ carpetas, crear chats y búsquedas
    def tg_cuenta_lista(self):
        acc = self._tg_cuenta()
        return acc if acc is not None and self._tg_disponible() else None

    def tg_carpetas(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .dialogos_telegram_chats import DialogoCarpetasTG
            DialogoCarpetasTG(self, acc).present()

    def tg_crear_chat(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .dialogos_telegram_chats import crear_chat
            crear_chat(self, acc, lambda cid, titulo: (self._tg_refrescar_chats(acc), self._tg_abrir_por_id(acc, cid, titulo)))

    def tg_unirse_enlace(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .dialogos_telegram_chats import unirse_por_enlace
            unirse_por_enlace(self, acc, lambda _r: self._tg_refrescar_chats(acc))

    def tg_buscar_global(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .dialogos_telegram_chats import buscar_global
            buscar_global(self, acc, self._tg_abrir_por_id)

    def tg_buscar_publicaciones(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .dialogos_telegram_chats import buscar_publicaciones
            buscar_publicaciones(self, acc)

    def tg_frecuentes(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .dialogos_telegram_chats import chats_frecuentes
            chats_frecuentes(self, acc, self._tg_abrir_por_id)

    def tg_chat_funcion(self, nombre: str, *extra):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from . import dialogos_telegram_chats as ch
            getattr(ch, nombre)(self, *datos, *extra)

    # ------------------------------------------------------------------ mensajes: encuestas, fijados, programados, listas, bots
    def tg_msg_funcion(self, nombre: str, *extra):
        """Una función de dialogos_telegram_mensajes sobre el mensaje enfocado: (ventana, cuenta, chat, mensaje, ...)."""
        obj = self._tg_mensaje()
        if obj is None or not self._tg_disponible():
            return
        x = obj.fila.extra
        from . import dialogos_telegram_mensajes as m
        getattr(m, nombre)(self, x["account_id"], x["chat_id"], x["message_id"], *extra)

    def tg_chat_msg_funcion(self, nombre: str, *extra):
        """Una función de dialogos_telegram_mensajes sobre el chat enfocado: (ventana, cuenta, chat, título, ...)."""
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from . import dialogos_telegram_mensajes as m
            getattr(m, nombre)(self, *datos, *extra)

    def tg_extra_cuenta(self, clase: str):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from . import dialogos_telegram_extra as e
            getattr(e, clase)(self, acc).present()

    def tg_extra_chat(self, nombre: str, *extra):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from . import dialogos_telegram_extra as e
            getattr(e, nombre)(self, *datos, *extra)

    def tg_extra_msg(self, nombre: str, *extra):
        obj = self._tg_mensaje()
        if obj is None or not self._tg_disponible():
            return
        x = obj.fila.extra
        from . import dialogos_telegram_extra as e
        getattr(e, nombre)(self, x["account_id"], x["chat_id"], x["message_id"], *extra)

    def _tg_recargar(self):
        datos = self._tg_chat_abierto()
        if datos:
            self._tg_refrescar_chat(datos[0], datos[1])

    def tg_chat_funcion_cid(self, nombre: str):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from . import dialogos_telegram_mensajes as m
            getattr(m, nombre)(self, datos[0], datos[1], self._tg_recargar)

    def tg_programados(self):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from .dialogos_telegram_mensajes import DialogoProgramadosTG
            DialogoProgramadosTG(self, *datos).present()

    # ------------------------------------------------------------------ perfil y cuenta
    def tg_cuenta_dialogo(self, nombre: str, **kw):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from . import dialogos_telegram_cuenta as c
            getattr(c, nombre)(self, acc, **kw).present()

    def tg_contactos(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .dialogos_telegram_cuenta import DialogoContactosTG
            DialogoContactosTG(self, acc, self._tg_abrir_por_id).present()

    def tg_boosts(self, potenciar: bool = False):
        datos = self._tg_chat_de_fila()
        if datos and self._tg_disponible():
            from . import dialogos_telegram_cuenta as c
            (c.potenciar_canal if potenciar else c.estado_boosts)(self, *datos)

    def tg_drive(self):
        acc = self.tg_cuenta_lista()
        if acc is not None:
            from .tgdrive import abrir
            abrir(self, acc)

    def _registrar_acciones_telegram(self, accion):
        accion("tg-drive", self.tg_drive)
        for nombre, clase, kw in (("tg-mi-perfil", "DialogoMiPerfilTG", {}), ("tg-bloqueados", "DialogoBloqueadosTG", {}),
                                  ("tg-sesiones", "DialogoSesionesTG", {}), ("tg-privacidad", "DialogoPrivacidadTG", {}),
                                  ("tg-seguridad", "DialogoSeguridadTG", {}), ("tg-historias", "DialogoHistoriasTG", {}),
                                  ("tg-mis-historias", "DialogoHistoriasTG", {"propias": True}),
                                  ("tg-mis-bots", "DialogoMisBotsTG", {})):
            accion(nombre, lambda c=clase, k=kw: self.tg_cuenta_dialogo(c, **k))
        accion("tg-contactos", self.tg_contactos)
        for nombre, clase in (("tg-privacidad-global", "DialogoPrivacidadGlobalTG"), ("tg-cambiar-numero", "DialogoNumeroTG"),
                              ("tg-importar-contactos", "DialogoImportarContactosTG")):
            accion(nombre, lambda c=clase: self.tg_extra_cuenta(c))
        accion("tg-historias-archivadas", lambda: self.tg_cuenta_dialogo("DialogoHistoriasTG", archivadas=True))
        for nombre, fn in (("tg-album", "enviar_album"), ("tg-gif", "enviar_gif"), ("tg-videonota", "enviar_videonota"),
                           ("tg-sticker", "enviar_sticker"), ("tg-chats-comun", "chats_en_comun"),
                           ("tg-historias-chat", "historias_del_chat")):
            accion(nombre, lambda f=fn: self.tg_extra_chat(f))
        accion("tg-saltar-fecha", lambda: self.tg_extra_chat("saltar_a_fecha", self._tg_recargar))
        for nombre, fn, recarga in (("tg-editar-pie", "editar_pie", True), ("tg-copiar-a", "copiar_a_otro_chat", False),
                                    ("tg-reenviar-album", "reenviar_album", False), ("tg-leido-hasta", "leido_hasta_aqui", True)):
            accion(nombre, lambda f=fn, r=recarga: self.tg_extra_msg(f, *((self._tg_recargar,) if r else ())))
        accion("tg-boosts", self.tg_boosts)
        accion("tg-potenciar", lambda: self.tg_boosts(True))
        accion("tg-encuesta-nueva", lambda: self.tg_chat_msg_funcion("crear_encuesta", self._tg_recargar))
        accion("tg-encuesta", lambda: self.tg_msg_funcion("ver_encuesta", self._tg_recargar))
        accion("tg-fijar-mensaje", lambda: self.tg_msg_funcion("fijar_mensaje", True))
        accion("tg-desfijar-mensaje", lambda: self.tg_msg_funcion("fijar_mensaje", False))
        accion("tg-desfijar-todos", lambda: self.tg_chat_msg_funcion("desfijar_todos"))
        accion("tg-programar", lambda: self.tg_chat_msg_funcion("programar_mensaje"))
        accion("tg-programados", self.tg_programados)
        accion("tg-lista-tareas-nueva", lambda: self.tg_chat_msg_funcion("crear_lista_tareas"))
        accion("tg-lista-tareas", lambda: self.tg_msg_funcion("ver_lista_tareas"))
        accion("tg-botones", lambda: self.tg_msg_funcion("botones_del_mensaje", self._tg_recargar))
        accion("tg-comandos-bot", lambda: self.tg_chat_msg_funcion("comandos_del_bot", self._tg_recargar))
        accion("tg-iniciar-bot", lambda: self.tg_chat_funcion_cid("iniciar_bot"))
        accion("tg-detalles-mensaje", lambda: self.tg_msg_funcion("detalles_mensaje"))
        accion("tg-traducir-mensaje", lambda: self.tg_msg_funcion("traducir_mensaje"))
        accion("tg-guardar-mensaje", lambda: self.tg_msg_funcion("reenviar_a_guardados"))
        accion("tg-borrar-descarga", lambda: self.tg_msg_funcion("borrar_descarga_local"))
        accion("tg-menciones-leidas", lambda: self.tg_chat_msg_funcion("marcar_menciones_leidas"))
        accion("tg-reacciones-leidas", lambda: self.tg_chat_msg_funcion("marcar_reacciones_leidas"))
        accion("tg-dado", lambda: self.tg_chat_msg_funcion("enviar_dado"))
        accion("tg-ubicacion", lambda: self.tg_chat_msg_funcion("enviar_ubicacion"))
        accion("tg-contacto", lambda: self.tg_chat_msg_funcion("enviar_contacto"))
        accion("tg-carpetas", self.tg_carpetas)
        accion("tg-crear-chat", self.tg_crear_chat)
        accion("tg-unirse-enlace", self.tg_unirse_enlace)
        accion("tg-buscar-global", self.tg_buscar_global)
        accion("tg-buscar-publicaciones", self.tg_buscar_publicaciones)
        accion("tg-frecuentes", self.tg_frecuentes)
        accion("tg-buscar-chat", lambda: self.tg_chat_funcion("buscar_en_chat"))
        accion("tg-medios", lambda: self.tg_chat_funcion("medios_del_chat"))
        accion("tg-similares", lambda: self.tg_chat_funcion("canales_similares", self._tg_abrir_por_id))
        for nombre, clase in (("tg-miembros", "DialogoMiembrosTG"), ("tg-enlaces", "DialogoEnlacesTG"),
                              ("tg-solicitudes", "DialogoSolicitudesTG"), ("tg-ajustes", "DialogoAjustesChatTG"),
                              ("tg-temas", "DialogoTemasTG")):
            accion(nombre, lambda c=clase: self.tg_admin_dialogo(c))
        accion("tg-registro-admin", lambda: self.tg_admin_funcion("registro_admin"))
        accion("tg-grupo-comentarios", lambda: self.tg_admin_funcion("grupo_de_comentarios"))
        accion("tg-vaciar", self.tg_vaciar)
        accion("tg-enviar-como", self.tg_enviar_como)
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("tg-chat-nuevo", self.tg_chat_nuevo)
        a("tg-guardados", self.tg_guardados)
        a("tg-enviar-archivo", self._por_red("enviar_archivo"), ["<Ctrl><Alt>u"])
        a("tg-reaccionar", self._por_red("reaccionar"), ["<Ctrl><Alt>x"])
        a("tg-quien-reacciono", self.tg_quien_reacciono)
        a("tg-reenviar", self.tg_reenviar, ["<Ctrl><Alt>t"])
        a("tg-copiar", self._por_red("copiar"), ["<Ctrl><Alt>c"])
        a("tg-descargar", self._por_red("descargar"), ["<Ctrl><Alt>d"])
        a("tg-abrir-adjunto", self._por_red("abrir_adjunto"), ["<Ctrl><Alt>o"])
        a("tg-silenciar", lambda: self._tg_alternar("silenciar"))
        a("tg-fijar", lambda: self._tg_alternar("fijar"))
        a("tg-archivar", lambda: self._tg_alternar("archivar"))
        a("tg-leido", lambda: self.tg_marcar_leido(True))
        a("tg-no-leido", lambda: self.tg_marcar_leido(False))
        a("tg-todo-leido", self.tg_marcar_todo_leido)
        a("tg-info", self.tg_info)
        a("tg-credenciales", self.tg_credenciales)
        a("tg-eliminar-cuenta", self.tg_eliminar_cuenta)
