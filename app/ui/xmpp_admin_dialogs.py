# -*- coding: utf-8 -*-
"""Diálogos de administración y comandos ad-hoc de XMPP (tanda X7).

- XMPPFormularioDialog: presenta CUALQUIER formulario de un comando ad-hoc (XEP-0050 + 0004) como
  campos accesibles y recorre las etapas (Siguiente/Anterior/Completar/Cancelar). Sirve igual para la
  administración del servidor (0133), para las pasarelas (X8) y para cualquier bot.
- XMPPComandosDialog: lista los comandos que ofrece un JID (el servidor, un componente o un contacto)
  y ejecuta el elegido, abriendo el formulario.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import (AccessibleTextCtrl, AccessibleListCtrl, CustomTextEntryDialog,
                                  mostrar_info, confirmar)


def pintar_campo(panel, sizer, c):
    """Pinta UN campo de formulario (0004) como control accesible según su tipo y lo añade al sizer.
    Devuelve (var, tipo, control, mapa_opciones) para recogerlo luego, o None si es solo texto fijo.
    Compartido por los comandos ad-hoc (X7) y el registro en pasarelas (X8)."""
    tipo = c.get("type") or "text-single"
    var = c.get("var") or ""
    etiqueta = c.get("label") or var
    req = c.get("required")
    etq = etiqueta + (_(" (obligatorio)") if req else "")
    valor = c.get("value")
    desc = c.get("desc") or ""

    if tipo == "fixed":
        sizer.Add(wx.StaticText(panel, label=etiqueta), 0, wx.ALL, 4)
        return None
    if tipo == "hidden":
        return (var, "hidden", None, {"_valor": valor})

    sizer.Add(wx.StaticText(panel, label=etq), 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
    ayuda = desc or etq
    mapa = {}
    if tipo == "boolean":
        ctl = wx.CheckBox(panel, name=etq)
        ctl.SetValue(str(valor) in ("1", "true", "True", "yes"))
    elif tipo == "list-single":
        opciones = c.get("opciones") or []
        etiquetas = [op[0] for op in opciones]
        ctl = wx.Choice(panel, choices=etiquetas, name=etq)
        mapa = {i: opciones[i][1] for i in range(len(opciones))}
        for i, op in enumerate(opciones):
            if op[1] == valor:
                ctl.SetSelection(i)
                break
        else:
            if etiquetas:
                ctl.SetSelection(0)
    elif tipo in ("list-multi", "jid-multi"):
        opciones = c.get("opciones") or []
        etiquetas = [op[0] for op in opciones]
        ctl = wx.CheckListBox(panel, choices=etiquetas, name=etq)
        mapa = {i: opciones[i][1] for i in range(len(opciones))}
        seleccion = valor if isinstance(valor, (list, tuple)) else ([valor] if valor else [])
        for i, op in enumerate(opciones):
            if op[1] in seleccion:
                ctl.Check(i, True)
    elif tipo == "text-multi":
        v = "\n".join(valor) if isinstance(valor, (list, tuple)) else (valor or "")
        ctl = AccessibleTextCtrl(panel, value=v, style=wx.TE_MULTILINE, name=etq, ayuda_texto=ayuda)
    else:  # text-single, text-private, jid-single y cualquier otro
        estilo = wx.TE_PASSWORD if tipo == "text-private" else 0
        v = valor if isinstance(valor, str) else (valor[0] if isinstance(valor, (list, tuple)) and valor else "")
        ctl = AccessibleTextCtrl(panel, value=v or "", style=estilo, name=etq, ayuda_texto=ayuda)
    sizer.Add(ctl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
    return (var, tipo, ctl, mapa)


def recoger_controles(controles):
    """Recoge las respuestas {var: {'type':, 'value':}} de una lista de (var, tipo, control, mapa)."""
    respuestas = {}
    for var, tipo, ctl, mapa in controles:
        if not var:
            continue
        if tipo == "hidden":
            respuestas[var] = {"type": "hidden", "value": mapa.get("_valor")}
            continue
        if ctl is None:
            continue
        if tipo == "boolean":
            respuestas[var] = {"type": "boolean", "value": bool(ctl.GetValue())}
        elif tipo == "list-single":
            sel = ctl.GetSelection()
            respuestas[var] = {"type": tipo, "value": mapa.get(sel, "")}
        elif tipo in ("list-multi", "jid-multi"):
            respuestas[var] = {"type": tipo, "value": [mapa[i] for i in ctl.GetCheckedItems()]}
        else:
            respuestas[var] = {"type": tipo, "value": ctl.GetValue()}
    return respuestas


class XMPPFormularioDialog(wx.Dialog):
    """Formulario accesible de un comando ad-hoc, con navegación multi-etapa."""

    _ACCION_ETIQUETA = {
        "execute": _("&Ejecutar"),
        "next": _("&Siguiente"),
        "prev": _("&Anterior"),
        "complete": _("&Completar"),
        "cancel": _("&Cancelar comando"),
    }

    def __init__(self, parent, account_id, jid, node, resultado, titulo_ventana=None):
        self.account_id = account_id
        self.jid = jid
        self.node = node
        self.resultado = resultado
        titulo = titulo_ventana or resultado.get("titulo") or _("Comando del servidor")
        super().__init__(parent, title=titulo, size=(620, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Formulario de comando de XMPP"))
        self._controles = []  # [(var, tipo, control, mapa_opciones)]
        self.panel = wx.ScrolledWindow(self, style=wx.VSCROLL)
        self.panel.SetScrollRate(0, 10)
        self.raiz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.raiz)
        self.raiz.Add(self.panel, 1, wx.EXPAND | wx.ALL, 6)
        self.zona_botones = wx.BoxSizer(wx.HORIZONTAL)
        self.raiz.Add(self.zona_botones, 0, wx.EXPAND | wx.ALL, 6)
        self.CenterOnParent()
        self._pintar()

    # ---------------- pintado de una etapa ----------------
    def _pintar(self):
        self.panel.DestroyChildren()
        self._controles = []
        s = wx.BoxSizer(wx.VERTICAL)
        r = self.resultado

        if r.get("instrucciones"):
            s.Add(wx.StaticText(self.panel, label=r["instrucciones"]), 0, wx.ALL, 6)
        for tipo_nota, texto in r.get("notas", []):
            pref = {"error": _("Error: "), "warn": _("Aviso: ")}.get(tipo_nota, "")
            s.Add(wx.StaticText(self.panel, label=pref + texto), 0, wx.ALL, 4)

        if r.get("completado"):
            # Comando terminado: mostrar el resultado (campos como texto) y solo cerrar.
            if not r.get("notas") and not r.get("campos"):
                s.Add(wx.StaticText(self.panel, label=_("Comando completado.")), 0, wx.ALL, 6)
            for c in r.get("campos", []):
                etiqueta = c.get("label") or c.get("var") or ""
                valor = c.get("value")
                valor = ", ".join(valor) if isinstance(valor, (list, tuple)) else (valor or "")
                txt = f"{etiqueta}: {valor}" if etiqueta else str(valor)
                if txt.strip():
                    s.Add(wx.StaticText(self.panel, label=txt), 0, wx.ALL, 3)
        else:
            for c in r.get("campos", []):
                self._añadir_campo(s, c)

        self.panel.SetSizer(s)
        self.panel.Layout()
        self.panel.FitInside()
        self._pintar_botones()
        self.Layout()
        # Foco al primer control editable, o al primer botón.
        for _var, _t, ctl, _m in self._controles:
            if ctl is not None:
                ctl.SetFocus()
                break

    def _añadir_campo(self, s, c):
        entrada = pintar_campo(self.panel, s, c)
        if entrada is not None:
            self._controles.append(entrada)

    def _pintar_botones(self):
        self.zona_botones.Clear(delete_windows=True)
        r = self.resultado
        acciones = r.get("acciones", [])
        if not r.get("completado"):
            # Orden natural de lectura: anterior, siguiente, completar, ejecutar, cancelar.
            orden = ["prev", "next", "complete", "execute", "cancel"]
            mostradas = [a for a in orden if a in acciones]
            if not mostradas:
                # Sin acciones declaradas pero con formulario: ofrecer completar/ejecutar.
                mostradas = ["complete"] if r.get("sessionid") else ["execute"]
            for a in mostradas:
                b = wx.Button(self.panel.GetParent(), label=self._ACCION_ETIQUETA.get(a, a))
                b.Bind(wx.EVT_BUTTON, lambda e, ac=a: self._accion(ac))
                self.zona_botones.Add(b, 0, wx.ALL, 4)
        # Si el resultado trae un texto/enlace (típico del emparejamiento de una pasarela: WhatsApp),
        # ofrecer mostrarlo como código QR para escanearlo con el móvil.
        if self._texto_para_qr():
            bq = wx.Button(self, label=_("Mostrar como código &QR..."))
            bq.Bind(wx.EVT_BUTTON, lambda e: self._mostrar_qr())
            self.zona_botones.Add(bq, 0, wx.ALL, 4)
        self.zona_botones.AddStretchSpacer()
        self.zona_botones.Add(wx.Button(self, wx.ID_CANCEL, _("Ce&rrar")), 0, wx.ALL, 4)

    def _texto_para_qr(self):
        """Junta el texto de notas y campos fijos/valores del resultado (para un posible QR)."""
        r = self.resultado
        trozos = [tx for _tn, tx in r.get("notas", []) if tx]
        for c in r.get("campos", []):
            v = c.get("value")
            v = " ".join(v) if isinstance(v, (list, tuple)) else (v or "")
            etq = c.get("label") or ""
            if v:
                trozos.append(v)
            elif c.get("type") == "fixed" and etq:
                trozos.append(etq)
        texto = "\n".join(t for t in trozos if t).strip()
        return texto if len(texto) >= 8 else ""

    def _mostrar_qr(self):
        texto = self._texto_para_qr()
        if not texto:
            return
        from .qr_utils import mostrar_codigo_qr
        mostrar_codigo_qr(self, texto, titulo=_("Código QR de vinculación"),
                          ayuda=_("Si es un código de emparejamiento (por ejemplo de WhatsApp), "
                                  "escanéalo con el móvil desde Dispositivos vinculados."))

    # ---------------- recoger respuestas y avanzar ----------------
    def _recoger(self):
        return recoger_controles(self._controles)

    def _accion(self, action):
        acc, jid, node = self.account_id, self.jid, self.node
        sessionid = self.resultado.get("sessionid")
        respuestas = None if action == "cancel" else self._recoger()

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.ejecutar_comando(acc, jid, node, action=action,
                                                  sessionid=sessionid, respuestas=respuestas)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            self.resultado = res or {}
            for tn, tx in self.resultado.get("notas", []):
                if tn == "error":
                    notificar(_("El servidor devolvió un error: {0}").format(tx))
            if self.resultado.get("status") == "canceled":
                notificar(_("Comando cancelado."))
            self._pintar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class XMPPComandosDialog(wx.Dialog):
    """Lista los comandos ad-hoc que ofrece un JID y ejecuta el elegido."""

    def __init__(self, parent, account_id, jid=None, titulo=None):
        self.account_id = account_id
        self.jid = jid  # None = servidor de la cuenta
        self.comandos = []
        super().__init__(parent, title=titulo or _("Comandos del servidor"),
                         size=(560, 440), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Comandos ad-hoc de XMPP"))
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Comandos disponibles (Intro para ejecutar):"))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de comandos"))
        self.lista.InsertColumn(0, _("Comando"), width=460)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._ejecutar())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn = wx.Button(self, label=_("&Ejecutar"))
        btn.Bind(wx.EVT_BUTTON, lambda e: self._ejecutar())
        fila.Add(btn, 0, wx.ALL, 5)
        btn_otro = wx.Button(self, label=_("Comandos de &otro JID..."))
        btn_otro.Bind(wx.EVT_BUTTON, lambda e: self._otro_jid())
        fila.Add(btn_otro, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Ce&rrar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        acc, jid = self.account_id, self.jid
        speak(_("Consultando comandos..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.listar_comandos(acc, jid)

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron listar los comandos: {0}").format(err))
                return
            self.comandos = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, c in enumerate(self.comandos):
                self.lista.InsertItem(i, c["nombre"])
                self.lista.establecer_lectura(i, c["nombre"])
            self.lbl.SetLabel(_("{0} comandos (Intro para ejecutar):").format(len(self.comandos)))
            if self.comandos:
                self.lista.Focus(0)
                self.lista.Select(0)
            else:
                notificar(_("Este JID no ofrece comandos (o tu cuenta no tiene permiso)."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.comandos[i] if 0 <= i < len(self.comandos) else None

    def _ejecutar(self):
        c = self._actual()
        if not c:
            speak(_("Selecciona un comando."), interrupt=True)
            return
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.ejecutar_comando(acc, c["jid"], c["node"], action="execute")

        def _fin(res, err):
            if err:
                notificar(_("No se pudo ejecutar: {0}").format(err))
                return
            dlg = XMPPFormularioDialog(self, acc, c["jid"], c["node"], res or {},
                                       titulo_ventana=c["nombre"])
            dlg.ShowModal()
            dlg.Destroy()
            if (res or {}).get("completado"):
                pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _otro_jid(self):
        d = CustomTextEntryDialog(self, _("JID cuyo menú de comandos quieres ver "
                                          "(servidor, componente o contacto):"), _("Comandos de otro JID"))
        jid = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not jid:
            return
        self.jid = jid
        self._cargar()
