# -*- coding: utf-8 -*-
"""Visor de código QR accesible y reutilizable.

Sirve para vincular por QR (p. ej. la pasarela de WhatsApp) además del método por número/código: se
pasa el texto o enlace que da el puente y se muestra como un QR grande en pantalla para escanearlo con
el móvil, junto con el texto en claro (para leerlo con el lector, copiarlo o guardarlo).
"""
import wx

from app.core.speech import speak, notificar

try:
    from app.core.i18n import _
except Exception:  # pragma: no cover
    def _(s):
        return s

import logging
log = logging.getLogger(__name__)


class QRDialog(wx.Dialog):
    """Muestra un texto/enlace como código QR (imagen grande) + el texto en claro."""

    def __init__(self, parent, texto, titulo=None, ayuda=None):
        super().__init__(parent, title=titulo or _("Código QR"),
                         size=(460, 620), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Código QR para escanear"))
        self.texto = texto or ""
        s = wx.BoxSizer(wx.VERTICAL)
        if ayuda:
            s.Add(wx.StaticText(self, label=ayuda), 0, wx.ALL, 8)
        self.lbl_estado = wx.StaticText(self, label=_("Apunta la cámara del móvil a este código para "
                                                      "escanearlo."))
        s.Add(self.lbl_estado, 0, wx.ALL, 6)
        self.bmp = wx.StaticBitmap(self)
        s.Add(self.bmp, 0, wx.ALIGN_CENTER | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Texto del código (por si prefieres copiarlo o leerlo):")),
              0, wx.ALL, 6)
        from .accessible_controls import AccessibleTextCtrl
        self.txt = AccessibleTextCtrl(self, value=self.texto,
                                      style=wx.TE_MULTILINE | wx.TE_READONLY,
                                      name=_("Texto del código QR"))
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, label=_("&Copiar texto"))
        b.Bind(wx.EVT_BUTTON, self._on_copiar)
        fila.Add(b, 0, wx.ALL, 5)
        b = wx.Button(self, label=_("&Guardar imagen..."))
        b.Bind(wx.EVT_BUTTON, self._on_guardar)
        fila.Add(b, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Ce&rrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._img = None
        self._render()

    def _render(self):
        try:
            import qrcode
            from io import BytesIO
            img = qrcode.make(self.texto)
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            self._png = buf.getvalue()
            buf.seek(0)
            wx_img = wx.Image(buf)
            if wx_img.IsOk():
                # Escalar a un tamaño cómodo (cuadrado ~380 px).
                lado = 380
                wx_img = wx_img.Scale(lado, lado, wx.IMAGE_QUALITY_HIGH)
                self.bmp.SetBitmap(wx.Bitmap(wx_img))
                self.Layout()
        except Exception as e:
            log.debug(f"No se pudo dibujar el QR: {e}")
            self._png = None
            self.lbl_estado.SetLabel(_("No se pudo dibujar el QR (falta la librería 'qrcode'). Usa el "
                                       "texto de abajo o el método por número/código."))

    def _on_copiar(self, event):
        try:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(self.texto))
                wx.TheClipboard.Close()
                notificar(_("Texto copiado."))
        except Exception:
            speak(_("No se pudo copiar."), interrupt=True)

    def _on_guardar(self, event):
        if not getattr(self, "_png", None):
            speak(_("No hay imagen que guardar."), interrupt=True)
            return
        dlg = wx.FileDialog(self, _("Guardar código QR"), defaultFile="codigo_qr.png",
                            wildcard=_("Imagen PNG|*.png"),
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            try:
                with open(dlg.GetPath(), "wb") as f:
                    f.write(self._png)
                notificar(_("Código QR guardado."))
            except Exception as e:
                notificar(_("No se pudo guardar: {0}").format(e))
        dlg.Destroy()


def mostrar_codigo_qr(parent, texto, titulo=None, ayuda=None):
    """Abre el visor de QR con el texto/enlace dado. Devuelve True si se mostró."""
    if not (texto or "").strip():
        speak(_("No hay texto para generar el código QR."), interrupt=True)
        return False
    dlg = QRDialog(parent, texto, titulo=titulo, ayuda=ayuda)
    dlg.ShowModal()
    dlg.Destroy()
    return True


class ImagenDialog(wx.Dialog):
    """Muestra una imagen (PNG/JPG…) grande en una ventana; útil para escanear un QR recibido."""

    def __init__(self, parent, ruta, titulo=None, ayuda=None):
        super().__init__(parent, title=titulo or _("Imagen"),
                         size=(560, 640), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Ventana de imagen"))
        self.ruta = ruta
        s = wx.BoxSizer(wx.VERTICAL)
        if ayuda:
            s.Add(wx.StaticText(self, label=ayuda), 0, wx.ALL, 8)
        self.bmp = wx.StaticBitmap(self, name=_("Imagen"))
        s.Add(self.bmp, 1, wx.ALIGN_CENTER | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b = wx.Button(self, label=_("&Guardar imagen..."))
        b.Bind(wx.EVT_BUTTON, self._on_guardar)
        fila.Add(b, 0, wx.ALL, 5)
        b = wx.Button(self, label=_("Abrir con el &visor del sistema"))
        b.Bind(wx.EVT_BUTTON, self._on_sistema)
        fila.Add(b, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Ce&rrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        try:
            img = wx.Image(self.ruta)
            if img.IsOk():
                # Escalar para que quepa (máx 460 px de lado, manteniendo proporción).
                w, h = img.GetWidth(), img.GetHeight()
                lado = 460
                if w and h:
                    f = min(lado / w, lado / h, 3.0)
                    img = img.Scale(max(1, int(w * f)), max(1, int(h * f)), wx.IMAGE_QUALITY_HIGH)
                self.bmp.SetBitmap(wx.Bitmap(img))
                self.Layout()
        except Exception as e:
            log.debug(f"No se pudo cargar la imagen: {e}")

    def _on_guardar(self, event):
        import os
        dlg = wx.FileDialog(self, _("Guardar imagen"),
                            defaultFile=os.path.basename(self.ruta) or "imagen.png",
                            wildcard=_("Imagen|*.png;*.jpg;*.jpeg;*.gif|Todos|*.*"),
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            try:
                import shutil
                shutil.copyfile(self.ruta, dlg.GetPath())
                notificar(_("Imagen guardada."))
            except Exception as e:
                notificar(_("No se pudo guardar: {0}").format(e))
        dlg.Destroy()

    def _on_sistema(self, event):
        try:
            import os
            os.startfile(self.ruta)
        except Exception as e:
            notificar(_("No se pudo abrir: {0}").format(e))


def mostrar_imagen(parent, ruta, titulo=None, ayuda=None):
    """Abre una imagen local en una ventana (para verla/escanearla). Devuelve True si se mostró."""
    import os
    if not ruta or not os.path.exists(ruta):
        speak(_("No se encontró la imagen."), interrupt=True)
        return False
    dlg = ImagenDialog(parent, ruta, titulo=titulo, ayuda=ayuda)
    dlg.ShowModal()
    dlg.Destroy()
    return True
