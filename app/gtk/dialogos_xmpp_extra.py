# -*- coding: utf-8 -*-
"""XMPP: comandos del servidor (ad-hoc), pasarelas, buscar usuarios, editar tu ficha, cambiar la contraseña y crear o
cancelar cuentas en el servidor. Los mismos servicios que usa la versión de Windows.

El formulario de un comando (XEP-0050 con XEP-0004) se pinta con controles accesibles según el tipo de cada campo, así que
sirve igual para la administración del servidor, para las pasarelas y para cualquier bot."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar

from .dialogos import DialogoBase, DialogoTexto, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_reddit import _marco, _texto_de, _vista_texto


def _servicio():
    from app.services.xmpp.xmpp_service import xmpp_service
    return xmpp_service


# --------------------------------------------------------------------------- formularios (XEP-0004)
class CamposFormulario:
    """Los controles de un formulario de campos (var, tipo, etiqueta, opciones, valor...) dentro de una caja con desplazamiento."""

    def __init__(self, campos: list):
        self.caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6, margin_start=6,
                            margin_end=6)
        self.controles = []     # (var, tipo, control, mapa de opciones)
        self.primero = None
        for c in campos:
            self._campo(c)

    def _etiqueta(self, texto: str, widget=None):
        lbl = Gtk.Label(label=texto, xalign=0, wrap=True, use_underline=True)
        if widget is not None:
            lbl.set_mnemonic_widget(widget)
        self.caja.append(lbl)

    def _campo(self, c: dict):
        tipo = c.get("type") or "text-single"
        var = c.get("var") or ""
        etiqueta = c.get("label") or var
        req = c.get("required")
        etq = etiqueta + (_(" (obligatorio)") if req else "")
        valor = c.get("value")
        if tipo == "fixed":
            self.caja.append(Gtk.Label(label=etiqueta, xalign=0, wrap=True))
            return
        if tipo == "hidden":
            self.controles.append((var, "hidden", None, {"_valor": valor}))
            return
        mapa = {}
        if tipo == "boolean":
            ctl = Gtk.CheckButton(label=etq, active=str(valor) in ("1", "true", "True", "yes"))
            self.caja.append(ctl)
        elif tipo == "list-single":
            opciones = c.get("opciones") or []
            ctl = Gtk.DropDown.new_from_strings([o[0] for o in opciones] or [""])
            mapa = {i: o[1] for i, o in enumerate(opciones)}
            ctl.set_selected(next((i for i, o in enumerate(opciones) if o[1] == valor), 0))
            self._etiqueta(etq, ctl)
            self.caja.append(ctl)
        elif tipo in ("list-multi", "jid-multi"):
            opciones = c.get("opciones") or []
            seleccion = valor if isinstance(valor, (list, tuple)) else ([valor] if valor else [])
            ctl = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            ctl.update_property([Gtk.AccessibleProperty.LABEL], [etq])
            self.caja.append(Gtk.Label(label=etq, xalign=0))
            for i, o in enumerate(opciones):
                chk = Gtk.CheckButton(label=o[0], active=o[1] in seleccion)
                ctl.append(chk)
                mapa[i] = (o[1], chk)
            self.caja.append(ctl)
        elif tipo == "text-multi":
            ctl = _vista_texto()
            ctl.get_buffer().set_text("\n".join(valor) if isinstance(valor, (list, tuple)) else (valor or ""))
            self._etiqueta(etq, ctl)
            self.caja.append(_marco(ctl, 100))
        elif tipo == "text-private":
            ctl = Gtk.PasswordEntry(show_peek_icon=True)
            ctl.set_text(valor if isinstance(valor, str) else "")
            self._etiqueta(etq, ctl)
            self.caja.append(ctl)
        else:
            v = valor if isinstance(valor, str) else (valor[0] if isinstance(valor, (list, tuple)) and valor else "")
            ctl = Gtk.Entry(text=v or "", hexpand=True)
            self._etiqueta(etq, ctl)
            self.caja.append(ctl)
        if c.get("desc") and hasattr(ctl, "update_property"):
            ctl.update_property([Gtk.AccessibleProperty.DESCRIPTION], [c["desc"]])
        self.controles.append((var, tipo, ctl, mapa))
        if self.primero is None:
            self.primero = ctl

    def respuestas(self) -> dict:
        res = {}
        for var, tipo, ctl, mapa in self.controles:
            if not var:
                continue
            if tipo == "hidden":
                res[var] = {"type": "hidden", "value": mapa.get("_valor")}
            elif tipo == "boolean":
                res[var] = {"type": "boolean", "value": bool(ctl.get_active())}
            elif tipo == "list-single":
                res[var] = {"type": tipo, "value": mapa.get(ctl.get_selected(), "")}
            elif tipo in ("list-multi", "jid-multi"):
                res[var] = {"type": tipo, "value": [v for v, chk in mapa.values() if chk.get_active()]}
            elif tipo == "text-multi":
                res[var] = {"type": tipo, "value": _texto_de(ctl)}
            else:
                res[var] = {"type": tipo, "value": ctl.get_text()}
        return res


class VentanaConScroll(Gtk.Window):
    """Ventana con una zona con desplazamiento arriba, una fila de botones abajo y Escape para cerrar."""

    def __init__(self, padre, titulo: str, ancho: int = 620, alto: int = 560):
        super().__init__(title=titulo, transient_for=padre, modal=True)
        self.padre = padre
        from .mnemonicos import vigilar
        vigilar(self)
        self.set_default_size(ancho, alto)
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_bottom=10, margin_start=10,
                       margin_end=10)
        self.set_child(raiz)
        self.zona = Gtk.ScrolledWindow(vexpand=True, hexpand=True, has_frame=True)
        raiz.append(self.zona)
        self.botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        raiz.append(self.botones)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)

    def boton(self, etiqueta: str, fn, defecto: bool = False):
        b = Gtk.Button(label=etiqueta, use_underline=True)
        if defecto:
            b.add_css_class("suggested-action")
        b.connect("clicked", lambda *_a: fn())
        self.botones.append(b)
        return b

    def limpiar_botones(self):
        w = self.botones.get_first_child()
        while w is not None:
            sig = w.get_next_sibling()
            self.botones.remove(w)
            w = sig


ACCIONES = {"execute": "_Ejecutar", "next": "_Siguiente", "prev": "_Anterior", "complete": "_Completar", "cancel": "_Cancelar comando"}


class DialogoFormularioXMPP(VentanaConScroll):
    """Un comando ad-hoc: cada etapa es un formulario; los botones llevan a la siguiente, la anterior o terminan."""

    def __init__(self, padre, account_id: int, jid: str, node: str, resultado: dict, titulo: str = None):
        super().__init__(padre, titulo or resultado.get("titulo") or _("Comando del servidor"))
        self.account_id, self.jid, self.node, self.resultado = account_id, jid, node, resultado
        self.campos = None
        self._pintar()

    def _pintar(self):
        r = self.resultado
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_start=6, margin_end=6)
        if r.get("instrucciones"):
            caja.append(Gtk.Label(label=r["instrucciones"], xalign=0, wrap=True, selectable=True))
        for tn, tx in r.get("notas", []):
            pref = {"error": _("Error: "), "warn": _("Aviso: ")}.get(tn, "")
            caja.append(Gtk.Label(label=pref + tx, xalign=0, wrap=True, selectable=True))
        self.campos = None
        if r.get("completado"):
            if not r.get("notas") and not r.get("campos"):
                caja.append(Gtk.Label(label=_("Comando completado."), xalign=0))
            for c in r.get("campos", []):
                etiqueta, valor = c.get("label") or c.get("var") or "", c.get("value")
                valor = ", ".join(valor) if isinstance(valor, (list, tuple)) else (valor or "")
                texto = f"{etiqueta}: {valor}" if etiqueta else str(valor)
                if texto.strip():
                    caja.append(Gtk.Label(label=texto, xalign=0, wrap=True, selectable=True))
        else:
            self.campos = CamposFormulario(r.get("campos", []))
            caja.append(self.campos.caja)
        self.zona.set_child(caja)
        self.limpiar_botones()
        acciones = r.get("acciones", [])
        if not r.get("completado"):
            mostradas = [a for a in ("prev", "next", "complete", "execute", "cancel") if a in acciones]
            if not mostradas:
                mostradas = ["complete"] if r.get("sessionid") else ["execute"]
            for a in mostradas:
                self.boton(_(ACCIONES.get(a, a)), lambda ac=a: self._accion(ac), a in ("execute", "complete", "next"))
        self.boton(_("Ce_rrar"), self.close)
        if self.campos is not None and self.campos.primero is not None:
            self.campos.primero.grab_focus()

    def _accion(self, accion: str):
        sessionid = self.resultado.get("sessionid")
        respuestas = None if accion == "cancel" else (self.campos.respuestas() if self.campos else {})

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            self.resultado = res or {}
            for tn, tx in self.resultado.get("notas", []):
                if tn == "error":
                    notificar(_("El servidor devolvió un error: {e}").format(e=tx))
            if self.resultado.get("status") == "canceled":
                notificar(_("Comando cancelado."))
            self._pintar()
        ejecutar(lambda: _servicio().ejecutar_comando(self.account_id, self.jid, self.node, action=accion,
                                                     sessionid=sessionid, respuestas=respuestas), _fin)


class DialogoComandosXMPP(DialogoLista):
    """Los comandos que ofrece un JID (el servidor, un componente o un contacto) y su ejecución."""

    def __init__(self, padre, account_id: int, jid: str = None, titulo: str = None):
        super().__init__(padre, titulo or _("Comandos del servidor"), _("Lista de comandos"), lambda pos: self._ejecutar(pos))
        self.account_id, self.jid, self.comandos = account_id, jid, []
        self.boton(_("_Ejecutar"), lambda: self._ejecutar(self.lista.posicion()))
        self.boton(_("Comandos de _otro JID…"), self._otro)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Consultando los comandos…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron listar los comandos: {e}").format(e=err))
                return
            self.comandos = res or []
            self.lista.poner([c["nombre"] for c in self.comandos])
            self.mostrar_estado(_("{n} comandos").format(n=len(self.comandos)) if self.comandos
                                else _("Este JID no ofrece comandos (o tu cuenta no tiene permiso)."))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().listar_comandos(self.account_id, self.jid), _fin)

    def _ejecutar(self, pos):
        if not 0 <= pos < len(self.comandos):
            return
        c = self.comandos[pos]

        def _fin(res, err):
            if err:
                notificar(_("No se pudo ejecutar: {e}").format(e=err))
                return
            DialogoFormularioXMPP(self, self.account_id, c["jid"], c["node"], res or {}, c["nombre"]).present()
        ejecutar(lambda: _servicio().ejecutar_comando(self.account_id, c["jid"], c["node"], action="execute"), _fin)

    def _otro(self):
        def _con(jid):
            self.jid = jid
            self._cargar()
        DialogoTexto(self, _("Comandos de otro JID"), _("_JID (servidor, componente o contacto):"), _con,
                     aceptar=_("_Ver")).present()


# --------------------------------------------------------------------------- pasarelas
class DialogoRegistroPasarela(VentanaConScroll):
    """El formulario de registro de una pasarela (XEP-0077)."""

    def __init__(self, padre, account_id: int, gateway_jid: str, nombre: str, datos: dict):
        super().__init__(padre, (_("Editar registro en {n}") if datos.get("registrado") else _("Registrarse en {n}")).format(n=nombre),
                         560, 520)
        self.account_id, self.gateway_jid, self.datos = account_id, gateway_jid, datos
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        instr = datos.get("instrucciones") or ""
        if "whatsapp" in (str(gateway_jid) + " " + str(nombre)).lower():
            instr = _("Para vincular tu cuenta de WhatsApp: pulsa «Registrarse»; después abre el chat con la pasarela, donde "
                      "llega el código QR (como texto o imagen) y, en el móvil, Ajustes, Dispositivos vinculados, Vincular un "
                      "dispositivo. También puedes emparejar por número con el comando «pair-phone» de «Comandos de la pasarela».") \
                + (("\n\n" + _("Mensaje de la pasarela:") + "\n" + instr) if instr else "")
        if instr:
            caja.append(Gtk.Label(label=instr, xalign=0, wrap=True, selectable=True))
        self.campos = CamposFormulario(datos.get("campos", []))
        caja.append(self.campos.caja)
        if not datos.get("campos"):
            caja.append(Gtk.Label(label=_("Esta pasarela no pide datos: pulsa Registrarse."), xalign=0))
        self.zona.set_child(caja)
        self.boton(_("_Registrarse"), self._registrar, True)
        self.boton(_("_Cancelar"), self.close)
        if self.campos.primero is not None:
            self.campos.primero.grab_focus()

    def _registrar(self):
        respuestas = self.campos.respuestas()

        def _fin(res, err):
            if err:
                notificar(_("No se pudo registrar: {e}").format(e=err))
                return
            notificar(_("Registro enviado. Si la pasarela pide emparejamiento (por ejemplo el código de WhatsApp), ábrelo en "
                        "«Comandos de la pasarela» o en el chat con la pasarela: el código aparece como texto."))
            self.close()
        ejecutar(lambda: _servicio().registrar_en_gateway(self.account_id, self.gateway_jid, respuestas,
                                                          self.datos.get("es_form", False)), _fin)


class DialogoPasarelasXMPP(DialogoLista):
    """Las pasarelas del servidor (WhatsApp, Telegram…): registrarse, comandos, chat y cancelar el registro."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Pasarelas del servidor"), _("Lista de pasarelas"), lambda pos: self._registrar(pos))
        self.account_id, self.gateways = account_id, []
        self.boton(_("_Registrarse…"), lambda: self._registrar(self.lista.posicion()))
        self.boton(_("_Comandos de la pasarela…"), self._comandos)
        self.boton(_("Abrir _chat con la pasarela"), self._chat)
        self.boton(_("Cancelar re_gistro…"), self._desregistrar)
        self.cerrar_boton()
        self.mostrar_estado(_("Buscando pasarelas…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron buscar pasarelas: {e}").format(e=err))
                return
            self.gateways = res or []
            self.lista.poner([_("{n}, red {r}, {j}").format(n=g["nombre"], r=g["red"] or g["tipo"] or _("desconocida"), j=g["jid"])
                              for g in self.gateways])
            self.mostrar_estado(_("{n} pasarelas").format(n=len(self.gateways)) if self.gateways else _(
                "Tu servidor no ofrece pasarelas. Necesitas un servidor con un bridge instalado (por ejemplo slidge-whatsapp)."))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().descubrir_transportes(account_id), _fin)

    def _actual(self, pos=None):
        p = self.lista.posicion() if pos is None else pos
        return self.gateways[p] if 0 <= p < len(self.gateways) else None

    def _registrar(self, pos):
        g = self._actual(pos)
        if not g:
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo obtener el registro: {e}").format(e=err))
                return
            DialogoRegistroPasarela(self, self.account_id, g["jid"], g["nombre"], res or {}).present()
        ejecutar(lambda: _servicio().formulario_registro(self.account_id, g["jid"]), _fin)

    def _comandos(self):
        g = self._actual()
        if g:
            DialogoComandosXMPP(self, self.account_id, g["jid"], _("Comandos de {n}").format(n=g["nombre"])).present()

    def _chat(self):
        g = self._actual()
        if g:
            self.close()
            self.padre._x_abrir_por_jid(self.account_id, g["jid"], g["nombre"])

    def _desregistrar(self):
        g = self._actual()
        if g:
            confirmar_async(self, _("Vas a cancelar tu registro en {n}. Dejarás de usar esa pasarela y sus contactos desaparecerán "
                                    "de tu lista. ¿Seguro?").format(n=g["nombre"]),
                            lambda: ejecutar(lambda: _servicio().desregistrar_gateway(self.account_id, g["jid"]),
                                             lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e
                                                                    else _("Registro cancelado."))),
                            etiqueta_si=_("Cancelar el registro"))


# --------------------------------------------------------------------------- buscar usuarios (XEP-0055)
class DialogoBuscarUsuariosXMPP(VentanaConScroll):
    """El formulario de búsqueda de un directorio del servidor y sus resultados; Intro agrega al contacto."""

    def __init__(self, padre, account_id: int, servicio: str):
        super().__init__(padre, _("Buscar usuarios en {s}").format(s=servicio), 640, 560)
        self.account_id, self.servicio, self.resultados, self.campos = account_id, servicio, [], None
        self.contenido = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.formulario = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.contenido.append(self.formulario)
        from .dialogos_mastodon import ListaSimple
        self.lista = ListaSimple(_("Resultados de la búsqueda"), lambda pos: self._agregar(pos))
        self.contenido.append(Gtk.Label(label=_("_Resultados (Intro agrega al contacto):"), xalign=0, use_underline=True))
        self.contenido.append(self.lista)
        self.zona.set_child(self.contenido)
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        self.contenido.append(self.estado)
        self.boton(_("_Buscar"), self._buscar, True)
        self.boton(_("_Agregar a mis contactos"), lambda: self._agregar(self.lista.posicion()))
        self.boton(_("_Cerrar"), self.close)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo abrir la búsqueda: {e}").format(e=err))
                return
            self.campos = CamposFormulario((res or {}).get("campos", []))
            self.formulario.append(self.campos.caja)
            if self.campos.primero is not None:
                self.campos.primero.grab_focus()
        ejecutar(lambda: _servicio().formulario_busqueda(account_id, servicio), _fin)

    def _buscar(self):
        if self.campos is None:
            return
        respuestas = self.campos.respuestas()
        self.estado.set_text(_("Buscando…"))

        def _fin(res, err):
            if err:
                self.estado.set_text(_("No se pudo buscar: {e}").format(e=err))
                return
            self.resultados = res or []
            self.lista.poner([f"{f.get('jid', '')}. " + ", ".join(f"{k}: {v}" for k, v in f.items() if k != "jid" and v)
                              for f in self.resultados])
            self.estado.set_text(_("{n} resultados").format(n=len(self.resultados)) if self.resultados else _("Sin resultados."))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().ejecutar_busqueda(self.account_id, self.servicio, respuestas), _fin)

    def _agregar(self, pos):
        if 0 <= pos < len(self.resultados) and self.resultados[pos].get("jid"):
            jid = self.resultados[pos]["jid"]
            ejecutar(lambda: _servicio().agregar_contacto(self.account_id, jid),
                     lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e else _("Solicitud enviada a {j}.").format(j=jid)))


# --------------------------------------------------------------------------- ficha y cuenta
CAMPOS_VCARD = [("FN", "Nombre"), ("NICKNAME", "Apodo"), ("EMAIL", "Correo"), ("TEL", "Teléfono"), ("ORG", "Organización"),
                ("TITLE", "Cargo"), ("URL", "Web"), ("BDAY", "Cumpleaños (AAAA-MM-DD)"), ("DESC", "Nota")]


class DialogoEditarFichaXMPP(DialogoBase):
    """Editar tu ficha (vCard, XEP-0054)."""

    def __init__(self, padre, account_id: int, valores: dict):
        super().__init__(padre, _("Editar mi ficha (vCard)"), _("_Guardar"))
        self.account_id, self.entradas = account_id, {}
        for clave, nombre in CAMPOS_VCARD:
            self.entradas[clave] = self.campo(f"_{_(nombre)}:", Gtk.Entry(text=valores.get(clave) or "", activates_default=True,
                                                                          width_chars=40))
        self.set_focus(self.entradas["FN"])

    def aceptar(self):
        campos = {k: e.get_text().strip() for k, e in self.entradas.items()}
        self.close()
        ejecutar(lambda: _servicio().editar_mi_vcard(self.account_id, campos),
                 lambda r, e: notificar(_("No se pudo guardar: {e}").format(e=e) if e else _("Ficha guardada.")))


def cambiar_contrasena(padre, account_id: int):
    d = DialogoBase(padre, _("Cambiar la contraseña"), _("_Cambiar"))
    nueva = d.campo(_("_Contraseña nueva:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
    d.set_focus(nueva)

    def _ok():
        p = nueva.get_text()
        if not p:
            return
        d.close()
        ejecutar(lambda: _servicio().cambiar_password(account_id, p),
                 lambda r, e: notificar(_("No se pudo: {e}").format(e=e) if e else _("Contraseña cambiada.")))
    d.aceptar = _ok
    d.present()


def crear_cuenta_nueva(padre, al_terminar):
    d = DialogoBase(padre, _("Crear cuenta nueva"), _("_Crear"))
    jid = d.campo(_("_JID de la cuenta nueva (usuario@servidor):"), Gtk.Entry(activates_default=True, width_chars=36))
    clave = d.campo(_("_Contraseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
    correo = d.campo(_("C_orreo (opcional, algunos servidores lo piden):"), Gtk.Entry(activates_default=True))
    d.set_focus(jid)

    def _ok():
        j, p, c = jid.get_text().strip(), clave.get_text(), correo.get_text().strip()
        if not (j and p):
            notificar(_("Escribe el JID y la contraseña."))
            return
        d.close()
        notificar(_("Creando la cuenta…"))

        def _fin(res, err):
            if err:
                notificar(_("No se pudo crear la cuenta: {e}").format(e=err))
                return
            notificar(_("Cuenta creada y añadida. Ya puedes conectarla desde el menú XMPP."))
            al_terminar()
        ejecutar(lambda: _servicio().registrar_cuenta_nueva(j, p, c), _fin)
    d.aceptar = _ok
    d.present()


def cancelar_cuenta_servidor(padre, account_id: int, al_terminar):
    def _hacer():
        def _fin(res, err):
            if err:
                notificar(_("No se pudo cancelar la cuenta: {e}").format(e=err))
                return
            notificar(_("Cuenta cancelada y eliminada."))
            al_terminar()
        ejecutar(lambda: _servicio().cancelar_cuenta_servidor(account_id), _fin)
    confirmar_async(padre, _("Vas a BORRAR tu cuenta del servidor de forma permanente. Se perderá en el servidor y se quitará de la "
                             "aplicación. ¿Seguro?"), _hacer, etiqueta_si=_("Borrar la cuenta"))
