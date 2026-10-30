# -*- coding: utf-8 -*-
"""Diálogos de Matrix para la interfaz GTK 4: alta de cuenta, miembros, directorio, perfil, dispositivos, ajustes
de sala, hilos, fijados, encuestas, ubicación y palabras de aviso."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.plataforma import llamar_en_ui, mostrar_aviso_dialogo
from app.core.speech import notificar

from .dialogos import DialogoBase, DialogoElegir, DialogoTexto, confirmar_async, ejecutar
from .dialogos_irc import DialogoMotivoIRC
from .dialogos_mastodon import DialogoLista
from .dialogos_xmpp import DialogoMensajeXMPP

log = logging.getLogger(__name__)

# El mismo diálogo de escritura (varias líneas, Ctrl+Intro envía) sirve para Matrix.
DialogoMensajeMatrix = DialogoMensajeXMPP

RANGOS = [(0, "Miembro"), (50, "Moderador/a"), (100, "Administrador/a")]
ACCESOS = [("public", "Pública (cualquiera puede entrar)"), ("invite", "Privada (solo con invitación)"),
           ("knock", "Llamando a la puerta (pides entrar y te aceptan)")]
HISTORIALES = [("world_readable", "Visible para todo el mundo (incluso sin entrar)"),
               ("shared", "Visible para los miembros (todo el historial)"),
               ("invited", "Desde que cada miembro fue invitado"), ("joined", "Desde que cada miembro entró")]


def _servicio():
    from app.services.matrix import matrix_service
    return matrix_service


def _hora(iso: str) -> str:
    from app.services.matrix.matrix_common import hora_local
    return hora_local(iso, con_fecha=True) if iso else ""


class DialogoCuentaMatrix(DialogoBase):
    """Alta de una cuenta de Matrix: servidor, usuario y contraseña. Si el servidor usa inicio de sesión por navegador
    (MAS/OAuth), se abre el navegador y el usuario y la contraseña se ignoran."""

    def __init__(self, padre, al_terminar):
        super().__init__(padre, _("Añadir cuenta de Matrix"), _("Iniciar _sesión"))
        self.al_terminar = al_terminar
        self.servidor = self.campo(_("_Servidor (por ejemplo matrix.org):"),
                                   Gtk.Entry(text="matrix.org", activates_default=True, width_chars=36),
                                   _("El servidor donde tienes la cuenta. Si tu dominio delega en otro, se descubre solo."))
        self.usuario = self.campo(_("_Usuario (@nombre:servidor o solo nombre):"),
                                  Gtk.Entry(activates_default=True, width_chars=36))
        self.clave = self.campo(_("_Contraseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True),
                                _("Solo se usa para iniciar sesión; se guarda el token de sesión cifrado, no la contraseña."))
        self.admin = Gtk.CheckButton(label=_("Soy _administrador del servidor (pedir permisos de administración)"),
                                     use_underline=True)
        self.admin.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                   [_("Solo para servidores con inicio de sesión por navegador. El servidor solo concede "
                                      "los permisos si de verdad lo eres.")])
        self.rejilla.attach(self.admin, 1, self._fila, 1, 1)
        self._fila += 1
        try:
            cifrado = _servicio().cifrado_disponible()
        except Exception:
            cifrado = False
        nota = (_("El cifrado de extremo a extremo está activo: las salas cifradas funcionan. Para leer tu historial "
                  "cifrado anterior, importa las claves desde el menú Matrix.") if cifrado
                else _("Aviso: el motor de cifrado (python-olm) no está instalado, así que las salas cifradas no se "
                       "podrán leer."))
        self.rejilla.attach(Gtk.Label(label=nota, xalign=0, wrap=True, max_width_chars=60), 0, self._fila, 2, 1)
        self.set_focus(self.servidor)

    def aceptar(self):
        hs, usuario, clave = self.servidor.get_text().strip(), self.usuario.get_text().strip(), self.clave.get_text()
        if not hs:
            notificar(_("Escribe al menos el servidor"))
            return
        admin = self.admin.get_active()
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Conectando con el servidor de Matrix…"))

        def _crear():
            matrix = _servicio()
            meta = None
            try:
                meta = matrix.descubrir_auth_navegador(hs)
            except Exception:
                meta = None
            if meta:
                return matrix.crear_cuenta_oauth(hs, pedir_admin=admin, progreso=lambda m: llamar_en_ui(notificar, m))
            if not usuario or not clave:
                from app.services.matrix.matrix_common import MatrixError
                raise MatrixError(_("Este servidor pide usuario y contraseña; escríbelos."))
            return matrix.crear_cuenta(hs, usuario, clave)

        def _fin(ident, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo iniciar sesión: {e}").format(e=err))
                return
            notificar(_("Cuenta de Matrix añadida"))
            self.close()
            self.al_terminar(ident)
        ejecutar(_crear, _fin)


class DialogoRegistroMatrix(DialogoBase):
    """Crea una cuenta nueva en un servidor con registro abierto (o con token de registro)."""

    def __init__(self, padre, al_terminar):
        super().__init__(padre, _("Registrar una cuenta nueva de Matrix"), _("_Registrar"))
        self.al_terminar = al_terminar
        self.servidor = self.campo(_("_Servidor:"), Gtk.Entry(activates_default=True, width_chars=36))
        self.usuario = self.campo(_("_Usuario deseado:"), Gtk.Entry(activates_default=True, width_chars=36))
        self.clave = self.campo(_("_Contraseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        self.token = self.campo(_("_Token de registro (si el servidor lo pide):"),
                                Gtk.Entry(activates_default=True, width_chars=36))
        self.set_focus(self.servidor)

    def aceptar(self):
        hs, usuario, clave = self.servidor.get_text().strip(), self.usuario.get_text().strip(), self.clave.get_text()
        usuario = usuario.lstrip("@").split(":")[0]
        token = self.token.get_text().strip()
        if not hs or not usuario or not clave:
            notificar(_("Rellena el servidor, el usuario y la contraseña"))
            return
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Registrando la cuenta…"))

        def _hacer():
            matrix = _servicio()
            return matrix.registrar_con_token(hs, usuario, clave, token) if token else matrix.registrar_cuenta(hs, usuario, clave)

        def _fin(ident, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo registrar: {e}").format(e=err))
                return
            notificar(_("Cuenta registrada y añadida"))
            self.close()
            self.al_terminar(ident)
        ejecutar(_hacer, _fin)


class DialogoClaveMatrix(DialogoBase):
    """Pide una contraseña (de la cuenta o de un archivo de claves) y la entrega a `al_aceptar`."""

    def __init__(self, padre, titulo: str, etiqueta: str, al_aceptar, aceptar: str = None):
        super().__init__(padre, titulo, aceptar)
        self.al_aceptar = al_aceptar
        self.entrada = self.campo(etiqueta, Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        self.set_focus(self.entrada)

    def aceptar(self):
        clave = self.entrada.get_text()
        if clave:
            self.close()
            self.al_aceptar(clave)


class DialogoUnirseMatrix(DialogoBase):
    """Unirse a una sala por alias (#sala:servidor) o por id (!id:servidor)."""

    def __init__(self, padre, account_id: int, al_terminar, sala: str = ""):
        super().__init__(padre, _("Unirse a una sala de Matrix"), _("_Entrar"))
        self.account_id, self.al_terminar = account_id, al_terminar
        self.sala = self.campo(_("_Sala (#sala:servidor):"), Gtk.Entry(text=sala, activates_default=True, width_chars=40))
        self.set_focus(self.sala)

    def aceptar(self):
        sala = self.sala.get_text().strip()
        if not sala:
            return
        self.close()
        notificar(_("Entrando en {s}…").format(s=sala))

        def _fin(room_id, err):
            notificar(_("No se pudo entrar: {e}").format(e=err) if err else _("Dentro de la sala"))
            if not err:
                self.al_terminar(room_id)
        ejecutar(lambda: _servicio().unirse_sala(self.account_id, sala), _fin)


class DialogoCrearSalaMatrix(DialogoBase):
    def __init__(self, padre, account_id: int, al_terminar):
        super().__init__(padre, _("Crear una sala de Matrix"), _("_Crear"))
        self.account_id, self.al_terminar = account_id, al_terminar
        self.nombre = self.campo(_("_Nombre de la sala:"), Gtk.Entry(activates_default=True, width_chars=36))
        self.tema = self.campo(_("_Tema (opcional):"), Gtk.Entry(activates_default=True, width_chars=36))
        self.publica = Gtk.CheckButton(label=_("Sala _pública (aparece en el directorio y cualquiera puede entrar)"),
                                       use_underline=True)
        self.rejilla.attach(self.publica, 1, self._fila, 1, 1)
        self.set_focus(self.nombre)

    def aceptar(self):
        nombre, tema, publica = self.nombre.get_text().strip(), self.tema.get_text().strip(), self.publica.get_active()
        if not nombre:
            notificar(_("Escribe el nombre de la sala"))
            return
        self.close()

        def _fin(room_id, err):
            notificar(_("No se pudo crear: {e}").format(e=err) if err else _("Sala creada"))
            if not err:
                self.al_terminar(room_id)
        ejecutar(lambda: _servicio().crear_sala(self.account_id, nombre, publica=publica, tema=tema), _fin)


class DialogoEncuestaMatrix(DialogoBase):
    """Crear una encuesta: la pregunta y una opción por línea (mínimo dos)."""

    def __init__(self, padre, al_crear):
        super().__init__(padre, _("Crear una encuesta"), _("_Enviar"))
        self.al_crear = al_crear
        self.set_resizable(True)
        self.pregunta = self.campo(_("_Pregunta:"), Gtk.Entry(activates_default=True, width_chars=44))
        self.opciones = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                     left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        marco = Gtk.ScrolledWindow(child=self.opciones, min_content_height=110, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Opciones (una por línea):"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.opciones)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.oculta = Gtk.CheckButton(label=_("Ocultar los votos hasta cerrar la encuesta"), use_underline=True)
        self.rejilla.attach(self.oculta, 1, self._fila, 1, 1)
        self.set_focus(self.pregunta)

    def aceptar(self):
        b = self.opciones.get_buffer()
        lineas = [x.strip() for x in b.get_text(b.get_start_iter(), b.get_end_iter(), False).splitlines() if x.strip()]
        pregunta = self.pregunta.get_text().strip()
        if not pregunta or len(lineas) < 2:
            notificar(_("Escribe la pregunta y al menos dos opciones"))
            return
        self.close()
        self.al_crear(pregunta, lineas, self.oculta.get_active())


class DialogoUbicacionMatrix(DialogoBase):
    def __init__(self, padre, al_enviar):
        super().__init__(padre, _("Compartir una ubicación"), _("_Enviar"))
        self.al_enviar = al_enviar
        self.lat = self.campo(_("_Latitud (por ejemplo 40.4168):"), Gtk.Entry(activates_default=True, width_chars=20))
        self.lon = self.campo(_("L_ongitud (por ejemplo -3.7038):"), Gtk.Entry(activates_default=True, width_chars=20))
        self.desc = self.campo(_("_Descripción (opcional):"), Gtk.Entry(activates_default=True, width_chars=36))
        self.set_focus(self.lat)

    def aceptar(self):
        try:
            lat = float(self.lat.get_text().strip().replace(",", "."))
            lon = float(self.lon.get_text().strip().replace(",", "."))
        except ValueError:
            notificar(_("Escribe la latitud y la longitud como números"))
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            notificar(_("Las coordenadas están fuera de rango"))
            return
        self.close()
        self.al_enviar(lat, lon, self.desc.get_text().strip())


class DialogoOpcionesMatrix(DialogoLista):
    """Lista genérica de resultados con una acción por Intro (espacios de una sala, resultados de búsqueda...)."""

    def __init__(self, padre, titulo: str, nombre_lista: str, textos, al_elegir, accion: str = None):
        super().__init__(padre, titulo, nombre_lista, al_activar=lambda pos: self._elegir())
        self.al_elegir = al_elegir
        self.boton(accion or _("_Abrir"), self._elegir)
        self.cerrar_boton()
        self.lista.poner(textos)
        self.mostrar_estado(_("{n} resultados").format(n=len(textos)) if textos else _("No hay nada que mostrar"))

    def _elegir(self):
        p = self.lista.posicion()
        if p >= 0:
            self.close()
            self.al_elegir(p)


class DialogoDirectorioMatrix(DialogoLista):
    """Salas públicas de un servidor (el propio o el que escribas), con filtro; Intro entra en la seleccionada."""

    def __init__(self, padre, account_id: int, al_unirse):
        super().__init__(padre, _("Directorio de salas de Matrix"), _("Lista de salas"), al_activar=lambda pos: self._unirse())
        self.account_id, self.al_unirse = account_id, al_unirse
        self.salas = []
        caja = self.get_child()
        fila = Gtk.Box(spacing=8)
        e1 = Gtk.Label(label=_("_Buscar:"), use_underline=True)
        self.filtro = Gtk.Entry(hexpand=True, activates_default=False)
        e1.set_mnemonic_widget(self.filtro)
        e2 = Gtk.Label(label=_("_Servidor (vacío = el tuyo):"), use_underline=True)
        self.servidor = Gtk.Entry(width_chars=20)
        e2.set_mnemonic_widget(self.servidor)
        for w in (e1, self.filtro, e2, self.servidor):
            fila.append(w)
        for e in (self.filtro, self.servidor):
            e.connect("activate", lambda *_a: self._buscar())
        caja.insert_child_after(fila, None)
        self.boton(_("_Buscar"), self._buscar)
        self.boton(_("_Entrar en la sala"), self._unirse)
        self.cerrar_boton()
        self._buscar()

    def _buscar(self):
        self.mostrar_estado(_("Consultando el servidor…"))
        filtro, servidor = self.filtro.get_text().strip(), self.servidor.get_text().strip()

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo obtener el directorio: {e}").format(e=err))
                return
            self.salas = res or []
            self.lista.poner([_("{n}, {m} miembros. {t}").format(n=s["nombre"], m=s["miembros"],
                                                                 t=" ".join((s["tema"] or "").split())[:140]).strip()
                              for s in self.salas])
            self.mostrar_estado(_("{n} salas").format(n=len(self.salas)) if self.salas else _("No hay salas que mostrar"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().directorio_salas(self.account_id, filtro, server=servidor), _fin)

    def _unirse(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.salas):
            s = self.salas[p]
            self.close()
            self.al_unirse(s["alias"] or s["room_id"])


def ver_perfil_usuario(account_id: int, uid: str, nombre: str):
    """Muestra el perfil y la presencia de un usuario de Matrix."""
    def _datos():
        matrix = _servicio()
        perfil = matrix.perfil_de(account_id, uid)
        try:
            presencia = matrix.presencia_de(account_id, uid)
        except Exception:
            presencia = ""
        return perfil, presencia

    def _fin(res, err):
        if err:
            notificar(_("No se pudo leer el perfil: {e}").format(e=err))
            return
        perfil, presencia = res
        lineas = [_("Usuario: {u}").format(u=uid), _("Nombre visible: {n}").format(n=perfil.get("nombre") or _("(sin nombre)"))]
        if presencia:
            lineas.append(_("Presencia: {p}").format(p=presencia))
        if perfil.get("avatar_mxc"):
            lineas.append(_("Tiene avatar."))
        mostrar_aviso_dialogo("\n".join(lineas), _("Perfil de {n}").format(n=nombre))
    ejecutar(_datos, _fin)


class DialogoMiembrosMatrix(DialogoLista):
    """Miembros de una sala con su rango en palabras, y las acciones de moderación que permita tu rango."""

    def __init__(self, padre, account_id: int, room_id: str, nombre: str, al_abrir_dm=None):
        super().__init__(padre, _("Miembros de {s}").format(s=nombre), _("Lista de miembros"),
                         al_activar=lambda pos: self._perfil())
        self.account_id, self.room_id, self.al_abrir_dm = account_id, room_id, al_abrir_dm
        self.miembros = []
        self.boton(_("Ver _perfil"), self._perfil)
        self.boton(_("Mensaje _directo"), self._dm)
        self.boton(_("_Invitar…"), self._invitar)
        self.b_mod = self.boton(_("_Moderación…"), self._moderar, sensible=False)
        self.boton(_("_Actualizar"), self._refrescar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        self.mostrar_estado(_("Cargando miembros…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los miembros: {e}").format(e=err))
                return
            self.miembros = res or []
            yo = _servicio()._user_id(self.account_id) or ""
            mi_nivel = next((m[3] for m in self.miembros if m[0] == yo), 0)
            self.b_mod.set_sensitive(mi_nivel >= 50)
            self.lista.poner([f"{m[1]} ({m[0]})" + (f", {m[2]}" if m[2] else "") for m in self.miembros])
            self.mostrar_estado(_("{n} miembros").format(n=len(self.miembros)) if self.miembros
                                else _("No se encontraron miembros. ¿Está conectada la cuenta?"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().miembros_con_rango(self.account_id, self.room_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.miembros[p] if 0 <= p < len(self.miembros) else None

    def _perfil(self):
        m = self._actual()
        if m:
            ver_perfil_usuario(self.account_id, m[0], m[1])

    def _dm(self):
        m = self._actual()
        if not m or not self.al_abrir_dm:
            return
        self.close()
        self.al_abrir_dm(m[0])

    def _invitar(self):
        def _enviar(uid):
            ejecutar(lambda: _servicio().accion_moderacion(self.account_id, self.room_id, uid, "invitar"),
                     lambda r, e: notificar(_("No se pudo invitar: {e}").format(e=e) if e else _("Invitación enviada")))
        DialogoTexto(self, _("Invitar a la sala"), _("_Usuario (@nombre:servidor):"), _enviar, aceptar=_("_Invitar")).present()

    ACCIONES = [("rango", "Cambiar el rango…"), ("expulsar", "Expulsar de la sala…"), ("banear", "Banear…"),
                ("desbanear", "Quitar el baneo"), ("firmar", "Firmar su identidad (experto)…")]

    def _moderar(self):
        m = self._actual()
        if not m:
            return
        uid, nombre = m[0], m[1]

        def _elegida(i):
            clave = self.ACCIONES[i][0]
            if clave == "rango":
                self._rango(uid, nombre)
            elif clave == "firmar":
                self._firmar(uid, nombre)
            elif clave == "desbanear":
                self._moderacion("desbanear", uid, "")
            else:
                confirmar_async(self, _("¿Seguro que quieres hacerlo con {n}?").format(n=nombre),
                                lambda: DialogoMotivoIRC(self, _("Motivo"), lambda mot: self._moderacion(clave, uid, mot)).present(),
                                etiqueta_si=_("Continuar"))
        DialogoElegir(self, _("Acciones sobre {n}").format(n=nombre), _("_Acción:"), [_(a[1]) for a in self.ACCIONES],
                      _elegida, aceptar=_("_Hacerlo")).present()

    def _moderacion(self, accion: str, uid: str, motivo: str):
        ejecutar(lambda: _servicio().accion_moderacion(self.account_id, self.room_id, uid, accion, motivo),
                 lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Hecho")), self._refrescar()))

    def _rango(self, uid: str, nombre: str):
        def _elegido(i):
            nivel = RANGOS[i][0]

            def _poner():
                ejecutar(lambda: _servicio().fijar_rango(self.account_id, self.room_id, uid, nivel),
                         lambda r, e: (notificar(_("No se pudo cambiar el rango: {e}").format(e=e) if e else _("Rango cambiado")),
                                       self._refrescar()))
            if nivel >= 100:
                confirmar_async(self, _("Hacer administrador/a a {n} no se puede deshacer (tendrá tu mismo poder). ¿Continuar?")
                                .format(n=nombre), _poner, etiqueta_si=_("Continuar"))
            else:
                _poner()
        DialogoElegir(self, _("Cambiar el rango de {n}").format(n=nombre), _("_Nuevo rango:"), [_(r[1]) for r in RANGOS],
                      _elegido, aceptar=_("_Cambiar")).present()

    def _firmar(self, uid: str, nombre: str):
        def _hacer():
            ejecutar(lambda: _servicio().firmar_usuario(self.account_id, uid),
                     lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e
                                            else _("Hecho: ahora confías en {n} por verificación cruzada").format(n=nombre)))
        confirmar_async(self, _("Modo experto. Vas a firmar la identidad de {n} ({u}) sin comparar emojis. Solo hazlo si "
                                "has comprobado por otra vía que es quien dice ser. Para confiar en una persona lo normal "
                                "es verificarla por emojis. ¿Continuar?").format(n=nombre, u=uid), _hacer,
                        etiqueta_si=_("Firmar"))


class DialogoPerfilMatrix(DialogoBase):
    """Tu perfil y estado: nombre visible, avatar, presencia con mensaje y zona horaria."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mi perfil y estado de Matrix"), _("_Guardar"))
        self.account_id = account_id
        self.set_resizable(True)
        from app.services.matrix.matrix_avanzado import _AvanzadoMixin, presencias_legibles
        self.presencias = presencias_legibles()
        self.nombre = self.campo(_("_Nombre visible:"), Gtk.Entry(activates_default=True, width_chars=36))
        self.presencia = self.campo(_("_Presencia:"), Gtk.DropDown.new_from_strings([p[1] for p in self.presencias]))
        self.estado = self.campo(_("_Mensaje de estado (opcional):"), Gtk.Entry(activates_default=True, width_chars=36))
        self.zona = self.campo(_("_Zona horaria (así otros ven bien tu hora):"),
                               Gtk.Entry(activates_default=True, width_chars=30),
                               _("Por ejemplo Europe/Madrid. Vacío para no indicarla."))
        self.zona_sistema = _AvanzadoMixin.zona_horaria_sistema()
        fila = Gtk.Box(spacing=8)
        for etiqueta, fn in ((_("Cambiar _avatar…"), self._avatar), (_("Usar la zona horaria de e_ste equipo"), self._tz_sistema),
                             (_("Más _campos del perfil…"), self._campos)):
            b = Gtk.Button(label=etiqueta, use_underline=True)
            b.connect("clicked", lambda *_a, f=fn: f())
            fila.append(b)
        self.rejilla.attach(fila, 0, self._fila, 2, 1)
        self.set_focus(self.nombre)
        ejecutar(lambda: {"nombre": _servicio().mi_perfil(account_id).get("nombre") or "",
                          "tz": _servicio().mi_zona_horaria(account_id)}, self._cargado)

    def _cargado(self, res, err):
        if err or not res:
            return
        self.nombre.set_text(res["nombre"])
        self.zona.set_text(res["tz"] or "")

    def _tz_sistema(self):
        self.zona.set_text(self.zona_sistema)

    def _avatar(self):
        dlg = Gtk.FileDialog(title=_("Elige la imagen del avatar"))
        filtro = Gtk.FileFilter()
        filtro.set_name(_("Imágenes"))
        filtro.add_mime_type("image/*")
        almacen = Gio_list_store([filtro])
        dlg.set_filters(almacen)

        def _fin(origen, res):
            try:
                ruta = origen.open_finish(res).get_path()
            except Exception:
                return
            notificar(_("Subiendo el avatar…"))
            ejecutar(lambda: _servicio().cambiar_avatar(self.account_id, ruta),
                     lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e else _("Avatar cambiado")))
        dlg.open(self, None, _fin)

    def _campos(self):
        DialogoCamposPerfilMatrix(self, self.account_id).present()

    def aceptar(self):
        nombre, estado, tz = self.nombre.get_text().strip(), self.estado.get_text().strip(), self.zona.get_text().strip()
        presencia = self.presencias[self.presencia.get_selected()][0]

        def _guardar():
            matrix = _servicio()
            if nombre:
                matrix.cambiar_nombre_visible(self.account_id, nombre)
            matrix.fijar_presencia(self.account_id, presencia, estado)
            matrix.fijar_zona_horaria(self.account_id, tz)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo guardar: {e}").format(e=err))
                return
            notificar(_("Perfil y estado guardados"))
            self.close()
        ejecutar(_guardar, _fin)


def Gio_list_store(filtros):
    from gi.repository import Gio
    almacen = Gio.ListStore.new(Gtk.FileFilter)
    for f in filtros:
        almacen.append(f)
    return almacen


class DialogoCamposPerfilMatrix(DialogoLista):
    """Campos extra del perfil (pronombres, web...): añadir, editar valor o borrar."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Más campos del perfil de Matrix"), _("Lista de campos del perfil"),
                         al_activar=lambda pos: self._editar())
        self.account_id = account_id
        self.campos = []
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Editar valor…"), self._editar)
        self.boton(_("_Borrar…"), self._borrar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(perfil, err):
            if err:
                self.mostrar_estado(_("No se pudo leer el perfil: {e}").format(e=err))
                return
            conocidos = _servicio().campos_perfil_conocidos()
            self.campos = [(k, v) for k, v in (perfil or {}).items() if k not in ("displayname", "avatar_url")]
            self.lista.poner([f"{conocidos.get(k, k)}: {v}" for k, v in self.campos])
            self.mostrar_estado(_("{n} campos").format(n=len(self.campos)) if self.campos else _("No hay campos extra"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().leer_perfil_completo(self.account_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.campos[p] if 0 <= p < len(self.campos) else None

    def _guardar(self, clave: str, valor: str, aviso: str):
        ejecutar(lambda: _servicio().fijar_campo_perfil(self.account_id, clave, valor),
                 lambda r, e: (notificar(_("No se pudo guardar: {e}").format(e=e) if e else aviso), self._refrescar()))

    def _anadir(self):
        def _clave(clave):
            if clave in ("displayname", "avatar_url"):
                notificar(_("Ese campo se edita en la pantalla del perfil, no aquí"))
                return
            DialogoTexto(self, _("Valor del campo"), _("_Valor de «{c}»:").format(c=clave),
                         lambda v: self._guardar(clave, v, _("Campo añadido"))).present()
        DialogoTexto(self, _("Añadir un campo del perfil"), _("_Nombre del campo (por ejemplo m.pronouns o web):"), _clave).present()

    def _editar(self):
        c = self._actual()
        if not c:
            return
        d = DialogoTexto(self, _("Editar campo"), _("_Nuevo valor de «{c}»:").format(c=c[0]),
                         lambda v: self._guardar(c[0], v, _("Campo actualizado")))
        d.entrada.set_text(str(c[1]))
        d.present()

    def _borrar(self):
        c = self._actual()
        if not c:
            return
        confirmar_async(self, _("¿Borrar el campo «{c}» del perfil?").format(c=c[0]),
                        lambda: ejecutar(lambda: _servicio().borrar_campo_perfil(self.account_id, c[0]),
                                         lambda r, e: (notificar(_("No se pudo borrar: {e}").format(e=e) if e else _("Campo borrado")),
                                                       self._refrescar())), etiqueta_si=_("Borrar"))


class DialogoDispositivosMatrix(DialogoLista):
    """Sesiones de tu cuenta: verificar por emojis, renombrar o cerrar la sesión de otro dispositivo."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mis dispositivos de Matrix"), _("Lista de dispositivos"))
        self.account_id = account_id
        self.dispositivos = []
        self.boton(_("_Verificar por emojis…"), self._verificar)
        self.boton(_("_Renombrar…"), self._renombrar)
        self.boton(_("Cerrar la _sesión del dispositivo…"), self._eliminar)
        self.boton(_("_Actualizar"), self._refrescar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron leer los dispositivos: {e}").format(e=err))
                return
            self.dispositivos = res or []
            textos = []
            for d in self.dispositivos:
                t = d["nombre"] or d["id"]
                t += _(" (este dispositivo)") if d["actual"] else ""
                partes = [d["id"]] + ([_("visto {f}").format(f=d["ultima_vez"])] if d["ultima_vez"] else []) + ([d["ip"]] if d["ip"] else [])
                textos.append(f"{t}. " + ", ".join(partes))
            self.lista.poner(textos)
            self.mostrar_estado(_("{n} dispositivos").format(n=len(self.dispositivos)))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().mis_dispositivos(self.account_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.dispositivos[p] if 0 <= p < len(self.dispositivos) else None

    def _verificar(self):
        d = self._actual()
        if not d:
            return
        if d["actual"]:
            notificar(_("Elige otro dispositivo tuyo: este es el que estás usando"))
            return
        notificar(_("Iniciando la verificación; compara los emojis cuando aparezcan en los dos dispositivos"))
        ejecutar(lambda: _servicio().verificar_mi_sesion(self.account_id, d["id"]),
                 lambda r, e: e and notificar(_("No se pudo: {e}").format(e=e)))

    def _renombrar(self):
        d = self._actual()
        if not d:
            return
        dlg = DialogoTexto(self, _("Renombrar el dispositivo"), _("_Nuevo nombre:"),
                           lambda n: ejecutar(lambda: _servicio().renombrar_dispositivo(self.account_id, d["id"], n),
                                              lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Dispositivo renombrado")),
                                                            self._refrescar())))
        dlg.entrada.set_text(d["nombre"])
        dlg.present()

    def _eliminar(self):
        d = self._actual()
        if not d:
            return
        if d["actual"]:
            notificar(_("No puedes cerrar la sesión que estás usando desde aquí"))
            return

        def _con_clave(clave):
            ejecutar(lambda: _servicio().eliminar_dispositivos(self.account_id, [d["id"]], clave),
                     lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Sesión cerrada")), self._refrescar()))
        confirmar_async(self, _("¿Cerrar la sesión de «{n}»? Hará falta tu contraseña.").format(n=d["nombre"] or d["id"]),
                        lambda: DialogoClaveMatrix(self, _("Cerrar la sesión de otro dispositivo"),
                                                   _("_Contraseña de tu cuenta:"), _con_clave).present(),
                        etiqueta_si=_("Continuar"))


class DialogoDispositivosSalaMatrix(DialogoLista):
    """Dispositivos de los miembros de una sala cifrada y su confianza."""

    def __init__(self, padre, account_id: int, room_id: str, nombre: str):
        super().__init__(padre, _("Dispositivos cifrados de {s}").format(s=nombre), _("Lista de dispositivos de la sala"))
        self.account_id, self.room_id = account_id, room_id
        self.dispositivos = []
        self.boton(_("_Verificar por emojis…"), self._verificar)
        self.boton(_("Marcar de _confianza (experto)…"), self._confiar)
        self.boton(_("_Bloquear"), self._bloquear)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo: {e}").format(e=err))
                return
            self.dispositivos = res or []
            self.lista.poner([f"{d['user_id']}: {d['nombre']} ({d['device_id']}), {d['estado']}" for d in self.dispositivos])
            self.mostrar_estado(_("{n} dispositivos").format(n=len(self.dispositivos)) if self.dispositivos
                                else _("No hay dispositivos que mostrar (¿la sala es cifrada?)"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().dispositivos_de_sala(self.account_id, self.room_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.dispositivos[p] if 0 <= p < len(self.dispositivos) else None

    def _verificar(self):
        d = self._actual()
        if d:
            notificar(_("Iniciando la verificación; compara los emojis cuando aparezcan"))
            ejecutar(lambda: _servicio().iniciar_verificacion(self.account_id, d["user_id"], d["device_id"]),
                     lambda r, e: e and notificar(_("No se pudo: {e}").format(e=e)))

    def _confiar(self):
        d = self._actual()
        if not d:
            return

        def _hacer():
            ejecutar(lambda: _servicio().confiar_dispositivo(self.account_id, d["user_id"], d["device_id"], True),
                     lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Dispositivo de confianza")), self._refrescar()))
        confirmar_async(self, _("Compara esta huella con la que muestra el otro cliente antes de confiar:\n{h}\n\n¿Marcarlo "
                                "como de confianza?").format(h=d.get("ed25519") or _("(sin huella)")), _hacer, etiqueta_si=_("Confiar"))

    def _bloquear(self):
        d = self._actual()
        if d:
            ejecutar(lambda: _servicio().confiar_dispositivo(self.account_id, d["user_id"], d["device_id"], False),
                     lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Dispositivo bloqueado")), self._refrescar()))


class DialogoAjustesSalaMatrix(DialogoBase):
    """Ajustes de una sala que administras: nombre, tema, alias, acceso, historial, directorio, cifrado y versión."""

    def __init__(self, padre, account_id: int, room_id: str, nombre: str):
        super().__init__(padre, _("Ajustes de {s}").format(s=nombre), _("_Guardar"))
        self.account_id, self.room_id = account_id, room_id
        self.set_resizable(True)
        self.nombre = self.campo(_("_Nombre:"), Gtk.Entry(activates_default=True, width_chars=40))
        self.tema = self.campo(_("_Tema:"), Gtk.Entry(activates_default=True, width_chars=40))
        self.alias = self.campo(_("_Alias público (#sala:servidor; vacío = no tocar):"),
                                Gtk.Entry(activates_default=True, width_chars=40))
        self.acceso = self.campo(_("Regla de _acceso:"), Gtk.DropDown.new_from_strings([_(a[1]) for a in ACCESOS]))
        self.historial = self.campo(_("_Historial visible:"), Gtk.DropDown.new_from_strings([_(h[1]) for h in HISTORIALES]))
        self.directorio = Gtk.CheckButton(label=_("Aparece en el _directorio público del servidor"), use_underline=True)
        self.rejilla.attach(self.directorio, 1, self._fila, 1, 1)
        self._fila += 1
        self.info = Gtk.Label(label="", xalign=0, wrap=True)
        self.rejilla.attach(self.info, 0, self._fila, 2, 1)
        self._fila += 1
        fila = Gtk.Box(spacing=8)
        self.b_cifrar = Gtk.Button(label=_("Activar el ci_frado (irreversible)…"), use_underline=True)
        self.b_cifrar.connect("clicked", lambda *_a: self._cifrar())
        b_ver = Gtk.Button(label=_("Actualizar la _versión de la sala…"), use_underline=True)
        b_ver.connect("clicked", lambda *_a: self._version())
        fila.append(self.b_cifrar)
        fila.append(b_ver)
        self.rejilla.attach(fila, 0, self._fila, 2, 1)
        self.original = {}
        self.set_focus(self.nombre)
        ejecutar(lambda: (_servicio().estado_sala(account_id, room_id), _servicio().visibilidad_directorio(account_id, room_id)),
                 self._cargado)

    def _cargado(self, res, err):
        if err or not res:
            notificar(_("No se pudieron leer los ajustes: {e}").format(e=err))
            return
        d, publica = res
        self.original = {"nombre": d["nombre"], "tema": d["tema"], "acceso": d["acceso"], "historial": d["historial"],
                         "directorio": bool(publica), "alias": d["alias"]}
        self.nombre.set_text(d["nombre"])
        self.tema.set_text(d["tema"])
        self.alias.set_text(d["alias"])
        self.acceso.set_selected(next((i for i, a in enumerate(ACCESOS) if a[0] == d["acceso"]), 1))
        self.historial.set_selected(next((i for i, h in enumerate(HISTORIALES) if h[0] == d["historial"]), 1))
        self.directorio.set_active(bool(publica))
        self.info.set_text((_("Sala cifrada. ") if d["cifrada"] else _("Sala sin cifrar. ")) + _("Versión {v}.").format(v=d["version"] or "?"))
        self.b_cifrar.set_sensitive(not d["cifrada"])

    def aceptar(self):
        o = self.original
        cambios = {}
        for clave, valor in (("nombre", self.nombre.get_text().strip()), ("tema", self.tema.get_text().strip())):
            if valor != o.get(clave, ""):
                cambios[clave] = valor
        acceso = ACCESOS[self.acceso.get_selected()][0]
        historial = HISTORIALES[self.historial.get_selected()][0]
        if acceso != o.get("acceso"):
            cambios["acceso"] = acceso
        if historial != o.get("historial"):
            cambios["historial"] = historial
        alias = self.alias.get_text().strip()
        alias_nuevo = alias if alias and alias != o.get("alias") else ""
        directorio = self.directorio.get_active()

        def _guardar():
            matrix = _servicio()
            if cambios:
                matrix.configurar_sala(self.account_id, self.room_id, cambios)
            if alias_nuevo:
                matrix.fijar_alias_sala(self.account_id, self.room_id, alias_nuevo)
            if directorio != o.get("directorio"):
                matrix.publicar_en_directorio(self.account_id, self.room_id, directorio)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo guardar (¿tienes permiso?): {e}").format(e=err))
                return
            notificar(_("Ajustes guardados"))
            self.close()
        ejecutar(_guardar, _fin)

    def _cifrar(self):
        confirmar_async(self, _("Activar el cifrado es irreversible: la sala no podrá volver a ser pública sin cifrar. "
                                "¿Continuar?"),
                        lambda: ejecutar(lambda: _servicio().activar_cifrado_sala(self.account_id, self.room_id),
                                         lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Cifrado activado")),
                                                       e is None and self.b_cifrar.set_sensitive(False))), etiqueta_si=_("Activar"))

    def _version(self):
        def _pedida(version):
            confirmar_async(self, _("Se creará una sala nueva con la versión {v} y se invitará a los miembros. ¿Continuar?")
                            .format(v=version),
                            lambda: ejecutar(lambda: _servicio().actualizar_version_sala(self.account_id, self.room_id, version),
                                             lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e
                                                                    else _("Sala actualizada: {r}").format(r=r))),
                            etiqueta_si=_("Actualizar"))
        DialogoTexto(self, _("Actualizar la versión de la sala"), _("_Versión nueva (por ejemplo 11):"), _pedida).present()


class DialogoHilosMatrix(DialogoLista):
    """Hilos de una sala; Intro abre el hilo seleccionado."""

    def __init__(self, padre, account_id: int, room_id: str, nombre: str):
        super().__init__(padre, _("Hilos de {s}").format(s=nombre), _("Lista de hilos"), al_activar=lambda pos: self._abrir())
        self.account_id, self.room_id, self.nombre = account_id, room_id, nombre
        self.hilos = []
        self.boton(_("_Abrir el hilo"), self._abrir)
        self.cerrar_boton()
        self.mostrar_estado(_("Consultando los hilos…"))
        ejecutar(lambda: _servicio().hilos_de_sala(account_id, room_id), self._cargado)

    def _cargado(self, res, err):
        if err:
            self.mostrar_estado(_("No se pudieron leer los hilos: {e}").format(e=err))
            return
        self.hilos = res or []
        self.lista.poner([f"{h['quien']}: {' '.join(h['texto'].split())[:160]}" for h in self.hilos])
        self.mostrar_estado(_("{n} hilos").format(n=len(self.hilos)) if self.hilos else _("Esta sala no tiene hilos"))
        self.lista.enfocar()

    def _abrir(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.hilos):
            DialogoHiloMatrix(self, self.account_id, self.room_id, self.hilos[p]["event_id"], self.nombre).present()


class DialogoHiloMatrix(DialogoLista):
    """Mensajes de un hilo y botón para responder en él."""

    def __init__(self, padre, account_id: int, room_id: str, raiz: str, nombre: str, al_cambiar=None):
        super().__init__(padre, _("Hilo en {s}").format(s=nombre), _("Mensajes del hilo"))
        self.account_id, self.room_id, self.raiz, self.al_cambiar = account_id, room_id, raiz, al_cambiar
        self.boton(_("_Responder en el hilo…"), self._responder)
        self.boton(_("_Actualizar"), self._refrescar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        self.mostrar_estado(_("Cargando el hilo…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo leer el hilo: {e}").format(e=err))
                return
            self.lista.poner([f"{_hora(m['fecha'])} {m['quien']}: {m['texto']}" for m in res])
            self.mostrar_estado(_("{n} mensajes").format(n=len(res)))
            self.lista.enfocar()
            p = self.lista.modelo.get_n_items() - 1
            if p >= 0:
                self.lista.seleccion.set_selected(p)
        ejecutar(lambda: _servicio().mensajes_de_hilo(self.account_id, self.room_id, self.raiz), _fin)

    def _responder(self):
        def _enviar(texto):
            ejecutar(lambda: _servicio().enviar_mensaje(self.account_id, self.room_id, texto, thread_root=self.raiz),
                     lambda r, e: (notificar(_("No se pudo enviar: {e}").format(e=e) if e else _("Mensaje enviado")),
                                   self._refrescar(), self.al_cambiar and self.al_cambiar()))
        DialogoMensajeMatrix(self, _("Responder en el hilo"), _enviar).present()


class DialogoFijadosMatrix(DialogoLista):
    """Mensajes fijados de una sala, con opción de soltarlos."""

    def __init__(self, padre, account_id: int, room_id: str, nombre: str):
        super().__init__(padre, _("Mensajes fijados de {s}").format(s=nombre), _("Lista de mensajes fijados"))
        self.account_id, self.room_id = account_id, room_id
        self.fijados = []
        self.boton(_("_Quitar de fijados"), self._quitar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron leer: {e}").format(e=err))
                return
            self.fijados = res or []
            self.lista.poner([f"{m['quien']}: {m['texto']}" for m in self.fijados])
            self.mostrar_estado(_("{n} mensajes fijados").format(n=len(self.fijados)) if self.fijados
                                else _("Esta sala no tiene mensajes fijados"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().mensajes_fijados_detalle(self.account_id, self.room_id), _fin)

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.fijados):
            eid = self.fijados[p]["event_id"]
            ejecutar(lambda: _servicio().fijar_evento(self.account_id, self.room_id, eid, False),
                     lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Mensaje desfijado")), self._refrescar()))


class DialogoPalabrasMatrix(DialogoLista):
    """Palabras que te avisan en cualquier sala (reglas de notificación del servidor)."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Palabras que me avisan"), _("Lista de palabras de aviso"))
        self.account_id = account_id
        self.palabras = []
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Quitar"), self._quitar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron leer: {e}").format(e=err))
                return
            self.palabras = res or []
            self.lista.poner(self.palabras)
            self.mostrar_estado(_("{n} palabras").format(n=len(self.palabras)) if self.palabras else _("No hay palabras de aviso"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().palabras_aviso(self.account_id), _fin)

    def _anadir(self):
        def _nueva(p):
            ejecutar(lambda: _servicio().agregar_palabra_aviso(self.account_id, p),
                     lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Palabra añadida: te avisará en todas tus sesiones")),
                                   self._refrescar()))
        DialogoTexto(self, _("Añadir palabra de aviso"), _("_Palabra o frase:"), _nueva, aceptar=_("_Añadir")).present()

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.palabras):
            palabra = self.palabras[p]
            ejecutar(lambda: _servicio().quitar_palabra_aviso(self.account_id, palabra),
                     lambda r, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Palabra quitada")), self._refrescar()))
