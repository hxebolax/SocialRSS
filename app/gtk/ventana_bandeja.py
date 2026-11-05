# -*- coding: utf-8 -*-
"""Bandeja de entrada unificada en la ventana principal (mixin de VentanaPrincipal).

Un buffer que mezcla por fecha las publicaciones de varios buffers de varias redes. La configuración es la misma que la de
Windows (`inbox.enabled` y `inbox.fuentes`, una lista de {red, account_id, buffer}). Cada fila conserva su identificador
original y anota de dónde viene; al actuar sobre ella (responder, me gusta, abrir la conversación...) la ventana se coloca un
instante en el buffer de origen, de modo que cada acción funciona igual que allí."""

import dataclasses
import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from app.core.bandeja import parse_fecha
from app.core.config_manager import config
from app.core.database import db

from . import datos, datos_bluesky, datos_mastodon, datos_peertube, datos_reddit, datos_wsocial, datos_x
from .datos import Nodo
from .dialogos_rss import DialogoCasillas

log = logging.getLogger(__name__)

SERVICIO = "bandeja"
SONDEO_SEGUNDOS = 45
TOPE_FILAS = 500
# Redes de publicaciones que pueden alimentar la bandeja (los chats tienen otro modelo de lista).
_MODULOS = {"mastodon": datos_mastodon, "bluesky": datos_bluesky, "wsocial": datos_wsocial, "x": datos_x,
            "reddit": datos_reddit, "peertube": datos_peertube}
_RSS_BUFFERS = ("all", "unread", "today", "favorites", "read_later")
# Acciones que son de la propia bandeja o de la ventana, no de la publicación enfocada.
_SIN_CONTEXTO = ("actualizar", "buscar", "cerrar-buffer", "ir-arbol", "ir-lista", "ir-lectura")


def _clave(f: dict):
    return (f["red"], f.get("account_id"), f["buffer"])


def _clave_conservar(obj):
    return (obj.fila.extra.get("clave_lista") or obj.fila.id) if obj else None


class _BandejaMixin:
    # ------------------------------------------------------------------ configuración
    def bandeja_habilitada(self) -> bool:
        return bool(config.get("inbox", "enabled", False))

    def bandeja_fuentes(self) -> list:
        fuentes = config.get("inbox", "fuentes", []) or []
        return [f for f in fuentes if isinstance(f, dict) and f.get("red") and f.get("buffer")]

    def _bandeja_guardar(self, fuentes: list):
        config.set("inbox", "fuentes", fuentes)

    def bandeja_nodo_raiz(self) -> Nodo:
        return Nodo(_("Bandeja de entrada"), "bandeja", servicio=SERVICIO)

    # ------------------------------------------------------------------ nodos de origen
    @staticmethod
    def _bandeja_buffer_de(n: Nodo) -> str:
        return n.tipo if n.servicio == "rss" else n.timeline

    def _bandeja_candidatos(self):
        """Todos los buffers de publicaciones que se pueden añadir: [(fuente, etiqueta, nodo)]."""
        resultado = []

        def _recorrer(nodos, cuenta=""):
            for n in nodos:
                if n.servicio in _MODULOS and n.tipo == "timeline" and not n.cerrable \
                        and not n.timeline.startswith(("dm:", "thread:")) and n.timeline not in ("dms",):
                    fuente = {"red": n.servicio, "account_id": n.account_id, "buffer": n.timeline}
                    resultado.append((fuente, f"{self._bandeja_red(n.servicio)} · {cuenta} · {n.titulo}", n))
                _recorrer(n.hijos, n.titulo if n.tipo == "cuenta" else cuenta)
        _recorrer([n for n in self.arbol if n.servicio in _MODULOS])
        rss = next((n for n in self.arbol if n.servicio == "rss"), None)
        if rss is not None:
            for h in rss.hijos:
                if h.tipo in _RSS_BUFFERS:
                    resultado.append(({"red": "rss", "account_id": self.account_id, "buffer": h.tipo},
                                      f"{_('RSS y podcasts')} · {h.titulo}", h))
        return resultado

    @staticmethod
    def _bandeja_red(servicio: str) -> str:
        from app.core.plantillas import nombre_red
        return nombre_red(servicio)

    def _bandeja_nodo(self, fuente: dict):
        """El nodo del árbol al que corresponde una fuente (o uno equivalente si el buffer ya no está)."""
        red, acc, buf = fuente["red"], fuente.get("account_id"), fuente["buffer"]
        if red == "x" and buf == "mentions_tl":      # clave histórica de Windows
            buf = "mentions"
        if red == "rss":
            rss = next((n for n in self.arbol if n.servicio == "rss"), None)
            for h in (rss.hijos if rss else []):
                if h.tipo == buf:
                    return h
            return Nodo(buf, buf, servicio="rss")
        pila = [n for n in self.arbol if n.servicio == red]
        while pila:
            n = pila.pop(0)
            if n.account_id == acc and n.timeline == buf and n.tipo == "timeline":
                return n
            pila.extend(n.hijos)
        return Nodo(buf, "timeline", servicio=red, account_id=acc, timeline=buf)

    def _bandeja_etiqueta(self, fuente: dict) -> str:
        for f, et, _n in self._bandeja_candidatos():
            if _clave(f) == _clave(fuente):
                return et
        nombre = db.query_one("SELECT name FROM accounts WHERE id = ?", (fuente.get("account_id"),))
        return f"{self._bandeja_red(fuente['red'])} · {nombre[0] if nombre else fuente.get('account_id')} · {fuente['buffer']}"

    # ------------------------------------------------------------------ lista
    def bandeja_filas(self):
        """Hilo lector: filas de todas las fuentes mezcladas por fecha, con su origen anotado."""
        from datetime import datetime, timedelta
        etiquetas = {_clave(f): et for f, et, _n in self._bandeja_candidatos()}
        vistas, todas = set(), []
        for fuente in self.bandeja_fuentes():
            red, acc = fuente["red"], fuente.get("account_id")
            nodo = self._bandeja_nodo(fuente)
            try:
                filas = _MODULOS[red].listar(acc, nodo.timeline) if red in _MODULOS else datos.listar(acc, nodo)
            except Exception:
                log.exception("Bandeja: no se pudo leer %s", fuente)
                continue
            origen = etiquetas.get(_clave(fuente)) or f"{self._bandeja_red(red)} · {nodo.titulo}"
            for f in filas:
                clave_fila = (f.servicio, acc, f.id)
                if clave_fila in vistas:
                    continue
                vistas.add(clave_fila)
                extra = dict(f.extra)
                extra["origen"] = origen
                extra["bandeja"] = {"red": red, "account_id": acc, "buffer": fuente["buffer"]}
                extra["clave_lista"] = f"{red}:{acc}:{f.id}"
                todas.append((parse_fecha(f.fecha), dataclasses.replace(f, extra=extra)))
        limite = datetime.now() + timedelta(minutes=2)
        todas = [t for t in todas if t[0] <= limite and (t[1].titulo or t[1].autor)]
        todas.sort(key=lambda t: t[0], reverse=bool(config.get("general", "newest_first", True)))
        return [f for _d, f in todas[:TOPE_FILAS]]

    def _bandeja_activa(self) -> bool:
        return self.nodo_actual is not None and self.nodo_actual.servicio == SERVICIO

    def _bandeja_sondeo(self):
        """Recarga la lista desde la caché mientras la bandeja está a la vista (lo que llegue por tiempo real o por los
        sondeos de cada red aparece sin hacer nada)."""
        if self._bandeja_activa() and not self.busqueda and self.is_active():
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=_clave_conservar(obj), incremental=True)
        return GLib.SOURCE_CONTINUE

    def bandeja_vacia(self) -> str:
        return (_("La bandeja no tiene buffers. Usa Ver, Añadir buffers a la bandeja para elegirlos.")
                if not self.bandeja_fuentes() else _("No hay elementos."))

    # ------------------------------------------------------------------ actuar sobre una fila
    def _con_contexto_bandeja(self, nombre: str, fn):
        """Envuelve una acción de la ventana: con la bandeja a la vista y una fila enfocada, se ejecuta como si el buffer de
        origen de esa fila fuese el actual."""
        if nombre in _SIN_CONTEXTO or nombre.startswith("bandeja-"):
            return fn

        def _f():
            actual = self.nodo_actual
            obj = self._fila_actual() if actual is not None and actual.servicio == SERVICIO else None
            origen = obj.fila.extra.get("bandeja") if obj else None
            if not origen:
                return fn()
            self.nodo_actual = self._bandeja_nodo(origen)
            try:
                return fn()
            finally:
                self.nodo_actual = actual
        return _f

    # ------------------------------------------------------------------ actualizar
    def bandeja_actualizar(self):
        fuentes = self.bandeja_fuentes()
        if not fuentes:
            self.avisar(self.bandeja_vacia())
            return
        self.avisar(_("Actualizando la bandeja…"))
        hecho_rss = False
        for fuente in fuentes:
            nodo = self._bandeja_nodo(fuente)
            try:
                if fuente["red"] == "rss":
                    if not hecho_rss:
                        hecho_rss = True
                        from app.services.rss.rss_service import rss_service
                        rss_service.actualizar_todos_async(self.account_id, callback=lambda *_a: None)
                elif fuente["red"] == "mastodon":
                    self._actualizar_mastodon(nodo, silencioso=True)
                else:
                    getattr(self, {"bluesky": "bsky_actualizar_nodo", "wsocial": "ws_actualizar_nodo",
                                   "x": "tw_actualizar_nodo", "reddit": "rd_actualizar_nodo",
                                   "peertube": "pt_actualizar_nodo"}[fuente["red"]])(nodo, silencioso=True)
            except Exception:
                log.exception("Bandeja: no se pudo actualizar %s", fuente)
        for seg in (4, 12):
            GLib.timeout_add_seconds(seg, self._bandeja_recargar_tras_red)

    def _bandeja_recargar_tras_red(self):
        if self._bandeja_activa():
            obj = self._fila_actual()
            self.cargar_lista(conservar_id=_clave_conservar(obj), incremental=True)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ fuentes
    def bandeja_anadir(self):
        ya = {_clave(f) for f in self.bandeja_fuentes()}
        nuevos = [(f, et) for f, et, _n in self._bandeja_candidatos() if _clave(f) not in ya]
        if not nuevos:
            self.avisar(_("No hay más buffers que añadir."))
            return

        def _hecho(indices):
            if not indices:
                return
            self._bandeja_guardar(self.bandeja_fuentes() + [nuevos[i][0] for i in indices])
            self._bandeja_cambiada(_("{n} buffers añadidos a la bandeja.").format(n=len(indices)))
        DialogoCasillas(self, _("Añadir buffers a la bandeja"),
                        _("Marca los buffers que quieres añadir a la bandeja de entrada:"),
                        [et for _f, et in nuevos], set(), _hecho, _("_Añadir")).present()

    def bandeja_quitar(self):
        actuales = self.bandeja_fuentes()
        if not actuales:
            self.avisar(self.bandeja_vacia())
            return

        def _hecho(indices):
            if not indices:
                return
            quitar = set(indices)
            self._bandeja_guardar([f for i, f in enumerate(actuales) if i not in quitar])
            self._bandeja_cambiada(_("{n} buffers quitados de la bandeja.").format(n=len(indices)))
        DialogoCasillas(self, _("Quitar buffers de la bandeja"),
                        _("Marca los buffers que quieres quitar de la bandeja de entrada:"),
                        [self._bandeja_etiqueta(f) for f in actuales], set(), _hecho, _("_Quitar")).present()

    def _bandeja_cambiada(self, aviso: str):
        self.avisar(aviso)
        if self._bandeja_activa():
            self.cargar_lista()

    def _bandeja_fuente_del_nodo(self, n):
        if n is None or n.servicio not in _MODULOS and n.servicio != "rss":
            return None
        if n.servicio == "rss":
            return {"red": "rss", "account_id": self.account_id, "buffer": n.tipo} if n.tipo in _RSS_BUFFERS else None
        if n.tipo != "timeline" or n.cerrable or n.timeline.startswith(("dm:", "thread:")) or n.timeline == "dms":
            return None
        return {"red": n.servicio, "account_id": n.account_id, "buffer": n.timeline}

    def bandeja_anadir_nodo(self):
        fuente = self._bandeja_fuente_del_nodo(self.nodo_actual)
        if fuente is None:
            self.avisar(_("Este buffer no se puede añadir a la bandeja."))
        elif any(_clave(f) == _clave(fuente) for f in self.bandeja_fuentes()):
            self.avisar(_("Ya está en la bandeja."))
        else:
            self._bandeja_guardar(self.bandeja_fuentes() + [fuente])
            self.avisar(_("{t} añadido a la bandeja.").format(t=self.nodo_actual.titulo))

    def bandeja_quitar_nodo(self):
        fuente = self._bandeja_fuente_del_nodo(self.nodo_actual)
        actuales = self.bandeja_fuentes()
        restantes = [f for f in actuales if fuente is None or _clave(f) != _clave(fuente)]
        if fuente is None or len(restantes) == len(actuales):
            self.avisar(_("Este buffer no está en la bandeja."))
        else:
            self._bandeja_guardar(restantes)
            self.avisar(_("{t} quitado de la bandeja.").format(t=self.nodo_actual.titulo))

    def bandeja_alternar(self):
        activa = not self.bandeja_habilitada()
        config.set("inbox", "enabled", activa)
        self.cargar_arbol()
        self.avisar(_("Bandeja de entrada mostrada.") if activa else _("Bandeja de entrada oculta."))

    def bandeja_ir_al_origen(self):
        obj = self._fila_actual() if self._bandeja_activa() else None
        origen = obj.fila.extra.get("bandeja") if obj else None
        if not origen:
            self.avisar(_("Elige una publicación de la bandeja."))
            return
        self._id_pendiente = obj.fila.id
        self.seleccionar_nodo(self._bandeja_nodo(origen))

    # ------------------------------------------------------------------ menú y acciones
    def menu_bandeja_nodo(self, n):
        """Secciones del menú del árbol para la bandeja y para los buffers que se pueden añadir a ella."""
        if not self.bandeja_habilitada():
            return []
        if n.servicio == SERVICIO:
            return [[(_("Actualizar la bandeja"), "win.actualizar")],
                    [(_("Añadir buffers…"), "win.bandeja-anadir"), (_("Quitar buffers…"), "win.bandeja-quitar")]]
        fuente = self._bandeja_fuente_del_nodo(n)
        if fuente is None:
            return []
        dentro = any(_clave(f) == _clave(fuente) for f in self.bandeja_fuentes())
        return [[(_("Quitar de la bandeja"), "win.bandeja-quitar-nodo") if dentro
                 else (_("Añadir a la bandeja"), "win.bandeja-anadir-nodo")]]

    def _registrar_acciones_bandeja(self, accion):
        accion("bandeja-alternar", self.bandeja_alternar)
        accion("bandeja-anadir", self.bandeja_anadir)
        accion("bandeja-quitar", self.bandeja_quitar)
        accion("bandeja-anadir-nodo", self.bandeja_anadir_nodo)
        accion("bandeja-quitar-nodo", self.bandeja_quitar_nodo)
        accion("bandeja-origen", self.bandeja_ir_al_origen, ["<Ctrl><Alt>z"])
        GLib.timeout_add_seconds(SONDEO_SEGUNDOS, self._bandeja_sondeo)
