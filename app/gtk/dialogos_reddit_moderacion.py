# -*- coding: utf-8 -*-
"""Reddit: moderación de subreddits (colas, acciones sobre un elemento, usuarios, motivos de eliminación, configuración y
registro). Los mismos servicios que usa la versión de Windows (reddit_moderacion.py)."""

import webbrowser

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.reddit import reddit_service

from .dialogos import DialogoBase, DialogoElegir, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_reddit import _marco, _texto_de, _vista_texto
from .dialogos_reddit_comunidad import _hacer, fecha

REDDIT = "https://www.reddit.com"
COLAS = [("modqueue", "Cola de moderación"), ("reports", "Reportados"), ("spam", "Spam"), ("unmoderated", "Sin moderar"),
         ("edited", "Editados")]
RELACIONES = [("banned", "Usuarios baneados"), ("muted", "Usuarios silenciados"), ("contributor", "Contribuidores aprobados"),
              ("moderator", "Moderadores")]
PERMISOS_MOD = ["all", "access", "chat_config", "chat_operator", "config", "flair", "mail", "posts", "wiki"]
TIPOS_ACCESO = [("public", "Público"), ("restricted", "Restringido"), ("private", "Privado")]
CONFIG_TEXTO = [("title", "Título del subreddit"), ("public_description", "Descripción pública (subtítulo)"),
                ("description", "Barra lateral (descripción larga)"), ("submit_text", "Texto al publicar")]


def acciones_de_elemento(cuenta: int, fullname: str, comentario: bool, quitar_de_cola=None):
    """[(etiqueta, función, aviso)] de moderación sobre una publicación o un comentario (por su fullname)."""
    R = reddit_service
    acc = [
        (_("Aprobar"), lambda: R.mod_aprobar(cuenta, fullname), _("Aprobado."), True),
        (_("Eliminar"), lambda: R.mod_eliminar(cuenta, fullname), _("Eliminado."), True),
        (_("Eliminar como spam"), lambda: R.mod_eliminar(cuenta, fullname, spam=True), _("Marcado como spam."), True),
        (_("Bloquear comentarios"), lambda: R.mod_bloquear(cuenta, fullname, True), _("Bloqueado."), False),
        (_("Desbloquear comentarios"), lambda: R.mod_bloquear(cuenta, fullname, False), _("Desbloqueado."), False),
        (_("Distinguir (con placa de moderador)"), lambda: R.mod_distinguir(cuenta, fullname, how="yes"),
         _("Distinguido."), False),
    ]
    if not comentario:
        acc.append((_("Distinguir y fijar arriba"), lambda: R.mod_distinguir(cuenta, fullname, how="yes", sticky=True),
                    _("Distinguido y fijado."), False))
    acc += [
        (_("Quitar distinción"), lambda: R.mod_distinguir(cuenta, fullname, how="no"), _("Distinción quitada."), False),
        (_("Ignorar reportes"), lambda: R.mod_ignorar_reportes(cuenta, fullname, True), _("Reportes ignorados."), False),
        (_("Dejar de ignorar reportes"), lambda: R.mod_ignorar_reportes(cuenta, fullname, False),
         _("Se vuelven a tener en cuenta los reportes."), False),
    ]
    return acc


def moderar_elemento(padre, cuenta: int, fullname: str, comentario: bool, al_quitar=None):
    """Pregunta qué acción de moderación aplicar a un elemento y la ejecuta. `al_quitar()` se llama si el elemento sale de la
    cola (aprobado o eliminado)."""
    acciones = acciones_de_elemento(cuenta, fullname, comentario)
    etiquetas = [a[0] for a in acciones] + [_("Enviar un mensaje de eliminación…")]

    def _elegida(i):
        if i == len(acciones):
            return mensaje_eliminacion(padre, cuenta, fullname)
        _et, fn, aviso, quita = acciones[i]
        _hacer(padre, fn, aviso, (lambda _r: al_quitar()) if quita and al_quitar else None)
    DialogoElegir(padre, _("Moderar"), _("_Acción:"), etiquetas, _elegida, aceptar=_("_Aplicar")).present()


def mensaje_eliminacion(padre, cuenta: int, fullname: str):
    d = DialogoBase(padre, _("Mensaje de eliminación"), _("_Enviar"))
    tv = _vista_texto()
    lbl = Gtk.Label(label=_("_Motivo que se enviará al autor:"), use_underline=True, xalign=0, yalign=0)
    lbl.set_mnemonic_widget(tv)
    d.rejilla.attach(lbl, 0, 0, 1, 1)
    d.rejilla.attach(_marco(tv, 120), 1, 0, 1, 1)

    def _ok():
        texto = _texto_de(tv).strip()
        if texto:
            d.close()
            _hacer(padre, lambda: reddit_service.mod_mensaje_eliminacion(cuenta, fullname, texto), _("Mensaje enviado."))
    d.aceptar = _ok
    d.present()


# --------------------------------------------------------------------------- colas
class DialogoColaModReddit(DialogoLista):
    """Las colas de moderación de un subreddit: aprobar, eliminar y el resto de acciones sobre cada elemento."""

    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(padre, _("Moderación de r/{s}").format(s=subreddit), _("Elementos de la cola"),
                         lambda pos: self._acciones(pos))
        self.account_id, self.subreddit, self.items = account_id, subreddit, []
        self.combo = Gtk.DropDown.new_from_strings([_(n) for _c, n in COLAS])
        self.combo.update_property([Gtk.AccessibleProperty.LABEL], [_("Cola de moderación")])
        self.combo.connect("notify::selected", lambda *_a: self._cargar())
        self.fila_botones.prepend(self.combo)
        self.boton(_("_Aprobar"), lambda: self._rapida(0))
        self.boton(_("_Eliminar"), lambda: self._rapida(1))
        self.boton(_("_Más acciones…"), lambda: self._acciones(self.lista.posicion()))
        self.boton(_("A_brir en el navegador"), self._navegador)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        cola = COLAS[self.combo.get_selected()][0]
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar la cola: {e}").format(e=err))
                return
            self.items = res or []
            self._pintar()
        ejecutar(lambda: reddit_service.cargar_cola(self.account_id, self.subreddit, cola), _fin)

    def _pintar(self):
        lineas = []
        for it in self.items:
            tipo = _("Comentario") if it.get("tipo") == "comment" else _("Publicación")
            texto = (it.get("titulo") or it.get("cuerpo") or "").replace("\n", " ").strip()
            lineas.append(_("{t} de u/{a}. {c}. {r} reportes. {f}").format(
                t=tipo, a=it.get("author", ""), c=texto or _("(sin texto)"), r=it.get("num_reports", 0),
                f=fecha(it.get("created_utc"))))
        self.lista.poner(lineas)
        self.mostrar_estado(_("{n} elementos").format(n=len(self.items)))
        self.lista.enfocar()

    def _actual(self, pos=None):
        p = self.lista.posicion() if pos is None else pos
        return self.items[p] if 0 <= p < len(self.items) else None

    def _quitar(self, it):
        if it in self.items:
            self.items.remove(it)
            self._pintar()

    def _rapida(self, i: int):
        it = self._actual()
        if not it:
            return
        _et, fn, aviso, _q = acciones_de_elemento(self.account_id, it["fullname"], it.get("tipo") == "comment")[i]
        _hacer(self, fn, aviso, lambda _r: self._quitar(it))

    def _acciones(self, pos):
        it = self._actual(pos)
        if it:
            moderar_elemento(self, self.account_id, it["fullname"], it.get("tipo") == "comment", lambda: self._quitar(it))

    def _navegador(self):
        it = self._actual()
        if it and it.get("permalink"):
            p = it["permalink"]
            webbrowser.open(REDDIT + p if p.startswith("/") else p)


# --------------------------------------------------------------------------- usuarios
class DialogoUsuariosModReddit(DialogoLista):
    """Baneados, silenciados, contribuidores y moderadores de un subreddit."""

    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(padre, _("Usuarios de r/{s}").format(s=subreddit), _("Lista de usuarios"))
        self.account_id, self.subreddit, self.filas = account_id, subreddit, []
        self.combo = Gtk.DropDown.new_from_strings([_(n) for _c, n in RELACIONES])
        self.combo.update_property([Gtk.AccessibleProperty.LABEL], [_("Relación")])
        self.combo.connect("notify::selected", lambda *_a: self._cargar())
        self.fila_botones.prepend(self.combo)
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Quitar"), self._quitar)
        self.boton(_("_Permisos…"), self._permisos)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _rel(self) -> str:
        return RELACIONES[self.combo.get_selected()][0]

    def _cargar(self):
        rel = self._rel()
        self.mostrar_estado(_("Cargando…"))
        fns = {"banned": reddit_service.listar_baneados, "muted": reddit_service.listar_silenciados,
               "contributor": reddit_service.listar_contribuidores, "moderator": reddit_service.listar_moderadores}

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar la lista: {e}").format(e=err))
                return
            self.filas = res or []
            lineas = []
            for r in self.filas:
                if rel == "moderator":
                    detalle = ", ".join(r.get("permissions", [])) or _("(sin permisos)")
                else:
                    detalle = " ".join(x for x in (r.get("note", ""), fecha(r.get("date"))) if x)
                lineas.append(_("u/{u}. {d}").format(u=r.get("name", ""), d=detalle))
            self.lista.poner(lineas)
            self.mostrar_estado(_("{n} usuarios").format(n=len(self.filas)))
            self.lista.enfocar()
        ejecutar(lambda: fns[rel](self.account_id, self.subreddit), _fin)

    def _usuario(self):
        p = self.lista.posicion()
        return self.filas[p].get("name", "") if 0 <= p < len(self.filas) else ""

    def _anadir(self):
        rel = self._rel()
        if rel == "banned":
            return self._banear()
        if rel == "moderator":
            return self._invitar()
        d = DialogoBase(self, _("Añadir usuario"), _("_Añadir"))
        usuario = d.campo(_("_Usuario (sin u/):"), Gtk.Entry(activates_default=True, width_chars=30))
        d.set_focus(usuario)

        def _ok():
            u = usuario.get_text().strip().lstrip("u/").lstrip("/")
            if not u:
                return
            d.close()
            fn = reddit_service.silenciar if rel == "muted" else reddit_service.contribuidor
            _hacer(self, lambda: fn(self.account_id, self.subreddit, u), _("Usuario añadido."), lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _quitar(self):
        u, rel = self._usuario(), self._rel()
        if not u:
            return
        acciones = {
            "banned": lambda: reddit_service.quitar_ban(self.account_id, self.subreddit, u),
            "muted": lambda: reddit_service.silenciar(self.account_id, self.subreddit, u, quitar=True),
            "contributor": lambda: reddit_service.contribuidor(self.account_id, self.subreddit, u, quitar=True),
            "moderator": lambda: reddit_service.quitar_moderador(self.account_id, self.subreddit, u),
        }
        confirmar_async(self, _("¿Quitar a u/{u}?").format(u=u),
                        lambda: _hacer(self, acciones[rel], _("Usuario quitado."), lambda _r: self._cargar()),
                        etiqueta_si=_("Quitar"))

    def _banear(self):
        d = DialogoBase(self, _("Banear en r/{s}").format(s=self.subreddit), _("_Banear"))
        usuario = d.campo(_("_Usuario (sin u/):"), Gtk.Entry(activates_default=True, width_chars=30))
        motivo = d.campo(_("_Motivo (para los moderadores):"), Gtk.Entry(activates_default=True, max_length=100))
        tv = _vista_texto()
        lbl = Gtk.Label(label=_("Me_nsaje para el usuario (opcional):"), use_underline=True, xalign=0, yalign=0)
        lbl.set_mnemonic_widget(tv)
        d.rejilla.attach(lbl, 0, d._fila, 1, 1)
        d.rejilla.attach(_marco(tv, 90), 1, d._fila, 1, 1)
        d._fila += 1
        dias = d.campo(_("_Duración en días (0 = permanente):"), Gtk.SpinButton.new_with_range(0, 999, 1))
        nota = d.campo(_("Nota _interna (opcional):"), Gtk.Entry(activates_default=True, max_length=100))
        d.set_focus(usuario)

        def _ok():
            u = usuario.get_text().strip().lstrip("u/").lstrip("/")
            if not u:
                notificar(_("Escribe el nombre de usuario."))
                return
            n = int(dias.get_value())
            args = dict(motivo=motivo.get_text().strip(), mensaje=_texto_de(tv).strip(), duracion=n if n > 0 else None,
                        nota=nota.get_text().strip())
            d.close()
            _hacer(self, lambda: reddit_service.banear(self.account_id, self.subreddit, u, **args), _("Usuario baneado."),
                   lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _permisos_dialogo(self, titulo: str, usuario_fijo: str, al_aceptar):
        d = DialogoBase(self, titulo, _("_Aceptar"))
        usuario = None
        if not usuario_fijo:
            usuario = d.campo(_("_Usuario (sin u/):"), Gtk.Entry(activates_default=True, width_chars=30))
        casillas = []
        for p in PERMISOS_MOD:
            c = Gtk.CheckButton(label=p, active=(p == "all"))
            d.rejilla.attach(c, 1, d._fila, 1, 1)
            d._fila += 1
            casillas.append((p, c))
        d.set_focus(usuario or casillas[0][1])

        def _ok():
            u = usuario_fijo or usuario.get_text().strip().lstrip("u/").lstrip("/")
            if not u:
                notificar(_("Escribe el nombre de usuario."))
                return
            marcados = [p for p, c in casillas if c.get_active()]
            d.close()
            al_aceptar(u, ["all"] if "all" in marcados else marcados)
        d.aceptar = _ok
        d.present()

    def _invitar(self):
        self._permisos_dialogo(_("Invitar como moderador"), "", lambda u, p: _hacer(
            self, lambda: reddit_service.invitar_moderador(self.account_id, self.subreddit, u, permisos=p),
            _("Invitación enviada.")))

    def _permisos(self):
        u = self._usuario()
        if not u or self._rel() != "moderator":
            notificar(_("Elige un moderador"))
            return
        self._permisos_dialogo(_("Permisos de u/{u}").format(u=u), u, lambda usr, p: _hacer(
            self, lambda: reddit_service.actualizar_permisos_moderador(self.account_id, self.subreddit, usr, p),
            _("Permisos actualizados."), lambda _r: self._cargar()))


# --------------------------------------------------------------------------- motivos de eliminación
class DialogoMotivosModReddit(DialogoLista):
    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(padre, _("Motivos de eliminación de r/{s}").format(s=subreddit), _("Lista de motivos"))
        self.account_id, self.subreddit, self.motivos = account_id, subreddit, []
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los motivos: {e}").format(e=err))
                return
            self.motivos = res or []
            self.lista.poner([_("{t}. {m}").format(t=m.get("title", ""), m=(m.get("message", "") or "").replace("\n", " "))
                              for m in self.motivos])
            self.mostrar_estado(_("{n} motivos").format(n=len(self.motivos)))
            self.lista.enfocar()
        ejecutar(lambda: reddit_service.listar_motivos(self.account_id, self.subreddit), _fin)

    def _anadir(self):
        d = DialogoBase(self, _("Nuevo motivo"), _("_Añadir"))
        titulo = d.campo(_("_Título:"), Gtk.Entry(activates_default=True, width_chars=40, max_length=100))
        tv = _vista_texto()
        lbl = Gtk.Label(label=_("_Mensaje:"), use_underline=True, xalign=0, yalign=0)
        lbl.set_mnemonic_widget(tv)
        d.rejilla.attach(lbl, 0, d._fila, 1, 1)
        d.rejilla.attach(_marco(tv, 120), 1, d._fila, 1, 1)
        d.set_focus(titulo)

        def _ok():
            t = titulo.get_text().strip()
            if not t:
                return
            m = _texto_de(tv).strip()
            d.close()
            _hacer(self, lambda: reddit_service.añadir_motivo(self.account_id, self.subreddit, t, m), _("Motivo añadido."),
                   lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _borrar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.motivos):
            return
        m = self.motivos[p]
        confirmar_async(self, _("¿Borrar el motivo «{t}»?").format(t=m.get("title", "")),
                        lambda: _hacer(self, lambda: reddit_service.borrar_motivo(self.account_id, self.subreddit, m.get("id", "")),
                                       _("Motivo borrado."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- configuración
class DialogoConfigSubreddit(Gtk.Window):
    """Los ajustes más comunes de un subreddit (título, descripciones, acceso y +18); el resto, en solo lectura."""

    def __init__(self, padre, account_id: int, subreddit: str):
        super().__init__(title=_("Configuración de r/{s}").format(s=subreddit), transient_for=padre, modal=True)
        from .mnemonicos import vigilar
        vigilar(self)
        self.padre, self.account_id, self.subreddit = padre, account_id, subreddit
        self.ajustes, self.campos = {}, {}
        self.set_default_size(680, 680)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(Gtk.ScrolledWindow(child=caja))
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        caja.append(self.estado)
        for clave, etq in CONFIG_TEXTO:
            multi = clave in ("description", "submit_text")
            lbl = Gtk.Label(label=_(etq) + ":", xalign=0)
            caja.append(lbl)
            if multi:
                w = _vista_texto()
                caja.append(_marco(w, 90))
            else:
                w = Gtk.Entry(hexpand=True)
                caja.append(w)
            w.update_property([Gtk.AccessibleProperty.LABEL], [_(etq)])
            self.campos[clave] = w
        self.tipo = Gtk.DropDown.new_from_strings([_(n) for _c, n in TIPOS_ACCESO])
        self.tipo.update_property([Gtk.AccessibleProperty.LABEL], [_("Tipo de acceso")])
        caja.append(Gtk.Label(label=_("Tipo de acceso:"), xalign=0))
        caja.append(self.tipo)
        self.nsfw = Gtk.CheckButton(label=_("Comunidad +18 (NSFW)"))
        caja.append(self.nsfw)
        caja.append(Gtk.Label(label=_("Otros ajustes (solo lectura):"), xalign=0))
        self.otros = _vista_texto(False)
        self.otros.update_property([Gtk.AccessibleProperty.LABEL], [_("Otros ajustes")])
        caja.append(_marco(self.otros, 120))
        botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        caja.append(botones)
        g = Gtk.Button(label=_("_Guardar"), use_underline=True)
        g.add_css_class("suggested-action")
        g.connect("clicked", lambda *_a: self._guardar())
        c = Gtk.Button(label=_("_Cancelar"), use_underline=True)
        c.connect("clicked", lambda *_a: self.close())
        botones.append(g)
        botones.append(c)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.estado.set_text(_("Cargando la configuración…"))
        ejecutar(lambda: reddit_service.obtener_config_subreddit(account_id, subreddit), self._cargado)

    def _cargado(self, res, err):
        if err:
            self.estado.set_text(_("No se pudo cargar la configuración: {e}").format(e=err))
            return
        self.ajustes = res or {}
        for clave, w in self.campos.items():
            valor = str(self.ajustes.get(clave, "") or "")
            w.get_buffer().set_text(valor) if isinstance(w, Gtk.TextView) else w.set_text(valor)
        tipo = self.ajustes.get("subreddit_type", "public")
        self.tipo.set_selected(next((i for i, (c, _n) in enumerate(TIPOS_ACCESO) if c == tipo), 0))
        self.nsfw.set_active(bool(self.ajustes.get("over_18", False)))
        editables = {c for c, _e in CONFIG_TEXTO} | {"subreddit_type", "over_18"}
        self.otros.get_buffer().set_text("\n".join(
            f"{k}: {v}" for k, v in sorted(self.ajustes.items())
            if k not in editables and isinstance(v, (str, int, float, bool)) and str(v) != ""))
        self.estado.set_text(_("Configuración cargada."))

    def _guardar(self):
        ajustes = {c: (_texto_de(w) if isinstance(w, Gtk.TextView) else w.get_text()) for c, w in self.campos.items()}
        ajustes["subreddit_type"] = TIPOS_ACCESO[self.tipo.get_selected()][0]
        ajustes["over_18"] = self.nsfw.get_active()
        self.close()
        _hacer(self.padre, lambda: reddit_service.actualizar_config_subreddit(self.account_id, self.subreddit, **ajustes),
               _("Configuración guardada."))


# --------------------------------------------------------------------------- registro
def ver_registro(padre, account_id: int, subreddit: str):
    def _fin(res, err):
        if err:
            notificar(_("No se pudo cargar el registro: {e}").format(e=err))
            return
        d = DialogoLista(padre, _("Registro de r/{s}").format(s=subreddit), _("Acciones de moderación"))
        d.lista.poner([_("{f}, u/{m}: {a} {d}").format(f=fecha(a.get("created_utc")), m=a.get("mod", ""),
                                                        a=a.get("action", ""), d=a.get("details") or a.get("description") or "").strip()
                       for a in res or []])
        d.mostrar_estado(_("{n} acciones").format(n=len(res or [])) if res else _("No hay acciones recientes de moderación."))
        d.cerrar_boton()
        d.present()
    ejecutar(lambda: reddit_service.modlog(account_id, subreddit, limit=100), _fin)
