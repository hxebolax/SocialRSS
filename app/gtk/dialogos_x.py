# -*- coding: utf-8 -*-
"""Diálogos de X (Twitter): alta de cuenta, publicar y perfil."""

import logging
import os
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar
from app.core.task_manager import task_manager
from app.services.x import x_service

from .dialogos import DialogoBase
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

EXT_IMAGEN = (".png", ".jpg", ".jpeg", ".gif", ".webp")
EXT_VIDEO = (".mp4", ".mov")
NAVEGADORES = [("auto", "Detectar automáticamente"), ("firefox", "Firefox"), ("chrome", "Chrome"), ("edge", "Edge"),
               ("brave", "Brave"), ("opera", "Opera")]

def aviso_riesgos() -> str:
    return _(
        "AVISO IMPORTANTE sobre el cliente de X (Twitter)\n\n"
        "Este cliente NO es oficial: usa la API interna de X mediante las cookies de tu "
        "sesión. Debes saber que:\n\n"
        "• Incumple las condiciones de uso de X y existe RIESGO de que X limite, bloquee o "
        "suspenda tu cuenta.\n"
        "• X cambia su web a menudo, así que el cliente puede dejar de funcionar hasta que se "
        "publique una corrección.\n"
        "• Se recomienda usar una cuenta secundaria de pruebas.\n"
        "• Tus cookies se guardan CIFRADAS en este equipo; nunca se piden ni guardan tu "
        "contraseña salvo que elijas el método de usuario y contraseña.\n\n"
        "¿Entiendes los riesgos y quieres continuar?")


class DialogoCuentaX(DialogoBase):
    """Alta de una cuenta de X, o con `account_id` reimportar las cookies de una que ya existe.

    Tres métodos: leer las cookies del navegador (el recomendado, sin contraseña), pegarlas a mano (auth_token y ct0)
    o usuario y contraseña. Pulsar el botón principal significa haber leído el aviso de riesgos."""

    def __init__(self, padre, al_terminar, account_id: int = None):
        super().__init__(padre, _("Reimportar las cookies de X") if account_id else _("Añadir cuenta de X"),
                         _("_Entiendo los riesgos y continúo"))
        self.al_terminar, self.account_id = al_terminar, account_id
        self.set_default_size(620, -1)
        aviso = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD, left_margin=8,
                             right_margin=8, top_margin=6, bottom_margin=6)
        aviso.get_buffer().set_text(aviso_riesgos())
        aviso.update_property([Gtk.AccessibleProperty.LABEL], [_("Aviso de riesgos")])
        self.rejilla.attach(Gtk.ScrolledWindow(child=aviso, min_content_height=190, hexpand=True, has_frame=True),
                            0, self._fila, 2, 1)
        self._fila += 1
        self.metodo = self.campo(_("_Método de inicio de sesión:"), Gtk.DropDown.new_from_strings([
            _("Importar las cookies de mi navegador (recomendado, sin contraseña)"),
            _("Pegar las cookies a mano (auth_token y ct0)"),
            _("Usuario y contraseña (menos robusto)")]))
        self._filas_metodo = []     # [(método, [widgets de la fila])]
        self.navegador = self._fila_metodo(0, _("_Navegador con la sesión de X abierta:"), Gtk.DropDown.new_from_strings(
            [_(n) if c == "auto" else n for c, n in NAVEGADORES]), _("Firefox es el que mejor funciona en Linux."))
        self.auth = self._fila_metodo(1, _("_auth_token:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True),
                                      _("En el navegador: herramientas de desarrollo, almacenamiento, cookies de x.com."))
        self.ct0 = self._fila_metodo(1, _("_ct0:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        self.usuario = self._fila_metodo(2, _("_Usuario, correo o teléfono:"), Gtk.Entry(activates_default=True,
                                                                                         width_chars=40))
        self.clave = self._fila_metodo(2, _("_Contraseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        self.totp = self._fila_metodo(2, _("Clave _TOTP de 2FA (opcional):"), Gtk.PasswordEntry(activates_default=True,
                                                                                               show_peek_icon=True))
        self.metodo.connect("notify::selected", lambda *_a: self._mostrar_metodo())
        self._mostrar_metodo()
        self.set_focus(self.metodo)

    def _fila_metodo(self, metodo: int, etiqueta: str, widget, ayuda: str = ""):
        fila = self._fila
        self.campo(etiqueta, widget, ayuda)
        self._filas_metodo.append((metodo, [self.rejilla.get_child_at(0, fila), widget]))
        return widget

    def _mostrar_metodo(self):
        elegido = self.metodo.get_selected()
        for m, widgets in self._filas_metodo:
            for w in widgets:
                w.set_visible(m == elegido)

    def aceptar(self):
        elegido = self.metodo.get_selected()
        if elegido == 0:
            navegador = NAVEGADORES[self.navegador.get_selected()][0]
            datos = navegador
        elif elegido == 1:
            auth, ct0 = self.auth.get_text().strip(), self.ct0.get_text().strip()
            if not auth or not ct0:
                notificar(_("Debes pegar auth_token y ct0"))
                return
            datos = {"auth_token": auth, "ct0": ct0}
        else:
            usuario, clave = self.usuario.get_text().strip(), self.clave.get_text()
            if not usuario or not clave:
                notificar(_("Debes indicar usuario y contraseña"))
                return
            datos = (usuario, clave, self.totp.get_text().strip() or None)
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Iniciando sesión en X…"))
        acc = self.account_id

        def _bg(ctx):
            if acc:
                if elegido == 0:
                    return x_service.reimportar_desde_navegador(acc, datos)
                if elegido == 1:
                    return x_service.reimportar_cookies(acc, datos)
                return x_service.reimportar_cookies(acc, x_service.obtener_cookies_login(*datos))
            if elegido == 0:
                return x_service.login_con_cookies(x_service.cookies_desde_navegador(datos))
            if elegido == 1:
                return x_service.login_con_cookies(datos)
            return x_service.login_programatico(*datos)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo iniciar sesión en X: {e}").format(e=err))
                return
            notificar((_("Cookies de X reimportadas: @{u}") if acc else _("Cuenta de X añadida: @{u}")).format(
                u=res.get("screen_name", "")))
            self.close()
            self.al_terminar(res.get("account_id") or acc)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class DialogoPublicarX(DialogoBase):
    """Redactar una publicación (nueva, respuesta o cita), con adjuntos descritos, encuesta o programada."""

    def __init__(self, padre, account_id: int, texto_inicial: str = "", responde_a: str = None,
                 cita_id: str = None, titulo: str = None):
        super().__init__(padre, titulo or (_("Responder en X") if responde_a else _("Citar en X") if cita_id
                                           else _("Nueva publicación en X")), _("_Publicar"))
        self.account_id, self.responde_a, self.cita_id = account_id, responde_a, cita_id
        self.adjuntos = []       # [(ruta, descripción)]
        self._terminado = False
        self.set_default_size(620, -1)
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        instalar_ayudas(self.texto)
        self.buffer = self.texto.get_buffer()
        self.buffer.set_text(texto_inicial)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=130, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Texto:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.contador = Gtk.Label(xalign=0)
        self.rejilla.attach(self.contador, 1, self._fila, 1, 1)
        self._fila += 1
        self.buffer.connect("changed", lambda *_a: self._actualizar_contador())
        self.lista_adj = Gtk.Label(xalign=0, wrap=True, label=_("Sin adjuntos"))
        self.campo(_("Adjuntos:"), self.lista_adj)
        self.encuesta = self.campo(_("_Encuesta, una opción por línea (de 2 a 4):"), self._caja_encuesta(),
                                   _("Déjalo vacío si no quieres encuesta. No se puede combinar con adjuntos."))
        self.duracion = self.campo(_("_Duración de la encuesta en minutos:"),
                                   Gtk.SpinButton.new_with_range(5, 10080, 5))
        self.duracion.set_value(1440)
        self.programar = self.campo(_("Pro_gramar para (AAAA-MM-DD HH:MM, opcional):"),
                                    Gtk.Entry(activates_default=True, width_chars=20))
        fila = Gtk.Box(spacing=8)
        for et, fn in ((_("Añadir _adjunto…"), self._anadir_adjunto), (_("_Quitar el último adjunto"), self._quitar_adjunto),
                       (_("_Mejorar la redacción con IA"), lambda: self._ia("mejorar")),
                       (_("Sugerir _hashtags con IA"), lambda: self._ia("hashtags"))):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, f=fn: f())
            fila.append(b)
        self.rejilla.attach(fila, 1, self._fila, 1, 1)
        self._fila += 1
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl>Return"),
                                          Gtk.CallbackAction.new(lambda *_a: self.aceptar() or True)))
        self.add_controller(ctl)
        self.connect("close-request", self._al_cerrar)
        self._actualizar_contador()
        self.set_focus(self.texto)
        self.buffer.place_cursor(self.buffer.get_end_iter())

    def _caja_encuesta(self):
        self.vista_encuesta = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, left_margin=6,
                                           right_margin=6, top_margin=4, bottom_margin=4)
        return Gtk.ScrolledWindow(child=self.vista_encuesta, min_content_height=70, has_frame=True)

    def _texto(self) -> str:
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def _opciones_encuesta(self):
        b = self.vista_encuesta.get_buffer()
        crudo = b.get_text(b.get_start_iter(), b.get_end_iter(), False)
        return [l.strip() for l in crudo.splitlines() if l.strip()][:4]

    def _actualizar_contador(self):
        self.contador.set_text(_("Caracteres: {n} (280 en cuentas normales; las Premium admiten más)").format(
            n=len(self._texto())))

    def _al_cerrar(self, *_a):
        if self._terminado or not (self._texto().strip() or self.adjuntos):
            return False
        dlg = Gtk.AlertDialog()
        dlg.set_message(_("Hay una publicación sin enviar"))
        dlg.set_detail(_("¿Quieres descartarla?"))
        dlg.set_buttons([_("Seguir editando"), _("Descartar")])
        dlg.set_default_button(0)
        dlg.set_cancel_button(0)

        def _fin(origen, res):
            try:
                if origen.choose_finish(res) == 1:
                    self._terminado = True
                    self.close()
            except Exception:
                pass
        dlg.choose(self, None, _fin)
        return True

    def _ia(self, que: str):
        from .acciones_ia import transformar_texto

        def _aplicar(resultado):
            if not resultado:
                notificar(_("La IA no devolvió nada"))
                return
            b = self.buffer
            b.begin_user_action()
            if que == "mejorar":
                b.delete(b.get_start_iter(), b.get_end_iter())
                b.insert(b.get_end_iter(), resultado)
                notificar(_("Redacción mejorada. Ctrl+Z deshace el cambio."))
            else:
                b.insert(b.get_end_iter(), ("\n" if self._texto() and not self._texto().endswith("\n") else "") + resultado)
                notificar(_("Hashtags añadidos al final: {h}").format(h=resultado))
            b.end_user_action()
        transformar_texto(self._texto(), que, _aplicar)

    # --- adjuntos ---
    def _anadir_adjunto(self):
        if len(self.adjuntos) >= 4:
            notificar(_("Máximo 4 adjuntos"))
            return
        dlg = Gtk.FileDialog(title=_("Añadir adjunto"))

        def _fin(origen, res):
            try:
                f = origen.open_finish(res)
            except Exception:
                return
            ruta = f.get_path()
            if not ruta.lower().endswith(EXT_IMAGEN + EXT_VIDEO):
                notificar(_("Formato no admitido: usa imágenes (png, jpg, gif, webp) o un vídeo (mp4, mov)"))
                return
            self._pedir_descripcion(ruta)
        dlg.open(self, None, _fin)

    def _pedir_descripcion(self, ruta: str):
        d = DialogoBase(self, _("Descripción del adjunto"), _("_Aceptar"))
        entrada = d.campo(_("_Descripción para quien no lo ve:"), Gtk.Entry(activates_default=True, width_chars=50),
                          _("Describe la imagen o el vídeo. Se recomienda siempre."))
        if ruta.lower().endswith(EXT_IMAGEN):
            ia = Gtk.Button(label=_("_Generar con IA"), use_underline=True)

            def _generar():
                from .acciones_ia import texto_alternativo
                texto_alternativo(ruta, lambda t: (entrada.set_text(t), entrada.grab_focus()))
            ia.connect("clicked", lambda *_a: _generar())
            d.rejilla.attach(ia, 1, d._fila, 1, 1)
            d._fila += 1

        def _guardar():
            self.adjuntos.append((ruta, entrada.get_text().strip()))
            self._pintar_adjuntos()
            d.close()
        d.aceptar = _guardar
        d.set_focus(entrada)
        d.present()

    def _quitar_adjunto(self):
        if self.adjuntos:
            self.adjuntos.pop()
            self._pintar_adjuntos()

    def _pintar_adjuntos(self):
        if not self.adjuntos:
            self.lista_adj.set_text(_("Sin adjuntos"))
            return
        self.lista_adj.set_text("; ".join(f"{os.path.basename(r)} ({d or _('sin descripción')})" for r, d in self.adjuntos))
        notificar(_("{n} adjuntos").format(n=len(self.adjuntos)))

    # --- publicar ---
    def aceptar(self):
        texto = self._texto().strip()
        opciones = self._opciones_encuesta()
        if not texto and not self.adjuntos and not opciones:
            notificar(_("Escribe algo, adjunta un archivo o crea una encuesta"))
            return
        if opciones and len(opciones) < 2:
            notificar(_("La encuesta necesita al menos 2 opciones"))
            return
        if opciones and self.adjuntos:
            notificar(_("Una encuesta no se puede combinar con adjuntos"))
            return
        cuando = None
        if self.programar.get_text().strip():
            try:
                cuando = datetime.strptime(self.programar.get_text().strip(), "%Y-%m-%d %H:%M")
            except ValueError:
                notificar(_("Formato de fecha no válido. Usa AAAA-MM-DD HH:MM"))
                return
            if cuando <= datetime.now():
                notificar(_("La fecha programada ya ha pasado"))
                return
            if self.adjuntos or opciones or self.responde_a or self.cita_id:
                notificar(_("Una publicación programada solo puede llevar texto"))
                return
        if self.adjuntos and not cuando and config.get("x", "alt_text_reminder", True) \
                and any(not d for _r, d in self.adjuntos) and not getattr(self, "_aviso_alt", False):
            self._aviso_alt = True
            notificar(_("Hay adjuntos sin descripción. Pulsa Publicar de nuevo para enviarlos así."))
            return
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Programando…") if cuando else _("Publicando…"))

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo publicar: {e}").format(e=err))
                return
            notificar(_("Publicación programada") if cuando else _("Publicado en X"))
            self._terminado = True
            self.close()
            self.padre.tw_tras_publicar()
        if cuando:
            x_service.programar_tweet_async(self.account_id, texto, cuando, callback=_fin)
            return
        x_service.publicar_async(
            self.account_id, texto, media_paths=[r for r, _d in self.adjuntos] or None,
            reply_to=self.responde_a, quote_id=self.cita_id, alt_texts=[d for _r, d in self.adjuntos] or None,
            poll_opciones=opciones or None, poll_minutos=int(self.duracion.get_value()) if opciones else 0,
            callback=_fin)


class DialogoPerfilX(Gtk.Window):
    """Ficha de una cuenta con acciones: seguir, silenciar, bloquear, ver sus publicaciones y escribirle."""

    def __init__(self, padre, account_id: int, screen: str):
        super().__init__(title=_("Perfil de @{u}").format(u=screen.lstrip("@")), transient_for=padre, modal=True)
        self.padre, self.account_id, self.screen = padre, account_id, screen.lstrip("@")
        self.datos = {}
        self.set_default_size(560, 480)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)
        self.texto = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD,
                                  left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        self.texto.update_property([Gtk.AccessibleProperty.LABEL], [_("Datos del perfil")])
        self.texto.get_buffer().set_text(_("Cargando el perfil…"))
        caja.append(Gtk.ScrolledWindow(child=self.texto, vexpand=True, has_frame=True))
        self.botones = []
        for acciones in ((("_Seguir", "follow"), ("Dejar de s_eguir", "unfollow"), ("Sile_nciar", "mute"),
                          ("_Quitar el silencio", "unmute"), ("_Bloquear", "block"), ("_Desbloquear", "unblock")),
                         (("Ver _publicaciones", "ver"), ("Enviar _mensaje", "dm"),
                          ("Abrir en el _navegador", "web"), ("_Cerrar", "cerrar"))):
            fila = Gtk.Box(spacing=8, halign=Gtk.Align.END)
            for et, clave in acciones:
                b = Gtk.Button(label=_(et), use_underline=True, sensitive=clave == "cerrar")
                b.connect("clicked", lambda *_a, c=clave: self._pulsado(c))
                fila.append(b)
                self.botones.append(b)
            caja.append(fila)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.set_focus(self.texto)
        x_service.obtener_perfil_async(account_id, self.screen, callback=self._pintar)

    def _pintar(self, d, err):
        if err or not d:
            self.texto.get_buffer().set_text(_("No se pudo cargar el perfil: {e}").format(e=err or ""))
            return
        self.datos = d
        lineas = [f"{d.get('name') or self.screen} (@{d.get('screen_name') or self.screen})"]
        if d.get("verified"):
            lineas.append(_("Cuenta verificada"))
        lineas.append(_("{p} publicaciones · {s} seguidores · {g} siguiendo").format(
            p=d.get("statuses", 0), s=d.get("followers", 0), g=d.get("following", 0)))
        if d.get("description"):
            lineas += ["", d["description"]]
        lineas += ["", f"https://x.com/{d.get('screen_name') or self.screen}"]
        self.texto.get_buffer().set_text("\n".join(lineas))
        for b in self.botones:
            b.set_sensitive(True)

    def _pulsado(self, clave: str):
        uid = self.datos.get("user_id")
        if clave == "cerrar":
            return self.close()
        if clave == "web":
            from app.core.privacy import abrir_url
            return abrir_url(f"https://x.com/{self.datos.get('screen_name') or self.screen}")
        if clave == "ver":
            self.close()
            return self.padre.tw_abrir_cronologia(self.account_id, uid, self.datos.get("screen_name") or self.screen,
                                                  self.datos.get("name", ""))
        if clave == "dm":
            self.close()
            return self.padre.tw_mensaje_a(self.account_id, uid, self.datos.get("screen_name") or self.screen)
        if not uid:
            return
        textos = {"follow": _("Ahora sigues a @{u}"), "unfollow": _("Has dejado de seguir a @{u}"),
                  "mute": _("@{u} silenciada"), "unmute": _("Silencio quitado a @{u}"),
                  "block": _("@{u} bloqueada"), "unblock": _("@{u} desbloqueada")}

        def _fin(res, err):
            notificar(_("No se pudo: {e}").format(e=err) if err else textos[clave].format(u=self.screen))
        x_service.accion_cuenta_async(self.account_id, uid, clave, callback=_fin)


class DialogoMensajeX(DialogoBase):
    """Escribir un mensaje directo. Ctrl+Intro lo envía."""

    def __init__(self, padre, account_id: int, user_id: str, titulo: str, al_enviar=None):
        super().__init__(padre, _("Mensaje a {t}").format(t=titulo), _("_Enviar"))
        self.account_id, self.user_id, self.al_enviar = account_id, str(user_id), al_enviar
        self.set_default_size(560, -1)
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        instalar_ayudas(self.texto)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=110, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Mensaje:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl>Return"),
                                          Gtk.CallbackAction.new(lambda *_a: self.aceptar() or True)))
        self.add_controller(ctl)
        self.set_focus(self.texto)

    def aceptar(self):
        b = self.texto.get_buffer()
        texto = b.get_text(b.get_start_iter(), b.get_end_iter(), False).strip()
        if not texto:
            notificar(_("Escribe el mensaje"))
            return
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo enviar: {e}").format(e=err))
                return
            notificar(_("Mensaje enviado"))
            self.close()
            if self.al_enviar:
                self.al_enviar()
        x_service.enviar_dm_async(self.account_id, self.user_id, texto, callback=_fin)
