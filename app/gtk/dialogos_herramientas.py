# -*- coding: utf-8 -*-
"""Herramientas generales de la interfaz GTK (menú Herramientas): filtros de contenido, fragmentos de texto, copias de
seguridad, migración a otro ordenador, borrado de datos y visor de registros. Usan los mismos módulos de `app.core` que
la versión de Windows, así que los datos son compatibles."""

import glob
import logging
import os
import re
import sys
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from app.core import backup, data_cleanup
from app.core.config_manager import config
from app.core.content_filters import ContentFilterEngine
from app.core.paths import get_data_path
from app.core.speech import copiar_al_portapapeles, notificar
from app.core.task_manager import TareaDuplicadaError, task_manager
from app.services.wsocial.wsocial_common import LIMITE_CARACTERES

from .dialogos import DialogoBase, confirmar_async
from .dialogos_mastodon import DialogoLista, ListaSimple
from .fragmentos import instalar_ayudas, obtener_fragmentos

log = logging.getLogger(__name__)

SERVICIOS_FILTRO = [("all", _("Todos")), ("mastodon", _("Solo Mastodon")), ("bluesky", _("Solo Bluesky")),
                    ("wsocial", _("Solo W Social")), ("rss", _("Solo RSS")), ("x", _("Solo X (Twitter)"))]
CAMPOS_FILTRO = [("all", _("Texto y autor")), ("text", _("Solo el texto")), ("author", _("Solo el autor"))]
ACCIONES_FILTRO = [("hide", _("Ocultar")), ("mark_read", _("Marcar como leído")), ("star", _("Destacar")),
                   ("read_later", _("Leer más tarde (solo RSS)"))]


def _etiqueta(mapa, valor):
    for v, txt in mapa:
        if v == valor:
            return txt
    return valor


def _guardar_en(ventana, titulo: str, nombre: str, al_elegir):
    dlg = Gtk.FileDialog(title=titulo, initial_name=nombre)

    def _fin(origen, res):
        try:
            ruta = origen.save_finish(res).get_path()
        except GLib.Error:
            return
        al_elegir(ruta)
    dlg.save(ventana, None, _fin)


def _abrir_de(ventana, titulo: str, patrones, al_elegir):
    dlg = Gtk.FileDialog(title=titulo)
    filtro = Gtk.FileFilter()
    filtro.set_name(", ".join(patrones))
    for p in patrones:
        filtro.add_pattern(p)
    almacen = Gio.ListStore.new(Gtk.FileFilter)
    almacen.append(filtro)
    dlg.set_filters(almacen)

    def _fin(origen, res):
        try:
            ruta = origen.open_finish(res).get_path()
        except GLib.Error:
            return
        al_elegir(ruta)
    dlg.open(ventana, None, _fin)


def reiniciar_aplicacion(app):
    """Cierra esta instancia y lanza otra cuando haya terminado (la instancia única de GTK no deja arrancar la nueva
    antes), para aplicar una importación o una restauración pendientes."""
    try:
        config.save()
    except Exception:
        pass
    pid = os.getpid()
    orden = [sys.executable] + sys.argv
    script = 'while kill -0 {p} 2>/dev/null; do sleep 0.3; done; exec "$@"'.format(p=pid)
    import subprocess
    try:
        subprocess.Popen(["sh", "-c", script, "socialrss-reinicio"] + orden, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.error("No se pudo programar el reinicio: %s", e)
        notificar(_("No se pudo reiniciar automáticamente. Cierra y vuelve a abrir la aplicación."))
        return
    app.quit()


# --------------------------------------------------------------------------- filtros de contenido
class DialogoNuevoFiltro(DialogoBase):
    def __init__(self, padre, al_crear):
        super().__init__(padre, _("Nuevo filtro de contenido"), _("_Crear"))
        self.al_crear = al_crear
        self.patron = self.campo(_("_Palabra, frase o expresión regular:"), Gtk.Entry(activates_default=True, width_chars=40),
                                 _("El texto que, si aparece, activa la acción del filtro."))
        self.regex = self.campo("", Gtk.CheckButton(label=_("Es una e_xpresión regular")))
        self.servicio = self.campo(_("_Aplicar a:"), Gtk.DropDown.new_from_strings([t for _v, t in SERVICIOS_FILTRO]))
        self.campo_dd = self.campo(_("_Buscar en:"), Gtk.DropDown.new_from_strings([t for _v, t in CAMPOS_FILTRO]))
        self.accion = self.campo(_("A_cción:"), Gtk.DropDown.new_from_strings([t for _v, t in ACCIONES_FILTRO]))
        self.set_focus(self.patron)

    def aceptar(self):
        patron = self.patron.get_text().strip()
        if not patron:
            return
        if self.regex.get_active():
            try:
                re.compile(patron)
            except re.error as e:
                notificar(_("La expresión regular no es válida: {e}").format(e=e))
                return
        self.close()
        self.al_crear(SERVICIOS_FILTRO[self.servicio.get_selected()][0], patron, self.regex.get_active(),
                      CAMPOS_FILTRO[self.campo_dd.get_selected()][0], ACCIONES_FILTRO[self.accion.get_selected()][0])


class DialogoFiltrosContenido(DialogoLista):
    """Filtros que ocultan o marcan publicaciones y artículos por palabra clave (iguales que en Windows)."""

    def __init__(self, padre):
        super().__init__(padre, _("Filtros de contenido"), _("Lista de filtros de contenido"),
                         al_activar=lambda pos: self._alternar())
        self.filtros = []
        self.boton(_("_Nuevo filtro…"), self._nuevo)
        self.boton(_("Activar / _desactivar"), self._alternar)
        self.boton(_("_Eliminar"), self._eliminar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.filtros = ContentFilterEngine.listar_todos()
        self.lista.poner([
            _("{patron}. {tipo}. {servicio}. Buscar en: {campo}. Acción: {accion}. {estado}").format(
                patron=f["pattern"], tipo=_("Expresión regular") if f["is_regex"] else _("Texto"),
                servicio=_etiqueta(SERVICIOS_FILTRO, f["service"]), campo=_etiqueta(CAMPOS_FILTRO, f["field"]),
                accion=_etiqueta(ACCIONES_FILTRO, f["action"]), estado=_("Activo") if f["active"] else _("Desactivado"))
            for f in self.filtros])
        if self.filtros:
            self.lista.seleccion.set_selected(min(pos, len(self.filtros) - 1))
        self.mostrar_estado(_("{n} filtros").format(n=len(self.filtros)) if self.filtros
                            else _("No hay filtros. Pulsa «Nuevo filtro» para crear uno."))

    def _actual(self):
        p = self.lista.posicion()
        return self.filtros[p] if 0 <= p < len(self.filtros) else None

    def _nuevo(self):
        def _crear(servicio, patron, es_regex, campo, accion):
            ContentFilterEngine.crear(servicio, patron, patron, es_regex, campo, accion)
            self._cargar(len(self.filtros))
            notificar(_("Filtro creado."))
        DialogoNuevoFiltro(self, _crear).present()

    def _alternar(self):
        f = self._actual()
        if f:
            pos = self.lista.posicion()
            ContentFilterEngine.set_activo(f["id"], not f["active"])
            self._cargar(pos)
            notificar(_("Filtro desactivado.") if f["active"] else _("Filtro activado."))

    def _eliminar(self):
        f = self._actual()
        if not f:
            return

        def _hacer():
            ContentFilterEngine.eliminar(f["id"])
            self._cargar(self.lista.posicion())
            notificar(_("Filtro eliminado."))
        confirmar_async(self, _("¿Eliminar este filtro?"), _hacer, etiqueta_si=_("Eliminar"))


# --------------------------------------------------------------------------- fragmentos de texto
class DialogoFragmento(DialogoBase):
    def __init__(self, padre, al_aceptar, titulo: str = "", texto: str = ""):
        super().__init__(padre, _("Fragmento de texto"))
        self.al_aceptar = al_aceptar
        self.titulo_e = self.campo(_("_Título (para identificarlo):"), Gtk.Entry(text=titulo, width_chars=40))
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.texto.get_buffer().set_text(texto)
        marco = Gtk.ScrolledWindow(has_frame=True, min_content_height=140, min_content_width=420, child=self.texto)
        self.campo(_("_Texto:"), marco)
        lbl = self.rejilla.get_child_at(0, self._fila - 1)
        lbl.set_mnemonic_widget(self.texto)
        self.set_focus(self.titulo_e)

    def aceptar(self):
        buf = self.texto.get_buffer()
        texto = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if not texto.strip():
            notificar(_("El texto no puede estar vacío."))
            return
        self.close()
        self.al_aceptar(self.titulo_e.get_text().strip() or texto.strip()[:30], texto)


class DialogoFragmentos(DialogoLista):
    """Gestor de fragmentos reutilizables (los mismos que en Windows; se insertan al redactar con Ctrl+Mayús+J)."""

    def __init__(self, padre):
        super().__init__(padre, _("Fragmentos de texto"), _("Lista de fragmentos"), al_activar=lambda pos: self._editar())
        self.fragmentos = list(obtener_fragmentos())
        self.boton(_("_Nuevo…"), self._nuevo)
        self.boton(_("_Editar…"), self._editar)
        self.boton(_("E_liminar"), self._eliminar)
        self.cerrar_boton()
        self.connect("close-request", lambda *_a: self._guardar() and False)
        self._pintar()

    def _guardar(self):
        config.set("general", "snippets", self.fragmentos)
        return True

    def _pintar(self, pos: int = 0):
        self.lista.poner([f"{f.get('titulo', '')}. {' '.join((f.get('texto') or '').split())[:120]}" for f in self.fragmentos])
        if self.fragmentos:
            self.lista.seleccion.set_selected(min(pos, len(self.fragmentos) - 1))
        self.mostrar_estado(_("{n} fragmentos. Se insertan al redactar con Ctrl+Mayús+J.").format(n=len(self.fragmentos))
                            if self.fragmentos else _("No hay fragmentos. Pulsa «Nuevo» para crear uno."))

    def _nuevo(self):
        def _a(titulo, texto):
            self.fragmentos.append({"titulo": titulo, "texto": texto})
            self._guardar()
            self._pintar(len(self.fragmentos))
        DialogoFragmento(self, _a).present()

    def _editar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.fragmentos):
            return
        f = self.fragmentos[p]

        def _a(titulo, texto):
            self.fragmentos[p] = {"titulo": titulo, "texto": texto}
            self._guardar()
            self._pintar(p)
        DialogoFragmento(self, _a, f.get("titulo", ""), f.get("texto", "")).present()

    def _eliminar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.fragmentos):
            del self.fragmentos[p]
            self._guardar()
            self._pintar(p)
            notificar(_("Fragmento eliminado."))


class DialogoElegirFragmento(DialogoLista):
    """Lista de fragmentos para insertar uno en el campo de redacción."""

    def __init__(self, padre, al_elegir):
        super().__init__(padre, _("Insertar un fragmento"), _("Lista de fragmentos"), al_activar=lambda pos: self._elegir())
        self.al_elegir = al_elegir
        self.fragmentos = obtener_fragmentos()
        self.lista.poner([f.get("titulo") or f["texto"][:40] for f in self.fragmentos])
        self.mostrar_estado(_("Elige un fragmento y pulsa Intro."))
        self.boton(_("_Insertar"), self._elegir)
        self.cerrar_boton()

    def _elegir(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.fragmentos):
            self.close()
            self.al_elegir(self.fragmentos[p]["texto"])


# --------------------------------------------------------------------------- copias de seguridad
class DialogoPassword(DialogoBase):
    def __init__(self, padre, titulo: str, etiqueta: str, al_aceptar):
        super().__init__(padre, titulo)
        self.al_aceptar = al_aceptar
        self.entrada = self.campo(etiqueta, Gtk.PasswordEntry(show_peek_icon=True, activates_default=True, width_chars=36))
        self.set_focus(self.entrada)

    def aceptar(self):
        valor = self.entrada.get_text()
        self.close()
        self.al_aceptar(valor)


def _en_segundo_plano(trabajo, exito, fallo, clave: str, siempre=None):
    def _fin(res, err):
        if siempre:
            siempre()
        if isinstance(err, TareaDuplicadaError):
            notificar(_("Ya hay una operación de copias en curso; espera a que termine."))
        elif err:
            notificar(fallo.format(err))
        else:
            exito(res)
    task_manager.start_task(lambda ctx: trabajo(), callback=_fin, pass_context=True, unique_key=clave)


class DialogoCopias(DialogoLista):
    """Crear, restaurar, exportar e importar copias de seguridad."""

    def __init__(self, padre, app):
        super().__init__(padre, _("Copias de seguridad"), _("Lista de copias de seguridad"),
                         al_activar=lambda pos: self._restaurar())
        self.app = app
        self.copias = []
        self.boton(_("_Crear copia ahora"), self._crear)
        self.boton(_("_Restaurar la seleccionada"), self._restaurar)
        self.boton(_("_Exportar a un archivo…"), self._exportar)
        self.boton(_("_Importar desde un archivo…"), self._importar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.copias = backup.listar_backups()
        self.lista.poner([_("{nombre}. {fecha}. {kb} KB").format(nombre=c["nombre"], fecha=c["fecha"], kb=c["tamano_kb"])
                          for c in self.copias])
        self.mostrar_estado(_("Una copia incluye tus feeds, artículos, preferencias y credenciales cifradas. "
                              "Las credenciales solo se podrán usar en este mismo equipo.")
                            + " " + (_("{n} copias.").format(n=len(self.copias)) if self.copias else _("Todavía no hay copias.")))

    def _ocupado(self, ocupado: bool):
        for b in self.botones[:-1]:
            b.set_sensitive(not ocupado)

    def _lanzar(self, trabajo, exito, fallo):
        self._ocupado(True)
        _en_segundo_plano(trabajo, lambda _r: exito(), fallo, "backup:operacion", siempre=lambda: self._ocupado(False))

    def _crear(self):
        notificar(_("Creando la copia de seguridad…"))

        def _ok():
            self._cargar()
            notificar(_("Copia de seguridad creada."))
        self._lanzar(lambda: (backup.crear_backup(), backup.rotar_backups(10)), _ok, _("No se pudo crear la copia: {0}"))

    def _preparada(self, texto: str):
        notificar(texto)
        confirmar_async(self, texto + "\n\n" + _("¿Reiniciar ahora la aplicación?"),
                        lambda: reiniciar_aplicacion(self.app), etiqueta_si=_("Reiniciar"))

    def _restaurar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.copias):
            return
        c = self.copias[p]

        def _hacer():
            notificar(_("Preparando la restauración…"))
            self._lanzar(lambda: backup.restaurar_backup(c["ruta"]),
                         lambda: self._preparada(_("Restauración preparada. Se aplicará al reiniciar la aplicación.")),
                         _("No se pudo restaurar: {0}"))
        confirmar_async(self, _("Tus datos actuales se reemplazarán con los de esta copia al REINICIAR la aplicación "
                                "(se guarda una copia previa por seguridad).\n\n¿Continuar?"), _hacer,
                        etiqueta_si=_("Restaurar"))

    def _exportar(self):
        def _ruta(ruta):
            notificar(_("Exportando la copia…"))
            self._lanzar(lambda: backup.crear_backup(ruta), lambda: notificar(_("Copia exportada.")),
                         _("No se pudo exportar: {0}"))
        _guardar_en(self, _("Guardar la copia de seguridad como"), "socialrss_backup.zip", _ruta)

    def _importar(self):
        def _ruta(ruta):
            def _hacer():
                notificar(_("Preparando la importación…"))
                self._lanzar(lambda: backup.restaurar_backup(ruta),
                             lambda: self._preparada(_("Importación preparada. Se aplicará al reiniciar la aplicación.")),
                             _("No se pudo importar: {0}"))
            confirmar_async(self, _("Tus datos actuales se reemplazarán con los del archivo al REINICIAR la aplicación. "
                                    "¿Continuar?"), _hacer, etiqueta_si=_("Importar"))
        _abrir_de(self, _("Elegir la copia que importar"), ["*.zip"], _ruta)


# --------------------------------------------------------------------------- migración
class DialogoMigracion(DialogoBase):
    """Llevar toda la instalación a otro ordenador en un archivo protegido por contraseña."""

    def __init__(self, padre, app):
        super().__init__(padre, _("Migrar a otro ordenador"), _("_Cerrar"))
        self.app = app
        self.boton_cancelar.set_visible(False)
        txt = Gtk.Label(wrap=True, xalign=0, width_chars=60, max_width_chars=60, label=_(
            "Lleva toda tu instalación a otro ordenador: cuentas, inicios de sesión, configuración y base de datos. "
            "El archivo se protege con una contraseña; solo quien la conozca podrá importarlo. Es independiente de las "
            "copias de seguridad: esta es la forma de que en el otro equipo quede todo igual."))
        self.rejilla.attach(txt, 0, 0, 2, 1)
        self.b_exportar = Gtk.Button(label=_("_Exportar a un archivo…"), use_underline=True)
        self.b_exportar.connect("clicked", lambda *_a: self._exportar())
        self.b_importar = Gtk.Button(label=_("_Importar desde un archivo…"), use_underline=True)
        self.b_importar.connect("clicked", lambda *_a: self._importar())
        self.rejilla.attach(self.b_exportar, 0, 1, 2, 1)
        self.rejilla.attach(self.b_importar, 0, 2, 2, 1)
        self.set_focus(self.b_exportar)

    def _aviso(self, texto: str):
        notificar(texto)
        dlg = Gtk.AlertDialog()
        dlg.set_message(_("Migración"))
        dlg.set_detail(texto)
        dlg.set_buttons([_("Aceptar")])
        dlg.choose(self, None, lambda *_a: None)

    def _exportar(self):
        def _con_clave(clave):
            if len(clave) < 10:
                self._aviso(_("La contraseña debe tener al menos 10 caracteres. La exportación contiene tus credenciales "
                              "descifradas: una contraseña corta se puede romper por fuerza bruta."))
                return

            def _repetida(clave2):
                if clave2 != clave:
                    self._aviso(_("Las contraseñas no coinciden. Inténtalo de nuevo."))
                    return
                nombre = "socialrss_migracion_{0}.srmig".format(datetime.now().strftime("%Y%m%d_%H%M%S"))

                def _ruta(ruta):
                    self.b_exportar.set_sensitive(False)
                    notificar(_("Exportando. Espera, puede tardar según el tamaño de la base de datos…"))

                    def _bg(ctx):
                        from app.core import migration
                        return migration.exportar_migracion(ruta, clave)

                    def _fin(res, err):
                        self.b_exportar.set_sensitive(True)
                        if err:
                            self._aviso(_("No se pudo exportar: {0}").format(err))
                        else:
                            self._aviso(_("Exportación creada en:\n{0}\n\nLlévala al otro ordenador, instala SocialRSS allí "
                                          "y usa Importar con la misma contraseña.").format(res))
                    task_manager.start_task(_bg, callback=_fin, pass_context=True)
                _guardar_en(self, _("Guardar la exportación como"), nombre, _ruta)
            DialogoPassword(self, _("Contraseña"), _("_Repite la contraseña:"), _repetida).present()
        DialogoPassword(self, _("Contraseña"), _("_Contraseña para proteger la exportación (mínimo 10 caracteres):"),
                        _con_clave).present()

    def _importar(self):
        def _ruta(ruta):
            def _con_clave(clave):
                from app.core import migration
                try:
                    info = migration.info_paquete(ruta, clave)
                except Exception as e:
                    self._aviso(_("No se pudo abrir el archivo: {0}").format(e))
                    return
                resumen = _(
                    "Archivo válido.\n\nFecha de exportación: {0}\nCuentas con credenciales: {1}\n"
                    "Incluye base de datos: {2}\nSesiones de Telegram: {3}\n\n"
                    "ATENCIÓN: al importar se BORRARÁ y SUSTITUIRÁ POR COMPLETO todo lo que tengas en ESTE ordenador. "
                    "Esto NO se puede deshacer. Si quieres conservar lo actual, cancela y haz antes una copia de seguridad."
                    "\n\n¿Seguro que quieres continuar e importar?"
                ).format(info.get("fecha", "?"), info.get("num_cuentas_aprox", 0),
                         _("sí") if info.get("tiene_db") else _("no"), info.get("num_sesiones", 0))

                def _hacer():
                    self.b_importar.set_sensitive(False)
                    notificar(_("Preparando la importación. Espera…"))

                    def _fin(res, err):
                        self.b_importar.set_sensitive(True)
                        if err:
                            self._aviso(_("No se pudo importar: {0}").format(err))
                            return
                        notificar(_("Importación preparada. La aplicación se reiniciará para aplicarla."))
                        self.close()
                        reiniciar_aplicacion(self.app)
                    task_manager.start_task(lambda ctx: migration.importar_migracion(ruta, clave), callback=_fin,
                                            pass_context=True)
                confirmar_async(self, resumen, _hacer, etiqueta_si=_("Importar y sustituir todo"))
            DialogoPassword(self, _("Contraseña"), _("_Contraseña con la que se creó la exportación:"), _con_clave).present()
        _abrir_de(self, _("Elegir el archivo de migración"), ["*.srmig"], _ruta)

    def aceptar(self):
        self.close()


# --------------------------------------------------------------------------- borrado de datos
class DialogoBorrado(DialogoBase):
    """Borrar publicaciones, artículos o mensajes antiguos por red y antigüedad, y optimizar la base de datos."""

    TIPOS = [
        ("mastodon", _("Publicaciones de Mastodon"), lambda d, c: data_cleanup.borrar_mastodon(d, c)),
        ("bluesky", _("Publicaciones de Bluesky"), lambda d, c: data_cleanup.borrar_bluesky(d, c)),
        ("wsocial", _("Publicaciones de W Social"), lambda d, c: data_cleanup.borrar_wsocial(d, c)),
        ("rss", _("Artículos de RSS"), lambda d, c: data_cleanup.borrar_rss(d, c)),
        ("telegram", _("Mensajes cacheados de Telegram"), lambda d, c: data_cleanup.borrar_telegram(d)),
        ("x", _("Publicaciones de X (Twitter)"), lambda d, c: data_cleanup.borrar_x(d, c)),
        ("reddit", _("Publicaciones de Reddit"), lambda d, c: data_cleanup.borrar_reddit(d, c)),
        ("irc", _("Mensajes de IRC"), lambda d, c: data_cleanup.borrar_irc(d)),
        ("matrix", _("Mensajes de Matrix"), lambda d, c: data_cleanup.borrar_matrix(d)),
        ("xmpp", _("Mensajes de XMPP (con 0 días borra también la lista de contactos y grupos)"),
         lambda d, c: data_cleanup.borrar_xmpp(d)),
    ]

    def __init__(self, padre):
        super().__init__(padre, _("Borrar datos / liberar espacio"), _("_Borrar lo seleccionado"))
        self.resumen = Gtk.Label(wrap=True, xalign=0, width_chars=60, max_width_chars=60, accessible_role=Gtk.AccessibleRole.STATUS)
        self.rejilla.attach(self.resumen, 0, 0, 2, 1)
        self._fila = 1
        self.dias = self.campo(_("_Antigüedad: borrar lo de más de estos días (0 = todo):"),
                               Gtk.SpinButton.new_with_range(0, 3650, 1))
        self.dias.set_value(30)
        self.casillas = {}
        for clave, etiqueta, _fn in self.TIPOS:
            self.casillas[clave] = self.campo("", Gtk.CheckButton(label=etiqueta))
        self.conservar = self.campo("", Gtk.CheckButton(
            label=_("_Conservar lo marcado (favoritos, destacados, leer más tarde)"), use_underline=True, active=True))
        self.optimizar = self.campo("", Gtk.CheckButton(
            label=_("_Optimizar la base de datos al terminar (recomendado)"), use_underline=True, active=True))
        self._resumen()
        self.set_focus(self.dias)

    def _resumen(self):
        d = data_cleanup.contar()
        self.resumen.set_text(_(
            "Datos actuales: {0} publicaciones de Mastodon, {1} de Bluesky, {2} artículos de RSS, {3} mensajes de "
            "Telegram, {4} publicaciones de X, {5} mensajes de IRC, {6} de Matrix, {7} de XMPP, {8} de W Social y "
            "{9} de Reddit. Tamaño de la base de datos: {10} MB.").format(
            d["mastodon"], d["bluesky"], d["rss"], d["telegram"], d.get("x", 0), d.get("irc", 0), d.get("matrix", 0),
            d.get("xmpp", 0), d.get("wsocial", 0), d.get("reddit", 0), d["tamano_mb"]))

    def aceptar(self):
        elegidos = [(c, fn) for c, _e, fn in self.TIPOS if self.casillas[c].get_active()]
        if not elegidos:
            notificar(_("Selecciona al menos un tipo de datos a borrar."))
            return
        dias = int(self.dias.get_value())
        conservar = self.conservar.get_active()
        optimizar = self.optimizar.get_active()
        ambito = _("de más de {0} días").format(dias) if dias > 0 else _("TODOS")

        def _hacer():
            self.boton_aceptar.set_sensitive(False)
            notificar(_("Borrando datos…"))

            def _bg(ctx):
                total = sum(int(fn(dias, conservar) or 0) for _c, fn in elegidos)
                if optimizar:
                    data_cleanup.optimizar()
                return total

            def _fin(res, err):
                self.boton_aceptar.set_sensitive(True)
                if err:
                    notificar(_("Error al borrar: {0}").format(err))
                else:
                    self._resumen()
                    notificar(_("Borrado completado. {0} elementos eliminados.").format(res or 0))
            task_manager.start_task(_bg, callback=_fin, pass_context=True)
        confirmar_async(self, _("Se borrarán los datos seleccionados {0}. Esta acción no se puede deshacer.\n\n¿Continuar?")
                        .format(ambito), _hacer, etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- visor de registros
NIVELES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
RANGO = {n: i for i, n in enumerate(NIVELES)}
RE_CAB = re.compile(r"^\[\d{4}-\d\d-\d\d[ T](\d\d:\d\d:\d\d)[,.]?\d*\]\s+([A-Z]+)\b(.*)$")
RE_MODULO = re.compile(r"^\s*\[([^\]]*)\]\s*-?\s?(.*)$")
MAX_BYTES_COLA = 1_200_000


def parsear_registros(texto: str) -> list:
    """Trocea el texto del log en registros; las líneas de continuación (trazas) se unen al anterior."""
    regs, cur = [], None
    for linea in texto.splitlines():
        m = RE_CAB.match(linea)
        if m:
            if cur is not None:
                regs.append(cur)
            hora, nivel, resto = m.group(1), m.group(2), m.group(3)
            modulo, mensaje = "", resto.strip()
            mm = RE_MODULO.match(resto)
            if mm:
                modulo, mensaje = mm.group(1), mm.group(2)
            cur = {"hora": hora, "nivel": nivel if nivel in RANGO else "INFO", "modulo": modulo, "mensaje": mensaje}
        elif cur is None:
            cur = {"hora": "", "nivel": "INFO", "modulo": "", "mensaje": linea}
        else:
            cur["mensaje"] = (cur["mensaje"] + "\n" + linea) if cur["mensaje"] else linea
    if cur is not None:
        regs.append(cur)
    return regs


def leer_cola(ruta: str):
    """Lee solo el final del archivo. Devuelve (texto, truncado)."""
    tam = os.path.getsize(ruta)
    with open(ruta, "rb") as f:
        truncado = tam > MAX_BYTES_COLA
        if truncado:
            f.seek(tam - MAX_BYTES_COLA)
            datos = f.read()
            corte = datos.find(b"\n")
            if corte != -1:
                datos = datos[corte + 1:]
        else:
            datos = f.read()
    return datos.decode("utf-8", errors="replace"), truncado


class DialogoRegistro(Gtk.Window):
    """Visor de registros: sesión, nivel mínimo, búsqueda, copiar, abrir la carpeta, limpiar, nivel que se guarda y
    seguimiento en vivo. Es una ventana normal (no modal) para consultarla sin cerrar la aplicación."""

    def __init__(self, padre):
        super().__init__(title=_("Visor de registros"), transient_for=padre)
        self.set_default_size(900, 580)
        self.sesiones, self.registros, self.mostrados = [], [], []
        self._seguir = 0
        self._token = 0
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)

        def fila(*pares):
            f = Gtk.Box(spacing=8)
            for etiqueta, widget in pares:
                if etiqueta:
                    lbl = Gtk.Label(label=etiqueta, use_underline=True)
                    lbl.set_mnemonic_widget(widget)
                    f.append(lbl)
                f.append(widget)
            caja.append(f)
            return f

        self.c_sesion = Gtk.DropDown(hexpand=True)
        self.c_nivel = Gtk.DropDown.new_from_strings([_("Todos"), _("Depuración (DEBUG)"), _("Información (INFO)"),
                                                      _("Avisos (WARNING)"), _("Errores (ERROR)")])
        fila((_("_Sesión:"), self.c_sesion), (_("_Nivel mínimo:"), self.c_nivel))
        self.buscar = Gtk.SearchEntry(hexpand=True)
        self.buscar.update_property([Gtk.AccessibleProperty.LABEL], [_("Texto a buscar en el registro")])
        self.buscar.connect("activate", lambda *_a: self._buscar(1))
        b_ant = Gtk.Button(label=_("Buscar a_nterior"), use_underline=True)
        b_ant.connect("clicked", lambda *_a: self._buscar(-1))
        b_sig = Gtk.Button(label=_("Buscar s_iguiente"), use_underline=True)
        b_sig.connect("clicked", lambda *_a: self._buscar(1))
        fila((_("_Buscar:"), self.buscar), (None, b_ant), (None, b_sig))
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        caja.append(self.estado)
        self.lista = ListaSimple(_("Registros"), lambda pos: self._ver(pos))
        caja.append(self.lista)
        self.c_guardar = Gtk.DropDown.new_from_strings([_("Depuración (DEBUG): todo"), _("Información (INFO)"),
                                                        _("Avisos (WARNING)"), _("Errores (ERROR)")])
        self.c_guardar.set_selected({"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}.get(
            str(config.get("general", "log_level", "INFO")).upper(), 1))
        self.vivo = Gtk.CheckButton(label=_("Seguir en _vivo"), use_underline=True)
        fila((_("_Guardar en disco a partir de:"), self.c_guardar), (None, self.vivo))
        botones = Gtk.Box(spacing=8)
        for et, fn in ((_("_Recargar"), self._cargar), (_("_Copiar lo mostrado"), self._copiar),
                       (_("Abrir c_arpeta"), self._abrir_carpeta), (_("_Limpiar esta sesión"), self._limpiar),
                       (_("C_errar"), self.close)):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            botones.append(b)
        caja.append(botones)

        self.c_sesion.connect("notify::selected", lambda *_a: self._cargar())
        self.c_nivel.connect("notify::selected", lambda *_a: self._pintar(True))
        self.c_guardar.connect("notify::selected", lambda *_a: self._cambiar_guardado())
        self.vivo.connect("toggled", lambda *_a: self._alternar_vivo())
        self.connect("close-request", self._al_cerrar)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        for tecla, d in (("F3", 1), ("<Shift>F3", -1)):
            ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(tecla),
                                              Gtk.CallbackAction.new(lambda *_a, d=d: self._buscar(d) or True)))
        self.add_controller(ctl)
        self._cargar_sesiones()

    def present(self):
        super().present()
        self.lista.enfocar()

    # ---- sesiones
    def _cargar_sesiones(self):
        try:
            archivos = glob.glob(str(get_data_path("logs") / "session_*.log"))
            archivos.sort(key=os.path.getmtime, reverse=True)
        except Exception:
            archivos = []
        self.sesiones = archivos
        etiquetas = []
        for i, ruta in enumerate(archivos):
            nombre = os.path.basename(ruta)
            m = re.search(r"session_(\d{8})_(\d{6})", nombre)
            try:
                et = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S") if m else nombre
            except Exception:
                et = nombre
            etiquetas.append(et + ("  " + _("(actual)") if i == 0 else ""))
        self.c_sesion.set_model(Gtk.StringList.new(etiquetas or [_("(no hay registros todavía)")]))
        self._cargar()

    def _ruta(self):
        i = self.c_sesion.get_selected()
        return self.sesiones[i] if 0 <= i < len(self.sesiones) else None

    def _es_viva(self) -> bool:
        return self.c_sesion.get_selected() == 0 and bool(self.sesiones)

    # ---- carga
    def _cargar(self):
        ruta = self._ruta()
        if not ruta:
            self.registros, self.mostrados = [], []
            self.lista.poner([])
            self.estado.set_text(_("No hay registros todavía."))
            return
        self._token += 1
        token = self._token
        self.estado.set_text(_("Cargando…"))

        def _bg(ctx):
            return leer_cola(ruta)

        def _fin(res, err):
            if token != self._token:
                return
            if err:
                self.estado.set_text(_("No se pudo leer el registro: {e}").format(e=err))
                return
            texto, truncado = res
            self.registros = parsear_registros(texto)
            self._truncado = truncado
            self._pintar(False, al_final=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _minimo(self) -> int:
        return self.c_nivel.get_selected()   # 0 todos, 1 DEBUG, 2 INFO, 3 WARNING, 4 ERROR

    def _pintar(self, anunciar: bool = False, al_final: bool = False):
        minimo = self._minimo()
        self.mostrados = [r for r in self.registros if minimo == 0 or RANGO.get(r["nivel"], 1) >= minimo - 1]
        self.lista.poner([self._linea(r) for r in self.mostrados])
        if al_final and self.mostrados:
            self.lista.seleccion.set_selected(len(self.mostrados) - 1)
        resumen = _("{n} registros").format(n=len(self.mostrados))
        if getattr(self, "_truncado", False):
            resumen += ". " + _("Solo se muestra el final del archivo.")
        self.estado.set_text(resumen)
        if anunciar:
            notificar(resumen)
        self.lista.enfocar()

    @staticmethod
    def _linea(r: dict) -> str:
        return " ".join(x for x in (r["hora"], r["nivel"], r["modulo"], " ".join(r["mensaje"].split())[:400]) if x)

    def _ver(self, pos: int):
        if 0 <= pos < len(self.mostrados):
            from .dialogos_mastodon_extra import DialogoTextoLargo
            r = self.mostrados[pos]
            DialogoTextoLargo(self, f"{r['hora']} {r['nivel']}", f"{r['modulo']}\n\n{r['mensaje']}").present()

    def _buscar(self, direccion: int):
        texto = self.buscar.get_text().strip().lower()
        if not texto or not self.mostrados:
            return
        n = len(self.mostrados)
        p = self.lista.posicion()
        for i in range(1, n + 1):
            k = (p + direccion * i) % n
            r = self.mostrados[k]
            if texto in self._linea(r).lower() or texto in r["mensaje"].lower():
                self.lista.seleccion.set_selected(k)
                self.lista.enfocar()
                notificar(self._linea(r))
                return
        notificar(_("No se encontró."))

    # ---- acciones
    def _copiar(self):
        if not self.mostrados:
            return
        copiar_al_portapapeles("\n".join(f"{r['hora']} {r['nivel']} [{r['modulo']}] {r['mensaje']}" for r in self.mostrados),
                               _("Registros copiados"))

    def _abrir_carpeta(self):
        try:
            Gtk.show_uri(self, Gio.File.new_for_path(str(get_data_path("logs"))).get_uri(), 0)
        except Exception as e:
            notificar(_("No se pudo abrir la carpeta: {e}").format(e=e))

    def _limpiar(self):
        ruta = self._ruta()
        if not ruta:
            return
        aviso = _("¿Vaciar el archivo de esta sesión?")
        if self._es_viva():
            aviso += "\n\n" + _("Es la sesión en curso: se irán añadiendo entradas nuevas.")

        def _hacer():
            try:
                with open(ruta, "w", encoding="utf-8"):
                    pass
                self._cargar()
                notificar(_("Archivo de esta sesión vaciado."))
            except Exception as e:
                notificar(_("No se pudo vaciar: {0}").format(e))
        confirmar_async(self, aviso, _hacer, etiqueta_si=_("Vaciar"))

    def _cambiar_guardado(self):
        import logging as _lg
        elegido = ["DEBUG", "INFO", "WARNING", "ERROR"][self.c_guardar.get_selected()]
        config.set("general", "log_level", elegido)
        try:
            _lg.getLogger().setLevel(getattr(_lg, elegido, _lg.INFO))
        except Exception:
            pass
        notificar(_("Se guardará a partir de {0}. Se aplica del todo al reiniciar.").format(elegido))

    def _alternar_vivo(self):
        if self.vivo.get_active() and not self._seguir:
            self._seguir = GLib.timeout_add_seconds(2, self._tic)
            notificar(_("Siguiendo el registro en vivo."))
        elif not self.vivo.get_active() and self._seguir:
            GLib.source_remove(self._seguir)
            self._seguir = 0
            notificar(_("Seguimiento en vivo desactivado."))

    def _tic(self):
        if self._es_viva():
            self._cargar()
        return True

    def _al_cerrar(self, *_a):
        if self._seguir:
            GLib.source_remove(self._seguir)
            self._seguir = 0
        return False


# --------------------------------------------------------------------------- centro de notificaciones
class DialogoCentroNotificaciones(DialogoLista):
    """Todo lo que te menciona o interpela, de todas las redes, de más reciente a más antiguo. Intro abre el elemento
    en su red. Lee solo la caché local."""

    def __init__(self, padre, al_abrir):
        super().__init__(padre, _("Centro de notificaciones (todas las redes)"), _("Lista de notificaciones"),
                         al_activar=lambda pos: self._abrir())
        self.al_abrir = al_abrir
        self.items = []
        self.boton(_("_Abrir"), self._abrir)
        self.boton(_("_Actualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        from app.core.centro_notificaciones import reunir
        from .datos import fecha_legible
        self.mostrar_estado(_("Cargando notificaciones…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las notificaciones: {0}").format(err))
                return
            self.items = res or []
            self.lista.poner([". ".join(x for x in (it["red"], it["tipo"], it["de"], " ".join(it["texto"].split()),
                                                    fecha_legible(it["fecha"]) if it["fecha"] else "") if x)
                              for it in self.items])
            self.mostrar_estado(_("{0} notificaciones.").format(len(self.items)) if self.items
                                else _("No tienes notificaciones nuevas."))
            self.lista.enfocar()
        task_manager.start_task(lambda ctx: reunir(), callback=_fin, pass_context=True)

    def _abrir(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items) and self.items[p].get("abrir"):
            destino = self.items[p]["abrir"]
            self.close()
            self.al_abrir(destino)


# --------------------------------------------------------------------------- publicación cruzada
LIMITES_CRUZADA = {"mastodon": 500, "bluesky": 300, "wsocial": LIMITE_CARACTERES, "x": 280}
NOMBRES_RED = {"mastodon": "Mastodon", "bluesky": "Bluesky", "wsocial": "W Social", "x": "X"}


def destinos_publicacion_cruzada() -> list:
    """(red, account_id, etiqueta) de cada cuenta activa de una red habilitada que publica estados."""
    from app.core.database import db
    from app.core.feature_flags import servicio_habilitado
    destinos = []
    for red in ("mastodon", "bluesky", "wsocial", "x"):
        if not servicio_habilitado(red):
            continue
        for aid, nombre in db.query("SELECT id, name FROM accounts WHERE service_id=? AND active=1 ORDER BY position, id",
                                    (red,)):
            destinos.append((red, aid, f"{NOMBRES_RED[red]}: {nombre}"))
    return destinos


def publicar_en_cuenta(red: str, account_id: int, texto: str, imagenes, alts, etiqueta: str, estado: dict, idem: str):
    """Publica en UNA cuenta y actualiza el conteo de `estado`. Cada red avisa con una firma distinta y aquí se adaptan."""
    def _terminado(ok, motivo=None):
        estado["hechas"] += 1
        if not ok:
            estado["errores"].append(_("{0} ({1})").format(etiqueta, str(motivo)[:120]) if motivo else etiqueta)
        if estado["hechas"] >= estado["total"]:
            notificar(_("Publicado en todas las redes.") if not estado["errores"]
                      else _("Publicado, pero falló en: {0}").format("; ".join(estado["errores"])))
    try:
        if red == "mastodon":
            from app.services.mastodon.mastodon_composer import MastodonComposer
            MastodonComposer.publicar_toot_async(account_id, texto, media_paths=imagenes, alt_texts=alts,
                                                 idempotency_key=idem,
                                                 callback=lambda ok, msg: _terminado(bool(ok), None if ok else msg))
        elif red == "bluesky":
            from app.services.bluesky import bluesky_service
            bluesky_service.publicar_post_async(account_id, texto, media_paths=imagenes, alt_texts=alts,
                                                callback=lambda ok, msg: _terminado(bool(ok), None if ok else msg))
        elif red == "wsocial":
            from app.services.wsocial.wsocial_service import wsocial_service
            wsocial_service.publicar_post_async(account_id, texto, imagenes=imagenes, alt_texts=alts,
                                                callback=lambda ok, msg: _terminado(bool(ok), None if ok else msg))
        elif red == "x":
            from app.services.x.x_service import x_service
            x_service.publicar_async(account_id, texto, media_paths=imagenes, alt_texts=alts,
                                     callback=lambda _res, err: _terminado(err is None, err))
    except Exception as e:
        _terminado(False, e)


class DialogoPublicacionCruzada(DialogoBase):
    """Un solo texto (y hasta cuatro imágenes) publicado a la vez en las cuentas marcadas de varias redes."""

    def __init__(self, padre, texto_inicial: str = ""):
        super().__init__(padre, _("Publicación cruzada (varias redes)"), _("_Publicar en las cuentas marcadas"))
        self.imagenes = []          # (ruta, descripción)
        self._publicando = False
        self.destinos = destinos_publicacion_cruzada()
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True, left_margin=6,
                                  right_margin=6, top_margin=6, bottom_margin=6)
        self.texto.get_buffer().set_text(texto_inicial or "")
        instalar_ayudas(self.texto)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=140, min_content_width=480, hexpand=True,
                                   has_frame=True)
        self.campo(_("_Texto para todas las redes marcadas:"), marco)
        self.rejilla.get_child_at(0, self._fila - 1).set_mnemonic_widget(self.texto)
        self.contador = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        self.rejilla.attach(self.contador, 0, self._fila, 2, 1)
        self._fila += 1
        self.texto.get_buffer().connect("changed", lambda *_a: self._contar())
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl><Shift>space"),
                                          Gtk.CallbackAction.new(lambda *_a: self._decir_caracteres() or True)))
        self.texto.add_controller(ctl)

        self.checks = []
        if not self.destinos:
            self.rejilla.attach(Gtk.Label(label=_("No hay cuentas de Mastodon, Bluesky, W Social o X activas."), xalign=0),
                                0, self._fila, 2, 1)
            self._fila += 1
            self.boton_aceptar.set_sensitive(False)
        else:
            self.rejilla.attach(Gtk.Label(label=_("Publicar en estas cuentas:"), xalign=0), 0, self._fila, 2, 1)
            self._fila += 1
            self.todas = Gtk.CheckButton(label=_("Seleccionar _todas las cuentas"), use_underline=True)
            self.todas.connect("toggled", lambda *_a: self._marcar_todas())
            self.rejilla.attach(self.todas, 0, self._fila, 2, 1)
            self._fila += 1
            recordadas = set(config.get("general", "crosspost_ultimas", []) or [])
            for red, aid, etiqueta in self.destinos:
                cb = Gtk.CheckButton(label=etiqueta, active=f"{red}:{aid}" in recordadas)
                cb.connect("toggled", lambda *_a: self._sincronizar_todas())
                self.rejilla.attach(cb, 0, self._fila, 2, 1)
                self._fila += 1
                self.checks.append((red, aid, etiqueta, cb))
            self._sincronizar_todas()

        self.lbl_imagenes = Gtk.Label(xalign=0, label=_("Sin imágenes"))
        fila = Gtk.Box(spacing=8)
        b_add = Gtk.Button(label=_("Añadir _imagen…"), use_underline=True)
        b_add.connect("clicked", lambda *_a: self._anadir_imagen())
        b_quitar = Gtk.Button(label=_("_Quitar la última imagen"), use_underline=True)
        b_quitar.connect("clicked", lambda *_a: self._quitar_imagen())
        for w in (b_add, b_quitar, self.lbl_imagenes):
            fila.append(w)
        self.rejilla.attach(fila, 0, self._fila, 2, 1)
        self._fila += 1
        self._contar()
        self.set_focus(self.texto)

    def _texto(self) -> str:
        b = self.texto.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def _excedidas(self, n: int, redes=None) -> list:
        return [NOMBRES_RED[r] for r, lim in LIMITES_CRUZADA.items() if n > lim and (redes is None or r in redes)]

    def _contar(self):
        n = len(self._texto())
        excede = self._excedidas(n)
        self.contador.set_text(_("{0} caracteres. Excede el límite de: {1}").format(n, ", ".join(excede)) if excede
                               else _("{0} caracteres.").format(n))

    def _decir_caracteres(self):
        n = len(self._texto())
        excede = self._excedidas(n)
        notificar(_("{0} caracteres escritos. Excede el límite de: {1}.").format(n, ", ".join(excede)) if excede
                  else _("{0} caracteres escritos.").format(n))

    def _marcar_todas(self):
        valor = self.todas.get_active()
        for *_r, cb in self.checks:
            cb.set_active(valor)

    def _sincronizar_todas(self):
        if self.checks:
            self.todas.set_active(all(cb.get_active() for *_r, cb in self.checks))

    def _anadir_imagen(self):
        if len(self.imagenes) >= 4:
            notificar(_("Todas las redes admiten hasta cuatro imágenes."))
            return

        def _ruta(ruta):
            d = DialogoBase(self, _("Descripción de la imagen"), _("_Aceptar"))
            entrada = d.campo(_("_Descripción para quien no la ve:"), Gtk.Entry(activates_default=True, width_chars=50),
                              _("Describe la imagen. Se recomienda siempre."))
            ia = Gtk.Button(label=_("_Generar con IA"), use_underline=True)
            ia.connect("clicked", lambda *_a: self._descripcion_ia(ruta, entrada))
            d.rejilla.attach(ia, 1, d._fila, 1, 1)
            d._fila += 1

            def _listo():
                self.imagenes.append((ruta, entrada.get_text().strip()))
                self.lbl_imagenes.set_text(_("{0} imágenes").format(len(self.imagenes)))
                notificar(self.lbl_imagenes.get_text())
                d.close()
            d.aceptar = _listo
            d.set_focus(entrada)
            d.present()
        _abrir_de(self, _("Elige una imagen"), ["*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp"], _ruta)

    @staticmethod
    def _descripcion_ia(ruta: str, entrada):
        from .acciones_ia import texto_alternativo
        texto_alternativo(ruta, lambda t: (entrada.set_text(t), entrada.grab_focus()))

    def _quitar_imagen(self):
        if self.imagenes:
            self.imagenes.pop()
            self.lbl_imagenes.set_text(_("{0} imágenes").format(len(self.imagenes)) if self.imagenes else _("Sin imágenes"))
            notificar(self.lbl_imagenes.get_text())

    def aceptar(self):
        if self._publicando:
            return
        texto = self._texto().strip()
        if not texto and not self.imagenes:
            notificar(_("Escribe algo o añade una imagen."))
            return
        elegidos, vistos = [], set()
        for red, aid, etiqueta, cb in self.checks:
            if cb.get_active() and (red, aid) not in vistos:
                vistos.add((red, aid))
                elegidos.append((red, aid, etiqueta))
        if not elegidos:
            notificar(_("Marca al menos una cuenta."))
            return
        excede = self._excedidas(len(texto), {r for r, _a, _e in elegidos})

        def _publicar():
            import uuid
            config.set("general", "crosspost_ultimas", [f"{r}:{a}" for r, a, _e in elegidos])
            self._publicando = True
            self.boton_aceptar.set_sensitive(False)
            base = uuid.uuid4().hex
            estado = {"total": len(elegidos), "hechas": 0, "errores": []}
            notificar(_("Publicando en {0} cuentas…").format(estado["total"]))
            rutas = [r for r, _d in self.imagenes] or None
            alts = [d for _r, d in self.imagenes] or None
            for red, aid, etiqueta in elegidos:
                publicar_en_cuenta(red, aid, texto, rutas, alts, etiqueta, estado, f"{base}:{red}:{aid}")
            self.close()
        if excede:
            confirmar_async(self, _("El texto supera el límite de {0}. Puede que se rechace o se recorte en esas redes. "
                                    "¿Publicar de todas formas?").format(", ".join(excede)), _publicar,
                            etiqueta_si=_("Publicar"))
        else:
            _publicar()


# --------------------------------------------------------------------------- búsqueda global
ETIQUETAS_RED = {"mastodon": "Mastodon", "bluesky": "Bluesky", "wsocial": "W Social", "rss": "RSS", "telegram": "Telegram",
                 "x": "X", "irc": "IRC", "matrix": "Matrix", "xmpp": "XMPP"}


class DialogoBusquedaGlobal(DialogoLista):
    """Busca en texto completo en todo lo guardado en local. Intro abre el resultado en su red."""

    def __init__(self, padre, al_abrir, consulta: str = "", solo_rss: bool = False):
        super().__init__(padre, _("Buscar en todo el contenido"), _("Lista de resultados de la búsqueda"),
                         al_activar=lambda pos: self._abrir())
        from app.core.busqueda_global import AMBITOS
        self.al_abrir = al_abrir
        self.resultados = []
        self._ambitos = AMBITOS
        fila = Gtk.Box(spacing=8)
        self.entrada = Gtk.SearchEntry(hexpand=True)
        self.entrada.update_property([Gtk.AccessibleProperty.LABEL], [_("Texto a buscar")])
        self.entrada.connect("activate", lambda *_a: self._buscar())
        self.ambito = Gtk.DropDown.new_from_strings(
            [_("Todos")] + [_("Solo {0}").format(ETIQUETAS_RED[a]) for a in AMBITOS[1:]])
        self.ambito.update_property([Gtk.AccessibleProperty.LABEL], [_("Ámbito de la búsqueda")])
        etiqueta = Gtk.Label(label=_("_Buscar:"), use_underline=True)
        etiqueta.set_mnemonic_widget(self.entrada)
        for w in (etiqueta, self.entrada, self.ambito):
            fila.append(w)
        self.get_child().insert_child_after(fila, None)
        self.boton(_("_Buscar"), self._buscar)
        self.boton(_("_Abrir"), self._abrir)
        self.cerrar_boton()
        self.lista.anterior = self.entrada
        if solo_rss:
            self.ambito.set_selected(AMBITOS.index("rss"))
        if consulta:
            self.entrada.set_text(consulta)
            self._buscar()
        self.mostrar_estado(_("Escribe lo que buscas y pulsa Intro."))

    def present(self):
        Gtk.Window.present(self)
        self.entrada.grab_focus()

    def _buscar(self):
        consulta = self.entrada.get_text().strip()
        if not consulta:
            notificar(_("Escribe algo que buscar."))
            return
        from app.core.busqueda_global import buscar
        ambito = self.ambito.get_selected()
        self.mostrar_estado(_("Buscando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("Error en la búsqueda: {0}").format(err))
                return
            from .datos import fecha_legible
            self.resultados = res or []
            self.lista.poner([". ".join(x for x in (ETIQUETAS_RED.get(r["service"], r["service"]), r["origen"],
                                                    " ".join((r["texto"] or "").split())[:240],
                                                    fecha_legible(r["fecha"]) if r["fecha"] else "") if x)
                              for r in self.resultados])
            self.mostrar_estado(_("{0} resultados encontrados.").format(len(self.resultados)) if self.resultados
                                else _("Sin resultados."))
            if self.resultados:
                self.lista.enfocar()
        task_manager.start_task(lambda ctx: buscar(consulta, ambito), callback=_fin, pass_context=True)

    def _abrir(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.resultados):
            r = self.resultados[p]
            self.close()
            self.al_abrir(r)


def markdown_a_texto(texto: str) -> str:
    """Las respuestas de la IA suelen venir en markdown; para leerlas se quitan las marcas (asteriscos, almohadillas...)
    pasando por HTML. Si no hay librería de markdown, se deja tal cual."""
    try:
        import markdown
        from app.core.html_utils import html_a_texto
        return html_a_texto(markdown.markdown(texto or "", extensions=["extra", "sane_lists", "nl2br"])).strip()
    except Exception:
        return texto or ""


# --------------------------------------------------------------------------- borradores y programados
class DialogoColaUnificada(DialogoLista):
    """Borradores (de Mastodon y los guardados en la configuración por otras redes) y publicaciones programadas de
    Mastodon, de todas las cuentas, en una sola lista. Intro abre el borrador; en una programada, cancelarla."""

    def __init__(self, padre):
        super().__init__(padre, _("Borradores y programados de todas las redes"), _("Lista de borradores y programados"),
                         al_activar=lambda pos: self._abrir())
        self.items = []         # (etiqueta, tipo, red, datos)
        self.boton(_("_Abrir"), self._abrir)
        self.boton(_("_Cancelar la programada"), self._cancelar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        from app.core.database import db
        from app.services.mastodon import mastodon_borradores as mb
        from app.services.mastodon.mastodon_service import mastodon_service
        self.mostrar_estado(_("Reuniendo borradores y programados…"))
        items = []
        cuentas = [r[0] for r in db.query("SELECT id FROM accounts WHERE service_id='mastodon' AND active=1 ORDER BY position, id")]
        for acc in cuentas:
            for d in mb.listar_borradores(acc):
                items.append((_("[Borrador Mastodon] {0}").format((d["texto"] or _("(sin texto)"))[:80]), "borrador_db",
                              "mastodon", d))
        for red, clave in (("Mastodon", ("mastodon", "drafts")), ("Bluesky", ("bluesky", "drafts")), ("X", ("x", "drafts"))):
            datos = config.get(clave[0], clave[1], [])
            for d in (datos if isinstance(datos, list) else []):
                txt = (d.get("text", "") if isinstance(d, dict) else str(d)) or ""
                if txt.strip():
                    items.append((_("[Borrador {0}] {1}").format(red, txt[:80]), "borrador", red.lower(), txt))
        tg = config.get("telegram", "drafts", {})
        for txt in (tg.values() if isinstance(tg, dict) else []):
            if (txt or "").strip():
                items.append((_("[Borrador Telegram] {0}").format(txt[:80]), "borrador", "telegram", txt))
        self.items = items
        pendientes = {"n": len(cuentas)}

        def _programadas(acc):
            def _fin(res, err):
                pendientes["n"] -= 1
                for p in ([] if err else res or []):
                    cuando = (p.get("scheduled_at", "") or "")[:16].replace("T", " ")
                    self.items.append((_("[Programado Mastodon {0}] {1}").format(cuando, (p.get("text", "") or "")[:70]),
                                       "programado", "mastodon", dict(p, _cuenta=acc)))
                if pendientes["n"] <= 0:
                    self._pintar()
            mastodon_service.obtener_programados_async(acc, callback=_fin)
        for acc in cuentas:
            _programadas(acc)
        if not cuentas:
            self._pintar()

    def _pintar(self):
        self.lista.poner([e for e, *_r in self.items])
        self.mostrar_estado(_("{0} elementos.").format(len(self.items)) if self.items
                            else _("No tienes borradores ni publicaciones programadas."))
        self.lista.enfocar()

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir(self):
        item = self._actual()
        if not item:
            return
        _etq, tipo, red, datos = item
        if tipo == "borrador_db":
            self.close()
            self.padre.abrir_borrador(datos["id"])
        elif tipo == "borrador":
            self.close()
            self.padre.herr_publicacion_cruzada(datos)
        else:
            notificar(_("Una publicación programada se puede cancelar o cambiar de fecha desde «Programadas» de Mastodon."))

    def _cancelar(self):
        item = self._actual()
        if not item or item[1] != "programado":
            notificar(_("Selecciona una publicación programada."))
            return
        from app.services.mastodon.mastodon_service import mastodon_service
        datos = item[3]

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo cancelar: {e}").format(e=err) if err else _("Publicación cancelada"))
                self._cargar()
            mastodon_service.cancelar_programado_async(datos["_cuenta"], datos["id"], callback=_fin)
        confirmar_async(self, _("¿Cancelar esta publicación programada?"), _hacer, etiqueta_si=_("Cancelar publicación"))


# --------------------------------------------------------------------------- mostrar u ocultar buffers
REDES_BUFFERS = [("mastodon", _("Mastodon")), ("bluesky", _("Bluesky")), ("wsocial", _("W Social")), ("rss", _("RSS")),
                 ("telegram", _("Telegram")), ("x", _("X (Twitter)")), ("irc", _("IRC")), ("matrix", _("Matrix")),
                 ("xmpp", _("XMPP")), ("reddit", _("Reddit")), ("peertube", _("PeerTube"))]


def clave_ocultos(servicio: str) -> str:
    """Dónde guarda la configuración los buffers ocultos de una red (PeerTube los llama secciones)."""
    return "hidden_sections" if servicio == "peertube" else "hidden_buffers"


def buffers_de(servicio: str) -> list:
    """(clave de la configuración, etiqueta) de los buffers fijos que el árbol de esa red puede ocultar."""
    from .datos import CLAVE_OCULTOS
    if servicio == "rss":
        return [("all", _("Todos los artículos")), ("unread", _("No leídos")), ("favorites", _("Favoritos")),
                ("read_later", _("Leer más tarde")), ("today", _("Hoy"))]
    if servicio == "peertube":
        from . import datos_peertube
        return [(c, _(t)) for c, t in datos_peertube.SECCIONES.items()]
    from importlib import import_module
    modulo = import_module(f"app.gtk.datos_{servicio}")
    lista = [(CLAVE_OCULTOS.get((servicio, c), c), _(t)) for c, t in modulo.BUFFERS]
    if servicio == "xmpp":
        # Cada pasarela detectada (WhatsApp, Discord…) se puede ocultar entera.
        try:
            from app.core.database import db
            vistos = set()
            for (acc,) in db.query("SELECT id FROM accounts WHERE service_id='xmpp' AND active=1"):
                for p in modulo.puentes(acc):
                    if p["dominio"] not in vistos:
                        vistos.add(p["dominio"])
                        lista.append(("puente:" + p["dominio"], _("Pasarela: {0}").format(p["nombre"])))
        except Exception:
            pass
    return lista


def redes_con_buffers() -> list:
    from app.core.feature_flags import servicio_habilitado
    from . import datos_peertube, datos_reddit, datos_x
    disponible = {"x": datos_x.disponible(), "reddit": datos_reddit.disponible(), "peertube": datos_peertube.disponible()}
    return [(s, n) for s, n in REDES_BUFFERS if disponible.get(s, True) and servicio_habilitado(s)] or REDES_BUFFERS[:1]


class DialogoBuffers(DialogoBase):
    """Elegir qué buffers fijos aparecen en el árbol de cada red. Se guarda en la configuración que comparte la versión de
    Windows (los buffers que crea el usuario se cierran desde su menú contextual)."""

    def __init__(self, padre):
        super().__init__(padre, _("Mostrar u ocultar buffers"), _("_Aceptar"))
        self.redes = redes_con_buffers()
        self.ocultos = {s: set(config.get(s, clave_ocultos(s), []) or []) for s, _n in self.redes}
        self.actual = None
        self.red = self.campo(_("_Red:"), Gtk.DropDown.new_from_strings([n for _s, n in self.redes]))
        self.caja_checks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        marco = Gtk.ScrolledWindow(child=self.caja_checks, min_content_height=300, min_content_width=380, has_frame=True)
        self.campo(_("_Buffers visibles (marca los que quieres ver):"), marco)
        self.checks = []
        self.red.connect("notify::selected", lambda *_a: self._cambiar())
        self._cambiar()
        self.set_focus(self.red)

    def _guardar_estado(self):
        if self.actual is not None:
            for clave, cb in self.checks:
                (self.ocultos[self.actual].discard if cb.get_active() else self.ocultos[self.actual].add)(clave)

    def _cambiar(self):
        self._guardar_estado()
        servicio = self.redes[self.red.get_selected()][0]
        self.actual = servicio
        while (hijo := self.caja_checks.get_first_child()) is not None:
            self.caja_checks.remove(hijo)
        self.checks = []
        for clave, etiqueta in buffers_de(servicio):
            cb = Gtk.CheckButton(label=etiqueta, active=clave not in self.ocultos[servicio])
            self.caja_checks.append(cb)
            self.checks.append((clave, cb))

    def aceptar(self):
        self._guardar_estado()
        cambio = False
        for servicio, _n in self.redes:
            clave = clave_ocultos(servicio)
            previo = set(config.get(servicio, clave, []) or [])
            if previo != self.ocultos[servicio]:
                config.set(servicio, clave, sorted(self.ocultos[servicio]))
                cambio = True
        self.close()
        if cambio:
            self.padre.cargar_arbol()
            notificar(_("Buffers actualizados."))


# --------------------------------------------------------------------------- plantillas de lectura
# Redes cuyas filas se leen con las plantillas del usuario en la interfaz GTK (todas menos el visor de registros, que es de Windows).
REDES_CON_PLANTILLAS = ("mastodon", "bluesky", "telegram", "rss", "x", "irc", "matrix", "xmpp", "reddit", "peertube",
                        "wsocial", "inbox")


class DialogoPlantillas(Gtk.Window):
    """Cómo lee el lector de pantalla cada elemento de cada red: texto de la plantilla con campos entre llaves,
    modelos rápidos, vista previa con datos de ejemplo y restaurar los valores por defecto. Son las mismas plantillas
    (y la misma configuración) que usa la versión de Windows."""

    def __init__(self, padre):
        from app.core.plantillas import PLANTILLAS, REDES, plantillas_de_red
        super().__init__(title=_("Editor de plantillas de lectura"), transient_for=padre, modal=True)
        self.padre = padre
        self.set_default_size(720, 640)
        self._plantillas_de_red = plantillas_de_red
        self.redes = [(c, n()) for c, n in REDES if c in REDES_CON_PLANTILLAS]
        self.valores = {p["clave"]: config.get("accessibility", p["clave"], p["default"]) or p["default"]
                        for p in PLANTILLAS}
        self.iniciales = dict(self.valores)
        self.actual = None              # definición de la plantilla que se está editando
        self.de_red = []
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)

        def etiqueta(texto, widget):
            lbl = Gtk.Label(label=texto, use_underline=True, xalign=0)
            lbl.set_mnemonic_widget(widget)
            caja.append(lbl)

        def fila(texto, widget):
            etiqueta(texto, widget)
            caja.append(widget)
            return widget
        self.red = fila(_("_Red social:"), Gtk.DropDown.new_from_strings([n for _c, n in self.redes]))
        self.plantilla = fila(_("_Plantilla a editar:"), Gtk.DropDown.new_from_strings([""]))
        self.preset = fila(_("_Modelo rápido (rellena el texto de abajo):"), Gtk.DropDown.new_from_strings([""]))
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, left_margin=6, right_margin=6,
                                  top_margin=6, bottom_margin=6)
        etiqueta(_("_Texto de la plantilla (usa los campos entre llaves, por ejemplo {title}):"), self.texto)
        caja.append(Gtk.ScrolledWindow(child=self.texto, min_content_height=90, has_frame=True, vexpand=True))
        self.campos = ListaSimple(_("Campos disponibles"), lambda pos: self._insertar())
        self.campos.set_min_content_height(110)
        etiqueta(_("_Campos disponibles (Intro inserta el campo):"), self.campos.vista)
        caja.append(self.campos)
        self.vista_previa = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.vista_previa.update_property([Gtk.AccessibleProperty.LABEL], [_("Vista previa de la plantilla")])
        caja.append(self.vista_previa)
        botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        for et, fn in ((_("_Insertar el campo"), self._insertar), (_("_Leer la vista previa"), self._leer),
                       (_("R_estaurar esta"), self._restaurar), (_("Restaurar _todas"), self._restaurar_todas),
                       (_("_Guardar"), self._guardar), (_("_Cancelar"), self.close)):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            botones.append(b)
        caja.append(botones)
        self.red.connect("notify::selected", lambda *_a: self._cambiar_red())
        self.plantilla.connect("notify::selected", lambda *_a: self._cambiar_plantilla())
        self.preset.connect("notify::selected", lambda *_a: self._aplicar_preset())
        self.texto.get_buffer().connect("changed", lambda *_a: self._refrescar())
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self._cambiar_red()
        self.set_focus(self.red)

    def _texto(self) -> str:
        b = self.texto.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def _poner_texto(self, valor: str):
        self.texto.get_buffer().set_text(valor)

    def _recordar(self):
        if self.actual is not None:
            self.valores[self.actual["clave"]] = self._texto()

    def _cambiar_red(self):
        self._recordar()
        self.de_red = self._plantillas_de_red(self.redes[self.red.get_selected()][0])
        self.plantilla.set_model(Gtk.StringList.new([p["nombre"]() for p in self.de_red]))
        self.actual = None
        self._cambiar_plantilla()

    def _cambiar_plantilla(self):
        self._recordar()
        i = self.plantilla.get_selected()
        if not 0 <= i < len(self.de_red):
            return
        d = self.actual = self.de_red[i]
        self.campos.poner(["{{{0}}} — {1}".format(c, desc()) for c, desc in d["campos"]])
        self._presets = list(d.get("presets", []))
        self.preset.set_model(Gtk.StringList.new([_("(elige un modelo)")] + [n() for n, _p in self._presets]))
        self._poner_texto(self.valores.get(d["clave"], d["default"]))
        self._refrescar()

    def _aplicar_preset(self):
        i = self.preset.get_selected()
        if 0 < i <= len(getattr(self, "_presets", [])):
            self._poner_texto(self._presets[i - 1][1])
            notificar(_("Modelo aplicado."))

    def _insertar(self):
        p = self.campos.posicion()
        if self.actual is None or not 0 <= p < len(self.actual["campos"]):
            return
        marcador = "{{{0}}}".format(self.actual["campos"][p][0])
        self.texto.get_buffer().insert_at_cursor(marcador)
        self.texto.grab_focus()
        notificar(_("Insertado {0}.").format(marcador))

    def _refrescar(self):
        if self.actual is None:
            return
        from app.core.plantillas import columnas_de_plantilla, render_ejemplo
        plantilla = self._texto()
        cols = columnas_de_plantilla(plantilla)
        texto = _("Columnas (en orden): {0}").format(", ".join(h for _c, h in cols) if cols else _("(ninguna)"))
        try:
            ejemplo = render_ejemplo(self.actual["clave"], plantilla)
        except Exception:
            ejemplo = ""
        if ejemplo:
            texto += "\n" + _("Ejemplo de fila: {0}").format(ejemplo)
        self.vista_previa.set_text(texto)

    def _leer(self):
        notificar(self.vista_previa.get_text())

    def _restaurar(self):
        if self.actual is not None:
            self._poner_texto(self.actual["default"])
            notificar(_("Plantilla restaurada a su valor por defecto."))

    def _restaurar_todas(self):
        from app.core.plantillas import PLANTILLAS

        def _hacer():
            for p in PLANTILLAS:
                self.valores[p["clave"]] = p["default"]
            if self.actual is not None:
                self._poner_texto(self.valores[self.actual["clave"]])
            notificar(_("Todas las plantillas restauradas."))
        confirmar_async(self, _("¿Restaurar TODAS las plantillas a sus valores por defecto?"), _hacer,
                        etiqueta_si=_("Restaurar"))

    def _guardar(self):
        from app.core.plantillas import definicion
        self._recordar()
        cambio = False
        for clave, texto in self.valores.items():
            if not (texto or "").strip():
                d = definicion(clave)
                texto = d["default"] if d else texto
            if texto != self.iniciales.get(clave):
                config.set("accessibility", clave, texto)
                cambio = True
        self.close()
        if cambio:
            notificar(_("Plantillas guardadas."))
            self.padre.cargar_lista()


# --------------------------------------------------------------------------- ortografía
class DialogoOrtografia(DialogoLista):
    """Recorre las palabras desconocidas del texto y deja reemplazarlas, ignorarlas o escribir la corrección a mano."""

    def __init__(self, padre, texto: str, al_terminar):
        from app.services import spellcheck
        super().__init__(padre, _("Corrección ortográfica"), _("Sugerencias de corrección"),
                         al_activar=lambda pos: self._reemplazar())
        self._spell = spellcheck
        self.texto = texto
        self.al_terminar = al_terminar
        self.errores = spellcheck.revisar(texto)
        self.indice = 0
        self.manual = Gtk.Entry(hexpand=True, activates_default=False)
        self.manual.update_property([Gtk.AccessibleProperty.LABEL], [_("Corrección manual")])
        self.manual.connect("activate", lambda *_a: self._reemplazar(manual=True))
        fila = Gtk.Box(spacing=8)
        etiqueta = Gtk.Label(label=_("O _escribe la corrección a mano:"), use_underline=True)
        etiqueta.set_mnemonic_widget(self.manual)
        fila.append(etiqueta)
        fila.append(self.manual)
        self.get_child().insert_child_after(fila, self.lista)
        self.lista.seleccion.connect("notify::selected", self._sugerencia_elegida)
        self.boton(_("_Reemplazar"), self._reemplazar)
        self.boton(_("_Ignorar"), self._ignorar)
        self.boton(_("_Finalizar"), self._terminar)
        self.cerrar_boton()
        if not self.errores:
            self.mostrar_estado(_("No se encontraron errores ortográficos."))
            notificar(_("No se encontraron errores ortográficos."))
        else:
            self._mostrar()

    def _mostrar(self):
        if self.indice >= len(self.errores):
            notificar(_("Revisión terminada."))
            self._terminar()
            return
        palabra, sugerencias = self.errores[self.indice]
        self.mostrar_estado(_("Palabra desconocida: {0}. {1} sugerencias.").format(palabra, len(sugerencias)))
        self.lista.poner(sugerencias)
        self.manual.set_text(sugerencias[0] if sugerencias else palabra)
        self.lista.enfocar()

    def _sugerencia_elegida(self, *_a):
        """Al moverse por las sugerencias, la elegida pasa al campo de corrección (que se puede retocar)."""
        if self.indice < len(self.errores):
            sugerencias = self.errores[self.indice][1]
            p = self.lista.posicion()
            if 0 <= p < len(sugerencias):
                self.manual.set_text(sugerencias[p])

    def _reemplazar(self, manual: bool = False):
        if self.indice >= len(self.errores):
            return
        palabra = self.errores[self.indice][0]
        nueva = self.manual.get_text().strip()
        if nueva:
            self.texto = self._spell.reemplazar_palabra(self.texto, palabra, nueva)
            notificar(_("Reemplazado por {0}.").format(nueva))
        self.indice += 1
        self._mostrar()

    def _ignorar(self):
        self.indice += 1
        self._mostrar()

    def _terminar(self):
        self.close()
        self.al_terminar(self.texto)


# --------------------------------------------------------------------------- papelera
class DialogoPapelera(DialogoLista):
    """Artículos eliminados: se pueden restaurar o vaciar la papelera."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Papelera de artículos"), _("Lista de artículos en la papelera"),
                         al_activar=lambda pos: self._restaurar())
        self.account_id = account_id
        self.articulos = []
        self.boton(_("_Restaurar el seleccionado"), self._restaurar)
        self.boton(_("_Vaciar la papelera"), self._vaciar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        from app.services.rss.rss_service import rss_service
        self.articulos = rss_service.listar_papelera(self.account_id) if self.account_id else []
        self.lista.poner([f"{a.title or ''}. {a.author or ''}".rstrip(". ") for a in self.articulos])
        if self.articulos:
            self.lista.seleccion.set_selected(min(pos, len(self.articulos) - 1))
        self.mostrar_estado(_("{0} artículos eliminados (se pueden restaurar).").format(len(self.articulos))
                            if self.articulos else _("La papelera está vacía."))

    def _restaurar(self):
        from app.services.rss.rss_service import rss_service
        p = self.lista.posicion()
        if 0 <= p < len(self.articulos):
            rss_service.restaurar_de_papelera(self.articulos[p].id)
            self._cargar(p)
            notificar(_("Artículo restaurado."))
            self.padre.cargar_arbol()

    def _vaciar(self):
        if not self.articulos:
            notificar(_("La papelera ya está vacía."))
            return

        def _hacer():
            from app.services.rss.rss_service import rss_service
            n = rss_service.vaciar_papelera(self.account_id)
            self._cargar()
            notificar(_("Papelera vaciada. {0} artículos borrados.").format(n))
        confirmar_async(self, _("¿Borrar definitivamente todos los artículos de la papelera?"), _hacer,
                        etiqueta_si=_("Vaciar"))


# --------------------------------------------------------------------------- versiones anteriores de un artículo
class DialogoVersionesArticulo(DialogoLista):
    """Versiones guardadas cuando el medio corrigió la noticia y, para la elegida, qué frases se quitaron y cuáles se
    añadieron respecto a la versión actual."""

    def __init__(self, padre, article_id: str):
        from app.services.rss import rss_versiones
        super().__init__(padre, _("Versiones anteriores del artículo"), _("Versiones (la más reciente primero)"))
        self._rss_versiones = rss_versiones
        self.article_id = article_id
        self.versiones = rss_versiones.listar(article_id)
        self.cambios = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                    left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        self.cambios.update_property([Gtk.AccessibleProperty.LABEL], [_("Cambios respecto a la versión actual")])
        marco = Gtk.ScrolledWindow(child=self.cambios, min_content_height=200, has_frame=True)
        self.get_child().insert_child_after(marco, self.lista)
        self.lista.poner([f"{fecha.replace('T', ' ')}. {titulo}" for _v, fecha, titulo in self.versiones])
        self.lista.seleccion.connect("notify::selected", lambda *_a: self._mostrar())
        self.cerrar_boton()
        self.mostrar_estado(_("{0} versiones anteriores.").format(len(self.versiones)) if self.versiones
                            else _("Este artículo no tiene versiones anteriores."))
        self._mostrar()

    def _mostrar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.versiones):
            self.cambios.get_buffer().set_text(self._rss_versiones.diferencias(self.article_id, self.versiones[p][0]))


# --------------------------------------------------------------------------- prueba de sonidos
class DialogoPruebaSonidos(DialogoLista):
    """Elegir un paquete de sonidos y escuchar cada uno con su descripción (Intro lo reproduce)."""

    def __init__(self, padre):
        from app.services.audio.sound_manager import listar_paquetes_sonido, sonidos_del_paquete
        super().__init__(padre, _("Prueba de sonidos identificativos"), _("Lista de sonidos"),
                         al_activar=lambda pos: self._reproducir())
        self._sonidos_del_paquete = sonidos_del_paquete
        self.paquetes = listar_paquetes_sonido()
        self.sonidos = []
        if len(self.paquetes) > 1:
            fila = Gtk.Box(spacing=8)
            self.paquete = Gtk.DropDown.new_from_strings(self.paquetes)
            actual = config.get("audio", "sound_pack", "default")
            if actual in self.paquetes:
                self.paquete.set_selected(self.paquetes.index(actual))
            etiqueta = Gtk.Label(label=_("_Paquete de sonidos:"), use_underline=True)
            etiqueta.set_mnemonic_widget(self.paquete)
            fila.append(etiqueta)
            fila.append(self.paquete)
            self.get_child().insert_child_after(fila, None)
            self.paquete.connect("notify::selected", lambda *_a: self._cargar())
        else:
            self.paquete = None
        self.boton(_("_Reproducir"), self._reproducir)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        pack = self.paquetes[self.paquete.get_selected()] if self.paquete is not None else (
            self.paquetes[0] if self.paquetes else "default")
        self.sonidos = self._sonidos_del_paquete(pack)
        self.lista.poner([f"{desc}. {fname}" for fname, desc, _r in self.sonidos])
        self.mostrar_estado(_("{0} sonidos. Intro reproduce el elegido.").format(len(self.sonidos)) if self.sonidos
                            else _("Este paquete no tiene sonidos."))

    def _reproducir(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.sonidos):
            from app.services.audio.sound_manager import sound_manager
            if not sound_manager._initialized:
                sound_manager.inicializar()
            sound_manager.play_file(self.sonidos[p][2])
