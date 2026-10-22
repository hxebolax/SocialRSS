# -*- coding: utf-8 -*-
"""Diálogo modal de espera con barra de progreso para la carga inicial de los buffers.

Se muestra UNA sola vez, al arrancar, mientras se descargan por primera vez los buffers de las
redes que cargan al entrar (Mastodon, Bluesky y Telegram). Bloquea la ventana principal hasta que
termina (o hasta un tope de seguridad), para que el usuario no navegue por el árbol antes de tiempo
y dispare cargas repetidas. NO es cancelable: no tiene botón de cerrar y la tecla Escape no lo
cierra (el filtro global de Escape respeta el atributo `_no_cerrable`).
"""
import wx


class DialogoCargandoBuffers(wx.Dialog):
    def __init__(self, parent, total):
        # wx.CAPTION sin wx.CLOSE_BOX: barra de título con el nombre, pero sin botón de cerrar.
        super().__init__(parent, title=_("Cargando buffers"), style=wx.CAPTION,
                         name=_("Cargando buffers"))
        self._total = max(1, int(total))
        # El filtro global de teclas (Escape cierra diálogos) respeta este atributo y NO lo cierra.
        self._no_cerrable = True
        self._permitir_cierre = False

        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_(
            "Cargando los buffers de tus redes por primera vez.\n"
            "Espera un momento; en cuanto termine podrás navegar."))
        s.Add(self.lbl, 0, wx.ALL, 14)

        self.gauge = wx.Gauge(self, range=self._total, size=(380, 22),
                              name=_("Progreso de carga de buffers"))
        s.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)

        self.lbl_prog = wx.StaticText(self, label=_("{0} de {1}").format(0, self._total),
                                      name=_("Buffers cargados"))
        s.Add(self.lbl_prog, 0, wx.ALL, 14)

        self.SetSizerAndFit(s)
        self.CentreOnParent()
        # Vetar el cierre por la X o Alt+F4 hasta que terminemos nosotros.
        self.Bind(wx.EVT_CLOSE, self._on_close)
        # FOCO REAL para el lector de pantalla: este modal no tenía NINGÚN control
        # enfocable (solo textos y la barra) y el foco quedaba en el limbo; con el foco
        # indefinido, NVDA dejaba de responder a varios gestos durante toda la carga
        # (fallo reportado). La barra de progreso recibe el foco: NVDA la anuncia y
        # tiene siempre un objeto válido bajo el foco.
        wx.CallAfter(self.gauge.SetFocus)

    def _on_close(self, event):
        if self._permitir_cierre:
            event.Skip()
        else:
            event.Veto()

    def actualizar(self, hechas):
        """Avanza la barra a 'hechas' buffers cargados."""
        n = max(0, min(self._total, int(hechas)))
        try:
            self.gauge.SetValue(n)
            self.lbl_prog.SetLabel(_("{0} de {1}").format(n, self._total))
        except Exception:
            pass

    def cerrar(self):
        """Cierra el diálogo (lo llamamos nosotros al terminar o al llegar al tope de seguridad)."""
        self._permitir_cierre = True
        try:
            if self.IsModal():
                self.EndModal(wx.ID_OK)
            else:
                self.Destroy()
        except Exception:
            pass
