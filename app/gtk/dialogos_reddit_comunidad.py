# -*- coding: utf-8 -*-
"""Reddit: wiki de una comunidad y modmail (la mensajería de moderación).

Usan los mismos servicios que la versión de Windows (reddit_wiki.py, reddit_modmail.py)."""

import logging
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.reddit import reddit_service

from .dialogos import DialogoBase, ejecutar
from .dialogos_mastodon import DialogoLista, ListaSimple
from .dialogos_reddit import _marco, _texto_de, _vista_texto

log = logging.getLogger(__name__)

ESTADOS_MODMAIL = [("all", "Todas"), ("new", "Nuevas"), ("inprogress", "En curso"), ("archived", "Archivadas"),
                   ("appeals", "Apelaciones"), ("mod", "De moderadores"), ("notifications", "Notificaciones"),
                   ("join_requests", "Solicitudes de ingreso")]


def fecha(epoch) -> str:
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%d/%m/%Y %H:%M") if epoch else ""
    except Exception:
        return ""


def _hacer(padre, fn, ok: str, tras=None):
    """Ejecuta una acción de red y avisa del resultado; `tras(res)` se llama si salió bien."""
    def _fin(res, err):
        if err:
            notificar(_("No se pudo completar: {e}").format(e=err))
            return
        notificar(ok)
        if tras:
            tras(res)
    ejecutar(fn, _fin)


# --------------------------------------------------------------------------- wiki
class DialogoWikiReddit(Gtk.Window):
    """Páginas de la wiki de una comunidad: leerlas y, si hay permiso, editarlas, crear páginas nuevas y ver las revisiones."""

    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(title=_("Wiki de r/{s}").format(s=subreddit), transient_for=padre, modal=True)
        from .mnemonicos import vigilar
        vigilar(self)
        self.padre, self.account_id, self.subreddit = padre, account_id, subreddit
        self.paginas, self.actual = [], None
        self.set_default_size(760, 640)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        caja.append(self.estado)
        self.lista = ListaSimple(_("Páginas de la wiki"), lambda pos: self._abrir(pos))
        self.lista.set_vexpand(False)
        self.lista.set_min_content_height(120)
        caja.append(self.lista)
        etiqueta = Gtk.Label(label=_("_Contenido de la página:"), use_underline=True, xalign=0)
        caja.append(etiqueta)
        self.texto = _vista_texto()
        etiqueta.set_mnemonic_widget(self.texto)
        self.texto.update_property([Gtk.AccessibleProperty.LABEL], [_("Contenido de la página")])
        marco = _marco(self.texto, 260)
        marco.set_vexpand(True)
        caja.append(marco)
        self.meta = Gtk.Label(xalign=0, wrap=True)
        caja.append(self.meta)
        botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        caja.append(botones)
        self.b_guardar = self._boton(botones, _("_Guardar cambios"), self._guardar, False)
        self._boton(botones, _("Página _nueva…"), self._nueva)
        self._boton(botones, _("Ver _revisiones"), self._revisiones)
        self._boton(botones, _("_Cerrar"), self.close)
        self.lista.siguiente = self.texto
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.estado.set_text(_("Cargando las páginas…"))
        self._cargar()

    @staticmethod
    def _boton(caja, etiqueta, fn, sensible=True):
        b = Gtk.Button(label=etiqueta, use_underline=True, sensitive=sensible)
        b.connect("clicked", lambda *_a: fn())
        caja.append(b)
        return b

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.estado.set_text(_("No se pudo cargar la wiki: {e}").format(e=err))
                return
            self.paginas = res or []
            self.lista.poner(self.paginas)
            self.estado.set_text(_("{n} páginas").format(n=len(self.paginas)) if self.paginas
                                 else _("Esta comunidad no tiene páginas wiki."))
            if self.paginas:
                self._abrir(0)
            self.lista.enfocar()
        ejecutar(lambda: reddit_service.listar_paginas_wiki(self.account_id, self.subreddit), _fin)

    def _abrir(self, pos):
        if not 0 <= pos < len(self.paginas):
            return
        pagina = self.paginas[pos]
        self.estado.set_text(_("Cargando {p}…").format(p=pagina))

        def _fin(res, err):
            if err:
                self.estado.set_text(_("No se pudo abrir la página: {e}").format(e=err))
                return
            self.actual = res
            self.texto.get_buffer().set_text(res.get("content_md", ""))
            puede = bool(res.get("may_revise"))
            self.b_guardar.set_sensitive(puede)
            meta = _("Última edición: {f}").format(f=fecha(res.get("revision_date")))
            if res.get("revision_by"):
                meta += " " + _("por u/{u}").format(u=res["revision_by"])
            if not puede:
                meta += " (" + _("solo lectura") + ")"
            self.meta.set_text(meta)
            self.estado.set_text(_("Página cargada."))
            self.texto.set_editable(puede)
        ejecutar(lambda: reddit_service.leer_pagina_wiki(self.account_id, self.subreddit, pagina), _fin)

    def _guardar(self):
        if not self.actual:
            return
        pagina, contenido = self.actual.get("name"), _texto_de(self.texto)
        d = DialogoBase(self, _("Guardar la página"), _("_Guardar"))
        motivo = d.campo(_("_Motivo del cambio (opcional):"), Gtk.Entry(activates_default=True, width_chars=40))
        d.set_focus(motivo)

        def _ok():
            d.close()
            _hacer(self, lambda: reddit_service.editar_pagina_wiki(self.account_id, self.subreddit, pagina, contenido,
                                                                   motivo.get_text().strip()), _("Página guardada."))
        d.aceptar = _ok
        d.present()

    def _nueva(self):
        d = DialogoBase(self, _("Página nueva"), _("_Crear"))
        nombre = d.campo(_("_Nombre de la página (por ejemplo reglas/spam):"), Gtk.Entry(activates_default=True, width_chars=40))
        d.set_focus(nombre)

        def _ok():
            texto = nombre.get_text().strip().strip("/")
            if not texto:
                return
            d.close()
            _hacer(self, lambda: reddit_service.crear_pagina_wiki(self.account_id, self.subreddit, texto,
                                                                  _("(nueva página)")),
                   _("Página creada."), lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _revisiones(self):
        if not self.actual:
            return
        pagina = self.actual.get("name")

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar las revisiones: {e}").format(e=err))
                return
            d = DialogoLista(self, _("Revisiones de {p}").format(p=pagina), _("Revisiones"))
            d.lista.poner([_("{f}, u/{u}: {m}").format(f=fecha(r.get("timestamp")), u=r.get("author") or "?",
                                                       m=r.get("reason") or _("sin motivo")) for r in res or []])
            d.mostrar_estado(_("{n} revisiones").format(n=len(res or [])))
            d.cerrar_boton()
            d.present()
        ejecutar(lambda: reddit_service.revisiones_wiki(self.account_id, self.subreddit, pagina), _fin)


# --------------------------------------------------------------------------- modmail
class DialogoModmailReddit(DialogoLista):
    """Conversaciones de modmail de una comunidad (o de todas las que moderas): abrirlas, responder, archivar y crear nuevas."""

    def __init__(self, padre, account_id: int, subreddit: str = "all"):
        super().__init__(padre, _("Modmail de r/{s}").format(s=subreddit) if subreddit != "all" else _("Modmail"),
                         _("Conversaciones de modmail"), lambda pos: self._abrir(pos))
        self.account_id, self.subreddit, self.convs = account_id, subreddit, []
        self.combo = Gtk.DropDown.new_from_strings([_(n) for _c, n in ESTADOS_MODMAIL])
        self.combo.update_property([Gtk.AccessibleProperty.LABEL], [_("Bandeja")])
        self.combo.connect("notify::selected", lambda *_a: self._cargar())
        self.fila_botones.prepend(self.combo)
        self.boton(_("_Abrir"), lambda: self._abrir(self.lista.posicion()))
        self.boton(_("_Archivar"), lambda: self._archivar(True))
        self.boton(_("_Desarchivar"), lambda: self._archivar(False))
        self.boton(_("_Nueva conversación…"), self._nueva)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        estado = ESTADOS_MODMAIL[self.combo.get_selected()][0]
        self.mostrar_estado(_("Cargando las conversaciones…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar el modmail: {e}").format(e=err))
                return
            self.convs = res or []
            self.lista.poner([_("{a}, de u/{u}, {n} mensajes, {f}").format(
                a=c.get("subject") or _("sin asunto"), u=c.get("participant", ""), n=c.get("num_messages", 0),
                f=fecha(c.get("last_updated"))) for c in self.convs])
            self.mostrar_estado(_("{n} conversaciones").format(n=len(self.convs)))
        ejecutar(lambda: reddit_service.listar_conversaciones_modmail(self.account_id, self.subreddit, estado), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.convs[p] if 0 <= p < len(self.convs) else None

    def _abrir(self, pos):
        if not 0 <= pos < len(self.convs):
            return
        c = self.convs[pos]
        sub = c.get("subreddit") or self.subreddit

        def _fin(res, err):
            if err:
                notificar(_("No se pudo abrir la conversación: {e}").format(e=err))
                return
            DialogoConversacionModmail(self, self.account_id, sub, c.get("id"), res).present()
        ejecutar(lambda: reddit_service.leer_conversacion_modmail(self.account_id, sub, c.get("id")), _fin)

    def _archivar(self, archivar: bool):
        c = self._actual()
        if not c:
            return
        sub = c.get("subreddit") or self.subreddit
        _hacer(self, lambda: reddit_service.archivar_modmail(self.account_id, sub, c.get("id"), archivar),
               _("Archivada.") if archivar else _("Desarchivada."), lambda _r: self._cargar())

    def _nueva(self):
        d = DialogoBase(self, _("Nueva conversación de modmail"), _("_Enviar"))
        sub = d.campo(_("_Subreddit (sin r/):"), Gtk.Entry(activates_default=True, text="" if self.subreddit == "all"
                                                           else self.subreddit, width_chars=36))
        dest = d.campo(_("_Destinatario (usuario, sin u/):"), Gtk.Entry(activates_default=True))
        asunto = d.campo(_("_Asunto:"), Gtk.Entry(activates_default=True, max_length=100))
        cuerpo = _vista_texto()
        marco = _marco(cuerpo, 120)
        lbl = Gtk.Label(label=_("_Mensaje:"), use_underline=True, xalign=0, yalign=0)
        lbl.set_mnemonic_widget(cuerpo)
        d.rejilla.attach(lbl, 0, d._fila, 1, 1)
        d.rejilla.attach(marco, 1, d._fila, 1, 1)
        d.set_focus(dest)

        def _ok():
            s, u, a, m = (sub.get_text().strip().lstrip("r/").lstrip("/"), dest.get_text().strip(),
                          asunto.get_text().strip(), _texto_de(cuerpo).strip())
            if not (s and u and a and m) or s == "all":
                notificar(_("Rellena el subreddit, el destinatario, el asunto y el mensaje."))
                return
            d.close()
            _hacer(self, lambda: reddit_service.crear_modmail(self.account_id, s, a, m, u),
                   _("Conversación creada."), lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()


class DialogoConversacionModmail(Gtk.Window):
    """Una conversación de modmail: los mensajes (solo lectura) y la respuesta, con opción de nota interna."""

    def __init__(self, padre, account_id: int, subreddit: str, conv_id: str, datos: dict):
        super().__init__(title=datos.get("subject") or _("Conversación"), transient_for=padre, modal=True)
        from .mnemonicos import vigilar
        vigilar(self)
        self.account_id, self.subreddit, self.conv_id = account_id, subreddit, conv_id
        self.padre = padre
        self.set_default_size(680, 600)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)
        lineas = [_("Asunto: {a}").format(a=datos.get("subject", "")), ""]
        for m in datos.get("messages", []):
            nota = " " + _("(nota interna)") if m.get("es_interno") else ""
            lineas += [_("u/{u}{n} — {f}:").format(u=m.get("author", ""), n=nota, f=fecha(m.get("date"))),
                       m.get("body", ""), ""]
        et1 = Gtk.Label(label=_("_Mensajes:"), use_underline=True, xalign=0)
        caja.append(et1)
        self.mensajes = _vista_texto(False)
        self.mensajes.get_buffer().set_text("\n".join(lineas))
        et1.set_mnemonic_widget(self.mensajes)
        marco = _marco(self.mensajes, 260)
        marco.set_vexpand(True)
        caja.append(marco)
        et2 = Gtk.Label(label=_("Tu _respuesta:"), use_underline=True, xalign=0)
        caja.append(et2)
        self.respuesta = _vista_texto()
        et2.set_mnemonic_widget(self.respuesta)
        caja.append(_marco(self.respuesta, 100))
        self.interno = Gtk.CheckButton(label=_("Enviar como _nota interna (solo moderadores)"), use_underline=True)
        caja.append(self.interno)
        botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        caja.append(botones)
        enviar = Gtk.Button(label=_("_Responder"), use_underline=True)
        enviar.add_css_class("suggested-action")
        enviar.connect("clicked", lambda *_a: self._responder())
        cerrar = Gtk.Button(label=_("_Cerrar"), use_underline=True)
        cerrar.connect("clicked", lambda *_a: self.close())
        botones.append(enviar)
        botones.append(cerrar)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)

    def present(self):
        super().present()
        self.mensajes.grab_focus()

    def _responder(self):
        texto = _texto_de(self.respuesta).strip()
        if not texto:
            notificar(_("Escribe la respuesta"))
            return
        interno = self.interno.get_active()
        _hacer(self, lambda: reddit_service.responder_modmail(self.account_id, self.subreddit, self.conv_id, texto, interno),
               _("Respuesta enviada."), lambda _r: self.close())



# --------------------------------------------------------------------------- etiquetas (flair), emojis y barra lateral
class DialogoFlairsReddit(DialogoLista):
    """Etiquetas de un subreddit: plantillas de usuario o de publicación, tu etiqueta y asignarla a otros."""

    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(padre, _("Etiquetas de r/{s}").format(s=subreddit), _("Plantillas de etiqueta"))
        self.account_id, self.subreddit, self.filas = account_id, subreddit, []
        self.combo = Gtk.DropDown.new_from_strings([_("Etiquetas de usuario"), _("Etiquetas de publicación")])
        self.combo.update_property([Gtk.AccessibleProperty.LABEL], [_("Tipo de etiqueta")])
        self.combo.connect("notify::selected", lambda *_a: self._cargar())
        self.fila_botones.prepend(self.combo)
        self.boton(_("_Nueva plantilla…"), self._nueva)
        self.boton(_("_Borrar plantilla"), self._borrar)
        self.b_mia = self.boton(_("Establecer _mi etiqueta…"), self._mi_flair)
        self.b_asig = self.boton(_("_Asignar a un usuario…"), self._asignar)
        self.cerrar_boton()
        self._cargar()

    def _es_link(self) -> bool:
        return self.combo.get_selected() == 1

    def _cargar(self):
        es_link = self._es_link()
        self.b_mia.set_sensitive(not es_link)
        self.b_asig.set_sensitive(not es_link)
        self.mostrar_estado(_("Cargando…"))
        fn = reddit_service.plantillas_flair_enlace if es_link else reddit_service.plantillas_flair_usuario

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las plantillas: {e}").format(e=err))
                return
            self.filas = res or []
            self.lista.poner([f"{t.get('text') or _('sin texto')}. " + (_("solo moderadores") if t.get("mod_only") else _("todos"))
                              + (". " + _("editable") if t.get("text_editable") else "") for t in self.filas])
            self.mostrar_estado(_("{n} plantillas").format(n=len(self.filas)))
            self.lista.enfocar()
        ejecutar(lambda: fn(self.account_id, self.subreddit), _fin)

    def _nueva(self):
        d = DialogoBase(self, _("Nueva plantilla de etiqueta"), _("_Crear"))
        texto = d.campo(_("_Texto de la etiqueta:"), Gtk.Entry(activates_default=True, max_length=64, width_chars=36))
        mod = d.campo(_("Solo la pueden usar los _moderadores:"), Gtk.Switch(halign=Gtk.Align.START))
        edit = d.campo(_("El usuario puede _editar el texto:"), Gtk.Switch(halign=Gtk.Align.START))
        d.set_focus(texto)

        def _ok():
            t = texto.get_text().strip()
            if not t:
                return notificar(_("Escribe el texto de la etiqueta."))
            args = dict(tipo="link" if self._es_link() else "user", mod_only=mod.get_active(), text_editable=edit.get_active())
            d.close()
            _hacer(self, lambda: reddit_service.crear_plantilla_flair(self.account_id, self.subreddit, t, **args),
                   _("Plantilla creada."), lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _borrar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.filas):
            return
        t = self.filas[p]
        from .dialogos import confirmar_async
        confirmar_async(self, _("¿Borrar la plantilla «{t}»?").format(t=t.get("text") or ""),
                        lambda: _hacer(self, lambda: reddit_service.borrar_plantilla_flair(self.account_id, self.subreddit,
                                                                                         t.get("id", "")),
                                       _("Plantilla borrada."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))

    def _mi_flair(self):
        d = DialogoBase(self, _("Mi etiqueta"), _("_Guardar"))
        texto = d.campo(_("_Texto de tu etiqueta (vacío para quitarla):"), Gtk.Entry(activates_default=True, width_chars=36))
        d.set_focus(texto)

        def _ok():
            t = texto.get_text().strip()
            d.close()
            _hacer(self, lambda: reddit_service.establecer_mi_flair(self.account_id, self.subreddit, text=t),
                   _("Etiqueta actualizada."))
        d.aceptar = _ok
        d.present()

    def _asignar(self):
        d = DialogoBase(self, _("Asignar una etiqueta"), _("_Asignar"))
        usuario = d.campo(_("_Usuario (sin u/):"), Gtk.Entry(activates_default=True, width_chars=30))
        texto = d.campo(_("_Texto de la etiqueta (vacío para quitarla):"), Gtk.Entry(activates_default=True, width_chars=36))
        d.set_focus(usuario)

        def _ok():
            u, t = usuario.get_text().strip().lstrip("u/").lstrip("/"), texto.get_text().strip()
            if not u:
                return
            d.close()
            if t:
                _hacer(self, lambda: reddit_service.asignar_flair_usuario(self.account_id, self.subreddit, u, text=t),
                       _("Etiqueta asignada."))
            else:
                _hacer(self, lambda: reddit_service.quitar_flair_usuario(self.account_id, self.subreddit, u), _("Etiqueta quitada."))
        d.aceptar = _ok
        d.present()


class DialogoEmojisReddit(DialogoLista):
    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(padre, _("Emojis de r/{s}").format(s=subreddit), _("Lista de emojis"))
        self.account_id, self.subreddit, self.emojis = account_id, subreddit, []
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los emojis…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los emojis: {e}").format(e=err))
                return
            self.emojis = res or []
            self.lista.poner([f":{e.get('name', '')}:" + (". " + _("solo moderadores") if e.get("mod_flair_only") else "")
                              for e in self.emojis])
            self.mostrar_estado(_("{n} emojis").format(n=len(self.emojis)))
            self.lista.enfocar()
        ejecutar(lambda: reddit_service.listar_emojis(self.account_id, self.subreddit), _fin)

    def _anadir(self):
        d = DialogoBase(self, _("Nuevo emoji"), _("_Elegir la imagen…"))
        nombre = d.campo(_("_Nombre del emoji (sin dos puntos):"), Gtk.Entry(activates_default=True, width_chars=30))
        d.set_focus(nombre)

        def _ok():
            n = nombre.get_text().strip().strip(":")
            if not n:
                return
            d.close()
            dlg = Gtk.FileDialog(title=_("Imagen del emoji"))

            def _fin(origen, res):
                try:
                    ruta = origen.open_finish(res).get_path()
                except Exception:
                    return
                _hacer(self, lambda: reddit_service.añadir_emoji(self.account_id, self.subreddit, n, ruta), _("Emoji añadido."),
                       lambda _r: self._cargar())
            dlg.open(self, None, _fin)
        d.aceptar = _ok
        d.present()

    def _borrar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.emojis):
            nombre = self.emojis[p].get("name", "")
            from .dialogos import confirmar_async
            confirmar_async(self, _("¿Borrar el emoji «{n}»?").format(n=nombre),
                            lambda: _hacer(self, lambda: reddit_service.borrar_emoji(self.account_id, self.subreddit, nombre),
                                           _("Emoji borrado."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))


class DialogoWidgetsReddit(DialogoLista):
    """Los elementos de la barra lateral de un subreddit (solo lectura): Intro muestra su contenido."""

    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(padre, _("Barra lateral de r/{s}").format(s=subreddit), _("Elementos de la barra lateral"),
                         lambda pos: self._ver(pos))
        self.widgets = []
        self.boton(_("_Ver el contenido"), lambda: self._ver(self.lista.posicion()))
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando la barra lateral…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar la barra lateral: {e}").format(e=err))
                return
            self.widgets = res or []
            self.lista.poner([_("{n}, tipo {k}").format(n=w.get("shortName") or w.get("kind") or _("(elemento)"), k=w.get("kind", ""))
                              for w in self.widgets])
            self.mostrar_estado(_("{n} elementos").format(n=len(self.widgets)))
            self.lista.enfocar()
        ejecutar(lambda: reddit_service.listar_widgets_sidebar(account_id, subreddit), _fin)

    def _ver(self, pos):
        if 0 <= pos < len(self.widgets):
            from .dialogos_peertube_instancia import texto_en_dialogo
            w = self.widgets[pos]
            texto_en_dialogo(self, w.get("shortName") or w.get("kind") or _("Elemento"), w.get("text") or _("(sin texto)"))
