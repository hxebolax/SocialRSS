# -*- coding: utf-8 -*-
"""Ventana del explorador de Telegram Drive: la nube personal de SocialRSS.

Interfaz plenamente accesible (wxPython) para navegar por la jerarquía virtual, subir y
descargar, crear/renombrar/mover/copiar/eliminar y gestionar la papelera y las
transferencias. Las operaciones de red NUNCA bloquean la interfaz: se encolan en el
servicio, que trabaja en segundo plano y avisa con wx.CallAfter. Todos los atajos tienen
también su entrada de menú (el usuario no está obligado a conocerlos).
"""
import os
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import (AccessibleListCtrl, CustomTextEntryDialog, mostrar_info,
                                   confirmar)
from .tgdrive_dialogs import (ConflictoDialog, PropiedadesDialog, TransferenciasDialog,
                              EntradaItemDialog, PurgarCanalDialog, CONF_AMBOS, CONF_OMITIR)

from app.services.tgdrive.tgdrive_service import tgdrive_service


class TelegramDriveFrame(wx.Frame):
    """Ventana independiente (no modal) para que SocialRSS siga usándose durante las
    transferencias. Una por cuenta de Telegram."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Telegram Drive"), size=(900, 600),
                         name=_("Telegram Drive"))
        self.account_id = account_id
        self.svc = tgdrive_service
        self.repo = tgdrive_service.repo
        # Estado de navegación.
        self._carpeta_actual = None          # UUID de la carpeta (None = raíz)
        self._historial = [None]
        self._hist_pos = 0
        self._modo = "normal"                # normal | trash | search
        self._busqueda = ""
        self._items = []
        self._portapapeles = None            # (accion, [item_ids])
        self._marcados = set()               # ids de elementos marcados con la casilla (Espacio)
        self._plantilla_actual = None        # cadena de plantilla con la que se pintaron las columnas
        self._panel = wx.Panel(self)
        self._construir_ui()
        self._crear_menu()
        self._registrar_atajos()
        self.svc.set_callback_ui(self._on_cambio_servicio)
        # Reanudar lo pendiente de sesiones anteriores (si el origen sigue existiendo) y avisar.
        try:
            _res = self.svc.reanudar_pendientes() or {}
        except Exception:
            _res = {}
        if _res.get("pendientes"):
            wx.CallAfter(notificar, _("Reanudando {0} transferencias pendientes.").format(_res["pendientes"]))
        if _res.get("huerfanas"):
            wx.CallAfter(notificar, _("{0} subidas no se pueden reanudar: su archivo de origen ya no existe.").format(_res["huerfanas"]))
        self.CenterOnScreen()
        # Comprobar canal al abrir (en segundo plano para no bloquear).
        wx.CallAfter(self._asegurar_canal)

    # Clave de la plantilla de lectura/columnas del Drive (editable en el editor de plantillas).
    _CLAVE_PLANTILLA = "tgdrive_items"
    # Ancho por defecto de cada columna según el campo.
    _ANCHOS = {"marcado": 80, "nombre": 320, "tipo": 90, "tam": 90, "modificado": 150,
               "descripcion": 240, "estado": 110}

    # ------------------------------------------------------------- construcción
    def _construir_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl_ruta = wx.StaticText(self._panel, label=_("Ubicación: /"))
        s.Add(self.lbl_ruta, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(
            self._panel, style=wx.LC_REPORT | wx.BORDER_SUNKEN,
            name=_("Contenido de Telegram Drive"))
        # Las COLUMNAS (y su orden) salen de la plantilla configurable "Telegram Drive (nube)".
        self._cols = self._crear_columnas()
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._abrir())
        self.lista.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._menu_contextual)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._menu_contextual)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        self._panel.SetSizer(s)
        # Barra de estado real (se lee con Ctrl+Mayús+B). El texto útil lo pone _actualizar_estado.
        self.CreateStatusBar(1)
        self._texto_estado = ""

    def _plantilla_str(self):
        """Cadena de plantilla activa (la que el usuario configuró, o la de por defecto)."""
        from app.core.config_manager import config
        from app.core.plantillas import definicion
        d = definicion(self._CLAVE_PLANTILLA)
        return (config.get("accessibility", self._CLAVE_PLANTILLA, "") or (d["default"] if d else "")).strip()

    def _crear_columnas(self):
        """(Re)crea las columnas de la lista a partir de la plantilla. Devuelve [(campo, encabezado)]."""
        from app.core.plantillas import columnas_de_plantilla, definicion
        plantilla = self._plantilla_str()
        cols = columnas_de_plantilla(plantilla)
        if not cols:
            d = definicion(self._CLAVE_PLANTILLA)
            cols = columnas_de_plantilla(d["default"]) if d else [("nombre", _("Nombre"))]
        while self.lista.GetColumnCount() > 0:
            self.lista.DeleteColumn(0)
        for i, (campo, encabezado) in enumerate(cols):
            self.lista.InsertColumn(i, encabezado, width=self._ANCHOS.get(campo, 130))
        self._plantilla_actual = plantilla
        return cols

    def _datos_item(self, it):
        """Diccionario campo -> valor para una fila, con TODOS los campos que puede usar la
        plantilla (el que no aparezca en la plantilla, simplemente no se muestra ni se lee)."""
        estados = {"normal": "", "trash": _("En la papelera"), "incomplete": _("Subiendo"),
                   "deleted": _("Eliminándose")}
        return {
            # Valor CLARO en la columna Marcado: al navegar por columnas se lee "Marcado, Sí" o
            # "Marcado, No" (antes, vacío = solo se oía el encabezado y no se sabía el estado).
            "marcado": _("Sí") if it.id in self._marcados else _("No"),
            "nombre": it.name,
            "tipo": _("Carpeta") if it.es_carpeta else _("Archivo"),
            "tam": "" if it.es_carpeta else self._tam(it.size),
            "modificado": (it.modified_at or "")[:16].replace("T", " "),
            "descripcion": it.descripcion or "",
            "estado": estados.get(it.estado, it.estado),
        }

    # Atajos con CONTROL: van por la tabla de aceleradores (funcionan con cualquier foco). wx no
    # interpreta nombres de tecla en español tras "\t", así que se enlazan por id y la etiqueta del
    # menú solo muestra el hint en español entre paréntesis. Las teclas SUELTAS o con Alt/Mayús
    # (Retroceso, Supr, Alt+flechas, Alt+Intro) NO van aquí: el ListCtrl se las come antes de que
    # el acelerador actúe; se gestionan en _on_char_hook (que recibe la tecla antes que el control).
    _ATAJOS = {
        "subir_arch": (wx.ACCEL_CTRL, ord("U")),
        "subir_carp": (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("U")),
        "descargar": (wx.ACCEL_CTRL, ord("D")),
        "nueva_carp": (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("N")),
        "cerrar": (wx.ACCEL_CTRL, ord("W")),
        "renombrar": (wx.ACCEL_CTRL, wx.WXK_F2),
        "cortar": (wx.ACCEL_CTRL, ord("X")),
        "copiar": (wx.ACCEL_CTRL, ord("C")),
        "pegar": (wx.ACCEL_CTRL, ord("V")),
        "sel_todo": (wx.ACCEL_CTRL, ord("A")),
        "refrescar": (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("R")),
        "buscar": (wx.ACCEL_CTRL, ord("F")),
        "leer_estado": (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("B")),
    }

    def _crear_menu(self):
        self._ids = {}
        barra = wx.MenuBar()
        m_archivo = wx.Menu()
        self._add(m_archivo, _("Subir archivos... (Ctrl+U)"), self._subir_archivos, "subir_arch")
        self._add(m_archivo, _("Subir una carpeta... (Ctrl+Mayús+U)"), self._subir_carpeta, "subir_carp")
        self._add(m_archivo, _("Descargar selección (Ctrl+D)"), self._descargar, "descargar")
        self._add(m_archivo, _("Nueva carpeta... (Ctrl+Mayús+N)"), self._nueva_carpeta, "nueva_carp")
        m_archivo.AppendSeparator()
        self._add(m_archivo, _("Abrir la carpeta de descargas"), self._abrir_carpeta_descargas)
        self._add(m_archivo, _("Propiedades (Alt+Intro)"), self._propiedades, "propiedades")
        m_archivo.AppendSeparator()
        self._add(m_archivo, _("Cerrar (Ctrl+W)"), lambda e: self.Close(), "cerrar")
        barra.Append(m_archivo, _("&Archivo"))

        m_editar = wx.Menu()
        self._add(m_editar, _("Cambiar el nombre... (Ctrl+F2)"), self._renombrar, "renombrar")
        self._add(m_editar, _("Cambiar la descripción..."), self._cambiar_descripcion)
        self._add(m_editar, _("Cortar (Ctrl+X)"), self._cortar, "cortar")
        self._add(m_editar, _("Copiar (Ctrl+C)"), self._copiar, "copiar")
        self._add(m_editar, _("Pegar (Ctrl+V)"), self._pegar, "pegar")
        self._add(m_editar, _("Marcar todo (Ctrl+A)"), self._seleccionar_todo, "sel_todo")
        self._add(m_editar, _("Desmarcar todo"), self._desmarcar_todo)
        m_editar.AppendSeparator()
        self._add(m_editar, _("Enviar a la papelera (Supr)"), self._a_papelera, "papelera")
        self._add(m_editar, _("Eliminar definitivamente (Mayús+Supr)"), self._eliminar_definitivo, "eliminar")
        barra.Append(m_editar, _("&Editar"))

        m_ver = wx.Menu()
        self._add(m_ver, _("Actualizar (Ctrl+Mayús+R)"), self._refrescar, "refrescar")
        self._add(m_ver, _("Buscar... (Ctrl+F)"), self._buscar, "buscar")
        self._add(m_ver, _("Subir a la carpeta anterior (Retroceso)"), self._subir_nivel, "subir_nivel")
        self._add(m_ver, _("Ir atrás (Alt+Izquierda)"), self._atras, "atras")
        self._add(m_ver, _("Ir adelante (Alt+Derecha)"), self._adelante, "adelante")
        m_ver.AppendSeparator()
        self._add(m_ver, _("Papelera"), self._ver_papelera)
        self._add(m_ver, _("Ver la carpeta normal"), self._ver_normal)
        self._add(m_ver, _("Transferencias..."), self._abrir_transferencias)
        m_ver.AppendSeparator()
        self._add(m_ver, _("Leer la barra de estado (Ctrl+Mayús+B)"), self._leer_barra_estado, "leer_estado")
        barra.Append(m_ver, _("&Ver"))

        m_papelera = wx.Menu()
        self._add(m_papelera, _("Restaurar seleccionado"), self._restaurar)
        self._add(m_papelera, _("Vaciar la papelera"), self._vaciar_papelera)
        barra.Append(m_papelera, _("&Papelera"))

        m_nube = wx.Menu()
        self._add(m_nube, _("Revisar y reparar el canal..."), self._revisar_canal)
        self._add(m_nube, _("Listar TODO el canal (purga avanzada)..."), self._purgar_canal)
        self._add(m_nube, _("Reconstruir el índice desde el canal..."), self._reconstruir_indice)
        self._add(m_nube, _("Exportar el índice al canal"), self._exportar_indice)
        self._add(m_nube, _("Reintentar eliminaciones pendientes"), self._reintentar_eliminaciones)
        m_nube.AppendSeparator()
        self._add(m_nube, _("Eliminar Telegram Drive por completo..."), self._eliminar_drive)
        barra.Append(m_nube, _("&Nube"))
        self.SetMenuBar(barra)

    def _add(self, menu, etiqueta, handler, clave=None):
        """Añade un ítem de menú y lo enlaza. Si `clave` está en _ATAJOS, reutiliza su id para
        que la tabla de aceleradores dispare el mismo handler. Todos los handlers aceptan un
        argumento de evento (métodos con `event=None` o lambdas `lambda e: ...`)."""
        item_id = wx.ID_ANY
        if clave is not None:
            ref = wx.NewIdRef()
            self._ids[clave] = int(ref)
            item_id = ref
        it = menu.Append(item_id, etiqueta)
        self.Bind(wx.EVT_MENU, lambda e: handler(e), it)
        return it

    def _registrar_atajos(self):
        """Construye la tabla de aceleradores a partir de _ATAJOS (funciona con cualquier foco
        dentro de la ventana, no solo sobre la lista)."""
        entradas = []
        for clave, (flags, code) in self._ATAJOS.items():
            if clave in self._ids:
                entradas.append(wx.AcceleratorEntry(flags, code, self._ids[clave]))
        self.SetAcceleratorTable(wx.AcceleratorTable(entradas))
        # Teclas SUELTAS o con Alt/Mayús: aquí, porque el ListCtrl se las quedaría antes que la
        # tabla de aceleradores (por eso Retroceso no subía de nivel). El char hook recibe la
        # tecla antes que el control con foco.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def _on_char_hook(self, event):
        code = event.GetKeyCode()
        mod = event.GetModifiers()
        if code == wx.WXK_ESCAPE:
            self.Close()
        elif code == wx.WXK_SPACE and mod == wx.MOD_NONE and self.FindFocus() is self.lista:
            # El AccessibleListCtrl se come el Espacio; lo interceptamos aquí para marcar.
            self._toggle_marca()
        elif code == wx.WXK_BACK and mod == wx.MOD_NONE:
            self._subir_nivel()
        elif code == wx.WXK_LEFT and mod == wx.MOD_ALT:
            self._atras()
        elif code == wx.WXK_RIGHT and mod == wx.MOD_ALT:
            self._adelante()
        elif code == wx.WXK_DELETE and mod == wx.MOD_SHIFT:
            self._eliminar_definitivo()
        elif code == wx.WXK_DELETE and mod == wx.MOD_NONE:
            self._a_papelera()
        elif code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and mod == wx.MOD_ALT:
            self._propiedades()
        else:
            event.Skip()

    # ------------------------------------------------------------- canal
    def _asegurar_canal(self):
        """Si la cuenta no tiene canal de almacenamiento, ofrece crearlo o reconstruir el
        índice desde un canal existente."""
        if self.svc.obtener_channel_id(self.account_id) is not None:
            self._refrescar()
            self._mantenimiento_apertura()
            return
        opciones = [_("Crear un canal privado nuevo para la nube"),
                    _("Ya tengo un canal: reconstruir el índice desde él"),
                    _("Ahora no")]
        dlg = wx.SingleChoiceDialog(self, _(
            "Telegram Drive necesita un canal privado donde guardar tus archivos. Solo tú "
            "tendrás acceso (no aparece en búsquedas). ¿Qué quieres hacer?"),
            _("Configurar Telegram Drive"), opciones)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        sel = dlg.GetSelection()
        dlg.Destroy()
        if sel == 0:
            self._crear_canal()
        elif sel == 1:
            self._reconstruir_indice()

    def _crear_canal(self):
        speak(_("Creando el canal privado..."), interrupt=True)

        def _bg(ctx):
            return self.svc.crear_canal(self.account_id)

        def _fin(cid, err):
            if err:
                notificar(_("No se pudo crear el canal: {0}").format(err))
            else:
                notificar(_("Canal privado creado. Ya puedes subir archivos."))
                self._refrescar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _mantenimiento_apertura(self):
        """Tareas silenciosas al abrir con canal ya configurado (en segundo plano, best-effort):
        aplicar la retención de la papelera y reintentar las eliminaciones que quedaron pendientes
        tras una desconexión. No molesta al usuario salvo que purgue algo."""
        def _bg(ctx):
            purgados = self.svc.purgar_papelera_caducada(self.account_id)
            self.svc.reintentar_eliminaciones(self.account_id)
            return purgados

        def _fin(purgados, err):
            if not err and purgados:
                notificar(_("Se vaciaron {0} elementos caducados de la papelera.").format(purgados))
                if self._modo in ("trash", "normal"):
                    self._refrescar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------- listado
    def _refrescar(self, event=None, silencioso=False):
        """Recarga la vista actual (normal, papelera o búsqueda) sin mover el foco al azar. Si
        `silencioso`, la recolocación del foco no la anuncia el lector (para los refrescos de
        FONDO cuando termina una transferencia: la novedad entra sin releerte el elemento)."""
        foco_id = self._id_enfocado()
        # Si el usuario cambió la plantilla, rehacer las columnas.
        if self._plantilla_str() != self._plantilla_actual:
            self._cols = self._crear_columnas()
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        if self._modo == "trash":
            self._items = self.repo.listar_papelera(self.account_id)
            self._ubicacion = _("Papelera")
        elif self._modo == "search":
            self._items = self.repo.buscar(self.account_id, self._busqueda)
            self._ubicacion = _("Búsqueda: {0}").format(self._busqueda)
        else:
            self._items = self.repo.listar(self.account_id, self._carpeta_actual)
            self._ubicacion = self._ruta_actual()
        self.lbl_ruta.SetLabel(_("Ubicación: {0}").format(self._ubicacion))
        # El TÍTULO de la ventana refleja la ruta/vista actual (al navegar, no por pulsación).
        self.SetTitle(_("Telegram Drive — {0}").format(self._ubicacion))
        nuevo_foco = 0
        for i, it in enumerate(self._items):
            self._pintar_fila(i, it)
            if it.id == foco_id:
                nuevo_foco = i
        if self._items:
            self._enfocar_fila(nuevo_foco, silencioso)
        self._actualizar_estado()

    def _enfocar_fila(self, idx, silencioso=False):
        """Coloca foco+selección en una fila. Si `silencioso`, usa la marca de ventana que hace
        que el complemento de NVDA NO relea el elemento (misma técnica que la lista principal)."""
        if not silencioso:
            self.lista.Focus(idx)
            self.lista.Select(idx)
            return
        try:
            self.lista._silencioso = True
            self.lista._marcar_movimiento_silencioso(True)
            self.lista.Focus(idx)
            self.lista.Select(idx)
            wx.CallAfter(lambda: setattr(self.lista, "_silencioso", False))
            wx.CallLater(150, lambda: self.lista._marcar_movimiento_silencioso(False))
        except Exception:
            self.lista.Focus(idx)
            self.lista.Select(idx)

    def _pintar_fila(self, i, it):
        """Pinta una fila usando las columnas de la plantilla y fija la lectura con la propia
        plantilla (así lo que se lee y en qué orden es lo que el usuario configuró)."""
        from app.core.plantillas import pulir_lectura
        from collections import defaultdict
        datos = self._datos_item(it)
        pos = self.lista.InsertItem(i, datos.get(self._cols[0][0], "") if self._cols else it.name)
        for col, (campo, _enc) in enumerate(self._cols):
            if col == 0:
                continue
            self.lista.SetItem(pos, col, datos.get(campo, ""))
        try:
            lectura = pulir_lectura(self._plantilla_str().format_map(defaultdict(str, datos)))
        except Exception:
            lectura = it.name
        self.lista.establecer_lectura(pos, lectura)

    def _actualizar_estado(self):
        """Compone la barra de estado con información ÚTIL y la guarda para poder leerla con
        Ctrl+Mayús+B: ubicación, elementos a la vista, marcados, transferencias en curso y el
        tamaño total de la nube."""
        try:
            partes = [_("Ubicación: {0}").format(getattr(self, "_ubicacion", "/"))]
            partes.append(_("{0} elementos a la vista").format(len(self._items)))
            marcados = self._contar_marcados()
            if marcados:
                partes.append(_("{0} marcados").format(marcados))
            activas = sum(1 for t in self.svc.obtener_transferencias(self.account_id)
                          if t["estado"] in ("pendiente", "subiendo", "descargando"))
            if activas:
                partes.append(_("{0} transferencias en curso").format(activas))
            c = self.repo.contar(self.account_id)
            partes.append(_("nube: {0} carpetas, {1} archivos, {2}").format(
                c["carpetas"], c["archivos"], self._tam(c["bytes"])))
            self._texto_estado = " · ".join(partes)
            self.SetStatusText(self._texto_estado)
        except Exception:
            pass

    def _contar_marcados(self):
        return sum(1 for it in self._items if it.id in self._marcados)

    def _leer_barra_estado(self, event=None):
        """Verbaliza la barra de estado (Ctrl+Mayús+B), como en la ventana principal."""
        speak(self._texto_estado or _("La barra de estado está vacía."), interrupt=True)

    # ------------------------------------------------------------- marcado (casillas)
    def _toggle_marca(self, event=None):
        """Marca o desmarca el elemento con foco (barra espaciadora). El AccessibleListCtrl se
        come el Espacio, así que esto se llama desde el char hook, ANTES que la lista."""
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._items)):
            return
        it = self._items[idx]
        if it.id in self._marcados:
            self._marcados.discard(it.id)
            estado = _("sin marcar")
        else:
            self._marcados.add(it.id)
            estado = _("marcado")
        self._repintar_marca(idx, it)
        self._actualizar_estado()
        speak("{0}, {1}".format(it.name, estado), interrupt=True)

    def _repintar_marca(self, idx, it):
        """Actualiza la celda 'Marcado' (si existe en la plantilla) y la lectura de la fila."""
        from app.core.plantillas import pulir_lectura
        from collections import defaultdict
        datos = self._datos_item(it)
        for col, (campo, _enc) in enumerate(self._cols):
            if campo == "marcado":
                self.lista.SetItem(idx, col, datos["marcado"])
        try:
            self.lista.establecer_lectura(
                idx, pulir_lectura(self._plantilla_str().format_map(defaultdict(str, datos))))
        except Exception:
            pass

    def _on_cambio_servicio(self):
        """Lo llama el servicio (vía wx.CallAfter) en cada cambio de transferencia. Para NO
        molestar al foco (NVDA releía el elemento en cada tic de progreso), solo se reconstruye
        la lista si CAMBIA el conjunto de elementos visibles (p. ej. termina una subida y aparece
        el archivo). En los demás casos, solo se actualiza la barra de estado."""
        if not self:
            return
        if self._modo == "normal":
            try:
                ids_ahora = [it.id for it in self.repo.listar(self.account_id, self._carpeta_actual)]
            except Exception:
                ids_ahora = None
            if ids_ahora is not None and ids_ahora != [it.id for it in self._items]:
                self._refrescar(silencioso=True)   # refresco de fondo: no releer el foco
                return
        self._actualizar_estado()

    # ------------------------------------------------------------- navegación
    def _ruta_actual(self):
        if self._carpeta_actual is None:
            return "/"
        it = self.repo.obtener_item(self._carpeta_actual)
        return it.path if it else "/"

    def _abrir(self):
        it = self._sel()
        if not it:
            return
        if it.es_carpeta and self._modo == "normal":
            self._navegar_a(it.id)
        elif it.es_archivo:
            self._descargar()

    def _navegar_a(self, carpeta_id):
        self._carpeta_actual = carpeta_id
        # Recortar historial adelante y añadir.
        self._historial = self._historial[:self._hist_pos + 1]
        self._historial.append(carpeta_id)
        self._hist_pos = len(self._historial) - 1
        self._modo = "normal"
        self._refrescar()

    def _subir_nivel(self):
        if self._modo != "normal" or self._carpeta_actual is None:
            self._ver_normal()
            return
        it = self.repo.obtener_item(self._carpeta_actual)
        self._navegar_a(it.parent_id if it else None)

    def _atras(self):
        if self._hist_pos > 0:
            self._hist_pos -= 1
            self._carpeta_actual = self._historial[self._hist_pos]
            self._modo = "normal"
            self._refrescar()

    def _adelante(self):
        if self._hist_pos < len(self._historial) - 1:
            self._hist_pos += 1
            self._carpeta_actual = self._historial[self._hist_pos]
            self._modo = "normal"
            self._refrescar()

    def _ver_papelera(self, event=None):
        self._modo = "trash"
        self._refrescar()

    def _ver_normal(self, event=None):
        self._modo = "normal"
        self._refrescar()

    # ------------------------------------------------------------- selección
    def _sel(self):
        idx = self.lista.GetFocusedItem()
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def _seleccionados(self):
        """Elementos sobre los que actúan las operaciones: los MARCADOS con la casilla
        (barra espaciadora) que estén a la vista; si no hay ninguno marcado, el que tiene foco."""
        marcados = [it for it in self._items if it.id in self._marcados]
        if marcados:
            return marcados
        return [self._sel()] if self._sel() else []

    def _id_enfocado(self):
        it = self._sel()
        return it.id if it else None

    def _seleccionar_todo(self, event=None):
        """Marca TODOS los elementos de la vista (Ctrl+A)."""
        for it in self._items:
            self._marcados.add(it.id)
        for idx, it in enumerate(self._items):
            self._repintar_marca(idx, it)
        self._actualizar_estado()
        speak(_("Todo marcado ({0}).").format(len(self._items)), interrupt=True)

    def _desmarcar_todo(self, event=None):
        for idx, it in enumerate(self._items):
            self._marcados.discard(it.id)
            self._repintar_marca(idx, it)
        self._actualizar_estado()
        speak(_("Nada marcado."), interrupt=True)

    # ------------------------------------------------------------- operaciones locales
    def _nueva_carpeta(self, event=None):
        if self._modo != "normal":
            return
        dlg = EntradaItemDialog(self, _("Nueva carpeta"), pedir_nombre=True)
        if dlg.ShowModal() == wx.ID_OK:
            nombre = dlg.nombre
            desc = dlg.descripcion
            if nombre:
                if self.repo.existe_nombre(self.account_id, self._carpeta_actual, nombre):
                    notificar(_("Ya existe algo con ese nombre."))
                else:
                    self.repo.crear_carpeta(self.account_id, self._carpeta_actual, nombre,
                                            self.svc.obtener_channel_id(self.account_id), desc)
                    notificar(_("Carpeta creada."))
                    self._refrescar()
        dlg.Destroy()

    def _cambiar_descripcion(self, event=None):
        it = self._sel()
        if not it:
            return
        dlg = EntradaItemDialog(self, _("Descripción de «{0}»").format(it.name),
                                pedir_nombre=False, descripcion=it.descripcion or "")
        if dlg.ShowModal() == wx.ID_OK:
            self.repo.establecer_descripcion(it.id, dlg.descripcion)
            notificar(_("Descripción guardada."))
            self._refrescar()
        dlg.Destroy()

    def _renombrar(self, event=None):
        it = self._sel()
        if not it:
            return
        dlg = CustomTextEntryDialog(self, _("Nuevo nombre:"), _("Cambiar el nombre"),
                                    value=it.name)
        if dlg.ShowModal() == wx.ID_OK:
            nuevo = dlg.GetValue().strip()
            if nuevo and nuevo != it.name:
                if self.repo.existe_nombre(self.account_id, it.parent_id, nuevo):
                    notificar(_("Ya existe algo con ese nombre en la carpeta."))
                else:
                    self.repo.renombrar(it.id, nuevo)
                    notificar(_("Renombrado."))
                    self._refrescar()
        dlg.Destroy()

    def _cortar(self, event=None):
        sel = self._seleccionados()
        if sel:
            self._portapapeles = ("cortar", [i.id for i in sel])
            speak(_("{0} elementos cortados.").format(len(sel)), interrupt=True)

    def _copiar(self, event=None):
        sel = self._seleccionados()
        if sel:
            self._portapapeles = ("copiar", [i.id for i in sel])
            speak(_("{0} elementos copiados.").format(len(sel)), interrupt=True)

    def _pegar(self, event=None):
        if not self._portapapeles or self._modo != "normal":
            return
        accion, ids = self._portapapeles
        hechos = 0
        for iid in ids:
            try:
                if accion == "copiar":
                    self.repo.copiar(iid, self._carpeta_actual)
                else:
                    self.repo.mover(iid, self._carpeta_actual)
                hechos += 1
            except ValueError as e:
                notificar(str(e))
        if accion == "cortar":
            self._portapapeles = None
        notificar(_("{0} elementos pegados.").format(hechos))
        self._refrescar()

    def _a_papelera(self, event=None):
        sel = self._seleccionados()
        if not sel or self._modo != "normal":
            return
        from app.core.config_manager import config
        if config.get("telegram_drive", "confirmar_borrado", True):
            if confirmar(self, _("¿Enviar {0} elementos a la papelera?").format(len(sel)),
                         _("Papelera")) != wx.ID_YES:
                return
        for it in sel:
            self.repo.a_papelera(it.id)
        notificar(_("Enviado a la papelera."))
        self._refrescar()

    def _restaurar(self, event=None):
        if self._modo != "trash":
            notificar(_("Abre la papelera para restaurar."))
            return
        for it in self._seleccionados():
            self.repo.restaurar(it.id)
        notificar(_("Restaurado."))
        self._refrescar()

    def _eliminar_definitivo(self, event=None):
        sel = self._seleccionados()
        if not sel:
            return
        if confirmar(self, _("¿Eliminar DEFINITIVAMENTE {0} elementos? No se puede deshacer.").format(len(sel)),
                     _("Eliminar definitivamente")) != wx.ID_YES:
            return
        ids = [i.id for i in sel]
        speak(_("Eliminando..."), interrupt=True)

        def _bg(ctx):
            for iid in ids:
                self.svc.eliminar_definitivo(self.account_id, iid)
            return True

        def _fin(res, err):
            if err:
                notificar(_("Error al eliminar: {0}").format(err))
            else:
                notificar(_("Eliminado definitivamente."))
            self._refrescar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _vaciar_papelera(self, event=None):
        if confirmar(self, _("¿Vaciar la papelera? Se eliminarán definitivamente todos sus elementos."),
                     _("Vaciar papelera")) != wx.ID_YES:
            return
        speak(_("Vaciando la papelera..."), interrupt=True)

        def _bg(ctx):
            return self.svc.vaciar_papelera(self.account_id)

        def _fin(n, err):
            notificar(_("Papelera vaciada.") if not err else _("Error: {0}").format(err))
            self._refrescar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _propiedades(self, event=None):
        it = self._sel()
        if not it:
            return
        partes = self.repo.obtener_partes(it.id) if it.es_archivo else []
        dlg = PropiedadesDialog(self, it, partes)
        dlg.ShowModal()
        dlg.Destroy()

    def _buscar(self, event=None):
        dlg = CustomTextEntryDialog(self, _("Buscar por nombre:"), _("Buscar"))
        if dlg.ShowModal() == wx.ID_OK:
            texto = dlg.GetValue().strip()
            if texto:
                self._busqueda = texto
                self._modo = "search"
                self._refrescar()
        dlg.Destroy()

    # ------------------------------------------------------------- subidas/descargas
    def _subir_archivos(self, event=None):
        if self._modo != "normal":
            self._ver_normal()
        if self.svc.obtener_channel_id(self.account_id) is None:
            self._asegurar_canal()
            return
        dlg = wx.FileDialog(self, _("Elige archivos para subir"),
                            style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            rutas = dlg.GetPaths()
            desc = self._pedir_descripcion_opcional(
                _("Descripción para los archivos (opcional):")) if rutas else ""
            self._encolar_subidas(rutas, self._carpeta_actual, desc)
        dlg.Destroy()

    def _subir_carpeta(self, event=None):
        if self.svc.obtener_channel_id(self.account_id) is None:
            self._asegurar_canal()
            return
        dlg = wx.DirDialog(self, _("Elige una carpeta para subir (con su estructura)"))
        if dlg.ShowModal() == wx.ID_OK:
            raiz_local = dlg.GetPath()
            desc = self._pedir_descripcion_opcional(
                _("Descripción para la carpeta (opcional):"))
            self._encolar_carpeta(raiz_local, self._carpeta_actual, desc)
        dlg.Destroy()

    def _pedir_descripcion_opcional(self, etiqueta):
        """Muestra un diálogo para poner una descripción opcional. Devuelve el texto ('' si se
        deja vacío o se cancela)."""
        dlg = EntradaItemDialog(self, _("Descripción (opcional)"), pedir_nombre=False,
                                etiqueta_desc=etiqueta)
        desc = dlg.descripcion if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        return desc

    def _encolar_subidas(self, rutas, parent_id, descripcion=""):
        """Encola varios archivos resolviendo los conflictos de nombre (una sola pregunta si
        el usuario marca 'aplicar a todos'). La descripción, si la hay, se aplica a cada uno."""
        decision_global = None
        encolados = 0
        for ruta in rutas:
            nombre = os.path.basename(ruta)
            if self.repo.existe_nombre(self.account_id, parent_id, nombre):
                dec = decision_global
                if dec is None:
                    dlg = ConflictoDialog(self, nombre)
                    if dlg.ShowModal() != wx.ID_OK:
                        dlg.Destroy()
                        break  # cancelar todo
                    dec = dlg.decision
                    if dlg.a_todos:
                        decision_global = dec
                    dlg.Destroy()
                if dec == CONF_OMITIR:
                    continue
                if dec == CONF_AMBOS:
                    nombre = self.repo.nombre_disponible(self.account_id, parent_id, nombre)
                # CONF_REEMPLAZAR: se sube con el mismo nombre (quedan ambos; el borrado del
                # viejo se deja al usuario para no destruir datos sin control).
            self.svc.encolar_subida(self.account_id, parent_id, ruta, nombre, descripcion)
            encolados += 1
        if encolados:
            notificar(_("{0} archivos añadidos a la cola de subida.").format(encolados))

    def _encolar_carpeta(self, raiz_local, parent_id, descripcion=""):
        """Sube una carpeta completa conservando su estructura: crea las carpetas virtuales y
        encola cada archivo. Recorre el árbol local. La descripción se pone en la carpeta raíz."""
        mapa = {raiz_local: self.repo.crear_carpeta(
            self.account_id, parent_id, os.path.basename(raiz_local.rstrip("\\/")) or "carpeta",
            self.svc.obtener_channel_id(self.account_id), descripcion).id}
        total = 0
        for dir_actual, subdirs, ficheros in os.walk(raiz_local):
            padre_id = mapa.get(dir_actual)
            if padre_id is None:
                continue
            for sub in subdirs:
                ruta_sub = os.path.join(dir_actual, sub)
                nueva = self.repo.crear_carpeta(self.account_id, padre_id, sub,
                                                self.svc.obtener_channel_id(self.account_id))
                mapa[ruta_sub] = nueva.id
            for f in ficheros:
                self.svc.encolar_subida(self.account_id, padre_id, os.path.join(dir_actual, f), f)
                total += 1
        notificar(_("Carpeta añadida: {0} archivos en cola.").format(total))
        self._refrescar()

    def _descargar(self, event=None):
        sel = self._seleccionados()
        if not sel:
            return
        dlg = wx.DirDialog(self, _("Elige dónde guardar la descarga"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        destino = dlg.GetPath()
        dlg.Destroy()
        total = 0
        for it in sel:
            total += self._encolar_descarga_recursiva(it, destino)
        notificar(_("{0} archivos añadidos a la cola de descarga.").format(total))

    def _encolar_descarga_recursiva(self, it, carpeta_destino):
        """Encola la descarga de un archivo, o de todos los de una carpeta conservando la
        estructura en disco."""
        if it.es_archivo:
            self.svc.encolar_descarga(self.account_id, it.id, carpeta_destino)
            return 1
        sub = os.path.join(carpeta_destino, it.name)
        os.makedirs(sub, exist_ok=True)
        n = 0
        for hijo in self.repo.listar(self.account_id, it.id):
            n += self._encolar_descarga_recursiva(hijo, sub)
        return n

    def _abrir_carpeta_descargas(self, event=None):
        ruta = self.svc.carpeta_descargas()
        try:
            os.startfile(ruta)
        except Exception:
            mostrar_info(self, ruta, _("Carpeta de descargas"))

    # ------------------------------------------------------------- transferencias / índice
    def _abrir_transferencias(self, event=None):
        dlg = TransferenciasDialog(self, self.svc, self.account_id)
        dlg.ShowModal()
        dlg.Destroy()
        self._actualizar_estado()

    def _reconstruir_indice(self, event=None):
        if confirmar(self, _("Se buscará tu canal de Telegram Drive y se reconstruirá desde él la "
                             "lista de archivos. Esto reemplaza el índice local. ¿Continuar?"),
                     _("Reconstruir índice")) != wx.ID_YES:
            return
        speak(_("Buscando el canal y reconstruyendo el índice..."), interrupt=True)

        def _bg(ctx):
            # Las situaciones ESPERADAS (no hay canal, no hay índice en el canal) se devuelven como
            # aviso, no como excepción, para no ensuciar el log con un ERROR con traza.
            try:
                return {"ok": self.svc.reconstruir_indice(self.account_id)}
            except ValueError as e:
                return {"aviso": str(e)}

        def _fin(res, err):
            if err:
                mostrar_info(self, str(err), _("Reconstruir índice"))
                return
            if res and res.get("aviso"):
                # Mensaje CLARO y modal (una notificación se puede perder): p. ej. cuando no hay
                # ningún canal para reconstruir.
                mostrar_info(self, res["aviso"], _("Reconstruir índice"))
                return
            datos = (res or {}).get("ok") or {}
            notificar(_("Índice reconstruido: {0} elementos.").format(datos.get("items", 0)))
            self._carpeta_actual = None
            self._historial = [None]
            self._hist_pos = 0
            self._refrescar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _exportar_indice(self, event=None):
        speak(_("Exportando el índice al canal..."), interrupt=True)

        def _bg(ctx):
            return self.svc.exportar_indice(self.account_id)

        def _fin(res, err):
            notificar(_("Índice exportado al canal.") if not err
                      else _("Error: {0}").format(err))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _revisar_canal(self, event=None):
        """Revisa el canal frente al índice y ofrece borrar del canal lo que no cuadra
        (huérfanos) y quitar del índice los archivos rotos (con partes que faltan)."""
        speak(_("Revisando el canal, un momento..."), interrupt=True)

        def _bg(ctx):
            return self.svc.revisar_canal(self.account_id)

        def _fin(res, err):
            if err:
                mostrar_info(self, str(err), _("Revisar el canal"))
                return
            orf = res.get("orfanos", [])
            rotos = res.get("items_rotos", [])
            resumen = _("El canal tiene {0} documentos; {1} coinciden con tu índice.\n\n"
                        "Documentos huérfanos (no corresponden a ningún archivo): {2}\n"
                        "Archivos con partes que faltan en el canal: {3}").format(
                            res.get("total_canal", 0), res.get("referenciados", 0),
                            len(orf), len(rotos))
            if not orf and not rotos:
                mostrar_info(self, resumen + "\n\n" + _("Todo está en orden."), _("Revisar el canal"))
                return
            acciones = []
            if orf and confirmar(
                    self, resumen + "\n\n" + _("¿Borrar del canal esos {0} documentos huérfanos? "
                                              "(no le corresponden a ningún archivo tuyo)").format(len(orf)),
                    _("Revisar el canal")) == wx.ID_YES:
                acciones.append(("orfanos", orf))
            if rotos and confirmar(
                    self, _("¿Quitar del índice esos {0} archivos rotos? Les faltan partes en el "
                            "canal, así que no se podrían descargar enteros.").format(len(rotos)),
                    _("Revisar el canal")) == wx.ID_YES:
                acciones.append(("rotos", rotos))
            if not acciones:
                return
            speak(_("Reparando..."), interrupt=True)

            def _bg2(ctx):
                n_o = n_r = 0
                for tipo, datos in acciones:
                    if tipo == "orfanos":
                        n_o = self.svc.limpiar_orfanos_canal(self.account_id, datos)
                    else:
                        n_r = self.svc.quitar_items_rotos(self.account_id, datos)
                return (n_o, n_r)

            def _fin2(res2, err2):
                if err2:
                    notificar(_("Error al reparar: {0}").format(err2))
                else:
                    n_o, n_r = res2 or (0, 0)
                    notificar(_("Reparado: {0} huérfanos borrados del canal, {1} archivos rotos "
                                "quitados del índice.").format(n_o, n_r))
                self._refrescar()
            task_manager.start_task(_bg2, callback=_fin2, pass_context=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _purgar_canal(self, event=None):
        """AVANZADO: lista TODO el canal y abre el diálogo de purga (borrado directo, sin
        papelera). Se avisa antes de entrar."""
        if confirmar(self, _("Vas a abrir la PURGA AVANZADA: lista todo lo que hay en el canal "
                             "para borrarlo directamente, SIN papelera. Es para quien sabe lo que "
                             "hace (limpiar índices json de sobra, archivos sueltos, partes "
                             "colgadas). ¿Continuar?"),
                     _("Purga avanzada del canal")) != wx.ID_YES:
            return
        speak(_("Leyendo todo el canal, puede tardar..."), interrupt=True)

        def _bg(ctx):
            return self.svc.listar_canal_detallado(self.account_id)

        def _fin(docs, err):
            if err:
                mostrar_info(self, str(err), _("Purga avanzada del canal"))
                return
            if not docs:
                mostrar_info(self, _("El canal no tiene documentos."), _("Purga avanzada del canal"))
                return
            dlg = PurgarCanalDialog(self, self.svc, self.account_id, docs)
            dlg.ShowModal()
            dlg.Destroy()
            self._refrescar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _reintentar_eliminaciones(self, event=None):
        def _bg(ctx):
            return self.svc.reintentar_eliminaciones(self.account_id)

        def _fin(n, err):
            notificar(_("{0} eliminaciones pendientes resueltas.").format(n or 0)
                      if not err else _("Error: {0}").format(err))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _eliminar_drive(self, event=None):
        """Elimina POR COMPLETO la nube: borra el canal privado (con TODOS los archivos) y el
        índice local. Es IRREVERSIBLE, así que se avisa MUY claramente y se pide confirmar dos
        veces."""
        c = self.repo.contar(self.account_id)
        aviso = _(
            "Vas a ELIMINAR por completo tu Telegram Drive.\n\n"
            "Se borrará el canal privado de Telegram y con él TODOS tus archivos "
            "({0} archivos, {1} carpetas, {2}). Esta acción NO se puede deshacer y NO hay "
            "papelera que valga: se pierde todo.\n\n¿Seguro que quieres continuar?").format(
                c["archivos"], c["carpetas"], self._tam(c["bytes"]))
        if confirmar(self, aviso, _("Eliminar Telegram Drive")) != wx.ID_YES:
            return
        if confirmar(self, _("Última confirmación: se perderá TODO lo que tienes en la nube. "
                             "¿Eliminar definitivamente?"),
                     _("Eliminar Telegram Drive")) != wx.ID_YES:
            return
        speak(_("Eliminando Telegram Drive..."), interrupt=True)

        def _bg(ctx):
            return self.svc.eliminar_drive_completo(self.account_id, borrar_canal=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo eliminar la nube: {0}").format(err))
                self._refrescar()
            else:
                notificar(_("Telegram Drive eliminado. Se ha borrado el canal y todo su contenido."))
                self.Close()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _menu_contextual(self, event):
        it = self._sel()
        menu = wx.Menu()
        if self._modo == "trash":
            self._add(menu, _("Restaurar"), self._restaurar)
            self._add(menu, _("Eliminar definitivamente"), self._eliminar_definitivo)
        else:
            if it and it.es_carpeta:
                self._add(menu, _("Abrir"), lambda e=None: self._abrir())
            if it and it.es_archivo:
                self._add(menu, _("Descargar"), self._descargar)
            self._add(menu, _("Cambiar el nombre..."), self._renombrar)
            self._add(menu, _("Cambiar la descripción..."), self._cambiar_descripcion)
            self._add(menu, _("Cortar"), self._cortar)
            self._add(menu, _("Copiar"), self._copiar)
            self._add(menu, _("Pegar"), self._pegar)
            self._add(menu, _("Enviar a la papelera"), self._a_papelera)
            self._add(menu, _("Propiedades"), self._propiedades)
        self.lista.PopupMenu(menu)
        menu.Destroy()

    @staticmethod
    def _tam(n):
        n = float(n or 0)
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or u == "TB":
                return f"{int(n)} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return ""
