# -*- coding: utf-8 -*-
"""Navegación por la ESTRUCTURA del artículo en el lector limpio (mixin de CleanArticleViewer),
como en el visor universal del autor (ePubViewer): letras sobre el cuadro de texto.

    H / Mayús+H   encabezado siguiente / anterior (1 a 6: de ese nivel)
    T             tabla            L  lista        I  elemento de lista
    Q             cita             K  enlace       G  imagen
    S             separador        Z  fin de la tabla, lista o cita (Mayús+Z: su principio)
    Intro         sobre un enlace lo abre en el navegador; sobre una tabla la muestra en una
                  lista (fila a fila, con las flechas izquierda y derecha por columnas) y
                  desde ahí se puede ver como página HTML.
    Ctrl+F        buscar en el artículo; F3 o Ctrl+G siguiente, Mayús+F3 o Ctrl+Mayús+G anterior.

El cuadro es de solo lectura, así que las letras no escriben nada; se capturan en el
EVT_CHAR_HOOK del diálogo solo cuando el foco está en el cuadro de texto y sin Ctrl ni Alt.
"""
import wx

from app.core.speech import speak
from app.core import texto_estructurado as te

# letra -> (tipo de marca, nivel de encabezado, nombre en singular, en plural)
_LETRAS = {
    "H": (te.ENCABEZADO, 0, "encabezado", "encabezados"),
    "T": (te.TABLA, 0, "tabla", "tablas"),
    "L": (te.LISTA, 0, "lista", "listas"),
    "I": (te.ELEMENTO, 0, "elemento de lista", "elementos de lista"),
    "Q": (te.CITA, 0, "cita", "citas"),
    "K": (te.ENLACE, 0, "enlace", "enlaces"),
    "G": (te.IMAGEN, 0, "imagen", "imágenes"),
    "S": (te.SEPARADOR, 0, "separador", "separadores"),
}
for _n in range(1, 7):
    _LETRAS[str(_n)] = (te.ENCABEZADO, _n, f"encabezado de nivel {_n}", f"encabezados de nivel {_n}")


class _NavEstructuraMixin:
    _estructura = None
    _mapa = None
    _ultima_busqueda = ""

    # ------------------------------------------------------------------ texto y cursor
    def _poner_texto(self, texto: str, estructura=None):
        """Pone el texto en el cuadro (sin disparar EVT_TEXT) y guarda su estructura."""
        self._estructura = estructura
        self._mapa = te.MapaUtf16(texto)
        self.txt_lector.ChangeValue(texto)

    def _pos(self) -> int:
        p = self.txt_lector.GetInsertionPoint()
        return self._mapa.desde_control(p) if self._mapa else p

    def _ir_a(self, posicion: int):
        local = self._mapa.a_control(posicion) if self._mapa else posicion
        self.txt_lector.SetInsertionPoint(local)
        self.txt_lector.ShowPosition(local)

    # ------------------------------------------------------------------ teclas
    def _tecla_estructura(self, event) -> bool:
        """Atiende las teclas de navegación. True si la consumió."""
        if self.modo != "texto" or wx.Window.FindFocus() is not self.txt_lector:
            return False
        key = event.GetKeyCode()
        ctrl, alt, shift = event.ControlDown(), event.AltDown(), event.ShiftDown()
        if ctrl and not alt and key == ord("F"):
            self._buscar_en_articulo()
            return True
        if ctrl and not alt and key == ord("G"):
            self._repetir_busqueda(not shift)
            return True
        if key == wx.WXK_F3 and not ctrl and not alt:
            self._repetir_busqueda(not shift)
            return True
        if ctrl or alt or event.MetaDown():
            return False
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not shift:
            return self._activar_en_cursor()
        codigo = event.GetUnicodeKey()
        if codigo == wx.WXK_NONE or codigo < 32:
            return False
        letra = chr(codigo).upper()
        if letra == "Z":
            self._saltar_contenedor(not shift)
            return True
        if letra in _LETRAS:
            self._saltar(letra, not shift)
            return True
        return False

    def _saltar(self, letra: str, adelante: bool):
        tipo, nivel, singular, plural = _LETRAS[letra]
        est = self._estructura
        if est is None:
            speak(_("Este texto no tiene estructura para navegar."), interrupt=True)
            return
        if not est.grupo(tipo, nivel):
            speak(_("No hay {0} en el artículo.").format(_(plural)), interrupt=True)
            return
        marca = est.siguiente(tipo, self._pos(), adelante, nivel)
        if marca is None:
            texto = _("No hay más {0} hacia delante.") if adelante else _("No hay más {0} hacia atrás.")
            speak(texto.format(_(plural)), interrupt=True)
            return
        self._ir_a(marca.posicion)
        speak(te.describir(marca, est), interrupt=True)

    def _saltar_contenedor(self, al_final: bool):
        est = self._estructura
        marca = est.contenedor_en(self._pos()) if est else None
        if marca is None:
            speak(_("No estás dentro de una tabla, una lista ni una cita."), interrupt=True)
            return
        nombre = {te.TABLA: _("la tabla"), te.LISTA: _("la lista"), te.CITA: _("la cita")}[marca.tipo]
        if al_final:
            fin = marca.posicion + marca.longitud
            destino = min(fin, len(est.texto))
            self._ir_a(destino)
            speak(_("Después de {0}.").format(nombre) + " " + est.linea_en(destino), interrupt=True)
        else:
            self._ir_a(marca.posicion)
            speak(_("Principio de {0}.").format(nombre) + " " + est.linea_en(marca.posicion),
                  interrupt=True)

    def _activar_en_cursor(self) -> bool:
        est = self._estructura
        if est is None:
            return False
        pos = self._pos()
        enlace = est.en_linea(pos, (te.ENLACE,))
        tabla = est.contenedor_en(pos, (te.TABLA,))
        if tabla is not None:
            self._ver_tabla(tabla)
            return True
        if enlace is not None:
            from urllib.parse import urljoin
            from app.core.privacy import abrir_url, limpiar_url
            url = urljoin(self.link or "", enlace.referencia)
            if url.startswith(("http://", "https://")):
                abrir_url(limpiar_url(url))
                speak(_("Abriendo {0} en el navegador.").format(enlace.texto), interrupt=True)
                return True
        return False

    def _ver_tabla(self, marca):
        from .rss_tabla import TablaDialog
        dlg = TablaDialog(self, marca)
        dlg.ShowModal()
        dlg.Destroy()
        self.txt_lector.SetFocus()

    # ------------------------------------------------------------------ búsqueda
    def _buscar_en_articulo(self):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Texto que buscar en el artículo:"),
                                    _("Buscar en el artículo"), value=self._ultima_busqueda)
        dlg.txt.SelectAll()
        termino = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        self.txt_lector.SetFocus()
        if termino:
            self._ultima_busqueda = termino
            self._repetir_busqueda(True, desde_cursor=True)

    def _repetir_busqueda(self, adelante: bool, desde_cursor: bool = False):
        if not self._ultima_busqueda:
            self._buscar_en_articulo()
            return
        texto = self.txt_lector.GetValue()
        if self._mapa is None:
            self._mapa = te.MapaUtf16(texto)
        bajo, aguja = texto.lower(), self._ultima_busqueda.lower()
        pos = self._pos()
        if adelante:
            i = bajo.find(aguja, pos if desde_cursor else pos + 1)
            vuelta = i < 0
            if vuelta:
                i = bajo.find(aguja)
        else:
            i = bajo.rfind(aguja, 0, max(pos, 0))
            vuelta = i < 0
            if vuelta:
                i = bajo.rfind(aguja)
        if i < 0:
            speak(_("No se encontró «{0}».").format(self._ultima_busqueda), interrupt=True)
            return
        desde, hasta = self._mapa.a_control(i), self._mapa.a_control(i + len(aguja))
        self.txt_lector.SetSelection(desde, hasta)
        self.txt_lector.ShowPosition(desde)
        ini = texto.rfind("\n", 0, i) + 1
        fin = texto.find("\n", i)
        linea = texto[ini:len(texto) if fin < 0 else fin]
        aviso = (_("Se continúa desde el principio.") + " ") if vuelta and adelante else \
            ((_("Se continúa desde el final.") + " ") if vuelta else "")
        speak(aviso + linea, interrupt=True)
