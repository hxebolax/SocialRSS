# -*- coding: utf-8 -*-
"""Diálogos de Bluesky: alta de cuenta, publicar y perfil."""

import logging
import os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.core.task_manager import task_manager
from app.services.bluesky.bluesky_service import bluesky_service

from .dialogos import DialogoBase, confirmar_async
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

LIMITE_BSKY = 300
EXT_IMAGEN = (".png", ".jpg", ".jpeg", ".gif", ".webp")
EXT_VIDEO = (".mp4", ".mov", ".webm", ".mpeg", ".mpg")
CONTROL_RESPUESTAS = [("everybody", "Todos"), ("following", "Cuentas que sigo"),
                      ("mentioned", "Solo las mencionadas"), ("nobody", "Nadie")]


class DialogoCuentaBluesky(DialogoBase):
    """Alta con el usuario y una CONTRASEÑA DE APLICACIÓN (Ajustes de Bluesky, no la contraseña normal)."""

    def __init__(self, padre, al_terminar):
        super().__init__(padre, _("Añadir cuenta de Bluesky"), _("_Iniciar sesión"))
        self.al_terminar = al_terminar
        self.usuario = self.campo(_("_Usuario (por ejemplo ana.bsky.social):"),
                                  Gtk.Entry(activates_default=True, width_chars=40))
        self.clave = self.campo(_("_Contraseña de aplicación:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True),
                                _("Créala en Ajustes, Contraseñas de aplicación, en bsky.app. No uses tu contraseña normal."))
        self.pds = self.campo(_("_Servidor (opcional):"), Gtk.Entry(text="https://bsky.social", activates_default=True),
                              _("Solo cámbialo si tu cuenta está en otro servidor (PDS)."))
        self.set_focus(self.usuario)

    def aceptar(self):
        usuario, clave = self.usuario.get_text().strip(), self.clave.get_text()
        if not usuario or not clave:
            notificar(_("Escribe el usuario y la contraseña de aplicación"))
            return
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Iniciando sesión…"))

        def _bg(ctx):
            return bluesky_service.completar_login(usuario, clave, self.pds.get_text().strip() or "https://bsky.social")

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo iniciar sesión: {e}").format(e=err))
                return
            notificar(_("Cuenta añadida: @{h}").format(h=res["handle"]))
            self.close()
            self.al_terminar(res["account_id"])
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class DialogoPublicarBsky(DialogoBase):
    """Redactar una publicación (nueva, respuesta o cita). Ctrl+Intro publica."""

    def __init__(self, padre, account_id: int, texto_inicial: str = "", responde_a: str = None,
                 cita_uri: str = None, titulo: str = None):
        super().__init__(padre, titulo or (_("Responder") if responde_a else _("Citar") if cita_uri
                                           else _("Nueva publicación")), _("_Publicar"))
        self.account_id, self.responde_a, self.cita_uri = account_id, responde_a, cita_uri
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
        self.idioma = self.campo(_("_Idioma (código, opcional):"), Gtk.Entry(max_length=5, width_chars=6))
        self.control = self.campo(_("¿Quién puede _responder?"), Gtk.DropDown.new_from_strings(
            [_(n) for _c, n in CONTROL_RESPUESTAS]))
        self.lista_adj = Gtk.Label(xalign=0, wrap=True, label=_("Sin adjuntos"))
        self.campo(_("Adjuntos:"), self.lista_adj)
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

    def _texto(self) -> str:
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def _actualizar_contador(self):
        self.contador.set_text(_("Caracteres restantes: {n}").format(n=LIMITE_BSKY - len(self._texto())))

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
        if self.adjuntos and self.adjuntos[0][0].lower().endswith(EXT_VIDEO):
            notificar(_("Un vídeo no se puede combinar con otros adjuntos"))
            return
        if len(self.adjuntos) >= 4:
            notificar(_("Bluesky admite hasta cuatro imágenes"))
            return
        dlg = Gtk.FileDialog(title=_("Añadir adjunto"))

        def _fin(origen, res):
            try:
                f = origen.open_finish(res)
            except Exception:
                return
            ruta = f.get_path()
            if ruta.lower().endswith(EXT_VIDEO) and self.adjuntos:
                notificar(_("Un vídeo no se puede combinar con otros adjuntos"))
                return
            if not ruta.lower().endswith(EXT_IMAGEN + EXT_VIDEO):
                notificar(_("Formato no admitido: usa imágenes (png, jpg, gif, webp) o un vídeo (mp4, mov, webm)"))
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
        if not texto and not self.adjuntos:
            notificar(_("Escribe algo o añade un adjunto"))
            return
        if len(texto) > LIMITE_BSKY:
            notificar(_("El texto supera el límite de {n} caracteres").format(n=LIMITE_BSKY))
            return
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Publicando…"))
        es_video = bool(self.adjuntos) and self.adjuntos[0][0].lower().endswith(EXT_VIDEO)

        def _fin(ok, detalle):
            if not ok:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo publicar: {e}").format(e=detalle))
                return
            notificar(_("Publicado"))
            self._terminado = True
            self.close()
            self.padre.tras_publicar()
        idioma = self.idioma.get_text().strip()
        bluesky_service.publicar_post_async(
            self.account_id, texto, reply_to_uri=self.responde_a, quote_uri=self.cita_uri,
            media_paths=[r for r, _d in self.adjuntos] or None, alt_texts=[d for _r, d in self.adjuntos] or None,
            es_video=es_video, langs=[idioma] if idioma else None,
            reply_control=CONTROL_RESPUESTAS[self.control.get_selected()][0], callback=_fin)


class DialogoPerfilBsky(Gtk.Window):
    """Ficha de una cuenta con acciones: seguir, silenciar, bloquear y ver sus publicaciones."""

    def __init__(self, padre, account_id: int, actor: str):
        super().__init__(title=_("Perfil de {u}").format(u=actor), transient_for=padre, modal=True)
        self.padre, self.account_id, self.actor = padre, account_id, actor
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
        fila = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.b_seguir = Gtk.Button(label=_("_Seguir"), use_underline=True, sensitive=False)
        self.b_silenciar = Gtk.Button(label=_("Sile_nciar"), use_underline=True, sensitive=False)
        self.b_bloquear = Gtk.Button(label=_("_Bloquear"), use_underline=True, sensitive=False)
        b_pub = Gtk.Button(label=_("Ver _publicaciones"), use_underline=True)
        cerrar = Gtk.Button(label=_("_Cerrar"), use_underline=True)
        b_dm = Gtk.Button(label=_("Enviar _mensaje"), use_underline=True)
        b_seg = Gtk.Button(label=_("Se_guidores"), use_underline=True)
        b_sig = Gtk.Button(label=_("S_iguiendo"), use_underline=True)
        b_rep = Gtk.Button(label=_("_Denunciar"), use_underline=True)
        b_dm.connect("clicked", lambda *_a: (self.close(), padre.bsky_mensaje_nuevo(account_id, self.datos.get("did") or actor)))
        b_seg.connect("clicked", lambda *_a: self._cuentas("followers"))
        b_sig.connect("clicked", lambda *_a: self._cuentas("follows"))
        b_rep.connect("clicked", lambda *_a: self._denunciar())
        self.b_seguir.connect("clicked", lambda *_a: self._accion("unfollow" if self.datos.get("following_uri") else "follow"))
        self.b_silenciar.connect("clicked", lambda *_a: self._accion("unmute" if self.datos.get("muted") else "mute"))
        self.b_bloquear.connect("clicked", lambda *_a: self._bloqueo())
        b_pub.connect("clicked", lambda *_a: (self.close(), padre.bsky_abrir_cronologia(account_id, self.datos.get("did") or actor,
                                                                                        self.datos.get("handle") or actor)))
        cerrar.connect("clicked", lambda *_a: self.close())
        for b in (self.b_seguir, self.b_silenciar, self.b_bloquear, b_dm, b_seg, b_sig, b_rep, b_pub, cerrar):
            fila.append(b)
        caja.append(fila)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.set_focus(self.texto)
        self._cargar()

    def _cuentas(self, tipo: str):
        from .dialogos_bluesky_extra import DialogoActoresBsky
        DialogoActoresBsky(self.get_transient_for(), self.account_id, tipo, actor=self.datos.get("did") or self.actor).present()

    def _denunciar(self):
        from .dialogos_bluesky_extra import DialogoReportarBsky
        DialogoReportarBsky(self, self.account_id, did=self.datos.get("did"), quien="@" + self.datos.get("handle", "")).present()

    def _cargar(self):
        bluesky_service.obtener_perfil_async(self.account_id, self.actor, callback=self._pintar)

    def _pintar(self, d, err):
        if err or not d:
            self.texto.get_buffer().set_text(_("No se pudo cargar el perfil: {e}").format(e=err))
            return
        self.datos = d
        etiquetas = []
        if d["following_uri"]:
            etiquetas.append(_("le sigues"))
        if d["muted"]:
            etiquetas.append(_("silenciada"))
        if d["blocking_uri"]:
            etiquetas.append(_("bloqueada"))
        lineas = [f"{d['display_name'] or d['handle']} (@{d['handle']})"]
        if etiquetas:
            lineas.append(", ".join(etiquetas).capitalize())
        lineas.append(_("{p} publicaciones · {s} seguidores · {g} siguiendo").format(
            p=d["posts"], s=d["followers"], g=d["following"]))
        if d["description"]:
            lineas += ["", d["description"]]
        lineas += ["", f"https://bsky.app/profile/{d['handle']}"]
        self.texto.get_buffer().set_text("\n".join(lineas))
        for b in (self.b_seguir, self.b_silenciar, self.b_bloquear):
            b.set_sensitive(True)
        self.b_seguir.set_label(_("Dejar de _seguir") if d["following_uri"] else _("_Seguir"))
        self.b_silenciar.set_label(_("Quitar sile_nciar") if d["muted"] else _("Sile_nciar"))
        self.b_bloquear.set_label(_("Desb_loquear") if d["blocking_uri"] else _("_Bloquear"))

    def _bloqueo(self):
        if self.datos.get("blocking_uri"):
            self._accion("unblock")
        else:
            confirmar_async(self, _("¿Bloquear a @{u}?").format(u=self.datos.get("handle", self.actor)),
                            lambda: self._accion("block"), etiqueta_si=_("Bloquear"))

    def _accion(self, accion: str):
        textos = {"follow": _("Siguiendo a @{u}"), "unfollow": _("Has dejado de seguir a @{u}"),
                  "mute": _("Silenciada @{u}"), "unmute": _("Silencio quitado a @{u}"),
                  "block": _("Bloqueada @{u}"), "unblock": _("Desbloqueada @{u}")}

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            notificar(textos[accion].format(u=self.datos.get("handle", self.actor)))
            self._cargar()
        bluesky_service.accion_cuenta_async(
            self.account_id, self.datos["did"], accion, following_uri=self.datos.get("following_uri", ""),
            blocking_uri=self.datos.get("blocking_uri", ""), callback=_fin)
