# -*- coding: utf-8 -*-
"""IRC en la ventana principal (mixin de VentanaPrincipal).

Cada red es una cuenta con los buffers «Canales», «Privados» y «Avisos del servidor». Intro sobre un chat abre su
conversación como un buffer más (Ctrl+W lo cierra). Ctrl+N escribe en el chat; lo que empieza por barra es un
comando (/help los enseña). Los mensajes llegan en tiempo real por el bus de eventos del servicio."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.plataforma import llamar_en_ui, mostrar_aviso_dialogo
from app.core.speech import copiar_al_portapapeles, speak
from app.services.irc.irc_common import HAS_PYDLE, IRCError
from app.services.irc.irc_comandos import ejecutar_comando
from app.services.irc.irc_service import irc_service

from . import datos_irc
from .dialogos import DialogoTexto, confirmar_async, ejecutar

log = logging.getLogger(__name__)

ANUNCIAR_MAX = 3            # anuncios por voz de una misma ráfaga de mensajes
ANTIRREBOTE_MS = 500        # las ráfagas del tiempo real se juntan en un solo repintado
EVENTOS_ESTADO = ("desconectado", "conectado", "nick_error", "nick_cambiado", "amigo_online", "amigo_offline", "sts",
                   "invitacion", "ctcp_respuesta", "oper_ok", "kill", "mis_modos")
EVENTOS_CHAT = ("mensaje", "notice", "join", "part", "quit", "kick", "topic", "modo_canal", "historico")
NIVELES = [("all", "Avisar de todo"), ("mentions", "Solo cuando me mencionen"), ("none", "Silencio")]


class _IRCMixin:
    # ------------------------------------------------------------------ contexto
    def _irc_ctx(self) -> bool:
        """True si lo que está enfocado (fila o, si no hay, nodo del árbol) es de IRC."""
        obj = self._fila_actual()
        if obj is not None:
            return obj.fila.servicio == "irc"
        n = self.nodo_actual
        return n is not None and n.servicio == "irc"

    def _irc_cuenta(self):
        n = self.nodo_actual
        if n is not None and n.servicio == "irc" and n.account_id:
            return n.account_id
        cuentas = datos_irc.cuentas()
        if len(cuentas) == 1:
            return cuentas[0][0]
        self.avisar(_("Selecciona una red de IRC"))
        return None

    def _irc_disponible(self) -> bool:
        if not HAS_PYDLE:
            self.avisar(_("Falta pydle. Instálalo con: pip install pydle"))
            return False
        return True

    def _irc_conectada(self, account_id: int, avisar: bool = True) -> bool:
        if datos_irc.conectada(account_id):
            return True
        if avisar:
            self.avisar(_("La red no está conectada. Conéctala desde el menú IRC."))
        return False

    def _irc_chat_abierto(self):
        """(account_id, nombre) de la conversación abierta o del buffer de avisos, o None."""
        n = self.nodo_actual
        if n is not None and n.servicio == "irc":
            nombre = datos_irc.nombre_de_chat(n.timeline)
            if nombre is not None:
                return n.account_id, nombre
            if n.timeline == "server":
                return n.account_id, datos_irc.SERVIDOR
        return None

    def _irc_chat_de_fila(self, avisar: bool = True):
        """(account_id, nombre del chat) del chat enfocado, de la conversación abierta, o None."""
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "irc" and obj.fila.extra.get("chat"):
            return obj.fila.extra["account_id"], obj.fila.extra["chat"]
        abierto = self._irc_chat_abierto()
        if abierto:
            return abierto
        if avisar:
            self.avisar(_("Selecciona un chat de IRC"))
        return None

    def _irc_canal_de_fila(self):
        """Como `_irc_chat_de_fila`, pero exige que sea un canal."""
        datos = self._irc_chat_de_fila()
        if datos and not datos_irc.es_canal(datos[1]):
            self.avisar(_("Esta acción es de canales"))
            return None
        return datos

    # ------------------------------------------------------------------ abrir y actualizar
    def irc_actualizar_nodo(self, nodo, silencioso: bool = False):
        """Los datos llegan solos por el tiempo real: actualizar es releer la lista y avisar si la red está caída."""
        if nodo is self.nodo_actual:
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
        if not silencioso and not datos_irc.conectada(nodo.account_id):
            self.avisar(_("La red no está conectada. Conéctala desde el menú IRC."))

    def irc_abrir_fila(self):
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "irc" and obj.fila.extra.get("tipo") == "chat":
            self.irc_abrir_chat(obj)
        else:
            self.avisar(_("Selecciona un chat de la lista"))

    def irc_abrir_chat(self, obj):
        x = obj.fila.extra
        self._irc_abrir_por_nombre(x["account_id"], x["chat"])

    def _irc_abrir_por_nombre(self, account_id: int, nombre: str):
        self._abrir_buffer(account_id, datos_irc.clave_chat(nombre), datos_irc.titulo_chat(account_id, nombre),
                           servicio="irc")

    def _irc_abrir_privado(self, account_id: int, nick: str):
        """Abre (creándolo si hace falta) el chat privado con un apodo."""
        irc_service.abrir_privado(account_id, nick)
        self._irc_abrir_por_nombre(account_id, nick)

    # ------------------------------------------------------------------ escribir
    def irc_nuevo(self):
        """Ctrl+N: escribe en el chat abierto o enfocado (en Avisos, en la consola del servidor); si no, entra en un canal."""
        if not self._irc_disponible():
            return
        datos = self._irc_chat_de_fila(avisar=False)
        if datos:
            return self.irc_escribir(*datos)
        self.irc_unirse()

    def irc_escribir(self, account_id: int, chat: str, texto_inicial: str = ""):
        if not self._irc_conectada(account_id):
            return
        from .dialogos_irc import DialogoEscribirIRC
        if chat == datos_irc.SERVIDOR:
            titulo = _("Consola del servidor")
        else:
            titulo = _("Mensaje en {c}").format(c=datos_irc.titulo_chat(account_id, chat))
        DialogoEscribirIRC(self, titulo, lambda texto: self._irc_enviar(account_id, chat, texto), texto_inicial).present()

    def irc_responder(self):
        """Ctrl+R: escribe en el chat con el apodo del mensaje enfocado por delante, como se menciona en IRC."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "irc" or obj.fila.extra.get("tipo") != "mensaje":
            return self.irc_nuevo()
        x = obj.fila.extra
        nick = x.get("nick") or ""
        prefijo = f"{nick}: " if nick and not x.get("propio") and datos_irc.es_canal(x["chat"]) else ""
        self.irc_escribir(x["account_id"], x["chat"], prefijo)

    def _irc_enviar(self, account_id: int, chat: str, texto: str):
        es_comando = texto.startswith("/") and not texto.startswith("//")
        if texto.startswith("//"):
            texto = texto[1:]       # «//» al principio envía una barra literal

        def _hacer():
            try:
                if es_comando:
                    return ejecutar_comando(account_id, chat, texto)
                if chat == datos_irc.SERVIDOR:
                    irc_service.comando_crudo(account_id, texto)     # en la consola, el texto suelto va al servidor
                else:
                    irc_service.enviar_mensaje(account_id, chat, texto)
            except IRCError as e:
                return {"error": str(e)}        # un error de uso no es un fallo del programa: no merece traza
            return None

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo enviar: {e}").format(e=err))
                return
            if res and res.get("error"):
                self.avisar(res["error"])
                return
            self._irc_refrescar_chat(account_id, chat, ir_al_final=True)
            if not res:
                if es_comando:
                    speak(_("Hecho"), interrupt=False)
                return
            if res.get("texto"):
                mostrar_aviso_dialogo(res["texto"], res.get("titulo") or "IRC")
            if res.get("privado"):
                self._irc_abrir_privado(account_id, res["privado"])
            if res.get("limpiar"):
                self.avisar(_("La vista no acumula pantalla: el historial se conserva"))
            if res.get("aviso"):
                self.avisar(res["aviso"])
        ejecutar(_hacer, _fin)

    # ------------------------------------------------------------------ canales y chats
    def irc_unirse(self, canal: str = None):
        acc = self._irc_cuenta()
        if acc is None or not self._irc_disponible() or not self._irc_conectada(acc):
            return

        def _entrar(nombre):
            nombre = nombre if nombre.startswith(("#", "&", "+", "!")) else "#" + nombre

            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo entrar en {c}: {e}").format(c=nombre, e=err))
                    return
                self.avisar(_("Dentro de {c}").format(c=nombre))
                self._irc_abrir_por_nombre(acc, nombre)
            ejecutar(lambda: irc_service.unirse_canal(acc, nombre), _fin)
        if canal:
            return _entrar(canal)
        DialogoTexto(self, _("Unirse a un canal"), _("_Canal (por ejemplo #nvda):"), _entrar,
                     aceptar=_("_Unirse")).present()

    def irc_salir(self):
        datos = self._irc_canal_de_fila()
        if not datos or not self._irc_conectada(datos[0]):
            return
        acc, canal = datos

        def _fin(res, err):
            self.avisar(_("No se pudo salir de {c}: {e}").format(c=canal, e=err) if err
                        else _("Has salido de {c}").format(c=canal))
            self.cargar_lista(incremental=True)
        ejecutar(lambda: irc_service.salir_canal(acc, canal), _fin)

    def irc_borrar(self):
        """Supr sobre un chat de la lista: lo quita con su historial (y sale del canal si seguía dentro)."""
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "irc" or obj.fila.extra.get("tipo") != "chat":
            self.avisar(_("Selecciona un chat de la lista"))
            return
        x = obj.fila.extra
        acc, nombre = x["account_id"], x["chat"]

        def _hacer():
            if x.get("unido") and not x.get("privado") and datos_irc.conectada(acc):
                ejecutar(lambda: irc_service.salir_canal(acc, nombre), lambda r, e: None)
            datos_irc.eliminar_chat(acc, nombre)
            self.cargar_lista(incremental=True)
            self.avisar(_("Chat eliminado"))
        confirmar_async(self, _("¿Eliminar el chat {c} y su historial local?").format(c=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    def irc_canales_servidor(self):
        acc = self._irc_cuenta()
        if acc is None or not self._irc_conectada(acc):
            return
        from .dialogos_irc import DialogoCanalesServidorIRC

        def _unirse(canal):
            self.irc_unirse(canal)
        DialogoCanalesServidorIRC(self, acc, _unirse).present()

    def irc_usuarios(self):
        datos = self._irc_canal_de_fila()
        if not datos:
            return
        from .dialogos_irc import DialogoUsuariosIRC
        acc, canal = datos
        DialogoUsuariosIRC(self, acc, canal, lambda nick: self._irc_abrir_privado(acc, nick)).present()

    def irc_whois(self):
        """Datos de un usuario: el del mensaje enfocado o, si no hay, el que se pregunte."""
        acc = self._irc_cuenta()
        if acc is None or not self._irc_conectada(acc):
            return
        from .dialogos_irc import consultar_whois
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "irc" and obj.fila.extra.get("tipo") == "mensaje" \
                and obj.fila.extra.get("nick"):
            return consultar_whois(acc, obj.fila.extra["nick"])
        DialogoTexto(self, _("Datos de un usuario"), _("_Apodo:"), lambda nick: consultar_whois(acc, nick),
                     aceptar=_("_Consultar")).present()

    def irc_info(self):
        """Ctrl+Alt+P: sobre un mensaje, los datos de quien lo escribió; sobre un chat, su información."""
        obj = self._fila_actual()
        if obj is not None and obj.fila.servicio == "irc" and obj.fila.extra.get("tipo") == "mensaje" \
                and obj.fila.extra.get("nick") and not obj.fila.extra.get("propio"):
            return self.irc_whois()
        return self.irc_info_canal()

    def irc_info_canal(self):
        datos = self._irc_chat_de_fila()
        if not datos:
            return
        acc, nombre = datos
        info = datos_irc.info_chat(acc, nombre)
        lineas = [info["nombre"], _("Privado") if info["privado"] else _("Canal")]
        if not info["privado"]:
            lineas.append(_("Unido") if info["unido"] else _("No unido"))
            if info["tema"]:
                lineas.append(_("Tema: {t}").format(t=info["tema"]))
            if datos_irc.conectada(acc):
                usuarios = irc_service.usuarios_de_canal(acc, nombre)
                if usuarios:
                    lineas.append(_("{n} usuarios").format(n=len(usuarios)))
                rango = irc_service.mi_rango_en(acc, nombre)
                if rango:
                    lineas.append(_("Tu rango: {r}").format(r=rango))
        lineas.append(_("Nivel de aviso: {n}").format(n=dict((k, _(v)) for k, v in NIVELES).get(info["aviso"], "")))
        if info["no_leidos"]:
            lineas.append(_("{n} sin leer").format(n=info["no_leidos"]))
        mostrar_aviso_dialogo("\n".join(lineas), _("Información del chat"))

    def irc_tema(self):
        datos = self._irc_canal_de_fila()
        if not datos or not self._irc_conectada(datos[0]):
            return
        acc, canal = datos
        actual = datos_irc.info_chat(acc, canal)["tema"]
        from .dialogos import DialogoBase
        d = DialogoBase(self, _("Tema de {c}").format(c=canal), _("_Cambiar"))
        entrada = d.campo(_("_Tema:"), Gtk.Entry(text=actual, activates_default=True, width_chars=60))
        d.set_focus(entrada)

        def _poner():
            tema = entrada.get_text()
            d.close()

            def _fin(res, err):
                self.avisar(_("No se pudo cambiar el tema: {e}").format(e=err) if err else _("Tema cambiado"))
            ejecutar(lambda: irc_service.cambiar_tema(acc, canal, tema), _fin)
        d.aceptar = _poner
        d.present()

    def irc_modos(self):
        datos = self._irc_canal_de_fila()
        if not datos or not self._irc_conectada(datos[0]):
            return
        from .dialogos_irc import DialogoModosCanalIRC
        DialogoModosCanalIRC(self, *datos).present()

    def irc_baneos(self):
        datos = self._irc_canal_de_fila()
        if not datos or not self._irc_conectada(datos[0]):
            return
        from .dialogos_irc import DialogoBaneosIRC
        DialogoBaneosIRC(self, *datos).present()

    def irc_nivel_aviso(self):
        datos = self._irc_canal_de_fila()
        if not datos:
            return
        acc, canal = datos
        from .dialogos import DialogoElegir
        actual = datos_irc.info_chat(acc, canal)["aviso"]

        def _elegido(i):
            irc_service.fijar_nivel_aviso(acc, canal, NIVELES[i][0])
            self.avisar(_("Nivel de aviso de {c}: {n}").format(c=canal, n=_(NIVELES[i][1])))
        d = DialogoElegir(self, _("Nivel de aviso"), _("_Avisar:"), [_(n) for _k, n in NIVELES], _elegido,
                          aceptar=_("_Guardar"))
        d.combo.set_selected(next((i for i, (k, _n) in enumerate(NIVELES) if k == actual), 0))
        d.present()

    def irc_alternar_eventos(self):
        datos = self._irc_canal_de_fila()
        if not datos:
            return
        acc, canal = datos
        mostrar = not irc_service.mostrar_eventos(acc, canal)
        irc_service.fijar_mostrar_eventos(acc, canal, mostrar)
        self._irc_refrescar_chat(acc, canal)
        self.avisar(_("Entradas y salidas visibles en {c}").format(c=canal) if mostrar
                    else _("Entradas y salidas ocultas en {c}").format(c=canal))

    def irc_exportar(self):
        datos = self._irc_chat_de_fila()
        if not datos or datos[1] == datos_irc.SERVIDOR:
            return
        acc, canal = datos
        seguro = "".join(c for c in canal if c.isalnum() or c in "#-_").lstrip("#") or "chat"
        dlg = Gtk.FileDialog(title=_("Guardar conversación"), initial_name=f"irc_{seguro}.txt")

        def _fin(origen, res):
            try:
                ruta = origen.save_finish(res).get_path()
            except Exception:
                return
            ejecutar(lambda: irc_service.exportar_chat(acc, canal, ruta),
                     lambda n, err: self.avisar(_("No se pudo exportar: {e}").format(e=err) if err
                                                else _("Conversación exportada ({n} líneas)").format(n=n)))
        dlg.save(self, None, _fin)

    def irc_copiar(self):
        obj = self._fila_actual()
        if obj is None or obj.fila.servicio != "irc" or obj.fila.extra.get("tipo") != "mensaje":
            self.avisar(_("Selecciona un mensaje de IRC"))
            return
        copiar_al_portapapeles(obj.fila.extra.get("texto") or obj.fila.titulo)
        self.avisar(_("Texto copiado"))

    def irc_marcar_leido(self):
        datos = self._irc_chat_de_fila()
        if not datos:
            return
        datos_irc.marcar_leido(*datos)
        self._irc_refrescar_lista(datos[0])
        self.avisar(_("Marcado como leído"))

    def irc_marcar_todo_leido(self):
        acc = self._irc_cuenta()
        if acc is None:
            return
        datos_irc.marcar_todo_leido(acc)
        self._irc_refrescar_lista(acc)
        self.avisar(_("Todo marcado como leído"))

    # ------------------------------------------------------------------ red: conexión y estado propio
    def irc_conectar(self, account_id: int = None):
        acc = account_id or self._irc_cuenta()
        if acc is None or not self._irc_disponible():
            return
        if datos_irc.conectada(acc):
            self.avisar(_("Esta red ya está conectada"))
            return
        self.avisar(_("Conectando a la red…"))
        self._irc_conectar_en_segundo_plano(acc, avisar=True)

    def _irc_conectar_en_segundo_plano(self, acc: int, avisar: bool):
        conectando = self.__dict__.setdefault("_irc_conectando", set())
        if acc in conectando:
            return
        conectando.add(acc)

        def _fin(res, err):
            conectando.discard(acc)
            if err:
                if avisar:
                    self.avisar(_("No se pudo conectar: {e}").format(e=err))
                else:
                    log.warning(f"No se pudo conectar con la red IRC {acc}: {err}")
                return
            config.set("irc", f"autoconnect_{acc}", True)       # al reiniciar, vuelve a conectarse
        ejecutar(lambda: irc_service.conectar(acc), _fin)

    def irc_desconectar(self, account_id: int = None):
        acc = account_id or self._irc_cuenta()
        if acc is None:
            return
        if not datos_irc.conectada(acc):
            self.avisar(_("Esta red no está conectada"))
            return

        def _fin(res, err):
            config.set("irc", f"autoconnect_{acc}", False)      # lo has pedido tú: ni el arranque ni el vigilante la reconectan
            self.avisar(_("No se pudo desconectar: {e}").format(e=err) if err else _("Desconectado de la red"))
            self._irc_refrescar_arbol()
        ejecutar(lambda: irc_service.desconectar(acc), _fin)

    def irc_consola(self):
        acc = self._irc_cuenta()
        if acc is not None:
            self.irc_escribir(acc, datos_irc.SERVIDOR)

    def irc_ausente(self):
        acc = self._irc_cuenta()
        if acc is None or not self._irc_conectada(acc):
            return

        def _poner(mensaje):
            ejecutar(lambda: irc_service.fijar_ausente(acc, mensaje),
                     lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Estás marcado como ausente")))
        DialogoTexto(self, _("Marcarme como ausente"), _("_Mensaje de ausencia:"), _poner, aceptar=_("_Marcar")).present()

    def irc_disponible(self):
        acc = self._irc_cuenta()
        if acc is None or not self._irc_conectada(acc):
            return
        ejecutar(lambda: irc_service.volver_disponible(acc),
                 lambda r, e: self.avisar(_("No se pudo: {e}").format(e=e) if e else _("Vuelves a estar disponible")))

    def irc_cambiar_nick(self):
        acc = self._irc_cuenta()
        if acc is None or not self._irc_conectada(acc):
            return

        def _poner(nick):
            ejecutar(lambda: irc_service.cambiar_nick(acc, nick),
                     lambda r, e: self.avisar(_("No se pudo cambiar el apodo: {e}").format(e=e) if e else None))
        DialogoTexto(self, _("Cambiar de apodo"), _("_Nuevo apodo:"), _poner, aceptar=_("_Cambiar")).present()

    def irc_whowas(self):
        acc = self._irc_cuenta()
        if acc is None or not self._irc_conectada(acc):
            return

        def _consultar(nick):
            def _fin(res, err):
                if err:
                    self.avisar(_("No se pudo: {e}").format(e=err))
                else:
                    mostrar_aviso_dialogo(res, _("Datos anteriores de {n}").format(n=nick))
            ejecutar(lambda: irc_service.whowas_legible(acc, nick), _fin)
        DialogoTexto(self, _("Datos de un apodo desconectado"), _("_Apodo:"), _consultar,
                     aceptar=_("_Consultar")).present()

    def irc_ignorados(self):
        acc = self._irc_cuenta()
        if acc is not None:
            from .dialogos_irc import DialogoIgnoradosIRC
            DialogoIgnoradosIRC(self, acc).present()

    def irc_amigos(self):
        acc = self._irc_cuenta()
        if acc is not None:
            from .dialogos_irc import DialogoAmigosIRC
            DialogoAmigosIRC(self, acc).present()

    def irc_editar_red(self):
        acc = self._irc_cuenta()
        if acc is None:
            return
        from .dialogos_irc import DialogoRedIRC
        DialogoRedIRC(self, acc, lambda ident: self.cargar_arbol()).present()

    def irc_eliminar_red(self):
        n = self.nodo_actual
        if n is None or n.servicio != "irc" or not n.account_id:
            self.avisar(_("Selecciona una red de IRC"))
            return
        acc = n.account_id
        nombre = next((nm for i, nm in datos_irc.cuentas() if i == acc), "")

        def _hacer():
            irc_service.eliminar_cuenta(acc)
            datos_irc.EXTRA.pop(acc, None)
            self.cargar_arbol(conservar=False)
            self.avisar(_("Red eliminada"))
        confirmar_async(self, _("¿Eliminar la red {n} y todo su historial de este equipo?").format(n=nombre), _hacer,
                        etiqueta_si=_("Eliminar"))

    # ------------------------------------------------------------------ refrescos y lectura
    def _irc_refrescar_arbol(self):
        """Reconstruye el árbol (el nombre de la red dice si está desconectada) sin perder dónde se está."""
        self.cargar_arbol()

    def _irc_refrescar_lista(self, account_id: int):
        n = self.nodo_actual
        if n is not None and n.servicio == "irc" and n.account_id == account_id and n.listable:
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _irc_refrescar_chat(self, account_id: int, chat: str, ir_al_final: bool = False):
        """Repinta la conversación si es la que se ve; con `ir_al_final`, deja el foco en el último mensaje."""
        n = self.nodo_actual
        if n is None or n.servicio != "irc" or n.account_id != account_id:
            return
        abierto = datos_irc.nombre_de_chat(n.timeline)
        es_esta = (abierto is not None and abierto == chat.lower()) or (n.timeline == "server" and chat == datos_irc.SERVIDOR)
        if not es_esta:
            return self._irc_refrescar_lista(account_id)
        obj = self._fila_actual()
        if ir_al_final:
            def _al_final(filas):
                if filas:
                    self.seleccion_lista.set_selected(len(filas) - 1)
            self.cargar_lista(incremental=True, despues=_al_final)
        else:
            self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)

    def _irc_al_cambiar_nodo(self, anterior, nuevo):
        """Al dejar una conversación (o los avisos del servidor) se da por leída."""
        if anterior is None or anterior is nuevo or anterior.servicio != "irc":
            return
        nombre = datos_irc.nombre_de_chat(anterior.timeline)
        if nombre is None and anterior.timeline == "server":
            nombre = datos_irc.SERVIDOR
        if nombre is not None:
            datos_irc.marcar_leido(anterior.account_id, nombre)

    # ------------------------------------------------------------------ tiempo real
    def iniciar_tiempo_real_irc(self):
        """Se suscribe a los eventos del servicio y conecta las redes que quedaron conectadas la última vez."""
        if not HAS_PYDLE:
            return
        if not getattr(self, "_irc_suscrito", False):
            self._irc_suscrito = True
            self._irc_pendientes = {}
            event_bus.subscribe("irc_evento", self._irc_ev)
        for acc, _nombre in datos_irc.cuentas():
            fila = db.query_one("SELECT COALESCE(auto_connect, 0) FROM irc_networks WHERE account_id = ?", (acc,))
            if config.get("irc", f"autoconnect_{acc}", bool(fila[0]) if fila else False):
                self._irc_conectar_en_segundo_plano(acc, avisar=False)

    def detener_tiempo_real_irc(self):
        if getattr(self, "_irc_suscrito", False):
            self._irc_suscrito = False
            event_bus.unsubscribe("irc_evento", self._irc_ev)

    def _irc_ev(self, datos):
        llamar_en_ui(self._irc_novedad, datos or {})

    def _irc_novedad(self, datos: dict):
        """Llega en el hilo de la UI. Las ráfagas se juntan en un solo repintado y en pocos anuncios."""
        acc, tipo = datos.get("account_id"), datos.get("tipo")
        if tipo in EVENTOS_ESTADO:
            self._irc_estado(acc, tipo, datos)
        if tipo not in EVENTOS_CHAT and tipo not in ("conectado", "desconectado"):
            return
        info = self._irc_pendientes.setdefault(acc, {"chats": set(), "nuevos": [], "arbol": False})
        if tipo in ("conectado", "desconectado"):
            info["arbol"] = True
        if datos.get("canal"):
            info["chats"].add(datos["canal"].lower())
        if tipo in ("mensaje", "notice"):
            info["nuevos"].append(datos)
        if not info.get("programado"):
            info["programado"] = True
            GLib.timeout_add(ANTIRREBOTE_MS, self._irc_vaciar, acc)

    def _irc_vaciar(self, account_id):
        info = self._irc_pendientes.pop(account_id, {"chats": set(), "nuevos": [], "arbol": False})
        if info["arbol"]:
            self._irc_refrescar_arbol()
        n = self.nodo_actual
        if n is not None and n.servicio == "irc" and n.account_id == account_id and not info["arbol"]:
            abierto = datos_irc.nombre_de_chat(n.timeline)
            if n.timeline in ("channels", "privates") or abierto in info["chats"] or (
                    n.timeline == "server" and datos_irc.SERVIDOR in info["chats"]):
                obj = self._fila_actual()
                self.cargar_lista(conservar_id=obj.fila.id if obj else None, incremental=True)
        self._irc_anunciar(account_id, info["nuevos"])
        return GLib.SOURCE_REMOVE

    def _irc_anunciar(self, account_id, mensajes):
        """Dice por voz lo que llega a la conversación abierta, los privados y las menciones (según el nivel de aviso)."""
        if not mensajes or not config.get("irc", "announce_new", True):
            return
        n = self.nodo_actual
        abierto = datos_irc.nombre_de_chat(n.timeline) if (n is not None and n.servicio == "irc"
                                                           and n.account_id == account_id) else None
        avisos_abiertos = n is not None and n.servicio == "irc" and n.account_id == account_id and n.timeline == "server"
        dichos = 0
        for m in mensajes:
            canal, nick, texto = (m.get("canal") or ""), (m.get("nick") or ""), " ".join((m.get("texto") or "").split())[:300]
            if not texto:
                continue
            if m.get("tipo") == "notice" and canal == datos_irc.SERVIDOR:
                if not (avisos_abiertos or nick.lower().endswith("serv")):
                    continue
                frase = _("Aviso de {n}: {t}").format(n=nick or _("el servidor"), t=texto)
            elif m.get("privado"):
                frase = f"{nick}: {texto}"
            else:
                nivel = datos_irc.info_chat(account_id, canal)["aviso"]
                if nivel == "none":
                    continue
                if m.get("highlight"):
                    frase = _("{n} te menciona en {c}: {t}").format(n=nick, c=canal, t=texto)
                elif canal.lower() == abierto and nivel == "all":
                    frase = f"{nick}: {texto}"
                else:
                    continue
            speak(frase, interrupt=False)
            dichos += 1
            if dichos >= ANUNCIAR_MAX:
                break

    def _irc_estado(self, acc, tipo, datos):
        """Avisos hablados de los cambios de estado de la red: una caída avisa una vez, no en cada reintento."""
        fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (acc,))
        red = fila[0] if fila else _("IRC")
        if tipo == "desconectado" and not datos.get("esperado"):
            speak(_("Se perdió la conexión con {r}; reintentando en segundo plano.").format(r=red), interrupt=False)
        elif tipo == "conectado" and datos.get("reconexion"):
            speak(_("Reconectado a {r}.").format(r=red), interrupt=False)
        elif tipo == "conectado":
            speak(_("Conectado a {r} como {n}.").format(r=red, n=datos.get("nick") or ""), interrupt=False)
        elif tipo == "nick_error":
            speak(f"{red}: {datos.get('motivo') or ''}", interrupt=False)
        elif tipo == "nick_cambiado":
            speak(_("{r}: ahora eres {n}.").format(r=red, n=datos.get("ahora") or ""), interrupt=False)
        elif tipo == "amigo_online":
            speak(_("{r}: {n} se ha conectado.").format(r=red, n=datos.get("nick") or ""), interrupt=False)
        elif tipo == "amigo_offline":
            speak(_("{r}: {n} se ha desconectado.").format(r=red, n=datos.get("nick") or ""), interrupt=False)
        elif tipo == "sts":
            speak(_("{r}: la red exige conexión segura; se usará TLS (puerto {p}) a partir de la próxima conexión.").format(
                r=red, p=datos.get("puerto") or 6697), interrupt=False)
        elif tipo == "invitacion" and datos.get("canal"):
            canal = datos["canal"]
            confirmar_async(self, _("{n} te invita al canal {c}. ¿Unirte ahora?").format(n=datos.get("nick") or "", c=canal),
                            lambda: self._irc_unirse_en(acc, canal), titulo=_("Invitación a un canal"),
                            etiqueta_si=_("Unirme"))
        elif tipo == "ctcp_respuesta" and datos.get("texto"):
            speak(datos["texto"], interrupt=False)
        elif tipo == "oper_ok":
            speak(_("{r}: ya eres operador de la red.").format(r=red), interrupt=False)
        elif tipo == "kill":
            speak(_("{r}: te han desconectado de la red ({p}: {m}).").format(
                r=red, p=datos.get("por") or "", m=datos.get("razon") or ""), interrupt=False)
        elif tipo == "mis_modos":
            speak(_("{r}: tus modos han cambiado ({m}).").format(r=red, m=datos.get("modos") or ""), interrupt=False)

    def _irc_unirse_en(self, acc: int, canal: str):
        ejecutar(lambda: irc_service.unirse_canal(acc, canal),
                 lambda r, e: self.avisar(_("No se pudo entrar: {e}").format(e=e) if e else _("Dentro de {c}").format(c=canal)))

    # ------------------------------------------------------------------ acciones
    def _registrar_acciones_irc(self, accion):
        def a(nombre, fn, teclas=()):
            accion(nombre, fn, teclas)
        a("irc-unirse", self.irc_unirse)
        a("irc-salir", self.irc_salir)
        a("irc-usuarios", self.irc_usuarios)
        a("irc-whois", self.irc_whois)
        a("irc-info-canal", self.irc_info_canal)
        a("irc-tema", self.irc_tema)
        a("irc-modos", self.irc_modos)
        a("irc-baneos", self.irc_baneos)
        a("irc-nivel-aviso", self.irc_nivel_aviso)
        a("irc-eventos", self.irc_alternar_eventos)
        a("irc-exportar", self.irc_exportar)
        a("irc-copiar", self.irc_copiar)
        a("irc-leido", self.irc_marcar_leido)
        a("irc-todo-leido", self.irc_marcar_todo_leido)
        a("irc-canales-servidor", self.irc_canales_servidor)
        a("irc-conectar", self.irc_conectar)
        a("irc-desconectar", self.irc_desconectar)
        a("irc-consola", self.irc_consola, ["<Ctrl><Alt>g"])
        a("irc-ausente", self.irc_ausente)
        a("irc-disponible", self.irc_disponible)
        a("irc-nick", self.irc_cambiar_nick)
        a("irc-whowas", self.irc_whowas)
        a("irc-ignorados", self.irc_ignorados)
        a("irc-amigos", self.irc_amigos)
        a("irc-editar-red", self.irc_editar_red)
        a("irc-eliminar-red", self.irc_eliminar_red)
