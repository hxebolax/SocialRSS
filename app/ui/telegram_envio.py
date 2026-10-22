# -*- coding: utf-8 -*-
"""Diálogo para enviar UNO O VARIOS archivos por Telegram, cada uno con su propio comentario.

Telegram permite adjuntar varios archivos a la vez; aquí se eligen todos, se le pone un comentario
(opcional) a CADA uno y se envían. El progreso de subida no vive aquí: la conversación inserta una
fila por archivo y la va actualizando con el porcentaje (ver `_enviar_archivos_con_progreso`)."""
import os

import wx

from app.core.speech import speak
from .accessible_controls import AccessibleTextCtrl, AccessibleListCtrl


def tipo_por_extension(ruta: str) -> str:
    """Deduce el tipo de envío (photo/video/voice/audio/document) por la extensión. Réplica ligera
    de TelegramService.tipo_por_extension para no cargar el SDK al abrir el diálogo."""
    ext = os.path.splitext(ruta)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return "photo"
    if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
        return "video"
    if ext in (".ogg", ".oga"):
        return "voice"
    if ext in (".mp3", ".m4a", ".flac", ".wav"):
        return "audio"
    return "document"


def _etiqueta_tipo(tipo: str) -> str:
    return {
        "photo": _("Foto"), "video": _("Vídeo"), "voice": _("Nota de voz"),
        "audio": _("Audio"), "document": _("Documento"),
    }.get(tipo, _("Documento"))


class TelegramEnviarArchivosDialog(wx.Dialog):
    """Elige varios archivos y un comentario para cada uno. Al aceptar, `self.items` es una lista de
    dicts {ruta, caption, tipo} en el orden de envío."""

    def __init__(self, parent, rutas_iniciales=None, caption_inicial=""):
        super().__init__(parent, title=_("Enviar archivos"), size=(700, 540),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Enviar archivos"))
        self.items = []      # [{ruta, caption, tipo}]
        self._sel = -1       # índice actualmente seleccionado en la lista (o -1)
        # El texto que ya había en la caja de redacción se usará como comentario del PRIMER archivo
        # que se añada (venga de rutas iniciales o del selector de archivos que se abre al entrar).
        self._caption_pendiente = caption_inicial or ""

        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(self, label=_("&Archivos a enviar (cada uno con su comentario):")),
              0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Archivos a enviar"))
        self.lista.InsertColumn(0, _("Archivo"), width=320)
        self.lista.InsertColumn(1, _("Tipo"), width=90)
        self.lista.InsertColumn(2, _("Comentario"), width=250)
        self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_seleccionar)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        fila_bot = wx.BoxSizer(wx.HORIZONTAL)
        btn_add = wx.Button(self, label=_("&Añadir archivos..."), name=_("Añadir archivos"))
        btn_add.Bind(wx.EVT_BUTTON, self._on_añadir)
        fila_bot.Add(btn_add, 0, wx.ALL, 5)
        self.btn_quitar = wx.Button(self, label=_("&Quitar seleccionado"), name=_("Quitar seleccionado"))
        self.btn_quitar.Bind(wx.EVT_BUTTON, self._on_quitar)
        self.btn_quitar.Enable(False)
        fila_bot.Add(self.btn_quitar, 0, wx.ALL, 5)
        s.Add(fila_bot, 0)

        s.Add(wx.StaticText(self, label=_("&Comentario para el archivo seleccionado:")), 0, wx.ALL, 6)
        self.txt_comentario = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE, name=_("Comentario del archivo"),
            ayuda_texto=_("Escribe el pie de foto o comentario que acompañará a este archivo."))
        self.txt_comentario.Bind(wx.EVT_TEXT, self._on_texto_comentario)
        self.txt_comentario.Enable(False)
        s.Add(self.txt_comentario, 0, wx.EXPAND | wx.ALL, 6)

        fin = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_enviar = wx.Button(self, wx.ID_OK, _("&Enviar"), name=_("Enviar"))
        self.btn_enviar.Bind(wx.EVT_BUTTON, self._on_enviar)
        self.btn_enviar.Enable(False)
        fin.Add(self.btn_enviar, 0, wx.ALL, 5)
        btn_cancelar = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"), name=_("Cancelar"))
        fin.Add(btn_cancelar, 0, wx.ALL, 5)
        s.Add(fin, 0, wx.ALIGN_RIGHT)

        self.SetSizer(s)
        self.SetEscapeId(wx.ID_CANCEL)

        if rutas_iniciales:
            self._agregar_rutas(rutas_iniciales)
        if self.items:
            self._seleccionar_fila(0)
        else:
            wx.CallAfter(self._on_añadir, None)   # abrir directamente el selector de archivos

    # ---- gestión de la lista de archivos ----
    def _agregar_rutas(self, rutas):
        existentes = {it["ruta"] for it in self.items}
        primero = len(self.items) == 0
        for r in rutas:
            if r in existentes:
                continue
            self.items.append({"ruta": r, "caption": "", "tipo": tipo_por_extension(r)})
            existentes.add(r)
        # Volcar el comentario pendiente (el que venía de la caja de redacción) al primer archivo.
        if primero and self._caption_pendiente and self.items:
            self.items[0]["caption"] = self._caption_pendiente
            self._caption_pendiente = ""
        self._pintar()
        self.btn_enviar.Enable(bool(self.items))

    def _pintar(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, it in enumerate(self.items):
            nombre = os.path.basename(it["ruta"])
            tipo = _etiqueta_tipo(it["tipo"])
            com = (it["caption"] or "").replace("\n", " ")
            self.lista.InsertItem(i, nombre)
            self.lista.SetItem(i, 1, tipo)
            self.lista.SetItem(i, 2, com)
            lectura = _("{0}, {1}").format(nombre, tipo)
            if com:
                lectura += _(", comentario: {0}").format(com)
            else:
                lectura += _(", sin comentario")
            self.lista.establecer_lectura(i, lectura)

    def _seleccionar_fila(self, idx):
        if 0 <= idx < self.lista.GetItemCount():
            self.lista.Focus(idx)
            self.lista.Select(idx)

    def _on_seleccionar(self, event):
        idx = event.GetIndex()
        self._sel = idx
        self.btn_quitar.Enable(True)
        self.txt_comentario.Enable(True)
        it = self.items[idx] if 0 <= idx < len(self.items) else None
        # ChangeValue no dispara EVT_TEXT (no pisaría el caption del item con un evento espurio).
        self.txt_comentario.ChangeValue(it["caption"] if it else "")
        event.Skip()

    def _on_texto_comentario(self, event):
        if 0 <= self._sel < len(self.items):
            self.items[self._sel]["caption"] = self.txt_comentario.GetValue()
            # Reflejar en la columna Comentario sin repintar toda la lista (silencioso).
            self.lista.SetItem(self._sel, 2, self.txt_comentario.GetValue().replace("\n", " "))
        event.Skip()

    def _on_añadir(self, event):
        with wx.FileDialog(self, _("Selecciona los archivos a enviar"),
                           style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                nuevas = dlg.GetPaths()
                antes = len(self.items)
                self._agregar_rutas(nuevas)
                if len(self.items) > antes:
                    self._seleccionar_fila(antes)   # primer archivo recién añadido
                    speak(_("{0} archivos en la lista.").format(len(self.items)), interrupt=True)

    def _on_quitar(self, event):
        idx = self._sel
        if not (0 <= idx < len(self.items)):
            return
        del self.items[idx]
        self._sel = -1
        self._pintar()
        if self.items:
            nuevo = min(idx, len(self.items) - 1)
            self._seleccionar_fila(nuevo)
        else:
            self.txt_comentario.ChangeValue("")
            self.txt_comentario.Enable(False)
            self.btn_quitar.Enable(False)
            self.btn_enviar.Enable(False)
        speak(_("Quitado. {0} archivos.").format(len(self.items)), interrupt=True)

    def _on_enviar(self, event):
        if not self.items:
            speak(_("Añade al menos un archivo."), interrupt=True)
            return
        event.Skip()   # deja que ID_OK cierre el diálogo con EndModal
