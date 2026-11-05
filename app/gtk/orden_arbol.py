# -*- coding: utf-8 -*-
"""Orden del árbol de navegación y su reordenación con Ctrl+Arriba / Ctrl+Abajo (mixin de VentanaPrincipal).

Se guarda donde lo guarda la versión de Windows, así que el orden elegido vale en las dos interfaces:
- servicios de la raíz: `general.service_order`;
- buffers fijos de cada red (y los de RSS): `<red>.buffer_order`, compartido por todas las cuentas de la red;
- cuentas: `accounts.position`; feeds y categorías de RSS: `rss_feeds.position` y `rss_categories.position`;
- buffers abiertos durante la sesión (hilos, usuarios...): el orden de su lista en memoria."""

import logging

from app.core.config_manager import config
from app.core.database import db

from . import datos_bluesky, datos_irc, datos_mastodon, datos_matrix, datos_reddit, datos_telegram, datos_wsocial, datos_x, datos_xmpp
from .datos import Nodo

log = logging.getLogger(__name__)

_MODULOS = {"mastodon": datos_mastodon, "bluesky": datos_bluesky, "wsocial": datos_wsocial, "telegram": datos_telegram,
            "irc": datos_irc, "xmpp": datos_xmpp, "matrix": datos_matrix, "x": datos_x, "reddit": datos_reddit}
_RSS_FIJOS = ["all", "unread", "favorites", "read_later", "today"]


def _claves_fijas(servicio: str):
    if servicio == "rss":
        return list(_RSS_FIJOS)
    modulo = _MODULOS.get(servicio)
    return [k for k, _t in getattr(modulo, "BUFFERS", [])] if modulo else []


def _orden_buffers(servicio: str):
    """Orden vigente: el guardado (que puede traer claves de la otra interfaz) y, detrás, las que falten."""
    guardado = [k for k in (config.get(servicio, "buffer_order", []) or []) if isinstance(k, str)]
    return guardado + [k for k in _claves_fijas(servicio) if k not in guardado]


def _clave_fijo(n: Nodo):
    """Clave de un buffer fijo de una red (o de RSS), o None si el nodo no lo es."""
    if n.servicio == "rss":
        return n.tipo if n.tipo in _RSS_FIJOS and n.category_id is None and n.feed_id is None else None
    if n.tipo == "timeline" and not n.cerrable and n.timeline in _claves_fijas(n.servicio):
        return n.timeline
    return None


def _reordenar_en_sitio(nodos: list, clave, orden: list):
    """Coloca los nodos con clave en los huecos que ya ocupaban, ordenados según `orden`; el resto no se mueve."""
    huecos = [i for i, n in enumerate(nodos) if clave(n) is not None]
    if len(huecos) < 2:
        return
    pos = {k: i for i, k in enumerate(orden)}
    elegidos = sorted((nodos[i] for i in huecos), key=lambda n: pos.get(clave(n), len(pos)))
    for i, n in zip(huecos, elegidos):
        nodos[i] = n


def aplicar_orden(arbol: list):
    """Ordena el árbol recién construido (servicios, buffers fijos de cada cuenta y buffers fijos de RSS)."""
    saved = [k for k in (config.get("general", "service_order", []) or []) if isinstance(k, str)]
    pos = {k: i for i, k in enumerate(saved)}
    base = {id(n): i for i, n in enumerate(arbol)}
    arbol.sort(key=lambda n: (pos.get(_clave_servicio(n), len(pos)), base[id(n)]))

    def _recorrer(nodos):
        for n in nodos:
            if n.tipo in ("cuenta", "servicio"):
                if n.servicio in _MODULOS:
                    _reordenar_en_sitio(n.hijos, _clave_fijo, _orden_buffers(n.servicio))
                _recorrer(n.hijos)
    _recorrer(arbol)
    for n in arbol:
        if n.servicio == "rss":
            _reordenar_en_sitio(n.hijos, _clave_fijo, _orden_buffers("rss"))


def _clave_servicio(n: Nodo) -> str:
    return "inbox" if n.servicio == "bandeja" else n.servicio


def _intercambiar(lista: list, visibles: list, clave, subir: bool):
    """Intercambia en `lista` la posición de `clave` con la de su vecino entre `visibles` (en el orden en que se ven).
    Devuelve la lista nueva, o None si ya está en el extremo."""
    if clave not in visibles:
        return None
    i = visibles.index(clave)
    j = i - 1 if subir else i + 1
    if not 0 <= j < len(visibles):
        return None
    lista = list(lista) + [k for k in visibles if k not in lista]
    a, b = lista.index(visibles[i]), lista.index(visibles[j])
    lista[a], lista[b] = lista[b], lista[a]
    return lista


def _reasignar_posiciones(tabla: str, ids: list):
    """Deja las filas de `tabla` con las posiciones en el orden de `ids` (reutiliza las que ya tenían si son distintas)."""
    marcas = ",".join("?" * len(ids))
    previas = sorted(r[0] or 0 for r in db.query(f"SELECT position FROM {tabla} WHERE id IN ({marcas})", tuple(ids)))
    posiciones = previas if len(set(previas)) == len(previas) else [previas[0] + i for i in range(len(ids))]
    for id_, p in zip(ids, posiciones):
        db.execute(f"UPDATE {tabla} SET position = ? WHERE id = ?", (p, id_))


class _OrdenMixin:
    def _configurar_orden_arbol(self):
        from gi.repository import Gtk       # no se importa arriba: así el módulo se puede probar sin cargar GTK 4
        ctl = Gtk.EventControllerKey()
        ctl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctl.connect("key-pressed", self._on_tecla_arbol_orden)
        self.vista_arbol.add_controller(ctl)

    def _on_tecla_arbol_orden(self, ctl, keyval, keycode, estado):
        from gi.repository import Gdk
        modificadores = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK
                         | Gdk.ModifierType.SUPER_MASK)
        if keyval not in (Gdk.KEY_Up, Gdk.KEY_Down) or (estado & modificadores) != Gdk.ModifierType.CONTROL_MASK:
            return False
        self.mover_nodo_arbol(keyval == Gdk.KEY_Up)
        return True

    # ------------------------------------------------------------------ hermanos
    def _hermanos(self, nodo: Nodo):
        """La lista (viva) de hermanos que contiene al nodo, o None."""
        def _buscar(lista):
            if any(n is nodo for n in lista):
                return lista
            for n in lista:
                r = _buscar(n.hijos)
                if r is not None:
                    return r
            return None
        return _buscar(self.arbol)

    # ------------------------------------------------------------------ mover
    def mover_nodo_arbol(self, subir: bool):
        n = self.nodo_actual
        if n is None:
            return
        hermanos = self._hermanos(n)
        movido = False
        if hermanos is not None:
            try:
                movido = self._mover(n, hermanos, subir)
            except Exception:
                log.exception("No se pudo mover el elemento del árbol")
        if not movido:
            self.avisar(_("No se puede mover más arriba.") if subir else _("No se puede mover más abajo."))
            return
        previo = n
        self.cargar_arbol(seleccionar=self._clave_de(previo))
        self.avisar(_("Movido."), interrumpir=True)

    @staticmethod
    def _clave_de(n: Nodo):
        return (n.servicio, n.account_id, n.tipo, n.category_id, n.feed_id, n.timeline)

    def _mover(self, n: Nodo, hermanos: list, subir: bool) -> bool:
        # servicios de la raíz
        if hermanos is self.arbol:
            claves = [_clave_servicio(h) for h in hermanos]
            saved = [k for k in (config.get("general", "service_order", []) or []) if isinstance(k, str)]
            nueva = _intercambiar(saved, claves, _clave_servicio(n), subir)
            if nueva is None:
                return False
            config.set("general", "service_order", nueva)
            return True
        # buffers fijos de una red o de RSS
        clave = _clave_fijo(n)
        if clave is not None:
            visibles = [_clave_fijo(h) for h in hermanos if _clave_fijo(h) is not None]
            nueva = _intercambiar(_orden_buffers(n.servicio), visibles, clave, subir)
            if nueva is None:
                return False
            config.set(n.servicio, "buffer_order", nueva)
            return True
        # cuentas
        if n.tipo == "cuenta" and n.account_id is not None and n.servicio != "rss" and not n.timeline:
            cuentas = [h for h in hermanos if h.tipo == "cuenta" and h.account_id is not None and not h.timeline]
            return self._mover_db(n, cuentas, "accounts", lambda h: h.account_id, subir)
        # feeds y categorías de RSS
        if n.servicio == "rss" and n.tipo == "feed":
            return self._mover_db(n, [h for h in hermanos if h.tipo == "feed"], "rss_feeds", lambda h: h.feed_id, subir,
                                  misma_marca=self._pinned)
        if n.servicio == "rss" and n.tipo == "category":
            return self._mover_db(n, [h for h in hermanos if h.tipo == "category"], "rss_categories",
                                  lambda h: h.category_id, subir)
        # listas, búsquedas, cronologías y demás buffers guardados de X y de Reddit
        if n.servicio in ("x", "reddit") and n.timeline:
            movido = self._mover_x(n, hermanos, subir) if n.servicio == "x" else self._mover_reddit(n, subir)
            if movido is not None:
                return movido
        # buffers abiertos durante la sesión
        if n.cerrable:
            return self._mover_extra(n, subir)
        return False

    # ------------------------------------------------------------------ X y Reddit
    @staticmethod
    def _id_x(h: Nodo, tabla: str):
        """Id de la fila de X que corresponde a un nodo del árbol (las listas la llevan en la clave; las demás se buscan)."""
        tl = h.timeline
        if tabla == "x_lists":
            return f"{h.account_id}:{tl[5:]}"
        if tabla == "x_searches":
            f = db.query_one("SELECT id FROM x_searches WHERE account_id = ? AND query = ?", (h.account_id, tl[7:]))
        else:
            uid, _s, modo = tl[5:].partition(":")
            f = db.query_one("SELECT id FROM x_user_timelines WHERE account_id = ? AND user_id = ? "
                             "AND COALESCE(modo, 'tweets') = ?", (h.account_id, uid, modo or "tweets"))
        return f[0] if f else None

    def _mover_x(self, n: Nodo, hermanos: list, subir: bool):
        """True/False si el nodo es de un grupo de X que se reordena; None si no lo es."""
        tl = n.timeline
        tablas = (("list:", "x_lists"), ("search:", "x_searches"), ("user:", "x_user_timelines"))
        for prefijo, tabla in tablas:
            if tl.startswith(prefijo):
                grupo = [h for h in hermanos if h.timeline.startswith(prefijo)]
                if any(self._id_x(h, tabla) is None for h in grupo):
                    return False
                return self._mover_db(n, grupo, tabla, lambda h, t=tabla: self._id_x(h, t), subir)
        for prefijo, clave in (("thread:", "thread_buffers"), ("community:", "community_buffers"), ("dm:", "dm_buffers")):
            if tl.startswith(prefijo):
                guardadas = list(config.get("x", clave, []) or [])
                visibles = [(i, str(c.get("id"))) for i, c in enumerate(guardadas)
                            if isinstance(c, dict) and c.get("account_id") in (None, n.account_id)]
                return self._mover_en_config("x", clave, guardadas, visibles, tl[len(prefijo):], subir)
        return None

    def _mover_reddit(self, n: Nodo, subir: bool):
        tl = n.timeline
        if tl.startswith("sub:") and tl.count(":") == 1:
            clave, valor = "saved_subreddits", tl[4:]
            guardadas = list(config.get("reddit", clave, []) or [])
            visibles = [(i, s.get("name") if isinstance(s, dict) else str(s)) for i, s in enumerate(guardadas)]
        elif tl.startswith("search:"):
            clave, valor = "saved_searches", tl[7:]
            guardadas = list(config.get("reddit", clave, []) or [])
            visibles = list(enumerate(guardadas))
        else:
            clave, valor = "gtk_buffers", tl
            guardadas = list(config.get("reddit", clave, []) or [])
            visibles = [(i, c.get("timeline")) for i, c in enumerate(guardadas)
                        if isinstance(c, dict) and c.get("account_id") in (None, n.account_id)]
            if valor not in [v for _i, v in visibles]:
                return None
        return self._mover_en_config("reddit", clave, guardadas, visibles, valor, subir)

    @staticmethod
    def _mover_en_config(servicio: str, clave: str, guardadas: list, visibles: list, valor, subir: bool) -> bool:
        """Intercambia en una lista guardada la entrada `valor` con la vecina de su grupo (`visibles`: (posición, valor))."""
        pos = next((k for k, (_i, v) in enumerate(visibles) if v == valor), None)
        if pos is None:
            return False
        otro = pos - 1 if subir else pos + 1
        if not 0 <= otro < len(visibles):
            return False
        a, b = visibles[pos][0], visibles[otro][0]
        guardadas[a], guardadas[b] = guardadas[b], guardadas[a]
        config.set(servicio, clave, guardadas)
        return True

    @staticmethod
    def _pinned(h: Nodo) -> int:
        fila = db.query_one("SELECT COALESCE(pinned, 0) FROM rss_feeds WHERE id = ?", (h.feed_id,))
        return fila[0] if fila else 0

    def _mover_db(self, n: Nodo, grupo: list, tabla: str, ident, subir: bool, misma_marca=None) -> bool:
        i = next((k for k, h in enumerate(grupo) if h is n), None)
        if i is None:
            return False
        j = i - 1 if subir else i + 1
        if not 0 <= j < len(grupo):
            return False
        if misma_marca is not None and misma_marca(grupo[i]) != misma_marca(grupo[j]):
            return False        # los feeds fijados siempre van arriba
        ids = [ident(h) for h in grupo]
        # Con posiciones repetidas (instalaciones antiguas) el orden visible lo decide el título: se fija tal como se ve.
        _reasignar_posiciones(tabla, ids)
        ids[i], ids[j] = ids[j], ids[i]
        _reasignar_posiciones(tabla, ids)
        return True

    def _mover_extra(self, n: Nodo, subir: bool) -> bool:
        from . import buffers_guardados
        if buffers_guardados.guarda(n.servicio, n.timeline):
            return buffers_guardados.mover(n.servicio, n.account_id, n.timeline, subir)
        modulo = _MODULOS.get(n.servicio)
        lista = getattr(modulo, "EXTRA", {}).get(n.account_id) if modulo else None
        if not lista or n not in lista:
            return False
        i = lista.index(n)
        j = i - 1 if subir else i + 1
        if not 0 <= j < len(lista):
            return False
        lista[i], lista[j] = lista[j], lista[i]
        return True
