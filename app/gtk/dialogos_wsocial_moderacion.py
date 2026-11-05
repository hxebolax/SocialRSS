# -*- coding: utf-8 -*-
"""W Social: palabras silenciadas, preferencias de contenido, servicios de etiquetado, borradores del servidor y la salida
pendiente (la de Bluesky comparte pantalla). Los mismos servicios que usa la versión de Windows."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar
from app.services.wsocial.wsocial_service import wsocial_service

from .datos_wsocial import adaptar
from .dialogos import DialogoBase, DialogoTexto, confirmar_async
from .dialogos_mastodon import DialogoLista
from .dialogos_reddit import _marco, _texto_de, _vista_texto

ETIQUETAS = [("porn", "Pornografía"), ("sexual", "Contenido sexual"), ("nudity", "Desnudos"),
             ("graphic-media", "Violencia gráfica"), ("spam", "Spam"), ("impersonation", "Suplantación")]
OPCIONES = [("ignore", "Mostrar sin más"), ("warn", "Avisar antes"), ("hide", "Ocultar del todo")]


class DialogoPalabrasWSocial(DialogoLista):
    """Palabras silenciadas: se ocultan las publicaciones que las contengan."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Palabras silenciadas de W Social"), _("Lista de palabras silenciadas"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Añadir palabra…"), self._anadir)
        self.boton(_("_Quitar palabra"), self._quitar)
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = (res or {}).get("palabras", [])
            lineas = []
            for p in self.items:
                donde = ", ".join(_("texto") if a == "content" else _("etiquetas") for a in p.get("ambitos", []))
                quien = _("Solo cuentas que no sigo") if p.get("solo_desconocidos") else _("Cualquiera")
                lineas.append(f"{p.get('valor', '')}. {donde}. {quien}. " + (p.get("caduca") or _("Nunca")))
            self.lista.poner(lineas)
            self.mostrar_estado(_("{n} palabras").format(n=len(self.items)) if self.items else _("No hay palabras silenciadas"))
            self.lista.enfocar()
        wsocial_service.obtener_preferencias_async(self.account_id, callback=adaptar(_fin))

    def _guardar(self, palabras: list, ok: str):
        wsocial_service.fijar_palabras_silenciadas_async(self.account_id, palabras, callback=adaptar(lambda r, e: (
            notificar(_("No se pudo guardar: {e}").format(e=e)) if e else (notificar(ok), self._cargar()))))

    def _anadir(self):
        d = DialogoBase(self, _("Silenciar una palabra"), _("_Silenciar"))
        valor = d.campo(_("_Palabra o frase:"), Gtk.Entry(activates_default=True, width_chars=36))
        donde = d.campo(_("_Dónde silenciarla:"), Gtk.DropDown.new_from_strings(
            [_("En el texto y en las etiquetas"), _("Solo en las etiquetas")]))
        solo = d.campo(_("Aplicarla solo a cuentas que _no sigues:"), Gtk.Switch(halign=Gtk.Align.START))
        d.set_focus(valor)

        def _ok():
            texto = valor.get_text().strip()
            if not texto:
                return
            nueva = {"valor": texto, "ambitos": ["content", "tag"] if donde.get_selected() == 0 else ["tag"],
                     "solo_desconocidos": solo.get_active(), "caduca": ""}
            d.close()
            self._guardar(list(self.items) + [nueva], _("Palabra silenciada."))
        d.aceptar = _ok
        d.present()

    def _quitar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            self._guardar([x for i, x in enumerate(self.items) if i != p], _("Palabra quitada."))


class DialogoContenidoWSocial(DialogoBase):
    """Contenido para adultos, qué hacer con cada tipo de contenido marcado e idiomas."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Preferencias de contenido de W Social"), _("_Guardar"))
        self.account_id = account_id
        self.adulto = self.campo(_("Permitir contenido para _adultos:"), Gtk.Switch(halign=Gtk.Align.START))
        self.combos = {}
        for clave, nombre in ETIQUETAS:
            self.combos[clave] = self.campo(_("{n}:").format(n=_(nombre)), Gtk.DropDown.new_from_strings([_(n) for _c, n in OPCIONES]))
            self.combos[clave].set_selected(1)
        self.idiomas = self.campo(_("_Idiomas que quieres ver (códigos separados por comas):"), Gtk.Entry(activates_default=True))
        self.boton_aceptar.set_sensitive(False)
        self.set_focus(self.adulto)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudieron leer las preferencias: {e}").format(e=err or ""))
                return
            self.adulto.set_active(bool(res.get("adulto")))
            for clave, combo in self.combos.items():
                valor = (res.get("etiquetas") or {}).get(clave, "warn")
                combo.set_selected(next((i for i, (v, _n) in enumerate(OPCIONES) if v == valor), 1))
            self.idiomas.set_text(", ".join(res.get("idiomas") or []))
            self.boton_aceptar.set_sensitive(True)
        wsocial_service.obtener_preferencias_async(account_id, callback=adaptar(_fin))

    def aceptar(self):
        wsocial_service.fijar_contenido_adulto_async(self.account_id, self.adulto.get_active())
        etiquetas = {}
        for clave, combo in self.combos.items():
            valor = OPCIONES[combo.get_selected()][0]
            etiquetas[clave] = valor
            wsocial_service.fijar_visibilidad_etiqueta_async(self.account_id, clave, valor)
        config.set("wsocial", "prefs_etiquetas", etiquetas)
        idiomas = [i.strip() for i in self.idiomas.get_text().replace(";", ",").split(",") if i.strip()]
        if idiomas:
            wsocial_service.fijar_idiomas_async(self.account_id, idiomas)
        notificar(_("Preferencias de contenido guardadas."))
        self.close()


class DialogoEtiquetadoresWSocial(DialogoLista):
    """Servicios de etiquetado a los que estás suscrito."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Servicios de etiquetado de W Social"), _("Lista de servicios de etiquetado"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Suscribirse o cancelar"), self._alternar)
        self.boton(_("_Añadir por usuario…"), self._anadir)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{x.get('nombre') or x.get('handle')} (@{x.get('handle')}). "
                              + (_("Suscrito") if x.get("suscrito") else _("No suscrito")) + f". {x.get('descripcion', '')}"
                              for x in self.items])
            self.mostrar_estado(_("{n} servicios").format(n=len(self.items)) if self.items
                                else _("No estás suscrito a ningún servicio de etiquetado."))
            self.lista.enfocar()
        wsocial_service.labelers_async(self.account_id, callback=adaptar(_fin))

    def _alternar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            x = self.items[p]
            nuevo = not x.get("suscrito")
            wsocial_service.suscribir_labeler(self.account_id, x.get("did", ""), nuevo)
            notificar(_("Suscrito.") if nuevo else _("Suscripción cancelada."))
            self._cargar()

    def _anadir(self):
        def _con(actor):
            def _fin(res, err):
                if err or not (res or {}).get("did"):
                    notificar(_("No se encontró ese servicio."))
                    return
                wsocial_service.suscribir_labeler(self.account_id, res["did"], True)
                notificar(_("Servicio añadido."))
                self._cargar()
            wsocial_service.obtener_perfil_async(self.account_id, actor, callback=adaptar(_fin))
        DialogoTexto(self, _("Añadir un servicio"), _("_Usuario o identificador del servicio de etiquetado:"), _con,
                     aceptar=_("_Añadir")).present()


class DialogoBorradoresWSocial(DialogoLista):
    """Borradores guardados en el servidor (los mismos en todos tus dispositivos)."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Borradores de W Social"), _("Lista de borradores"), lambda pos: self._editar())
        self.account_id, self.items = account_id, []
        self.boton(_("_Nuevo…"), lambda: self._form(None))
        self.boton(_("_Editar…"), self._editar)
        self.boton(_("_Borrar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los borradores…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los borradores: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([" ".join((d.get("texto") or _("(sin texto)")).split())[:160] + f" — {(d.get('updated_at') or '')[:16]}"
                              for d in self.items])
            self.mostrar_estado(_("{n} borradores").format(n=len(self.items)) if self.items else _("No hay borradores"))
            self.lista.enfocar()
        wsocial_service.listar_borradores_async(self.account_id, callback=adaptar(_fin))

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _editar(self):
        d = self._actual()
        if d:
            self._form(d)

    def _form(self, draft):
        d = DialogoBase(self, _("Editar el borrador") if draft else _("Nuevo borrador"), _("_Guardar"))
        tv = _vista_texto()
        tv.get_buffer().set_text((draft or {}).get("texto", ""))
        lbl = Gtk.Label(label=_("_Texto:"), use_underline=True, xalign=0, yalign=0)
        lbl.set_mnemonic_widget(tv)
        d.rejilla.attach(lbl, 0, 0, 1, 1)
        d.rejilla.attach(_marco(tv, 140), 1, 0, 1, 1)
        d.set_focus(tv)

        def _ok():
            texto = _texto_de(tv).strip()
            if not texto:
                return
            d.close()
            cb = adaptar(lambda r, e: notificar(_("No se pudo guardar: {e}").format(e=e)) if e else (
                notificar(_("Borrador guardado.")), self._cargar()))
            if draft:
                wsocial_service.actualizar_borrador_async(self.account_id, draft["draft_id"], texto, callback=cb)
            else:
                wsocial_service.crear_borrador_async(self.account_id, texto, callback=cb)
        d.aceptar = _ok
        d.present()

    def _borrar(self):
        d = self._actual()
        if d:
            confirmar_async(self, _("¿Borrar este borrador?"),
                            lambda: wsocial_service.borrar_borrador_async(self.account_id, d["draft_id"], callback=adaptar(
                                lambda r, e: notificar(_("No se pudo borrar: {e}").format(e=e)) if e else (
                                    notificar(_("Borrador borrado.")), self._cargar()))), etiqueta_si=_("Borrar"))
