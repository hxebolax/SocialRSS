# -*- coding: utf-8 -*-
"""Telegram: tu perfil, contactos, bloqueados, sesiones, privacidad, verificación en dos pasos, historias, boosts y bots propios.
Los mismos servicios que usa la versión de Windows (telegram_perfil.py y telegram_bots.py)."""


import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.telegram.telegram_service import telegram_service

from .datos import fecha_legible
from .dialogos import DialogoBase, DialogoTexto, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube_instancia import texto_en_dialogo
from .dialogos_telegram_admin import _hacer, _pedir

PRIVACIDAD = [("last_seen", "Última conexión"), ("profile_photo", "Foto de perfil"), ("phone_number", "Número de teléfono"),
              ("forwards", "Reenvío de mensajes"), ("calls", "Llamadas"), ("voice_messages", "Mensajes de voz"),
              ("chat_invite", "Invitaciones a grupos"), ("bio", "Biografía"), ("birthday", "Cumpleaños")]
MODOS = [("todos", "Todos"), ("contactos", "Mis contactos"), ("nadie", "Nadie")]


def _archivo(padre, titulo: str, al_elegir):
    dlg = Gtk.FileDialog(title=titulo)

    def _fin(origen, res):
        try:
            f = origen.open_finish(res)
        except Exception:
            return
        al_elegir(f.get_path())
    dlg.open(padre, None, _fin)


# --------------------------------------------------------------------------- perfil
class DialogoMiPerfilTG(DialogoBase):
    """Nombre, apellido, biografía y nombre público; además la foto y el cumpleaños."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mi perfil de Telegram"), _("_Guardar"))
        self.account_id = account_id
        self.nombre = self.campo(_("_Nombre:"), Gtk.Entry(activates_default=True, width_chars=36))
        self.apellido = self.campo(_("_Apellido:"), Gtk.Entry(activates_default=True))
        self.bio = self.campo(_("_Biografía:"), Gtk.Entry(activates_default=True, max_length=140))
        self.usuario = self.campo(_("Nombre de _usuario (sin @):"), Gtk.Entry(activates_default=True))
        fila = Gtk.Box(spacing=8)
        for et, fn in ((_("Cambiar la _foto…"), self._foto), (_("_Quitar la foto"), self._quitar_foto),
                       (_("_Cumpleaños…"), self._cumple), (_("Quitar el _estado emoji"), self._quitar_estado)):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, f=fn: f())
            fila.append(b)
        self.rejilla.attach(fila, 1, self._fila, 1, 1)
        self._fila += 1
        self.boton_aceptar.set_sensitive(False)
        self.set_focus(self.nombre)

        def _fin(p, err):
            if err or not p:
                return notificar(_("No se pudo leer el perfil: {e}").format(e=err or ""))
            self.nombre.set_text(p["first_name"])
            self.apellido.set_text(p["last_name"])
            self.usuario.set_text(p["username"])
            self.boton_aceptar.set_sensitive(True)
            self.original_usuario = p["username"]
        ejecutar(lambda: telegram_service.obtener_mi_perfil(account_id), _fin)

    def aceptar(self):
        n, a, b, u = (self.nombre.get_text().strip(), self.apellido.get_text().strip(), self.bio.get_text().strip(),
                      self.usuario.get_text().strip().lstrip("@"))
        cambia_usuario = u != getattr(self, "original_usuario", u)
        self.close()

        def _todo():
            telegram_service.editar_perfil(self.account_id, n, a, b)
            if cambia_usuario:
                telegram_service.cambiar_username(self.account_id, u)
            return True
        _hacer(self.padre, _todo, _("Perfil guardado."))

    def _foto(self):
        _archivo(self, _("Elegir la foto de perfil"), lambda r: _hacer(
            self, lambda: telegram_service.establecer_foto_perfil(self.account_id, r), _("Foto cambiada.")))

    def _quitar_foto(self):
        _hacer(self, lambda: telegram_service.borrar_foto_perfil(self.account_id), _("Foto quitada."))

    def _quitar_estado(self):
        _hacer(self, lambda: telegram_service.quitar_estado_emoji(self.account_id), _("Estado emoji quitado."))

    def _cumple(self):
        d = DialogoBase(self, _("Cumpleaños"), _("_Guardar"))
        dia = d.campo(_("_Día:"), Gtk.SpinButton.new_with_range(1, 31, 1))
        mes = d.campo(_("_Mes:"), Gtk.SpinButton.new_with_range(1, 12, 1))
        anio = d.campo(_("_Año (0 = no indicarlo):"), Gtk.SpinButton.new_with_range(0, 2100, 1))
        d.set_focus(dia)

        def _ok():
            a, m, y = int(dia.get_value()), int(mes.get_value()), int(anio.get_value()) or None
            d.close()
            _hacer(self, lambda: telegram_service.actualizar_cumpleanos(self.account_id, a, m, y), _("Cumpleaños guardado."))
        d.aceptar = _ok
        d.present()


# --------------------------------------------------------------------------- contactos y bloqueados
class DialogoContactosTG(DialogoLista):
    """Tu agenda: abrir el chat, añadir, borrar, anotar y buscar usuarios, grupos y canales públicos."""

    def __init__(self, padre, account_id: int, abrir_chat):
        super().__init__(padre, _("Contactos de Telegram"), _("Lista de contactos"), lambda pos: self._abrir(pos))
        self.account_id, self.abrir_chat, self.items = account_id, abrir_chat, []
        self.boton(_("_Abrir el chat"), lambda: self._abrir(self.lista.posicion()))
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Borrar"), self._borrar)
        self.boton(_("_Nota…"), self._nota)
        self.boton(_("_Buscar…"), self._buscar)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los contactos…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los contactos: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{c['nombre']} {c['username']} {c['telefono']}".strip() for c in self.items])
            self.mostrar_estado(_("{n} contactos").format(n=len(self.items)))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.obtener_contactos(self.account_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir(self, pos):
        if 0 <= pos < len(self.items):
            c = self.items[pos]
            self.close()
            self.abrir_chat(self.account_id, c["id"], c["nombre"])

    def _anadir(self):
        d = DialogoBase(self, _("Añadir un contacto"), _("_Añadir"))
        ident = d.campo(_("_Usuario (@usuario, número o teléfono):"), Gtk.Entry(activates_default=True, width_chars=30))
        nombre = d.campo(_("_Nombre:"), Gtk.Entry(activates_default=True))
        apellido = d.campo(_("_Apellido (opcional):"), Gtk.Entry(activates_default=True))
        d.set_focus(ident)

        def _ok():
            i, n, a = ident.get_text().strip(), nombre.get_text().strip(), apellido.get_text().strip()
            if not (i and n):
                return notificar(_("Escribe el usuario y el nombre."))
            d.close()
            _hacer(self, lambda: telegram_service.añadir_contacto(self.account_id, i, n, a), _("Contacto añadido."),
                   lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()

    def _borrar(self):
        c = self._actual()
        if c:
            confirmar_async(self, _("¿Borrar a {n} de tus contactos?").format(n=c["nombre"]),
                            lambda: _hacer(self, lambda: telegram_service.borrar_contacto(self.account_id, [c["id"]]),
                                           _("Contacto borrado."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))

    def _nota(self):
        c = self._actual()
        if c:
            _pedir(self, _("Nota privada"), _("_Nota (vacía = borrarla):"), lambda t: _hacer(
                self, lambda: telegram_service.nota_contacto(self.account_id, c["id"], t), _("Nota guardada.")))

    def _buscar(self):
        def _con(texto):
            def _fin(res, err):
                if err:
                    return notificar(_("No se pudo buscar: {e}").format(e=err))
                from .dialogos_telegram_chats import DialogoResultadosTG
                filas = [{"linea": f"{r['nombre']} {r['username']} ({r['tipo']})", "id": r["id"], "nombre": r["nombre"],
                          "texto": r["username"]} for r in res or []]
                DialogoResultadosTG(self, _("Resultados de «{t}»").format(t=texto), filas,
                                    lambda r: self.abrir_chat(self.account_id, r["id"], r["nombre"])).present()
            ejecutar(lambda: telegram_service.buscar_contactos(self.account_id, texto), _fin)
        DialogoTexto(self, _("Buscar usuarios, grupos y canales"), _("_Buscar:"), _con, aceptar=_("_Buscar")).present()


class DialogoBloqueadosTG(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Usuarios bloqueados"), _("Lista de usuarios bloqueados"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Desbloquear"), self._desbloquear)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{b['nombre']} {b['username']}".strip() for b in self.items])
            self.mostrar_estado(_("{n} bloqueados").format(n=len(self.items)) if self.items else _("No has bloqueado a nadie."))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.listar_bloqueados(self.account_id), _fin)

    def _desbloquear(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            b = self.items[p]
            _hacer(self, lambda: telegram_service.desbloquear_usuario(self.account_id, b["id"]), _("Usuario desbloqueado."),
                   lambda _r: self._cargar())


# --------------------------------------------------------------------------- sesiones y seguridad
class DialogoSesionesTG(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Sesiones activas"), _("Lista de sesiones"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Cerrar esta sesión"), self._cerrar)
        self.boton(_("Cerrar _todas las demás"), self._todas)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{s['device']} ({s['platform']}), {s['app']}, {s['ip']}" + (". " + _("Sesión actual") if s["current"] else "")
                              for s in self.items])
            self.mostrar_estado(_("{n} sesiones").format(n=len(self.items)))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.listar_sesiones(self.account_id), _fin)

    def _cerrar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.items):
            return
        s = self.items[p]
        if s["current"]:
            return notificar(_("Esa es la sesión actual."))
        _hacer(self, lambda: telegram_service.cerrar_sesion_concreta(self.account_id, s["id"]), _("Sesión cerrada."),
               lambda _r: self._cargar())

    def _todas(self):
        confirmar_async(self, _("¿Cerrar todas las demás sesiones? Los otros dispositivos tendrán que volver a entrar."),
                        lambda: _hacer(self, lambda: telegram_service.cerrar_otras_sesiones(self.account_id),
                                       _("Sesiones cerradas."), lambda _r: self._cargar()), etiqueta_si=_("Cerrar"))


class DialogoPrivacidadTG(DialogoBase):
    """Quién puede ver cada dato tuyo (todos, tus contactos o nadie)."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Privacidad de Telegram"), _("_Guardar"))
        self.account_id = account_id
        self.combos = {}
        for clave, nombre in PRIVACIDAD:
            c = self.campo(_("{n}:").format(n=_(nombre)), Gtk.DropDown.new_from_strings([_(n) for _m, n in MODOS]))
            c.set_selected(1)
            self.combos[clave] = c
        self.set_focus(self.combos["last_seen"])

    def aceptar(self):
        valores = {k: MODOS[c.get_selected()][0] for k, c in self.combos.items()}
        self.close()

        def _todo():
            for k, m in valores.items():
                telegram_service.fijar_privacidad(self.account_id, k, m)
            return True
        _hacer(self.padre, _todo, _("Privacidad guardada."))


class DialogoSeguridadTG(DialogoBase):
    """Verificación en dos pasos, cierre automático de la cuenta y de las sesiones."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Seguridad de la cuenta"), _("_Cerrar"))
        self.account_id = account_id
        self.boton_cancelar.set_visible(False)
        fila = Gtk.Box(spacing=8, orientation=Gtk.Orientation.VERTICAL)
        for et, fn in ((_("_Activar la verificación en dos pasos…"), self._activar), (_("_Cambiar la contraseña…"), self._cambiar),
                       (_("_Quitar la verificación en dos pasos…"), self._quitar),
                       (_("Autodestrucción de la _cuenta por inactividad…"), self._ttl_cuenta),
                       (_("Cierre de las _sesiones inactivas…"), self._ttl_sesiones)):
            b = Gtk.Button(label=et, use_underline=True, halign=Gtk.Align.START)
            b.connect("clicked", lambda *_a, f=fn: f())
            fila.append(b)
        self.rejilla.attach(fila, 0, 0, 1, 1)

    def aceptar(self):
        self.close()

    def _activar(self):
        d = DialogoBase(self, _("Activar la verificación en dos pasos"), _("_Activar"))
        p1 = d.campo(_("_Contraseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        pista = d.campo(_("_Pista (opcional):"), Gtk.Entry(activates_default=True))
        correo = d.campo(_("Correo de _recuperación (opcional):"), Gtk.Entry(activates_default=True))
        d.set_focus(p1)

        def _ok():
            p = p1.get_text()
            if not p:
                return
            args = (pista.get_text().strip() or None, correo.get_text().strip() or None)
            d.close()
            _hacer(self, lambda: telegram_service.activar_2fa(self.account_id, p, *args), _("Verificación en dos pasos activada."))
        d.aceptar = _ok
        d.present()

    def _cambiar(self):
        d = DialogoBase(self, _("Cambiar la contraseña"), _("_Cambiar"))
        actual = d.campo(_("Contraseña _actual:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        nueva = d.campo(_("Contraseña _nueva:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        pista = d.campo(_("_Pista (opcional):"), Gtk.Entry(activates_default=True))
        d.set_focus(actual)

        def _ok():
            a, n, h = actual.get_text(), nueva.get_text(), pista.get_text().strip() or None
            if not (a and n):
                return
            d.close()
            _hacer(self, lambda: telegram_service.cambiar_2fa(self.account_id, a, n, h), _("Contraseña cambiada."))
        d.aceptar = _ok
        d.present()

    def _quitar(self):
        d = DialogoBase(self, _("Quitar la verificación en dos pasos"), _("_Quitar"))
        p = d.campo(_("_Contraseña actual:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        d.set_focus(p)

        def _ok():
            valor = p.get_text()
            if valor:
                d.close()
                _hacer(self, lambda: telegram_service.quitar_2fa(self.account_id, valor), _("Verificación en dos pasos quitada."))
        d.aceptar = _ok
        d.present()

    def _ttl(self, titulo: str, etiqueta: str, leer, guardar):
        def _con(actual, err):
            d = DialogoBase(self, titulo, _("_Guardar"))
            dias = d.campo(etiqueta, Gtk.SpinButton.new_with_range(1, 730, 1))
            if isinstance(actual, int) and actual:
                dias.set_value(actual)
            d.set_focus(dias)

            def _ok():
                n = int(dias.get_value())
                d.close()
                _hacer(self, lambda: guardar(self.account_id, n), _("Guardado."))
            d.aceptar = _ok
            d.present()
        if leer:
            ejecutar(lambda: leer(self.account_id), _con)
        else:
            _con(None, None)

    def _ttl_cuenta(self):
        self._ttl(_("Autodestrucción de la cuenta"), _("_Días de inactividad antes de borrar la cuenta:"),
                  telegram_service.obtener_ttl_cuenta, telegram_service.establecer_ttl_cuenta)

    def _ttl_sesiones(self):
        self._ttl(_("Cierre de sesiones inactivas"), _("_Días de inactividad antes de cerrar una sesión:"), None,
                  telegram_service.establecer_ttl_sesiones)


# --------------------------------------------------------------------------- historias
class DialogoHistoriasTG(DialogoLista):
    """Las historias activas de tus contactos (descargar la que elijas) y las tuyas."""

    def __init__(self, padre, account_id: int, propias: bool = False, archivadas: bool = False):
        super().__init__(padre, _("Historias archivadas") if archivadas else _("Mis historias") if propias
                         else _("Historias de tus contactos"), _("Lista de historias"))
        self.account_id, self.propias, self.archivadas, self.items = account_id, propias or archivadas, archivadas, []
        if propias and not archivadas:
            self.boton(_("_Publicar una…"), self._publicar)
            self.boton(_("_Quién la vio…"), self._quien)
            self.boton(_("_Editar el texto…"), self._editar)
            self.boton(_("_Privacidad…"), self._privacidad)
            self.boton(_("_Fijar en el perfil"), lambda: self._fijar(True))
            self.boton(_("_Desfijar"), lambda: self._fijar(False))
            self.boton(_("_Borrar"), self._borrar)
        elif archivadas:
            self.boton(_("_Quién la vio…"), self._quien)
        else:
            self.boton(_("_Marcar como vista"), self._vista)
            self.boton(_("_Descargar"), self._descargar)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las historias: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{h.get('autor') or _('Yo')}: {h.get('caption') or _('sin texto')} ({h.get('tipo') or '?'}) "
                              f"— {fecha_legible(h.get('fecha', ''))}" for h in self.items])
            self.mostrar_estado(_("{n} historias").format(n=len(self.items)) if self.items else _("No hay historias."))
            self.lista.enfocar()
        ejecutar(lambda: (telegram_service.mis_historias_archivadas if self.archivadas else telegram_service.mis_historias
                          if self.propias else telegram_service.obtener_historias)(self.account_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _vista(self):
        h = self._actual()
        if h:
            _hacer(self, lambda: telegram_service.marcar_historia_vista(self.account_id, h["chat_id"], h["story_id"]),
                   _("Historia marcada como vista."))

    def _descargar(self):
        h = self._actual()
        if h:
            _hacer(self, lambda: telegram_service.descargar_historia(self.account_id, h["chat_id"], h["story_id"]),
                   _("Historia descargada."))

    def _publicar(self):
        def _con(ruta):
            _pedir(self, _("Publicar una historia"), _("_Texto (opcional):"), lambda t: _hacer(
                self, lambda: telegram_service.publicar_historia(self.account_id, ruta, t), _("Historia publicada."),
                lambda _r: self._cargar()), _("_Publicar"))
        _archivo(self, _("Elegir la foto o el vídeo"), _con)

    def _quien(self):
        h = self._actual()
        if h:
            def _fin(res, err):
                texto_en_dialogo(self, _("Quién vio la historia"), "\n".join(res or []) or (_("Nadie todavía.") if not err else str(err)))
            ejecutar(lambda: telegram_service.quien_vio_historia(self.account_id, h["story_id"]), _fin)

    def _editar(self):
        h = self._actual()
        if h:
            _pedir(self, _("Editar el texto de la historia"), _("_Nuevo texto:"), lambda t: _hacer(
                self, lambda: telegram_service.editar_pie_historia(self.account_id, h["story_id"], t), _("Texto editado."),
                lambda _r: self._cargar()), _("_Guardar"), h.get("caption", ""))

    def _privacidad(self):
        h = self._actual()
        if h:
            opciones = [("PUBLIC", _("Todos")), ("CONTACTS", _("Mis contactos")), ("CLOSE_FRIENDS", _("Mejores amigos"))]
            from .dialogos import DialogoElegir
            DialogoElegir(self, _("Privacidad de la historia"), _("_Quién la ve:"), [n for _c, n in opciones], lambda i: _hacer(
                self, lambda: telegram_service.editar_privacidad_historia(self.account_id, h["story_id"], opciones[i][0]),
                _("Privacidad cambiada.")), _("_Guardar")).present()

    def _fijar(self, fijar: bool):
        h = self._actual()
        if h:
            _hacer(self, lambda: telegram_service.fijar_historia(self.account_id, h["story_id"], fijar),
                   _("Historia fijada en tu perfil.") if fijar else _("Historia desfijada."))

    def _borrar(self):
        h = self._actual()
        if h:
            confirmar_async(self, _("¿Borrar esta historia?"),
                            lambda: _hacer(self, lambda: telegram_service.borrar_historia(self.account_id, h["story_id"]),
                                           _("Historia borrada."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- boosts y bots propios
def estado_boosts(padre, account_id: int, chat_id: int, titulo: str):
    def _fin(st, err):
        if err or not st:
            return notificar(_("No se pudo consultar: {e}").format(e=err or ""))
        texto = "\n".join([_("Nivel: {n}").format(n=st["nivel"]), _("Boosts: {n}").format(n=st["boosts"]),
                           _("Boosts para el siguiente nivel: {n}").format(n=st["siguiente_nivel"]),
                           _("Ya has potenciado este canal.") if st["mi_boost"] else _("Todavía no has potenciado este canal.")])
        texto_en_dialogo(padre, _("Potenciación de {t}").format(t=titulo), texto)
    ejecutar(lambda: telegram_service.estado_boosts(account_id, chat_id), _fin)


def potenciar_canal(padre, account_id: int, chat_id: int, titulo: str):
    _hacer(padre, lambda: telegram_service.potenciar_canal(account_id, chat_id), _("Canal potenciado."))


class DialogoMisBotsTG(DialogoLista):
    """Los bots que posees: cambiar su nombre y sus descripciones, o crear uno nuevo con BotFather."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mis bots"), _("Lista de bots"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Nombre…"), lambda: self._editar("nombre"))
        self.boton(_("_Descripción…"), lambda: self._editar("descripcion"))
        self.boton(_("Descripción _corta…"), lambda: self._editar("corta"))
        self.boton(_("_Crear un bot…"), self._crear)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los bots: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{n} ({u})" for _i, n, u in self.items])
            self.mostrar_estado(_("{n} bots").format(n=len(self.items)) if self.items else _("No tienes bots."))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.mis_bots(self.account_id), _fin)

    def _editar(self, que: str):
        p = self.lista.posicion()
        if not 0 <= p < len(self.items):
            return
        bot_id = self.items[p][0]
        S = telegram_service
        fn = {"nombre": S.fijar_nombre_bot, "descripcion": S.fijar_descripcion_bot, "corta": S.fijar_descripcion_corta_bot}[que]
        etq = {"nombre": _("_Nombre:"), "descripcion": _("_Descripción:"), "corta": _("Descripción _corta:")}[que]
        _pedir(self, _("Editar el bot"), etq, lambda t: t and _hacer(self, lambda: fn(self.account_id, bot_id, t), _("Bot actualizado.")))

    def _crear(self):
        d = DialogoBase(self, _("Crear un bot"), _("_Crear"))
        nombre = d.campo(_("_Nombre:"), Gtk.Entry(activates_default=True, width_chars=30))
        usuario = d.campo(_("Nombre de _usuario (debe acabar en bot):"), Gtk.Entry(activates_default=True))
        d.set_focus(nombre)

        def _ok():
            n, u = nombre.get_text().strip(), usuario.get_text().strip().lstrip("@")
            if not (n and u.lower().endswith("bot")):
                return notificar(_("Escribe el nombre y un usuario que acabe en bot."))
            d.close()
            _hacer(self, lambda: telegram_service.crear_bot(self.account_id, n, u), _("Bot creado."), lambda _r: self._cargar())
        d.aceptar = _ok
        d.present()


