# -*- coding: utf-8 -*-
"""XMPP (Jabber) en la ventana principal (mixin de VentanaPrincipal).

Cada cuenta tiene los buffers Conversaciones, Contactos, Grupos, Solicitudes y Archivados (y una rama por cada
pasarela, como WhatsApp o Discord). Intro sobre un chat abre su conversación como un buffer más (Ctrl+W lo cierra).
Los mensajes, la presencia y las solicitudes llegan en tiempo real por el bus de eventos del servicio."""

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
from app.services.xmpp.xmpp_common import HAS_SLIXMPP

from . import datos_xmpp
from .dialogos import DialogoElegir, DialogoTexto, confirmar_async, ejecutar

log = logging.getLogger(__name__)

ANUNCIAR_MAX = 3
ANTIRREBOTE_MS = 500
REACCIONES = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👎", "🔥", "🎉", "🤔"]
SILENCIOS = [(1, "1 hora"), (8, "8 horas"), (24, "1 día"), (168, "1 semana"), (-1, "Para siempre")]
NIVELES = [("all", "Avisar de todo"), ("mentions", "Solo cuando me mencionen"), ("none", "Silencio")]
EVENTOS_LISTA = ("mensaje", "roster_actualizado", "presencia", "solicitud", "invitacion_muc", "leido_por", "entregado",
                 "historial", "pep", "conectado", "desconectado")


def _servicio():
    return datos_xmpp.servicio()


class _XMPPMixin:
    # ------------------------------------------------------------------ contexto
    def _x_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de XMPP."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "xmpp"
        n = self.nodo_actual
        return n is not None and n.servicio == "xmpp"

    def _x_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "xmpp" and n.account_id:
            return n.account_id
        cuentas = datos_xmpp.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una cuenta de XMPP"))
        return None

    def _x_disponible(self) -> bool:
        if not HAS_SLIXMPP:
            self.avisar(_("Falta slixmpp. Instálalo con: pip install slixmpp"))
            return False
        return True

    def _x_conectada(self, account_id: int, avisar: bool = True) -> bool:
        if datos_xmpp.conectada(account_id):
            return True
        if avisar:
            self.avisar(_("La cuenta no está conectada. Conéctala desde el menú XMPP."))
        return False

    def _x_chat_abierto(self):
        """(account_id, jid) de la conversación abierta, o None."""
        n = self.nodo_actual
        if n is not None and n.servicio == "xmpp":
            jid = datos_xmpp.jid_de_chat(n.timeline)
            if jid is not None:
                return n.account_id, jid
        return None

    def _x_chat_de_fila(self, avisar: bool = True):
        """(account_id, jid) del chat enfocado (un chat o un mensaje), de la conversación abierta, o None."""
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "xmpp" and obj.fila.extra.get("jid"):
            return obj.fila.extra["account_id"], obj.fila.extra["jid"]
        abierto = self._x_chat_abierto()
        if abierto:
            return abierto
        if avisar:
            self.avisar(_("Selecciona un chat de XMPP"))
        return None

    def _x_es_sala(self, account_id: int, jid: str) -> bool:
        return datos_xmpp.info_chat(account_id, jid)["tipo"] == "muc"

    def _x_mensaje(self, propio: bool = False):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "xmpp" or obj.fila.extra.get("tipo") != "mensaje":
            self.avisar(_("Selecciona un mensaje de XMPP"))
            return None
        if propio and not obj.fila.extra.get("propio"):
            self.avisar(_("Solo puedes hacer esto con tus propios mensajes"))
            return None
        return obj

    # ------------------------------------------------------------------ abrir y actualizar
    def x_actualizar_nodo(self, nodo, silencioso: bool = False):
        """Los datos llegan solos; actualizar relee la lista y, en una conversación, pide al servidor lo que falte."""
        if nodo is not self.nodo_actual:
            return
        jid = datos_xmpp.jid_de_chat(nodo.timeline)
        if jid is not None and datos_xmpp.conectada(nodo.account_id):
            ejecutar(lambda: _servicio().catch_up(nodo.account_id, jid),
                     lambda n, e: self._x_refrescar_lista(nodo.account_id))
        else:
            self._x_refrescar_lista(nodo.account_id)
            if not silencioso and not datos_xmpp.conectada(nodo.account_id):
                self.avisar(_("La cuenta no está conectada. Conéctala desde el menú XMPP."))

    def x_cargar_mas(self):
        abierto = self._x_chat_abierto()
        if not abierto or not self._x_conectada(abierto[0]):
            if not abierto:
                self.avisar(_("Abre antes una conversación"))
            return
        acc, jid = abierto
        actual = self._fila_actual()
        self.avisar(_("Cargando mensajes anteriores…"))

        def _fin(n, err):
            if err:
                self.avisar(_("No se pudo: {e}").format(e=err))
                return
            self.cargar_lista(conservar_id=actual.fila.id if actual else None, incremental=True)
            self.avisar(_("{n} mensajes anteriores añadidos").format(n=n) if n
                        else _("No hay más mensajes anteriores (o el servidor no guarda historial)"))
        ejecutar(lambda: _servicio().cargar_anteriores(acc, jid), _fin)

    def x_abrir_fila(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "xmpp":
            self.avisar(_("Selecciona un chat de la lista"))
            return
        if obj.fila.extra.get("tipo") == "chat":
            self.x_abrir_chat(obj)
        elif obj.fila.extra.get("tipo") == "solicitud":
            self.x_responder_solicitud(obj)

    def x_abrir_chat(self, obj):
        x = obj.fila.extra
        if not self._x_conectada(x["account_id"]):
            return
        self._x_abrir_por_jid(x["account_id"], x["jid"], x["nombre"])

    def _x_abrir_por_jid(self, account_id: int, jid: str, nombre: str = ""):
        self._abrir_buffer(account_id, datos_xmpp.clave_chat(jid), nombre or datos_xmpp.titulo_chat(account_id, jid),
                           servicio="xmpp")
        # Lo que pasó mientras no estabas: se pide al servidor y la lista se repinta al llegar.
        ejecutar(lambda: _servicio().catch_up(account_id, jid), lambda n, e: n and self._x_refrescar_chat(account_id, jid))

    def x_responder_solicitud(self, obj):
        x = obj.fila.extra
        acc, jid, nombre = x["account_id"], x["jid"], x["nombre"]
        if x.get("sala"):
            confirmar_async(self, _("Te han invitado a la sala {j}. ¿Entrar?").format(j=jid),
                            lambda: self._x_aceptar_sala(acc, jid), titulo=_("Invitación a una sala"),
                            etiqueta_si=_("Entrar"))
            return
        confirmar_async(self, _("{n} ({j}) quiere agregarte a sus contactos. ¿Aceptar?").format(n=nombre, j=jid),
                        lambda: self._x_roster("aceptar", acc, jid), titulo=_("Solicitud de contacto"),
                        etiqueta_si=_("Aceptar"))

    def _x_aceptar_sala(self, acc: int, jid: str):
        def _trabajo():
            ya = db.query_one("SELECT COALESCE(autojoin,0) FROM xmpp_chats WHERE account_id = ? AND jid = ?", (acc, jid))
            if not (ya and ya[0]):
                _servicio().unirse_sala(acc, jid)
            db.execute("UPDATE xmpp_chats SET solicitud = 0 WHERE account_id = ? AND jid = ?", (acc, jid))

        def _fin(res, err):
            self.avisar(_("No se pudo entrar: {e}").format(e=err) if err else _("Dentro de la sala"))
            self._x_refrescar_lista(acc)
        ejecutar(_trabajo, _fin)

    # ------------------------------------------------------------------ escribir
    def x_nuevo(self):
        """Ctrl+N: escribe en el chat abierto o enfocado; en otro caso, añade un contacto."""
        if not self._x_disponible():
            return
        datos = self._x_chat_de_fila(avisar=False)
        if datos:
            return self.x_escribir(*datos)
        self.x_agregar_contacto()

    def x_escribir(self, account_id: int, jid: str):
        if not self._x_conectada(account_id):
            return
        from .dialogos_xmpp import DialogoMensajeXMPP
        DialogoMensajeXMPP(self, _("Mensaje a {n}").format(n=datos_xmpp.titulo_chat(account_id, jid)),
                           lambda texto: self._x_enviar(account_id, jid, texto)).present()

    def _x_enviar(self, account_id: int, jid: str, texto: str):
        sala = self._x_es_sala(account_id, jid)

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo enviar: {e}").format(e=err))
                return
            self._x_refrescar_chat(account_id, jid, ir_al_final=True)
        ejecutar(lambda: _servicio().enviar_muc(account_id, jid, texto) if sala
                 else _servicio().enviar_mensaje(account_id, jid, texto), _fin)

    def x_responder(self):
        obj = self._x_mensaje()
        if obj is None or not self._x_conectada(obj.fila.extra["account_id"]):
            return
        x = obj.fila.extra
        if not x.get("stanza_id"):
            self.avisar(_("Este mensaje no se puede responder"))
            return
        acc, jid, rid = x["account_id"], x["jid"], x["stanza_id"]
        sala = self._x_es_sala(acc, jid)
        cita = f"{obj.fila.autor}: {x.get('texto') or ''}"[:200]

        def _enviar(texto):
            ejecutar(lambda: _servicio().enviar_respuesta(acc, jid, rid, texto, es_muc=sala),
                     lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e)) if e
                     else self._x_refrescar_chat(acc, jid, ir_al_final=True))
        from .dialogos_xmpp import DialogoMensajeXMPP
        DialogoMensajeXMPP(self, _("Responder"), _enviar, cita=cita, aceptar=_("_Responder")).present()

    def x_editar(self):
        """Ctrl+Alt+E: corrige tu último mensaje enviado a este contacto (XEP-0308)."""
        datos = self._x_chat_de_fila()
        if not datos or not self._x_conectada(datos[0]):
            return
        acc, jid = datos
        if self._x_es_sala(acc, jid):
            self.avisar(_("Los mensajes de una sala no se pueden corregir"))
            return
        ultimo = _servicio().ultimo_mensaje_propio(acc, jid)
        if not ultimo:
            self.avisar(_("No has enviado ningún mensaje que corregir"))
            return

        def _corregir(texto):
            ejecutar(lambda: _servicio().enviar_correccion(acc, jid, texto, ultimo["id"]),
                     lambda r, e: self.avisar(_("No se pudo corregir: {e}").format(e=e)) if e
                     else (self.avisar(_("Mensaje corregido")), self._x_refrescar_chat(acc, jid)))
        from .dialogos_xmpp import DialogoMensajeXMPP
        DialogoMensajeXMPP(self, _("Corregir mi último mensaje"), _corregir, texto_inicial=ultimo["texto"],
                           aceptar=_("_Corregir")).present()

    def x_borrar(self):
        """Supr: en un mensaje, lo retira (el tuyo) o lo modera; en un chat de la lista, quita el contacto o sale del grupo."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "xmpp":
            return
        x = obj.fila.extra
        acc, jid = x["account_id"], x.get("jid")
        if x.get("tipo") == "chat":
            if x.get("sala"):
                return self.x_salir_grupo(False)
            return self._x_roster_confirmado("quitar", acc, jid, x["nombre"])
        if x.get("tipo") != "mensaje" or not self._x_conectada(acc):
            return
        rid, sala = x.get("stanza_id"), self._x_es_sala(acc, jid)
        if not rid:
            self.avisar(_("Este mensaje no se puede retirar"))
            return
        if x.get("propio"):
            def _retirar():
                ejecutar(lambda: _servicio().retractar_mensaje(acc, jid, rid, es_muc=sala),
                         lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e)) if e
                         else (self.avisar(_("Mensaje retirado")), self._x_refrescar_chat(acc, jid)))
            confirmar_async(self, _("¿Retirar tu mensaje para todos?"), _retirar, etiqueta_si=_("Retirar"))
        elif sala:
            def _moderar():
                ejecutar(lambda: _servicio().moderar_mensaje(acc, jid, rid),
                         lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e)) if e else self.avisar(_("Mensaje retirado")))
            confirmar_async(self, _("¿Retirar este mensaje de la sala? Necesitas ser moderador/a."), _moderar,
                            etiqueta_si=_("Retirar"))
        else:
            self.avisar(_("Solo puedes retirar tus propios mensajes"))

    def x_reaccionar(self):
        obj = self._x_mensaje()
        if obj is None or not self._x_conectada(obj.fila.extra["account_id"]):
            return
        x = obj.fila.extra
        if not x.get("stanza_id"):
            return
        acc, jid, rid = x["account_id"], x["jid"], x["stanza_id"]
        sala = self._x_es_sala(acc, jid)
        mias = _servicio().mis_reacciones(acc, rid)

        def _poner(i):
            emoji = REACCIONES[i]
            quitar = emoji in mias
            ejecutar(lambda: _servicio().enviar_reaccion(acc, jid, rid, emoji, quitar=quitar, es_muc=sala),
                     lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e)) if e
                     else (self.avisar(_("Reacción quitada") if quitar else _("Reacción enviada")),
                           self._x_refrescar_chat(acc, jid)))
        DialogoElegir(self, _("Reaccionar"), _("_Emoji (si ya lo pusiste, se quita):"), REACCIONES, _poner,
                      aceptar=_("_Reaccionar")).present()

    def x_copiar(self):
        obj = self._x_mensaje()
        if obj is None:
            return
        copiar_al_portapapeles(obj.fila.extra.get("texto") or obj.fila.titulo)
        self.avisar(_("Texto copiado"))

    def x_destacar(self, obj) -> bool:
        """Ctrl+D: marca o desmarca un mensaje como destacado (favorito local)."""
        x = obj.fila.extra
        if x.get("tipo") != "mensaje":
            return False
        nuevo = not obj.favorito
        db.execute("UPDATE xmpp_mensajes SET destacado = ? WHERE id = ?", (1 if nuevo else 0, x["mid"]))
        obj.favorito = nuevo
        self.avisar(_("Mensaje destacado") if nuevo else _("Ya no está destacado"))
        return True

    # ------------------------------------------------------------------ adjuntos
    def x_enviar_archivo(self):
        datos = self._x_chat_de_fila()
        if not datos or not self._x_conectada(datos[0]):
            return
        acc, jid = datos
        sala = self._x_es_sala(acc, jid)
        dlg = Gtk.FileDialog(title=_("Elige el archivo a enviar"))

        def _fin(origen, res):
            try:
                ruta = origen.open_finish(res).get_path()
            except Exception:
                return
            self.avisar(_("Subiendo {f}…").format(f=os.path.basename(ruta)))
            ejecutar(lambda: _servicio().enviar_archivo(acc, jid, ruta, es_muc=sala),
                     lambda r, e: self.avisar(_("No se pudo enviar: {e}").format(e=e)) if e
                     else (self.avisar(_("Archivo enviado")), self._x_refrescar_chat(acc, jid, ir_al_final=True)))
        dlg.open(self, None, _fin)

    def _x_adjunto(self, obj):
        x = obj.fila.extra
        if not x.get("descargable") or not x.get("media_url"):
            self.avisar(_("Este mensaje no tiene un archivo"))
            return None
        return x

    def x_descargar(self):
        obj = self._x_mensaje()
        x = self._x_adjunto(obj) if obj else None
        if not x or not self._x_conectada(x["account_id"]):
            return
        xmpp, url = _servicio(), x["media_url"]
        self.avisar(_("Descargando…"))
        titulo = datos_xmpp.titulo_chat(x["account_id"], x["jid"])
        ejecutar(lambda: xmpp.descargar_a_descargas(x["account_id"], url, xmpp.nombre_desde_url(url), titulo),
                 lambda ruta, e: self.avisar(_("No se pudo descargar: {e}").format(e=e) if e
                                             else _("Descargado en {r}").format(r=ruta)))

    def x_abrir_adjunto(self):
        obj = self._x_mensaje()
        x = self._x_adjunto(obj) if obj else None
        if not x or not self._x_conectada(x["account_id"]):
            return
        from app.services.xmpp.xmpp_medios import es_extension_activa
        xmpp, url = _servicio(), x["media_url"]
        nombre = xmpp.nombre_desde_url(url)
        if es_extension_activa(nombre):       # un ejecutable o script nunca se abre desde la aplicación
            self.avisar(_("Por seguridad, «{n}» no se abre desde la aplicación. Se descargará a tu carpeta de descargas.").format(n=nombre))
            return self.x_descargar()

        def _fin(ruta, err):
            if err or not ruta:
                self.avisar(_("No se pudo abrir: {e}").format(e=err or ""))
                return
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(ruta).get_uri(), None)
                self.avisar(_("Abriendo el archivo"))
            except GLib.Error as e:
                self.avisar(_("Descargado en {r}, pero no se pudo abrir: {e}").format(r=ruta, e=e.message))
        ejecutar(lambda: xmpp.descargar_media(x["account_id"], url), _fin)

    def x_reproducir(self) -> bool:
        """F2: reproduce una nota de voz, un audio o un vídeo. True si la fila era reproducible."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "xmpp" or not obj.fila.extra.get("reproducible"):
            return False
        x = obj.fila.extra
        if not self._x_conectada(x["account_id"]):
            return True
        titulo = f"{obj.fila.autor}: {_(datos_xmpp.ETIQUETAS_ADJUNTO[x['adjunto']])}"
        self.avisar(_("Cargando…"))

        def _sonar(ruta, err):
            if err or not ruta:
                self.avisar(_("No se pudo reproducir: {e}").format(e=err or ""))
                return
            from app.services.media_player.player2_service import KIND_PODCAST, player2_service

            def _fin(ok):
                self.avisar(_("Reproduciendo: {t}").format(t=titulo) if ok else _("No se pudo reproducir"))
                if ok:
                    db.execute("UPDATE xmpp_mensajes SET reproducido = 1 WHERE id = ?", (x["mid"],))
            player2_service.cargar_y_reproducir(ruta, titulo, KIND_PODCAST, resume=False, callback=_fin)
        ejecutar(lambda: _servicio().descargar_media(x["account_id"], x["media_url"]), _sonar)
        return True

    # ------------------------------------------------------------------ contactos y grupos
    def x_agregar_contacto(self):
        acc = self._x_cuenta()
        if acc is None or not self._x_disponible() or not self._x_conectada(acc):
            return
        from .dialogos_xmpp import DialogoContactoXMPP
        DialogoContactoXMPP(self, acc, lambda: self._x_refrescar_lista(acc)).present()

    def _x_roster_confirmado(self, accion: str, acc: int, jid: str, nombre: str):
        confirmar_async(self, _("¿Quitar a {n} de tus contactos?").format(n=nombre),
                        lambda: self._x_roster(accion, acc, jid), etiqueta_si=_("Quitar"))

    def _x_roster(self, accion: str, acc: int, jid: str, arg=None):
        if not self._x_conectada(acc):
            return
        xmpp = _servicio()
        trabajos = {"aceptar": lambda: xmpp.aceptar_solicitud(acc, jid), "rechazar": lambda: xmpp.rechazar_solicitud(acc, jid),
                    "quitar": lambda: xmpp.quitar_contacto(acc, jid), "renombrar": lambda: xmpp.renombrar_contacto(acc, jid, arg),
                    "bloquear": lambda: xmpp.bloquear_contacto(acc, jid, bool(arg))}

        def _fin(res, err):
            self.avisar(_("No se pudo: {e}").format(e=err) if err else _("Hecho"))
            self._x_refrescar_lista(acc)
        ejecutar(trabajos[accion], _fin)

    def x_rechazar_solicitud(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "xmpp" or obj.fila.extra.get("tipo") != "solicitud":
            self.avisar(_("Selecciona una solicitud"))
            return
        x = obj.fila.extra
        if x.get("sala"):
            db.execute("DELETE FROM xmpp_chats WHERE account_id = ? AND jid = ? AND tipo = 'muc' AND COALESCE(solicitud,0) = 1",
                       (x["account_id"], x["jid"]))
            self.avisar(_("Invitación rechazada"))
            self._x_refrescar_lista(x["account_id"])
        else:
            self._x_roster("rechazar", x["account_id"], x["jid"])

    def x_renombrar(self):
        datos = self._x_chat_de_fila()
        if not datos or self._x_es_sala(*datos):
            return
        acc, jid = datos
        DialogoTexto(self, _("Renombrar contacto"), _("_Nuevo nombre:"), lambda n: self._x_roster("renombrar", acc, jid, n),
                     aceptar=_("_Renombrar")).present()

    def x_bloquear(self):
        datos = self._x_chat_de_fila()
        if not datos or self._x_es_sala(*datos):
            return
        acc, jid = datos
        bloqueado = datos_xmpp.info_chat(acc, jid)["bloqueado"]
        self._x_roster("bloquear", acc, jid, not bloqueado)

    def x_quitar_contacto(self):
        datos = self._x_chat_de_fila()
        if not datos or self._x_es_sala(*datos):
            return
        self._x_roster_confirmado("quitar", datos[0], datos[1], datos_xmpp.titulo_chat(*datos))

    def x_info(self):
        """Ctrl+Alt+P: datos del contacto o del grupo."""
        datos = self._x_chat_de_fila()
        if not datos:
            return
        acc, jid = datos
        c = datos_xmpp.info_chat(acc, jid)
        lineas = [_("Nombre: {n}").format(n=c["nombre"]), _("JID: {j}").format(j=jid)]
        if c["tipo"] == "muc":
            lineas.append(_("Grupo (sala)"))
            if c["mi_nick"]:
                lineas.append(_("Tu apodo: {n}").format(n=c["mi_nick"]))
        else:
            lineas.append(_("Presencia: {p}").format(p=c["presencia"] or _("Sin conexión")))
            lineas.append(_("Estado: {e}").format(e=c["estado"] or _("(sin mensaje)")))
            subs = {"both": _("mutua"), "to": _("tú lo ves"), "from": _("él te ve"), "none": _("ninguna")}
            lineas.append(_("Suscripción: {s}").format(s=subs.get(c["suscripcion"] or "none", c["suscripcion"])))
            if c["pep"]:
                lineas.append(_("Ánimo o actividad: {e}").format(e=c["pep"]))
            if c["bloqueado"]:
                lineas.append(_("Bloqueado"))
        lineas.append(_("Nivel de aviso: {n}").format(n=dict((k, _(v)) for k, v in NIVELES).get(c["aviso"], "")))
        if c["cifrado"]:
            lineas.append(_("Cifrado OMEMO activado"))
        mostrar_aviso_dialogo("\n".join(lineas), _("Información de {n}").format(n=c["nombre"]))

    def _x_consulta(self, funcion: str, titulo: str, vacio: str):
        datos = self._x_chat_de_fila()
        if not datos or not self._x_conectada(datos[0]):
            return
        acc, jid = datos
        nombre = datos_xmpp.titulo_chat(acc, jid)
        self.avisar(_("Consultando…"))

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo consultar: {e}").format(e=err))
            elif not res:
                mostrar_aviso_dialogo(vacio, titulo.format(n=nombre))
            else:
                mostrar_aviso_dialogo("\n".join(f"{etq}: {val}" for etq, val in res), titulo.format(n=nombre))
        ejecutar(lambda: getattr(_servicio(), funcion)(acc, jid), _fin)

    def x_vcard(self):
        self._x_consulta("ver_vcard", _("Ficha de {n}"), _("Este contacto no tiene ficha (vCard) publicada."))

    def x_info_tecnica(self):
        self._x_consulta("info_tecnica", _("Información técnica de {n}"),
                         _("El contacto no respondió a las consultas (puede estar sin conexión o no permitirlas)."))

    def x_zumbido(self):
        datos = self._x_chat_de_fila()
        if not datos or self._x_es_sala(*datos) or not self._x_conectada(datos[0]):
            return
        acc, jid = datos
        ejecutar(lambda: _servicio().enviar_zumbido(acc, jid),
                 lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Zumbido enviado")))

    def x_unirse_sala(self, sala: str = ""):
        acc = self._x_cuenta()
        if acc is None or not self._x_disponible() or not self._x_conectada(acc):
            return
        from .dialogos_xmpp import DialogoUnirseSalaXMPP
        DialogoUnirseSalaXMPP(self, acc, lambda jid: (self._x_refrescar_lista(acc), self._x_abrir_por_jid(acc, jid)),
                              sala).present()

    def x_directorio_salas(self):
        acc = self._x_cuenta()
        if acc is None or not self._x_conectada(acc):
            return
        from .dialogos_xmpp import DialogoDirectorioSalasXMPP
        DialogoDirectorioSalasXMPP(self, acc, lambda jid: self.x_unirse_sala(jid)).present()

    def x_salir_grupo(self, olvidar: bool):
        datos = self._x_chat_de_fila()
        if not datos or not self._x_es_sala(*datos) or not self._x_conectada(datos[0]):
            if datos and not self._x_es_sala(*datos):
                self.avisar(_("Esta acción es de grupos"))
            return
        acc, jid = datos
        nombre = datos_xmpp.titulo_chat(acc, jid)

        def _salir():
            ejecutar(lambda: _servicio().salir_sala(acc, jid, olvidar=olvidar),
                     lambda r, e: (self.avisar(_("No se pudo: {e}").format(e=e)) if e else self.avisar(_("Has salido de {n}").format(n=nombre)),
                                   self._x_refrescar_lista(acc)))
        confirmar_async(self, (_("¿Salir de {n} y olvidarlo (no volver a entrar solo)?") if olvidar
                               else _("¿Salir de {n}?")).format(n=nombre), _salir, etiqueta_si=_("Salir"))

    def x_ocupantes(self):
        datos = self._x_chat_de_fila()
        if not datos or not self._x_conectada(datos[0]):
            return
        if not self._x_es_sala(*datos):
            self.avisar(_("Esta acción es de grupos"))
            return
        from .dialogos_xmpp import DialogoOcupantesXMPP
        DialogoOcupantesXMPP(self, datos[0], datos[1], datos_xmpp.titulo_chat(*datos)).present()

    def x_tema(self):
        datos = self._x_chat_de_fila()
        if not datos or not self._x_conectada(datos[0]):
            return
        if not self._x_es_sala(*datos):
            self.avisar(_("Esta acción es de grupos"))
            return
        acc, jid = datos

        def _poner(tema):
            ejecutar(lambda: _servicio().cambiar_tema(acc, jid, tema),
                     lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Tema cambiado")))
        from .dialogos_xmpp import DialogoMensajeXMPP
        DialogoMensajeXMPP(self, _("Tema de la sala"), _poner, aceptar=_("_Cambiar"), permitir_vacio=True).present()

    # ------------------------------------------------------------------ organización de chats
    def x_alternar(self, que: str):
        datos = self._x_chat_de_fila()
        if not datos:
            return
        acc, jid = datos
        c = datos_xmpp.info_chat(acc, jid)
        xmpp = _servicio()
        if que == "fijar":
            xmpp.fijar_chat(acc, jid, not c["fijado"])
            self.avisar(_("Chat desfijado") if c["fijado"] else _("Chat fijado arriba"))
        elif que == "archivar":
            xmpp.archivar_chat(acc, jid, not c["archivado"])
            self.avisar(_("Chat desarchivado") if c["archivado"] else _("Chat archivado"))
        elif que == "silenciar":
            if c["silenciado"] or xmpp.esta_silenciado(acc, jid):
                xmpp.silenciar_chat(acc, jid, 0)
                self.avisar(_("Silencio quitado"))
            else:
                def _poner(i):
                    xmpp.silenciar_chat(acc, jid, SILENCIOS[i][0])
                    self.avisar(_("Chat silenciado: {t}").format(t=_(SILENCIOS[i][1])))
                    self._x_refrescar_lista(acc)
                DialogoElegir(self, _("Silenciar"), _("_Durante:"), [_(s[1]) for s in SILENCIOS], _poner,
                              aceptar=_("_Silenciar")).present()
                return
        self._x_refrescar_lista(acc)

    def x_nivel_aviso(self):
        datos = self._x_chat_de_fila()
        if not datos:
            return
        acc, jid = datos
        sala = self._x_es_sala(acc, jid)
        niveles = [n for n in NIVELES if sala or n[0] != "mentions"]
        actual = datos_xmpp.info_chat(acc, jid)["aviso"]

        def _elegido(i):
            db.execute("UPDATE xmpp_chats SET notify_level = ? WHERE account_id = ? AND jid = ?", (niveles[i][0], acc, jid))
            self.avisar(_("Nivel de aviso: {n}").format(n=_(niveles[i][1])))
        d = DialogoElegir(self, _("Nivel de aviso"), _("_Avisar:"), [_(n[1]) for n in niveles], _elegido, aceptar=_("_Guardar"))
        d.combo.set_selected(next((i for i, n in enumerate(niveles) if n[0] == actual), 0))
        d.present()

    def x_marcar_leido(self, leido: bool = True):
        datos = self._x_chat_de_fila()
        if not datos:
            return
        acc, jid = datos
        if leido:
            self._x_leer_conversacion(acc, jid)
        else:
            datos_xmpp.marcar_no_leido(acc, jid)
        self._x_refrescar_lista(acc)
        self.avisar(_("Marcado como leído") if leido else _("Marcado como no leído"))

    def x_marcar_todo_leido(self):
        acc = self._x_cuenta()
        if acc is None:
            return
        datos_xmpp.marcar_todo_leido(acc)
        self._x_refrescar_lista(acc)
        self.avisar(_("Todo marcado como leído"))

    def x_vaciar(self):
        datos = self._x_chat_de_fila()
        if not datos:
            return
        acc, jid = datos
        nombre = datos_xmpp.titulo_chat(acc, jid)

        def _hacer():
            ejecutar(lambda: _servicio().vaciar_conversacion(acc, jid),
                     lambda n, e: (self.avisar(_("No se pudo: {e}").format(e=e)) if e else self.avisar(_("{n} mensajes borrados").format(n=n)),
                                   self._x_refrescar_chat(acc, jid)))
        confirmar_async(self, _("¿Borrar todos los mensajes de {n} de este equipo? El contacto se conserva.").format(n=nombre),
                        _hacer, etiqueta_si=_("Borrar"))

    def x_exportar(self):
        datos = self._x_chat_de_fila()
        if not datos:
            return
        acc, jid = datos
        seguro = "".join(c for c in datos_xmpp.titulo_chat(acc, jid) if c.isalnum() or c in "-_") or "chat"
        dlg = Gtk.FileDialog(title=_("Guardar conversación"), initial_name=f"xmpp_{seguro}.txt")

        def _fin(origen, res):
            try:
                ruta = origen.save_finish(res).get_path()
            except Exception:
                return
            ejecutar(lambda: _servicio().exportar_conversacion(acc, jid, ruta),
                     lambda n, e: self.avisar(_("No se pudo exportar: {e}").format(e=e) if e
                                              else _("Conversación exportada ({n} mensajes)").format(n=n)))
        dlg.save(self, None, _fin)

    def x_cifrado(self):
        datos = self._x_chat_de_fila()
        if not datos:
            return
        xmpp = _servicio()
        if not xmpp.omemo_disponible():
            self.avisar(_("Falta el cifrado OMEMO. Instálalo con: pip install slixmpp-omemo"))
            return
        acc, jid = datos
        activo = xmpp.esta_cifrado(acc, jid)
        try:
            nuevo = xmpp.fijar_cifrado(acc, jid, not activo)
        except Exception as e:
            self.avisar(_("No se pudo cambiar el cifrado: {e}").format(e=e))
            return
        self.avisar(_("Cifrado OMEMO activado: los mensajes de esta conversación se enviarán cifrados de extremo a extremo")
                    if nuevo else _("Cifrado OMEMO desactivado"))

    def x_huellas(self):
        datos = self._x_chat_de_fila()
        if not datos or not self._x_conectada(datos[0]):
            return
        if not _servicio().omemo_disponible():
            self.avisar(_("Falta el cifrado OMEMO. Instálalo con: pip install slixmpp-omemo"))
            return
        from .dialogos_xmpp import DialogoHuellasXMPP
        DialogoHuellasXMPP(self, datos[0], datos[1], datos_xmpp.titulo_chat(*datos)).present()

    # ------------------------------------------------------------------ cuenta: conexión y estado
    def x_conectar(self, account_id: int = None):
        acc = account_id or self._x_cuenta()
        if acc is None or not self._x_disponible():
            return
        if datos_xmpp.conectada(acc):
            self.avisar(_("Esta cuenta ya está conectada"))
            return
        self.avisar(_("Conectando con XMPP…"))
        self._x_conectar_en_segundo_plano(acc, avisar=True)

    def _x_conectar_en_segundo_plano(self, acc: int, avisar: bool):
        conectando = self.__dict__.setdefault("_x_conectando", set())
        if acc in conectando:
            return
        conectando.add(acc)

        def _fin(res, err):
            conectando.discard(acc)
            if err:
                if avisar:
                    self.avisar(_("No se pudo conectar: {e}").format(e=err))
                else:
                    log.warning(f"No se pudo conectar la cuenta XMPP {acc}: {err}")
                return
            config.set("xmpp", f"autoconnect_{acc}", True)       # al reiniciar, vuelve a conectarse
            self._x_refrescar_arbol()
            ejecutar(lambda: _servicio().sincronizar_nombres_salas(acc), lambda n, e: n and self._x_refrescar_lista(acc))
        ejecutar(lambda: _servicio().conectar(acc), _fin)

    def x_desconectar(self, account_id: int = None):
        acc = account_id or self._x_cuenta()
        if acc is None:
            return
        if not datos_xmpp.conectada(acc):
            self.avisar(_("Esta cuenta no está conectada"))
            return

        def _fin(res, err):
            config.set("xmpp", f"autoconnect_{acc}", False)      # lo has pedido tú: no se reconecta sola
            self.avisar(_("No se pudo desconectar: {e}").format(e=err) if err else _("Desconectado de XMPP"))
            self._x_refrescar_arbol()
        ejecutar(lambda: _servicio().desconectar(acc), _fin)

    def x_presencia(self):
        acc = self._x_cuenta()
        if acc is None or not self._x_conectada(acc):
            return
        from .dialogos_xmpp import DialogoPresenciaXMPP
        DialogoPresenciaXMPP(self, acc).present()

    def x_animo(self, tipo: str):
        acc = self._x_cuenta()
        if acc is None or not self._x_conectada(acc):
            return
        from .dialogos_xmpp import DialogoAnimoXMPP
        DialogoAnimoXMPP(self, acc, tipo).present()

    def x_invisible(self, invisible: bool):
        acc = self._x_cuenta()
        if acc is None or not self._x_conectada(acc):
            return
        ejecutar(lambda: _servicio().fijar_invisible(acc) if invisible else _servicio().fijar_visible(acc),
                 lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e
                                          else (_("Ahora eres invisible") if invisible else _("Vuelves a ser visible"))))

    def x_eliminar_cuenta(self):
        n = self.nodo_actual
        if n is None or n.servicio != "xmpp" or not n.account_id:
            self.avisar(_("Selecciona una cuenta de XMPP"))
            return
        acc = n.account_id
        nombre = next((nm for i, nm in datos_xmpp.cuentas() if i == acc), "")

        def _hacer():
            def _trabajo():
                _servicio().eliminar_cuenta(acc)
                db.execute("DELETE FROM accounts WHERE id = ?", (acc,))

            def _fin(res, err):
                datos_xmpp.EXTRA.pop(acc, None)
                self.cargar_arbol(conservar=False)
                self.avisar(_("No se pudo: {e}").format(e=err) if err else _("Cuenta eliminada de este equipo"))
            ejecutar(_trabajo, _fin)
        confirmar_async(self, _("¿Eliminar la cuenta {n} y sus datos de este equipo? La cuenta del servidor no se toca.").format(n=nombre),
                        _hacer, etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ servidor, pasarelas, ficha y cuenta
    def _x_lista(self):
        """La cuenta de XMPP conectada sobre la que actuar, o None (avisa de lo que falte)."""
        acc = self._x_cuenta()
        if acc is None or not self._x_disponible() or not self._x_conectada(acc):
            return None
        return acc

    def x_comandos_servidor(self):
        acc = self._x_lista()
        if acc is not None:
            from .dialogos_xmpp_extra import DialogoComandosXMPP
            DialogoComandosXMPP(self, acc).present()

    def x_comandos_de(self):
        acc = self._x_lista()
        if acc is None:
            return
        from .dialogos_xmpp_extra import DialogoComandosXMPP
        datos = self._x_chat_de_fila()
        if datos:
            return DialogoComandosXMPP(self, acc, datos[1], _("Comandos de {j}").format(j=datos[1])).present()
        self.x_comandos_servidor()

    def x_pasarelas(self):
        acc = self._x_lista()
        if acc is not None:
            from .dialogos_xmpp_extra import DialogoPasarelasXMPP
            DialogoPasarelasXMPP(self, acc).present()

    def x_buscar_usuarios(self):
        acc = self._x_lista()
        if acc is None:
            return

        def _con(servicio):
            from .dialogos_xmpp_extra import DialogoBuscarUsuariosXMPP
            DialogoBuscarUsuariosXMPP(self, acc, servicio).present()
        d = DialogoTexto(self, _("Buscar usuarios"), _("_Servicio de búsqueda del servidor:"), _con,
                         _("Si no lo sabes, prueba el sugerido o «vjud.tu-servidor»."), _("_Buscar"))
        d.entrada.set_text("search." + (_servicio().dominio_servidor(acc) or ""))
        d.present()

    def x_editar_ficha(self):
        acc = self._x_lista()
        if acc is None:
            return

        def _fin(valores, err):
            from .dialogos_xmpp_extra import DialogoEditarFichaXMPP
            DialogoEditarFichaXMPP(self, acc, valores or {}).present()
        ejecutar(lambda: _servicio().mi_vcard_campos(acc), _fin)

    def x_cambiar_contrasena(self):
        acc = self._x_lista()
        if acc is not None:
            from .dialogos_xmpp_extra import cambiar_contrasena
            cambiar_contrasena(self, acc)

    def x_crear_cuenta_nueva(self):
        if not self._x_disponible():
            return
        from .dialogos_xmpp_extra import crear_cuenta_nueva
        crear_cuenta_nueva(self, lambda: (self.cargar_arbol(conservar=False), self.iniciar_tiempo_real_xmpp()))

    def x_cancelar_cuenta_servidor(self):
        acc = self._x_lista()
        if acc is not None:
            from .dialogos_xmpp_extra import cancelar_cuenta_servidor
            cancelar_cuenta_servidor(self, acc, lambda: (datos_xmpp.EXTRA.pop(acc, None), self.cargar_arbol(conservar=False)))

    # ------------------------------------------------------------------ refrescos y lectura
    def _x_refrescar_arbol(self):
        """Reconstruye el árbol (el nombre dice si está desconectada y aparecen las pasarelas) sin perder dónde se está."""
        self.cargar_arbol()

    def _x_refrescar_lista(self, account_id: int):
        n = self.nodo_actual
        if n is not None and n.servicio == "xmpp" and n.account_id == account_id and n.listable:
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _x_refrescar_chat(self, account_id: int, jid: str, ir_al_final: bool = False):
        n = self.nodo_actual
        if n is None or n.servicio != "xmpp" or n.account_id != account_id:
            return
        if datos_xmpp.jid_de_chat(n.timeline) != jid:
            return self._x_refrescar_lista(account_id)
        obj = self._fila_actual()
        if ir_al_final:
            def _al_final(filas):
                if filas:
                    self.seleccion_lista.set_selected(len(filas) - 1)
            self.cargar_lista(incremental=True, despues=_al_final)
        else:
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _x_leer_conversacion(self, account_id: int, jid: str):
        """Marca un chat como leído aquí y, en un 1:1 con la cuenta conectada, avisa al contacto (marcador de lectura)."""
        if datos_xmpp.conectada(account_id) and not self._x_es_sala(account_id, jid):
            ejecutar(lambda: _servicio().marcar_leido(account_id, jid), lambda r, e: None)
        else:
            datos_xmpp.marcar_leido(account_id, jid)

    def _x_al_cambiar_nodo(self, anterior, nuevo):
        """Al dejar una conversación se da por leída."""
        if anterior is None or anterior is nuevo or anterior.servicio != "xmpp":
            return
        jid = datos_xmpp.jid_de_chat(anterior.timeline)
        if jid is not None:
            self._x_leer_conversacion(anterior.account_id, jid)

    # ------------------------------------------------------------------ tiempo real
    def iniciar_tiempo_real_xmpp(self):
        """Se suscribe a los eventos del servicio y conecta las cuentas que quedaron conectadas la última vez."""
        if not HAS_SLIXMPP:
            return
        if not getattr(self, "_x_suscrito", False):
            self._x_suscrito = True
            self._x_pendientes = {}
            event_bus.subscribe("xmpp_evento", self._x_ev)
        for acc, _nombre in datos_xmpp.cuentas():
            if config.get("xmpp", f"autoconnect_{acc}", True):
                self._x_conectar_en_segundo_plano(acc, avisar=False)

    def detener_tiempo_real_xmpp(self):
        if getattr(self, "_x_suscrito", False):
            self._x_suscrito = False
            event_bus.unsubscribe("xmpp_evento", self._x_ev)

    def _x_ev(self, datos):
        llamar_en_ui(self._x_novedad, datos or {})

    def _x_novedad(self, datos: dict):
        """Llega en el hilo de la UI. Las ráfagas (la presencia de una pasarela es un torrente) se juntan en un repintado."""
        acc, tipo = datos.get("account_id"), datos.get("tipo")
        if acc is None:
            return
        if tipo == "zumbido":
            speak(_("{n} te ha enviado un zumbido").format(n=datos.get("nombre") or datos.get("jid") or ""), interrupt=False)
        elif tipo == "solicitud":
            speak(_("{j} quiere agregarte a sus contactos").format(j=datos.get("jid") or ""), interrupt=False)
        elif tipo == "invitacion_muc":
            speak(_("Te han invitado a la sala {j}").format(j=datos.get("room_jid") or ""), interrupt=False)
        elif tipo == "chatstate":
            self._x_escribiendo(acc, datos)
        if tipo not in EVENTOS_LISTA:
            return
        info = self._x_pendientes.setdefault(acc, {"chats": set(), "nuevos": [], "arbol": False})
        if tipo in ("conectado", "desconectado"):
            info["arbol"] = True
            speak(_("Conectado a XMPP") if tipo == "conectado" else _("Se perdió la conexión con XMPP; reintentando"),
                  interrupt=False)
        if datos.get("chat_jid"):
            info["chats"].add(datos["chat_jid"])
            if tipo == "mensaje" and not datos.get("propio"):
                info["nuevos"].append(datos["chat_jid"])
        if not info.get("programado"):
            info["programado"] = True
            GLib.timeout_add(ANTIRREBOTE_MS, self._x_vaciar, acc)

    def _x_escribiendo(self, acc: int, datos: dict):
        """«X está escribiendo…» en la barra de estado, sin voz (sería ruido) y solo en la conversación abierta."""
        abierto = self._x_chat_abierto()
        if abierto and abierto == (acc, datos.get("jid")):
            nombre = datos.get("nick") or datos_xmpp.titulo_chat(acc, datos.get("jid"))
            self.estado.set_text(_("{n} está escribiendo…").format(n=nombre) if datos.get("estado") == "composing"
                                 else self.nodo_actual.titulo)

    def _x_vaciar(self, account_id):
        info = self._x_pendientes.pop(account_id, {"chats": set(), "nuevos": [], "arbol": False})
        if info["arbol"]:
            self._x_refrescar_arbol()
        n = self.nodo_actual
        if n is not None and n.servicio == "xmpp" and n.account_id == account_id and n.listable and not info["arbol"]:
            abierto = datos_xmpp.jid_de_chat(n.timeline)
            if abierto is None or abierto in info["chats"] or not info["chats"]:
                obj = self._fila_actual()
                self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
        self._x_anunciar(account_id, info["nuevos"])
        return GLib.SOURCE_REMOVE

    def _x_anunciar(self, account_id, chats):
        """Dice por voz lo que llega a la conversación abierta y a los chats privados, según el nivel de aviso."""
        if not chats or not config.get("xmpp", "announce_new", True):
            return
        xmpp_cargado = datos_xmpp.cargado()
        abierto = self._x_chat_abierto()
        dichos = 0
        for jid in dict.fromkeys(chats):
            c = datos_xmpp.info_chat(account_id, jid)
            ultimo = datos_xmpp.ultimo_ajeno(account_id, jid)
            if not ultimo or c["aviso"] == "none":
                continue
            quien, texto, tipo, mencion = ultimo
            sala = c["tipo"] == "muc"
            es_este = abierto == (account_id, jid)
            if xmpp_cargado and _servicio().esta_silenciado(account_id, jid) and not es_este:
                continue
            if sala and not es_este and not mencion:
                continue
            if sala and c["aviso"] == "mentions" and not mencion:
                continue
            cuerpo = " ".join(texto.split())[:300] or _("Archivo adjunto")
            if tipo == "media":
                cuerpo = _("Archivo adjunto")
            speak((_("{n} te menciona en {c}: {t}").format(n=quien, c=c["nombre"], t=cuerpo) if sala and mencion and not es_este
                   else f"{quien}: {cuerpo}"), interrupt=False)
            dichos += 1
            if dichos >= ANUNCIAR_MAX:
                break

    # ------------------------------------------------------------------ acciones
    def _registrar_acciones_xmpp(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("x-agregar-contacto", self.x_agregar_contacto)
        a("x-aceptar", lambda: self.x_abrir_fila())
        a("x-rechazar", self.x_rechazar_solicitud)
        a("x-renombrar", self.x_renombrar)
        a("x-bloquear", self.x_bloquear)
        a("x-quitar-contacto", self.x_quitar_contacto)
        a("x-vcard", self.x_vcard)
        a("x-info-tecnica", self.x_info_tecnica)
        a("x-zumbido", self.x_zumbido)
        a("x-unirse-sala", self.x_unirse_sala)
        a("x-directorio-salas", self.x_directorio_salas)
        a("x-salir-grupo", lambda: self.x_salir_grupo(False))
        a("x-olvidar-grupo", lambda: self.x_salir_grupo(True))
        a("x-ocupantes", self.x_ocupantes)
        a("x-tema", self.x_tema)
        a("x-reaccionar", self.x_reaccionar)
        a("x-copiar", self.x_copiar)
        a("x-enviar-archivo", self.x_enviar_archivo)
        a("x-descargar", self.x_descargar)
        a("x-abrir-adjunto", self.x_abrir_adjunto)
        a("x-fijar", lambda: self.x_alternar("fijar"))
        a("x-archivar", lambda: self.x_alternar("archivar"))
        a("x-silenciar", lambda: self.x_alternar("silenciar"))
        a("x-nivel-aviso", self.x_nivel_aviso)
        a("x-leido", lambda: self.x_marcar_leido(True))
        a("x-no-leido", lambda: self.x_marcar_leido(False))
        a("x-todo-leido", self.x_marcar_todo_leido)
        a("x-vaciar", self.x_vaciar)
        a("x-exportar", self.x_exportar)
        a("x-cifrado", self.x_cifrado)
        a("x-huellas", self.x_huellas)
        a("x-conectar", self.x_conectar)
        a("x-desconectar", self.x_desconectar)
        a("x-presencia", self.x_presencia)
        a("x-animo", lambda: self.x_animo("mood"))
        a("x-actividad", lambda: self.x_animo("activity"))
        a("x-invisible", lambda: self.x_invisible(True))
        a("x-visible", lambda: self.x_invisible(False))
        a("x-eliminar-cuenta", self.x_eliminar_cuenta)
        a("x-comandos-servidor", self.x_comandos_servidor)
        a("x-comandos-de", self.x_comandos_de)
        a("x-pasarelas", self.x_pasarelas)
        a("x-buscar-usuarios", self.x_buscar_usuarios)
        a("x-editar-ficha", self.x_editar_ficha)
        a("x-cambiar-contrasena", self.x_cambiar_contrasena)
        a("x-crear-cuenta-nueva", self.x_crear_cuenta_nueva)
        a("x-cancelar-cuenta-servidor", self.x_cancelar_cuenta_servidor)
