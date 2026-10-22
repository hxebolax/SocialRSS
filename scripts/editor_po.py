# -*- coding: utf-8 -*-
"""
Editor y Traductor de archivos PO/POT para SocialRSS Accessible Suite.

Funcionalidades:
- Abrir y editar archivos .po existentes.
- Abrir archivos .pot y generar nuevos .po para cualquier idioma.
- Navegar entradas con CheckListBox accesible (compatible con NVDA).
- Ver y editar msgid (origen) y msgstr (destino).
- Traducir entradas individuales o todo el archivo con Google Translate.
- Preservar variables ({}, %s, &atajos, \\n) durante la traducción.
- Guardar el archivo .po resultante.
"""

import os
import sys
import glob
import wx
from ctypes import windll, c_long
from threading import Thread

# Asegurar que los módulos locales del directorio scripts se encuentren
_DIR_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR_SCRIPTS)
import polib
from traductor_google import traducir, IDIOMAS

# Dominio gettext del proyecto (los .po/.mo se llaman socialrss.* dentro de <idioma>/LC_MESSAGES).
DOMINIO = "socialrss"

# Ruta base del proyecto (padre de scripts/). Las traducciones viven en data/locales.
_DIR_PROYECTO = os.path.dirname(_DIR_SCRIPTS)
_DIR_LOCALES = os.path.join(_DIR_PROYECTO, "data", "locales")

# ── Accesibilidad para CheckListBox (NVDA) ──────────────────────────

user32 = windll.user32
CHILDID_SELF = 0
EVENT_OBJECT_STATECHANGE = 0x800a
OBJID_CLIENT = -4


def NotifyWinEvent(event, hwnd, idObject, idChild):
	user32.NotifyWinEvent(event, hwnd, c_long(idObject), c_long(idChild))


class ListCtrlAccessible(wx.Accessible):
	"""Accesibilidad para CheckListBox con lectores de pantalla."""

	def GetRole(self, childId):
		if childId == CHILDID_SELF:
			return super().GetRole(childId)
		return (wx.ACC_OK, wx.ROLE_SYSTEM_CHECKBUTTON)

	def GetState(self, childId):
		if childId == CHILDID_SELF:
			return super().GetState(childId)
		states = (wx.ACC_STATE_SYSTEM_SELECTABLE
				| wx.ACC_STATE_SYSTEM_FOCUSABLE)
		if self.Window.IsChecked(childId - 1):
			states |= wx.ACC_STATE_SYSTEM_CHECKED
		if self.Window.IsSelected(childId - 1):
			states |= (wx.ACC_STATE_SYSTEM_SELECTED
					| wx.ACC_STATE_SYSTEM_FOCUSED)
		return (wx.ACC_OK, states)


class CustomCheckListBox(wx.CheckListBox):
	"""CheckListBox accesible para NVDA."""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.SetAccessible(ListCtrlAccessible(self))
		self.Bind(wx.EVT_CHECKLISTBOX, self._notificar)

	def _notificar(self, evt):
		NotifyWinEvent(
			EVENT_OBJECT_STATECHANGE, self.Handle,
			OBJID_CLIENT, evt.Selection + 1
		)


# ── Diálogo principal ───────────────────────────────────────────────

class EditorPO(wx.Frame):
	"""Editor y traductor de archivos PO/POT."""

	def __init__(self):
		super().__init__(
			None, title="Editor de Traducciones PO",
			size=(800, 600),
			style=wx.DEFAULT_FRAME_STYLE
		)
		self._po = None
		self._archivo_actual = ""
		self._modificado = False
		self._crear_ui()
		self._crear_menu()
		self.Bind(wx.EVT_CLOSE, self._on_cerrar)
		self.Centre()

	# ── Interfaz ────────────────────────────────────────────────────

	def _crear_menu(self):
		barra = wx.MenuBar()

		menu_archivo = wx.Menu()
		menu_archivo.Append(wx.ID_OPEN, "&Abrir archivo .po/.pot\tCtrl+O")
		menu_archivo.Append(wx.ID_SAVE, "&Guardar\tCtrl+S")
		menu_archivo.Append(wx.ID_SAVEAS, "Guardar &como...\tCtrl+Shift+S")
		menu_archivo.AppendSeparator()
		self._id_generar = wx.NewIdRef()
		menu_archivo.Append(self._id_generar, "&Generar .po desde .pot...")
		menu_archivo.AppendSeparator()
		menu_archivo.Append(wx.ID_EXIT, "&Salir\tAlt+F4")
		barra.Append(menu_archivo, "&Archivo")

		menu_traducir = wx.Menu()
		self._id_trad_sel = wx.NewIdRef()
		menu_traducir.Append(
			self._id_trad_sel,
			"Traducir entrada &seleccionada\tF5"
		)
		self._id_trad_marcadas = wx.NewIdRef()
		menu_traducir.Append(
			self._id_trad_marcadas,
			"Traducir entradas &marcadas\tF6"
		)
		self._id_trad_vacias = wx.NewIdRef()
		menu_traducir.Append(
			self._id_trad_vacias,
			"Traducir entradas &vacías\tF7"
		)
		self._id_trad_todo = wx.NewIdRef()
		menu_traducir.Append(
			self._id_trad_todo,
			"Traducir &todo el archivo\tF8"
		)
		menu_traducir.AppendSeparator()
		self._id_copiar_originales = wx.NewIdRef()
		menu_traducir.Append(
			self._id_copiar_originales,
			"&Copiar originales a entradas vacías\tF9"
		)
		barra.Append(menu_traducir, "&Traducir")

		menu_sel = wx.Menu()
		self._id_sel_todo = wx.NewIdRef()
		menu_sel.Append(self._id_sel_todo, "Marcar &todas\tCtrl+A")
		self._id_sel_nada = wx.NewIdRef()
		menu_sel.Append(self._id_sel_nada, "&Desmarcar todas\tCtrl+D")
		self._id_sel_vacias = wx.NewIdRef()
		menu_sel.Append(
			self._id_sel_vacias, "Marcar &vacías\tCtrl+E"
		)
		self._id_sel_invertir = wx.NewIdRef()
		menu_sel.Append(
			self._id_sel_invertir, "&Invertir selección\tCtrl+I"
		)
		barra.Append(menu_sel, "&Selección")

		# Menú Herramientas
		menu_herr = wx.Menu()
		self._id_h_actualizar = wx.NewIdRef()
		menu_herr.Append(
			self._id_h_actualizar,
			"&Actualizar todos los .po desde .pot"
		)
		self._id_h_trad_todos = wx.NewIdRef()
		menu_herr.Append(
			self._id_h_trad_todos,
			"&Traducir cadenas vacías en todos los idiomas"
		)
		menu_herr.AppendSeparator()
		self._id_h_compilar_mo = wx.NewIdRef()
		menu_herr.Append(
			self._id_h_compilar_mo,
			"&Compilar todos los .mo"
		)
		menu_herr.AppendSeparator()
		self._id_h_estadisticas = wx.NewIdRef()
		menu_herr.Append(
			self._id_h_estadisticas,
			"&Estadísticas de todos los idiomas"
		)
		self._id_h_limpiar = wx.NewIdRef()
		menu_herr.Append(
			self._id_h_limpiar,
			"&Limpiar entradas obsoletas del archivo actual"
		)
		menu_herr.AppendSeparator()
		self._id_h_generar_todos = wx.NewIdRef()
		menu_herr.Append(
			self._id_h_generar_todos,
			"&Generar, traducir y compilar todos los idiomas"
		)
		barra.Append(menu_herr, "&Herramientas")

		self.SetMenuBar(barra)

		self.Bind(wx.EVT_MENU, self._on_abrir, id=wx.ID_OPEN)
		self.Bind(wx.EVT_MENU, self._on_guardar, id=wx.ID_SAVE)
		self.Bind(wx.EVT_MENU, self._on_guardar_como, id=wx.ID_SAVEAS)
		self.Bind(wx.EVT_MENU, self._on_generar_po, id=self._id_generar)
		self.Bind(wx.EVT_MENU, self._on_salir, id=wx.ID_EXIT)
		self.Bind(wx.EVT_MENU, self._on_trad_seleccionada, id=self._id_trad_sel)
		self.Bind(wx.EVT_MENU, self._on_trad_marcadas, id=self._id_trad_marcadas)
		self.Bind(wx.EVT_MENU, self._on_trad_vacias, id=self._id_trad_vacias)
		self.Bind(wx.EVT_MENU, self._on_trad_todo, id=self._id_trad_todo)
		self.Bind(wx.EVT_MENU, self._on_copiar_originales, id=self._id_copiar_originales)
		self.Bind(wx.EVT_MENU, self._on_sel_todo, id=self._id_sel_todo)
		self.Bind(wx.EVT_MENU, self._on_sel_nada, id=self._id_sel_nada)
		self.Bind(wx.EVT_MENU, self._on_sel_vacias, id=self._id_sel_vacias)
		self.Bind(wx.EVT_MENU, self._on_sel_invertir, id=self._id_sel_invertir)
		self.Bind(wx.EVT_MENU, self._on_h_actualizar, id=self._id_h_actualizar)
		self.Bind(wx.EVT_MENU, self._on_h_trad_todos, id=self._id_h_trad_todos)
		self.Bind(wx.EVT_MENU, self._on_h_compilar_mo, id=self._id_h_compilar_mo)
		self.Bind(wx.EVT_MENU, self._on_h_estadisticas, id=self._id_h_estadisticas)
		self.Bind(wx.EVT_MENU, self._on_h_limpiar, id=self._id_h_limpiar)
		self.Bind(wx.EVT_MENU, self._on_h_generar_todos, id=self._id_h_generar_todos)

	def _crear_ui(self):
		panel = wx.Panel(self)
		sizer = wx.BoxSizer(wx.VERTICAL)

		# Idiomas
		hbox_idiomas = wx.BoxSizer(wx.HORIZONTAL)
		hbox_idiomas.Add(
			wx.StaticText(panel, label="Idioma &origen:"),
			0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5
		)
		self.choice_origen = wx.Choice(panel)
		for codigo, nombre in sorted(IDIOMAS.items(), key=lambda x: x[1]):
			self.choice_origen.Append(f"{nombre} ({codigo})", codigo)
		self._seleccionar_idioma(self.choice_origen, 'es')
		hbox_idiomas.Add(self.choice_origen, 1, wx.RIGHT, 15)

		hbox_idiomas.Add(
			wx.StaticText(panel, label="Idioma &destino:"),
			0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5
		)
		self.choice_destino = wx.Choice(panel)
		for codigo, nombre in sorted(IDIOMAS.items(), key=lambda x: x[1]):
			self.choice_destino.Append(f"{nombre} ({codigo})", codigo)
		self._seleccionar_idioma(self.choice_destino, 'en')
		hbox_idiomas.Add(self.choice_destino, 1)

		sizer.Add(hbox_idiomas, 0, wx.EXPAND | wx.ALL, 8)

		# Lista de entradas
		sizer.Add(
			wx.StaticText(panel, label="&Entradas:"),
			0, wx.LEFT | wx.TOP, 8
		)
		self.lista = CustomCheckListBox(panel)
		self.lista.Bind(wx.EVT_LISTBOX, self._on_seleccion)
		sizer.Add(self.lista, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

		# Origen (msgid)
		sizer.Add(
			wx.StaticText(panel, label="O&rigen (msgid):"),
			0, wx.LEFT | wx.TOP, 8
		)
		self.txt_origen = wx.TextCtrl(
			panel, style=wx.TE_MULTILINE | wx.TE_READONLY,
			size=(-1, 80)
		)
		sizer.Add(self.txt_origen, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

		# Destino (msgstr)
		sizer.Add(
			wx.StaticText(panel, label="&Traducción (msgstr):"),
			0, wx.LEFT | wx.TOP, 8
		)
		self.txt_destino = wx.TextCtrl(
			panel, style=wx.TE_MULTILINE, size=(-1, 80)
		)
		self.txt_destino.Bind(wx.EVT_TEXT, self._on_editar_destino)
		sizer.Add(self.txt_destino, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

		# Barra de estado
		self.barra_estado = self.CreateStatusBar(2)
		self.barra_estado.SetStatusWidths([-2, -1])
		self._actualizar_estado("Sin archivo cargado")

		# Progreso (oculto)
		self.gauge = wx.Gauge(panel, range=100, size=(-1, 18))
		self.gauge.Hide()
		sizer.Add(self.gauge, 0, wx.EXPAND | wx.ALL, 8)

		panel.SetSizer(sizer)

	# ── Utilidades ──────────────────────────────────────────────────

	def _seleccionar_idioma(self, choice, codigo):
		for i in range(choice.GetCount()):
			if choice.GetClientData(i) == codigo:
				choice.SetSelection(i)
				return

	def _idioma_origen(self):
		sel = self.choice_origen.GetSelection()
		return self.choice_origen.GetClientData(sel) if sel != wx.NOT_FOUND else 'es'

	def _idioma_destino(self):
		sel = self.choice_destino.GetSelection()
		return self.choice_destino.GetClientData(sel) if sel != wx.NOT_FOUND else 'en'

	def _actualizar_estado(self, texto, info=""):
		self.barra_estado.SetStatusText(texto, 0)
		self.barra_estado.SetStatusText(info, 1)

	def _actualizar_titulo(self):
		mod = " *" if self._modificado else ""
		nombre = os.path.basename(self._archivo_actual) if self._archivo_actual else "Sin archivo"
		self.SetTitle(f"{nombre}{mod} — Editor de Traducciones PO")

	def _cargar_lista(self):
		"""Llena el CheckListBox con las entradas del archivo."""
		self.lista.Clear()
		if not self._po:
			return
		for entrada in self._po:
			if entrada.obsolete:
				continue
			resumen = entrada.msgid[:80].replace('\n', ' ')
			estado = "✓" if entrada.msgstr else "○"
			self.lista.Append(f"{estado} {resumen}")

		total = len([e for e in self._po if not e.obsolete])
		trad = len(self._po.translated_entries())
		pct = self._po.percent_translated()
		self._actualizar_estado(
			f"{total} entradas | {trad} traducidas ({pct}%)",
			os.path.basename(self._archivo_actual)
		)

	def _entradas_visibles(self):
		"""Devuelve las entradas no obsoletas."""
		if not self._po:
			return []
		return [e for e in self._po if not e.obsolete]

	def _confirmar_no_guardado(self):
		"""Si hay cambios sin guardar, pide confirmación."""
		if not self._modificado:
			return True
		r = wx.MessageBox(
			"Hay cambios sin guardar. ¿Desea continuar sin guardar?",
			"Cambios pendientes",
			wx.YES_NO | wx.ICON_WARNING, self
		)
		return r == wx.YES

	# ── Eventos de archivo ──────────────────────────────────────────

	def _on_abrir(self, event):
		if not self._confirmar_no_guardado():
			return
		dlg = wx.FileDialog(
			self, "Abrir archivo PO o POT",
			wildcard="Archivos PO/POT (*.po;*.pot)|*.po;*.pot",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
		)
		if dlg.ShowModal() == wx.ID_OK:
			ruta = dlg.GetPath()
			try:
				self._po = polib.pofile(ruta)
				self._archivo_actual = ruta
				self._modificado = False
				self._cargar_lista()
				self._actualizar_titulo()
				self.txt_origen.SetValue("")
				self.txt_destino.SetValue("")
			except Exception as e:
				wx.MessageBox(
					f"Error abriendo archivo:\n{e}",
					"Error", wx.OK | wx.ICON_ERROR, self
				)
		dlg.Destroy()

	def _on_guardar(self, event):
		if not self._po or not self._archivo_actual:
			self._on_guardar_como(event)
			return
		try:
			self._po.save(self._archivo_actual)
			self._modificado = False
			self._actualizar_titulo()
			self._actualizar_estado("Archivo guardado correctamente.")
		except Exception as e:
			wx.MessageBox(
				f"Error guardando:\n{e}",
				"Error", wx.OK | wx.ICON_ERROR, self
			)

	def _on_guardar_como(self, event):
		if not self._po:
			return
		dlg = wx.FileDialog(
			self, "Guardar como",
			wildcard="Archivo PO (*.po)|*.po",
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
		)
		if dlg.ShowModal() == wx.ID_OK:
			self._archivo_actual = dlg.GetPath()
			self._on_guardar(event)
		dlg.Destroy()

	def _on_generar_po(self, event):
		"""Abre un .pot y genera un .po nuevo para el idioma destino."""
		dlg_pot = wx.FileDialog(
			self, "Seleccionar archivo POT",
			wildcard="Archivos POT (*.pot)|*.pot",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
		)
		if dlg_pot.ShowModal() != wx.ID_OK:
			dlg_pot.Destroy()
			return
		ruta_pot = dlg_pot.GetPath()
		dlg_pot.Destroy()

		# Pedir idioma destino
		codigos = sorted(IDIOMAS.keys(), key=lambda c: IDIOMAS[c])
		nombres = [f"{IDIOMAS[c]} ({c})" for c in codigos]
		dlg_idioma = wx.SingleChoiceDialog(
			self, "Seleccione el idioma para el nuevo archivo .po:",
			"Generar .po desde .pot", nombres
		)
		if dlg_idioma.ShowModal() != wx.ID_OK:
			dlg_idioma.Destroy()
			return
		idx = dlg_idioma.GetSelection()
		codigo_destino = codigos[idx]
		dlg_idioma.Destroy()

		# Pedir dónde guardar. Se sugiere la RUTA ESTÁNDAR del proyecto para que gettext lo
		# encuentre: data/locales/<idioma>/LC_MESSAGES/socialrss.po (se crea la carpeta si falta).
		directorio = os.path.join(_DIR_LOCALES, codigo_destino, "LC_MESSAGES")
		try:
			os.makedirs(directorio, exist_ok=True)
		except Exception:
			directorio = os.path.dirname(ruta_pot)
		nombre_sugerido = f"{DOMINIO}.po"
		dlg_guardar = wx.FileDialog(
			self, "Guardar nuevo archivo .po",
			defaultDir=directorio,
			defaultFile=nombre_sugerido,
			wildcard="Archivo PO (*.po)|*.po",
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
		)
		if dlg_guardar.ShowModal() != wx.ID_OK:
			dlg_guardar.Destroy()
			return
		ruta_po = dlg_guardar.GetPath()
		dlg_guardar.Destroy()

		try:
			pot = polib.pofile(ruta_pot)
			pot.metadata['Language'] = codigo_destino
			pot.metadata['Content-Type'] = 'text/plain; charset=UTF-8'
			pot.save(ruta_po)

			# Abrir el nuevo archivo
			self._po = polib.pofile(ruta_po)
			self._archivo_actual = ruta_po
			self._modificado = False
			self._seleccionar_idioma(self.choice_destino, codigo_destino)
			self._cargar_lista()
			self._actualizar_titulo()
			self.txt_origen.SetValue("")
			self.txt_destino.SetValue("")
			self._actualizar_estado(
				f"Archivo generado para {IDIOMAS.get(codigo_destino, codigo_destino)}"
			)
		except Exception as e:
			wx.MessageBox(
				f"Error generando archivo:\n{e}",
				"Error", wx.OK | wx.ICON_ERROR, self
			)

	def _on_salir(self, event):
		self.Close()

	def _on_cerrar(self, event):
		if self._modificado:
			r = wx.MessageBox(
				"Hay cambios sin guardar. ¿Desea guardar antes de salir?",
				"Salir", wx.YES_NO | wx.CANCEL | wx.ICON_WARNING, self
			)
			if r == wx.CANCEL:
				return
			if r == wx.YES:
				self._on_guardar(None)
		self.Destroy()

	# ── Navegación de entradas ──────────────────────────────────────

	def _on_seleccion(self, event):
		"""Muestra la entrada seleccionada."""
		idx = self.lista.GetSelection()
		if idx == wx.NOT_FOUND:
			return
		entradas = self._entradas_visibles()
		if idx >= len(entradas):
			return
		entrada = entradas[idx]
		self.txt_origen.SetValue(entrada.msgid)
		self.txt_destino.ChangeValue(entrada.msgstr)

	def _on_editar_destino(self, event):
		"""Guarda la edición manual del usuario en la entrada actual."""
		idx = self.lista.GetSelection()
		if idx == wx.NOT_FOUND:
			return
		entradas = self._entradas_visibles()
		if idx >= len(entradas):
			return
		entradas[idx].msgstr = self.txt_destino.GetValue()
		self._modificado = True
		self._actualizar_titulo()
		# Actualizar icono en la lista
		resumen = entradas[idx].msgid[:80].replace('\n', ' ')
		estado = "✓" if entradas[idx].msgstr else "○"
		self.lista.SetString(idx, f"{estado} {resumen}")

	# ── Selección masiva ────────────────────────────────────────────

	def _on_sel_todo(self, event):
		for i in range(self.lista.GetCount()):
			self.lista.Check(i, True)

	def _on_sel_nada(self, event):
		for i in range(self.lista.GetCount()):
			self.lista.Check(i, False)

	def _on_sel_vacias(self, event):
		entradas = self._entradas_visibles()
		for i, e in enumerate(entradas):
			self.lista.Check(i, not e.msgstr)

	def _on_sel_invertir(self, event):
		for i in range(self.lista.GetCount()):
			self.lista.Check(i, not self.lista.IsChecked(i))

	# ── Traducción ──────────────────────────────────────────────────

	def _on_trad_seleccionada(self, event):
		"""Traduce solo la entrada actualmente seleccionada."""
		idx = self.lista.GetSelection()
		if idx == wx.NOT_FOUND:
			wx.MessageBox(
				"Seleccione una entrada primero.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return
		entradas = self._entradas_visibles()
		self._traducir_entradas([entradas[idx]], [idx])

	def _on_trad_marcadas(self, event):
		"""Traduce las entradas marcadas con check."""
		entradas = self._entradas_visibles()
		indices = [i for i in range(self.lista.GetCount()) if self.lista.IsChecked(i)]
		if not indices:
			wx.MessageBox(
				"No hay entradas marcadas.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return
		seleccion = [entradas[i] for i in indices]
		self._traducir_entradas(seleccion, indices)

	def _on_trad_vacias(self, event):
		"""Traduce solo las entradas sin traducción."""
		entradas = self._entradas_visibles()
		indices = [i for i, e in enumerate(entradas) if not e.msgstr]
		if not indices:
			wx.MessageBox(
				"Todas las entradas ya tienen traducción.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return
		seleccion = [entradas[i] for i in indices]
		self._traducir_entradas(seleccion, indices)

	def _on_trad_todo(self, event):
		"""Traduce todas las entradas del archivo."""
		entradas = self._entradas_visibles()
		if not entradas:
			return
		r = wx.MessageBox(
			f"Se traducirán {len(entradas)} entradas.\n"
			"Las traducciones existentes serán sobrescritas.\n\n"
			"¿Desea continuar?",
			"Traducir todo", wx.YES_NO | wx.ICON_QUESTION, self
		)
		if r != wx.YES:
			return
		indices = list(range(len(entradas)))
		self._traducir_entradas(entradas, indices)

	def _on_copiar_originales(self, event):
		"""Copia msgid a msgstr en las entradas vacías."""
		entradas = self._entradas_visibles()
		indices = [i for i, e in enumerate(entradas) if not e.msgstr]
		if not indices:
			wx.MessageBox(
				"Todas las entradas ya tienen traducción.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return
		r = wx.MessageBox(
			f"Se copiará el texto original a {len(indices)} entradas vacías.\n"
			"¿Continuar?",
			"Copiar originales", wx.YES_NO | wx.ICON_QUESTION, self
		)
		if r != wx.YES:
			return
		copiadas = 0
		for i in indices:
			e = entradas[i]
			if e.msgid:
				e.msgstr = e.msgid
				copiadas += 1
		self._modificado = True
		self._actualizar_titulo()
		self._cargar_lista()
		self._actualizar_estado(
			f"{copiadas} entradas copiadas del original."
		)
		wx.MessageBox(
			f"Se copiaron {copiadas} entradas del original.",
			"Copia completada", wx.OK | wx.ICON_INFORMATION, self
		)

	def _traducir_entradas(self, entradas, indices):
		"""Traduce una lista de entradas en hilo de fondo."""
		origen = self._idioma_origen()
		destino = self._idioma_destino()
		mismo_idioma = (origen == destino)

		if mismo_idioma:
			# Copiar msgid a msgstr directamente (sin hilo)
			copiadas = 0
			for i, entrada in zip(indices, entradas):
				if entrada.msgid:
					entrada.msgstr = entrada.msgid
					copiadas += 1
					resumen = entrada.msgid[:80].replace('\n', ' ')
					self.lista.SetString(i, f"\u2713 {resumen}")
			self._modificado = True
			self._actualizar_titulo()
			self._cargar_lista()
			self._actualizar_estado(
				f"{copiadas} entradas copiadas del original."
			)
			wx.MessageBox(
				f"Se copiaron {copiadas} entradas del original (mismo idioma).",
				"Copia completada", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		total = len(entradas)
		self.gauge.SetRange(total)
		self.gauge.SetValue(0)
		self.gauge.Show()
		self.Layout()

		# Deshabilitar controles durante traducción
		self.lista.Disable()
		self.txt_destino.Disable()

		def hilo():
			errores = 0
			for i, entrada in enumerate(entradas):
				if not entrada.msgid:
					wx.CallAfter(self.gauge.SetValue, i + 1)
					continue
				resultado = traducir(entrada.msgid, origen, destino)
				if resultado:
					entrada.msgstr = resultado
				else:
					errores += 1

				wx.CallAfter(self._progreso_traduccion, i + 1, total, indices[i], entrada)

			wx.CallAfter(self._fin_traduccion, total, errores)

		Thread(target=hilo, daemon=True).start()

	def _progreso_traduccion(self, actual, total, idx_lista, entrada):
		"""Actualiza la UI durante la traducción."""
		self.gauge.SetValue(actual)
		self._actualizar_estado(f"Traduciendo {actual}/{total}...")

		# Actualizar la línea en la lista
		resumen = entrada.msgid[:80].replace('\n', ' ')
		estado = "✓" if entrada.msgstr else "○"
		self.lista.SetString(idx_lista, f"{estado} {resumen}")

		# Si es la entrada seleccionada, actualizar el campo
		if self.lista.GetSelection() == idx_lista:
			self.txt_destino.ChangeValue(entrada.msgstr)

	def _fin_traduccion(self, total, errores):
		"""Finaliza el proceso de traducción."""
		self.gauge.Hide()
		self.lista.Enable()
		self.txt_destino.Enable()
		self.Layout()

		self._modificado = True
		self._actualizar_titulo()
		self._cargar_lista()

		msg = f"Traducción completada: {total} entradas procesadas."
		if errores:
			msg += f"\n{errores} entrada(s) no pudieron traducirse."
		self._actualizar_estado(msg)
		wx.MessageBox(msg, "Traducción finalizada", wx.OK | wx.ICON_INFORMATION, self)

	# ── Herramientas masivas ───────────────────────────────────────

	def _buscar_pot(self):
		"""Busca el archivo .pot en el directorio locales."""
		pots = glob.glob(os.path.join(_DIR_LOCALES, "*.pot"))
		if not pots:
			wx.MessageBox(
				f"No se encontró ningún archivo .pot en:\n{_DIR_LOCALES}",
				"Error", wx.OK | wx.ICON_ERROR, self
			)
			return None
		return pots[0]

	def _buscar_pos(self):
		"""Busca todos los archivos .po dentro de locales/**/LC_MESSAGES/."""
		# Patrón: locales/<idioma>/LC_MESSAGES/*.po
		resultados = []
		if not os.path.isdir(_DIR_LOCALES):
			return resultados
		for idioma_dir in os.listdir(_DIR_LOCALES):
			lc_dir = os.path.join(_DIR_LOCALES, idioma_dir, "LC_MESSAGES")
			if os.path.isdir(lc_dir):
				for f in os.listdir(lc_dir):
					if f.endswith(".po"):
						resultados.append((
							idioma_dir,
							os.path.join(lc_dir, f)
						))
		# También buscar .po sueltos en locales/
		for f in os.listdir(_DIR_LOCALES):
			if f.endswith(".po"):
				codigo = os.path.splitext(f)[0]
				resultados.append((
					codigo,
					os.path.join(_DIR_LOCALES, f)
				))
		return resultados

	def _on_h_actualizar(self, event):
		"""Actualiza todos los .po del directorio locales con el .pot."""
		ruta_pot = self._buscar_pot()
		if not ruta_pot:
			return

		archivos_po = self._buscar_pos()
		if not archivos_po:
			wx.MessageBox(
				"No se encontraron archivos .po en el directorio locales.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		r = wx.MessageBox(
			f"Se actualizarán {len(archivos_po)} archivo(s) .po "
			f"con las cadenas de:\n{os.path.basename(ruta_pot)}\n\n"
			"Las traducciones existentes se conservarán.\n"
			"¿Continuar?",
			"Actualizar .po desde .pot",
			wx.YES_NO | wx.ICON_QUESTION, self
		)
		if r != wx.YES:
			return

		try:
			pot = polib.pofile(ruta_pot)
			resultados = []
			for idioma, ruta_po in archivos_po:
				po = polib.pofile(ruta_po)
				antes = len(po)
				po.merge(pot)
				nuevas = len(po) - antes
				po.save(ruta_po)
				resultados.append(f"  {idioma}: {nuevas} cadenas nuevas")

			msg = "Actualización completada:\n\n" + "\n".join(resultados)
			wx.MessageBox(msg, "Resultado", wx.OK | wx.ICON_INFORMATION, self)

			# Si el archivo actual es uno de los actualizados, recargarlo
			if self._archivo_actual:
				for _, ruta in archivos_po:
					if os.path.abspath(ruta) == os.path.abspath(self._archivo_actual):
						self._po = polib.pofile(self._archivo_actual)
						self._cargar_lista()
						break

		except Exception as e:
			wx.MessageBox(
				f"Error actualizando archivos:\n{e}",
				"Error", wx.OK | wx.ICON_ERROR, self
			)

	def _on_h_trad_todos(self, event):
		"""Traduce las cadenas vacías en todos los .po de locales."""
		archivos_po = self._buscar_pos()
		if not archivos_po:
			wx.MessageBox(
				"No se encontraron archivos .po.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		origen = self._idioma_origen()

		# Resumen previo
		lineas = []
		total_vacias = 0
		for idioma, ruta in archivos_po:
			po = polib.pofile(ruta)
			vacias = len(po.untranslated_entries())
			total_vacias += vacias
			nombre_idioma = IDIOMAS.get(idioma, idioma)
			lineas.append(f"  {nombre_idioma} ({idioma}): {vacias} vacías")

		if total_vacias == 0:
			wx.MessageBox(
				"Todos los idiomas están completamente traducidos.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		r = wx.MessageBox(
			f"Se traducirán {total_vacias} cadenas en total:\n\n"
			+ "\n".join(lineas)
			+ f"\n\nIdioma origen: {IDIOMAS.get(origen, origen)}"
			+ "\n¿Continuar?",
			"Traducir todos los idiomas",
			wx.YES_NO | wx.ICON_QUESTION, self
		)
		if r != wx.YES:
			return

		self.gauge.SetRange(total_vacias)
		self.gauge.SetValue(0)
		self.gauge.Show()
		self.Layout()

		def hilo():
			procesadas = 0
			errores_total = 0
			for idioma, ruta in archivos_po:
				po = polib.pofile(ruta)
				vacias = po.untranslated_entries()
				if not vacias:
					continue

				nombre_idioma = IDIOMAS.get(idioma, idioma)
				wx.CallAfter(
					self._actualizar_estado,
					f"Traduciendo {nombre_idioma}..."
				)

				mismo = (idioma == origen)
				for entrada in vacias:
					if not entrada.msgid:
						procesadas += 1
						continue
					if mismo:
						# Mismo idioma: copiar directamente
						entrada.msgstr = entrada.msgid
					else:
						resultado = traducir(
							entrada.msgid, origen, idioma
						)
						if resultado:
							entrada.msgstr = resultado
						else:
							errores_total += 1
					procesadas += 1
					wx.CallAfter(self.gauge.SetValue, procesadas)

				po.save(ruta)

			wx.CallAfter(
				self._fin_trad_masiva, procesadas, errores_total
			)

		Thread(target=hilo, daemon=True).start()

	def _fin_trad_masiva(self, total, errores):
		"""Finaliza la traducción masiva."""
		self.gauge.Hide()
		self.Layout()
		msg = f"Traducción masiva completada: {total} entradas procesadas."
		if errores:
			msg += f"\n{errores} entrada(s) fallaron."
		self._actualizar_estado(msg)
		wx.MessageBox(
			msg, "Traducción masiva",
			wx.OK | wx.ICON_INFORMATION, self
		)
		# Recargar el archivo actual si estaba abierto
		if self._archivo_actual and self._po:
			self._po = polib.pofile(self._archivo_actual)
			self._cargar_lista()

	def _on_h_compilar_mo(self, event):
		"""Compila todos los .po a .mo en el directorio locales."""
		archivos_po = self._buscar_pos()
		if not archivos_po:
			wx.MessageBox(
				"No se encontraron archivos .po.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		resultados = []
		errores = []
		for idioma, ruta_po in archivos_po:
			try:
				po = polib.pofile(ruta_po)
				ruta_mo = ruta_po.replace(".po", ".mo")
				po.save_as_mofile(ruta_mo)
				pct = po.percent_translated()
				resultados.append(f"  {idioma}: {pct}% → {os.path.basename(ruta_mo)}")
			except Exception as e:
				errores.append(f"  {idioma}: {e}")

		msg = f"Compilación completada: {len(resultados)} archivo(s).\n\n"
		msg += "\n".join(resultados)
		if errores:
			msg += "\n\nErrores:\n" + "\n".join(errores)

		wx.MessageBox(msg, "Compilar .mo", wx.OK | wx.ICON_INFORMATION, self)

	def _on_h_estadisticas(self, event):
		"""Muestra estadísticas de traducción de todos los idiomas."""
		archivos_po = self._buscar_pos()
		if not archivos_po:
			wx.MessageBox(
				"No se encontraron archivos .po.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		lineas = []
		for idioma, ruta in archivos_po:
			try:
				po = polib.pofile(ruta)
				total = len([e for e in po if not e.obsolete])
				trad = len(po.translated_entries())
				fuzzy = len(po.fuzzy_entries())
				vacias = len(po.untranslated_entries())
				pct = po.percent_translated()
				nombre = IDIOMAS.get(idioma, idioma)
				lineas.append(
					f"{nombre} ({idioma}):\n"
					f"  Total: {total} | Traducidas: {trad} | "
					f"Fuzzy: {fuzzy} | Vacías: {vacias} | "
					f"Completado: {pct}%"
				)
			except Exception as e:
				lineas.append(f"{idioma}: Error - {e}")

		# Mostrar en diálogo con TextCtrl para poder copiar
		dlg = wx.Dialog(
			self, title="Estadísticas de traducción",
			size=(500, 400),
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
		)
		dsizer = wx.BoxSizer(wx.VERTICAL)
		txt = wx.TextCtrl(
			dlg,
			value="\n\n".join(lineas),
			style=wx.TE_MULTILINE | wx.TE_READONLY
		)
		dsizer.Add(txt, 1, wx.EXPAND | wx.ALL, 10)
		btn = wx.Button(dlg, wx.ID_OK, "&Cerrar")
		dsizer.Add(btn, 0, wx.ALIGN_CENTER | wx.BOTTOM, 10)
		dlg.SetSizer(dsizer)
		dlg.Centre()
		dlg.ShowModal()
		dlg.Destroy()

	def _on_h_limpiar(self, event):
		"""Elimina entradas obsoletas del archivo actual."""
		if not self._po:
			wx.MessageBox(
				"No hay archivo abierto.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		obsoletas = self._po.obsolete_entries()
		if not obsoletas:
			wx.MessageBox(
				"No hay entradas obsoletas.",
				"Aviso", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		r = wx.MessageBox(
			f"Se eliminarán {len(obsoletas)} entradas obsoletas.\n"
			"¿Continuar?",
			"Limpiar obsoletas",
			wx.YES_NO | wx.ICON_QUESTION, self
		)
		if r != wx.YES:
			return

		for entry in obsoletas:
			self._po.remove(entry)

		self._modificado = True
		self._cargar_lista()
		self._actualizar_titulo()
		self._actualizar_estado(
			f"{len(obsoletas)} entradas obsoletas eliminadas."
		)

	def _on_h_generar_todos(self, event):
		"""Genera, traduce y compila todos los idiomas que falten."""
		ruta_pot = self._buscar_pot()
		if not ruta_pot:
			return

		origen = self._idioma_origen()

		# Detectar idiomas existentes
		existentes = set()
		if os.path.isdir(_DIR_LOCALES):
			for nombre in os.listdir(_DIR_LOCALES):
				lc = os.path.join(_DIR_LOCALES, nombre, "LC_MESSAGES")
				if os.path.isdir(lc):
					existentes.add(nombre)

		# Calcular idiomas que faltan
		todos = set(IDIOMAS.keys())
		faltan = sorted(todos - existentes, key=lambda c: IDIOMAS[c])

		if not faltan:
			wx.MessageBox(
				f"Todos los {len(todos)} idiomas ya tienen directorio "
				"en locales/.\n\nNo hay nada que generar.",
				"Todo completo", wx.OK | wx.ICON_INFORMATION, self
			)
			return

		# Mostrar resumen previo
		pot = polib.pofile(ruta_pot)
		total_cadenas = len([e for e in pot if not e.obsolete])

		lineas_prev = []
		for c in faltan:
			lineas_prev.append(f"  {IDIOMAS[c]} ({c})")

		msg_prev = (
			f"Se crearán {len(faltan)} idiomas nuevos.\n"
			f"Cada uno con {total_cadenas} cadenas a traducir.\n"
			f"Idioma origen: {IDIOMAS.get(origen, origen)}\n\n"
			f"Existentes: {len(existentes)} | Nuevos: {len(faltan)}\n\n"
			+ "\n".join(lineas_prev[:15])
		)
		if len(faltan) > 15:
			msg_prev += f"\n  ... y {len(faltan) - 15} más"
		msg_prev += "\n\n¿Continuar? (Puede tardar varios minutos)"

		r = wx.MessageBox(
			msg_prev,
			"Generar todos los idiomas",
			wx.YES_NO | wx.ICON_QUESTION, self
		)
		if r != wx.YES:
			return

		total_entradas = len(faltan) * total_cadenas
		self.gauge.SetRange(max(total_entradas, 1))
		self.gauge.SetValue(0)
		self.gauge.Show()
		self.lista.Disable()
		self.txt_destino.Disable()
		self.Layout()

		def hilo():
			resultados = []  # (codigo, nombre, creado, traducidas, errores, pct, mo_ok)
			procesadas = 0

			for codigo in faltan:
				nombre_idioma = IDIOMAS.get(codigo, codigo)
				wx.CallAfter(
					self._actualizar_estado,
					f"Procesando {nombre_idioma} ({codigo})..."
				)

				# 1. Crear estructura de directorios
				lc_dir = os.path.join(
					_DIR_LOCALES, codigo, "LC_MESSAGES"
				)
				os.makedirs(lc_dir, exist_ok=True)

				ruta_po = os.path.join(lc_dir, "audiorecorder.po")
				ruta_mo = os.path.join(lc_dir, "audiorecorder.mo")

				# 2. Crear .po desde plantilla
				po = polib.pofile(ruta_pot)
				po.metadata['Language'] = codigo
				po.metadata['Content-Type'] = (
					'text/plain; charset=UTF-8'
				)
				po.save(ruta_po)
				# Recargar para trabajar con la copia
				po = polib.pofile(ruta_po)

				# 3. Traducir todas las entradas
				entradas = [e for e in po if not e.obsolete and e.msgid]
				mismo = (codigo == origen)
				errores_idioma = 0
				traducidas_idioma = 0

				for entrada in entradas:
					if mismo:
						entrada.msgstr = entrada.msgid
						traducidas_idioma += 1
					else:
						resultado = traducir(
							entrada.msgid, origen, codigo
						)
						if resultado:
							entrada.msgstr = resultado
							traducidas_idioma += 1
						else:
							errores_idioma += 1

					procesadas += 1
					wx.CallAfter(self.gauge.SetValue, procesadas)

				# 4. Guardar .po
				po.save(ruta_po)

				# 5. Compilar .mo
				mo_ok = True
				try:
					po.save_as_mofile(ruta_mo)
				except Exception:
					mo_ok = False

				pct = po.percent_translated()
				resultados.append((
					codigo, nombre_idioma, True,
					traducidas_idioma, errores_idioma,
					pct, mo_ok
				))

			wx.CallAfter(
				self._fin_generar_todos,
				resultados, len(existentes)
			)

		Thread(target=hilo, daemon=True).start()

	def _fin_generar_todos(self, resultados, existentes_prev):
		"""Muestra el informe final de la generación masiva."""
		self.gauge.Hide()
		self.lista.Enable()
		self.txt_destino.Enable()
		self.Layout()

		# Construir informe detallado
		total_trad = 0
		total_err = 0
		total_mo = 0
		lineas = []

		for (
			codigo, nombre, creado,
			traducidas, errores, pct, mo_ok
		) in resultados:
			total_trad += traducidas
			total_err += errores
			if mo_ok:
				total_mo += 1

			estado_mo = "✓" if mo_ok else "✗ ERROR"
			linea = (
				f"{nombre} ({codigo}):\n"
				f"  Traducidas: {traducidas} | "
				f"Errores: {errores} | "
				f"Completado: {pct}% | "
				f".mo: {estado_mo}"
			)
			lineas.append(linea)

		resumen = (
			f"═══ INFORME DE GENERACIÓN ═══\n\n"
			f"Idiomas previos: {existentes_prev}\n"
			f"Idiomas nuevos creados: {len(resultados)}\n"
			f"Total idiomas ahora: "
			f"{existentes_prev + len(resultados)}\n\n"
			f"─── Totales ───\n"
			f"Cadenas traducidas: {total_trad}\n"
			f"Cadenas con error: {total_err}\n"
			f"Archivos .mo generados: {total_mo}/{len(resultados)}\n\n"
			f"─── Detalle por idioma ───\n\n"
			+ "\n\n".join(lineas)
		)

		# Mostrar en diálogo con TextCtrl copiable
		dlg = wx.Dialog(
			self, title="Resultado — Generar todos los idiomas",
			size=(600, 500),
			style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
		)
		dsizer = wx.BoxSizer(wx.VERTICAL)
		txt = wx.TextCtrl(
			dlg, value=resumen,
			style=wx.TE_MULTILINE | wx.TE_READONLY
		)
		dsizer.Add(txt, 1, wx.EXPAND | wx.ALL, 10)
		btn = wx.Button(dlg, wx.ID_OK, "&Cerrar")
		dsizer.Add(btn, 0, wx.ALIGN_CENTER | wx.BOTTOM, 10)
		dlg.SetSizer(dsizer)
		dlg.Centre()
		dlg.ShowModal()
		dlg.Destroy()

		self._actualizar_estado(
			f"Generación completada: {len(resultados)} idiomas nuevos."
		)


# ── Punto de entrada ────────────────────────────────────────────────

def main():
	app = wx.App()
	frame = EditorPO()
	frame.Show()
	app.MainLoop()


if __name__ == "__main__":
	main()
