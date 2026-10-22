# -*- coding: utf-8 -*-
"""Notificaciones AGRUPADAS de Mastodon (auditoría N2).

Vista interactiva sobre la API v2 de notificaciones agrupadas (Mastodon 4.3+): cada fila es
un grupo real del servidor ("A X, Y y 3 más les gustó tu publicación"), con expansión (ver
todas las cuentas del grupo) y descarte por grupo. Si el servidor no tiene la API v2, se
agrupa localmente por tipo y publicación (sin descartar por grupo, que requiere group_key).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import wx

from app.core.speech import speak, notificar
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import confirmar, habilitar_escape_cierre, mostrar_info

log = logging.getLogger(__name__)

TIPOS = {
    "favourite": lambda: _("Favoritos"), "reblog": lambda: _("Impulsos"),
    "mention": lambda: _("Menciones"), "follow": lambda: _("Nuevos seguidores"),
    "follow_request": lambda: _("Solicitudes de seguimiento"),
    "poll": lambda: _("Encuestas finalizadas"), "status": lambda: _("Publicaciones de cuentas que sigues"),
    "update": lambda: _("Ediciones de publicaciones"), "quote": lambda: _("Citas"),
    "admin.sign_up": lambda: _("Altas de cuentas"), "admin.report": lambda: _("Reportes"),
    "severed_relationships": lambda: _("Relaciones cortadas"),
    "moderation_warning": lambda: _("Avisos de moderación"),
}


class GruposNotificacionesDialog(wx.Dialog):
    """Gestor modal de notificaciones agrupadas de una cuenta."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Notificaciones agrupadas"), size=(640, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Notificaciones agrupadas de Mastodon"))
        self.account_id = account_id
        self._grupos = []
        self._nativo = False

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Cargando grupos de notificaciones..."))
        vbox.Add(self.lbl, 0, wx.ALL, 5)
        self.lista = wx.ListBox(self, style=wx.LB_SINGLE, name=_("Grupos de notificaciones"))
        vbox.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_cuentas = wx.Button(self, label=_("Ver &cuentas del grupo"))
        self.btn_cuentas.Bind(wx.EVT_BUTTON, self._on_cuentas)
        botones.Add(self.btn_cuentas, 0, wx.ALL, 5)
        self.btn_descartar = wx.Button(self, label=_("&Descartar grupo"))
        self.btn_descartar.Bind(wx.EVT_BUTTON, self._on_descartar)
        botones.Add(self.btn_descartar, 0, wx.ALL, 5)
        btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"))
        botones.Add(btn_cerrar, 0, wx.ALL, 5)
        vbox.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(vbox)
        habilitar_escape_cierre(self)
        self.btn_cuentas.Disable()
        self.btn_descartar.Disable()
        self.CenterOnParent()
        self.lista.SetFocus()
        self._cargar()

    def _cargar(self):
        def _cb(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron obtener las notificaciones: {0}").format(err))
                return
            self._nativo = bool((res or {}).get("nativo"))
            self._grupos = (res or {}).get("grupos") or []
            self._grupos.sort(key=lambda g: -g.get("total", 0))
            self._pintar()
        mastodon_service.obtener_notificaciones_agrupadas_async(self.account_id, callback=_cb)

    def _pintar(self):
        self.lista.Clear()
        for g in self._grupos:
            etiqueta = TIPOS.get(g.get("tipo", ""), lambda t=g.get("tipo", ""): t)()
            autores = ", ".join(g.get("autores") or [])
            resto = g.get("total", 0) - len(g.get("autores") or [])
            if resto > 0 and autores:
                autores = _("{0} y {1} más").format(autores, resto)
            partes = ["{0}: {1}".format(etiqueta, autores or g.get("total", 0))]
            if g.get("extracto"):
                partes.append("«{0}»".format(g["extracto"]))
            if g.get("fecha"):
                partes.append(str(g["fecha"])[:16].replace("T", " "))
            self.lista.Append(" — ".join(partes))
        hay = bool(self._grupos)
        if hay:
            self.lista.SetSelection(0)
        self.btn_cuentas.Enable(hay)
        # El descarte por grupo requiere group_key (API v2 del servidor).
        self.btn_descartar.Enable(hay and self._nativo)
        if self._nativo:
            self.lbl.SetLabel(_("Grupos de notificaciones de tu servidor ({0}):").format(len(self._grupos)))
        else:
            self.lbl.SetLabel(_("Tu servidor no agrupa notificaciones (necesita Mastodon 4.3): "
                                "se agrupan localmente ({0} grupos). No se pueden descartar por grupo.").format(
                                    len(self._grupos)))
        if not hay:
            self.lbl.SetLabel(_("No tienes notificaciones."))

    def _grupo(self):
        idx = self.lista.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self._grupos):
            return None
        return self._grupos[idx]

    def _on_cuentas(self, event):
        g = self._grupo()
        if not g:
            return
        # Sin group_key (agrupado local): se muestran los autores de muestra que tenemos.
        if not (self._nativo and g.get("group_key")):
            texto = "\n".join(g.get("autores") or []) or _("Sin datos de cuentas.")
            mostrar_info(self, texto, _("Cuentas del grupo"))
            return
        speak(_("Consultando las cuentas del grupo..."), interrupt=True)

        def _cb(res, err):
            if not self:
                return
            if err:
                notificar(_("Error: {0}").format(err))
                return
            lineas = ["@{0} ({1})".format(c["acct"], c["nombre"]) if c.get("nombre") else "@" + c["acct"]
                      for c in (res or [])]
            mostrar_info(self, "\n".join(lineas) or _("Sin cuentas."), _("Cuentas del grupo"))
        mastodon_service.cuentas_grupo_notif_async(self.account_id, g["group_key"], callback=_cb)

    def _on_descartar(self, event):
        g = self._grupo()
        if not g or not g.get("group_key"):
            return
        if confirmar(self, _("¿Descartar este grupo entero de notificaciones del servidor?"),
                     _("Descartar grupo")) != wx.ID_YES:
            return

        def _cb(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo descartar: {0}").format(err))
                return
            notificar(_("Grupo descartado."))
            self._cargar()
        mastodon_service.descartar_grupo_notif_async(self.account_id, g["group_key"], callback=_cb)
