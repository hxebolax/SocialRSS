# -*- coding: utf-8 -*-
"""Diálogos de Reddit: alta de cuenta (OAuth por navegador), publicar, mensajes privados, perfil y visor del hilo."""

import logging
import os
import webbrowser
from collections import defaultdict
from datetime import datetime, timezone

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.database import db
from app.core.plantillas import definicion, pulir_lectura
from app.core.plataforma import llamar_en_ui
from app.core.speech import notificar, speak, copiar_al_portapapeles
from app.services.reddit import reddit_service

from . import datos_reddit
from .datos import fecha_legible
from .dialogos import DialogoBase, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista, ListaSimple
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

PREFS_APPS_URL = "https://www.reddit.com/prefs/apps"
REDIRECT = "http://localhost:8080"
TIPOS = [("texto", "Texto"), ("enlace", "Enlace"), ("imagen", "Imagen"), ("video", "Vídeo"),
         ("galeria", "Galería de imágenes"), ("encuesta", "Encuesta")]
EXT_IMAGEN = (".png", ".jpg", ".jpeg", ".gif", ".webp")
EXT_VIDEO = (".mp4", ".mov")


def _texto_de(tv: Gtk.TextView) -> str:
    b = tv.get_buffer()
    return b.get_text(b.get_start_iter(), b.get_end_iter(), False)


def _vista_texto(editable: bool = True) -> Gtk.TextView:
    vista = Gtk.TextView(accepts_tab=False, editable=editable, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6,
                         right_margin=6, top_margin=6, bottom_margin=6)
    if editable:
        instalar_ayudas(vista)
    return vista


def _marco(tv: Gtk.TextView, alto: int) -> Gtk.ScrolledWindow:
    return Gtk.ScrolledWindow(child=tv, min_content_height=alto, hexpand=True, has_frame=True)


def _campo_texto(dlg: DialogoBase, etiqueta: str, alto: int = 140, texto: str = ""):
    """Fila 'etiqueta: cuadro de texto de varias líneas' en la rejilla del diálogo; devuelve (vista, [etiqueta, marco])."""
    tv = _vista_texto()
    tv.get_buffer().set_text(texto)
    marco = _marco(tv, alto)
    lbl = Gtk.Label(label=etiqueta, use_underline=True, xalign=0, yalign=0)
    lbl.set_mnemonic_widget(tv)
    dlg.rejilla.attach(lbl, 0, dlg._fila, 1, 1)
    dlg.rejilla.attach(marco, 1, dlg._fila, 1, 1)
    dlg._fila += 1
    return tv, [lbl, marco]


# ---------------------------------------------------------------------------------------------- alta de cuenta
class DialogoCuentaReddit(DialogoBase):
    """Alta (o nueva autorización) de una cuenta: el identificador de aplicación de reddit.com/prefs/apps y el navegador."""

    def __init__(self, padre, al_terminar, account_id: int = None):
        super().__init__(padre, _("Volver a autorizar la cuenta de Reddit") if account_id else _("Añadir cuenta de Reddit"),
                         _("_Abrir el navegador y autorizar"))
        self.al_terminar, self.account_id = al_terminar, account_id
        self.set_default_size(620, -1)
        ayuda = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD, left_margin=8,
                             right_margin=8, top_margin=6, bottom_margin=6)
        ayuda.get_buffer().set_text(_(
            "Reddit no deja usar una aplicación sin que la registres tú.\n\n"
            "1. Entra en {url} con tu cuenta y pulsa «Crear otra aplicación».\n"
            "2. Ponle un nombre cualquiera, elige el tipo «installed app» y como dirección de redirección escribe {red}\n"
            "3. Copia el identificador que aparece bajo el nombre de la aplicación (una cadena corta de letras y números) "
            "y pégalo aquí.\n\n"
            "Al continuar se abre el navegador para que inicies sesión y apruebes el acceso. SocialRSS espera la "
            "respuesta en el puerto 8080 de este equipo; si otro programa lo usa, ciérralo antes.").format(
            url=PREFS_APPS_URL, red=REDIRECT))
        ayuda.update_property([Gtk.AccessibleProperty.LABEL], [_("Cómo conseguir el identificador de aplicación")])
        self.rejilla.attach(Gtk.ScrolledWindow(child=ayuda, min_content_height=200, hexpand=True, has_frame=True),
                            0, self._fila, 2, 1)
        self._fila += 1
        self.client_id = self.campo(_("_Identificador de la aplicación (client id):"),
                                    Gtk.Entry(text=config.get("reddit", "client_id", "") or "", activates_default=True,
                                              width_chars=40))
        boton = Gtk.Button(label=_("Abrir {u} en el _navegador").format(u="reddit.com/prefs/apps"), use_underline=True,
                           halign=Gtk.Align.START)
        boton.connect("clicked", lambda *_a: webbrowser.open(PREFS_APPS_URL))
        self.rejilla.attach(boton, 1, self._fila, 1, 1)
        self._fila += 1
        self.estado = Gtk.Label(xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.STATUS)
        self.rejilla.attach(self.estado, 0, self._fila, 2, 1)
        self.set_focus(self.client_id)

    def _progreso(self, texto: str):
        def _ui():
            self.estado.set_text(texto)
            speak(texto, interrupt=False)
        llamar_en_ui(_ui)

    def aceptar(self):
        cid = self.client_id.get_text().strip()
        if not cid:
            notificar(_("Escribe el identificador de la aplicación"))
            return
        config.set("reddit", "client_id", cid)
        self.boton_aceptar.set_sensitive(False)
        self.estado.set_text(_("Abriendo el navegador…"))

        def _hacer():
            if self.account_id:
                return reddit_service.reautenticar(self.account_id, cid, self._progreso)
            return reddit_service.iniciar_alta_oauth(cid, self._progreso)

        def _fin(res, err):
            self.boton_aceptar.set_sensitive(True)
            if err:
                self.estado.set_text(str(err))
                notificar(_("No se pudo añadir la cuenta: {e}").format(e=err))
                return
            self.close()
            nueva = (res or {}).get("account_id") or self.account_id
            notificar(_("Cuenta de Reddit lista: u/{u}").format(u=(res or {}).get("username", "")))
            self.al_terminar(nueva)
        ejecutar(_hacer, _fin)


# ---------------------------------------------------------------------------------------------- textos y mensajes
class DialogoTextoLibre(DialogoBase):
    """Un texto de varias líneas (una respuesta, una edición...); lo entrega a `al_aceptar(texto)`."""

    def __init__(self, padre, titulo: str, etiqueta: str, al_aceptar, texto: str = "", aceptar: str = None):
        super().__init__(padre, titulo, aceptar)
        self.al_aceptar = al_aceptar
        self.set_default_size(620, -1)
        self.texto, _w = _campo_texto(self, etiqueta, 200, texto)
        self.set_focus(self.texto)

    def aceptar(self):
        t = _texto_de(self.texto).strip()
        if not t:
            notificar(_("Escribe algo primero"))
            return
        self.close()
        self.al_aceptar(t)


class DialogoMensajeReddit(DialogoBase):
    """Mensaje privado a un usuario."""

    def __init__(self, padre, account_id: int, destinatario: str = ""):
        super().__init__(padre, _("Enviar mensaje privado"), _("_Enviar"))
        self.account_id = account_id
        self.set_default_size(620, -1)
        self.dest = self.campo(_("_Destinatario (usuario, sin u/):"), Gtk.Entry(text=destinatario, activates_default=True,
                                                                              width_chars=30))
        self.asunto = self.campo(_("A_sunto:"), Gtk.Entry(activates_default=True, max_length=100, width_chars=40))
        self.cuerpo, _w = _campo_texto(self, _("_Mensaje:"), 160)
        self.set_focus(self.cuerpo if destinatario else self.dest)

    def aceptar(self):
        dest = self.dest.get_text().strip().removeprefix("/").removeprefix("u/")
        cuerpo = _texto_de(self.cuerpo).strip()
        if not dest or not cuerpo:
            notificar(_("El destinatario y el mensaje son obligatorios"))
            return
        asunto = self.asunto.get_text().strip()

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar: {e}").format(e=err))
                return
            self.close()
            notificar(_("Mensaje enviado a u/{0}").format(dest))
        self.boton_aceptar.set_sensitive(False)
        ejecutar(lambda: reddit_service.enviar_pm(self.account_id, dest, asunto, cuerpo),
                 lambda r, e: (self.boton_aceptar.set_sensitive(True), _fin(r, e)))


# ---------------------------------------------------------------------------------------------- publicar
class DialogoPublicarReddit(DialogoBase):
    """Publica en un subreddit: texto, enlace, imagen, vídeo, galería o encuesta. Con `crosspost` (fullname y título),
    comparte una publicación existente en otro subreddit."""

    def __init__(self, padre, account_id: int, subreddit: str = "", crosspost: tuple = None):
        super().__init__(padre, _("Crosspost") if crosspost else _("Publicar en Reddit"),
                         _("_Publicar"))
        self.account_id, self.crosspost = account_id, crosspost
        self._flairs, self._galeria, self._archivo = [], [], ""
        self.set_default_size(660, -1)
        self.sub = self.campo(_("_Comunidad (subreddit, sin r/):"), Gtk.Entry(text=subreddit, activates_default=True,
                                                                             width_chars=30))
        self.titulo = self.campo(_("_Título:"), Gtk.Entry(text=crosspost[1] if crosspost else "", activates_default=True,
                                                         max_length=300, width_chars=50))
        self._por_tipo = {t: [] for t, _n in TIPOS}
        self.tipo = None
        if not crosspost:
            self.tipo = self.campo(_("Tipo de _publicación:"), Gtk.DropDown.new_from_strings([_(n) for _t, n in TIPOS]))
            self.tipo.connect("notify::selected", lambda *_a: self._mostrar_tipo())
            self.cuerpo, w = _campo_texto(self, _("Te_xto:"), 130)
            self._cuerpo_widgets = w
            self.url = self.campo(_("_Dirección (URL):"), Gtk.Entry(activates_default=True, width_chars=50))
            self._por_tipo["enlace"] += self._fila_widgets(self._fila - 1)
            self.etiqueta_archivo = Gtk.Label(xalign=0, label=_("Ningún archivo elegido"), wrap=True)
            self.boton_archivo = Gtk.Button(label=_("_Elegir archivo…"), use_underline=True, halign=Gtk.Align.START)
            self.boton_archivo.connect("clicked", lambda *_a: self._elegir_archivo())
            caja = Gtk.Box(spacing=8)
            caja.append(self.boton_archivo)
            caja.append(self.etiqueta_archivo)
            self.rejilla.attach(caja, 1, self._fila, 1, 1)
            self._por_tipo["imagen"].append(caja)
            self._por_tipo["video"].append(caja)
            self._fila += 1
            self.lista_galeria = ListaSimple(_("Imágenes de la galería"))
            self.rejilla.attach(self.lista_galeria, 1, self._fila, 1, 1)
            self._fila += 1
            botones = Gtk.Box(spacing=8)
            b_add = Gtk.Button(label=_("_Añadir imágenes…"), use_underline=True)
            b_add.connect("clicked", lambda *_a: self._anadir_galeria())
            b_del = Gtk.Button(label=_("_Quitar la imagen"), use_underline=True)
            b_del.connect("clicked", lambda *_a: self._quitar_galeria())
            botones.append(b_add)
            botones.append(b_del)
            self.rejilla.attach(botones, 1, self._fila, 1, 1)
            self._fila += 1
            self._por_tipo["galeria"] += [self.lista_galeria, botones]
            self.opciones, w = _campo_texto(self, _("_Opciones de la encuesta (una por línea, de 2 a 6):"), 90)
            self._por_tipo["encuesta"] += w
            self.duracion = self.campo(_("Du_ración de la encuesta (días):"), Gtk.SpinButton.new_with_range(1, 7, 1))
            self.duracion.set_value(3)
            self._por_tipo["encuesta"] += self._fila_widgets(self._fila - 1)
        self.nsfw = self.campo(_("Marcar como _+18:"), Gtk.Switch(halign=Gtk.Align.START))
        self.spoiler = self.campo(_("Marcar como s_poiler:"), Gtk.Switch(halign=Gtk.Align.START))
        self.respuestas = self.campo(_("Recibir las _respuestas en la bandeja de entrada:"),
                                     Gtk.Switch(active=True, halign=Gtk.Align.START))
        self.flair = self.campo(_("Etiqueta (_flair):"), Gtk.DropDown.new_from_strings([_("(sin etiqueta)")]))
        b_flair = Gtk.Button(label=_("Cargar las etiquetas del su_breddit"), use_underline=True, halign=Gtk.Align.START)
        b_flair.connect("clicked", lambda *_a: self._cargar_flairs())
        self.rejilla.attach(b_flair, 1, self._fila, 1, 1)
        self._fila += 1
        if self.tipo is not None:
            self._mostrar_tipo()
        self.set_focus(self.titulo if subreddit else self.sub)

    def _fila_widgets(self, fila: int):
        return [self.rejilla.get_child_at(0, fila), self.rejilla.get_child_at(1, fila)]

    def _tipo_actual(self) -> str:
        return TIPOS[self.tipo.get_selected()][0] if self.tipo is not None else "crosspost"

    def _mostrar_tipo(self):
        actual = self._tipo_actual()
        visibles = {id(w) for w in self._por_tipo.get(actual, [])}
        for widgets in self._por_tipo.values():
            for w in widgets:
                w.set_visible(id(w) in visibles)
        for w in self._cuerpo_widgets:
            w.set_visible(actual in ("texto", "encuesta"))
        self._cuerpo_widgets[0].set_text_with_mnemonic(_("Te_xto:") if actual == "texto" else _("Te_xto (opcional):"))

    # ------------------------------------------------------------------ archivos
    def _elegir_archivo(self):
        tipo = self._tipo_actual()
        dlg = Gtk.FileDialog(title=_("Elegir un vídeo") if tipo == "video" else _("Elegir una imagen"))

        def _fin(origen, res):
            try:
                f = origen.open_finish(res)
            except Exception:
                return
            self._archivo = f.get_path()
            self.etiqueta_archivo.set_text(os.path.basename(self._archivo))
        dlg.open(self, None, _fin)

    def _anadir_galeria(self):
        dlg = Gtk.FileDialog(title=_("Añadir imágenes"))

        def _fin(origen, res):
            try:
                archivos = origen.open_multiple_finish(res)
            except Exception:
                return
            for i in range(archivos.get_n_items()):
                self._galeria.append(archivos.get_item(i).get_path())
            self.lista_galeria.poner([os.path.basename(p) for p in self._galeria])
            speak(_("{n} imágenes en la galería").format(n=len(self._galeria)), interrupt=False)
        dlg.open_multiple(self, None, _fin)

    def _quitar_galeria(self):
        p = self.lista_galeria.posicion()
        if 0 <= p < len(self._galeria):
            del self._galeria[p]
            self.lista_galeria.poner([os.path.basename(x) for x in self._galeria])

    # ------------------------------------------------------------------ etiquetas
    def _cargar_flairs(self):
        sub = self.sub.get_text().strip().removeprefix("r/")
        if not sub:
            notificar(_("Escribe primero la comunidad (subreddit)"))
            return
        speak(_("Cargando etiquetas…"), interrupt=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar las etiquetas: {e}").format(e=err))
                return
            self._flairs = res or []
            self.flair.set_model(Gtk.StringList.new([_("(sin etiqueta)")] + [f.get("text") or _("(sin texto)")
                                                                              for f in self._flairs]))
            self.flair.set_selected(0)
            speak(_("{n} etiquetas disponibles").format(n=len(self._flairs)), interrupt=True)
        ejecutar(lambda: reddit_service.flairs_de_subreddit(self.account_id, sub), _fin)

    # ------------------------------------------------------------------ publicar
    def aceptar(self):
        sub = self.sub.get_text().strip().removeprefix("/").removeprefix("r/")
        titulo = self.titulo.get_text().strip()
        if not sub or not titulo:
            notificar(_("La comunidad y el título son obligatorios"))
            return
        i = self.flair.get_selected()
        fl = self._flairs[i - 1] if 0 < i <= len(self._flairs) else {}
        comun = dict(flair_id=fl.get("id"), flair_text=fl.get("text"), nsfw=self.nsfw.get_active(),
                     spoiler=self.spoiler.get_active(), send_replies=self.respuestas.get_active())
        tipo = self._tipo_actual()
        if tipo == "crosspost":
            def hacer():
                return reddit_service.crosspost(self.account_id, sub, self.crosspost[0], titulo, **comun)
        elif tipo == "texto":
            def hacer():
                return reddit_service.publicar_texto(self.account_id, sub, titulo, _texto_de(self.cuerpo), **comun)
        elif tipo == "enlace":
            url = self.url.get_text().strip()
            if not url:
                notificar(_("Escribe la dirección (URL) del enlace"))
                return

            def hacer():
                return reddit_service.publicar_enlace(self.account_id, sub, titulo, url, **comun)
        elif tipo in ("imagen", "video"):
            if not self._archivo:
                notificar(_("Elige un archivo para publicar"))
                return
            ruta = self._archivo

            def hacer():
                f = reddit_service.publicar_imagen if tipo == "imagen" else reddit_service.publicar_video
                return f(self.account_id, sub, titulo, ruta, **comun)
        elif tipo == "galeria":
            if len(self._galeria) < 2:
                notificar(_("Añade al menos 2 imágenes a la galería"))
                return
            imagenes = list(self._galeria)

            def hacer():
                return reddit_service.publicar_galeria(self.account_id, sub, titulo, imagenes, **comun)
        else:
            opciones = [x.strip() for x in _texto_de(self.opciones).splitlines() if x.strip()]
            if not 2 <= len(opciones) <= 6:
                notificar(_("La encuesta necesita entre 2 y 6 opciones"))
                return
            dias, cuerpo = int(self.duracion.get_value()), _texto_de(self.cuerpo)

            def hacer():
                return reddit_service.publicar_encuesta(self.account_id, sub, titulo, opciones, dias, selftext=cuerpo,
                                                        **comun)

        def _fin(res, err):
            self.boton_aceptar.set_sensitive(True)
            if err:
                notificar(_("No se pudo publicar en Reddit: {e}").format(e=err))
                return
            self.close()
            notificar(_("Publicado en r/{0}").format(sub))
            self.padre.rd_tras_publicar()
        self.boton_aceptar.set_sensitive(False)
        speak(_("Publicando en Reddit…"), interrupt=True)
        ejecutar(hacer, _fin)


# ---------------------------------------------------------------------------------------------- perfil
class DialogoPerfilReddit(Gtk.Window):
    """Ficha de un usuario con acciones: seguir, bloquear, escribirle y ver sus publicaciones."""

    def __init__(self, padre, account_id: int, usuario: str):
        usuario = usuario.removeprefix("/").removeprefix("u/")
        super().__init__(title=_("Perfil de u/{0}").format(usuario), transient_for=padre, modal=True)
        self.padre, self.account_id, self.usuario, self.siguiendo = padre, account_id, usuario, False
        self.set_default_size(560, 420)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)
        self.texto = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD, left_margin=8,
                                  right_margin=8, top_margin=6, bottom_margin=6)
        self.texto.update_property([Gtk.AccessibleProperty.LABEL], [_("Datos del perfil")])
        self.texto.get_buffer().set_text(_("Cargando el perfil…"))
        caja.append(Gtk.ScrolledWindow(child=self.texto, vexpand=True, has_frame=True))
        fila = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.b_seguir = Gtk.Button(label=_("_Seguir"), use_underline=True, sensitive=False)
        self.b_seguir.connect("clicked", lambda *_a: self._seguir())
        b_bloquear = Gtk.Button(label=_("_Bloquear"), use_underline=True)
        b_bloquear.connect("clicked", lambda *_a: self._bloquear())
        b_mensaje = Gtk.Button(label=_("Enviar _mensaje"), use_underline=True)
        b_mensaje.connect("clicked", lambda *_a: (self.close(), DialogoMensajeReddit(padre, account_id, usuario).present()))
        b_posts = Gtk.Button(label=_("Ver _publicaciones"), use_underline=True)
        b_posts.connect("clicked", lambda *_a: (self.close(), padre.rd_abrir_usuario(account_id, usuario)))
        b_web = Gtk.Button(label=_("Abrir en el na_vegador"), use_underline=True)
        b_web.connect("clicked", lambda *_a: webbrowser.open(f"{datos_reddit.REDDIT_BASE}/user/{usuario}"))
        cerrar = Gtk.Button(label=_("_Cerrar"), use_underline=True)
        cerrar.connect("clicked", lambda *_a: self.close())
        for b in (self.b_seguir, b_bloquear, b_mensaje, b_posts, b_web, cerrar):
            fila.append(b)
        caja.append(fila)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.set_focus(self.texto)
        ejecutar(lambda: reddit_service._cargar_perfil_sync(account_id, usuario), self._pintar)

    def _pintar(self, p, err):
        if err or not p:
            self.texto.get_buffer().set_text(_("No se pudo cargar el perfil: {e}").format(e=err))
            return
        creada = ""
        if p.get("created_utc"):
            try:
                creada = _("cuenta creada el {0}").format(
                    datetime.fromtimestamp(p["created_utc"], timezone.utc).strftime("%d/%m/%Y"))
            except Exception:
                pass
        valores = {"name": _("u/{0}").format(p.get("name", "")), "post_karma": str(p.get("post_karma", 0)),
                   "comment_karma": str(p.get("comment_karma", 0)), "created": creada, "bio": p.get("bio", "")}
        plantilla = config.get("accessibility", "reddit_person", "") or definicion("reddit_person")["default"]
        extra = [t for t, v in ((_("Moderador de algún subreddit"), p.get("is_mod")), (_("Empleado de Reddit"), p.get("is_employee")),
                                (_("La sigues"), p.get("is_friend"))) if v]
        self.texto.get_buffer().set_text("\n".join([pulir_lectura(plantilla.format_map(defaultdict(str, valores)))] + extra))
        self.siguiendo = bool(p.get("is_friend"))
        self.b_seguir.set_label(_("Dejar de _seguir") if self.siguiendo else _("_Seguir"))
        self.b_seguir.set_sensitive(True)

    def _seguir(self):
        seguir = not self.siguiendo

        def _fin(res, err):
            if err:
                notificar(_("No se pudo completar: {e}").format(e=err))
                return
            self.siguiendo = seguir
            self.b_seguir.set_label(_("Dejar de _seguir") if seguir else _("_Seguir"))
            notificar(_("Ahora sigues a u/{0}").format(self.usuario) if seguir else _("Has dejado de seguir a u/{0}").format(self.usuario))
        ejecutar(lambda: reddit_service.seguir_usuario(self.account_id, self.usuario, seguir), _fin)

    def _bloquear(self):
        def _hacer():
            ejecutar(lambda: reddit_service.bloquear_usuario(self.account_id, self.usuario),
                     lambda r, e: notificar(_("No se pudo bloquear: {e}").format(e=e) if e
                                            else _("u/{0} bloqueado").format(self.usuario)))
        confirmar_async(self, _("¿Bloquear a u/{0}?").format(self.usuario), _hacer, etiqueta_si=_("Bloquear"))


# ---------------------------------------------------------------------------------------------- hilo de comentarios
class DialogoHiloReddit(DialogoLista):
    """La publicación y sus comentarios en orden de lectura, con el nivel de anidamiento. Se puede responder, votar,
    guardar, editar y borrar sobre el elemento enfocado."""

    def __init__(self, padre, account_id: int, post_db_id: str):
        super().__init__(padre, _("Comentarios de la publicación de Reddit"), _("Comentarios del hilo"),
                         lambda pos: self._responder())
        self.account_id, self.post_db_id = account_id, post_db_id
        self.elementos = []
        self.yo = (reddit_service.nombre_usuario(account_id) or "").lower()
        self.set_default_size(820, 560)
        self.boton(_("_Responder"), self._responder)
        self.boton(_("Voto _positivo"), lambda: self._votar("up"))
        self.boton(_("Voto _negativo"), lambda: self._votar("down"))
        self.boton(_("_Quitar el voto"), lambda: self._votar("clear"))
        self.boton(_("_Guardar o quitar"), self._guardar)
        self.boton(_("_Editar"), self._editar)
        self.boton(_("_Borrar"), self._borrar)
        self.boton(_("_Traducir"), self._traducir)
        self.boton(_("Abrir en el na_vegador"), self._navegador)
        self.boton(_("Copiar en_lace"), self._copiar)
        self.boton(_("_Actualizar"), self._cargar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando comentarios…"))
        self._cargar()

    # ------------------------------------------------------------------ datos
    def _cargar(self):
        fila = db.query_one("SELECT post_id FROM reddit_posts WHERE id = ?", (self.post_db_id,))
        if not fila or not fila[0]:
            self.mostrar_estado(_("No se pudo localizar la publicación"))
            return
        speak(_("Cargando comentarios…"), interrupt=True)
        pos = max(self.lista.posicion(), 0)

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los comentarios: {e}").format(e=err))
                return
            post = dict((res or {}).get("post") or {})
            comentarios = (res or {}).get("comments") or []
            self.elementos = [dict(post, tipo="post", depth=-1)] + [dict(c, tipo="comment") for c in comentarios]
            self._pintar(pos)
            self.mostrar_estado(_("Hilo cargado: {n} comentarios").format(n=len(comentarios)))
            speak(_("Hilo cargado: {n} comentarios").format(n=len(comentarios)), interrupt=True)
        reddit_service.cargar_hilo_async(self.account_id, fila[0], callback=_fin)

    def _linea(self, el: dict) -> str:
        autor = _("u/{0}").format(el.get("author", ""))
        fecha = fecha_legible(datos_reddit.fecha_iso(el.get("created_utc")))
        voto = {1: _("Tu voto: positivo."), 0: _("Tu voto: negativo.")}.get(el.get("likes"), "")
        guardado = _("Guardado.") if el.get("saved") else ""
        if el["tipo"] == "post":
            cuerpo = (el.get("title") or "").rstrip(".")
            if el.get("selftext"):
                cuerpo += ". " + el["selftext"].replace("\n", " ").rstrip(".")
            return " ".join(p for p in (_("Publicación."), f"{autor}: {cuerpo}.", _("{0} puntos.").format(el.get("score", 0)),
                                        fecha + "." if fecha else "", voto, guardado) if p)
        nivel = _("Nivel {0}.").format(int(el.get("depth", 0)) + 1) if int(el.get("depth", 0)) > 0 else ""
        autor_original = _("Autor original.") if el.get("is_submitter") else ""
        return " ".join(p for p in (nivel, autor_original, f"{autor}: {(el.get('body') or '').replace(chr(10), ' ')}.",
                                    _("{0} puntos.").format(el.get("score", 0)), fecha + "." if fecha else "", voto,
                                    guardado) if p)

    def _pintar(self, posicion: int = 0):
        self.lista.poner([self._linea(el) for el in self.elementos])
        if self.elementos:
            self.lista.seleccion.set_selected(min(posicion, len(self.elementos) - 1))
        self.lista.enfocar()

    def _actual(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.elementos):
            notificar(_("Enfoca primero un elemento del hilo"))
            return None
        return self.elementos[p]

    def _refrescar_actual(self):
        p = self.lista.posicion()
        self._pintar(p)

    # ------------------------------------------------------------------ acciones
    def _responder(self):
        el = self._actual()
        if not el or not el.get("fullname"):
            return

        def _enviar(texto):
            speak(_("Enviando respuesta…"), interrupt=True)

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo responder: {e}").format(e=err))
                    return
                notificar(_("Respuesta enviada"))
                self._cargar()
            ejecutar(lambda: reddit_service.responder(self.account_id, el["fullname"], texto), _fin)
        DialogoTextoLibre(self, _("Responder en Reddit"), _("_Respuesta:"), _enviar, aceptar=_("_Enviar")).present()

    def _votar(self, direccion: str):
        el = self._actual()
        if not el:
            return
        mensajes = {"up": _("Voto positivo"), "down": _("Voto negativo"), "clear": _("Voto retirado")}

        def _fin(res, err):
            if err:
                notificar(_("No se pudo votar: {e}").format(e=err))
                return
            el["likes"] = {"up": 1, "down": 0}.get(direccion)
            self._refrescar_actual()
            speak(mensajes[direccion], interrupt=True)
        ejecutar(lambda: reddit_service.votar(self.account_id, el["fullname"], direccion), _fin)

    def _guardar(self):
        el = self._actual()
        if not el:
            return
        guardar = not el.get("saved")

        def _fin(res, err):
            if err:
                notificar(_("No se pudo completar: {e}").format(e=err))
                return
            el["saved"] = 1 if guardar else 0
            self._refrescar_actual()
            speak(_("Guardado") if guardar else _("Quitado de guardados"), interrupt=True)
        ejecutar(lambda: reddit_service.guardar(self.account_id, el["fullname"], guardar), _fin)

    def _propio(self, el) -> bool:
        return bool(el.get("author")) and str(el["author"]).lower() == self.yo

    def _editar(self):
        el = self._actual()
        if not el:
            return
        if not self._propio(el):
            notificar(_("Solo puedes editar lo tuyo"))
            return
        campo = "selftext" if el["tipo"] == "post" else "body"
        if el["tipo"] == "post" and not el.get("selftext"):
            notificar(_("Solo se puede editar el texto de una publicación de texto"))
            return

        def _guardar(texto):
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo editar: {e}").format(e=err))
                    return
                el[campo] = texto
                self._refrescar_actual()
                speak(_("Editado"), interrupt=True)
            ejecutar(lambda: reddit_service.editar(self.account_id, el["fullname"], texto), _fin)
        DialogoTextoLibre(self, _("Editar en Reddit"), _("_Texto:"), _guardar, texto=el.get(campo) or "",
                          aceptar=_("_Guardar")).present()

    def _borrar(self):
        el = self._actual()
        if not el:
            return
        if not self._propio(el):
            notificar(_("Solo puedes borrar lo tuyo"))
            return

        def _hacer():
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo borrar: {e}").format(e=err))
                    return
                notificar(_("Borrado"))
                self._cargar()
            ejecutar(lambda: reddit_service.borrar(self.account_id, el["fullname"]), _fin)
        confirmar_async(self, _("¿Seguro que quieres borrar este elemento? No se puede deshacer."), _hacer,
                        etiqueta_si=_("Borrar"))

    def _traducir(self):
        el = self._actual()
        if not el:
            return
        texto = (f"{el.get('title', '')}\n\n{el.get('selftext', '')}" if el["tipo"] == "post" else el.get("body", "")).strip()
        if not texto:
            notificar(_("No hay texto que traducir"))
            return
        from . import acciones_ia
        acciones_ia.traducir_texto(self, texto)

    def avisar(self, texto: str, interrumpir: bool = True):
        self.mostrar_estado(texto)
        speak(texto, interrupt=interrumpir)

    def _navegador(self):
        el = self._actual()
        if el and el.get("permalink"):
            webbrowser.open(datos_reddit.url_permalink(el["permalink"]))

    def _copiar(self):
        el = self._actual()
        if el and el.get("permalink"):
            copiar_al_portapapeles(datos_reddit.url_permalink(el["permalink"]), _("Enlace copiado"))
