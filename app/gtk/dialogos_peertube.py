# -*- coding: utf-8 -*-
"""Diálogos de PeerTube: alta de cuenta, comentarios, subir, importar, editar un vídeo y gestionar listas y canales."""

import logging
import os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar, speak
from app.services.peertube import peertube_service

from . import datos_peertube
from .datos import fecha_legible
from .dialogos import DialogoBase, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_reddit import DialogoTextoLibre, _campo_texto, _texto_de

log = logging.getLogger(__name__)


def _privacidades() -> list:
    return [_(n) for _i, n in datos_peertube.PRIVACIDADES]


def _privacidad_defecto() -> int:
    pref = int(config.get("peertube", "default_privacy", 1) or 1)
    return next((i for i, (v, _n) in enumerate(datos_peertube.PRIVACIDADES) if v == pref), 0)


# ---------------------------------------------------------------------------------------------- alta de cuenta
class DialogoCuentaPeerTube(DialogoBase):
    """Alta de una cuenta (instancia, usuario y contraseña) o, con `account_id`, volver a iniciar sesión en una existente."""

    def __init__(self, padre, al_terminar, account_id: int = None):
        super().__init__(padre, _("Volver a iniciar sesión en PeerTube") if account_id else _("Añadir cuenta de PeerTube"),
                         _("_Iniciar sesión"))
        self.al_terminar, self.account_id = al_terminar, account_id
        self.set_default_size(560, -1)
        self.instancia = self.usuario = None
        if not account_id:
            sugeridas = [u for u in (config.get("peertube", "saved_instances", []) or []) if isinstance(u, str)]
            self.instancia = self.campo(_("_Instancia (dirección de la web):"),
                                        Gtk.Entry(text=sugeridas[0] if sugeridas else "", activates_default=True, width_chars=40),
                                        _("Por ejemplo framatube.org. La cuenta tiene que existir ya en esa instancia."))
            self.usuario = self.campo(_("_Usuario o correo:"), Gtk.Entry(activates_default=True, width_chars=30))
        self.clave = self.campo(_("_Contraseña:"), Gtk.PasswordEntry(activates_default=True, show_peek_icon=True))
        self.otp = self.campo(_("Código de doble factor (si lo tienes _activado):"),
                              Gtk.Entry(activates_default=True, width_chars=12))
        self.estado = Gtk.Label(xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.STATUS)
        self.rejilla.attach(self.estado, 0, self._fila, 2, 1)
        self.set_focus(self.clave if account_id else self.instancia)

    def _progreso(self, texto: str):
        from app.core.plataforma import llamar_en_ui

        def _ui():
            self.estado.set_text(texto)
            speak(texto, interrupt=False)
        llamar_en_ui(_ui)

    def aceptar(self):
        clave, otp = self.clave.get_text(), self.otp.get_text().strip() or None
        if self.account_id:
            def hacer():
                return peertube_service.reautenticar(self.account_id, clave, otp=otp)
        else:
            instancia, usuario = self.instancia.get_text().strip(), self.usuario.get_text().strip()
            if not instancia or not usuario or not clave:
                notificar(_("Escribe la instancia, el usuario y la contraseña"))
                return

            def hacer():
                return peertube_service.iniciar_alta(instancia, usuario, clave, otp=otp, progreso=self._progreso)
        self.boton_aceptar.set_sensitive(False)
        self.estado.set_text(_("Conectando…"))

        def _fin(res, err):
            self.boton_aceptar.set_sensitive(True)
            if err:
                self.estado.set_text(str(err))
                notificar(_("No se pudo iniciar sesión: {e}").format(e=err))
                return
            self.close()
            notificar(_("Sesión iniciada: {u}").format(u=f"{res.get('username')}@{res.get('instancia')}"))
            self.al_terminar((res or {}).get("account_id") or self.account_id)
        ejecutar(hacer, _fin)


# ---------------------------------------------------------------------------------------------- comentarios
class DialogoComentariosPT(DialogoLista):
    """Los hilos de comentarios de un vídeo; Intro abre un hilo con sus respuestas anidadas y Volver regresa a la lista."""

    def __init__(self, padre, account_id: int, uuid: str, titulo: str):
        super().__init__(padre, _("Comentarios: {t}").format(t=titulo), _("Comentarios del vídeo"), lambda pos: self._abrir_o_volver())
        self.account_id, self.uuid = account_id, uuid
        self.items, self.hilo_abierto = [], None
        self.yo = (peertube_service.nombre_usuario(account_id) or "").lower()
        self.set_default_size(780, 520)
        self.b_abrir = self.boton(_("_Abrir el hilo"), self._abrir_o_volver)
        self.boton(_("_Nuevo comentario"), self._nuevo)
        self.boton(_("_Responder"), self._responder)
        self.boton(_("_Borrar"), self._borrar)
        self.boton(_("_Actualizar"), self._recargar)
        self.cerrar_boton()
        self._cargar_hilos()

    def _linea(self, c: dict) -> str:
        nivel = _("Nivel {0}.").format(c.get("nivel", 0) + 1) if c.get("nivel", 0) > 0 else ""
        texto = _("(comentario borrado)") if c.get("borrado") else c.get("texto", "")
        resp = _("{0} respuestas.").format(c["total_replies"]) if self.hilo_abierto is None and c.get("total_replies") else ""
        return " ".join(p for p in (nivel, f"{c.get('autor_display') or c.get('autor')}: {texto}.", resp,
                                    fecha_legible(c.get("fecha", ""))) if p)

    def _pintar(self, posicion: int = 0):
        self.lista.poner([self._linea(c) for c in self.items])
        if self.items:
            self.lista.seleccion.set_selected(min(posicion, len(self.items) - 1))
        self.lista.enfocar()

    def _cargar_hilos(self):
        self.hilo_abierto = None
        self.b_abrir.set_label(_("_Abrir el hilo"))
        self.mostrar_estado(_("Cargando comentarios…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los comentarios: {e}").format(e=err))
                return
            self.items = res or []
            self._pintar()
            self.mostrar_estado(_("{n} hilos").format(n=len(self.items)) if self.items else _("Este vídeo no tiene comentarios"))
        ejecutar(lambda: peertube_service.listar_hilos(self.account_id, self.uuid), _fin)

    def _cargar_hilo(self, thread_id):
        self.hilo_abierto = thread_id
        self.b_abrir.set_label(_("_Volver a los hilos"))
        self.mostrar_estado(_("Cargando el hilo…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar el hilo: {e}").format(e=err))
                return
            self.items = res or []
            self._pintar()
            self.mostrar_estado(_("Hilo con {n} comentarios").format(n=len(self.items)))
        ejecutar(lambda: peertube_service.cargar_hilo(self.account_id, self.uuid, thread_id), _fin)

    def _recargar(self):
        self._cargar_hilos() if self.hilo_abierto is None else self._cargar_hilo(self.hilo_abierto)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir_o_volver(self):
        if self.hilo_abierto is not None:
            return self._cargar_hilos()
        c = self._actual()
        if c:
            self._cargar_hilo(c["id"])

    def _enviar(self, fn, ok: str):
        def _fin(res, err):
            if err:
                notificar(_("No se pudo completar: {e}").format(e=err))
                return
            notificar(ok)
            self._recargar()
        ejecutar(fn, _fin)

    def _nuevo(self):
        DialogoTextoLibre(self, _("Nuevo comentario"), _("_Comentario:"), lambda t: self._enviar(
            lambda: peertube_service.publicar_comentario(self.account_id, self.uuid, t), _("Comentario publicado")),
            aceptar=_("_Publicar")).present()

    def _responder(self):
        c = self._actual()
        if not c:
            return
        DialogoTextoLibre(self, _("Responder"), _("_Respuesta:"), lambda t: self._enviar(
            lambda: peertube_service.responder_comentario(self.account_id, self.uuid, c["id"], t), _("Respuesta publicada")),
            aceptar=_("_Responder")).present()

    def _borrar(self):
        c = self._actual()
        if not c:
            return
        confirmar_async(self, _("¿Borrar este comentario?"), lambda: self._enviar(
            lambda: peertube_service.borrar_comentario(self.account_id, self.uuid, c["id"]), _("Comentario borrado")),
            etiqueta_si=_("Borrar"))


# ---------------------------------------------------------------------------------------------- formularios de vídeo
class _ConCanales:
    """Carga los canales del usuario en un desplegable."""

    def _cargar_canales(self, desplegable):
        self._canales = []

        def _fin(res, err):
            self._canales = res or []
            desplegable.set_model(Gtk.StringList.new([c["display"] for c in self._canales] or [_("(sin canales)")]))
        ejecutar(lambda: peertube_service.mis_canales(self.account_id), _fin)

    def _canal_id(self, desplegable):
        i = desplegable.get_selected()
        return self._canales[i]["id"] if 0 <= i < len(self._canales) else None


class DialogoSubirPT(DialogoBase, _ConCanales):
    """Subir un vídeo. Al aceptar llama a `al_aceptar(datos)`."""

    def __init__(self, padre, account_id: int, al_aceptar):
        super().__init__(padre, _("Subir vídeo a PeerTube"), _("_Subir"))
        self.account_id, self.al_aceptar, self._ruta = account_id, al_aceptar, ""
        self.set_default_size(620, -1)
        self.etiqueta_archivo = Gtk.Label(label=_("Ningún archivo elegido"), xalign=0, wrap=True)
        boton = Gtk.Button(label=_("_Elegir el vídeo…"), use_underline=True, halign=Gtk.Align.START)
        boton.connect("clicked", lambda *_a: self._elegir())
        caja = Gtk.Box(spacing=8)
        caja.append(boton)
        caja.append(self.etiqueta_archivo)
        self.rejilla.attach(caja, 1, self._fila, 1, 1)
        self._fila += 1
        self.canal = self.campo(_("_Canal:"), Gtk.DropDown.new_from_strings([_("Cargando canales…")]))
        self.titulo = self.campo(_("_Título:"), Gtk.Entry(activates_default=True, max_length=120, width_chars=50))
        self.descripcion, _w = _campo_texto(self, _("_Descripción:"), 90)
        self.etiquetas = self.campo(_("_Etiquetas (separadas por comas, máximo 5):"), Gtk.Entry(activates_default=True))
        self.privacidad = self.campo(_("_Privacidad:"), Gtk.DropDown.new_from_strings(_privacidades()))
        self.privacidad.set_selected(_privacidad_defecto())
        self.nsfw = self.campo(_("Contenido +_18:"), Gtk.Switch(halign=Gtk.Align.START))
        self._cargar_canales(self.canal)
        self.set_focus(boton)

    def _elegir(self):
        dlg = Gtk.FileDialog(title=_("Elegir el vídeo"))

        def _fin(origen, res):
            try:
                f = origen.open_finish(res)
            except Exception:
                return
            self._ruta = f.get_path()
            self.etiqueta_archivo.set_text(os.path.basename(self._ruta))
            if not self.titulo.get_text():
                self.titulo.set_text(os.path.splitext(os.path.basename(self._ruta))[0])
        dlg.open(self, None, _fin)

    def aceptar(self):
        titulo, canal_id = self.titulo.get_text().strip(), self._canal_id(self.canal)
        if not self._ruta or not titulo or not canal_id:
            notificar(_("El archivo, el título y el canal son obligatorios"))
            return
        etiquetas = [t.strip() for t in self.etiquetas.get_text().split(",") if t.strip()][:5]
        datos = {"ruta": self._ruta, "channel_id": canal_id, "name": titulo,
                 "privacy": datos_peertube.PRIVACIDADES[self.privacidad.get_selected()][0],
                 "description": _texto_de(self.descripcion).strip() or None, "tags": etiquetas or None,
                 "nsfw": self.nsfw.get_active()}
        self.close()
        self.al_aceptar(datos)


class DialogoImportarPT(DialogoBase, _ConCanales):
    """Importar un vídeo desde una dirección web o un enlace magnet."""

    def __init__(self, padre, account_id: int, al_aceptar):
        super().__init__(padre, _("Importar vídeo por dirección"), _("_Importar"))
        self.account_id, self.al_aceptar = account_id, al_aceptar
        self.set_default_size(620, -1)
        self.url = self.campo(_("_Dirección del vídeo (web o enlace magnet):"), Gtk.Entry(activates_default=True, width_chars=50))
        self.canal = self.campo(_("_Canal:"), Gtk.DropDown.new_from_strings([_("Cargando canales…")]))
        self.titulo = self.campo(_("_Título (opcional):"), Gtk.Entry(activates_default=True, max_length=120))
        self.privacidad = self.campo(_("_Privacidad:"), Gtk.DropDown.new_from_strings(_privacidades()))
        self.privacidad.set_selected(_privacidad_defecto())
        aviso = Gtk.Label(label=_("Importa solo contenido del que tengas derechos o con una licencia que lo permita."),
                          xalign=0, wrap=True)
        self.rejilla.attach(aviso, 0, self._fila, 2, 1)
        self._cargar_canales(self.canal)
        self.set_focus(self.url)

    def aceptar(self):
        url, canal_id = self.url.get_text().strip(), self._canal_id(self.canal)
        if not url or not canal_id:
            notificar(_("La dirección y el canal son obligatorios"))
            return
        datos = {"url": url, "channel_id": canal_id, "name": self.titulo.get_text().strip(),
                 "privacy": datos_peertube.PRIVACIDADES[self.privacidad.get_selected()][0],
                 "es_magnet": url.lower().startswith("magnet:")}
        self.close()
        self.al_aceptar(datos)


class DialogoEditarVideoPT(DialogoBase):
    """Editar el título, la descripción y la privacidad de un vídeo propio."""

    def __init__(self, padre, actuales: dict, al_aceptar):
        super().__init__(padre, _("Editar vídeo"), _("_Guardar"))
        self.al_aceptar = al_aceptar
        self.set_default_size(620, -1)
        self.titulo = self.campo(_("_Título:"), Gtk.Entry(text=actuales.get("name", ""), activates_default=True, max_length=120,
                                                          width_chars=50))
        self.descripcion, _w = _campo_texto(self, _("_Descripción:"), 120, actuales.get("description", "") or "")
        self.privacidad = self.campo(_("_Privacidad:"), Gtk.DropDown.new_from_strings(_privacidades()))
        self.privacidad.set_selected(next((i for i, (v, _n) in enumerate(datos_peertube.PRIVACIDADES)
                                           if v == actuales.get("privacy")), 0))
        self.set_focus(self.titulo)

    def aceptar(self):
        titulo = self.titulo.get_text().strip()
        if not titulo:
            notificar(_("El título es obligatorio"))
            return
        self.close()
        self.al_aceptar({"name": titulo, "description": _texto_de(self.descripcion).strip(),
                         "privacy": datos_peertube.PRIVACIDADES[self.privacidad.get_selected()][0]})


# ---------------------------------------------------------------------------------------------- listas y canales
class DialogoListaPT(DialogoBase):
    """Crear una lista de reproducción o editar una existente."""

    def __init__(self, padre, al_aceptar, lista: dict = None):
        super().__init__(padre, _("Editar la lista") if lista else _("Nueva lista de reproducción"),
                         _("_Guardar") if lista else _("_Crear"))
        self.al_aceptar = al_aceptar
        self.set_default_size(560, -1)
        self.nombre = self.campo(_("_Nombre:"), Gtk.Entry(text=(lista or {}).get("display", ""), activates_default=True,
                                                          width_chars=40))
        self.descripcion = self.campo(_("_Descripción (opcional):"), Gtk.Entry(text=(lista or {}).get("descripcion", "") or "",
                                                                            activates_default=True))
        self.privacidad = self.campo(_("_Privacidad:"), Gtk.DropDown.new_from_strings(_privacidades()))
        self.privacidad.set_selected(2 if not lista else 0)
        self.set_focus(self.nombre)

    def aceptar(self):
        nombre = self.nombre.get_text().strip()
        if not nombre:
            notificar(_("Escribe un nombre"))
            return
        self.close()
        self.al_aceptar(nombre, self.descripcion.get_text().strip(),
                        datos_peertube.PRIVACIDADES[self.privacidad.get_selected()][0])


class DialogoCanalPT(DialogoBase):
    """Crear un canal o editar uno existente."""

    def __init__(self, padre, al_aceptar, canal: dict = None):
        super().__init__(padre, _("Editar el canal") if canal else _("Nuevo canal"), _("_Guardar") if canal else _("_Crear"))
        self.al_aceptar, self.editando = al_aceptar, bool(canal)
        self.set_default_size(560, -1)
        self.identificador = None
        if not canal:
            self.identificador = self.campo(_("_Identificador (sin espacios):"), Gtk.Entry(activates_default=True, width_chars=30),
                                            _("Letras minúsculas, números y guiones bajos. No se puede cambiar después."))
        self.nombre = self.campo(_("_Nombre visible:"), Gtk.Entry(text=(canal or {}).get("display", ""), activates_default=True,
                                                                 width_chars=40))
        self.descripcion = self.campo(_("_Descripción (opcional):"), Gtk.Entry(text=(canal or {}).get("description", "") or "",
                                                                            activates_default=True))
        self.set_focus(self.identificador or self.nombre)

    def aceptar(self):
        nombre = self.nombre.get_text().strip()
        ident = self.identificador.get_text().strip().lower() if self.identificador else ""
        if not nombre or (not self.editando and not ident):
            notificar(_("Escribe el nombre y el identificador"))
            return
        self.close()
        self.al_aceptar(ident, nombre, self.descripcion.get_text().strip())
