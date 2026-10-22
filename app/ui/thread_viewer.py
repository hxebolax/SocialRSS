# -*- coding: utf-8 -*-
"""
Visor de conversaciones e hilos de Mastodon.
Carga el contexto del toot y permite leer la cadena cronológica completa de respuestas,
traducir el hilo completo, resumirlo con IA y exportarlo a distintos formatos.
"""

import json
import logging
import wx

from app.core.speech import speak, notificar
from app.core.database import db
from app.core.task_manager import task_manager
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.ai.ai_service import ai_service
from app.services.translation.translation_service import translation_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl

log = logging.getLogger(__name__)

class ThreadViewerDialog(wx.Dialog):
    """Visor accesible de hilos y conversaciones."""
    
    def __init__(self, parent, account_id: int, status_db_id: str, status_id: str = None):
        super().__init__(
            parent,
            title=_("Visor de Conversación de Mastodon"),
            size=(750, 550),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo visor de conversación")
        )
        
        self.frame = parent  # MainFrame: se usa para interactuar con los mensajes del hilo
        self.account_id = account_id
        self.status_db_id = status_db_id
        # Id del SERVIDOR para abrir una publicación que no está en la base local (la citada
        # por un toot, una cita de otra persona...). Si se da, manda sobre status_db_id.
        self._status_id_directo = status_id
        self.original_status_id = None
        self.hilo_toots = [] # Lista de toots en orden cronológico
        self.hilo_db_ids = []  # db_id (en mastodon_statuses) de cada toot, en el mismo orden

        self._init_ui()
        self._cargar_conversacion()
        self.CenterOnParent()

    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        sizer.Add(wx.StaticText(self, label=_("Cadena de mensajes (cronológico). Pulsa la tecla "
                                              "de aplicación o el botón Acciones para responder, "
                                              "citar, etc. al mensaje enfocado:")), 0, wx.ALL, 5)

        # Lista accesible
        self.lista_hilo = AccessibleListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Mensajes de la conversación")
        )
        self.lista_hilo.InsertColumn(0, _("Nº"), width=50)
        self.lista_hilo.InsertColumn(1, _("Autor"), width=150)
        self.lista_hilo.InsertColumn(2, _("Mensaje"), width=450)
        self.lista_hilo.InsertColumn(3, _("Fecha"), width=140)
        self.lista_hilo.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_toot_selected)
        # Interactuar con el mensaje enfocado (como en el timeline): menú contextual y atajos.
        self.lista_hilo.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, lambda e: self._menu())
        self.lista_hilo.Bind(wx.EVT_KEY_DOWN, self._on_key)

        sizer.Add(self.lista_hilo, 3, wx.EXPAND | wx.ALL, 5)
        
        # Caja de lectura de detalles
        self.txt_detalles = AccessibleTextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SUNKEN,
            name=_("Texto completo del mensaje seleccionado"),
            ayuda_texto=_("Contenido íntegro del toot seleccionado para lectura del screen reader.")
        )
        sizer.Add(self.txt_detalles, 2, wx.EXPAND | wx.ALL, 5)
        
        # Botonera de herramientas
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_acciones = wx.Button(self, label=_("&Acciones..."), name=_("Acciones sobre el mensaje enfocado"))
        self.btn_acciones.Bind(wx.EVT_BUTTON, lambda e: self._menu())
        btn_sizer.Add(self.btn_acciones, 0, wx.ALL, 5)

        self.btn_buffer = wx.Button(self, label=_("Abrir como &buffer"), name=_("Abrir esta conversación como buffer"))
        self.btn_buffer.Bind(wx.EVT_BUTTON, self._on_abrir_como_buffer)
        btn_sizer.Add(self.btn_buffer, 0, wx.ALL, 5)

        self.btn_resumir = wx.Button(self, label=_("&Resumir Hilo con IA"), name=_("Resumir conversación"))
        self.btn_resumir.Bind(wx.EVT_BUTTON, self._on_resumir_hilo)
        btn_sizer.Add(self.btn_resumir, 0, wx.ALL, 5)

        self.btn_traducir = wx.Button(self, label=_("&Traducir Hilo"), name=_("Traducir conversación"))
        self.btn_traducir.Bind(wx.EVT_BUTTON, self._on_traducir_hilo)
        btn_sizer.Add(self.btn_traducir, 0, wx.ALL, 5)

        self.btn_exportar = wx.Button(self, label=_("&Exportar Hilo..."), name=_("Exportar conversación"))
        self.btn_exportar.Bind(wx.EVT_BUTTON, self._on_exportar)
        btn_sizer.Add(self.btn_exportar, 0, wx.ALL, 5)

        btn_sizer.AddStretchSpacer()

        self.btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"), name=_("Cerrar visor"))
        btn_sizer.Add(self.btn_cerrar, 0, wx.ALL, 5)
        
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(sizer)

    def _cargar_conversacion(self):
        speak(_("Cargando contexto de conversación desde el servidor..."), interrupt=True)
        
        # Obtener ID del toot a abrir. Si la fila es un impulso (boost), la conversación es
        # la del toot ORIGINAL (reblog_of_id), no la del impulso.
        row = ((self._status_id_directo,) if self._status_id_directo else db.query_one(
            "SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?",
            (self.status_db_id,)))
        if not row:
            speak(_("Toot no encontrado en la base de datos local."), interrupt=True)
            return
            
        self.original_status_id = row[0]
        
        def _bg_load(ctx):
            client = mastodon_service.obtener_cliente(self.account_id)
            # El CONTEXTO (resto de mensajes enlazados) puede fallar o ir lento; si falla, al
            # menos mostramos el mensaje actual en vez de quedarnos en blanco.
            ancestros, descendientes = [], []
            try:
                context = client.status_context(self.original_status_id)
                ancestros = context.get("ancestors", []) or []
                descendientes = context.get("descendants", []) or []
            except Exception as e:
                log.debug(f"No se pudo obtener el contexto de la conversación: {e}")
            # El mensaje actual sí es imprescindible; si esto falla, es un error real (red).
            status_actual = client.status(self.original_status_id)
            combinado = ancestros + [status_actual] + descendientes
            self._idx_actual = len(ancestros)

            # Cachear cada toot del hilo en la base de datos para poder INTERACTUAR con él
            # (responder, citar, favorito...) reutilizando las acciones del timeline por db_id.
            db_ids = []
            for raw in combinado:
                try:
                    obj = mastodon_service._procesar_y_guardar_toot(self.account_id, raw)
                    db_ids.append(obj.id)
                except Exception as e:
                    log.debug(f"No se pudo cachear un toot del hilo: {e}")
                    db_ids.append(None)

            log.info(f"Conversación cargada: {len(ancestros)} ancestros, {len(descendientes)} descendientes.")
            return combinado, db_ids

        def _finish(res, err):
            if err:
                from app.core.net import humanizar_error
                notificar(humanizar_error(err))
                self.EndModal(wx.ID_CANCEL)
            else:
                self.hilo_toots, self.hilo_db_ids = res
                # Abierto por id del servidor (publicación citada...): adoptar la fila local
                # recién guardada del mensaje abierto, para "Abrir como buffer" y demás.
                if not self.status_db_id:
                    idx = getattr(self, "_idx_actual", 0)
                    if 0 <= idx < len(self.hilo_db_ids):
                        self.status_db_id = self.hilo_db_ids[idx]
                self._aplicar_filtros_servidor()
                self._render_lista()
                speak(_("Conversación cargada. Enfoque la lista para leer e interactuar."), interrupt=True)

        task_manager.start_task(_bg_load, callback=_finish, pass_context=True)

    # --- FILTROS DEL SERVIDOR (contexto "Conversaciones", como la web oficial) ---
    def _aplicar_filtros_servidor(self):
        """Quita del hilo los mensajes con un filtro 'ocultar' del contexto Conversaciones y
        guarda la evaluación de cada uno ('avisar' esconde el texto hasta mostrarlo). El
        mensaje ABIERTO nunca se oculta: avisa (lo has pedido tú)."""
        from app.services.mastodon import filtros_servidor as fsrv
        me = getattr(mastodon_service, "_me_ids", {}).get(self.account_id)
        fsrv.asegurar_vigentes(self.account_id)
        toots, ids, evs = [], [], []
        for t, d in zip(self.hilo_toots, self.hilo_db_ids):
            abierto = str(t.get("id")) == str(self.original_status_id)
            ev = fsrv.evaluar_raw(t, self.account_id, "thread", foco_detalle=abierto, me_id=me)
            if ev.get("accion") == "hide":
                continue
            toots.append(t)
            ids.append(d)
            evs.append(ev)
        self.hilo_toots, self.hilo_db_ids, self._evs_filtro = toots, ids, evs

    def _ev_filtro(self, idx):
        evs = getattr(self, "_evs_filtro", None) or []
        return evs[idx] if 0 <= idx < len(evs) else {"accion": None, "titulos": [], "titulos_media": []}

    def _clave_filtro(self, idx):
        if 0 <= idx < len(self.hilo_db_ids) and self.hilo_db_ids[idx]:
            return self.hilo_db_ids[idx]
        return "hilo:{0}:{1}".format(self.account_id, self.hilo_toots[idx].get("id"))

    def _texto_fila(self, idx, content_clean):
        from app.services.mastodon import filtros_servidor as fsrv
        return fsrv.contenido_con_filtro(content_clean, self._ev_filtro(idx), self._clave_filtro(idx))

    def _on_mostrar_filtrado(self):
        """'Mostrar de todos modos': revela el mensaje enfocado y rehace SOLO su fila."""
        from app.services.mastodon import filtros_servidor as fsrv
        idx = self.lista_hilo.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.hilo_toots):
            return
        fsrv.revelar(self._clave_filtro(idx))
        from bs4 import BeautifulSoup
        t = self.hilo_toots[idx]
        content_clean = BeautifulSoup(t.get("content", ""), "html.parser").get_text(" ").replace("\n", " ").strip()
        if idx < self.lista_hilo.GetItemCount():
            self.lista_hilo.SetItem(idx, 2, self._texto_fila(idx, content_clean))
            self._lecturas_fila(idx, content_clean)
        self.lista_hilo.reanunciar_foco()

    def _lecturas_fila(self, idx, content_clean):
        from app.core.config_manager import config
        from app.core.plantillas import pulir_lectura
        num = str(idx + 1)
        try:
            author = self.lista_hilo.texto_celda_completo(idx, 1)
            fecha_rel = self.lista_hilo.texto_celda_completo(idx, 3)
        except Exception:
            author, fecha_rel = "", ""
        texto = self._texto_fila(idx, content_clean)
        plantilla_c = config.get("accessibility", "conversation_template",
                                 "Mensaje {num} de {author}: {content}")
        try:
            lectura_c = plantilla_c.format(num=num, author=author, content=texto)
        except Exception:
            lectura_c = f"Mensaje {num} de {author}: {texto}"
        if fecha_rel:
            lectura_c = f"{lectura_c}. {fecha_rel}"
        self.lista_hilo.establecer_lectura(idx, pulir_lectura(lectura_c))

    def _render_lista(self):
        self.lista_hilo.DeleteAllItems()
        self.lista_hilo.limpiar_lecturas()

        from bs4 import BeautifulSoup
        from app.core.config_manager import config
        from .main_frame import formatear_tiempo_relativo

        _ocultar_nombres = config.get("accessibility", "hide_emojis_names", False)
        for idx, t in enumerate(self.hilo_toots):
            num = str(idx + 1)
            author = t.get("account", {}).get("display_name") or t.get("account", {}).get("username", "Desconocido")
            if _ocultar_nombres and author:
                from app.core.html_utils import quitar_emojis as _qe
                author = _qe(author)

            # Limpiar contenido HTML (texto COMPLETO, sin recortar, para poder leerlo todo).
            soup = BeautifulSoup(t.get("content", ""), "html.parser")
            content_clean = soup.get_text(" ").replace("\n", " ").strip()
            # Filtro del servidor con 'avisar': "[Filtrado: título]" hasta mostrarlo.
            content_clean = self._texto_fila(idx, content_clean)
            fecha_rel = formatear_tiempo_relativo(str(t.get("created_at"))) if t.get("created_at") else ""

            pos = self.lista_hilo.InsertItem(idx, num)
            self.lista_hilo.SetItem(pos, 1, author)
            # Contenido íntegro en la columna (la columna se ve recortada por su ancho,
            # pero el lector lee el texto completo al navegar por columnas).
            self.lista_hilo.SetItem(pos, 2, content_clean)
            self.lista_hilo.SetItem(pos, 3, fecha_rel)

            # Lectura accesible (al subir/bajar) con el contenido COMPLETO, para que el
            # lector lea todo el mensaje, no solo una parte.
            plantilla_c = config.get("accessibility", "conversation_template",
                                     "Mensaje {num} de {author}: {content}")
            try:
                lectura_c = plantilla_c.format(num=num, author=author, content=content_clean)
            except Exception:
                lectura_c = f"Mensaje {num} de {author}: {content_clean}"
            if fecha_rel:
                lectura_c = f"{lectura_c}. {fecha_rel}"
            from app.core.plantillas import pulir_lectura
            self.lista_hilo.establecer_lectura(pos, pulir_lectura(lectura_c))

        # Enfocar el toot original dentro del hilo.
        pos_original = 0
        for idx, t in enumerate(self.hilo_toots):
            if str(t["id"]) == str(self.original_status_id):
                pos_original = idx
                break

        if self.hilo_toots:
            self.lista_hilo.Focus(pos_original)
            self.lista_hilo.Select(pos_original)
            self.lista_hilo.EnsureVisible(pos_original)
            # Dejar el foco en la lista para navegarla como un buffer (flechas para
            # moverse, izquierda/derecha o Control más número para leer por columnas).
            self.lista_hilo.SetFocus()

    def _on_toot_selected(self, event):
        idx = event.GetIndex()
        if idx < 0 or idx >= len(self.hilo_toots):
            return
            
        t = self.hilo_toots[idx]
        author = t.get("account", {}).get("display_name") or t.get("account", {}).get("username")
        user = f"@{t.get('account', {}).get('acct')}"
        date = str(t.get("created_at"))
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(t.get("content", ""), "html.parser")
        content_clean = soup.get_text("\n").strip()
        
        detalles = f"Autor: {author} ({user})\n"
        detalles += f"Fecha: {date}\n"
        detalles += f"Privacidad: {t.get('visibility')}\n"
        if t.get("spoiler_text"):
            detalles += f"CW: {t.get('spoiler_text')}\n"
        from app.services.mastodon import filtros_servidor as fsrv
        ev = self._ev_filtro(idx)
        for lin in fsrv.lineas_detalle(ev):
            detalles += lin + "\n"
        if fsrv.oculta_texto(ev, self._clave_filtro(idx)):
            content_clean = _("(Texto oculto por el filtro. Usa «Mostrar de todos modos» en Acciones.)")

        detalles += "\n" + content_clean
        self.txt_detalles.SetValue(detalles)

    # --- INTERACCIÓN CON EL MENSAJE ENFOCADO (responder, citar, favorito...) ---
    def _db_id_actual(self):
        """Devuelve el db_id del mensaje enfocado en el hilo (o None)."""
        idx = self.lista_hilo.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.hilo_db_ids):
            return None
        return self.hilo_db_ids[idx]

    def _toot_actual(self):
        """Devuelve el toot (dict crudo) enfocado en el hilo (o None). Se usa para leer la
        multimedia (descripciones, imágenes) sin depender de la base de datos."""
        idx = self.lista_hilo.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.hilo_toots):
            return None
        return self.hilo_toots[idx]

    def _accion(self, cual):
        """Ejecuta una acción sobre el mensaje enfocado del hilo, reutilizando las acciones
        del timeline de la ventana principal."""
        # Acciones que leen el contenido del propio mensaje (no necesitan db_id cacheado).
        if cual == "descripcion":
            self._on_ver_descripcion()
            return
        elif cual == "ia_imagen":
            self._on_describir_imagen()
            return
        db_id = self._db_id_actual()
        if not db_id:
            speak(_("Enfoca primero un mensaje del hilo."), interrupt=True)
            return
        f = self.frame
        if cual == "responder":
            f._on_responder_toot(None, db_id=db_id)
        elif cual == "citar":
            f._on_citar_toot(None, db_id=db_id)
        elif cual == "favorito":
            f._on_boost_fav_toot(None, db_id=db_id)
        elif cual == "marcador":
            f._on_marcador_toot(db_id)
        elif cual == "impulsar":
            f._on_impulsar_toot(db_id)
        elif cual == "traducir":
            self._on_traducir_hilo(None)
        elif cual == "navegador":
            f._on_abrir_toot_navegador(db_id)
        elif cual == "copiar":
            f._on_copiar_enlace_toot(db_id)
        elif cual == "reportar":
            f._on_reportar_toot(db_id)
        elif cual == "autor":
            f._on_detalles_autor(db_id)
        elif cual == "tarjeta":
            f._on_tarjeta_enlace(db_id)

    def _menu(self):
        if not self._db_id_actual():
            speak(_("Enfoca primero un mensaje del hilo."), interrupt=True)
            return
        menu = wx.Menu()
        from app.services.mastodon import filtros_servidor as fsrv
        idx_f = self.lista_hilo.GetFocusedItem()
        if (0 <= idx_f < len(self.hilo_toots) and fsrv.esta_filtrado(self._ev_filtro(idx_f))
                and not fsrv.esta_revelado(self._clave_filtro(idx_f))):
            it_f = menu.Append(wx.ID_ANY, _("Mostrar de todos modos (filtrada)"))
            self.Bind(wx.EVT_MENU, lambda e: self._on_mostrar_filtrado(), it_f)
        opciones = [
            (_("Responder (Ctrl+R)"), "responder"),
            (_("Citar (Ctrl+Shift+U)"), "citar"),
            (_("Marcar/quitar favorito (Ctrl+B)"), "favorito"),
            (_("Guardar/quitar marcador"), "marcador"),
            (_("Impulsar / citar"), "impulsar"),
            (_("Traducir (Ctrl+T)"), "traducir"),
            (_("Ver descripción de la multimedia (Ctrl+Mayús+D)"), "descripcion"),
            (_("Describir imagen con IA (Ctrl+I)"), "ia_imagen"),
            (_("Abrir en el navegador"), "navegador"),
            (_("Copiar enlace"), "copiar"),
            (_("Reportar publicación..."), "reportar"),
            (_("Ver detalles del autor..."), "autor"),
            (_("Ver tarjeta de enlace..."), "tarjeta"),
        ]
        for etiqueta, clave in opciones:
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, c=clave: self._accion(c), it)
        self.lista_hilo.PopupMenu(menu)
        menu.Destroy()

    def _on_key(self, event):
        key = event.GetKeyCode()
        mod = event.GetModifiers()
        if mod == wx.ACCEL_CTRL and key == ord('R'):
            self._accion("responder")
        elif mod == (wx.ACCEL_CTRL | wx.ACCEL_SHIFT) and key == ord('U'):
            self._accion("citar")
        elif mod == wx.ACCEL_CTRL and key == ord('B'):
            self._accion("favorito")
        elif mod == wx.ACCEL_CTRL and key == ord('T'):
            self._accion("traducir")
        elif mod == (wx.ACCEL_CTRL | wx.ACCEL_SHIFT) and key == ord('D'):
            self._on_ver_descripcion()
        elif mod == wx.ACCEL_CTRL and key == ord('I'):
            self._on_describir_imagen()
        elif key == wx.WXK_WINDOWS_MENU:  # tecla de aplicación
            self._menu()
        else:
            event.Skip()

    def _on_abrir_como_buffer(self, event):
        """Abre esta conversación como un buffer permanente en el árbol y cierra el visor."""
        if not self.hilo_toots:
            return
        # Usar el db_id del toot ORIGINAL del hilo (el que se abrió) para crear el buffer.
        self.frame._anadir_buffer_conversacion(self.status_db_id)
        self.EndModal(wx.ID_OK)

    def _confirmar_media_filtrada(self) -> bool:
        """Multimedia difuminada por un filtro: pedir confirmación antes de verla o analizarla."""
        from app.services.mastodon import filtros_servidor as fsrv
        idx = self.lista_hilo.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.hilo_toots):
            return True
        ev = self._ev_filtro(idx)
        clave = self._clave_filtro(idx)
        if not fsrv.media_filtrada(ev, clave):
            return True
        from .accessible_controls import confirmar
        titulos = ", ".join(ev.get("titulos_media") or ev.get("titulos") or [])
        if not confirmar(self, _("La multimedia de esta publicación está oculta por tu filtro «{0}». "
                                 "¿Abrirla de todos modos?").format(titulos), _("Multimedia filtrada")):
            return False
        fsrv.revelar(clave)
        return True

    # --- LECTURA E IA SOBRE EL MENSAJE ENFOCADO (igual que en una publicación normal) ---
    def _on_ver_descripcion(self, event=None):
        """Ctrl+Mayús+D: muestra la descripción (texto alternativo) que el autor puso a la
        multimedia del mensaje enfocado, igual que en el timeline."""
        t = self._toot_actual()
        if not t:
            speak(_("Enfoca primero un mensaje del hilo."), interrupt=True)
            return
        if not self._confirmar_media_filtrada():
            return
        descripciones = []
        for att in (t.get("media_attachments") or []):
            d = (att.get("description") or "").strip()
            if d:
                descripciones.append(d)
        if not descripciones:
            speak(_("Esta publicación no tiene descripción en su multimedia."), interrupt=True)
            return
        if len(descripciones) == 1:
            texto = descripciones[0]
        else:
            texto = "\n\n".join(_("Imagen {0}: {1}").format(i + 1, d) for i, d in enumerate(descripciones))
        # El diálogo muestra el texto en un cuadro de solo lectura que el lector ya lee al abrirse;
        # no lo locutamos aquí para no oírlo dos veces.
        from .accessible_controls import mostrar_info
        mostrar_info(self, texto, _("Descripción de la multimedia"))

    def _on_describir_imagen(self, event=None):
        """Ctrl+I: descarga la primera imagen del mensaje enfocado y la describe con IA,
        igual que en el timeline."""
        t = self._toot_actual()
        if not t:
            speak(_("Enfoca primero un mensaje del hilo."), interrupt=True)
            return
        images = [a for a in (t.get("media_attachments") or []) if a.get("type") in ("image", "photo")]
        if not images:
            notificar(_("Esta publicación no contiene imágenes que describir."))
            return
        if not self._confirmar_media_filtrada():
            return
        img_url = images[0].get("url")
        if not img_url:
            notificar(_("No se pudo obtener la imagen de la publicación."))
            return
        speak(_("Conectando con la IA para analizar la imagen..."), interrupt=True)

        def _bg_desc(ctx):
            import os
            import requests
            from app.core.paths import get_data_path
            temp_path = get_data_path("temp/ia_download.jpg")
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            _hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            res_img = requests.get(img_url, timeout=20, headers=_hdrs)
            temp_path.write_bytes(res_img.content)
            resultado = ai_service.describir_imagen(temp_path)
            try:
                os.remove(temp_path)
            except Exception:
                pass
            return resultado

        def _finish(desc, err):
            if err:
                notificar(_("Error de la IA: {0}").format(err))
            else:
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Descripción de IA"), desc)

        task_manager.start_task(_bg_desc, callback=_finish, pass_context=True)

    # --- IA Y TRADUCCIÓN ---
    def _on_resumir_hilo(self, event):
        if not self.hilo_toots:
            return
            
        from bs4 import BeautifulSoup
        textos = []
        for idx, t in enumerate(self.hilo_toots):
            soup = BeautifulSoup(t.get("content", ""), "html.parser")
            textos.append(f"Post {idx+1} ({t.get('account', {}).get('username')}): {soup.get_text(' ').strip()}")
            
        texto_unido = "\n\n".join(textos)
        speak(_("Resumiendo la conversación completa..."), interrupt=True)
        
        def _bg_sum(ctx):
            return ai_service.resumir_texto(f"Resume este hilo de conversación de Mastodon:\n\n{texto_unido}")
            
        def _finish(res, err):
            if err:
                notificar(f"Error al resumir hilo: {err}")
            else:
                # Markdown -> visor HTML navegable; texto plano -> cuadro de solo lectura (que el
                # lector ya lee al abrirse; por eso no se locuta también aquí).
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Resumen de conversación con IA"), res)

        task_manager.start_task(_bg_sum, callback=_finish, pass_context=True)

    def _on_traducir_hilo(self, event):
        idx = self.lista_hilo.GetFocusedItem()
        if idx < 0 or idx >= len(self.hilo_toots):
            return
            
        t = self.hilo_toots[idx]
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(t.get("content", ""), "html.parser")
        texto = soup.get_text("\n").strip()
        
        speak(_("Traduciendo toot seleccionado..."), interrupt=True)
        
        def _bg_trans(ctx):
            return translation_service.traducir(texto, idioma_destino="es")
            
        def _finish(res, err):
            if err:
                notificar(f"Error de traducción: {err}")
            else:
                speak(f"Traducción: {res}", interrupt=True)
                from .accessible_controls import mostrar_info
                mostrar_info(self, res, _("Traducción de Toot"))
                
        task_manager.start_task(_bg_trans, callback=_finish, pass_context=True)

    # --- EXPORTAR ---
    def _on_exportar(self, event):
        if not self.hilo_toots:
            return
            
        dlg = wx.FileDialog(
            self, 
            message=_("Exportar conversación como"),
            wildcard="Archivo Markdown (*.md)|*.md|Archivo de texto (*.txt)|*.txt|Archivo JSON (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            from bs4 import BeautifulSoup
            
            try:
                ext = path.split(".")[-1].lower()
                if ext == "json":
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(self.hilo_toots, f, indent=4, default=str)
                elif ext == "md":
                    # Markdown formateado
                    lineas = [f"# Conversación de Mastodon\n"]
                    for idx, t in enumerate(self.hilo_toots):
                        author = t.get("account", {}).get("display_name") or t.get("account", {}).get("username")
                        user = f"@{t.get('account', {}).get('acct')}"
                        soup = BeautifulSoup(t.get("content", ""), "html.parser")
                        lineas.append(f"### {idx+1}. {author} ({user}) - {t.get('created_at')}")
                        lineas.append(soup.get_text("\n").strip())
                        lineas.append("\n---\n")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("\n".join(lineas))
                else:
                    # Texto simple
                    lineas = []
                    for idx, t in enumerate(self.hilo_toots):
                        author = t.get("account", {}).get("display_name") or t.get("account", {}).get("username")
                        soup = BeautifulSoup(t.get("content", ""), "html.parser")
                        lineas.append(f"[{idx+1}] {author} ({t.get('created_at')}):\n{soup.get_text(' ').strip()}\n")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("\n".join(lineas))
                notificar(_("Conversación exportada con éxito."))
            except Exception as e:
                notificar(f"Error al exportar: {e}")
        dlg.Destroy()
