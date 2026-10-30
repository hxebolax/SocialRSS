# -*- coding: utf-8 -*-
"""Telegram Drive en GTK: la nube personal de SocialRSS (los archivos viven como documentos de un canal privado).

Una ventana por cuenta, no modal, para seguir usando la aplicación mientras se transfiere. La lista se lee con la plantilla de
lectura «tgdrive_items» (la misma de Windows). Espacio marca elementos, Intro abre la carpeta o descarga el archivo, Retroceso
sube un nivel, Supr envía a la papelera, Alt+Izquierda y Alt+Derecha van atrás y adelante, Ctrl+Mayús+R actualiza. Los mismos
servicios y los mismos datos que usa la versión de Windows (app/services/tgdrive)."""

import logging
import os
from collections import defaultdict

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.plantillas import definicion, pulir_lectura
from app.core.speech import notificar
from app.services.tgdrive.tgdrive_service import tgdrive_service

from .dialogos import DialogoBase, DialogoElegir, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista, ListaSimple
from .dialogos_peertube_instancia import texto_en_dialogo
from .dialogos_reddit import _marco, _texto_de, _vista_texto
from .dialogos_telegram_admin import _hacer

log = logging.getLogger(__name__)

CLAVE_PLANTILLA = "tgdrive_items"
VENTANAS = {}           # cuenta -> ventana abierta


def tamano(n) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return ""


def abrir(padre, account_id: int):
    """Abre (o trae al frente) la ventana de Telegram Drive de una cuenta."""
    v = VENTANAS.get(account_id)
    if v is not None:
        v.present()
        return v
    v = VentanaTGDrive(padre, account_id)
    VENTANAS[account_id] = v
    v.connect("close-request", lambda *_a: VENTANAS.pop(account_id, None) and False)
    v.present()
    return v


class VentanaTGDrive(Gtk.Window):
    def __init__(self, padre, account_id: int):
        super().__init__(title=_("Telegram Drive"), transient_for=padre)
        from .mnemonicos import vigilar
        vigilar(self)
        self.padre, self.account_id = padre, account_id
        self.svc, self.repo = tgdrive_service, tgdrive_service.repo
        self.carpeta, self.historial, self.pos = None, [None], 0
        self.modo, self.busqueda = "normal", ""
        self.items, self.marcados, self.portapapeles = [], set(), None
        self.set_default_size(920, 620)
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8,
                       margin_end=8)
        self.set_child(raiz)
        self.ubicacion = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        raiz.append(self.ubicacion)
        self.lista = ListaSimple(_("Archivos y carpetas"), lambda pos: self._abrir())
        self.lista.set_vexpand(True)
        raiz.append(self.lista)
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        raiz.append(self.estado)
        botones = Gtk.Box(spacing=6, halign=Gtk.Align.START)
        raiz.append(botones)
        primero = None
        for etiqueta, fn in ((_("_Subir archivos…"), self.subir_archivos), (_("Subir _carpeta…"), self.subir_carpeta),
                             (_("_Descargar…"), self.descargar), (_("_Nueva carpeta…"), self.nueva_carpeta),
                             (_("_Renombrar…"), self.renombrar), (_("Enviar a la _papelera"), self.a_papelera),
                             (_("_Buscar…"), self.buscar), (_("Más _acciones…"), self.mas_acciones)):
            b = Gtk.Button(label=etiqueta, use_underline=True)
            b.connect("clicked", lambda *_a, f=fn: f())
            botones.append(b)
            primero = primero or b
        self.lista.siguiente = primero
        ctl = Gtk.EventControllerKey()
        ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._tecla)
        self.lista.vista.add_controller(ctl)
        atajos = Gtk.ShortcutController()
        for tecla, fn in (("Escape", self.close), ("<Ctrl><Shift>r", self.refrescar), ("<Ctrl>f", self.buscar),
                          ("<Ctrl>u", self.subir_archivos), ("<Ctrl>d", self.descargar), ("<Ctrl>x", self.cortar),
                          ("<Ctrl>c", self.copiar), ("<Ctrl>v", self.pegar), ("<Ctrl>w", self.close)):
            atajos.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(tecla),
                                                 Gtk.CallbackAction.new(lambda *_a, f=fn: f() or True)))
        self.add_controller(atajos)
        self.svc.set_callback_ui(self._cambio_servicio)
        try:
            r = self.svc.reanudar_pendientes() or {}
            if r.get("pendientes"):
                notificar(_("Reanudando {n} transferencias pendientes.").format(n=r["pendientes"]))
            if r.get("huerfanas"):
                notificar(_("{n} subidas no se pueden reanudar: su archivo de origen ya no existe.").format(n=r["huerfanas"]))
        except Exception:
            log.debug("No se pudieron reanudar las transferencias", exc_info=True)
        GLib.idle_add(self._asegurar_canal)

    # ------------------------------------------------------------------ canal
    def _asegurar_canal(self):
        if self.svc.obtener_channel_id(self.account_id) is not None:
            self.refrescar()
            self._mantenimiento()
            return GLib.SOURCE_REMOVE
        DialogoElegir(self, _("Configurar Telegram Drive"),
                      _("Telegram Drive necesita un canal privado donde guardar tus archivos. Solo tú tendrás acceso. ¿Qué quieres hacer?"),
                      [_("Crear un canal privado nuevo para la nube"), _("Ya tengo un canal: reconstruir el índice desde él")],
                      lambda i: self._crear_canal() if i == 0 else self.reconstruir_indice(), _("_Continuar")).present()
        return GLib.SOURCE_REMOVE

    def _crear_canal(self):
        notificar(_("Creando el canal privado…"))
        _hacer(self, lambda: self.svc.crear_canal(self.account_id), _("Canal privado creado. Ya puedes subir archivos."),
               lambda _r: self.refrescar())

    def _mantenimiento(self):
        def _bg():
            purgados = self.svc.purgar_papelera_caducada(self.account_id)
            self.svc.reintentar_eliminaciones(self.account_id)
            return purgados

        def _fin(n, err):
            if not err and n:
                notificar(_("Se vaciaron {n} elementos caducados de la papelera.").format(n=n))
                self.refrescar()
        ejecutar(_bg, _fin)

    def _cambio_servicio(self):
        """El servicio avisa (en el hilo de la interfaz) de que cambió algo en las transferencias."""
        self._pintar_estado()

    # ------------------------------------------------------------------ lista
    def _plantilla(self) -> str:
        d = definicion(CLAVE_PLANTILLA)
        return config.get("accessibility", CLAVE_PLANTILLA, "") or (d["default"] if d else "{nombre}")

    def _datos(self, it) -> dict:
        estados = {"normal": "", "trash": _("En la papelera"), "incomplete": _("Subiendo"), "deleted": _("Eliminándose")}
        return {"marcado": _("Sí") if it.id in self.marcados else _("No"), "nombre": it.name,
                "tipo": _("Carpeta") if it.es_carpeta else _("Archivo"), "tam": "" if it.es_carpeta else tamano(it.size),
                "modificado": (it.modified_at or "")[:16].replace("T", " "), "descripcion": it.descripcion or "",
                "estado": estados.get(it.estado, it.estado)}

    def _linea(self, it) -> str:
        return pulir_lectura(self._plantilla().format_map(defaultdict(str, self._datos(it))))

    def _ruta(self) -> str:
        if self.carpeta is None:
            return "/"
        it = self.repo.obtener_item(self.carpeta)
        return it.path if it else "/"

    def refrescar(self):
        foco = self._id_foco()
        if self.modo == "trash":
            self.items, donde = self.repo.listar_papelera(self.account_id), _("Papelera")
        elif self.modo == "search":
            self.items, donde = self.repo.buscar(self.account_id, self.busqueda), _("Búsqueda: {t}").format(t=self.busqueda)
        else:
            self.items, donde = self.repo.listar(self.account_id, self.carpeta), self._ruta()
        self.ubicacion.set_text(_("Ubicación: {u}").format(u=donde))
        self.set_title(_("Telegram Drive — {u}").format(u=donde))
        self.lista.poner([self._linea(i) for i in self.items])
        if foco is not None:
            pos = next((k for k, i in enumerate(self.items) if i.id == foco), None)
            if pos is not None:
                self.lista.seleccion.set_selected(pos)
        self.lista.enfocar()
        self._pintar_estado()

    def _pintar_estado(self):
        c = self.repo.contar(self.account_id)
        activas = [t for t in self.svc.obtener_transferencias(self.account_id)
                   if t["estado"] in ("pendiente", "preparando", "calculando_hash", "subiendo", "descargando", "reconstruyendo",
                                      "verificando")]
        self.estado.set_text(_("{a} archivos, {c} carpetas, {t}. Marcados: {m}. Transferencias activas: {n}.").format(
            a=c["archivos"], c=c["carpetas"], t=tamano(c["bytes"]), m=len(self.marcados), n=len(activas)))

    def _id_foco(self):
        p = self.lista.posicion()
        return self.items[p].id if 0 <= p < len(self.items) else None

    def _sel(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _seleccion(self):
        """Los marcados que se ven; si no hay ninguno, el elemento con el foco."""
        marcados = [i for i in self.items if i.id in self.marcados]
        return marcados or ([self._sel()] if self._sel() else [])

    def _tecla(self, ctl, keyval, keycode, estado):
        alt = bool(estado & Gdk.ModifierType.ALT_MASK)
        if keyval == Gdk.KEY_space and not estado & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            self.marcar()
        elif keyval == Gdk.KEY_BackSpace:
            self.subir_nivel()
        elif keyval == Gdk.KEY_Delete:
            self.a_papelera() if self.modo != "trash" else self.eliminar_definitivo()
        elif alt and keyval == Gdk.KEY_Left:
            self.atras()
        elif alt and keyval == Gdk.KEY_Right:
            self.adelante()
        elif keyval in (Gdk.KEY_F2,):
            self.renombrar()
        else:
            return False
        return True

    def marcar(self):
        it = self._sel()
        if not it:
            return
        (self.marcados.discard if it.id in self.marcados else self.marcados.add)(it.id)
        p = self.lista.posicion()
        self.lista.poner([self._linea(i) for i in self.items])
        self.lista.seleccion.set_selected(p)
        self.lista.enfocar()
        self._pintar_estado()

    # ------------------------------------------------------------------ navegación
    def _abrir(self):
        it = self._sel()
        if not it:
            return
        if it.es_carpeta and self.modo == "normal":
            self.navegar(it.id)
        elif it.es_archivo:
            self.descargar()

    def navegar(self, carpeta_id):
        self.carpeta = carpeta_id
        self.historial = self.historial[:self.pos + 1] + [carpeta_id]
        self.pos = len(self.historial) - 1
        self.modo = "normal"
        self.refrescar()

    def subir_nivel(self):
        if self.modo != "normal" or self.carpeta is None:
            self.modo = "normal"
            return self.refrescar()
        it = self.repo.obtener_item(self.carpeta)
        self.navegar(it.parent_id if it else None)

    def atras(self):
        if self.pos > 0:
            self.pos -= 1
            self.carpeta, self.modo = self.historial[self.pos], "normal"
            self.refrescar()

    def adelante(self):
        if self.pos < len(self.historial) - 1:
            self.pos += 1
            self.carpeta, self.modo = self.historial[self.pos], "normal"
            self.refrescar()

    def ver_papelera(self):
        self.modo = "trash"
        self.refrescar()

    # ------------------------------------------------------------------ operaciones sobre elementos
    def _pedir_nombre(self, titulo: str, con_nombre: bool, nombre: str = "", descripcion: str = "", al_aceptar=None, aceptar=None):
        d = DialogoBase(self, titulo, aceptar)
        e = d.campo(_("_Nombre:"), Gtk.Entry(text=nombre, activates_default=True, width_chars=40)) if con_nombre else None
        tv = _vista_texto()
        tv.get_buffer().set_text(descripcion)
        lbl = Gtk.Label(label=_("_Descripción (opcional):"), use_underline=True, xalign=0, yalign=0)
        lbl.set_mnemonic_widget(tv)
        d.rejilla.attach(lbl, 0, d._fila, 1, 1)
        d.rejilla.attach(_marco(tv, 100), 1, d._fila, 1, 1)
        d.set_focus(e or tv)

        def _ok():
            n = e.get_text().strip() if e else ""
            if con_nombre and not n:
                return
            texto = _texto_de(tv).strip()
            d.close()
            al_aceptar(n, texto)
        d.aceptar = _ok
        d.present()

    def nueva_carpeta(self):
        if self.modo != "normal":
            return

        def _crear(nombre, desc):
            if self.repo.existe_nombre(self.account_id, self.carpeta, nombre):
                return notificar(_("Ya existe algo con ese nombre."))
            self.repo.crear_carpeta(self.account_id, self.carpeta, nombre, self.svc.obtener_channel_id(self.account_id), desc)
            notificar(_("Carpeta creada."))
            self.refrescar()
        self._pedir_nombre(_("Nueva carpeta"), True, al_aceptar=_crear, aceptar=_("_Crear"))

    def renombrar(self):
        it = self._sel()
        if not it:
            return

        def _con(nombre, _desc):
            if nombre == it.name:
                return
            if self.repo.existe_nombre(self.account_id, it.parent_id, nombre):
                return notificar(_("Ya existe algo con ese nombre en la carpeta."))
            self.repo.renombrar(it.id, nombre)
            notificar(_("Renombrado."))
            self.refrescar()
        d = DialogoBase(self, _("Cambiar el nombre"), _("_Cambiar"))
        e = d.campo(_("_Nuevo nombre:"), Gtk.Entry(text=it.name, activates_default=True, width_chars=40))
        d.set_focus(e)
        d.aceptar = lambda: (d.close(), _con(e.get_text().strip(), "")) if e.get_text().strip() else None
        d.present()

    def cambiar_descripcion(self):
        it = self._sel()
        if it:
            self._pedir_nombre(_("Descripción de «{n}»").format(n=it.name), False, descripcion=it.descripcion or "",
                               al_aceptar=lambda _n, d: (self.repo.establecer_descripcion(it.id, d),
                                                         notificar(_("Descripción guardada.")), self.refrescar()),
                               aceptar=_("_Guardar"))

    def cortar(self):
        sel = self._seleccion()
        if sel:
            self.portapapeles = ("cortar", [i.id for i in sel])
            notificar(_("{n} elementos cortados.").format(n=len(sel)))

    def copiar(self):
        sel = self._seleccion()
        if sel:
            self.portapapeles = ("copiar", [i.id for i in sel])
            notificar(_("{n} elementos copiados.").format(n=len(sel)))

    def pegar(self):
        if not self.portapapeles or self.modo != "normal":
            return
        accion, ids = self.portapapeles
        hechos = 0
        for iid in ids:
            try:
                (self.repo.copiar if accion == "copiar" else self.repo.mover)(iid, self.carpeta)
                hechos += 1
            except ValueError as e:
                notificar(str(e))
        if accion == "cortar":
            self.portapapeles = None
        self.marcados.clear()
        notificar(_("{n} elementos pegados.").format(n=hechos))
        self.refrescar()

    def a_papelera(self):
        sel = self._seleccion()
        if not sel or self.modo != "normal":
            return

        def _hacer_():
            for it in sel:
                self.repo.a_papelera(it.id)
            self.marcados.clear()
            notificar(_("Enviado a la papelera."))
            self.refrescar()
        if config.get("telegram_drive", "confirmar_borrado", True):
            confirmar_async(self, _("¿Enviar {n} elementos a la papelera?").format(n=len(sel)), _hacer_, etiqueta_si=_("Enviar"))
        else:
            _hacer_()

    def restaurar(self):
        if self.modo != "trash":
            return notificar(_("Abre la papelera para restaurar."))
        for it in self._seleccion():
            self.repo.restaurar(it.id)
        self.marcados.clear()
        notificar(_("Restaurado."))
        self.refrescar()

    def eliminar_definitivo(self):
        sel = self._seleccion()
        if not sel:
            return
        ids = [i.id for i in sel]

        def _todo():
            for iid in ids:
                self.svc.eliminar_definitivo(self.account_id, iid)
            return True
        confirmar_async(self, _("¿Eliminar DEFINITIVAMENTE {n} elementos? No se puede deshacer.").format(n=len(sel)),
                        lambda: ejecutar(_todo, lambda r, e: (notificar(_("Error al eliminar: {e}").format(e=e) if e
                                                                        else _("Eliminado definitivamente.")),
                                                              self.marcados.clear(), self.refrescar())),
                        etiqueta_si=_("Eliminar"))

    def vaciar_papelera(self):
        confirmar_async(self, _("¿Vaciar la papelera? Se eliminarán definitivamente todos sus elementos."),
                        lambda: ejecutar(lambda: self.svc.vaciar_papelera(self.account_id),
                                         lambda r, e: (notificar(_("Error: {e}").format(e=e) if e else _("Papelera vaciada.")),
                                                       self.refrescar())), etiqueta_si=_("Vaciar"))

    def propiedades(self):
        it = self._sel()
        if not it:
            return
        partes = self.repo.obtener_partes(it.id) if it.es_archivo else []
        lineas = [_("Nombre: {v}").format(v=it.name), _("Tipo: {v}").format(v=_("Carpeta") if it.es_carpeta else _("Archivo")),
                  _("Ruta: {v}").format(v=it.path), _("Descripción: {v}").format(v=it.descripcion or _("(ninguna)")),
                  _("Estado: {v}").format(v=it.estado)]
        if it.es_archivo:
            lineas += [_("Tamaño: {v}").format(v=tamano(it.size)), _("Tipo de contenido: {v}").format(v=it.mime or _("(desconocido)")),
                       _("Partes: {v}").format(v=it.num_parts), _("Cifrado: {v}").format(v=_("sí") if it.encrypted else _("no")),
                       _("SHA-256: {v}").format(v=it.sha256 or "")]
            lineas += [_("  Parte {o}: {t}, mensaje {m}").format(o=p.orden + 1, t=tamano(p.size), m=p.message_id) for p in partes]
        lineas += [_("Creado: {v}").format(v=it.created_at), _("Modificado: {v}").format(v=it.modified_at)]
        texto_en_dialogo(self, _("Propiedades"), "\n".join(lineas))

    def buscar(self):
        d = DialogoBase(self, _("Buscar"), _("_Buscar"))
        e = d.campo(_("_Buscar por nombre:"), Gtk.Entry(activates_default=True, width_chars=36))
        d.set_focus(e)

        def _ok():
            t = e.get_text().strip()
            if t:
                d.close()
                self.busqueda, self.modo = t, "search"
                self.refrescar()
        d.aceptar = _ok
        d.present()

    # ------------------------------------------------------------------ subidas y descargas
    def _hay_canal(self) -> bool:
        if self.svc.obtener_channel_id(self.account_id) is None:
            self._asegurar_canal()
            return False
        return True

    def subir_archivos(self):
        if self.modo != "normal":
            self.modo = "normal"
            self.refrescar()
        if not self._hay_canal():
            return
        dlg = Gtk.FileDialog(title=_("Elige archivos para subir"))

        def _fin(origen, res):
            try:
                archivos = origen.open_multiple_finish(res)
            except Exception:
                return
            rutas = [archivos.get_item(i).get_path() for i in range(archivos.get_n_items())]
            if rutas:
                self._pedir_nombre(_("Descripción (opcional)"), False, al_aceptar=lambda _n, d: self._encolar(rutas, d),
                                   aceptar=_("_Subir"))
        dlg.open_multiple(self, None, _fin)

    def _encolar(self, rutas: list, descripcion: str, decision=None, i: int = 0, encolados: int = 0):
        """Encola los archivos preguntando qué hacer con cada nombre repetido (nunca se sobrescribe en silencio)."""
        while i < len(rutas):
            ruta = rutas[i]
            nombre = os.path.basename(ruta)
            if self.repo.existe_nombre(self.account_id, self.carpeta, nombre) and decision is None:
                def _elegir(k, _ruta=ruta, _i=i, _n=encolados):
                    dec = ("reemplazar", "ambos", "omitir")[k]
                    self._conflicto(rutas, descripcion, dec, _i, _n)
                DialogoElegir(self, _("Ya existe ese nombre"), _("Ya existe «{n}» en esta carpeta. ¿_Qué quieres hacer?").format(n=nombre),
                              [_("Reemplazar (quedan ambos hasta que borres el viejo)"), _("Conservar ambos con nombres distintos"),
                               _("Omitir este archivo")], _elegir, _("_Aplicar")).present()
                return
            self._encolar_uno(ruta, nombre, descripcion, decision)
            encolados += 1
            i += 1
        if encolados:
            notificar(_("{n} archivos añadidos a la cola de subida.").format(n=encolados))

    def _conflicto(self, rutas, descripcion, dec, i, encolados):
        ruta = rutas[i]
        if dec != "omitir":
            self._encolar_uno(ruta, os.path.basename(ruta), descripcion, dec)
            encolados += 1
        self._encolar(rutas, descripcion, None, i + 1, encolados)

    def _encolar_uno(self, ruta: str, nombre: str, descripcion: str, decision=None):
        if decision == "ambos" or (decision is None and self.repo.existe_nombre(self.account_id, self.carpeta, nombre)):
            nombre = self.repo.nombre_disponible(self.account_id, self.carpeta, nombre)
        self.svc.encolar_subida(self.account_id, self.carpeta, ruta, nombre, descripcion)

    def subir_carpeta(self):
        if not self._hay_canal():
            return
        dlg = Gtk.FileDialog(title=_("Elige una carpeta para subir (con su estructura)"))

        def _fin(origen, res):
            try:
                raiz = origen.select_folder_finish(res).get_path()
            except Exception:
                return
            self._pedir_nombre(_("Descripción (opcional)"), False, al_aceptar=lambda _n, d: self._encolar_carpeta(raiz, d),
                               aceptar=_("_Subir"))
        dlg.select_folder(self, None, _fin)

    def _encolar_carpeta(self, raiz: str, descripcion: str):
        canal = self.svc.obtener_channel_id(self.account_id)
        mapa = {raiz: self.repo.crear_carpeta(self.account_id, self.carpeta, os.path.basename(raiz.rstrip("\\/")) or "carpeta",
                                              canal, descripcion).id}
        total = 0
        for actual, subdirs, ficheros in os.walk(raiz):
            padre = mapa.get(actual)
            if padre is None:
                continue
            for sub in subdirs:
                mapa[os.path.join(actual, sub)] = self.repo.crear_carpeta(self.account_id, padre, sub, canal).id
            for f in ficheros:
                self.svc.encolar_subida(self.account_id, padre, os.path.join(actual, f), f)
                total += 1
        notificar(_("Carpeta añadida: {n} archivos en cola.").format(n=total))
        self.refrescar()

    def descargar(self):
        sel = self._seleccion()
        if not sel:
            return
        dlg = Gtk.FileDialog(title=_("Elige dónde guardar la descarga"))

        def _fin(origen, res):
            try:
                destino = origen.select_folder_finish(res).get_path()
            except Exception:
                return
            total = sum(self._encolar_descarga(it, destino) for it in sel)
            notificar(_("{n} archivos añadidos a la cola de descarga.").format(n=total))
        dlg.select_folder(self, None, _fin)

    def _encolar_descarga(self, it, destino: str) -> int:
        if it.es_archivo:
            self.svc.encolar_descarga(self.account_id, it.id, destino)
            return 1
        sub = os.path.join(destino, it.name)
        os.makedirs(sub, exist_ok=True)
        return sum(self._encolar_descarga(h, sub) for h in self.repo.listar(self.account_id, it.id))

    # ------------------------------------------------------------------ índice, canal y transferencias
    def reconstruir_indice(self):
        def _bg():
            try:
                return {"ok": self.svc.reconstruir_indice(self.account_id)}
            except ValueError as e:
                return {"aviso": str(e)}

        def _fin(res, err):
            if err or (res or {}).get("aviso"):
                return texto_en_dialogo(self, _("Reconstruir índice"), str(err or res["aviso"]))
            notificar(_("Índice reconstruido: {n} elementos.").format(n=((res or {}).get("ok") or {}).get("items", 0)))
            self.carpeta, self.historial, self.pos = None, [None], 0
            self.refrescar()
        confirmar_async(self, _("Se buscará tu canal de Telegram Drive y se reconstruirá desde él la lista de archivos. Esto "
                                "reemplaza el índice local. ¿Continuar?"), lambda: ejecutar(_bg, _fin), etiqueta_si=_("Reconstruir"))

    def exportar_indice(self):
        _hacer(self, lambda: self.svc.exportar_indice(self.account_id), _("Índice exportado al canal."))

    def revisar_canal(self):
        def _fin(res, err):
            if err:
                return texto_en_dialogo(self, _("Revisar el canal"), str(err))
            orf, rotos = res.get("orfanos", []), res.get("items_rotos", [])
            resumen = _("El canal tiene {t} documentos; {r} coinciden con tu índice.\n\nDocumentos huérfanos: {o}\n"
                        "Archivos con partes que faltan en el canal: {b}").format(
                            t=res.get("total_canal", 0), r=res.get("referenciados", 0), o=len(orf), b=len(rotos))
            if not orf and not rotos:
                return texto_en_dialogo(self, _("Revisar el canal"), resumen + "\n\n" + _("Todo está en orden."))

            def _reparar():
                def _bg():
                    return (self.svc.limpiar_orfanos_canal(self.account_id, orf) if orf else 0,
                            self.svc.quitar_items_rotos(self.account_id, rotos) if rotos else 0)
                ejecutar(_bg, lambda r, e: (notificar(_("Error al reparar: {e}").format(e=e) if e else _(
                    "Reparado: {o} huérfanos borrados del canal, {b} archivos rotos quitados del índice.").format(o=r[0], b=r[1])),
                    self.refrescar()))
            confirmar_async(self, resumen + "\n\n" + _("¿Reparar? Se borran del canal los huérfanos y se quitan del índice los rotos."),
                            _reparar, etiqueta_si=_("Reparar"))
        ejecutar(lambda: self.svc.revisar_canal(self.account_id), _fin)

    def eliminar_drive(self):
        c = self.repo.contar(self.account_id)

        def _segunda():
            confirmar_async(self, _("Última confirmación: se perderá TODO lo que tienes en la nube. ¿Eliminar definitivamente?"),
                            lambda: ejecutar(lambda: self.svc.eliminar_drive_completo(self.account_id, borrar_canal=True),
                                             lambda r, e: (notificar(_("No se pudo eliminar la nube: {e}").format(e=e)), self.refrescar())
                                             if e else (notificar(_("Telegram Drive eliminado.")), self.close())),
                            etiqueta_si=_("Eliminar para siempre"))
        confirmar_async(self, _("Vas a ELIMINAR por completo tu Telegram Drive.\n\nSe borrará el canal privado de Telegram y con él "
                                "TODOS tus archivos ({a} archivos, {c} carpetas, {t}). Esto NO se puede deshacer.\n\n¿Seguro?").format(
                                    a=c["archivos"], c=c["carpetas"], t=tamano(c["bytes"])), _segunda, etiqueta_si=_("Continuar"))

    def mas_acciones(self):
        acciones = [(_("Cambiar la descripción…"), self.cambiar_descripcion), (_("Cortar"), self.cortar), (_("Copiar"), self.copiar),
                    (_("Pegar"), self.pegar), (_("Propiedades"), self.propiedades), (_("Marcar o desmarcar"), self.marcar),
                    (_("Subir un nivel"), self.subir_nivel), (_("Atrás"), self.atras), (_("Adelante"), self.adelante)]
        if self.modo == "trash":
            acciones += [(_("Restaurar"), self.restaurar), (_("Eliminar definitivamente"), self.eliminar_definitivo),
                         (_("Vaciar la papelera"), self.vaciar_papelera), (_("Volver a mis archivos"), self.subir_nivel)]
        else:
            acciones.append((_("Ver la papelera"), self.ver_papelera))
        acciones += [(_("Transferencias…"), self.transferencias), (_("Abrir la carpeta de descargas"), self.carpeta_descargas),
                     (_("Reconstruir el índice desde el canal"), self.reconstruir_indice),
                     (_("Exportar el índice al canal"), self.exportar_indice), (_("Revisar el canal…"), self.revisar_canal),
                     (_("Reintentar las eliminaciones pendientes"), self.reintentar_eliminaciones),
                     (_("Eliminar Telegram Drive por completo…"), self.eliminar_drive)]
        DialogoElegir(self, _("Más acciones"), _("_Acción:"), [a[0] for a in acciones], lambda i: acciones[i][1](),
                      _("_Aplicar")).present()

    def reintentar_eliminaciones(self):
        ejecutar(lambda: self.svc.reintentar_eliminaciones(self.account_id),
                 lambda n, e: notificar(_("{n} eliminaciones pendientes resueltas.").format(n=n or 0) if not e
                                        else _("Error: {e}").format(e=e)))

    def carpeta_descargas(self):
        ruta = self.svc.carpeta_descargas()
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(ruta).get_uri(), None)
        except Exception:
            texto_en_dialogo(self, _("Carpeta de descargas"), ruta)

    def transferencias(self):
        DialogoTransferencias(self, self.account_id).present()


ESTADOS = {"pendiente": "Pendiente", "preparando": "Preparando", "calculando_hash": "Calculando hash", "subiendo": "Subiendo",
           "descargando": "Descargando", "reconstruyendo": "Reconstruyendo", "verificando": "Verificando", "pausada": "Pausada",
           "cancelando": "Cancelando", "cancelada": "Cancelada", "completada": "Completada", "fallida": "Fallida"}


class DialogoTransferencias(DialogoLista):
    """Las subidas y descargas con su progreso, que se actualiza solo. Cancelar, reintentar y quitar de la lista."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Transferencias"), _("Lista de transferencias"))
        self.account_id, self.items, self.svc = account_id, [], tgdrive_service
        self.boton(_("_Cancelar"), lambda: self._sobre(self.svc.cancelar_transferencia))
        self.boton(_("R_eintentar"), lambda: self._sobre(self.svc.reintentar_transferencia))
        self.boton(_("_Quitar de la lista"), lambda: self._sobre(self.svc.quitar_transferencia))
        self.boton(_("_Limpiar terminadas…"), self._limpiar)
        self.cerrar_boton()
        self._cargar()
        self._temporizador = GLib.timeout_add_seconds(1, self._tic)
        self.connect("close-request", self._cerrar)

    def _cerrar(self, *_a):
        if self._temporizador:
            GLib.source_remove(self._temporizador)
            self._temporizador = 0
        return False

    def _tic(self):
        self._cargar()
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _texto(t) -> str:
        op = _("Subida") if t["operacion"] == "upload" else _("Descarga")
        estado = _(ESTADOS.get(t["estado"], t["estado"]))
        if t["estado"] == "fallida" and t["error"]:
            estado = _("Fallida: {e}").format(e=t["error"][:40])
        prog = f"{t['porcentaje']}%" if t["estado"] in ("subiendo", "descargando") else ""
        return ". ".join(x for x in (t["nombre"] or "", op, estado, prog) if x)

    def _cargar(self):
        nuevos = self.svc.obtener_transferencias(self.account_id)
        p = self.lista.posicion()
        foco = self.items[p]["id"] if 0 <= p < len(self.items) else None
        if [t["id"] for t in nuevos] == [t["id"] for t in self.items] and self.items:
            self.items = nuevos     # mismo conjunto: se actualizan los textos sin mover el foco
            for i, t in enumerate(nuevos):
                if self.lista.modelo.get_string(i) != self._texto(t):
                    self.lista.modelo.splice(i, 1, [self._texto(t)])
            return
        self.items = nuevos
        self.lista.poner([self._texto(t) for t in nuevos])
        pos = next((i for i, t in enumerate(nuevos) if t["id"] == foco), 0)
        if nuevos:
            self.lista.seleccion.set_selected(pos)
        self.mostrar_estado(_("{n} transferencias").format(n=len(nuevos)) if nuevos else _("No hay transferencias."))

    def _sobre(self, fn):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            fn(self.items[p]["id"])
            self._cargar()

    def _limpiar(self):
        opciones = [(_("Solo las completadas"), ("completada",)), (_("Solo las fallidas"), ("fallida",)),
                    (_("Solo las canceladas"), ("cancelada",)), (_("Todas las terminadas"), ("completada", "fallida", "cancelada"))]

        def _hacer_(i):
            n = self.svc.limpiar_transferencias(self.account_id, opciones[i][1])
            notificar(_("{n} transferencias quitadas de la lista.").format(n=n) if n
                      else _("No había transferencias terminadas que quitar."))
            self._cargar()
        DialogoElegir(self, _("Limpiar transferencias"), _("_Qué transferencias terminadas quitar:"), [o[0] for o in opciones],
                      _hacer_, _("_Quitar")).present()


