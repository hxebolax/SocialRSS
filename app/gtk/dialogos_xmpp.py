# -*- coding: utf-8 -*-
"""Diálogos de XMPP para la interfaz GTK 4: alta de cuenta, contactos, salas, mensajes, estado y huellas OMEMO."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.plataforma import mostrar_aviso_dialogo
from app.core.speech import notificar

from .dialogos import DialogoBase, DialogoElegir, DialogoTexto, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

LIMITE_MENSAJE = 8000


def _servicio():
    from app.services.xmpp.xmpp_service import xmpp_service
    return xmpp_service


class DialogoCuentaXMPP(DialogoBase):
    """Alta de una cuenta XMPP (Jabber): JID y contraseña. El servidor y el puerto se descubren solos; TLS es obligatorio."""

    def __init__(self, padre, al_terminar):
        super().__init__(padre, _("Añadir cuenta de XMPP (Jabber)"), _("Iniciar _sesión"))
        self.al_terminar = al_terminar
        self.jid = self.campo(_("_JID (usuario@servidor):"), Gtk.Entry(activates_default=True, width_chars=40),
                              _("Tu dirección de Jabber completa, por ejemplo nombre@jabber.org."))
        self.clave = self.campo(_("_Contraseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True),
                                _("Se guarda cifrada en este equipo; XMPP la usa en cada conexión."))
        self.recurso = self.campo(_("_Recurso (identifica este dispositivo):"),
                                  Gtk.Entry(text="SocialRSS", activates_default=True, width_chars=24))
        self.sin_cert = Gtk.CheckButton(label=_("_No verificar el certificado del servidor (menos seguro)"),
                                        use_underline=True)
        self.sin_cert.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                      [_("Solo para servidores con certificado caducado o autofirmado en los que confías. "
                                         "La conexión sigue cifrada.")])
        self.rejilla.attach(self.sin_cert, 1, self._fila, 1, 1)
        self.set_focus(self.jid)

    def aceptar(self):
        jid, clave, recurso = self.jid.get_text().strip(), self.clave.get_text(), self.recurso.get_text().strip()
        if "@" not in jid or not clave:
            notificar(_("Escribe el JID completo (usuario@servidor) y la contraseña"))
            return
        sin_cert = self.sin_cert.get_active()
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Verificando la cuenta de XMPP…"))

        def _crear():
            xmpp = _servicio()
            ident = xmpp.crear_cuenta(jid, clave, recurso or "SocialRSS", no_verificar_cert=sin_cert)
            xmpp.guardar_password(ident, clave)
            return ident

        def _fin(ident, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo añadir la cuenta: {e}").format(e=err))
                return
            notificar(_("Cuenta de XMPP añadida"))
            self.close()
            self.al_terminar(ident)
        ejecutar(_crear, _fin)


class DialogoContactoXMPP(DialogoBase):
    """Añadir un contacto al roster: se le pide suscripción de presencia."""

    def __init__(self, padre, account_id: int, al_terminar=None):
        super().__init__(padre, _("Agregar contacto de XMPP"), _("_Agregar"))
        self.account_id, self.al_terminar = account_id, al_terminar
        self.jid = self.campo(_("_JID del contacto (usuario@servidor):"), Gtk.Entry(activates_default=True, width_chars=40))
        self.nombre = self.campo(_("_Nombre (opcional):"), Gtk.Entry(activates_default=True, width_chars=30))
        self.set_focus(self.jid)

    def aceptar(self):
        jid, nombre = self.jid.get_text().strip(), self.nombre.get_text().strip()
        if "@" not in jid:
            notificar(_("Escribe un JID válido (usuario@servidor)"))
            return
        self.close()

        def _fin(res, err):
            notificar(_("No se pudo: {e}").format(e=err) if err
                      else _("Contacto agregado; espera a que acepte tu solicitud"))
            if not err and self.al_terminar:
                self.al_terminar()
        ejecutar(lambda: _servicio().agregar_contacto(self.account_id, jid, nombre), _fin)


class DialogoUnirseSalaXMPP(DialogoBase):
    """Entrar en una sala (MUC): JID de la sala, apodo y contraseña si la pide."""

    def __init__(self, padre, account_id: int, al_terminar=None, sala: str = ""):
        super().__init__(padre, _("Unirse a una sala de XMPP"), _("_Entrar"))
        self.account_id, self.al_terminar = account_id, al_terminar
        self.sala = self.campo(_("_Sala (sala@conference.servidor):"), Gtk.Entry(text=sala, activates_default=True, width_chars=40))
        self.nick = self.campo(_("_Apodo (vacío = tu usuario):"), Gtk.Entry(activates_default=True, width_chars=24))
        self.clave = self.campo(_("_Contraseña de la sala (si la pide):"), Gtk.PasswordEntry(activates_default=True))
        self.guardar = Gtk.CheckButton(label=_("_Guardar la sala y entrar automáticamente al conectar"),
                                       use_underline=True, active=True)
        self.rejilla.attach(self.guardar, 1, self._fila, 1, 1)
        self.set_focus(self.sala)

    def aceptar(self):
        sala = self.sala.get_text().strip()
        if "@" not in sala:
            notificar(_("Escribe el JID de la sala (sala@conference.servidor)"))
            return
        nick, clave, guardar = self.nick.get_text().strip(), self.clave.get_text(), self.guardar.get_active()
        self.close()
        notificar(_("Entrando en {s}…").format(s=sala))

        def _fin(res, err):
            notificar(_("No se pudo entrar: {e}").format(e=err) if err else _("Dentro de la sala"))
            if not err and self.al_terminar:
                self.al_terminar(sala)
        ejecutar(lambda: _servicio().unirse_sala(self.account_id, sala, nick, clave, guardar=guardar), _fin)


class DialogoDirectorioSalasXMPP(DialogoLista):
    """Salas públicas del servicio de salas del servidor, con filtro; Intro entra en la seleccionada."""

    def __init__(self, padre, account_id: int, al_unirse):
        super().__init__(padre, _("Directorio de salas de XMPP"), _("Lista de salas"),
                         al_activar=lambda pos: self._unirse())
        self.account_id, self.al_unirse = account_id, al_unirse
        self.salas, self.visibles = [], []
        caja = self.get_child()
        fila = Gtk.Box(spacing=8)
        etiqueta = Gtk.Label(label=_("_Filtrar por nombre:"), use_underline=True)
        self.filtro = Gtk.SearchEntry(hexpand=True)
        etiqueta.set_mnemonic_widget(self.filtro)
        self.filtro.update_property([Gtk.AccessibleProperty.LABEL], [_("Filtro de salas")])
        self.filtro.connect("search-changed", lambda *_a: self._aplicar_filtro())
        fila.append(etiqueta)
        fila.append(self.filtro)
        caja.insert_child_after(fila, None)
        self.boton(_("_Entrar en la sala"), self._unirse)
        self.cerrar_boton()
        self.mostrar_estado(_("Consultando el servidor…"))
        ejecutar(lambda: _servicio().directorio_salas(account_id), self._descargado)

    def _descargado(self, res, err):
        if err:
            self.mostrar_estado(_("No se pudo obtener el directorio: {e}").format(e=err))
            return
        self.salas = res or []
        self._aplicar_filtro()
        self.filtro.grab_focus()

    def _aplicar_filtro(self):
        q = self.filtro.get_text().strip().lower()
        self.visibles = [s for s in self.salas if not q or q in s["nombre"].lower() or q in s["jid"].lower()]
        self.lista.poner([_("{n} ({j})").format(n=s["nombre"], j=s["jid"]) for s in self.visibles])
        self.mostrar_estado(_("{n} salas").format(n=len(self.visibles)) if self.visibles else _("No hay salas que mostrar"))

    def _unirse(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.visibles):
            self.close()
            self.al_unirse(self.visibles[p]["jid"])


class DialogoOcupantesXMPP(DialogoLista):
    """Ocupantes de una sala con su rol y afiliación en palabras, y las acciones de moderación."""

    ACCIONES = [("dar_voz", "Dar voz"), ("quitar_voz", "Quitar voz"), ("dar_moderador", "Hacer moderador/a"),
                ("quitar_moderador", "Quitar moderador/a"), ("hacer_miembro", "Hacer miembro"),
                ("hacer_admin", "Hacer administrador/a"), ("expulsar", "Expulsar de la sala…"),
                ("banear", "Banear…"), ("desbanear", "Quitar el baneo")]

    def __init__(self, padre, account_id: int, sala: str, nombre: str):
        super().__init__(padre, _("Ocupantes de {s}").format(s=nombre), _("Lista de ocupantes"))
        self.account_id, self.sala = account_id, sala
        self.ocupantes = []
        self.boton(_("_Invitar a alguien…"), self._invitar)
        self.boton_mod = self.boton(_("Acciones de _moderación…"), self._acciones)
        self.boton(_("_Actualizar"), self._refrescar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        xmpp = _servicio()
        self.ocupantes = xmpp.ocupantes(self.account_id, self.sala)
        self.boton_mod.set_sensitive(xmpp.mi_rol_en(self.account_id, self.sala) == "moderator")
        textos = []
        for o in self.ocupantes:
            t = f"{o['nick']}, {o['rol']}"
            if o["afi_clave"] not in ("none", ""):
                t += f", {o['afiliacion']}"
            textos.append(t)
        self.lista.poner(textos)
        self.mostrar_estado(_("{n} ocupantes").format(n=len(self.ocupantes)) if self.ocupantes
                            else _("No hay ocupantes que mostrar. ¿Estás dentro de la sala?"))
        self.lista.enfocar()

    def _nick(self):
        p = self.lista.posicion()
        return self.ocupantes[p]["nick"] if 0 <= p < len(self.ocupantes) else None

    def _invitar(self):
        def _enviar(jid):
            ejecutar(lambda: _servicio().invitar_sala(self.account_id, self.sala, jid),
                     lambda r, e: notificar(_("No se pudo invitar: {e}").format(e=e) if e else _("Invitación enviada")))
        DialogoTexto(self, _("Invitar a la sala"), _("_JID de quien invitas:"), _enviar, aceptar=_("_Invitar")).present()

    def _acciones(self):
        nick = self._nick()
        if not nick:
            return

        def _elegida(i):
            clave = self.ACCIONES[i][0]
            if clave in ("expulsar", "banear"):
                from .dialogos_irc import DialogoMotivoIRC
                confirmar_async(self, _("¿Seguro que quieres hacerlo con {n}?").format(n=nick),
                                lambda: DialogoMotivoIRC(self, _("Motivo"), lambda m: self._hacer(clave, nick, m)).present(),
                                etiqueta_si=_("Continuar"))
            else:
                self._hacer(clave, nick, "")
        DialogoElegir(self, _("Acciones sobre {n}").format(n=nick), _("_Acción:"), [_(a[1]) for a in self.ACCIONES],
                      _elegida, aceptar=_("_Hacerlo")).present()

    def _hacer(self, accion: str, nick: str, motivo: str):
        def _fin(res, err):
            if err:
                notificar(_("No se pudo (¿eres moderador/a?): {e}").format(e=err))
                return
            notificar(_("Hecho"))
            self._refrescar()
        ejecutar(lambda: _servicio().accion_ocupante(self.account_id, self.sala, nick, accion, motivo), _fin)


class DialogoMensajeXMPP(DialogoBase):
    """Escribir un mensaje: nuevo, respuesta, corrección de tu último mensaje o tema de una sala. Ctrl+Intro lo envía."""

    def __init__(self, padre, titulo: str, al_enviar, texto_inicial: str = "", cita: str = "", aceptar: str = None,
                 permitir_vacio: bool = False):
        super().__init__(padre, titulo, aceptar or _("_Enviar"))
        self.al_enviar = al_enviar
        self.set_resizable(True)
        self.set_default_size(580, -1)
        if cita:
            # En una sola columna: una etiqueta con ajuste de línea que abarca dos columnas de la rejilla desquicia el cálculo de altura.
            self.rejilla.attach(Gtk.Label(label=_("Respondiendo a:"), xalign=0, yalign=0), 0, self._fila, 1, 1)
            self.rejilla.attach(Gtk.Label(label=cita, xalign=0, wrap=True, selectable=True, hexpand=True,
                                          width_chars=40, max_width_chars=70), 1, self._fila, 1, 1)
            self._fila += 1
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        instalar_ayudas(self.texto)
        self.texto.get_buffer().set_text(texto_inicial)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=110, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Mensaje:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.rejilla.attach(Gtk.Label(label=_("Ctrl+Intro envía el mensaje"), xalign=0), 1, self._fila, 1, 1)
        self.vacio_permitido = permitir_vacio
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl>Return"),
                                          Gtk.CallbackAction.new(lambda *_a: self.aceptar() or True)))
        self.add_controller(ctl)
        self.set_focus(self.texto)

    def aceptar(self):
        b = self.texto.get_buffer()
        texto = b.get_text(b.get_start_iter(), b.get_end_iter(), False).strip()
        if not texto and not self.vacio_permitido:
            notificar(_("Escribe el mensaje"))
            return
        if len(texto) > LIMITE_MENSAJE:
            notificar(_("El mensaje es demasiado largo"))
            return
        self.close()
        self.al_enviar(texto)


class DialogoPresenciaXMPP(DialogoBase):
    """Tu presencia: disponible, ausente, ocupado… con un mensaje opcional."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mi presencia"), _("_Guardar"))
        from app.services.xmpp.xmpp_common import presencias_legibles
        self.account_id = account_id
        self.opciones = presencias_legibles()
        self.combo = self.campo(_("_Estado:"), Gtk.DropDown.new_from_strings([o[1] for o in self.opciones]))
        self.mensaje = self.campo(_("_Mensaje (opcional):"), Gtk.Entry(activates_default=True, width_chars=40),
                                  _("Una frase corta que verán tus contactos."))
        self.set_focus(self.combo)

    def aceptar(self):
        show, mensaje = self.opciones[self.combo.get_selected()][0], self.mensaje.get_text().strip()
        self.close()
        ejecutar(lambda: _servicio().fijar_presencia(self.account_id, show, mensaje),
                 lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e else _("Presencia actualizada")))


class DialogoAnimoXMPP(DialogoBase):
    """Publicar tu estado de ánimo (XEP-0107) o tu actividad (XEP-0108)."""

    def __init__(self, padre, account_id: int, tipo: str):
        from app.services.xmpp.xmpp_perfil import ACTIVIDADES, MOODS
        titulo = _("Mi estado de ánimo") if tipo == "mood" else _("Mi actividad")
        super().__init__(padre, titulo, _("_Publicar"))
        self.account_id, self.tipo = account_id, tipo
        self.opciones = MOODS if tipo == "mood" else ACTIVIDADES
        self.combo = self.campo(_("¿Cómo te sientes?") if tipo == "mood" else _("¿Qué estás haciendo?"),
                                Gtk.DropDown.new_from_strings([_(o[1]) for o in self.opciones]))
        self.texto = self.campo(_("_Texto opcional:"), Gtk.Entry(activates_default=True, width_chars=36))
        quitar = Gtk.Button(label=_("_Quitar mi estado"), use_underline=True)
        quitar.connect("clicked", lambda *_a: self._quitar())
        self.rejilla.attach(quitar, 1, self._fila, 1, 1)
        self.set_focus(self.combo)

    def _hecho(self, mensaje: str):
        return lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e else mensaje)

    def aceptar(self):
        clave, texto = self.opciones[self.combo.get_selected()][0], self.texto.get_text().strip()
        self.close()
        xmpp = _servicio()
        ejecutar(lambda: xmpp.publicar_animo(self.account_id, clave, texto) if self.tipo == "mood"
                 else xmpp.publicar_actividad(self.account_id, clave, texto), self._hecho(_("Estado publicado")))

    def _quitar(self):
        self.close()
        xmpp = _servicio()
        ejecutar(lambda: xmpp.limpiar_animo(self.account_id) if self.tipo == "mood"
                 else xmpp.limpiar_actividad(self.account_id), self._hecho(_("Estado retirado")))


class DialogoHuellasXMPP(DialogoLista):
    """Huellas OMEMO: la tuya y las de los dispositivos del contacto, con su confianza. Se puede verificar o desconfiar."""

    def __init__(self, padre, account_id: int, jid: str, nombre: str):
        super().__init__(padre, _("Huellas de cifrado OMEMO — {n}").format(n=nombre), _("Lista de dispositivos y huellas"))
        self.account_id, self.jid = account_id, jid
        self.huellas = []
        self.boton(_("_Verificar (confiar)"), lambda: self._confiar(True))
        self.boton(_("_Desconfiar"), lambda: self._confiar(False))
        self.boton(_("Mi _huella"), self._mi_huella)
        self.cerrar_boton()
        self.mostrar_estado(_("Consultando los dispositivos…"))
        ejecutar(lambda: _servicio().huellas_de(account_id, jid), self._cargadas)

    def _cargadas(self, res, err):
        if err:
            self.mostrar_estado(_("No se pudo consultar: {e}").format(e=err))
            return
        self.huellas = res or []
        self.lista.poner([_("Dispositivo {d}, confianza {c}. Huella {h}").format(d=h["device_id"], c=h["confianza"],
                                                                                h=h["huella"]) for h in self.huellas])
        self.mostrar_estado(_("{n} dispositivos").format(n=len(self.huellas)) if self.huellas
                            else _("Este contacto no ha publicado claves OMEMO"))
        self.lista.enfocar()

    def _confiar(self, confiar: bool):
        p = self.lista.posicion()
        if not (0 <= p < len(self.huellas)):
            return
        h = self.huellas[p]

        def _fin(ok, err):
            if err or not ok:
                notificar(_("No se pudo cambiar la confianza"))
                return
            notificar(_("Dispositivo verificado") if confiar else _("Dispositivo marcado como no fiable"))
            ejecutar(lambda: _servicio().huellas_de(self.account_id, self.jid), self._cargadas)
        ejecutar(lambda: _servicio().confiar_dispositivo(self.account_id, h["_jid"], h["_ik"], confiar), _fin)

    def _mi_huella(self):
        def _fin(h, err):
            if err or not h:
                notificar(_("No se pudo obtener tu huella"))
                return
            mostrar_aviso_dialogo(_("Dispositivo {d}\nHuella: {h}\nConfianza: {c}").format(
                d=h["device_id"], h=h["huella"], c=h["confianza"]), _("Mi huella OMEMO"))
        ejecutar(lambda: _servicio().mi_huella(self.account_id), _fin)
