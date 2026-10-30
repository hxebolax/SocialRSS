# -*- coding: utf-8 -*-
"""Reddit: hilos en directo, colecciones y borradores (los mismos servicios que usa la versión de Windows)."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.reddit import reddit_service

from .dialogos import DialogoBase, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_reddit import _marco, _texto_de, _vista_texto
from .dialogos_reddit_comunidad import _hacer


def _fila_texto(dlg: DialogoBase, etiqueta: str, alto: int = 110):
    """Fila 'etiqueta: cuadro de texto de varias líneas' en la rejilla de un diálogo."""
    tv = _vista_texto()
    lbl = Gtk.Label(label=etiqueta, use_underline=True, xalign=0, yalign=0)
    lbl.set_mnemonic_widget(tv)
    dlg.rejilla.attach(lbl, 0, dlg._fila, 1, 1)
    dlg.rejilla.attach(_marco(tv, alto), 1, dlg._fila, 1, 1)
    dlg._fila += 1
    return tv


# --------------------------------------------------------------------------- hilos en directo
class DialogoLiveReddit(DialogoLista):
    """Un hilo en directo: abrirlo por su identificador (o crear uno), leer las novedades y publicar."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Hilos en directo"), _("Novedades del hilo"))
        self.account_id, self.thread_id = account_id, ""
        self.entrada = Gtk.Entry(hexpand=True, width_chars=24)
        self.entrada.update_property([Gtk.AccessibleProperty.LABEL], [_("Identificador del hilo en directo")])
        self.entrada.connect("activate", lambda *_a: self._abrir())
        self.fila_botones.prepend(self.entrada)
        self.boton(_("_Abrir"), self._abrir)
        self.boton(_("_Crear nuevo…"), self._crear)
        self.b_pub = self.boton(_("_Publicar novedad…"), self._publicar, False)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self.mostrar_estado(_("Escribe el identificador de un hilo en directo y pulsa Intro."))

    def present(self):
        Gtk.Window.present(self)
        self.entrada.grab_focus()

    def _abrir(self):
        tid = self.entrada.get_text().strip()
        if tid:
            self.thread_id = tid
            self._cargar()

    def _crear(self):
        d = DialogoBase(self, _("Nuevo hilo en directo"), _("_Crear"))
        titulo = d.campo(_("_Título:"), Gtk.Entry(activates_default=True, width_chars=40))
        desc = _fila_texto(d, _("_Descripción (opcional):"), 90)
        d.set_focus(titulo)

        def _ok():
            t = titulo.get_text().strip()
            if not t:
                return
            texto = _texto_de(desc).strip()
            d.close()

            def _tras(nuevo):
                if nuevo:
                    self.entrada.set_text(nuevo)
                    self._abrir()
            _hacer(self, lambda: reddit_service.crear_live_thread(self.account_id, t, texto), _("Hilo creado."), _tras)
        d.aceptar = _ok
        d.present()

    def _cargar(self):
        if not self.thread_id:
            return
        self.b_pub.set_sensitive(True)
        self.mostrar_estado(_("Cargando las novedades…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar el hilo: {e}").format(e=err))
                return
            lineas = []
            for u in res or []:
                texto = (u.get("body", "") or "").replace("\n", " ")
                lineas.append(_("u/{u}: {t}").format(u=u.get("author", ""), t=(_("[tachada] ") if u.get("stricken") else "") + texto))
            self.lista.poner(lineas)
            self.mostrar_estado(_("{n} novedades").format(n=len(lineas)))
            self.lista.enfocar()
        ejecutar(lambda: reddit_service.leer_updates_live(self.account_id, self.thread_id), _fin)

    def _publicar(self):
        if not self.thread_id:
            return
        d = DialogoBase(self, _("Publicar novedad"), _("_Publicar"))
        texto = _fila_texto(d, _("_Texto de la novedad:"))

        def _ok():
            t = _texto_de(texto).strip()
            if not t:
                return
            d.close()
            _hacer(self, lambda: reddit_service.publicar_update_live(self.account_id, self.thread_id, t),
                   _("Novedad publicada."), lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()


# --------------------------------------------------------------------------- colecciones
class DialogoColeccionesReddit(DialogoLista):
    """Colecciones de un subreddit: ver su contenido, crearlas y borrarlas."""

    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(padre, _("Colecciones de r/{s}").format(s=subreddit), _("Colecciones"), lambda pos: self._ver(pos))
        self.account_id, self.subreddit, self.cols = account_id, subreddit, []
        self.boton(_("_Ver"), lambda: self._ver(self.lista.posicion()))
        self.boton(_("_Nueva colección…"), self._nueva)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando las colecciones…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las colecciones: {e}").format(e=err))
                return
            self.cols = res or []
            self.lista.poner([_("{t}, {n} publicaciones").format(t=c.get("title") or _("sin título"), n=c.get("num_links", 0))
                              for c in self.cols])
            self.mostrar_estado(_("{n} colecciones").format(n=len(self.cols)))
            self.lista.enfocar()
        ejecutar(lambda: reddit_service.listar_collections(self.account_id, self.subreddit), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.cols[p] if 0 <= p < len(self.cols) else None

    def _ver(self, pos):
        if not 0 <= pos < len(self.cols):
            return
        c = self.cols[pos]

        def _fin(res, err):
            if err:
                notificar(_("No se pudo abrir la colección: {e}").format(e=err))
                return
            d = DialogoLista(self, _("Colección: {t}").format(t=res.get("title", "")), _("Publicaciones de la colección"))
            d.lista.poner([_("{t} (u/{a})").format(t=p.get("title", ""), a=p.get("author", "")) for p in res.get("posts", [])])
            d.mostrar_estado(" ".join(x for x in (res.get("description", ""), _("{n} publicaciones").format(
                n=len(res.get("posts", [])))) if x))
            d.cerrar_boton()
            d.present()
        ejecutar(lambda: reddit_service.leer_collection(self.account_id, self.subreddit, c.get("id")), _fin)

    def _nueva(self):
        d = DialogoBase(self, _("Nueva colección"), _("_Crear"))
        titulo = d.campo(_("_Título:"), Gtk.Entry(activates_default=True, width_chars=40))
        desc = _fila_texto(d, _("_Descripción (opcional):"), 90)
        d.set_focus(titulo)

        def _ok():
            t = titulo.get_text().strip()
            if not t:
                return
            texto = _texto_de(desc).strip()
            d.close()
            _hacer(self, lambda: reddit_service.crear_collection(self.account_id, self.subreddit, t, texto),
                   _("Colección creada."), lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _borrar(self):
        c = self._actual()
        if not c:
            return
        confirmar_async(self, _("¿Borrar la colección «{t}»?").format(t=c.get("title", "")),
                        lambda: _hacer(self, lambda: reddit_service.borrar_collection(self.account_id, self.subreddit, c.get("id")),
                                       _("Colección borrada."), lambda _r: self._cargar()),
                        etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- borradores
class DialogoBorradoresReddit(DialogoLista):
    """Borradores de publicaciones guardados en Reddit: crear, publicar y borrar."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Borradores de Reddit"), _("Lista de borradores"))
        self.account_id, self.drafts = account_id, []
        self.boton(_("_Nuevo…"), self._nuevo)
        self.boton(_("_Publicar"), self._enviar)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los borradores…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los borradores: {e}").format(e=err))
                return
            self.drafts = res or []
            self.lista.poner([
                _("{t}, en r/{s}, {k}").format(t=d.get("title") or _("sin título"), s=d.get("subreddit") or "?",
                                               k=_("enlace") if d.get("is_link") else _("texto")) for d in self.drafts])
            self.mostrar_estado(_("{n} borradores").format(n=len(self.drafts)))
            self.lista.enfocar()
        ejecutar(lambda: reddit_service.listar_drafts(self.account_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.drafts[p] if 0 <= p < len(self.drafts) else None

    def _nuevo(self):
        d = DialogoBase(self, _("Nuevo borrador"), _("_Guardar"))
        sub = d.campo(_("_Comunidad (sin r/; opcional):"), Gtk.Entry(activates_default=True))
        titulo = d.campo(_("_Título:"), Gtk.Entry(activates_default=True, width_chars=40, max_length=300))
        url = d.campo(_("_Enlace (déjalo vacío para un borrador de texto):"), Gtk.Entry(activates_default=True))
        cuerpo = _fila_texto(d, _("Te_xto (si no es un enlace):"), 120)
        d.set_focus(titulo)

        def _ok():
            t = titulo.get_text().strip()
            if not t:
                notificar(_("Escribe el título"))
                return
            datos = (sub.get_text().strip().lstrip("r/").lstrip("/"), t, _texto_de(cuerpo), url.get_text().strip())
            d.close()
            _hacer(self, lambda: reddit_service.crear_draft(self.account_id, *datos), _("Borrador guardado."),
                   lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _enviar(self):
        d = self._actual()
        if d:
            confirmar_async(self, _("¿Publicar el borrador «{t}»?").format(t=d.get("title", "")),
                            lambda: _hacer(self, lambda: reddit_service.enviar_draft(self.account_id, d.get("id")),
                                           _("Borrador publicado."), lambda _r: self._cargar()),
                            etiqueta_si=_("Publicar"))

    def _borrar(self):
        d = self._actual()
        if d:
            confirmar_async(self, _("¿Borrar el borrador «{t}»?").format(t=d.get("title", "")),
                            lambda: _hacer(self, lambda: reddit_service.borrar_draft(self.account_id, d.get("id")),
                                           _("Borrador eliminado."), lambda _r: self._cargar()),
                            etiqueta_si=_("Borrar"))
