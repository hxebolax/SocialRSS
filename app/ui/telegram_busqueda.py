# -*- coding: utf-8 -*-
"""Búsqueda de mensajes de Telegram en una VENTANA APARTE (accesible) y selector de fecha accesible.

Antes, buscar dentro de un chat mezclaba los resultados en la propia conversación; y el "Ir a una
fecha" usaba un calendario desplegable poco práctico con lector. Aquí:
- `TelegramBusquedaChatDialog`: busca en un chat/grupo/canal y muestra los resultados en su propia
  lista (no toca la conversación). Con acotado por fechas OPCIONAL usando campos accesibles.
- `SelectorFechaDialog` / `pedir_fecha`: elige una fecha con tres campos numéricos (día, mes, año),
  que el lector lee sin problema, en vez de un calendario.
"""
import datetime as _dt
import logging

import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleTextCtrl, AccessibleListCtrl, mostrar_info

log = logging.getLogger(__name__)


class _CamposFecha:
    """Tres campos numéricos (día, mes, año) accesibles. Devuelve un datetime o None.

    Van dentro de un GRUPO (wx.StaticBox) cuyo título es la etiqueta ("Desde", "Hasta", "Fecha"):
    así el lector ANUNCIA esa etiqueta al ENTRAR en el primer campo del grupo (al tabular del
    checkbox al día lee "Desde"; al pasar del año de Desde al día de Hasta lee "Hasta"). Además, por
    si el lector no locuta el grupo, el propio campo "día" lleva la etiqueta en su nombre
    ("Desde, día") como refuerzo."""

    def __init__(self, parent, sizer, etiqueta, hoy=None):
        hoy = hoy or _dt.date.today()
        nombre_grupo = (etiqueta or "").rstrip(":").strip()  # "Desde" / "Hasta" / "Fecha"
        box = wx.StaticBox(parent, label=nombre_grupo)
        bs = wx.StaticBoxSizer(box, wx.HORIZONTAL)
        # OJO: los controles se parentean al StaticBox (agrupación accesible correcta en wx 4).
        bs.Add(wx.StaticText(box, label=_("Día:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
        # El primer campo lleva el nombre del grupo delante como refuerzo del anuncio.
        self.dia = wx.SpinCtrl(box, min=1, max=31, initial=hoy.day,
                               name=_("{0}, día").format(nombre_grupo) if nombre_grupo else _("Día"))
        bs.Add(self.dia, 0, wx.RIGHT, 6)
        bs.Add(wx.StaticText(box, label=_("Mes:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.mes = wx.SpinCtrl(box, min=1, max=12, initial=hoy.month, name=_("Mes"))
        bs.Add(self.mes, 0, wx.RIGHT, 6)
        bs.Add(wx.StaticText(box, label=_("Año:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.anio = wx.SpinCtrl(box, min=2000, max=hoy.year + 1, initial=hoy.year, name=_("Año"))
        bs.Add(self.anio, 0)
        sizer.Add(bs, 0, wx.ALL | wx.EXPAND, 6)

    def valor(self):
        try:
            return _dt.datetime(self.anio.GetValue(), self.mes.GetValue(), self.dia.GetValue())
        except ValueError:
            return None


class SelectorFechaDialog(wx.Dialog):
    """Elige una fecha con campos numéricos accesibles (día/mes/año), sin calendario."""

    def __init__(self, parent, titulo, mensaje):
        super().__init__(parent, title=titulo, style=wx.DEFAULT_DIALOG_STYLE, name=titulo)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=mensaje), 0, wx.ALL, 8)
        self._campos = _CamposFecha(self, s, _("Fecha:"))
        s.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizerAndFit(s)
        self.CenterOnParent()

    def fecha(self):
        return self._campos.valor()


def pedir_fecha(parent, titulo, mensaje):
    """Muestra el selector de fecha accesible y devuelve un datetime, o None si se cancela."""
    dlg = SelectorFechaDialog(parent, titulo, mensaje)
    try:
        if dlg.ShowModal() != wx.ID_OK:
            return None
        return dlg.fecha()
    finally:
        dlg.Destroy()


def etiqueta_adjunto(r) -> str:
    """Etiqueta legible del adjunto de un resultado ("[Documento: informe.pdf]"), la MISMA que usa
    la conversación. Si la etiqueta no incluye el nombre del archivo (un audio con duración, un
    vídeo...), se añade: en una búsqueda el nombre es justo lo que identifica el resultado."""
    media = r.get("media") or ""
    if not media:
        return ""
    from .telegram_conv_mensajes import _ConvMensajesMixin as _M
    nombre = (r.get("file_name") or "").strip()
    etq = _M._etiqueta_media(_M, media, r.get("duration") or 0, nombre)
    if nombre and nombre not in etq and etq.endswith("]"):
        etq = f"{etq[:-1]}: {nombre}]"
    return etq


def texto_resultado(r) -> str:
    """Texto de un resultado para la lista: adjunto (con su nombre) y el texto o pie del mensaje."""
    return " ".join(x for x in (etiqueta_adjunto(r), (r.get("texto") or "").strip()) if x)


class TelegramBusquedaChatDialog(wx.Dialog):
    """Busca mensajes DENTRO de un chat/grupo/canal y los muestra en su propia lista (no los mezcla
    con la conversación). Enter sobre un resultado muestra el mensaje completo.

    `estado` (opcional) restaura una búsqueda anterior: texto, fechas, resultados y el resultado
    en el que estabas. La conversación lo guarda al usar "Ir al mensaje", para que al volver a
    Buscar no haya que repetir la búsqueda ni buscar otra vez el sitio."""

    def __init__(self, parent, account_id, chat_id, chat_title, conversacion=None, estado=None):
        titulo = _("Buscar en: {0}").format(chat_title or "")
        if estado and estado.get("resultados"):
            titulo = _("Buscar en: {0} (última búsqueda)").format(chat_title or "")
        super().__init__(parent, title=titulo,
                         size=(760, 560), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Buscar en la conversación"))
        self._titulo_base = _("Buscar en: {0}").format(chat_title or "")
        self.account_id = account_id
        self.chat_id = chat_id
        self._conv = conversacion   # conversación de origen (para "Ir al mensaje"), opcional
        self.resultados = []
        self.mensaje_objetivo = None  # message_id a enfocar en la conversación al cerrar (Ir al mensaje)
        s = wx.BoxSizer(wx.VERTICAL)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Texto a buscar (vacío = todos, útil con fechas):")),
                 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.txt = AccessibleTextCtrl(self, style=wx.TE_PROCESS_ENTER, name=_("Texto a buscar"),
                                      ayuda_texto=_("Escribe y pulsa Intro para buscar."))
        self.txt.Bind(wx.EVT_TEXT_ENTER, lambda e: self._buscar())
        fila.Add(self.txt, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(fila, 0, wx.EXPAND)

        self.cb_fechas = wx.CheckBox(self, label=_("Acotar por &fechas"),
                                     name=_("Acotar la búsqueda por fechas"))
        self.cb_fechas.Bind(wx.EVT_CHECKBOX, self._on_toggle_fechas)
        s.Add(self.cb_fechas, 0, wx.ALL, 6)
        self._panel_fechas = wx.Panel(self)
        pf = wx.BoxSizer(wx.VERTICAL)
        self._desde = _CamposFecha(self._panel_fechas, pf, _("Desde:"),
                                   hoy=_dt.date.today() - _dt.timedelta(days=30))
        self._hasta = _CamposFecha(self._panel_fechas, pf, _("Hasta:"))
        self._panel_fechas.SetSizer(pf)
        self._panel_fechas.Hide()
        s.Add(self._panel_fechas, 0, wx.EXPAND)

        botb = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_buscar = wx.Button(self, label=_("&Buscar"), name=_("Buscar"))
        self.btn_buscar.Bind(wx.EVT_BUTTON, lambda e: self._buscar())
        botb.Add(self.btn_buscar, 0, wx.ALL, 5)
        self.lbl = wx.StaticText(self, label="")
        botb.Add(self.lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        s.Add(botb, 0, wx.EXPAND)

        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Resultados de la búsqueda"))
        self.lista.InsertColumn(0, _("Fecha"), width=150)
        self.lista.InsertColumn(1, _("Autor"), width=180)
        self.lista.InsertColumn(2, _("Mensaje"), width=380)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_activar)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        fin = wx.BoxSizer(wx.HORIZONTAL)
        if self._conv is not None:
            self.btn_ir = wx.Button(self, label=_("&Ir al mensaje en la conversación"),
                                    name=_("Traer el mensaje a la conversación"))
            self.btn_ir.Bind(wx.EVT_BUTTON, lambda e: self._ir_al_mensaje())
            fin.Add(self.btn_ir, 0, wx.ALL, 5)
        fin.AddStretchSpacer()
        fin.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(fin, 0, wx.EXPAND | wx.ALL, 4)

        self.SetSizer(s)
        self.CenterOnParent()
        self.txt.SetFocus()
        if estado:
            self._restaurar(estado)

    def estado(self) -> dict:
        """Lo necesario para reabrir esta búsqueda tal cual: texto, fechas, resultados y el
        resultado enfocado."""
        return {
            "texto": self.txt.GetValue(),
            "fechas": self.cb_fechas.GetValue(),
            "desde": self._desde.valor(),
            "hasta": self._hasta.valor(),
            "resultados": list(self.resultados),
            "foco": self.lista.GetFocusedItem(),
        }

    def _restaurar(self, estado):
        """Vuelve a dejar la búsqueda como estaba (sin repetirla en el servidor)."""
        try:
            self.txt.SetValue(estado.get("texto") or "")
            if estado.get("fechas"):
                self.cb_fechas.SetValue(True)
                for campos, valor in ((self._desde, estado.get("desde")),
                                      (self._hasta, estado.get("hasta"))):
                    if valor:
                        campos.anio.SetValue(valor.year)
                        campos.mes.SetValue(valor.month)
                        campos.dia.SetValue(valor.day)
                self._panel_fechas.Show(True)
                self.Layout()
            self.resultados = list(estado.get("resultados") or [])
        except Exception:
            self.resultados = []
        if self.resultados:
            self._pintar(foco=estado.get("foco", 0), restaurada=True)

    def _on_toggle_fechas(self, event):
        self._panel_fechas.Show(self.cb_fechas.GetValue())
        self.Layout()

    def _buscar(self):
        query = self.txt.GetValue().strip()
        desde = hasta = None
        if self.cb_fechas.GetValue():
            desde = self._desde.valor()
            h = self._hasta.valor()
            # 'hasta' inclusivo: hasta el final de ese día.
            hasta = (h + _dt.timedelta(days=1)) if h else None
        if not query and not (desde or hasta):
            speak(_("Escribe un texto o marca acotar por fechas."), interrupt=True)
            self.txt.SetFocus()
            return
        self.lbl.SetLabel(_("Buscando…"))
        acc, chat = self.account_id, self.chat_id

        def _bg(ctx):
            from app.services.telegram.telegram_service import telegram_service
            return telegram_service.buscar_mensajes_resultados(acc, chat, query, desde, hasta)

        def _fin(res, err):
            if not self:
                return
            if err:
                self.lbl.SetLabel("")
                notificar(_("Error en la búsqueda: {0}").format(err))
                return
            self.resultados = res or []
            if self.GetTitle() != self._titulo_base:
                self.SetTitle(self._titulo_base)  # ya no es "la última búsqueda", es una nueva
            self._pintar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _pintar(self, foco=0, restaurada=False):
        from app.ui.ui_helpers import formatear_tiempo_relativo
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, r in enumerate(self.resultados):
            fecha = formatear_tiempo_relativo(r.get("fecha") or "") or (r.get("fecha") or "")
            autor = r.get("autor") or ""
            texto = texto_resultado(r)
            self.lista.InsertItem(i, fecha)
            self.lista.SetItem(i, 1, autor)
            self.lista.SetItem(i, 2, texto)
            self.lista.establecer_lectura(i, _("{0}. {1}: {2}").format(fecha, autor or _("mensaje"), texto))
        n = len(self.resultados)
        self.lbl.SetLabel(_("Última búsqueda: {0} resultados.").format(n) if restaurada
                          else _("{0} resultados.").format(n))
        if n:
            i = foco if isinstance(foco, int) and 0 <= foco < n else 0
            self.lista.Focus(i)
            self.lista.Select(i)
            self.lista.EnsureVisible(i)
            self.lista.SetFocus()
        else:
            notificar(_("Sin resultados."))

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.resultados[i] if 0 <= i < len(self.resultados) else None

    def _on_activar(self, event):
        r = self._actual()
        if not r:
            return
        from app.ui.ui_helpers import formatear_tiempo_relativo
        lineas = [
            _("Fecha: {0}").format(formatear_tiempo_relativo(r.get("fecha") or "") or r.get("fecha") or "?"),
            _("Autor: {0}").format(r.get("autor") or _("(desconocido)")),
        ]
        adjunto = etiqueta_adjunto(r)
        if adjunto:
            lineas.append(_("Adjunto: {0}").format(adjunto.strip("[]")))
        lineas.append("")
        lineas.append(r.get("texto") or _("(sin texto)"))
        mostrar_info(self, "\n".join(lineas), _("Mensaje encontrado"))

    def _ir_al_mensaje(self):
        """Trae a la conversación de origen los mensajes alrededor de la fecha del resultado y cierra
        la búsqueda. El foco en el mensaje concreto lo pone la conversación DESPUÉS de cerrarse este
        modal (mientras el diálogo sigue abierto, el foco no puede irse a la ventana de detrás)."""
        r = self._actual()
        if not r or self._conv is None:
            return
        fecha = r.get("fecha") or ""
        try:
            f = _dt.datetime.fromisoformat(fecha)
        except Exception:
            notificar(_("No se pudo situar el mensaje por su fecha."))
            return
        acc, chat = self.account_id, self.chat_id
        mid = r.get("message_id")
        speak(_("Trayendo el mensaje a la conversación…"), interrupt=True)

        def _bg(ctx):
            from app.services.telegram.telegram_service import telegram_service
            # Por IDENTIFICADOR: trae el propio mensaje y los anteriores. Por fecha solo llegaban
            # los estrictamente anteriores y el mensaje buscado podía no aparecer. Si fallara,
            # se recurre a la fecha como antes.
            if mid is not None:
                try:
                    return telegram_service.cargar_mensajes_hasta_id(acc, chat, mid)
                except Exception as e:
                    log.debug(f"Ir al mensaje por id falló, se usa la fecha: {e}")
            return telegram_service.cargar_mensajes_desde_fecha(acc, chat, f)

        def _fin(n, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            # Guardar el mensaje objetivo y cerrar: la conversación lo enfocará al recuperar el foco.
            self.mensaje_objetivo = mid
            self.EndModal(wx.ID_OK)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
