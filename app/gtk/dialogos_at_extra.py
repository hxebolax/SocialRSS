# -*- coding: utf-8 -*-
"""Bluesky y W Social (las dos hablan el protocolo AT): tendencias, paquetes de inicio, grupos de chat, buscar cuentas,
solicitudes de mensaje y actividad de cuentas. Cada red tiene su servicio, pero las pantallas son las mismas: `RedAT` adapta
lo que difiere (nombres de campos y funciones)."""

from dataclasses import dataclass
from typing import Any

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import copiar_al_portapapeles, notificar
from app.services.bluesky.bluesky_service import bluesky_service
from app.services.wsocial.wsocial_service import wsocial_service

from .dialogos import DialogoBase, DialogoElegir, confirmar_async
from .datos_wsocial import adaptar
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube_instancia import texto_en_dialogo


@dataclass
class RedAT:
    clave: str          # «bluesky» o «wsocial»
    nombre: str
    servicio: Any

    def cb(self, f):
        """El servicio de W Social entrega (ok, resultado); el de Bluesky, (resultado, error): se unifica en (resultado, error)."""
        return adaptar(f) if self.clave == "wsocial" else f

    # ---- lo que cambia de una red a otra
    def tendencias(self, acc, cb):
        if self.clave == "bluesky":
            self.servicio.cargar_tendencias_async(acc, callback=lambda r, e: cb(
                [{"nombre": t["topic"], "publicaciones": t["posts"], "categoria": ""} for t in r or []] if r is not None else r, e))
        else:
            self.servicio.tendencias_async(acc, callback=self.cb(cb))

    def buscar_cuentas(self, acc, texto, cb):
        if self.clave == "bluesky":
            self.servicio.buscar_actores_async(acc, texto, callback=cb)
        else:
            self.servicio.buscar_cuentas_async(acc, texto, callback=self.cb(cb))

    def paquetes(self, acc, cb):
        if self.clave == "bluesky":
            self.servicio.obtener_starter_packs_async(acc, callback=lambda r, e: cb(
                [{"uri": p["uri"], "name": p["name"], "cuentas": p["members"], "description": ""} for p in r or []]
                if r is not None else r, e))
        else:
            self.servicio.cargar_starter_packs_async(acc, callback=self.cb(cb))

    def miembros(self, acc, convo_id, cb):
        def _norm(r, e):
            cb([{"did": m.get("did", ""), "handle": m.get("handle", ""), "nombre": m.get("nombre") or m.get("name", ""),
                 "papel": m.get("papel", "")} for m in r or []] if r is not None else r, e)
        (self.servicio.miembros_convo_async if self.clave == "bluesky" else self.servicio.miembros_grupo_async)(
            acc, convo_id, callback=self.cb(_norm))

    def salir_grupo(self, acc, convo_id, cb):
        if self.clave == "bluesky":
            self.servicio.salir_grupo_async(acc, convo_id, callback=cb)
        else:
            self.servicio.accion_convo_async(acc, convo_id, "salir", callback=self.cb(cb))

    def abrir_perfil(self, padre, acc, did):
        if self.clave == "bluesky":
            from .dialogos_bluesky import DialogoPerfilBsky
            DialogoPerfilBsky(padre, acc, did).present()
        else:
            from .dialogos_wsocial import DialogoPerfilWSocial
            DialogoPerfilWSocial(padre, acc, did).present()


BSKY = RedAT("bluesky", "Bluesky", bluesky_service)
WS = RedAT("wsocial", "W Social", wsocial_service)


def _pedir(padre, titulo: str, etiqueta: str, al_aceptar, aceptar: str = None, valor: str = ""):
    d = DialogoBase(padre, titulo, aceptar)
    e = d.campo(etiqueta, Gtk.Entry(text=valor, activates_default=True, width_chars=44))
    d.set_focus(e)

    def _ok():
        texto = e.get_text().strip()
        if texto:
            d.close()
            al_aceptar(texto)
    d.aceptar = _ok
    d.present()


def _cuentas(texto: str) -> list:
    return [c.strip().lstrip("@") for c in texto.replace(";", ",").replace(" ", ",").split(",") if c.strip()]


def _aviso(ok: str):
    def _fin(res, err):
        notificar(_("No se pudo: {e}").format(e=err) if err else ok)
    return _fin


# --------------------------------------------------------------------------- tendencias
class DialogoTendenciasAT(DialogoLista):
    """Los temas del momento. Intro busca las publicaciones del tema en un buffer."""

    def __init__(self, padre, red: RedAT, account_id: int):
        super().__init__(padre, _("Tendencias de {r}").format(r=red.nombre), _("Temas en tendencia"), lambda pos: self._abrir(pos))
        self.red, self.account_id, self.items = red, account_id, []
        self.boton(_("_Buscar el tema"), lambda: self._abrir(self.lista.posicion()))
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando las tendencias…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las tendencias: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([t["nombre"] + (", " + _("{n} publicaciones").format(n=t["publicaciones"]) if t.get("publicaciones") else "")
                              + (f", {t['categoria']}" if t.get("categoria") else "") for t in self.items])
            self.mostrar_estado(_("{n} temas").format(n=len(self.items)) if self.items else _("No hay tendencias"))
            self.lista.enfocar()
        red.tendencias(account_id, _fin)

    def _abrir(self, pos):
        if 0 <= pos < len(self.items):
            tema = self.items[pos]["nombre"]
            self.close()
            self.padre._abrir_buffer(self.account_id, f"search:{tema}", _("Búsqueda: {t}").format(t=tema), servicio=self.red.clave)


# --------------------------------------------------------------------------- paquetes de inicio
class DialogoPaquetesAT(DialogoLista):
    """Tus paquetes de inicio (starter packs): ver sus cuentas, crear uno nuevo y, en Bluesky, borrarlos."""

    def __init__(self, padre, red: RedAT, account_id: int):
        super().__init__(padre, _("Paquetes de inicio de {r}").format(r=red.nombre), _("Paquetes de inicio"),
                         lambda pos: self._ver(pos))
        self.red, self.account_id, self.items = red, account_id, []
        if red.clave == "wsocial":
            self.boton(_("_Ver cuentas"), lambda: self._ver(self.lista.posicion()))
        self.boton(_("_Crear uno nuevo…"), self._crear)
        if red.clave == "bluesky":
            self.boton(_("_Borrar"), self._borrar)
        if red.clave == "wsocial":
            self.boton(_("_Sugeridos…"), self._sugeridos)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los paquetes…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los paquetes: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{p['name']}, " + _("{n} cuentas").format(n=p.get("cuentas", 0))
                              + (f". {p['description']}" if p.get("description") else "") for p in self.items])
            self.mostrar_estado(_("{n} paquetes").format(n=len(self.items)) if self.items else _("No tienes paquetes de inicio"))
            self.lista.enfocar()
        self.red.paquetes(self.account_id, _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _ver(self, pos):
        if not 0 <= pos < len(self.items) or self.red.clave != "wsocial":
            return

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo abrir el paquete: {e}").format(e=err or ""))
                return
            lineas = [res.get("description") or ""] + [f"{c['display_name'] or c['handle']} (@{c['handle']})" for c in res["cuentas"]]
            lineas += [_("Feed: {n}").format(n=f["name"]) for f in res.get("feeds", [])]
            texto_en_dialogo(self, res.get("name") or _("Paquete de inicio"), "\n".join(x for x in lineas if x))
        self.red.servicio.detalle_starter_pack_async(self.account_id, self.items[pos]["uri"], callback=self.red.cb(_fin))

    def _crear(self):
        if self.red.clave == "bluesky":
            return self._crear_bluesky()
        d = DialogoBase(self, _("Crear un paquete de inicio"), _("_Crear"))
        nombre = d.campo(_("_Nombre del paquete:"), Gtk.Entry(activates_default=True, width_chars=40))
        desc = d.campo(_("_Descripción (opcional):"), Gtk.Entry(activates_default=True))
        cuentas = d.campo(_("_Cuentas, separadas por comas:"), Gtk.Entry(activates_default=True))
        d.set_focus(nombre)

        def _ok():
            n, a = nombre.get_text().strip(), _cuentas(cuentas.get_text())
            if not n or not a:
                notificar(_("Escribe el nombre y al menos una cuenta."))
                return
            ds = desc.get_text().strip()
            d.close()
            wsocial_service.crear_starter_pack_async(self.account_id, n, ds, a, callback=adaptar(lambda r, e: (
                notificar(_("No se pudo crear el paquete: {e}").format(e=e)) if e else (notificar(_("Paquete de inicio creado.")),
                                                                                         self._cargar()))))
        d.aceptar = _ok
        d.present()

    def _crear_bluesky(self):
        def _con_listas(listas, err):
            if err:
                notificar(_("Error al cargar tus listas: {e}").format(e=err))
                return
            if not listas:
                notificar(_("Necesitas al menos una lista. Créala en Mi cuenta, Listas, y añádele cuentas antes de hacer el paquete."))
                return
            d = DialogoBase(self, _("Crear un paquete de inicio"), _("_Crear"))
            lista = d.campo(_("_Lista que incluirá el paquete:"), Gtk.DropDown.new_from_strings([x["name"] for x in listas]))
            nombre = d.campo(_("_Nombre del paquete:"), Gtk.Entry(activates_default=True, width_chars=40))
            desc = d.campo(_("_Descripción (opcional):"), Gtk.Entry(activates_default=True))
            d.set_focus(lista)

            def _ok():
                n = nombre.get_text().strip()
                if not n:
                    return
                uri, ds = listas[lista.get_selected()]["uri"], desc.get_text().strip()
                d.close()
                bluesky_service.crear_starter_pack_async(self.account_id, n, ds, uri, callback=lambda r, e: (
                    notificar(_("No se pudo crear el paquete: {e}").format(e=e)) if e else (
                        notificar(_("Paquete de bienvenida creado.")), self._cargar())))
            d.aceptar = _ok
            d.present()
        bluesky_service.obtener_listas_async(self.account_id, callback=_con_listas)

    def _borrar(self):
        p = self._actual()
        if p:
            confirmar_async(self, _("¿Borrar el paquete «{n}»?").format(n=p["name"]),
                            lambda: bluesky_service.borrar_starter_pack_async(self.account_id, p["uri"], callback=lambda r, e: (
                                notificar(_("No se pudo borrar: {e}").format(e=e)) if e else (notificar(_("Paquete borrado.")),
                                                                                              self._cargar()))),
                            etiqueta_si=_("Borrar"))

    def _sugeridos(self):
        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar los paquetes sugeridos: {e}").format(e=err))
                return
            d = DialogoLista(self, _("Paquetes de inicio sugeridos"), _("Paquetes sugeridos"))
            d.lista.poner([f"{p['name']} — {p['creator']}" for p in res or []])
            d.mostrar_estado(_("{n} paquetes").format(n=len(res or [])))
            d.cerrar_boton()
            d.present()
        wsocial_service.paquetes_sugeridos_async(self.account_id, callback=adaptar(_fin))


# --------------------------------------------------------------------------- cuentas
class DialogoBuscarCuentasAT(DialogoLista):
    """Busca cuentas por texto; Intro abre el perfil."""

    def __init__(self, padre, red: RedAT, account_id: int, texto: str):
        super().__init__(padre, _("Cuentas de {r}: {t}").format(r=red.nombre, t=texto), _("Cuentas encontradas"),
                         lambda pos: self._perfil(pos))
        self.red, self.account_id, self.items = red, account_id, []
        self.boton(_("_Perfil"), lambda: self._perfil(self.lista.posicion()))
        self.cerrar_boton()
        self.mostrar_estado(_("Buscando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo buscar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{a.get('display_name') or a.get('handle')} (@{a.get('handle')})"
                              + (f". {a['description']}" if a.get("description") else "") for a in self.items])
            self.mostrar_estado(_("{n} cuentas").format(n=len(self.items)) if self.items else _("No se encontró ninguna cuenta"))
            self.lista.enfocar()
        red.buscar_cuentas(account_id, texto, _fin)

    def _perfil(self, pos):
        if 0 <= pos < len(self.items):
            self.red.abrir_perfil(self.padre, self.account_id, self.items[pos]["did"])


class DialogoActividadBsky(DialogoLista):
    """Cuentas a cuya actividad estás suscrito (Bluesky te avisa cuando publican)."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Cuentas a cuya actividad sigues"), _("Cuentas suscritas"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Dejar de recibir avisos"), self._quitar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{a.get('display_name') or a.get('handle')} (@{a.get('handle')})" for a in self.items])
            self.mostrar_estado(_("{n} cuentas").format(n=len(self.items)) if self.items else _("No sigues la actividad de ninguna cuenta."))
            self.lista.enfocar()
        bluesky_service.listar_suscripciones_actividad_async(self.account_id, callback=_fin)

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            a = self.items[p]
            bluesky_service.suscribir_actividad_async(self.account_id, a["did"], posts=False, replies=False, callback=lambda r, e: (
                notificar(_("No se pudo: {e}").format(e=e)) if e else (notificar(_("Dejarás de recibir avisos de esta cuenta.")),
                                                                      self._cargar())))


def lista_de_cuentas(padre, titulo: str, cuentas: list, vacio: str):
    d = DialogoLista(padre, titulo, titulo)
    d.lista.poner([f"{a.get('display_name') or a.get('handle')} (@{a.get('handle')})" for a in cuentas])
    d.mostrar_estado(_("{n} cuentas").format(n=len(cuentas)) if cuentas else vacio)
    d.cerrar_boton()
    d.present()


# --------------------------------------------------------------------------- grupos de chat
def crear_grupo(padre, red: RedAT, acc: int):
    d = DialogoBase(padre, _("Nuevo grupo"), _("_Crear"))
    nombre = d.campo(_("_Nombre del grupo:"), Gtk.Entry(activates_default=True, width_chars=40))
    cuentas = d.campo(_("_Cuentas a añadir, separadas por comas (por ejemplo nombre.bsky.social):"),
                      Gtk.Entry(activates_default=True))
    d.set_focus(nombre)

    def _ok():
        n, a = nombre.get_text().strip(), _cuentas(cuentas.get_text())
        if not n or not a:
            notificar(_("Escribe el nombre y añade al menos una cuenta al grupo."))
            return
        d.close()
        red.servicio.crear_grupo_async(acc, n, a, callback=red.cb(_aviso(_("Grupo creado."))))
    d.aceptar = _ok
    d.present()


def entrar_en_grupo(padre, red: RedAT, acc: int):
    def _con(enlace):
        red.servicio.pedir_entrada_grupo_async(acc, enlace, callback=red.cb(lambda r, e: notificar(
            _("No se pudo: {e}").format(e=e) if e else (_("Solicitud enviada.") if (r or {}).get("pendiente") else _("Ya estás en el grupo.")))))
    _pedir(padre, _("Entrar en un grupo"), _("_Enlace de invitación al grupo:"), _con, _("_Entrar"))


class DialogoGrupoAT(DialogoLista):
    """Gestionar un grupo: quién está, añadir y expulsar cuentas, cambiar el nombre, el enlace de invitación y las solicitudes."""

    def __init__(self, padre, red: RedAT, account_id: int, convo_id: str, titulo: str):
        super().__init__(padre, _("Grupo: {t}").format(t=titulo), _("Miembros del grupo"))
        self.red, self.account_id, self.convo_id, self.items = red, account_id, convo_id, []
        self.boton(_("_Añadir cuentas…"), self._anadir)
        self.boton(_("_Expulsar"), self._expulsar)
        self.boton(_("_Renombrar…"), self._renombrar)
        self.boton(_("_Enlace de invitación"), self._enlace)
        self.boton(_("_Solicitudes de entrada…"), self._solicitudes)
        self.boton(_("S_alir del grupo"), self._salir)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los miembros…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{m['nombre'] or m['handle']} (@{m['handle']})" + (f", {m['papel']}" if m.get("papel") else "")
                              for m in self.items])
            self.mostrar_estado(_("{n} miembros").format(n=len(self.items)))
            self.lista.enfocar()
        self.red.miembros(self.account_id, self.convo_id, _fin)

    def _tras(self, ok: str):
        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
            else:
                notificar(ok)
                self._cargar()
        return _fin

    def _anadir(self):
        _pedir(self, _("Añadir al grupo"), _("_Cuentas a añadir, separadas por comas:"),
               lambda t: self.red.servicio.gestionar_miembros_grupo_async(self.account_id, self.convo_id, _cuentas(t), quitar=False,
                                                                           callback=self.red.cb(self._tras(_("Cuentas añadidas al grupo.")))),
               _("_Añadir"))

    def _expulsar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.items):
            return
        m = self.items[p]
        confirmar_async(self, _("Vas a expulsar a {u} del grupo. ¿Continuar?").format(u=m["handle"] or m["did"]),
                        lambda: self.red.servicio.gestionar_miembros_grupo_async(
                            self.account_id, self.convo_id, [m["did"] or m["handle"]], quitar=True,
                            callback=self.red.cb(self._tras(_("Cuenta expulsada.")))), etiqueta_si=_("Expulsar"))

    def _renombrar(self):
        _pedir(self, _("Cambiar el nombre"), _("_Nuevo nombre del grupo:"),
               lambda t: self.red.servicio.editar_grupo_async(self.account_id, self.convo_id, t,
                                                              callback=self.red.cb(_aviso(_("Nombre cambiado.")))), _("_Cambiar"))

    def _enlace(self):
        def _fin(res, err):
            if err:
                notificar(_("No se pudo obtener el enlace: {e}").format(e=err))
                return
            enlace = (res or {}).get("enlace", "")
            if enlace:
                copiar_al_portapapeles(enlace, _("Enlace de invitación copiado"))
            texto_en_dialogo(self, _("Invitar al grupo"), enlace or _("El servidor no ha devuelto ningún enlace."))
        self.red.servicio.enlace_grupo_async(self.account_id, self.convo_id, "crear", callback=self.red.cb(_fin))

    def _solicitudes(self):
        def _fin(res, err):
            if err:
                notificar(_("No se pudieron consultar las solicitudes: {e}").format(e=err))
                return
            if not res:
                notificar(_("No hay solicitudes pendientes."))
                return

            def _elegida(i):
                s = res[i]
                confirmar_async(
                    self, _("¿Aceptar a {u} en el grupo? Si dices que no, se rechaza.").format(u=s.get("handle")),
                    lambda: self.red.servicio.resolver_solicitud_grupo_async(
                        self.account_id, self.convo_id, s["did"], True, callback=self.red.cb(_aviso(_("Solicitud aceptada.")))),
                    etiqueta_si=_("Aceptar"),
                    al_no=lambda: self.red.servicio.resolver_solicitud_grupo_async(
                        self.account_id, self.convo_id, s["did"], False, callback=self.red.cb(_aviso(_("Solicitud rechazada.")))))
            DialogoElegir(self, _("Solicitudes del grupo"), _("_Solicitud:"),
                          [f"{s.get('nombre') or s.get('handle')} (@{s.get('handle')})" for s in res], _elegida,
                          _("_Resolver")).present()
        self.red.servicio.solicitudes_grupo_async(self.account_id, self.convo_id, callback=self.red.cb(_fin))

    def _salir(self):
        confirmar_async(self, _("¿Salir de este grupo?"),
                        lambda: self.red.salir_grupo(self.account_id, self.convo_id, lambda r, e: (
                            notificar(_("No se pudo salir: {e}").format(e=e)) if e else (notificar(_("Has salido del grupo.")),
                                                                                          self.close()))),
                        etiqueta_si=_("Salir"))


# --------------------------------------------------------------------------- solicitudes de mensaje (Bluesky)
class DialogoSolicitudesMensajesBsky(DialogoLista):
    """Mensajes directos de gente que no sigues: aceptar uno para poder responder y abrirlo."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Solicitudes de mensajes"), _("Solicitudes de mensaje"), lambda pos: self._aceptar(pos))
        self.account_id, self.items = account_id, []
        self.boton(_("_Aceptar y abrir"), lambda: self._aceptar(self.lista.posicion()))
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([s["title"] for s in self.items])
            self.mostrar_estado(_("{n} solicitudes").format(n=len(self.items)) if self.items else _("No tienes solicitudes de mensaje."))
            self.lista.enfocar()
        bluesky_service.solicitudes_mensajes_async(account_id, callback=_fin)

    def _aceptar(self, pos):
        if not 0 <= pos < len(self.items):
            return
        s = self.items[pos]

        def _fin(res, err):
            if err:
                notificar(_("No se pudo aceptar: {e}").format(e=err))
                return
            self.close()
            self.padre._abrir_buffer(self.account_id, f"dm:{s['convo_id']}", s["title"], servicio="bluesky")
        bluesky_service.aceptar_solicitud_convo_async(self.account_id, s["convo_id"], callback=_fin)


# --------------------------------------------------------------------------- salida pendiente
class DialogoSalidaAT(DialogoLista):
    """Publicaciones que quedaron pendientes por un fallo de red: reintentarlas o descartarlas."""

    ESTADOS = {"pending": "Pendiente", "error": "Con error", "failed": "Con error", "unknown": "Sin confirmar"}

    def __init__(self, padre, red: RedAT, account_id: int):
        super().__init__(padre, _("Salida pendiente de {r}").format(r=red.nombre), _("Publicaciones pendientes"))
        self.red, self.account_id, self.items = red, account_id, []
        self.boton(_("_Reintentar"), self._reintentar)
        self.boton(_("_Descartar"), self._descartar)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.items = self.red.servicio.listar_outbox(self.account_id)
        self.lista.poner([" ".join(((o.get("texto") or (o.get("payload") or {}).get("texto") or "")).split())[:160] + ". "
                          + _(self.ESTADOS.get(o.get("estado"), o.get("estado") or "")) + (f". {o['error']}" if o.get("error") else "")
                          for o in self.items])
        self.mostrar_estado(_("{n} pendientes").format(n=len(self.items)) if self.items
                            else _("No hay ninguna publicación pendiente."))
        self.lista.enfocar()

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _reintentar(self):
        o = self._actual()
        if not o:
            return
        notificar(_("Reintentando…"))

        def _fin(a, b):
            ok = a if isinstance(a, bool) else not b
            detalle = b if isinstance(a, bool) else (b or a)
            if ok:
                notificar(_("Ya se había publicado; se ha marcado como enviada.") if isinstance(detalle, dict) and detalle.get("ya_estaba")
                          else _("Publicación enviada."))
            else:
                notificar(_("Sigue sin poder enviarse: {e}").format(e=detalle))
            self._cargar()
        self.red.servicio.reintentar_outbox_async(self.account_id, o["id"], callback=_fin)

    def _descartar(self):
        o = self._actual()
        if o:
            confirmar_async(self, _("¿Descartar esta publicación pendiente?"),
                            lambda: (self.red.servicio.borrar_outbox(o["id"]), self._cargar()), etiqueta_si=_("Descartar"))
