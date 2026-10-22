# -*- coding: utf-8 -*-
"""Diálogos accesibles de Telegram Drive: conflictos de nombre, propiedades y transferencias.

Todos usan controles accesibles del proyecto (etiquetas comprensibles, foco correcto) y no
tocan Telegram directamente: hablan con el servicio de la nube. Las operaciones de red se
lanzan en segundo plano desde la ventana principal, no aquí.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, AccessibleCheckListBox,
                                   mostrar_info, confirmar, habilitar_escape_cierre, elegir_opcion)

# Decisiones posibles ante un conflicto de nombre.
CONF_REEMPLAZAR = "reemplazar"
CONF_AMBOS = "ambos"
CONF_OMITIR = "omitir"


class ConflictoDialog(wx.Dialog):
    """Pregunta qué hacer cuando ya existe un elemento con el mismo nombre. Nunca sobrescribe
    en silencio. Permite aplicar la misma decisión a todos los conflictos siguientes."""

    def __init__(self, parent, nombre: str):
        super().__init__(parent, title=_("Ya existe ese nombre"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Conflicto de nombre"))
        self.decision = None
        self.a_todos = False
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "Ya existe «{0}» en esta carpeta. ¿Qué quieres hacer?").format(nombre)), 0, wx.ALL, 10)
        self.cb_todos = wx.CheckBox(self, label=_("Aplicar esta decisión a todos los conflictos"),
                                    name=_("Aplicar a todos"))
        s.Add(self.cb_todos, 0, wx.ALL, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etq, dec, nombre_b in (
                (_("&Reemplazar"), CONF_REEMPLAZAR, _("Reemplazar el existente")),
                (_("Conservar &ambos"), CONF_AMBOS, _("Conservar ambos con nombres distintos")),
                (_("&Omitir"), CONF_OMITIR, _("Omitir este elemento"))):
            b = wx.Button(self, label=etq, name=nombre_b)
            b.Bind(wx.EVT_BUTTON, lambda e, d=dec: self._elegir(d))
            fila.Add(b, 0, wx.ALL, 4)
        b_cancel = wx.Button(self, wx.ID_CANCEL, _("&Cancelar todo"))
        fila.Add(b_cancel, 0, wx.ALL, 4)
        s.Add(fila, 0, wx.ALIGN_CENTER)
        self.SetSizerAndFit(s)
        self.CenterOnParent()
        try:
            habilitar_escape_cierre(self)
        except Exception:
            pass

    def _elegir(self, decision):
        self.decision = decision
        self.a_todos = self.cb_todos.GetValue()
        self.EndModal(wx.ID_OK)


class EntradaItemDialog(wx.Dialog):
    """Diálogo accesible para pedir un nombre (opcional) y una descripción (opcional). Se usa al
    crear carpetas (nombre + descripción), al subir (solo descripción) y al editar la descripción.
    El nombre, si se pide, es obligatorio; la descripción siempre es opcional."""

    def __init__(self, parent, titulo, pedir_nombre=True, nombre="", descripcion="",
                 etiqueta_desc=None):
        super().__init__(parent, title=titulo, style=wx.DEFAULT_DIALOG_STYLE, name=titulo,
                         size=(460, 300))
        self._pedir_nombre = pedir_nombre
        s = wx.BoxSizer(wx.VERTICAL)
        self.txt_nombre = None
        if pedir_nombre:
            s.Add(wx.StaticText(self, label=_("Nombre:")), 0, wx.ALL, 6)
            self.txt_nombre = AccessibleTextCtrl(self, value=nombre, name=_("Nombre"))
            s.Add(self.txt_nombre, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=etiqueta_desc or _("Descripción (opcional):")), 0, wx.ALL, 6)
        self.txt_desc = AccessibleTextCtrl(self, value=descripcion,
                                           style=wx.TE_MULTILINE, name=_("Descripción"))
        s.Add(self.txt_desc, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        (self.txt_nombre or self.txt_desc).SetFocus()
        try:
            habilitar_escape_cierre(self)
        except Exception:
            pass

    @property
    def nombre(self):
        return self.txt_nombre.GetValue().strip() if self.txt_nombre else ""

    @property
    def descripcion(self):
        return self.txt_desc.GetValue().strip()


class PropiedadesDialog(wx.Dialog):
    """Muestra TODAS las propiedades de un elemento (metadatos de la nube), en un cuadro de
    solo lectura recorrible con el lector de pantalla."""

    def __init__(self, parent, item, partes):
        super().__init__(parent, title=_("Propiedades"), size=(560, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Propiedades del elemento"))
        s = wx.BoxSizer(wx.VERTICAL)
        txt = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                 name=_("Propiedades"))
        txt.SetValue(self._texto(item, partes))
        s.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(self.CreateButtonSizer(wx.OK), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        try:
            habilitar_escape_cierre(self, cerrar_id=wx.ID_OK)
        except Exception:
            pass

    @staticmethod
    def _tam(n):
        n = float(n or 0)
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or u == "TB":
                return f"{int(n)} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return ""

    def _texto(self, it, partes):
        tipo = _("Carpeta") if it.es_carpeta else _("Archivo")
        lineas = [
            _("Nombre: {0}").format(it.name),
            _("Tipo: {0}").format(tipo),
            _("Ruta: {0}").format(it.path),
            _("Descripción: {0}").format(it.descripcion or _("(ninguna)")),
            _("Estado: {0}").format(it.estado),
        ]
        if it.es_archivo:
            lineas += [
                _("Tamaño: {0}").format(self._tam(it.size)),
                _("Tipo de contenido: {0}").format(it.mime or _("(desconocido)")),
                _("Partes: {0}").format(it.num_parts),
                _("Cifrado: {0}").format(_("sí") if it.encrypted else _("no")),
                _("SHA-256: {0}").format(it.sha256 or ""),
            ]
        lineas += [
            _("Creado: {0}").format(it.created_at),
            _("Modificado: {0}").format(it.modified_at),
            _("Identificador interno: {0}").format(it.id),
            _("Canal: {0}").format(it.channel_id or ""),
            _("Versión de metadatos: {0}").format(it.meta_version),
        ]
        if partes:
            lineas.append("")
            lineas.append(_("Detalle de las partes:"))
            for p in partes:
                lineas.append(_("  Parte {0}: {1}, mensaje {2}").format(
                    p.orden, self._tam(p.size), p.message_id))
        return "\n".join(lineas)


class PurgarCanalDialog(wx.Dialog):
    """AVANZADO. Lista TODOS los documentos del canal con casillas para marcarlos y borrarlos
    DIRECTAMENTE del canal (sin pasar por la papelera). Para quien sabe lo que hace: sirve para
    purgar restos (índices json acumulados, archivos sueltos, partes colgadas...). Si no sabes lo
    que haces, cierra este diálogo."""

    def __init__(self, parent, servicio, account_id, docs):
        super().__init__(parent, title=_("Purga avanzada del canal"), size=(720, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Purga avanzada del canal"))
        self.servicio = servicio
        self.account_id = account_id
        self._docs = list(docs)
        # El manifiesto vigente (el de mayor message_id) se resalta para no borrarlo por error.
        manifiestos = [d["message_id"] for d in self._docs if d.get("es_manifiesto") and d.get("message_id")]
        self._manifiesto_vigente = max(manifiestos) if manifiestos else None
        s = wx.BoxSizer(wx.VERTICAL)
        aviso = wx.StaticText(self, label=_(
            "ATENCIÓN: diálogo avanzado. Lo que marques aquí se BORRA DIRECTAMENTE del canal, "
            "SIN pasar por la papelera y sin posible recuperación. Es para purgar restos "
            "(índices json de sobra, archivos sueltos, partes colgadas). Si no sabes lo que "
            "haces, cierra esta ventana."))
        aviso.Wrap(680)
        f = aviso.GetFont()
        f.SetWeight(wx.FONTWEIGHT_BOLD)
        aviso.SetFont(f)
        s.Add(aviso, 0, wx.ALL, 8)
        self.clb = AccessibleCheckListBox(self, choices=self._etiquetas(),
                                          name=_("Documentos del canal"))
        s.Add(self.clb, 1, wx.EXPAND | wx.ALL, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etq, hnd, nombre in (
                (_("Marcar los &sueltos y los índices viejos"), self._on_marcar_sueltos,
                 _("Marcar lo que no pertenece a ningún archivo de tu nube y los índices sobrantes")),
                (_("&Desmarcar todo"), self._on_desmarcar, _("Quitar todas las marcas")),
                (_("&Eliminar del canal lo marcado"), self._on_eliminar,
                 _("Borrar del canal, sin papelera, los documentos marcados"))):
            b = wx.Button(self, label=etq, name=nombre)
            b.Bind(wx.EVT_BUTTON, hnd)
            fila.Add(b, 0, wx.ALL, 3)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 3)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)
        self.CenterOnParent()
        if self._docs:
            self.clb.SetSelection(0)
        self.clb.SetFocus()
        try:
            habilitar_escape_cierre(self)
        except Exception:
            pass

    def _etiquetas(self):
        et = []
        for d in self._docs:
            nombre = d.get("file_name") or _("(sin nombre)")
            if d.get("es_manifiesto"):
                if d.get("message_id") == self._manifiesto_vigente:
                    marca = _("índice ACTUAL (mejor no borrar)")
                else:
                    marca = _("índice viejo (sobra)")
            elif d.get("referenciado"):
                marca = _("archivo de tu nube: {0}").format(d.get("item") or "")
            else:
                marca = _("SUELTO (no está en tu nube)")
            et.append(_("{0} — {1}  ·  mensaje {2}").format(nombre, marca, d.get("message_id")))
        return et

    def _on_marcar_sueltos(self, event):
        """Marca lo que NO pertenece a ningún archivo de tu nube, y los índices viejos; deja sin
        marcar el índice vigente y los archivos válidos."""
        n = 0
        for i, d in enumerate(self._docs):
            sobra = (not d.get("referenciado")) and (d.get("message_id") != self._manifiesto_vigente)
            self.clb.Check(i, bool(sobra))
            if sobra:
                n += 1
        speak(_("{0} marcados.").format(n), interrupt=True)

    def _on_desmarcar(self, event):
        for i in range(self.clb.GetCount()):
            self.clb.Check(i, False)
        speak(_("Nada marcado."), interrupt=True)

    def _on_eliminar(self, event):
        marcados = list(self.clb.GetCheckedItems())
        if not marcados:
            notificar(_("No has marcado nada."))
            return
        ids = [self._docs[i]["message_id"] for i in marcados if self._docs[i].get("message_id")]
        if confirmar(self, _("Vas a BORRAR del canal {0} documentos, SIN papelera y sin poder "
                             "recuperarlos. ¿Seguro?").format(len(ids)),
                     _("Purga avanzada del canal")) != wx.ID_YES:
            return
        speak(_("Borrando del canal..."), interrupt=True)
        idset = set(ids)

        def _bg(ctx):
            return self.servicio.purgar_mensajes_canal(self.account_id, ids)

        def _fin(res, err):
            if err:
                mostrar_info(self, _("Error al purgar: {0}").format(err), _("Purga avanzada del canal"))
                return
            # Quitar de la lista local lo ya borrado y refrescar las casillas.
            self._docs = [d for d in self._docs if d.get("message_id") not in idset]
            self.clb.Set(self._etiquetas())
            if self._docs:
                self.clb.SetSelection(0)
            notificar(_("{0} documentos borrados del canal.").format(res or 0))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class TransferenciasDialog(wx.Dialog):
    """Lista de transferencias (subidas y descargas) con progreso en vivo. Permite cancelar,
    reintentar y quitar de la lista. Se refresca solo sin mover el foco."""

    def __init__(self, parent, servicio, account_id):
        super().__init__(parent, title=_("Transferencias"), size=(720, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Transferencias de Telegram Drive"))
        self.servicio = servicio
        self.account_id = account_id
        self._items = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de transferencias"))
        self.lista.InsertColumn(0, _("Nombre"), width=280)
        self.lista.InsertColumn(1, _("Operación"), width=110)
        self.lista.InsertColumn(2, _("Estado"), width=130)
        self.lista.InsertColumn(3, _("Progreso"), width=90)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etq, hnd, nombre in (
                (_("&Cancelar"), self._on_cancelar, _("Cancelar la transferencia")),
                (_("R&eintentar"), self._on_reintentar, _("Reintentar la transferencia")),
                (_("&Quitar de la lista"), self._on_quitar, _("Quitar la transferencia terminada")),
                (_("&Limpiar terminadas..."), self._on_limpiar,
                 _("Quitar en lote las transferencias completadas, fallidas o canceladas"))):
            b = wx.Button(self, label=etq, name=nombre)
            b.Bind(wx.EVT_BUTTON, hnd)
            fila.Add(b, 0, wx.ALL, 3)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 3)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self._cargar(), self._timer)
        self._timer.Start(1000)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        try:
            habilitar_escape_cierre(self)
        except Exception:
            pass

    def _on_close(self, event):
        try:
            self._timer.Stop()
        except Exception:
            pass
        event.Skip()

    _ESTADOS = None

    def _estado_legible(self, clave):
        mapa = {
            "pendiente": _("Pendiente"), "preparando": _("Preparando"),
            "calculando_hash": _("Calculando hash"), "subiendo": _("Subiendo"),
            "descargando": _("Descargando"), "reconstruyendo": _("Reconstruyendo"),
            "verificando": _("Verificando"), "pausada": _("Pausada"),
            "cancelando": _("Cancelando"), "cancelada": _("Cancelada"),
            "completada": _("Completada"), "fallida": _("Fallida"),
        }
        return mapa.get(clave, clave)

    def _celdas(self, t):
        """Devuelve (op, estado, progreso) legibles de una transferencia."""
        op = _("Subida") if t["operacion"] == "upload" else _("Descarga")
        estado = self._estado_legible(t["estado"])
        if t["estado"] == "fallida" and t["error"]:
            estado = _("Fallida: {0}").format(t["error"][:40])
        prog = f"{t['porcentaje']}%" if t["estado"] in ("subiendo", "descargando") else ""
        return op, estado, prog

    def _cargar(self):
        """Refresca la lista. Si el CONJUNTO de transferencias no cambió (mismo orden e ids), solo
        actualiza en el sitio las celdas que varían (estado y progreso) SIN mover el foco: así el
        lector no relee la fila cada segundo mientras sube/baja. Solo reconstruye del todo cuando
        cambian los ids (aparece/termina/desaparece una transferencia)."""
        if not self:
            return
        nuevos = self.servicio.obtener_transferencias(self.account_id)
        ids_nuevos = [t["id"] for t in nuevos]
        ids_viejos = [t["id"] for t in self._items]
        if ids_nuevos == ids_viejos and self.lista.GetItemCount() == len(nuevos):
            # Mismo conjunto: actualizar solo estado y progreso, sin tocar el foco.
            self._items = nuevos
            for i, t in enumerate(nuevos):
                op, estado, prog = self._celdas(t)
                self.lista.SetItem(i, 2, estado)
                self.lista.SetItem(i, 3, prog)
                self.lista.establecer_lectura(i, "{0}. {1}. {2}. {3}".format(
                    t["nombre"] or "", op, estado, prog))
            return
        # El conjunto cambió: reconstruir conservando el foco por id.
        foco_id = None
        idx = self.lista.GetFocusedItem()
        if 0 <= idx < len(self._items):
            foco_id = self._items[idx]["id"]
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self._items = nuevos
        nuevo_foco = 0
        for i, t in enumerate(self._items):
            op, estado, prog = self._celdas(t)
            pos = self.lista.InsertItem(i, t["nombre"] or "")
            self.lista.SetItem(pos, 1, op)
            self.lista.SetItem(pos, 2, estado)
            self.lista.SetItem(pos, 3, prog)
            self.lista.establecer_lectura(pos, "{0}. {1}. {2}. {3}".format(
                t["nombre"] or "", op, estado, prog))
            if t["id"] == foco_id:
                nuevo_foco = i
        if self._items:
            self.lista.Focus(nuevo_foco)
            self.lista.Select(nuevo_foco)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def _on_cancelar(self, event):
        t = self._sel()
        if t:
            self.servicio.cancelar_transferencia(t["id"])
            self._cargar()

    def _on_reintentar(self, event):
        t = self._sel()
        if t:
            self.servicio.reintentar_transferencia(t["id"])
            self._cargar()

    def _on_quitar(self, event):
        t = self._sel()
        if t:
            self.servicio.quitar_transferencia(t["id"])
            self._cargar()

    def _on_limpiar(self, event):
        """Quita EN LOTE las transferencias terminadas (completadas, fallidas o canceladas), sin
        tocar las que están en curso. El usuario elige qué grupo con botones accesibles."""
        opciones = [
            (_("Solo las &completadas"), ("completada",)),
            (_("Solo las &fallidas"), ("fallida",)),
            (_("Solo las c&anceladas"), ("cancelada",)),
            (_("&Todas las terminadas"), ("completada", "fallida", "cancelada")),
        ]
        idx = elegir_opcion(self, _("¿Qué transferencias terminadas quieres quitar de la lista?"),
                            _("Limpiar transferencias"), [o[0] for o in opciones])
        if idx is None:
            return
        estados = opciones[idx][1]
        n = self.servicio.limpiar_transferencias(self.account_id, estados)
        notificar(_("{0} transferencias quitadas de la lista.").format(n) if n
                  else _("No había transferencias terminadas que quitar."))
        self._cargar()
