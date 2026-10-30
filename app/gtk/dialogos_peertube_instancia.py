# -*- coding: utf-8 -*-
"""PeerTube: directos, estadísticas, moderación y administración de la instancia (los mismos servicios que usa la versión de
Windows). Lo de moderación y administración solo funciona si la cuenta tiene ese rol: el servidor rechaza el resto."""

from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import copiar_al_portapapeles, notificar
from app.services.peertube import peertube_service

from . import datos_peertube
from .dialogos import DialogoBase, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube import _ConCanales
from .dialogos_reddit import _marco, _texto_de, _vista_texto
from .dialogos_reddit_comunidad import _hacer

LATENCIAS = [(1, "Normal"), (2, "Alta (más estable)"), (3, "Baja (más inmediata)")]
ROLES = [(2, "Usuario"), (1, "Moderador"), (0, "Administrador")]


def texto_en_dialogo(padre, titulo: str, texto: str):
    """Ventana de solo lectura con un texto largo (el lector de pantalla lo recorre con las flechas)."""
    d = DialogoBase(padre, titulo, _("_Cerrar"))
    tv = _vista_texto(False)
    tv.get_buffer().set_text(texto)
    tv.update_property([Gtk.AccessibleProperty.LABEL], [titulo])
    d.rejilla.attach(_marco(tv, 260), 0, 0, 1, 1)
    d.boton_cancelar.set_visible(False)
    d.set_default_size(520, 380)
    d.present()
    tv.grab_focus()


def _pedir(padre, titulo: str, etiqueta: str, al_aceptar, texto_largo: bool = False, aceptar: str = None, clave: bool = False):
    d = DialogoBase(padre, titulo, aceptar)
    if texto_largo:
        tv = _vista_texto()
        lbl = Gtk.Label(label=etiqueta, use_underline=True, xalign=0, yalign=0)
        lbl.set_mnemonic_widget(tv)
        d.rejilla.attach(lbl, 0, 0, 1, 1)
        d.rejilla.attach(_marco(tv, 100), 1, 0, 1, 1)
        leer = lambda: _texto_de(tv).strip()  # noqa: E731
    else:
        e = d.campo(etiqueta, Gtk.PasswordEntry(activates_default=True, show_peek_icon=True) if clave
                    else Gtk.Entry(activates_default=True, width_chars=40))
        d.set_focus(e)
        leer = lambda: e.get_text().strip()  # noqa: E731

    def _ok():
        d.close()
        al_aceptar(leer())
    d.aceptar = _ok
    d.present()


# --------------------------------------------------------------------------- directos
class DialogoCrearDirecto(DialogoBase, _ConCanales):
    """Crear una emisión en directo (RTMP). Entrega los datos a `al_aceptar(dict)`."""

    def __init__(self, padre, account_id: int, al_aceptar):
        super().__init__(padre, _("Crear directo en PeerTube"), _("_Crear"))
        self.account_id, self.al_aceptar = account_id, al_aceptar
        self.canal = self.campo(_("_Canal:"), Gtk.DropDown.new_from_strings([_("Cargando canales…")]))
        self.titulo = self.campo(_("_Título del directo:"), Gtk.Entry(activates_default=True, max_length=120, width_chars=44))
        self.privacidad = self.campo(_("_Privacidad:"), Gtk.DropDown.new_from_strings(
            [_(n) for _i, n in datos_peertube.PRIVACIDADES]))
        self.latencia = self.campo(_("_Latencia:"), Gtk.DropDown.new_from_strings([_(n) for _i, n in LATENCIAS]))
        self.permanente = self.campo(_("Directo _permanente (puedes emitir varias veces):"), Gtk.Switch(halign=Gtk.Align.START))
        self.repeticion = self.campo(_("Guardar la _repetición al terminar:"), Gtk.Switch(halign=Gtk.Align.START))
        self.permanente.connect("notify::active", lambda *_a: self.permanente.get_active() and self.repeticion.set_active(False))
        self._cargar_canales(self.canal)
        self.set_focus(self.titulo)

    def aceptar(self):
        titulo, canal = self.titulo.get_text().strip(), self._canal_id(self.canal)
        if not titulo or not canal:
            notificar(_("El título y el canal son obligatorios"))
            return
        permanente = self.permanente.get_active()
        datos = {"channel_id": canal, "name": titulo, "privacy": datos_peertube.PRIVACIDADES[self.privacidad.get_selected()][0],
                 "permanent_live": permanente, "latency_mode": LATENCIAS[self.latencia.get_selected()][0],
                 "save_replay": self.repeticion.get_active() and not permanente}
        self.close()
        self.al_aceptar(datos)


class DialogoDatosEmision(DialogoBase):
    """URL RTMP y clave de emisión. La clave es secreta: va oculta y solo se enseña o se copia con una acción explícita."""

    def __init__(self, padre, info: dict):
        super().__init__(padre, _("Datos de emisión del directo"), _("_Cerrar"))
        self.boton_cancelar.set_visible(False)
        self._clave = info.get("stream_key", "")
        self.rtmp = self.campo(_("URL del servidor _RTMP:"), Gtk.Entry(text=info.get("rtmp_url", ""), editable=True, width_chars=50))
        if info.get("rtmps_url"):
            self.campo(_("URL RTMP_S (segura):"), Gtk.Entry(text=info["rtmps_url"], width_chars=50))
        self.clave = self.campo(_("Clave de emisión (SECRETA, no la compartas):"), Gtk.PasswordEntry(
            show_peek_icon=True, activates_default=False))
        self.clave.set_text(self._clave)
        fila = Gtk.Box(spacing=8)
        for et, fn in ((_("Copiar la URL R_TMP"), lambda: copiar_al_portapapeles(info.get("rtmp_url", ""), _("URL RTMP copiada."))),
                       (_("Copiar la _clave de emisión"), lambda: self._clave and copiar_al_portapapeles(
                           self._clave, _("Clave de emisión copiada. No la compartas; da acceso a emitir en tu directo.")))):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, f=fn: f())
            fila.append(b)
        self.rejilla.attach(fila, 1, self._fila, 1, 1)
        self._fila += 1
        lat = {1: _("Normal"), 2: _("Alta"), 3: _("Baja")}.get(info.get("latency_mode"), "")
        resumen = [_("Directo permanente: sí") if info.get("permanent_live") else _("Directo permanente: no")]
        if lat:
            resumen.append(_("Latencia: {l}").format(l=lat))
        resumen.append(_("Guardar repetición: sí") if info.get("save_replay") else _("Guardar repetición: no"))
        self.rejilla.attach(Gtk.Label(label="\n".join(resumen), xalign=0), 1, self._fila, 1, 1)
        self.set_focus(self.rtmp)


def sesiones_directo(padre, account_id: int, video_id):
    def _fin(res, err):
        if err:
            notificar(_("No se pudieron cargar las sesiones: {e}").format(e=err))
            return
        if not res:
            notificar(_("Este directo aún no tiene sesiones."))
            return
        lineas = []
        for s in res:
            inicio = s.get("inicio", "")
            try:
                inicio = datetime.fromisoformat(inicio.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
            lineas.append(_("Sesión del {f}{e}{r}").format(
                f=inicio, e=" " + _("(con error)") if s.get("error") else "",
                r=" — " + _("repetición: {t}").format(t=s["repeticion_titulo"]) if s.get("repeticion_titulo") else ""))
        d = DialogoLista(padre, _("Sesiones del directo"), _("Sesiones"))
        d.lista.poner(lineas)
        d.mostrar_estado(_("{n} sesiones").format(n=len(lineas)))
        d.cerrar_boton()
        d.present()
    ejecutar(lambda: peertube_service.sesiones_directo(account_id, video_id), _fin)


# --------------------------------------------------------------------------- estadísticas
def estadisticas_video(padre, account_id: int, video_id):
    def _fin(st, err):
        if err or not st:
            notificar(_("No se pudieron cargar las estadísticas: {e}").format(e=err or ""))
            return
        lineas = [_("Espectadores totales: {n}").format(n=st.get("total_espectadores", 0)),
                  _("Pico de espectadores: {n}").format(n=st.get("pico_espectadores", 0)),
                  _("Tiempo total de reproducción: {n} minutos").format(n=int(st.get("tiempo_total", 0) // 60)),
                  _("Tiempo medio por espectador: {n} segundos").format(n=int(st.get("tiempo_medio", 0)))]
        if st.get("paises"):
            lineas += ["", _("Países principales:")] + [
                _("  {p}: {n} espectadores").format(p=p["pais"], n=p["espectadores"]) for p in st["paises"]]
        texto_en_dialogo(padre, _("Estadísticas del vídeo"), "\n".join(lineas))
    ejecutar(lambda: peertube_service.stats_video(account_id, video_id), _fin)


def estadisticas_instancia(padre, account_id: int):
    etiquetas = {"totalUsers": _("Usuarios"), "totalVideos": _("Vídeos"), "totalLocalVideos": _("Vídeos locales"),
                 "totalVideoComments": _("Comentarios"), "totalInstanceFollowers": _("Instancias seguidoras"),
                 "totalInstanceFollowing": _("Instancias seguidas"), "totalLocalVideoViews": _("Reproducciones locales"),
                 "totalLocalVideoChannels": _("Canales locales"), "totalLocalPlaylists": _("Listas locales")}

    def _fin(st, err):
        if err or not st:
            notificar(_("No se pudieron cargar las estadísticas: {e}").format(e=err or ""))
            return
        texto_en_dialogo(padre, _("Estadísticas de la instancia"),
                         "\n".join(f"{etiquetas.get(k, k)}: {v}" for k, v in st.items()))
    ejecutar(lambda: peertube_service.stats_instancia(account_id), _fin)


# --------------------------------------------------------------------------- moderación
class DialogoAbusosPT(DialogoLista):
    """La cola de denuncias de la instancia (o las que has puesto tú)."""

    def __init__(self, padre, account_id: int, solo_mias: bool = False):
        super().__init__(padre, _("Mis denuncias") if solo_mias else _("Cola de denuncias"), _("Denuncias"))
        self.account_id, self.solo_mias, self.items = account_id, solo_mias, []
        if not solo_mias:
            self.boton(_("_Aceptar"), lambda: self._resolver(True))
            self.boton(_("_Rechazar"), lambda: self._resolver(False))
            self.boton(_("_Mensajes…"), self._mensajes)
            self.boton(_("_Borrar"), self._borrar)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando las denuncias…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las denuncias: {e}").format(e=err))
                return
            self.items = res or []
            lineas = []
            for a in self.items:
                objeto = a.get("video_titulo") or (_("Comentario") if a.get("comentario_denunciado") else a.get("denunciado", ""))
                lineas.append(_("{m}. Estado: {s}. Contra {d}. {o}").format(
                    m=a.get("motivo", ""), s=a.get("estado", ""), d=a.get("denunciado", ""), o=objeto))
            self.lista.poner(lineas)
            self.mostrar_estado(_("{n} denuncias").format(n=len(self.items)))
            self.lista.enfocar()
        ejecutar(lambda: peertube_service.mis_denuncias(self.account_id) if self.solo_mias
                 else peertube_service.listar_abusos(self.account_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _resolver(self, aceptar: bool):
        a = self._actual()
        if not a:
            return
        fn = peertube_service.aceptar_abuso if aceptar else peertube_service.rechazar_abuso
        _pedir(self, _("Aceptar denuncia") if aceptar else _("Rechazar denuncia"), _("_Comentario de moderación (opcional):"),
               lambda c: _hacer(self, lambda: fn(self.account_id, a["id"], comentario=c),
                                _("Denuncia aceptada.") if aceptar else _("Denuncia rechazada."), lambda _r: self._cargar()),
               texto_largo=True)

    def _borrar(self):
        a = self._actual()
        if a:
            confirmar_async(self, _("¿Borrar esta denuncia? Se elimina del sistema de moderación."),
                            lambda: _hacer(self, lambda: peertube_service.borrar_abuso(self.account_id, a["id"]),
                                           _("Denuncia borrada."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))

    def _mensajes(self):
        a = self._actual()
        if not a:
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron cargar los mensajes: {e}").format(e=err))
                return
            texto = "\n\n".join(f"{m.get('autor', '')}: {m.get('mensaje', '')}" for m in res or []) or _("(sin mensajes)")
            texto_en_dialogo(self, _("Mensajes de la denuncia"), texto)
            _pedir(self, _("Mensaje de moderación"), _("_Añadir un mensaje (opcional):"),
                   lambda m: m and _hacer(self, lambda: peertube_service.añadir_mensaje_abuso(self.account_id, a["id"], m),
                                          _("Mensaje añadido.")), texto_largo=True)
        ejecutar(lambda: peertube_service.mensajes_abuso(self.account_id, a["id"]), _fin)


class DialogoBloqueadosPT(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Vídeos bloqueados"), _("Vídeos bloqueados"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Desbloquear"), self._desbloquear)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los vídeos bloqueados: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([_("{t}. Motivo: {m}").format(t=b.get("titulo", ""), m=b.get("razon") or _("(sin motivo)"))
                              for b in self.items])
            self.mostrar_estado(_("{n} vídeos").format(n=len(self.items)))
            self.lista.enfocar()
        ejecutar(lambda: peertube_service.listar_videos_bloqueados(self.account_id), _fin)

    def _desbloquear(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            b = self.items[p]
            confirmar_async(self, _("¿Desbloquear «{t}»? Volverá a estar disponible.").format(t=b.get("titulo", "")),
                            lambda: _hacer(self, lambda: peertube_service.desbloquear_video(self.account_id, b["video_id"]),
                                           _("Vídeo desbloqueado."), lambda _r: self._cargar()), etiqueta_si=_("Desbloquear"))


# --------------------------------------------------------------------------- administración
class _ListaAdmin(DialogoLista):
    """Base: una lista que se rellena con `descargar()` y se dice con `fila(elemento)`."""

    TITULO = ""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, self.TITULO, self.TITULO)
        self.account_id, self.items = account_id, []
        self.botones_admin()
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def botones_admin(self):
        pass

    def descargar(self):
        return []

    def fila(self, it) -> str:
        return ""

    def _cargar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([self.fila(i) for i in self.items])
            self.mostrar_estado(_("{n} elementos").format(n=len(self.items)))
            self.lista.enfocar()
        ejecutar(self.descargar, _fin)

    def actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def hacer(self, fn, ok: str):
        _hacer(self, fn, ok, lambda _r: self._cargar())


class DialogoUsuariosAdminPT(_ListaAdmin):
    TITULO = _("Usuarios de la instancia")

    def botones_admin(self):
        self.boton(_("_Nuevo…"), self._nuevo)
        self.boton(_("_Rol y cuota…"), self._editar)
        self.boton(_("_Bloquear o desbloquear"), self._bloquear)
        self.boton(_("B_orrar"), self._borrar)

    def descargar(self):
        return peertube_service.admin_listar_usuarios(self.account_id)

    def fila(self, u):
        return _("{u}, {c}, rol {r}, {e}").format(u=u.get("username", ""), c=u.get("email", ""), r=u.get("rol", ""),
                                                  e=_("bloqueado") if u.get("bloqueado") else _("activo"))

    def _nuevo(self):
        d = DialogoBase(self, _("Nuevo usuario"), _("_Crear"))
        usuario = d.campo(_("_Nombre de usuario:"), Gtk.Entry(activates_default=True, width_chars=30))
        correo = d.campo(_("_Correo electrónico:"), Gtk.Entry(activates_default=True))
        clave = d.campo(_("Con_traseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        d.set_focus(usuario)

        def _ok():
            u, c, p = usuario.get_text().strip(), correo.get_text().strip(), clave.get_text()
            if not (u and c and p):
                notificar(_("Rellena el usuario, el correo y la contraseña."))
                return
            d.close()
            self.hacer(lambda: peertube_service.admin_crear_usuario(self.account_id, u, p, c), _("Usuario creado."))
        d.aceptar = _ok
        d.present()

    def _editar(self):
        u = self.actual()
        if not u:
            return
        d = DialogoBase(self, _("Rol de {u}").format(u=u["username"]), _("_Guardar"))
        rol = d.campo(_("_Rol:"), Gtk.DropDown.new_from_strings([_(n) for _i, n in ROLES]))
        rol.set_selected(next((i for i, (v, _n) in enumerate(ROLES) if v == u.get("rol_id")), 0))
        cuota = d.campo(_("_Cuota de vídeo en GB (-1 = sin límite):"), Gtk.Entry(text="-1", activates_default=True))
        d.set_focus(rol)

        def _ok():
            try:
                gb = float(cuota.get_text().strip())
                q = -1 if gb < 0 else int(gb * 1024 ** 3)
            except ValueError:
                q = None
            r = ROLES[rol.get_selected()][0]
            d.close()
            self.hacer(lambda: peertube_service.admin_editar_usuario(self.account_id, u["id"], role=r, videoQuota=q),
                       _("Usuario actualizado."))
        d.aceptar = _ok
        d.present()

    def _bloquear(self):
        u = self.actual()
        if u:
            bloquear = not u.get("bloqueado")
            self.hacer(lambda: peertube_service.admin_bloquear_usuario(self.account_id, u["id"], bloquear=bloquear),
                       _("Usuario bloqueado.") if bloquear else _("Usuario desbloqueado."))

    def _borrar(self):
        u = self.actual()
        if u:
            confirmar_async(self, _("¿Borrar al usuario «{u}»? Se eliminan sus vídeos y datos.").format(u=u["username"]),
                            lambda: self.hacer(lambda: peertube_service.admin_borrar_usuario(self.account_id, u["id"]),
                                               _("Usuario borrado.")), etiqueta_si=_("Borrar"))


class DialogoRegistrosAdminPT(_ListaAdmin):
    TITULO = _("Solicitudes de registro")

    def botones_admin(self):
        self.boton(_("_Aceptar"), lambda: self._resolver(True))
        self.boton(_("_Rechazar"), lambda: self._resolver(False))

    def descargar(self):
        return peertube_service.admin_listar_registros(self.account_id)

    def fila(self, r):
        return _("{u}, {c}, motivo: {m}").format(u=r.get("username", ""), c=r.get("email", ""), m=r.get("motivo", ""))

    def _resolver(self, aceptar: bool):
        r = self.actual()
        if not r:
            return
        fn = peertube_service.admin_aceptar_registro if aceptar else peertube_service.admin_rechazar_registro
        _pedir(self, _("Aceptar") if aceptar else _("Rechazar"), _("_Respuesta al solicitante (opcional):"),
               lambda t: self.hacer(lambda: fn(self.account_id, r["id"], respuesta=t),
                                    _("Registro aceptado.") if aceptar else _("Registro rechazado.")), texto_largo=True)


class DialogoFederacionAdminPT(_ListaAdmin):
    TITULO = _("Federación de la instancia")

    def __init__(self, padre, account_id: int):
        self.modo = "siguiendo"
        super().__init__(padre, account_id)

    def botones_admin(self):
        self.boton(_("Ver _siguiendo"), lambda: self._cambiar("siguiendo"))
        self.boton(_("Ver se_guidores"), lambda: self._cambiar("seguidores"))
        self.boton(_("Seguir _instancia…"), self._seguir)
        self.boton(_("_Dejar o rechazar"), self._quitar)
        self.boton(_("_Aceptar seguidor"), self._aceptar)

    def _cambiar(self, modo):
        self.modo = modo
        self._cargar()

    def descargar(self):
        return (peertube_service.admin_listar_seguidores if self.modo == "seguidores"
                else peertube_service.admin_listar_siguiendo)(self.account_id)

    def fila(self, f):
        return _("{h}, {e}").format(h=f.get("host", ""), e=f.get("estado", ""))

    def _seguir(self):
        _pedir(self, _("Seguir instancia"), _("_Instancia a seguir (host):"),
               lambda h: h and self.hacer(lambda: peertube_service.admin_seguir_instancias(self.account_id, [h]),
                                          _("Solicitud de seguimiento enviada.")))

    def _quitar(self):
        f = self.actual()
        if not f:
            return
        if self.modo == "seguidores":
            self.hacer(lambda: peertube_service.admin_aceptar_seguidor(self.account_id, f["host"], aceptar=False),
                       _("Seguidor rechazado."))
        else:
            confirmar_async(self, _("¿Dejar de seguir a {h}?").format(h=f["host"]),
                            lambda: self.hacer(lambda: peertube_service.admin_dejar_instancia(self.account_id, f["host"]),
                                               _("Has dejado de seguir la instancia.")), etiqueta_si=_("Dejar de seguir"))

    def _aceptar(self):
        f = self.actual()
        if not f or self.modo != "seguidores":
            notificar(_("Selecciona un seguidor en la lista de seguidores."))
            return
        self.hacer(lambda: peertube_service.admin_aceptar_seguidor(self.account_id, f["host"]), _("Seguidor aceptado."))


class DialogoTrabajosAdminPT(_ListaAdmin):
    TITULO = _("Trabajos del servidor")

    def __init__(self, padre, account_id: int):
        self.estado_trabajos = "completed"
        super().__init__(padre, account_id)

    def botones_admin(self):
        for est, etq in (("completed", _("_Completados")), ("failed", _("_Fallidos")), ("waiting", _("En _espera")),
                         ("active", _("Act_ivos"))):
            self.boton(etq, lambda e=est: self._ver(e))

    def _ver(self, estado):
        self.estado_trabajos = estado
        self._cargar()

    def descargar(self):
        return peertube_service.admin_listar_jobs(self.account_id, estado=self.estado_trabajos)

    def fila(self, j):
        return _("{t}, {s}. {e}").format(t=j.get("tipo", ""), s=j.get("estado", ""), e=(j.get("error", "") or "")[:200])


class DialogoPluginsAdminPT(_ListaAdmin):
    TITULO = _("Plugins instalados")

    def botones_admin(self):
        self.boton(_("_Instalar…"), self._instalar)
        self.boton(_("_Desinstalar"), self._desinstalar)

    def descargar(self):
        return peertube_service.admin_listar_plugins(self.account_id)

    def fila(self, p):
        return _("{n}, versión {v}, {p}").format(n=p.get("nombre", ""), v=p.get("version", ""), p=p.get("npm", ""))

    def _instalar(self):
        _pedir(self, _("Instalar plugin"), _("_Nombre del paquete npm del plugin:"),
               lambda n: n and self.hacer(lambda: peertube_service.admin_instalar_plugin(self.account_id, n), _("Plugin instalado.")))

    def _desinstalar(self):
        p = self.actual()
        if p:
            confirmar_async(self, _("¿Desinstalar «{n}»?").format(n=p["nombre"]),
                            lambda: self.hacer(lambda: peertube_service.admin_desinstalar_plugin(self.account_id, p["npm"]),
                                               _("Plugin desinstalado.")), etiqueta_si=_("Desinstalar"))


class DialogoRunnersAdminPT(_ListaAdmin):
    TITULO = _("Tokens de registro de runners")

    def botones_admin(self):
        self.boton(_("_Generar nuevo"), lambda: self.hacer(lambda: peertube_service.admin_generar_token_runner(self.account_id),
                                                           _("Token generado.")))
        self.boton(_("_Copiar token"), self._copiar)
        self.boton(_("_Borrar"), self._borrar)

    def descargar(self):
        return peertube_service.admin_listar_tokens_runner(self.account_id)

    def fila(self, t):
        return _("{t}, creado {f}").format(t=t.get("token", ""), f=t.get("fecha", ""))

    def _copiar(self):
        t = self.actual()
        if t and t.get("token"):
            copiar_al_portapapeles(t["token"], _("Token copiado. Úsalo para registrar un runner."))

    def _borrar(self):
        t = self.actual()
        if t:
            confirmar_async(self, _("¿Borrar este token de registro?"),
                            lambda: self.hacer(lambda: peertube_service.admin_borrar_token_runner(self.account_id, t["id"]),
                                               _("Token borrado.")), etiqueta_si=_("Borrar"))




# --------------------------------------------------------------------------- importaciones y avisos
class DialogoImportacionesPT(DialogoLista):
    """Tus importaciones de vídeo por dirección: su estado, reintentar, cancelar y borrar."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mis importaciones"), _("Lista de importaciones"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Reintentar"), lambda: self._sobre(peertube_service.reintentar_importacion, _("Importación reintentada.")))
        self.boton(_("_Cancelar"), lambda: self._sobre(peertube_service.cancelar_importacion, _("Importación cancelada.")))
        self.boton(_("_Borrar"), lambda: self._sobre(peertube_service.borrar_importacion, _("Importación borrada.")))
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las importaciones: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{i['titulo']}. {i['estado']}" + (f". {i['error']}" if i.get("error") else "") for i in self.items])
            self.mostrar_estado(_("{n} importaciones").format(n=len(self.items)) if self.items else _("No tienes importaciones."))
            self.lista.enfocar()
        ejecutar(lambda: peertube_service.listar_importaciones(self.account_id), _fin)

    def _sobre(self, fn, ok: str):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            i = self.items[p]
            _hacer(self, lambda: fn(self.account_id, i["id"]), ok, lambda _r: self._cargar())


AVISOS_PT = [("newVideoFromSubscription", "Nuevo vídeo de una suscripción"), ("newLiveFromSubscription", "Directo de una suscripción"),
             ("newCommentOnMyVideo", "Comentario en mis vídeos"), ("commentMention", "Menciones en comentarios"),
             ("newFollow", "Nuevo seguidor de mi canal"), ("myVideoPublished", "Mis vídeos publicados"),
             ("myVideoImportFinished", "Importaciones terminadas"), ("newPeerTubeVersion", "Nuevas versiones de PeerTube")]


class DialogoAvisosPT(DialogoBase):
    """De qué recibes aviso en PeerTube (aviso web). Solo cambia lo que se muestra; el resto se conserva."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Preferencias de notificaciones"), _("_Guardar"))
        self.account_id, self.prefs, self.casillas = account_id, {}, {}
        for clave, nombre in AVISOS_PT:
            c = Gtk.CheckButton(label=_(nombre))
            self.rejilla.attach(c, 1, self._fila, 1, 1)
            self._fila += 1
            self.casillas[clave] = c
        self.boton_aceptar.set_sensitive(False)
        self.set_focus(next(iter(self.casillas.values())))

        def _fin(prefs, err):
            if err:
                return notificar(_("No se pudieron cargar las preferencias: {e}").format(e=err))
            self.prefs = dict(prefs or {})
            for clave, c in self.casillas.items():
                try:
                    c.set_active(bool(int(self.prefs.get(clave, 0) or 0) & 1))
                except (TypeError, ValueError):
                    pass
            self.boton_aceptar.set_sensitive(True)
        ejecutar(lambda: peertube_service.preferencias_notificaciones(account_id), _fin)

    def aceptar(self):
        ajustes = dict(self.prefs)
        for clave, c in self.casillas.items():
            try:
                valor = int(ajustes.get(clave, 0) or 0)
            except (TypeError, ValueError):
                valor = 0
            ajustes[clave] = (valor | 1) if c.get_active() else (valor & ~1)
        self.close()
        _hacer(self.padre, lambda: peertube_service.guardar_preferencias_notificaciones(self.account_id, ajustes),
               _("Preferencias guardadas."))
