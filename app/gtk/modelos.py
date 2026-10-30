# -*- coding: utf-8 -*-
"""Objetos GObject que envuelven los datos para los modelos de lista de GTK 4."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gio  # noqa: E402

from . import lectura
from .datos import Nodo, Fila


class NodoObj(GObject.Object):
    """Elemento del árbol. `etiqueta` cambia (contadores) sin rehacer el árbol."""
    etiqueta = GObject.Property(type=str, default="")

    def __init__(self, nodo: Nodo):
        super().__init__()
        self.nodo = nodo
        self.etiqueta = nodo.etiqueta
        self.hijos = None

    def clave(self):
        n = self.nodo
        return (n.servicio, n.account_id, n.tipo, n.category_id, n.feed_id, n.timeline)

    def sincronizar(self):
        self.etiqueta = self.nodo.etiqueta

    def anadir_hijo(self, nodo: Nodo):
        """Añade un buffer hijo en caliente (la lista de hijos y, si ya existe, su almacén)."""
        self.nodo.hijos.append(nodo)
        if self.hijos is not None:
            self.hijos.append(NodoObj(nodo))

    def quitar_hijo(self, nodo: Nodo):
        if nodo in self.nodo.hijos:
            self.nodo.hijos.remove(nodo)
        if self.hijos is not None:
            for i in range(self.hijos.get_n_items()):
                if self.hijos.get_item(i).nodo is nodo:
                    self.hijos.remove(i)
                    break

    def almacen_hijos(self):
        """Gio.ListStore con los hijos (para Gtk.TreeListModel) o None si es una hoja."""
        if not self.nodo.hijos:
            return None
        if self.hijos is None:
            self.hijos = Gio.ListStore.new(NodoObj)
            for h in self.nodo.hijos:
                self.hijos.append(NodoObj(h))
        return self.hijos


class FilaObj(GObject.Object):
    """Artículo de la lista. Los estados son propiedades para repintar la fila sin tocar la selección."""
    leido = GObject.Property(type=bool, default=False)
    favorito = GObject.Property(type=bool, default=False)
    mas_tarde = GObject.Property(type=bool, default=False)
    impulsado = GObject.Property(type=bool, default=False)
    marcador = GObject.Property(type=bool, default=False)

    def __init__(self, fila: Fila):
        super().__init__()
        self.fila = fila
        self.leido = fila.leido
        self.favorito = fila.favorito
        self.mas_tarde = fila.mas_tarde
        self.impulsado = bool(fila.extra.get("impulsado"))
        self.marcador = bool(fila.extra.get("marcador"))

    def descripcion(self) -> str:
        """Texto completo de la fila para el lector de pantalla. Se calcula una vez por estado: pintar una fila
        la pide varias veces y cada llamada de más es tiempo del hilo de la interfaz."""
        clave = (self.leido, self.favorito, self.mas_tarde, self.impulsado, self.marcador, id(self.fila))
        if getattr(self, "_desc_clave", None) != clave:
            self._desc_clave = clave
            origen = self.fila.extra.get("origen")      # en la bandeja, de qué buffer viene
            grupo = self.fila.extra.get("grupo")        # primera fila de un bloque de fechas (Agrupar por fecha)
            plantilla = lectura.leer(self)              # la plantilla de lectura del usuario, si la fila usa una
            if plantilla is not None:
                texto, con_origen = plantilla
                origen = None if con_origen else origen
            else:
                texto = self._construir_descripcion()
            self._desc = (f"{grupo}. " if grupo else "") + (f"{origen}. " if origen else "") + texto
        return self._desc

    def _construir_descripcion(self) -> str:
        from .datos import fecha_legible
        f = self.fila
        if f.servicio in ("reddit", "peertube"):
            # La lectura ya sale de las plantillas del usuario (fecha, puntos e indicadores incluidos).
            return f.titulo
        if f.servicio in ("mastodon", "bluesky", "wsocial", "telegram", "irc", "xmpp", "matrix", "x"):
            return self._descripcion_mastodon()
        partes = []
        if not self.leido:
            partes.append(_("No leído"))
        partes.append(f.titulo)
        if f.feed_titulo:
            partes.append(f.feed_titulo)
        if f.autor and f.autor != f.feed_titulo:
            partes.append(f.autor)
        fecha = fecha_legible(f.fecha)
        if fecha:
            partes.append(fecha)
        if f.audio:
            partes.append(_("con audio"))
        if self.favorito:
            partes.append(_("favorito"))
        if self.mas_tarde:
            partes.append(_("leer más tarde"))
        return ". ".join(partes)

    def _descripcion_mastodon(self) -> str:
        from .datos import fecha_legible
        f, x = self.fila, self.fila.extra
        partes = []
        if x.get("impulsado_por"):
            partes.append(_("Impulsado por {n}").format(n=x["impulsado_por"]))
        partes.append(f.titulo)
        if x.get("respuesta_a"):
            partes.append(x["respuesta_a"])
        if x.get("adjuntos"):
            partes.append(_("{n} adjuntos").format(n=x["adjuntos"]))
        if x.get("encuesta"):
            partes.append(_("con encuesta"))
        fecha = fecha_legible(f.fecha)
        if fecha:
            partes.append(fecha)
        if x.get("respuestas"):
            partes.append(_("{n} respuestas").format(n=x["respuestas"]))
        en_x = f.servicio == "x"      # X no habla de impulsos ni de favoritos
        if x.get("n_impulsos"):
            partes.append((_("{n} retweets y citas") if en_x else _("{n} impulsos")).format(n=x["n_impulsos"]))
        if x.get("n_favoritos"):
            partes.append((_("{n} me gusta") if en_x else _("{n} favoritos")).format(n=x["n_favoritos"]))
        if self.favorito:
            partes.append(_("te gusta") if en_x else _("favorito"))
        if self.impulsado:
            partes.append(_("retuiteado") if en_x else _("impulsado"))
        if self.marcador:
            partes.append(_("en marcadores"))
        return ". ".join(partes)
