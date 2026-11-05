# -*- coding: utf-8 -*-
"""Telegram: administrar un grupo o canal (miembros, enlaces de invitación, solicitudes de unión, ajustes, temas de foro,
comentarios y registro de administración). Los mismos servicios que usa la versión de Windows (telegram_admin.py)."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import copiar_al_portapapeles, notificar
from app.services.telegram.telegram_service import telegram_service

from .datos import fecha_legible
from .dialogos import DialogoBase, DialogoElegir, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube_instancia import texto_en_dialogo

ROLES = {"owner": "Dueño", "administrator": "Administrador", "member": "Miembro", "restricted": "Restringido",
         "banned": "Baneado", "left": "Se fue"}


def _hacer(padre, fn, ok: str, tras=None):
    def _fin(res, err):
        if err:
            notificar(_("No se pudo: {e}").format(e=err))
            return
        notificar(ok)
        if tras:
            tras(res)
    ejecutar(fn, _fin)


def _pedir(padre, titulo: str, etiqueta: str, al_aceptar, aceptar: str = None, valor: str = ""):
    d = DialogoBase(padre, titulo, aceptar)
    e = d.campo(etiqueta, Gtk.Entry(text=valor, activates_default=True, width_chars=44))
    d.set_focus(e)

    def _ok():
        d.close()
        al_aceptar(e.get_text().strip())
    d.aceptar = _ok
    d.present()


def _numero(texto: str):
    texto = (texto or "").strip()
    return int(texto) if texto.lstrip("-").isdigit() else None


# --------------------------------------------------------------------------- miembros
class DialogoMiembrosTG(DialogoLista):
    """Miembros del chat y lo que un administrador puede hacer con cada uno."""

    def __init__(self, padre, account_id: int, chat_id: int, titulo: str):
        super().__init__(padre, _("Miembros de {t}").format(t=titulo), _("Lista de miembros"))
        self.account_id, self.chat_id, self.items = account_id, chat_id, []
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Acciones…"), self._acciones)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los miembros…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los miembros: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{m['nombre']} ({m['username']}). {_(ROLES.get(m['rol'], m['rol']))}. {m['presencia']}"
                              + (f". {m['titulo']}" if m.get("titulo") else "") + (". " + _("Bot") if m.get("bot") else "")
                              for m in self.items])
            self.mostrar_estado(_("{n} miembros").format(n=len(self.items)))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.obtener_miembros(self.account_id, self.chat_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _anadir(self):
        def _con(texto):
            ids = [c.strip() for c in texto.replace(";", ",").split(",") if c.strip()]
            if ids:
                _hacer(self, lambda: telegram_service.añadir_miembros(self.account_id, self.chat_id, ids),
                       _("Miembros añadidos."), lambda _r: self._cargar())
        _pedir(self, _("Añadir al chat"), _("_Usuarios (@usuario o número), separados por comas:"), _con, _("_Añadir"))

    def _acciones(self):
        m = self._actual()
        if not m:
            return
        rol, uid = (m.get("rol") or "").lower(), m["id"]
        if rol == "owner":
            return notificar(_("Al dueño no se le puede promover, degradar ni banear."))
        opciones = []
        if rol == "administrator":
            opciones += [(_("Quitar administrador (degradar a miembro)"), lambda: self._simple("degradar", uid, _("Administrador quitado."))),
                         (_("Cambiar sus permisos de administrador…"), lambda: self._permisos(uid)),
                         (_("Título de administrador…"), lambda: self._titulo(uid))]
        else:
            opciones += [(_("Promover a administrador"), lambda: self._simple("promover", uid, _("Promovido a administrador."))),
                         (_("Promover con permisos concretos…"), lambda: self._permisos(uid))]
        if rol == "banned":
            opciones.append((_("Quitar el baneo (volver a admitir)"), lambda: self._simple("desbanear", uid, _("Baneo quitado."))))
        else:
            if rol == "restricted":
                opciones.append((_("Quitar restricciones (podrá volver a escribir)"),
                                 lambda: self._simple("quitar_restriccion", uid, _("Restricciones quitadas."))))
            else:
                opciones.append((_("Silenciar (no podrá escribir)"), lambda: self._simple("silenciar", uid, _("Silenciado."))))
            opciones.append((_("Banear (expulsar del grupo)"), lambda: self._confirmar("banear", uid, m)))
        opciones.append((_("Borrar todos sus mensajes (limpiar spam)"), lambda: self._confirmar("borrar_mensajes", uid, m)))
        DialogoElegir(self, _("Acciones sobre {n}").format(n=m["nombre"]), _("_Acción:"), [o[0] for o in opciones],
                      lambda i: opciones[i][1](), _("_Aplicar")).present()

    def _simple(self, accion: str, uid, ok: str):
        S, a, c = telegram_service, self.account_id, self.chat_id
        fn = {"degradar": lambda: S.degradar_admin(a, c, uid), "promover": lambda: S.promover_admin(a, c, uid),
              "desbanear": lambda: S.desbanear_miembro(a, c, uid), "silenciar": lambda: S.silenciar_miembro(a, c, uid),
              "quitar_restriccion": lambda: S.quitar_restriccion_miembro(a, c, uid), "banear": lambda: S.banear_miembro(a, c, uid),
              "borrar_mensajes": lambda: S.borrar_mensajes_de_usuario(a, c, uid)}[accion]
        _hacer(self, fn, ok, lambda _r: self._cargar())

    def _confirmar(self, accion: str, uid, m):
        texto = (_("¿Banear a {n}? Se le expulsa del grupo.") if accion == "banear"
                 else _("¿Borrar todos los mensajes de {n}?")).format(n=m["nombre"])
        confirmar_async(self, texto, lambda: self._simple(accion, uid, _("Hecho.")), etiqueta_si=_("Continuar"))

    def _titulo(self, uid):
        _pedir(self, _("Título de administrador"), _("_Título (por ejemplo Moderador):"),
               lambda t: _hacer(self, lambda: telegram_service.titulo_administrador(self.account_id, self.chat_id, uid, t),
                                _("Título cambiado."), lambda _r: self._cargar()))

    def _permisos(self, uid):
        def _con(actuales, err):
            d = DialogoBase(self, _("Permisos de administrador"), _("_Guardar"))
            casillas = {}
            for campo, etq in telegram_service.PRIVILEGIOS_ADMIN:
                c = Gtk.CheckButton(label=_(etq), active=bool((actuales or {}).get(campo)))
                d.rejilla.attach(c, 1, d._fila, 1, 1)
                d._fila += 1
                casillas[campo] = c
            d.set_focus(next(iter(casillas.values())))

            def _ok():
                priv = {k: c.get_active() for k, c in casillas.items()}
                d.close()
                _hacer(self, lambda: telegram_service.promover_admin(self.account_id, self.chat_id, uid, priv),
                       _("Permisos guardados."), lambda _r: self._cargar())
            d.aceptar = _ok
            d.present()
        ejecutar(lambda: telegram_service.obtener_privilegios_admin(self.account_id, self.chat_id, uid), _con)


# --------------------------------------------------------------------------- enlaces de invitación
class DialogoEnlacesTG(DialogoLista):
    def __init__(self, padre, account_id: int, chat_id: int, titulo: str):
        super().__init__(padre, _("Enlaces de invitación de {t}").format(t=titulo), _("Enlaces de invitación"))
        self.account_id, self.chat_id, self.items = account_id, chat_id, []
        self.boton(_("_Crear uno nuevo…"), self._crear)
        self.boton(_("_Copiar"), self._copiar)
        self.boton(_("_Quién se unió…"), self._quien)
        self.boton(_("_Revocar"), self._revocar)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los enlaces…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los enlaces: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{e['enlace']}. {e['nombre'] or _('sin nombre')}. " + (_("Revocado. ") if e["revocado"] else "")
                              + _("{n} miembros").format(n=e["miembros"])
                              + (". " + _("{n} solicitudes pendientes").format(n=e["solicitudes"]) if e["solicitudes"] else "")
                              for e in self.items])
            self.mostrar_estado(_("{n} enlaces").format(n=len(self.items)) if self.items else _("No hay enlaces"))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.listar_enlaces_invitacion(self.account_id, self.chat_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _copiar(self):
        e = self._actual()
        if e:
            copiar_al_portapapeles(e["enlace"], _("Enlace copiado"))

    def _crear(self):
        d = DialogoBase(self, _("Nuevo enlace de invitación"), _("_Crear"))
        nombre = d.campo(_("_Nombre (opcional):"), Gtk.Entry(activates_default=True, width_chars=30))
        dias = d.campo(_("_Caduca en días (0 = no caduca):"), Gtk.SpinButton.new_with_range(0, 365, 1))
        limite = d.campo(_("_Límite de miembros (0 = sin límite):"), Gtk.SpinButton.new_with_range(0, 99999, 1))
        aprob = d.campo(_("Exigir la _aprobación de un administrador:"), Gtk.Switch(halign=Gtk.Align.START))
        d.set_focus(nombre)

        def _ok():
            args = dict(nombre=nombre.get_text().strip() or None, caduca_en_segundos=int(dias.get_value()) * 86400 or None,
                        limite_miembros=int(limite.get_value()) or None, con_aprobacion=aprob.get_active() or None)
            d.close()

            def _fin(enlace, err):
                if err:
                    notificar(_("No se pudo crear el enlace: {e}").format(e=err))
                    return
                copiar_al_portapapeles(enlace, _("Enlace creado y copiado"))
                self._cargar()
            ejecutar(lambda: telegram_service.crear_enlace_invitacion(self.account_id, self.chat_id, **args), _fin)
        d.aceptar = _ok
        d.present()

    def _quien(self):
        e = self._actual()
        if not e:
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            texto_en_dialogo(self, _("Quién se unió por este enlace"),
                             "\n".join(f"{n} {u} — {fecha_legible(f)}" for n, u, f in res or []) or _("Nadie todavía."))
        ejecutar(lambda: telegram_service.quien_se_unio_por_enlace(self.account_id, self.chat_id, e["enlace"]), _fin)

    def _revocar(self):
        e = self._actual()
        if e:
            confirmar_async(self, _("¿Revocar este enlace? Dejará de funcionar."),
                            lambda: _hacer(self, lambda: telegram_service.revocar_enlace_invitacion(self.account_id, self.chat_id,
                                                                                                    e["enlace"]),
                                           _("Enlace revocado."), lambda _r: self._cargar()), etiqueta_si=_("Revocar"))

    def _borrar(self):
        e = self._actual()
        if e:
            _hacer(self, lambda: telegram_service.borrar_enlace_invitacion(self.account_id, self.chat_id, e["enlace"]),
                   _("Enlace borrado."), lambda _r: self._cargar())


# --------------------------------------------------------------------------- solicitudes de unión
class DialogoSolicitudesTG(DialogoLista):
    def __init__(self, padre, account_id: int, chat_id: int, titulo: str):
        super().__init__(padre, _("Solicitudes de unión a {t}").format(t=titulo), _("Solicitudes de unión"))
        self.account_id, self.chat_id, self.items = account_id, chat_id, []
        self.boton(_("_Aprobar"), lambda: self._uno(True))
        self.boton(_("_Rechazar"), lambda: self._uno(False))
        self.boton(_("Aprobar _todas"), lambda: self._todas(True))
        self.boton(_("Rechazar t_odas"), lambda: self._todas(False))
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las solicitudes: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{s['nombre']}. {fecha_legible(s['fecha'])}" + (f". {s['bio']}" if s.get("bio") else "")
                              for s in self.items])
            self.mostrar_estado(_("{n} solicitudes").format(n=len(self.items)) if self.items else _("No hay solicitudes pendientes."))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.listar_solicitudes_union(self.account_id, self.chat_id), _fin)

    def _uno(self, aprobar: bool):
        p = self.lista.posicion()
        if not 0 <= p < len(self.items):
            return
        uid = self.items[p]["user_id"]
        fn = telegram_service.aprobar_solicitud if aprobar else telegram_service.rechazar_solicitud
        _hacer(self, lambda: fn(self.account_id, self.chat_id, uid), _("Solicitud aprobada.") if aprobar else _("Solicitud rechazada."),
               lambda _r: self._cargar())

    def _todas(self, aprobar: bool):
        fn = telegram_service.aprobar_todas_solicitudes if aprobar else telegram_service.rechazar_todas_solicitudes
        confirmar_async(self, _("¿Aprobar todas las solicitudes?") if aprobar else _("¿Rechazar todas las solicitudes?"),
                        lambda: _hacer(self, lambda: fn(self.account_id, self.chat_id), _("Hecho."), lambda _r: self._cargar()),
                        etiqueta_si=_("Continuar"))


# --------------------------------------------------------------------------- ajustes del chat
class DialogoAjustesChatTG(DialogoBase):
    """Título, descripción, nombre público, modo lento, borrado automático y contenido protegido. Solo se envía lo que cambias."""

    def __init__(self, padre, account_id: int, chat_id: int, titulo: str):
        super().__init__(padre, _("Ajustes de {t}").format(t=titulo), _("_Guardar"))
        self.account_id, self.chat_id, self.original = account_id, chat_id, {}
        self.titulo = self.campo(_("_Título:"), Gtk.Entry(text=titulo, activates_default=True, width_chars=40))
        self.descripcion = self.campo(_("_Descripción:"), Gtk.Entry(activates_default=True))
        self.username = self.campo(_("_Nombre público (sin @, vacío = privado):"), Gtk.Entry(activates_default=True))
        self.lento = self.campo(_("_Modo lento en segundos (0 = desactivado):"), Gtk.SpinButton.new_with_range(0, 86400, 10))
        self.auto = self.campo(_("Borrado a_utomático de mensajes en segundos (0 = desactivado):"),
                               Gtk.SpinButton.new_with_range(0, 31536000, 3600))
        self.protegido = self.campo(_("Contenido _protegido (no se puede reenviar ni guardar):"), Gtk.Switch(halign=Gtk.Align.START))
        self.foro = self.campo(_("Activar los _temas (modo foro):"), Gtk.Switch(halign=Gtk.Align.START))
        self.original_titulo = titulo
        self.boton_aceptar.set_sensitive(False)
        self.set_focus(self.titulo)

        def _fin(info, err):
            if info:
                self.descripcion.set_text(info.get("bio", "") or "")
                self.username.set_text((info.get("username") or "").lstrip("@"))
                self.protegido.set_active(bool(info.get("protegido")))
            self.original = {"descripcion": self.descripcion.get_text(), "username": self.username.get_text(),
                             "protegido": self.protegido.get_active()}
            self.boton_aceptar.set_sensitive(True)
        ejecutar(lambda: telegram_service.obtener_info_chat(account_id, chat_id), _fin)

    def aceptar(self):
        S, a, c = telegram_service, self.account_id, self.chat_id
        tareas = []
        if self.titulo.get_text().strip() != self.original_titulo:
            tareas.append(lambda t=self.titulo.get_text().strip(): S.editar_titulo_chat(a, c, t))
        if self.descripcion.get_text() != self.original.get("descripcion"):
            tareas.append(lambda t=self.descripcion.get_text().strip(): S.editar_descripcion_chat(a, c, t))
        if self.username.get_text().strip().lstrip("@") != self.original.get("username"):
            tareas.append(lambda t=self.username.get_text().strip().lstrip("@"): S.establecer_username_chat(a, c, t))
        if int(self.lento.get_value()):
            tareas.append(lambda n=int(self.lento.get_value()): S.establecer_modo_lento(a, c, n))
        if int(self.auto.get_value()):
            tareas.append(lambda n=int(self.auto.get_value()): S.establecer_autodestruccion_chat(a, c, n))
        if self.protegido.get_active() != self.original.get("protegido"):
            tareas.append(lambda v=self.protegido.get_active(): S.restringir_reenvios(a, c, v))
        if self.foro.get_active():
            tareas.append(lambda: S.activar_temas_foro(a, c, True))
        self.close()
        if not tareas:
            return notificar(_("No has cambiado nada."))

        def _todo():
            for t in tareas:
                t()
            return True
        _hacer(self.padre, _todo, _("Ajustes guardados."))


# --------------------------------------------------------------------------- temas de foro
class DialogoTemasTG(DialogoLista):
    def __init__(self, padre, account_id: int, chat_id: int, titulo: str):
        super().__init__(padre, _("Temas de {t}").format(t=titulo), _("Temas del foro"))
        self.account_id, self.chat_id, self.items = account_id, chat_id, []
        self.boton(_("_Crear…"), self._crear)
        self.boton(_("_Renombrar…"), self._renombrar)
        self.boton(_("Ce_rrar o reabrir"), self._cerrar)
        self.boton(_("_Fijar o desfijar"), self._fijar)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los temas: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([t["title"] for t in self.items])
            self.mostrar_estado(_("{n} temas").format(n=len(self.items)) if self.items else _("Este chat no tiene temas."))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.obtener_temas(self.account_id, self.chat_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _crear(self):
        _pedir(self, _("Nuevo tema"), _("_Título:"), lambda t: t and _hacer(
            self, lambda: telegram_service.crear_tema(self.account_id, self.chat_id, t), _("Tema creado."), lambda _r: self._cargar()))

    def _renombrar(self):
        t = self._actual()
        if t:
            _pedir(self, _("Renombrar el tema"), _("_Título:"), lambda n: n and _hacer(
                self, lambda: telegram_service.editar_tema(self.account_id, self.chat_id, t["id"], n), _("Tema renombrado."),
                lambda _r: self._cargar()), valor=t["title"])

    def _cerrar(self):
        t = self._actual()
        if t:
            _hacer(self, lambda: telegram_service.cerrar_tema(self.account_id, self.chat_id, t["id"], True),
                   _("Tema cerrado."), lambda _r: self._cargar())

    def _fijar(self):
        t = self._actual()
        if t:
            _hacer(self, lambda: telegram_service.fijar_tema(self.account_id, self.chat_id, t["id"], True), _("Tema fijado."),
                   lambda _r: self._cargar())

    def _borrar(self):
        t = self._actual()
        if t:
            confirmar_async(self, _("¿Borrar el tema «{t}» con todos sus mensajes?").format(t=t["title"]),
                            lambda: _hacer(self, lambda: telegram_service.borrar_tema(self.account_id, self.chat_id, t["id"]),
                                           _("Tema borrado."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- registro de administración y otros
def registro_admin(padre, account_id: int, chat_id: int, titulo: str):
    def _fin(res, err):
        if err:
            notificar(_("No se pudo cargar el registro: {e}").format(e=err))
            return
        texto_en_dialogo(padre, _("Registro de administración de {t}").format(t=titulo),
                         "\n".join(f"{fecha_legible(e['fecha'])}: {e['accion']}" for e in res or []) or _("Sin acciones recientes."))
    ejecutar(lambda: telegram_service.obtener_registro_admin(account_id, chat_id), _fin)


def grupo_de_comentarios(padre, account_id: int, canal_id: int):
    def _grupos(res, err):
        if err:
            return notificar(_("No se pudieron cargar los grupos: {e}").format(e=err))
        opciones = [_("(Quitar el grupo de comentarios)")] + [g["titulo"] for g in res or []]

        def _elegido(i):
            if i == 0:
                _hacer(padre, lambda: telegram_service.desvincular_grupo_discusion(account_id, canal_id), _("Grupo de comentarios quitado."))
            else:
                g = res[i - 1]
                _hacer(padre, lambda: telegram_service.vincular_grupo_discusion(account_id, canal_id, g["id"]),
                       _("Grupo de comentarios vinculado."))
        DialogoElegir(padre, _("Grupo de comentarios"), _("_Grupo:"), opciones, _elegido, _("_Aplicar")).present()
    ejecutar(lambda: telegram_service.chats_aptos_discusion(account_id), _grupos)


def vaciar_chat(padre, account_id: int, chat_id: int, titulo: str, tipo: str, al_terminar):
    confirmar_async(padre, _("¿Vaciar la conversación con {t}? Se borra el historial.").format(t=titulo),
                    lambda: _hacer(padre, lambda: telegram_service.vaciar_chat(account_id, chat_id, False, tipo),
                                   _("Conversación vaciada."), lambda _r: al_terminar()), etiqueta_si=_("Vaciar"))


def enviar_como(padre, account_id: int, chat_id: int):
    def _fin(res, err):
        if err or not res:
            return notificar(_("No hay perfiles alternativos para este chat."))
        DialogoElegir(padre, _("Enviar como"), _("_Perfil:"), [n for _i, n in res],
                      lambda i: _hacer(padre, lambda: telegram_service.establecer_enviar_como(account_id, chat_id, res[i][0]),
                                       _("Se publicará con ese perfil.")), _("_Elegir")).present()
    ejecutar(lambda: telegram_service.obtener_enviar_como(account_id, chat_id), _fin)


