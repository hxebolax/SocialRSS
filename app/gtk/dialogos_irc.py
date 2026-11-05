# -*- coding: utf-8 -*-
"""Diálogos de IRC para la interfaz GTK 4: alta de redes, escribir, usuarios, canales del servidor y gestión de canal."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.plataforma import mostrar_aviso_dialogo
from app.core.speech import notificar
from app.services.irc.irc_avanzado import MODOS_CANAL
from app.services.irc.irc_service import irc_service

from .dialogos import DialogoBase, DialogoElegir, DialogoTexto, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista

log = logging.getLogger(__name__)

SECRETO_SIN_CAMBIOS = "********"     # marca de «ya hay una contraseña guardada y no se toca»
MAX_CANALES_VISIBLES = 3000


def consultar_whois(account_id: int, nick: str):
    """WHOIS legible en segundo plano y cuadro con el resultado."""
    notificar(_("Consultando a {n}…").format(n=nick))

    def _fin(res, err):
        if err:
            notificar(_("No se pudo consultar: {e}").format(e=err))
        elif res:
            mostrar_aviso_dialogo(res, _("Datos de {n}").format(n=nick))
    ejecutar(lambda: irc_service.whois_legible(account_id, nick), _fin)


class DialogoRedIRC(DialogoBase):
    """Alta o edición de una red IRC (Libera.Chat, OFTC, un servidor propio…) con valores seguros por defecto."""

    def __init__(self, padre, account_id: int = None, al_terminar=None):
        super().__init__(padre, _("Editar red IRC") if account_id else _("Añadir red IRC"), _("_Guardar"))
        self.account_id, self.al_terminar = account_id, al_terminar
        self.set_resizable(True)
        self.set_default_size(560, 620)
        # Con tantos campos la rejilla va dentro de un panel con desplazamiento.
        caja = self.get_child()
        caja.remove(self.rejilla)
        caja.prepend(Gtk.ScrolledWindow(child=self.rejilla, vexpand=True, propagate_natural_height=False))

        self.nombre = self.campo(_("_Nombre de la red (por ejemplo Libera.Chat):"), Gtk.Entry(width_chars=34),
                                 _("Nombre con el que verás esta red en el árbol."))
        self.servidor = self.campo(_("_Servidor (por ejemplo irc.libera.chat):"), Gtk.Entry(width_chars=34),
                                   _("Dirección del servidor IRC."))
        self.puerto = self.campo(_("_Puerto:"), Gtk.Entry(text="6697", width_chars=8, input_purpose=Gtk.InputPurpose.DIGITS),
                                 _("6697 es el puerto seguro (TLS) habitual; 6667 el no cifrado."))
        self.tls = self.casilla(_("Usar conexión segura (_TLS)"), True)
        self.verificar = self.casilla(_("_Verificar el certificado del servidor (recomendado)"), True)
        self.apodo = self.campo(_("_Apodo:"), Gtk.Entry(width_chars=24), _("Tu apodo en esta red."))
        self.alternativos = self.campo(_("Apodos a_lternativos (separados por comas):"), Gtk.Entry(width_chars=34),
                                       _("Se usan en orden si tu apodo está ocupado."))
        self.usuario = self.campo(_("_Usuario (ident, opcional):"), Gtk.Entry(width_chars=24),
                                  _("Si se deja vacío se usa el apodo."))
        self.real = self.campo(_("Nombre _real (opcional):"), Gtk.Entry(width_chars=34),
                               _("Texto libre visible en el whois."))
        self.clave_servidor = self.campo(_("Contraseña del ser_vidor (opcional):"), Gtk.PasswordEntry(show_peek_icon=True),
                                         _("Solo para servidores o bouncers que piden PASS."))
        self.sasl = self.casilla(_("Iniciar sesión con SAS_L (recomendado si la red lo admite)"), False)
        self.sasl_usuario = self.campo(_("Usuario SASL (vacío = el apodo):"), Gtk.Entry(width_chars=24))
        self.sasl_clave = self.campo(_("Contraseña SASL:"), Gtk.PasswordEntry(show_peek_icon=True))
        self.nickserv = self.campo(_("Contraseña de Nick_Serv (para redes sin SASL):"),
                                   Gtk.PasswordEntry(show_peek_icon=True), _("Se envía IDENTIFY a NickServ al conectar."))
        self.canales = self.campo(_("_Canales al conectar (separados por comas):"), Gtk.Entry(width_chars=34),
                                  _("Por ejemplo #nvda, #accesibilidad."))
        self.auto = self.casilla(_("C_onectar automáticamente al abrir la aplicación"), True)
        self.secretos = {"server_password": self.clave_servidor, "sasl_password": self.sasl_clave,
                         "nickserv_password": self.nickserv}
        if account_id:
            self._cargar(account_id)
        self.set_focus(self.nombre)

    def casilla(self, etiqueta: str, valor: bool):
        c = Gtk.CheckButton(label=etiqueta, use_underline=True, active=valor)
        self.rejilla.attach(c, 1, self._fila, 1, 1)
        self._fila += 1
        return c

    def _cargar(self, account_id: int):
        cfg = irc_service.obtener_red(account_id) or {}
        self.nombre.set_text(cfg.get("name") or "")
        self.servidor.set_text(cfg.get("host") or "")
        self.puerto.set_text(str(cfg.get("port") or 6697))
        self.tls.set_active(bool(cfg.get("tls", True)))
        self.verificar.set_active(bool(cfg.get("verify_cert", True)))
        self.apodo.set_text(cfg.get("nick") or "")
        self.alternativos.set_text(cfg.get("alt_nicks") or "")
        self.usuario.set_text(cfg.get("username") or "")
        self.real.set_text(cfg.get("realname") or "")
        self.sasl.set_active(bool(cfg.get("use_sasl")))
        self.sasl_usuario.set_text(cfg.get("sasl_username") or "")
        self.canales.set_text(cfg.get("autojoin") or "")
        self.auto.set_active(bool(cfg.get("auto_connect", True)))
        for clave, campo in self.secretos.items():
            if cfg.get(f"{clave}_definida"):
                campo.set_text(SECRETO_SIN_CAMBIOS)

    def aceptar(self):
        host, nick = self.servidor.get_text().strip(), self.apodo.get_text().strip()
        if not host:
            notificar(_("Escribe la dirección del servidor"))
            self.servidor.grab_focus()
            return
        if not nick:
            notificar(_("Escribe tu apodo"))
            self.apodo.grab_focus()
            return
        try:
            puerto = int(self.puerto.get_text().strip() or "6697")
        except ValueError:
            notificar(_("El puerto debe ser un número"))
            self.puerto.grab_focus()
            return
        datos = {"name": self.nombre.get_text().strip() or host, "host": host, "port": puerto,
                 "tls": self.tls.get_active(), "verify_cert": self.verificar.get_active(), "nick": nick,
                 "alt_nicks": self.alternativos.get_text().strip(), "username": self.usuario.get_text().strip(),
                 "realname": self.real.get_text().strip(), "use_sasl": self.sasl.get_active(),
                 "sasl_username": self.sasl_usuario.get_text().strip(), "autojoin": self.canales.get_text().strip(),
                 "auto_connect": self.auto.get_active()}
        # Una contraseña solo se toca si cambió: el marcador es «sin cambios», vacía la borra.
        for clave, campo in self.secretos.items():
            valor = campo.get_text()
            if valor != SECRETO_SIN_CAMBIOS:
                datos[clave] = valor.strip()
        if self.account_id:
            irc_service.actualizar_red(self.account_id, datos)
            ident = self.account_id
        else:
            ident = irc_service.crear_red(datos)
        notificar(_("Red guardada"))
        self.close()
        if self.al_terminar:
            self.al_terminar(ident)


class DialogoEscribirIRC(DialogoBase):
    """Una línea para el chat: texto normal o un comando que empieza por barra. Intro la envía."""

    def __init__(self, padre, titulo: str, al_enviar, texto_inicial: str = ""):
        super().__init__(padre, titulo, _("_Enviar"))
        self.al_enviar = al_enviar
        self.entrada = self.campo(_("_Mensaje:"), Gtk.Entry(text=texto_inicial, activates_default=True, width_chars=60),
                                  _("Intro envía. Los comandos empiezan por barra; /help los enseña."))
        self.set_focus(self.entrada)
        self.entrada.set_position(-1)

    def aceptar(self):
        texto = self.entrada.get_text().strip()
        if not texto:
            return
        self.close()
        self.al_enviar(texto)


class DialogoMotivoIRC(DialogoBase):
    """Motivo opcional de una expulsión o un baneo."""

    def __init__(self, padre, titulo: str, al_aceptar):
        super().__init__(padre, titulo, _("_Aceptar"))
        self.al_aceptar = al_aceptar
        self.entrada = self.campo(_("_Motivo (opcional):"), Gtk.Entry(activates_default=True, width_chars=40))
        self.set_focus(self.entrada)

    def aceptar(self):
        motivo = self.entrada.get_text().strip()
        self.close()
        self.al_aceptar(motivo)


class DialogoAmigosIRC(DialogoBase):
    """Apodos de los que la red avisa al conectarse o desconectarse."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Amigos (avisos de conexión)"), _("_Guardar"))
        self.account_id = account_id
        self.entrada = self.campo(_("_Apodos separados por comas:"),
                                  Gtk.Entry(text=", ".join(irc_service.amigos(account_id)), activates_default=True,
                                            width_chars=50),
                                  _("La red te avisará cuando se conecten o se desconecten."))
        self.set_focus(self.entrada)

    def aceptar(self):
        nicks = [n.strip() for n in self.entrada.get_text().split(",") if n.strip()]
        irc_service.fijar_amigos(self.account_id, nicks)
        notificar(_("Lista de amigos guardada ({n})").format(n=len(nicks)))
        self.close()


class DialogoIgnoradosIRC(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Usuarios ignorados"), _("Lista de usuarios ignorados"))
        self.account_id = account_id
        self.nicks = []
        self.boton(_("_Dejar de ignorar"), self._quitar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        self.nicks = irc_service.ignorados(self.account_id)
        self.lista.poner(self.nicks)
        self.mostrar_estado(_("{n} ignorados").format(n=len(self.nicks)) if self.nicks
                            else _("No tienes usuarios ignorados en esta red"))

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.nicks):
            irc_service.ignorar(self.account_id, self.nicks[p], False)
            notificar(_("{n} ya no está ignorado").format(n=self.nicks[p]))
            self._refrescar()
            self.lista.enfocar()


class DialogoUsuariosIRC(DialogoLista):
    """Usuarios de un canal con su rango dicho en palabras. Intro abre un privado con el usuario."""

    def __init__(self, padre, account_id: int, canal: str, al_privado):
        super().__init__(padre, _("Usuarios de {c}").format(c=canal), _("Lista de usuarios del canal"),
                         al_activar=lambda pos: self._privado())
        self.account_id, self.canal, self.al_privado = account_id, canal, al_privado
        self.usuarios = []
        self.boton(_("_Datos (whois)"), self._whois)
        self.boton(_("_Privado"), self._privado)
        self.boton(_("_Ignorar o dejar de ignorar"), self._ignorar)
        self.boton_op = self.boton(_("Acciones de _operador…"), self._acciones_op)
        self.boton(_("_Actualizar"), self._refrescar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        self.usuarios = irc_service.usuarios_de_canal(self.account_id, self.canal)
        self._soy_op = irc_service.soy_operador(self.account_id, self.canal)
        self.boton_op.set_sensitive(self._soy_op)
        self.lista.poner([f"{n}, {r}" if r else n for n, r in self.usuarios])
        self.mostrar_estado(_("{n} usuarios").format(n=len(self.usuarios)) if self.usuarios
                            else _("No hay usuarios que mostrar. ¿Estás dentro del canal?"))
        self.lista.enfocar()

    def _nick(self):
        p = self.lista.posicion()
        return self.usuarios[p][0] if 0 <= p < len(self.usuarios) else None

    def _whois(self):
        nick = self._nick()
        if nick:
            consultar_whois(self.account_id, nick)

    def _privado(self):
        nick = self._nick()
        if nick:
            self.close()
            self.al_privado(nick)

    def _ignorar(self):
        nick = self._nick()
        if not nick:
            return
        ignorar = not irc_service.esta_ignorado(self.account_id, nick)
        irc_service.ignorar(self.account_id, nick, ignorar)
        notificar(_("{n} ignorado. No verás sus mensajes.").format(n=nick) if ignorar
                  else _("{n} ya no está ignorado").format(n=nick))

    def _acciones_op(self):
        nick = self._nick()
        if not nick:
            return
        acciones = [("op", _("Dar operador")), ("deop", _("Quitar operador")), ("voice", _("Dar voz")),
                    ("devoice", _("Quitar voz")), ("kick", _("Expulsar del canal…")),
                    ("bankick", _("Banear y expulsar…"))]

        def _elegida(i):
            clave = acciones[i][0]
            if clave in ("kick", "bankick"):
                etiqueta = _("expulsar") if clave == "kick" else _("banear y expulsar")
                confirmar_async(self, _("¿Seguro que quieres {a} a {n} de {c}?").format(a=etiqueta, n=nick, c=self.canal),
                                lambda: DialogoMotivoIRC(self, _("Motivo"), lambda m: self._hacer(clave, nick, m)).present(),
                                etiqueta_si=_("Continuar"))
            else:
                self._hacer(clave, nick, "")
        DialogoElegir(self, _("Acciones sobre {n}").format(n=nick), _("_Acción:"), [a[1] for a in acciones],
                      _elegida, aceptar=_("_Hacerlo")).present()

    def _hacer(self, accion: str, nick: str, motivo: str):
        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            notificar(_("Hecho"))
            self._refrescar()
        ejecutar(lambda: irc_service.accion_usuario_canal(self.account_id, self.canal, nick, accion, motivo), _fin)


class DialogoCanalesServidorIRC(DialogoLista):
    """Lista de canales del servidor (LIST) con filtro por nombre o tema. Intro entra en el canal."""

    def __init__(self, padre, account_id: int, al_unirse):
        super().__init__(padre, _("Canales del servidor"), _("Lista de canales del servidor"),
                         al_activar=lambda pos: self._unirse())
        self.account_id, self.al_unirse = account_id, al_unirse
        self.canales, self.visibles = [], []
        caja = self.get_child()
        fila = Gtk.Box(spacing=8)
        etiqueta = Gtk.Label(label=_("_Filtrar por nombre o tema:"), use_underline=True)
        self.filtro = Gtk.SearchEntry(hexpand=True)
        etiqueta.set_mnemonic_widget(self.filtro)
        self.filtro.update_property([Gtk.AccessibleProperty.LABEL], [_("Filtro de canales")])
        self.filtro.connect("search-changed", lambda *_a: self._aplicar_filtro())
        fila.append(etiqueta)
        fila.append(self.filtro)
        caja.insert_child_after(fila, None)
        self.boton(_("_Unirse al canal"), self._unirse)
        self.cerrar_boton()
        self.mostrar_estado(_("Pidiendo la lista de canales (puede tardar en redes grandes)…"))
        ejecutar(lambda: irc_service.canales_del_servidor(account_id), self._descargada)

    def _descargada(self, res, err):
        if err:
            self.mostrar_estado(_("No se pudo obtener la lista: {e}").format(e=err))
            return
        self.canales = res or []
        self._aplicar_filtro()
        self.filtro.grab_focus()

    def _aplicar_filtro(self):
        q = self.filtro.get_text().strip().lower()
        coinciden = [c for c in self.canales
                     if not q or q in (c.get("name") or "").lower() or q in (c.get("topic") or "").lower()]
        self.visibles = coinciden[:MAX_CANALES_VISIBLES]
        self.lista.poner([_("{c}, {u} usuarios. {t}").format(c=c.get("name") or "", u=c.get("users") or 0,
                                                             t=" ".join((c.get("topic") or "").split()))
                          for c in self.visibles])
        resumen = _("{n} canales").format(n=len(coinciden))
        if len(coinciden) > len(self.visibles):
            resumen += " " + _("(se muestran los {m} con más usuarios; escribe en el filtro para acotar)").format(
                m=len(self.visibles))
        self.mostrar_estado(resumen)

    def _unirse(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.visibles)):
            return
        canal = self.visibles[p].get("name")
        self.al_unirse(canal)


class DialogoModosCanalIRC(DialogoBase):
    """Modos de un canal: casillas para los habituales, clave y límite de usuarios. El servidor valida que seas operador."""

    def __init__(self, padre, account_id: int, canal: str):
        super().__init__(padre, _("Modos del canal {c}").format(c=canal), _("_Guardar"))
        self.account_id, self.canal = account_id, canal
        self.estado = irc_service.modos_actuales_canal(account_id, canal)
        letras = self.estado.get("letras") or ""
        self.casillas = {}
        for letra, etiqueta in MODOS_CANAL:
            c = Gtk.CheckButton(label=etiqueta, active=letra in letras)
            self.rejilla.attach(c, 0, self._fila, 2, 1)
            self._fila += 1
            self.casillas[letra] = c
        self.clave = self.campo(_("_Clave del canal (vacío = sin clave):"), Gtk.Entry(text=self.estado.get("clave") or ""))
        self.limite = self.campo(_("_Límite de usuarios (0 = sin límite):"),
                                 Gtk.Entry(text=str(self.estado.get("limite") or 0), input_purpose=Gtk.InputPurpose.DIGITS))
        self.set_focus(next(iter(self.casillas.values())))

    def aceptar(self):
        antes = set(self.estado.get("letras") or "")
        cambios = []
        for letra, c in self.casillas.items():
            if c.get_active() and letra not in antes:
                cambios.append(f"+{letra}")
            elif not c.get_active() and letra in antes:
                cambios.append(f"-{letra}")
        clave = self.clave.get_text().strip()
        try:
            limite = int(self.limite.get_text().strip() or 0)
        except ValueError:
            limite = 0
        acc, canal, est = self.account_id, self.canal, self.estado
        self.boton_aceptar.set_sensitive(False)

        def _trabajo():
            for modo in cambios:
                irc_service.fijar_modo_canal(acc, canal, modo, "")
            if clave != (est.get("clave") or ""):
                irc_service.fijar_clave_canal(acc, canal, clave)
            if limite != int(est.get("limite") or 0):
                irc_service.fijar_limite_canal(acc, canal, limite)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo (¿eres operador del canal?): {e}").format(e=err))
                return
            notificar(_("Modos aplicados"))
            self.close()
        ejecutar(_trabajo, _fin)


class DialogoBaneosIRC(DialogoLista):
    """Baneos de un canal: ver, añadir por máscara y quitar."""

    def __init__(self, padre, account_id: int, canal: str):
        super().__init__(padre, _("Baneos de {c}").format(c=canal), _("Lista de baneos"))
        self.account_id, self.canal = account_id, canal
        self.baneos = []
        self.boton(_("_Añadir baneo…"), self._anadir)
        self.boton(_("_Quitar baneo"), self._quitar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.baneos = res or []
            self.lista.poner([_("{m}, puesto por {p}").format(m=b.get("mascara") or "", p=b.get("por") or _("desconocido"))
                              for b in self.baneos])
            self.mostrar_estado(_("{n} baneos").format(n=len(self.baneos)) if self.baneos else _("No hay baneos"))
            self.lista.enfocar()
        ejecutar(lambda: irc_service.lista_baneos(self.account_id, self.canal), _fin)

    def _tras(self, res, err):
        if err:
            notificar(_("No se pudo (¿eres operador del canal?): {e}").format(e=err))
        else:
            notificar(_("Hecho"))
        self._refrescar()

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.baneos):
            mascara = self.baneos[p].get("mascara") or ""
            ejecutar(lambda: irc_service.quitar_baneo(self.account_id, self.canal, mascara), self._tras)

    def _anadir(self):
        def _poner(mascara):
            ejecutar(lambda: irc_service.banear_mascara(self.account_id, self.canal, mascara), self._tras)
        DialogoTexto(self, _("Añadir baneo"), _("_Máscara (por ejemplo *!*@host.com):"), _poner,
                     aceptar=_("_Banear")).present()
