# -*- coding: utf-8 -*-
"""Administración del servidor Matrix (Synapse) para la interfaz GTK 4: usuarios, salas, informes, tokens de registro y
federación. Solo funciona con un servidor Synapse y una cuenta administradora del servidor; si no, el servidor rechaza las
llamadas y aquí se muestra su mensaje tal cual."""

import logging
import time
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.plataforma import mostrar_aviso_dialogo
from app.core.speech import notificar

from .dialogos import DialogoBase, DialogoElegir, DialogoTexto, confirmar_async, ejecutar
from .dialogos_matrix import DialogoClaveMatrix
from .dialogos_mastodon import DialogoLista

log = logging.getLogger(__name__)


def _servicio():
    from app.services.matrix import matrix_service
    return matrix_service


def fecha_ms(ms) -> str:
    """Fecha local legible de un instante en milisegundos (vacío si no hay)."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y %H:%M") if ms else ""
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _tarea(fn, ok: str, despues=None):
    """Ejecuta una acción de administración en segundo plano y avisa del resultado."""
    def _fin(res, err):
        notificar(_("No se pudo: {e}").format(e=err) if err else ok)
        if not err and despues:
            despues()
    ejecutar(fn, _fin)


class DialogoSoloLectura(DialogoLista):
    """Lista de solo lectura (conexiones, dispositivos, salas o archivos de alguien)."""

    def __init__(self, padre, titulo: str, nombre_lista: str, textos, vacio: str):
        super().__init__(padre, titulo, nombre_lista)
        self.cerrar_boton()
        self.lista.poner(textos)
        self.mostrar_estado(_("{n} elementos").format(n=len(textos)) if textos else vacio)


class DialogoAdminBase(DialogoLista):
    """Lista con un campo de búsqueda del lado del servidor y botones de acción."""

    def __init__(self, padre, account_id: int, titulo: str, nombre_lista: str, etiqueta_busqueda: str, al_activar=None):
        super().__init__(padre, titulo, nombre_lista, al_activar=al_activar)
        self.account_id = account_id
        self.items = []
        caja = self.get_child()
        fila = Gtk.Box(spacing=8)
        etiqueta = Gtk.Label(label=etiqueta_busqueda, use_underline=True)
        self.busqueda = Gtk.Entry(hexpand=True)
        etiqueta.set_mnemonic_widget(self.busqueda)
        self.busqueda.connect("activate", lambda *_a: self.cargar())
        fila.append(etiqueta)
        fila.append(self.busqueda)
        caja.insert_child_after(fila, None)
        self.boton(_("_Buscar"), self.cargar)

    def actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def cargar(self):
        self.mostrar_estado(_("Consultando el servidor…"))
        q = self.busqueda.get_text().strip()

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo (¿eres administrador del servidor?): {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([self.texto(i) for i in self.items])
            self.mostrar_estado(_("{n} resultados").format(n=len(self.items)) if self.items else _("No hay nada que mostrar"))
            self.lista.enfocar()
        ejecutar(lambda: self.consulta(q), _fin)

    def consulta(self, q: str):
        raise NotImplementedError

    def texto(self, item) -> str:
        raise NotImplementedError


class DialogoAdminUsuarios(DialogoAdminBase):
    ACCIONES = [("conexiones", "Ver sus conexiones (IP y sesiones)"), ("salas", "Ver sus salas"),
                ("dispositivos", "Ver y cerrar sus dispositivos"), ("archivos", "Ver sus archivos subidos"),
                ("directo", "Mensaje directo"), ("ratelimit", "Límite de mensajes…"),
                ("sombra", "Sombra-baneo (parece que envía, pero no llega)…"), ("admin", "Hacer o quitar administrador del servidor"),
                ("bloqueo", "Bloquear o desbloquear la cuenta"), ("clave", "Cambiar la contraseña…"),
                ("desactivar", "Desactivar la cuenta…")]

    def __init__(self, padre, account_id: int, al_directo=None):
        super().__init__(padre, account_id, _("Usuarios del servidor"), _("Lista de usuarios del servidor"),
                         _("_Buscar cuenta:"), al_activar=lambda pos: self._detalles())
        self.al_directo = al_directo
        self.boton(_("_Detalles"), self._detalles)
        self.boton(_("_Acciones…"), self._acciones)
        self.cerrar_boton()
        self.cargar()

    def consulta(self, q):
        return _servicio().admin_listar_usuarios(self.account_id, q)

    def texto(self, u):
        marcas = [m for m, c in ((_("administrador"), u["admin"]), (_("desactivado"), u["desactivado"]),
                                 (_("bloqueado"), u["bloqueado"]), (_("invitado"), u["invitado"])) if c]
        return f"{u['nombre'] or u['user_id']} ({u['user_id']})" + (", " + ", ".join(marcas) if marcas else "")

    def _detalles(self):
        u = self.actual()
        if not u:
            return

        def _fin(d, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            d = d or {}
            lineas = [_("Usuario: {u}").format(u=u["user_id"]),
                      _("Nombre visible: {n}").format(n=d.get("displayname") or _("(sin nombre)")),
                      _("Administrador del servidor: {s}").format(s=_("sí") if d.get("admin") else _("no")),
                      _("Desactivado: {s}").format(s=_("sí") if d.get("deactivated") else _("no")),
                      _("Creado: {f}").format(f=fecha_ms(d.get("creation_ts") and int(d["creation_ts"]) * 1000) or "?")]
            correos = [t.get("address") for t in (d.get("threepids") or []) if t.get("address")]
            if correos:
                lineas.append(_("Correos o teléfonos: {c}").format(c=", ".join(correos)))
            mostrar_aviso_dialogo("\n".join(lineas), _("Detalles de {u}").format(u=u["user_id"]))
        ejecutar(lambda: _servicio().admin_detalle_usuario(self.account_id, u["user_id"]), _fin)

    def _acciones(self):
        u = self.actual()
        if not u:
            return

        def _elegida(i):
            getattr(self, "_a_" + self.ACCIONES[i][0])(u)
        DialogoElegir(self, _("Acciones sobre {u}").format(u=u["user_id"]), _("_Acción:"), [_(a[1]) for a in self.ACCIONES],
                      _elegida, aceptar=_("_Hacerlo")).present()

    def _lista(self, titulo, nombre, funcion, formato, vacio):
        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            DialogoSoloLectura(self, titulo, nombre, [formato(x) for x in (res or [])], vacio).present()
        ejecutar(funcion, _fin)

    def _a_conexiones(self, u):
        self._lista(_("Conexiones de {u}").format(u=u["user_id"]), _("Lista de conexiones"),
                    lambda: _servicio().admin_whois(self.account_id, u["user_id"]),
                    lambda c: _("Dispositivo {d}, IP {i}, {f}, {n}").format(d=c["dispositivo"], i=c["ip"], f=fecha_ms(c["ultima_vez"]) or "?",
                                                                            n=c["navegador"]), _("Sin conexiones registradas"))

    def _a_salas(self, u):
        self._lista(_("Salas de {u}").format(u=u["user_id"]), _("Lista de salas"),
                    lambda: _servicio().admin_salas_de_usuario(self.account_id, u["user_id"]), str, _("No participa en ninguna sala"))

    def _a_dispositivos(self, u):
        DialogoAdminDispositivos(self, self.account_id, u["user_id"]).present()

    def _a_archivos(self, u):
        self._lista(_("Archivos de {u}").format(u=u["user_id"]), _("Lista de archivos"),
                    lambda: _servicio().admin_archivos_usuario(self.account_id, u["user_id"]),
                    lambda m: _("{n}, {t}, {k} KB, {f}{q}").format(n=m["nombre"] or m["media_id"], t=m["tipo"], k=m["tamano"] // 1024,
                                                                   f=fecha_ms(m["creado"]),
                                                                   q=_(" (en cuarentena)") if m["cuarentena"] else ""),
                    _("No ha subido archivos"))

    def _a_directo(self, u):
        if self.al_directo:
            self.close()
            self.al_directo(u["user_id"])

    def _a_ratelimit(self, u):
        def _ver(actual):
            texto = (_("Ahora: {m} mensajes por segundo y ráfaga de {b}.").format(m=actual.get("messages_per_second"), b=actual.get("burst_count"))
                     if actual else _("Ahora usa el límite del servidor."))

            def _elegida(i):
                if i == 1:
                    return _tarea(lambda: _servicio().admin_quitar_ratelimit(self.account_id, u["user_id"]),
                                  _("Límite propio quitado"))
                if i == 2:
                    return _tarea(lambda: _servicio().admin_fijar_ratelimit(self.account_id, u["user_id"], 0, 0), _("Sin límite para este usuario"))
                DialogoTexto(self, _("Límite de mensajes"), _("_Mensajes por segundo (por ejemplo 2):"), lambda v: _tarea(
                    lambda: _servicio().admin_fijar_ratelimit(self.account_id, u["user_id"], int(float(v)), max(1, int(float(v)) * 5)),
                    _("Límite fijado"))).present()
            DialogoElegir(self, _("Límite de mensajes de {u}").format(u=u["user_id"]), texto + " " + _("_Qué hacer:"),
                          [_("Fijar un límite…"), _("Quitar el límite propio"), _("Sin ningún límite")], _elegida, aceptar=_("_Aplicar")).present()
        ejecutar(lambda: _servicio().admin_ver_ratelimit(self.account_id, u["user_id"]), lambda r, e: _ver(r or {}))

    def _a_sombra(self, u):
        def _elegida(i):
            _tarea(lambda: _servicio().admin_shadow_ban(self.account_id, u["user_id"], banear=(i == 0)),
                   _("Sombra-baneo puesto") if i == 0 else _("Sombra-baneo quitado"))
        DialogoElegir(self, _("Sombra-baneo de {u}").format(u=u["user_id"]), _("_Qué hacer:"),
                      [_("Aplicar el sombra-baneo"), _("Quitar el sombra-baneo")], _elegida, aceptar=_("_Aplicar")).present()

    def _a_admin(self, u):
        nuevo = not u["admin"]
        confirmar_async(self, (_("¿Hacer a {u} administrador del servidor? Tendrá poder total sobre él.") if nuevo
                               else _("¿Quitar a {u} el rol de administrador del servidor?")).format(u=u["user_id"]),
                        lambda: _tarea(lambda: _servicio().admin_fijar_admin(self.account_id, u["user_id"], nuevo),
                                       _("Cambio hecho"), self.cargar), etiqueta_si=_("Continuar"))

    def _a_bloqueo(self, u):
        nuevo = not u["bloqueado"]
        _tarea(lambda: _servicio().admin_bloquear_usuario(self.account_id, u["user_id"], nuevo),
               _("Cuenta bloqueada") if nuevo else _("Cuenta desbloqueada"), self.cargar)

    def _a_clave(self, u):
        DialogoClaveMatrix(self, _("Cambiar la contraseña de {u}").format(u=u["user_id"]), _("_Contraseña nueva:"),
                           lambda c: _tarea(lambda: _servicio().admin_resetear_password(self.account_id, u["user_id"], c),
                                            _("Contraseña cambiada; se han cerrado sus sesiones"))).present()

    def _a_desactivar(self, u):
        def _elegida(i):
            borrar = i == 1
            confirmar_async(self, _("Desactivar la cuenta {u} no se puede deshacer{b}. ¿Continuar?").format(
                u=u["user_id"], b=_(" y borrará sus datos") if borrar else ""),
                lambda: _tarea(lambda: _servicio().admin_desactivar_usuario(self.account_id, u["user_id"], borrar),
                               _("Cuenta desactivada"), self.cargar), etiqueta_si=_("Desactivar"))
        DialogoElegir(self, _("Desactivar {u}").format(u=u["user_id"]), _("_Qué hacer:"),
                      [_("Solo desactivar la cuenta"), _("Desactivar y borrar sus datos")], _elegida, aceptar=_("_Continuar")).present()


class DialogoAdminDispositivos(DialogoLista):
    """Sesiones de otra cuenta; se puede cerrar la que quieras."""

    def __init__(self, padre, account_id: int, user_id: str):
        super().__init__(padre, _("Dispositivos de {u}").format(u=user_id), _("Lista de dispositivos"))
        self.account_id, self.user_id = account_id, user_id
        self.dispositivos = []
        self.boton(_("_Cerrar la sesión"), self._cerrar_sesion)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo: {e}").format(e=err))
                return
            self.dispositivos = res or []
            self.lista.poner([_("{i}, {n}, IP {ip}, {f}").format(i=d["id"], n=d["nombre"] or _("(sin nombre)"), ip=d["ip"] or "?",
                                                                 f=fecha_ms(d["ultima_vez"]) or "?") for d in self.dispositivos])
            self.mostrar_estado(_("{n} dispositivos").format(n=len(self.dispositivos)) if self.dispositivos else _("Sin dispositivos"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().admin_dispositivos_usuario(self.account_id, self.user_id), _fin)

    def _cerrar_sesion(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.dispositivos):
            d = self.dispositivos[p]
            confirmar_async(self, _("¿Cerrar la sesión {i} de {u}?").format(i=d["id"], u=self.user_id),
                            lambda: _tarea(lambda: _servicio().admin_borrar_dispositivo_usuario(self.account_id, self.user_id, d["id"]),
                                           _("Sesión cerrada"), self._refrescar), etiqueta_si=_("Cerrar"))


class DialogoAdminSalas(DialogoAdminBase):
    ACCIONES = [("admin", "Hacerme administrador de la sala"), ("purgar", "Purgar el historial antiguo…"),
                ("bloqueo", "Bloquear o desbloquear la sala"), ("cuarentena", "Poner sus archivos en cuarentena…"),
                ("borrar", "Borrar la sala del servidor…")]

    def __init__(self, padre, account_id: int):
        super().__init__(padre, account_id, _("Salas del servidor"), _("Lista de salas del servidor"), _("_Buscar sala:"),
                         al_activar=lambda pos: self._detalles())
        self.boton(_("_Detalles"), self._detalles)
        self.boton(_("_Acciones…"), self._acciones)
        self.cerrar_boton()
        self.cargar()

    def consulta(self, q):
        return _servicio().admin_listar_salas(self.account_id, q)

    def texto(self, s):
        marcas = [m for m, c in ((_("cifrada"), s["cifrada"]), (_("pública"), s["publica"])) if c]
        return _("{n}, {m} miembros").format(n=s["nombre"], m=s["miembros"]) + (", " + ", ".join(marcas) if marcas else "")

    def _detalles(self):
        s = self.actual()
        if not s:
            return

        def _fin(d, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            d = d or {}
            lineas = [_("Sala: {n}").format(n=d.get("name") or s["nombre"]), _("Identificador: {i}").format(i=s["room_id"]),
                      _("Alias: {a}").format(a=d.get("canonical_alias") or _("(ninguno)")),
                      _("Creador: {c}").format(c=d.get("creator") or "?"),
                      _("Miembros: {m} (locales {l})").format(m=d.get("joined_members"), l=d.get("joined_local_members")),
                      _("Versión: {v}").format(v=d.get("version") or "?"),
                      _("Cifrada: {s}").format(s=d.get("encryption") or _("no")),
                      _("Pública: {s}").format(s=_("sí") if d.get("public") else _("no"))]
            mostrar_aviso_dialogo("\n".join(lineas), _("Detalles de {n}").format(n=s["nombre"]))
        ejecutar(lambda: _servicio().admin_detalle_sala(self.account_id, s["room_id"]), _fin)

    def _acciones(self):
        s = self.actual()
        if not s:
            return

        def _elegida(i):
            getattr(self, "_a_" + self.ACCIONES[i][0])(s)
        DialogoElegir(self, _("Acciones sobre {n}").format(n=s["nombre"]), _("_Acción:"), [_(a[1]) for a in self.ACCIONES],
                      _elegida, aceptar=_("_Hacerlo")).present()

    def _a_admin(self, s):
        _tarea(lambda: _servicio().admin_hacerme_admin_sala(self.account_id, s["room_id"]), _("Ya eres administrador de la sala"))

    def _a_purgar(self, s):
        def _dias(texto):
            try:
                dias = max(1, int(float(texto)))
            except ValueError:
                notificar(_("Escribe un número de días"))
                return
            antes = int((time.time() - dias * 86400) * 1000)
            confirmar_async(self, _("Se borrará de forma definitiva el historial de «{n}» anterior a hace {d} días. ¿Continuar?")
                            .format(n=s["nombre"], d=dias),
                            lambda: ejecutar(lambda: _servicio().admin_purgar_historial(self.account_id, s["room_id"], antes),
                                             lambda pid, e: notificar(_("No se pudo: {e}").format(e=e) if e
                                                                      else _("Purga iniciada en el servidor"))), etiqueta_si=_("Purgar"))
        d = DialogoTexto(self, _("Purgar el historial"), _("Borrar lo anterior a hace cuántos _días:"), _dias, aceptar=_("_Continuar"))
        d.entrada.set_text("30")
        d.present()

    def _a_bloqueo(self, s):
        def _con_estado(bloqueada, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            nuevo = not bloqueada
            confirmar_async(self, (_("¿Bloquear la sala {n}? Nadie nuevo podrá entrar.") if nuevo else _("¿Desbloquear la sala {n}?")).format(n=s["nombre"]),
                            lambda: _tarea(lambda: _servicio().admin_bloquear_sala(self.account_id, s["room_id"], nuevo),
                                           _("Sala bloqueada") if nuevo else _("Sala desbloqueada")), etiqueta_si=_("Continuar"))
        ejecutar(lambda: _servicio().admin_sala_bloqueada(self.account_id, s["room_id"]), _con_estado)

    def _a_cuarentena(self, s):
        confirmar_async(self, _("Los archivos de «{n}» dejarán de servirse a los usuarios (sin borrarse del disco). ¿Continuar?").format(n=s["nombre"]),
                        lambda: ejecutar(lambda: _servicio().admin_cuarentena_media_sala(self.account_id, s["room_id"]),
                                         lambda n, e: notificar(_("No se pudo: {e}").format(e=e) if e
                                                                else _("{n} archivos en cuarentena").format(n=n))), etiqueta_si=_("Continuar"))

    def _a_borrar(self, s):
        def _elegida(i):
            bloquear = i == 1
            confirmar_async(self, _("Se echará a todos de «{n}» y se borrará su historia{b}. No se puede deshacer. ¿Continuar?").format(
                n=s["nombre"], b=_(", y se impedirá que vuelva a crearse") if bloquear else ""),
                lambda: _tarea(lambda: _servicio().admin_borrar_sala(self.account_id, s["room_id"], bloquear=bloquear, purgar=True),
                               _("Sala borrada"), self.cargar), etiqueta_si=_("Borrar"))
        DialogoElegir(self, _("Borrar {n}").format(n=s["nombre"]), _("_Qué hacer:"),
                      [_("Borrar la sala"), _("Borrar y bloquear que vuelva a crearse")], _elegida, aceptar=_("_Continuar")).present()


class DialogoAdminInformes(DialogoLista):
    """Mensajes que los usuarios han reportado, con su motivo; Intro muestra el detalle."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mensajes reportados"), _("Lista de mensajes reportados"), al_activar=lambda pos: self._detalle())
        self.account_id = account_id
        self.informes = []
        self.boton(_("_Detalle"), self._detalle)
        self.boton(_("_Actualizar"), self._refrescar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        self.mostrar_estado(_("Consultando el servidor…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo (¿eres administrador del servidor?): {e}").format(e=err))
                return
            self.informes = res or []
            self.lista.poner([_("{f}: {q} reportó un mensaje de {a} en {s}. Motivo: {m}").format(
                f=fecha_ms(i["fecha"]), q=i["quien_reporta"], a=i["autor"], s=i["sala"], m=i["motivo"] or _("(sin motivo)")) for i in self.informes])
            self.mostrar_estado(_("{n} reportes").format(n=len(self.informes)) if self.informes else _("No hay reportes"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().admin_listar_informes(self.account_id), _fin)

    def _detalle(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.informes)):
            return
        i = self.informes[p]

        def _fin(d, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            d = d or {}
            contenido = ((d.get("event_json") or {}).get("content") or {}).get("body") or _("(sin texto)")
            mostrar_aviso_dialogo("\n".join([_("Reportado por: {q}").format(q=i["quien_reporta"]), _("Autor: {a}").format(a=i["autor"]),
                                             _("Sala: {s}").format(s=i["sala"]), _("Motivo: {m}").format(m=i["motivo"] or _("(sin motivo)")),
                                             _("Mensaje: {t}").format(t=contenido)]), _("Detalle del reporte"))
        ejecutar(lambda: _servicio().admin_detalle_informe(self.account_id, i["id"]), _fin)


class DialogoAdminTokens(DialogoLista):
    """Tokens de registro para servidores de registro restringido."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Tokens de registro"), _("Lista de tokens de registro"))
        self.account_id = account_id
        self.tokens = []
        self.boton(_("_Crear un token…"), self._crear)
        self.boton(_("_Borrar el token"), self._borrar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo (¿eres administrador del servidor?): {e}").format(e=err))
                return
            self.tokens = res or []
            self.lista.poner([_("{t}, usos {h} de {p}{c}").format(
                t=t["token"], h=t["usos_hechos"], p=t["usos_permitidos"] if t["usos_permitidos"] is not None else _("ilimitados"),
                c=_(", caduca {f}").format(f=fecha_ms(t["caduca"])) if t["caduca"] else "") for t in self.tokens])
            self.mostrar_estado(_("{n} tokens").format(n=len(self.tokens)) if self.tokens else _("No hay tokens"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().admin_listar_tokens_registro(self.account_id), _fin)

    def _crear(self):
        def _usos(texto):
            try:
                usos = int(texto) if texto.strip() else None
            except ValueError:
                notificar(_("Escribe un número o déjalo vacío"))
                return
            ejecutar(lambda: _servicio().admin_crear_token_registro(self.account_id, usos),
                     lambda tok, e: (notificar(_("No se pudo: {e}").format(e=e) if e else _("Token creado: {t}").format(t=tok)),
                                     self._refrescar()))
        DialogoUsosToken(self, _usos).present()

    def _borrar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.tokens):
            token = self.tokens[p]["token"]
            confirmar_async(self, _("¿Borrar el token {t}?").format(t=token),
                            lambda: _tarea(lambda: _servicio().admin_borrar_token_registro(self.account_id, token),
                                           _("Token borrado"), self._refrescar), etiqueta_si=_("Borrar"))


class DialogoUsosToken(DialogoBase):
    """Cuántas veces se podrá usar un token de registro nuevo (vacío = sin límite)."""

    def __init__(self, padre, al_aceptar):
        super().__init__(padre, _("Crear un token de registro"), _("_Crear"))
        self.al_aceptar = al_aceptar
        self.usos = self.campo(_("_Usos permitidos (vacío = ilimitados):"), Gtk.Entry(activates_default=True, width_chars=12))
        self.set_focus(self.usos)

    def aceptar(self):
        texto = self.usos.get_text()
        self.close()
        self.al_aceptar(texto)


class DialogoAdminFederacion(DialogoLista):
    """Servidores remotos con los que federa el tuyo y su estado de conexión."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Federación"), _("Lista de servidores remotos"))
        self.account_id = account_id
        self.destinos = []
        self.boton(_("_Reintentar la conexión ahora"), self._reintentar)
        self.boton(_("_Actualizar"), self._refrescar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo (¿eres administrador del servidor?): {e}").format(e=err))
                return
            self.destinos = res or []
            self.lista.poner([d["destino"] + (_(", con fallos desde {f}").format(f=fecha_ms(d["fallos_desde"])) if d["fallos_desde"]
                                              else _(", sin problemas")) for d in self.destinos])
            self.mostrar_estado(_("{n} servidores").format(n=len(self.destinos)) if self.destinos else _("Este servidor no federa con ninguno"))
            self.lista.enfocar()
        ejecutar(lambda: _servicio().admin_federacion_destinos(self.account_id), _fin)

    def _reintentar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.destinos):
            d = self.destinos[p]["destino"]
            _tarea(lambda: _servicio().admin_federacion_reset(self.account_id, d), _("Reintentando la conexión con {d}").format(d=d),
                   self._refrescar)


class DialogoAvisoServidor(DialogoBase):
    """Envía un aviso del servidor a un usuario (le llega como mensaje directo de la cuenta de avisos)."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Aviso del servidor"), _("_Enviar"))
        self.account_id = account_id
        self.usuario = self.campo(_("_Usuario destino (@usuario:servidor):"), Gtk.Entry(activates_default=True, width_chars=36))
        self.mensaje = self.campo(_("_Mensaje del aviso:"), Gtk.Entry(activates_default=True, width_chars=50))
        self.set_focus(self.usuario)

    def aceptar(self):
        uid, texto = self.usuario.get_text().strip(), self.mensaje.get_text().strip()
        if not uid or not texto:
            notificar(_("Escribe el usuario y el mensaje"))
            return
        self.close()
        ejecutar(lambda: _servicio().admin_enviar_aviso_servidor(self.account_id, uid, texto),
                 lambda r, e: notificar(_("No se pudo enviar (¿no eres administrador o el servidor no tiene avisos de servidor?): {e}").format(e=e)
                                        if e else _("Aviso del servidor enviado a {u}").format(u=uid)))
