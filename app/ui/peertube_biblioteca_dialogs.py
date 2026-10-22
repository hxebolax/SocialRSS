# -*- coding: utf-8 -*-
"""Diálogos de biblioteca y creación de PeerTube (Fase 5): subir, importar, editar un vídeo, y
gestionar listas de reproducción, canales e importaciones. Accesibles y en español.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.peertube import peertube_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                   mostrar_info, confirmar, habilitar_escape_cierre)

# Niveles de privacidad de PeerTube (id -> etiqueta).
PRIVACIDADES = [(1, lambda: _("Público")), (2, lambda: _("No listado")), (3, lambda: _("Privado")),
                (4, lambda: _("Interno"))]


def _elige_privacidad(choice):
    return PRIVACIDADES[choice.GetSelection()][0]


def _preseleccionar_privacidad_defecto(choice):
    """Selecciona en el desplegable la privacidad por defecto configurada en Preferencias
    (peertube.default_privacy: 1 público, 2 no listado, 3 privado, 4 interno)."""
    from app.core.config_manager import config
    pref = int(config.get("peertube", "default_privacy", 1) or 1)
    idx = next((i for i, p in enumerate(PRIVACIDADES) if p[0] == pref), 0)
    choice.SetSelection(idx)


class _CanalMixinDialog:
    """Comparte la carga asíncrona de los canales del usuario en un wx.Choice."""

    def _cargar_canales_en(self, account_id, choice, tras=None):
        def _bg(ctx):
            return peertube_service.mis_canales(account_id)

        def _fin(res, err):
            if not self:
                return
            self._canales = res or []
            choice.Set([c["display"] for c in self._canales] or [_("(sin canales)")])
            if self._canales:
                choice.SetSelection(0)
            if tras:
                tras()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _canal_id(self, choice):
        i = choice.GetSelection()
        if 0 <= i < len(getattr(self, "_canales", [])):
            return self._canales[i]["id"]
        return None


class PeerTubeSubirDialog(wx.Dialog, _CanalMixinDialog):
    """Subir un vídeo. `self.resultado` = dict con los datos, o None."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Subir vídeo a PeerTube"), size=(560, 520),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Subir vídeo"))
        self.account_id = account_id
        self.resultado = None
        self._canales = []
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_archivo = AccessibleTextCtrl(self, style=wx.TE_READONLY, name=_("Archivo de vídeo"))
        b_arch = wx.Button(self, label=_("&Examinar..."))
        b_arch.Bind(wx.EVT_BUTTON, self._on_examinar)
        fila.Add(self.txt_archivo, 1, wx.EXPAND | wx.RIGHT, 4)
        fila.Add(b_arch, 0)
        s.Add(wx.StaticText(self, label=_("Archivo de vídeo:")), 0, wx.LEFT | wx.TOP, 8)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Canal:")), 0, wx.LEFT, 8)
        self.choice_canal = wx.Choice(self, choices=[_("Cargando canales...")], name=_("Canal"))
        s.Add(self.choice_canal, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Título:")), 0, wx.LEFT, 8)
        self.txt_titulo = AccessibleTextCtrl(self, name=_("Título"), max_length=120)
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Descripción:")), 0, wx.LEFT, 8)
        self.txt_desc = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 90),
                                           name=_("Descripción"))
        s.Add(self.txt_desc, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Etiquetas (separadas por comas):")), 0, wx.LEFT, 8)
        self.txt_tags = AccessibleTextCtrl(self, name=_("Etiquetas"))
        s.Add(self.txt_tags, 0, wx.EXPAND | wx.ALL, 6)
        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        fila2.Add(wx.StaticText(self, label=_("Privacidad:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_priv = wx.Choice(self, choices=[p[1]() for p in PRIVACIDADES], name=_("Privacidad"))
        _preseleccionar_privacidad_defecto(self.choice_priv)
        fila2.Add(self.choice_priv, 0, wx.RIGHT, 12)
        self.cb_nsfw = wx.CheckBox(self, label=_("Contenido +18"), name=_("Contenido +18"))
        fila2.Add(self.cb_nsfw, 0, wx.ALIGN_CENTER_VERTICAL)
        s.Add(fila2, 0, wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Subir"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar_canales_en(account_id, self.choice_canal)

    def _on_examinar(self, event):
        wildcard = (_("Vídeos") + " (*.mp4;*.webm;*.mkv;*.mov;*.avi)|*.mp4;*.webm;*.mkv;*.mov;*.avi|"
                    + _("Todos los archivos") + " (*.*)|*.*")
        with wx.FileDialog(self, _("Elige el vídeo"), wildcard=wildcard,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fd:
            if fd.ShowModal() == wx.ID_OK:
                self.txt_archivo.SetValue(fd.GetPath())
                if not self.txt_titulo.GetValue():
                    import os
                    self.txt_titulo.SetValue(os.path.splitext(os.path.basename(fd.GetPath()))[0])

    def _on_ok(self, event):
        ruta = self.txt_archivo.GetValue().strip()
        titulo = self.txt_titulo.GetValue().strip()
        canal_id = self._canal_id(self.choice_canal)
        if not ruta or not titulo or not canal_id:
            mostrar_info(self, _("El archivo, el título y el canal son obligatorios."),
                         _("Faltan datos"))
            return
        tags = [t.strip() for t in self.txt_tags.GetValue().split(",") if t.strip()][:5]
        self.resultado = {"ruta": ruta, "channel_id": canal_id, "name": titulo,
                          "privacy": _elige_privacidad(self.choice_priv),
                          "description": self.txt_desc.GetValue().strip() or None,
                          "tags": tags or None, "nsfw": self.cb_nsfw.GetValue()}
        self.EndModal(wx.ID_OK)


class PeerTubeImportarDialog(wx.Dialog, _CanalMixinDialog):
    """Importar un vídeo desde una URL. `self.resultado` = dict o None."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Importar vídeo por URL"), size=(540, 340),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Importar vídeo"))
        self.account_id = account_id
        self.resultado = None
        self._canales = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Dirección del vídeo (URL o enlace magnet):")),
              0, wx.LEFT | wx.TOP, 8)
        self.txt_url = AccessibleTextCtrl(self, name=_("URL o magnet"))
        s.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Canal:")), 0, wx.LEFT, 8)
        self.choice_canal = wx.Choice(self, choices=[_("Cargando canales...")], name=_("Canal"))
        s.Add(self.choice_canal, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Título (opcional):")), 0, wx.LEFT, 8)
        self.txt_titulo = AccessibleTextCtrl(self, name=_("Título"), max_length=120)
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Privacidad:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_priv = wx.Choice(self, choices=[p[1]() for p in PRIVACIDADES], name=_("Privacidad"))
        _preseleccionar_privacidad_defecto(self.choice_priv)
        fila.Add(self.choice_priv, 0)
        s.Add(fila, 0, wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_(
            "Aviso: importa solo contenido del que tengas derechos o con licencia que lo permita.")),
            0, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Importar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar_canales_en(account_id, self.choice_canal)

    def _on_ok(self, event):
        url = self.txt_url.GetValue().strip()
        canal_id = self._canal_id(self.choice_canal)
        if not url or not canal_id:
            mostrar_info(self, _("La dirección y el canal son obligatorios."), _("Faltan datos"))
            return
        self.resultado = {"url": url, "channel_id": canal_id,
                          "name": self.txt_titulo.GetValue().strip(),
                          "privacy": _elige_privacidad(self.choice_priv),
                          "es_magnet": url.lower().startswith("magnet:")}
        self.EndModal(wx.ID_OK)


class PeerTubeEditarVideoDialog(wx.Dialog):
    """Editar los metadatos de un vídeo propio. `self.resultado` = dict o None."""

    def __init__(self, parent, datos):
        super().__init__(parent, title=_("Editar vídeo"), size=(540, 420),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Editar vídeo"))
        self.resultado = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Título:")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_titulo = AccessibleTextCtrl(self, value=datos.get("name", ""), name=_("Título"),
                                             max_length=120)
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Descripción:")), 0, wx.LEFT, 8)
        self.txt_desc = AccessibleTextCtrl(self, value=datos.get("description", ""),
                                           style=wx.TE_MULTILINE, size=(-1, 120), name=_("Descripción"))
        s.Add(self.txt_desc, 0, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Privacidad:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_priv = wx.Choice(self, choices=[p[1]() for p in PRIVACIDADES], name=_("Privacidad"))
        idx = next((i for i, p in enumerate(PRIVACIDADES) if p[0] == datos.get("privacy")), 0)
        self.choice_priv.SetSelection(idx)
        fila.Add(self.choice_priv, 0)
        s.Add(fila, 0, wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.CenterOnParent()

    def _on_ok(self, event):
        titulo = self.txt_titulo.GetValue().strip()
        if not titulo:
            mostrar_info(self, _("El título es obligatorio."), _("Falta el título"))
            return
        self.resultado = {"name": titulo, "description": self.txt_desc.GetValue().strip(),
                          "privacy": _elige_privacidad(self.choice_priv)}
        self.EndModal(wx.ID_OK)
