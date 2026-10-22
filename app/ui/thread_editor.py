# -*- coding: utf-8 -*-
"""
Editor avanzado de hilos para Mastodon.
Permite pegar textos largos, dividirlos, numerarlos, adjuntar imágenes por toot,
generar texto alternativo por IA, guardar borradores y publicar de forma secuencial.
"""

import os
import json
import logging
from datetime import datetime
import wx

from app.core.speech import speak, notificar
from app.core.config_manager import config
from app.core.task_manager import task_manager
from app.services.mastodon.mastodon_threading import MastodonThreadingManager, quitar_numeracion_hilo
from app.services.ai.ai_service import ai_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog

log = logging.getLogger(__name__)

class ThreadEditorDialog(wx.Dialog):
    """Diálogo del editor avanzado de hilos."""
    
    def __init__(self, parent, account_id: int, thread_id: str = None, inicial: dict = None):
        """`inicial` permite abrir el editor con lo que ya se estaba escribiendo en el compositor
        normal (cuando el texto se pasa del límite y se decide convertirlo en hilo). Claves:
        texto, visibilidad, cw, menciones, idioma y adjuntos [(ruta, alt), ...]."""
        super().__init__(
            parent,
            title=_("Editor Avanzado de Hilos Mastodon"),
            size=(750, 600),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo editor de hilos")
        )

        self.account_id = account_id
        self.thread_id = thread_id
        self._inicial = inicial or {}
        # Límite real de caracteres de la instancia (puede ser >500). Es el MÁXIMO por
        # publicación; el usuario puede elegir dividir en trozos más pequeños si quiere.
        from app.services.mastodon.mastodon_service import mastodon_service as _ms
        self._limite_instancia = _ms.obtener_limite_caracteres(account_id)
        
        # Estructura del hilo en memoria
        # Lista de dicts: {'content': str, 'media_paths': list, 'alt_texts': list, 'posted_status_id': str, 'posted_at': str}
        self.toots_data = []
        
        self._init_ui()
        
        if self.thread_id:
            self._cargar_hilo_existente()
        elif self._inicial:
            self._aplicar_estado_inicial()
        else:
            # Insertar un toot vacío inicial
            self._insertar_toot_vacio(0)

        self.CenterOnParent()
        # Foco en el cuadro de texto para empezar a escribir el hilo de inmediato.
        self.notebook.SetSelection(0)
        self.txt_largo.SetFocus()

        # Refrescar de la instancia (sin bloquear) el límite real de caracteres y el de la
        # descripción de adjuntos, para alinearnos con lo que marque el servidor (se cachean y
        # persisten en la misma llamada; así el alt-text usa el máximo correcto al editarlo).
        def _aplicar_limite(limite, err):
            if not self:
                return
            if not err and limite and limite != self._limite_instancia:
                self._limite_instancia = int(limite)
        _ms.actualizar_limite_caracteres_async(account_id, callback=_aplicar_limite)

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Notebook de pestañas (1. Composición de texto largo, 2. Estructura y previsualización de toots)
        self.notebook = wx.Notebook(self, name=_("Pestañas del editor"))
        
        # PESTAÑA 1: TEXTO LARGO
        self.tab_cuerpo = wx.Panel(self.notebook)
        sz_cuerpo = wx.BoxSizer(wx.VERTICAL)
        
        sz_cuerpo.Add(wx.StaticText(self.tab_cuerpo, label=_("Escriba o pegue el texto completo aquí:")), 0, wx.ALL, 5)
        
        self.txt_largo = AccessibleTextCtrl(
            self.tab_cuerpo,
            style=wx.TE_MULTILINE | wx.TE_RICH2,
            name=_("Texto completo del hilo"),
            ayuda_texto=_("Introduzca el texto completo de su hilo. Presione el botón 'Dividir texto' para procesarlo.")
        )
        # Redactar traduciendo (Ctrl+Mayús+T) e insertar fragmentos (Ctrl+Mayús+J), como en el resto
        # de campos de escritura.
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt_largo)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt_largo)
        sz_cuerpo.Add(self.txt_largo, 1, wx.EXPAND | wx.ALL, 5)
        
        # Parámetros de división
        sz_params = wx.BoxSizer(wx.HORIZONTAL)

        # Caracteres por publicación: por defecto el máximo de la instancia, pero el usuario
        # puede poner menos para trocear el hilo en más publicaciones (p. ej. 500 aunque la
        # instancia permita 10000). El tope es el límite de la instancia.
        sz_params.Add(wx.StaticText(self.tab_cuerpo, label=_("Caracteres por publicación:")),
                      0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        # Valor inicial: lo último que el usuario eligió (si cabe en esta instancia), o el
        # máximo de la instancia.
        _pref = 0
        try:
            _pref = int(config.get("mastodon", "thread_chars_per_post", 0) or 0)
        except (TypeError, ValueError):
            _pref = 0
        _inicial = _pref if (0 < _pref <= int(self._limite_instancia)) else int(self._limite_instancia)
        self.spin_chars = wx.SpinCtrl(
            self.tab_cuerpo, min=50, max=max(50, int(self._limite_instancia)),
            initial=_inicial, name=_("Caracteres por publicación"))
        self.spin_chars.SetToolTip(_("Máximo de caracteres de cada publicación del hilo. Por "
                                     "defecto es el límite de tu instancia ({0}); ponlo más bajo "
                                     "para dividir en más publicaciones.").format(self._limite_instancia))
        sz_params.Add(self.spin_chars, 0, wx.ALL, 5)

        sz_params.Add(wx.StaticText(self.tab_cuerpo, label=_("Numeración:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.choice_num = wx.Choice(
            self.tab_cuerpo,
            choices=[_("Sin numeración"), _("1/N"), _("(1/N)"), _("Hilo 1/N"), _("Parte 1 de N")],
            name=_("Selector de formato de numeración")
        )
        self.choice_num.SetSelection(1) # Default 1/N
        sz_params.Add(self.choice_num, 1, wx.EXPAND | wx.ALL, 5)
        
        self.btn_dividir = wx.Button(self.tab_cuerpo, label=_("&Dividir texto"), name=_("Botón dividir texto del hilo"))
        self.btn_dividir.Bind(wx.EVT_BUTTON, self._on_dividir_texto)
        sz_params.Add(self.btn_dividir, 0, wx.ALL, 5)

        self.btn_corregir = wx.Button(self.tab_cuerpo, label=_("Corre&gir ortografía"), name=_("Corregir ortografía del hilo"))
        self.btn_corregir.Bind(wx.EVT_BUTTON, self._on_corregir_ortografia)
        sz_params.Add(self.btn_corregir, 0, wx.ALL, 5)

        # Idioma del hilo (Mastodon guarda el idioma por publicación). Por defecto, el de la app.
        sz_params.Add(wx.StaticText(self.tab_cuerpo, label=_("&Idioma:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self._idiomas = [
            ("es", _("Español")), ("en", _("Inglés")), ("ca", _("Catalán")),
            ("gl", _("Gallego")), ("eu", _("Euskera")), ("pt", _("Portugués")),
            ("fr", _("Francés")), ("it", _("Italiano")), ("de", _("Alemán")),
        ]
        self.choice_idioma = wx.Choice(self.tab_cuerpo, choices=[n for _c, n in self._idiomas],
                                       name=_("Idioma del hilo"))
        _idioma_def = (config.get("general", "language", "es") or "es")
        _codigos = [c for c, _n in self._idiomas]
        self.choice_idioma.SetSelection(_codigos.index(_idioma_def) if _idioma_def in _codigos else 0)
        sz_params.Add(self.choice_idioma, 0, wx.ALL, 5)

        sz_cuerpo.Add(sz_params, 0, wx.EXPAND | wx.ALL, 5)

        # --- PRIVACIDAD DEL HILO ---
        # Mastodon admite la visibilidad POR PUBLICACIÓN, así que un hilo entero puede ser no
        # listado, solo para seguidores o directo (mensaje privado). Antes el editor publicaba
        # siempre en público, sin dejar elegir: era imposible mandar un hilo en privado.
        sz_priv = wx.BoxSizer(wx.HORIZONTAL)
        sz_priv.Add(wx.StaticText(self.tab_cuerpo, label=_("&Privacidad del hilo:")),
                    0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.priv_keys = ["public", "unlisted", "private", "direct"]
        self.choice_priv = wx.Choice(
            self.tab_cuerpo,
            choices=[_("Público"), _("No listado"), _("Solo seguidores"),
                     _("Directo (mensaje privado a los mencionados)")],
            name=_("Privacidad del hilo"))
        # Misma visibilidad por defecto que el compositor normal (la del servidor si está
        # configurado así, o la de la app), para no publicar el hilo con otro criterio.
        try:
            from app.services.mastodon.mastodon_service import mastodon_service as _msv
            _vis_def = _msv.visibilidad_predeterminada(self.account_id)
        except Exception:
            _vis_def = config.get("mastodon", "default_visibility", "public")
        self.choice_priv.SetSelection(self.priv_keys.index(_vis_def) if _vis_def in self.priv_keys else 0)
        self.choice_priv.Bind(wx.EVT_CHOICE, self._on_cambio_privacidad)
        sz_priv.Add(self.choice_priv, 0, wx.ALL, 5)

        sz_priv.Add(wx.StaticText(self.tab_cuerpo, label=_("&Destinatarios:")),
                    0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_menciones = AccessibleTextCtrl(
            self.tab_cuerpo, size=(220, -1), name=_("Destinatarios del hilo"),
            ayuda_texto=_("Cuentas a las que va dirigido el hilo, separadas por espacios o comas "
                          "(por ejemplo: @ana @luis@otra.social). En un hilo directo o solo para "
                          "seguidores se añaden a TODAS las publicaciones del hilo: Mastodon "
                          "entrega los mensajes directos únicamente a las cuentas mencionadas en "
                          "cada uno, así que sin esto solo recibirían el primero."))
        sz_priv.Add(self.txt_menciones, 1, wx.EXPAND | wx.ALL, 5)

        self.btn_menciones_texto = wx.Button(self.tab_cuerpo, label=_("Tomar del te&xto"),
                                             name=_("Tomar los destinatarios del texto"))
        self.btn_menciones_texto.SetToolTip(_("Rellena los destinatarios con las cuentas que ya "
                                              "hayas mencionado en el texto del hilo."))
        self.btn_menciones_texto.Bind(wx.EVT_BUTTON, self._on_menciones_del_texto)
        sz_priv.Add(self.btn_menciones_texto, 0, wx.ALL, 5)
        sz_cuerpo.Add(sz_priv, 0, wx.EXPAND | wx.ALL, 5)

        # --- AVISO DE CONTENIDO (CW) DEL HILO ---
        sz_cw = wx.BoxSizer(wx.HORIZONTAL)
        self.chk_cw = wx.CheckBox(self.tab_cuerpo, label=_("A&viso de contenido en todo el hilo"),
                                  name=_("Aviso de contenido del hilo"))
        self.chk_cw.Bind(wx.EVT_CHECKBOX, self._on_toggle_cw)
        sz_cw.Add(self.chk_cw, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_cw = AccessibleTextCtrl(self.tab_cuerpo, size=(300, -1),
                                         name=_("Texto del aviso de contenido"),
                                         ayuda_texto=_("Texto del aviso que verán antes de abrir "
                                                       "cada publicación del hilo."))
        self.txt_cw.Enable(False)
        sz_cw.Add(self.txt_cw, 1, wx.EXPAND | wx.ALL, 5)
        sz_cuerpo.Add(sz_cw, 0, wx.EXPAND | wx.ALL, 5)

        self.tab_cuerpo.SetSizer(sz_cuerpo)
        self.notebook.AddPage(self.tab_cuerpo, _("1. Texto Largo"))
        
        # PESTAÑA 2: TOOTS INDIVIDUALES Y PREVIEW
        self.tab_toots = wx.Panel(self.notebook)
        sz_toots = wx.BoxSizer(wx.VERTICAL)
        
        sz_toots.Add(wx.StaticText(self.tab_toots, label=_("Toots resultantes del hilo:")), 0, wx.ALL, 5)
        
        # Lista de toots
        self.lista_toots = AccessibleListCtrl(
            self.tab_toots,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de toots resultantes")
        )
        self.lista_toots.InsertColumn(0, _("Nº"), width=50)
        self.lista_toots.InsertColumn(1, _("Contenido del Toot"), width=400)
        self.lista_toots.InsertColumn(2, _("Adjuntos"), width=100)
        self.lista_toots.InsertColumn(3, _("Estado"), width=100)
        self.lista_toots.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_toot_selected)
        self.lista_toots.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_editar_toot_directo)
        
        sz_toots.Add(self.lista_toots, 1, wx.EXPAND | wx.ALL, 5)
        
        # Botonera de acciones por toot
        sz_acc_toots = wx.BoxSizer(wx.HORIZONTAL)
        
        self.btn_subir = wx.Button(self.tab_toots, label=_("&Subir"), name=_("Mover toot arriba"))
        self.btn_subir.Bind(wx.EVT_BUTTON, self._on_subir_toot)
        sz_acc_toots.Add(self.btn_subir, 0, wx.ALL, 2)

        self.btn_bajar = wx.Button(self.tab_toots, label=_("&Bajar"), name=_("Mover toot abajo"))
        self.btn_bajar.Bind(wx.EVT_BUTTON, self._on_bajar_toot)
        sz_acc_toots.Add(self.btn_bajar, 0, wx.ALL, 2)

        self.btn_add = wx.Button(self.tab_toots, label=_("&Insertar"), name=_("Añadir toot vacío"))
        self.btn_add.Bind(wx.EVT_BUTTON, self._on_insertar_toot)
        sz_acc_toots.Add(self.btn_add, 0, wx.ALL, 2)

        self.btn_del = wx.Button(self.tab_toots, label=_("&Eliminar"), name=_("Eliminar toot"))
        self.btn_del.Bind(wx.EVT_BUTTON, self._on_eliminar_toot)
        sz_acc_toots.Add(self.btn_del, 0, wx.ALL, 2)

        self.btn_edit = wx.Button(self.tab_toots, label=_("Edi&tar texto"), name=_("Editar toot seleccionado"))
        self.btn_edit.Bind(wx.EVT_BUTTON, self._on_editar_toot_manual)
        sz_acc_toots.Add(self.btn_edit, 0, wx.ALL, 2)

        self.btn_merge = wx.Button(self.tab_toots, label=_("&Fusionar"), name=_("Fusionar con toot siguiente"))
        self.btn_merge.Bind(wx.EVT_BUTTON, self._on_fusionar_siguiente)
        sz_acc_toots.Add(self.btn_merge, 0, wx.ALL, 2)
        
        sz_toots.Add(sz_acc_toots, 0, wx.ALIGN_CENTER | wx.ALL, 2)
        
        # Panel de multimedia para el toot seleccionado
        sb_media = wx.StaticBox(self.tab_toots, label=_("Multimedia del toot seleccionado"))
        sz_media = wx.StaticBoxSizer(sb_media, wx.VERTICAL)
        
        self.lbl_media_status = wx.StaticText(self.tab_toots, label=_("Sin archivos adjuntos."))
        sz_media.Add(self.lbl_media_status, 0, wx.ALL, 5)
        
        sz_media_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_add_media = wx.Button(self.tab_toots, label=_("Ad&juntar archivo..."), name=_("Adjuntar imagen o vídeo"))
        self.btn_add_media.Bind(wx.EVT_BUTTON, self._on_adjuntar_archivo)
        sz_media_btns.Add(self.btn_add_media, 0, wx.ALL, 2)

        self.btn_alt_ia = wx.Button(self.tab_toots, label=_("&Alt-Text con IA"), name=_("Generar texto alternativo con Gemini"))
        self.btn_alt_ia.Bind(wx.EVT_BUTTON, self._on_generar_alt_ia)
        sz_media_btns.Add(self.btn_alt_ia, 0, wx.ALL, 2)

        self.btn_alt_manual = wx.Button(self.tab_toots, label=_("Alt-Text &manual"), name=_("Escribir descripción manualmente"))
        self.btn_alt_manual.Bind(wx.EVT_BUTTON, self._on_escribir_alt_manual)
        sz_media_btns.Add(self.btn_alt_manual, 0, wx.ALL, 2)

        self.btn_clear_media = wx.Button(self.tab_toots, label=_("&Limpiar adjuntos"), name=_("Quitar archivos del toot"))
        self.btn_clear_media.Bind(wx.EVT_BUTTON, self._on_limpiar_adjuntos)
        sz_media_btns.Add(self.btn_clear_media, 0, wx.ALL, 2)
        
        sz_media.Add(sz_media_btns, 0, wx.EXPAND | wx.ALL, 2)
        sz_toots.Add(sz_media, 0, wx.EXPAND | wx.ALL, 5)
        
        self.tab_toots.SetSizer(sz_toots)
        self.notebook.AddPage(self.tab_toots, _("2. Estructura del Hilo"))
        
        sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 10)
        
        # Botones de Acción inferior (Borradores, Import/Export, Publicar)
        sz_bottom = wx.BoxSizer(wx.HORIZONTAL)
        
        # Borradores e importación/exportación
        self.btn_borrador = wx.Button(self, label=_("&Guardar borrador"), name=_("Guardar borrador de hilo"))
        self.btn_borrador.Bind(wx.EVT_BUTTON, self._on_guardar_borrador)
        sz_bottom.Add(self.btn_borrador, 0, wx.ALL, 5)

        self.btn_importar = wx.Button(self, label=_("&Importar..."), name=_("Importar hilo desde archivo"))
        self.btn_importar.Bind(wx.EVT_BUTTON, self._on_importar_archivo)
        sz_bottom.Add(self.btn_importar, 0, wx.ALL, 5)

        self.btn_exportar = wx.Button(self, label=_("&Exportar..."), name=_("Exportar hilo a archivo"))
        self.btn_exportar.Bind(wx.EVT_BUTTON, self._on_exportar_archivo)
        sz_bottom.Add(self.btn_exportar, 0, wx.ALL, 5)

        sz_bottom.AddStretchSpacer()

        self.btn_publicar = wx.Button(self, label=_("&Publicar Hilo"), name=_("Enviar hilo secuencial a Mastodon"))
        self.btn_publicar.Bind(wx.EVT_BUTTON, self._on_publicar_hilo)
        sz_bottom.Add(self.btn_publicar, 0, wx.ALL, 5)

        self.btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"), name=_("Cerrar editor"))
        sz_bottom.Add(self.btn_cerrar, 0, wx.ALL, 5)
        
        sizer.Add(sz_bottom, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(sizer)
        
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_page_changed)

    def _on_page_changed(self, event):
        page_text = self.notebook.GetPageText(event.GetSelection())
        speak(f"Pestaña {page_text}", interrupt=True)

    # --- PRIVACIDAD DEL HILO ---
    def _visibilidad_elegida(self) -> str:
        idx = self.choice_priv.GetSelection()
        return self.priv_keys[idx] if 0 <= idx < len(self.priv_keys) else "public"

    def _menciones_hilo(self) -> str:
        """Destinatarios normalizados ("@ana @luis") que deben ir en CADA publicación. Solo se
        aplican a los hilos privados o directos: en los públicos, repetir menciones sería spam."""
        if self._visibilidad_elegida() not in ("private", "direct"):
            return ""
        from app.services.mastodon.mastodon_threading import normalizar_menciones
        return normalizar_menciones(self.txt_menciones.GetValue())

    def _cw_hilo(self) -> str:
        return self.txt_cw.GetValue().strip() if self.chk_cw.IsChecked() else ""

    def _on_toggle_cw(self, event):
        activo = self.chk_cw.IsChecked()
        self.txt_cw.Enable(activo)
        if activo:
            self.txt_cw.SetFocus()

    def _on_cambio_privacidad(self, event):
        """Al elegir un hilo directo, rellenar los destinatarios con las menciones que ya haya en
        el texto (lo habitual: estás respondiendo a alguien) y explicar qué implica."""
        vis = self._visibilidad_elegida()
        if vis in ("private", "direct") and not self.txt_menciones.GetValue().strip():
            self._on_menciones_del_texto(None, silencioso=True)
        if vis == "direct":
            speak(_("Hilo directo: solo lo verán las cuentas que pongas en Destinatarios, que se "
                    "añadirán a todas las publicaciones del hilo."), interrupt=True)

    def _on_menciones_del_texto(self, event, silencioso=False):
        from app.services.mastodon.mastodon_threading import menciones_de_texto
        encontradas = menciones_de_texto(self.txt_largo.GetValue())
        if not encontradas:
            if not silencioso:
                speak(_("En el texto del hilo no hay ninguna mención."), interrupt=True)
            return
        self.txt_menciones.SetValue(encontradas)
        if not silencioso:
            speak(_("Destinatarios: {0}.").format(encontradas), interrupt=True)
        event.Skip()

    def _insertar_toot_vacio(self, index: int):
        self.toots_data.insert(index, {
            "content": "",
            "media_paths": [],
            "alt_texts": [],
            "posted_status_id": None,
            "posted_at": None
        })

    def _aplicar_estado_inicial(self):
        """Rellena el editor con lo que venía del compositor normal (texto, privacidad, aviso de
        contenido, destinatarios, idioma y adjuntos) y ya lo divide, para seguir donde se iba.

        Las cuentas mencionadas se recogen del propio texto: si el usuario estaba escribiendo una
        respuesta o un mensaje directo, esas menciones son los destinatarios del hilo."""
        ini = self._inicial
        texto = (ini.get("texto") or "").strip()
        self.txt_largo.SetValue(texto)

        vis = ini.get("visibilidad") or ""
        if vis in self.priv_keys:
            self.choice_priv.SetSelection(self.priv_keys.index(vis))
        cw = (ini.get("cw") or "").strip()
        if cw:
            self.chk_cw.SetValue(True)
            self.txt_cw.Enable(True)
            self.txt_cw.SetValue(cw)
        from app.services.mastodon.mastodon_threading import menciones_de_texto, normalizar_menciones
        menciones = normalizar_menciones(ini.get("menciones") or "") or menciones_de_texto(texto)
        self.txt_menciones.SetValue(menciones)
        idioma = ini.get("idioma")
        if idioma:
            codigos = [c for c, _n in self._idiomas]
            if idioma in codigos:
                self.choice_idioma.SetSelection(codigos.index(idioma))

        if texto:
            self._on_dividir_texto(None)
        if not self.toots_data:
            self._insertar_toot_vacio(0)

        # Los adjuntos que ya había en el compositor se quedan en la PRIMERA publicación del hilo
        # (con su texto alternativo), que es donde estaban pensados.
        adjuntos = ini.get("adjuntos") or []
        if adjuntos and self.toots_data:
            self.toots_data[0]["media_paths"] = [a[0] for a in adjuntos]
            self.toots_data[0]["alt_texts"] = [(a[1] or "") for a in adjuntos]
            self._render_lista_toots()

    def _cargar_hilo_existente(self):
        hilo = MastodonThreadingManager.cargar_hilo(self.thread_id)
        if hilo:
            self.toots_data = hilo["toots"]
            # Reconstruir el texto plano SIN la numeración de hilo, para que si se
            # vuelve a dividir no se duplique (p. ej. "5/5 5/5").
            textos = [quitar_numeracion_hilo(t["content"]) for t in self.toots_data]
            self.txt_largo.SetValue("\n\n---\n\n".join(textos))
            # Restaurar la privacidad con la que se guardó el hilo (un hilo directo debe seguir
            # siendo directo al retomarlo), su aviso de contenido y sus destinatarios.
            vis = hilo.get("visibility") or "public"
            if vis in self.priv_keys:
                self.choice_priv.SetSelection(self.priv_keys.index(vis))
            cw = hilo.get("spoiler_text") or ""
            if cw:
                self.chk_cw.SetValue(True)
                self.txt_cw.Enable(True)
                self.txt_cw.SetValue(cw)
            self.txt_menciones.SetValue(hilo.get("mentions") or "")
            self._render_lista_toots()

    def _render_lista_toots(self):
        self.lista_toots.DeleteAllItems()
        self.lista_toots.limpiar_lecturas()
        
        for idx, toot in enumerate(self.toots_data):
            num = str(idx + 1)
            content = toot["content"].replace("\n", " ").strip()
            
            # Formato de adjuntos
            adj_cnt = len(toot.get("media_paths", []))
            adj_str = f"{adj_cnt} adjuntos" if adj_cnt else _("Ninguno")
            
            # Estado de envío
            estado = _("Enviado") if toot.get("posted_status_id") else _("Pendiente")
            
            pos = self.lista_toots.InsertItem(idx, num)
            self.lista_toots.SetItem(pos, 1, content)
            self.lista_toots.SetItem(pos, 2, adj_str)
            self.lista_toots.SetItem(pos, 3, estado)
            
            # Anuncio de voz
            speech_desc = f"Toot {num}: {content}. {adj_str}. {estado}."
            self.lista_toots.establecer_lectura(pos, speech_desc)

    # --- ACCIONES DE TEXTO ---
    def _on_corregir_ortografia(self, event):
        """Corrige la ortografía del cuerpo del hilo."""
        from app.services import spellcheck
        if not spellcheck.disponible():
            speak(_("La corrección ortográfica no está disponible."), interrupt=True)
            return
        from .spellcheck_dialog import SpellCheckDialog
        dlg = SpellCheckDialog(self, self.txt_largo.GetValue())
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_largo.SetValue(dlg.get_texto())
            notificar(_("Corrección aplicada."))
        dlg.Destroy()

    def _on_dividir_texto(self, event):
        text = self.txt_largo.GetValue()
        if not text.strip():
            speak(_("No hay texto para dividir."), interrupt=True)
            return

        # Si el texto está separado en bloques (p. ej. un hilo reconstruido con
        # separadores "---"), quitar la numeración del final de cada bloque para no
        # duplicarla al volver a dividir.
        if "\n---\n" in text or "\n\n---\n\n" in text:
            bloques = [quitar_numeracion_hilo(b.strip()) for b in text.split("---")]
            text = "\n\n".join(b for b in bloques if b)
            
        formato_opcs = ["sin", "simple", "parentesis", "hilo", "parte"]
        formato = formato_opcs[self.choice_num.GetSelection()]
        
        # Caracteres por publicación elegidos por el usuario (por defecto el de la instancia),
        # nunca por encima del máximo real de la instancia.
        try:
            limite_elegido = int(self.spin_chars.GetValue())
        except Exception:
            limite_elegido = self._limite_instancia
        limite_max = max(50, min(limite_elegido, int(self._limite_instancia)))
        # Recordar la elección del usuario para la próxima vez.
        try:
            config.set("mastodon", "thread_chars_per_post", limite_elegido)
        except Exception:
            pass
        # Las menciones de un hilo directo/privado y el aviso de contenido ocupan sitio en TODAS
        # las publicaciones: se pasan a la división para que las reserve y ninguna parte se pase
        # del límite (si no, el servidor rechazaría el hilo a media publicación).
        menciones = self._menciones_hilo()
        # Si los destinatarios ya están escritos al principio del texto, se quitan de ahí: los
        # pondrá la división en cada parte y, si no, saldrían duplicados en la primera.
        if menciones:
            cabecera = text.lstrip()
            for m in menciones.split():
                if cabecera.lower().startswith(m.lower() + " "):
                    cabecera = cabecera[len(m):].lstrip()
            text = cabecera
        toots = MastodonThreadingManager.dividir_hilo(
            text,
            limite_max=limite_max,
            margen=15,
            numerar=(formato != "sin"),
            formato=formato,
            cw=self._cw_hilo(),
            menciones=menciones
        )
        
        # Mapear los contenidos resultantes, preservando adjuntos previos si existían
        nuevos_toots = []
        for idx, c in enumerate(toots):
            adj_previos = []
            alts_previas = []
            # Intentar rescatar adjuntos del toot que estaba en esta posición
            if idx < len(self.toots_data):
                adj_previos = self.toots_data[idx].get("media_paths", [])
                alts_previas = self.toots_data[idx].get("alt_texts", [])
                
            nuevos_toots.append({
                "content": c,
                "media_paths": adj_previos,
                "alt_texts": alts_previas,
                "posted_status_id": None,
                "posted_at": None
            })
            
        self.toots_data = nuevos_toots
        self._render_lista_toots()
        speak(f"Texto dividido en {len(self.toots_data)} toots. Pestaña de estructura activa.", interrupt=True)
        self.notebook.SetSelection(1)

    # --- EDICIÓN Y MOVIMIENTO DE FILAS ---
    def _on_toot_selected(self, event):
        idx = event.GetIndex()
        if idx < 0 or idx >= len(self.toots_data):
            return
            
        toot = self.toots_data[idx]
        paths = toot.get("media_paths", [])
        if paths:
            adjuntos_nombres = [os.path.basename(p) for p in paths]
            self.lbl_media_status.SetLabel(f"Adjuntos: {', '.join(adjuntos_nombres)}")
        else:
            self.lbl_media_status.SetLabel(_("Sin archivos adjuntos."))

    def _on_editar_toot_directo(self, event):
        self._on_editar_toot_manual(None)

    def _on_editar_toot_manual(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx < 0 or idx >= len(self.toots_data):
            return
            
        toot = self.toots_data[idx]
        
        dlg = CustomTextEntryDialog(
            self, 
            message=f"Editar texto del Toot {idx + 1}:",
            title=_("Modificar toot"), 
            value=toot["content"]
        )
        if dlg.ShowModal() == wx.ID_OK:
            toot["content"] = dlg.GetValue()
            self._render_lista_toots()
            speak(_("Toot editado."), interrupt=True)
        dlg.Destroy()

    def _on_subir_toot(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx > 0 and idx < len(self.toots_data):
            self.toots_data[idx], self.toots_data[idx - 1] = self.toots_data[idx - 1], self.toots_data[idx]
            self._render_lista_toots()
            self.lista_toots.Focus(idx - 1)
            self.lista_toots.Select(idx - 1)
            speak(_("Toot movido hacia arriba."), interrupt=True)

    def _on_bajar_toot(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx >= 0 and idx < len(self.toots_data) - 1:
            self.toots_data[idx], self.toots_data[idx + 1] = self.toots_data[idx + 1], self.toots_data[idx]
            self._render_lista_toots()
            self.lista_toots.Focus(idx + 1)
            self.lista_toots.Select(idx + 1)
            speak(_("Toot movido hacia abajo."), interrupt=True)

    def _on_insertar_toot(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx < 0:
            idx = len(self.toots_data)
        else:
            idx = idx + 1
            
        self._insertar_toot_vacio(idx)
        self._render_lista_toots()
        self.lista_toots.Focus(idx)
        self.lista_toots.Select(idx)
        notificar(_("Toot vacío insertado."))

    def _on_eliminar_toot(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx < 0 or idx >= len(self.toots_data):
            return
            
        self.toots_data.pop(idx)
        if not self.toots_data:
            self._insertar_toot_vacio(0)
            
        self._render_lista_toots()
        notificar(_("Toot eliminado."))

    def _on_fusionar_siguiente(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx >= 0 and idx < len(self.toots_data) - 1:
            actual = self.toots_data[idx]
            siguiente = self.toots_data[idx + 1]
            
            actual["content"] = (actual["content"].strip() + " " + siguiente["content"].strip()).strip()
            # Combinar multimedia
            actual["media_paths"].extend(siguiente.get("media_paths", []))
            actual["alt_texts"].extend(siguiente.get("alt_texts", []))
            
            self.toots_data.pop(idx + 1)
            self._render_lista_toots()
            speak(_("Toots fusionados."), interrupt=True)

    # --- ACCIONES MULTIMEDIA ---
    def _on_adjuntar_archivo(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx < 0 or idx >= len(self.toots_data):
            speak(_("Enfoque el toot al que desea adjuntar el archivo."), interrupt=True)
            return
            
        toot = self.toots_data[idx]
        if len(toot["media_paths"]) >= 4:
            notificar(_("No se pueden añadir más de 4 adjuntos por toot."))
            return

        # Extensiones OFICIALES de Mastodon, agrupadas por tipo (media_attachment.rb).
        ext_img = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif")
        ext_vid = (".webm", ".mp4", ".m4v", ".mov")
        ext_aud = (".ogg", ".oga", ".mp3", ".wav", ".flac", ".opus", ".aac", ".m4a", ".3gp", ".wma")
        comodin = ";".join("*{0}".format(e) for e in (ext_img + ext_vid + ext_aud))
        dlg = wx.FileDialog(
            self,
            message=_("Seleccionar archivo para adjuntar"),
            wildcard="|".join([
                "{0} ({1})|{1}".format(_("Archivos multimedia admitidos"), comodin),
                "{0} (*.*)|*.*".format(_("Todos los archivos")),
            ]),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            from app.services.mastodon.mastodon_composer import tipo_de_adjunto
            tipo = tipo_de_adjunto(path)
            if not tipo:
                speak(_("Ese tipo de archivo no lo admite Mastodon como adjunto."), interrupt=True)
                dlg.Destroy()
                return
            # Regla oficial de combinación: el audio y el vídeo deben ir solos (no mezclar con nada).
            tipos = [tipo_de_adjunto(p) for p in toot["media_paths"]]
            hay_av = any(t in ("audio", "video") for t in tipos)
            if tipo in ("audio", "video") and toot["media_paths"]:
                notificar(_("El audio y el vídeo deben ir solos: no se pueden combinar con otros archivos."))
                dlg.Destroy()
                return
            if tipo == "image" and hay_av:
                notificar(_("No se pueden añadir imágenes a un toot que ya lleva audio o vídeo."))
                dlg.Destroy()
                return
            toot["media_paths"].append(path)
            toot["alt_texts"].append("")
            self._render_lista_toots()
            self._on_toot_selected(wx.ListEvent(wx.wxEVT_COMMAND_LIST_ITEM_SELECTED, self.lista_toots.GetId()))
            speak(_("Archivo adjuntado."), interrupt=True)
        dlg.Destroy()

    def _on_generar_alt_ia(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx < 0 or idx >= len(self.toots_data):
            return
            
        toot = self.toots_data[idx]
        paths = toot["media_paths"]
        if not paths:
            speak(_("Este toot no tiene archivos adjuntos."), interrupt=True)
            return
            
        speak(_("Generando texto alternativo con IA..."), interrupt=True)
        
        # Tomar la primera imagen
        path = paths[0]
        
        def _bg_alt(ctx):
            return ai_service.generar_texto_alternativo(path)
            
        def _finish(res, err):
            if err:
                notificar(f"Error al generar texto alternativo: {err}")
            else:
                toot["alt_texts"][0] = res
                speak(f"Texto alternativo autogenerado: {res}", interrupt=True)
                
        task_manager.start_task(_bg_alt, callback=_finish, pass_context=True)

    def _on_escribir_alt_manual(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx < 0 or idx >= len(self.toots_data):
            return
            
        toot = self.toots_data[idx]
        paths = toot["media_paths"]
        if not paths:
            speak(_("Este toot no tiene archivos adjuntos."), interrupt=True)
            return
            
        alt_actual = toot["alt_texts"][0] if toot["alt_texts"] else ""

        from app.services.mastodon.mastodon_service import mastodon_service as _ms2
        limite = int(_ms2.obtener_limite_descripcion(self.account_id) or 1500)
        dlg = CustomTextEntryDialog(
            self,
            message=_("Escriba la descripción del adjunto (máximo {0} caracteres):").format(limite),
            title=_("Texto alternativo"),
            value=alt_actual,
            multiline=True,
            max_length=limite
        )
        if dlg.ShowModal() == wx.ID_OK:
            toot["alt_texts"][0] = dlg.GetValue()[:limite]
            notificar(_("Texto alternativo guardado."))
        dlg.Destroy()

    def _on_limpiar_adjuntos(self, event):
        idx = self.lista_toots.GetFocusedItem()
        if idx < 0 or idx >= len(self.toots_data):
            return
            
        self.toots_data[idx]["media_paths"].clear()
        self.toots_data[idx]["alt_texts"].clear()
        self._render_lista_toots()
        self.lbl_media_status.SetLabel(_("Sin archivos adjuntos."))
        notificar(_("Adjuntos eliminados."))

    # --- BORRADORES ---
    def _on_guardar_borrador(self, event):
        dlg = CustomTextEntryDialog(
            self, 
            message=_("Introduzca un título descriptivo para este borrador de hilo:"),
            title=_("Guardar borrador de hilo"), 
            value=self.thread_id or _("Mi Hilo del ") + datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        if dlg.ShowModal() == wx.ID_OK:
            titulo = dlg.GetValue().strip()
            if not titulo:
                titulo = _("Borrador sin título")
                
            # El borrador conserva también la privacidad, el aviso de contenido y los
            # destinatarios: al retomarlo se publica como se pensó, no en público.
            self.thread_id = MastodonThreadingManager.guardar_hilo(
                self.thread_id,
                titulo,
                self.account_id,
                self.toots_data,
                visibility=self._visibilidad_elegida(),
                spoiler_text=self._cw_hilo(),
                mentions=self._menciones_hilo()
            )
            notificar(_("Hilo guardado como borrador con éxito."))
        dlg.Destroy()

    def _on_importar_archivo(self, event):
        # Diálogo para abrir un archivo TXT/JSON/Markdown
        dlg = wx.FileDialog(
            self, 
            message=_("Seleccionar archivo para importar"),
            wildcard="Archivos de texto (*.txt;*.md;*.json)|*.txt;*.md;*.json",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            ext = os.path.splitext(path.lower())[1]
            try:
                if ext == ".json":
                    with open(path, "r", encoding="utf-8") as f:
                        self.toots_data = json.load(f)
                    self._render_lista_toots()
                else:
                    # TXT / Markdown
                    with open(path, "r", encoding="utf-8") as f:
                        text = f.read()
                    self.txt_largo.SetValue(text)
                    self.notebook.SetSelection(0)
                notificar(_("Hilo importado correctamente."))
            except Exception as e:
                notificar(f"Error al importar archivo: {e}")
        dlg.Destroy()

    def _on_exportar_archivo(self, event):
        # Diálogo para guardar a TXT/JSON/Markdown
        dlg = wx.FileDialog(
            self, 
            message=_("Exportar hilo como"),
            wildcard="Archivo Markdown (*.md)|*.md|Archivo JSON (*.json)|*.json|Archivo de texto (*.txt)|*.txt",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            ext = os.path.splitext(path.lower())[1]
            try:
                if ext == ".json":
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(self.toots_data, f, indent=4, ensure_ascii=False)
                elif ext == ".md":
                    # Markdown export con divisores
                    content = "\n\n---\n\n".join([t["content"] for t in self.toots_data])
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                else:
                    content = "\n\n========\n\n".join([t["content"] for t in self.toots_data])
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                notificar(_("Hilo exportado correctamente."))
            except Exception as e:
                notificar(f"Error al exportar archivo: {e}")
        dlg.Destroy()

    # --- PUBLICACIÓN ---
    def _on_publicar_hilo(self, event):
        # Si el usuario escribió el texto pero no pulsó "Dividir texto", dividirlo
        # automáticamente ahora para que "Publicar" funcione sin pasos previos.
        if not self.toots_data and self.txt_largo.GetValue().strip():
            self._on_dividir_texto(None)
        if not self.toots_data:
            notificar(_("El hilo está vacío. Escribe el texto del hilo antes de publicar."))
            return

        visibilidad = self._visibilidad_elegida()
        menciones = self._menciones_hilo()
        cw = self._cw_hilo()

        # Un hilo DIRECTO sin destinatarios no lo recibiría nadie: Mastodon entrega los mensajes
        # directos solo a las cuentas mencionadas. Se avisa y no se publica.
        if visibilidad == "direct" and not menciones:
            from .accessible_controls import mostrar_info
            mostrar_info(self, _(
                "Un hilo directo solo lo reciben las cuentas mencionadas en él, así que necesitas "
                "indicar al menos un destinatario. Escríbelo en «Destinatarios» (por ejemplo "
                "@ana@instancia.social) o usa «Tomar del texto»."), _("Falta el destinatario"))
            self.notebook.SetSelection(0)
            self.txt_menciones.SetFocus()
            return

        # Garantizar la mención en CADA publicación del hilo: si el usuario editó, insertó o
        # fusionó mensajes a mano después de dividir, alguno podría haberse quedado sin ella y no
        # le llegaría al destinatario.
        if menciones:
            from app.services.mastodon.mastodon_threading import asegurar_menciones
            for toot in self.toots_data:
                toot["content"] = asegurar_menciones(toot.get("content", ""), menciones)
            self._render_lista_toots()

        # 1. Guardar el hilo en la base de datos antes de publicar (para poder
        #    reanudarlo si algo falla). Si falla aquí, avisar y no continuar.
        try:
            # NOTA: wx.GetLocalTime() NO existe en wxPython 4.2; usar datetime.
            temp_title = _("Hilo temporal de envío ") + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.thread_id = MastodonThreadingManager.guardar_hilo(
                self.thread_id, temp_title, self.account_id, self.toots_data,
                visibility=visibilidad, spoiler_text=cw, mentions=menciones)
        except Exception as e:
            notificar(_("No se pudo preparar el hilo para publicar: {0}").format(e))
            return

        # 2. Cerrar el diálogo y publicar en SEGUNDO PLANO (sin un diálogo de
        #    progreso modal anidado, que podía bloquear el envío). El resultado se
        #    anuncia por voz. Capturamos los datos en locales porque el diálogo se
        #    destruye al cerrarse y el callback no puede usar 'self'.
        thread_id = self.thread_id
        account_id = self.account_id
        n_toots = len(self.toots_data)
        idx_idioma = self.choice_idioma.GetSelection()
        idioma = self._idiomas[idx_idioma][0] if 0 <= idx_idioma < len(self._idiomas) else None

        etiqueta_vis = self.choice_priv.GetStringSelection()

        def _callback(success, error_msg):
            if success:
                notificar(_("Hilo de {0} publicaciones publicado como «{1}».").format(
                    n_toots, etiqueta_vis))
                try:
                    MastodonThreadingManager.eliminar_hilo(thread_id)
                except Exception:
                    pass
            else:
                notificar(_("Error al publicar el hilo: {0}. Puedes reanudarlo desde Ver Hilos Guardados.").format(error_msg))

        speak(_("Publicando el hilo en segundo plano..."), interrupt=True)
        MastodonThreadingManager().publicar_hilo_async(
            account_id, thread_id, visibilidad=visibilidad,
            sensitive=bool(cw), spoiler_text=(cw or None),
            language=idioma, callback=_callback)
        self.EndModal(wx.ID_OK)


class HilosLocalesDialog(wx.Dialog):
    """Diálogo para administrar hilos locales guardados."""
    
    def __init__(self, parent, account_id: int):
        super().__init__(
            parent,
            title=_("Hilos Locales Guardados"),
            size=(500, 400),
            style=wx.DEFAULT_DIALOG_STYLE,
            name=_("Diálogo hilos guardados")
        )
        
        self.account_id = account_id
        self.selected_thread_id = None
        
        self._init_ui()
        self._cargar_hilos()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        sizer.Add(wx.StaticText(self, label=_("Hilos registrados:")), 0, wx.ALL, 5)
        
        self.lista_hilos = AccessibleListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de hilos locales")
        )
        self.lista_hilos.InsertColumn(0, _("Título"), width=300)
        self.lista_hilos.InsertColumn(1, _("Actualizado"), width=150)
        
        sizer.Add(self.lista_hilos, 1, wx.EXPAND | wx.ALL, 5)
        
        # Botones
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.btn_abrir = wx.Button(self, wx.ID_OK, _("&Editar / Reanudar"), name=_("Abrir hilo seleccionado"))
        self.btn_abrir.Bind(wx.EVT_BUTTON, self._on_abrir)
        self.btn_eliminar = wx.Button(self, wx.ID_DELETE, _("E&liminar"), name=_("Eliminar hilo seleccionado"))
        self.btn_eliminar.Bind(wx.EVT_BUTTON, self._on_eliminar)

        btn_sizer.Add(self.btn_abrir, 0, wx.ALL, 5)
        btn_sizer.Add(self.btn_eliminar, 0, wx.ALL, 5)
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)

        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizer(sizer)

        self.lista_hilos.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_selected)
        # Doble clic o Intro sobre un hilo también lo abre.
        self.lista_hilos.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_abrir)

    def _on_abrir(self, event):
        """Confirma la apertura del hilo enfocado (Editar / Reanudar)."""
        idx = self.lista_hilos.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.hilos_rows):
            speak(_("Seleccione un hilo de la lista."), interrupt=True)
            return
        self.selected_thread_id = self.hilos_rows[idx]["id"]
        self.EndModal(wx.ID_OK)

    def _cargar_hilos(self):
        self.lista_hilos.DeleteAllItems()
        self.lista_hilos.limpiar_lecturas()
        self.hilos_rows = MastodonThreadingManager.obtener_hilos(self.account_id)
        
        for idx, h in enumerate(self.hilos_rows):
            pos = self.lista_hilos.InsertItem(idx, h["title"])
            self.lista_hilos.SetItem(pos, 1, h["updated_at"][:16].replace("T", " "))
            
            self.lista_hilos.establecer_lectura(pos, f"Hilo: {h['title']}. Actualizado {h['updated_at'][:16]}.")

    def _on_selected(self, event):
        idx = event.GetIndex()
        if idx >= 0 and idx < len(self.hilos_rows):
            self.selected_thread_id = self.hilos_rows[idx]["id"]

    def _on_eliminar(self, event):
        idx = self.lista_hilos.GetFocusedItem()
        if idx < 0 or idx >= len(self.hilos_rows):
            return
            
        tid = self.hilos_rows[idx]["id"]
        title = self.hilos_rows[idx]["title"]
        
        dlg = wx.MessageDialog(
            self, 
            _("¿Seguro que desea eliminar el borrador de hilo '{title}'?").format(title=title),
            _("Confirmar eliminación"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
        )
        if dlg.ShowModal() == wx.ID_YES:
            MastodonThreadingManager.eliminar_hilo(tid)
            self._cargar_hilos()
            notificar(_("Hilo eliminado."))
        dlg.Destroy()
