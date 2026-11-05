# -*- coding: utf-8 -*-
"""Mastodon: tus listas (crear, editar, borrar y gestionar sus miembros), más acciones sobre una cuenta (seguir con opciones,
destacarla, anotarla, listas, bloquear su dominio…), sobre tu perfil (hashtags y cuentas destacados, novedades de seguidores,
dominios bloqueados) y sobre una publicación (filtrarla, borrar y volver a redactar, citas). Los mismos servicios que usa la
versión de Windows."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.mastodon.mastodon_service import mastodon_service

from .dialogos import DialogoBase, DialogoElegir, DialogoTexto, confirmar_async
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube_instancia import texto_en_dialogo

POLITICAS_LISTA = [("list", "Solo de los miembros de la lista"), ("followed", "De cuentas que sigues"), ("none", "De nadie")]
POLITICAS_CITA = [("public", "Cualquiera"), ("followers", "Mis seguidores"), ("nobody", "Nadie")]


def _hecho(ok: str, tras=None):
    def _fin(res, err):
        if err:
            return notificar(_("No se pudo: {e}").format(e=err))
        notificar(ok)
        if tras:
            tras(res)
    return _fin


# --------------------------------------------------------------------------- listas
class DialogoListasMastodon(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Tus listas de Mastodon"), _("Lista de tus listas"), lambda pos: self._abrir(pos))
        self.account_id, self.items = account_id, []
        self.boton(_("_Abrir"), lambda: self._abrir(self.lista.posicion()))
        self.boton(_("_Miembros…"), self._miembros)
        self.boton(_("_Crear lista…"), self._crear)
        self.boton(_("_Editar…"), self._editar)
        self.boton(_("_Borrar lista"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las listas: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([i["title"] for i in self.items])
            self.mostrar_estado(_("{n} listas").format(n=len(self.items)) if self.items else _("No tienes listas"))
            self.lista.enfocar()
        mastodon_service.obtener_listas_async(self.account_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir(self, pos):
        if 0 <= pos < len(self.items):
            it = self.items[pos]
            self.close()
            self.padre._abrir_buffer(self.account_id, f"list:{it['id']}", _("Lista {t}").format(t=it["title"]))

    def _miembros(self):
        it = self._actual()
        if it:
            DialogoMiembrosLista(self, self.account_id, it).present()

    def _crear(self):
        def _con(t):
            mastodon_service.crear_lista_async(self.account_id, t, callback=_hecho(_("Lista creada."), lambda _r: self._cargar()))
        DialogoTexto(self, _("Crear una lista"), _("_Nombre de la lista:"), _con, aceptar=_("_Crear")).present()

    def _editar(self):
        it = self._actual()
        if not it:
            return
        d = DialogoBase(self, _("Editar la lista"), _("_Guardar"))
        titulo = d.campo(_("_Nombre:"), Gtk.Entry(text=it["title"], activates_default=True, width_chars=36))
        politica = d.campo(_("Mostrar _respuestas:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in POLITICAS_LISTA]))
        exclusiva = d.campo(_("Lista e_xclusiva (sus miembros no salen en Inicio):"), Gtk.Switch(halign=Gtk.Align.START))
        d.set_focus(titulo)

        def _ok():
            t = titulo.get_text().strip()
            if not t:
                return
            pol, exc = POLITICAS_LISTA[politica.get_selected()][0], exclusiva.get_active()
            d.close()
            mastodon_service.actualizar_lista_async(self.account_id, it["id"], t, pol, exc,
                                                    callback=_hecho(_("Lista actualizada."), lambda _r: self._cargar()))
        d.aceptar = _ok
        d.present()

    def _borrar(self):
        it = self._actual()
        if it:
            confirmar_async(self, _("¿Borrar la lista «{t}»?").format(t=it["title"]),
                            lambda: mastodon_service.borrar_lista_async(self.account_id, it["id"],
                                                                        callback=_hecho(_("Lista borrada."), lambda _r: self._cargar())),
                            etiqueta_si=_("Borrar"))


class DialogoMiembrosLista(DialogoLista):
    def __init__(self, padre, account_id: int, lista: dict):
        super().__init__(padre, _("Miembros de {t}").format(t=lista["title"]), _("Miembros de la lista"))
        self.account_id, self.lista_id, self.items = account_id, lista["id"], []
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Quitar"), self._quitar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los miembros: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{c['display_name']} (@{c['acct']})" for c in self.items])
            self.mostrar_estado(_("{n} miembros").format(n=len(self.items)))
            self.lista.enfocar()
        mastodon_service.obtener_miembros_lista_async(self.account_id, self.lista_id, callback=_fin)

    def _anadir(self):
        DialogoTexto(self, _("Añadir a la lista"), _("_Cuenta (@usuario o @usuario@servidor):"), lambda t: (
            mastodon_service.gestionar_miembro_lista_async(self.account_id, self.lista_id, t, True,
                                                           callback=_hecho(_("Cuenta añadida."), lambda _r: self._cargar()))),
                     aceptar=_("_Añadir")).present()

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            c = self.items[p]
            mastodon_service.gestionar_miembro_lista_async(self.account_id, self.lista_id, c["acct"], False,
                                                           callback=_hecho(_("Cuenta quitada."), lambda _r: self._cargar()))


# --------------------------------------------------------------------------- una cuenta
def acciones_cuenta(padre, account_id: int, acct: str):
    """Menú de lo que se puede hacer con una cuenta además de seguir, silenciar y bloquear."""
    acct = acct.lstrip("@")
    M = mastodon_service
    dominio = acct.split("@", 1)[1] if "@" in acct else ""
    opciones = [
        (_("Seguir con opciones (impulsos y avisos)…"), lambda: _seguir_con_opciones(padre, account_id, acct)),
        (_("Destacar la cuenta en mi perfil"), lambda: M.destacar_cuenta_async(account_id, acct, True,
                                                                              callback=_hecho(_("Cuenta destacada.")))),
        (_("Dejar de destacar la cuenta"), lambda: M.destacar_cuenta_async(account_id, acct, False,
                                                                          callback=_hecho(_("Cuenta ya no destacada.")))),
        (_("Mi nota privada sobre la cuenta…"), lambda: DialogoTexto(
            padre, _("Nota privada"), _("_Nota (vacía = borrarla):"),
            lambda t: M.nota_cuenta_async(account_id, acct, t, callback=_hecho(_("Nota guardada."))), aceptar=_("_Guardar")).present()),
        (_("Listas a las que pertenece…"), lambda: _listas_de(padre, account_id, acct)),
        (_("Hashtags destacados en su perfil…"), lambda: _hashtags_de(padre, account_id, acct)),
        (_("Seguidores en común…"), lambda: _en_comun(padre, account_id, acct)),
    ]
    if dominio:
        opciones.append((_("Bloquear el servidor {d}…").format(d=dominio), lambda: confirmar_async(
            padre, _("¿Bloquear todo el servidor {d}? Dejarás de ver a sus cuentas.").format(d=dominio),
            lambda: M.bloquear_dominio_async(account_id, dominio, True, callback=_hecho(_("Servidor bloqueado."))),
            etiqueta_si=_("Bloquear"))))
    DialogoElegir(padre, _("Más acciones sobre @{a}").format(a=acct), _("_Acción:"), [o[0] for o in opciones],
                  lambda i: opciones[i][1](), _("_Aplicar")).present()


def _seguir_con_opciones(padre, account_id: int, acct: str):
    def _rel(r, err):
        if err or not r:
            return notificar(_("No se pudo consultar la cuenta: {e}").format(e=err or ""))
        d = DialogoBase(padre, _("Seguir a @{a}").format(a=acct), _("_Seguir"))
        impulsos = d.campo(_("Ver sus _impulsos:"), Gtk.Switch(active=r["showing_reblogs"], halign=Gtk.Align.START))
        avisos = d.campo(_("_Avisarme de sus publicaciones nuevas:"), Gtk.Switch(active=r["notifying"], halign=Gtk.Align.START))
        d.set_focus(impulsos)

        def _ok():
            a, b = impulsos.get_active(), avisos.get_active()
            d.close()
            mastodon_service.seguir_con_opciones_async(account_id, r["id"], a, b, callback=_hecho(_("Seguimiento actualizado.")))
        d.aceptar = _ok
        d.present()
    mastodon_service.obtener_relacion_autor_async(account_id, acct, callback=_rel)


def _listas_de(padre, account_id: int, acct: str):
    def _fin(res, err):
        if err:
            return notificar(_("No se pudo: {e}").format(e=err))
        d = DialogoLista(padre, _("Listas de @{a}").format(a=acct), _("Listas a las que pertenece"))
        d.lista.poner([x["title"] for x in res or []])
        d.mostrar_estado(_("{n} listas").format(n=len(res or [])))
        d.cerrar_boton()
        d.present()
    mastodon_service.listas_de_cuenta_async(account_id, acct, callback=_fin)


def _hashtags_de(padre, account_id: int, acct: str):
    def _fin(res, err):
        if err:
            return notificar(_("No se pudo: {e}").format(e=err))
        texto_en_dialogo(padre, _("Hashtags destacados de @{a}").format(a=acct),
                         "\n".join(f"#{t['nombre']}" for t in res or []) or _("No tiene hashtags destacados."))
    mastodon_service.hashtags_destacados_de_async(account_id, acct, callback=_fin)


def _en_comun(padre, account_id: int, acct: str):
    def _fin(res, err):
        if err:
            return notificar(_("No se pudo: {e}").format(e=err))
        texto_en_dialogo(padre, _("Seguidores en común con @{a}").format(a=acct),
                         "\n".join(f"@{c}" for c in res or []) or _("No hay seguidores en común."))
    mastodon_service.seguidores_conocidos_async(account_id, acct, callback=_fin)


# --------------------------------------------------------------------------- tu perfil
class DialogoHashtagsDestacados(DialogoLista):
    """Los hashtags que destacas en tu perfil."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Hashtags destacados en mi perfil"), _("Hashtags destacados"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Añadir…"), self._anadir)
        self.boton(_("_Quitar"), self._quitar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"#{t['name']}, " + _("{n} publicaciones").format(n=t["statuses_count"]) for t in self.items])
            self.mostrar_estado(_("{n} hashtags").format(n=len(self.items)))
            self.lista.enfocar()
        mastodon_service.obtener_hashtags_destacados_async(self.account_id, callback=_fin)

    def _anadir(self):
        DialogoTexto(self, _("Destacar un hashtag"), _("_Hashtag (sin #):"), lambda t: mastodon_service.crear_hashtag_destacado_async(
            self.account_id, t.lstrip("#"), callback=_hecho(_("Hashtag destacado."), lambda _r: self._cargar())),
                     aceptar=_("_Destacar")).present()

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            mastodon_service.borrar_hashtag_destacado_async(self.account_id, self.items[p]["id"],
                                                            callback=_hecho(_("Hashtag quitado."), lambda _r: self._cargar()))


class DialogoCuentasDestacadas(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Cuentas destacadas en mi perfil"), _("Cuentas destacadas"))
        self.cerrar_boton()

        def _fin(res, err):
            if err:
                return self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
            self.lista.poner([f"{c['display_name']} (@{c['acct']})" for c in res or []])
            self.mostrar_estado(_("{n} cuentas").format(n=len(res or [])))
            self.lista.enfocar()
        mastodon_service.obtener_cuentas_destacadas_async(account_id, callback=_fin)


class DialogoDominiosBloqueados(DialogoLista):
    """Los servidores que has bloqueado (tú, no la administración)."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Servidores bloqueados"), _("Servidores bloqueados"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Bloquear uno…"), self._anadir)
        self.boton(_("_Desbloquear"), self._quitar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                return self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
            self.items = res or []
            self.lista.poner([str(d) for d in self.items])
            self.mostrar_estado(_("{n} servidores").format(n=len(self.items)))
            self.lista.enfocar()
        mastodon_service.obtener_dominios_bloqueados_async(self.account_id, callback=_fin)

    def _anadir(self):
        DialogoTexto(self, _("Bloquear un servidor"), _("_Servidor (por ejemplo ejemplo.social):"), lambda t: (
            mastodon_service.bloquear_dominio_async(self.account_id, t, True, callback=_hecho(_("Servidor bloqueado."),
                                                                                              lambda _r: self._cargar()))),
                     aceptar=_("_Bloquear")).present()

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            mastodon_service.bloquear_dominio_async(self.account_id, str(self.items[p]), False,
                                                    callback=_hecho(_("Servidor desbloqueado."), lambda _r: self._cargar()))


def novedades_seguidores(padre, account_id: int):
    def _fin(r, err):
        if err or not r:
            return notificar(_("No se pudo comprobar: {e}").format(e=err or ""))
        if r.get("primera_vez"):
            return texto_en_dialogo(padre, _("Novedades de seguidores"),
                                    _("Es la primera vez: se ha guardado la lista de tus {n} seguidores. La próxima vez verás quién "
                                      "se ha ido y quién ha llegado.").format(n=r.get("total", 0)))
        lineas = [_("Seguidores ahora: {n}").format(n=r.get("total", 0)), ""]
        lineas += [_("Nuevos:")] + [f"  {c['display_name']} (@{c['acct']})" for c in r.get("nuevos", [])]
        lineas += ["", _("Te han dejado de seguir:")] + [f"  {c['display_name']} (@{c['acct']})" for c in r.get("perdidos", [])]
        texto_en_dialogo(padre, _("Novedades de seguidores"), "\n".join(lineas))
    mastodon_service.novedades_seguidores_async(account_id, callback=_fin)


def quitar_imagen(padre, account_id: int, avatar: bool):
    confirmar_async(padre, _("¿Quitar tu foto de perfil?") if avatar else _("¿Quitar tu imagen de cabecera?"),
                    lambda: (mastodon_service.quitar_avatar_async if avatar else mastodon_service.quitar_cabecera_async)(
                        account_id, callback=_hecho(_("Foto quitada.") if avatar else _("Cabecera quitada."))), etiqueta_si=_("Quitar"))


# --------------------------------------------------------------------------- una publicación
def filtrar_publicacion(padre, account_id: int, db_id: str):
    """Ocultar una publicación concreta: en uno de tus filtros del servidor o en uno nuevo."""
    def _filtros(res, err):
        if err:
            return notificar(_("No se pudieron leer los filtros: {e}").format(e=err))
        filtros = res or []
        opciones = [_("(Crear un filtro nuevo)")] + [f.get("title") or f.get("phrase") or str(f.get("id")) for f in filtros]

        def _elegido(i):
            if i == 0:
                DialogoTexto(padre, _("Filtro nuevo"), _("_Nombre del filtro:"), lambda t: mastodon_service.crear_filtro_para_publicacion_async(
                    account_id, t, db_id, callback=_hecho(_("Publicación filtrada."))), aceptar=_("_Crear")).present()
            else:
                mastodon_service.filtrar_publicacion_async(account_id, filtros[i - 1]["id"], db_id,
                                                           callback=_hecho(_("Publicación filtrada.")))
        DialogoElegir(padre, _("Filtrar la publicación"), _("_Filtro:"), opciones, _elegido, _("_Aplicar")).present()
    mastodon_service.obtener_filtros_servidor_async(account_id, callback=_filtros)


def borrar_y_redactar(padre, account_id: int, db_id: str, al_terminar=None):
    def _hacer():
        def _fin(datos, err):
            if err:
                return notificar(_("No se pudo borrar: {e}").format(e=err))
            notificar(_("Publicación borrada. Puedes volver a redactarla.") + (" " + _("Los adjuntos no se recuperan.")
                                                                             if datos.get("tenia_adjuntos") else ""))
            if al_terminar:
                al_terminar()
            from .dialogos import DialogoPublicar
            DialogoPublicar(padre, account_id, datos.get("text", ""), datos.get("in_reply_to_id"), datos.get("visibility"),
                            datos.get("spoiler_text", "")).present()
        mastodon_service.borrar_y_redactar_async(account_id, db_id, callback=_fin)
    confirmar_async(padre, _("¿Borrar la publicación y abrir el editor con su texto para volver a escribirla?"), _hacer,
                    etiqueta_si=_("Borrar y redactar"))


def citas_de(padre, account_id: int, status_id: str):
    def _fin(res, err):
        if err:
            return notificar(_("No se pudieron cargar las citas: {e}").format(e=err))
        d = DialogoLista(padre, _("Citas de tu publicación"), _("Publicaciones que te citan"))
        citas = res or []
        d.lista.poner([f"@{c['autor']}: {c['texto']}" for c in citas])

        def _revocar():
            p = d.lista.posicion()
            if 0 <= p < len(citas):
                c = citas[p]
                confirmar_async(d, _("¿Revocar esta cita? Dejará de mostrar tu publicación."),
                                lambda: mastodon_service.revocar_cita_async(account_id, status_id, c["id"],
                                                                            callback=_hecho(_("Cita revocada."), lambda _r: d.close())),
                                etiqueta_si=_("Revocar"))
        d.boton(_("_Revocar la cita"), _revocar)
        d.cerrar_boton()
        d.mostrar_estado(_("{n} citas").format(n=len(citas)) if citas else _("Nadie ha citado esta publicación."))
        d.present()
    mastodon_service.citas_de_status_async(account_id, status_id, callback=_fin)


def politica_de_citas(padre, account_id: int, status_id: str):
    DialogoElegir(padre, _("Quién puede citar esta publicación"), _("_Quién:"), [_(n) for _c, n in POLITICAS_CITA],
                  lambda i: mastodon_service.politica_citas_async(account_id, status_id, POLITICAS_CITA[i][0],
                                                                  callback=_hecho(_("Política de citas cambiada."))),
                  _("_Aplicar")).present()
