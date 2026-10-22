# -*- coding: utf-8 -*-
"""Diálogos de emisiones en directo de PeerTube (Fase 6): crear un directo y ver sus datos de emisión.

La clave de emisión (streamKey) es SECRETA: se muestra oculta y solo se revela o copia con una acción
explícita y advertida (no se registra nunca en logs).
"""
import wx

from app.core.speech import speak, notificar, copiar_al_portapapeles
from .accessible_controls import AccessibleTextCtrl, mostrar_info, habilitar_escape_cierre
from .peertube_biblioteca_dialogs import _CanalMixinDialog, PRIVACIDADES, _elige_privacidad

# Modos de latencia (id -> etiqueta).
LATENCIAS = [(1, lambda: _("Normal")), (2, lambda: _("Alta (más estable)")),
             (3, lambda: _("Baja (más inmediata)"))]


class PeerTubeDirectoDialog(wx.Dialog, _CanalMixinDialog):
    """Crear una emisión en directo. `self.resultado` = dict o None."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Crear directo en PeerTube"), size=(540, 460),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Crear directo"))
        self.account_id = account_id
        self.resultado = None
        self._canales = []
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Canal:")), 0, wx.LEFT | wx.TOP, 8)
        self.choice_canal = wx.Choice(self, choices=[_("Cargando canales...")], name=_("Canal"))
        s.Add(self.choice_canal, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Título del directo:")), 0, wx.LEFT, 8)
        self.txt_titulo = AccessibleTextCtrl(self, name=_("Título"), max_length=120)
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Privacidad:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_priv = wx.Choice(self, choices=[p[1]() for p in PRIVACIDADES], name=_("Privacidad"))
        self.choice_priv.SetSelection(0)
        fila.Add(self.choice_priv, 0, wx.RIGHT, 12)
        fila.Add(wx.StaticText(self, label=_("Latencia:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_lat = wx.Choice(self, choices=[l[1]() for l in LATENCIAS], name=_("Latencia"))
        self.choice_lat.SetSelection(0)
        fila.Add(self.choice_lat, 0)
        s.Add(fila, 0, wx.ALL, 6)
        self.cb_permanente = wx.CheckBox(self, label=_("Directo permanente (puedo emitir varias veces)"),
                                         name=_("Directo permanente"))
        self.cb_permanente.Bind(wx.EVT_CHECKBOX, self._on_permanente)
        self.cb_replay = wx.CheckBox(self, label=_("Guardar la repetición al terminar"),
                                     name=_("Guardar repetición"))
        s.Add(self.cb_permanente, 0, wx.ALL, 4)
        s.Add(self.cb_replay, 0, wx.ALL, 4)
        s.Add(wx.StaticText(self, label=_(
            "Tras crearlo, obtendrás la URL RTMP y la clave de emisión para tu programa (OBS, etc.).")),
            0, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Crear"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        bs.Add(b_ok, 0, wx.ALL, 4)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar_canales_en(account_id, self.choice_canal)

    def _on_permanente(self, event):
        # Permanente y "guardar repetición" suelen ser incompatibles: si se marca uno, se desmarca el otro.
        if self.cb_permanente.GetValue() and self.cb_replay.GetValue():
            self.cb_replay.SetValue(False)

    def _on_ok(self, event):
        titulo = self.txt_titulo.GetValue().strip()
        canal_id = self._canal_id(self.choice_canal)
        if not titulo or not canal_id:
            mostrar_info(self, _("El título y el canal son obligatorios."), _("Faltan datos"))
            return
        permanente = self.cb_permanente.GetValue()
        self.resultado = {"channel_id": canal_id, "name": titulo,
                          "privacy": _elige_privacidad(self.choice_priv),
                          "permanent_live": permanente,
                          "latency_mode": LATENCIAS[self.choice_lat.GetSelection()][0],
                          "save_replay": self.cb_replay.GetValue() and not permanente}
        self.EndModal(wx.ID_OK)


class PeerTubeDatosEmisionDialog(wx.Dialog):
    """Muestra la URL RTMP y la clave de emisión (oculta por defecto)."""

    def __init__(self, parent, info):
        super().__init__(parent, title=_("Datos de emisión del directo"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Datos de emisión"))
        self._clave = info.get("stream_key", "")
        self._visible = False
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("URL del servidor RTMP:")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_rtmp = AccessibleTextCtrl(self, value=info.get("rtmp_url", ""), style=wx.TE_READONLY,
                                           name=_("URL RTMP"))
        s.Add(self.txt_rtmp, 0, wx.EXPAND | wx.ALL, 6)
        b_rtmp = wx.Button(self, label=_("Copiar la &URL RTMP"))
        b_rtmp.Bind(wx.EVT_BUTTON, lambda e: copiar_al_portapapeles(
            info.get("rtmp_url", ""), _("URL RTMP copiada.")))
        s.Add(b_rtmp, 0, wx.ALL, 4)
        if info.get("rtmps_url"):
            s.Add(wx.StaticText(self, label=_("URL RTMPS (segura):")), 0, wx.LEFT, 8)
            self.txt_rtmps = AccessibleTextCtrl(self, value=info.get("rtmps_url", ""),
                                                style=wx.TE_READONLY, name=_("URL RTMPS"))
            s.Add(self.txt_rtmps, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Clave de emisión (SECRETA, no la compartas):")),
              0, wx.LEFT, 8)
        self.txt_clave = AccessibleTextCtrl(self, value=self._enmascarada(), style=wx.TE_READONLY,
                                            name=_("Clave de emisión"))
        s.Add(self.txt_clave, 0, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_ver = wx.Button(self, label=_("&Mostrar la clave"))
        self.btn_ver.Bind(wx.EVT_BUTTON, self._on_ver)
        b_copiar = wx.Button(self, label=_("Copiar la &clave de emisión"))
        b_copiar.Bind(wx.EVT_BUTTON, self._on_copiar)
        fila.Add(self.btn_ver, 0, wx.RIGHT, 4)
        fila.Add(b_copiar, 0)
        s.Add(fila, 0, wx.ALL, 4)
        info_txt = []
        info_txt.append(_("Directo permanente: sí") if info.get("permanent_live")
                        else _("Directo permanente: no"))
        lat = {1: _("Normal"), 2: _("Alta"), 3: _("Baja")}.get(info.get("latency_mode"), "")
        if lat:
            info_txt.append(_("Latencia: {0}").format(lat))
        info_txt.append(_("Guardar repetición: sí") if info.get("save_replay")
                        else _("Guardar repetición: no"))
        s.Add(wx.StaticText(self, label="\n".join(info_txt)), 0, wx.ALL, 8)
        s.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)

    def _enmascarada(self):
        return "•" * min(len(self._clave), 24) if self._clave else ""

    def _on_ver(self, event):
        self._visible = not self._visible
        self.txt_clave.SetValue(self._clave if self._visible else self._enmascarada())
        self.btn_ver.SetLabel(_("&Ocultar la clave") if self._visible else _("&Mostrar la clave"))

    def _on_copiar(self, event):
        if not self._clave:
            return
        copiar_al_portapapeles(self._clave, _("Clave de emisión copiada. No la compartas; da acceso "
                                              "a emitir en tu directo."))
