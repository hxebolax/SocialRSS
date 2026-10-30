# -*- coding: utf-8 -*-
"""Matrix en la ventana principal (mixin de VentanaPrincipal).

Cada cuenta tiene los buffers Salas, Mensajes directos e Invitaciones. Intro sobre una sala abre su conversación como un
buffer más (Ctrl+W lo cierra). Los mensajes, las invitaciones y la verificación de dispositivos llegan en tiempo real por
el bus de eventos del servicio."""

import logging
import os
import time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.plataforma import llamar_en_ui, mostrar_aviso_dialogo
from app.core.speech import copiar_al_portapapeles, speak
from app.services.matrix.matrix_common import HAS_NIO

from . import datos_matrix
from .dialogos import DialogoElegir, DialogoTexto, confirmar_async, ejecutar

log = logging.getLogger(__name__)

ANUNCIAR_MAX = 3
ANTIRREBOTE_MS = 700
REACCIONES = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👎", "🔥", "🎉", "🤔"]
NIVELES = [("all", "Avisar de todo"), ("mentions", "Solo cuando me mencionen"), ("none", "Silencio (sin sonidos)")]
ETIQUETAS = [("favorita", "Favorita (arriba del todo)"), ("baja", "Baja prioridad (abajo del todo)"), ("", "Sin etiqueta")]
EVENTOS_LISTA = ("mensaje", "invitacion", "recibo_lectura", "conectado", "desconectado", "sincronizada", "sync")


def _servicio():
    return datos_matrix.servicio()


class _MatrixMixin:
    # ------------------------------------------------------------------ contexto
    def _mx_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de Matrix."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "matrix"
        n = self.nodo_actual
        return n is not None and n.servicio == "matrix"

    def _mx_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "matrix" and n.account_id:
            return n.account_id
        cuentas = datos_matrix.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de Matrix"))
        return None

    def _mx_disponible(self) -> bool:
        if not HAS_NIO:
            self.avisar(_("Falta matrix-nio. Instálalo con: pip install matrix-nio"))
            return False
        return True

    def _mx_conectada(self, account_id: int, avisar: bool = True) -> bool:
        if datos_matrix.conectada(account_id):
            return True
        if avisar:
            self.avisar(_("La cuenta no está conectada. Conéctala desde el menú Matrix."))
        return False

    def _mx_chat_abierto(self):
        """(account_id, room_id) de la conversación abierta, o None."""
        n = self.nodo_actual
        if n is not None and n.servicio == "matrix":
            room = datos_matrix.sala_de_chat(n.timeline)
            if room is not None:
                return n.account_id, room
        return None

    def _mx_sala_de_fila(self, avisar: bool = True):
        """(account_id, room_id) de la sala enfocada (una sala o un mensaje) o de la conversación abierta."""
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "matrix" and obj.fila.extra.get("room_id"):
            return obj.fila.extra["account_id"], obj.fila.extra["room_id"]
        abierto = self._mx_chat_abierto()
        if abierto:
            return abierto
        if avisar:
            self.avisar(_("Selecciona una sala de Matrix"))
        return None

    def _mx_mensaje(self, propio: bool = False, avisar: bool = True):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "matrix" or obj.fila.extra.get("tipo") != "mensaje":
            if avisar:
                self.avisar(_("Selecciona un mensaje de Matrix"))
            return None
        if propio and not obj.fila.extra.get("propio"):
            self.avisar(_("Solo puedes hacer esto con tus propios mensajes"))
            return None
        return obj

    def _mx_evento_real(self, obj) -> str:
        """event_id del mensaje si ya está en el servidor (no uno local sin enviar)."""
        eid = obj.fila.extra.get("event_id") or ""
        if not eid or eid.startswith("local:"):
            self.avisar(_("Este mensaje no está en el servidor"))
            return ""
        return eid

    def _mx_aviso_error(self, texto: str):
        return lambda r, e: self.avisar(_(texto).format(e=e)) if e else None

    # ------------------------------------------------------------------ abrir y actualizar
    def mx_actualizar_nodo(self, nodo, silencioso: bool = False):
        """Los datos llegan solos; actualizar relee la lista (y en una conversación pide el historial que falte)."""
        if nodo is not self.nodo_actual:
            return
        self._mx_refrescar_lista(nodo.account_id)
        if not silencioso and not datos_matrix.conectada(nodo.account_id):
            self.avisar(_("La cuenta no está conectada. Conéctala desde el menú Matrix."))

    def mx_cargar_mas(self):
        abierto = self._mx_chat_abierto()
        if not abierto:
            self.avisar(_("Abre antes una conversación"))
            return
        if not self._mx_conectada(abierto[0]):
            return
        acc, room = abierto
        actual = self._fila_actual()
        self.avisar(_("Cargando mensajes anteriores…"))

        def _fin(n, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            self.avisar(_("{n} mensajes anteriores añadidos").format(n=n) if n else _("No hay más mensajes anteriores"))
        ejecutar(lambda: _servicio().cargar_anteriores(acc, room), _fin)

    def mx_abrir_fila(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "matrix":
            self.avisar(_("Selecciona una sala de la lista"))
            return
        x = obj.fila.extra
        if x.get("tipo") == "sala":
            self.mx_abrir_sala(x["account_id"], x["room_id"], x["nombre"])
        elif x.get("tipo") == "invitacion":
            self.mx_responder_invitacion(x["account_id"], x["room_id"], x["nombre"])
        elif x.get("tipo") == "mensaje":
            self.mx_activar_mensaje(obj)

    def mx_abrir_sala(self, account_id: int, room_id: str, nombre: str = ""):
        if not self._mx_conectada(account_id):
            return
        self._abrir_buffer(account_id, datos_matrix.clave_chat(room_id), nombre or datos_matrix.titulo_sala(account_id, room_id),
                           servicio="matrix")

    def mx_activar_mensaje(self, obj):
        """Intro sobre un mensaje: abre su enlace (o los enlaces, a elegir) o la ubicación."""
        x = obj.fila.extra
        if (x.get("texto") or "").startswith(_("[Ubicación:")):
            return self.mx_abrir_ubicacion(obj)
        urls = datos_matrix.urls_de(x.get("texto", ""))
        if not urls:
            self.avisar(_("Este mensaje no tiene ningún enlace"))
            return
        from app.core.privacy import abrir_url, limpiar_url
        if len(urls) == 1:
            self.avisar(_("Abriendo el enlace"))
            abrir_url(limpiar_url(urls[0]))
            return
        DialogoElegir(self, _("Abrir un enlace"), _("_Enlace:"), urls,
                      lambda i: abrir_url(limpiar_url(urls[i])), aceptar=_("_Abrir")).present()

    def mx_abrir_ubicacion(self, obj=None):
        obj = obj or self._mx_mensaje()
        if obj is None:
            return
        import re
        m = re.search(r"geo:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", obj.fila.extra.get("texto", ""))
        if not m:
            self.avisar(_("Este mensaje no tiene una ubicación"))
            return
        from app.core.privacy import abrir_url
        abrir_url(f"https://www.openstreetmap.org/?mlat={m.group(1)}&mlon={m.group(2)}#map=16/{m.group(1)}/{m.group(2)}")

    def mx_responder_invitacion(self, acc: int, room_id: str, nombre: str):
        confirmar_async(self, _("Te han invitado a {n}. ¿Aceptar?").format(n=nombre),
                        lambda: self._mx_unirse_a(acc, room_id), titulo=_("Invitación a una sala"), etiqueta_si=_("Aceptar"))

    def mx_rechazar(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "matrix" or obj.fila.extra.get("tipo") != "invitacion":
            self.avisar(_("Selecciona una invitación"))
            return
        x = obj.fila.extra
        self._mx_salir_de(x["account_id"], x["room_id"], x["nombre"], rechazo=True)

    def _mx_unirse_a(self, acc: int, room_id: str):
        if not self._mx_conectada(acc):
            return
        ejecutar(lambda: _servicio().unirse_sala(acc, room_id),
                 lambda r, e: (self.avisar(_("No se pudo entrar: {e}").format(e=e) if e else _("Dentro de la sala")),
                               self._mx_refrescar_lista(acc)))

    # ------------------------------------------------------------------ escribir
    def mx_nuevo(self):
        """Ctrl+N: escribe en la sala abierta o enfocada; en otro caso, pide unirse a una sala."""
        if not self._mx_disponible():
            return
        datos = self._mx_sala_de_fila(avisar=False)
        if datos and not datos_matrix.info_sala(*datos)["invitacion"]:
            return self.mx_escribir(*datos)
        self.mx_unirse()

    def mx_escribir(self, account_id: int, room_id: str, inicial: str = ""):
        if not self._mx_conectada(account_id):
            return
        from .dialogos_matrix import DialogoMensajeMatrix
        DialogoMensajeMatrix(self, _("Mensaje a {n}").format(n=datos_matrix.titulo_sala(account_id, room_id)),
                             lambda texto: self._mx_enviar(account_id, room_id, texto), texto_inicial=inicial).present()

    def _mx_enviar(self, account_id: int, room_id: str, texto: str, reply_to: str = "", editar: str = ""):
        emote = False
        if texto.startswith("/me ") and len(texto) > 4:
            texto, emote = texto[4:].strip(), True
        elif texto.startswith("//"):
            texto = texto[1:]

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo enviar: {e}").format(e=err))
            self._mx_refrescar_chat(account_id, room_id, ir_al_final=not editar)
        ejecutar(lambda: _servicio().enviar_mensaje(account_id, room_id, texto, emote=emote, reply_to=reply_to,
                                                    editar_event=editar), _fin)

    def mx_responder(self):
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        x = obj.fila.extra
        eid = self._mx_evento_real(obj)
        if not eid:
            return
        acc, room = x["account_id"], x["room_id"]
        cita = f"{obj.fila.autor}: {x.get('texto') or ''}"[:200]
        from .dialogos_matrix import DialogoMensajeMatrix
        DialogoMensajeMatrix(self, _("Responder"), lambda texto: self._mx_enviar(acc, room, texto, reply_to=eid), cita=cita,
                             aceptar=_("_Responder")).present()

    def mx_editar(self):
        """Ctrl+Alt+E: edita tu mensaje enfocado o, si no, el último que enviaste en la sala abierta."""
        obj = self._mx_mensaje(avisar=False)
        if obj is not None and obj.fila.extra.get("propio") and obj.fila.extra.get("tipo_msg") == "msg":
            x = obj.fila.extra
            acc, room, eid, texto = x["account_id"], x["room_id"], x["event_id"], x.get("texto", "")
        else:
            datos = self._mx_sala_de_fila()
            if not datos:
                return
            acc, room = datos
            f = db.query_one("SELECT event_id, texto FROM matrix_mensajes WHERE account_id = ? AND room_id = ? AND es_propio = 1 "
                             "AND tipo = 'msg' AND event_id NOT LIKE 'local:%' ORDER BY id DESC LIMIT 1", (acc, room))
            if not f:
                self.avisar(_("No has enviado ningún mensaje que editar"))
                return
            eid, texto = f
        if not self._mx_conectada(acc) or not eid or eid.startswith("local:"):
            return
        from .dialogos_matrix import DialogoMensajeMatrix
        DialogoMensajeMatrix(self, _("Editar mi mensaje"), lambda t: self._mx_enviar(acc, room, t, editar=eid),
                             texto_inicial=texto, aceptar=_("_Guardar")).present()

    def mx_borrar(self):
        """Supr: en un mensaje, lo borra (el tuyo, o cualquiera si eres moderador); en una sala, sale de ella."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "matrix":
            return
        x = obj.fila.extra
        acc, room = x["account_id"], x.get("room_id")
        if x.get("tipo") == "invitacion":
            return self.mx_rechazar()
        if x.get("tipo") == "sala":
            return self.mx_salir(False)
        if x.get("tipo") != "mensaje":
            return
        eid = x.get("event_id") or ""
        if eid.startswith("local:"):
            return self.mx_descartar()
        if not self._mx_conectada(acc) or not eid:
            return

        def _borrar():
            ejecutar(lambda: _servicio().borrar_mensaje(acc, room, eid),
                     lambda r, e: (self.avisar(_("No se pudo (¿tienes permiso?): {e}").format(e=e) if e else _("Mensaje borrado")),
                                   self._mx_refrescar_chat(acc, room)))
        confirmar_async(self, _("¿Borrar este mensaje para todos?") if x.get("propio")
                        else _("¿Borrar este mensaje de la sala? Necesitas ser moderador/a."), _borrar, etiqueta_si=_("Borrar"))

    def mx_reaccionar(self):
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        eid = self._mx_evento_real(obj)
        if not eid:
            return
        x = obj.fila.extra
        acc, room = x["account_id"], x["room_id"]

        def _poner(i):
            ejecutar(lambda: _servicio().reaccionar(acc, room, eid, REACCIONES[i]),
                     lambda r, e: (self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Reacción enviada")),
                                   self._mx_refrescar_chat(acc, room)))
        DialogoElegir(self, _("Reaccionar"), _("_Emoji:"), REACCIONES, _poner, aceptar=_("_Reaccionar")).present()

    def mx_quitar_reaccion(self):
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        eid = self._mx_evento_real(obj)
        if not eid:
            return
        x = obj.fila.extra
        acc, room = x["account_id"], x["room_id"]

        def _quitar(i):
            ejecutar(lambda: _servicio().quitar_reaccion(acc, room, eid, REACCIONES[i]),
                     lambda r, e: (self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Reacción quitada")),
                                   self._mx_refrescar_chat(acc, room)))
        DialogoElegir(self, _("Quitar mi reacción"), _("_Emoji que pusiste:"), REACCIONES, _quitar, aceptar=_("_Quitar")).present()

    def mx_copiar(self):
        obj = self._mx_mensaje()
        if obj is None:
            return
        copiar_al_portapapeles(obj.fila.extra.get("texto") or obj.fila.titulo)
        self.avisar(_("Texto copiado"))

    def mx_mencionar(self):
        obj = self._mx_mensaje()
        if obj is None:
            return
        x = obj.fila.extra
        if x.get("propio") or not x.get("usuario"):
            self.avisar(_("Selecciona un mensaje de otra persona"))
            return
        self.mx_escribir(x["account_id"], x["room_id"], inicial=f"{x['usuario']}: ")

    def mx_hilo(self):
        """Abre el hilo del mensaje (su raíz si ya está en uno; él mismo si va a empezarlo)."""
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        x = obj.fila.extra
        raiz = x.get("thread_root") or self._mx_evento_real(obj)
        if not raiz:
            return
        from .dialogos_matrix import DialogoHiloMatrix
        acc, room = x["account_id"], x["room_id"]
        DialogoHiloMatrix(self, acc, room, raiz, datos_matrix.titulo_sala(acc, room),
                          al_cambiar=lambda: self._mx_refrescar_chat(acc, room)).present()

    def mx_quien_leyo(self):
        obj = self._mx_mensaje()
        if obj is None:
            return
        x = obj.fila.extra
        eid = self._mx_evento_real(obj)
        if not eid:
            return
        ejecutar(lambda: _servicio().quien_ha_leido(x["account_id"], x["room_id"], eid, obj.fila.fecha or ""),
                 lambda nombres, e: self.avisar(_("No se pudo: {e}").format(e=e)) if e
                 else self.avisar((_("Leído por: {n}").format(n=", ".join(nombres[:6])) if len(nombres) <= 6
                                   else _("Leído por {c} personas: {n}…").format(c=len(nombres), n=", ".join(nombres[:6])))
                                  if nombres else _("Todavía nadie ha marcado como leído hasta aquí")))

    def mx_fijar_mensaje(self):
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        eid = self._mx_evento_real(obj)
        if not eid:
            return
        x = obj.fila.extra
        acc, room = x["account_id"], x["room_id"]

        def _hacer():
            fijados = _servicio().eventos_fijados(acc, room)
            fijar = eid not in fijados
            _servicio().fijar_evento(acc, room, eid, fijar)
            return fijar
        ejecutar(_hacer, lambda fijar, e: self.avisar(_("No se pudo (¿tienes permiso?): {e}").format(e=e) if e
                                                      else (_("Mensaje fijado") if fijar else _("Mensaje desfijado"))))

    def mx_reportar_mensaje(self):
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        eid = self._mx_evento_real(obj)
        if not eid:
            return
        x = obj.fila.extra
        from .dialogos_irc import DialogoMotivoIRC
        DialogoMotivoIRC(self, _("Reportar el mensaje"),
                         lambda motivo: ejecutar(lambda: _servicio().reportar_evento(x["account_id"], x["room_id"], eid, motivo),
                                                 lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e
                                                                          else _("Mensaje reportado a los administradores")))).present()

    def mx_solicitar_claves(self):
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        eid = self._mx_evento_real(obj)
        if not eid:
            return
        x = obj.fila.extra
        ejecutar(lambda: _servicio().solicitar_claves_mensaje(x["account_id"], x["room_id"], eid),
                 lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e
                                          else _("He pedido las claves a tus otros dispositivos; si alguno está en línea, el "
                                                 "mensaje se descifrará solo en unos segundos")))

    def mx_ignorar(self):
        obj = self._mx_mensaje()
        if obj is None or not self._mx_conectada(obj.fila.extra["account_id"]):
            return
        x = obj.fila.extra
        if x.get("propio") or not x.get("sender"):
            self.avisar(_("Selecciona un mensaje de otra persona"))
            return
        quien = x.get("usuario") or x["sender"]
        confirmar_async(self, _("¿Ignorar a {n}? No volverás a ver sus mensajes en ninguno de tus clientes.").format(n=quien),
                        lambda: ejecutar(lambda: _servicio().ignorar_usuario(x["account_id"], x["sender"], True),
                                         lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e
                                                                  else _("Ahora ignoras a {n}").format(n=quien))),
                        etiqueta_si=_("Ignorar"))

    def mx_reintentar(self):
        obj = self._mx_mensaje()
        if obj is None or obj.fila.extra.get("send_state") != "fallido":
            self.avisar(_("Este mensaje no tiene un envío fallido"))
            return
        x = obj.fila.extra
        acc, room = x["account_id"], x["room_id"]
        ejecutar(lambda: _servicio().reintentar_envio(acc, room, x["event_id"], x["texto"], emote=x.get("tipo_msg") == "accion",
                                                      reply_to=x.get("reply_to", ""), thread_root=x.get("thread_root", "")),
                 lambda r, e: (self.avisar(_("No se pudo enviar: {e}").format(e=e) if e else _("Mensaje enviado")),
                               self._mx_refrescar_chat(acc, room)))

    def mx_descartar(self):
        obj = self._mx_mensaje()
        if obj is None or not (obj.fila.extra.get("event_id") or "").startswith("local:"):
            self.avisar(_("Este mensaje no es uno sin enviar"))
            return
        x = obj.fila.extra
        _servicio().descartar_mensaje_local(x["account_id"], x["event_id"])
        self.avisar(_("Mensaje descartado"))
        self._mx_refrescar_chat(x["account_id"], x["room_id"])

    # ------------------------------------------------------------------ encuestas y ubicación
    def mx_crear_encuesta(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        from .dialogos_matrix import DialogoEncuestaMatrix

        def _crear(pregunta, opciones, oculta):
            ejecutar(lambda: _servicio().crear_encuesta(acc, room, pregunta, opciones, oculta),
                     lambda r, e: (self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Encuesta enviada")),
                                   self._mx_refrescar_chat(acc, room, ir_al_final=True)))
        DialogoEncuestaMatrix(self, _crear).present()

    def mx_votar(self) -> bool:
        """Ctrl+Alt+V sobre una encuesta de Matrix. True si la fila era de Matrix."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "matrix":
            return False
        x = obj.fila.extra
        poll = datos_matrix.encuesta(x.get("poll_json", "")) if x.get("tipo") == "mensaje" else None
        if not poll:
            self.avisar(_("Este mensaje no es una encuesta"))
            return True
        if poll.get("cerrada"):
            self.avisar(_("La encuesta está cerrada"))
            return True
        if not self._mx_conectada(x["account_id"]):
            return True
        ids, textos = list(poll["opciones"].keys()), list(poll["opciones"].values())
        acc, room, eid = x["account_id"], x["room_id"], x["event_id"]

        def _votar(i):
            ejecutar(lambda: _servicio().votar_encuesta(acc, room, eid, ids[i]),
                     lambda r, e: (self.avisar(_("No se pudo votar: {e}").format(e=e) if e else _("Voto enviado")),
                                   self._mx_refrescar_chat(acc, room)))
        DialogoElegir(self, _("Votar: {p}").format(p=poll.get("pregunta", "")), _("_Opción:"), textos, _votar,
                      aceptar=_("_Votar")).present()
        return True

    def mx_cerrar_encuesta(self):
        obj = self._mx_mensaje(propio=True)
        if obj is None:
            return
        x = obj.fila.extra
        if not datos_matrix.encuesta(x.get("poll_json", "")):
            self.avisar(_("Este mensaje no es una encuesta"))
            return
        if not self._mx_conectada(x["account_id"]):
            return
        acc, room, eid = x["account_id"], x["room_id"], x["event_id"]
        confirmar_async(self, _("¿Cerrar la encuesta? Nadie más podrá votar."),
                        lambda: ejecutar(lambda: _servicio().cerrar_encuesta(acc, room, eid),
                                         lambda r, e: (self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Encuesta cerrada")),
                                                       self._mx_refrescar_chat(acc, room))), etiqueta_si=_("Cerrar"))

    def mx_enviar_ubicacion(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        from .dialogos_matrix import DialogoUbicacionMatrix
        DialogoUbicacionMatrix(self, lambda lat, lon, desc: ejecutar(
            lambda: _servicio().enviar_ubicacion(acc, room, lat, lon, desc),
            lambda r, e: (self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Ubicación enviada")),
                          self._mx_refrescar_chat(acc, room, ir_al_final=True)))).present()

    # ------------------------------------------------------------------ adjuntos
    def mx_enviar_archivo(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        dlg = Gtk.FileDialog(title=_("Elige el archivo a enviar"))

        def _fin(origen, res):
            try:
                ruta = origen.open_finish(res).get_path()
            except Exception:
                return
            self.avisar(_("Subiendo {f}…").format(f=os.path.basename(ruta)))
            ejecutar(lambda: _servicio().enviar_archivo(acc, room, ruta),
                     lambda r, e: (self.avisar(_("No se pudo enviar: {e}").format(e=e) if e else _("Archivo enviado")),
                                   self._mx_refrescar_chat(acc, room, ir_al_final=True)))
        dlg.open(self, None, _fin)

    def _mx_adjunto(self):
        obj = self._mx_mensaje()
        if obj is None:
            return None
        x = obj.fila.extra
        if not x.get("media_mxc"):
            self.avisar(_("Este mensaje no tiene un archivo"))
            return None
        return obj, x, datos_matrix.nombre_adjunto(x["texto"], x["media_mxc"])

    def mx_descargar(self):
        r = self._mx_adjunto()
        if not r or not self._mx_conectada(r[1]["account_id"]):
            return
        obj, x, nombre = r
        titulo = datos_matrix.titulo_sala(x["account_id"], x["room_id"])
        self.avisar(_("Descargando…"))
        ejecutar(lambda: _servicio().descargar_a_descargas(x["account_id"], x["media_mxc"], nombre, titulo,
                                                            media_crypto=x.get("media_crypto") or ""),
                 lambda ruta, e: self.avisar(_("No se pudo descargar: {e}").format(e=e) if e
                                             else _("Descargado en {r}").format(r=ruta)))

    def mx_abrir_adjunto(self):
        r = self._mx_adjunto()
        if not r or not self._mx_conectada(r[1]["account_id"]):
            return
        obj, x, nombre = r
        from app.services.matrix.matrix_medios import es_extension_activa
        if es_extension_activa(nombre):       # un ejecutable o script nunca se abre desde la aplicación
            self.avisar(_("Por seguridad, «{n}» no se abre desde la aplicación. Se descargará a tu carpeta de descargas.").format(n=nombre))
            return self.mx_descargar()

        def _fin(ruta, err):
            if err or not ruta:
                self.avisar(_("No se pudo abrir: {e}").format(e=err or ""))
                return
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(ruta).get_uri(), None)
                self.avisar(_("Abriendo el archivo"))
            except GLib.Error as e:
                self.avisar(_("Descargado en {r}, pero no se pudo abrir: {e}").format(r=ruta, e=e.message))
        self.avisar(_("Descargando…"))
        ejecutar(lambda: _servicio().descargar_media(x["account_id"], x["media_mxc"], nombre,
                                                      media_crypto=x.get("media_crypto") or ""), _fin)

    def mx_reproducir(self) -> bool:
        """F2: reproduce un audio o un vídeo. True si la fila era reproducible."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "matrix" or not obj.fila.extra.get("reproducible"):
            return False
        x = obj.fila.extra
        if not self._mx_conectada(x["account_id"]):
            return True
        nombre = datos_matrix.nombre_adjunto(x["texto"], x["media_mxc"])
        titulo = f"{obj.fila.autor}: {nombre}"
        self.avisar(_("Cargando…"))

        def _sonar(ruta, err):
            if err or not ruta:
                self.avisar(_("No se pudo reproducir: {e}").format(e=err or ""))
                return
            from app.services.media_player.player2_service import KIND_PODCAST, player2_service
            player2_service.cargar_y_reproducir(ruta, titulo, KIND_PODCAST, resume=False,
                                                callback=lambda ok: self.avisar(_("Reproduciendo: {t}").format(t=titulo) if ok
                                                                                 else _("No se pudo reproducir")))
        ejecutar(lambda: _servicio().descargar_media(x["account_id"], x["media_mxc"], nombre,
                                                      media_crypto=x.get("media_crypto") or ""), _sonar)
        return True

    # ------------------------------------------------------------------ salas
    def mx_unirse(self, sala: str = ""):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_disponible() or not self._mx_conectada(acc):
            return
        from .dialogos_matrix import DialogoUnirseMatrix
        DialogoUnirseMatrix(self, acc, lambda room_id: (self._mx_refrescar_lista(acc), self.mx_abrir_sala(acc, room_id)),
                            sala).present()

    def mx_crear_sala(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        from .dialogos_matrix import DialogoCrearSalaMatrix
        DialogoCrearSalaMatrix(self, acc, lambda room_id: (self._mx_refrescar_lista(acc), self.mx_abrir_sala(acc, room_id))).present()

    def mx_mensaje_directo(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        DialogoTexto(self, _("Mensaje directo"), _("_Usuario (@nombre:servidor):"), lambda uid: self.mx_dm_con(acc, uid),
                     aceptar=_("_Abrir")).present()

    def mx_dm_con(self, acc: int, uid: str):
        def _fin(room_id, err):
            if err:
                self.avisar(_("No se pudo abrir el directo: {e}").format(e=err))
                return
            self._mx_refrescar_lista(acc)
            self.mx_abrir_sala(acc, room_id, uid)
        ejecutar(lambda: _servicio().crear_dm(acc, uid), _fin)

    def mx_directorio(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        from .dialogos_matrix import DialogoDirectorioMatrix
        DialogoDirectorioMatrix(self, acc, lambda sala: self.mx_unirse(sala)).present()

    def mx_llamar_puerta(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return

        def _sala(sala):
            from .dialogos_irc import DialogoMotivoIRC
            DialogoMotivoIRC(self, _("Motivo para los moderadores"), lambda motivo: ejecutar(
                lambda: _servicio().llamar_puerta(acc, sala, motivo),
                lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e
                                         else _("Solicitud enviada: cuando te acepten, la sala aparecerá en la lista")))).present()
        DialogoTexto(self, _("Llamar a la puerta de una sala"), _("_Sala (#sala:servidor):"), _sala,
                     aceptar=_("_Continuar")).present()

    def mx_crear_espacio(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        DialogoTexto(self, _("Crear un espacio"), _("_Nombre del espacio:"), lambda n: ejecutar(
            lambda: _servicio().crear_espacio(acc, n),
            lambda r, e: (self.avisar(_("No se pudo crear el espacio: {e}").format(e=e) if e
                                      else _("Espacio creado. Añade salas con «Añadir la sala a un espacio»")),
                          self._mx_refrescar_lista(acc))), aceptar=_("_Crear")).present()

    def mx_salir(self, olvidar: bool):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        self._mx_salir_de(datos[0], datos[1], datos_matrix.titulo_sala(*datos), olvidar=olvidar)

    def _mx_salir_de(self, acc: int, room_id: str, nombre: str, olvidar: bool = False, rechazo: bool = False):
        def _hacer():
            ejecutar(lambda: _servicio().salir_sala(acc, room_id, olvidar=olvidar),
                     lambda r, e: (self.avisar(_("No se pudo: {e}").format(e=e) if e
                                               else (_("Invitación rechazada") if rechazo else _("Has salido de {n}").format(n=nombre))),
                                   self._mx_refrescar_lista(acc)))
        texto = (_("¿Rechazar la invitación a {n}?") if rechazo else _("¿Salir de {n} y olvidarla (se borra su historial de aquí)?")
                 if olvidar else _("¿Salir de {n}?")).format(n=nombre)
        confirmar_async(self, texto, _hacer, etiqueta_si=_("Rechazar") if rechazo else _("Salir"))

    def mx_etiqueta(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        actual = datos_matrix.info_sala(acc, room)["etiqueta"]

        def _poner(i):
            clave = ETIQUETAS[i][0]
            ejecutar(lambda: _servicio().fijar_etiqueta_sala(acc, room, clave),
                     lambda r, e: (self.avisar(_("No se pudo etiquetar: {e}").format(e=e) if e else _("Etiqueta guardada")),
                                   self._mx_refrescar_lista(acc)))
        d = DialogoElegir(self, _("Etiqueta de la sala"), _("_Etiqueta:"), [_(t[1]) for t in ETIQUETAS], _poner, aceptar=_("_Guardar"))
        d.combo.set_selected(next((i for i, t in enumerate(ETIQUETAS) if t[0] == actual), 2))
        d.present()

    def mx_nivel_aviso(self):
        datos = self._mx_sala_de_fila()
        if not datos:
            return
        acc, room = datos
        actual = datos_matrix.info_sala(acc, room)["aviso"]

        def _elegido(i):
            ejecutar(lambda: _servicio().fijar_nivel_aviso(acc, room, NIVELES[i][0]),
                     lambda r, e: self.avisar(_("Nivel de aviso: {n}").format(n=_(NIVELES[i][1]))))
        d = DialogoElegir(self, _("Nivel de aviso"), _("_Avisar:"), [_(n[1]) for n in NIVELES], _elegido, aceptar=_("_Guardar"))
        d.combo.set_selected(next((i for i, n in enumerate(NIVELES) if n[0] == actual), 0))
        d.present()

    def mx_marcar_leido(self, leido: bool = True):
        datos = self._mx_sala_de_fila()
        if not datos:
            return
        acc, room = datos
        if leido:
            self._mx_leer_sala(acc, room)
        else:
            datos_matrix.marcar_no_leido(acc, room)
        self._mx_refrescar_lista(acc)
        self.avisar(_("Marcada como leída") if leido else _("Marcada como no leída"))

    def mx_marcar_todo_leido(self):
        acc = self._mx_cuenta()
        if acc is None:
            return
        datos_matrix.marcar_todo_leido(acc)
        self._mx_refrescar_lista(acc)
        self.avisar(_("Todo marcado como leído"))

    def mx_miembros(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        from .dialogos_matrix import DialogoMiembrosMatrix
        DialogoMiembrosMatrix(self, acc, room, datos_matrix.titulo_sala(acc, room),
                              al_abrir_dm=lambda uid: self.mx_dm_con(acc, uid)).present()

    def mx_hilos(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        from .dialogos_matrix import DialogoHilosMatrix
        DialogoHilosMatrix(self, datos[0], datos[1], datos_matrix.titulo_sala(*datos)).present()

    def mx_fijados(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        from .dialogos_matrix import DialogoFijadosMatrix
        DialogoFijadosMatrix(self, datos[0], datos[1], datos_matrix.titulo_sala(*datos)).present()

    def mx_reportar_sala(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        from .dialogos_irc import DialogoMotivoIRC
        DialogoMotivoIRC(self, _("Reportar la sala"), lambda motivo: ejecutar(
            lambda: _servicio().reportar_sala(acc, room, motivo),
            lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Sala reportada a los administradores")))).present()

    def mx_dispositivos_sala(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        from .dialogos_matrix import DialogoDispositivosSalaMatrix
        DialogoDispositivosSalaMatrix(self, datos[0], datos[1], datos_matrix.titulo_sala(*datos)).present()

    def mx_ajustes(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        if not _servicio().soy_admin_de_sala(acc, room):
            self.avisar(_("No tienes permiso para cambiar los ajustes de esta sala"))
            return
        from .dialogos_matrix import DialogoAjustesSalaMatrix
        DialogoAjustesSalaMatrix(self, acc, room, datos_matrix.titulo_sala(acc, room)).present()

    def mx_permitir_llamar(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        ejecutar(lambda: _servicio().permitir_llamar_puerta(acc, room),
                 lambda r, e: self.avisar(_("No se pudo (¿eres administrador/a?): {e}").format(e=e) if e
                                          else _("Ahora se puede llamar a la puerta de esta sala")))

    def mx_avatar_sala(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        dlg = Gtk.FileDialog(title=_("Elige la imagen del avatar de la sala"))

        def _fin(origen, res):
            try:
                ruta = origen.open_finish(res).get_path()
            except Exception:
                return
            self.avisar(_("Subiendo el avatar…"))
            ejecutar(lambda: _servicio().cambiar_avatar_sala(acc, room, ruta),
                     lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Avatar de la sala cambiado")))
        dlg.open(self, None, _fin)

    def mx_a_espacio(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        espacios = [e for e in _servicio().mis_espacios(acc) if e[0] != room]
        if not espacios:
            self.avisar(_("No tienes ningún espacio. Crea uno desde el menú Matrix."))
            return

        def _elegido(i):
            ejecutar(lambda: _servicio().sala_a_espacio(acc, espacios[i][0], room),
                     lambda r, e: self.avisar(_("No se pudo (¿tienes permiso en el espacio?): {e}").format(e=e) if e
                                              else _("Sala añadida al espacio")))
        DialogoElegir(self, _("Añadir la sala a un espacio"), _("_Espacio:"), [e[1] or e[0] for e in espacios], _elegido,
                      aceptar=_("_Añadir")).present()

    def mx_ver_espacio(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        if not datos_matrix.info_sala(acc, room)["espacio"]:
            self.avisar(_("Esta sala no es un espacio"))
            return

        def _trabajo():
            try:
                return _servicio().jerarquia_espacio(acc, room)
            except Exception:
                return _servicio().salas_de_espacio(acc, room)

        def _fin(salas, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            if not salas:
                self.avisar(_("Este espacio no tiene salas"))
                return
            from .dialogos_matrix import DialogoOpcionesMatrix
            textos = [s["nombre"] + (_(", {m} miembros").format(m=s["miembros"]) if s.get("miembros") else "") for s in salas]
            DialogoOpcionesMatrix(self, _("Salas del espacio"), _("Lista de salas del espacio"), textos,
                                  lambda i: self._mx_unirse_a(acc, salas[i]["room_id"]), accion=_("_Unirme")).present()
        ejecutar(_trabajo, _fin)

    def mx_exportar(self):
        datos = self._mx_sala_de_fila()
        if not datos:
            return
        acc, room = datos
        seguro = "".join(c for c in datos_matrix.titulo_sala(acc, room) if c.isalnum() or c in "-_") or "sala"
        dlg = Gtk.FileDialog(title=_("Guardar conversación"), initial_name=f"matrix_{seguro}.txt")

        def _fin(origen, res):
            try:
                ruta = origen.save_finish(res).get_path()
            except Exception:
                return
            ejecutar(lambda: _servicio().exportar_sala(acc, room, ruta),
                     lambda n, e: self.avisar(_("No se pudo exportar: {e}").format(e=e) if e
                                              else _("Conversación exportada ({n} mensajes)").format(n=n)))
        dlg.save(self, None, _fin)

    def mx_buscar(self):
        acc = self._mx_cuenta()
        if acc is None:
            return

        def _buscar(q):
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo buscar: {e}").format(e=err))
                    return
                if not res:
                    self.avisar(_("No hay mensajes que coincidan"))
                    return
                from .dialogos_matrix import DialogoOpcionesMatrix
                textos = [f"{datos_matrix.titulo_sala(acc, r['room_id'])}, {r['quien']}: {' '.join((r['texto'] or '').split())[:200]}"
                          for r in res]
                DialogoOpcionesMatrix(self, _("Resultados de la búsqueda"), _("Lista de mensajes encontrados"), textos,
                                      lambda i: self.mx_abrir_sala(acc, res[i]["room_id"])).present()
            ejecutar(lambda: _servicio().buscar_mensajes_fts(acc, q), _fin)
        DialogoTexto(self, _("Buscar en los mensajes guardados"), _("_Buscar:"), _buscar, aceptar=_("_Buscar")).present()

    def mx_info(self):
        """Ctrl+Alt+P: datos de la sala o, sobre un mensaje de otra persona, perfil de quien lo escribió."""
        obj = self._mx_mensaje(avisar=False)
        if obj is not None and not obj.fila.extra.get("propio") and obj.fila.extra.get("sender"):
            from .dialogos_matrix import ver_perfil_usuario
            x = obj.fila.extra
            return ver_perfil_usuario(x["account_id"], x["sender"], x.get("usuario") or x["sender"])
        datos = self._mx_sala_de_fila()
        if not datos:
            return
        acc, room = datos
        c = datos_matrix.info_sala(acc, room)
        lineas = [_("Nombre: {n}").format(n=c["nombre"]), _("Identificador: {i}").format(i=room),
                  _("Mensaje directo") if c["dm"] else (_("Espacio") if c["espacio"] else _("Sala"))]
        if c["tema"]:
            lineas.append(_("Tema: {t}").format(t=c["tema"]))
        lineas.append(_("Cifrada de extremo a extremo") if c["cifrada"] else _("Sin cifrar"))
        lineas.append(_("Nivel de aviso: {n}").format(n=dict((k, _(v)) for k, v in NIVELES).get(c["aviso"], "")))
        if c["etiqueta"]:
            lineas.append(_("Etiqueta: {e}").format(e=_(dict(ETIQUETAS).get(c["etiqueta"], c["etiqueta"]))))
        mostrar_aviso_dialogo("\n".join(lineas), _("Información de {n}").format(n=c["nombre"]))

    # ------------------------------------------------------------------ cuenta
    def mx_conectar(self, account_id: int = None):
        acc = account_id or self._mx_cuenta()
        if acc is None or not self._mx_disponible():
            return
        if datos_matrix.conectada(acc):
            self.avisar(_("Esta cuenta ya está conectada"))
            return
        self.avisar(_("Conectando con Matrix…"))
        self._mx_conectar_en_segundo_plano(acc, avisar=True)

    def _mx_conectar_en_segundo_plano(self, acc: int, avisar: bool):
        conectando = self.__dict__.setdefault("_mx_conectando", set())
        if acc in conectando:
            return
        conectando.add(acc)

        def _fin(res, err):
            conectando.discard(acc)
            if err:
                if avisar:
                    self.avisar(_("No se pudo conectar: {e}").format(e=err))
                else:
                    log.warning(f"No se pudo conectar la cuenta de Matrix {acc}: {err}")
                return
            config.set("matrix", f"autoconnect_{acc}", True)       # al reiniciar, vuelve a conectarse
            if avisar:
                self.avisar(_("Conectado a Matrix; sincronizando en segundo plano"))
            self._mx_refrescar_arbol()
        ejecutar(lambda: _servicio().conectar(acc), _fin)

    def mx_desconectar(self, account_id: int = None):
        acc = account_id or self._mx_cuenta()
        if acc is None:
            return
        if not datos_matrix.conectada(acc):
            self.avisar(_("Esta cuenta no está conectada"))
            return

        def _fin(res, err):
            config.set("matrix", f"autoconnect_{acc}", False)      # lo has pedido tú: no se reconecta sola
            self.avisar(_("No se pudo desconectar: {e}").format(e=err) if err else _("Desconectado de Matrix"))
            self._mx_refrescar_arbol()
        ejecutar(lambda: _servicio().desconectar(acc), _fin)

    def mx_perfil(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        from .dialogos_matrix import DialogoPerfilMatrix
        DialogoPerfilMatrix(self, acc).present()

    def mx_dispositivos(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        from .dialogos_matrix import DialogoDispositivosMatrix
        DialogoDispositivosMatrix(self, acc).present()

    def mx_palabras(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        from .dialogos_matrix import DialogoPalabrasMatrix
        DialogoPalabrasMatrix(self, acc).present()

    def mx_registrar(self):
        if not self._mx_disponible():
            return
        from .dialogos_matrix import DialogoRegistroMatrix

        def _hecha(account_id):
            self.cargar_arbol(seleccionar=("matrix", account_id, "timeline", None, None, "rooms"))
            self.iniciar_tiempo_real_matrix()
        DialogoRegistroMatrix(self, _hecha).present()

    def mx_claves(self, exportar: bool):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        dlg = Gtk.FileDialog(title=_("Archivo de claves de cifrado"), initial_name="claves_matrix.txt" if exportar else None)

        def _con_clave(ruta):
            from .dialogos_matrix import DialogoClaveMatrix

            def _hacer(clave):
                def _trabajo():
                    if exportar:
                        _servicio().exportar_claves(acc, ruta, clave)
                        return None
                    return _servicio().importar_claves(acc, ruta, clave)

                def _fin(res, err):
                    if err:
                        self.avisar(_("No se pudo: {e}").format(e=err))
                    elif exportar:
                        self.avisar(_("Claves exportadas"))
                    else:
                        self.avisar(_("Claves importadas. Se han descifrado {n} mensajes que estaban cifrados").format(n=res) if res
                                    else _("Claves importadas. Ya puedes leer el historial cifrado de esas salas"))
                ejecutar(_trabajo, _fin)
            DialogoClaveMatrix(self, _("Exportar claves") if exportar else _("Importar claves"),
                               _("_Contraseña del archivo de claves:"), _hacer).present()

        def _fin_archivo(origen, res):
            try:
                f = origen.save_finish(res) if exportar else origen.open_finish(res)
            except Exception:
                return
            _con_clave(f.get_path())
        (dlg.save if exportar else dlg.open)(self, None, _fin_archivo)

    def mx_redescifrar(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return
        self.avisar(_("Reintentando descifrar los mensajes pendientes…"))

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            recuperados, solicitadas = res
            if recuperados:
                self.avisar(_("Se han descifrado {n} mensajes").format(n=recuperados))
            elif solicitadas:
                self.avisar(_("Faltaban las claves de {n} conversaciones. Las he pedido a tus otros dispositivos: si alguno "
                              "está en línea, los mensajes se descifrarán solos en unos segundos").format(n=solicitadas))
            else:
                self.avisar(_("No había mensajes cifrados pendientes"))
        ejecutar(lambda: _servicio().redescifrar_pendientes(acc), _fin)

    def mx_cross_signing(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return

        def _estado(est, err):
            if err:
                self.avisar(_("No se pudo consultar el estado: {e}").format(e=err))
                return
            est = est or {}
            if est.get("configurado_en_servidor") and est.get("claves_locales"):
                mostrar_aviso_dialogo(_("Tu verificación cruzada ya está configurada y este equipo tiene las claves. Para que "
                                        "alguien te reconozca como de confianza, verifícate con esa persona por emojis."),
                                      _("Verificación cruzada"))
                return
            aviso = _("La verificación cruzada de esta aplicación es experimental: la biblioteca base (matrix-nio) no la "
                      "soporta de forma oficial, así que no sustituye a la copia de seguridad de claves y puede que Element "
                      "no la reconozca por completo. Guarda tus claves con «Exportar claves» como respaldo fiable.\n\n"
                      "Se creará tu identidad de cifrado en el servidor y hará falta tu contraseña. ¿Continuar?")
            if est.get("configurado_en_servidor") and not est.get("claves_locales"):
                aviso = _("Ya la tenías en el servidor pero este equipo no tiene las claves privadas: al continuar se "
                          "reemplaza por una nueva y tendrás que re-verificarte con quienes ya lo hubieran hecho.\n\n") + aviso
            from .dialogos_matrix import DialogoClaveMatrix
            confirmar_async(self, aviso, lambda: DialogoClaveMatrix(
                self, _("Verificación cruzada"), _("_Contraseña de tu cuenta de Matrix:"),
                lambda clave: self._mx_configurar_xsign(acc, clave)).present(),
                titulo=_("Configurar la verificación cruzada"), etiqueta_si=_("Continuar"))
        ejecutar(lambda: _servicio().estado_cross_signing(acc), _estado)

    def _mx_configurar_xsign(self, acc: int, clave: str):
        self.avisar(_("Configurando la verificación cruzada…"))

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo configurar: {e}").format(e=err))
                return
            res = res or {}
            if res.get("estado") == "aprobacion_navegador":
                return self._mx_aprobar_xsign(acc, res.get("url") or "")
            self.avisar(_("Verificación cruzada configurada. Ahora verifícate por emojis con quien quieras que confíe en ti"))
        ejecutar(lambda: _servicio().configurar_cross_signing(acc, clave), _fin)

    def _mx_aprobar_xsign(self, acc: int, url: str):
        if not url:
            self.avisar(_("El servidor no indicó la página de aprobación"))
            return
        from app.core.privacy import abrir_url
        abrir_url(url)
        confirmar_async(self, _("Tu servidor pide aprobar la verificación cruzada en el navegador (se acaba de abrir la "
                                "página; si no, la dirección es {u}).\n\nInicia sesión si hace falta, aprueba, y cuando "
                                "termines vuelve aquí. ¿Ya lo has aprobado?").format(u=url),
                        lambda: ejecutar(lambda: _servicio().continuar_cross_signing_tras_aprobacion(acc),
                                         lambda r, e: self.avisar(_("No se pudo finalizar: {e}").format(e=e) if e
                                                                  else _("Verificación cruzada configurada"))),
                        titulo=_("Aprobar en el navegador"), etiqueta_si=_("Ya está aprobado"))

    def mx_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "matrix" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de Matrix"))
            return
        acc = n.account_id
        nombre = next((nm for i, nm in datos_matrix.cuentas() if i == acc), "")

        def _hacer():
            def _fin(res, err):
                datos_matrix.EXTRA.pop(acc, None)
                self.cargar_arbol(conservar=False)
                self.avisar(_("No se pudo: {e}").format(e=err) if err else _("Cuenta eliminada de este equipo"))
            ejecutar(lambda: _servicio().eliminar_cuenta(acc), _fin)
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo? Se cerrará su sesión en el servidor.")
                        .format(n=nombre), _hacer, etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ administración del servidor (Synapse)
    def _mx_admin_cuenta(self):
        acc = self._mx_cuenta()
        if acc is None or not self._mx_conectada(acc):
            return None
        return acc

    def mx_admin_comprobar(self):
        acc = self._mx_admin_cuenta()
        if acc is None:
            return
        self.avisar(_("Comprobando…"))
        ejecutar(lambda: _servicio().es_admin_servidor(acc),
                 lambda es, e: self.avisar(_("No se pudo comprobar: {e}").format(e=e) if e
                                           else (_("Sí: tu cuenta es administradora del servidor") if es
                                                 else _("No: tu cuenta no es administradora del servidor (o no es Synapse)"))))

    def mx_admin_dialogo(self, nombre: str):
        acc = self._mx_admin_cuenta()
        if acc is None:
            return
        from . import dialogos_matrix_admin as adm
        if nombre == "usuarios":
            return adm.DialogoAdminUsuarios(self, acc, al_directo=lambda uid: self.mx_dm_con(acc, uid)).present()
        clase = {"salas": adm.DialogoAdminSalas, "informes": adm.DialogoAdminInformes, "tokens": adm.DialogoAdminTokens,
                 "federacion": adm.DialogoAdminFederacion, "aviso": adm.DialogoAvisoServidor}[nombre]
        clase(self, acc).present()

    def mx_admin_estadisticas(self):
        acc = self._mx_admin_cuenta()
        if acc is None:
            return
        self.avisar(_("Consultando el servidor…"))

        def _fin(d, err):
            if err:
                self.avisar(_("No se pudo (¿eres administrador del servidor?): {e}").format(e=err))
                return
            d = d or {}

            def num(v):
                return str(v) if v is not None else _("(no disponible)")
            mostrar_aviso_dialogo("\n".join([_("Versión de Synapse: {v}").format(v=d.get("version") or _("(desconocida)")),
                                             _("Usuarios en el servidor: {n}").format(n=num(d.get("usuarios_total"))),
                                             _("Salas en el servidor: {n}").format(n=num(d.get("salas_total")))]),
                                  _("Estadísticas del servidor"))
        ejecutar(lambda: _servicio().admin_estadisticas(acc), _fin)

    def mx_admin_actualizaciones(self):
        acc = self._mx_admin_cuenta()
        if acc is None:
            return
        ejecutar(lambda: _servicio().admin_actualizaciones_fondo(acc),
                 lambda d, e: self.avisar(_("No se pudo: {e}").format(e=e) if e
                                          else (_("El servidor está migrando la base de datos en segundo plano; puede ir más lento")
                                                if (d or {}).get("en_curso") else _("La base de datos del servidor está al día"))))

    def mx_admin_media(self):
        """Borra los archivos locales no usados desde hace X días para liberar disco."""
        acc = self._mx_admin_cuenta()
        if acc is None:
            return

        def _dias(texto):
            try:
                dias = max(1, int(float(texto)))
            except ValueError:
                self.avisar(_("Escribe un número de días"))
                return
            antes = int((time.time() - dias * 86400) * 1000)
            confirmar_async(self, _("Se borrarán del disco los archivos locales no vistos desde hace más de {d} días. ¿Continuar?")
                            .format(d=dias),
                            lambda: ejecutar(lambda: _servicio().admin_borrar_media_antiguos(acc, antes),
                                             lambda n, e: self.avisar(_("No se pudo: {e}").format(e=e) if e
                                                                      else _("{n} archivos borrados").format(n=n or 0))),
                            etiqueta_si=_("Borrar"))
        d = DialogoTexto(self, _("Mantenimiento de archivos"), _("Borrar lo no usado desde hace más de cuántos _días:"), _dias,
                         aceptar=_("_Continuar"))
        d.entrada.set_text("90")
        d.present()

    # ------------------------------------------------------------------ refrescos y lectura
    def _mx_refrescar_arbol(self):
        """Reconstruye el árbol (el nombre dice si está desconectada) sin perder dónde se está."""
        self.cargar_arbol()

    def _mx_refrescar_lista(self, account_id: int):
        n = self.nodo_actual
        if n is not None and n.servicio == "matrix" and n.account_id == account_id and n.listable:
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _mx_refrescar_chat(self, account_id: int, room_id: str, ir_al_final: bool = False):
        n = self.nodo_actual
        if n is None or n.servicio != "matrix" or n.account_id != account_id:
            return
        if datos_matrix.sala_de_chat(n.timeline) != room_id:
            return self._mx_refrescar_lista(account_id)
        obj = self._fila_actual()
        if ir_al_final:
            def _al_final(filas):
                if filas:
                    self.seleccion_lista.set_selected(len(filas) - 1)
            self.cargar_lista(incremental=True, despues=_al_final)
        else:
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _mx_leer_sala(self, account_id: int, room_id: str):
        """Marca una sala como leída aquí y, con la cuenta conectada, también en el servidor (otros clientes la verán leída)."""
        datos_matrix.marcar_leido(account_id, room_id)
        if datos_matrix.conectada(account_id):
            ejecutar(lambda: _servicio().marcar_leida_servidor(account_id, room_id), lambda r, e: None)

    def _mx_al_cambiar_nodo(self, anterior, nuevo):
        """Al dejar una conversación se da por leída."""
        if anterior is None or anterior is nuevo or anterior.servicio != "matrix":
            return
        room = datos_matrix.sala_de_chat(anterior.timeline)
        if room is not None:
            self._mx_leer_sala(anterior.account_id, room)

    # ------------------------------------------------------------------ tiempo real
    def iniciar_tiempo_real_matrix(self):
        """Se suscribe a los eventos del servicio y conecta las cuentas que quedaron conectadas la última vez."""
        if not HAS_NIO:
            return
        if not getattr(self, "_mx_suscrito", False):
            self._mx_suscrito = True
            self._mx_pendientes = {}
            self._mx_marca = {}
            event_bus.subscribe("matrix_evento", self._mx_ev)
        for acc, _nombre in datos_matrix.cuentas():
            self._mx_marca.setdefault(acc, datos_matrix.ultimo_id(acc))
            if config.get("matrix", f"autoconnect_{acc}", True):
                self._mx_conectar_en_segundo_plano(acc, avisar=False)

    def detener_tiempo_real_matrix(self):
        if getattr(self, "_mx_suscrito", False):
            self._mx_suscrito = False
            event_bus.unsubscribe("matrix_evento", self._mx_ev)

    def _mx_ev(self, datos):
        llamar_en_ui(self._mx_novedad, datos or {})

    def _mx_nombre_cuenta(self, acc: int) -> str:
        return next((n for i, n in datos_matrix.cuentas() if i == acc), _("Matrix"))

    def _mx_novedad(self, datos: dict):
        """Llega en el hilo de la UI. Las ráfagas se juntan en un solo repintado."""
        acc, tipo = datos.get("account_id"), datos.get("tipo")
        if acc is None:
            return
        cuenta = self._mx_nombre_cuenta(acc)
        if tipo == "invitacion":
            speak(_("{c}: invitación a {n}").format(c=cuenta, n=datos.get("nombre") or ""), interrupt=False)
        elif tipo == "verificacion_solicitada":
            speak(_("{c}: verificación iniciada desde otro dispositivo; enseguida aparecerán los emojis para comparar").format(c=cuenta),
                  interrupt=False)
        elif tipo == "verificacion_emojis":
            self._mx_dialogo_verificacion(acc, datos)
        elif tipo == "verificacion_ok":
            speak(_("{c}: sesión verificada. Este dispositivo ya es de confianza").format(c=cuenta), interrupt=False)
        elif tipo == "verificacion_cancelada":
            speak(_("{c}: verificación cancelada. {m}").format(c=cuenta, m=datos.get("motivo") or ""), interrupt=False)
        elif tipo == "typing":
            self._mx_escribiendo(acc, datos)
        if tipo not in EVENTOS_LISTA:
            return
        info = self._mx_pendientes.setdefault(acc, {"salas": set(), "nuevos": [], "arbol": False})
        if tipo in ("conectado", "desconectado"):
            info["arbol"] = True
            if tipo == "conectado":
                self._mx_marca[acc] = datos_matrix.ultimo_id(acc)
                speak(_("Reconectado a {c}").format(c=cuenta) if datos.get("reconexion") else _("Conectado a {c}").format(c=cuenta),
                      interrupt=False)
            elif not datos.get("esperado", True):
                speak(_("Se perdió la conexión con {c}; reintentando en segundo plano").format(c=cuenta), interrupt=False)
        if tipo == "sincronizada":
            info["arbol"] = True
        if tipo == "invitacion":
            info["arbol"] = True
        if datos.get("room_id"):
            info["salas"].add(datos["room_id"])
            if tipo == "mensaje" and not datos.get("propio") and not datos.get("inicial") and datos.get("texto"):
                info["nuevos"].append(datos["room_id"])
        if not info.get("programado"):
            info["programado"] = True
            GLib.timeout_add(ANTIRREBOTE_MS, self._mx_vaciar, acc)

    def _mx_escribiendo(self, acc: int, datos: dict):
        """«X está escribiendo…» en la barra de estado, sin voz y solo en la conversación abierta."""
        abierto = self._mx_chat_abierto()
        if abierto and abierto == (acc, datos.get("room_id")):
            nombres = datos.get("nombres") or []
            self.estado.set_text(_("{n} está escribiendo…").format(n=", ".join(nombres[:3])) if nombres
                                 else self.nodo_actual.titulo)

    def _mx_dialogo_verificacion(self, acc: int, datos: dict):
        txid, emojis = datos.get("txid"), datos.get("emojis") or ""

        def _si():
            ejecutar(lambda: _servicio().confirmar_verificacion(acc, txid), lambda r, e: e and self.avisar(_("No se pudo: {e}").format(e=e)))

        def _no():
            ejecutar(lambda: _servicio().cancelar_verificacion(acc, txid), lambda r, e: None)
        confirmar_async(self, _("Verificación de Matrix por emojis.\n\nComprueba que estos emojis coinciden exactamente con los "
                                "que ve la otra persona (o tu otro dispositivo):\n\n{e}\n\n¿Coinciden?").format(e=emojis),
                        _si, titulo=_("Verificar en Matrix"), etiqueta_si=_("Coinciden"), al_no=_no)

    def _mx_vaciar(self, account_id):
        info = self._mx_pendientes.pop(account_id, {"salas": set(), "nuevos": [], "arbol": False})
        if info["arbol"]:
            self._mx_refrescar_arbol()
        n = self.nodo_actual
        if n is not None and n.servicio == "matrix" and n.account_id == account_id and n.listable and not info["arbol"]:
            abierto = datos_matrix.sala_de_chat(n.timeline)
            if abierto is None or abierto in info["salas"] or not info["salas"]:
                obj = self._fila_actual()
                self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
        self._mx_anunciar(account_id, info["nuevos"])
        return GLib.SOURCE_REMOVE

    def _mx_anunciar(self, account_id, salas):
        """Dice por voz lo que llega a la conversación abierta, a los directos y a las salas donde te mencionan."""
        if not salas or not config.get("matrix", "announce_new", True):
            return
        abierto = self._mx_chat_abierto()
        dichos = 0
        for room in dict.fromkeys(salas):
            c = datos_matrix.info_sala(account_id, room)
            ultimo = datos_matrix.ultimo_ajeno(account_id, room)
            if not ultimo:
                continue
            quien, texto, tipo, mencion, mid = ultimo
            vista = max(self._mx_marca.get(account_id, 0), self._mx_marca.get((account_id, room), 0))
            if mid <= vista:
                continue
            self._mx_marca[(account_id, room)] = mid
            es_esta = abierto == (account_id, room)
            if c["aviso"] == "none" or (c["aviso"] == "mentions" and not mencion and not es_esta):
                continue
            if not c["dm"] and not es_esta and not mencion:
                continue
            cuerpo = " ".join(texto.split())[:300] or _("Archivo adjunto")
            if tipo == "media":
                cuerpo = _("Archivo adjunto")
            speak(_("{n} te menciona en {c}: {t}").format(n=quien, c=c["nombre"], t=cuerpo) if mencion and not es_esta and not c["dm"]
                  else f"{quien}: {cuerpo}", interrupt=False)
            dichos += 1
            if dichos >= ANUNCIAR_MAX:
                break

    # ------------------------------------------------------------------ menú contextual
    def mx_menu_contextual(self, obj):
        """Lista (etiqueta, acción) del menú contextual de una fila de Matrix."""
        x = obj.fila.extra
        if x.get("tipo") == "invitacion":
            return ((_("Aceptar la invitación"), "win.abrir-chat"), (_("Rechazar la invitación"), "win.mx-rechazar"))
        if x.get("tipo") == "sala":
            lista = [(_("Abrir la conversación"), "win.abrir-chat"), (_("Escribir"), "win.nuevo"),
                     (_("Información"), "win.perfil"), (_("Miembros y moderación…"), "win.usuarios-canal"),
                     (_("Nivel de aviso…"), "win.mx-nivel-aviso"), (_("Etiqueta de la sala…"), "win.mx-etiqueta"),
                     (_("Marcar como leída"), "win.mx-leido"), (_("Marcar como no leída"), "win.mx-no-leido"),
                     (_("Hilos de la sala…"), "win.mx-hilos"), (_("Mensajes fijados…"), "win.mx-fijados"),
                     (_("Ajustes de la sala…"), "win.mx-ajustes"), (_("Añadir la sala a un espacio…"), "win.mx-a-espacio")]
            if x.get("espacio"):
                lista.append((_("Ver las salas del espacio…"), "win.mx-ver-espacio"))
            lista += [(_("Dispositivos cifrados de la sala…"), "win.mx-dispositivos-sala"),
                      (_("Reportar la sala…"), "win.mx-reportar-sala"), (_("Exportar la conversación…"), "win.mx-exportar"),
                      (_("Salir de la sala"), "win.papelera"), (_("Salir y olvidar la sala"), "win.mx-olvidar")]
            return tuple(lista)
        lista = [(_("Responder"), "win.responder"), (_("Responder en un hilo o ver el hilo…"), "win.mx-hilo"),
                 (_("Reaccionar…"), "win.mx-reaccionar"), (_("Quitar mi reacción…"), "win.mx-quitar-reaccion"),
                 (_("¿Quién ha leído este mensaje?"), "win.mx-quien-leyo"), (_("Copiar el texto"), "win.mx-copiar")]
        if x.get("poll_json"):
            lista.append((_("Votar en la encuesta…"), "win.votar"))
            if x.get("propio"):
                lista.append((_("Cerrar la encuesta…"), "win.mx-cerrar-encuesta"))
        if x.get("adjunto"):
            lista += [(_("Reproducir"), "win.reproducir")] if x.get("reproducible") else []
            lista += [(_("Descargar el adjunto"), "win.mx-descargar"), (_("Abrir el adjunto"), "win.mx-abrir-adjunto")]
        if datos_matrix.urls_de(x.get("texto", "")) or (x.get("texto") or "").startswith(_("[Ubicación:")):
            lista.append((_("Abrir el enlace o la ubicación"), "win.abrir-chat"))
        if (x.get("texto") or "").startswith(_("[Mensaje cifrado")):
            lista.append((_("Solicitar las claves de este mensaje"), "win.mx-solicitar-claves"))
        if x.get("send_state") == "fallido":
            lista += [(_("Reintentar el envío"), "win.mx-reintentar"), (_("Descartar el mensaje no enviado"), "win.mx-descartar")]
        if x.get("propio"):
            if x.get("tipo_msg") == "msg":
                lista.append((_("Editar mi mensaje"), "win.editar"))
            lista.append((_("Borrar mi mensaje"), "win.papelera"))
        else:
            lista += [(_("Mencionar a {n}").format(n=x.get("usuario") or ""), "win.mx-mencionar"),
                      (_("Datos del usuario"), "win.perfil"), (_("Ignorar al usuario…"), "win.mx-ignorar"),
                      (_("Reportar el mensaje…"), "win.mx-reportar-mensaje")]
        lista += [(_("Fijar o desfijar el mensaje"), "win.mx-fijar-mensaje"), (_("Traducir"), "win.traducir"),
                  (_("Resumir con IA"), "win.resumir-ia")]
        return tuple(lista)

    # ------------------------------------------------------------------ acciones
    def _registrar_acciones_matrix(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("mx-conectar", self.mx_conectar)
        a("mx-desconectar", self.mx_desconectar)
        a("mx-crear-sala", self.mx_crear_sala)
        a("mx-mensaje-directo", self.mx_mensaje_directo)
        a("mx-directorio", self.mx_directorio)
        a("mx-llamar-puerta", self.mx_llamar_puerta)
        a("mx-crear-espacio", self.mx_crear_espacio)
        a("mx-rechazar", self.mx_rechazar)
        a("mx-olvidar", lambda: self.mx_salir(True))
        a("mx-etiqueta", self.mx_etiqueta)
        a("mx-nivel-aviso", self.mx_nivel_aviso)
        a("mx-leido", lambda: self.mx_marcar_leido(True))
        a("mx-no-leido", lambda: self.mx_marcar_leido(False))
        a("mx-hilos", self.mx_hilos)
        a("mx-fijados", self.mx_fijados)
        a("mx-reportar-sala", self.mx_reportar_sala)
        a("mx-dispositivos-sala", self.mx_dispositivos_sala)
        a("mx-ajustes", self.mx_ajustes)
        a("mx-permitir-llamar", self.mx_permitir_llamar)
        a("mx-avatar-sala", self.mx_avatar_sala)
        a("mx-a-espacio", self.mx_a_espacio)
        a("mx-ver-espacio", self.mx_ver_espacio)
        a("mx-exportar", self.mx_exportar)
        a("mx-buscar", self.mx_buscar)
        a("mx-hilo", self.mx_hilo)
        a("mx-reaccionar", self.mx_reaccionar)
        a("mx-quitar-reaccion", self.mx_quitar_reaccion)
        a("mx-quien-leyo", self.mx_quien_leyo)
        a("mx-copiar", self.mx_copiar)
        a("mx-mencionar", self.mx_mencionar)
        a("mx-fijar-mensaje", self.mx_fijar_mensaje)
        a("mx-reportar-mensaje", self.mx_reportar_mensaje)
        a("mx-solicitar-claves", self.mx_solicitar_claves)
        a("mx-ignorar", self.mx_ignorar)
        a("mx-reintentar", self.mx_reintentar)
        a("mx-descartar", self.mx_descartar)
        a("mx-crear-encuesta", self.mx_crear_encuesta)
        a("mx-cerrar-encuesta", self.mx_cerrar_encuesta)
        a("mx-enviar-ubicacion", self.mx_enviar_ubicacion)
        a("mx-enviar-archivo", self.mx_enviar_archivo)
        a("mx-descargar", self.mx_descargar)
        a("mx-abrir-adjunto", self.mx_abrir_adjunto)
        a("mx-perfil", self.mx_perfil)
        a("mx-dispositivos", self.mx_dispositivos)
        a("mx-palabras", self.mx_palabras)
        a("mx-registrar", self.mx_registrar)
        a("mx-exportar-claves", lambda: self.mx_claves(True))
        a("mx-importar-claves", lambda: self.mx_claves(False))
        a("mx-redescifrar", self.mx_redescifrar)
        a("mx-cross-signing", self.mx_cross_signing)
        a("mx-eliminar-cuenta", self.mx_eliminar_cuenta)
        a("mx-admin-comprobar", self.mx_admin_comprobar)
        for nombre in ("usuarios", "salas", "informes", "tokens", "federacion", "aviso"):
            a("mx-admin-" + nombre, lambda n=nombre: self.mx_admin_dialogo(n))
        a("mx-admin-estadisticas", self.mx_admin_estadisticas)
        a("mx-admin-actualizaciones", self.mx_admin_actualizaciones)
        a("mx-admin-media", self.mx_admin_media)
