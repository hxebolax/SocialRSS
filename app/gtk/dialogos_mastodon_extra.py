# -*- coding: utf-8 -*-
"""Mastodon, parte avanzada: hilos propios, notificaciones agrupadas y su política, y administración
de la instancia (informes, cuentas y dominios bloqueados)."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.mastodon.mastodon_threading import MastodonThreadingManager

from .dialogos import DialogoBase, VISIBILIDADES, confirmar_async
from .dialogos_mastodon import DialogoLista, DialogoHistorial

log = logging.getLogger(__name__)

NUMERACIONES = [("simple", "1/N"), ("parentesis", "(1/N)"), ("hilo", "Hilo 1/N"), ("parte", "Parte 1 de N"),
                ("sin", "Sin numerar")]


class DialogoTextoLargo(DialogoHistorial):
    """Texto de solo lectura con título propio (se reutiliza el visor del historial)."""

    def __init__(self, padre, titulo: str, texto: str):
        super().__init__(padre, [])
        self.set_title(titulo)
        tv = self.get_child().get_child()
        tv.get_buffer().set_text(texto)
        tv.update_property([Gtk.AccessibleProperty.LABEL], [titulo])


# --------------------------------------------------------------------------- hilos propios
class DialogoHilo(DialogoBase):
    """Escribe un texto largo y lo publica como hilo: se divide en mensajes que responden al anterior."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Nuevo hilo"), _("_Publicar el hilo"))
        self.account_id = account_id
        self.limite = mastodon_service.obtener_limite_caracteres(account_id)
        self.set_default_size(680, -1)
        self.texto = Gtk.TextView(accepts_tab=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6,
                                  top_margin=6, bottom_margin=6)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=220, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Texto del hilo:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.resumen = Gtk.Label(xalign=0)
        self.rejilla.attach(self.resumen, 1, self._fila, 1, 1)
        self._fila += 1
        self.numeracion = self.campo(_("_Numeración:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in NUMERACIONES]))
        self.aviso = self.campo(_("_Aviso de contenido (opcional):"), Gtk.Entry())
        self.visibilidad = self.campo(_("_Visibilidad:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in VISIBILIDADES]))
        pred = mastodon_service.visibilidad_predeterminada(account_id)
        self.visibilidad.set_selected(next((i for i, (c, _n) in enumerate(VISIBILIDADES) if c == pred), 0))
        fila = Gtk.Box(spacing=8)
        b1 = Gtk.Button(label=_("_Ver las partes"), use_underline=True)
        b1.connect("clicked", lambda *_a: self._ver_partes())
        b2 = Gtk.Button(label=_("_Guardar sin publicar"), use_underline=True)
        b2.connect("clicked", lambda *_a: self._guardar())
        fila.append(b1)
        fila.append(b2)
        self.rejilla.attach(fila, 1, self._fila, 1, 1)
        self._fila += 1
        self.texto.get_buffer().connect("changed", lambda *_a: self._actualizar_resumen())
        self.set_focus(self.texto)
        self._actualizar_resumen()

    def _contenido(self) -> str:
        b = self.texto.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False).strip()

    def _partes(self):
        return MastodonThreadingManager.dividir_hilo(
            self._contenido(), limite_max=self.limite, numerar=NUMERACIONES[self.numeracion.get_selected()][0] != "sin",
            formato=NUMERACIONES[self.numeracion.get_selected()][0], cw=self.aviso.get_text().strip())

    def _actualizar_resumen(self):
        n = len(self._contenido())
        self.resumen.set_text(_("{n} caracteres; límite por mensaje {l}").format(n=n, l=self.limite))

    def _ver_partes(self):
        partes = self._partes()
        if not partes:
            notificar(_("Escribe el texto del hilo"))
            return
        DialogoTextoLargo(self, _("{n} mensajes").format(n=len(partes)),
                          "\n\n".join(_("Mensaje {i}:").format(i=i) + "\n" + p for i, p in enumerate(partes, 1))).present()

    def _preparar(self):
        partes = self._partes()
        if not partes:
            notificar(_("Escribe el texto del hilo"))
            return None
        titulo = " ".join(self._contenido().split())[:50]
        visib = VISIBILIDADES[self.visibilidad.get_selected()][0]
        aviso = self.aviso.get_text().strip()
        tid = MastodonThreadingManager.guardar_hilo(None, titulo, self.account_id, [{"content": p} for p in partes],
                                                    visibility=visib, spoiler_text=aviso)
        return tid, visib, aviso, len(partes)

    def _guardar(self):
        r = self._preparar()
        if r:
            notificar(_("Hilo guardado: {n} mensajes").format(n=r[3]))
            self.close()

    def aceptar(self):
        r = self._preparar()
        if not r:
            return
        tid, visib, aviso, n = r
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Publicando el hilo de {n} mensajes…").format(n=n))
        publicar_hilo(self.padre, self.account_id, tid, visib, aviso, al_terminar=self.close,
                      al_fallar=lambda: self.boton_aceptar.set_sensitive(True))


def publicar_hilo(ventana, account_id, tid, visib, aviso, al_terminar=None, al_fallar=None):
    gestor = MastodonThreadingManager()

    def _fin(ok, detalle):
        if not ok:
            notificar(_("El hilo no se pudo publicar entero: {e}. Puedes reanudarlo desde Hilos guardados.").format(e=detalle))
            if al_fallar:
                al_fallar()
            return
        notificar(_("Hilo publicado"))
        if al_terminar:
            al_terminar()
        ventana.tras_publicar()

    def _progreso(pct, msg):
        ventana.estado.set_text(msg)
    gestor.publicar_hilo_async(account_id, tid, visibilidad=visib, sensitive=bool(aviso), spoiler_text=aviso or None,
                               callback=_fin, progress_callback=_progreso)


class DialogoHilos(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Hilos guardados"), _("Lista de hilos guardados"))
        self.account_id = account_id
        self.boton(_("_Publicar o reanudar"), self._publicar)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._refrescar()

    def _refrescar(self):
        self.items = MastodonThreadingManager.obtener_hilos(self.account_id)
        self.lista.poner([f"{h['title']} — {h['updated_at'][:16]}" + (_(". Publicado") if h["published"] else _(". Sin publicar"))
                          for h in self.items])
        self.mostrar_estado(_("{n} hilos").format(n=len(self.items)) if self.items else _("No hay hilos guardados"))

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _publicar(self):
        h = self._actual()
        if not h:
            return
        hilo = MastodonThreadingManager.cargar_hilo(h["id"])
        if not hilo:
            return
        self.close()
        notificar(_("Publicando el hilo…"))
        publicar_hilo(self.padre, self.account_id, h["id"], hilo["visibility"], hilo["spoiler_text"])

    def _borrar(self):
        h = self._actual()
        if not h:
            return

        def _hacer():
            MastodonThreadingManager.eliminar_hilo(h["id"])
            notificar(_("Hilo borrado"))
            self._refrescar()
            self.lista.enfocar()
        confirmar_async(self, _("¿Borrar el hilo «{t}»?").format(t=h["title"]), _hacer, etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- notificaciones
TIPOS_GRUPO = {"favourite": "favoritos", "reblog": "impulsos", "follow": "seguidores nuevos", "mention": "menciones",
               "poll": "encuestas terminadas", "status": "publicaciones", "update": "ediciones",
               "follow_request": "solicitudes de seguimiento", "quote": "citas"}


class DialogoNotifAgrupadas(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Notificaciones agrupadas"), _("Lista de grupos de notificaciones"))
        self.account_id, self.grupos = account_id, []
        self.boton(_("_Ver las cuentas"), self._ver_cuentas)
        self.boton(_("_Descartar el grupo"), self._descartar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.grupos = res.get("grupos", [])
            textos = []
            for g in self.grupos:
                tipo = _(TIPOS_GRUPO.get(g["tipo"], g["tipo"]))
                autores = ", ".join(g["autores"])
                t = _("{n} {tipo} de {a}").format(n=g["total"], tipo=tipo, a=autores) if autores else f"{g['total']} {tipo}"
                if g["extracto"]:
                    t += ": " + " ".join(g["extracto"].split())
                textos.append(t)
            self.lista.poner(textos)
            self.mostrar_estado(_("{n} grupos").format(n=len(self.grupos)) if self.grupos else _("No hay notificaciones"))
            self.lista.enfocar()
        mastodon_service.obtener_notificaciones_agrupadas_async(self.account_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.grupos[p] if 0 <= p < len(self.grupos) else None

    def _ver_cuentas(self):
        g = self._actual()
        if not g or not g["group_key"]:
            notificar(_("Este servidor no permite ver las cuentas de un grupo"))
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            DialogoTextoLargo(self, _("Cuentas del grupo"), "\n".join(f"{a['nombre']} (@{a['acct']})" for a in res)).present()
        mastodon_service.cuentas_grupo_notif_async(self.account_id, g["group_key"], callback=_fin)

    def _descartar(self):
        g = self._actual()
        if not g or not g["group_key"]:
            notificar(_("Este servidor no permite descartar un grupo"))
            return

        def _fin(res, err):
            notificar(_("No se pudo: {e}").format(e=err) if err else _("Grupo descartado"))
            self._cargar()
        mastodon_service.descartar_grupo_notif_async(self.account_id, g["group_key"], callback=_fin)


POLITICAS = [("for_not_following", "Personas a las que no sigues"), ("for_not_followers", "Personas que no te siguen"),
             ("for_new_accounts", "Cuentas nuevas"), ("for_private_mentions", "Menciones privadas que no pediste"),
             ("for_limited_accounts", "Cuentas limitadas por moderación")]
ACCIONES_POLITICA = [("accept", "Aceptar"), ("filter", "Filtrar a solicitudes"), ("drop", "Ignorar")]


class DialogoPoliticaNotif(DialogoBase):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Política de notificaciones"), _("_Guardar"))
        self.account_id = account_id
        self.combos = []
        for clave, nombre in POLITICAS:
            c = self.campo(_(nombre) + ":", Gtk.DropDown.new_from_strings([_(n) for _k, n in ACCIONES_POLITICA]))
            self.combos.append((clave, c))
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo cargar: {e}").format(e=err))
                return
            for clave, c in self.combos:
                v = res.get(clave)
                if isinstance(v, str):
                    c.set_selected(next((i for i, (k, _n) in enumerate(ACCIONES_POLITICA) if k == v), 0))
            self.boton_aceptar.set_sensitive(True)
        mastodon_service.obtener_politica_notificaciones_async(account_id, callback=_fin)

    def aceptar(self):
        opciones = {clave: ACCIONES_POLITICA[c.get_selected()][0] for clave, c in self.combos}

        def _fin(res, err):
            notificar(_("No se pudo guardar: {e}").format(e=err) if err else _("Política guardada"))
            if not err:
                self.close()
        mastodon_service.fijar_politica_notificaciones_async(self.account_id, opciones, callback=_fin)


class DialogoSolicitudesNotif(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Solicitudes de notificaciones"), _("Lista de solicitudes de notificaciones"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Aceptar"), lambda: self._resolver(True))
        self.boton(_("_Descartar"), lambda: self._resolver(False))
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([_("{n} (@{a}): {c} notificaciones").format(n=s["nombre"] or s["autor"], a=s["autor"], c=s["cantidad"])
                              for s in self.items])
            self.mostrar_estado(_("{n} solicitudes").format(n=len(self.items)) if self.items else _("No hay solicitudes"))
            self.lista.enfocar()
        mastodon_service.solicitudes_notificacion_async(self.account_id, callback=_fin)

    def _resolver(self, aceptar: bool):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return

        def _fin(res, err):
            notificar(_("No se pudo: {e}").format(e=err) if err else (_("Solicitud aceptada") if aceptar else _("Solicitud descartada")))
            self._cargar()
        (mastodon_service.aceptar_solicitud_notif_async if aceptar else mastodon_service.descartar_solicitud_notif_async)(
            self.account_id, self.items[p]["id"], callback=_fin)


# --------------------------------------------------------------------------- administración
def _error_admin(err) -> str:
    texto = str(err)
    if "403" in texto or "forbidden" in texto.lower() or "permission" in texto.lower():
        return _("Tu cuenta no tiene permisos de administración en esta instancia.")
    return _("No se pudo cargar: {e}").format(e=err)


class DialogoInformes(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Informes de moderación"), _("Lista de informes"))
        self.account_id, self.items, self.resueltos = account_id, [], False
        self.boton(_("_Resolver"), lambda: self._accion("resolver"))
        self.boton(_("_Asignármelo"), lambda: self._accion("asignar"))
        self.boton(_("Re_abrir"), lambda: self._accion("reabrir"))
        self.b_alternar = self.boton(_("Ver r_esueltos"), self._alternar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_error_admin(err))
                return
            self.items = res or []
            self.lista.poner([f"#{i['id']} @{i['objetivo']}: {i['categoria']}. {' '.join(i['comentario'].split())}" for i in self.items])
            self.mostrar_estado((_("{n} informes resueltos") if self.resueltos else _("{n} informes abiertos")).format(n=len(self.items)))
            self.lista.enfocar()
        mastodon_service.admin_listar_informes_async(self.account_id, resueltos=self.resueltos, callback=_fin)

    def _alternar(self):
        self.resueltos = not self.resueltos
        self.b_alternar.set_label(_("Ver a_biertos") if self.resueltos else _("Ver r_esueltos"))
        self._cargar()

    def _accion(self, accion):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return

        def _fin(res, err):
            notificar(_("No se pudo: {e}").format(e=err) if err else _("Hecho"))
            self._cargar()
        mastodon_service.admin_accion_informe_async(self.account_id, self.items[p]["id"], accion, callback=_fin)


ESTADOS_CUENTA = [("active", "Activas"), ("pending", "Pendientes de aprobar"), ("silenced", "Silenciadas"),
                  ("suspended", "Suspendidas"), ("disabled", "Desactivadas")]


class DialogoCuentasAdmin(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Cuentas de la instancia"), _("Lista de cuentas de la instancia"))
        self.account_id, self.items = account_id, []
        self.estado_combo = Gtk.DropDown.new_from_strings([_(n) for _c, n in ESTADOS_CUENTA])
        self.estado_combo.update_property([Gtk.AccessibleProperty.LABEL], [_("Estado de las cuentas")])
        self.estado_combo.connect("notify::selected", lambda *_a: self._cargar())
        self.fila_botones.append(self.estado_combo)
        self.botones.append(self.estado_combo)
        self.lista.siguiente = self.estado_combo
        for etiqueta, accion in ((_("_Aprobar"), "aprobar"), (_("_Rechazar"), "rechazar"), (_("_Silenciar"), "silenciar"),
                                 (_("Quitar _silencio"), "quitar_silencio"), (_("S_uspender"), "suspender"),
                                 (_("Quitar susp_ensión"), "quitar_suspension")):
            self.boton(etiqueta, lambda a=accion: self._accion(a))
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_error_admin(err))
                return
            self.items = res or []
            self.lista.poner([f"@{c['usuario']}. {c['rol'] or _('usuario')}. {c['email']}" for c in self.items])
            self.mostrar_estado(_("{n} cuentas").format(n=len(self.items)) if self.items else _("No hay cuentas en este estado"))
        mastodon_service.admin_listar_cuentas_async(
            self.account_id, estado=ESTADOS_CUENTA[self.estado_combo.get_selected()][0], callback=_fin)

    def _accion(self, accion):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        c = self.items[p]

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo: {e}").format(e=err) if err else _("Hecho"))
                self._cargar()
            mastodon_service.admin_accion_cuenta_async(self.account_id, c["id"], accion, callback=_fin)
        if accion in ("suspender", "rechazar"):
            confirmar_async(self, _("¿Seguro que quieres {a} a @{u}?").format(a=_(accion), u=c["usuario"]), _hacer,
                            etiqueta_si=_("Sí, continuar"))
        else:
            _hacer()


SEVERIDADES = [("silence", "Silenciar"), ("suspend", "Suspender"), ("noop", "Solo registrar")]


class DialogoDominios(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Dominios bloqueados"), _("Lista de dominios bloqueados"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Bloquear un dominio…"), self._bloquear)
        self.boton(_("_Quitar el bloqueo"), self._quitar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_error_admin(err))
                return
            nombres = dict(SEVERIDADES)
            self.items = res or []
            self.lista.poner([f"{d['dominio']}. {_(nombres.get(d['severidad'], d['severidad']))}" for d in self.items])
            self.mostrar_estado(_("{n} dominios").format(n=len(self.items)) if self.items else _("No hay dominios bloqueados"))
            self.lista.enfocar()
        mastodon_service.admin_listar_dominios_bloqueados_async(self.account_id, callback=_fin)

    def _bloquear(self):
        d = DialogoBase(self, _("Bloquear un dominio"), _("_Bloquear"))
        dominio = d.campo(_("_Dominio:"), Gtk.Entry(activates_default=True, width_chars=40))
        sev = d.campo(_("_Severidad:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in SEVERIDADES]))
        d.set_focus(dominio)

        def _hacer():
            nombre = dominio.get_text().strip()
            if not nombre:
                return

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo: {e}").format(e=err))
                    return
                notificar(_("Dominio bloqueado"))
                d.close()
                self._cargar()
            mastodon_service.admin_bloquear_dominio_async(self.account_id, nombre, SEVERIDADES[sev.get_selected()][0], callback=_fin)
        d.aceptar = _hacer
        d.present()

    def _quitar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        item = self.items[p]

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo: {e}").format(e=err) if err else _("Bloqueo quitado"))
                self._cargar()
            mastodon_service.admin_desbloquear_dominio_async(self.account_id, item["id"], callback=_fin)
        confirmar_async(self, _("¿Quitar el bloqueo de {d}?").format(d=item["dominio"]), _hacer, etiqueta_si=_("Quitar"))
