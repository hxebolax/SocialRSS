# -*- coding: utf-8 -*-
"""Redacción traducida para los campos de escritura de todas las redes.

Permite escribir en tu idioma, elegir el idioma destino y, al aceptar, pegar la traducción en el
campo de texto en el que estabas (para responder a una conversación en otro idioma). Se activa con
Ctrl+Shift+T dentro de cualquier campo de redacción.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleTextCtrl

# Idiomas ofrecidos como destino (código, nombre). 'auto' no aplica como destino.
IDIOMAS = [
    ("es", _("Español")), ("en", _("Inglés")), ("fr", _("Francés")), ("pt", _("Portugués")),
    ("de", _("Alemán")), ("it", _("Italiano")), ("ca", _("Catalán")), ("gl", _("Gallego")),
    ("eu", _("Euskera")), ("nl", _("Neerlandés")), ("ru", _("Ruso")), ("ja", _("Japonés")),
    ("zh-CN", _("Chino")), ("ar", _("Árabe")), ("ko", _("Coreano")), ("tr", _("Turco")),
]


class RedactarTraduciendoDialog(wx.Dialog):
    """Escribe en tu idioma y obtén la traducción al idioma destino elegido."""

    def __init__(self, parent, destino_inicial=None):
        super().__init__(parent, title=_("Redactar traduciendo"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Redactar traduciendo"))
        self.resultado = None
        self._destino_inicial = destino_inicial or config.get("translation", "default_target", "en")
        self._init_ui()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Escribe en tu idioma lo que quieres decir:")), 0, wx.ALL, 5)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, name=_("Texto a traducir"),
                                      ayuda_texto=_("Escribe aquí; se traducirá al idioma destino y se pegará en tu mensaje."))
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Idioma destino:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice = wx.Choice(self, choices=[n for _c, n in IDIOMAS], name=_("Idioma destino de la traducción"))
        idx = next((i for i, (c, _n) in enumerate(IDIOMAS) if c == self._destino_inicial), 0)
        self.choice.SetSelection(idx)
        fila.Add(self.choice, 0, wx.ALL, 5)
        s.Add(fila, 0)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_ok = wx.Button(self, wx.ID_OK, _("&Aceptar (traducir y pegar)"))
        self.btn_ok.Bind(wx.EVT_BUTTON, self._on_aceptar)
        self.btn_ok.SetDefault()
        btns.Add(self.btn_ok, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.txt.SetFocus()

    def _on_aceptar(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            speak(_("Escribe algo para traducir."), interrupt=True)
            return
        destino = IDIOMAS[self.choice.GetSelection()][0]
        self.btn_ok.Disable()
        speak(_("Traduciendo..."), interrupt=True)
        from app.services.translation.translation_service import translation_service

        def _bg(ctx):
            return translation_service.traducir(texto, idioma_origen="auto", idioma_destino=destino)

        def _fin(res, err):
            if err or not res:
                self.btn_ok.Enable()
                notificar(_("No se pudo traducir: {0}").format(err or _("respuesta vacía")))
                return
            self.resultado = res
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


def instalar_traductor_redaccion(text_ctrl):
    """Activa Ctrl+Shift+T en un campo de texto: abre 'Redactar traduciendo' y pega la traducción
    en la posición del cursor. Llamar una vez por cada campo de redacción."""
    if text_ctrl is None or getattr(text_ctrl, "_traductor_instalado", False):
        return
    text_ctrl._traductor_instalado = True

    def _on_char_hook(event):
        if (event.GetKeyCode() == ord('T')
                and event.GetModifiers() == (wx.MOD_CONTROL | wx.MOD_SHIFT)):
            _abrir(text_ctrl)
            return
        event.Skip()

    text_ctrl.Bind(wx.EVT_CHAR_HOOK, _on_char_hook)


def _abrir(text_ctrl):
    parent = text_ctrl.GetTopLevelParent()
    dlg = RedactarTraduciendoDialog(parent)
    if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
        try:
            # Pegar en la posición del cursor (o sustituir la selección).
            ini, fin = text_ctrl.GetSelection()
            if ini != fin:
                text_ctrl.Replace(ini, fin, dlg.resultado)
            else:
                text_ctrl.WriteText(dlg.resultado)
            text_ctrl.SetFocus()
            notificar(_("Traducción pegada en el mensaje."))
        except Exception:
            pass
    dlg.Destroy()
