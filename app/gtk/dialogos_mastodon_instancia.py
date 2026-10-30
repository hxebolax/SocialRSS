# -*- coding: utf-8 -*-
"""Mastodon: anuncios del servidor, enlaces en tendencia e información de la instancia (los mismos servicios que Windows)."""

import webbrowser

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.services.mastodon.mastodon_service import mastodon_service

from .dialogos import DialogoBase
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube_instancia import texto_en_dialogo


class DialogoAnuncios(DialogoLista):
    """Anuncios del servidor: leerlos, marcarlos como leídos y reaccionar con un emoji."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Anuncios del servidor"), _("Lista de anuncios"))
        self.account_id, self.items = account_id, []
        self.boton(_("Marcar como _leído"), self._leer)
        self.boton(_("_Reaccionar…"), lambda: self._reaccionar(True))
        self.boton(_("_Quitar mi reacción…"), lambda: self._reaccionar(False))
        self.boton(_("A_ctualizar"), self._cargar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        self.mostrar_estado(_("Cargando los anuncios…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los anuncios: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([(_("(sin leer) ") if not a.get("leido") else "") + " ".join((a.get("texto") or "").split())
                              + (f" — {a['fecha'][:10]}" if a.get("fecha") else "") for a in self.items])
            self.mostrar_estado(_("{n} anuncios").format(n=len(self.items)) if self.items else _("No hay anuncios"))
            self.lista.enfocar()
        mastodon_service.obtener_anuncios_async(self.account_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _leer(self):
        a = self._actual()
        if not a:
            return

        def _fin(res, err):
            notificar(_("No se pudo: {e}").format(e=err) if err else _("Anuncio marcado como leído"))
            if not err:
                self._cargar()
        mastodon_service.marcar_anuncio_leido_async(self.account_id, a["id"], callback=_fin)

    def _reaccionar(self, anadir: bool):
        a = self._actual()
        if not a:
            return
        d = DialogoBase(self, _("Reaccionar al anuncio") if anadir else _("Quitar mi reacción"), _("_Aceptar"))
        emoji = d.campo(_("_Emoji (o :atajo:):"), Gtk.Entry(activates_default=True))
        d.set_focus(emoji)

        def _ok():
            e = emoji.get_text().strip()
            if not e:
                return
            d.close()

            def _fin(res, err):
                notificar(_("No se pudo: {e}").format(e=err) if err else _("Hecho"))
            mastodon_service.reaccionar_anuncio_async(self.account_id, a["id"], e, añadir=anadir, callback=_fin)
        d.aceptar = _ok
        d.present()


class DialogoEnlacesTendencia(DialogoLista):
    """Enlaces que se comparten ahora mismo en la red. Intro abre el enlace en el navegador."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Enlaces en tendencia"), _("Lista de enlaces en tendencia"), lambda pos: self._abrir(pos))
        self.account_id, self.items = account_id, []
        self.boton(_("_Abrir en el navegador"), lambda: self._abrir(self.lista.posicion()))
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando los enlaces…"))

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar los enlaces: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{x.get('title', '')} — {x.get('url', '')}" for x in self.items])
            self.mostrar_estado(_("{n} enlaces").format(n=len(self.items)) if self.items else _("No hay enlaces en tendencia"))
            self.lista.enfocar()
        mastodon_service.cargar_trending_links_async(account_id, callback=_fin)

    def _abrir(self, pos):
        if 0 <= pos < len(self.items) and self.items[pos].get("url"):
            webbrowser.open(self.items[pos]["url"])


def info_instancia(padre, account_id: int):
    """Normas, descripción, términos, idiomas y datos de actividad de la instancia, como texto."""
    def _fin(d, err):
        if err or not d:
            notificar(_("No se pudo cargar la información de la instancia: {e}").format(e=err or ""))
            return
        lineas = []
        if d.get("software"):
            lineas.append(_("Software: {s}").format(s=d["software"]))
        if d.get("usuarios_activos_mes") is not None:
            lineas.append(_("Usuarios activos al mes: {n}").format(n=d["usuarios_activos_mes"]))
        if d.get("federadas") is not None:
            lineas.append(_("Instancias federadas conocidas: {n}").format(n=d["federadas"]))
        if d.get("streaming_ok") is not None:
            lineas.append(_("Tiempo real disponible: {s}").format(s=_("sí") if d["streaming_ok"] else _("no")))
        if d.get("idiomas"):
            lineas.append(_("Idiomas: {i}").format(i=", ".join(d["idiomas"])))
        if d.get("descripcion"):
            lineas += ["", _("Descripción:"), d["descripcion"]]
        if d.get("reglas"):
            lineas += ["", _("Normas:")] + [f"{i}. {r}" for i, r in enumerate(d["reglas"], 1)]
        if d.get("actividad"):
            lineas += ["", _("Actividad reciente:")] + [
                _("  Semana {s}: {p} publicaciones, {r} registros, {a} activos").format(
                    s=x["semana"], p=x["publicaciones"], r=x["registros"], a=x["activos"]) for x in d["actividad"]]
        if d.get("terminos"):
            lineas += ["", _("Términos del servicio:"), d["terminos"]]
        texto_en_dialogo(padre, _("Información de la instancia"), "\n".join(lineas) or _("Sin información"))
    mastodon_service.info_instancia_async(account_id, callback=_fin)
